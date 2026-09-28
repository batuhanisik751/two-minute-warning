"""The feature report: does each feature separate hits from misses at all? (``twm radar
features-report``, step C3).

A sanity check before any model (C4), not a model. Among the rows a model will train and be
graded on (in the pool, final label, train-eligible, evaluation seasons 2014-2025), per feature
and position: how often it is missing, its mean for players who hit (``y_hit``) and who did
not, and its **single-feature ROC AUC**: the chance that a random hit has a higher value than a
random miss (0.5 = no signal; below 0.5 the feature works the other way round, so the AUC is
reported oriented, max(AUC, 1 - AUC), with the direction). Missing values are left out of the
AUC (the null rate says how many). Plus the xFP check on the warehouse (the re-scored xFP
against ffopportunity's own total) and how often each teammate rule fired.

Deterministic (sorted, fixed decimals; the only time in it is the warehouse's ``built_at``).
"""

from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import polars as pl
from sklearn.metrics import roc_auc_score

from twm.config import FANTASY_POSITIONS
from twm.modules.waiver_radar.features import (
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    FEATURE_FAMILIES,
    UNAVAILABILITY_RULES,
)
from twm.scoring import ScoringRules, nflverse_ppr, xfp_columns, xfp_sql

FIRST_EVAL_SEASON = 2014  # PROJECT_SPEC 8.1: walk-forward evaluation seasons 2014-2025
LAST_EVAL_SEASON = 2025
TOP_N = 10
GROUPS = (*FANTASY_POSITIONS, "all")

CSV_COLUMNS = (
    "position", "feature", "family", "n", "n_hit", "null_rate", "mean_hit", "mean_miss",
    "auc", "direction",
)  # fmt: skip


@dataclass(frozen=True)
class FeatureReport:
    markdown: str
    csv_rows: list[dict[str, object]]
    summary: list[str]


@dataclass(frozen=True)
class XfpCheck:
    """xFP re-scored with nflverse-PPR weights vs ffopportunity's total_fantasy_points_exp."""

    n_rows: int
    n_exact: int  # |difference| < 0.005: equal to the cent
    n_within_rounding: int  # |difference| <= rounding_bound
    rounding_bound: float
    n_beyond: int
    n_beyond_rush_2pt: int  # of those, rows with an expected rushing two-point conversion
    max_abs_diff: float
    max_abs_diff_without_rush_2pt: float


def _fmt(x: float | None, digits: int = 3) -> str:
    return "-" if x is None else f"{x:.{digits}f}"


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


def xfp_rounding_bound(rules: ScoringRules | None = None) -> float:
    """The largest difference rounding alone can explain: ffopportunity rounds each expected
    component to 0.01 (error <= 0.005 x its weight) and each of its pass/rec/rush point totals
    to 0.01 (<= 0.005 each; the total is their sum)."""
    rules = rules or nflverse_ppr()
    weights = sum(abs(rules.points[k]) for k in xfp_columns(rules))
    return round(0.005 * weights + 3 * 0.005, 6)


