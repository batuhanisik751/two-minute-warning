"""reports/decisions/fourth_downs.md + .csv: the G3 grades (PROJECT_SPEC 8.4 items 2, 3, 5).

Everything comes from the stored grades (data/decisions/graded/, ``twm decisions grade``):
what was graded and excluded (by rule), the league's WP lost and go rate per season against
the recommended rate, the coach leaderboard, the worst calls, the inputs that are not models
(kickoff spot, option ranges, Platt), and the checks (stored grades reproduced bit for bit
from their inputs; chosen-option WP vs the pre-snap WP).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from twm.modules.decisions import coach
from twm.modules.decisions import grade as gr
from twm.modules.decisions import regrade_compare as rc

RULE_TEXT = {
    "not_a_snap": "not a scrimmage snap (no play type a decision can have)",
    "penalty_no_play": "the snap did not count (pre-snap penalty, or a penalty that wiped "
    "the play out: `no_play`; the replayed down is graded instead)",
    "kneel_or_spike": "a kneel or a spike (running out the clock)",
    "aborted_snap": "an aborted snap (the intended play is unknown)",
    "end_of_half": "the half's last snap (end-of-half desperation)",
    "missing_state": "a state the models cannot score (missing timeouts, spread, kickoff spot)",
}
VERIFY_SAMPLE = 200  # stored rows per season and kind regraded for the report's check


def report_paths() -> tuple[Path, Path]:
    from twm.config import ROOT

    d = ROOT / "reports" / "decisions"
    return d / "fourth_downs.md", d / "fourth_downs.csv"


def _summaries(out_dir: Path | None) -> dict[int, dict[str, Any]]:
    d = out_dir if out_dir is not None else gr.graded_dir()
    out = {}
    for f in sorted(d.glob("season_*.json")):
        s = json.loads(f.read_text())
        out[int(s["season"])] = s
    return out


def verify_sample(fourth: pl.DataFrame, tries: pl.DataFrame, n: int = VERIFY_SAMPLE,
                  models_root: Path | None = None, models=None) -> dict[str, Any]:  # fmt: skip
    """Regrade ``n`` stored graded rows per season and kind (a fixed-seed sample) from their
    stored inputs; returns rows checked and the columns that differ (should be none)."""
    out: dict[str, Any] = {"rows": 0, "mismatches": []}
    for kind, rows in (("fourth_downs", fourth), ("two_point", tries)):
        for (season,), part in rows.filter(pl.col("exclusion").is_null()).group_by(
                "season", maintain_order=True):  # fmt: skip
            sample = part.sample(min(n, part.height), seed=int(season))
            bad = gr.verify(sample, kind, models_root=models_root, models=models)
            out["rows"] += sample.height
            out["mismatches"] += [f"{kind} {season}: {c}" for c in bad]
    return out


def _last_test(fourth: pl.DataFrame) -> int:
    from twm.modules.decisions import wp as wpm

    done = [s for s in fourth.get_column("season").unique().to_list() if s in wpm.TEST_SEASONS]
    return max(done) if done else int(fourth.get_column("season").max())


def compute(out_dir: Path | None = None, *, cfg: Any = None, verify: bool = True,
            models_root: Path | None = None, models=None,
            before: Path | None = None) -> dict[str, Any]:  # fmt: skip
    """Every table of the report from the stored grades."""
    from twm.config import settings

    cfg = cfg if cfg is not None else settings().decisions
    fourth = gr.load_graded("fourth_downs", out_dir=out_dir)
    tries = gr.load_graded("two_point", out_dir=out_dir)
    seasons = coach.coach_season(fourth, tries)
    graded = fourth.filter(pl.col("exclusion").is_null())
    return {
        "fourth": fourth, "tries": tries, "summaries": _summaries(out_dir),
        "league": coach.league_by_season(fourth, tries), "coach_season": seasons,
        "coach_week": coach.coach_week(fourth, tries), "cfg": cfg,
        "consistency": graded.group_by("chosen").agg(
            pl.len().alias("n"), (pl.col("wp_chosen") - pl.col("wp_before")).mean().alias("d")
        ).sort("chosen"),
        "verify": (verify_sample(fourth, tries, models_root=models_root, models=models)
                   if verify else None),
        "wp_diag": wp_diagnostics(fourth.get_column("season").unique().to_list(), models_root),
        "fold_smooth": fold_smoothness(fourth.get_column("season").unique().to_list(),
                                       None if out_dir is None else out_dir.parent / "wp_backtest"),
        "compare": rc.compare(fourth, tries, season=_last_test(fourth),
                              min_games=int(cfg.leaderboard_min_games),
                              before=before if before is not None else (
                                  rc.before_dir() if out_dir is None
                                  else out_dir.parent / "graded_g1")),
        "platt": platt_check(fourth),
    }  # fmt: skip


def _probe(season: int, **over: Any) -> dict[str, Any]:
    s = dict(season=season, score_differential=0, game_seconds_remaining=3300,
             half_seconds_remaining=1500, half_number=1, posteam_timeouts_remaining=3,
             defteam_timeouts_remaining=3, receives_2h_kickoff=0, posteam_is_home=0.5,
             posteam_spread=0.0, down=1, ydstogo=10, yardline_100=70)  # fmt: skip
    return {**s, **over}


def wp_diagnostics(seasons, models_root: Path | None = None) -> pl.DataFrame:
    """Two consistency checks of each season's WP fold model on synthetic states (model
    properties, not grades): ``score_step`` = the largest WP change for ONE point of score
    (-14..+14) for a team receiving a first-quarter kickoff (1st and 10 at its 30, even
    spread; football says about 0.01-0.03); ``halftime_possession`` = WP with the ball at
    its 42 minus WP when the opponent has it at the opponent's 42, 1 second before halftime,
    tied, even spread (football says about 0: nothing can happen)."""
    from twm.modules.decisions import submodels as sm
    from twm.modules.decisions import wp as wpm

    out = []
    for s in sorted(seasons):
        try:
            m = wpm.load_fold_model(s, models_root=models_root)
        except wpm.WpModelError:
            continue
        steps = pl.DataFrame([_probe(s, score_differential=d) for d in range(-14, 15)])
        w = wpm.wp(steps, m)
        jump = np.abs(np.diff(w))
        half = pl.DataFrame([_probe(s, game_seconds_remaining=1801, half_seconds_remaining=1,
                                    receives_2h_kickoff=1, yardline_100=58)])  # fmt: skip
        own, opp = wpm.wp(half, m)[0], 1.0 - wpm.wp(sm.other_side(half), m)[0]
        out.append({"season": s, "score_step": float(jump.max()),
                    "score_step_at": int(np.argmax(jump)) - 14,
                    "halftime_possession": float(own - opp)})  # fmt: skip
    schema = {"season": pl.Int64, "score_step": pl.Float64, "score_step_at": pl.Int64,
              "halftime_possession": pl.Float64}  # fmt: skip
    return pl.DataFrame(out, schema=schema)


def platt_check(fourth: pl.DataFrame) -> pl.DataFrame:
    """For information (never used to choose): real fourth-down tries by distance band and
    era, observed conversion rate vs the mean raw and used P(convert)."""
    g = fourth.filter(pl.col("exclusion").is_null() & (pl.col("chosen") == "go"))
    era = (pl.when(pl.col("season") <= 2014).then(pl.lit("2006-2014"))
           .when(pl.col("season") <= 2022).then(pl.lit("2015-2022"))
           .otherwise(pl.lit("2023+")).alias("era"))  # fmt: skip
    band = (pl.when(pl.col("ydstogo") <= 1).then(pl.lit("1")).when(pl.col("ydstogo") <= 3)
            .then(pl.lit("2-3")).when(pl.col("ydstogo") <= 6).then(pl.lit("4-6"))
            .otherwise(pl.lit("7+")).alias("ydstogo_band"))  # fmt: skip
    return g.with_columns(era, band).group_by("era", "ydstogo_band").agg(
        pl.len().alias("n"),
        pl.col("outcome").is_in(["converted", "touchdown"]).mean().alias("observed"),
        pl.col("p_convert_raw").mean().alias("raw"), pl.col("p_convert").mean().alias("used"),
    ).sort("era", "ydstogo_band")  # fmt: skip


def _table(head: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    return out + ["| " + " | ".join("" if v is None else str(v) for v in r) + " |" for r in rows]


def _pct(x: Any, nd: int = 1) -> str:
    return "" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{100 * x:.{nd}f}%"


def _pts(x: Any, nd: int = 2) -> str:
    """A WP value (0-1) in WP points (0-100)."""
    return "" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{100 * x:.{nd}f}"


def _counts_md(c: dict) -> list[str]:
    f, t = c["fourth"], c["tries"]
    rules = [r for r in RULE_TEXT if f.filter(pl.col("exclusion") == r).height]
    rows = []
    for (season,), part in f.group_by("season", maintain_order=True):
        tp = t.filter(pl.col("season") == season)
        n = part.group_by("exclusion").len()
        ex = dict(n.iter_rows())
        g = part.filter(pl.col("exclusion").is_null())
        gt = tp.filter(pl.col("exclusion").is_null())
        rows.append([season, part.height, *[ex.get(r, 0) for r in rules], g.height,
                     g.filter(pl.col("grade") == "clear").height,
                     g.filter(pl.col("grade") == "toss_up").height, tp.height,
                     tp.height - gt.height, gt.filter(pl.col("grade") == "clear").height,
                     gt.filter(pl.col("grade") == "toss_up").height])  # fmt: skip
    rows.sort(key=lambda r: r[0])
    head = ["Season", "4th downs", *[f"excl: {r}" for r in rules], "Graded", "Clear",
            "Toss-up", "Tries", "Excl", "Clear", "Toss-up"]  # fmt: skip
    text = [f"- `{r}`: {RULE_TEXT[r]}; {f.filter(pl.col('exclusion') == r).height:,} rows"
            for r in rules]  # fmt: skip
    return ["## 1. What was graded", "",
            "Exclusion rules, applied in this order (the first that applies is counted):", "",
            *text, "", *_table(head, rows), ""]  # fmt: skip


def trend(league: pl.DataFrame, col: str = "wp_lost_per_team_game") -> dict[str, Any]:
    """Least-squares slope per season of ``col`` over the complete seasons (2006-2025) and
    the means of the first and last five."""
    from twm.modules.decisions import wp as wpm

    f = league.filter(pl.col("season").is_in(list(wpm.TEST_SEASONS))).sort("season")
    if f.height < 2:
        return {}
    x, y = f.get_column("season").to_numpy(), f.get_column(col).to_numpy()
    slope = float(np.polyfit(x, y, 1)[0])
    return {"slope": slope, "first": float(y[:5].mean()), "last": float(y[-5:].mean()),
            "seasons": (int(x[0]), int(x[-1]))}  # fmt: skip


def _league_md(c: dict) -> list[str]:
    lg = c["league"]
    rows = [[r["season"], r["clear"], r["clear_wrong"], _pts(r["wp_lost"], 1),
             _pts(r["wp_lost_per_team_game"]), _pts(r["wp_lost_per_clear"]), _pct(r["go_rate"]),
             _pct(r["recommended_go_rate"]), _pct(r["aggressiveness"]), _pct(r["two_point_rate"]),
             _pct(r["recommended_two_point_rate"]), r["tries_clear"], _pts(r["tries_wp_lost"], 1)]
            for r in lg.iter_rows(named=True)]  # fmt: skip
    head = ["Season", "Clear 4th", "Wrong", "WP lost (pts)", "Per team-game", "Per clear",
            "Go rate", "Recommended go", "Go when go clearly best", "2-pt rate",
            "Recommended 2-pt", "Clear tries", "Tries WP lost"]  # fmt: skip
    tr = trend(lg)
    line = []
    if tr:
        a, b = tr["seasons"]
        line = [f"**Trend ({a}-{b})**: fourth-down WP lost per team-game {_pts(tr['first'])} "
                f"WP points in the first five seasons, {_pts(tr['last'])} in the last five; "
                f"least-squares slope {100 * tr['slope']:+.3f} points per season. The swings "
                "between neighbouring seasons (each season is graded by its own fold models) "
                "are as large as the trend: read it with section 6.", ""]  # fmt: skip
    return ["## 2. The league, season by season", "",
            "WP lost counts clear decisions only (WP points: 1.00 = one percentage point of "
            "win probability). The go rate and the recommended go rate are over every graded "
            "fourth down (toss-ups included); 'go when go clearly best' is the aggressiveness "
            "index. Two-point columns: share of tries that went for two vs the share where "
            "two had the higher WP.", "", *line, *_table(head, rows), ""]  # fmt: skip


def _coach_rows(b: pl.DataFrame) -> list[list[Any]]:
    return [[r["rank"], r["coach"], r["team"], r["games"], r["fourth_graded"], r["fourth_wrong"],
             _pts(r["fourth_wp_lost"]), r["two_point_graded"], _pts(r["two_point_wp_lost"]),
             _pct(r["aggressiveness"], 0), _pts(r["wp_lost_per_game"])]
            for r in b.iter_rows(named=True)]  # fmt: skip


def _coaches_md(c: dict, season: int) -> list[str]:
    games = c["coach_season"].filter(pl.col("season") == season).get_column("games")
    need = min(int(c["cfg"].leaderboard_min_games), int(games.max() or 0))
    b = coach.leaderboard(c["coach_season"], season, need)
    head = ["Rank", "Coach", "Team", "Games", "Clear 4th", "Wrong", "4th WP lost",
            "Clear tries", "Tries WP lost", "Go when clearly best", "WP lost per game"]  # fmt: skip
    if b.height <= 10:
        body = _table(head, _coach_rows(b))
    else:
        body = [*_table(head, _coach_rows(b.head(5))), "", "Bottom five:", "",
                *_table(head, _coach_rows(b.tail(5)))]  # fmt: skip
    return [f"### {season} (coaches with at least {need} games; least WP lost per game first)",
            "", *body, ""]  # fmt: skip


def _worst_md(c: dict, season: int, n: int = 10) -> list[str]:
    w = coach.worst_calls(c["fourth"], n, season)
    rows = [[r["week"], f"{r['posteam']} v {r['defteam']}", r["coach"], r["clock"],
             f"{r['score_differential']:+d}", f"4th & {r['ydstogo']}, {r['field']}", r["chosen"],
             r["recommended"], _pts(r["wp_go"], 1), _pts(r["wp_fg"], 1) if r["fg_available"]
             else "-", _pts(r["wp_punt"], 1) if r["punt_available"] else "-",
             _pts(r["wp_lost"], 1), r["outcome"]] for r in w.iter_rows(named=True)]  # fmt: skip
    head = ["Wk", "Game", "Coach", "Clock", "Score", "Situation", "Chose", "Best", "WP go",
            "WP FG", "WP punt", "WP lost", "What happened"]  # fmt: skip
    return [f"### Worst fourth-down calls of {season}", "", *_table(head, rows), ""]


def _inputs_md(c: dict) -> list[str]:
    rows = []
    f = c["fourth"]
    for season, s in sorted(c["summaries"].items()):
        k = f.filter(pl.col("season") == season).group_by("kick_source").agg(
            pl.col("kick_yardline_100").mean()).sort("kick_source")  # fmt: skip
        spot = ", ".join(f"{src}: own {100 - v:.1f}" for src, v in k.iter_rows() if v is not None)
        p = s["platt"]
        kept = (f"kept ({p['val_logloss_platt']:.4f} vs {p['val_logloss_raw']:.4f})"
                if p["kept"] else (f"not kept ({p['val_logloss_platt']:.4f} vs "
                                   f"{p['val_logloss_raw']:.4f})" if "val_logloss_raw" in p
                                   else "none (no earlier folds)"))  # fmt: skip
        rows.append([season, spot, s["fg_max_distance"], s["punt_min_yardline"],
                     f"{s['fg_runoff_make']:.0f} / {s['fg_runoff_miss']:.0f}", kept])  # fmt: skip
    head = ["Season", "Kickoff spot (mean of the games' spots, by source)", "FG range (yd)",
            "Punt from yardline_100 >=", "FG runoff make / miss (s)",
            "4th-down Platt on S-1 (log loss with vs without)"]  # fmt: skip
    return ["## 4. Inputs that are not models (point-in-time, stored on every row)", "",
            *_table(head, rows), ""]  # fmt: skip


def _checks_md(c: dict) -> list[str]:
    out = ["## 5. Checks", ""]
    v = c["verify"]
    if v is not None:
        res = "every value identical" if not v["mismatches"] else f"MISMATCH: {v['mismatches']}"
        out += [f"- **Stored inputs reproduce the grades**: {v['rows']:,} stored rows (up to "
                f"{VERIFY_SAMPLE} per season and kind) regraded from their stored inputs with "
                f"the models their version columns name: {res} (`grade.verify`).", ""]  # fmt: skip
    cons = ", ".join(f"{r['chosen']} {100 * r['d']:+.2f} ({r['n']:,})"
                     for r in c["consistency"].iter_rows(named=True))  # fmt: skip
    out += ["- **The chosen option's WP vs the WP before the snap** (the WP model already "
            f"expects what teams usually do; mean difference in WP points): {cons}.", "",
            "**Fourth-down conversion: observed vs predicted** (real tries; information only, "
            "never used to choose anything):", ""]  # fmt: skip
    rows = [[r["era"], r["ydstogo_band"], r["n"], _pct(r["observed"]), _pct(r["raw"]),
             _pct(r["used"])] for r in c["platt"].iter_rows(named=True)]  # fmt: skip
    return out + [*_table(["Seasons", "Yards to go", "Tries", "Observed", "Model (raw)",
                           "Model (used)"], rows), ""]  # fmt: skip


def _wp_md(c: dict) -> list[str]:
    """Section 6: the WP model's smoothness per fold (G1b limits and G3's two original checks)
    and, when the grades made with G1's WP folds were frozen, what the regrade changed."""
    from twm.modules.decisions import wp_select as ws
    from twm.modules.decisions import wp_smooth as wsm

    d, fs = c["wp_diag"], c["fold_smooth"]
    lim = wsm.THRESHOLDS
    by = {r["season"]: r for r in fs.iter_rows(named=True)} if fs.height else {}
    rows = []
    for r in d.iter_rows(named=True):
        g = by.get(r["season"], {})
        rows.append([r["season"], *[f"{g[k]:.1f}" if k in g else "-" for k in
                                    ("score_step_h1", "score_step_h2", "curvature",
                                     "halftime_possession", "possession_2min")],
                     _pts(r["score_step"], 1), _pts(r["halftime_possession"], 1),
                     ("yes" if g.get("meets") else "no") if g else "-"])  # fmt: skip
    n_ok = int(fs.get_column("meets").sum()) if fs.height else 0
    head = ["Fold", f"1-pt step, 1st half (<= {lim['score_step_h1']:g})",
            f"1-pt step, 2nd half (<= {lim['score_step_h2']:g})",
            f"Curvature (<= {lim['curvature']:g})",
            f"Halftime possession (<= {lim['halftime_possession']:g})",
            "Ball 30-120 s before half (reported)",
            "G3 check: score step", "G3 check: halftime possession",
            "Meets the limits"]  # fmt: skip
    verdict = (f"All {fs.height} fold models meet every limit." if _smooth_ok(c) else
               f"Only {n_ok} of {fs.height} fold models meet every limit: treat the grades of "
               "the others as provisional.")  # fmt: skip
    out = ["## 6. The WP model's smoothness (G1b) and what the regrade changed", "",
           "Every grade is a difference between the WPs of hypothetical states, so it inherits "
           "any unevenness of the WP model. G3 found G1's model uneven (one point of score "
           "worth 6-19 WP points in the first quarter; the ball worth up to 20 points one "
           f"second before halftime). G1b replaced it with **{ws.CHOSEN}** "
           f"({ws.chosen().description}), chosen on the validation seasons 2004-2005 only "
           "(reports/decisions/wp_backtest.md, 'Smoothness'; limits and reasons: "
           "docs/decision_metrics.md). Per fold model, in WP points (model properties on "
           "synthetic states, not grades): the G1b metrics and G3's two original checks "
           "(a team receiving a first-quarter kickoff; the ball at its own 42 one second "
           "before halftime, tied).", "", *_table(head, rows), "", verdict, ""]  # fmt: skip
    return out + _compare_md(c)


