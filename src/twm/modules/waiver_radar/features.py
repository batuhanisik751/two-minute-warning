"""Waiver Radar features (C3): what a pool player's situation looked like at the Tuesday as-of.

One row per candidate-pool row (C1: every rostered QB/RB/WR/TE at the as-of after week N of
season S), with the families of PROJECT_SPEC 8.1 (docs/waiver_radar.md "Features" explains
each one for a beginner):

- **Opportunity**: snap, target, air-yards, carry shares; red-zone and goal-line looks; a routes
  proxy; expected fantasy points (xFP) and points over expected (FPOE); fantasy points; the C1
  columns (PPG to date and its rank, the preseason rank, games played).
- **Role change**: the depth chart (rank now, a week earlier, the change); teammates who became
  unavailable (injured reserve, released or traded, ruled out, or missing the last game after
  playing most snaps) and the opportunity they vacated; a recent team change.
- **Context**: the team's offense (EPA per play, pace, neutral pass rate), the next opponents'
  fantasy points allowed to the position (no betting lines: spec 6.4), byes, games remaining.
- **Player**: position, age, rookie, draft round, years of experience.

Definitions shared by the windowed features: a **team game** is a regular-season game of the
player's as-of team (the pool row's ``team``) of season S that is visible at the as-of. "last"
is the team's most recent visible game, "avg3" the mean over its last 3 (fewer early in the
season), "season" the mean over all of them. A game the player missed counts as 0 (a share of
0, no targets ...). Everything is NULL only when the team has no visible game yet.

Point in time. Two paths compute the same numbers:

- :func:`features_for` (the reference): every input is read through an
  :class:`~twm.asof.AsOfView` at the as-of, so only rows public by then exist;
  tests/test_waiver_radar_features.py runs the leakage harness on it and proves that leaky
  variants fail.
- :func:`features_history` (the batch path for backtests): each season's inputs are read once
  from the warehouse WITH their ``available_at`` and filtered with :func:`twm.asof.asof_filter`
  for every as-of. Every SQL input is either a table's own rows or a per-game aggregate of ONE
  table (all rows of a game share its ``available_at``), and every join across tables happens
  after filtering, so filtering the aggregates equals aggregating the filtered rows. Tests (and
  the realdata tests on real as-ofs) check the two paths give the same frame.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import duckdb
import polars as pl

from twm.asof import asof_filter, to_utc
from twm.config import FANTASY_POSITIONS, league
from twm.modules.waiver_radar.pool import statuses_are_weekly
from twm.scoring import ScoringRules, score_sql, xfp_sql

# --------------------------------------------------------------------------------------
# Columns
# --------------------------------------------------------------------------------------

# Model features, by family (docs/waiver_radar.md; every one is registered in twm.registry).
OPPORTUNITY_FEATURES = (
    "snap_share_last", "snap_share_avg3", "snap_share_season", "snap_share_delta",
    "target_share_last", "target_share_avg3", "air_yards_share_avg3", "wopr_avg3",
    "carry_share_last", "carry_share_avg3", "rz_targets_avg3", "rz_carries_avg3",
    "gl_opps_avg3", "routes_proxy_avg3", "xfp_last", "xfp_avg3", "fpoe_avg3",
    "fantasy_points_last", "fantasy_points_avg3", "ppg_to_date", "ppg_pos_rank",
    "preseason_pos_rank", "preseason_ranked", "games_played_to_date",
)  # fmt: skip
ROLE_FEATURES = (
    "depth_rank_now", "depth_rank_prev", "depth_rank_change", "depth_listed",
    "vacated_target_share", "vacated_carry_share", "same_pos_vacated_target_share",
    "same_pos_vacated_carry_share", "teammate_same_pos_unavailable", "top_teammate_out",
    "joined_team_recently",
)  # fmt: skip
CONTEXT_FEATURES = (
    "team_epa_per_play", "team_epa_per_play_neutral", "team_plays_per_game",
    "team_pass_rate_neutral", "opp_fp_allowed_next3", "n_opp_games_seen", "bye_in_next3",
    "team_games_remaining",
)  # fmt: skip
PLAYER_FEATURES = (
    "position", "age_at_asof", "is_rookie", "draft_round", "is_undrafted", "years_exp",
)  # fmt: skip
FEATURE_FAMILIES: dict[str, tuple[str, ...]] = {
    "opportunity": OPPORTUNITY_FEATURES,
    "role change": ROLE_FEATURES,
    "context": CONTEXT_FEATURES,
    "player": PLAYER_FEATURES,
}
FEATURE_COLUMNS: tuple[str, ...] = tuple(c for f in FEATURE_FAMILIES.values() for c in f)
CATEGORICAL_FEATURES = ("position",)
# Not model features: which teammates were unavailable and why (JSON text, for C6 reasons and
# for eyeballing), one entry per teammate.
INFO_COLUMNS = ("teammates_out",)
KEY_COLUMNS = ("season", "week", "gsis_id")
# What features_for needs from the pool rows (C1 POOL_COLUMNS has them all).
REQUIRED_POOL_COLUMNS = (
    "season", "week", "gsis_id", "team", "position", "ppg_to_date", "ppg_pos_rank",
    "preseason_pos_rank", "games_to_date",
)  # fmt: skip
# Pool columns that are also features under the same name (the dataset keeps one copy).
POOL_FEATURES = ("position", "ppg_to_date", "ppg_pos_rank", "preseason_pos_rank")

FEATURE_SCHEMA: dict[str, pl.DataType] = {
    **dict.fromkeys(FEATURE_COLUMNS, pl.Float64()),
    "ppg_pos_rank": pl.Int32(),
    "preseason_pos_rank": pl.Int32(),
    "preseason_ranked": pl.Boolean(),
    "games_played_to_date": pl.Int32(),
    "depth_rank_now": pl.Int32(),
    "depth_rank_prev": pl.Int32(),
    "depth_rank_change": pl.Int32(),
    "depth_listed": pl.Boolean(),
    "teammate_same_pos_unavailable": pl.Int32(),
    "top_teammate_out": pl.Boolean(),
    "joined_team_recently": pl.Boolean(),
    "n_opp_games_seen": pl.Int32(),
    "bye_in_next3": pl.Boolean(),
    "team_games_remaining": pl.Int32(),
    "position": pl.String(),
    "is_rookie": pl.Boolean(),
    "draft_round": pl.Int32(),
    "is_undrafted": pl.Boolean(),
    "years_exp": pl.Int32(),
}
FLOAT_DECIMALS = 6  # floats are rounded so the two paths and two runs agree to the bit

# Teammate unavailability rules, in the order the first one that fires is reported.
UNAVAILABILITY_RULES = ("roster_status", "left_team", "injury_report", "missed_last_game")

# Depth-chart slot names -> position group. Legacy weekly charts (2001-2024) spell slots many
# ways (LWR, RWR, WR1, 'WR\8', HB, FB, H-B, 'TE/HB' ...); daily charts (2025+) use QB, RB, FB,
# WR and TE. A slot is split into its letter tokens: any TE token makes it a TE slot (the
# combined slots 'TE/HB', 'HB-TE', 'FB/TE' hold tight ends on the rosters), else the first
# token decides. 'H' (H-B, an H-back) and 'F' hold mostly tight ends on the weekly rosters, 'J'
# running backs; slots without a known token (offensive line, '\n', '19' ...) are not mapped.
# The same map turns snap-count and roster positions into groups (FB -> RB).
SLOT_GROUPS: dict[str, str] = {
    "QB": "QB",
    "RB": "RB", "HB": "RB", "FB": "RB", "J": "RB",
    "WR": "WR", "LWR": "WR", "RWR": "WR", "SWR": "WR", "WRE": "WR", "WE": "WR",
    "TE": "TE", "LTE": "TE", "RTE": "TE", "H": "TE", "F": "TE",
}  # fmt: skip
TE_TOKENS = ("TE", "LTE", "RTE")
_TOKEN = re.compile(r"[A-Z]+")


def slot_group(slot: str | None) -> str | None:
    """The position group (QB/RB/WR/TE) of a depth-chart slot or roster position, or None."""
    if slot is None:
        return None
    tokens = _TOKEN.findall(slot.upper())
    if not tokens:
        return None
    if any(t in TE_TOKENS for t in tokens):
        return "TE"
    return SLOT_GROUPS.get(tokens[0])


# --------------------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FeatureRules:
    """The features' knobs as plain values.

    window: games in an "avg3" window (PROJECT_SPEC 8.1: "last 3 weeks").
    missed_game_min_snap_share: a teammate who missed the team's last game counts as
    unavailable only if he averaged at least this snap share in the games before it.
    injury_statuses: injury-report statuses that make a teammate unavailable.
    available_statuses: weekly-roster statuses that mean "on the team and able to play"
    (config/league.yaml pool.roster_statuses); any other status (RES, PUP, CUT ...) does not.
    red_zone_yardline / goal_line_yardline: yards from the opponent's end zone.
    depth_prev_lag: the "previous" depth chart is the one in force this long before the as-of.
    cancelled_games: game ids of scheduled games that were cancelled later and are absent from
    nflverse (config availability.schedule_exceptions): counted as scheduled (see docs).
    """

    window: int = 3
    missed_game_min_snap_share: float = 0.5
    injury_statuses: tuple[str, ...] = ("Out", "Doubtful")
    available_statuses: tuple[str, ...] = ("ACT", "INA", "DEV")
    red_zone_yardline: int = 20
    goal_line_yardline: int = 10
    depth_prev_lag: timedelta = timedelta(days=7)
    cancelled_games: tuple[str, ...] = ()
    scoring: ScoringRules = field(default_factory=ScoringRules.from_config)

    def __post_init__(self) -> None:
        if self.window < 1:
            raise ValueError("window must be at least 1")
        if not 0 <= self.missed_game_min_snap_share <= 1:
            raise ValueError("missed_game_min_snap_share must be a share (0-1)")
        if not 0 < self.goal_line_yardline <= self.red_zone_yardline <= 100:
            raise ValueError("need 0 < goal_line_yardline <= red_zone_yardline <= 100")

    @classmethod
    def from_config(cls, scoring: ScoringRules | None = None) -> FeatureRules:
        from twm.warehouse.available import AvailabilityRules

        cancelled = tuple(x.game_id for x in AvailabilityRules.from_settings().cancelled_games())
        return cls(
            available_statuses=tuple(league().pool.roster_statuses),
            cancelled_games=cancelled,
            scoring=scoring or ScoringRules.from_config(),
        )


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


def _not_2pt(alias: str = "") -> str:
    return f"COALESCE({alias}two_point_attempt, 0) = 0"


def input_sql(season: int, rules: FeatureRules, *, batch: bool) -> dict[str, str]:
    """The SQL of every input of ``season``, by name. The same text runs through an AsOfView
    (tables are their point-in-time views) and on the warehouse (``batch``: every row). Each
    query reads ONE table; per-game aggregates group rows of one game, which share one
    ``available_at`` (kept as the group's max)."""
    s = int(season)
    rz, gl = int(rules.red_zone_yardline), int(rules.goal_line_yardline)
    run_pass = f"play_type IN ('pass', 'run') AND {_not_2pt()}"
    neutral = f"{run_pass} AND is_neutral"
    # epa summed as DECIMAL: exact, so the per-game sum does not depend on the order DuckDB
    # adds the plays in (a DOUBLE sum can differ in the last bits between two runs)
    epa = "CAST(epa AS DECIMAL(18, 9))"
    return {
        "team_games": f"""
            SELECT game_id, week, home_team AS team, available_at FROM fact_game
            WHERE season = {s} AND season_type = 'REG' AND result IS NOT NULL
            UNION ALL
            SELECT game_id, week, away_team AS team, available_at FROM fact_game
            WHERE season = {s} AND season_type = 'REG' AND result IS NOT NULL""",
        "snaps": f"""
            SELECT game_id, team, gsis_id, position, offense_snaps, offense_pct, available_at
            FROM fact_snaps
            WHERE season = {s} AND game_type = 'REG' AND gsis_id IS NOT NULL""",
        "player_games": f"""
            SELECT game_id, team, opponent_team, player_id AS gsis_id, targets, target_share,
                   air_yards_share, wopr, carries,
                   round({score_sql(rules.scoring)}, {FLOAT_DECIMALS}) AS fantasy_points,
                   available_at
            FROM fact_player_week WHERE season = {s} AND season_type = 'REG'""",
        "team_carries": f"""
            SELECT game_id, team, carries AS team_carries, available_at FROM fact_team_week
            WHERE season = {s} AND season_type = 'REG'""",
        "red_zone": f"""
            WITH looks AS (
                SELECT game_id, posteam AS team, receiver_player_id AS gsis_id,
                       1 AS rz_target, 0 AS rz_carry,
                       CASE WHEN yardline_100 <= {gl} THEN 1 ELSE 0 END AS gl_opp, available_at
                FROM fact_play
                WHERE season = {s} AND season_type = 'REG' AND play_type = 'pass'
                  AND {_not_2pt()} AND receiver_player_id IS NOT NULL AND yardline_100 <= {rz}
                UNION ALL
                SELECT game_id, posteam, rusher_player_id, 0, 1,
                       CASE WHEN yardline_100 <= {gl} THEN 1 ELSE 0 END, available_at
                FROM fact_play
                WHERE season = {s} AND season_type = 'REG' AND play_type = 'run'
                  AND {_not_2pt()} AND rusher_player_id IS NOT NULL AND yardline_100 <= {rz}
            )
            SELECT game_id, team, gsis_id, sum(rz_target) AS rz_targets,
                   sum(rz_carry) AS rz_carries, sum(gl_opp) AS gl_opps,
                   max(available_at) AS available_at
            FROM looks GROUP BY game_id, team, gsis_id""",
        "team_plays": f"""
            SELECT game_id, posteam AS team,
                   count(*) FILTER (WHERE qb_dropback = 1 AND {_not_2pt()}) AS dropbacks,
                   count(*) FILTER (WHERE {run_pass}) AS n_plays,
                   count(epa) FILTER (WHERE {run_pass}) AS n_epa,
                   CAST(sum({epa}) FILTER (WHERE {run_pass}) AS DOUBLE) AS epa_sum,
                   count(*) FILTER (WHERE {neutral}) AS n_neutral,
                   count(epa) FILTER (WHERE {neutral}) AS n_epa_neutral,
                   CAST(sum({epa}) FILTER (WHERE {neutral}) AS DOUBLE) AS epa_sum_neutral,
                   count(*) FILTER (WHERE {neutral} AND "pass" = 1) AS n_pass_neutral,
                   max(available_at) AS available_at
            FROM fact_play
            WHERE season = {s} AND season_type = 'REG' AND posteam IS NOT NULL
            GROUP BY game_id, posteam""",
        "expected": f"""
            SELECT game_id, posteam AS team, player_id AS gsis_id,
                   round({xfp_sql(rules.scoring)}, {FLOAT_DECIMALS}) AS xfp, available_at
            FROM fact_opportunity_week WHERE season = {s} AND season_type = 'REG'""",
        "rosters": f"""
            SELECT week, team, gsis_id, position, status, full_name, entry_year, years_exp,
                   available_at
            FROM fact_roster_week WHERE season = {s} AND gsis_id IS NOT NULL""",
        "injuries": f"""
            SELECT week, team, gsis_id, report_status, available_at FROM fact_injury_report
            WHERE season = {s} AND season_type = 'REG' AND gsis_id IS NOT NULL""",
        "depth": f"""
            SELECT team, COALESCE(dt, available_at) AS chart_time, gsis_id, position AS slot,
                   unit, depth_rank, available_at
            FROM fact_depth_chart
            WHERE season = {s} AND gsis_id IS NOT NULL
              AND (source_format = 'daily' OR season_type = 'REG')""",
        "schedule": f"""
            SELECT game_id, week, home_team, away_team, available_at FROM fact_schedule
            WHERE season = {s} AND season_type = 'REG'""",
        # hindsight table: the view shows only players who exist by the as-of; the batch path
        # reads the row rule's time and filters on it per as-of
        "players": (
            "SELECT gsis_id, birth_date, draft_round, public_from_utc FROM dim_player "
            "WHERE public_from_utc IS NOT NULL"
            if batch
            else "SELECT gsis_id, birth_date, draft_round FROM dim_player"
        ),
        # static: the calendar
        "calendar": f"""
            SELECT max(week) FILTER (WHERE is_last_reg_week) AS last_reg_week FROM dim_week
            WHERE season = {s} AND season_type = 'REG'""",
    }


EVENT_INPUTS = (
    "team_games", "snaps", "player_games", "team_carries", "red_zone", "team_plays", "expected",
    "rosters", "injuries", "depth", "schedule",
)  # fmt: skip
# Sort keys: inputs are sorted after loading so every later step sees the same row order.
_ORDER = {
    "team_games": ("team", "week", "game_id"),
    "snaps": ("game_id", "team", "gsis_id"),
    "player_games": ("game_id", "team", "gsis_id"),
    "team_carries": ("game_id", "team"),
    "red_zone": ("game_id", "team", "gsis_id"),
    "team_plays": ("game_id", "team"),
    "expected": ("game_id", "team", "gsis_id"),
    "rosters": ("gsis_id", "week", "team"),
    "injuries": ("team", "week", "gsis_id"),
    "depth": ("team", "chart_time", "gsis_id", "slot", "unit", "depth_rank"),
    "schedule": ("week", "game_id"),
    "players": ("gsis_id",),
}


@dataclass(frozen=True)
class SeasonInputs:
    """Every input frame of one season. ``batch``: read from the warehouse with future rows
    (filter with :meth:`visible`); otherwise read through an as-of view."""

    season: int
    frames: dict[str, pl.DataFrame]
    last_reg_week: int | None
    batch: bool

    def visible(self, as_of: datetime) -> SeasonInputs:
        """The inputs as they were public at ``as_of`` (``available_at <= as_of``; players by
        their row rule). Through a view this changes nothing (the view already filtered)."""
        frames = {n: asof_filter(self.frames[n], as_of) for n in EVENT_INPUTS}
        players = self.frames["players"]
        if self.batch:
            players = asof_filter(players, as_of, column="public_from_utc").drop("public_from_utc")
        frames["players"] = players
        return replace(self, frames=frames, batch=False)


def _require_tables(source: SqlSource) -> None:
    """A warehouse built before C3 has no fact_opportunity_week: say so plainly."""
    from twm.asof import WarehouseTooOldError

    have = getattr(source, "tables", None)
    if have is None:  # the warehouse itself
        found = source.sql(
            "SELECT count(*) AS n FROM duckdb_tables() WHERE table_name = 'fact_opportunity_week'"
        ).item()
        ok = bool(found)
    else:
        ok = "fact_opportunity_week" in have
    if not ok:
        raise WarehouseTooOldError(
            "the warehouse has no fact_opportunity_week (added in step C3): rebuild it with "
            "`uv run twm build --start 1999` (after `twm ingest ff_opportunity` if needed)"
        )


def load_inputs(
    source: SqlSource, season: int, rules: FeatureRules, *, batch: bool = False
) -> SeasonInputs:
    """Run :func:`input_sql` on ``source`` (an AsOfView, or the warehouse with ``batch``)."""
    queries = input_sql(season, rules, batch=batch)
    _require_tables(source)
    frames: dict[str, pl.DataFrame] = {}
    last = None
    for name, query in queries.items():
        df = source.sql(query)
        if name == "calendar":
            value = df.item(0, 0) if df.height else None
            last = None if value is None else int(value)
            continue
        frames[name] = df.sort(list(_ORDER[name]), nulls_last=True)
    return SeasonInputs(int(season), frames, last, batch)


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
        if pool_rows.select("gsis_id").is_duplicated().any():
            raise ValueError("pool rows repeat a gsis_id (pass one method's pool)")


def _map_groups(df: pl.DataFrame, col: str, out: str) -> pl.DataFrame:
    """Add ``out``: slot_group of ``col``, computed once per distinct value (fast)."""
    values = df.get_column(col).drop_nulls().unique().to_list()
    mapping = {v: slot_group(v) for v in values}
    return df.with_columns(
        pl.col(col).replace_strict(mapping, default=None, return_dtype=pl.String).alias(out)
    )


def _team_game_index(team_games: pl.DataFrame) -> pl.DataFrame:
    """(team, game_id, week, k): k = 1 for the team's most recent visible game, 2 before ..."""
    return (
        team_games.select("team", "game_id", "week")
        .sort(["team", "week", "game_id"], descending=[False, True, True])
        .with_columns(pl.int_range(1, pl.len() + 1).over("team").cast(pl.Int32).alias("k"))
    )


def _player_game_values(inp: SeasonInputs) -> pl.DataFrame:
    """Per (game_id, team, gsis_id) with any row: the per-game values of a player for a team.
    Missing pieces are 0; ``played`` = offensive snaps or a stat line."""
    f = inp.frames
    k = ["game_id", "team", "gsis_id"]
    snaps = f["snaps"].select(
        *k,
        pl.col("offense_pct").alias("snap_share"),
        pl.col("offense_snaps"),
        pl.col("position").alias("snap_pos"),
    )
    stats = f["player_games"].select(
        *k, "targets", "target_share", "air_yards_share", "wopr", "carries", "fantasy_points",
        pl.lit(True).alias("has_stats"),
    )  # fmt: skip
    rz = f["red_zone"].select(*k, "rz_targets", "rz_carries", "gl_opps")
    xp = f["expected"].select(*k, "xfp")
    keys = pl.concat([x.select(k) for x in (snaps, stats, rz, xp)]).unique(maintain_order=True)
    out = keys.join(snaps, on=k, how="left").join(stats, on=k, how="left")
    out = out.join(rz, on=k, how="left").join(xp, on=k, how="left")
    out = out.join(f["team_carries"].select("game_id", "team", "team_carries"),
                   on=["game_id", "team"], how="left")  # fmt: skip
    out = out.join(f["team_plays"].select("game_id", "team", "dropbacks"),
                   on=["game_id", "team"], how="left")  # fmt: skip
    zero = ["snap_share", "offense_snaps", "targets", "target_share", "air_yards_share", "wopr",
            "carries", "fantasy_points", "rz_targets", "rz_carries", "gl_opps", "xfp",
            "dropbacks"]  # fmt: skip
    out = out.with_columns(
        [pl.col(c).cast(pl.Float64).fill_null(0.0) for c in zero]
        + [pl.col("has_stats").fill_null(False)]
    )
    return out.with_columns(
        pl.when(pl.col("team_carries") > 0)
        .then(pl.col("carries") / pl.col("team_carries"))
        .otherwise(0.0)
        .alias("carry_share"),
        (pl.col("dropbacks") * pl.col("snap_share")).alias("routes_proxy"),
        (pl.col("fantasy_points") - pl.col("xfp")).alias("fpoe"),
        ((pl.col("offense_snaps") > 0) | pl.col("has_stats")).alias("played"),
    )


_WINDOW_VALUES = (
    "snap_share", "target_share", "air_yards_share", "wopr", "carry_share", "rz_targets",
    "rz_carries", "gl_opps", "routes_proxy", "xfp", "fpoe", "fantasy_points",
)  # fmt: skip


def _opportunity(
    players: pl.DataFrame, games: pl.DataFrame, values: pl.DataFrame, window: int
) -> pl.DataFrame:
    """last / avg3 / season / delta per pool row (``_row``) over the team's visible games."""
    grid = players.select("_row", "gsis_id", "team").join(games, on="team", how="inner")
    grid = grid.join(
        values.select("game_id", "team", "gsis_id", *_WINDOW_VALUES),
        on=["game_id", "team", "gsis_id"],
        how="left",
    ).with_columns(pl.col(c).fill_null(0.0) for c in _WINDOW_VALUES)
    last = pl.col("k") == 1
    recent = pl.col("k") <= window
    before = (pl.col("k") >= 2) & (pl.col("k") <= window + 1)
    agg = grid.group_by("_row").agg(
        pl.col("snap_share").filter(last).mean().alias("snap_share_last"),
        pl.col("snap_share").filter(recent).mean().alias("snap_share_avg3"),
        pl.col("snap_share").mean().alias("snap_share_season"),
        pl.col("snap_share").filter(before).mean().alias("_snap_before"),
        pl.col("target_share").filter(last).mean().alias("target_share_last"),
        pl.col("target_share").filter(recent).mean().alias("target_share_avg3"),
        pl.col("air_yards_share").filter(recent).mean().alias("air_yards_share_avg3"),
        pl.col("wopr").filter(recent).mean().alias("wopr_avg3"),
        pl.col("carry_share").filter(last).mean().alias("carry_share_last"),
        pl.col("carry_share").filter(recent).mean().alias("carry_share_avg3"),
        pl.col("rz_targets").filter(recent).mean().alias("rz_targets_avg3"),
        pl.col("rz_carries").filter(recent).mean().alias("rz_carries_avg3"),
        pl.col("gl_opps").filter(recent).mean().alias("gl_opps_avg3"),
        pl.col("routes_proxy").filter(recent).mean().alias("routes_proxy_avg3"),
        pl.col("xfp").filter(last).mean().alias("xfp_last"),
        pl.col("xfp").filter(recent).mean().alias("xfp_avg3"),
        pl.col("fpoe").filter(recent).mean().alias("fpoe_avg3"),
        pl.col("fantasy_points").filter(last).mean().alias("fantasy_points_last"),
        pl.col("fantasy_points").filter(recent).mean().alias("fantasy_points_avg3"),
    )
    return agg.with_columns(
        (pl.col("snap_share_last") - pl.col("_snap_before")).alias("snap_share_delta")
    ).drop("_snap_before")


def _roster_facts(rosters: pl.DataFrame) -> dict[str, Any]:
    """Point-in-time roster facts: each player's latest and earliest row of the season, each
    team's latest roster week, each (team, player)'s latest row on that team, and whether the
    statuses are weekly (:func:`statuses_are_weekly`)."""
    r = _map_groups(rosters, "position", "pos_group")
    latest = r.sort(["gsis_id", "week", "team"]).group_by("gsis_id", maintain_order=True).last()
    first = r.sort(["gsis_id", "week", "team"]).group_by("gsis_id", maintain_order=True).first()
    team_week = r.group_by("team").agg(pl.col("week").max().alias("team_latest_week"))
    on_team = (
        r.sort(["team", "gsis_id", "week"])
        .group_by("team", "gsis_id", maintain_order=True)
        .last()
        .join(team_week, on="team", how="left")
        .with_columns((pl.col("week") == pl.col("team_latest_week")).alias("on_latest"))
    )
    return {
        "latest": latest,
        "first": first,
        "on_team": on_team,
        "statuses_are_weekly": statuses_are_weekly(rosters),
    }


def _unavailable_teammates(
    inp: SeasonInputs,
    games: pl.DataFrame,
    values: pl.DataFrame,
    roster: dict[str, Any],
    rules: FeatureRules,
) -> pl.DataFrame:
    """One row per (team, teammate) who played for the team this season (visible games) and is
    unavailable at the as-of, with every rule that fired and his shares over the last
    ``window`` team games up to his last appearance (before he became unavailable)."""
    w = rules.window
    played = values.filter(pl.col("played")).join(
        games, on=["game_id", "team"], how="inner"
    )  # his visible appearances for each team
    if played.height == 0:
        return _empty_teammates()
    last_seen = played.group_by("team", "gsis_id").agg(pl.col("k").min().alias("k_last"))
    # every team game from his last appearance back (window games), shares 0 when absent
    grid = last_seen.join(games, on="team", how="inner").filter(
        (pl.col("k") >= pl.col("k_last")) & (pl.col("k") < pl.col("k_last") + w)
    )
    grid = grid.join(
        values.select("game_id", "team", "gsis_id", "target_share", "carry_share", "snap_share"),
        on=["game_id", "team", "gsis_id"],
        how="left",
    ).with_columns(pl.col(c).fill_null(0.0) for c in ("target_share", "carry_share", "snap_share"))
    shares = grid.group_by("team", "gsis_id").agg(
        pl.col("k_last").first(),
        pl.col("week").max().alias("last_week"),
        pl.col("target_share").mean().alias("target_share_avg3"),
        pl.col("carry_share").mean().alias("carry_share_avg3"),
        pl.col("snap_share").mean().alias("snap_share_avg3"),
        pl.col("game_id").alias("window_games"),
    )
    # rule 4: missed the team's last game after averaging >= the threshold before it
    snap_by_k = (
        last_seen.select("team", "gsis_id")
        .join(games, on="team", how="inner")
        .join(
            values.select("game_id", "team", "gsis_id", "snap_share", "played"),
            on=["game_id", "team", "gsis_id"],
            how="left",
        )  # fmt: skip
        .with_columns(pl.col("snap_share").fill_null(0.0), pl.col("played").fill_null(False))
    )
    missed = snap_by_k.group_by("team", "gsis_id").agg(
        (~pl.col("played").filter(pl.col("k") == 1).any()).alias("_missed_last"),
        pl.col("snap_share").filter((pl.col("k") >= 2) & (pl.col("k") <= w + 1)).mean()
        .alias("_share_before"),
    )  # fmt: skip
    missed = missed.with_columns(
        (
            pl.col("_missed_last")
            & (pl.col("_share_before") >= rules.missed_game_min_snap_share).fill_null(False)
        ).alias("r_missed")
    )
    # rules 1 and 2: his row on the team's latest public roster (status), or no row there
    on_team = roster["on_team"].select(
        "team", "gsis_id", "on_latest", pl.col("status").alias("team_status"),
        pl.col("pos_group").alias("team_pos"), pl.col("full_name").alias("team_name"),
    )  # fmt: skip
    avail = list(rules.available_statuses)
    weekly = roster["statuses_are_weekly"]
    # rule 3: the team's latest public injury report lists him Out (or Doubtful)
    inj = inp.frames["injuries"]
    report_week = inj.group_by("team").agg(pl.col("week").max().alias("_report_week"))
    out_rows = (
        inj.join(report_week, on="team")
        .filter(pl.col("week") == pl.col("_report_week"))
        .filter(pl.col("report_status").is_in(list(rules.injury_statuses)))
        .group_by("team", "gsis_id")
        .agg(pl.col("report_status").first())
    )
    # his position: his latest row on the team's roster, else his latest snap row for it
    snap_pos = _map_groups(
        played.sort(["team", "gsis_id", "k"]).group_by("team", "gsis_id", maintain_order=True)
        .agg(pl.col("snap_pos").drop_nulls().first()),
        "snap_pos",
        "snap_group",
    )  # fmt: skip
    t = (
        shares.join(missed.select("team", "gsis_id", "r_missed"), on=["team", "gsis_id"])
        .join(on_team, on=["team", "gsis_id"], how="left")
        .join(out_rows, on=["team", "gsis_id"], how="left")
        .join(
            snap_pos.select("team", "gsis_id", "snap_group"), on=["team", "gsis_id"], how="left"
        )  # fmt: skip
        .with_columns(
            (
                pl.lit(weekly)
                & pl.col("on_latest").fill_null(False)
                & pl.col("team_status").is_not_null()
                & ~pl.col("team_status").is_in(avail)
            ).alias("r_status"),
            (pl.col("on_latest").is_not_null() & ~pl.col("on_latest").fill_null(True)).alias(
                "r_left"
            ),
            pl.col("report_status").is_not_null().alias("r_injury"),
            pl.coalesce("team_pos", "snap_group").alias("position"),
        )
    )
    flags = {"roster_status": "r_status", "left_team": "r_left", "injury_report": "r_injury",
             "missed_last_game": "r_missed"}  # fmt: skip
    t = t.filter(pl.any_horizontal([pl.col(c) for c in flags.values()]))
    t = t.filter(pl.col("position").is_in(list(FANTASY_POSITIONS)))
    fired = [pl.when(pl.col(c)).then(pl.lit(name)) for name, c in flags.items()]
    t = t.with_columns(
        pl.concat_str(fired, separator="+", ignore_nulls=True).alias("rules"),
        pl.coalesce(fired).alias("rule"),
    )
    names = roster["latest"].select("gsis_id", pl.col("full_name").alias("latest_name"))
    t = t.join(names, on="gsis_id", how="left").with_columns(
        pl.coalesce("team_name", "latest_name").alias("name")
    )
    return t.select(
        "team", "gsis_id", "name", "position", "rule", "rules", "team_status", "report_status",
        "k_last", "last_week", "target_share_avg3", "carry_share_avg3", "snap_share_avg3",
        "window_games",
    ).sort("team", "gsis_id")  # fmt: skip


def _empty_teammates() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "team": pl.String, "gsis_id": pl.String, "name": pl.String, "position": pl.String,
            "rule": pl.String, "rules": pl.String, "team_status": pl.String,
            "report_status": pl.String, "k_last": pl.Int32, "last_week": pl.Int32,
            "target_share_avg3": pl.Float64, "carry_share_avg3": pl.Float64,
            "snap_share_avg3": pl.Float64, "window_games": pl.List(pl.String),
        }
    )  # fmt: skip


