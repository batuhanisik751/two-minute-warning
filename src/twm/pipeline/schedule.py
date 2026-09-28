"""When the scheduled job runs and what it does (step E4). Pure functions of the clock, the
config (``pipeline:`` in settings.yaml) and the current season's schedule; tested without a
real clock.

**The gate** (before anything is downloaded): in season (from ``days_before_first_game`` days
before the season's first regular-season game day to ``days_after_last_game`` days after its
last one, the dates read from the cached schedule) every scheduled run goes ahead; in the
offseason only the nightly run on ``offseason_weekday`` does. A manual run always goes ahead,
and so does a run that cannot tell (no cached schedule yet: a cold cache).

**The plan** (after the warehouse is built): a week's list is due when the clock is inside its
live window, from its Tuesday as-of to the first kickoff of the next week (the same window
that makes a list 'live', :func:`twm.modules.waiver_radar.weekly.run_kind`). The last
regular-season week has no list (nothing follows it). An operator may name a week instead.

**Not ready** (``twm radar score`` exit code 3: some of the week's data has not arrived): a
warning while a later retry attempt is still to come, a failure from the week's LAST attempt on
(``retry_attempts``: the last one is the deadline), so GitHub emails the owner once the data is
late for real. A run later in the window (a nightly one) that still finds it missing fails too.
An operator-named week that is not ready is always a failure: it was asked for explicitly.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl

from twm.config import WEEKDAY_KEYS, PipelineConfig

TRIGGERS = ("schedule", "manual")


def _utc(t: datetime) -> datetime:
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)


def _hhmm(text: str) -> tuple[int, int]:
    h, m = text.split(":")
    return int(h), int(m)


# --------------------------------------------------------------------------------------
# Cron lines (the workflow must match the config; tests/test_workflows.py)
# --------------------------------------------------------------------------------------


def cron_weekday(day: str) -> int:
    """Cron's day of week (0 = Sunday ... 6 = Saturday)."""
    return (WEEKDAY_KEYS.index(day) + 1) % 7


def nightly_cron(cfg: PipelineConfig) -> str:
    h, m = _hhmm(cfg.nightly)
    return f"{m} {h} * * *"


def retry_crons(cfg: PipelineConfig) -> list[str]:
    out = []
    for day, hhmm in cfg.attempts():
        h, m = _hhmm(hhmm)
        out.append(f"{m} {h} * * {cron_weekday(day)}")
    return out


def cron_role(cron: str | None, cfg: PipelineConfig) -> str | None:
    """'nightly' or 'retry' for a cron line of the workflow (``github.event.schedule``), None
    when unknown or not given."""
    if not cron:
        return None
    norm = " ".join(cron.split())
    if norm == nightly_cron(cfg):
        return "nightly"
    if norm in retry_crons(cfg):
        return "retry"
    return None


# --------------------------------------------------------------------------------------
# The season window and the gate
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SeasonDates:
    season: int
    first_game: date  # the first regular-season game day
    last_game: date  # the last regular-season game day


def season_dates(schedule: pl.DataFrame, season: int) -> SeasonDates | None:
    """The first and last regular-season game days of ``season`` in an nflverse schedule
    (``season``, ``game_type``, ``gameday``); None without regular-season games."""
    need = {"season", "game_type", "gameday"}
    if not need <= set(schedule.columns):
        return None
    reg = schedule.filter(
        (pl.col("season").cast(pl.Int64) == season) & (pl.col("game_type") == "REG")
    ).select(pl.col("gameday").cast(pl.String).str.slice(0, 10).str.to_date("%Y-%m-%d"))
    days = reg.get_column("gameday").drop_nulls()
    if days.len() == 0:
        return None
    return SeasonDates(season, days.min(), days.max())  # type: ignore[arg-type]


def season_dates_from_cache(raw_dir: Path, season: int) -> SeasonDates | None:
    """:func:`season_dates` from the cached ``schedules/<season>.parquet`` (None if absent)."""
    path = raw_dir / "schedules" / f"{season}.parquet"
    if not path.exists():
        return None
    try:
        df = pl.read_parquet(path, columns=["season", "game_type", "gameday"])
    except Exception:  # an unreadable cache file: the run goes ahead and re-downloads it
        return None
    return season_dates(df, season)


def in_season(dates: SeasonDates, today: date, cfg: PipelineConfig) -> bool:
    w = cfg.season_window
    start = dates.first_game - timedelta(days=w.days_before_first_game)
    end = dates.last_game + timedelta(days=w.days_after_last_game)
    return start <= today <= end


@dataclass(frozen=True)
class Gate:
    run: bool
    reason: str
    in_season: bool | None  # None: unknown (no cached schedule)


def gate(
    now: datetime,
    *,
    trigger: str,
    cron: str | None,
    dates: SeasonDates | None,
    cfg: PipelineConfig,
) -> Gate:
    """Whether this run goes ahead (see the module docstring)."""
    if trigger not in TRIGGERS:
        raise ValueError(f"trigger must be one of {TRIGGERS}, not {trigger!r}")
    t = _utc(now)
    season_text = "" if dates is None else f"{dates.season} season"
    inside = None if dates is None else in_season(dates, t.date(), cfg)
    if trigger == "manual":
        return Gate(True, "manual run", inside)
    if dates is None:
        return Gate(True, "no cached schedule yet (cold cache): running", None)
    if inside:
        return Gate(
            True, f"in season ({season_text}: {dates.first_game} to {dates.last_game})", True
        )
    role = cron_role(cron, cfg)
    day = WEEKDAY_KEYS[t.weekday()]
    if day == cfg.offseason_weekday and role in ("nightly", None):
        return Gate(True, f"offseason: the weekly run ({cfg.offseason_weekday})", False)
    return Gate(
        False,
        f"offseason ({season_text} runs {dates.first_game} to {dates.last_game} plus the "
        f"window): only the nightly run on {cfg.offseason_weekday.title()}s goes ahead",
        False,
    )


