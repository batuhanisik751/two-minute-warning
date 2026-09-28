"""The production Waiver Radar model: the walk-forward fold that scores a season live (C6).

**Which model scores season S** (PROJECT_SPEC 6.2 rule 2): exactly the fold the backtest would
use for test season S: the C4 winner (:data:`MODEL`, the logistic regression) for the primary
label (:data:`LABEL`, ``y_hit``), trained on every labelled season before S (2013-2025 for
2026), its settings tuned on S-1 with a model trained on the seasons before that, calibrated
(isotonic) on those S-1 scores, then refit on all seasons before S
(:func:`twm.backtest.walkforward.fit_fold`, the harness the backtest uses, which refuses any
row of S or later). No refit during the season.

**Its version** is the backtest's fingerprint (:func:`twm.predictions.model_version`): the
model, label, features, settings (tuned and fixed), training and test seasons and the content
hash of the training rows. The fitted pipeline, the calibrator and the training means of the
transformed columns (for the reasons) are saved to ``models/waiver_radar/<version>.joblib``
(gitignored) and the version is recorded in the store's ``model_versions`` table.

**Which version is current** (the rule, :func:`current_production`): the store's current
version for (``logit``, ``y_hit``, test season S) in the sense of
:func:`twm.predictions.current_versions`: the one written last. :func:`ensure_production`
reuses it only when its training seasons, training-data hash, features and fixed settings equal
what the dataset and the code give today and its file is present and holds that version;
otherwise it trains a new fold, saves and records it, and the new one (written later) becomes
current. So a change in the training data (a rebuilt dataset) makes a new version; an unchanged
dataset keeps the old one, and the weeks it already scored stay in the store under their
version.

Loading a saved model gives bit-identical scores (tested). The files are pickles: load only
files this code wrote.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import polars as pl
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from twm import predictions as pr
from twm.backtest.walkforward import Fold, fit_fold, production_fold, season_span
from twm.modules.waiver_radar.backtest import _version_record, season_hashes
from twm.modules.waiver_radar.models import (
    FEATURES,
    KEYS,
    MODULE,
    FittedPipeline,
    _as_model_input,
    estimator,
    model_rows,
    tune_metric_for,
)

MODEL = "logit"  # the C4 winner (docs/waiver_radar.md "Results"; ties go to the simpler model)
LABEL = "y_hit"  # the primary label
FILE_FORMAT = 1  # bumped when the saved bundle's layout changes


class ProductionModelError(ValueError):
    """The production model cannot be built or loaded as asked."""


@dataclass
class ProductionModel:
    """A trained production fold: everything needed to score a week and explain the scores."""

    model_version: str
    season: int  # the season it scores (the fold's test season)
    model: str
    label: str
    features: tuple[str, ...]
    fold: Fold
    params: dict[str, Any]  # the final model's settings (tuned + fixed), as hashed
    fitted: FittedPipeline = field(repr=False)
    calibrator: IsotonicRegression | None = field(repr=False)
    # mean of every transformed column over the training rows (the reasons' reference point)
    train_means: np.ndarray = field(repr=False)
    version_row: dict[str, Any] = field(repr=False)
    n_train: int = 0
    n_train_pos: int = 0
    calibration: str = ""

    @property
    def training_seasons(self) -> tuple[int, ...]:
        return self.fold.train_seasons

    def describe(self) -> str:
        return (
            f"{self.model_version}: {self.model} for {self.label}, trained on "
            f"{season_span(self.training_seasons)} ({self.n_train:,} rows, {self.n_train_pos:,} "
            f"hits), tuned and calibrated on {self.fold.val_season}"
        )

    def raw(self, x: pl.DataFrame) -> np.ndarray:
        """The uncalibrated model score (the ranking value)."""
        return self.fitted.predict(x.select(list(self.features)))

    def probability(self, raw: np.ndarray) -> np.ndarray:
        """The calibrated probability of a raw score."""
        if self.calibrator is None:
            return np.asarray(raw, dtype=np.float64)
        return np.asarray(self.calibrator.predict(raw), dtype=np.float64)

    def predict(self, x: pl.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        raw = self.raw(x)
        return raw, self.probability(raw)

    # -- the logistic regression's pieces, for the reasons ---------------------------------

    @property
    def classifier(self) -> LogisticRegression:
        clf = self.fitted.pipeline.named_steps["clf"]
        if not isinstance(clf, LogisticRegression):
            raise ProductionModelError(f"{self.model} is not a logistic regression")
        return clf

    def transform(self, x: pl.DataFrame) -> np.ndarray:
        """The rows as the classifier sees them (imputed, with missing indicators, scaled,
        position one-hot)."""
        prep = self.fitted.pipeline.named_steps["prep"]
        return np.asarray(
            prep.transform(_as_model_input(x.select(list(self.features)))), dtype=np.float64
        )

    def transformed_names(self) -> list[str]:
        return [str(n) for n in self.fitted.pipeline.named_steps["prep"].get_feature_names_out()]


# --------------------------------------------------------------------------------------
# Training
# --------------------------------------------------------------------------------------


def training_rows(dataset: pl.DataFrame, season: int) -> pl.DataFrame:
    """The labelled model rows (in the pool, final label, 2+ window games) of every season
    before ``season``, sorted by key: all the production fold may learn from."""
    return model_rows(dataset).filter(pl.col("season") < season).sort(list(KEYS))


def training_identity(dataset: pl.DataFrame, season: int) -> tuple[tuple[int, ...], str]:
    """(training seasons, training-data hash) of the fold for ``season``, computed exactly as
    the backtest computes a fold's ``dataset_hash`` (without training anything)."""
    rows = training_rows(dataset, season)
    hashes = season_hashes(rows)
    if not hashes:
        raise ProductionModelError(f"no labelled season before {season} in the dataset")
    fold = production_fold(hashes, season)
    return fold.train_seasons, pr.combine_hashes([hashes[s] for s in fold.train_seasons])


