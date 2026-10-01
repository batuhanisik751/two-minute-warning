"""Regression Watch's own walk-forward xFP (step H6-b; PROJECT_SPEC 6.3 and 8.2, P2).

D1's xFP re-scores nflverse's ffopportunity expectations, which come from models trained on many
seasons, including seasons AFTER some backtest weeks (a mild, known leakage, spec 6.3). This
module re-estimates the SAME per-play expectations walk-forward: a play of season S gets its
expectations from models that saw seasons 2006 .. S-1 only (the 2026 fold is the live model).

- per pass (two-point tries apart): the chance it is caught (``pass_completion_exp``), the
  yards after the catch if caught (``yards_after_catch_exp``), the chance of a touchdown
  (``pass_touchdown_exp``) and of an interception (``pass_interception_exp``);
- per run: the yards (``rush_yards_exp``) and the chance of a touchdown
  (``rush_touchdown_exp``); a kneel is -1 yard and an aborted snap 0 (ffopportunity's values,
  docs/warehouse.md), never fitted;
- per two-point try (pass or run): the chance it succeeds (``two_point_conv_exp``).

**Inputs** are the opportunity and the situation before the snap (:data:`PASS_FEATURES`,
:data:`RUSH_FEATURES`, from ``fact_play``): never an nflfastR model column (epa, wp, xpass,
cpoe, xyac) and not the garbage-time flag (a function of nflfastR's wp).

**Models.** Each component is fit twice on the training seasons before the validation season
(the last training season, as in :func:`twm.backtest.walkforward.plan_folds`): LightGBM
(single-threaded, fixed seed, early stopping on the validation season) and a GLM (splines on the
numbers, one-hot on the categories; logistic for chances, ridge for yards). The family with the
lower validation loss (log loss; squared error for yards, whose expectation is a mean) is refit
on every training season. A thin fold (2007: one training season) uses the GLM. Two-point tries
are rare (about 50 a season): their model is the training seasons' success rate per kind.

**Scoring.** The predictions keep ffopportunity's column names, so D1's own SQL
(:func:`~twm.modules.regression_watch.plays.play_expected_sql` and :func:`twm.scoring.xfp_sql`,
config/scoring.yaml) turns them into expected stats and xFP: there is no second copy of the
rules. Outputs (git-ignored): ``data/regression_watch/own_xfp/``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

FIRST_SEASON = 2006  # ffopportunity's per-play files start here
FIRST_TEST_SEASON = FIRST_SEASON + 1
SEED = 20261001
FILE_FORMAT = 2
OUT_DIR = Path("data/regression_watch/own_xfp")

PASS_FEATURES = ("air_yards", "yardline_100", "down", "ydstogo", "pass_location", "qtr",
                 "score_differential")  # fmt: skip
RUSH_FEATURES = ("yardline_100", "down", "ydstogo", "run_location", "run_gap", "qtr",
                 "score_differential", "qb_scramble")  # fmt: skip
# Text categories (codes 0..k-1 for LightGBM, one-hot for the GLM; anything else = missing).
CATEGORIES: dict[str, tuple[str, ...]] = {
    "pass_location": ("left", "middle", "right"),
    "run_location": ("left", "middle", "right"),
    "run_gap": ("end", "tackle", "guard"),
}
GLM_ONE_HOT = ("down", "qtr", *CATEGORIES)  # few values: one-hot in the GLM
GLM_FLAGS = ("qb_scramble",)  # 0/1 as is
# Model columns that must never be a feature here (spec 6.3): nflfastR model outputs.
FORBIDDEN = ("ep", "epa", "wp", "def_wp", "home_wp", "away_wp", "wpa", "vegas_wp",
             "vegas_home_wp", "vegas_wpa", "xpass", "pass_oe", "cpoe", "xyac_epa",
             "xyac_mean_yardage", "xyac_median_yardage", "xyac_success", "xyac_fd",
             "is_garbage_time", "is_neutral")  # fmt: skip
assert not set(FORBIDDEN) & {*PASS_FEATURES, *RUSH_FEATURES}

Progress = Callable[[str], None]


@dataclass(frozen=True)
class Component:
    """One per-play expectation: the ffopportunity ``column`` it re-estimates, from the plays
    of ``kind`` ('pass' or 'rush'), learned from ``label`` on the ``rows`` that have one."""

    name: str
    kind: str
    column: str
    label: str
    objective: str  # 'binary' (a chance), 'regression' (yards) or 'rate' (a success rate)
    rows: str  # 'play' (no two-point try), 'caught' (caught passes only) or 'two_point'
    title: str


COMPONENTS: tuple[Component, ...] = (
    Component("completion", "pass", "pass_completion_exp", "complete_pass", "binary", "play",
              "catch probability per pass"),
    Component("yac", "pass", "yards_after_catch_exp", "yac", "regression", "caught",
              "yards after the catch (caught passes)"),
    Component("pass_td", "pass", "pass_touchdown_exp", "pass_touchdown", "binary", "play",
              "touchdown probability per pass"),
    Component("interception", "pass", "pass_interception_exp", "interception", "binary", "play",
              "interception probability per pass"),
    Component("rush_yards", "rush", "rush_yards_exp", "rushing_yards", "regression", "play",
              "yards per carry"),
    Component("rush_td", "rush", "rush_touchdown_exp", "rush_touchdown", "binary", "play",
              "touchdown probability per carry"),
    Component("pass_2pt", "pass", "two_point_conv_exp", "two_point_converted", "rate",
              "two_point", "two-point try by pass: success probability"),
    Component("rush_2pt", "rush", "two_point_conv_exp", "two_point_converted", "rate",
              "two_point", "two-point try by run: success probability"),
)  # fmt: skip
COMPONENT_BY_NAME = {c.name: c for c in COMPONENTS}
EXPECTED_COLUMNS = {
    "pass": ("pass_completion_exp", "yards_after_catch_exp", "pass_touchdown_exp",
             "pass_interception_exp", "two_point_conv_exp"),
    "rush": ("rush_yards_exp", "rush_touchdown_exp", "two_point_conv_exp"),
}  # fmt: skip
FEATURES = {"pass": PASS_FEATURES, "rush": RUSH_FEATURES}
KEYS = ("game_id", "play_id")


def fit_mask(c: Component) -> pl.Expr:
    """The play rows a component learns from (a known label, the right kind of play)."""
    play = ~pl.col("is_2pt") & ~pl.col("is_fixed")
    return {
        "play": play,
        "caught": play & (pl.col("complete_pass") == 1) & pl.col("yac").is_not_null(),
        "two_point": pl.col("is_2pt"),
    }[c.rows]


def predict_mask(c: Component) -> pl.Expr:
    """The play rows a component predicts: every pass for the yards after the catch (D1 scores
    completion chance x (air yards + expected YAC)), else the rows it learns from."""
    if c.rows == "caught":
        return ~pl.col("is_2pt") & ~pl.col("is_fixed")
    return fit_mask(c)


# --------------------------------------------------------------------------------------
# The plays (typed warehouse, read-only)
# --------------------------------------------------------------------------------------

_PASS_SQL = """
SELECT o.season, o.week, o.season_type, o.game_id, o.play_id, o.posteam,
       o.passer_player_id, o.receiver_player_id, o.two_point_attempt,
       COALESCE(o.two_point_attempt, 0) = 1 AS is_2pt, FALSE AS is_fixed,
       o.two_point_converted, o.complete_pass, o.pass_touchdown, o.interception,
       o.air_yards, o.receiving_yards,
       CASE WHEN o.complete_pass = 1 THEN CAST(o.receiving_yards - o.air_yards AS DOUBLE) END
           AS yac,
       o.is_garbage_time, o.available_at,
       p.yardline_100, p.down, p.ydstogo, p.pass_location, p.qtr, p.score_differential,
       {exp}
