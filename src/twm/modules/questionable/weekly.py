"""The live Questionable list, its nightly snapshots and their grading (docs/questionable.md).

For a regular-season week, every QB/RB/WR/TE whose final injury report says Questionable or
Doubtful and whose game has not kicked off at ``as_of``: his chance to play (the pinned table,
:mod:`.production`) and his bucket's "if he plays" line. The tag is not the final word:
inactives come out about 90 minutes before kickoff.

Which injury rows the list may see. The warehouse stamps a 2025+ injury row (nflverse has no
``date_modified`` since 2025) as available only at its team's kickoff, so the as-of view hides
every 2026 row until the game starts. A row the warehouse holds was in nflverse's file when it
was downloaded, so it was public by the build of ``fact_injury_report``
(``build_manifest.built_at``): when ``as_of`` is at or after that build, the list also reads
the warehouse's rows of the week (``source`` 'observed'); before it (a time-machine as-of),
only the as-of view's rows (``source`` 'asof').

Snapshots: each run is stored in the predictions store's ``questionable_snapshots`` table
keyed by its as-of, append-only (a stored as-of is never rewritten). Grading: a player's LAST
snapshot before his kickoff is graded played / did not play once his game's snap counts exist.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from twm.modules.questionable import history as hs

TABLE = "questionable_snapshots"
COLUMNS: dict[str, str] = {
    "as_of": "TIMESTAMP NOT NULL",  # naive UTC
    "season": "INTEGER NOT NULL",
    "week": "INTEGER NOT NULL",
    "gsis_id": "VARCHAR NOT NULL",
    "player": "VARCHAR",
    "position": "VARCHAR",
    "team": "VARCHAR",
    "opponent": "VARCHAR",
    "game_id": "VARCHAR",
    "kickoff_utc": "TIMESTAMP",
    "report_status": "VARCHAR NOT NULL",
    "practice_status": "VARCHAR",
    "practice": "VARCHAR",
    "missed_prev": "BOOLEAN",
    "body_part": "VARCHAR",
    "play_chance": "DOUBLE",
    "plays_n": "INTEGER",
    "plays_median": "DOUBLE",
    "plays_dud_rate": "DOUBLE",
    "healthy_median": "DOUBLE",
    "healthy_dud_rate": "DOUBLE",
    "season_ppg": "DOUBLE",
    "season_games": "INTEGER",
    "source": "VARCHAR NOT NULL",  # 'asof' (the as-of view) or 'observed' (module docstring)
    "model_version": "VARCHAR NOT NULL",
    "created_at": "TIMESTAMP NOT NULL",
}
INACTIVES_NOTE = ("The tag is not the final word: teams name their inactive players about 90 "
                  "minutes before kickoff.")  # fmt: skip
_INJ_COLS = ("season, season_type, week, team, gsis_id, position, full_name, report_status, "
             "practice_status, report_primary_injury, date_modified")  # fmt: skip


def naive_utc(t: datetime) -> datetime:
    return t.astimezone(UTC).replace(tzinfo=None) if t.tzinfo is not None else t


def injury_built_at(view: Any) -> datetime | None:
    """When the warehouse's fact_injury_report was built (naive UTC; None if unknown)."""
    row = view.sql("SELECT max(built_at) AS t FROM wh.main.build_manifest "
                   "WHERE table_name = 'fact_injury_report'")  # fmt: skip
    t = row.get_column("t")[0] if row.height else None
    return t


def injury_source(season: int, week: int, observed: bool) -> str:
    """The rows the list reads: the as-of view's, plus (``observed``) the warehouse's rows of
    the week."""
    view = f"SELECT {_INJ_COLS} FROM fact_injury_report"
    if not observed:
        return f"({view})"
    raw = (f"SELECT {_INJ_COLS} FROM wh.main.fact_injury_report "
           f"WHERE season = {int(season)} AND week = {int(week)}")  # fmt: skip
    return f"({view} UNION {raw})"


def upcoming(rows: pl.DataFrame, week: int, as_of: datetime) -> pl.DataFrame:
    """The week's rows whose game has not kicked off at ``as_of`` (unknown kickoff: left out)."""
    t = naive_utc(as_of)
    return rows.filter((pl.col("week") == week) & pl.col("kickoff_utc").is_not_null()
                       & (pl.col("kickoff_utc") > t))  # fmt: skip


