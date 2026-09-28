"""Waiver Radar features (C3): every feature family on a hand-computable synthetic world, the
teammate rules one by one, depth charts (daily and weekly), the next opponents, xFP, the
point-in-time harness (and deliberately leaky variants), batch == reference, the dataset, the
report and the CLI.

The tests share one synthetic warehouse (``world``); every id and name is made up. Fantasy
points are receptions only (1 point each in full PPR), so the numbers stay easy to follow.

- **2025** (game-day rosters, daily depth charts), 6 regular-season weeks, 6 teams:
  W1 KC@PHI, DAL@SEA (MIA, BUF off); W2 PHI@DAL, SEA@KC, MIA@BUF; W3 DAL@KC, BUF@MIA (PHI, SEA
  off); W4 KC@PHI, SEA@DAL (moved to Wednesday, after the week-4 as-of: a split week), MIA@BUF;
  W5 PHI@SEA, BUF@MIA (KC, DAL off); W6 KC@DAL, SEA@PHI, MIA@BUF.
  Kansas City at the week-3 as-of (after W3): its star receiver (2) missed W3 after 90% snaps
  in W1-W2 (missed_last_game); a receiver (5) is Out on the W3 injury report (injury_report); a
  running back (6) was released after W2 (left_team); a tight end (8) is on injured reserve
  on the W3 roster (roster_status); another receiver (3) missed W3 but averaged only 40% (not
  out). Receiver 10 was traded from DAL to KC after W2.
- **2024** (weekly "legacy" depth charts): KC and PHI, three weeks, for the legacy chart path.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pytest
from polars.testing import assert_frame_equal
from typer.testing import CliRunner

from tests.conftest import (
    DAILY_DC_DTYPES,
    LEGACY_DC_DTYPES,
    PBP_DTYPES,
    PLAYER_STATS_DTYPES,
    PLAYERS_DTYPES,
    ROSTERS_WEEKLY_DTYPES,
    TEAM_STATS_DTYPES,
    RawCache,
    frame,
    game,
)
from twm import ids
from twm import registry as rg
from twm.asof import AsOfView, weekly_as_of
from twm.backtest.leakage import LeakageError, assert_future_invariant
from twm.cli import app
from twm.modules.waiver_radar import features as ft
from twm.modules.waiver_radar.dataset import build_dataset, dataset_columns
from twm.modules.waiver_radar.feature_report import (
    CSV_COLUMNS,
    build_feature_report,
    xfp_check,
    xfp_rounding_bound,
)
from twm.modules.waiver_radar.pool import PoolRules, candidate_pool, pool_history
from twm.scoring import ScoringRules, full_ppr, nflverse_ppr, xfp, xfp_sql
from twm.sources import nflverse as nv
from twm.warehouse import available as av
from twm.warehouse import build as wb
from twm.warehouse import schema as sc

POOL_RULES = PoolRules(cutoffs={"QB": 1, "RB": 1, "WR": 1, "TE": 1})
RULES = ft.FeatureRules()  # the defaults: window 3, 50% snap rule, Out/Doubtful


def A(x: float) -> object:  # noqa: N802
    """Features are rounded to 6 decimals: compare with that tolerance."""
    return pytest.approx(x, abs=2e-6)


def gid(n: int) -> str:
    return f"00-0077{n:03d}"


def pfr(n: int) -> str:
    return f"Feat{n:03d}"


# --------------------------------------------------------------------------------------
# The synthetic world
# --------------------------------------------------------------------------------------

STATS_DTYPES = {
    **PLAYER_STATS_DTYPES, "targets": pl.Int32(), "target_share": pl.Float64(),
    "air_yards_share": pl.Float64(), "wopr": pl.Float64(), "carries": pl.Int32(),
}  # fmt: skip
TEAM_DTYPES = {**TEAM_STATS_DTYPES, "carries": pl.Int32()}
PLAY_DTYPES = {**PBP_DTYPES, "play_type": pl.String(), "qb_dropback": pl.Float64(),
               "rush": pl.Float64(), "two_point_attempt": pl.Float64(),
               "receiver_player_id": pl.String(), "rusher_player_id": pl.String(),
               "yardline_100": pl.Float64()}  # fmt: skip
ROSTER_DTYPES = {**ROSTERS_WEEKLY_DTYPES, "years_exp": pl.Int32()}

SCHED_2025 = {
    1: [("KC", "PHI"), ("DAL", "SEA")],
    2: [("PHI", "DAL"), ("SEA", "KC"), ("MIA", "BUF")],
    3: [("DAL", "KC"), ("BUF", "MIA")],
    4: [("KC", "PHI"), ("SEA", "DAL"), ("MIA", "BUF")],
    5: [("PHI", "SEA"), ("BUF", "MIA")],
    6: [("KC", "DAL"), ("SEA", "PHI"), ("MIA", "BUF")],
}
SPLIT = (4, "SEA", "DAL")  # played on the Wednesday after week 4's Tuesday as-of

PLAYERS = {
    1: ("Kc Qb", "QB"), 2: ("Kc Star Wr", "WR"), 3: ("Kc Wr Two", "WR"),
    4: ("Kc Wr Focus", "WR"), 5: ("Kc Wr Out", "WR"), 6: ("Kc Rb Released", "RB"),
    7: ("Kc Rb Focus", "RB"), 8: ("Kc Te Ir", "TE"), 9: ("Kc Te Focus", "TE"),
    10: ("Traded Wr", "WR"), 11: ("Kc Wr Deep", "WR"),
    20: ("Phi Qb", "QB"), 21: ("Phi Wr", "WR"), 30: ("Dal Qb", "QB"), 31: ("Dal Wr", "WR"),
    40: ("Sea Qb", "QB"), 41: ("Sea Wr", "WR"), 50: ("Mia Wr", "WR"), 60: ("Buf Wr", "WR"),
}  # fmt: skip
TEAM_OF = {20: "PHI", 21: "PHI", 30: "DAL", 31: "DAL", 40: "SEA", 41: "SEA", 50: "MIA",
           60: "BUF"}  # fmt: skip
BIRTH = {4: "2002-09-23", 7: "1998-01-15"}
DRAFT = {4: (2025, 3), 2: (2020, 1)}  # player -> (draft_year, draft_round); 7 is undrafted
ENTRY = {4: 2025, 7: 2023}


def S(tgt=0, share=0.0, rec=0, car=0, ay=None, wo=None) -> dict[str, Any]:  # noqa: N802
    return {"targets": tgt, "target_share": share, "receptions": rec, "carries": car,
            "air_yards_share": ay, "wopr": wo}  # fmt: skip


# player -> {week: (team, roster status, snap share or None = no snap row, stats or None)}
LINES: dict[int, dict[int, tuple[str, str, float | None, dict | None]]] = {
    1: {w: ("KC", "ACT", 1.0, S(car=2)) for w in (1, 2, 3, 4, 6)},
    # inactive in W3, on injured reserve from the W4 roster (public only at W4's as-of)
    2: {1: ("KC", "ACT", 0.9, S(6, 0.30, 4)), 2: ("KC", "ACT", 0.9, S(5, 0.25, 3)),
        3: ("KC", "INA", None, None), 4: ("KC", "RES", None, None)},
    3: {1: ("KC", "ACT", 0.4, S(2, 0.10, 1)), 2: ("KC", "ACT", 0.4, S(2, 0.10, 1)),
        3: ("KC", "INA", None, None), 4: ("KC", "ACT", 0.5, S(3, 0.15, 2))},
    4: {1: ("KC", "ACT", 0.3, S(1, 0.05, 1, ay=0.04, wo=0.10)),
        2: ("KC", "ACT", 0.3, S(1, 0.05, 1, ay=0.06, wo=0.12)),
        3: ("KC", "ACT", 0.85, S(6, 0.30, 5, ay=0.35, wo=0.70)),
        4: ("KC", "ACT", 0.8, S(5, 0.25, 4, ay=0.30, wo=0.60))},
    5: {1: ("KC", "ACT", 0.45, S(2, 0.10, 2)), 2: ("KC", "ACT", 0.45, S(2, 0.10, 1)),
        3: ("KC", "INA", None, None), 4: ("KC", "ACT", 0.5, S(2, 0.10, 1))},
    6: {1: ("KC", "ACT", 0.45, S(1, 0.05, 1, car=8)),
        2: ("KC", "ACT", 0.45, S(1, 0.05, 1, car=10))},
    7: {1: ("KC", "ACT", 0.3, S(car=4)), 2: ("KC", "ACT", 0.3, S(car=5)),
        3: ("KC", "ACT", 0.7, S(2, 0.10, 1, car=12)),
        4: ("KC", "ACT", 0.75, S(1, 0.05, 1, car=14))},
    8: {1: ("KC", "ACT", 0.4, S(2, 0.10, 1)), 2: ("KC", "ACT", 0.4, S(3, 0.15, 2)),
        3: ("KC", "RES", None, None), 4: ("KC", "RES", None, None)},
    9: {1: ("KC", "ACT", 0.6, S(2, 0.10, 1)), 2: ("KC", "ACT", 0.6, S(2, 0.10, 1)),
        3: ("KC", "ACT", 0.9, S(3, 0.15, 2)), 4: ("KC", "ACT", 0.9, S(2, 0.10, 2))},
    10: {1: ("DAL", "ACT", 0.6, S(2, 0.10, 1)), 2: ("DAL", "ACT", 0.6, S(2, 0.10, 1)),
         3: ("KC", "ACT", 0.5, S(3, 0.15, 2)), 4: ("KC", "ACT", 0.5, S(2, 0.10, 1))},
    11: {w: ("KC", "DEV", None, None) for w in (1, 2, 3, 4)},
}  # fmt: skip
WEEK1_WR_RECEPTIONS = {21: 6, 31: 4, 41: 2}  # later weeks: 3 each (MIA, BUF: 3 every game)

# KC's plays per game: (pass plays, runs, garbage-time passes); every other team 15, 10, 0
KC_PLAYS = {1: (20, 10, 2), 2: (20, 10, 2), 3: (30, 10, 0), 4: (25, 10, 0), 6: (20, 10, 0)}
OTHER_PLAYS = (15, 10, 0)

DAY1 = date(2025, 9, 7)


def games_2025() -> list[dict]:
    out = []
    for w, pairs in SCHED_2025.items():
        day = DAY1 + timedelta(days=7 * (w - 1))
        for away, home in pairs:
            gday, gtime = day.isoformat(), "13:00"
            if (w, away, home) == SPLIT:
                gday, gtime = (day + timedelta(days=3)).isoformat(), "19:00"
            out.append(game(2025, w, gday, gtime, away, home, result=3))
    return out


def _game_of(games: list[dict], week: int, team: str) -> dict | None:
    teams = ("home_team", "away_team")
    return next((g for g in games if g["week"] == week and team in (g[t] for t in teams)), None)


def _opp(g: dict, team: str) -> str:
    return g["away_team"] if team == g["home_team"] else g["home_team"]


def all_lines(games: list[dict]) -> dict[int, dict[int, tuple]]:
    """LINES plus the QB and WR of every other team in every game it plays."""
    lines = {n: dict(v) for n, v in LINES.items()}
    for n, team in TEAM_OF.items():
        lines[n] = {}
        for w in SCHED_2025:
            if _game_of(games, w, team) is None:
                continue
            if PLAYERS[n][1] == "QB":
                lines[n][w] = (team, "ACT", 1.0, S())
            else:
                rec = WEEK1_WR_RECEPTIONS.get(n, 3) if w == 1 else 3
                lines[n][w] = (team, "ACT", 0.8, S(rec + 1, 0.2, rec))
    # MIA and BUF are off in week 1 but list a week-1 roster (so they are in the universe at
    # the week-1 as-of with no game yet)
    lines[50][1] = ("MIA", "ACT", None, None)
    lines[60][1] = ("BUF", "ACT", None, None)
    return lines


def world_rows(games: list[dict]) -> dict[str, list[dict]]:
    stats, snaps, rosters, opps = [], [], [], []
    for n, weeks in all_lines(games).items():
        name, pos = PLAYERS[n]
        for w, (team, status, snap, st) in weeks.items():
            rosters.append({"season": 2025, "week": w, "game_type": "REG", "team": team,
                            "position": pos, "full_name": name, "gsis_id": gid(n),
                            "pfr_id": pfr(n), "status": status, "entry_year": ENTRY.get(n, 2020),
                            "years_exp": 2025 - ENTRY.get(n, 2020)})  # fmt: skip
            g = _game_of(games, w, team)
            if g is None:
                continue
            if snap is not None:
                snaps.append({"game_id": g["game_id"], "season": 2025, "game_type": "REG",
                              "week": w, "player": name, "pfr_player_id": pfr(n),
                              "position": pos, "team": team, "opponent": _opp(g, team),
                              "offense_snaps": round(60 * snap), "offense_pct": snap})  # fmt: skip
            if st is not None:
                stats.append({"player_id": gid(n), "player_name": name, "position": pos,
                              "season": 2025, "week": w, "season_type": "REG",
                              "game_id": g["game_id"], "team": team,
                              "opponent_team": _opp(g, team),
                              "fantasy_points_ppr": float(st["receptions"]), **st})  # fmt: skip
    # expected stats of the focus receiver (4): xFP = receptions_exp + 0.1 x yards_exp
    for w, (rec_exp, yds_exp) in {1: (0.5, 5.0), 2: (0.6, 6.0), 3: (4.0, 40.0)}.items():
        g = _game_of(games, w, "KC")
        opps.append({"season": "2025", "posteam": "KC", "week": float(w), "game_id": g["game_id"],
                     "player_id": gid(4), "full_name": PLAYERS[4][0], "position": "WR",
                     "receptions_exp": rec_exp, "rec_yards_gained_exp": yds_exp})  # fmt: skip
    return {"stats": stats, "snaps": snaps, "rosters": rosters, "opportunity": opps}


def plays_2025(games: list[dict]) -> list[dict]:
    """Generic plays per team-game, plus KC's red-zone plays in week 3."""
    out = []
    for g in games:
        pid = 0
        for team in (g["home_team"], g["away_team"]):
            p, r, garbage = KC_PLAYS[g["week"]] if team == "KC" else OTHER_PLAYS
            base = {"game_id": g["game_id"], "season": 2025, "week": g["week"],
                    "season_type": "REG", "game_date": g["gameday"], "posteam": team,
                    "defteam": _opp(g, team), "home_team": g["home_team"],
                    "away_team": g["away_team"], "qtr": 1.0, "down": 1.0,
                    "half_seconds_remaining": 1500.0, "score_differential": 0.0,
                    "two_point_attempt": 0.0, "yardline_100": 50.0}  # fmt: skip
            kinds = (
                [("pass", 0.2, 0.5)] * p
                + [("run", -0.1, 0.5)] * r
                + [("pass", 1.0, 0.99)] * garbage
            )
            for kind, epa, wp in kinds:
                pid += 1
                out.append({**base, "play_id": float(pid), "play_type": kind, "epa": epa, "wp": wp,
                            "pass": 1.0 if kind == "pass" else 0.0,
                            "qb_dropback": 1.0 if kind == "pass" else 0.0,
                            "rush": 1.0 if kind == "run" else 0.0})  # fmt: skip
            if team == "KC" and g["week"] == 3:
                special = [
                    ("pass", gid(4), None, 15.0, 0.0),  # red-zone target
                    ("pass", gid(4), None, 8.0, 0.0),  # red-zone and goal-line target
                    ("run", None, gid(7), 18.0, 0.0),
                    ("run", None, gid(7), 5.0, 0.0),
                    ("run", None, gid(7), 2.0, 0.0),
                    ("run", None, gid(7), 2.0, 1.0),  # a two-point try: not an opportunity
                    ("pass", gid(9), None, 2.0, 1.0),  # a two-point pass: not a dropback
                    ("qb_kneel", None, gid(1), 5.0, 0.0),  # a kneel: not a carry here
                ]
                for kind, rec, rusher, yl, two in special:
                    pid += 1
                    out.append({**base, "play_id": float(pid), "play_type": kind, "epa": 0.3,
                                "wp": 0.5, "pass": 1.0 if kind == "pass" else 0.0,
                                "qb_dropback": 1.0 if kind == "pass" else 0.0,
                                "rush": 1.0 if kind == "run" else 0.0,
                                "receiver_player_id": rec, "rusher_player_id": rusher,
                                "yardline_100": yl, "two_point_attempt": two})  # fmt: skip
    return out


