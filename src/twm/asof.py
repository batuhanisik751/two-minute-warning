"""Point-in-time access to the warehouse: ``asof_filter`` and the as-of view (spec 6.1, B2).

Every prediction has an ``as_of`` moment (UTC). It may use a warehouse row only if the row's
``available_at <= as_of`` (``twm.warehouse.available`` explains how ``available_at`` is set).
This module is the one place that rule is implemented:

- :func:`asof_filter` keeps the rows of a polars frame that were public at ``as_of`` (features);
- :func:`outcomes_after` keeps the rows that became public strictly AFTER ``as_of`` (labels:
  spec 6.2 rule 3, "labels must be built from outcomes strictly after as_of");
- :class:`AsOfView` opens the warehouse "as it looked at as_of": plain SQL against
  ``fact_play`` etc. only ever sees rows available by then. ``wh.fact_play`` (the attached
  warehouse) is the deliberate, visible way around it;
- :func:`weekly_as_of` / :func:`end_of_regular_season_as_of` read the official as-of times from
  ``dim_week`` so every module uses the same moments; :func:`parse_as_of` and :func:`week_at`
  turn a ``twm score --as-of`` text (a season-week or a zoned timestamp) into one of them.

``as_of`` must be timezone-AWARE (``datetime(2025, 10, 7, 14, tzinfo=UTC)`` or the value from
:func:`weekly_as_of`). A naive datetime is refused: ``datetime.now()`` or a notebook's local time
silently shifted by a few hours is the classic way the future leaks into a backtest.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from twm.warehouse import available as av
from twm.warehouse import schema as sc

WAREHOUSE_ALIAS = "wh"


# --------------------------------------------------------------------------------------
# Timestamps
# --------------------------------------------------------------------------------------


def to_utc(as_of: datetime) -> datetime:
    """A timezone-aware datetime as a naive datetime holding UTC (the warehouse convention).

    Aware input in any zone is converted (07:00 US Eastern daylight time -> 11:00). Naive input
    raises TypeError: we cannot know which zone it meant, and guessing wrong leaks.
    """
    if not isinstance(as_of, datetime):
        raise TypeError(f"as_of must be a datetime, got {type(as_of).__name__}")
    if as_of.tzinfo is None or as_of.tzinfo.utcoffset(as_of) is None:
        raise TypeError(
            f"as_of must be timezone-aware, got the naive {as_of!r}; a naive time is ambiguous "
            "(local? UTC?) and a few hours of error can leak a whole game. Use "
            "weekly_as_of(...) or datetime(..., tzinfo=UTC)."
        )
    return as_of.astimezone(UTC).replace(tzinfo=None)


def sql_timestamp(naive_utc: datetime) -> str:
    """A naive-UTC datetime as a DuckDB ``TIMESTAMP '...'`` literal (microsecond precision)."""
    return f"TIMESTAMP '{naive_utc.isoformat(sep=' ', timespec='microseconds')}'"


class WarehouseTooOldError(RuntimeError):
    """The warehouse file lacks a column the point-in-time rules need: rebuild it."""


# --------------------------------------------------------------------------------------
# Frame filters
# --------------------------------------------------------------------------------------


def _checked_column(df: pl.DataFrame | pl.LazyFrame, column: str) -> pl.Datetime:
    schema = df.collect_schema() if isinstance(df, pl.LazyFrame) else df.schema
    if column not in schema:
        raise KeyError(
            f"no {column!r} column: this table is static or hindsight (dim_team, dim_week, "
            "dim_player ...), which are always visible and need no filter; see "
            "twm.warehouse.available.TABLE_AVAILABILITY"
        )
    # a polars join suffixes the second table's column (available_at_right): filtering only
    # the first would let the other table's future rows through
    others = [c for c in schema if c != column and c.startswith(column)]
    if others:
        raise ValueError(
            f"the frame also has {others}: it was joined after loading, and the other table's "
            "future rows would pass unfiltered. Filter each table with asof_filter BEFORE "
            "joining, then join"
        )
    dtype = schema[column]
    if not isinstance(dtype, pl.Datetime):
        raise TypeError(f"{column!r} must be a Datetime column, got {dtype}")
    if isinstance(df, pl.LazyFrame):
        # computes only this one column (projection pushdown), so it is cheap
        n_null = df.select(pl.col(column).null_count()).collect().item()
    else:
        n_null = df.get_column(column).null_count()
    if n_null:
        raise ValueError(
            f"{n_null} row(s) have a NULL {column!r}: a row with an unknown availability time "
            "can never be used safely (the warehouse build forbids this; was the frame joined "
            "or edited after loading?)"
        )
    return dtype


def _lit(naive_utc: datetime, dtype: pl.Datetime) -> pl.Expr:
    """A literal comparable with a column of ``dtype`` (naive UTC or timezone-aware).

    Always microseconds: polars compares across time units exactly, so the as-of is never
    rounded to a coarser column unit."""
    lit = pl.lit(naive_utc, dtype=pl.Datetime("us"))
    if dtype.time_zone is None:
        return lit
    return lit.dt.replace_time_zone("UTC").dt.convert_time_zone(dtype.time_zone)


def asof_filter[F: (pl.DataFrame, pl.LazyFrame)](
    df: F, as_of: datetime, *, column: str = av.AVAILABLE_AT
) -> F:
    """Rows that were public at ``as_of``: ``column <= as_of`` (inclusive). For FEATURES.

    Works on a polars DataFrame or LazyFrame and returns the same kind. Raises KeyError if the
    column is missing (a static or hindsight table), TypeError if it is not a Datetime column
    or ``as_of`` is naive, ValueError if any value is NULL.
    """
    ts = to_utc(as_of)
    dtype = _checked_column(df, column)
    return df.filter(pl.col(column) <= _lit(ts, dtype))


def outcomes_after[F: (pl.DataFrame, pl.LazyFrame)](
    df: F,
    as_of: datetime,
    *,
    until: datetime | None = None,
    column: str = av.AVAILABLE_AT,
) -> F:
    """Rows that became public strictly AFTER ``as_of`` (and at or before ``until``). For LABELS.

    A row available exactly at ``as_of`` is a feature row (see :func:`asof_filter`), never a
    label row, so features and labels can never share a row. Same validation as asof_filter;
    ValueError if ``until`` is before ``as_of``.
    """
    ts = to_utc(as_of)
    dtype = _checked_column(df, column)
    cond = pl.col(column) > _lit(ts, dtype)
    if until is not None:
        end = to_utc(until)
        if end < ts:
            raise ValueError(f"until ({until}) is before as_of ({as_of})")
        cond = cond & (pl.col(column) <= _lit(end, dtype))
    return df.filter(cond)


# --------------------------------------------------------------------------------------
# Official as-of times
# --------------------------------------------------------------------------------------


def _with_connection(
    db: Path | str | duckdb.DuckDBPyConnection,
) -> tuple[duckdb.DuckDBPyConnection, bool]:
    if isinstance(db, duckdb.DuckDBPyConnection):
        return db, False
    from twm.warehouse.build import connect

    return connect(db, read_only=True), True


def weekly_as_of(
    db: Path | str | duckdb.DuckDBPyConnection,
    season: int,
    week: int,
    season_type: str | None = "REG",
) -> datetime:
    """The official Tuesday as-of after (season, week) from ``dim_week``, aware UTC.

    ``season_type`` 'REG' or 'POST' (``None``: any; week numbers are unique within a season).
    ``db`` is a warehouse path or an open connection. LookupError if the week is not built.
    """
    con, close = _with_connection(db)
    try:
        sql = "SELECT asof_weekly_utc FROM dim_week WHERE season = ? AND week = ?"
        params: list[Any] = [season, week]
        if season_type is not None:
            sql += " AND season_type = ?"
            params.append(season_type)
        row = con.execute(sql, params).fetchone()
    finally:
        if close:
            con.close()
    if row is None:
        kind = f" {season_type}" if season_type else ""
        raise LookupError(f"no{kind} week {week} of {season} in dim_week (built?)")
    return row[0].replace(tzinfo=UTC)


# ``twm score --as-of``: "2026-W3" (also 2026W3, 2026-w03) or "2026 3" (season and week).
_SEASON_WEEK = (re.compile(r"^\s*(\d{4})\s*-?\s*[Ww](\d{1,2})\s*$"),
                re.compile(r"^\s*(\d{4})\s+(\d{1,2})\s*$"))  # fmt: skip


class AsOfParseError(ValueError):
    """An ``--as-of`` text that is neither a season-week nor a zoned ISO timestamp."""


def parse_as_of(text: str) -> tuple[int, int] | datetime:
    """``twm score --as-of`` text -> (season, week) or an aware UTC datetime.

    Accepted: a season-week ("2026-W3", "2026W3", "2026 3") or an ISO 8601 timestamp WITH a
    zone ("2026-09-29T14:00Z", "2026-09-29T10:00-04:00"). A timestamp without a zone (or a bare
    date) is refused: we cannot know which zone it meant, and a few hours of error move it into
    another week's window (see :func:`week_at`)."""
    for pattern in _SEASON_WEEK:
        m = pattern.match(text)
        if m:
            season, week = int(m.group(1)), int(m.group(2))
            if week < 1:
                raise AsOfParseError(f"--as-of {text!r}: weeks start at 1")
            return season, week
    try:
        t = datetime.fromisoformat(text.strip())
    except ValueError:
        raise AsOfParseError(
            f"--as-of {text!r} is not a season-week (2026-W3 or '2026 3') or an ISO timestamp "
            "with a zone (2026-09-29T14:00Z)"
        ) from None
    if t.tzinfo is None or t.tzinfo.utcoffset(t) is None:
        raise AsOfParseError(
            f"--as-of {text!r} has no time zone; add one (2026-09-29T14:00Z for UTC, or "
            "-04:00 for US Eastern daylight time): a naive time is ambiguous"
        )
    return t.astimezone(UTC)


