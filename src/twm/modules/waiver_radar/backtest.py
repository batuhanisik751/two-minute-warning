"""The Waiver Radar walk-forward backtest (C4): run the baselines and the models, grade them,
store every prediction and write ``reports/waiver_radar/backtest.md`` (+ CSV).

- :func:`run_backtest`: for every label and model, one prediction per evaluation row of every
  test season (2014-2025 by default). Models go through the walk-forward harness
  (:mod:`twm.backtest.walkforward`); baselines need no training. Every prediction carries its
  rank within its (as-of, position) list and its ``model_version``.
- :func:`store_frames` / :func:`twm.predictions.write_predictions`: the time-machine rows.
- :func:`build_backtest_report`: precision@10 pooled, per position and per season, with and
  without the pool players real leagues had rostered, rank-bucket hit rates, PR-AUC and Brier,
  the experts' baseline where it exists, the acceptance verdict, the winner, its calibration
  and its feature importance per fold. Deterministic apart from the one "Created" line.

**Rostered rows.** FantasyPros' rostership (``owned_avg``, 2020 partly, 2021 on) shows that a
few pool players were owned in at least ``ownership_available_below`` (50) percent of leagues;
they hit about half the time and flatter every ranking, so every precision number is also given
WITHOUT them (the rows are removed and the rest re-ranked). Rows without a figure stay.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

from twm.backtest.metrics import (
    DEFAULT_BUCKETS,
    Pooled,
    brier,
    bucket_rates,
    calibration_bins,
    pooled_precision,
    pr_auc,
)
from twm.backtest.walkforward import (
    DOMINANCE_SHARE,
    RAW_SCORE,
    SCORE,
    FoldResult,
    plan_folds,
    season_span,
    walk_forward,
)
from twm.config import FANTASY_POSITIONS, league, settings
from twm.modules.waiver_radar.expert_ranks import EXPERT_COLUMNS
from twm.modules.waiver_radar.models import (
    ALL_MODELS,
    BASELINES,
    FEATURES,
    GROUP,
    ID,
    KEYS,
    LABELS,
    MODELS,
    MODULE,
    NAIVE_BASELINES,
    K,
    baseline_scores,
    estimator,
    model_rows,
    precision_table,
    rank_scores,
    tune_metric_for,
)
from twm.predictions import code_version, combine_hashes, frame_hash, model_version, now_utc

SUBSETS = ("all", "without_rostered")
SUBSET_TITLES = {"all": "all pool rows", "without_rostered": "without rostered"}
CSV_COLUMNS = (
    "label", "model", "subset", "scope", "seasons", "key", "metric", "value", "n_groups",
    "n_rows", "n_pos",
)  # fmt: skip
# What a model learns from (the training rows' content goes into its model_version) ...
TRAIN_HASH_COLUMNS = (*KEYS, "position", *[f for f in FEATURES if f != "position"], *LABELS)
# ... and every column a prediction or a grade can depend on (recorded, not in the version).
ROW_HASH_COLUMNS = (*TRAIN_HASH_COLUMNS, *EXPERT_COLUMNS, "owned_avg")
TOP_FEATURES = 5
# Context line of the report (not a spec baseline, never stored): the single strongest feature
# of the C3 feature report, ranked on its own. Shows how much the models add over it.
REFERENCE_FEATURE = "xfp_avg3"


def eval_seasons() -> tuple[int, int]:
    first, last = settings().seasons["waiver_radar_eval"]
    return int(first), int(last)


def rostered_threshold() -> float:
    return float(league().pool.ownership_available_below)


# --------------------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------------------


@dataclass
class ModelRun:
    model: str
    label: str
    predictions: pl.DataFrame  # KEYS, position, raw_score, score, rank, model_version
    folds: list[FoldResult] = field(default_factory=list)
    versions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class BacktestRun:
    labels: tuple[str, ...]
    models: tuple[str, ...]
    test_seasons: tuple[int, ...]
    rows: pl.DataFrame  # the model rows of every season used (training and test)
    season_hashes: dict[int, str]  # per season: content hash of TRAIN_HASH_COLUMNS
    row_hashes: dict[int, str]  # per season: content hash of ROW_HASH_COLUMNS
    runs: dict[tuple[str, str], ModelRun]

    def run(self, label: str, model: str) -> ModelRun:
        return self.runs[(label, model)]


def season_hashes(
    rows: pl.DataFrame, columns: Sequence[str] = TRAIN_HASH_COLUMNS
) -> dict[int, str]:
    """Content hash of each season's model rows (``columns``, rows sorted by key)."""
    cols = [c for c in columns if c in rows.columns]
    out = {}
    for season in sorted(rows.get_column("season").unique().to_list()):
        part = rows.filter(pl.col("season") == season).select(cols).sort(list(KEYS))
        out[int(season)] = frame_hash(part)
    return out


def _check_args(models: Sequence[str], labels: Sequence[str]) -> None:
    bad = [m for m in models if m not in ALL_MODELS]
    if bad:
        raise ValueError(f"unknown model(s) {bad}; choose from {ALL_MODELS}")
    bad = [lb for lb in labels if lb not in LABELS]
    if bad:
        raise ValueError(f"unknown label(s) {bad}; choose from {LABELS}")
    if not models or not labels:
        raise ValueError("at least one model and one label are needed")


