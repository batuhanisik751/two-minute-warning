"""Ranking and probability metrics for walk-forward backtests (shared by every module).

- :func:`add_rank`: rank rows within groups (e.g. one as-of and position) by a score, highest
  first, with deterministic tie-breaks; rows without a score go to the bottom.
- :func:`precision_at_k`: per group, the share of the top k that were hits. A group with fewer
  than k rows divides by its number of rows and is flagged (``short``).
- :func:`pooled_precision`: the mean over groups (every group weighs the same, like "a typical
  week"), with the counts behind it.
- :func:`bucket_rates`: the hit rate of rank buckets (ranks 1-5, 6-10, 11-25 ...), pooled.
- :func:`pr_auc`, :func:`brier`: area under the precision-recall curve (average precision) and
  the Brier score (mean squared error of a probability; lower is better).
- :func:`calibration_bins`: predicted vs observed hit rate in equal-count probability bins;
  :func:`calibration_fixed_bins`: the same in fixed-width bins (0-10%, 10-20% ...), empty bins
  included, always with their counts.
- :func:`block_bootstrap`, :func:`paired_block_bootstrap`: honest 95% intervals for a pooled
  per-group metric (e.g. precision@10) by resampling whole blocks (seasons) with replacement,
  and for the difference between two methods on the same groups (PROJECT_SPEC 1.5).
- :func:`first_events`, :func:`caught_before`: "breakouts caught": for each entity, the first
  time an event happened, and whether a method ranked it in its top k at or before that time
  (never after: no hindsight).

Everything is deterministic: sorts are total (ties broken by the given id column) and the
bootstrap uses a fixed seed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import polars as pl
from sklearn.metrics import average_precision_score

RANK = "rank"
DEFAULT_BUCKETS: tuple[tuple[int, int], ...] = ((1, 5), (6, 10), (11, 25))
N_BOOT = 2000  # bootstrap resamples
BOOTSTRAP_SEED = 20140907  # any fixed number: the same resamples every run
LEVEL = 0.95  # interval coverage


def add_rank(
    df: pl.DataFrame,
    *,
    group: Sequence[str],
    by: Sequence[tuple[str, bool]],
    id_col: str,
    name: str = RANK,
) -> pl.DataFrame:
    """``df`` sorted by group and ranking, with a 1-based ``name`` column per group.

    ``by`` = [(column, descending), ...] in order of priority; nulls always sort last (a row
    without a score is ranked below every row with one); remaining ties go to the smaller
    ``id_col`` (ascending), so the ranking is a total order.
    """
    cols = [*group, *(c for c, _ in by), id_col]
    desc = [False] * len(group) + [d for _, d in by] + [False]
    out = df.sort(cols, descending=desc, nulls_last=True, maintain_order=True)
    rank = (pl.int_range(pl.len()).over(list(group)) + 1).cast(pl.Int32)
    return out.with_columns(rank.alias(name))


def precision_at_k(
    ranked: pl.DataFrame, *, group: Sequence[str], label: str, k: int = 10, rank: str = RANK
) -> pl.DataFrame:
    """One row per group: n_rows, n_pos (hits in the group), n_top (= min(k, n_rows)),
    hits_top (hits among ranks 1..k), p_at_k = hits_top / n_top, short = n_rows < k."""
    y = pl.col(label).cast(pl.Int64)
    return (
        ranked.group_by(list(group))
        .agg(
            pl.len().alias("n_rows"),
            y.sum().alias("n_pos"),
            y.filter(pl.col(rank) <= k).sum().alias("hits_top"),
        )
        .with_columns(pl.min_horizontal(pl.col("n_rows"), pl.lit(k)).alias("n_top"))
        .with_columns(
            (pl.col("hits_top") / pl.col("n_top")).alias("p_at_k"),
            (pl.col("n_rows") < k).alias("short"),
        )
        .sort(list(group))
    )


@dataclass(frozen=True)
class Pooled:
    """Mean precision@k over groups and the counts behind it."""

    value: float | None  # None when there is no group
    n_groups: int
    n_short: int  # groups with fewer than k rows
    n_rows: int
    n_pos: int  # hits among all rows of the groups
    hits_top: int  # hits among the top k of every group

    @property
    def base_rate(self) -> float | None:
        """The hit rate of all rows: what a random ranking's precision would be on average."""
        return self.n_pos / self.n_rows if self.n_rows else None


