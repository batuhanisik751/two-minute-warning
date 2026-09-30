"""The K and D/ST streamer's walk-forward backtest (S1d): run the baselines and the models per
position, grade them, store the predictions and write ``reports/streamer/backtest.md`` (+ CSV).

The shared harness (:func:`twm.backtest.walkforward.walk_forward`) does the fitting, tuning and
isotonic calibration and refuses any training row of the test season or later. This module adds
the time rule of the streamer's dataset (a small adapter, not a fork): for test season S the
**training cutoff** is S's first Tuesday as-of; a model may learn only from rows whose label was
public by then (``available_at <= cutoff``, :func:`training_rows`), and
:func:`assert_available_by` refuses a training frame with a later (or unknown) ``available_at``.
Each test season is one ``walk_forward`` call on the rows of seasons <= S, so the harness's own
season guard runs too. No in-season refit: the model made before S ranks every week of S.

Grading (:mod:`twm.backtest.metrics`): per list (one as-of and position), precision@3 and @5
and whether the #1 pick started (precision@1); pooled as the mean over lists; 95% intervals by
resampling whole seasons (block bootstrap), and paired intervals for model minus baseline on the
same lists. Calibration: Brier score against a constant forecast (the training seasons' rate)
and fixed-width probability bins.
"""

from __future__ import annotations

import json
import warnings
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import polars as pl

from twm.backtest.metrics import (
    Interval,
    block_bootstrap,
    brier,
    paired_block_bootstrap,
)
from twm.backtest.walkforward import (
    RAW_SCORE,
    SCORE,
    FoldResult,
    TestSeasonInTrainingError,
    walk_forward,
)
from twm.modules.streamer.models import (
    ALL_METHODS,
    BASELINES,
    GROUP,
    KEYS,
    LABEL,
    MODULE,
    POSITIONS,
    TIE_COLUMNS,
    baseline_scores,
    estimator,
    graded_rows,
    list_table,
    position_features,
    rank_scores,
    training_source,
    tune_metric,
)
from twm.predictions import code_version, combine_hashes, frame_hash, model_version, now_utc

DEFAULT_TESTS = tuple(range(2013, 2026))  # 2012 (the GitHub build's first season) trains 2013
HORIZON_WEEKS = 1
ENTITY_TYPES = {"K": "kicker", "DST": "team_defense"}  # the store's entity_type (S2a)


def training_cutoff(dataset: pl.DataFrame, season: int) -> datetime:
    """The first as-of of ``season`` in the dataset: a model graded on that season is made
    then, so it may learn only from labels public by that moment."""
    s = dataset.filter(pl.col("season") == season).get_column("as_of")
    if s.len() == 0:
        raise ValueError(f"no rows of season {season}")
    return s.min()  # type: ignore[return-value]


def assert_available_by(frame: pl.DataFrame, cutoff: datetime, test_season: int) -> None:
    """Raise TestSeasonInTrainingError if a training row's label became public after the
    cutoff (or has no available_at: not final)."""
    late = frame.filter(pl.col("available_at").is_null() | (pl.col("available_at") > cutoff))
    if late.height:
        raise TestSeasonInTrainingError(
            f"refusing to train the {test_season} model: {late.height} training row(s) have a "
            f"label public after the cutoff {cutoff} (or no public label). A model graded on "
            f"{test_season} may only learn from labels known by its first as-of."
        )


def training_rows(source: pl.DataFrame, test_season: int, cutoff: datetime) -> pl.DataFrame:
    """Rows of seasons before ``test_season`` whose label was public by ``cutoff``."""
    return source.filter(
        (pl.col("season") < test_season)
        & (pl.col("label_status") == "final")
        & (pl.col("available_at") <= cutoff)
    )


# --------------------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------------------

TRAIN_HASH_COLUMNS = (*KEYS, *dict.fromkeys(position_features("K") + position_features("DST")),
                      LABEL)  # fmt: skip


