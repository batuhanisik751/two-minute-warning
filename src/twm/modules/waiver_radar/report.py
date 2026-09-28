"""The candidate-pool report: pool sizes per season and position, and the evidence behind the
stand-in (``twm radar pool-report``, step C1).

Two checks the owner needs to trust the pool (docs/waiver_radar.md):

- **ECR vs fallback (2020+).** Seasons with a preseason cheat sheet are computed both ways, with
  the cheat sheet and with the 2013-2019 fallback (last season's PPG, rookies of rounds 1-2
  drafted). If the two agree closely, the fallback years are comparable to the ECR years.
- **Rostership.** FantasyPros records how many leagues roster each player (2020+ on average
  across sites, 2021-2023 also ESPN alone). A player owned in fewer than
  ``ownership_available_below`` percent of leagues is "really available": the report counts how
  often the pool agrees (a confusion matrix, precision and recall of ``in_pool``).

The output is deterministic (sorted, fixed decimals; the only time in it is the warehouse's
``built_at``), so two runs on the same warehouse can be diffed.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from twm.config import FANTASY_POSITIONS, PoolConfig, league
from twm.modules.waiver_radar.pool import PoolRules, pool_history

CSV_COLUMNS = (
    "season", "week", "as_of", "method", "used", "position", "n_universe", "n_pool",
    "n_excluded_preseason_only", "n_excluded_ppg_only", "n_excluded_both",
    "n_excluded_rookie_draft", "n_owned_avg", "n_pool_owned_below", "n_pool_owned_at_least",
    "n_out_owned_below", "n_out_owned_at_least",
)  # fmt: skip


@dataclass(frozen=True)
class ReportOutput:
    markdown: str
    csv_rows: list[dict[str, object]]
    summary: list[str]


def _fmt(x: float | None, digits: int = 1) -> str:
    return "-" if x is None else f"{x:.{digits}f}"


def _pct(num: int, den: int) -> str:
    return "-" if not den else f"{100 * num / den:.1f}%"


def _table(header: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


def _warehouse_info(db: Path | str) -> tuple[str, dict[str, dict[str, object]]]:
    from twm.warehouse.build import connect

    con = connect(db, read_only=True)
    try:
        built = con.execute("SELECT max(built_at) FROM build_manifest").fetchone()[0]
        row = con.execute(
            "SELECT notes FROM build_manifest WHERE table_name = 'fact_roster_week'"
        ).fetchone()
    finally:
        con.close()
    notes = json.loads(row[0]) if row else {}
    return (str(built) if built else "unknown"), notes.get("regime_by_season", {})


def _with_labels(hist: pl.DataFrame, cfg: PoolConfig) -> pl.DataFrame:
    exc = pl.col("excluded_by").fill_null("")
    below, high, low = cfg.ownership_available_below, cfg.ownership_high, cfg.ownership_low
    return hist.with_columns(
        exc.is_in(["preseason", "rookie_draft"]).alias("x_pre_only"),
        (exc == "ppg").alias("x_ppg_only"),
        exc.str.ends_with("+ppg").alias("x_both"),
        exc.str.starts_with("rookie_draft").alias("x_rookie"),
        pl.col("owned_avg").is_not_null().alias("has_avg"),
        (pl.col("owned_avg") < below).alias("avg_below"),
        (pl.col("owned_avg") >= high).alias("avg_high"),
        (pl.col("owned_avg") < low).alias("avg_low"),
        pl.col("owned_espn").is_not_null().alias("has_espn"),
        (pl.col("owned_espn") < below).alias("espn_below"),
        (pl.col("owned_espn") >= high).alias("espn_high"),
        (pl.col("owned_espn") < low).alias("espn_low"),
    )


def _auto_methods(hist: pl.DataFrame) -> pl.DataFrame:
    """Per season, the method the pool uses: 'ecr' if it was computed, else 'prior_ppg'."""
    return hist.group_by("season").agg(
        pl.when(pl.col("method").eq("ecr").any())
        .then(pl.lit("ecr"))
        .otherwise(pl.lit("prior_ppg"))
        .alias("auto_method")
    )


def _csv_rows(h: pl.DataFrame) -> list[dict[str, object]]:
    agg = (
        h.group_by("season", "week", "as_of", "method", "used", "position")
        .agg(
            pl.len().alias("n_universe"),
            pl.col("in_pool").sum().alias("n_pool"),
            pl.col("x_pre_only").sum().alias("n_excluded_preseason_only"),
            pl.col("x_ppg_only").sum().alias("n_excluded_ppg_only"),
            pl.col("x_both").sum().alias("n_excluded_both"),
            pl.col("x_rookie").sum().alias("n_excluded_rookie_draft"),
            pl.col("has_avg").sum().alias("n_owned_avg"),
            (pl.col("in_pool") & pl.col("avg_below")).sum().alias("n_pool_owned_below"),
            (pl.col("in_pool") & ~pl.col("avg_below")).sum().alias("n_pool_owned_at_least"),
            (~pl.col("in_pool") & pl.col("avg_below")).sum().alias("n_out_owned_below"),
            (~pl.col("in_pool") & ~pl.col("avg_below")).sum().alias("n_out_owned_at_least"),
        )
        .sort("season", "week", "method", "position")
    )
    rows = []
    for r in agg.iter_rows(named=True):
        r["as_of"] = r["as_of"].strftime("%Y-%m-%d %H:%M")
        rows.append({c: r[c] for c in CSV_COLUMNS})
    return rows


def build_report(
    db: Path | str, seasons: Sequence[int], *, rules: PoolRules | None = None
) -> ReportOutput:
    """Compute every as-of's pool of ``seasons`` (both methods where possible) and render the
    markdown report and the CSV rows."""
    lg = league()
    cfg = lg.pool
    rules = rules or PoolRules.from_config(lg)
    built_at, regimes = _warehouse_info(db)
    hist = pool_history(db, seasons, methods=("ecr", "prior_ppg"), rules=rules)
    auto = _auto_methods(hist)
    h = _with_labels(
        hist.join(auto, on="season", how="left").with_columns(
            (pl.col("method") == pl.col("auto_method")).alias("used")
        ),
        cfg,
    )
    used = h.filter(pl.col("used"))
    cut = rules.cutoffs
    teams = rules.teams if rules.teams is not None else lg.teams
    multiplier = rules.multiplier if rules.multiplier is not None else lg.candidate_pool_multiplier
    lines = [
        "# Waiver Radar candidate pool: sizes and validation",
        "",
        f"Generated by `uv run twm radar pool-report` from the warehouse built at {built_at} "
        "(UTC). Deterministic: rerun on the same warehouse to get the same file. Rules and "
        "definitions: `docs/waiver_radar.md`.",
        "",
        f"A player is **in the pool** (probably on waivers in a {teams}-team league) when he is "
        "on an NFL roster (status " + ", ".join(rules.roster_statuses) + ") and outside the top "
        "N at his position by BOTH the preseason list and points per game so far. N = "
        + ", ".join(f"{p} {cut[p]}" for p in FANTASY_POSITIONS)
        + f" (config/league.yaml: starter threshold x {multiplier:g}). "
        "Preseason list: FantasyPros' preseason cheat sheet (method `ecr`) where one exists, "
        "else last season's PPG rank (min "
        f"{rules.prior_season_min_games} games) with rookies drafted in rounds 1-"
        f"{rules.rookie_drafted_rounds} counted as drafted (method `prior_ppg`).",
        "",
        "One as-of per regular-season week (the Tuesday 14:00 UTC after it), for every week "
        "whose games are all in the cache (the current season stops at the last complete "
        "week).",
        "",
    ]

    # ---- 1. method and roster regime per season --------------------------------------
    seasons_seen = sorted(set(h.get_column("season").to_list()))
    weeks = used.group_by("season").agg(
        pl.col("week").n_unique().alias("n"),
        pl.col("week").min().alias("lo"),
        pl.col("week").max().alias("hi"),
    )
    wk = {r["season"]: r for r in weeks.iter_rows(named=True)}
    am = dict(auto.iter_rows())
    rows = []
    for s in seasons_seen:
        reg = regimes.get(str(s), {})
        share = reg.get("share_not_act")
        rows.append([
            s, am.get(s, "-"), wk[s]["n"] if s in wk else 0,
            f"{wk[s]['lo']}-{wk[s]['hi']}" if s in wk else "-",
            reg.get("regime", "-"), "-" if share is None else f"{100 * share:.2f}%",
        ])  # fmt: skip
    lines += ["## Method and roster snapshot per season", ""]
    lines += _table(
        ["season", "method", "as-ofs", "weeks", "roster snapshot", "played but not ACT"], rows
    )
    lines += [
        "",
        "Roster snapshot (measured by the build from the data: the share of players who "
        "played in a week but are not ACT on that week's roster): `post_game` = the week-N "
        "roster was taken after the games and becomes usable only at week N+1's as-of, so the "
        "week-1 as-of of such a season has no roster and an empty pool, and every later pool "
        "uses the previous week's roster; `game_day` = week N's roster is usable at week N's "
        "as-of.",
        "",
    ]

    # ---- 2. pool sizes -----------------------------------------------------------------
    per_week = used.group_by("season", "week", "position").agg(
        pl.len().alias("universe"),
        pl.col("in_pool").sum().alias("pool"),
        pl.col("x_pre_only").sum().alias("pre"),
        pl.col("x_ppg_only").sum().alias("ppg"),
        pl.col("x_both").sum().alias("both"),
        pl.col("x_rookie").sum().alias("rookie"),
    )
    sizes = (
        per_week.filter(pl.col("universe") > 0)
        .group_by("season", "position")
        .agg(
            pl.len().alias("weeks"),
            pl.col("universe").mean().alias("universe"),
            pl.col("pool").mean().alias("pool_mean"),
            pl.col("pool").min().alias("pool_min"),
            pl.col("pool").max().alias("pool_max"),
            pl.col("pre").mean().alias("pre"),
            pl.col("ppg").mean().alias("ppg"),
            pl.col("both").mean().alias("both"),
            pl.col("rookie").mean().alias("rookie"),
        )
        .sort("season", "position")
    )
    lines += [
        "## Pool size per season and position",
        "",
        "Per as-of averages over the season's weeks with a non-empty universe (the week-1 "
        "as-of of 2013-2015 has none). universe = rostered players at the position; excluded "
        "= kept out of the pool by the preseason list only (rookie rule included), by PPG only, "
        "or by both; rookie = drafted rookies among them (prior_ppg seasons).",
        "",
    ]
    lines += _table(
        ["season", "pos", "method", "weeks", "universe", "pool mean", "pool min", "pool max",
         "excl. preseason", "excl. ppg", "excl. both", "rookie"],
        [[r["season"], r["position"], am.get(r["season"], "-"), r["weeks"],
          _fmt(r["universe"]), _fmt(r["pool_mean"]), r["pool_min"], r["pool_max"],
          _fmt(r["pre"]), _fmt(r["ppg"]), _fmt(r["both"]), _fmt(r["rookie"])]
         for r in sizes.iter_rows(named=True)],
    )  # fmt: skip
    lines.append("")

    # ---- 3. ECR vs fallback ------------------------------------------------------------
    ecr_seasons = auto.filter(pl.col("auto_method") == "ecr").get_column("season").to_list()
    both = h.filter(pl.col("season").is_in(ecr_seasons))
    lines += ["## Validation (a): ECR pool vs the 2013-2019 fallback, 2020 on", ""]
    if both.height:
        wide = (
            both.select("season", "week", "gsis_id", "position", "method", "in_pool")
            .pivot(on="method", index=["season", "week", "gsis_id", "position"],
                   values="in_pool")
        )  # fmt: skip
        wide = wide.with_columns(
            (pl.col("ecr") & pl.col("prior_ppg")).alias("b"),
            (pl.col("ecr") & ~pl.col("prior_ppg")).alias("e"),
            (~pl.col("ecr") & pl.col("prior_ppg")).alias("f"),
            (~pl.col("ecr") & ~pl.col("prior_ppg")).alias("n"),
        )
        jac = (
            wide.group_by("season", "week")
            .agg(pl.col("b").sum(), (pl.col("b") | pl.col("e") | pl.col("f")).sum().alias("u"))
            .with_columns((pl.col("b") / pl.col("u")).alias("j"))
        )
        tot = wide.group_by("season").agg(
            pl.col("b").sum(), pl.col("e").sum(), pl.col("f").sum(), pl.col("n").sum()
        )
        js = jac.group_by("season").agg(
            pl.col("j").mean().alias("mean"), pl.col("j").min().alias("min"), pl.len()
        )
        rows = [
            [r["season"], r["len"], _fmt(r["mean"], 3), _fmt(r["min"], 3), t["b"], t["e"],
             t["f"], t["n"]]
            for r, t in zip(js.sort("season").iter_rows(named=True),
                            tot.sort("season").iter_rows(named=True), strict=True)
        ]  # fmt: skip
        lines += [
            "Same universe, two preseason lists. Jaccard = players in both pools / players "
            "in either (1 = identical pools), per as-of, all positions together; counts are "
            "player-weeks summed over the season.",
            "",
        ]
        lines += _table(
            ["season", "as-ofs", "Jaccard mean", "Jaccard min", "in both", "ECR pool only",
             "fallback pool only", "in neither"],
            rows,
        )  # fmt: skip
        pos_tot = wide.group_by("position").agg(
            pl.col("b").sum(), pl.col("e").sum(), pl.col("f").sum(), pl.col("n").sum()
        )
        lines += ["", "By position, all ECR seasons together:", ""]
        lines += _table(
            ["pos", "in both", "ECR only", "fallback only", "in neither", "agreement"],
            [[r["position"], r["b"], r["e"], r["f"], r["n"],
              _pct(r["b"] + r["n"], r["b"] + r["e"] + r["f"] + r["n"])]
             for r in pos_tot.sort("position").iter_rows(named=True)],
        )  # fmt: skip
    else:
        lines.append("No season with a preseason cheat sheet in the selected seasons.")
    lines.append("")

    # ---- 4. ownership --------------------------------------------------------------------
    below, high, low = cfg.ownership_available_below, cfg.ownership_high, cfg.ownership_low
    lines += [
        "## Validation (b): the pool vs real rostership",
        "",
        f"A player owned in fewer than {below:g}% of leagues counts as really available. "
        "Rostership is FantasyPros' figure from the latest weekly ranking before the as-of "
        "(the Friday before that week's games, so up to four days older than the pool), "
        "`owned_avg` = average across sites (2020 partly, 2021 on), `owned_espn` = ESPN "
        "(2020 partly, 2021-2023, 2024 partly). Only players FantasyPros ranked that week have "
        "a figure; the deep pool has none (see coverage), and those players are almost all "
        "available, so the precision below is a lower bound. precision = share of pool "
        "players that are really available; recall = share of really available players that "
        "are in the pool.",
        "",
    ]
    for src, label in (("avg", "owned_avg"), ("espn", "owned_espn")):
        has, bel, hi, lo = f"has_{src}", f"{src}_below", f"{src}_high", f"{src}_low"
        t = (
            h.filter(pl.col(has))
            .group_by("season", "method")
            .agg(
                pl.len().alias("n"),
                (pl.col("in_pool") & pl.col(bel)).sum().alias("tp"),
                (pl.col("in_pool") & ~pl.col(bel)).sum().alias("fp"),
                (~pl.col("in_pool") & pl.col(bel)).sum().alias("fn"),
                (~pl.col("in_pool") & ~pl.col(bel)).sum().alias("tn"),
                (pl.col("in_pool") & pl.col(hi)).sum().alias("pool_high"),
                (~pl.col("in_pool") & pl.col(lo)).sum().alias("out_low"),
            )
            .sort("season", "method")
        )
        cov = h.group_by("season", "method").agg(
            pl.len().alias("rows"),
            pl.col(has).sum().alias("with"),
            pl.col("in_pool").sum().alias("pool"),
            (pl.col("in_pool") & pl.col(has)).sum().alias("pool_with"),
        )
        t = t.join(cov, on=["season", "method"], how="left").sort("season", "method")
        lines += [f"### {label}", ""]
        if not t.height:
            lines += ["No rostership figures in the selected seasons.", ""]
            continue
        lines += _table(
            ["season", "method", "player-weeks with a figure", "pool rows with a figure",
             "pool & available", "pool & owned", "not pool & available", "not pool & owned",
             "precision", "recall", f"pool owned >= {high:g}%", f"not pool owned < {low:g}%"],
            [[r["season"], r["method"], f"{r['with']} of {r['rows']}",
              f"{r['pool_with']} of {r['pool']}", r["tp"], r["fp"], r["fn"], r["tn"],
              _pct(r["tp"], r["tp"] + r["fp"]), _pct(r["tp"], r["tp"] + r["fn"]),
              r["pool_high"], r["out_low"]]
             for r in t.iter_rows(named=True)],
        )  # fmt: skip
        lines.append("")

    summary = [
        f"{len(seasons_seen)} seasons, {used.select('season', 'week').n_unique()} as-ofs, "
        f"{hist.height} player rows (both methods where possible)",
    ]
    return ReportOutput("\n".join(lines).rstrip() + "\n", _csv_rows(h), summary)


def write_report(report: ReportOutput, md_path: Path) -> Path:
    """Write the markdown and ``<same name>.csv`` next to it; return the CSV path."""
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(report.markdown, encoding="utf-8")
    csv_path = md_path.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(CSV_COLUMNS), lineterminator="\n")
        w.writeheader()
        w.writerows(report.csv_rows)
    return csv_path
