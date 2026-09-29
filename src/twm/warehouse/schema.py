"""Warehouse table specifications: names, sources, primary keys, columns and target types.

This module is *declarative*. It says, for every table, which nflverse dataset feeds it, which
columns are kept, what DuckDB type each column must have (so a build from seasons 1999-2026 and
a build from 2025-2026 produce identical types even though the Parquet files drift between
Int32 and Float64), and what the primary key is. ``build.py`` turns these specs into SQL.

Column names are never invented here: "all columns" tables take their names and types from the
committed snapshots in ``data/schemas/<dataset>.json`` (the contract with upstream), and the
hand-picked lists (``fact_play``, ``dim_player``) are checked against the snapshot at import
time so a typo fails loudly.

Team abbreviations: play-by-play, player stats and team stats already use the current
abbreviations for relocated franchises (LA, LAC, LV) back to 1999, but schedules, snap counts,
injuries and legacy depth charts use the abbreviation of the day (STL, SD, OAK, LAR). Every team
column in the warehouse is normalized with :data:`TEAM_ALIASES` so joins across tables work in
every season; the original value is kept in a ``*_raw`` column where it differed.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import cache

from twm import situations
from twm.sources import nflverse as nv

# Historical abbreviation -> abbreviation used today (dim_team.current_abbr).
TEAM_ALIASES: dict[str, str] = {"OAK": "LV", "SD": "LAC", "STL": "LA", "LAR": "LA"}
# The weekly rosters of 2002-2015 spell five teams with the NFL's own codes. Each mapping was
# checked on the data (C1): the same players in the same weeks carry the right-hand code in
# player_stats for thousands of player-weeks (tests/test_waiver_radar_pool.py, realdata).
ROSTER_TEAM_ALIASES: dict[str, str] = {
    "ARZ": "ARI", "BLT": "BAL", "CLV": "CLE", "HST": "HOU", "SL": "LA",
}  # fmt: skip

POLARS_TO_DUCKDB: dict[str, str] = {
    "Int8": "INTEGER",
    "Int16": "INTEGER",
    "Int32": "INTEGER",
    "Int64": "BIGINT",
    "UInt8": "INTEGER",
    "UInt16": "INTEGER",
    "UInt32": "BIGINT",
    "UInt64": "BIGINT",
    "Float32": "DOUBLE",
    "Float64": "DOUBLE",
    "String": "VARCHAR",
    "Utf8": "VARCHAR",
    "Boolean": "BOOLEAN",
    "Date": "DATE",
}


def duckdb_type_of(polars_dtype: str) -> str:
    if polars_dtype.startswith("Datetime"):
        return "TIMESTAMP"
    try:
        return POLARS_TO_DUCKDB[polars_dtype]
    except KeyError as e:  # pragma: no cover - only on a new upstream dtype
        raise ValueError(f"no DuckDB type mapping for polars dtype {polars_dtype!r}") from e


# --------------------------------------------------------------------------------------
# SQL expression helpers (all pure string builders)
# --------------------------------------------------------------------------------------


def q(name: str) -> str:
    """Quote an identifier (``desc`` and ``pass`` are SQL keywords)."""
    return '"' + name.replace('"', '""') + '"'


def sql_str(text: str) -> str:
    """A SQL string literal (single quotes doubled), for file paths and comments."""
    return "'" + text.replace("'", "''") + "'"


def normalize_team_sql(expr: str, extra: Mapping[str, str] | None = None) -> str:
    """Empty string -> NULL, then historical abbreviations -> current ones (``extra``: more
    source-specific spellings, e.g. :data:`ROSTER_TEAM_ALIASES`)."""
    inner = f"NULLIF({expr}, '')"
    aliases = {**TEAM_ALIASES, **(extra or {})}
    whens = " ".join(f"WHEN '{old}' THEN '{new}'" for old, new in aliases.items())
    return f"(CASE {inner} {whens} ELSE {inner} END)"


def normalize_roster_team_sql(expr: str) -> str:
    return normalize_team_sql(expr, ROSTER_TEAM_ALIASES)


def season_type_sql(game_type_expr: str) -> str:
    """REG for game_type 'REG', POST for anything else (WC/DIV/CON/SB/SBBYE)."""
    return f"(CASE WHEN {game_type_expr} = 'REG' THEN 'REG' ELSE 'POST' END)"


FLOAT_TYPES = ("DOUBLE", "FLOAT", "REAL")
INTEGER_TYPES = ("INTEGER", "BIGINT")


def cast_sql(src: str, target: str, source_type: str, *, alias: str = "") -> str:
    """CAST a source column to the target type in a way that is time-zone independent.

    A TIMESTAMP WITH TIME ZONE source is converted with ``AT TIME ZONE 'UTC'`` so the stored
    naive TIMESTAMP holds UTC regardless of the session's TimeZone setting. Text timestamps
    (daily depth-chart ``dt`` like '2026-09-26T12:12:29Z') are parsed as UTC. A floating-point
    source cast to an integer type fails the build on a non-integral value (DuckDB would
    otherwise round silently, half to even); NaN and infinity fail in the CAST itself. Every
    such column was verified integral on the 1999-2026 cache, so this guard only ever fires on
    an upstream change. ``alias`` qualifies the column (``d."dt"``) for joins.
    """
    col = f"{alias}.{q(src)}" if alias else q(src)
    st = source_type.upper()
    if target == "TIMESTAMP":
        if st.startswith("TIMESTAMP WITH TIME ZONE"):
            return f"CAST({col} AT TIME ZONE 'UTC' AS TIMESTAMP)"
        if st.startswith("TIMESTAMP"):
            return f"CAST({col} AS TIMESTAMP)"
        if st == "VARCHAR":
            return f"CAST(strptime(NULLIF({col}, ''), '%Y-%m-%dT%H:%M:%SZ') AS TIMESTAMP)"
        return f"CAST({col} AS TIMESTAMP)"
    if target == "DATE" and st == "VARCHAR":
        return f"CAST(NULLIF({col}, '') AS DATE)"
    if st == target:
        return col
    if st in FLOAT_TYPES and target in INTEGER_TYPES:
        # error() is not constant-folded inside CASE (checked on duckdb 1.5.5), so it fires
        # only for rows that reach the ELSE branch. NaN = floor(NaN) in DuckDB, so NaN and
        # +/-inf fall through to the CAST, which raises a ConversionException.
        msg = sql_str(f"non-integral value in {src}: ")
        return (
            f"CAST(CASE WHEN {col} IS NULL OR {col} = floor({col}) THEN {col} "
            f"ELSE error({msg} || CAST({col} AS VARCHAR)) END AS {target})"
        )
    return f"CAST({col} AS {target})"


# --------------------------------------------------------------------------------------
# Specs
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Column:
    """One warehouse column.

    name: name in the warehouse. type: DuckDB type. source: source column (defaults to name).
    computed: True for columns the builder computes in Python (not read from the source view).
    transform: optional function applied to the cast source expression (e.g. team
    normalization). derived: SQL computed from OTHER source columns of the same row (e.g. the
    garbage-time flag from wp, clock and score); ``requires`` lists those columns. doc:
    plain-language meaning.
    """

    name: str
    type: str
    source: str | None = None
    transform: Callable[[str], str] | None = None
    computed: bool = False
    doc: str = ""
    derived: Callable[[], str] | None = None
    requires: tuple[str, ...] = ()

    def sql(self, available: Mapping[str, str]) -> str:
        """The SELECT expression for this column given the source view's column types."""
        if self.derived is not None:
            missing = [c for c in self.requires if c not in available]
            if missing:
                raise KeyError(f"{self.name} needs source columns {missing}")
            return f"CAST({self.derived()} AS {self.type}) AS {q(self.name)}"
        src = self.source or self.name
        if src not in available:
            expr = f"CAST(NULL AS {self.type})"
        else:
            expr = cast_sql(src, self.type, available[src])
            if self.transform is not None:
                expr = f"CAST({self.transform(expr)} AS {self.type})"
        return f"{expr} AS {q(self.name)}"


