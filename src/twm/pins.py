"""The owner-approved production models (step E4): a committed pin, the model file and the
backtest the model was approved with.

The scheduled job (``twm pipeline run`` on GitHub Actions) must score with the model the owner
approved, never with one it trained itself (PROJECT_SPEC 9: retraining is a separate, manual
workflow with a review step). A fresh runner has no ``models/`` folder and no predictions store
(both gitignored), so the approved model travels in the repository:

- ``config/production_models.yaml`` (the **pin**): per module, the season it scores, its
  ``model_version``, the model file and its sha256, and the backtest snapshot's files with
  their sha256 and row counts;
- the model file under ``artifacts/production_models/<module>/<version>.joblib`` (about 15 KB),
  written only by :func:`twm.modules.waiver_radar.production.save_model` (through
  :func:`approve`);
- the **backtest snapshot** under ``artifacts/production_models/<module>/backtest-<version>/``:
  the walk-forward backtest of the production model and label (``logit`` / ``y_hit``) for the
  evaluation seasons before the pinned season, as three zstd Parquet files (``predictions``,
  ``outcomes``, ``model_versions``), exported read-only from the store the owner approved.
  The weekly list's chance, band and priority, the priority table and the time-machine lists
  are computed from it, so they come from the same backtest as the committed evaluation
  (``reports/waiver_radar/evaluation.csv``, the published track record) instead of a rebuild
  whose float noise differs between machines (a Linux runner's rebuild of the 2022-2023 folds
  swapped a few top-10 orderings: 3,498 vs 3,501 hits).

:func:`load_pinned` refuses (:class:`PinError`) unless the pin names this module and season, the
file exists, its sha256 equals the pin's (checked BEFORE the pickle is opened: only the approved
bytes are ever unpickled), and the model inside is the pinned version for that season.
:func:`load_backtest` checks each snapshot file's sha256 before reading it and its row count
after; :func:`restore_backtest` writes the snapshot into a (fresh) predictions store and
:func:`evaluation_mismatches` checks it against the committed evaluation. The retrain workflow
(``.github/workflows/retrain.yml``, ``twm model candidate``) produces a candidate model and
backtest snapshot for the owner to review and commit; nothing commits automatically.

The K and D/ST streamer (S2a, :mod:`twm.modules.streamer.production`) pins two more entries,
``streamer_k`` (``model: logit``) and ``streamer_dst`` (``model: rule``: the file is the rule's
JSON definition and the snapshot a ``hit_rates`` table); :func:`read_snapshot` reads any pin's
snapshot files with the same sha256 and row checks. A pin without ``model`` is the Radar's.
Regression Watch (D4a, :mod:`twm.modules.regression_watch.production`) pins
``regression_watch`` (``model: params``: one JSON file of frozen parameters, no snapshot).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

PIN_FILE = "production_models.yaml"
ARTIFACT_DIR = Path("artifacts") / "production_models"
BACKTEST_TABLES = ("predictions", "outcomes", "model_versions")
# Every snapshot file a pin may name (S2a): a rule's frozen backtest is one hit-rate table.
SNAPSHOT_TABLES = (*BACKTEST_TABLES, "hit_rates")
PARQUET_LEVEL = 19  # zstd level of the snapshot files (measured: 0.89 MB for 91,638 rows)
FLOAT_TOLERANCE = 1.01e-6  # the evaluation CSV prints 6 decimals
HEADER = """\
# The owner-approved production models (step E4; src/twm/pins.py, docs/deploy.md
# "The approved model and its backtest"). The scheduled job scores ONLY with the model file
# named here and reads the bands, priorities and time-machine lists ONLY from the backtest
# snapshot named here, after checking every file's sha256. Written by `uv run twm model pin`
# (or copied from the retrain workflow's artifact after review); change it only by committing
# a reviewed model.
"""


class PinError(ValueError):
    """The pinned model or its backtest cannot be used (missing, wrong season, wrong file,
    wrong version, wrong rows)."""


@dataclass(frozen=True)
class SnapshotFile:
    file: str  # relative to the project root
    sha256: str
    rows: int

    def path(self, root: Path | None = None) -> Path:
        return _resolve(self.file, root)

    def as_dict(self) -> dict[str, Any]:
        return {"file": self.file, "sha256": self.sha256, "rows": self.rows}


@dataclass(frozen=True)
class Pin:
    module: str
    season: int
    model_version: str
    file: str  # relative to the project root
    sha256: str
    approved: str = ""  # the date `twm model pin` wrote it (informative)
    backtest_seasons: str = ""  # e.g. "2014-2025"
    backtest: dict[str, SnapshotFile] = field(default_factory=dict)  # table -> file
    # what the file holds when it is not the Radar's pickled model (S2a streamer pins):
    # 'logit' (a pickled model) or 'rule' (a ranking rule's definition, JSON); "" = the Radar's
    model: str = ""

    def path(self, root: Path | None = None) -> Path:
        return _resolve(self.file, root)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "season": self.season, "model_version": self.model_version, "file": self.file,
            "sha256": self.sha256,
        }  # fmt: skip
        if self.model:
            out["model"] = self.model
        if self.approved:
            out["approved"] = self.approved
        if self.backtest:
            out["backtest"] = {
                "seasons": self.backtest_seasons,
                **{t: self.backtest[t].as_dict() for t in SNAPSHOT_TABLES if t in self.backtest},
            }
        return out


def _resolve(file: str, root: Path | None) -> Path:
    from twm.config import ROOT

    p = Path(file)
    return p if p.is_absolute() else (root if root is not None else ROOT) / p


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


# --------------------------------------------------------------------------------------
# The pin file
# --------------------------------------------------------------------------------------

_PIN_KEYS = {"season", "model_version", "file", "sha256", "approved", "backtest", "model"}


def _snapshot(module: str, table: str, entry: Any, where: Path) -> SnapshotFile:
    if not isinstance(entry, dict) or {"file", "sha256", "rows"} - set(entry):
        raise PinError(f"{where}: backtest {table} of {module} needs file, sha256 and rows")
    return SnapshotFile(str(entry["file"]), str(entry["sha256"]).lower(), int(entry["rows"]))


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
        unknown = sorted(set(entry) - _PIN_KEYS)
        if missing or unknown:
            raise PinError(f"{path}: the pin of {module} lacks {missing} or has unknown {unknown}")
        bt = entry.get("backtest") or {}
        if not isinstance(bt, dict) or set(bt) - {"seasons", *SNAPSHOT_TABLES}:
            raise PinError(f"{path}: the backtest of {module} has unknown keys")
        pins[str(module)] = Pin(
            module=str(module), season=int(entry["season"]),
            model_version=str(entry["model_version"]), file=str(entry["file"]),
            sha256=str(entry["sha256"]).lower(), approved=str(entry.get("approved") or ""),
            backtest_seasons=str(bt.get("seasons") or ""),
            backtest={t: _snapshot(str(module), t, bt[t], path) for t in SNAPSHOT_TABLES
                      if t in bt},
            model=str(entry.get("model") or ""),
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


# --------------------------------------------------------------------------------------
# The model file
# --------------------------------------------------------------------------------------


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


# --------------------------------------------------------------------------------------
# The backtest snapshot
# --------------------------------------------------------------------------------------


def backtest_dir(version: str, root: Path | None = None) -> Path:
    from twm.modules.waiver_radar.production import MODULE

    base = root if root is not None else _resolve(".", None)
    return base / ARTIFACT_DIR / MODULE / f"backtest-{version}"


def backtest_frames(store: Path, season: int) -> tuple[dict[str, Any], str]:
    """({table: frame}, seasons) of the store's backtest that a list of ``season`` reads: the
    current versions of the production model and label (:func:`twm.predictions.current_versions`)
    of the evaluation seasons before ``season`` (:func:`confidence.seasons_before`), their
    predictions (walk-forward rows only) and the outcomes of those rows. Opens the store
    read-only. :class:`PinError` when it has none or an outcome is not final."""
    import polars as pl

    from twm import predictions as pr
    from twm.modules.waiver_radar import confidence as cf
    from twm.modules.waiver_radar.production import LABEL, MODEL, MODULE

    if not Path(store).exists():
        raise PinError(f"predictions store not found: {store}; run `uv run twm radar backtest`")
    first, last = cf.seasons_before(season)
    versions = pr.current_versions(store, MODULE, LABEL).filter(
        (pl.col("model") == MODEL) & pl.col("test_season").is_between(first, last)
    )
    if versions.height == 0:
        raise PinError(f"{store} has no {MODEL}/{LABEL} backtest of {first}-{last}")
    ids = versions.get_column("model_version").to_list()
    marks = ", ".join("?" for _ in ids)
    con = pr.connect(store, read_only=True)
    try:
        preds = con.execute(
            f"SELECT * FROM predictions WHERE module = ? AND kind = 'backtest' AND model_version "
            f"IN ({marks}) ORDER BY model_version, season, week, rank_group, rank, entity_id",
            [MODULE, *ids],
        ).pl()
        # the pre-D4a outcome columns only: the Radar's snapshot keeps its exact layout
        cols = ", ".join(f"o.{c}" for c in pr.LEGACY_OUTCOME_COLUMNS)
        outs = con.execute(
            f"SELECT {cols} FROM outcomes o WHERE o.module = ? AND (o.entity_id, o.season, o.week) "
            f"IN (SELECT entity_id, season, week FROM predictions WHERE module = ? AND "
            f"kind = 'backtest' AND model_version IN ({marks})) ORDER BY season, week, entity_id",
            [MODULE, MODULE, *ids],
        ).pl()
        vers = con.execute(
            f"SELECT * FROM model_versions WHERE model_version IN ({marks}) ORDER BY model_version",
            ids,
        ).pl()
    finally:
        con.close()
    open_ = outs.filter((pl.col("label_status") != "final") | pl.col(LABEL).is_null())
    keys = preds.select("entity_id", "season", "week").unique()
    if open_.height or outs.height != keys.height:
        raise PinError(
            f"{store}: {open_.height} backtest outcomes are not final and "
            f"{keys.height - outs.height} have none; rebuild the backtest"
        )
    seen = sorted(int(s) for s in versions.get_column("test_season").to_list())
    span = f"{seen[0]}-{seen[-1]}" if seen[0] != seen[-1] else str(seen[0])
    return {"predictions": preds, "outcomes": outs, "model_versions": vers}, span


def export_backtest(
    store: Path, season: int, version: str, *, root: Path | None = None
) -> tuple[dict[str, SnapshotFile], str]:
    """Write :func:`backtest_frames` to ``backtest-<version>/<table>.parquet`` (zstd) under
    ``root`` (default: the project) and describe the files for the pin."""
    frames, span = backtest_frames(store, season)
    base = root if root is not None else _resolve(".", None)
    out_dir = backtest_dir(version, base)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for table in BACKTEST_TABLES:
        path = out_dir / f"{table}.parquet"
        tmp = path.with_name(path.name + ".tmp")
        frames[table].write_parquet(
            tmp, compression="zstd", compression_level=PARQUET_LEVEL, statistics=False
        )
        tmp.replace(path)
        try:
            rel = path.relative_to(base).as_posix()
        except ValueError:
            rel = str(path)
        files[table] = SnapshotFile(rel, sha256_of(path), frames[table].height)
    return files, span


def read_snapshot(pin: Pin, tables: Sequence[str], root: Path | None = None) -> dict[str, Any]:
    """The pin's snapshot files ``tables`` as frames: each file's sha256 is checked before it
    is read (a changed file is never opened) and its row count after."""
    import polars as pl

    missing = [t for t in tables if t not in pin.backtest]
    if missing:
        raise PinError(f"the pin of {pin.module} names no backtest {', '.join(missing)}")
    frames = {}
    for table in tables:
        snap = pin.backtest[table]
        path = snap.path(root)
        if not path.exists():
            raise PinError(f"the approved backtest file is missing: {snap.file}")
        digest = sha256_of(path)
        if digest != snap.sha256:
            raise PinError(
                f"{snap.file} is not the approved backtest file (sha256 {digest[:12]}..., the "
                f"pin says {snap.sha256[:12]}...); it was not read"
            )
        df = pl.read_parquet(path)
        if df.height != snap.rows:
            raise PinError(f"{snap.file} has {df.height:,} rows, the pin says {snap.rows:,}")
        frames[table] = df
    return frames


def load_backtest(pin: Pin, root: Path | None = None) -> dict[str, Any]:
    """The pin's backtest snapshot as frames ({table: frame}). Each file's sha256 is checked
    before it is read and its row count after; the frames must hang together (every
    prediction's version and outcome present, one production model and label)."""

    from twm.modules.waiver_radar.production import LABEL, MODEL

    if set(pin.backtest) != set(BACKTEST_TABLES):
        raise PinError(
            f"the pin of {pin.module} has no approved backtest snapshot (it needs "
            f"{', '.join(BACKTEST_TABLES)}): approve it again with `uv run twm model pin "
            f"{pin.module} --version {pin.model_version}`"
        )
    frames = read_snapshot(pin, BACKTEST_TABLES, root)
    v, p, o = frames["model_versions"], frames["predictions"], frames["outcomes"]
    kinds = set(v.select("model", "label").unique().iter_rows())
    if kinds != {(MODEL, LABEL)}:
        raise PinError(f"the backtest snapshot holds {sorted(kinds)}, not {MODEL}/{LABEL} only")
    if not set(p.get_column("model_version").unique().to_list()) <= set(
        v.get_column("model_version").to_list()
    ):
        raise PinError("the backtest snapshot has predictions of versions it does not list")
    keys = p.select("entity_id", "season", "week").unique()
    if keys.join(o, on=["entity_id", "season", "week"], how="anti").height:
        raise PinError("the backtest snapshot has predictions without an outcome")
    return frames


def restore_backtest(pin: Pin, store: Path, *, root: Path | None = None) -> dict[str, Any]:
    """Write the pin's backtest snapshot into the predictions store ``store`` (created if
    needed): {'status': 'restored' | 'already there', table: rows}. Refuses when the store
    already holds another backtest of the same model, label and seasons (use a fresh store):
    the snapshot must never mix with a local rebuild."""
    import polars as pl

    from twm import predictions as pr
    from twm.modules.waiver_radar.production import LABEL, MODEL, MODULE

    frames = load_backtest(pin, root)
    mine = frames["model_versions"]
    want = set(mine.get_column("model_version").to_list())
    counts = {t: frames[t].height for t in BACKTEST_TABLES}
    if Path(store).exists():
        seasons = mine.get_column("test_season").to_list()
        cur = pr.current_versions(store, MODULE, LABEL).filter(
            (pl.col("model") == MODEL) & pl.col("test_season").is_in(seasons)
        )
        have = set(cur.get_column("model_version").to_list())
        if have == want:
            con = pr.connect(store, read_only=True)
            try:
                n = con.execute(
                    "SELECT count(*) FROM predictions WHERE model_version IN ("
                    + ", ".join("?" for _ in want)
                    + ")",
                    sorted(want),
                ).fetchone()
            finally:
                con.close()
            if n is not None and int(n[0]) == counts["predictions"]:
                return {"status": "already there", **counts}
        if have:
            raise PinError(
                f"{store} already holds a different {MODEL}/{LABEL} backtest of those seasons "
                f"({len(have - want)} other versions, or other rows): restore the approved one "
                "into a fresh store (--store)"
            )
    pr.write_predictions(
        store, predictions=frames["predictions"], versions=mine,
        outcomes=frames["outcomes"], replace="versions",
    )  # fmt: skip
    return {"status": "restored", **counts}


def evaluation_mismatches(store: Path, csv_path: Path) -> list[str]:
    """Where the store's production-model backtest disagrees with the committed evaluation
    (the published track record): every CSV row of the production model and label that the
    store alone determines (:data:`~twm.modules.waiver_radar.evaluation.STORE_ONLY_SCOPES`,
    subset 'all': precision@10 pooled, by season and by position with their intervals and
    counts, rank buckets, PR-AUC, Brier, calibration) must match, counts exactly and values
    to the CSV's 6 decimals. [] = consistent."""
    import csv

    import polars as pl

    from twm.modules.waiver_radar import evaluation as ev
    from twm.modules.waiver_radar.production import LABEL, MODEL

    if not csv_path.exists():
        return [f"the evaluation report is missing: {csv_path}"]
    with csv_path.open() as f:
        want = {
            tuple(r[c] for c in ev.CSV_COLUMNS[:7]): r
            for r in csv.DictReader(f)
            if r["label"] == LABEL and r["subset"] == "all" and r["model"] == MODEL
            and r["scope"] in ev.STORE_ONLY_SCOPES
        }  # fmt: skip
    rows = ev.load_predictions(store, LABEL).filter(pl.col("model") == MODEL)
    got = {tuple(str(r[c]) for c in ev.CSV_COLUMNS[:7]): r
           for r in ev.store_only_results(rows, LABEL)}  # fmt: skip
    problems = []
    if set(got) != set(want):
        problems.append(
            f"{len(set(want) - set(got))} rows of the evaluation are missing from the backtest "
            f"and {len(set(got) - set(want))} are extra"
        )
    for key in sorted(set(got) & set(want)):
        a, b = got[key], want[key]
        for col in ev.CSV_COLUMNS[7:]:
            x, y = str(a[col]), str(b[col])
            if x == y:
                continue
            try:
                close = abs(float(x) - float(y)) <= FLOAT_TOLERANCE and col in ("value", "lo",
                                                                                "hi")  # fmt: skip
            except ValueError:
                close = False
            if not close:
                problems.append(f"{' '.join(key[3:7])}: {col} {x} in the backtest, {y} in the "
                                "evaluation")  # fmt: skip
    return problems


# --------------------------------------------------------------------------------------
# Approving
# --------------------------------------------------------------------------------------


def approve(
    pm: Any,
    *,
    store: Path,
    root: Path | None = None,
    path: Path | None = None,
    today: datetime | None = None,
) -> Pin:
    """Approve ``pm`` (a production model) together with the backtest of ``store`` (opened
    read-only): write the model to ``artifacts/production_models/<module>/`` with the
    production code's own writer (an existing file that already holds this version is kept
    byte for byte: pickling is not byte-stable), export the backtest snapshot next to it and
    pin both in ``config/production_models.yaml`` (replacing the module's previous pin; other
    modules keep theirs). Review, then commit all of it."""
    from twm.modules.waiver_radar import production as prod

    base = root if root is not None else _resolve(".", None)
    target = prod.model_path(pm.model_version, base / ARTIFACT_DIR / prod.MODULE)
    keep = False
    if target.exists():
        try:
            keep = prod.load_model(target).model_version == pm.model_version
        except prod.ProductionModelError:
            keep = False
    file = target if keep else prod.save_model(pm, target.parent)
    snapshot, span = export_backtest(store, int(pm.season), pm.model_version, root=base)
    pins = read_pins(path)
    day = (today or datetime.now(UTC)).astimezone(UTC).date().isoformat()
    try:
        rel = file.relative_to(base).as_posix()
    except ValueError:
        rel = str(file)
    pin = Pin(prod.MODULE, int(pm.season), pm.model_version, rel, sha256_of(file), day, span,
              snapshot)  # fmt: skip
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