def _compare_md(c: dict) -> list[str]:
    cp = c.get("compare")
    if cp is None:
        return ["No frozen grades from G1's WP folds (data/decisions/graded_g1/) to compare "
                "with.", ""]  # fmt: skip
    t, late, f4 = cp["tries"], cp["late_h1"], cp["fourth"]
    tb, ta = trend(cp["league_before"]), trend(cp["league_after"])
    rk = cp["ranking"]
    out = ["### Before (G1's WP folds) vs after (the smoothed folds)", "",
           f"Same decisions, same sub-models and inputs; only the WP model changed "
           f"({f4['graded']:,} graded fourth downs and {t['matched']:,} tries matched).", "",
           f"- **Two-point decisions**: clear 'mistakes' {t['mistakes_before']:,} -> "
           f"{t['mistakes_after']:,}; of them first-quarter kicks at a 6-point lead "
           f"{t['q1_six_before']:,} -> {t['q1_six_after']:,}; their WP lost "
           f"{100 * t['lost_before']:.0f} -> {100 * t['lost_after']:.0f} points. "
           f"{t['changed']:,} of {t['matched']:,} try grades changed (clear vs toss-up, or the "
           "recommended option).",
           f"- **Fourth downs in the last 2:00 of the first half** ({late['graded']:,} graded): "
           f"{late['changed']:,} grades changed; clear {late['clear_before']:,} -> "
           f"{late['clear_after']:,}; clearly 'go' {late['go_before']:,} -> {late['go_after']:,}; "
           f"WP lost on clear calls {100 * late['lost_before']:.0f} -> "
           f"{100 * late['lost_after']:.0f} points.",
           f"- **All fourth downs**: {f4['changed']:,} of {f4['graded']:,} grades changed; clear "
           f"{f4['clear_before']:,} -> {f4['clear_after']:,}."]  # fmt: skip
    if tb and ta:
        out.append(f"- **League trend** (fourth-down WP lost per team-game, first five -> last "
                   f"five seasons; slope per season): before {_pts(tb['first'])} -> "
                   f"{_pts(tb['last'])} ({100 * tb['slope']:+.3f}); after {_pts(ta['first'])} -> "
                   f"{_pts(ta['last'])} ({100 * ta['slope']:+.3f}).")  # fmt: skip
    j = rk["table"]
    if j.height:
        rho = f"{rk['spearman']:.2f}" if rk["spearman"] is not None else "-"
        moves = j.with_columns((pl.col("rank_before") - pl.col("rank")).alias("up"))
        big = moves.sort(pl.col("up").abs(), "coach", descending=[True, False]).head(3)
        mv = "; ".join(f"{r['coach']} {r['rank_before']} -> {r['rank']}"
                       for r in big.iter_rows(named=True))  # fmt: skip
        out += [f"- **{rk['season']} coach ranking**: rank correlation before vs after {rho}; "
                f"biggest moves: {mv}.", "",
                *_table(["Rank now", "Coach", "Team", "Rank before", "WP lost per game now",
                         "Before"],
                        [[r["rank"], r["coach"], r["team"], r["rank_before"],
                          _pts(r["wp_lost_per_game"]), _pts(r["per_game_before"])]
                         for r in j.iter_rows(named=True)])]  # fmt: skip
    return [*out, ""]


