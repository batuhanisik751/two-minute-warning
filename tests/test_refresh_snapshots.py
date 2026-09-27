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
