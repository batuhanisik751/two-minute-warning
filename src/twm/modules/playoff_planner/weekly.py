"""The live playoff grid, its snapshots and their grading (docs/playoff_planner.md, "Live").

As of a time, through the as-of view: the season's unit-games of the **completed weeks** (a
week is complete when each of its games is in the warehouse, or kicked off more than
:data:`CANCELLED_AFTER` before the as-of and never arrived: a cancelled game) and the schedule
of the fantasy playoff weeks (:data:`WEEKS`, configurable). For each NFL team, week and
position: the opponent, the opponent's ratings with the pinned spec and the position's chosen
candidate (``rating``; 1.0 where the rule chose 'none') and its rank among the 32 teams (1 =
the easiest matchup). A bye week has no opponent and no rating.

Snapshots: each run is stored in the predictions store's ``playoff_planner_snapshots`` table
keyed by its as-of, append-only; a completed week already stored for the season is not stored
again (one snapshot per completed week). Grading needs only the stored (or published) rows and
the warehouse: :func:`read_actuals` and :func:`grade` (each team-week-position's last snapshot
rated before that week; realized multiplier = the unit's points in that game over the
snapshot's league average).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from twm.modules.playoff_planner import history as hs
from twm.modules.playoff_planner import ratings as rt

TABLE = "playoff_planner_snapshots"
WEEKS = (15, 16, 17)
CANCELLED_AFTER = timedelta(days=3)
COLUMNS: dict[str, str] = {
    "as_of": "TIMESTAMP NOT NULL",  # naive UTC
    "season": "INTEGER NOT NULL",
    "through_week": "INTEGER NOT NULL",  # the last completed week in the ratings
    "week": "INTEGER NOT NULL",  # a fantasy playoff week (an NFL regular-season week)
    "team": "VARCHAR NOT NULL",
    "opponent": "VARCHAR",  # NULL: bye
    "home": "BOOLEAN",
    "game_id": "VARCHAR",
    "kickoff_utc": "TIMESTAMP",
    "position": "VARCHAR NOT NULL",  # QB / RB / WR / TE / K / DST
    "candidate": "VARCHAR NOT NULL",  # the position's chosen candidate
    "rating": "DOUBLE",  # the chosen multiplier (1.0 for 'none'; NULL: bye)
    "rating_rank": "INTEGER",  # 1 = the easiest of the 32 opponents (NULL: 'none' or bye)
    "raw": "DOUBLE",
    "shrunk": "DOUBLE",
    "adjusted": "DOUBLE",
    "opp_games": "INTEGER",  # the opponent's completed games in the ratings
    "lg_ppg": "DOUBLE",  # league points per team-game at the position (the ratings' 1.0)
    "model_version": "VARCHAR NOT NULL",
    "created_at": "TIMESTAMP NOT NULL",
}
_TYPES = {"TIMESTAMP": pl.Datetime("us"), "INTEGER": pl.Int32, "VARCHAR": pl.String,
          "DOUBLE": pl.Float64, "BOOLEAN": pl.Boolean}  # fmt: skip
GRID_COLUMNS = [c for c in COLUMNS if c not in ("as_of", "model_version", "created_at")]
NOTE = ("A matchup rating moves a player's usual points a little; his role, health and the "
        "weather move them more. Week 17 was the last NFL week until 2020: teams that had "
        "clinched rested starters then; since 2021 that is week 18.")  # fmt: skip


def naive_utc(t: datetime) -> datetime:
    from datetime import UTC

    return t.astimezone(UTC).replace(tzinfo=None) if t.tzinfo is not None else t


def empty_grid() -> pl.DataFrame:
    return pl.DataFrame(schema={c: _TYPES[COLUMNS[c].split()[0]] for c in GRID_COLUMNS})


def schedule_sql(season: int) -> str:
    """The season's regular-season games: game_id, week, kickoff_utc, home_team, away_team."""
    return f"""
    SELECT game_id, week, kickoff_utc, home_team, away_team FROM fact_schedule
    WHERE season = {int(season)} AND season_type = 'REG' ORDER BY week, game_id"""


