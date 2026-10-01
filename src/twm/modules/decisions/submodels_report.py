"""The sub-model backtest report (G2): reports/decisions/submodels.md + .csv.

Every number comes from the saved walk-forward folds (test seasons 2006-2025, each scored by a
model that learned from earlier seasons only). Binary models (go-for-it conversion, field goal)
get Brier score, log loss and the expected calibration error (ECE, 10 fixed-width bins), each
against a point-in-time lookup baseline (the training seasons' rate per distance), with 95%
**season-block bootstrap** intervals (:mod:`twm.backtest.metrics`, 2,000 resamples, fixed
seed), per era and in a reliability table. The punt distribution gets its log score (5-yard
bins) against the raw same-yardline history, predicted vs observed rates by field zone, and
expected WP vs the WP of what really happened (G1's fold model of the same season). The try
rates get Brier and log loss against a naive all-history rate. Deterministic: sorted rows, no
timestamp.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
import polars as pl

from twm.backtest import metrics as mt
from twm.modules.decisions import conversion as cv
from twm.modules.decisions import fieldgoal as fg
from twm.modules.decisions import punt as pu
from twm.modules.decisions import submodels as sm
from twm.modules.decisions import tries as tr
from twm.modules.decisions.wp_report import ERAS, SCHEMA, _fmt, _row

N_BINS = 10
EPS = 1e-12


def report_paths() -> tuple[Path, Path]:
    from twm.config import ROOT

    md = ROOT / "reports" / "decisions" / "submodels.md"
    return md, md.with_suffix(".csv")


def interval(sub: pl.DataFrame, value: str, block: str = "season") -> mt.Interval:
    return mt.block_bootstrap(sub.select(block, value), block=block, value=value)


def ece(sub: pl.DataFrame, col: str, label: str) -> tuple[float | None, float | None, float | None]:
    """Expected calibration error (10 fixed-width bins, row-weighted mean |predicted -
    observed|) with a season-block bootstrap interval."""
    if sub.height == 0:
        return None, None, None
    seasons = sorted(sub.get_column("season").unique().to_list())
    pos = {s: i for i, s in enumerate(seasons)}
    p = sub.get_column(col).cast(pl.Float64).to_numpy()
    y = sub.get_column(label).cast(pl.Float64).to_numpy()
    si = np.array([pos[s] for s in sub.get_column("season").to_list()])
    b = np.clip(np.floor(p * N_BINS).astype(np.int64), 0, N_BINS - 1)
    n, sp, sy = (np.zeros((len(seasons), N_BINS)) for _ in range(3))
    np.add.at(n, (si, b), 1.0)
    np.add.at(sp, (si, b), p)
    np.add.at(sy, (si, b), y)

    def value(n_, sp_, sy_):
        with np.errstate(invalid="ignore", divide="ignore"):
            gap = np.where(n_ > 0, np.abs(sp_ - sy_) / np.where(n_ > 0, n_, 1.0), 0.0)
        return (n_ * gap).sum(axis=-1) / n_.sum(axis=-1)

    point = float(value(n.sum(0), sp.sum(0), sy.sum(0)))
    if len(seasons) < 2:
        return point, None, None
    idx = mt.block_indices(len(seasons))
    boot = value(n[idx].sum(1), sp[idx].sum(1), sy[idx].sum(1))
    lo, hi = np.quantile(boot, [(1 - mt.LEVEL) / 2, 1 - (1 - mt.LEVEL) / 2])
    return point, float(lo), float(hi)


def terms(frame: pl.DataFrame, label: str, methods: Mapping[str, str]) -> pl.DataFrame:
    """Per-row Brier (``b_<m>``) and log-loss (``l_<m>``) terms of each method's column."""
    y = pl.col(label).cast(pl.Float64)
    exprs = []
    for m, col in methods.items():
        p = pl.col(col).cast(pl.Float64)
        q = p.clip(EPS, 1.0 - EPS)
        exprs += [((p - y) ** 2).alias(f"b_{m}"),
                  (-(y * q.log() + (1.0 - y) * (1.0 - q).log())).alias(f"l_{m}")]  # fmt: skip
    return frame.with_columns(exprs)


def lookup(rows: pl.DataFrame, label: str, group: Sequence[pl.Expr], seasons: Sequence[int]):
    """The point-in-time lookup baseline: for each test season S, the rate of ``label`` per
    ``group`` cell over seasons < S ((made + 1) / (n + 2): a cell never seen gets 0.5).
    Returns KEYS + ``baseline``."""
    g = rows.with_columns([e.alias(f"g{i}") for i, e in enumerate(group)])
    keys = [f"g{i}" for i in range(len(group))]
    out = []
    for s in seasons:
        past = (
            g.filter(pl.col("season") < s)
            .group_by(keys)
            .agg(pl.col(label).sum().alias("k"), pl.len().alias("n"))
        )
        test = g.filter(pl.col("season") == s).join(past, on=keys, how="left")
        out.append(
            test.select(
                *sm.KEYS,
                ((pl.col("k").fill_null(0) + 1) / (pl.col("n").fill_null(0) + 2)).alias("baseline"),
            )
        )
    return pl.concat(out)  # fmt: skip


