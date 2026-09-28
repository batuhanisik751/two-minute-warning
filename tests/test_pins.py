"""The owner-approved production model and its backtest snapshot (step E4): the pin loader
refuses anything but the pinned files, the snapshot restores exactly and is refused when
tampered with, and the committed snapshot reproduces the committed evaluation."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest
import yaml

from tests.radar_synthetic import synthetic_dataset
from twm import pins
from twm import predictions as pr
from twm.config import ROOT
from twm.modules.waiver_radar import backtest as bt
from twm.modules.waiver_radar import production as prod

T0 = datetime(2026, 9, 28, 7, 0)


@pytest.fixture(scope="module")
def model() -> prod.ProductionModel:
    return prod.train_production(synthetic_dataset((2022, 2023, 2024), weeks=4, per_pos=15), 2025)


def backtest_store(path: Path, seed: int = 7, created: datetime = T0) -> Path:
    """A store with a logit y_hit backtest of 2023-2024 (and an lgbm one the snapshot leaves
    out), like `twm radar backtest` writes it."""
    ds = synthetic_dataset((2022, 2023, 2024), weeks=4, per_pos=15, seed=seed)
    run = bt.run_backtest(ds, models=("logit", "baseline_last_points"), labels=("y_hit",),
                          test_seasons=(2023, 2024))  # fmt: skip
    preds, versions, outcomes = bt.store_frames(run, created_at=created)
    pr.write_predictions(path, predictions=preds, versions=versions, outcomes=outcomes)
    return path


@pytest.fixture(scope="module")
def store(tmp_path_factory) -> Path:
    return backtest_store(tmp_path_factory.mktemp("bt") / "predictions.duckdb")


@pytest.fixture
def approved(model, store, tmp_path: Path) -> tuple[Path, Path]:
    """(project root, pin file) with ``model`` and ``store``'s backtest approved in it."""
    pin_path = tmp_path / "config" / pins.PIN_FILE
    pins.approve(model, store=store, root=tmp_path, path=pin_path,
                 today=datetime(2026, 9, 28, tzinfo=UTC))  # fmt: skip
    return tmp_path, pin_path


def test_the_pinned_version_loads_and_scores_like_the_original(model, approved) -> None:
    root, pin_path = approved
    pm, pin = pins.load_pinned("waiver_radar", 2025, path=pin_path, root=root)
    assert pm.model_version == model.model_version == pin.model_version
    assert pin.file == f"artifacts/production_models/waiver_radar/{model.model_version}.joblib"
    assert pin.approved == "2026-09-28" and len(pin.sha256) == 64
    x = synthetic_dataset((2024,), weeks=2, per_pos=5)
    assert (pm.predict(x)[1] == model.predict(x)[1]).all()
    text = pin_path.read_text()
    assert text.startswith("# The owner-approved") and "sha256" in text


def test_a_changed_file_is_refused_before_it_is_opened(model, approved, monkeypatch) -> None:
    root, pin_path = approved
    pin = pins.get_pin("waiver_radar", pin_path)
    opened = []
    monkeypatch.setattr(prod, "load_model", lambda p: opened.append(p))
    with pin.path(root).open("ab") as f:
        f.write(b"x")
    with pytest.raises(pins.PinError, match="not the approved file"):
        pins.load_pinned("waiver_radar", 2025, path=pin_path, root=root)
    assert opened == []  # never unpickled


def test_a_file_holding_another_version_is_refused(model, approved, tmp_path: Path) -> None:
    root, pin_path = approved
    other = prod.train_production(synthetic_dataset((2022, 2023, 2024), weeks=4, per_pos=15,
                                                    seed=99), 2025)  # fmt: skip
    assert other.model_version != model.model_version
    pin = pins.get_pin("waiver_radar", pin_path)
    written = prod.save_model(other, tmp_path / "elsewhere")
    shutil.copyfile(written, pin.path(root))  # right name, wrong model inside
    raw = yaml.safe_load(pin_path.read_text())
    raw["waiver_radar"]["sha256"] = pins.sha256_of(pin.path(root))  # even with a matching hash
    pin_path.write_text(yaml.safe_dump(raw))
    with pytest.raises(pins.PinError, match=f"holds {other.model_version}"):
        pins.load_pinned("waiver_radar", 2025, path=pin_path, root=root)


