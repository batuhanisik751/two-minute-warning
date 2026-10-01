"""reports/decisions/nfl4th_benchmark.md + .csv: our fourth-down recommendations vs nfl4th (G5).

Reads the last ``twm decisions benchmark-nfl4th`` run (data/decisions/benchmark/nfl4th/:
``joined.parquet`` + ``info.json``, :mod:`twm.modules.decisions.benchmark`). Every number in
the report comes from that run. When nfl4th could only run its bundled field-goal model
(``nfl4th_status`` "fg_only": its WP and conversion models are downloads that were not made),
the report says so and shows the one comparison that exists.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import polars as pl

from twm.modules.decisions import benchmark as bm

POPULATIONS = (("all", None), ("clear", "clear"), ("toss-up", "toss_up"),
               ("one option", "one_option"))  # fmt: skip
LABEL = {"go": "go", "field_goal": "field goal", "punt": "punt"}


def report_paths() -> tuple[Path, Path]:
    from twm.config import ROOT

    d = ROOT / "reports" / "decisions"
    return d / "nfl4th_benchmark.md", d / "nfl4th_benchmark.csv"


def agreement(j: pl.DataFrame) -> pl.DataFrame:
    """Rows, agreements and the rate per population (our grade) and season, plus 'all seasons'."""
    out = []
    for name, grade in POPULATIONS:
        sub = j if grade is None else j.filter(pl.col("grade") == grade)
        for season in [*sorted(set(j.get_column("season").to_list())), None]:
            s = sub if season is None else sub.filter(pl.col("season") == season)
            n, k = s.height, int(s.get_column("agree").sum() or 0)
            out.append({"population": name, "season": "all" if season is None else str(season),
                        "rows": n, "agree": k, "rate": k / n if n else None})  # fmt: skip
    return pl.DataFrame(out)


def confusion(j: pl.DataFrame) -> pl.DataFrame:
    """Rows by our recommendation (rows) and nfl4th's (columns)."""
    c = j.group_by("recommended", "nfl4th_recommended").len()
    rows = []
    for o in bm.OPTIONS:
        r = {"ours": o}
        for t in bm.OPTIONS:
            hit = c.filter((pl.col("recommended") == o) & (pl.col("nfl4th_recommended") == t))
            r[t] = int(hit.get_column("len").sum()) if hit.height else 0
        rows.append(r)
    return pl.DataFrame(rows)


def go_gain_distribution(j: pl.DataFrame) -> dict[str, float]:
    """Ours - nfl4th's go gain (WP points): mean, spread, quantiles, share within 1.5 points."""
    d = j.get_column("go_gain_diff").drop_nulls().drop_nans()
    both = j.drop_nulls(["our_go_gain", "their_go_gain"])
    q = {f"p{int(p * 100)}": float(d.quantile(p, "linear")) for p in (0.05, 0.25, 0.5, 0.75, 0.95)}
    return {"rows": float(d.len()), "mean": float(d.mean()), "sd": float(d.std()), **q,
            "within_1_5": float((d.abs() <= 1.5).mean()), "beyond_5": float((d.abs() > 5).mean()),
            "corr": float(both.select(pl.corr("our_go_gain", "their_go_gain")).item())}  # fmt: skip


def go_rates(j: pl.DataFrame) -> pl.DataFrame:
    """Per season: rows, the share that went, and the share each side recommends going."""
    return (
        j.group_by("season")
        .agg(rows=pl.len(), went=(pl.col("chosen") == "go").mean(),
             ours=(pl.col("recommended") == "go").mean(),
             nfl4th=(pl.col("nfl4th_recommended") == "go").mean())
        .sort("season")
    )  # fmt: skip


def _mean(s: pl.DataFrame, expr: pl.Expr) -> float | None:
    v = s.select(expr).item() if s.height else None
    return None if v is None else float(v)


