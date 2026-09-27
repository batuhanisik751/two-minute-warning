"""When is a warehouse row available? The ``available_at`` rules and the table registry (B2).

The one idea (PROJECT_SPEC Section 6.1): every prediction is made at a moment ``as_of``, and it
may only use rows that were public at that moment. So every *event* row in the warehouse (a
play, a game, an injury report line, a depth-chart slot ...) carries ``available_at``: the
earliest moment (UTC) we are confident the WHOLE row, every column of it, was public. A
prediction at ``as_of`` may use the row only if ``available_at <= as_of`` (inclusive).

We rarely know the true publication time, so the rules below are *estimates*, and every
estimate errs LATE: a late estimate only hides a little information for a few hours, while an
early one lets the future leak into a backtest and makes the model look better than it is.

Four kinds of tables (see :data:`TABLE_AVAILABILITY`):

- **event**: has an ``available_at`` column (the last column); filter it with
  :func:`twm.asof.asof_filter` or read it through :class:`twm.asof.AsOfView`. A few event
  columns are snapshots taken *today* (``fact_player_week.position``: a player who changed
  position shows the later one for every past season); they are listed as
  ``hindsight_columns`` and the as-of view leaves them out.
- **static**: no ``available_at``; always visible (team list, the calendar of as-of times).
  ``dim_week``'s schedule-derived counts of a week that has not been played yet are masked
  (``masked_columns``): the calendar is known, a future postponement is not.
- **hindsight**: a snapshot taken *today* (``dim_player``, ``bridge_player_id``). Its rows
  appear only from the player's draft (or first public data row) on (``public_from_utc``; a
  bridge row copies its player's), and point-in-time readers see only an allowlist of columns
  that do not change later.
- **meta**: build bookkeeping (``build_manifest``, the id report tables ``report_id_*``);
  never exposed point-in-time.

Some columns hold a value that is already "baked in" when the row becomes visible (a position
taken today, nflverse's final schedule); ``available_at`` cannot fix those, and the leakage
harness cannot see them either. They are listed here and in docs/warehouse.md.

The build (``twm.warehouse.build``) turns :func:`available_at_sql` into the ``available_at``
column inside the same transaction, fails loudly if any event row ends up without one, and
records per-table counts in ``build_manifest.notes``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Literal

from twm.warehouse import schema as sc
from twm.warehouse import weeks as wk

Kind = Literal["event", "static", "hindsight", "meta"]
KINDS = ("event", "static", "hindsight", "meta")

AVAILABLE_AT = "available_at"
SLOT_AVAILABLE_AT = "slot_available_at"
PUBLIC_FROM = "public_from_utc"

# Injury rows carry an observed last-modified stamp from 2010 (2009's is effectively empty,
# docs/assumptions.md section 4); earlier and later seasons fall back to kickoff.
INJURY_DATE_MODIFIED_FIRST_SEASON = 2010
# From 2021 the stamps are real UTC (their time of day moves with daylight saving time); the
# 2010-2020 ones are not (DST-invariant, ~11:50 on Fridays), so they get a conservative offset.
INJURY_UTC_STAMP_FIRST_SEASON = 2021
# A legacy (weekly, 2001-2024) depth chart for week N counts as public 6 days before that week's
# Tuesday as-of: the Wednesday 14:00 UTC before week N's games (practice week starts).
LEGACY_DEPTH_CHART_LEAD = timedelta(days=6)
# An injury report for week N is never treated as public before the as-of of week N-1 has
# passed (spec 6.1: "Injury reports for week N+1: not available at the Tuesday as-of").
AFTER_PREVIOUS_ASOF = timedelta(seconds=1)
# A schedule change with a SOURCED announcement date D counts as public from D + 1 day at this
# time (UTC). Undated changes (the default) count from the game's own kickoff.
EXCEPTION_PUBLIC_TIME = time(12, 0)

GAME_DATA_TABLES = {
    "fact_play": "pbp",
    "fact_player_week": "player_stats",
    "fact_team_week": "team_stats",
    "fact_snaps": "snap_counts",
}

# fact_schedule columns that can still change after the spring release (flexed or moved games,
# Week 17/18 slots chosen late, venue moves): hidden in the as-of view until slot_available_at.
SCHEDULE_SLOT_COLUMNS = (
    "gameday", "weekday", "gametime", "kickoff_utc", "kickoff_is_estimated", "location",
    "stadium", "away_rest", "home_rest",
)  # fmt: skip
# dim_week columns computed from the FINAL schedule of that week (they reveal later
# postponements, cancellations and moved games): masked until the week's own as-of.
DIM_WEEK_SCHEDULE_COLUMNS = (
    "n_games", "n_kickoff_estimated", "first_gameday", "last_gameday", "first_kickoff_utc",
    "last_kickoff_utc", "last_game_end_utc_est", "n_games_after_asof", "is_split_week",
)  # fmt: skip


class AvailabilityError(RuntimeError):
    """An event row got no ``available_at`` (a rule could not place it in time), or a rule
    would drop rows from an official as-of snapshot."""


# --------------------------------------------------------------------------------------
# Rules configuration
# --------------------------------------------------------------------------------------

DEFAULT_NIGHT_SLOTS = {"Monday": time(21, 0), "default": time(20, 30)}


@dataclass(frozen=True)
class ScheduleChange:
    """A schedule change announced after the release (config ``schedule_exceptions``).

    ``public_from`` (naive UTC) is when the changed row counts as public: 12:00 UTC on the day
    after a sourced announcement date, or ``None``, meaning "from the game's own kickoff" (the
    default: a game is always announced before it is played, so no date is needed to be safe).
    ``cancelled``: the game is absent from nflverse (never played).
    """

    game_id: str
    public_from: datetime | None = None
    what: str = ""
    cancelled: bool = False

    @classmethod
    def announced_on(
        cls,
        game_id: str,
        announced: str | date | None,
        what: str = "",
        cancelled: bool = False,
    ) -> ScheduleChange:
        if announced is None:
            return cls(game_id, None, what, cancelled)
        day = announced if isinstance(announced, date) else date.fromisoformat(str(announced))
        public = datetime.combine(day + timedelta(days=1), EXCEPTION_PUBLIC_TIME)
        return cls(game_id, public, what, cancelled)


@dataclass(frozen=True)
class AvailabilityRules:
    """``config/settings.yaml`` -> ``availability`` as plain values."""

    game_data_lag: Mapping[str, timedelta] = field(
        default_factory=lambda: {ds: timedelta(hours=6) for ds in GAME_DATA_TABLES.values()}
    )
    game_result_lag: timedelta = timedelta(hours=3)
    schedule_release_month: int = 5
    schedule_release_day: int = 20
    schedule_slot_lead: timedelta = timedelta(days=12)
    # Eastern kickoff assumed for games whose kickoff time was guessed: weekday name -> time,
    # "default" for the others.
    estimated_kickoff_et: Mapping[str, time] = field(
        default_factory=lambda: dict(DEFAULT_NIGHT_SLOTS)
    )
    injury_legacy_offset: timedelta = timedelta(hours=9)
    draft_public_month: int = 5
    draft_public_day: int = 15
    schedule_exceptions: tuple[ScheduleChange, ...] = ()

    @classmethod
    def from_config(cls, cfg: Any) -> AvailabilityRules:
        """From the validated ``settings().availability`` model (or an equivalent mapping)."""
        get = cfg.get if isinstance(cfg, Mapping) else lambda k: getattr(cfg, k)

        def month_day(key: str) -> tuple[int, int]:
            month, day = (int(x) for x in str(get(key)).split("-"))
            return month, day

        def item(x: Any, k: str, default: Any = None) -> Any:
            return x.get(k, default) if isinstance(x, Mapping) else getattr(x, k, default)

        rel_m, rel_d = month_day("schedule_release_month_day")
        draft_m, draft_d = month_day("draft_public_month_day")
        return cls(
            game_data_lag={
                str(k): timedelta(hours=float(v)) for k, v in get("game_data_lag_hours").items()
            },
            game_result_lag=timedelta(hours=float(get("game_result_lag_hours"))),
            schedule_release_month=rel_m,
            schedule_release_day=rel_d,
            schedule_slot_lead=timedelta(days=int(get("schedule_slot_lead_days"))),
            estimated_kickoff_et={
                str(k): wk.parse_hhmm(str(v))
                for k, v in get("estimated_kickoff_for_availability_et").items()
            },
            injury_legacy_offset=timedelta(hours=float(get("injury_legacy_stamp_offset_hours"))),
            draft_public_month=draft_m,
            draft_public_day=draft_d,
            schedule_exceptions=tuple(
                ScheduleChange.announced_on(
                    item(x, "game_id"),
                    item(x, "announced"),
                    item(x, "what", ""),
                    bool(item(x, "cancelled", False)),
                )
                for x in get("schedule_exceptions")
            ),
        )

    @classmethod
    def from_settings(cls) -> AvailabilityRules:
        from twm.config import settings

        return cls.from_config(settings().availability)

    def night_slot(self, weekday: str) -> time:
        """The assumed Eastern kickoff of a guessed-kickoff game played on ``weekday``."""
        return self.estimated_kickoff_et.get(weekday, self.estimated_kickoff_et["default"])

    def cancelled_games(self) -> list[ScheduleChange]:
        """Scheduled games that were later cancelled and are absent from nflverse (2022 W17
        BUF at CIN). Feature code counting "games remaining" before the cancellation should add
        them back (or at least warn): until then the game was still on the schedule."""
        return [x for x in self.schedule_exceptions if x.cancelled]


def availability_game_end(
    gameday: str | date,
    game_end_utc_est: datetime,
    kickoff_is_estimated: bool,
    rules: AvailabilityRules,
) -> datetime:
    """The game end the availability rules use (``fact_game.availability_game_end_utc``).

    A game with a real kickoff time: ``game_end_utc_est`` (kickoff + 4 h). A game whose kickoff
    was guessed (all of 1999, the 2000-2005 '09:00' placeholders): the guess may be hours too
    early (a 13:00 default for what was really a night game), so assume the latest normal slot
    for that weekday instead (``estimated_kickoff_et``: 21:00 ET on Monday nights, the
    1999-2005 Monday-night kickoff; 20:30 ET otherwise) + 4 h, never earlier than the guess.
    """
    if not kickoff_is_estimated:
        return game_end_utc_est
    day = gameday if isinstance(gameday, date) else date.fromisoformat(str(gameday))
    slot = rules.night_slot(day.strftime("%A"))
    night, _ = wk.kickoff_utc(day.isoformat(), slot.strftime("%H:%M"))
    return max(game_end_utc_est, night + wk.GAME_DURATION_EST)


# --------------------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Availability:
    """How one warehouse table relates to time.

    kind: event / static / hindsight / meta (module docstring). rule: plain-language sentence.
    visible_columns: hindsight only, the columns a point-in-time reader may use.
    hidden_columns: hindsight only, column -> why it would leak.
    row_visible_column: hindsight only, the stored column holding the time a row starts to
    exist point-in-time (rows with NULL or a later time are left out of the as-of view).
    hindsight_columns: event tables, columns that are a snapshot taken today (column -> why);
    the as-of view leaves them out.
    masked_columns / masked_until: columns shown as NULL in the as-of view while the SQL
    expression ``masked_until`` (over the stored row) is NULL or after the as-of.
    extra_columns: helper columns the build stores (before ``available_at``), name -> doc.
    week_key: event tables only, SQL expressions (season, week) naming the row's own week, for
    the manifest count ``n_available_after_week_asof`` (None when a row has no week).
    """

    table: str
    kind: Kind
    rule: str
    visible_columns: tuple[str, ...] = ()
    hidden_columns: Mapping[str, str] = field(default_factory=dict)
    row_visible_column: str | None = None
    hindsight_columns: Mapping[str, str] = field(default_factory=dict)
    masked_columns: tuple[str, ...] = ()
    masked_until: str | None = None
    extra_columns: Mapping[str, str] = field(default_factory=dict)
    week_key: tuple[str, str] | None = None


_SW = ("season", "week")

TODAYS_POSITION = (
    "today's position (nflverse's current player table): a player who changed position shows "
    "the later one for every past season (Cordarrelle Patterson WR -> RB in 2021, Taysom Hill "
    "QB -> TE); derive a point-in-time position from the as-of-visible depth-chart slot "
    "(fact_depth_chart.position) instead"
)

# Ids are identifiers, not features: visible so a point-in-time reader can join other sources
# (spec 6.2 rule 5, "no identifiers as model features", is enforced by the feature registry).
DIM_PLAYER_VISIBLE = (
    "gsis_id", "display_name", "birth_date", "draft_year", "draft_round", "draft_pick",
    "draft_team", "college_name", "espn_id", "pfr_id", "sleeper_id", "fantasypros_id",
    "yahoo_id", "sportradar_id", "mfl_id",
)  # fmt: skip
DIM_PLAYER_HIDDEN = {
    "last_season": "the last season he played: at an as-of in 2019 it tells you whether a "
    "player retires after 2019 (or is still active today)",
    "status": "today's roster status (ACT, RES, RET ...): reveals later injuries and retirements",
    "latest_team": "the team he plays for today: reveals every later trade and signing",
    "rookie_season": "the first season he played, which for an undrafted or late-signed "
    "player can be after the as-of (a player who has not debuted yet 'exists' in hindsight)",
    "position": TODAYS_POSITION,
    "position_group": TODAYS_POSITION,
    "height": "today's listed height: can differ from the value listed at the as-of",
    "weight": "today's listed weight: players gain and lose weight over a career (and a "
    "change of weight often comes with a change of position)",
}

BRIDGE_VISIBLE = ("id_type", "source_id", "gsis_id")
BRIDGE_BOOKKEEPING = (
    "bookkeeping about today's sources (which of them list the player, e.g. a later season's "
    "roster), not needed to join ids point-in-time; read wh.bridge_player_id to inspect it"
)
BRIDGE_HIDDEN = {
    "method": BRIDGE_BOOKKEEPING,
    "n_candidates": BRIDGE_BOOKKEEPING,
    "is_conflict": BRIDGE_BOOKKEEPING,
}

TABLE_AVAILABILITY: dict[str, Availability] = {
    a.table: a
    for a in (
        Availability(
            "fact_game",
            "event",
            "The whole game row (final score, closing lines, coaches) is public once the game "
            "is over: available_at = availability_game_end_utc (kickoff + 4 h; a guessed "
            "kickoff counts as a night game: 21:00 ET on Mondays, 20:30 ET otherwise) + "
            "game_result_lag_hours (3 h, for weather delays and overtime).",
            week_key=_SW,
        ),
        Availability(
            "fact_schedule",
            "event",
            "Who plays whom in which week: regular-season games from the spring schedule "
            "release (May 20 of the season, 00:00 UTC); a playoff game once the previous "
            "round's last game is final (game end + game_result_lag_hours). The date, time, "
            "venue and rest days (slot columns) are public only from slot_available_at: "
            "schedule_slot_lead_days (12) before kickoff, and for the last two regular-season "
            "weeks not before the previous week's as-of. Games in "
            "availability.schedule_exceptions (moved or relocated after the release) wait for "
            "their own kickoff (or a sourced announcement date). The row content is "
            "nflverse's FINAL schedule: changes not listed there cannot be detected.",
            masked_columns=SCHEDULE_SLOT_COLUMNS,
            masked_until=SLOT_AVAILABLE_AT,
            extra_columns={
                SLOT_AVAILABLE_AT: "UTC time from which the slot columns (gameday, weekday, "
                "gametime, kickoff_utc, kickoff_is_estimated, location, stadium, away_rest, "
                "home_rest) count as public: max(available_at, kickoff - "
                "schedule_slot_lead_days, the previous week's as-of for the last two "
                "regular-season weeks, a listed schedule change's kickoff); the as-of "
                "view shows them as NULL before that",
            },
            week_key=_SW,
        ),
        Availability(
            "fact_play",
            "event",
            "A play is public in nflverse's nightly run after its game: game end + "
            "game_data_lag_hours.pbp (6 h). Uses the game's own end, so a game moved to "
            "Tuesday/Wednesday is not visible at its week's Tuesday as-of.",
            week_key=_SW,
        ),
        Availability(
            "fact_player_week",
            "event",
            "A player's game stats are public in the nightly run after the game: game end + "
            "game_data_lag_hours.player_stats (6 h), joined on game_id. position, "
            "position_group and headshot_url are today's values and left out of the as-of view.",
            hindsight_columns={
                "position": TODAYS_POSITION,
                "position_group": TODAYS_POSITION,
                "headshot_url": "today's headshot (a later team's uniform can reveal a move)",
            },
            week_key=_SW,
        ),
        Availability(
            "fact_team_week",
            "event",
            "A team's game stats are public in the nightly run after the game: game end + "
            "game_data_lag_hours.team_stats (6 h), joined on game_id.",
            week_key=_SW,
        ),
        Availability(
            "fact_snaps",
            "event",
            "Snap counts are public after the game's data refresh: game end + "
            "game_data_lag_hours.snap_counts (6 h), joined on game_id.",
            week_key=_SW,
        ),
        Availability(
            "fact_injury_report",
            "event",
            "An injury-report line is public at its observed last-modified time "
            "(date_modified: 2021-2024 as stored, 2010-2020 + injury_legacy_stamp_offset_hours "
            "(9 h) because those stamps are not true UTC); otherwise at the team's kickoff that "
            "week (the final report is out by then); a team without a game that week uses the "
            "week's as-of. Never before the previous week's as-of has passed.",
            week_key=_SW,
        ),
        Availability(
            "fact_depth_chart",
            "event",
            "Daily snapshots (2025+) at their snapshot time dt. Weekly legacy charts (2001-2024) "
            "on the Wednesday before that week's games (its Tuesday as-of minus 6 days): week "
            "N's chart is visible at the as-of after week N, week N+1's is not. player_position "
            "(legacy roster position) is today's value and left out of the as-of view.",
            hindsight_columns={"player_position": TODAYS_POSITION},
            week_key=("season", "COALESCE(week, week_at_dt)"),
        ),
        Availability(
            "coach_game",
            "event",
            "Who coaches a game is certain at kickoff; a future game's listed coach can "
            "reveal a firing, so the row stays hidden until kickoff.",
            week_key=_SW,
        ),
        Availability(
            "coach_team_season",
            "event",
            "A coach's stint (first/last week, games) is only known once it is over: after "
            "the team's last game of the season is final (game end + game_result_lag_hours), "
            "or, if another coach takes over, at the new coach's first kickoff (so a stint row "
            "never announces a firing early). At an in-season as-of the current stint has no "
            "row yet.",
            week_key=("season", "last_week"),
        ),
        Availability(
            "dim_coach",
            "event",
            "A head coach exists for point-in-time purposes from the kickoff of his first "
            "game in the warehouse.",
        ),
        Availability(
            "dim_team",
            "static",
            "Team names and abbreviations: always visible. Accepted hindsight: team_division "
            "is today's alignment (SEA shows NFC West for 1999-2001) and every team column in "
            "the warehouse uses today's franchise code (a 2014 STL row reads LA), which reveals "
            "later relocations. Use a point-in-time division lookup if divisions become a "
            "feature.",
        ),
        Availability(
            "dim_week",
            "static",
            "The calendar of weeks and official as-of times: always visible. The columns "
            "computed from the week's final schedule (n_games, first/last gameday and kickoff, "
            "n_games_after_asof, is_split_week ...) reveal later postponements and "
            "cancellations, so the as-of view shows them as NULL until the week's own "
            "asof_weekly_utc.",
            masked_columns=DIM_WEEK_SCHEDULE_COLUMNS,
            masked_until="asof_weekly_utc",
        ),
        Availability(
            "build_manifest",
            "meta",
            "Build bookkeeping (row counts over the whole warehouse, future rows included; "
            "hashes): not exposed point-in-time, never a model input. Read it as "
            "wh.build_manifest when needed.",
        ),
        Availability(
            "dim_player",
            "hindsight",
            "A snapshot taken today. A player exists point-in-time only from his draft "
            "(draft_public_month_day, May 15 of draft_year) or, undrafted, from his first "
            "public data row (stats, snaps, injury report, depth chart), whichever applies "
            "(public_from_utc); a player with neither never appears. Readers see only columns "
            "that do not change later (name, birth date, draft, college, ids); position, size, "
            "last_season, status, latest_team and rookie_season are hidden. draft_team uses "
            "today's franchise code (a 2016 San Diego pick reads LAC).",
            visible_columns=DIM_PLAYER_VISIBLE,
            hidden_columns=DIM_PLAYER_HIDDEN,
            row_visible_column=PUBLIC_FROM,
            extra_columns={
                PUBLIC_FROM: "UTC time from which this player exists point-in-time: his draft "
                "(availability.draft_public_month_day of draft_year, 00:00 UTC) or, undrafted, "
                "the earliest available_at of his rows in fact_player_week, fact_snaps, "
                "fact_injury_report and fact_depth_chart; NULL (never visible) when neither "
                "exists",
            },
        ),
        Availability(
            "bridge_player_id",
            "hindsight",
            "Built today from today's id tables. A link is visible point-in-time once its "
            "player exists in dim_player (his public_from_utc, copied here), so a future "
            "player's ids never appear early. Readers see id_type, source_id and gsis_id; the "
            "bookkeeping columns (method, n_candidates, is_conflict) are hidden.",
            visible_columns=BRIDGE_VISIBLE,
            hidden_columns=BRIDGE_HIDDEN,
            row_visible_column=PUBLIC_FROM,
            extra_columns={
                PUBLIC_FROM: "dim_player.public_from_utc of the linked player: the row is "
                "visible point-in-time from then on (NULL: never)",
            },
        ),
        Availability(
            "report_id_coverage",
            "meta",
            "The unmatched-id report (match rates over the whole warehouse and cache, future "
            "seasons included): bookkeeping, never a model input.",
        ),
        Availability(
            "report_id_unmatched",
            "meta",
            "The unmatched-id report (ids without a gsis_id, suspect links, ambiguous ids, "
            "conflicts, name links): bookkeeping, never a model input.",
        ),
    )
}


def kind_of(table: str) -> Kind:
    """The registered kind of ``table``; KeyError (with a hint) for an unknown table."""
    try:
        return TABLE_AVAILABILITY[table].kind
    except KeyError as e:
        raise KeyError(
            f"{table!r} is not in twm.warehouse.available.TABLE_AVAILABILITY; register it "
            "(event / static / hindsight / meta) before reading it point-in-time"
        ) from e


def event_tables() -> list[str]:
    return [t for t, a in TABLE_AVAILABILITY.items() if a.kind == "event"]


def stored_column_names(table: sc.Table) -> list[str]:
    """The columns the build stores: the spec's columns, then the registry's helper columns
    (``slot_available_at``, ``public_from_utc``), then ``available_at`` last for an event
    table."""
    a = TABLE_AVAILABILITY[table.name]
    extra = [AVAILABLE_AT] if a.kind == "event" else []
    return table.column_names + list(a.extra_columns) + extra


# --------------------------------------------------------------------------------------
# Point-in-time SQL (shared by twm.asof.AsOfView and the leakage harness)
# --------------------------------------------------------------------------------------


def masked_condition(a: Availability, cutoff: str) -> str | None:
    """SQL condition: this row's masked columns are NOT yet public at ``cutoff``."""
    if not a.masked_columns or a.masked_until is None:
        return None
    return f"(({a.masked_until}) IS NULL OR ({a.masked_until}) > {cutoff})"


