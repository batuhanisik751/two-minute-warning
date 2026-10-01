"""Smoothness of a win-probability surface (G1b): metrics on a fixed grid of synthetic states.

Every fourth-down and two-point grade (G3) is a DIFFERENCE between the WP of hypothetical
states, so the WP model must be locally sensible, not only calibrated. These checks run any
WP function over the same synthetic states (docs/decision_metrics.md, "WP smoothness"):

- ``score_step_h1`` / ``score_step_h2``: the largest WP change (WP points) for ONE point of
  score, over score lines -14..+14 in common first-half / second-half states (:data:`SCORE_TIMES`
  x :data:`SCORE_YARDLINES` x :data:`SCORE_SPREADS`, plus both second-half kickoff owners in
  the first half). Fourth quarter excluded after its first snap: one point can matter a lot there.
- ``curvature``: the largest |second difference| of WP along the same score lines (an
  isolated step makes it about as large as the step; a smooth curve keeps it under 1 point).
- ``halftime_possession``: the largest |value of having the ball| 1-10 seconds before halftime
  outside field-goal range: WP with the ball at its own 42 (or 25) minus WP when the opponent
  has it at ITS own 42 (25), same score and clock (football: about 0).
- ``monotone_violations``: adjacent grid pairs where WP falls by more than 0.1 point as the
  lead grows by one or the ball moves one yard closer to the end zone (football: none).
- ``yard_step`` (reported, no threshold): the largest WP change for one yard of field position.
- ``possession_2min`` (reported, no threshold; added in G1b after the first regrade): the mean
  value of the ball at its own 42 / 25, 30-120 seconds before halftime, tied (compare
  :func:`empirical_possession_2min`: what happened in 1999-2005).

:func:`measure` takes any ``predict(states) -> WP`` function (one row per state).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import polars as pl

# (game_seconds_remaining, half_number): Q1 10:00, Q2 15:00, Q2 5:00, Q3 13:20, Q3 5:00, Q4 15:00
SCORE_TIMES: tuple[tuple[int, int], ...] = (
    (3300, 1), (2700, 1), (2100, 1), (1700, 2), (1200, 2), (900, 2),
)  # fmt: skip
SCORE_YARDLINES = (75, 50, 30)  # own 25, midfield, opponent's 30
SCORE_SPREADS = (-3.5, 0.0, 3.5)  # the possession team's closing spread
SCORE_RANGE = tuple(range(-14, 15))
POSS_SECONDS = (1, 5, 10)  # seconds left in the first half
POSS_YARDLINES = (58, 75)  # own 42 / own 25: a field goal needs 76 / 93 yards
POSS_SCORES = (-7, -3, 0, 3, 7)
POSS_SPREADS = (0.0, 3.5)
YARD_TIMES: tuple[tuple[int, int], ...] = ((3300, 1), (2100, 1), (1500, 2))
YARD_SCORES = (-7, 0, 7)
MONOTONE_TOL = 0.001  # WP falls by more than 0.1 point = a violation
POSS2_SECONDS = (30, 60, 90, 120)  # the two-minute window before halftime (reported only)
METRICS = ("score_step_h1", "score_step_h2", "curvature", "halftime_possession",
           "monotone_violations", "yard_step", "possession_2min")  # fmt: skip
# Upper limits a WP model must meet to grade decisions (WP points; set in G1b BEFORE any fix
# was tried, from football and the nflfastR readings: docs/decision_metrics.md)
THRESHOLDS: dict[str, float] = {
    "score_step_h1": 6.0,
    "score_step_h2": 8.0,
    "curvature": 4.0,
    "halftime_possession": 3.0,
    "monotone_violations": 0,
}


def failures(m: dict[str, Any]) -> list[str]:
    """The thresholds a measurement breaks (empty = smooth enough to grade decisions)."""
    return [k for k, lim in THRESHOLDS.items() if not m[k] <= lim]


def worst_ratio(m: dict[str, Any]) -> float:
    """max(metric / threshold) over the thresholds (<= 1 = meets them all); a violation count
    above 0 counts as its count + 1."""
    r = [m[k] / lim if lim else (m[k] + 1.0 if m[k] else 0.0) for k, lim in THRESHOLDS.items()]
    return float(max(r))


def base_state(season: int, **over: Any) -> dict[str, Any]:
    """A neutral-site 1st and 10 with all timeouts, tied, even spread, first half."""
    s = dict(season=season, score_differential=0, game_seconds_remaining=3300,
             half_seconds_remaining=1500, half_number=1, posteam_timeouts_remaining=3,
             defteam_timeouts_remaining=3, receives_2h_kickoff=0, posteam_is_home=0.5,
             posteam_spread=0.0, down=1, ydstogo=10, yardline_100=75)  # fmt: skip
    s.update(over)
    s["half_seconds_remaining"] = s["game_seconds_remaining"] - (1800 if s["half_number"] == 1
                                                                 else 0)  # fmt: skip
    s["ydstogo"] = min(10, int(s["yardline_100"]))
    return s


def score_lines(season: int) -> pl.DataFrame:
    """Score lines: ``line`` id, ``half_number`` and the states, ordered by score within line."""
    rows, line = [], 0
    for gsr, half in SCORE_TIMES:
        for yl in SCORE_YARDLINES:
            for spread in SCORE_SPREADS:
                for r in (0, 1) if half == 1 else (0,):
                    for d in SCORE_RANGE:
                        rows.append({"line": line, **base_state(
                            season, game_seconds_remaining=gsr, half_number=half,
                            yardline_100=yl, posteam_spread=spread, receives_2h_kickoff=r,
                            score_differential=d)})  # fmt: skip
                    line += 1
    return pl.DataFrame(rows)


def possession_pairs(
    season: int, seconds: tuple[int, ...] = POSS_SECONDS, scores: tuple[int, ...] = POSS_SCORES
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(own, opp): the same moment before halftime with the ball (own) or with the opponent
    holding it at ITS own yardline (opp, seen from the opponent: :func:`submodels.other_side`)."""
    from twm.modules.decisions.submodels import other_side

    rows = [base_state(season, game_seconds_remaining=1800 + secs, half_number=1,
                       yardline_100=yl, score_differential=d, posteam_spread=sp,
                       receives_2h_kickoff=r)
            for secs in seconds for yl in POSS_YARDLINES for d in scores
            for sp in POSS_SPREADS for r in (0, 1)]  # fmt: skip
    own = pl.DataFrame(rows)
    return own, other_side(own)


