"""`twm doctor` (PROJECT_SPEC 9): every check on a synthetic setup, one PASS and each WARN or
FAIL it can give, the exit code, and that .env values never reach the output."""

from __future__ import annotations

import json
import os
import shutil
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import polars as pl
import pytest
from typer.testing import CliRunner

from tests.conftest import SCHEDULE_DTYPES, frame, game
from twm import doctor as dr
from twm import predictions as pr
from twm.cli import app
from twm.config import CONFIG_DIR, CONFIG_DIR_ENV, reload
from twm.sources import nflverse as nv

NOW = datetime(2026, 9, 29, 20, 0, tzinfo=UTC)  # the Tuesday after week 3 of the fake 2026
SECRET = "not-a-real-secret-value-123"


def _ctx(tmp: Path, **change) -> dr.Context:
    root = tmp / "proj"
    (root / "data").mkdir(parents=True, exist_ok=True)
    cfg = root / "config"
    if not cfg.exists():
        shutil.copytree(CONFIG_DIR, cfg)
    (root / ".gitignore").write_text(".env\n")
    base = dict(
        root=root, config_dir=cfg, env_file=root / ".env", env_example=root / ".env.example",
        gitignore=root / ".gitignore", site_packages=None, links={}, cloud_synced=False,
        raw_dir=root / "data" / "raw", schema_dir=root / "data" / "schemas",
        warehouse=root / "data" / "warehouse.duckdb", store=root / "data" / "predictions.duckdb",
        models_dir=root / "models" / "waiver_radar",
        dataset=root / "data" / "waiver_radar" / "dataset.parquet", current_season=2026, now=NOW,
    )  # fmt: skip
    base.update(change)
    return dr.Context(**base)


def _text(check: dr.Check) -> str:
    return "\n".join(check.lines())


# --------------------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------------------


def test_config_passes_and_prints_the_derived_league_shape(tmp_path):
    c = dr.check_config(_ctx(tmp_path))
    assert c.status == "PASS"
    text = _text(c)
    assert "QB top 12, RB top 24, WR top 24, TE top 12" in text
    assert "RB/WR top 36" in text and "QB 18, RB 36, WR 36, TE 18" in text


def test_config_fails_with_the_validation_message(tmp_path):
    ctx = _ctx(tmp_path)
    league = (ctx.config_dir / "league.yaml").read_text()
    (ctx.config_dir / "league.yaml").write_text(league.replace("teams: 12", "teams: 1"))
    (ctx.config_dir / "scoring.yaml").unlink()
    c = dr.check_config(ctx)
    assert c.status == "FAIL" and "2 of 3 files" in c.title
    text = _text(c)
    assert "scoring.yaml: not found" in text and "between 2 and 32 teams" in text


# --------------------------------------------------------------------------------------
# .env
# --------------------------------------------------------------------------------------


def test_env_missing_is_a_warning(tmp_path):
    c = dr.check_env(_ctx(tmp_path))
    assert c.status == "WARN" and "not found" in c.title