def future_row_condition(a: Availability, cutoff: str) -> str | None:
    """SQL condition: this row does not exist yet at ``cutoff`` (None: every row exists)."""
    if a.kind == "event":
        return f"{AVAILABLE_AT} > {cutoff}"
    if a.row_visible_column is not None:
        col = sc.q(a.row_visible_column)
        return f"({col} IS NULL OR {col} > {cutoff})"
    return None


def required_columns(a: Availability) -> list[str]:
    """Columns the as-of view needs in the stored table (missing: built before this rule)."""
    need = [AVAILABLE_AT] if a.kind == "event" else []
    if a.row_visible_column:
        need.append(a.row_visible_column)
    if a.masked_until and a.masked_until in a.extra_columns:
        need.append(a.masked_until)
    return need


def point_in_time_select(a: Availability, source: str, columns: Sequence[str], cutoff: str) -> str:
    """The SELECT behind the as-of view of one table.

    ``source`` is the qualified stored table, ``columns`` its stored columns in order and
    ``cutoff`` the as-of as a SQL TIMESTAMP literal. Rows that do not exist yet are filtered
    out, hindsight columns dropped (allowlist for a hindsight table), masked columns NULL while
    not yet public.
    """
    if a.kind == "meta":
        raise ValueError(f"{a.table} is build bookkeeping: it has no point-in-time view")
    if a.kind == "hindsight":
        cols = [c for c in columns if c in a.visible_columns]
    else:
        cols = [c for c in columns if c not in a.hindsight_columns]
    parts = []
    for c in cols:
        if c in a.masked_columns and a.masked_until is not None:
            parts.append(
                f"CASE WHEN ({a.masked_until}) <= {cutoff} THEN {sc.q(c)} END AS {sc.q(c)}"
            )
        else:
            parts.append(sc.q(c))
    sql = f"SELECT {', '.join(parts)} FROM {source}"
    future = future_row_condition(a, cutoff)
    return f"{sql} WHERE NOT {future}" if future else sql