def pooled_precision(per_group: pl.DataFrame) -> Pooled:
    """Pool a :func:`precision_at_k` table: the plain mean of p_at_k over its groups."""
    if per_group.height == 0:
        return Pooled(None, 0, 0, 0, 0, 0)
    return Pooled(
        value=float(per_group.get_column("p_at_k").mean()),  # type: ignore[arg-type]
        n_groups=per_group.height,
        n_short=int(per_group.get_column("short").sum()),
        n_rows=int(per_group.get_column("n_rows").sum()),
        n_pos=int(per_group.get_column("n_pos").sum()),
        hits_top=int(per_group.get_column("hits_top").sum()),
    )


def bucket_rates(
    ranked: pl.DataFrame,
    *,
    label: str,
    buckets: Sequence[tuple[int, int]] = DEFAULT_BUCKETS,
    rank: str = RANK,
) -> list[tuple[str, int, int, float | None]]:
    """(bucket name, rows, hits, hit rate) per rank bucket, pooled over every group."""
    out = []
    for lo, hi in buckets:
        sub = ranked.filter(pl.col(rank).is_between(lo, hi))
        hits = int(sub.get_column(label).cast(pl.Int64).sum()) if sub.height else 0
        out.append((f"{lo}-{hi}", sub.height, hits, hits / sub.height if sub.height else None))
    return out


def pr_auc(y: np.ndarray, score: np.ndarray) -> float | None:
    """Average precision (area under the precision-recall curve); None without a positive or
    a negative."""
    y = np.asarray(y, dtype=np.int64)
    if y.size == 0 or y.min() == y.max():
        return None
    return float(average_precision_score(y, np.asarray(score, dtype=np.float64)))


def brier(y: np.ndarray, prob: np.ndarray) -> float | None:
    """Mean of (probability - outcome)^2; None for no rows."""
    y = np.asarray(y, dtype=np.float64)
    if y.size == 0:
        return None
    p = np.asarray(prob, dtype=np.float64)
    return float(np.mean((p - y) ** 2))


def calibration_bins(
    df: pl.DataFrame, *, prob: str, label: str, id_cols: Sequence[str], n_bins: int = 10
) -> pl.DataFrame:
    """Equal-count bins of the predicted probability (lowest first): rows, hits (``n_pos``),
    mean predicted, observed hit rate, min and max predicted. Rows are ordered by
    (prob, id_cols) so tied probabilities are split deterministically."""
    if df.height == 0:
        return pl.DataFrame(
            schema={"bin": pl.Int32, "n": pl.UInt32, "n_pos": pl.Int64, "mean_pred": pl.Float64,
                    "observed": pl.Float64, "min_pred": pl.Float64, "max_pred": pl.Float64}
        )  # fmt: skip
    ordered = df.sort([prob, *id_cols], maintain_order=True)
    p = ordered.get_column(prob).cast(pl.Float64).to_numpy()
    y = ordered.get_column(label).cast(pl.Int64).to_numpy()
    n = ordered.height
    bins = (np.arange(n, dtype=np.int64) * n_bins) // n  # contiguous, in probability order
    rows = []
    for b in np.unique(bins):
        sel = bins == b  # numpy sums in a fixed order: the same bits on every run
        pb, yb = p[sel], y[sel]
        rows.append(
            (int(b) + 1, int(sel.sum()), int(yb.sum()), float(pb.mean()), float(yb.mean()),
             float(pb.min()), float(pb.max()))
        )  # fmt: skip
    return pl.DataFrame(
        rows,
        schema={"bin": pl.Int32, "n": pl.UInt32, "n_pos": pl.Int64, "mean_pred": pl.Float64,
                "observed": pl.Float64, "min_pred": pl.Float64, "max_pred": pl.Float64},
        orient="row",
    )  # fmt: skip


