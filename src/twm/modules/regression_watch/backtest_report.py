"""The Regression Watch backtest report (``twm regression backtest``, step D3):
reports/regression_watch/backtest.md and a CSV with every number in it.

Deterministic: the choices and metrics are exact functions of the warehouse, the bootstrap is
seeded and the only time printed is the warehouse's ``built_at``.
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from twm.config import FANTASY_POSITIONS, League
from twm.modules.regression_watch import backtest as bt
from twm.modules.regression_watch import projection as pj
from twm.modules.regression_watch import tags as tg

CSV_COLUMNS = (
    "table", "weeks", "position", "method", "metric", "group", "season", "value", "lo", "hi",
    "n", "n_seasons", "share_above_zero", "per_asof", "not_graded", "detail",
)  # fmt: skip
POSITIONS = (*FANTASY_POSITIONS, bt.POOLED)


@dataclass
class BacktestReport:
    markdown: str
    csv_rows: list[dict[str, object]]
    summary: list[str]
    accepted: bool


def _f(v: object, digits: int = 2) -> str:
    return "n/a" if v is None else f"{float(v):.{digits}f}"  # type: ignore[arg-type]


def _pct(v: object) -> str:
    return "n/a" if v is None else f"{100 * float(v):.1f}%"  # type: ignore[arg-type]


def _ci(r: dict[str, object] | None, digits: int = 2, pct: bool = False) -> str:
    if r is None or r.get("value") is None:
        return "n/a"
    fmt = _pct if pct else (lambda x: _f(x, digits))
    if r.get("lo") is None:
        return fmt(r["value"])
    return f"{fmt(r['value'])} ({fmt(r['lo'])} to {fmt(r['hi'])})"


def _t(header: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return lines + ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]


def _pos(p: str) -> str:
    return "pooled" if p == bt.POOLED else p


def _find(rows: list[dict[str, object]], **kw: object) -> dict[str, object] | None:
    for r in rows:
        if all(r.get(k) == v for k, v in kw.items()):
            return r
    return None


def _mae_table(m: list[dict[str, object]], weeks: str, metric: str = "mae") -> list[str]:
    """Per position: each method's value and the paired differences, with 95% intervals."""
    digits = 2 if metric == "mae" else 3
    rows = []
    for p in POSITIONS:
        got = {r["method"]: r for r in m
               if r["weeks"] == weeks and r["position"] == p and r["metric"] == metric}  # fmt: skip
        model = got.get("model")
        if model is None:
            continue
        rows.append([
            f"**{_pos(p)}**" if p == bt.POOLED else p, model["n"], _ci(model, digits),
            _ci(got.get("baseline_ppg"), digits), _ci(got.get("baseline_last3"), digits),
            _ci(got.get("model-baseline_ppg"), digits),
            _ci(got.get("model-baseline_last3"), digits),
        ])  # fmt: skip
    unit = "rows" if metric == "mae" else "lists"
    head = ["position", unit, "projection", "season-to-date PPG", "last-3 PPG",
            "projection - PPG", "projection - last 3"]  # fmt: skip
    return _t(head, rows)


def accepted(m: list[dict[str, object]]) -> tuple[bool, str]:
    """P1 acceptance (PROJECT_SPEC 10): the projection beats season-to-date PPG on MAE in the
    walk-forward (pooled, headline weeks). Also says whether the interval excludes 0."""
    d = _find(m, weeks="headline", position=bt.POOLED, metric="mae", method="model-baseline_ppg")
    model = _find(m, weeks="headline", position=bt.POOLED, metric="mae", method="model")
    ppg = _find(m, weeks="headline", position=bt.POOLED, metric="mae", method="baseline_ppg")
    if not d or d.get("value") is None or model is None or ppg is None:
        return False, "Acceptance cannot be judged: no graded rows."
    ok = float(d["value"]) < 0  # type: ignore[arg-type]
    sure = d.get("hi") is not None and float(d["hi"]) < 0  # type: ignore[arg-type]
    verdict = "MET" if ok else "NOT MET"
    text = (
        f"**P1 acceptance ({verdict}).** Pooled over the test seasons at the headline as-of "
        f"weeks, the projection's MAE is {_f(model['value'])} points per game against "
        f"{_f(ppg['value'])} for season-to-date PPG: a difference of {_ci(d)} (95% season-block "
        "bootstrap interval), " + ("which excludes 0." if sure else "which does NOT exclude 0.")
    )
    return ok, text


