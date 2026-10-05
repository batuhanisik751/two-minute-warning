"""The live Teammate-out list, its nightly snapshots and their grading (docs/teammate_out.md).

For a regular-season week, every starter (the history's thresholds over his team's latest
games) whose game has not kicked off at ``as_of`` and who is Out or Doubtful on the week's
injury report or off the active roster (a reserve status: injured reserve, PUP, suspended ...)
but still with the team; for each, his teammates' predicted shares, PPR points and 80% range
with the pinned table (:mod:`.production`). A teammate is listed when he played at least two
of the team's games this season in which every listed starter played, is on the team's
latest roster with an active status and is not tagged Out or Doubtful himself.

The injury rows are read exactly as the Questionable list reads them (its 2025+ "observed"
rule: :func:`twm.modules.questionable.weekly.injury_source`), so both lists see the same tags.

Snapshots: each run is stored in the predictions store's ``teammate_out_snapshots`` table keyed
by its as-of, append-only. Grading needs only the stored (or published) rows and the
warehouse: :func:`read_actuals` and :func:`grade` (a teammate's LAST row before his kickoff).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from twm.modules.questionable import history as qhs
from twm.modules.questionable import weekly as qwk
from twm.modules.teammate_out import history as hs

TABLE = "teammate_out_snapshots"
COLUMNS: dict[str, str] = {
    "as_of": "TIMESTAMP NOT NULL",  # naive UTC
    "season": "INTEGER NOT NULL",
    "week": "INTEGER NOT NULL",
    "team": "VARCHAR NOT NULL",
    "opponent": "VARCHAR",
    "game_id": "VARCHAR",
    "kickoff_utc": "TIMESTAMP",
    "out_ids": "VARCHAR NOT NULL",  # comma-separated gsis ids of the absent starters
    "out_players": "VARCHAR",  # their names, ', '-separated (same order)
    "out_positions": "VARCHAR",
    "out_reasons": "VARCHAR",  # 'Out' / 'Doubtful' / 'roster RES' ... (same order)
    "n_out": "INTEGER NOT NULL",
    "vac_carry_share": "DOUBLE",
    "vac_target_share": "DOUBLE",
    "gsis_id": "VARCHAR NOT NULL",
    "player": "VARCHAR",
    "position": "VARCHAR",
    "role": "VARCHAR",
    "base_games": "INTEGER",
    "base_carry_share": "DOUBLE",
    "base_target_share": "DOUBLE",
    "base_snap_share": "DOUBLE",
    "base_points": "DOUBLE",  # his baseline PPR points per game ("nothing changes")
    "base_team_carries": "DOUBLE",
    "base_team_targets": "DOUBLE",
    "pred_carry_share": "DOUBLE",
    "pred_target_share": "DOUBLE",
    "carry_share_change": "DOUBLE",
    "target_share_change": "DOUBLE",
    "pred_points": "DOUBLE",
    "points_lo": "DOUBLE",
    "points_hi": "DOUBLE",
    "pred_gain": "DOUBLE",
    "alloc_carry_share": "DOUBLE",
    "alloc_target_share": "DOUBLE",
    "source": "VARCHAR NOT NULL",  # 'asof' or 'observed' (the Questionable rule)
    "model_version": "VARCHAR NOT NULL",
    "created_at": "TIMESTAMP NOT NULL",
}
OUT_STATUSES = ("Out", "Doubtful")
ACTIVE_STATUSES = ("ACT", "INA", "DEV")  # on the team and able to play (Radar's rule)
NOTE = ("Inactives come out about 90 minutes before kickoff: a Doubtful starter may still "
        "play, and then nobody gains.")  # fmt: skip
_KEY = ["season", "team", "gi"]


def roster_sql(season: int, week: int) -> str:
    """Each team's latest weekly roster (of weeks up to ``week``): team, gsis_id, status,
    full_name."""
    return f"""
    SELECT team, gsis_id, any_value(full_name) AS full_name,
           CASE WHEN bool_or(status IN ({hs._in(ACTIVE_STATUSES)})) THEN 'ACT'
                ELSE min(status) END AS status
    FROM fact_roster_week
    WHERE season = {int(season)} AND season_type = 'REG' AND week <= {int(week)}
      AND gsis_id IS NOT NULL
    GROUP BY team, gsis_id, week
    QUALIFY week = max(week) OVER (PARTITION BY team)"""


def live_starters(pg: pl.DataFrame) -> pl.DataFrame:
    """Starters going into each team's next game (the history's rule over the team's last
    WINDOW games in ``pg``): season, week, team, gi (the NEXT game's index), out_id,
    out_pos, win_games, win_carry_share, win_target_share."""
    last = pg.group_by("season", "team").agg(pl.col("gi").max().alias("_last"))
    w = pg.join(last, on=["season", "team"]).filter(
        pl.col("played") & (pl.col("gi") > pl.col("_last") - hs.WINDOW)
    )
    s = w.sort("gi").group_by("season", "team", "gsis_id").agg(
        pl.col("_last").first(), pl.col("position").last(), pl.len().alias("win_games"),
        pl.col("carry_share").mean().round(hs.DECIMALS).alias("win_carry_share"),
        pl.col("target_share").mean().round(hs.DECIMALS).alias("win_target_share"),
    )  # fmt: skip
    rb = (pl.col("position") == "RB") & (pl.col("win_carry_share") >= hs.RB_CARRY_SHARE)
    pas = pl.col("position").is_in(["WR", "TE"]) & (pl.col("win_target_share") >= hs.TARGET_SHARE)
    s = s.filter((pl.col("win_games") >= hs.MIN_WINDOW_GAMES) & (rb | pas))
    return s.select(
        "season", pl.lit(None, dtype=pl.Int32).alias("week"), "team",
        (pl.col("_last") + 1).alias("gi"), pl.col("gsis_id").alias("out_id"),
        pl.col("position").alias("out_pos"), "win_games", "win_carry_share", "win_target_share",
    ).sort("team", "out_id")  # fmt: skip


def absent(starters: pl.DataFrame, tags: pl.DataFrame, roster: pl.DataFrame) -> pl.DataFrame:
    """The starters who are Out / Doubtful on the week's report (``tags``: team, gsis_id,
    report_status) or on a reserve status on the team's latest roster, and not gone (a gone
    status there); with ``out_reason``."""
    t = tags.select("team", pl.col("gsis_id").alias("out_id"), "report_status").unique(
        ["team", "out_id"])  # fmt: skip
    r = roster.select("team", pl.col("gsis_id").alias("out_id"), "status")
    s = starters.join(t, on=["team", "out_id"], how="left").join(
        r, on=["team", "out_id"], how="left")  # fmt: skip
    gone = pl.col("status").is_in(list(hs.LEFT_STATUSES)).fill_null(False)
    reserve = pl.col("status").is_not_null() & ~pl.col("status").is_in(list(ACTIVE_STATUSES))
    s = s.filter(~gone & (pl.col("report_status").is_not_null() | reserve))
    return s.with_columns(
        pl.coalesce("report_status", pl.concat_str(pl.lit("roster "), pl.col("status")))
        .alias("out_reason")
    ).drop("report_status", "status")  # fmt: skip


def _joined(col: str, sep: str) -> pl.Expr:
    return pl.col(col).cast(pl.List(pl.String)).list.join(sep)


def week_frames(db: Path | str, season: int, week: int, as_of: datetime) -> dict[str, Any]:
    """Through the as-of view: the season's player-games before ``week`` (pg), the week's
    Out / Doubtful tags (the Questionable reading), the latest rosters and the week's games."""
    from twm.asof import AsOfView

    t = qwk.naive_utc(as_of)
    s, w = int(season), int(week)
    with AsOfView(db, as_of) as v:
        built = qwk.injury_built_at(v)
        observed = built is not None and built <= t
        pg = v.sql(hs.players_sql([s])).filter(pl.col("week") < w)
        tags = v.sql(qhs.tagged_sql([s], OUT_STATUSES, qwk.injury_source(s, w, observed)))
        roster = v.sql(roster_sql(s, w))
        games = v.sql(f"""
            SELECT game_id, kickoff_utc, home_team AS team, away_team AS opponent
            FROM fact_schedule WHERE season = {s} AND week = {w} AND season_type = 'REG'
            UNION ALL
            SELECT game_id, kickoff_utc, away_team, home_team
            FROM fact_schedule WHERE season = {s} AND week = {w} AND season_type = 'REG'""")
    return {"pg": pg, "tags": tags.filter(pl.col("week") == w), "roster": roster,
            "games": games, "source": "observed" if observed else "asof"}  # fmt: skip


