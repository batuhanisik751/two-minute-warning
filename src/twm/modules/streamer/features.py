"""The streamer's features (S1c): what a pooled kicker's or team defense's situation looked like
at the Tuesday as-of after week N, for the week N+1 pick-up decision.

One row per pool row (:mod:`twm.modules.streamer.pool`, K and DST). Families (every column is
registered in :mod:`twm.registry`, module ``streamer``; docs/glossary.md explains each one):

- **Entity**: the pool's own numbers under streamer names (points per game to date and its
  rank, the preseason rank, games so far) and the points of the latest game.
- **Kicker** (NULL on DST rows): field-goal and extra-point attempts per game, long attempts,
  accuracy by distance (0-39, 40-49, 50+ yards; blocked kicks are not in nflverse's distance
  buckets), and ``is_team_kicker`` (he kicked in his team's latest game with a kick attempt).
- **Defense** (NULL on K rows): sacks, takeaways, touchdowns and ESPN points allowed per game.
- **Team**: the entity's as-of team's offense: points, red-zone trips and stalls per game.
- **Next opponent**: the team's week N+1 opponent: points it allowed and scored per game, its
  red-zone defense, sacks it allowed and giveaways per game (NULL on a bye).
- **Next game**: home or away, and the venue's roof (dome, retractable) as known from earlier
  games at that stadium (NULL when there is none yet).
- **Experts**: the entity's rank on FantasyPros' latest weekly K or DST page (late 2020 on).

No betting lines or weather of the upcoming game (PROJECT_SPEC 6.4). "Per game" divides by the
team's (or the kicker's) regular-season games of season S visible at the as-of in the input the
number comes from, so a game whose play-by-play is not public yet is left out of both the sum
and the count. A **red-zone trip** is a drive (``fact_play.fixed_drive``) with a snap at the
opponent's 20 or closer (pass, run, field goal, kneel, spike or a penalty snap; two-point tries
excluded); a **stall** is a trip that did not end in a touchdown (``fixed_drive_result``).

Point in time, as in the Radar (:mod:`twm.modules.waiver_radar.features`): :func:`features_for`
reads every input through an :class:`~twm.asof.AsOfView` (the reference, run through the
leakage harness in tests/test_streamer_features.py); :func:`features_history` reads each season's
inputs once with ``available_at`` and filters them per as-of (every input reads ONE table, per
game aggregates only, so filtering then aggregating equals the view). Tests check both paths
give the same frame. ``fact_schedule``'s venue columns are masked until ``slot_available_at`` in
both paths, and a venue is used only when the schedule's ``stadium`` is visible.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import duckdb
import polars as pl

from twm.asof import asof_filter
from twm.modules.streamer.pool import StreamerRules
from twm.scoring_kdst import score_defense_sql, score_kicking_sql

# --------------------------------------------------------------------------------------
# Columns
# --------------------------------------------------------------------------------------

ENTITY_FEATURES = (
    "kdst_points_per_game", "kdst_ppg_rank", "kdst_preseason_rank", "kdst_games_to_date",
    "kdst_points_last",
)  # fmt: skip
KICKER_FEATURES = (
    "is_team_kicker", "k_fg_att_per_game", "k_pat_att_per_game", "k_fg_att_40_plus_per_game",
    "k_fg_pct_0_39", "k_fg_pct_40_49", "k_fg_pct_50_plus",
)  # fmt: skip
DEFENSE_FEATURES = (
    "dst_sacks_per_game", "dst_takeaways_per_game", "dst_tds_per_game",
    "dst_points_allowed_per_game",
)  # fmt: skip
TEAM_FEATURES = (
    "team_points_per_game", "team_games_to_date", "team_rz_trips_per_game",
    "team_rz_stalls_per_game", "team_rz_stall_rate",
)  # fmt: skip
OPPONENT_FEATURES = (
    "next_opp_points_allowed_per_game", "next_opp_rz_trips_allowed_per_game",
    "next_opp_rz_stall_rate_forced", "next_opp_points_per_game",
    "next_opp_sacks_allowed_per_game", "next_opp_giveaways_per_game", "next_opp_games_to_date",
)  # fmt: skip
GAME_FEATURES = ("next_is_home", "next_venue_dome", "next_venue_retractable")
EXPERT_FEATURES = ("weekly_ecr_rank", "weekly_ecr_listed")
FEATURE_FAMILIES: dict[str, tuple[str, ...]] = {
    "entity": ENTITY_FEATURES,
    "kicker": KICKER_FEATURES,
    "defense": DEFENSE_FEATURES,
    "team": TEAM_FEATURES,
    "next opponent": OPPONENT_FEATURES,
    "next game": GAME_FEATURES,
    "experts": EXPERT_FEATURES,
}
FEATURE_COLUMNS: tuple[str, ...] = tuple(c for f in FEATURE_FAMILIES.values() for c in f)
KEY_COLUMNS = ("season", "week", "position", "entity_id")
# What compute_features needs from the pool rows (S1b POOL_COLUMNS has them all).
REQUIRED_POOL_COLUMNS = (
    "season", "week", "position", "entity_id", "gsis_id", "team", "ppg_to_date", "ppg_pos_rank",
    "preseason_pos_rank", "games_to_date", "is_team_kicker",
)  # fmt: skip
# Pool columns that are also features under the same name (the dataset keeps one copy).
POOL_FEATURES = ("is_team_kicker",)

_INT_FEATURES = (
    "kdst_ppg_rank", "kdst_preseason_rank", "kdst_games_to_date", "team_games_to_date",
    "next_opp_games_to_date", "weekly_ecr_rank",
)  # fmt: skip
_BOOL_FEATURES = (
    "is_team_kicker", "next_is_home", "next_venue_dome", "next_venue_retractable",
    "weekly_ecr_listed",
)  # fmt: skip
FEATURE_SCHEMA: dict[str, pl.DataType] = {
    **dict.fromkeys(FEATURE_COLUMNS, pl.Float64()),
    **dict.fromkeys(_INT_FEATURES, pl.Int32()),
    **dict.fromkeys(_BOOL_FEATURES, pl.Boolean()),
}
FLOAT_DECIMALS = 6  # floats are rounded so the two paths and two runs agree to the bit

# Red zone: a snap at the opponent's 20 or closer. Snaps that count (kickoffs, punts and tries
# do not: a kickoff's yardline is the kicking spot).
RED_ZONE_YARDLINE = 20
RED_ZONE_PLAY_TYPES = ("pass", "run", "field_goal", "qb_kneel", "qb_spike", "no_play")
# Distance buckets for accuracy: nflverse's fg_made_* / fg_missed_* columns, summed.
FG_BUCKETS: dict[str, tuple[str, ...]] = {
    "0_39": ("0_19", "20_29", "30_39"),
    "40_49": ("40_49",),
    "50_plus": ("50_59", "60_"),
}
# fact_game roof values -> the venue's kind (open / closed are game-day states of a
# retractable roof, never used as such).
RETRACTABLE_ROOFS = ("open", "closed")
DOME_ROOFS = ("dome",)
SCHEDULE_MASKED = ("location", "stadium")  # fact_schedule slot columns this module reads


# --------------------------------------------------------------------------------------
# Inputs: one season, read through a view (reference) or from the warehouse (batch)
# --------------------------------------------------------------------------------------


class SqlSource(Protocol):
    def sql(self, query: str, params: Sequence[Any] | None = None) -> pl.DataFrame: ...


class _Warehouse:
    """The warehouse itself (every row, future included) behind the ``sql`` interface."""

    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self._con = con

    def sql(self, query: str, params: Sequence[Any] | None = None) -> pl.DataFrame:
        return self._con.execute(query, params or []).pl()


def _bucket_sum(kind: str, bucket: str) -> str:
    return " + ".join(f"COALESCE({kind}_{b}, 0)" for b in FG_BUCKETS[bucket])


def input_sql(season: int, rules: StreamerRules) -> dict[str, str]:
    """The SQL of every input of ``season``, by name. The same text runs through an AsOfView
    (tables are their point-in-time views) and on the warehouse (every row). Each query reads
    ONE table; per-game aggregates group rows of one game, which share one ``available_at``."""
    s = int(season)
    kp = score_kicking_sql(rules.kicking, table_alias="k")
    dp = score_defense_sql(rules.defense, table_alias="d")
    buckets = ", ".join(
        f"{_bucket_sum(kind, b)} AS {kind}_{b}"
        for b in FG_BUCKETS
        for kind in ("fg_made", "fg_missed")
    )
    rz_types = ", ".join(f"'{t}'" for t in RED_ZONE_PLAY_TYPES)
    rz = (
        f"yardline_100 <= {RED_ZONE_YARDLINE} AND play_type IN ({rz_types}) "
        "AND COALESCE(two_point_attempt, 0) = 0"
    )
    tds = ("kickoff_return_tds", "punt_return_tds", "interception_return_tds",
           "fumble_return_tds", "blocked_kick_return_tds")  # fmt: skip
    return {
        "kicks": f"""
            SELECT k.player_id AS gsis_id, k.team, k.game_id, k.week, k.fg_att, k.pat_att,
                   {buckets}, round({kp}, {FLOAT_DECIMALS}) AS points, k.available_at
            FROM fact_kicker_week k WHERE k.season = {s} AND k.season_type = 'REG'""",
        "defense": f"""
            SELECT d.team, d.opponent_team, d.game_id, d.week, d.def_sacks,
                   COALESCE(d.def_interceptions, 0) + COALESCE(d.fumble_recovery_opp, 0)
                       AS takeaways,
                   {" + ".join(f"d.{c}" for c in tds)} AS tds, d.points_allowed,
                   round({dp}, {FLOAT_DECIMALS}) AS points, d.available_at
            FROM fact_defense_week d WHERE d.season = {s} AND d.season_type = 'REG'""",
        "team_games": f"""
            SELECT game_id, week, home_team AS team, away_team AS opponent,
                   home_score AS points_for, away_score AS points_against, available_at
            FROM fact_game WHERE season = {s} AND season_type = 'REG' AND result IS NOT NULL
            UNION ALL
            SELECT game_id, week, away_team, home_team, away_score, home_score, available_at
            FROM fact_game WHERE season = {s} AND season_type = 'REG' AND result IS NOT NULL""",
        "red_zone": f"""
            WITH drives AS (
                SELECT game_id, posteam AS team, defteam AS opponent, fixed_drive,
                       bool_or({rz}) AS trip,
                       bool_or(fixed_drive_result = 'Touchdown') AS td,
                       max(available_at) AS available_at
                FROM fact_play
                WHERE season = {s} AND season_type = 'REG' AND posteam IS NOT NULL
                  AND defteam IS NOT NULL
                GROUP BY game_id, posteam, defteam, fixed_drive
            )
            SELECT game_id, team, opponent,
                   count(*) FILTER (WHERE trip) AS rz_trips,
                   count(*) FILTER (WHERE trip AND td) AS rz_tds,
                   max(available_at) AS available_at
            FROM drives GROUP BY game_id, team, opponent""",
        "schedule": f"""
            SELECT game_id, week, home_team, away_team, location, stadium, stadium_id,
                   slot_available_at, available_at
            FROM fact_schedule WHERE season = {s} AND season_type = 'REG'""",
        # every earlier game with a roof (all seasons): the venue's kind from its history
        "venues": f"""
            SELECT stadium_id, roof, available_at FROM fact_game
            WHERE season <= {s} AND stadium_id IS NOT NULL AND roof IS NOT NULL""",
        "weekly_ranks": f"""
            SELECT page_pos AS position, gsis_id, nfl_team, pos_rank, scrape_date, available_at
            FROM fact_ranking_kdst WHERE season = {s} AND page_kind = 'weekly'""",
    }


# Sort keys: inputs are sorted after loading so every later step sees the same row order.
_ORDER = {
    "kicks": ("gsis_id", "week", "game_id"),
    "defense": ("team", "week", "game_id"),
    "team_games": ("team", "week", "game_id"),
    "red_zone": ("team", "game_id", "opponent"),
    "schedule": ("week", "game_id"),
    "venues": ("stadium_id", "roof", "available_at"),
    "weekly_ranks": ("position", "scrape_date", "pos_rank", "gsis_id", "nfl_team"),
}


@dataclass(frozen=True)
class SeasonInputs:
    """Every input frame of one season. ``batch``: read from the warehouse with future rows
    (filter with :meth:`visible`); otherwise read through an as-of view."""

    season: int
    frames: dict[str, pl.DataFrame]
    batch: bool

    def visible(self, as_of: datetime) -> SeasonInputs:
        """The inputs as they were public at ``as_of`` (``available_at <= as_of``), with the
        schedule's venue columns NULL until ``slot_available_at`` (as the view shows them;
        through a view this changes nothing)."""
        frames = {n: asof_filter(df, as_of) for n, df in self.frames.items()}
        sched = frames["schedule"]
        slot_public = asof_filter(sched, as_of, column="slot_available_at").select("game_id")
        public = pl.col("game_id").is_in(slot_public.get_column("game_id").implode())
        frames["schedule"] = sched.with_columns(
            pl.when(public).then(pl.col(c)).alias(c) for c in SCHEDULE_MASKED
        )
        return replace(self, frames=frames, batch=False)


def _require_tables(source: SqlSource) -> None:
    """A warehouse built before S1 has no fact_kicker_week: say so plainly."""
    from twm.asof import WarehouseTooOldError

    need = ("fact_kicker_week", "fact_defense_week", "fact_ranking_kdst")
    have = getattr(source, "tables", None)
    if have is None:  # the warehouse itself
        found = set(source.sql("SELECT table_name FROM duckdb_tables()").get_column("table_name"))
    else:
        found = set(have)
    missing = [t for t in need if t not in found]
    if missing:
        raise WarehouseTooOldError(
            f"the warehouse has no {', '.join(missing)} (added in step S1): rebuild it with "
            "`uv run twm build --start 2012`"
        )


def load_inputs(
    source: SqlSource, season: int, rules: StreamerRules, *, batch: bool = False
) -> SeasonInputs:
    """Run :func:`input_sql` on ``source`` (an AsOfView, or the warehouse with ``batch``)."""
    _require_tables(source)
    frames = {
        name: source.sql(query).sort(list(_ORDER[name]), nulls_last=True)
        for name, query in input_sql(season, rules).items()
    }
    return SeasonInputs(int(season), frames, batch)


# --------------------------------------------------------------------------------------
# One as-of
# --------------------------------------------------------------------------------------


def _check_pool_rows(pool_rows: pl.DataFrame, season: int, week: int) -> None:
    missing = [c for c in REQUIRED_POOL_COLUMNS if c not in pool_rows.columns]
    if missing:
        raise KeyError(f"pool rows lack columns {missing}")
    if pool_rows.height:
        seasons = set(pool_rows.get_column("season").unique().to_list())
        weeks = set(pool_rows.get_column("week").unique().to_list())
        if seasons != {season} or weeks != {week}:
            raise ValueError(
                f"pool rows are for seasons {sorted(seasons)} weeks {sorted(weeks)}, "
                f"not {season} week {week}"
            )
        if pool_rows.select("position", "entity_id").is_duplicated().any():
            raise ValueError("pool rows repeat an entity (pass one method's pool)")


def _ratio(num: pl.Expr, den: pl.Expr) -> pl.Expr:
    """num / den, NULL when den is 0 or NULL."""
    return pl.when(den > 0).then(num.cast(pl.Float64) / den)


def _kicker_stats(kicks: pl.DataFrame) -> pl.DataFrame:
    """Per kicker (gsis_id): attempts per game, long attempts, accuracy by distance, and the
    points of his latest game (every kicked game of the season, any team)."""
    c = pl.col
    made = {b: c(f"fg_made_{b}").sum() for b in FG_BUCKETS}
    tried = {b: (c(f"fg_made_{b}") + c(f"fg_missed_{b}")).sum() for b in FG_BUCKETS}
    per = kicks.group_by("gsis_id").agg(
        _ratio(c("fg_att").fill_null(0).sum(), pl.len()).alias("k_fg_att_per_game"),
        _ratio(c("pat_att").fill_null(0).sum(), pl.len()).alias("k_pat_att_per_game"),
        _ratio(tried["40_49"] + tried["50_plus"], pl.len()).alias("k_fg_att_40_plus_per_game"),
        *[_ratio(made[b], tried[b]).alias(f"k_fg_pct_{b}") for b in FG_BUCKETS],
        c("points").sort_by("week", "game_id").last().alias("_k_last"),
    )
    return per


def _defense_stats(defense: pl.DataFrame) -> pl.DataFrame:
    """Per team: its D/ST per game (sacks, takeaways, TDs, ESPN points allowed), the points of
    its latest game; and per offense faced (``opponent_team``): sacks allowed and giveaways."""
    c = pl.col
    own = defense.group_by("team").agg(
        c("def_sacks").mean().alias("dst_sacks_per_game"),
        c("takeaways").mean().alias("dst_takeaways_per_game"),
        c("tds").mean().alias("dst_tds_per_game"),
        c("points_allowed").mean().alias("dst_points_allowed_per_game"),
        c("points").sort_by("week", "game_id").last().alias("_dst_last"),
    )
    faced = defense.group_by(c("opponent_team").alias("team")).agg(
        c("def_sacks").mean().alias("next_opp_sacks_allowed_per_game"),
        c("takeaways").mean().alias("next_opp_giveaways_per_game"),
    )
    return own.join(faced, on="team", how="full", coalesce=True)


def _team_stats(team_games: pl.DataFrame, red_zone: pl.DataFrame) -> pl.DataFrame:
    """Per team: its offense (points and red-zone trips / stalls per game) and, as a next
    opponent, what its defense allowed (points, red-zone trips, the share of trips it held
    to no touchdown) and what its offense scored."""
    c = pl.col
    games = team_games.group_by("team").agg(
        pl.len().cast(pl.Int32).alias("team_games_to_date"),
        c("points_for").mean().alias("team_points_per_game"),
        c("points_against").mean().alias("next_opp_points_allowed_per_game"),
    )
    stalls = c("rz_trips").sum() - c("rz_tds").sum()
    offense = red_zone.group_by("team").agg(
        _ratio(c("rz_trips").sum(), pl.len()).alias("team_rz_trips_per_game"),
        _ratio(stalls, pl.len()).alias("team_rz_stalls_per_game"),
        _ratio(stalls, c("rz_trips").sum()).alias("team_rz_stall_rate"),
    )
    defense = red_zone.group_by(c("opponent").alias("team")).agg(
        _ratio(c("rz_trips").sum(), pl.len()).alias("next_opp_rz_trips_allowed_per_game"),
        _ratio(stalls, c("rz_trips").sum()).alias("next_opp_rz_stall_rate_forced"),
    )
    return (
        games.join(offense, on="team", how="full", coalesce=True)
        .join(defense, on="team", how="full", coalesce=True)
        .with_columns(
            c("team_points_per_game").alias("next_opp_points_per_game"),
            c("team_games_to_date").alias("next_opp_games_to_date"),
        )
    )


def _venue_kinds(venues: pl.DataFrame) -> pl.DataFrame:
    """Per stadium_id: 'retractable' (any earlier game with the roof open or closed), else
    'dome', else 'outdoors'."""
    c = pl.col
    return venues.group_by("stadium_id").agg(
        pl.when(c("roof").is_in(RETRACTABLE_ROOFS).any())
        .then(pl.lit("retractable"))
        .when(c("roof").is_in(DOME_ROOFS).any())
        .then(pl.lit("dome"))
        .otherwise(pl.lit("outdoors"))
        .alias("_venue")
    )


def _next_games(schedule: pl.DataFrame, venues: pl.DataFrame, week: int) -> pl.DataFrame:
    """Per team with a week ``week + 1`` game: the opponent, home or away (NULL while the
    venue is masked; a neutral site is not home) and the venue's roof kind."""
    c = pl.col
    nxt = schedule.filter(c("week") == week + 1)
    sides = [
        nxt.select(c(me).alias("team"), c(other).alias("_opp"), pl.lit(me == "home_team")
                   .alias("_listed_home"), "location", "stadium", "stadium_id")
        for me, other in (("home_team", "away_team"), ("away_team", "home_team"))
    ]  # fmt: skip
    kinds = _venue_kinds(venues)
    return (
        pl.concat(sides, how="vertical")
        .join(kinds, on="stadium_id", how="left")
        .select(
            "team",
            "_opp",
            pl.when(c("location").is_null())
            .then(None)
            .otherwise((c("location") != "Neutral") & c("_listed_home"))
            .alias("next_is_home"),
            pl.when(c("stadium").is_not_null()).then(c("_venue")).alias("_venue"),
        )
        .with_columns(
            (c("_venue") == "dome").alias("next_venue_dome"),
            (c("_venue") == "retractable").alias("next_venue_retractable"),
        )
    )