def _teammate_features(
    players: pl.DataFrame, out: pl.DataFrame, values: pl.DataFrame
) -> pl.DataFrame:
    """Vacated shares, counts, top_teammate_out and the teammates_out text per pool row."""
    pairs = (
        players.select("_row", "gsis_id", "team", "position")
        .join(
            out.rename({"gsis_id": "mate_id", "position": "mate_pos", "name": "mate_name"}),
            on="team",
            how="inner",
        )
        .filter(pl.col("mate_id") != pl.col("gsis_id"))
    )
    same = pl.col("mate_pos") == pl.col("position")
    # the player's own snap share over each same-position teammate's window games
    mine = (
        pairs.filter(same)
        .select("_row", "mate_id", "gsis_id", "team", "window_games")
        .explode("window_games", empty_as_null=True)
        .rename({"window_games": "game_id"})
        .join(
            values.select("game_id", "team", "gsis_id", "snap_share"),
            on=["game_id", "team", "gsis_id"],
            how="left",
        )  # fmt: skip
        .group_by("_row", "mate_id")
        .agg(pl.col("snap_share").fill_null(0.0).mean().alias("_my_snap"))
    )
    pairs = pairs.join(mine, on=["_row", "mate_id"], how="left")
    agg = pairs.group_by("_row").agg(
        pl.col("target_share_avg3").sum().alias("vacated_target_share"),
        pl.col("carry_share_avg3").sum().alias("vacated_carry_share"),
        pl.col("target_share_avg3").filter(same).sum().alias("same_pos_vacated_target_share"),
        pl.col("carry_share_avg3").filter(same).sum().alias("same_pos_vacated_carry_share"),
        same.sum().cast(pl.Int32).alias("teammate_same_pos_unavailable"),
        (same & (pl.col("snap_share_avg3") > pl.col("_my_snap"))).any().alias("top_teammate_out"),
    )
    text = (
        pairs.sort(
            "_row", "mate_pos", "snap_share_avg3", "mate_id", descending=[False, False, True, False]
        )  # fmt: skip
        .group_by("_row", maintain_order=True)
        .agg(
            pl.struct(
                "mate_id",
                "mate_name",
                "mate_pos",
                "rule",
                "rules",
                "last_week",
                "target_share_avg3",
                "carry_share_avg3",
                "snap_share_avg3",
            ).alias("_mates")
        )  # fmt: skip
        .with_columns(
            pl.col("_mates")
            .map_elements(_teammates_json, return_dtype=pl.String)
            .alias("teammates_out")
        )
        .drop("_mates")
    )
    return agg.join(text, on="_row", how="left")