# --------------------------------------------------------------------------------------
# SQL for the build
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AvailabilitySQL:
    """How the build computes ``available_at`` (or another time column) for one table.

    The staged row is aliased ``s``. ``expr`` is the SQL expression, ``joins`` the LEFT JOINs it
    needs (each joined relation is unique on its join key, so the row count never changes),
    ``branch`` labels which rule placed each row (counted into the manifest; ``branches`` lists
    every label so a branch that placed no row is recorded as 0), ``flags`` are extra (note
    name, SQL condition) counts and ``extra`` are helper columns (name, SQL) stored before the
    time column.
    """

    expr: str
    joins: str = ""
    branch: str | None = None
    branches: tuple[str, ...] = ()
    flags: tuple[tuple[str, str], ...] = ()
    extra: tuple[tuple[str, str], ...] = ()


def _interval(td: timedelta) -> str:
    return f"to_microseconds(CAST({round(td.total_seconds() * 1_000_000)} AS BIGINT))"


def _ts(value: datetime) -> str:
    return f"TIMESTAMP '{value.isoformat(sep=' ', timespec='microseconds')}'"


# The kickoff the rules use: availability_game_end_utc - 4 h, i.e. kickoff_utc for a game with a
# real kickoff time, and the night-slot guess (21:00 / 20:30 ET) for an estimated one.
def _kickoff(alias: str) -> str:
    return f"({alias}.availability_game_end_utc - {_interval(wk.GAME_DURATION_EST)})"


