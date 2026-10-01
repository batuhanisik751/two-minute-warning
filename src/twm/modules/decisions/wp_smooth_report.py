"""The smoothness section of reports/decisions/wp_backtest.md (+ csv rows; G1b).

Per fold: the smoothness metrics (:mod:`wp_smooth`) of the model in use, next to G1's fold
model before G1b (frozen in reports/decisions/wp_g1_before.csv with its test-season log loss
and Brier; those model files are kept under models/decisions/ and named there); the
candidates compared on the validation seasons (:mod:`wp_select`); nflfastR's stored wp /
vegas_wp read on the same grid (references only).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import polars as pl

from twm.modules.decisions import wp_select as ws
from twm.modules.decisions import wp_smooth as wsm

SHOWN = ("score_step_h1", "score_step_h2", "curvature", "halftime_possession",
         "monotone_violations", "yard_step", "possession_2min")  # fmt: skip
HEAD = {"score_step_h1": "1-pt step, 1st half", "score_step_h2": "1-pt step, 2nd half",
        "curvature": "Curvature", "halftime_possession": "Halftime possession",
        "monotone_violations": "Monotone violations", "yard_step": "1-yd step",
        "possession_2min": "Ball 30-120 s before half (mean)"}  # fmt: skip


def g1_csv() -> Path:
    from twm.config import ROOT

    return ROOT / "reports" / "decisions" / "wp_g1_before.csv"


def _row(scope: str, subset: str, method: str, metric: str, value: Any, n: int = 0) -> dict:
    return {"table": "smoothness", "scope": scope, "subset": subset, "method": method,
            "metric": metric, "value": None if value is None else float(value), "lo": None,
            "hi": None, "n_plays": n, "n_blocks": 0}  # fmt: skip


def rows(summaries: Sequence[dict], states_rows: pl.DataFrame | None, *,
         select_dir: Path | None = None, before: Path | None = None) -> list[dict]:  # fmt: skip
    """Every number of the section, in the report CSV's schema."""
    out = [_row("limit", "-", "threshold", k, v) for k, v in wsm.THRESHOLDS.items()]
    for s in summaries:
        for k in SHOWN:
            if "smoothness" in s:
                out.append(_row("fold", str(s["test_season"]), "own", k, s["smoothness"][k]))
    b = before if before is not None else g1_csv()
    if b.exists():
        frozen = pl.read_csv(b)
        g1 = frozen.filter(pl.col("metric").is_in(SHOWN))
        out += [_row("fold", str(r["season"]), "g1_before", r["metric"], r["value"])
                for r in g1.iter_rows(named=True)]  # fmt: skip
        seasons = [s["test_season"] for s in summaries]
        w = frozen.filter(pl.col("season").is_in(seasons)).pivot(
            on="metric", index="season", values="value").drop_nulls("log_loss")  # fmt: skip
        if w.height:
            n = w.get_column("n_plays")
            for k in ("log_loss", "brier"):
                v = float((w.get_column(k) * n).sum() / n.sum())
                out.append(_row("cost", "pooled", "g1_before", k, v, int(n.sum())))
    results = ws.load_results(select_dir)
    for r in ws.summarize(results):
        for k in ("log_loss", "brier", "worst_ratio", "meets", "seconds", *SHOWN):
            out.append(_row("validation", "2004-2005", r["candidate"], k, r[k], r["n"]))
    if ws.summarize(results):
        out.append(_row("validation", "2004-2005", ws.choose(results), "chosen_by_rule", 1))
    if states_rows is not None:
        for span, lo, hi in (("1999-2005", 1999, 2005), ("2006-2025", 2006, 2025)):
            sub = states_rows.filter(pl.col("season").is_between(lo, hi))
            if sub.height:
                v, low, high, n = wsm.empirical_possession_2min(sub)
                out += [_row("empirical", span, "what happened", k, x, n)
                        for k, x in (("possession_2min", v), ("possession_2min_lo", low),
                                     ("possession_2min_hi", high))]  # fmt: skip
        ref = states_rows.filter(pl.col("season").is_between(2006, 2025))
        for col, method in (("vegas_wp", "nflfastr_vegas_wp"), ("wp", "nflfastr_wp")):
            r = wsm.reference(ref, col)
            for k in ("score_step_h1", "score_step_h2", "curvature", "halftime_possession",
                      "halftime_possession_mean"):  # fmt: skip
                out.append(_row("reference", "2006-2025", method, k, r[k], r["n_plays"]))
    return out