def metric_rows(sub: pl.DataFrame, table: str, scope: str, subset: str, label: str,
                methods: Mapping[str, str], ref: str = "model") -> list[dict]:  # fmt: skip
    """Brier, log loss and ECE per method and ``ref`` minus each other method, with
    season-block intervals."""
    out: list[dict] = []
    if sub.height == 0:
        return out
    n, k = sub.height, sub.get_column("season").n_unique()
    for m, col in methods.items():
        for metric, c in (("brier", f"b_{m}"), ("log_loss", f"l_{m}")):
            iv = interval(sub, c)
            out.append(_row(table, scope, subset, m, metric, iv.value, iv.lo, iv.hi, n, k))
        out.append(_row(table, scope, subset, m, "ece", *ece(sub, col, label), n, k))
    for other in methods:
        if other == ref:
            continue
        for metric, pre in (("brier", "b"), ("log_loss", "l")):
            d = sub.select("season", (pl.col(f"{pre}_{ref}") - pl.col(f"{pre}_{other}"))
                           .alias("d"))  # fmt: skip
            iv = interval(d, "d")
            out.append(_row(table, scope, subset, f"{ref} - {other}", metric, iv.value, iv.lo,
                            iv.hi, n, k))  # fmt: skip
    return out


def reliability_rows(sub: pl.DataFrame, table: str, label: str, col: str) -> list[dict]:
    """10 fixed-width bins: rows, mean predicted, observed rate (season-block interval)."""
    out = []
    f = sub.select("season", pl.col(col).alias("p"), pl.col(label).cast(pl.Float64).alias("y"),
                   (pl.col(col) * N_BINS).floor().clip(0, N_BINS - 1).cast(pl.Int64)
                   .alias("bin"))  # fmt: skip
    for b in range(N_BINS):
        s = f.filter(pl.col("bin") == b)
        subset = f"{b / N_BINS:.1f}-{(b + 1) / N_BINS:.1f}"
        if s.height == 0:
            continue
        iv, k = interval(s, "y"), s.get_column("season").n_unique()
        out.append(_row(table, "reliability", subset, "model", "mean_predicted",
                        float(s.get_column("p").mean()), n=s.height, blocks=k))  # fmt: skip
        out.append(_row(table, "reliability", subset, "model", "observed", iv.value, iv.lo,
                        iv.hi, s.height, k))  # fmt: skip
    return out


def rate_rows(sub: pl.DataFrame, table: str, scope: str, subset: str, label: str,
              pred: str) -> list[dict]:  # fmt: skip
    """Observed rate (season-block interval) and mean predicted probability of a slice."""
    if sub.height == 0:
        return []
    k = sub.get_column("season").n_unique()
    obs = interval(sub.with_columns(pl.col(label).cast(pl.Float64)), label)
    prd = interval(sub.with_columns(pl.col(pred).cast(pl.Float64)), pred)
    return [_row(table, scope, subset, "observed", "rate", obs.value, obs.lo, obs.hi,
                 sub.height, k),
            _row(table, scope, subset, "model", "mean_predicted", prd.value, prd.lo, prd.hi,
                 sub.height, k)]  # fmt: skip


# --------------------------------------------------------------------------------------
# Binary models: conversion and field goal
# --------------------------------------------------------------------------------------


def binary_frame(rows: pl.DataFrame, model: str, seasons: Sequence[int], out_dir=None):
    """The test rows of ``seasons`` with the saved fold probability (``prob``) and the fold
    summaries; SubModelError when the folds do not cover exactly those rows."""
    preds, summaries = sm.load_backtest(model, seasons, out_dir=out_dir)
    test = rows.filter(pl.col("season").is_in(list(seasons)))
    frame = test.join(preds.select(*sm.KEYS, "prob"), on=list(sm.KEYS), how="inner")
    if frame.height != test.height or preds.height != test.height:
        raise sm.SubModelError(f"{model}: {preds.height} saved predictions for {test.height} "
                               "test rows: rerun the backtest")  # fmt: skip
    return frame.sort(list(sm.KEYS)), summaries


def _eras(frame: pl.DataFrame):
    for name, lo, hi in ERAS:
        yield name, frame.filter(pl.col("season").is_between(lo, hi))


def conversion_rows(rows: pl.DataFrame, seasons: Sequence[int], out_dir=None):
    frame, summaries = binary_frame(rows, cv.MODEL, seasons, out_dir)
    base = lookup(rows, cv.LABEL, [pl.col("down"), pl.col("ydstogo").clip(1, 15)], seasons)
    f = terms(frame.join(base, on=list(sm.KEYS)), cv.LABEL,
              {"model": "prob", "lookup": "baseline"})  # fmt: skip
    methods = {"model": "prob", "lookup": "baseline"}
    span = f"{min(seasons)}-{max(seasons)}"
    out = []
    for down in (4, 3):
        d = f.filter(pl.col("down") == down)
        out += metric_rows(d, "conversion", f"down {down}", span, cv.LABEL, methods)
        for name, sub in _eras(d):
            out += metric_rows(sub, "conversion", f"down {down}", name, cv.LABEL, methods)
    fourth = f.filter(pl.col("down") == 4)
    out += reliability_rows(fourth, "conversion", cv.LABEL, "prob")
    for ytg, cond in (("4th and 1", pl.col("ydstogo") == 1), ("4th and 2-3",
                      pl.col("ydstogo").is_between(2, 3)), ("4th and 4-6",
                      pl.col("ydstogo").is_between(4, 6)), ("4th and 7+",
                      pl.col("ydstogo") >= 7)):  # fmt: skip
        out += rate_rows(fourth.filter(cond), "conversion", "by distance", f"{ytg}, {span}",
                         cv.LABEL, "prob")  # fmt: skip
        for name, sub in _eras(fourth.filter(cond)):
            out += rate_rows(sub, "conversion", "by distance", f"{ytg}, {name}", cv.LABEL,
                             "prob")  # fmt: skip
    return out, summaries, fourth