def injuries_2025() -> list[dict]:
    def row(week, n, status):
        return {"season": 2025, "game_type": "REG", "team": "KC", "week": week,
                "gsis_id": gid(n), "position": PLAYERS[n][1], "full_name": PLAYERS[n][0],
                "report_status": status}  # fmt: skip

    # W2: the tight end (9) Out (only the latest report counts); W3: receiver 5 Out, 3
    # Questionable; W4 (after the week-3 as-of): only 3 Questionable
    return [row(2, 9, "Out"), row(3, 5, "Out"), row(3, 3, "Questionable"),
            row(4, 3, "Questionable")]  # fmt: skip


def depth_2025() -> list[dict]:
    def slot(dt, team, n, pos_abb, pos_slot, rank, grp="3WR 1TE"):
        return {"dt": dt, "team": team, "player_name": PLAYERS[n][0], "espn_id": None,
                "gsis_id": gid(n), "pos_grp": grp, "pos_name": pos_abb, "pos_abb": pos_abb,
                "pos_slot": pos_slot, "pos_rank": rank}  # fmt: skip

    before = [(2, "WR", 1, 1), (4, "WR", 1, 2), (3, "WR", 2, 1), (5, "WR", 2, 2),
              (6, "RB", 1, 1), (7, "RB", 1, 2), (8, "TE", 1, 1), (9, "TE", 1, 2),
              (1, "QB", 1, 1)]  # fmt: skip
    after3 = [(4, "WR", 1, 1), (10, "WR", 1, 2), (3, "WR", 2, 1), (5, "WR", 2, 2),
              (7, "RB", 1, 1), (9, "TE", 1, 1), (1, "QB", 1, 1)]  # fmt: skip
    late = [(10, "WR", 1, 1), (4, "WR", 1, 2), (3, "WR", 2, 1), (5, "WR", 2, 2),
            (7, "RB", 1, 1), (9, "TE", 1, 1), (1, "QB", 1, 1)]  # fmt: skip
    rows = []
    for dt in ("2025-09-02T10:00:00Z", "2025-09-16T10:00:00Z"):
        rows += [slot(dt, "KC", *x) for x in before]
    rows += [slot("2025-09-23T10:00:00Z", "KC", *x) for x in after3]
    rows.append(slot("2025-09-23T10:00:00Z", "KC", 11, "KR", 1, 1, grp="Special Teams"))
    rows += [slot("2025-09-23T15:00:00Z", "KC", *x) for x in late]  # after week 3's as-of
    rows += [slot("2025-09-16T10:00:00Z", "PHI", 21, "WR", 1, 1),
             slot("2025-09-16T10:00:00Z", "PHI", 20, "QB", 1, 1)]  # fmt: skip
    return rows


