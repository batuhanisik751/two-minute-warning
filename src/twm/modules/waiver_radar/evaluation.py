"""Waiver Radar evaluation (C5): metrics, honest intervals, breakouts caught, the report.

**One source of truth.** Every number here is computed from the predictions store
(``data/predictions.duckdb``, written by ``twm radar backtest``): each backtest prediction, its
rank in its weekly list and the outcome. The time machine (C6, spec 8.7) reads the same rows,
so the report and the time machine can never disagree. Nothing is re-predicted. The dataset
(``data/waiver_radar/dataset.parquet``) adds descriptive columns only: names and teams,
FantasyPros rostership (``owned_avg``, for the numbers without rostered players), whether an
experts' page existed (``ecr_available``), and the labels and weekly ranks of every rostered
player (for "breakouts caught"). The dataset's labels are checked against the store's outcomes
row by row; a mismatch stops the evaluation (the store is stale: re-run the backtest).

**Which predictions.** For every (model, label, test season) of the evaluation seasons
(settings ``seasons.waiver_radar_eval``) the model version written last
(:func:`twm.predictions.current_versions`): a backtest re-run after a change adds new versions
and leaves the old ones in the store. The live season's production fold (C6) is left out until
its outcomes are known.

**What is measured** (PROJECT_SPEC 8.1), per label, per method, pooled and per position, with the
number of lists (weeks x positions) and hits next to every rate:

- precision@10 per weekly list and pooled (the mean over lists), per season and its spread;
- the same WITHOUT the pool players real leagues had rostered (owned in at least 50% of
  leagues, FantasyPros, 2020 on): the rows are removed and the lists re-ranked;
- 95% intervals by the **season-block bootstrap** (:func:`twm.backtest.metrics.block_bootstrap`:
  the 12 test seasons are resampled with replacement 2,000 times with a fixed seed) for pooled
  precision@10 and for the **paired** difference between methods (each model minus each naive
  baseline, logistic regression minus LightGBM, the models minus the experts on the lists the
  experts covered);
- hit rate by rank bucket (1-5, 6-10, 11-25);
- PR-AUC and Brier (the models' calibrated probabilities), calibration in equal-count and
  fixed-width bins with counts;
- **breakouts caught** (:func:`breakout_events`): a breakout is a player-season in which a
  player the Radar ranked (a pool row) had two starter finishes inside one window (``y_sustained``)
  at that as-of or a later one of the same season, in or out of the pool by then; his breakout
  as-of is the first such as-of. A method caught him if it ranked him in the top 10 of his
  position at that as-of or an earlier one of the season: never a later one, so nothing is
  judged with hindsight.

Deterministic: fixed seed, total sorts; the report differs between runs only in its
"Generated at" line.
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import polars as pl

from twm import predictions as pr
from twm.backtest.metrics import (
    BOOTSTRAP_SEED,
    DEFAULT_BUCKETS,
    N_BOOT,
    Interval,
    add_rank,
    block_bootstrap,
    brier,
    bucket_rates,
    calibration_bins,
    calibration_fixed_bins,
    caught_before,
    first_events,
    paired_block_bootstrap,
    pooled_precision,
    pr_auc,
    precision_at_k,
)
from twm.backtest.walkforward import RAW_SCORE, SCORE
from twm.config import FANTASY_POSITIONS, league
from twm.modules.waiver_radar.backtest import (
    SUBSETS,
    eval_seasons,
    rostered_threshold,
    season_ranges,
)
from twm.modules.waiver_radar.models import (
    BASELINES,
    GROUP,
    ID,
    KEYS,
    LABELS,
    MODELS,
    MODULE,
    NAIVE_BASELINES,
    K,
    model_rows,
)

EXPERTS = "baseline_ecr"
# Display order: the models, then the experts, then the naive baselines.
METHOD_ORDER = ("logit", "lgbm", EXPERTS, "baseline_last_points", "baseline_snap_delta")
TITLES = {
    "logit": "logistic regression",
    "lgbm": "LightGBM",
    EXPERTS: "the experts (FantasyPros)",
    "baseline_last_points": "last week's points",
    "baseline_snap_delta": "snap-share change",
}
SUBSET_TITLES = {"all": "all pool rows", "without_rostered": "without rostered"}
BREAKOUT_LABEL = "y_sustained"
RECENT_ASOFS = 3  # "caught recently": a top-10 rank in the 3 Tuesdays up to the breakout
N_LIST = 10  # players in each "biggest breakouts" list
LIST_GROUP = ("model", *GROUP)
DATASET_COLUMNS = (
    "season", "week", "gsis_id", "name", "team", "position", "in_pool", "train_eligible",
    "label_status", "owned_avg", "ecr_available", "preseason_pos_rank", "window_weeks",
    "window_ranks", "window_points", "y_hit", "y_sustained",
)  # fmt: skip
FIGURE_CAPTIONS = {
    "precision_by_season": "Precision@10 by season, one line per method, with the base rate",
    "precision_pooled": "Pooled precision@10 per method with 95% intervals, per position",
    "hit_rate_by_rank": "Hit rate by rank bucket per method",
    "calibration": "Calibration of the winner for both labels, with counts",
    "breakouts_caught": "Breakouts caught per method",
}
CSV_COLUMNS = (
    "label", "subset", "model", "scope", "seasons", "key", "metric", "value", "lo", "hi",
    "n_groups", "n_rows", "n_pos", "n_top_hits", "n_top",
)  # fmt: skip


class EvaluationError(ValueError):
    """The store and the dataset cannot be evaluated together (missing or stale rows)."""


# --------------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------------


def load_predictions(
    store: Path | str, label: str, *, seasons: tuple[int, int] | None = None
) -> pl.DataFrame:
    """The current backtest predictions of every method for ``label``, with the outcome
    (``y``): model, season, week, gsis_id, position, score, raw_score, rank, model_version.

    Only the model versions whose test season is in ``seasons`` (first, last; default the
    evaluation seasons, settings ``seasons.waiver_radar_eval``): the production fold of the
    live season (C6) scores weeks whose outcomes are not known yet and is graded later."""
    if label not in LABELS:
        raise ValueError(f"unknown label {label!r}; labels: {LABELS}")
    if not Path(store).exists():
        raise EvaluationError(f"predictions store not found: {store}; run `twm radar backtest`")
    first, last = seasons if seasons is not None else eval_seasons()
    versions = pr.current_versions(store, MODULE, label).filter(
        pl.col("test_season").is_between(first, last)
    )
    if versions.height == 0:
        raise EvaluationError(
            f"no {label} predictions in the store; run `twm radar backtest --label {label}`"
        )
    con = pr.connect(store, read_only=True)
    try:
        con.register("v", versions.select("model_version", "model", "test_season").to_arrow())
        df = con.execute(
            f"""
            SELECT v.model, p.season, p.week, p.entity_id AS gsis_id, p.rank_group AS position,
                   p.score, p.raw_score, p.rank, p.model_version, v.test_season,
                   o.{label} AS y, o.label_status
            FROM predictions p JOIN v USING (model_version)
            LEFT JOIN outcomes o ON o.module = p.module AND o.entity_id = p.entity_id
                 AND o.season = p.season AND o.week = p.week
            WHERE p.module = ? AND p.kind = 'backtest'
            ORDER BY v.model, p.season, p.week, p.rank_group, p.rank
            """,
            [MODULE],
        ).pl()
    finally:
        con.close()
    if df.filter(pl.col("season") != pl.col("test_season")).height:
        raise EvaluationError("a prediction's season differs from its model's test season")
    open_ = df.filter(pl.col("y").is_null() | (pl.col("label_status") != "final"))
    if open_.height:
        raise EvaluationError(f"{open_.height:,} predictions have no final outcome in the store")
    return df.drop("test_season", "label_status").with_columns(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
    )


@dataclass
class EvalData:
    """What one label's evaluation reads: the store's predictions joined with the dataset's
    descriptive columns, the rostered players' universe (breakouts) and the model rows of the
    dataset (the base-rate Brier needs the training seasons' hit rate)."""

    label: str
    rows: pl.DataFrame
    universe: pl.DataFrame
    train_rows: pl.DataFrame
    versions: pl.DataFrame  # per method: its number of model versions and of predictions
    seasons: tuple[int, ...]


def prepare(predictions: pl.DataFrame, dataset: pl.DataFrame, label: str) -> EvalData:
    """Join the store's predictions with the dataset and check that they agree: every
    prediction must be a model row of the dataset (in the pool, final label, 2+ window games)
    with the same label."""
    missing = [c for c in DATASET_COLUMNS if c not in dataset.columns]
    if missing:
        raise EvaluationError(f"the dataset lacks columns {missing}; rebuild it")
    ds = dataset.select(DATASET_COLUMNS)
    info = ds.select(
        *KEYS, "name", "team", "owned_avg", "ecr_available",
        pl.col(label).alias("_y_ds"),
        (pl.col("in_pool") & pl.col("train_eligible") & (pl.col("label_status") == "final"))
        .alias("_model_row"),
    )  # fmt: skip
    rows = predictions.join(info, on=list(KEYS), how="left")
    unknown = rows.filter(pl.col("_model_row").is_null())
    if unknown.height:
        raise EvaluationError(
            f"{unknown.height:,} stored predictions have no row in the dataset (e.g. "
            f"{unknown.row(0, named=True)['gsis_id']} {unknown.row(0, named=True)['season']}); "
            "the store and the dataset come from different builds: re-run `twm radar backtest`"
        )
    stale = rows.filter(~pl.col("_model_row") | (pl.col("_y_ds") != pl.col("y")))
    if stale.height:
        raise EvaluationError(
            f"{stale.height:,} stored predictions disagree with the dataset (label or pool "
            "membership changed); re-run `twm radar backtest`"
        )
    seasons = tuple(sorted(int(s) for s in rows.get_column("season").unique().to_list()))
    universe = ds.filter(
        pl.col("season").is_in(list(seasons)) & (pl.col("label_status") == "final")
    ).sort(list(KEYS))
    train = model_rows(ds).select("season", pl.col(label).alias("y"))
    versions = (
        predictions.group_by("model")
        .agg(pl.col("model_version").n_unique().alias("versions"), pl.len().alias("rows"))
        .sort("model")
    )
    return EvalData(label, rows.drop("_y_ds", "_model_row"), universe, train, versions, seasons)


def load(store: Path | str, dataset: pl.DataFrame, label: str) -> EvalData:
    return prepare(load_predictions(store, label), dataset, label)


# --------------------------------------------------------------------------------------
# Ranked lists
# --------------------------------------------------------------------------------------


def _is_rostered() -> pl.Expr:
    return (pl.col("owned_avg") >= rostered_threshold()).fill_null(False)


def ranked_subset(rows: pl.DataFrame, subset: str) -> pl.DataFrame:
    """The rows of ``subset`` ranked again within every (method, as-of, position) list, with
    the backtest's rule (score, then the uncalibrated score, then the player id; no score
    last). For ``all`` this reproduces the stored ranks exactly (checked); ``without_rostered``
    removes the rostered rows first, so the players below them move up."""
    if subset not in SUBSETS:
        raise ValueError(f"unknown subset {subset!r}; subsets: {SUBSETS}")
    base = rows if subset == "all" else rows.filter(~_is_rostered())
    out = add_rank(
        base.rename({"rank": "stored_rank"}),
        group=LIST_GROUP,
        by=[(SCORE, True), (RAW_SCORE, True)],
        id_col=ID,
    )
    if subset == "all":
        bad = out.filter(pl.col("rank") != pl.col("stored_rank")).height
        if bad:
            raise EvaluationError(f"{bad:,} stored ranks do not follow the ranking rule")
    return out.drop("stored_rank")


# --------------------------------------------------------------------------------------
# Results table (the CSV) and the evaluation of one label
# --------------------------------------------------------------------------------------


def _res(
    label: str, subset: str, model: str, scope: str, seasons: str, key: object, metric: str,
    value: float | None, *, lo: float | None = None, hi: float | None = None,
    n_groups: int | None = None, n_rows: int | None = None, n_pos: int | None = None,
    n_top_hits: int | None = None, n_top: int | None = None,
) -> dict[str, object]:  # fmt: skip
    return {
        "label": label, "subset": subset, "model": model, "scope": scope, "seasons": seasons,
        "key": "" if key is None else str(key), "metric": metric, "value": value, "lo": lo,
        "hi": hi, "n_groups": n_groups, "n_rows": n_rows, "n_pos": n_pos,
        "n_top_hits": n_top_hits, "n_top": n_top,
    }  # fmt: skip


def _rng(seasons: Sequence[int]) -> str:
    lo, hi = min(seasons), max(seasons)
    return f"{lo}-{hi}" if lo != hi else str(lo)


@dataclass(frozen=True)
class Pool:
    """Pooled precision@10 of a set of lists with its interval and counts."""

    iv: Interval
    n_groups: int
    n_rows: int
    n_pos: int
    hits_top: int
    n_top: int

    @property
    def value(self) -> float | None:
        return self.iv.value

    @property
    def base_rate(self) -> float | None:
        return self.n_pos / self.n_rows if self.n_rows else None


@dataclass
class LabelEvaluation:
    label: str
    seasons: tuple[int, ...]
    methods: tuple[str, ...]  # present, in METHOD_ORDER
    winner: str | None
    groups: dict[str, pl.DataFrame]  # subset -> per (model, as-of, position) precision table
    ranked: dict[str, pl.DataFrame]  # subset -> ranked rows
    covered: pl.DataFrame  # (season, week, position) lists with an experts' page
    breakouts: dict[str, pl.DataFrame]  # subset -> events with size (one row per breakout)
    caught: dict[str, pl.DataFrame]  # subset -> per method caught frame (model column)
    calibration: dict[tuple[str, str], pl.DataFrame]  # (model, 'equal'|'fixed') -> bins
    results: list[dict[str, object]] = field(default_factory=list)
    versions: pl.DataFrame | None = None
    n_boot: int = N_BOOT
    seed: int = BOOTSTRAP_SEED

    @property
    def full_methods(self) -> tuple[str, ...]:
        """Methods graded on every list (the experts' lists exist only from 2020)."""
        return tuple(m for m in self.methods if not (BASELINES.get(m) and BASELINES[m].needs))

    def lists(
        self,
        subset: str,
        model: str,
        seasons: Sequence[int] | None = None,
        positions: Sequence[str] | None = None,
        only: pl.DataFrame | None = None,
    ) -> pl.DataFrame:
        g = self.groups[subset].filter(pl.col("model") == model)
        if seasons is not None:
            g = g.filter(pl.col("season").is_in(list(seasons)))
        if positions is not None:
            g = g.filter(pl.col("position").is_in(list(positions)))
        if only is not None:
            g = g.join(only, on=list(GROUP), how="semi")
        return g

    def pool(
        self,
        subset: str,
        model: str,
        seasons: Sequence[int] | None = None,
        positions: Sequence[str] | None = None,
        only: pl.DataFrame | None = None,
    ) -> Pool:
        g = self.lists(subset, model, seasons, positions, only)
        pp = pooled_precision(g)
        iv = block_bootstrap(g, n_boot=self.n_boot, seed=self.seed)
        n_top = int(g.get_column("n_top").sum()) if g.height else 0
        return Pool(iv, pp.n_groups, pp.n_rows, pp.n_pos, pp.hits_top, n_top)

    def diff(
        self,
        subset: str,
        a: str,
        b: str,
        seasons: Sequence[int] | None = None,
        positions: Sequence[str] | None = None,
        only: pl.DataFrame | None = None,
    ) -> Interval:
        ga = self.lists(subset, a, seasons, positions, only)
        gb = self.lists(subset, b, seasons, positions, only)
        return paired_block_bootstrap(ga, gb, keys=list(GROUP), n_boot=self.n_boot, seed=self.seed)

    def get(self, **match: object) -> list[dict[str, object]]:
        """Result rows whose fields equal ``match`` (keys compared as text)."""
        want = {k: (str(v) if k == "key" else v) for k, v in match.items()}
        return [r for r in self.results if all(r[k] == v for k, v in want.items())]

    def one(self, **match: object) -> dict[str, object]:
        rows = self.get(**match)
        if len(rows) != 1:
            raise KeyError(f"{len(rows)} result rows match {match}")
        return rows[0]


def _methods_present(rows: pl.DataFrame) -> tuple[str, ...]:
    have = set(rows.get_column("model").unique().to_list())
    return tuple(m for m in METHOD_ORDER if m in have)


def pick_winner(ev: LabelEvaluation) -> str | None:
    """The C4 rule: the model with the higher pooled precision@10 over all test seasons (all
    pool rows); a tie goes to the logistic regression (the simpler model)."""
    models = [m for m in MODELS if m in ev.methods]
    if not models:
        return None
    score = {m: ev.pool("all", m, ev.seasons).value or 0.0 for m in models}
    return max(models, key=lambda m: (score[m], m == "logit"))


def evaluate_label(
    data: EvalData, *, n_boot: int = N_BOOT, seed: int = BOOTSTRAP_SEED
) -> LabelEvaluation:
    """Every metric of one label (see the module docstring); results in ``.results``."""
    label, seasons = data.label, data.seasons
    methods = _methods_present(data.rows)
    ranked = {s: ranked_subset(data.rows, s) for s in SUBSETS}
    groups = {s: precision_at_k(ranked[s], group=LIST_GROUP, label="y", k=K) for s in SUBSETS}
    covered = (
        groups["all"].filter(pl.col("model") == EXPERTS).select(GROUP).unique().sort(list(GROUP))
    )
    ev = LabelEvaluation(
        label, seasons, methods, None, groups, ranked, covered, {}, {}, {},
        versions=data.versions, n_boot=n_boot, seed=seed,
    )  # fmt: skip
    ev.winner = pick_winner(ev)
    res = ev.results
    ranges = [_years(r) for r in season_ranges(seasons)]
    full = ranges[0]
    fm = ev.full_methods
    fitted = [m for m in MODELS if m in methods]

    for subset in SUBSETS:
        # pooled precision@10 with its interval, per season range
        for yrs in ranges:
            for m in fm:
                p = ev.pool(subset, m, yrs)
                res.append(_pool_row(label, subset, m, "pooled", _rng(yrs), "", p))
            # paired differences: each model minus each naive baseline, logit minus lgbm
            for a, b in _pairs(methods):
                iv = ev.diff(subset, a, b, yrs)
                res += _diff_rows(label, subset, a, "diff", _rng(yrs), b, iv)
                won = _seasons_won(ev, subset, a, b, yrs)
                res.append(
                    _res(label, subset, a, "diff", _rng(yrs), b, "seasons_won", won,
                         n_groups=len(yrs))
                )  # fmt: skip
        # per position (the full range), with intervals and the winner's gaps
        for pos in FANTASY_POSITIONS:
            for m in fm:
                p = ev.pool(subset, m, full, [pos])
                res.append(_pool_row(label, subset, m, "position", _rng(full), pos, p))
            if ev.winner is not None:
                for b in [x for x in NAIVE_BASELINES if x in methods]:
                    iv = ev.diff(subset, ev.winner, b, full, [pos])
                    res += _diff_rows(label, subset, ev.winner, "position_diff", _rng(full),
                                      f"{pos} vs {b}", iv)  # fmt: skip
        # per season (the experts: on their own lists, where a page existed) and the spread
        for m in methods:
            vals = []
            for s in seasons:
                g = ev.lists(subset, m, [s])
                if g.height == 0 and m not in fm:
                    continue
                pp = pooled_precision(g)
                vals.append(pp.value)
                res.append(
                    _res(label, subset, m, "season", str(s), s, f"p_at_{K}", pp.value,
                         n_groups=pp.n_groups, n_rows=pp.n_rows, n_pos=pp.n_pos,
                         n_top_hits=pp.hits_top, n_top=int(g.get_column("n_top").sum()))
                )  # fmt: skip
            v = np.array([x for x in vals if x is not None], dtype=np.float64)
            if v.size and m in fm:
                for name, x in (("min", v.min()), ("median", np.median(v)), ("max", v.max())):
                    res.append(
                        _res(label, subset, m, "spread", _rng(full), name, f"season_{name}",
                             float(x), n_groups=int(v.size))
                    )  # fmt: skip
        # the experts, on the lists they covered (2020 on)
        if EXPERTS in methods and ev.covered.height:
            cov_years = sorted(set(ev.covered.get_column("season").to_list()))
            for m in methods:
                p = ev.pool(subset, m, cov_years, only=ev.covered)
                res.append(_pool_row(label, subset, m, "experts", _rng(cov_years), "", p))
            for a in fitted:
                iv = ev.diff(subset, a, EXPERTS, cov_years, only=ev.covered)
                res += _diff_rows(label, subset, a, "experts_diff", _rng(cov_years), EXPERTS, iv)
                won = _seasons_won(ev, subset, a, EXPERTS, cov_years, only=ev.covered)
                res.append(
                    _res(label, subset, a, "experts_diff", _rng(cov_years), EXPERTS,
                         "seasons_won", won, n_groups=len(cov_years))
                )  # fmt: skip
        # hit rate by rank bucket (full-coverage methods, the full range)
        for m in fm:
            r = ranked[subset].filter((pl.col("model") == m) & pl.col("season").is_in(full))
            for name, n, hits, rate in bucket_rates(r, label="y", buckets=DEFAULT_BUCKETS):
                res.append(
                    _res(label, subset, m, "bucket", _rng(full), name, "hit_rate", rate,
                         n_rows=n, n_pos=hits)
                )  # fmt: skip
        # base rates per season (every method ranks the same rows)
        ref = fm[0] if fm else None
        if ref is not None:
            for s in seasons:
                pp = pooled_precision(ev.lists(subset, ref, [s]))
                res.append(
                    _res(label, subset, "base_rate", "season", str(s), s, "base_rate",
                         pp.base_rate, n_groups=pp.n_groups, n_rows=pp.n_rows, n_pos=pp.n_pos)
                )  # fmt: skip
            for yrs in ranges:
                pp = pooled_precision(ev.lists(subset, ref, yrs))
                res.append(
                    _res(label, subset, "base_rate", "pooled", _rng(yrs), "", "base_rate",
                         pp.base_rate, n_groups=pp.n_groups, n_rows=pp.n_rows, n_pos=pp.n_pos)
                )  # fmt: skip
        # breakouts caught
        _breakouts(ev, data, subset)

    # probabilities (models only, all pool rows): PR-AUC, Brier, calibration
    for m in fitted:
        r = ranked["all"].filter(pl.col("model") == m)
        for key, yrs in [*((str(s), [s]) for s in seasons), *(("pooled", y) for y in ranges)]:
            sub = r.filter(pl.col("season").is_in(yrs))
            res += _prob_rows(label, m, "prob", _rng(yrs), key, sub)
        for pos in FANTASY_POSITIONS:
            sub = r.filter(pl.col("season").is_in(full) & (pl.col("position") == pos))
            res += _prob_rows(label, m, "prob_position", _rng(full), pos, sub)
        sub = r.filter(pl.col("season").is_in(full))
        eq = calibration_bins(sub, prob=SCORE, label="y", id_cols=list(KEYS))
        fx = calibration_fixed_bins(sub, prob=SCORE, label="y")
        ev.calibration[(m, "equal")] = eq
        ev.calibration[(m, "fixed")] = fx
        for b in eq.iter_rows(named=True):
            for metric in ("mean_pred", "observed", "min_pred", "max_pred"):
                res.append(
                    _res(label, "all", m, "calibration_equal", _rng(full), b["bin"], metric,
                         b[metric], n_rows=int(b["n"]), n_pos=int(b["n_pos"]))
                )  # fmt: skip
        for b in fx.iter_rows(named=True):
            key = f"{b['lo']:.1f}-{b['hi']:.1f}"
            for metric in ("mean_pred", "observed"):
                res.append(
                    _res(label, "all", m, "calibration_fixed", _rng(full), key, metric,
                         b[metric], n_rows=int(b["n"]), n_pos=int(b["n_pos"]))
                )  # fmt: skip
    for key, yrs in [*((str(s), [s]) for s in seasons), *(("pooled", y) for y in ranges)]:
        b, n, pos = _base_rate_brier(data, ranked["all"], yrs)
        res.append(
            _res(label, "all", "base_rate", "prob", _rng(yrs), key, "brier", b, n_rows=n,
                 n_pos=pos)
        )  # fmt: skip
    return ev


def _years(r: tuple[int, int]) -> list[int]:
    return list(range(r[0], r[1] + 1))


def _pairs(methods: Sequence[str]) -> list[tuple[str, str]]:
    out = [(a, b) for a in MODELS for b in NAIVE_BASELINES if a in methods and b in methods]
    if "logit" in methods and "lgbm" in methods:
        out.append(("logit", "lgbm"))
    return out


def _pool_row(
    label: str, subset: str, model: str, scope: str, seasons: str, key: object, p: Pool
) -> dict[str, object]:
    return _res(
        label, subset, model, scope, seasons, key, f"p_at_{K}", p.value, lo=p.iv.lo,
        hi=p.iv.hi, n_groups=p.n_groups, n_rows=p.n_rows, n_pos=p.n_pos,
        n_top_hits=p.hits_top, n_top=p.n_top,
    )  # fmt: skip


def _diff_rows(
    label: str, subset: str, model: str, scope: str, seasons: str, key: object, iv: Interval
) -> list[dict[str, object]]:
    return [
        _res(label, subset, model, scope, seasons, key, f"p_at_{K}_diff", iv.value, lo=iv.lo,
             hi=iv.hi, n_groups=iv.n_groups),
        _res(label, subset, model, scope, seasons, key, "share_resamples_above_0",
             iv.share_above_zero, n_groups=iv.n_blocks),
    ]  # fmt: skip


def _seasons_won(
    ev: LabelEvaluation,
    subset: str,
    a: str,
    b: str,
    seasons: Sequence[int],
    only: pl.DataFrame | None = None,
) -> int:
    won = 0
    for s in seasons:
        va = pooled_precision(ev.lists(subset, a, [s], only=only)).value
        vb = pooled_precision(ev.lists(subset, b, [s], only=only)).value
        won += int(va is not None and vb is not None and va > vb)
    return won


def _prob_rows(
    label: str, model: str, scope: str, seasons: str, key: object, sub: pl.DataFrame
) -> list[dict[str, object]]:
    y = sub.get_column("y").cast(pl.Int64).to_numpy()
    sc = sub.get_column(SCORE).to_numpy()
    n, pos = int(y.size), int(y.sum())
    return [
        _res(label, "all", model, scope, seasons, key, "pr_auc", pr_auc(y, sc), n_rows=n,
             n_pos=pos),
        _res(label, "all", model, scope, seasons, key, "brier", brier(y, sc), n_rows=n,
             n_pos=pos),
    ]  # fmt: skip


def _base_rate_brier(
    data: EvalData, ranked: pl.DataFrame, seasons: Sequence[int]
) -> tuple[float | None, int, int]:
    """The Brier score of always predicting, in each test season, the hit rate of the seasons
    before it (what the models must beat to be informative); the C4 definition."""
    ref = next((m for m in MODELS if m in set(ranked.get_column("model").to_list())), None)
    if ref is None:
        return None, 0, 0
    test = ranked.filter(pl.col("model") == ref)
    num, den, pos = 0.0, 0, 0
    for s in seasons:
        train = data.train_rows.filter(pl.col("season") < s).get_column("y").cast(pl.Float64)
        yt = test.filter(pl.col("season") == s).get_column("y").cast(pl.Float64)
        if train.len() == 0 or yt.len() == 0:
            continue
        p = float(train.mean())
        num += float(((yt - p) ** 2).sum())
        den += yt.len()
        pos += int(yt.sum())
    return (num / den if den else None), den, pos


# --------------------------------------------------------------------------------------
# Breakouts caught
# --------------------------------------------------------------------------------------


def breakout_events(ranked: pl.DataFrame, universe: pl.DataFrame) -> pl.DataFrame:
    """One row per breakout (season, gsis_id) of the ranked rows (one subset).

    A player-season is a breakout when the player was in the Radar's lists (a ranked pool
    row) at some as-of and had ``y_sustained`` (two starter finishes in one window) at that
    as-of or a later one of the season, in the pool or not by then (``universe``: every
    rostered player with a final label). ``breakout_week`` = the first such as-of (the Tuesday
    after that week). Adds his row at that as-of (name, team, position, the window's weeks,
    weekly ranks and points, whether it was a pool row), ``first_pool_week`` and the size of
    the breakout: starter finishes and points in every week after ``breakout_week``
    (``rest_starter_weeks``, ``rest_points``; from the windows of his later rows)."""
    pool_first = (
        ranked.select(*KEYS).unique().group_by("season", ID)
        .agg(pl.col("week").min().alias("first_pool_week"))
    )  # fmt: skip
    uni = universe.join(pool_first, on=["season", ID], how="inner").filter(
        pl.col("week") >= pl.col("first_pool_week")
    )
    ev = first_events(uni, entity=["season", ID], order="week", event=BREAKOUT_LABEL).rename(
        {"event_order": "breakout_week"}
    )
    at = universe.select(
        "season", "week", ID, "name", "team", "position", "in_pool", "owned_avg",
        "preseason_pos_rank", "window_weeks", "window_ranks", "window_points",
    )  # fmt: skip
    ev = ev.join(pool_first, on=["season", ID], how="left").join(
        at.rename({"week": "breakout_week"}), on=["season", "breakout_week", ID], how="left"
    )
    thresholds = league().starter_thresholds()
    later = (
        universe.join(ev.select("season", ID, "breakout_week"), on=["season", ID])
        .filter(pl.col("week") >= pl.col("breakout_week"))
        .select("season", ID, "breakout_week", "position", "window_weeks", "window_ranks",
                "window_points")
        .explode("window_weeks", "window_ranks", "window_points", empty_as_null=True)
        .filter(pl.col("window_weeks") > pl.col("breakout_week"))
        .unique(subset=["season", ID, "window_weeks"], keep="first", maintain_order=True)
    )  # fmt: skip
    thr = pl.col("position").replace_strict(thresholds, default=None, return_dtype=pl.Int32)
    size = later.group_by("season", ID).agg(
        (pl.col("window_ranks") <= thr).fill_null(False).sum().cast(pl.Int32)
        .alias("rest_starter_weeks"),
        pl.col("window_points").fill_null(0.0).sum().round(2).alias("rest_points"),
    )  # fmt: skip
    return (
        ev.join(size, on=["season", ID], how="left")
        .with_columns(
            pl.col("rest_starter_weeks").fill_null(0), pl.col("rest_points").fill_null(0.0)
        )
        .sort("season", ID)
    )


def _caught_frame(
    methods: Sequence[str], ranked: pl.DataFrame, events: pl.DataFrame
) -> pl.DataFrame:
    """caught_before for every method (``model`` column), with the breakout's position."""
    frames = []
    ev_m = events.select("season", ID, pl.col("breakout_week").alias("event_order"))
    for m in methods:
        r = ranked.filter(pl.col("model") == m).rename({"week": "order"})
        c = caught_before(ev_m, r, entity=["season", ID], order="order", k=K,
                          recent=RECENT_ASOFS)  # fmt: skip
        frames.append(c.with_columns(pl.lit(m).alias("model")))
    return pl.concat(frames, how="vertical").join(
        events.select("season", ID, "position"), on=["season", ID], how="left"
    )


def _flagged(ranked: pl.DataFrame, model: str, seasons: Sequence[int]) -> int:
    """Distinct player-seasons a method put in its top 10 at least once."""
    top = ranked.filter(
        (pl.col("model") == model) & pl.col("season").is_in(list(seasons)) & (pl.col("rank") <= K)
    )
    return top.select("season", ID).unique().height


def _breakouts(ev: LabelEvaluation, data: EvalData, subset: str) -> None:
    """Breakouts caught per method: over every test season (the methods graded on every
    list), per position and per season; and, for the experts' comparison, only on the lists
    the experts covered (their ranks exist there only: every method may use only those
    lists, and only breakouts whose breakout list was covered count)."""
    ranked = ev.ranked[subset]
    fm = ev.full_methods
    events = breakout_events(ranked.filter(pl.col("model").is_in(list(fm))), data.universe)
    ev.breakouts[subset] = events
    caught = _caught_frame(ev.methods, ranked, events)
    ev.caught[subset] = caught
    label, full = ev.label, list(ev.seasons)
    rng = _rng(full)
    for m in fm:
        c = caught.filter(pl.col("model") == m)
        ev.results += _caught_rows(ev, label, subset, m, rng, "", c)
        ev.results.append(
            _res(label, subset, m, "breakouts", rng, "", "players_flagged",
                 float(_flagged(ranked, m, full)), n_rows=_flagged(ranked, m, full))
        )  # fmt: skip
        for pos in FANTASY_POSITIONS:
            cp = c.filter(pl.col("position") == pos)
            ev.results += _caught_rows(ev, label, subset, m, rng, pos, cp,
                                       scope="breakouts_position")  # fmt: skip
        for s in full:
            cs = c.filter(pl.col("season") == s)
            ev.results += _caught_rows(ev, label, subset, m, str(s), s, cs,
                                       scope="breakouts_season", interval=False)  # fmt: skip
    if ev.winner is not None:  # paired: the winner minus each naive baseline, same breakouts
        for b in [x for x in NAIVE_BASELINES if x in ev.methods]:
            for col in ("caught", "caught_recent"):
                iv = _caught_diff(ev, caught, ev.winner, b, col)
                ev.results.append(
                    _res(label, subset, ev.winner, "breakouts_diff", rng, b,
                         f"{col}_share_diff", iv.value, lo=iv.lo, hi=iv.hi,
                         n_groups=iv.n_groups)
                )  # fmt: skip
    n_pool = int(events.get_column("in_pool").fill_null(False).sum())
    ev.results.append(
        _res(label, subset, "breakouts", "breakouts", rng, "", "share_pool_row_at_breakout",
             n_pool / events.height if events.height else None, n_rows=events.height,
             n_pos=n_pool)
    )  # fmt: skip
    # the experts' comparison: covered lists only
    if EXPERTS in ev.methods and ev.covered.height:
        cov_years = sorted(set(ev.covered.get_column("season").to_list()))
        on_cov = ranked.join(ev.covered, on=list(GROUP), how="semi")
        ev_cov = events.join(
            ev.covered.rename({"week": "breakout_week"}),
            on=["season", "breakout_week", "position"],
            how="semi",
        )
        c_cov = _caught_frame(ev.methods, on_cov, ev_cov)
        for m in ev.methods:
            c = c_cov.filter(pl.col("model") == m)
            ev.results += _caught_rows(ev, label, subset, m, _rng(cov_years), "", c,
                                       scope="breakouts_experts")  # fmt: skip
            ev.results.append(
                _res(label, subset, m, "breakouts_experts", _rng(cov_years), "",
                     "players_flagged", float(_flagged(on_cov, m, cov_years)),
                     n_rows=_flagged(on_cov, m, cov_years))
            )  # fmt: skip
        for a in [x for x in MODELS if x in ev.methods]:
            iv = _caught_diff(ev, c_cov, a, EXPERTS)
            ev.results.append(
                _res(label, subset, a, "breakouts_experts_diff", _rng(cov_years), EXPERTS,
                     "caught_share_diff", iv.value, lo=iv.lo, hi=iv.hi, n_groups=iv.n_groups)
            )  # fmt: skip


def _caught_diff(
    ev: LabelEvaluation, caught: pl.DataFrame, a: str, b: str, col: str = "caught"
) -> Interval:
    ca = caught.filter(pl.col("model") == a).with_columns(pl.col(col).cast(pl.Float64))
    cb = caught.filter(pl.col("model") == b).with_columns(pl.col(col).cast(pl.Float64))
    return paired_block_bootstrap(
        ca, cb, keys=["season", ID], value=col, n_boot=ev.n_boot, seed=ev.seed
    )


def _caught_rows(
    ev: LabelEvaluation, label: str, subset: str, model: str, seasons: str, key: object,
    c: pl.DataFrame, *, scope: str = "breakouts", interval: bool = True,
) -> list[dict[str, object]]:  # fmt: skip
    n, hit = c.height, int(c.get_column("caught").sum()) if c.height else 0
    recent = int(c.get_column("caught_recent").sum()) if c.height else 0
    lo = hi = None
    if interval and n:
        iv = block_bootstrap(
            c.with_columns(pl.col("caught").cast(pl.Float64)), value="caught",
            n_boot=ev.n_boot, seed=ev.seed,
        )  # fmt: skip
        lo, hi = iv.lo, iv.hi
    return [
        _res(label, subset, model, scope, seasons, key, "caught_share", hit / n if n else None,
             lo=lo, hi=hi, n_rows=n, n_pos=hit),
        _res(label, subset, model, scope, seasons, key, "caught_recent_share",
             recent / n if n else None, n_rows=n, n_pos=recent),
    ]  # fmt: skip


def biggest_breakouts(
    ev: LabelEvaluation, *, caught: bool, subset: str = "all", n: int = N_LIST
) -> pl.DataFrame:
    """The ``n`` biggest breakouts the winner caught (or missed), biggest first: most starter
    weeks after the breakout, then most points; with the winner's and last week's points'
    best rank at or before the breakout."""
    if ev.winner is None:
        return pl.DataFrame()
    events = ev.breakouts[subset]
    c = ev.caught[subset]
    win = c.filter(pl.col("model") == ev.winner).select(
        "season", ID, "caught", "caught_recent", "best_rank", "best_order", "first_top_k"
    )
    out = events.join(win, on=["season", ID], how="left")
    if "baseline_last_points" in ev.methods:
        lp = c.filter(pl.col("model") == "baseline_last_points").select(
            "season", ID, pl.col("best_rank").alias("last_points_best_rank"),
            pl.col("caught").alias("last_points_caught"),
        )  # fmt: skip
        out = out.join(lp, on=["season", ID], how="left")
    return (
        out.filter(pl.col("caught") == caught)
        .sort(["rest_starter_weeks", "rest_points", "season", ID],
              descending=[True, True, False, False])
        .head(n)
    )  # fmt: skip


# --------------------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class EvaluationReport:
    markdown: str
    csv_rows: list[dict[str, object]]
    summary: list[str]


def _p(x: object, digits: int = 1) -> str:
    return "-" if x is None else f"{100 * float(x):.{digits}f}%"


def _pts(x: object, digits: int = 1) -> str:
    return "-" if x is None else f"{100 * float(x):+.{digits}f}"


def _ci(r: dict[str, object]) -> str:
    """'47.8% (45.1-50.3)'."""
    if r["value"] is None:
        return "-"
    if r["lo"] is None:
        return _p(r["value"])
    return f"{_p(r['value'])} ({100 * r['lo']:.1f}-{100 * r['hi']:.1f})"


def _dci(r: dict[str, object]) -> str:
    """'+7.4 pts (+5.8 to +9.1)'."""
    if r["value"] is None:
        return "-"
    return f"{_pts(r['value'])} ({_pts(r['lo'])} to {_pts(r['hi'])})"


def _n(x: object) -> str:
    return "-" if x is None or x == "" else f"{int(x):,}"


def _table(header: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


def _name(m: str) -> str:
    return TITLES.get(m, m)


def _label_words(label: str) -> str:
    return (
        "at least one starter week in his next 3 games"
        if label == "y_hit"
        else "at least two starter weeks in his next 3 games"
    )


def _headline(ev: LabelEvaluation) -> list[str]:
    full = _rng(ev.seasons)
    lines = [f"## Label `{ev.label}` ({_label_words(ev.label)})", ""]
    base = ev.get(subset="all", model="base_rate", scope="pooled", seasons=full)
    if base:
        b = base[0]
        lines += [
            f"{b['n_groups']:,} weekly lists (as-of x position) in {full}, {b['n_rows']:,} pool "
            f"rows, {b['n_pos']:,} hits: base rate {_p(b['value'])}, the precision a random "
            "pick would get.",
            "",
        ]
    if ev.winner:
        lines += [
            f"Winner (C4 rule: higher pooled precision@{K}, ties to the simpler model): "
            f"**{_name(ev.winner)}** (`{ev.winner}`).",
            "",
        ]
    lines += [
        f"### Precision@{K}, pooled, with 95% intervals",
        "",
        f"The share of each list's top {K} who hit, averaged over the lists, with the 95% "
        f"season-block bootstrap interval in brackets ({ev.n_boot:,} resamples of the test "
        "seasons). `picks` = hits among the top-10 picks / all top-10 picks. `without "
        "rostered`: pool players owned in at least "
        f"{rostered_threshold():g}% of real leagues (FantasyPros, 2020 on) removed and the lists "
        "re-ranked. The experts' ranks exist only from 2020: see their section below.",
        "",
    ]
    yrs_list = sorted(
        {str(r["seasons"]) for r in ev.get(scope="pooled", subset="all")},
        key=lambda s: (int(s.split("-")[0]), s),
    )
    header = ["method"]
    for yrs in yrs_list:
        header += [f"{yrs} {SUBSET_TITLES[s]}" for s in SUBSETS]
    header += ["lists", f"picks ({full})"]
    rows = []
    for m in ev.full_methods:
        row: list[object] = [f"{_name(m)}"]
        for yrs in yrs_list:
            for s in SUBSETS:
                row.append(_ci(ev.one(subset=s, model=m, scope="pooled", seasons=yrs,
                                      metric=f"p_at_{K}")))  # fmt: skip
        r = ev.one(subset="all", model=m, scope="pooled", seasons=full, metric=f"p_at_{K}")
        row += [_n(r["n_groups"]), f"{_n(r['n_top_hits'])}/{_n(r['n_top'])}"]
        rows.append(row)
    lines += _table(header, rows)
    return lines


def _diff_section(ev: LabelEvaluation) -> list[str]:
    yrs_list = sorted({str(r["seasons"]) for r in ev.get(scope="diff", subset="all")})
    if not yrs_list:
        return []
    full = _rng(ev.seasons)
    lines = [
        "",
        "### Paired differences (percentage points, 95% intervals)",
        "",
        "Model minus method, on the same lists: each resample draws the same seasons for both, "
        "so a hard season counts against both at once. `ahead in` = the share of the "
        f"{ev.n_boot:,} resamples in which the model was ahead; `seasons won` = seasons in which "
        "its precision@10 was higher.",
        "",
    ]
    header = ["comparison"]
    for yrs in yrs_list:
        header += [f"{yrs} {SUBSET_TITLES[s]}" for s in SUBSETS]
    header += [f"ahead in ({full})", f"seasons won ({full})"]
    rows = []
    for a, b in _pairs(ev.methods):
        row: list[object] = [f"{_name(a)} - {_name(b)}"]
        for yrs in yrs_list:
            for s in SUBSETS:
                row.append(_dci(ev.one(subset=s, model=a, scope="diff", seasons=yrs, key=b,
                                       metric=f"p_at_{K}_diff")))  # fmt: skip
        ahead = ev.one(subset="all", model=a, scope="diff", seasons=full, key=b,
                       metric="share_resamples_above_0")  # fmt: skip
        won = ev.one(subset="all", model=a, scope="diff", seasons=full, key=b,
                     metric="seasons_won")  # fmt: skip
        row += [_p(ahead["value"], 0), f"{int(won['value'])} of {won['n_groups']}"]
        rows.append(row)
    lines += _table(header, rows)
    return lines


def _season_section(ev: LabelEvaluation) -> list[str]:
    fm = ev.full_methods
    lines = [
        "",
        "### Per season (all pool rows)",
        "",
        "One row per test season: its lists, hits and base rate, then each method's "
        f"precision@{K}. The last rows give the spread over the seasons.",
        "",
    ]
    rows = []
    for s in ev.seasons:
        b = ev.one(subset="all", model="base_rate", scope="season", key=s, metric="base_rate")
        row: list[object] = [s, _n(b["n_groups"]), _n(b["n_pos"]), _p(b["value"])]
        for m in fm:
            row.append(_p(ev.one(subset="all", model=m, scope="season", key=s,
                                 metric=f"p_at_{K}")["value"]))  # fmt: skip
        rows.append(row)
    for name in ("min", "median", "max"):
        row = [f"*season {name}*", "", "", ""]
        for m in fm:
            row.append(_p(ev.one(subset="all", model=m, scope="spread", key=name)["value"]))
        rows.append(row)
    lines += _table(["season", "lists", "hits", "base rate", *(_name(m) for m in fm)], rows)
    return lines


def _position_section(ev: LabelEvaluation) -> list[str]:
    full = _rng(ev.seasons)
    fm = ev.full_methods
    lines = [
        "",
        f"### Per position ({full}, 95% intervals)",
        "",
    ]
    for s in SUBSETS:
        rows = []
        for pos in FANTASY_POSITIONS:
            ref = ev.one(subset=s, model=fm[0], scope="position", key=pos, metric=f"p_at_{K}")
            rate = ref["n_pos"] / ref["n_rows"] if ref["n_rows"] else None
            row: list[object] = [pos, _n(ref["n_groups"]), _n(ref["n_pos"]), _p(rate)]
            for m in fm:
                row.append(_ci(ev.one(subset=s, model=m, scope="position", key=pos,
                                      metric=f"p_at_{K}")))  # fmt: skip
            if ev.winner and "baseline_last_points" in ev.methods:
                row.append(_dci(ev.one(subset=s, model=ev.winner, scope="position_diff",
                                       key=f"{pos} vs baseline_last_points",
                                       metric=f"p_at_{K}_diff")))  # fmt: skip
            rows.append(row)
        header = ["position", "lists", "hits", "base rate", *(_name(m) for m in fm)]
        if ev.winner and "baseline_last_points" in ev.methods:
            header.append(f"{_name(ev.winner)} - last week's points")
        lines += [f"{SUBSET_TITLES[s].capitalize()}:", "", *_table(header, rows), ""]
    return lines[:-1]


def _bucket_section(ev: LabelEvaluation) -> list[str]:
    full = _rng(ev.seasons)
    lines = [
        "",
        f"### Hit rate by rank ({full})",
        "",
        "The share of the players ranked 1-5, 6-10 and 11-25 in their list who hit (hits/players "
        "in brackets). A good ranking puts its best calls first, so the rate should fall down "
        "the list. The experts are left out (their lists cover 2020 on only).",
        "",
    ]
    names = [f"{lo}-{hi}" for lo, hi in DEFAULT_BUCKETS]
    for s in SUBSETS:
        rows = []
        for m in ev.full_methods:
            row: list[object] = [_name(m)]
            for b in names:
                r = ev.one(subset=s, model=m, scope="bucket", key=b)
                row.append(f"{_p(r['value'])} ({_n(r['n_pos'])}/{_n(r['n_rows'])})")
            rows.append(row)
        lines += [f"{SUBSET_TITLES[s].capitalize()}:", ""]
        lines += _table(["method", *(f"ranks {b}" for b in names)], rows)
        lines.append("")
    return lines[:-1]


def _prob_section(ev: LabelEvaluation) -> list[str]:
    fitted = [m for m in MODELS if m in ev.methods]
    if not fitted:
        return []
    full = _rng(ev.seasons)
    lines = [
        "",
        "### PR-AUC and Brier (the models' probabilities, all pool rows)",
        "",
        "PR-AUC (average precision): how well the probabilities put hits above misses across "
        "all rows (higher is better; a random ranking scores the base rate). Brier: the mean "
        "squared error of the probability (lower is better); `base-rate Brier` always predicts "
        "the hit rate of the seasons before (what an uninformative forecast scores).",
        "",
    ]
    header = ["seasons", "hits / rows", "base-rate Brier"]
    for m in fitted:
        header += [f"{_name(m)} PR-AUC", f"{_name(m)} Brier"]
    rows = []
    keys = [*(str(s) for s in ev.seasons), "pooled"]
    for key in keys:
        seasons = key if key != "pooled" else full
        b = ev.one(model="base_rate", scope="prob", key=key, seasons=seasons)
        row: list[object] = [
            key if key != "pooled" else f"{full} pooled",
            f"{_n(b['n_pos'])} / {_n(b['n_rows'])}",
            "-" if b["value"] is None else f"{b['value']:.4f}",
        ]
        for m in fitted:
            for metric in ("pr_auc", "brier"):
                r = ev.one(model=m, scope="prob", key=key, seasons=seasons, metric=metric)
                row.append("-" if r["value"] is None else f"{r['value']:.4f}")
        rows.append(row)
    for pos in FANTASY_POSITIONS:
        ref = ev.one(model=fitted[0], scope="prob_position", key=pos, metric="pr_auc")
        row = [f"{pos} ({full})", f"{_n(ref['n_pos'])} / {_n(ref['n_rows'])}", ""]
        for m in fitted:
            for metric in ("pr_auc", "brier"):
                r = ev.one(model=m, scope="prob_position", key=pos, metric=metric)
                row.append("-" if r["value"] is None else f"{r['value']:.4f}")
        rows.append(row)
    lines += _table(header, rows)
    return lines


def _calibration_section(ev: LabelEvaluation) -> list[str]:
    m = ev.winner
    if m is None or (m, "equal") not in ev.calibration:
        return []
    full = _rng(ev.seasons)
    eq, fx = ev.calibration[(m, "equal")], ev.calibration[(m, "fixed")]
    lines = [
        "",
        f"### Calibration of the winner ({_name(m)}, {full})",
        "",
        "When the model says 30%, do about 30% hit? First table: rows sorted by predicted "
        "probability and cut into 10 bins of equal size. Second table: fixed-width bins (0-10%, "
        "10-20% ...), which show how few rows get a high probability. In a well-calibrated "
        "model `mean predicted` and `observed` match; a bin with few rows is noisy.",
        "",
    ]
    rows = [
        [b["bin"], _n(b["n"]), _n(b["n_pos"]), _p(b["mean_pred"]), _p(b["observed"]),
         f"{_p(b['min_pred'])}-{_p(b['max_pred'])}"]
        for b in eq.iter_rows(named=True)
    ]  # fmt: skip
    lines += _table(["equal-count bin", "rows", "hits", "mean predicted", "observed",
                     "predicted range"], rows)  # fmt: skip
    lines.append("")
    rows = [
        [f"{100 * b['lo']:.0f}-{100 * b['hi']:.0f}%", _n(b["n"]), _n(b["n_pos"]),
         _p(b["mean_pred"]), _p(b["observed"])]
        for b in fx.iter_rows(named=True)
    ]  # fmt: skip
    lines += _table(["fixed-width bin", "rows", "hits", "mean predicted", "observed"], rows)
    return lines


def _breakout_section(ev: LabelEvaluation) -> list[str]:
    full = _rng(ev.seasons)
    fm = ev.full_methods
    events = ev.breakouts["all"]
    share = ev.one(
        subset="all", model="breakouts", scope="breakouts", metric="share_pool_row_at_breakout"
    )
    lines = [
        "",
        "### Breakouts caught",
        "",
        "A **breakout** is a player-season in which a player the Radar ranked (a pool row) had "
        f"two starter weeks within one 3-game window (`{BREAKOUT_LABEL}`) at that Tuesday or a "
        "later one of the same season (by then he may have left the pool). His **breakout "
        "Tuesday** is the first such as-of. A method **caught** him if it ranked him in the top "
        f"{K} of his position at the breakout Tuesday or an earlier one of that season; a "
        "ranking made after the breakout never counts. `in the 3 Tuesdays before` asks for a "
        f"top-{K} rank at the breakout Tuesday or one of the two before it (a tighter test: a "
        "manager who added him early might have dropped him). `players flagged` = the "
        f"different players a method put in its top {K} at least once: 'at least once' rewards "
        "a method whose top 10 changes a lot from week to week, so read the two together. Each "
        f"method is judged with its own `{ev.label}` lists.",
        "",
        f"{events.height:,} breakouts in {full} ({share['n_pos']:,} of them, "
        f"{_p(share['value'])}, still in the pool at the breakout Tuesday).",
        "",
    ]
    for s in SUBSETS:
        header = ["method", "caught (95% interval)", "caught/breakouts",
                  "in the 3 Tuesdays before", "players flagged",
                  "caught per 100 flagged"]  # fmt: skip
        rows = []
        for m in fm:
            r = ev.one(subset=s, model=m, scope="breakouts", metric="caught_share")
            rr = ev.one(subset=s, model=m, scope="breakouts", metric="caught_recent_share")
            f = ev.one(subset=s, model=m, scope="breakouts", metric="players_flagged")
            per = 100 * int(r["n_pos"]) / int(f["n_rows"]) if f["n_rows"] else None
            rows.append([
                _name(m), _ci(r), f"{_n(r['n_pos'])}/{_n(r['n_rows'])}",
                f"{_p(rr['value'])} ({_n(rr['n_pos'])})", _n(f["n_rows"]),
                "-" if per is None else f"{per:.0f}",
            ])  # fmt: skip
        lines += [f"{SUBSET_TITLES[s].capitalize()} ({full}):", "", *_table(header, rows), ""]
    if ev.winner:
        rows = []
        for b in [x for x in NAIVE_BASELINES if x in ev.methods]:
            for s in SUBSETS:
                r = ev.one(subset=s, model=ev.winner, scope="breakouts_diff", key=b,
                           metric="caught_share_diff")  # fmt: skip
                rr = ev.one(subset=s, model=ev.winner, scope="breakouts_diff", key=b,
                            metric="caught_recent_share_diff")  # fmt: skip
                rows.append([f"{_name(ev.winner)} - {_name(b)}", SUBSET_TITLES[s], _dci(r),
                             _dci(rr)])  # fmt: skip
        lines += [f"Paired differences in the share caught ({full}, percentage points, 95% "
                  "intervals, same breakouts):", ""]  # fmt: skip
        lines += _table(["comparison", "pool rows", "caught", "in the 3 Tuesdays before"], rows)
        lines.append("")
    exp = ev.get(subset="all", model=EXPERTS, scope="breakouts_experts", metric="caught_share")
    if exp:
        yrs = exp[0]["seasons"]
        lines += [
            f"On the experts' lists ({yrs}): only the lists where their page existed, for every "
            "method, and only breakouts whose breakout Tuesday's list was one of them:",
            "",
        ]
        rows = []
        for m in ev.methods:
            row: list[object] = [_name(m)]
            for s in SUBSETS:
                r = ev.one(subset=s, model=m, scope="breakouts_experts", metric="caught_share")
                row.append(f"{_ci(r)} {_n(r['n_pos'])}/{_n(r['n_rows'])}")
            f = ev.one(subset="all", model=m, scope="breakouts_experts", metric="players_flagged")
            row.append(_n(f["n_rows"]))
            rows.append(row)
        for a in [x for x in MODELS if x in ev.methods]:
            row = [f"*{_name(a)} - experts*"]
            for s in SUBSETS:
                r = ev.one(subset=s, model=a, scope="breakouts_experts_diff", key=EXPERTS)
                row.append(_dci(r))
            rows.append([*row, ""])
        lines += _table(["method", *(SUBSET_TITLES[s] for s in SUBSETS), "players flagged"],
                        rows)  # fmt: skip
        lines.append("")
    rows = []
    for pos in FANTASY_POSITIONS:
        ref = ev.one(subset="all", model=fm[0], scope="breakouts_position", key=pos,
                     metric="caught_share")  # fmt: skip
        row = [pos, _n(ref["n_rows"])]
        for m in fm:
            r = ev.one(subset="all", model=m, scope="breakouts_position", key=pos,
                       metric="caught_share")  # fmt: skip
            row.append(f"{_p(r['value'])} ({_n(r['n_pos'])})")
        rows.append(row)
    lines += [f"Per position ({full}, all pool rows; caught in brackets):", ""]
    lines += _table(["position", "breakouts", *(_name(m) for m in fm)], rows)
    lines.append("")
    rows = []
    for season in ev.seasons:
        ref = ev.one(subset="all", model=fm[0], scope="breakouts_season", key=season,
                     metric="caught_share")  # fmt: skip
        row = [season, _n(ref["n_rows"])]
        for m in fm:
            r = ev.one(subset="all", model=m, scope="breakouts_season", key=season,
                       metric="caught_share")  # fmt: skip
            row.append(f"{_p(r['value'])} ({_n(r['n_pos'])})")
        rows.append(row)
    lines += ["Per season (all pool rows):", ""]
    lines += _table(["season", "breakouts", *(_name(m) for m in fm)], rows)
    if ev.winner:
        for caught in (True, False):
            big = biggest_breakouts(ev, caught=caught)
            title = "caught" if caught else "missed"
            lines += [
                "",
                f"The {big.height} biggest breakouts the {_name(ev.winner)} {title} (most "
                "starter weeks after the breakout Tuesday, then points). `next 3 weeks` = his "
                "weekly rank at his position in the window (`-` = no stat line); `preseason "
                "rank` = the pool's preseason list (last season's points per game rank before "
                "2020, FantasyPros from 2020; `-` = unranked); `owned` = FantasyPros "
                "rostership that week (2020 on); best rank = the best rank in the lists up to "
                "the breakout Tuesday (with the week).",
                "",
            ]
            lines += _table(
                ["season", "player", "pos", "team", "breakout Tuesday after week",
                 "next 3 weeks (rank)", "starter weeks after", "points after",
                 "preseason rank", "owned", f"{_name(ev.winner)} best rank",
                 "last week's points best rank"],
                [_big_row(r) for r in big.iter_rows(named=True)],
            )  # fmt: skip
    return lines


def _big_row(r: dict[str, object]) -> list[object]:
    ranks = r.get("window_ranks") or []
    weeks = r.get("window_weeks") or []
    wr = ", ".join(f"wk {w}: {'-' if k is None else k}" for w, k in zip(weeks, ranks, strict=False))
    best = "-" if r.get("best_rank") is None else f"{r['best_rank']} (wk {r['best_order']})"
    lp = r.get("last_points_best_rank")
    pre = r.get("preseason_pos_rank")
    owned = r.get("owned_avg")
    return [
        r["season"], r["name"], r["position"], r["team"], r["breakout_week"], wr,
        r["rest_starter_weeks"], f"{r['rest_points']:.1f}", "-" if pre is None else pre,
        "-" if owned is None else f"{owned:.0f}%", best, "-" if lp is None else lp,
    ]  # fmt: skip


def _experts_section(ev: LabelEvaluation) -> list[str]:
    rows_e = ev.get(subset="all", model=EXPERTS, scope="experts", metric=f"p_at_{K}")
    if not rows_e:
        return []
    yrs = str(rows_e[0]["seasons"])
    lines = [
        "",
        f"### The experts ({yrs}, on the lists where their page existed)",
        "",
        "FantasyPros' positional ranks visible at the as-of (C4: rest-of-season page, else "
        "weekly; saved on Fridays, so they had not seen the weekend's games yet). Every method "
        f"graded on the same {rows_e[0]['n_groups']:,} lists; intervals resample these "
        f"{len(_years(tuple(int(x) for x in yrs.split('-'))))} seasons only, so they are wide.",
        "",
    ]
    rows = []
    for m in ev.methods:
        row: list[object] = [_name(m)]
        for s in SUBSETS:
            row.append(_ci(ev.one(subset=s, model=m, scope="experts", metric=f"p_at_{K}")))
        rows.append(row)
    for a in [x for x in MODELS if x in ev.methods]:
        row = [f"*{_name(a)} - experts*"]
        for s in SUBSETS:
            row.append(_dci(ev.one(subset=s, model=a, scope="experts_diff",
                                   metric=f"p_at_{K}_diff")))  # fmt: skip
        won = ev.one(subset="all", model=a, scope="experts_diff", metric="seasons_won")
        row[0] = f"{row[0]} (won {int(won['value'])} of {won['n_groups']} seasons)"
        rows.append(row)
    lines += _table(["method", *(SUBSET_TITLES[s] for s in SUBSETS)], rows)
    return lines


def build_evaluation_report(
    evals: Sequence[LabelEvaluation],
    *,
    generated: str = "",
    command: str = "uv run twm radar evaluate",
    store_name: str = "data/predictions.duckdb",
    dataset_name: str = "data/waiver_radar/dataset.parquet",
    figures: Sequence[str] = (),
) -> EvaluationReport:
    """The markdown report, its CSV rows (every number) and a short summary. ``generated`` is
    the only run-dependent text (one line)."""
    first = evals[0]
    lines = [
        "# Waiver Radar evaluation (step C5)",
        "",
        f"Generated by `{command}` from `{store_name}` (the predictions store) and "
        f"`{dataset_name}`.",
        generated,
        "",
        "**How to read this.** Every Tuesday of every season from "
        f"{first.seasons[0]} to {first.seasons[-1]}, each method ranked the players probably "
        "on waivers at each position; the models learned only from earlier seasons (C4, "
        "`reports/waiver_radar/backtest.md`). This report grades those stored rankings. "
        f"**Precision@{K}** = the share of a list's top {K} who became a fantasy starter soon "
        "(see each label). Every rate comes with its counts, and the main ones with a **95% "
        "interval**: the range the number would plausibly move within if the same kind of "
        "seasons were played again. It comes from the *season-block bootstrap*: draw "
        f"{len(first.seasons)} seasons at random from the {len(first.seasons)} test seasons "
        f"(some twice, some not at all), recompute, repeat {first.n_boot:,} times (fixed seed "
        f"{first.seed}), keep the middle 95%. Whole seasons are drawn because the weeks of one "
        "season are not independent (same players, same model). docs/waiver_radar.md "
        "('Evaluation') explains everything in plain words.",
        "",
        "## Inputs",
        "",
        "- Predictions: the current model version of every (method, label, test season) in the "
        "store (the one written last); nothing is re-predicted, so the time machine shows the "
        "same rankings.",
        "- The dataset adds names, teams, rostership and the experts' coverage; its labels and "
        "pool rows were checked against the store: they agree on every prediction.",
        "",
    ]
    rows = []
    for ev in evals:
        if ev.versions is None:
            continue
        for r in ev.versions.iter_rows(named=True):
            rows.append([f"`{ev.label}`", r["model"], r["versions"], f"{r['rows']:,}"])
    if rows:
        lines += _table(["label", "method", "model versions", "predictions"], rows)
        lines.append("")
    lines += ["## Headlines", ""]
    summary: list[str] = []
    for ev in evals:
        for line in _headline_lines(ev):
            lines.append(f"- {line}")
            summary.append(line)
    lines.append("")
    if figures:
        lines += ["## Figures", ""]
        for f in figures:
            stem = Path(f).stem
            lines += [f"![{FIGURE_CAPTIONS.get(stem, stem)}]({f})", ""]
    for ev in evals:
        lines += _headline(ev)
        lines += _diff_section(ev)
        lines += _season_section(ev)
        lines += _position_section(ev)
        lines += _bucket_section(ev)
        lines += _prob_section(ev)
        lines += _calibration_section(ev)
        lines += _experts_section(ev)
        lines += _breakout_section(ev)
        lines.append("")
    csv_rows = [r for ev in evals for r in ev.results]
    csv_rows = sorted(csv_rows, key=lambda r: tuple(str(r[c]) for c in CSV_COLUMNS[:7]))
    text = "\n".join(line for line in lines).rstrip() + "\n"
    return EvaluationReport(text, csv_rows, summary)


def _headline_lines(ev: LabelEvaluation) -> list[str]:
    full = _rng(ev.seasons)
    out = []
    w = ev.winner
    if w is None:
        return out
    r = ev.one(subset="all", model=w, scope="pooled", seasons=full, metric=f"p_at_{K}")
    rw = ev.one(subset="without_rostered", model=w, scope="pooled", seasons=full,
                metric=f"p_at_{K}")  # fmt: skip
    out.append(
        f"`{ev.label}`: {_name(w)} precision@{K} {full} {_ci(r)} over {_n(r['n_groups'])} lists "
        f"({_n(r['n_top_hits'])} of {_n(r['n_top'])} top-10 picks hit); without rostered "
        f"{_ci(rw)}."
    )
    for b in [x for x in NAIVE_BASELINES if x in ev.methods]:
        d = ev.one(subset="all", model=w, scope="diff", seasons=full, key=b,
                   metric=f"p_at_{K}_diff")  # fmt: skip
        won = ev.one(subset="all", model=w, scope="diff", seasons=full, key=b,
                     metric="seasons_won")  # fmt: skip
        out.append(
            f"`{ev.label}`: {_name(w)} - {_name(b)}: {_dci(d)} points, ahead in "
            f"{int(won['value'])} of {won['n_groups']} seasons."
        )
    d = ev.get(subset="all", model="logit", scope="diff", seasons=full, key="lgbm",
               metric=f"p_at_{K}_diff")  # fmt: skip
    if d:
        out.append(f"`{ev.label}`: {_name('logit')} - {_name('lgbm')}: {_dci(d[0])} points.")
    c = ev.get(subset="all", model=w, scope="breakouts", seasons=full, metric="caught_share")
    if c:
        out.append(
            f"`{ev.label}`: breakouts caught by {_name(w)}: {_ci(c[0])} "
            f"({_n(c[0]['n_pos'])} of {_n(c[0]['n_rows'])})."
        )
    return out


def _fmt(v: object) -> object:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.6f}"
    return v


def write_evaluation_report(report: EvaluationReport, md_path: Path) -> Path:
    """Write the markdown and ``<same name>.csv`` next to it; return the CSV path."""
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(report.markdown, encoding="utf-8")
    csv_path = md_path.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(CSV_COLUMNS), lineterminator="\n")
        w.writeheader()
        for r in report.csv_rows:
            w.writerow({k: _fmt(r[k]) for k in CSV_COLUMNS})
    return csv_path


__all__ = [
    "EvalData",
    "EvaluationError",
    "EvaluationReport",
    "LabelEvaluation",
    "biggest_breakouts",
    "breakout_events",
    "build_evaluation_report",
    "evaluate_label",
    "load",
    "load_predictions",
    "prepare",
    "ranked_subset",
    "write_evaluation_report",
]
