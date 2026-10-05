"""The honest check: walk-forward by season (train 2020..s-1, test s = 2022..2025).

Start/sit-like pairs of one week: two players of one position ranked 1-24 apart (both within
:data:`PAIR_CAP`; A is the higher-ranked), plus FLEX pairs (RB/WR/TE of two positions, both
within :data:`FLEX_CAP`, whose ranks' training mean points are within :data:`FLEX_CLOSE`;
A has the higher mean). The model's P(A > B) is scored against what happened (A more points:
1, a tie: 1/2, else 0) next to (i) "the higher-ranked wins" at the training seasons' rate and
(ii) a coin flip, by Brier score and log loss. Each fold picks the smoothing ``c`` on its own
last training season (fit on the seasons before it), so the test season is never used.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from twm.modules.startsit.dist import RANK_CAP, SMOOTHING_GRID, RankTable, fit

PAIR_CAP = {"QB": 30, "RB": 60, "WR": 72, "TE": 30}
FLEX_CAP = {"RB": 48, "WR": 60, "TE": 24}
FLEX_CLOSE = 2.0  # points: the two ranks' training means at most this far apart
MAX_GAP = 24
FIRST_TRAIN, TEST_SEASONS = 2020, (2022, 2023, 2024, 2025)
BUCKETS = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85)
GAP_BINS = ((1, 1), (2, 3), (4, 6), (7, 12), (13, 24))
_KEYS = ["season", "week"]
_COLS = ["pos", "pos_rank", "points", "team", "fantasypros_id"]


def odds_matrix(table: RankTable, pa: str, pb: str, na: int, nb: int) -> np.ndarray:
    """P(A > B) for A ranked 1..na at ``pa`` and B ranked 1..nb at ``pb`` (default play)."""
    out = np.zeros((na, nb))
    da = [table.dist(pa, r) for r in range(1, na + 1)]
    va, wa = np.stack([d.values for d in da]), np.stack([d.weights for d in da])
    for j in range(nb):
        b = table.dist(pb, j + 1)
        cw = np.concatenate([[0.0], np.cumsum(b.weights)])
        lo = cw[np.searchsorted(b.values, va, side="left")]
        hi = cw[np.searchsorted(b.values, va, side="right")]
        out[:, j] = (wa * (lo + 0.5 * (hi - lo))).sum(axis=1)
    return out


def _outcome(a: pl.Expr, b: pl.Expr) -> pl.Expr:
    return pl.when(a > b).then(1.0).when(a == b).then(0.5).otherwise(0.0)


def same_pos_pairs(h: pl.DataFrame) -> pl.DataFrame:
    """Every same-position pair of a week, 1-24 ranks apart, both within PAIR_CAP."""
    cap = pl.col("pos").replace_strict(PAIR_CAP, default=0)
    x = h.filter(pl.col("pos_rank") <= cap).select(*_KEYS, *_COLS)
    p = x.join(x, on=[*_KEYS, "pos"], suffix="_b").rename(
        {c: f"{c}_a" for c in _COLS if c != "pos"})  # fmt: skip
    gap = pl.col("pos_rank_b") - pl.col("pos_rank_a")
    p = p.filter((gap >= 1) & (gap <= MAX_GAP))
    return p.with_columns(
        pl.col("pos").alias("pos_b"), gap.alias("gap"), pl.lit("same").alias("kind"),
        (pl.col("team_a") == pl.col("team_b")).alias("teammates"),
        _outcome(pl.col("points_a"), pl.col("points_b")).alias("y"),
    ).rename({"pos": "pos_a"})  # fmt: skip


def flex_pairs(h: pl.DataFrame, table: RankTable) -> pl.DataFrame:
    """RB/WR/TE pairs of two positions whose ranks' training means are within FLEX_CLOSE."""
    means = pl.DataFrame([
        {"pos": pos, "pos_rank": r, "mean": table.dist(pos, r).mean()}
        for pos, cap in FLEX_CAP.items() for r in range(1, cap + 1)
    ]).with_columns(pl.col("pos_rank").cast(h.schema["pos_rank"]))  # fmt: skip
    x = h.select(*_KEYS, *_COLS).join(means, on=["pos", "pos_rank"])
    p = x.join(x, on=_KEYS, suffix="_b").filter(
        (pl.col("pos") < pl.col("pos_b")) & ((pl.col("mean") - pl.col("mean_b")).abs()
                                             <= FLEX_CLOSE))  # fmt: skip
    flip = pl.col("mean_b") > pl.col("mean")
    side = {c: (pl.when(flip).then(pl.col(f"{c}_b")).otherwise(pl.col(c)).alias(f"{c}_a"),
                pl.when(flip).then(pl.col(c)).otherwise(pl.col(f"{c}_b")).alias(f"{c}_b"))
            for c in _COLS}  # fmt: skip
    p = p.select(*_KEYS, *[e for pair in side.values() for e in pair])
    return p.with_columns(
        pl.lit(None, dtype=pl.Int32).alias("gap"), pl.lit("flex").alias("kind"),
        (pl.col("team_a") == pl.col("team_b")).alias("teammates"),
        _outcome(pl.col("points_a"), pl.col("points_b")).alias("y"),
    )  # fmt: skip