# ---- 2024: weekly (legacy) depth charts -----------------------------------------------

LEGACY = {101: ("Legacy Wr A", "WR"), 102: ("Legacy Wr B", "WR"), 103: ("Legacy Rb C", "RB"),
          104: ("Legacy Te D", "TE")}  # fmt: skip


def games_2024() -> list[dict]:
    return [
        game(2024, 1, "2024-09-08", "13:00", "KC", "PHI", result=3),
        game(2024, 2, "2024-09-15", "13:00", "PHI", "KC", result=3),
        game(2024, 3, "2024-09-22", "13:00", "KC", "PHI", result=3),
    ]


def legacy_rows(games: list[dict]) -> dict[str, list[dict]]:
    rosters, snaps, stats = [], [], []
    for g in games:
        for n, (name, pos) in LEGACY.items():
            rosters.append({"season": 2024, "week": g["week"], "game_type": "REG", "team": "KC",
                            "position": pos, "full_name": name, "gsis_id": gid(n),
                            "pfr_id": pfr(n), "status": "ACT"})  # fmt: skip
            snaps.append({"game_id": g["game_id"], "season": 2024, "game_type": "REG",
                          "week": g["week"], "player": name, "pfr_player_id": pfr(n),
                          "position": pos, "team": "KC", "opponent": "PHI",
                          "offense_snaps": 30.0, "offense_pct": 0.5})  # fmt: skip
            stats.append({"player_id": gid(n), "player_name": name, "position": pos,
                          "season": 2024, "week": g["week"], "season_type": "REG",
                          "game_id": g["game_id"], "team": "KC", "opponent_team": "PHI",
                          "receptions": 1, "fantasy_points_ppr": 1.0})  # fmt: skip

    def chart(week, n, slot, depth):
        return {"season": 2024, "club_code": "KC", "week": week, "game_type": "REG",
                "depth_team": str(depth), "last_name": "X", "first_name": "Y",
                "formation": "Offense", "gsis_id": gid(n), "position": LEGACY[n][1],
                "depth_position": slot, "full_name": LEGACY[n][0]}  # fmt: skip

    depth = [
        # week 1: A starts at LWR, B at RWR; the junk slot '\n' (seen upstream) is ignored
        chart(1, 101, "LWR", 1), chart(1, 102, "LWR", 2), chart(1, 102, "RWR", 1),
        chart(1, 102, "\n", 1), chart(1, 103, "HB", 1), chart(1, 103, "FB", 2),
        chart(1, 104, "TE/HB", 1),
        # week 2: B starts, A is a backup
        chart(2, 102, "LWR", 1), chart(2, 101, "RWR", 2), chart(2, 103, "HB", 1),
        chart(2, 104, "TE/HB", 1),
        # week 3: not public at the week-2 as-of
        chart(3, 101, "LWR", 1), chart(3, 102, "LWR", 2),
    ]  # fmt: skip
    return {"rosters": rosters, "snaps": snaps, "stats": stats, "depth": depth}