@dataclass(frozen=True)
class Table:
    """One warehouse table.

    source: nflverse dataset name (``None`` when derived from other tables or built in Python).
    primary_key: columns that identify a row; uniqueness is asserted after every build
    (NULL-safe, so a nullable key column such as legacy depth-chart ``week`` is allowed).
    not_null_key: key columns that must be non-null; rows violating this are dropped and
    counted in the manifest (e.g. player_stats rows with a NULL player_id).
    dedupe_order: when set, exact duplicates on the primary key are resolved by keeping the
    first row in this ORDER BY (e.g. latest ``date_modified``); dropped rows are counted.
    distinct: drop exact duplicate rows before anything else (legacy depth charts).
    """

    name: str
    doc: str
    primary_key: tuple[str, ...]
    columns: tuple[Column, ...]
    source: str | None = None
    not_null_key: tuple[str, ...] | None = None
    dedupe_order: tuple[str, ...] = ()
    distinct: bool = False

    def __post_init__(self) -> None:
        names = self.column_names
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:  # DuckDB would silently rename the second one to <name>_1
            raise ValueError(f"{self.name}: duplicate column names {dupes}")

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    @property
    def required_not_null(self) -> tuple[str, ...]:
        return self.primary_key if self.not_null_key is None else self.not_null_key


@cache
def snapshot_columns(dataset: str) -> dict[str, str]:
    """Column -> polars dtype from the committed snapshot ``data/schemas/<dataset>.json``."""
    path = nv.snapshot_path(dataset)
    return dict(json.loads(path.read_text())["columns"])


def columns_from_snapshot(
    dataset: str,
    *,
    types: Mapping[str, str] | None = None,
    transforms: Mapping[str, Callable[[str], str]] | None = None,
    exclude: tuple[str, ...] = (),
) -> list[Column]:
    """All snapshot columns of a dataset, in snapshot order, with optional type overrides."""
    types = dict(types or {})
    transforms = dict(transforms or {})
    out = []
    for name, dtype in snapshot_columns(dataset).items():
        if name in exclude:
            continue
        out.append(
            Column(name, types.get(name, duckdb_type_of(dtype)), transform=transforms.get(name))
        )
    return out


def _check_names(dataset: str, names: list[str]) -> None:
    snap = snapshot_columns(dataset)
    missing = [n for n in names if n not in snap]
    if missing:
        raise ValueError(f"{dataset}: columns not in data/schemas/{dataset}.json: {missing}")


def _raw_team(name: str) -> Column:
    return Column(f"{name}_raw", "VARCHAR", source=name, doc=f"{name} as spelled in the source")


# ---- fact_game ------------------------------------------------------------------------

FACT_GAME_EXTRA = [
    Column("season_type", "VARCHAR", computed=True, doc="REG or POST (from game_type)"),
    _raw_team("home_team"),
    _raw_team("away_team"),
    Column("kickoff_utc", "TIMESTAMP", computed=True, doc="scheduled kickoff, UTC (naive)"),
    Column(
        "kickoff_is_estimated",
        "BOOLEAN",
        computed=True,
        doc=(
            "gametime missing (all of 1999) or the '09:00' placeholder of 2000-2005 prime-time "
            "rows: a weekday default was used, so kickoff_utc and game_end_utc_est are guesses"
        ),
    ),
    Column("game_end_utc_est", "TIMESTAMP", computed=True, doc="kickoff_utc + 4 h (estimate)"),
    Column("home_implied_total", "DOUBLE", computed=True, doc="(total_line + spread_line) / 2"),
    Column("away_implied_total", "DOUBLE", computed=True, doc="(total_line - spread_line) / 2"),
    Column(
        "availability_game_end_utc",
        "TIMESTAMP",
        computed=True,
        doc=(
            "the game end used by the available_at rules (B2): game_end_utc_est, except for "
            "kickoff_is_estimated games, where the kickoff is assumed to be the latest normal "
            "night slot for that weekday (config availability.estimated_kickoff_for_availability"
            "_et: 21:00 ET on Mondays, 20:30 ET otherwise) + 4 h, so the estimate errs late; "
            "never earlier than game_end_utc_est. The row itself (final score) is available "
            "game_result_lag_hours after this"
        ),
    ),
]


def fact_game_spec() -> Table:
    cols = columns_from_snapshot(
        "schedules",
        types={"gameday": "DATE"},
        transforms={"home_team": normalize_team_sql, "away_team": normalize_team_sql},
    )
    return Table(
        name="fact_game",
        source="schedules",
        primary_key=("game_id",),
        columns=tuple(cols + FACT_GAME_EXTRA),
        doc=(
            "One row per scheduled game (regular season and playoffs), including games not yet "
            "played (result/total NULL). All schedule columns plus kickoff_utc (Eastern gameday + "
            "gametime converted to UTC, DST-aware), game_end_utc_est (kickoff + 4 h) and the "
            "closing-line implied team totals. Team columns use current abbreviations; "
            "home_team_raw/away_team_raw keep the original spelling (e.g. SD, STL, OAK)."
        ),
    )


# ---- fact_schedule ----------------------------------------------------------------------

# Only the columns that are public when the fixture list is published: who plays whom, where
# and when. Deliberately NOT here: scores/result/total/overtime (known after the game), betting
# lines/odds/moneylines (closing lines are set just before kickoff), coaches (a future game's
# listed coach can reveal a firing), referee, starting QBs, temp/wind, and roof (open/closed is
# decided on game day for retractable roofs, so it hints at the weather). The values are
# nflverse's FINAL schedule: the date/time/venue ("slot") columns are masked in the as-of view
# until slot_available_at, and the curated availability.schedule_exceptions (moved or relocated
# games) wait for their announcement; other later changes cannot be detected (docs/warehouse.md).
FACT_SCHEDULE_COLUMNS = (
    "game_id", "season", "week", "game_type", "season_type", "gameday", "weekday", "gametime",
    "kickoff_utc", "kickoff_is_estimated", "home_team", "away_team", "home_team_raw",
    "away_team_raw", "location", "div_game", "surface", "stadium_id", "stadium", "away_rest",
    "home_rest",
)  # fmt: skip


def fact_schedule_spec() -> Table:
    game_cols = {c.name: c for c in fact_game_spec().columns}
    missing = [n for n in FACT_SCHEDULE_COLUMNS if n not in game_cols]
    if missing:
        raise ValueError(f"fact_schedule: columns not in fact_game: {missing}")
    cols = tuple(Column(n, game_cols[n].type, doc=game_cols[n].doc) for n in FACT_SCHEDULE_COLUMNS)
    return Table(
        name="fact_schedule",
        primary_key=("game_id",),
        columns=cols,
        doc=(
            "One row per scheduled game with ONLY what is public once the schedule is announced "
            "(teams, week, date/time, venue, rest days): no scores, no betting lines, no coaches, "
            "no weather. Use it for upcoming opponents, byes and games remaining. Regular-season "
            "rows are available from the spring schedule release; playoff rows once the previous "
            "round is over (see available_at); the date/time/venue columns only from "
            "slot_available_at. It is nflverse's FINAL schedule: games postponed, flexed or "
            "moved after the release show their final week/slot (listed changes in "
            "availability.schedule_exceptions wait for their announcement; the cancelled 2022 "
            "W17 BUF-CIN game is absent)."
        ),
    )


# ---- fact_play ------------------------------------------------------------------------

FACT_PLAY_COLUMNS: list[str] = """
game_id play_id season week season_type game_date posteam defteam home_team away_team home_coach
away_coach
qtr down ydstogo yardline_100 goal_to_go game_seconds_remaining half_seconds_remaining
quarter_seconds_remaining game_half score_differential posteam_score defteam_score
posteam_timeouts_remaining defteam_timeouts_remaining drive fixed_drive fixed_drive_result series
series_success
play_type desc pass rush special qb_kneel qb_spike qb_dropback qb_scramble shotgun no_huddle
pass_length pass_location run_location run_gap air_yards yards_after_catch yards_gained
complete_pass incomplete_pass interception sack touchdown pass_touchdown rush_touchdown
return_touchdown fumble fumble_lost first_down penalty penalty_team penalty_yards aborted_play
play_deleted timeout timeout_team two_point_attempt two_point_conv_result extra_point_attempt
extra_point_result field_goal_attempt field_goal_result kick_distance punt_attempt kickoff_attempt
fourth_down_converted fourth_down_failed third_down_converted third_down_failed
passer_player_id passer_player_name receiver_player_id receiver_player_name rusher_player_id
rusher_player_name kicker_player_id punter_player_id
ep epa wp def_wp home_wp away_wp wpa vegas_wp vegas_home_wp vegas_wpa xpass pass_oe cpoe xyac_epa
xyac_mean_yardage xyac_median_yardage xyac_success xyac_fd success air_epa yac_epa comp_air_epa
comp_yac_epa qb_epa
spread_line total_line roof surface temp wind stadium weather div_game location start_time
time_of_day home_score away_score total result
""".split()  # noqa: SIM905