def _weekly_ranks(ranks: pl.DataFrame) -> tuple[pl.DataFrame, set[str]]:
    """The latest visible weekly page per position: (rank per (position, key), positions that
    have a page). K rows are keyed by gsis_id, DST rows by team."""
    c = pl.col
    latest = ranks.filter(c("scrape_date") == c("scrape_date").max().over("position"))
    keyed = latest.select(
        "position",
        pl.when(c("position") == "K").then(c("gsis_id")).otherwise(c("nfl_team")).alias("_key"),
        "pos_rank",
    ).filter(c("_key").is_not_null())
    best = keyed.group_by("position", "_key").agg(c("pos_rank").min().alias("weekly_ecr_rank"))
    return best, set(latest.get_column("position").unique().to_list())


def compute_features(
    inp: SeasonInputs,
    season: int,
    week: int,
    pool_rows: pl.DataFrame,
) -> pl.DataFrame:
    """The features of ``pool_rows`` from inputs already filtered to the as-of
    (:meth:`SeasonInputs.visible`). Returns KEY_COLUMNS + FEATURE_COLUMNS in the pool rows'
    order."""
    _check_pool_rows(pool_rows, season, week)
    if inp.batch:
        raise ValueError("inputs still hold future rows: call .visible(as_of) first")
    c = pl.col
    f = inp.frames
    rows = pool_rows.select(REQUIRED_POOL_COLUMNS).with_row_index("_row")
    kick = _kicker_stats(f["kicks"])
    dst = _defense_stats(f["defense"])
    team = _team_stats(f["team_games"], f["red_zone"])
    nxt = _next_games(f["schedule"], f["venues"], week).unique(
        "team", keep="first", maintain_order=True
    )
    ranks, paged = _weekly_ranks(f["weekly_ranks"])
    own = [x for x in dst.columns if x.startswith("dst_") or x == "_dst_last"]
    opp_cols = [x for x in OPPONENT_FEATURES if x in team.columns or x in dst.columns]
    opp = team.join(dst.drop(own), on="team", how="full", coalesce=True).select(
        c("team").alias("_opp"), *opp_cols
    )
    is_k, is_dst = c("position") == "K", c("position") == "DST"
    out = (
        rows.with_columns(
            pl.when(is_k).then(c("gsis_id")).otherwise(c("team")).alias("_key"),
            pl.when(is_dst).then(c("team")).alias("_dst_team"),
        )
        .join(kick, on="gsis_id", how="left")
        .join(dst.select("team", *own).rename({"team": "_dst_team"}), on="_dst_team", how="left")
        .join(team.select("team", *TEAM_FEATURES), on="team", how="left")
        .join(nxt, on="team", how="left")
        .join(opp, on="_opp", how="left")
        .join(ranks, on=["position", "_key"], how="left")
        .sort("_row")
    )
    out = out.with_columns(
        c("ppg_to_date").alias("kdst_points_per_game"),
        c("ppg_pos_rank").alias("kdst_ppg_rank"),
        c("preseason_pos_rank").alias("kdst_preseason_rank"),
        c("games_to_date").alias("kdst_games_to_date"),
        pl.when(is_k).then(c("_k_last")).otherwise(c("_dst_last")).alias("kdst_points_last"),
        pl.when(c("position").is_in(sorted(paged)))
        .then(c("weekly_ecr_rank").is_not_null())
        .alias("weekly_ecr_listed"),
    )
    cols = []
    for name in FEATURE_COLUMNS:
        e = c(name).cast(FEATURE_SCHEMA[name])
        if FEATURE_SCHEMA[name] == pl.Float64():
            e = e.round(FLOAT_DECIMALS)
        cols.append(e.alias(name))
    return out.select(
        c("season").cast(pl.Int32), c("week").cast(pl.Int32), "position", "entity_id", *cols
    )