def xfp_check(db: Path | str, rules: ScoringRules | None = None) -> XfpCheck:
    """Compare the re-scored xFP (nflverse-PPR weights by default: the weights ffopportunity
    uses for its own points) with ``total_fantasy_points_exp`` on every warehouse row."""
    from twm.warehouse.build import connect

    rules = rules or nflverse_ppr()
    bound = xfp_rounding_bound(rules)
    con = connect(db, read_only=True)
    try:
        row = con.execute(f"""
            WITH d AS (
                SELECT abs({xfp_sql(rules)} - total_fantasy_points_exp) AS diff,
                       COALESCE(rush_two_point_conv_exp, 0) > 0 AS rush_2pt
                FROM fact_opportunity_week WHERE total_fantasy_points_exp IS NOT NULL
            )
            SELECT count(*), count(*) FILTER (WHERE diff < 0.005 - 1e-9),
                   count(*) FILTER (WHERE diff <= {bound} + 1e-9),
                   count(*) FILTER (WHERE diff > {bound} + 1e-9),
                   count(*) FILTER (WHERE diff > {bound} + 1e-9 AND rush_2pt),
                   COALESCE(max(diff), 0), COALESCE(max(diff) FILTER (WHERE NOT rush_2pt), 0)
            FROM d""").fetchone()
    finally:
        con.close()
    return XfpCheck(
        n_rows=int(row[0]),
        n_exact=int(row[1]),
        n_within_rounding=int(row[2]),
        rounding_bound=bound,
        n_beyond=int(row[3]),
        n_beyond_rush_2pt=int(row[4]),
        max_abs_diff=round(float(row[5]), 4),
        max_abs_diff_without_rush_2pt=round(float(row[6]), 4),
    )


def eval_rows(ds: pl.DataFrame, first: int, last: int) -> pl.DataFrame:
    """The rows a model trains and is graded on: in the pool, final label, train-eligible,
    seasons ``first``..``last``."""
    return ds.filter(
        pl.col("in_pool")
        & (pl.col("label_status") == "final")
        & pl.col("train_eligible")
        & pl.col("season").is_between(first, last)
    )


def _family(feature: str) -> str:
    return next(f for f, cols in FEATURE_FAMILIES.items() if feature in cols)


def _stats(sub: pl.DataFrame, feature: str) -> dict[str, object]:
    y = sub.get_column("y_hit").cast(pl.Int8)
    x = sub.get_column(feature).cast(pl.Float64)
    n, n_hit = sub.height, int(y.sum())
    known = x.is_not_null()
    xs, ys = x.filter(known), y.filter(known)
    mean_hit = xs.filter(ys == 1).mean() if int(ys.sum()) else None
    mean_miss = xs.filter(ys == 0).mean() if int((ys == 0).sum()) else None
    auc, direction = None, ""
    if 0 < int(ys.sum()) < ys.len():
        raw = float(roc_auc_score(ys.to_numpy(), xs.to_numpy()))
        auc = max(raw, 1.0 - raw)
        direction = "+" if raw >= 0.5 else "-"
    return {
        "n": n,
        "n_hit": n_hit,
        "null_rate": None if not n else round(1 - known.sum() / n, 4),
        "mean_hit": None if mean_hit is None else round(float(mean_hit), 4),
        "mean_miss": None if mean_miss is None else round(float(mean_miss), 4),
        "auc": None if auc is None else round(auc, 4),
        "direction": direction,
    }


def feature_table(ev: pl.DataFrame) -> pl.DataFrame:
    """One row per (position group, numeric feature): n, null rate, means, oriented AUC."""
    rows = []
    for group in GROUPS:
        sub = ev if group == "all" else ev.filter(pl.col("position") == group)
        for feature in FEATURE_COLUMNS:
            if feature in CATEGORICAL_FEATURES:
                continue
            rows.append({"position": group, "feature": feature, "family": _family(feature),
                         **_stats(sub, feature)})  # fmt: skip
    return pl.DataFrame(rows, infer_schema_length=None)


def _rule_counts(ev: pl.DataFrame) -> list[list[object]]:
    """Per rule (and for no teammate out): rows with at least one unavailable teammate of the
    player's own position out by that rule, their share, their hit rate, and how many of them
    have such a teammate out by that rule alone."""
    texts = ev.get_column("teammates_out").to_list()
    positions = ev.get_column("position").to_list()
    parsed = [
        [m for m in (json.loads(t) if t else []) if m["position"] == pos]
        for t, pos in zip(texts, positions, strict=True)
    ]
    hits = ev.get_column("y_hit").to_list()
    out = []
    for rule in UNAVAILABILITY_RULES:
        flags = [any(rule in m["rules"].split("+") for m in mates) for mates in parsed]
        n = sum(flags)
        h = sum(1 for f, y in zip(flags, hits, strict=True) if f and y)
        alone = sum(1 for mates in parsed if any(m["rules"] == rule for m in mates))
        out.append([rule, f"{n:,}", _pct(n, len(parsed)), _pct(h, n), f"{alone:,}"])
    none = [not mates for mates in parsed]
    n = sum(none)
    h = sum(1 for f, y in zip(none, hits, strict=True) if f and y)
    out.append(["(nobody out at his position)", f"{n:,}", _pct(n, len(parsed)), _pct(h, n), "-"])
    return out


