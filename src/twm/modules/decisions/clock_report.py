"""reports/decisions/clock.md + .csv: the G4 clock-management metrics (PROJECT_SPEC 8.4 item 4).

Everything comes from the stored rows (data/decisions/clock/, ``twm decisions clock``): league
totals per season for each metric, the coach-season table (parallel to G3's coach tables), a
coach leaderboard per metric, the worst cases with their context, the measured constants and
the reproduction check (every stored output recomputed from its stored inputs).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import polars as pl

from twm.modules.decisions import clock as ck

COACH = ("season", "coach")


def report_paths() -> tuple[Path, Path]:
    from twm.config import ROOT

    d = ROOT / "reports" / "decisions"
    return d / "clock.md", d / "clock.csv"


def summaries(out_dir: Path | None = None) -> dict[int, dict[str, Any]]:
    d = out_dir if out_dir is not None else ck.clock_dir()
    return {int(s["season"]): s for s in (json.loads(f.read_text())
                                         for f in sorted(d.glob("season_*.json")))}  # fmt: skip


def coach_season(games: pl.DataFrame, m1: pl.DataFrame, m2: pl.DataFrame,
                 m3: pl.DataFrame) -> pl.DataFrame:  # fmt: skip
    """One row per (season, coach) of ``games`` (team-games): each metric's candidates, cases
    and amounts (metric 1: timeouts left in cases; metric 2: EP and WP left in cases; metric 3:
    seconds wasted). Zeros when a coach had none."""
    base = games.group_by(COACH).agg(pl.col("team").unique().sort().str.join("/"),
                                     pl.col("game_id").n_unique().alias("games"))  # fmt: skip
    case = pl.col("case").fill_null(False)
    a1 = m1.group_by(COACH).agg(
        pl.len().alias("m1_candidates"),
        case.sum().alias("m1_cases"),
        pl.col("timeouts_left").filter(case).sum().alias("m1_timeouts_left"),
    )
    a2 = m2.group_by(COACH).agg(
        pl.len().alias("m2_candidates"), case.sum().alias("m2_cases"),
        pl.col("ep_left").filter(case).sum().alias("m2_ep_left"),
        pl.col("wp_left").filter(case).sum().alias("m2_wp_left"))  # fmt: skip
    a3 = m3.group_by(COACH).agg(
        pl.len().alias("m3_decisive_games"),
        case.sum().alias("m3_cases"),
        pl.col("seconds_wasted").sum().alias("m3_seconds_wasted"),
    )
    out = base
    for a in (a1, a2, a3):
        out = out.join(a, on=list(COACH), how="left")
    nums = [c for c in out.columns if c.startswith("m")]
    return out.with_columns(pl.col(c).fill_null(0) for c in nums).sort(list(COACH))


def league(games: pl.DataFrame, m1: pl.DataFrame, m2: pl.DataFrame, m3: pl.DataFrame,
           summ: dict[int, dict[str, Any]]) -> pl.DataFrame:  # fmt: skip
    """Per season: team-games, each metric's candidates / cases / amounts, the constants."""
    cs = coach_season(games, m1, m2, m3).drop("coach", "team")
    f = cs.group_by("season").agg(pl.all().sum()).sort("season")
    consts = pl.DataFrame([{"season": s, **{k: v.get(k) for k in ck.ci.CONSTANTS}}
                           for s, v in summ.items()]) if summ else None  # fmt: skip
    f = f.rename({"games": "team_games"})
    return f.join(consts, on="season", how="left") if consts is not None else f


