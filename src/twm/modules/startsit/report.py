"""reports/startsit/backtest.md: the frozen walk-forward backtest as a page of aggregate tables
(no player, no player's rank; regenerated from the frozen files by `twm model check startsit`,
which refuses when the committed page differs)."""

from __future__ import annotations

import polars as pl


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{100 * x:.1f}%"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return out + ["| " + " | ".join(r) + " |" for r in rows]


def report_text(frames: dict[str, pl.DataFrame], spec: dict) -> str:
    m, cal, gap = frames["metrics"], frames["calibration"], frames["rank_gap"]
    tm, folds = frames["teammates"], frames["folds"]
    lines = [
        "# Start/sit odds: walk-forward backtest",
        "",
        "LOCAL ONLY feature (it reads FantasyPros weekly ranks, which may not be republished); "
        "this page holds only aggregate numbers. Method and limits: docs/start_sit.md.",
        "",
        f"Pin `{spec['model_version']}`: distributions fit on {spec['train_seasons']} "
        f"({spec['rank_rows']:,} ranked player-weeks, {_pct(spec['dnp_share'])} without a stat "
        f"line), smoothing c = {spec['c']:g}. Test seasons {spec['test_seasons']}, each fit on "
        "the seasons before it.",
        "",
        "## Brier score and log loss (lower is better)",
        "",
    ]
    rows = [[r["season"], r["kind"], f"{r['pairs']:,}", f"{r['brier_model']:.4f}",
             f"{r['brier_rank_rate']:.4f}", f"{r['brier_coin']:.4f}", f"{r['logloss_model']:.4f}",
             f"{r['logloss_rank_rate']:.4f}", f"{r['logloss_coin']:.4f}"]
            for r in m.iter_rows(named=True)]  # fmt: skip
    lines += _table(["season", "pairs of", "pairs", "Brier model", "Brier rank rate",
                     "Brier coin", "log loss model", "log loss rank rate", "log loss coin"],
                    rows)  # fmt: skip
    lines += ["", "## Calibration (the favourite's predicted chance vs how often it won)", ""]
    rows = [[r["bucket"], f"{r['pairs']:,}", _pct(r["predicted"]), _pct(r["actual"])]
            for r in cal.iter_rows(named=True)]  # fmt: skip
    lines += _table(["predicted", "pairs", "mean predicted", "favourite won"], rows)
    lines += ["", "## How often the higher-ranked player outscored the other, "
              f"{spec['train_seasons']}", ""]  # fmt: skip
    rows = [[r["rank_gap"], f"{r['pairs']:,}", _pct(r["higher_won"]), _pct(r["QB"]),
             _pct(r["RB"]), _pct(r["WR"]), _pct(r["TE"])]
            for r in gap.iter_rows(named=True)]  # fmt: skip
    lines += _table(["ranks apart", "pairs", "all", "QB", "RB", "WR", "TE"], rows)
    lines += ["", "## Teammates (the independence assumption)", ""]
    rows = [["yes" if r["teammates"] else "no", f"{r['pairs']:,}",
             _pct(r["favourite_predicted"]), _pct(r["favourite_won"]),
             f"{r['brier_model']:.4f}"] for r in tm.iter_rows(named=True)]  # fmt: skip
    lines += _table(["teammates", "pairs", "favourite predicted", "favourite won",
                     "Brier model"], rows)  # fmt: skip
    lines += ["", "## Folds (smoothing chosen on each fold's last training season)", ""]
    rows = [[str(r["season"]), r["train"], f"{r['c']:g}", _pct(r["rate_same"]),
             _pct(r["rate_flex"])] for r in folds.iter_rows(named=True)]  # fmt: skip
    lines += _table(["test season", "trained on", "c", "higher rank won (same position)",
                     "higher mean won (FLEX)"], rows)  # fmt: skip
    return "\n".join(lines) + "\n"
