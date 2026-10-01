"""The WP backtest report (G1): reports/decisions/wp_backtest.md + .csv.

Our walk-forward model (``own``) against nflfastR's ``wp`` (no spread) and ``vegas_wp`` (with
the closing spread) on the SAME plays: every test play 2006-2025 where nflfastR has both
numbers. Metrics: Brier score and log loss (lower = better) and the expected calibration error
(ECE: the play-weighted mean gap between predicted and observed win rates over 10 fixed-width
probability bins). Pooled, per era, per slice (4th quarter, one-score games, first play of a
drive) with **season-block bootstrap** 95% intervals (:mod:`twm.backtest.metrics`: whole
seasons are resampled, 2,000 times, with a fixed seed); per season with game-block intervals
for the difference to ``vegas_wp``. Deterministic: no timestamp, sorted rows.

Caveat (PROJECT_SPEC 6.3): nflfastR's models were fit on many seasons, including seasons we
test on, so on those seasons their numbers are partly in-sample; ours never saw the season it
scores.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import polars as pl

from twm.backtest import metrics as mt
from twm.modules.decisions import wp, wp_smooth_report
from twm.modules.decisions.wp_data import KEYS, LABEL, States

METHODS = {"own": "prob", "nflfastr_wp": "wp", "nflfastr_vegas_wp": "vegas_wp"}
LABELS = {"own": "Our model", "nflfastr_wp": "nflfastR wp",
          "nflfastr_vegas_wp": "nflfastR vegas_wp"}  # fmt: skip
ERAS = (("2006-2014", 2006, 2014), ("2015-2022", 2015, 2022), ("2023-2025", 2023, 2025))
SLICES = {
    "4th quarter": pl.col("qtr") == 4,
    "one-score games (lead of 8 or less)": pl.col("score_differential").abs() <= 8,
    "first play of a drive": pl.col("first_of_drive"),
    "overtime": pl.col("qtr") >= 5,
}
N_BINS = 10


def report_paths() -> tuple[Path, Path]:
    from twm.config import ROOT

    md = ROOT / "reports" / "decisions" / "wp_backtest.md"
    return md, md.with_suffix(".csv")


def load_backtest(
    states: States, seasons: Sequence[int] = wp.TEST_SEASONS, out_dir: Path | None = None
) -> tuple[pl.DataFrame, list[dict]]:
    """Every saved fold's test predictions joined to their play states (label, nflfastR
    columns, slices), and the fold summaries. Raises WpModelError when a fold is missing or
    does not cover exactly its season's plays."""
    d = out_dir if out_dir is not None else wp.backtest_dir()
    missing = [s for s in seasons if not (d / f"fold_{s}.json").exists()]
    if missing:
        raise wp.WpModelError(f"missing WP backtest folds {missing}: run the backtest first")
    summaries = [json.loads((d / f"fold_{s}.json").read_text()) for s in seasons]
    preds = pl.concat([pl.read_parquet(d / f"fold_{s}.parquet") for s in seasons])
    rows = states.rows.with_columns(
        (pl.col("play_id").rank("ordinal").over("game_id", "fixed_drive") == 1)
        .and_(pl.col("fixed_drive").is_not_null())
        .alias("first_of_drive")
    ).filter(pl.col("season").is_in(list(seasons)))
    if preds.height != rows.height:
        raise wp.WpModelError(f"{preds.height} predictions for {rows.height} test plays")
    cols = [*KEYS, LABEL, "wp", "vegas_wp", "qtr", "score_differential", "first_of_drive"]
    frame = rows.select(cols).join(preds, on=list(KEYS), how="inner").sort(list(KEYS))
    if frame.height != rows.height:
        raise wp.WpModelError("the saved predictions are not the test plays of today's states")
    return frame, summaries


# --------------------------------------------------------------------------------------
# Metrics with intervals
# --------------------------------------------------------------------------------------


def per_play(frame: pl.DataFrame) -> pl.DataFrame:
    """Rows where every method has a probability, with each method's per-play Brier term
    ``b_<method>`` and log-loss term ``l_<method>`` (their means are the Brier score and the
    log loss)."""
    eps = 1e-12
    y = pl.col(LABEL).cast(pl.Float64)
    exprs = []
    for m, col in METHODS.items():
        p = pl.col(col).cast(pl.Float64)
        q = p.clip(eps, 1.0 - eps)
        exprs.append(((p - y) ** 2).alias(f"b_{m}"))
        exprs.append((-(y * q.log() + (1.0 - y) * (1.0 - q).log())).alias(f"l_{m}"))
    known = pl.all_horizontal([pl.col(c).is_not_null() for c in METHODS.values()])
    return frame.filter(known).with_columns(exprs)


