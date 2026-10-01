"""The Decision Report Card's approved grading (step P3): what the scheduled job grades the season
in progress with, and the frozen history it publishes.

The job must grade with models the owner approved, never with ones it trained (PROJECT_SPEC 9),
and a fresh runner has no ``models/`` folder (gitignored) and only the 2012+ warehouse. So the
current season's grading travels in the repository, pinned in ``config/production_models.yaml``
as the entry ``decisions`` (``model: grading``):

- the **grading spec** ``artifacts/production_models/decisions/<version>.json`` (the pin's file
  and sha256; version = a hash of its content): the season, each fold model (WP, conversion,
  field goal, punt, tries) by version, file and sha256, the season-level inputs G3 measured on
  the full warehouse (the longest made field goal and the punt range before S, the field
  goal's runoffs, the Platt choice; :mod:`~twm.modules.decisions.grade_inputs`), G4's clock
  constants and the ``decisions:`` settings the grades were made with;
- the five **fold models** ``artifacts/production_models/decisions/<model version>.joblib``,
  byte copies of G1b's and G2's current-season folds (each opened only after its sha256
  matched the spec: a pickle is only ever opened when it is the approved bytes);
- the **frozen history** (:mod:`twm.modules.decisions.frozen`) as the pin's snapshot.

:func:`load_pinned` refuses (:class:`twm.pins.PinError`) unless all of it matches;
:func:`approve` (``twm decisions pin``) writes it on the owner's Mac and refuses unless the
history reproduces the committed reports and the pinned grading reproduces the stored grades of
the season in progress exactly.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PIN_KEY = "decisions"
MODULE = "decisions"
MODEL = "grading"  # the pin's `model`: a grading spec naming pickled fold models by sha256
SPEC_FORMAT = 1
ARTIFACT_SUBDIR = "decisions"
MODEL_NAMES = ("wp", "conversion", "fieldgoal", "punt", "tries")
SEASON_INPUTS = ("fg_max_distance", "punt_min_yardline", "fg_runoff_make", "fg_runoff_miss",
                 "runoff_seasons", "platt")  # fmt: skip
CLOCK_INPUTS = ("kneel_play", "kneel_cycle", "play_seconds_run", "play_seconds_pass",
                "n_kneel_play", "n_kneel_cycle", "n_play_seconds_run", "n_play_seconds_pass",
                "constant_seasons")  # fmt: skip


class DecisionsProductionError(ValueError):
    """The grading cannot be approved (stored grades missing or inconsistent)."""


@dataclass(frozen=True)
class GradingSpec:
    """The approved grading of one season (module docstring)."""

    season: int
    models: dict[str, dict[str, str]]  # name -> {version, file, sha256}
    inputs: dict[str, Any]  # SEASON_INPUTS
    clock: dict[str, Any]  # CLOCK_INPUTS
    config: dict[str, Any]  # settings `decisions:` (DecisionsConfig) as approved
    model_version: str = field(default="")

    def content(self) -> dict[str, Any]:
        return {"format": SPEC_FORMAT, "module": MODULE, "season": int(self.season),
                "models": self.models, "inputs": self.inputs, "clock": self.clock,
                "config": self.config}  # fmt: skip

    def cfg(self) -> Any:
        from twm.config import DecisionsConfig

        return DecisionsConfig(**self.config)

    def versions(self) -> dict[str, str]:
        return {f"{k}_version": self.models[k]["version"] for k in MODEL_NAMES}

    def info(self) -> dict[str, Any]:
        """G3's season-level ``info`` (:func:`twm.modules.decisions.grade.season_inputs`)."""
        from twm.modules.decisions import grade as gr

        c = self.cfg()
        return {**{k: self.inputs[k] for k in SEASON_INPUTS}, "margin": float(c.toss_up_margin),
                "end_of_half_seconds": int(c.end_of_half_seconds), **gr.late_game_info(c),
                **self.versions()}  # fmt: skip

    def describe(self) -> str:
        platt = "kept" if self.inputs["platt"].get("kept") else "not kept"
        return (f"season {self.season}: WP {self.models['wp']['version']}, sub-models "
                f"{', '.join(self.models[k]['version'] for k in MODEL_NAMES[1:])}; FG to "
                f"{self.inputs['fg_max_distance']} yd, punts from the "
                f"{self.inputs['punt_min_yardline']}, Platt {platt}")  # fmt: skip


