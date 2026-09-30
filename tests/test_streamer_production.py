"""The streamer's approved methods (S2a): the K model and the D/ST rule are pinned with their
frozen backtests, load bit-identically, are refused when any file is tampered with, and the
snapshots reproduce the backtest report; the Radar's pin is kept byte for byte."""

from __future__ import annotations

import dataclasses
import shutil
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import yaml

from tests.test_streamer_backtest import toy_dataset
from twm import pins
from twm import predictions as pr
from twm.config import ROOT
from twm.modules.streamer import backtest as bt
from twm.modules.streamer import backtest_report as rep
from twm.modules.streamer import confidence as sc
from twm.modules.streamer import models as sm
from twm.modules.streamer import production as sp

SEASON = 2014  # the toy backtest grades 2013; the approved methods rank 2014
DAY = datetime(2026, 9, 30, tzinfo=UTC)


def make_backtest(tmp: Path, data: pl.DataFrame) -> tuple[Path, Path]:
    """(store, report CSV) of the toy backtest of 2013 (logit + the next-opponent rule)."""
    run = bt.run_backtest(data, methods=("logit", "baseline_opponent"), test_seasons=[2013])
    preds, versions = bt.store_frames(run, created_at=datetime(2026, 9, 29, 12))
    store = tmp / "store.duckdb"
    pr.write_predictions(store, predictions=preds, versions=versions)
    csv_path = rep.write_report(rep.build_report(run, created="Created."), tmp / "backtest.md")
    return store, csv_path


@pytest.fixture(scope="module")
def data() -> pl.DataFrame:
    return toy_dataset()


@pytest.fixture(scope="module")
def backtest(data, tmp_path_factory) -> tuple[Path, Path]:
    return make_backtest(tmp_path_factory.mktemp("sbt"), data)


@pytest.fixture
def approved(data, backtest, tmp_path) -> tuple[Path, Path, dict]:
    """(project root, pin file, {position: Pin}) with both methods approved under tmp_path."""
    store, csv_path = backtest
    pin_path = tmp_path / "config" / pins.PIN_FILE
    made = sp.approve(data, store=store, season=SEASON, csv_path=csv_path, root=tmp_path,
                      path=pin_path, today=DAY)  # fmt: skip
    return tmp_path, pin_path, made


def test_both_methods_are_pinned_and_load(data, approved, backtest) -> None:
    root, pin_path, made = approved
    pm, kpin = sp.load_pinned_k(SEASON, path=pin_path, root=root)
    rule, dpin = sp.load_pinned_rule(SEASON, path=pin_path, root=root)
    assert (kpin.model, dpin.model) == ("logit", "rule")
    assert kpin.file == f"artifacts/production_models/streamer/{pm.model_version}.joblib"
    assert dpin.file.endswith(f"{rule.model_version}.json") and kpin.approved == "2026-09-30"
    raw = yaml.safe_load(pin_path.read_text())
    assert raw["streamer_dst"]["model"] == "rule" and set(raw["streamer_dst"]["backtest"]) == {
        "seasons", "hit_rates"}  # fmt: skip
    # the D/ST rule is the backtest's rule: same fingerprint as the 2013 fold would have for 2014
    assert rule.model_version == sp.rule_definition(SEASON).model_version
    assert rule.column == "next_opp_points_per_game" and rule.higher_is_better is False
    # the pinned K model is the backtest's fold for 2014 and scores bit-identically
    fresh = sp.train_k(data, SEASON)
    assert pm.model_version == fresh.model_version == made["K"].model_version
    rows = data.filter((pl.col("season") == 2014) & (pl.col("position") == "K"))
    assert np.array_equal(pm.predict(rows)[1], fresh.predict(rows)[1])
    assert np.array_equal(pm.raw(rows), fresh.raw(rows))
    frames = sp.load_k_snapshot(kpin, root)
    assert frames["predictions"].height == frames["outcomes"].height > 0
    hit_rates = sp.load_hit_rates(dpin, root)
    assert sp.track_record_mismatches(frames, hit_rates, backtest[1]) == []
    assert sp.track_record_mismatches(frames, hit_rates, root / "none.csv") != []


