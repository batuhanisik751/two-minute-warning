"""The Regression Watch frame (step D1): one row per player-game with fantasy points, expected
fantasy points (xFP) and points over expected (FPOE), with and without garbage time.

One row per regular-season game of a QB, RB, WR or TE, 2006 on (ffopportunity's first season):
every player with a weekly stat line (``fact_player_week``) or an ffopportunity row
(``fact_opportunity_week``) in the game. docs/regression_watch.md explains every column for a
beginner; in short:

- ``fantasy_points``: the weekly stat line scored with config/scoring.yaml (0 when the player
  has an ffopportunity row but no stat line), exactly what the Waiver Radar and the published
  ``player_week_summary`` use;
- ``xfp``: ffopportunity's expected stats of the game re-scored with the same weights
  (:func:`twm.scoring.xfp_sql`; NULL without an ffopportunity row); ``fpoe`` = points - xFP;
- ``points_garbage`` / ``xfp_garbage``: the points and expected points of his garbage-time plays
  (``fact_play.is_garbage_time``), computed play by play (:mod:`.plays`);
- ``points_ng`` = ``fantasy_points - points_garbage``, ``xfp_ng`` = ``xfp - xfp_garbage``,
  ``fpoe_ng`` = ``points_ng - xfp_ng``: the same three without garbage time. Subtracting (rather
  than re-adding the other plays) keeps ``points_ng == fantasy_points`` for a player without a
  garbage-time play; the two definitions differ only where the play-by-play does not reproduce
  the weekly line (0.06% of player-games, docs/regression_watch.md);
- ``play_points`` / ``play_xfp``: the sums over ALL his plays (the reconciliation columns);
- components for the stability study (D2): targets, receptions and expected receptions,
  receiving/rushing/passing yards and expected yards, touchdowns and expected touchdowns,
  interceptions, all from the SAME ffopportunity row (actual and expected over the same plays,
  :data:`COMPONENTS`), and yards after the catch with its expectation from the per-play file.

**Position (point in time, as the Waiver Radar does it).** The player's weekly-roster position
(``fact_roster_week``) as public at his week's official Tuesday as-of: that week's roster row if
it is public by then ('roster', game-day rosters from 2016), else his latest earlier roster row
of the season public by then ('roster_earlier': 2002-2015 rosters are post-game snapshots,
public a week later), else that game's snap-count position ('snaps', 2013 on), else his latest
roster row of the previous season ('roster_previous_season'). Never today's position. Rows
without one of QB/RB/WR/TE are left out (fullbacks listed as FB are not in the frame).

**Point in time.** Everything is read through an :class:`~twm.asof.AsOfView`. A row counts as
public (``available_at``) at the later of its week's Tuesday as-of and the moment every
game-data input of its game is public (game end + the largest ``game_data_lag_hours``), so a
split-week game joins later, and a row never changes after it appears. Two entry points:

- :func:`player_games_for` / :func:`player_games_asof`: the frame as it looked at an as-of (the
  rows of one season public by then);
- :func:`player_games_history`: every season at once through one view, each row with its
  ``available_at``; filtering it to ``available_at <= as_of`` equals :func:`player_games_for`
  at that as-of (tested, also with the leakage harness).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import polars as pl

from twm.asof import AsOfView, sql_timestamp, to_utc, weekly_as_of
from twm.config import FANTASY_POSITIONS
from twm.modules.regression_watch.plays import play_expected_sql, play_stats_sql
from twm.scoring import ScoringRules, score_sql, xfp_sql

# ffopportunity starts in 2006: earlier seasons have no expected points.
FIRST_SEASON = 2006
# The history path reads the warehouse "as it looks at the end of time": every built row.
END_OF_TIME = datetime(9999, 12, 31, tzinfo=UTC)
FLOAT_DECIMALS = 6  # floats are rounded so the two paths and two runs agree to the bit

# Frame component -> the fact_opportunity_week column it is (actual and expected come from the
# same ffopportunity row, i.e. the same plays; verified names: data/schemas/ff_opportunity.json).
COMPONENTS: dict[str, str] = {
    "targets": "rec_attempt",
    "receptions": "receptions",
    "receptions_exp": "receptions_exp",
    "receiving_yards": "rec_yards_gained",
    "receiving_yards_exp": "rec_yards_gained_exp",
    "receiving_tds": "rec_touchdown",
    "receiving_tds_exp": "rec_touchdown_exp",
    "carries": "rush_attempt",
    "rushing_yards": "rush_yards_gained",
    "rushing_yards_exp": "rush_yards_gained_exp",
    "rushing_tds": "rush_touchdown",
    "rushing_tds_exp": "rush_touchdown_exp",
    "pass_attempts": "pass_attempt",
    "completions": "pass_completions",
    "completions_exp": "pass_completions_exp",
    "passing_yards": "pass_yards_gained",
    "passing_yards_exp": "pass_yards_gained_exp",
    "passing_tds": "pass_touchdown",
    "passing_tds_exp": "pass_touchdown_exp",
    "interceptions": "pass_interception",
    "interceptions_exp": "pass_interception_exp",
}
# From the per-play pass file (caught targets, two-point tries excluded).
YAC_COLUMNS = ("yac", "yac_exp")

KEY_COLUMNS = ("season", "week", "game_id", "gsis_id")
POINT_COLUMNS = (
    "fantasy_points", "xfp", "fpoe", "points_ng", "xfp_ng", "fpoe_ng", "points_garbage",
    "xfp_garbage", "play_points", "play_xfp",
)  # fmt: skip
COUNT_COLUMNS = ("n_opportunities", "n_opportunities_garbage")
FRAME_COLUMNS = (
    *KEY_COLUMNS, "team", "position", "position_source", *POINT_COLUMNS, *COUNT_COLUMNS,
    *COMPONENTS, *YAC_COLUMNS, "available_at",
)  # fmt: skip
POSITION_SOURCES = ("roster", "roster_earlier", "snaps", "roster_previous_season")

FRAME_SCHEMA: dict[str, pl.DataType] = {
    "season": pl.Int32(),
    "week": pl.Int32(),
    "game_id": pl.String(),
    "gsis_id": pl.String(),
    "team": pl.String(),
    "position": pl.String(),
    "position_source": pl.String(),
    **dict.fromkeys(POINT_COLUMNS, pl.Float64()),
    **dict.fromkeys(COUNT_COLUMNS, pl.Int32()),
    **dict.fromkeys(COMPONENTS, pl.Float64()),
    **dict.fromkeys(YAC_COLUMNS, pl.Float64()),
    "available_at": pl.Datetime("us"),
}
assert tuple(FRAME_SCHEMA) == FRAME_COLUMNS


class SqlView(Protocol):
    """What the frame needs from an :class:`~twm.asof.AsOfView`."""

    @property
    def as_of(self) -> datetime: ...

    def sql(self, query: str, params: Sequence[Any] | None = None) -> pl.DataFrame: ...


def _in(values: Sequence[int]) -> str:
    return ", ".join(str(int(v)) for v in values) or "NULL"


def _interval(td: timedelta) -> str:
    return f"to_microseconds(CAST({round(td.total_seconds() * 1_000_000)} AS BIGINT))"


def input_lag() -> timedelta:
    """The largest game-data lag of the frame's inputs (config ``availability``): a game's row
    is public only once every input of the game is (stats, expected points, plays, snaps)."""
    from twm.warehouse.available import AvailabilityRules

    lags = AvailabilityRules.from_settings().game_data_lag
    return max(lags[k] for k in ("player_stats", "pbp", "snap_counts"))


def frame_sql(
    seasons: Sequence[int],
    cutoff: datetime,
    rules: ScoringRules,
    lag: timedelta,
    positions: Sequence[str] | None = FANTASY_POSITIONS,
) -> str:
    """The frame's SQL (run through an AsOfView): rows of ``seasons`` public at ``cutoff``
    (naive UTC). Every input is read from the view's point-in-time tables. ``positions=None``
    keeps every row, also those without a point-in-time position (for the coverage report)."""
    seasons = sorted({int(s) for s in seasons if int(s) >= FIRST_SEASON})
    s = _in(seasons)
    prev = _in(sorted({x - 1 for x in seasons} | set(seasons)))
    reg = f"season_type = 'REG' AND season IN ({s})"
    keep = (
        "TRUE"
        if positions is None
        else "f.position IN (" + ", ".join(f"'{p}'" for p in positions) + ")"
    )
    d = FLOAT_DECIMALS
    comps = ",\n               ".join(
        f"CAST({src} AS DOUBLE) AS {name}" for name, src in COMPONENTS.items()
    )
    comp_out = ", ".join(f"round(ex.{c}, {d}) AS {c}" for c in COMPONENTS)
    return f"""
    WITH st AS (
        SELECT game_id, player_id AS gsis_id, team, {score_sql(rules)} AS fantasy_points
        FROM fact_player_week
        WHERE {reg} AND player_id IS NOT NULL AND game_id IS NOT NULL
    ), ex AS (
        SELECT game_id, player_id AS gsis_id, posteam AS team, {xfp_sql(rules)} AS xfp,
               {comps}
        FROM fact_opportunity_week WHERE {reg}
    ), pp AS (
        SELECT game_id, gsis_id, sum(points) AS play_points,
               COALESCE(sum(points) FILTER (WHERE is_garbage_time), 0) AS points_garbage
        FROM (SELECT game_id, gsis_id, is_garbage_time, {score_sql(rules)} AS points
              FROM ({play_stats_sql(reg)}))
        GROUP BY game_id, gsis_id
    ), px AS (
        SELECT game_id, gsis_id, sum(x) AS play_xfp,
               COALESCE(sum(x) FILTER (WHERE is_garbage_time), 0) AS xfp_garbage,
               count(*) AS n_opportunities,
               count(*) FILTER (WHERE is_garbage_time) AS n_opportunities_garbage,
               sum(yac) AS yac, sum(yac_exp) AS yac_exp
        FROM (SELECT game_id, gsis_id, is_garbage_time, yac, yac_exp, {xfp_sql(rules)} AS x
              FROM ({play_expected_sql(reg)}))
        GROUP BY game_id, gsis_id
    ), keys AS (
        SELECT game_id, gsis_id FROM st UNION SELECT game_id, gsis_id FROM ex
    ), base AS (
        SELECT k.game_id, k.gsis_id, g.season, g.week, COALESCE(st.team, ex.team) AS team,
               w.asof_weekly_utc AS week_asof,
               GREATEST(w.asof_weekly_utc,
                        g.availability_game_end_utc + {_interval(lag)}) AS available_at
        FROM keys k
        JOIN fact_game g ON g.game_id = k.game_id AND g.season_type = 'REG'
        JOIN dim_week w ON w.season = g.season AND w.week = g.week AND w.season_type = 'REG'
        LEFT JOIN st ON st.game_id = k.game_id AND st.gsis_id = k.gsis_id
        LEFT JOIN ex ON ex.game_id = k.game_id AND ex.gsis_id = k.gsis_id
    ), ros AS (
        SELECT season, week, gsis_id, position, available_at FROM fact_roster_week
        WHERE season IN ({prev}) AND gsis_id IS NOT NULL AND position IS NOT NULL
    ), pos_season AS (
        -- his latest roster row of the season up to the game's week, public at the week's as-of
        SELECT b.game_id, b.gsis_id, r.position,
               CASE WHEN r.week = b.week THEN 'roster' ELSE 'roster_earlier' END AS source
        FROM base b JOIN ros r
          ON r.season = b.season AND r.gsis_id = b.gsis_id AND r.week <= b.week
         AND r.available_at <= b.week_asof
        QUALIFY row_number() OVER (PARTITION BY b.game_id, b.gsis_id ORDER BY r.week DESC) = 1
    ), pos_prev AS (
        SELECT b.game_id, b.gsis_id, r.position
        FROM base b JOIN ros r
          ON r.season = b.season - 1 AND r.gsis_id = b.gsis_id AND r.available_at <= b.week_asof
        QUALIFY row_number() OVER (PARTITION BY b.game_id, b.gsis_id ORDER BY r.week DESC) = 1
    ), sn AS (
        -- (game_id, gsis_id) is unique among snap rows with a gsis_id (checked by the build)
        SELECT game_id, gsis_id, min(position) AS position FROM fact_snaps
        WHERE season IN ({s}) AND gsis_id IS NOT NULL AND position IS NOT NULL
        GROUP BY game_id, gsis_id
    ), framed AS (
        SELECT b.*,
               COALESCE(ps.position, sn.position, pv.position) AS position,
               CASE WHEN ps.position IS NOT NULL THEN ps.source
                    WHEN sn.position IS NOT NULL THEN 'snaps'
                    WHEN pv.position IS NOT NULL THEN 'roster_previous_season' END
                   AS position_source,
               COALESCE(st.fantasy_points, 0) AS fantasy_points, ex.xfp,
               COALESCE(pp.points_garbage, 0) AS points_garbage,
               CASE WHEN ex.xfp IS NOT NULL THEN COALESCE(px.xfp_garbage, 0) END AS xfp_garbage,
               COALESCE(pp.play_points, 0) AS play_points, px.play_xfp,
               COALESCE(px.n_opportunities, 0) AS n_opportunities,
               COALESCE(px.n_opportunities_garbage, 0) AS n_opportunities_garbage,
               px.yac, px.yac_exp
        FROM base b
        LEFT JOIN pos_season ps ON ps.game_id = b.game_id AND ps.gsis_id = b.gsis_id
        LEFT JOIN sn ON sn.game_id = b.game_id AND sn.gsis_id = b.gsis_id
        LEFT JOIN pos_prev pv ON pv.game_id = b.game_id AND pv.gsis_id = b.gsis_id
        LEFT JOIN st ON st.game_id = b.game_id AND st.gsis_id = b.gsis_id
        LEFT JOIN ex ON ex.game_id = b.game_id AND ex.gsis_id = b.gsis_id
        LEFT JOIN pp ON pp.game_id = b.game_id AND pp.gsis_id = b.gsis_id
        LEFT JOIN px ON px.game_id = b.game_id AND px.gsis_id = b.gsis_id
    )
    SELECT f.season, f.week, f.game_id, f.gsis_id, f.team, f.position, f.position_source,
           round(f.fantasy_points, {d}) AS fantasy_points,
           round(f.xfp, {d}) AS xfp,
           round(f.fantasy_points - f.xfp, {d}) AS fpoe,
           round(f.fantasy_points - f.points_garbage, {d}) AS points_ng,
           round(f.xfp - f.xfp_garbage, {d}) AS xfp_ng,
           round((f.fantasy_points - f.points_garbage) - (f.xfp - f.xfp_garbage), {d})
               AS fpoe_ng,
           round(f.points_garbage, {d}) AS points_garbage,
           round(f.xfp_garbage, {d}) AS xfp_garbage,
           round(f.play_points, {d}) AS play_points,
           round(f.play_xfp, {d}) AS play_xfp,
           f.n_opportunities, f.n_opportunities_garbage,
           {comp_out},
           round(f.yac, {d}) AS yac, round(f.yac_exp, {d}) AS yac_exp,
           f.available_at
    FROM framed f
    LEFT JOIN ex ON ex.game_id = f.game_id AND ex.gsis_id = f.gsis_id
    WHERE {keep} AND f.available_at <= {sql_timestamp(cutoff)}
    ORDER BY f.season, f.week, f.game_id, f.gsis_id"""


def _typed(df: pl.DataFrame) -> pl.DataFrame:
    return df.cast(FRAME_SCHEMA).select(FRAME_COLUMNS)  # type: ignore[arg-type]


def empty_frame() -> pl.DataFrame:
    return pl.DataFrame(schema=FRAME_SCHEMA)


def player_games_for(
    view: SqlView,
    seasons: int | Sequence[int],
    *,
    rules: ScoringRules | None = None,
    positions: Sequence[str] | None = FANTASY_POSITIONS,
) -> pl.DataFrame:
    """The frame at the view's as-of: every player-game of ``seasons`` public by then
    (``available_at <= as_of``), sorted by (season, week, game_id, gsis_id). ``positions=None``
    also keeps players without a QB/RB/WR/TE point-in-time position (diagnostics)."""
    rules = rules or ScoringRules.from_config()
    wanted = [seasons] if isinstance(seasons, int) else list(seasons)
    if not [s for s in wanted if int(s) >= FIRST_SEASON]:
        return empty_frame()
    df = view.sql(frame_sql(wanted, to_utc(view.as_of), rules, input_lag(), positions))
    return _typed(df)


def player_games_asof(
    db: Path | str, season: int, week: int, *, rules: ScoringRules | None = None
) -> pl.DataFrame:
    """The frame of ``season`` at the official Tuesday as-of after regular-season ``week``:
    season to date, never a later game."""
    as_of = weekly_as_of(db, season, week)
    with AsOfView(db, as_of) as view:
        return player_games_for(view, season, rules=rules)


def player_games_history(
    db: Path | str,
    seasons: Sequence[int],
    *,
    as_of: datetime | None = None,
    rules: ScoringRules | None = None,
    positions: Sequence[str] | None = FANTASY_POSITIONS,
) -> pl.DataFrame:
    """Every player-game of ``seasons`` through ONE as-of view (default: the end of time, i.e.
    every built row), each with its ``available_at``. ``frame.filter(available_at <= t)`` equals
    :func:`player_games_for` at ``t`` for every ``t`` up to ``as_of``."""
    with AsOfView(db, as_of or END_OF_TIME) as view:
        return player_games_for(view, seasons, rules=rules, positions=positions)


def visible(frame: pl.DataFrame, as_of: datetime) -> pl.DataFrame:
    """The rows of a history frame public at ``as_of`` (the batch equivalent of an as-of)."""
    from twm.asof import asof_filter

    return asof_filter(frame, as_of)
