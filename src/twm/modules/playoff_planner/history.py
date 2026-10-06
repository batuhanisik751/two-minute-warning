"""The warehouse rows of the playoff planner (docs/playoff_planner.md, "Data and definitions").

- **unit-game**: one fantasy unit's points in one regular-season game: a QB / RB / WR / TE
  (``fact_player_week`` scored with config/scoring.yaml, PPR), a kicker (``fact_kicker_week``,
  :func:`twm.scoring_kdst.score_kicking_sql`) or a team's D/ST (``fact_defense_week``,
  :func:`twm.scoring_kdst.score_defense_sql`; ``pid`` = the team).
- **position**: the point-in-time ``fact_roster_week.position`` of that week (HB / FB are
  running backs), else his most common roster position of the season; ``fact_player_week``'s
  own ``position`` is a today's-snapshot column and is not used.
- **team-game**: a ``fact_defense_week`` row (team, opponent_team): every played game has two.
- **opponent**: the unit's ``opponent_team``. A rating is always about the opponent: the
  defense a QB / RB / WR / TE / K faces, or the offense a D/ST faces.

The backtest reads the warehouse directly (outcomes, filtered by week: nothing after the as-of
week enters a rating or a base); the live grid reads the same SQL through the as-of view.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import duckdb

FIRST_SEASON = 2013  # the first test season (docs/playoff_planner.md)
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")
SKILL = ("QB", "RB", "WR", "TE")
DECIMALS = 6


def _seasons(seasons: Sequence[int]) -> str:
    return ", ".join(str(int(s)) for s in seasons)


def units_sql(seasons: Sequence[int]) -> str:
    """Every unit-game of ``seasons``: season, week, game_id, team, opponent, position, pid,
    player, points."""
    from twm.scoring import score_sql
    from twm.scoring_kdst import score_defense_sql, score_kicking_sql

    s = _seasons(seasons)
    pos = ("CASE WHEN {c} IN ('RB', 'HB', 'FB') THEN 'RB' WHEN {c} IN ('QB', 'WR', 'TE') "
           "THEN {c} END")  # fmt: skip
    return f"""
    WITH ro AS (SELECT season, week, gsis_id, min({pos.format(c="position")}) AS p
                FROM fact_roster_week WHERE season_type = 'REG' AND season IN ({s})
                  AND gsis_id IS NOT NULL GROUP BY ALL),
    rs AS (SELECT season, gsis_id, mode(p) AS p FROM ro WHERE p IS NOT NULL GROUP BY ALL),
    sk AS (SELECT f.season, f.week, f.game_id, f.team, f.opponent_team AS opponent,
                  coalesce(ro.p, rs.p) AS position, f.player_id AS pid,
                  f.player_display_name AS player, round({score_sql(table_alias="f")}, {DECIMALS})
                  AS points
           FROM fact_player_week f
           LEFT JOIN ro ON ro.season = f.season AND ro.week = f.week AND ro.gsis_id = f.player_id
           LEFT JOIN rs ON rs.season = f.season AND rs.gsis_id = f.player_id
           WHERE f.season_type = 'REG' AND f.season IN ({s}) AND f.player_id IS NOT NULL),
    k AS (SELECT season, week, game_id, team, opponent_team AS opponent, 'K' AS position,
                 player_id AS pid, player_display_name AS player,
                 round({score_kicking_sql()}, {DECIMALS}) AS points
          FROM fact_kicker_week WHERE season_type = 'REG' AND season IN ({s})
            AND player_id IS NOT NULL),
    d AS (SELECT season, week, game_id, team, opponent_team AS opponent, 'DST' AS position,
                 team AS pid, team || ' D/ST' AS player,
                 round({score_defense_sql()}, {DECIMALS}) AS points
          FROM fact_defense_week WHERE season_type = 'REG' AND season IN ({s}))
    SELECT * FROM sk WHERE position IS NOT NULL
    UNION ALL SELECT * FROM k UNION ALL SELECT * FROM d
    ORDER BY season, week, team, position, pid"""


def team_games_sql(seasons: Sequence[int]) -> str:
    """Every played team-game of ``seasons``: season, week, game_id, team, opponent."""
    return f"""
    SELECT DISTINCT season, week, game_id, team, opponent_team AS opponent
    FROM fact_defense_week WHERE season_type = 'REG' AND season IN ({_seasons(seasons)})
    ORDER BY season, week, team"""


def connect(db: Path | str) -> duckdb.DuckDBPyConnection:
    """The warehouse, read-only (history rows are outcomes; never the as-of view)."""
    con = duckdb.connect(str(db), read_only=True)
    con.execute("SET TimeZone='UTC'")
    return con