def version_of(content: dict[str, Any]) -> str:
    text = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return f"{MODEL}-{hashlib.sha256(text.encode()).hexdigest()[:16]}"


def spec_json(spec: GradingSpec) -> str:
    return json.dumps({**spec.content(), "model_version": spec.model_version}, indent=1,
                      sort_keys=True) + "\n"  # fmt: skip


def from_content(d: dict[str, Any]) -> GradingSpec:
    """A spec from its JSON content; :class:`DecisionsProductionError` when it is not one this
    code wrote (format, keys) or its version is not the hash of its content."""
    try:
        if d.get("format") != SPEC_FORMAT or d.get("module") != MODULE:
            raise KeyError("format/module")
        spec = GradingSpec(season=int(d["season"]), models=d["models"], inputs=d["inputs"],
                           clock=d["clock"], config=d["config"])  # fmt: skip
        missing = [k for k in MODEL_NAMES if {"version", "file", "sha256"} - set(spec.models[k])]
        missing += [k for k in SEASON_INPUTS if k not in spec.inputs]
        missing += [k for k in CLOCK_INPUTS if k not in spec.clock]
        if missing:
            raise KeyError(", ".join(missing))
    except (KeyError, TypeError, ValueError) as e:
        raise DecisionsProductionError(f"not a grading spec of this code (missing {e})") from e
    version = version_of(spec.content())
    if d.get("model_version") != version:
        raise DecisionsProductionError(
            f"the spec says {d.get('model_version')}, its content is {version}"
        )
    return GradingSpec(**{**spec.__dict__, "model_version": version})


def load_spec(path: Path) -> GradingSpec:
    try:
        return from_content(json.loads(Path(path).read_text()))
    except (OSError, ValueError) as e:
        if isinstance(e, DecisionsProductionError):
            raise
        raise DecisionsProductionError(f"cannot read the grading spec {path}: {e}") from e


def artifact_dir(root: Path | None = None) -> Path:
    from twm import pins
    from twm.config import ROOT

    return (root if root is not None else ROOT) / pins.ARTIFACT_DIR / ARTIFACT_SUBDIR