def week_at(
    db: Path | str | duckdb.DuckDBPyConnection, when: datetime
) -> tuple[int, int, datetime]:
    """The regular-season week whose list is current at ``when``: (season, week, its official
    Tuesday as-of), from ``dim_week``.

    Week N's list is made at its official as-of (the Tuesday 14:00 UTC after week N) and is
    the current one until the next regular-season week's as-of, so its window is [as-of of
    week N, as-of of the next week). ``when`` maps to the week whose window contains it: the
    latest regular-season as-of at or before ``when`` (a Saturday maps to the week before it;
    the offseason maps to the last week of the season before). LookupError if ``when`` is
    before the first as-of in the warehouse."""
    t = to_utc(when)
    con, close = _with_connection(db)
    try:
        row = con.execute(
            "SELECT season, week, asof_weekly_utc FROM dim_week WHERE season_type = 'REG' "
            "AND asof_weekly_utc <= ? ORDER BY asof_weekly_utc DESC, season DESC LIMIT 1",
            [t],
        ).fetchone()
        first = con.execute(
            "SELECT min(asof_weekly_utc) FROM dim_week WHERE season_type = 'REG'"
        ).fetchone()
    finally:
        if close:
            con.close()
    if row is None:
        start = f" (the first is {first[0]:%Y-%m-%d %H:%M} UTC)" if first and first[0] else ""
        raise LookupError(
            f"{t:%Y-%m-%d %H:%M} UTC is before every regular-season as-of in the warehouse" + start
        )
    return int(row[0]), int(row[1]), row[2].replace(tzinfo=UTC)