def fold_smoothness(seasons, wp_dir: Path | None = None) -> pl.DataFrame:
    """The G1b smoothness metrics of each season's WP fold model (from its fold summary in
    ``wp_dir``, default data/decisions/wp_backtest)."""
    from twm.modules.decisions import wp as wpm
    from twm.modules.decisions import wp_smooth as wsm

    out = []
    for s in sorted(seasons):
        f = (wp_dir if wp_dir is not None else wpm.backtest_dir()) / f"fold_{s}.json"
        sm = json.loads(f.read_text()).get("smoothness") if f.exists() else None
        if sm:
            out.append({"season": s, **{k: float(sm[k]) for k in wsm.THRESHOLDS},
                        "possession_2min": float(sm.get("possession_2min", float("nan"))),
                        "meets": not wsm.failures(sm)})  # fmt: skip
    return pl.DataFrame(out)


def _smooth_ok(c: dict) -> bool:
    fs = c.get("fold_smooth")
    return fs is not None and fs.height > 0 and bool(fs.get_column("meets").all())


LIMITS = [
    "A touchdown inside a fourth-down option counts 7 points (extra point assumed good); "
    "after any score the other team starts at the season's measured kickoff spot (section 4).",
    "Clock: a go uses the conversion model's median snap-to-snap time, a scoring play plus "
    "its kickoff the field goal's median make-to-next-snap time, a punt the punt model's; "
    "late in a half real teams hurry, so these over-run the clock there.",
    "League-wide models: no kicker, offense or defense strength beyond the closing spread; "
    "field goals of 55+ yards are under-predicted in 2023-2025 (G2, no recency weights).",
    "Overtime is priced by the same WP model (its rules changed in 2022 and 2024).",
    "Rule settings (not model choices): `end_of_half_seconds` = 10 was set from the play-type "
    "mix at the end of halves (punts become rare under 7 s) and the punt-range quantile from "
    "the punting-yardline distribution, both read over 2006-2026 before any grade existed; "
    "the per-season ranges themselves use the seasons before S only.",
    "Honesty note (from G2): the 10/5-season training windows of the conversion and field-goal "
    "models were added after a first backtest; each fold still picks its window on S-1 only.",
    "Honesty note (G1b): the smoothness limits were fixed before any fix was tried, and every "
    "candidate was chosen on the 2004-05 validation seasons only; but two later candidate rounds "
    "(the late-game hand-over and the redefined drive value) were prompted by looking at regraded "
    "seasons, and the pooled test log loss of a first refit (.4484) was seen before them. The "
    "2006-2025 numbers are therefore slightly optimistic; 2026 is the first clean test.",
]


