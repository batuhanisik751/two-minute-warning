"""Shared machinery of the fourth-down sub-models (PROJECT_SPEC 8.4 item 2, step G2).

- :func:`load_plays`: the play rows every sub-model starts from (typed warehouse, read-only),
  each with the state of the NEXT snap in the same game (``n_*`` columns: who has the ball,
  where, which half, the clock), which is how a play's result is read (where the next drive
  starts, who kept the ball) without the raw play-by-play.
- :class:`LgbmEstimator`: a LightGBM binary classifier for the shared walk-forward harness,
  with monotone constraints, an optional feature-set choice (a grid dimension, picked on the
  validation season like any setting) and imputation fit on the training rows only.
- :func:`run_binary_fold` / :func:`run_binary_backtest`: one walk-forward fold per test season
  (train on seasons < S, tune on S-1, isotonic only when it helps out of sample), saved under
  ``models/decisions/`` (models) and ``data/decisions/submodels/<model>/`` (test predictions).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import polars as pl

from twm.backtest.walkforward import RAW_SCORE, fit_fold, plan_folds
from twm.modules.decisions import wp

MODULE = "decisions"
KEYS = ("season", "game_id", "play_id")
TEST_SEASONS = wp.TEST_SEASONS
FIRST_SEASON = wp.FIRST_SEASON
FILE_FORMAT = 1

_PLAY_COLS = ("game_id", "play_id", "season", "week", "season_type", "qtr", "game_half", "down",
              "ydstogo", "yardline_100", "goal_to_go", "game_seconds_remaining",
              "half_seconds_remaining", "score_differential", "posteam_timeouts_remaining",
              "defteam_timeouts_remaining", "posteam", "defteam", "home_team", "away_team",
              "play_type", "yards_gained", "first_down", "touchdown", "td_team",
              "interception", "fumble_lost", "penalty", "field_goal_result",
              "extra_point_attempt", "extra_point_result", "two_point_attempt",
              "two_point_conv_result", "roof", "surface", "temp", "wind", "weather",
              "kickoff_attempt")  # fmt: skip
_GAME_COLS = ("game_id", "spread_line", "location", "result")


def _select_list() -> str:
    return ", ".join(f'"{c}"' for c in _PLAY_COLS)


def load_plays(db: Path | str, seasons: Sequence[int]) -> pl.DataFrame:
    """Every play row of ``seasons`` with its game's closing spread, venue and result, sorted
    by game and play, and the next snap's state (:func:`add_next_snap`)."""
    import duckdb

    ss = ", ".join(str(int(s)) for s in seasons) or "NULL"
    con = duckdb.connect()
    try:
        con.execute(f"ATTACH '{Path(db)}' AS w (READ_ONLY)")
        plays = con.sql(
            f"SELECT {_select_list()} FROM w.fact_play WHERE season IN ({ss}) "
            "ORDER BY game_id, play_id"
        ).pl()
        games = con.sql(
            f"SELECT {', '.join(_GAME_COLS)} FROM w.fact_game WHERE season IN ({ss}) "
            "ORDER BY game_id"
        ).pl()
    finally:
        con.close()
    return add_next_snap(plays.join(games, on="game_id", how="left"))


NEXT = ("play_id", "posteam", "yardline_100", "down", "game_half", "game_seconds_remaining")


def add_next_snap(plays: pl.DataFrame) -> pl.DataFrame:
    """Add ``n_<col>`` for ``col`` in :data:`NEXT`: the first LATER row of the same game that
    is a snap (a down, a possession team and a yardline; ``no_play`` penalty rows count: their
    state is where the ball was). NULL when the game has no later snap. ``plays`` must be
    sorted by game and play."""
    snap = (pl.col("down").is_not_null() & pl.col("posteam").is_not_null()
            & pl.col("yardline_100").is_not_null())  # fmt: skip
    nxt = [
        pl.when(snap).then(pl.col(c)).shift(-1).backward_fill().over("game_id").alias(f"n_{c}")
        for c in NEXT
    ]
    return plays.with_columns(nxt)


def era_flags() -> list[pl.Expr]:
    """The G1 era flags (registered features), from ``season``."""
    return [
        (pl.col("season") >= 2015).cast(pl.Int8).alias("era_pat_2015"),
        (pl.col("season") >= 2023).cast(pl.Int8).alias("era_kickoff_2023"),
    ]