def end_of_regular_season_as_of(
    db: Path | str | duckdb.DuckDBPyConnection, season: int
) -> datetime:
    """The Hot-Seat end-of-regular-season as-of of ``season`` from ``dim_week``, aware UTC."""
    con, close = _with_connection(db)
    try:
        row = con.execute(
            "SELECT asof_end_of_regular_season_utc FROM dim_week "
            "WHERE season = ? AND is_last_reg_week",
            [season],
        ).fetchone()
    finally:
        if close:
            con.close()
    if row is None or row[0] is None:
        raise LookupError(f"no last regular-season week of {season} in dim_week (built?)")
    return row[0].replace(tzinfo=UTC)


# --------------------------------------------------------------------------------------
# The as-of view
# --------------------------------------------------------------------------------------


class AsOfView:
    """The warehouse as it looked at ``as_of``. Use as a context manager::

        db = "data/warehouse.duckdb"
        with AsOfView(db, weekly_as_of(db, 2025, 5)) as v:
            v.sql("SELECT player_id, sum(receptions) FROM fact_player_week "
                  "WHERE season = 2025 GROUP BY 1")

    Inside, every warehouse table is a view of the same name: event tables show only rows with
    ``available_at <= as_of`` (without their today's-snapshot columns such as
    ``fact_player_week.position``); static tables (dim_team, dim_week) show every row, with
    ``dim_week``'s schedule-derived counts NULL for weeks not played yet; ``fact_schedule``'s
    date/time/venue columns are NULL until ``slot_available_at``; hindsight tables
    (dim_player, and bridge_player_id: a link is visible once its player is) show only players
    who exist by then (drafted, or with a public data row) and only their allowlisted columns
    (querying ``latest_team`` or ``position`` is an error). The meta tables (build_manifest,
    report_id_coverage, report_id_unmatched) are not views. So ordinary SQL is point-in-time
    safe by default.

    The warehouse itself is attached read-only as ``wh``: ``wh.fact_play`` returns every row,
    including the future. It exists for inspection and for label builders, and it is exactly
    what :func:`twm.backtest.leakage.assert_future_invariant` catches when a feature uses it.
    """

    def __init__(self, db_path: Path | str, as_of: datetime) -> None:
        self._as_of_utc = to_utc(as_of)
        self._db_path = Path(db_path)
        if not self._db_path.exists():
            raise FileNotFoundError(f"warehouse not found: {self._db_path}")
        self._used: set[str] = set()
        self._con: duckdb.DuckDBPyConnection | None = duckdb.connect(":memory:")
        try:
            self._setup()
        except BaseException:
            self.close()
            raise

    def _setup(self) -> None:
        con = self._con
        assert con is not None
        con.execute("SET TimeZone='UTC'")
        con.execute(f"ATTACH {sc.sql_str(str(self._db_path))} AS {WAREHOUSE_ALIAS} (READ_ONLY)")
        present = {
            r[0]
            for r in con.execute(
                "SELECT table_name FROM duckdb_tables() WHERE database_name = ?",
                [WAREHOUSE_ALIAS],
            ).fetchall()
        }
        cutoff = sql_timestamp(self._as_of_utc)
        self._tables: dict[str, list[str]] = {}
        for name in sorted(present):
            a = av.TABLE_AVAILABILITY.get(name)
            if a is None or a.kind == "meta":  # unregistered tables / bookkeeping: not exposed
                continue
            src = f"{WAREHOUSE_ALIAS}.main.{sc.q(name)}"
            cols = [
                r[0]
                for r in con.execute(
                    "SELECT column_name FROM duckdb_columns() WHERE database_name = ? "
                    "AND table_name = ? ORDER BY column_index",
                    [WAREHOUSE_ALIAS, name],
                ).fetchall()
            ]
            missing = [c for c in av.required_columns(a) if c not in cols]
            if missing:
                raise WarehouseTooOldError(
                    f"{self._db_path}: {name} has no {', '.join(missing)} column; the warehouse "
                    "was built before B2 (or with older B2 rules). Rebuild it with `twm build`."
                )
            sql = av.point_in_time_select(a, src, cols, cutoff)
            con.execute(f"CREATE VIEW main.{sc.q(name)} AS {sql}")
            self._tables[name] = [
                r[0] for r in con.execute(f"DESCRIBE main.{sc.q(name)}").fetchall()
            ]

    # -- context manager -------------------------------------------------------------------

    def __enter__(self) -> AsOfView:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None

    # -- queries ---------------------------------------------------------------------------

    @property
    def as_of(self) -> datetime:
        """The as-of moment (aware, UTC)."""
        return self._as_of_utc.replace(tzinfo=UTC)

    @property
    def tables(self) -> list[str]:
        """The point-in-time tables this view exposes."""
        return sorted(self._tables)

    @property
    def tables_used(self) -> frozenset[str]:
        """Registered table names this view was asked about (``table()`` calls and names that
        appear in ``sql()`` text, ``wh.`` bypasses included). Used by the leakage harness."""
        return frozenset(self._used)

    def _connection(self) -> duckdb.DuckDBPyConnection:
        if self._con is None:
            raise RuntimeError("this AsOfView is closed")
        return self._con

    def sql(self, query: str, params: Sequence[Any] | None = None) -> pl.DataFrame:
        """Run SQL against the as-of views (``wh.<table>`` reaches the unfiltered warehouse)."""
        self._used.update(_mentioned_tables(query))
        return self._connection().execute(query, params or []).pl()

    def table(self, name: str, columns: Iterable[str] | None = None) -> pl.DataFrame:
        """One point-in-time table (optionally only some columns) as a polars DataFrame."""
        if name not in self._tables:
            raise KeyError(f"{name!r} is not a point-in-time table here; have: {self.tables}")
        self._used.add(name)
        cols = "*" if columns is None else ", ".join(sc.q(c) for c in columns)
        return self._connection().execute(f"SELECT {cols} FROM main.{sc.q(name)}").pl()


_TABLE_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _mentioned_tables(query: str) -> set[str]:
    """Registered table names that appear as words in ``query``, in any letter case (SQL
    identifiers are case-insensitive: ``FROM Fact_Game`` reads ``fact_game``). A deliberately
    simple scan: it may over-report, e.g. a name inside a string literal, which only costs a
    copy."""
    words = {w.lower() for w in _TABLE_NAME_RE.findall(query)}
    return {w for w in words if w in av.TABLE_AVAILABILITY}
