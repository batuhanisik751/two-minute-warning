"""Waiver Radar labels (C2): weekly finishes, the window (byes, season end), played weeks,
positions, pending labels, point-in-time direction, the report and the CLI.

The tests share one synthetic warehouse (``world``); every id and name is made up.

- **2025**, 8 regular-season weeks (week 8 is the last), 8 teams. Byes: KC and MIA week 4,
  PHI and BUF weeks 3 and 5, DAL and SF week 8 (the last week). Week 2's SF at BUF is moved
  to Wednesday, after week 2's Tuesday as-of (a split week).
- **2026**, 7 weeks, 4 teams, the "live" season: week 2's DAL at SEA has a score but no stat
  lines yet, week 6's DAL at SEA and all of week 7 are not played.

Thresholds are tiny (``RULES``: QB 1, RB 2, WR 2, TE 1, FLEX 3) so ranks are easy to follow.
Points are whole numbers (receptions, 1 point each in full PPR).
"""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import polars as pl
import pytest
from typer.testing import CliRunner

from tests.conftest import PLAYERS_DTYPES, RawCache, frame, game
from twm import asof as asof_mod
from twm import ids
from twm.asof import weekly_as_of
from twm.cli import app
from twm.config import League, league
from twm.modules.waiver_radar import labels as lb
from twm.modules.waiver_radar.label_report import CSV_COLUMNS, build_label_report
from twm.modules.waiver_radar.pool import pool_history
from twm.sources import nflverse as nv
from twm.warehouse import build as wb

RULES = lb.LabelRules(starter_thresholds={"QB": 1, "RB": 2, "WR": 2, "TE": 1}, flex_rank=3)


def gid(n: int) -> str:
    return f"00-0090{n:03d}"


def pfr(n: int) -> str:
    return f"Synth{n:03d}"


# --------------------------------------------------------------------------------------
# The synthetic world
# --------------------------------------------------------------------------------------

# (away, home) per week
SCHED_2025 = {
    1: [("KC", "PHI"), ("DAL", "SEA"), ("LAC", "SF"), ("MIA", "BUF")],
    2: [("KC", "DAL"), ("PHI", "SEA"), ("LAC", "MIA"), ("SF", "BUF")],
    3: [("KC", "SEA"), ("DAL", "LAC"), ("SF", "MIA")],  # PHI, BUF off
    4: [("PHI", "DAL"), ("SEA", "LAC"), ("SF", "BUF")],  # KC, MIA off
    5: [("KC", "LAC"), ("DAL", "SF"), ("SEA", "MIA")],  # PHI, BUF off
    6: [("KC", "SF"), ("PHI", "LAC"), ("DAL", "MIA"), ("SEA", "BUF")],
    7: [("KC", "MIA"), ("PHI", "SF"), ("DAL", "BUF"), ("SEA", "LAC")],
    8: [("KC", "BUF"), ("PHI", "MIA"), ("SEA", "LAC")],  # DAL, SF off (last week)
}
SCHED_2026 = {w: [("KC", "PHI"), ("DAL", "SEA")] for w in range(1, 8)}
QB_OF = {"KC": 100, "PHI": 101, "DAL": 102, "SEA": 103, "LAC": 104, "SF": 105, "MIA": 106,
         "BUF": 107}  # fmt: skip

# player -> (name, position on his weekly roster); teams per week below
PLAYERS = {
    1: "Wr Boundary", 2: "Wr Tie B", 3: "Wr Tie C", 4: "Wr Dal", 5: "Wr Kc Tie", 6: "Wr Phi Tie",
    7: "Wr Sustained", 8: "Wr Filler Sea", 9: "Wr Filler Lac", 10: "Wr Kc Bye", 11: "Wr Phi Byes",
    12: "Wr Split", 20: "Te Played", 21: "Te Filler", 30: "Rb Traded", 31: "Rb Traded Bye",
    32: "Rb Filler Sea", 33: "Rb Filler Lac", 40: "Converted", 50: "Earlier Roster",
    51: "Snaps Only Pos", 52: "No Position", 53: "Full Back",
    **{n: f"Qb {t}" for t, n in QB_OF.items()},
}  # fmt: skip

# player -> {week: (team, roster position, fantasy points or None = no stat line)}
ALL8 = range(1, 9)
LINES: dict[int, dict[int, tuple[str, str, int | None]]] = {
    1: {w: ("SEA", "WR", {1: 30, 5: 30}.get(w, 1)) for w in ALL8},
    8: {w: ("SEA", "WR", 8) for w in ALL8},
    9: {w: ("LAC", "WR", 7) for w in ALL8},
    7: {w: ("LAC", "WR", {2: 25, 3: 25}.get(w, 2)) for w in ALL8},
    12: {2: ("SF", "WR", 40), 3: ("SF", "WR", 0)},
    10: {6: ("KC", "WR", 22)},
    11: {6: ("PHI", "WR", 21)},
    4: {7: ("DAL", "WR", 20)},
    2: {7: ("LAC", "WR", 15), 8: ("LAC", "WR", 12)},
    3: {7: ("MIA", "WR", 15)},
    5: {8: ("KC", "WR", 18)},
    6: {8: ("PHI", "WR", 18)},
    21: {w: ("LAC", "TE", 5) for w in ALL8},
    # inactive in week 2, blocking (snaps, no stat line) in week 3
    20: {1: ("SEA", "TE", 1), 2: ("SEA", "TE", None), 3: ("SEA", "TE", None),
         4: ("SEA", "TE", 3), 5: ("SEA", "TE", 9), 6: ("SEA", "TE", 1)},
    32: {w: ("SEA", "RB", 10) for w in ALL8},
    33: {w: ("LAC", "RB", 8) for w in ALL8},
    # DAL through week 4, then KC; KC through week 3, then DAL
    30: {**{w: ("DAL", "RB", 1) for w in (1, 2, 3, 4)},
         **{w: ("KC", "RB", {5: 20, 6: 20}.get(w, 1)) for w in (5, 6, 7, 8)}},
    31: {**{w: ("KC", "RB", 1) for w in (1, 2, 3)},
         **{w: ("DAL", "RB", {4: 20}.get(w, 1)) for w in (4, 5, 6, 7)}},
    # a WR on the roster through week 3, a RB from week 4 (today's position says TE)
    40: {**{w: ("SEA", "WR", 0) for w in (1, 2, 3)},
         **{w: ("SEA", "RB", {4: 9}.get(w, 0)) for w in (4, 5, 6)}},
    53: {1: ("SEA", "FB", 50)},
}  # fmt: skip
# rows without a same-week roster row: 50 has a week-1 roster row only; 51 has no roster row
# (his snap row says TE); 52 has neither
NO_ROSTER = {50: {1: ("MIA", 0), 2: ("MIA", 0)}, 51: {1: ("BUF", 0)}, 52: {1: ("MIA", 0)}}
ROSTER_ONLY = {50: {1: ("MIA", "WR")}}


