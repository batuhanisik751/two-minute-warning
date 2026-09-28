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


def test_calibration_bins_count_hits():
    df = pl.DataFrame({"p": [0.1, 0.2, 0.3, 0.4], "y": [0, 1, 1, 1], "id": ["a", "b", "c", "d"]})
    bins = m.calibration_bins(df, prob="p", label="y", id_cols=["id"], n_bins=2)
    assert bins.get_column("n_pos").to_list() == [1, 2]


def test_calibration_fixed_bins_list_every_bin_with_counts():
    df = pl.DataFrame({"p": [0.02, 0.05, 0.15, 0.95, 1.0, None], "y": [0, 1, 0, 1, 1, 1]})
    bins = m.calibration_fixed_bins(df, prob="p", label="y", n_bins=10)
    assert bins.height == 10  # empty bins are listed too
    assert bins.get_column("n").to_list() == [2, 1, 0, 0, 0, 0, 0, 0, 0, 2]  # NULL p left out
    assert bins.get_column("n_pos").to_list() == [1, 0, 0, 0, 0, 0, 0, 0, 0, 2]
    first, last = bins.row(0, named=True), bins.row(9, named=True)
    assert first["lo"] == 0.0 and first["hi"] == pytest.approx(0.1)
    assert first["mean_pred"] == pytest.approx(0.035) and first["observed"] == pytest.approx(0.5)
    assert last["mean_pred"] == pytest.approx(0.975)  # 1.0 falls in the last bin
    assert bins.row(4, named=True)["observed"] is None


def _lists(values: dict[int, list[float]]) -> pl.DataFrame:
    """A per-group table: season -> the p_at_k of its lists."""
    rows = [
        {"season": s, "week": w + 1, "position": "WR", "p_at_k": v}
        for s, vals in values.items()
        for w, v in enumerate(vals)
    ]
    return pl.DataFrame(rows)


def test_block_bootstrap_is_deterministic_and_resamples_whole_seasons():
    g = _lists({2014: [0.2, 0.4], 2015: [0.5, 0.7, 0.6], 2016: [0.1], 2017: [0.9, 0.8]})
    a = m.block_bootstrap(g, n_boot=500, seed=11)
    b = m.block_bootstrap(g, n_boot=500, seed=11)
    assert a == b  # same seed, same interval
    assert a.value == pytest.approx(g["p_at_k"].mean())  # the point value is the plain mean
    assert (a.n_blocks, a.n_groups, a.n_boot, a.level) == (4, 8, 500, 0.95)
    # another seed draws other resamples (with 4 seasons the interval may still coincide)
    assert not np.array_equal(m.block_indices(4, 500, 11), m.block_indices(4, 500, 12))
    # by hand: the same resamples, each the mean over the groups of the drawn seasons
    idx = m.block_indices(4, 500, 11)
    sums = np.array([0.6, 1.8, 0.1, 1.7])
    counts = np.array([2, 3, 1, 2])
    vals = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    lo, hi = np.quantile(vals, [0.025, 0.975])
    assert (a.lo, a.hi) == (pytest.approx(lo), pytest.approx(hi))
    # the interval lies between the worst and the best season
    assert 0.1 <= a.lo <= a.value <= a.hi <= 0.85


def test_block_bootstrap_of_identical_seasons_has_zero_width():
    g = _lists({s: [0.3, 0.5] for s in range(2014, 2026)})
    iv = m.block_bootstrap(g)
    assert iv.value == pytest.approx(0.4)
    assert iv.lo == pytest.approx(0.4) and iv.hi == pytest.approx(0.4)
    assert iv.n_boot == m.N_BOOT and iv.n_blocks == 12
    one = m.block_bootstrap(_lists({2020: [0.1, 0.3]}))  # one season: nothing to resample
    assert one.lo == pytest.approx(0.2) and one.hi == pytest.approx(0.2)
    empty = m.block_bootstrap(g.head(0))
    assert empty.value is None and empty.lo is None


def test_paired_bootstrap_measures_the_gap_not_the_seasons():
    a = _lists({2014: [0.2, 0.4], 2015: [0.9], 2016: [0.5, 0.1, 0.3]})
    # b is a minus 0.05 in every list: the seasons differ a lot, the gap never does
    b = a.with_columns(pl.col("p_at_k") - 0.05)
    iv = m.paired_block_bootstrap(a, b, keys=["season", "week", "position"])
    assert iv.value == pytest.approx(0.05)
    assert iv.lo == pytest.approx(0.05) and iv.hi == pytest.approx(0.05)
    assert iv.share_above_zero == 1.0
    unpaired_a = m.block_bootstrap(a)
    assert unpaired_a.hi - unpaired_a.lo > 0.1  # the seasons alone vary widely
    # a real gap that changes sign: the interval covers 0 and the share is in between
    c = a.with_columns(
        pl.when(pl.col("season") == 2015).then(pl.col("p_at_k") + 0.2)
        .otherwise(pl.col("p_at_k") - 0.1).alias("p_at_k")
    )  # fmt: skip
    iv2 = m.paired_block_bootstrap(a, c, keys=["season", "week", "position"])
    assert iv2.lo < 0 < iv2.hi and 0 < iv2.share_above_zero < 1
    assert iv2.value == pytest.approx((0.1 * 5 - 0.2) / 6)
    with pytest.raises(ValueError, match="same groups"):
        m.paired_block_bootstrap(a, b.head(3), keys=["season", "week", "position"])


def test_first_events_and_caught_before_never_use_later_ranks():
    # the event (a breakout) of each player: A in week 3, B in week 2, C in week 4, E none
    rows = pl.DataFrame(
        {
            "id": ["A"] * 4 + ["B"] * 4 + ["C"] * 4 + ["E"] * 2,
            "week": [1, 2, 3, 4] * 3 + [1, 2],
            "ev": [False, False, True, True, False, True, False, False,
                   False, False, False, True, False, False],
        }
    )  # fmt: skip
    events = m.first_events(rows, entity=["id"], order="week", event="ev")
    assert events.rows() == [("A", 3), ("B", 2), ("C", 4)]
    ranked = pl.DataFrame(
        {
            "id": ["A"] * 4 + ["B"] * 3 + ["C"] * 2 + ["E"],
            "week": [1, 2, 3, 4, 1, 2, 3, 1, 4, 1],
            "rank": [15, 8, 12, 1, 11, 11, 2, 3, 20, 1],
        }
    )
    got = m.caught_before(events, ranked, entity=["id"], order="week", k=10, recent=2)
    r = {x["id"]: x for x in got.iter_rows(named=True)}
    # A: top 10 in week 2, before his week-3 event; week 4's rank 1 comes after it
    assert r["A"]["caught"] and r["A"]["best_rank"] == 8 and r["A"]["best_order"] == 2
    assert r["A"]["first_top_k"] == 2 and r["A"]["caught_recent"]  # week 2 is in weeks 2-3
    # B: rank 2 in week 3 is AFTER his week-2 event: not caught
    assert not r["B"]["caught"] and r["B"]["best_rank"] == 11 and r["B"]["first_top_k"] is None
    # C: rank 3 in week 1, three weeks before his week-4 event: caught, but not recently
    assert r["C"]["caught"] and not r["C"]["caught_recent"] and r["C"]["best_order"] == 1
    assert "E" not in r  # no event, not a breakout
    # an event without any ranked row is not caught
    alone = m.caught_before(
        pl.DataFrame({"id": ["Z"], "event_order": [3]}), ranked, entity=["id"], order="week"
    )
    assert alone.rows(named=True)[0]["caught"] is False
    assert alone.rows(named=True)[0]["best_rank"] is None