FROM w.fact_opportunity_pass o LEFT JOIN w.fact_play p USING (game_id, play_id)
WHERE o.season IN ({seasons}) ORDER BY o.game_id, o.play_id"""
_RUSH_SQL = """
SELECT o.season, o.week, o.season_type, o.game_id, o.play_id, o.posteam, o.rusher_player_id,
       o.two_point_attempt, COALESCE(o.two_point_attempt, 0) = 1 AS is_2pt,
       COALESCE(o.two_point_attempt, 0) = 0
           AND (COALESCE(p.qb_kneel, 0) = 1 OR COALESCE(p.aborted_play, 0) = 1) AS is_fixed,
       COALESCE(o.two_point_attempt, 0) = 0 AND COALESCE(p.qb_kneel, 0) = 1 AS is_kneel,
       o.two_point_converted, o.rush_touchdown, CAST(o.rushing_yards AS DOUBLE) AS rushing_yards,
       o.is_garbage_time, o.available_at,
       p.yardline_100, p.down, p.ydstogo, p.run_location, p.run_gap, p.qtr,
       p.score_differential, p.qb_scramble,
       {exp}
FROM w.fact_opportunity_rush o LEFT JOIN w.fact_play p USING (game_id, play_id)
WHERE o.season IN ({seasons}) ORDER BY o.game_id, o.play_id"""


def load_plays(db: Path | str, seasons: Sequence[int]) -> dict[str, pl.DataFrame]:
    """{'pass': ..., 'rush': ...}: ffopportunity's per-play rows of ``seasons`` (every season
    type) with the labels, the situation before the snap from ``fact_play`` (same game_id and
    play_id; NULL for the few plays not there) and ffopportunity's own expectations, sorted by
    (game_id, play_id). ``is_fixed``: a kneel or an aborted snap (fixed values, never fitted)."""
    import duckdb

    ss = ", ".join(str(int(s)) for s in sorted(set(seasons))) or "NULL"
    con = duckdb.connect()
    try:
        con.execute(f"ATTACH '{Path(db)}' AS w (READ_ONLY)")
        out = {}
        for kind, sql in (("pass", _PASS_SQL), ("rush", _RUSH_SQL)):
            exp = ", ".join(f"o.{c}" for c in EXPECTED_COLUMNS[kind])
            out[kind] = con.sql(sql.format(exp=exp, seasons=ss)).pl()
    finally:
        con.close()
    return out


# --------------------------------------------------------------------------------------
# Models: LightGBM, the GLM and the two-point rate
# --------------------------------------------------------------------------------------

LGBM_FIXED: dict[str, Any] = {
    "learning_rate": 0.05, "num_leaves": 31, "min_child_samples": 200, "reg_lambda": 1.0,
    "subsample": 1.0, "colsample_bytree": 1.0, "deterministic": True, "force_col_wise": True,
    "n_jobs": 1, "random_state": SEED, "verbose": -1,
}  # fmt: skip
MAX_TREES = 2000
EARLY_STOPPING_ROUNDS = 50
GLM_KNOTS = 6  # spline knots per number (quantiles of the training rows)
GLM_C = 1.0  # logistic GLM: L2 strength (sklearn's C)
RIDGE_ALPHA = 1.0  # yards GLM
EPS = 1e-6  # probabilities are clipped to [EPS, 1 - EPS] for the log loss


def matrix(x: pl.DataFrame, features: Sequence[str]) -> np.ndarray:
    """The model matrix: numbers as floats (NaN = missing), categories as codes 0..k-1."""
    cols = []
    for f in features:
        if f in CATEGORIES:
            codes = {v: float(i) for i, v in enumerate(CATEGORIES[f])}
            cols.append(pl.col(f).replace_strict(codes, default=None, return_dtype=pl.Float64))
        else:
            cols.append(pl.col(f).cast(pl.Float64))
    return x.select(cols).to_numpy()


def glm_pipeline(features: Sequence[str], objective: str):
    """Splines on the numbers (median-imputed, plus a missing flag), one-hot on the few-valued
    columns, flags as they are; then a logistic regression (chances) or a ridge (yards)."""
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import MissingIndicator, SimpleImputer
    from sklearn.linear_model import LogisticRegression, Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import OneHotEncoder, SplineTransformer

    num = [i for i, f in enumerate(features) if f not in GLM_ONE_HOT and f not in GLM_FLAGS]
    cat = [i for i, f in enumerate(features) if f in GLM_ONE_HOT]
    flag = [i for i, f in enumerate(features) if f in GLM_FLAGS]
    parts = [
        ("num", make_pipeline(SimpleImputer(strategy="median"),
                              SplineTransformer(n_knots=GLM_KNOTS, knots="quantile")), num),
        ("missing", MissingIndicator(features="all"), num),
        ("cat", make_pipeline(SimpleImputer(strategy="constant", fill_value=-1.0),
                              OneHotEncoder(handle_unknown="ignore", sparse_output=False)), cat),
    ]  # fmt: skip
    if flag:
        parts.append(("flag", SimpleImputer(strategy="constant", fill_value=0.0), flag))
    model = (
        LogisticRegression(C=GLM_C, max_iter=2000)
        if objective == "binary"
        else Ridge(alpha=RIDGE_ALPHA)
    )
    return make_pipeline(ColumnTransformer(parts), model)


@dataclass
class Fitted:
    """A fitted component model: ``predict`` = the expectation per play row."""

    family: str  # 'lgbm', 'glm' or 'rate'
    objective: str
    features: tuple[str, ...]
    model: Any
    n_trees: int | None = None

    def predict(self, x: pl.DataFrame) -> np.ndarray:
        if self.family == "rate":
            return np.full(x.height, float(self.model))
        m = matrix(x, self.features)
        if self.objective == "binary":
            return self.model.predict_proba(m)[:, 1]
        return self.model.predict(m)


def _y(c: Component, rows: pl.DataFrame) -> np.ndarray:
    return rows.get_column(c.label).cast(pl.Float64).to_numpy()


def fit_lgbm(
    c: Component, train: pl.DataFrame, val: pl.DataFrame | None = None, n_trees: int | None = None
) -> Fitted:
    """LightGBM on ``train``; with ``val``: early stopping on it (``n_trees`` = the best
    iteration), else exactly ``n_trees`` trees."""
    import lightgbm as lgb

    feats = FEATURES[c.kind]
    cls = lgb.LGBMClassifier if c.objective == "binary" else lgb.LGBMRegressor
    objective = "binary" if c.objective == "binary" else "regression"
    model = cls(**LGBM_FIXED, objective=objective, n_estimators=n_trees or MAX_TREES)
    kw: dict[str, Any] = {"categorical_feature": [i for i, f in enumerate(feats)
                                                  if f in CATEGORIES]}  # fmt: skip
    if val is not None:
        kw["eval_X"], kw["eval_y"] = matrix(val, feats), _y(c, val)
        kw["callbacks"] = [lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False)]
    model.fit(matrix(train, feats), _y(c, train), **kw)
    trees = int(model.best_iteration_ or 0) if val is not None else int(n_trees or MAX_TREES)
    return Fitted("lgbm", c.objective, feats, model, max(trees, 1))


def fit_glm(c: Component, train: pl.DataFrame) -> Fitted:
    feats = FEATURES[c.kind]
    pipe = glm_pipeline(feats, c.objective)
    pipe.fit(matrix(train, feats), _y(c, train))
    return Fitted("glm", c.objective, feats, pipe)


def fit_rate(c: Component, train: pl.DataFrame) -> Fitted:
    """The success rate of the training rows (0.5 without any)."""
    y = _y(c, train)
    return Fitted("rate", c.objective, (), float(y.mean()) if len(y) else 0.5)


def loss(objective: str, y: np.ndarray, p: np.ndarray) -> float:
    """Log loss for a chance, squared error for yards (the expectation is a mean)."""
    if objective == "regression":
        return float(np.mean((y - p) ** 2))
    q = np.clip(p, EPS, 1 - EPS)
    return float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q)))


# --------------------------------------------------------------------------------------
# Walk-forward: one fold per test season
# --------------------------------------------------------------------------------------


@dataclass
class ComponentFold:
    """What one component's model did in one fold (a row of ``folds.parquet``)."""

    component: str
    test_season: int
    train_first: int
    train_last: int
    val_season: int | None
    family: str
    n_trees: int | None
    val_loss_lgbm: float | None
    val_loss_glm: float | None
    n_train: int
    n_predicted: int