# D1 (Regression Watch): who gets a play's yards, touchdowns and lost fumbles, so fantasy points
# can be computed per play (twm.modules.regression_watch.plays). Appended after the columns
# above so the older columns keep their positions. Checked against data/schemas/pbp.json at
# import like the rest; every yard column is integral in 1999-2026 (checked on the cache).
FACT_PLAY_ATTRIBUTION_DOCS: dict[str, str] = {
    "pass_attempt": "1 on a pass attempt (sacks and two-point passes included)",
    "rush_attempt": "1 on a rush attempt (kneels and scrambles included)",
    "passing_yards": "yards credited to the passer on a completion (NULL otherwise); includes "
    "yards gained after a lateral by the receiver",
    "receiving_yards": "yards credited to the receiver (NULL on an incompletion); a lateral's "
    "yards after it go to lateral_receiving_yards",
    "rushing_yards": "yards credited to the rusher (kneels included); a lateral's yards after it "
    "go to lateral_rushing_yards",
    "lateral_reception": "1 when the receiver lateralled the ball after the catch",
    "lateral_rush": "1 when the rusher lateralled the ball",
    "lateral_receiver_player_id": "player who received a lateral after a catch (gsis_id)",
    "lateral_receiving_yards": "receiving yards credited to lateral_receiver_player_id",
    "lateral_rusher_player_id": "player who received a lateral on a run (gsis_id)",
    "lateral_rushing_yards": "rushing yards credited to lateral_rusher_player_id",
    "td_team": "team that scored a touchdown on the play (current abbreviation)",
    "td_player_id": "player who scored the play's touchdown (gsis_id)",
    "fumbled_1_team": "team of the first player who fumbled (current abbreviation)",
    "fumbled_1_player_id": "first player who fumbled (gsis_id)",
    "fumbled_2_team": "team of the second player who fumbled on the same play",
    "fumbled_2_player_id": "second player who fumbled on the same play (gsis_id)",
    "fumble_recovery_1_team": "team that recovered the first fumble",
    "fumble_recovery_1_player_id": "player who recovered the first fumble (gsis_id)",
    "fumble_recovery_2_team": "team that recovered the second fumble",
    "fumble_recovery_2_player_id": "player who recovered the second fumble (gsis_id)",
    "kickoff_returner_player_id": "kickoff returner (gsis_id)",
    "punt_returner_player_id": "punt returner (gsis_id)",
    "lateral_kickoff_returner_player_id": "player who received a lateral on a kickoff return",
    "lateral_punt_returner_player_id": "player who received a lateral on a punt return",
}
FACT_PLAY_COLUMNS += list(FACT_PLAY_ATTRIBUTION_DOCS)

# Columns that are counts, yard lines, seconds or 0/1 flags but arrive as DOUBLE in some seasons.
FACT_PLAY_INTEGER: set[str] = set(
    """
play_id season week qtr down ydstogo yardline_100 goal_to_go game_seconds_remaining
half_seconds_remaining quarter_seconds_remaining score_differential posteam_score defteam_score
posteam_timeouts_remaining defteam_timeouts_remaining drive fixed_drive series series_success
pass rush special qb_kneel qb_spike qb_dropback qb_scramble shotgun no_huddle air_yards
yards_after_catch yards_gained complete_pass incomplete_pass interception sack touchdown
pass_touchdown rush_touchdown return_touchdown fumble fumble_lost first_down penalty penalty_yards
aborted_play play_deleted timeout two_point_attempt extra_point_attempt field_goal_attempt
kick_distance punt_attempt kickoff_attempt fourth_down_converted fourth_down_failed
third_down_converted third_down_failed xyac_median_yardage success temp wind div_game home_score
away_score total result
pass_attempt rush_attempt passing_yards receiving_yards rushing_yards lateral_reception
lateral_rush lateral_receiving_yards lateral_rushing_yards
""".split()  # noqa: SIM905
)
FACT_PLAY_TEAM_COLUMNS = (
    "posteam", "defteam", "home_team", "away_team", "penalty_team", "timeout_team", "td_team",
    "fumbled_1_team", "fumbled_2_team", "fumble_recovery_1_team", "fumble_recovery_2_team",
)  # fmt: skip


def fact_play_spec() -> Table:
    _check_names("pbp", FACT_PLAY_COLUMNS)
    snap = snapshot_columns("pbp")
    cols = []
    for name in FACT_PLAY_COLUMNS:
        if name in FACT_PLAY_INTEGER:
            typ = "INTEGER"
        elif name == "game_date":
            typ = "DATE"
        else:
            typ = duckdb_type_of(snap[name])
        transform = normalize_team_sql if name in FACT_PLAY_TEAM_COLUMNS else None
        doc = FACT_PLAY_ATTRIBUTION_DOCS.get(name, "")
        cols.append(Column(name, typ, transform=transform, doc=doc))
    rules = situations.SituationRules.from_config()
    cols += [
        Column(
            "is_garbage_time",
            "BOOLEAN",
            derived=lambda: situations.garbage_time_sql(rules),
            requires=situations.INPUT_COLUMNS,
            doc="Garbage time (config garbage_time, PROJECT_SPEC 7.3): "
            + rules.describe_garbage_time()
            + ". Never NULL; FALSE when the play has no win probability.",
        ),
        Column(
            "is_neutral",
            "BOOLEAN",
            derived=lambda: situations.neutral_sql(rules),
            requires=situations.INPUT_COLUMNS,
            doc="Neutral situation (config neutral, PROJECT_SPEC 7.3): "
            + rules.describe_neutral()
            + ". Never NULL; never TRUE together with is_garbage_time.",
        ),
    ]
    return Table(
        name="fact_play",
        source="pbp",
        primary_key=("game_id", "play_id"),
        columns=tuple(cols),
        doc=(
            "One row per play from nflfastR play-by-play: identifiers, game state at the snap, "
            "play descriptors, the main players involved, nflverse model columns (EPA, WP, CPOE, "
            "xYAC; Section 6.3 caveat) and game context. Counts and 0/1 flags are INTEGER, model "
            "outputs DOUBLE. Empty-string team codes (1999-2000) are stored as NULL."
        ),
    )


# ---- weekly stats, snaps, injuries -----------------------------------------------------


def fact_player_week_spec() -> Table:
    cols = columns_from_snapshot(
        "player_stats",
        transforms={"team": normalize_team_sql, "opponent_team": normalize_team_sql},
    )
    return Table(
        name="fact_player_week",
        source="player_stats",
        primary_key=("player_id", "season", "week", "season_type"),
        columns=tuple(cols),
        doc=(
            "One row per player per game week (regular season and playoffs) with every nflverse "
            "weekly stat column. Rows with a NULL player_id (one placeholder row per week in "
            "the source, 2001-2026) are dropped and counted in the manifest."
        ),
    )


def fact_team_week_spec() -> Table:
    cols = columns_from_snapshot(
        "team_stats",
        transforms={"team": normalize_team_sql, "opponent_team": normalize_team_sql},
    )
    return Table(
        name="fact_team_week",
        source="team_stats",
        primary_key=("team", "season", "week", "season_type"),
        columns=tuple(cols),
        doc="One row per team per game week with every nflverse weekly team stat column.",
    )


FACT_SNAPS_GSIS = Column(
    "gsis_id",
    "VARCHAR",
    computed=True,
    doc=(
        "the player's gsis_id through bridge_player_id (id_type 'pfr'); NULL when the PFR id "
        "maps to no player, or when the usage check found the link implausible for this season "
        "(both listed in report_id_unmatched). Unique per game_id among non-NULL values "
        "(build_manifest notes.n_duplicate_game_gsis), so joins on (game_id, gsis_id) are safe"
    ),
)


def fact_snaps_spec() -> Table:
    cols = columns_from_snapshot(
        "snap_counts", transforms={"team": normalize_team_sql, "opponent": normalize_team_sql}
    )
    at = [c.name for c in cols].index("pfr_player_id") + 1
    cols = [*cols[:at], FACT_SNAPS_GSIS, *cols[at:], _raw_team("team"), _raw_team("opponent")]
    return Table(
        name="fact_snaps",
        source="snap_counts",
        primary_key=("game_id", "pfr_player_id"),
        columns=tuple(cols),
        doc=(
            "One row per player per game with offensive, defensive and special-teams snap counts "
            "and percentages. The source identifies players by Pro-Football-Reference id "
            "(pfr_player_id); gsis_id is mapped through bridge_player_id (B3)."
        ),
    )


