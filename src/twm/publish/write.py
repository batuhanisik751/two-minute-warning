"""Write a publish to Postgres in one transaction (step E2).

One run (:func:`publish`), all or nothing:

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
6. append a 'success' pipeline_runs row, count the rows and measure the tables, commit.

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

from twm.publish.collect import PublishData
from twm.publish.tables import REPLACED, TABLES, Table
from twm.publish.target import Target, redact

STAGE = "publish"
EXIT_SKIPPED_INCOMPLETE = 4  # published, but some live lists were incomplete: retry later
# pg_advisory_xact_lock key: any fixed 64-bit number shared by every twm publisher
LOCK_KEY = 7_274_871_120_260_928
CONNECT_TIMEOUT_S = 20
LIVE_KEY = ("season", "week", "position")


class PublishError(RuntimeError):
    """The publish failed; the target was rolled back (see ``recorded``)."""

    def __init__(self, message: str, *, recorded: bool = False) -> None:
        super().__init__(message)
        self.recorded = recorded


@dataclass
class LiveDecision:
    season: int
    week: int
    position: str
    action: str  # insert | kept | skipped | replace
    detail: str = ""

    @property
    def label(self) -> str:
        return f"{self.season}-W{self.week:02d} {self.position}"


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
    """INSERT ... ON CONFLICT (key) DO UPDATE, only where something changed."""
    from psycopg import sql

    if not rows:
        return 0
    cols = [sql.Identifier(c) for c in table.names]
    rest = [c for c in table.names if c not in table.key]
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
    return len(rows)


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


def target_live(conn) -> dict[tuple[int, int, str], dict[str, Any]]:
    """The published live lists: (season, week, position) -> model_version, generated_at,
    and the picks in rank order."""
    out: dict[tuple[int, int, str], dict[str, Any]] = {}
    for s, w, p, version, generated, picks in conn.execute(
        "SELECT l.season, l.week, l.position, l.model_version, l.generated_at, "
        "array_remove(array_agg(k.gsis_id ORDER BY k.rank), NULL) "
        "FROM radar_list l LEFT JOIN radar_pick k USING (season, week, position, kind) "
        "WHERE l.kind = 'live' GROUP BY 1, 2, 3, 4, 5"
    ).fetchall():
        out[(int(s), int(w), str(p))] = {
            "model_version": version, "generated_at": generated, "picks": list(picks or []),
        }  # fmt: skip
    return out


def plan_live(
    data: PublishData,
    published: dict[tuple[int, int, str], dict[str, Any]],
    *,
    allow_incomplete: bool,
    replace_live: Iterable[tuple[int, int]],
) -> tuple[list[LiveDecision], pl.DataFrame]:
    """What happens to each local live list (see the module docstring) and the list keys
    (season, week, position) to write. Raises when a named replacement is impossible."""
    weeks = sorted(set(replace_live))
    live = data.lists.filter(pl.col("kind") == "live")
    picks = data.picks.filter(pl.col("kind") == "live")
    local_weeks = {(int(s), int(w)) for s, w in live.select("season", "week").iter_rows()}
    for s, w in weeks:
        if (s, w) not in local_weeks:
            raise PublishError(
                f"--replace-live {s}-W{w:02d}: the local predictions store has no live list of "
                f"{s} week {w} to replace it with; nothing was published"
            )
    decisions: list[LiveDecision] = []
    write: list[tuple[int, int, str]] = []
    for r in live.iter_rows(named=True):
        key = (int(r["season"]), int(r["week"]), str(r["position"]))
        replacing = key[:2] in weeks
        old = published.get(key)
        if r["incomplete"] and not allow_incomplete and (replacing or old is None):
            if replacing:
                raise PublishError(
                    f"--replace-live {key[0]}-W{key[1]:02d}: the local {key[2]} list was scored "
                    "with incomplete data; add --allow-incomplete to publish it anyway. "
                    "Nothing was published."
                )
            decisions.append(LiveDecision(*key, "skipped", "scored with incomplete data"))
            continue
        mine = (
            picks.filter(
                (pl.col("season") == key[0]) & (pl.col("week") == key[1])
                & (pl.col("position") == key[2])
            ).sort("rank").get_column("gsis_id").to_list()
        )  # fmt: skip
        if replacing:
            detail = (
                "nothing published yet" if old is None else
                f"replaces the list of {old['model_version']} generated "
                f"{old['generated_at']:%Y-%m-%d %H:%M} UTC ({len(old['picks'])} picks, No. 1 "
                f"{(old['picks'] or ['-'])[0]}) with {r['model_version']} generated "
                f"{r['generated_at']:%Y-%m-%d %H:%M} UTC ({len(mine)} picks, No. 1 "
                f"{(mine or ['-'])[0]})"
            )  # fmt: skip
            decisions.append(LiveDecision(*key, "replace", detail))
            write.append(key)
        elif old is not None:
            same = old["picks"] == mine and old["model_version"] == r["model_version"]
            detail = "" if same else (
                "the local list differs from the published one; the published one is kept "
                f"(to replace it: --replace-live {key[0]}-W{key[1]:02d})"
            )  # fmt: skip
            decisions.append(LiveDecision(*key, "kept", detail))
        else:
            decisions.append(LiveDecision(*key, "insert"))
            write.append(key)
    keys = pl.DataFrame(
        write, schema={"season": pl.Int32, "week": pl.Int32, "position": pl.String}, orient="row"
    )
    return decisions, keys


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
) -> PublishResult:
    """Write ``data`` to ``target`` (module docstring). ``fail_after`` (tests only) raises
    right after the named step, to prove the rollback."""
    import psycopg

    run_id = run_id or uuid.uuid4().hex
    started = started or _now()
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
                              run_id=run_id, started=started, fail_after=fail_after)  # fmt: skip
        except Exception as e:
            message = redact(f"{type(e).__name__}: {e}", target.url)
            recorded = False
            if not dry_run:
                recorded = record_failure(
                    conn, run_id=run_id, started=started, target=target.name, error=message,
                    data_as_of=data.data_as_of, notes={"step": getattr(e, "step", None)},
                )  # fmt: skip
            raise PublishError(message, recorded=recorded) from None
    finally:
        conn.close()
    return result


class _StepError(RuntimeError):
    def __init__(self, step: str) -> None:
        super().__init__(f"forced failure after {step} (test)")
        self.step = step


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
) -> PublishResult:
    import psycopg

    def step(name: str) -> None:
        if fail_after == name:
            raise _StepError(name)

    written: dict[str, int] = {}
    warnings = list(data.warnings)
    result: PublishResult | None = None
    # one transaction: any exception rolls it back
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", [LOCK_KEY])
        check_schema(conn)
        published = target_live(conn)
        decisions, live_keys = plan_live(
            data, published, allow_incomplete=allow_incomplete, replace_live=replace_live
        )
        step("plan")
        t = data.tables
        for name in ("model_versions", "dim_team", "dim_player"):
            written[name] = _upsert(conn, TABLES[name], rows_of(t[name], TABLES[name]))
        step("upsert")
        # backtest lists: replaced
        conn.execute("DELETE FROM radar_pick WHERE kind = 'backtest'")
        conn.execute("DELETE FROM radar_list WHERE kind = 'backtest'")
        back = data.lists.filter(pl.col("kind") == "backtest")
        back_picks = data.picks.filter(pl.col("kind") == "backtest")
        n_lists = _copy(conn, TABLES["radar_list"], rows_of(back, TABLES["radar_list"]))
        n_picks = _copy(conn, TABLES["radar_pick"], rows_of(back_picks, TABLES["radar_pick"]))
        step("backtest_lists")
        # live lists: frozen; only new ones (and named replacements) are written
        for s, w in sorted(set(replace_live)):
            conn.execute(
                "DELETE FROM radar_pick WHERE kind = 'live' AND season = %s AND week = %s",
                [s, w],
            )
            conn.execute(
                "DELETE FROM radar_list WHERE kind = 'live' AND season = %s AND week = %s",
                [s, w],
            )
        key = list(LIVE_KEY)
        live = data.lists.filter(pl.col("kind") == "live").join(live_keys, on=key)
        live_picks = data.picks.filter(pl.col("kind") == "live").join(live_keys, on=key)
        n_lists += _copy(conn, TABLES["radar_list"], rows_of(live, TABLES["radar_list"]))
        n_picks += _copy(conn, TABLES["radar_pick"], rows_of(live_picks, TABLES["radar_pick"]))
        written["radar_list"], written["radar_pick"] = n_lists, n_picks
        step("live_lists")
        # outcomes: every pick in the target (frozen live lists included) + the local keys
        target_keys = pl.DataFrame(
            conn.execute("SELECT DISTINCT season, week, gsis_id FROM radar_pick").fetchall(),
            schema={"season": pl.Int32, "week": pl.Int32, "gsis_id": pl.String},
            orient="row",
        )
        keys = pl.concat([data.outcome_keys, target_keys]).unique()
        outcomes = data.outcome_source.join(keys, on=["season", "week", "gsis_id"])
        no_outcome = target_keys.join(outcomes, on=["season", "week", "gsis_id"], how="anti")
        if no_outcome.height:
            r = no_outcome.row(0, named=True)
            warnings.append(
                f"{no_outcome.height} published picks have no outcome row in the dataset "
                f"(e.g. {r['gsis_id']} in {r['season']} week {r['week']}); the site shows "
                "them without an outcome"
            )
        written["radar_outcome"] = _replace(
            conn, TABLES["radar_outcome"],
            rows_of(outcomes.sort("season", "week", "gsis_id"), TABLES["radar_outcome"]),
        )  # fmt: skip
        for name in REPLACED:
            if name in ("radar_outcome", "site_meta"):
                continue
            written[name] = _replace(conn, TABLES[name], rows_of(t[name], TABLES[name]))
        step("replace")
        meta = dict(data.meta)
        meta.update(_latest_lists(conn))
        meta_rows = sorted(meta.items())
        written["site_meta"] = _replace(conn, TABLES["site_meta"], meta_rows)
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
            warnings=warnings,
        )  # fmt: skip
        step("before_commit")
        if dry_run:
            raise psycopg.Rollback()
        _insert_run(
            conn, run_id=run_id, started=started, status="success", target=target.name,
            data_as_of=data.data_as_of, notes=_run_notes(result),
        )  # fmt: skip
        written["pipeline_runs"] = 1
    assert result is not None
    return result


def _latest_lists(conn) -> dict[str, str]:
    out = {}
    for kind in ("live", None):
        where = "WHERE kind = 'live'" if kind else ""
        row = conn.execute(
            f"SELECT season, week FROM radar_list {where} ORDER BY season DESC, week DESC LIMIT 1"
        ).fetchone()
        prefix = "latest_live_list" if kind else "latest_list"
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
) -> None:
    from psycopg.types.json import Jsonb

    from twm.predictions import code_version

    code = code_version()
    sha = code.split("(")[-1].rstrip(")") if "(" in code else None
    conn.execute(
        "INSERT INTO pipeline_runs (run_id, started_at, finished_at, status, stage, git_sha, "
        "data_as_of, notes) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        [run_id, started, _now(), status, STAGE, sha, data_as_of,
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
) -> bool:
    """Append a 'failed' pipeline_runs row in its own transaction; False when that is not
    possible either (e.g. the tables do not exist yet)."""
    import psycopg

    try:
        with conn.transaction():
            _insert_run(
                conn, run_id=run_id, started=started, status="failed", target=target,
                data_as_of=data_as_of,
                notes={"error": error[:2000], **{k: v for k, v in (notes or {}).items() if v}},
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
    for name in TABLES:
        lines.append(
            f"{name:<{width}}  {result.counts.get(name, 0):>10,}  "
            f"{result.written.get(name, 0):>9,}  {_mb(result.sizes.get(name, 0)):>9}"
        )
    lines.append(
        f"total size of these tables: {_mb(result.total_bytes)} (whole database "
        f"{_mb(result.database_bytes)})"
    )
    lines += [f"warning: {w}" for w in result.warnings]
    if not result.dry_run:
        lines.append(f"pipeline run {result.run_id}: success")
    return lines


def _mb(n: int) -> str:
    return f"{n / 1_048_576:.2f} MB"