def fit_component(c: Component, plays: pl.DataFrame, fold: Any) -> tuple[Fitted, ComponentFold]:
    """Fit ``c`` for ``fold`` (a :class:`twm.backtest.walkforward.Fold`) on its training
    seasons only; the family is chosen on the validation season (GLM in a thin fold)."""
    from twm.backtest.walkforward import assert_before

    rows = plays.filter(fit_mask(c))
    train = rows.filter(pl.col("season").is_in(list(fold.train_seasons)))
    assert_before(train, fold.test_season, what=f"fit own xFP {c.name}")
    ll = gl = None
    tune = val = rows.clear()
    if not fold.thin:
        tune = rows.filter(pl.col("season").is_in(list(fold.tune_seasons)))
        val = rows.filter(pl.col("season") == fold.val_season)
    if c.objective == "rate":
        fitted = fit_rate(c, train)
    elif not tune.height or not val.height:  # a thin fold (or nothing to validate on): GLM
        fitted = fit_glm(c, train)
    else:
        assert_before(tune, fold.val_season, what=f"tune own xFP {c.name}")
        assert_before(val, fold.test_season, what=f"validate own xFP {c.name}")
        lg, gm = fit_lgbm(c, tune, val), fit_glm(c, tune)
        yv = _y(c, val)
        ll, gl = loss(c.objective, yv, lg.predict(val)), loss(c.objective, yv, gm.predict(val))
        fitted = fit_lgbm(c, train, n_trees=lg.n_trees) if ll < gl else fit_glm(c, train)
    info = ComponentFold(
        c.name, fold.test_season, min(fold.train_seasons), max(fold.train_seasons),
        fold.val_season, fitted.family, fitted.n_trees, ll, gl, train.height, 0,
    )  # fmt: skip
    return fitted, info


