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
- ``drive_value`` and ``z_margin`` (G1b, :data:`SMOOTH_FEATURES`): see :func:`smooth_features`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
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


# Expected points of the CURRENT drive for the offense (touchdown 7, field goal 3, opponent's
# touchdown -7, safety -2, else 0) after a 1st down, by yards to the end zone (rows: 1-10,
# 11-20 ... 91-99, read at 5.5 ... 95.5) and seconds left in the half (columns: 0-5, 6-15,
# 16-30, 31-60, 61-120, 121-240, 241-600, over 600, read at DRIVE_EP_SECONDS): measured in G1b
# on the 50,596 first-half first downs of 1999-2005 (validation seasons only, never a test
# season), then made monotone (isotonic: fewer yards or more time never lowers it).
DRIVE_EP_TABLE: tuple[tuple[float, ...], ...] = (
    (3.06, 3.86, 4.96, 5.21, 5.27, 5.51, 5.51, 5.51),
    (2.62, 2.82, 3.39, 3.99, 4.39, 4.48, 4.55, 4.67),
    (1.85, 2.09, 2.76, 3.53, 3.67, 3.83, 3.99, 4.01),
    (0.74, 1.42, 2.28, 2.63, 3.30, 3.33, 3.33, 3.33),
    (0.19, 0.45, 1.09, 2.14, 2.33, 2.70, 2.70, 2.70),
    (0.00, 0.30, 0.74, 1.36, 1.99, 2.09, 2.09, 2.09),
    (0.00, 0.00, 0.12, 0.76, 1.42, 1.72, 1.72, 1.72),
    (0.00, 0.00, 0.09, 0.32, 0.89, 1.35, 1.35, 1.40),
    (0.00, 0.00, 0.05, 0.11, 0.76, 0.76, 1.05, 1.05),
    (0.00, 0.00, 0.00, 0.07, 0.36, 0.50, 0.70, 0.70),
)
DRIVE_EP_YARDS = tuple(5.5 + 10 * i for i in range(10))
DRIVE_EP_SECONDS = (3.0, 10.5, 23.0, 45.5, 90.5, 180.5, 420.5, 900.0)
OPP_DRIVE_START = 72  # the other team's next drive: its own 28 (1999-2005 typical start)
# The offense's points MINUS the defense's from a first-half 1st down until halftime (same rows,
# columns and reading points as DRIVE_EP_TABLE): measured in G1b on the 1999-2005 first halves
# (validation seasons only), made non-increasing in yards to go per column (isotonic). The value
# of having the ball before the half ends, the other team's later possessions included.
HALF_VALUE_TABLE: tuple[tuple[float, ...], ...] = (
    (3.05, 3.77, 4.71, 4.95, 4.97, 4.63, 5.31, 5.70),
    (2.62, 2.82, 3.32, 3.67, 4.01, 4.25, 4.36, 4.37),
    (1.70, 1.97, 2.66, 3.40, 3.29, 3.60, 3.63, 3.93),
    (0.78, 1.39, 2.16, 2.71, 2.97, 3.03, 2.92, 2.81),
    (0.18, 0.53, 1.09, 2.03, 2.13, 2.31, 2.29, 2.33),
    (0.00, 0.32, 0.70, 1.21, 1.50, 1.86, 1.83, 1.74),
    (0.00, -0.06, 0.11, 0.53, 1.05, 1.29, 1.30, 1.14),
    (0.00, -0.06, 0.03, 0.11, 0.37, 0.38, 0.39, 0.39),
    (0.00, -0.06, 0.03, -0.03, 0.11, -0.26, -0.06, -0.12),
    (0.00, -0.06, 0.00, -0.03, -0.29, -0.93, -0.30, -0.49),
)
MARGIN_SD = 13.5  # standard deviation of an NFL final margin about the spread (points)
SMOOTH_FEATURES = ("drive_value", "z_margin")


DRIVE_POINTS = {"Touchdown": 7.0, "Field goal": 3.0, "Opp touchdown": -7.0, "Safety": -2.0}
_SECONDS_EDGES = (5, 15, 30, 60, 120, 240, 600)