def _games(season: int, sched: dict, start: date, *, moved=(), unplayed=()) -> list[dict]:
    out = []
    for w, pairs in sched.items():
        day = start + timedelta(days=7 * (w - 1))
        for away, home in pairs:
            gday, gtime = day.isoformat(), "13:00"
            if (w, away, home) in moved:  # a Wednesday game after the Tuesday as-of
                gday, gtime = (day + timedelta(days=3)).isoformat(), "19:00"
            result = None if (w, away, home) in unplayed else 3
            out.append(game(season, w, gday, gtime, away, home, result=result))
    return out


def _gid_of(games: list[dict], week: int, team: str) -> dict:
    return next(g for g in games if g["week"] == week and team in (g["home_team"], g["away_team"]))


def _stat(g: dict, n: int, team: str, points: int, position: str = "WR") -> dict:
    opp = g["away_team"] if team == g["home_team"] else g["home_team"]
    return {"player_id": gid(n), "player_name": PLAYERS[n], "position": position,
            "season": g["season"], "week": g["week"], "season_type": "REG",
            "game_id": g["game_id"], "team": team, "opponent_team": opp,
            "receptions": points, "fantasy_points_ppr": float(points)}  # fmt: skip


def _roster(season: int, week: int, n: int, team: str, pos: str) -> dict:
    return {
        "season": season,
        "week": week,
        "game_type": "REG",
        "team": team,
        "position": pos,
        "full_name": PLAYERS[n],
        "gsis_id": gid(n),
        "status": "ACT",
        "pfr_id": pfr(n),
    }


def _snap(g: dict, n: int, team: str, pos: str, snaps: float) -> dict:
    opp = g["away_team"] if team == g["home_team"] else g["home_team"]
    return {
        "game_id": g["game_id"],
        "season": g["season"],
        "game_type": "REG",
        "week": g["week"],
        "player": PLAYERS[n],
        "pfr_player_id": pfr(n),
        "position": pos,
        "team": team,
        "opponent": opp,
        "offense_snaps": snaps,
        "offense_pct": snaps / 60,
    }


def world_2025() -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    games = _games(2025, SCHED_2025, date(2025, 9, 7), moved={(2, "SF", "BUF")})
    stats, rosters, snaps = [], [], []
    for team, n in QB_OF.items():  # every game has stat lines
        for g in games:
            if team in (g["home_team"], g["away_team"]):
                stats.append(_stat(g, n, team, 10 + n - 100, "QB"))
                rosters.append(_roster(2025, g["week"], n, team, "QB"))
    for n, weeks in LINES.items():
        for w, (team, pos, pts) in weeks.items():
            rosters.append(_roster(2025, w, n, team, pos))
            if pts is not None:
                # nflverse's own position column is today's value: say TE for the converted WR
                stats.append(_stat(_gid_of(games, w, team), n, team, pts,
                                   "TE" if n == 40 else pos))  # fmt: skip
    for n, weeks in NO_ROSTER.items():
        for w, (team, pts) in weeks.items():
            stats.append(_stat(_gid_of(games, w, team), n, team, pts))
    for n, weeks in ROSTER_ONLY.items():
        for w, (team, pos) in weeks.items():
            rosters.append(_roster(2025, w, n, team, pos))
    snaps += [
        _snap(_gid_of(games, 2, "SEA"), 20, "SEA", "TE", 0.0),  # special teams only: not played
        _snap(_gid_of(games, 3, "SEA"), 20, "SEA", "TE", 30.0),  # blocking: played, no stats
        _snap(_gid_of(games, 4, "SEA"), 20, "SEA", "TE", 40.0),
        _snap(_gid_of(games, 1, "BUF"), 51, "BUF", "TE", 10.0),  # his only position source
    ]
    return games, stats, rosters, snaps


def world_2026() -> tuple[list[dict], list[dict], list[dict]]:
    games = _games(2026, SCHED_2026, date(2026, 9, 13),
                   unplayed={(6, "DAL", "SEA"), (7, "KC", "PHI"), (7, "DAL", "SEA")})  # fmt: skip
    stats, rosters = [], []
    for g in games:
        for team in (g["away_team"], g["home_team"]):
            n = QB_OF[team]
            rosters.append(_roster(2026, g["week"], n, team, "QB"))
            no_stats = g["result"] is None or (g["week"] == 2 and "DAL" in g["game_id"])
            if not no_stats:
                stats.append(_stat(g, n, team, 10 + n - 100, "QB"))
    return games, stats, rosters


