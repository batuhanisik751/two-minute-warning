"""The stability report (``twm regression stability``, step D2): reports/regression_watch/
stability.md and a CSV with every number in it.

The numbers come from :mod:`.stability` (docs/regression_watch.md explains them for a beginner):
split-half correlations of every metric by position (odd/even games and first/second half, with
bootstrap intervals), the shrinkage table (reliability of FPOE/game after g games) estimated on
all the seasons, and the same table on shorter windows to show how stable it is. The output is
deterministic (seeded bootstrap, fixed decimals; the only time in it is the warehouse's
``built_at``).
"""

from __future__ import annotations

import csv
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from twm.config import FANTASY_POSITIONS
from twm.modules.regression_watch import stability as st
from twm.modules.regression_watch.production import XFP_TITLES

GS = (4, 8, 12)  # the games shown in the shrinkage tables
CSV_COLUMNS = (
    "table", "window", "split", "position", "metric", "g", "n", "value", "lo", "hi",
    "var_signal", "var_noise", "prior_mean",
)  # fmt: skip


@dataclass(frozen=True)
class StabilityReport:
    markdown: str
    csv_rows: list[dict[str, object]]
    summary: list[str]
    split_half: pl.DataFrame
    shrinkage: pl.DataFrame  # estimated on every season of the report


def windows(first: int, last: int) -> list[tuple[int, int]]:
    """Season windows for the stability check: three consecutive blocks, then the growing
    windows a walk-forward backtest would use for a test season after them (first .. last-10,
    first .. last-5, first .. last-1)."""
    n = last - first + 1
    out: list[tuple[int, int]] = []
    if n >= 3:
        cut1, cut2 = first + round(n / 3) - 1, first + round(2 * n / 3) - 1
        out += [(first, cut1), (cut1 + 1, cut2), (cut2 + 1, last)]
    out += [(first, last - k) for k in (10, 5, 1) if last - k >= first]
    seen: set[tuple[int, int]] = set()
    return [w for w in out if not (w in seen or seen.add(w))]


def _num(v: object, digits: int = 6) -> object:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return round(float(v), digits) if isinstance(v, float) else v


def _label(first: int, last: int) -> str:
    return f"{first}-{last}" if first != last else str(first)


def csv_rows(
    split_half: pl.DataFrame,
    tables: dict[tuple[int, int], pl.DataFrame],
    intervals: pl.DataFrame,
    full: tuple[int, int],
) -> list[dict[str, object]]:
    """Every number of the report, one per row (split-half r; r(g) for every window and g,
    with the bootstrap interval where computed)."""
    rows: list[dict[str, object]] = []
    for r in split_half.iter_rows(named=True):
        rows.append({"table": "split_half", "window": _label(*full), "split": r["split"],
                     "position": r["position"], "metric": r["metric"], "g": "", "n": r["n"],
                     "value": r["r"], "lo": r["lo"], "hi": r["hi"]})  # fmt: skip
    ci = {(r["position"], r["metric"], r["g"]): r for r in intervals.iter_rows(named=True)}
    for w, table in tables.items():
        for r in table.iter_rows(named=True):
            b = ci.get((r["position"], r["metric"], r["g"]), {}) if w == full else {}
            rows.append({"table": "shrinkage", "window": _label(*w), "split": "odd_even",
                         "position": r["position"], "metric": r["metric"], "g": r["g"],
                         "n": r["n"], "value": r["reliability"], "lo": b.get("lo"),
                         "hi": b.get("hi"), "var_signal": r["var_signal"],
                         "var_noise": r["var_noise"], "prior_mean": r["prior_mean"]})  # fmt: skip
    return [{c: _num(row.get(c)) for c in CSV_COLUMNS} for row in rows]


