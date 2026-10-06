"""Write a publish to Postgres in one transaction (step E2; step P2: every module's lists).

One run (:func:`publish`), all or nothing; steps 2, 4 and 5 run for each module the publish
carries (:data:`twm.publish.tables.FAMILIES`: the Waiver Radar, the K and D/ST streamer,
Regression Watch) with the same code:

1. take a transaction-scoped advisory lock (two publishes never interleave) and check that the
   target has every table and column this code writes (else: run the migrations);
2. read the target's **live** lists and decide, per local live list: *insert* (absent in the
   target), *kept* (already published: live lists are frozen), *skipped* (incomplete and not
   ``allow_incomplete``) or *replace* (only for a week named in ``replace_live``; what it
   replaces is reported);
3. upsert model_versions, dim_team, dim_player (never deleted);
4. replace every 'backtest' list with the local ones; insert the new live lists;
5. replace radar_outcome (the outcomes of every published pick, including the frozen live
   lists already in the target, plus the current season's dataset rows), track_record,
   tier_stats, player_week_summary, glossary and site_meta;
6. step P3, when the publish carries the Decision Report Card (``data.decisions``): upsert
   dim_coach; its split tables (:data:`twm.publish.tables.DECISIONS`) as two units, the frozen
   history (seasons before the approved grading's season) and the season in progress, each
   replaced unless unchanged and each part watched by the guard; decisions_track_record like
   the other replaced tables;
6b. feature #1, when the publish carries the Questionable list (``data.questionable``): insert
   every local snapshot whose (season, week, as_of) is not in the target (append-only: never
   deleted or changed; *insert* decisions are reported, the others kept silently); its tables
   (:data:`twm.publish.tables.QUESTIONABLE`) are replaced like the others (hash, guard), the
   live record graded from every snapshot of the season the target holds after the run (the
   published ones included: :func:`twm.publish.questionable.live_record`); feature #5,
   Teammate out (``data.teammate_out``, :data:`twm.publish.tables.TEAMMATE_OUT`), the same;
   feature #6, the playoff planner (``data.playoff_planner``,
   :data:`twm.publish.tables.PLAYOFF_PLANNER`), the same with snapshots keyed by (season,
   through_week);
6c. feature #10, when the publish carries coach tendencies (``data.coach_tendencies``): its
   four tables (:data:`twm.publish.tables.COACH_TENDENCIES`) are replaced like the others
   (hash, guard); its coaches are upserted into dim_coach;
7. append a 'success' pipeline_runs row, count the rows and measure the tables, commit.

Any error rolls everything back; a 'failed' pipeline_runs row is then written in a new
transaction (the error text scrubbed of the connection string). ``dry_run`` does all of it
and rolls back instead of committing (nothing is recorded).
"""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import polars as pl

from twm.publish import playoff_planner as pp
from twm.publish import questionable as qn
from twm.publish import teammate_out as tn
from twm.publish.collect import PublishData
from twm.publish.tables import (
    COACH_TENDENCIES,
    DECISIONS,
    FAMILIES,
    KEEP_ON_CONFLICT,
    PLAYOFF_PLANNER,
    QUESTIONABLE,
    SHARED,
    TABLES,
    TEAMMATE_OUT,
    Family,
    Snapshots,
    Table,
)
from twm.publish.target import Target, redact

STAGE = "publish"
EXIT_SKIPPED_INCOMPLETE = 4  # published, but some live lists were incomplete: retry later
EXIT_SHRINK_REFUSED = 5  # refused: a replaced table would lose too many rows (--allow-shrink)
HASH_PREFIX = "hash:"  # site_meta keys holding each replaced table's content hash
HASH_VERSION = "v1"
# pg_advisory_xact_lock key: any fixed 64-bit number shared by every twm publisher
LOCK_KEY = 7_274_871_120_260_928
CONNECT_TIMEOUT_S = 20


class PublishError(RuntimeError):
    """The publish failed; the target was rolled back (see ``recorded``). ``shrink``: refused
    by the empty-replacement guard (:class:`ShrinkError`)."""

    def __init__(self, message: str, *, recorded: bool = False, shrink: bool = False) -> None:
        super().__init__(message)
        self.recorded = recorded
        self.shrink = shrink


class ShrinkError(PublishError):
    """A replaced table would lose more than ``max_shrink_share`` of the rows the target holds:
    the local inputs are probably missing or broken (nothing is written)."""

    step = "shrink_guard"

    def __init__(self, message: str) -> None:
        super().__init__(message, shrink=True)


@dataclass
class LiveDecision:
    season: int
    week: int
    position: str  # '' for a list without positions (Regression Watch)
    action: str  # insert | kept | skipped | replace
    detail: str = ""
    module: str = "waiver_radar"

    @property
    def label(self) -> str:
        """'2026-W03 QB' (the Radar), 'streamer 2026-W03 K', 'regression watch 2026-W03'."""
        fam = FAMILIES.get(self.module)
        prefix = fam.label if fam is not None else self.module.replace("_", " ")
        text = f"{self.season}-W{self.week:02d}" + (f" {self.position}" if self.position else "")
        return f"{prefix} {text}" if prefix else text


@dataclass
class PublishResult:
    run_id: str
    target: str
    dry_run: bool
    written: dict[str, int]
    counts: dict[str, int]
    sizes: dict[str, int]
    database_bytes: int
    live: list[LiveDecision]
    warnings: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)  # replaced tables skipped (same hash)

    @property
    def total_bytes(self) -> int:
        return sum(self.sizes.values())

    @property
    def skipped(self) -> list[LiveDecision]:
        return [d for d in self.live if d.action == "skipped"]


