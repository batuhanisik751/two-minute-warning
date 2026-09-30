"""The streamer's training dataset (S1c): pool rows + features + labels, one row per as-of and
K or DST entity (``twm streamer dataset``).

Every pool row (S1b, method 'auto': ``in_pool`` true or false, every regular-season as-of of the
seasons, the last week included with label_status 'season_end') with the features
(:mod:`features`, batch path) and the next-week label (:mod:`labels`). Train only on rows with
``label_status == 'final'``.

``available_at``: when the row's label became public = the latest ``available_at`` of the
rankable lines of the label week at the row's position (a rank compares everyone who played),
always after the row's ``as_of``; NULL unless the label is final. A walk-forward backtest can
train on the rows with ``available_at <= `` its cutoff.

Deterministic like the Radar's dataset: sorted by (season, week, position, entity_id), fixed
column order, floats rounded in the feature code, fixed Parquet settings, so two builds from the
same warehouse give byte-identical files. The file lives under ``data/`` (gitignored; a symlink
to local storage made by scripts/local_storage.sh).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import polars as pl

from twm.modules.streamer.features import (
    FEATURE_COLUMNS,
    KEY_COLUMNS,
    POOL_FEATURES,
    features_history,
)
from twm.modules.streamer.labels import LABEL_COLUMNS, label_rows, weekly_finishes
from twm.modules.streamer.pool import POOL_COLUMNS, StreamerRules, pool_history
from twm.modules.waiver_radar.dataset import write_dataset
from twm.registry import check_features

DEFAULT_DATASET = Path("data/streamer/dataset.parquet")
# The GitHub pipeline builds the warehouse from 2012 (the streamer's pool starts there too).
DEFAULT_START = 2012


def dataset_columns() -> list[str]:
    """Column order: pool columns, the features not already there, labels, available_at."""
    extra = [c for c in FEATURE_COLUMNS if c not in POOL_FEATURES]
    return [*POOL_COLUMNS, *extra, *LABEL_COLUMNS, "available_at"]


def label_available_at(
    db: Path | str, seasons: Sequence[int], rules: StreamerRules
) -> pl.DataFrame:
    """(position, season, label_week, available_at): the latest available_at of every rankable
    K / DST line of each regular-season week."""
    from twm.warehouse.build import connect

    con = connect(db, read_only=True)
    try:
        con.execute("SET TimeZone='UTC'")
        fin = weekly_finishes(con, seasons, rules)
    finally:
        con.close()
    return fin.group_by("position", "season", pl.col("week").alias("label_week")).agg(
        pl.col("available_at").max()
    )


def build_dataset(
    db: Path | str, seasons: Sequence[int], *, rules: StreamerRules | None = None
) -> pl.DataFrame:
    """Pool rows (method 'auto') + features + labels + available_at for every as-of of
    ``seasons``."""
    check_features(FEATURE_COLUMNS, "streamer")  # every column is a registered feature
    rules = rules or StreamerRules.from_config()
    hist = pool_history(db, seasons, rules=rules)
    labelled = label_rows(db, hist, rules)
    feats = features_history(db, labelled.select(POOL_COLUMNS), rules=rules)
    feats = feats.drop([c for c in POOL_FEATURES if c in feats.columns])
    public = label_available_at(db, seasons, rules)
    key = list(KEY_COLUMNS)
    out = labelled.join(feats, on=key, how="left", maintain_order="left").join(
        public, on=["position", "season", "label_week"], how="left", maintain_order="left"
    )
    out = out.with_columns(
        pl.when(pl.col("label_status") == "final")
        .then(pl.col("available_at"))
        .cast(pl.Datetime("us", "UTC"))
        .alias("available_at")
    )
    if out.height != labelled.height:  # pragma: no cover - the key is unique per pool row
        raise RuntimeError("features did not join one-to-one onto the labelled pool rows")
    return out.select(dataset_columns()).sort(key)


__all__ = ["DEFAULT_DATASET", "build_dataset", "dataset_columns", "write_dataset"]
