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
from twm.config import settings
from twm.sources import nflverse as nv
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
    return [c.name for c in table.columns if (c.source or c.name) not in available]


def _pk(table: sc.Table) -> str:
    return ", ".join(sc.q(c) for c in table.primary_key)


def _materialize(
    con: duckdb.DuckDBPyConnection, table: sc.Table, stage_sql: str, stats: TableStats
) -> TableStats:
    """Create ``table.name`` from ``stage_sql`` (a SELECT producing the spec's typed columns)."""
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
    con.execute(f"DROP TABLE IF EXISTS {sc.q(table.name)}")
    con.execute(
        f"CREATE TABLE {sc.q(table.name)} AS SELECT * FROM {src} WHERE {not_null}{qualify} "
        f"ORDER BY {_pk(table)}"
    )
    con.execute("DROP TABLE IF EXISTS _stage")
    con.execute("DROP TABLE IF EXISTS _stage_d")
    stats.n_rows = con.execute(f"SELECT count(*) FROM {sc.q(table.name)}").fetchone()[0]
    stats.n_dropped_duplicates += n_kept - stats.n_rows
    assert_primary_key(con, table)
    stats.content_hash = content_hash(con, table.name)
    if stats.n_dropped_null_key or stats.n_dropped_duplicates:
        log.info(
            "%s: dropped %d null-key and %d duplicate rows",
            table.name,
            stats.n_dropped_null_key,
            stats.n_dropped_duplicates,
        )
    return stats


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
    so any DuckDB client can read what a column means."""
    con.execute(f"COMMENT ON TABLE {sc.q(table.name)} IS {sc.sql_str(table.doc)}")
    for c in table.columns:
        if c.doc:
            con.execute(
                f"COMMENT ON COLUMN {sc.q(table.name)}.{sc.q(c.name)} IS {sc.sql_str(c.doc)}"
            )


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
    con: duckdb.DuckDBPyConnection, table: sc.Table, src: SourceFiles, stats: TableStats
) -> TableStats:
    if not src.paths[table.source]:  # every requested season predates the dataset
        return _materialize(con, table, _empty_select(table), stats)
    _, sql = _source_select(con, table, src, stats)
    return _materialize(con, table, sql, stats)


def _without_computed(table: sc.Table) -> sc.Table:
    """The same spec minus the columns Python computes later (kickoff_utc, is_current ...)."""
    return dataclasses.replace(table, columns=tuple(c for c in table.columns if not c.computed))


def _build_fact_game(
    con: duckdb.DuckDBPyConnection, src: SourceFiles, stats: TableStats
) -> TableStats:
    table = sc.tables()["fact_game"]
    _, sql = _source_select(con, _without_computed(table), src, stats)
    df = con.execute(sql).pl()
    kick, est, end, home_it, away_it, stype = [], [], [], [], [], []
    cols = ("gameday", "gametime", "weekday", "spread_line", "total_line", "game_type")
    for gameday, gametime, weekday, spread, total, game_type in df.select(*cols).iter_rows():
        k, e = wk.kickoff_utc(gameday.isoformat(), gametime, weekday)
        kick.append(k)
        est.append(e)
        end.append(k + wk.GAME_DURATION_EST)
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
    )
    stats.notes["n_kickoff_estimated"] = int(sum(est))
    types = _register(con, "df_fact_game", df)
    sql = f"SELECT\n  {_select_list(table, types, transforms=False)}\nFROM df_fact_game"
    out = _materialize(con, table, sql, stats)
    con.unregister("df_fact_game")
    return out


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


def _build_coaches(con: duckdb.DuckDBPyConnection, stats: dict[str, TableStats]) -> None:
    slug_home, slug_away = SLUG_SQL.format(x="home_coach"), SLUG_SQL.format(x="away_coach")
    coach_game_sql = f"""
        SELECT game_id, season, week, season_type, home_team AS team,
               {slug_home} AS coach_id, TRUE AS is_home
        FROM fact_game WHERE home_coach IS NOT NULL
        UNION ALL
        SELECT game_id, season, week, season_type, away_team AS team,
               {slug_away} AS coach_id, FALSE AS is_home
        FROM fact_game WHERE away_coach IS NOT NULL"""
    _materialize(con, sc.COACH_GAME, coach_game_sql, stats["coach_game"])
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
    _materialize(con, sc.DIM_COACH, dim_coach_sql, stats["dim_coach"])
    cts_sql = """
        SELECT coach_id, team, season, CAST(min(week) AS INTEGER) AS first_week,
               CAST(max(week) AS INTEGER) AS last_week, CAST(count(*) AS INTEGER) AS n_games
        FROM coach_game GROUP BY coach_id, team, season"""
    _materialize(con, sc.COACH_TEAM_SEASON, cts_sql, stats["coach_team_season"])
    # How many team-seasons show more than one coach: the schedule columns record in-season
    # changes only through 2023 (see docs/assumptions.md), so this makes the gap visible.
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
    con: duckdb.DuckDBPyConnection, src: SourceFiles, stats: TableStats
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
          NULLIF(CAST(d.gsis_id AS VARCHAR), '') AS gsis_id,
          CAST(d.espn_id AS VARCHAR) AS espn_id,
          CAST(d.player_name AS VARCHAR) AS player_name,
          CAST(NULL AS VARCHAR) AS player_position,
          {sc.unit_sql("d.pos_grp", sc.UNIT_DAILY)} AS unit,
          CAST(d.pos_grp AS VARCHAR) AS unit_raw,
          CAST(d.pos_abb AS VARCHAR) AS position,
          CAST(d.pos_slot AS INTEGER) AS pos_slot,
          CAST(d.pos_rank AS INTEGER) AS depth_rank
        FROM src_depth_daily d
        LEFT JOIN df_week_at_dt w
          ON w.season = d.file_season AND w.dt = {dt_expr_d}""")
    if not parts:
        parts.append(_empty_select(table))
    sql = " UNION ALL BY NAME ".join(f"({p})" for p in parts)
    out = _materialize(con, table, f"SELECT * FROM ({sql})", stats)
    if daily_types is not None:
        con.unregister("df_week_at_dt")
    n_other = con.execute("SELECT count(*) FROM fact_depth_chart WHERE unit = 'other'").fetchone()[
        0
    ]
    if n_other:
        stats.notes["n_unit_other"] = n_other
        log.warning("fact_depth_chart: %d rows with unknown unit (stored as 'other')", n_other)
    return out


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
    specs = sc.tables()
    stats = {
        name: TableStats(
            name,
            seasons=_seasons_of(t, src, seasons),
            seasons_skipped=src.seasons_skipped.get(t.source, []) if t.source else [],
        )
        for name, t in specs.items()
    }
    path = Path(db_path) if db_path is not None else settings().path("warehouse")
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
                _build_fact_game(con, src, stats["fact_game"])
                _build_dim_week(con, rules, stats["dim_week"])
                _assert_week_windows(con)  # before anything maps timestamps to weeks
                _build_coaches(con, stats)
                for name in (
                    "fact_play",
                    "fact_player_week",
                    "fact_team_week",
                    "fact_snaps",
                    "fact_injury_report",
                ):
                    _build_from_source(con, specs[name], src, stats[name])
                _build_fact_depth_chart(con, src, stats["fact_depth_chart"])
                _build_from_source(con, specs["dim_player"], src, stats["dim_player"])
                _assert_no_timestamptz(con)
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
