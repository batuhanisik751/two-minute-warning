"""Hot-Seat H3b models (PROJECT_SPEC 8.5): two small models, LightGBM as a challenger, two
baselines, all trained walk-forward by :func:`twm.backtest.walkforward.walk_forward`.

**Rows**: :mod:`twm.modules.hot_seat.targets`; interim coaches never train (spec 8.5).

**Features** (:data:`FEATURES`, 18 registered H3a columns): the performance differences
(wins minus market-expected wins, point differential per game, Pythagorean minus actual wins),
neutral EPA and its trend, the context columns and the P2 link. Left out on purpose: the levels
that grow with the week (``reg_games_played``, ``reg_wins``, ``expected_wins``,
``pythagorean_wins``: their differences are in), ``tenure_censored`` (a data-coverage flag) and
``took_over_mid_season`` (always false on training rows: interims are excluded).

**Models** (estimators reused from the Waiver Radar, like the streamer):

- ``logit``: :class:`~twm.modules.waiver_radar.models.LogitEstimator` (median imputation +
  missing indicators, standardization) with an L2 penalty only, its C chosen inside the fit by
  an inner walk-forward over the last 4 training seasons (:class:`InnerCvLogit`; H3b-2: the
  harness's one-season choice of C and L1/L2 swung from fold to fold) on the season-level label
  ``y`` of every row;
- ``hazard``: the same estimator on the interval label ``event`` (a positive departure
  announced before the next row), plus a derived final-interval indicator
  (``games_remaining = 0``: the end-of-season row's interval to final game + 30 days). The
  season risk at an as-of with ``g`` games left = 1 - prod(1 - h) over the intervals still to
  come (``g, g-1, ..., 1`` and the final one), the features held at their current values;
- ``lgbm``: the Radar's LightGBM, single-threaded (the streamer's class), on ``y``; used only if
  it beats both walk-forward;
- baselines ``base_win_pct`` (win% to date, from ``reg_wins / reg_games_played``) and
  ``base_wins_vs_expected``: one-feature logistic regressions, same harness, same inner
  choice of C.

Probabilities: the models' own (``raw_score``) are the primary ones; the harness's isotonic
calibration (one validation season) is reported beside them for ``logit`` and ``lgbm``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import polars as pl

from twm.backtest.walkforward import RAW_SCORE
from twm.modules.streamer.models import StreamerLgbmEstimator
from twm.modules.waiver_radar.models import FittedPipeline, LogitEstimator

MODULE = "hot_seat"
KEYS = ("season", "snapshot", "week", "team", "coach_id")
FEATURES: tuple[str, ...] = (
    "wins_vs_expected", "point_diff_per_game", "pythag_minus_wins",
    "off_epa_neutral", "def_epa_neutral", "off_epa_neutral_trend", "def_epa_neutral_trend",
    "tenure_seasons", "is_first_year_coach", "is_second_year_coach", "prev_season_wins",
    "prev_playoff_round", "consecutive_losing_seasons", "division_rank", "games_remaining",
    "starting_qb_changes", "rookie_r1_qb_on_roster", "fourth_down_wp_lost_per_game",
)  # fmt: skip
FINAL_INTERVAL = "final_interval"  # derived inside the hazard model, never a stored column
EPS = 1e-12
# the Radar's L2 grid (l1_ratio 0), searched by the inner walk-forward of InnerCvLogit
L2_GRID: tuple[float, ...] = tuple(
    float(p["C"]) for p in LogitEstimator().grid() if float(p["l1_ratio"]) == 0.0
)
DEFAULT_C = float(LogitEstimator.default_params["C"])  # fewer than 2 inner seasons
INNER_SEASONS = 4
MIN_INNER_SEASONS = 2


def neg_log_loss(label: str) -> Callable[[pl.DataFrame], float | None]:
    """Tuning metric (higher is better): minus the mean log loss of RAW_SCORE on ``label``."""

    def metric(val: pl.DataFrame) -> float | None:
        if val.height == 0:
            return None
        y = val.get_column(label).cast(pl.Float64).to_numpy()
        p = np.clip(val.get_column(RAW_SCORE).to_numpy(), EPS, 1 - EPS)
        return float(np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))

    return metric


def add_final_interval(x: pl.DataFrame) -> pl.DataFrame:
    """The hazard model's inputs: the features plus 1.0 on the final interval."""
    return x.with_columns((pl.col("games_remaining") == 0).cast(pl.Float64).alias(FINAL_INTERVAL))


