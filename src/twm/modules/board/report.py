"""``twm board backtest``: every variant's walk-forward, the ECR baseline, the metric tables and
the reports ``reports/board/{cliff,breakout}.md`` with their csv files (step I1b)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from twm.modules.board import backtest as bt
from twm.modules.board import ecr as be
from twm.modules.board import evaluation as ev

POPULATIONS = {"cliff": ("cliff_main", "cliff_missed", "cliff_sensitivity"),
               "breakout": ("breakout_wr_te", "breakout_rb")}  # fmt: skip
PRIMARY = {"cliff_missed": "logit_simple"}  # every other variant: "logit"
TOP_FEATURES = 8
Progress = Callable[[str], None]


@dataclass
class VariantReport:
    run: bt.VariantRun
    metrics: pl.DataFrame
    seasons: pl.DataFrame
    calibration: pl.DataFrame
    importance: pl.DataFrame
    agree_summary: pl.DataFrame
    agree_examples: pl.DataFrame
    dominant: list[str] = field(default_factory=list)
    signs: dict[str, float] = field(default_factory=dict)  # the primary's last-fold coefficients

    CACHED = ("metrics", "seasons", "calibration", "importance", "agree_summary",
              "agree_examples", "dominant", "signs")  # fmt: skip

    def to_cache(self) -> dict:
        """Plain data only (the fitted models and the variant's estimator factories stay out)."""
        return {"variant": self.run.variant.name, "scored": self.run.scored,
                **{k: getattr(self, k) for k in self.CACHED}}  # fmt: skip

    @classmethod
    def from_cache(cls, d: dict) -> VariantReport:
        run = bt.VariantRun(bt.variants()[d["variant"]], d["scored"])
        return cls(run=run, **{k: d[k] for k in cls.CACHED})

    @property
    def primary(self) -> str:
        return PRIMARY.get(self.run.variant.name, "logit")


def add_ecr(scored: pl.DataFrame, kind: str, ranks: dict[int, pl.DataFrame]) -> pl.DataFrame:
    """``p_ecr`` (the baseline's score) on the rows of snapshots with a preseason ECR."""
    parts = []
    for (s,), part in scored.group_by(["season"], maintain_order=True):
        r = ranks.get(int(s))
        if r is None:
            parts.append(part.with_columns(pl.lit(None, dtype=pl.Float64).alias("p_ecr")))
            continue
        a = be.attach_ecr(part.with_columns(pl.col("pos_rank").alias("pos_rank_s")), r)
        parts.append(a.with_columns(be.ecr_score(a, kind).alias("p_ecr")).drop(
            "pos_rank_s", "ecr_rank", "ecr_fill"))  # fmt: skip
    return pl.concat(parts, how="vertical").sort("season", "gsis_id")


def importance(run: bt.VariantRun) -> tuple[pl.DataFrame, list[str]]:
    """Mean importance share per feature over the folds (logit: the standard deviation of the
    feature's log-odds contribution; LightGBM: gain), and the folds whose top feature held over
    40% of the importance (spec 6.2 rule 6)."""
    rows, dominant = [], []
    for name, res in run.results.items():
        n = len(res.folds)
        acc: dict[str, float] = {}
        for fr in res.folds:
            for f, share in fr.importance:
                acc[f] = acc.get(f, 0.0) + share / n
            if fr.dominant and len(run.variant.specs[name].features) > 1:
                dominant.append(f"{name} {fr.fold.test_season}: {fr.dominant[0]} "
                                f"{fr.dominant[1]:.0%}")  # fmt: skip
        rows += [{"model": name, "feature": f, "mean_share": round(v, 6)} for f, v in acc.items()]
    df = pl.DataFrame(rows, schema={"model": pl.String, "feature": pl.String,
                                    "mean_share": pl.Float64})  # fmt: skip
    return df.sort(["model", "mean_share", "feature"], descending=[False, True, False]), dominant


def directions(run: bt.VariantRun, model: str) -> dict[str, float]:
    """The last fold's standardized coefficient per input (no missing indicators): its sign says
    whether a higher value raises (+) or lowers (-) the predicted chance."""
    from twm.modules.hot_seat.models import coefficients

    res = run.results.get(model)
    if res is None or not res.folds:
        return {}
    coefs = coefficients(res.folds[-1].model)
    return {n: c for n, c in coefs if n != "intercept" and not n.startswith("missingindicator")}


def evaluate(run: bt.VariantRun, ranks: dict[int, pl.DataFrame]) -> VariantReport:
    v = run.variant
    df = add_ecr(run.scored, v.ecr_kind, ranks)
    run.scored = df
    models = v.models
    base = [m for m in models if v.specs[m].kind == "baseline"]
    rows = ev.slice_table(df, models, base, "all")
    era = df.filter(pl.col("season") >= ev.ECR_FIRST_SNAPSHOT, pl.col("p_ecr").is_not_null())
    rows += ev.slice_table(era, [*models, "ecr"], [*base, "ecr"], "ecr_era")
    metrics = pl.DataFrame(rows, infer_schema_length=None).with_columns(
        pl.lit(v.name).alias("variant")
    )
    model_names = [m for m in models if v.specs[m].kind == "model"]
    imp, dominant = importance(run)
    primary = PRIMARY.get(v.name, "logit")
    summary, examples = ev.disagreements(df, primary)
    return VariantReport(
        run=run,
        metrics=metrics.select("variant", pl.exclude("variant")),
        seasons=ev.per_season(df, models).with_columns(pl.lit(v.name).alias("variant")),
        calibration=ev.calibration(df, model_names),
        importance=imp,
        agree_summary=summary,
        agree_examples=examples,
        dominant=dominant,
        signs=directions(run, primary),
    )


def run_all(
    dataset: pl.DataFrame,
    db: Path | str,
    names: tuple[str, ...] | None = None,
    progress: Progress | None = None,
    cache: Path | None = None,
) -> dict[str, VariantReport]:
    """Walk-forward and evaluation of every variant (``names``: a subset). With ``cache``, a
    variant already evaluated on the SAME dataset (its file name carries the dataset's hash) is
    loaded instead of refit, and every new one is saved there (``twm board backtest --resume``:
    long runs can be split into short ones)."""
    import hashlib
    import pickle

    say = progress or (lambda _m: None)
    key = hashlib.sha256(dataset.write_ipc(None).getvalue()).hexdigest()[:16]
    seasons = [s for s in bt.TEST_SEASONS if s >= ev.ECR_FIRST_SNAPSHOT]
    ranks: dict[int, pl.DataFrame] | None = None
    out = {}
    for name, v in bt.variants().items():
        if names and name not in names:
            continue
        path = cache / f"{name}-{key}.pkl" if cache is not None else None
        if path is not None and path.exists():
            out[name] = VariantReport.from_cache(pickle.loads(path.read_bytes()))  # our own file
            say(f"{name}: reused {path.name}")
            continue
        if ranks is None:
            ranks = {s: be.ecr_ranks(db, s + 1) for s in seasons}
        out[name] = evaluate(bt.run_variant(dataset, v, progress=say), ranks)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(pickle.dumps(out[name].to_cache()))
        say(f"{name}: evaluated")
    return out


# --------------------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------------------


def _f(x: float | None, d: int = 3) -> str:
    return "-" if x is None else f"{x:.{d}f}"


def _ci(m: pl.DataFrame, slice_: str, model: str, metric: str, vs: str | None = None) -> str:
    q = m.filter(pl.col("slice") == slice_, pl.col("model") == model, pl.col("metric") == metric)
    q = q.filter(pl.col("vs").is_null() if vs is None else pl.col("vs") == vs)
    if q.height == 0:
        return "-"
    r = q.row(0, named=True)
    sign = "+" if vs is not None and r["value"] is not None and r["value"] > 0 else ""
    return f"{sign}{_f(r['value'])} [{_f(r['lo'])}, {_f(r['hi'])}]"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return out + ["| " + " | ".join(r) + " |" for r in rows]


def results_section(rep: VariantReport) -> list[str]:
    v, m = rep.run.variant, rep.metrics
    lines = [f"### {v.title} (`{v.name}`, label `{v.label}`)", ""]
    slices = (("all", "snapshots 2007-2024 (labels 2008-2025)"),
              ("ecr_era", "snapshots 2019-2024 (labels 2020-2025), with the ECR"))  # fmt: skip
    for sl, what in slices:
        models = [*v.models, *(["ecr"] if sl == "ecr_era" else [])]
        rows = [[f"`{mo}`", _ci(m, sl, mo, "pr_auc"), _ci(m, sl, mo, "p_at_10"),
                 _ci(m, sl, mo, "p_at_20"), _ci(m, sl, mo, "brier")] for mo in models]  # fmt: skip
        lines += [f"**{what}**", ""]
        lines += _table(["model", "PR-AUC [95% CI]", "precision@10", "precision@20", "Brier"],
                        rows)  # fmt: skip
        diffs = m.filter(pl.col("slice") == sl, pl.col("metric") == "pr_auc_diff",
                         pl.col("model").is_in(v.models))  # fmt: skip
        drows = [[f"`{r['model']}` - `{r['vs']}`", _ci(m, sl, r["model"], "pr_auc_diff", r["vs"]),
                  _ci(m, sl, r["model"], "p_at_10_diff", r["vs"]),
                  _ci(m, sl, r["model"], "p_at_20_diff", r["vs"]),
                  _f(r["share_above_zero"], 2)] for r in diffs.iter_rows(named=True)]  # fmt: skip
        lines += ["", "Paired differences (same seasons resampled for both):", ""]
        lines += _table(["difference", "PR-AUC", "precision@10", "precision@20",
                         "share of resamples > 0 (PR-AUC)"], drows)  # fmt: skip
        lines.append("")
    return lines


def features_section(rep: VariantReport) -> list[str]:
    signs = rep.signs
    lines = ["Top features (mean share of the importance over the 18 folds; logit = spread of "
             "the feature's log-odds contribution, sign from the 2024 fold's standardized "
             "coefficient; LightGBM = gain):", ""]  # fmt: skip
    for model in [m for m in rep.run.variant.models if rep.run.variant.specs[m].kind == "model"]:
        top = rep.importance.filter(pl.col("model") == model).head(TOP_FEATURES)
        items = []
        for f, s in top.select("feature", "mean_share").iter_rows():
            sign = signs.get(f) if model == rep.primary else None
            mark = "" if sign is None else (" (+)" if sign > 0 else " (-)")
            items.append(f"`{f}`{mark} {s:.0%}")
        lines.append(f"- `{model}`: " + ", ".join(items))
    if rep.dominant:
        lines += ["", "Importance smoke test (spec 6.2 rule 6), folds whose top feature holds "
                  "over 40%: " + "; ".join(rep.dominant)]  # fmt: skip
    return [*lines, ""]


def calibration_section(rep: VariantReport) -> list[str]:
    c = rep.calibration.filter(pl.col("model") == rep.primary)
    rows = [[str(r["bin"]), str(r["n"]), _f(r["mean_pred"]), _f(r["observed"]),
             f"{_f(r['min_pred'])}-{_f(r['max_pred'])}"]
            for r in c.iter_rows(named=True)]  # fmt: skip
    return [f"Calibration of `{rep.primary}` (its own probability; 10 equal-count bins, every "
            "test season):", "", *_table(["bin", "rows", "mean predicted", "observed",
                                          "range"], rows), ""]  # fmt: skip


def disagreement_section(rep: VariantReport, names: pl.DataFrame) -> list[str]:
    s, ex = rep.agree_summary, rep.agree_examples
    words = {"model_only": f"in `{rep.primary}`'s top 10, not the ECR's",
             "ecr_only": f"in the ECR's top 10, not `{rep.primary}`'s",
             "both": "in both"}  # fmt: skip
    rows = [[words[r["group"]], str(r["rows"]), str(r["hits"]), _f(r["hit_rate"], 2)]
            for r in s.iter_rows(named=True)]  # fmt: skip
    lines = ["Where the model and the market disagree most (snapshots 2019-2024, per season "
             "the top 10 of each; who was right = the label):", "",
             *_table(["group", "players", "label = 1", "rate"], rows), ""]  # fmt: skip
    if ex.height:
        ex = ex.join(names, on="gsis_id", how="left")
        erows = [[str(r["season"] + 1), r["display_name"] or r["gsis_id"], r["position"],
                  r["group"], str(r["rank_model"]), str(r["rank_ecr"]),
                  _f(r["ppg"], 1), _f(r["ppg_next"], 1), str(r["games_next"]), str(r["y"])]
                 for r in ex.iter_rows(named=True)]  # fmt: skip
        lines += ["Largest rank gaps (2 per season and side):", "",
                  *_table(["label season", "player", "pos", "group", "rank by model",
                           "rank by ECR score",
                           "PPG S", "PPG S+1", "games S+1", "label"], erows), ""]  # fmt: skip
    return lines


def population_table(reps: dict[str, VariantReport], pop: str) -> list[str]:
    cols = [n for n in POPULATIONS[pop] if n in reps]
    base = None
    for n in cols:
        t = reps[n].seasons.select("season", pl.col("rows").alias(f"{n} rows"),
                                   pl.col("positives").alias(f"{n} positives"))  # fmt: skip
        base = t if base is None else base.join(t, on="season", how="full", coalesce=True)
    if base is None:
        return []
    base = base.sort("season")
    rows = [[str(r[0]), *(str(x) for x in r[1:])] for r in base.iter_rows()]
    header = ["snapshot S", *base.columns[1:]]
    ranges = []
    for n in cols:
        p = base.get_column(f"{n} positives")
        ranges.append(f"`{n}` {int(p.sum())} positives ({int(p.min())}-{int(p.max())} a season)")
    return ["Rows and positives per test season (label season = S + 1): " + "; ".join(ranges)
            + ".", "", *_table(header, rows), ""]  # fmt: skip


INTRO = {
    "cliff": [
        "# Cliff backtest (step I1b)", "",
        "Who among last season's top-36 veterans (3+ prior seasons, 8+ games) loses 30% or more "
        "of his points per game next season? Features at the end-of-season snapshot of S (the "
        "Tuesday after the Super Bowl, point in time), label from S+1. Walk-forward by snapshot "
        "season: each test season's models learn only from earlier snapshots (2002 on). Owner "
        "decision 2026-10-02: players with fewer than 6 games in S+1 get the separate label "
        "`y_missed` with its own simple logit and are left out of the main Cliff model; the "
        "sensitivity run counts them as cliffs. docs/board.md explains every column.", "",
        "Baselines: last season's PPG rank (a one-feature logit) and FantasyPros' PRESEASON "
        "expert consensus ranking (ECR; not ADP, which the warehouse does not have) of S+1, "
        "2020+ only, scored as ECR rank minus last season's rank. The ECR is taken months after "
        "the snapshot (after free agency and the draft), so it knows more than the model.", "",
    ],
    "breakout": [
        "# Breakout backtest (step I1b)", "",
        "Which WR/TE (and, reported apart, RB) entering their 2nd or 3rd season, not already "
        "top-36 WR / top-12 TE / top-24 RB in PPG, finish top-24 WR / top-12 TE / top-24 RB "
        "(8+ games) next season? Features at the end-of-season snapshot of S, label from S+1, "
        "walk-forward by snapshot season (training from 2002). Team vacated targets are not a "
        "feature: no point-in-time offseason roster exists at the snapshot (docs/board.md).", "",
        "Baselines: last season's PPG rank (one-feature logit; an unranked player gets the "
        "training median rank plus a missing flag) and the preseason FantasyPros ECR of S+1 "
        "(2020+ only; ECR, not ADP), scored as minus the ECR rank (unranked last).", "",
    ],
}  # fmt: skip
CAVEATS = [
    "## Read before trusting", "",
    "- Intervals resample whole seasons (18 in `all`, 6 in `ecr_era`): the ECR-era intervals are "
    "wide. Precision@k is the mean over seasons of the share of the season's top k with the "
    "label.",
    "- Probabilities are each model's own (no isotonic step); the PPG-rank baseline and the ECR "
    "are rankings first.",
    "- Inputs missing in early seasons (NGS before 2016, RYOE before 2018, snap counts before "
    "2013, own xFP before 2009) are imputed inside each fit with a missing indicator.",
    "- Head-coach departures: the owner's cited research file (accepted in bulk, 2026-10-01); a "
    "blank date counts as known at the snapshot for fired / mutual / interim types only.",
    "",
]  # fmt: skip


def write_reports(reps: dict[str, VariantReport], out_dir: Path, names: pl.DataFrame) -> list[Path]:
    """reports/board/{cliff,breakout}.md, {pop}.csv (metrics), {pop}_seasons.csv and
    {pop}_features.csv for the populations with a variant in ``reps``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for pop, names_ in POPULATIONS.items():
        got = [reps[n] for n in names_ if n in reps]
        if not got:
            continue
        lines = [*INTRO[pop], *population_table(reps, pop), "## Results", ""]
        for rep in got:
            lines += results_section(rep) + features_section(rep) + calibration_section(rep)
            lines += disagreement_section(rep, names)
        lines += CAVEATS
        md = out_dir / f"{pop}.md"
        md.write_text("\n".join(lines).rstrip() + "\n")
        pl.concat([r.metrics for r in got], how="vertical").write_csv(out_dir / f"{pop}.csv")
        pl.concat([r.seasons for r in got], how="diagonal").write_csv(
            out_dir / f"{pop}_seasons.csv")  # fmt: skip
        imp = [r.importance.with_columns(pl.lit(r.run.variant.name).alias("variant")) for r in got]
        pl.concat(imp, how="vertical").write_csv(out_dir / f"{pop}_features.csv")
        paths += [md, out_dir / f"{pop}.csv"]
    return paths