def parse_week(text: str) -> tuple[int, int]:
    """'2026-W04' (or '2026-W4') -> (2026, 4)."""
    import re

    m = re.fullmatch(r"\s*(\d{4})-[Ww](\d{1,2})\s*", text)
    if not m:
        raise ValueError(f"{text!r} is not a week like 2026-W04")
    return int(m.group(1)), int(m.group(2))


# --------------------------------------------------------------------------------------
# Rows for Postgres
# --------------------------------------------------------------------------------------


def _value(v: Any, pg_type: str) -> Any:
    from psycopg.types.json import Jsonb

    if pg_type == "jsonb":
        if v is None:
            return None
        return Jsonb(json.loads(v) if isinstance(v, str) else v)
    if isinstance(v, float) and math.isnan(v):
        raise ValueError("NaN cannot be published")
    return v


def rows_of(df: pl.DataFrame, table: Table) -> list[tuple]:
    """``df``'s rows in the table's column order, adapted for psycopg."""
    missing = [c for c in table.names if c not in df.columns]
    if missing:
        raise ValueError(f"{table.name}: rows lack columns {missing}")
    types = table.types
    return [
        tuple(_value(v, t) for v, t in zip(row, types, strict=True))
        for row in df.select(list(table.names)).iter_rows()
    ]


def _copy(conn, table: Table, rows: Sequence[tuple]) -> int:
    from psycopg import sql

    if not rows:
        return 0
    stmt = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(table.name), sql.SQL(", ").join(map(sql.Identifier, table.names))
    )
    with conn.cursor() as cur, cur.copy(stmt) as cp:
        cp.set_types(list(table.types))
        for r in rows:
            cp.write_row(r)
    return len(rows)


def _upsert(conn, table: Table, rows: Sequence[tuple]) -> int:
    """INSERT ... ON CONFLICT (key) DO UPDATE, only where something changed; returns the rows
    inserted or changed."""
    from psycopg import sql

    if not rows:
        return 0
    cols = [sql.Identifier(c) for c in table.names]
    keep = KEEP_ON_CONFLICT.get(table.name, ())
    rest = [c for c in table.names if c not in table.key and c not in keep]
    stmt = sql.SQL(
        "INSERT INTO {t} ({cols}) VALUES ({vals}) ON CONFLICT ({key}) DO UPDATE SET {sets} "
        "WHERE ({old}) IS DISTINCT FROM ({new})"
    ).format(
        t=sql.Identifier(table.name),
        cols=sql.SQL(", ").join(cols),
        vals=sql.SQL(", ").join(sql.SQL("%s::{}").format(sql.SQL(t)) for t in table.types),
        key=sql.SQL(", ").join(map(sql.Identifier, table.key)),
        sets=sql.SQL(", ").join(
            sql.SQL("{c} = EXCLUDED.{c}").format(c=sql.Identifier(c)) for c in rest
        ),
        old=sql.SQL(", ").join(
            sql.SQL("{t}.{c}").format(t=sql.Identifier(table.name), c=sql.Identifier(c))
            for c in rest
        ),
        new=sql.SQL(", ").join(sql.SQL("EXCLUDED.{c}").format(c=sql.Identifier(c)) for c in rest),
    )
    with conn.cursor() as cur:
        cur.executemany(stmt, rows)
        # rows inserted or changed (psycopg sums executemany's counts); unchanged rows: 0
        return max(int(cur.rowcount), 0)


def _replace(conn, table: Table, rows: Sequence[tuple]) -> int:
    from psycopg import sql

    conn.execute(sql.SQL("DELETE FROM {}").format(sql.Identifier(table.name)))
    return _copy(conn, table, rows)


# --------------------------------------------------------------------------------------
# The target's state
# --------------------------------------------------------------------------------------


class SchemaMismatchError(PublishError):
    pass


def check_schema(conn) -> None:
    """Every table and column this code writes exists in the target (current schema)."""
    have: dict[str, set[str]] = {}
    for table, column in conn.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema()"
    ).fetchall():
        have.setdefault(table, set()).add(column)
    problems = []
    for t in TABLES.values():
        if t.name not in have:
            problems.append(f"table {t.name} is missing")
            continue
        lost = [c for c in t.names if c not in have[t.name]]
        if lost:
            problems.append(f"{t.name} lacks {lost}")
    if problems:
        raise SchemaMismatchError(
            "the target's tables do not match this code: "
            + "; ".join(problems[:5])
            + ". Apply the migrations first (local: `uv run twm migrate-local`; Neon: "
            "docs/deploy.md step 4)."
        )


RADAR = FAMILIES["waiver_radar"]


def _key3(row: Sequence[Any], family: Family) -> tuple[int, int, str]:
    """A live list's key as (season, week, position); position '' without positions."""
    return (int(row[0]), int(row[1]), str(row[2]) if len(family.key) > 2 else "")


def target_live(conn, family: Family = RADAR) -> dict[tuple[int, int, str], dict[str, Any]]:
    """The published live lists of ``family``: (season, week, position) -> model_version,
    generated_at, and the listed ids in order."""
    from psycopg import sql

    key = sql.SQL(", ").join(sql.SQL("l.{}").format(sql.Identifier(c)) for c in family.key)
    using = sql.SQL(", ").join(map(sql.Identifier, [*family.key, "kind"]))
    n = len(family.key)
    stmt = sql.SQL(
        "SELECT {key}, l.{version}, l.generated_at, "
        "array_remove(array_agg(k.{row_id} ORDER BY k.{order}), NULL) "
        "FROM {lists} l LEFT JOIN {rows} k USING ({using}) "
        "WHERE l.kind = 'live' GROUP BY {groups}"
    ).format(
        key=key, version=sql.Identifier(family.version), row_id=sql.Identifier(family.row_id),
        order=sql.Identifier(family.order or family.row_id), lists=sql.Identifier(family.lists),
        rows=sql.Identifier(family.rows), using=using,
        groups=sql.SQL(", ".join(str(i) for i in range(1, n + 3))),
    )  # fmt: skip
    out: dict[tuple[int, int, str], dict[str, Any]] = {}
    for row in conn.execute(stmt).fetchall():
        version, generated, picks = row[n], row[n + 1], row[n + 2]
        out[_key3(row, family)] = {
            "model_version": version, "generated_at": generated, "picks": list(picks or []),
        }  # fmt: skip
    return out


