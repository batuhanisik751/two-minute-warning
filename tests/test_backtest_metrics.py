"""Backtest metrics (C4): ranking with ties and missing scores, precision@k by hand (including
a list shorter than k), pooling, rank-bucket hit rates, PR-AUC, Brier and calibration bins."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from twm.backtest import metrics as m


def _rows(group: str, scores: list[float | None], labels: list[int]) -> list[dict]:
    return [
        {"g": group, "id": f"p{i:02d}", "score": s, "y": bool(y)}
        for i, (s, y) in enumerate(zip(scores, labels, strict=True))
    ]


def test_add_rank_orders_by_score_then_id_and_puts_missing_last():
    df = pl.DataFrame(
        [
            {"g": "a", "id": "p3", "score": 5.0},
            {"g": "a", "id": "p1", "score": 5.0},  # tie with p3: the smaller id first
            {"g": "a", "id": "p2", "score": None},  # no score: last
            {"g": "a", "id": "p0", "score": 9.0},
            {"g": "b", "id": "p9", "score": 1.0},
        ]
    )
    out = m.add_rank(df, group=["g"], by=[("score", True)], id_col="id")
    got = {(r["g"], r["id"]): r["rank"] for r in out.iter_rows(named=True)}
    assert got == {("a", "p0"): 1, ("a", "p1"): 2, ("a", "p3"): 3, ("a", "p2"): 4, ("b", "p9"): 1}
    # a second key breaks ties before the id
    df2 = df.with_columns(pl.Series("raw", [1.0, 0.5, 0.0, 0.0, 0.0]))
    out2 = m.add_rank(df2, group=["g"], by=[("score", True), ("raw", True)], id_col="id")
    got2 = {r["id"]: r["rank"] for r in out2.filter(pl.col("g") == "a").iter_rows(named=True)}
    assert got2["p3"] == 2 and got2["p1"] == 3


def test_precision_at_10_by_hand_with_a_short_list():
    # list A: 12 players; the top 10 by score hold 4 hits (ranks 1, 2, 5, 10), rank 11 is a hit
    # that does not count. list B: 7 players, 3 hits -> 3/7 (a short list).
    a_scores = [float(x) for x in range(12, 0, -1)]
    a_labels = [1, 1, 0, 0, 1, 0, 0, 0, 0, 1, 1, 0]
    b_scores = [float(x) for x in range(7, 0, -1)]
    b_labels = [0, 1, 0, 1, 0, 0, 1]
    df = pl.DataFrame(_rows("A", a_scores, a_labels) + _rows("B", b_scores, b_labels))
    ranked = m.add_rank(df, group=["g"], by=[("score", True)], id_col="id")
    table = m.precision_at_k(ranked, group=["g"], label="y", k=10)
    rows = {r["g"]: r for r in table.iter_rows(named=True)}
    assert rows["A"]["hits_top"] == 4 and rows["A"]["n_top"] == 10
    assert rows["A"]["p_at_k"] == pytest.approx(0.4) and not rows["A"]["short"]
    assert rows["A"]["n_pos"] == 5 and rows["A"]["n_rows"] == 12
    assert rows["B"]["p_at_k"] == pytest.approx(3 / 7) and rows["B"]["short"]
    pooled = m.pooled_precision(table)
    assert pooled.value == pytest.approx((0.4 + 3 / 7) / 2)  # every list weighs the same
    assert (pooled.n_groups, pooled.n_short, pooled.n_rows, pooled.n_pos) == (2, 1, 19, 8)
    assert pooled.hits_top == 7 and pooled.base_rate == pytest.approx(8 / 19)
    assert m.pooled_precision(table.head(0)).value is None


def test_bucket_rates_pool_every_list():
    scores = [float(x) for x in range(30, 0, -1)]
    labels = [1, 1, 1, 0, 0] + [1, 0, 0, 0, 0] + [1] * 3 + [0] * 12 + [1] * 5
    df = pl.DataFrame(_rows("A", scores, labels) + _rows("B", scores, labels))
    ranked = m.add_rank(df, group=["g"], by=[("score", True)], id_col="id")
    got = m.bucket_rates(ranked, label="y")
    assert got == [("1-5", 10, 6, 0.6), ("6-10", 10, 2, 0.2), ("11-25", 30, 6, 0.2)]


def test_pr_auc_and_brier():
    y = np.array([1, 0, 1, 0])
    s = np.array([0.9, 0.8, 0.7, 0.1])
    # hits at ranks 1 and 3: precision 1 and 2/3 -> average precision (1 + 2/3) / 2
    assert m.pr_auc(y, s) == pytest.approx((1 + 2 / 3) / 2)
    assert m.pr_auc(np.array([0, 0]), np.array([0.1, 0.2])) is None
    assert m.brier(y, s) == pytest.approx(np.mean((s - y) ** 2))
    assert m.brier(np.array([]), np.array([])) is None


def test_calibration_bins_are_equal_count_and_deterministic():
    df = pl.DataFrame(
        {"p": [0.1] * 6 + [0.5] * 4, "y": [0, 0, 0, 1, 0, 0, 1, 1, 0, 1],
         "id": [f"p{i}" for i in range(10)]}
    )  # fmt: skip
    bins = m.calibration_bins(df, prob="p", label="y", id_cols=["id"], n_bins=5)
    assert bins.get_column("n").to_list() == [2, 2, 2, 2, 2]
    assert bins.get_column("mean_pred").to_list() == pytest.approx([0.1, 0.1, 0.1, 0.5, 0.5])
    # ties split by id: p0,p1 | p2,p3 | p4,p5 | p6,p7 | p8,p9
    assert bins.get_column("observed").to_list() == pytest.approx([0.0, 0.5, 0.0, 1.0, 0.5])
    assert m.calibration_bins(df.head(0), prob="p", label="y", id_cols=["id"]).height == 0