def empty_features() -> pl.DataFrame:
    """A typed frame with the output columns of :func:`compute_features` and no rows."""
    return pl.DataFrame(
        schema={
            "season": pl.Int32,
            "week": pl.Int32,
            "position": pl.String,
            "entity_id": pl.String,
            **FEATURE_SCHEMA,
        }  # fmt: skip
    ).select(*KEY_COLUMNS, *FEATURE_COLUMNS)


def features_for(
    view: SqlSource,
    season: int,
    week: int,
    pool_rows: pl.DataFrame,
    *,
    rules: StreamerRules | None = None,
) -> pl.DataFrame:
    """The reference implementation: the features of ``pool_rows`` (S1b pool rows of
    ``season``/``week``) at the view's as-of, every input read through the view.

    ``view`` is an :class:`~twm.asof.AsOfView` (anything with ``sql`` and ``as_of``).
    Returns KEY_COLUMNS + FEATURE_COLUMNS, one row per pool row, in order."""
    rules = rules or StreamerRules.from_config()
    as_of = view.as_of  # type: ignore[attr-defined]
    inp = load_inputs(view, season, rules, batch=False)
    return compute_features(inp.visible(as_of), season, week, pool_rows)


def features_history(
    db: Path | str,
    pool: pl.DataFrame,
    *,
    rules: StreamerRules | None = None,
) -> pl.DataFrame:
    """The batch path: the features of every row of ``pool`` (S1b pool history, one method
    per as-of), each season's inputs read once and filtered per as-of (``as_of`` column).
    Equals :func:`features_for` row by row (tested)."""
    from twm.warehouse.build import connect

    rules = rules or StreamerRules.from_config()
    missing = [c for c in (*REQUIRED_POOL_COLUMNS, "as_of") if c not in pool.columns]
    if missing:
        raise KeyError(f"pool rows lack columns {missing}")
    if pool.height == 0:
        return empty_features()
    frames = []
    con = connect(db, read_only=True)
    try:
        con.execute("SET TimeZone='UTC'")
        source = _Warehouse(con)
        for season in sorted(pool.get_column("season").unique().to_list()):
            inputs = load_inputs(source, int(season), rules, batch=True)
            rows = pool.filter(pl.col("season") == season)
            for (week, as_of), grp in sorted(
                rows.partition_by(["week", "as_of"], as_dict=True, maintain_order=True).items()
            ):
                frames.append(compute_features(inputs.visible(as_of), int(season), int(week), grp))
    finally:
        con.close()
    return pl.concat(frames, how="vertical")