def test_a_missing_file_pin_or_season_is_refused(approved, tmp_path: Path) -> None:
    root, pin_path = approved
    with pytest.raises(pins.PinError, match="scores season 2025, not 2026"):
        pins.load_pinned("waiver_radar", 2026, path=pin_path, root=root)
    with pytest.raises(pins.PinError, match="no approved model"):
        pins.load_pinned("waiver_radar", 2025, path=tmp_path / "none.yaml", root=root)
    with pytest.raises(pins.PinError, match="unknown module"):
        pins.load_pinned("hot_seat", 2025, path=pin_path, root=root)
    pins.get_pin("waiver_radar", pin_path).path(root).unlink()
    with pytest.raises(pins.PinError, match="file is missing"):
        pins.load_pinned("waiver_radar", 2025, path=pin_path, root=root)
    raw = yaml.safe_load(pin_path.read_text())
    del raw["waiver_radar"]["sha256"]
    pin_path.write_text(yaml.safe_dump(raw))
    with pytest.raises(pins.PinError, match="lacks"):
        pins.read_pins(pin_path)


def test_the_identity_note_reports_changed_training_data(model) -> None:
    same = synthetic_dataset((2022, 2023, 2024), weeks=4, per_pos=15)
    assert pins.identity_note(model, same) is None
    changed = synthetic_dataset((2022, 2023, 2024), weeks=4, per_pos=15, seed=3)
    note = pins.identity_note(model, changed)
    assert note is not None and "still used" in note


def test_the_committed_pin_loads() -> None:
    """config/production_models.yaml and its file are committed and consistent (what the
    scheduled job loads first)."""
    from twm.config import settings

    pin = pins.get_pin("waiver_radar")
    assert pin.season == settings().current_season
    assert (ROOT / pin.file).exists() and not (ROOT / pin.file).is_symlink()
    assert (ROOT / pin.file).stat().st_size < 100_000  # small enough to commit
    pm, _ = pins.load_pinned("waiver_radar", pin.season)
    assert pm.model_version == pin.model_version


def test_the_retrain_report_compares_the_candidate_with_the_approved_model(
    model, approved, store, tmp_path
) -> None:
    from twm.pipeline.report import candidate_report

    root, pin_path = approved
    pin = pins.get_pin("waiver_radar", pin_path)
    frame = synthetic_dataset((2022, 2023, 2024, 2025), weeks=4, per_pos=15, seed=5)
    same = candidate_report(model, pin, frame, old=model, store=store, old_pin=None)
    assert "identical to the approved model" in same and "Nothing was committed" in same
    assert "candidate backtest (2023-2024): precision@10" in same
    other = prod.train_production(frame, 2025)
    text = candidate_report(other, pin, frame, old=model)
    assert f"approved now: {model.model_version}" in text
    assert "probabilities differ by" in text and "top 10 of each weekly list agree" in text
    # a candidate backtest compared with the approved snapshot; a mismatch with the evaluation
    cand = backtest_store(tmp_path / "cand.duckdb", seed=3)
    old_pin = pins.Pin(**{**pin.__dict__, "backtest": {
        t: pins.SnapshotFile(str(b.path(root)), b.sha256, b.rows) for t, b in
        pin.backtest.items()}})  # fmt: skip
    text = candidate_report(other, pin, frame, old=model, store=cand, old_pin=old_pin,
                            evaluation_problems=["pooled p_at_10: n_top_hits 1 vs 2"])  # fmt: skip
    assert "approved backtest (2023-2024): precision@10" in text
    assert "does NOT reproduce the committed" in text and "Nothing to approve" not in text
    again = candidate_report(model, pin, frame, old=model, store=store, old_pin=old_pin)
    assert "the two backtests give the same lists' numbers" in again
    assert "Nothing to approve" in again
    missing = candidate_report(other, pin, frame, old=None, old_note="no approved model")
    assert "no approved model to compare with" in missing


# --------------------------------------------------------------------------------------
# The backtest snapshot
# --------------------------------------------------------------------------------------


def _current(store: Path) -> pl.DataFrame:
    return (
        pr.current_versions(store, "waiver_radar", "y_hit")
        .filter(pl.col("model") == "logit")
        .select("model_version", "test_season", "created_at")
    )


