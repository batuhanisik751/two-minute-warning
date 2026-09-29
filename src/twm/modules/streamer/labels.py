"""The streamer's label (S1b): did a pooled kicker or D/ST finish as a weekly starter NEXT week?

Streaming is a one-week decision: at the Tuesday as-of after week N you pick up a kicker or a
team defense for week N+1 only. So every pool row (:mod:`twm.modules.streamer.pool`) gets:

- ``label_week`` = N+1, the next regular-season week. ``y_start`` = the entity's fantasy points
  (config/scoring.yaml ``kicking:`` / ``defense:``) that week rank in the top (teams x lineup
  slots) at its position: K 12 and DST 12 in the default 12-team league
  (:attr:`StreamerRules.start_thresholds`). ``y_start`` is NULL when there is nothing to label:
- **bye**: the entity's as-of team has no regular-season game in week N+1 (``fact_game``; the
  2022 BUF-CIN game that was never finished is not in the schedule data, so both teams count
  as on a bye in 2022 week 17). You would not stream a team on its bye.
- **season_end**: week N is the last regular-season week (``dim_week.is_last_reg_week``).
- **pending**: some game of week N+1 has no final score or no stat lines in the cache yet
  (the Radar's :func:`~twm.modules.waiver_radar.labels.week_status`: a rank compares everyone
  who played that week, so a missing game could change it). Never guessed.

**Who is ranked** in week N+1: every team with a ``fact_defense_week`` row (DST); every player
with a ``fact_kicker_week`` row (a field-goal or extra-point attempt) whose roster position that
week (``fact_roster_week``; else his latest earlier roster week of the season) is K, or who has
no roster row at all. So a punter who kicked two extra points for a hurt kicker is not ranked
among the kickers. Points are rounded to 6 decimals, then ranked with SQL rank() semantics
(ties share the better rank): **ties at the cutoff all count as starts** (two kickers tied for
12th are both rank 12), so a week can have more than 12 starts.

**The entity's own week**: a DST's row is its as-of team's game. A kicker's is his own kicking
row that week, for any team (a kicker signed elsewhere keeps counting); a kicker without one
(inactive, cut, or no attempt at all) did not help a fantasy team: ``played_next`` False,
``y_start`` False (not NULL: picking him was a miss).

Point in time: labels are hindsight by design, so they read the warehouse directly, but every
outcome row passes :func:`twm.asof.outcomes_after` for its as-of (public strictly after it), and
the label week is chosen by week number, so a week-N game moved past the as-of (a split week)
belongs to week N, never to the label (tests/test_streamer_labels.py).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import duckdb
import polars as pl

from twm.asof import outcomes_after
from twm.modules.streamer.pool import StreamerRules
from twm.modules.waiver_radar.labels import (
    POINTS_DECIMALS,
    _connect,
    _in,
    _team_weeks,
    last_reg_weeks,
    week_status,
)
from twm.scoring_kdst import score_defense_sql, score_kicking_sql

LABEL_STATUSES = ("final", "pending", "bye", "season_end")
# Columns label_rows() adds to the pool rows, in order.
LABEL_COLUMNS = (
    "label_week", "label_status", "played_next", "next_points", "next_pos_rank", "n_ranked",
    "y_start",
)  # fmt: skip
REQUIRED_POOL_COLUMNS = ("season", "week", "as_of", "position", "entity_id", "gsis_id", "team")
FINISH_COLUMNS = ("position", "season", "week", "key", "team", "points", "available_at")


def _finishes_sql(seasons: Sequence[int], rules: StreamerRules) -> str:
    """Every regular-season K and DST line of ``seasons`` that can be ranked (see module doc)."""
    s = _in(seasons)
    kp = score_kicking_sql(rules.kicking, table_alias="k")
    dp = score_defense_sql(rules.defense, table_alias="d")
    return f"""
    WITH k AS (
        SELECT k.season, k.week, k.player_id AS key, k.team,
               round({kp}, {POINTS_DECIMALS}) AS points, k.available_at
        FROM fact_kicker_week k WHERE k.season_type = 'REG' AND k.season IN ({s})
    ),
    ros AS (
        SELECT season, week, gsis_id, position FROM fact_roster_week
        WHERE season IN ({s}) AND gsis_id IS NOT NULL AND position IS NOT NULL
    ),
    pos AS (
        -- his roster position that week, else his latest earlier roster week of the season
        SELECT k.season, k.week, k.key, r.position FROM k JOIN ros r
          ON r.season = k.season AND r.gsis_id = k.key AND r.week <= k.week
        QUALIFY row_number() OVER (PARTITION BY k.season, k.week, k.key
                                   ORDER BY r.week DESC, r.position) = 1
    )
    SELECT 'K' AS position, k.season, k.week, k.key, k.team, k.points, k.available_at
    FROM k LEFT JOIN pos p USING (season, week, key)
    WHERE p.position IS NULL OR p.position = 'K'
    UNION ALL
    SELECT 'DST', d.season, d.week, d.team, d.team, round({dp}, {POINTS_DECIMALS}),
           d.available_at
    FROM fact_defense_week d WHERE d.season_type = 'REG' AND d.season IN ({s})"""


def weekly_finishes(
    con: duckdb.DuckDBPyConnection, seasons: Sequence[int], rules: StreamerRules
) -> pl.DataFrame:
    """The rankable K and DST lines (``FINISH_COLUMNS``), unranked: ranks depend on the as-of
    (only rows public after it count), so :func:`label_rows` ranks per as-of."""
    df = con.execute(_finishes_sql(seasons, rules)).pl()
    return df.select(list(FINISH_COLUMNS)).cast({"season": pl.Int32, "week": pl.Int32})


LABEL_SCHEMA = {
    "label_week": pl.Int32(),
    "label_status": pl.String(),
    "played_next": pl.Boolean(),
    "next_points": pl.Float64(),
    "next_pos_rank": pl.Int32(),
    "n_ranked": pl.Int32(),
    "y_start": pl.Boolean(),
}


def _label_group(
    grp: pl.DataFrame,
    season: int,
    week: int,
    as_of: object,
    *,
    last_week: int | None,
    is_final: bool,
    teams_playing: set[str],
    finishes: pl.DataFrame,
    rules: StreamerRules,
) -> pl.DataFrame:
    """The labels of one as-of's pool rows (see the module docstring)."""
    if last_week is None or week >= last_week:
        return grp.with_columns(
            pl.lit("season_end").alias("label_status"),
            *[
                pl.lit(None, dtype=t).alias(c)
                for c, t in LABEL_SCHEMA.items()
                if c != "label_status"
            ],
        ).select(*grp.columns, *LABEL_COLUMNS)
    lw = week + 1
    rows = outcomes_after(finishes.filter(pl.col("season") == season, pl.col("week") == lw), as_of)  # type: ignore[arg-type]
    ranked = rows.select(
        "position",
        "key",
        "points",
        pl.col("points").rank("min", descending=True).over("position").cast(pl.Int32).alias("rank"),
        pl.len().over("position").cast(pl.Int32).alias("n"),
    )
    n_by_pos = ranked.group_by("position").agg(pl.col("n").first())
    threshold = pl.col("position").replace_strict(rules.start_thresholds, default=None)
    df = (
        grp.with_columns(
            pl.when(pl.col("position") == "K")
            .then(pl.col("gsis_id"))
            .otherwise(pl.col("team"))
            .alias("_key"),
            pl.when(~pl.col("team").is_in(sorted(teams_playing)))
            .then(pl.lit("bye"))
            .when(pl.lit(is_final))
            .then(pl.lit("final"))
            .otherwise(pl.lit("pending"))
            .alias("label_status"),
        )
        .join(
            ranked.drop("n"), left_on=["position", "_key"], right_on=["position", "key"], how="left"
        )
        .join(n_by_pos, on="position", how="left")
    )
    final = pl.col("label_status") == "final"
    return df.with_columns(
        pl.lit(lw, dtype=pl.Int32).alias("label_week"),
        pl.when(final).then(pl.col("points").is_not_null()).alias("played_next"),
        pl.when(final).then(pl.col("points")).alias("next_points"),
        pl.when(final).then(pl.col("rank")).alias("next_pos_rank"),
        pl.when(final).then(pl.col("n").fill_null(0)).alias("n_ranked"),
        pl.when(final).then((pl.col("rank") <= threshold).fill_null(False)).alias("y_start"),
    ).select(*grp.columns, *LABEL_COLUMNS)


