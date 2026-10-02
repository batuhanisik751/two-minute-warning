"""Board backtest metrics (step I1b), every one from the shared code:

- PR-AUC (average precision), ROC-AUC, Brier with season-block bootstrap intervals and paired
  differences: :mod:`twm.modules.hot_seat.evaluation` (seasons resampled with replacement, the
  same resamples for every method);
- precision@10 and @20 per test season, pooled (mean over seasons) with a season-block interval
  and paired differences: :mod:`twm.backtest.metrics`;
- calibration: equal-count bins (:func:`twm.backtest.metrics.calibration_bins`).

Slices: ``all`` = every test season (snapshots 2007-2024, labels 2008-2025); ``ecr_era`` =
snapshots 2019-2024 (labels 2020-2025), the seasons with a preseason ECR, where the ECR baseline
(``ecr``, a score, not a probability: no Brier) is compared on the same rows.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import polars as pl

from twm.backtest import metrics as mt
from twm.modules.hot_seat import evaluation as ev

KS = (10, 20)
ECR_FIRST_SNAPSHOT = 2019
N_CAL_BINS = 10


def ranked(df: pl.DataFrame, model: str) -> pl.DataFrame:
    """Rows ranked within each season by ``p_<model>`` (highest first; ties: the id)."""
    return mt.add_rank(df, group=["season"], by=[(f"p_{model}", True)], id_col="gsis_id")


def precision_table(df: pl.DataFrame, model: str, k: int) -> pl.DataFrame:
    return mt.precision_at_k(ranked(df, model), group=["season"], label="y", k=k)


def _row(slice_: str, model: str, metric: str, vs: str | None, value, lo, hi, share=None) -> dict:
    return {"slice": slice_, "model": model, "vs": vs, "metric": metric, "value": value,
            "lo": lo, "hi": hi, "share_above_zero": share}  # fmt: skip


def slice_table(df: pl.DataFrame, models: Sequence[str], refs: Sequence[str], name: str) -> list:
    """Metric rows of one slice: each model's values, then each model minus each reference."""
    out: list[dict] = []
    est, prec = {}, {}
    for m in models:
        s = ev.Slice.of(df.get_column("y"), df.get_column(f"p_{m}"), df.get_column("season"))
        est[m] = ev.slice_metrics(s)
        for metric in ("pr_auc", "roc_auc", "brier"):
            if metric == "brier" and m == "ecr":
                continue
            e = est[m][metric]
            out.append(_row(name, m, metric, None, e.value, e.lo, e.hi))
        for k in KS:
            prec[(m, k)] = precision_table(df, m, k)
            iv = mt.block_bootstrap(prec[(m, k)], block="season", value="p_at_k")
            out.append(_row(name, m, f"p_at_{k}", None, iv.value, iv.lo, iv.hi))
    for m in models:
        for r in refs:
            if r == m or r not in est:
                continue
            d = ev.difference(est[m]["pr_auc"], est[r]["pr_auc"])
            share = float(np.mean(d.boot > 0)) if d.boot.size else None
            out.append(_row(name, m, "pr_auc_diff", r, d.value, d.lo, d.hi, share))
            for k in KS:
                iv = mt.paired_block_bootstrap(prec[(m, k)], prec[(r, k)], keys=["season"])
                out.append(_row(name, m, f"p_at_{k}_diff", r, iv.value, iv.lo, iv.hi,
                                iv.share_above_zero))  # fmt: skip
    return out


def per_season(df: pl.DataFrame, models: Sequence[str]) -> pl.DataFrame:
    """Rows, positives and each model's hits in its top 10 and 20 per test season."""
    base = df.group_by("season").agg(pl.len().alias("rows"), pl.col("y").sum().alias("positives"))
    for m in models:
        for k in KS:
            t = precision_table(df, m, k).select(
                "season", pl.col("hits_top").alias(f"{m}_hits_top{k}")
            )
            base = base.join(t, on="season", how="left")
    return base.sort("season")


def calibration(df: pl.DataFrame, models: Sequence[str]) -> pl.DataFrame:
    parts = []
    for m in models:
        b = mt.calibration_bins(df, prob=f"p_{m}", label="y", id_cols=["season", "gsis_id"],
                                n_bins=N_CAL_BINS)  # fmt: skip
        parts.append(b.with_columns(pl.lit(m).alias("model")))
    return pl.concat(parts, how="vertical").select("model", pl.exclude("model"))


def disagreements(
    df: pl.DataFrame, model: str, top: int = 10, per_season_examples: int = 2
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Where ``model`` and the ECR disagree (ECR era): per season, the model's top ``top`` not in
    the ECR's top ``top`` ('model_only'), the reverse ('ecr_only') and both ('both'). Returns
    (hit rate per group pooled over the seasons, the largest rank gaps per season and group)."""
    era = df.filter(pl.col("season") >= ECR_FIRST_SNAPSHOT, pl.col("p_ecr").is_not_null())
    era = ranked(era, model).rename({"rank": "rank_model"})
    era = ranked(era, "ecr").rename({"rank": "rank_ecr"})
    in_m, in_e = pl.col("rank_model") <= top, pl.col("rank_ecr") <= top
    era = era.with_columns(
        pl.when(in_m & in_e).then(pl.lit("both")).when(in_m).then(pl.lit("model_only"))
        .when(in_e).then(pl.lit("ecr_only")).otherwise(None).alias("group"),
        (pl.col("rank_model") - pl.col("rank_ecr")).abs().alias("gap"),
    )  # fmt: skip
    flagged = era.filter(pl.col("group").is_not_null())
    summary = (
        flagged.group_by("group")
        .agg(pl.len().alias("rows"), pl.col("y").cast(pl.Int64).sum().alias("hits"))
        .with_columns((pl.col("hits") / pl.col("rows")).alias("hit_rate"))
        .sort("group")
    )
    examples = (
        flagged.filter(pl.col("group") != "both")
        .sort(["season", "group", "gap", "gsis_id"], descending=[False, False, True, False])
        .group_by(["season", "group"], maintain_order=True)
        .head(per_season_examples)
    )
    return summary, examples
