"""The leakage harness: prove a feature builder cannot see the future (spec 13, B2).

The idea in one sentence: if a feature computed at ``as_of`` really uses only what was public
at ``as_of``, then deleting or scrambling everything that was not public yet cannot change it.
:func:`assert_future_invariant` does exactly that:

1. run the builder on the real warehouse (through an :class:`~twm.asof.AsOfView`);
2. build two scratch copies of the warehouse in a temp directory, holding the tables the
   builder touched plus every static/hindsight table, where everything not yet public at
   ``as_of`` is (a) DELETED or (b) PERTURBED:

   - event rows with ``available_at > as_of``, and ``dim_player`` rows of players who do not
     exist yet (``public_from_utc`` NULL or after the as-of): deleted in (a); in (b) their
     non-key columns are scrambled;
   - masked values (``fact_schedule``'s date/time/venue before ``slot_available_at``,
     ``dim_week``'s schedule counts of weeks not played yet): NULL in (a), scrambled in (b);
   - today's-snapshot columns (``dim_player.latest_team``, ``fact_player_week.position`` ...):
     scrambled on every row in (b).

   Scrambling is per type: signed integers x -> x * 3 + 7, or ~x (bitwise NOT) where that
   would not fit the type (never an overflow error, never equal to x), unsigned integers
   x -> x - 1 (0 -> 1), floats x -> x * 3 + 7, decimals x -> -x (0 -> 1),
   booleans negated, strings reversed and marked, timestamps and dates shifted by a day. Keys
   such as game_id / player_id / season / week / team are left alone so joins still match;
3. run the builder on each copy; any difference from step 1 raises :class:`LeakageError`
   naming the variant, the columns that differ and a few example rows.

A builder that reads ``fact_player_week`` through the view passes; one that reads
``wh.fact_player_week WHERE week <= N`` (the attached raw warehouse) fails, because week
numbers are not time: a game moved to a Wednesday belongs to week N but is not public at
week N's Tuesday as-of.

Limits (read before trusting a green result):

- it checks the one ``as_of`` you pass; run it at several (a split week, a season start ...);
- the builder must be deterministic (same input -> same output), and ``key`` must identify
  its output rows (checked);
- a future row whose value is NULL stays NULL in the perturbed copy, and a leak that only
  moves a result by less than the float tolerance goes unnoticed;
- static tables (``dim_team``, ``dim_week``'s calendar columns) and ``dim_player``'s
  allowlisted columns of players who already exist are copied unchanged: they are TRUSTED,
  not tested. ``dim_team.team_division`` (today's alignment) or a normalized team code
  (STL -> LA) is hindsight the harness cannot see; keep future-looking reads of those tables
  out of feature builders;
- it cannot catch leakage that is already baked into a visible value: nflverse model columns
  (``epa``/``wp``, spec 6.3), the FINAL schedule in ``fact_schedule`` (a game moved after the
  release and not listed in ``availability.schedule_exceptions``), stat corrections, or a wrong
  ``available_at`` rule. It tests the builder, not the warehouse.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

import duckdb
import polars as pl
from polars.testing import assert_frame_equal

from twm.asof import AsOfView, sql_timestamp, to_utc
from twm.warehouse import available as av
from twm.warehouse import schema as sc

Builder = Callable[[AsOfView], pl.DataFrame]

# Columns never perturbed: they identify rows and are needed for joins to keep matching.
KEY_COLUMNS = frozenset(
    {"game_id", "play_id", "player_id", "gsis_id", "season", "week", "team", "season_type",
     "pfr_player_id", av.AVAILABLE_AT}
)  # fmt: skip
SIGNED_INTEGER_TYPES = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT")
UNSIGNED_INTEGER_TYPES = ("UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT", "UHUGEINT")
FLOAT_TYPES = ("FLOAT", "DOUBLE")
VARIANTS = ("deleted", "perturbed")
N_EXAMPLES = 3


class LeakageError(AssertionError):
    """A builder's output changed when future rows were deleted or perturbed."""


