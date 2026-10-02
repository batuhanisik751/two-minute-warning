"""Board populations and labels (step I1b, PROJECT_SPEC 8.6; owner decision 2026-10-02).

A row = one player at the end-of-season snapshot of season S; the label looks at season S+1
(every game of S+1 is played after the snapshot, spec 6.2 rule 3).

**Cliff** (veterans): at least :data:`CLIFF_MIN_PRIOR` prior seasons (S - entry year: seasons in
the league before S) and top-:data:`CLIFF_TOP` at his position in PPG in S (ranked: at least 8
games). In S+1, with ``g`` games and PPG ``p``:

- ``y_missed`` = g < :data:`MIN_GAMES_NEXT` (6): injury, benching, release, retirement (0 games);
- ``y_cliff`` = g >= 6 and p <= (1 - :data:`CLIFF_DROP`) x PPG(S) (a drop of 30% or more);
  NULL when ``y_missed`` (those rows are left out of the main Cliff model);
- ``y_cliff_or_missed`` = ``y_cliff`` or ``y_missed`` (the sensitivity run).

**Breakout** (young players): WR, TE and RB in their first or second season (S - entry year 0
or 1: entering their 2nd or 3rd) with a regular-season stat line in S, NOT top-N in PPG in S
(:data:`BREAKOUT_EXCLUDE_TOP`: WR 36, TE 12, RB 24; an unranked player, fewer than 8 games,
is not top-N). ``y_breakout`` = top-N at his S+1 position in PPG with at least 8 games in S+1
(:data:`BREAKOUT_TOP`: WR 24, TE 12, RB 24); RB is reported apart (spec: "optionally RB").
"""

from __future__ import annotations

import polars as pl

CLIFF_MIN_PRIOR = 3
CLIFF_TOP = 36
CLIFF_DROP = 0.30
MIN_GAMES_NEXT = 6
EPS = 1e-9  # a drop of exactly 30% counts (floating point)
BREAKOUT_POSITIONS = ("WR", "TE", "RB")
BREAKOUT_EXCLUDE_TOP = {"WR": 36, "TE": 12, "RB": 24}
BREAKOUT_TOP = {"WR": 24, "TE": 12, "RB": 24}
OUTCOME_COLUMNS = ("games_next", "ppg_next", "pos_rank_next", "position_next")
LABELS = ("y_cliff", "y_missed", "y_cliff_or_missed", "y_breakout")


def outcomes(seasons: pl.DataFrame) -> pl.DataFrame:
    """S+1 outcomes keyed to season S: games, PPG, rank and position in S+1, from a
    player-season table (:func:`twm.modules.board.seasons.player_seasons`)."""
    return seasons.select(
        "gsis_id",
        (pl.col("season") - 1).cast(pl.Int32).alias("season"),
        pl.col("games").alias("games_next"),
        pl.col("ppg").alias("ppg_next"),
        pl.col("pos_rank").alias("pos_rank_next"),
        pl.col("position").alias("position_next"),
    )


def add_labels(rows: pl.DataFrame, nxt: pl.DataFrame, last_complete: int) -> pl.DataFrame:
    """``rows`` (features with season, position, ppg, pos_rank, prior_seasons) + the outcome
    columns, population flags (``in_cliff``, ``in_breakout``) and :data:`LABELS`. Rows of a season
    whose S+1 is not complete (S >= ``last_complete``) get NULL labels."""
    c = pl.col
    df = rows.join(nxt, on=["gsis_id", "season"], how="left").with_columns(
        c("games_next").fill_null(0)
    )
    known = c("season") < int(last_complete)
    missed = c("games_next") < MIN_GAMES_NEXT
    cliff = ~missed & (c("ppg_next") <= (1.0 - CLIFF_DROP) * c("ppg") + EPS)
    top_next = c("position_next").replace_strict(BREAKOUT_TOP, default=None, return_dtype=pl.Int64)
    excl = c("position").replace_strict(BREAKOUT_EXCLUDE_TOP, default=None, return_dtype=pl.Int64)
    in_breakout = (
        c("position").is_in(list(BREAKOUT_POSITIONS))
        & c("prior_seasons").is_in([0, 1])
        & (c("pos_rank").is_null() | (c("pos_rank") > excl))
    )
    return df.with_columns(
        ((c("pos_rank") <= CLIFF_TOP) & (c("prior_seasons") >= CLIFF_MIN_PRIOR))
        .fill_null(False)
        .alias("in_cliff"),
        in_breakout.fill_null(False).alias("in_breakout"),
        pl.when(known & ~missed).then(cliff).alias("y_cliff"),
        pl.when(known).then(missed).alias("y_missed"),
        pl.when(known).then(missed | cliff).alias("y_cliff_or_missed"),
        pl.when(known).then((c("pos_rank_next") <= top_next).fill_null(False)).alias("y_breakout"),
    )