def _players() -> pl.DataFrame:
    rows = []
    for n, (name, pos) in {**PLAYERS, **LEGACY}.items():
        year, rnd = DRAFT.get(n, (None, None))
        rows.append({"gsis_id": gid(n), "display_name": name, "position": pos,
                     "birth_date": BIRTH.get(n, "1995-06-01"), "draft_year": year,
                     "draft_round": rnd, "pfr_id": pfr(n)})  # fmt: skip
    return frame(rows, PLAYERS_DTYPES)


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The synthetic 2024/2025 warehouse, built once for the module (offline)."""
    tmp = tmp_path_factory.mktemp("features_world")
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
        g25 = games_2025()
        rows = world_rows(g25)
        roster = frame(rows["rosters"], ROSTER_DTYPES)
        cache.write_season(2025, g25, player_stats=[], rosters=roster,
                           snaps=rows["snaps"], injuries=injuries_2025(),
                           depth_charts=frame(depth_2025(), DAILY_DC_DTYPES),
                           opportunity=rows["opportunity"])  # fmt: skip
        cache.write("player_stats", 2025, frame(rows["stats"], STATS_DTYPES))
        cache.write("pbp", 2025, frame(plays_2025(g25), PLAY_DTYPES))
        team_rows = [{"season": 2025, "week": g["week"], "team": t, "season_type": "REG",
                      "game_id": g["game_id"], "opponent_team": _opp(g, t), "carries": 20}
                     for g in g25 for t in (g["home_team"], g["away_team"])]  # fmt: skip
        cache.write("team_stats", 2025, frame(team_rows, TEAM_DTYPES))
        g24 = games_2024()
        leg = legacy_rows(g24)
        cache.write_season(2024, g24, player_stats=leg["stats"], snaps=leg["snaps"],
                           rosters=frame(leg["rosters"], ROSTER_DTYPES), injuries=[],
                           depth_charts=frame(leg["depth"], LEGACY_DC_DTYPES))  # fmt: skip
        db = tmp / "features.duckdb"
        wb.build_warehouse([2024, 2025], db_path=db)
    return db


def _pool(view: AsOfView, season: int, week: int) -> pl.DataFrame:
    return candidate_pool(view, season, week, rules=POOL_RULES)


def feats(db: Path, season: int, week: int, rules: ft.FeatureRules = RULES) -> dict[int, dict]:
    """The reference features at a week's as-of, by fixture player number."""
    with AsOfView(db, weekly_as_of(db, season, week)) as v:
        df = ft.features_for(v, season, week, _pool(v, season, week), rules=rules)
    return {int(r["gsis_id"][-3:]): r for r in df.iter_rows(named=True)}


def mates(db: Path, season: int, week: int) -> dict[tuple[str, int], dict]:
    with AsOfView(db, weekly_as_of(db, season, week)) as v:
        df = ft.unavailable_teammates(v, season, rules=RULES)
    return {(r["team"], int(r["gsis_id"][-3:])): r for r in df.iter_rows(named=True)}


# --------------------------------------------------------------------------------------
# The warehouse table
# --------------------------------------------------------------------------------------


def test_opportunity_table_is_registered_and_built(world):
    assert av.kind_of("fact_opportunity_week") == "event"
    assert av.GAME_DATA_TABLES["fact_opportunity_week"] == "pbp"
    assert "ff_opportunity" in sc.SOURCE_DATASETS
    assert set(av.TABLE_AVAILABILITY["fact_opportunity_week"].hindsight_columns) == {"position"}
    con = duckdb.connect(str(world), read_only=True)
    rows = con.execute(
        """SELECT season, week, season_type, posteam, receptions_exp
        FROM fact_opportunity_week WHERE player_id = ? ORDER BY week""",
        [gid(4)],
    ).fetchall()
    con.close()
    # season arrives as a string and week as a float upstream: cast to integers
    assert rows == [(2025, 1, "REG", "KC", 0.5), (2025, 2, "REG", "KC", 0.6),
                    (2025, 3, "REG", "KC", 4.0)]  # fmt: skip
    with (
        AsOfView(world, weekly_as_of(world, 2025, 3)) as v,
        pytest.raises(duckdb.BinderException),
    ):
        v.sql("SELECT position FROM fact_opportunity_week")


def test_opportunity_dedupe_and_null_ids(raw, db_path):
    g = [game(2025, 1, "2025-09-07", "13:00", "KC", "PHI", result=3)]
    rows = [
        {"season": "2025", "posteam": "KC", "week": 1.0, "game_id": g[0]["game_id"],
         "player_id": "00-0000009", "full_name": "B Name", "position": "OLB",
         "receptions_exp": 0.7},
        {"season": "2025", "posteam": "KC", "week": 1.0, "game_id": g[0]["game_id"],
         "player_id": "00-0000009", "full_name": "A Name", "position": "TE",
         "receptions_exp": 0.6},
        {"season": "2025", "posteam": "KC", "week": 1.0, "game_id": g[0]["game_id"],
         "player_id": None, "full_name": None, "position": None, "receptions_exp": 9.0},
    ]  # fmt: skip
    raw.write_season(2025, g, opportunity=rows)
    manifest = {m["table_name"]: m for m in wb.build_warehouse([2025], db_path=db_path)}
    m = manifest["fact_opportunity_week"]
    assert (m["n_rows"], m["n_dropped_null_key"], m["n_dropped_duplicates"]) == (1, 1, 1)
    con = duckdb.connect(str(db_path), read_only=True)
    # the row at a QB/RB/WR/TE position wins when an id repeats in a game
    assert con.execute("SELECT full_name, receptions_exp FROM fact_opportunity_week").fetchall() \
        == [("A Name", 0.6)]  # fmt: skip
    con.close()


# --------------------------------------------------------------------------------------
# xFP and FPOE
# --------------------------------------------------------------------------------------


def test_xfp_rescoring_hand_computed():
    df = pl.DataFrame({
        "pass_yards_gained_exp": [250.0, 0.0], "pass_touchdown_exp": [1.5, 0.0],
        "pass_interception_exp": [0.8, 0.0], "pass_two_point_conv_exp": [0.1, 0.0],
        "rush_yards_gained_exp": [12.0, 55.0], "rush_touchdown_exp": [0.1, 0.6],
        "rush_two_point_conv_exp": [0.0, 0.2], "receptions_exp": [0.0, 3.5],
        "rec_yards_gained_exp": [0.0, 28.0], "rec_touchdown_exp": [0.0, 0.25],
        "rec_two_point_conv_exp": [0.0, None],
    })  # fmt: skip
    # QB: 250 x 0.04 + 1.5 x 4 - 0.8 x 2 + 0.1 x 2 + 12 x 0.1 + 0.1 x 6 = 16.4
    # RB (full PPR): 55 x 0.1 + 0.6 x 6 + 0.2 x 2 + 3.5 + 28 x 0.1 + 0.25 x 6 = 17.3
    got = xfp(df, full_ppr()).get_column("xfp").to_list()
    assert got == A([16.4, 17.3])
    half = full_ppr().with_points(**{"receiving.receptions": 0.5})
    assert xfp(df, half).get_column("xfp").to_list() == A([16.4, 15.55])
    # fumbles and return touchdowns have no expected value: they never change xFP
    fumbly = full_ppr().with_points(**{"misc.fumbles_lost": -9.0})
    assert xfp(df, fumbly).get_column("xfp").to_list() == A([16.4, 17.3])
    con = duckdb.connect()
    con.register("t", df)
    assert [r[0] for r in con.execute(f"SELECT {xfp_sql(full_ppr())} FROM t").fetchall()] == \
        A([16.4, 17.3])  # fmt: skip
    with pytest.raises(KeyError, match="receptions_exp"):
        xfp(df.drop("receptions_exp"), full_ppr())


def test_xfp_rounding_bound_and_check(world):
    # 11 expected components rounded to 0.01 plus 3 part totals rounded to 0.01
    assert xfp_rounding_bound(nflverse_ppr()) == A(0.005 * 25.24 + 0.015)
    check = xfp_check(world)
    assert check.n_rows > 0 and check.rounding_bound > 0


def test_xfp_and_fpoe_windows(world):
    f = feats(world, 2025, 3)[4]
    # xFP per KC game: 1.0, 1.2, 8.0; points (receptions): 1, 1, 5
    assert (f["xfp_last"], f["xfp_avg3"]) == (8.0, A(3.4))
    assert f["fpoe_avg3"] == A(round((0 - 0.2 - 3.0) / 3, 6))
    assert (f["fantasy_points_last"], f["fantasy_points_avg3"]) == (5.0, A(7 / 3))


# --------------------------------------------------------------------------------------
# Opportunity windows
# --------------------------------------------------------------------------------------


