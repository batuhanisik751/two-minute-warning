"""Hot-Seat H5 research report: ``reports/hot_seat/research.md`` + ``research.csv``.

Every number in the markdown is computed here from :class:`research.Research` (nothing typed);
docs/research_decisions_vs_firings.md quotes this report.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from twm.modules.hot_seat import research as rs

FILES = {"report": "research.md", "csv": "research.csv"}
ROBUST_HEAD = (
    "spec", "what", "rows", "coach-seasons", "firings", "SD", "odds ratio", "classical 95%",
    "season-bootstrap 95%",
)  # fmt: skip


def _table(head: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(v) for v in r) + " |" for r in rows]
    return [*out, ""]


def _ci(lo: float, hi: float) -> str:
    return f"{lo:.2f} to {hi:.2f}"


def _mle(v: float | None) -> str:
    return "did not converge" if v is None else f"{v:.4f}"


def _risk(p0: float, odds_ratio: float) -> float:
    """The probability at baseline ``p0`` once its odds are multiplied by ``odds_ratio``."""
    odds = p0 / (1.0 - p0) * odds_ratio
    return odds / (1.0 + odds)


def verdict(classical: tuple[float, float], boot: tuple[float, float], odds_ratio: float) -> str:
    """``none`` (both 95% intervals hold 1), ``higher`` / ``lower`` (both exclude 1), or
    ``borderline`` (one does, one does not)."""
    holds = [lo <= 1.0 <= hi for lo, hi in (classical, boot)]
    if all(holds):
        return "none"
    if not any(holds):
        return "higher" if odds_ratio > 1.0 else "lower"
    return "borderline"


def decision_row(tab: pl.DataFrame, spec: str) -> dict[str, Any]:
    return tab.filter((pl.col("spec") == spec) & pl.col("is_decision")).row(0, named=True)


def facts(res: rs.Research, tab: pl.DataFrame) -> dict[str, Any]:
    """The primary estimate and the numbers the plain-English text uses."""
    r = decision_row(tab, "primary")
    p = res.estimates[0]
    games = float(p.design.rows.get_column("reg_games_played").mean())  # type: ignore[arg-type]
    c = (r["or_lo_classical"], r["or_hi_classical"])
    b = (r["or_lo_bootstrap"], r["or_hi_bootstrap"])
    p0 = r["n_positive_rows"] / r["n_rows"]
    lo, hi = min(c[0], b[0]), max(c[1], b[1])
    return {
        **r, "games": games, "p0": p0, "verdict": verdict(c, b, r["odds_ratio"]),
        "p1": _risk(p0, r["odds_ratio"]), "p_lo": _risk(p0, lo), "p_hi": _risk(p0, hi),
        "wins_mean": r["term_mean"] * games, "wins_sd": r["term_sd"] * games,
        "span_lo": lo, "span_hi": hi,
    }  # fmt: skip


def answer_lines(f: Mapping[str, Any], perm_p: float) -> list[str]:
    head = {
        "none": "**No detectable effect.**",
        "higher": "**Yes: worse fourth-down decisions go with a higher firing risk.**",
        "lower": "**The opposite: worse fourth-down decisions go with a LOWER firing risk.**",
        "borderline": "**Borderline: one 95% interval excludes no effect, the other does not.**",
    }[f["verdict"]]
    pct = f["term_sd"] * 100
    return [
        "## The answer",
        "",
        f"{head} Controlling for performance vs expectation and the context features, one "
        f"standard deviation more fourth-down WP lost per game multiplies a coach's odds of "
        f"being fired at season end by **{f['odds_ratio']:.2f}** (classical 95% interval "
        f"{_ci(f['or_lo_classical'], f['or_hi_classical'])}; season-bootstrap 95% interval "
        f"{_ci(f['or_lo_bootstrap'], f['or_hi_bootstrap'])}). An odds ratio of 1 means no "
        f"effect. The within-season permutation check gives p = {perm_p:.3f}.",
        "",
        f"In real terms: one SD is {pct:.2f} WP points lost per game on clear fourth-down "
        f"calls, which over a {f['games']:.1f}-game season (the sample's mean) is about "
        f"{f['wins_sd']:.2f} expected wins given away (the average coach gives away "
        f"{f['wins_mean']:.2f}). At the sample's firing rate ({f['p0']:.1%} of coach-seasons), "
        f"a coach one SD worse would be at {f['p1']:.1%}; the two intervals together allow "
        f"anything from {f['p_lo']:.1%} to {f['p_hi']:.1%}.",
        "",
    ]


def data_lines(
    res: rs.Research, f: Mapping[str, Any], labels: Mapping[str, int], grades: Mapping[str, int]
) -> list[str]:
    haz = next(e for e in res.estimates if e.spec.name == "hazard")
    return [
        "## Data",
        "",
        f"- Rows (primary): the end-of-season snapshot of {rs.FIRST_SEASON}-{rs.LAST_SEASON} "
        f"(the decision grades start in {rs.FIRST_SEASON}), interim coaches excluded: "
        f"**{f['coach_seasons']} coach-seasons, {f['positive_coach_seasons']} firings** "
        f"(y = 1: fired after the season or a mutual parting, announced from the last "
        f"regular-season game day to 30 days after the final game), {f['n_seasons']} seasons. "
        f"Of the {haz.positive_coach_seasons} positive coach-seasons in the hazard rows, "
        f"{haz.positive_coach_seasons - f['positive_coach_seasons']} have no positive "
        "end-of-season row (fired in season, or announced before the team's last game): they "
        "enter only the week-12 and hazard specifications.",
        f"- Labels: verified mode ({labels['departures_joined']} departures joined, "
        f"{labels['departures_unverified']} unverified, {labels['departures_date_imputed']} "
        "with a blank date imputed as the coach's last game day). Where they come from: cited "
        "public research (step H1) that the owner accepted in bulk on 2026-10-01 rather than "
        're-checking each row (docs/hot_seat.md, "Where the labels come from").',
        f"- Decision measure: `{rs.DECISION}` (step H3a): the WP lost on the team's clear "
        "fourth-down calls (WP(best) - WP(chosen), best and second-best more than 1.5 WP points "
        "apart) over its regular-season games / games. Read from the pinned, stored grades "
        f"(never regraded): {grades['graded']:,} graded regular-season fourth downs "
        f"{rs.FIRST_SEASON}-{rs.LAST_SEASON} ({grades['clear']:,} clear, {grades['toss_up']:,} "
        f"toss-ups); {grades['late_game']:,} late-game fourth downs (last 120 s of the 4th "
        "quarter and overtime) are not graded since G3b. Mean "
        f"{f['term_mean']:.4f}, SD {f['term_sd']:.4f} WP per game. Recomputed from the stored "
        f"grades it equals the stored feature (largest difference {res.check_clear_max_diff:.1e}).",
        f"- Controls (performance vs expectation): {', '.join(rs.PERFORMANCE)}; (context): "
        f"{', '.join(rs.CONTEXT)}. Every column is standardized (per 1 SD of the rows used).",
        "",
    ]


def primary_lines(res: rs.Research, tab: pl.DataFrame) -> list[str]:
    p = res.estimates[0]
    t = tab.filter(pl.col("spec") == "primary")
    rows = [
        (f"**{r['term']}**" if r["is_decision"] else r["term"], f"{r['odds_ratio']:.2f}",
         _ci(r["or_lo_classical"], r["or_hi_classical"]),
         _ci(r["or_lo_bootstrap"], r["or_hi_bootstrap"]), f"{r['term_sd']:.4g}")
        for r in t.iter_rows(named=True)
    ]  # fmt: skip
    d = decision_row(tab, "primary")
    return [
        "## Primary estimate (every term of the model)",
        "",
        "Logistic regression of `y` at the end-of-season snapshot; odds ratio per 1 SD of each "
        "column (SD in its own units in the last column).",
        "",
        *_table(["term", "odds ratio", "classical 95%", "season-bootstrap 95%", "SD"], rows),
        f"Decision term: log-odds coefficient {d['coef']:+.4f} per SD (classical SE "
        f"{d['se']:.4f}); pure maximum likelihood (no L2 term) odds ratio "
        f"{_mle(d['odds_ratio_mle'])}; {d['boot_converged']} of {res.n_boot} season resamples "
        f"converged; fit converged: {p.fit.converged}.",
        "",
    ]


def robustness_lines(res: rs.Research, tab: pl.DataFrame) -> list[str]:
    rows = []
    for e in res.estimates:
        r = decision_row(tab, e.spec.name)
        rows.append((
            e.spec.name, e.spec.what, r["n_rows"], r["coach_seasons"],
            r["positive_coach_seasons"], f"{r['term_sd']:.4f}", f"{r['odds_ratio']:.2f}",
            _ci(r["or_lo_classical"], r["or_hi_classical"]),
            _ci(r["or_lo_bootstrap"], r["or_hi_bootstrap"]),
        ))  # fmt: skip
    rup = next(e for e in res.estimates if e.spec.name == "rup_positive")
    n_rup = rup.design.rows.filter(pl.col("departure_type") == rs.RUP).height
    clk = next(e for e in res.estimates if e.spec.name == "with_clock")
    with_case = int((clk.design.rows.get_column(rs.CLOCK) > 0).sum())
    ck = tab.filter((pl.col("spec") == "with_clock") & (pl.col("term") == rs.CLOCK))
    clock_or = (
        f"the clock term's own odds ratio is {ck['odds_ratio'][0]:.2f} (classical "
        f"{_ci(ck['or_lo_classical'][0], ck['or_hi_classical'][0])})"
        if ck.height
        else "the clock column is constant in these rows and was dropped"
    )
    return [
        "## Robustness (the decision term in each specification)",
        "",
        "Odds ratio per 1 SD of the decision measure, SD in WP per game over that "
        "specification's rows. Rows: model rows (the hazard has one per coach and as-of week); "
        "firings: coach-seasons with y = 1 (hazard: an event).",
        "",
        *_table(ROBUST_HEAD, rows),
        f"- `rup_positive`: {n_rup} of its rows carry a `{rs.RUP}` departure in "
        f"{rs.FIRST_SEASON}-{rs.LAST_SEASON}"
        + (": the same labels, so the primary fit again." if n_rup == 0 else "."),
        f"- `with_clock`: `{rs.CLOCK}` = the season's clock-management cases (timeouts unused, "
        "end-of-half passivity, seconds wasted; G4's stored cases) per regular-season game; "
        f"{with_case} of its {clk.design.rows.height} coach-seasons have at least one case; "
        f"{clock_or}.",
        f"- `all_graded`: `{rs.DECISION_ALL}` = the WP lost on every graded fourth down (clear "
        "calls and toss-ups) per game, from the same stored grades.",
        "- `hazard`: one row per coach and as-of week, so its classical interval treats "
        "dependent rows as independent; the season bootstrap resamples whole "
        "seasons, every row of a coach-season together.",
        "",
    ]


def permutation_lines(res: rs.Research, f: Mapping[str, Any]) -> list[str]:
    vals = res.perm[np.isfinite(res.perm)]
    lo, hi = np.quantile(vals, [0.025, 0.975]) if vals.size else (np.nan, np.nan)
    return [
        "## Permutation check",
        "",
        f"The decision column was shuffled within each season {res.n_perm:,} times (every "
        "season keeps its own values, only which coach gets which moves) and the primary model "
        f"refit each time: {vals.size:,} fits converged. Under that null the decision term's "
        f"odds ratio has median {np.exp(np.median(vals)):.2f} and middle 95% "
        f"{_ci(float(np.exp(lo)), float(np.exp(hi)))}; the observed {f['odds_ratio']:.2f} gives "
        f"a two-sided p = {res.perm_p:.3f} (share of shuffles at least as far from 0 in log "
        "odds, counting the observed one). A shuffle keeps each season's own values, so an "
        "association between a season's level of WP lost and its number of firings stays in "
        "the null: its median need not be 1.",
        "",
    ]


def limits_lines(f: Mapping[str, Any], grades: Mapping[str, int]) -> list[str]:
    return [
        "## Honest limits",
        "",
        f"- Few firings: {f['positive_coach_seasons']} at the end-of-season snapshot over "
        f"{f['n_seasons']} seasons, so the intervals are wide"
        + (
            ": a modest effect in either direction cannot be ruled out."
            if f["verdict"] == "none"
            else " and the estimate rests on few events."
        ),
        '- Owners never see "WP lost": it is this project\'s measure. An owner sees the record '
        "and the results against expectations, which the controls hold fixed; the question is "
        "whether decision quality matters beyond them.",
        "- Correlation, not causation: the coefficient is an association given the controls.",
        "- The labels are cited public research that the owner accepted in bulk "
        "(docs/hot_seat.md), not row-by-row verified.",
        "- The decisions are graded by this project's own models (the G1 WP model and the G2 "
        "sub-models): if the grades are noisy, a real effect is pulled toward an odds ratio "
        "of 1.",
        f"- Late-game fourth downs ({grades['late_game']:,} in the regular seasons "
        f"{rs.FIRST_SEASON}-{rs.LAST_SEASON}) have not been graded since G3b, and those are "
        "often the "
        "calls fans and owners notice most.",
        f"- The clock-management cases are rare ({grades['clock_cases']:,} regular-season cases "
        f"in {rs.FIRST_SEASON}-{rs.LAST_SEASON}), so the `with_clock` row says little about "
        "clock management itself.",
        "",
    ]


def method_lines(res: rs.Research) -> list[str]:
    return [
        "## Method",
        "",
        f"- Near-unpenalized logistic regression (Newton's method; an L2 term of {rs.RIDGE:g} "
        "on the standardized slopes, never on the intercept, so that a season resample in "
        "which a binary column separates the classes still has a finite fit). The pure "
        "maximum-likelihood odds ratio is printed beside it.",
        "- Classical interval: Wald, coefficient +- 1.96 SE from the information matrix.",
        f"- Season-block bootstrap: {res.n_boot:,} resamples of the seasons with replacement "
        "(the shared `twm.backtest.metrics.block_indices`, fixed seed; a season drawn k times "
        "weighs k), refit each time; percentile interval. The columns keep the full sample's "
        "standardization in every resample.",
        f"- Permutation: {res.n_perm:,} within-season shuffles of the decision column, fixed "
        f"seed {rs.PERM_SEED}.",
        "- Rows with a NULL in a model column are dropped (counted in research.csv, "
        "`dropped_null_rows`); a column constant in a specification's rows is dropped.",
        "",
    ]


def report_lines(
    res: rs.Research,
    tab: pl.DataFrame,
    labels: Mapping[str, int],
    grades: Mapping[str, int],
) -> list[str]:
    f = facts(res, tab)
    return [
        "# Hot-Seat Meter: does poor fourth-down decision quality raise firing risk? (step H5)",
        "",
        'Generated by `uv run twm hotseat research` (PROJECT_SPEC 8.5, "Research question"): '
        "controlling for performance vs expectation, does poor decision quality (8.4) raise "
        "firing risk? Write-up: docs/research_decisions_vs_firings.md. Every coefficient: "
        "research.csv.",
        "",
        *answer_lines(f, res.perm_p),
        *data_lines(res, f, labels, grades),
        *primary_lines(res, tab),
        *robustness_lines(res, tab),
        *permutation_lines(res, f),
        *limits_lines(f, grades),
        *method_lines(res),
    ]


def write_outputs(
    res: rs.Research,
    labels: Mapping[str, int],
    grades: Mapping[str, int],
    out_dir: Path,
) -> dict[str, Path]:
    """research.md and research.csv in ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    tab = rs.estimate_table(res)
    paths = {k: out_dir / v for k, v in FILES.items()}
    tab.write_csv(paths["csv"], float_precision=6)
    paths["report"].write_text("\n".join(report_lines(res, tab, labels, grades)).rstrip() + "\n")
    return paths