def measure_drive_ep_table(db: Path | str, seasons: tuple[int, ...] = tuple(range(1999, 2006))):
    """How :data:`DRIVE_EP_TABLE` was measured (G1b; re-run by a realdata test): mean drive
    points (:data:`DRIVE_POINTS` of ``fixed_drive_result``) of the first-half 1st downs of
    ``seasons`` by yardline row and seconds column, then made monotone by isotonic passes
    (play-weighted) and clipped at 0. Returns a 10 x 8 array."""
    import duckdb
    from sklearn.isotonic import IsotonicRegression

    con = duckdb.connect()
    try:
        con.execute(f"ATTACH '{Path(db)}' AS w (READ_ONLY)")
        ss = ", ".join(str(int(x)) for x in seasons)
        f = con.sql(
            "SELECT half_seconds_remaining AS hsr, yardline_100 AS yl, fixed_drive_result AS r "
            f"FROM w.fact_play WHERE season IN ({ss}) AND down = 1 AND game_half = 'Half1' "
            f"AND play_type IN ({', '.join(repr(t) for t in SCRIMMAGE_TYPES)})"
        ).pl()
    finally:
        con.close()
    f = f.with_columns(
        pl.col("r").replace_strict(DRIVE_POINTS, default=0.0).alias("pts"),
        ((pl.col("yl") - 1) // 10).clip(0, 9).alias("yb"),
        pl.sum_horizontal([(pl.col("hsr") > e).cast(pl.Int32) for e in _SECONDS_EDGES])
        .alias("hb"),  # column k = (edge k-1, edge k] seconds
    )  # fmt: skip
    p, n = np.zeros((10, 8)), np.zeros((10, 8))
    for yb, hb, k, m in f.group_by("yb", "hb").agg(pl.len(), pl.col("pts").mean()).iter_rows():
        p[yb, hb], n[yb, hb] = m, k
    q = p.copy()
    for _ in range(3):
        for j in range(8):
            q[:, j] = IsotonicRegression(increasing=False).fit(
                np.arange(10), q[:, j], sample_weight=n[:, j]).predict(np.arange(10))  # fmt: skip
        for i in range(10):
            q[i, :] = IsotonicRegression(increasing=True).fit(
                np.arange(8), q[i, :], sample_weight=n[i, :]).predict(np.arange(8))  # fmt: skip
    return np.clip(q, 0.0, None)


def measure_half_value_table(db: Path | str,
                             seasons: tuple[int, ...] = tuple(range(1999, 2006))):  # fmt: skip
    """How :data:`HALF_VALUE_TABLE` was measured (G1b; re-run by a realdata test): for every
    first-half 1st down of ``seasons``, the offense's points minus the defense's from that snap
    to halftime (the score at the first snap of the second half), averaged by yardline row and
    seconds column, then made non-increasing in yards to go (isotonic, play-weighted, per
    column). Returns a 10 x 8 array."""
    import duckdb
    from sklearn.isotonic import IsotonicRegression

    con = duckdb.connect()
    try:
        con.execute(f"ATTACH '{Path(db)}' AS w (READ_ONLY)")
        ss = ", ".join(str(int(x)) for x in seasons)
        f = con.sql(
            "SELECT game_id, game_half, posteam, home_team, down, yardline_100 AS yl, "
            "half_seconds_remaining AS hsr, score_differential AS sd FROM w.fact_play "
            f"WHERE season IN ({ss}) AND game_half IN ('Half1', 'Half2') AND posteam IS NOT NULL "
            "AND score_differential IS NOT NULL ORDER BY game_id, play_id"
        ).pl()
    finally:
        con.close()
    home = pl.col("posteam") == pl.col("home_team")
    f = f.with_columns(pl.when(home).then(pl.col("sd")).otherwise(-pl.col("sd")).alias("lead"))
    half = (f.filter(pl.col("game_half") == "Half2").group_by("game_id")
            .agg(pl.col("lead").first().alias("lead_half")))  # fmt: skip
    h1 = f.filter((pl.col("game_half") == "Half1") & (pl.col("down") == 1)).join(half, "game_id")
    h1 = h1.with_columns(
        ((pl.col("lead_half") - pl.col("lead")) * pl.when(home).then(1).otherwise(-1)).alias("net"),
        ((pl.col("yl") - 1) // 10).clip(0, 9).alias("yb"),
        pl.sum_horizontal([(pl.col("hsr") > e).cast(pl.Int32) for e in _SECONDS_EDGES])
        .alias("hb"),
    )  # fmt: skip
    p, n = np.zeros((10, 8)), np.zeros((10, 8))
    for yb, hb, k, m in h1.group_by("yb", "hb").agg(pl.len(), pl.col("net").mean()).iter_rows():
        p[yb, hb], n[yb, hb] = m, k
    for j in range(8):
        p[:, j] = IsotonicRegression(increasing=False).fit(
            np.arange(10), p[:, j], sample_weight=n[:, j]).predict(np.arange(10))  # fmt: skip
    return p


def _read(table, yardline_100, half_seconds) -> np.ndarray:
    """``table`` interpolated: linear in log(1 + seconds) (0 at 0 seconds: the half is over)
    and linear in yards (flat beyond the first and last row)."""
    y = np.asarray(yardline_100, dtype=np.float64)
    h = np.log1p(np.clip(np.asarray(half_seconds, dtype=np.float64), 0.0, None))
    xs = np.log1p(np.array((0.0, *DRIVE_EP_SECONDS)))
    by_row = np.column_stack([np.interp(h, xs, (0.0, *row)) for row in table])
    pos = np.interp(y, DRIVE_EP_YARDS, np.arange(10.0))
    lo = np.floor(pos).astype(int).clip(0, 8)
    frac = pos - lo
    k = np.arange(y.size)
    return (1.0 - frac) * by_row[k, lo] + frac * by_row[k, lo + 1]


def drive_ep(yardline_100, half_seconds) -> np.ndarray:
    """:data:`DRIVE_EP_TABLE` read at the given states (see :func:`_read`)."""
    return _read(DRIVE_EP_TABLE, yardline_100, half_seconds)


def half_value(yardline_100, half_seconds) -> np.ndarray:
    """:data:`HALF_VALUE_TABLE` read at the given states (see :func:`_read`)."""
    return _read(HALF_VALUE_TABLE, yardline_100, half_seconds)


def smooth_features(frame: pl.DataFrame) -> pl.DataFrame:
    """``drive_value`` = what having the ball here is expected to add to the offense's margin
    before the half ends (:func:`half_value`: its points minus the other team's, measured);
    0 when the half is over. ``z_margin`` = (score_differential + drive_value +
    posteam_spread x t) / (MARGIN_SD x sqrt(t) + 1), t = share of regulation left: the lead in
    standard deviations of what can still happen (a random-walk view of the margin)."""
    yl = frame.get_column("yardline_100").to_numpy()
    hs = frame.get_column("half_seconds_remaining").to_numpy().astype(np.float64)
    value = half_value(yl, hs)
    f = frame.with_columns(pl.Series("drive_value", value, dtype=pl.Float64))
    t = (pl.col("game_seconds_remaining") / 3600).clip(0.0, 1.0)
    z = (pl.col("score_differential") + pl.col("drive_value") + pl.col("posteam_spread") * t) / (
        MARGIN_SD * t.sqrt() + 1.0
    )
    return f.with_columns(z.alias("z_margin"))


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
    f = smooth_features(add_features(f)).with_columns(won.cast(pl.Int8).alias(LABEL))
    cols = list(dict.fromkeys([*KEYS, *FEATURES, *SMOOTH_FEATURES, LABEL, *CONTEXT]))
    rows = f.select(cols).sort(list(KEYS))
    return States(rows, drops, n0, n_tie_games)


def load_states(db: Path | str, seasons: tuple[int, ...]) -> States:
    """:func:`build_states` on the warehouse rows of ``seasons``."""
    plays, games = load_raw(db, seasons)
    return build_states(plays, games)
