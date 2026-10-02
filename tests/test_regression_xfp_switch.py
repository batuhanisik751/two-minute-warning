"""Regression Watch's switch to the own walk-forward xFP (step H6-b2): synthetic data only.

- the pin's ``xfp`` entry is required and checked (source, every model file's sha256 BEFORE it
  is opened, the fold), with no fallback to ffopportunity;
- the weekly list (and so the scheduled job) loads the pinned live models and NEVER fits
  (every fitting function is replaced by one that raises);
- the own and ffopportunity sources are one switch: the frame's xFP changes with it, the
  ffopportunity path stays available for research.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.test_regression_own_xfp import synthetic_plays
from tests.test_regression_weekly import SEASON, build_world, toy_csv, toy_params
from twm import pins
from twm.config import ROOT
from twm.modules.regression_watch import own_xfp as ox
from twm.modules.regression_watch import production as rp
from twm.modules.regression_watch import xfp_source as xs

FITS = ("fit_fold", "fit_component", "fit_lgbm", "fit_glm", "fit_rate", "fit_live", "run_folds",
        "predict_fold", "history_player_games")  # fmt: skip


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> Path:
    return build_world(tmp_path_factory.mktemp("xfp_switch"))


@pytest.fixture(scope="module")
def live() -> ox.LiveModels:
    """Live models for SEASON fit on synthetic plays of 2006-2009 (the fold's own code)."""
    plays = synthetic_plays()
    models, _ = ox.fit_fold(plays, 2010, [2006, 2007, 2008, 2009])
    return ox.LiveModels(SEASON, (2006, 2007, 2008, 2009), f"own_xfp-{SEASON}-test", models)


@pytest.fixture()
def approved(tmp_path, live):
    """(root, pin file, own parameters): approved into a temporary root and pin file."""
    params = toy_params(xfp_source="own")
    csv_path = toy_csv(tmp_path / "backtest.csv", params)
    pin_file = tmp_path / "production_models.yaml"
    shutil.copy(ROOT / "config" / "production_models.yaml", pin_file)
    rp.approve(params, csv_path=csv_path, root=tmp_path, path=pin_file, live=live,
               today=datetime(2025, 9, 1, tzinfo=UTC))  # fmt: skip
    return tmp_path, pin_file, params


def _no_fitting(monkeypatch) -> None:
    def boom(*a, **k):
        raise AssertionError("the weekly path must never fit")

    for name in FITS:
        monkeypatch.setattr(ox, name, boom)


# --------------------------------------------------------------------------------------
# The pin: required, checked, no fallback
# --------------------------------------------------------------------------------------


def test_approve_pins_every_live_model_with_its_sha256(approved, live):
    root, pin_file, params = approved
    pin = pins.read_pins(pin_file)["regression_watch"]
    assert pin.xfp.source == "own" and pin.xfp.fold == SEASON
    assert pin.xfp.version == live.version and pin.xfp.train_seasons == "2006-2009"
    assert set(pin.xfp.models) == set(ox.COMPONENT_BY_NAME)
    for m in pin.xfp.models.values():
        assert m.file.startswith(f"artifacts/production_models/regression_watch/{live.version}/")
        assert pins.sha256_of(m.path(root)) == m.sha256
    text = pin_file.read_text()
    assert "xfp:\n    source: own\n    fold: 2025" in text
    again = pins.write_pins(pins.read_pins(pin_file), root / "again.yaml")
    assert pins.read_pins(again) == pins.read_pins(pin_file)
    got, params_back, _ = rp.load_pinned_xfp(SEASON, path=pin_file, root=root)
    assert params_back.model_version == params.model_version and params_back.xfp_source == "own"
    assert got.version == live.version and set(got.models) == set(live.models)


def test_own_parameters_cannot_be_approved_without_the_seasons_live_models(tmp_path, live):
    params = toy_params(xfp_source="own")
    csv_path = toy_csv(tmp_path / "backtest.csv", params)
    with pytest.raises(rp.RegressionProductionError, match="need the live models"):
        rp.approve(params, csv_path=csv_path, root=tmp_path, path=tmp_path / "p.yaml")
    other = ox.LiveModels(SEASON + 1, live.train_seasons, live.version, live.models)
    with pytest.raises(rp.RegressionProductionError, match="need the live models"):
        rp.approve(params, csv_path=csv_path, root=tmp_path, path=tmp_path / "p.yaml",
                   live=other)  # fmt: skip
    assert not (tmp_path / "p.yaml").exists()


def _repin(pin_file: Path, **changes) -> None:
    from dataclasses import replace

    current = pins.read_pins(pin_file)
    current["regression_watch"] = replace(current["regression_watch"], **changes)
    pins.write_pins(current, pin_file)


def test_a_pin_without_an_xfp_source_is_refused(approved):
    root, pin_file, _ = approved
    _repin(pin_file, xfp=None)
    assert "xfp:" not in pin_file.read_text().split("regression_watch:")[1].split("\nstreamer")[0]
    with pytest.raises(pins.PinError, match="names no xFP source"):
        rp.load_pinned_params(SEASON, path=pin_file, root=root)
    with pytest.raises(pins.PinError, match="names no xFP source"):
        xs.pinned_source(pin_file)


def test_a_changed_or_missing_model_file_is_refused_before_it_is_opened(approved, monkeypatch):
    root, pin_file, _ = approved
    pin = pins.read_pins(pin_file)["regression_watch"]
    file = pin.xfp.models["completion"].path(root)
    file.write_bytes(file.read_bytes() + b"\0")
    import joblib

    monkeypatch.setattr(joblib, "load", lambda *a, **k: pytest.fail("a pickle was opened"))
    with pytest.raises(pins.PinError, match="not the approved completion model"):
        rp.load_pinned_params(SEASON, path=pin_file, root=root)
    with pytest.raises(pins.PinError, match="not the approved completion model"):
        rp.load_pinned_xfp(SEASON, path=pin_file, root=root)
    file.unlink()
    with pytest.raises(pins.PinError, match="model is missing"):
        rp.load_pinned_xfp(SEASON, path=pin_file, root=root)


def test_the_pins_source_must_be_the_parameters_source(approved):
    root, pin_file, _ = approved
    _repin(pin_file, xfp=pins.XfpPin("ffopportunity"))
    with pytest.raises(pins.PinError, match="made with 'own'"):
        rp.load_pinned_params(SEASON, path=pin_file, root=root)


def test_the_pinned_fold_must_be_the_season_with_every_component(approved):
    from dataclasses import replace

    root, pin_file, _ = approved
    xp = pins.read_pins(pin_file)["regression_watch"].xfp
    _repin(pin_file, xfp=replace(xp, fold=SEASON - 1))
    with pytest.raises(pins.PinError, match=f"the {SEASON} fold"):
        rp.load_pinned_params(SEASON, path=pin_file, root=root)
    fewer = {k: v for k, v in xp.models.items() if k != "rush_2pt"}
    _repin(pin_file, xfp=replace(xp, models=fewer))
    with pytest.raises(pins.PinError, match="rush_2pt"):
        rp.load_pinned_params(SEASON, path=pin_file, root=root)


@pytest.mark.parametrize("entry", [
    {"source": "nflverse"}, {"source": "own"}, {"source": "own", "fold": 2026, "version": "v"},
    {"source": "ffopportunity", "fold": 2026}, {"source": "own", "fold": 2026, "version": "v",
                                                "models": {"a": {"file": "x"}}},
    {"source": "own", "colour": 1}, "own",
])  # fmt: skip
def test_malformed_xfp_entries_are_refused(tmp_path, entry):
    import yaml

    raw = yaml.safe_load((ROOT / "config" / "production_models.yaml").read_text())
    raw["regression_watch"]["xfp"] = entry
    path = tmp_path / "p.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(pins.PinError, match="xfp"):
        pins.read_pins(path)


# --------------------------------------------------------------------------------------
# The weekly list (the scheduled job's path): pinned models, never a fit
# --------------------------------------------------------------------------------------


def test_the_weekly_list_scores_with_the_pinned_models_and_never_fits(world, approved,
                                                                     monkeypatch):  # fmt: skip
    from tests.test_regression_weekly import IN_WINDOW
    from twm.config import league
    from twm.modules.regression_watch import weekly as rwk

    root, pin_file, _ = approved
    live, params, _ = rp.load_pinned_xfp(SEASON, path=pin_file, root=root)
    _no_fitting(monkeypatch)
    own = rwk.run_week(world, SEASON, 4, params=params, league=league(), now=IN_WINDOW,
                       live=live)  # fmt: skip
    ffo = rwk.run_week(world, SEASON, 4, params=toy_params(), league=league(), now=IN_WINDOW)
    a, b = own.table.sort("gsis_id"), ffo.table.sort("gsis_id")
    assert a.height == b.height > 0 and a["gsis_id"].equals(b["gsis_id"])
    assert a["ppg"].equals(b["ppg"])  # the points are the same; only the expectations moved
    assert not a["xfp_pg"].equals(b["xfp_pg"])
    assert a["fpoe_pg"].to_list() == pytest.approx((a["ppg"] - a["xfp_pg"]).to_list(), abs=1e-6)
    preds, versions = rwk.store_frames(own)
    assert set(preds["model_version"]) == {params.model_version}
    assert '"xfp_source": "own"' in versions["params"][0]


def test_the_source_switch_needs_the_matching_models(world, live):
    from tests.test_regression_weekly import IN_WINDOW
    from twm.config import league
    from twm.modules.regression_watch import weekly as rwk

    kw = {"league": league(), "now": IN_WINDOW}
    with pytest.raises(ValueError, match="pinned live models are needed"):
        rwk.run_week(world, SEASON, 4, params=toy_params(xfp_source="own"), **kw)
    with pytest.raises(ValueError, match="do not apply"):
        rwk.run_week(world, SEASON, 4, params=toy_params(), live=live, **kw)
    wrong = ox.LiveModels(SEASON + 1, live.train_seasons, live.version, live.models)
    with pytest.raises(ox.LiveModelError, match=f"score {SEASON + 1}, not {SEASON}"):
        rwk.run_week(world, SEASON, 4, params=toy_params(xfp_source="own"), live=wrong, **kw)


def test_the_weekly_command_and_the_job_never_reach_a_fit() -> None:
    """The score command, the weekly list and the runner name no fitting function: they load
    the pinned files (the runner's preflight loads them before any download)."""
    import inspect

    from twm import cli
    from twm.modules.regression_watch import weekly as rwk

    runner = (ROOT / "src" / "twm" / "pipeline" / "runner.py").read_text()
    pre = runner[runner.index("def check_model(") : runner.index("return Hooks(")]
    assert "load_pinned_xfp(season)" in pre
    for code in (inspect.getsource(cli.regression_score), inspect.getsource(rwk.run_week),
                 inspect.getsource(cli._regression_approved), runner):  # fmt: skip
        for name in FITS:
            assert f"{name}(" not in code, name
    assert "load_pinned_xfp" in inspect.getsource(cli._regression_approved)


# --------------------------------------------------------------------------------------
# One switch: own or ffopportunity (research keeps the ffopportunity path)
# --------------------------------------------------------------------------------------


def test_history_follows_the_source_and_never_defaults(monkeypatch):
    seen = []
    fake = lambda db, last, progress=None: seen.append(last) or "own frame"  # noqa: E731
    monkeypatch.setattr(ox, "history_player_games", fake)
    assert xs.history("w.duckdb", 2025, "ffopportunity") is None  # the frame as built
    assert xs.history("w.duckdb", 2025, "own") == "own frame" and seen == [2025]
    dirs = []
    other = lambda db, last, out_dir=None, progress=None: dirs.append(out_dir) or "copy"  # noqa: E731
    monkeypatch.setattr(ox, "history_player_games", other)
    assert xs.history("w.duckdb", 2025, "own", out_dir=Path("x")) == "copy"  # I3a's copy
    assert dirs == [Path("x")]
    with pytest.raises(ValueError, match="must be one of"):
        xs.history("w.duckdb", 2025, "nflverse")
    with pytest.raises(ValueError, match="must be one of"):
        toy_params(xfp_source="")


def test_the_source_is_part_of_the_parameters_version():
    own, ffo = toy_params(xfp_source="own"), toy_params()
    assert own.content()["xfp_source"] == "own" and ffo.content()["xfp_source"] == "ffopportunity"
    assert own.model_version != ffo.model_version
    assert rp.from_definition(own.definition()).model_version == own.model_version
    assert "xFP: own walk-forward" in own.describe()


# --------------------------------------------------------------------------------------
# The committed pin (step H6-b2): the job's preflight refuses a broken one
# --------------------------------------------------------------------------------------


def test_the_committed_pin_loads_the_2026_live_models():
    live, params, pin = rp.load_pinned_xfp(2026)
    assert params.xfp_source == "own" and live.fold == 2026 and live.version == pin.xfp.version
    assert live.train_seasons == tuple(range(2006, 2026)) and pin.xfp.train_seasons == "2006-2025"
    sizes = [m.path().stat().st_size for m in pin.xfp.models.values()]
    assert len(sizes) == 8 and sum(sizes) < 5_000_000
    assert {m.family for m in live.models.values()} <= {"lgbm", "glm", "rate"}


def test_the_jobs_preflight_stops_on_a_changed_xfp_model(tmp_path, monkeypatch):
    """A wrong sha256 in the xfp entry stops the preflight's model check (which runs before
    any download)."""
    from dataclasses import replace

    from twm.pipeline import runner

    pin_file = tmp_path / "production_models.yaml"
    shutil.copy(ROOT / "config" / "production_models.yaml", pin_file)
    xp = pins.read_pins(pin_file)["regression_watch"].xfp
    bad = {**xp.models, "yac": pins.ModelFile(xp.models["yac"].file, "0" * 64)}
    _repin(pin_file, xfp=replace(xp, models=bad))
    monkeypatch.setattr(pins, "default_pin_path", lambda: pin_file)
    with pytest.raises(pins.PinError, match="not the approved yac model"):
        runner.default_hooks().check_model(2026)
