"""A synthetic 2024/2025 world for the K and D/ST streamer's tests (S1b): 8 teams, one kicker
each, a practice-squad kicker, a punter who kicks, a kicker with no roster row, byes, a split
week (a Tuesday game after week 3's as-of) and FantasyPros K / DST pages.

League used by the tests: 2 teams x 1 slot -> start threshold 2, pool cutoff round(3.0) = 3.
Kicker points = PATs + 3 x field goals (30-39 yards); D/ST points = sacks (every game ends
22-20, which scores 0 in the points-allowed tiers).
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from tests.conftest import (
    FF_PLAYERIDS_DTYPES,
    PLAYER_STATS_DTYPES,
    PLAYERS_DTYPES,
    RANKINGS_ALL_DTYPES,
    TEAM_STATS_DTYPES,
    RawCache,
    frame,
    game,
)
from twm.modules.streamer.pool import StreamerRules

RULES = StreamerRules(slots={"K": 1, "DST": 1}, teams=2, multiplier=1.5)
TEAMS = ("PHI", "DAL", "KC", "LAC", "SF", "SEA", "MIN", "CHI")
KICKER = {301: "PHI", 302: "DAL", 303: "KC", 304: "LAC", 305: "SF", 306: "SEA", 307: "MIN",
          308: "CHI"}  # fmt: skip
PS_KICKER, PUNTER, NO_ROSTER_KICKER = 309, 310, 311  # KC practice squad, SEA punter, CHI (wk 3+)


def gid(n: int) -> str:
    return f"00-00{n:05d}"


def games_2024() -> list[dict]:
    return [game(2024, 1, "2024-09-05", "20:20", "DAL", "PHI", result=2),
            game(2024, 1, "2024-09-08", "13:00", "KC", "LAC", result=2),
            game(2024, 1, "2024-09-08", "16:25", "SF", "SEA", result=2),
            game(2024, 1, "2024-09-09", "20:15", "MIN", "CHI", result=2)]  # fmt: skip


def games_2025(*, week4_final: bool = True) -> list[dict]:
    w4 = 2 if week4_final else None
    return [
        game(2025, 1, "2025-09-04", "20:20", "DAL", "PHI", result=2),
        game(2025, 1, "2025-09-07", "13:00", "KC", "LAC", result=2),
        game(2025, 1, "2025-09-07", "16:25", "SF", "SEA", result=2),
        game(2025, 1, "2025-09-08", "20:15", "MIN", "CHI", result=2),
        # week 2: SF and CHI on their bye
        game(2025, 2, "2025-09-14", "13:00", "PHI", "KC", result=2),
        game(2025, 2, "2025-09-14", "13:00", "LAC", "DAL", result=2),
        game(2025, 2, "2025-09-14", "16:25", "SEA", "MIN", result=2),
        # week 3: Sunday, then MIN at LAC on Tuesday, after week 3's as-of (a split week)
        game(2025, 3, "2025-09-21", "13:00", "KC", "SF", result=2),
        game(2025, 3, "2025-09-21", "13:00", "CHI", "PHI", result=2),
        game(2025, 3, "2025-09-21", "16:25", "DAL", "SEA", result=2),
        game(2025, 3, "2025-09-23", "19:00", "MIN", "LAC", result=2),
        # week 4: the last regular-season week
        game(2025, 4, "2025-09-28", "13:00", "PHI", "DAL", result=2),
        game(2025, 4, "2025-09-28", "13:00", "SF", "CHI", result=2),
        game(2025, 4, "2025-09-28", "16:25", "SEA", "KC", result=2),
        game(2025, 4, "2025-09-28", "16:25", "LAC", "MIN", result=w4),
        game(2025, 5, "2025-10-04", "16:30", "KC", "PHI", game_type="WC", result=None),
    ]


# (season, week) -> kicker n -> points (PATs + 3 x FGs); team = KICKER[n] unless noted
KICKS: dict[tuple[int, int], dict[int, int]] = {
    (2024, 1): {308: 10, 307: 9, 306: 8, 301: 7, 302: 6, 303: 5, 304: 4, 305: 3},
    # 306 and 307 tie for 3rd by PPG after week 1
    (2025, 1): {301: 10, 302: 1, 303: 9, 304: 2, 305: 3, 306: 8, 307: 8, 308: 4},
    # 301 and 303 tie at the start cutoff (2nd); the punter kicks 2 PATs for SEA
    (2025, 2): {304: 7, 301: 5, 303: 5, 307: 3, 302: 2, 306: 1, PUNTER: 2},
    # 308 is on injured reserve; 311 (no roster row) kicks for CHI; 304 and 307 on Tuesday
    (2025, 3): {304: 10, NO_ROSTER_KICKER: 9, 305: 6, 302: 5, 303: 4, 301: 3, 306: 2, 307: 1},
    (2025, 4): {307: 9, 305: 8, 306: 6, 304: 5, NO_ROSTER_KICKER: 4, 302: 3, 301: 2, 303: 1},
}
KICKER_TEAM_OVERRIDE = {PUNTER: "SEA", NO_ROSTER_KICKER: "CHI"}
# (season, week) -> team -> sacks (= D/ST points)
SACKS: dict[tuple[int, int], dict[str, int]] = {
    (2024, 1): {"PHI": 1, "DAL": 2, "KC": 3, "LAC": 4, "SF": 5, "SEA": 6, "MIN": 7, "CHI": 8},
    (2025, 1): {"PHI": 1, "DAL": 2, "KC": 3, "LAC": 4, "SF": 5, "SEA": 6, "MIN": 7, "CHI": 8},
    (2025, 2): {"LAC": 7, "PHI": 6, "KC": 6, "DAL": 5, "MIN": 3, "SEA": 2},
    (2025, 3): {"KC": 1, "SF": 2, "CHI": 3, "PHI": 4, "DAL": 5, "SEA": 6, "MIN": 7, "LAC": 8},
    (2025, 4): {"PHI": 8, "DAL": 7, "SF": 6, "CHI": 5, "SEA": 4, "KC": 3, "LAC": 2, "MIN": 1},
}

KICK_DTYPES = {**PLAYER_STATS_DTYPES,
               **dict.fromkeys(("fg_att", "fg_made", "fg_made_30_39", "pat_att", "pat_made"),
                               pl.Int32())}  # fmt: skip
DEF_DTYPES = {**TEAM_STATS_DTYPES, "def_sacks": pl.Float64(), "def_interceptions": pl.Int32()}


def _game_of(games: list[dict], season: int, week: int, team: str) -> dict:
    (g,) = [x for x in games if x["season"] == season and x["week"] == week
            and team in (x["home_team"], x["away_team"])]  # fmt: skip
    return g


def kicker_stats(season: int, games: list[dict]) -> list[dict]:
    rows = []
    for (s, w), pts in KICKS.items():
        if s != season:
            continue
        for n, p in pts.items():
            team = KICKER_TEAM_OVERRIDE.get(n, KICKER.get(n))
            g = _game_of(games, s, w, team)
            opp = g["away_team"] if team == g["home_team"] else g["home_team"]
            fgs, pats = divmod(p, 3)
            rows.append({"player_id": gid(n), "player_name": f"K{n}",
                         "position": "P" if n == PUNTER else "K", "season": s, "week": w,
                         "season_type": "REG", "game_id": g["game_id"], "team": team,
                         "opponent_team": opp, "receptions": 0, "fg_att": fgs, "fg_made": fgs,
                         "fg_made_30_39": fgs, "pat_att": pats, "pat_made": pats})  # fmt: skip
    return rows


def team_stats(season: int, games: list[dict]) -> list[dict]:
    rows = []
    for g in games:
        if g["game_type"] != "REG":
            continue
        for team, opp in ((g["home_team"], g["away_team"]), (g["away_team"], g["home_team"])):
            rows.append({"season": season, "week": g["week"], "team": team,
                         "season_type": "REG", "game_id": g["game_id"], "opponent_team": opp,
                         "def_sacks": float(SACKS[(season, g["week"])][team]),
                         "def_interceptions": 0})  # fmt: skip
    return rows


def rosters_2025(games: list[dict]) -> list[dict]:
    """Game-day rosters: a team is on a week's roster only if it plays that week."""
    rows = []
    for w in (1, 2, 3, 4):
        playing = {t for g in games if g["week"] == w and g["game_type"] == "REG"
                   for t in (g["home_team"], g["away_team"])}  # fmt: skip
        for n, team in KICKER.items():
            if team in playing:
                status = "RES" if (n == 308 and w >= 3) else "ACT"
                rows.append(_roster(w, n, team, "K", status))
        if "KC" in playing:
            rows.append(_roster(w, PS_KICKER, "KC", "K", "DEV"))
        if "SEA" in playing:
            rows.append(_roster(w, PUNTER, "SEA", "P", "ACT"))
    return rows