def posteam_spread() -> pl.Expr:
    """The closing spread from the possession team's view (as in G1's WP features)."""
    home = pl.col("posteam") == pl.col("home_team")
    return (
        pl.when(home).then(pl.col("spread_line")).otherwise(-pl.col("spread_line"))
        .cast(pl.Float64).alias("posteam_spread")
    )  # fmt: skip


def count_filters(frame: pl.DataFrame, steps: Sequence[tuple[str, pl.Expr]]):
    """Apply ``steps`` (name, keep-condition) in order; returns (rows, {name: rows dropped})."""
    drops: dict[str, int] = {}
    for name, cond in steps:
        before = frame.height
        frame = frame.filter(cond.fill_null(False))
        drops[name] = before - frame.height
    return frame, drops


# --------------------------------------------------------------------------------------
# A LightGBM estimator for the harness
# --------------------------------------------------------------------------------------


class LgbmFitted(wp.WpFitted):
    """A fitted model: the features it uses (maybe a subset of the candidates) and the values
    that fill missing inputs (medians of its own training rows)."""

    def __init__(self, clf, features, refit, fill: Mapping[str, float]):
        super().__init__(clf, features, refit)
        self.fill = dict(fill)

    def prepare(self, x: pl.DataFrame) -> pl.DataFrame:
        fill = [pl.col(c).cast(pl.Float64).fill_null(v) for c, v in self.fill.items()
                if c in x.columns]  # fmt: skip
        return x.with_columns(fill)

    def predict(self, x: pl.DataFrame) -> np.ndarray:
        return super().predict(self.prepare(x))


class LgbmEstimator:
    """LightGBM (binary log loss) for :func:`twm.backtest.walkforward.fit_fold`.

    ``feature_sets`` (name -> columns): when given, every grid entry is tried with each set
    (``params["feature_set"]``), so the validation season decides whether, e.g., score and
    clock context help. ``impute`` (column -> condition selecting the reference rows, or None
    for all rows): missing values are filled with the median of the column over the reference
    rows OF THE TRAINING ROWS PASSED TO ``fit`` (never validation or test rows)."""

    def __init__(
        self,
        name: str,
        *,
        monotone: Mapping[str, int],
        grid: Sequence[Mapping[str, Any]],
        default_params: Mapping[str, Any],
        feature_sets: Mapping[str, Sequence[str]] | None = None,
        impute: Mapping[str, pl.Expr | None] | None = None,
    ):
        self.name = name
        self.monotone = dict(monotone)
        self.feature_sets = {k: tuple(v) for k, v in (feature_sets or {}).items()}
        self.impute = dict(impute or {})
        sets = list(self.feature_sets) or [None]
        self._grid = [{**g, **({"feature_set": s} if s else {})} for s in sets for g in grid]
        first = {"feature_set": sets[0]} if sets[0] else {}
        self.default_params = {**default_params, **first}
        self.fixed_params = {
            **wp.LGBM_FIXED, "monotone": dict(self.monotone), "max_trees": wp.MAX_TREES,
            "early_stopping_rounds": wp.EARLY_STOPPING_ROUNDS, "metric": "log loss",
            "impute": sorted(self.impute),
        }  # fmt: skip

    def grid(self) -> list[dict[str, Any]]:
        return [dict(g) for g in self._grid]

    def _fill(self, x: pl.DataFrame) -> dict[str, float]:
        fill = {}
        for c, cond in self.impute.items():
            if c not in x.columns:
                continue
            ref = x if cond is None else x.filter(cond.fill_null(False))
            v = ref.get_column(c).drop_nulls().cast(pl.Float64)
            fill[c] = float(v.median()) if v.len() else 0.0
        return fill

    def fit(
        self,
        x: pl.DataFrame,
        y: np.ndarray,
        params: Mapping[str, Any],
        *,
        eval_x: pl.DataFrame | None = None,
        eval_y: np.ndarray | None = None,
    ) -> LgbmFitted:
        p = dict(params)
        fs = p.pop("feature_set", None)
        features = self.feature_sets[fs] if fs else tuple(x.columns)
        fill = self._fill(x)
        prep = LgbmFitted(None, features, {}, fill).prepare
        mono = [self.monotone.get(f, 0) for f in features]
        clf = lgb.LGBMClassifier(**{**wp.LGBM_FIXED, **p}, monotone_constraints=mono)
        kw: dict[str, Any] = {"feature_name": list(features)}
        if eval_x is not None and eval_y is not None:
            kw["eval_X"] = (wp._matrix(prep(eval_x), features),)
            kw["eval_y"] = (eval_y,)
            kw["callbacks"] = [lgb.early_stopping(wp.EARLY_STOPPING_ROUNDS, verbose=False)]
        clf.fit(wp._matrix(prep(x), features), y, **kw)
        refit = dict(params)
        if eval_x is not None and clf.best_iteration_:
            refit["n_estimators"] = int(clf.best_iteration_)
        return LgbmFitted(clf, features, refit, fill)


