"""The committed schema snapshots in data/schemas are the contract with upstream nflverse."""

import json

import pytest

from twm.sources import nflverse as nv

SNAPSHOT_NAMES = [*nv.DATASETS, "depth_charts_legacy"]


@pytest.mark.parametrize("name", SNAPSHOT_NAMES)
def test_snapshot_exists_and_is_well_formed(name):
    path = nv.snapshot_path(name)
    assert path.exists(), f"missing snapshot {path}; run scripts/verify_sources.py"
    snap = json.loads(path.read_text())
    assert snap["dataset"] == name
    assert snap["nflreadpy_version"]
    assert isinstance(snap["columns"], dict) and len(snap["columns"]) > 5


def test_key_columns_present_in_snapshots():
    """Columns the point-in-time layer and modules depend on (verified in Step A3)."""
    need = {
        "pbp": [
            "game_id",
            "season",
            "week",
            "game_date",
            "posteam",
            "epa",
            "wp",
            "vegas_wp",
            "xpass",
            "cpoe",
            "home_coach",
            "away_coach",
            "spread_line",
            "total_line",
        ],
        "player_stats": [
            "player_id",
            "season",
            "week",
            "position",
            "team",
            "passing_yards",
            "passing_interceptions",
            "sack_fumbles_lost",
            "rushing_fumbles_lost",
            "receiving_fumbles_lost",
            "special_teams_tds",
            "target_share",
            "air_yards_share",
            "wopr",
            "fantasy_points_ppr",
        ],
        "schedules": [
            "game_id",
            "season",
            "week",
            "gameday",
            "gametime",
            "home_coach",
            "away_coach",
            "spread_line",
            "total_line",
            "result",
            "total",
            "home_moneyline",
            "away_moneyline",
            "roof",
            "temp",
            "wind",
        ],
        "snap_counts": [
            "game_id",
            "season",
            "week",
            "pfr_player_id",
            "position",
            "team",
            "offense_snaps",
            "offense_pct",
        ],
        "injuries": ["season", "week", "team", "gsis_id", "report_status", "practice_status"],
        "depth_charts": ["dt", "team", "gsis_id", "pos_grp", "pos_abb", "pos_slot", "pos_rank"],
        "depth_charts_legacy": [
            "season",
            "week",
            "club_code",
            "depth_team",
            "gsis_id",
            "position",
            "depth_position",
            "formation",
        ],
        "rosters_weekly": [
            "season",
            "week",
            "team",
            "gsis_id",
            "pfr_id",
            "espn_id",
            "status",
            "birth_date",
            "draft_number",
            "entry_year",
        ],
        "ff_opportunity": [
            "season",
            "week",
            "player_id",
            "posteam",
            "position",
            "total_fantasy_points_exp",
            "total_fantasy_points",
            "rec_yards_gained_exp",
            "rec_touchdown_exp",
            "receptions_exp",
            "rush_yards_gained_exp",
            "rush_touchdown_exp",
            "pass_yards_gained_exp",
            "pass_touchdown_exp",
            "pass_interception_exp",
        ],
        "ff_playerids": [
            "gsis_id",
            "espn_id",
            "pfr_id",
            "sleeper_id",
            "fantasypros_id",
            "name",
            "position",
            "birthdate",
        ],
        "ff_rankings_all": ["ecr_type", "page_type", "id", "pos", "ecr", "sd", "scrape_date"],
    }
    for name, cols in need.items():
        snap = json.loads(nv.snapshot_path(name).read_text())["columns"]
        missing = [c for c in cols if c not in snap]
        assert not missing, f"{name}: {missing}"


@pytest.mark.network
def test_live_current_season_matches_snapshot():
    """Downloads the current schedules file and checks it against the snapshot."""
    nv.fetch("schedules", nv.settings().current_season, force=True)