def _val(t: pl.DataFrame, scope: str, subset: str, method: str, metric: str) -> float | None:
    hit = t.filter((pl.col("scope") == scope) & (pl.col("subset") == subset)
                   & (pl.col("method") == method) & (pl.col("metric") == metric))  # fmt: skip
    return hit.get_column("value")[0] if hit.height else None


def _n(v: float | None, nd: int = 1) -> str:
    if v is None or v != v:  # None or NaN
        return "-"
    return f"{v:.{nd}f}" if nd else f"{v:.0f}"


def _m(k: str, v: float | None) -> str:
    """A metric value: counts without decimals."""
    return _n(v, 0 if k == "monotone_violations" else 1)


def _limits_line() -> str:
    lim = wsm.THRESHOLDS
    return ", ".join(f"{HEAD[k].lower()} <= {lim[k]:g}" if lim[k] else f"no {HEAD[k].lower()}"
                     for k in lim)  # fmt: skip


def markdown(t: pl.DataFrame, summaries: Sequence[dict]) -> list[str]:
    """The report section (``t`` = the long table; its 'smoothness' rows are used)."""
    full = t.filter(pl.col("table") == "metrics")
    span = f"{summaries[0]['test_season']}-{summaries[-1]['test_season']}" if summaries else "-"
    t = t.filter(pl.col("table") == "smoothness")
    out = ["## Smoothness (G1b): can the model price hypothetical states?", "",
           "Fourth-down and two-point grades are differences between the WPs of hypothetical "
           "states, so the model must also be locally sensible. Metrics on a fixed grid of "
           "synthetic states (WP points; definitions and why each limit: "
           "docs/decision_metrics.md, 'WP smoothness'). Limits: " + _limits_line() + ".",
           ""]  # fmt: skip
    v = t.filter((pl.col("scope") == "validation") & (pl.col("metric") == "log_loss"))
    val = sorted(set(v.get_column("method")))
    if val:
        cands = sorted(val, key=lambda c: _val(t, "validation", "2004-2005", c, "log_loss"))
        out += ["### Choosing the model (validation seasons 2004-2005 only)", "",
                "Each candidate ran the walk-forward folds of 2004 and 2005 (trained on "
                "1999-2003 / 1999-2004; no test or graded season). Rule, fixed before any "
                "candidate was fitted: the lowest pooled log loss among the candidates whose "
                "two fold models meet every limit (else the smallest worst metric/limit ratio). "
                "Worst value over the two folds:", "",
                "| Candidate | What | Log loss | " + " | ".join(HEAD[k] for k in SHOWN)
                + " | Meets limits | Fit seconds |",
                "|---|---|---|" + "---|" * len(SHOWN) + "---|---|"]  # fmt: skip
        for c in cands:
            v = {k: _val(t, "validation", "2004-2005", c, k) for k in
                 ("log_loss", "meets", "seconds", *SHOWN)}  # fmt: skip
            desc = ws.CANDIDATES[c].description if c in ws.CANDIDATES else "-"
            mark = " (chosen)" if c == ws.CHOSEN else ""
            meets = "yes" if v["meets"] else "no"
            out.append(f"| {c}{mark} | {desc} | {v['log_loss']:.5f} | "
                       + " | ".join(_m(k, v[k]) for k in SHOWN)
                       + f" | {meets} | {_n(v['seconds'], 0)} |")  # fmt: skip
        hit = t.filter((pl.col("scope") == "validation") & (pl.col("metric") == "chosen_by_rule"))
        rule = hit.get_column("method")[0] if hit.height else None
        out += ["", f"In use: **{ws.CHOSEN}** ({ws.chosen().description}); the rule picks "
                f"**{rule}** from the saved results.", ""]  # fmt: skip
    seasons = [str(s["test_season"]) for s in summaries]
    out += ["### Every fold: G1 before vs the model in use", "",
            "| Fold | " + " | ".join(f"{HEAD[k]}: G1 -> now" for k in SHOWN) + " | Meets |",
            "|---|" + "---|" * len(SHOWN) + "---|"]  # fmt: skip
    ok = 0
    for y in seasons:
        now = {k: _val(t, "fold", y, "own", k) for k in SHOWN}
        meets = all(now[k] is not None and now[k] <= lim for k, lim in wsm.THRESHOLDS.items())
        ok += meets
        cells = [f"{_m(k, _val(t, 'fold', y, 'g1_before', k))} -> {_m(k, now[k])}" for k in SHOWN]
        out.append(f"| {y} | " + " | ".join(cells) + f" | {'yes' if meets else 'no'} |")
    out += ["", f"{ok} of {len(seasons)} fold models meet every limit.", ""]
    before = {k: _val(t, "cost", "pooled", "g1_before", k) for k in ("log_loss", "brier")}
    now = {k: _val(full, "pooled", span, "own", k) for k in ("log_loss", "brier")}
    if before["log_loss"] is not None and now["log_loss"] is not None:
        out += [f"**What smoothness cost** (test seasons {span}, the same compared plays): log "
                f"loss {before['log_loss']:.4f} (G1) -> {now['log_loss']:.4f} (now), Brier "
                f"{before['brier']:.4f} -> {now['brier']:.4f}.", ""]  # fmt: skip
    refs = [("nflfastr_vegas_wp", "nflfastR vegas_wp"), ("nflfastr_wp", "nflfastR wp")]
    if t.filter(pl.col("scope") == "reference").height:
        out += ["**References** (nflfastR's stored values read off real plays 2006-2025 near "
                "each grid line: local regressions, score levels with at least 100 plays; their "
                "model is not runnable offline, was fit partly on these seasons, and these "
                "readings are approximate):", "",
                "| Reference | 1-pt step, 1st half | 1-pt step, 2nd half | Curvature | "
                "Halftime possession (max / mean) |", "|---|---|---|---|---|"]  # fmt: skip
        for m, label in refs:
            g = {k: _val(t, "reference", "2006-2025", m, k) for k in
                 ("score_step_h1", "score_step_h2", "curvature", "halftime_possession",
                  "halftime_possession_mean")}  # fmt: skip
            out.append(f"| {label} | {_n(g['score_step_h1'])} | {_n(g['score_step_h2'])} | "
                       f"{_n(g['curvature'])} | {_n(g['halftime_possession'])} / "
                       f"{_n(g['halftime_possession_mean'])} |")  # fmt: skip
        out.append("")
    emp = [(sp, {k: _val(t, "empirical", sp, "what happened", k) for k in
                 ("possession_2min", "possession_2min_lo", "possession_2min_hi")})
           for sp in ("1999-2005", "2006-2025")]  # fmt: skip
    emp = [(sp, e) for sp, e in emp if _n(e["possession_2min"]) != "-"]
    if emp:
        txt = "; ".join(f"{sp}: {e['possession_2min']:+.1f} (95% {e['possession_2min_lo']:+.1f} "
                        f"to {e['possession_2min_hi']:+.1f})" for sp, e in emp)  # fmt: skip
        out += ["**The ball 30-120 s before halftime** (reported, no limit; added after the first "
                "regrade showed it): what it was worth in real games at the own 25-50, within "
                "10 points (logistic regression on spread, score, kickoff, home): " + txt + ".",
                ""]  # fmt: skip
    return out
