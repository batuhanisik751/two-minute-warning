"""Build the DuckDB warehouse from the local Parquet cache. Never downloads.

``build_warehouse(seasons)`` reads ``data/raw/<dataset>/<season>.parquet`` (and ``all.parquet``
for players/teams) straight from disk via :func:`twm.sources.nflverse.cache_path` - it never
calls ``nflverse.fetch`` so a stale current-season file can never trigger a download - and
writes every table of :mod:`twm.warehouse.schema` into a scratch file next to the target
(``data/warehouse.duckdb.building``) inside one transaction, then swaps it into place with an
atomic rename. Readers holding the old file (a notebook, ``twm doctor``, a Streamlit page) are
never blocked and keep the old contents until they reopen; a failed build removes the scratch
file and never touches the target. Building into a fresh scratch file also keeps the file
compact: rewriting tables inside an existing file would keep the old copies until COMMIT and
double the size. Concurrent builds of the same target are refused through an advisory lock on
``<db>.build.lock`` (:class:`BuildInProgressError`), and a stale ``<db>.wal`` left by some
other writer is discarded before the rename so DuckDB cannot replay it into the new file: the
warehouse file is written only by ``twm build``. A missing cache file for a requested season
raises :class:`MissingCacheError` listing every missing path; seasons before a dataset's
``first_season`` are skipped and reported in the manifest.

Determinism rules ("build twice, get the same content": per-table ``content_hash`` and row sets
are identical; the DuckDB file itself is not byte-identical, so never compare the files):

- every connection runs ``SET TimeZone='UTC'`` and every stored timestamp is a naive TIMESTAMP
  holding UTC (asserted after the build), so the result does not depend on the machine's zone;
- every column has an explicit target type (schema.py) so the set of seasons does not change
  dtypes;
- every table is ``CREATE TABLE AS SELECT ... ORDER BY <primary key>`` and its ``content_hash``
  is an order-free md5 over per-row md5s of the row rendered as text, computed in DuckDB (the
  text rendering is DuckDB's, so compare hashes only across builds with the same
  ``duckdb_version``, which the manifest records);
- nothing in table content comes from now()/random; only ``build_manifest.built_at`` does.

Point-in-time (B2): every *event* table (see :mod:`twm.warehouse.available`) gets an
``available_at`` column, computed in SQL by the same ``CREATE TABLE`` that materializes the
table (last column, table still ordered by its key). A NULL ``available_at`` stops the build
with :class:`~twm.warehouse.available.AvailabilityError`; the manifest notes record which rule
placed how many rows and ``n_available_after_week_asof``.
"""

from __future__ import annotations

import dataclasses
import fcntl
import json
import logging
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from twm import __version__
from twm import ids as pid
from twm.config import FANTASY_POSITIONS, STREAMER_POSITIONS, league, settings
from twm.sources import nflverse as nv
from twm.warehouse import available as av
from twm.warehouse import coach_corrections as cc
from twm.warehouse import schema as sc
from twm.warehouse import weeks as wk

log = logging.getLogger("twm.warehouse")

SESSION_TIMEZONE = "UTC"

PL_TYPES: dict[str, pl.DataType] = {
    "INTEGER": pl.Int32(),
    "BIGINT": pl.Int64(),
    "DOUBLE": pl.Float64(),
    "VARCHAR": pl.String(),
    "BOOLEAN": pl.Boolean(),
    "DATE": pl.Date(),
    "TIMESTAMP": pl.Datetime("us"),
}


class MissingCacheError(FileNotFoundError):
    """A requested season is not in the local cache. Run ``twm ingest`` first (never build)."""

    def __init__(self, missing: Sequence[Path]):
        self.missing = list(missing)
        listing = "\n".join(f"  - {nv.project_relative(p)}" for p in self.missing)
        super().__init__(
            f"{len(self.missing)} cache file(s) missing; run `twm ingest` for them:\n{listing}"
        )


class PrimaryKeyError(RuntimeError):
    """A table's primary key is not unique (or a required key column is NULL) after build."""


class BuildInProgressError(RuntimeError):
    """Another process holds ``<db>.build.lock``: a build of the same target is running."""


@dataclass
class TableStats:
    table_name: str
    n_rows: int = 0
    content_hash: str = ""
    seasons: list[int] = field(default_factory=list)
    seasons_skipped: list[int] = field(default_factory=list)
    n_source_rows: int = 0
    n_dropped_null_key: int = 0
    n_dropped_duplicates: int = 0
    notes: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------------------
# Connection and sources
# --------------------------------------------------------------------------------------


def connect(
    db_path: Path | str | None = None, *, read_only: bool = True
) -> duckdb.DuckDBPyConnection:
    """Open the warehouse (default: settings paths.warehouse) with a fixed UTC session zone.

    Read-only by default: the warehouse is written only by ``twm build`` (into its scratch
    file, which is the one ``read_only=False`` caller). A read-write handle would block
    ``twm doctor`` and could leave a ``.wal`` beside the file; see :func:`build_warehouse`.
    """
    path = Path(db_path) if db_path is not None else settings().path("warehouse")
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path), read_only=read_only)
    con.execute(f"SET TimeZone='{SESSION_TIMEZONE}'")
    return con


@dataclass(frozen=True)
class SourceFiles:
    """Cache files a build reads, per dataset, plus the seasons skipped as too early."""

    paths: dict[str, list[Path]]
    seasons_used: dict[str, list[int]]
    seasons_skipped: dict[str, list[int]]


def resolve_sources(seasons: Sequence[int]) -> SourceFiles:
    """Map requested seasons to cache paths; raise MissingCacheError listing all gaps at once."""
    paths: dict[str, list[Path]] = {}
    used: dict[str, list[int]] = {}
    skipped: dict[str, list[int]] = {}
    missing: list[Path] = []
    for name in sc.SOURCE_DATASETS:
        ds = nv.DATASETS[name]
        if not ds.per_season:
            p = nv.cache_path(name, None)
            paths[name], used[name], skipped[name] = [p], [], []
            if not p.exists():
                missing.append(p)
            continue
        paths[name], used[name], skipped[name] = [], [], []
        for s in seasons:
            if ds.first_season is not None and s < ds.first_season:
                skipped[name].append(s)
                continue
            p = nv.cache_path(name, s)
            if not p.exists():
                missing.append(p)
            paths[name].append(p)
            used[name].append(s)
    if missing:
        raise MissingCacheError(missing)
    return SourceFiles(paths, used, skipped)


def _sql_list(paths: Iterable[Path]) -> str:
    return "[" + ", ".join(sc.sql_str(str(p)) for p in paths) + "]"


def _describe(con: duckdb.DuckDBPyConnection, relation: str) -> dict[str, str]:
    return {row[0]: row[1] for row in con.execute(f"DESCRIBE {relation}").fetchall()}


def _source_view(con: duckdb.DuckDBPyConnection, name: str, paths: list[Path]) -> dict[str, str]:
    """A temp view over the union of the dataset's cache files; returns column -> DuckDB type."""
    con.execute(
        f"CREATE OR REPLACE TEMP VIEW src_{name} AS "
        f"SELECT * FROM read_parquet({_sql_list(paths)}, union_by_name=true)"
    )
    return _describe(con, f"src_{name}")


def _depth_chart_views(
    con: duckdb.DuckDBPyConnection, paths: list[Path], seasons: list[int]
) -> tuple[dict[str, str] | None, dict[str, str] | None]:
    """Split depth-chart files by format (daily files have ``dt``); daily rows get the file's
    season as ``file_season`` because the daily format has no season column."""
    legacy, daily = [], []
    for p, s in zip(paths, seasons, strict=True):
        cols = pl.read_parquet_schema(p)
        (daily if "dt" in cols else legacy).append((p, s))
    legacy_types = daily_types = None
    if legacy:
        legacy_types = _source_view(con, "depth_legacy", [p for p, _ in legacy])
    if daily:
        parts = [
            f"(SELECT {s} AS file_season, * FROM read_parquet({sc.sql_str(str(p))}))"
            for p, s in daily
        ]
        con.execute(
            "CREATE OR REPLACE TEMP VIEW src_depth_daily AS " + " UNION ALL BY NAME ".join(parts)
        )
        daily_types = _describe(con, "src_depth_daily")
    return legacy_types, daily_types


# --------------------------------------------------------------------------------------
# Generic materialization: stage -> drop null keys -> dedupe -> CREATE TABLE AS SELECT ...
# ORDER BY primary key -> checks
# --------------------------------------------------------------------------------------


def _select_list(table: sc.Table, available: Mapping[str, str], *, transforms: bool = True) -> str:
    parts = []
    for c in table.columns:
        col = c if transforms else sc.Column(c.name, c.type)
        parts.append(col.sql(available))
    return ",\n  ".join(parts)


def _null_columns(table: sc.Table, available: Mapping[str, str]) -> list[str]:
    return [
        c.name for c in table.columns if c.derived is None and (c.source or c.name) not in available
    ]


