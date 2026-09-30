"""The week-mapping check (step F4): ESPN's current week (the scoring period the last sync read)
against the warehouse's week for the same moment, the ``dim_week`` row whose as-of window
(after the previous week's Tuesday as-of, up to its own) holds the sync time. F3 assumed they
match (the lists' default week, lineup regret's "a week before ESPN's current week is over"):
when they do not, the report prints a warning and nothing else changes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb

from twm.league import store
from twm.league.drops import read_warehouse


@dataclass(frozen=True)
class WeekCheck:
    season: int
    at: datetime  # the settings sync time (naive UTC)
    espn_week: int | None
    warehouse_week: int | None
    warning: str = ""  # empty when they agree


def warehouse_week(warehouse: Path | None, season: int, at: datetime) -> tuple[int | None, str]:
    """(the regular-season week whose as-of window holds ``at``, why None)."""
    rows = read_warehouse(warehouse, "SELECT week, window_start_utc, window_end_utc FROM dim_week "
                          "WHERE season = ? AND season_type = 'REG' ORDER BY week",
                          [season])  # fmt: skip
    if not rows:
        return None, "the warehouse (dim_week) has no weeks for this season"
    for week, start, end in rows:
        if (start is None or at > start) and (end is None or at <= end):
            return int(week), ""
    return None, "the sync is after the regular season's last as-of"


def week_check(con: duckdb.DuckDBPyConnection, warehouse: Path | None) -> WeekCheck | None:
    """None before any sync."""
    part = store.latest(con, "league_settings")
    if part is None:
        return None
    raw = store.settings_of(con, part.league_id, part.season).get("current_week", "")
    espn = int(raw) if raw.strip().isdigit() else None
    wh, why = warehouse_week(warehouse, part.season, part.synced_at)
    at = f"{part.synced_at:%Y-%m-%d %H:%M} UTC"
    warning = ""
    if wh is None:
        warning = f"ESPN's current week ({espn}) could not be checked: {why}."
    elif espn != wh:
        warning = (f"ESPN's current week is {espn}, but the warehouse puts the sync time ({at}) "
                   f"in week {wh}. The lists' default week and lineup regret assume they match: "
                   "check the week (`--week N`) or sync again after Tuesday's as-of.")  # fmt: skip
    return WeekCheck(part.season, part.synced_at, espn, wh, warning)
