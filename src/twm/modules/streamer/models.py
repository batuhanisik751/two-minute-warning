"""K and D/ST streamer models (S1d): three baselines and the Waiver Radar's two estimators.

**Rows.** One ranked list per (as-of, position) = the pool rows (``in_pool``) whose next-week
label is final (:func:`graded_rows`). A model learns from the same kind of rows of earlier
seasons (``train_on='pool'``, the Radar's rule) or, as a sensitivity check, from every final row
of the universe, rostered or not (``train_on='universe'``, :func:`training_source`).

**One model per position** (K, DST). Features: :data:`twm.modules.streamer.features.FEATURE_COLUMNS`
minus the other position's family (:func:`position_features`): a K model never sees ``dst_*``
columns (always NULL on kicker rows) and a DST model never sees ``k_*`` / ``is_team_kicker``.
``weekly_ecr_*`` are NULL before late 2020: the logistic regression imputes the median and adds
a missing indicator; LightGBM handles missing values natively. No row is dropped for it.

**Estimators**: the Radar's :class:`~twm.modules.waiver_radar.models.LogitEstimator` (L1/L2
logistic regression) and :class:`~twm.modules.waiver_radar.models.LgbmEstimator`, reused as they
are (no streamer feature is categorical, so their categorical branch is empty).

**Baselines** (design item 6) rank by one column, no training:

- ``baseline_last_points``: last game's fantasy points (``kdst_points_last``);
- ``baseline_ppg``: points per game so far this season (``kdst_points_per_game``);
- ``baseline_opponent``: next opponent's strength: for a kicker the points the next opponent
  allowed per game (more is better); for a D/ST the points the next opponent scored per game
  (fewer is better).

A row without a value is ranked below every row with one. **Ranks**: within each (as-of,
position), highest score first; ties -> higher uncalibrated score, then the smaller entity_id.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import polars as pl

from twm.backtest.metrics import add_rank, pooled_precision, precision_at_k
from twm.backtest.walkforward import RAW_SCORE, SCORE
from twm.modules.streamer.features import DEFENSE_FEATURES, FEATURE_COLUMNS, KICKER_FEATURES
from twm.modules.waiver_radar.models import LgbmEstimator, LogitEstimator
from twm.modules.waiver_radar.models import estimator as radar_estimator

MODULE = "streamer"
LABEL = "y_start"
POSITIONS = ("K", "DST")
ID = "entity_id"
GROUP = ("season", "week", "position")  # one ranked list per as-of and position
KEYS = ("season", "week", "position", "entity_id")
LIST_KS = (1, 3, 5)  # precision@1 = "the #1 pick started"
TUNE_K = 5
TRAIN_ON = ("pool", "universe")


@dataclass(frozen=True)
class Baseline:
    name: str
    columns: Mapping[str, tuple[str, bool]]  # position -> (column, higher_is_better)
    description: str


BASELINES: dict[str, Baseline] = {
    b.name: b
    for b in (
        Baseline("baseline_last_points",
                 {p: ("kdst_points_last", True) for p in POSITIONS},
                 "last game's fantasy points"),
        Baseline("baseline_ppg",
                 {p: ("kdst_points_per_game", True) for p in POSITIONS},
                 "fantasy points per game so far this season"),
        Baseline("baseline_opponent",
                 {"K": ("next_opp_points_allowed_per_game", True),
                  "DST": ("next_opp_points_per_game", False)},
                 "next opponent: points it allowed per game (K, more is better) / scored per "
                 "game (DST, fewer is better)"),
    )
}  # fmt: skip
MODELS = ("logit", "lgbm")
ALL_METHODS = (*BASELINES, *MODELS)


def position_features(position: str) -> tuple[str, ...]:
    """FEATURE_COLUMNS without the other position's family (in FEATURE_COLUMNS order)."""
    if position not in POSITIONS:
        raise ValueError(f"position must be one of {POSITIONS}, not {position!r}")
    other = set(DEFENSE_FEATURES if position == "K" else KICKER_FEATURES)
    return tuple(c for c in FEATURE_COLUMNS if c not in other)