def test_the_snapshot_restores_the_approved_backtest_exactly(approved, store, tmp_path) -> None:
    root, pin_path = approved
    pin = pins.get_pin("waiver_radar", pin_path)
    assert set(pin.backtest) == set(pins.BACKTEST_TABLES) and pin.backtest_seasons == "2023-2024"
    folder = f"artifacts/production_models/waiver_radar/backtest-{pin.model_version}"
    assert {b.file for b in pin.backtest.values()} == {
        f"{folder}/{t}.parquet" for t in pins.BACKTEST_TABLES}  # fmt: skip
    # only the production model and label, only the walk-forward rows
    frames = pins.load_backtest(pin, root)
    assert set(frames["model_versions"].get_column("model").to_list()) == {"logit"}
    assert frames["predictions"].height == pin.backtest["predictions"].rows > 0
    fresh = tmp_path / "fresh.duckdb"
    assert pins.restore_backtest(pin, fresh, root=root)["status"] == "restored"
    assert _current(fresh).equals(_current(store))  # same versions, same created_at
    cols = "p.model_version, entity_id, season, week, score, raw_score, rank, kind, p.created_at"
    q = f"SELECT {cols} FROM predictions p JOIN model_versions v USING (model_version) " \
        "WHERE v.model = 'logit' ORDER BY ALL"  # fmt: skip
    a, b = pr.connect(fresh, read_only=True), pr.connect(store, read_only=True)
    try:
        assert a.execute(q).fetchall() == b.execute(q).fetchall()
    finally:
        a.close()
        b.close()
    # a second restore is a no-op; a store with another backtest is refused
    assert pins.restore_backtest(pin, fresh, root=root)["status"] == "already there"
    other = backtest_store(tmp_path / "other.duckdb", seed=3)
    with pytest.raises(pins.PinError, match="different logit/y_hit backtest"):
        pins.restore_backtest(pin, other, root=root)


def test_a_tampered_snapshot_is_refused_before_it_is_read(approved, monkeypatch) -> None:
    root, pin_path = approved
    pin = pins.get_pin("waiver_radar", pin_path)
    reads = []
    real = pl.read_parquet
    monkeypatch.setattr(pl, "read_parquet", lambda p, *a, **k: reads.append(p) or real(p))
    path = pin.backtest["outcomes"].path(root)
    df = real(path)
    df.with_columns(pl.col("y_hit").not_()).write_parquet(path)  # flip every outcome
    with pytest.raises(pins.PinError, match="not the approved backtest file"):
        pins.load_backtest(pin, root)
    assert path not in reads  # never read
    with pytest.raises(pins.PinError, match="not the approved backtest file"):
        pins.restore_backtest(pin, root / "x.duckdb", root=root)


def test_a_snapshot_with_other_rows_or_none_is_refused(approved) -> None:
    root, pin_path = approved
    raw = yaml.safe_load(pin_path.read_text())
    raw["waiver_radar"]["backtest"]["model_versions"]["rows"] += 1
    pin_path.write_text(yaml.safe_dump(raw))
    with pytest.raises(pins.PinError, match="rows, the pin says"):
        pins.load_backtest(pins.get_pin("waiver_radar", pin_path), root)
    del raw["waiver_radar"]["backtest"]
    pin_path.write_text(yaml.safe_dump(raw))
    with pytest.raises(pins.PinError, match="no approved backtest snapshot"):
        pins.load_backtest(pins.get_pin("waiver_radar", pin_path), root)
    pins.get_pin("waiver_radar", pin_path)  # the model alone still loads
    pins.load_pinned("waiver_radar", 2025, path=pin_path, root=root)


def test_approving_again_keeps_the_model_file_byte_for_byte(model, approved, store) -> None:
    root, pin_path = approved
    before = pins.get_pin("waiver_radar", pin_path)
    again = pins.approve(model, store=store, root=root, path=pin_path)
    assert again.sha256 == before.sha256 and again.backtest == before.backtest


def test_evaluation_mismatches_compare_the_store_with_the_report(store, tmp_path) -> None:
    import csv

    from twm.modules.waiver_radar import evaluation as ev

    rows = ev.load_predictions(store, "y_hit").filter(pl.col("model") == "logit")
    report = tmp_path / "evaluation.csv"
    with report.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(ev.CSV_COLUMNS))
        w.writeheader()
        w.writerows(ev.store_only_results(rows, "y_hit"))
    assert pins.evaluation_mismatches(store, report) == []
    text = report.read_text()
    first = next(r for r in csv.DictReader(text.splitlines()) if r["scope"] == "pooled")
    report.write_text(text.replace(f",{first['n_top_hits']},", f",{int(first['n_top_hits']) + 1},",
                                   1))  # fmt: skip
    problems = pins.evaluation_mismatches(store, report)
    assert problems and "n_top_hits" in problems[0]
    assert pins.evaluation_mismatches(store, tmp_path / "none.csv")[0].startswith("the evaluation")


def test_the_committed_snapshot_reproduces_the_committed_evaluation(tmp_path) -> None:
    """The published track record (reports/waiver_radar/evaluation.csv) and the lists' chance,
    band and priority (the snapshot) come from the same backtest: every logit y_hit row the
    store alone determines matches, counts exactly, values to the CSV's 6 decimals. Committed
    files only: this runs in CI."""
    pin = pins.get_pin("waiver_radar")
    store = tmp_path / "restored.duckdb"
    done = pins.restore_backtest(pin, store)
    assert done["status"] == "restored" and done["model_versions"] == 12
    assert pins.evaluation_mismatches(store, ROOT / "reports/waiver_radar/evaluation.csv") == []
    # the priority table the site shows, from the snapshot
    from twm.publish.collect import tier_stats

    tiers = tier_stats(store, pin.season).filter(pl.col("week") == 0)
    assert tiers.get_column("tier").to_list() == ["must-add", "speculative", "watch"]
    assert tiers.get_column("hits").to_list() == [1495, 3915, 723]  # the Mac's backtest


