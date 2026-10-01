"""Hot-Seat H3b walk-forward backtest (PROJECT_SPEC 8.5 "Evaluation").

Test seasons 2006-2025, each model trained on every earlier season from 2002
(:func:`twm.backtest.walkforward.walk_forward`, which refuses any row of the test season or
later). Interim coaches never train; every test-season row is scored by its fold's model,
interims included (reported apart). Metrics per as-of week, at the end-of-season snapshot and
over all rows: ROC-AUC, PR-AUC, Brier (season-block intervals, :mod:`.evaluation`); the top-5
hit rate at week 12 and at season end; firings per season; reliability tables. Variants: the
main run, censored rows dropped, ``resigned_under_pressure`` counted as positive.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

import numpy as np
import polars as pl

from twm.backtest.metrics import calibration_fixed_bins
from twm.backtest.walkforward import WalkForwardResult, walk_forward
from twm.modules.hot_seat import evaluation as ev
from twm.modules.hot_seat.models import (
    KEYS,
    MODULE,
    SPECS,
    InnerCvLogit,
    Spec,
    coefficients,
    neg_log_loss,
    season_risk,
)

FIRST_SEASON = 2002
TEST_SEASONS = tuple(range(2006, 2026))
TOP_K = 5
TOP_WEEK = 12
MAIN_MODELS = ("logit", "hazard", "lgbm", "base_win_pct", "base_wins_vs_expected")
SENSITIVITY_MODELS = ("logit", "hazard", "base_win_pct", "base_wins_vs_expected")
SCORED_COLUMNS = (*KEYS, "is_interim", "y", "censored")


@dataclass
class ModelRun:
    name: str
    result: WalkForwardResult
    scored: pl.DataFrame  # SCORED_COLUMNS + prob (primary) + prob_iso (harness isotonic)


def score_folds(spec: Spec, result: WalkForwardResult, rows: pl.DataFrame) -> pl.DataFrame:
    """Every row of each fold's test season (interims too) scored by that fold's model:
    ``prob`` = the model's own probability (the hazard model's season risk), ``prob_iso`` =
    the harness's isotonic calibration of it (NULL for the hazard model)."""
    out = []
    for fr in result.folds:
        test = rows.filter(pl.col("season") == fr.fold.test_season).sort(list(KEYS))
        x = test.select(list(spec.features))
        raw = fr.model.predict(x)
        if spec.name == "hazard":
            prob, iso = season_risk(fr.model, x), np.full(test.height, np.nan)
        else:
            prob, iso = raw, fr.calibrate(raw)
        out.append(
            test.select(SCORED_COLUMNS).with_columns(
                pl.Series("prob", prob, dtype=pl.Float64),
                pl.Series("prob_iso", iso, dtype=pl.Float64).fill_nan(None),
            )
        )
    return pl.concat(out, how="vertical")


def run_model(
    spec: Spec, rows: pl.DataFrame, test_seasons: Iterable[int] = TEST_SEASONS
) -> ModelRun:
    """Walk-forward of one model on ``rows`` (non-interim rows train), every test row scored."""
    train = rows.filter(~pl.col("is_interim"))
    estimator = spec.make()
    if isinstance(estimator, InnerCvLogit):
        # fit reads the season of each training row from this frame (its inner walk-forward)
        estimator.bind(train, spec.features, KEYS)
    with warnings.catch_warnings():
        # the 2006 fold trains on 2002-2005, which have no decision grades: the imputer drops
        # the all-missing column for that fold (the model then cannot use it), as intended
        warnings.filterwarnings("ignore", message="Skipping features without any observed")
        result = walk_forward(
            train,
            estimator=estimator,
            features=spec.features,
            label=spec.label,
            module=MODULE,
            test_seasons=test_seasons,
            keys=KEYS,
            tune_metric=neg_log_loss(spec.label),
        )
        scored = score_folds(spec, result, rows)
    return ModelRun(spec.name, result, scored)