def test_snap_target_and_share_windows(world):
    f = feats(world, 2025, 3)[4]  # KC games W1, W2, W3: snaps .3, .3, .85
    assert f["snap_share_last"] == 0.85
    assert f["snap_share_avg3"] == A(round(1.45 / 3, 6))
    assert f["snap_share_season"] == A(round(1.45 / 3, 6))
    assert f["snap_share_delta"] == A(0.55)  # .85 - mean(.3, .3)
    assert (f["target_share_last"], f["target_share_avg3"]) == (0.3, A(0.4 / 3))
    assert f["air_yards_share_avg3"] == A(0.15)
    assert f["wopr_avg3"] == A(round(0.92 / 3, 6))
    # week 4 (KC played): the window moves: W2, W3, W4; the season has 4 games
    g = feats(world, 2025, 4)[4]
    assert g["snap_share_avg3"] == A(round(1.95 / 3, 6))
    assert g["snap_share_season"] == A(0.5625)
    assert g["snap_share_delta"] == A(round(0.8 - 1.45 / 3, 6))


def test_week_one_asof_has_one_game_and_no_delta(world):
    f = feats(world, 2025, 1)[4]
    assert f["snap_share_last"] == f["snap_share_avg3"] == f["snap_share_season"] == 0.3
    assert f["snap_share_delta"] is None
    assert f["games_played_to_date"] == 1


def test_a_missed_game_counts_as_zero(world):
    f = feats(world, 2025, 3)
    # receiver 3 missed W3: last 0, the average counts the 0
    assert f[3]["snap_share_last"] == 0.0
    assert f[3]["snap_share_avg3"] == A(round(0.8 / 3, 6))
    # the traded receiver: KC games W1, W2 (he played them for DAL) count as 0 for KC
    t = f[10]
    assert (t["snap_share_last"], t["snap_share_avg3"]) == (0.5, A(round(0.5 / 3, 6)))
    assert t["target_share_avg3"] == A(0.05) and t["joined_team_recently"] is True
    assert f[4]["joined_team_recently"] is False


def test_carry_share_red_zone_and_routes(world):
    f = feats(world, 2025, 3)
    rb = f[7]  # carries 4, 5, 12 of the team's 20
    assert (rb["carry_share_last"], rb["carry_share_avg3"]) == (0.6, A(0.35))
    # red zone (yardline <= 20): 3 runs (18, 5, 2); the two-point try is not one; goal line
    # (<= 10): 5 and 2
    assert (rb["rz_carries_avg3"], rb["gl_opps_avg3"]) == (1.0, A(round(2 / 3, 6)))
    wr = f[4]  # targets at the 15 and the 8
    assert wr["rz_targets_avg3"] == A(round(2 / 3, 6))
    assert wr["gl_opps_avg3"] == A(round(1 / 3, 6))
    # dropbacks: W1 20 + 2 garbage, W2 22, W3 30 + 2 red-zone passes (not the 2-pt pass)
    assert wr["routes_proxy_avg3"] == A(round((22 * 0.3 + 22 * 0.3 + 32 * 0.85) / 3, 6))
    assert f[1]["carry_share_avg3"] == A(0.1)  # the QB: 2 of 20 each game


def test_no_visible_game_means_null(world):
    f = feats(world, 2025, 1)[50]  # MIA is off in week 1: no game yet
    for c in ("snap_share_last", "snap_share_avg3", "target_share_avg3", "xfp_avg3",
              "vacated_target_share", "top_teammate_out", "team_epa_per_play"):  # fmt: skip
        assert f[c] is None, c
    assert f["teammates_out"] is None
    # schedule features do not need a game: 5 games left, first opponent BUF has no game yet
    assert f["team_games_remaining"] == 5
    assert (f["opp_fp_allowed_next3"], f["n_opp_games_seen"]) == (1.0, 0)


def test_c1_columns_and_player_features(world):
    f = feats(world, 2025, 3)
    wr = f[4]
    assert wr["position"] == "WR" and wr["is_rookie"] is True and wr["years_exp"] == 0
    assert (wr["draft_round"], wr["is_undrafted"]) == (3, False)
    as_of = weekly_as_of(world, 2025, 3)
    days = (as_of.date() - date(2002, 9, 23)).days
    assert wr["age_at_asof"] == A(round(days / 365.25, 6))
    rb = f[7]
    assert (rb["draft_round"], rb["is_undrafted"], rb["is_rookie"]) == (None, True, False)
    assert rb["preseason_ranked"] is False and rb["preseason_pos_rank"] is None
    with AsOfView(world, as_of) as v:
        pool = _pool(v, 2025, 3)
    row = pool.filter(pl.col("gsis_id") == gid(4)).row(0, named=True)
    assert (wr["ppg_to_date"], wr["ppg_pos_rank"]) == (row["ppg_to_date"], row["ppg_pos_rank"])
    assert wr["games_played_to_date"] == row["games_to_date"] == 3


# --------------------------------------------------------------------------------------
# Teammates: the four rules, vacated shares, top_teammate_out
# --------------------------------------------------------------------------------------


def test_each_unavailability_rule_fires_alone(world):
    m = mates(world, 2025, 3)
    assert {k: r["rules"] for k, r in m.items()} == {
        ("KC", 2): "missed_last_game",  # 90% in W1-W2, no snap in W3, INA (an available status)
        ("KC", 5): "injury_report",  # Out on the W3 report; 45% before (below 50%)
        ("KC", 6): "left_team",  # on the W1-W2 rosters, not on the W3 roster (no CUT row)
        ("KC", 8): "roster_status",  # RES on the W3 roster; 40% before
        # traded to KC after W2 (60% for DAL in W1-W2, so he also "missed" DAL's W3)
        ("DAL", 10): "left_team+missed_last_game",
    }
    assert m["KC", 8]["team_status"] == "RES" and m["KC", 5]["report_status"] == "Out"
    # receiver 3 also missed W3 but averaged 40%: he does NOT count
    assert ("KC", 3) not in m
    # the shares cover his last 3 team games up to his last appearance (W1, W2)
    assert m["KC", 2]["last_week"] == 2
    assert m["KC", 2]["target_share_avg3"] == A(0.275)
    assert m["KC", 6]["carry_share_avg3"] == A(0.45)


def test_vacated_shares_same_position_and_top_teammate(world):
    f = feats(world, 2025, 3)
    wr = f[4]
    # all four KC teammates out: targets .275 + .10 + .05 + .125; carries .45 (the RB)
    assert wr["vacated_target_share"] == A(0.55)
    assert wr["vacated_carry_share"] == A(0.45)
    # receivers only: 2 (.275) and 5 (.10)
    assert wr["same_pos_vacated_target_share"] == A(0.375)
    assert wr["same_pos_vacated_carry_share"] == 0.0
    assert wr["teammate_same_pos_unavailable"] == 2
    assert wr["top_teammate_out"] is True  # 2 averaged 90% vs his 30% in W1-W2
    rb = f[7]
    assert (rb["same_pos_vacated_carry_share"], rb["teammate_same_pos_unavailable"]) == (
        A(0.45),
        1,
    )
    assert rb["top_teammate_out"] is True  # 45% vs his 30%
    te = f[9]
    assert te["same_pos_vacated_target_share"] == A(0.125)
    assert te["top_teammate_out"] is False  # the injured TE played less than he did (40% < 60%)
    # a player is never his own teammate: 2 (in the pool, INA) sees only receiver 5
    star = f[2]
    assert (star["same_pos_vacated_target_share"], star["teammate_same_pos_unavailable"]) == (
        A(0.10),
        1,
    )
    out = json.loads(wr["teammates_out"])
    assert [(m["gsis_id"], m["rule"]) for m in out] == [
        (gid(6), "left_team"), (gid(8), "roster_status"), (gid(2), "missed_last_game"),
        (gid(5), "injury_report")]  # fmt: skip
    # DAL lost the traded receiver: his DAL shares (W1-W2) are vacated there
    dal = feats(world, 2025, 3)[31]
    assert dal["same_pos_vacated_target_share"] == A(0.10)