# --------------------------------------------------------------------------------------
# A saved sub-model and the fold runner
# --------------------------------------------------------------------------------------


class SubModelError(ValueError):
    """A sub-model file or fold is missing, of another format, or cannot score the states."""


@dataclass
class SubModel:
    """One fold's fitted sub-model, saved as ``models/decisions/<version>.joblib``.
    ``fitted`` is a :class:`LgbmFitted` for the binary models and None for table models;
    ``extras`` holds what the model needs besides it (outcome tables, rates, rules)."""

    version: str
    model: str
    test_season: int
    train_seasons: tuple[int, ...]
    val_season: int | None
    features: tuple[str, ...]
    params: dict[str, Any]
    fitted: Any = field(repr=False, default=None)
    calibrator: Any = field(repr=False, default=None)
    calibration: str = "none"
    dataset_hash: str = ""
    extras: dict[str, Any] = field(default_factory=dict, repr=False)

    def probability(self, x: pl.DataFrame) -> np.ndarray:
        if self.fitted is None:
            raise SubModelError(f"{self.model} is not a probability model")
        raw = self.fitted.predict(x)
        if self.calibrator is None:
            return raw
        return np.asarray(self.calibrator.predict(raw), dtype=np.float64)


def training_hash(train: pl.DataFrame, columns: Sequence[str]) -> str:
    """Hash of the training rows (per season, then combined): a saved fold is reused only
    when this is unchanged."""
    from twm.predictions import combine_hashes, frame_hash

    cols = list(dict.fromkeys([*KEYS, *columns]))
    parts = train.select(cols).sort(list(KEYS)).partition_by("season", maintain_order=True)
    return combine_hashes([frame_hash(p) for p in parts])


@dataclass(frozen=True)
class BinarySpec:
    """What a binary sub-model learns: its name, label, candidate features (all registered),
    estimator factory, and optionally the tables it carries (``extras``: built from the
    training rows only) and a validation-only ablation (feature group -> columns)."""

    model: str
    label: str
    features: tuple[str, ...]
    estimator: Callable[[], LgbmEstimator]
    extras: Callable[[pl.DataFrame], dict[str, Any]] | None = None
    ablation: Mapping[str, tuple[str, ...]] | None = None
    extra_columns: tuple[str, ...] = ()  # columns ``extras`` reads (part of the data hash)
    windows: tuple[int | None, ...] = (None,)  # training windows tried (last N seasons; None = all)

    @property
    def hash_columns(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys([*self.features, self.label, *self.extra_columns]))


@dataclass
class FoldOutput:
    model: SubModel
    predictions: pl.DataFrame  # KEYS + raw + prob (binary) or the model's own columns
    summary: dict[str, Any]


def run_binary_fold(
    rows: pl.DataFrame, test_season: int, spec: BinarySpec, *, progress=print
) -> FoldOutput:
    """Tune (on S-1), calibrate only if it helps out of sample, refit on the training seasons
    and score season S. ``rows`` = the sub-model's rows of every season; only seasons before
    ``test_season`` are learned from (the harness refuses anything else). With several
    ``spec.windows`` (train on the last N seasons only: the league changes), each window is
    fitted and the one with the lowest validation log loss (on S-1, the same season for all)
    is kept; ties go to the earlier window."""
    data_hash = training_hash(rows.filter(pl.col("season") < test_season), spec.hash_columns)
    outs = []
    for w in spec.windows:
        r = rows if w is None else rows.filter(pl.col("season") >= test_season - w)
        outs.append(_fit_window(r, test_season, spec, w, data_hash, progress))
    best = min(range(len(outs)), key=lambda i: (outs[i].summary.get("val_logloss", 0.0), i))
    out = outs[best]
    out.summary["windows"] = [
        {"window": o.summary["window"], "val_logloss": o.summary.get("val_logloss")} for o in outs
    ]
    return out