# When a game's result is public: its (availability) end + game_result_lag.
def _result_public(alias: str, rules: AvailabilityRules) -> str:
    return f"({alias}.availability_game_end_utc + {_interval(rules.game_result_lag)})"


def _exceptions_relation(rules: AvailabilityRules) -> str:
    """The listed schedule changes as a relation (game_id, public_from), unique on game_id.

    ``public_from`` is NULL for an undated change: the rule then uses the game's own kickoff."""
    rows = [x for x in rules.schedule_exceptions if not x.cancelled]
    if not rows:
        return (
            "(SELECT CAST(NULL AS VARCHAR) AS game_id, CAST(NULL AS TIMESTAMP) AS public_from "
            "WHERE FALSE)"
        )
    values = ", ".join(
        f"({sc.sql_str(x.game_id)}, "
        f"{_ts(x.public_from) if x.public_from else 'CAST(NULL AS TIMESTAMP)'})"
        for x in rows
    )
    return f"(SELECT * FROM (VALUES {values}) t(game_id, public_from))"


def available_at_sql(table: str, rules: AvailabilityRules) -> AvailabilitySQL:
    """The ``available_at`` rule of an event table as SQL (see the registry for the prose)."""
    if kind_of(table) != "event":
        raise ValueError(f"{table} is {kind_of(table)}: it has no available_at column")

    if table == "fact_game":
        return AvailabilitySQL(
            expr=_result_public("s", rules),
            branch=(
                "CASE WHEN s.kickoff_is_estimated THEN 'estimated_kickoff_night_slot' "
                "ELSE 'game_end' END"
            ),
            branches=("estimated_kickoff_night_slot", "game_end"),
        )

    if table in GAME_DATA_TABLES:
        lag = rules.game_data_lag[GAME_DATA_TABLES[table]]
        return AvailabilitySQL(
            expr=f"(_g.availability_game_end_utc + {_interval(lag)})",
            joins="LEFT JOIN fact_game _g ON _g.game_id = s.game_id",
        )

    if table == "fact_schedule":
        release = (
            f"make_timestamp(s.season, {rules.schedule_release_month}, "
            f"{rules.schedule_release_day}, 0, 0, 0)"
        )
        rule = (
            f"CASE WHEN s.season_type = 'REG' THEN {release} "
            f"ELSE COALESCE(_p.prev_round_end, {_kickoff('_g')}) END"
        )
        # A listed change is public from its sourced announcement, else from the game's own
        # kickoff. GREATEST skips NULLs: a game that is not listed keeps its rule value.
        changed = (
            f"CASE WHEN _x.game_id IS NOT NULL THEN COALESCE(_x.public_from, {_kickoff('_g')}) END"
        )
        slot = (
            f"GREATEST({rule}, {_kickoff('_g')} - {_interval(rules.schedule_slot_lead)}, "
            f"CASE WHEN _l.week IS NOT NULL THEN _pw.asof_weekly_utc END, {changed})"
        )
        return AvailabilitySQL(
            expr=f"GREATEST({rule}, {changed})",
            extra=((SLOT_AVAILABLE_AT, slot),),
            joins=f"""
            LEFT JOIN fact_game _g ON _g.game_id = s.game_id
            LEFT JOIN dim_week _w
              ON _w.season = s.season AND _w.week = s.week AND _w.season_type = s.season_type
            LEFT JOIN dim_week _pw ON _pw.season = s.season AND _pw.week = _w.prev_week
            LEFT JOIN (
                SELECT season, week, max({_result_public("fg", rules)}) AS prev_round_end
                FROM fact_game fg GROUP BY season, week
            ) _p ON _p.season = s.season AND _p.week = _w.prev_week
            LEFT JOIN (
                SELECT season, week FROM dim_week WHERE is_last_reg_week
                UNION
                SELECT season, prev_week FROM dim_week
                WHERE is_last_reg_week AND prev_week IS NOT NULL
            ) _l ON s.season_type = 'REG' AND _l.season = s.season AND _l.week = s.week
            LEFT JOIN {_exceptions_relation(rules)} _x ON _x.game_id = s.game_id""",
            branch=(
                "CASE WHEN s.season_type = 'REG' THEN 'reg_schedule_release' "
                "WHEN _p.prev_round_end IS NOT NULL THEN 'post_previous_round_end' "
                "ELSE 'post_own_kickoff_no_previous_week' END"
            ),
            branches=(
                "reg_schedule_release",
                "post_previous_round_end",
                "post_own_kickoff_no_previous_week",
            ),
            flags=(
                ("n_schedule_exceptions", "_x.game_id IS NOT NULL"),
                (
                    "n_schedule_exceptions_at_kickoff",
                    "_x.game_id IS NOT NULL AND _x.public_from IS NULL",
                ),
                ("n_slot_last_two_reg_weeks", "_l.week IS NOT NULL"),
            ),
        )

    if table == "coach_game":
        return AvailabilitySQL(
            expr=_kickoff("_g"), joins="LEFT JOIN fact_game _g ON _g.game_id = s.game_id"
        )

    if table == "dim_coach":
        return AvailabilitySQL(
            expr="_f.first_kickoff",
            joins=f"""
            LEFT JOIN (
                SELECT c.coach_id, min({_kickoff("g")}) AS first_kickoff
                FROM coach_game c JOIN fact_game g ON g.game_id = c.game_id
                GROUP BY c.coach_id
            ) _f ON _f.coach_id = s.coach_id""",
        )

    if table == "coach_team_season":
        return AvailabilitySQL(
            expr="COALESCE(_n.next_coach_kickoff, _s.stint_end)",
            joins=f"""
            LEFT JOIN (
                SELECT c.coach_id, c.team, c.season,
                       max({_result_public("g", rules)}) AS stint_end
                FROM coach_game c JOIN fact_game g ON g.game_id = c.game_id
                GROUP BY c.coach_id, c.team, c.season
            ) _s ON _s.coach_id = s.coach_id AND _s.team = s.team AND _s.season = s.season
            LEFT JOIN (
                SELECT a.coach_id, a.team, a.season, min({_kickoff("g")}) AS next_coach_kickoff
                FROM (
                    SELECT coach_id, team, season, max(week) AS last_week
                    FROM coach_game GROUP BY coach_id, team, season
                ) a
                JOIN coach_game c
                  ON c.team = a.team AND c.season = a.season AND c.week > a.last_week
                 AND c.coach_id <> a.coach_id
                JOIN fact_game g ON g.game_id = c.game_id
                GROUP BY a.coach_id, a.team, a.season
            ) _n ON _n.coach_id = s.coach_id AND _n.team = s.team AND _n.season = s.season""",
            branch=(
                "CASE WHEN _n.next_coach_kickoff IS NOT NULL THEN 'next_coach_first_kickoff' "
                "ELSE 'stint_last_game_end' END"
            ),
            branches=("next_coach_first_kickoff", "stint_last_game_end"),
        )

    if table == "fact_injury_report":
        stamped = "s.date_modified IS NOT NULL"
        utc_era = f"s.season >= {INJURY_UTC_STAMP_FIRST_SEASON}"
        legacy_era = f"s.season >= {INJURY_DATE_MODIFIED_FIRST_SEASON}"
        raw = (
            f"CASE WHEN {utc_era} AND {stamped} THEN s.date_modified "
            f"WHEN {legacy_era} AND {stamped} "
            f"THEN s.date_modified + {_interval(rules.injury_legacy_offset)} "
            "WHEN _k.kickoff IS NOT NULL THEN _k.kickoff "
            "ELSE _w.asof_weekly_utc END"
        )
        floor = f"(_w.window_start_utc + {_interval(AFTER_PREVIOUS_ASOF)})"
        return AvailabilitySQL(
            expr=f"CASE WHEN {floor} > {raw} THEN {floor} ELSE {raw} END",
            joins=f"""
            LEFT JOIN (
                SELECT season, week, season_type, team, max(kickoff) AS kickoff FROM (
                    SELECT season, week, season_type, home_team AS team,
                           {_kickoff("fg")} AS kickoff FROM fact_game fg
                    UNION ALL
                    SELECT season, week, season_type, away_team AS team,
                           {_kickoff("fg")} AS kickoff FROM fact_game fg
                ) GROUP BY season, week, season_type, team
            ) _k ON _k.season = s.season AND _k.week = s.week
                AND _k.season_type = s.season_type AND _k.team = s.team
            LEFT JOIN dim_week _w ON _w.season = s.season AND _w.week = s.week""",
            branch=(
                f"CASE WHEN {utc_era} AND {stamped} THEN 'date_modified' "
                f"WHEN {legacy_era} AND {stamped} THEN 'date_modified_legacy_shifted' "
                "WHEN _k.kickoff IS NOT NULL THEN 'kickoff' "
                "WHEN _w.asof_weekly_utc IS NOT NULL THEN 'week_asof_no_game' "
                "ELSE 'unplaced' END"
            ),
            branches=(
                "date_modified",
                "date_modified_legacy_shifted",
                "kickoff",
                "week_asof_no_game",
                "unplaced",
            ),
            flags=(("n_raised_to_after_previous_week_asof", f"{floor} > {raw}"),),
        )

    if table == "fact_depth_chart":
        lead = _interval(LEGACY_DEPTH_CHART_LEAD)
        return AvailabilitySQL(
            expr=f"""CASE
                WHEN s.source_format = 'daily' THEN s.dt
                WHEN _e.asof_weekly_utc IS NOT NULL THEN _e.asof_weekly_utc - {lead}
                WHEN s.game_type = 'REG' AND _r.asof_weekly_utc IS NOT NULL
                    THEN _r.asof_weekly_utc - {lead}
                WHEN s.game_type = 'SBBYE' THEN _sb.sb_asof - {lead}
            END""",
            joins="""
            LEFT JOIN dim_week _e
              ON s.source_format = 'legacy' AND _e.season = s.season AND _e.week = s.week
             AND _e.season_type = s.season_type
            LEFT JOIN dim_week _r
              ON s.source_format = 'legacy' AND s.game_type = 'REG'
             AND _r.season = s.season AND _r.week = s.week
            LEFT JOIN (
                SELECT season, max(asof_weekly_utc) AS sb_asof
                FROM dim_week WHERE game_type = 'SB' GROUP BY season
            ) _sb ON s.game_type = 'SBBYE' AND _sb.season = s.season""",
            branch="""CASE
                WHEN s.source_format = 'daily' AND s.dt IS NOT NULL THEN 'daily_dt'
                WHEN s.source_format = 'daily' THEN 'daily_without_dt'
                WHEN _e.asof_weekly_utc IS NOT NULL THEN 'legacy_same_week'
                WHEN s.game_type = 'REG' AND _r.asof_weekly_utc IS NOT NULL
                    THEN 'legacy_reg_after_finale_to_next_round'
                WHEN s.game_type = 'SBBYE' AND _sb.sb_asof IS NOT NULL
                    THEN 'legacy_sbbye_to_super_bowl_week'
                ELSE 'legacy_unmapped'
            END""",
            branches=(
                "daily_dt",
                "daily_without_dt",
                "legacy_same_week",
                "legacy_reg_after_finale_to_next_round",
                "legacy_sbbye_to_super_bowl_week",
                "legacy_unmapped",
            ),
        )

    raise ValueError(f"no available_at rule for event table {table!r}")  # pragma: no cover