def mean_interval(sub: pl.DataFrame, value: str, block: str = "season") -> mt.Interval:
    """Pooled mean of ``value`` over plays with a block-bootstrap interval."""
    return mt.block_bootstrap(sub.select(block, value), block=block, value=value)


def ece(sub: pl.DataFrame, col: str) -> tuple[float | None, float | None, float | None]:
    """Expected calibration error (10 fixed-width bins, play-weighted mean |predicted -
    observed|) with a season-block bootstrap interval (None when one season)."""
    if sub.height == 0:
        return None, None, None
    seasons = sorted(sub.get_column("season").unique().to_list())
    pos = {s: i for i, s in enumerate(seasons)}
    p = sub.get_column(col).cast(pl.Float64).to_numpy()
    y = sub.get_column(LABEL).cast(pl.Float64).to_numpy()
    si = np.array([pos[s] for s in sub.get_column("season").to_list()])
    b = np.clip(np.floor(p * N_BINS).astype(np.int64), 0, N_BINS - 1)
    shape = (len(seasons), N_BINS)
    n, sp, sy = (np.zeros(shape) for _ in range(3))
    np.add.at(n, (si, b), 1.0)
    np.add.at(sp, (si, b), p)
    np.add.at(sy, (si, b), y)

    def value(n_, sp_, sy_):  # arrays (..., N_BINS)
        with np.errstate(invalid="ignore", divide="ignore"):
            gap = np.where(n_ > 0, np.abs(sp_ - sy_) / np.where(n_ > 0, n_, 1.0), 0.0)
        return (n_ * gap).sum(axis=-1) / n_.sum(axis=-1)

    point = float(value(n.sum(0), sp.sum(0), sy.sum(0)))
    if len(seasons) < 2:
        return point, None, None
    idx = mt.block_indices(len(seasons))
    boot = value(n[idx].sum(1), sp[idx].sum(1), sy[idx].sum(1))
    lo, hi = np.quantile(boot, [(1 - mt.LEVEL) / 2, 1 - (1 - mt.LEVEL) / 2])
    return point, float(lo), float(hi)


def _row(table, scope, subset, method, metric, value, lo=None, hi=None, n=0, blocks=0) -> dict:
    return {"table": table, "scope": scope, "subset": subset, "method": method,
            "metric": metric, "value": value, "lo": lo, "hi": hi, "n_plays": n,
            "n_blocks": blocks}  # fmt: skip


def metric_rows(sub: pl.DataFrame, scope: str, subset: str) -> list[dict]:
    """Brier, log loss and ECE per method, and our model minus each nflfastR column (Brier and
    log loss; negative = ours is better), with season-block intervals."""
    out = []
    n, k = sub.height, sub.get_column("season").n_unique()
    for m, col in METHODS.items():
        for metric, c in (("brier", f"b_{m}"), ("log_loss", f"l_{m}")):
            iv = mean_interval(sub, c)
            out.append(_row("metrics", scope, subset, m, metric, iv.value, iv.lo, iv.hi, n, k))
        e = ece(sub, col)
        out.append(_row("metrics", scope, subset, m, "ece", *e, n, k))
    for other in ("nflfastr_wp", "nflfastr_vegas_wp"):
        for metric, pre in (("brier", "b"), ("log_loss", "l")):
            d = sub.select("season", (pl.col(f"{pre}_own") - pl.col(f"{pre}_{other}")).alias("d"))
            iv = mean_interval(d, "d")
            out.append(_row("difference", scope, subset, f"own - {other}", metric, iv.value,
                            iv.lo, iv.hi, n, k))  # fmt: skip
    return out


