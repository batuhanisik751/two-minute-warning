"""The walk-forward backtest (docs/playoff_planner.md, "Backtest").

As of after week H (:data:`HORIZONS`) of a test season, each unit's base is its points per game
to date (weeks <= H, at least :data:`MIN_GAMES` games; QB / RB / WR / TE need a base of at least
:data:`MIN_BASE`). Its points in each of weeks 15-17 (:data:`PLAYOFF_WEEKS`) are predicted as
base x the multiplier of its opponent that week (ratings from weeks <= H only). A unit is scored
in a week it played for the same team (a player who did not play has no points to predict).

The rule (:func:`select`, fixed before any test number was seen) is applied per position,
pooled over the horizons: from 'none', the next candidate replaces the current choice only if
its pooled MAE is lower AND its MAE is lower in at least :data:`SEASON_WINS_NEEDED` of the 13
test seasons.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import polars as pl

from twm.modules.playoff_planner import history as hs
from twm.modules.playoff_planner import ratings as rt

TEST_SEASONS = tuple(range(hs.FIRST_SEASON, 2026))
HORIZONS = (4, 8, 12, 14)
PLAYOFF_WEEKS = (15, 16, 17)
SEASON_WINS_NEEDED = 7
MIN_GAMES = 2
MIN_BASE = 5.0  # PPR points per game, QB / RB / WR / TE only (fantasy-relevant players)
QUINTILES = 5
ALL = "all"


def bases(units: pl.DataFrame, through_week: int) -> pl.DataFrame:
    """Per (season, pid): position, team and player of his latest game through the week,
    games and base (points per game); filtered by MIN_GAMES / MIN_BASE."""
    u = units.filter(pl.col("week") <= through_week).sort("season", "pid", "week")
    b = u.group_by("season", "pid").agg(
        pl.col("position").last(), pl.col("team").last(), pl.col("player").last(),
        pl.len().alias("games"), pl.col("points").mean().alias("base"))  # fmt: skip
    skill = pl.col("position").is_in(list(hs.SKILL))
    return b.filter((pl.col("games") >= MIN_GAMES) & (~skill | (pl.col("base") >= MIN_BASE)))


def scored_rows(units: pl.DataFrame, totals: pl.DataFrame, horizon: int,
                weeks: Sequence[int] = PLAYOFF_WEEKS) -> pl.DataFrame:  # fmt: skip
    """The horizon's scored rows: each based unit's games of ``weeks`` for the same team, with
    base, actual, the opponent's ratings as of the horizon and its quintile (1 hardest)."""
    b = bases(units, horizon)
    act = (units.filter(pl.col("week").is_in(list(weeks)))
           .group_by("season", "week", "pid", "team")
           .agg(pl.col("opponent").first(), pl.col("points").sum().alias("actual")))  # fmt: skip
    r = rt.ratings(totals, horizon).select(
        "season", "position", pl.col("team").alias("opponent"), "raw", "shrunk", "adjusted",
        "lg_ppg", "games",
    ).rename({"games": "opp_games"})  # fmt: skip
    q = r.with_columns(
        (((pl.col("raw").fill_null(1.0).rank("ordinal").over("season", "position") - 1)
          * QUINTILES) // pl.len().over("season", "position") + 1).cast(pl.Int32)
        .alias("quintile"))  # fmt: skip
    out = b.join(act, on=["season", "pid", "team"]).join(
        q, on=["season", "position", "opponent"], how="left")  # fmt: skip
    return out.with_columns(pl.lit(int(horizon)).alias("horizon")).sort(
        "season", "week", "position", "pid")  # fmt: skip


def predictions(rows: pl.DataFrame) -> pl.DataFrame:
    """Long rows: one per (scored row, candidate) with ``pred`` = base x multiplier."""
    parts = [rows.with_columns(pl.lit(c).alias("candidate"),
                               (pl.col("base") * rt.multiplier(c)).alias("pred"))
             for c in rt.CANDIDATES]  # fmt: skip
    return pl.concat(parts).with_columns((pl.col("actual") - pl.col("pred")).abs().alias("err"))


def all_rows(units: pl.DataFrame, totals: pl.DataFrame,
             horizons: Sequence[int] = HORIZONS) -> pl.DataFrame:  # fmt: skip
    """:func:`scored_rows` of every horizon, stacked."""
    return pl.concat([scored_rows(units, totals, h) for h in horizons], how="diagonal_relaxed")


def metrics(rows: pl.DataFrame) -> pl.DataFrame:
    """MAE (and n) per candidate, position, horizon and season, with the pooled levels 'all'
    (horizon and season as strings)."""
    p = predictions(rows).with_columns(pl.col("horizon").cast(pl.String),
                                       pl.col("season").cast(pl.String))  # fmt: skip
    out = []
    for pos_all in (False, True):
        for h_all in (False, True):
            for s_all in (False, True):
                x = p.with_columns(
                    *([pl.lit(ALL).alias("position")] if pos_all else []),
                    *([pl.lit(ALL).alias("horizon")] if h_all else []),
                    *([pl.lit(ALL).alias("season")] if s_all else []))  # fmt: skip
                out.append(x.group_by("candidate", "position", "horizon", "season").agg(
                    pl.len().alias("n"), pl.col("err").mean().alias("mae")))  # fmt: skip
    m = pl.concat(out)
    order = {c: i for i, c in enumerate(rt.CANDIDATES)}
    return m.with_columns(pl.col("candidate").replace_strict(order).alias("_o")).sort(
        "position", "_o", "horizon", "season").drop("_o")  # fmt: skip


@dataclass(frozen=True)
class Selection:
    chosen: dict[str, str]  # position -> candidate
    path: dict[str, tuple[str, ...]]


def select(m: pl.DataFrame, needed: int = SEASON_WINS_NEEDED) -> Selection:
    """The fixed rule (module docstring), per position, on the horizon-pooled MAEs."""
    chosen, path = {}, {}
    for pos in hs.POSITIONS:
        part = m.filter((pl.col("position") == pos) & (pl.col("horizon") == ALL))

        def mae(c: str, part: pl.DataFrame = part) -> dict[str, float]:
            x = part.filter(pl.col("candidate") == c)
            return dict(zip(x["season"], x["mae"], strict=True))

        cur, steps = rt.CANDIDATES[0], [rt.CANDIDATES[0]]
        for c in rt.CANDIDATES[1:]:
            a, b = mae(c), mae(cur)
            if not a or ALL not in a:
                continue
            wins = sum(a[s] < b[s] for s in a if s != ALL and s in b)
            if a[ALL] < b[ALL] and wins >= needed:
                cur = c
                steps.append(c)
        chosen[pos], path[pos] = cur, tuple(steps)
    return Selection(chosen, path)


def effects(rows: pl.DataFrame) -> pl.DataFrame:
    """Per position (and 'all') and horizon (and 'all'): the best (Q5) vs worst (Q1)
    matchup quintile of the as-of raw rating. ``rated_gap``: what the raw ratings said
    (mean base x (raw - 1), Q5 minus Q1, points per game); ``shrunk_gap``: the same with the
    shrunk ratings; ``realized_gap``: what happened in weeks 15-17 (mean actual - base, Q5
    minus Q1); ``survived`` = realized / rated."""
    r = rows.filter(pl.col("quintile").is_in([1, QUINTILES])).with_columns(
        (pl.col("base") * (pl.col("raw").fill_null(1.0) - 1)).alias("_rated"),
        (pl.col("base") * (pl.col("shrunk") - 1)).alias("_shrunk"),
        (pl.col("actual") - pl.col("base")).alias("_real"),
        pl.col("horizon").cast(pl.String),
    )  # fmt: skip
    out = []
    for pos_all in (False, True):
        for h_all in (False, True):
            x = r.with_columns(*([pl.lit(ALL).alias("position")] if pos_all else []),
                               *([pl.lit(ALL).alias("horizon")] if h_all else []))  # fmt: skip
            g = x.group_by("position", "horizon").agg(
                *[(pl.col(c).filter(pl.col("quintile") == QUINTILES).mean()
                   - pl.col(c).filter(pl.col("quintile") == 1).mean()).alias(n)
                  for c, n in (("_rated", "rated_gap"), ("_shrunk", "shrunk_gap"),
                               ("_real", "realized_gap"))],
                (pl.col("quintile") == 1).sum().alias("n_worst"),
                (pl.col("quintile") == QUINTILES).sum().alias("n_best"))  # fmt: skip
            out.append(g)
    e = pl.concat(out).with_columns(
        (pl.col("realized_gap") / pl.col("rated_gap")).alias("survived")
    )
    return e.select("position", "horizon", "n_worst", "n_best", "rated_gap", "shrunk_gap",
                    "realized_gap", "survived").sort("position", "horizon")  # fmt: skip


def stability(totals: pl.DataFrame, horizons: Sequence[int] = HORIZONS,
              weeks: Sequence[int] = PLAYOFF_WEEKS) -> pl.DataFrame:  # fmt: skip
    """Per position and horizon: the Spearman rank correlation of the as-of raw rating with
    the rating of weeks ``weeks`` alone (points allowed per game there over the as-of league
    average), per season, then its mean and range over the seasons."""
    late = totals.filter(pl.col("week").is_in(list(weeks))).group_by(
        "season", "position", pl.col("opponent").alias("team")).agg(
        pl.col("points").mean().alias("_late"))  # fmt: skip
    out = []
    for h in horizons:
        r = rt.ratings(totals, h).join(late, on=["season", "position", "team"])
        c = r.filter(pl.col("raw").is_not_null()).group_by("season", "position").agg(
            pl.corr("raw", "_late", method="spearman").alias("rho"))  # fmt: skip
        out.append(c.group_by("position").agg(
            pl.lit(int(h)).alias("horizon"), pl.len().alias("seasons"),
            pl.col("rho").mean().alias("rho_mean"), pl.col("rho").min().alias("rho_min"),
            pl.col("rho").max().alias("rho_max")))  # fmt: skip
    return pl.concat(out).sort("position", "horizon")


def late_weeks(units: pl.DataFrame, horizon: int = HORIZONS[-1],
               weeks: Sequence[int] = PLAYOFF_WEEKS) -> pl.DataFrame:  # fmt: skip
    """Do teams rest starters late? Per era (week 17 was the last week through 2020; 18 weeks
    from 2021), position and week: the based units (as of ``horizon``), the share that played
    for the same team, and their mean points minus base."""
    b = bases(units, horizon)
    act = units.filter(pl.col("week").is_in(list(weeks))).group_by(
        "season", "week", "pid", "team").agg(pl.col("points").sum().alias("actual"))  # fmt: skip
    wk = pl.DataFrame({"week": list(weeks)}, schema={"week": pl.Int32})
    x = b.join(wk, how="cross").join(act, on=["season", "week", "pid", "team"], how="left")
    x = x.with_columns(pl.when(pl.col("season") <= 2020).then(pl.lit("2013-2020 (17 weeks)"))
                       .otherwise(pl.lit("2021+ (18 weeks)")).alias("era"))  # fmt: skip
    return x.group_by("era", "position", "week").agg(
        pl.len().alias("based"), pl.col("actual").is_not_null().mean().alias("played_share"),
        (pl.col("actual") - pl.col("base")).mean().alias("vs_base"),
    ).sort("era", "position", "week")  # fmt: skip
