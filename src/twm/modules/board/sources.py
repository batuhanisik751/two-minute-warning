"""Board inputs beside the season table (step I1b), each read through an AsOfView (point in time)
except the own walk-forward xFP, which is a file of per player-game predictions
(:mod:`twm.modules.regression_watch.own_xfp`: a play of season S is scored by models trained on
the seasons before S only).

- Next Gen Stats per player-season (2016 on): from the WEEKLY rows (a game with at least 5
  targets or 10 carries; the week-0 season totals list only the season's qualified leaders):
  separation = target-weighted mean of ``avg_separation``; rush yards over expected per carry =
  sum(``rush_yards_over_expected``) / sum(``rush_attempts``), NULL before 2018 (upstream has
  no RYOE for 2016-2017).
- Snap share per player-season (2013 on): mean ``offense_pct`` over his regular-season games
  with an offensive snap.
- Players (``dim_player``, only players who exist at the as-of): birth date, draft.
- Combine (``fact_combine``, public from the draft's first day): his latest combine row by
  ``gsis_id`` (forty, weight, height, vertical, broad jump) and the speed score.
"""

from __future__ import annotations

from typing import Any

import polars as pl

NGS_FIRST_SEASON = 2016
RYOE_FIRST_SEASON = 2018
SNAPS_FIRST_SEASON = 2013
XFP_FIRST_SEASON = 2009  # receiver xFP is unusable in 2006-2008 (docs/progress.md, D1)


def speed_score(weight: pl.Expr, forty: pl.Expr) -> pl.Expr:
    """Bill Barnwell's Speed Score (Football Outsiders, 2008): weight x 200 / forty^4
    (pounds, seconds); 100 = average for a running back. NULL without both."""
    return weight * 200.0 / forty.pow(4)


def ngs_seasons(view: Any, last_season: int) -> pl.DataFrame:
    s = int(last_season)
    return view.sql(f"""
        WITH rec AS (
            SELECT gsis_id, season,
                   sum(avg_separation * targets) / NULLIF(sum(targets), 0) AS ngs_separation
            FROM fact_ngs_receiving_week
            WHERE season_type = 'REG' AND week > 0 AND season <= {s} AND gsis_id IS NOT NULL
              AND avg_separation IS NOT NULL
            GROUP BY gsis_id, season
        ), ru AS (
            SELECT gsis_id, season,
                   sum(rush_yards_over_expected) / NULLIF(sum(rush_attempts), 0)
                       AS ngs_ryoe_per_att
            FROM fact_ngs_rushing_week
            WHERE season_type = 'REG' AND week > 0 AND season <= {s} AND gsis_id IS NOT NULL
              AND rush_yards_over_expected IS NOT NULL
            GROUP BY gsis_id, season
        )
        SELECT COALESCE(rec.gsis_id, ru.gsis_id) AS gsis_id,
               COALESCE(rec.season, ru.season) AS season, ngs_separation, ngs_ryoe_per_att
        FROM rec FULL JOIN ru ON rec.gsis_id = ru.gsis_id AND rec.season = ru.season
        ORDER BY season, gsis_id""").with_columns(
        pl.col("season").cast(pl.Int32),
        pl.col("ngs_separation").cast(pl.Float64).round(6),
        pl.col("ngs_ryoe_per_att").cast(pl.Float64).round(6),
    )


def snap_seasons(view: Any, last_season: int) -> pl.DataFrame:
    return view.sql(f"""
        SELECT gsis_id, season, avg(offense_pct) AS snap_share
        FROM fact_snaps
        WHERE game_type = 'REG' AND season <= {int(last_season)} AND gsis_id IS NOT NULL
          AND offense_snaps > 0
        GROUP BY gsis_id, season ORDER BY season, gsis_id""").with_columns(
        pl.col("season").cast(pl.Int32), pl.col("snap_share").cast(pl.Float64).round(6)
    )


def players(view: Any) -> pl.DataFrame:
    return view.sql(
        "SELECT gsis_id, birth_date, draft_year, draft_round, draft_pick FROM dim_player"
    ).with_columns(pl.col(c).cast(pl.Int32) for c in ("draft_year", "draft_round", "draft_pick"))


def combine(view: Any) -> pl.DataFrame:
    df = view.sql("""
        SELECT gsis_id, season AS combine_season, forty, wt AS weight, height_in, vertical,
               broad_jump
        FROM fact_combine WHERE gsis_id IS NOT NULL
        QUALIFY row_number() OVER (PARTITION BY gsis_id ORDER BY season DESC) = 1
        ORDER BY gsis_id""")
    return df.with_columns(
        speed_score(pl.col("weight"), pl.col("forty")).round(6).alias("speed_score")
    )


def xfp_seasons(player_games: pl.DataFrame, last_season: int) -> pl.DataFrame:
    """Per player-season own walk-forward xFP (regular season, :data:`XFP_FIRST_SEASON` ..
    ``last_season``) from :func:`~twm.modules.regression_watch.own_xfp.history_player_games`
    rows (one per regular-season game and player)."""
    return (
        player_games.filter(pl.col("season").is_between(XFP_FIRST_SEASON, int(last_season)))
        .group_by("gsis_id", "season")
        .agg(pl.col("xfp").sum().round(6).alias("xfp_total"))
        .with_columns(pl.col("season").cast(pl.Int32))
        .sort("season", "gsis_id")
    )


def own_xfp_history(db: Any, last_season: int, progress: Any = None) -> pl.DataFrame:
    """The own walk-forward xFP per player-game, 2007 .. ``last_season`` (Regression Watch's
    history: each season scored by the fold trained on the seasons before it; folds not current
    on disk are refit, the others reused)."""
    from twm.modules.regression_watch import own_xfp as ox

    pg = ox.history_player_games(db, int(last_season), progress=progress)
    return pg.select("game_id", "gsis_id", pl.col("season").cast(pl.Int32), "xfp")