def render(c: dict) -> str:
    """The markdown report."""
    from twm.modules.decisions import wp as wpm

    seasons = sorted(c["summaries"])
    done = [s for s in seasons if s in wpm.TEST_SEASONS]
    cfg = c["cfg"]
    lines = [
        "# Fourth downs and two-point tries: the grades (G3, regraded in G1b)", "",
        "Generated by `uv run twm decisions grade` (code: src/twm/modules/decisions/{grade_inputs,"
        "grade,coach,decisions_report}.py; every rule: docs/decision_metrics.md). Each season S "
        f"({seasons[0]}-{seasons[-1]}) is graded by the fold models that learned from seasons "
        "before S only (WP: G1, smoothed in G1b; conversion, field goal, punt, try rates: G2). "
        "A decision is "
        f"graded (**clear**) only when the best option's WP beats the second best by more than "
        f"{100 * cfg.toss_up_margin:.1f} WP points (`decisions.toss_up_margin`); otherwise it is "
        "a **toss-up**, counted but not graded. Every row is stored with all its inputs under "
        "data/decisions/graded/. " + _header_note(c), "",
        *_counts_md(c), *_league_md(c), "## 3. Coaches", "",
        "Credited to the head coach of the team with the ball in that game "
        "(`fact_game.home_coach` / `away_coach`). Full tables per coach-season: the CSV.", "",
    ]  # fmt: skip
    for s in [*done[-1:], *[x for x in seasons if x not in wpm.TEST_SEASONS]]:
        lines += _coaches_md(c, s)
    if done:
        lines += _worst_md(c, done[-1])
    lines += [*_inputs_md(c), *_checks_md(c), *_wp_md(c), "## 7. Limitations", "",
              *[f"- {x}" for x in LIMITS], ""]  # fmt: skip
    return "\n".join(lines)


