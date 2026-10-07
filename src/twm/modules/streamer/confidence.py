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

- **K, a kicker who did not kick in his team's latest game** (``is_team_kicker`` false: practice
  squad, camp, released): the start rate of such pool kickers in the backtest seasons (flags
  from the streamer dataset), never a probability bin. Each fold's calibration has its own
  scale (the 2026 model scores such a kicker above every earlier fold's), so the bins could
  give him the chance of a kicking kicker (audit 2026-10-06). Lists scored before this rule
  keep their stored chance.

**Point in time**: a list of season S reads only the backtest seasons before S (the Radar's
rule): a reconstructed 2020 list never rests on 2020-2025 outcomes.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
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


@dataclass
class KConfidence(cf.Confidence):
    """The K bins plus ``idle``: (rows, starts) of the backtest's pool kickers who had not
    kicked in their team's latest game, the evidence of such a kicker's chance
    (:func:`k_band`); None without the dataset's flags or with fewer than ``min_rows``."""

    idle: tuple[int, int] | None = None


FLAG_KEYS = ("season", "week", "entity_id")


def kicker_flags(dataset: Path) -> pl.DataFrame:
    """(season, week, entity_id, is_team_kicker) of the streamer dataset's K rows;
    ValueError when the file is missing."""
    if not Path(dataset).exists():
        raise ValueError(f"the streamer dataset {dataset} is missing (a kicker who did not "
                         "kick reads its flags): run `uv run twm streamer dataset`")  # fmt: skip
    d = pl.read_parquet(dataset, columns=["position", *FLAG_KEYS, "is_team_kicker"])
    return d.filter(pl.col("position") == "K").select(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32), "entity_id",
        "is_team_kicker",
    ).unique(list(FLAG_KEYS))  # fmt: skip


def idle_counts(rows: pl.DataFrame, flags: pl.DataFrame) -> tuple[int, int]:
    """(rows, starts) of the backtest ``rows`` (season, week, entity_id, y) whose kicker had not
    kicked in his team's latest game (``flags``: :func:`kicker_flags`); ValueError when a row
    has no flag (a dataset that does not cover the backtest)."""
    keyed = rows.with_columns(pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32))
    j = keyed.join(flags, on=list(FLAG_KEYS), how="left")
    missing = j.filter(pl.col("is_team_kicker").is_null()).height
    if missing:
        raise ValueError(f"{missing} K backtest rows have no is_team_kicker in the streamer "
                         "dataset: run `uv run twm streamer dataset`")  # fmt: skip
    idle = j.filter(~pl.col("is_team_kicker"))
    return idle.height, int(idle.get_column("y").sum())


def k_confidence(frames: dict[str, pl.DataFrame], season: int, min_rows: int = K_MIN_ROWS,
                 flags: pl.DataFrame | None = None) -> KConfidence:  # fmt: skip
    """The K bins and tier table from the K snapshot's seasons before ``season``; with the
    dataset's ``flags`` (:func:`kicker_flags`), also the not-kicking evidence (``idle``)."""
    p = frames["predictions"].select(
        "season", "week", pl.col("rank_group").alias("position"), "entity_id", "score", "rank"
    )
    rows = p.join(frames["outcomes"].select("season", "week", "entity_id", "y_start"),
                  on=["season", "week", "entity_id"], how="left")  # fmt: skip
    first, last = seasons_before(rows.get_column("season").unique().to_list(), season)
    rows = rows.filter(pl.col("season").is_between(first, last)).rename({"y_start": "y"})
    y = rows.get_column("y").cast(pl.Int64).to_numpy()
    bins = cf.similar_bins(rows.get_column("score").to_numpy(), y, min_rows)
    conf = KConfidence(bins, "logit_k", "y_start", (first, last), rows.height)
    history = rows.select("season", "week", "position", "score", "rank", "y")
    conf.tiers = cf.tier_table(conf, history, top=TOP_N)
    conf.history = history
    if flags is not None:
        n, hits = idle_counts(rows, flags)
        conf.idle = (n, hits) if n >= min_rows else None
    return conf


def k_band(conf: cf.Confidence, score: np.ndarray, kicked: Sequence[bool | None]) -> pl.DataFrame:
    """``conf.band(score)``, except that a kicker who had not kicked in his team's latest game
    (``kicked`` False) gets the start rate of such pool kickers (``conf.idle``) with its 90%
    Wilson interval and no probability bin (band_from / band_to NULL)."""
    band = conf.band(score)
    idle = getattr(conf, "idle", None)
    if idle is None:
        return band
    n, hits = idle
    lo, hi = cf.wilson(hits, n)
    cols = {c: band.get_column(c).to_list() for c in band.columns}
    for i, k in enumerate(kicked):
        if k is not None and not k:
            cols["chance"][i], cols["band_lo"][i], cols["band_hi"][i] = hits / n, lo, hi
            cols["band_n"][i], cols["band_hits"][i] = n, hits
            cols["band_from"][i] = cols["band_to"][i] = None
    return pl.DataFrame(cols, schema=band.schema)


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


def k_band_json(row: dict[str, Any]) -> str:
    """The store's ``band`` of a K row: :func:`cf.band_json`; a kicker who had not kicked in his
    team's latest game (:func:`k_band`: no probability bin) gets ``basis: not_kicking``."""
    if row["band_from"] is not None:
        return cf.band_json(row)
    r = cf.BAND_DECIMALS
    return json.dumps(
        {"basis": "not_kicking", "chance": round(row["chance"], r), "lo": round(row["band_lo"], r),
         "hi": round(row["band_hi"], r), "n": int(row["band_n"]), "hits": int(row["band_hits"]),
         "level": cf.BAND_LEVEL},
        sort_keys=True, separators=(",", ":"),
    )  # fmt: skip


def band_text(chance: float, lo: float, hi: float, position: str, idle: bool = False) -> str:
    """'41% (similar kickers started 36-46%)'; D/ST: '(the rule's picks at this rank ...)';
    ``idle``: a kicker who had not kicked in his team's latest game."""
    who = "similar kickers" if position == "K" else "this rank's past picks"
    if idle:
        who = "pool kickers who had not kicked in their team's latest game"
    d = 0 if round(100 * lo) != round(100 * hi) else 1
    return f"{100 * chance:.0f}% ({who} started {100 * lo:.{d}f}-{100 * hi:.{d}f}%)"
