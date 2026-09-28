"""Waiver Radar evaluation figures (C5): small, deterministic PNGs for the report and notebook.

Every figure is drawn from a :class:`twm.modules.waiver_radar.evaluation.LabelEvaluation`
(itself computed from the predictions store), so a figure can never show a number the report
does not. Each chart's title states its takeaway and is computed from the numbers, never typed
in. Colors follow the method, not its rank, from a colorblind-checked palette; every chart also
carries a legend or direct labels and marker shapes, so no reading depends on color alone.

Deterministic: matplotlib's object API (no global pyplot state), the Agg renderer, fixed sizes
and fonts, and no "Software" or date metadata in the PNG, so the same evaluation gives
byte-identical files. matplotlib is a development dependency (``uv sync`` installs it).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from twm.config import FANTASY_POSITIONS
from twm.modules.waiver_radar.models import K

if TYPE_CHECKING:
    from matplotlib.axes import Axes
    from matplotlib.figure import Figure

    from twm.modules.waiver_radar.evaluation import LabelEvaluation

FIGURE_DIR = Path("reports/waiver_radar/figures")
FIGURES = (
    "precision_by_season.png",
    "precision_pooled.png",
    "hit_rate_by_rank.png",
    "calibration.png",
    "breakouts_caught.png",
)
DPI = 100
# Categorical palette (validated for colorblind separation; fixed per method, never cycled).
COLORS = {
    "logit": "#2a78d6",
    "baseline_last_points": "#eb6834",
    "lgbm": "#1baf7a",
    "baseline_snap_delta": "#eda100",
    "baseline_ecr": "#e87ba4",
}
MARKERS = {
    "logit": "o",
    "lgbm": "s",
    "baseline_ecr": "D",
    "baseline_last_points": "^",
    "baseline_snap_delta": "v",
}
SHORT = {
    "logit": "Radar (logistic regression)",
    "lgbm": "LightGBM",
    "baseline_ecr": "Experts (FantasyPros)",
    "baseline_last_points": "Last week's points",
    "baseline_snap_delta": "Snap-share change",
}
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"
BASE = "#8a8984"
SURFACE = "#ffffff"
RC = {
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "axes.titlesize": 10,
    "axes.labelsize": 9,
    "axes.edgecolor": GRID,
    "axes.labelcolor": INK_2,
    "axes.linewidth": 1.0,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.color": GRID,
    "grid.linewidth": 0.8,
    "xtick.color": INK_2,
    "ytick.color": INK_2,
    "legend.frameon": False,
    "legend.fontsize": 8,
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
    "text.color": INK,
    "svg.hashsalt": "twm",
}


def _new(width: float, height: float, **kw: Any) -> tuple[Figure, Any]:
    from matplotlib.figure import Figure

    fig = Figure(figsize=(width, height), dpi=DPI, layout="constrained")
    axes = fig.subplots(**kw)
    return fig, axes


def _save(fig: Figure, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=DPI, format="png", metadata={"Software": None})
    return path


def _pct_axis(ax: Axes, axis: str = "y") -> None:
    from matplotlib.ticker import PercentFormatter

    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(PercentFormatter(1.0, decimals=0))


TITLE_PT = 11.0
SUBTITLE_PT = 8.5


def _title(fig: Figure, title: str, subtitle: str) -> None:
    """A left-aligned bold takeaway title and a grey subtitle, wrapped to the figure's width;
    the plots are laid out below them."""
    import textwrap

    width, height = fig.get_size_inches()
    t_lines = textwrap.wrap(title, width=int(width * 8.6))
    s_lines = textwrap.wrap(subtitle, width=int(width * 13.0))
    t_h, s_h, pad = TITLE_PT * 1.3 / 72, SUBTITLE_PT * 1.35 / 72, 0.08
    y = 1 - pad / height
    fig.text(0.01, y, "\n".join(t_lines), ha="left", va="top", fontsize=TITLE_PT,
             fontweight="bold", color=INK, linespacing=1.2)  # fmt: skip
    y -= len(t_lines) * t_h / height + 0.03 / height
    fig.text(0.01, y, "\n".join(s_lines), ha="left", va="top", fontsize=SUBTITLE_PT,
             color=INK_2, linespacing=1.25)  # fmt: skip
    y -= len(s_lines) * s_h / height + 0.1 / height
    fig.get_layout_engine().set(rect=(0, 0, 1, y))


def _full(ev: LabelEvaluation) -> str:
    return f"{ev.seasons[0]}-{ev.seasons[-1]}"


def _label_text(label: str) -> str:
    return "a starter week" if label == "y_hit" else "two starter weeks"


# --------------------------------------------------------------------------------------
# 1. Precision@10 by season
# --------------------------------------------------------------------------------------


def precision_by_season(ev: LabelEvaluation, path: Path) -> Path:
    fig, ax = _new(8.0, 4.4)
    seasons = list(ev.seasons)
    base = [
        ev.one(subset="all", model="base_rate", scope="season", key=s)["value"] for s in seasons
    ]
    ax.plot(seasons, base, color=BASE, lw=1.5, ls=(0, (4, 3)), label="Random pick (base rate)")
    for m in ev.methods:
        vals = [
            (r["key"], r["value"])
            for r in ev.get(subset="all", model=m, scope="season", metric=f"p_at_{K}")
            if r["value"] is not None
        ]
        vals = sorted((int(s), v) for s, v in vals)
        if not vals:
            continue
        xs, ys = zip(*vals, strict=True)
        ax.plot(xs, ys, color=COLORS[m], lw=2, marker=MARKERS[m], ms=5, label=SHORT[m],
                zorder=3 if m == "logit" else 2)  # fmt: skip
    ax.set_xticks(seasons)
    ax.set_xticklabels([str(s) for s in seasons], rotation=0)
    ax.set_ylim(0, None)
    _pct_axis(ax)
    ax.set_ylabel(f"Top-{K} picks who got {_label_text(ev.label)}")
    ax.legend(loc="upper left", bbox_to_anchor=(1.0, 1.0))
    ax.grid(axis="x", visible=False)
    winner = ev.winner or "logit"
    won = ev.get(subset="all", model=winner, scope="diff", seasons=_full(ev),
                 key="baseline_last_points", metric="seasons_won")  # fmt: skip
    if won:
        w = won[0]
        title = (
            f"The Radar's top {K} beat last week's points in {int(w['value'])} of "
            f"{w['n_groups']} seasons"
        )
    else:
        title = f"Precision@{K} by season"
    _title(
        fig,
        title,
        f"Share of each weekly top-{K} list who got {_label_text(ev.label)} in the next 3 games, "
        "averaged per season (experts: 2020 on, weeks with a page).",
    )
    return _save(fig, path)


# --------------------------------------------------------------------------------------
# 2. Pooled precision@10 with intervals, per position
# --------------------------------------------------------------------------------------


def precision_pooled(ev: LabelEvaluation, path: Path) -> Path:
    full = _full(ev)
    methods = list(reversed(ev.full_methods))  # the winner on top
    panels = [("All positions", "pooled", ""), *((p, "position", p) for p in FANTASY_POSITIONS)]
    fig, axes = _new(11.0, 3.6, nrows=1, ncols=len(panels), sharey=True)
    for ax, (title, scope, key) in zip(axes, panels, strict=True):
        ys = range(len(methods))
        for y, m in zip(ys, methods, strict=True):
            r = ev.one(subset="all", model=m, scope=scope, seasons=full, key=key,
                       metric=f"p_at_{K}")  # fmt: skip
            v, lo, hi = r["value"], r["lo"], r["hi"]
            ax.barh(y, v, height=0.6, color=COLORS[m], zorder=2)
            ax.errorbar(v, y, xerr=[[v - lo], [hi - v]], fmt="none", ecolor=INK, elinewidth=1,
                        capsize=3, zorder=3)  # fmt: skip
            ax.text(hi + 0.015, y, f"{100 * v:.0f}%", va="center", fontsize=8, color=INK)
        base = ev.one(subset="all", model=methods[0], scope=scope, seasons=full, key=key,
                      metric=f"p_at_{K}")  # fmt: skip
        rate = base["n_pos"] / base["n_rows"]
        ax.axvline(rate, color=BASE, lw=1.2, ls=(0, (4, 3)), zorder=1)
        ax.set_title(f"{title}\n{base['n_groups']:,} lists", fontsize=9, color=INK)
        ax.set_xlim(0, 0.75)
        _pct_axis(ax, "x")
        ax.grid(axis="y", visible=False)
    axes[0].set_yticks(range(len(methods)))
    axes[0].set_yticklabels([SHORT[m] for m in methods])
    w = ev.winner or methods[-1]
    r = ev.one(subset="all", model=w, scope="pooled", seasons=full, metric=f"p_at_{K}")
    _title(
        fig,
        f"{SHORT[w]}: {100 * r['value']:.1f}% of its top-{K} picks got "
        f"{_label_text(ev.label)} (95% interval {100 * r['lo']:.1f}-{100 * r['hi']:.1f}%)",
        f"Pooled precision@{K}, {full}, all pool rows. Black lines: 95% season-block bootstrap "
        "intervals. Dashed: a random pick (the base rate).",
    )
    return _save(fig, path)


# --------------------------------------------------------------------------------------
# 3. Hit rate by rank bucket
# --------------------------------------------------------------------------------------


def hit_rate_by_rank(ev: LabelEvaluation, path: Path) -> Path:
    from twm.backtest.metrics import DEFAULT_BUCKETS

    full = _full(ev)
    buckets = [f"{lo}-{hi}" for lo, hi in DEFAULT_BUCKETS]
    methods = list(ev.full_methods)
    fig, ax = _new(8.0, 4.2)
    width = 0.8 / len(methods)
    for i, m in enumerate(methods):
        xs = [b + (i - (len(methods) - 1) / 2) * width for b in range(len(buckets))]
        vals = [ev.one(subset="all", model=m, scope="bucket", key=b)["value"] for b in buckets]
        ax.bar(xs, vals, width=width * 0.92, color=COLORS[m], label=SHORT[m], zorder=2)
        for x, v in zip(xs, vals, strict=True):
            ax.text(x, v + 0.008, f"{100 * v:.0f}", ha="center", fontsize=7, color=INK)
    base = ev.one(subset="all", model="base_rate", scope="pooled", seasons=full)["value"]
    ax.axhline(base, color=BASE, lw=1.2, ls=(0, (4, 3)), zorder=1, label="Random pick")
    ax.set_xticks(range(len(buckets)))
    ax.set_xticklabels([f"ranked {b}" for b in buckets])
    _pct_axis(ax)
    ax.set_ylim(0, None)
    ax.set_ylabel(f"Players who got {_label_text(ev.label)}")
    ax.grid(axis="x", visible=False)
    ax.legend(loc="upper right", ncols=1)
    w = ev.winner or methods[0]
    top_w = ev.one(subset="all", model=w, scope="bucket", key=buckets[0])
    parts = [f"{SHORT[w].split(' (')[0]} {100 * top_w['value']:.0f}%"]
    if "baseline_last_points" in methods:
        top_b = ev.one(subset="all", model="baseline_last_points", scope="bucket", key=buckets[0])
        parts.append(f"last week's points {100 * top_b['value']:.0f}%")
    _title(
        fig,
        f"The higher a player is ranked, the more often he hits: ranks 1-5 {', '.join(parts)}",
        f"Share of the players ranked 1-5, 6-10 and 11-25 in their weekly list, {full}, "
        f"all pool rows ({top_w['n_rows']:,} players per method in ranks 1-5).",
    )
    return _save(fig, path)


# --------------------------------------------------------------------------------------
# 4. Calibration (reliability) of the winner, one panel per label
# --------------------------------------------------------------------------------------

MIN_ROWS_FOR_TITLE = 1000  # the title's claim covers bins with at least this many rows
MIN_ROWS_TO_DRAW = 100  # smaller bins are too noisy to draw (their counts are in the report)


def calibration(evals: Sequence[LabelEvaluation], path: Path) -> Path:
    from matplotlib.lines import Line2D

    fig, axes = _new(4.3 * len(evals), 4.8, nrows=1, ncols=len(evals), squeeze=False)
    gaps, hidden = [], []
    for ax, ev in zip(axes[0], evals, strict=True):
        m = ev.winner or "logit"
        bins = ev.calibration.get((m, "fixed"))
        if bins is None:
            continue
        all_rows = [b for b in bins.iter_rows(named=True) if b["n"]]
        rows = [b for b in all_rows if b["n"] >= MIN_ROWS_TO_DRAW]
        hidden.append(sum(b["n"] for b in all_rows if b["n"] < MIN_ROWS_TO_DRAW))
        top = max([0.3, *(max(b["mean_pred"], b["observed"]) for b in rows)])
        lim = min(1.0, (int(top * 10) + 1) / 10)
        ax.plot([0, lim], [0, lim], color=BASE, lw=1.2, ls=(0, (4, 3)), zorder=1)
        xs = [b["mean_pred"] for b in rows]
        ys = [b["observed"] for b in rows]
        biggest = max(b["n"] for b in rows)
        sizes = [16 + 70 * (b["n"] / biggest) ** 0.5 for b in rows]
        color = COLORS.get(m, INK)
        ax.plot(xs, ys, color=color, lw=1.2, alpha=0.6, zorder=2)
        ax.scatter(xs, ys, s=sizes, color=color, edgecolors=SURFACE, linewidths=1.5, zorder=3)
        for b in rows:
            above = b["observed"] > b["mean_pred"]
            ax.annotate(
                f"{b['n']:,}", (b["mean_pred"], b["observed"]),
                xytext=(-6, 6) if above else (6, -9), ha="right" if above else "left",
                textcoords="offset points", fontsize=6.5, color=INK_2,
            )  # fmt: skip
            if b["n"] >= MIN_ROWS_FOR_TITLE:
                gaps.append(abs(b["mean_pred"] - b["observed"]))
        ax.set_xlim(0, lim)
        ax.set_ylim(0, lim)
        ax.set_aspect("equal")
        _pct_axis(ax)
        _pct_axis(ax, "x")
        ax.set_xlabel("Predicted chance (mean of a 10-point bin)")
        ax.set_ylabel("Share who really hit")
        n = sum(b["n"] for b in all_rows)
        what = _label_text(ev.label)
        ax.set_title(f"{ev.label} ({what}): {n:,} predictions", fontsize=9)
        ax.legend(
            handles=[
                Line2D([], [], color=color, marker="o", lw=1.2, label=SHORT[m]),
                Line2D([], [], color=BASE, ls=(0, (4, 3)), label="perfect calibration"),
            ],
            loc="upper left",
        )
    gap = 100 * max(gaps) if gaps else None
    title = (
        f"Predicted chances match what happened: within {gap:.1f} points in every bin with "
        f"{MIN_ROWS_FOR_TITLE:,}+ predictions"
        if gap is not None
        else "Calibration"
    )
    first = evals[0]
    _title(
        fig,
        title,
        f"{first.seasons[0]}-{first.seasons[-1]}. Each dot is a 10-point bin of predictions "
        "(0-10%, 10-20% ...), sized and labelled by its number of players. Bins with fewer than "
        f"{MIN_ROWS_TO_DRAW} predictions are too noisy to draw ("
        + ", ".join(f"{e.label}: {h:,} predictions" for e, h in zip(evals, hidden, strict=True))
        + "; see the report).",
    )
    return _save(fig, path)


# --------------------------------------------------------------------------------------
# 5. Breakouts caught
# --------------------------------------------------------------------------------------


def breakouts_caught(ev: LabelEvaluation, path: Path) -> Path:
    full = _full(ev)
    methods = list(reversed(ev.full_methods))
    fig, ax = _new(8.0, 4.0)
    h = 0.36
    for y, m in enumerate(methods):
        r = ev.one(subset="all", model=m, scope="breakouts", metric="caught_share")
        rr = ev.one(subset="all", model=m, scope="breakouts", metric="caught_recent_share")
        f = ev.one(subset="all", model=m, scope="breakouts", metric="players_flagged")
        ax.barh(y + h / 2, r["value"], height=h * 0.9, color=COLORS[m], zorder=2)
        ax.barh(y - h / 2, rr["value"], height=h * 0.9, color=COLORS[m], alpha=0.45, zorder=2,
                hatch="////", edgecolor=SURFACE, linewidth=0)  # fmt: skip
        ax.errorbar(r["value"], y + h / 2, xerr=[[r["value"] - r["lo"]], [r["hi"] - r["value"]]],
                    fmt="none", ecolor=INK, elinewidth=1, capsize=2.5, zorder=3)  # fmt: skip
        solid = (
            f"{r['n_pos']:,} of {r['n_rows']:,} ({100 * r['value']:.0f}%), "
            f"{f['n_rows']:,} players flagged"
        )
        ax.text(r["hi"] + 0.012, y + h / 2, solid, va="center", fontsize=7.5, color=INK)
        recent = f"{100 * rr['value']:.0f}% in the 3 Tuesdays before"
        ax.text(rr["value"] + 0.012, y - h / 2, recent, va="center", fontsize=7.5, color=INK_2)
    ax.set_yticks(range(len(methods)))
    ax.set_yticklabels([SHORT[m] for m in methods])
    ax.set_xlim(0, 1.05)
    _pct_axis(ax, "x")
    ax.set_xlabel("Breakouts the method had in its top 10 before they happened")
    ax.grid(axis="y", visible=False)
    w = ev.winner or methods[-1]
    r = ev.one(subset="all", model=w, scope="breakouts", metric="caught_share")
    who = SHORT[w].split(" (")[0]
    title = f"{who} flagged {100 * r['value']:.0f}% of {r['n_rows']:,} breakouts in advance"
    if "baseline_last_points" in ev.methods:
        b = ev.one(subset="all", model="baseline_last_points", scope="breakouts",
                   metric="caught_share")  # fmt: skip
        fw = ev.one(subset="all", model=w, scope="breakouts", metric="players_flagged")
        fb = ev.one(subset="all", model="baseline_last_points", scope="breakouts",
                    metric="players_flagged")  # fmt: skip
        title += (
            f", last week's points {100 * b['value']:.0f}% by flagging "
            f"{fb['n_rows'] / fw['n_rows']:.1f} times as many players"
        )
    _title(
        fig,
        title,
        f"{full}: breakout = two starter weeks in one 3-game window; caught = ranked top {K} at "
        "or before that Tuesday. Solid: at any earlier Tuesday of the season (95% interval); "
        "hatched: in the 3 Tuesdays before.",
    )
    return _save(fig, path)


# --------------------------------------------------------------------------------------
# All of them
# --------------------------------------------------------------------------------------


def write_figures(evals: Sequence[LabelEvaluation], out_dir: Path) -> list[Path]:
    """The five figures of the evaluation: 1, 2, 3 and 5 for the primary label (``y_hit`` if
    evaluated, else the first one), 4 (calibration) with one panel per label."""
    import matplotlib

    if not evals:
        return []
    primary = next((e for e in evals if e.label == "y_hit"), evals[0])
    with matplotlib.rc_context(RC):
        return [
            precision_by_season(primary, out_dir / FIGURES[0]),
            precision_pooled(primary, out_dir / FIGURES[1]),
            hit_rate_by_rank(primary, out_dir / FIGURES[2]),
            calibration(evals, out_dir / FIGURES[3]),
            breakouts_caught(primary, out_dir / FIGURES[4]),
        ]


__all__ = ["FIGURES", "FIGURE_DIR", "write_figures"]