def predict_fold(
    plays: dict[str, pl.DataFrame], test_season: int, data_seasons: Sequence[int]
) -> tuple[dict[str, pl.DataFrame], list[ComponentFold]]:
    """Every component's expectations for the plays of ``test_season`` from models trained on
    the seasons before it: {'pass': keys + expected columns + '<component>_family', 'rush':
    ...} and one :class:`ComponentFold` per component."""
    from twm.backtest.walkforward import production_fold

    fold = production_fold(data_seasons, test_season)
    out, infos = {}, []
    for kind in ("pass", "rush"):
        frame = plays[kind]
        flags = ("is_2pt", "is_fixed", *(("is_kneel",) if kind == "rush" else ()))
        test = frame.filter(pl.col("season") == test_season).select(*KEYS, *flags)
        cols: dict[str, pl.Series] = {}
        for c in (c for c in COMPONENTS if c.kind == kind):
            fitted, info = fit_component(c, frame, fold)
            mask = test.select(predict_mask(c)).to_series().to_numpy()
            pred = np.full(test.height, np.nan)
            sub = frame.filter(pl.col("season") == test_season).filter(predict_mask(c))
            if mask.any():
                pred[mask] = fitted.predict(sub)
            info.n_predicted = int(mask.sum())
            cols[c.column] = pl.Series(c.column, pred).fill_nan(None)
            cols[f"{c.name}_family"] = pl.Series(f"{c.name}_family", [info.family] * test.height)
            infos.append(info)
        out[kind] = (
            fixed_values(test.with_columns(**cols))
            if kind == "rush"
            else (test.with_columns(**cols))
        )
    return out, infos


