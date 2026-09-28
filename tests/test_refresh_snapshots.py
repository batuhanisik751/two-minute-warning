"""scripts/refresh_snapshots.py picks the right season per snapshot and warns on fallbacks."""

import importlib.util
from pathlib import Path

import pytest

from twm.sources import nflverse as nv

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "refresh_snapshots.py"
CURRENT = nv.settings().current_season


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("refresh_snapshots", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_targets_use_current_season_and_warn_only_on_fallback(script, monkeypatch):
    cached = {
        "pbp": [2010, CURRENT],  # current season cached -> use it, no warning
        "participation": [2016, CURRENT - 1],  # published after the season -> fallback
        "snap_counts": [],  # nothing cached -> current (refresh_snapshot then says "not cached")
    }
    monkeypatch.setattr(script.nv, "cached_seasons", lambda name: cached[name])
    names = ["pbp", "participation", "snap_counts", "teams", nv.LEGACY_SNAPSHOT]
    out = {t.snapshot_name: t for t in script.targets(names)}

    assert out["pbp"][:3] == ("pbp", CURRENT, "pbp") and out["pbp"].warning is None
    assert out["snap_counts"].season == CURRENT and out["snap_counts"].warning is None
    assert out["teams"][:3] == ("teams", None, "teams") and out["teams"].warning is None
    legacy = out[nv.LEGACY_SNAPSHOT]
    assert legacy[:3] == ("depth_charts", nv.DEPTH_CHARTS_LEGACY_LAST_SEASON, nv.LEGACY_SNAPSHOT)
    assert legacy.warning is None

    fallback = out["participation"]
    assert fallback.season == CURRENT - 1
    assert f"season {CURRENT} is not cached, snapshotting {CURRENT - 1}" in fallback.warning
    assert f"twm ingest participation --start {CURRENT}" in fallback.warning


def test_main_prints_the_fallback_warning_to_stderr(script, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(script.nv, "cached_seasons", lambda name: [CURRENT - 1])
    written = []
    monkeypatch.setattr(
        script.nv, "refresh_snapshot", lambda *a, **k: written.append((a, k)) or tmp_path / "x.json"
    )
    monkeypatch.setattr(script.nv, "load_snapshot", lambda name: {})
    assert script.main(["injuries"]) == 0
    assert written[0][0] == ("injuries", CURRENT - 1)
    err = capsys.readouterr().err
    assert err.startswith("WARNING injuries: season") and "not cached" in err


# ---- `twm snapshot` (the CLI wrapper) ------------------------------------------------------


def _cli_env(raw, tmp_path, monkeypatch):
    import polars as pl

    from tests.conftest import season_2025_games

    monkeypatch.setattr(nv, "schema_dir", lambda: tmp_path / "schemas")
    raw.write_season(2025, season_2025_games())
    return pl


def test_cli_snapshot_reads_the_cache_and_never_downloads(raw, tmp_path, monkeypatch):
    import json

    from typer.testing import CliRunner

    from twm.cli import app

    _cli_env(raw, tmp_path, monkeypatch)  # raw's loader raises on any download
    out = CliRunner().invoke(app, ["snapshot", "schedules", "--season", "2025"])
    assert out.exit_code == 0, out.output
    assert "from the cache" in out.output and "season 2025" in out.output
    snap = json.loads((tmp_path / "schemas" / "schedules.json").read_text())
    assert snap["sample_season"] == 2025 and "game_id" in snap["columns"]
    teams = CliRunner().invoke(app, ["snapshot", "teams"])
    assert teams.exit_code == 0 and (tmp_path / "schemas" / "teams.json").exists()
    missing = CliRunner().invoke(app, ["snapshot", "schedules", "--season", "2024"])
    assert missing.exit_code == 1 and "not cached" in missing.output


def test_cli_snapshot_downloads_only_when_asked(raw, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from twm.cli import app

    pl = _cli_env(raw, tmp_path, monkeypatch)
    calls = []

    def loader(ds):
        def fetch(season=None, **kw):
            calls.append((ds.name, season))
            return pl.DataFrame({"game_id": ["x"], "season": [season]})

        return fetch

    monkeypatch.setattr(nv, "_loader", loader)
    out = CliRunner().invoke(app, ["snapshot", "schedules", "--season", "2025", "--download"])
    assert out.exit_code == 0, out.output
    assert calls == [("schedules", 2025)] and "downloaded" in out.output


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["snapshot", "nope"], "unknown dataset"),
        (["snapshot", "teams", "--season", "2025"], "no seasons"),
        (["snapshot", "depth_charts_legacy", "--season", "2025"], "up to 2024"),
        (["snapshot", "snap_counts", "--season", "2010"], "starts in 2013"),
    ],
)
def test_cli_snapshot_refuses_bad_arguments(args, message):
    from typer.testing import CliRunner

    from twm.cli import app

    out = CliRunner().invoke(app, args)
    assert out.exit_code == 2 and message in out.output