def predict_week(fr: dict[str, Any], spec: Any, season: int, week: int,
                 as_of: datetime) -> pl.DataFrame:  # fmt: skip
    """The list rows (snapshot columns except as_of / model_version / created_at)."""
    t = qwk.naive_utc(as_of)
    games = fr["games"].filter(pl.col("kickoff_utc").is_not_null() & (pl.col("kickoff_utc") > t))
    pg, roster, tags = fr["pg"], fr["roster"], fr["tags"]
    outs = absent(live_starters(pg), tags, roster).join(games.select("team"), on="team")
    if outs.is_empty():
        return empty_rows()
    outs = outs.with_columns(pl.lit(int(week)).alias("week"))
    bg = hs.baseline_games(outs, pg)
    ev = hs.vacated(outs.drop("out_reason"), bg, pg)
    ok = roster.filter(pl.col("status") == "ACT").select("team", "gsis_id")
    ruled = tags.select("team", "gsis_id")
    m = hs.base_mates(ev, bg, pg).join(ok, on=["team", "gsis_id"], how="semi")
    m = m.join(ruled, on=["team", "gsis_id"], how="anti")
    if m.is_empty():
        return empty_rows()
    m = spec.predict(hs.with_roles(m, ev))
    return _rows(m, outs, fr, games)


