"""Pure functions for ``dim_week``: kickoff times, official as-of timestamps, week windows.

Everything here is plain Python (no DuckDB, no I/O) so it can be unit-tested with a handful of
synthetic games. ``build.py`` feeds it the rows of ``fact_game`` and stores the result.

Vocabulary (see PROJECT_SPEC.md Section 6.1):

- **kickoff_utc**: the scheduled kickoff converted from US Eastern (schedules ``gameday`` and
  ``gametime`` are Eastern wall-clock strings) to UTC, DST-aware via ``zoneinfo``. Rows with
  no usable ``gametime`` (all of 1999, and the 102 prime-time rows of 2000-2005 that carry
  the ``'09:00'`` placeholder) get a weekday default and ``kickoff_is_estimated = True``.
- **game_end_utc_est**: kickoff + 4 hours. A documented estimate of when the game is over.
- **asof_weekly_utc**: the official "we know everything about week N" moment. It is anchored to
  the calendar, not to the last game: the first Tuesday 14:00 UTC (config ``as_of.weekly``)
  strictly after the Eastern date of the week's *first* kickoff. Thursday-to-Monday weeks get the
  Tuesday that follows Monday night. A week whose first game is on Wednesday (2012 and 2026
  openers) still gets the following Tuesday. Games rescheduled to Tuesday/Wednesday (2010 W16,
  the 2020 COVID weeks, 2021 W15) end *after* the as-of and are flagged via
  ``n_games_after_asof`` / ``is_split_week``; they are simply not yet available at that as-of.
  This keeps every week's as-of strictly before
  the next week's first kickoff, which the last-game-based rule violated.
- **asof_end_of_regular_season_utc**: only on the last REG week: ``days_after`` days after the
  last game *date* (Eastern) of that week at ``time`` UTC (config
  ``as_of.hot_seat_end_of_season``; default 1 day, 12:00 UTC = 07:00 EST Monday, before
  Black-Monday announcements). Computed from the ``gameday`` string, never from kickoff_utc, so a
  Sunday-night finish that spills into Monday UTC still yields Monday 12:00 UTC.
- **window**: week N owns timestamps in (asof of week N-1, asof of week N]. Timestamps before
  week 1's as-of belong to week 1: every snapshot from the file's first date (late March in 2026)
  up to week 1's as-of. Timestamps after the last week's as-of belong to no week (``None``):
  post-Super-Bowl snapshots must never be mistaken for Super-Bowl-week information. The week is a
  window label for as-of filtering, not a chart selector; to get the chart in force at an as-of,
  take the latest dt <= as_of per team.

Timestamps are **naive datetimes holding UTC** everywhere (the warehouse stores TIMESTAMP, not
TIMESTAMP WITH TIME ZONE, so results do not depend on the machine's time zone).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
GAME_DURATION_EST = timedelta(hours=4)

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

# Fallback kickoff times (Eastern) for schedule rows without a usable ``gametime``: all of
# 1999 (no gametime at all) and the 102 Monday/Thursday/Saturday prime-time rows of 2000-2005
# that nflverse records as '09:00'. That value is a 12-hour-clock placeholder (the true kickoffs
# were 21:00 / 20:30 ET, i.e. 9 pm), so any gametime before EARLIEST_REAL_KICKOFF_ET is treated
# as missing. 09:30 ET is the earliest real NFL kickoff (London games, 2014+), hence the strict
# ``<`` boundary. The defaults are typical slots, not the latest ones: 13:00 ET puts a Sunday
# game's estimated end at 21:00 UTC even when it really was a late-afternoon or night game, and
# 20:00 ET is an hour before the 21:00 ET Monday-night kickoffs of 1999-2005. No official as-of
# (Tuesday 14:00 UTC, or Monday 12:00 UTC end-of-season) falls inside that gap, and the rows are
# flagged (``kickoff_is_estimated``, ``dim_week.n_kickoff_estimated``), and the availability
# rules (twm.warehouse.available) give them a conservative night-slot game end: the latest
# normal slot for that weekday (21:00 ET Monday nights in 1999-2005, 20:30 ET otherwise).
DEFAULT_KICKOFF_ET = {"Sunday": "13:00", "Saturday": "16:30"}
DEFAULT_KICKOFF_ET_OTHER = "20:00"
EARLIEST_REAL_KICKOFF_ET = time(9, 30)


@dataclass(frozen=True)
class AsOfRules:
    """The as-of configuration (``config/settings.yaml`` -> ``as_of``) as plain values."""

    weekly_weekday: int = WEEKDAYS["tuesday"]
    weekly_time: time = time(14, 0)
    end_of_season_days_after: int = 1
    end_of_season_time: time = time(12, 0)

    @classmethod
    def from_config(cls, as_of: Mapping[str, Any]) -> AsOfRules:
        weekly = as_of["weekly"]
        eos = as_of["hot_seat_end_of_season"]
        if not isinstance(eos, Mapping) or eos.get("anchor") != "last_reg_week":
            raise ValueError(
                "as_of.hot_seat_end_of_season must be a mapping with anchor: last_reg_week"
            )
        return cls(
            weekly_weekday=WEEKDAYS[str(weekly["weekday"]).lower()],
            weekly_time=parse_hhmm(str(weekly["time"])),
            end_of_season_days_after=int(eos.get("days_after", 1)),
            end_of_season_time=parse_hhmm(str(eos.get("time", "12:00"))),
        )


def parse_hhmm(text: str) -> time:
    hh, mm = text.strip().split(":")
    return time(int(hh), int(mm))


def to_naive_utc(ts: datetime) -> datetime:
    """Accept naive (assumed UTC) or aware datetimes; return naive UTC."""
    if ts.tzinfo is None:
        return ts
    return ts.astimezone(UTC).replace(tzinfo=None)


def season_type_of(game_type: str | None) -> str:
    """REG for the regular season, POST for everything else (WC, DIV, CON, SB, SBBYE)."""
    return "REG" if game_type == "REG" else "POST"


# --------------------------------------------------------------------------------------
# Kickoff conversion
# --------------------------------------------------------------------------------------


def kickoff_utc(
    gameday: str, gametime: str | None, weekday: str | None = None
) -> tuple[datetime, bool]:
    """Convert an Eastern ``gameday`` ('2026-09-10') + ``gametime`` ('20:35') to naive UTC.

    Returns ``(kickoff, is_estimated)``. When ``gametime`` is missing (every 1999 game) or is
    the ``'09:00'`` placeholder of the 2000-2005 prime-time rows (anything earlier than
    :data:`EARLIEST_REAL_KICKOFF_ET`), a documented default is used (13:00 ET Sunday, 16:30 ET
    Saturday, 20:00 ET otherwise) and ``is_estimated`` is True.
    """
    estimated = (
        gametime is None
        or not str(gametime).strip()
        or parse_hhmm(str(gametime)) < EARLIEST_REAL_KICKOFF_ET
    )
    if estimated:
        day = date.fromisoformat(gameday)
        wd = weekday or day.strftime("%A")
        gametime = DEFAULT_KICKOFF_ET.get(wd, DEFAULT_KICKOFF_ET_OTHER)
    local = datetime.combine(date.fromisoformat(gameday), parse_hhmm(str(gametime)), EASTERN)
    return local.astimezone(UTC).replace(tzinfo=None), estimated


def implied_totals(
    spread_line: float | None, total_line: float | None
) -> tuple[float | None, float | None]:
    """Closing-line implied team totals. ``spread_line`` > 0 means the home team is favored."""
    if spread_line is None or total_line is None:
        return None, None
    return (total_line + spread_line) / 2, (total_line - spread_line) / 2


# --------------------------------------------------------------------------------------
# As-of rules
# --------------------------------------------------------------------------------------


def first_weekday_after(day: date, weekday: int) -> date:
    """The first date with ``weekday`` (Monday=0) strictly after ``day``."""
    delta = (weekday - day.weekday()) % 7
    return day + timedelta(days=delta or 7)


def weekly_asof(first_gameday: str | date, rules: AsOfRules) -> datetime:
    """Tuesday 14:00 UTC (config) strictly after the Eastern date of the week's first kickoff."""
    day = first_gameday if isinstance(first_gameday, date) else date.fromisoformat(first_gameday)
    return datetime.combine(first_weekday_after(day, rules.weekly_weekday), rules.weekly_time)