def fixed_values(rush: pl.DataFrame) -> pl.DataFrame:
    """ffopportunity's values for a kneel (-1 yard) and an aborted snap (0), no touchdown."""
    fixed = pl.col("is_fixed")
    return rush.with_columns(
        pl.when(fixed)
        .then(pl.when(pl.col("is_kneel")).then(-1.0).otherwise(0.0))
        .otherwise(pl.col("rush_yards_exp"))
        .alias("rush_yards_exp"),
        pl.when(fixed)
        .then(0.0)
        .otherwise(pl.col("rush_touchdown_exp"))
        .alias("rush_touchdown_exp"),
    )


# --------------------------------------------------------------------------------------
# The run: every fold (cached on disk, optionally in parallel processes), then the tables
# --------------------------------------------------------------------------------------


def settings_fingerprint() -> str:
    """What the models depend on besides the data (a changed setting refits every fold)."""
    import hashlib

    blob = json.dumps(
        {"format": FILE_FORMAT, "lgbm": LGBM_FIXED, "max_trees": MAX_TREES,
         "early": EARLY_STOPPING_ROUNDS, "glm": [GLM_KNOTS, GLM_C, RIDGE_ALPHA],
         "features": FEATURES, "categories": CATEGORIES,
         "components": [asdict(c) for c in COMPONENTS]},
        sort_keys=True, default=str,
    )  # fmt: skip
    return hashlib.sha256(blob.encode()).hexdigest()


