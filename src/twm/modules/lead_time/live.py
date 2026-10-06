"""The 2026 live lead-time tracker (LOCAL ONLY): the owner's league syncs store ESPN's
``percent_owned`` of every free agent of that league at each sync (``league_free_agents`` in
data/league.duckdb, opened read-only), and the Radar's 2026 lists sit in the predictions store.

Differences from the historical study (docs/lead_time.md "2026 live"):

- the % comes from ESPN directly at ``synced_at`` (naive UTC), not from FantasyPros; ESPN gives
  the CURRENT %, so rows of several weeks written by one sync carry the same snapshot: the path
  is keyed on ``synced_at`` (one value per waiver period: the latest sync in it), never on the
  row's ``week``;
- only players free in the owner's league are seen: a player absent from a sync may simply be
  rostered there, so absence is NOT a low % (no 0-fill, unlike :mod:`.crowd`), and a crossing
  that happens after the owner's league rostered him is invisible (state ``left_pool``);
- the path starts with the first sync (2026-09-30 on): a player already over the threshold at
  his first observation is not an add.

No league id or name is read or returned.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl

from twm.modules.lead_time import PRIMARY
from twm.modules.lead_time import crowd as cr
from twm.modules.lead_time import flags as fl

LIVE_SCHEMA = {"season": pl.Int32, "gsis_id": pl.String, "synced_at": pl.Datetime("us"),
               "pct": pl.Float64}  # fmt: skip


def read_live_owned(con: duckdb.DuckDBPyConnection, season: int) -> pl.DataFrame:
    """One row per (season, gsis_id, synced_at) of the league syncs: ESPN's percent_owned
    (the largest when several leagues are synced at the same moment). ``con``: the league
    store opened read-only (:func:`twm.league.store.connect` with ``read_only=True``)."""
    df = con.execute(
        """SELECT season, gsis_id, synced_at, max(percent_owned) AS pct
        FROM league_free_agents WHERE season = ? AND gsis_id IS NOT NULL
          AND percent_owned IS NOT NULL AND synced_at IS NOT NULL GROUP BY ALL""",
        [int(season)],
    ).pl()
    return df.cast(LIVE_SCHEMA).sort("gsis_id", "synced_at")  # type: ignore[arg-type]


def live_path(rows: pl.DataFrame, windows: pl.DataFrame) -> pl.DataFrame:
    """Per (season, gsis_id, period): the % at the player's latest sync in the period (absent
    periods stay absent). Columns: season, gsis_id, period, synced_at, pct."""
    p = cr.periods_at(rows, windows, "synced_at")
    return (
        p.sort("season", "gsis_id", "synced_at")
        .group_by(["season", "gsis_id", "period"], maintain_order=True)
        .last()
        .select("season", "gsis_id", "period", "synced_at", "pct")
        .sort("season", "gsis_id", "period")
    )


def live_lists(store: Path, season: int) -> pl.DataFrame:
    """The Radar's lists of ``season`` from the predictions store (read-only), one model
    version per (week, position): the latest written, then the larger version id. Columns:
    season, week, position, gsis_id, rank, tier, kind."""
    from twm.modules.waiver_radar.production import MODULE

    con = duckdb.connect(str(store), read_only=True)
    try:
        df = con.execute(
            """SELECT season, week, rank_group AS position, entity_id AS gsis_id, rank, tier,
                      kind, created_at, model_version
            FROM predictions WHERE module = ? AND season = ?""",
            [MODULE, int(season)],
        ).pl()
    finally:
        con.close()
    key = ["season", "week", "position"]
    chosen = (
        df.group_by([*key, "model_version"])
        .agg(pl.col("created_at").max().alias("_w"))
        .sort([*key, "_w", "model_version"], descending=[False, False, False, True, True])
        .group_by(key, maintain_order=True)
        .first()
        .select(*key, "model_version")
    )
    out = df.join(chosen, on=[*key, "model_version"], how="inner")
    return out.select(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32), "position", "gsis_id",
        pl.col("rank").cast(pl.Int32), pl.col("tier").cast(pl.String), "kind",
    ).sort("season", "week", "position", "rank")  # fmt: skip


LIVE_STATES = ("added", "already", "below", "left_pool", "unseen")


def live_tracker(
    path: pl.DataFrame, lists: pl.DataFrame, threshold: float = PRIMARY
) -> pl.DataFrame:
    """Per player of the syncs or the Radar's lists (LOCAL ONLY): his first and latest observed
    period and %, the crossing period (first period at or above ``threshold`` after a first
    observation under it), the state (added / already: over it at his first sync / below at
    the latest sync / left_pool: under it when last seen and missing from the latest sync,
    maybe rostered in the owner's league / unseen: listed, never in a sync), the Radar's first
    flag week per level and, for added players, lead_<level> (crossing - flag; + = Radar first)."""
    keys = ["season", "gsis_id"]
    p = path.sort(*keys, "period")
    obs = p.group_by(keys, maintain_order=True).agg(
        pl.col("period").first().alias("first_period"), pl.col("pct").first().alias("first_pct"),
        pl.col("period").last().alias("last_period"), pl.col("pct").last().alias("last_pct"),
    )  # fmt: skip
    cross = (
        p.join(obs.select(*keys, "first_period", "first_pct"), on=keys)
        .filter((pl.col("first_pct") < threshold) & (pl.col("period") > pl.col("first_period")))
        .filter(pl.col("pct") >= threshold)
        .group_by(keys)
        .agg(pl.col("period").min().alias("cross_period"))
    )
    first = fl.first_flags(fl.flag_weeks(lists)) if lists.height else None
    people = obs.select(keys)
    if first is not None:
        people = pl.concat([people, first.select(keys)]).unique(maintain_order=True)
    df = people.join(obs, on=keys, how="left").join(cross, on=keys, how="left")
    if first is not None:
        df = df.join(first.select(*keys, "must_add", "spec_plus", "listed"), on=keys, how="left")
    else:
        df = df.with_columns(
            [pl.lit(None, dtype=pl.Int32).alias(c) for c in ("must_add", "spec_plus", "listed")]
        )
    latest = path.get_column("period").max() if path.height else None
    state = (
        pl.when(pl.col("first_period").is_null()).then(pl.lit("unseen"))
        .when(pl.col("cross_period").is_not_null()).then(pl.lit("added"))
        .when(pl.col("first_pct") >= threshold).then(pl.lit("already"))
        .when(pl.col("last_period") == latest).then(pl.lit("below"))
        .otherwise(pl.lit("left_pool"))
    )  # fmt: skip
    added = pl.col("cross_period").is_not_null()
    lead = {lv: pl.col("cross_period") - pl.col(lv) for lv in ("must_add", "spec_plus", "listed")}
    return df.with_columns(
        state.alias("state"),
        *[pl.when(added).then(e).cast(pl.Int32).alias(f"lead_{lv}") for lv, e in lead.items()],
    ).sort(keys)


def live_summary(tracker: pl.DataFrame) -> pl.DataFrame:
    """Counts for the local report: per state, the players and how many the Radar had flagged
    at each level (and, of the added ones, flagged before the crossing)."""
    return (
        tracker.group_by("state")
        .agg(
            pl.len().cast(pl.Int64).alias("n"),
            *[
                pl.col(lv).is_not_null().sum().cast(pl.Int64).alias(f"flagged_{lv}")
                for lv in ("must_add", "listed")
            ],
            *[
                (pl.col(f"lead_{lv}") >= 1).sum().cast(pl.Int64).alias(f"before_{lv}")
                for lv in ("must_add", "listed")
            ],
        )
        .sort("state")
    )