def run_variant(
    rows: pl.DataFrame,
    models: Sequence[str],
    test_seasons: Iterable[int] = TEST_SEASONS,
    progress: Callable[[str], None] | None = None,
) -> dict[str, ModelRun]:
    out = {}
    for name in models:
        out[name] = run_model(SPECS[name], rows, test_seasons)
        if progress:
            progress(f"  {name}: {len(out[name].result.folds)} folds")
    return out


def slices(df: pl.DataFrame) -> list[tuple[str, pl.DataFrame]]:
    """'all', one per weekly as-of week ('week_02' ...), 'end_of_season'."""
    weekly = df.filter(pl.col("snapshot") == "weekly")
    weeks = sorted(weekly.get_column("week").unique().to_list())
    out = [("all", df)]
    out += [(f"week_{w:02d}", weekly.filter(pl.col("week") == w)) for w in weeks]
    out.append(("end_of_season", df.filter(pl.col("snapshot") == "end_of_season")))
    return out


def evaluated(scored: pl.DataFrame, prob: str = "prob") -> pl.DataFrame:
    """The graded rows: not interim, with a probability."""
    return scored.filter(~pl.col("is_interim") & pl.col(prob).is_not_null())


def top_k_counts(df: pl.DataFrame, prob: str = "prob", k: int = TOP_K) -> pl.DataFrame:
    """Per season: positives, and positives among the season's top k by ``prob`` (ties: the
    team code). One as-of per season is expected (week 12 or the end-of-season rows)."""
    ranked = df.sort(["season", prob, "team"], descending=[False, True, False]).with_columns(
        (pl.int_range(pl.len()).over("season") < k).alias("_top")
    )
    return (
        ranked.group_by("season")
        .agg(
            pl.col("y").cast(pl.Int64).sum().alias("positives"),
            (pl.col("y").cast(pl.Int64) * pl.col("_top")).sum().alias("hits"),
        )
        .sort("season")
    )


def _row(variant, model, prob, sl, metric, e: ev.Estimate, df: pl.DataFrame) -> dict:
    return {"variant": variant, "model": model, "prob": prob, "slice": sl, "metric": metric,
            "value": e.value, "lo": e.lo, "hi": e.hi, "n_rows": df.height,
            "n_pos": int(df.get_column("y").sum()) if df.height else 0,
            "n_seasons": df.get_column("season").n_unique()}  # fmt: skip


Estimates = dict[tuple[str, str, str], dict[str, ev.Estimate]]


def metric_table(
    runs: dict[str, ModelRun], variant: str, n_boot: int = ev.N_BOOT
) -> tuple[pl.DataFrame, Estimates]:
    """One row per (model, probability, slice, metric) and the estimates (for differences)."""
    rows, est = [], {}
    for name, run in runs.items():
        for prob in ("prob", "prob_iso"):
            df = evaluated(run.scored, prob)
            if df.height == 0:
                continue
            for sl, sdf in slices(df):
                m = ev.slice_metrics(ev.Slice.of(sdf["y"], sdf[prob], sdf["season"]), n_boot)
                est[(name, prob, sl)] = m
                rows += [_row(variant, name, prob, sl, k, e, sdf) for k, e in m.items()]
            if prob != "prob":
                continue
            week = df.filter((pl.col("snapshot") == "weekly") & (pl.col("week") == TOP_WEEK))
            eos = df.filter(pl.col("snapshot") == "end_of_season")
            for sl, sdf in ((f"week_{TOP_WEEK:02d}", week), ("end_of_season", eos)):
                if sdf.height == 0:
                    continue
                c = top_k_counts(sdf, prob)
                e = ev.ratio(c["hits"].to_numpy(), c["positives"].to_numpy(), n_boot)
                rows.append(_row(variant, name, prob, sl, f"top{TOP_K}_hit_rate", e, sdf))
    return pl.DataFrame(rows, infer_schema_length=None), est