def _players() -> pl.DataFrame:
    rows = [{"gsis_id": gid(n), "display_name": name, "position": "TE" if n == 40 else "WR",
             "pfr_id": pfr(n)} for n, name in PLAYERS.items()]  # fmt: skip
    return frame(rows, PLAYERS_DTYPES)


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The synthetic 2025/2026 warehouse, built once for the module (offline)."""
    tmp = tmp_path_factory.mktemp("labels_world")
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
        cache.write("players", None, _players())
        g25, s25, r25, sn25 = world_2025()
        cache.write_season(2025, g25, player_stats=s25, rosters=r25, snaps=sn25, injuries=[])
        g26, s26, r26 = world_2026()
        cache.write_season(2026, g26, player_stats=s26, rosters=r26, snaps=[], injuries=[])
        db = tmp / "labels.duckdb"
        wb.build_warehouse([2025, 2026], db_path=db)
    return db


def pool_rows(db: Path, season: int, entries: list[tuple[int, int, str]]) -> pl.DataFrame:
    """Hand-made pool rows: (as-of week, player, team at the as-of)."""
    rows = [
        {"season": season, "week": w, "as_of": weekly_as_of(db, season, w), "gsis_id": gid(n),
         "name": PLAYERS[n], "team": team, "in_pool": True}
        for w, n, team in entries
    ]  # fmt: skip
    return pl.DataFrame(rows, schema_overrides={"season": pl.Int32, "week": pl.Int32})


def labels(db: Path, season: int, entries: list[tuple[int, int, str]], **kw) -> dict:
    df = lb.label_rows(db, pool_rows(db, season, entries), rules=kw.pop("rules", RULES), **kw)
    return {(r["week"], int(r["gsis_id"][-3:])): r for r in df.iter_rows(named=True)}


def finish(db: Path, season: int = 2025) -> dict:
    df = lb.weekly_finishes(db, [season], rules=RULES)
    return {(r["week"], int(r["gsis_id"][-3:])): r for r in df.iter_rows(named=True)}


# --------------------------------------------------------------------------------------
# Weekly finishes
# --------------------------------------------------------------------------------------


def test_weekly_finishes_columns_ranks_and_ties(world):
    df = lb.weekly_finishes(world, [2025], rules=RULES)
    assert df.columns == list(lb.FINISH_COLUMNS)
    f = finish(world)
    # rank exactly at the threshold (2) counts, threshold + 1 does not; FLEX is rank <= 3
    assert (f[4, 8]["pos_rank"], f[4, 9]["pos_rank"], f[4, 7]["pos_rank"]) == (1, 2, 3)
    assert f[4, 9]["is_starter_finish"] and not f[4, 7]["is_starter_finish"]
    assert f[4, 7]["is_flex_finish"] and not f[4, 1]["is_flex_finish"]  # rank 4
    # ties share the better rank ('min'): 20, 15, 15 -> 1, 2, 2 (both at the threshold count)
    assert [f[7, n]["pos_rank"] for n in (4, 2, 3, 8)] == [1, 2, 2, 4]
    assert f[7, 2]["is_starter_finish"] and f[7, 3]["is_starter_finish"]
    # 18, 18, 12 -> 1, 1, 3: the 12 is not a starter finish ('dense' would make it 2)
    assert [f[8, n]["pos_rank"] for n in (5, 6, 2)] == [1, 1, 3]
    assert not f[8, 2]["is_starter_finish"]
    assert f[4, 8]["n_ranked"] == 4 and f[4, 8]["fantasy_points"] == 8.0
    # QB and TE finishes are never FLEX finishes
    assert f[5, 20]["is_starter_finish"] and not f[5, 20]["is_flex_finish"]
    assert all(r["is_week_final"] for r in f.values())


def test_weekly_finish_positions_are_point_in_time(world):
    f = finish(world)
    # the converted player: a WR on the week-3 roster, a RB from week 4 (today's position and
    # nflverse's stat-row position say TE; neither is used)
    assert (f[3, 40]["position"], f[4, 40]["position"]) == ("WR", "RB")
    assert f[4, 40]["pos_rank"] == 3 and not f[4, 40]["is_starter_finish"]  # 9 pts: 3rd RB
    assert {r["position_source"] for k, r in f.items() if k[1] == 40} == {"roster"}
    # fallbacks: his latest earlier roster week; the game's snap-count position; nothing
    assert (f[2, 50]["position"], f[2, 50]["position_source"]) == ("WR", "roster_earlier")
    assert (f[1, 50]["position_source"], f[1, 51]["position_source"]) == ("roster", "snaps")
    assert (f[1, 51]["position"], f[1, 51]["pos_rank"]) == ("TE", 3)
    assert (f[1, 52]["position_source"], f[1, 52]["position"]) == ("none", None)
    assert f[1, 52]["pos_rank"] is None and not f[1, 52]["is_starter_finish"]
    # a fullback is not ranked (with the RBs or at all): the RB ranks ignore his 50 points
    assert (f[1, 53]["position"], f[1, 53]["pos_rank"]) == ("FB", None)
    assert (f[1, 32]["pos_rank"], f[1, 33]["pos_rank"]) == (1, 2)


# --------------------------------------------------------------------------------------
# Labels: boundaries, byes, season end, played, trades, positions
# --------------------------------------------------------------------------------------


def test_week_n_never_counts_week_n_plus_1_does(world):
    got = labels(world, 2025, [(1, 1, "SEA"), (2, 1, "SEA"), (4, 1, "SEA"), (5, 1, "SEA")])
    # starter finishes (rank 1) in weeks 1 and 5 only
    one = got[1, 1]
    assert (one["window_weeks"], one["window_ranks"]) == ([2, 3, 4], [5, 4, 4])
    assert one["y_hit"] is False and one["n_starter_finishes"] == 0  # week 1 = N: not counted
    assert got[2, 1]["y_hit"] and got[2, 1]["window_weeks"] == [3, 4, 5]  # N+3
    assert got[4, 1]["y_hit"] and got[4, 1]["window_ranks"][0] == 1  # N+1
    assert got[5, 1]["y_hit"] is False  # week 5 = N
    assert one["label_status"] == "final" and one["window_points"] == [1.0, 1.0, 1.0]


def test_threshold_ties_and_flex_in_labels(world):
    got = labels(world, 2025, [(3, 9, "LAC"), (3, 7, "LAC"), (5, 2, "LAC"), (5, 3, "MIA")])
    assert got[3, 9]["window_ranks"][0] == 2 and got[3, 9]["y_hit"]  # exactly the threshold
    assert got[3, 7]["window_ranks"] == [3, 4, 5] and got[3, 7]["y_hit"] is False  # + 1
    assert got[3, 7]["n_flex_finishes"] == 1 and got[3, 7]["best_rank"] == 3
    b = got[5, 2]
    assert (b["window_weeks"], b["window_ranks"]) == ([6, 7, 8], [None, 2, 3])
    assert (b["n_starter_finishes"], b["y_hit"], b["y_sustained"]) == (1, True, False)
    assert (b["n_window_played"], b["n_flex_finishes"]) == (2, 2)
    assert got[5, 3]["y_hit"] and got[5, 3]["window_weeks"] == [6, 7, 8]


def test_byes_extend_the_window(world):
    got = labels(world, 2025, [(1, 10, "KC"), (2, 10, "KC"), (1, 11, "PHI"), (2, 11, "PHI"),
                               (5, 4, "DAL"), (5, 1, "SEA")])  # fmt: skip
    # KC is off in week 4: as-of 2's window is 3, 5, 6 (not 3, 4, 5), which reaches the week-6
    # finish; as-of 1's window 2, 3, 5 does not
    assert got[2, 10]["window_weeks"] == [3, 5, 6] and got[2, 10]["y_hit"]
    assert got[1, 10]["window_weeks"] == [2, 3, 5] and got[1, 10]["y_hit"] is False
    # PHI is off in weeks 3 and 5 (two byes)
    assert got[1, 11]["window_weeks"] == [2, 4, 6] and got[1, 11]["y_hit"]
    assert got[2, 11]["window_weeks"] == [4, 6, 7] and got[2, 11]["y_hit"]
    assert got[1, 11]["n_window_played"] == 1  # he has a stat line only in week 6
    # DAL is off in the last week: the window stops at 6, 7
    assert (got[5, 4]["window_weeks"], got[5, 4]["window_games"]) == ([6, 7], 2)
    assert got[5, 4]["is_short_window"] and got[5, 4]["train_eligible"]
    assert got[5, 1]["window_weeks"] == [6, 7, 8] and not got[5, 1]["is_short_window"]


def test_season_end_shortens_the_window(world):
    entries = [(6, 1, "SEA"), (7, 1, "SEA"), (8, 1, "SEA"), (6, 4, "DAL"), (7, 4, "DAL")]
    got = labels(world, 2025, entries)
    assert (got[6, 1]["window_weeks"], got[6, 1]["window_games"]) == ([7, 8], 2)
    assert got[6, 1]["is_short_window"] and got[6, 1]["train_eligible"]
    last_minus_1 = got[7, 1]
    assert (last_minus_1["window_weeks"], last_minus_1["window_games"]) == ([8], 1)
    assert last_minus_1["is_short_window"] and not last_minus_1["train_eligible"]
    assert (8, 1) not in got  # no rows for the last regular-season week
    # a hit in a one-game window is a real label, just not one to train on
    assert got[6, 4]["window_weeks"] == [7] and got[6, 4]["y_hit"]
    assert not got[6, 4]["train_eligible"]
    # DAL has no game after week 7: 0 games, final, no hit (alone, too)
    alone = labels(world, 2025, [(7, 4, "DAL")])[7, 4]
    none = got[7, 4]
    assert alone == none
    assert (none["window_weeks"], none["window_games"], none["label_status"]) == ([], 0, "final")
    assert (none["y_hit"], none["n_window_played"], none["best_rank"]) == (False, 0, None)
    assert lb.last_reg_weeks(world, [2025, 2026]) == {2025: 8, 2026: 7}


def test_played_weeks_snaps_and_inactive(world):
    got = labels(world, 2025, [(1, 20, "SEA"), (2, 20, "SEA")])
    te = got[1, 20]
    # week 2 inactive (a special-teams-only snap row does not count), week 3 blocking only
    # (snaps, no stat line: played, 0 points, no rank), week 4 rank 2; the week-5 finish is
    # outside the window: an inactive week uses a slot and does not extend it
    assert (te["window_weeks"], te["window_ranks"]) == ([2, 3, 4], [None, None, 2])
    assert te["window_points"] == [None, 0.0, 3.0]
    assert (te["n_window_played"], te["y_hit"]) == (2, False)
    assert (got[2, 20]["n_window_played"], got[2, 20]["y_hit"]) == (3, True)


def test_sustained_needs_two_finishes(world):
    got = labels(world, 2025, [(1, 7, "LAC"), (2, 7, "LAC")])
    assert got[1, 7]["window_ranks"] == [2, 1, 3]
    assert (got[1, 7]["y_hit"], got[1, 7]["y_sustained"]) == (True, True)
    assert got[2, 7]["window_ranks"] == [1, 3, 4]
    assert (got[2, 7]["y_hit"], got[2, 7]["y_sustained"]) == (True, False)


def test_traded_players(world):
    got = labels(world, 2025, [(3, 30, "DAL"), (3, 31, "KC")])
    # traded DAL -> KC after week 4: his KC games in the DAL window (4, 5, 6) count
    t = got[3, 30]
    assert (t["window_weeks"], t["window_ranks"]) == ([4, 5, 6], [5, 1, 1])
    assert t["y_sustained"]
    # traded KC -> DAL after week 3: the window follows KC (off in week 4), so his week-4
    # finish for DAL is not in it
    u = got[3, 31]
    assert (u["window_weeks"], u["window_ranks"]) == ([5, 6, 7], [4, 4, 3])
    assert (u["n_window_played"], u["y_hit"]) == (3, False)


def test_converted_player_is_ranked_at_his_roster_position(world):
    got = labels(world, 2025, [(3, 40, "SEA")])
    # 9 points in week 4 would be WR rank 1 (or TE rank 1, today's position); he is the 3rd RB
    assert got[3, 40]["window_ranks"][0] == 3 and got[3, 40]["y_hit"] is False


def test_split_week_game_belongs_to_week_n(world):
    got = labels(world, 2025, [(1, 12, "SF"), (2, 12, "SF")])
    # week 2's Wednesday game (40 points) counts for as-of 1 ...
    assert got[1, 12]["window_weeks"] == [2, 3, 4] and got[1, 12]["y_hit"]
    # ... and not for as-of 2, although its rows became public after week 2's as-of
    assert got[2, 12]["window_weeks"] == [3, 4, 5] and got[2, 12]["y_hit"] is False
    row = finish(world)[2, 12]
    assert row["available_at"] > weekly_as_of(world, 2025, 2).replace(tzinfo=None)


def test_pending_until_every_window_week_is_complete(world):
    got = labels(world, 2026, [(1, 100, "KC"), (2, 100, "KC"), (3, 100, "KC"),
                               (2, 102, "DAL")])  # fmt: skip
    # week 2's DAL at SEA has a score but no stat lines: every rank of week 2 could change
    p = got[1, 100]
    assert (p["label_status"], p["window_weeks"], p["window_games"]) == (
        "pending", [2, 3, 4], 3,
    )  # fmt: skip
    nulls = ("y_hit", "y_sustained", "n_window_played", "n_starter_finishes", "best_rank",
             "window_ranks")  # fmt: skip
    assert all(p[c] is None for c in nulls)
    assert p["train_eligible"] and not p["is_short_window"]
    # weeks 3-5 are complete (KC's QB is the 4th of 4 every week)
    assert got[2, 100]["label_status"] == "final" and got[2, 100]["window_ranks"] == [4, 4, 4]
    assert got[2, 100]["y_hit"] is False
    assert got[2, 102]["label_status"] == "final" and got[2, 102]["window_ranks"] == [2, 2, 2]
    # week 6: KC's own game is final, DAL at SEA is not played yet: still pending
    assert got[3, 100]["label_status"] == "pending" and got[3, 100]["y_hit"] is None
    st = {r["week"]: r for r in lb.week_status(world, [2026]).iter_rows(named=True)}
    assert [(w, st[w]["n_games"], st[w]["n_final"]) for w in (2, 6, 7)] == [
        (2, 2, 1), (6, 2, 1), (7, 2, 0),
    ]  # fmt: skip


# --------------------------------------------------------------------------------------
# Point in time: only outcomes strictly after the as-of
# --------------------------------------------------------------------------------------


def _copy(world: Path, tmp_path: Path) -> Path:
    dst = tmp_path / "copy.duckdb"
    shutil.copy(world, dst)
    return dst


def _leaky_outcomes_after(df, as_of, **kw):
    """A deliberate mutation: lets rows public at or before the as-of in."""
    return df.filter(pl.col("available_at") >= asof_mod.to_utc(as_of))


def test_only_rows_strictly_after_the_as_of_count(world, tmp_path, monkeypatch):
    """A window-week stat line stamped exactly at the as-of was public then: it is a feature
    row, never a label row. One microsecond later it counts."""
    db = _copy(world, tmp_path)
    as_of = weekly_as_of(db, 2025, 1)

    def stamp(ts: datetime) -> None:
        con = duckdb.connect(str(db))
        con.execute(
            "UPDATE fact_player_week SET available_at = ? WHERE player_id = ? AND week = 2",
            [ts.astimezone(UTC).replace(tzinfo=None), gid(7)],
        )
        con.close()

    stamp(as_of)
    entry = [(1, 7, "LAC")]
    at = labels(db, 2025, entry)[1, 7]
    # the week-2 finish is gone: 2 played weeks, 1 finish, not sustained
    assert (at["window_ranks"], at["n_window_played"]) == ([None, 1, 3], 2)
    assert (at["y_hit"], at["y_sustained"]) == (True, False)
    # the mutation that lets rows at the as-of in changes the label: this test catches it
    with monkeypatch.context() as m:
        m.setattr(lb, "outcomes_after", _leaky_outcomes_after)
        assert labels(db, 2025, entry)[1, 7]["y_sustained"] is True
    stamp(as_of + timedelta(microseconds=1))
    assert labels(db, 2025, entry)[1, 7]["y_sustained"] is True


TEAM_OF = ((1, "SEA"), (7, "LAC"), (12, "SF"), (20, "SEA"), (40, "SEA"), (10, "KC"),
           (11, "PHI"), (4, "DAL"), (2, "LAC"), (3, "MIA"))  # fmt: skip
ENTRIES_2025 = [(w, n, team) for w in range(1, 8) for n, team in TEAM_OF]


def _pruned(world: Path, tmp_path: Path, week: int) -> Path:
    """A copy of the world without any event row public at or before 2025 week N's as-of."""
    db = _copy(world, tmp_path)
    cut = weekly_as_of(db, 2025, week).replace(tzinfo=None)
    con = duckdb.connect(str(db))
    for t in ("fact_player_week", "fact_snaps", "fact_game", "fact_roster_week"):
        con.execute(f"DELETE FROM {t} WHERE available_at <= ?", [cut])
    con.execute("CHECKPOINT")
    con.close()
    return db


