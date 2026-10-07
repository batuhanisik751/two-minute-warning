"""Which plays each tendency is measured on (docs/coach_tendencies.md, "Definitions").

Every play of a regular-season game is credited to the head coach of the team with the ball in
that game: ``coach_game`` joined on ``(game_id, team = posteam)``, the schedule's coaches
corrected by data/manual/coach_corrections.csv in the warehouse build (so a mid-season firing
splits the season at the right game). The flags, from each play's own pre-snap state:

- ``is_off``: an offensive snap: ``pass = 1 or rush = 1`` (dropbacks incl. sacks and scrambles,
  designed runs, and plays wiped out by a penalty after the snap) with a down (two-point tries
  excluded; kneels and spikes are neither pass nor rush);
- ``is_neutral_play``: an offensive snap on 1st-3rd down in a neutral situation:
  ``fact_play.is_neutral`` (win probability 0.20-0.80, more than 120 s left in the half, config
  ``neutral``) and quarters 1-3;
- ``is_early``: a neutral snap on 1st or 2nd down;
- ``is_fourth``: a fourth-down choice (play_type pass, run, punt or field_goal; replayed downs
  ``no_play`` and kneels excluded) with ``is_neutral`` (any quarter: a close fourth quarter is
  where the choice is real); ``is_go``: the offense ran a play (fakes included);
  ``is_short``: 4th-and-1 or 4th-and-2.
"""

from __future__ import annotations

from typing import Protocol

import polars as pl

PLAY_COLUMNS = (
    "game_id", "play_id", "season", "week", "season_type", "posteam", "qtr", "down", "ydstogo",
    "fixed_drive", "play_type", "pass", "rush", "no_huddle", "shotgun", "timeout",
    "game_seconds_remaining", "pass_oe", "is_neutral",
)  # fmt: skip
NEUTRAL_MAX_QTR = 3
NEUTRAL_MAX_DOWN = 3
SHORT_YARDS = 2
FOURTH_CHOICES = ("pass", "run", "punt", "field_goal")
GO_TYPES = ("pass", "run")


class Runner(Protocol):
    """Anything that runs SQL into polars: :class:`twm.asof.AsOfView` (point in time)."""

    def sql(self, query: str) -> pl.DataFrame: ...


def season_plays_sql(season: int) -> str:
    """Every regular-season row of ``season`` (pace needs the rows between snaps too), with the
    head coach of the team with the ball (NULL on rows without one)."""
    cols = ", ".join(f'p."{c}"' for c in PLAY_COLUMNS)
    return (
        f"SELECT {cols}, c.coach_id FROM fact_play p LEFT JOIN coach_game c "
        "ON c.game_id = p.game_id AND c.team = p.posteam "
        f"WHERE p.season = {int(season)} AND p.season_type = 'REG' ORDER BY p.game_id, p.play_id"
    )


def flag_plays(plays: pl.DataFrame) -> pl.DataFrame:
    """Add the module docstring's flags (all non-null booleans)."""
    snap = (pl.col("pass") == 1) | (pl.col("rush") == 1)
    off = (snap & pl.col("down").is_not_null()).fill_null(False)
    neutral_state = pl.col("is_neutral").fill_null(False)
    neutral = off & neutral_state & (pl.col("qtr") <= NEUTRAL_MAX_QTR) & (
        pl.col("down") <= NEUTRAL_MAX_DOWN)  # fmt: skip
    fourth = (
        (pl.col("down") == 4) & pl.col("play_type").is_in(FOURTH_CHOICES) & neutral_state
    ).fill_null(False)
    return plays.with_columns(
        off.alias("is_off"),
        neutral.fill_null(False).alias("is_neutral_play"),
        (neutral & (pl.col("down") <= 2)).fill_null(False).alias("is_early"),
        fourth.alias("is_fourth"),
        (fourth & pl.col("play_type").is_in(GO_TYPES)).alias("is_go"),
        (fourth & (pl.col("ydstogo") <= SHORT_YARDS)).fill_null(False).alias("is_short"),
    )


# Pace: a pair is a neutral offensive snap and the NEXT row of the game's play-by-play when that
# row is the same offense's next snap of the same drive and quarter, with no timeout charged on
# the first play (nflfastR puts a timeout on the play's own row or on a row of its own; either
# breaks the pair, as do two-minute warnings, quarter ends and pre-snap penalty rows). Seconds =
# game clock elapsed between the two snaps (a stopped clock after an incompletion counts as it
# ran: the usual seconds-per-play convention). Pairs over MAX_PAIR_SECONDS are clock glitches.
# Pace is "not charted" for a team-game whose clock is too coarse (audit 2026-10-06): fewer than
# CLOCK_MIN_SHARE of its offensive snaps carry distinct game_seconds_remaining values (every
# 1999 team-game is at most 0.5; below the cutoff: 62 of 2000's 492 team-games, weeks 1-11, and
# 3 of 2001's; from 2002 to 2025 none is below 0.77). Such games give no pairs, so a team-season
# without a charted game has no pace (NULL, like PROE before 2006).
MAX_PAIR_SECONDS = 60
CLOCK_MIN_SHARE = 0.75


def charted_games(plays: pl.DataFrame) -> pl.DataFrame:
    """(game_id, posteam) of the team-games whose clock is charted (see CLOCK_MIN_SHARE)."""
    off = plays.filter(pl.col("is_off") & pl.col("posteam").is_not_null())
    share = off.group_by("game_id", "posteam").agg(
        (pl.col("game_seconds_remaining").n_unique() / pl.len()).alias("share")
    )
    return share.filter(pl.col("share") >= CLOCK_MIN_SHARE).select("game_id", "posteam")


def pace_pairs(plays: pl.DataFrame) -> pl.DataFrame:
    """One row per pace pair of :func:`flag_plays` output (all rows of the games, in any order):
    the first snap's keys and ``seconds``; none from a team-game whose clock is not charted."""
    d = plays.sort("game_id", "play_id")

    def nxt(c: str) -> pl.Expr:
        return pl.col(c).shift(-1).over("game_id")

    seconds = pl.col("game_seconds_remaining") - nxt("game_seconds_remaining")
    same = [(pl.col(c) == nxt(c)).fill_null(False) for c in ("posteam", "fixed_drive", "qtr")]
    ok = (
        pl.col("is_off")
        & nxt("is_off").fill_null(False)
        & pl.all_horizontal(same)
        & (pl.col("timeout").fill_null(0) != 1)
        & pl.col("is_neutral").fill_null(False)
        & (pl.col("qtr") <= NEUTRAL_MAX_QTR)
    )
    out = d.with_columns(seconds.alias("seconds"), ok.fill_null(False).alias("_pair"))
    out = out.filter(
        pl.col("_pair") & pl.col("seconds").is_between(0, MAX_PAIR_SECONDS, closed="both")
    ).join(charted_games(d), on=["game_id", "posteam"], how="semi")
    keep = [c for c in ("game_id", "play_id", "season", "week", "posteam", "coach_id") if c in d]
    return out.select(*keep, "seconds")