def build_feature_report(
    dataset: pl.DataFrame,
    db: Path | str,
    *,
    first_eval_season: int = FIRST_EVAL_SEASON,
    last_eval_season: int = LAST_EVAL_SEASON,
    top_n: int = TOP_N,
) -> FeatureReport:
    """Render the report from a :func:`~twm.modules.waiver_radar.dataset.build_dataset` frame
    (the xFP check and ``built_at`` read the warehouse ``db``)."""
    built_at = _built_at(db)
    ev = eval_rows(dataset, first_eval_season, last_eval_season)
    table = feature_table(ev)
    xc = xfp_check(db)
    seasons = sorted(dataset.get_column("season").unique().to_list())
    lines = [
        "# Waiver Radar features: a single-feature sanity check",
        "",
        f"Generated by `uv run twm radar features-report` from the dataset built by `uv run "
        f"twm radar dataset` on the warehouse built at {built_at} (UTC). Deterministic: rerun "
        "on the same dataset to get the same file. Definitions: `docs/waiver_radar.md` "
        "(section Features) and `uv run twm glossary <feature>`.",
        "",
        f"The dataset has {dataset.height:,} rows (every rostered QB/RB/WR/TE at every "
        f"labelled Tuesday as-of of {seasons[0] if seasons else '-'}-"
        f"{seasons[-1] if seasons else '-'}), "
        f"{int(dataset.get_column('in_pool').sum()):,} of them in the pool. Rows used below: "
        f"in the pool, final label, train-eligible, seasons {first_eval_season}-"
        f"{last_eval_season}: {ev.height:,} rows, {int(ev.get_column('y_hit').sum()):,} hits "
        f"({_pct(int(ev.get_column('y_hit').sum()), ev.height)}).",
        "",
        "**How to read it.** AUC is the chance that a random player who hit (`y_hit`) has a "
        "higher value than a random one who did not: 0.5 is a coin flip, 0.6 a useful signal, "
        "0.7 strong for a single number. It is oriented (max(AUC, 1 - AUC)); `dir` + means "
        "higher values go with hits, - lower ones. Missing values are left out of the AUC and "
        "the means; `null` says how many rows miss the feature. A feature can be weak on its "
        "own and still help a model in combination (and the reverse: several features carry "
        "the same signal).",
        "",
        "## Hit rate by position",
        "",
    ]
    rows = []
    for pos in FANTASY_POSITIONS:
        sub = ev.filter(pl.col("position") == pos)
        n, h = sub.height, int(sub.get_column("y_hit").sum()) if sub.height else 0
        rows.append([pos, f"{n:,}", f"{h:,}", _pct(h, n)])
    lines += _table(["position", "rows", "hits", "y_hit rate"], rows)
    lines += ["", f"## Top {top_n} features per position (by oriented AUC)", ""]
    top_rows = []
    for group in GROUPS:
        t = (
            table.filter(pl.col("position") == group)
            .filter(pl.col("auc").is_not_null())
            .sort(["auc", "feature"], descending=[True, False])
            .head(top_n)
        )
        top_rows.append(
            [group]
            + [f"{r['feature']} {r['auc']:.3f}{r['direction']}" for r in t.iter_rows(named=True)]
        )
    lines += _table(["position", *[f"#{i}" for i in range(1, top_n + 1)]], top_rows)
    for group in GROUPS:
        lines += ["", f"## Every feature: {group}", ""]
        t = table.filter(pl.col("position") == group)
        lines += _table(
            ["feature", "family", "null", "mean (hit)", "mean (no hit)", "AUC", "dir"],
            [
                [r["feature"], r["family"],
                 "-" if r["null_rate"] is None else f"{100 * r['null_rate']:.1f}%",
                 _fmt(r["mean_hit"]), _fmt(r["mean_miss"]), _fmt(r["auc"]), r["direction"]]
                for r in t.iter_rows(named=True)
            ],
        )  # fmt: skip
    lines += [
        "",
        "## Teammate rules",
        "",
        "Rows (same scope) with at least one teammate of the player's own position who is "
        "out by each rule (a teammate can be out by several), and how often those rows hit; "
        "`alone` counts rows where such a teammate is out by that rule only. Most pool "
        "players are deep backups, so a teammate being out barely moves their hit rate on its "
        "own; it matters together with the player's own role (C4).",
        "",
    ]
    lines += _table(["rule", "rows", "share of rows", "y_hit rate", "alone"], _rule_counts(ev))
    lines += [
        "",
        "## xFP check (warehouse)",
        "",
        "xFP is re-scored from ffopportunity's expected components with config/scoring.yaml. "
        "With the nflverse-PPR weights (ffopportunity's own) it should reproduce "
        "`total_fantasy_points_exp` up to ffopportunity's rounding: each expected component "
        f"and each part total is stored with 2 decimals, so up to {xc.rounding_bound} points.",
        "",
    ]
    lines += _table(
        ["player-games", "equal to the cent", "within rounding", "beyond rounding",
         "of which rushing 2-pt tries", "max difference", "max without rushing 2-pt"],
        [[f"{xc.n_rows:,}", f"{xc.n_exact:,} ({_pct(xc.n_exact, xc.n_rows)})",
          f"{xc.n_within_rounding:,} ({_pct(xc.n_within_rounding, xc.n_rows)})",
          f"{xc.n_beyond:,}", f"{xc.n_beyond_rush_2pt:,}", xc.max_abs_diff,
          xc.max_abs_diff_without_rush_2pt]],
    )  # fmt: skip
    lines += [
        "",
        "The rows beyond rounding are rushing two-point tries: ffopportunity's weekly "
        "`rush_fantasy_points_exp` adds 0.1 x the expected yards of a two-point try, which "
        "its `rush_yards_gained_exp` (and real scoring) leaves out (docs/waiver_radar.md).",
        "",
    ]
    csv_rows = [
        {c: ("" if r[c] is None else r[c]) for c in CSV_COLUMNS}
        for r in table.sort("position", "feature").iter_rows(named=True)
    ]
    summary = [
        f"dataset: {dataset.height:,} rows; evaluation rows {ev.height:,} "
        f"({first_eval_season}-{last_eval_season}, in pool, final, train-eligible)",
        f"xFP check: {xc.n_within_rounding:,} of {xc.n_rows:,} player-games within rounding "
        f"({_pct(xc.n_within_rounding, xc.n_rows)}); {xc.n_beyond} beyond, "
        f"{xc.n_beyond_rush_2pt} of them rushing 2-pt tries",
    ]
    for row in top_rows:
        summary.append(f"top {row[0]}: " + ", ".join(row[1:4]))
    return FeatureReport("\n".join(lines).rstrip() + "\n", csv_rows, summary)


def write_feature_report(report: FeatureReport, md_path: Path) -> Path:
    """Write the markdown and ``<same name>.csv`` next to it; return the CSV path."""
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(report.markdown, encoding="utf-8")
    csv_path = md_path.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(CSV_COLUMNS), lineterminator="\n")
        w.writeheader()
        w.writerows(report.csv_rows)
    return csv_path
