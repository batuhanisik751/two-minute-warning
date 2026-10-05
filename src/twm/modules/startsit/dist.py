"""Rank -> points distributions and the odds that one player outscores another.

For a position and weekly rank r, the distribution is every training week's league points of
the players ranked within r +- k at that position, where k = 1 + floor(c * r) grows with the
rank (deeper ranks are noisier and sparser), cut symmetric at the top (rank 1 pools rank 1
only, rank 2 pools 1-3). ``c`` is chosen walk-forward (backtest.py). Ranks deeper than
:data:`RANK_CAP` are pooled into the cap rank.

A distribution is stored as :data:`N_ATOMS` equal-weight quantiles of the points of the ranked
players WHO HAD A STAT LINE, plus the share who did not (``dnp``: did not play, or played
without a stat). By default a player's distribution mixes both, as the history of his rank
did; with an explicit play chance p it is the played part with weight p and 0 points with
weight 1 - p (a missed game scores 0).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

RANK_CAP = {"QB": 40, "RB": 80, "WR": 100, "TE": 40}
N_ATOMS = 200
SMOOTHING_GRID = (0.0, 0.05, 0.1, 0.2, 0.3)


def half_window(rank: int, c: float) -> int:
    """The +- k of rank ``rank`` (symmetric: never more than rank - 1 above it)."""
    return min(1 + int(np.floor(c * rank)), rank - 1)


@dataclass(frozen=True)
class Dist:
    """A discrete distribution: sorted ``values`` with ``weights`` summing to 1."""

    values: np.ndarray
    weights: np.ndarray

    def quantile(self, q: float) -> float:
        cw = np.cumsum(self.weights)
        i = int(np.searchsorted(cw, q - 1e-12, side="left"))
        return float(self.values[min(i, len(self.values) - 1)])

    def mean(self) -> float:
        return float(np.dot(self.values, self.weights))


def make_dist(played_atoms: np.ndarray, dnp: float, play_chance: float | None = None) -> Dist:
    """The distribution of one rank: by default the history (``dnp`` share at 0 points); with
    ``play_chance`` p, the played atoms with weight p and 0 points with weight 1 - p."""
    p = 1.0 - float(dnp) if play_chance is None else float(play_chance)
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"a play chance must be within 0 and 1, not {p}")
    atoms = np.asarray(played_atoms, dtype=float)
    values = np.concatenate([[0.0], atoms])
    weights = np.concatenate([[1.0 - p], np.full(len(atoms), p / len(atoms))])
    order = np.argsort(values, kind="stable")
    return Dist(values[order], weights[order])


def p_greater(a: Dist, b: Dist) -> float:
    """P(A > B) for independent draws of ``a`` and ``b``; a tie counts one half."""
    cw = np.concatenate([[0.0], np.cumsum(b.weights)])
    lo = cw[np.searchsorted(b.values, a.values, side="left")]
    hi = cw[np.searchsorted(b.values, a.values, side="right")]
    return float(np.dot(a.weights, lo + 0.5 * (hi - lo)))


def atoms(points: np.ndarray, n: int = N_ATOMS) -> np.ndarray:
    """``n`` equal-weight quantiles of ``points`` (actual sample values: exact zeros stay)."""
    qs = (np.arange(n) + 0.5) / n
    return np.quantile(np.asarray(points, dtype=float), qs, method="inverted_cdf")


@dataclass(frozen=True)
class RankTable:
    """Every position's rank distributions: ``atoms[pos]`` (cap x N_ATOMS, row r-1 = rank r),
    ``dnp[pos]`` and ``n[pos]`` (the pooled rows behind each rank)."""

    c: float
    atoms: dict[str, np.ndarray]
    dnp: dict[str, np.ndarray]
    n: dict[str, np.ndarray]

    def rank_of(self, pos: str, rank: int) -> int:
        return max(1, min(int(rank), RANK_CAP[pos]))

    def dist(self, pos: str, rank: int, play_chance: float | None = None) -> Dist:
        i = self.rank_of(pos, rank) - 1
        return make_dist(self.atoms[pos][i], float(self.dnp[pos][i]), play_chance)

    def frame(self) -> pl.DataFrame:
        """Long form (pos, rank, n, dnp, atom, points): what is frozen."""
        parts = []
        for pos, m in self.atoms.items():
            ranks, k = m.shape
            parts.append(pl.DataFrame({
                "pos": [pos] * (ranks * k),
                "rank": np.repeat(np.arange(1, ranks + 1), k).astype(np.int32),
                "n": np.repeat(self.n[pos], k).astype(np.int32),
                "dnp": np.repeat(self.dnp[pos], k),
                "atom": np.tile(np.arange(k), ranks).astype(np.int32),
                "points": m.ravel(),
            }))  # fmt: skip
        return pl.concat(parts)

    @classmethod
    def from_frame(cls, df: pl.DataFrame, c: float) -> RankTable:
        a, d, n = {}, {}, {}
        for pos in RANK_CAP:
            sub = df.filter(pl.col("pos") == pos).sort("rank", "atom")
            ranks = sub["rank"].n_unique()
            a[pos] = sub["points"].to_numpy().reshape(ranks, -1)
            first = sub.filter(pl.col("atom") == 0)
            d[pos], n[pos] = first["dnp"].to_numpy(), first["n"].to_numpy()
        return cls(c, a, d, n)


def fit(history: pl.DataFrame, c: float, seasons: list[int] | None = None) -> RankTable:
    """The rank distributions from ``history`` rows (data.history; only ``seasons`` when given)
    with smoothing ``c``."""
    h = history if seasons is None else history.filter(pl.col("season").is_in(seasons))
    a, d, n = {}, {}, {}
    for pos, cap in RANK_CAP.items():
        sub = h.filter(pl.col("pos") == pos)
        rk = np.minimum(sub["pos_rank"].to_numpy(), cap)
        pts, played = sub["points"].to_numpy(), sub["played"].to_numpy()
        a[pos] = np.zeros((cap, N_ATOMS))
        d[pos], n[pos] = np.zeros(cap), np.zeros(cap, dtype=np.int64)
        for r in range(1, cap + 1):
            k = half_window(r, c)
            inside = (rk >= r - k) & (rk <= r + k)
            if not inside.any():
                raise ValueError(f"no {pos} rows ranked near {r}: cannot fit")
            pp = played[inside]
            n[pos][r - 1], d[pos][r - 1] = int(inside.sum()), float(1.0 - pp.mean())
            a[pos][r - 1] = atoms(pts[inside][pp] if pp.any() else np.zeros(1))
    return RankTable(float(c), a, d, n)
