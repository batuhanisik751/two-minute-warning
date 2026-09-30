"""reports/streamer/backtest.md (+ .csv): the streamer's walk-forward results per position
(S1d). Built from :class:`twm.modules.streamer.backtest.BacktestRun`; deterministic apart from
the one "Created" line (fixed bootstrap seed, deterministic models)."""

from __future__ import annotations

import csv
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from twm.backtest.metrics import Interval, calibration_fixed_bins
from twm.backtest.walkforward import SCORE, season_span
from twm.modules.streamer.backtest import (
    BacktestRun,
    Graded,
    constant_brier,
    grade,
    model_brier,
)
from twm.modules.streamer.models import BASELINES, KEYS, LABEL, MODELS

CSV_COLUMNS = (
    "position", "method", "train_on", "scope", "seasons", "key", "metric", "value", "lo", "hi",
    "share_above_zero", "n_groups", "n_rows", "n_pos",
)  # fmt: skip
METRIC_TITLES = {1: "#1 pick started", 3: "precision@3", 5: "precision@5"}
TITLES = {
    "logit": "logistic regression",
    "lgbm": "LightGBM",
    "baseline_last_points": "baseline: last game's points",
    "baseline_ppg": "baseline: points per game",
    "baseline_opponent": "baseline: next opponent",
}
TOP_FEATURES = 3


def _p(x: float | None, digits: int = 1) -> str:
    return "-" if x is None else f"{100 * x:.{digits}f}%"


def _ci(iv: Interval) -> str:
    if iv.value is None:
        return "-"
    if iv.lo is None or iv.hi is None:
        return _p(iv.value)
    return f"{_p(iv.value)} ({_p(iv.lo)} to {_p(iv.hi)})"


def _dci(iv: Interval) -> str:
    def pts(x: float | None) -> str:
        return "-" if x is None else f"{100 * x:+.1f}"

    if iv.value is None:
        return "-"
    return f"{pts(iv.value)} ({pts(iv.lo)} to {pts(iv.hi)})"


def _table(header: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return out + ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]


def _num(x: float | None) -> str:
    return "" if x is None else f"{x:.6f}"


def _row(
    position: str, method: str, train_on: str, scope: str, seasons: str, key: object,
    metric: str, value: float | None, iv: Interval | None = None,
    n_groups: int | None = None, n_rows: int | None = None, n_pos: int | None = None,
) -> dict[str, object]:  # fmt: skip
    return {
        "position": position, "method": method, "train_on": train_on, "scope": scope,
        "seasons": seasons, "key": key, "metric": metric, "value": _num(value),
        "lo": _num(iv.lo) if iv else "", "hi": _num(iv.hi) if iv else "",
        "share_above_zero": _num(iv.share_above_zero) if iv else "",
        "n_groups": "" if n_groups is None else n_groups,
        "n_rows": "" if n_rows is None else n_rows, "n_pos": "" if n_pos is None else n_pos,
    }  # fmt: skip


@dataclass(frozen=True)
class Verdict:
    position: str
    winner: str | None  # the model kept: LightGBM only if it beats logit on pooled P@5
    beats: dict[str, bool]  # baseline -> winner's pooled P@5 is higher
    clear: dict[str, bool]  # baseline -> the paired 95% interval is above 0
    text: str


def verdict(graded: dict[str, Graded], position: str, seasons: Sequence[int]) -> Verdict:
    models = [m for m in MODELS if m in graded]
    if not models:
        return Verdict(position, None, {}, {}, f"{position}: no model was run")
    p5 = {m: graded[m].interval(5, seasons).value or 0.0 for m in models}
    winner = "lgbm" if "lgbm" in p5 and p5["lgbm"] > p5.get("logit", -1.0) else models[0]
    beats, clear, parts = {}, {}, []
    for b in (b for b in BASELINES if b in graded):
        d = graded[winner].diff(graded[b], 5, seasons)
        beats[b] = (d.value or 0.0) > 0
        clear[b] = d.lo is not None and d.lo > 0
        parts.append(f"{TITLES[b].removeprefix('baseline: ')} {_dci(d)}")
    if beats and all(clear.values()):
        head = "beats every baseline, with season-block intervals above zero"
    elif beats and all(beats.values()):
        head = "is ahead of every baseline, but not clearly (an interval includes zero)"
    else:
        head = "does NOT beat every baseline"
    text = (
        f"**{position}**: the kept model, {TITLES[winner]} (precision@5 {_p(p5[winner])}), "
        f"{head}. Model minus baseline, precision@5 points: " + "; ".join(parts) + "."
    )
    return Verdict(position, winner, beats, clear, text)


