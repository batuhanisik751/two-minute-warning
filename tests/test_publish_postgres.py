"""`twm publish` against a real Postgres (marker ``postgres``; step E2).

Run with ``uv run pytest -m postgres`` while the local database is up (``docker compose up -d
twm-postgres``; CI starts a postgres:16 service). The tests never touch the ``twm`` database
itself: the session migrates a fresh template database (web/drizzle/*.sql, applied by
:mod:`twm.publish.migrate`) and every test gets its own copy, dropped afterwards. Without a
reachable server they are skipped with the reason, unless ``TWM_REQUIRE_POSTGRES=1`` (CI),
where they fail.
"""

from __future__ import annotations

import csv
import os
import uuid
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import polars as pl
import pytest

from tests import publish_synthetic as ps
from twm.config import ROOT
from twm.publish import collect as col
from twm.publish import migrate as mg
from twm.publish import target as tg
from twm.publish import write as wr
from twm.publish.tables import TABLES

pytestmark = pytest.mark.postgres
psycopg = pytest.importorskip("psycopg")
from psycopg.conninfo import make_conninfo  # noqa: E402

NO_ENV_FILE = Path("/nonexistent/.env")
PREFIX = f"twm_test_{os.getpid()}_{uuid.uuid4().hex[:6]}"
ROLE_NAMES = ("twm_web", "twm_job")


def _admin_url() -> str:
    env = {k: v for k, v in os.environ.items() if k == tg.LOCAL_ENV}
    return tg.resolve("local", env=env, env_file=NO_ENV_FILE).url


@pytest.fixture(scope="session")
def server() -> Iterator[str]:
    """The local server's admin URL, with a migrated template database for the session."""
    url = _admin_url()
    try:
        admin = psycopg.connect(url, autocommit=True, connect_timeout=5)
    except psycopg.OperationalError:
        msg = (
            "local Postgres is not reachable (start it with `docker compose up -d "
            "twm-postgres`, or set TWM_LOCAL_DATABASE_URL)"
        )
        if os.environ.get("TWM_REQUIRE_POSTGRES") == "1":
            pytest.fail(msg)
        pytest.skip(msg)
    template = f"{PREFIX}_tmpl"
    admin.execute(f'CREATE DATABASE "{template}"')
    with psycopg.connect(make_conninfo(url, dbname=template), autocommit=True) as conn:
        assert mg.apply_migrations(conn) == [m.tag for m in mg.read_migrations()]
    try:
        yield url
    finally:
        rows = admin.execute(
            "SELECT datname FROM pg_database WHERE datname LIKE %s", [f"{PREFIX}%"]
        ).fetchall()
        for (name,) in rows:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


