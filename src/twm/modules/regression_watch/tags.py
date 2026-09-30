"""Regression Watch step D3: the Sell-high, Buy-low and Legit tags (PROJECT_SPEC 8.2).

Within one as-of and position of the universe (:func:`projection.player_state`):

- **Sell-high**: FPOE/game in the top decile AND the projection at least X points/game BELOW
  his current PPG (he has been lucky, and it should fade);
- **Buy-low**: FPOE/game in the bottom decile AND the projection at least X points/game ABOVE
  his current PPG (the mirror);
- **Legit**: his PPG ranks inside the position's starter threshold (teams x dedicated starters,
  config/league.yaml: QB 12, RB 24, WR 24, TE 12) and his FPOE/game is NOT in the top decile:
  the production is carried by opportunity (xFP), not by luck.

The deciles use FPOE/game with garbage time (the number the page shows), ranked within the
position's universe at that as-of: the top decile is the ceil(n / 10) highest (ties by
gsis_id). X is chosen by :func:`choose_x` on validation seasons only (never the test season):
the X of :data:`X_GRID` with the best precision among those that tag at least
:data:`MIN_TAGS_PER_ASOF` players per as-of on average (ties: the smaller X, more tags).

Hits (graded in the backtest): Sell-high = rest-of-season PPG below the current PPG; Buy-low =
above; Legit = his rest-of-season PPG still ranks inside the starter threshold.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import polars as pl

from twm.config import League

DECILE = 0.10
X_GRID: tuple[float, ...] = tuple(x / 2 for x in range(13))  # 0.0 .. 6.0 points/game
MIN_TAGS_PER_ASOF = 3  # a tag list shorter than this a week is of little use (fixed a priori)
TAGS = ("sell_high", "buy_low", "legit")
TAG_TITLES = {"sell_high": "Sell-high", "buy_low": "Buy-low", "legit": "Legit"}
ASOF_KEYS = ("season", "week")


def _starter(league: League) -> pl.Expr:
    return pl.col("position").replace_strict(
        league.starter_thresholds(), default=0, return_dtype=pl.Int64
    )


def decile_flags(universe: pl.DataFrame, by: Sequence[str] = ASOF_KEYS) -> pl.DataFrame:
    """``fpoe_top`` / ``fpoe_bottom``: FPOE/game in the top / bottom decile of the universe,
    within each group of ``by`` + position (one as-of and position)."""
    keys = [*by, "position"]
    n = pl.len().over(keys)
    k = (n.cast(pl.Float64) * DECILE).ceil().cast(pl.Int64)
    top = universe.sort([*keys, "fpoe_pg", "gsis_id"], descending=[*[False] * len(keys), True,
                                                                   False])  # fmt: skip
    top = top.with_columns(pl.int_range(1, pl.len() + 1).over(keys).alias("_top"))
    both = top.sort([*keys, "fpoe_pg", "gsis_id"]).with_columns(
        pl.int_range(1, pl.len() + 1).over(keys).alias("_bottom")
    )
    return both.with_columns(
        (pl.col("_top") <= k).alias("fpoe_top"), (pl.col("_bottom") <= k).alias("fpoe_bottom")
    ).drop("_top", "_bottom")


def _gap(tag: str) -> pl.Expr:
    """How far the projection is below (Sell-high) or above (Buy-low) the current PPG, rounded
    like the inputs so a gap of exactly X counts."""
    d = (
        pl.col("ppg") - pl.col("ppg_ros")
        if tag == "sell_high"
        else pl.col("ppg_ros") - pl.col("ppg")
    )
    return d.round(6)


def tag_exprs(league: League, x_sell: float | pl.Expr, x_buy: float | pl.Expr) -> list[pl.Expr]:
    """The three tags on rows with ``ppg``, ``ppg_ros``, ``ppg_rank`` and the decile flags."""
    xs = x_sell if isinstance(x_sell, pl.Expr) else pl.lit(float(x_sell))
    xb = x_buy if isinstance(x_buy, pl.Expr) else pl.lit(float(x_buy))
    return [
        (pl.col("fpoe_top") & (_gap("sell_high") >= xs)).alias("sell_high"),
        (pl.col("fpoe_bottom") & (_gap("buy_low") >= xb)).alias("buy_low"),
        ((pl.col("ppg_rank") <= _starter(league)) & ~pl.col("fpoe_top")).alias("legit"),
    ]


def hit_exprs(league: League) -> list[pl.Expr]:
    """Did the tag come true? On rows with ``ros_ppg`` / ``ros_rank`` (rest of the season)."""
    return [
        (pl.col("ros_ppg") < pl.col("ppg")).alias("hit_sell_high"),
        (pl.col("ros_ppg") > pl.col("ppg")).alias("hit_buy_low"),
        (pl.col("ros_rank") <= _starter(league)).alias("hit_legit"),
    ]


@dataclass(frozen=True)
class Threshold:
    """The X chosen for one tag, with its validation precision and count."""

    tag: str
    x: float
    precision: float | None
    n_tags: int
    n_asofs: int
    seasons: tuple[int, ...]
    fallback: bool = False  # no X of the grid reached the minimum count: the smallest X


def choose_x(
    rows: pl.DataFrame,
    tag: str,
    *,
    grid: Sequence[float] = X_GRID,
    min_per_asof: float = MIN_TAGS_PER_ASOF,
) -> Threshold:
    """The X for ``tag`` ("sell_high" | "buy_low") from graded validation ``rows`` (universe
    players with a rest of season: ``ppg``, ``ppg_ros``, decile flags, ``ros_ppg``): the best
    precision among the X that tag at least ``min_per_asof`` players per as-of on average;
    ties go to the smaller X. The caller passes validation seasons only."""
    if tag not in ("sell_high", "buy_low"):
        raise ValueError(f"only Sell-high and Buy-low have an X, not {tag}")
    seasons = tuple(sorted(rows.get_column("season").unique().to_list())) if rows.height else ()
    n_asofs = rows.select(*ASOF_KEYS).n_unique() if rows.height else 0
    if tag == "sell_high":
        base, gap, hit = pl.col("fpoe_top"), _gap(tag), "_hit"
        hit_expr = pl.col("ros_ppg") < pl.col("ppg")
    else:
        base, gap, hit = pl.col("fpoe_bottom"), _gap(tag), "_hit"
        hit_expr = pl.col("ros_ppg") > pl.col("ppg")
    cand = rows.filter(base).select(gap.alias("_gap"), hit_expr.alias(hit))
    best: Threshold | None = None
    for x in grid:
        sel = cand.filter(pl.col("_gap") >= x)
        n = sel.height
        if n == 0 or n < min_per_asof * n_asofs:
            continue
        prec = float(sel.get_column(hit).mean())  # type: ignore[arg-type]
        if best is None or prec > best.precision:  # type: ignore[operator]
            best = Threshold(tag, float(x), prec, n, n_asofs, seasons)
    if best is None:
        x0 = float(min(grid))
        sel = cand.filter(pl.col("_gap") >= x0)
        prec = float(sel.get_column(hit).mean()) if sel.height else None  # type: ignore[arg-type]
        return Threshold(tag, x0, prec, sel.height, n_asofs, seasons, fallback=True)
    return best
