"""Pure-function tests for dim_week rules: kickoff conversion, as-of timestamps, windows."""

from datetime import UTC, date, datetime, time

import pytest

from twm.config import settings
from twm.warehouse import weeks as wk

RULES = wk.AsOfRules()


def _g(season, week, game_type, gameday, gametime=None):
    k, _ = wk.kickoff_utc(gameday, gametime)
    return {
        "season": season,
        "week": week,
        "game_type": game_type,
        "gameday": gameday,
        "kickoff_utc": k,
        "game_end_utc_est": k + wk.GAME_DURATION_EST,
    }


def test_rules_from_config():
    rules = wk.AsOfRules.from_config(settings().as_of)
    assert rules.weekly_weekday == 1 and rules.weekly_time == time(14, 0)
    assert rules.end_of_season_days_after == 1 and rules.end_of_season_time == time(12, 0)


def test_rules_reject_legacy_string_config():
    with pytest.raises(ValueError):
        wk.AsOfRules.from_config(
            {
                "weekly": {"weekday": "tuesday", "time": "14:00"},
                "hot_seat_end_of_season": "day_after_week_18",
            }  # fmt: skip
        )


@pytest.mark.parametrize(
    ("gameday", "gametime", "expected"),
    [
        ("2026-09-10", "20:35", datetime(2026, 9, 11, 0, 35)),  # EDT
        ("2027-01-10", "13:00", datetime(2027, 1, 10, 18, 0)),  # EST
        ("2025-09-28", "09:30", datetime(2025, 9, 28, 13, 30)),  # international
        ("2025-09-15", "22:00", datetime(2025, 9, 16, 2, 0)),  # late kickoff crosses midnight
        ("2025-11-02", "13:00", datetime(2025, 11, 2, 18, 0)),  # DST fall-back Sunday
        ("2025-10-26", "13:00", datetime(2025, 10, 26, 17, 0)),  # last EDT Sunday
    ],
)
def test_kickoff_utc(gameday, gametime, expected):
    k, est = wk.kickoff_utc(gameday, gametime)
    assert k == expected and est is False


@pytest.mark.parametrize(
    ("gameday", "gametime", "weekday", "expected"),
    [
        ("1999-09-12", None, None, datetime(1999, 9, 12, 17, 0)),  # no gametime, Sunday 13:00
        ("1999-09-13", "", "Monday", datetime(1999, 9, 14, 0, 0)),  # empty string, 20:00 ET
        # nflverse 2000-2005 prime-time rows carry '09:00' (a 12-hour-clock placeholder for
        # 21:00/20:30 ET); they take the weekday default and are flagged
        ("2000-09-04", "09:00", None, datetime(2000, 9, 5, 0, 0)),  # Monday night
        ("2005-09-08", "09:00", "Thursday", datetime(2005, 9, 9, 0, 0)),  # Thursday opener
        ("2001-12-22", "09:00", "Saturday", datetime(2001, 12, 22, 21, 30)),  # Saturday 16:30
    ],
)
def test_kickoff_utc_missing_or_placeholder_gametime_is_estimated(
    gameday, gametime, weekday, expected
):
    assert wk.kickoff_utc(gameday, gametime, weekday) == (expected, True)


def test_kickoff_utc_placeholder_boundary_is_strict():
    # 09:30 ET is the earliest real kickoff (London); it must never be treated as missing
    assert wk.kickoff_utc("2025-09-28", "09:30") == (datetime(2025, 9, 28, 13, 30), False)
    assert wk.kickoff_utc("2000-09-04", "09:29", "Monday")[1] is True


def test_implied_totals():
    assert wk.implied_totals(3, 47) == (25, 22)
    assert wk.implied_totals(None, 47) == (None, None)
    # negative line = away favoured; half points survive
    assert wk.implied_totals(-2.5, 44.5) == (21.0, 23.5)


GAME_TYPES = ["REG", "WC", "DIV", "CON", "SB", "SBBYE"]


def test_season_type():
    assert [wk.season_type_of(g) for g in GAME_TYPES] == [
        "REG", "POST", "POST", "POST", "POST", "POST",
    ]  # fmt: skip