def train_production(
    dataset: pl.DataFrame, season: int, *, model: str = MODEL, label: str = LABEL
) -> ProductionModel:
    """Train the fold that scores ``season``: seasons before it only (the harness refuses
    anything else), tuned on the last of them, calibrated as in the backtest."""
    rows = training_rows(dataset, season)
    hashes = season_hashes(rows)
    if not hashes:
        raise ProductionModelError(f"no labelled season before {season} in the dataset")
    fold = production_fold(hashes, season)
    train = rows.filter(pl.col("season").is_in(list(fold.train_seasons)))
    est = estimator(model)
    fr = fit_fold(
        train,
        fold,
        estimator=est,
        features=FEATURES,
        label=label,
        module=MODULE,
        keys=(*KEYS, "position"),
        tune_metric=tune_metric_for(label),
    )
    params = {**fr.params, "fixed": dict(est.fixed_params)}
    dhash = pr.combine_hashes([hashes[s] for s in fold.train_seasons])
    record = _version_record(
        model=model,
        label=label,
        features=FEATURES,
        params=params,
        training=fold.train_seasons,
        test_season=season,
        dataset_hash=dhash,
        notes={
            "kind": "production",
            "validation_season": fold.val_season,
            "thin": fold.thin,
            "calibration": fr.calibration,
            "n_train": fr.n_train,
            "n_train_pos": fr.n_train_pos,
        },
    )
    fitted = fr.model
    assert isinstance(fitted, FittedPipeline)
    x = train.sort(list(KEYS)).select(list(FEATURES))
    means = np.zeros(0)
    if model == "logit":
        prep = fitted.pipeline.named_steps["prep"]
        means = np.asarray(prep.transform(_as_model_input(x)), dtype=np.float64).mean(axis=0)
    return ProductionModel(
        model_version=record["model_version"],
        season=int(season),
        model=model,
        label=label,
        features=tuple(FEATURES),
        fold=fold,
        params=params,
        fitted=fitted,
        calibrator=fr.calibrator,
        train_means=means,
        version_row=record,
        n_train=fr.n_train,
        n_train_pos=fr.n_train_pos,
        calibration=fr.calibration,
    )