def _headline(
    run: BacktestRun, position: str, graded: dict[str, Graded], csv_rows: list
) -> list[str]:
    tests = run.test_seasons
    span = season_span(tests)
    rows = []
    for m in (*MODELS, *BASELINES):
        if m not in graded:
            continue
        g = graded[m]
        cells = []
        for k in (3, 5, 1):
            iv = g.interval(k, tests)
            lists = g.seasons(tests)
            cells.append(_ci(iv))
            csv_rows.append(
                _row(position, m, run.train_on, "pooled", span, "", f"p_at_{k}", iv.value, iv,
                     lists.height, int(lists["n_rows"].sum()), int(lists["n_pos"].sum()))
            )  # fmt: skip
        rows.append([TITLES[m], *cells])
    lists = graded[next(iter(graded))].seasons(tests)
    n_rows, n_pos = int(lists["n_rows"].sum()), int(lists["n_pos"].sum())
    short5 = int(lists["short_5"].sum())
    out = [
        f"### {position}",
        "",
        f"{lists.height} weekly lists ({span}), {n_rows} pool rows, {n_pos} of them started "
        f"(base rate {_p(n_pos / n_rows if n_rows else None)}); average list "
        f"{n_rows / max(lists.height, 1):.1f} rows, {short5} lists shorter than 5.",
        "",
        *_table(["method", "precision@3", "precision@5", "#1 pick started"], rows),
        "",
    ]
    return out + (_team_kicker_line(run, graded) if position == "K" else [])


def _team_kicker_line(run: BacktestRun, graded: dict[str, Graded]) -> list[str]:
    """K lists include practice-squad and camp kickers (the pool rule); few of them kick."""
    tk = run.graded.filter(pl.col("position") == "K").select(*KEYS, "is_team_kicker")
    rates = (
        tk.join(run.graded.select(*KEYS, LABEL), on=list(KEYS))
        .group_by("is_team_kicker")
        .agg(pl.col(LABEL).mean())
    )
    by = {r["is_team_kicker"]: r[LABEL] for r in rates.iter_rows(named=True)}
    parts = []
    for m, g in graded.items():
        top = g.rows.filter(pl.col("rank") <= 5).join(tk, on=list(KEYS), how="left")
        share = 1.0 - float(top.get_column("is_team_kicker").fill_null(False).mean() or 0.0)
        parts.append(f"{TITLES[m].removeprefix('baseline: ')} {_p(share, 0)}")
    return [
        "Pool kickers who did not kick in their team's latest game (practice squad, camp, "
        f"free agents) started {_p(by.get(False))} of the time, team kickers {_p(by.get(True))}. "
        "Share of such kickers among each method's top 5: " + ", ".join(parts) + ".",
        "",
    ]


def _diff_section(
    run: BacktestRun, position: str, graded: dict[str, Graded], csv_rows: list
) -> list[str]:
    tests, span = run.test_seasons, season_span(run.test_seasons)
    rows = []
    for m in (m for m in MODELS if m in graded):
        for b in (b for b in BASELINES if b in graded):
            cells = []
            for k in (3, 5, 1):
                d = graded[m].diff(graded[b], k, tests)
                cells.append(f"{_dci(d)}, {_p(d.share_above_zero, 0)}")
                csv_rows.append(
                    _row(position, m, run.train_on, "diff", span, b, f"p_at_{k}", d.value, d,
                         d.n_groups)
                )  # fmt: skip
            rows.append([TITLES[m], TITLES[b].removeprefix("baseline: "), *cells])
    if not rows:
        return []
    return [
        f"#### {position}: model minus baseline (percentage points, 95% interval, share of "
        "resamples above zero)",
        "",
        *_table(["model", "baseline", "precision@3", "precision@5", "#1 pick started"], rows),
        "",
    ]


def _season_section(
    run: BacktestRun, position: str, graded: dict[str, Graded], csv_rows: list
) -> list[str]:
    methods = [m for m in (*MODELS, *BASELINES) if m in graded]
    ref = graded[methods[0]].lists
    rows = []
    for s in run.test_seasons:
        lists = ref.filter(pl.col("season") == s)
        n_rows, n_pos = int(lists["n_rows"].sum()), int(lists["n_pos"].sum())
        cells = []
        for m in methods:
            v = graded[m].seasons([s]).get_column("p_at_5").mean()
            cells.append(_p(v, 0))  # type: ignore[arg-type]
            csv_rows.append(
                _row(position, m, run.train_on, "season", str(s), "", "p_at_5", v,  # type: ignore[arg-type]
                     None, lists.height, n_rows, n_pos)
            )  # fmt: skip
        rows.append([s, lists.height, _p(n_pos / n_rows if n_rows else None, 0), *cells])
    names = (TITLES[m].removeprefix("baseline: ") for m in methods)
    head = ["season", "lists", "base rate", *names]
    return [f"#### {position}: precision@5 by test season", "", *_table(head, rows), ""]