def end_of_regular_season_asof(last_gameday: str | date, rules: AsOfRules) -> datetime:
    """``days_after`` days after the last game date (Eastern) of the last REG week, at ``time``."""
    day = last_gameday if isinstance(last_gameday, date) else date.fromisoformat(last_gameday)
    return datetime.combine(
        day + timedelta(days=rules.end_of_season_days_after), rules.end_of_season_time
    )


# --------------------------------------------------------------------------------------
# dim_week
# --------------------------------------------------------------------------------------


def build_dim_week(games: Iterable[Mapping[str, Any]], rules: AsOfRules) -> list[dict[str, Any]]:
    """One row per (season, week, season_type) from ``fact_game``-like rows.

    Each game mapping needs: season, week, game_type, gameday (str 'YYYY-MM-DD'),
    kickoff_utc (naive UTC datetime) and game_end_utc_est; ``kickoff_is_estimated`` is optional
    and counted into ``n_kickoff_estimated``. Rows are returned ordered by
    (season, week); ``prev_week``/``next_week`` and the window run over the whole season
    (REG and POST together) because week numbers are unique within a season and the
    week-18 -> Wild Card gap must have an owner.
    """
    groups: dict[tuple[int, int, str], list[Mapping[str, Any]]] = {}
    for g in games:
        key = (int(g["season"]), int(g["week"]), season_type_of(g["game_type"]))
        groups.setdefault(key, []).append(g)

    rows: list[dict[str, Any]] = []
    for (season, week, season_type), gs in sorted(groups.items()):
        gamedays = sorted(str(g["gameday"]) for g in gs)
        kicks = [g["kickoff_utc"] for g in gs if g.get("kickoff_utc") is not None]
        ends = [g["game_end_utc_est"] for g in gs if g.get("game_end_utc_est") is not None]
        asof = weekly_asof(gamedays[0], rules)
        n_after = sum(1 for e in ends if e > asof)
        rows.append(
            {
                "season": season,
                "week": week,
                "season_type": season_type,
                "game_type": sorted({str(g["game_type"]) for g in gs})[0],
                "n_games": len(gs),
                "n_kickoff_estimated": sum(1 for g in gs if g.get("kickoff_is_estimated")),
                "first_gameday": date.fromisoformat(gamedays[0]),
                "last_gameday": date.fromisoformat(gamedays[-1]),
                "first_kickoff_utc": min(kicks) if kicks else None,
                "last_kickoff_utc": max(kicks) if kicks else None,
                "last_game_end_utc_est": max(ends) if ends else None,
                "asof_weekly_utc": asof,
                "n_games_after_asof": n_after,
                "is_split_week": n_after > 0,
                "is_last_reg_week": False,
                "asof_end_of_regular_season_utc": None,
                "prev_week": None,
                "next_week": None,
                "window_start_utc": None,
                "window_end_utc": asof,
            }
        )

    # Last REG week per season -> end-of-season as-of. Windows/prev/next over the whole season.
    by_season: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        by_season.setdefault(r["season"], []).append(r)
    for season_rows in by_season.values():
        reg = [r for r in season_rows if r["season_type"] == "REG"]
        if reg:
            last = max(reg, key=lambda r: r["week"])
            last["is_last_reg_week"] = True
            last["asof_end_of_regular_season_utc"] = end_of_regular_season_asof(
                last["last_gameday"], rules
            )
        season_rows.sort(key=lambda r: r["week"])
        for i, r in enumerate(season_rows):
            if i > 0:
                r["prev_week"] = season_rows[i - 1]["week"]
                r["window_start_utc"] = season_rows[i - 1]["asof_weekly_utc"]
            if i + 1 < len(season_rows):
                r["next_week"] = season_rows[i + 1]["week"]
    return rows


def week_for_timestamp(
    dim_week_rows: Sequence[Mapping[str, Any]], season: int, ts: datetime
) -> int | None:
    """Map a UTC timestamp to the week whose window contains it.

    Window of week N = (asof_weekly_utc of the previous week, asof_weekly_utc of week N].
    Before week 1's as-of -> week 1. After the last week's as-of (offseason) -> ``None``.
    ``dim_week_rows`` are mappings with at least season, week and asof_weekly_utc (any order).
    """
    ts = to_naive_utc(ts)
    weeks = sorted(
        (
            (int(r["week"]), to_naive_utc(r["asof_weekly_utc"]))
            for r in dim_week_rows
            if int(r["season"]) == season and r.get("asof_weekly_utc") is not None
        ),
    )
    if not weeks:
        return None
    for week, asof in weeks:
        if ts <= asof:
            return week
    return None
