"""The 80% range of Regression Watch's rest-of-season projection (feature #4, owner 2026-10-04).

One machinery, shared with the My League trade checker (:func:`twm.league.trade.residual_pool`):

- **Misses** (:func:`graded_misses`): the frozen backtest's graded rows (the player had 3+
  games after the list), miss = his actual rest-of-season points per game minus the projection.
- **The pool** (:func:`near_horizon`): one position's misses whose weeks left (``horizon``)
  are within :data:`HORIZON_WINDOW` of the list's, else the nearest weeks left available.
- **The range** (:func:`walk_forward`): the projection plus the pool's 10th and 90th
  percentiles (:data:`LEVEL`), so 80% of comparable past misses fall inside it; a pool with
  fewer than :data:`MIN_MISSES` misses gives no range (NULL).
- **Walk-forward**: a list of season S draws only on misses of seasons before S (the live 2026
  lists: the pinned 2011-2025 backtest; the frozen 2011 lists have no earlier season: NULL).
- **Coverage** (:func:`coverage`): the share of graded rows whose actual rest-of-season points
  per game fell inside their range, by position (docs/regression_watch.md, /methodology).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

POSITIONS = ("QB", "RB", "WR", "TE")
LEVEL = 0.80  # the range's coverage: the 10th to the 90th percentile
HORIZON_WINDOW = 2  # misses whose weeks left are within this of the list's
MIN_MISSES = 30  # fewer misses in the pool: no range
ROUND = 6  # like the projection's inputs (projection.STATE_ROUND)
KEYS = ["season", "week", "entity_id"]
RANGE_COLUMNS = ("range_lo", "range_hi", "range_misses", "range_weeks_lo", "range_weeks_hi",
                 "range_first", "range_last")  # fmt: skip


def graded_misses(predictions: pl.DataFrame, outcomes: pl.DataFrame) -> pl.DataFrame:
    """The backtest rows with an actual rest-of-season PPG and a projection (``score``), with
    ``miss`` = ros_ppg - score (season, week, entity_id, rank_group, score, horizon, ros_ppg)."""
    return (
        predictions.select("season", "week", "entity_id", "rank_group", "score", "horizon")
        .join(outcomes.select("season", "week", "entity_id", "ros_ppg"), on=KEYS, how="inner")
        .filter(pl.col("ros_ppg").is_not_null() & pl.col("score").is_not_null())
        .with_columns((pl.col("ros_ppg") - pl.col("score")).alias("miss"))
    )


def pinned_misses(path: Path | None = None, root: Path | None = None) -> pl.DataFrame:
    """:func:`graded_misses` of the approved Regression Watch frozen backtest (every file's
    sha256 checked before it is read; :class:`twm.pins.PinError` otherwise)."""
    from twm import pins
    from twm.modules.regression_watch import frozen as fz
    from twm.modules.regression_watch.production import PIN_KEY

    snap = fz.load_snapshot(pins.get_pin(PIN_KEY, path), root)
    return graded_misses(snap["predictions"], snap["outcomes"])


@dataclass(frozen=True)
class Pool:
    misses: np.ndarray  # in the backtest's row order (the trade checker resamples by index)
    horizons: tuple[int, int]  # (fewest, most) weeks left used
    seasons: tuple[int, int]  # (first, last) season used


def near_horizon(
    misses: pl.DataFrame, position: str, horizon: int, window: int = HORIZON_WINDOW
) -> Pool | None:
    """``position``'s misses within ``window`` weeks left of ``horizon``, else those at the
    nearest weeks left available; None when the position has none."""
    sub = misses.filter(pl.col("rank_group") == position)
    if sub.height == 0:
        return None
    sub = sub.with_columns((pl.col("horizon") - horizon).abs().alias("gap"))
    gap = max(window, int(sub.get_column("gap").min()))  # type: ignore[arg-type]
    sub = sub.filter(pl.col("gap") <= gap)
    h, s = sub.get_column("horizon"), sub.get_column("season")
    return Pool(sub.get_column("miss").to_numpy(), (int(h.min()), int(h.max())),  # type: ignore[arg-type]
                (int(s.min()), int(s.max())))  # type: ignore[arg-type]  # fmt: skip


def quantiles(pool: Pool | None, level: float = LEVEL) -> tuple[float, float] | None:
    """The pool's (lower, upper) percentiles of the central ``level`` (10th and 90th for 80%);
    None without a pool of :data:`MIN_MISSES` misses."""
    if pool is None or len(pool.misses) < MIN_MISSES:
        return None
    tail = (1.0 - level) / 2.0
    lo, hi = np.quantile(pool.misses, [tail, 1.0 - tail])
    return float(lo), float(hi)


def pools(rows: pl.DataFrame, misses: pl.DataFrame, level: float = LEVEL) -> pl.DataFrame:
    """One row per (season, rank_group, horizon) of ``rows``: the walk-forward pool (misses of
    seasons before ``season`` only) and its percentiles ``q_lo`` / ``q_hi`` (NULL: no range)."""
    keys = ["season", "rank_group", "horizon"]
    recs: list[dict[str, Any]] = []
    for season, pos, horizon in rows.select(keys).unique().sort(keys).iter_rows():
        pool = near_horizon(misses.filter(pl.col("season") < season), pos, horizon)
        q = quantiles(pool, level)
        recs.append({
            "season": season, "rank_group": pos, "horizon": horizon,
            "q_lo": q[0] if q else None, "q_hi": q[1] if q else None,
            "range_misses": len(pool.misses) if pool else 0,
            "range_weeks_lo": pool.horizons[0] if pool else None,
            "range_weeks_hi": pool.horizons[1] if pool else None,
            "range_first": pool.seasons[0] if pool else None,
            "range_last": pool.seasons[1] if pool else None,
        })  # fmt: skip
    schema = {**{k: rows.schema[k] for k in keys}, "q_lo": pl.Float64, "q_hi": pl.Float64,
              **dict.fromkeys(RANGE_COLUMNS[2:], pl.Int32)}  # fmt: skip
    return pl.DataFrame(recs, schema=schema, orient="row")


def walk_forward(rows: pl.DataFrame, misses: pl.DataFrame, level: float = LEVEL) -> pl.DataFrame:
    """``rows`` (season, rank_group, horizon, score, ...) in their order, plus
    :data:`RANGE_COLUMNS`: the range = score + the walk-forward pool's percentiles (rounded)."""
    keys = ["season", "rank_group", "horizon"]
    out = (rows.with_row_index("_row").join(pools(rows, misses, level), on=keys, how="left")
           .sort("_row"))  # fmt: skip
    return out.with_columns(
        (pl.col("score") + pl.col("q_lo")).round(ROUND).alias("range_lo"),
        (pl.col("score") + pl.col("q_hi")).round(ROUND).alias("range_hi"),
    ).drop("_row", "q_lo", "q_hi")


