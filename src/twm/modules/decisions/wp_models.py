"""Win-probability model families tried in G1b to make the WP surface smooth (wp_smooth.py).

All are the harness's ``Estimator`` (fit on the rows they are given only; the validation season
only for early stopping) and return a fitted model whose ``predict`` is the probability that the
possession team wins:

- :class:`LgbmEnsemble`: G1's monotone LightGBM, optionally as a bag of ``members`` models with
  different seeds and row / feature subsamples (averaged on the logit scale: averaging moves
  the step locations of single trees), optionally with linear trees, optionally boosted from
  the logit of a smooth base model (:class:`SplineLogit`) instead of from a constant.
- :class:`SplineLogit`: a smooth parametric model, logistic regression on a cubic B-spline of
  ``z_margin`` plus a few linear terms.
- :class:`Blend`: the average of an ensemble's and a spline model's probabilities.

Single-threaded and ``deterministic=True`` everywhere: two fits are bit-identical.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import lightgbm as lgb
import numpy as np
import polars as pl

from twm.modules.decisions import wp as wpm

Z_KNOTS = (-8.0, -4.0, -2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0, 4.0, 8.0)


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-z))


def _logit(p: np.ndarray, eps: float = 1e-9) -> np.ndarray:
    q = np.clip(p, eps, 1 - eps)
    return np.log(q / (1 - q))


class SplineLogitFitted:
    """A fitted :class:`SplineLogit` (``logit`` = the linear predictor)."""

    importance_kind = "abs standardized coefficient"

    def __init__(self, spline: Any, lr: Any, mean: np.ndarray, sd: np.ndarray, refit: dict,
                 symmetric: bool = False):  # fmt: skip
        self.spline, self.lr, self.mean, self.sd = spline, lr, mean, sd
        self.refit_params, self.symmetric = refit, symmetric
        self.late_terms = False

    def design(self, x: pl.DataFrame) -> np.ndarray:
        if self.symmetric:  # scaled, never centred: centring would add an intercept
            return sym_design(x, self.spline, self.late_terms) / self.sd
        return (spline_design(x, self.spline) - self.mean) / self.sd

    def logit(self, x: pl.DataFrame) -> np.ndarray:
        return np.asarray(self.lr.decision_function(self.design(x)), dtype=np.float64)

    def predict(self, x: pl.DataFrame) -> np.ndarray:
        return _sigmoid(self.logit(x))

    def importance(self, x: pl.DataFrame) -> dict[str, float]:
        coef = np.abs(self.lr.coef_[0])
        terms = list(SYM_TERMS if self.symmetric else LINEAR_TERMS)
        n_spline = (coef.size - len(terms) - (2 if self.late_terms else 0)) // (
            2 if self.late_terms else 1
        )
        names = [f"z_spline_{i}" for i in range(n_spline)] + terms
        if self.late_terms:
            names += [f"z_spline_{i}_late" for i in range(n_spline)]
            names += ["drive_value_late", "avail_late"]
        return {n: float(c) for n, c in zip(names, coef, strict=True)}

    @property
    def n_trees(self) -> int:
        return 0


LINEAR_TERMS = ("drive_value", "spread_time", "receives_2h_kickoff", "home", "pto_late",
                "dto_late", "down2", "down3", "down4", "log_ydstogo", "late_down_log_ydstogo",
                "overtime", "era_pat_2015", "era_kickoff_2023")  # fmt: skip


def make_spline() -> Any:
    """Cubic B-spline basis of z_margin on fixed knots :data:`Z_KNOTS` (linear beyond them)."""
    from sklearn.preprocessing import SplineTransformer

    knots = np.asarray(Z_KNOTS, dtype=np.float64).reshape(-1, 1)
    st = SplineTransformer(knots=knots, degree=3, extrapolation="linear", include_bias=False)
    return st.fit(knots)


def spline_design(x: pl.DataFrame, spline: Any) -> np.ndarray:
    """The spline model's columns: the B-spline of ``z_margin``, then :data:`LINEAR_TERMS`
    (``late`` = 1 / (13.5 sqrt(share of regulation left) + 1): timeouts matter late)."""
    from twm.modules.decisions.wp_data import MARGIN_SD

    def col(c: str) -> np.ndarray:
        return x.get_column(c).cast(pl.Float64).to_numpy()

    t = np.clip(col("game_seconds_remaining") / 3600.0, 0.0, 1.0)
    late = 1.0 / (MARGIN_SD * np.sqrt(t) + 1.0)
    down, lyd = col("down"), np.log(col("ydstogo"))
    lin = {
        "drive_value": col("drive_value"), "spread_time": col("spread_time"),
        "receives_2h_kickoff": col("receives_2h_kickoff"), "home": col("posteam_is_home") - 0.5,
        "pto_late": col("posteam_timeouts_remaining") * late,
        "dto_late": col("defteam_timeouts_remaining") * late,
        "down2": (down == 2).astype(float), "down3": (down == 3).astype(float),
        "down4": (down == 4).astype(float), "log_ydstogo": lyd,
        "late_down_log_ydstogo": (down >= 3) * lyd,
        "overtime": (col("half_number") == 3).astype(float),
        "era_pat_2015": col("era_pat_2015"), "era_kickoff_2023": col("era_kickoff_2023"),
    }  # fmt: skip
    basis = spline.transform(col("z_margin").reshape(-1, 1))
    return np.column_stack([basis, *(lin[k] for k in LINEAR_TERMS)])


class SplineLogit:
    """Logistic regression on :func:`spline_design` (columns standardized on the training rows;
    light L2 penalty ``C``). Smooth by construction; no tuning grid (one setting)."""

    name = "wp_spline_logit"
    default_params: Mapping[str, Any] = {"C": 1.0}
    fixed_params: Mapping[str, Any] = {"knots": Z_KNOTS, "terms": LINEAR_TERMS,
                                       "solver": "lbfgs", "max_iter": 2000}  # fmt: skip

    def grid(self) -> list[dict[str, Any]]:
        return [dict(self.default_params)]

    def fit(self, x: pl.DataFrame, y: np.ndarray, params: Mapping[str, Any], *,
            eval_x: pl.DataFrame | None = None, eval_y: np.ndarray | None = None):  # fmt: skip
        from sklearn.linear_model import LogisticRegression

        spline = make_spline()
        d = spline_design(x, spline)
        mean, sd = d.mean(axis=0), d.std(axis=0)
        sd[sd == 0] = 1.0
        lr = LogisticRegression(C=float(params["C"]), solver="lbfgs", max_iter=2000)
        lr.fit((d - mean) / sd, y)
        return SplineLogitFitted(spline, lr, mean, sd, dict(params))


MONOTONE: dict[str, int] = {**wpm.MONOTONE, "drive_value": 1, "z_margin": 1}


SYM_TERMS = ("drive_value", "spread_time", "receives_2h_centred", "home", "timeout_edge_late",
             "avail", "avail_era_pat_2015", "avail_era_kickoff_2023", "overtime", "down2",
             "down3", "down4", "log_ydstogo_10", "late_down_log_ydstogo_10")  # fmt: skip


def availability(x: pl.DataFrame) -> np.ndarray:
    """Share of a typical drive's value that the half still allows: drive_ep of the other
    team's usual start at this clock / at a full half (1 early in a half, 0 when it ends)."""
    from twm.modules.decisions.wp_data import OPP_DRIVE_START, drive_ep

    hs = x.get_column("half_seconds_remaining").cast(pl.Float64).to_numpy()
    start = np.full(hs.size, float(OPP_DRIVE_START))
    return drive_ep(start, hs) / drive_ep(start[:1], np.array([1e9]))[0]