def season_hashes(plays: dict[str, pl.DataFrame]) -> dict[int, str]:
    """sha256 of every season's inputs (features, labels, flags; not ffopportunity's values)."""
    import hashlib

    out: dict[int, str] = {}
    seasons = sorted(set(plays["pass"]["season"]) | set(plays["rush"]["season"]))
    for s in seasons:
        h = hashlib.sha256()
        for kind in ("pass", "rush"):
            cols = [c for c in plays[kind].columns if c not in EXPECTED_COLUMNS[kind]]
            part = plays[kind].filter(pl.col("season") == s).select(cols)
            h.update(part.write_csv().encode())
        out[int(s)] = h.hexdigest()
    return out


def fold_hash(hashes: dict[int, str], test_season: int) -> str:
    """The fold's data: the training seasons and the test season's own inputs."""
    import hashlib

    blob = settings_fingerprint() + "".join(
        f"{s}:{h};" for s, h in sorted(hashes.items()) if s <= test_season
    )
    return hashlib.sha256(blob.encode()).hexdigest()


def _fold_paths(out_dir: Path, season: int) -> dict[str, Path]:
    d = out_dir / "folds"
    return {"pass": d / f"{season}_pass.parquet", "rush": d / f"{season}_rush.parquet",
            "meta": d / f"{season}.json"}  # fmt: skip


def fold_is_current(out_dir: Path, season: int, data_hash: str) -> bool:
    paths = _fold_paths(out_dir, season)
    if not all(p.exists() for p in paths.values()):
        return False
    meta = json.loads(paths["meta"].read_text())
    return meta.get("data_hash") == data_hash and meta.get("format") == FILE_FORMAT


def _inputs_path(out_dir: Path, kind: str) -> Path:
    return out_dir / f"inputs_{kind}.parquet"


def run_fold(out_dir: Path, season: int, data_seasons: list[int], data_hash: str) -> float:
    """Fit and predict one fold from the saved inputs (rows of seasons <= ``season`` only) and
    save it; returns the seconds it took. Top level: runs in a worker process."""
    from threadpoolctl import threadpool_limits

    t0 = time.perf_counter()
    plays = {
        k: pl.read_parquet(_inputs_path(out_dir, k)).filter(pl.col("season") <= season)
        for k in ("pass", "rush")
    }
    with threadpool_limits(limits=1):  # one BLAS thread: the GLM fits are reproducible
        preds, infos = predict_fold(plays, season, [s for s in data_seasons if s <= season])
    paths = _fold_paths(out_dir, season)
    paths["meta"].parent.mkdir(parents=True, exist_ok=True)
    for kind in ("pass", "rush"):
        preds[kind].write_parquet(paths[kind])
    seconds = round(time.perf_counter() - t0, 1)
    meta = {"format": FILE_FORMAT, "data_hash": data_hash, "season": season,
            "seconds": seconds, "components": [asdict(i) for i in infos]}  # fmt: skip
    paths["meta"].write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n")
    return seconds


def run_folds(
    db: Path | str,
    last_season: int,
    out_dir: Path,
    *,
    first_test_season: int = FIRST_TEST_SEASON,
    workers: int = 1,
    progress: Progress | None = None,
) -> list[int]:
    """Fit every fold ``first_test_season`` .. ``last_season`` that is not current on disk
    (inputs or settings changed), in up to ``workers`` processes (each fit is single-threaded
    and seeded, so the result does not depend on the order). Returns the refitted seasons."""
    from concurrent.futures import ProcessPoolExecutor

    say = progress or (lambda _m: None)
    plays = load_plays(db, range(FIRST_SEASON, last_season + 1))
    out_dir.mkdir(parents=True, exist_ok=True)
    for kind in ("pass", "rush"):
        plays[kind].write_parquet(_inputs_path(out_dir, kind))
    hashes = season_hashes(plays)
    data_seasons = sorted(hashes)
    todo = [
        (s, fold_hash(hashes, s))
        for s in range(first_test_season, last_season + 1)
        if not fold_is_current(out_dir, s, fold_hash(hashes, s))
    ]
    say(f"own xFP: {len(todo)} fold(s) to fit, "
        f"{last_season - first_test_season + 1 - len(todo)} current")  # fmt: skip
    if workers <= 1:
        for s, h in todo:
            say(f"fold {s}: {run_fold(out_dir, s, data_seasons, h)} s")
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {s: pool.submit(run_fold, out_dir, s, data_seasons, h) for s, h in todo}
            for s, f in futures.items():
                say(f"fold {s}: {f.result()} s")
    return [s for s, _ in todo]