def _version_record(
    *,
    model: str,
    label: str,
    features: Sequence[str],
    params: dict[str, Any],
    training: Sequence[int],
    test_season: int,
    dataset_hash: str,
    notes: dict[str, Any],
) -> dict[str, Any]:
    version = model_version(
        module=MODULE,
        model=model,
        label=label,
        features=features,
        params=params,
        training_seasons=training,
        test_season=test_season,
        dataset_hash=dataset_hash,
    )
    return {
        "model_version": version,
        "module": MODULE,
        "model": model,
        "label": label,
        "feature_list": json.dumps(sorted(features)),
        "params": json.dumps(params, sort_keys=True),
        "training_seasons": json.dumps(sorted(training)),
        "test_season": test_season,
        "dataset_hash": dataset_hash,
        "notes": json.dumps(notes, sort_keys=True),
    }


def _run_baseline(
    rows: pl.DataFrame, name: str, label: str, tests: Sequence[int], row_hashes: dict[int, str]
) -> ModelRun:
    """A baseline learns nothing: its version is the rule and the test season; the content of
    the rows it ranked is recorded in the notes."""
    b = BASELINES[name]
    test = rows.filter(pl.col("season").is_in(list(tests)))
    ranked = rank_scores(baseline_scores(test, name))
    params = {"column": b.column, "higher_is_better": b.higher_is_better, "needs": b.needs}
    versions = []
    for s in tests:
        versions.append(
            _version_record(
                model=name,
                label=label,
                features=[b.column],
                params=params,
                training=[],
                test_season=s,
                dataset_hash=combine_hashes([]),
                notes={
                    "kind": "baseline",
                    "description": b.description,
                    "test_rows_hash": row_hashes[s],
                },  # fmt: skip
            )
        )
    vmap = {v["test_season"]: v["model_version"] for v in versions}
    preds = ranked.with_columns(
        pl.col("season").replace_strict(vmap, return_dtype=pl.String).alias("model_version")
    )
    return ModelRun(name, label, preds, [], versions)


def _run_model(
    rows: pl.DataFrame,
    name: str,
    label: str,
    tests: Sequence[int],
    hashes: dict[int, str],
    row_hashes: dict[int, str],
) -> ModelRun:
    """A model's version fingerprints what it learned from (the training seasons' rows), so
    the same trained model keeps its version whatever rows it scores; the content of the test
    rows is recorded in the notes."""
    seasons_needed = [s for s in hashes if s <= max(tests)]
    data = rows.filter(pl.col("season").is_in(seasons_needed))
    est = estimator(name)
    result = walk_forward(
        data,
        estimator=est,
        features=FEATURES,
        label=label,
        module=MODULE,
        test_seasons=tests,
        keys=(*KEYS, "position"),
        tune_metric=tune_metric_for(label),
    )
    versions = []
    for fr in result.folds:
        f = fr.fold
        dhash = combine_hashes([hashes[s] for s in f.train_seasons])
        versions.append(
            _version_record(
                model=name,
                label=label,
                features=FEATURES,
                params={**fr.params, "fixed": dict(est.fixed_params)},
                training=f.train_seasons,
                test_season=f.test_season,
                dataset_hash=dhash,
                notes={
                    "validation_season": f.val_season,
                    "thin": f.thin,
                    "calibration": fr.calibration,
                    "test_rows_hash": row_hashes[f.test_season],
                },
            )
        )
    vmap = {v["test_season"]: v["model_version"] for v in versions}
    preds = rank_scores(result.predictions).with_columns(
        pl.col("season").replace_strict(vmap, return_dtype=pl.String).alias("model_version")
    )
    return ModelRun(name, label, preds, result.folds, versions)


def run_backtest(
    dataset: pl.DataFrame,
    *,
    models: Sequence[str] = ALL_MODELS,
    labels: Sequence[str] = ("y_hit",),
    test_seasons: Iterable[int] | None = None,
    progress: Callable[[str], None] | None = None,
) -> BacktestRun:
    """Walk-forward predictions of every model for every label over ``test_seasons`` (default:
    settings ``seasons.waiver_radar_eval``, 2014-2025)."""
    models, labels = tuple(dict.fromkeys(models)), tuple(dict.fromkeys(labels))
    _check_args(models, labels)
    if test_seasons is None:
        first, last = eval_seasons()
        test_seasons = range(first, last + 1)
    tests = tuple(sorted({int(s) for s in test_seasons}))
    rows = model_rows(dataset).filter(pl.col("season") <= max(tests)).sort(list(KEYS))
    hashes = season_hashes(rows)
    row_hashes = season_hashes(rows, ROW_HASH_COLUMNS)
    missing = [s for s in tests if s not in hashes]
    if missing:
        raise ValueError(f"no evaluation rows for test season(s) {missing}")
    runs: dict[tuple[str, str], ModelRun] = {}
    for label in labels:
        for name in models:
            if progress:
                progress(f"{label}: {name}")
            if name in BASELINES:
                runs[(label, name)] = _run_baseline(rows, name, label, tests, row_hashes)
            else:
                runs[(label, name)] = _run_model(rows, name, label, tests, hashes, row_hashes)
    return BacktestRun(labels, models, tests, rows, hashes, row_hashes, runs)


# --------------------------------------------------------------------------------------
# The store
# --------------------------------------------------------------------------------------