PAIR_COLUMNS = ["season", "week", "kind", "pos_a", "pos_b", "pos_rank_a", "pos_rank_b", "gap",
                "teammates", "y"]  # fmt: skip


def predict(pairs: pl.DataFrame, table: RankTable) -> pl.DataFrame:
    """``pairs`` (PAIR_COLUMNS) with the model's ``p`` = P(A > B)."""
    out = []
    for (pa, pb), g in pairs.group_by("pos_a", "pos_b", maintain_order=True):
        ra = np.minimum(g["pos_rank_a"].to_numpy(), RANK_CAP[pa])
        rb = np.minimum(g["pos_rank_b"].to_numpy(), RANK_CAP[pb])
        m = odds_matrix(table, pa, pb, int(ra.max()), int(rb.max()))
        out.append(g.with_columns(pl.Series("p", m[ra - 1, rb - 1])))
    keys = ["season", "week", "kind", "pos_a", "pos_b", "pos_rank_a", "pos_rank_b"]
    return pl.concat(out).sort(keys, maintain_order=True)


def week_pairs(h: pl.DataFrame, table: RankTable) -> pl.DataFrame:
    """The same-position and FLEX pairs of ``h``'s weeks, with the model's ``p``."""
    both = pl.concat([same_pos_pairs(h).select(PAIR_COLUMNS),
                      flex_pairs(h, table).select(PAIR_COLUMNS)])  # fmt: skip
    return predict(both, table)


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def log_loss(p: np.ndarray, y: np.ndarray) -> float:
    q = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q)))


def choose_c(history: pl.DataFrame, train: list[int]) -> tuple[float, dict[float, float]]:
    """The smoothing with the lowest log loss on ``train``'s last season, fit on the seasons
    before it (``train`` needs two seasons or more; ties go to the smaller c)."""
    if len(train) < 2:
        raise ValueError("choosing the smoothing needs two training seasons or more")
    fit_on, check = train[:-1], train[-1]
    held = history.filter(pl.col("season") == check)
    losses = {}
    for c in SMOOTHING_GRID:
        pr = week_pairs(held, fit(history, c, fit_on))
        losses[c] = log_loss(pr["p"].to_numpy(), pr["y"].to_numpy())
    return min(losses, key=lambda c: (losses[c], c)), losses


def higher_rank_rate(history: pl.DataFrame, table: RankTable, seasons: list[int]) -> dict:
    """Baseline (i): how often A (the higher-ranked; FLEX: the higher mean) won in ``seasons``."""
    pr = week_pairs(history.filter(pl.col("season").is_in(seasons)), table)
    return {k: float(g["y"].mean()) for (k,), g in pr.group_by("kind")}


def walk_forward(history: pl.DataFrame, test_seasons=TEST_SEASONS, progress=None) -> dict:
    """Every test season's pairs with the model's ``p`` and baseline (i)'s ``p_rank``, and the
    fold choices (season, c, the inner log losses, the training higher-rank rates)."""
    preds, folds = [], []
    for s in test_seasons:
        train = list(range(FIRST_TRAIN, s))
        c, losses = choose_c(history, train)
        table = fit(history, c, train)
        rates = higher_rank_rate(history, table, train)
        pr = week_pairs(history.filter(pl.col("season") == s), table)
        pr = pr.with_columns(pl.col("kind").replace_strict(rates).alias("p_rank"))
        preds.append(pr)
        folds.append({"season": s, "c": c, "train": f"{train[0]}-{train[-1]}",
                      "rate_same": rates["same"], "rate_flex": rates["flex"],
                      **{f"inner_ll_c{k:g}": v for k, v in losses.items()}})  # fmt: skip
        if progress:
            progress(f"fold {s}: c {c:g}, {pr.height:,} pairs")
    return {"pairs": pl.concat(preds), "folds": pl.DataFrame(folds)}