# --------------------------------------------------------------------------------------
# Saving and loading
# --------------------------------------------------------------------------------------


def models_dir() -> Path:
    """``models/waiver_radar`` under the project (settings ``paths.models``; gitignored)."""
    from twm.config import settings

    return settings().path("models") / MODULE


def model_path(version: str, root: Path | None = None) -> Path:
    return (root if root is not None else models_dir()) / f"{version}.joblib"


def save_model(pm: ProductionModel, root: Path | None = None) -> Path:
    """Write the model to ``<root>/<version>.joblib`` (atomically: temp file, then rename)."""
    path = model_path(pm.model_version, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    joblib.dump({"format": FILE_FORMAT, "model": pm}, tmp)
    tmp.replace(path)
    return path


def load_model(path: Path) -> ProductionModel:
    """Load a model saved by :func:`save_model` (a pickle: only load files this code wrote)."""
    if not path.exists():
        raise ProductionModelError(f"model file not found: {path}")
    bundle = joblib.load(path)
    if not isinstance(bundle, dict) or bundle.get("format") != FILE_FORMAT:
        raise ProductionModelError(f"{path} is not a model file of this version of the code")
    pm = bundle["model"]
    if not isinstance(pm, ProductionModel):
        raise ProductionModelError(f"{path} does not hold a production model")
    return pm


def version_frame(pm: ProductionModel, *, created_at: Any = None) -> pl.DataFrame:
    """The model's ``model_versions`` row."""
    created = created_at if created_at is not None else pr.now_utc()
    return pl.DataFrame([pm.version_row], schema_overrides={"test_season": pl.Int32}).with_columns(
        pl.lit(pr.code_version()).alias("code_version"),
        pl.lit(created, dtype=pl.Datetime("us")).alias("created_at"),
    )


# --------------------------------------------------------------------------------------
# The current production model
# --------------------------------------------------------------------------------------


def current_production(
    store: Path | str, season: int, *, model: str = MODEL, label: str = LABEL
) -> dict[str, Any] | None:
    """The store's current ``model_versions`` row for (model, label, test season ``season``)
    (:func:`twm.predictions.current_versions`: the one written last), or None."""
    if not Path(store).exists():
        return None
    cur = pr.current_versions(store, MODULE, label).filter(
        (pl.col("model") == model) & (pl.col("test_season") == season)
    )
    return cur.row(0, named=True) if cur.height else None


@dataclass
class Ensured:
    model: ProductionModel
    reused: bool  # True: the current stored version was loaded; False: a new fold was trained
    path: Path
    registered: bool  # a new model_versions row was written


def ensure_production(
    dataset: pl.DataFrame,
    season: int,
    *,
    store: Path | str,
    root: Path | None = None,
    model: str = MODEL,
    label: str = LABEL,
    retrain: bool = False,
    created_at: Any = None,
) -> Ensured:
    """The production model for ``season``: the current stored version when it was trained on
    exactly today's training data (same seasons, same data hash, same features) and its file
    is present; otherwise a newly trained fold, saved and recorded (it becomes current)."""
    seasons, dhash = training_identity(dataset, season)
    cur = None if retrain else current_production(store, season, model=model, label=label)
    if cur is not None:
        same = (
            json.loads(cur["training_seasons"]) == list(seasons)
            and cur["dataset_hash"] == dhash
            and json.loads(cur["feature_list"]) == sorted(FEATURES)
            and json.loads(cur["params"]).get("fixed")
            == json.loads(json.dumps(dict(estimator(model).fixed_params)))
        )
        path = model_path(cur["model_version"], root)
        if same and path.exists():
            pm = load_model(path)
            if pm.model_version != cur["model_version"]:
                raise ProductionModelError(
                    f"{path} holds {pm.model_version}, not {cur['model_version']}"
                )
            return Ensured(pm, True, path, False)
    pm = train_production(dataset, season, model=model, label=label)
    path = save_model(pm, root)
    registered = pr.register_version(store, version_frame(pm, created_at=created_at))
    return Ensured(pm, False, path, registered)