def _spot_summary(long: pl.DataFrame, td: str, spot: str, prefix: str) -> pl.DataFrame:
    has = pl.col(spot).is_not_null()
    return long.group_by("row").agg(
        (pl.col("prob") * (pl.col("outcome") == td)).sum().alias(f"{prefix}_p_td"),
        ((pl.col("prob") * pl.col(spot).fill_null(0)).sum()
         / (pl.col("prob") * has).sum()).alias(f"{prefix}_spot"),
    )  # fmt: skip


def conversion_spots(
    fourth: pl.DataFrame, seasons: Sequence[int], models_root=None, out_dir=None
) -> list:
    """Held-out check of the ball-spot tables on fourth downs (each season with its own fold):
    predicted vs observed share of touchdowns among conversions and mean new spot after a
    non-touchdown conversion; share of opponent touchdowns and mean opponent spot after a
    failure."""
    parts = []
    for s in seasons:
        m = sm.load_fold_model(cv.MODEL, s, models_root=models_root, out_dir=out_dir)
        f = fourth.filter(pl.col("season") == s).sort(list(sm.KEYS)).with_row_index("row")
        a = _spot_summary(cv.after_success(f, m), "touchdown", "yardline_100", "succ")
        b = _spot_summary(cv.after_failure(f, m), "opp_touchdown", "opp_yardline_100", "fail")
        parts.append(f.join(a, on="row", how="left").join(b, on="row", how="left"))
    f = pl.concat(parts)
    same = pl.col("same_half")
    succ = f.filter(pl.col(cv.LABEL) == 1)
    fail = f.filter(pl.col(cv.LABEL) == 0)
    span = f"{min(seasons)}-{max(seasons)}"
    out = rate_rows(
        succ.with_columns(pl.col("off_td").cast(pl.Float64)),
        "conversion",
        "after a conversion",
        f"touchdown share, {span}",
        "off_td",
        "succ_p_td",
    )
    kept = succ.filter(~pl.col("off_td") & same & (pl.col("n_posteam") == pl.col("posteam")))
    out += rate_rows(
        kept.with_columns(
            (100 - pl.col("n_yardline_100")).alias("obs").cast(pl.Float64),
            (100 - pl.col("succ_spot")).alias("pred"),
        ),
        "conversion",
        "after a conversion",
        f"new spot (yards from own goal), {span}",
        "obs",
        "pred",
    )
    out += rate_rows(
        fail.with_columns(pl.col("def_td").cast(pl.Float64)),
        "conversion",
        "after a failure",
        f"opponent touchdown share, {span}",
        "def_td",
        "fail_p_td",
    )
    lost = fail.filter(~pl.col("def_td") & same & (pl.col("n_posteam") == pl.col("defteam")))
    out += rate_rows(lost.with_columns(pl.col("n_yardline_100").cast(pl.Float64).alias("obs"),
                                       pl.col("fail_spot").alias("pred")),
                     "conversion", "after a failure", f"opponent spot (its yardline_100), "
                     f"{span}", "obs", "pred")  # fmt: skip
    return out


FG_BANDS = (("under 30 yd", 0, 29), ("30-39 yd", 30, 39), ("40-49 yd", 40, 49),
            ("50-54 yd", 50, 54), ("55+ yd", 55, 99))  # fmt: skip