def _header_note(c: dict) -> str:
    if _smooth_ok(c):
        return ("The WP model was smoothed in G1b (section 6: every fold model meets the "
                "smoothness limits; what the regrade changed).")  # fmt: skip
    return ("**Read section 6 first**: some WP fold models break the smoothness limits, so "
            "part of their grades can be artifacts.")  # fmt: skip


CSV_COLUMNS = ("table", "season", "coach", "team", "metric", "value")


def csv_rows(c: dict) -> pl.DataFrame:
    """Tidy rows: the league per season, every coach-season, the WP diagnostics."""

    def melt(f: pl.DataFrame, table: str, ids: list[str]) -> pl.DataFrame:
        vals = [x for x in f.columns if x not in ids and f.schema[x].is_numeric()]
        long = f.select(*ids, *[pl.col(v).cast(pl.Float64) for v in vals]).unpivot(
            index=ids, variable_name="metric", value_name="value")  # fmt: skip
        for col in ("coach", "team"):
            if col not in long.columns:
                long = long.with_columns(pl.lit(None, pl.String).alias(col))
        return long.with_columns(pl.lit(table).alias("table"),
                                 pl.col("season").cast(pl.Int32)).select(CSV_COLUMNS)  # fmt: skip

    parts = [melt(c["league"], "league", ["season"]),
             melt(c["coach_season"], "coach_season", ["season", "coach", "team"]),
             melt(c["wp_diag"], "wp_diagnostics", ["season"])]  # fmt: skip
    fs = c.get("fold_smooth")
    if fs is not None and fs.height:
        parts.append(melt(fs.with_columns(pl.col("meets").cast(pl.Float64)), "wp_smoothness",
                          ["season"]))  # fmt: skip
    cp = c.get("compare")
    if cp is not None:
        flat = [(f"{k}_{m}", float(v)) for k in ("tries", "late_h1", "fourth")
                for m, v in cp[k].items()]  # fmt: skip
        if cp["ranking"]["spearman"] is not None:
            flat.append(("ranking_spearman", cp["ranking"]["spearman"]))
        parts.append(pl.DataFrame({"table": "regrade_g1b", "season": None, "coach": None,
                                   "team": None, "metric": [k for k, _ in flat],
                                   "value": [v for _, v in flat]},
                                  schema={"table": pl.String, "season": pl.Int32,
                                          "coach": pl.String, "team": pl.String,
                                          "metric": pl.String, "value": pl.Float64}))  # fmt: skip
        parts.append(melt(cp["league_before"], "league_before_g1b", ["season"]))
    return pl.concat(parts).sort("table", "season", "coach", "metric", nulls_last=True)


def write_report(*, out_dir: Path | None = None, paths: tuple[Path, Path] | None = None,
                 models_root: Path | None = None,
                 progress: Callable[[str], None] = print):  # fmt: skip
    """Write the markdown report and the CSV; returns their paths."""
    md, csv = paths if paths is not None else report_paths()
    c = compute(out_dir, models_root=models_root)
    progress(f"report: {c['fourth'].height:,} fourth downs, {c['tries'].height:,} tries, "
             f"{len(c['summaries'])} seasons")  # fmt: skip
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(render(c))
    csv_rows(c).with_columns(pl.col("value").round(6)).write_csv(csv)
    return md, csv