def _teammates_json(mates: list[dict[str, Any]] | pl.Series) -> str:
    rows = mates.to_list() if isinstance(mates, pl.Series) else mates
    out = [
        {
            "gsis_id": m["mate_id"],
            "name": m["mate_name"],
            "position": m["mate_pos"],
            "rule": m["rule"],
            "rules": m["rules"],
            "last_week": m["last_week"],
            "target_share_avg3": round(float(m["target_share_avg3"]), 4),
            "carry_share_avg3": round(float(m["carry_share_avg3"]), 4),
            "snap_share_avg3": round(float(m["snap_share_avg3"]), 4),
        }
        for m in rows
    ]
    return json.dumps(out, sort_keys=True, separators=(",", ":"))


def _depth_ranks(depth: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """The chart in force per team (its latest ``chart_time``): (a) every player listed on
    it, (b) each offensive player's rank in his position group: 1 + the number of group
    players with a strictly better (lower) best depth rank (ties share the better rank)."""
    if depth.height == 0:
        empty_l = pl.DataFrame(schema={"team": pl.String, "gsis_id": pl.String})
        empty_r = pl.DataFrame(
            schema={
                "team": pl.String,
                "gsis_id": pl.String,
                "group": pl.String,
                "rank": pl.Int32,
            }  # fmt: skip
        )
        return empty_l, empty_r
    cur = depth.filter(pl.col("chart_time") == pl.col("chart_time").max().over("team"))
    listed = cur.select("team", "gsis_id").unique().sort("team", "gsis_id")
    off = _map_groups(cur.filter(pl.col("unit") == "offense"), "slot", "group").filter(
        pl.col("group").is_not_null() & pl.col("depth_rank").is_not_null()
    )
    best = off.group_by("team", "group", "gsis_id").agg(pl.col("depth_rank").min().alias("best"))
    ranks = best.with_columns(
        pl.col("best").rank("min").over("team", "group").cast(pl.Int32).alias("rank")
    ).select("team", "gsis_id", "group", "rank")
    return listed, ranks


def _depth_features(
    players: pl.DataFrame, depth: pl.DataFrame, as_of: datetime, rules: FeatureRules
) -> pl.DataFrame:
    listed, now = _depth_ranks(depth)
    _, prev = _depth_ranks(asof_filter(depth, as_of - rules.depth_prev_lag))
    teams_with_chart = listed.select("team").unique()
    p = players.select("_row", "gsis_id", "team", pl.col("position").alias("group"))
    p = p.join(now.rename({"rank": "depth_rank_now"}), on=["team", "gsis_id", "group"],
               how="left")  # fmt: skip
    p = p.join(prev.rename({"rank": "depth_rank_prev"}), on=["team", "gsis_id", "group"],
               how="left")  # fmt: skip
    p = p.join(listed.with_columns(pl.lit(True).alias("_listed")), on=["team", "gsis_id"],
               how="left")  # fmt: skip
    p = p.join(teams_with_chart.with_columns(pl.lit(True).alias("_has_chart")), on="team",
               how="left")  # fmt: skip
    return p.select(
        "_row",
        "depth_rank_now",
        "depth_rank_prev",
        (pl.col("depth_rank_prev") - pl.col("depth_rank_now")).alias("depth_rank_change"),
        pl.when(pl.col("_has_chart"))
        .then(pl.col("_listed").fill_null(False))
        .alias("depth_listed"),
    )


def _team_context(games: pl.DataFrame, plays: pl.DataFrame) -> pl.DataFrame:
    """Per team: EPA per play (all / neutral), plays per game, neutral pass rate, to date."""
    n_games = games.group_by("team").agg(pl.len().alias("_n_games"))
    p = plays.join(games.select("game_id", "team"), on=["game_id", "team"], how="inner")
    agg = p.group_by("team").agg(
        pl.col("n_plays").sum(), pl.col("n_epa").sum(), pl.col("epa_sum").sum(),
        pl.col("n_neutral").sum(), pl.col("n_epa_neutral").sum(),
        pl.col("epa_sum_neutral").sum(), pl.col("n_pass_neutral").sum(),
    )  # fmt: skip
    agg = n_games.join(agg, on="team", how="left")

    def ratio(num: str, den: str) -> pl.Expr:
        return pl.when(pl.col(den) > 0).then(pl.col(num) / pl.col(den))

    return agg.select(
        "team",
        ratio("epa_sum", "n_epa").alias("team_epa_per_play"),
        ratio("epa_sum_neutral", "n_epa_neutral").alias("team_epa_per_play_neutral"),
        ratio("n_plays", "_n_games").alias("team_plays_per_game"),
        ratio("n_pass_neutral", "n_neutral").alias("team_pass_rate_neutral"),
    )


def _schedule_rows(inp: SeasonInputs, rules: FeatureRules) -> pl.DataFrame:
    """(team, week, opponent) for every visible regular-season fixture of the season, plus
    the listed cancelled games (still on the schedule until they were cancelled)."""
    s = inp.frames["schedule"]
    rows = pl.concat(
        [
            s.select(pl.col("home_team").alias("team"), "week",
                     pl.col("away_team").alias("opponent"), "game_id"),
            s.select(pl.col("away_team").alias("team"), "week",
                     pl.col("home_team").alias("opponent"), "game_id"),
        ]
    )  # fmt: skip
    extra = []
    for gid in rules.cancelled_games:
        parts = gid.split("_")
        if len(parts) != 4 or not parts[0].isdigit() or int(parts[0]) != inp.season:
            continue
        week, away, home = int(parts[1]), parts[2], parts[3]
        if gid in set(s.get_column("game_id").to_list()):
            continue
        extra += [
            {"team": home, "week": week, "opponent": away, "game_id": gid},
            {"team": away, "week": week, "opponent": home, "game_id": gid},
        ]
    if extra:
        rows = pl.concat([rows, pl.DataFrame(extra, schema=rows.schema)])
    return rows.sort("team", "week", "game_id")


def _opponent_strength(
    inp: SeasonInputs, games: pl.DataFrame, roster_latest: pl.DataFrame
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(a) per (defense, position group): fantasy points allowed per game to date divided by
    the league average for the group; (b) per team: visible games played."""
    f = inp.frames
    n_games = games.group_by("team").agg(pl.len().cast(pl.Int32).alias("n_games"))
    scored = f["player_games"].join(games.select("game_id", "team"), on=["game_id", "team"],
                                    how="inner")  # fmt: skip
    snap_pos = f["snaps"].select("game_id", "gsis_id", pl.col("position").alias("_snap_pos"))
    snap_pos = snap_pos.unique(["game_id", "gsis_id"], keep="first", maintain_order=True)
    scored = scored.join(snap_pos, on=["game_id", "gsis_id"], how="left").join(
        roster_latest.select("gsis_id", pl.col("position").alias("_roster_pos")),
        on="gsis_id",
        how="left",
    )
    scored = _map_groups(scored, "_snap_pos", "_g1")
    scored = _map_groups(scored, "_roster_pos", "_g2")
    scored = scored.with_columns(pl.coalesce("_g1", "_g2").alias("group")).filter(
        pl.col("group").is_in(list(FANTASY_POSITIONS))
    )
    allowed = scored.group_by(pl.col("opponent_team").alias("defense"), "group").agg(
        pl.col("fantasy_points").sum().alias("points")
    )
    total_games = int(n_games.get_column("n_games").sum()) if n_games.height else 0
    league = allowed.group_by("group").agg(pl.col("points").sum().alias("_league_points"))
    grid = (
        n_games.rename({"team": "defense"})
        .join(pl.DataFrame({"group": list(FANTASY_POSITIONS)}), how="cross")
        .join(allowed, on=["defense", "group"], how="left")
        .join(league, on="group", how="left")
        .with_columns(pl.col("points").fill_null(0.0))
    )
    avg = pl.col("_league_points") / total_games if total_games else pl.lit(None)
    grid = grid.with_columns(
        pl.when(avg > 0)
        .then((pl.col("points") / pl.col("n_games")) / avg)
        .otherwise(1.0)
        .alias("ratio")
    )
    return grid.select("defense", "group", "ratio"), n_games


def _schedule_features(
    players: pl.DataFrame,
    inp: SeasonInputs,
    week: int,
    games: pl.DataFrame,
    roster_latest: pl.DataFrame,
    rules: FeatureRules,
) -> pl.DataFrame:
    sched = _schedule_rows(inp, rules)
    ahead = sched.filter(pl.col("week") > week)
    remaining = ahead.group_by("team").agg(pl.len().cast(pl.Int32).alias("team_games_remaining"))
    nxt = (
        ahead.sort("team", "week", "game_id")
        .group_by("team", maintain_order=True)
        .head(rules.window)
    )
    ratio, n_games = _opponent_strength(inp, games, roster_latest)
    last = inp.last_reg_week
    horizon = [] if last is None else list(range(week + 1, min(week + rules.window, last) + 1))
    p = players.select("_row", "team", pl.col("position").alias("group"))
    opp = (
        p.join(nxt.select("team", "opponent"), on="team", how="inner")
        .join(ratio.rename({"defense": "opponent"}), on=["opponent", "group"], how="left")
        .join(n_games.rename({"team": "opponent"}), on="opponent", how="left")
        .group_by("_row")
        .agg(
            pl.col("ratio").fill_null(1.0).mean().alias("opp_fp_allowed_next3"),
            pl.col("n_games").fill_null(0).sum().cast(pl.Int32).alias("n_opp_games_seen"),
        )
    )
    weeks_with_game = (
        sched.filter(pl.col("week").is_in(horizon))
        .group_by("team")
        .agg(pl.col("week").n_unique().alias("_n_weeks"))
    )
    out = (
        p.join(opp, on="_row", how="left")
        .join(remaining, on="team", how="left")
        .join(weeks_with_game, on="team", how="left")
        .with_columns(
            pl.col("team_games_remaining").fill_null(0),
            (pl.col("_n_weeks").fill_null(0) < len(horizon)).alias("bye_in_next3"),
        )
    )
    return out.select("_row", "opp_fp_allowed_next3", "n_opp_games_seen", "bye_in_next3",
                      "team_games_remaining")  # fmt: skip


def compute_features(
    inp: SeasonInputs,
    season: int,
    week: int,
    as_of: datetime,
    pool_rows: pl.DataFrame,
    rules: FeatureRules,
) -> pl.DataFrame:
    """The features of ``pool_rows`` at ``as_of`` from inputs already filtered to it
    (:meth:`SeasonInputs.visible`). Returns KEY_COLUMNS + FEATURE_COLUMNS + INFO_COLUMNS in
    the pool rows' order."""
    _check_pool_rows(pool_rows, season, week)
    if inp.batch:
        raise ValueError("inputs still hold future rows: call .visible(as_of) first")
    f = inp.frames
    players = pool_rows.select(REQUIRED_POOL_COLUMNS).with_row_index("_row")
    games = _team_game_index(f["team_games"])
    values = _player_game_values(inp)
    roster = _roster_facts(f["rosters"])

    opp = _opportunity(players, games, values, rules.window)
    mates = _unavailable_teammates(inp, games, values, roster, rules)
    tm = _teammate_features(players, mates, values)
    depth = _depth_features(players, f["depth"], as_of, rules)
    ctx = _team_context(games, f["team_plays"])
    sched = _schedule_features(players, inp, week, games, roster["latest"], rules)

    has_games = games.select("team").unique().with_columns(pl.lit(True).alias("_has_games"))
    latest = roster["latest"].select("gsis_id", "entry_year", "years_exp")
    first = roster["first"].select("gsis_id", pl.col("team").alias("_first_team"))
    who = f["players"].select("gsis_id", "birth_date", "draft_round")
    ref_day = to_utc(as_of).date()

    out = (
        players.join(opp, on="_row", how="left")
        .join(tm, on="_row", how="left")
        .join(depth, on="_row", how="left")
        .join(sched, on="_row", how="left")
        .join(ctx, on="team", how="left")
        .join(has_games, on="team", how="left")
        .join(latest, on="gsis_id", how="left")
        .join(first, on="gsis_id", how="left")
        .join(who, on="gsis_id", how="left")
        .sort("_row")
    )
    has = pl.col("_has_games").fill_null(False)
    vacated = ("vacated_target_share", "vacated_carry_share", "same_pos_vacated_target_share",
               "same_pos_vacated_carry_share")  # fmt: skip
    out = out.with_columns(
        # teammates: 0 / False when the team has games but nobody is out; NULL without games
        *[pl.when(has).then(pl.col(c).fill_null(0.0)).alias(c) for c in vacated],
        pl.when(has).then(pl.col("teammate_same_pos_unavailable").fill_null(0))
        .alias("teammate_same_pos_unavailable"),
        pl.when(has).then(pl.col("top_teammate_out").fill_null(False)).alias("top_teammate_out"),
        pl.when(has).then(pl.col("teammates_out").fill_null("[]")).alias("teammates_out"),
        pl.col("preseason_pos_rank").is_not_null().alias("preseason_ranked"),
        pl.col("games_to_date").alias("games_played_to_date"),
        (pl.col("_first_team") != pl.col("team")).alias("joined_team_recently"),
        ((pl.lit(ref_day) - pl.col("birth_date")).dt.total_days() / 365.25).alias("age_at_asof"),
        (pl.col("entry_year") == season).alias("is_rookie"),
        pl.col("draft_round").is_null().alias("is_undrafted"),
    )  # fmt: skip
    schema = FEATURE_SCHEMA
    cols = []
    for c in FEATURE_COLUMNS:
        e = pl.col(c).cast(schema[c])
        if schema[c] == pl.Float64():
            e = e.round(FLOAT_DECIMALS)
        cols.append(e.alias(c))
    return out.select(
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        "gsis_id",
        *cols,
        pl.col("teammates_out").cast(pl.String),
    )


def empty_features() -> pl.DataFrame:
    """A typed frame with the output columns of :func:`compute_features` and no rows."""
    return pl.DataFrame(
        schema={
            "season": pl.Int32,
            "week": pl.Int32,
            "gsis_id": pl.String,
            **FEATURE_SCHEMA,
            "teammates_out": pl.String,
        }  # fmt: skip
    ).select("season", "week", "gsis_id", *FEATURE_COLUMNS, *INFO_COLUMNS)


def features_for(
    view: SqlSource,
    season: int,
    week: int,
    pool_rows: pl.DataFrame,
    *,
    rules: FeatureRules | None = None,
) -> pl.DataFrame:
    """The reference implementation: the features of ``pool_rows`` (C1 pool rows of
    ``season``/``week``) at the view's as-of, every input read through the view.

    ``view`` is an :class:`~twm.asof.AsOfView` (anything with ``sql`` and ``as_of``).
    Returns KEY_COLUMNS + FEATURE_COLUMNS + INFO_COLUMNS, one row per pool row, in order."""
    rules = rules or FeatureRules.from_config()
    as_of = view.as_of  # type: ignore[attr-defined]
    inp = load_inputs(view, season, rules, batch=False)
    return compute_features(inp.visible(as_of), season, week, as_of, pool_rows, rules)


def features_history(
    db: Path | str,
    pool: pl.DataFrame,
    *,
    rules: FeatureRules | None = None,
) -> pl.DataFrame:
    """The batch path: the features of every pool row of ``pool`` (C1 pool history, one
    method per as-of), each season's inputs read once and filtered per as-of (``as_of``
    column). Equals :func:`features_for` row by row (tested)."""
    from twm.warehouse.build import connect

    rules = rules or FeatureRules.from_config()
    missing = [c for c in (*REQUIRED_POOL_COLUMNS, "as_of") if c not in pool.columns]
    if missing:
        raise KeyError(f"pool rows lack columns {missing}")
    if pool.height == 0:
        return empty_features()
    frames = []
    con = connect(db, read_only=True)
    try:
        source = _Warehouse(con)
        for season in sorted(pool.get_column("season").unique().to_list()):
            inputs = load_inputs(source, int(season), rules, batch=True)
            rows = pool.filter(pl.col("season") == season)
            for (week, as_of), grp in sorted(
                rows.partition_by(["week", "as_of"], as_dict=True, maintain_order=True).items()
            ):
                frames.append(
                    compute_features(
                        inputs.visible(as_of), int(season), int(week), as_of, grp, rules
                    )
                )
    finally:
        con.close()
    return pl.concat(frames, how="vertical")


def unavailable_teammates(
    view: SqlSource, season: int, *, rules: FeatureRules | None = None
) -> pl.DataFrame:
    """Every unavailable teammate per team at the view's as-of, with the rules that fired
    (the teammate half of :func:`features_for`, for inspection and C6 reasons)."""
    rules = rules or FeatureRules.from_config()
    inp = load_inputs(view, season, rules, batch=False).visible(view.as_of)  # type: ignore[attr-defined]
    games = _team_game_index(inp.frames["team_games"])
    values = _player_game_values(inp)
    return _unavailable_teammates(inp, games, values, _roster_facts(inp.frames["rosters"]), rules)


def uses_bypass(queries: dict[str, str]) -> bool:
    """True when any input SQL reaches the raw warehouse (``wh.``): never in this module."""
    return any(re.search(r"\bwh\.", q, re.IGNORECASE) for q in queries.values())


__all__ = [
    "CATEGORICAL_FEATURES", "FEATURE_COLUMNS", "FEATURE_FAMILIES", "INFO_COLUMNS",
    "KEY_COLUMNS", "FeatureRules", "SeasonInputs", "compute_features", "features_for",
    "features_history", "input_sql", "load_inputs", "slot_group", "unavailable_teammates",
]  # fmt: skip
