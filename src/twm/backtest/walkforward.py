"""Walk-forward backtests by season (PROJECT_SPEC 6.2 rules 2, 4, 5 and 6), shared by every module.

**What walk-forward means.** To grade a model on season S we pretend it is the summer before S:
the model may learn only from seasons before S, then it predicts every week of S, and those
predictions are compared with what really happened. Repeating this for every test season
(2014, 2015, ...) gives a track record made of honest, out-of-sample predictions.

For each test season S (:func:`plan_folds`):

- **training rows** = rows of seasons < S only;
- **validation season** V = the last training season (spec 6.2 rule 2). Hyperparameters are
  tuned on a model trained on the seasons before V and scored on V (``tune_metric``); the
  isotonic **calibrator** (which turns a model score into an honest probability) is fit on the
  V scores of the chosen hyperparameters;
- the **final model** is refit with the chosen hyperparameters on ALL seasons < S (V included)
  and the calibrator is applied to its scores. The calibrator was fit on a model trained on one
  season less: a common compromise (the refit model has one more season of data, so its scores
  can be a little sharper than the ones the calibrator saw), accepted because it keeps the
  latest season in the final model;
- a **thin fold** has a single training season (e.g. S = 2014 trained on 2013): there is no
  season left to validate on, so the model uses its default hyperparameters and the calibrator
  is fit on out-of-fold scores from ``n_cal_folds``-fold cross-fitting grouped by week inside
  that season (the weeks are dealt round-robin to the folds; each fold's scores come from a
  model that never saw those weeks). Flagged ``thin`` in the results.

**Guards.** :func:`assert_before` raises :class:`TestSeasonInTrainingError` whenever a frame
used to fit, tune or calibrate holds a row of the test season or later: :func:`fit_fold`
checks the training rows, the tuning rows, the validation rows and the cross-fitting parts
before any model sees them. :func:`twm.registry.check_features` refuses identifiers, labels
and unregistered columns (spec 6.2 rule 5). Only the ``features`` columns ever reach a model.

**Preprocessing** (imputation, scaling, encoding) lives inside each estimator's scikit-learn
``Pipeline`` and is fit in :meth:`Estimator.fit` on the rows passed to it: the training rows of
that fit, never validation or test rows (spec 6.2 rule 4). Test rows are only transformed.

**Determinism.** Rows are sorted by the key columns before every fit; estimators use fixed
seeds; tuning ties go to the higher PR-AUC, then to the earlier grid entry.

**Importance smoke test** (spec 6.2 rule 6): every fold reports the final model's feature
importance as shares of the total; a feature holding more than ``DOMINANCE_SHARE`` of it is
flagged (:attr:`FoldResult.dominant`) for a human to investigate.

No in-season refit (spec default): a model trained before season S scores every week of S.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
import polars as pl
from sklearn.isotonic import IsotonicRegression

from twm.backtest.metrics import pr_auc
from twm.registry import check_features

SEASON = "season"
RAW_SCORE = "raw_score"
SCORE = "score"
DOMINANCE_SHARE = 0.40
N_CAL_FOLDS = 4


class WalkForwardError(ValueError):
    """The walk-forward cannot run as asked (missing columns, no training season ...)."""


class TestSeasonInTrainingError(WalkForwardError):
    """A frame used to fit, tune or calibrate holds a row of the test season or later."""

    __test__ = False  # not a pytest test class despite the name


# --------------------------------------------------------------------------------------
# Folds
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Fold:
    """One test season and the seasons the model may learn from."""

    test_season: int
    train_seasons: tuple[int, ...]  # every season in the data before test_season
    val_season: int | None  # the last training season; None for a thin fold
    tune_seasons: tuple[int, ...]  # training seasons before val_season (fit while tuning)

    @property
    def thin(self) -> bool:
        return self.val_season is None

    def describe(self) -> str:
        span = season_span(self.train_seasons)
        if self.thin:
            return f"test {self.test_season}: train {span} (thin: one season, no validation)"
        return (
            f"test {self.test_season}: train {span}, tune on {season_span(self.tune_seasons)} "
            f"-> validate on {self.val_season}"
        )


def season_span(seasons: Sequence[int]) -> str:
    """'2013-2016' for consecutive seasons, else a comma list; '-' for none."""
    if not seasons:
        return "-"
    s = sorted(seasons)
    if s == list(range(s[0], s[-1] + 1)):
        return str(s[0]) if s[0] == s[-1] else f"{s[0]}-{s[-1]}"
    return ", ".join(str(x) for x in s)


def plan_folds(data_seasons: Iterable[int], test_seasons: Iterable[int]) -> list[Fold]:
    """One :class:`Fold` per test season (sorted): train = data seasons before it, validation
    = the last of those (none when there is only one). WalkForwardError when a test season has
    no data or no earlier season to learn from."""
    have = sorted({int(s) for s in data_seasons})
    folds = []
    for s in sorted({int(x) for x in test_seasons}):
        if s not in have:
            raise WalkForwardError(f"test season {s} has no rows")
        train = tuple(x for x in have if x < s)
        if not train:
            raise WalkForwardError(f"test season {s} has no earlier season to train on")
        if len(train) == 1:
            folds.append(Fold(s, train, None, ()))
        else:
            folds.append(Fold(s, train, train[-1], train[:-1]))
    return folds


def assert_before(frame: pl.DataFrame, test_season: int, *, what: str) -> None:
    """Raise TestSeasonInTrainingError if ``frame`` has any row of ``test_season`` or later.

    Called on every frame a model is fit, tuned or calibrated on: the one rule a backtest must
    never break (spec 6.2 rule 2, spec 13 "the harness must refuse to train on the test
    season")."""
    if SEASON not in frame.columns:
        raise WalkForwardError(f"cannot {what}: the rows have no {SEASON!r} column")
    bad = frame.filter(pl.col(SEASON) >= test_season)
    if bad.height:
        seasons = sorted(bad.get_column(SEASON).unique().to_list())
        raise TestSeasonInTrainingError(
            f"refusing to {what}: {bad.height} row(s) of season(s) {seasons} are not before "
            f"the test season {test_season}. A model graded on season {test_season} may only "
            "learn from earlier seasons (PROJECT_SPEC 6.2 rule 2)."
        )


# --------------------------------------------------------------------------------------
# Estimators (implemented per module, e.g. twm.modules.waiver_radar.models)
# --------------------------------------------------------------------------------------


class FittedModel(Protocol):
    """A trained model: preprocessing + estimator, fit on training rows only."""

    refit_params: dict[str, Any]  # hyperparameters to reuse when refitting on more seasons
    importance_kind: str  # what importance() measures, e.g. "gain"

    def predict(self, x: pl.DataFrame) -> np.ndarray:
        """Uncalibrated score of the positive class, one per row (higher = more likely)."""
        ...

    def importance(self, x: pl.DataFrame) -> dict[str, float]:
        """Importance per input feature (non-negative; any scale); ``x`` = the training rows
        the model was fit on (for measures that need them)."""
        ...


class Estimator(Protocol):
    name: str
    default_params: Mapping[str, Any]  # used when there is no validation season (thin fold)
    fixed_params: Mapping[str, Any]  # settings never tuned (recorded with the model version)

    def grid(self) -> list[dict[str, Any]]:
        """Hyperparameter candidates for tuning (the first is tried first)."""
        ...

    def fit(
        self,
        x: pl.DataFrame,
        y: np.ndarray,
        params: Mapping[str, Any],
        *,
        eval_x: pl.DataFrame | None = None,
        eval_y: np.ndarray | None = None,
    ) -> FittedModel:
        """Fit preprocessing and model on (x, y) only; ``eval_x``/``eval_y`` (the validation
        season) may be used for early stopping, never for fitting preprocessing."""
        ...


TuneMetric = Callable[[pl.DataFrame], float | None]


@dataclass(frozen=True)
class Trial:
    params: dict[str, Any]  # as tried
    refit_params: dict[str, Any]  # as the refit will use them (e.g. with the number of trees)
    metric: float | None  # tune_metric on the validation season
    pr_auc: float | None


@dataclass
class FoldResult:
    fold: Fold
    params: dict[str, Any]  # hyperparameters of the final model
    trials: list[Trial]  # tuning on the validation season (empty for a thin fold)
    calibration: str  # how the calibrator was fit, in words
    n_train: int
    n_train_pos: int
    n_cal: int  # rows the calibrator was fit on
    importance: list[tuple[str, float]]  # (feature, share of the total), largest first
    importance_kind: str
    model: FittedModel = field(repr=False)
    calibrator: IsotonicRegression | None = field(repr=False, default=None)

    @property
    def dominant(self) -> tuple[str, float] | None:
        """The top feature if it holds more than DOMINANCE_SHARE of the importance."""
        if self.importance and self.importance[0][1] > DOMINANCE_SHARE:
            return self.importance[0]
        return None

    def calibrate(self, raw: np.ndarray) -> np.ndarray:
        if self.calibrator is None:
            return np.asarray(raw, dtype=np.float64)
        return np.asarray(self.calibrator.predict(raw), dtype=np.float64)


@dataclass
class WalkForwardResult:
    predictions: pl.DataFrame  # keys + raw_score + score (calibrated), every test row
    folds: list[FoldResult]


# --------------------------------------------------------------------------------------
# The harness
# --------------------------------------------------------------------------------------


def _xy(frame: pl.DataFrame, features: Sequence[str], label: str) -> tuple[pl.DataFrame, Any]:
    y = frame.get_column(label)
    if y.null_count():
        raise WalkForwardError(f"{y.null_count()} training row(s) have no {label!r} label")
    return frame.select(list(features)), y.cast(pl.Int8).to_numpy()


def _fit_isotonic(raw: np.ndarray, y: np.ndarray) -> IsotonicRegression | None:
    if y.size == 0 or int(y.min()) == int(y.max()):
        return None  # one class only: nothing to calibrate against
    iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip", increasing=True)
    iso.fit(np.asarray(raw, dtype=np.float64), y.astype(np.float64))
    return iso


def _shares(imp: Mapping[str, float]) -> list[tuple[str, float]]:
    total = float(sum(max(v, 0.0) for v in imp.values()))
    items = [(k, (max(float(v), 0.0) / total) if total > 0 else 0.0) for k, v in imp.items()]
    return sorted(items, key=lambda kv: (-kv[1], kv[0]))


def _choose(trials: list[Trial]) -> Trial:
    """Best tune_metric; ties -> higher PR-AUC; then the earlier grid entry."""

    def key(it: tuple[int, Trial]) -> tuple[float, float, int]:
        i, t = it
        m = -np.inf if t.metric is None else t.metric
        a = -np.inf if t.pr_auc is None else t.pr_auc
        return (-m, -a, i)

    return min(enumerate(trials), key=key)[1]


def cross_fit_parts(weeks: Iterable[int], n_parts: int) -> list[list[int]]:
    """The weeks of one season dealt round-robin into ``n_parts`` parts (sorted weeks: part k
    gets the k-th, (k+n)-th ... week), so every part spans the whole season."""
    w = sorted({int(x) for x in weeks})
    parts = [w[k::n_parts] for k in range(n_parts)]
    return [p for p in parts if p]


def fit_fold(
    train: pl.DataFrame,
    fold: Fold,
    *,
    estimator: Estimator,
    features: Sequence[str],
    label: str,
    module: str,
    keys: Sequence[str],
    tune_metric: TuneMetric,
    cal_group: str = "week",
    n_cal_folds: int = N_CAL_FOLDS,
) -> FoldResult:
    """Tune, calibrate and fit the final model of one fold from ``train`` (seasons before the
    test season ONLY: anything else is refused)."""
    check_features(features, module)
    test_season = fold.test_season
    assert_before(train, test_season, what=f"train the {estimator.name} model for {test_season}")
    train = train.sort(list(keys))
    x, y = _xy(train, features, label)
    trials: list[Trial] = []
    if fold.thin:
        params = dict(estimator.default_params)
        raw_oof = np.full(train.height, np.nan)
        weeks = train.get_column(cal_group)
        for part in cross_fit_parts(weeks.to_list(), n_cal_folds):
            inside = weeks.is_in(part).to_numpy()
            fit_rows = train.filter(~pl.Series(inside))
            assert_before(fit_rows, test_season, what="cross-fit the calibration model")
            xp, yp = _xy(fit_rows, features, label)
            m = estimator.fit(xp, yp, params)
            raw_oof[inside] = m.predict(x.filter(pl.Series(inside)))
        calibrator = _fit_isotonic(raw_oof, y)
        n_cal = train.height
        calibration = (
            f"isotonic, cross-fitted: {n_cal_folds} parts of {fold.train_seasons[0]} grouped by "
            f"{cal_group} (thin fold)"
        )
        final_params = params
    else:
        assert fold.val_season is not None
        tune = train.filter(pl.col(SEASON).is_in(list(fold.tune_seasons)))
        val = train.filter(pl.col(SEASON) == fold.val_season)
        assert_before(tune, fold.val_season, what="tune hyperparameters")
        assert_before(val, test_season, what="validate")
        xt, yt = _xy(tune, features, label)
        xv, yv = _xy(val, features, label)
        val_scores: list[np.ndarray] = []
        for params in estimator.grid():
            m = estimator.fit(xt, yt, params, eval_x=xv, eval_y=yv)
            s = m.predict(xv)
            val_scores.append(s)
            metric = tune_metric(val.with_columns(pl.Series(RAW_SCORE, s)))
            trials.append(Trial(dict(params), dict(m.refit_params), metric, pr_auc(yv, s)))
        best = _choose(trials)
        calibrator = _fit_isotonic(val_scores[trials.index(best)], yv)
        n_cal = val.height
        calibration = (
            f"isotonic on the {fold.val_season} scores of a model trained on "
            f"{season_span(fold.tune_seasons)}"
        )
        final_params = best.refit_params
    final = estimator.fit(x, y, final_params)
    return FoldResult(
        fold=fold,
        params=dict(final_params),
        trials=trials,
        calibration=calibration if calibrator is not None else "none (one class only)",
        n_train=train.height,
        n_train_pos=int(y.sum()),
        n_cal=n_cal,
        importance=_shares(final.importance(x)),
        importance_kind=final.importance_kind,
        model=final,
        calibrator=calibrator,
    )


def walk_forward(
    rows: pl.DataFrame,
    *,
    estimator: Estimator,
    features: Sequence[str],
    label: str,
    module: str,
    test_seasons: Iterable[int],
    keys: Sequence[str],
    tune_metric: TuneMetric,
    cal_group: str = "week",
    n_cal_folds: int = N_CAL_FOLDS,
) -> WalkForwardResult:
    """Run every fold: returns one prediction per test row (``keys``, ``raw_score`` = the final
    model's score, ``score`` = the calibrated probability) and the per-fold details.

    ``rows`` holds every season (training and test) with ``SEASON``, ``label``, ``features``,
    ``keys`` and ``cal_group`` columns. Test rows' labels are never read."""
    check_features(features, module)
    need = list(dict.fromkeys([SEASON, label, cal_group, *features, *keys]))
    missing = [c for c in need if c not in rows.columns]
    if missing:
        raise WalkForwardError(f"rows lack columns {missing}")
    if SEASON not in keys:
        raise WalkForwardError(f"keys must include {SEASON!r}")
    folds = plan_folds(rows.get_column(SEASON).unique().to_list(), test_seasons)
    preds, results = [], []
    for fold in folds:
        train = rows.filter(pl.col(SEASON).is_in(list(fold.train_seasons)))
        test = rows.filter(pl.col(SEASON) == fold.test_season).sort(list(keys))
        fr = fit_fold(
            train,
            fold,
            estimator=estimator,
            features=features,
            label=label,
            module=module,
            keys=keys,
            tune_metric=tune_metric,
            cal_group=cal_group,
            n_cal_folds=n_cal_folds,
        )
        raw = fr.model.predict(test.select(list(features)))
        preds.append(
            test.select(list(keys)).with_columns(
                pl.Series(RAW_SCORE, raw, dtype=pl.Float64),
                pl.Series(SCORE, fr.calibrate(raw), dtype=pl.Float64),
            )
        )
        results.append(fr)
    return WalkForwardResult(pl.concat(preds, how="vertical"), results)