def yard_lines(season: int) -> pl.DataFrame:
    """Field-position lines: yardline_100 1..99 (1st and 10, goal to go inside the 10)."""
    rows, line = [], 0
    for gsr, half in YARD_TIMES:
        for d in YARD_SCORES:
            rows += [{"line": line, **base_state(
                season, game_seconds_remaining=gsr, half_number=half, score_differential=d,
                yardline_100=yl)} for yl in range(1, 100)]  # fmt: skip
            line += 1
    return pl.DataFrame(rows)


def _by_line(frame: pl.DataFrame, w: np.ndarray) -> list[np.ndarray]:
    lines = frame.get_column("line").to_numpy()
    return [w[lines == k] for k in np.unique(lines)]


def measure(predict: Callable[[pl.DataFrame], np.ndarray], season: int) -> dict[str, Any]:
    """Every metric of the module docstring for one WP function (values in WP points, 0-100),
    with where the largest score step sits (``score_step_at``: the lower score of the pair,
    ``score_step_state``: clock/half/yardline/spread of that line)."""
    sl = score_lines(season)
    ws = np.asarray(predict(sl.drop("line")), dtype=np.float64)
    halves = sl.group_by("line", maintain_order=True).agg(pl.first("half_number"))
    half_of = dict(zip(*halves.get_columns(), strict=True))
    steps = {1: 0.0, 2: 0.0}
    worst: dict[str, Any] = {"step": -1.0}
    curv, viol = 0.0, 0
    for k, w in enumerate(_by_line(sl, ws)):
        d = np.diff(w)
        steps[half_of[k]] = max(steps[half_of[k]], float(np.abs(d).max()))
        curv = max(curv, float(np.abs(np.diff(d)).max()))
        viol += int((d < -MONOTONE_TOL).sum())
        if np.abs(d).max() > worst["step"]:
            i = int(np.abs(d).argmax())
            r = sl.filter(pl.col("line") == k).row(i, named=True)
            worst = {"step": float(np.abs(d).max()), "at": SCORE_RANGE[i], "state": (
                f"{r['game_seconds_remaining']} s left, half {r['half_number']}, yardline "
                f"{r['yardline_100']}, spread {r['posteam_spread']:+.1f}, "
                f"2H kick {r['receives_2h_kickoff']}")}  # fmt: skip
    own, opp = possession_pairs(season)
    poss = np.asarray(predict(own), np.float64) - (1.0 - np.asarray(predict(opp), np.float64))
    own2, opp2 = possession_pairs(season, POSS2_SECONDS, (0,))
    poss2 = np.asarray(predict(own2), np.float64) - (1.0 - np.asarray(predict(opp2), np.float64))
    yl = yard_lines(season)
    wy = np.asarray(predict(yl.drop("line")), dtype=np.float64)
    ysteps = [np.diff(w) for w in _by_line(yl, wy)]  # WP(yl + 1) - WP(yl): should be <= 0
    viol += sum(int((d > MONOTONE_TOL).sum()) for d in ysteps)
    return {
        "score_step_h1": 100 * steps[1], "score_step_h2": 100 * steps[2],
        "curvature": 100 * curv, "halftime_possession": 100 * float(np.abs(poss).max()),
        "halftime_possession_mean": 100 * float(poss.mean()),
        "monotone_violations": viol,
        "yard_step": 100 * max(float(np.abs(d).max()) for d in ysteps),
        "possession_2min": 100 * float(poss2.mean()),
        "score_step_at": worst["at"], "score_step_state": worst["state"],
    }  # fmt: skip


