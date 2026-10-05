"""The weekly ranks mapped to the week they precede, with each player's league points.

``fact_ranking`` has no week column: a weekly snapshot (``ecr_type`` 'wp', pages
weekly-qb/-rb/-wr/-te; 'weekly-offense' 2019-2020 and 'redraft-offense' are ignored) belongs to
the regular-season week whose ``dim_week`` window holds its ``available_at`` (after the
previous week's Tuesday as-of, at or before this week's; the window only: an AsOfView hides a
future week's kickoff columns). Snapshots are scraped Fridays, after the week's Thursday game:
a row whose team kicked off before the snapshot was available is not a valid start/sit row (the
game was already on).
When two snapshots precede the same week, a player's newest valid one is used.
"""

from __future__ import annotations

from collections.abc import Sequence

import duckdb
import polars as pl

from twm.scoring import score_sql
from twm.warehouse.schema import RANKING_TEAM_ALIASES, TEAM_ALIASES

POSITIONS = ("QB", "RB", "WR", "TE")
PAGES = ("weekly-qb", "weekly-rb", "weekly-wr", "weekly-te")
_PAGES_SQL = ", ".join(f"'{p}'" for p in PAGES)


def _team_sql(col: str) -> str:
    aliases = {**TEAM_ALIASES, **RANKING_TEAM_ALIASES}
    whens = " ".join(f"WHEN '{a}' THEN '{b}'" for a, b in sorted(aliases.items()))
    return f"CASE {col} {whens} WHEN 'FA' THEN NULL ELSE {col} END"


def ranks_sql(seasons: Sequence[int], *, only_valid: bool = True) -> str:
    """One row per (season, week, pos, fantasypros_id): the player's newest weekly rank available
    before his team's kickoff that week (``valid``). Plain warehouse table names, so it runs on
    a read-only warehouse connection and inside an :class:`twm.asof.AsOfView` alike. With
    ``only_valid`` False a player whose game was already on keeps his newest row, ``valid``
    False (the CLI says why it cannot compare him)."""
    ss = ", ".join(str(int(s)) for s in seasons)
    valid = "(g.kickoff_utc IS NOT NULL AND r.available_at < g.kickoff_utc)"
    return f"""
    WITH snaps AS (
        SELECT DISTINCT season, scrape_date, available_at FROM fact_ranking
        WHERE ecr_type = 'wp' AND page_kind = 'weekly' AND page_type IN ({_PAGES_SQL})
          AND season IN ({ss})
    ), snap_week AS (
        SELECT s.season, s.scrape_date, s.available_at, w.week FROM snaps s
        JOIN dim_week w ON w.season = s.season AND w.season_type = 'REG'
         AND s.available_at > coalesce(w.window_start_utc, TIMESTAMP '1900-01-01')
         AND s.available_at <= w.window_end_utc
    ), ranks AS (
        SELECT sw.season, sw.week, sw.scrape_date, sw.available_at, r.page_pos AS pos,
               r.fantasypros_id, r.gsis_id, r.player, {_team_sql("r.team")} AS team,
               r.pos_rank, r.ecr, r.sd
        FROM fact_ranking r
        JOIN snap_week sw ON sw.season = r.season AND sw.scrape_date = r.scrape_date
        WHERE r.ecr_type = 'wp' AND r.page_kind = 'weekly' AND r.page_type IN ({_PAGES_SQL})
          AND r.pos_rank IS NOT NULL
    ), games AS (
        SELECT season, week, home_team AS team, kickoff_utc, game_id FROM fact_schedule
        WHERE season_type = 'REG' AND season IN ({ss})
        UNION ALL
        SELECT season, week, away_team AS team, kickoff_utc, game_id FROM fact_schedule
        WHERE season_type = 'REG' AND season IN ({ss})
    )
    SELECT r.*, g.kickoff_utc, g.game_id, {valid} AS valid FROM ranks r
    JOIN games g ON g.season = r.season AND g.week = r.week AND g.team = r.team
    WHERE {valid if only_valid else "TRUE"}
    QUALIFY row_number() OVER (PARTITION BY r.season, r.week, r.pos, r.fantasypros_id
                               ORDER BY {valid} DESC, r.available_at DESC) = 1
    """


def history(con: duckdb.DuckDBPyConnection, seasons: Sequence[int]) -> pl.DataFrame:
    """The valid rank rows of ``seasons`` with the week's league points (``points``; 0 when the
    player has no stat line that week: ``played`` False). Hindsight labels: training and
    backtest only, never a live list."""
    sql = f"""
    WITH v AS ({ranks_sql(seasons)})
    SELECT v.season, v.week, v.pos, v.fantasypros_id, v.gsis_id, v.team, v.game_id,
           v.pos_rank, v.ecr, v.sd, p.player_id IS NOT NULL AS played,
           {score_sql(table_alias="p")} AS points
    FROM v LEFT JOIN fact_player_week p
      ON p.player_id = v.gsis_id AND p.season = v.season AND p.week = v.week
     AND p.season_type = 'REG'
    WHERE v.gsis_id IS NOT NULL
    ORDER BY v.season, v.week, v.pos, v.pos_rank, v.fantasypros_id
    """
    return con.sql(sql).pl()