def fact_injury_report_spec() -> Table:
    # date_modified is absent from the 2025+ snapshot but present upstream 2009-2024 (usable
    # 2010-2024; 2009 carries one junk stamp); it is excluded here and appended explicitly below
    # so a refreshed snapshot cannot add a second copy (DuckDB would silently rename it
    # date_modified_1).
    cols = columns_from_snapshot(
        "injuries",
        types={"season": "INTEGER", "week": "INTEGER"},
        transforms={"team": normalize_team_sql},
        exclude=("date_modified",),
    )
    cols = [
        Column("season_type", "VARCHAR", source="game_type", transform=season_type_sql)
        if c.name == "season_type"
        else c
        for c in cols
    ]
    cols += [
        _raw_team("team"),
        Column(
            "date_modified",
            "TIMESTAMP",
            doc=(
                "last-modified time of the report row (UTC); populated 2010-2024 (62 nulls in "
                "2010); 2009 is NULL except 17 rows with a junk 2010-01-01 stamp (ignore, see "
                "assumptions section 4); absent upstream 2025+ so NULL"
            ),
        ),
    ]
    return Table(
        name="fact_injury_report",
        source="injuries",
        primary_key=("season", "week", "team", "gsis_id"),
        columns=tuple(cols),
        dedupe_order=("date_modified DESC NULLS LAST",),
        doc=(
            "One row per player per team-week on the official injury report: game status "
            "(Out/Doubtful/Questionable) and practice participation. season_type is derived from "
            "game_type. When a player appears twice in a week (2024 has two such pairs), the row "
            "with the latest date_modified wins; ties are broken on the remaining columns."
        ),
    )


# ---- depth charts (two source formats, one table) ---------------------------------------

DEPTH_CHART_COLUMNS = (
    Column("season", "INTEGER", doc="season of the file the row came from"),
    Column("source_format", "VARCHAR", doc="'legacy' (2001-2024, weekly) or 'daily' (2025+)"),
    Column("week", "INTEGER", doc="legacy: chart week (NULL for SBBYE rows); daily: NULL"),
    Column("week_at_dt", "INTEGER", doc="daily: dim_week week whose window contains dt"),
    Column("game_type", "VARCHAR", doc="legacy: REG/WC/DIV/CON/SB/SBBYE; daily: NULL"),
    Column("season_type", "VARCHAR", doc="REG or POST from game_type; daily: NULL"),
    Column("dt", "TIMESTAMP", doc="daily: snapshot time (UTC) per team; legacy: NULL"),
    Column("team", "VARCHAR", doc="current abbreviation"),
    Column("team_raw", "VARCHAR", doc="team as spelled in the source"),
    Column(
        "gsis_id",
        "VARCHAR",
        doc=(
            "player id (NULL for empty or unmatched daily slots); a daily row whose source has "
            "no gsis_id gets it from its espn_id through bridge_player_id (gsis_id_from_espn)"
        ),
    ),
    Column("espn_id", "VARCHAR", doc="daily only (canonical id text)"),
    Column(
        "gsis_id_from_espn",
        "BOOLEAN",
        doc="daily rows: gsis_id was empty upstream and was filled from espn_id (B3)",
    ),
    Column("player_name", "VARCHAR"),
    Column("player_position", "VARCHAR", doc="legacy roster position (daily: NULL)"),
    Column("unit", "VARCHAR", doc="offense / defense / special_teams / other"),
    Column("unit_raw", "VARCHAR", doc="legacy formation or daily pos_grp"),
    Column("position", "VARCHAR", doc="slot position: legacy depth_position or daily pos_abb"),
    Column("pos_slot", "INTEGER", doc="daily slot number within the position group"),
    Column("depth_rank", "INTEGER", doc="1 = starter; legacy depth_team or daily pos_rank"),
)

UNIT_LEGACY = {"Offense": "offense", "Defense": "defense", "Special Teams": "special_teams"}
UNIT_DAILY = {
    "3WR 1TE": "offense",
    "Base 4-3 D": "defense",
    "Base 3-4 D": "defense",
    "Special Teams": "special_teams",
}


def unit_sql(expr: str, mapping: Mapping[str, str]) -> str:
    whens = " ".join(f"WHEN '{k}' THEN '{v}'" for k, v in mapping.items())
    return f"(CASE {expr} {whens} ELSE 'other' END)"


def fact_depth_chart_spec() -> Table:
    _check_names(
        "depth_charts_legacy",
        ["season", "club_code", "week", "game_type", "depth_team", "formation", "position",
         "depth_position", "gsis_id", "full_name", "first_name", "last_name"],
    )  # fmt: skip
    _check_names(
        "depth_charts",
        ["dt", "team", "player_name", "espn_id", "gsis_id", "pos_grp", "pos_abb", "pos_slot",
         "pos_rank"],
    )  # fmt: skip
    return Table(
        name="fact_depth_chart",
        source="depth_charts",
        primary_key=(
            "season",
            "source_format",
            "week",
            "game_type",
            "dt",
            "team",
            "unit_raw",
            "position",
            "pos_slot",
            "depth_rank",
            "gsis_id",
        ),
        not_null_key=("season", "source_format", "team", "unit_raw"),
        columns=DEPTH_CHART_COLUMNS,
        distinct=True,
        doc=(
            "One row per depth-chart slot, unified across the two upstream formats. Legacy rows "
            "(2001-2024) are one chart per team-week (a REG and a WC chart can exist for the same "
            "week; SBBYE rows have NULL week); exact duplicates are removed. Daily rows (2025+) "
            "are one league-wide pull per dt (all 32 teams; a date can have several pulls) and are "
            "mapped to a week by dt via week_at_dt (NULL after the season's last as-of). "
            "gsis_id may be NULL (empty slot or unmatched name) and the same player may fill "
            "several slots (WR and KR), so it is never used to dedupe daily rows."
        ),
    )


# ---- weekly rosters (C1) ------------------------------------------------------------------

# Status order used to keep one row when a player appears twice in a week (a trade shows the
# old team's TRD/TRC/TRT row next to the new team's ACT row): the more "on a team" status
# wins; 'others' (TRC, TRD, TRT, NWT, RSN, EXE ...) sit between SUS and CUT; ties: team.
ROSTER_STATUS_PRIORITY = ("ACT", "INA", "DEV", "RES", "PUP", "SUS", None, "CUT", "RET")


def roster_status_rank_sql(expr: str) -> str:
    """0 for the most preferred status, then 1, 2 ...; statuses not listed get the rank of
    the ``None`` slot of :data:`ROSTER_STATUS_PRIORITY` (NULL status too)."""
    other = ROSTER_STATUS_PRIORITY.index(None)
    whens = " ".join(
        f"WHEN '{st}' THEN {i}" for i, st in enumerate(ROSTER_STATUS_PRIORITY) if st is not None
    )
    return f"(CASE {expr} {whens} ELSE {other} END)"


def _nonempty(expr: str) -> str:
    return f"NULLIF(trim({expr}), '')"