def _roster(week: int, n: int, team: str, pos: str, status: str) -> dict:
    return {"season": 2025, "week": week, "game_type": "REG", "team": team, "position": pos,
            "full_name": f"Kicker {n}", "gsis_id": gid(n), "status": status}  # fmt: skip


def players() -> pl.DataFrame:
    ids = [*KICKER, PS_KICKER, PUNTER, NO_ROSTER_KICKER]
    rows = [{"gsis_id": gid(n), "display_name": f"Kicker {n}",
             "position": "P" if n == PUNTER else "K"} for n in ids]  # fmt: skip
    return frame(rows, PLAYERS_DTYPES)


def ff_playerids() -> pl.DataFrame:
    return frame([{"gsis_id": gid(n), "name": f"Kicker {n}", "position": "K",
                   "fantasypros_id": 920000 + n} for n in (*KICKER, PS_KICKER)],
                 FF_PLAYERIDS_DTYPES)  # fmt: skip


PRESEASON_DAY = "2025-08-28"  # the last August scrape before the first game (2025-09-04)


def rankings() -> pl.DataFrame:
    k_sheet = {"fp_page": "k-cheatsheets", "page_type": "redraft-kdst", "ecr_type": "rp"}
    dst_sheet = {"fp_page": "/nfl/rankings/dst-cheatsheets.php", "page_type": "redraft-dst",
                 "ecr_type": "rp", "pos": "DST"}  # fmt: skip

    def k(n: int, ecr: float, day: str = PRESEASON_DAY, **kw) -> dict:
        # the 2019-2020 short-form K pages spell a kicker 'PK'
        return {**k_sheet, "player": f"Kicker {n}", "id": str(920000 + n), "pos": "PK",
                "team": KICKER.get(n, "KC"), "ecr": ecr, "scrape_date": day, **kw}  # fmt: skip

    rows = [k(n, float(i + 1)) for i, n in enumerate((301, 302, 303, 304, 305, 306, 307))]
    rows += [
        {**k(999, 8.0), "player": "Free Agent", "id": "929999", "team": "FA"},
        k(308, 1.0, "2025-09-11"),  # after kickoff: not the preseason list
        {**k(309, 1.0), "page_type": "dynasty-offense"},  # dynasty: never kept
        {"fp_page": "/nfl/rankings/k.php", "page_type": "weekly-k", "ecr_type": "wp",
         "player": "Kicker 304", "id": str(920304), "pos": "K", "team": "LAC", "ecr": 5.0,
         "scrape_date": "2025-09-12", "player_owned_avg": 12.0},
    ]  # fmt: skip
    teams = [*TEAMS, "JAC"]  # FantasyPros spells Jacksonville JAC
    rows += [
        {
            **dst_sheet,
            "player": f"{t} D/ST",
            "id": str(8000 + i),
            "team": t,
            "ecr": float(i + 1),
            "scrape_date": PRESEASON_DAY,
        }
        for i, t in enumerate(teams)
    ]
    rows.append({"fp_page": "/nfl/rankings/dst.php", "page_type": "weekly-dst", "ecr_type": "wp",
                 "player": "LAC D/ST", "id": str(8003), "pos": "DST", "team": "LAC", "ecr": 9.0,
                 "scrape_date": "2025-09-12", "player_owned_avg": 30.0})  # fmt: skip
    return frame(rows, RANKINGS_ALL_DTYPES)