def test_vacated_shares_stay_those_before_he_became_unavailable(world):
    # at the week-4 as-of the star receiver has missed W3 and W4: still out (mean of 0, .9,
    # .9 before W4 is 60%), and his vacated share is still his W1-W2 share, not diluted by
    # the zeros; receiver 5 played W4 and is no longer on the report: available again
    m = mates(world, 2025, 4)
    assert m["KC", 2]["rules"] == "roster_status+missed_last_game"  # now on IR too
    assert m["KC", 2]["target_share_avg3"] == A(0.275)
    assert ("KC", 5) not in m
    f = feats(world, 2025, 4)[4]
    assert f["same_pos_vacated_target_share"] == A(0.275)


def test_only_the_latest_injury_report_counts(world):
    # the TE (9) is Out on the W2 report: unavailable at the W2 as-of, not at W3's (the W3
    # report does not list him)
    assert mates(world, 2025, 2)["KC", 9]["rules"] == "injury_report"
    assert ("KC", 9) not in mates(world, 2025, 3)


def test_season_level_statuses_are_not_used():
    """2002-2015 rosters repeat one status all season (a player who ends the season on IR is
    RES on every week's roster, the weeks he played included): roster_status needs statuses
    that change from week to week."""
    frozen = pl.DataFrame({"gsis_id": ["a", "a", "b", "b"], "status": ["RES", "RES", "ACT", "ACT"]})
    weekly = pl.DataFrame({"gsis_id": ["a", "a", "b", "b"], "status": ["ACT", "RES", "ACT", None]})
    assert not ft.statuses_are_weekly(frozen)
    assert ft.statuses_are_weekly(weekly)
    assert not ft.statuses_are_weekly(frozen.head(0))


def test_rules_are_configurable(world):
    strict = ft.FeatureRules(missed_game_min_snap_share=0.35, injury_statuses=("Doubtful",))
    with AsOfView(world, weekly_as_of(world, 2025, 3)) as v:
        m = ft.unavailable_teammates(v, 2025, rules=strict)
    rules = {int(g[-3:]): r for g, r in zip(m["gsis_id"], m["rules"], strict=True)}
    assert rules[3] == "missed_last_game"  # 40% now clears the threshold
    assert rules[5] == "missed_last_game"  # no longer Out-by-report, but 45% >= 35%


# --------------------------------------------------------------------------------------
# Depth charts
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("slot", "group"),
    [("QB", "QB"), ("RB", "RB"), ("HB", "RB"), ("FB", "RB"), ("J", "RB"), ("RB`", "RB"),
     ("RB86", "RB"), ("WR", "WR"), ("WR ", "WR"), ("LWR", "WR"), ("RWR", "WR"), ("SWR", "WR"),
     ("WR1", "WR"), ("WR\\8", "WR"), ("WRE", "WR"), ("WE", "WR"), ("TE", "TE"), ("LTE", "TE"),
     ("RTE", "TE"), ("TE\\n", "TE"), ("H-B", "TE"), ("F", "TE"), ("TE/HB", "TE"),
     ("HB-TE", "TE"), ("HB/TE", "TE"), ("FB/TE", "TE"), ("RB/TE", "TE"), ("TE/FB", "TE"),
     ("LT", None), ("C", None), ("\n", None), ("19", None), ("OC", None), ("KR", None),
     (None, None)],
)  # fmt: skip
def test_slot_groups(slot, group):
    assert ft.slot_group(slot) == group


def test_daily_depth_chart_now_previous_and_change(world):
    f = feats(world, 2025, 3)
    # now: the 10:00 UTC snapshot before the 14:00 as-of (the 15:00 one is after it);
    # previous: the chart in force 7 days before (09-16 10:00)
    assert (f[4]["depth_rank_prev"], f[4]["depth_rank_now"], f[4]["depth_rank_change"]) == (
        3,
        1,
        2,
    )  # two receivers were listed ahead of him; now he shares the top
    assert (f[7]["depth_rank_prev"], f[7]["depth_rank_now"], f[7]["depth_rank_change"]) == (2, 1, 1)
    # the traded receiver: listed now (3rd), not a week ago on KC's chart
    assert (f[10]["depth_rank_now"], f[10]["depth_rank_prev"], f[10]["depth_listed"]) == (
        3,
        None,
        True,
    )
    # listed as a kick returner only: on the chart, no receiver rank
    assert (f[11]["depth_listed"], f[11]["depth_rank_now"]) == (True, None)
    # the injured star is off the chart; DAL publishes no chart at all
    assert (f[2]["depth_listed"], f[2]["depth_rank_now"], f[2]["depth_rank_prev"]) == (
        False,
        None,
        1,
    )
    assert f[31]["depth_listed"] is None


def test_legacy_depth_chart(world):
    f = feats(world, 2024, 2)
    # week 2's chart (public on the Wednesday before week 2) is in force; week 1's a week ago
    assert (f[101]["depth_rank_prev"], f[101]["depth_rank_now"], f[101]["depth_rank_change"]) \
        == (1, 2, -1)  # fmt: skip
    assert (f[102]["depth_rank_prev"], f[102]["depth_rank_now"]) == (1, 1)  # the '\n' slot
    assert (f[103]["depth_rank_now"], f[104]["depth_rank_now"]) == (1, 1)  # HB+FB; TE/HB
    # week 3's chart is not public yet: at week 3's as-of it is
    assert feats(world, 2024, 3)[101]["depth_rank_now"] == 1


# --------------------------------------------------------------------------------------
# Context: team offense, next opponents, byes
# --------------------------------------------------------------------------------------


def test_team_offense_context(world):
    kc = feats(world, 2025, 3)[4]
    plays = [p for p in plays_2025(games_2025()) if p["posteam"] == "KC" and p["week"] <= 3
             and p["play_type"] in ("pass", "run") and p["two_point_attempt"] == 0]  # fmt: skip
    neutral = [p for p in plays if p["wp"] == 0.5]
    assert kc["team_epa_per_play"] == A(round(sum(p["epa"] for p in plays) / len(plays), 6))
    assert kc["team_epa_per_play_neutral"] == A(
        round(sum(p["epa"] for p in neutral) / len(neutral), 6)
    )
    assert kc["team_plays_per_game"] == A(round(len(plays) / 3, 6))
    assert kc["team_pass_rate_neutral"] == A(
        round(sum(p["pass"] for p in neutral) / len(neutral), 6)
    )


def test_next_opponents_points_allowed(world):
    f = feats(world, 2025, 1)
    # week 1 WR points allowed: PHI 8 (KC's receivers), KC 6, SEA 5 (DAL's two), DAL 2;
    # league average 21 / 4 team-games. KC's next three: SEA, DAL, PHI
    assert f[4]["opp_fp_allowed_next3"] == A(round((5 + 2 + 8) / 3 / 5.25, 6))
    assert f[4]["n_opp_games_seen"] == 3
    # RBs: only PHI allowed a point (1 of 4 team-games): (0 + 0 + 4) / 3
    assert f[7]["opp_fp_allowed_next3"] == A(round(4 / 3, 6))
    assert f[1]["opp_fp_allowed_next3"] == 1.0  # QBs scored nothing anywhere: all average
    assert f[4]["team_games_remaining"] == 4 and f[4]["bye_in_next3"] is False


def test_next_opponents_skip_the_bye(world):
    f = feats(world, 2025, 3)[4]
    # KC after week 3: W4 PHI, W5 off, W6 DAL (the season ends in week 6)
    assert f["bye_in_next3"] is True and f["team_games_remaining"] == 2
    assert f["n_opp_games_seen"] == 2 + 3  # PHI played W1-W2, DAL W1-W3
    wr_points = _wr_points_allowed(3)
    league = sum(p for p, _ in wr_points.values()) / sum(n for _, n in wr_points.values())
    ratio = {d: (p / n) / league for d, (p, n) in wr_points.items()}
    assert f["opp_fp_allowed_next3"] == A(round((ratio["PHI"] + ratio["DAL"]) / 2, 6))


