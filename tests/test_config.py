"""Config YAMLs load and their values are internally consistent: starter thresholds derive
from the league shape, and season boundaries in settings.yaml match the dataset registry."""

from datetime import date

import nflreadpy
import pytest
import yaml
from pydantic import ValidationError

from twm.config import CONFIG_DIR, AvailabilityConfig, League, league, scoring, settings
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
    assert lg.starter_thresholds()["QB"] == lg.teams * lg.lineup["QB"]
    assert lg.starter_thresholds()["RB"] == lg.teams * lg.lineup["RB"]
    assert lg.flex_worthy_ranks()["RB"] == lg.teams * (lg.lineup["RB"] + lg.lineup["FLEX"])
    # the shipped league.yaml derives everything: no explicit numbers to keep in sync
    assert lg.starter_rank_threshold is None and lg.flex_worthy_rank is None
    assert lg.shape.overridden == ()


# ---- League shape: every number derives from teams + lineup (the owner's requirement) ----


def _league(**change) -> League:
    """The shipped league.yaml with some keys changed (None removes a key)."""
    raw = yaml.safe_load((CONFIG_DIR / "league.yaml").read_text())
    for k, v in change.items():
        if v is None:
            raw.pop(k, None)
        else:
            raw[k] = v
    return League(**raw)


LINEUP = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1, "bench": 7}
SUPERFLEX = {"FLEX": ["RB", "WR", "TE"], "SUPERFLEX": ["QB", "RB", "WR", "TE"]}


@pytest.mark.parametrize(
    ("change", "thresholds", "flex", "cutoffs"),
    [
        # the default (owner's) league: the old hard-coded numbers, unchanged
        ({}, (12, 24, 24, 12), {"RB": 36, "WR": 36}, (18, 36, 36, 18)),
        ({"teams": 10}, (10, 20, 20, 10), {"RB": 30, "WR": 30}, (15, 30, 30, 15)),
        ({"teams": 14}, (14, 28, 28, 14), {"RB": 42, "WR": 42}, (21, 42, 42, 21)),
        # 11 teams: 11 x 1.5 = 16.5 rounds half up to 17 (Python's round() would give 16)
        ({"teams": 11}, (11, 22, 22, 11), {"RB": 33, "WR": 33}, (17, 33, 33, 17)),
        # a 3-WR lineup: WR 12 x 3 = 36; FLEX-worthy WR 12 x (3 + 1) = 48
        ({"lineup": {**LINEUP, "WR": 3}}, (12, 24, 36, 12), {"RB": 36, "WR": 48},
         (18, 36, 54, 18)),
        # superflex: the dedicated thresholds stay; QB is FLEX-worthy to 12 x (1 + 1) = 24 and
        # RB/WR count both multi-position slots (an upper bound: 12 x (2 + 1 + 1) = 48)
        ({"lineup": {**LINEUP, "SUPERFLEX": 1}, "slot_eligibility": SUPERFLEX},
         (12, 24, 24, 12), {"QB": 24, "RB": 48, "WR": 48}, (18, 36, 36, 18)),
        # two TE slots: TE 12 x 2 = 24; TE still has no FLEX-worthy rank (not in the list)
        ({"lineup": {**LINEUP, "TE": 2}}, (12, 24, 24, 24), {"RB": 36, "WR": 36},
         (18, 36, 36, 36)),
        # TE opted in: 12 x (1 + 1) = 24
        ({"flex_worthy_positions": ["RB", "WR", "TE"]}, (12, 24, 24, 12),
         {"RB": 36, "WR": 36, "TE": 24}, (18, 36, 36, 18)),
        # no FLEX slot at all: no FLEX-worthy ranks
        ({"lineup": {k: v for k, v in LINEUP.items() if k != "FLEX"}}, (12, 24, 24, 12), {},
         (18, 36, 36, 18)),
        # a bigger pool multiplier
        ({"candidate_pool_multiplier": 2.0}, (12, 24, 24, 12), {"RB": 36, "WR": 36},
         (24, 48, 48, 24)),
        # a one-position "flex" slot is a dedicated slot (RB2: [RB] = a third RB)
        ({"lineup": {**LINEUP, "RB2": 1}, "slot_eligibility": {"FLEX": ["RB", "WR", "TE"],
                                                               "RB2": ["RB"]}},
         (12, 36, 24, 12), {"RB": 48, "WR": 36}, (18, 54, 36, 18)),
        # K, DST, IR and an IDP flex are accepted and ignored
        ({"lineup": {**LINEUP, "IR": 2, "IDP": 1}, "slot_eligibility": {
            "FLEX": ["RB", "WR", "TE"], "IDP": ["DL", "LB", "DB"]}},
         (12, 24, 24, 12), {"RB": 36, "WR": 36}, (18, 36, 36, 18)),
    ],
)  # fmt: skip
def test_league_shapes_derive_thresholds_and_cutoffs(change, thresholds, flex, cutoffs):
    lg = _league(**change)
    positions = ("QB", "RB", "WR", "TE")
    assert lg.starter_thresholds() == dict(zip(positions, thresholds, strict=True))
    assert lg.flex_worthy_ranks() == flex
    assert lg.candidate_pool_cutoffs() == dict(zip(positions, cutoffs, strict=True))
    assert lg.shape.size_text == f"{lg.teams}-team"
    text = " ".join(lg.shape.describe())
    assert f"{lg.teams} teams" in text and "12-team" not in text