def calibration_fixed_bins(
    df: pl.DataFrame, *, prob: str, label: str, n_bins: int = 10
) -> pl.DataFrame:
    """Fixed-width bins of the predicted probability: bin i (1-based) holds probabilities in
    [(i-1)/n_bins, i/n_bins) (the last bin includes 1.0). Every bin is listed, empty ones with
    n = 0 and NULL rates, so the counts are always visible: bin, lo, hi, n, n_pos, mean_pred,
    observed."""
    known = df.filter(pl.col(prob).is_not_null()).sort([prob, label])
    p = known.get_column(prob).cast(pl.Float64).to_numpy()
    y = known.get_column(label).cast(pl.Int64).to_numpy()
    idx = np.clip(np.floor(p * n_bins).astype(np.int64), 0, n_bins - 1)
    rows = []
    for b in range(n_bins):
        sel = idx == b  # numpy sums in a fixed (sorted) order: the same bits on every run
        n = int(sel.sum())
        rows.append(
            (b + 1, b / n_bins, (b + 1) / n_bins, n, int(y[sel].sum()),
             float(p[sel].mean()) if n else None, float(y[sel].mean()) if n else None)
        )  # fmt: skip
    return pl.DataFrame(
        rows,
        schema={"bin": pl.Int32, "lo": pl.Float64, "hi": pl.Float64, "n": pl.Int64,
                "n_pos": pl.Int64, "mean_pred": pl.Float64, "observed": pl.Float64},
        orient="row",
    )  # fmt: skip


# --------------------------------------------------------------------------------------
# Uncertainty: the season-block bootstrap
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Interval:
    """A pooled value with its bootstrap interval (``lo``-``hi``, ``level`` coverage).

    ``share_above_zero`` (differences only): the share of resamples in which the difference is
    above 0, e.g. 1.0 = the first method was ahead in every resample."""

    value: float | None
    lo: float | None
    hi: float | None
    n_blocks: int
    n_groups: int
    n_boot: int
    level: float
    share_above_zero: float | None = None


def block_indices(n_blocks: int, n_boot: int = N_BOOT, seed: int = BOOTSTRAP_SEED) -> np.ndarray:
    """(n_boot, n_blocks) block indices drawn with replacement: row b is resample b. Depends
    only on its arguments, so every method evaluated with the same blocks gets the same
    resamples (which is what makes paired differences paired)."""
    rng = np.random.default_rng(seed)
    return rng.integers(0, n_blocks, size=(n_boot, n_blocks)) if n_blocks else np.zeros((0, 0))


def _block_totals(
    per_group: pl.DataFrame, block: str, value: str, blocks: Sequence[object]
) -> tuple[np.ndarray, np.ndarray]:
    """Per block (in ``blocks`` order): the sum of ``value`` over its groups, and its number of
    groups."""
    # sorted by block and value, then summed in numpy: the same bits whatever the input order
    # (a multithreaded group-by may add floats in a different order on every run)
    ordered = per_group.select(block, pl.col(value).cast(pl.Float64)).sort([block, value])
    keys = ordered.get_column(block).to_list()
    vals = ordered.get_column(value).to_numpy()
    sums = np.zeros(len(blocks), dtype=np.float64)
    counts = np.zeros(len(blocks), dtype=np.float64)
    pos = {b: i for i, b in enumerate(blocks)}
    start = 0
    for i in range(1, len(keys) + 1):
        if i == len(keys) or keys[i] != keys[start]:
            j = pos[keys[start]]
            sums[j], counts[j] = float(np.sum(vals[start:i])), i - start
            start = i
    return sums, counts


def _interval(
    values: np.ndarray, point: float | None, n_blocks: int, n_groups: int, n_boot: int,
    level: float, diff: bool,
) -> Interval:  # fmt: skip
    if point is None or values.size == 0:
        return Interval(point, None, None, n_blocks, n_groups, n_boot, level)
    tail = (1.0 - level) / 2.0
    lo, hi = np.quantile(values, [tail, 1.0 - tail])
    share = float(np.mean(values > 0)) if diff else None
    return Interval(point, float(lo), float(hi), n_blocks, n_groups, n_boot, level, share)


def block_bootstrap(
    per_group: pl.DataFrame,
    *,
    block: str = "season",
    value: str = "p_at_k",
    n_boot: int = N_BOOT,
    seed: int = BOOTSTRAP_SEED,
    level: float = LEVEL,
) -> Interval:
    """The mean of ``value`` over the groups (e.g. pooled precision@10 over weekly lists) and
    a percentile interval from resampling whole blocks (e.g. seasons) with replacement.

    Why blocks: the lists of one season share its players, coaches and the model trained for
    it, so they are not independent; resampling seasons keeps that together and gives an
    honest (wider) interval than pretending every list is independent. A resample's value is
    the mean over all groups of the drawn blocks (a block drawn twice counts twice), which is
    how the pooled value itself weighs a season: by its number of groups."""
    if per_group.height == 0:
        return Interval(None, None, None, 0, 0, n_boot, level)
    blocks = sorted(per_group.get_column(block).unique().to_list())
    sums, counts = _block_totals(per_group, block, value, blocks)
    point = float(sums.sum() / counts.sum())
    idx = block_indices(len(blocks), n_boot, seed)
    values = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    return _interval(values, point, len(blocks), per_group.height, n_boot, level, diff=False)