def label_rows(
    con_or_db: Path | str | duckdb.DuckDBPyConnection,
    pool: pl.DataFrame,
    rules: StreamerRules | None = None,
) -> pl.DataFrame:
    """``pool`` (pool rows of any as-ofs) with ``LABEL_COLUMNS`` appended, in the same order."""
    missing = [c for c in REQUIRED_POOL_COLUMNS if c not in pool.columns]
    if missing:
        raise ValueError(f"pool lacks columns {missing}")
    rules = rules or StreamerRules.from_config()
    if pool.height == 0:
        return pool.with_columns(*[pl.lit(None, dtype=t).alias(c) for c, t in LABEL_SCHEMA.items()])
    seasons = sorted(int(s) for s in pool.get_column("season").unique())
    con, close = _connect(con_or_db)
    try:
        last = last_reg_weeks(con, seasons)
        final = {
            (int(r["season"]), int(r["week"])): bool(r["is_week_final"])
            for r in week_status(con, seasons).iter_rows(named=True)
        }
        team_weeks = _team_weeks(con, seasons)
        finishes = weekly_finishes(con, seasons, rules)
    finally:
        if close:
            con.close()
    playing: dict[tuple[int, int], set[str]] = {}
    for r in team_weeks.iter_rows(named=True):
        playing.setdefault((int(r["season"]), int(r["week"])), set()).add(r["team"])
    indexed = pool.with_row_index("_row")
    out = []
    for (season, week, as_of), grp in indexed.group_by(
        ["season", "week", "as_of"], maintain_order=True
    ):
        s, w = int(season), int(week)  # type: ignore[call-overload]
        out.append(
            _label_group(
                grp,
                s,
                w,
                as_of,
                last_week=last.get(s),
                is_final=final.get((s, w + 1), False),
                teams_playing=playing.get((s, w + 1), set()),
                finishes=finishes,
                rules=rules,
            )
        )
    return pl.concat(out, how="vertical").sort("_row").drop("_row")