def compute(out_dir: Path | None = None, *, cfg: Any = None, verify: bool = True,
            models_root: Path | None = None,
            models: dict | None = None) -> dict[str, Any]:  # fmt: skip
    """Every table of the report from the stored rows."""
    from twm.config import settings

    cfg = cfg if cfg is not None else settings().decisions
    win = ck.load("defense_snaps", out_dir=out_dir)
    pas = ck.load("half_passivity", out_dir=out_dir)
    games = ck.load("team_games", out_dir=out_dir)
    m1, m3 = ck.timeouts_unused(win), ck.seconds_wasted(win)
    m2 = pas.filter(pl.col("exclusion").is_null())
    summ = summaries(out_dir)
    check = None
    if verify:
        check = {"defense_snaps": (win.height, ck.verify(win, "defense_snaps")),
                 "half_passivity": (m2.height, ck.verify(pas, "half_passivity",
                                                         models_root=models_root,
                                                         models=models))}  # fmt: skip
    return {"win": win, "pas": pas, "games": games, "m1": m1, "m2": m2, "m3": m3,
            "summaries": summ, "cfg": cfg, "verify": check,
            "coach_season": coach_season(games, m1, m2, m3),
            "league": league(games, m1, m2, m3, summ)}  # fmt: skip


def leaderboard(cs: pl.DataFrame, cases: str, amount: str, seasons: list[int],
                n: int = 10) -> pl.DataFrame:  # fmt: skip
    """Coaches over ``seasons`` with at least one case, most cases first (then amount): games,
    cases per 100 games, the amount."""
    f = cs.filter(pl.col("season").is_in(seasons)).group_by("coach").agg(
        pl.col("team").unique().sort().str.join("/").str.replace_all(r"/+", "/"),
        pl.col("games").sum(), pl.col(cases).sum(), pl.col(amount).sum())  # fmt: skip
    f = f.filter(pl.col(cases) > 0).with_columns(
        (100 * pl.col(cases) / pl.col("games")).alias("per_100_games")
    )
    return f.sort([cases, amount, "coach"], descending=[True, True, False]).head(n)


def _clock(seconds: Any) -> str:
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"


def _field(y: Any) -> str:
    y = int(y)
    return f"own {100 - y}" if y > 50 else (f"opp {y}" if y < 50 else "midfield")