def components(j: pl.DataFrame) -> list[dict[str, Any]]:
    """Each sub-model side by side on the rows where both sides have it: means (ours, nfl4th),
    the mean absolute difference and the correlation; the observed rate where one exists."""
    fg = j.filter(pl.col("chosen") == "field_goal")
    went = j.filter(pl.col("chosen") == "go")
    specs = [
        ("P(make) on field-goal attempts", fg, "p_make", "fg_make_prob_bundled", 1,
         (pl.col("field_goal_result") == "made").mean()),
        ("P(first down) on real attempts", went, "p_convert", "first_down_prob", 1,
         pl.col("outcome").is_in(["converted", "touchdown"]).mean()),
        ("P(first down), every row", j, "p_convert", "first_down_prob", 1, None),
        ("WP after a conversion", j, "wp_go_success", "wp_succeed", 100, None),
        ("WP after a failed attempt", j, "wp_go_failure", "wp_fail", 100, None),
        ("WP of a field goal", j.filter(pl.col("fg_available")), "wp_fg", "fg_wp", 100, None),
        ("WP of a punt (both have one)", j.filter(pl.col("punt_available")), "wp_punt",
         "punt_wp", 100, None),
        ("WP of going for it", j, "wp_go", "go_wp", 100, None),
    ]  # fmt: skip
    out = []
    for name, rows, a, b, k, obs in specs:
        s = rows.drop_nulls([a, b]).filter(pl.col(a).is_not_nan() & pl.col(b).is_not_nan())
        out.append({"component": name, "rows": s.height,
                    "ours": _mean(s, pl.col(a).mean() * k),
                    "nfl4th": _mean(s, pl.col(b).mean() * k),
                    "mean_abs_diff": _mean(s, (pl.col(a) - pl.col(b)).abs().mean() * k),
                    "corr": _mean(s, pl.corr(a, b)) if s.height > 2 else None,
                    "observed": _mean(s, obs * k) if obs is not None else None})  # fmt: skip
    return out


def disagreements(j: pl.DataFrame, n: int = 12) -> pl.DataFrame:
    """The ``n`` disagreements with the largest go-gain difference (either sign)."""
    return (
        j.filter(~pl.col("agree"))
        .with_columns(size=pl.col("go_gain_diff").abs())
        .sort(["size", "game_id", "play_id"], descending=[True, False, False])
        .head(n)
    )


def causes(j: pl.DataFrame) -> pl.DataFrame:
    """Disagreements by likely cause (rows, how many we grade clear, mean go-gain gap)."""
    d = j.filter(~pl.col("agree"))
    return (
        d.group_by("likely_cause").agg(rows=pl.len(), clear=(pl.col("grade") == "clear").sum(),
                                       mean_abs_go_gain_diff=pl.col("go_gain_diff").abs().mean())
        .sort("rows", descending=True)
    )  # fmt: skip


def _phase() -> pl.Expr:
    late = pl.col("half_seconds_remaining") <= 120
    q4 = pl.col("qtr") == 4
    return (pl.when(late & q4).then(pl.lit("Q4, last 2:00"))
            .when(late & (pl.col("qtr") == 2)).then(pl.lit("Q2, last 2:00"))
            .when(q4 & (pl.col("half_seconds_remaining") <= 300)).then(pl.lit("Q4, 5:00-2:00"))
            .when(q4).then(pl.lit("Q4, before 5:00")).otherwise(pl.lit("Q1-Q3")))  # fmt: skip


def _zone() -> pl.Expr:
    y = pl.col("yardline_100")
    return (pl.when(y <= 20).then(pl.lit("opp 20 and in")).when(y <= 40).then(pl.lit("opp 21-40"))
            .when(y <= 55).then(pl.lit("opp 41 to own 45"))
            .otherwise(pl.lit("own 44 and back")))  # fmt: skip