def test_league_overrides_are_optional_and_reported():
    lg = _league(starter_rank_threshold={"QB": 24}, flex_worthy_rank=40)
    assert lg.starter_thresholds() == {"QB": 24, "RB": 24, "WR": 24, "TE": 12}
    assert lg.candidate_pool_cutoffs()["QB"] == 36
    assert lg.flex_worthy_ranks() == {"RB": 40, "WR": 40}
    assert lg.shape.overridden == (
        "starter_rank_threshold.QB", "flex_worthy_rank.RB", "flex_worthy_rank.WR",
    )  # fmt: skip
    assert "explicit overrides" in lg.shape.describe()[-1]
    per_pos = _league(flex_worthy_rank={"WR": 30})
    assert per_pos.flex_worthy_ranks() == {"RB": 36, "WR": 30}
    # an override is frozen: it no longer follows teams
    assert _league(teams=10, starter_rank_threshold={"QB": 12}).starter_thresholds()["QB"] == 12


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"teams": 1}, "between 2 and 32"),
        ({"teams": 40}, "between 2 and 32"),
        ({"lineup": {**LINEUP, "WR": -1}}, "whole number >= 0"),
        ({"lineup": {**LINEUP, "FLX": 1}}, "lineup.FLX: unknown slot"),
        ({"lineup": {**LINEUP, "SUPERFLEX": 1}}, "lineup.SUPERFLEX: unknown slot"),
        ({"slot_eligibility": {"FLEX": []}}, "non-empty list"),
        ({"slot_eligibility": {"FLEX": ["RB", "WRR"]}}, "unknown"),
        ({"slot_eligibility": {"FLEX": ["RB", "RB"]}}, "twice"),
        ({"slot_eligibility": {"FLEX": ["RB", "WR"], "WR": ["WR", "TE"]}}, "not a multi"),
        ({"lineup": {k: v for k, v in LINEUP.items() if k != "TE"}}, "TE has no starting slot"),
        ({"lineup": {**LINEUP, "QB": 0}}, "QB has no starting slot"),
        ({"candidate_pool_multiplier": 0.8}, "at least 1"),
        ({"candidate_pool_multiplier": -1}, "must be positive"),
        ({"starter_rank_threshold": {"K": 12}}, "unknown positions"),
        ({"starter_rank_threshold": {"QB": 0}}, "at least 1"),
        ({"flex_worthy_rank": {"TE": 24}}, "no FLEX-worthy rank to override"),
        ({"flex_worthy_rank": 10}, "below its starter threshold"),
        ({"lineup": {k: v for k, v in LINEUP.items() if k != "FLEX"}, "flex_worthy_rank": 36},
         "no multi-position slot"),
        ({"flex_worthy_positions": ["RB", "K"]}, "flex_worthy_positions"),
        ({"teams_typo": 12}, "Extra inputs"),
    ],
)  # fmt: skip
def test_invalid_league_shapes_are_refused_clearly(change, message):
    with pytest.raises(ValidationError, match=message):
        _league(**change)


def test_config_dir_override_and_reload(tmp_path, monkeypatch):
    """$TWM_CONFIG_DIR points every loader at another folder (a what-if league); reload()
    forgets the cached config and rebuilds the registry texts that quote it."""
    from twm import config as cfg
    from twm import registry

    for name in cfg.CONFIG_FILES:
        (tmp_path / name).write_text((CONFIG_DIR / name).read_text())
    raw = yaml.safe_load((tmp_path / "league.yaml").read_text())
    (tmp_path / "league.yaml").write_text(yaml.safe_dump({**raw, "teams": 10}))
    monkeypatch.setenv(cfg.CONFIG_DIR_ENV, str(tmp_path))
    try:
        cfg.reload()
        assert cfg.config_dir() == tmp_path
        assert cfg.league().teams == 10 and cfg.league().starter_thresholds()["WR"] == 20
        assert "10-team" in registry.get("candidate_pool").explanation
        assert "12-team" not in registry.glossary_markdown()
    finally:
        monkeypatch.delenv(cfg.CONFIG_DIR_ENV)
        cfg.reload()
    assert cfg.league().teams == 12 and "12-team" in registry.get("candidate_pool").explanation


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


def test_as_of_block_is_typed_and_validated_at_load():
    """settings.yaml `as_of` is a typed model (validated when the config loads, not only when
    the warehouse builds)."""
    from twm.config import AsOfConfig

    a = settings().as_of
    assert isinstance(a, AsOfConfig)
    assert (a.weekly.weekday, a.weekly.time) == ("tuesday", "14:00")
    assert (a.hot_seat_end_of_season.days_after, a.hot_seat_end_of_season.time) == (1, "12:00")
    good = a.model_dump()
    eos = good["hot_seat_end_of_season"]
    bad_cases = [
        ({"weekly": {"weekday": "tuesdy", "time": "14:00"}}, "not a weekday name"),
        ({"weekly": {"weekday": "tuesday", "time": "2pm"}}, "HH:MM"),
        ({"weekly": {"weekday": "tuesday", "time": "14:00", "zone": "UTC"}}, "Extra inputs"),
        ({"hot_seat_end_of_season": "day_after_week_18"}, "must be a mapping"),
        ({"hot_seat_end_of_season": {**eos, "anchor": "last_game"}}, "last_reg_week"),
        ({"hot_seat_end_of_season": {**eos, "days_after": 30}}, "0-14"),
        ({"board": {"post_draft": "06-31", "preseason": "tuesday_before_week_1"}}, "MM-DD"),
        ({"typo": 1}, "Extra inputs"),
    ]
    for change, message in bad_cases:
        with pytest.raises(ValidationError, match=message):
            AsOfConfig(**{**good, **change})
    # "Tuesday" is accepted and normalized
    assert (
        AsOfConfig(**{**good, "weekly": {"weekday": "Tuesday", "time": "14:00"}}).weekly.weekday
        == "tuesday"
    )