DIFF_PAIRS = (
    ("hazard", "logit"), ("lgbm", "logit"), ("lgbm", "hazard"),
    ("logit", "base_win_pct"), ("logit", "base_wins_vs_expected"),
    ("hazard", "base_win_pct"), ("hazard", "base_wins_vs_expected"),
)  # fmt: skip


def difference_table(est: Estimates, variant: str, slices_: Sequence[str]) -> pl.DataFrame:
    """a minus b (primary probabilities) with paired season-block intervals."""
    rows = []
    for a, b in DIFF_PAIRS:
        for sl in slices_:
            ea, eb = est.get((a, "prob", sl)), est.get((b, "prob", sl))
            if ea is None or eb is None:
                continue
            for metric in ev.METRICS:
                d = ev.difference(ea[metric], eb[metric])
                rows.append({"variant": variant, "a": a, "b": b, "slice": sl, "metric": metric,
                             "diff": d.value, "lo": d.lo, "hi": d.hi})  # fmt: skip
    return pl.DataFrame(rows, infer_schema_length=None)


def firings_per_season(rows: pl.DataFrame) -> pl.DataFrame:
    """Per season, coach-seasons (interims apart): positive departures (y = 1 on any row),
    of them fired in season, positives at week 12 and at season end, censored, interims."""
    cs = rows.group_by("season", "team", "coach_id").agg(
        pl.col("is_interim").any().alias("interim"),
        (pl.col("y") == 1).any().alias("positive"),
        pl.col("censored").any().alias("censored"),
        (pl.col("departure_type") == "fired_in_season").any().alias("in_season"),
        ((pl.col("snapshot") == "weekly") & (pl.col("week") == TOP_WEEK) & (pl.col("y") == 1))
        .any()
        .alias("pos_week12"),
        ((pl.col("snapshot") == "end_of_season") & (pl.col("y") == 1)).any().alias("pos_eos"),
    )
    core = pl.col("interim").not_()
    return (
        cs.group_by("season")
        .agg(
            (pl.col("positive") & core).sum().alias("positive_departures"),
            (pl.col("positive") & core & pl.col("in_season")).sum().alias("fired_in_season"),
            (pl.col("pos_week12") & core).sum().alias(f"positives_week_{TOP_WEEK}"),
            (pl.col("pos_eos") & core).sum().alias("positives_end_of_season"),
            (pl.col("censored") & core).sum().alias("censored_coach_seasons"),
            pl.col("interim").sum().alias("interim_coach_seasons"),
        )
        .sort("season")
    )


def coefficient_table(run: ModelRun, variant: str) -> pl.DataFrame:
    """Every fold's standardized coefficients (logistic models) with the final model's C and
    l1_ratio (``fr.model.refit_params``: ``fr.params`` are the tuning fit's)."""
    rows = []
    for fr in run.result.folds:
        params = fr.model.refit_params
        for term, coef in coefficients(fr.model):
            rows.append(
                {"variant": variant, "model": run.name, "test_season": fr.fold.test_season,
                 "term": term, "coef": coef, "C": params.get("C"),
                 "l1_ratio": params.get("l1_ratio")}
            )  # fmt: skip
    return pl.DataFrame(rows, infer_schema_length=None)


def penalty_table(runs: dict[str, ModelRun], variant: str) -> pl.DataFrame:
    """Per inner-CV model and fold: each candidate C's log loss summed over the inner seasons
    (one row per C; a single NULL-loss row when the default C was used), the chosen one
    flagged, for the final model (all training seasons) and the tuning model (the validation
    season held out: the harness's isotonic calibration is fit on its scores)."""
    rows = []
    for name, run in runs.items():
        for fr in run.result.folds:
            fits = [("final", fr.model.refit_params)]
            fits += [("tuning", t.refit_params) for t in fr.trials]
            for fit, p in fits:
                if "c_source" not in p:
                    continue
                losses = p["inner_log_loss"] or {p["C"]: None}
                for c, loss in losses.items():
                    rows.append(
                        {"variant": variant, "model": name, "test_season": fr.fold.test_season,
                         "fit": fit, "C": float(c), "sum_log_loss": loss,
                         "chosen": c == p["C"], "c_source": p["c_source"],
                         "inner_seasons": p["inner_seasons"]}
                    )  # fmt: skip
    return pl.DataFrame(rows, infer_schema_length=None)