def fieldgoal_rows(rows: pl.DataFrame, seasons: Sequence[int], out_dir=None):
    frame, summaries = binary_frame(rows, fg.MODEL, seasons, out_dir)
    base = lookup(rows, fg.LABEL, [(pl.col("fg_distance").clip(18, 70) // 5)], seasons)
    methods = {"model": "prob", "lookup": "baseline"}
    f = terms(frame.join(base, on=list(sm.KEYS)), fg.LABEL, methods)
    span = f"{min(seasons)}-{max(seasons)}"
    out = metric_rows(f, "fieldgoal", "all kicks", span, fg.LABEL, methods)
    for name, sub in _eras(f):
        out += metric_rows(sub, "fieldgoal", "all kicks", name, fg.LABEL, methods)
    out += metric_rows(f.filter(pl.col("fg_distance") >= 50), "fieldgoal", "50+ yards", span,
                       fg.LABEL, methods)  # fmt: skip
    out += reliability_rows(f, "fieldgoal", fg.LABEL, "prob")
    for band, lo, hi in FG_BANDS:
        sub = f.filter(pl.col("fg_distance").is_between(lo, hi))
        out += rate_rows(sub, "fieldgoal", "by distance", f"{band}, {span}", fg.LABEL, "prob")
        for name, e in _eras(sub):
            out += rate_rows(e, "fieldgoal", "by distance", f"{band}, {name}", fg.LABEL, "prob")
    return out, summaries


def weather_coverage(rows: pl.DataFrame) -> pl.DataFrame:
    """Per season: kicks, outdoor kicks, weather read from the text, weather still missing,
    surface unknown."""
    return rows.group_by("season").agg(
        pl.len().alias("kicks"), (pl.col("roof_closed") == 0).sum().alias("outdoor"),
        pl.col("weather_from_text").sum().alias("from_text"),
        pl.col("weather_missing").sum().alias("missing"),
        pl.col("surface_grass").null_count().alias("surface_unknown"),
    ).sort("season")  # fmt: skip


# --------------------------------------------------------------------------------------
# Punts
# --------------------------------------------------------------------------------------

PUNT_ZONES = (("opp 30-49 (yardline_100 30-49)", 30, 49), ("50-59", 50, 59), ("60-69", 60, 69),
              ("70-79", 70, 79), ("own 1-20 (80-99)", 80, 99))  # fmt: skip


PUNT_EVENTS = (("starts at its own 20", "touchback", "p_touchback"),
               ("return touchdown", "return_td", "p_return_td"),
               ("kicking team keeps the ball", "kicking", "p_kicking"))  # fmt: skip


def punt_frame(rows: pl.DataFrame, seasons: Sequence[int], out_dir=None):
    preds, summaries = sm.load_backtest(pu.MODEL, seasons, out_dir=out_dir)
    test = rows.filter(pl.col("season").is_in(list(seasons)))
    f = test.join(preds, on=list(sm.KEYS), how="inner")
    if f.height != test.height or preds.height != test.height:
        raise sm.SubModelError(f"{pu.MODEL}: {preds.height} saved predictions for "
                               f"{test.height} test punts: rerun the backtest")  # fmt: skip
    recv = pl.col("outcome") == "receiving"
    return f.with_columns(
        (recv & (pl.col("spot") == 80)).cast(pl.Float64).alias("touchback"),
        (pl.col("outcome") == "return_td").cast(pl.Float64).alias("return_td"),
        pl.col("outcome").is_in(["kicking", "kicking_td"]).cast(pl.Float64).alias("kicking"),
        (pl.col("log_score") - pl.col("log_score_raw")).alias("log_score_gain"),
    ).sort(list(sm.KEYS)), summaries


def punt_rows(f: pl.DataFrame, seasons: Sequence[int]) -> list[dict]:
    span = f"{min(seasons)}-{max(seasons)}"
    n, k = f.height, f.get_column("season").n_unique()
    out = []
    for method, col in (("model", "log_score"), ("raw same-yardline history", "log_score_raw"),
                        ("model - raw", "log_score_gain")):  # fmt: skip
        iv = interval(f, col)
        out.append(_row("punt", "log score", span, method, "log_score", iv.value, iv.lo, iv.hi,
                        n, k))  # fmt: skip
    zones = [(f"all punts, {span}", f), *((z, f.filter(pl.col("yardline_100").is_between(lo, hi)))
                                          for z, lo, hi in PUNT_ZONES)]  # fmt: skip
    for zone, sub in zones:
        for what, obs, pred in PUNT_EVENTS:
            out += rate_rows(sub, "punt", what, zone, obs, pred)
        r = sub.filter(pl.col("outcome") == "receiving").with_columns(
            pl.col("spot").cast(pl.Float64))  # fmt: skip
        out += rate_rows(r, "punt", "receiving spot (its yardline_100)", zone, "spot",
                         "pred_spot")  # fmt: skip
    return out


def punt_wp_rows(f: pl.DataFrame, seasons: Sequence[int], *, models_root=None,
                 wp_dir=None, out_dir=None, progress=print) -> tuple[list[dict], int]:  # fmt: skip
    """Expected WP of punting (the distribution) vs the WP of the result that really happened
    (same state construction, same G1 fold model of the season); punts whose state lacks an
    input (timeouts, spread) are skipped. Returns rows and the number of punts used."""
    from twm.modules.decisions import wp as wpm

    parts = []
    for s in seasons:
        try:
            wm = wpm.load_fold_model(s, models_root=models_root, out_dir=wp_dir)
        except wpm.WpModelError:
            progress(f"report: no G1 WP fold for {s}; punt expected WP skipped for it")
            continue
        m = sm.load_fold_model(pu.MODEL, s, models_root=models_root, out_dir=out_dir)
        st = f.filter(pl.col("season") == s).drop_nulls(list(sm.STATE_INPUTS))
        if st.height == 0:
            continue
        exp = pu.expected_wp(st, m, wm)
        real = st.select("outcome", "spot").with_row_index("row").with_columns(prob=pl.lit(1.0))
        rw = pu.outcome_wp(real, st, float(m.extras["runoff_seconds"]), wm).sort("row")
        parts.append(st.select("season", "yardline_100").with_columns(
            pl.Series("exp_wp", exp), pl.Series("real_wp", rw.get_column("wp_kick")),
        ))  # fmt: skip
    if not parts:
        return [], 0
    w = pl.concat(parts).with_columns(
        (pl.col("exp_wp") - pl.col("real_wp")).alias("bias"),
        (pl.col("exp_wp") - pl.col("real_wp")).abs().alias("abs_err"))  # fmt: skip
    span = f"{min(seasons)}-{max(seasons)}"
    out = []
    zones = [(f"all punts, {span}", w), *((z, w.filter(pl.col("yardline_100").is_between(lo, hi)))
                                          for z, lo, hi in PUNT_ZONES)]  # fmt: skip
    for zone, sub in zones:
        k = sub.get_column("season").n_unique()
        for metric in ("exp_wp", "real_wp", "bias", "abs_err"):
            iv = interval(sub, metric)
            out.append(_row("punt", "expected WP", zone, "model", metric, iv.value, iv.lo,
                            iv.hi, sub.height, k))  # fmt: skip
    return out, w.height


# --------------------------------------------------------------------------------------
# Extra points and two-point tries
# --------------------------------------------------------------------------------------


def tries_rows(seasons: Sequence[int], out_dir=None) -> tuple[list[dict], list[dict]]:
    preds, summaries = sm.load_backtest(tr.MODEL, seasons, out_dir=out_dir)
    methods = {"model": "prob", "naive": "prob_naive"}
    f = terms(preds, "success", methods)
    span = f"{min(seasons)}-{max(seasons)}"
    out = []
    for kind in ("pat", "two_point"):
        sub = f.filter(pl.col("kind") == kind)
        out += metric_rows(sub, "tries", kind, span, "success", methods)
        for name, e in _eras(sub):
            out += metric_rows(e, "tries", kind, name, "success", methods)
    for s in summaries:
        season = s["test_season"]
        for kind in ("pat", "two_point"):
            r = s["rates"][kind]
            obs = f.filter((pl.col("season") == season) & (pl.col("kind") == kind))
            out.append(_row("tries", kind, str(season), "model", "rate", r["rate"], r["lo"],
                            r["hi"], r["n"], len(r["seasons"])))  # fmt: skip
            k = int(obs.get_column("success").sum())
            lo, hi = tr.jeffreys(k, obs.height)
            rate = k / obs.height if obs.height else None
            out.append(_row("tries", kind, str(season), "observed", "rate", rate, lo, hi,
                            obs.height, 1))  # fmt: skip
    return out, summaries


# --------------------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------------------


def compute(rows: Mapping[str, pl.DataFrame], *, seasons: Sequence[int] = sm.TEST_SEASONS,
            models_root=None, out_dirs: Mapping[str, Path] | None = None, wp_dir=None,
            progress=print) -> tuple[pl.DataFrame, dict]:  # fmt: skip
    """(the long results table, everything else the markdown needs) from the saved folds."""
    od = dict(out_dirs or {})
    seasons = sorted(seasons)
    t, info = [], {"seasons": seasons}
    cr, info["conversion"], fourth = conversion_rows(rows["conversion"], seasons,
                                                     od.get(cv.MODEL))  # fmt: skip
    t += cr + conversion_spots(fourth, seasons, models_root, od.get(cv.MODEL))
    progress("report: conversion done")
    fr, info["fieldgoal"] = fieldgoal_rows(rows["fieldgoal"], seasons, od.get(fg.MODEL))
    t += fr
    info["weather"] = weather_coverage(rows["fieldgoal"])
    last = sm.load_fold_model(
        fg.MODEL, seasons[-1], models_root=models_root, out_dir=od.get(fg.MODEL)
    )
    info["miss_rule"] = last.extras.get("miss_rule", {})
    progress("report: field goal done")
    pf, info["punt"] = punt_frame(rows["punt"], seasons, od.get(pu.MODEL))
    t += punt_rows(pf, seasons)
    wr, info["punt_wp_n"] = punt_wp_rows(
        pf,
        seasons,
        models_root=models_root,
        wp_dir=wp_dir,
        out_dir=od.get(pu.MODEL),
        progress=progress,
    )
    t += wr
    progress("report: punt done")
    trr, info["tries"] = tries_rows(seasons, od.get(tr.MODEL))
    t += trr
    info["n_rows"] = {k: v.height for k, v in rows.items()}
    return pl.DataFrame(t, schema=SCHEMA, orient="row"), info


# --------------------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------------------


def _g(t: pl.DataFrame, table: str, scope: str, subset: str, method: str, metric: str) -> dict:
    hit = t.filter((pl.col("table") == table) & (pl.col("scope") == scope)
                   & (pl.col("subset") == subset) & (pl.col("method") == method)
                   & (pl.col("metric") == metric))  # fmt: skip
    return hit.row(0, named=True) if hit.height else {"value": None, "lo": None, "hi": None}


def _metric_table(t: pl.DataFrame, table: str, scope: str, subsets: Sequence[str],
                  other: str, other_label: str, prefix: str = "") -> list[str]:  # fmt: skip
    lines = ["| Rows | Method | Brier | Log loss | Calibration error (ECE, pts) |",
             "|---|---|---|---|---|"]  # fmt: skip
    for sub in subsets:
        n = _g(t, table, scope, sub, "model", "brier").get("n_plays")
        if n is None:
            continue
        for m, label in (("model", "Model"), (other, other_label)):
            b, ll, e = (_g(t, table, scope, sub, m, x) for x in ("brier", "log_loss", "ece"))
            lines.append(f"| {prefix}{sub} ({n:,}) | {label} | {_fmt(b)} | {_fmt(ll)} | "
                         f"{_fmt(e, 1, 100)} |")  # fmt: skip
        b, ll = (_g(t, table, scope, sub, f"model - {other}", x) for x in ("brier", "log_loss"))
        lines.append(f"| {prefix}{sub} | Model minus {other_label.lower()} | "
                     f"{_fmt(b, sign=True)} | {_fmt(ll, sign=True)} | |")  # fmt: skip
    return lines


def _rate_table(t: pl.DataFrame, table: str, scope: str, subsets: Sequence[str],
                nd: int = 3, scale: float = 1.0) -> list[str]:  # fmt: skip
    lines = ["| Slice | Rows | Observed (95%) | Predicted (mean) |", "|---|---|---|---|"]
    for sub in subsets:
        o = _g(t, table, scope, sub, "observed", "rate")
        p = _g(t, table, scope, sub, "model", "mean_predicted")
        if o["value"] is None:
            continue
        pv = "-" if p["value"] is None else f"{p['value'] * scale:.{nd}f}"
        lines.append(f"| {sub} | {o['n_plays']:,} | {_fmt(o, nd, scale)} | {pv} |")
    return lines


def _reliability_table(t: pl.DataFrame, table: str) -> list[str]:
    lines = ["| Predicted | Rows | Mean predicted | Observed (95%) |", "|---|---|---|---|"]
    for b in range(N_BINS):
        sub = f"{b / N_BINS:.1f}-{(b + 1) / N_BINS:.1f}"
        p = _g(t, table, "reliability", sub, "model", "mean_predicted")
        o = _g(t, table, "reliability", sub, "model", "observed")
        if p["value"] is None:
            continue
        lines.append(f"| {sub} | {p['n_plays']:,} | {p['value']:.3f} | {_fmt(o, 3)} |")
    return lines


def _windows(sums: list[dict]) -> str:
    """How often each training window was chosen on validation, e.g. 'all 12, last 10 5'."""
    count: dict[str, int] = {}
    for s in sums:
        w = s.get("window")
        key = "all earlier seasons" if w is None else f"the last {w}"
        count[key] = count.get(key, 0) + 1
    return ", ".join(f"{k} in {v}" for k, v in sorted(count.items()))


def _span(info: dict) -> str:
    return f"{info['seasons'][0]}-{info['seasons'][-1]}"


def _subsets(span: str) -> list[str]:
    return [span, *(name for name, _, _ in ERAS)]


def _conversion_md(t: pl.DataFrame, info: dict) -> list[str]:
    span, sums = _span(info), info["conversion"]
    ctx = sum(s["params"].get("feature_set") == "context" for s in sums)
    iso = sum(s["calibration"].startswith("isotonic") for s in sums)
    drops = info.get("drops", {}).get("conversion", {})
    dist = [f"4th and {x}" for x in ("1", "2-3", "4-6", "7+")]
    out = [
        "## 1. Go for it: P(convert)", "",
        f"- **Rows**: {info['n_rows']['conversion']:,} third- and fourth-down passes and runs "
        f"1999-2025 (dropped: {drops.get('not_a_pass_or_run', 0):,} other third/fourth-down rows "
        "- `no_play` penalty rows, punts, field goals, kneels, spikes). **Label**: a first down "
        "or the offense's touchdown with no interception or lost fumble; a defensive penalty "
        "that gives a first down on a play that counts is a conversion.",
        f"- **Model**: LightGBM, monotone in yards to go. Score/clock/spread context was chosen "
        f"on the validation season in {ctx} of {len(sums)} folds; isotonic calibration helped "
        f"out of sample in {iso} of {len(sums)}. Training window chosen on validation: "
        f"{_windows(sums)} folds. Lookup baseline = the training seasons' (all earlier "
        "seasons) rate per down and distance (1-15+).", "",
        "### Fourth downs (the decisions graded in G3)", "",
        *_metric_table(t, "conversion", "down 4", _subsets(span), "lookup", "Lookup"), "",
        "### Third downs", "",
        *_metric_table(t, "conversion", "down 3", [span], "lookup", "Lookup"), "",
        "### Fourth-down conversion rate by distance", "",
        *_rate_table(t, "conversion", "by distance",
                     [f"{d}, {s}" for d in dist for s in _subsets(span)]), "",
        "### Reliability (fourth downs, 10 bins)", "", *_reliability_table(t, "conversion"), "",
        "### Where the ball ends up (held-out check of the ball-spot tables, fourth downs)", "",
        "Spots are yards from the team's own goal line after a conversion (higher = better "
        "for the offense) and the opponent's `yardline_100` after a failure (its distance to "
        "the end zone; higher = better for the team that failed).", "",
        *_rate_table(t, "conversion", "after a conversion",
                     [f"touchdown share, {span}", f"new spot (yards from own goal), {span}"], 2),
        *_rate_table(t, "conversion", "after a failure",
                     [f"opponent touchdown share, {span}",
                      f"opponent spot (its yardline_100), {span}"], 2)[2:], "",
    ]  # fmt: skip
    return out


def _monotone_note(t: pl.DataFrame, span: str) -> str:
    bad = []
    for scope in _subsets(span):
        obs = [_g(t, "fieldgoal", "by distance", f"{b}, {scope}", "observed", "rate")["value"]
               for b, _, _ in FG_BANDS]  # fmt: skip
        obs = [v for v in obs if v is not None]
        if any(b > a for a, b in zip(obs, obs[1:], strict=False)):
            bad.append(scope)
    return ("Observed make rates fall with every distance band in every era."
            if not bad else f"Not monotone in: {', '.join(bad)}.")  # fmt: skip


def _fieldgoal_md(t: pl.DataFrame, info: dict) -> list[str]:
    span, sums = _span(info), info["fieldgoal"]
    iso = sum(s["calibration"].startswith("isotonic") for s in sums)
    abl = [s["ablation"]["weather"] for s in sums if s.get("ablation")]
    diffs = [a["val_logloss_with"] - a["val_logloss_without"] for a in abl]
    helped = sum(d < 0 for d in diffs)
    w = info["weather"]
    cov = ["| Season | Kicks | Outdoors | Weather read from the text | Weather still missing "
           "(imputed) | Surface unknown |", "|---|---|---|---|---|---|"]  # fmt: skip
    for r in w.iter_rows(named=True):
        if r["from_text"] or r["missing"] or r["surface_unknown"]:
            cov.append(
                f"| {r['season']} | {r['kicks']:,} | {r['outdoor']:,} | "
                f"{r['from_text']:,} | {r['missing']:,} | {r['surface_unknown']:,} |"
            )
    rest = w.filter((pl.col("from_text") == 0) & (pl.col("missing") == 0)
                    & (pl.col("surface_unknown") == 0)).height  # fmt: skip
    mr = info.get("miss_rule", {})
    mi, bl = mr.get("missed", {}), mr.get("blocked", {})
    trees = [s["params"]["n_estimators"] for s in sums]
    return [
        "## 2. Field goals: P(make)", "",
        f"- **Rows**: {info['n_rows']['fieldgoal']:,} field-goal attempts 1999-2025 (fakes are "
        "pass/run plays; `no_play` rows dropped). **Label**: made (missed and blocked = 0).",
        "- **Inputs** (known to the coach): distance = yardline + 18, indoors (dome or closed "
        "roof: 70 F, no wind), temperature, wind, grass vs turf, era flags. Missing outdoor "
        "weather is read from the play-by-play weather text, else imputed with the training "
        "seasons' outdoor median and flagged (`weather_missing`).",
        f"- **Model**: LightGBM, monotone (longer or windier never helps, warmer never hurts); "
        f"{min(trees)}-{max(trees)} trees; isotonic helped in {iso} of {len(sums)} folds; "
        f"training window chosen on validation: {_windows(sums)} folds. Lookup baseline = "
        "every earlier season's make rate per 5-yard distance band.",
        f"- **Do the conditions help?** Refit without the weather group on each validation "
        f"season: lower log loss with them in {helped} of {len(diffs)} folds (mean change "
        f"{np.mean(diffs) if diffs else 0:+.5f}; negative = they help).", "",
        *_metric_table(t, "fieldgoal", "all kicks", _subsets(span), "lookup", "Lookup"),
        *_metric_table(t, "fieldgoal", "50+ yards", [span], "lookup", "Lookup",
                       "50+ yards, ")[2:], "",
        "### Make rate by distance and era", "", _monotone_note(t, span), "",
        *_rate_table(t, "fieldgoal", "by distance",
                     [f"{b}, {s}" for b, _, _ in FG_BANDS for s in _subsets(span)]), "",
        "### Reliability (10 bins)", "", *_reliability_table(t, "fieldgoal"), "",
        "### Weather coverage (seasons with any gap)", "", *cov, "",
        f"The other {rest} seasons have every kick's weather and surface.", "",
        "### After a miss", "",
        "The opponent takes over at the spot of the kick (the hold, ~8 yards behind the line) "
        "or at its own 20 if that spot is inside the 20: opponent `yardline_100` = min(80, 92 - "
        f"yardline_100). Checked on the {info['seasons'][-1]} fold's training seasons (misses "
        f"followed by the opponent's snap in the same half): {mi.get('exact', 0):.1%} exactly, "
        f"{mi.get('within_1', 0):.1%} within one yard (the hold is 7-9 yards deep) of "
        f"{mi.get('n', 0):,} misses; blocked kicks {bl.get('within_1', 0):.0%} within one yard "
        f"of {bl.get('n', 0):,} (they can be returned).", "",
    ]  # fmt: skip


def _punt_md(t: pl.DataFrame, info: dict) -> list[str]:
    span, sums = _span(info), info["punt"]
    chosen: dict[str, int] = {}
    for s in sums:
        key = f"window {s['params']['window'] or 'all'}, bandwidth {s['params']['bandwidth']}"
        chosen[key] = chosen.get(key, 0) + 1
    zones = [f"all punts, {span}", *(z for z, _, _ in PUNT_ZONES)]
    other = info.get("drops", {}).get("punt", {}).get("other_result", 0)
    ls = ["| Method | Mean log score (5-yard bins; higher = better) |", "|---|---|"]
    for m in ("model", "raw same-yardline history", "model - raw"):
        ls.append(f"| {m} | {_fmt(_g(t, 'punt', 'log score', span, m, 'log_score'), 3,
                                     sign=m.endswith('raw'))} |")  # fmt: skip
    wp = ["| Punting yardline | Punts | Expected WP (95%) | WP of the real result | Expected "
          "minus real | Mean absolute gap |", "|---|---|---|---|---|---|"]  # fmt: skip
    for z in zones:
        e = _g(t, "punt", "expected WP", z, "model", "exp_wp")
        if e["value"] is None:
            continue
        r, b, a = (_g(t, "punt", "expected WP", z, "model", x) for x in
                   ("real_wp", "bias", "abs_err"))  # fmt: skip
        wp.append(f"| {z} | {e['n_plays']:,} | {_fmt(e, 3)} | {_fmt(r, 3)} | "
                  f"{_fmt(b, 4, sign=True)} | {_fmt(a, 3)} |")  # fmt: skip
    out = [
        "## 3. Punts: where the receiving team starts", "",
        f"- **Rows**: {info['n_rows']['punt']:,} punts 1999-2025 (fake punts are pass/run "
        f"plays; `no_play` rows dropped; {other} ended the half or the game and are left "
        "out). The result is the next snap: the receiving team's spot (touchback, fair "
        "catch, return, penalties included), the kicking team keeping the ball (muff, block, "
        "penalty), or a touchdown.",
        "- **Distribution**: the results of training punts from nearby yardlines (Gaussian "
        "kernel), from the last N training seasons; (N, bandwidth) chosen on each validation "
        "season: " + "; ".join(f"{k} in {v} folds" for k, v in sorted(chosen.items())) + ".",
        "- **Expected WP**: G1's WP of each resulting state (the receiving team's 1st and 10 "
        "at its spot, etc.), averaged over the distribution, vs the same WP of the result that "
        f"really happened ({info['punt_wp_n']:,} test punts with a full state).", "",
        *ls, "", "### Predicted vs observed by punting yardline", "",
    ]  # fmt: skip
    for what in ("starts at its own 20", "return touchdown", "kicking team keeps the ball"):
        out += [f"**{what.capitalize()}**", "", *_rate_table(t, "punt", what, zones, 4), ""]
    out += ["**Receiving team's spot (its yardline_100; receiving results only)**", "",
            *_rate_table(t, "punt", "receiving spot (its yardline_100)", zones, 1), "",
            "### Expected WP of punting vs the real result", "", *wp, ""]  # fmt: skip
    return out


def _tries_md(t: pl.DataFrame, info: dict) -> list[str]:
    span, sums = _span(info), info["tries"]
    first = [s["test_season"] for s in sums if "field goals" in s["rates"]["pat"]["source"]]
    lines = ["| Season | PAT rate used (95%; tries) | PAT observed | Two-point rate used "
             "(95%; tries) | Two-point observed |", "|---|---|---|---|---|"]  # fmt: skip
    for s in sums:
        y = str(s["test_season"])
        cells = []
        for kind in ("pat", "two_point"):
            m, o = (_g(t, "tries", kind, y, x, "rate") for x in ("model", "observed"))
            cells += [f"{_fmt(m, 3)}; {m.get('n_plays', 0):,}",
                      f"{_fmt(o, 3)}; {o.get('n_plays', 0):,}"]  # fmt: skip
        lines.append(f"| {y} | " + " | ".join(cells) + " |")
    return [
        "## 4. Extra points and two-point tries", "",
        "- **Rates for season S** (used by G3): extra points made / tried in the last 5 seasons "
        "before S under S's rule (from 2015 the kick is from the 15); two-point tries "
        "converted / tried in the last 5 seasons. "
        + (f"{', '.join(map(str, first))} (first season of the new rule) uses field goals of "
           f"{tr.FG_EQUIVALENT[0]}-{tr.FG_EQUIVALENT[1]} yards instead. " if first else "")
        + "Intervals: Jeffreys 95%. Naive = every earlier season pooled.", "",
        *_metric_table(t, "tries", "pat", _subsets(span), "naive", "Naive", "PAT, "),
        *_metric_table(t, "tries", "two_point", [span], "naive", "Naive", "two-point, ")[2:],
        "",
        *lines, "",
    ]  # fmt: skip


def render(t: pl.DataFrame, info: dict) -> str:
    span = _span(info)
    secs = {k: sum(s.get("seconds", 0.0) for s in info[k])
            for k in ("conversion", "fieldgoal", "punt", "tries")}  # fmt: skip
    out = [
        "# Fourth-down sub-models: walk-forward backtest (G2)", "",
        "Generated by `uv run twm decisions submodels-backtest` (code: "
        "src/twm/modules/decisions/{conversion,fieldgoal,punt,tries}.py; definitions: "
        "docs/decision_metrics.md). Each test season "
        f"{span} is scored by models that learned from earlier seasons only (tuned on the "
        "season before it); intervals are 95% season-block bootstrap (2,000 resamples) unless "
        "noted. Lower Brier and log loss = better; the calibration error (ECE) is the average "
        "gap, in percentage points, between predicted and observed rates over 10 bins. **Honesty "
        "note:** the option to train the conversion and field-goal models on only the last 10 or "
        "5 seasons was added after a first backtest had shown recent seasons under-predicted. "
        "Each fold still picks its window on its validation season only, but because the option "
        "itself was suggested by test-season results, the conversion and field-goal numbers below"
        " are slightly optimistic; 2026 is their first clean test.", "",
        *_conversion_md(t, info), *_fieldgoal_md(t, info), *_punt_md(t, info),
        *_tries_md(t, info),
        "## Runtime", "",
        "Fold fitting, one thread, summed over the folds (for the binary models: the "
        "chosen training window's fit; the command prints the wall time): " + ", ".join(
            f"{k} {v:.0f} s" for k, v in secs.items()) + ".", "",
    ]  # fmt: skip
    return "\n".join(out)


def write_report(rows: Mapping[str, pl.DataFrame], *, drops: Mapping[str, dict] | None = None,
                 seasons: Sequence[int] = sm.TEST_SEASONS, paths=None, models_root=None,
                 out_dirs=None, wp_dir=None, progress=print) -> tuple[Path, Path]:  # fmt: skip
    """Compute and write the markdown report and its CSV (every number in the report)."""
    t, info = compute(rows, seasons=seasons, models_root=models_root, out_dirs=out_dirs,
                      wp_dir=wp_dir, progress=progress)  # fmt: skip
    info["drops"] = dict(drops or {})
    md, csv = paths if paths is not None else report_paths()
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(render(t, info), encoding="utf-8")
    t.write_csv(csv, float_precision=6)
    return md, csv