@dataclass
class MethodRun:
    method: str
    position: str
    predictions: pl.DataFrame  # KEYS, raw_score, score, rank, model_version
    folds: list[FoldResult] = field(default_factory=list)
    versions: list[dict[str, Any]] = field(default_factory=list)
    n_train: dict[int, int] = field(default_factory=dict)  # test season -> training rows


@dataclass
class BacktestRun:
    methods: tuple[str, ...]
    positions: tuple[str, ...]
    test_seasons: tuple[int, ...]
    train_on: str
    graded: pl.DataFrame  # the graded rows (pool, final) of the test seasons
    source: pl.DataFrame  # training source rows of every season (before the time filter)
    runs: dict[tuple[str, str], MethodRun]

    def run(self, method: str, position: str) -> MethodRun:
        return self.runs[(method, position)]


def stored_name(method: str, position: str) -> str:
    """The model name in the predictions store: one model per position ('logit_k')."""
    return f"{method}_{position.lower()}"


def _hash(rows: pl.DataFrame) -> str:
    cols = [c for c in TRAIN_HASH_COLUMNS if c in rows.columns]
    return frame_hash(rows.select(cols).sort(list(KEYS)))


def _version(
    method: str, position: str, features: Sequence[str], params: dict[str, Any],
    training: Sequence[int], test_season: int, dataset_hash: str, notes: dict[str, Any],
) -> dict[str, Any]:  # fmt: skip
    name = stored_name(method, position)
    version = model_version(
        module=MODULE, model=name, label=LABEL, features=features, params=params,
        training_seasons=training, test_season=test_season, dataset_hash=dataset_hash,
    )  # fmt: skip
    return {
        "model_version": version, "module": MODULE, "model": name, "label": LABEL,
        "feature_list": json.dumps(sorted(features)), "params": json.dumps(params, sort_keys=True),
        "training_seasons": json.dumps(sorted(training)), "test_season": test_season,
        "dataset_hash": dataset_hash, "notes": json.dumps(notes, sort_keys=True),
    }  # fmt: skip


def _with_versions(preds: pl.DataFrame, versions: list[dict[str, Any]]) -> pl.DataFrame:
    vmap = {v["test_season"]: v["model_version"] for v in versions}
    return preds.with_columns(
        pl.col("season").replace_strict(vmap, return_dtype=pl.String).alias("model_version")
    )


def run_baseline(graded: pl.DataFrame, name: str, position: str) -> MethodRun:
    """A baseline learns nothing: its version is the rule, the position and the test season."""
    test = graded.filter(pl.col("position") == position)
    col, higher = BASELINES[name].columns[position]
    params = {"column": col, "higher_is_better": higher}
    versions = [
        _version(name, position, [col], params, [], int(s), combine_hashes([]),
                 {"kind": "baseline", "description": BASELINES[name].description,
                  "test_rows_hash": _hash(test.filter(pl.col("season") == s))})
        for s in sorted(test.get_column("season").unique().to_list())
    ]  # fmt: skip
    preds = rank_scores(baseline_scores(test, name))
    return MethodRun(name, position, _with_versions(preds, versions), [], versions)


def run_model_season(
    source: pl.DataFrame,
    test: pl.DataFrame,
    name: str,
    position: str,
    test_season: int,
    cutoff: datetime,
) -> tuple[pl.DataFrame, FoldResult, pl.DataFrame]:
    """One fold: train on ``source`` rows public by ``cutoff`` (seasons < test_season), score
    ``test`` (the test season's rows). Returns (predictions, fold result, training rows)."""
    features = position_features(position)
    train = training_rows(source.filter(pl.col("position") == position), test_season, cutoff)
    assert_available_by(train, cutoff, test_season)
    test = test.filter((pl.col("position") == position) & (pl.col("season") == test_season))
    rows = pl.concat([train, test.select(train.columns)], how="vertical")
    with warnings.catch_warnings():
        # weekly_ecr_* are NULL in every training row before late 2020: the imputer drops
        # them for that fold (expected; the model simply cannot use them yet)
        warnings.filterwarnings("ignore", message="Skipping features without any observed")
        result = walk_forward(
            rows,
            estimator=estimator(name),
            features=features,
            label=LABEL,
            module=MODULE,
            test_seasons=[test_season],
            keys=KEYS,
            tune_metric=tune_metric(),
        )
    return result.predictions, result.folds[0], train