def row_visibility_sql(table: str, rules: AvailabilityRules) -> AvailabilitySQL:
    """The row rule of a hindsight table as SQL.

    ``dim_player.public_from_utc``: built after every event table, because an undrafted player
    exists from his first public data row (snap counts through their B3 ``gsis_id``). A NULL
    result means "never visible" (errs late): an undrafted player with no row in any built
    season. ``bridge_player_id``: the linked player's ``public_from_utc``.
    """
    a = TABLE_AVAILABILITY[table]
    if a.row_visible_column is None:
        raise ValueError(f"{table} has no row visibility rule")
    if table == "bridge_player_id":  # the linked player's own row rule, reused as is
        return AvailabilitySQL(
            expr="_p.public_from_utc",
            joins="LEFT JOIN dim_player _p ON _p.gsis_id = s.gsis_id",
            branch="CASE WHEN _p.public_from_utc IS NULL THEN 'never' ELSE 'player_public' END",
            branches=("never", "player_public"),
        )
    if table != "dim_player":  # pragma: no cover - a new hindsight table needs its rule here
        raise ValueError(f"{table} has no row visibility rule")
    first_row = "_f.first_row"
    return AvailabilitySQL(
        expr=(
            "CASE WHEN s.draft_year IS NOT NULL THEN make_timestamp(CAST(s.draft_year AS BIGINT), "
            f"{rules.draft_public_month}, {rules.draft_public_day}, 0, 0, 0) "
            f"ELSE {first_row} END"
        ),
        joins="""
            LEFT JOIN (
                SELECT gsis_id, min(available_at) AS first_row FROM (
                    SELECT player_id AS gsis_id, available_at FROM fact_player_week
                    UNION ALL SELECT gsis_id, available_at FROM fact_snaps
                    UNION ALL SELECT gsis_id, available_at FROM fact_injury_report
                    UNION ALL SELECT gsis_id, available_at FROM fact_depth_chart
                ) WHERE gsis_id IS NOT NULL GROUP BY gsis_id
            ) _f ON _f.gsis_id = s.gsis_id""",
        branch=(
            "CASE WHEN s.draft_year IS NOT NULL THEN 'draft' "
            f"WHEN {first_row} IS NOT NULL THEN 'first_fact_row' ELSE 'never' END"
        ),
        branches=("draft", "first_fact_row", "never"),
    )
