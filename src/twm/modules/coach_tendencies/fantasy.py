"""The fantasy link, measured: do a team's tendencies go with its pass-catchers' production?

Team-seasons 2013-2025 (regular season, every coach of the season pooled). Production from
``fact_team_week`` (the team's receiving totals = all its pass-catchers, backs included):
``targets_per_game`` and ``recv_ppr_per_game`` (full PPR receiving points: 1 per reception,
0.1 per yard, 6 per touchdown, 2 per two-point catch, -2 per lost fumble). Both sides are
season-relative (tendency minus the league value, production minus the season's team mean), so
era drift is not counted. ``same_season``: tendency and production of the same season (a
description, not a forecast); ``next_season``: tendency in s, production in s+1 for the same
team (whoever coaches it). Pearson r with the season-block bootstrap of :mod:`.persistence`;
``y_per_x_sd`` = r * sd(y): the production difference that goes with a one-SD tendency
difference (``x_sd``).
"""

from __future__ import annotations

import numpy as np
import polars as pl

from twm.modules.coach_tendencies.persistence import N_BOOT, SEED, block_bootstrap, pearson
from twm.modules.coach_tendencies.season import METRICS

FANTASY_FIRST, FANTASY_LAST = 2013, 2025
TARGETS = ("targets_per_game", "recv_ppr_per_game")
HORIZONS = ("same_season", "next_season")
LINK_COLUMNS = (
    "metric", "target", "horizon", "n", "n_seasons", "r", "ci_low", "ci_high", "x_sd",
    "y_per_x_sd",
)  # fmt: skip
TEAM_WEEK_COLUMNS = (
    "team", "season", "game_id", "targets", "receptions", "receiving_yards", "receiving_tds",
    "receiving_2pt_conversions", "receiving_fumbles_lost",
)  # fmt: skip


def team_week_sql(first: int = FANTASY_FIRST, last: int = FANTASY_LAST) -> str:
    cols = ", ".join(f'"{c}"' for c in TEAM_WEEK_COLUMNS)
    return (
        f"SELECT {cols} FROM fact_team_week WHERE season_type = 'REG' "
        f"AND season BETWEEN {int(first)} AND {int(last)} ORDER BY season, team, game_id"
    )


def team_receiving(team_week: pl.DataFrame) -> pl.DataFrame:
    """Per (team, season): games, targets and receiving PPR points per game."""
    z = {c: pl.col(c).fill_null(0) for c in TEAM_WEEK_COLUMNS[3:]}
    ppr = (
        z["receptions"] + 0.1 * z["receiving_yards"] + 6 * z["receiving_tds"]
        + 2 * z["receiving_2pt_conversions"] - 2 * z["receiving_fumbles_lost"]
    )  # fmt: skip
    g = team_week.group_by("team", "season").agg(
        pl.col("game_id").n_unique().cast(pl.Int64).alias("games"),
        z["targets"].sum().alias("_t"),
        ppr.sum().alias("_p"),
    )
    return g.select(
        "team", "season", "games",
        (pl.col("_t") / pl.col("games")).alias("targets_per_game"),
        (pl.col("_p") / pl.col("games")).alias("recv_ppr_per_game"),
    ).sort("season", "team")  # fmt: skip


def link_rows(team_tend: pl.DataFrame, receiving: pl.DataFrame) -> pl.DataFrame:
    """One row per (horizon, team, season of the tendency): ``x_<m>`` (tendency - league) and
    ``y_<target>`` (production - the season's team mean), FANTASY_FIRST..FANTASY_LAST."""
    span = pl.col("season").is_between(FANTASY_FIRST, FANTASY_LAST)
    x = team_tend.filter(span).select(
        "team", "season", *[(pl.col(m) - pl.col(f"{m}_league")).alias(f"x_{m}") for m in METRICS]
    )
    y = receiving.filter(span).with_columns(
        (pl.col(t) - pl.col(t).mean().over("season")).alias(f"y_{t}") for t in TARGETS
    )
    ycols = ["team", "season", *[f"y_{t}" for t in TARGETS]]
    same = x.join(y.select(ycols), on=["team", "season"])
    nxt = x.join(y.select(ycols).with_columns(pl.col("season") - 1), on=["team", "season"])
    return pl.concat(
        [
            same.with_columns(pl.lit(HORIZONS[0]).alias("horizon")),
            nxt.with_columns(pl.lit(HORIZONS[1]).alias("horizon")),
        ]
    ).sort("horizon", "season", "team")


def fantasy_link(rows: pl.DataFrame, n_boot: int = N_BOOT, seed: int = SEED) -> pl.DataFrame:
    """The published link frame: one row per (metric, target, horizon)."""
    out = []
    for m in METRICS:
        for t in TARGETS:
            for h in HORIZONS:
                p = rows.filter(
                    (pl.col("horizon") == h)
                    & pl.col(f"x_{m}").is_not_null()
                    & pl.col(f"y_{t}").is_not_null()
                )
                x, y = p[f"x_{m}"].to_numpy(), p[f"y_{t}"].to_numpy()
                s = p["season"].to_numpy()
                r = pearson(x, y)
                lo, hi = block_bootstrap(x, y, s, n_boot, seed) if p.height else (np.nan, np.nan)
                sx = float(x.std(ddof=1)) if p.height > 1 else float("nan")
                sy = float(y.std(ddof=1)) if p.height > 1 else float("nan")
                out.append(
                    {
                        "metric": m,
                        "target": t,
                        "horizon": h,
                        "n": p.height,
                        "n_seasons": len(np.unique(s)),
                        "r": r,
                        "ci_low": lo,
                        "ci_high": hi,
                        "x_sd": sx,
                        "y_per_x_sd": r * sy,
                    }
                )
    return pl.DataFrame(out).select(LINK_COLUMNS)