def run_model(
    dataset: pl.DataFrame,
    source: pl.DataFrame,
    graded: pl.DataFrame,
    name: str,
    position: str,
    tests: Sequence[int],
    progress: Callable[[str], None] | None = None,
) -> MethodRun:
    """Walk-forward predictions of one model for one position, one fold per test season. The
    version fingerprints the training rows; the test rows' content goes into the notes."""
    est = estimator(name)
    features = position_features(position)
    preds, folds, versions, n_train = [], [], [], {}
    for s in tests:
        if progress:
            progress(f"{name} {position} {s}")
        cutoff = training_cutoff(dataset, s)
        p, fr, train = run_model_season(source, graded, name, position, s, cutoff)
        seasons = fr.fold.train_seasons
        dhash = combine_hashes([_hash(train.filter(pl.col("season") == x)) for x in seasons])
        test_rows = graded.filter((pl.col("position") == position) & (pl.col("season") == s))
        versions.append(
            _version(name, position, features, {**fr.params, "fixed": dict(est.fixed_params)},
                     seasons, s, dhash,
                     {"validation_season": fr.fold.val_season, "thin": fr.fold.thin,
                      "calibration": fr.calibration, "training_cutoff": str(cutoff),
                      "test_rows_hash": _hash(test_rows)})
        )  # fmt: skip
        preds.append(p)
        folds.append(fr)
        n_train[s] = train.height
    ties = graded.select(*KEYS, *TIE_COLUMNS)
    joined = pl.concat(preds, how="vertical").join(ties, on=list(KEYS), how="left")
    ranked = rank_scores(joined)
    return MethodRun(name, position, _with_versions(ranked, versions), folds, versions, n_train)


def run_backtest(
    dataset: pl.DataFrame,
    *,
    methods: Sequence[str] = ALL_METHODS,
    positions: Sequence[str] = POSITIONS,
    test_seasons: Iterable[int] = DEFAULT_TESTS,
    train_on: str = "pool",
    progress: Callable[[str], None] | None = None,
) -> BacktestRun:
    """Every method's predictions for every position over ``test_seasons``."""
    methods, positions = tuple(dict.fromkeys(methods)), tuple(dict.fromkeys(positions))
    bad = [m for m in methods if m not in ALL_METHODS]
    bad += [p for p in positions if p not in POSITIONS]
    if bad or not methods or not positions:
        raise ValueError(f"unknown or missing methods/positions {bad}; methods {ALL_METHODS}")
    tests = tuple(sorted({int(s) for s in test_seasons}))
    graded = graded_rows(dataset).filter(pl.col("season").is_in(list(tests))).sort(list(KEYS))
    for p in positions:
        have = set(graded.filter(pl.col("position") == p).get_column("season").to_list())
        if missing := [s for s in tests if s not in have]:
            raise ValueError(f"no graded {p} rows for test season(s) {missing}")
    source = training_source(dataset, train_on).sort(list(KEYS))
    runs: dict[tuple[str, str], MethodRun] = {}
    for p in positions:
        for m in methods:
            if m in BASELINES:
                runs[(m, p)] = run_baseline(graded, m, p)
            else:
                runs[(m, p)] = run_model(dataset, source, graded, m, p, tests, progress)
    return BacktestRun(methods, positions, tests, train_on, graded, source, runs)


