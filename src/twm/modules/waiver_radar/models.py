"""Waiver Radar models (C4): three baselines, a regularized logistic regression and LightGBM.

**Rows.** A model learns from, and is graded on, pool rows whose label is known and whose
window has at least 2 games: ``in_pool AND train_eligible AND label_status = 'final'``
(:func:`model_rows`). Labels: ``y_hit`` (primary) and ``y_sustained``.

**Features.** Exactly :data:`FEATURES` (the 49 registered C3 features, checked with
:func:`twm.registry.check_features`); ``position`` is categorical. No identifiers.

**Baselines** (PROJECT_SPEC 8.1) produce a ranking score only, no probability:

- ``baseline_last_points``: last game's fantasy points (``fantasy_points_last``);
- ``baseline_snap_delta``: the snap-share change (``snap_share_delta``; NULL at a team's first
  game, so week-1 as-ofs rank by id only);
- ``baseline_ecr``: the FantasyPros positional rank visible at the as-of (``ecr_pos_rank``, a
  metric column, lower is better), graded only where ``ecr_available`` (2020 on).

A row without a baseline value is ranked below every row with one.

**Models** give calibrated probabilities (the walk-forward harness calibrates them):

- ``logit``: median imputation + a missing indicator per column, standardization, one-hot
  position (standardized too), then an L1- or L2-penalized logistic regression (liblinear,
  with an effectively unpenalized intercept); C and the penalty are tuned on the validation
  season. Importance = the spread (standard deviation over the training rows) of each
  feature's contribution to the log-odds.
- ``lgbm``: LightGBM (position as a categorical column, missing values handled natively); a
  small grid of num_leaves x min_child_samples x feature_fraction at learning rate 0.05, the
  number of trees chosen by early stopping on the validation season. Deterministic
  (``deterministic=True``, fixed seed and thread count, column-wise histograms).

**Ranks**: within each (as-of, position), highest score first; ties go to the higher
uncalibrated model score (isotonic calibration maps nearby scores to the same probability,
and the model's own order is kept), then to the smaller ``gsis_id``.
"""

from __future__ import annotations

import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import lightgbm as lgb
import numpy as np
import polars as pl
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler

from twm.backtest.metrics import add_rank, pooled_precision, precision_at_k
from twm.backtest.walkforward import RAW_SCORE, SCORE
from twm.modules.waiver_radar.features import CATEGORICAL_FEATURES, FEATURE_COLUMNS

MODULE = "waiver_radar"
FEATURES: tuple[str, ...] = FEATURE_COLUMNS
LABELS = ("y_hit", "y_sustained")
ID = "gsis_id"
GROUP = ("season", "week", "position")  # one ranked list per as-of and position
KEYS = ("season", "week", "gsis_id")
K = 10
SEED = 20140907  # any fixed number; every random choice below derives from it
LGBM_THREADS = 4  # fixed, so results do not depend on the machine's core count
INTERCEPT_SCALING = 100.0


@dataclass(frozen=True)
class Baseline:
    name: str
    column: str
    higher_is_better: bool
    needs: str | None = None  # a boolean column: grade only the groups where it is true
    description: str = ""


BASELINES: dict[str, Baseline] = {
    b.name: b
    for b in (
        Baseline("baseline_last_points", "fantasy_points_last", True,
                 description="last game's fantasy points"),
        Baseline("baseline_snap_delta", "snap_share_delta", True,
                 description="snap-share change (last game vs the 3 before)"),
        Baseline("baseline_ecr", "ecr_pos_rank", False, needs="ecr_available",
                 description="FantasyPros positional rank at the as-of (2020 on)"),
    )
}  # fmt: skip
NAIVE_BASELINES = ("baseline_last_points", "baseline_snap_delta")  # the acceptance test's pair
MODELS = ("logit", "lgbm")
ALL_MODELS = (*BASELINES, *MODELS)


def model_rows(dataset: pl.DataFrame) -> pl.DataFrame:
    """The rows models learn from and are graded on: in the pool, final label, 2+ window
    games."""
    return dataset.filter(
        pl.col("in_pool") & pl.col("train_eligible") & (pl.col("label_status") == "final")
    )


def _numeric(features: Sequence[str]) -> list[str]:
    return [c for c in features if c not in CATEGORICAL_FEATURES]


def _categorical(features: Sequence[str]) -> list[str]:
    return [c for c in features if c in CATEGORICAL_FEATURES]


def _as_model_input(x: pl.DataFrame) -> pl.DataFrame:
    """Numbers and booleans as Float64 (NULL -> NaN for scikit-learn), categories as text."""
    return x.with_columns(
        [pl.col(c).cast(pl.Float64) for c in x.columns if c not in CATEGORICAL_FEATURES]
    )


# --------------------------------------------------------------------------------------
# Logistic regression
# --------------------------------------------------------------------------------------


