"""Cliff & Breakout Board (step I1b, PROJECT_SPEC 8.6): season-level fantasy points per game and
the end-of-season snapshot. docs/board.md explains every rule for a beginner.

**Points per game (PPG)** of a player-season = his regular-season fantasy points (the weekly
stat lines of ``fact_player_week`` scored with config/scoring.yaml, :func:`twm.scoring.score_sql`)
divided by his **games played** = his regular-season games with a stat line in
``fact_player_week`` (nflverse writes a line for every player with a recorded play: a game in
uniform without a touch, target, pass or fumble is not counted; that is why PPG here can be a
little higher than a site that counts every game on the field).

**Position** (point in time, never today's): his latest regular-season weekly-roster row of the
season (``fact_roster_week``, 2002 on), else the snap-count position of his last game of the
season (2013 on), else his latest roster row of the season before. 1999-2001 have no rosters:
those seasons only feed career totals and season-over-season trends, never a rank.

**Rank** within position (QB/RB/WR/TE): by PPG among the players with at least
:data:`MIN_GAMES_RANKED` games (spec 8.6), ties to more points, then the id. Fewer games: no rank.

**Snapshot** of season S (:func:`snapshot_as_of`): the moment every season-S row the board reads
is public, i.e. the latest ``available_at`` of the season's rows in :data:`SNAPSHOT_TABLES`
(after the Super Bowl with the project's lags: the Tuesday after it, 14:00 UTC, when its weekly
roster is public; a week later for 2002-2015, whose rosters are post-game snapshots).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from twm.config import FANTASY_POSITIONS
from twm.scoring import ScoringRules, score_sql

FIRST_SEASON = 1999  # play-by-play and weekly stat lines start here
FIRST_ROSTER_SEASON = 2002  # weekly rosters (point-in-time positions) start here
MIN_GAMES_RANKED = 8  # spec 8.6: a PPG rank needs at least 8 games
SNAPSHOT_TABLES = (
    "fact_player_week", "fact_team_week", "fact_snaps", "fact_opportunity_week",
    "fact_opportunity_pass", "fact_opportunity_rush", "fact_ngs_passing_week",
    "fact_ngs_rushing_week", "fact_ngs_receiving_week", "fact_game", "fact_roster_week",
)  # fmt: skip


class SeasonNotOverError(LookupError):
    """The season's Super Bowl has not been played (no end-of-season snapshot yet)."""