@pytest.mark.parametrize("week", [1, 2, 3, 5])
def test_labels_ignore_everything_at_or_before_the_as_of(world, tmp_path, week):
    """Delete every event row public at or before the as-of: the labels do not change (they
    are built from what happened after it). Week 2 is the split week."""
    entries = [e for e in ENTRIES_2025 if e[0] == week]
    before = lb.label_rows(world, pool_rows(world, 2025, entries), rules=RULES)
    db = _pruned(world, tmp_path, week)
    after = lb.label_rows(db, pool_rows(db, 2025, entries), rules=RULES)
    assert before.height == len(entries)
    assert after.equals(before)


def _windows_from_week_n(keys, team_weeks, n_games):
    """A deliberate mutation: the window starts at week N (``>=``) instead of N+1."""
    return (
        keys.join(team_weeks.rename({"week": "w"}), on=["season", "team"], how="inner")
        .filter(pl.col("w") >= pl.col("week"))
        .sort("season", "week", "team", "w")
        .group_by("season", "week", "team", maintain_order=True)
        .head(n_games)
    )


def test_a_window_that_counts_week_n_is_caught(world, tmp_path, monkeypatch):
    """A window that starts at week N fails three tests above: the boundary test (the window
    weeks), the split-week test (week 2's Wednesday game leaks into as-of 2's label; the time
    filter alone cannot stop it, the game was public only after that as-of) and the deletion
    test. Week N's other rows stay out even then: they were public at the as-of."""
    entries = [e for e in ENTRIES_2025 if e[0] == 1]
    db = _pruned(world, tmp_path, 1)
    with monkeypatch.context() as m:
        m.setattr(lb, "_windows", _windows_from_week_n)
        a = labels(world, 2025, [(1, 1, "SEA")])[1, 1]
        assert a["window_weeks"] == [1, 2, 3] and a["y_hit"] is False
        assert labels(world, 2025, [(2, 12, "SF")])[2, 12]["y_hit"] is True
        full = lb.label_rows(world, pool_rows(world, 2025, entries), rules=RULES)
        pruned = lb.label_rows(db, pool_rows(db, 2025, entries), rules=RULES)
        assert not pruned.equals(full)