def assert_future_invariant(
    builder: Builder,
    db_path: Path | str,
    as_of: datetime,
    *,
    key: Sequence[str] | None = None,
) -> pl.DataFrame:
    """Raise :class:`LeakageError` unless ``builder`` ignores every row after ``as_of``.

    ``builder`` takes an :class:`AsOfView` and returns a polars DataFrame; ``key`` are the
    columns that identify an output row (default: sort by every column before comparing).
    Returns the builder's output on the real warehouse.
    """
    ts = to_utc(as_of)
    with AsOfView(db_path, as_of) as view:
        expected = builder(view)
        used = set(view.tables_used)
    if key:
        missing = [k for k in key if k not in expected.columns]
        if missing:
            raise ValueError(f"key columns {missing} are not in the builder's output")
        if expected.select(key).is_duplicated().any():
            raise ValueError(
                f"key {list(key)} does not identify the output rows uniquely (the comparison "
                "would depend on row order); pass the full key or key=None"
            )
    with tempfile.TemporaryDirectory(prefix="twm-leakage-") as tmp:
        for variant in VARIANTS:
            copy = Path(tmp) / f"{variant}.duckdb"
            _scratch_copy(Path(db_path), copy, used, ts, variant)
            with AsOfView(copy, as_of) as view:
                got = builder(view)
            _compare(expected, got, variant, key)
    return expected


# --------------------------------------------------------------------------------------
# Scratch copies
# --------------------------------------------------------------------------------------


def _perturbed(col: str, typ: str) -> str | None:
    """A SQL expression that always differs from ``col`` (NULL stays NULL), or None."""
    c = sc.q(col)
    t = typ.upper()
    if t in SIGNED_INTEGER_TYPES:
        # x * 3 + 7 computed as DOUBLE (no overflow error) moves values UP, so a max() over
        # future rows changes too; where it does not fit the type (x near the limits, e.g.
        # fact_game.gsis ~ 2e9 in INTEGER), bitwise NOT: same type, never overflows, never x
        return f"COALESCE(TRY_CAST(CAST({c} AS DOUBLE) * 3 + 7 AS {typ}), ~{c})"
    if t in UNSIGNED_INTEGER_TYPES:
        return f"(CASE WHEN {c} = 0 THEN CAST(1 AS {typ}) ELSE {c} - 1 END)"
    if t in FLOAT_TYPES:
        return f"CAST({c} * 3 + 7 AS {typ})"
    if t.startswith("DECIMAL"):
        # -x never overflows a decimal; 0 becomes 1 (NULL if 1 does not fit: still different)
        return f"(CASE WHEN {c} = 0 THEN TRY_CAST(1 AS {typ}) ELSE -{c} END)"
    if t == "BOOLEAN":
        return f"(NOT {c})"
    if t == "VARCHAR":
        return f"('~' || reverse({c}))"  # the mark makes '' and palindromes change too
    if t.startswith("TIMESTAMP") or t == "DATE":
        return f"CAST({c} + INTERVAL 1 DAY AS {typ})"
    return None  # lists, structs ...: left alone


