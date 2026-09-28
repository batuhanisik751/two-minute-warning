"""A synthetic Waiver Radar dataset for the C4 model and harness tests (offline, made up).

Every column the backtest reads exists with the real dtype; three features carry signal (a
hidden "skill" per row drives snap share, last game's points and xFP, and the chance of a
hit), the rest is noise with some missing values. Seeded, so every call gives the same frame.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl

from twm.config import FANTASY_POSITIONS
from twm.modules.waiver_radar.features import FEATURE_COLUMNS, FEATURE_SCHEMA

SEASONS = (2013, 2014, 2015, 2016)


def gid(pos_index: int, i: int) -> str:
    return f"00-{pos_index}{i:06d}"


def synthetic_dataset(
    seasons: tuple[int, ...] = SEASONS, *, weeks: int = 6, per_pos: int = 25, seed: int = 7
) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    keys: dict[str, list] = {"season": [], "week": [], "as_of": [], "gsis_id": []}
    pos: list[str] = []
    for s in seasons:
        for w in range(1, weeks + 1):
            as_of = datetime(s, 9, 9, 14, tzinfo=UTC) + timedelta(days=7 * (w - 1))
            for p_i, p in enumerate(FANTASY_POSITIONS):
                for i in range(per_pos):
                    keys["season"].append(s)
                    keys["week"].append(w)
                    keys["as_of"].append(as_of)
                    keys["gsis_id"].append(gid(p_i, i))
                    pos.append(p)
    n = len(pos)
    skill = rng.normal(size=n)
    cols: dict[str, object] = {}
    for c in FEATURE_COLUMNS:
        t = FEATURE_SCHEMA[c]
        if c == "position":
            cols[c] = pos
        elif t == pl.Boolean():
            cols[c] = rng.random(n) < 0.3
        elif t == pl.Int32():
            cols[c] = rng.integers(0, 20, n)
        else:
            cols[c] = rng.normal(size=n)
    cols["snap_share_last"] = np.clip(0.4 + 0.2 * skill + 0.1 * rng.normal(size=n), 0, 1)
    cols["fantasy_points_last"] = np.round(6 + 4 * skill + 3 * rng.normal(size=n), 2)
    cols["xfp_avg3"] = np.round(7 + 3 * skill + 2 * rng.normal(size=n), 2)
    cols["snap_share_delta"] = np.round(0.1 * skill + 0.1 * rng.normal(size=n), 4)
    y_hit = rng.random(n) < 1 / (1 + np.exp(-(-2.0 + 1.6 * skill)))
    y_sus = y_hit & (rng.random(n) < 0.35)
    season = np.array(keys["season"])
    owned = np.where(rng.random(n) < 0.05, 70.0, rng.uniform(0, 45, n))
    df = pl.DataFrame(keys).with_columns(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
    )
    feats = pl.DataFrame(cols).cast(FEATURE_SCHEMA)  # type: ignore[arg-type]
    # some missing values, like the real data (a feature NULL when the team has no game yet)
    feats = feats.with_columns(
        pl.when(pl.Series(rng.random(n) < 0.1))
        .then(None)
        .otherwise(pl.col("snap_share_delta"))
        .alias("snap_share_delta"),
        pl.when(pl.Series(rng.random(n) < 0.05))
        .then(None)
        .otherwise(pl.col("depth_rank_now"))
        .alias("depth_rank_now"),
    )
    ecr_ok = season >= 2015
    ecr_rank = np.argsort(np.argsort(-(skill + rng.normal(size=n)))) % 60 + 1
    extra = pl.DataFrame(
        {
            "name": [f"Synthetic {g}" for g in keys["gsis_id"]],
            "in_pool": rng.random(n) < 0.95,
            "train_eligible": rng.random(n) < 0.97,
            "label_status": ["final"] * n,
            "owned_avg": pl.Series(np.where(season >= 2015, owned, np.nan)).fill_nan(None),
            "ecr_available": ecr_ok,
            "ecr_pos_rank": pl.Series(
                np.where(ecr_ok & (rng.random(n) < 0.7), ecr_rank, -1), dtype=pl.Int32
            ),
            "ecr_page_kind": ["ros"] * n,
            "ecr_scrape_date": [None] * n,
            "y_hit": y_hit,
            "y_sustained": y_sus,
        }
    ).with_columns(
        pl.when(pl.col("ecr_pos_rank") < 0).then(None).otherwise(pl.col("ecr_pos_rank"))
        .alias("ecr_pos_rank"),
        pl.col("ecr_scrape_date").cast(pl.Date),
    )  # fmt: skip
    extra = extra.with_columns(
        pl.when(pl.col("ecr_pos_rank").is_null()).then(None).otherwise(pl.col("ecr_page_kind"))
        .alias("ecr_page_kind")
    )  # fmt: skip
    return df.hstack(feats).hstack(extra)