def _tag_table(t: list[dict[str, object]], weeks: str, positions: Sequence[str]) -> list[str]:
    rows = []
    for tag in tg.TAGS:
        for p in positions:
            got = {r["group"]: r for r in t
                   if r["weeks"] == weeks and r["position"] == p and r["tag"] == tag}  # fmt: skip
            tagged = got.get("tagged")
            if tagged is None:
                continue
            rows.append([
                tg.TAG_TITLES[tag], _pos(p), tagged["n"], _f(tagged["per_asof"], 1),
                _ci(tagged, pct=True), _ci(got.get("base"), pct=True),
                _ci(got.get("reference"), pct=True), tagged["not_graded"],
            ])  # fmt: skip
    head = ["tag", "position", "tags graded", "per as-of", "hit rate", "base rate",
            "reference", "not graded (< 3 games left)"]  # fmt: skip
    return _t(head, rows)


def _season_table(b: bt.Backtest) -> list[str]:
    g = b.graded.filter(pl.col("evaluable") & pl.col("week").is_in(list(bt.HEADLINE_WEEKS)))
    per = (
        g.group_by("season")
        .agg(
            pl.len().alias("n"),
            (pl.col("pred_model") - pl.col("ros_ppg")).abs().mean().alias("model"),
            (pl.col("pred_baseline_ppg") - pl.col("ros_ppg")).abs().mean().alias("ppg"),
        )
        .sort("season")
    )
    by = {r["season"]: r for r in per.iter_rows(named=True)}
    rows = []
    for s, ch in sorted(b.choices.items()):
        r = by.get(s, {})
        val = (
            f"{ch.validation_seasons[0]}-{ch.validation_seasons[-1]}"
            if ch.validation_seasons
            else "none"
        )  # noqa: E501
        rows.append([
            s, f"`{ch.variant.name}`", val, _f(ch.val_mae, 3), _f(ch.spec_val_mae, 3),
            f"{ch.sell.x:g}", f"{ch.buy.x:g}", r.get("n", 0), _f(r.get("model")),
            _f(r.get("ppg")),
        ])  # fmt: skip
    head = ["test season", "variant", "validation seasons", "validation MAE",
            "spec formula's validation MAE", "X Sell-high", "X Buy-low", "graded rows",
            "test MAE projection", "test MAE PPG"]  # fmt: skip
    return _t(head, rows)


def _week_table(b: bt.Backtest) -> list[str]:
    g = b.graded.filter(pl.col("evaluable"))
    per = (
        g.group_by("week")
        .agg(
            pl.len().alias("n"),
            *[(pl.col(f"pred_{m}") - pl.col("ros_ppg")).abs().mean().alias(m)
              for m in ("model", "baseline_ppg", "baseline_last3")],
        )
        .sort("week")
    )  # fmt: skip
    ex = b.excluded.group_by("week").agg(pl.col("universe").sum(), pl.col("left_out").sum())
    per = per.join(ex, on="week", how="left").sort("week")
    rows = [
        [r["week"], r["universe"], r["left_out"], r["n"], _f(r["model"]), _f(r["baseline_ppg"]),
         _f(r["baseline_last3"])]
        for r in per.iter_rows(named=True)
    ]  # fmt: skip
    head = ["as-of week", "universe rows", "left out (< 3 games left)", "graded", "projection",
            "PPG", "last 3"]  # fmt: skip
    return _t(head, rows)


def _priors_table(b: bt.Backtest) -> list[str]:
    rows = []
    for sp in b.priors:
        if sp.season not in b.choices:
            continue
        pr = sp.priors
        rows.append([
            sp.season, f"{pr.seasons[0]}-{pr.seasons[-1]}",
            *[_f(pr.factor(p, 8), 3) for p in FANTASY_POSITIONS],
            *[_f(pr.mean(p), 2) for p in FANTASY_POSITIONS],
        ])  # fmt: skip
    head = ["test season", "estimated on", *[f"r(8) {p}" for p in FANTASY_POSITIONS],
            *[f"mean FPOE/g {p}" for p in FANTASY_POSITIONS]]  # fmt: skip
    return _t(head, rows)


def _r(v: object, digits: int = 6) -> object:
    return round(float(v), digits) if isinstance(v, float) else v