def store_frames(
    run: BacktestRun, *, created_at: Any = None
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """(predictions, model_versions, outcomes) frames for
    :func:`twm.predictions.write_predictions`."""
    created = created_at if created_at is not None else now_utc()
    horizon = int(settings().horizons["waiver_radar_weeks"])
    as_of = run.rows.select(*KEYS, "as_of")
    preds = []
    for mr in run.runs.values():
        preds.append(
            mr.predictions.join(as_of, on=list(KEYS), how="left").select(
                pl.lit(MODULE).alias("module"),
                pl.lit("player").alias("entity_type"),
                pl.col(ID).alias("entity_id"),
                "season",
                "week",
                "as_of",
                pl.lit(horizon, dtype=pl.Int32).alias("horizon"),
                pl.col("position").alias("rank_group"),
                pl.col(SCORE).alias("score"),
                pl.col(RAW_SCORE).alias("raw_score"),
                "rank",
                pl.lit(None, dtype=pl.String).alias("band"),
                "model_version",
                pl.lit("[]").alias("reasons_json"),
                pl.lit("backtest").alias("kind"),
                pl.lit(created, dtype=pl.Datetime("us")).alias("created_at"),
            )
        )
    versions = pl.DataFrame(
        [v for mr in run.runs.values() for v in mr.versions],
        schema_overrides={"test_season": pl.Int32},
    ).with_columns(
        pl.lit(code_version()).alias("code_version"),
        pl.lit(created, dtype=pl.Datetime("us")).alias("created_at"),
    )
    outcomes = run.rows.filter(pl.col("season").is_in(list(run.test_seasons))).select(
        pl.lit(MODULE).alias("module"),
        pl.col(ID).alias("entity_id"),
        "season",
        "week",
        "as_of",
        "y_hit",
        "y_sustained",
        "label_status",
    )
    return pl.concat(preds, how="vertical"), versions, outcomes


# --------------------------------------------------------------------------------------
# Grading
# --------------------------------------------------------------------------------------


@dataclass
class Graded:
    """One model's graded predictions for one label."""

    model: str
    label: str
    rows: pl.DataFrame  # KEYS, position, raw_score, score, y, owned_avg, ecr_available
    groups: dict[str, pl.DataFrame]  # subset -> per-(as-of, position) precision@K table

    def pooled(
        self,
        subset: str,
        seasons: Sequence[int],
        positions: Sequence[str] | None = None,
        only: pl.DataFrame | None = None,
    ) -> Pooled:
        g = self.groups[subset].filter(pl.col("season").is_in(list(seasons)))
        if positions is not None:
            g = g.filter(pl.col("position").is_in(list(positions)))
        if only is not None:
            g = g.join(only, on=list(GROUP), how="semi")
        return pooled_precision(g)


def _without_rostered(df: pl.DataFrame) -> pl.DataFrame:
    owned = (pl.col("owned_avg") >= rostered_threshold()).fill_null(False)
    return df.filter(~owned)


def grade(run: BacktestRun, label: str, model: str) -> Graded:
    mr = run.run(label, model)
    truth = run.rows.select(*KEYS, pl.col(label).alias("y"), "owned_avg", "ecr_available")
    rows = mr.predictions.select(*KEYS, "position", RAW_SCORE, SCORE).join(
        truth, on=list(KEYS), how="left"
    )
    groups = {
        "all": precision_table(rows, "y", K),
        "without_rostered": precision_table(_without_rostered(rows), "y", K),
    }
    return Graded(model, label, rows, groups)


def grade_reference(run: BacktestRun, label: str, column: str = REFERENCE_FEATURE) -> Graded:
    """Context, not a baseline of the spec: rank the same test rows by one feature alone."""
    test = run.rows.filter(pl.col("season").is_in(list(run.test_seasons)))
    value = pl.col(column).cast(pl.Float64)
    rows = test.select(
        *KEYS, "position", value.alias(RAW_SCORE), value.alias(SCORE), pl.col(label).alias("y"),
        "owned_avg", "ecr_available",
    )  # fmt: skip
    groups = {
        "all": precision_table(rows, "y", K),
        "without_rostered": precision_table(_without_rostered(rows), "y", K),
    }
    return Graded(f"reference: {column} alone", label, rows, groups)


def season_ranges(tests: Sequence[int]) -> list[tuple[int, int]]:
    """(first, last) of the test seasons, and the same without the first (thin) season."""
    first, last = min(tests), max(tests)
    out = [(first, last)]
    if first + 1 <= last:
        out.append((first + 1, last))
    return out


def _years(r: tuple[int, int]) -> list[int]:
    return list(range(r[0], r[1] + 1))


def _rng(r: tuple[int, int]) -> str:
    return f"{r[0]}-{r[1]}" if r[0] != r[1] else str(r[0])


@dataclass
class Verdict:
    label: str
    winner: str | None
    accepted: bool | None  # None when a naive baseline or both models are missing
    text: str


def verdict(graded: dict[str, Graded], label: str, tests: Sequence[int]) -> Verdict:
    """Winner = the model (logit, lgbm) with the higher pooled P@10 over all test seasons (ties
    -> logit, the simpler one); accepted = it beats BOTH naive baselines there (spec P1)."""
    full = _years(season_ranges(tests)[0])
    models = [m for m in MODELS if m in graded]
    if not models:
        return Verdict(label, None, None, "no model was run, so there is no winner")
    scores = {m: graded[m].pooled("all", full).value or 0.0 for m in models}
    winner = max(models, key=lambda m: (scores[m], m == "logit"))
    naive = [b for b in NAIVE_BASELINES if b in graded]
    if len(naive) < len(NAIVE_BASELINES):
        return Verdict(
            label,
            winner,
            None,
            f"winner {winner}; the naive baselines were not all "
            "run, so the acceptance test was not evaluated",
        )
    base = {b: graded[b].pooled("all", full).value or 0.0 for b in naive}
    ok = all(scores[winner] > v for v in base.values())
    vs = ", ".join(f"{b} {_p(v)}" for b, v in base.items())
    text = (
        f"{'PASS' if ok else 'FAIL'}: the winner {winner} ({_p(scores[winner])}) "
        f"{'beats' if ok else 'does not beat'} both naive baselines ({vs}) on pooled "
        f"precision@{K} over {_rng(season_ranges(tests)[0])}"
    )
    return Verdict(label, winner, ok, text)


# --------------------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BacktestReport:
    markdown: str
    csv_rows: list[dict[str, object]]
    summary: list[str]
    verdicts: dict[str, Verdict]


def _p(x: float | None, digits: int = 1) -> str:
    return "-" if x is None else f"{100 * x:.{digits}f}%"


def _f(x: float | None, digits: int = 4) -> str:
    return "-" if x is None else f"{x:.{digits}f}"


def _table(header: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


def _csv_row(
    label: str,
    model: str,
    subset: str,
    scope: str,
    seasons: str,
    key: object,
    metric: str,
    value: float | None,
    n_groups: int | None = None,
    n_rows: int | None = None,
    n_pos: int | None = None,
) -> dict[str, object]:
    return {
        "label": label,
        "model": model,
        "subset": subset,
        "scope": scope,
        "seasons": seasons,
        "key": key,
        "metric": metric,
        "value": "" if value is None else f"{value:.6f}",
        "n_groups": "" if n_groups is None else n_groups,
        "n_rows": "" if n_rows is None else n_rows,
        "n_pos": "" if n_pos is None else n_pos,
    }


def _covered_groups(run: BacktestRun) -> pl.DataFrame:
    """(season, week, position) groups of the test seasons where an experts' page existed."""
    return (
        run.rows.filter(
            pl.col("season").is_in(list(run.test_seasons))
            & pl.col("ecr_available").fill_null(False)
        )
        .select(GROUP)
        .unique()
        .sort(list(GROUP))
    )


def _fold_table(run: BacktestRun) -> list[str]:
    """The walk-forward plan: what each test season was trained, tuned and validated on."""
    rows = []
    for f in plan_folds(run.season_hashes, run.test_seasons):
        train = run.rows.filter(pl.col("season").is_in(list(f.train_seasons)))
        rows.append(
            [
                f.test_season,
                season_span(f.train_seasons),
                season_span(f.tune_seasons) if not f.thin else "-",
                f.val_season if not f.thin else "- (thin)",
                f"{train.height:,}",
                f"{run.rows.filter(pl.col('season') == f.test_season).height:,}",
            ]
        )
    return _table(
        ["test season", "final model trained on", "tuning model trained on", "validated on",
         "training rows", "test rows"],
        rows,
    )  # fmt: skip


def _label_section(
    run: BacktestRun, label: str, graded: dict[str, Graded], v: Verdict, csv_rows: list
) -> list[str]:
    tests = run.test_seasons
    ranges = season_ranges(tests)
    full = _years(ranges[0])
    models = [m for m in run.models if m in graded]
    lines = [f"## Label `{label}`", ""]
    base_all = graded[models[0]].pooled("all", full)
    lines += [
        f"Evaluation rows {base_all.n_rows:,} in {base_all.n_groups:,} (as-of, position) lists, "
        f"{base_all.n_pos:,} hits (base rate {_p(base_all.base_rate)}: the precision a random "
        "ranking would get).",
        "",
        f"**Verdict:** {v.text}.",
        "",
        f"### Precision@{K}, pooled",
        "",
        f"The share of each list's top {K} who hit, averaged over every (as-of, position) list. "
        "`without rostered`: pool players owned in at least "
        f"{rostered_threshold():g}% of real leagues (FantasyPros, 2020 on) removed and the rest "
        "re-ranked.",
        "",
    ]
    header = ["model"]
    for r in ranges:
        header += [f"{_rng(r)} {SUBSET_TITLES[s]}" for s in SUBSETS]
    header += ["lists", "short lists"]
    rows = []
    for m in models:
        g = graded[m]
        row: list[object] = [m]
        n_groups = n_short = 0
        for r in ranges:
            for s in SUBSETS:
                if BASELINES.get(m) and BASELINES[m].needs:
                    row.append("n/a")
                    continue
                pp = g.pooled(s, _years(r))
                row.append(_p(pp.value))
                csv_rows.append(
                    _csv_row(
                        label,
                        m,
                        s,
                        "pooled",
                        _rng(r),
                        "",
                        f"p_at_{K}",
                        pp.value,
                        pp.n_groups,
                        pp.n_rows,
                        pp.n_pos,
                    )
                )
                if r == ranges[0] and s == "all":
                    n_groups, n_short = pp.n_groups, pp.n_short
        row += [f"{n_groups:,}" if n_groups else "-", n_short if n_groups else "-"]
        rows.append(row)
    ref = grade_reference(run, label)
    row = [f"*{ref.model}*"]
    for r in ranges:
        for s in SUBSETS:
            pp = ref.pooled(s, _years(r))
            row.append(f"*{_p(pp.value)}*")
            csv_rows.append(
                _csv_row(label, f"reference_{REFERENCE_FEATURE}", s, "pooled", _rng(r), "",
                         f"p_at_{K}", pp.value, pp.n_groups, pp.n_rows, pp.n_pos)
            )  # fmt: skip
    rows.append([*row, "", ""])
    lines += _table(header, rows)
    lines += [
        "",
        f"Short lists have fewer than {K} rows (precision divides by their size). "
        "`baseline_ecr` exists only from 2020: see the experts' section below. The last row "
        f"(italics) is context, not a baseline: the same lists ranked by `{REFERENCE_FEATURE}` "
        "(expected fantasy points over the last 3 games), the strongest single feature of the "
        "C3 feature report, alone.",
        "",
    ]
    # seasons won
    if v.winner is not None:
        rows = []
        for b in NAIVE_BASELINES:
            if b not in graded:
                continue
            won = 0
            for s in tests:
                mv, bv = (
                    graded[v.winner].pooled("all", [s]).value,
                    graded[b].pooled("all", [s]).value,
                )
                won += int((mv or 0) > (bv or 0))
            rows.append([f"{v.winner} vs {b}", f"{won} of {len(tests)}"])
        if rows:
            lines += [f"Seasons in which the winner's precision@{K} is higher:", ""]
            lines += _table(["comparison", "seasons won"], rows)
            lines.append("")
    # per position
    lines += [f"### Per position ({_rng(ranges[0])}, all pool rows)", ""]
    header = ["position", "lists", "hits", "base rate", *models]
    rows = []
    for pos in FANTASY_POSITIONS:
        ref = graded[models[0]].pooled("all", full, [pos])
        row = [pos, f"{ref.n_groups:,}", f"{ref.n_pos:,}", _p(ref.base_rate)]
        for m in models:
            if BASELINES.get(m) and BASELINES[m].needs:
                row.append("n/a")
                continue
            for s in SUBSETS:
                pp = graded[m].pooled(s, full, [pos])
                csv_rows.append(
                    _csv_row(
                        label,
                        m,
                        s,
                        "position",
                        _rng(ranges[0]),
                        pos,
                        f"p_at_{K}",
                        pp.value,
                        pp.n_groups,
                        pp.n_rows,
                        pp.n_pos,
                    )
                )
            row.append(_p(graded[m].pooled("all", full, [pos]).value))
        rows.append(row)
    lines += _table(header, rows)
    lines += ["", f"Without rostered players ({_rng(ranges[0])}):", ""]
    rows = []
    for pos in FANTASY_POSITIONS:
        row = [pos]
        for m in models:
            if BASELINES.get(m) and BASELINES[m].needs:
                row.append("n/a")
                continue
            row.append(_p(graded[m].pooled("without_rostered", full, [pos]).value))
        rows.append(row)
    lines += _table(["position", *models], rows)
    # per season
    lines += ["", "### Per season (all pool rows)", ""]
    rows = []
    for s in tests:
        ref = graded[models[0]].pooled("all", [s])
        row = [s, f"{ref.n_groups}", f"{ref.n_pos:,}", _p(ref.base_rate)]
        for m in models:
            pp = graded[m].pooled("all", [s])
            ppw = graded[m].pooled("without_rostered", [s])
            csv_rows.append(
                _csv_row(
                    label,
                    m,
                    "all",
                    "season",
                    str(s),
                    s,
                    f"p_at_{K}",
                    pp.value,
                    pp.n_groups,
                    pp.n_rows,
                    pp.n_pos,
                )
            )
            csv_rows.append(
                _csv_row(
                    label,
                    m,
                    "without_rostered",
                    "season",
                    str(s),
                    s,
                    f"p_at_{K}",
                    ppw.value,
                    ppw.n_groups,
                    ppw.n_rows,
                    ppw.n_pos,
                )
            )
            row.append(_p(pp.value) if pp.n_groups else "-")
        rows.append(row)
    lines += _table(["season", "lists", "hits", "base rate", *models], rows)
    owned_seasons = [
        s
        for s in tests
        if graded[models[0]]
        .rows.filter((pl.col("season") == s) & (pl.col("owned_avg") >= rostered_threshold()))
        .height
    ]
    if owned_seasons:
        lines += ["", "Without rostered players (the seasons with rostership figures):", ""]
        rows = []
        for s in owned_seasons:
            ref = graded[models[0]].pooled("without_rostered", [s])
            row = [s, f"{graded[models[0]].pooled('all', [s]).n_rows - ref.n_rows:,}",
                   f"{ref.n_pos:,}", _p(ref.base_rate)]  # fmt: skip
            for m in models:
                pp = graded[m].pooled("without_rostered", [s])
                row.append(_p(pp.value) if pp.n_groups else "-")
            rows.append(row)
        lines += _table(["season", "rows removed", "hits left", "base rate", *models], rows)
    # rank buckets
    lines += [
        "",
        f"### Hit rate by rank ({_rng(ranges[0])}, all pool rows)",
        "",
        "The share of players ranked 1-5, 6-10 and 11-25 in their list who hit.",
        "",
    ]
    rows = []
    for m in models:
        if BASELINES.get(m) and BASELINES[m].needs:
            continue
        ranked = rank_scores(graded[m].rows.filter(pl.col("season").is_in(full)))
        row = [m]
        for name, n, hits, rate in bucket_rates(ranked, label="y", buckets=DEFAULT_BUCKETS):
            row.append(f"{_p(rate)} ({hits:,}/{n:,})")
            csv_rows.append(
                _csv_row(
                    label,
                    m,
                    "all",
                    "bucket",
                    _rng(ranges[0]),
                    name,
                    "hit_rate",
                    rate,
                    None,
                    n,
                    hits,
                )
            )
        rows.append(row)
    lines += _table(["model", *(f"ranks {lo}-{hi}" for lo, hi in DEFAULT_BUCKETS)], rows)
    # PR-AUC and Brier
    fitted = [m for m in models if m in MODELS]
    if fitted:
        lines += [
            "",
            "### PR-AUC and Brier (models)",
            "",
            "PR-AUC (average precision): how well the probabilities rank hits above misses "
            "across all rows of the season (higher is better). Brier: the mean squared "
            "error of the calibrated probability (lower is better); `base-rate Brier` is "
            "what always predicting the training seasons' hit rate would score.",
            "",
        ]
        header = ["season", "hits / rows", "base-rate Brier"]
        for m in fitted:
            header += [f"{m} PR-AUC", f"{m} Brier"]
        rows = []
        for key, seasons in [
            *((str(s), [s]) for s in tests),
            *((f"{_rng(r)} pooled", _years(r)) for r in ranges),
        ]:
            ref = graded[fitted[0]].rows.filter(pl.col("season").is_in(seasons))
            y = ref.get_column("y").cast(pl.Int64).to_numpy()
            base = _base_rate_brier(run, label, seasons)
            row = [key, f"{int(y.sum()):,} / {y.size:,}", _f(base)]
            csv_rows.append(
                _csv_row(
                    label,
                    "base_rate",
                    "all",
                    "season",
                    key,
                    key,
                    "brier",
                    base,
                    None,
                    y.size,
                    int(y.sum()),
                )
            )
            for m in fitted:
                sub = graded[m].rows.filter(pl.col("season").is_in(seasons))
                yy = sub.get_column("y").cast(pl.Int64).to_numpy()
                sc = sub.get_column(SCORE).to_numpy()
                a, b = pr_auc(yy, sc), brier(yy, sc)
                row += [_f(a), _f(b)]
                csv_rows.append(
                    _csv_row(
                        label,
                        m,
                        "all",
                        "season",
                        key,
                        key,
                        "pr_auc",
                        a,
                        None,
                        yy.size,
                        int(yy.sum()),
                    )
                )
                csv_rows.append(
                    _csv_row(
                        label,
                        m,
                        "all",
                        "season",
                        key,
                        key,
                        "brier",
                        b,
                        None,
                        yy.size,
                        int(yy.sum()),
                    )
                )
            rows.append(row)
        lines += _table(header, rows)
    # experts
    lines += ["", f"### The experts' ranks ({label})", ""]
    lines += _experts_section(run, label, graded, csv_rows)
    # calibration and importance of the winner
    if v.winner is not None:
        lines += [
            "",
            f"### Calibration of the winner ({v.winner}, {_rng(ranges[0])})",
            "",
            "Rows sorted by predicted probability and cut into 10 equal-size bins: in a "
            "well-calibrated model the mean prediction of a bin matches the share of its "
            "rows that hit.",
            "",
        ]
        g = graded[v.winner].rows.filter(pl.col("season").is_in(full))
        bins = calibration_bins(g, prob=SCORE, label="y", id_cols=list(KEYS))
        rows = []
        for b in bins.iter_rows(named=True):
            rows.append(
                [
                    b["bin"],
                    f"{b['n']:,}",
                    _p(b["mean_pred"]),
                    _p(b["observed"]),
                    f"{_p(b['min_pred'])}-{_p(b['max_pred'])}",
                ]
            )
            csv_rows.append(
                _csv_row(
                    label,
                    v.winner,
                    "all",
                    "calibration",
                    _rng(ranges[0]),
                    b["bin"],
                    "mean_pred",
                    b["mean_pred"],
                    None,
                    b["n"],
                )
            )
            csv_rows.append(
                _csv_row(
                    label,
                    v.winner,
                    "all",
                    "calibration",
                    _rng(ranges[0]),
                    b["bin"],
                    "observed",
                    b["observed"],
                    None,
                    b["n"],
                )
            )
        lines += _table(["bin", "rows", "mean predicted", "observed", "predicted range"], rows)
        lines += ["", *_importance_section(run, label, v.winner, csv_rows)]
    for m in fitted:
        if m != v.winner:
            lines += ["", *_params_section(run, label, m, csv_rows)]
    lines.append("")
    return lines


def _base_rate_brier(run: BacktestRun, label: str, seasons: Sequence[int]) -> float | None:
    """Brier of predicting, for each test season, the hit rate of its training seasons."""
    num, den = 0.0, 0
    for s in seasons:
        train = run.rows.filter(pl.col("season") < s).get_column(label).cast(pl.Float64)
        test = run.rows.filter(pl.col("season") == s).get_column(label).cast(pl.Float64)
        if train.len() == 0 or test.len() == 0:
            continue
        p = float(train.mean())  # type: ignore[arg-type]
        num += float(((test - p) ** 2).sum())
        den += test.len()
    return num / den if den else None


def _experts_section(
    run: BacktestRun, label: str, graded: dict[str, Graded], csv_rows: list
) -> list[str]:
    cov = _covered_groups(run)
    test = run.rows.filter(pl.col("season").is_in(list(run.test_seasons)))
    seasons_with = sorted(set(cov.get_column("season").to_list()))
    lines = [
        "FantasyPros rest-of-season (else weekly) positional ranks visible at the as-of "
        "(metric columns, never model features). The archive starts in December 2019 and its "
        "pages were saved on Fridays: at the Tuesday as-of the experts had not seen the last "
        "weekend yet. Graded only on the lists where a page existed, against every model on "
        "the same lists.",
        "",
    ]
    if cov.height == 0:
        return [*lines, "No test list has an experts' page."]
    covered_rows = test.join(cov, on=list(GROUP), how="semi")
    ranked = covered_rows.filter(pl.col("ecr_pos_rank").is_not_null())
    hits = covered_rows.filter(pl.col(label))
    by_kind = dict(ranked.group_by("ecr_page_kind").len().sort("ecr_page_kind").iter_rows())
    all_lists = test.select(GROUP).unique().filter(pl.col("season").is_in(seasons_with)).height
    lines += [
        f"Coverage: {cov.height:,} of {all_lists:,} lists of {seasons_with[0]}-{seasons_with[-1]} "
        f"had a page; in them {ranked.height:,} of {covered_rows.height:,} pool rows "
        f"({_p(ranked.height / covered_rows.height)}) carry an expert rank "
        f"({', '.join(f'{k} {n:,}' for k, n in by_kind.items())}), and "
        f"{hits.filter(pl.col('ecr_pos_rank').is_not_null()).height:,} of {hits.height:,} hits "
        f"({_p(hits.filter(pl.col('ecr_pos_rank').is_not_null()).height / max(hits.height, 1))}). "
        "Unranked players go to the bottom of the experts' list.",
        "",
    ]
    csv_rows.append(
        _csv_row(
            label,
            "baseline_ecr",
            "all",
            "coverage",
            f"{seasons_with[0]}-{seasons_with[-1]}",
            "lists",
            "share_covered",
            cov.height / all_lists if all_lists else None,
            cov.height,
            covered_rows.height,
            ranked.height,
        )
    )
    header = ["model", *(SUBSET_TITLES[s] for s in SUBSETS)]
    rows = []
    seasons = list(run.test_seasons)
    for m in run.models:
        if m not in graded:
            continue
        row: list[object] = [m]
        for s in SUBSETS:
            pp = graded[m].pooled(s, seasons, only=cov)
            row.append(_p(pp.value))
            csv_rows.append(
                _csv_row(
                    label,
                    m,
                    s,
                    "ecr_covered",
                    f"{seasons_with[0]}-{seasons_with[-1]}",
                    "",
                    f"p_at_{K}",
                    pp.value,
                    pp.n_groups,
                    pp.n_rows,
                    pp.n_pos,
                )
            )
        rows.append(row)
    lines += [f"Precision@{K} on the {cov.height:,} covered lists:", ""]
    lines += _table(header, rows)
    return lines


def _importance_section(run: BacktestRun, label: str, model: str, csv_rows: list) -> list[str]:
    mr = run.run(label, model)
    kind = mr.folds[0].importance_kind if mr.folds else ""
    lines = [
        f"### The winner per fold: hyperparameters, calibration, top features ({model})",
        "",
        f"Importance = share of the total {kind} of the final model of each fold (spec 6.2 rule "
        f"6: a feature above {DOMINANCE_SHARE:.0%} is flagged for a closer look). For the "
        "logistic regression the measure is how much the feature moves the log-odds across the "
        "training rows (the standard deviation of its term; its value, its missing-value "
        "indicator and, for `position`, its one-hot columns together). `position` shifts every "
        "player of a position alike, so it cannot change a ranking within one position.",
        "",
    ]
    rows = []
    for fr in mr.folds:
        top = ", ".join(f"{n} {100 * s:.0f}%" for n, s in fr.importance[:TOP_FEATURES])
        cal = "thin (cross-fitted)" if fr.fold.thin else f"on {fr.fold.val_season}"
        rows.append([fr.fold.test_season, _params_text(fr.params), cal, top])
        for n, s in fr.importance:
            csv_rows.append(
                _csv_row(label, model, "all", "importance", str(fr.fold.test_season), n,
                         "importance_share", s)
            )  # fmt: skip
        for t in fr.trials:
            csv_rows.append(
                _csv_row(label, model, "all", "tuning", str(fr.fold.test_season),
                         _params_text(t.params), f"val_p_at_{K}", t.metric)
            )  # fmt: skip
    lines += _table(["test season", "hyperparameters", "calibrated", "top features"], rows)
    lines += ["", _dominance_line(mr.folds)]
    agg: dict[str, float] = {}
    for fr in mr.folds:
        for n, s in fr.importance:
            agg[n] = agg.get(n, 0.0) + s / len(mr.folds)
    top = sorted(agg.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    lines += ["", "Mean share over the folds (top 10):", ""]
    lines += _table(["feature", "mean share"], [[n, _p(s)] for n, s in top])
    return lines


def _params_text(params: dict[str, Any]) -> str:
    short = {
        "num_leaves": "leaves",
        "min_child_samples": "min_child",
        "colsample_bytree": "feat_frac",
        "learning_rate": "lr",
        "n_estimators": "trees",
        "l1_ratio": "l1",
    }
    return " ".join(
        f"{short.get(k, k)}={v:g}" if isinstance(v, float) else f"{short.get(k, k)}={v}"
        for k, v in sorted(params.items())
    )


def _dominance_line(folds: Sequence[FoldResult]) -> str:
    flagged = [
        f"{fr.fold.test_season}: {fr.dominant[0]} {_p(fr.dominant[1])}"
        for fr in folds
        if fr.dominant is not None
    ]
    if not flagged:
        return f"Dominance check: no feature holds more than {DOMINANCE_SHARE:.0%} in any fold."
    return (
        f"Dominance check: one feature holds more than {DOMINANCE_SHARE:.0%} in "
        + "; ".join(flagged)
        + " (investigate: see docs/waiver_radar.md, 'Models and backtest')."
    )


def _params_section(run: BacktestRun, label: str, model: str, csv_rows: list) -> list[str]:
    mr = run.run(label, model)
    rows = []
    for fr in mr.folds:
        top = ", ".join(f"{n} {100 * s:.0f}%" for n, s in fr.importance[:3])
        rows.append([fr.fold.test_season, _params_text(fr.params), top])
        for n, s in fr.importance:
            csv_rows.append(
                _csv_row(label, model, "all", "importance", str(fr.fold.test_season), n,
                         "importance_share", s)
            )  # fmt: skip
        for t in fr.trials:
            csv_rows.append(
                _csv_row(label, model, "all", "tuning", str(fr.fold.test_season),
                         _params_text(t.params), f"val_p_at_{K}", t.metric)
            )  # fmt: skip
    kind = mr.folds[0].importance_kind if mr.folds else ""
    return [
        f"### {model} per fold (importance: share of the total {kind})",
        "",
        *_table(["test season", "hyperparameters", "top 3 features"], rows),
        "",
        _dominance_line(mr.folds),
    ]


def build_backtest_report(
    run: BacktestRun,
    *,
    created: str = "",
    command: str = "uv run twm radar backtest",
    dataset_name: str = "data/waiver_radar/dataset.parquet",
) -> BacktestReport:
    """The markdown report, its CSV rows and a short summary. ``created`` is the only
    run-dependent text (one line)."""
    csv_rows: list[dict[str, object]] = []
    verdicts: dict[str, Verdict] = {}
    graded_by_label = {label: {m: grade(run, label, m) for m in run.models} for label in run.labels}
    for label in run.labels:
        verdicts[label] = verdict(graded_by_label[label], label, run.test_seasons)
    primary = "y_hit" if "y_hit" in run.labels else run.labels[0]
    first, last = min(run.test_seasons), max(run.test_seasons)
    all_hash = combine_hashes(list(run.row_hashes.values()))
    lines = [
        "# Waiver Radar backtest (step C4)",
        "",
        f"Generated by `{command}` from `{dataset_name}`. {created}".rstrip(),
        "",
        "**How to read this.** Every Tuesday of every season from "
        f"{first} to {last}, the Radar ranks the players probably on waivers at each position. "
        f"A model graded on a season learned only from earlier seasons (walk-forward), and "
        f"every number here comes from those out-of-sample predictions. **Precision@{K}** is "
        f"the share of a list's top {K} who became a fantasy starter within their next 3 games "
        f"(`y_hit`; `y_sustained` = at least twice): if you added the Radar's top {K} each "
        f"week, it is the share that would have paid off. The **verdict** follows the spec's P1 "
        "acceptance test: the better model must beat both naive baselines (last week's points, "
        "snap-share change). docs/waiver_radar.md explains everything in plain words.",
        "",
        "## Setup",
        "",
        f"- Rows: pool rows with a final label and at least 2 window games "
        f"({run.rows.filter(pl.col('season').is_in(list(run.test_seasons))).height:,} test rows "
        f"in {first}-{last}; training uses the seasons before each test season, from "
        f"{min(run.season_hashes)}).",
        f"- Features: the {len(FEATURES)} registered Waiver Radar features "
        "(`docs/glossary.md`); `position` is categorical; no identifiers.",
        f"- Labels: {', '.join(f'`{lb}`' for lb in run.labels)}; models: "
        f"{', '.join(f'`{m}`' for m in run.models)}.",
        "- Lists: one per (as-of, position); ranks by score (ties: the model's uncalibrated "
        "score, then the player id). Baselines rank by their column; a player without a value "
        "goes to the bottom.",
        "- Tuning: on the validation season (the last training season) by pooled precision@10, "
        "ties to the higher PR-AUC; logit: C x L1/L2; lgbm: leaves x min_child x feature "
        "fraction, trees by early stopping. Calibration: isotonic regression on the validation "
        "season's scores, applied to the model refit on all training seasons; the first "
        "season (one training season, 'thin') uses default hyperparameters and 4-fold "
        "cross-fitting by week for the calibrator.",
        f"- Dataset content hash (all seasons): `{all_hash[:16]}`.",
        "",
        *_fold_table(run),
        "",
    ]
    lines += ["## Verdict", ""]
    for label in run.labels:
        tag = " (primary)" if label == primary else ""
        lines.append(f"- `{label}`{tag}: {verdicts[label].text}.")
    lines.append("")
    for label in run.labels:
        lines += _label_section(run, label, graded_by_label[label], verdicts[label], csv_rows)
    csv_rows.sort(key=lambda r: tuple(str(r[c]) for c in CSV_COLUMNS[:7]))
    v = verdicts[primary]
    summary = [f"{label}: {verdicts[label].text}" for label in run.labels]
    if v.winner:
        g = graded_by_label[primary]
        full = _years(season_ranges(run.test_seasons)[0])
        summary.append(
            f"{primary} pooled P@{K} {_rng(season_ranges(run.test_seasons)[0])}: "
            + ", ".join(
                f"{m} {_p(g[m].pooled('all', full).value)}"
                for m in run.models
                if not (BASELINES.get(m) and BASELINES[m].needs)
            )
        )
    return BacktestReport("\n".join(lines).rstrip() + "\n", csv_rows, summary, verdicts)


def write_backtest_report(report: BacktestReport, md_path: Path) -> Path:
    """Write the markdown and ``<same name>.csv`` next to it; return the CSV path."""
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(report.markdown, encoding="utf-8")
    csv_path = md_path.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(CSV_COLUMNS), lineterminator="\n")
        w.writeheader()
        w.writerows(report.csv_rows)
    return csv_path


__all__ = [
    "BacktestReport",
    "BacktestRun",
    "ModelRun",
    "build_backtest_report",
    "grade",
    "run_backtest",
    "store_frames",
    "verdict",
    "write_backtest_report",
]