def _rel(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return str(path)


def _checked_file(entry: dict[str, str], what: str, root: Path | None) -> Path:
    """The file an entry names, only after its sha256 matched (else PinError, never opened)."""
    from twm import pins

    file = pins._resolve(entry["file"], root)
    if not file.exists():
        raise pins.PinError(f"the approved {what} file is missing: {entry['file']}")
    digest = pins.sha256_of(file)
    if digest != entry["sha256"]:
        raise pins.PinError(f"{entry['file']} is not the approved {what} file (sha256 "
                            f"{digest[:12]}..., the pin says {entry['sha256'][:12]}...); it was "
                            "not opened")  # fmt: skip
    return file


def load_pinned_spec(season: int, *, path: Path | None = None, root: Path | None = None):
    """(the approved grading spec, its pin) for ``season``: the pin is a grading pin of that
    season, the spec file has the pinned sha256 (checked before it is read) and version."""
    from twm import pins

    pin = pins.get_pin(PIN_KEY, path)
    if pin.model != MODEL:
        raise pins.PinError(f"the pin of {PIN_KEY} says model {pin.model!r}, not {MODEL!r}")
    if pin.season != int(season):
        raise pins.PinError(
            f"the approved decisions grading is for season {pin.season}, not {season}: approve "
            "one for this season (`uv run twm decisions pin`), review and commit")  # fmt: skip
    file = _checked_file({"file": pin.file, "sha256": pin.sha256}, "grading spec", root)
    try:
        spec = load_spec(file)
    except DecisionsProductionError as e:
        raise pins.PinError(str(e)) from e
    if spec.model_version != pin.model_version or spec.season != int(season):
        raise pins.PinError(f"{pin.file} holds {spec.model_version}, the pin says "
                            f"{pin.model_version}")  # fmt: skip
    return spec, pin


def load_pinned(season: int, *, path: Path | None = None, root: Path | None = None):
    """(spec, the fold models as :class:`twm.modules.decisions.grade.Models`, pin): every model
    file opened only after its sha256 matched the spec, holding the spec's version, the fold
    of ``season`` trained on earlier seasons only."""
    from twm import pins
    from twm.modules.decisions import grade as gr
    from twm.modules.decisions import submodels as sm
    from twm.modules.decisions import wp as wpm

    spec, pin = load_pinned_spec(season, path=path, root=root)
    loaded = {}
    for name in MODEL_NAMES:
        entry = spec.models[name]
        file = _checked_file(entry, f"{name} model", root)
        try:
            m = wpm.load_model(file) if name == "wp" else sm.load_model(file)
        except (wpm.WpModelError, sm.SubModelError) as e:
            raise pins.PinError(str(e)) from e
        if m.version != entry["version"]:
            raise pins.PinError(f"{entry['file']} holds {m.version}, the spec says "
                                f"{entry['version']}")  # fmt: skip
        loaded[name] = m
    models = gr.Models(**loaded)
    try:
        models.check(int(season))
    except gr.GradeError as e:
        raise pins.PinError(str(e)) from e
    return spec, models, pin


# --------------------------------------------------------------------------------------
# Approving (the owner's Mac)
# --------------------------------------------------------------------------------------


def stored_root() -> Path:
    """``data/decisions``: G3's ``graded/`` and G4's ``clock/`` stored grades."""
    from twm.modules.decisions import wp as wpm

    return wpm.backtest_dir().parent


def build_spec(season: int, *, stored: Path | None = None, models_root: Path | None = None,
               root: Path | None = None) -> tuple[GradingSpec, dict[str, Path]]:  # fmt: skip
    """The spec of ``season`` from its stored G3 and G4 summaries (``season_<S>.json``: the
    inputs the stored grades were made with) and the current settings, which must be the ones
    those grades used; plus {model name: its file in ``models_root``} to copy."""
    from twm import pins
    from twm.config import ROOT, settings
    from twm.modules.decisions import clock as ck
    from twm.modules.decisions import grade as gr
    from twm.modules.decisions import wp as wpm

    base = stored if stored is not None else stored_root()
    try:
        g = json.loads((base / "graded" / f"season_{season}.json").read_text())
        c = json.loads((base / "clock" / f"season_{season}.json").read_text())
    except (OSError, ValueError) as e:
        raise DecisionsProductionError(
            f"no stored grades of {season}: run `twm decisions grade` and `twm decisions clock` "
            f"for it first ({e})") from e  # fmt: skip
    cfg = settings().decisions
    clock_cfg = cfg.clock.model_dump()
    odd = [k for k in clock_cfg if c.get(k) != clock_cfg[k]]
    odd += [k for k, v in gr.late_game_info(cfg).items() if g.get(k) != v]
    if (g.get("margin") != float(cfg.toss_up_margin)
            or g.get("end_of_half_seconds") != int(cfg.end_of_half_seconds) or odd
            or g.get("wp_version") != c.get("wp_version")):  # fmt: skip
        raise DecisionsProductionError(
            f"the stored grades of {season} were made with other settings or another WP model "
            f"than today's ({odd or 'margin / end of half / WP'}): regrade the season first"
        )
    mroot = models_root if models_root is not None else wpm.models_dir()
    target = artifact_dir(root)
    base_root = root if root is not None else ROOT
    models, sources = {}, {}
    for name in MODEL_NAMES:
        version = g[f"{name}_version"]
        src = mroot / f"{version}.joblib"
        if not src.exists():
            raise DecisionsProductionError(f"the {name} model {version} is not in {mroot}")
        models[name] = {"version": version, "sha256": pins.sha256_of(src),
                        "file": _rel(target / f"{version}.joblib", base_root)}  # fmt: skip
        sources[name] = src
    if g.get("format") != gr.GRADE_FORMAT or c.get("format") != ck.CLOCK_FORMAT:
        raise DecisionsProductionError(f"the stored grades of {season} are of another format")
    spec = GradingSpec(season=int(season), models=models,
                       inputs={k: g[k] for k in SEASON_INPUTS},
                       clock={k: c[k] for k in CLOCK_INPUTS}, config=cfg.model_dump())  # fmt: skip
    return GradingSpec(**{**spec.__dict__, "model_version": version_of(spec.content())}), sources


def approve(season: int, *, db: Path | str, stored: Path | None = None,
            models_root: Path | None = None, reports: dict[str, Path] | None = None,
            root: Path | None = None, path: Path | None = None, today: Any = None,
            progress: Any = print) -> Any:  # fmt: skip
    """Approve the grading of ``season`` (module docstring): build the spec from the stored
    grades, copy the fold models (byte for byte), freeze the history of every earlier graded
    season, then REFUSE unless the history reproduces ``reports`` (default
    reports/decisions/{fourth_downs,clock}.csv) and grading ``season`` with the copied pins
    from the warehouse ``db`` reproduces the stored grades exactly. Writes the pin last (other
    modules keep theirs). Returns the new pin."""
    import tempfile
    from datetime import UTC, datetime

    from twm import pins
    from twm.config import ROOT
    from twm.modules.decisions import frozen as fz
    from twm.modules.decisions import season as sn
    from twm.modules.decisions import wp as wpm

    base = root if root is not None else ROOT
    store = stored if stored is not None else stored_root()
    reps = reports if reports is not None else {
        "fourth_downs": ROOT / "reports" / "decisions" / "fourth_downs.csv",
        "clock": ROOT / "reports" / "decisions" / "clock.csv"}  # fmt: skip
    spec, sources = build_spec(season, stored=store, models_root=models_root, root=base)
    history = [s for s in wpm.TEST_SEASONS if s < int(season)]
    frames = fz.build_snapshot(history, graded_dir=store / "graded", clock_dir=store / "clock")
    problems = fz.snapshot_mismatches(frames, reps)
    if problems:
        raise DecisionsProductionError(
            f"the frozen history disagrees with the reports in {len(problems)} places (e.g. "
            f"{problems[0]}): regenerate them from the same stored grades")  # fmt: skip
    progress(f"history {history[0]}-{history[-1]}: reproduces {', '.join(reps)} reports")
    out = artifact_dir(base)
    out.mkdir(parents=True, exist_ok=True)
    for name, src in sources.items():
        dst = pins._resolve(spec.models[name]["file"], base)
        if not dst.exists() or pins.sha256_of(dst) != spec.models[name]["sha256"]:
            shutil.copyfile(src, dst)
    spec_file = out / f"{spec.model_version}.json"
    spec_file.write_text(spec_json(spec))
    pin = pins.Pin(PIN_KEY, int(season), spec.model_version, _rel(spec_file, base),
                   pins.sha256_of(spec_file), model=MODEL)  # fmt: skip
    with tempfile.TemporaryDirectory() as tmp:
        tmp_pins = Path(tmp) / "pins.yaml"
        pins.write_pins({PIN_KEY: pin}, tmp_pins)
        spec2, models, _ = load_pinned(season, path=tmp_pins, root=base)
        sn.grade_season(db, season, spec2, models, out_dir=Path(tmp), progress=progress)
        diff = sn.differences(season, store, Path(tmp))
    if diff:
        raise DecisionsProductionError(
            f"grading {season} with the pinned spec and models differs from the stored grades "
            f"in {', '.join(diff[:4])}: regrade the season with today's warehouse first"
        )
    progress(f"{season}: the pinned grading reproduces the stored grades exactly")
    files = fz.write_snapshot(frames, spec.model_version, base)
    day = (today or datetime.now(UTC)).astimezone(UTC).date().isoformat()
    new = pins.Pin(PIN_KEY, int(season), spec.model_version, pin.file, pin.sha256, day,
                   f"{history[0]}-{history[-1]}", files, MODEL)  # fmt: skip
    current = pins.read_pins(path)
    current[PIN_KEY] = new
    pins.write_pins(current, path)
    return new