def fact_roster_week_spec() -> Table:
    snap = snapshot_columns("rosters_weekly")
    docs = {
        "season": "NFL season",
        "week": "roster week (1-22; regular season and playoffs, like dim_week.week)",
        "game_type": "REG/WC/DIV/CON/SB",
        "team": "current abbreviation (the 2002-2015 NFL codes ARZ/BLT/CLV/HST/SL and OAK/SD/"
        "STL/LAR mapped to today's)",
        "gsis_id": "player id (rows without one are dropped and counted)",
        "position": "the roster position of that week: point-in-time (players do change "
        "position; dim_player.position is today's)",
        "depth_chart_position": "roster depth-chart position (NULL before 2016)",
        "status": "roster status that week: ACT active, INA inactive for the game, DEV "
        "practice squad, RES reserve lists (injured reserve ...), PUP, SUS, CUT, RET; plus "
        "transaction codes TRC/TRD/TRT in 2002-2015 duplicates",
        "status_description_abbr": "the source's short status description code",
        "full_name": "player name as listed on the roster",
        "years_exp": "seasons of NFL experience (= season - entry_year)",
        "entry_year": "the season the player entered the league",
        "rookie_year": "nflverse's rookie year (hidden point-in-time: a few rows carry a "
        "later season)",
        "draft_club": "drafting team as spelled in the source (NULL if undrafted)",
        "draft_number": "overall pick number (NULL if undrafted)",
    }
    _check_names("rosters_weekly", [n for n in docs if n != "season_type"])
    # explicit types: upstream drifts (draft_number is text in some seasons)
    types = dict.fromkeys(
        ("season", "week", "years_exp", "entry_year", "rookie_year", "draft_number"), "INTEGER"
    )
    cols = []
    for name in ("season", "week", "game_type"):
        cols.append(Column(name, types.get(name, duckdb_type_of(snap[name])), doc=docs[name]))
    cols += [
        Column(
            "season_type", "VARCHAR", source="game_type", transform=season_type_sql,
            doc="REG or POST (from game_type)",
        ),
        Column("team", "VARCHAR", transform=normalize_roster_team_sql, doc=docs["team"]),
        _raw_team("team"),
        Column("gsis_id", "VARCHAR", transform=_nonempty, doc=docs["gsis_id"]),
    ]  # fmt: skip
    for name in (
        "position", "depth_chart_position", "status", "status_description_abbr", "full_name",
        "years_exp", "entry_year", "rookie_year", "draft_club", "draft_number",
    ):  # fmt: skip
        cols.append(Column(name, types.get(name, duckdb_type_of(snap[name])), doc=docs[name]))
    return Table(
        name="fact_roster_week",
        source="rosters_weekly",
        primary_key=("season", "week", "gsis_id"),
        columns=tuple(cols),
        dedupe_order=(roster_status_rank_sql("status"), "team"),
        doc=(
            "One row per player per roster week (2002+): his team, position and status that "
            "week, the point-in-time source of positions (dim_player's is today's). Rows "
            "without a gsis_id are dropped; a player listed twice in a week (a trade) keeps "
            "one row by status (ACT > INA > DEV > RES > PUP > SUS > others > CUT > RET, then "
            "team). When a week's roster is public depends on the season (available_at: "
            "game-day snapshots from 2016, post-game snapshots before)."
        ),
    )


# ---- FantasyPros rankings (C1) ------------------------------------------------------------


def fact_ranking_spec() -> Table:
    _check_names(
        "ff_rankings_all",
        ["scrape_date", "ecr_type", "fp_page", "page_type", "id", "player", "pos", "team", "ecr",
         "sd", "best", "worst", "player_owned_espn", "player_owned_yahoo", "player_owned_avg"],
    )  # fmt: skip
    cols = (
        Column("scrape_date", "DATE", doc="the day FantasyPros' page was saved"),
        Column(
            "season",
            "INTEGER",
            doc="NFL season of the scrape: its year from March on, the year before in "
            "January-February (a January weekly ranking belongs to the season that is ending)",
        ),
        Column("ecr_type", "VARCHAR", doc="rp (redraft positional) or wp (weekly positional)"),
        Column(
            "page_kind",
            "VARCHAR",
            doc="preseason (a position's redraft cheat sheet), ros (rest of season) or "
            "weekly (that week's positional ranking); IDP, kicker, defense and dynasty-labelled "
            "pages are not kept",
        ),
        Column(
            "page_pos",
            "VARCHAR",
            doc="the position of the page (QB/RB/WR/TE). A few players are listed on another "
            "position's page too (a RB on the WR sheet): pos is the player's own position",
        ),
        Column("fp_page", "VARCHAR", doc="the page as the archive names it"),
        Column("page_type", "VARCHAR", doc="the archive's page label (redraft-wr, weekly-qb ...)"),
        Column("fantasypros_id", "VARCHAR", doc="FantasyPros player id (canonical text)"),
        Column(
            "gsis_id",
            "VARCHAR",
            computed=True,
            doc="the player's gsis_id through bridge_player_id (id_type 'fantasypros'); NULL "
            "when the FantasyPros id maps to no player (listed in the id report)",
        ),
        Column("player", "VARCHAR", doc="player name as FantasyPros writes it"),
        Column("pos", "VARCHAR", doc="FantasyPros' position for the player (QB/RB/WR/TE)"),
        Column("team", "VARCHAR", doc="team as FantasyPros spells it (not normalized)"),
        Column("ecr", "DOUBLE", doc="expert consensus rank on that page (lower is better)"),
        Column("sd", "DOUBLE", doc="standard deviation of the experts' ranks"),
        Column("best", "DOUBLE", doc="best rank any expert gave"),
        Column("worst", "DOUBLE", doc="worst rank any expert gave"),
        Column(
            "pos_rank",
            "INTEGER",
            computed=True,
            doc="the player's rank on the page among the page position's players, by ecr: "
            "1 + the number of page_pos players with a strictly lower ecr (ties share the "
            "best rank, like SQL rank()); a player of another position listed on the page gets "
            "the rank he would have among them. NULL when ecr is NULL",
        ),
        Column(
            "player_owned_espn",
            "DOUBLE",
            doc="percent of ESPN leagues rostering the player on the scrape day (0-100; "
            "populated 2020 partly, 2021-2023, 2024 partly)",
        ),
        Column("player_owned_yahoo", "DOUBLE", doc="percent of Yahoo leagues (0-100)"),
        Column(
            "player_owned_avg",
            "DOUBLE",
            doc="FantasyPros' average rostership across sites (0-100; 2020 partly, 2021+)",
        ),
    )
    return Table(
        name="fact_ranking",
        source="ff_rankings_all",
        primary_key=("scrape_date", "ecr_type", "page_kind", "page_pos", "fantasypros_id"),
        columns=cols,
        doc=(
            "One row per player per FantasyPros positional ranking page per scrape day "
            "(redraft cheat sheets, rest-of-season and weekly pages; QB/RB/WR/TE only), from "
            "the ff_rankings_all archive (December 2019 on), with expert consensus ranks and "
            "the rostership percentages of that day. Exact duplicates on a page are removed "
            "(the lowest ecr is kept). The seasons of the build only."
        ),
    )


# ---- expected fantasy points (C3, ffopportunity weekly) -----------------------------------

# The actual and expected (``_exp``) stat components xFP is re-scored from (twm.scoring.xfp),
# ffopportunity's own point totals (a cross-check only: its scoring is fixed upstream), and the
# attempts behind them. Everything else in the 159-column file (first downs, ``_diff`` and
# ``_team`` columns) is left out; the names are checked against the snapshot at import.
OPPORTUNITY_COMPONENTS = (
    "pass_attempt", "rec_attempt", "rush_attempt",
    "pass_completions", "pass_completions_exp", "receptions", "receptions_exp",
    "pass_yards_gained", "pass_yards_gained_exp", "rec_yards_gained", "rec_yards_gained_exp",
    "rush_yards_gained", "rush_yards_gained_exp",
    "pass_touchdown", "pass_touchdown_exp", "rec_touchdown", "rec_touchdown_exp",
    "rush_touchdown", "rush_touchdown_exp",
    "pass_two_point_conv", "pass_two_point_conv_exp", "rec_two_point_conv",
    "rec_two_point_conv_exp", "rush_two_point_conv", "rush_two_point_conv_exp",
    "pass_interception", "pass_interception_exp", "rec_fumble_lost", "rush_fumble_lost",
    "pass_fantasy_points", "pass_fantasy_points_exp", "rec_fantasy_points",
    "rec_fantasy_points_exp", "rush_fantasy_points", "rush_fantasy_points_exp",
    "total_fantasy_points", "total_fantasy_points_exp",
)  # fmt: skip