def season_rows(pp: pl.DataFrame) -> list[dict]:
    """Per test season: each method's Brier and log loss, and our log loss minus vegas_wp's
    with a game-block bootstrap interval (one season = one block, so games are resampled)."""
    out = []
    for s in sorted(pp.get_column("season").unique().to_list()):
        sub = pp.filter(pl.col("season") == s)
        g = sub.get_column("game_id").n_unique()
        for m in METHODS:
            for metric, c in (("brier", f"b_{m}"), ("log_loss", f"l_{m}")):
                v = float(sub.get_column(c).mean())
                out.append(_row("season", str(s), "all plays", m, metric, v, n=sub.height))
        for other in ("nflfastr_wp", "nflfastr_vegas_wp"):
            d = sub.select("game_id", (pl.col("l_own") - pl.col(f"l_{other}")).alias("d"))
            iv = mean_interval(d, "d", block="game_id")
            out.append(_row("season", str(s), "all plays", f"own - {other}", "log_loss",
                            iv.value, iv.lo, iv.hi, sub.height, g))  # fmt: skip
    return out


def reliability_rows(pp: pl.DataFrame) -> list[dict]:
    """Pooled reliability: 10 fixed-width bins per method (rows, mean predicted, observed win
    rate with a season-block interval)."""
    out = []
    for m, col in METHODS.items():
        idx = (pl.col(col) * N_BINS).floor().clip(0, N_BINS - 1).cast(pl.Int64)
        f = pp.select("season", pl.col(col).alias("p"), pl.col(LABEL).cast(pl.Float64).alias("y"),
                      idx.alias("bin"))  # fmt: skip
        for b in range(N_BINS):
            sub = f.filter(pl.col("bin") == b)
            k = sub.get_column("season").n_unique()
            subset = f"{b / N_BINS:.1f}-{(b + 1) / N_BINS:.1f}"
            if sub.height == 0:
                out.append(_row("reliability", "pooled", subset, m, "observed", None))
                continue
            iv = mean_interval(sub, "y")
            mp = float(sub.get_column("p").mean())
            out.append(_row("reliability", "pooled", subset, m, "mean_predicted", mp,
                            n=sub.height, blocks=k))  # fmt: skip
            out.append(_row("reliability", "pooled", subset, m, "observed", iv.value, iv.lo,
                            iv.hi, sub.height, k))  # fmt: skip
    return out


# --------------------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------------------

SCHEMA = {"table": pl.String, "scope": pl.String, "subset": pl.String, "method": pl.String,
          "metric": pl.String, "value": pl.Float64, "lo": pl.Float64, "hi": pl.Float64,
          "n_plays": pl.Int64, "n_blocks": pl.Int64}  # fmt: skip


def compute(
    states: States,
    *,
    seasons: Sequence[int] = wp.TEST_SEASONS,
    out_dir: Path | None = None,
    progress=print,
) -> tuple[pl.DataFrame, list[dict], dict]:
    """(the long results table, the fold summaries, counts) from the saved folds."""
    frame, summaries = load_backtest(states, seasons, out_dir=out_dir)
    pp = per_play(frame)
    info = {"n_test": frame.height, "n_compared": pp.height,
            "n_games": pp.get_column("game_id").n_unique(), "drops": states.drops,
            "n_tie_games": states.n_tie_games}  # fmt: skip
    progress(f"report: {pp.height:,} of {frame.height:,} test plays have both nflfastR columns")
    first, last = min(seasons), max(seasons)
    rows = metric_rows(pp, "pooled", f"{first}-{last}")
    for name, lo, hi in ERAS:
        rows += metric_rows(pp.filter(pl.col("season").is_between(lo, hi)), "era", name)
    for name, cond in SLICES.items():
        rows += metric_rows(pp.filter(cond), "slice", name)
    progress("report: pooled, era and slice metrics done; per season and reliability next")
    rows += season_rows(pp)
    rows += reliability_rows(pp)
    side = None if out_dir is None else out_dir.parent  # tests: nothing outside out_dir
    rows += wp_smooth_report.rows(
        summaries, states.rows,
        select_dir=None if side is None else side / "wp_select",
        before=None if side is None else side / "wp_smoothness_g1.csv",
    )  # fmt: skip
    progress("report: smoothness (folds, validation candidates, nflfastR readings) done")
    return pl.DataFrame(rows, schema=SCHEMA, orient="row"), summaries, info


def _get(t: pl.DataFrame, table: str, scope: str, subset: str, method: str, metric: str) -> dict:
    hit = t.filter((pl.col("table") == table) & (pl.col("scope") == scope)
                   & (pl.col("subset") == subset) & (pl.col("method") == method)
                   & (pl.col("metric") == metric))  # fmt: skip
    return hit.row(0, named=True) if hit.height else {"value": None, "lo": None, "hi": None}