@pytest.mark.realdata
def test_the_snapshot_reproduces_every_logit_row_with_the_real_dataset(tmp_path) -> None:
    """With the real dataset, every logit y_hit row of the evaluation that does not compare
    with another method (those methods are not in the snapshot) is reproduced, incl. the
    without-rostered subset, the breakouts and every interval."""
    import csv

    from twm.modules.waiver_radar import evaluation as ev

    ds_path = ROOT / "data/waiver_radar/dataset.parquet"
    if not ds_path.exists():
        pytest.skip("real dataset not present")
    store = tmp_path / "restored.duckdb"
    pins.restore_backtest(pins.get_pin("waiver_radar"), store)
    e = ev.evaluate_label(ev.load(store, pl.read_parquet(ds_path), "y_hit"))
    got = {tuple(str(r[c]) for c in ev.CSV_COLUMNS[:7]): r for r in e.results
           if r["model"] == "logit"}  # fmt: skip
    with (ROOT / "reports/waiver_radar/evaluation.csv").open() as f:
        want = {tuple(r[c] for c in ev.CSV_COLUMNS[:7]): r for r in csv.DictReader(f)
                if r["label"] == "y_hit" and r["model"] == "logit"}  # fmt: skip
    compared = {"diff", "position_diff", "experts", "experts_diff", "breakouts_diff",
                "breakouts_experts", "breakouts_experts_diff"}  # fmt: skip
    assert set(got) == {k for k in want if k[3] not in compared} and len(got) > 200
    for k, r in got.items():
        for c in ev.CSV_COLUMNS[7:]:
            x, y = str(ev._fmt(r[c])), want[k][c]
            assert x == y or abs(float(x) - float(y)) <= pins.FLOAT_TOLERANCE, (k, c, x, y)


def _copied_pin(tmp_path: Path) -> Path:
    """A copy of the committed pin whose files are copies under tmp_path (absolute paths)."""
    pin = pins.get_pin("waiver_radar")
    files = {}
    for t, b in pin.backtest.items():
        dst = tmp_path / "snap" / f"{t}.parquet"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(b.path(), dst)
        files[t] = pins.SnapshotFile(str(dst), b.sha256, b.rows)
    model = tmp_path / "model.joblib"
    shutil.copyfile(pin.path(), model)
    copy = pins.Pin(**{**pin.__dict__, "file": str(model), "backtest": files})
    return pins.write_pins({"waiver_radar": copy}, tmp_path / "production_models.yaml")


def test_model_check_covers_the_snapshot(tmp_path: Path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from twm.cli import app

    runner = CliRunner()
    ok = runner.invoke(app, ["model", "check"])
    assert ok.exit_code == 0, ok.output
    assert "backtest snapshot 2014-2025: 91,638 predictions" in ok.output
    assert "matches reports/waiver_radar/evaluation.csv" in ok.output
    # a tampered snapshot file
    path = _copied_pin(tmp_path)
    monkeypatch.setattr(pins, "default_pin_path", lambda: path)
    assert runner.invoke(app, ["model", "check"]).exit_code == 0  # the copies are intact
    target = tmp_path / "snap" / "predictions.parquet"
    df = pl.read_parquet(target)
    df.with_columns(pl.col("rank").reverse()).write_parquet(target)
    bad = runner.invoke(app, ["model", "check"])
    assert bad.exit_code == 1 and "not the approved backtest file" in bad.output
    # an evaluation that the snapshot does not reproduce
    monkeypatch.undo()
    csv_copy = tmp_path / "evaluation.csv"
    text = (ROOT / "reports/waiver_radar/evaluation.csv").read_text()
    csv_copy.write_text(text.replace("0.478279", "0.477869", 1))
    off = runner.invoke(app, ["model", "check", "--evaluation", str(csv_copy)])
    assert off.exit_code == 1 and "disagrees with" in off.output


def test_restore_backtest_command(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from twm.cli import app

    runner = CliRunner()
    store = tmp_path / "predictions.duckdb"
    first = runner.invoke(app, ["model", "restore-backtest", "--store", str(store)])
    assert first.exit_code == 0, first.output
    assert (
        first.output.startswith("restored: backtest snapshot 2014-2025")
        and "checked" in first.output
    )
    again = runner.invoke(app, ["model", "restore-backtest", "--store", str(store)])
    assert again.exit_code == 0 and again.output.startswith("already there")