def fact_opportunity_week_spec() -> Table:
    _check_names(
        "ff_opportunity",
        ["season", "week", "game_id", "player_id", "full_name", "position", "posteam",
         *OPPORTUNITY_COMPONENTS],
    )  # fmt: skip
    cols = [
        # upstream season is a String ('2026') and week a Float64 in every season 2006-2026
        Column("season", "INTEGER", doc="NFL season (a string upstream)"),
        Column("week", "INTEGER", doc="game week (a float upstream; integral, checked)"),
        Column(
            "season_type",
            "VARCHAR",
            computed=True,
            doc="REG or POST, from the game's fact_game row (ffopportunity has no game type)",
        ),
        Column("game_id", "VARCHAR"),
        Column("player_id", "VARCHAR", doc="the player's gsis_id (rows without one are dropped)"),
        Column("full_name", "VARCHAR", doc="player name as ffopportunity writes it"),
        Column(
            "position",
            "VARCHAR",
            doc="ffopportunity's position for the player (hidden point-in-time: it is not "
            "known when it was recorded); only used to pick a row when an id repeats",
        ),
        Column(
            "posteam",
            "VARCHAR",
            transform=normalize_team_sql,
            doc="the player's team in that game (current abbreviation; upstream already uses it)",
        ),
    ]
    for name in OPPORTUNITY_COMPONENTS:
        if name.endswith("_attempt"):
            doc = "attempts (passes thrown, targets, carries) in the game"
        elif "fantasy_points" in name:
            doc = (
                "ffopportunity's own points (its fixed PPR scoring, 2-dp): a cross-check only; "
                "xFP is re-scored from the _exp components with config/scoring.yaml"
            )
        elif name.endswith("_exp"):
            doc = "expected value from ffopportunity's model (rounded to 0.01 upstream)"
        else:
            doc = "actual value"
        cols.append(Column(name, "DOUBLE", doc=doc))
    return Table(
        name="fact_opportunity_week",
        source="ff_opportunity",
        primary_key=("game_id", "player_id"),
        columns=tuple(cols),
        # upstream repeats one gsis_id under a second name in two 2013 games (a TE's target
        # also listed for an OLB of another name): the row at a QB/RB/WR/TE position wins
        dedupe_order=(
            "CASE WHEN position IN ('QB', 'RB', 'WR', 'TE') THEN 0 ELSE 1 END",
            "full_name NULLS LAST",
        ),
        doc=(
            "One row per player per game (2006+, regular season and playoffs) from nflverse's "
            "ffopportunity weekly file: the actual and EXPECTED stat components (what an "
            "average player would have produced from the same targets and carries, from "
            "ffopportunity's models: spec 6.3 caveat). xFP = the _exp components re-scored "
            "with config/scoring.yaml (twm.scoring.xfp). Rows without a player id (a team's "
            "passes without an identified target) are dropped and counted; a (game, player) "
            "listed twice upstream keeps the row at a QB/RB/WR/TE position."
        ),
    )


# ---- expected values per play (D1, ffopportunity pbp_pass / pbp_rush) --------------------

# The build copies the play's garbage-time flag from fact_play (same game_id, play_id), so
# every consumer can split expected points into garbage time and the rest without a join.
OPPORTUNITY_PLAY_GARBAGE = Column(
    "is_garbage_time",
    "BOOLEAN",
    computed=True,
    doc="fact_play.is_garbage_time of the same play (game_id, play_id); NULL when the play is "
    "not in fact_play (3 pass plays of 2011-2018 on the 2026-09-29 cache; "
    "build_manifest.notes.n_not_in_fact_play)",
)
_EXP_DOC = "expected value from ffopportunity's model for this play (6 decimals upstream)"
_FLAG_DOC = "0/1 (a categorical '0'/'1' upstream)"


def _opportunity_play_head(season_type_from: str) -> list[Column]:
    return [
        Column("season", "INTEGER", doc="NFL season"),
        Column("week", "INTEGER", doc="game week"),
        Column("season_type", "VARCHAR", computed=True, doc=season_type_from),
        Column("game_id", "VARCHAR"),
        Column(
            "play_id",
            "INTEGER",
            doc="the play's id in the game (a float upstream; integral, "
            "checked); joins fact_play on (game_id, play_id)",
        ),
        Column(
            "posteam",
            "VARCHAR",
            transform=normalize_team_sql,
            doc="team with the ball (current abbreviation; upstream already uses it)",
        ),
    ]


def fact_opportunity_pass_spec() -> Table:
    cols = _opportunity_play_head("REG or POST, from the game's fact_game row")
    cols += [
        Column("passer_player_id", "VARCHAR", doc="the passer's gsis_id"),
        Column("passer_full_name", "VARCHAR", doc="passer name as ffopportunity writes it"),
        Column(
            "passer_position",
            "VARCHAR",
            doc="ffopportunity's position for the passer (hidden point-in-time: when it was "
            "recorded is not documented)",
        ),
        Column(
            "receiver_player_id",
            "VARCHAR",
            doc="the targeted player's gsis_id (NULL when the pass had no identified target)",
        ),
        Column("receiver_full_name", "VARCHAR", doc="receiver name as ffopportunity writes it"),
        Column(
            "receiver_position",
            "VARCHAR",
            doc="ffopportunity's position for the receiver (hidden point-in-time); only used to "
            "pick a row when a play repeats",
        ),
        Column("two_point_attempt", "INTEGER", doc="1 on a two-point try"),
        Column("two_point_converted", "INTEGER", doc="1 when the two-point try succeeded"),
        Column("pass_attempt", "INTEGER", doc="1 (every row is a pass attempt)"),
        Column("complete_pass", "INTEGER", doc=f"completed pass, {_FLAG_DOC}"),
        Column("pass_touchdown", "INTEGER", doc=f"passing touchdown, {_FLAG_DOC}"),
        Column("interception", "INTEGER", doc=f"interception, {_FLAG_DOC}"),
        Column("fumble_lost", "INTEGER", doc="1 when a fumble on the play was lost"),
        Column("air_yards", "INTEGER", doc="yards the ball travelled past the line of scrimmage"),
        Column("receiving_yards", "INTEGER", doc="receiving yards on a completion (else NULL)"),
        Column(
            "pass_completion_exp",
            "DOUBLE",
            doc=f"chance the pass is completed: {_EXP_DOC}; an expected reception for the "
            "receiver and an expected completion for the passer (two-point tries excluded)",
        ),
        Column(
            "yards_after_catch_exp",
            "DOUBLE",
            doc=f"expected yards after the catch if it is caught: {_EXP_DOC}. Expected "
            "passing/receiving yards of the play = pass_completion_exp x (air_yards + "
            "yards_after_catch_exp) (verified: sums reproduce fact_opportunity_week)",
        ),
        Column("pass_touchdown_exp", "DOUBLE", doc=f"chance of a touchdown: {_EXP_DOC}"),
        Column(
            "pass_interception_exp",
            "DOUBLE",
            doc=f"chance of an interception: {_EXP_DOC}; "
            "ffopportunity's weekly totals leave out two-point tries",
        ),
        Column(
            "two_point_conv_exp",
            "DOUBLE",
            doc=f"chance a two-point try succeeds: {_EXP_DOC} (0 on other plays)",
        ),
        OPPORTUNITY_PLAY_GARBAGE,
    ]
    _check_names("ff_opportunity_pass", [c.name for c in cols if not c.computed])
    return Table(
        name="fact_opportunity_pass",
        source="ff_opportunity_pass",
        primary_key=("game_id", "play_id"),
        columns=tuple(cols),
        # upstream repeats two 2013 pass plays: the same target listed a second time under an
        # outside linebacker's name (like fact_opportunity_week); the QB/RB/WR/TE row wins
        dedupe_order=(
            "CASE WHEN receiver_position IN ('QB', 'RB', 'WR', 'TE') THEN 0 ELSE 1 END",
            "receiver_full_name NULLS LAST",
        ),
        doc=(
            "One row per pass play (2006+, regular season and playoffs, two-point tries "
            "included) from nflverse's ffopportunity per-play file (pbp_pass): the passer, the "
            "target, what happened and the EXPECTED values of ffopportunity's models "
            "(completion, yards after catch, touchdown, interception, two-point conversion; "
            "spec 6.3 caveat), plus the play's garbage-time flag from fact_play. Summed per "
            "player-game they reproduce fact_opportunity_week's expected passing and receiving "
            "components (twm.modules.regression_watch.plays). A play listed twice upstream "
            "keeps the row whose receiver is at a QB/RB/WR/TE position."
        ),
    )


