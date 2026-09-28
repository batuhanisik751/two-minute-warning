"""The label report: how often pool players hit, and the evidence that the labels make sense
(``twm radar labels-report``, step C2).

What the owner needs before any model is trained on these labels (docs/waiver_radar.md
"Labels"):

- **Base rates.** Among in-pool rows that can be trained on (``train_eligible``, final label):
  how often ``y_hit`` / ``y_sustained`` are true, per season and position. This is the "no
  skill" precision a model must beat. The same rates outside the pool are a sanity check: those
  players are rostered because they are good, so they must hit far more often.
- **Windows.** How many rows have a short window (the last weeks of a season) or a pending
  label (the current season), and which as-ofs have an empty pool (2013-2015 week 1).
- **Weekly finishes.** Where the point-in-time position came from, how many stat lines are at a
  position that is not ranked (fullbacks ...), and that every completed week has at least the
  threshold's number of starter finishes.
- **Example hits.** For a few real as-ofs, the pool players who hit, with their window weeks and
  ranks, to eyeball that the hits make football sense.

Deterministic (sorted, fixed decimals; the only time in it is the warehouse's ``built_at``).
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from twm.config import FANTASY_POSITIONS, settings
from twm.modules.waiver_radar.labels import (
    POSITION_SOURCES,
    LabelRules,
    label_rows,
    last_reg_weeks,
    weekly_finishes,
)
from twm.modules.waiver_radar.pool import PoolRules, pool_history, pool_weeks

# As-ofs whose pool hits are listed (one per roster/ranking regime: post-game rosters with the
# prior-PPG list, game-day rosters with it, FantasyPros ECR); only the selected seasons show.
EXAMPLE_ASOFS: tuple[tuple[int, int], ...] = ((2015, 9), (2019, 4), (2023, 6), (2025, 10))
EXAMPLES_PER_POSITION = 8
FIRST_EVAL_SEASON = 2014  # PROJECT_SPEC 8.1: walk-forward evaluation seasons 2014-2025

CSV_COLUMNS = (
    "season", "week", "as_of", "method", "position", "in_pool", "n_rows", "n_short_window",
    "n_train_eligible", "n_pending", "n_labelled", "n_hit", "n_sustained", "n_flex_hit",
)  # fmt: skip


@dataclass(frozen=True)
class LabelReport:
    markdown: str
    csv_rows: list[dict[str, object]]
    summary: list[str]


def _pct(num: int, den: int) -> str:
    return "-" if not den else f"{100 * num / den:.1f}%"


def _table(header: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


def _built_at(db: Path | str) -> str:
    from twm.warehouse.build import connect

    con = connect(db, read_only=True)
    try:
        built = con.execute("SELECT max(built_at) FROM build_manifest").fetchone()[0]
    finally:
        con.close()
    return str(built) if built else "unknown"


def _rate_rows(lab: pl.DataFrame, groups: list[tuple[str, pl.Expr]]) -> list[list[object]]:
    """One row per group label: n, hit %, sustained % per position, then all positions."""
    out = []
    for label, cond in groups:
        sub = lab.filter(cond)
        row: list[object] = [label]
        for pos in (*FANTASY_POSITIONS, None):
            p = sub if pos is None else sub.filter(pl.col("position") == pos)
            n = p.height
            hit = int(p.get_column("y_hit").sum()) if n else 0
            sus = int(p.get_column("y_sustained").sum()) if n else 0
            row += [n, _pct(hit, n), _pct(sus, n)]
        out.append(row)
    return out


def _rate_header() -> list[str]:
    head = ["seasons"]
    for pos in (*FANTASY_POSITIONS, "all"):
        head += [f"{pos} n", f"{pos} y_hit", f"{pos} y_sustained"]
    return head


def _ranks_text(weeks: list[int], ranks: list[int | None], threshold: int) -> str:
    parts = []
    for w, r in zip(weeks, ranks, strict=True):
        mark = "-" if r is None else (f"**{r}**" if r <= threshold else str(r))
        parts.append(f"w{w} {mark}")
    return ", ".join(parts)


def _csv_rows(lab: pl.DataFrame) -> list[dict[str, object]]:
    final = pl.col("label_status") == "final"
    labelled = final & pl.col("train_eligible")
    agg = (
        lab.group_by("season", "week", "as_of", "method", "position", "in_pool")
        .agg(
            pl.len().alias("n_rows"),
            pl.col("is_short_window").sum().alias("n_short_window"),
            pl.col("train_eligible").sum().alias("n_train_eligible"),
            (~final).sum().alias("n_pending"),
            labelled.sum().alias("n_labelled"),
            (labelled & pl.col("y_hit")).sum().alias("n_hit"),
            (labelled & pl.col("y_sustained")).sum().alias("n_sustained"),
            (labelled & (pl.col("n_flex_finishes") >= 1)).sum().alias("n_flex_hit"),
        )
        .sort("season", "week", "method", "position", "in_pool")
    )
    rows = []
    for r in agg.iter_rows(named=True):
        r["as_of"] = r["as_of"].strftime("%Y-%m-%d %H:%M")
        if r["position"] not in ("RB", "WR"):
            r["n_flex_hit"] = ""
        rows.append({c: r[c] for c in CSV_COLUMNS})
    return rows


def build_label_report(
    db: Path | str,
    seasons: Sequence[int],
    *,
    rules: LabelRules | None = None,
    pool_rules: PoolRules | None = None,
    examples: Sequence[tuple[int, int]] = EXAMPLE_ASOFS,
    first_eval_season: int = FIRST_EVAL_SEASON,
    last_eval_season: int | None = None,
) -> LabelReport:
    """Label every as-of of ``seasons`` and render the markdown report and the CSV rows.

    The pooled evaluation row covers ``first_eval_season`` to ``last_eval_season`` (default:
    the season before config ``current_season``)."""
    rules = rules or LabelRules.from_config()
    pool_rules = pool_rules or PoolRules.from_config()
    last_eval = last_eval_season if last_eval_season is not None else settings().current_season - 1
    seasons = sorted(int(s) for s in seasons)
    built_at = _built_at(db)
    asofs = pool_weeks(db, seasons)
    hist = pool_history(db, seasons, rules=pool_rules)
    fin = weekly_finishes(db, seasons, rules=rules)
    lab = label_rows(db, hist, rules=rules, finishes=fin)
    last = last_reg_weeks(db, seasons)
    thr = dict(rules.starter_thresholds)

    lines = [
        "# Waiver Radar labels: base rates and checks",
        "",
        f"Generated by `uv run twm radar labels-report` from the warehouse built at {built_at} "
        "(UTC). Deterministic: rerun on the same warehouse to get the same file. Definitions: "
        "`docs/waiver_radar.md` (section Labels).",
        "",
        "Each row is a player on an NFL roster at a Tuesday as-of after week N (the C1 "
        "universe); `in_pool` = probably on waivers. Its window is the next "
        f"{rules.window_games} regular-season weeks after N in which his team (at the as-of) "
        "plays: a bye is skipped, and the window stops at the season's last regular-season "
        "week (fewer games at the end). A **starter finish** is a week he ranks at or above "
        "the weekly starter threshold at his position that week (config/league.yaml: "
        + ", ".join(f"{p} top {n}" for p, n in thr.items())
        + "), by fantasy points among every player of that position with a stat line; ties "
        "share the better rank. **y_hit** = at least one starter finish in the window, "
        "**y_sustained** = at least two. A row is **train-eligible** with at least "
        f"{rules.min_train_games} window games, and **pending** (no label yet) until every "
        "game of its window weeks has a final score and stat lines in the cache.",
        "",
    ]

    # ---- 1. as-ofs and rows ----------------------------------------------------------------
    seen = set(lab.select("season", "week").unique().iter_rows())
    rows = []
    for s in seasons:
        weeks_s = [w for (ss, w, _) in asofs if ss == s and w < last.get(s, 0)]
        empty = [w for w in weeks_s if (s, w) not in seen]
        sub = lab.filter(pl.col("season") == s)
        pool = sub.filter(pl.col("in_pool"))
        n_short = int(pool.get_column("is_short_window").sum())
        n_not_train = int((~pool.get_column("train_eligible")).sum())
        rows.append([
            s, last.get(s, "-"), len(weeks_s) - len(empty),
            ", ".join(str(w) for w in empty) or "-", sub.height, pool.height,
            f"{n_short} ({_pct(n_short, pool.height)})",
            f"{n_not_train} ({_pct(n_not_train, pool.height)})",
            int((pool.get_column("label_status") == "pending").sum()),
            int((sub.get_column("label_status") == "pending").sum()),
        ])  # fmt: skip
    lines += ["## As-ofs and rows per season", ""]
    lines += _table(
        ["season", "last REG week", "as-ofs labelled", "empty pool (week)", "universe rows",
         "pool rows", "pool: short window", "pool: not train-eligible", "pool: pending",
         "all: pending"],
        rows,
    )  # fmt: skip
    lines += [
        "",
        "As-ofs are weeks 1 to the last regular-season week minus 1 whose games are all in the "
        "cache (no window after the last week; the current season stops at the last complete "
        "week). Empty pool: no weekly roster was public yet at that as-of (2013-2015 week 1, "
        "post-game roster snapshots, see pool_sizes.md), so there are no rows. Short window = "
        f"fewer than {rules.window_games} games (the last two as-ofs of a season, or a team "
        "with no game left in a week, such as the cancelled 2022 week-17 Buffalo at Cincinnati "
        "game, which nflverse leaves out).",
        "",
    ]

    # ---- 2. base rates -----------------------------------------------------------------------
    usable = lab.filter((pl.col("label_status") == "final") & pl.col("train_eligible"))
    in_eval = pl.col("season").is_between(first_eval_season, last_eval)
    groups = [(f"{first_eval_season}-{last_eval} (pooled)", in_eval)]
    groups += [(str(s), pl.col("season") == s) for s in seasons]
    for title, cond, note in (
        (
            "## Base rates in the pool (train-eligible rows with a final label)",
            pl.col("in_pool"),
            "The share of pool rows that hit: what a model that ranks the pool at random "
            "would find. Pooled row = the walk-forward evaluation seasons (PROJECT_SPEC 8.1).",
        ),
        (
            "## Base rates outside the pool (sanity check)",
            ~pl.col("in_pool"),
            "Rostered players ranked high before the season or scoring well so far: they must "
            "hit far more often than the pool.",
        ),
    ):
        sub = usable.filter(cond)
        lines += [title, "", note, ""]
        lines += _table(_rate_header(), _rate_rows(sub, groups))
        lines.append("")

    # ---- 3. FLEX-worthy -----------------------------------------------------------------------
    flex = usable.filter(pl.col("in_pool") & pl.col("position").is_in(list(rules.flex_positions)))
    frows = []
    for label, cond in groups:
        sub = flex.filter(cond)
        r: list[object] = [label]
        for pos in rules.flex_positions:
            p = sub.filter(pl.col("position") == pos)
            r += [p.height, _pct(int((p.get_column("n_flex_finishes") >= 1).sum()), p.height)]
        frows.append(r)
    lines += [
        "## FLEX-worthy finishes in the pool (informative, not a label)",
        "",
        f"Share of pool rows with at least one week ranked in the top {rules.flex_rank} at his "
        "position (a RB or WR a 12-team league would start in its FLEX slot).",
        "",
    ]
    head = ["seasons"]
    for pos in rules.flex_positions:
        head += [f"{pos} n", f"{pos} FLEX-worthy"]
    lines += _table(head, frows)
    lines.append("")

    # ---- 4. windows ---------------------------------------------------------------------------
    pool = lab.filter(pl.col("in_pool"))
    wrows = []
    for s in seasons:
        p = pool.filter(pl.col("season") == s)
        games = p.get_column("window_games")
        counts = [int((games == k).sum()) for k in range(rules.window_games + 1)]
        wrows.append([s, *counts, int((p.get_column("label_status") == "pending").sum())])
    lines += [
        "## Window length and pending labels (pool rows)",
        "",
        "Rows by number of window games. 0 would mean the team has no game left (never "
        "expected before the last week); rows with fewer than "
        f"{rules.min_train_games} games are kept but not train-eligible.",
        "",
    ]
    lines += _table(
        ["season", *[f"{k} games" for k in range(rules.window_games + 1)], "pending"], wrows
    )
    pend = lab.filter(pl.col("label_status") == "pending")
    if pend.height:
        per = pend.group_by("season", "week").agg(
            pl.len().alias("n"), pl.col("in_pool").sum().alias("pool"),
            pl.col("window_weeks").first().alias("w"),
        ).sort("season", "week")  # fmt: skip
        lines += ["", "Pending as-ofs (a window week is not complete in the cache yet):", ""]
        lines += _table(
            ["season", "as-of week", "rows", "pool rows", "a window (first row)"],
            [[r["season"], r["week"], r["n"], r["pool"], ", ".join(str(w) for w in r["w"])]
             for r in per.iter_rows(named=True)],
        )  # fmt: skip
    lines.append("")

    # ---- 5. weekly finishes -------------------------------------------------------------------
    final_fin = fin.filter(pl.col("is_week_final"))
    src_rows = []
    for s in seasons:
        f = fin.filter(pl.col("season") == s)
        ranked = f.filter(pl.col("pos_rank").is_not_null())
        other = f.filter(pl.col("pos_rank").is_null())
        fb = other.filter(pl.col("position") == "FB")
        src_rows.append([
            s, f.height, ranked.height,
            *[int((f.get_column("position_source") == k).sum()) for k in POSITION_SOURCES],
            other.height, fb.height, _fb_starter_level(f, fb, thr.get("RB", 0)),
        ])  # fmt: skip
    short = (
        final_fin.filter(pl.col("pos_rank").is_not_null())
        .group_by("season", "week", "position")
        .agg(pl.col("is_starter_finish").sum().alias("n"), pl.len().alias("ranked"))
        .with_columns(pl.col("position").replace_strict(thr, default=None).alias("thr"))
    )
    below = short.filter((pl.col("n") < pl.col("thr")) & (pl.col("ranked") >= pl.col("thr")))
    above = short.filter(pl.col("n") > pl.col("thr"))
    lines += [
        "## Weekly finishes: where the positions come from",
        "",
        "Every regular-season stat line of the selected seasons. The position is the "
        "player's weekly-roster position that week (`roster`); without a roster row that "
        "week, his latest earlier roster week of the season (`roster_earlier`), else that "
        "game's snap-count position (`snaps`), else none. Only QB/RB/WR/TE are ranked; the "
        "other stat lines (fullbacks, defenders and kickers with a stat) are not. FB at RB "
        "level = fullback lines that scored at least as much as that week's "
        f"RB{thr.get('RB', 0)} (they would have been RB starter finishes had fullbacks been "
        "ranked with running backs).",
        "",
    ]
    lines += _table(
        ["season", "stat lines", "ranked", *POSITION_SOURCES, "not ranked", "FB lines",
         "FB at RB level"],
        src_rows,
    )  # fmt: skip
    lines += [
        "",
        f"Completed weeks with fewer starter finishes than the threshold at a position: "
        f"{below.height} (expected 0). Position-weeks with more (a tie at the threshold "
        f"rank): {above.height} of {short.height}.",
        "",
    ]

    # ---- 6. example hits ----------------------------------------------------------------------
    lines += [
        "## Example hits (eyeball check)",
        "",
        "Pool players with y_hit at a few as-ofs: his team and position at the as-of, points "
        "per game so far, then each window week with his rank at his position that week "
        "(**bold** = starter finish, - = no stat line). Up to "
        f"{EXAMPLES_PER_POSITION} per position (most finishes, then best rank).",
        "",
    ]
    shown_any = False
    for s, w in examples:
        ex = lab.filter(
            (pl.col("season") == s) & (pl.col("week") == w) & pl.col("in_pool")
            & pl.col("y_hit").fill_null(False)
        )  # fmt: skip
        n_pool = lab.filter((pl.col("season") == s) & (pl.col("week") == w) & pl.col("in_pool"))
        if n_pool.height == 0:
            continue
        shown_any = True
        method = n_pool.get_column("method").first()
        as_of = n_pool.get_column("as_of").first()
        lines += [
            f"### {s} week {w} (as-of {as_of:%Y-%m-%d %H:%M} UTC, method {method}): "
            f"{ex.height} hits among {n_pool.height} pool players, "
            f"{int(ex.get_column('y_sustained').sum())} sustained",
            "",
        ]
        ex = ex.sort(
            ["position", "n_starter_finishes", "best_rank", "name", "gsis_id"],
            descending=[False, True, False, False, False],
        )
        trows = []
        for pos in FANTASY_POSITIONS:
            pe = ex.filter(pl.col("position") == pos)
            for r in pe.head(EXAMPLES_PER_POSITION).iter_rows(named=True):
                ppg = "-" if r["ppg_to_date"] is None else f"{r['ppg_to_date']:.1f}"
                trows.append([
                    r["name"], r["team"], r["position"], ppg, r["games_to_date"],
                    _ranks_text(r["window_weeks"], r["window_ranks"], thr[r["position"]]),
                    r["n_starter_finishes"], "yes" if r["y_sustained"] else "no",
                ])  # fmt: skip
            if pe.height > EXAMPLES_PER_POSITION:
                trows.append([f"... {pe.height - EXAMPLES_PER_POSITION} more {pos}", "", "",
                              "", "", "", "", ""])  # fmt: skip
        lines += _table(
            ["player", "team", "pos", "PPG so far", "games", "window: rank", "finishes",
             "sustained"],
            trows,
        )  # fmt: skip
        lines.append("")
    if not shown_any:
        lines += ["None of the example as-ofs is in the selected seasons.", ""]

    n_asofs = lab.select("season", "week").n_unique()
    summary = [
        f"{len(seasons)} seasons, {n_asofs} labelled as-ofs, {lab.height} rows "
        f"({int(lab.get_column('in_pool').sum())} in the pool), "
        f"{int((lab.get_column('label_status') == 'pending').sum())} pending",
    ]
    return LabelReport("\n".join(lines).rstrip() + "\n", _csv_rows(lab), summary)


def _fb_starter_level(season_fin: pl.DataFrame, fb: pl.DataFrame, rb_threshold: int) -> int:
    """Fullback stat lines scoring at least the week's RB``rb_threshold`` score."""
    if fb.height == 0 or rb_threshold < 1:
        return 0
    cut = (
        season_fin.filter((pl.col("position") == "RB") & (pl.col("pos_rank") <= rb_threshold))
        .group_by("week")
        .agg(pl.col("fantasy_points").min().alias("cut"), pl.len().alias("n"))
        .filter(pl.col("n") >= rb_threshold)
    )
    j = fb.join(cut, on="week", how="inner")
    return int((j.get_column("fantasy_points") >= j.get_column("cut")).sum())


def write_label_report(report: LabelReport, md_path: Path) -> Path:
    """Write the markdown and ``<same name>.csv`` next to it; return the CSV path."""
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(report.markdown, encoding="utf-8")
    csv_path = md_path.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(CSV_COLUMNS), lineterminator="\n")
        w.writeheader()
        w.writerows(report.csv_rows)
    return csv_path