def _fmt(r: dict, nd: int = 4, scale: float = 1.0, sign: bool = False) -> str:
    if r.get("value") is None:
        return "-"
    f = f"{{:{'+' if sign else ''}.{nd}f}}"
    v = f.format(r["value"] * scale)
    if r.get("lo") is None:
        return v
    return f"{v} ({f.format(r['lo'] * scale)} to {f.format(r['hi'] * scale)})"


def _method_table(t: pl.DataFrame, scope: str, subset: str) -> list[str]:
    lines = ["| Method | Brier | Log loss | Calibration error (ECE, pts) |", "|---|---|---|---|"]
    for m in METHODS:
        b, ll, e = (_get(t, "metrics", scope, subset, m, x) for x in ("brier", "log_loss", "ece"))
        lines.append(f"| {LABELS[m]} | {_fmt(b)} | {_fmt(ll)} | {_fmt(e, 1, 100)} |")
    for other in ("nflfastr_wp", "nflfastr_vegas_wp"):
        b, ll = (_get(t, "difference", scope, subset, f"own - {other}", x)
                 for x in ("brier", "log_loss"))  # fmt: skip
        lines.append(f"| Ours minus {LABELS[other]} | {_fmt(b, sign=True)} | "
                     f"{_fmt(ll, sign=True)} | |")  # fmt: skip
    return lines


def _setup(summaries: list[dict], info: dict) -> list[str]:
    from twm.modules.decisions import wp_select as ws

    first, last = summaries[0], summaries[-1]
    names = sorted({s.get("candidate", "g1") for s in summaries})
    fam = "; ".join(f"**{n}** ({ws.CANDIDATES[n].description})" if n in ws.CANDIDATES else n
                    for n in names)  # fmt: skip
    n_trials = "/".join(str(n) for n in sorted({len(s["trials"]) for s in summaries}))
    kept = [s["test_season"] for s in summaries if s["calibration"].startswith("isotonic")]
    dropped = [s["test_season"] for s in summaries if "isotonic dropped" in s["calibration"]]
    d = info["drops"]
    return [
        "## Setup", "",
        f"- **Rows**: one per scrimmage play with a valid pre-snap state, from the warehouse's "
        f"`fact_play` + `fact_game` (1999-2025). Dropped: {d['not_scrimmage']:,} rows that are "
        f"not a scrimmage play (kickoffs, extra points, two-point tries, timeouts, `no_play` "
        f"penalty rows whose down is replayed), {d['missing_state']:,} with a missing or "
        f"impossible state, {d['tie']:,} plays of the {info['n_tie_games']} tie games (the label "
        "'the team with the ball wins' does not apply; excluded from training and metrics).",
        "- **Label**: the possession team before the snap won the game.",
        f"- **Walk-forward**: {len(summaries)} folds, test seasons "
        f"{first['test_season']}-{last['test_season']}. The first fold is '{first['fold']}'; the "
        f"last is '{last['fold']}'. Each model learns from earlier seasons only; its settings "
        f"are tuned on the season before the test season ({n_trials} setting(s) per fold, "
        "log loss), then it is refit on every earlier season. Single-threaded and "
        "deterministic.",
        f"- **Model** (G1b): {fam}. Chosen on the validation seasons 2004-2005 by a rule fixed "
        "beforehand (section 'Smoothness'). G1's original model, one monotone LightGBM (31 or "
        "63 leaves x at least 200 or 1,000 plays per leaf, learning rate 0.05, early stopping), "
        "was well calibrated but uneven between neighbouring states.",
        "- **Features**: score difference, seconds left in the game and in the half, half, down, "
        "distance, yards to the end zone, both teams' timeouts, receives the second-half kickoff "
        "(from the opening kickoff), home / away / neutral, the closing spread from the "
        "offense's view, era flags (2015 extra-point rule, 2023+ kickoff rules) and two "
        "time interactions (spread x time left, lead / time left: nflfastR's own, chosen on "
        "1999-2003 -> 2004-2005 validation before any test season was scored); G1b adds "
        "`drive_value` (the net points, for minus against, that the ball is expected to add "
        "before halftime: a table measured on 1999-2005) and `z_margin` (the lead, drive "
        "value and remaining spread in standard deviations of what is left: a random-walk "
        "view).",
        f"- **Calibration**: isotonic only if it beats the raw model out of sample on the "
        f"validation season (fit on half its games, scored on the other half) and (G1b) the "
        f"calibrated model still meets the smoothness limits (a step-function calibrator "
        f"re-creates steps). Kept in {len(kept)} of {len(summaries)} folds"
        f"{': ' + ', '.join(map(str, kept)) if kept else ''}"
        f"{'; dropped for smoothness in ' + ', '.join(map(str, dropped)) if dropped else ''}.",
        f"- **Comparison**: {info['n_compared']:,} of {info['n_test']:,} test plays "
        f"({info['n_games']:,} games) have nflfastR's `wp` and `vegas_wp`; every number below "
        "is on those plays. Intervals: 95%, season-block bootstrap (2,000 resamples). The "
        "calibration error's interval is rough: resampling adds noise to every bin, which "
        "pushes the error up, so its interval can sit at or above the point value.",
        "- **Caveat** (PROJECT_SPEC 6.3): nflfastR's models were fit once on many seasons, "
        "including seasons tested here, so their numbers are partly in-sample; ours never saw "
        "the season it scores. `vegas_wp` uses the same closing spread as ours; `wp` uses none.",
        "",
    ]  # fmt: skip