# --------------------------------------------------------------------------------------
# References: nflfastR's stored wp / vegas_wp read off real plays near each grid point
# --------------------------------------------------------------------------------------

REF_WINDOW = {"seconds": 300, "yards": 12, "spread": 3.0}
REF_MIN_PLAYS = 100  # a score level needs this many nearby plays to be read (fewer: noisy)
_EPS = 1e-4


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, _EPS, 1 - _EPS)
    return np.log(p / (1 - p))


def _local_fit(sub: pl.DataFrame, col: str, centre: dict[str, float], levels: str | None):
    """OLS of logit(``col``) on the deviations from ``centre`` (+ one dummy per level of
    ``levels``): returns (coefficients, names). Deviations are zero at the grid point, so the
    prediction there is the intercept plus the level's dummy."""
    y = _logit(sub.get_column(col).to_numpy())
    cols, names = [np.ones(sub.height)], ["const"]
    for c, v in centre.items():
        cols.append(sub.get_column(c).cast(pl.Float64).to_numpy() - v)
        names.append(c)
    if levels is not None:
        lv = sub.get_column(levels).to_numpy()
        for k in np.unique(lv)[1:]:
            cols.append((lv == k).astype(np.float64))
            names.append(f"{levels}={k}")
    beta = np.linalg.lstsq(np.column_stack(cols), y, rcond=None)[0]
    return dict(zip(names, beta, strict=True))


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-z))


def reference(rows: pl.DataFrame, col: str) -> dict[str, Any]:
    """The score-step, curvature and halftime-possession metrics of nflfastR's STORED ``col``
    (``wp`` or ``vegas_wp``) on the same grid, read off real plays (``rows``: G1 model rows):
    their model cannot be run on synthetic states offline, so each grid line is a local
    regression of logit(``col``) on plays near it (:data:`REF_WINDOW`: same half, 1st down,
    clock / yardline / spread within the window; one level per score with at least
    :data:`REF_MIN_PLAYS` plays). Steps are read only between adjacent scores both seen."""
    r = rows.filter(pl.col(col).is_not_null() & (pl.col("down") == 1)
                    & pl.col("score_differential").is_between(-14, 14))  # fmt: skip
    w = REF_WINDOW
    steps, curv, n = {1: [], 2: []}, [], 0
    for gsr, half in SCORE_TIMES:
        for yl in SCORE_YARDLINES:
            for sp in SCORE_SPREADS:
                for kick in (0, 1) if half == 1 else (0,):
                    sub = r.filter((pl.col("half_number") == half)
                                   & (pl.col("receives_2h_kickoff") == kick)
                                   & _near("game_seconds_remaining", gsr, w["seconds"])
                                   & _near("yardline_100", yl, w["yards"])
                                   & _near("posteam_spread", sp, w["spread"]))  # fmt: skip
                    counts = sub.group_by("score_differential").len()
                    keep = counts.filter(pl.col("len") >= REF_MIN_PLAYS)
                    sub = sub.filter(pl.col("score_differential").is_in(
                        keep.get_column("score_differential").implode()))  # fmt: skip
                    if keep.height < 2:
                        continue
                    n += sub.height
                    centre = {"game_seconds_remaining": gsr, "yardline_100": yl,
                              "posteam_spread": sp, "ydstogo": 10, "posteam_is_home": 0.5,
                              "posteam_timeouts_remaining": 3,
                              "defteam_timeouts_remaining": 3}  # fmt: skip
                    b = _local_fit(sub, col, centre, "score_differential")
                    lv = sorted(keep.get_column("score_differential").to_list())
                    p = {k: float(_sigmoid(b["const"] + b.get(f"score_differential={k}", 0.0)))
                         for k in lv}  # fmt: skip
                    d = [p[k + 1] - p[k] for k in lv if k + 1 in p]
                    steps[half] += [abs(x) for x in d]
                    curv += [abs(p[k + 1] - 2 * p[k] + p[k - 1]) for k in lv
                             if k + 1 in p and k - 1 in p]  # fmt: skip
    poss, n_poss = _reference_possession(rows, col)
    return {"score_step_h1": 100 * max(steps[1], default=np.nan),
            "score_step_h2": 100 * max(steps[2], default=np.nan),
            "curvature": 100 * max(curv, default=np.nan),
            "halftime_possession": 100 * float(np.abs(poss).max()),
            "halftime_possession_mean": 100 * float(poss.mean()),
            "n_plays": n, "n_plays_halftime": n_poss}  # fmt: skip