def win_pct(x: pl.DataFrame) -> pl.DataFrame:
    """The win% baseline's one input: wins to date / games to date."""
    return x.select((pl.col("reg_wins") / pl.col("reg_games_played")).alias("win_pct"))


@dataclass
class DerivedModel:
    """A fitted Radar logistic regression behind a fixed column transformation."""

    inner: FittedPipeline
    derive: Callable[[pl.DataFrame], pl.DataFrame]
    refit_params: dict[str, Any]
    importance_kind: str

    def predict(self, x: pl.DataFrame) -> np.ndarray:
        return self.inner.predict(self.derive(x))

    def importance(self, x: pl.DataFrame) -> dict[str, float]:
        return self.inner.importance(self.derive(x))


def _identity(x: pl.DataFrame) -> pl.DataFrame:
    return x


def inner_seasons(seasons: np.ndarray) -> list[int]:
    """The inner validation seasons of a training set: its last INNER_SEASONS seasons that
    have at least one earlier season to fit on."""
    return sorted({int(s) for s in seasons})[1:][-INNER_SEASONS:]


def season_log_loss(y: np.ndarray, p: np.ndarray) -> float:
    p = np.clip(p, EPS, 1 - EPS)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


class InnerCvLogit:
    """The Radar's :class:`LogitEstimator` with an L2 penalty only, on ``derive(x)``, whose C is
    chosen INSIDE ``fit`` by an inner walk-forward on the training rows alone: for each C of
    :data:`L2_GRID` and each inner season v (:func:`inner_seasons`: the last 4 training
    seasons, as many as exist in the earliest folds), fit on the training seasons before v,
    log loss on v; the C with the smallest sum over the v's (ties: the smaller C) is refit on
    every training row. Fewer than MIN_INNER_SEASONS inner seasons: :data:`DEFAULT_C`.

    The harness sees a one-point grid (no outer tuning). It hands ``fit`` feature columns only,
    so :meth:`bind` gives the estimator the training frame (sorted by the keys, like the
    harness): ``fit`` reads each row's season from it when ``x`` is exactly a season prefix of
    it (the harness's training and tuning sets); the fit itself uses ``x`` and ``y`` only."""

    def __init__(self, name: str, derive: Callable[[pl.DataFrame], pl.DataFrame] | None = None,
                 what: str | None = None) -> None:  # fmt: skip
        self.name, self.derive, self._base = name, derive or _identity, LogitEstimator()
        self.default_params: Mapping[str, Any] = {"C": DEFAULT_C, "l1_ratio": 0.0}
        self.fixed_params: Mapping[str, Any] = {
            **LogitEstimator.fixed_params, "penalty": "L2",
            "C": f"inner walk-forward over the last {INNER_SEASONS} training seasons, grid "
                 f"{list(L2_GRID)}, default {DEFAULT_C} below {MIN_INNER_SEASONS}",
            **({"derived": what} if what else {}),
        }  # fmt: skip
        self._seasons: np.ndarray | None = None
        self._ref: pl.DataFrame | None = None
        self._ends: set[int] = set()

    def bind(self, rows: pl.DataFrame, features: tuple[str, ...], keys: tuple[str, ...]) -> None:
        ref = rows.sort(list(keys))
        self._seasons = ref.get_column("season").to_numpy()
        self._ref = ref.select(list(features))
        change = np.flatnonzero(np.diff(self._seasons) != 0) + 1
        self._ends = {int(n) for n in change} | {len(self._seasons)}

    def seasons_of(self, x: pl.DataFrame) -> np.ndarray | None:
        """The season of each row of ``x`` when ``x`` is a season prefix of the bound frame."""
        n = x.height
        if self._ref is None or self._seasons is None or n not in self._ends:
            return None
        return self._seasons[:n] if x.equals(self._ref.head(n)) else None

    def grid(self) -> list[dict[str, Any]]:
        return [dict(self.default_params)]

    def _fit(self, dx: pl.DataFrame, y: np.ndarray, c: float) -> FittedPipeline:
        """One L2 fit on derived columns ``dx``."""
        return self._base.fit(dx, y, {"C": c, "l1_ratio": 0.0})

    def choose_c(
        self, x: pl.DataFrame, y: np.ndarray, seasons: np.ndarray | None
    ) -> tuple[float, dict[str, Any]]:
        """(C, how it was chosen): the inner walk-forward of the class docstring (``x``
        registered inputs, ``seasons`` the season of each of its rows, or None)."""
        vs: list[int] = []
        if seasons is not None:
            # an inner season counts only if the seasons before it hold both classes
            vs = [v for v in inner_seasons(seasons) if len(np.unique(y[seasons < v])) == 2]
        if len(vs) < MIN_INNER_SEASONS:
            why = "seasons unknown" if seasons is None else f"{len(vs)} inner season(s)"
            return DEFAULT_C, {"c_source": f"default ({why})", "inner_seasons": "",
                               "inner_log_loss": {}}  # fmt: skip
        loss, dx = dict.fromkeys(L2_GRID, 0.0), self.derive(x)
        for v in vs:
            before, on = pl.Series(seasons < v), seasons == v
            fit_x, fit_y = dx.filter(before), y[seasons < v]
            val_x, val_y = dx.filter(pl.Series(on)), y[on].astype(np.float64)
            for c in L2_GRID:
                loss[c] += season_log_loss(val_y, self._fit(fit_x, fit_y, c).predict(val_x))
        best = min(L2_GRID, key=lambda c: (loss[c], c))
        how = {"c_source": "inner walk-forward", "inner_seasons": ",".join(map(str, vs)),
               "inner_log_loss": loss}  # fmt: skip
        return best, how

    def fit(
        self,
        x: pl.DataFrame,
        y: np.ndarray,
        params: Mapping[str, Any],
        *,
        eval_x: pl.DataFrame | None = None,
        eval_y: np.ndarray | None = None,
    ) -> DerivedModel:
        """``params`` (the harness's one grid point) is not used: C is chosen here."""
        c, how = self.choose_c(x, y, self.seasons_of(x))
        inner = self._fit(self.derive(x), y, c)
        refit = {"C": c, "l1_ratio": 0.0, **how}
        return DerivedModel(inner, self.derive, refit, inner.importance_kind)