def local_live_weeks(data: PublishData) -> set[tuple[int, int]]:
    """(season, week) of every local live list, whatever the module."""
    return {(int(s), int(w)) for d in data.families.values()
            for s, w in d.lists.filter(pl.col("kind") == "live").select("season", "week")
            .iter_rows()}  # fmt: skip


def plan_live(
    data: PublishData,
    published: dict[tuple[int, int, str], dict[str, Any]],
    *,
    allow_incomplete: bool,
    replace_live: Iterable[tuple[int, int]],
    module: str = "waiver_radar",
) -> tuple[list[LiveDecision], pl.DataFrame]:
    """What happens to each local live list of ``module`` (see the module docstring) and the
    list keys to write. Raises when a named replacement is impossible (a week no module has a
    local live list of; an incomplete list without ``allow_incomplete``). A week named by
    ``replace_live`` replaces the module's published lists of that week only where the module
    has a local list of it."""
    fam = FAMILIES[module]
    weeks = sorted(set(replace_live))
    have = local_live_weeks(data)
    for s, w in weeks:
        if (s, w) not in have:
            raise PublishError(
                f"--replace-live {s}-W{w:02d}: the local predictions store has no live list of "
                f"{s} week {w} to replace it with; nothing was published"
            )
    d = data.families[module]
    live = d.lists.filter(pl.col("kind") == "live")
    rows = d.rows.filter(pl.col("kind") == "live")
    mine_weeks = {(int(s), int(w)) for s, w in live.select("season", "week").iter_rows()}
    decisions: list[LiveDecision] = []
    write: list[tuple] = []
    for r in live.iter_rows(named=True):
        key = _key3([r[c] for c in fam.key], fam)
        replacing = key[:2] in weeks and key[:2] in mine_weeks
        old = published.get(key)
        if r["incomplete"] and not allow_incomplete and (replacing or old is None):
            if replacing:
                raise PublishError(
                    f"--replace-live {key[0]}-W{key[1]:02d}: the local {fam.label or 'Radar'} "
                    f"{key[2]} list was scored with incomplete data; add --allow-incomplete to "
                    "publish it anyway. Nothing was published."
                )
            decisions.append(LiveDecision(*key, "skipped", "scored with incomplete data",
                                          module=module))  # fmt: skip
            continue
        match = pl.lit(True)
        for c in fam.key:
            match = match & (pl.col(c) == r[c])
        mine = rows.filter(match).sort(fam.order or fam.row_id).get_column(fam.row_id).to_list()
        version = r[fam.version]
        if replacing:
            detail = (
                "nothing published yet" if old is None else
                f"replaces the list of {old['model_version']} generated "
                f"{old['generated_at']:%Y-%m-%d %H:%M} UTC ({len(old['picks'])} picks, No. 1 "
                f"{(old['picks'] or ['-'])[0]}) with {version} generated "
                f"{r['generated_at']:%Y-%m-%d %H:%M} UTC ({len(mine)} picks, No. 1 "
                f"{(mine or ['-'])[0]})"
            )  # fmt: skip
            decisions.append(LiveDecision(*key, "replace", detail, module=module))
            write.append(tuple(r[c] for c in fam.key))
        elif old is not None:
            same = old["picks"] == mine and old["model_version"] == version
            detail = "" if same else (
                "the local list differs from the published one; the published one is kept "
                f"(to replace it: --replace-live {key[0]}-W{key[1]:02d})"
            )  # fmt: skip
            decisions.append(LiveDecision(*key, "kept", detail, module=module))
        else:
            decisions.append(LiveDecision(*key, "insert", module=module))
            write.append(tuple(r[c] for c in fam.key))
    schema = {c: TABLES[fam.lists].types[TABLES[fam.lists].names.index(c)] for c in fam.key}
    keys = pl.DataFrame(write, schema={c: pl.Int32 if schema[c] == "integer" else pl.String
                                       for c in fam.key}, orient="row")  # fmt: skip
    return decisions, keys


def _key_value(v: Any) -> Any:
    """A snapshot key's value as both sides compare it (aware UTC times, plain ints)."""
    if isinstance(v, datetime):
        return v.astimezone(UTC)
    return int(v) if isinstance(v, int) else v


def target_snapshots(conn, snap: Snapshots = QUESTIONABLE) -> set[tuple]:
    """The key of every snapshot of ``snap`` in the target: (season, week, as_of) (feature #1:
    the Questionable list; feature #5: Teammate out) or (season, through_week) (feature #6,
    the playoff planner)."""
    cols = ", ".join(f'"{c}"' for c in snap.key)
    rows = conn.execute(f'SELECT {cols} FROM "{snap.lists}"').fetchall()
    return {tuple(_key_value(v) for v in r) for r in rows}


def plan_snapshots(q: Any, published: set[tuple],
                   snap: Snapshots = QUESTIONABLE) -> tuple[
        list[LiveDecision], pl.DataFrame]:  # fmt: skip
    """The local snapshots to insert (absent in the target; the rest are kept: append-only)
    and an *insert* decision for each (labelled by season, its week and its as-of)."""
    key = list(snap.key)
    new, out = [], []
    for r in q.lists.select(*key, pl.col("as_of").alias("_as_of")).iter_rows():
        if tuple(_key_value(v) for v in r[:-1]) in published:
            continue
        new.append(r[:-1])
        out.append(LiveDecision(int(r[0]), int(r[1]), f"as of {r[-1]:%Y-%m-%d %H:%M} UTC",
                                "insert", module=snap.module))  # fmt: skip
    keys = pl.DataFrame(new, schema=q.lists.select(key).schema, orient="row")
    return out, keys


