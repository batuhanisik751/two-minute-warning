"""Waiver Radar candidate pool (C1): the two new warehouse tables (fact_roster_week,
fact_ranking), the pool itself, its point-in-time safety, the report and the CLI.

The pool tests share one synthetic warehouse (``world``): every id and name is made up.

- 2025 (FantasyPros season, game-day rosters): week 1 Thu-Mon, week 2 one game (PHI at KC;
  every other team is off, so its players' latest roster row stays week 1), week 3 with a game
  moved to Tuesday evening (after week 3's as-of: a split week), week 4 unplayed.
- 2014 (fallback season, post-game rosters: a player who played in week 1 is RES on the week-1
  roster) with 2013 as the "last season".

Cutoffs are tiny (``RULES``: WR 2, QB/RB/TE 1) so boundaries are easy to see.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import polars as pl
import pytest
from typer.testing import CliRunner

from tests.conftest import (
    FF_PLAYERIDS_DTYPES,
    PLAYERS_DTYPES,
    RANKINGS_ALL_DTYPES,
    RawCache,
    frame,
    game,
    season_2025_games,
)
from twm import ids
from twm.asof import AsOfView, weekly_as_of
from twm.backtest.leakage import LeakageError, assert_future_invariant
from twm.cli import app
from twm.config import League, PoolConfig, league
from twm.modules.waiver_radar import pool as wp
from twm.modules.waiver_radar.report import CSV_COLUMNS, build_report
from twm.sources import nflverse as nv
from twm.warehouse import available as av
from twm.warehouse import build as wb
from twm.warehouse import schema as sc

RULES = wp.PoolRules(
    cutoffs={"QB": 1, "RB": 1, "WR": 2, "TE": 1},
    roster_statuses=("ACT", "INA", "DEV"),
    prior_season_min_games=2,
    rookie_drafted_rounds=2,
)


def gid(n: int) -> str:
    """Synthetic gsis id of fixture player n."""
    return f"00-00{n:05d}"


def fpid(n: int) -> str:
    """Synthetic FantasyPros id of fixture player n."""
    return str(910000 + n)


# --------------------------------------------------------------------------------------
# The synthetic world
# --------------------------------------------------------------------------------------

# 2025 players: n -> (name, draft_year, draft_round)
PLAYERS_2025 = {n: f"Player {n}" for n in range(101, 117)}
PLAYERS_2014 = {n: f"Player {n}" for n in range(201, 209)}
DRAFT = {205: (2014, 1), 206: (2014, 3)}  # the two 2014 rookies


def _players() -> pl.DataFrame:
    rows = [
        {"gsis_id": gid(n), "display_name": name, "position": "WR",
         "draft_year": DRAFT.get(n, (None, None))[0], "draft_round": DRAFT.get(n, (None, None))[1]}
        for n, name in {**PLAYERS_2025, **PLAYERS_2014}.items()
    ]  # fmt: skip
    return frame(rows, PLAYERS_DTYPES)


def _ff_playerids() -> pl.DataFrame:
    rows = [
        {"gsis_id": gid(n), "name": name, "position": "WR", "fantasypros_id": 910000 + n}
        for n, name in PLAYERS_2025.items()
    ]
    return frame(rows, FF_PLAYERIDS_DTYPES)


def _stat(g: dict, n: int, team: str, receptions: int) -> dict:
    opp = g["away_team"] if team == g["home_team"] else g["home_team"]
    return {"player_id": gid(n), "player_name": f"P{n}", "position": "WR",
            "season": g["season"], "week": g["week"], "season_type": "REG",
            "game_id": g["game_id"], "team": team, "opponent_team": opp,
            "receptions": receptions, "fantasy_points_ppr": float(receptions)}  # fmt: skip


def _roster(season: int, week: int, n: int, team: str, pos: str, status: str = "ACT",
            **kw) -> dict:  # fmt: skip
    return {"season": season, "week": week, "game_type": "REG", "team": team, "position": pos,
            "full_name": f"Player {n}", "gsis_id": gid(n), "status": status, **kw}  # fmt: skip


def world_2025() -> tuple[list[dict], list[dict], list[dict]]:
    g = {x["game_id"]: x for x in season_2025_games()}
    w1_phi, w1_kc, w1_sea = g["2025_01_DAL_PHI"], g["2025_01_KC_LAC"], g["2025_01_SF_SEA"]
    w2, w3_sf, w3_split = g["2025_02_PHI_KC"], g["2025_03_LAC_SF"], g["2025_03_SEA_DAL"]
    stats = [
        # week 1: 104 leads; 101, 105 and 106 tie at 8 per game (all share rank 2)
        _stat(w1_phi, 101, "PHI", 8), _stat(w1_phi, 103, "PHI", 1), _stat(w1_phi, 107, "PHI", 7),
        _stat(w1_phi, 109, "PHI", 6), _stat(w1_phi, 105, "DAL", 8), _stat(w1_phi, 116, "PHI", 2),
        _stat(w1_kc, 102, "KC", 4), _stat(w1_kc, 104, "KC", 20), _stat(w1_kc, 111, "KC", 2),
        _stat(w1_kc, 113, "LAC", 3), _stat(w1_sea, 106, "SEA", 8), _stat(w1_sea, 112, "SEA", 2),
        # week 2 (PHI at KC only)
        _stat(w2, 101, "PHI", 5), _stat(w2, 104, "KC", 0), _stat(w2, 109, "PHI", 6),
        # week 3: Sunday game, then the Tuesday-evening game (after week 3's as-of)
        _stat(w3_sf, 113, "LAC", 3),
        _stat(w3_split, 105, "DAL", 30), _stat(w3_split, 110, "DAL", 12),
        _stat(w3_split, 106, "SEA", 30),
    ]  # fmt: skip
    r = []
    week1 = {101: ("PHI", "WR"), 102: ("KC", "WR"), 103: ("PHI", "WR"), 104: ("KC", "WR"),
             105: ("DAL", "WR"), 106: ("SEA", "WR"), 107: ("PHI", "RB"), 108: ("KC", "RB"),
             109: ("PHI", "WR"), 111: ("KC", "WR"), 112: ("SEA", "WR"), 113: ("LAC", "RB"),
             116: ("PHI", "WR")}  # fmt: skip
    r += [_roster(2025, 1, n, t, p) for n, (t, p) in week1.items()]
    r += [_roster(2025, 1, 114, "KC", "QB", "DEV"), _roster(2025, 1, 115, "PHI", "TE", "INA")]
    # week 2: only PHI and KC play. 107 went on injured reserve, 108 was cut, 109 is now a RB,
    # 116 was released without a CUT row (he simply is not on PHI's week-2 roster)
    week2 = {101: ("PHI", "WR", "ACT"), 102: ("KC", "WR", "ACT"), 103: ("PHI", "WR", "ACT"),
             104: ("KC", "WR", "ACT"), 107: ("PHI", "RB", "RES"), 108: ("KC", "RB", "CUT"),
             109: ("PHI", "RB", "ACT"), 111: ("KC", "WR", "ACT"), 114: ("KC", "QB", "DEV"),
             115: ("PHI", "TE", "INA")}  # fmt: skip
    r += [_roster(2025, 2, n, t, p, st) for n, (t, p, st) in week2.items()]
    # week 3: DAL, SEA and LAC play; 110 signs with DAL (first roster row)
    week3 = {105: ("DAL", "WR"), 106: ("SEA", "WR"), 110: ("DAL", "WR"), 112: ("SEA", "WR"),
             113: ("LAC", "RB")}  # fmt: skip
    r += [_roster(2025, 3, n, t, p) for n, (t, p) in week3.items()]

    wr_sheet = {"fp_page": "/nfl/rankings/ppr-wr-cheatsheets.php", "page_type": "redraft-wr",
                "ecr_type": "rp"}  # fmt: skip
    rb_sheet = {"fp_page": "/nfl/rankings/ppr-rb-cheatsheets.php", "page_type": "redraft-rb",
                "ecr_type": "rp"}  # fmt: skip
    weekly = {"fp_page": "/nfl/rankings/ppr-wr.php", "page_type": "weekly-wr", "ecr_type": "wp"}

    def rk(base: dict, n: int, pos: str, ecr: float, day: str, **kw) -> dict:
        return {**base, "player": f"Player {n}", "id": fpid(n), "pos": pos, "ecr": ecr,
                "scrape_date": day, **kw}  # fmt: skip

    pre = "2025-08-28"  # the last August cheat sheet before the first game (2025-09-04)
    rankings = [
        rk(wr_sheet, 101, "WR", 1.0, pre), rk(wr_sheet, 102, "WR", 2.0, pre),
        rk(wr_sheet, 112, "RB", 2.5, pre),  # a RB listed on the WR sheet (rank among WRs: 3)
        rk(wr_sheet, 103, "WR", 3.0, pre), rk(wr_sheet, 103, "WR", 3.0, pre),  # exact duplicate
        rk(wr_sheet, 105, "WR", 4.0, pre),
        rk(rb_sheet, 111, "RB", 1.0, pre),  # a RB for FantasyPros, a WR on his roster
        rk(rb_sheet, 112, "RB", 2.0, pre), rk(rb_sheet, 107, "RB", 3.0, pre),
        # an IDP page lists 106: not a WR rank
        {"fp_page": "/nfl/rankings/db-cheatsheets.php", "page_type": "redraft-db",
         "ecr_type": "rp", "player": "Player 106", "id": fpid(106), "pos": "WR", "ecr": 1.0,
         "scrape_date": pre},
        # an earlier cheat sheet and one after kickoff: neither is the preseason list
        rk(wr_sheet, 103, "WR", 1.0, "2025-08-21"), rk(wr_sheet, 104, "WR", 1.0, "2025-09-11"),
        # weekly rankings with rostership (the Friday before each week's games)
        rk(weekly, 101, "WR", 1.0, "2025-09-05", player_owned_avg=99.0, player_owned_espn=98.0),
        rk(weekly, 104, "WR", 9.0, "2025-09-05", player_owned_avg=30.0),
        rk(weekly, 103, "WR", 20.0, "2025-09-05", player_owned_avg=60.0),
        rk(weekly, 101, "WR", 1.0, "2025-09-12", player_owned_avg=99.0),
        rk(weekly, 104, "WR", 2.0, "2025-09-12", player_owned_avg=95.0),
        rk(weekly, 105, "WR", 30.0, "2025-09-19", player_owned_avg=40.0),
        rk(weekly, 104, "WR", 3.0, "2025-09-26", player_owned_avg=5.0),  # after week 3's as-of
    ]  # fmt: skip
    return stats, r, rankings


def games_2013_2014() -> tuple[list[dict], list[dict]]:
    g13 = [
        game(2013, 1, "2013-09-08", "13:00", "KC", "HOU", result=3),
        game(2013, 2, "2013-09-15", "13:00", "HOU", "KC", result=3),
        game(2013, 3, "2013-09-22", "13:00", "KC", "HOU", result=3),
    ]
    g14 = [
        game(2014, 1, "2014-09-07", "13:00", "KC", "HOU", result=3),
        game(2014, 2, "2014-09-14", "13:00", "HOU", "KC", result=3),
        game(2014, 3, "2014-09-21", "13:00", "KC", "HOU", result=3),
    ]
    return g13, g14


def world_2013_2014() -> tuple[list[dict], list[dict], list[dict]]:
    g13, g14 = games_2013_2014()
    s13 = []
    for g in g13:  # last season: 201 10 per game, 202 8, 208 (then a TE) 7, 203 6
        s13 += [_stat(g, 201, "HOU", 10), _stat(g, 202, "HOU", 8), _stat(g, 208, "HOU", 7),
                _stat(g, 203, "KC", 6)]  # fmt: skip
    s13.append(_stat(g13[0], 204, "KC", 50))  # one game only: below prior_season_min_games
    s14 = [
        _stat(g14[0], 207, "HOU", 5), _stat(g14[0], 201, "HOU", 1), _stat(g14[1], 201, "HOU", 1),
        _stat(g14[0], 203, "KC", 2), _stat(g14[0], 204, "KC", 3),
    ]  # fmt: skip
    r14 = []
    base = {201: ("HOU", "WR"), 202: ("HOU", "WR"), 203: ("KC", "WR"), 204: ("KC", "WR"),
            205: ("KC", "WR"), 206: ("HOU", "WR"), 208: ("HOU", "WR")}  # fmt: skip
    for week in (1, 2, 3):
        for n, (team, pos) in base.items():
            if n == 204 and week == 3:
                continue  # released after week 2 (no row any more)
            status = "CUT" if n == 204 and week == 2 else "ACT"
            entry = 2014 if n in DRAFT else 2010
            # the NFL's own code for Houston in the 2002-2015 rosters (normalized to HOU)
            r14.append(_roster(2014, week, n, "HST" if team == "HOU" else team, pos, status,
                               entry_year=entry))  # fmt: skip
        # 207 played in week 1 but the (post-game) week-1 roster already shows him on IR
        r14.append(_roster(2014, week, 207, "HST", "WR", "RES", entry_year=2010))
    r13 = [_roster(2013, 1, 208, "HOU", "TE")]
    return s13 + s14, r13, r14


def write_world(cache: RawCache) -> None:
    cache.write("players", None, _players())
    cache.write("ff_playerids", None, _ff_playerids())
    stats25, rost25, rankings = world_2025()
    cache.write("ff_rankings_all", None, frame(rankings, RANKINGS_ALL_DTYPES))
    cache.write_season(2025, season_2025_games(), player_stats=stats25, snaps=[],
                       rosters=rost25)  # fmt: skip
    g13, g14 = games_2013_2014()
    stats, r13, r14 = world_2013_2014()
    cache.write_season(2013, g13, player_stats=[s for s in stats if s["season"] == 2013],
                       snaps=[], rosters=r13)  # fmt: skip
    cache.write_season(2014, g14, player_stats=[s for s in stats if s["season"] == 2014],
                       snaps=[], rosters=r14)  # fmt: skip


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory):
    """The synthetic 2013/2014/2025 warehouse, built once for the module (offline)."""
    tmp = tmp_path_factory.mktemp("world")
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
        write_world(cache)
        db = tmp / "world.duckdb"
        manifest = wb.build_warehouse([2013, 2014, 2025], db_path=db)
    return db, {m["table_name"]: m for m in manifest}


def _pool(db: Path, season: int, week: int, **kw) -> pl.DataFrame:
    with AsOfView(db, weekly_as_of(db, season, week)) as v:
        return wp.candidate_pool(v, season, week, rules=RULES, **kw)


def _by_player(df: pl.DataFrame) -> dict[str, dict]:
    return {r["gsis_id"]: r for r in df.iter_rows(named=True)}


def _con(db: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(db), read_only=True)
    con.execute("SET TimeZone='UTC'")
    return con


# --------------------------------------------------------------------------------------
# fact_roster_week
# --------------------------------------------------------------------------------------


def test_roster_dedupe_null_ids_and_team_codes(raw, db_path):
    games = season_2025_games()
    rows = [
        _roster(2025, 1, 301, "KC", "WR", "ACT"), _roster(2025, 1, 301, "PHI", "WR", "TRC"),
        _roster(2025, 1, 302, "DAL", "RB", "DEV"), _roster(2025, 1, 302, "ARZ", "RB", "INA"),
        _roster(2025, 1, 303, "KC", "TE", "RET"), _roster(2025, 1, 303, "BAL", "TE", "CUT"),
        _roster(2025, 1, 304, "PHI", "QB", "ACT"), _roster(2025, 1, 304, "DAL", "QB", "ACT"),
        _roster(2025, 1, 305, "BLT", "WR", "CUT"), _roster(2025, 1, 305, "HST", "WR", "TRD"),
        {**_roster(2025, 1, 306, "KC", "WR"), "gsis_id": None},
        {**_roster(2025, 1, 307, "KC", "WR"), "gsis_id": " "},
        _roster(2025, 1, 308, "OAK", "WR", rookie_year=2027, draft_number="12"),
    ]  # fmt: skip
    raw.write_season(2025, games, rosters=rows)
    manifest = {m["table_name"]: m for m in wb.build_warehouse([2025], db_path=db_path)}
    con = _con(db_path)
    got = {
        r[0]: r[1:]
        for r in con.execute(
            "SELECT gsis_id, status, team, team_raw FROM fact_roster_week ORDER BY 1"
        ).fetchall()
    }
    assert got == {
        gid(301): ("ACT", "KC", "KC"),  # ACT beats a trade code
        gid(302): ("INA", "ARI", "ARZ"),  # INA beats DEV; ARZ is Arizona
        gid(303): ("CUT", "BAL", "BAL"),  # CUT beats RET
        gid(304): ("ACT", "DAL", "DAL"),  # same status: the team decides (alphabetical)
        gid(305): ("TRD", "HOU", "HST"),  # a trade code beats CUT; HST is Houston
        gid(308): ("ACT", "LV", "OAK"),
    }
    m = manifest["fact_roster_week"]
    assert (m["n_dropped_null_key"], m["n_dropped_duplicates"]) == (2, 5)
    assert m["seasons"] == "[2025]"
    assert con.execute(
        "SELECT draft_number, rookie_year FROM fact_roster_week WHERE gsis_id = ?", [gid(308)]
    ).fetchone() == (12, 2027)
    con.close()
    # rookie_year is hindsight (a few real rows carry a later season): hidden point-in-time
    with AsOfView(db_path, weekly_as_of(db_path, 2025, 1)) as v:
        assert "rookie_year" not in v.table("fact_roster_week").columns
        assert "entry_year" in v.table("fact_roster_week").columns
        with pytest.raises(duckdb.BinderException):
            v.sql("SELECT rookie_year FROM fact_roster_week")


def test_roster_regime_and_availability(world):
    db, manifest = world
    notes = json.loads(manifest["fact_roster_week"]["notes"])
    regimes = {s: r["regime"] for s, r in notes["regime_by_season"].items()}
    # 2013: its one listed player who played is ACT
    assert regimes == {"2013": "game_day", "2014": "post_game", "2025": "game_day"}
    # 2014: of the 4 listed players who played in week 1, 207 is RES; 201 played week 2 (ACT)
    assert notes["regime_by_season"]["2014"]["n_played_not_act"] == 1
    assert notes["regime_by_season"]["2014"]["share_not_act"] == pytest.approx(1 / 5)
    assert notes["regime_by_season"]["2025"]["share_not_act"] == 0
    assert notes["regime_threshold"] == 0.01
    con = _con(db)

    def at(season, week):
        return con.execute(
            "SELECT DISTINCT available_at FROM fact_roster_week WHERE season = ? AND week = ?",
            [season, week],
        ).fetchall()

    def asof(season, week):
        return weekly_as_of(db, season, week).replace(tzinfo=None)

    # game day: week N's roster at week N's as-of; post-game: at week N+1's; last week: +7 days
    assert at(2025, 1) == [(asof(2025, 1),)] and at(2025, 3) == [(asof(2025, 3),)]
    assert at(2014, 1) == [(asof(2014, 2),)] and at(2014, 2) == [(asof(2014, 3),)]
    assert at(2014, 3) == [(asof(2014, 3) + timedelta(days=7),)]
    counts = notes["available_at_rule_counts"]
    assert counts["unplaced_week_not_in_dim_week"] == 0 and counts["post_game_next_week_asof"] > 0
    con.close()
    # the boundary through the as-of view: invisible one microsecond before, visible at it
    w2 = weekly_as_of(db, 2014, 2)
    q = "SELECT count(*) AS n FROM fact_roster_week WHERE season = 2014 AND week = 1"
    with AsOfView(db, w2 - timedelta(microseconds=1)) as v:
        assert v.sql(q).item() == 0
    with AsOfView(db, w2) as v:
        assert v.sql(q).item() == 8


def test_regime_threshold_comes_from_config(raw, db_path, tmp_path, monkeypatch):
    """Two listed players played in week 1, one of them shows RES: a 50% share is post-game
    at the shipped 1% threshold and game-day at 60%."""
    games = season_2025_games()
    g = games[0]
    raw.write_season(
        2025, games, snaps=[],
        player_stats=[_stat(g, 1, g["home_team"], 3), _stat(g, 2, g["home_team"], 3)],
        rosters=[_roster(2025, 1, 1, g["home_team"], "WR"),
                 _roster(2025, 1, 2, g["home_team"], "WR", "RES")],
    )  # fmt: skip
    rules = av.AvailabilityRules.from_settings()
    assert rules.roster_postgame_share_threshold == 0.01
    wb.build_warehouse([2025], db_path=db_path)
    q = "SELECT DISTINCT available_at FROM fact_roster_week"
    assert _con(db_path).execute(q).fetchall() == [(datetime(2025, 9, 16, 14, 0),)]  # week 2
    monkeypatch.setattr(
        av.AvailabilityRules,
        "from_settings",
        classmethod(lambda cls: cls(roster_postgame_share_threshold=0.6)),
    )
    other = tmp_path / "loose.duckdb"
    wb.build_warehouse([2025], db_path=other)
    assert _con(other).execute(q).fetchall() == [(datetime(2025, 9, 9, 14, 0),)]  # week 1


def test_roster_week_outside_the_schedule_stops_the_build(raw, db_path):
    raw.write_season(2025, season_2025_games(), rosters=[_roster(2025, 30, 1, "KC", "WR")])
    with pytest.raises(av.AvailabilityError, match="fact_roster_week: 1 row"):
        wb.build_warehouse([2025], db_path=db_path)


# --------------------------------------------------------------------------------------
# fact_ranking
# --------------------------------------------------------------------------------------


def _ranking_rows() -> list[dict]:
    wr = {"fp_page": "/nfl/rankings/ppr-wr-cheatsheets.php", "page_type": "redraft-wr",
          "ecr_type": "rp", "pos": "WR", "scrape_date": "2025-08-28"}  # fmt: skip
    return [
        # pos_rank: 1, 2, 2 (tie), a RB listed on the WR sheet (rank among WRs 4), then 4
        {**wr, "player": "A", "id": "900001", "ecr": 1.0},
        {**wr, "player": "B", "id": "900101", "ecr": 2.0},
        {**wr, "player": "C", "id": "900102", "ecr": 2.0},
        {**wr, "player": "D", "id": "900103", "ecr": 2.5, "pos": "RB"},
        {**wr, "player": "E", "id": "900104", "ecr": 3.0},
        # duplicate of E with a worse ecr: dropped (the lowest ecr is kept)
        {**wr, "player": "E", "id": "900104", "ecr": 5.0},
        # a DB on an offense page, an IDP page, a kicker page, a dynasty-labelled sheet
        {**wr, "player": "F", "id": "900105", "ecr": 9.0, "pos": "DB"},
        {**wr, "fp_page": "/nfl/rankings/db-cheatsheets.php", "page_type": "redraft-db",
         "player": "G", "id": "900106", "ecr": 1.0},
        {**wr, "fp_page": "/nfl/rankings/k-cheatsheets.php", "page_type": "redraft-k",
         "player": "H", "id": "900107", "ecr": 1.0, "pos": "K"},
        {**wr, "fp_page": "ppr-wr-cheatsheets", "page_type": "dynasty-offense",
         "player": "I", "id": "900108", "ecr": 1.0},
        # the 2020 short form of a cheat sheet, a rest-of-season page, weekly pages
        {**wr, "fp_page": "ppr-te-cheatsheets", "page_type": "redraft-offense",
         "player": "J", "id": "900109", "ecr": 1.0, "pos": "TE", "scrape_date": "2025-08-27"},
        {**wr, "fp_page": "/nfl/rankings/ros-ppr-wr.php", "player": "A", "id": "900001",
         "ecr": 2.0, "scrape_date": "2025-10-03"},
        {**wr, "fp_page": "/nfl/rankings/qb.php", "page_type": "weekly-qb", "ecr_type": "wp",
         "player": "K", "id": "900110", "ecr": 1.0, "pos": "QB", "scrape_date": "2025-10-03",
         "player_owned_avg": 88.5, "player_owned_espn": 90.0},
        {**wr, "fp_page": "qb", "page_type": "weekly-offense", "ecr_type": "wp",
         "player": "K", "id": "900110", "ecr": 1.0, "pos": "QB", "scrape_date": "2026-01-02"},
        # another ecr_type (overall rankings) and a season that is not built
        {**wr, "ecr_type": "ro", "player": "L", "id": "900111", "ecr": 1.0},
        {**wr, "player": "M", "id": "900112", "ecr": 1.0, "scrape_date": "2026-03-05"},
    ]  # fmt: skip


def test_fact_ranking_pages_ranks_and_availability(raw, db_path):
    raw.write("ff_rankings_all", None, frame(_ranking_rows(), RANKINGS_ALL_DTYPES))
    raw.write_season(2025, season_2025_games())
    manifest = {m["table_name"]: m for m in wb.build_warehouse([2025], db_path=db_path)}
    con = _con(db_path)
    rows = con.execute(
        "SELECT player, season, page_kind, page_pos, pos, ecr, pos_rank, gsis_id, available_at "
        "FROM fact_ranking ORDER BY scrape_date, page_kind, page_pos, ecr, player"
    ).fetchall()
    pre = datetime(2025, 8, 29)  # scraped 2025-08-28: public the next day at 00:00 UTC
    assert rows == [
        ("J", 2025, "preseason", "TE", "TE", 1.0, 1, None, datetime(2025, 8, 28)),
        ("A", 2025, "preseason", "WR", "WR", 1.0, 1, "00-0000001", pre),
        ("B", 2025, "preseason", "WR", "WR", 2.0, 2, None, pre),
        ("C", 2025, "preseason", "WR", "WR", 2.0, 2, None, pre),
        ("D", 2025, "preseason", "WR", "RB", 2.5, 4, None, pre),
        ("E", 2025, "preseason", "WR", "WR", 3.0, 4, None, pre),
        ("A", 2025, "ros", "WR", "WR", 2.0, 1, "00-0000001", datetime(2025, 10, 4)),
        ("K", 2025, "weekly", "QB", "QB", 1.0, 1, None, datetime(2025, 10, 4)),
        # scraped in January: still the 2025 season
        ("K", 2025, "weekly", "QB", "QB", 1.0, 1, None, datetime(2026, 1, 3)),
    ]
    assert con.execute(
        "SELECT player_owned_avg, player_owned_espn FROM fact_ranking WHERE page_kind = 'weekly' "
        "AND scrape_date = DATE '2025-10-03'"
    ).fetchone() == (88.5, 90.0)
    m = manifest["fact_ranking"]
    notes = json.loads(m["notes"])
    assert notes["rows_by_reason"] == {
        "kept": 10, "not_a_qb_rb_wr_te_page": 3, "not_qb_rb_wr_te_player": 1,
        "season_not_built": 1,
    }  # fmt: skip
    assert notes["n_rows_other_ecr_types"] == 1
    assert (m["n_source_rows"], m["n_dropped_null_key"], m["n_dropped_duplicates"]) == (15, 5, 1)
    assert notes["n_rows_listed_on_another_positions_page"] == 1
    assert m["seasons"] == "[2025]"
    con.close()


def test_page_classification_sql_covers_the_real_page_names():
    """Every page name seen in the archive (docs/waiver_radar.md) lands in the right kind."""
    cases = {  # (ecr_type, fp_page, page_type) -> (page_kind, page_pos)
        ("rp", "/nfl/rankings/ppr-wr-cheatsheets.php", "redraft-wr"): ("preseason", "WR"),
        ("rp", "/nfl/rankings/qb-cheatsheets.php", "redraft-qb"): ("preseason", "QB"),
        ("rp", "ppr-rb-cheatsheets", "redraft-offense"): ("preseason", "RB"),
        ("rp", "ppr-te-cheatsheets", "dynasty-offense"): (None, "TE"),
        ("rp", "/nfl/rankings/ros-ppr-te.php", "redraft-te"): ("ros", "TE"),
        ("rp", "ros-qb", "redraft-offense"): ("ros", "QB"),
        ("wp", "/nfl/rankings/ppr-rb.php", "weekly-rb"): ("weekly", "RB"),
        ("wp", "qb", "weekly-offense"): ("weekly", "QB"),
        ("wp", "ppr-wr", "redraft-offense"): ("weekly", "WR"),
        ("rp", "/nfl/rankings/dst-cheatsheets.php", "redraft-dst"): (None, None),
        ("rp", "/nfl/rankings/db-cheatsheets.php", "redraft-db"): (None, None),
        ("rp", "ros-dst", "redraft-kdst"): (None, None),
        ("wp", "/nfl/rankings/dl.php", "weekly-dl"): (None, None),
        ("wp", "/nfl/rankings/k.php", "weekly-k"): (None, None),
        ("ro", "ppr-cheatsheets", "redraft-offense"): (None, None),
    }
    con = duckdb.connect()
    sql = (
        f"SELECT {ids.ranking_page_kind_sql('e', 'p', 't')}, {ids.ranking_page_pos_sql('p')} "
        "FROM (SELECT CAST(? AS VARCHAR) AS e, CAST(? AS VARCHAR) AS p, CAST(? AS VARCHAR) AS t)"
    )
    for (etype, page, ptype), (kind, pos) in cases.items():
        got = con.execute(sql, [etype, page, ptype]).fetchone()
        assert got[0] == kind, (etype, page, ptype)
        if kind is not None:
            assert got[1] == pos, page


# --------------------------------------------------------------------------------------
# The pool (ECR season)
# --------------------------------------------------------------------------------------


def test_pool_week_1_ecr_season(world):
    db, _ = world
    df = _pool(db, 2025, 1)
    assert df.columns == list(wp.POOL_COLUMNS)
    assert df.get_column("method").unique().to_list() == ["ecr"]
    p = _by_player(df)
    assert set(p) == {gid(n) for n in (*range(101, 110), 111, 112, 113, 114, 115, 116)}
    got = {int(k[-3:]): (r["in_pool"], r["excluded_by"]) for k, r in p.items()}
    assert got == {
        101: (False, "preseason+ppg"),  # WR sheet 1, PPG shares rank 2
        102: (False, "preseason"),  # WR sheet rank 2 = N: out
        103: (True, None),  # rank 3 = N + 1 (the duplicate row and the earlier sheet ignored)
        104: (False, "ppg"),  # unranked before kickoff (the later sheet does not count)
        105: (False, "ppg"),  # 8 per game shares rank 2 with 101 and 106 ('min' ties)
        106: (False, "ppg"),  # listed only on an IDP page: unranked
        107: (False, "ppg"),  # RB sheet rank 3 > 1, RB PPG rank 1
        108: (True, None),
        109: (True, None),  # 6 per game: rank 5 after the three-way tie at 2
        111: (False, "preseason"),  # not on the WR sheet: his RB-sheet rank 1 vs the RB cutoff
        112: (True, None),  # RB listed on the WR sheet: rank 3 among WRs
        113: (True, None),  # RB PPG rank 2 > 1
        114: (True, None),  # practice squad (DEV) counts as rostered
        115: (True, None),  # inactive (INA) counts as rostered
        116: (True, None),
    }
    assert [p[gid(n)]["ppg_pos_rank"] for n in (104, 101, 105, 106, 109, 103)] == [
        1,
        2,
        2,
        2,
        5,
        10,
    ]
    assert (p[gid(111)]["preseason_pos_rank"], p[gid(111)]["preseason_rank_pos"]) == (1, "RB")
    assert (p[gid(112)]["preseason_pos_rank"], p[gid(112)]["preseason_rank_pos"]) == (3, "WR")
    assert p[gid(104)]["preseason_source"] == "unranked"
    assert p[gid(104)]["preseason_pos_rank"] is None
    assert (p[gid(101)]["ppg_to_date"], p[gid(101)]["games_to_date"]) == (8.0, 1)
    assert p[gid(108)]["ppg_to_date"] is None and p[gid(108)]["games_to_date"] == 0
    assert p[gid(101)]["prior_season_ppg"] is None  # 2024 is not in the warehouse
    assert df.get_column("as_of").unique().to_list() == [datetime(2025, 9, 9, 14, tzinfo=UTC)]
    # rostership from the latest weekly scrape before the as-of (2025-09-05)
    assert (p[gid(104)]["owned_avg"], p[gid(101)]["owned_espn"]) == (30.0, 98.0)


def test_pool_statuses_positions_and_roster_changes(world):
    db, _ = world
    p2 = _by_player(_pool(db, 2025, 2))
    # RES and CUT are not rostered players; a player missing from his team's latest roster
    # (released without a CUT row) is gone; teams off in week 2 keep their week-1 rows
    assert gid(107) not in p2 and gid(108) not in p2 and gid(116) not in p2
    assert p2[gid(105)]["roster_week"] == 1 and p2[gid(101)]["roster_week"] == 2
    # 109 changed position: a WR at week 1's as-of, a RB (ranked among RBs) at week 2's
    assert _by_player(_pool(db, 2025, 1))[gid(109)]["position"] == "WR"
    assert (p2[gid(109)]["position"], p2[gid(109)]["ppg_pos_rank"]) == ("RB", 2)
    # 107 (RES) is out of the universe but still holds RB PPG rank 1
    assert p2[gid(113)]["ppg_pos_rank"] == 3
    assert (p2[gid(101)]["ppg_to_date"], p2[gid(101)]["games_to_date"]) == (6.5, 2)
    assert p2[gid(101)]["excluded_by"] == "preseason"  # PPG rank 4 now
    # the cheat sheet scraped after kickoff is visible now, and still not the preseason list
    assert p2[gid(104)]["preseason_source"] == "unranked"
    assert p2[gid(104)]["owned_avg"] == 95.0
    # 110 signs in week 3: not rostered at week 2's as-of, rostered at week 3's
    assert gid(110) not in p2
    p3 = _by_player(_pool(db, 2025, 3))
    assert p3[gid(110)]["in_pool"] and p3[gid(110)]["games_to_date"] == 0
    # the Tuesday game of week 3 is after the as-of: 105 and 106 still have one game
    assert (p3[gid(105)]["games_to_date"], p3[gid(105)]["ppg_to_date"]) == (1, 8.0)
    # rostership: 101 is missing from the latest scrape (09-19), his 09-12 row is used;
    # 103's last row (09-05) is too old
    assert (p3[gid(101)]["owned_avg"], p3[gid(101)]["owned_scrape_date"]) == (
        99.0, datetime(2025, 9, 12).date(),
    )  # fmt: skip
    assert p3[gid(105)]["owned_avg"] == 40.0 and p3[gid(103)]["owned_avg"] is None


def test_pool_methods(world):
    db, _ = world
    fallback = _pool(db, 2025, 1, method="prior_ppg")
    assert fallback.get_column("method").unique().to_list() == ["prior_ppg"]
    # no 2024 season in the warehouse: nobody has a prior-season rank
    assert set(fallback.get_column("preseason_source")) == {"unranked"}
    assert _pool(db, 2014, 2).get_column("method").unique().to_list() == ["prior_ppg"]
    with pytest.raises(ValueError, match="method 'ecr' is not available"):
        _pool(db, 2014, 2, method="ecr")
    with pytest.raises(ValueError, match="method must be one of"):
        _pool(db, 2025, 1, method="adp")


# --------------------------------------------------------------------------------------
# The pool (fallback season)
# --------------------------------------------------------------------------------------


def test_pool_fallback_season(world):
    db, _ = world
    # post-game rosters: week 1's roster is public only at week 2's as-of, so week 1 is empty
    empty = _pool(db, 2014, 1)
    assert empty.height == 0 and empty.columns == list(wp.POOL_COLUMNS)
    p = _by_player(_pool(db, 2014, 2))
    assert set(p) == {gid(n) for n in (201, 202, 203, 204, 205, 206, 208)}  # 207 is RES
    got = {int(k[-3:]): (r["preseason_source"], r["preseason_pos_rank"], r["in_pool"],
                         r["excluded_by"]) for k, r in p.items()}  # fmt: skip
    assert got == {
        201: ("prior_ppg", 1, False, "preseason"),
        202: ("prior_ppg", 2, False, "preseason"),  # rank N: out
        208: ("prior_ppg", 3, True, None),  # a TE last season, ranked among WRs now: N + 1
        203: ("prior_ppg", 4, True, None),
        204: ("unranked", None, False, "ppg"),  # 1 game last season < 2; PPG rank 2 now
        205: ("rookie_draft", None, False, "rookie_draft"),  # round 1 rookie
        206: ("unranked", None, True, None),  # round 3 rookie: in the pool
    }
    assert (p[gid(201)]["prior_season_ppg"], p[gid(201)]["prior_season_games"]) == (10.0, 3)
    assert (p[gid(204)]["prior_season_ppg"], p[gid(204)]["prior_season_games"]) == (50.0, 1)
    assert p[gid(204)]["ppg_pos_rank"] == 2  # 207 (on IR) holds rank 1
    assert p[gid(201)]["team"] == "HOU"  # the roster's HST
    assert all(r["owned_avg"] is None for r in p.values())  # no rostership before 2020
    # 204 was cut after week 1: at week 3's as-of (week-2 roster public) he is gone
    assert gid(204) not in _by_player(_pool(db, 2014, 3))


def test_thresholds_derive_from_league_config():
    lg = league()
    assert PoolConfig.model_fields.keys() >= {"roster_statuses", "ownership_available_below"}
    default = wp.PoolRules.from_config(lg)
    assert default.cutoffs == {"QB": 18, "RB": 36, "WR": 36, "TE": 18}
    assert default.cutoffs == {
        p: round(n * lg.candidate_pool_multiplier) for p, n in lg.starter_rank_threshold.items()
    }
    assert default.roster_statuses == ("ACT", "INA", "DEV")
    bigger = League(**{**lg.model_dump(), "candidate_pool_multiplier": 2.0})
    assert wp.PoolRules.from_config(bigger).cutoffs == {"QB": 24, "RB": 48, "WR": 48, "TE": 24}


def test_changing_the_cutoff_changes_the_pool(world):
    db, _ = world
    wide = wp.PoolRules(**{**RULES.__dict__, "cutoffs": {"QB": 1, "RB": 1, "WR": 3, "TE": 1}})
    with AsOfView(db, weekly_as_of(db, 2025, 1)) as v:
        p = _by_player(wp.candidate_pool(v, 2025, 1, rules=wide))
    assert p[gid(103)]["excluded_by"] == "preseason"  # rank 3 is inside N = 3 now
    assert p[gid(112)]["excluded_by"] == "preseason"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"roster_statuses": []}, "non-empty list"),
        ({"roster_statuses": ["ACT", "act"]}, "upper-case"),
        ({"prior_season_min_games": 0}, "between 1 and 17"),
        ({"rookie_drafted_rounds": 9}, "between 0 and 7"),
        ({"ownership_available_below": 120}, "percentage"),
        ({"ownership_low": 90}, "below pool.ownership_high"),
        ({"typo": 1}, "Extra inputs"),
    ],
)
def test_pool_config_is_validated(change, message):
    from pydantic import ValidationError

    good = league().pool.model_dump()
    with pytest.raises(ValidationError, match=message):
        PoolConfig(**{**good, **change})


# --------------------------------------------------------------------------------------
# Which as-ofs count, history
# --------------------------------------------------------------------------------------


def test_pool_weeks_only_counts_weeks_whose_games_are_in(world):
    db, _ = world
    weeks = [(s, w) for s, w, _ in wp.pool_weeks(db, [2014, 2025])]
    # 2025 week 3 has an unplayed Sunday game (no result in the cache): left out, and so is
    # week 4; the Tuesday game of week 3 alone would not hold it back
    assert weeks == [(2014, 1), (2014, 2), (2014, 3), (2025, 1), (2025, 2)]
    assert [(s, w) for s, w, _ in wp.pool_weeks(db, [2025], weeks=[2])] == [(2025, 2)]
    hist = wp.pool_history(db, [2014, 2025], methods=("ecr", "prior_ppg"), rules=RULES)
    per = hist.group_by("season", "week", "method").len().sort("season", "week", "method")
    assert per.rows() == [
        (2014, 2, "prior_ppg", 7), (2014, 3, "prior_ppg", 6),
        (2025, 1, "ecr", 15), (2025, 1, "prior_ppg", 15),
        (2025, 2, "ecr", 12), (2025, 2, "prior_ppg", 12),
    ]  # fmt: skip
    auto = wp.pool_history(db, [2025], rules=RULES)
    assert set(auto.get_column("method")) == {"ecr"}
    assert wp.pool_history(db, [], rules=RULES).columns == list(wp.POOL_COLUMNS)


# --------------------------------------------------------------------------------------
# Point in time: the leakage harness
# --------------------------------------------------------------------------------------


def _builder(season: int, week: int, view_wrapper=None):
    def build(v: AsOfView) -> pl.DataFrame:
        return wp.candidate_pool(
            view_wrapper(v) if view_wrapper else v, season, week, rules=RULES
        ).drop("as_of")

    return build


@pytest.mark.parametrize(
    ("season", "week"), [(2025, 1), (2025, 2), (2025, 3), (2014, 2), (2014, 3)]
)
def test_pool_passes_the_leakage_harness(world, season, week):
    """Deleting or scrambling everything not yet public at the as-of changes nothing: 2025
    week 3 is a split week, 2014 has post-game rosters."""
    db, _ = world
    out = assert_future_invariant(
        _builder(season, week), db, weekly_as_of(db, season, week), key=["gsis_id"]
    )
    assert out.height > 0


class _Rewritten:
    """A deliberately LEAKY view: rewrites one piece of the pool's SQL (e.g. to read the raw
    warehouse ``wh.``), to prove the harness catches such a change in the real pool code."""

    def __init__(self, view: AsOfView, old: str, new: str):
        self._view, self._old, self._new = view, old, new

    @property
    def as_of(self) -> datetime:
        return self._view.as_of

    def sql(self, query: str, params=None) -> pl.DataFrame:
        assert self._old in query, "the rewrite no longer matches the pool's SQL"
        return self._view.sql(query.replace(self._old, self._new), params)


@pytest.mark.parametrize(
    ("season", "week", "old", "new", "what"),
    [
        # rosters: a post-game season's week-2 roster is not public at week 2's as-of
        (2014, 2, "FROM fact_roster_week WHERE season", "FROM wh.fact_roster_week WHERE season",
         "roster"),
        # PPG: 'week <= 3' is not time; week 3's Tuesday game was not played at its as-of
        (2025, 3, "FROM fact_player_week WHERE season = 2025 AND season_type = 'REG'",
         "FROM wh.fact_player_week WHERE season = 2025 AND season_type = 'REG' AND week <= 3",
         "ppg"),
        # rostership: the next Friday's scrape is not public at the Tuesday as-of
        (2025, 3, "player_owned_espn FROM fact_ranking",
         "player_owned_espn FROM wh.fact_ranking", "ownership"),
    ],
)  # fmt: skip
def test_a_leaky_pool_fails_the_harness(world, season, week, old, new, what):
    db, _ = world
    builder = _builder(season, week, lambda v: _Rewritten(v, old, new))
    with pytest.raises(LeakageError):
        assert_future_invariant(builder, db, weekly_as_of(db, season, week), key=["gsis_id"])


def test_pool_reads_only_through_the_view(world):
    db, _ = world
    with AsOfView(db, weekly_as_of(db, 2025, 2)) as v:
        wp.candidate_pool(v, 2025, 2, rules=RULES)
        used = v.tables_used
    assert used >= {"fact_roster_week", "fact_player_week", "fact_ranking", "dim_player"}
    sql = wp._base_sql(2025, RULES)
    assert "wh." not in sql.lower()


# --------------------------------------------------------------------------------------
# Report and CLI
# --------------------------------------------------------------------------------------


def test_report_is_deterministic_and_complete(world):
    db, _ = world
    one = build_report(db, [2014, 2025], rules=RULES)
    two = build_report(db, [2014, 2025], rules=RULES)
    assert one.markdown == two.markdown and one.csv_rows == two.csv_rows
    md = one.markdown
    for heading in ("## Method and roster snapshot per season", "## Pool size per season",
                    "## Validation (a)", "## Validation (b)", "### owned_avg",
                    "### owned_espn"):  # fmt: skip
        assert heading in md, heading
    # 2014's week-1 as-of has no public roster yet (post-game snapshots): an empty pool
    assert "| 2014 | prior_ppg | 2 | 2-3 | post_game | 20.00% |" in md
    assert "| 2025 | ecr | 2 | 1-2 | game_day | 0.00% |" in md
    assert list(one.csv_rows[0]) == list(CSV_COLUMNS)
    row = next(r for r in one.csv_rows if (r["season"], r["week"], r["method"],
                                           r["position"]) == (2025, 1, "ecr", "WR"))  # fmt: skip
    assert (row["n_universe"], row["n_pool"], row["used"]) == (10, 4, True)
    assert (row["n_excluded_preseason_only"], row["n_excluded_ppg_only"],
            row["n_excluded_both"]) == (2, 3, 1)  # fmt: skip


def test_cli_pool_and_report(world, tmp_path):
    db, _ = world
    runner = CliRunner()
    res = runner.invoke(app, ["radar", "pool", "2025", "1", "--db", str(db)])
    assert res.exit_code == 0, res.output
    assert "2025 week 1, as-of 2025-09-09 14:00 UTC, method ecr" in res.output
    # the shipped cutoffs (WR 36 ...): only the players without a game and a rank are left
    assert "3 in the pool of 15 rostered players" in res.output
    assert "Player 108" in res.output and "Player 104" not in res.output
    res = runner.invoke(app, ["radar", "pool", "2025", "1", "--db", str(db), "--all",
                              "--pos", "wr", "--limit", "0"])  # fmt: skip
    assert res.exit_code == 0 and "Player 104" in res.output and "Player 107" not in res.output
    res = runner.invoke(app, ["radar", "pool", "2014", "2", "--db", str(db), "--method", "ecr"])
    assert res.exit_code == 1 and "not available" in res.output
    res = runner.invoke(app, ["radar", "pool", "2014", "2", "--db", str(tmp_path / "no.duckdb")])
    assert res.exit_code == 1 and "warehouse not found" in res.output
    out = tmp_path / "rep" / "pool.md"
    args = ["radar", "pool-report", "--db", str(db), "--out", str(out), "--start", "2014",
            "--end", "2025"]  # fmt: skip
    res = runner.invoke(app, args)
    assert res.exit_code == 0, res.output
    first = out.read_text(), out.with_suffix(".csv").read_text()
    assert first[1].splitlines()[0] == ",".join(CSV_COLUMNS)
    assert runner.invoke(app, args).exit_code == 0
    assert (out.read_text(), out.with_suffix(".csv").read_text()) == first
    bad = runner.invoke(app, ["radar", "pool-report", "--db", str(db), "--out", str(out),
                              "--start", "2026", "--end", "2025"])  # fmt: skip
    assert bad.exit_code == 2


# --------------------------------------------------------------------------------------
# Registry and schema
# --------------------------------------------------------------------------------------


def test_new_tables_are_registered():
    assert av.kind_of("fact_roster_week") == "event"
    assert av.kind_of("fact_ranking") == "event"
    assert set(av.TABLE_AVAILABILITY["fact_roster_week"].hindsight_columns) == {"rookie_year"}
    assert sc.tables()["fact_roster_week"].primary_key == ("season", "week", "gsis_id")
    assert "ff_rankings_all" in sc.SOURCE_DATASETS and "rosters_weekly" in sc.SOURCE_DATASETS


# --------------------------------------------------------------------------------------
# Real data (opt-in: `uv run pytest -m realdata`; builds into a temp file, never downloads)
# --------------------------------------------------------------------------------------


@pytest.mark.realdata
def test_real_pool_sizes_and_invariants(real_full_db):
    path, _ = real_full_db
    rules = wp.PoolRules.from_config()
    hist = wp.pool_history(path, [2019, 2023], rules=rules)
    assert set(hist.get_column("method")) == {"prior_ppg", "ecr"}
    sizes = (
        hist.group_by("season", "week", "position")
        .agg(pl.len().alias("universe"), pl.col("in_pool").sum().alias("pool"))
        .group_by("season", "position")
        .agg(pl.col("universe").mean(), pl.col("pool").mean(), pl.col("week").n_unique())
        .sort("season", "position")
    )
    print(sizes)  # the numbers are in the report too (reports/waiver_radar/pool_sizes.md)
    for r in sizes.iter_rows(named=True):
        n = rules.cutoffs[r["position"]]
        assert r["week"] == 17 if r["season"] == 2019 else r["week"] == 18
        # every week the pool is most of the universe but never all of it
        assert n < r["universe"] < 400, r
        assert r["universe"] - 3 * n < r["pool"] < r["universe"] - n / 2, r
    cut = pl.col("position").replace_strict(dict(rules.cutoffs))
    list_cut = pl.col("preseason_rank_pos").replace_strict(dict(rules.cutoffs), default=None)
    bad = hist.filter(
        pl.col("in_pool")
        & (
            (pl.col("ppg_pos_rank") <= cut).fill_null(False)
            | (pl.col("preseason_pos_rank") <= list_cut).fill_null(False)
            | (pl.col("preseason_source") == "rookie_draft")
        )
    )
    assert bad.height == 0
    # every universe row is the player's roster row of that week (position and status)
    con = duckdb.connect(str(path), read_only=True)
    con.register("h", hist.select("season", "roster_week", "gsis_id", "position", "status"))
    assert con.execute(
        "SELECT count(*) FROM h LEFT JOIN fact_roster_week r ON r.season = h.season "
        "AND r.week = h.roster_week AND r.gsis_id = h.gsis_id "
        "WHERE r.position IS DISTINCT FROM h.position OR r.status IS DISTINCT FROM h.status"
    ).fetchone() == (0,)
    con.close()


@pytest.mark.realdata
def test_real_roster_regimes_and_team_codes(real_full_db):
    path, manifest = real_full_db
    notes = json.loads(manifest["fact_roster_week"]["notes"])
    regimes = {int(s): r["regime"] for s, r in notes["regime_by_season"].items()}
    assert {s for s, r in regimes.items() if r == "post_game"} == set(range(2002, 2016))
    assert notes["n_rows_unknown_team"] == 0
    con = duckdb.connect(str(path), read_only=True)
    # each roster-only team code names the same team as player_stats for the same players
    for code, team in sc.ROSTER_TEAM_ALIASES.items():
        n, agree = con.execute(
            "SELECT count(*), count(*) FILTER (WHERE p.team = r.team) FROM fact_roster_week r "
            "JOIN fact_player_week p ON p.player_id = r.gsis_id AND p.season = r.season "
            "AND p.week = r.week WHERE r.team_raw = ?",
            [code],
        ).fetchone()
        assert n > 1000 and agree / n > 0.99, (code, team, n, agree)
    con.close()


@pytest.mark.realdata
def test_real_preseason_list_matches_the_id_reports_pool(real_full_db):
    """fact_ranking + the shared selection rules give exactly the id report's preseason pool
    (one definition of the preseason cheat sheet, two readers)."""
    path, _ = real_full_db
    cut = league().candidate_pool_cutoffs()
    con = duckdb.connect()
    con.execute(f"ATTACH {sc.sql_str(str(path))} AS wh (READ_ONLY)")
    con.execute("CREATE VIEW dim_week AS SELECT * FROM wh.dim_week")
    con.execute("CREATE VIEW fact_ranking AS SELECT * FROM wh.fact_ranking")
    raw = nv.cache_path("ff_rankings_all", None)
    con.execute(
        f"CREATE VIEW raw_ff_rankings_all AS SELECT * FROM read_parquet({sc.sql_str(str(raw))})"
    )
    pool = ids.pool_coverage(con, cut)
    whens = " ".join(f"WHEN '{p}' THEN {n}" for p, n in cut.items())
    mine = con.execute(f"""
        WITH pre_pages AS (SELECT * FROM fact_ranking WHERE page_kind = 'preseason'),
        s AS ({ids.preseason_scrape_sql("pre_pages")})
        SELECT p.season, p.fantasypros_id, p.pos FROM pre_pages p JOIN s USING (season, scrape_date)
        WHERE p.pos = p.page_pos AND p.pos_rank <= CASE p.pos {whens} END
        ORDER BY 1, 2""").fetchall()
    theirs = con.execute(
        f"SELECT season, source_id, position FROM ({pool.rows_sql}) ORDER BY 1, 2"
    ).fetchall()
    assert mine == theirs and len(mine) == 7 * 108
    con.close()


@pytest.mark.realdata
@pytest.mark.parametrize(("season", "week"), [(2014, 2), (2023, 6)])
def test_real_pool_passes_the_leakage_harness(real_full_db, season, week):
    path, _ = real_full_db

    def build(v: AsOfView) -> pl.DataFrame:
        return wp.candidate_pool(v, season, week).drop("as_of")

    out = assert_future_invariant(build, path, weekly_as_of(path, season, week), key=["gsis_id"])
    assert out.height > 300