def _table(head: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    return out + ["| " + " | ".join("" if v is None else str(v) for v in r) + " |" for r in rows]


def _f(x: Any, nd: int = 1) -> str:
    return "" if x is None else f"{x:.{nd}f}"


def _league_md(c: dict) -> list[str]:
    rows = [[r["season"], r["team_games"], r["m1_candidates"], r["m1_cases"],
             r["m1_timeouts_left"], r["m2_candidates"], r["m2_cases"], _f(r["m2_ep_left"]),
             _f(100 * r["m2_wp_left"]), r["m3_decisive_games"], r["m3_cases"],
             _f(r["m3_seconds_wasted"], 0), _f(r.get("kneel_play"), 0),
             _f(r.get("kneel_cycle"), 0), _f(r.get("play_seconds_run"), 0),
             _f(r.get("play_seconds_pass"), 0)]
            for r in c["league"].iter_rows(named=True)]  # fmt: skip
    head = ["Season", "Team-games", "1: cand.", "1: cases", "1: TOs left", "2: cand.",
            "2: cases", "2: EP left", "2: WP left", "3: decisive games", "3: cases",
            "3: seconds", "p", "g", "run s", "pass s"]  # fmt: skip
    return ["## 1. The league, season by season", "",
            "1 = timeouts unused in a lost one-score game (candidates: regulation losses by 1-8 "
            "with the opponent holding the ball to the end from a run-out snap; cases: a timeout "
            "still held). 2 = end-of-half passivity (candidates: passive tails with a decision "
            "snap; cases: EP left >= the threshold; EP in points, WP in WP points, summed over "
            "cases). 3 = late timeouts when trailing (team-games with a decisive interval; "
            "cases: seconds wasted > 0). p, g, run s, pass s: the measured kneel and play "
            "seconds (5 seasons before S).", "", *_table(head, rows), ""]  # fmt: skip


def _m1_md(c: dict, season: int) -> list[str]:
    f = c["m1"].filter((pl.col("season") == season) & pl.col("case")).sort(
        ["timeouts_left", "ro_game_seconds_remaining"], descending=True)  # fmt: skip
    rows = [[r["week"], f"{r['team']} v {r['opp']}", r["coach"], f"{r['team_margin']:+d}",
             f"Q4 {_clock(r['ro_game_seconds_remaining'])}, {r['ro_down']} & {r['ro_ydstogo']}, "
             f"{_field(r['ro_yardline_100'])}, down {r['ro_score_differential']}",
             r["ro_team_t"], f"{r['ro_k_all']:.0f} / {r['ro_k_free']:.0f}", r["timeouts_left"],
             _clock(r["last_clock"]), r["drive_missed_stops"], _f(r["drive_seconds_wasted"], 0)]
            for r in f.iter_rows(named=True)]  # fmt: skip
    head = ["Wk", "Team v opp", "Coach", "Final", "First run-out snap (opponent's ball)",
            "TOs then", "K(d,t) / K(d,0) s", "TOs left", "Last snap", "Missed stops (3)",
            "Seconds wasted (3)"]  # fmt: skip
    note = (
        "A case is a fact (lost with a timeout in hand), not by itself a mistake: 0 missed "
        "stops (metric 3) means the timeouts kept could not have stopped a decisive clock "
        "(e.g. the opponent converted a first down after the others were used)."
    )
    return [f"### {season}: timeouts unused in lost one-score games ({f.height} cases)", "",
            *([note, ""] if rows else []), *(_table(head, rows) if rows else ["None."]),
            ""]  # fmt: skip


def _m2_md(c: dict, seasons: list[int], title: str, n: int = 15) -> list[str]:
    f = c["m2"].filter(pl.col("season").is_in(seasons) & pl.col("case")).sort(
        "ep_left", descending=True)  # fmt: skip
    rows = [[r["season"], r["week"], f"{r['posteam']} v {r['defteam']}", r["coach"],
             f"Q2 {_clock(r['half_seconds_remaining'])}", f"1 & {r['ydstogo']}, "
             f"{_field(r['yardline_100'])}", f"{r['score_differential']:+d}",
             r["posteam_timeouts_remaining"], f"{r['tail_kneels']} kneel / {r['tail_runs']} run",
             _f(r["ep_left"], 2), _f(100 * r["wp_left"], 2)]
            for r in f.head(n).iter_rows(named=True)]  # fmt: skip
    head = ["Season", "Wk", "Team v opp", "Coach", "Clock", "Situation", "Score", "TOs",
            "Tail", "EP left", "WP left"]  # fmt: skip
    return [f"### {title} ({f.height} cases{', top ' + str(n) if f.height > n else ''})", "",
            *(_table(head, rows) if rows else ["None."]), ""]  # fmt: skip


def _m3_md(c: dict, season: int, n: int = 10) -> list[str]:
    f = c["m3"].filter((pl.col("season") == season) & pl.col("case")).sort(
        "seconds_wasted", descending=True)  # fmt: skip
    rows = [[r["week"], f"{r['team']} v {r['opp']}", r["coach"], f"{r['team_margin']:+d}",
             f"Q4 {_clock(r['w_game_seconds_remaining'])}, {r['w_down']} & {r['w_ydstogo']}, "
             f"{_field(r['w_yardline_100'])}, down {r['w_score_differential']}", r["w_team_t"],
             _f(r["w_runoff"], 0), r["missed_stops"], r["counted"], r["timeouts_kept"],
             _f(r["seconds_wasted"], 0)] for r in f.head(n).iter_rows(named=True)]  # fmt: skip
    head = ["Wk", "Team v opp", "Coach", "Final", "Worst missed stop (opponent's snap)", "TOs",
            "Runoff s", "Missed stops", "Counted", "TOs kept", "Seconds wasted"]  # fmt: skip
    return [f"### {season}: seconds wasted with timeouts in hand ({f.height} cases)", "",
            *(_table(head, rows) if rows else ["None."]), ""]  # fmt: skip


BOARDS = (("m1_cases", "m1_timeouts_left", "Timeouts unused in lost one-score games",
           "Timeouts left"),
          ("m2_cases", "m2_ep_left", "End-of-half passivity", "EP left (points)"),
          ("m3_cases", "m3_seconds_wasted", "Late timeouts: seconds wasted",
           "Seconds wasted"))  # fmt: skip


def _boards_md(c: dict, seasons: list[int], label: str) -> list[str]:
    out = [f"### {label}", ""]
    for cases, amount, title, unit in BOARDS:
        b = leaderboard(c["coach_season"], cases, amount, seasons)
        rows = [[i, r["coach"], r["team"], r["games"], r[cases], _f(r["per_100_games"], 2),
                 _f(r[amount], 1)] for i, r in enumerate(b.iter_rows(named=True), 1)]  # fmt: skip
        out += [f"**{title}** (most cases first)", "",
                *(_table(["Rank", "Coach", "Teams", "Games", "Cases", "Per 100 games",
                          unit], rows) if rows else ["None."]), ""]  # fmt: skip
    return out


def _m2_spread(c: dict) -> str:
    g = c["m2"]
    if g.height == 0:
        return ""
    ep, sec = g.get_column("ep_left"), g.get_column("half_seconds_remaining")
    return (
        f"Graded candidates: {g.height}; EP left median {ep.median():.2f}, 90th percentile "
        f"{ep.quantile(0.9):.2f}, max {ep.max():.2f} points; decision snap at a median "
        f"{sec.median():.0f} s before halftime (definition: >= 40 s)."
    )


def _checks_md(c: dict) -> list[str]:
    v, cfg = c["verify"], c["cfg"].clock
    out = ["## 5. Inputs and checks", "",
           f"- Config (`decisions.clock`, fixed before grading): one-score margin "
           f"{cfg.one_score_margin}, final window {cfg.final_window_seconds} s, constants from "
           f"the {cfg.runoff_seasons} seasons before S, clock ran >= {cfg.clock_ran_min_seconds}"
           f" s, passivity >= {cfg.passivity_min_seconds} s, >= {cfg.passivity_min_timeouts} "
           f"timeout, EP >= {cfg.passivity_min_ep} point.",
           f"- Metric 2 candidates excluded as `missing_state`: "
           f"{c['pas'].filter(pl.col('exclusion').is_not_null()).height}. " + _m2_spread(c),
           "- Correction (disclosed in docs/decision_metrics.md): metric 3's first written "
           "'decisive' formula, K(d', t) < clock <= K(d', 0), left out the gap a timeout called "
           "right after the play stops; corrected to K(d', t - 1) < clock <= K(d', -1) after a "
           "hand check of one 2025 case in a test run, before the full grading."]  # fmt: skip
    if v is not None:
        for kind, (n, bad) in v.items():
            out.append(f"- Reproduction ({kind}): {n:,} stored rows recomputed from their "
                       f"stored inputs; differing outputs: {bad or 'none'}.")  # fmt: skip
    return [*out, ""]


LIMITS = [
    "The kneel arithmetic assumes the offense kneels (the fewest seconds per snap) and that "
    "no other stoppage comes; teams that run instead burn more clock, so 'could run out the "
    "clock' is conservative for metric 1 and 'decisive' is generous for metric 3.",
    "Metric 2 reads G1b's half_value (net points from a first-half 1st down to halftime, "
    "1999-2005); it averages passive and attacking teams alike, so EP left is understated. "
    "2nd- and 3rd-down tails are outside the definition (the table is for 1st downs).",
    "Metric 3 counts only decisive intervals (the opponent could kneel out otherwise): clock "
    "lost earlier in the two minutes, before the two-minute warning or when two scores behind "
    "is not graded. The seconds are ex ante: the drive's outcome is context only.",
    "Timeouts are read from pre-snap counts; a timeout called after the game's last snap is "
    "invisible (it could not change anything).",
]


def render(c: dict) -> str:
    """The markdown report."""
    from twm.modules.decisions import wp as wpm

    seasons = sorted(c["summaries"])
    done = [s for s in seasons if s in wpm.TEST_SEASONS]
    last = done[-1] if done else seasons[-1]
    current = [s for s in seasons if s not in wpm.TEST_SEASONS]
    lines = [
        "# Clock management: three limited metrics (G4)", "",
        "Generated by `uv run twm decisions clock` (code: src/twm/modules/decisions/{clock_inputs,"
        "clock,clock_report}.py). Every definition, written before any season was graded, is in "
        "docs/decision_metrics.md (\"Clock management\"); thresholds are in config/settings.yaml "
        f"(`decisions.clock`). Seasons {seasons[0]}-{seasons[-1]}; each case is credited to the "
        "head coach of the team it describes in that game. Every stored row keeps its inputs "
        "(data/decisions/clock/); section 5 recomputes every output from them.", "",
        *_league_md(c), "## 2. Worst cases", "",
        *_m1_md(c, last), *_m2_md(c, [last], f"{last}: end-of-half passivity"),
        *_m2_md(c, done, f"{done[0] if done else last}-{last}: end-of-half passivity, most EP "
                         "left"),
        *_m3_md(c, last),
    ]  # fmt: skip
    for s in current:
        lines += [*_m1_md(c, s), *_m2_md(c, [s], f"{s}: end-of-half passivity"), *_m3_md(c, s)]
    lines += ["## 3. Coach leaderboards", "",
              "Credited to the head coach (`fact_game.home_coach` / `away_coach`). The full "
              "coach-season table (parallel to G3's) is in the CSV (`table` = coach_season).", "",
              *_boards_md(c, done, f"{done[0]}-{last} (all complete seasons)" if done else ""),
              *_boards_md(c, [last], f"{last}"),
              "## 4. Definitions in one line each", "",
              "- 1: regulation loss by 1-8; the opponent kept the ball to the end from a snap "
              "where K(d, t) < clock <= K(d, 0); the team still held a timeout at the end.",
              "- 2: a first half ended by kneels/designed runs from a 1st down with >= 40 s and "
              ">= 1 timeout (no timeout called, no pass); a case when half_value >= 1.0 point.",
              "- 3: after an opponent play in the final 2:00 while down 1-8, decisive "
              "(K(d', t - 1) < clock at the play's end <= K(d', -1)), no timeout, runoff >= "
              "10 s; "
              "the first k such runoffs of the drive count, k = timeouts kept at its end.", "",
              *_checks_md(c), "## 6. Limitations", "", *[f"- {x}" for x in LIMITS], ""]  # fmt: skip
    return "\n".join(lines)


CSV_COLUMNS = ("table", "season", "coach", "team", "metric", "value")


def csv_rows(c: dict) -> pl.DataFrame:
    """Tidy rows: the league per season and every coach-season."""

    def melt(f: pl.DataFrame, table: str, ids: list[str]) -> pl.DataFrame:
        vals = [x for x in f.columns if x not in ids and f.schema[x].is_numeric()]
        long = f.select(*ids, *[pl.col(v).cast(pl.Float64) for v in vals]).unpivot(
            index=ids, variable_name="metric", value_name="value")  # fmt: skip
        for col in ("coach", "team"):
            if col not in long.columns:
                long = long.with_columns(pl.lit(None, pl.String).alias(col))
        return long.with_columns(pl.lit(table).alias("table"),
                                 pl.col("season").cast(pl.Int32)).select(CSV_COLUMNS)  # fmt: skip

    return pl.concat([melt(c["league"], "league", ["season"]),
                      melt(c["coach_season"], "coach_season", ["season", "coach", "team"])]
                     ).sort("table", "season", "coach", "metric", nulls_last=True)  # fmt: skip


def write_report(*, out_dir: Path | None = None, paths: tuple[Path, Path] | None = None,
                 models_root: Path | None = None, models: dict | None = None,
                 progress: Callable[[str], None] = print):  # fmt: skip
    """Write the markdown report and the CSV; returns their paths."""
    md, csv = paths if paths is not None else report_paths()
    c = compute(out_dir, models_root=models_root, models=models)
    progress(f"report: {c['win'].height:,} window rows, {c['pas'].height} half-end candidates, "
             f"{len(c['summaries'])} seasons")  # fmt: skip
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(render(c))
    csv_rows(c).with_columns(pl.col("value").round(6)).write_csv(csv)
    return md, csv