def _latest_snapshot(conn, snap: Snapshots = QUESTIONABLE) -> dict[str, str]:
    """site_meta's newest snapshot of ``snap`` in the target ('questionable_latest_season' /
    '_week' / '_as_of', 'teammate_out_latest_season' ..., 'playoff_planner_latest_season' /
    '_through_week' / '_as_of')."""
    week = snap.key[1]
    row = conn.execute(f'SELECT season, "{week}", as_of FROM "{snap.lists}" '
                       f'ORDER BY as_of DESC, season DESC, "{week}" DESC LIMIT 1'
                       ).fetchone()  # fmt: skip
    keys = tuple(f"{snap.module}_latest_{k}" for k in ("season", week, "as_of"))
    if row is None:
        return dict.fromkeys(keys, "")
    return dict(zip(keys, (str(row[0]), str(row[1]), row[2].astimezone(UTC).isoformat()),
                    strict=True))  # fmt: skip


def snapshot_modules(data: PublishData) -> list[tuple[Snapshots, Any, Any]]:
    """(its tables, its data, its publish module) of each snapshot module the publish carries:
    the Questionable list (feature #1), Teammate out (feature #5), the playoff planner
    (feature #6)."""
    got = ((QUESTIONABLE, data.questionable, qn), (TEAMMATE_OUT, data.teammate_out, tn),
           (PLAYOFF_PLANNER, data.playoff_planner, pp))  # fmt: skip
    return [(sn, d, mod) for sn, d, mod in got if d is not None]


# --------------------------------------------------------------------------------------
# The publish
# --------------------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _connect(target: Target):
    import psycopg

    # prepare_threshold=None: no server-side prepared statements, so a connection pooler in
    # front of the database (Neon's -pooler endpoint) cannot break the publish
    return psycopg.connect(
        target.url, autocommit=True, connect_timeout=CONNECT_TIMEOUT_S,
        application_name="twm publish", prepare_threshold=None, **dict(target.connect_args),
    )  # fmt: skip


def publish(
    target: Target,
    data: PublishData,
    *,
    dry_run: bool = False,
    allow_incomplete: bool = False,
    replace_live: Iterable[tuple[int, int]] = (),
    run_id: str | None = None,
    started: datetime | None = None,
    connect: Callable[[Target], Any] = _connect,
    fail_after: str | None = None,
    allow_shrink: bool = False,
    max_shrink_share: float | None = None,
    notes: dict[str, Any] | None = None,
) -> PublishResult:
    """Write ``data`` to ``target`` (module docstring). ``allow_shrink`` turns the
    empty-replacement guard off (``max_shrink_share``: default settings
    ``publish.max_shrink_share``); ``notes`` are added to the run's pipeline_runs row.
    ``fail_after`` (tests only) raises right after the named step, to prove the rollback."""
    import psycopg

    run_id = run_id or uuid.uuid4().hex
    started = started or _now()
    if max_shrink_share is None:
        from twm.config import settings

        max_shrink_share = settings().publish.max_shrink_share
    extra = dict(notes or {})
    try:
        conn = connect(target)
    except psycopg.Error as e:
        raise PublishError(
            f"cannot connect to the {target.describe()}: {redact(str(e), target.url)}"
        ) from None
    try:
        try:
            result = _publish(conn, target, data, dry_run=dry_run,
                              allow_incomplete=allow_incomplete, replace_live=replace_live,
                              run_id=run_id, started=started, fail_after=fail_after,
                              allow_shrink=allow_shrink, max_shrink_share=max_shrink_share,
                              notes=extra)  # fmt: skip
        except Exception as e:
            message = redact(f"{type(e).__name__}: {e}", target.url)
            recorded = False
            if not dry_run:
                recorded = record_failure(
                    conn, run_id=run_id, started=started, target=target.name, error=message,
                    data_as_of=data.data_as_of,
                    notes={"step": getattr(e, "step", None), **extra},
                )  # fmt: skip
            raise PublishError(
                message, recorded=recorded, shrink=isinstance(e, ShrinkError)
            ) from None
    finally:
        conn.close()
    return result


class _StepError(RuntimeError):
    def __init__(self, step: str) -> None:
        super().__init__(f"forced failure after {step} (test)")
        self.step = step


# --------------------------------------------------------------------------------------
# The empty-replacement guard and the unchanged-table skip (reviewer's rules after E2)
# --------------------------------------------------------------------------------------

# The replaced tables, as the guard and the hashes see them (step P2: per module): the backtest
# part of each module's lists (live lists are frozen, never replaced), its outcomes and other
# replaced tables, and the tables every module shares (site_meta excepted).
BACKTEST_ONLY = {n for f in FAMILIES.values() for n in (f.lists, f.rows)}


def guarded(modules: Iterable[str]) -> tuple[str, ...]:
    """The tables the empty-replacement guard watches in a publish of ``modules``."""
    out = [n for m in FAMILIES if m in set(modules)
           for n in (FAMILIES[m].lists, FAMILIES[m].rows, FAMILIES[m].outcomes,
                     *FAMILIES[m].replaced)]  # fmt: skip
    return (*out, *SHARED)


def units(modules: Iterable[str]) -> dict[str, tuple[str, ...]]:
    """Hash unit -> the tables it covers, in write order: each module's backtest lists (its
    lists and rows together), its outcomes and other replaced tables, then the shared ones."""
    out: dict[str, tuple[str, ...]] = {}
    for m in [m for m in FAMILIES if m in set(modules)]:
        f = FAMILIES[m]
        out[f.unit] = (f.lists, f.rows)
        out.update({n: (n,) for n in (f.outcomes, *f.replaced)})
    out.update({n: (n,) for n in SHARED})
    return out