def test_env_reports_key_names_never_values(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.env_file.write_text(
        f"DATABASE_URL=postgres://u:{SECRET}@host/db?sslmode=require&x=1\n"
        "ENABLE_MY_LEAGUE=false\nESPN_YEAR=2026\nESPN_S2=\nMY_OWN_KEY=abc\n"
    )
    c = dr.check_env(ctx)
    text = _text(c)
    assert c.status == "PASS" and SECRET not in text and "abc" not in text
    assert "set: DATABASE_URL, ENABLE_MY_LEAGUE, ESPN_YEAR" in text
    assert "empty or absent: ESPN_LEAGUE_ID, ESPN_S2, ESPN_SWID" in text
    assert "other keys: MY_OWN_KEY" in text and "My League: off" in text


def test_env_my_league_on_without_its_keys_warns(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.env_file.write_text(f"ENABLE_MY_LEAGUE=true\nESPN_LEAGUE_ID={SECRET}\n")
    c = dr.check_env(ctx)
    text = _text(c)
    assert c.status == "WARN" and SECRET not in text
    assert "needs ESPN_YEAR" in text and "ESPN_S2, ESPN_SWID" in text
    ctx.env_file.write_text(
        "ENABLE_MY_LEAGUE=1\nESPN_LEAGUE_ID=1\nESPN_YEAR=2026\nESPN_S2=x\nESPN_SWID=y\n"
    )
    assert dr.check_env(ctx).status == "PASS"


def test_env_unparsable_lines_warn_with_line_numbers_only(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.env_file.write_text(f"ESPN_YEAR=2026\nthis is {SECRET}\n")
    c = dr.check_env(ctx)
    assert c.status == "WARN" and SECRET not in _text(c) and "do not parse" in _text(c)
    assert ": 2" in _text(c)


def test_env_not_ignored_by_git_fails(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.env_file.write_text("ESPN_YEAR=2026\n")
    ctx.gitignore.write_text("data/\n")
    c = dr.check_env(ctx)
    assert c.status == "FAIL" and ".gitignore" in c.title


# --------------------------------------------------------------------------------------
# Virtualenv and local storage
# --------------------------------------------------------------------------------------


def test_hidden_pth_files_fail(tmp_path):
    sp = tmp_path / "site-packages"
    sp.mkdir()
    (sp / "twm.pth").write_text("/src\n")
    ok = dr.check_hidden_pth(_ctx(tmp_path, site_packages=sp))
    assert ok.status == "PASS"
    assert dr.check_hidden_pth(_ctx(tmp_path, site_packages=tmp_path / "none")).status == "WARN"
    if not hasattr(os, "chflags") or not hasattr(stat, "UF_HIDDEN"):
        pytest.skip("hidden file flags exist on macOS/BSD only")
    os.chflags(sp / "twm.pth", stat.UF_HIDDEN)
    c = dr.check_hidden_pth(_ctx(tmp_path, site_packages=sp))
    assert c.status == "FAIL" and "chflags -R nohidden .venv" in c.title
    assert "twm.pth" in _text(c)


def test_links_dangling_fails_local_in_a_synced_folder_warns(tmp_path):
    store = tmp_path / "store"
    (store / "raw").mkdir(parents=True)
    root = tmp_path / "proj" / "data"
    root.mkdir(parents=True)
    (root / "raw").symlink_to(store / "raw")
    (root / "gone").symlink_to(store / "missing")
    (tmp_path / "proj" / "models").mkdir()
    links = {"data/raw": root / "raw", "models": tmp_path / "proj" / "models",
             "data/predictions.duckdb": root / "predictions.duckdb"}  # fmt: skip
    assert dr.check_links(_ctx(tmp_path, links=links)).status == "PASS"
    warn = dr.check_links(_ctx(tmp_path, links=links, cloud_synced=True))
    assert warn.status == "WARN" and "in the project folder: models" in _text(warn)
    bad = dr.check_links(_ctx(tmp_path, links={**links, "data/raw": root / "gone"}))
    assert bad.status == "FAIL" and "missing" in _text(bad)


# --------------------------------------------------------------------------------------
# nflreadpy and the raw cache
# --------------------------------------------------------------------------------------


def _snapshot(ctx: dr.Context, name: str, version: str) -> None:
    ctx.schema_dir.mkdir(parents=True, exist_ok=True)
    (ctx.schema_dir / f"{name}.json").write_text(json.dumps({"nflreadpy_version": version}))


def test_nflreadpy_version_against_the_snapshots(tmp_path):
    from importlib.metadata import version

    ctx = _ctx(tmp_path)
    assert dr.check_nflreadpy(ctx).status == "WARN"  # no snapshots
    _snapshot(ctx, "pbp", version("nflreadpy"))
    assert dr.check_nflreadpy(ctx).status == "PASS"
    _snapshot(ctx, "schedules", "0.0.1")
    c = dr.check_nflreadpy(ctx)
    assert c.status == "WARN" and "0.0.1" in c.title


def _small_registry(monkeypatch) -> None:
    """Two per-season datasets and one one-file dataset."""
    reg = {
        "schedules": nv.Dataset("schedules", "load_schedules", first_season=2024),
        "snap_counts": nv.Dataset("snap_counts", "load_snap_counts", first_season=2025),
        "players": nv.Dataset("players", "load_players", per_season=False),
    }
    monkeypatch.setattr(nv, "DATASETS", reg)


def _write(ctx: dr.Context, name: str, season: int | None, df: pl.DataFrame,
           mtime: datetime | None = None) -> Path:  # fmt: skip
    path = ctx.raw_dir / name / (f"{season}.parquet" if season else "all.parquet")
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path)
    if mtime is not None:
        os.utime(path, (mtime.timestamp(), mtime.timestamp()))
    return path


def test_cache_coverage_lists_missing_seasons(tmp_path, monkeypatch):
    _small_registry(monkeypatch)
    ctx = _ctx(tmp_path)
    assert dr.check_cache_coverage(ctx).status == "WARN"  # empty
    one = pl.DataFrame({"x": [1]})
    for s in (2024, 2026):
        _write(ctx, "schedules", s, one)
    _write(ctx, "snap_counts", 2026, pl.DataFrame({"x": []}, schema={"x": pl.Int64}))  # 0 rows
    c = dr.check_cache_coverage(ctx)
    text = _text(c)
    assert c.status == "WARN" and "schedules: 2025" in text
    assert "snap_counts: 2025-2026" in text and "players (one file)" in text
    for s in (2025,):
        _write(ctx, "schedules", s, one)
    for s in (2025, 2026):
        _write(ctx, "snap_counts", s, one)
    _write(ctx, "players", None, one)
    assert dr.check_cache_coverage(ctx).status == "PASS"


def _schedule_2026(results_through: int) -> pl.DataFrame:
    """Weeks 1-4 of a fake 2026 (Thursday + Sunday games; week 3's last game is Sunday
    2026-09-27 16:25 ET); results through ``results_through``."""
    days = {1: "2026-09-13", 2: "2026-09-20", 3: "2026-09-27", 4: "2026-10-04"}
    rows = []
    for w, day in days.items():
        r = 3 if w <= results_through else None
        rows.append(game(2026, w, day, "13:00", "KC", "PHI", result=r))
        rows.append(game(2026, w, day, "16:25", "DAL", "SEA", result=r))
    return frame(rows, SCHEDULE_DTYPES)


def test_freshness_flags_missing_results_and_stale_files(tmp_path, monkeypatch):
    _small_registry(monkeypatch)
    ctx = _ctx(tmp_path)
    assert dr.check_freshness(ctx).status == "WARN"  # no 2026 schedule
    fresh = NOW - timedelta(hours=10)
    _write(ctx, "schedules", 2026, _schedule_2026(3), mtime=fresh)
    _write(ctx, "snap_counts", 2026, pl.DataFrame({"x": [1]}), mtime=fresh)
    ok = dr.check_freshness(ctx)
    assert ok.status == "PASS" and "results through week 3" in _text(ok)
    _write(ctx, "schedules", 2026, _schedule_2026(2), mtime=fresh)
    behind = dr.check_freshness(ctx)
    assert behind.status == "WARN" and "week 3's results are missing" in _text(behind)
    assert "twm ingest --start 2026" in behind.title
    _write(ctx, "schedules", 2026, _schedule_2026(3), mtime=fresh)
    _write(ctx, "snap_counts", 2026, pl.DataFrame({"x": [1]}), mtime=NOW - timedelta(days=9))
    stale = dr.check_freshness(ctx)
    assert stale.status == "WARN" and "older than 7 days: snap_counts" in _text(stale)
    # before the season's first game, file ages do not matter
    early = _ctx(tmp_path, now=datetime(2026, 6, 1, tzinfo=UTC))
    assert dr.check_freshness(early).status == "PASS"


# --------------------------------------------------------------------------------------
# Warehouse, ids, store
# --------------------------------------------------------------------------------------


def test_warehouse_not_built_stale_and_fresh(raw, tmp_path):
    from tests.conftest import season_2025_games
    from twm.warehouse import build as wb

    ctx = _ctx(tmp_path, raw_dir=nv.raw_dir())
    c, db = dr.check_warehouse(ctx)
    assert c.status == "WARN" and "not built" in c.title and db is None
    raw.write_season(2025, season_2025_games())
    wb.build_warehouse([2025], db_path=ctx.warehouse)
    c, db = dr.check_warehouse(ctx)
    assert c.status == "PASS" and "seasons 2025" in c.title and db == ctx.warehouse
    assert any("fact_game" in d and d.endswith("rows") for d in c.details)
    later = datetime.now(UTC) + timedelta(hours=1)
    path = nv.cache_path("pbp", 2025)
    os.utime(path, (later.timestamp(), later.timestamp()))
    c, _ = dr.check_warehouse(ctx)
    assert c.status == "WARN" and "changed after it" in c.title
    assert any("pbp/2025.parquet" in d for d in c.details)


def test_ids_coverage_warns_when_low(tmp_path):
    db = tmp_path / "wh.duckdb"
    con = duckdb.connect(str(db))
    con.execute(
        "CREATE TABLE report_id_coverage AS SELECT 'fact_snaps' AS dataset, 'fantasy' AS scope, "
        "2024 AS season, 5 AS n_rows_unmatched, 100 AS n_rows, 0.95 AS match_rate"
    )
    con.execute("CREATE TABLE bridge_player_id AS SELECT false AS is_conflict")
    con.execute("CREATE TABLE report_id_unmatched AS SELECT 'ambiguous' AS kind")
    con.close()
    c = dr.check_ids(_ctx(tmp_path), db)
    assert c is not None and c.status == "WARN" and "ids: snaps" in _text(c)


def _version_row(season: int, version: str) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "model_version": [version], "module": ["waiver_radar"], "model": ["logit"],
            "label": ["y_hit"], "feature_list": ["[]"], "params": ["{}"],
            "training_seasons": [json.dumps([season - 2, season - 1])],
            "test_season": [season], "dataset_hash": ["x"], "code_version": ["t"],
            "notes": ["{}"], "created_at": [datetime(2026, 9, 1)],
        },
        schema_overrides={"test_season": pl.Int32},
    )  # fmt: skip


def test_store_absent_missing_model_file_and_ok(tmp_path):
    ctx = _ctx(tmp_path)
    assert dr.check_store(ctx).status == "WARN"  # no store
    pr.register_version(ctx.store, _version_row(2025, "logit-old"))
    c = dr.check_store(ctx)
    assert c.status == "WARN" and "no production model for 2026" in c.title
    pr.register_version(ctx.store, _version_row(2026, "logit-abc"))
    c = dr.check_store(ctx)
    assert c.status == "WARN" and "file is missing" in c.title
    ctx.models_dir.mkdir(parents=True)
    (ctx.models_dir / "logit-abc.joblib").write_bytes(b"x")
    c = dr.check_store(ctx)
    assert c.status == "PASS" and "2024-2025" in _text(c)


def test_unreadable_store_fails(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.store.write_bytes(b"this is not a duckdb file" * 100)
    assert dr.check_store(ctx).status == "FAIL"


# --------------------------------------------------------------------------------------
# The command
# --------------------------------------------------------------------------------------


def test_cli_exits_1_on_a_failure_and_names_it(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    shutil.copytree(CONFIG_DIR, cfg)
    league = (cfg / "league.yaml").read_text()
    (cfg / "league.yaml").write_text(league.replace("  TE: 1\n", "  TE: 0\n", 1))
    monkeypatch.setenv(CONFIG_DIR_ENV, str(cfg))
    reload()
    try:
        result = CliRunner().invoke(app, ["doctor"])
    finally:
        monkeypatch.delenv(CONFIG_DIR_ENV)
        reload()
    assert result.exit_code == 1, result.output
    assert "FAIL  Config" in result.output and "TE has no starting slot" in result.output
    assert result.output.rstrip().splitlines()[-1].startswith("FAILED: ")


def test_cli_summary_line(monkeypatch):
    monkeypatch.setattr(
        dr, "run_checks", lambda: [dr.Check("PASS", "a"), dr.Check("WARN", "b", ["detail"])]
    )
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert result.output.splitlines() == [
        "PASS  a", "WARN  b", "      detail", "ok: 1 passed, 1 warning(s), 0 failed",
    ]  # fmt: skip
