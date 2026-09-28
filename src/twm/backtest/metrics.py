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
- :func:`calibration_bins`: predicted vs observed hit rate in equal-count probability bins.

Everything is deterministic: sorts are total (ties broken by the given id column).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import polars as pl
from sklearn.metrics import average_precision_score

RANK = "rank"
DEFAULT_BUCKETS: tuple[tuple[int, int], ...] = ((1, 5), (6, 10), (11, 25))


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
    """Equal-count bins of the predicted probability (lowest first): rows, mean predicted,
    observed hit rate, min and max predicted. Rows are ordered by (prob, id_cols) so tied
    probabilities are split deterministically."""
    if df.height == 0:
        return pl.DataFrame(
            schema={"bin": pl.Int32, "n": pl.UInt32, "mean_pred": pl.Float64,
                    "observed": pl.Float64, "min_pred": pl.Float64, "max_pred": pl.Float64}
        )  # fmt: skip
    ordered = df.sort([prob, *id_cols], maintain_order=True).with_row_index("_i")
    n = ordered.height
    binned = ordered.with_columns(
        ((pl.col("_i").cast(pl.Int64) * n_bins) // n + 1).cast(pl.Int32).alias("bin")
    )
    return (
        binned.group_by("bin")
        .agg(
            pl.len().alias("n"),
            pl.col(prob).mean().alias("mean_pred"),
            pl.col(label).cast(pl.Float64).mean().alias("observed"),
            pl.col(prob).min().alias("min_pred"),
            pl.col(prob).max().alias("max_pred"),
        )
        .sort("bin")
    )