def _t(header: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return out + ["| " + " | ".join(str(c) for c in r) + " |" for r in rows] + [""]


def _f(v: object, digits: int = 2) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    return f"{float(v):.{digits}f}"


def _ci(v: object, lo: object, hi: object, digits: int = 2) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    return f"{_f(v, digits)} ({_f(lo, digits)} to {_f(hi, digits)})"


def _cell(sh: pl.DataFrame, split: str, pos: str, metric: str, *, with_n: bool = False) -> str:
    row = sh.filter(
        (pl.col("split") == split) & (pl.col("position") == pos) & (pl.col("metric") == metric)
    )
    if row.is_empty():
        return "-"
    r = row.row(0, named=True)
    text = _ci(r["r"], r["lo"], r["hi"])
    return f"{text}, n {r['n']:,}" if with_n and text != "-" else text


def _md_headline(sh: pl.DataFrame, split: str) -> list[str]:
    """xFP vs FPOE vs points per game, all plays and without garbage time."""
    out = []
    for ng, title in (("", "all plays"), ("_ng", "without garbage time")):
        rows = []
        for pos in FANTASY_POSITIONS:
            n = sh.filter((pl.col("split") == split) & (pl.col("position") == pos)
                          & (pl.col("metric") == "xfp"))["n"]  # fmt: skip
            points = "points_ng" if ng else "fantasy_points"
            rows.append((pos, f"{int(n[0]):,}" if len(n) else "-",
                         _cell(sh, split, pos, f"xfp{ng}"), _cell(sh, split, pos, f"fpoe{ng}"),
                         _cell(sh, split, pos, points)))  # fmt: skip
        out += [f"**{title.capitalize()}**", ""]
        out += _t(["position", "player-seasons", "xFP/game (opportunity)",
                   "FPOE/game (efficiency)", "points/game"], rows)  # fmt: skip
    return out


def _md_components(sh: pl.DataFrame) -> list[str]:
    rows = []
    for pos in FANTASY_POSITIONS:
        rate = "completion_rate_over_expected" if pos == "QB" else "catch_rate_over_expected"
        rows.append((pos, _cell(sh, "odd_even", pos, "td_rate_over_expected", with_n=True),
                     _cell(sh, "odd_even", pos, rate, with_n=True),
                     _cell(sh, "odd_even", pos, "yac_over_expected", with_n=True)))  # fmt: skip
    return _t(["position", "TD rate over expected", "catch rate over expected (QB: completion "
               "rate)", "YAC over expected per catch"], rows)  # fmt: skip


def _rel(table: pl.DataFrame, pos: str, metric: str, g: int) -> dict[str, object]:
    row = table.filter(
        (pl.col("position") == pos) & (pl.col("metric") == metric) & (pl.col("g") == g)
    )
    return row.row(0, named=True) if not row.is_empty() else {}


def _md_shrinkage(table: pl.DataFrame, intervals: pl.DataFrame, metric: str) -> list[str]:
    """r(g) at GS (with intervals) and 17, the variances and the games for half weight."""
    ci = {(r["position"], r["g"]): r for r in intervals.iter_rows(named=True)}
    rows = []
    for pos in FANTASY_POSITIONS:
        base = _rel(table, pos, metric, 1)
        if not base or base.get("reliability") is None:
            rows.append((pos, "-", "-", "-", "-", "-", *["-"] * (len(GS) + 1)))
            continue
        vs, vn = base["var_signal"], base["var_noise"]
        half = _f(vn / vs, 0) if vs and vs > 0 else "never"
        cells = []
        for g in GS:
            b = ci.get((pos, g), {})
            cells.append(_ci(_rel(table, pos, metric, g)["reliability"], b.get("lo"),
                             b.get("hi")))  # fmt: skip
        r17 = _rel(table, pos, metric, st.MAX_G).get("reliability")
        rows.append((pos, f"{base['n']:,}", _f(vs), _f(vn, 1), _f(base["prior_mean"]), half,
                     *cells, _f(r17)))  # fmt: skip
    head = ["position", "player-seasons", "signal variance", "noise variance per game",
            "average per game", "games for half weight",
            *[f"r({g})" for g in GS], f"r({st.MAX_G})"]  # fmt: skip
    return _t(head, rows)


def _md_windows(tables: dict[tuple[int, int], pl.DataFrame], metric: str, g: int) -> list[str]:
    rows = []
    for w, table in tables.items():
        cells = []
        for pos in FANTASY_POSITIONS:
            r = _rel(table, pos, metric, g)
            cells.append(f"{_f(r.get('reliability'))} (n {r['n']:,})" if r else "-")
        rows.append((_label(*w), *cells))
    return _t(["seasons", *FANTASY_POSITIONS], rows)


LIMITS = [
    "- **Who is in it.** Every player-season with 8+ games, backups included. Part of why "
    "opportunity looks so sticky is simply that starters stay starters and backups stay "
    "backups; efficiency has no such built-in spread.",
    "- **One noise size per position.** The model gives every player of a position the same "
    "per-game luck, but a player with 10 targets a game has bigger FPOE swings than one with 3. "
    "D3 can test whether a per-opportunity version predicts better.",
    "- **Rates need chances.** A rate over expected counts a player-season only with at least "
    f"{st.MIN_DENOMINATOR} chances in each half (targets for the catch rate, catches for YAC, "
    "passes, carries and targets for the TD rate), so its "
    "n is smaller than the per-game metrics' n.",
    "- **No garbage-time split for the parts.** The parts (touchdowns, catches, YAC) come from "
    "ffopportunity's weekly rows, which do not separate garbage time; only xFP, FPOE and points "
    "have a no-garbage-time version.",
    "- **Model outputs (PROJECT_SPEC 6.3).** xFP comes from ffopportunity's models, trained on "
    "many seasons including later ones; the garbage-time flag from nflfastR's win probability. "
    "A mild, known leak, the same as in the rest of Regression Watch P1.",
    "- **Resampling player-seasons.** The intervals resample player-seasons, as if each were "
    "independent; the same player appears in several seasons, so the true intervals are a "
    "little wider.",
    "- **Seasons.** 2006-2008 are left out: receiver xFP is unusable there (targets of "
    "incomplete passes are missing upstream, docs/regression_watch.md).",
]


# With the own walk-forward xFP (step H6-b2) two limits read differently.
OWN_LIMITS = {
    "- **No garbage-time split for the parts.**": (
        "- **No garbage-time split for the parts.** The parts (touchdowns, catches, YAC) are "
        "summed per game without separating garbage time; only xFP, FPOE and points have a "
        "no-garbage-time version."),
    "- **Model outputs (PROJECT_SPEC 6.3).**": (
        "- **Model outputs (PROJECT_SPEC 6.3).** xFP is Regression Watch's own walk-forward "
        "xFP (step H6-b2): every season's expectations come from models trained on the seasons "
        "before it only, so no week's xFP knows the future. The garbage-time flag still comes "
        "from nflfastR's win probability, trained on many seasons: a mild, known leak."),
}  # fmt: skip


def limits(xfp_source: str) -> list[str]:
    """The report's limits for the xFP source ('ffopportunity': :data:`LIMITS` as written)."""
    if xfp_source == "ffopportunity":
        return list(LIMITS)
    return [next((v for k, v in OWN_LIMITS.items() if t.startswith(k)), t) for t in LIMITS]


def _built_at(db: Path | str) -> str:
    from twm.warehouse.build import connect

    con = connect(db, read_only=True)
    try:
        built = con.execute("SELECT max(built_at) FROM build_manifest").fetchone()[0]
    finally:
        con.close()
    return str(built) if built else "unknown"


def _range(sh: pl.DataFrame, split: str, metric: str) -> tuple[float, str, float, str]:
    """(lowest r, its position, highest r, its position) of a metric."""
    t = sh.filter((pl.col("split") == split) & (pl.col("metric") == metric)).drop_nans("r")
    t = t.drop_nulls("r").sort("r")
    lo, hi = t.row(0, named=True), t.row(-1, named=True)
    return lo["r"], lo["position"], hi["r"], hi["position"]


def _headline_sentence(sh: pl.DataFrame, split: str) -> tuple[str, bool]:
    x_lo, x_lo_p, x_hi, x_hi_p = _range(sh, split, "xfp")
    f_lo, f_lo_p, f_hi, f_hi_p = _range(sh, split, "fpoe")
    sticky = x_lo > f_hi
    verdict = (
        "Opportunity is far stickier than efficiency at every position"
        if sticky
        else "In this data opportunity is NOT stickier than efficiency at every position"
    )
    return (
        f"{verdict}: xFP/game correlates {_f(x_lo)} ({x_lo_p}) to {_f(x_hi)} ({x_hi_p}) "
        f"between the halves, FPOE/game only {_f(f_lo)} ({f_lo_p}) to {_f(f_hi)} ({f_hi_p}).",
        sticky,
    )


def _yac_coverage(frame: pl.DataFrame, seasons: Sequence[int]) -> tuple[int, float]:
    """(first season where every catch has a YAC expectation, share of player-games with a
    catch that have one)."""
    c = frame.filter(pl.col("season").is_in(list(seasons)) & (pl.col("receptions") > 0))
    by = c.group_by("season").agg(pl.col("yac_exp").is_not_null().mean().alias("s")).sort("season")
    full = by.filter(pl.col("s") == 1.0)["season"]
    share = float(c["yac_exp"].is_not_null().mean()) if c.height else 0.0
    return (int(full.min()) if len(full) else -1), share


def build_stability_report(
    db: Path | str | None,
    seasons: Sequence[int],
    *,
    frame: pl.DataFrame | None = None,
    n_boot: int = st.N_BOOT,
    seed: int = st.SEED,
    xfp: pl.DataFrame | None = None,
    xfp_source: str = "ffopportunity",
) -> StabilityReport:
    """Build the report for ``seasons`` (2009 on) from the warehouse ``db`` (or a given D1
    ``frame``, e.g. synthetic in tests). ``xfp``/``xfp_source``: the expected points the frame
    is read with (:func:`.player_week.with_xfp`; None = ffopportunity's) and its name."""
    seasons = sorted({int(s) for s in seasons if int(s) >= st.FIRST_STUDY_SEASON})
    if not seasons:
        raise ValueError(f"no season from {st.FIRST_STUDY_SEASON} on (the study starts then)")
    first, last = seasons[0], seasons[-1]
    if frame is None:
        frame = st.load_frame(db, seasons, xfp=xfp)
    built = _built_at(db) if db is not None else "unknown"
    sh = st.split_half(frame, seasons, n_boot=n_boot, seed=seed)
    if sh.filter(pl.col("n") >= 3).is_empty():
        raise ValueError(f"no player-season with {st.MIN_GAMES}+ games in {first}-{last}")
    full = (first, last)
    tables = {full: st.shrinkage(seasons, frame=frame)}
    for w in windows(first, last):
        inside = [s for s in seasons if w[0] <= s <= w[1]]
        if inside and w not in tables:
            tables[w] = st.shrinkage(inside, frame=frame)
    intervals = {
        m: st.shrinkage_intervals(frame, seasons, metric=m, gs=GS, n_boot=n_boot, seed=seed)
        for m in st.SHRINKAGE_METRICS
    }
    headline, _ = _headline_sentence(sh, "odd_even")
    md = [
        "# Regression Watch: stability study and shrinkage (step D2)",
        "",
        f"Seasons {_label(first, last)}, regular season, QB/RB/WR/TE; xFP: "
        f"{XFP_TITLES[xfp_source]}. Warehouse built {built} "
        "UTC. Regenerate with `uv run twm regression stability` (docs/regression_watch.md "
        "explains every number; notebooks/02_regression_stability.ipynb walks through it).",
        "",
        "A player's fantasy points split into **opportunity** (xFP: what an average player "
        "would have scored from his targets and carries) and **efficiency** (FPOE = points - "
        "xFP). Which of the two repeats? If a player's efficiency in one half of a season "
        "predicts his efficiency in the other half, it is (at least partly) skill; if not, it "
        "was mostly luck and will shrink back toward zero.",
        "",
        f"**Headline.** {headline}",
        "",
        "## 1. How the study works",
        "",
        f"- Every player-season with at least {st.MIN_GAMES} games; a game counts when he had "
        "a target, carry or pass. Each player-season gets one position (the one of most of "
        "his games).",
        "- His games are split into two halves: odd (1st, 3rd, 5th ...) against even (2nd, "
        "4th ...), and, as a harder test, the first half of his games against the second.",
        "- The **split-half correlation r** compares the two halves across player-seasons: "
        "r = 1 means the halves rank players exactly the same, r = 0 means one half says "
        "nothing about the other. In brackets: a 95% interval from "
        f"{n_boot:,} bootstrap resamples of the player-seasons.",
        "",
        "## 2. Opportunity against efficiency (odd against even games)",
        "",
        *_md_headline(sh, "odd_even"),
    ]
    md += _md_rest(sh, frame, seasons, tables, intervals, full, xfp_source)
    rows = csv_rows(sh, tables, pl.concat(list(intervals.values())), full)
    return StabilityReport("\n".join(md), rows, _summary(sh, tables[full]), sh, tables[full])


def _md_rest(
    sh: pl.DataFrame,
    frame: pl.DataFrame,
    seasons: Sequence[int],
    tables: dict[tuple[int, int], pl.DataFrame],
    intervals: dict[str, pl.DataFrame],
    full: tuple[int, int],
    xfp_source: str = "ffopportunity",
) -> list[str]:
    """Sections 3-7 of the markdown."""
    yac_first, yac_share = _yac_coverage(frame, seasons)
    yac_text = (
        f"every catch of {_label(*full)} has one"
        if yac_share == 1.0
        else f"{100 * yac_share:.1f}% of player-games with a catch have one (complete from "
        f"{yac_first})"
    )
    md = [
        "## 3. The parts of efficiency (odd against even games)",
        "",
        "Each part is a rate over expected per chance, added up over the half: **TD rate** = "
        "(touchdowns - expected touchdowns) per pass, carry or target; **catch rate** = "
        "(catches - expected catches) per target (for QBs the same for completions per pass, "
        "often called CPOE); **YAC** = (yards after the catch - expected YAC) per catch. A "
        f"player-season counts for a rate when each half has at least {st.MIN_DENOMINATOR} "
        "chances.",
        "",
        *_md_components(sh),
        f"YAC over expected needs a per-catch YAC expectation ({XFP_TITLES[xfp_source]}): "
        f"{yac_text}, so it "
        "is studied on every season of the study.",
        "",
        "## 4. A harder test: first half against second half of his games",
        "",
        "Here roles also change (injuries, depth charts, trades), so correlations are lower "
        "than with odd and even games; the gap between opportunity and efficiency is what "
        "matters.",
        "",
        *_md_headline(sh, "first_second"),
        "## 5. How much FPOE/game to believe: the shrinkage table",
        "",
        "Treat each game's FPOE as the player's true level plus luck. Across player-seasons the "
        "two odd/even halves share the true level (their covariance estimates the **signal "
        "variance**) and differ only by luck (the variance of their difference estimates the "
        "**noise variance per game**). After g games the average is worth "
        "**r(g) = signal / (signal + noise / g)** of its value: the **shrinkage factor** D3's "
        "rest-of-season projection multiplies FPOE/game by. 'Games for half weight' = noise / "
        "signal, the number of games after which his FPOE/game deserves half its value.",
        "",
        "**FPOE/game, all plays** (intervals: bootstrap over player-seasons)",
        "",
        *_md_shrinkage(tables[full], intervals["fpoe"], "fpoe"),
    ]
    wr8 = _rel(tables[full], "WR", "fpoe", 8).get("reliability")
    if wr8 is not None:
        md += [
            f"Example: a WR who scored 4.0 points per game over expected in his first 8 games "
            f"keeps r(8) x 4.0 = {_f(wr8 * 4.0)} points per game of it in the projection; the "
            "rest is expected to fade.",
            "",
        ]
    md += [
        "**FPOE/game without garbage time**",
        "",
        *_md_shrinkage(tables[full], intervals["fpoe_ng"], "fpoe_ng"),
        "## 6. Is the table stable across seasons?",
        "",
        "r(8) for FPOE/game estimated on shorter windows: three blocks of seasons, then the "
        "growing windows a walk-forward backtest would use (it may only learn from seasons "
        "before the one it tests; `stability.shrinkage(seasons)` takes exactly those). The "
        "CSV has every g and the no-garbage-time version.",
        "",
        *_md_windows(tables, "fpoe", 8),
        "## 7. Limits",
        "",
        *limits(xfp_source),
        "",
    ]
    return md


def _summary(sh: pl.DataFrame, table: pl.DataFrame) -> list[str]:
    """Short lines for the terminal: the headline correlations and r(g) at GS."""
    out = [_headline_sentence(sh, "odd_even")[0]]
    for pos in FANTASY_POSITIONS:
        cells = ", ".join(
            f"r({g}) {_f(_rel(table, pos, 'fpoe', g).get('reliability'))}" for g in GS
        )
        out.append(f"{pos} FPOE/game shrinkage: {cells}")
    return out


def write_stability_report(report: StabilityReport, md_path: Path) -> Path:
    """Write the markdown and ``<same name>.csv`` next to it; return the CSV path."""
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(report.markdown, encoding="utf-8")
    csv_path = md_path.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(CSV_COLUMNS), lineterminator="\n")
        w.writeheader()
        w.writerows(report.csv_rows)
    return csv_path
