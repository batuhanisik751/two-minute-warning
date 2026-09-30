"""How sure is a streamer pick? The 'chance' with its range and the priority tier (S2a), read
off the frozen backtest of the list's method, point in time.

Both positions reuse the Radar's machinery (:mod:`twm.modules.waiver_radar.confidence`): bins of
at least ``min_rows`` backtest picks, merged until the start rate never goes down as the
ranking value goes up (pool-adjacent-violators), each with a 90% Wilson interval; the chance a
list shows is the bin's observed start rate (``y_start``: a top-12 finish next week), and the
tier comes from it (must-add 50%+, speculative 25-50%, watch below).

- **K** (the logistic regression): bins of the model's calibrated probability, as in the Radar
  (:data:`K_MIN_ROWS` picks per bin: a K backtest season has about 180 graded pool rows).
- **DST** (the next-opponent rule, no probability): bins of the **rank** the rule gave (rank 1,
  2, ... merged into groups of at least :data:`DST_MIN_ROWS` picks): "the rule's #1 pick
  started 50% of the time". The ranking value is minus the rank, so a better rank never shows
  a lower chance.

**Point in time**: a list of season S reads only the backtest seasons before S (the Radar's
rule): a reconstructed 2020 list never rests on 2020-2025 outcomes.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import polars as pl

from twm.modules.waiver_radar import confidence as cf

K_MIN_ROWS = 200
DST_MIN_ROWS = 100
TOP_N = cf.TOP_N  # every pool row of a streamer list gets a tier (lists have < 25 rows)


def seasons_before(seasons: list[int], season: int) -> tuple[int, int]:
    """(first, last) backtest season a list of ``season`` may learn from."""
    before = sorted(s for s in seasons if s < int(season))
    if not before:
        raise ValueError(
            f"no backtest season before {season} to set the chance and the priority from (the "
            f"backtest starts in {min(seasons) if seasons else '?'})"
        )
    return before[0], before[-1]


def k_confidence(frames: dict[str, pl.DataFrame], season: int, min_rows: int = K_MIN_ROWS):
    """The K bins and tier table from the K snapshot's seasons before ``season``."""
    p = frames["predictions"].select(
        "season", "week", pl.col("rank_group").alias("position"), "entity_id", "score", "rank"
    )
    rows = p.join(frames["outcomes"].select("season", "week", "entity_id", "y_start"),
                  on=["season", "week", "entity_id"], how="left")  # fmt: skip
    first, last = seasons_before(rows.get_column("season").unique().to_list(), season)
    rows = rows.filter(pl.col("season").is_between(first, last)).rename({"y_start": "y"})
    y = rows.get_column("y").cast(pl.Int64).to_numpy()
    bins = cf.similar_bins(rows.get_column("score").to_numpy(), y, min_rows)
    conf = cf.Confidence(bins, "logit_k", "y_start", (first, last), rows.height)
    history = rows.select("season", "week", "position", "score", "rank", "y")
    conf.tiers = cf.tier_table(conf, history, top=TOP_N)
    conf.history = history
    return conf


def dst_confidence(hit_rates: pl.DataFrame, season: int, min_rows: int = DST_MIN_ROWS):
    """The D/ST rank bins (value = minus the rank) and tier table from the hit-rate table's
    seasons before ``season``."""
    first, last = seasons_before(hit_rates.get_column("season").unique().to_list(), season)
    h = hit_rates.filter(pl.col("season").is_between(first, last))
    per_rank = h.group_by("rank").agg(pl.col("lists").sum(), pl.col("starts").sum()).sort("rank")
    ranks = per_rank.get_column("rank").to_numpy()
    n = per_rank.get_column("lists").to_numpy()
    hits = per_rank.get_column("starts").to_numpy()
    value = np.repeat(-ranks.astype(np.float64), n)
    y = np.concatenate([np.r_[np.ones(k, np.int64), np.zeros(m - k, np.int64)]
                        for k, m in zip(hits, n, strict=True)])  # fmt: skip
    bins = cf.similar_bins(value, y, min_rows)
    conf = cf.Confidence(bins, "baseline_opponent_dst", "y_start", (first, last), int(n.sum()))
    band = conf.band(-ranks.astype(np.float64))
    tier = [cf.tier_of(float(c)) for c in band.get_column("chance").to_list()]
    t = per_rank.with_columns(pl.Series("tier", tier)).filter(pl.col("rank") <= TOP_N)
    n_lists = int(h.filter(pl.col("rank") == 1).get_column("lists").sum())
    rows = []
    for name in cf.TIER_ORDER:
        sub = t.filter(pl.col("tier") == name)
        k, m = int(sub["starts"].sum()), int(sub["lists"].sum())
        cut = conf.cutoffs.get(name) if name != "watch" else None
        rows.append((name, None if cut is None else -cut, n_lists, m, k, k / m if m else None,
                     m / n_lists if n_lists else None))  # fmt: skip
    conf.tiers = pl.DataFrame(
        rows, orient="row",
        schema={"tier": pl.String, "worst_rank": pl.Float64, "lists": pl.Int64, "rows": pl.Int64,
                "hits": pl.Int64, "rate": pl.Float64, "per_list": pl.Float64},
    )  # fmt: skip
    return conf


def dst_band_json(row: dict[str, Any]) -> str:
    """The store's ``band`` of a D/ST row: the chance, its 90% interval, the evidence and the
    rank range of its bin (``basis: rank``: no model probability)."""
    r = cf.BAND_DECIMALS
    return json.dumps(
        {"basis": "rank", "chance": round(row["chance"], r), "lo": round(row["band_lo"], r),
         "hi": round(row["band_hi"], r), "n": int(row["band_n"]), "hits": int(row["band_hits"]),
         "rank_from": int(-row["band_to"]), "rank_to": int(-row["band_from"]),
         "level": cf.BAND_LEVEL},
        sort_keys=True, separators=(",", ":"),
    )  # fmt: skip


def band_text(chance: float, lo: float, hi: float, position: str) -> str:
    """'41% (similar kickers started 36-46%)'; D/ST: '(the rule's picks at this rank ...)'."""
    who = "similar kickers" if position == "K" else "this rank's past picks"
    d = 0 if round(100 * lo) != round(100 * hi) else 1
    return f"{100 * chance:.0f}% ({who} started {100 * lo:.{d}f}-{100 * hi:.{d}f}%)"