def features_as_of_week(
    db: Path | str,
    season: int,
    week: int,
    *,
    rules: StreamerRules | None = None,
    positions: Sequence[str] | None = None,
) -> tuple[datetime, pl.DataFrame]:
    """(as_of, pool rows joined with their features) at the official Tuesday as-of of
    (season, week): for the CLI. ``positions``: default the league's (K and DST)."""
    from twm.asof import AsOfView, weekly_as_of
    from twm.modules.streamer.pool import candidate_pool

    rules = rules or StreamerRules.from_config()
    when = weekly_as_of(db, season, week)
    with AsOfView(db, when) as view:
        pool = candidate_pool(view, season, week, rules=rules, positions=positions)
        feats = features_for(view, season, week, pool, rules=rules)
    extra = [c for c in FEATURE_COLUMNS if c not in POOL_FEATURES]
    return when, pool.join(
        feats.select(*KEY_COLUMNS, *extra), on=list(KEY_COLUMNS), how="left", maintain_order="left"
    )


def uses_bypass(queries: dict[str, str]) -> bool:
    """True when any input SQL reaches the raw warehouse (``wh.``): never in this module."""
    return any(re.search(r"\bwh\.", q, re.IGNORECASE) for q in queries.values())


__all__ = [
    "FEATURE_COLUMNS", "FEATURE_FAMILIES", "KEY_COLUMNS", "POOL_FEATURES", "SeasonInputs",
    "compute_features", "empty_features", "features_as_of_week", "features_for",
    "features_history", "input_sql", "load_inputs", "uses_bypass",
]  # fmt: skip