# --------------------------------------------------------------------------------------
# The store
# --------------------------------------------------------------------------------------


def store_frames(run: BacktestRun, *, created_at: Any = None) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(predictions, model_versions) frames for :func:`twm.predictions.write_predictions`.
    No outcomes: the store's outcomes table holds the Radar's labels (y_hit, y_sustained)."""
    created = created_at if created_at is not None else now_utc()
    as_of = run.graded.select(*KEYS, "as_of")
    preds = [
        mr.predictions.join(as_of, on=list(KEYS), how="left").select(
            pl.lit(MODULE).alias("module"),
            pl.col("position").replace_strict(ENTITY_TYPES).alias("entity_type"),
            "entity_id",
            "season",
            "week",
            pl.col("as_of").dt.convert_time_zone("UTC").dt.replace_time_zone(None),
            pl.lit(HORIZON_WEEKS, dtype=pl.Int32).alias("horizon"),
            pl.col("position").alias("rank_group"),
            pl.col(SCORE).alias("score"),
            pl.col(RAW_SCORE).alias("raw_score"),
            "rank",
            pl.lit(None, dtype=pl.String).alias("band"),
            "model_version",
            pl.lit("[]").alias("reasons_json"),
            pl.lit("backtest").alias("kind"),
            pl.lit(created, dtype=pl.Datetime("us")).alias("created_at"),
        )  # fmt: skip
        for mr in run.runs.values()
    ]
    versions = pl.DataFrame(
        [v for mr in run.runs.values() for v in mr.versions],
        schema_overrides={"test_season": pl.Int32},
    ).with_columns(
        pl.lit(code_version()).alias("code_version"),
        pl.lit(created, dtype=pl.Datetime("us")).alias("created_at"),
    )
    return pl.concat(preds, how="vertical"), versions


# --------------------------------------------------------------------------------------
# Grading
# --------------------------------------------------------------------------------------


@dataclass
class Graded:
    method: str
    position: str
    rows: pl.DataFrame  # KEYS, raw_score, score, rank, y
    lists: pl.DataFrame  # list_table: one row per (as-of, position)

    def seasons(self, seasons: Sequence[int]) -> pl.DataFrame:
        return self.lists.filter(pl.col("season").is_in(list(seasons)))

    def interval(self, k: int, seasons: Sequence[int]) -> Interval:
        return block_bootstrap(self.seasons(seasons), value=f"p_at_{k}")

    def diff(self, other: Graded, k: int, seasons: Sequence[int]) -> Interval:
        return paired_block_bootstrap(
            self.seasons(seasons), other.seasons(seasons), keys=list(GROUP), value=f"p_at_{k}"
        )


def grade(run: BacktestRun, method: str, position: str) -> Graded:
    truth = run.graded.select(*KEYS, pl.col(LABEL).alias("y"))
    rows = run.run(method, position).predictions.join(truth, on=list(KEYS), how="left")
    return Graded(method, position, rows, list_table(rows, "y"))


def constant_brier(run: BacktestRun, position: str, seasons: Sequence[int]) -> float | None:
    """Brier of forecasting, for each test season, the start rate of the rows the models
    trained on (seasons before it): what a model must beat to add information."""
    num, den = 0.0, 0
    src = run.source.filter(pl.col("position") == position)
    for s in seasons:
        train = src.filter(pl.col("season") < s).get_column(LABEL).cast(pl.Float64)
        test = run.graded.filter((pl.col("position") == position) & (pl.col("season") == s))
        y = test.get_column(LABEL).cast(pl.Float64)
        if train.len() and y.len():
            p = float(train.mean())  # type: ignore[arg-type]
            num += float(((y - p) ** 2).sum())
            den += y.len()
    return num / den if den else None


def model_brier(g: Graded) -> float | None:
    return brier(g.rows.get_column("y").to_numpy(), g.rows.get_column(SCORE).to_numpy())