_TYPES = {"TIMESTAMP": pl.Datetime("us"), "INTEGER": pl.Int32, "VARCHAR": pl.String,
          "DOUBLE": pl.Float64}  # fmt: skip
LIST_COLUMNS = [c for c in COLUMNS if c not in ("as_of", "model_version", "created_at")]


def empty_rows() -> pl.DataFrame:
    return pl.DataFrame(schema={c: _TYPES[COLUMNS[c].split()[0]] for c in LIST_COLUMNS})


def _rows(m: pl.DataFrame, outs: pl.DataFrame, fr: dict[str, Any], games: pl.DataFrame
          ) -> pl.DataFrame:  # fmt: skip
    names = fr["roster"].select("team", "gsis_id", pl.col("full_name").alias("player"))
    tag_names = fr["tags"].select("team", "gsis_id", pl.col("player").alias("_tag_name"))
    o = (outs.rename({"out_id": "gsis_id"}).join(names, on=["team", "gsis_id"], how="left")
         .join(tag_names, on=["team", "gsis_id"], how="left")
         .with_columns(pl.coalesce("player", "_tag_name", "gsis_id").alias("player"))
         .sort("team", "gsis_id").group_by("team", maintain_order=True)
         .agg(pl.col("gsis_id").str.join(",").alias("out_ids"),
              pl.col("player").str.join(", ").alias("out_players"),
              pl.col("out_pos").str.join(", ").alias("out_positions"),
              pl.col("out_reason").str.join(", ").alias("out_reasons")))  # fmt: skip
    r = (m.join(names, on=["team", "gsis_id"], how="left").join(o, on="team", how="left")
         .join(games.select("team", "opponent", "game_id", "kickoff_utc"), on="team", how="left")
         .with_columns(
             pl.col("week").cast(pl.Int32), pl.lit(fr["source"]).alias("source"),
             (pl.col("pred_carry_share") - pl.col("base_carry_share")).alias("carry_share_change"),
             (pl.col("pred_target_share") - pl.col("base_target_share"))
             .alias("target_share_change"),
             pl.col("player").fill_null(pl.col("gsis_id")),
         ))  # fmt: skip
    r = r.with_columns(
        [pl.col(c).cast(_TYPES[COLUMNS[c].split()[0]]) for c in LIST_COLUMNS]
    ).with_columns(pl.col(pl.Float64).round(hs.DECIMALS))
    return r.select(LIST_COLUMNS).sort("kickoff_utc", "team", "pred_points", "gsis_id",
                                       descending=[False, False, True, False])  # fmt: skip