# --------------------------------------------------------------------------------------
# The plan: which week's list is due, and its deadline
# --------------------------------------------------------------------------------------


def attempt_times(as_of: datetime, cfg: PipelineConfig) -> list[datetime]:
    """The retry attempts of the week whose Tuesday as-of is ``as_of``, as UTC times, sorted:
    each ``<weekday> HH:MM`` on the first such day on or after the as-of's date."""
    a = _utc(as_of)
    out = []
    for day, hhmm in cfg.attempts():
        offset = (WEEKDAY_KEYS.index(day) - a.weekday()) % 7
        h, m = _hhmm(hhmm)
        d = a.date() + timedelta(days=offset)
        out.append(datetime(d.year, d.month, d.day, h, m, tzinfo=UTC))
    return sorted(out)


def deadline(as_of: datetime, cfg: PipelineConfig) -> datetime:
    """The week's last retry attempt: from then on, 'not ready' is a failure."""
    return attempt_times(as_of, cfg)[-1]


@dataclass(frozen=True)
class WeekWindow:
    week: int
    as_of: datetime  # aware UTC
    next_kickoff: datetime | None  # the first kickoff of week + 1 (aware UTC)


@dataclass(frozen=True)
class Plan:
    season: int
    week: int | None  # the week to score; None = no list is due
    reason: str
    explicit: bool = False
    as_of: datetime | None = None
    window_end: datetime | None = None
    deadline: datetime | None = None

    def not_ready_fails(self, now: datetime) -> bool:
        """True when a not-ready week is a failure at ``now`` (module docstring)."""
        if self.week is None:
            return False
        if self.explicit or self.deadline is None:
            return True
        return _utc(now) >= self.deadline

    def attempt_text(self, now: datetime) -> str:
        if self.week is None:
            return ""
        if self.explicit:
            return "week named by the operator"
        if self.deadline is None:
            return ""
        if _utc(now) >= self.deadline:
            return f"last attempt: the deadline was {self.deadline:%a %Y-%m-%d %H:%M} UTC"
        return f"early attempt: the last one is {self.deadline:%a %Y-%m-%d %H:%M} UTC"


def plan_week(
    now: datetime,
    season: int,
    windows: Sequence[WeekWindow],
    cfg: PipelineConfig,
    *,
    week: int | None = None,
) -> Plan:
    """Which week's list this run makes (module docstring). ``windows``: the regular-season
    weeks of ``season`` in order, each with its as-of and the next week's first kickoff."""
    t = _utc(now)
    by_week = {w.week: w for w in windows}
    if week is not None:
        w = by_week.get(week)
        return Plan(
            season, week, f"week {week} named by the operator", explicit=True,
            as_of=w.as_of if w else None, window_end=w.next_kickoff if w else None,
            deadline=None,
        )  # fmt: skip
    if not windows:
        return Plan(season, None, f"no regular-season weeks of {season} in the warehouse")
    last_week = max(by_week)
    passed = [w for w in windows if w.as_of <= t]
    if not passed:
        first = min(windows, key=lambda w: w.as_of)
        return Plan(
            season, None,
            f"no list is due yet: the first one is week {first.week}'s, at "
            f"{first.as_of:%a %Y-%m-%d %H:%M} UTC",
        )  # fmt: skip
    cur = max(passed, key=lambda w: w.as_of)
    if cur.week >= last_week:
        return Plan(
            season, None,
            f"week {cur.week} is the last regular-season week: no list follows it",
        )  # fmt: skip
    if cur.next_kickoff is not None and t < cur.next_kickoff:
        return Plan(
            season, cur.week,
            f"week {cur.week}'s list is due (as-of {cur.as_of:%a %Y-%m-%d %H:%M} UTC, live "
            f"until {cur.next_kickoff:%a %Y-%m-%d %H:%M} UTC)",
            as_of=cur.as_of, window_end=cur.next_kickoff, deadline=deadline(cur.as_of, cfg),
        )  # fmt: skip
    nxt = [w for w in windows if w.as_of > t]
    when = f"; the next one is week {nxt[0].week}'s, at {nxt[0].as_of:%a %H:%M} UTC" if nxt else ""
    return Plan(
        season, None,
        f"no list is due: week {cur.week}'s live window closed at the next kickoff{when}",
    )  # fmt: skip


def week_windows(db: Path | str, season: int) -> list[WeekWindow]:
    """The regular-season weeks of ``season`` from the warehouse: as-of (dim_week) and the next
    week's first kickoff (fact_game)."""
    from twm.warehouse.build import connect

    con = connect(db, read_only=True)
    try:
        rows = con.execute(
            """
            WITH k AS (
                SELECT week, min(kickoff_utc) AS first_kickoff FROM fact_game
                WHERE season = ? AND season_type = 'REG' GROUP BY week
            )
            SELECT w.week, w.asof_weekly_utc, k.first_kickoff
            FROM dim_week w LEFT JOIN k ON k.week = w.week + 1
            WHERE w.season = ? AND w.season_type = 'REG' AND w.asof_weekly_utc IS NOT NULL
            ORDER BY w.week
            """,
            [season, season],
        ).fetchall()
    finally:
        con.close()
    return [WeekWindow(int(w), _utc(a), None if k is None else _utc(k)) for w, a, k in rows]