def _pk(table: sc.Table) -> str:
    return ", ".join(sc.q(c) for c in table.primary_key)


def _materialize(
    con: duckdb.DuckDBPyConnection,
    table: sc.Table,
    stage_sql: str,
    stats: TableStats,
    arules: av.AvailabilityRules | None = None,
) -> TableStats:
    """Create ``table.name`` from ``stage_sql`` (a SELECT producing the spec's typed columns).

    Event tables (``available.TABLE_AVAILABILITY``) get ``available_at`` appended as the last
    column in the same statement; ``arules`` is required for them.
    """
    con.execute(f"CREATE OR REPLACE TEMP TABLE _stage AS {stage_sql}")
    stats.n_source_rows = con.execute("SELECT count(*) FROM _stage").fetchone()[0]
    src = "_stage"
    if table.distinct:
        con.execute("CREATE OR REPLACE TEMP TABLE _stage_d AS SELECT DISTINCT * FROM _stage")
        n_d = con.execute("SELECT count(*) FROM _stage_d").fetchone()[0]
        stats.n_dropped_duplicates += stats.n_source_rows - n_d
        src = "_stage_d"

    not_null = " AND ".join(f"{sc.q(c)} IS NOT NULL" for c in table.required_not_null) or "TRUE"
    n_kept = con.execute(f"SELECT count(*) FROM {src} WHERE {not_null}").fetchone()[0]
    n_all = con.execute(f"SELECT count(*) FROM {src}").fetchone()[0]
    stats.n_dropped_null_key = n_all - n_kept

    qualify = ""
    if table.dedupe_order:
        others = [c.name for c in table.columns if c.name not in table.primary_key]
        order = list(table.dedupe_order) + [f"{sc.q(c)} NULLS LAST" for c in others]
        qualify = (
            f" QUALIFY row_number() OVER "
            f"(PARTITION BY {_pk(table)} ORDER BY {', '.join(order)}) = 1"
        )
    body = f"SELECT * FROM {src} WHERE {not_null}{qualify}"
    avail = None
    a = av.TABLE_AVAILABILITY[table.name]
    time_col = av.AVAILABLE_AT if a.kind == "event" else a.row_visible_column
    if time_col is not None:
        if arules is None:
            raise ValueError(f"{table.name} has a time rule: availability rules are required")
        avail = (
            av.available_at_sql(table.name, arules)
            if a.kind == "event"
            else av.row_visibility_sql(table.name, arules)
        )
        extra = "".join(f"{expr} AS {sc.q(name)}, " for name, expr in avail.extra)
        body = f"SELECT s.*, {extra}{avail.expr} AS {sc.q(time_col)} FROM ({body}) s {avail.joins}"
    con.execute(f"DROP TABLE IF EXISTS {sc.q(table.name)}")
    con.execute(f"CREATE TABLE {sc.q(table.name)} AS SELECT * FROM ({body}) ORDER BY {_pk(table)}")
    con.execute("DROP TABLE IF EXISTS _stage")
    con.execute("DROP TABLE IF EXISTS _stage_d")
    stats.n_rows = con.execute(f"SELECT count(*) FROM {sc.q(table.name)}").fetchone()[0]
    stats.n_dropped_duplicates += n_kept - stats.n_rows
    assert_primary_key(con, table)
    if avail is not None:
        _check_available_at(con, table, avail, stats, time_col, allow_null=a.kind != "event")
    stats.content_hash = content_hash(con, table.name)
    if stats.n_dropped_null_key or stats.n_dropped_duplicates:
        log.info(
            "%s: dropped %d null-key and %d duplicate rows",
            table.name,
            stats.n_dropped_null_key,
            stats.n_dropped_duplicates,
        )
    return stats


def _check_available_at(
    con: duckdb.DuckDBPyConnection,
    table: sc.Table,
    avail: av.AvailabilitySQL,
    stats: TableStats,
    column: str = av.AVAILABLE_AT,
    *,
    allow_null: bool = False,
) -> None:
    """Refuse NULL ``available_at`` (naming table, count, examples); note rule-branch counts.

    ``allow_null``: a hindsight row time (``dim_player.public_from_utc``) may be NULL, which
    means "never visible point-in-time"."""
    name = sc.q(table.name)
    n_null = con.execute(f"SELECT count(*) FROM {name} WHERE {sc.q(column)} IS NULL").fetchone()[0]
    if n_null and not allow_null:
        cols = ", ".join(sc.q(c) for c in table.primary_key)
        extra = [c for c in ("season", "week", "game_type", "source_format") if c in
                 table.column_names and c not in table.primary_key]  # fmt: skip
        sel = ", ".join([cols, *(sc.q(c) for c in extra)])
        examples = con.execute(
            f"SELECT {sel} FROM {name} WHERE {sc.q(column)} IS NULL ORDER BY {cols} LIMIT 3"
        ).fetchall()
        raise av.AvailabilityError(
            f"{table.name}: {n_null} row(s) have no {column} (no rule could place them in "
            f"time, so no prediction could ever use them safely). Examples ({sel}): "
            f"{examples}. See docs/warehouse.md 'When is a row available?'."
        )
    if avail.branch or avail.flags:
        base = f"FROM {name} s {avail.joins}"
        if avail.branch:
            rows = con.execute(
                f"SELECT {avail.branch} AS b, count(*) {base} GROUP BY b ORDER BY b"
            ).fetchall()
            # every known branch is recorded, 0 included, so "0" and "not computed" differ
            counts = dict.fromkeys(sorted(avail.branches), 0)
            counts.update({b: n for b, n in rows})
            stats.notes[f"{column}_rule_counts"] = counts
        for note, cond in avail.flags:
            stats.notes[note] = con.execute(
                f"SELECT count(*) FILTER (WHERE {cond}) {base}"
            ).fetchone()[0]


def _note_after_week_asof(con: duckdb.DuckDBPyConnection, stats: dict[str, TableStats]) -> None:
    """``n_available_after_week_asof``: rows that are not yet available at their own week's
    Tuesday as-of (split-week games, late injury stamps, fired coaches' stints ...)."""
    for name, a in av.TABLE_AVAILABILITY.items():
        if a.kind != "event" or a.week_key is None:
            continue
        season, week = a.week_key
        stats[name].notes["n_available_after_week_asof"] = con.execute(
            f"SELECT count(*) FROM (SELECT {season} AS _season, {week} AS _week, available_at "
            f"FROM {sc.q(name)}) s JOIN dim_week w ON w.season = s._season AND w.week = s._week "
            "WHERE s.available_at > w.asof_weekly_utc"
        ).fetchone()[0]


def _assert_official_snapshots_complete(con: duckdb.DuckDBPyConnection) -> None:
    """Every game-data row of a non-split week must be visible at that week's Tuesday as-of,
    and every row of the regular-season finale at the end-of-season snapshot.

    The margins are small (7.6 h for a Monday-night game, 0 h for a 1999-2002 Monday-night
    finale at the end-of-season snapshot), so a larger lag in config would otherwise drop
    Monday-night games from the official snapshots silently.
    """
    problems = []
    for name in [*av.GAME_DATA_TABLES, *av.DERIVED_GAME_DATA_TABLES, "fact_game"]:
        weekly, finale = con.execute(f"""
            SELECT
              count(*) FILTER (WHERE NOT w.is_split_week AND x.available_at > w.asof_weekly_utc),
              count(*) FILTER (WHERE w.is_last_reg_week
                               AND x.available_at > w.asof_end_of_regular_season_utc)
            FROM {sc.q(name)} x JOIN fact_game g ON g.game_id = x.game_id
            JOIN dim_week w ON w.season = g.season AND w.week = g.week
             AND w.season_type = g.season_type""").fetchone()
        if weekly:
            problems.append(f"{name}: {weekly} row(s) of non-split weeks miss their Tuesday as-of")
        if finale:
            problems.append(f"{name}: {finale} finale row(s) miss the end-of-season snapshot")
    if problems:
        raise av.AvailabilityError(
            "the availability lags in config/settings.yaml (availability.game_data_lag_hours, "
            "game_result_lag_hours, estimated_kickoff_for_availability_et) push rows past an "
            "official as-of: " + "; ".join(problems) + ". Lower the lag or change the as-of."
        )


def assert_primary_key(con: duckdb.DuckDBPyConnection, table: sc.Table) -> None:
    """Raise PrimaryKeyError if the key is not unique (NULL-safe) or a required column is NULL."""
    name, pk = sc.q(table.name), _pk(table)
    for col in table.required_not_null:
        n = con.execute(f"SELECT count(*) FROM {name} WHERE {sc.q(col)} IS NULL").fetchone()[0]
        if n:
            raise PrimaryKeyError(f"{table.name}: key column {col!r} is NULL in {n} row(s)")
    dupes = con.execute(
        f"SELECT {pk}, count(*) AS n FROM {name} GROUP BY {pk} HAVING count(*) > 1 "
        f"ORDER BY n DESC LIMIT 3"
    ).fetchall()
    if dupes:
        n_groups = con.execute(
            f"SELECT count(*) FROM (SELECT 1 FROM {name} GROUP BY {pk} HAVING count(*) > 1)"
        ).fetchone()[0]
        raise PrimaryKeyError(
            f"{table.name}: primary key {table.primary_key} is violated: {n_groups} key "
            f"value(s) appear more than once, e.g. {dupes[0][:-1]} appears {dupes[0][-1]} "
            f"times. The source file has duplicate rows; see docs/warehouse.md 'Rows the build "
            f"drops' for how other tables handle this."
        )