# --------------------------------------------------------------------------------------
# Config, validation, empty input
# --------------------------------------------------------------------------------------


def test_thresholds_derive_from_league_config(world):
    lg = league()
    default = lb.LabelRules.from_config(lg)
    assert dict(default.starter_thresholds) == lg.starter_rank_threshold
    assert default.flex_rank == lg.flex_worthy_rank
    assert (default.window_games, default.min_train_games) == (3, 2)
    shape = {"QB": 1, "RB": 2, "WR": 1, "TE": 1}
    stricter = League(**{**lg.model_dump(), "starter_rank_threshold": shape,
                         "flex_worthy_rank": 1})  # fmt: skip
    rules = lb.LabelRules.from_config(stricter)
    entry = [(1, 7, "LAC")]
    assert labels(world, 2025, entry)[1, 7]["y_sustained"] is True  # WR ranks 2, 1, 3
    tight = labels(world, 2025, entry, rules=rules)[1, 7]  # WR top 1: only the rank-1 week
    assert (tight["n_starter_finishes"], tight["y_sustained"], tight["n_flex_finishes"]) == (
        1, False, 1,
    )  # fmt: skip
    with pytest.raises(ValueError, match="lack positions"):
        lb.LabelRules(starter_thresholds={"QB": 12}, flex_rank=36)
    with pytest.raises(ValueError, match="window_games"):
        lb.LabelRules(starter_thresholds=shape, flex_rank=3, min_train_games=4)


