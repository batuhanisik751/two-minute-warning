"""Grades before vs after the smoothed WP model (G1b): what changed in the regrade.

"Before" = the grades made with G1's WP folds, frozen under ``data/decisions/graded_g1/`` (a
copy of data/decisions/graded/ taken before the regrade; gitignored like every grade); "after"
= the current grades. Matched decision by decision (season, game_id, play_id). Decisions the
current grades leave out as ``late_game`` (G3b: the last 2:00 of Q4 and overtime, no longer
graded) are left out of "before" too, so the comparison is the WP model's change alone.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from twm.modules.decisions import coach

KEY = ["season", "game_id", "play_id"]
LATE_H1 = (pl.col("half_number") == 1) & (pl.col("half_seconds_remaining") <= 120)


def before_dir() -> Path:
    from twm.modules.decisions import grade as gr

    return gr.graded_dir().parent / "graded_g1"


def _sum(f: pl.DataFrame, col: str) -> float:
    return float(f.get_column(col).sum())


def _mistakes(t: pl.DataFrame) -> pl.DataFrame:
    return t.filter((pl.col("grade") == "clear") & ~pl.col("correct").fill_null(True))


def _pair(before: pl.DataFrame, after: pl.DataFrame) -> pl.DataFrame:
    cols = ["grade", "recommended", "correct", "wp_lost"]
    b = before.select(*KEY, *[pl.col(c).alias(f"{c}_b") for c in cols])
    return after.join(b, on=KEY, how="inner")


def same_exclusions(before: pl.DataFrame, after: pl.DataFrame) -> pl.DataFrame:
    """``before`` with the late-game decisions of ``after`` excluded as they are now (exclusion
    ``late_game``, no grade), so both sides grade the same decisions."""
    late = after.filter(pl.col("exclusion") == "late_game").select(
        *KEY, pl.lit(True).alias("_late"))  # fmt: skip
    b = before.join(late, on=KEY, how="left")
    is_late = pl.col("_late").fill_null(False)
    blank = [c for c in ("grade", "recommended", "correct", "wp_lost") if c in b.columns]
    return b.with_columns(
        pl.when(is_late).then(pl.lit("late_game")).otherwise(pl.col("exclusion"))
        .alias("exclusion"),
        *(pl.when(is_late).then(None).otherwise(pl.col(c)).alias(c) for c in blank),
    ).drop("_late")  # fmt: skip


def changed(p: pl.DataFrame) -> pl.Expr:
    """The grade changed: clear <-> toss-up, or another recommended option."""
    return (pl.col("grade") != pl.col("grade_b")) | (
        pl.col("recommended") != pl.col("recommended_b"))  # fmt: skip


def _rank(seasons: pl.DataFrame, season: int, min_games: int) -> pl.DataFrame:
    b = coach.leaderboard(seasons, season, min_games)
    return b.select("coach", "team", pl.col("rank").cast(pl.Int64), "wp_lost_per_game")


def compare(after_fourth: pl.DataFrame, after_tries: pl.DataFrame, *, season: int,
            min_games: int, before: Path) -> dict[str, Any] | None:  # fmt: skip
    """Every before/after number of the report; None when ``before`` holds no frozen grades
    (:func:`before_dir` in production)."""
    from twm.modules.decisions import grade as gr

    d = before
    if not d.exists() or not list(d.glob("fourth_downs_*.parquet")):
        return None
    bf = same_exclusions(gr.load_graded("fourth_downs", out_dir=d), after_fourth)
    bt = same_exclusions(gr.load_graded("two_point", out_dir=d), after_tries)
    out: dict[str, Any] = {}
    q1_six = (pl.col("qtr") == 1) & (pl.col("score_differential") == 6)
    mb, ma = _mistakes(bt), _mistakes(after_tries)
    out["tries"] = {
        "mistakes_before": mb.height, "mistakes_after": ma.height,
        "q1_six_before": mb.filter(q1_six).height, "q1_six_after": ma.filter(q1_six).height,
        "lost_before": float(mb.get_column("wp_lost").sum()),
        "lost_after": float(ma.get_column("wp_lost").sum()),
    }  # fmt: skip
    pt = _pair(bt, after_tries)
    out["tries"]["changed"] = pt.filter(changed(pt)).height
    out["tries"]["matched"] = pt.height
    gb = bf.filter(pl.col("exclusion").is_null())
    ga = after_fourth.filter(pl.col("exclusion").is_null())
    pf = _pair(gb, ga)
    late = pf.filter(LATE_H1)
    out["late_h1"] = {
        "graded": late.height, "changed": late.filter(changed(late)).height,
        "clear_before": late.filter(pl.col("grade_b") == "clear").height,
        "clear_after": late.filter(pl.col("grade") == "clear").height,
        "go_before": late.filter((pl.col("grade_b") == "clear")
                                 & (pl.col("recommended_b") == "go")).height,
        "go_after": late.filter((pl.col("grade") == "clear")
                                & (pl.col("recommended") == "go")).height,
        "lost_before": _sum(late.filter(pl.col("grade_b") == "clear"), "wp_lost_b"),
        "lost_after": _sum(late.filter(pl.col("grade") == "clear"), "wp_lost"),
    }  # fmt: skip
    out["fourth"] = {"graded": pf.height, "changed": pf.filter(changed(pf)).height,
                     "clear_before": pf.filter(pl.col("grade_b") == "clear").height,
                     "clear_after": pf.filter(pl.col("grade") == "clear").height}  # fmt: skip
    lb, la = coach.league_by_season(bf, bt), coach.league_by_season(after_fourth, after_tries)
    out["league_before"], out["league_after"] = lb, la
    sb, sa = coach.coach_season(bf, bt), coach.coach_season(after_fourth, after_tries)
    rb, ra = _rank(sb, season, min_games), _rank(sa, season, min_games)
    j = ra.join(rb.select("coach", "team", pl.col("rank").alias("rank_before"),
                          pl.col("wp_lost_per_game").alias("per_game_before")),
                on=["coach", "team"], how="inner").sort("rank")  # fmt: skip
    r1, r2 = j.get_column("rank").to_numpy(), j.get_column("rank_before").to_numpy()
    rho = float(np.corrcoef(r1, r2)[0, 1]) if j.height > 2 else None
    out["ranking"] = {"season": season, "table": j, "spearman": rho}
    return out