def write_comments(con: duckdb.DuckDBPyConnection, table: sc.Table) -> None:
    """Store the spec's table and column docs in the file (``duckdb_columns().comment``),
    so any DuckDB client can read what a column means (``available_at``: its rule)."""
    con.execute(f"COMMENT ON TABLE {sc.q(table.name)} IS {sc.sql_str(table.doc)}")
    for c in table.columns:
        if c.doc:
            con.execute(
                f"COMMENT ON COLUMN {sc.q(table.name)}.{sc.q(c.name)} IS {sc.sql_str(c.doc)}"
            )
    a = av.TABLE_AVAILABILITY[table.name]
    for name, doc in a.extra_columns.items():
        con.execute(f"COMMENT ON COLUMN {sc.q(table.name)}.{sc.q(name)} IS {sc.sql_str(doc)}")
    if a.kind == "event":
        doc = f"UTC time this row counts as public (use it when available_at <= as_of). {a.rule}"
        con.execute(f"COMMENT ON COLUMN {sc.q(table.name)}.available_at IS {sc.sql_str(doc)}")


def content_hash(con: duckdb.DuckDBPyConnection, table_name: str) -> str:
    """Order-free, version-stable hash: md5 over the sorted per-row md5(text of the row)."""
    h = con.execute(
        f"SELECT md5(string_agg(h, '|' ORDER BY h)) FROM "
        f"(SELECT md5(CAST(t AS VARCHAR)) AS h FROM {sc.q(table_name)} t)"
    ).fetchone()[0]
    return h or "empty"


def _register(con: duckdb.DuckDBPyConnection, name: str, df: pl.DataFrame) -> dict[str, str]:
    con.register(name, df)
    return _describe(con, name)


def _frame(table: sc.Table, rows: list[dict[str, Any]]) -> pl.DataFrame:
    schema = {c.name: PL_TYPES[c.type] for c in table.columns}
    return pl.DataFrame({c: [r.get(c) for r in rows] for c in schema}, schema=schema, strict=False)


# --------------------------------------------------------------------------------------
# Table builders
# --------------------------------------------------------------------------------------


def _empty_select(table: sc.Table) -> str:
    cols = ", ".join(f"CAST(NULL AS {c.type}) AS {sc.q(c.name)}" for c in table.columns)
    return f"SELECT {cols} WHERE FALSE"


def _source_select(
    con: duckdb.DuckDBPyConnection, table: sc.Table, src: SourceFiles, stats: TableStats
) -> tuple[dict[str, str], str]:
    """Open the dataset view, note spec columns the files lack, return (types, SELECT sql)."""
    available = _source_view(con, table.source, src.paths[table.source])
    if nulls := _null_columns(table, available):
        stats.notes["null_columns"] = nulls
    return available, f"SELECT\n  {_select_list(table, available)}\nFROM src_{table.source}"


def _build_from_source(
    con: duckdb.DuckDBPyConnection,
    table: sc.Table,
    src: SourceFiles,
    stats: TableStats,
    arules: av.AvailabilityRules | None = None,
) -> TableStats:
    if not src.paths[table.source]:  # every requested season predates the dataset
        return _materialize(con, table, _empty_select(table), stats, arules)
    _, sql = _source_select(con, table, src, stats)
    return _materialize(con, table, sql, stats, arules)


def _without_computed(table: sc.Table) -> sc.Table:
    """The same spec minus the columns Python computes later (kickoff_utc, is_current ...)."""
    return dataclasses.replace(table, columns=tuple(c for c in table.columns if not c.computed))


def _build_fact_game(
    con: duckdb.DuckDBPyConnection,
    src: SourceFiles,
    stats: TableStats,
    arules: av.AvailabilityRules,
) -> TableStats:
    table = sc.tables()["fact_game"]
    _, sql = _source_select(con, _without_computed(table), src, stats)
    df = con.execute(sql).pl()
    kick, est, end, avail_end, home_it, away_it, stype = [], [], [], [], [], [], []
    cols = ("gameday", "gametime", "weekday", "spread_line", "total_line", "game_type")
    for gameday, gametime, weekday, spread, total, game_type in df.select(*cols).iter_rows():
        k, e = wk.kickoff_utc(gameday.isoformat(), gametime, weekday)
        kick.append(k)
        est.append(e)
        end.append(k + wk.GAME_DURATION_EST)
        avail_end.append(av.availability_game_end(gameday, end[-1], e, arules))
        h, a = wk.implied_totals(spread, total)
        home_it.append(h)
        away_it.append(a)
        stype.append(wk.season_type_of(game_type))
    df = df.with_columns(
        pl.Series("season_type", stype, dtype=pl.String),
        pl.Series("kickoff_utc", kick, dtype=pl.Datetime("us")),
        pl.Series("kickoff_is_estimated", est, dtype=pl.Boolean),
        pl.Series("game_end_utc_est", end, dtype=pl.Datetime("us")),
        pl.Series("home_implied_total", home_it, dtype=pl.Float64),
        pl.Series("away_implied_total", away_it, dtype=pl.Float64),
        pl.Series("availability_game_end_utc", avail_end, dtype=pl.Datetime("us")),
    )
    stats.notes["n_kickoff_estimated"] = int(sum(est))
    # H1b: the cited head-coach corrections, before any coach table reads these columns
    # (gameday is the US-Eastern date of the kickoff).
    df, applied = cc.apply_corrections(df, cc.read_corrections(cc.corrections_path()))
    stats.notes["coach_corrections"] = applied
    stats.notes["n_coach_team_games_corrected"] = sum(a["games"] for a in applied)
    for a in applied:
        log.info("fact_game: coach correction line %s (%s) changed %s team-games",
                 a["line"], a["kind"], a["games"])  # fmt: skip
    types = _register(con, "df_fact_game", df)
    sql = f"SELECT\n  {_select_list(table, types, transforms=False)}\nFROM df_fact_game"
    out = _materialize(con, table, sql, stats, arules)
    con.unregister("df_fact_game")
    return out


def _build_fact_opportunity_week(
    con: duckdb.DuckDBPyConnection,
    src: SourceFiles,
    stats: TableStats,
    arules: av.AvailabilityRules,
) -> TableStats:
    """ffopportunity's weekly expected stats (C3). ``season_type`` comes from the game's
    fact_game row; rows without a player id (team rows upstream) are dropped and counted."""
    table = sc.tables()["fact_opportunity_week"]
    if not src.paths[table.source]:  # every requested season predates 2006
        return _materialize(con, table, _empty_select(table), stats, arules)
    _, inner = _source_select(con, _without_computed(table), src, stats)
    cols = ", ".join(
        "g.season_type AS season_type" if c.computed else f"s.{sc.q(c.name)}" for c in table.columns
    )
    sql = f"SELECT {cols} FROM ({inner}) s LEFT JOIN fact_game g ON g.game_id = s.game_id"
    return _materialize(con, table, sql, stats, arules)


# The player column of each per-play expected-values table, compared with fact_play's.
_OPPORTUNITY_PLAY_PLAYERS = {
    "fact_opportunity_pass": ("passer_player_id", "receiver_player_id"),
    "fact_opportunity_rush": ("rusher_player_id",),
}


def _build_fact_opportunity_play(
    con: duckdb.DuckDBPyConnection,
    name: str,
    src: SourceFiles,
    stats: TableStats,
    arules: av.AvailabilityRules,
) -> TableStats:
    """ffopportunity's per-play expected values (D1, ``fact_opportunity_pass`` /
    ``fact_opportunity_rush``). ``season_type`` comes from the game's fact_game row and
    ``is_garbage_time`` from the same play in fact_play (built before). The manifest notes how
    many rows have no fact_play play (``n_not_in_fact_play``) and how often the player ids
    differ from fact_play's for the same play (``n_<column>_differs_from_fact_play``)."""
    table = sc.tables()[name]
    if not src.paths[table.source]:  # every requested season predates 2006
        out = _materialize(con, table, _empty_select(table), stats, arules)
    else:
        _, inner = _source_select(con, _without_computed(table), src, stats)
        computed = {
            "season_type": "g.season_type AS season_type",
            "is_garbage_time": "f.is_garbage_time AS is_garbage_time",
        }
        cols = ", ".join(
            computed[c.name] if c.computed else f"s.{sc.q(c.name)}" for c in table.columns
        )
        sql = (
            f"SELECT {cols} FROM ({inner}) s LEFT JOIN fact_game g ON g.game_id = s.game_id "
            "LEFT JOIN fact_play f ON f.game_id = s.game_id AND f.play_id = s.play_id"
        )
        out = _materialize(con, table, sql, stats, arules)
    diff = ", ".join(
        f"count(*) FILTER (WHERE f.play_id IS NOT NULL AND o.{c} IS DISTINCT FROM f.{c})"
        for c in _OPPORTUNITY_PLAY_PLAYERS[name]
    )
    row = con.execute(
        f"SELECT count(*) FILTER (WHERE f.play_id IS NULL), {diff} FROM {sc.q(name)} o "
        "LEFT JOIN fact_play f ON f.game_id = o.game_id AND f.play_id = o.play_id"
    ).fetchone()
    stats.notes["n_not_in_fact_play"] = int(row[0])
    for c, n in zip(_OPPORTUNITY_PLAY_PLAYERS[name], row[1:], strict=True):
        stats.notes[f"n_{c}_differs_from_fact_play"] = int(n)
    return out