def _wr_points_allowed(up_to_week: int) -> dict[str, tuple[float, int]]:
    """Independent re-count from the fixture: defense -> (WR points allowed, games)."""
    games = [g for g in games_2025() if g["week"] <= up_to_week]
    out: dict[str, list] = {}
    for g in games:
        for team in (g["home_team"], g["away_team"]):
            d = _opp(g, team)
            pts = sum(st["receptions"] for n, weeks in all_lines(games_2025()).items()
                      for w, (t, _, _, st) in weeks.items()
                      if w == g["week"] and t == team and st and PLAYERS[n][1] == "WR")  # fmt: skip
            acc = out.setdefault(d, [0.0, 0])
            acc[0] += pts
            acc[1] += 1
    return {d: (p, n) for d, (p, n) in out.items()}


def test_a_cancelled_game_still_counts_as_scheduled(world):
    rules = ft.FeatureRules(cancelled_games=("2025_05_KC_DAL", "1999_01_KC_DAL"))
    f = feats(world, 2025, 3, rules)[4]
    # KC's week-5 bye becomes the (later cancelled) game at DAL: no bye, 3 games left
    assert (f["bye_in_next3"], f["team_games_remaining"]) == (False, 3)
    assert f["n_opp_games_seen"] == 2 + 3 + 3


# --------------------------------------------------------------------------------------
# Point in time
# --------------------------------------------------------------------------------------


def _builder(season: int, week: int, wrap=None):
    def build(v: AsOfView) -> pl.DataFrame:
        view = wrap(v) if wrap else v
        pool = candidate_pool(v, season, week, rules=POOL_RULES)
        return ft.features_for(view, season, week, pool, rules=RULES)

    return build


@pytest.mark.parametrize(("season", "week"), [(2025, 1), (2025, 3), (2025, 4), (2024, 2)])
def test_features_pass_the_leakage_harness(world, season, week):
    """Deleting or scrambling everything not yet public changes nothing: week 4 is a split
    week (SEA at DAL on the Wednesday), 2024 uses weekly depth charts."""
    out = assert_future_invariant(
        _builder(season, week), world, weekly_as_of(world, season, week), key=["gsis_id"]
    )
    assert out.height > 0 and out.columns == ["season", "week", "gsis_id", *ft.FEATURE_COLUMNS,
                                              *ft.INFO_COLUMNS]  # fmt: skip


class _Rewritten:
    """A deliberately LEAKY view: rewrites a piece of the features' SQL."""

    def __init__(self, view: AsOfView, old: str, new: str):
        self._view, self._old, self._new = view, old, new
        self.hits = 0

    @property
    def as_of(self) -> datetime:
        return self._view.as_of

    def sql(self, query: str, params=None) -> pl.DataFrame:
        if self._old in query:
            self.hits += 1
            query = query.replace(self._old, self._new)
        return self._view.sql(query, params)


# Each leak reads a table around the view (``wh.``) AND skips the time filter on that input
# (the reference path filters every input frame a second time with asof_filter, so a
# ``wh.`` read alone cannot leak: test_sql_bypass_alone_is_filtered_again below).
LEAKS = {
    # teammates: the NEXT week's injury report (W4, public at KC's W4 kickoff) becomes "the
    # latest report": receiver 5 is no longer Out, his share is no longer vacated
    "next_week_injury_report": (2025, 3, "injuries", "FROM fact_injury_report",
                                "FROM wh.fact_injury_report"),
    # teammates: a teammate's FUTURE roster status (the star is on IR on the week-4 roster)
    "future_roster_status": (2025, 3, "rosters", "FROM fact_roster_week WHERE season = 2025",
                             "FROM wh.fact_roster_week WHERE season = 2025"),
    # rolling averages: 'week <= 4' is not time: week 4's SEA at DAL was played on the
    # Wednesday after the as-of, so DAL's and SEA's 'last game' would be one not yet played
    "rolling_average_by_week_number": (
        2025, 4, "team_games", "FROM fact_game\n            WHERE season = 2025 AND "
        "season_type = 'REG'", "FROM wh.fact_game\n            WHERE season = 2025 AND "
        "week <= 4 AND season_type = 'REG'"),
    # depth chart: the snapshot taken an hour after the as-of
    "depth_chart_after_asof": (2025, 3, "depth", "FROM fact_depth_chart",
                               "FROM wh.fact_depth_chart"),
}  # fmt: skip


def _leaky_visible(skip: str):
    original = ft.SeasonInputs.visible

    def visible(self, as_of):
        out = original(self, as_of)
        return dataclasses.replace(out, frames={**out.frames, skip: self.frames[skip]})

    return visible


@pytest.mark.parametrize("leak", list(LEAKS))
def test_leaky_features_fail_the_harness(world, leak, monkeypatch):
    season, week, skip, old, new = LEAKS[leak]
    wrappers: list[_Rewritten] = []

    def wrap(v):
        wrappers.append(_Rewritten(v, old, new))
        return wrappers[-1]

    monkeypatch.setattr(ft.SeasonInputs, "visible", _leaky_visible(skip))
    with pytest.raises(LeakageError):
        assert_future_invariant(
            _builder(season, week, wrap), world, weekly_as_of(world, season, week),
            key=["gsis_id"],
        )  # fmt: skip
    assert wrappers and wrappers[0].hits > 0, "the rewrite no longer matches the features' SQL"


@pytest.mark.parametrize("leak", list(LEAKS))
def test_sql_bypass_alone_is_filtered_again(world, leak):
    """Defense in depth: even a ``wh.`` read in the reference path is cut back to the rows
    public at the as-of, because every input frame passes asof_filter afterwards."""
    season, week, _, old, new = LEAKS[leak]
    out = assert_future_invariant(
        _builder(season, week, lambda v: _Rewritten(v, old, new)), world,
        weekly_as_of(world, season, week), key=["gsis_id"],
    )  # fmt: skip
    assert out.height > 0


def test_features_read_only_through_the_view(world):
    assert not ft.uses_bypass(ft.input_sql(2025, RULES, batch=False))
    assert not ft.uses_bypass(ft.input_sql(2025, RULES, batch=True))
    with AsOfView(world, weekly_as_of(world, 2025, 3)) as v:
        ft.features_for(v, 2025, 3, _pool(v, 2025, 3), rules=RULES)
        used = v.tables_used
    assert used >= {"fact_game", "fact_snaps", "fact_player_week", "fact_team_week", "fact_play",
                    "fact_opportunity_week", "fact_roster_week", "fact_injury_report",
                    "fact_depth_chart", "fact_schedule", "dim_player", "dim_week"}  # fmt: skip


def test_batch_path_equals_the_reference(world):
    pool = pool_history(world, [2024, 2025], rules=POOL_RULES)
    batch = ft.features_history(world, pool, rules=RULES)
    frames = []
    for (season, week, as_of), rows in sorted(
        pool.partition_by(["season", "week", "as_of"], as_dict=True, maintain_order=True).items()
    ):
        with AsOfView(world, as_of) as v:
            frames.append(ft.features_for(v, season, week, rows, rules=RULES))
    reference = pl.concat(frames)
    assert batch.height == pool.height == reference.height > 0
    assert set(batch.get_column("week").unique()) >= {1, 2, 3, 4, 5}
    assert_frame_equal(batch, reference)


def test_features_for_checks_its_input(world):
    with AsOfView(world, weekly_as_of(world, 2025, 3)) as v:
        pool = _pool(v, 2025, 3)
        with pytest.raises(KeyError, match="team"):
            ft.features_for(v, 2025, 3, pool.drop("team"), rules=RULES)
        with pytest.raises(ValueError, match="not 2025 week 2"):
            ft.features_for(v, 2025, 2, pool, rules=RULES)
        empty = ft.features_for(v, 2025, 3, pool.head(0), rules=RULES)
    assert empty.height == 0 and empty.columns == ft.empty_features().columns
    con = duckdb.connect(str(world), read_only=True)
    try:
        inp = ft.load_inputs(ft._Warehouse(con), 2025, RULES, batch=True)
    finally:
        con.close()
    with pytest.raises(ValueError, match="visible"):  # future rows must be filtered first
        ft.compute_features(inp, 2025, 3, datetime(2025, 9, 23, 14, tzinfo=UTC), pool, RULES)