def _season_table(t: pl.DataFrame, summaries: list[dict]) -> list[str]:
    lines = ["| Season | Plays | Brier: ours / wp / vegas_wp | Log loss: ours / wp / vegas_wp "
             "| Ours minus vegas_wp, log loss (game-block 95%) | Calibration | Trees | Fold "
             "seconds |", "|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for s in summaries:
        y = str(s["test_season"])
        b = " / ".join(_fmt(_get(t, "season", y, "all plays", m, "brier")) for m in METHODS)
        ll = " / ".join(_fmt(_get(t, "season", y, "all plays", m, "log_loss")) for m in METHODS)
        d = _get(t, "season", y, "all plays", "own - nflfastr_vegas_wp", "log_loss")
        lines.append(f"| {y} | {d.get('n_plays', 0):,} | {b} | {ll} | {_fmt(d, sign=True)} | "
                     f"{s['calibration'].split()[0]} | {_trees(s['params'])} | "
                     f"{s['seconds']:.0f} |")  # fmt: skip
    return lines


def _trees(params: dict) -> str:
    """Trees of the refit: one number, or members x (fewest-most) for a bag."""
    n = params.get("n_estimators")
    if isinstance(n, list):
        return f"{len(n)} x {min(n)}-{max(n)}"
    return "-" if n is None else str(n)


def _reliability_table(t: pl.DataFrame) -> list[str]:
    lines = ["| Predicted | " + " | ".join(f"{LABELS[m]}: plays, predicted, observed (95%)"
                                           for m in METHODS) + " |",
             "|---|---|---|---|"]  # fmt: skip
    for b in range(N_BINS):
        subset = f"{b / N_BINS:.1f}-{(b + 1) / N_BINS:.1f}"
        cells = []
        for m in METHODS:
            mp = _get(t, "reliability", "pooled", subset, m, "mean_predicted")
            ob = _get(t, "reliability", "pooled", subset, m, "observed")
            if mp["value"] is None:
                cells.append("-")
                continue
            cells.append(f"{mp['n_plays']:,}, {mp['value']:.3f}, {_fmt(ob, 3)}")
        lines.append(f"| {subset} | " + " | ".join(cells) + " |")
    return lines


def _era_test(summaries: list[dict]) -> list[str]:
    rows = [(s["test_season"], s["era_ablation"]) for s in summaries if s.get("era_ablation")]
    if not rows:
        from twm.modules.decisions import wp_select as ws

        fams = {s.get("candidate", "g1") for s in summaries}
        if any(f in ws.CANDIDATES and not ws.CANDIDATES[f].era_ablation for f in fams):
            return ["Not run for this model family: its design uses the era flags (as "
                    "possession terms), so they cannot be dropped like a tree "
                    "feature."]  # fmt: skip
        return ["No fold had an era flag that varies within its tuning seasons."]
    lines = ["| Test season | Flags that vary while tuning | Validation log loss with | "
             "without | With minus without |", "|---|---|---|---|---|"]  # fmt: skip
    diffs = []
    for season, a in rows:
        d = a["val_logloss_with"] - a["val_logloss_without"]
        diffs.append(d)
        lines.append(f"| {season} | {', '.join(a['varying'])} | {a['val_logloss_with']:.5f} | "
                     f"{a['val_logloss_without']:.5f} | {d:+.5f} |")  # fmt: skip
    better = sum(d < 0 for d in diffs)
    lines += ["", f"The flags lowered the validation log loss in {better} of {len(diffs)} folds "
              f"(mean change {np.mean(diffs):+.5f}; negative = they help). A season flag "
              "cannot tell the rule change from anything else that changed in the same season "
              "(more passing, more fourth-down attempts), so read it as 'the league since "
              "2015', not as the value of the extra-point rule."]  # fmt: skip
    return lines


def _importance(summaries: list[dict]) -> list[str]:
    last = summaries[-1]
    top = ", ".join(f"{f} {v:.0%}" for f, v in last["importance"][:6])
    dom = [s["test_season"] for s in summaries if s.get("dominant")]
    names = sorted({s["dominant"] for s in summaries if s.get("dominant")})
    return [f"Gain shares in the {last['test_season']} model: {top}. A feature above 40% is "
            f"flagged by the harness: {len(dom)} of {len(summaries)} folds "
            f"({', '.join(names) or 'none'}). Expected for win probability: the score is "
            "the state's main fact, and it is known before the snap (no leakage)."]  # fmt: skip


def render(t: pl.DataFrame, summaries: list[dict], info: dict) -> str:
    secs = [s["seconds"] for s in summaries]
    span = f"{summaries[0]['test_season']}-{summaries[-1]['test_season']}"
    out = [
        "# Win probability: our walk-forward model vs nflfastR (G1, smoothed in G1b)", "",
        "Generated by `uv run twm decisions wp-backtest` (code: src/twm/modules/decisions/; "
        "walk-through: notebooks/03_wp_model.ipynb). Win probability (WP) = the chance that "
        "the team with the ball wins, from the situation before the snap. Lower Brier and log "
        "loss = better forecasts; the calibration error says how far, on average, '70%' "
        "forecasts are from winning 70% of the time.", "",
        "Honesty note (G1b): the smoothness limits were fixed before any fix was tried, and every "
        "candidate was chosen on the 2004-05 validation seasons only; but two later candidate "
        "rounds (the late-game hand-over and the redefined drive value) were prompted by looking "
        "at regraded seasons, and the pooled test log loss of a first refit (.4484) was seen "
        "before them. The 2006-2025 numbers are therefore slightly optimistic; 2026 is the first "
        "clean test.", "",
        *_setup(summaries, info),
        f"## Pooled {span}", "", *_method_table(t, "pooled", span), "",
        "## By era", "",
    ]  # fmt: skip
    for name, _, _ in ERAS:
        out += [f"### {name}", "", *_method_table(t, "era", name), ""]
    out += [f"## Slices ({span})", ""]
    for name in SLICES:
        out += [f"### {name.capitalize()}", "", *_method_table(t, "slice", name), ""]
    out += [*wp_smooth_report.markdown(t, summaries)]
    out += ["## Per season", "", *_season_table(t, summaries), "",
            f"Runtime per fold (tuning {len(summaries[-1]['trials'])} settings"
            f"{' + era test' if any(x.get('era_ablation') for x in summaries) else ''}"
            f" + smoothness check + refit, one thread): "
            f"{min(secs):.0f}-{max(secs):.0f} s, {sum(secs) / 60:.0f} min in all.", "",
            f"## Reliability (pooled {span}, 10 bins)", "", *_reliability_table(t), "",
            "## Do the era flags matter?", "",
            "Each fold's tuning model refit without the two era flags (same settings and trees) "
            "and scored on its validation season; only folds whose tuning seasons contain both "
            "values of a flag can tell.", "", *_era_test(summaries), "",
            "## Feature importance (smoke test)", "", *_importance(summaries), ""]  # fmt: skip
    return "\n".join(out)


def write_report(
    states: States, *, seasons: Sequence[int] = wp.TEST_SEASONS, out_dir: Path | None = None,
    paths: tuple[Path, Path] | None = None, progress=print,
) -> tuple[Path, Path]:  # fmt: skip
    """Compute and write the markdown report and its CSV (every number in the report)."""
    t, summaries, info = compute(states, seasons=seasons, out_dir=out_dir, progress=progress)
    md, csv = paths if paths is not None else report_paths()
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(render(t, summaries, info), encoding="utf-8")
    t.write_csv(csv, float_precision=6)
    return md, csv