def csv_rows(b: bt.Backtest) -> list[dict[str, object]]:
    """Every number of the report, long format."""
    out: list[dict[str, object]] = []
    for r in b.metrics:
        out.append({"table": r["kind"], "weeks": r["weeks"], "position": r["position"],
                    "method": r["method"], "metric": r["metric"], "value": _r(r["value"]),
                    "lo": _r(r["lo"]), "hi": _r(r["hi"]), "n": r["n"],
                    "n_seasons": r["n_seasons"],
                    "share_above_zero": _r(r["share_above_zero"])})  # fmt: skip
    for r in b.tags:
        out.append({"table": "tag", "weeks": r["weeks"], "position": r["position"],
                    "metric": r["tag"], "group": r["group"], "value": _r(r["value"]),
                    "lo": _r(r["lo"]), "hi": _r(r["hi"]), "n": r["n"],
                    "n_seasons": r["n_seasons"], "per_asof": r["per_asof"],
                    "not_graded": r["not_graded"]})  # fmt: skip
    for s, ch in sorted(b.choices.items()):
        val = ",".join(map(str, ch.validation_seasons))
        out.append({"table": "choice", "season": s, "method": ch.variant.name,
                    "metric": "validation_mae", "value": _r(ch.val_mae), "n": ch.n_val,
                    "detail": f"validation seasons {val}; "
                              f"spec formula {_r(ch.spec_val_mae)}"})  # fmt: skip
        for th in (ch.sell, ch.buy):
            out.append({"table": "threshold", "season": s, "metric": th.tag, "value": th.x,
                        "group": "fallback" if th.fallback else "chosen",
                        "n": th.n_tags, "per_asof": round(th.n_tags / th.n_asofs, 3)
                        if th.n_asofs else None,
                        "detail": f"validation precision {_r(th.precision)}"})  # fmt: skip
    for r in b.excluded.iter_rows(named=True):
        out.append({"table": "left_out", "weeks": str(r["week"]), "position": r["position"],
                    "n": r["universe"], "value": r["left_out"]})  # fmt: skip
    return [{c: row.get(c) for c in CSV_COLUMNS} for row in out]


def write_backtest_report(report: BacktestReport, md_path: Path) -> Path:
    """Write the markdown and ``<same name>.csv`` next to it; return the CSV path."""
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(report.markdown, encoding="utf-8")
    csv_path = md_path.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(CSV_COLUMNS), lineterminator="\n")
        w.writeheader()
        w.writerows(report.csv_rows)
    return csv_path


def _method_lines(league: League, test: Sequence[int], n_boot: int) -> list[str]:
    sizes = pj.universe_sizes(league)
    starters = league.starter_thresholds()
    return [
        "## How the backtest works",
        "",
        f"- **Universe** at each as-of (the official Tuesday after the week, season to date "
        f"only): QBs, RBs, WRs and TEs with at least {pj.MIN_GAMES} games whose PPG or xFP/game "
        "ranks inside teams x (starting slots the position can fill, FLEX included) x the pool "
        "multiplier: " + ", ".join(f"{p} {n}" for p, n in sizes.items()) + ".",
        "- **Projection** = recency-weighted xFP/game + r(g) x FPOE/game, where r(g) is D2's "
        "shrinkage factor for his position after his g games, estimated only on seasons "
        "2009 .. S-1 for season S. 24 variants: shrink toward 0 (the spec formula) or toward "
        "the position's average FPOE; recency half-life none / 8 / 4 / 2 games; garbage time "
        "kept, left out of the efficiency, or left out of both parts (opportunity scaled back "
        "up by the position's xFP / no-garbage xFP ratio of the earlier seasons).",
        f"- **Walk-forward**: test seasons {test[0]}-{test[-1]}. For test season S the variant "
        "with the lowest MAE on the validation seasons 2010 .. S-1 (headline weeks) is used; "
        "the test season is never looked at. Each validation season is itself projected with "
        "shrinkage from the seasons before it.",
        f"- **Outcome**: points per game over the rest of the regular season (games played, "
        "rows public strictly after the as-of). Players with fewer than "
        f"{pj.MIN_GAMES} games left are left out and counted (table below).",
        "- **Baselines**: season-to-date PPG and the average of the last 3 games.",
        f"- **Intervals**: 95%, {n_boot:,} resamples of whole seasons (the Radar's season-block "
        "bootstrap); differences are paired (the same resampled seasons for both methods). "
        "Spearman is computed per weekly list of a position and averaged.",
        "- **Tags**: Sell-high = FPOE/game in the top decile of his position's universe AND "
        "the projection at least X points/game below his PPG; Buy-low the mirror (bottom "
        "decile, at least X above); Legit = PPG rank inside the starter threshold ("
        + ", ".join(f"{p} {n}" for p, n in starters.items())
        + ") with FPOE/game not in the top decile. X (0 to 6 by 0.5) is chosen per test season "
        f"on the validation seasons: the best precision among the X that tag at least "
        f"{tg.MIN_TAGS_PER_ASOF} players per as-of. Hits: Sell-high = rest-of-season PPG "
        "below his PPG at the as-of; Buy-low = above; Legit = his rest-of-season PPG still "
        "ranks inside the threshold (among every player of the position with 3+ games left).",
        "",
    ]


