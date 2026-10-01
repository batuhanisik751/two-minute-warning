"""The app's own win-probability (WP) model, trained walk-forward (PROJECT_SPEC 8.4 item 1, G1).

**What it predicts.** For any play state (score difference, clock, down, distance, field
position, timeouts, who gets the ball after halftime, home, the pregame spread, era), the
chance that the team with the ball wins the game. G2/G3 ask it about hypothetical states
(after a made field goal, a failed fourth down, a punt ...) through :func:`wp`.

**Model.** LightGBM (binary log loss) on :data:`twm.modules.decisions.wp_data.FEATURES` with
monotone constraints where football logic is unambiguous (:data:`MONOTONE`: a bigger lead, a
better spread, more own timeouts, fewer yards to go, fewer yards to the end zone, an earlier
down and getting the ball after halftime never lower the offense's WP; more defensive timeouts
never raise it). Single-threaded and ``deterministic=True`` (two fits are bit-identical).

**Walk-forward** (the shared harness, :func:`twm.backtest.walkforward.fit_fold`): for test
season S the model learns from seasons < S only (the harness refuses anything else), its
settings are tuned on S-1 with a model trained on the seasons before that (early stopping on
S-1, :data:`GRID`, metric = log loss), then it is refit on every season < S. The harness also
fits an isotonic calibrator on the S-1 scores; it is KEPT only when it helps on validation:
fit on half the S-1 games and scored on the other half (both ways), its log loss must beat the
raw model's on S-1 (:func:`isotonic_helps`).
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import polars as pl

from twm.backtest.walkforward import RAW_SCORE, fit_fold, plan_folds
from twm.modules.decisions.wp_data import FEATURES, KEYS, LABEL, time_interactions
from twm.predictions import model_version

MODULE = "decisions"
MODEL = "wp_lgbm"
SEED = 20060907  # any fixed number
MONOTONE: dict[str, int] = {
    "score_differential": 1,
    "diff_time_ratio": 1,
    "posteam_spread": 1,
    "spread_time": 1,
    "posteam_timeouts_remaining": 1,
    "defteam_timeouts_remaining": -1,
    "ydstogo": -1,
    "yardline_100": -1,
    "down": -1,
    "receives_2h_kickoff": 1,
}
LGBM_FIXED: dict[str, Any] = {
    "objective": "binary",
    "learning_rate": 0.05,
    "subsample": 1.0,
    "colsample_bytree": 1.0,
    "reg_lambda": 1.0,
    "deterministic": True,
    "force_col_wise": True,
    "n_jobs": 1,  # single-threaded: the S1d lesson (thread races changed early stopping)
    "random_state": SEED,
    "verbose": -1,
}
MAX_TREES = 3000
EARLY_STOPPING_ROUNDS = 50
GRID: tuple[dict[str, Any], ...] = tuple(
    {"num_leaves": nl, "min_child_samples": mcs, "n_estimators": MAX_TREES}
    for nl in (31, 63)
    for mcs in (200, 1000)
)
DEFAULT_PARAMS: dict[str, Any] = {"num_leaves": 31, "min_child_samples": 1000, "n_estimators": 300}


def _matrix(x: pl.DataFrame, features: Sequence[str]) -> np.ndarray:
    return x.select([pl.col(c).cast(pl.Float64) for c in features]).to_numpy()


class WpFitted:
    """A fitted LightGBM WP model (the harness's ``FittedModel``): ``predict`` = the raw
    probability that the possession team wins."""

    importance_kind = "gain"

    def __init__(self, clf: lgb.LGBMClassifier, features: Sequence[str], refit: dict[str, Any]):
        self.clf = clf
        self.features = tuple(features)
        self.refit_params = refit

    def predict(self, x: pl.DataFrame) -> np.ndarray:
        # the booster directly (= predict_proba's positive column, without sklearn's
        # feature-name warning); early-stopped models use their best iteration
        best = self.clf.best_iteration_ or None
        raw = self.clf.booster_.predict(_matrix(x, self.features), num_iteration=best)
        return np.asarray(raw, dtype=np.float64)

    def importance(self, x: pl.DataFrame) -> dict[str, float]:
        gain = self.clf.booster_.feature_importance(importance_type="gain")
        return {f: float(g) for f, g in zip(self.features, gain, strict=True)}

    @property
    def n_trees(self) -> int:
        return int(self.clf.booster_.num_trees())


class WpEstimator:
    """LightGBM with monotone constraints (the harness's ``Estimator``)."""

    name = MODEL
    default_params: Mapping[str, Any] = DEFAULT_PARAMS
    fixed_params: Mapping[str, Any] = {
        **LGBM_FIXED, "monotone": dict(MONOTONE), "max_trees": MAX_TREES,
        "early_stopping_rounds": EARLY_STOPPING_ROUNDS, "metric": "log loss",
    }  # fmt: skip

    def grid(self) -> list[dict[str, Any]]:
        return [dict(g) for g in GRID]

    def fit(
        self,
        x: pl.DataFrame,
        y: np.ndarray,
        params: Mapping[str, Any],
        *,
        eval_x: pl.DataFrame | None = None,
        eval_y: np.ndarray | None = None,
    ) -> WpFitted:
        features = tuple(x.columns)
        mono = [MONOTONE.get(f, 0) for f in features]
        clf = lgb.LGBMClassifier(**{**LGBM_FIXED, **params}, monotone_constraints=mono)
        kw: dict[str, Any] = {"feature_name": list(features)}
        if eval_x is not None and eval_y is not None:
            kw["eval_X"] = (_matrix(eval_x, features),)
            kw["eval_y"] = (eval_y,)
            kw["callbacks"] = [lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False)]
        clf.fit(_matrix(x, features), y, **kw)
        refit = dict(params)
        if eval_x is not None and clf.best_iteration_:
            refit["n_estimators"] = int(clf.best_iteration_)
        return WpFitted(clf, features, refit)


def log_loss(y: np.ndarray, p: np.ndarray, eps: float = 1e-12) -> float:
    """Mean binary log loss (natural log); probabilities clipped to [eps, 1 - eps]."""
    y = np.asarray(y, dtype=np.float64)
    q = np.clip(np.asarray(p, dtype=np.float64), eps, 1.0 - eps)
    return float(-np.mean(y * np.log(q) + (1.0 - y) * np.log(1.0 - q)))


# --------------------------------------------------------------------------------------
# Calibration: isotonic only when it helps on validation
# --------------------------------------------------------------------------------------


def isotonic_helps(
    game_id: Sequence[str], y: np.ndarray, raw: np.ndarray
) -> tuple[bool, float, float]:
    """Does an isotonic calibrator improve the validation season's log loss out of sample?

    The validation games (sorted ids) are dealt alternately into two halves; a calibrator fit
    on one half scores the other (both ways). Returns (helps, raw log loss, cross-fitted
    isotonic log loss); helps = the isotonic log loss is lower."""
    from sklearn.isotonic import IsotonicRegression

    ids = np.asarray(game_id)
    games = np.unique(ids)  # sorted
    half = np.isin(ids, games[::2])
    y = np.asarray(y, dtype=np.float64)
    raw = np.asarray(raw, dtype=np.float64)
    cross = np.empty_like(raw)
    for fit_on in (half, ~half):
        iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
        iso.fit(raw[fit_on], y[fit_on])
        cross[~fit_on] = iso.predict(raw[~fit_on])
    raw_ll, iso_ll = log_loss(y, raw), log_loss(y, cross)
    return iso_ll < raw_ll, raw_ll, iso_ll


# --------------------------------------------------------------------------------------
# The saved model and wp()
# --------------------------------------------------------------------------------------


class WpModelError(ValueError):
    """A WP model file is missing, of another format, or a state frame cannot be scored."""


@dataclass
class WpModel:
    """One fold's final model, as saved under ``models/decisions/<version>.joblib``."""

    version: str
    test_season: int
    train_seasons: tuple[int, ...]
    val_season: int | None
    features: tuple[str, ...]
    params: dict[str, Any]
    fitted: WpFitted = field(repr=False)
    calibrator: Any = field(repr=False, default=None)  # IsotonicRegression or None
    calibration: str = "none"
    dataset_hash: str = ""

    def raw(self, x: pl.DataFrame) -> np.ndarray:
        return self.fitted.predict(x)

    def probability(self, x: pl.DataFrame) -> np.ndarray:
        raw = self.raw(x)
        if self.calibrator is None:
            return raw
        return np.asarray(self.calibrator.predict(raw), dtype=np.float64)


def complete_states(states: pl.DataFrame) -> pl.DataFrame:
    """Add the features that follow from others when they are missing: the era flags from
    ``season`` and ``spread_time`` / ``diff_time_ratio`` (:func:`wp_data.time_interactions`).
    Everything else must be given (a hypothetical state is explicit)."""
    s = states
    if "season" in s.columns:
        if "era_pat_2015" not in s.columns:
            s = s.with_columns((pl.col("season") >= 2015).cast(pl.Int8).alias("era_pat_2015"))
        if "era_kickoff_2023" not in s.columns:
            s = s.with_columns((pl.col("season") >= 2023).cast(pl.Int8).alias("era_kickoff_2023"))
    need = ("posteam_spread", "score_differential", "game_seconds_remaining", "half_number")
    if all(c in s.columns for c in need):
        add = [e for e in time_interactions() if e.meta.output_name() not in s.columns]
        if add:
            s = s.with_columns(add)
    return s


def wp(states: pl.DataFrame, model: WpModel) -> np.ndarray:
    """Win probability of the team with the ball, one per row of ``states`` (real or
    hypothetical: e.g. the opponent's 1st and 10 at its 25 after a made field goal; the kicking
    team's WP is then 1 minus it). ``states`` holds every model feature, or the inputs of
    :func:`complete_states`. Refuses missing columns, NULLs and out-of-range values."""
    s = complete_states(states)
    missing = [c for c in model.features if c not in s.columns]
    if missing:
        raise WpModelError(f"states lack columns {missing}")
    x = s.select(model.features)
    nulls = [c for c in model.features if x.get_column(c).null_count()]
    if nulls:
        raise WpModelError(f"states have NULLs in {nulls}")
    bad = x.filter(~_in_range()).height
    if bad:
        raise WpModelError(f"{bad} state(s) are out of range (down 1-4, distance >= 1, "
                           "yardline 1-99, timeouts 0-3, clock >= 0, half 1-3)")  # fmt: skip
    return model.probability(x)


def _in_range() -> pl.Expr:
    return (
        pl.col("down").is_between(1, 4)
        & (pl.col("ydstogo") >= 1)
        & pl.col("yardline_100").is_between(1, 99)
        & pl.col("posteam_timeouts_remaining").is_between(0, 3)
        & pl.col("defteam_timeouts_remaining").is_between(0, 3)
        & (pl.col("game_seconds_remaining") >= 0)
        & (pl.col("half_seconds_remaining") >= 0)
        & pl.col("half_number").is_between(1, 3)
    )


# --------------------------------------------------------------------------------------
# One walk-forward fold
# --------------------------------------------------------------------------------------

ERA = ("era_pat_2015", "era_kickoff_2023")


@dataclass
class FoldOutput:
    model: WpModel
    predictions: pl.DataFrame  # KEYS + raw + prob, every test play
    summary: dict[str, Any]


def _training_hash(train: pl.DataFrame) -> str:
    from twm.predictions import combine_hashes, frame_hash

    cols = [*KEYS, *FEATURES, LABEL]
    parts = train.select(cols).sort(list(KEYS)).partition_by("season", maintain_order=True)
    return combine_hashes([frame_hash(p) for p in parts])


def run_fold(rows: pl.DataFrame, test_season: int, *, progress=print) -> FoldOutput:
    """Tune, calibrate (if it helps), refit and score one test season; ``rows`` = model rows
    of every season (:func:`wp_data.build_states`). Only seasons < ``test_season`` are learned
    from (the harness refuses anything else)."""
    t0 = time.perf_counter()
    fold = plan_folds(rows.get_column("season").unique().to_list(), [test_season])[0]
    train = rows.filter(pl.col("season").is_in(list(fold.train_seasons))).sort(list(KEYS))
    test = rows.filter(pl.col("season") == test_season).sort(list(KEYS))
    val_scores: list[np.ndarray] = []

    def tune_metric(val: pl.DataFrame) -> float:
        s = val.get_column(RAW_SCORE).to_numpy()
        val_scores.append(s)
        ll = log_loss(val.get_column(LABEL).to_numpy(), s)
        progress(f"  {test_season}: trial {len(val_scores)}/{len(GRID)} validation log loss "
                 f"{ll:.4f} [{time.perf_counter() - t0:.0f} s]")  # fmt: skip
        return -ll

    progress(f"{fold.describe()}: {train.height:,} training plays, {test.height:,} test plays")
    fr = fit_fold(train, fold, estimator=WpEstimator(), features=FEATURES, label=LABEL,
                  module=MODULE, keys=KEYS, tune_metric=tune_metric)  # fmt: skip
    summary: dict[str, Any] = {"test_season": test_season, "fold": fold.describe()}
    calibrator, calibration = None, "none (thin fold: no validation season)"
    if not fold.thin:
        best = next(i for i, t in enumerate(fr.trials) if t.refit_params == fr.params)
        val = train.filter(pl.col("season") == fold.val_season)
        yv = val.get_column(LABEL).to_numpy()
        helps, raw_ll, iso_ll = isotonic_helps(val.get_column("game_id").to_list(), yv,
                                               val_scores[best])  # fmt: skip
        calibrator = fr.calibrator if helps else None
        calibration = (
            f"{'isotonic' if helps else 'none'} (validation {fold.val_season}: raw log loss "
            f"{raw_ll:.5f}, cross-fitted isotonic {iso_ll:.5f})"
        )
        summary.update(val_logloss=raw_ll, val_logloss_isotonic=iso_ll, best_trial=best + 1)
        summary["era_ablation"] = _era_ablation(train, fold, fr.params, yv, raw_ll)
    test_raw = fr.model.predict(test)
    model = WpModel(
        version="", test_season=test_season, train_seasons=fold.train_seasons,
        val_season=fold.val_season, features=tuple(FEATURES), params=dict(fr.params),
        fitted=fr.model, calibrator=calibrator, calibration=calibration,
        dataset_hash=_training_hash(train),
    )  # fmt: skip
    model.version = model_version(
        module=MODULE, model=MODEL, label=LABEL, features=FEATURES,
        params={**WpEstimator.fixed_params, **fr.params, "calibration": calibration.split()[0]},
        training_seasons=fold.train_seasons, test_season=test_season,
        dataset_hash=model.dataset_hash,
    )  # fmt: skip
    preds = test.select(KEYS).with_columns(
        pl.Series("raw", test_raw), pl.Series("prob", model.probability(test.select(FEATURES)))
    )
    summary.update(
        version=model.version, calibration=calibration, params=dict(fr.params),
        n_train=fr.n_train, n_train_pos=fr.n_train_pos, n_test=test.height,
        dataset_hash=model.dataset_hash, format=FILE_FORMAT,
        trials=[{"params": t.params, "n_trees": t.refit_params.get("n_estimators"),
                 "val_logloss": -t.metric if t.metric is not None else None} for t in fr.trials],
        importance=[[f, round(v, 4)] for f, v in fr.importance],
        dominant=fr.dominant[0] if fr.dominant else None,
        seconds=round(time.perf_counter() - t0, 1),
    )  # fmt: skip
    progress(f"  {test_season}: done in {summary['seconds']:.0f} s, {calibration}")
    return FoldOutput(model, preds, summary)


def _era_ablation(
    train: pl.DataFrame, fold: Any, params: Mapping[str, Any], yv: np.ndarray, val_ll: float
) -> dict[str, Any] | None:
    """Do the era flags matter? The tuning model refit without them (same settings and number
    of trees) and scored on the validation season. None when the flags are constant in the
    tuning seasons (a tree cannot split on them, so they cannot matter there)."""
    tune = train.filter(pl.col("season").is_in(list(fold.tune_seasons)))
    varying = [c for c in ERA if tune.get_column(c).n_unique() > 1]
    if not varying:
        return None
    feats = [f for f in FEATURES if f not in ERA]
    m = WpEstimator().fit(tune.select(feats), tune.get_column(LABEL).to_numpy(), params)
    val = train.filter(pl.col("season") == fold.val_season)
    without = log_loss(yv, m.predict(val.select(feats)))
    return {"varying": varying, "val_logloss_with": val_ll, "val_logloss_without": without}


# --------------------------------------------------------------------------------------
# Saving and loading (the Radar's pattern: joblib pickles written only by this code)
# --------------------------------------------------------------------------------------

FILE_FORMAT = 1  # bumped when the saved bundle's layout changes
TEST_SEASONS = tuple(range(2006, 2026))
FIRST_SEASON = 1999  # the first season in the warehouse: 2006 is trained on 1999-2005


def fold_seasons() -> tuple[int, ...]:
    """Seasons a fold may be fitted for: the backtest's :data:`TEST_SEASONS` plus the current
    season (settings ``current_season``), whose fold learns from every completed season and
    scores the season in progress (G3 grades it). Reports cover TEST_SEASONS only."""
    from twm.config import settings

    return tuple(sorted({*TEST_SEASONS, int(settings().current_season)}))


def models_dir() -> Path:
    """``models/decisions`` (settings ``paths.models``; gitignored)."""
    from twm.config import settings

    return settings().path("models") / MODULE


def backtest_dir() -> Path:
    """``data/decisions/wp_backtest``: per-fold predictions and summaries (gitignored)."""
    from twm.config import ROOT

    return ROOT / "data" / MODULE / "wp_backtest"


def save_model(model: WpModel, root: Path | None = None) -> Path:
    """Write ``<root>/<version>.joblib`` atomically (temp file, then rename)."""
    import joblib

    path = (root if root is not None else models_dir()) / f"{model.version}.joblib"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    joblib.dump({"format": FILE_FORMAT, "model": model}, tmp)
    tmp.replace(path)
    return path


def load_model(path: Path) -> WpModel:
    """Load a file written by :func:`save_model` (a pickle: only load files this code wrote)."""
    import joblib

    if not path.exists():
        raise WpModelError(f"WP model file not found: {path}")
    bundle = joblib.load(path)
    if not isinstance(bundle, dict) or bundle.get("format") != FILE_FORMAT:
        raise WpModelError(f"{path} is not a WP model file of this version of the code")
    model = bundle["model"]
    if not isinstance(model, WpModel):
        raise WpModelError(f"{path} does not hold a WP model")
    return model


def save_fold(out: FoldOutput, *, models_root: Path | None = None, out_dir: Path | None = None):
    """Save the model, the test predictions (``fold_<S>.parquet``) and the summary
    (``fold_<S>.json``, with the model file's path)."""
    d = out_dir if out_dir is not None else backtest_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = save_model(out.model, models_root)
    s = out.summary["test_season"]
    out.predictions.write_parquet(d / f"fold_{s}.parquet")
    summary = {**out.summary, "model_file": path.name}
    (d / f"fold_{s}.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    return path


def load_fold_model(season: int, *, models_root: Path | None = None, out_dir: Path | None = None):
    """The backtest model that scores ``season`` (trained on earlier seasons only)."""
    d = out_dir if out_dir is not None else backtest_dir()
    f = d / f"fold_{season}.json"
    if not f.exists():
        raise WpModelError(f"no WP backtest fold for {season}: run `twm decisions wp-backtest`")
    name = json.loads(f.read_text())["model_file"]
    return load_model((models_root if models_root is not None else models_dir()) / name)


# --------------------------------------------------------------------------------------
# The backtest driver
# --------------------------------------------------------------------------------------


def fold_is_current(rows: pl.DataFrame, season: int, out_dir: Path, models_root: Path) -> bool:
    """A saved fold can be reused when its summary, predictions and model file exist and its
    training rows hash equals today's (same data, same feature code)."""
    f = out_dir / f"fold_{season}.json"
    if not f.exists() or not (out_dir / f"fold_{season}.parquet").exists():
        return False
    s = json.loads(f.read_text())
    if not (models_root / str(s.get("model_file"))).exists():
        return False
    train = rows.filter(pl.col("season") < season)
    return s.get("dataset_hash") == _training_hash(train) and s.get("format") == FILE_FORMAT


def run_backtest(
    rows: pl.DataFrame,
    seasons: Sequence[int] = TEST_SEASONS,
    *,
    force: bool = False,
    models_root: Path | None = None,
    out_dir: Path | None = None,
    progress=print,
) -> list[int]:
    """Run (or reuse, unless ``force``) the fold of every season in ``seasons``, one at a
    time, saving each as soon as it is done. Returns the seasons that were fitted."""
    models_root = models_root if models_root is not None else models_dir()
    out_dir = out_dir if out_dir is not None else backtest_dir()
    fitted = []
    for i, s in enumerate(sorted({int(x) for x in seasons}), 1):
        if not force and fold_is_current(rows, s, out_dir, models_root):
            progress(f"[{i}/{len(seasons)}] {s}: saved fold is current, reused")
            continue
        progress(f"[{i}/{len(seasons)}] {s}: fitting")
        out = run_fold(rows.filter(pl.col("season") <= s), s, progress=progress)
        save_fold(out, models_root=models_root, out_dir=out_dir)
        fitted.append(s)
    return fitted