def _repin(pin_path: Path, key: str, **changes) -> None:
    ps = pins.read_pins(pin_path)
    ps[key] = dataclasses.replace(ps[key], **changes)
    pins.write_pins(ps, pin_path)


def test_tampered_or_wrong_files_are_refused(approved) -> None:
    root, pin_path, made = approved
    kfile, dfile = made["K"].path(root), made["DST"].path(root)
    with pytest.raises(pins.PinError, match="scores season 2014, not 2015"):
        sp.load_pinned_k(2015, path=pin_path, root=root)
    # a changed rule file is never read
    good = dfile.read_text()
    dfile.write_text(good.replace('"higher_is_better": false', '"higher_is_better": true'))
    with pytest.raises(pins.PinError, match="not the approved file"):
        sp.load_pinned_rule(SEASON, path=pin_path, root=root)
    # ... and one pinned with its new sha256 is not the code's rule
    _repin(pin_path, "streamer_dst", sha256=pins.sha256_of(dfile))
    with pytest.raises(pins.PinError, match="not the production D/ST rule"):
        sp.load_pinned_rule(SEASON, path=pin_path, root=root)
    dfile.write_text(good)
    _repin(pin_path, "streamer_dst", sha256=pins.sha256_of(dfile))
    sp.load_pinned_rule(SEASON, path=pin_path, root=root)  # restored: loads again
    # a changed model file is never unpickled
    shutil.copyfile(kfile, root / "k.bak")
    with kfile.open("ab") as f:
        f.write(b"\0")
    with pytest.raises(pins.PinError, match="not the approved file"):
        sp.load_pinned_k(SEASON, path=pin_path, root=root)
    shutil.copyfile(root / "k.bak", kfile)
    # a changed snapshot file is never read
    snap = made["DST"].backtest["hit_rates"].path(root)
    hr = pl.read_parquet(snap)
    hr.with_columns(pl.col("starts") + 1).write_parquet(snap)
    _, dpin = sp.load_pinned_rule(SEASON, path=pin_path, root=root)
    with pytest.raises(pins.PinError, match="not the approved backtest file"):
        sp.load_hit_rates(dpin, root)
    # the pin must say what the file is
    _repin(pin_path, "streamer_dst", model="logit")
    with pytest.raises(pins.PinError, match="not 'rule'"):
        sp.load_pinned_rule(SEASON, path=pin_path, root=root)
    with pytest.raises(pins.PinError, match="no approved model for streamer_k"):
        sp.load_pinned_k(SEASON, path=root / "empty.yaml", root=root)


def test_approve_refuses_a_backtest_the_report_does_not_show(data, backtest, tmp_path) -> None:
    store, csv_path = backtest
    changed = tmp_path / "backtest.csv"
    text = csv_path.read_text().splitlines()
    i = next(
        n for n, line in enumerate(text) if line.startswith("DST,baseline_opponent,pool,pooled")
    )
    cells = text[i].split(",")
    cells[7] = f"{float(cells[7]) + 0.01:.6f}"
    text[i] = ",".join(cells)
    changed.write_text("\n".join(text) + "\n")
    with pytest.raises(sp.StreamerProductionError, match="disagrees with"):
        sp.approve(data, store=store, season=SEASON, csv_path=changed, root=tmp_path,
                   path=tmp_path / "pins.yaml")  # fmt: skip
    assert not (tmp_path / "pins.yaml").exists()


def test_a_constant_k_model_is_refused(data, monkeypatch) -> None:
    monkeypatch.setattr(sm, "is_constant", lambda scores: True)
    with pytest.raises(sp.StreamerProductionError, match="constant model"):
        sp.train_k(data, SEASON)


def test_the_radar_pin_is_kept_byte_for_byte(data, backtest, tmp_path) -> None:
    committed = (ROOT / "config" / pins.PIN_FILE).read_text()
    radar = committed[committed.index("waiver_radar:") :]
    pin_path = tmp_path / pins.PIN_FILE
    pins.write_pins({"waiver_radar": pins.read_pins()["waiver_radar"]}, pin_path)
    store, csv_path = backtest
    sp.approve(data, store=store, season=SEASON, csv_path=csv_path, root=tmp_path, path=pin_path)
    text = pin_path.read_text()
    assert text.endswith(radar) and text.count("waiver_radar:") == 1
    assert pins.read_pins(pin_path)["waiver_radar"] == pins.read_pins()["waiver_radar"]
    assert "model:" not in radar  # the Radar's entry has no model key


