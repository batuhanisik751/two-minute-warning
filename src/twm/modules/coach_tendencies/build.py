"""The deterministic build of the coach-tendency frames (C10b publishes them).

``build(runner, seasons, current_season)`` reads, per regular season, every ``fact_play`` row
with its offense's head coach (``coach_game``), then ``fact_team_week`` for the fantasy link.
Pass a :class:`twm.asof.AsOfView` as ``runner`` and the current season's row only counts plays
public at its ``as_of`` (``through_week`` and ``plays`` say how much of the season it covers);
persistence and the fantasy link use completed seasons only (before ``current_season``). Every
frame is sorted and the bootstrap is seeded, so equal inputs give equal frames.

Published frames (name -> key):

- ``coach_tendency_season`` (coach_id, team, season, metric): value, sample, league_avg,
  percentile, is_current, through_week, games, plays;
- ``coach_tendency_career`` (coach_id, metric): completed seasons pooled, vs_league;
- ``coach_tendency_persistence`` (metric, comparison): n_pairs, r and its 95% interval;
- ``coach_tendency_fantasy_link`` (metric, target, horizon): n, r and its 95% interval.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import polars as pl

from twm.modules.coach_tendencies import fantasy, persistence, season
from twm.modules.coach_tendencies.plays import Runner, flag_plays, pace_pairs, season_plays_sql

FIRST_SEASON = 1999
FRAME_KEYS: dict[str, tuple[str, ...]] = {
    "coach_tendency_season": ("coach_id", "team", "season", "metric"),
    "coach_tendency_career": ("coach_id", "metric"),
    "coach_tendency_persistence": ("metric", "comparison"),
    "coach_tendency_fantasy_link": ("metric", "target", "horizon"),
}


def latest_season(runner: Runner) -> int:
    """The newest season with a regular-season play visible to ``runner``."""
    return int(runner.sql("SELECT max(season) AS s FROM fact_play WHERE season_type = 'REG'")[0, 0])


def season_wide(runner: Runner, year: int, current_season: int) -> tuple[pl.DataFrame, ...]:
    """(coach-team rows, team rows) of one regular season, wide (see :mod:`.season`)."""
    flagged = flag_plays(runner.sql(season_plays_sql(year)))
    pairs = pace_pairs(flagged)
    return season.coach_season(flagged, pairs, current_season), season.team_season(flagged, pairs)


def build(
    runner: Runner,
    seasons: Sequence[int] | None = None,
    current_season: int | None = None,
    *,
    n_boot: int = persistence.N_BOOT,
    seed: int = persistence.SEED,
    progress: Callable[[str], None] | None = None,
) -> dict[str, pl.DataFrame]:
    """The published frames plus ``coach_wide`` / ``team_wide`` (wide, for checks)."""
    current = latest_season(runner) if current_season is None else current_season
    years = list(range(FIRST_SEASON, current + 1)) if seasons is None else sorted(seasons)
    coach, team = [], []
    for y in years:
        c, t = season_wide(runner, y, current)
        coach.append(c)
        team.append(t)
        if progress:
            progress(f"season {y}: {c.height} coach-team rows")
    cw = pl.concat(coach, how="vertical_relaxed").sort(season.COACH_KEYS)
    tw = pl.concat(team, how="vertical_relaxed").sort(season.TEAM_KEYS)
    rec = fantasy.team_receiving(runner.sql(fantasy.team_week_sql()))
    done = pl.col("season") < current
    links = fantasy.link_rows(tw.filter(done), rec.filter(done))
    pairs = persistence.pairs(persistence.main_rows(cw))
    return {
        "coach_tendency_season": season.season_long(cw),
        "coach_tendency_career": season.career(cw),
        "coach_tendency_persistence": persistence.persistence(pairs, n_boot, seed),
        "coach_tendency_fantasy_link": fantasy.fantasy_link(links, n_boot, seed),
        "coach_wide": cw,
        "team_wide": tw,
    }
