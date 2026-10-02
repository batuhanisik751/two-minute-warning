"""Step I3a: the Time Machine's comparison logic on synthetic lists (the real check of every
module is tests/test_timemachine_realdata.py, marked realdata)."""

from __future__ import annotations

import polars as pl
import pytest

from twm.timemachine.compare import TOLERANCE, compare_lists, sample_seasons


def _lists() -> pl.DataFrame:
    return pl.DataFrame({
        "season": [2020] * 3 + [2021] * 2,
        "week": [5] * 5,
        "entity_id": ["a", "b", "c", "a", "d"],
        "rank": [1, 2, 3, 1, 2],
        "score": [0.9, 0.5, 0.1, 0.7, 0.3],
        "reasons_json": ['["x"]', "[]", "[]", '["y"]', "[]"],
    })  # fmt: skip


def _cmp(stored: pl.DataFrame, new: pl.DataFrame, rank: str | None = "rank"):
    return compare_lists(stored, new, lists=["season", "week"], entity=["entity_id"], rank=rank,
                         numbers=["score"], exact=["reasons_json"])  # fmt: skip


def test_identical_lists_reproduce():
    c = _cmp(_lists(), _lists().reverse())  # the frames' order does not matter when ranked
    assert (c.lists, c.rows, c.mismatches, c.max_diff) == (2, 5, 0, 0.0)


def test_drift_of_1e6_fails_and_1e10_passes():
    small = _lists().with_columns(pl.col("score") + 1e-10)
    assert _cmp(_lists(), small).mismatches == 0
    assert _cmp(_lists(), small).max_diff <= TOLERANCE
    drift = _lists().with_columns(
        pl.when(pl.col("entity_id") == "b").then(pl.col("score") + 1e-6).otherwise("score")
    )
    c = _cmp(_lists(), drift)
    assert c.mismatches == 1 and c.max_diff == pytest.approx(1e-6)
    assert "score off by more than" in c.examples[0]


def test_reordered_list_fails():
    swapped = _lists().with_columns(
        pl.col("rank").replace_strict({1: 2, 2: 1, 3: 3}).alias("rank")
    )  # 2020's a and b swap places, and so do 2021's a and d
    c = _cmp(_lists(), swapped)
    assert c.mismatches == 4 and "another rank" in c.examples[0]


def test_reordered_rows_fail_without_a_rank_column():
    df = _lists()
    moved = pl.concat([df[1:2], df[0:1], df[2:]])  # 2020's first two rows swap places
    c = _cmp(df, moved, rank=None)
    assert c.mismatches == 2 and "another position" in c.examples[0]
    assert _cmp(df, df, rank=None).mismatches == 0


def test_missing_extra_and_changed_rows_fail():
    df = _lists()
    extra = pl.concat([df, df[0:1].with_columns(pl.lit("z").alias("entity_id"))])
    assert _cmp(df, df[1:]).mismatches == 1  # a stored row not recomputed
    assert _cmp(df, extra).mismatches == 1  # a recomputed row not stored
    changed = df.with_columns(pl.lit("[]").alias("reasons_json"))
    assert _cmp(df, changed).mismatches == 2
    nulls = df.with_columns(pl.lit(None, dtype=pl.Float64).alias("score"))
    assert _cmp(df, nulls).mismatches == 5  # a missing number never equals a present one
    assert _cmp(nulls, nulls).mismatches == 0


def test_sample_is_seeded_and_keeps_both_ends():
    a = sample_seasons(range(2006, 2026), 4, seed=7)
    assert a == sample_seasons(range(2006, 2026), 4, seed=7)
    assert len(a) == 4 and a[0] == 2006 and a[-1] == 2025
    assert sample_seasons([2020, 2021], 5, seed=1) == [2020, 2021]
    assert sample_seasons(range(2010, 2020), 1, seed=1) == [2010, 2019]
