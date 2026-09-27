"""Config YAMLs load and their values are internally consistent: starter thresholds derive
from the league shape, and season boundaries in settings.yaml match the dataset registry."""

from datetime import date

import nflreadpy

from twm.config import league, scoring, settings
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