@dataclass
class FittedPipeline:
    pipeline: Pipeline
    refit_params: dict[str, Any]
    importance_kind: str
    features: tuple[str, ...]

    def predict(self, x: pl.DataFrame) -> np.ndarray:
        x = _as_model_input(x.select(list(self.features)))
        with warnings.catch_warnings():
            # LightGBM keeps the feature names it was given; the pipeline hands it the
            # preprocessed array (same columns, same order), which scikit-learn warns about
            warnings.filterwarnings("ignore", message="X does not have valid feature names")
            return np.asarray(self.pipeline.predict_proba(x)[:, 1], dtype=np.float64)

    def importance(self, x: pl.DataFrame) -> dict[str, float]:
        """LightGBM: total gain per feature. Logistic regression: the standard deviation, over
        the training rows ``x``, of the feature's contribution to the log-odds (its value's,
        its missing indicator's and, for position, its one-hot columns' terms added up)."""
        model = self.pipeline.steps[-1][1]
        if isinstance(model, LogisticRegression):
            return _logit_importance(self.pipeline, self.features, x)
        gains = model.booster_.feature_importance(importance_type="gain")
        names = model.booster_.feature_name()
        return {n: float(g) for n, g in zip(names, gains, strict=True)}


def _base_feature(name: str, features: Sequence[str]) -> str:
    """The input feature behind a transformed column ('num__missingindicator_x' -> 'x')."""
    part, col = name.split("__", 1)
    if part == "cat":
        return next(f for f in _categorical(features) if col.startswith(f + "_"))
    return col.removeprefix("missingindicator_")


def _logit_importance(
    pipeline: Pipeline, features: Sequence[str], x: pl.DataFrame
) -> dict[str, float]:
    prep: ColumnTransformer = pipeline.named_steps["prep"]
    coefs = pipeline.named_steps["clf"].coef_[0]
    z = prep.transform(_as_model_input(x.select(list(features))))
    groups: dict[str, list[int]] = {f: [] for f in features}
    for i, name in enumerate(prep.get_feature_names_out()):
        groups[_base_feature(name, features)].append(i)
    return {f: float(np.std(z[:, idx] @ coefs[idx])) if idx else 0.0 for f, idx in groups.items()}


class LogitEstimator:
    """L1/L2-penalized logistic regression behind imputation, scaling and one-hot position."""

    name = "logit"
    default_params: Mapping[str, Any] = {"C": 0.1, "l1_ratio": 0.0}
    # everything else that shapes the model (recorded in model_versions and hashed)
    fixed_params: Mapping[str, Any] = {
        "impute": "median + missing indicators", "scale": "standard",
        "position": "one-hot, standardized", "solver": "liblinear",
        "intercept_scaling": INTERCEPT_SCALING, "max_iter": 2000, "random_state": SEED,
        "calibration": "isotonic",
    }  # fmt: skip

    def grid(self) -> list[dict[str, Any]]:
        # l1_ratio 0 = L2 (ridge), 1 = L1 (lasso). L1 with a large C is slow and rarely wins.
        return [
            {"C": 0.01, "l1_ratio": 0.0},
            {"C": 0.1, "l1_ratio": 0.0},
            {"C": 1.0, "l1_ratio": 0.0},
            {"C": 0.01, "l1_ratio": 1.0},
            {"C": 0.1, "l1_ratio": 1.0},
        ]

    def pipeline(self, features: Sequence[str], params: Mapping[str, Any]) -> Pipeline:
        numeric = Pipeline(
            [
                ("impute", SimpleImputer(strategy="median", add_indicator=True)),
                ("scale", StandardScaler()),
            ]
        )
        categorical = Pipeline(
            [
                ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
                ("scale", StandardScaler()),
            ]
        )
        prep = ColumnTransformer(
            [
                ("num", numeric, _numeric(features)),
                ("cat", categorical, _categorical(features)),
            ],
            sparse_threshold=0.0,
        )
        # liblinear penalizes the intercept like any weight; a large intercept_scaling makes
        # that penalty negligible, so the base rate sits in the intercept (every input is
        # standardized, so no column can stand in for it)
        clf = LogisticRegression(
            C=float(params["C"]),
            l1_ratio=float(params["l1_ratio"]),
            solver="liblinear",
            intercept_scaling=INTERCEPT_SCALING,
            max_iter=2000,
            random_state=SEED,
        )
        return Pipeline([("prep", prep), ("clf", clf)])

    def fit(
        self,
        x: pl.DataFrame,
        y: np.ndarray,
        params: Mapping[str, Any],
        *,
        eval_x: pl.DataFrame | None = None,
        eval_y: np.ndarray | None = None,
    ) -> FittedPipeline:
        features = tuple(x.columns)
        pipe = self.pipeline(features, params)
        pipe.fit(_as_model_input(x), y)  # preprocessing sees the training rows only
        return FittedPipeline(pipe, dict(params), "log-odds contribution (std)", features)


# --------------------------------------------------------------------------------------
# LightGBM
# --------------------------------------------------------------------------------------

