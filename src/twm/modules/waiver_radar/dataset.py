"""The Waiver Radar training dataset (C3): pool rows + features + labels, one row per as-of and
player (``twm radar dataset``).

Every labelled universe row of the candidate pool (C1, ``in_pool`` true or false; weeks 1 to
the season's last regular-season week minus 1) with the features (:mod:`features`, batch path)
and the labels (C2). Deterministic: sorted by (season, week, gsis_id), fixed column order,
floats rounded in the feature code, written with fixed Parquet settings, so two builds from the
same warehouse give byte-identical files. The file lives under ``data/`` (gitignored).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import polars as pl

from twm.modules.waiver_radar.features import (
    FEATURE_COLUMNS,
    INFO_COLUMNS,
    KEY_COLUMNS,
    POOL_FEATURES,
    FeatureRules,
    features_history,
)
from twm.modules.waiver_radar.labels import LABEL_COLUMNS, LabelRules, label_rows
from twm.modules.waiver_radar.pool import POOL_COLUMNS, PoolRules, pool_history
from twm.registry import check_features

DEFAULT_DATASET = Path("data/waiver_radar/dataset.parquet")
PARQUET_COMPRESSION = "zstd"
PARQUET_ROW_GROUP_SIZE = 64_000


def dataset_columns() -> list[str]:
    """Column order: pool columns, the features not already there, info, labels."""
    extra = [c for c in FEATURE_COLUMNS if c not in POOL_FEATURES]
    return [*POOL_COLUMNS, *extra, *INFO_COLUMNS, *LABEL_COLUMNS]


def build_dataset(
    db: Path | str,
    seasons: Sequence[int],
    *,
    pool_rules: PoolRules | None = None,
    label_rules: LabelRules | None = None,
    rules: FeatureRules | None = None,
) -> pl.DataFrame:
    """Pool rows (method 'auto') + features + labels for every labelled as-of of ``seasons``."""
    check_features(FEATURE_COLUMNS, "waiver_radar")  # every column is a registered feature
    hist = pool_history(db, seasons, rules=pool_rules)
    labelled = label_rows(db, hist, rules=label_rules)
    feats = features_history(db, labelled.select(POOL_COLUMNS), rules=rules)
    feats = feats.drop([c for c in POOL_FEATURES if c in feats.columns])
    out = labelled.join(feats, on=list(KEY_COLUMNS), how="left", maintain_order="left")
    if out.height != labelled.height:  # pragma: no cover - the key is unique per pool row
        raise RuntimeError("features did not join one-to-one onto the labelled pool rows")
    return out.select(dataset_columns()).sort(list(KEY_COLUMNS))


def write_dataset(df: pl.DataFrame, path: Path) -> Path:
    """Write the dataset as Parquet (atomically: temp file, then rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.write_parquet(
        tmp,
        compression=PARQUET_COMPRESSION,
        row_group_size=PARQUET_ROW_GROUP_SIZE,
        statistics=True,
    )
    tmp.replace(path)
    return path