LIMITS = [
    "## Limits",
    "",
    "- xFP and the garbage-time flag come from nflverse models trained on many seasons, "
    "including later ones (PROJECT_SPEC 6.3): an old week's xFP knows a little about the "
    "future. P2 re-estimates xFP walk-forward.",
    "- Stat corrections are in the cache's latest version (docs/assumptions.md section 12).",
    "- The outcome counts games played: a player who gets hurt is graded on the games he "
    "played, and one with fewer than 3 games left is not graded at all.",
    "- One variant for all positions per season; r(g) has one noise size per position.",
    "- Validation and test seasons share the same league-wide trends; the season-block "
    "bootstrap treats seasons as independent.",
    "",
]


def _tag_sentences(t: list[dict[str, object]]) -> list[str]:
    out = []
    for tag in tg.TAGS:
        got = {r["group"]: r for r in t if r["weeks"] == "headline"
               and r["position"] == bt.POOLED and r["tag"] == tag}  # fmt: skip
        tagged, base, ref = got.get("tagged"), got.get("base"), got.get("reference")
        if not tagged or tagged.get("value") is None or not base:
            continue
        beats = tagged.get("lo") is not None and float(tagged["lo"]) > float(base["value"])  # type: ignore[arg-type]
        out.append(
            f"- **{tg.TAG_TITLES[tag]}**: {_pct(tagged['value'])} of {tagged['n']} tags came "
            f"true against a base rate of {_pct(base['value'])} ({bt.TAG_GROUPS[tag][0]}) and "
            f"{_pct(ref['value'] if ref else None)} for {bt.TAG_GROUPS[tag][1]}; "
            + ("its interval is above the base rate." if beats
               else "its interval does NOT clear the base rate.")
        )  # fmt: skip
    return out


def build_backtest_report(
    b: bt.Backtest, league: League, built_at: str = "unknown"
) -> BacktestReport:
    """The markdown, the CSV rows and a short summary of a :class:`backtest.Backtest`."""
    test = list(b.test_seasons)
    ok, verdict = accepted(b.metrics)
    variants = sorted({ch.variant.name for ch in b.choices.values()})
    md = [
        "# Regression Watch backtest: rest-of-season projection and tags (step D3)",
        "",
        f"`uv run twm regression backtest` on the warehouse built {built_at}. Walk-forward "
        f"test seasons {test[0]}-{test[-1]}, headline as-of weeks "
        f"{', '.join(map(str, bt.HEADLINE_WEEKS))}; all weeks 4-14 as a secondary table. "
        "Points per game, full PPR (config/scoring.yaml). Glossary: `twm glossary ppg_ros`.",
        "",
        verdict,
        "",
        f"Variants chosen (on earlier seasons only): {', '.join(f'`{v}`' for v in variants)} "
        "(by season below).",
        "",
        *_method_lines(league, test, b.n_boot),
        f"## Headline: mean absolute error (points per game), as-of weeks "
        f"{', '.join(map(str, bt.HEADLINE_WEEKS))}",
        "",
        "Lower is better; a negative difference means the projection was closer. Rows = "
        "graded player-as-ofs; value (95% interval).",
        "",
        *_mae_table(b.metrics, "headline"),
        "",
        "## Rank correlation (Spearman), headline weeks",
        "",
        "Higher is better: how well each method orders the players of one position in one "
        "week, averaged over those weekly lists.",
        "",
        *_mae_table(b.metrics, "headline", "spearman"),
        "",
        "## All as-of weeks 4-14 (secondary)",
        "",
        *_mae_table(b.metrics, "all"),
        "",
        *_mae_table(b.metrics, "all", "spearman"),
        "",
        "## By as-of week (all test seasons, pooled positions)",
        "",
        *_week_table(b),
        "",
        "## By test season: what was chosen, and how it did",
        "",
        *_season_table(b),
        "",
        "## Tags (test seasons, headline weeks)",
        "",
        *_tag_sentences(b.tags),
        "",
        *_tag_table(b.tags, "headline", POSITIONS),
        "",
        "All as-of weeks 4-14, pooled positions:",
        "",
        *_tag_table(b.tags, "all", (bt.POOLED,)),
        "",
        "## Shrinkage used for each test season",
        "",
        *_priors_table(b),
        "",
        *LIMITS,
    ]
    summary = [verdict.replace("**", ""), *[s.replace("**", "") for s in _tag_sentences(b.tags)]]
    return BacktestReport("\n".join(md), csv_rows(b), summary, ok)