def _build_fact_kicker_week(
    con: duckdb.DuckDBPyConnection, stats: TableStats, arules: av.AvailabilityRules
) -> TableStats:
    """S1: fact_player_week's kicking columns, one row per player-game with an attempt."""
    table = sc.tables()["fact_kicker_week"]
    cols = ", ".join(sc.q(c) for c in table.column_names)
    sql = (
        f"SELECT {cols} FROM fact_player_week WHERE COALESCE(fg_att, 0) + COALESCE(pat_att, 0) > 0"
    )
    return _materialize(con, table, sql, stats, arules)


# How a touchdown play is classified for D/ST scoring (docs/warehouse.md, fact_defense_week).
# The play_type fallbacks cover rows whose *_attempt flag is 0 (e.g. 97 kickoffs in 2001-2002).
PLAY_KIND_SQL = """CASE
    WHEN kickoff_attempt = 1 OR play_type = 'kickoff' THEN 'kickoff'
    WHEN punt_attempt = 1 OR play_type = 'punt' THEN 'punt'
    WHEN field_goal_attempt = 1 OR play_type = 'field_goal' THEN 'field_goal'
    WHEN extra_point_attempt = 1 OR two_point_attempt = 1 OR play_type = 'extra_point'
        THEN 'conversion'
    ELSE 'scrimmage' END"""
# fact_play has no punt_blocked column: nflfastR writes "BLOCKED" (upper case) in the description.
BLOCKED_KICK_SQL = """(COALESCE(field_goal_result = 'blocked', FALSE)
    OR (COALESCE(punt_attempt = 1 OR play_type = 'punt', FALSE)
        AND COALESCE("desc" LIKE '%BLOCKED%', FALSE)))"""
DST_TOUCHDOWNS_SQL = f"""
    WITH p AS (
        SELECT game_id, posteam, defteam, td_team, interception,
               {PLAY_KIND_SQL} AS kind, {BLOCKED_KICK_SQL} AS blocked
        FROM fact_play WHERE touchdown = 1 AND td_team IS NOT NULL)
    SELECT game_id, td_team AS team,
        count(*) FILTER (kind = 'kickoff' AND td_team = posteam
            OR kind = 'field_goal' AND td_team = defteam AND NOT blocked) AS kickoff_return_tds,
        count(*) FILTER (kind = 'punt' AND td_team = defteam AND NOT blocked) AS punt_return_tds,
        count(*) FILTER (kind = 'scrimmage' AND td_team = defteam AND interception = 1)
            AS interception_return_tds,
        count(*) FILTER (kind = 'scrimmage' AND td_team = defteam
                AND COALESCE(interception, 0) = 0
            OR kind = 'kickoff' AND td_team = defteam
            OR kind = 'punt' AND td_team = posteam) AS fumble_return_tds,
        count(*) FILTER (kind IN ('punt', 'field_goal') AND td_team = defteam AND blocked)
            AS blocked_kick_return_tds,
        count(*) FILTER (kind = 'scrimmage' AND td_team = defteam) AS scrimmage_defense_tds
    FROM p GROUP BY game_id, td_team"""


def _build_fact_defense_week(
    con: duckdb.DuckDBPyConnection, stats: TableStats, arules: av.AvailabilityRules
) -> TableStats:
    """S1: one D/ST row per fact_team_week row (see the fact_defense_week spec)."""
    table = sc.tables()["fact_defense_week"]
    counts = ", ".join(f"t.{sc.q(c)}" for c in sc.DEFENSE_WEEK_FROM_TEAM_STATS)
    tds = ", ".join(f"COALESCE(d.{c}, 0) AS {c}" for c in sc.DEFENSE_WEEK_TDS)
    against = (
        "CASE WHEN g.home_team = t.team THEN g.away_score "
        "WHEN g.away_team = t.team THEN g.home_score END"
    )
    sql = f"""
        WITH td AS ({DST_TOUCHDOWNS_SQL})
        SELECT t.game_id, t.season, t.week, t.season_type, t.team, t.opponent_team,
               {counts}, {tds},
               {against} AS points_scored_against,
               COALESCE(o.scrimmage_defense_tds, 0) AS offense_giveaway_tds,
               {against} - 6 * COALESCE(o.scrimmage_defense_tds, 0) AS points_allowed,
               ot.passing_yards + ot.sack_yards_lost + ot.rushing_yards AS yards_allowed
        FROM fact_team_week t
        LEFT JOIN fact_game g ON g.game_id = t.game_id
        LEFT JOIN td d ON d.game_id = t.game_id AND d.team = t.team
        LEFT JOIN td o ON o.game_id = t.game_id AND o.team = t.opponent_team
        LEFT JOIN fact_team_week ot ON ot.game_id = t.game_id AND ot.team = t.opponent_team"""
    cols = ", ".join(f"CAST({sc.q(c.name)} AS {c.type}) AS {sc.q(c.name)}" for c in table.columns)
    return _materialize(con, table, f"SELECT {cols} FROM ({sql})", stats, arules)


def _build_fact_schedule(
    con: duckdb.DuckDBPyConnection, stats: TableStats, arules: av.AvailabilityRules
) -> TableStats:
    """The pre-game view of fact_game: only the columns public when the schedule is out."""
    table = sc.tables()["fact_schedule"]
    cols = ", ".join(sc.q(c) for c in table.column_names)
    out = _materialize(con, table, f"SELECT {cols} FROM fact_game", stats, arules)
    # Listed schedule changes of the built seasons: cancelled games (absent from nflverse,
    # feature code must add them back for "games remaining" before the announcement) and
    # listed games the schedule does not have (a typo in the config, or an nflverse change).
    built = {r[0] for r in con.execute("SELECT DISTINCT season FROM fact_game").fetchall()}
    ids = {r[0] for r in con.execute("SELECT game_id FROM fact_game").fetchall()}
    listed = [x for x in arules.schedule_exceptions if _season_of(x.game_id) in built]
    cancelled = [x.game_id for x in listed if x.cancelled]
    missing = [x.game_id for x in listed if not x.cancelled and x.game_id not in ids]
    if cancelled:
        out.notes["cancelled_games"] = cancelled
    if missing:
        out.notes["schedule_exceptions_not_found"] = missing
        log.warning("fact_schedule: listed schedule exceptions not in the schedule: %s", missing)
    return out


def _season_of(game_id: str) -> int | None:
    head = game_id.split("_", 1)[0]
    return int(head) if head.isdigit() else None


def _build_dim_week(
    con: duckdb.DuckDBPyConnection, rules: wk.AsOfRules, stats: TableStats
) -> TableStats:
    table = sc.DIM_WEEK
    games = con.execute(
        "SELECT season, week, game_type, gameday, kickoff_utc, kickoff_is_estimated, "
        "game_end_utc_est FROM fact_game"
    ).pl()
    rows = wk.build_dim_week(games.to_dicts(), rules)
    types = _register(con, "df_dim_week", _frame(table, rows))
    out = _materialize(
        con, table, f"SELECT\n  {_select_list(table, types)}\nFROM df_dim_week", stats
    )
    con.unregister("df_dim_week")
    return out


SLUG_SQL = "trim(regexp_replace(lower({x}), '[^a-z0-9]+', '_', 'g'), '_')"
# Canonical spelling of a coach name: surrounding/repeated whitespace collapsed.
CLEAN_NAME_SQL = r"regexp_replace(trim({x}), '\s+', ' ', 'g')"
COACH_NAMES_SQL = """(
            SELECT home_coach AS coach_name FROM fact_game
            UNION SELECT away_coach FROM fact_game
        ) WHERE coach_name IS NOT NULL"""