def _near(col: str, centre: float, width: float) -> pl.Expr:
    return (pl.col(col) - centre).abs() <= width


def _reference_possession(rows: pl.DataFrame, col: str) -> tuple[np.ndarray, int]:
    """The halftime-possession values of ``col`` on the possession grid, from first-half plays
    with at most 15 seconds left, the ball between the opponent's 45 and its own 10 and the
    score within 10: one regression of logit(``col``) on score, spread, second-half kickoff,
    home, yardline and clock (linear: there are only a few thousand such plays)."""
    sub = rows.filter(pl.col(col).is_not_null() & (pl.col("half_number") == 1)
                      & (pl.col("half_seconds_remaining") <= 15)
                      & pl.col("yardline_100").is_between(45, 90)
                      & pl.col("score_differential").is_between(-10, 10))  # fmt: skip
    centre = {"score_differential": 0.0, "posteam_spread": 0.0, "receives_2h_kickoff": 0.0,
              "posteam_is_home": 0.5, "yardline_100": 66.0,
              "half_seconds_remaining": 5.0}  # fmt: skip
    own, opp = possession_pairs(2006)  # the season does not enter
    if sub.height < REF_MIN_PLAYS:
        return np.full(own.height, np.nan), sub.height
    b = _local_fit(sub, col, centre, None)

    def f(st: pl.DataFrame) -> np.ndarray:
        z = np.full(st.height, b["const"])
        for c, v in centre.items():
            z += b[c] * (st.get_column(c).cast(pl.Float64).to_numpy() - v)
        return _sigmoid(z)

    return f(own) - (1.0 - f(opp)), sub.height


def empirical_possession_2min(rows: pl.DataFrame) -> tuple[float, float, float, int]:
    """What the ball was worth 30-120 seconds before halftime in real games: a logistic
    regression of ``posteam_wins`` on the spread, the score, the (centred) second-half kickoff
    and home, over first-half plays at the own 25-50 (yardline 50-75) within 10 points; the
    value of the ball = 2 x WP at the intercept - 1 (WP points, with a 95% interval)."""
    import statsmodels.api as smx

    sub = rows.filter((pl.col("half_number") == 1)
                      & pl.col("half_seconds_remaining").is_between(POSS2_SECONDS[0],
                                                                     POSS2_SECONDS[-1])
                      & pl.col("yardline_100").is_between(50, 75)
                      & pl.col("score_differential").is_between(-10, 10))  # fmt: skip
    if sub.height < REF_MIN_PLAYS:
        return float("nan"), float("nan"), float("nan"), sub.height
    x = sub.select(pl.col("posteam_spread"), pl.col("score_differential"),
                   pl.col("receives_2h_kickoff") - 0.5,
                   pl.col("posteam_is_home") - 0.5).to_numpy().astype(np.float64)  # fmt: skip
    fit = smx.Logit(sub.get_column("posteam_wins").to_numpy(), smx.add_constant(x)).fit(disp=0)
    c, se = float(fit.params[0]), float(fit.bse[0])

    def v(z: float) -> float:
        return 100.0 * (2.0 / (1.0 + np.exp(-np.clip(z, -30.0, 30.0))) - 1.0)

    return float(v(c)), float(v(c - 1.96 * se)), float(v(c + 1.96 * se)), sub.height