def range_reason(r: dict[str, Any], level: float = LEVEL) -> dict[str, Any] | None:
    """The ``range`` entry of a row's reasons (None without a range)."""
    if r.get("range_lo") is None or r.get("range_hi") is None:
        return None
    return {"level": level, "lo": r["range_lo"], "hi": r["range_hi"],
            "misses": r["range_misses"], "weeks_left": [r["range_weeks_lo"], r["range_weeks_hi"]],
            "seasons": f"{r['range_first']}-{r['range_last']}"}  # fmt: skip


def with_ranges(table: pl.DataFrame, misses: pl.DataFrame, horizon: int) -> pl.DataFrame:
    """A weekly list (:func:`twm.modules.regression_watch.weekly.score_week`: season, position,
    ppg_ros, reasons_json) with ``range_lo`` / ``range_hi`` and each reasons' ``range``; the
    pool: ``misses`` of seasons before the list's, at ``horizon`` weeks left."""
    if table.height == 0:
        return table
    rows = table.select(pl.col("season"), pl.col("position").alias("rank_group"),
                        pl.lit(int(horizon), dtype=pl.Int32).alias("horizon"),
                        pl.col("ppg_ros").alias("score"))  # fmt: skip
    wf = walk_forward(rows, misses)
    texts = []
    for text, r in zip(table.get_column("reasons_json"), wf.iter_rows(named=True), strict=True):
        d = json.loads(text)
        if (rr := range_reason(r)) is not None:
            d["range"] = rr
        texts.append(json.dumps(d, sort_keys=True, separators=(",", ":")))
    return table.with_columns(wf.get_column("range_lo"), wf.get_column("range_hi"),
                              pl.Series("reasons_json", texts, dtype=pl.String))  # fmt: skip


def coverage(rows: pl.DataFrame) -> pl.DataFrame:
    """By position (POSITIONS order, then 'All'): the graded rows with a range (``n``), how many
    actual rest-of-season PPGs (``ros_ppg``) fell inside [range_lo, range_hi], below or above
    it, and the share inside (``coverage``)."""
    g = rows.filter(pl.col("ros_ppg").is_not_null() & pl.col("range_lo").is_not_null())
    g = g.with_columns(pl.lit("All").alias("_all"))
    parts = []
    for by in ("rank_group", "_all"):
        parts.append(g.group_by(pl.col(by).alias("position")).agg(
            pl.len().cast(pl.Int64).alias("n"),
            ((pl.col("ros_ppg") >= pl.col("range_lo")) & (pl.col("ros_ppg") <= pl.col("range_hi")))
            .sum().cast(pl.Int64).alias("inside"),
            (pl.col("ros_ppg") < pl.col("range_lo")).sum().cast(pl.Int64).alias("below"),
            (pl.col("ros_ppg") > pl.col("range_hi")).sum().cast(pl.Int64).alias("above"),
            pl.col("season").min().alias("first"), pl.col("season").max().alias("last"),
        ))  # fmt: skip
    order = {p: i for i, p in enumerate((*POSITIONS, "All"))}
    out = pl.concat(parts).with_columns((pl.col("inside") / pl.col("n")).alias("coverage"))
    return out.sort(pl.col("position").replace_strict(order, default=len(order)))


def backtest_coverage(misses: pl.DataFrame, level: float = LEVEL) -> pl.DataFrame:
    """:func:`coverage` of the frozen backtest itself, every row ranged walk-forward (only the
    misses of earlier seasons): how honest the 80% is."""
    return coverage(walk_forward(misses, misses, level))