def _build_coaches(
    con: duckdb.DuckDBPyConnection, stats: dict[str, TableStats], arules: av.AvailabilityRules
) -> None:
    slug_home, slug_away = SLUG_SQL.format(x="home_coach"), SLUG_SQL.format(x="away_coach")
    coach_game_sql = f"""
        SELECT game_id, season, week, season_type, home_team AS team,
               {slug_home} AS coach_id, TRUE AS is_home
        FROM fact_game WHERE home_coach IS NOT NULL
        UNION ALL
        SELECT game_id, season, week, season_type, away_team AS team,
               {slug_away} AS coach_id, FALSE AS is_home
        FROM fact_game WHERE away_coach IS NOT NULL"""
    _materialize(con, sc.COACH_GAME, coach_game_sql, stats["coach_game"], arules)
    # Two spellings of one name ('Sean McVay' / 'Sean Mcvay', a double space) share a slug and
    # therefore a coach_id; the bridge tables already merge them, so dim_coach must too. The
    # canonical coach_name is the whitespace-collapsed, alphabetically (byte order) first
    # spelling; every merge is recorded in the manifest and logged so the owner can see it.
    slug_name = SLUG_SQL.format(x="coach_name")
    merged = con.execute(f"""
        SELECT coach_id, list(coach_name) FROM (
            SELECT DISTINCT {slug_name} AS coach_id, coach_name FROM {COACH_NAMES_SQL}
        ) GROUP BY coach_id HAVING count(*) > 1 ORDER BY coach_id""").fetchall()
    if merged:
        spellings = {cid: sorted(names) for cid, names in merged}
        stats["dim_coach"].notes["merged_spellings"] = spellings
        log.warning("dim_coach: merged spellings that share a coach_id: %s", spellings)
    dim_coach_sql = f"""
        SELECT coach_id, min(coach_name) AS coach_name FROM (
            SELECT {slug_name} AS coach_id, {CLEAN_NAME_SQL.format(x="coach_name")} AS coach_name
            FROM {COACH_NAMES_SQL}
        ) GROUP BY coach_id"""
    _materialize(con, sc.DIM_COACH, dim_coach_sql, stats["dim_coach"], arules)
    cts_sql = """
        SELECT coach_id, team, season, CAST(min(week) AS INTEGER) AS first_week,
               CAST(max(week) AS INTEGER) AS last_week, CAST(count(*) AS INTEGER) AS n_games
        FROM coach_game GROUP BY coach_id, team, season"""
    _materialize(con, sc.COACH_TEAM_SEASON, cts_sql, stats["coach_team_season"], arules)
    # How many team-seasons show more than one coach after the corrections (nflverse's own
    # columns record in-season changes only through 2023: docs/assumptions.md section 10).
    stats["coach_team_season"].notes["n_team_seasons_multi_coach"] = con.execute(
        "SELECT count(*) FROM (SELECT team, season FROM coach_team_season "
        "GROUP BY team, season HAVING count(*) > 1)"
    ).fetchone()[0]


def _week_at_dt_frame(con: duckdb.DuckDBPyConnection, pairs: pl.DataFrame) -> pl.DataFrame:
    """Map each distinct (season, dt) of the daily depth charts to a dim_week week.

    Delegates to :func:`twm.warehouse.weeks.week_for_timestamp`, the single implementation of
    the window rule (window_start < dt <= as-of, inclusive at the as-of; before week 1 -> week
    1; after the season's last as-of -> NULL). ``_assert_week_windows`` has already run, so the
    as-ofs are strictly increasing in week number, which that rule relies on.
    """
    dim = con.execute("SELECT season, week, asof_weekly_utc FROM dim_week").pl().to_dicts()
    out = [
        None if dt is None else wk.week_for_timestamp(dim, int(season), dt)
        for season, dt in pairs.iter_rows()
    ]
    return pairs.with_columns(pl.Series("week_at_dt", out, dtype=pl.Int32))


def _build_fact_depth_chart(
    con: duckdb.DuckDBPyConnection,
    src: SourceFiles,
    stats: TableStats,
    arules: av.AvailabilityRules,
) -> TableStats:
    table = sc.tables()["fact_depth_chart"]
    legacy_types, daily_types = _depth_chart_views(
        con, src.paths["depth_charts"], src.seasons_used["depth_charts"]
    )
    parts = []
    if legacy_types is not None:
        t = sc.cast_sql
        parts.append(f"""
        SELECT
          {t("season", "INTEGER", legacy_types["season"])} AS season,
          'legacy' AS source_format,
          {t("week", "INTEGER", legacy_types["week"])} AS week,
          CAST(NULL AS INTEGER) AS week_at_dt,
          CAST(game_type AS VARCHAR) AS game_type,
          {sc.season_type_sql("game_type")} AS season_type,
          CAST(NULL AS TIMESTAMP) AS dt,
          {sc.normalize_team_sql("club_code")} AS team,
          CAST(club_code AS VARCHAR) AS team_raw,
          NULLIF(CAST(gsis_id AS VARCHAR), '') AS gsis_id,
          CAST(NULL AS VARCHAR) AS espn_id,
          FALSE AS gsis_id_from_espn,
          COALESCE(full_name, first_name || ' ' || last_name) AS player_name,
          CAST(position AS VARCHAR) AS player_position,
          {sc.unit_sql("formation", sc.UNIT_LEGACY)} AS unit,
          CAST(formation AS VARCHAR) AS unit_raw,
          CAST(depth_position AS VARCHAR) AS position,
          CAST(NULL AS INTEGER) AS pos_slot,
          {t("depth_team", "INTEGER", legacy_types["depth_team"])} AS depth_rank
        FROM src_depth_legacy""")
    if daily_types is not None:
        dt_expr = sc.cast_sql("dt", "TIMESTAMP", daily_types["dt"])
        dt_expr_d = sc.cast_sql("dt", "TIMESTAMP", daily_types["dt"], alias="d")
        espn = pid.canonical_id_sql("d.espn_id", daily_types["espn_id"])
        src_gsis = "NULLIF(trim(CAST(d.gsis_id AS VARCHAR)), '')"
        pairs = con.execute(
            f"SELECT DISTINCT file_season AS season, {dt_expr} AS dt FROM src_depth_daily"
        ).pl()
        _register(con, "df_week_at_dt", _week_at_dt_frame(con, pairs))
        parts.append(f"""
        SELECT
          CAST(d.file_season AS INTEGER) AS season,
          'daily' AS source_format,
          CAST(NULL AS INTEGER) AS week,
          w.week_at_dt AS week_at_dt,
          CAST(NULL AS VARCHAR) AS game_type,
          CAST(NULL AS VARCHAR) AS season_type,
          {dt_expr_d} AS dt,
          {sc.normalize_team_sql("d.team")} AS team,
          CAST(d.team AS VARCHAR) AS team_raw,
          COALESCE({src_gsis}, b.gsis_id) AS gsis_id,
          {espn} AS espn_id,
          ({src_gsis} IS NULL AND b.gsis_id IS NOT NULL) AS gsis_id_from_espn,
          CAST(d.player_name AS VARCHAR) AS player_name,
          CAST(NULL AS VARCHAR) AS player_position,
          {sc.unit_sql("d.pos_grp", sc.UNIT_DAILY)} AS unit,
          CAST(d.pos_grp AS VARCHAR) AS unit_raw,
          CAST(d.pos_abb AS VARCHAR) AS position,
          CAST(d.pos_slot AS INTEGER) AS pos_slot,
          CAST(d.pos_rank AS INTEGER) AS depth_rank
        FROM src_depth_daily d
        LEFT JOIN df_week_at_dt w
          ON w.season = d.file_season AND w.dt = {dt_expr_d}
        LEFT JOIN _bridge b ON b.id_type = 'espn' AND b.source_id = {espn}""")
    if not parts:
        parts.append(_empty_select(table))
    sql = " UNION ALL BY NAME ".join(f"({p})" for p in parts)
    out = _materialize(con, table, f"SELECT * FROM ({sql})", stats, arules)
    if daily_types is not None:
        con.unregister("df_week_at_dt")
    n_other = con.execute("SELECT count(*) FROM fact_depth_chart WHERE unit = 'other'").fetchone()[
        0
    ]
    if n_other:
        stats.notes["n_unit_other"] = n_other
        log.warning("fact_depth_chart: %d rows with unknown unit (stored as 'other')", n_other)
    if daily_types is not None:
        filled, still = con.execute(
            "SELECT count(*) FILTER (WHERE gsis_id_from_espn), "
            "count(*) FILTER (WHERE gsis_id IS NULL AND espn_id IS NOT NULL) "
            "FROM fact_depth_chart WHERE source_format = 'daily'"
        ).fetchone()
        stats.notes["n_daily_gsis_filled_from_espn"] = filled
        stats.notes["n_daily_espn_without_gsis"] = still
    return out


# --------------------------------------------------------------------------------------
# Weekly rosters and FantasyPros rankings (C1: the Waiver Radar candidate pool)
# --------------------------------------------------------------------------------------


