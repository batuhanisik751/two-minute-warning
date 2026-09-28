"""The owner-approved production models (step E4): a committed pin plus the model file.

The scheduled job (``twm pipeline run`` on GitHub Actions) must score with the model the owner
approved, never with one it trained itself (PROJECT_SPEC 9: retraining is a separate, manual
workflow with a review step). A fresh runner has no ``models/`` folder (gitignored), so the
approved model travels in the repository:

- ``config/production_models.yaml`` (the **pin**): per module, the season it scores, its
  ``model_version``, the model file's path and the file's sha256;
- the model file itself under ``artifacts/production_models/<module>/<version>.joblib``
  (tracked; about 15 KB), written only by :func:`twm.modules.waiver_radar.production.save_model`
  (through :func:`approve`).

:func:`load_pinned` refuses (:class:`PinError`) unless the pin names this module and season, the
file exists, its sha256 equals the pin's (checked BEFORE the pickle is opened: only the approved
bytes are ever unpickled), and the model inside is the pinned version for that season. The
retrain workflow (``.github/workflows/retrain.yml``, ``twm model candidate``) trains a new fold
and uploads the file, the pin and a short report for the owner to review and commit; nothing
commits automatically.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

PIN_FILE = "production_models.yaml"
ARTIFACT_DIR = Path("artifacts") / "production_models"
HEADER = """\
# The owner-approved production models (step E4; src/twm/pins.py, docs/deploy.md
# "The approved model"). The scheduled job scores ONLY with the file named here, after checking
# its sha256 and the version inside. Written by `uv run twm model pin` (or copied from the
# retrain workflow's artifact after review); change it only by committing a reviewed model.
"""


class PinError(ValueError):
    """The pinned model cannot be used (missing, wrong season, wrong file or wrong version)."""


@dataclass(frozen=True)
class Pin:
    module: str
    season: int
    model_version: str
    file: str  # relative to the project root
    sha256: str
    approved: str = ""  # the date `twm model pin` wrote it (informative)

    def path(self, root: Path | None = None) -> Path:
        from twm.config import ROOT

        p = Path(self.file)
        return p if p.is_absolute() else (root if root is not None else ROOT) / p

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "season": self.season, "model_version": self.model_version, "file": self.file,
            "sha256": self.sha256,
        }  # fmt: skip
        if self.approved:
            out["approved"] = self.approved
        return out


def default_pin_path() -> Path:
    """``config/production_models.yaml`` (always the project's own config folder: a what-if
    ``TWM_CONFIG_DIR`` cannot swap the approved model)."""
    from twm.config import CONFIG_DIR

    return CONFIG_DIR / PIN_FILE


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def read_pins(path: Path | None = None) -> dict[str, Pin]:
    """Every pin in the file ({} when the file does not exist)."""
    path = path if path is not None else default_pin_path()
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text()) or {}
    if not isinstance(raw, dict):
        raise PinError(f"{path} must map module names to pins")
    pins = {}
    for module, entry in raw.items():
        if not isinstance(entry, dict):
            raise PinError(f"{path}: the pin of {module} must be a mapping")
        missing = [k for k in ("season", "model_version", "file", "sha256") if not entry.get(k)]
        unknown = sorted(set(entry) - {"season", "model_version", "file", "sha256", "approved"})
        if missing or unknown:
            raise PinError(f"{path}: the pin of {module} lacks {missing} or has unknown {unknown}")
        pins[str(module)] = Pin(
            module=str(module), season=int(entry["season"]),
            model_version=str(entry["model_version"]), file=str(entry["file"]),
            sha256=str(entry["sha256"]).lower(), approved=str(entry.get("approved") or ""),
        )  # fmt: skip
    return pins


def write_pins(pins: dict[str, Pin], path: Path | None = None) -> Path:
    path = path if path is not None else default_pin_path()
    body = yaml.safe_dump(
        {m: p.as_dict() for m, p in sorted(pins.items())}, sort_keys=False, default_flow_style=False
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(HEADER + body)
    tmp.replace(path)
    return path


def get_pin(module: str, path: Path | None = None) -> Pin:
    pins = read_pins(path)
    if module not in pins:
        where = path if path is not None else default_pin_path()
        raise PinError(
            f"no approved model for {module} in {where}: approve one with `uv run twm model pin "
            f"{module} --version <model_version>` (or from the retrain workflow's artifact)"
        )
    return pins[module]


def load_pinned(
    module: str, season: int, *, path: Path | None = None, root: Path | None = None
) -> tuple[Any, Pin]:
    """(the production model, its pin) for ``module`` and ``season``; :class:`PinError` when
    anything does not match (see the module docstring). Only ``waiver_radar`` exists."""
    from twm.modules.waiver_radar import production as prod

    if module != prod.MODULE:
        raise PinError(f"unknown module {module!r}; pinned models exist for {prod.MODULE}")
    pin = get_pin(module, path)
    if pin.season != int(season):
        raise PinError(
            f"the approved {module} model scores season {pin.season}, not {season}: train and "
            "review one for this season (the retrain workflow), then commit its pin"
        )
    file = pin.path(root)
    if not file.exists():
        raise PinError(f"the approved model file is missing: {pin.file}")
    digest = sha256_of(file)
    if digest != pin.sha256:
        raise PinError(
            f"{pin.file} is not the approved file (sha256 {digest[:12]}..., the pin says "
            f"{pin.sha256[:12]}...); it was not opened"
        )
    try:
        pm = prod.load_model(file)
    except prod.ProductionModelError as e:
        raise PinError(str(e)) from e
    if pm.model_version != pin.model_version:
        raise PinError(f"{pin.file} holds {pm.model_version}, the pin says {pin.model_version}")
    if int(pm.season) != int(season) or pm.model != prod.MODEL or pm.label != prod.LABEL:
        raise PinError(
            f"{pin.file} holds a {pm.model}/{pm.label} model for season {pm.season}, not the "
            f"production {prod.MODEL}/{prod.LABEL} model for {season}"
        )
    return pm, pin


def approve(
    pm: Any,
    *,
    root: Path | None = None,
    path: Path | None = None,
    today: datetime | None = None,
) -> Pin:
    """Write ``pm`` (a production model) to ``artifacts/production_models/<module>/`` with the
    production code's own writer and pin it in ``config/production_models.yaml`` (replacing
    the module's previous pin; other modules keep theirs). Review, then commit both files."""
    from twm.config import ROOT
    from twm.modules.waiver_radar import production as prod

    base = root if root is not None else ROOT
    target_dir = base / ARTIFACT_DIR / prod.MODULE
    file = prod.save_model(pm, target_dir)
    pins = read_pins(path)
    day = (today or datetime.now(UTC)).astimezone(UTC).date().isoformat()
    try:
        rel = file.relative_to(base).as_posix()
    except ValueError:
        rel = str(file)
    pin = Pin(prod.MODULE, int(pm.season), pm.model_version, rel, sha256_of(file), day)
    pins[prod.MODULE] = pin
    write_pins(pins, path)
    return pin


def identity_note(pm: Any, dataset: Any) -> str | None:
    """None when the pinned model was trained on exactly the training rows today's dataset
    gives (same seasons, same data hash); otherwise a plain-English warning. The pinned model
    is used either way: the job never retrains on its own."""
    import json

    from twm.modules.waiver_radar import production as prod

    try:
        seasons, dhash = prod.training_identity(dataset, pm.season)
    except prod.ProductionModelError as e:
        return f"cannot compare the approved model with today's dataset: {e}"
    row = pm.version_row
    trained = row.get("training_seasons")
    trained = json.loads(trained) if isinstance(trained, str) else list(trained or [])
    if list(seasons) == [int(s) for s in trained] and dhash == row.get("dataset_hash"):
        return None
    return (
        f"today's training data differs from the data the approved model {pm.model_version} "
        f"was trained on (data hash {str(row.get('dataset_hash'))[:12]}... then, {dhash[:12]}... "
        "now): the approved model is still used; run the retrain workflow and review it if the "
        "change matters"
    )