def _scores(g: pl.DataFrame) -> dict:
    p, q, y = g["p"].to_numpy(), g["p_rank"].to_numpy(), g["y"].to_numpy()
    half = np.full(len(y), 0.5)
    return {"pairs": len(y), "brier_model": brier(p, y), "brier_rank_rate": brier(q, y),
            "brier_coin": brier(half, y), "logloss_model": log_loss(p, y),
            "logloss_rank_rate": log_loss(q, y), "logloss_coin": log_loss(half, y),
            "mean_p": float(p.mean()), "a_won": float(y.mean())}  # fmt: skip


def metrics(pairs: pl.DataFrame) -> pl.DataFrame:
    """Brier and log loss of the model and both baselines: by season x kind, and pooled."""
    rows = []
    for (s, k), g in sorted(pairs.group_by("season", "kind"), key=lambda t: t[0]):
        rows.append({"season": str(s), "kind": k, **_scores(g)})
    for k in ("same", "flex"):
        rows.append({"season": "all", "kind": k, **_scores(pairs.filter(pl.col("kind") == k))})
    rows.append({"season": "all", "kind": "all", **_scores(pairs)})
    return pl.DataFrame(rows)


def calibration(pairs: pl.DataFrame) -> pl.DataFrame:
    """The favourite's predicted chance (max(p, 1 - p)) in 5-point buckets vs how often it won."""
    fav = pairs.with_columns(
        pl.max_horizontal("p", 1 - pl.col("p")).alias("pf"),
        pl.when(pl.col("p") >= 0.5).then(pl.col("y")).otherwise(1 - pl.col("y")).alias("yf"),
    )  # fmt: skip
    rows = []
    for i, lo in enumerate(BUCKETS):
        hi = BUCKETS[i + 1] if i + 1 < len(BUCKETS) else 1.01
        g = fav.filter((pl.col("pf") >= lo) & (pl.col("pf") < hi))
        label = f"{lo * 100:.0f}-{hi * 100:.0f}%" if hi <= 1 else f"{lo * 100:.0f}%+"
        rows.append({"bucket": label, "pairs": g.height,
                     "predicted": float(g["pf"].mean()) if g.height else None,
                     "actual": float(g["yf"].mean()) if g.height else None})  # fmt: skip
    return pl.DataFrame(rows)


def rank_gap(history: pl.DataFrame) -> pl.DataFrame:
    """Beginner table, every season of ``history``: how often the higher-ranked of two players
    at one position outscored the other, by how many ranks apart they were (ties: one half)."""
    p = same_pos_pairs(history)
    rows = []
    for lo, hi in GAP_BINS:
        g = p.filter((pl.col("gap") >= lo) & (pl.col("gap") <= hi))
        label = f"{lo}" if lo == hi else f"{lo}-{hi}"
        row = {"rank_gap": label, "pairs": g.height, "higher_won": float(g["y"].mean())}
        for pos in RANK_CAP:
            gp = g.filter(pl.col("pos_a") == pos)
            row[pos] = float(gp["y"].mean()) if gp.height else None
        rows.append(row)
    return pl.DataFrame(rows)


def teammates(pairs: pl.DataFrame) -> pl.DataFrame:
    """The independence assumption on teammates (same team, same week) vs everyone else: the
    favourite's mean predicted chance vs how often it won, and the model's Brier score."""
    rows = []
    for flag, g in sorted(pairs.group_by("teammates"), key=lambda t: t[0]):
        fav_y = np.where(g["p"].to_numpy() >= 0.5, g["y"].to_numpy(), 1 - g["y"].to_numpy())
        pf = np.maximum(g["p"].to_numpy(), 1 - g["p"].to_numpy())
        rows.append({"teammates": bool(flag[0]), "pairs": g.height,
                     "favourite_predicted": float(pf.mean()), "favourite_won": float(fav_y.mean()),
                     "brier_model": brier(g["p"].to_numpy(), g["y"].to_numpy()),
                     "brier_coin": brier(np.full(g.height, 0.5), g["y"].to_numpy())})  # fmt: skip
    return pl.DataFrame(rows)