@pytest.mark.parametrize("game_type", [*GAME_TYPES, None])
def test_season_type_python_and_sql_agree(game_type):
    # weeks.season_type_of (fact_game, dim_week) and schema.season_type_sql (injuries, legacy
    # depth charts) implement one rule; a change to either must move both.
    import duckdb

    from twm.warehouse import schema as sc

    lit = "NULL" if game_type is None else f"'{game_type}'"
    (sql_result,) = duckdb.execute(f"SELECT {sc.season_type_sql(lit)}").fetchone()
    assert sql_result == wk.season_type_of(game_type)


def test_weekly_asof_is_calendar_anchored():
    # Thursday opener -> Tuesday of the following week
    assert wk.weekly_asof("2025-09-04", RULES) == datetime(2025, 9, 9, 14, 0)
    # Sunday-only week
    assert wk.weekly_asof("2025-09-14", RULES) == datetime(2025, 9, 16, 14, 0)
    # Wednesday opener (2026 W1) -> the Tuesday six days later
    assert wk.weekly_asof("2026-09-09", RULES) == datetime(2026, 9, 15, 14, 0)
    # Saturday wild-card start
    assert wk.weekly_asof("2026-01-10", RULES) == datetime(2026, 1, 13, 14, 0)
    # A Monday first game -> next day
    assert wk.weekly_asof(date(2025, 9, 8), RULES) == datetime(2025, 9, 9, 14, 0)


def test_end_of_regular_season_asof_from_gameday_not_kickoff():
    # SNF Sunday 2026-01-04 20:20 ET ends Monday 01:20 UTC; the rule still says Monday 12:00 UTC
    assert wk.end_of_regular_season_asof("2026-01-04", RULES) == datetime(2026, 1, 5, 12, 0)
    # Monday-night last REG week (2002-12-30 shape) -> Tuesday 12:00 UTC
    assert wk.end_of_regular_season_asof("2002-12-30", RULES) == datetime(2002, 12, 31, 12, 0)


def _season_2020_like():
    """W5 with a Tuesday game (2020 shape), W6 starting Sunday, W12 with a Wednesday game."""
    return [
        _g(2020, 5, "REG", "2020-10-08", "20:20"),
        _g(2020, 5, "REG", "2020-10-11", "13:00"),
        _g(2020, 5, "REG", "2020-10-13", "19:00"),  # Tuesday
        _g(2020, 6, "REG", "2020-10-18", "13:00"),
        _g(2020, 6, "REG", "2020-10-19", "20:15"),
        _g(2020, 12, "REG", "2020-11-26", "12:30"),
        _g(2020, 12, "REG", "2020-12-02", "15:40"),  # Wednesday
        _g(2020, 13, "REG", "2020-12-06", "13:00"),
    ]


def test_dim_week_split_week_asof_stays_before_next_week():
    rows = {r["week"]: r for r in wk.build_dim_week(_season_2020_like(), RULES)}
    w5, w6, w12, w13 = rows[5], rows[6], rows[12], rows[13]
    assert w5["asof_weekly_utc"] == datetime(2020, 10, 13, 14, 0)
    assert w5["asof_weekly_utc"] < w6["first_kickoff_utc"]
    assert w5["n_games_after_asof"] == 1 and w5["is_split_week"] is True
    assert w5["last_game_end_utc_est"] == datetime(2020, 10, 14, 3, 0)
    assert w6["n_games_after_asof"] == 0 and w6["is_split_week"] is False
    assert w12["asof_weekly_utc"] == datetime(2020, 12, 1, 14, 0)
    assert w12["n_games_after_asof"] == 1
    assert w12["asof_weekly_utc"] < w13["first_kickoff_utc"]
    # windows/prev/next run over the whole season by week number
    assert (w5["prev_week"], w5["next_week"]) == (None, 6)
    assert (w6["prev_week"], w6["next_week"]) == (5, 12)
    assert w6["window_start_utc"] == w5["asof_weekly_utc"]
    assert w6["window_end_utc"] == w6["asof_weekly_utc"]