def fact_opportunity_rush_spec() -> Table:
    cols = _opportunity_play_head("REG or POST, from the game's fact_game row")
    cols += [
        Column("rusher_player_id", "VARCHAR", doc="the rusher's gsis_id"),
        Column("full_name", "VARCHAR", doc="rusher name as ffopportunity writes it"),
        Column(
            "position",
            "VARCHAR",
            doc="ffopportunity's position for the rusher (hidden point-in-time: when it was "
            "recorded is not documented)",
        ),
        Column("two_point_attempt", "INTEGER", doc="1 on a two-point try"),
        Column("two_point_converted", "INTEGER", doc="1 when the two-point try succeeded"),
        Column("rush_attempt", "INTEGER", doc="1 (every row is a rush attempt, kneels included)"),
        Column("rush_touchdown", "INTEGER", doc=f"rushing touchdown, {_FLAG_DOC}"),
        Column("fumble_lost", "INTEGER", doc="1 when a fumble on the play was lost"),
        Column("rushing_yards", "INTEGER", doc="rushing yards on the play"),
        Column(
            "rush_yards_exp",
            "DOUBLE",
            doc=f"expected rushing yards: {_EXP_DOC}. ffopportunity sets a kneel to -1 and an "
            "aborted snap to 0 here (its raw model value, rushing_yards_exp upstream, is not "
            "kept); two-point tries are left out of its weekly yards",
        ),
        Column(
            "rush_touchdown_exp",
            "DOUBLE",
            doc=f"chance of a rushing touchdown: {_EXP_DOC} (0 on kneels and two-point tries; "
            "the raw model value rushing_td_exp is not kept)",
        ),
        Column(
            "two_point_conv_exp",
            "DOUBLE",
            doc=f"chance a two-point try succeeds: {_EXP_DOC} (0 on other plays)",
        ),
        OPPORTUNITY_PLAY_GARBAGE,
    ]
    _check_names("ff_opportunity_rush", [c.name for c in cols if not c.computed])
    return Table(
        name="fact_opportunity_rush",
        source="ff_opportunity_rush",
        primary_key=("game_id", "play_id"),
        columns=tuple(cols),
        doc=(
            "One row per rushing play (2006+, regular season and playoffs, kneels and "
            "two-point tries included) from nflverse's ffopportunity per-play file (pbp_rush): "
            "the rusher, what happened and the EXPECTED rushing yards, touchdown and two-point "
            "conversion of ffopportunity's models (spec 6.3 caveat), plus the play's "
            "garbage-time flag from fact_play. Summed per player-game they reproduce "
            "fact_opportunity_week's expected rushing components "
            "(twm.modules.regression_watch.plays)."
        ),
    )


# ---- dimensions --------------------------------------------------------------------------


def dim_team_spec() -> Table:
    cols = columns_from_snapshot("teams")
    cols += [
        Column("current_abbr", "VARCHAR", source="team_abbr", transform=normalize_team_sql),
        Column("is_current", "BOOLEAN", computed=True, doc="team_abbr == current_abbr"),
    ]
    return Table(
        name="dim_team",
        source="teams",
        primary_key=("team_abbr",),
        columns=tuple(cols),
        doc=(
            "All 36 nflverse team rows, including the historical aliases OAK, SD, STL and LAR. "
            "current_abbr maps each to the abbreviation used today; is_current is false for the "
            "four aliases. Join facts on current abbreviations."
        ),
    )


DIM_PLAYER_COLUMNS = [
    "gsis_id", "display_name", "position", "position_group", "birth_date", "rookie_season",
    "last_season", "draft_year", "draft_round", "draft_pick", "draft_team", "college_name",
    "espn_id", "pfr_id", "height", "weight", "status", "latest_team",
]  # fmt: skip
# One id per system, from bridge_player_id (B3); espn_id/pfr_id above come from there too.
DIM_PLAYER_ID_COLUMNS = {
    "sleeper_id": "Sleeper",
    "fantasypros_id": "FantasyPros",
    "yahoo_id": "Yahoo",
    "sportradar_id": "Sportradar",
    "mfl_id": "MyFantasyLeague",
}
BRIDGE_ID_DOC = (
    "{site} id from bridge_player_id (canonical text; when a player has several ids of this "
    "type, the most trusted method wins, then the smallest id; the others are listed in "
    "report_id_unmatched as 'multiple_ids')"
)


def dim_player_spec() -> Table:
    _check_names("players", DIM_PLAYER_COLUMNS)
    snap = snapshot_columns("players")
    docs = {
        "espn_id": BRIDGE_ID_DOC.format(site="ESPN") + "; nflverse's players table first",
        "pfr_id": BRIDGE_ID_DOC.format(site="Pro-Football-Reference")
        + "; nflverse's players table first",
    }
    cols = [
        Column(
            n,
            "DATE" if n == "birth_date" else duckdb_type_of(snap[n]),
            transform=normalize_team_sql if n in ("draft_team", "latest_team") else None,
            computed=n in docs,
            doc=docs.get(n, ""),
        )
        for n in DIM_PLAYER_COLUMNS
    ]
    cols += [
        Column(n, "VARCHAR", computed=True, doc=BRIDGE_ID_DOC.format(site=site))
        for n, site in DIM_PLAYER_ID_COLUMNS.items()
    ]
    return Table(
        name="dim_player",
        source="players",
        primary_key=("gsis_id",),
        columns=tuple(cols),
        doc=(
            "One row per player from nflverse's players table, with one id per other system "
            "(PFR, ESPN, Sleeper, FantasyPros, Yahoo, Sportradar, MFL) from bridge_player_id. "
            "gsis_id is the canonical key; rows without one are dropped and counted."
        ),
    )


# ---- player ids and the unmatched-id report (B3, twm.ids) --------------------------------

BRIDGE_PLAYER_ID = Table(
    name="bridge_player_id",
    primary_key=("id_type", "source_id"),
    columns=(
        Column(
            "id_type",
            "VARCHAR",
            doc="the id system: pfr, espn, sleeper, fantasypros, yahoo, sportradar, pff, nfl, "
            "mfl, esb, smart, otc, rotowire, fantasy_data, cbs, stats, fleaflicker, cfbref, "
            "rotoworld, ktc, swish (twm.ids.ID_TYPES)",
        ),
        Column(
            "source_id",
            "VARCHAR",
            doc="the id in that system as canonical text (integers without a decimal part, "
            "trimmed)",
        ),
        Column("gsis_id", "VARCHAR", doc="the player it belongs to (always in dim_player)"),
        Column(
            "method",
            "VARCHAR",
            doc="where the link comes from, most trusted first: manual (data/manual/"
            "player_id_overrides.csv), players, rosters_weekly, ff_playerids, name_birthdate, "
            "name_position_draft (name passes, only for ff_playerids rows without a gsis_id); "
            "<source>_usage_override (e.g. rosters_weekly_usage_override): the usage check "
            "replaced a link whose player's career does not fit where the id is used",
        ),
        Column(
            "n_candidates",
            "INTEGER",
            doc="distinct gsis_ids the id-based sources proposed for this id, players missing "
            "from dim_player included (1 = all agree)",
        ),
        Column(
            "is_conflict",
            "BOOLEAN",
            doc="sources disagreed (n_candidates > 1); the most trusted source won, unless "
            "the usage check overrode it",
        ),
    ),
    doc=(
        "One row per (id_type, source_id): the gsis_id that id belongs to. Join any other "
        "source's player id here to reach gsis_id. Ids that the winning source maps to two "
        "players are ambiguous and left out (listed in report_id_unmatched). A row is visible "
        "point-in-time once its player exists in dim_player (public_from_utc)."
    ),
)

REPORT_ID_COVERAGE = Table(
    name="report_id_coverage",
    primary_key=("dataset", "season", "id_type", "scope"),
    not_null_key=("dataset", "id_type", "scope"),
    columns=(
        Column(
            "dataset",
            "VARCHAR",
            doc="warehouse table or raw cache dataset; [..] names a subset (ecr_type, format)",
        ),
        Column("season", "INTEGER", doc="season (rankings: the scrape's calendar year)"),
        Column("id_type", "VARCHAR", doc="bridge id_type, or 'gsis' (is the id in dim_player?)"),
        Column("scope", "VARCHAR", doc="'all' rows, or 'fantasy' (QB/RB/WR/TE rows)"),
        Column("n_rows", "BIGINT", doc="rows with an id"),
        Column("n_rows_unmatched", "BIGINT", doc="rows whose id maps to no gsis_id"),
        Column("n_ids", "BIGINT", doc="distinct ids"),
        Column("n_ids_unmatched", "BIGINT", doc="distinct ids that map to no gsis_id"),
        Column("match_rate", "DOUBLE", doc="1 - n_rows_unmatched / n_rows"),
        Column("id_match_rate", "DOUBLE", doc="1 - n_ids_unmatched / n_ids"),
    ),
    doc=(
        "The unmatched-id report, part 1 (spec 7.5, rebuilt on every build): how many rows and "
        "distinct ids of each dataset map to a gsis_id, per season, id type and scope. "
        "Bookkeeping (kind meta): not part of the as-of view."
    ),
)