def _fit_window(rows, test_season, spec, window, data_hash, progress) -> FoldOutput:
    t0 = time.perf_counter()
    fold = plan_folds(rows.get_column("season").unique().to_list(), [test_season])[0]
    train = rows.filter(pl.col("season").is_in(list(fold.train_seasons))).sort(list(KEYS))
    test = rows.filter(pl.col("season") == test_season).sort(list(KEYS))
    est = spec.estimator()
    n_trials = len(est.grid())
    val_scores: list[np.ndarray] = []

    def tune_metric(val: pl.DataFrame) -> float:
        s = val.get_column(RAW_SCORE).to_numpy()
        val_scores.append(s)
        ll = wp.log_loss(val.get_column(spec.label).to_numpy(), s)
        progress(f"  {spec.model} {test_season}: trial {len(val_scores)}/{n_trials} validation "
                 f"log loss {ll:.4f} [{time.perf_counter() - t0:.0f} s]")  # fmt: skip
        return -ll

    progress(f"{spec.model} {fold.describe()}: {train.height:,} training rows, "
             f"{test.height:,} test rows")  # fmt: skip
    fr = fit_fold(train, fold, estimator=est, features=spec.features, label=spec.label,
                  module=MODULE, keys=KEYS, tune_metric=tune_metric)  # fmt: skip
    summary: dict[str, Any] = {"test_season": test_season, "fold": fold.describe(),
                               "window": window}  # fmt: skip
    calibrator, calibration = None, "none (thin fold: no validation season)"
    if not fold.thin:
        best = next(i for i, t in enumerate(fr.trials) if t.refit_params == fr.params)
        val = train.filter(pl.col("season") == fold.val_season)
        yv = val.get_column(spec.label).to_numpy()
        helps, raw_ll, iso_ll = wp.isotonic_helps(val.get_column("game_id").to_list(), yv,
                                                  val_scores[best])  # fmt: skip
        calibrator = fr.calibrator if helps else None
        calibration = (f"{'isotonic' if helps else 'none'} (validation {fold.val_season}: raw "
                       f"log loss {raw_ll:.5f}, cross-fitted isotonic {iso_ll:.5f})")  # fmt: skip
        summary.update(val_logloss=raw_ll, val_logloss_isotonic=iso_ll, best_trial=best + 1)
        if spec.ablation:
            summary["ablation"] = _ablation(train, fold, est, spec, fr.params, yv, raw_ll)
    extras = spec.extras(train) if spec.extras else {}
    return _finish(spec, fold, fr, data_hash, test, calibrator, calibration, extras, summary,
                   t0, progress)  # fmt: skip


def _ablation(train, fold, est: LgbmEstimator, spec: BinarySpec, params, yv, val_ll) -> dict:
    """Does each feature group of ``spec.ablation`` matter? The tuning model refit without the
    group (same settings and trees) and scored on the validation season (information only:
    the group stays in the model either way)."""
    tune = train.filter(pl.col("season").is_in(list(fold.tune_seasons)))
    val = train.filter(pl.col("season") == fold.val_season)
    p = {k: v for k, v in params.items() if k != "feature_set"}
    used = est.feature_sets[params["feature_set"]] if "feature_set" in params else spec.features
    out = {}
    for group, cols in (spec.ablation or {}).items():
        feats = [f for f in used if f not in cols]
        e = LgbmEstimator(est.name, monotone=est.monotone, grid=[p], default_params=p,
                          impute={c: v for c, v in est.impute.items() if c in feats})  # fmt: skip
        m = e.fit(tune.select(feats), tune.get_column(spec.label).to_numpy(), p)
        without = wp.log_loss(yv, m.predict(val.select(feats)))
        out[group] = {"val_logloss_with": val_ll, "val_logloss_without": without}
    return out


