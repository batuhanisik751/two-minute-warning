"""Shared fixtures: a synthetic Parquet cache for warehouse tests (offline, tiny)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from twm import ids
from twm.sources import nflverse as nv

TEAMS_36 = [
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB", "HOU",
    "IND", "JAX", "KC", "LA", "LAC", "LAR", "LV", "MIA", "MIN", "NE", "NO", "NYG", "NYJ", "OAK",
    "PHI", "PIT", "SD", "SEA", "SF", "STL", "TB", "TEN", "WAS",
]  # fmt: skip

SCHEDULE_DTYPES: dict[str, pl.DataType] = {
    "game_id": pl.String(),
    "season": pl.Int32(),
    "game_type": pl.String(),
    "week": pl.Int32(),
    "gameday": pl.String(),
    "weekday": pl.String(),
    "gametime": pl.String(),
    "away_team": pl.String(),
    "away_score": pl.Int32(),
    "home_team": pl.String(),
    "home_score": pl.Int32(),
    "result": pl.Int32(),
    "total": pl.Int32(),
    "spread_line": pl.Float64(),
    "total_line": pl.Float64(),
    "away_coach": pl.String(),
    "home_coach": pl.String(),
    "gsis": pl.Int32(),
}

PBP_DTYPES: dict[str, pl.DataType] = {
    "game_id": pl.String(),
    "play_id": pl.Float64(),
    "season": pl.Int32(),
    "week": pl.Int32(),
    "season_type": pl.String(),
    "game_date": pl.String(),
    "posteam": pl.String(),
    "defteam": pl.String(),
    "home_team": pl.String(),
    "away_team": pl.String(),
    "home_coach": pl.String(),
    "away_coach": pl.String(),
    "qtr": pl.Float64(),
    "down": pl.Float64(),
    "goal_to_go": pl.Float64(),
    "desc": pl.String(),
    "pass": pl.Float64(),
    "epa": pl.Float64(),
    "wp": pl.Float64(),
    "half_seconds_remaining": pl.Float64(),
    "score_differential": pl.Float64(),
    "xyac_median_yardage": pl.Int32(),
    # integer-typed in the warehouse but DOUBLE upstream: exercises the DOUBLE -> INTEGER cast
    "yards_gained": pl.Float64(),
    "air_yards": pl.Float64(),
    "kick_distance": pl.Float64(),
    "penalty_yards": pl.Float64(),
}

PLAYER_STATS_DTYPES: dict[str, pl.DataType] = {
    "player_id": pl.String(),
    "player_name": pl.String(),
    "position": pl.String(),
    "season": pl.Int32(),
    "week": pl.Int32(),
    "season_type": pl.String(),
    "game_id": pl.String(),
    "team": pl.String(),
    "opponent_team": pl.String(),
    "receptions": pl.Int32(),
    "fantasy_points_ppr": pl.Float64(),
}

TEAM_STATS_DTYPES: dict[str, pl.DataType] = {
    "season": pl.Int32(),
    "week": pl.Int32(),
    "team": pl.String(),
    "season_type": pl.String(),
    "game_id": pl.String(),
    "opponent_team": pl.String(),
    "passing_epa": pl.Float64(),
}

SNAP_DTYPES: dict[str, pl.DataType] = {
    "game_id": pl.String(),
    "season": pl.Int32(),
    "game_type": pl.String(),
    "week": pl.Int32(),
    "player": pl.String(),
    "pfr_player_id": pl.String(),
    "position": pl.String(),
    "team": pl.String(),
    "opponent": pl.String(),
    "offense_snaps": pl.Float64(),
    "offense_pct": pl.Float64(),
}

INJURY_DTYPES: dict[str, pl.DataType] = {
    "season": pl.Int32(),
    "season_type": pl.String(),
    "game_type": pl.String(),
    "team": pl.String(),
    "week": pl.Int32(),
    "gsis_id": pl.String(),
    "position": pl.String(),
    "full_name": pl.String(),
    "report_status": pl.String(),
    "practice_status": pl.String(),
}

LEGACY_DC_DTYPES: dict[str, pl.DataType] = {
    "season": pl.Int32(),
    "club_code": pl.String(),
    "week": pl.Int32(),
    "game_type": pl.String(),
    "depth_team": pl.String(),
    "last_name": pl.String(),
    "first_name": pl.String(),
    "formation": pl.String(),
    "gsis_id": pl.String(),
    "position": pl.String(),
    "depth_position": pl.String(),
    "full_name": pl.String(),
}

DAILY_DC_DTYPES: dict[str, pl.DataType] = {
    "dt": pl.String(),
    "team": pl.String(),
    "player_name": pl.String(),
    "espn_id": pl.String(),
    "gsis_id": pl.String(),
    "pos_grp": pl.String(),
    "pos_name": pl.String(),
    "pos_abb": pl.String(),
    "pos_slot": pl.Int32(),
    "pos_rank": pl.Int32(),
}

PLAYERS_DTYPES: dict[str, pl.DataType] = {
    "gsis_id": pl.String(),
    "display_name": pl.String(),
    "position": pl.String(),
    "position_group": pl.String(),
    "birth_date": pl.String(),
    "rookie_season": pl.Int32(),
    "draft_year": pl.Int32(),
    "draft_round": pl.Int32(),
    "draft_pick": pl.Int32(),
    "draft_team": pl.String(),
    "pfr_id": pl.String(),
    "latest_team": pl.String(),
}


# ffopportunity weekly (fact_opportunity_week, C3): upstream season is a String and week a
# Float64 in every season. Only the columns tests set; the rest are NULL in the warehouse.
OPPORTUNITY_DTYPES: dict[str, pl.DataType] = {
    "season": pl.String(),
    "posteam": pl.String(),
    "week": pl.Float64(),
    "game_id": pl.String(),
    "player_id": pl.String(),
    "full_name": pl.String(),
    "position": pl.String(),  # the id report reads it from the raw file
    "rec_attempt": pl.Float64(),
    "rush_attempt": pl.Float64(),
    "receptions": pl.Float64(),
    "receptions_exp": pl.Float64(),
    "rec_yards_gained": pl.Float64(),
    "rec_yards_gained_exp": pl.Float64(),
    "rec_touchdown_exp": pl.Float64(),
    "rush_yards_gained_exp": pl.Float64(),
    "rush_touchdown_exp": pl.Float64(),
    "pass_yards_gained_exp": pl.Float64(),
    "pass_touchdown_exp": pl.Float64(),
    "pass_interception_exp": pl.Float64(),
    "total_fantasy_points": pl.Float64(),
    "total_fantasy_points_exp": pl.Float64(),
}

OPPORTUNITY_FIRST_SEASON = 2006


# ff_playerids (DynastyProcess) and weekly rosters: the id sources of the B3 bridge. Every id in
# the fixtures is synthetic (9xxxxx, 8xxxxx ... numbers, "OneAl00"-style PFR slugs).
FF_PLAYERIDS_DTYPES: dict[str, pl.DataType] = {
    "mfl_id": pl.Int64(),
    "sportradar_id": pl.String(),
    "fantasypros_id": pl.Int64(),
    "gsis_id": pl.String(),
    "pff_id": pl.Int64(),
    "sleeper_id": pl.Int64(),
    "nfl_id": pl.String(),
    "espn_id": pl.Int64(),
    "yahoo_id": pl.String(),
    "pfr_id": pl.String(),
    "name": pl.String(),
    "position": pl.String(),
    "birthdate": pl.String(),
    "draft_year": pl.Int64(),
}

ROSTERS_WEEKLY_DTYPES: dict[str, pl.DataType] = {
    "season": pl.Int32(),
    "week": pl.Int32(),
    "team": pl.String(),
    "position": pl.String(),
    "full_name": pl.String(),
    "gsis_id": pl.String(),
    "espn_id": pl.String(),
    "sportradar_id": pl.String(),
    "yahoo_id": pl.String(),
    "pfr_id": pl.String(),
    "sleeper_id": pl.String(),
    # C1 (fact_roster_week): NULL unless a test sets them
    "game_type": pl.String(),
    "status": pl.String(),
    "entry_year": pl.Int32(),
    "rookie_year": pl.Int32(),
    "draft_number": pl.String(),  # a string in some real seasons: exercises the cast
}

# FantasyPros rankings (ff_rankings_all, one file): the C1 fact_ranking source. Synthetic ids
# (9xxxxx) and names.
RANKINGS_ALL_DTYPES: dict[str, pl.DataType] = {
    "fp_page": pl.String(),
    "page_type": pl.String(),
    "player": pl.String(),
    "id": pl.String(),
    "pos": pl.String(),
    "team": pl.String(),
    "ecr": pl.Float64(),
    "sd": pl.Float64(),
    "player_owned_avg": pl.Float64(),
    "player_owned_espn": pl.Float64(),
    "ecr_type": pl.String(),
    "scrape_date": pl.String(),
}


def frame(rows: list[Mapping[str, Any]], dtypes: Mapping[str, pl.DataType]) -> pl.DataFrame:
    """Build a typed frame from partial row dicts (missing keys -> NULL)."""
    cols = list(dtypes)
    for r in rows:
        unknown = set(r) - set(cols)
        if unknown:
            raise KeyError(f"fixture row has columns outside the dtype map: {unknown}")
    data = {c: [r.get(c) for r in rows] for c in cols}
    return pl.DataFrame(data, schema=dict(dtypes), strict=False)


def game(
    season: int,
    week: int,
    gameday: str,
    gametime: str | None,
    away: str,
    home: str,
    *,
    game_type: str = "REG",
    result: int | None = None,
    spread: float | None = 3.0,
    total: float | None = 47.0,
    home_coach: str | None = "Home Coach",
    away_coach: str | None = "Away Coach",
) -> dict[str, Any]:
    day = datetime.strptime(gameday, "%Y-%m-%d")
    return {
        "game_id": f"{season}_{week:02d}_{away}_{home}",
        "season": season,
        "game_type": game_type,
        "week": week,
        "gameday": gameday,
        "weekday": day.strftime("%A"),
        "gametime": gametime,
        "away_team": away,
        "home_team": home,
        "home_score": None if result is None else 20 + result,
        "away_score": None if result is None else 20,
        "result": result,
        "total": None if result is None else 40 + result,
        "spread_line": spread,
        "total_line": total,
        "away_coach": away_coach,
        "home_coach": home_coach,
    }


def plays_for(games: list[dict[str, Any]], n: int = 2) -> list[dict[str, Any]]:
    """A couple of plays per game with 1999-style quirks avoided (callers add those)."""
    out = []
    for g in games:
        stype = "REG" if g["game_type"] == "REG" else "POST"
        for i in range(n):
            out.append(
                {
                    "game_id": g["game_id"],
                    "play_id": float(i + 1),
                    "season": g["season"],
                    "week": g["week"],
                    "season_type": stype,
                    "game_date": g["gameday"],
                    "posteam": g["home_team"] if i % 2 == 0 else g["away_team"],
                    "defteam": g["away_team"] if i % 2 == 0 else g["home_team"],
                    "home_team": g["home_team"],
                    "away_team": g["away_team"],
                    "home_coach": g["home_coach"],
                    "away_coach": g["away_coach"],
                    "qtr": 1.0,
                    "down": 1.0,
                    "goal_to_go": 0.0,
                    "desc": f"play {i + 1}",
                    "pass": float(i % 2),
                    "epa": 0.5 * i,
                    "wp": 0.5,
                    "half_seconds_remaining": 1500.0,
                    "score_differential": 0.0,
                    "xyac_median_yardage": 3,
                    "yards_gained": float(4 + i),
                    "air_yards": 7.0,
                }
            )
    return out


def default_teams() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "team_abbr": TEAMS_36,
            "team_name": [f"Team {t}" for t in TEAMS_36],
            "team_conf": ["AFC"] * len(TEAMS_36),
            "team_division": ["AFC East"] * len(TEAMS_36),
        }
    )


def default_players() -> pl.DataFrame:
    return frame(
        [
            {
                "gsis_id": "00-0000001",
                "display_name": "Alpha One",
                "position": "QB",
                "birth_date": "1990-01-01",
                "rookie_season": 2012,
                "latest_team": "SD",
            },
            {
                "gsis_id": "00-0000002",
                "display_name": "Beta Two",
                "position": "WR",
                "birth_date": None,
                "rookie_season": 2020,
                "latest_team": "KC",
            },
            {"gsis_id": None, "display_name": "No Id", "position": "RB"},
        ],  # fmt: skip
        PLAYERS_DTYPES,
    )


def default_ff_playerids() -> pl.DataFrame:
    """The two default players with synthetic cross-site ids (A. One has them all)."""
    return frame(
        [
            {
                "gsis_id": "00-0000001",
                "name": "Alpha One",
                "position": "QB",
                "fantasypros_id": 900001,
                "sleeper_id": 800001,
                "mfl_id": 700001,
                "yahoo_id": "600001",
                "sportradar_id": "sr-0001",
                "birthdate": "1990-01-01",
            },
            {
                "gsis_id": "00-0000002",
                "name": "Beta Two",
                "position": "WR",
                "fantasypros_id": 900002,
                "mfl_id": 700002,
            },
        ],  # fmt: skip
        FF_PLAYERIDS_DTYPES,
    )


def default_rankings() -> pl.DataFrame:
    """One synthetic preseason QB cheat-sheet row for Alpha One (FantasyPros id 900001)."""
    return frame(
        [
            {
                "fp_page": "/nfl/rankings/qb-cheatsheets.php",
                "page_type": "redraft-qb",
                "player": "Alpha One",
                "id": "900001",
                "pos": "QB",
                "team": "KC",
                "ecr": 1.0,
                "ecr_type": "rp",
                "scrape_date": "2025-08-28",
            },
        ],  # fmt: skip
        RANKINGS_ALL_DTYPES,
    )


def default_rosters(season: int, week: int = 1) -> pl.DataFrame:
    """A. One on a weekly roster with the PFR id the default snap counts use (OneAl00), in
    ``week`` (a roster week must be a week of the schedule: fact_roster_week places it in time
    by that week's as-of)."""
    return frame(
        [
            {
                "season": season,
                "week": week,
                "team": "KC",
                "position": "QB",
                "full_name": "Alpha One",
                "gsis_id": "00-0000001",
                "pfr_id": "OneAl00",
                "sleeper_id": "800001",
            }
        ],  # fmt: skip
        ROSTERS_WEEKLY_DTYPES,
    )


class RawCache:
    """Writes synthetic dataset files where ``nflverse.cache_path`` expects them."""

    def __init__(self, root: Path):
        self.root = root

    def write(self, dataset: str, season: int | None, df: pl.DataFrame) -> Path:
        path = nv.cache_path(dataset, season)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(path)
        return path

    def write_season(
        self,
        season: int,
        games: list[dict[str, Any]],
        *,
        pbp: list[dict[str, Any]] | None = None,
        player_stats: list[dict[str, Any]] | None = None,
        team_stats: list[dict[str, Any]] | None = None,
        snaps: list[dict[str, Any]] | None = None,
        injuries: pl.DataFrame | list[dict[str, Any]] | None = None,
        depth_charts: pl.DataFrame | None = None,
        rosters: pl.DataFrame | list[dict[str, Any]] | None = None,
        opportunity: list[dict[str, Any]] | None = None,
    ) -> None:
        """Write every per-season dataset a build needs, with sensible tiny defaults."""
        self.write("schedules", season, frame(games, SCHEDULE_DTYPES))
        self.write("pbp", season, frame(pbp if pbp is not None else plays_for(games), PBP_DTYPES))
        if player_stats is None:
            first_per_week = {g["week"]: g for g in reversed(games)}
            player_stats = [
                {"player_id": "00-0000001", "player_name": "A.One", "position": "QB",
                 "season": g["season"], "week": g["week"],
                 "season_type": "REG" if g["game_type"] == "REG" else "POST",
                 "game_id": g["game_id"], "team": g["home_team"],
                 "opponent_team": g["away_team"], "receptions": 0,
                 "fantasy_points_ppr": 10.0}
                for g in first_per_week.values()
            ]  # fmt: skip
        self.write("player_stats", season, frame(player_stats, PLAYER_STATS_DTYPES))
        if team_stats is None:
            team_stats = [
                {"season": g["season"], "week": g["week"], "team": g["home_team"],
                 "season_type": "REG" if g["game_type"] == "REG" else "POST",
                 "game_id": g["game_id"], "opponent_team": g["away_team"], "passing_epa": 1.5}
                for g in games
            ]  # fmt: skip
        self.write("team_stats", season, frame(team_stats, TEAM_STATS_DTYPES))
        if snaps is None:
            snaps = [
                {"game_id": g["game_id"], "season": g["season"], "game_type": g["game_type"],
                 "week": g["week"], "player": "A. One", "pfr_player_id": "OneAl00",
                 "position": "QB", "team": g["home_team"], "opponent": g["away_team"],
                 "offense_snaps": 60.0, "offense_pct": 1.0}
                for g in games
            ]  # fmt: skip
        self.write("snap_counts", season, frame(snaps, SNAP_DTYPES))
        if injuries is None:
            injuries = [
                {"season": g["season"], "season_type": None, "game_type": g["game_type"],
                 "team": g["home_team"], "week": g["week"], "gsis_id": "00-0000002",
                 "position": "WR", "full_name": "Beta Two", "report_status": "Questionable",
                 "practice_status": "Limited Participation in Practice"}
                for g in games
            ]  # fmt: skip
        if not isinstance(injuries, pl.DataFrame):
            injuries = frame(injuries, INJURY_DTYPES)
        self.write("injuries", season, injuries)
        if depth_charts is None:
            depth_charts = daily_depth_chart(games) if season >= 2025 else legacy_depth_chart(games)
        self.write("depth_charts", season, depth_charts)
        if season >= OPPORTUNITY_FIRST_SEASON:  # ffopportunity starts in 2006
            if opportunity is None:
                opportunity = [
                    {"season": str(g["season"]), "posteam": g["home_team"],
                     "week": float(g["week"]), "game_id": g["game_id"],
                     "player_id": "00-0000001", "full_name": "Alpha One",
                     "position": "QB", "receptions_exp": 0.5, "rec_yards_gained_exp": 5.0,
                     "total_fantasy_points_exp": 1.0}
                    for g in games
                ]  # fmt: skip
            self.write("ff_opportunity", season, frame(opportunity, OPPORTUNITY_DTYPES))
        if season >= 2002:  # weekly rosters start in 2002
            if rosters is None:
                rosters = default_rosters(season, min(g["week"] for g in games))
            if not isinstance(rosters, pl.DataFrame):
                rosters = frame(rosters, ROSTERS_WEEKLY_DTYPES)
            self.write("rosters_weekly", season, rosters)

    def write_globals(self) -> None:
        self.write("teams", None, default_teams())
        self.write("players", None, default_players())
        self.write("ff_playerids", None, default_ff_playerids())
        self.write("ff_rankings_all", None, default_rankings())


def legacy_depth_chart(games: list[dict[str, Any]]) -> pl.DataFrame:
    rows = []
    for g in games:
        rows.append(
            {"season": g["season"], "club_code": g["home_team"], "week": g["week"],
             "game_type": g["game_type"], "depth_team": "1", "last_name": "One",
             "first_name": "Alpha", "formation": "Offense", "gsis_id": "00-0000001",
             "position": "QB", "depth_position": "QB", "full_name": "Alpha One"}
        )  # fmt: skip
    return frame(rows, LEGACY_DC_DTYPES)


def daily_depth_chart(games: list[dict[str, Any]]) -> pl.DataFrame:
    rows = []
    for g in games:
        rows.append(
            {"dt": f"{g['gameday']}T12:00:00Z", "team": g["home_team"], "player_name": "Alpha One",
             "espn_id": "1", "gsis_id": "00-0000001", "pos_grp": "3WR 1TE",
             "pos_name": "Quarterback", "pos_abb": "QB", "pos_slot": 1, "pos_rank": 1}
        )  # fmt: skip
    return frame(rows, DAILY_DC_DTYPES)


# --------------------------------------------------------------------------------------
# Default synthetic seasons
# --------------------------------------------------------------------------------------


# Row counts a build of season_2025_games() alone produces (one place for every test to use).
SEASON_2025_COUNTS = {
    "fact_game": 14,
    "dim_week": 8,
    "dim_team": 36,
    "fact_play": 28,
    "coach_game": 28,
    "dim_player": 2,
}


def season_2025_games() -> list[dict[str, Any]]:
    """2025: W1 Thu/Sun/Mon (Monday-night finish), W2 Sunday only, W3 partial week with a
    Tuesday game (2020-W5 shape), W4 Sunday, then a mini postseason W5..W8 (WC/DIV/CON/SB)."""
    g = [
        game(2025, 1, "2025-09-04", "20:20", "DAL", "PHI", result=4),
        game(2025, 1, "2025-09-07", "13:00", "KC", "LAC", result=-6),
        game(2025, 1, "2025-09-07", "16:25", "SF", "SEA", result=3),
        game(2025, 1, "2025-09-08", "20:15", "MIN", "CHI", result=-7),
        game(2025, 2, "2025-09-14", "13:00", "PHI", "KC", result=3),
        game(2025, 3, "2025-09-18", "20:15", "MIA", "BUF", result=10),
        game(2025, 3, "2025-09-21", "13:00", "LAC", "SF", result=None),
        game(2025, 3, "2025-09-23", "19:00", "SEA", "DAL", result=None),
        game(2025, 4, "2025-09-28", "13:00", "CHI", "MIN", result=None),
        game(2025, 5, "2025-10-04", "16:30", "KC", "DAL", game_type="WC", result=None),
        game(2025, 5, "2025-10-05", "13:00", "SF", "PHI", game_type="WC", result=None),
        game(2025, 6, "2025-10-12", "15:00", "DAL", "PHI", game_type="DIV", result=None),
        game(2025, 7, "2025-10-19", "15:00", "PHI", "KC", game_type="CON", result=None),
        game(2025, 8, "2025-11-02", "18:30", "KC", "PHI", game_type="SB", result=None),
    ]
    return g


def season_2020_split_games() -> list[dict[str, Any]]:
    """2020 W11-W13 with the real W12 shape: a Thursday-to-Monday week plus one game moved to
    Wednesday (BAL at PIT), which ends after W12's Tuesday as-of (a split week)."""
    return [
        game(2020, 11, "2020-11-19", "20:20", "ARI", "SEA", result=7),
        game(2020, 11, "2020-11-22", "13:00", "TEN", "BAL", result=-6),
        game(2020, 12, "2020-11-26", "12:30", "HOU", "DET", result=-16),
        game(2020, 12, "2020-11-29", "13:00", "TEN", "IND", result=-19),
        game(2020, 12, "2020-11-30", "20:15", "SEA", "PHI", result=-6),
        game(2020, 12, "2020-12-02", "15:40", "BAL", "PIT", result=5),
        game(2020, 13, "2020-12-06", "13:00", "IND", "HOU", result=None),
    ]


INJURY_DM_DTYPES: dict[str, pl.DataType] = {
    **{k: v for k, v in INJURY_DTYPES.items() if k != "season_type"},
    "date_modified": pl.Datetime("us", "UTC"),
}


def injury(
    season: int, week: int, team: str, gsis: str, dm: datetime | None = None, **kw: Any
) -> dict[str, Any]:
    """One injury-report row in the 2009-2024 layout (with date_modified, UTC-aware)."""
    return {"season": season, "game_type": "REG", "team": team, "week": week, "gsis_id": gsis,
            "position": "WR", "full_name": "Player", "report_status": "Questionable",
            "practice_status": None, "date_modified": dm, **kw}  # fmt: skip


def draft_class_players() -> pl.DataFrame:
    """dim_player rows for the point-in-time row rule: two drafted players (2014, 2016), an
    undrafted one seen in player stats (00-UDFA01), one seen only in snap counts (pfr_id
    UdfaSn00) and one with no data row at all (never visible)."""
    return frame(
        [
            {
                "gsis_id": "00-D2014",
                "display_name": "Drafted 2014",
                "position": "WR",
                "draft_year": 2014,
                "draft_round": 1,
                "draft_pick": 5,
                "draft_team": "SD",
            },
            {
                "gsis_id": "00-D2016",
                "display_name": "Drafted 2016",
                "position": "QB",
                "draft_year": 2016,
                "draft_round": 1,
                "draft_pick": 1,
                "draft_team": "LA",
            },
            {"gsis_id": "00-UDFA01", "display_name": "Undrafted Stats", "position": "RB"},
            {
                "gsis_id": "00-UDFA02",
                "display_name": "Undrafted Snaps",
                "position": "TE",
                "pfr_id": "UdfaSn00",
            },
            {"gsis_id": "00-GHOST", "display_name": "No Rows", "position": "K"},
        ],  # fmt: skip
        PLAYERS_DTYPES,
    )


def season_2015_draft_games() -> list[dict[str, Any]]:
    return [
        game(2015, 5, "2015-10-11", "13:00", "MIA", "HOU", result=3),
        game(2015, 6, "2015-10-18", "13:00", "HOU", "KC", result=-3),
    ]


def player_week(g: dict[str, Any], player_id: str, receptions: int, **kw: Any) -> dict[str, Any]:
    """One fact_player_week source row for a player of the home team in game ``g``."""
    return {"player_id": player_id, "player_name": player_id, "position": "WR",
            "season": g["season"], "week": g["week"],
            "season_type": "REG" if g["game_type"] == "REG" else "POST",
            "game_id": g["game_id"], "team": g["home_team"], "opponent_team": g["away_team"],
            "receptions": receptions, "fantasy_points_ppr": float(receptions), **kw}  # fmt: skip


@pytest.fixture
def raw(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RawCache:
    """Point nflverse.raw_dir() at a tmp dir and forbid any download."""
    root = tmp_path / "raw"
    monkeypatch.setattr(nv, "raw_dir", lambda: root)

    def _no_download(ds):
        raise AssertionError(f"twm build must never download ({ds.name})")

    monkeypatch.setattr(nv, "_loader", _no_download)
    monkeypatch.setattr(nv, "_configure_nflreadpy", lambda: None)
    # the committed overrides file is never read by offline tests: they write their own
    monkeypatch.setattr(ids, "overrides_path", lambda: tmp_path / "manual" / ids.OVERRIDES_FILE)
    cache = RawCache(root)
    cache.write_globals()
    return cache


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "wh.duckdb"


# --------------------------------------------------------------------------------------
# Real data (opt-in: `uv run pytest -m realdata`)
# --------------------------------------------------------------------------------------

REAL_FULL_SEASONS = list(range(1999, 2027))


@pytest.fixture(scope="session")
def real_full_db(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """One full 1999-2026 build from the real cache, shared by every realdata test module.

    Reads the cache and never downloads (a download fails the test); the scratch file is
    removed afterwards, so pytest's kept temp dirs do not pile up warehouse copies. Yields
    (path, manifest by table name)."""
    from twm.warehouse import build as wb

    needed = [nv.cache_path("schedules", s) for s in REAL_FULL_SEASONS]
    needed.append(nv.cache_path("ff_playerids", None))
    needed.append(nv.cache_path("ff_rankings_all", None))
    if not all(p.exists() for p in needed):
        pytest.skip("real cache not present (run `twm ingest`)")
    path = tmp_path_factory.mktemp("real") / "full.duckdb"
    with pytest.MonkeyPatch.context() as mp:

        def _no_download(ds):
            pytest.fail(f"realdata test tried to download {ds.name}")

        mp.setattr(nv, "_loader", _no_download)
        manifest = wb.build_warehouse(REAL_FULL_SEASONS, db_path=path)
    yield path, {m["table_name"]: m for m in manifest}
    for f in path.parent.glob(path.name + "*"):
        f.unlink(missing_ok=True)