def completed_week(schedule: pl.DataFrame, team_games: pl.DataFrame, as_of: datetime) -> int:
    """The last week w such that every game of weeks <= w is in ``team_games`` or kicked off
    more than CANCELLED_AFTER before ``as_of`` (0: none)."""
    t = naive_utc(as_of)
    seen = set(team_games["game_id"].to_list()) if team_games.height else set()
    done = 0
    for w in sorted(set(schedule["week"].to_list())):
        g = schedule.filter(pl.col("week") == w)
        ok = all(gid in seen or (k is not None and k + CANCELLED_AFTER < t)
                 for gid, k in zip(g["game_id"], g["kickoff_utc"], strict=True))  # fmt: skip
        if not ok:
            break
        done = int(w)
    return done


def week_frames(db: Path | str, season: int, as_of: datetime) -> dict[str, Any]:
    """Through the as-of view: the season's unit-games and team-games and its schedule."""
    from twm.asof import AsOfView

    s = int(season)
    from datetime import UTC

    with AsOfView(db, naive_utc(as_of).replace(tzinfo=UTC)) as v:
        units = v.sql(hs.units_sql([s]))
        games = v.sql(hs.team_games_sql([s]))
        schedule = v.sql(schedule_sql(s))
    return {"units": units, "team_games": games, "schedule": schedule}


def opponents(schedule: pl.DataFrame, weeks: Sequence[int]) -> pl.DataFrame:
    """Every team of the season x each week of ``weeks``: opponent, home, game_id,
    kickoff_utc (NULL opponent: bye)."""
    s = schedule.filter(pl.col("week").is_in(list(weeks)))
    both = pl.concat([
        s.select("week", pl.col("home_team").alias("team"), pl.col("away_team").alias("opponent"),
                 pl.lit(True).alias("home"), "game_id", "kickoff_utc"),
        s.select("week", pl.col("away_team").alias("team"), pl.col("home_team").alias("opponent"),
                 pl.lit(False).alias("home"), "game_id", "kickoff_utc"),
    ])  # fmt: skip
    teams = pl.concat([schedule["home_team"], schedule["away_team"]]).unique().sort()
    wk = pl.DataFrame({"week": list(weeks)}, schema={"week": schedule.schema["week"]})
    grid = pl.DataFrame({"team": teams}).join(wk, how="cross")
    return grid.join(both, on=["team", "week"], how="left").sort("week", "team")


def grid(fr: dict[str, Any], spec: Any, season: int, as_of: datetime,
         weeks: Sequence[int] = WEEKS) -> pl.DataFrame:  # fmt: skip
    """The grid rows (snapshot columns except as_of / model_version / created_at): every team
    x week of ``weeks`` x position, rated with the completed weeks' games (module docstring).
    Empty when no week is complete yet."""
    tw = completed_week(fr["schedule"], fr["team_games"], as_of)
    if tw == 0 or fr["schedule"].is_empty():
        return empty_grid()
    tg = fr["team_games"].filter(pl.col("week") <= tw)
    totals = rt.unit_totals(fr["units"].filter(pl.col("week") <= tw), tg)
    r = spec.ratings(totals, tw).filter(pl.col("season") == int(season))
    r = r.with_columns(pl.col("rating").rank("min", descending=True).over("position")
                       .cast(pl.Int32).alias("rating_rank"))  # fmt: skip
    r = r.select(pl.col("team").alias("opponent"), "position", "candidate", "rating",
                 "rating_rank", "raw", "shrunk", "adjusted",
                 pl.col("games").alias("opp_games"), "lg_ppg")  # fmt: skip
    pos = pl.DataFrame({"position": list(hs.POSITIONS)})
    g = opponents(fr["schedule"], weeks).join(pos, how="cross")
    g = g.join(r, on=["opponent", "position"], how="left")
    ch = spec.chosen
    g = g.with_columns(
        pl.lit(int(season)).alias("season"), pl.lit(int(tw)).alias("through_week"),
        pl.col("position").replace_strict(ch, default="none").alias("candidate"),
        pl.when(pl.col("opponent").is_not_null()).then(pl.col("rating").fill_null(1.0))
        .alias("rating"),
    )  # fmt: skip
    ranked = pl.col("candidate") != "none"  # no rank where every matchup counts as 1.0
    g = g.with_columns(pl.when(ranked).then(pl.col("rating_rank")).alias("rating_rank"))
    g = g.with_columns([pl.col(c).cast(_TYPES[COLUMNS[c].split()[0]]) for c in GRID_COLUMNS])
    g = g.with_columns(pl.col(pl.Float64).round(hs.DECIMALS))
    order = {p: i for i, p in enumerate(hs.POSITIONS)}
    return g.with_columns(pl.col("position").replace_strict(order).alias("_o")).sort(
        "week", "team", "_o").select(GRID_COLUMNS)  # fmt: skip