def _jsonable(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


def content_hash(*frames: pl.DataFrame) -> str:
    """A version-tagged sha256 of the frames' columns and rows (in order), from each row's
    canonical JSON: the same rows give the same hash, whatever the library versions."""
    import hashlib

    h = hashlib.sha256()
    for df in frames:
        h.update(json.dumps(list(df.columns)).encode())
        for row in df.iter_rows():
            h.update(json.dumps(row, default=_jsonable, separators=(",", ":")).encode())
            h.update(b"\n")
    return f"{HASH_VERSION}:{h.hexdigest()}"


def target_counts(conn, names: Iterable[str] = guarded(FAMILIES)) -> dict[str, int]:
    """Rows the target holds now in each guarded table (lists and rows: backtest only)."""
    out = {}
    for name in names:
        where = " WHERE kind = 'backtest'" if name in BACKTEST_ONLY else ""
        out[name] = int(conn.execute(f'SELECT count(*) FROM "{name}"{where}').fetchone()[0])
    return out


def shrink_problems(current: dict[str, int], new: dict[str, int], share: float) -> list[str]:
    """Tables that would lose more than ``share`` of the rows the target holds."""
    problems = []
    for name in current:
        cur, nxt = current.get(name, 0), new.get(name, 0)
        if cur > 0 and nxt < cur * (1 - share):
            what = f"{name} (backtest rows)" if name in BACKTEST_ONLY else name
            problems.append(
                f"{what} would go from {cur:,} to {nxt:,} rows ({(nxt - cur) / cur:+.1%})"
            )
    return problems


def split_parts(dec: Any) -> tuple[tuple[str, dict[str, pl.DataFrame]], ...]:
    """The Decision Report Card's two units and their rows: (history, current)."""
    return ((DECISIONS.history, dec.history), (DECISIONS.season, dec.current))


def split_where(unit: str, season: int) -> str:
    """The rows of ``unit`` in a split table: the history is every season before ``season``."""
    op = "<" if unit == DECISIONS.history else ">="
    return f"season {op} {int(season)}"


def split_key(name: str, unit: str) -> str:
    """'decision_fourth (history)' / 'decision_fourth (season)': the guard's and the counts'
    name of one part of a split table."""
    return f"{name} ({'history' if unit == DECISIONS.history else 'season'})"


def split_counts(conn, season: int) -> dict[str, int]:
    """Rows the target holds in each part of each split table."""
    out = {}
    for unit in (DECISIONS.history, DECISIONS.season):
        where = split_where(unit, season)
        for name in DECISIONS.tables:
            row = conn.execute(f'SELECT count(*) FROM "{name}" WHERE {where}').fetchone()
            out[split_key(name, unit)] = int(row[0])
    return out


def split_new_counts(dec: Any) -> dict[str, int]:
    return {split_key(n, unit): part[n].height for unit, part in split_parts(dec)
            for n in DECISIONS.tables}  # fmt: skip


def stored_hashes(conn) -> dict[str, str]:
    rows = conn.execute(
        "SELECT key, value FROM site_meta WHERE key LIKE %s", [HASH_PREFIX + "%"]
    ).fetchall()
    return {str(k)[len(HASH_PREFIX) :]: str(v) for k, v in rows}


def _target_keys(conn, family: Family, where: str = "") -> pl.DataFrame:
    """(season, week, row id) of the rows ``family`` holds in the target."""
    rows = conn.execute(
        f'SELECT DISTINCT season, week, "{family.row_id}" FROM "{family.rows}" {where}'
    ).fetchall()
    return pl.DataFrame(rows, orient="row", schema={"season": pl.Int32, "week": pl.Int32,
                                                    family.row_id: pl.String})  # fmt: skip


def _planned_frames(
    conn, data: PublishData, mods: Sequence[str], weeks: Sequence[tuple[int, int]]
) -> tuple[dict[str, pl.DataFrame], dict[str, tuple[pl.DataFrame, pl.DataFrame]],
           dict[str, list[tuple[int, int]]]]:  # fmt: skip
    """(frames, backs, replaced): what each replaced table will hold (a module's outcomes: of
    every row in the target after the publish, i.e. its local lists' and the frozen live
    lists' already there, less the live weeks it replaces; its other replaced tables; the
    shared ones), each module's backtest (lists, rows), and the live weeks each replaces."""
    frames: dict[str, pl.DataFrame] = {}
    backs: dict[str, tuple[pl.DataFrame, pl.DataFrame]] = {}
    replaced: dict[str, list[tuple[int, int]]] = {}
    for m in mods:
        f, d = FAMILIES[m], data.families[m]
        backs[m] = (d.lists.filter(pl.col("kind") == "backtest"),
                    d.rows.filter(pl.col("kind") == "backtest"))  # fmt: skip
        mine = {(int(s), int(w)) for s, w in d.lists.filter(pl.col("kind") == "live")
                .select("season", "week").iter_rows()}  # fmt: skip
        replaced[m] = [wk for wk in weeks if wk in mine]
        okey = ["season", "week", f.row_id]
        kept = _target_keys(conn, f, "WHERE kind = 'live'")
        if replaced[m]:
            gone = pl.DataFrame(replaced[m], schema={"season": pl.Int32, "week": pl.Int32},
                                orient="row")  # fmt: skip
            kept = kept.join(gone, on=["season", "week"], how="anti")
        keys = pl.concat([d.outcome_keys.select(okey), kept]).unique()
        frames[f.outcomes] = d.outcome_source.join(keys, on=okey).sort(okey)
        frames.update({n: data.tables[n] for n in f.replaced})
    frames.update({n: data.tables[n] for n in SHARED})
    return frames, backs, replaced


def _missing_outcomes(conn, mods: Sequence[str], frames: dict[str, pl.DataFrame]) -> list[str]:
    """A warning per module whose published rows (frozen ones included) lack an outcome row."""
    out = []
    for m in mods:
        f = FAMILIES[m]
        none = _target_keys(conn, f).join(frames[f.outcomes], on=["season", "week", f.row_id],
                                          how="anti")  # fmt: skip
        if none.height:
            r = none.row(0, named=True)
            what = "published picks" if not f.label else f"published {f.label} rows"
            out.append(
                f"{none.height} {what} have no outcome row in the dataset (e.g. {r[f.row_id]} "
                f"in {r['season']} week {r['week']}); the site shows them without an outcome"
            )
    return out


def _publish(
    conn,
    target: Target,
    data: PublishData,
    *,
    dry_run: bool,
    allow_incomplete: bool,
    replace_live: Iterable[tuple[int, int]],
    run_id: str,
    started: datetime,
    fail_after: str | None,
    allow_shrink: bool = False,
    max_shrink_share: float = 0.10,
    notes: dict[str, Any] | None = None,
) -> PublishResult:
    import psycopg

    def step(name: str) -> None:
        if fail_after == name:
            raise _StepError(name)

    written: dict[str, int] = {}
    warnings = list(data.warnings)
    unchanged: list[str] = []
    result: PublishResult | None = None
    weeks = sorted(set(replace_live))
    t = data.tables
    mods = [m for m in FAMILIES if m in data.families]
    # one transaction: any exception rolls it back
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", [LOCK_KEY])
        check_schema(conn)
        decisions: list[LiveDecision] = []
        plans: dict[str, pl.DataFrame] = {}
        for m in mods:
            dec, plans[m] = plan_live(data, target_live(conn, FAMILIES[m]),
                                      allow_incomplete=allow_incomplete, replace_live=weeks,
                                      module=m)  # fmt: skip
            decisions += dec
        # feature #1 / #5: append-only snapshots (the Questionable list, Teammate out)
        snaps = snapshot_modules(data)
        snap_keys: dict[str, pl.DataFrame] = {}
        for sn, sd, _ in snaps:
            s_dec, snap_keys[sn.module] = plan_snapshots(sd, target_snapshots(conn, sn), sn)
            decisions += s_dec
        step("plan")
        # what the replaced tables will hold (computed before anything is written)
        frames, backs, replaced_weeks = _planned_frames(conn, data, mods, weeks)
        dec = data.decisions
        if dec is not None:
            frames.update({n: t[n] for n in DECISIONS.replaced})
        for sn, sd, mod in snaps:
            frames.update({n: t[n] for n in sn.replaced})
            # the live record: every snapshot of the season the target holds after this run,
            # the published ones included (a fresh runner's store has only the night's)
            frames[f"{sn.module}_live"] = mod.live_record(sd, mod.published_rows(conn, sd.season))
        ct = data.coach_tendencies
        if ct is not None:  # feature #10: replaced tables rebuilt from the warehouse
            frames.update({n: t[n] for n in COACH_TENDENCIES})
        new_counts = {n: f.height for n, f in frames.items()}
        for m, (back, back_rows) in backs.items():
            new_counts[FAMILIES[m].lists] = back.height
            new_counts[FAMILIES[m].rows] = back_rows.height
        # 1. the empty-replacement guard
        current = target_counts(conn, guarded(mods))
        if dec is not None:
            current.update(target_counts(conn, DECISIONS.replaced))
            current.update(split_counts(conn, dec.season))
            new_counts.update(split_new_counts(dec))
        for sn, _, _ in snaps:
            current.update(target_counts(conn, sn.replaced))
        if ct is not None:
            current.update(target_counts(conn, COACH_TENDENCIES))
        problems = shrink_problems(current, new_counts, max_shrink_share)
        if problems and not allow_shrink:
            raise ShrinkError(
                "refused: " + "; ".join(problems) + f". A replaced table may lose at most "
                f"{max_shrink_share:.0%} of its rows (settings publish.max_shrink_share): the "
                "local inputs look missing or incomplete. Nothing was written; if the drop is "
                "intended, publish again with --allow-shrink."
            )
        if problems:
            warnings += [f"--allow-shrink: {p}" for p in problems]
        step("guard")
        # 2. content hashes: an unchanged table is not rewritten
        plan_units = units(mods)
        if dec is not None:
            plan_units.update({n: (n,) for n in DECISIONS.replaced})
        for sn, _, _ in snaps:
            plan_units.update({n: (n,) for n in sn.replaced})
        if ct is not None:
            plan_units.update({n: (n,) for n in COACH_TENDENCIES})
        hashes = {}
        for unit, names in plan_units.items():
            if len(names) == 2:  # a module's backtest lists
                back, back_rows = backs[next(m for m in mods if FAMILIES[m].unit == unit)]
                hashes[unit] = content_hash(back.drop("generated_at"),
                                            back_rows.select(TABLES[names[1]].names))  # fmt: skip
            else:
                hashes[unit] = content_hash(frames[unit].select(TABLES[unit].names))
        if dec is not None:
            for unit, part in split_parts(dec):
                hashes[unit] = content_hash(*(part[n].select(TABLES[n].names)
                                              for n in DECISIONS.tables))  # fmt: skip
        before = stored_hashes(conn)

        def same(unit: str) -> bool:
            if unit in (DECISIONS.history, DECISIONS.season):
                keys = [split_key(n, unit) for n in DECISIONS.tables]
                return before.get(unit) == hashes[unit] and all(
                    current[k] == new_counts[k] for k in keys)  # fmt: skip
            return before.get(unit) == hashes[unit] and all(
                current[n] == new_counts[n] for n in plan_units[unit]
            )

        # dim_coach: the decisions' coaches and (step H4a) the Hot-Seat Meter's
        dims = ("model_versions", "dim_team", "dim_player",
                *(DECISIONS.dims if dec is not None or "dim_coach" in t else ()))  # fmt: skip
        for name in dims:
            written[name] = _upsert(conn, TABLES[name], rows_of(t[name], TABLES[name]))
        step("upsert")
        for m in mods:  # backtest lists: replaced (unless unchanged)
            f, (back, back_rows) = FAMILIES[m], backs[m]
            written[f.lists] = written[f.rows] = 0
            if same(f.unit):
                unchanged.append(f.unit)
                continue
            conn.execute(f"DELETE FROM \"{f.rows}\" WHERE kind = 'backtest'")
            conn.execute(f"DELETE FROM \"{f.lists}\" WHERE kind = 'backtest'")
            written[f.lists] = _copy(conn, TABLES[f.lists], rows_of(back, TABLES[f.lists]))
            written[f.rows] = _copy(conn, TABLES[f.rows], rows_of(back_rows, TABLES[f.rows]))
        step("backtest_lists")
        for m in mods:  # live lists: frozen; only new ones (and named replacements) are written
            f, d = FAMILIES[m], data.families[m]
            for s, w in replaced_weeks[m]:
                for name in (f.rows, f.lists):
                    conn.execute(f'DELETE FROM "{name}" WHERE kind = \'live\' AND season = %s '
                                 "AND week = %s", [s, w])  # fmt: skip
            key = list(f.key)
            live = d.lists.filter(pl.col("kind") == "live").join(plans[m], on=key)
            live_rows = d.rows.filter(pl.col("kind") == "live").join(plans[m], on=key)
            written[f.lists] += _copy(conn, TABLES[f.lists], rows_of(live, TABLES[f.lists]))
            written[f.rows] += _copy(conn, TABLES[f.rows], rows_of(live_rows, TABLES[f.rows]))
        for sn, sd, _ in snaps:  # feature #1 / #5: only snapshots absent in the target
            key, keys = list(sn.key), snap_keys[sn.module]
            new_lists, new_rows = sd.lists.join(keys, on=key), sd.rows.join(keys, on=key)
            for name, frame in ((sn.lists, new_lists), (sn.rows, new_rows)):
                written[name] = _copy(conn, TABLES[name], rows_of(frame, TABLES[name]))
        step("live_lists")
        warnings += _missing_outcomes(conn, mods, frames)
        for unit, names in plan_units.items():
            if len(names) == 2:
                continue
            if same(unit):
                unchanged.append(unit)
                written[unit] = 0
                continue
            written[unit] = _replace(conn, TABLES[unit], rows_of(frames[unit], TABLES[unit]))
        step("replace")
        if dec is not None:  # step P3: the frozen history and the season in progress
            for name in DECISIONS.tables:
                written[name] = 0
            for unit, part in split_parts(dec):
                if same(unit):
                    unchanged.append(unit)
                    continue
                where = split_where(unit, dec.season)
                for name in reversed(DECISIONS.tables):
                    conn.execute(f'DELETE FROM "{name}" WHERE {where}')
                for name in DECISIONS.tables:
                    written[name] += _copy(conn, TABLES[name], rows_of(part[name], TABLES[name]))
        step("decisions")
        meta = dict(data.meta)
        meta.update(_latest_lists(conn))
        for sn, _, _ in snaps:
            meta.update(_latest_snapshot(conn, sn))
        meta.update({HASH_PREFIX + u: h for u, h in before.items() if u not in hashes})
        meta.update({HASH_PREFIX + u: h for u, h in hashes.items()})
        written["site_meta"] = _replace(conn, TABLES["site_meta"], sorted(meta.items()))
        counts = {
            name: int(conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0])
            for name in TABLES
        }
        if not dry_run:
            counts["pipeline_runs"] += 1
        sizes, db_bytes = _sizes(conn)
        result = PublishResult(
            run_id=run_id, target=target.name, dry_run=dry_run, written=written,
            counts=counts, sizes=sizes, database_bytes=db_bytes, live=decisions,
            warnings=warnings, unchanged=unchanged,
        )  # fmt: skip
        step("before_commit")
        if dry_run:
            raise psycopg.Rollback()
        _insert_run(
            conn, run_id=run_id, started=started, status="success", target=target.name,
            data_as_of=data.data_as_of, notes={**_run_notes(result), **(notes or {})},
        )  # fmt: skip
        written["pipeline_runs"] = 1
    assert result is not None
    return result


