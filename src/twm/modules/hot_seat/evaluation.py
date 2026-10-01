"""Hot-Seat H3b metrics with season-block bootstrap intervals (PROJECT_SPEC 1.5, 8.5).

ROC-AUC, PR-AUC (average precision, scikit-learn's definition) and the Brier score of a slice of
rows, pooled over its seasons, and a 95% interval from resampling whole seasons with
replacement (:func:`twm.backtest.metrics.block_indices`: the same resamples for every method,
so differences are paired). A season drawn twice weighs twice: each resample is the metric
with per-row weights = how often the row's season was drawn, computed for all resamples at once
(the unweighted value equals scikit-learn's, tested). ``top5``: of the slice's positives, the
share in their season's top 5 by predicted risk.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from twm.backtest.metrics import LEVEL, N_BOOT, block_indices

CHUNK = 250  # resamples per matrix product (bounds memory on the all-rows slice)


@dataclass(frozen=True)
class Slice:
    """One slice of scored rows: label, probability and the season block of each row."""

    y: np.ndarray
    p: np.ndarray
    block: np.ndarray  # 0..n_blocks-1
    n_blocks: int

    @classmethod
    def of(cls, y, p, seasons) -> Slice:
        seasons = np.asarray(seasons)
        uniq, block = np.unique(seasons, return_inverse=True)
        return cls(np.asarray(y, dtype=np.float64), np.asarray(p, dtype=np.float64), block,
                   len(uniq))  # fmt: skip


def season_weights(n_blocks: int, n_boot: int = N_BOOT) -> np.ndarray:
    """(n_boot, n_blocks): how often each season is drawn in each resample."""
    idx = block_indices(n_blocks, n_boot)
    w = np.zeros((n_boot, n_blocks))
    for k in range(n_blocks):
        w[:, k] = (idx == k).sum(axis=1)
    return w


def _groups(s: Slice, descending: bool) -> tuple[np.ndarray, np.ndarray]:
    """(n_blocks, n_groups) positive and negative counts per distinct probability (sorted)."""
    vals, inv = np.unique(-s.p if descending else s.p, return_inverse=True)
    pos = np.zeros((s.n_blocks, len(vals)))
    neg = np.zeros((s.n_blocks, len(vals)))
    np.add.at(pos, (s.block, inv), s.y)
    np.add.at(neg, (s.block, inv), 1.0 - s.y)
    return pos, neg


def _auc(pos: np.ndarray, neg: np.ndarray) -> np.ndarray:
    """ROC-AUC from (B, G) weighted counts in ascending probability order."""
    below = np.cumsum(neg, axis=1) - neg
    num = (pos * (below + 0.5 * neg)).sum(axis=1)
    den = pos.sum(axis=1) * neg.sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, num / np.where(den > 0, den, 1.0), np.nan)


def _ap(pos: np.ndarray, neg: np.ndarray) -> np.ndarray:
    """Average precision from (B, G) weighted counts in descending probability order."""
    tp, fp = np.cumsum(pos, axis=1), np.cumsum(neg, axis=1)
    total = pos.sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        prec = np.where(tp + fp > 0, tp / np.where(tp + fp > 0, tp + fp, 1.0), 0.0)
        val = (pos * prec).sum(axis=1) / np.where(total > 0, total, 1.0)
    has_both = (total > 0) & (neg.sum(axis=1) > 0)
    return np.where(has_both, val, np.nan)


METRICS = ("roc_auc", "pr_auc", "brier")


def resampled(s: Slice, weights: np.ndarray) -> dict[str, np.ndarray]:
    """Every metric for each row of ``weights`` ((B, n_blocks) season weights)."""
    sq = np.zeros(s.n_blocks)
    cnt = np.zeros(s.n_blocks)
    np.add.at(sq, s.block, (s.p - s.y) ** 2)
    np.add.at(cnt, s.block, 1.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        brier = (weights @ sq) / (weights @ cnt)
    up_pos, up_neg = _groups(s, descending=False)
    dn_pos, dn_neg = _groups(s, descending=True)
    auc, ap = [], []
    for i in range(0, weights.shape[0], CHUNK):
        w = weights[i : i + CHUNK]
        auc.append(_auc(w @ up_pos, w @ up_neg))
        ap.append(_ap(w @ dn_pos, w @ dn_neg))
    return {"roc_auc": np.concatenate(auc), "pr_auc": np.concatenate(ap), "brier": brier}


@dataclass(frozen=True)
class Estimate:
    value: float | None
    lo: float | None
    hi: float | None
    boot: np.ndarray  # the resampled values (for paired differences)


def _estimate(value: float, boot: np.ndarray, level: float = LEVEL) -> Estimate:
    if not np.isfinite(value):
        return Estimate(None, None, None, boot)
    ok = boot[np.isfinite(boot)]
    if ok.size == 0:
        return Estimate(float(value), None, None, boot)
    tail = (1.0 - level) / 2.0
    lo, hi = np.quantile(ok, [tail, 1.0 - tail])
    return Estimate(float(value), float(lo), float(hi), boot)


def slice_metrics(s: Slice, n_boot: int = N_BOOT) -> dict[str, Estimate]:
    """ROC-AUC, PR-AUC and Brier with season-block intervals."""
    point = resampled(s, np.ones((1, s.n_blocks)))
    boot = resampled(s, season_weights(s.n_blocks, n_boot))
    return {m: _estimate(float(point[m][0]), boot[m]) for m in METRICS}


def difference(a: Estimate, b: Estimate) -> Estimate:
    """a - b with the paired interval (both from the same season resamples)."""
    if a.value is None or b.value is None:
        return Estimate(None, None, None, a.boot - b.boot)
    return _estimate(a.value - b.value, a.boot - b.boot)


def ratio(hits: np.ndarray, totals: np.ndarray, n_boot: int = N_BOOT) -> Estimate:
    """sum(hits) / sum(totals) over seasons (one entry each), season-block interval."""
    hits, totals = np.asarray(hits, dtype=np.float64), np.asarray(totals, dtype=np.float64)
    if totals.sum() == 0:
        return Estimate(None, None, None, np.array([]))
    w = season_weights(len(totals), n_boot)
    with np.errstate(invalid="ignore", divide="ignore"):
        boot = (w @ hits) / (w @ totals)
    return _estimate(float(hits.sum() / totals.sum()), boot)