# --------------------------------------------------------------------------------------
# Registry, dataset, report, CLI
# --------------------------------------------------------------------------------------


def test_every_feature_is_registered_and_no_identifier_is_a_feature():
    used = rg.check_features(ft.FEATURE_COLUMNS, "waiver_radar")
    assert len(used) == len(ft.FEATURE_COLUMNS) == len(set(ft.FEATURE_COLUMNS))
    assert not {"gsis_id", "team", "name", "player_id"} & set(ft.FEATURE_COLUMNS)
    assert set(ft.FEATURE_SCHEMA) == set(ft.FEATURE_COLUMNS)
    for name in ("vacated_target_share", "top_teammate_out", "snap_share_delta"):
        assert rg.get(name).reason_template


def test_dataset_is_the_labelled_pool_with_features(world):
    ds = build_dataset(world, [2024, 2025], pool_rules=POOL_RULES)
    assert ds.columns == dataset_columns()
    assert ds.select("season", "week", "gsis_id").is_duplicated().sum() == 0
    assert ds.equals(ds.sort("season", "week", "gsis_id"))
    assert set(ds.get_column("week").unique()) <= {1, 2, 3, 4, 5}  # no last-week rows
    row = ds.filter((pl.col("season") == 2025) & (pl.col("week") == 3)
                    & (pl.col("gsis_id") == gid(4))).row(0, named=True)  # fmt: skip
    assert row["same_pos_vacated_target_share"] == A(0.375)
    assert row["label_status"] == "final" and row["window_weeks"] == [4, 6]


def test_report_and_cli(world, tmp_path):
    runner = CliRunner()
    out = tmp_path / "ds" / "dataset.parquet"
    args = ["radar", "dataset", "--db", str(world), "--out", str(out), "--start", "2024",
            "--end", "2025"]  # fmt: skip
    res = runner.invoke(app, args)
    assert res.exit_code == 0, res.output
    first = out.read_bytes()
    assert runner.invoke(app, args).exit_code == 0
    assert out.read_bytes() == first  # deterministic, byte for byte
    rep = tmp_path / "rep" / "features.md"
    rargs = ["radar", "features-report", "--db", str(world), "--dataset", str(out), "--out",
             str(rep)]  # fmt: skip
    res = runner.invoke(app, rargs)
    assert res.exit_code == 0, res.output
    text, csv_text = rep.read_text(), rep.with_suffix(".csv").read_text()
    assert "## Top 10 features per position" in text and "## xFP check" in text
    assert csv_text.splitlines()[0] == ",".join(CSV_COLUMNS)
    assert runner.invoke(app, rargs).exit_code == 0
    assert (rep.read_text(), rep.with_suffix(".csv").read_text()) == (text, csv_text)
    report = build_feature_report(pl.read_parquet(out), world)
    assert report.markdown == text
    res = runner.invoke(app, ["radar", "features", "2025", "3", "--db", str(world), "--team",
                              "kc", "--all"])  # fmt: skip
    assert res.exit_code == 0, res.output
    assert "Kc Wr Focus" in res.output and "missed_last_game" in res.output
    missing = runner.invoke(app, ["radar", "features-report", "--db", str(world), "--dataset",
                                  str(tmp_path / "nope.parquet")])  # fmt: skip
    assert missing.exit_code == 1 and "run `twm radar dataset` first" in missing.output


# --------------------------------------------------------------------------------------
# Real data (opt-in: `uv run pytest -m realdata`; builds into a temp file, never downloads)
# --------------------------------------------------------------------------------------

# a post-game-roster season (2014), a split week (2020 W12), game-day rosters with weekly
# charts (2016, 2023), daily charts (2025) and an early week
REAL_ASOFS = [(2013, 2), (2014, 6), (2016, 9), (2020, 12), (2023, 6), (2025, 10)]


@pytest.mark.realdata
def test_real_batch_equals_reference(real_full_db):
    path, _ = real_full_db
    rules = ft.FeatureRules.from_config()
    for season, week in REAL_ASOFS:
        with AsOfView(path, weekly_as_of(path, season, week)) as v:
            pool = candidate_pool(v, season, week)
            ref = ft.features_for(v, season, week, pool, rules=rules)
        bat = ft.features_history(path, pool, rules=rules)
        assert ref.height == pool.height > 300, (season, week)
        assert_frame_equal(bat, ref)


@pytest.mark.realdata
@pytest.mark.parametrize(("season", "week"), [(2014, 6), (2020, 12)])
def test_real_features_pass_the_leakage_harness(real_full_db, season, week):
    path, _ = real_full_db

    def build(v: AsOfView) -> pl.DataFrame:
        return ft.features_for(v, season, week, candidate_pool(v, season, week))

    out = assert_future_invariant(build, path, weekly_as_of(path, season, week), key=["gsis_id"])
    assert out.height > 300


@pytest.mark.realdata
def test_real_roster_status_rule_only_with_weekly_statuses(real_full_db):
    """No roster_status teammate in a season whose roster statuses never change (2014); many
    in a season whose statuses are weekly (2023)."""
    path, _ = real_full_db
    counts = {}
    for season in (2014, 2023):
        with AsOfView(path, weekly_as_of(path, season, 8)) as v:
            m = ft.unavailable_teammates(v, season)
        counts[season] = m.filter(pl.col("rules").str.contains("roster_status")).height
        assert m.height > 50, season
    print(counts)
    assert counts[2014] == 0 and counts[2023] > 20


@pytest.mark.realdata
def test_real_null_rates_and_ranges(real_full_db):
    path, _ = real_full_db
    pool = pool_history(path, [2019, 2024], weeks=[3, 9, 15])
    f = ft.features_history(path, pool)
    n = f.height
    nulls = {c: f.get_column(c).null_count() / n for c in ft.FEATURE_COLUMNS}
    print({c: round(v, 3) for c, v in nulls.items() if v})
    # windowed opportunity features are NULL only for a team without a visible game (none here)
    for c in ("snap_share_avg3", "target_share_avg3", "xfp_avg3", "vacated_target_share",
              "team_epa_per_play", "opp_fp_allowed_next3", "bye_in_next3", "position",
              "is_undrafted", "depth_listed"):  # fmt: skip
        assert nulls[c] == 0, c
    for c in ("depth_rank_now", "draft_round", "ppg_to_date", "age_at_asof", "is_rookie"):
        assert nulls[c] < 0.6, c
    for c in ("snap_share_avg3", "target_share_avg3", "carry_share_avg3", "team_pass_rate_neutral"):
        assert f.get_column(c).min() >= 0 and f.get_column(c).max() <= 1.0 + 1e-9, c
    assert 0.3 < f.get_column("opp_fp_allowed_next3").mean() < 3
    assert 50 < f.get_column("team_plays_per_game").mean() < 75
    assert (f.get_column("vacated_target_share") > 0).mean() > 0.3


@pytest.mark.realdata
def test_real_xfp_reproduces_ffopportunity(real_full_db):
    path, _ = real_full_db
    c = xfp_check(path)
    print(c)
    assert c.n_rows > 100_000
    assert c.n_within_rounding / c.n_rows > 0.998
    # every row beyond the rounding bound is a rushing two-point try (docs/waiver_radar.md)
    assert c.n_beyond == c.n_beyond_rush_2pt
    assert c.max_abs_diff_without_rush_2pt <= c.rounding_bound
    # the default config scores the expected components like nflverse-PPR
    assert xfp_check(path, ScoringRules.from_config()).n_within_rounding == c.n_within_rounding