def test_label_rows_validation_and_empty_input(world):
    rows = pool_rows(world, 2025, [(1, 1, "SEA")])
    with pytest.raises(KeyError, match="team"):
        lb.label_rows(world, rows.drop("team"), rules=RULES)
    with pytest.raises(ValueError, match="NULL as_of"):
        lb.label_rows(world, rows.with_columns(pl.lit(None).cast(rows.schema["as_of"])
                                               .alias("as_of")), rules=RULES)  # fmt: skip
    with pytest.raises(ValueError, match="already have"):
        lb.label_rows(world, rows.with_columns(pl.lit(1).alias("y_hit")), rules=RULES)
    empty = lb.label_rows(world, rows.head(0), rules=RULES)
    assert empty.height == 0 and empty.columns == [*rows.columns, *lb.LABEL_COLUMNS]
    last_only = lb.label_rows(world, pool_rows(world, 2025, [(8, 1, "SEA")]), rules=RULES)
    assert last_only.height == 0 and last_only.columns == empty.columns
    assert last_only.schema == empty.schema
    with pytest.raises(FileNotFoundError):
        lb.weekly_finishes(world.parent / "nope.duckdb", [2025])


def test_labels_history_uses_the_c1_pool(world):
    hist = lb.labels_history(world, [2025, 2026], rules=RULES)
    pool = pool_history(world, [2025, 2026])
    # every pool row of weeks 1 to last - 1, in the same order, plus the label columns
    kept = pool.filter(pl.col("week") < pl.col("season").replace_strict({2025: 8, 2026: 7}))
    assert hist.columns == [*pool.columns, *lb.LABEL_COLUMNS]
    assert hist.select(pool.columns).equals(kept)
    assert hist.height > 0 and set(hist.get_column("label_status")) <= set(lb.LABEL_STATUSES)
    # the 2026 as-ofs whose games are in: weeks 1-5 (week 6 has an unplayed game)
    assert sorted(set(hist.filter(pl.col("season") == 2026).get_column("week"))) == [1, 2, 3, 4, 5]