REPORT_ID_UNMATCHED = Table(
    name="report_id_unmatched",
    primary_key=("kind", "dataset", "id_type", "source_id"),
    columns=(
        Column(
            "kind",
            "VARCHAR",
            doc="unmatched (an id of that dataset maps to no gsis_id), suspect (the dataset's "
            "use of the id contradicts its link: seasons outside the player's career, re-linked "
            "or left NULL, or a position on the other side of the ball, kept), ambiguous (left "
            "out of the bridge), conflict (sources disagreed, precedence won), name_link (made "
            "by a name pass: spot-check), multiple_ids (a player with several ids of a "
            "dim_player type)",
        ),
        Column("dataset", "VARCHAR", doc="as in report_id_coverage; bridge_player_id otherwise"),
        Column("id_type", "VARCHAR"),
        Column("source_id", "VARCHAR"),
        Column(
            "gsis_id",
            "VARCHAR",
            doc="the linked player (suspect, conflict, name_link, multiple_ids)",
        ),
        Column("name", "VARCHAR", doc="player name (the dataset's, or dim_player's)"),
        Column("position", "VARCHAR", doc="the dataset's position (or today's for bridge rows)"),
        Column("season_min", "INTEGER"),
        Column("season_max", "INTEGER"),
        Column("n_rows", "BIGINT", doc="unmatched: rows of the dataset with this id"),
        Column("detail", "VARCHAR", doc="the candidates and methods, or the name-pass evidence"),
    ),
    doc=(
        "The unmatched-id report, part 2: every unmatched id with a name, plus the bridge's "
        "ambiguous ids, conflicts, name-pass links and players with several ids of one type. "
        "Bookkeeping (kind meta). `twm ids` renders it as markdown."
    ),
)


DIM_COACH = Table(
    name="dim_coach",
    primary_key=("coach_id",),
    columns=(
        Column("coach_id", "VARCHAR", doc="slug of the name: lowercase, non-alphanumerics -> _"),
        Column(
            "coach_name",
            "VARCHAR",
            doc=(
                "canonical spelling: whitespace collapsed, then the alphabetically (byte order) "
                "first of the spellings seen for this slug; merges are listed in "
                "build_manifest.notes.merged_spellings"
            ),
        ),
    ),
    doc=(
        "One row per head coach seen in schedules (home_coach/away_coach); spellings that share "
        "a slug are one coach. Interim coaches absent from the schedule columns (2024+) are not "
        "here."
    ),
)

COACH_GAME = Table(
    name="coach_game",
    primary_key=("game_id", "team"),
    columns=(
        Column("game_id", "VARCHAR"),
        Column("season", "INTEGER"),
        Column("week", "INTEGER"),
        Column("season_type", "VARCHAR"),
        Column("team", "VARCHAR"),
        Column("coach_id", "VARCHAR"),
        Column("is_home", "BOOLEAN"),
    ),
    doc=(
        "One row per team per game: the head coach nflverse lists in the schedule for that game "
        "(two rows per game). Upstream reflects mid-season coaching changes through 2023 but NOT "
        "in 2024-2025 (e.g. TEN/NYG 2025, NYJ/CHI/NO 2024 list the fired coach all season), so "
        "interim coaches are missing there. Rows exist for unplayed games too."
    ),
)

COACH_TEAM_SEASON = Table(
    name="coach_team_season",
    primary_key=("coach_id", "team", "season"),
    columns=(
        Column("coach_id", "VARCHAR"),
        Column("team", "VARCHAR"),
        Column("season", "INTEGER"),
        Column("first_week", "INTEGER", doc="first scheduled week of the stint (REG or POST)"),
        Column("last_week", "INTEGER", doc="last scheduled week (18 all season for 2026 today)"),
        Column(
            "n_games",
            "INTEGER",
            doc=(
                "games scheduled for this coach-team stint that season (played or not, playoffs "
                "included). Point-in-time the row only appears once the stint is over, so this "
                "count never includes future games there (only a direct read of "
                "wh.coach_team_season does). For games coached to date, count coach_game rows "
                "through twm.asof (AsOfView / asof_filter)."
            ),
        ),
    ),
    doc=(
        "One row per coach-team-season as listed in the schedule (first/last week, games). "
        "Point-in-time since B2: a stint row appears only once the stint is over (season end, "
        "or the next coach's first kickoff), so at an in-season as-of the current coach has no "
        "row here; count coach_game rows through AsOfView for games coached to date. A team "
        "has >1 row only when nflverse "
        "records the change; 2024+ seasons show one coach per team even after in-season "
        "firings. Interim flags are P2."
    ),
)

DIM_WEEK = Table(
    name="dim_week",
    primary_key=("season", "week", "season_type"),
    columns=(
        Column("season", "INTEGER"),
        Column("week", "INTEGER"),
        Column("season_type", "VARCHAR", doc="REG or POST"),
        Column("game_type", "VARCHAR", doc="REG/WC/DIV/CON/SB"),
        Column("n_games", "INTEGER", doc="scheduled games (played or not)"),
        Column(
            "n_kickoff_estimated",
            "INTEGER",
            doc=(
                "games with a guessed kickoff time (all of 1999; the 102 prime-time rows of "
                "2000-2005 with the '09:00' placeholder): kickoff/end columns are guesses"
            ),
        ),
        Column("first_gameday", "DATE", doc="Eastern date of the first kickoff"),
        Column("last_gameday", "DATE"),
        Column("first_kickoff_utc", "TIMESTAMP"),
        Column("last_kickoff_utc", "TIMESTAMP"),
        Column("last_game_end_utc_est", "TIMESTAMP", doc="last kickoff + 4 h"),
        Column("asof_weekly_utc", "TIMESTAMP", doc="Tuesday 14:00 UTC after the week"),
        Column("n_games_after_asof", "INTEGER", doc="games (est.) ending after the as-of"),
        Column(
            "is_split_week",
            "BOOLEAN",
            doc="n_games_after_asof > 0: a game moved past Monday "
            "(2010 W16, 2020 W5/W12/W13, 2021 W15)",
        ),
        Column("is_last_reg_week", "BOOLEAN"),
        Column("asof_end_of_regular_season_utc", "TIMESTAMP", doc="only on the last REG week"),
        Column("prev_week", "INTEGER", doc="previous week number in the season (any type)"),
        Column("next_week", "INTEGER"),
        Column("window_start_utc", "TIMESTAMP", doc="previous week's as-of (NULL for week 1)"),
        Column("window_end_utc", "TIMESTAMP", doc="= asof_weekly_utc"),
    ),
    doc=(
        "One row per (season, week, season_type) present in fact_game with the official as-of "
        "timestamps (see weeks.py). A timestamp t belongs to the week with "
        "window_start_utc < t <= window_end_utc."
    ),
)

BUILD_MANIFEST_COLUMNS = (
    "table_name",
    "n_rows",
    "content_hash",
    "seasons",
    "seasons_skipped",
    "n_source_rows",
    "n_dropped_null_key",
    "n_dropped_duplicates",
    "notes",
    "built_at",
    "twm_version",
    "nflreadpy_version",
    "duckdb_version",
    "polars_version",
)


@cache
def tables() -> dict[str, Table]:
    """All warehouse tables in build order (dimensions first, then facts)."""
    specs = [
        dim_team_spec(),
        fact_game_spec(),
        DIM_WEEK,
        fact_schedule_spec(),
        DIM_COACH,
        COACH_GAME,
        COACH_TEAM_SEASON,
        fact_play_spec(),
        fact_player_week_spec(),
        fact_team_week_spec(),
        fact_opportunity_week_spec(),
        fact_opportunity_pass_spec(),
        fact_opportunity_rush_spec(),
        fact_snaps_spec(),
        fact_injury_report_spec(),
        fact_depth_chart_spec(),
        fact_roster_week_spec(),
        fact_ranking_spec(),
        dim_player_spec(),
        BRIDGE_PLAYER_ID,
        REPORT_ID_COVERAGE,
        REPORT_ID_UNMATCHED,
    ]
    return {t.name: t for t in specs}


# Datasets a build reads from the cache. Depth charts are split by format at build time.
SOURCE_DATASETS = (
    "schedules",
    "pbp",
    "player_stats",
    "team_stats",
    "ff_opportunity",
    "ff_opportunity_pass",
    "ff_opportunity_rush",
    "snap_counts",
    "injuries",
    "depth_charts",
    "players",
    "teams",
    "ff_playerids",
    "rosters_weekly",
    "ff_rankings_all",
)