def list_rows(db: Path | str, spec: Any, season: int, week: int, as_of: datetime) -> pl.DataFrame:
    """The week's list at ``as_of`` (module docstring)."""
    return predict_week(week_frames(db, season, week, as_of), spec, season, week, as_of)


def connect(store: Path | str) -> duckdb.DuckDBPyConnection:
    """The predictions store with the snapshot table (created if missing)."""
    from twm import predictions as pr

    con = pr.connect(store)
    body = ", ".join(f"{c} {t}" for c, t in COLUMNS.items())
    con.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} ({body})")
    return con


def store_snapshot(
    store: Path | str,
    rows: pl.DataFrame,
    *,
    season: int,
    week: int,
    as_of: datetime,
    model_version: str,
    created_at: datetime | None = None,
) -> dict[str, int]:
    """Append the list as the snapshot of ``as_of`` (append-only: an as-of of the week that is
    already stored is never rewritten: ``{'rows': 0, 'kept': n}``; empty lists are not stored)."""
    from datetime import UTC

    t = qwk.naive_utc(as_of)
    made = qwk.naive_utc(created_at or datetime.now(UTC))
    con = connect(store)
    try:
        kept = con.execute(f"SELECT count(*) FROM {TABLE} WHERE as_of = ? AND season = ? "
                           "AND week = ?", [t, season, week]).fetchone()[0]  # fmt: skip
        if kept or rows.is_empty():
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
        return con.execute(f"SELECT * FROM {TABLE}{where} ORDER BY as_of, team, gsis_id").pl()
    finally:
        con.close()


ACTUAL_COLUMNS = ("season", "week", "team", "gsis_id", "played", "carry_share", "target_share",
                  "points")  # fmt: skip


def read_actuals(db: Path | str, season: int) -> pl.DataFrame:
    """The season's RB/WR/TE player-games straight from the warehouse (outcomes, read after
    the fact): :data:`ACTUAL_COLUMNS`. A team-week with any row has played its game."""
    con = hs.connect(db)
    try:
        return con.execute(hs.players_sql([int(season)])).pl().select(ACTUAL_COLUMNS)
    finally:
        con.close()


def last_before_kickoff(snaps: pl.DataFrame) -> pl.DataFrame:
    """Each (week, team, teammate)'s LAST row taken before his kickoff."""
    pre = snaps.filter(pl.col("as_of") < pl.col("kickoff_utc"))
    keys = ["season", "week", "team", "gsis_id"]
    return pre.sort("as_of").group_by(keys, maintain_order=True).last()