def _calibration_section(
    run: BacktestRun, position: str, graded: dict[str, Graded], kept: str | None, csv_rows: list
) -> list[str]:
    tests, span = run.test_seasons, season_span(run.test_seasons)
    base = constant_brier(run, position, tests)
    lines = [f"#### {position}: probabilities", ""]
    csv_rows.append(_row(position, "constant", run.train_on, "brier", span, "", "brier", base))
    parts = [f"constant forecast (the training seasons' start rate) {base:.4f}" if base else ""]
    for m in (m for m in MODELS if m in graded):
        b = model_brier(graded[m])
        csv_rows.append(_row(position, m, run.train_on, "brier", span, "", "brier", b))
        parts.append(f"{TITLES[m]} {b:.4f}" if b is not None else f"{TITLES[m]} -")
    lines += ["Brier score (lower is better): " + "; ".join(p for p in parts if p) + ".", ""]
    if kept is None:
        return lines
    bins = calibration_fixed_bins(graded[kept].rows, prob=SCORE, label="y", n_bins=10)
    rows = []
    for r in bins.filter(pl.col("n") > 0).iter_rows(named=True):
        rows.append([f"{_p(r['lo'], 0)}-{_p(r['hi'], 0)}", r["n"], _p(r["mean_pred"]),
                     _p(r["observed"])])  # fmt: skip
        csv_rows.append(
            _row(position, kept, run.train_on, "calibration", span, f"{r['lo']:.1f}-{r['hi']:.1f}",
                 "observed", r["observed"], None, None, r["n"], r["n_pos"])
        )  # fmt: skip
    empty = bins.filter(pl.col("n") == 0).height
    lines += [
        f"Calibration of the kept model ({TITLES[kept]}), fixed 10-point bins"
        + (f" ({empty} empty bins not shown)" if empty else "") + ":",
        "",
        *_table(["predicted", "rows", "mean predicted", "observed start rate"], rows),
        "",
    ]  # fmt: skip
    return lines


def _params_text(params: dict) -> str:
    keep = ("C", "l1_ratio", "num_leaves", "min_child_samples", "colsample_bytree", "n_estimators")
    return ", ".join(f"{k}={params[k]:g}" for k in keep if k in params)


def _folds_section(run: BacktestRun, position: str) -> list[str]:
    lines = []
    for m in (m for m in MODELS if (m, position) in run.runs):
        mr = run.run(m, position)
        rows = []
        for fr in mr.folds:
            f = fr.fold
            top = ", ".join(f"{n} {_p(v, 0)}" for n, v in fr.importance[:TOP_FEATURES])
            constant = not any(v > 0 for _, v in fr.importance)
            flag = " (constant model: ties, ranked by id)" if constant else ""
            flag = " (dominant)" if fr.dominant else flag
            rows.append([
                f.test_season, season_span(f.train_seasons), f.val_season or "thin",
                f"{fr.n_train} ({fr.n_train_pos})", _params_text(fr.params), top + flag,
            ])  # fmt: skip
        kind = mr.folds[0].importance_kind if mr.folds else ""
        lines += [
            f"#### {position}: {TITLES[m]} folds (importance: {kind})",
            "",
            *_table(["test", "trained on", "validation", "training rows (starts)",
                     "chosen settings", f"top {TOP_FEATURES} features"], rows),
            "",
        ]  # fmt: skip
    return lines


def _sensitivity_section(run: BacktestRun, alt: BacktestRun, csv_rows: list) -> list[str]:
    tests, span = run.test_seasons, season_span(run.test_seasons)
    rows = []
    for pos in run.positions:
        for m in (m for m in MODELS if (m, pos) in run.runs and (m, pos) in alt.runs):
            a, b = grade(alt, m, pos), grade(run, m, pos)
            cells = []
            for k in (3, 5, 1):
                iv, d = a.interval(k, tests), a.diff(b, k, tests)
                cells.append(f"{_p(iv.value)} ({_dci(d)})")
                csv_rows.append(
                    _row(pos, m, alt.train_on, "pooled", span, "", f"p_at_{k}", iv.value, iv,
                         iv.n_groups)
                )  # fmt: skip
                csv_rows.append(
                    _row(pos, m, alt.train_on, "diff", span, f"{m} trained on {run.train_on}",
                         f"p_at_{k}", d.value, d, d.n_groups)
                )  # fmt: skip
            rows.append([pos, TITLES[m], *cells])
    return [
        f"## Sensitivity: models trained on every {alt.train_on} row",
        "",
        f"The same walk-forward, but each model also learns from the kickers and D/STs that "
        f"were NOT in the pool (rostered in most leagues): {alt.source.height:,} final rows "
        f"instead of {run.source.height:,} (all seasons, both positions). Graded on the same "
        f"pool lists; in brackets the change against training on "
        f"{run.train_on} rows only (percentage points, 95% season-block interval).",
        "",
        *_table(["position", "model", "precision@3", "precision@5", "#1 pick started"], rows),
        "",
    ]


