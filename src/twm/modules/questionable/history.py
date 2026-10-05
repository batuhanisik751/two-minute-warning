"""The history rows of the Questionable table (docs/questionable.md, "Data and definitions").

One row per tagged player-week: a QB/RB/WR/TE whose team's final injury report gave him the
game status Questionable or Doubtful (``fact_injury_report.report_status``) for a
regular-season week his team played, from :data:`FIRST_SEASON` on. Out rows are counted
(:func:`out_counts`) but never modelled: Out means he does not play.

- **played**: at least one offensive snap in that week's game (``fact_snaps.offense_snaps``
  > 0; PFR lists only players who took a snap). ``fact_roster_week.status`` 'ACT' is NOT
  game-day active, so it is not used.
- **practice**: the final practice status of the week, bucketed by :func:`practice_bucket`.
- **missed_prev**: no offensive snap in his team's previous regular-season game of the
  season while he was on that team's weekly roster (False in a team's first game).
- **body_part**: the report's primary injury, side words removed, :data:`BODY_PARTS` or
  'other'.

Sums are rounded to 6 decimals in SQL: DuckDB's parallel float sums may differ in the last bit
between runs, which would move a row across the 5-point floor of :mod:`.plays`.

These are outcomes (labels): the module reads the warehouse directly, never through the as-of
view, and the backtest trains only on seasons before the one it scores.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import duckdb
import polars as pl

FIRST_SEASON = 2016  # the NFL dropped "Probable" and redefined "Questionable" in 2016
POSITIONS = ("QB", "RB", "WR", "TE")
MODELLED = ("Questionable", "Doubtful")
STATUSES = (*MODELLED, "Out")
PRACTICE = ("full", "limited", "dnp", "none")
# The 8 most frequent primary injuries of tagged QB/RB/WR/TE 2016-2025 (counted once, before
# any backtest; the outcome was not looked at); everything else is 'other'.
BODY_PARTS = ("knee", "ankle", "hamstring", "shoulder", "foot", "illness", "groin", "hip")
_SIDES = ("right ", "left ")
_PLURALS = {"ribs": "rib", "hips": "hip", "ankles": "ankle", "quadriceps": "quadricep"}


def practice_bucket(status: str | None) -> str:
    """'full' / 'limited' / 'dnp' / 'none' from ``fact_injury_report.practice_status``:
    'Full Participation in Practice', 'Limited Participation in Practice', 'Did Not
    Participate In Practice' (and 'Out (Definitely Will Not Play)': did not practice);
    blank, NULL and 'Note' are 'none'."""
    s = (status or "").strip().lower()
    if s.startswith("full"):
        return "full"
    if s.startswith("limited"):
        return "limited"
    if s.startswith("did not") or s.startswith("out"):
        return "dnp"
    return "none"


def body_part(injury: str | None) -> str:
    """The primary injury reduced to one of :data:`BODY_PARTS` or 'other' (side words and
    plurals removed: 'Right Shoulder' -> 'shoulder', 'Ribs' -> 'rib' -> 'other')."""
    s = (injury or "").strip().lower()
    for side in _SIDES:
        s = s.removeprefix(side)
    s = _PLURALS.get(s, s)
    return s if s in BODY_PARTS else "other"


def _in(values: Sequence[object]) -> str:
    """An SQL IN list of constants (strings quoted; only this module's own values)."""
    return ", ".join(f"'{v}'" if isinstance(v, str) else str(int(str(v))) for v in values)


def _common_ctes(seasons: Sequence[int]) -> str:
    """team games (one row per team: its game, opponent, kickoff and previous game's week),
    snaps (one row per player-week), points (config/scoring.yaml) and played player-weeks."""
    from twm.scoring import score_sql

    s = _in(seasons)
    return f"""
    sched AS (
        SELECT game_id, season, week, kickoff_utc, home_team AS team, away_team AS opponent
        FROM fact_schedule WHERE season_type = 'REG' AND season IN ({s})
        UNION ALL
        SELECT game_id, season, week, kickoff_utc, away_team AS team, home_team AS opponent
        FROM fact_schedule WHERE season_type = 'REG' AND season IN ({s})
    ),
    tg AS (SELECT *, lag(week) OVER (PARTITION BY season, team ORDER BY week) AS prev_week
           FROM sched),
    snap AS (SELECT season, week, gsis_id, max(offense_snaps) AS snaps,
                    min(position) AS snap_position
             FROM fact_snaps WHERE game_type = 'REG' AND gsis_id IS NOT NULL
               AND season IN ({s}) GROUP BY ALL),
    pts AS (SELECT season, week, player_id AS gsis_id, round(sum({score_sql()}), 6) AS points
            FROM fact_player_week WHERE season_type = 'REG' AND season IN ({s}) GROUP BY ALL),
    pw AS (SELECT n.season, n.week, n.gsis_id, n.snap_position, coalesce(p.points, 0) AS points
           FROM snap n LEFT JOIN pts p USING (season, week, gsis_id) WHERE n.snaps > 0)"""


def tagged_sql(
    seasons: Sequence[int],
    statuses: Sequence[str] = STATUSES,
    injury_source: str = "fact_injury_report",
) -> str:
    """One row per tagged QB/RB/WR/TE player-week whose team has a regular-season game that
    week (module docstring). ``injury_source`` is a table or a parenthesized subquery with
    fact_injury_report's columns (the live list passes the rows it may see)."""
    s = _in(seasons)
    return f"""
    WITH {_common_ctes(seasons)},
    inj AS (
        SELECT season, week, team, gsis_id, position, full_name, report_status,
               practice_status, report_primary_injury
        FROM {injury_source} AS src
        WHERE season_type = 'REG' AND season IN ({s}) AND gsis_id IS NOT NULL
          AND position IN ({_in(POSITIONS)}) AND report_status IN ({_in(statuses)})
        QUALIFY row_number() OVER (PARTITION BY season, week, gsis_id
                                   ORDER BY date_modified DESC NULLS LAST, team) = 1
    ),
    ros AS (SELECT DISTINCT season, week, team, gsis_id FROM fact_roster_week
            WHERE season_type = 'REG' AND season IN ({s})),
    prior AS (SELECT i.season, i.week, i.gsis_id, count(pw.week) AS prior_games,
                     round(coalesce(sum(pw.points), 0), 6) AS prior_points
              FROM inj i LEFT JOIN pw ON pw.season = i.season AND pw.gsis_id = i.gsis_id
                                     AND pw.week < i.week
              GROUP BY ALL)
    SELECT i.season, i.week, i.team, g.opponent, g.game_id, g.kickoff_utc, i.gsis_id,
           i.full_name AS player, i.position, i.report_status, i.practice_status,
           i.report_primary_injury,
           coalesce(n.snaps, 0) > 0 AS played,
           (g.prev_week IS NOT NULL AND r.gsis_id IS NOT NULL
            AND coalesce(pn.snaps, 0) = 0) AS missed_prev,
           CASE WHEN coalesce(n.snaps, 0) > 0 THEN coalesce(pt.points, 0) END AS points,
           pr.prior_games, pr.prior_points
    FROM inj i
    JOIN tg g ON g.season = i.season AND g.week = i.week AND g.team = i.team
    LEFT JOIN snap n ON n.season = i.season AND n.week = i.week AND n.gsis_id = i.gsis_id
    LEFT JOIN ros r ON r.season = i.season AND r.week = g.prev_week AND r.team = i.team
                   AND r.gsis_id = i.gsis_id
    LEFT JOIN snap pn ON pn.season = i.season AND pn.week = g.prev_week
                     AND pn.gsis_id = i.gsis_id
    LEFT JOIN pts pt ON pt.season = i.season AND pt.week = i.week AND pt.gsis_id = i.gsis_id
    JOIN prior pr ON pr.season = i.season AND pr.week = i.week AND pr.gsis_id = i.gsis_id
    ORDER BY i.season, i.week, i.team, i.gsis_id"""


def played_sql(seasons: Sequence[int]) -> str:
    """Every QB/RB/WR/TE player-week with an offensive snap (PFR's game position), his
    points, his earlier played games and points that season, and whether he was on any injury
    report that week (the healthy baseline of "if he plays" is the rows with ``on_report``
    False)."""
    s = _in(seasons)
    return f"""
    WITH {_common_ctes(seasons)},
    rep AS (SELECT DISTINCT season, week, gsis_id FROM fact_injury_report
            WHERE season_type = 'REG' AND season IN ({s}) AND gsis_id IS NOT NULL),
    prior AS (SELECT a.season, a.week, a.gsis_id, count(b.week) AS prior_games,
                     round(coalesce(sum(b.points), 0), 6) AS prior_points
              FROM pw a LEFT JOIN pw b ON b.season = a.season AND b.gsis_id = a.gsis_id
                                      AND b.week < a.week
              GROUP BY ALL)
    SELECT a.season, a.week, a.gsis_id, a.snap_position AS position, a.points,
           pr.prior_games, pr.prior_points, r.gsis_id IS NOT NULL AS on_report
    FROM pw a JOIN prior pr USING (season, week, gsis_id)
    LEFT JOIN rep r USING (season, week, gsis_id)
    WHERE a.snap_position IN ({_in(POSITIONS)})
    ORDER BY a.season, a.week, a.gsis_id"""


def with_buckets(df: pl.DataFrame) -> pl.DataFrame:
    s = pl.String
    return df.with_columns(
        pl.col("practice_status").fill_null("").map_elements(practice_bucket, return_dtype=s)
        .alias("practice"),
        pl.col("report_primary_injury").fill_null("").map_elements(body_part, return_dtype=s)
        .alias("body_part"),
        (pl.col("prior_points") / pl.col("prior_games")).alias("prior_ppg"),
    )  # fmt: skip


def read_tagged(
    con: duckdb.DuckDBPyConnection, seasons: Sequence[int], statuses: Sequence[str] = STATUSES
) -> pl.DataFrame:
    """:func:`tagged_sql` on an open warehouse connection, with the practice bucket, the
    body part and the season-to-date points per game (``prior_ppg``) added."""
    df = con.execute(tagged_sql(seasons, statuses)).pl()
    return with_buckets(df)


def read_played(con: duckdb.DuckDBPyConnection, seasons: Sequence[int]) -> pl.DataFrame:
    df = con.execute(played_sql(seasons)).pl()
    return df.with_columns((pl.col("prior_points") / pl.col("prior_games")).alias("prior_ppg"))


def connect(db: Path | str) -> duckdb.DuckDBPyConnection:
    """The warehouse, read-only (history rows are outcomes; never the as-of view)."""
    con = duckdb.connect(str(db), read_only=True)
    con.execute("SET TimeZone='UTC'")
    return con


def out_counts(tagged: pl.DataFrame) -> pl.DataFrame:
    """Rows and played share by game status (Out included: reported, not modelled)."""
    return (tagged.group_by("report_status")
            .agg(pl.len().alias("n"), pl.col("played").mean().alias("played_rate"))
            .sort("report_status"))  # fmt: skip