def list_rows(db: Path | str, spec: Any, season: int, week: int, as_of: datetime) -> pl.DataFrame:
    """The week's list at ``as_of`` (module docstring): one row per tagged player whose game
    has not kicked off, with the snapshot columns except as_of / model_version / created_at."""
    from twm.asof import AsOfView

    t = naive_utc(as_of)
    with AsOfView(db, as_of) as v:
        built = injury_built_at(v)
        observed = built is not None and built <= t
        rows = v.sql(hs.tagged_sql([season], hs.MODELLED, injury_source(season, week, observed)))
    rows = upcoming(rows, week, t)
    rows = hs.with_buckets(rows).with_columns(
        pl.lit("observed" if observed else "asof").alias("source"),
        pl.when(pl.col("prior_games") > 0).then(pl.col("prior_ppg")).alias("season_ppg"),
        pl.col("prior_games").cast(pl.Int32).alias("season_games"),
    )
    chance = spec.chance(rows) if rows.height else pl.Series("p", [], dtype=pl.Float64)
    lines = [spec.if_plays(s, str(m).lower()) or {} for s, m in
             zip(rows["report_status"], rows["missed_prev"], strict=True)]  # fmt: skip
    rows = rows.with_columns(
        chance.alias("play_chance"),
        pl.Series("plays_n", [x.get("n") for x in lines], dtype=pl.Int32),
        pl.Series("plays_median", [x.get("median") for x in lines], dtype=pl.Float64),
        pl.Series("plays_dud_rate", [x.get("dud_rate") for x in lines], dtype=pl.Float64),
        pl.Series("healthy_median", [x.get("healthy_median") for x in lines], dtype=pl.Float64),
        pl.Series("healthy_dud_rate", [x.get("healthy_dud_rate") for x in lines],
                  dtype=pl.Float64),
    )  # fmt: skip
    cols = [c for c in COLUMNS if c not in ("as_of", "model_version", "created_at")]
    return rows.select(cols).sort("kickoff_utc", "team", "play_chance", "player")


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
    """Append the list as the snapshot of ``as_of`` (append-only: if that as-of of the week
    is already stored, nothing is written: ``{'rows': 0, 'kept': n}``)."""
    t = naive_utc(as_of)
    made = naive_utc(created_at or datetime.now(UTC))
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
    if not Path(store).exists():
        return pl.DataFrame(schema={c: pl.String for c in COLUMNS})
    con = duckdb.connect(str(store), read_only=True)
    try:
        con.execute("SET TimeZone='UTC'")
        have = con.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name = ?",
                           [TABLE]).fetchone()[0]  # fmt: skip
        if not have:
            return pl.DataFrame(schema={c: pl.String for c in COLUMNS})
        where = "" if season is None else f" WHERE season = {int(season)}"
        return con.execute(f"SELECT * FROM {TABLE}{where} ORDER BY as_of, gsis_id").pl()
    finally:
        con.close()


def last_before_kickoff(snaps: pl.DataFrame) -> pl.DataFrame:
    """Each player-week's LAST snapshot taken before his kickoff."""
    pre = snaps.filter(pl.col("as_of") < pl.col("kickoff_utc"))
    return pre.sort("as_of").group_by("season", "week", "gsis_id", maintain_order=True).last()


def grade(snaps: pl.DataFrame, played: pl.DataFrame) -> pl.DataFrame:
    """:func:`last_before_kickoff` rows with ``outcome`` 'played' / 'did not play' (None while
    his team's game has no snap counts yet). ``played``: season, week, team, gsis_id,
    offense_snaps rows of the games (fact_snaps)."""
    last = last_before_kickoff(snaps)
    if last.is_empty():
        return last.with_columns(pl.lit(None, dtype=pl.String).alias("outcome"))
    games = played.select("season", "week", "team").unique().with_columns(pl.lit(True).alias("_g"))
    snapped = (played.filter(pl.col("offense_snaps") > 0).select("season", "week", "gsis_id")
               .unique().with_columns(pl.lit(True).alias("_p")))  # fmt: skip
    out = last.join(games, on=["season", "week", "team"], how="left").join(
        snapped, on=["season", "week", "gsis_id"], how="left")  # fmt: skip
    return out.with_columns(
        pl.when(pl.col("_g").is_null()).then(None)
        .when(pl.col("_p").fill_null(False)).then(pl.lit("played"))
        .otherwise(pl.lit("did not play")).alias("outcome")
    ).drop("_g", "_p")  # fmt: skip


def read_game_snaps(db: Path | str, season: int) -> pl.DataFrame:
    """fact_snaps rows of ``season``'s regular-season games (outcomes: read after the fact)."""
    con = hs.connect(db)
    try:
        return con.execute("SELECT season, week, team, gsis_id, offense_snaps FROM fact_snaps "
                           "WHERE season = ? AND game_type = 'REG' AND gsis_id IS NOT NULL",
                           [int(season)]).pl()  # fmt: skip
    finally:
        con.close()


def summary(graded: pl.DataFrame) -> dict[str, Any]:
    """The season-to-date live record (for the site): graded players, the average chance the
    list gave them and the share that played; by game status too."""
    if graded.is_empty() or "outcome" not in graded.columns:  # nothing stored or graded yet
        none: dict[str, Any] = {"n": 0, "predicted": None, "actual": None}
        return {**none, "pending": graded.height, "by_status": {s: dict(none) for s in hs.MODELLED}}
    g = graded.filter(pl.col("outcome").is_not_null())

    def one(df: pl.DataFrame) -> dict[str, Any]:
        n = df.height
        return {"n": n,
                "predicted": float(df["play_chance"].mean()) if n else None,  # type: ignore[arg-type]
                "actual": float((df["outcome"] == "played").mean()) if n else None}  # type: ignore[arg-type]  # fmt: skip

    out = one(g)
    out["pending"] = graded.height - g.height
    out["by_status"] = {s: one(g.filter(pl.col("report_status") == s)) for s in hs.MODELLED}
    return out