def _scratch_copy(src: Path, dst: Path, used: set[str], ts: datetime, variant: str) -> None:
    cutoff = sql_timestamp(ts)
    con = duckdb.connect(str(dst))
    try:
        con.execute("SET TimeZone='UTC'")
        con.execute(f"ATTACH {sc.sql_str(str(src))} AS src (READ_ONLY)")
        present = {
            r[0]
            for r in con.execute(
                "SELECT table_name FROM duckdb_tables() WHERE database_name = 'src'"
            ).fetchall()
        }
        specs = sc.tables()
        for name in sorted(present):
            a = av.TABLE_AVAILABILITY.get(name)
            if a is None or a.kind == "meta" or (a.kind == "event" and name not in used):
                continue
            cols = con.execute(
                "SELECT column_name, data_type FROM duckdb_columns() "
                "WHERE database_name = 'src' AND table_name = ? ORDER BY column_index",
                [name],
            ).fetchall()
            keys = KEY_COLUMNS | set(specs[name].primary_key if name in specs else ())
            keys |= set(a.extra_columns)  # the time columns themselves
            future = av.future_row_condition(a, cutoff)
            masked = av.masked_condition(a, cutoff)
            parts = []
            for col, typ in cols:
                p = _perturbed(col, typ)
                expr = sc.q(col)
                if variant == "deleted":
                    if masked and col in a.masked_columns:
                        expr = f"CASE WHEN {masked} THEN NULL ELSE {expr} END"
                elif p is not None and col not in keys:
                    conds = []
                    if col in a.hidden_columns or col in a.hindsight_columns:
                        conds.append("TRUE")  # today's snapshot: wrong for every row
                    if future:
                        conds.append(future)
                    if masked and col in a.masked_columns:
                        conds.append(masked)
                    if conds:
                        expr = f"CASE WHEN {' OR '.join(conds)} THEN {p} ELSE {expr} END"
                parts.append(f"{expr} AS {sc.q(col)}")
            select = f"SELECT {', '.join(parts)} FROM src.main.{sc.q(name)}"
            if variant == "deleted" and future:
                select += f" WHERE NOT {future}"
            con.execute(f"CREATE TABLE main.{sc.q(name)} AS {select}")
        con.execute("DETACH src")
    finally:
        con.close()


# --------------------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------------------


def _sorted(df: pl.DataFrame, key: Sequence[str] | None) -> pl.DataFrame:
    by = list(key) if key else df.columns
    if not by:
        return df
    return df.sort(by, nulls_last=True, maintain_order=True)


def _compare(
    expected: pl.DataFrame, got: pl.DataFrame, variant: str, key: Sequence[str] | None
) -> None:
    if expected.columns != got.columns:
        raise LeakageError(
            f"[{variant}] the builder returned different columns: {expected.columns} on the "
            f"real warehouse vs {got.columns} with future rows {variant}"
        )
    exp, act = _sorted(expected, key), _sorted(got, key)
    try:
        assert_frame_equal(exp, act, check_exact=False)
        return
    except AssertionError as e:
        detail = str(e).splitlines()[0]
    if exp.height != act.height:
        on = list(key) if key else exp.columns
        only_real = exp.join(act, on=on, how="anti", nulls_equal=True)
        only_copy = act.join(exp, on=on, how="anti", nulls_equal=True)
        raise LeakageError(
            f"[{variant}] the output has {exp.height} rows on the real warehouse but "
            f"{act.height} when every row after the as-of is {variant}: the builder reads "
            f"future rows. Rows only in the real output ({only_real.height}):\n"
            f"{only_real.head(N_EXAMPLES)}\nRows only with future {variant} "
            f"({only_copy.height}):\n{only_copy.head(N_EXAMPLES)}"
        )
    differing = [c for c in exp.columns if not _column_equal(exp[c], act[c])]
    mask = pl.Series([False] * exp.height)
    for c in differing:
        mask = mask | _row_differs(exp[c], act[c])
    raise LeakageError(
        f"[{variant}] columns {differing} change when every row after the as-of is "
        f"{variant}: the builder reads future rows ({detail}). Example rows (real):\n"
        f"{exp.filter(mask).head(N_EXAMPLES)}\n(with future {variant}):\n"
        f"{act.filter(mask).head(N_EXAMPLES)}"
    )


def _column_equal(a: pl.Series, b: pl.Series) -> bool:
    try:
        assert_frame_equal(a.to_frame(), b.to_frame(), check_exact=False)
    except AssertionError:
        return False
    return True


def _row_differs(a: pl.Series, b: pl.Series) -> pl.Series:
    if a.dtype.is_float() and b.dtype.is_float():
        close = ((a - b).abs() <= 1e-8 + 1e-5 * b.abs()).fill_null(False)
        both_null = a.is_null() & b.is_null()
        return ~(close | both_null)
    return ~a.eq_missing(b)