# --------------------------------------------------------------------------------------
# Report and CLI
# --------------------------------------------------------------------------------------


def test_report_is_deterministic_and_complete(world):
    kw = {"rules": RULES, "examples": ((2025, 2), (2026, 1)), "last_eval_season": 2025}
    one = build_label_report(world, [2025, 2026], **kw)
    two = build_label_report(world, [2025, 2026], **kw)
    assert one.markdown == two.markdown and one.csv_rows == two.csv_rows
    md = one.markdown
    for heading in ("## As-ofs and rows per season", "## Base rates in the pool",
                    "## Base rates outside the pool", "## FLEX-worthy finishes",
                    "## Window length and pending labels", "## Weekly finishes",
                    "## Example hits", "Pending as-ofs"):  # fmt: skip
        assert heading in md, heading
    assert "| 2025 | 8 | 7 | - |" in md and "| 2026 | 7 | 5 | - |" in md
    assert list(one.csv_rows[0]) == list(CSV_COLUMNS)
    assert "QB top 1, RB top 2, WR top 2, TE top 1" in md


def test_cli_labels_and_report(world, tmp_path):
    runner = CliRunner()
    base = ["radar", "labels", "2025", "2", "--db", str(world)]
    # the shipped thresholds (WR 36 for the pool): only the player whose game was moved past
    # the as-of has no game yet, so he is the pool
    res = runner.invoke(app, base)
    assert res.exit_code == 0, res.output
    assert "2025 week 2, as-of 2025-09-16 14:00 UTC: 1 pool players" in res.output
    assert "Wr Split" in res.output and "3,4,5" in res.output
    res = runner.invoke(app, [*base, "--all", "--limit", "0"])
    assert res.exit_code == 0 and "Rb Traded" in res.output and "Wr Boundary" in res.output
    res = runner.invoke(app, [*base, "--all", "--hits-only", "--pos", "rb"])
    assert res.exit_code == 0 and "Rb Filler Sea" in res.output
    assert "Wr Boundary" not in res.output
    res = runner.invoke(app, ["radar", "labels", "2025", "8", "--db", str(world)])
    assert res.exit_code == 0 and "last regular-season week" in res.output
    res = runner.invoke(app, ["radar", "labels", "2025", "30", "--db", str(world)])
    assert res.exit_code == 1 and "cannot label" in res.output
    res = runner.invoke(app, ["radar", "labels", "2025", "2", "--db", str(tmp_path / "x.db")])
    assert res.exit_code == 1 and "warehouse not found" in res.output
    out = tmp_path / "rep" / "labels.md"
    args = ["radar", "labels-report", "--db", str(world), "--out", str(out), "--start", "2025",
            "--end", "2026"]  # fmt: skip
    res = runner.invoke(app, args)
    assert res.exit_code == 0, res.output
    first = out.read_text(), out.with_suffix(".csv").read_text()
    assert first[1].splitlines()[0] == ",".join(CSV_COLUMNS)
    assert runner.invoke(app, args).exit_code == 0
    assert (out.read_text(), out.with_suffix(".csv").read_text()) == first
    bad = runner.invoke(app, ["radar", "labels-report", "--db", str(world), "--out", str(out),
                              "--start", "2026", "--end", "2025"])  # fmt: skip
    assert bad.exit_code == 2


# --------------------------------------------------------------------------------------
# Real data (opt-in: `uv run pytest -m realdata`; builds into a temp file, never downloads)
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_labels(real_full_db):
    path, _ = real_full_db
    lab = lb.labels_history(path, list(range(2013, 2027)))
    return path, lab