def _finish(spec, fold, fr, data_hash, test, calibrator, calibration, extras, summary, t0,
            progress):  # fmt: skip
    from twm.predictions import model_version

    model = SubModel(
        version="", model=spec.model, test_season=fold.test_season,
        train_seasons=fold.train_seasons, val_season=fold.val_season,
        features=tuple(fr.model.features), params=dict(fr.params), fitted=fr.model,
        calibrator=calibrator, calibration=calibration,
        dataset_hash=data_hash, extras=extras,
    )  # fmt: skip
    model.version = model_version(
        module=MODULE, model=spec.model, label=spec.label, features=spec.features,
        params={**spec.estimator().fixed_params, **fr.params, "window": summary["window"],
                "calibration": calibration.split()[0]},
        training_seasons=fold.train_seasons, test_season=fold.test_season,
        dataset_hash=model.dataset_hash,
    )  # fmt: skip
    x = test.select(spec.features)
    preds = test.select(KEYS).with_columns(
        pl.Series("raw", fr.model.predict(x), dtype=pl.Float64),
        pl.Series("prob", model.probability(x), dtype=pl.Float64),
    )
    summary.update(
        version=model.version, model=spec.model, calibration=calibration,
        params=dict(fr.params), features_used=list(fr.model.features),
        n_train=fr.n_train, n_train_pos=fr.n_train_pos, n_test=test.height,
        dataset_hash=model.dataset_hash, format=FILE_FORMAT,
        trials=[{"params": t.params, "n_trees": t.refit_params.get("n_estimators"),
                 "val_logloss": -t.metric if t.metric is not None else None} for t in fr.trials],
        importance=[[f, round(v, 4)] for f, v in fr.importance],
        dominant=fr.dominant[0] if fr.dominant else None,
        seconds=round(time.perf_counter() - t0, 1),
    )  # fmt: skip
    progress(f"  {spec.model} {fold.test_season}: done in {summary['seconds']:.0f} s, "
             f"{summary['params'].get('feature_set', '')} {calibration}")  # fmt: skip
    return FoldOutput(model, preds, summary)


# --------------------------------------------------------------------------------------
# Saving, loading and the backtest driver (G1's pattern)
# --------------------------------------------------------------------------------------


def backtest_dir(model: str) -> Path:
    """``data/decisions/submodels/<model>``: per-fold test predictions and summaries."""
    return wp.backtest_dir().parent / "submodels" / model


def save_model(model: SubModel, root: Path | None = None) -> Path:
    """Write ``<root>/<version>.joblib`` atomically (root default: models/decisions)."""
    import joblib

    path = (root if root is not None else wp.models_dir()) / f"{model.version}.joblib"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    joblib.dump({"format": FILE_FORMAT, "submodel": model}, tmp)
    tmp.replace(path)
    return path


def load_model(path: Path) -> SubModel:
    """Load a file written by :func:`save_model` (a pickle: only load files this code wrote)."""
    import joblib

    if not path.exists():
        raise SubModelError(f"sub-model file not found: {path}")
    bundle = joblib.load(path)
    if not isinstance(bundle, dict) or bundle.get("format") != FILE_FORMAT:
        raise SubModelError(f"{path} is not a sub-model file of this version of the code")
    model = bundle.get("submodel")
    if not isinstance(model, SubModel):
        raise SubModelError(f"{path} does not hold a sub-model")
    return model


