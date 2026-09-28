"""How sure is a weekly Waiver Radar number? The confidence band and the suggested priority
(C6), both read off the backtest, never set by intuition.

**The band: "similar players hit X-Y%".** For the model's probability ``p`` of a player, take
the backtest predictions of the same model and label (the stored walk-forward predictions of
2014-2025, each made by a model that had never seen its season) whose probabilities were
close to ``p``, and report how often those players really hit, with a 90% Wilson interval.
"Close" = the same **bin**: the backtest predictions sorted by probability and cut into
consecutive groups of at least :data:`MIN_ROWS` predictions (a probability shared by several
predictions is never split), then merged where needed so that the hit rate never goes down as
the probability goes up (pool-adjacent-violators: a higher model probability never shows a
lower chance). The number the list shows (**chance**) is that bin's observed hit rate, not the
model's own probability: C5 found the probabilities too high above about 60% (the 70-80% bin
hit 66.7%), and the observed rate is the honest number. The model's calibrated probability
stays in the store (``score``) and in the report.

Why not C5's 10 equal-count bins: the top one spans every probability from 34% to 100%
(9,163 predictions, 47.3% hit), so the whole top of every list would show the same 47% and no
player could reach the 50% of a must-add. Bins of 500 give a 90% interval of at most about
+/-3.7 points and still separate 60% from 70%.

**The suggested priority** of the top 25 of a list comes from the chance: **must-add** when
similar players hit at least 50% of the time, **speculative** from 25% to 50%, **watch** below
(ranks 26 and lower get none). Because the bins only go up, each tier is a cutoff on the model
probability (:attr:`Confidence.cutoffs`), derived from the backtest; :func:`tier_table` gives
each tier's historical hit rate on the backtest's top-25 rows, with counts.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np
import polars as pl

MIN_ROWS = 500
BAND_LEVEL = 0.90
Z = NormalDist().inv_cdf(0.5 + BAND_LEVEL / 2)
TOP_N = 25  # suggested priorities go to the top 25 of each list (spec 8.1 output)
MUST_ADD = 0.50  # similar players hit at least this often
SPECULATIVE = 0.25
TIER_ORDER = ("must-add", "speculative", "watch")
BAND_DECIMALS = 4


def wilson(hits: int, n: int, z: float = Z) -> tuple[float, float]:
    """Wilson score interval of hits/n (``z`` = 1.645 for 90%)."""
    if n <= 0:
        return (0.0, 1.0)
    p = hits / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def similar_bins(prob: np.ndarray, y: np.ndarray, min_rows: int = MIN_ROWS) -> pl.DataFrame:
    """Bins of the backtest probabilities (see the module docstring): bin, p_from (its lowest
    probability), p_to (its highest), n, hits, rate, lo, hi (90% Wilson)."""
    p = np.asarray(prob, dtype=np.float64)
    yy = np.asarray(y, dtype=np.int64)
    if p.size == 0:
        raise ValueError("no backtest predictions to build the confidence bins from")
    order = np.lexsort((yy, p))
    p, yy = p[order], yy[order]
    values, starts, counts = np.unique(p, return_index=True, return_counts=True)
    hits_per_value = np.add.reduceat(yy, starts)
    # 1. consecutive groups of whole probability values, at least min_rows each
    blocks: list[list[float]] = []  # [p_from, p_to, n, hits]
    cur: list[float] | None = None
    for v, c, h in zip(values, counts, hits_per_value, strict=True):
        if cur is None:
            cur = [float(v), float(v), 0.0, 0.0]
        cur[1], cur[2], cur[3] = float(v), cur[2] + int(c), cur[3] + int(h)
        if cur[2] >= min_rows:
            blocks.append(cur)
            cur = None
    if cur is not None:
        if blocks:
            last = blocks[-1]
            last[1], last[2], last[3] = cur[1], last[2] + cur[2], last[3] + cur[3]
        else:
            blocks.append(cur)
    # 2. pool adjacent violators: merge until the rate never decreases
    merged: list[list[float]] = []
    for b in blocks:
        merged.append(list(b))
        while len(merged) > 1 and merged[-2][3] / merged[-2][2] > merged[-1][3] / merged[-1][2]:
            hi = merged.pop()
            lo = merged[-1]
            lo[1], lo[2], lo[3] = hi[1], lo[2] + hi[2], lo[3] + hi[3]
    rows = []
    for i, (p_from, p_to, n, h) in enumerate(merged, start=1):
        lo, hi = wilson(int(h), int(n))
        rows.append((i, p_from, p_to, int(n), int(h), h / n, lo, hi))
    return pl.DataFrame(
        rows,
        schema={"bin": pl.Int32, "p_from": pl.Float64, "p_to": pl.Float64, "n": pl.Int64,
                "hits": pl.Int64, "rate": pl.Float64, "lo": pl.Float64, "hi": pl.Float64},
        orient="row",
    )  # fmt: skip


def tier_of(rate: float | None) -> str:
    if rate is None:
        return "watch"
    if rate >= MUST_ADD:
        return "must-add"
    if rate >= SPECULATIVE:
        return "speculative"
    return "watch"


@dataclass
class Confidence:
    """The band bins and tier cutoffs of one model and label, from its backtest."""

    bins: pl.DataFrame
    model: str
    label: str
    seasons: tuple[int, int]
    n_predictions: int
    tiers: pl.DataFrame = field(default_factory=pl.DataFrame)  # tier_table()
    # the backtest rows the bins come from (season, week, position, score, rank, y)
    history: pl.DataFrame = field(default_factory=pl.DataFrame, repr=False)

    def week_tiers(self, week: int) -> pl.DataFrame:
        """:func:`tier_table` of the backtest lists of one week number only (every season):
        how the tiers did at this point of past seasons (week 2 differs from week 10)."""
        if self.history.height == 0:
            return tier_table(self, self.history)
        return tier_table(self, self.history.filter(pl.col("week") == week))

    def _index(self, prob: np.ndarray) -> np.ndarray:
        starts = self.bins.get_column("p_from").to_numpy()
        idx = np.searchsorted(starts, np.asarray(prob, dtype=np.float64), side="right") - 1
        return np.clip(idx, 0, len(starts) - 1)

    def band(self, prob: Sequence[float] | np.ndarray) -> pl.DataFrame:
        """Per probability: chance (the similar players' hit rate), lo, hi (90%), n, hits,
        p_from, p_to (the bin's probability range)."""
        idx = self._index(np.asarray(prob, dtype=np.float64))
        b = self.bins
        return pl.DataFrame(
            {
                "chance": b.get_column("rate").to_numpy()[idx],
                "band_lo": b.get_column("lo").to_numpy()[idx],
                "band_hi": b.get_column("hi").to_numpy()[idx],
                "band_n": b.get_column("n").to_numpy()[idx],
                "band_hits": b.get_column("hits").to_numpy()[idx],
                "band_from": b.get_column("p_from").to_numpy()[idx],
                "band_to": b.get_column("p_to").to_numpy()[idx],
            }
        )

    @property
    def cutoffs(self) -> dict[str, float | None]:
        """The lowest model probability of each tier (None when no bin reaches it)."""
        out: dict[str, float | None] = {}
        for tier, floor in (("must-add", MUST_ADD), ("speculative", SPECULATIVE)):
            hit = self.bins.filter(pl.col("rate") >= floor)
            out[tier] = float(hit.get_column("p_from").min()) if hit.height else None  # type: ignore[arg-type]
        return out


def band_json(row: dict[str, Any]) -> str:
    """The ``band`` column of the store: the chance, its 90% interval and the evidence."""
    r = BAND_DECIMALS
    return json.dumps(
        {"chance": round(row["chance"], r), "lo": round(row["band_lo"], r),
         "hi": round(row["band_hi"], r), "n": int(row["band_n"]), "hits": int(row["band_hits"]),
         "p_from": round(row["band_from"], r), "p_to": round(row["band_to"], r),
         "level": BAND_LEVEL},
        sort_keys=True, separators=(",", ":"),
    )  # fmt: skip


def band_text(chance: float, lo: float, hi: float) -> str:
    """'52% (similar players hit 45-55%)'; one decimal when whole percents would hide the
    range ('2% (similar players hit 1.9-2.4%)')."""
    d = 0 if round(100 * lo) != round(100 * hi) else 1
    return f"{100 * chance:.0f}% (similar players hit {100 * lo:.{d}f}-{100 * hi:.{d}f}%)"


def top_k_rate(conf: Confidence, k: int = 10) -> tuple[float | None, int, int]:
    """(hit rate, hits, rows) of the backtest's top ``k`` of every list."""
    h = conf.history
    if h.height == 0:
        return None, 0, 0
    top = h.filter(pl.col("rank") <= k)
    hits = int(top.get_column("y").cast(pl.Int64).sum())
    return (hits / top.height if top.height else None), hits, top.height


# --------------------------------------------------------------------------------------
# From the predictions store
# --------------------------------------------------------------------------------------


def backtest_rows(
    store: Path | str, *, model: str, label: str, seasons: tuple[int, int] | None = None
) -> pl.DataFrame:
    """The stored walk-forward predictions of ``model`` for ``label`` (current versions,
    evaluation seasons, final outcomes): season, week, position, gsis_id, score, rank, y."""
    from twm.modules.waiver_radar import evaluation as ev

    rows = ev.load_predictions(store, label, seasons=seasons)
    return rows.filter(pl.col("model") == model).select(
        "season", "week", "position", "gsis_id", "score", "raw_score", "rank", "y"
    )


def tier_table(conf: Confidence, rows: pl.DataFrame, top: int = TOP_N) -> pl.DataFrame:
    """Historical hit rate of each tier on the backtest's top-``top`` rows of every list:
    tier, p_from (the tier's lowest model probability), lists, rows, hits, rate, per_list."""
    top_rows = rows.filter(pl.col("rank") <= top) if rows.height else rows
    if top_rows.height == 0:
        return pl.DataFrame(
            schema={"tier": pl.String, "p_from": pl.Float64, "lists": pl.Int64,
                    "rows": pl.Int64, "hits": pl.Int64, "rate": pl.Float64,
                    "per_list": pl.Float64}
        )  # fmt: skip
    b = conf.band(top_rows.get_column("score").to_numpy())
    tiered = top_rows.with_columns(
        pl.Series("tier", [tier_of(float(r)) for r in b.get_column("chance").to_list()])
    )
    n_lists = tiered.select("season", "week", "position").unique().height
    cut = conf.cutoffs
    out = []
    for tier in TIER_ORDER:
        sub = tiered.filter(pl.col("tier") == tier)
        n = sub.height
        h = int(sub.get_column("y").cast(pl.Int64).sum()) if n else 0
        out.append(
            (tier, cut.get(tier) if tier != "watch" else 0.0, n_lists, n, h, h / n if n else None,
             n / n_lists if n_lists else None)
        )  # fmt: skip
    return pl.DataFrame(
        out,
        schema={"tier": pl.String, "p_from": pl.Float64, "lists": pl.Int64, "rows": pl.Int64,
                "hits": pl.Int64, "rate": pl.Float64, "per_list": pl.Float64},
        orient="row",
    )  # fmt: skip


def seasons_before(season: int) -> tuple[int, int]:
    """The backtest seasons a list of ``season`` may learn its band from: the evaluation
    seasons (settings ``seasons.waiver_radar_eval``) strictly before it. For the live season
    that is all of them (2014-2025 for 2026); a reconstructed list of 2020 uses 2014-2019 only,
    so its band and priority never rest on outcomes after its as-of."""
    from twm.modules.waiver_radar.backtest import eval_seasons

    first, last = eval_seasons()
    last = min(last, int(season) - 1)
    if last < first:
        raise ValueError(
            f"no backtest season before {season} to set the band and the priority from (the "
            f"backtest starts in {first})"
        )
    return first, last


def from_store(
    store: Path | str,
    *,
    model: str,
    label: str,
    seasons: tuple[int, int] | None = None,
    min_rows: int = MIN_ROWS,
) -> Confidence:
    """Build the bins and the tier table from the store's backtest of ``model``/``label`` in
    ``seasons`` (first, last; default: every evaluation season; a weekly list passes
    :func:`seasons_before`)."""
    from twm.modules.waiver_radar.backtest import eval_seasons

    span = seasons if seasons is not None else eval_seasons()
    rows = backtest_rows(store, model=model, label=label, seasons=span)
    if rows.height == 0:
        raise ValueError(
            f"no backtest predictions of {model} for {label} in {store}; run "
            "`uv run twm radar backtest` first"
        )
    y = rows.get_column("y").cast(pl.Int64).to_numpy()
    bins = similar_bins(rows.get_column("score").to_numpy(), y, min_rows)
    seen = rows.get_column("season")
    conf = Confidence(bins, model, label, (int(seen.min()), int(seen.max())), rows.height)  # type: ignore[arg-type]
    conf.tiers = tier_table(conf, rows)
    conf.history = rows.select("season", "week", "position", "score", "rank", "y")
    return conf


# --------------------------------------------------------------------------------------
# Position notes (computed from the evaluation, never typed)
# --------------------------------------------------------------------------------------


def position_notes(
    csv_path: Path,
    *,
    label: str,
    model: str,
    baseline: str = "baseline_last_points",
    before: int | None = None,
) -> dict[str, str]:
    """Per position where the evaluation could not show the model ahead of ``baseline``
    (the 95% interval of the precision@10 difference reaches 0): a one-line note, with the
    numbers read from ``reports/waiver_radar/evaluation.csv``. Empty when the file is absent,
    and (``before``: the season of the list) when the evaluation includes that season or a
    later one: a reconstructed list never quotes outcomes after its as-of."""
    if not csv_path.exists():
        return {}
    ev = pl.read_csv(csv_path, infer_schema_length=0)
    cols = ("label", "subset", "model", "scope", "key", "metric")
    if any(c not in ev.columns for c in cols):
        return {}
    base = ev.filter((pl.col("label") == label) & (pl.col("subset") == "all"))
    notes = {}
    diffs = base.filter(
        (pl.col("model") == model) & (pl.col("scope") == "position_diff")
        & (pl.col("metric") == "p_at_10_diff") & pl.col("key").str.ends_with(f" vs {baseline}")
    )  # fmt: skip
    for r in diffs.iter_rows(named=True):
        pos = r["key"].split(" vs ")[0]
        lo, hi, d = float(r["lo"]), float(r["hi"]), float(r["value"])
        if lo > 0:
            continue

        def p10(m: str, pos: str = pos) -> float | None:
            hit = base.filter(
                (pl.col("model") == m) & (pl.col("scope") == "position") & (pl.col("key") == pos)
                & (pl.col("metric") == "p_at_10")
            )  # fmt: skip
            return float(hit.row(0, named=True)["value"]) if hit.height else None

        mine, theirs = p10(model), p10(baseline)
        if mine is None or theirs is None:
            continue
        seasons = r["seasons"]
        if before is not None and int(str(seasons).split("-")[-1]) >= before:
            continue
        notes[pos] = (
            f"Note for {pos}: in the {seasons} backtest the Radar's top 10 at {pos} hit "
            f"{mine:.1%} of the time and a list of last week's top scorers {theirs:.1%} "
            f"(difference {100 * d:+.1f} points, 95% interval {100 * lo:+.1f} to {100 * hi:+.1f}): "
            f"at {pos} this list is no better than last week's points."
        )
    return notes