def _latest_lists(conn) -> dict[str, str]:
    """site_meta's latest list (any kind, and live) of every module: 'latest_list_season' ...
    for the Radar, 'stream_latest_list_season' ... and 'regression_latest_list_season' ...
    for the others (tables.Family.meta)."""
    out = {}
    for f in FAMILIES.values():
        for kind in ("live", None):
            where = "WHERE kind = 'live'" if kind else ""
            row = conn.execute(
                f'SELECT season, week FROM "{f.lists}" {where} '
                "ORDER BY season DESC, week DESC LIMIT 1"
            ).fetchone()
            prefix = f.meta + ("latest_live_list" if kind else "latest_list")
            out[f"{prefix}_season"] = "" if row is None else str(row[0])
            out[f"{prefix}_week"] = "" if row is None else str(row[1])
    return out


def _sizes(conn) -> tuple[dict[str, int], int]:
    names = list(TABLES)
    sizes = {
        str(name): int(size)
        for name, size in conn.execute(
            "SELECT c.relname, pg_total_relation_size(c.oid) FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = current_schema() AND c.relkind = 'r' AND c.relname = ANY(%s)",
            [names],
        ).fetchall()
    }
    db = conn.execute("SELECT pg_database_size(current_database())").fetchone()
    return sizes, int(db[0]) if db else 0