def _build_fact_roster_week(
    con: duckdb.DuckDBPyConnection,
    src: SourceFiles,
    stats: TableStats,
    arules: av.AvailabilityRules,
) -> TableStats:
    """Weekly rosters; ``available_at`` depends on each season's snapshot regime, measured
    here from the data (:func:`twm.warehouse.available.roster_regime_sql`). Needs fact_snaps
    and fact_player_week (who played) and dim_week."""
    table = sc.tables()["fact_roster_week"]
    regime = av.ROSTER_REGIME_RELATION
    if not src.paths["rosters_weekly"]:  # every requested season predates 2002
        con.execute(
            f"CREATE OR REPLACE TEMP TABLE {regime} AS SELECT CAST(NULL AS INTEGER) AS season, "
            "CAST(NULL AS VARCHAR) AS regime WHERE FALSE"
        )
        return _materialize(con, table, _empty_select(table), stats, arules)
    types, sql = _source_select(con, table, src, stats)
    status = "CAST(status AS VARCHAR)" if "status" in types else "CAST(NULL AS VARCHAR)"
    staged = (
        f"(SELECT {sc.cast_sql('season', 'INTEGER', types['season'])} AS season, "
        f"{sc.cast_sql('week', 'INTEGER', types['week'])} AS week, "
        f"NULLIF(trim(CAST(gsis_id AS VARCHAR)), '') AS gsis_id, {status} AS status "
        "FROM src_rosters_weekly)"
    )
    con.execute(f"CREATE OR REPLACE TEMP TABLE {regime} AS {av.roster_regime_sql(staged, arules)}")
    out = _materialize(con, table, sql, stats, arules)
    rows = con.execute(
        f"SELECT season, regime, share_not_act, n_played_on_roster, n_played_not_act "
        f"FROM {regime} ORDER BY season"
    ).fetchall()
    out.notes["regime_threshold"] = arules.roster_postgame_share_threshold
    out.notes["regime_by_season"] = {
        str(season): {
            "regime": reg,
            "share_not_act": None if share is None else round(share, 6),
            "n_played_on_roster": n,
            "n_played_not_act": n_not,
        }
        for season, reg, share, n, n_not in rows
    }
    known = "SELECT current_abbr FROM dim_team"
    out.notes["n_rows_unknown_team"] = con.execute(
        f"SELECT count(*) FROM fact_roster_week WHERE team IS NOT NULL AND team NOT IN ({known})"
    ).fetchone()[0]
    if out.notes["n_rows_unknown_team"]:
        log.warning(
            "fact_roster_week: %d rows with a team code that is not in dim_team",
            out.notes["n_rows_unknown_team"],
        )
    return out