def load_predictions(out_dir: Path, seasons: Sequence[int]) -> dict[str, pl.DataFrame]:
    """The saved folds of ``seasons``: {'pass': ..., 'rush': ...} sorted by (game_id, play_id)."""
    out = {}
    for kind in ("pass", "rush"):
        parts = [pl.read_parquet(_fold_paths(out_dir, s)[kind]) for s in sorted(seasons)]
        out[kind] = pl.concat(parts, how="vertical").sort(list(KEYS))
    return out


def load_fold_info(out_dir: Path, seasons: Sequence[int]) -> pl.DataFrame:
    rows = []
    for s in sorted(seasons):
        meta = json.loads(_fold_paths(out_dir, s)["meta"].read_text())
        rows += [{**c, "fold_seconds": meta["seconds"]} for c in meta["components"]]
    return pl.from_dicts(rows, infer_schema_length=None)


def own_tables(
    plays: dict[str, pl.DataFrame], preds: dict[str, pl.DataFrame]
) -> dict[str, pl.DataFrame]:
    """ffopportunity's per-play rows with its expected columns replaced by the own ones (same
    names): what D1's per-play SQL reads. Plays without an own prediction are dropped."""
    out = {}
    for kind in ("pass", "rush"):
        cols = EXPECTED_COLUMNS[kind]
        own = preds[kind].select(*KEYS, *cols)
        out[kind] = plays[kind].drop(cols).join(own, on=list(KEYS), how="inner")
    return out


def player_game_xfp(
    tables: dict[str, pl.DataFrame], rules: Any = None, *, where: str = "season_type = 'REG'"
) -> pl.DataFrame:
    """One row per (game_id, gsis_id) of the per-play ``tables`` (columns as
    ``fact_opportunity_pass`` / ``_rush``): xFP over all his plays and over the garbage-time
    ones, the non-garbage xFP, the expected components with the frame's names and ``yac_exp``,
    all through D1's own SQL (:func:`.plays.play_expected_sql`, :func:`twm.scoring.xfp_sql`)."""
    import duckdb

    from twm.modules.regression_watch.player_week import COMPONENTS as FRAME_COMPONENTS
    from twm.modules.regression_watch.player_week import EXPECTED_COMPONENTS
    from twm.modules.regression_watch.plays import play_expected_sql
    from twm.scoring import ScoringRules, xfp_sql

    rules = rules or ScoringRules.from_config()
    comps = ", ".join(f"sum({FRAME_COMPONENTS[c]}) AS {c}" for c in EXPECTED_COMPONENTS)
    inner = play_expected_sql(where, pass_table="src_pass", rush_table="src_rush")
    sql = f"""
    SELECT game_id, gsis_id, min(season) AS season, min(week) AS week,
           sum(x) AS xfp, COALESCE(sum(x) FILTER (WHERE is_garbage_time), 0) AS xfp_garbage,
           sum(x) - COALESCE(sum(x) FILTER (WHERE is_garbage_time), 0) AS xfp_ng,
           {comps}, sum(yac_exp) AS yac_exp, count(*) AS n_opportunities
    FROM (SELECT *, {xfp_sql(rules)} AS x FROM ({inner}))
    GROUP BY game_id, gsis_id ORDER BY game_id, gsis_id"""
    con = duckdb.connect()
    try:
        con.register("src_pass", tables["pass"])
        con.register("src_rush", tables["rush"])
        return con.sql(sql).pl()
    finally:
        con.close()