def _distance() -> pl.Expr:
    d = pl.col("ydstogo")
    return (pl.when(d == 1).then(pl.lit("1")).when(d <= 3).then(pl.lit("2-3"))
            .when(d <= 6).then(pl.lit("4-6")).otherwise(pl.lit("7+")))  # fmt: skip


SITUATIONS = (("game phase", _phase, ["Q1-Q3", "Q2, last 2:00", "Q4, before 5:00",
                                      "Q4, 5:00-2:00", "Q4, last 2:00"]),
              ("field position", _zone, ["opp 20 and in", "opp 21-40", "opp 41 to own 45",
                                         "own 44 and back"]),
              ("yards to go", _distance, ["1", "2-3", "4-6", "7+"]))  # fmt: skip


def by_situation(j: pl.DataFrame, expr: pl.Expr, order: list[str]) -> pl.DataFrame:
    """Per group: rows, agreement, each side's go recommendation rate, mean go-gain gap."""
    g = (j.with_columns(group=expr)
         .group_by("group")
         .agg(rows=pl.len(), agree=pl.col("agree").mean(),
              ours=(pl.col("recommended") == "go").mean(),
              nfl4th=(pl.col("nfl4th_recommended") == "go").mean(),
              gap=pl.col("go_gain_diff").mean(),
              abs_gap=pl.col("go_gain_diff").abs().mean()))  # fmt: skip
    rank = {k: i for i, k in enumerate(order)}
    return g.sort(pl.col("group").replace_strict(rank, return_dtype=pl.Int32))


def _f(x: Any, nd: int = 1, pct: bool = False) -> str:
    if x is None or (isinstance(x, float) and x != x):
        return "-"
    return f"{100 * x:.{nd}f}%" if pct else f"{x:.{nd}f}"