def coefficient_summary(coefs: pl.DataFrame) -> pl.DataFrame:
    """Per model and term: the last fold's coefficient (trained on every season before the
    last test season), the mean and range over the folds, and the share of folds whose sign
    matches the last fold's (0 counts as no match)."""
    last = coefs.group_by("variant", "model").agg(pl.col("test_season").max().alias("_last"))
    df = coefs.join(last, on=["variant", "model"])
    final = df.filter(pl.col("test_season") == pl.col("_last")).select(
        "variant", "model", "term", pl.col("coef").alias("last_fold")
    )
    agg = df.group_by("variant", "model", "term").agg(
        pl.col("coef").mean().alias("mean"),
        pl.col("coef").min().alias("min"),
        pl.col("coef").max().alias("max"),
        pl.len().alias("folds"),
    )
    out = final.join(agg, on=["variant", "model", "term"], how="left")
    signs = df.join(final, on=["variant", "model", "term"]).group_by(
        "variant", "model", "term"
    ).agg(((pl.col("coef").sign() == pl.col("last_fold").sign()) & (pl.col("coef") != 0))
          .mean().alias("same_sign_share"))  # fmt: skip
    return out.join(signs, on=["variant", "model", "term"], how="left")


def reliability(runs: dict[str, ModelRun], variant: str) -> pl.DataFrame:
    """Fixed 10-point bins of predicted vs observed, all rows and end of season."""
    out = []
    for name, run in runs.items():
        for prob in ("prob", "prob_iso"):
            df = evaluated(run.scored, prob)
            if df.height == 0:
                continue
            for sl, sdf in (("all", df), ("end_of_season", slices(df)[-1][1])):
                bins = calibration_fixed_bins(sdf, prob=prob, label="y")
                tags = {"variant": variant, "model": name, "prob": prob, "slice": sl}
                out.append(bins.with_columns(pl.lit(v).alias(k) for k, v in tags.items()))
    cols = ["variant", "model", "prob", "slice"]
    return pl.concat(out, how="vertical").select(*cols, pl.exclude(cols))


def group_summary(runs: dict[str, ModelRun], variant: str) -> pl.DataFrame:
    """Mean predicted risk by group, all rows and end of season: positives, plain negatives,
    censored (non-firing departures) and interims (never trained on, scored apart)."""
    group = (
        pl.when(pl.col("is_interim"))
        .then(pl.lit("interim"))
        .when(pl.col("y") == 1)
        .then(pl.lit("positive"))
        .when(pl.col("censored"))
        .then(pl.lit("censored"))
        .otherwise(pl.lit("negative"))
    )
    out = []
    for name, run in runs.items():
        df = run.scored.with_columns(group.alias("group"))
        for sl, sdf in (
            ("all", df),
            ("end_of_season", df.filter(pl.col("snapshot") == "end_of_season")),
        ):
            agg = sdf.group_by("group").agg(
                pl.len().alias("n_rows"),
                pl.struct("season", "team", "coach_id").n_unique().alias("coach_seasons"),
                pl.col("y").sum().alias("n_pos"),
                pl.col("prob").mean().alias("mean_prob"),
            )
            tags = {"variant": variant, "model": name, "slice": sl}
            out.append(agg.with_columns(pl.lit(v).alias(k) for k, v in tags.items()))
    cols = ["variant", "model", "slice", "group"]
    return pl.concat(out, how="vertical").select(*cols, pl.exclude(cols)).sort(cols)