def _build_fact_ranking(
    con: duckdb.DuckDBPyConnection,
    src: SourceFiles,
    seasons: list[int],
    stats: TableStats,
    arules: av.AvailabilityRules,
    *,
    kdst: bool = False,
) -> TableStats:
    """FantasyPros positional rankings (redraft cheat sheets, rest of season, weekly) of the
    built seasons, QB/RB/WR/TE only, with gsis_id through ``_bridge`` and pos_rank.
    ``kdst=True`` builds fact_ranking_kdst the same way from the K and DST pages (S1b), plus
    ``nfl_team`` (the team normalized like every warehouse team column).

    The page classification is :func:`twm.ids.ranking_page_kind_sql` (shared with the id
    report). Exact duplicates on a page (the archive repeats some rows) keep the lowest ecr,
    then the page name, then every other column; pos_rank is computed after that."""
    table = sc.tables()["fact_ranking_kdst" if kdst else "fact_ranking"]
    types = _source_view(con, "ff_rankings_all", src.paths["ff_rankings_all"])
    date = "CAST(NULLIF(trim(CAST(scrape_date AS VARCHAR)), '') AS DATE)"
    positions = ", ".join(f"'{p}'" for p in (STREAMER_POSITIONS if kdst else FANTASY_POSITIONS))
    what = "k_dst" if kdst else "qb_rb_wr_te"
    in_seasons = ", ".join(str(int(x)) for x in seasons) or "NULL"
    kind = pid.ranking_page_kind_sql("ecr_type", "fp_page", "page_type", kdst=kdst)
    pos = "CAST(pos AS VARCHAR)"
    if kdst:  # the 2019-12 to 2020-10 short-form K pages call a kicker 'PK'
        pos = f"(CASE WHEN {pos} = 'PK' THEN 'K' ELSE {pos} END)"

    def col(name: str, target: str) -> str:  # a column the file lacks is NULL (like Column.sql)
        return (
            sc.cast_sql(name, target, types[name]) if name in types else (f"CAST(NULL AS {target})")
        )

    num = {c: col(c, "DOUBLE") for c in
           ("ecr", "sd", "best", "worst", "player_owned_espn", "player_owned_yahoo",
            "player_owned_avg")}  # fmt: skip
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _rank_src AS
        SELECT {date} AS scrape_date,
               CAST(CASE WHEN month({date}) >= 3 THEN year({date})
                         ELSE year({date}) - 1 END AS INTEGER) AS season,
               CAST(ecr_type AS VARCHAR) AS ecr_type,
               {kind} AS page_kind,
               {pid.ranking_page_pos_sql("fp_page", kdst=kdst)} AS page_pos,
               CAST(fp_page AS VARCHAR) AS fp_page,
               CAST(page_type AS VARCHAR) AS page_type,
               {pid.canonical_id_sql("id", types["id"])} AS fantasypros_id,
               {col("player", "VARCHAR")} AS player, {pos} AS pos,
               {col("team", "VARCHAR")} AS team,
               {", ".join(f"{e} AS {sc.q(c)}" for c, e in num.items())}
        FROM src_ff_rankings_all
        WHERE ecr_type IN ('rp', 'wp')""")
    reasons = dict(
        con.execute(f"""
        SELECT CASE WHEN season NOT IN ({in_seasons}) OR season IS NULL THEN 'season_not_built'
                    WHEN page_kind IS NULL THEN 'not_a_{what}_page'
                    WHEN pos IS NULL OR pos NOT IN ({positions}) THEN 'not_{what}_player'
                    WHEN fantasypros_id IS NULL THEN 'no_id'
                    ELSE 'kept' END AS reason, count(*)
        FROM _rank_src GROUP BY 1 ORDER BY 1""").fetchall()
    )
    kept = (
        f"season IN ({in_seasons}) AND page_kind IS NOT NULL AND pos IN ({positions}) "
        "AND fantasypros_id IS NOT NULL"
    )
    page = "scrape_date, ecr_type, page_kind, page_pos"
    others = ", ".join(f"{sc.q(c)} NULLS LAST" for c in
                       ("player", "pos", "team", *num, "season", "page_type"))  # fmt: skip
    own = "CASE WHEN pos = page_pos THEN 1 ELSE 0 END"
    cols = ", ".join(
        {
            "gsis_id": "b.gsis_id AS gsis_id",
            "pos_rank": "CAST(CASE WHEN r.ecr IS NOT NULL THEN r._own_le - r._own_eq + 1 END "
            "AS INTEGER) AS pos_rank",
            "nfl_team": "NULLIF("
            + sc.normalize_team_sql("r.team", sc.RANKING_TEAM_ALIASES)
            + ", 'FA') AS nfl_team",
        }.get(c.name, f"r.{sc.q(c.name)}")
        for c in table.columns
    )
    sql = f"""
        SELECT {cols} FROM (
            SELECT d.*,
                   sum({own}) OVER (PARTITION BY {page} ORDER BY ecr
                       RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS _own_le,
                   sum({own}) OVER (PARTITION BY {page}, ecr) AS _own_eq
            FROM (
                SELECT * FROM _rank_src WHERE {kept}
                QUALIFY row_number() OVER (
                    PARTITION BY {page}, fantasypros_id
                    ORDER BY ecr NULLS LAST, fp_page NULLS LAST, {others}) = 1
            ) d
        ) r
        LEFT JOIN _bridge b ON b.id_type = 'fantasypros' AND b.source_id = r.fantasypros_id"""
    n_dupes = (
        reasons.get("kept", 0)
        - con.execute(
            f"SELECT count(*) FROM (SELECT DISTINCT {page}, fantasypros_id FROM _rank_src "
            f"WHERE {kept})"
        ).fetchone()[0]
    )
    out = _materialize(con, table, sql, stats, arules)
    con.execute("DROP TABLE IF EXISTS _rank_src")
    out.n_source_rows = sum(reasons.values())
    out.n_dropped_duplicates = n_dupes
    out.n_dropped_null_key = out.n_source_rows - reasons.get("kept", 0)
    out.seasons = sorted(seasons)
    out.notes["rows_by_reason"] = reasons  # rp/wp rows of the archive: kept or why not
    out.notes["n_rows_other_ecr_types"] = con.execute(
        "SELECT count(*) FROM src_ff_rankings_all WHERE ecr_type NOT IN ('rp', 'wp') "
        "OR ecr_type IS NULL"
    ).fetchone()[0]
    out.notes["n_rows_by_page_kind"] = dict(
        con.execute(
            f"SELECT page_kind, count(*) FROM {table.name} GROUP BY 1 ORDER BY 1"
        ).fetchall()
    )
    out.notes["n_rows_without_gsis_id"] = con.execute(
        f"SELECT count(*) FROM {table.name} WHERE gsis_id IS NULL"
    ).fetchone()[0]
    out.notes["n_rows_listed_on_another_positions_page"] = con.execute(
        f"SELECT count(*) FROM {table.name} WHERE pos <> page_pos"
    ).fetchone()[0]
    if kdst:
        out.notes["n_rows_without_nfl_team_by_pos"] = dict(
            con.execute(
                f"SELECT pos, count(*) FILTER (WHERE nfl_team IS NULL) FROM {table.name} "
                "GROUP BY 1 ORDER BY 1"
            ).fetchall()
        )
    return out


# --------------------------------------------------------------------------------------
# Player ids (B3, twm.ids)
# --------------------------------------------------------------------------------------


def _stage_player_ids(con: duckdb.DuckDBPyConnection, src: SourceFiles, stats: TableStats) -> None:
    """Stage ``_players`` and ``_bridge`` (twm.ids) before the tables that use them."""
    ptypes = _source_view(con, "players", src.paths["players"])
    pid.stage_players(con, "src_players", ptypes)
    known = [r[0] for r in con.execute("SELECT gsis_id FROM _players").fetchall()]
    overrides = pid.read_overrides(pid.overrides_path(), known)
    ff = None
    if src.paths["ff_playerids"]:
        ff = ("src_ff_playerids", _source_view(con, "ff_playerids", src.paths["ff_playerids"]))
    rw = None
    if src.paths["rosters_weekly"]:
        rw = (
            "src_rosters_weekly",
            _source_view(con, "rosters_weekly", src.paths["rosters_weekly"]),
        )
    stats.notes.update(pid.stage_bridge(con, pid.BridgeInputs(("src_players", ptypes), ff, rw,
                                                              overrides)))  # fmt: skip


def _build_fact_snaps(
    con: duckdb.DuckDBPyConnection,
    src: SourceFiles,
    stats: TableStats,
    arules: av.AvailabilityRules,
) -> TableStats:
    """Snap counts with ``gsis_id`` mapped from the PFR id through ``_bridge``.

    First the usage check (twm.ids.stage_usage_checks): a link whose player's career does not
    fit the seasons the PFR id is used in is re-linked or, for those rows, left NULL."""
    table = sc.tables()["fact_snaps"]
    if not src.paths["snap_counts"]:
        return _materialize(con, table, _empty_select(table), stats, arules)
    types, inner = _source_select(con, _without_computed(table), src, stats)
    pfr = pid.canonical_id_sql("s.pfr_player_id", "VARCHAR")
    usage = f"SELECT s.season, {pfr}, s.position FROM ({inner}) s"
    stats.notes.update(pid.stage_usage_checks(con, "fact_snaps", "pfr", usage))
    cols = ", ".join(
        "CASE WHEN x.source_id IS NULL THEN b.gsis_id END AS gsis_id"
        if c.computed
        else f"s.{sc.q(c.name)}"
        for c in table.columns
    )
    sql = (
        f"SELECT {cols} FROM ({inner}) s "
        f"LEFT JOIN _bridge b ON b.id_type = 'pfr' AND b.source_id = {pfr} "
        "LEFT JOIN _usage_null x ON x.dataset = 'fact_snaps' AND x.id_type = 'pfr' "
        f"AND x.source_id = {pfr} AND x.season = s.season"
    )
    out = _materialize(con, table, sql, stats, arules)
    out.notes["n_rows_without_gsis_id"] = con.execute(
        "SELECT count(*) FROM fact_snaps WHERE gsis_id IS NULL"
    ).fetchone()[0]
    # feature joins use (game_id, gsis_id): two PFR ids of one player in one game would double
    # him, so the build counts such groups (0 expected) and warns
    n_dup = con.execute(
        "SELECT count(*) FROM (SELECT 1 FROM fact_snaps WHERE gsis_id IS NOT NULL "
        "GROUP BY game_id, gsis_id HAVING count(*) > 1)"
    ).fetchone()[0]
    out.notes["n_duplicate_game_gsis"] = n_dup
    if n_dup:
        log.warning(
            "fact_snaps: %d (game_id, gsis_id) pairs appear more than once (two PFR ids of "
            "one player in one game); see report_id_unmatched kind 'multiple_ids'",
            n_dup,
        )
    return out


def _build_dim_player(
    con: duckdb.DuckDBPyConnection,
    src: SourceFiles,
    stats: TableStats,
    arules: av.AvailabilityRules,
) -> TableStats:
    """nflverse's players plus one id per system from ``_bridge`` (twm.ids)."""
    table = sc.tables()["dim_player"]
    _, inner = _source_select(con, _without_computed(table), src, stats)
    cols = ", ".join(
        f"i.{sc.q(c.name)}" if c.computed else f"p.{sc.q(c.name)}" for c in table.columns
    )
    sql = (
        f"SELECT {cols} FROM ({inner}) p "
        f"LEFT JOIN ({pid.dim_player_ids_sql()}) i ON i.gsis_id = p.gsis_id"
    )
    out = _materialize(con, table, sql, stats, arules)
    # how many espn/pfr ids come from somewhere else than nflverse's players table
    for t in ("pfr", "espn"):
        out.notes[f"n_{t}_id_not_from_players"] = con.execute(
            f"SELECT count(*) FROM dim_player d JOIN _bridge b ON b.id_type = '{t}' "
            f"AND b.source_id = d.{t}_id WHERE b.method <> 'players'"
        ).fetchone()[0]
    out.notes["n_players_with_id"] = {
        c: con.execute(f"SELECT count({sc.q(c)}) FROM dim_player").fetchone()[0]
        for c in ("pfr_id", "espn_id", *sc.DIM_PLAYER_ID_COLUMNS)
    }
    out.notes["n_players_with_several_ids"] = pid.stage_multiple_ids(con)
    return out


def _build_id_reports(
    con: duckdb.DuckDBPyConnection,
    seasons: list[int],
    stats: dict[str, TableStats],
    arules: av.AvailabilityRules,
) -> None:
    """``bridge_player_id`` (after dim_player: its rows follow the player's row rule) and the
    unmatched-id report tables."""
    specs = sc.tables()
    bridge = specs["bridge_player_id"]
    cols = ", ".join(sc.q(c) for c in bridge.column_names)
    stats["bridge_player_id"].notes.update(pid.bridge_notes(con))  # after the usage checks
    _materialize(con, bridge, f"SELECT {cols} FROM _bridge", stats["bridge_player_id"], arules)
    cutoffs = league().candidate_pool_cutoffs()
    raw, notes = pid.raw_coverage(con, seasons, nv.cache_path)
    sources = pid.warehouse_coverage() + raw
    pool = pid.pool_coverage(con, cutoffs)
    if pool is not None:
        sources.append(pool)
    cov = stats["report_id_coverage"]
    cov.notes.update(notes)
    cov.notes["preseason_pool_cutoffs"] = cutoffs
    cov.notes.update(pid.stage_reports(con, sources))
    for name, staged in (
        ("report_id_coverage", "_report_coverage"),
        ("report_id_unmatched", "_report_unmatched"),
    ):
        t = specs[name]
        cols = ", ".join(f"CAST({sc.q(c.name)} AS {c.type}) AS {sc.q(c.name)}" for c in t.columns)
        _materialize(con, t, f"SELECT {cols} FROM {staged}", stats[name])
    kinds = con.execute(
        "SELECT kind, count(*) FROM report_id_unmatched GROUP BY 1 ORDER BY 1"
    ).fetchall()
    stats["report_id_unmatched"].notes["n_rows_by_kind"] = {k: n for k, n in kinds}


def _build_dim_team(
    con: duckdb.DuckDBPyConnection, src: SourceFiles, stats: TableStats
) -> TableStats:
    table = sc.tables()["dim_team"]
    _, inner = _source_select(con, _without_computed(table), src, stats)
    sql = f"SELECT *, current_abbr = team_abbr AS is_current FROM ({inner})"
    return _materialize(con, table, sql, stats)


# --------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------


def _assert_no_timestamptz(con: duckdb.DuckDBPyConnection) -> None:
    names = ", ".join(f"'{n}'" for n in sc.tables())
    bad = con.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        f"WHERE table_name IN ({names}) AND data_type LIKE 'TIMESTAMP WITH TIME ZONE%'"
    ).fetchall()
    if bad:
        raise RuntimeError(f"TIMESTAMP WITH TIME ZONE columns are not allowed: {bad}")


def _write_manifest(
    con: duckdb.DuckDBPyConnection, stats: list[TableStats]
) -> list[dict[str, Any]]:
    import nflreadpy

    built_at = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    rows = [
        {
            "table_name": s.table_name,
            "n_rows": s.n_rows,
            "content_hash": s.content_hash,
            "seasons": json.dumps(s.seasons),
            "seasons_skipped": json.dumps(s.seasons_skipped),
            "n_source_rows": s.n_source_rows,
            "n_dropped_null_key": s.n_dropped_null_key,
            "n_dropped_duplicates": s.n_dropped_duplicates,
            "notes": json.dumps(s.notes, sort_keys=True),
            "built_at": built_at,
            "twm_version": __version__,
            "nflreadpy_version": nflreadpy.__version__,
            "duckdb_version": duckdb.__version__,
            "polars_version": pl.__version__,
        }
        for s in stats
    ]
    df = pl.DataFrame(rows).with_columns(
        pl.col("n_rows", "n_source_rows", "n_dropped_null_key", "n_dropped_duplicates").cast(
            pl.Int64
        ),
        pl.col("built_at").cast(pl.Datetime("us")),
    )
    con.register("df_manifest", df)
    con.execute("DROP TABLE IF EXISTS build_manifest")
    con.execute("CREATE TABLE build_manifest AS SELECT * FROM df_manifest ORDER BY table_name")
    con.unregister("df_manifest")
    return rows