LGBM_FIXED: dict[str, Any] = {
    "objective": "binary",
    "reg_lambda": 1.0,
    "subsample": 1.0,
    "deterministic": True,
    "force_col_wise": True,
    "random_state": SEED,
    "n_jobs": LGBM_THREADS,
    "verbose": -1,
}
MAX_TREES = 2000
EARLY_STOPPING_ROUNDS = 50


class LgbmEstimator:
    """LightGBM behind an ordinal encoding of position (a LightGBM categorical column)."""

    name = "lgbm"
    default_params: Mapping[str, Any] = {
        "num_leaves": 15,
        "min_child_samples": 100,
        "colsample_bytree": 0.8,
        "learning_rate": 0.05,
        "n_estimators": 300,
    }
    fixed_params: Mapping[str, Any] = {
        **LGBM_FIXED, "position": "ordinal, categorical", "max_trees": MAX_TREES,
        "early_stopping_rounds": EARLY_STOPPING_ROUNDS, "calibration": "isotonic",
    }  # fmt: skip

    def grid(self) -> list[dict[str, Any]]:
        return [
            {"num_leaves": nl, "min_child_samples": mcs, "colsample_bytree": ff,
             "learning_rate": 0.05, "n_estimators": MAX_TREES}
            for nl in (15, 31) for mcs in (50, 200) for ff in (0.7, 1.0)
        ]  # fmt: skip

    def _prep(self, features: Sequence[str]) -> ColumnTransformer:
        return ColumnTransformer(
            [
                (
                    "cat",
                    OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1),
                    _categorical(features),
                ),
                ("num", "passthrough", _numeric(features)),
            ]
        )

    def fit(
        self,
        x: pl.DataFrame,
        y: np.ndarray,
        params: Mapping[str, Any],
        *,
        eval_x: pl.DataFrame | None = None,
        eval_y: np.ndarray | None = None,
    ) -> FittedPipeline:
        features = tuple(x.columns)
        cats, nums = _categorical(features), _numeric(features)
        prep = self._prep(features)
        xt = prep.fit_transform(_as_model_input(x))  # fit on the training rows only
        model = lgb.LGBMClassifier(**{**LGBM_FIXED, **params})
        fit_kw: dict[str, Any] = {
            "categorical_feature": list(range(len(cats))),
            "feature_name": [*cats, *nums],
        }
        if eval_x is not None and eval_y is not None:
            fit_kw["eval_X"] = (prep.transform(_as_model_input(eval_x)),)
            fit_kw["eval_y"] = (eval_y,)
            fit_kw["callbacks"] = [
                lgb.early_stopping(EARLY_STOPPING_ROUNDS, first_metric_only=True, verbose=False)
            ]
        model.fit(xt, y, **fit_kw)
        refit = dict(params)
        if eval_x is not None and model.best_iteration_:
            refit["n_estimators"] = int(model.best_iteration_)
        return FittedPipeline(Pipeline([("prep", prep), ("clf", model)]), refit, "gain", features)


ESTIMATORS = {"logit": LogitEstimator, "lgbm": LgbmEstimator}


def estimator(name: str) -> LogitEstimator | LgbmEstimator:
    try:
        return ESTIMATORS[name]()
    except KeyError:
        raise ValueError(f"unknown model {name!r}; models: {MODELS}") from None


# --------------------------------------------------------------------------------------
# Scores, ranks and the tuning metric
# --------------------------------------------------------------------------------------


def baseline_scores(rows: pl.DataFrame, name: str) -> pl.DataFrame:
    """KEYS + position + raw_score/score (the ranking score; higher = better) for a baseline.
    Rows outside the baseline's coverage (``needs`` false) are left out."""
    b = BASELINES[name]
    if b.needs is not None:
        rows = rows.filter(pl.col(b.needs).fill_null(False))
    value = pl.col(b.column).cast(pl.Float64)
    score = value if b.higher_is_better else -value
    return rows.select(*KEYS, "position", score.alias(RAW_SCORE), score.alias(SCORE))


def rank_scores(scored: pl.DataFrame) -> pl.DataFrame:
    """Rank within (as-of, position): score desc, raw_score desc, gsis_id asc; no score last."""
    return add_rank(scored, group=GROUP, by=[(SCORE, True), (RAW_SCORE, True)], id_col=ID)


def precision_table(scored: pl.DataFrame, label: str, k: int = K) -> pl.DataFrame:
    """Per (as-of, position) precision@k of scored rows (with ``label``)."""
    return precision_at_k(rank_scores(scored), group=GROUP, label=label, k=k)


def tune_metric_for(label: str):
    """The tuning metric: pooled precision@10 over the validation season's (as-of, position)
    groups, ranking by the uncalibrated score."""

    def metric(val: pl.DataFrame) -> float | None:
        scored = val.with_columns(pl.col(RAW_SCORE).alias(SCORE))
        return pooled_precision(precision_table(scored, label)).value

    return metric