def sym_design(x: pl.DataFrame, spline: Any, late_terms: bool = False) -> np.ndarray:
    """The symmetric spline model's columns (no intercept): an ODD spline of z_margin
    (B_k(z) - B_k(-z) on the symmetric knots), terms that change sign when the other team has
    the ball (spread, centred kickoff and home, timeout edge), and the possession terms, all
    zero when the half is about to end (drive_value, ``avail`` and its era interactions) or at
    1st and 10 (down, distance). So with seconds left outside field-goal range, WP does not
    depend on who has the ball (football: it should not). ``late_terms``: also the odd spline
    and the possession terms times ``late`` (the curve may sharpen as the game ends; every
    added column keeps the symmetry)."""
    from twm.modules.decisions.wp_data import MARGIN_SD

    def col(c: str) -> np.ndarray:
        return x.get_column(c).cast(pl.Float64).to_numpy()

    z = col("z_margin").reshape(-1, 1)
    odd = spline.transform(z) - spline.transform(-z)
    odd = odd[:, : odd.shape[1] // 2]  # the other half are the same columns negated
    t = np.clip(col("game_seconds_remaining") / 3600.0, 0.0, 1.0)
    late = 1.0 / (MARGIN_SD * np.sqrt(t) + 1.0)
    h1 = col("half_number") == 1
    down, lyd = col("down"), np.log(col("ydstogo") / 10.0)
    av = availability(x)
    lin = {
        "drive_value": col("drive_value"), "spread_time": col("spread_time"),
        "receives_2h_centred": np.where(h1, col("receives_2h_kickoff") - 0.5, 0.0),
        "home": col("posteam_is_home") - 0.5,
        "timeout_edge_late": (col("posteam_timeouts_remaining")
                              - col("defteam_timeouts_remaining")) * late,
        "avail": av, "avail_era_pat_2015": av * col("era_pat_2015"),
        "avail_era_kickoff_2023": av * col("era_kickoff_2023"),
        "overtime": (col("half_number") == 3).astype(float),
        "down2": (down == 2).astype(float), "down3": (down == 3).astype(float),
        "down4": (down == 4).astype(float), "log_ydstogo_10": lyd,
        "late_down_log_ydstogo_10": (down >= 3) * lyd,
    }  # fmt: skip
    cols = [odd, *(lin[k] for k in SYM_TERMS)]
    if late_terms:
        cols += [odd * late[:, None], lin["drive_value"] * late, av * late]
    return np.column_stack(cols)


class SymSplineLogit(SplineLogit):
    """:class:`SplineLogit` with the symmetric design :func:`sym_design` and no intercept."""

    name = "wp_spline_sym"
    fixed_params: Mapping[str, Any] = {"knots": Z_KNOTS, "terms": SYM_TERMS, "odd_spline": True,
                                       "intercept": False, "solver": "lbfgs",
                                       "max_iter": 2000}  # fmt: skip

    late_terms = False

    def fit(self, x: pl.DataFrame, y: np.ndarray, params: Mapping[str, Any], *,
            eval_x: pl.DataFrame | None = None, eval_y: np.ndarray | None = None):  # fmt: skip
        from sklearn.linear_model import LogisticRegression

        spline = make_spline()
        d = sym_design(x, spline, self.late_terms)
        sd = d.std(axis=0)
        sd[sd == 0] = 1.0
        lr = LogisticRegression(C=float(params["C"]), solver="lbfgs", max_iter=2000,
                                fit_intercept=False)  # fmt: skip
        lr.fit(d / sd, y)
        f = SplineLogitFitted(spline, lr, np.zeros_like(sd), sd, dict(params), symmetric=True)
        f.late_terms = self.late_terms
        return f


class SymSplineLateLogit(SymSplineLogit):
    """:class:`SymSplineLogit` plus the ``late`` interactions of :func:`sym_design`."""

    name = "wp_spline_sym_late"
    fixed_params: Mapping[str, Any] = {**SymSplineLogit.fixed_params, "late_terms": True}
    late_terms = True


class LgbmEnsembleFitted:
    """A fitted :class:`LgbmEnsemble`: the members' raw scores averaged (logit scale), plus the
    base model's logit when the trees were boosted from it."""

    importance_kind = "gain"

    def __init__(self, clfs: list[lgb.LGBMClassifier], features: Sequence[str], refit: dict,
                 base: SplineLogitFitted | None = None):  # fmt: skip
        self.clfs, self.features, self.refit_params, self.base = clfs, tuple(features), refit, base

    def margin(self, x: pl.DataFrame) -> np.ndarray:
        m = wpm._matrix(x, self.features)
        raw = [c.booster_.predict(m, raw_score=True, num_iteration=c.best_iteration_ or None)
               for c in self.clfs]  # fmt: skip
        out = np.mean(np.asarray(raw, dtype=np.float64), axis=0)
        return out + self.base.logit(x) if self.base is not None else out

    def predict(self, x: pl.DataFrame) -> np.ndarray:
        return _sigmoid(self.margin(x))

    def importance(self, x: pl.DataFrame) -> dict[str, float]:
        gain = np.mean([c.booster_.feature_importance(importance_type="gain")
                        for c in self.clfs], axis=0)  # fmt: skip
        return {f: float(g) for f, g in zip(self.features, gain, strict=True)}

    @property
    def n_trees(self) -> int:
        return int(sum(c.booster_.num_trees() for c in self.clfs))


class LgbmEnsemble:
    """G1's monotone LightGBM (:data:`wp.LGBM_FIXED`, grid :data:`wp.GRID` unless ``grid`` is
    given) as ``members`` models (member k: seed SEED + k, row share ``subsample`` per tree,
    feature share ``colsample`` per tree); ``linear_tree`` = linear models in the leaves;
    ``boost_from_spline`` = start every member from the logit of a :class:`SplineLogit` fit on
    the same rows (the trees then only correct it). Each member early-stops on its own while
    tuning; ``n_estimators`` of the refit is then one number per member."""

    def __init__(self, name: str, *, members: int = 1, subsample: float = 1.0,
                 colsample: float = 1.0, linear_tree: bool = False,
                 boost_from_spline: bool = False, symmetric_base: bool = False,
                 grid: Sequence[Mapping[str, Any]] | None = None):  # fmt: skip
        self.name, self.members = name, members
        self.subsample, self.colsample = subsample, colsample
        self.linear_tree, self.boost_from_spline = linear_tree, boost_from_spline
        self.symmetric_base = symmetric_base
        self._grid = [dict(g) for g in (grid if grid is not None else wpm.GRID)]
        self.default_params: Mapping[str, Any] = dict(wpm.DEFAULT_PARAMS)
        self.fixed_params: Mapping[str, Any] = {
            **wpm.LGBM_FIXED, "monotone": dict(MONOTONE), "max_trees": wpm.MAX_TREES,
            "early_stopping_rounds": wpm.EARLY_STOPPING_ROUNDS, "metric": "log loss",
            "members": members, "subsample": subsample, "colsample_bytree": colsample,
            "linear_tree": linear_tree, "boost_from_spline": boost_from_spline,
            **({"symmetric_base": True} if symmetric_base else {}),
        }  # fmt: skip

    def grid(self) -> list[dict[str, Any]]:
        return [dict(g) for g in self._grid]

    def _params(self, params: Mapping[str, Any], k: int, n_est: int) -> dict[str, Any]:
        p = {**wpm.LGBM_FIXED, **params, "n_estimators": n_est, "random_state": wpm.SEED + k,
             "subsample": self.subsample, "subsample_freq": 1 if self.subsample < 1 else 0,
             "colsample_bytree": self.colsample}  # fmt: skip
        if self.linear_tree:
            p.update(linear_tree=True, linear_lambda=1.0)
        return p

    def fit(self, x: pl.DataFrame, y: np.ndarray, params: Mapping[str, Any], *,
            eval_x: pl.DataFrame | None = None,
            eval_y: np.ndarray | None = None, init_score: np.ndarray | None = None,
            eval_init_score: np.ndarray | None = None) -> LgbmEnsembleFitted:  # fmt: skip
        """``init_score`` / ``eval_init_score``: start from these logits (a base fitted by the
        caller, which then adds it back itself: the fitted margin is the trees' only)."""
        features = tuple(x.columns)
        mono = [MONOTONE.get(f, 0) for f in features]
        base = None
        if self.boost_from_spline:
            cls = SymSplineLogit if self.symmetric_base else SplineLogit
            base = cls().fit(x, y, cls.default_params)
        n = params["n_estimators"]
        n_est = list(n) if isinstance(n, list | tuple) else [int(n)] * self.members
        xm = wpm._matrix(x, features)
        clfs = []
        for k in range(self.members):
            clf = lgb.LGBMClassifier(**self._params(params, k, n_est[k]), monotone_constraints=mono)
            kw: dict[str, Any] = {"feature_name": list(features)}
            if base is not None:
                kw["init_score"] = base.logit(x)
            elif init_score is not None:
                kw["init_score"] = init_score
            if eval_x is not None and eval_y is not None:
                stop = lgb.early_stopping(wpm.EARLY_STOPPING_ROUNDS, verbose=False)
                kw.update(eval_X=(wpm._matrix(eval_x, features),), eval_y=(eval_y,),
                          callbacks=[stop])  # fmt: skip
                if base is not None:
                    kw["eval_init_score"] = [base.logit(eval_x)]
                elif eval_init_score is not None:
                    kw["eval_init_score"] = [eval_init_score]
            clf.fit(xm, y, **kw)
            clfs.append(clf)
        refit = dict(params)
        if eval_x is not None:
            best = [int(c.best_iteration_ or c.n_estimators) for c in clfs]
            refit["n_estimators"] = best[0] if self.members == 1 else best
        return LgbmEnsembleFitted(clfs, features, refit, base)


class BlendFitted:
    """``weight`` x the ensemble's probability + (1 - ``weight``) x the spline model's."""

    importance_kind = "gain"

    def __init__(self, trees: LgbmEnsembleFitted, spline: SplineLogitFitted, weight: float):
        self.trees, self.spline, self.weight = trees, spline, weight
        self.refit_params = trees.refit_params

    def predict(self, x: pl.DataFrame) -> np.ndarray:
        w = self.weight
        return w * self.trees.predict(x) + (1.0 - w) * self.spline.predict(x)

    def importance(self, x: pl.DataFrame) -> dict[str, float]:
        return self.trees.importance(x)

    @property
    def n_trees(self) -> int:
        return self.trees.n_trees


class Blend:
    """A fixed-weight average of an :class:`LgbmEnsemble` and a :class:`SplineLogit` (both fit
    on the same rows; the grid and early stopping are the ensemble's)."""

    def __init__(self, name: str, trees: LgbmEnsemble, weight: float = 0.5,
                 spline: type[SplineLogit] | None = None):  # fmt: skip
        self.name, self.trees, self.weight = name, trees, weight
        self.spline = spline if spline is not None else SplineLogit
        self.default_params = trees.default_params
        self.fixed_params = {**trees.fixed_params, "blend_weight_trees": weight,
                             "spline": dict(self.spline.fixed_params)}  # fmt: skip

    def grid(self) -> list[dict[str, Any]]:
        return self.trees.grid()

    def fit(self, x: pl.DataFrame, y: np.ndarray, params: Mapping[str, Any], *,
            eval_x: pl.DataFrame | None = None,
            eval_y: np.ndarray | None = None) -> BlendFitted:  # fmt: skip
        t = self.trees.fit(x, y, params, eval_x=eval_x, eval_y=eval_y)
        s = self.spline().fit(x, y, self.spline.default_params)
        return BlendFitted(t, s, self.weight)


LATE_START = 900  # seconds left in the game: the late trees fade in over Q4 15:00 -> 10:00
LATE_FULL = 600


def late_gate(x: pl.DataFrame) -> np.ndarray:
    """0 in the first half and until 15:00 of the fourth quarter, then rising linearly to 1 at
    10:00 left; 1 in overtime."""
    half = x.get_column("half_number").to_numpy()
    gsr = x.get_column("game_seconds_remaining").cast(pl.Float64).to_numpy()
    ramp = np.clip((LATE_START - gsr) / (LATE_START - LATE_FULL), 0.0, 1.0)
    return np.where(half == 3, 1.0, np.where(half == 2, ramp, 0.0))


class LateTreesFitted:
    """``base`` logit + :func:`late_gate` x the late trees' margin."""

    importance_kind = "gain"

    def __init__(self, base: SplineLogitFitted, trees: LgbmEnsembleFitted, refit: dict):
        self.base, self.trees, self.refit_params = base, trees, refit

    def predict(self, x: pl.DataFrame) -> np.ndarray:
        g = late_gate(x)
        z = self.base.logit(x)
        if (g > 0).any():
            z = z + g * self.trees.margin(x)
        return _sigmoid(z)

    def importance(self, x: pl.DataFrame) -> dict[str, float]:
        return self.trees.importance(x)

    @property
    def n_trees(self) -> int:
        return self.trees.n_trees


class LateTrees:
    """:class:`SymSplineLateLogit` everywhere, plus a bag of monotone LightGBMs boosted from
    its logit on the plays with at most 15 minutes left in the game (and overtime), faded in by
    :func:`late_gate`. Late in a game the score's discreteness is real (a tie, a field goal, a
    touchdown), and that is where the spline model alone was worst on validation; the first
    half and the third quarter stay the smooth, possession-symmetric spline."""

    def __init__(self, name: str, trees: LgbmEnsemble):
        self.name, self.trees = name, trees
        self.default_params = trees.default_params
        self.fixed_params = {**trees.fixed_params, "late_start": LATE_START,
                             "late_full": LATE_FULL,
                             "base": dict(SymSplineLateLogit.fixed_params)}  # fmt: skip

    def grid(self) -> list[dict[str, Any]]:
        return self.trees.grid()

    def fit(self, x: pl.DataFrame, y: np.ndarray, params: Mapping[str, Any], *,
            eval_x: pl.DataFrame | None = None,
            eval_y: np.ndarray | None = None) -> LateTreesFitted:  # fmt: skip
        base = SymSplineLateLogit().fit(x, y, SymSplineLateLogit.default_params)
        late = late_gate(x) > 0
        kw: dict[str, Any] = {}
        if eval_x is not None and eval_y is not None:
            ev = late_gate(eval_x) > 0
            kw = {"eval_x": eval_x.filter(pl.Series(ev)), "eval_y": eval_y[ev],
                  "eval_init_score": base.logit(eval_x.filter(pl.Series(ev)))}  # fmt: skip
        xl = x.filter(pl.Series(late))
        trees = self.trees.fit(xl, y[late], params, init_score=base.logit(xl), **kw)
        return LateTreesFitted(base, trees, trees.refit_params)


class HandOverFitted:
    """(1 - g) x the spline model's probability + g x the trees', g = :func:`late_gate`."""

    importance_kind = "gain"

    def __init__(self, spline: SplineLogitFitted, trees: LgbmEnsembleFitted, refit: dict):
        self.spline, self.trees, self.refit_params = spline, trees, refit

    def predict(self, x: pl.DataFrame) -> np.ndarray:
        g = late_gate(x)
        p = self.spline.predict(x)
        if (g > 0).any():
            p = (1.0 - g) * p + g * self.trees.predict(x)
        return p

    def importance(self, x: pl.DataFrame) -> dict[str, float]:
        return self.trees.importance(x)

    @property
    def n_trees(self) -> int:
        return self.trees.n_trees


class HandOver:
    """:class:`SymSplineLateLogit` until 15:00 of the fourth quarter, then handing over (linearly,
    :func:`late_gate`) to a monotone LightGBM fit on every play (G1's settings and grid) by
    10:00 left, and in overtime. The late game keeps the trees' resolution of real score
    thresholds (a tie, a field goal, a touchdown); everything earlier is the smooth,
    possession-symmetric spline."""

    def __init__(self, name: str, trees: LgbmEnsemble):
        self.name, self.trees = name, trees
        self.default_params = trees.default_params
        self.fixed_params = {**trees.fixed_params, "late_start": LATE_START,
                             "late_full": LATE_FULL, "hand_over": True,
                             "spline": dict(SymSplineLateLogit.fixed_params)}  # fmt: skip

    def grid(self) -> list[dict[str, Any]]:
        return self.trees.grid()

    def fit(self, x: pl.DataFrame, y: np.ndarray, params: Mapping[str, Any], *,
            eval_x: pl.DataFrame | None = None,
            eval_y: np.ndarray | None = None) -> HandOverFitted:  # fmt: skip
        spline = SymSplineLateLogit().fit(x, y, SymSplineLateLogit.default_params)
        trees = self.trees.fit(x, y, params, eval_x=eval_x, eval_y=eval_y)
        return HandOverFitted(spline, trees, trees.refit_params)