def _seasons_of(table: sc.Table, src: SourceFiles, seasons: list[int]) -> list[int]:
    """Seasons a table was built from: [] for the one-file datasets (players, teams), whose
    content does not depend on the season selection."""
    if table.source is None:
        return list(seasons)
    if not nv.DATASETS[table.source].per_season:
        return []
    return src.seasons_used[table.source]


def build_warehouse(
    seasons: Sequence[int], db_path: Path | str | None = None
) -> list[dict[str, Any]]:
    """Build every warehouse table for ``seasons`` from the local cache; return the manifest.

    The build goes into ``<db_path>.building`` and is renamed over ``db_path`` only after
    COMMIT, so readers of the previous file are never blocked and never see a half-built file.
    Raises ValueError (no seasons, or none with a schedule) and MissingCacheError before
    touching the database, BuildInProgressError if another build holds ``<db_path>.build.lock``,
    and PrimaryKeyError (after rolling back and removing the scratch file) if a table's key is
    not unique; the previous warehouse is untouched in every failure case.
    """
    seasons = sorted({int(s) for s in seasons})
    if not seasons:
        raise ValueError("no seasons given")
    src = resolve_sources(seasons)
    if not src.paths["schedules"]:
        first = nv.DATASETS["schedules"].first_season
        raise ValueError(
            f"none of the requested seasons {seasons} is >= {first}, the first season with a "
            "schedule; nothing to build"
        )
    rules = wk.AsOfRules.from_config(settings().as_of)
    arules = av.AvailabilityRules.from_settings()
    specs = sc.tables()
    stats = {
        name: TableStats(
            name,
            seasons=_seasons_of(t, src, seasons),
            seasons_skipped=src.seasons_skipped.get(t.source, []) if t.source else [],
        )
        for name, t in specs.items()
    }
    link = Path(db_path) if db_path is not None else settings().path("warehouse")
    # A symlinked warehouse (e.g. kept outside a cloud-synced folder, see
    # scripts/local_storage.sh) is rebuilt at its target: the scratch file, lock and rename
    # all live next to the real file, and the link keeps pointing at it.
    path = link.resolve() if link.is_symlink() else link
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".building")
    # DuckDB's write-ahead log, a sidecar file it may leave next to a database.
    tmp_wal = tmp.with_name(tmp.name + ".wal")
    lock_path = path.with_name(path.name + ".build.lock")
    lock_fd = _acquire_build_lock(lock_path, path)
    try:
        tmp.unlink(missing_ok=True)  # a stale scratch file from an interrupted build
        tmp_wal.unlink(missing_ok=True)
        try:
            con = connect(tmp, read_only=False)
            try:
                con.execute("BEGIN TRANSACTION")
                log.info("building warehouse for seasons %s into %s", seasons, tmp)
                _build_dim_team(con, src, stats["dim_team"])
                _build_fact_game(con, src, stats["fact_game"], arules)
                _build_dim_week(con, rules, stats["dim_week"])
                _assert_week_windows(con)  # before anything maps timestamps to weeks
                _build_fact_schedule(con, stats["fact_schedule"], arules)
                _build_coaches(con, stats, arules)
                for name in ("fact_play", "fact_player_week", "fact_team_week"):
                    _build_from_source(con, specs[name], src, stats[name], arules)
                _build_fact_opportunity_week(con, src, stats["fact_opportunity_week"], arules)
                # D1: per-play expected values, after fact_play (they copy its garbage flag)
                for name in ("fact_opportunity_pass", "fact_opportunity_rush"):
                    _build_fact_opportunity_play(con, name, src, stats[name], arules)
                # S1: K and D/ST, from fact_player_week / fact_team_week / fact_play / fact_game
                _build_fact_kicker_week(con, stats["fact_kicker_week"], arules)
                _build_fact_defense_week(con, stats["fact_defense_week"], arules)
                # B3: the id bridge first, then the tables that map ids through it
                _stage_player_ids(con, src, stats["bridge_player_id"])
                _build_fact_snaps(con, src, stats["fact_snaps"], arules)
                _build_from_source(
                    con, specs["fact_injury_report"], src, stats["fact_injury_report"], arules
                )
                _build_fact_depth_chart(con, src, stats["fact_depth_chart"], arules)
                # C1: rosters need fact_snaps / fact_player_week (their snapshot regime) and
                # rankings the id bridge
                _build_fact_roster_week(con, src, stats["fact_roster_week"], arules)
                _build_fact_ranking(con, src, seasons, stats["fact_ranking"], arules)
                # S1b: the K and DST pages, the same way
                _build_fact_ranking(
                    con, src, seasons, stats["fact_ranking_kdst"], arules, kdst=True
                )
                # after every event table: an undrafted player exists from his first row
                _build_dim_player(con, src, stats["dim_player"], arules)
                _build_id_reports(con, seasons, stats, arules)
                _assert_no_timestamptz(con)
                _note_after_week_asof(con, stats)
                _assert_official_snapshots_complete(con)
                for table in specs.values():
                    write_comments(con, table)
                manifest = _write_manifest(con, [stats[n] for n in specs])
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
            finally:
                con.close()
            _discard_stale_wal(path)
            if link != path:
                _discard_stale_wal(link)  # a reader that opened the link name writes its WAL there
            # Atomic on the same filesystem; anyone who already opened the old file keeps
            # reading the old contents until they reopen.
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
            tmp_wal.unlink(missing_ok=True)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)  # the lock file itself stays: unlinking it would let a third
        # process lock a different inode
    return manifest


def _acquire_build_lock(lock_path: Path, target: Path) -> int:
    """Take an exclusive, non-blocking advisory lock on ``lock_path``; return its fd.

    flock is released by the kernel when the process exits, so a crashed build never leaves
    the target locked and a leftover lock file is harmless.
    """
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as e:
        os.close(fd)
        raise BuildInProgressError(
            f"another warehouse build is already writing {target} (lock {lock_path})"
        ) from e
    return fd


def _discard_stale_wal(path: Path) -> None:
    """Remove a ``<db>.wal`` beside the target before the rename.

    DuckDB associates the write-ahead log with the database *path*, so a WAL left by some
    other writer that died before checkpointing (a notebook that opened the file read-write)
    would be replayed into the freshly built file on its first open.
    """
    stale_wal = path.with_name(path.name + ".wal")
    if stale_wal.exists():
        log.warning(
            "discarding stale WAL %s (%d bytes): the warehouse is written only by `twm build`",
            stale_wal,
            stale_wal.stat().st_size,
        )
        stale_wal.unlink(missing_ok=True)


def _assert_week_windows(con: duckdb.DuckDBPyConnection) -> None:
    """Every week's as-of must precede the next week's first kickoff (point-in-time safety).

    This also guarantees the as-ofs increase with the week number, which
    :func:`twm.warehouse.weeks.week_for_timestamp` (used for ``week_at_dt``) relies on.
    """
    bad = con.execute(
        """
        SELECT a.season, a.week, a.asof_weekly_utc, b.first_kickoff_utc
        FROM dim_week a JOIN dim_week b ON b.season = a.season AND b.week = a.next_week
        WHERE a.asof_weekly_utc >= b.first_kickoff_utc
        """
    ).fetchall()
    if bad:
        weeks = [(s, w) for s, w, *_ in bad]
        raise RuntimeError(
            f"dim_week: for {weeks} the Tuesday as-of falls after the next week's first "
            "kickoff, so features at that as-of would see next week's games (point-in-time "
            "leak). Check fact_game for those weeks: a game was probably moved past Tuesday. "
            f"Details (season, week, asof_weekly_utc, next first_kickoff_utc): {bad}"
        )


def table_counts(db_path: Path | str | None = None) -> list[tuple[str, int]]:
    """(table, n_rows) for every table in the warehouse file, for ``twm doctor``.

    Raises ``duckdb.Error`` (IOException across processes, ConnectionException inside one)
    when another handle holds the file read-write; callers turn that into a one-line notice.
    """
    path = Path(db_path) if db_path is not None else settings().path("warehouse")
    if not path.exists():
        return []
    con = connect(path, read_only=True)
    try:
        names = [
            r[0]
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' ORDER BY table_name"
            ).fetchall()
        ]
        return [(n, con.execute(f"SELECT count(*) FROM {sc.q(n)}").fetchone()[0]) for n in names]
    finally:
        con.close()