def snapshot_as_of(db: Path | str | duckdb.DuckDBPyConnection, season: int) -> datetime:
    """The end-of-season snapshot of ``season`` (aware UTC): see the module docstring."""
    con = db if isinstance(db, duckdb.DuckDBPyConnection) else duckdb.connect(str(db), True)
    try:
        sb = con.execute(
            "SELECT count(*) FROM fact_game WHERE season = ? AND game_type = 'SB' "
            "AND result IS NOT NULL",
            [int(season)],
        ).fetchone()
        if not sb or not sb[0]:
            raise SeasonNotOverError(f"the {season} Super Bowl has not been played")
        present = {r[0] for r in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
        parts = [
            f"SELECT max(available_at) AS a FROM {t} WHERE season = {int(season)}"
            for t in SNAPSHOT_TABLES
            if t in present
        ]
        row = con.execute(f"SELECT max(a) FROM ({' UNION ALL '.join(parts)})").fetchone()
    finally:
        if not isinstance(db, duckdb.DuckDBPyConnection):
            con.close()
    assert row is not None and row[0] is not None
    return row[0].replace(tzinfo=UTC)


def _player_seasons_sql(last_season: int, rules: ScoringRules) -> str:
    s = int(last_season)
    return f"""
    WITH pw AS (
        SELECT player_id AS gsis_id, season, week, game_id, team, {score_sql(rules)} AS points,
               COALESCE(carries, 0) AS carries, COALESCE(receptions, 0) AS receptions,
               COALESCE(targets, 0) AS targets, COALESCE(rushing_yards, 0) AS rushing_yards,
               COALESCE(receiving_yards, 0) AS receiving_yards,
               COALESCE(receiving_air_yards, 0) AS air_yards,
               COALESCE(attempts, 0) AS pass_attempts
        FROM fact_player_week
        WHERE season_type = 'REG' AND season <= {s} AND player_id IS NOT NULL
    ), tw AS (
        SELECT team, game_id, COALESCE(attempts, 0) AS team_pass_attempts,
               COALESCE(targets, 0) AS team_targets,
               COALESCE(receiving_air_yards, 0) AS team_air_yards,
               COALESCE(carries, 0) AS team_carries
        FROM fact_team_week WHERE season_type = 'REG' AND season <= {s}
    ), ps AS (
        SELECT pw.gsis_id, pw.season, count(*) AS games, sum(points) AS points,
               sum(carries) AS carries, sum(receptions) AS receptions, sum(targets) AS targets,
               sum(rushing_yards) AS rushing_yards, sum(receiving_yards) AS receiving_yards,
               sum(air_yards) AS air_yards, sum(pass_attempts) AS pass_attempts,
               sum(tw.team_pass_attempts) AS team_pass_attempts,
               sum(tw.team_targets) AS team_targets, sum(tw.team_air_yards) AS team_air_yards,
               sum(tw.team_carries) AS team_carries,
               arg_max(pw.team, pw.week) AS team
        FROM pw LEFT JOIN tw ON tw.team = pw.team AND tw.game_id = pw.game_id
        GROUP BY pw.gsis_id, pw.season
    ), ros AS (
        SELECT gsis_id, season, arg_max(position, week) AS position,
               arg_max(entry_year, week) AS entry_year
        FROM fact_roster_week
        WHERE season_type = 'REG' AND season <= {s} AND gsis_id IS NOT NULL
          AND position IS NOT NULL
        GROUP BY gsis_id, season
    ), sn AS (
        SELECT gsis_id, season, arg_max(position, week) AS position
        FROM fact_snaps
        WHERE game_type = 'REG' AND season <= {s} AND gsis_id IS NOT NULL
          AND position IS NOT NULL
        GROUP BY gsis_id, season
    )
    SELECT ps.*, COALESCE(r.position, sn.position, rp.position) AS position,
           CASE WHEN r.position IS NOT NULL THEN 'roster' WHEN sn.position IS NOT NULL
                THEN 'snaps' WHEN rp.position IS NOT NULL THEN 'roster_previous_season'
           END AS position_source,
           COALESCE(r.entry_year, rp.entry_year) AS entry_year
    FROM ps
    LEFT JOIN ros r ON r.gsis_id = ps.gsis_id AND r.season = ps.season
    LEFT JOIN sn ON sn.gsis_id = ps.gsis_id AND sn.season = ps.season
    LEFT JOIN ros rp ON rp.gsis_id = ps.gsis_id AND rp.season = ps.season - 1
    ORDER BY ps.season, ps.gsis_id
    """


def add_ranks(seasons: pl.DataFrame, min_games: int = MIN_GAMES_RANKED) -> pl.DataFrame:
    """``ppg`` and ``pos_rank`` (1 = best PPG at the position in the season among players with
    at least ``min_games`` games; NULL otherwise or outside QB/RB/WR/TE)."""
    df = seasons.with_columns((pl.col("points") / pl.col("games")).alias("ppg"))
    ok = (pl.col("games") >= min_games) & pl.col("position").is_in(list(FANTASY_POSITIONS))
    ranked = (
        df.filter(ok)
        .sort(
            ["season", "position", "ppg", "points", "gsis_id"],
            descending=[False, False, True, True, False],
        )  # fmt: skip
        .with_columns(
            (pl.int_range(pl.len()).over(["season", "position"]) + 1).cast(pl.Int32).alias("r")
        )
        .select("gsis_id", "season", pl.col("r").alias("pos_rank"))
    )
    return df.join(ranked, on=["gsis_id", "season"], how="left").sort("season", "gsis_id")


def player_seasons(view: Any, last_season: int, rules: ScoringRules | None = None) -> pl.DataFrame:
    """One row per player-season 1999 .. ``last_season`` (regular season) as visible in
    ``view`` (an :class:`~twm.asof.AsOfView`): games, points, PPG, rank, usage totals, the
    team sums over the games he played, his last team (``team``), point-in-time position."""
    rules = rules or ScoringRules.from_config()
    df = view.sql(_player_seasons_sql(last_season, rules))
    ints = ("games", "carries", "receptions", "targets", "rushing_yards", "receiving_yards",
            "air_yards", "pass_attempts", "team_pass_attempts", "team_targets",
            "team_air_yards", "team_carries")  # fmt: skip
    df = df.with_columns(
        pl.col("season").cast(pl.Int32),
        *(pl.col(c).cast(pl.Int64) for c in ints),
        pl.col("points").cast(pl.Float64).round(6),
        pl.col("entry_year").cast(pl.Int32),
    )
    return add_ranks(df)


def team_seasons(view: Any, last_season: int) -> pl.DataFrame:
    """Per team-season (regular season): adjusted net yards per pass attempt, the QB-quality
    measure (ANY/A = (passing yards + 20 x TD - 45 x INT - sack yards) / (attempts + sacks),
    Pro Football Reference's formula; no model column)."""
    return view.sql(f"""
        SELECT team, season,
               (sum(passing_yards) + 20 * sum(passing_tds) - 45 * sum(passing_interceptions)
                - sum(sack_yards_lost)) / NULLIF(sum(attempts) + sum(sacks_suffered), 0)
                   AS team_any_a
        FROM fact_team_week WHERE season_type = 'REG' AND season <= {int(last_season)}
        GROUP BY team, season ORDER BY season, team""").with_columns(
        pl.col("season").cast(pl.Int32), pl.col("team_any_a").cast(pl.Float64).round(6)
    )
