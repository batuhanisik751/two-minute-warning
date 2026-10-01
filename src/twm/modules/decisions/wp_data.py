"""Play states for the app's own win-probability (WP) model (PROJECT_SPEC 8.4 item 1, step G1).

**One row per scrimmage play with a valid pre-snap state**, from ``fact_play`` (the typed
warehouse, never raw play-by-play) joined to ``fact_game`` (closing spread, final result,
venue). The label is ``posteam_wins``: the team with the ball before the snap won the game.

Filters, in order (:func:`build_states` returns the count each one drops, :data:`FILTERS`):

1. ``unplayed``: the game has no final result (``fact_game.result`` NULL: future or live games).
2. ``not_scrimmage``: kickoffs, extra points, two-point tries, timeouts and other rows without a
   down, and ``no_play`` rows (a penalty before or during the snap: the down is replayed, so the
   same state comes back on the next row and would count twice). Kept: ``pass``, ``run``,
   ``punt``, ``field_goal``, ``qb_kneel``, ``qb_spike`` with a down of 1-4.
3. ``missing_state``: a NULL in any state column (possession team, score, clock, down,
   distance, yardline, timeouts) or a value outside its range (down 1-4, distance >= 1,
   yardline 1-99, timeouts 0-3, clock >= 0).
4. ``tie``: the game ended in a tie (15 regular-season games 1999-2026): "the possession team
   wins" is neither true nor false. Excluded from training and from every metric (decision
   G1, documented in reports/decisions/wp_backtest.md); ``wp()`` still answers for any state.

Derived features (all known before the snap, point-in-time):

- ``posteam_spread``: the closing spread from the possession team's view, = the points the
  market expected it to win by (``spread_line`` is "home favored by"; negated for the away
  team). Closing lines are known at kickoff, so they are allowed (spec 8.4, 6.4).
- ``posteam_is_home``: 1 home, 0 away, 0.5 at a neutral site (``fact_game.location``).
- ``receives_2h_kickoff``: 1 in the first half when the possession team will receive the
  second-half kickoff, = it KICKED the opening kickoff (on a kickoff row ``posteam`` is the
  receiving team, so the kicker is ``defteam`` of the game's first kickoff in quarter 1). Known
  from the opening kickoff on; 0 in the second half and overtime. Checked on the warehouse:
  the opening kicker received the third-quarter kickoff in 7,308 of 7,321 games 1999-2026.
- ``half_number``: 1, 2, or 3 for overtime.
- ``era_pat_2015`` (2015 on: extra points from the 15-yard line) and ``era_kickoff_2023``
  (2023 on: fair catches on kickoffs go to the 25; 2024 on: the dynamic kickoff).
- ``spread_time`` and ``diff_time_ratio``: see :func:`time_interactions`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import polars as pl

KEYS = ("season", "game_id", "play_id")
LABEL = "posteam_wins"
STATE_COLUMNS = (
    "score_differential",
    "game_seconds_remaining",
    "half_seconds_remaining",
    "down",
    "ydstogo",
    "yardline_100",
    "posteam_timeouts_remaining",
    "defteam_timeouts_remaining",
)
DERIVED = (
    "half_number",
    "receives_2h_kickoff",
    "posteam_is_home",
    "posteam_spread",
    "era_pat_2015",
    "era_kickoff_2023",
    "spread_time",
    "diff_time_ratio",
)
FEATURES = (*STATE_COLUMNS, *DERIVED)
SCRIMMAGE_TYPES = ("pass", "run", "punt", "field_goal", "qb_kneel", "qb_spike")
FILTERS = ("unplayed", "not_scrimmage", "missing_state", "tie")
# kept for slices and the comparison with nflfastR (never features)
CONTEXT = ("week", "season_type", "qtr", "fixed_drive", "play_type", "posteam", "home_team",
           "wp", "vegas_wp")  # fmt: skip

_PLAY_COLS = ("game_id", "play_id", "season", "week", "season_type", "qtr", "game_half", "down",
              "fixed_drive", "play_type", "kickoff_attempt", "posteam", "defteam", "home_team",
              "away_team",
              *(c for c in STATE_COLUMNS if c != "down"), "wp", "vegas_wp")  # fmt: skip
_GAME_COLS = ("game_id", "spread_line", "result", "location")


def load_raw(db: Path | str, seasons: tuple[int, ...]) -> tuple[pl.DataFrame, pl.DataFrame]:
    """The plays and games of ``seasons`` from the typed warehouse (read-only): the inputs of
    :func:`build_states`. Kickoff rows are kept (the opening kickoff decides
    ``receives_2h_kickoff``); every other row is filtered in :func:`build_states`."""
    import duckdb

    con = duckdb.connect()
    try:
        con.execute(f"ATTACH '{Path(db)}' AS w (READ_ONLY)")
        ss = ", ".join(str(int(s)) for s in seasons) or "NULL"
        plays = con.sql(
            f"SELECT {', '.join(_PLAY_COLS)} FROM w.fact_play WHERE season IN ({ss}) "
            "ORDER BY game_id, play_id"
        ).pl()
        games = con.sql(
            f"SELECT {', '.join(_GAME_COLS)} FROM w.fact_game WHERE season IN ({ss}) "
            "ORDER BY game_id"
        ).pl()
    finally:
        con.close()
    return plays, games


def opening_kickers(plays: pl.DataFrame) -> pl.DataFrame:
    """game_id -> ``opening_kicker``: the team that kicked the game's first kickoff in quarter
    1 (``defteam`` of that row: on a kickoff row ``posteam`` is the receiving team)."""
    return (
        plays.filter((pl.col("kickoff_attempt") == 1) & (pl.col("qtr") == 1))
        .sort(["game_id", "play_id"])
        .group_by("game_id", maintain_order=True)
        .agg(pl.col("defteam").first().alias("opening_kicker"))
    )


@dataclass(frozen=True)
class States:
    """The model rows (KEYS, FEATURES, LABEL, CONTEXT; sorted by KEYS) and the rows each filter
    dropped (``drops``: filter -> count, in :data:`FILTERS` order; ``n_candidates`` = play rows
    before filtering)."""

    rows: pl.DataFrame
    drops: dict[str, int]
    n_candidates: int
    n_tie_games: int


def _valid_state() -> pl.Expr:
    home, away = pl.col("posteam") == pl.col("home_team"), pl.col("posteam") == pl.col("away_team")
    ok = (home | away) & (pl.col("defteam") != pl.col("posteam"))
    for c in STATE_COLUMNS:
        ok = ok & pl.col(c).is_not_null()
    return (
        ok
        & pl.col("down").is_between(1, 4)
        & (pl.col("ydstogo") >= 1)
        & pl.col("yardline_100").is_between(1, 99)
        & pl.col("posteam_timeouts_remaining").is_between(0, 3)
        & pl.col("defteam_timeouts_remaining").is_between(0, 3)
        & (pl.col("game_seconds_remaining") >= 0)
        & (pl.col("half_seconds_remaining") >= 0)
    )


def add_features(frame: pl.DataFrame) -> pl.DataFrame:
    """The derived features of :data:`DERIVED` from a frame with the state columns, ``season``,
    ``game_half``, ``posteam``, ``home_team``, ``opening_kicker``, ``spread_line`` and
    ``location``."""
    home = pl.col("posteam") == pl.col("home_team")
    return frame.with_columns(
        pl.when(pl.col("game_half") == "Half1").then(1)
        .when(pl.col("game_half") == "Half2").then(2)
        .otherwise(3).cast(pl.Int8).alias("half_number"),
        pl.when(pl.col("game_half") != "Half1").then(0)
        .when(pl.col("opening_kicker").is_null()).then(None)
        .otherwise((pl.col("posteam") == pl.col("opening_kicker")).cast(pl.Int8))
        .cast(pl.Int8).alias("receives_2h_kickoff"),
        pl.when(pl.col("location") == "Neutral").then(0.5)
        .otherwise(home.cast(pl.Float64)).alias("posteam_is_home"),
        pl.when(home).then(pl.col("spread_line")).otherwise(-pl.col("spread_line"))
        .cast(pl.Float64).alias("posteam_spread"),
        (pl.col("season") >= 2015).cast(pl.Int8).alias("era_pat_2015"),
        (pl.col("season") >= 2023).cast(pl.Int8).alias("era_kickoff_2023"),
    ).with_columns(time_interactions())  # fmt: skip


def time_interactions() -> list[pl.Expr]:
    """``spread_time`` = posteam_spread x exp(-4 x elapsed) and ``diff_time_ratio`` =
    score_differential / exp(-4 x elapsed), elapsed = share of regulation played (0-1, overtime
    = 1): the pregame spread matters less and the lead more as the clock runs (nflfastR's own
    two interactions). Chosen on 1999-2003 -> 2004-2005 validation only (log loss .4370 ->
    .4354), never on a test season."""
    played = ((3600 - pl.col("game_seconds_remaining")) / 3600).clip(0.0, 1.0)
    elapsed = pl.when(pl.col("half_number") == 3).then(1.0).otherwise(played)
    decay = (-4.0 * elapsed).exp()
    return [
        (pl.col("posteam_spread") * decay).alias("spread_time"),
        (pl.col("score_differential") / decay).alias("diff_time_ratio"),
    ]


def build_states(plays: pl.DataFrame, games: pl.DataFrame) -> States:
    """Model rows from raw play and game rows (see the module docstring for every filter)."""
    n0 = plays.height
    f = plays.join(games.select(_GAME_COLS), on="game_id", how="left").join(
        opening_kickers(plays), on="game_id", how="left"
    )
    drops: dict[str, int] = {}

    def keep(name: str, cond: pl.Expr) -> None:
        nonlocal f
        before = f.height
        f = f.filter(cond.fill_null(False))
        drops[name] = before - f.height

    keep("unplayed", pl.col("result").is_not_null())
    keep("not_scrimmage", pl.col("down").is_not_null()
         & pl.col("play_type").is_in(list(SCRIMMAGE_TYPES)))  # fmt: skip
    keep("missing_state", _valid_state())
    n_tie_games = f.filter(pl.col("result") == 0).get_column("game_id").n_unique()
    keep("tie", pl.col("result") != 0)
    home = pl.col("posteam") == pl.col("home_team")
    won = pl.when(home).then(pl.col("result") > 0).otherwise(pl.col("result") < 0)
    f = add_features(f).with_columns(won.cast(pl.Int8).alias(LABEL))
    cols = list(dict.fromkeys([*KEYS, *FEATURES, LABEL, *CONTEXT]))
    rows = f.select(cols).sort(list(KEYS))
    return States(rows, drops, n0, n_tie_games)


def load_states(db: Path | str, seasons: tuple[int, ...]) -> States:
    """:func:`build_states` on the warehouse rows of ``seasons``."""
    plays, games = load_raw(db, seasons)
    return build_states(plays, games)