def _run_notes(result: PublishResult) -> dict[str, Any]:
    by: dict[str, list[str]] = {}
    for d in result.live:
        by.setdefault(d.action, []).append(d.label)
    return {
        "target": result.target,
        "written": result.written,
        "live": by,
        "warnings": result.warnings,
        "unchanged": result.unchanged,
    }


def _insert_run(
    conn,
    *,
    run_id: str,
    started: datetime,
    status: str,
    target: str,
    data_as_of: datetime | None,
    notes: dict[str, Any],
    stage: str = STAGE,
) -> None:
    from psycopg.types.json import Jsonb

    from twm.predictions import code_version

    code = code_version()
    sha = code.split("(")[-1].rstrip(")") if "(" in code else None
    conn.execute(
        "INSERT INTO pipeline_runs (run_id, started_at, finished_at, status, stage, git_sha, "
        "data_as_of, notes) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        [run_id, started, _now(), status, stage, sha, data_as_of,
         Jsonb({"target": target, **notes})],
    )  # fmt: skip


def record_failure(
    conn,
    *,
    run_id: str,
    started: datetime,
    target: str,
    error: str,
    data_as_of: datetime | None = None,
    notes: dict[str, Any] | None = None,
    stage: str = STAGE,
) -> bool:
    """Append a 'failed' pipeline_runs row in its own transaction (``stage``: the stage that
    failed, e.g. 'ingest' for the scheduled pipeline); False when that is not possible either
    (e.g. the tables do not exist yet)."""
    import psycopg

    try:
        with conn.transaction():
            _insert_run(
                conn, run_id=run_id, started=started, status="failed", target=target,
                data_as_of=data_as_of,
                notes={"error": error[:2000], **{k: v for k, v in (notes or {}).items() if v}},
                stage=stage,
            )  # fmt: skip
        return True
    except psycopg.Error:
        return False