def graded_rows(dataset: pl.DataFrame) -> pl.DataFrame:
    """The rows a method is graded on: pool rows with a final next-week label."""
    return dataset.filter(pl.col("in_pool") & (pl.col("label_status") == "final"))


def training_source(dataset: pl.DataFrame, train_on: str = "pool") -> pl.DataFrame:
    """Rows a model may learn from (before the walk-forward's season and time filters): final
    labels only; in the pool only unless ``train_on='universe'``."""
    if train_on not in TRAIN_ON:
        raise ValueError(f"train_on must be one of {TRAIN_ON}, not {train_on!r}")
    final = dataset.filter(pl.col("label_status") == "final")
    return graded_rows(dataset) if train_on == "pool" else final


class StreamerLgbmEstimator(LgbmEstimator):
    """The Radar's LightGBM on ONE thread. With a few hundred rows a fold can be a constant
    model (no split has enough rows); its validation loss is then flat and a multithreaded loss
    sum made early stopping pick 1 or 2 trees at random (same predictions, a different
    model_version and report). One thread sums in a fixed order; the data are small."""

    default_params: Mapping[str, Any] = {**LgbmEstimator.default_params, "n_jobs": 1}
    fixed_params: Mapping[str, Any] = {**LgbmEstimator.fixed_params, "n_jobs": 1}

    def grid(self) -> list[dict[str, Any]]:
        return [{**p, "n_jobs": 1} for p in super().grid()]


def estimator(name: str) -> LogitEstimator | LgbmEstimator:
    """The Radar's estimators; LightGBM single-threaded (:class:`StreamerLgbmEstimator`)."""
    return StreamerLgbmEstimator() if name == "lgbm" else radar_estimator(name)


def baseline_scores(rows: pl.DataFrame, name: str) -> pl.DataFrame:
    """KEYS + raw_score/score (the ranking value; higher = better) of a baseline, per row's
    position."""
    b = BASELINES[name]
    value = pl.lit(None, dtype=pl.Float64)
    present = set(rows.get_column("position").unique().to_list())
    for pos, (col, higher) in b.columns.items():
        if pos not in present:  # only the columns of the positions in ``rows`` are needed
            continue
        v = pl.col(col).cast(pl.Float64)
        value = pl.when(pl.col("position") == pos).then(v if higher else -v).otherwise(value)
    return rows.select(*KEYS, value.alias(RAW_SCORE), value.alias(SCORE))


def rank_scores(scored: pl.DataFrame) -> pl.DataFrame:
    """Rank within (as-of, position): score desc, raw_score desc, entity_id asc; no score last."""
    return add_rank(scored, group=GROUP, by=[(SCORE, True), (RAW_SCORE, True)], id_col=ID)


def list_table(ranked: pl.DataFrame, label: str = LABEL) -> pl.DataFrame:
    """One row per list (as-of, position): n_rows, n_pos and p_at_<k> for every k of LIST_KS
    (a list shorter than k divides by its length), short_<k> flags."""
    out = None
    for k in LIST_KS:
        t = precision_at_k(ranked, group=GROUP, label=label, k=k).select(
            *GROUP, "n_rows", "n_pos", pl.col("p_at_k").alias(f"p_at_{k}"),
            pl.col("short").alias(f"short_{k}"),
        )  # fmt: skip
        out = t if out is None else out.join(t.drop("n_rows", "n_pos"), on=list(GROUP))
    assert out is not None
    return out.sort(list(GROUP))


def tune_metric():
    """Pooled precision@5 over the validation season's pool lists (uncalibrated score)."""

    def metric(val: pl.DataFrame) -> float | None:
        rows = val.filter(pl.col("in_pool")) if "in_pool" in val.columns else val
        scored = rows.with_columns(pl.col(RAW_SCORE).alias(SCORE))
        return pooled_precision(
            precision_at_k(rank_scores(scored), group=GROUP, label=LABEL, k=TUNE_K)
        ).value

    return metric