def write_world(cache: RawCache, *, week4_final: bool = True) -> None:
    cache.write("players", None, players())
    cache.write("ff_playerids", None, ff_playerids())
    cache.write("ff_rankings_all", None, rankings())
    g24, g25 = games_2024(), games_2025(week4_final=week4_final)
    # write_season's own dtype maps lack the kicking/defense columns: written again below
    cache.write_season(2024, g24, player_stats=[], team_stats=[], rosters=[])
    cache.write("player_stats", 2024, frame(kicker_stats(2024, g24), KICK_DTYPES))
    cache.write("team_stats", 2024, frame(team_stats(2024, g24), DEF_DTYPES))
    cache.write_season(2025, g25, player_stats=[], team_stats=[], rosters=rosters_2025(g25))
    cache.write("player_stats", 2025, frame(kicker_stats(2025, g25), KICK_DTYPES))
    cache.write("team_stats", 2025, frame(team_stats(2025, g25), DEF_DTYPES))


def build_world(tmp: Path, *, week4_final: bool = True) -> Path:
    """Write the cache under ``tmp`` and build the warehouse (offline); returns the db path."""
    import pytest

    from twm import ids
    from twm.sources import nflverse as nv
    from twm.warehouse import build as wb

    with pytest.MonkeyPatch.context() as mp:
        root = tmp / "raw"
        mp.setattr(nv, "raw_dir", lambda: root)

        def _no_download(ds):
            raise AssertionError(f"never download ({ds.name})")

        mp.setattr(nv, "_loader", _no_download)
        mp.setattr(nv, "_configure_nflreadpy", lambda: None)
        mp.setattr(ids, "overrides_path", lambda: tmp / "manual" / ids.OVERRIDES_FILE)
        cache = RawCache(root)
        cache.write_globals()
        write_world(cache, week4_final=week4_final)
        db = tmp / "streamer.duckdb"
        wb.build_warehouse([2024, 2025], db_path=db)
    return db