def _table(head: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    return out + ["| " + " | ".join(str(c) for c in r) + " |" for r in rows] + [""]


def _clock(r: dict) -> str:
    q = r["half_seconds_remaining"] - (900 if r["qtr"] in (1, 3) else 0)
    return f"Q{r['qtr']} {int(q) // 60}:{int(q) % 60:02d}"


def _field(y: Any) -> str:
    return f"own {100 - int(y)}" if y > 50 else ("50" if y == 50 else f"opp {int(y)}")


CAUSE_TEXT = {
    bm.CAUSES[0]: "the two conversion models give different chances of a first down; the go "
    "option moves by that difference times the swing between converting and failing.",
    bm.CAUSES[1]: "the two field-goal models give different make chances (nfl4th: one "
    "model by roof (outdoors or not) and era (2020 on), scaled down beyond the 40 and 0 from "
    "the 53; ours: G2's walk-forward model with distance, weather and surface).",
    bm.CAUSES[2]: "the punt is valued differently: nfl4th uses a fixed table of punt results by "
    "yard line (from its 31 out) priced by its WP; ours uses G2's punt-result distribution "
    "priced by our WP model.",
    bm.CAUSES[3]: "the states after the play are valued differently: the WP models differ "
    "(nfl4th averages its own xgboost model with nflfastR's vegas_wp; ours is G1b's "
    "walk-forward model), and so do the clock rules: our go option runs the median "
    "fourth-down runoff (14-22 s) off after either outcome, nfl4th 6 s; nfl4th also sets WP "
    "to 1 or 0 when the leading team has the ball late and the other side cannot stop the "
    "clock (its end-game rules).",
}


def _header(info: dict) -> list[str]:
    lo = info["left_out"]
    return [
        "# Fourth-down benchmark: our recommendations vs nfl4th",
        "",
        f"Seasons {', '.join(str(s) for s in info['seasons'])}; nfl4th "
        f"{', '.join(info['nfl4th_version'])} (R, CRAN); run status "
        f"**{', '.join(info['nfl4th_status'])}**; R ran with every network call denied: "
        f"{'yes' if info['network_denied'] else 'no (no macOS sandbox here)'}.",
        "",
        f"Rows: {info['graded_rows']:,} graded fourth downs (G3 rules, G1b WP model); "
        f"{info['benchmarked']:,} benchmarked; left out {lo['overtime']} in overtime and "
        f"{lo['last_15_seconds']} with 15 seconds or less left (nfl4th evaluates neither).",
        "",
        "**Fairness.** Our grades use models fitted only on seasons before the graded one. "
        "nfl4th's models were fitted on nflfastR data; the package does not document which "
        "seasons, and they very likely include these, so nfl4th is probably in-sample here "
        "(PROJECT_SPEC 6.3). Agreement is not accuracy: neither side is the truth.",
        "",
    ]


def _mapping() -> list[str]:
    rows = [[f"`{k}`", v] for k, v in bm.STATE_COLUMNS.items()]
    return [
        "## Inputs we give nfl4th",
        "",
        "nfl4th's `add_4th_probs()` gets a data frame of these columns (names checked against the "
        "installed package's help and source); it sets down = 4 and rebuilds the clock, spread "
        "and totals itself. Its games table (spread, total, roof) is built from our warehouse "
        "(`fact_game`) with nfl4th's own transformations; no play-by-play or schedule is "
        "downloaded. No `runoff` is passed (nfl4th's default: 0 seconds).",
        "",
        *_table(["nfl4th column", "how we fill it"], rows),
        "Option availability differs: our field goal exists within the longest field goal made "
        "before the season and our punt from the yard lines punts come from; nfl4th always "
        "prices a field goal (make chance 0 from its 53, a 70-yard try) and punts only from "
        "its 31 out.",
        "",
    ]


def _components_section(j: pl.DataFrame, only_fg: bool) -> list[str]:
    comp = components(j)
    if only_fg:
        comp = comp[:1]
    rows = [[c["component"], f"{c['rows']:,}", _f(c["ours"], 3), _f(c["nfl4th"], 3),
             _f(c["mean_abs_diff"], 3), _f(c["corr"], 2), _f(c["observed"], 3)]
            for c in comp]  # fmt: skip
    return [
        "## Sub-models side by side",
        "",
        "Means over the rows where both sides have the number (probabilities 0-1, WP in "
        "points 0-100); 'observed' is what really happened where it exists.",
        "",
        *_table(
            ["component", "rows", "ours", "nfl4th", "mean abs diff", "corr", "observed"], rows
        ),  # fmt: skip
    ]


def _blocked(info: dict) -> list[str]:
    return [
        "## Status: the decision benchmark has NOT run",
        "",
        "nfl4th 1.0.7 ships its field-goal model, its punt table and its two-point model, but "
        "downloads its win-probability model (`wp_model.rds`) and its conversion model "
        "(`fd_model.rds`) from GitHub (nflverse/nfl4th releases, `model_archive`) the first "
        "time they are needed. Only the CRAN install was approved, so they were not "
        "downloaded, and every nfl4th WP (go, field goal, punt) and recommendation needs them. "
        "This report therefore shows only the comparison that ships with the package (field "
        "goal make chances). With both files in `data/decisions/benchmark/nfl4th/cache/R/"
        "nfl4th/`, rerunning `twm decisions benchmark-nfl4th` writes the full report "
        "(agreement overall / clear / toss-up, confusion table, go-gain differences, go rates, "
        "biggest disagreements) without any network use.",
        "",
    ]


def _full_sections(j: pl.DataFrame) -> list[str]:
    ag = agreement(j)
    cf = confusion(j)
    gd = go_gain_distribution(j)
    gr = go_rates(j)
    out = ["## Agreement on the recommended option", "",
           "Same recommended option (go / field goal / punt), by our grade: 'clear' when our best "
           "option beats the second by more than the toss-up margin, else 'toss-up'.", "",
           *_table(["our grade", "season", "rows", "agree", "rate"],
                   [[r["population"], r["season"], f"{r['rows']:,}", f"{r['agree']:,}",
                     _f(r["rate"], 1, pct=True)] for r in ag.iter_rows(named=True)]),
           "## Confusion table (all benchmarked rows)", "",
           "Rows: our recommendation; columns: nfl4th's.", "",
           *_table(["ours \\ nfl4th", *[LABEL[o] for o in bm.OPTIONS]],
                   [[LABEL[r["ours"]], *[f"{r[o]:,}" for o in bm.OPTIONS]]
                    for r in cf.iter_rows(named=True)]),
           "## Go gain: ours minus nfl4th's", "",
           "Go gain = WP(go) - WP(best kick), in WP points (nfl4th's `go_boost`). The difference "
           "is ours minus nfl4th's on the same play.", "",
           *_table(["rows", "mean", "sd", "5%", "25%", "median", "75%", "95%",
                    "within 1.5", "beyond 5", "corr"],
                   [[f"{int(gd['rows']):,}", _f(gd["mean"], 2), _f(gd["sd"], 2), _f(gd["p5"], 2),
                     _f(gd["p25"], 2), _f(gd["p50"], 2), _f(gd["p75"], 2), _f(gd["p95"], 2),
                     _f(gd["within_1_5"], 1, pct=True), _f(gd["beyond_5"], 1, pct=True),
                     _f(gd["corr"], 3)]]),
           "## League go rate: what each side recommends", "",
           *_table(["season", "rows", "teams went", "we recommend go", "nfl4th recommends go"],
                   [[r["season"], f"{r['rows']:,}", _f(r["went"], 1, pct=True),
                     _f(r["ours"], 1, pct=True), _f(r["nfl4th"], 1, pct=True)]
                    for r in gr.iter_rows(named=True)])]  # fmt: skip
    return out


def _explain(r: dict) -> str:
    """One plain sentence per disagreement, numbers from the row."""
    parts = [f"we say {LABEL[r['recommended']]} (go gain {r['our_go_gain']:+.1f}), nfl4th says "
             f"{LABEL[r['nfl4th_recommended']]} ({r['their_go_gain']:+.1f})",
             f"P(first down) {_f(r['p_convert'], 0, pct=True)} vs "
             f"{_f(r['first_down_prob'], 0, pct=True)}"]  # fmt: skip
    if "field_goal" in (r["recommended"], r["nfl4th_recommended"]):
        parts.append(f"P(make) {_f(r['p_make'], 0, pct=True)} vs "
                     f"{_f(r['fg_make_prob'], 0, pct=True)}")  # fmt: skip
    parts.append(f"WP after success {_f(100 * r['wp_go_success'])} vs "
                 f"{_f(100 * r['wp_succeed'])}, after failure {_f(100 * r['wp_go_failure'])} vs "
                 f"{_f(100 * r['wp_fail'])}")  # fmt: skip
    return "; ".join(parts) + f". Likely cause: {r['likely_cause']}."


def _disagreement_sections(j: pl.DataFrame) -> list[str]:
    top = disagreements(j)
    rows = [[f"{r['season']} wk {r['week']}", f"{r['posteam']} v {r['defteam']}", _clock(r),
             f"4th & {r['ydstogo']} at {_field(r['yardline_100'])}",
             f"{r['score_differential']:+d}", r["grade"], LABEL.get(r["chosen"], "-"),
             _explain(r)] for r in top.iter_rows(named=True)]  # fmt: skip
    cs = causes(j)
    crow = [[r["likely_cause"], f"{r['rows']:,}", f"{r['clear']:,}",
             _f(r["mean_abs_go_gain_diff"], 1), CAUSE_TEXT.get(r["likely_cause"], "")]
            for r in cs.iter_rows(named=True)]  # fmt: skip
    return [
        "## Biggest disagreements", "",
        "The disagreements with the largest go-gain difference (WP points; 'ours vs nfl4th').",
        "",
        *_table(["game", "teams", "clock", "situation", "score", "our grade", "chosen",
                 "what differs"], rows),
        "## Why they disagree (all disagreements)", "",
        "Likely cause = the sub-model whose difference moves WP the most on that play: "
        "|conversion chance gap| x |success - failure WP|, |make chance gap| x |make - miss WP| "
        "(when a field goal is one of the two picks), |punt WP gap| (when a punt is), and the "
        "mean gap of the WP after success and failure. A heuristic, not a decomposition.", "",
        *_table(["likely cause", "rows", "clear for us", "mean go-gain gap", "what it means"],
                crow),
    ]  # fmt: skip


def _models(info: dict) -> list[str]:
    """Provenance of nfl4th's two downloaded model files (the only downloads besides CRAN)."""
    m = info.get("models") or []
    if not m:
        return []
    rows = [[f"`{r['file']}`", f"{r['bytes']:,}", f"`{r['sha256']}`", r["downloaded_utc"] or "-",
             r["url"] or "-"] for r in m]  # fmt: skip
    return [
        "## nfl4th's model files",
        "",
        "nfl4th downloads its WP and conversion models on first use. The owner approved these "
        "two files (2026-10-01); they were fetched once from the official nflverse/nfl4th "
        "release `model_archive` into nfl4th's cache, their sizes checked against the approved "
        "numbers, and their sha256 is re-checked on every run. Nothing else was downloaded: no "
        "play-by-play, no schedules, no other release asset; R runs with those code paths "
        "replaced by errors.",
        "",
        *_table(["file", "bytes", "sha256", "downloaded (UTC)", "url"], rows),
    ]


def _situation_sections(j: pl.DataFrame) -> list[str]:
    out = [
        "## Where they disagree",
        "",
        "Agreement and each side's go recommendation by situation; 'go-gain gap' = mean of "
        "ours minus nfl4th's (positive: we like going more), and its mean size.",
        "",
    ]
    for name, expr, order in SITUATIONS:
        g = by_situation(j, expr(), order)
        out += _table([name, "rows", "agree", "we say go", "nfl4th says go", "go-gain gap",
                       "mean size"],
                      [[r["group"], f"{r['rows']:,}", _f(r["agree"], 1, pct=True),
                        _f(r["ours"], 1, pct=True), _f(r["nfl4th"], 1, pct=True),
                        _f(r["gap"], 2), _f(r["abs_gap"], 2)]
                       for r in g.iter_rows(named=True)])  # fmt: skip
    return out


def render(j: pl.DataFrame, info: dict) -> list[str]:
    only_fg = "full" not in info["nfl4th_status"]
    lines = _header(info) + _models(info)
    if only_fg:
        lines += _blocked(info)
    else:
        lines += _full_sections(j) + _situation_sections(j) + _disagreement_sections(j)
    return lines + _components_section(j, only_fg) + _mapping()


CSV_COLUMNS = ("game_id", "play_id", "season", "week", "posteam", "defteam", "qtr",
               "half_seconds_remaining", "ydstogo", "yardline_100", "score_differential",
               "grade", "chosen", "recommended", "nfl4th_recommended", "agree", "our_go_gain",
               "their_go_gain", "go_gain_diff", "p_convert", "first_down_prob", "p_make",
               "fg_make_prob_bundled", "wp_go", "go_wp", "wp_fg", "fg_wp", "wp_punt", "punt_wp",
               "likely_cause", "nfl4th_status")  # fmt: skip


def write_report(*, out_dir: Path | None = None,
                 progress: Callable[[str], None] = print) -> tuple[Path, Path]:  # fmt: skip
    """reports/decisions/nfl4th_benchmark.md + .csv from the last benchmark run."""
    j, info = bm.load(out_dir)
    md, csv = report_paths()
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text("\n".join(render(j, info)).rstrip() + "\n")
    j.select(c for c in CSV_COLUMNS if c in j.columns).write_csv(csv)
    progress(f"report: {j.height:,} rows ({', '.join(info['nfl4th_status'])})")
    return md, csv
