"""Waiver Radar labels (C2): did a pool player become a weekly fantasy starter soon after the as-of?

At each Tuesday as-of after week N the Waiver Radar ranks the players in the candidate pool (C1)
by how likely they are to become startable soon. To train and grade that ranking, every pool row
gets a label built from what happened AFTER the as-of (PROJECT_SPEC 8.1, 6.2 rule 3):

- ``y_hit``: at least one **starter finish** in the window weeks he played;
- ``y_sustained``: at least two.

The pieces (docs/waiver_radar.md "Labels" explains each one for a beginner):

- **Weekly finish** (:func:`weekly_finishes`). Every regular-season week, the players with a
  stat line that week (``fact_player_week``) are ranked within their position by fantasy points
  (config/scoring.yaml); ties share the better rank (rank method 'min': 20, 15, 15, 10 ranks 1,
  2, 2, 4). The position is the player's point-in-time roster position THAT week
  (``fact_roster_week``; missing that week: his latest earlier roster week of the season; else
  the ``fact_snaps`` position of that game); never today's position. Only QB, RB, WR and TE are
  ranked. A **starter finish** is a rank at or above the position's weekly starter threshold
  (teams x dedicated starters, derived from config/league.yaml: ``League.starter_thresholds``);
  a **FLEX-worthy** finish (informative only) a rank at or above the position's FLEX-worthy rank
  (``League.flex_worthy_ranks``: RB and WR in the default league, none for TE).
- **Window.** The next ``window_games`` (3) regular-season weeks after N in which the player's
  as-of team (the pool's ``team``) has a game: a bye week of that team is skipped, which extends
  the window by a week. Capped at the season's last regular-season week (from ``dim_week``), so
  the last as-ofs have 2, 1 (or 0) games: ``window_games``, ``is_short_window``,
  ``train_eligible`` (at least ``min_train_games`` = 2). No rows for the last regular-season
  week itself (no window after it).
- **Played.** A window week in which the player has ``offense_snaps > 0`` or a stat line, for
  any team (a player traded mid-window keeps counting). A week he does not play (injured,
  inactive, benched) still uses up a window slot; only his as-of team's byes extend the window.
- **Pending.** Until every game of every window week (all teams: a weekly rank compares a player
  with everyone who played that week) has a final score and stat lines in the cache, the label
  is ``'pending'`` and the label columns are NULL: never guessed.

Point in time: labels are hindsight by design, so they read the warehouse directly (not through
an ``AsOfView``), but every outcome row (stat lines, snap counts) passes
:func:`twm.asof.outcomes_after` for its as-of first: only rows that became public STRICTLY after
the as-of can count, so a row can never be both a feature and a label. The window is also
restricted to weeks after N by week number, so a week-N game moved past the as-of (a split week)
belongs to week N, never to the window. tests/test_waiver_radar_labels.py proves both.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import duckdb
import polars as pl

from twm.asof import outcomes_after
from twm.config import FANTASY_POSITIONS, League, league
from twm.scoring import ScoringRules, score_sql

if TYPE_CHECKING:
    from twm.modules.waiver_radar.pool import Method, PoolRules

# PROJECT_SPEC 8.1: "weeks N+1..N+3 in which he plays" and "exclude weeks with <2 remaining games
# from training". Not league settings, so they live here, not in config/league.yaml.
WINDOW_GAMES = 3
MIN_TRAIN_GAMES = 2

# Fantasy points are rounded before ranking: a float sum's last bits depend on which stats a
# player had, and equal scores must tie (points are multiples of 0.01 with the default config).
POINTS_DECIMALS = 6

# weekly_finishes() output, in order.
FINISH_COLUMNS = (
    "season", "week", "gsis_id", "position", "position_source", "team", "game_id",
    "fantasy_points", "pos_rank", "n_ranked", "is_starter_finish", "is_flex_finish",
    "is_week_final", "available_at",
)  # fmt: skip

# Columns label_rows() adds to the pool rows, in order.
LABEL_COLUMNS = (
    "window_weeks", "window_games", "is_short_window", "train_eligible", "label_status",
    "n_window_played", "n_starter_finishes", "n_flex_finishes", "best_rank", "window_ranks",
    "window_points", "y_hit", "y_sustained",
)  # fmt: skip

# Label-detail columns that are NULL while a label is pending (window_* and flags are known).
_PENDING_NULL = (
    "n_window_played", "n_starter_finishes", "n_flex_finishes", "best_rank", "window_ranks",
    "window_points", "y_hit", "y_sustained",
)  # fmt: skip

LABEL_STATUSES = ("final", "pending")
POSITION_SOURCES = ("roster", "roster_earlier", "snaps", "none")
REQUIRED_POOL_COLUMNS = ("season", "week", "as_of", "gsis_id", "team")


@dataclass(frozen=True)
class LabelRules:
    """The label's knobs as plain values (thresholds derived from config/league.yaml).

    ``flex_ranks``: position -> FLEX-worthy rank, only for the positions that have one (default
    RB 36, WR 36); a position without one never has a FLEX-worthy finish. ``teams`` (the league
    size) is used only in generated text ("a 12-team league")."""

    starter_thresholds: Mapping[str, int]
    flex_ranks: Mapping[str, int] = field(default_factory=dict)
    window_games: int = WINDOW_GAMES
    min_train_games: int = MIN_TRAIN_GAMES
    scoring: ScoringRules = field(default_factory=ScoringRules.from_config)
    teams: int | None = None

    def __post_init__(self) -> None:
        missing = [p for p in FANTASY_POSITIONS if p not in self.starter_thresholds]
        if missing:
            raise ValueError(f"starter_thresholds lack positions {missing}")
        unknown = [p for p in self.flex_ranks if p not in FANTASY_POSITIONS]
        if unknown:
            raise ValueError(f"flex_ranks has positions outside {FANTASY_POSITIONS}: {unknown}")
        if any(int(n) < 1 for n in (*self.starter_thresholds.values(), *self.flex_ranks.values())):
            raise ValueError("starter thresholds and FLEX-worthy ranks must be at least 1")
        if self.window_games < 1 or not 0 <= self.min_train_games <= self.window_games:
            raise ValueError("need window_games >= 1 and 0 <= min_train_games <= window_games")

    @property
    def flex_positions(self) -> tuple[str, ...]:
        """The positions with a FLEX-worthy rank, in position order."""
        return tuple(p for p in FANTASY_POSITIONS if p in self.flex_ranks)

    @property
    def league_size(self) -> str:
        """ "12-team": the league size for generated text (config when ``teams`` is unset)."""
        return f"{self.teams if self.teams is not None else league().teams}-team"

    @classmethod
    def from_config(
        cls, lg: League | None = None, scoring: ScoringRules | None = None
    ) -> LabelRules:
        lg = lg or league()
        return cls(
            starter_thresholds=lg.starter_thresholds(),
            flex_ranks=lg.flex_worthy_ranks(),
            scoring=scoring or ScoringRules.from_config(),
            teams=lg.teams,
        )


# --------------------------------------------------------------------------------------
# Warehouse reads (hindsight on purpose: these are outcomes)
# --------------------------------------------------------------------------------------


def _connect(
    con_or_db: Path | str | duckdb.DuckDBPyConnection,
) -> tuple[duckdb.DuckDBPyConnection, bool]:
    if isinstance(con_or_db, duckdb.DuckDBPyConnection):
        return con_or_db, False
    from twm.warehouse.build import connect

    path = Path(con_or_db)
    if not path.exists():
        raise FileNotFoundError(f"warehouse not found: {path}")
    return connect(path, read_only=True), True


def _in(seasons: Sequence[int]) -> str:
    return ", ".join(str(int(s)) for s in seasons) or "NULL"


def week_status(
    con_or_db: Path | str | duckdb.DuckDBPyConnection, seasons: Sequence[int]
) -> pl.DataFrame:
    """One row per regular-season week: ``n_games``, ``n_final`` (games with a final score AND
    at least one stat line in the cache) and ``is_week_final`` (all of them). A week that is not
    final cannot be ranked for sure yet (its missing games could change every rank)."""
    con, close = _connect(con_or_db)
    try:
        df = con.execute(f"""
            SELECT g.season, g.week, count(*) AS n_games,
                   count(*) FILTER (WHERE g.result IS NOT NULL AND s.game_id IS NOT NULL)
                       AS n_final
            FROM fact_game g
            LEFT JOIN (SELECT DISTINCT game_id FROM fact_player_week
                       WHERE season_type = 'REG' AND season IN ({_in(seasons)})) s
              USING (game_id)
            WHERE g.season_type = 'REG' AND g.season IN ({_in(seasons)})
            GROUP BY 1, 2 ORDER BY 1, 2""").pl()
    finally:
        if close:
            con.close()
    return df.cast({"season": pl.Int32, "week": pl.Int32, "n_games": pl.Int32,
                    "n_final": pl.Int32}).with_columns(
        (pl.col("n_final") == pl.col("n_games")).alias("is_week_final")
    )  # fmt: skip


def _finishes_sql(seasons: Sequence[int], rules: LabelRules) -> str:
    s = _in(seasons)
    pts = score_sql(rules.scoring, table_alias="p")
    return f"""
    WITH pw AS (
        SELECT p.season, p.week, p.player_id AS gsis_id, p.game_id, p.team,
               round({pts}, {POINTS_DECIMALS}) AS fantasy_points, p.available_at
        FROM fact_player_week p
        WHERE p.season_type = 'REG' AND p.season IN ({s})
    ),
    ros AS (
        SELECT season, week, gsis_id, position FROM fact_roster_week
        WHERE season IN ({s}) AND gsis_id IS NOT NULL AND position IS NOT NULL
    ),
    same_week AS (
        SELECT pw.season, pw.week, pw.gsis_id, r.position
        FROM pw JOIN ros r USING (season, week, gsis_id)
    ),
    earlier AS (
        -- no roster row that week: his latest earlier roster week of the season
        SELECT pw.season, pw.week, pw.gsis_id, r.position
        FROM pw JOIN ros r
          ON r.season = pw.season AND r.gsis_id = pw.gsis_id AND r.week < pw.week
        QUALIFY row_number() OVER (PARTITION BY pw.season, pw.week, pw.gsis_id
                                   ORDER BY r.week DESC) = 1
    ),
    snap_pos AS (
        -- (game_id, gsis_id) is unique among snap rows with a gsis_id (checked by the build)
        SELECT game_id, gsis_id, min(position) AS position FROM fact_snaps
        WHERE season IN ({s}) AND gsis_id IS NOT NULL AND position IS NOT NULL
        GROUP BY 1, 2
    )
    SELECT pw.season, pw.week, pw.gsis_id,
           COALESCE(sw.position, e.position, sp.position) AS position,
           CASE WHEN sw.position IS NOT NULL THEN 'roster'
                WHEN e.position IS NOT NULL THEN 'roster_earlier'
                WHEN sp.position IS NOT NULL THEN 'snaps'
                ELSE 'none' END AS position_source,
           pw.team, pw.game_id, pw.fantasy_points, pw.available_at
    FROM pw
    LEFT JOIN same_week sw USING (season, week, gsis_id)
    LEFT JOIN earlier e USING (season, week, gsis_id)
    LEFT JOIN snap_pos sp ON sp.game_id = pw.game_id AND sp.gsis_id = pw.gsis_id"""


def weekly_finishes(
    con_or_db: Path | str | duckdb.DuckDBPyConnection,
    seasons: Sequence[int],
    *,
    rules: LabelRules | None = None,
) -> pl.DataFrame:
    """Every regular-season stat line of ``seasons`` with its weekly positional finish.

    One row per player-week with a ``fact_player_week`` row (``FINISH_COLUMNS``): the
    point-in-time ``position`` and where it came from (``position_source``: 'roster' that week,
    'roster_earlier' = his latest earlier roster week of the season, 'snaps' = that game's
    snap-count position, 'none'), ``fantasy_points`` (config/scoring.yaml), ``pos_rank`` among
    that week's players of the same position (rank 'min': ties share the better rank; NULL for
    positions other than QB/RB/WR/TE), ``n_ranked`` (players ranked there that week),
    ``is_starter_finish`` (``pos_rank`` <= the position's starter threshold),
    ``is_flex_finish`` (``pos_rank`` <= the position's FLEX-worthy rank; False for a position
    without one: QB and TE in the default league),
    ``is_week_final`` (every game of the week has a score and stat lines: see
    :func:`week_status`) and the row's ``available_at``. Reads the whole warehouse, future
    included: this is outcome data for labels, never a feature.
    """
    rules = rules or LabelRules.from_config()
    con, close = _connect(con_or_db)
    try:
        df = con.execute(_finishes_sql(seasons, rules)).pl()
        weeks = week_status(con, seasons)
    finally:
        if close:
            con.close()
    df = df.cast({"season": pl.Int32, "week": pl.Int32, "fantasy_points": pl.Float64})
    part = ["season", "week", "position"]
    ranked = pl.col("position").is_in(list(FANTASY_POSITIONS))
    threshold = pl.col("position").replace_strict(dict(rules.starter_thresholds), default=None)
    flex_rank = (
        pl.col("position").replace_strict(dict(rules.flex_ranks), default=None)
        if rules.flex_ranks
        else pl.lit(None, dtype=pl.Int64)
    )
    df = (
        df.with_columns(
            pl.when(ranked)
            .then(pl.col("fantasy_points").rank("min", descending=True).over(part))
            .cast(pl.Int32)
            .alias("pos_rank"),
            pl.when(ranked).then(pl.len().over(part)).cast(pl.Int32).alias("n_ranked"),
        )
        .with_columns(
            (pl.col("pos_rank") <= threshold).fill_null(False).alias("is_starter_finish"),
            (pl.col("pos_rank") <= flex_rank).fill_null(False).alias("is_flex_finish"),
        )
        .join(weeks.select("season", "week", "is_week_final"), on=["season", "week"], how="left")
        .with_columns(pl.col("is_week_final").fill_null(False))
    )
    return df.select(FINISH_COLUMNS).sort(
        "season", "week", "position", "pos_rank", "gsis_id", nulls_last=True
    )


def _snap_weeks(con: duckdb.DuckDBPyConnection, seasons: Sequence[int]) -> pl.DataFrame:
    """Regular-season snap rows with offensive snaps (a player who played, stats or not)."""
    return (
        con.execute(f"""
            SELECT season, week, gsis_id, available_at FROM fact_snaps
            WHERE game_type = 'REG' AND season IN ({_in(seasons)})
              AND gsis_id IS NOT NULL AND offense_snaps > 0""")
        .pl()
        .cast({"season": pl.Int32, "week": pl.Int32})
    )


def _team_weeks(con: duckdb.DuckDBPyConnection, seasons: Sequence[int]) -> pl.DataFrame:
    """(season, team, week) for every regular-season game of the final schedule."""
    return (
        con.execute(f"""
            SELECT season, home_team AS team, week FROM fact_game
            WHERE season_type = 'REG' AND season IN ({_in(seasons)})
            UNION
            SELECT season, away_team AS team, week FROM fact_game
            WHERE season_type = 'REG' AND season IN ({_in(seasons)})""")
        .pl()
        .cast({"season": pl.Int32, "week": pl.Int32})
    )


def last_reg_weeks(
    con_or_db: Path | str | duckdb.DuckDBPyConnection, seasons: Sequence[int]
) -> dict[int, int]:
    """Season -> its last regular-season week (``dim_week.is_last_reg_week``: 17 through 2020,
    18 from 2021 in the real data)."""
    con, close = _connect(con_or_db)
    try:
        rows = con.execute(
            f"SELECT season, week FROM dim_week WHERE is_last_reg_week "
            f"AND season IN ({_in(seasons)})"
        ).fetchall()
    finally:
        if close:
            con.close()
    return {int(s): int(w) for s, w in rows}


# --------------------------------------------------------------------------------------
# Labels
# --------------------------------------------------------------------------------------


def _windows(keys: pl.DataFrame, team_weeks: pl.DataFrame, n_games: int) -> pl.DataFrame:
    """Per (season, week N, team): the team's first ``n_games`` game weeks AFTER N, one row per
    window week ``w`` (a bye week has no game, so it is skipped and the window reaches one week
    further; the final schedule ends at the last regular-season week, so the window does too).
    A week-N game moved past the as-of (split week) is week N: never in the window."""
    return (
        keys.join(team_weeks.rename({"week": "w"}), on=["season", "team"], how="inner")
        .filter(pl.col("w") > pl.col("week"))
        .sort("season", "week", "team", "w")
        .group_by("season", "week", "team", maintain_order=True)
        .head(n_games)
    )


def _label_schema() -> dict[str, pl.DataType]:
    return {
        "window_weeks": pl.List(pl.Int32),
        "window_games": pl.Int32(),
        "is_short_window": pl.Boolean(),
        "train_eligible": pl.Boolean(),
        "label_status": pl.String(),
        "n_window_played": pl.Int32(),
        "n_starter_finishes": pl.Int32(),
        "n_flex_finishes": pl.Int32(),
        "best_rank": pl.Int32(),
        "window_ranks": pl.List(pl.Int32),
        "window_points": pl.List(pl.Float64),
        "y_hit": pl.Boolean(),
        "y_sustained": pl.Boolean(),
    }


def empty_labels(pool_rows: pl.DataFrame) -> pl.DataFrame:
    """``pool_rows`` with no rows and the label columns (typed)."""
    return pool_rows.head(0).with_columns(
        pl.lit(None, dtype=t).alias(c) for c, t in _label_schema().items()
    )


def label_rows(
    db: Path | str | duckdb.DuckDBPyConnection,
    pool_rows: pl.DataFrame,
    *,
    rules: LabelRules | None = None,
    finishes: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """The pool rows (C1 ``POOL_COLUMNS``, or any frame with ``REQUIRED_POOL_COLUMNS``) plus
    ``LABEL_COLUMNS``, in the input order. Rows of a season's last regular-season week (and any
    later week) are dropped: there is no window after it.

    ``finishes`` (optional) is a :func:`weekly_finishes` frame of the same seasons, to reuse it.
    See the module docstring for every definition.
    """
    missing = [c for c in REQUIRED_POOL_COLUMNS if c not in pool_rows.columns]
    if missing:
        raise KeyError(f"pool rows lack columns {missing}")
    clash = [c for c in (*LABEL_COLUMNS, "available_at") if c in pool_rows.columns]
    if clash:
        raise ValueError(f"pool rows already have columns {clash}")
    if pool_rows.get_column("as_of").null_count():
        raise ValueError("pool rows with a NULL as_of cannot be labelled")
    rules = rules or LabelRules.from_config()
    seasons = sorted(int(s) for s in pool_rows.get_column("season").unique().drop_nulls())
    if not seasons:
        return empty_labels(pool_rows)
    con, close = _connect(db)
    try:
        last = last_reg_weeks(con, seasons)
        team_weeks = _team_weeks(con, seasons)
        weeks = week_status(con, seasons)
        snaps = _snap_weeks(con, seasons)
        fin = weekly_finishes(con, seasons, rules=rules) if finishes is None else finishes
    finally:
        if close:
            con.close()
    no_last = [s for s in seasons if s not in last]
    if no_last:
        raise LookupError(f"no last regular-season week in dim_week for seasons {no_last}")

    last_df = pl.DataFrame(
        {"season": list(last), "_last_reg": list(last.values())},
        schema={"season": pl.Int32, "_last_reg": pl.Int32},
    )
    rows = (
        pool_rows.with_row_index("_row")
        .with_columns(pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32))
        .join(last_df, on="season", how="left")
        .filter(pl.col("week") < pl.col("_last_reg"))
        .drop("_last_reg")
    )
    if rows.height == 0:
        return empty_labels(pool_rows)

    keys = rows.select("season", "week", "team").unique()
    windows = (
        _windows(keys, team_weeks, rules.window_games)
        .join(
            weeks.select("season", pl.col("week").alias("w"), "is_week_final"),
            on=["season", "w"],
            how="left",
        )
        .with_columns(pl.col("is_week_final").fill_null(False))
    )
    slots = rows.select("_row", "season", "week", "as_of", "gsis_id", "team").join(
        windows, on=["season", "week", "team"], how="inner"
    )

    # outcomes: stat lines (with their weekly finish) and snap rows, each filtered to rows that
    # became public strictly after the slot's as-of BEFORE they are joined (spec 6.2 rule 3)
    stats = fin.select(
        "season", pl.col("week").alias("w"), "gsis_id", "pos_rank", "fantasy_points",
        "is_starter_finish", "is_flex_finish", "available_at",
    ).with_columns(pl.lit(True).alias("_has_stats"))  # fmt: skip
    snap = snaps.select("season", pl.col("week").alias("w"), "gsis_id", "available_at")
    stats_by = stats.partition_by("season", as_dict=True)
    snap_by = snap.partition_by("season", as_dict=True)

    def joined(grp: pl.DataFrame, st: pl.DataFrame, sn: pl.DataFrame) -> pl.DataFrame:
        sn = sn.select("season", "w", "gsis_id").unique()
        return grp.join(st.drop("available_at"), on=["season", "w", "gsis_id"], how="left").join(
            sn.with_columns(pl.lit(True).alias("_has_snaps")),
            on=["season", "w", "gsis_id"],
            how="left",
        )

    parts = [joined(slots.head(0), stats.head(0), snap.head(0))]  # the columns, even if empty
    for (season, _week, as_of), grp in slots.partition_by(
        ["season", "week", "as_of"], as_dict=True, maintain_order=True
    ).items():
        st = outcomes_after(stats_by.get((season,), stats.head(0)), as_of)
        sn = outcomes_after(snap_by.get((season,), snap.head(0)), as_of)
        parts.append(joined(grp, st, sn))
    slot_out = pl.concat(parts, how="vertical")
    has_stats = pl.col("_has_stats").fill_null(False)
    has_snaps = pl.col("_has_snaps").fill_null(False)
    played = has_stats | has_snaps
    agg = (
        slot_out.sort("_row", "w")
        .group_by("_row", maintain_order=True)
        .agg(
            pl.col("w").alias("window_weeks"),
            pl.len().cast(pl.Int32).alias("window_games"),
            pl.col("is_week_final").all().alias("_final"),
            played.sum().cast(pl.Int32).alias("n_window_played"),
            pl.col("is_starter_finish").fill_null(False).sum().cast(pl.Int32)
            .alias("n_starter_finishes"),
            pl.col("is_flex_finish").fill_null(False).sum().cast(pl.Int32)
            .alias("n_flex_finishes"),
            pl.col("pos_rank").min().cast(pl.Int32).alias("best_rank"),
            pl.col("pos_rank").cast(pl.Int32).alias("window_ranks"),
            # a week he played without a stat line (snaps only) scored 0; NULL = did not play
            pl.when(has_stats)
            .then(pl.col("fantasy_points"))
            .when(has_snaps)
            .then(pl.lit(0.0))
            .alias("window_points"),
        )
    )  # fmt: skip
    out = rows.join(agg, on="_row", how="left").sort("_row")
    # a team without any game left in the window (never seen in the real data): 0 games, final
    out = out.with_columns(
        pl.col("window_weeks").fill_null(pl.lit([], dtype=pl.List(pl.Int32))),
        pl.col("window_games").fill_null(0),
        pl.col("_final").fill_null(True),
        pl.col("n_window_played").fill_null(0),
        pl.col("n_starter_finishes").fill_null(0),
        pl.col("n_flex_finishes").fill_null(0),
        pl.col("window_ranks").fill_null(pl.lit([], dtype=pl.List(pl.Int32))),
        pl.col("window_points").fill_null(pl.lit([], dtype=pl.List(pl.Float64))),
    )
    pending = ~pl.col("_final")
    out = out.with_columns(
        (pl.col("window_games") < rules.window_games).alias("is_short_window"),
        (pl.col("window_games") >= rules.min_train_games).alias("train_eligible"),
        pl.when(pending).then(pl.lit("pending")).otherwise(pl.lit("final")).alias("label_status"),
        (pl.col("n_starter_finishes") >= 1).alias("y_hit"),
        (pl.col("n_starter_finishes") >= 2).alias("y_sustained"),
    ).with_columns(
        pl.when(pending).then(pl.lit(None)).otherwise(pl.col(c)).alias(c) for c in _PENDING_NULL
    )
    schema = _label_schema()
    return out.select(
        *[pl.col(c) for c in pool_rows.columns],
        *[pl.col(c).cast(schema[c]) for c in LABEL_COLUMNS],
    ).with_columns(pl.col("season").cast(pool_rows.schema["season"]),
                   pl.col("week").cast(pool_rows.schema["week"]))  # fmt: skip


def labels_history(
    db: Path | str,
    seasons: Sequence[int],
    weeks: Sequence[int] | None = None,
    *,
    methods: Sequence[Method] = ("auto",),
    pool_rules: PoolRules | None = None,
    rules: LabelRules | None = None,
) -> pl.DataFrame:
    """:func:`~twm.modules.waiver_radar.pool.pool_history` of ``seasons`` with the labels: every
    universe row (``in_pool`` True or False) of every labelled as-of (weeks 1 to the last
    regular-season week minus 1 whose games are in the cache)."""
    from twm.modules.waiver_radar.pool import pool_history

    hist = pool_history(db, seasons, weeks, methods=methods, rules=pool_rules)
    return label_rows(db, hist, rules=rules)