@dataclass(frozen=True)
class Spec:
    """One walk-forward model: estimator factory, input columns, training label."""

    name: str
    make: Callable[[], Any]
    features: tuple[str, ...]
    label: str
    kind: str  # "model" or "baseline"


SPECS: dict[str, Spec] = {
    "logit": Spec("logit", lambda: InnerCvLogit("logit"), FEATURES, "y", "model"),
    "hazard": Spec(
        "hazard",
        lambda: InnerCvLogit("hazard", add_final_interval, "final_interval = games_remaining 0"),
        FEATURES,
        "event",
        "model",
    ),
    "lgbm": Spec("lgbm", StreamerLgbmEstimator, FEATURES, "y", "model"),
    "base_win_pct": Spec(
        "base_win_pct",
        lambda: InnerCvLogit("base_win_pct", win_pct, "win_pct = reg_wins / reg_games_played"),
        ("reg_wins", "reg_games_played"),
        "y",
        "baseline",
    ),
    "base_wins_vs_expected": Spec(
        "base_wins_vs_expected",
        lambda: InnerCvLogit("base_wins_vs_expected"),
        ("wins_vs_expected",),
        "y",
        "baseline",
    ),
}


def season_risk(model: Any, x: pl.DataFrame) -> np.ndarray:
    """Hazard -> season risk per row: 1 - prod(1 - h) over the intervals still to come
    (``games_remaining`` = g, g-1, ..., 1, then 0 = the final interval), the other features
    held at the row's values (one interval per game left: a bye week's extra interval is not
    counted). An end-of-season row (g = 0) gets its final-interval hazard."""
    g = x.get_column("games_remaining").fill_null(0).clip(0).to_numpy().astype(np.int64)
    reps = g + 1
    idx = np.repeat(np.arange(x.height), reps)
    offsets = np.arange(reps.sum()) - np.repeat(np.cumsum(reps) - reps, reps)
    expanded = x[idx].with_columns(pl.Series("games_remaining", g[idx] - offsets))
    h = np.clip(model.predict(expanded), 0.0, 1.0 - EPS)
    log_surv = np.zeros(x.height)
    np.add.at(log_surv, idx, np.log1p(-h))
    return 1.0 - np.exp(log_surv)


def coefficients(model: Any) -> list[tuple[str, float]]:
    """(input column, standardized coefficient) of a fitted logistic model, intercept first;
    ``missingindicator_x`` = the indicator that x was missing."""
    inner = model.inner if isinstance(model, DerivedModel) else model
    pipe = inner.pipeline
    names = [n.split("__", 1)[1] for n in pipe.named_steps["prep"].get_feature_names_out()]
    clf = pipe.named_steps["clf"]
    return [("intercept", float(clf.intercept_[0]))] + [
        (n, float(c)) for n, c in zip(names, clf.coef_[0], strict=True)
    ]