def lgbm_verdict(diffs: pl.DataFrame) -> tuple[bool, str]:
    """LightGBM is used only if, over all test rows, it has a lower Brier AND a higher PR-AUC
    than both the logistic and the hazard model, each paired interval excluding 0."""
    d = diffs.filter((pl.col("variant") == "main") & (pl.col("slice") == "all"))
    parts, wins = [], True
    for other in ("logit", "hazard"):
        for metric, better in (("brier", "lo_hi_below"), ("pr_auc", "lo_above")):
            r = d.filter(
                (pl.col("a") == "lgbm") & (pl.col("b") == other) & (pl.col("metric") == metric)
            )
            if r.height == 0:
                return False, "LightGBM was not run"
            row = r.row(0, named=True)
            ok = row["hi"] < 0 if better == "lo_hi_below" else row["lo"] > 0
            wins &= bool(ok)
            parts.append(
                f"{metric} vs {other} {row['diff']:+.4f} ({row['lo']:+.4f} to {row['hi']:+.4f})"
            )
    verdict = "beats both: used" if wins else "does not beat both: not used"
    return wins, f"LightGBM {verdict} ({'; '.join(parts)})"


@dataclass
class BacktestResult:
    metrics: pl.DataFrame
    differences: pl.DataFrame
    coefficients: pl.DataFrame
    coefficient_summary: pl.DataFrame
    penalties: pl.DataFrame  # the inner walk-forward's choice of C, every fold
    reliability: pl.DataFrame
    groups: pl.DataFrame
    firings: pl.DataFrame
    predictions: pl.DataFrame  # main run, every model, test seasons
    lgbm_used: bool
    lgbm_text: str


def run_backtest(
    rows_main: pl.DataFrame,
    rows_rup: pl.DataFrame,
    *,
    test_seasons: Iterable[int] = TEST_SEASONS,
    n_boot: int = ev.N_BOOT,
    progress: Callable[[str], None] | None = None,
) -> BacktestResult:
    """The main run (every model), censored rows dropped and resigned-under-pressure positive
    (the two small models and the baselines)."""
    seasons = tuple(test_seasons)
    variants = {
        "main": (rows_main, MAIN_MODELS),
        "censored_dropped": (rows_main.filter(~pl.col("censored")), SENSITIVITY_MODELS),
        "rup_positive": (rows_rup, SENSITIVITY_MODELS),
    }
    metrics, diffs, coefs, pens, rel, groups, preds = [], [], [], [], [], [], []
    for variant, (rows, models) in variants.items():
        if progress:
            progress(f"{variant}: {rows.height} rows")
        runs = run_variant(rows, models, seasons, progress)
        tab, est = metric_table(runs, variant, n_boot)
        metrics.append(tab)
        diffs.append(difference_table(est, variant, [s for s, _ in slices(rows)]))
        coefs += [coefficient_table(runs[m], variant) for m in ("logit", "hazard")]
        pens.append(penalty_table(runs, variant))
        groups.append(group_summary(runs, variant))
        if variant == "main":
            rel.append(reliability(runs, variant))
            preds += [r.scored.with_columns(pl.lit(n).alias("model")) for n, r in runs.items()]
    differences = pl.concat(diffs, how="vertical")
    used, text = lgbm_verdict(differences)
    coef_df = pl.concat(coefs, how="vertical")
    return BacktestResult(
        metrics=pl.concat(metrics, how="vertical"),
        differences=differences,
        coefficients=coef_df,
        coefficient_summary=coefficient_summary(coef_df),
        penalties=pl.concat(pens, how="vertical"),
        reliability=pl.concat(rel, how="vertical"),
        groups=pl.concat(groups, how="vertical"),
        firings=firings_per_season(rows_main),
        predictions=pl.concat(preds, how="vertical"),
        lgbm_used=used,
        lgbm_text=text,
    )