def paired_block_bootstrap(
    a: pl.DataFrame,
    b: pl.DataFrame,
    *,
    keys: Sequence[str],
    block: str = "season",
    value: str = "p_at_k",
    n_boot: int = N_BOOT,
    seed: int = BOOTSTRAP_SEED,
    level: float = LEVEL,
) -> Interval:
    """Pooled ``value`` of ``a`` minus that of ``b`` on the SAME groups (``keys``), with a
    percentile interval from the same block resamples for both: a season that was hard for
    both methods is drawn for both at once, so the interval measures the gap, not the
    difficulty of the seasons. Raises if the two tables do not cover the same groups."""
    ka = a.select(list(keys)).sort(list(keys))
    kb = b.select(list(keys)).sort(list(keys))
    if not ka.equals(kb):
        raise ValueError("paired bootstrap: the two methods must be graded on the same groups")
    if a.height == 0:
        return Interval(None, None, None, 0, 0, n_boot, level)
    joined = a.select(*keys, pl.col(value).cast(pl.Float64).alias("_a")).join(
        b.select(*keys, pl.col(value).cast(pl.Float64).alias("_b")), on=list(keys)
    )
    diff = joined.with_columns((pl.col("_a") - pl.col("_b")).alias("_d"))
    blocks = sorted(diff.get_column(block).unique().to_list())
    sums, counts = _block_totals(diff, block, "_d", blocks)
    point = float(sums.sum() / counts.sum())
    idx = block_indices(len(blocks), n_boot, seed)
    values = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    return _interval(values, point, len(blocks), diff.height, n_boot, level, diff=True)


# --------------------------------------------------------------------------------------
# "Caught before it happened"
# --------------------------------------------------------------------------------------


def first_events(
    rows: pl.DataFrame, *, entity: Sequence[str], order: str, event: str
) -> pl.DataFrame:
    """One row per entity whose ``event`` is true in some row: (entity..., ``order`` of the
    first such row, as ``event_order``), sorted by entity."""
    return (
        rows.filter(pl.col(event).fill_null(False))
        .group_by(list(entity))
        .agg(pl.col(order).min().alias("event_order"))
        .sort(list(entity))
    )


def caught_before(
    events: pl.DataFrame,
    ranked: pl.DataFrame,
    *,
    entity: Sequence[str],
    order: str,
    rank: str = RANK,
    k: int = 10,
    recent: int | None = None,
) -> pl.DataFrame:
    """For every event of :func:`first_events` and one method's ranked rows (entity...,
    ``order``, ``rank``): was the entity ranked in the top ``k`` at or BEFORE its event?

    Only rows with ``order`` <= ``event_order`` count, so a ranking made after the event can
    never catch it (no hindsight). Adds ``best_rank`` (the best rank at or before the event;
    NULL if never ranked), ``best_order`` (the first order at which it was reached),
    ``first_top_k`` (the first order with a top-k rank), ``caught`` (a top-k rank at or before
    the event) and, with ``recent`` = r, ``caught_recent`` (a top-k rank at one of the r
    orders up to the event: event_order - r + 1 .. event_order)."""
    before = ranked.select(*entity, order, rank).join(
        events.select(*entity, "event_order"), on=list(entity), how="inner"
    )
    before = before.filter(pl.col(order) <= pl.col("event_order"))
    top = pl.col(rank) <= k
    aggs = [
        pl.col(rank).min().alias("best_rank"),
        pl.col(order).filter(pl.col(rank) == pl.col(rank).min()).min().alias("best_order"),
        pl.col(order).filter(top).min().alias("first_top_k"),
    ]
    if recent is not None:
        near = pl.col(order) > pl.col("event_order") - recent
        aggs.append((top & near).any().alias("caught_recent"))
    stats = before.group_by(list(entity)).agg(aggs)
    out = events.join(stats, on=list(entity), how="left").with_columns(
        pl.col("first_top_k").is_not_null().alias("caught")
    )
    if recent is not None:
        out = out.with_columns(pl.col("caught_recent").fill_null(False))
    return out.sort(list(entity))