@pytest.mark.realdata
def test_real_base_rates(real_labels):
    path, lab = real_labels
    usable = lab.filter(
        (pl.col("label_status") == "final") & pl.col("train_eligible")
        & pl.col("season").is_between(2014, 2025)
    )  # fmt: skip
    rates = (
        usable.group_by("in_pool", "position")
        .agg(pl.len().alias("n"), pl.col("y_hit").mean().alias("hit"),
             pl.col("y_sustained").mean().alias("sustained"))
        .sort("in_pool", "position")
    )  # fmt: skip
    print(rates)  # the full tables are in reports/waiver_radar/labels.md
    r = {(x["in_pool"], x["position"]): x for x in rates.iter_rows(named=True)}
    for pos in ("QB", "RB", "WR", "TE"):
        pool, out = r[True, pos], r[False, pos]
        assert pool["n"] > 5000 and out["n"] > 2000
        assert 0.03 < pool["hit"] < 0.25, pool  # a waiver hit is rare ...
        assert out["hit"] > 3 * pool["hit"], (pool, out)  # ... far rarer than for starters
        assert pool["sustained"] < pool["hit"]
    # every label is final before the current season, and no window is empty
    assert lab.filter((pl.col("season") < 2026) & (pl.col("label_status") != "final")).height == 0
    assert lab.get_column("window_games").min() >= 1
    last = lb.last_reg_weeks(path, list(range(2013, 2027)))
    assert all(r["week"] < last[r["season"]] for r in lab.select("season", "week").unique()
               .iter_rows(named=True))  # fmt: skip
    # the second-to-last as-of has one game left, the one before it two
    lm1 = lab.filter(pl.col("week") == pl.col("season").replace_strict(last) - 1)
    assert set(lm1.get_column("window_games")) == {1}


@pytest.mark.realdata
def test_real_hits_have_a_real_starter_finish(real_labels):
    """Recompute every hit's starter finish from the warehouse with plain SQL: a stat line in
    a window week, public after the as-of, ranked within the week's roster position."""
    path, lab = real_labels
    thr = league().starter_rank_threshold
    hits = (
        lab.filter(pl.col("y_hit").fill_null(False) & (pl.col("season") >= 2013))
        .with_row_index("hit_id")
        .select("hit_id", "season", "week", "as_of", "gsis_id", "window_weeks")
        .explode("window_weeks", empty_as_null=False)
        .rename({"window_weeks": "w"})
        .with_columns(pl.col("as_of").dt.replace_time_zone(None))
    )
    from twm.scoring import score_sql

    con = duckdb.connect(str(path), read_only=True)
    con.execute("SET TimeZone='UTC'")
    con.register("hits", hits)
    whens = " ".join(f"WHEN '{p}' THEN {n}" for p, n in thr.items())
    n_ok = con.execute(f"""
        WITH pts AS (
            SELECT p.season, p.week, p.player_id, p.available_at, r.position,
                   round({score_sql(table_alias="p")}, 6) AS fp
            FROM fact_player_week p JOIN fact_roster_week r
              ON r.season = p.season AND r.week = p.week AND r.gsis_id = p.player_id
            WHERE p.season_type = 'REG' AND p.season >= 2013
              AND r.position IN ('QB', 'RB', 'WR', 'TE')
        ),
        ranked AS (SELECT *, rank() OVER (PARTITION BY season, week, position ORDER BY fp DESC)
                   AS rk FROM pts)
        SELECT count(DISTINCT h.hit_id) FROM hits h JOIN ranked k
          ON k.season = h.season AND k.week = h.w AND k.player_id = h.gsis_id
        WHERE k.available_at > h.as_of AND k.week > h.week
          AND k.rk <= CASE k.position {whens} END""").fetchone()[0]
    con.close()
    n_hits = hits.get_column("hit_id").n_unique()
    assert n_hits > 10000 and n_ok == n_hits


@pytest.mark.realdata
def test_real_current_season_is_pending_until_complete(real_labels):
    path, lab = real_labels
    status = lb.week_status(path, [2026])
    final = set(status.filter(pl.col("is_week_final")).get_column("week"))
    cur = lab.filter(pl.col("season") == 2026)
    if cur.height == 0:
        pytest.skip("no 2026 as-of in the cache yet")
    for r in (
        cur.select("week", "window_weeks", "label_status", "y_hit")
        .unique(subset=["week", "window_weeks"])
        .iter_rows(named=True)
    ):
        complete = set(r["window_weeks"]) <= final
        assert r["label_status"] == ("final" if complete else "pending"), r
        assert (r["y_hit"] is None) == (not complete)


@pytest.mark.realdata
@pytest.mark.parametrize(("season", "week"), [(2014, 2), (2020, 12), (2023, 6)])
def test_real_labels_ignore_the_past(real_labels, season, week):
    """Turn every stat line public at or before the as-of, and every stat line of weeks up to
    N, into a rank-1 starter finish: the labels do not move (2020 week 12 is a split week: its
    Wednesday game became public after the as-of, and only the week number keeps it out)."""
    path, lab = real_labels
    pool = lab.filter((pl.col("season") == season) & (pl.col("week") == week)).drop(
        lb.LABEL_COLUMNS
    )
    fin = lb.weekly_finishes(path, [season])
    as_of = weekly_as_of(path, season, week).replace(tzinfo=None)
    good = lb.label_rows(path, pool, finishes=fin)
    for past in (pl.col("available_at") <= as_of, pl.col("week") <= week):
        fake = fin.with_columns(
            pl.when(past).then(pl.lit(1, dtype=pl.Int32)).otherwise("pos_rank").alias("pos_rank"),
            pl.when(past).then(pl.lit(True)).otherwise("is_starter_finish")
            .alias("is_starter_finish"),
            pl.when(past).then(pl.lit(99.0)).otherwise("fantasy_points").alias("fantasy_points"),
        )  # fmt: skip
        assert fin.filter(past).height > 1000
        assert lb.label_rows(path, pool, finishes=fake).equals(good)
    assert good.height > 300 and int(good.get_column("y_hit").sum()) > 20
    if (season, week) == (2020, 12):  # the moved game is week 12 and public after the as-of
        late = fin.filter((pl.col("week") == week) & (pl.col("available_at") > as_of))
        assert late.height > 20