@pytest.fixture
def db(server: str) -> Iterator[tg.Target]:
    """A fresh, migrated database (a copy of the session's template) as a local target."""
    name = f"{PREFIX}_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(server, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}" TEMPLATE "{PREFIX}_tmpl"')
    url = make_conninfo(server, dbname=name)
    yield tg.resolve("local", env={tg.LOCAL_ENV: url}, env_file=NO_ENV_FILE)
    with psycopg.connect(server, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def dump(target: tg.Target, *, skip: tuple[str, ...] = ("pipeline_runs",)) -> dict[str, list]:
    """Every table's rows in key order (the content a publish is judged by)."""
    out = {}
    with psycopg.connect(target.url) as conn:
        for name, t in TABLES.items():
            if name in skip:
                continue
            cols = ", ".join(f'"{c}"' for c in t.names)
            order = ", ".join(f'"{c}"' for c in t.key)
            out[name] = conn.execute(f'SELECT {cols} FROM "{name}" ORDER BY {order}').fetchall()
    return out


def rows(target: tg.Target, query: str, params: list | None = None) -> list[tuple]:
    with psycopg.connect(target.url) as conn:
        return conn.execute(query, params or []).fetchall()


def publish(target: tg.Target, inputs, **kwargs) -> wr.PublishResult:
    data = col.collect(inputs)
    assert col.validate(data) == []
    return wr.publish(target, data, **kwargs)


# --------------------------------------------------------------------------------------
# The schema
# --------------------------------------------------------------------------------------

PG_TYPES = {"timestamptz": "timestamp with time zone", "integer[]": "_int4",
            "text[]": "_text", "double precision[]": "_float8"}  # fmt: skip


def test_migrated_columns_equal_the_writer_tables(db: tg.Target) -> None:
    got: dict[str, list[tuple[str, str]]] = {}
    for table, column, dtype, udt in rows(
        db,
        "SELECT table_name, column_name, data_type, udt_name FROM information_schema.columns "
        "WHERE table_schema = 'public' ORDER BY table_name, ordinal_position",
    ):
        got.setdefault(table, []).append((column, udt if dtype == "ARRAY" else dtype))
    want = {
        name: [(c, PG_TYPES.get(t, t)) for c, t in table.columns] for name, table in TABLES.items()
    }
    assert got == want


def test_migrations_are_applied_once_and_match_drizzle(db: tg.Target) -> None:
    with psycopg.connect(db.url, autocommit=True) as conn:
        assert mg.apply_migrations(conn) == []
    ledger = rows(db, "SELECT hash, created_at FROM drizzle.__drizzle_migrations ORDER BY id")
    assert ledger == [(m.hash, m.when) for m in mg.read_migrations()]


# --------------------------------------------------------------------------------------
# Publishing
# --------------------------------------------------------------------------------------


def test_publish_row_counts_and_size(db: tg.Target, tmp_path: Path) -> None:
    syn = ps.build(tmp_path, live_weeks=(1,))
    res = publish(db, syn.inputs, run_id="run-1")
    lists = 2 * 3 * 4 + 4
    expected = {
        "radar_list": lists, "radar_pick": lists * 25, "dim_team": 4, "model_versions": 3,
        # outcomes: 6 backtest weeks x 100 picks, plus 3 current weeks x 4 positions x 31
        "radar_outcome": 600 + 3 * 4 * 31, "track_record": len(syn.csv_rows),
        "tier_stats": 3 * 4, "player_week_summary": 64, "dim_player": 4 * 31,
        "pipeline_runs": 1,
    }  # fmt: skip
    for name, n in expected.items():
        assert res.counts[name] == n, name
        assert rows(db, f'SELECT count(*) FROM "{name}"')[0][0] == n, name
    assert res.counts["glossary"] == len(col.glossary())
    assert [d.action for d in res.live] == ["insert"] * 4
    # sizes: every table measured, the whole publish far below the 200 MB target
    assert set(res.sizes) == set(TABLES) and all(v > 0 for v in res.sizes.values())
    assert 0 < res.total_bytes < 200 * 1_048_576
    # one successful run, with what it wrote
    run = rows(db, "SELECT run_id, status, stage, notes FROM pipeline_runs")
    assert run[0][:3] == ("run-1", "success", "publish")
    assert run[0][3]["live"] == {"insert": [f"2026-W01 {p}" for p in ("QB", "RB", "WR", "TE")]}
    # a live pick as published
    pick = rows(db, "SELECT gsis_id, team, chance, chance_low, chance_high, model_prob, tier, "
                    "reasons FROM radar_pick WHERE kind = 'live' AND position = 'QB' "
                    "AND rank = 1")[0]  # fmt: skip
    assert pick[:2] == (ps.gid(0, 0), "BUF") and pick[2] == pytest.approx(0.875)
    assert pick[6] == "must-add" and pick[7][0].startswith("Reason 1")
    meta = dict(rows(db, "SELECT key, value FROM site_meta"))
    assert meta["latest_live_list_week"] == "1" and meta["current_season"] == "2026"
    assert meta["generated_at"].startswith("2026-09-28T20:00")
    outcome = rows(db, "SELECT window_ranks, window_points FROM radar_outcome WHERE season = "
                       "2024 AND week = 1 AND gsis_id = %s", [ps.gid(0, 0)])  # fmt: skip
    assert outcome == [([1, None, 3], [10.5, None, 3.25])]


def test_track_record_in_the_database_equals_the_csv(db: tg.Target, tmp_path: Path) -> None:
    syn = ps.build(tmp_path)
    publish(db, syn.inputs)
    with syn.inputs.evaluation_csv.open() as f:
        csv_rows = list(csv.DictReader(f))
    got = rows(
        db,
        "SELECT label, excl_rostered, model, scope, scope_value, season_from, season_to, "
        "metric, value, low, high, n_lists, n_positives, n_rows, n_top_hits, n_top "
        "FROM track_record",
    )
    assert len(got) == len(csv_rows)

    def num(x: str, f=float):
        return f(x) if x else None

    want = {
        (r["label"], r["subset"] == "without_rostered", r["model"], r["scope"], r["key"],
         int(r["seasons"].split("-")[0]), int(r["seasons"].split("-")[-1]), r["metric"],
         num(r["value"]), num(r["lo"]), num(r["hi"]), num(r["n_groups"], int),
         num(r["n_pos"], int), num(r["n_rows"], int), num(r["n_top_hits"], int),
         num(r["n_top"], int))
        for r in csv_rows
    }  # fmt: skip
    assert set(got) == want


def test_publish_twice_gives_identical_tables(db: tg.Target, tmp_path: Path) -> None:
    syn = ps.build(tmp_path, live_weeks=(1, 2))
    publish(db, syn.inputs)
    first = dump(db)
    res = publish(db, syn.inputs)
    assert dump(db) == first
    assert [d.action for d in res.live] == ["kept"] * 8
    assert all(d.detail == "" for d in res.live)  # identical lists: nothing to report
    assert rows(db, "SELECT count(*), count(DISTINCT run_id) FROM pipeline_runs")[0] == (2, 2)


def test_dry_run_changes_nothing(db: tg.Target, tmp_path: Path) -> None:
    syn = ps.build(tmp_path)
    before = dump(db, skip=())
    res = publish(db, syn.inputs, dry_run=True)
    assert res.dry_run and res.counts["radar_list"] == 28
    assert dump(db, skip=()) == before  # nothing, not even a pipeline_runs row


def test_forced_error_rolls_back_and_records_a_failed_run(db: tg.Target, tmp_path: Path) -> None:
    syn = ps.build(tmp_path, live_weeks=(1,))
    publish(db, syn.inputs)
    before = dump(db)
    ps.write_live_week(syn.inputs.store, 2)  # a change that would be written
    for step in ("upsert", "backtest_lists", "live_lists", "replace", "before_commit"):
        with pytest.raises(wr.PublishError, match=f"forced failure after {step}") as e:
            publish(db, syn.inputs, fail_after=step, run_id=f"fail-{step}")
        assert e.value.recorded
        assert dump(db) == before, step
    failed = rows(db, "SELECT run_id, status, notes FROM pipeline_runs WHERE status = 'failed' "
                      "ORDER BY run_id")  # fmt: skip
    assert len(failed) == 5 and all("forced failure" in r[2]["error"] for r in failed)
    assert rows(db, "SELECT count(*) FROM pipeline_runs WHERE status = 'success'")[0][0] == 1


def test_a_database_without_the_tables_is_refused(server: str, tmp_path: Path) -> None:
    name = f"{PREFIX}_empty_{uuid.uuid4().hex[:6]}"
    with psycopg.connect(server, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    target = tg.resolve("local", env={tg.LOCAL_ENV: make_conninfo(server, dbname=name)},
                        env_file=NO_ENV_FILE)  # fmt: skip
    syn = ps.build(tmp_path)
    with pytest.raises(wr.PublishError, match="migrations") as e:
        publish(target, syn.inputs)
    assert not e.value.recorded  # no pipeline_runs table to record it in


# --------------------------------------------------------------------------------------
# Live lists are frozen (the reviewer's rules of 2026-09-28)
# --------------------------------------------------------------------------------------


def _week(target: tg.Target, week: int) -> tuple[list, list]:
    lists = rows(target, "SELECT * FROM radar_list WHERE kind = 'live' AND week = %s "
                         "ORDER BY position", [week])  # fmt: skip
    picks = rows(target, "SELECT * FROM radar_pick WHERE kind = 'live' AND week = %s "
                         "ORDER BY position, rank", [week])  # fmt: skip
    return lists, picks


def _store_with(tmp_path: Path, name: str, weeks: dict[int, dict]) -> Path:
    store = tmp_path / name
    ps.write_backtest(store)
    for w, kwargs in weeks.items():
        ps.write_live_week(store, w, **kwargs)
    return store


def test_live_lists_are_frozen(db: tg.Target, tmp_path: Path) -> None:
    syn = ps.build(tmp_path / "a", live_weeks=(1,))
    publish(db, syn.inputs)
    week1 = _week(db, 1)
    assert len(week1[0]) == 4 and len(week1[1]) == 100

    # 1. a store that no longer has week 1 but has week 2: both weeks remain, week 1 unchanged
    store_b = _store_with(tmp_path, "b.duckdb", {2: {}})
    res = publish(db, ps.with_store(syn, store_b))
    assert _week(db, 1) == week1
    assert len(_week(db, 2)[1]) == 100
    assert {d.action for d in res.live} == {"insert"}
    # the outcomes still cover week 1's picks (from the dataset)
    assert rows(db, "SELECT count(*) FROM radar_pick p LEFT JOIN radar_outcome o USING "
                    "(season, week, gsis_id) WHERE p.kind = 'live' AND o.gsis_id IS NULL"
                )[0][0] == 0  # fmt: skip

    # 2. week 1 scored again differently: without the flag, the published list is kept
    store_c = _store_with(tmp_path, "c.duckdb", {1: {"shift": 5}, 2: {}})
    res = publish(db, ps.with_store(syn, store_c))
    assert _week(db, 1) == week1
    kept = [d for d in res.live if d.week == 1]
    assert {d.action for d in kept} == {"kept"}
    assert all("--replace-live 2026-W01" in d.detail for d in kept)

    # 3. with --replace-live 2026-W01 it is replaced, and the summary says what it replaced
    res = publish(db, ps.with_store(syn, store_c), replace_live=[(2026, 1)])
    new1 = _week(db, 1)
    assert new1 != week1 and len(new1[1]) == 100
    first = rows(db, "SELECT gsis_id FROM radar_pick WHERE kind = 'live' AND week = 1 AND "
                     "position = 'QB' AND rank = 1")[0][0]  # fmt: skip
    assert first == ps.gid(0, 5)
    replaced = [d for d in res.live if d.action == "replace"]
    assert len(replaced) == 4 and all("replaces the list of" in d.detail for d in replaced)
    assert any("REPLACED" in line for line in wr.summary_lines(res, db))

    # 4. a store without any live list never removes the published ones
    store_d = _store_with(tmp_path, "d.duckdb", {})
    res = publish(db, ps.with_store(syn, store_d))
    assert res.live == [] and _week(db, 1) == new1 and len(_week(db, 2)[1]) == 100


def test_replace_live_needs_a_local_list(db: tg.Target, tmp_path: Path) -> None:
    syn = ps.build(tmp_path, live_weeks=(1,))
    publish(db, syn.inputs)
    before = dump(db)
    with pytest.raises(wr.PublishError, match="no live list of 2026 week 3"):
        publish(db, syn.inputs, replace_live=[(2026, 3)])
    assert dump(db) == before


def test_incomplete_live_lists_are_skipped(db: tg.Target, tmp_path: Path) -> None:
    syn = ps.build(tmp_path, live_weeks=(1,))
    ps.write_live_week(syn.inputs.store, 2, incomplete=True)
    res = publish(db, syn.inputs)
    assert [d.label for d in res.skipped] == [f"2026-W02 {p}" for p in ("QB", "RB", "WR", "TE")]
    assert _week(db, 2) == ([], [])
    assert len(_week(db, 1)[1]) == 100
    with pytest.raises(wr.PublishError, match="incomplete"):
        publish(db, syn.inputs, replace_live=[(2026, 2)])
    res = publish(db, syn.inputs, allow_incomplete=True)
    assert res.skipped == []
    assert {r[0] for r in rows(db, "SELECT incomplete FROM radar_list WHERE week = 2 AND "
                                   "kind = 'live'")} == {True}  # fmt: skip


def test_cli_exit_codes(db: tg.Target, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from twm.cli import app

    monkeypatch.setattr(tg, "default_env_file", lambda: NO_ENV_FILE)
    monkeypatch.setenv(tg.LOCAL_ENV, db.url)
    syn = ps.build(tmp_path, live_weeks=(1,))
    i = syn.inputs
    args = ["publish", "--target", "local", "--db", str(i.warehouse), "--store", str(i.store),
            "--dataset", str(i.dataset), "--evaluation", str(i.evaluation_csv),
            "--now", "2026-09-28T20:00:00"]  # fmt: skip
    runner = CliRunner()
    res = runner.invoke(app, args)
    assert res.exit_code == 0, res.output
    assert "live lists published (new): 2026-W01 QB" in res.output
    assert "total size of these tables" in res.output and "pipeline run" in res.output
    assert db.url not in res.output
    ps.write_live_week(i.store, 2, incomplete=True)
    res = runner.invoke(app, args)
    assert res.exit_code == wr.EXIT_SKIPPED_INCOMPLETE, res.output
    assert "SKIPPED" in res.output
    res = runner.invoke(app, [*args, "--replace-live", "2026-W07"])
    assert res.exit_code == 1 and "no live list of 2026 week 7" in res.output


# --------------------------------------------------------------------------------------
# The Neon roles (scripts/neon/roles.sql), tried on the local server
# --------------------------------------------------------------------------------------


@pytest.fixture
def roles(server: str, db: tg.Target) -> Iterator[dict[str, str]]:
    """Passwords for twm_web / twm_job; the roles are removed again afterwards (they are
    server-wide, so an existing pair is never touched: the test is skipped then)."""
    with psycopg.connect(server, autocommit=True) as admin:
        taken = admin.execute(
            "SELECT count(*) FROM pg_roles WHERE rolname = ANY(%s)", [list(ROLE_NAMES)]
        ).fetchone()[0]
    if taken:
        pytest.skip("roles twm_web / twm_job already exist on this server; not touching them")
    passwords = {r: f"Test-{uuid.uuid4().hex}" for r in ROLE_NAMES}
    yield passwords
    # their grants live in the test database (and on it): revoke them there, then drop
    with psycopg.connect(db.url, autocommit=True) as conn:
        conn.execute("DROP OWNED BY twm_web, twm_job")
    with psycopg.connect(server, autocommit=True) as admin:
        for r in ROLE_NAMES:
            admin.execute(f'DROP ROLE IF EXISTS "{r}"')


def test_roles_sql_gives_the_site_read_only_and_the_job_rows_only(
    server: str, db: tg.Target, roles: dict[str, str], tmp_path: Path
) -> None:
    sql_text = (ROOT / "scripts" / "neon" / "roles.sql").read_text()
    with psycopg.connect(db.url, autocommit=True) as owner:
        with pytest.raises(psycopg.errors.RaiseException, match="must be set"):
            owner.execute(sql_text)
        for _ in range(2):  # safe to run again
            with owner.transaction():
                owner.execute("SELECT set_config('twm.web_password', %s, true)",
                              [roles["twm_web"]])  # fmt: skip
                owner.execute("SELECT set_config('twm.job_password', %s, true)",
                              [roles["twm_job"]])  # fmt: skip
                owner.execute(sql_text)

    def as_role(role: str) -> str:
        return make_conninfo(db.url, user=role, password=roles[role])

    # the job can run a whole publish ...
    job = tg.resolve("local", env={tg.LOCAL_ENV: as_role("twm_job")}, env_file=NO_ENV_FILE)
    syn = ps.build(tmp_path, live_weeks=(1,))
    res = publish(job, syn.inputs)
    assert res.counts["radar_list"] == 28
    # ... but cannot change a table or the run log
    with psycopg.connect(job.url, autocommit=True) as conn:
        for stmt in ("CREATE TABLE x (a int)", "DROP TABLE glossary",
                     "ALTER TABLE glossary ADD COLUMN y int", "DELETE FROM pipeline_runs",
                     "UPDATE pipeline_runs SET status = 'failed'",
                     "SELECT * FROM drizzle.__drizzle_migrations"):  # fmt: skip
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(stmt)
    # the site reads everything and writes nothing
    with psycopg.connect(as_role("twm_web"), autocommit=True) as conn:
        for name in TABLES:
            conn.execute(f'SELECT count(*) FROM "{name}"')
        for stmt in ("INSERT INTO site_meta VALUES ('x', 'y')", "DELETE FROM radar_pick",
                     "CREATE TABLE x (a int)"):  # fmt: skip
            with pytest.raises((psycopg.errors.InsufficientPrivilege,
                                psycopg.errors.ReadOnlySqlTransaction)):  # fmt: skip
                conn.execute(stmt)
    # a table added by a later migration (by the owner) is covered by the default privileges
    with psycopg.connect(db.url, autocommit=True) as owner:
        owner.execute("CREATE TABLE later_table (a int)")
    with psycopg.connect(job.url, autocommit=True) as conn:
        conn.execute("INSERT INTO later_table VALUES (1)")
    with psycopg.connect(as_role("twm_web"), autocommit=True) as conn:
        assert conn.execute("SELECT a FROM later_table").fetchall() == [(1,)]


def test_remote_publish_needs_tls_even_to_a_real_server(server: str) -> None:
    """The remote target cannot be pointed at the local server at all."""
    url = make_conninfo(server, sslmode="verify-full")
    with pytest.raises(tg.TargetError, match="refusing --target remote"):
        tg.resolve("remote", env={"DATABASE_URL": url}, env_file=NO_ENV_FILE)


def test_generated_at_is_the_clock(db: tg.Target, tmp_path: Path) -> None:
    syn = ps.build(tmp_path, now=datetime.fromisoformat("2026-10-06T15:00:00+00:00"))
    publish(db, syn.inputs)
    meta = dict(rows(db, "SELECT key, value FROM site_meta"))
    assert meta["generated_at"].startswith("2026-10-06T15:00") and meta["current_week"] == "3"
    assert pl.DataFrame(rows(db, "SELECT kind FROM radar_list"), orient="row").height == 28