def record_failure_at(target: Target, **kwargs: Any) -> bool:
    """:func:`record_failure` on a new connection (for errors before the publish connected)."""
    import psycopg

    try:
        conn = _connect(target)
    except psycopg.Error:
        return False
    try:
        return record_failure(conn, target=target.name, **kwargs)
    finally:
        conn.close()


LIVE_ACTIONS = {
    "insert": "published (new)",
    "kept": "already published, kept (live lists are frozen)",
    "replace": "REPLACED (--replace-live)",
    "skipped": "SKIPPED, scored with incomplete data (publish them with --allow-incomplete, or "
    "score the week again once its data is in)",
}


def summary_lines(result: PublishResult, target: Target) -> list[str]:
    """The printed summary: target, live lists, rows per table, sizes."""
    lines = [f"target: {target.describe()}"]
    if result.dry_run:
        lines.append("DRY RUN: everything below was rolled back; nothing was published")
    live = result.live
    if not live:
        lines.append("live lists: none in the local store (fine: backtest and reconstructed "
                     "lists, the track record and the player pages are published)")  # fmt: skip
    for action, text in LIVE_ACTIONS.items():
        these = [d for d in live if d.action == action]
        if these:
            lines.append(f"live lists {text}: " + ", ".join(d.label for d in these))
            lines += [f"  {d.label}: {d.detail}" for d in these if d.detail]
    width = max(len(n) for n in TABLES)
    lines.append(f"{'table':<{width}}  {'rows now':>10}  {'written':>9}  {'size':>9}")
    notes = table_notes(result)
    for name in TABLES:
        lines.append(
            f"{name:<{width}}  {result.counts.get(name, 0):>10,}  "
            f"{result.written.get(name, 0):>9,}  {_mb(result.sizes.get(name, 0)):>9}"
            + (f"  {notes[name]}" if notes.get(name) else "")
        )
    lines.append(
        f"total size of these tables: {_mb(result.total_bytes)} (whole database "
        f"{_mb(result.database_bytes)})"
    )
    lines += [f"warning: {w}" for w in result.warnings]
    if not result.dry_run:
        lines.append(f"pipeline run {result.run_id}: success")
    return lines


def table_notes(result: PublishResult) -> dict[str, str]:
    """Per table: 'unchanged (not rewritten)' where the content hash matched."""
    out: dict[str, str] = {}
    plan = units(FAMILIES)
    for unit in result.unchanged:
        if unit in (DECISIONS.history, DECISIONS.season):
            part = "history" if unit == DECISIONS.history else "season in progress"
            for n in DECISIONS.tables:
                out[n] = (out[n] + "; " if n in out else "") + f"{part} unchanged (not rewritten)"
            continue
        names = plan.get(unit, (unit,))
        for n in names:
            out[n] = "backtest rows unchanged (not rewritten)" if len(names) == 2 \
                else "unchanged (not rewritten)"  # fmt: skip
    return out


def result_json(result: PublishResult, target: Target) -> dict[str, Any]:
    """The machine-readable summary (``twm publish --result-json``; the pipeline's job summary
    reads it). No connection string: the target is described by host and database only."""
    by: dict[str, list[str]] = {}
    for d in result.live:
        by.setdefault(d.action, []).append(d.label)
    notes = table_notes(result)
    return {
        "status": "dry_run" if result.dry_run else "success", "run_id": result.run_id,
        "target": target.describe(), "live": by, "warnings": result.warnings,
        "unchanged": result.unchanged, "database_bytes": result.database_bytes,
        "tables": {n: {"rows": result.counts.get(n, 0), "written": result.written.get(n, 0),
                       "bytes": result.sizes.get(n, 0), "note": notes.get(n, "")}
                   for n in TABLES},
    }  # fmt: skip


def _mb(n: int) -> str:
    return f"{n / 1_048_576:.2f} MB"