def grid_rows(db: Path | str, spec: Any, season: int, as_of: datetime,
              weeks: Sequence[int] = WEEKS) -> pl.DataFrame:  # fmt: skip
    """The grid at ``as_of`` (module docstring)."""
    return grid(week_frames(db, season, as_of), spec, season, as_of, weeks)


def connect(store: Path | str) -> duckdb.DuckDBPyConnection:
    """The predictions store with the snapshot table (created if missing)."""
    from twm import predictions as pr

    con = pr.connect(store)
    body = ", ".join(f"{c} {t}" for c, t in COLUMNS.items())
    con.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} ({body})")
    return con


def stored_weeks(store: Path | str, season: int) -> set[int]:
    """The completed weeks already stored for ``season`` (read-only; empty without a table)."""
    snaps = read_snapshots(store, season)
    return set(snaps["through_week"].unique().to_list()) if snaps.height else set()


def store_snapshot(
    store: Path | str,
    rows: pl.DataFrame,
    *,
    as_of: datetime,
    model_version: str,
    created_at: datetime | None = None,
) -> dict[str, int]:
    """Append the grid as the snapshot of ``as_of`` (append-only: nothing is rewritten; a
    (season, through_week) already stored is kept, ``{'rows': 0, 'kept': n}``; an empty grid
    is not stored)."""
    from datetime import UTC

    if rows.is_empty():
        return {"rows": 0, "kept": 0}
    t = naive_utc(as_of)
    made = naive_utc(created_at or datetime.now(UTC))
    season, tw = int(rows["season"][0]), int(rows["through_week"][0])
    con = connect(store)
    try:
        kept = con.execute(f"SELECT count(*) FROM {TABLE} WHERE (season = ? AND through_week = ?)"
                           " OR as_of = ?", [season, tw, t]).fetchone()[0]  # fmt: skip
        if kept:
            return {"rows": 0, "kept": int(kept)}
        df = rows.with_columns(
            pl.lit(t).alias("as_of"),
            pl.lit(model_version).alias("model_version"),
            pl.lit(made).alias("created_at"),
        ).select(list(COLUMNS))
        con.register("_snap", df)
        con.execute(f"INSERT INTO {TABLE} SELECT * FROM _snap")
        return {"rows": df.height, "kept": 0}
    finally:
        con.close()


def read_snapshots(store: Path | str, season: int | None = None) -> pl.DataFrame:
    """Every stored snapshot row (of ``season``); empty when the table does not exist."""
    empty = pl.DataFrame(schema={c: _TYPES[t.split()[0]] for c, t in COLUMNS.items()})
    if not Path(store).exists():
        return empty
    con = duckdb.connect(str(store), read_only=True)
    try:
        con.execute("SET TimeZone='UTC'")
        have = con.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name = ?",
                           [TABLE]).fetchone()[0]  # fmt: skip
        if not have:
            return empty
        where = "" if season is None else f" WHERE season = {int(season)}"
        return con.execute(f"SELECT * FROM {TABLE}{where} ORDER BY as_of, week, team, "
                           "position").pl()  # fmt: skip
    finally:
        con.close()