def grade(snaps: pl.DataFrame, actuals: pl.DataFrame) -> pl.DataFrame:
    """:func:`last_before_kickoff` rows with ``outcome``: None (game not played yet),
    'starter played' (an absent starter took a snap after all: nothing to grade), 'did not
    play' (the teammate), 'played'; and act_carry_share / act_target_share / act_points."""
    last = last_before_kickoff(snaps)
    games = actuals.select("season", "week", "team").unique().with_columns(pl.lit(True).alias("_g"))
    played = actuals.filter(pl.col("played"))
    snapped = played.select("season", "week", "gsis_id").unique()
    outs = (last.select("season", "week", "team", "as_of", "out_ids").unique()
            .with_columns(pl.col("out_ids").str.split(",").alias("_o")).explode("_o"))  # fmt: skip
    hit = outs.join(snapped.with_columns(pl.lit(True).alias("_p")).rename({"gsis_id": "_o"}),
                   on=["season", "week", "_o"], how="left")  # fmt: skip
    sat = hit.group_by("season", "week", "team", "as_of").agg(
        (~pl.col("_p").fill_null(False).any()).alias("starters_sat")
    )
    act = played.select("season", "week", "team", "gsis_id",
                        *[pl.col(c).alias(f"act_{c}") for c in
                          ("carry_share", "target_share", "points")])  # fmt: skip
    out = (last.join(games, on=["season", "week", "team"], how="left")
           .join(sat, on=["season", "week", "team", "as_of"], how="left")
           .join(act, on=["season", "week", "team", "gsis_id"], how="left"))  # fmt: skip
    return out.with_columns(
        pl.when(pl.col("_g").is_null()).then(None)
        .when(~pl.col("starters_sat").fill_null(True)).then(pl.lit("starter played"))
        .when(pl.col("act_points").is_null()).then(pl.lit("did not play"))
        .otherwise(pl.lit("played")).alias("outcome")
    ).drop("_g")  # fmt: skip


def summary(graded: pl.DataFrame) -> dict[str, Any]:
    """The season-to-date live record (for the site), over the graded 'played' rows: n, MAE
    of the predicted points and of "nothing changes" (his baseline points), MAE of the shares
    (predicted and baseline), the 80% range's coverage, and the top-gainer hit rate per
    team-week; plus pending / starter played / did not play counts."""
    counts = {"n": 0, "pending": 0, "starter_played": 0, "did_not_play": 0}
    if graded.is_empty() or "outcome" not in graded.columns:
        return {**counts, "pending": graded.height}
    o = graded["outcome"]
    counts.update(pending=int(o.is_null().sum()), starter_played=int((o == "starter played").sum()),
                  did_not_play=int((o == "did not play").sum()))  # fmt: skip
    g = graded.filter(pl.col("outcome") == "played")
    counts["n"] = g.height
    if g.is_empty():
        return counts

    def mae(a: str, b: str) -> float:
        return float((g[a] - g[b]).abs().mean())  # type: ignore[arg-type]

    ranged = g.filter(pl.col("points_lo").is_not_null() & pl.col("points_hi").is_not_null())
    inside = ranged.filter(
        pl.col("act_points").is_between(pl.col("points_lo"), pl.col("points_hi"))
    )
    k = ["season", "week", "team"]
    real = g.with_columns((pl.col("act_points") - pl.col("base_points")).alias("_real"))
    pick = (
        real.sort("pred_gain", "base_points", "gsis_id", descending=[True, True, False])
        .group_by(k, maintain_order=True)
        .first()
        .select(*k, pl.col("gsis_id").alias("_p"))
    )
    best = (
        real.sort("_real", "gsis_id", descending=[True, False])
        .group_by(k, maintain_order=True)
        .first()
        .select(*k, pl.col("gsis_id").alias("_r"))
    )
    hits = pick.join(best, on=k)
    return {
        **counts,
        "mae_points": mae("act_points", "pred_points"),
        "mae_points_base": mae("act_points", "base_points"),
        "mae_carry_share": mae("act_carry_share", "pred_carry_share"),
        "mae_carry_share_base": mae("act_carry_share", "base_carry_share"),
        "mae_target_share": mae("act_target_share", "pred_target_share"),
        "mae_target_share_base": mae("act_target_share", "base_target_share"),
        "ranged": ranged.height,
        "coverage": inside.height / ranged.height if ranged.height else None,
        "team_weeks": hits.height,
        "top_hit": float((hits["_p"] == hits["_r"]).mean()) if hits.height else None,  # type: ignore[arg-type]
    }  # fmt: skip
