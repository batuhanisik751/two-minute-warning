"""CLI smoke tests for `twm build` (fixtures only) and `twm doctor`."""

from __future__ import annotations

import os

import duckdb
import pytest
from typer.testing import CliRunner

from tests.conftest import SEASON_2025_COUNTS, game, season_2025_games
from twm.cli import app
from twm.config import Settings
from twm.warehouse import build as wb
from twm.warehouse import schema as sc

runner = CliRunner()


def _point_warehouse_at(monkeypatch, db_path):
    """Make settings().path("warehouse") return the tmp file; other keys keep their real paths."""
    orig = Settings.path
    monkeypatch.setattr(
        Settings, "path", lambda self, key: db_path if key == "warehouse" else orig(self, key)
    )


def test_build_cli_prints_manifest(raw, db_path):
    raw.write_season(2025, season_2025_games())
    raw.write_season(2026, [game(2026, 1, "2026-09-13", "13:00", "LV", "KC")])
    result = runner.invoke(app, ["build", "2025", "2026", "--db", str(db_path)])
    assert result.exit_code == 0, result.output
    assert f"built {len(sc.tables())} tables for seasons [2025, 2026]" in result.output
    assert "fact_game" in result.output and "dim_week" in result.output
    # the option spellings work too (`--seasons 2025 2026` = option 2025 + positional 2026)
    result = runner.invoke(app, ["build", "-s", "2025", "-s", "2026", "--db", str(db_path)])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["build", "--seasons", "2025", "2026", "--db", str(db_path)])
    assert result.exit_code == 0, result.output
    assert "seasons [2025, 2026]" in result.output
    assert "[SEASON]..." in runner.invoke(app, ["build", "--help"]).output


def test_build_cli_start_end_and_missing_cache(raw, db_path):
    raw.write_season(2025, season_2025_games())
    result = runner.invoke(app, ["build", "--start", "2025", "--end", "2025", "--db", str(db_path)])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["build", "2024", "--db", str(db_path)])
    assert result.exit_code == 3, result.output  # cache missing: run `twm ingest`
    assert "nothing built" in result.output and "schedules/2024.parquet" in result.output
    result = runner.invoke(app, ["build", "--db", str(db_path)])
    assert result.exit_code == 2, result.output  # bad arguments
    assert "give seasons" in result.output


def test_build_cli_reports_a_failed_build_in_one_line(raw, db_path):
    raw.write_season(2025, season_2025_games())
    runner.invoke(app, ["build", "2025", "--db", str(db_path)])
    # W2 kicks off Tuesday 09:30 ET, before W1's Tuesday 14:00 UTC as-of
    raw.write_season(2025, [game(2025, 1, "2025-09-04", "20:20", "DAL", "PHI"),
                            game(2025, 2, "2025-09-09", "09:30", "KC", "LAC")])  # fmt: skip
    result = runner.invoke(app, ["build", "2025", "--db", str(db_path)])
    assert result.exit_code == 1, result.output
    assert "rolled back" in result.output and "point-in-time leak" in result.output
    assert "Traceback" not in result.output
    assert dict(wb.table_counts(db_path))["fact_game"] == SEASON_2025_COUNTS["fact_game"]
    result = runner.invoke(app, ["build", "1990", "--db", str(db_path)])
    assert result.exit_code == 1 and "first season with a schedule" in result.output


def test_build_cli_reports_an_unwritable_target_directory_in_one_line(raw, tmp_path):
    raw.write_season(2025, season_2025_games())
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)  # the lock file (the first write) cannot be created here
    try:
        if os.access(ro, os.W_OK):
            pytest.skip("directory permissions are not enforced here (running as root?)")
        result = runner.invoke(app, ["build", "2025", "--db", str(ro / "w.duckdb")])
    finally:
        ro.chmod(0o700)
    assert result.exit_code == 1, result.output
    assert "build failed" in result.output and "w.duckdb.build.lock" in result.output
    assert "Traceback" not in result.output
    assert not (ro / "w.duckdb").exists()


def test_build_cli_rejects_mixing_seasons_with_start_end(raw, db_path):
    raw.write_season(2025, season_2025_games())
    for args in (["--seasons", "2025", "--start", "2025"], ["2025", "--end", "2025"]):
        result = runner.invoke(app, ["build", *args, "--db", str(db_path)])
        assert result.exit_code == 2, result.output
        assert "exclusive" in result.output  # rich wraps the panel, so one word is checked
    assert not db_path.exists()


def test_doctor_lists_warehouse_tables(raw, db_path, monkeypatch):
    raw.write_season(2025, season_2025_games())
    wb.build_warehouse([2025], db_path=db_path)
    _point_warehouse_at(monkeypatch, db_path)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    lines = [ln.split() for ln in result.output.splitlines() if ln.startswith("  ")]
    counts = {name: int(n.replace(",", "")) for name, n, _rows in lines}
    assert counts["fact_game"] == 14 and counts["dim_week"] == 8
    assert "not built" not in result.output and result.output.rstrip().endswith("ok")


def test_doctor_survives_a_read_write_handle_on_the_warehouse(raw, db_path, monkeypatch):
    raw.write_season(2025, season_2025_games())
    wb.build_warehouse([2025], db_path=db_path)
    _point_warehouse_at(monkeypatch, db_path)
    rw = duckdb.connect(str(db_path))  # the notebook pattern doctor must not crash on
    try:
        result = runner.invoke(app, ["doctor"])
    finally:
        rw.close()
    assert result.exit_code == 0, result.output
    assert "locked by another process" in result.output
    assert result.output.rstrip().endswith("ok")


def test_doctor_reports_not_built(db_path, monkeypatch):
    _point_warehouse_at(monkeypatch, db_path)  # db_path does not exist yet
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "not built" in result.output and "run `twm build 2025 2026`" in result.output
    assert result.output.rstrip().endswith("ok")