ACTUAL_COLUMNS = ("season", "week", "game_id", "team", "opponent", "position", "points")


def read_actuals(db: Path | str, season: int) -> pl.DataFrame:
    """The season's (team-game, position) points straight from the warehouse (outcomes, read
    after the fact; :func:`twm.modules.playoff_planner.ratings.unit_totals`)."""
    con = hs.connect(db)
    try:
        u = con.execute(hs.units_sql([int(season)])).pl()
        g = con.execute(hs.team_games_sql([int(season)])).pl()
    finally:
        con.close()
    return rt.unit_totals(u, g).select(ACTUAL_COLUMNS)


def last_before_week(snaps: pl.DataFrame) -> pl.DataFrame:
    """Each (week, team, position)'s row of the LAST snapshot rated before that week (its
    ``through_week`` below the week: its ratings hold no game of that week or later; kickoff
    times are often unset months ahead). Byes have none."""
    pre = snaps.filter(pl.col("opponent").is_not_null() & (pl.col("through_week") < pl.col("week")))
    keys = ["season", "week", "team", "position"]
    return pre.sort("through_week", "as_of").group_by(keys, maintain_order=True).last()


def grade(snaps: pl.DataFrame, actuals: pl.DataFrame, last_only: bool = True) -> pl.DataFrame:
    """Snapshot rows (``last_only``: :func:`last_before_week`; else every rated row before
    its week) with ``points`` (the team's units at the position in that game), ``realized`` =
    points / lg_ppg and ``outcome``: None (not played yet) or 'played'."""
    before = pl.col("opponent").is_not_null() & (pl.col("through_week") < pl.col("week"))
    rows = last_before_week(snaps) if last_only else snaps.filter(before)
    a = actuals.select("season", "week", "team", "position", "points").with_columns(
        pl.col("season", "week").cast(pl.Int32))  # fmt: skip
    out = rows.join(a, on=["season", "week", "team", "position"], how="left")
    return out.with_columns(
        pl.when(pl.col("lg_ppg") > 0).then(pl.col("points") / pl.col("lg_ppg")).alias("realized"),
        pl.when(pl.col("points").is_not_null()).then(pl.lit("played")).alias("outcome"),
    )  # fmt: skip


def summary(graded: pl.DataFrame) -> pl.DataFrame:
    """Per position (and 'all'): n graded, pending, MAE of the realized multiplier against the
    chosen rating and against a flat 1.0 ("matchups don't matter")."""
    schema = {"position": pl.String, "n": pl.Int64, "pending": pl.Int64,
              "mae_rating": pl.Float64, "mae_flat": pl.Float64}  # fmt: skip
    if graded.is_empty():
        return pl.DataFrame(schema=schema)
    g = graded.with_columns((pl.col("realized") - pl.col("rating")).abs().alias("_e"),
                            (pl.col("realized") - 1.0).abs().alias("_f"))  # fmt: skip
    parts = [g, g.with_columns(pl.lit("all").alias("position"))]
    out = pl.concat(parts).group_by("position").agg(
        (pl.col("outcome") == "played").sum().cast(pl.Int64).alias("n"),
        pl.col("outcome").is_null().sum().cast(pl.Int64).alias("pending"),
        pl.col("_e").filter(pl.col("outcome") == "played").mean().alias("mae_rating"),
        pl.col("_f").filter(pl.col("outcome") == "played").mean().alias("mae_flat"),
    )  # fmt: skip
    order = {p: i for i, p in enumerate([*hs.POSITIONS, "all"])}
    return out.sort(pl.col("position").replace_strict(order, default=99)).select(list(schema))
