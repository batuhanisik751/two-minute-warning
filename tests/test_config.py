"""Config YAMLs load and their values are internally consistent: starter thresholds derive
from the league shape, and season boundaries in settings.yaml match the dataset registry."""

from datetime import date

import nflreadpy
import pytest
from pydantic import ValidationError

from twm.config import AvailabilityConfig, league, scoring, settings
from twm.sources import nflverse as nv


def test_settings_load():
    s = settings()
    # nflreadpy.get_current_season() is a pure date computation (Labor-Day rule, no network).
    # Between March 15 and the season opener the "roster year" is one ahead of the "season
    # year", and the config may legitimately hold either.
    assert s.current_season in {
        nflreadpy.get_current_season(),
        nflreadpy.get_current_season(roster=True),
    }
    assert 2020 <= s.current_season <= date.today().year + 1
    assert s.horizons["waiver_radar_weeks"] == 3


def test_season_boundaries_match_dataset_registry():
    """settings.yaml and DATASETS must agree on where coverage starts (A3: snaps start 2013)."""
    s = settings()
    assert s.seasons["snaps_start"] == nv.DATASETS["snap_counts"].first_season
    assert s.seasons["pbp_start"] == nv.DATASETS["pbp"].first_season


def test_scoring_is_full_ppr():
    sc = scoring()
    assert sc.receiving["receptions"] == 1
    assert sc.passing["yards"] == 0.04


def test_starter_thresholds_match_league_shape():
    lg = league()
    assert lg.starter_rank_threshold["QB"] == lg.teams * lg.lineup["QB"]
    assert lg.starter_rank_threshold["RB"] == lg.teams * lg.lineup["RB"]
    assert lg.flex_worthy_rank == lg.teams * (lg.lineup["RB"] + lg.lineup["FLEX"])


def test_availability_block_loads_and_is_validated():
    a = settings().availability
    assert a.game_data_lag_hours == {"pbp": 6, "player_stats": 6, "team_stats": 6,
                                     "snap_counts": 6}  # fmt: skip
    assert a.game_result_lag_hours == 3
    assert a.schedule_release_month_day == "05-20"
    assert a.schedule_slot_lead_days == 12
    assert a.estimated_kickoff_for_availability_et == {"Monday": "21:00", "default": "20:30"}
    assert a.injury_legacy_stamp_offset_hours == 9
    assert a.draft_public_month_day == "05-15"
    ids = {x.game_id for x in a.schedule_exceptions}
    assert {"2017_11_TB_MIA", "2023_19_PIT_BUF", "2022_17_BUF_CIN"} <= ids
    assert [x.game_id for x in a.schedule_exceptions if x.cancelled] == ["2022_17_BUF_CIN"]
    # No announcement date is typed from memory: the shipped list relies on the kickoff rule.
    assert all(x.announced is None and x.source is None for x in a.schedule_exceptions)
    good = a.model_dump()
    slots = good["estimated_kickoff_for_availability_et"]
    exc = good["schedule_exceptions"]
    bad_cases = [
        ({"game_data_lag_hours": {"pbp": 6}}, "exactly the keys"),
        ({"game_data_lag_hours": {**good["game_data_lag_hours"], "pbp": -1}}, "between 0"),
        ({"game_data_lag_hours": {**good["game_data_lag_hours"], "pbp": 100}}, "between 0"),
        ({"game_result_lag_hours": -1}, "between 0"),
        ({"injury_legacy_stamp_offset_hours": 49}, "between 0"),
        ({"schedule_slot_lead_days": 90}, "between 0 and 60"),
        ({"schedule_release_month_day": "02-30"}, "MM-DD"),
        ({"schedule_release_month_day": "May 20"}, "MM-DD"),
        ({"draft_public_month_day": "13-01"}, "MM-DD"),
        ({"estimated_kickoff_for_availability_et": {**slots, "Monday": "9pm"}}, "HH:MM"),
        ({"estimated_kickoff_for_availability_et": {**slots, "default": "25:00"}}, "HH:MM"),
        ({"estimated_kickoff_for_availability_et": {"Monday": "21:00"}}, "default"),
        ({"estimated_kickoff_for_availability_et": {**slots, "Mon": "21:00"}}, "unknown keys"),
        ({"schedule_exceptions": [*exc, exc[0]]}, "more than once"),
        ({"schedule_exceptions": [{**exc[0], "announced": "2017-13-01"}]}, "YYYY-MM-DD"),
        ({"schedule_exceptions": [{**exc[0], "announced": "2017-09-07"}]}, "needs a source URL"),
        (
            {"schedule_exceptions": [{**exc[0], "announced": "2017-09-07", "source": "memory"}]},
            "needs a source URL",
        ),
        ({"typo_key": 1}, "Extra inputs"),
    ]
    for change, message in bad_cases:
        with pytest.raises(ValidationError, match=message):
            AvailabilityConfig(**{**good, **change})
