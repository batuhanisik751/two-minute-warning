"""Opt-in (`uv run pytest -m realdata`): a 10-team league on one real season (2024), pool and
labels, against the shipped 12-team league. Reads the real cache through the shared full build
(tests/conftest.py real_full_db); never downloads, never writes the real warehouse."""

from __future__ import annotations

import polars as pl
import pytest
import yaml

from twm.config import CONFIG_DIR, League, league
from twm.modules.waiver_radar.labels import LabelRules, label_rows
from twm.modules.waiver_radar.pool import PoolRules, pool_history

SEASON = 2024
KEYS = ["season", "week", "gsis_id"]


def ten_team_league() -> League:
    raw = yaml.safe_load((CONFIG_DIR / "league.yaml").read_text())
    return League(**{**raw, "teams": 10})


def shape_numbers(db, lg: League) -> tuple[pl.DataFrame, dict]:
    rows = label_rows(
        db, pool_history(db, [SEASON], rules=PoolRules.from_config(lg)),
        rules=LabelRules.from_config(lg),
    )  # fmt: skip
    pool = rows.filter(pl.col("in_pool") & (pl.col("label_status") == "final")
                       & pl.col("train_eligible"))  # fmt: skip
    by_pos = {
        p: (g.height, int(g.get_column("y_hit").sum()))
        for (p,), g in sorted(pool.group_by("position"), key=lambda x: x[0])
    }
    return rows, {
        "universe": rows.height,
        "pool": int(rows.get_column("in_pool").sum()),
        "trainable_pool": pool.height,
        "y_hit": int(pool.get_column("y_hit").sum()),
        "y_sustained": int(pool.get_column("y_sustained").sum()),
        "by_position": by_pos,
    }


@pytest.mark.realdata
def test_real_ten_team_league_pool_and_labels(real_full_db):
    db, _ = real_full_db
    ten = ten_team_league()
    assert ten.candidate_pool_cutoffs() == {"QB": 15, "RB": 30, "WR": 30, "TE": 15}
    rows12, n12 = shape_numbers(db, league())
    rows10, n10 = shape_numbers(db, ten)
    print(f"\n{SEASON} 12-team: {n12}\n{SEASON} 10-team: {n10}")
    assert rows10.height == rows12.height  # the same rostered players
    assert n10["pool"] > n12["pool"]
    both = rows12.select(*KEYS, "in_pool", "n_starter_finishes").join(
        rows10.select(*KEYS, "in_pool", "n_starter_finishes"), on=KEYS, suffix="_10"
    )
    assert both.filter(pl.col("in_pool") & ~pl.col("in_pool_10")).height == 0
    finishes = both.drop_nulls(["n_starter_finishes", "n_starter_finishes_10"])
    assert (
        finishes.filter(pl.col("n_starter_finishes_10") > pl.col("n_starter_finishes")).height == 0
    )
    # the 10-team base rate is lower: a stricter starter threshold over a deeper pool
    assert n10["y_hit"] / n10["trainable_pool"] < n12["y_hit"] / n12["trainable_pool"]
