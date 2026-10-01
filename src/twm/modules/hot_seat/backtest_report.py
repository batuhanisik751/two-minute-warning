"""Hot-Seat H3b backtest report (markdown + CSVs). Verified labels -> ``reports/hot_seat/``
(committed); suggested labels -> ``data/hot_seat/provisional/`` (git-ignored), every page with
a PROVISIONAL banner."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import polars as pl

from twm.modules.hot_seat.backtest import TOP_K, TOP_WEEK, BacktestResult
from twm.modules.hot_seat.models import FEATURES

PROVISIONAL = (
    "> **PROVISIONAL.** These numbers use the research prefill of "
    "`data/manual/coach_departures.csv` (`prefill = suggested`), not labels verified by the "
    "owner (step H2). Do not quote them; rerun `uv run twm hotseat backtest --labels verified` "
    "after H2."
)
MODELS = ("logit", "hazard", "lgbm", "base_win_pct", "base_wins_vs_expected")
HEADLINE_SLICES = ("all", f"week_{TOP_WEEK:02d}", "end_of_season")
FILES = {
    "report": "backtest.md",
    "metrics": "backtest_metrics.csv",
    "differences": "backtest_differences.csv",
    "coefficients": "backtest_coefficients.csv",
    "penalties": "backtest_penalties.csv",
    "reliability": "backtest_reliability.csv",
    "groups": "backtest_groups.csv",
    "firings": "firings_per_season.csv",
    "predictions": "backtest_predictions.parquet",
}


def _table(head: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join("" if v is None else str(v) for v in r) + " |" for r in rows]
    return [*out, ""]


def _ci(r: Mapping[str, Any] | None, digits: int = 3) -> str:
    if r is None or r["value"] is None:
        return "-"
    if r["lo"] is None:
        return f"{r['value']:.{digits}f}"
    return f"{r['value']:.{digits}f} ({r['lo']:.{digits}f} to {r['hi']:.{digits}f})"


def _lookup(m: pl.DataFrame) -> dict[tuple, dict]:
    return {
        (r["variant"], r["model"], r["prob"], r["slice"], r["metric"]): r
        for r in m.iter_rows(named=True)
    }


def label_lines(counts: Mapping[str, int], mode: str) -> list[str]:
    c = counts
    return [
        "## Labels",
        "",
        f"Mode `{mode}`. Rules: docs/hot_seat.md (Labels). Departures joined to feature rows: "
        f"{c['departures_joined']} ({c['departures_unverified']} not verified by the owner; "
        f"{c['departures_date_imputed']} with a blank date imputed as the coach's last listed "
        f"game day; {c['departures_blank_type']} with a blank type).",
        "",
        f"- feature rows 2002-2025: {c['rows_in']:,}; dropped: {c['dropped_last_reg_week']:,} "
        f"weekly rows of the last regular-season week, {c['dropped_gone']:,} rows after the "
        f"coach's departure was announced, {c['dropped_blank_type']:,} rows of blank-type "
        "departures",
        f"- labelled rows: {c['rows_out']:,}, of which {c['interim_rows']:,} interim (never "
        f"trained on, reported apart) and {c['censored_rows']:,} censored (a non-firing "
        "departure in the window: y = 0)",
        f"- positive departures announced more than 30 days after the final game (y = 0): "
        f"{c['positive_outside_window']}",
        "",
    ]


def firings_lines(f: pl.DataFrame, test_first: int) -> list[str]:
    head = ["season", "positive departures", "of them fired in season",
            f"positives at week {TOP_WEEK}", "positives at season end", "censored",
            "interim"]  # fmt: skip
    rows = [
        [f"{r['season']}{'' if r['season'] >= test_first else ' (train only)'}",
         r["positive_departures"], r["fired_in_season"], r[f"positives_week_{TOP_WEEK}"],
         r["positives_end_of_season"], r["censored_coach_seasons"], r["interim_coach_seasons"]]
        for r in f.iter_rows(named=True)
    ]  # fmt: skip
    pos = f.filter(pl.col("season") >= test_first)["positive_departures"]
    return [
        "## Firings per season (coach-seasons; interims apart)",
        "",
        f"Test seasons: {pos.sum()} positive departures, {pos.min()}-{pos.max()} per season. "
        "A coach fired before week 12 is not among the week-12 positives (his rows end); one "
        "fired in season has no end-of-season row.",
        "",
        *_table(head, rows),
    ]


def headline_lines(m: pl.DataFrame, variant: str = "main") -> list[str]:
    look = _lookup(m)
    head = ["model", "as-of", "ROC-AUC", "PR-AUC", "Brier", f"top-{TOP_K} hit rate",
            "rows", "positives", "seasons"]  # fmt: skip
    rows = []
    for model in MODELS:
        for sl in HEADLINE_SLICES:
            auc = look.get((variant, model, "prob", sl, "roc_auc"))
            if auc is None:
                continue
            top = look.get((variant, model, "prob", sl, f"top{TOP_K}_hit_rate"))
            ap = look.get((variant, model, "prob", sl, "pr_auc"))
            br = look.get((variant, model, "prob", sl, "brier"))
            rows.append([model, sl, _ci(auc), _ci(ap), _ci(br, 4), _ci(top) if top else "",
                         auc["n_rows"], auc["n_pos"], auc["n_seasons"]])  # fmt: skip
    return [
        "## Walk-forward results (test seasons 2006-2025, interims excluded)",
        "",
        "Pooled over the test seasons; 95% intervals from resampling whole seasons (2,000 "
        f"resamples). Top-{TOP_K} hit rate: of the positives at that as-of, the share in their "
        f"season's top {TOP_K} by predicted risk. The model's own probabilities (no isotonic).",
        "",
        *_table(head, rows),
    ]


def difference_lines(d: pl.DataFrame) -> list[str]:
    sub = d.filter((pl.col("variant") == "main") & pl.col("slice").is_in(HEADLINE_SLICES))
    rows = [
        [f"{r['a']} - {r['b']}", r["slice"], r["metric"], _ci(
            {"value": r["diff"], "lo": r["lo"], "hi": r["hi"]}, 4)]
        for r in sub.iter_rows(named=True)
    ]  # fmt: skip
    return [
        "## Paired differences (main run; same season resamples for both)",
        "",
        "Brier: negative = the first is better; ROC-AUC / PR-AUC: positive = the first is better.",
        "",
        *_table(["a - b", "as-of", "metric", "difference (95%)"], rows),
    ]


def weekly_lines(m: pl.DataFrame) -> list[str]:
    look = _lookup(m)
    have = m.filter((pl.col("variant") == "main") & (pl.col("metric") == "roc_auc"))
    weeks = sorted({s for s in have["slice"].unique().to_list() if s.startswith("week_")})
    rows = []
    for sl in [*weeks, "end_of_season"]:
        row: list[Any] = [sl]
        for model in ("logit", "hazard", "base_win_pct"):
            row += [_ci(look.get(("main", model, "prob", sl, k)), 3 if k != "brier" else 4)
                    for k in ("roc_auc", "pr_auc", "brier")]  # fmt: skip
        auc = look.get(("main", "logit", "prob", sl, "roc_auc")) or {}
        rows.append([*row, auc.get("n_rows"), auc.get("n_pos"), auc.get("n_seasons")])
    head = ["as-of"] + [f"{m_} {k}" for m_ in ("logit", "hazard", "win%") for k in
                        ("AUC", "PR-AUC", "Brier")] + ["rows", "positives", "seasons"]  # fmt: skip
    return [
        "## By as-of week (main run)",
        "",
        "Week w = the Tuesday as-of after week w's games. The last regular-season week's weekly "
        "row is replaced by the end-of-season row, so week 17 exists only for the 18-week "
        "seasons (2021-2025).",
        "",
        *_table(head, rows),
    ]


def calibration_lines(m: pl.DataFrame, rel: pl.DataFrame) -> list[str]:
    look = _lookup(m)
    rows = []
    for model in ("logit", "hazard", "lgbm"):
        for prob in ("prob", "prob_iso"):
            for sl in ("all", "end_of_season"):
                r = look.get(("main", model, prob, sl, "brier"))
                if r is not None:
                    rows.append([model, "own" if prob == "prob" else "isotonic", sl, _ci(r, 4)])
    eos = rel.filter((pl.col("slice") == "end_of_season") & (pl.col("prob") == "prob"))
    bins = []
    for model in ("logit", "hazard"):
        for r in eos.filter((pl.col("model") == model) & (pl.col("n") > 0)).iter_rows(named=True):
            bins.append([model, f"{r['lo']:.1f}-{r['hi']:.1f}", r["n"], r["n_pos"],
                         f"{r['mean_pred']:.3f}", f"{r['observed']:.3f}"])  # fmt: skip
    return [
        "## Calibration",
        "",
        "The models' own probabilities are primary (a logistic regression is calibrated on its "
        "training seasons; an isotonic map fit on one validation season of about 6 firings is "
        "thin). The harness's isotonic version is shown for comparison (not for the hazard "
        "model, whose season risk is a product of interval hazards).",
        "",
        *_table(["model", "probability", "as-of", "Brier (95%)"], rows),
        "Reliability at season end (own probabilities, 10-point bins, empty bins left out):",
        "",
        *_table(["model", "bin", "rows", "positives", "mean predicted", "observed"], bins),
    ]


def sensitivity_lines(m: pl.DataFrame) -> list[str]:
    look = _lookup(m)
    rows = []
    for variant in ("main", "censored_dropped", "rup_positive"):
        for model in ("logit", "hazard", "base_win_pct"):
            for sl in ("all", "end_of_season"):
                auc = look.get((variant, model, "prob", sl, "roc_auc"))
                if auc is None:
                    continue
                rows.append([variant, model, sl, _ci(auc),
                             _ci(look.get((variant, model, "prob", sl, "pr_auc"))),
                             _ci(look.get((variant, model, "prob", sl, "brier")), 4),
                             auc["n_rows"], auc["n_pos"]])  # fmt: skip
    return [
        "## Sensitivity runs",
        "",
        "`censored_dropped`: rows of coaches who left for a non-firing reason inside the window "
        "are removed (train and test). `rup_positive`: `resigned_under_pressure` counts as a "
        "firing (decision point 4; the main run keeps it censored, owner 2026-10-01).",
        "",
        *_table(
            ["run", "model", "as-of", "ROC-AUC", "PR-AUC", "Brier", "rows", "positives"], rows
        ),  # fmt: skip
    ]


def group_lines(g: pl.DataFrame) -> list[str]:
    sub = g.filter((pl.col("variant") == "main") & pl.col("model").is_in(["logit", "hazard"]))
    rows = [[r["model"], r["slice"], r["group"], r["n_rows"], r["coach_seasons"], r["n_pos"],
             f"{r['mean_prob']:.3f}"] for r in sub.iter_rows(named=True)]  # fmt: skip
    return [
        "## Interims and censored rows (main run)",
        "",
        "Interim coaches never train; their rows are scored by the fold model and kept out of "
        "every metric above. Censored = the coach left for a non-firing reason inside the "
        "window (y = 0 in the main run).",
        "",
        *_table(
            [
                "model",
                "as-of",
                "group",
                "rows",
                "coach-seasons",
                "positives",
                "mean predicted risk",
            ],
            rows,
        ),  # fmt: skip
    ]


def coefficient_lines(s: pl.DataFrame) -> list[str]:
    out = [
        "## Coefficients (main run, standardized: log-odds per standard deviation)",
        "",
        "Last fold = trained on 2002-2024 (test 2025). Mean and range over the 20 folds; "
        "same sign = share of folds with the last fold's sign. `missingindicator_x` = x was "
        "missing (decision grades before 2006, EPA trend in weeks 2-4). The hazard model adds "
        "`final_interval` (the end-of-season row's interval). L2 penalty; C per fold below "
        "(## Penalty).",
        "",
    ]
    for model in ("logit", "hazard"):
        sub = s.filter((pl.col("variant") == "main") & (pl.col("model") == model))
        sub = sub.sort(pl.col("last_fold").abs(), descending=True)
        rows = [[r["term"], f"{r['last_fold']:+.3f}", f"{r['mean']:+.3f}",
                 f"{r['min']:+.3f} to {r['max']:+.3f}", f"{r['same_sign_share']:.2f}"]
                for r in sub.iter_rows(named=True)]  # fmt: skip
        out += [f"**{model}**", ""]
        out += _table(["term", "last fold", "mean", "range", "same sign"], rows)
    return out


def penalty_lines(p: pl.DataFrame) -> list[str]:
    """The chosen C per fold (main run, final models) and how often each C won."""
    out = [
        "## Penalty (C of the L2 logistic models)",
        "",
        "Chosen inside each fit by an inner walk-forward: for each C, fit on the training "
        "seasons before v, log loss on v, summed over the last 4 training seasons v (fewer in "
        "the earliest folds; the default C below 2); the smallest sum wins and is refit on every "
        "training season. No test-season row enters. Final models (every training season); "
        "the tuning models (validation season held out) are in the CSV.",
        "",
    ]
    final = p.filter((pl.col("variant") == "main") & (pl.col("fit") == "final") & pl.col("chosen"))
    seasons = sorted(final.get_column("test_season").unique().to_list())
    rows = []
    for model in final.get_column("model").unique(maintain_order=True).to_list():
        sub = final.filter(pl.col("model") == model)
        by = {r["test_season"]: r["C"] for r in sub.iter_rows(named=True)}
        rows.append([model, *(f"{by[s]:g}" if s in by else "-" for s in seasons)])
    return [*out, *_table(["model", *map(str, seasons)], rows)]


def report_lines(res: BacktestResult, counts: Mapping[str, int], mode: str) -> list[str]:
    head = ["# Hot-Seat Meter: walk-forward backtest (step H3b)", ""]
    if mode != "verified":
        head += [PROVISIONAL, ""]
    head += [
        f"Generated by `uv run twm hotseat backtest --labels {mode}`. PROJECT_SPEC 8.5; label "
        "rules and model choices in docs/hot_seat.md. Test seasons 2006-2025, each trained on "
        "every earlier season from 2002 (the walk-forward harness refuses test-season rows).",
        "",
        f"Models: penalized logistic regression on the season label (`logit`), discrete-time "
        f"hazard (`hazard`, season risk = 1 - prod(1 - h)), LightGBM (`lgbm`, challenger), "
        f"baselines win% to date (`base_win_pct`) and wins minus market-expected wins alone "
        f"(`base_wins_vs_expected`). Features ({len(FEATURES)}): {', '.join(FEATURES)}.",
        "",
        f"**{res.lgbm_text}.**",
        "",
    ]
    return [
        *head,
        *label_lines(counts, mode),
        *firings_lines(res.firings, 2006),
        *headline_lines(res.metrics),
        *difference_lines(res.differences),
        *weekly_lines(res.metrics),
        *calibration_lines(res.metrics, res.reliability),
        *sensitivity_lines(res.metrics),
        *group_lines(res.groups),
        *coefficient_lines(res.coefficient_summary),
        *penalty_lines(res.penalties),
    ]


def write_outputs(
    res: BacktestResult, counts: Mapping[str, int], mode: str, out_dir: Path, data_dir: Path
) -> dict[str, Path]:
    """The report and its tables (CSV) in ``out_dir``, the predictions (Parquet, git-ignored
    data) in ``data_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    paths = {k: (data_dir if k == "predictions" else out_dir) / v for k, v in FILES.items()}
    text = "\n".join(report_lines(res, counts, mode)).rstrip() + "\n"
    paths["report"].write_text(text, encoding="utf-8")
    tables = {
        "metrics": res.metrics, "differences": res.differences,
        "coefficients": res.coefficient_summary, "penalties": res.penalties,
        "reliability": res.reliability,
        "groups": res.groups, "firings": res.firings,
    }  # fmt: skip
    for key, df in tables.items():
        df.write_csv(paths[key], float_precision=6)
    res.predictions.write_parquet(paths["predictions"], compression="zstd", statistics=False)
    return paths
