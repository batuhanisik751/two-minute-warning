"""The xFP report (``twm regression xfp-report``, step D1): can the play-by-play be trusted to
split points and expected points into garbage time and the rest?

Four checks (docs/regression_watch.md explains each for a beginner):

1. **Joins.** The per-play expected-value tables (``fact_opportunity_pass`` / ``_rush``) join
   ``fact_play`` on (game_id, play_id); how many do not, and how often the players differ.
2. **Points reconciliation.** For every player-game of the frame, the per-play points summed
   over all his plays (``play_points``) against his weekly stat line scored the same way
   (``fantasy_points``): the share within 0.1 points, and every difference with the stats
   that cause it.
3. **xFP reconciliation.** The per-play xFP summed over his plays (``play_xfp``) against the
   weekly ffopportunity xFP (``xfp``): the weekly file stores every expected stat with 2
   decimals, so they may differ by up to 0.005 x the sum of the scoring weights (the rounding
   bound of docs/assumptions.md section 9, without its point-subtotal term, which the weekly
   xFP does not use).
4. **Garbage time.** How many points and how much xFP come from garbage-time plays, by
   position and season.

Everything is read through the as-of view at the end of time (the whole warehouse, hindsight
columns hidden). The output is deterministic (sorted, fixed decimals; the only time in it is the
warehouse's ``built_at``).
"""

from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from twm.asof import AsOfView
from twm.config import FANTASY_POSITIONS
from twm.modules.regression_watch import player_week as pw
from twm.modules.regression_watch.plays import play_stats_sql
from twm.scoring import ScoringRules

POINTS_BOUND = 0.1  # points: the play sum must equal the weekly line within this
N_DIFFERENCES = 15  # differences listed in the markdown (the CSV has none: see _differences)
EPS = 1e-9

# Stat groups a points difference is explained by, with the cause in plain words.
CAUSES: dict[str, tuple[tuple[str, ...], str]] = {
    "lost fumbles": (
        ("fumbles_lost_total", "sack_fumbles_lost", "rushing_fumbles_lost",
         "receiving_fumbles_lost"),
        "the official stat line and the play-by-play's fumbler columns disagree on who lost the "
        "ball (mostly plays with two fumbles)",
    ),
    "yards": (
        ("passing_yards", "rushing_yards", "receiving_yards"),
        "the official stat line credits yards the play-by-play's yard columns do not (stat "
        "corrections, a pass caught by the passer himself, several laterals on one play)",
    ),
    "touchdowns": (
        ("passing_tds", "rushing_tds", "receiving_tds", "special_teams_tds",
         "fumble_recovery_tds"),
        "a touchdown counted differently: an upstream duplicate play (a run repeated as a "
        "kickoff row) or a return credited another way",
    ),
    "catches": (("receptions",), "a catch the play-by-play does not show"),
    "interceptions": (("passing_interceptions",), "an interception credited differently"),
    "two-point conversions": (
        ("passing_2pt_conversions", "rushing_2pt_conversions", "receiving_2pt_conversions"),
        "a two-point conversion credited differently",
    ),
}  # fmt: skip

CSV_COLUMNS = (
    "season", "position", "player_games", "with_xfp", "points_within_bound",
    "xfp_within_bound", "fantasy_points", "points_garbage", "points_garbage_share", "xfp",
    "xfp_garbage", "xfp_garbage_share", "opportunities", "opportunities_garbage",
    "opportunities_garbage_share", "fpoe_per_game", "fpoe_ng_per_game",
    "incompletions_with_target",
)  # fmt: skip


@dataclass(frozen=True)
class XfpReport:
    markdown: str
    csv_rows: list[dict[str, object]]
    summary: list[str]
    differences: pl.DataFrame  # player-games whose play sum misses the weekly line


def xfp_bound(rules: ScoringRules) -> float:
    """Largest honest gap between the per-play xFP sum and the weekly xFP: every weekly
    expected stat is rounded to 0.01 upstream (error <= 0.005 x its weight); the per-play
    values carry 6 decimals (negligible)."""
    from twm.scoring import xfp_columns

    return round(0.005 * sum(abs(rules.points[k]) for k in xfp_columns(rules)), 6)


