"""The scheduled pipeline's calendar (step E4): in season or not, which week's list is due,
which attempt is the last one. Pure functions; no real clock."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest

from twm.config import PipelineConfig, settings
from twm.pipeline import schedule as sc

CFG = PipelineConfig()  # the defaults equal config/settings.yaml (checked below)
DATES = sc.SeasonDates(2026, date(2026, 9, 10), date(2027, 1, 10))


def t(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def windows(n: int = 18) -> list[sc.WeekWindow]:
    """Tuesday 14:00 as-ofs from 2026-09-15; next week's first kickoff Friday 00:15 UTC."""
    out = []
    for w in range(1, n + 1):
        a = t("2026-09-15T14:00") + timedelta(days=7 * (w - 1))
        k = None if w == n else a + timedelta(days=2, hours=10, minutes=15)
        out.append(sc.WeekWindow(w, a, k))
    return out


def test_the_defaults_are_the_committed_config() -> None:
    assert settings().pipeline == CFG


# --------------------------------------------------------------------------------------
# Season window and gate
# --------------------------------------------------------------------------------------


def test_season_dates_from_a_schedule() -> None:
    df = pl.DataFrame({
        "season": [2026, 2026, 2026, 2026, 2025],
        "game_type": ["REG", "REG", "WC", "REG", "REG"],
        "gameday": ["2026-09-10", "2027-01-10", "2027-01-16", "2026-10-01", "2025-09-04"],
    })  # fmt: skip
    assert sc.season_dates(df, 2026) == DATES
    assert sc.season_dates(df, 2024) is None
    assert sc.season_dates(df.drop("gameday"), 2026) is None


@pytest.mark.parametrize(
    ("day", "inside"),
    [("2026-09-02", False), ("2026-09-03", True), ("2026-12-01", True), ("2027-01-20", True),
     ("2027-01-21", False), ("2027-06-01", False)],
)  # fmt: skip
def test_in_season_window(day: str, inside: bool) -> None:
    # 7 days before the first game day, 10 after the last one
    assert sc.in_season(DATES, date.fromisoformat(day), CFG) is inside


def test_gate_in_season_every_scheduled_run_goes_ahead() -> None:
    for cron in (sc.nightly_cron(CFG), *sc.retry_crons(CFG)):
        g = sc.gate(t("2026-10-06T15:17"), trigger="schedule", cron=cron, dates=DATES, cfg=CFG)
        assert g.run and g.in_season


def test_gate_offseason_runs_once_a_week() -> None:
    tuesday, wednesday = t("2027-03-02T10:47"), t("2027-03-03T10:47")
    nightly, retry = sc.nightly_cron(CFG), sc.retry_crons(CFG)[0]
    assert sc.gate(tuesday, trigger="schedule", cron=nightly, dates=DATES, cfg=CFG).run
    g = sc.gate(wednesday, trigger="schedule", cron=nightly, dates=DATES, cfg=CFG)
    assert not g.run and g.in_season is False and "Tuesdays" in g.reason
    # the Tuesday retry crons do not add offseason runs
    assert not sc.gate(tuesday, trigger="schedule", cron=retry, dates=DATES, cfg=CFG).run
    # a manual run always goes ahead; so does a run without a cached schedule
    assert sc.gate(wednesday, trigger="manual", cron=None, dates=DATES, cfg=CFG).run
    cold = sc.gate(wednesday, trigger="schedule", cron=nightly, dates=None, cfg=CFG)
    assert cold.run and cold.in_season is None
    with pytest.raises(ValueError, match="trigger"):
        sc.gate(wednesday, trigger="push", cron=None, dates=DATES, cfg=CFG)


def test_cron_lines_follow_the_config() -> None:
    assert sc.nightly_cron(CFG) == "47 10 * * *"
    assert sc.retry_crons(CFG) == ["17 15 * * 2", "47 18 * * 2", "17 21 * * 2", "17 3 * * 3"]
    assert sc.cron_role("47  10 * * *", CFG) == "nightly"
    assert sc.cron_role("17 3 * * 3", CFG) == "retry"
    assert sc.cron_role("0 0 * * *", CFG) is None and sc.cron_role(None, CFG) is None


# --------------------------------------------------------------------------------------
# Attempts and the deadline
# --------------------------------------------------------------------------------------


def test_attempt_times_and_the_last_one() -> None:
    a = t("2026-09-29T14:00")  # a Tuesday
    got = sc.attempt_times(a, CFG)
    assert got == [t("2026-09-29T15:17"), t("2026-09-29T18:47"), t("2026-09-29T21:17"),
                   t("2026-09-30T03:17")]  # fmt: skip
    assert sc.deadline(a, CFG) == t("2026-09-30T03:17")
    # every attempt lies after the as-of and before the next week's first kickoff (Thursday
    # night) in the committed config
    assert all(a < x < a + timedelta(days=2) for x in got)


@pytest.mark.parametrize(
    ("now", "week", "fails"),
    [
        ("2026-09-29T15:17", 3, False),  # first attempt: a warning
        ("2026-09-29T21:17", 3, False),
        ("2026-09-30T03:16", 3, False),  # a minute before the last attempt
        ("2026-09-30T03:17", 3, True),  # the last attempt: a failure (GitHub emails)
        ("2026-09-30T05:40", 3, True),  # the last attempt, delayed by GitHub
        ("2026-10-01T10:47", 3, True),  # a later nightly run in the window
    ],
)
def test_not_ready_is_a_failure_from_the_last_attempt_on(now: str, week: int, fails: bool) -> None:
    p = sc.plan_week(t(now), 2026, windows(), CFG)
    assert p.week == week
    assert p.not_ready_fails(t(now)) is fails
    assert p.attempt_text(t(now)).startswith("last attempt" if fails else "early attempt")


# --------------------------------------------------------------------------------------
# Which week's list is due
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "week"),
    [
        ("2026-09-15T13:59", None),  # before the first as-of
        ("2026-09-15T14:00", 1),  # at week 1's as-of
        ("2026-09-18T00:14", 1),  # just before week 2's first kickoff
        ("2026-09-18T00:15", None),  # the window closed: no reconstructed list is made
        ("2026-09-22T10:47", None),  # Tuesday's nightly run comes before the as-of
        ("2026-09-22T15:17", 2),
        ("2027-01-12T15:00", None),  # week 18: the last regular-season week has no list
    ],
)
def test_the_week_whose_list_is_due(now: str, week: int | None) -> None:
    p = sc.plan_week(t(now), 2026, windows(), CFG)
    assert p.week == week, p.reason
    if week is None:
        assert not p.not_ready_fails(t(now))


def test_an_operator_named_week() -> None:
    p = sc.plan_week(t("2026-10-20T12:00"), 2026, windows(), CFG, week=2)
    assert p.week == 2 and p.explicit and p.as_of == t("2026-09-22T14:00")
    assert p.not_ready_fails(t("2026-10-20T12:00"))  # it was asked for: not ready fails
    assert sc.plan_week(t("2026-10-20T12:00"), 2026, [], CFG).week is None


def test_config_validation() -> None:
    with pytest.raises(ValueError, match="weekday"):
        PipelineConfig(retry_attempts=["someday 15:00"])
    with pytest.raises(ValueError, match="HH:MM"):
        PipelineConfig(nightly="25:00")
    with pytest.raises(ValueError, match="at least one"):
        PipelineConfig(retry_attempts=[])
    assert PipelineConfig(retry_attempts=["Tuesday 15:00"]).attempts() == [("tuesday", "15:00")]