@dataclass(frozen=True)
class BacktestReport:
    markdown: str
    csv_rows: list[dict[str, object]]
    summary: list[str]
    verdicts: dict[str, Verdict]


def write_report(report: BacktestReport, md_path: Path) -> Path:
    """Write the markdown and ``<same name>.csv`` next to it; return the CSV path."""
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(report.markdown, encoding="utf-8")
    csv_path = md_path.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(CSV_COLUMNS), lineterminator="\n")
        w.writeheader()
        w.writerows(report.csv_rows)
    return csv_path


def _intro(run: BacktestRun, created: str, command: str, dataset_name: str) -> list[str]:
    span = season_span(run.test_seasons)
    thin = run.test_seasons[0]
    return [
        "# K and D/ST streamer: walk-forward backtest",
        "",
        f"{created} Command: `{command}`; dataset `{dataset_name}`; test seasons {span}; "
        f"models trained on {run.train_on} rows. docs/streamer.md explains it in plain words.",
        "",
        "- **What is graded**: at every regular-season Tuesday as-of of a test season, each "
        "method ranks the pool of its position (kickers or D/STs probably on waivers, "
        "reports/streamer/pool_labels.md). A pick is a hit when he finishes in the top 12 of "
        "the position the next week (`y_start`); byes and the last week have no label and are "
        "left out. precision@k = hits among the top k of a list (a list shorter than k divides "
        "by its length); `#1 pick started` = precision@1. Pooled = the mean over lists.",
        "- **Walk-forward**: the model graded on season S learns only from seasons before S "
        "AND only from labels public by S's first Tuesday as-of (the training cutoff; any later "
        "training row is refused), is tuned on the last training season (pooled precision@5) "
        "and calibrated there (isotonic); one model per position; no refit during the season. "
        f"{thin} is a thin fold: trained on one season, no tuning, cross-fitted calibration.",
        "- **Intervals**: 95% bootstrap intervals that resample whole seasons (2,000 draws); "
        "differences are paired (the same seasons drawn for both methods).",
        "- **Model choice**: LightGBM is kept only if it beats the logistic regression on "
        "pooled precision@5 over all test seasons; otherwise the logistic regression is kept.",
        "- No betting lines, no weather, no injury news: the as-of is Tuesday, before any of "
        "that exists for the next game (PROJECT_SPEC 6.1-6.4).",
        "",
    ]


def build_report(
    run: BacktestRun,
    *,
    alt: BacktestRun | None = None,
    created: str = "",
    command: str = "uv run twm streamer backtest",
    dataset_name: str = "data/streamer/dataset.parquet",
) -> BacktestReport:
    csv_rows: list[dict[str, object]] = []
    graded = {
        p: {m: grade(run, m, p) for m in run.methods if (m, p) in run.runs} for p in run.positions
    }
    verdicts = {p: verdict(graded[p], p, run.test_seasons) for p in run.positions}
    lines = _intro(run, created, command, dataset_name)
    lines += ["## Verdict", ""] + [f"- {verdicts[p].text}" for p in run.positions] + [""]
    lines += ["## Results", ""]
    for p in run.positions:
        lines += _headline(run, p, graded[p], csv_rows)
        lines += _diff_section(run, p, graded[p], csv_rows)
    lines += ["## By season", ""]
    for p in run.positions:
        lines += _season_section(run, p, graded[p], csv_rows)
    lines += ["## Probabilities", ""]
    for p in run.positions:
        lines += _calibration_section(run, p, graded[p], verdicts[p].winner, csv_rows)
    if alt is not None:
        lines += _sensitivity_section(run, alt, csv_rows)
    lines += ["## Folds", ""]
    for p in run.positions:
        lines += _folds_section(run, p)
    summary = [verdicts[p].text.replace("**", "") for p in run.positions]
    return BacktestReport("\n".join(lines).rstrip() + "\n", csv_rows, summary, verdicts)
