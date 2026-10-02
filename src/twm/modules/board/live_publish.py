"""When the season's LIVE board is scored (step I6b; docs/offseason.md "The live board").

Config ``as_of.board.live_publish`` (:class:`twm.config.BoardAsOf`) names the as-of of the live
board of season S1 (snapshot S = S1 - 1): ``week1_kickoff_eve`` (default: the preseason anchor
the models learned from, one hour before the first week-1 kickoff of S1), a fixed ``MM-DD`` of
S1 at 00:00 UTC, or ``days_before_week1: N`` (00:00 UTC N days before the kickoff's date). The
window is open from that as-of to the kickoff (a board scored then is 'live'); the features are
read point in time at the as-of, so the week-1 chart is each team's latest daily pull visible
then, which the list's ``note`` names (``board_list.note`` on the site).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

from twm.modules.board import preseason as pre
from twm.modules.board.seasons import SeasonNotOverError

EVE = "week1_kickoff_eve"
DAYS = "days_before_week1:"


def default_rule() -> str:
    from twm.config import settings

    return settings().as_of.board.live_publish


def live_as_of(kickoff: datetime, rule: str) -> datetime:
    """The live board's as-of for the first week-1 ``kickoff`` (aware UTC) under ``rule``."""
    k = kickoff.astimezone(UTC)
    if rule == EVE:
        return k - pre.KICKOFF_EVE_LEAD
    if rule.startswith(DAYS):
        day = k.date() - timedelta(days=int(rule.split(":", 1)[1]))
        return datetime.combine(day, time(0), UTC)
    month, day_ = (int(x) for x in rule.split("-"))
    return datetime(k.year, month, day_, tzinfo=UTC)


@dataclass(frozen=True)
class Window:
    season: int  # the board's season S1
    rule: str
    as_of: datetime | None
    kickoff: datetime | None
    open: bool
    reason: str


def window(db: Path | str | Any, season: int, now: datetime, rule: str | None = None) -> Window:
    """Whether the live board of ``season`` may be scored at ``now`` (:class:`Window`)."""
    rule = rule or default_rule()
    s1, t = int(season), now.astimezone(UTC)
    try:
        kickoff = pre.first_week1_kickoff(db, s1 - 1)
    except SeasonNotOverError:
        return Window(s1, rule, None, None, False, f"no week-1 kickoff of {s1} in the warehouse")
    at = live_as_of(kickoff, rule)
    span = f"{at:%Y-%m-%d %H:%M} UTC ({rule}) to the kickoff {kickoff:%Y-%m-%d %H:%M} UTC"
    if at >= kickoff:
        return Window(s1, rule, at, kickoff, False, f"never open: {span} (the date is too late)")
    if t < at:
        return Window(s1, rule, at, kickoff, False, f"not open yet: {span}")
    if t >= kickoff:
        return Window(s1, rule, at, kickoff, False, f"closed: {s1} has kicked off ({span})")
    return Window(s1, rule, at, kickoff, True, f"open: {span}")


def chart_used(view: Any, season: int) -> tuple[int, datetime | None, datetime | None]:
    """(teams, earliest, latest) of each team's latest daily depth-chart pull of ``season``
    visible in ``view`` (an AsOfView); (0, None, None) when no daily pull is visible (the
    legacy week-1 chart is then used, :func:`twm.modules.board.preseason.week1_chart`)."""
    row = view.sql(f"""
        WITH d AS (SELECT team, max(dt) AS dt FROM fact_depth_chart
                   WHERE season = {int(season)} AND source_format = 'daily'
                     AND team IS NOT NULL GROUP BY team)
        SELECT count(*) AS n, min(dt) AS first, max(dt) AS last FROM d""").row(0)
    return int(row[0]), row[1], row[2]


def note_text(rule: str, as_of: datetime, chart: tuple[int, datetime | None, datetime | None]
              ) -> str:  # fmt: skip
    """The live list's ``note`` (the site shows it): when it was scored and which depth chart."""
    teams, first, last = chart
    when = (f"the kickoff eve, {as_of:%Y-%m-%d %H:%M} UTC" if rule == EVE
            else f"{as_of:%Y-%m-%d %H:%M} UTC ({rule}), earlier than the kickoff eve the models "
            "learned from")  # fmt: skip
    if teams == 0:
        used = "the week-1 depth charts (no daily pull was public then)"
    else:
        span = f"{first:%Y-%m-%d}" if f"{first:%Y-%m-%d}" == f"{last:%Y-%m-%d}" \
            else f"{first:%Y-%m-%d} to {last:%Y-%m-%d}"  # fmt: skip
        used = f"each team's latest daily depth chart then ({teams} teams; pulls of {span})"
    return f"Read as of {when}, from {used}; depth charts can still change before week 1."


def board_note(db: Path | str, season: int, as_of: datetime, rule: str) -> str:
    """:func:`note_text` for the live board of ``season`` read at ``as_of``."""
    from twm.asof import AsOfView

    with AsOfView(db, as_of) as v:
        return note_text(rule, as_of, chart_used(v, int(season)))
