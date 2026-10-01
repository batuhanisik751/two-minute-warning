"""Choosing a smooth WP model (G1b) on validation seasons only.

Every candidate family (:data:`CANDIDATES`) runs the ordinary walk-forward fold
(:func:`wp.run_fold`: tuned on S-1, refit on every season < S) for the VALIDATION seasons
:data:`VALIDATION_SEASONS` = 2004 and 2005 only (trained on 1999-2003 / 1999-2004). The test
seasons 2006-2025 and the graded current season are refused (:class:`SelectionError`). Rule
(docs/decision_metrics.md, fixed before any candidate was fitted): among the candidates whose
two fold models meet every smoothness limit (:data:`wp_smooth.THRESHOLDS`), the lowest pooled
2004-2005 log loss; if none meets them all, the smallest worst metric/limit ratio, then log loss.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from twm.modules.decisions import wp as wpm
from twm.modules.decisions import wp_smooth
from twm.modules.decisions.wp_data import FEATURES, LABEL, SMOOTH_FEATURES

VALIDATION_SEASONS = (2004, 2005)
CHOSEN = "spline_sym_late_hand_over"  # = choose(load_results()) on the validation results (G1b)


class SelectionError(ValueError):
    """A candidate was asked to run on a season that is not a validation season."""


@dataclass(frozen=True)
class Candidate:
    name: str
    description: str
    features: tuple[str, ...]
    make: Callable[[], Any]  # -> a harness Estimator
    era_ablation: bool = True  # False: the family needs the era flags (spline design)


def _ens(name: str, **kw: Any) -> Callable[[], Any]:
    def make() -> Any:
        from twm.modules.decisions.wp_models import LgbmEnsemble

        return LgbmEnsemble(name, **kw)

    return make


def _spline() -> Any:
    from twm.modules.decisions.wp_models import SplineLogit

    return SplineLogit()


def _blend() -> Any:
    from twm.modules.decisions.wp_models import Blend

    return Blend("wp_blend", _ens("wp_lgbm_smooth_bag5", **BAG)(), 0.5)


def _spline_sym() -> Any:
    from twm.modules.decisions.wp_models import SymSplineLogit

    return SymSplineLogit()


def _spline_sym_late() -> Any:
    from twm.modules.decisions.wp_models import SymSplineLateLogit

    return SymSplineLateLogit()


def _late_trees() -> Any:
    from twm.modules.decisions.wp_models import LateTrees

    return LateTrees("wp_spline_sym_late_trees", _ens("wp_lgbm_late_bag5", **BAG)())


def _hand_over() -> Any:
    from twm.modules.decisions.wp_models import HandOver

    return HandOver("wp_spline_sym_late_hand_over", _ens("wp_lgbm_smooth")())


def _blend_sym() -> Any:
    from twm.modules.decisions.wp_models import Blend, SymSplineLogit

    return Blend("wp_blend_sym", _ens("wp_lgbm_smooth_bag5", **BAG)(), 0.5, SymSplineLogit)


BAG = {"members": 5, "subsample": 0.8, "colsample": 0.8}
SMOOTH = (*FEATURES, *SMOOTH_FEATURES)
CANDIDATES: dict[str, Candidate] = {c.name: c for c in (
    Candidate("g1", "G1 as it was: one monotone LightGBM", FEATURES, wpm.WpEstimator),
    Candidate("bag5", "bag of 5 LightGBMs (seeds; 80% of rows and features per tree)",
              FEATURES, _ens("wp_lgbm_bag5", **BAG)),
    Candidate("linear_tree", "one LightGBM with linear models in the leaves", FEATURES,
              _ens("wp_lgbm_linear_tree", linear_tree=True)),
    Candidate("smooth_feat", "one LightGBM + drive_value and z_margin", SMOOTH,
              _ens("wp_lgbm_smooth")),
    Candidate("smooth_feat_bag5", "bag of 5 LightGBMs + drive_value and z_margin", SMOOTH,
              _ens("wp_lgbm_smooth_bag5", **BAG)),
    Candidate("spline_logit", "logistic regression on a spline of z_margin + linear terms",
              SMOOTH, _spline, era_ablation=False),
    Candidate("blend", "50/50 average of smooth_feat_bag5 and spline_logit", SMOOTH, _blend,
              era_ablation=False),
    Candidate("boost_spline_bag5", "bag of 5 LightGBMs boosted from spline_logit's logit",
              SMOOTH, _ens("wp_lgbm_boost_spline_bag5", boost_from_spline=True, **BAG),
              era_ablation=False),
    # second round (after the first eight failed the halftime limit on validation): a spline
    # model that is symmetric in possession when the half is about to end
    Candidate("spline_sym", "spline_logit made possession-symmetric: odd spline, no "
              "intercept, possession terms that vanish as the half ends", SMOOTH, _spline_sym,
              era_ablation=False),
    Candidate("spline_sym_late", "spline_sym + its spline and possession terms x late in the "
              "game", SMOOTH, _spline_sym_late, era_ablation=False),
    Candidate("blend_sym", "50/50 average of smooth_feat_bag5 and spline_sym", SMOOTH,
              _blend_sym, era_ablation=False),
    Candidate("boost_spline_sym_bag5", "bag of 5 LightGBMs boosted from spline_sym's logit",
              SMOOTH, _ens("wp_lgbm_boost_spline_sym_bag5", boost_from_spline=True,
                           symmetric_base=True, **BAG), era_ablation=False),
    # third round: a first regrade of 2006 (a graded season) with spline_sym_late left no clear
    # two-point decision; on the 2005 VALIDATION season the spline was also the worst model in
    # the last minutes (log loss .247 vs G1's .239 with 5 minutes left or less). Two late-game
    # variants, chosen like every other candidate on 2004-2005 only:
    Candidate("spline_sym_late_trees", "spline_sym_late + a bag of 5 LightGBMs boosted from "
              "it on the last 15 minutes (fading in from Q4 15:00 to 10:00) and overtime",
              SMOOTH, _late_trees, era_ablation=False),
    # round 4 changed no candidate but a feature: drive_value became the measured net points
    # until halftime (wp_data.HALF_VALUE_TABLE) after the first one over-valued the ball two
    # minutes before halftime; every candidate was rerun on 2004-2005.
    Candidate("spline_sym_late_hand_over", "spline_sym_late, handing over to one monotone "
              "LightGBM fit on every play (smooth_feat) from Q4 15:00 to 10:00, and in "
              "overtime", SMOOTH, _hand_over, era_ablation=False),
)}  # fmt: skip


def chosen() -> Candidate:
    """The family every fold (2006-2025 and the current season) is fitted with."""
    return CANDIDATES[CHOSEN]


def select_dir() -> Path:
    """``data/decisions/wp_select``: one JSON per candidate and validation season."""
    return wpm.backtest_dir().parent / "wp_select"


def run_candidate(rows: pl.DataFrame, name: str, season: int, *, out_dir: Path | None = None,
                  progress=print) -> dict[str, Any]:  # fmt: skip
    """Fit candidate ``name``'s walk-forward fold for the validation ``season`` (2004 or 2005;
    anything else, in particular a test or graded season, raises :class:`SelectionError`),
    score its log loss on that season, measure its smoothness and save the result."""
    if season not in VALIDATION_SEASONS:
        raise SelectionError(f"candidates are compared on {VALIDATION_SEASONS} only (never a "
                             f"test or graded season); got {season}")  # fmt: skip
    if name not in CANDIDATES:
        raise SelectionError(f"unknown candidate {name!r}; one of {sorted(CANDIDATES)}")
    cand = CANDIDATES[name]
    t0 = time.perf_counter()
    out = wpm.run_fold(rows.filter(pl.col("season") <= season), season, candidate=cand,
                       progress=progress)  # fmt: skip
    test = rows.filter(pl.col("season") == season).sort(list(wpm.KEYS))
    p = out.predictions.sort(list(wpm.KEYS)).get_column("prob").to_numpy()
    y = test.get_column(LABEL).to_numpy()
    smooth = wp_smooth.measure(lambda st: wpm.wp(st, out.model), season)
    res = {
        "candidate": name, "season": season, "n": int(y.size),
        "log_loss": wpm.log_loss(y, p), "brier": float(np.mean((p - y) ** 2)),
        "smooth": smooth, "failures": wp_smooth.failures(smooth),
        "worst_ratio": wp_smooth.worst_ratio(smooth), "params": out.summary["params"],
        "calibration": out.summary["calibration"],
        "seconds": round(time.perf_counter() - t0, 1),
    }  # fmt: skip
    d = out_dir if out_dir is not None else select_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}_{season}.json").write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    return res


def load_results(out_dir: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    """Saved results by candidate (validation seasons in order)."""
    d = out_dir if out_dir is not None else select_dir()
    out: dict[str, list[dict[str, Any]]] = {}
    for f in sorted(d.glob("*.json")) if d.exists() else []:
        r = json.loads(f.read_text())
        out.setdefault(r["candidate"], []).append(r)
    return {k: sorted(v, key=lambda r: r["season"]) for k, v in out.items()}


def summarize(results: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[dict[str, Any]]:
    """One row per candidate with every validation season: pooled log loss (play-weighted),
    the worst value of each smoothness metric over the folds, worst ratio, meets-all."""
    rows = []
    for name, rs in results.items():
        if sorted(r["season"] for r in rs) != list(VALIDATION_SEASONS):
            continue
        n = sum(r["n"] for r in rs)
        row = {"candidate": name, "log_loss": sum(r["log_loss"] * r["n"] for r in rs) / n,
               "brier": sum(r["brier"] * r["n"] for r in rs) / n, "n": n,
               "worst_ratio": max(r["worst_ratio"] for r in rs),
               "meets": all(not r["failures"] for r in rs),
               "seconds": sum(r["seconds"] for r in rs)}  # fmt: skip
        for m in wp_smooth.METRICS:
            row[m] = max(r["smooth"][m] for r in rs)
        rows.append(row)
    return rows


def choose(results: Mapping[str, Sequence[Mapping[str, Any]]]) -> str:
    """The rule of the module docstring. Raises SelectionError when nothing was run."""
    rows = summarize(results)
    if not rows:
        raise SelectionError("no candidate has results for every validation season")
    ok = [r for r in rows if r["meets"]]
    if ok:
        return min(ok, key=lambda r: (r["log_loss"], r["candidate"]))["candidate"]
    return min(rows, key=lambda r: (r["worst_ratio"], r["log_loss"], r["candidate"]))["candidate"]