def _pct(num: float, den: float, digits: int = 1) -> str:
    return "-" if not den else f"{100 * num / den:.{digits}f}%"


def _table(header: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


def _warehouse_notes(db: Path | str) -> tuple[str, dict[str, dict[str, object]]]:
    """The build time and the per-play tables' manifest rows (bookkeeping, not point-in-time)."""
    from twm.warehouse.build import connect

    con = connect(db, read_only=True)
    try:
        built = con.execute("SELECT max(built_at) FROM build_manifest").fetchone()[0]
        rows = con.execute(
            "SELECT table_name, n_rows, n_dropped_duplicates, notes FROM build_manifest "
            "WHERE table_name IN ('fact_opportunity_pass', 'fact_opportunity_rush')"
        ).fetchall()
    finally:
        con.close()
    notes = {
        name: {"n_rows": n, "n_dropped_duplicates": dup, **json.loads(js)}
        for name, n, dup, js in rows
    }
    return (str(built) if built else "unknown"), notes


def _coverage(view: AsOfView, seasons: Sequence[int]) -> dict[str, int]:
    """Plays of fact_play (2006+, REG) and how many have a per-play expected-values row."""
    s = ", ".join(str(int(x)) for x in seasons)
    row = view.sql(f"""
        SELECT count(*) FILTER (WHERE f.pass_attempt = 1 AND f.passer_player_id IS NOT NULL
                                 AND COALESCE(f.sack, 0) = 0) AS passes,
               count(*) FILTER (WHERE f.pass_attempt = 1 AND f.passer_player_id IS NOT NULL
                                 AND COALESCE(f.sack, 0) = 0 AND o.play_id IS NOT NULL)
                   AS passes_matched,
               count(*) FILTER (WHERE f.pass_attempt = 1 AND COALESCE(f.sack, 0) = 1) AS sacks,
               count(*) FILTER (WHERE f.rush_attempt = 1 AND f.rusher_player_id IS NOT NULL)
                   AS runs,
               count(*) FILTER (WHERE f.rush_attempt = 1 AND f.rusher_player_id IS NOT NULL
                                 AND r.play_id IS NOT NULL) AS runs_matched
        FROM fact_play f
        LEFT JOIN fact_opportunity_pass o ON o.game_id = f.game_id AND o.play_id = f.play_id
        LEFT JOIN fact_opportunity_rush r ON r.game_id = f.game_id AND r.play_id = f.play_id
        WHERE f.season IN ({s}) AND f.season_type = 'REG'""").row(0, named=True)
    return {k: int(v or 0) for k, v in row.items()}


# Below this share of incomplete passes with a named target, receivers' xFP of the season
# counts little more than their catches (the report warns).
TARGET_COVERAGE_WARN = 0.5


def _target_coverage(view: AsOfView, seasons: Sequence[int]) -> dict[int, float]:
    """Per season: the share of incomplete passes (two-point tries excluded) whose target is
    named in ffopportunity's per-play file. An unnamed target is nobody's expected catch."""
    s = ", ".join(str(int(x)) for x in seasons)
    df = view.sql(f"""
        SELECT season, count(receiver_player_id) / count(*) AS share
        FROM fact_opportunity_pass
        WHERE season IN ({s}) AND season_type = 'REG' AND COALESCE(two_point_attempt, 0) = 0
          AND complete_pass = 0
        GROUP BY season""")
    return {int(a): float(b) for a, b in df.iter_rows()}


def _differences(view: AsOfView, frame: pl.DataFrame, rules: ScoringRules) -> pl.DataFrame:
    """Player-games whose play sum misses the weekly points by more than POINTS_BOUND, with the
    scored stats that differ (weekly vs plays) and the cause."""
    gap = (pl.col("fantasy_points") - pl.col("play_points")).abs()
    off = frame.filter(gap > POINTS_BOUND + EPS)
    cols = sorted({c for cs in rules.columns().values() for c in cs})
    empty = {"stats": pl.String, "cause": pl.String}
    if off.height == 0:
        return off.with_columns(pl.lit(None, dtype=t).alias(n) for n, t in empty.items())
    games = ", ".join(f"'{g}'" for g in sorted(set(off.get_column("game_id"))))
    where = f"season_type = 'REG' AND game_id IN ({games})"
    sums = ", ".join(f"sum({c}) AS {c}" for c in cols)
    plays = view.sql(
        f"SELECT game_id, gsis_id, {sums} FROM ({play_stats_sql(where)}) GROUP BY game_id, gsis_id"
    )
    weekly = view.sql(
        f"SELECT game_id, player_id AS gsis_id, {', '.join(cols)} FROM fact_player_week "
        f"WHERE {where}"
    )
    keys = off.select("game_id", "gsis_id")
    w = keys.join(weekly, on=["game_id", "gsis_id"], how="left")
    p = keys.join(plays, on=["game_id", "gsis_id"], how="left")
    stats, causes = [], []
    for wr, pr in zip(w.iter_rows(named=True), p.iter_rows(named=True), strict=True):
        diff = {c: (wr[c] or 0, pr[c] or 0) for c in cols if (wr[c] or 0) != (pr[c] or 0)}
        stats.append("; ".join(f"{c} {a:g} vs {b:g}" for c, (a, b) in diff.items()) or "-")
        groups = [g for g, (members, _) in CAUSES.items() if set(members) & set(diff)]
        causes.append(", ".join(groups) or "unexplained")
    return off.with_columns(
        (pl.col("fantasy_points") - pl.col("play_points")).round(2).alias("difference"),
        pl.Series("stats", stats, dtype=pl.String),
        pl.Series("cause", causes, dtype=pl.String),
    ).sort(pl.col("difference").abs(), "game_id", "gsis_id", descending=[True, False, False])


def _group_rows(frame: pl.DataFrame, by: list[str], bound: float) -> pl.DataFrame:
    within = (pl.col("fantasy_points") - pl.col("play_points")).abs() <= POINTS_BOUND + EPS
    xwithin = (pl.col("xfp") - pl.col("play_xfp")).abs() <= bound + 1e-6
    return (
        frame.group_by(by)
        .agg(
            pl.len().alias("player_games"),
            pl.col("xfp").is_not_null().sum().alias("with_xfp"),
            within.sum().alias("points_within_bound"),
            (xwithin & pl.col("xfp").is_not_null()).sum().alias("xfp_within_bound"),
            pl.col("fantasy_points").sum().alias("fantasy_points"),
            pl.col("points_garbage").sum().alias("points_garbage"),
            pl.col("xfp").sum().alias("xfp"),
            pl.col("xfp_garbage").sum().alias("xfp_garbage"),
            pl.col("n_opportunities").sum().alias("opportunities"),
            pl.col("n_opportunities_garbage").sum().alias("opportunities_garbage"),
            pl.col("fpoe").mean().alias("fpoe_per_game"),
            pl.col("fpoe_ng").mean().alias("fpoe_ng_per_game"),
        )
        .with_columns(
            (pl.col("points_garbage") / pl.col("fantasy_points")).alias("points_garbage_share"),
            (pl.col("xfp_garbage") / pl.col("xfp")).alias("xfp_garbage_share"),
            (pl.col("opportunities_garbage") / pl.col("opportunities")).alias(
                "opportunities_garbage_share"
            ),
        )
        .sort(by)
    )


def _csv_value(v: object) -> object:
    return round(v, 4) if isinstance(v, float) else v


def build_xfp_report(
    db: Path | str, seasons: Sequence[int], *, rules: ScoringRules | None = None
) -> XfpReport:
    """Build the report for ``seasons`` (2006 on) from the warehouse ``db``."""
    rules = rules or ScoringRules.from_config()
    seasons = sorted({int(s) for s in seasons if int(s) >= pw.FIRST_SEASON})
    if not seasons:
        raise ValueError(f"no season from {pw.FIRST_SEASON} on (ffopportunity starts then)")
    built, notes = _warehouse_notes(db)
    bound = xfp_bound(rules)
    with AsOfView(db, pw.END_OF_TIME) as view:
        everyone = pw.player_games_for(view, seasons, rules=rules, positions=None)
        frame = everyone.filter(pl.col("position").is_in(list(FANTASY_POSITIONS)))
        diffs = _differences(view, frame, rules)
        cov = _coverage(view, seasons)
        targets = _target_coverage(view, seasons)
    first, last = seasons[0], seasons[-1]
    n = frame.height
    n_within = int(
        ((frame["fantasy_points"] - frame["play_points"]).abs() <= POINTS_BOUND + EPS).sum()
    )
    n_exact = int(((frame["fantasy_points"] - frame["play_points"]).abs() < 0.005).sum())
    with_x = frame.filter(pl.col("xfp").is_not_null())
    dx = (with_x["xfp"] - with_x["play_xfp"]).abs()
    n_x_within = int((dx <= bound + 1e-6).sum())
    unplaced = everyone.filter(pl.col("position").is_null())
    no_pos, no_pos_used = unplaced.height, int(unplaced["xfp"].is_not_null().sum())
    by_pos = _group_rows(frame, ["position"], bound)
    by_season_pos = _group_rows(frame, ["season", "position"], bound)
    totals = _group_rows(frame.with_columns(pl.lit("all").alias("position")), ["position"], bound)

    md: list[str] = [
        "# Regression Watch: xFP and garbage time (step D1)",
        "",
        f"Seasons {first}-{last}, regular season, QB/RB/WR/TE. Warehouse built {built} UTC. "
        "Regenerate with `uv run twm regression xfp-report` (docs/regression_watch.md "
        "explains every number).",
        "",
        "**xFP** (expected fantasy points) is what an average player would have scored from "
        "the same targets and carries; **FPOE** = fantasy points - xFP. Both are computed "
        "twice: from the weekly files (what the app shows) and play by play, which is what "
        "lets us drop **garbage-time** plays (the game is decided: win probability below 5% "
        "or above 95%, except in a one-score game's last two minutes of a half).",
        "",
        "## 1. The per-play tables join the play-by-play",
        "",
    ]
    rows = []
    for name in ("fact_opportunity_pass", "fact_opportunity_rush"):
        nt = notes.get(name, {})
        n_rows = int(nt.get("n_rows", 0))
        miss = int(nt.get("n_not_in_fact_play", 0))
        suffix = "_differs_from_fact_play"
        players = [
            f"{k[2 : -len(suffix)]} {v:,}" for k, v in sorted(nt.items()) if k.endswith(suffix)
        ]
        dup = int(nt.get("n_dropped_duplicates", 0))
        rows.append(
            (f"`{name}`", f"{n_rows:,}", f"{miss:,}", _pct(n_rows - miss, n_rows, 3),
             ", ".join(players), f"{dup:,}")
        )  # fmt: skip
    md += _table(
        ["table (all built seasons)", "plays", "not in fact_play", "join rate",
         "player differs from fact_play", "duplicates dropped"], rows,
    )  # fmt: skip
    md += [
        "",
        f"Seen from the play-by-play ({first}-{last}, regular season): "
        f"{cov['passes_matched']:,} of {cov['passes']:,} pass attempts that are not sacks "
        f"({_pct(cov['passes_matched'], cov['passes'], 2)}) and {cov['runs_matched']:,} of "
        f"{cov['runs']:,} runs ({_pct(cov['runs_matched'], cov['runs'], 2)}) have expected "
        f"values; ffopportunity leaves out sacks ({cov['sacks']:,}). A differing receiver is "
        "almost always a target ffopportunity names where today's play-by-play has none "
        "(mostly 2006-2008): the per-play xFP follows ffopportunity, like its weekly file.",
        "",
        "## 2. Points: the plays add up to the weekly stat line",
        "",
        f"{n:,} player-games. Summing the fantasy points of every play (config/scoring.yaml, "
        f"the same weights) reproduces the weekly stat line within {POINTS_BOUND} points for "
        f"**{n_within:,} ({_pct(n_within, n, 2)})**, to the cent for {n_exact:,} "
        f"({_pct(n_exact, n, 2)}). The other {n - n_within} by cause (a player-game can have "
        "two):",
        "",
    ]
    cause_counts: dict[str, int] = {}
    for c in diffs.get_column("cause").to_list():
        for part in c.split(", "):
            cause_counts[part] = cause_counts.get(part, 0) + 1
    md += _table(
        ["cause", "player-games", "why"],
        [(k, v, CAUSES[k][1] if k in CAUSES else "-") for k, v in
         sorted(cause_counts.items(), key=lambda kv: (-kv[1], kv[0]))],
    )  # fmt: skip
    md += ["", f"The {min(N_DIFFERENCES, diffs.height)} largest differences:", ""]
    md += _table(
        ["game", "player", "pos", "weekly", "plays", "difference", "stats (weekly vs plays)"],
        [(r["game_id"], r["gsis_id"], r["position"], f"{r['fantasy_points']:.2f}",
          f"{r['play_points']:.2f}", f"{r['difference']:+.2f}", r["stats"])
         for r in diffs.head(N_DIFFERENCES).iter_rows(named=True)],
    )  # fmt: skip
    md += [
        "",
        "Without garbage time, the frame subtracts the garbage-time plays' points from the "
        "weekly points, so these rare differences stay in the non-garbage part.",
        "",
        "## 3. xFP: the plays add up to the weekly expected points",
        "",
        f"{with_x.height:,} player-games have an ffopportunity row (the others had no target, "
        f"carry or pass). The per-play xFP sum is within the rounding bound ({bound} points: "
        "every weekly expected stat is stored with 2 decimals) of the weekly xFP for "
        f"**{n_x_within:,} ({_pct(n_x_within, with_x.height, 3)})**; the largest gap is "
        f"{float(dx.max() or 0):.4f}.",
    ]
    if n_x_within < with_x.height:
        md += [
            "",
            f"The {with_x.height - n_x_within} outside are the passers of the two 2013 pass "
            "plays ffopportunity lists twice (a target repeated under a linebacker's name): "
            "its weekly file counts the play twice for the passer, the warehouse keeps one "
            "row.",
        ]
    md += [
        "",
        "## 4. How much comes from garbage time",
        "",
        "Share of the points, the xFP and the opportunities (targets, carries, pass attempts, "
        "two-point tries) that come from garbage-time plays:",
        "",
    ]
    pos_rows = []
    for r in pl.concat([by_pos, totals]).iter_rows(named=True):
        pos_rows.append((
            r["position"], f"{r['player_games']:,}", f"{r['fantasy_points']:,.0f}",
            _pct(r["points_garbage"], r["fantasy_points"]), f"{r['xfp']:,.0f}",
            _pct(r["xfp_garbage"], r["xfp"]),
            _pct(r["opportunities_garbage"], r["opportunities"]),
            f"{r['fpoe_per_game']:+.2f}", f"{r['fpoe_ng_per_game']:+.2f}",
        ))  # fmt: skip
    md += _table(
        ["position", "player-games", "points", "from garbage time", "xFP",
         "xFP from garbage time", "opportunities in garbage time", "FPOE per game",
         "FPOE per game without garbage time"], pos_rows,
    )  # fmt: skip
    md += [
        "",
        "## 5. Coverage by season and position",
        "",
        "Per season: the share of incomplete passes whose target is named (an unnamed target "
        "is nobody's expected catch), then per position the player-games in the frame, the "
        "share with xFP and the share whose plays reproduce the weekly points (the CSV next to "
        "this file has the garbage-time shares too).",
        "",
    ]
    cov_rows = []
    for season in seasons:
        sub = by_season_pos.filter(pl.col("season") == season)
        if not sub.height:
            continue
        cells = [str(season), _pct(targets.get(season, 0.0), 1.0 if season in targets else 0)]
        for p in FANTASY_POSITIONS:
            r = sub.filter(pl.col("position") == p)
            if not r.height:
                cells.append("-")
                continue
            x = r.row(0, named=True)
            cells.append(
                f"{x['player_games']:,} / {_pct(x['with_xfp'], x['player_games'], 0)} / "
                f"{_pct(x['points_within_bound'], x['player_games'])}"
            )
        cov_rows.append(cells)
    md += _table(["season", "incompletions with a target",
                  *[f"{p} (games / with xFP / points match)" for p in FANTASY_POSITIONS]],
                 cov_rows)  # fmt: skip
    thin = [x for x in seasons if x in targets and targets[x] < TARGET_COVERAGE_WARN]
    if thin:
        ratio = (
            by_season_pos.filter(pl.col("season").is_in(thin) & (pl.col("position") == "WR"))
            .select(pl.col("xfp").sum() / pl.col("fantasy_points").sum())
            .item()
        )
        later = (
            by_season_pos.filter(~pl.col("season").is_in(thin) & (pl.col("position") == "WR"))
            .select(pl.col("xfp").sum() / pl.col("fantasy_points").sum())
            .item()
        )
        md += [
            "",
            f"**Warning: {', '.join(map(str, thin))}.** The play-by-play (and so ffopportunity) "
            "names the target of almost no incomplete pass in these seasons, and nflverse's "
            "weekly `targets` is empty too. A receiver's xFP then counts little more than his "
            f"catches: WR xFP is {_pct(ratio or 0, 1)} of WR points there, "
            f"{_pct(later or 0, 1)} in the other seasons. Receivers' xFP and FPOE of those "
            "seasons are not comparable with later ones (passers' are: every pass has its "
            "passer).",
        ]
    md += [
        "",
        f"Left out: {no_pos:,} player-games without any point-in-time position (no roster row "
        f"or snap count public at the week's as-of; {no_pos_used:,} of them had a target, carry "
        "or pass), and every player at another position (FB, K, defense ...).",
        "",
        "## 6. Limitation (PROJECT_SPEC 6.3)",
        "",
        "The expected values come from ffopportunity's models and the garbage-time flag from "
        "nflfastR's win probability model. Both models were trained on many seasons, "
        "including seasons after some of the weeks shown here, so an old week's xFP knows a "
        "little about the future (a mild, known leak). Regression Watch uses them as they are "
        "in P1 and will re-estimate xFP walk-forward in P2 (only earlier seasons) and compare.",
        "",
    ]
    csv_rows = [
        {
            c: _csv_value(
                targets.get(r["season"]) if c == "incompletions_with_target" else r.get(c)
            )
            for c in CSV_COLUMNS
        }
        for r in pl.concat(
            [
                by_season_pos,
                by_pos.with_columns(pl.lit(None, dtype=pl.Int32).alias("season")).select(
                    by_season_pos.columns
                ),
            ]
        ).iter_rows(named=True)
    ]
    summary = [
        f"{n:,} player-games {first}-{last}: plays reproduce the weekly points within "
        f"{POINTS_BOUND} for {_pct(n_within, n, 2)}, the weekly xFP within {bound} for "
        f"{_pct(n_x_within, with_x.height, 3)}",
        "garbage-time share of points: "
        + ", ".join(
            f"{r['position']} {_pct(r['points_garbage'], r['fantasy_points'])}"
            for r in by_pos.iter_rows(named=True)
        ),
    ]
    return XfpReport("\n".join(md), csv_rows, summary, diffs)


def write_xfp_report(report: XfpReport, md_path: Path) -> Path:
    """Write the markdown and ``<same name>.csv`` next to it; return the CSV path."""
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(report.markdown, encoding="utf-8")
    csv_path = md_path.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(CSV_COLUMNS), lineterminator="\n")
        w.writeheader()
        w.writerows(report.csv_rows)
    return csv_path
