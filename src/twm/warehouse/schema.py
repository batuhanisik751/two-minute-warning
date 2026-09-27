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

from twm.sources import nflverse as nv

# Historical abbreviation -> abbreviation used today (dim_team.current_abbr).
TEAM_ALIASES: dict[str, str] = {"OAK": "LV", "SD": "LAC", "STL": "LA", "LAR": "LA"}

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


def normalize_team_sql(expr: str) -> str:
    """Empty string -> NULL, then historical abbreviations -> current ones."""
    inner = f"NULLIF({expr}, '')"
    whens = " ".join(f"WHEN '{old}' THEN '{new}'" for old, new in TEAM_ALIASES.items())
    return f"(CASE {inner} {whens} ELSE {inner} END)"


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
    normalization). doc: plain-language meaning.
    """

    name: str
    type: str
    source: str | None = None
    transform: Callable[[str], str] | None = None
    computed: bool = False
    doc: str = ""

    def sql(self, available: Mapping[str, str]) -> str:
        """The SELECT expression for this column given the source view's column types."""
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
""".split()  # noqa: SIM905
)
FACT_PLAY_TEAM_COLUMNS = (
    "posteam", "defteam", "home_team", "away_team", "penalty_team", "timeout_team"
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
        cols.append(Column(name, typ, transform=transform))
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
        fact_snaps_spec(),
        fact_injury_report_spec(),
        fact_depth_chart_spec(),
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
    "snap_counts",
    "injuries",
    "depth_charts",
    "players",
    "teams",
    "ff_playerids",
    "rosters_weekly",
)