def test_hit_rate_table_and_precision_by_hand() -> None:
    ranked = pl.DataFrame({
        "season": [2020] * 5 + [2021] * 2, "week": [1, 1, 1, 2, 2, 1, 1],
        "position": ["DST"] * 7, "rank": [1, 2, 3, 1, 2, 1, 2],
        "y_start": [True, False, True, False, False, True, True],
    })  # fmt: skip
    hr = sp.hit_rate_table(ranked)
    assert hr.rows() == [(2020, 2, 1, 1, 0), (2020, 2, 2, 1, 0), (2020, 3, 1, 1, 1),
                         (2020, 3, 2, 1, 0), (2020, 3, 3, 1, 1), (2021, 2, 1, 1, 1),
                         (2021, 2, 2, 1, 1)]  # fmt: skip
    p5 = sp.dst_precision(hr, 5).rows(named=True)
    # 2020: list of 3 (2 hits / 3) and list of 2 (0 / 2); 2021: one list of 2 (2 / 2)
    assert [(r["season"], r["n_groups"], r["n_rows"], r["n_pos"]) for r in p5] == [
        (2020, 2, 5, 2), (2021, 1, 2, 2)]  # fmt: skip
    assert p5[0]["p_sum"] == pytest.approx(2 / 3) and p5[1]["p_sum"] == pytest.approx(1.0)
    p1 = sp.dst_precision(hr, 1).rows(named=True)
    assert p1[0]["p_sum"] == pytest.approx(1.0)  # the two #1 picks of 2020: one started


def test_chance_and_priority_are_point_in_time(approved) -> None:
    root, pin_path, _ = approved
    _, kpin = sp.load_pinned_k(SEASON, path=pin_path, root=root)
    _, dpin = sp.load_pinned_rule(SEASON, path=pin_path, root=root)
    frames, hr = sp.load_k_snapshot(kpin, root), sp.load_hit_rates(dpin, root)
    k, d = sc.k_confidence(frames, SEASON), sc.dst_confidence(hr, SEASON)
    assert k.seasons == d.seasons == (2013, 2013)
    assert k.n_predictions == frames["predictions"].height
    assert d.n_predictions == int(hr["lists"].sum())
    # the D/ST chance of rank r = the rule's start rate at r (bins of 100+ picks, never higher
    # for a worse rank)
    band = d.band(np.array([-1.0, -2.0, -6.0]))
    assert band["chance"].to_list() == sorted(band["chance"].to_list(), reverse=True)
    assert int(d.bins["n"].sum()) == int(hr["lists"].sum())
    with pytest.raises(ValueError, match="no backtest season before 2013"):
        sc.k_confidence(frames, 2013)
    with pytest.raises(ValueError, match="no backtest season before 2012"):
        sc.dst_confidence(hr, 2012)


def test_the_committed_streamer_pins_check_out() -> None:
    """config/production_models.yaml pins the streamer for the current season; the files are
    committed, small, and reproduce reports/streamer/backtest.csv (`twm model check`)."""
    from typer.testing import CliRunner

    from twm.cli import app
    from twm.config import settings

    season = settings().current_season
    pm, kpin = sp.load_pinned_k(season)
    rule, dpin = sp.load_pinned_rule(season)
    for pin in (kpin, dpin):
        assert (ROOT / pin.file).stat().st_size < 100_000 and not (ROOT / pin.file).is_symlink()
    assert pm.model == "logit" and rule.column == "next_opp_points_per_game"
    res = CliRunner().invoke(app, ["model", "check", "streamer"])
    assert res.exit_code == 0, res.output
    assert "streamer_dst" in res.output and "matches reports/streamer/backtest.csv" in res.output
    assert "waiver_radar" not in res.output