def _season_17_weeks():
    return [
        _g(2019, 16, "REG", "2019-12-22", "13:00"),
        _g(2019, 17, "REG", "2019-12-29", "13:00"),
        _g(2019, 17, "REG", "2019-12-29", "16:25"),
        _g(2019, 18, "WC", "2020-01-04", "16:35"),
        _g(2019, 18, "WC", "2020-01-05", "13:05"),
        _g(2019, 19, "DIV", "2020-01-11", "16:35"),
        _g(2019, 20, "CON", "2020-01-19", "15:05"),
        _g(2019, 21, "SB", "2020-02-02", "18:30"),
    ]


def test_dim_week_last_reg_week_and_playoffs_17_week_season():
    rows = wk.build_dim_week(_season_17_weeks(), RULES)
    by_week = {r["week"]: r for r in rows}
    assert [r["is_last_reg_week"] for r in rows] == [False, True, False, False, False, False]
    assert by_week[17]["asof_end_of_regular_season_utc"] == datetime(2019, 12, 30, 12, 0)
    assert all(r["asof_end_of_regular_season_utc"] is None for r in rows if r["week"] != 17)
    assert by_week[18]["season_type"] == "POST" and by_week[18]["game_type"] == "WC"
    assert by_week[21]["game_type"] == "SB" and by_week[21]["next_week"] is None
    assert by_week[17]["next_week"] == 18 and by_week[18]["prev_week"] == 17
    # Every as-of precedes the next week's first kickoff (point-in-time safety)
    for r in rows:
        if r["next_week"] is not None:
            assert r["asof_weekly_utc"] < by_week[r["next_week"]]["first_kickoff_utc"]


def test_dim_week_monday_night_and_partial_week():
    games = [
        _g(2025, 1, "REG", "2025-09-04", "20:20"),
        _g(2025, 1, "REG", "2025-09-08", "20:15"),  # MNF
        _g(2025, 2, "REG", "2025-09-14", "13:00"),  # results are not an input: see build tests
    ]
    rows = wk.build_dim_week(games, RULES)
    assert rows[0]["asof_weekly_utc"] == datetime(2025, 9, 9, 14, 0)
    assert rows[0]["last_game_end_utc_est"] == datetime(2025, 9, 9, 4, 15)
    assert rows[0]["n_games"] == 2 and rows[1]["n_games"] == 1
    assert rows[1]["asof_weekly_utc"] == datetime(2025, 9, 16, 14, 0)


def test_dim_week_without_kickoff_times_still_gets_asof():
    g = {"season": 1999, "week": 1, "game_type": "REG", "gameday": "1999-09-12",
         "kickoff_utc": None, "game_end_utc_est": None}  # fmt: skip
    (row,) = wk.build_dim_week([g], RULES)
    assert row["asof_weekly_utc"] == datetime(1999, 9, 14, 14, 0)
    assert row["first_kickoff_utc"] is None and row["n_games_after_asof"] == 0


def test_week_for_timestamp_boundaries():
    rows = wk.build_dim_week(_season_17_weeks() + _season_2020_like(), RULES)
    w5_asof = datetime(2020, 10, 13, 14, 0)  # week 5 of the 2020-like season
    assert wk.week_for_timestamp(rows, 2020, w5_asof) == 5  # exactly at the as-of
    assert wk.week_for_timestamp(rows, 2020, datetime(2020, 10, 14, 7, 0)) == 6  # next morning
    assert wk.week_for_timestamp(rows, 2020, datetime(2020, 8, 1)) == 5  # before week 1 window
    # CON -> SB gap of two weeks: the Tuesday between them belongs to the SB week window
    assert wk.week_for_timestamp(rows, 2019, datetime(2020, 1, 28, 12, 0)) == 21
    assert wk.week_for_timestamp(rows, 2019, datetime(2020, 1, 21, 14, 0)) == 20  # CON as-of
    # after the SB as-of -> offseason sentinel (None), never the SB week
    assert wk.week_for_timestamp(rows, 2019, datetime(2020, 2, 4, 14, 0)) == 21
    assert wk.week_for_timestamp(rows, 2019, datetime(2020, 2, 4, 14, 0, 1)) is None
    assert wk.week_for_timestamp(rows, 2021, datetime(2021, 9, 1)) is None  # unknown season
    # aware datetimes are accepted
    assert wk.week_for_timestamp(rows, 2019, datetime(2020, 2, 4, 14, 0, tzinfo=UTC)) == 21