def save_fold(out: FoldOutput, *, models_root: Path | None = None, out_dir: Path | None = None):
    d = out_dir if out_dir is not None else backtest_dir(out.model.model)
    d.mkdir(parents=True, exist_ok=True)
    path = save_model(out.model, models_root)
    s = out.summary["test_season"]
    out.predictions.write_parquet(d / f"fold_{s}.parquet")
    summary = {**out.summary, "model_file": path.name}
    (d / f"fold_{s}.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    return path


def load_fold_model(
    model: str, season: int, *, models_root: Path | None = None, out_dir: Path | None = None
) -> SubModel:
    """The backtest sub-model that scores ``season`` (trained on earlier seasons only)."""
    d = out_dir if out_dir is not None else backtest_dir(model)
    f = d / f"fold_{season}.json"
    if not f.exists():
        raise SubModelError(f"no {model} fold for {season}: run `twm decisions "
                            "submodels-backtest`")  # fmt: skip
    name = json.loads(f.read_text())["model_file"]
    return load_model((models_root if models_root is not None else wp.models_dir()) / name)


def fold_is_current(dataset_hash: str, season: int, out_dir: Path, models_root: Path) -> bool:
    f = out_dir / f"fold_{season}.json"
    if not f.exists() or not (out_dir / f"fold_{season}.parquet").exists():
        return False
    s = json.loads(f.read_text())
    if not (models_root / str(s.get("model_file"))).exists():
        return False
    return s.get("dataset_hash") == dataset_hash and s.get("format") == FILE_FORMAT


FitOne = Callable[..., FoldOutput]  # (rows of seasons <= S, S, *, progress) -> FoldOutput


def run_backtest(
    rows: pl.DataFrame,
    model: str,
    fit_one: FitOne,
    hash_columns: Sequence[str],
    seasons: Sequence[int] = TEST_SEASONS,
    *,
    force: bool = False,
    models_root: Path | None = None,
    out_dir: Path | None = None,
    progress=print,
) -> list[int]:
    """Fit (or reuse, unless ``force``) the fold of every test season, one at a time, saving
    each as soon as it is done. A saved fold is reused when its training rows hash
    (``hash_columns`` of seasons < S) is unchanged. Returns the seasons that were fitted."""
    models_root = models_root if models_root is not None else wp.models_dir()
    out_dir = out_dir if out_dir is not None else backtest_dir(model)
    todo = sorted({int(x) for x in seasons})
    fitted = []
    for i, s in enumerate(todo, 1):
        h = training_hash(rows.filter(pl.col("season") < s), hash_columns)
        if not force and fold_is_current(h, s, out_dir, models_root):
            progress(f"[{model} {i}/{len(todo)}] {s}: saved fold is current, reused")
            continue
        progress(f"[{model} {i}/{len(todo)}] {s}: fitting")
        out = fit_one(rows.filter(pl.col("season") <= s), s, progress=progress)
        if out.model.dataset_hash != h:
            raise SubModelError(f"{model} {s}: the fold hashed other rows than the driver")
        save_fold(out, models_root=models_root, out_dir=out_dir)
        fitted.append(s)
    return fitted


def load_backtest(
    model: str, seasons: Sequence[int], out_dir: Path | None = None
) -> tuple[pl.DataFrame, list[dict]]:
    """The saved test predictions and fold summaries of ``seasons``."""
    d = out_dir if out_dir is not None else backtest_dir(model)
    missing = [s for s in seasons if not (d / f"fold_{s}.json").exists()]
    if missing:
        raise SubModelError(f"missing {model} folds {missing}: run the backtest first")
    summaries = [json.loads((d / f"fold_{s}.json").read_text()) for s in seasons]
    preds = pl.concat([pl.read_parquet(d / f"fold_{s}.parquet") for s in seasons])
    return preds, summaries


def clock_after(seconds: float) -> list[pl.Expr]:
    """The clock ``seconds`` later (never below 0): for the state after a play."""
    return [
        (pl.col("game_seconds_remaining") - seconds).clip(lower_bound=0).alias(
            "game_seconds_remaining"),
        (pl.col("half_seconds_remaining") - seconds).clip(lower_bound=0).alias(
            "half_seconds_remaining"),
    ]  # fmt: skip


def median_runoff(rows: pl.DataFrame) -> float:
    """Median seconds from a play's snap to the next snap in the same half (training rows)."""
    d = rows.filter(pl.col("n_game_half") == pl.col("game_half")).select(
        (pl.col("game_seconds_remaining") - pl.col("n_game_seconds_remaining")).alias("d")
    ).get_column("d").drop_nulls()  # fmt: skip
    return float(d.median()) if d.len() else 0.0


# --------------------------------------------------------------------------------------
# Hypothetical states for G1's wp() (shared by the punt model, and by G3)
# --------------------------------------------------------------------------------------

STATE_INPUTS = ("season", "score_differential", "game_seconds_remaining",
                "half_seconds_remaining", "half_number", "posteam_timeouts_remaining",
                "defteam_timeouts_remaining", "receives_2h_kickoff", "posteam_is_home",
                "posteam_spread")  # fmt: skip


def other_side(states: pl.DataFrame) -> pl.DataFrame:
    """The same moment seen by the other team (it now has the ball): the score difference,
    spread and timeouts swap sides, home/away flips (a neutral site stays 0.5), and in the
    first half the second-half kickoff goes to whoever does not get it now."""
    return states.with_columns(
        (-pl.col("score_differential")).alias("score_differential"),
        (-pl.col("posteam_spread")).alias("posteam_spread"),
        pl.col("defteam_timeouts_remaining").alias("posteam_timeouts_remaining"),
        pl.col("posteam_timeouts_remaining").alias("defteam_timeouts_remaining"),
        (1.0 - pl.col("posteam_is_home")).alias("posteam_is_home"),
        pl.when(pl.col("half_number") == 1).then(1 - pl.col("receives_2h_kickoff"))
        .otherwise(0).cast(pl.Int8).alias("receives_2h_kickoff"),
    )  # fmt: skip


def first_down_at(states: pl.DataFrame, yardline: pl.Expr) -> pl.DataFrame:
    """A first down (10 to go, or goal to go inside the 10) at ``yardline`` (1-99)."""
    y = yardline.clip(1, 99).cast(pl.Int32)
    return states.with_columns(
        y.alias("yardline_100"), pl.lit(1, pl.Int32).alias("down"),
        pl.min_horizontal(pl.lit(10), y).cast(pl.Int32).alias("ydstogo"),
    )  # fmt: skip
