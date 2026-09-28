"""The owner-approved production model (step E4): the pin loader refuses anything but the
pinned file and version, and the committed pin loads."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from tests.radar_synthetic import synthetic_dataset
from twm import pins
from twm.config import ROOT
from twm.modules.waiver_radar import production as prod


@pytest.fixture(scope="module")
def model() -> prod.ProductionModel:
    return prod.train_production(synthetic_dataset((2022, 2023, 2024), weeks=4, per_pos=15), 2025)


@pytest.fixture
def approved(model, tmp_path: Path) -> tuple[Path, Path]:
    """(project root, pin file) with ``model`` approved in it."""
    pin_path = tmp_path / "config" / pins.PIN_FILE
    pins.approve(model, root=tmp_path, path=pin_path, today=datetime(2026, 9, 28, tzinfo=UTC))
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


def test_the_retrain_report_compares_the_candidate_with_the_approved_model(model, approved):
    from twm.pipeline.report import candidate_report

    root, pin_path = approved
    pin = pins.get_pin("waiver_radar", pin_path)
    frame = synthetic_dataset((2022, 2023, 2024, 2025), weeks=4, per_pos=15, seed=5)
    same = candidate_report(model, pin, frame, old=model)
    assert "nothing to approve" in same and "Nothing was committed" in same
    other = prod.train_production(frame, 2025)
    text = candidate_report(other, pin, frame, old=model)
    assert f"approved now: {model.model_version}" in text
    assert "probabilities differ by" in text and "top 10 of each weekly list agree" in text
    missing = candidate_report(other, pin, frame, old=None, old_note="no approved model")
    assert "no approved model to compare with" in missing
