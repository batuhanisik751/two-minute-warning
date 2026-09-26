from twm.config import league, scoring, settings


def test_settings_load():
    s = settings()
    assert s.current_season == 2026
    assert s.horizons["waiver_radar_weeks"] == 3


def test_scoring_is_full_ppr():
    sc = scoring()
    assert sc.receiving["receptions"] == 1
    assert sc.passing["yards"] == 0.04


def test_starter_thresholds_match_league_shape():
    lg = league()
    assert lg.starter_rank_threshold["QB"] == lg.teams * lg.lineup["QB"]
    assert lg.starter_rank_threshold["RB"] == lg.teams * lg.lineup["RB"]
    assert lg.flex_worthy_rank == lg.teams * (lg.lineup["RB"] + lg.lineup["FLEX"])
