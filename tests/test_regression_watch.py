"""Regression Watch, step D1: per-play fantasy points and xFP, the player-game frame with and
without garbage time, its point-in-time guarantees, the report and the CLI.

Offline tests use synthetic plays (every attribution rule: passer / target / rusher, laterals,
touchdowns of every kind, two-point conversions, lost fumbles with two fumblers, garbage flags)
and a tiny synthetic warehouse; ``realdata`` tests run the reconciliations on the real cache.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import polars as pl
import pytest
from polars.testing import assert_frame_equal
from typer.testing import CliRunner

from tests.conftest import frame, game
from twm import registry as rg
from twm.asof import AsOfView, weekly_as_of
from twm.backtest.leakage import LeakageError, assert_future_invariant
from twm.cli import app
from twm.modules.regression_watch import player_week as pw
from twm.modules.regression_watch import plays as rp
from twm.modules.regression_watch import report as rr
from twm.scoring import ScoringRules, full_ppr, nflverse_ppr, score_sql, xfp_sql
from twm.warehouse import available as av
from twm.warehouse import build as wb
from twm.warehouse import schema as sc

T0 = datetime(2025, 9, 8, 12, 0)

# --------------------------------------------------------------------------------------
# Per-play fantasy points (plays.play_stats_sql) on a hand-made fact_play
# --------------------------------------------------------------------------------------


def _fact_play(rows: list[dict]) -> duckdb.DuckDBPyConnection:
    """An in-memory ``fact_play`` with the warehouse's columns and types; unset columns NULL."""
    con = duckdb.connect()
    cols = ", ".join(f"{sc.q(c.name)} {c.type}" for c in sc.tables()["fact_play"].columns)
    con.execute(f"CREATE TABLE fact_play ({cols}, available_at TIMESTAMP)")
    base = {"game_id": "2025_01_KC_PHI", "season": 2025, "week": 1, "season_type": "REG",
            "posteam": "KC", "is_garbage_time": False, "available_at": T0,
            "two_point_attempt": 0}  # fmt: skip
    full = [{**base, "play_id": i + 1, **r} for i, r in enumerate(rows)]
    keys = sorted({k for r in full for k in r})
    df = pl.from_dicts([{k: r.get(k) for k in keys} for r in full], infer_schema_length=None)
    con.register("df_rows", df)
    con.execute("INSERT INTO fact_play BY NAME SELECT * FROM df_rows")
    con.unregister("df_rows")
    return con


def _lines(rows: list[dict]) -> dict[tuple[int, str], dict]:
    con = _fact_play(rows)
    df = con.execute(rp.play_stats_sql()).pl()
    con.close()
    assert not df.select("play_id", "gsis_id").is_duplicated().any()
    return {(r["play_id"], r["gsis_id"]): r for r in df.iter_rows(named=True)}


def _nonzero(line: dict) -> dict[str, int]:
    return {c: line[c] for c in rp.PLAY_STAT_COLUMNS if line[c]}


QB, WR, WR2, RB, RB2, DEF = "QB", "WR1", "WR2", "RB1", "RB2", "DEF1"


def test_pass_attribution_passer_target_and_touchdown():
    lines = _lines([
        {"play_type": "pass", "passer_player_id": QB, "receiver_player_id": WR,
         "complete_pass": 1, "passing_yards": 25, "receiving_yards": 25, "pass_touchdown": 1,
         "td_player_id": WR, "td_team": "KC"},
        {"play_type": "pass", "passer_player_id": QB, "receiver_player_id": WR2,
         "complete_pass": 0, "interception": 1},
    ])  # fmt: skip
    assert _nonzero(lines[1, QB]) == {"passing_yards": 25, "passing_tds": 1}
    assert _nonzero(lines[1, WR]) == {"receptions": 1, "receiving_yards": 25, "receiving_tds": 1}
    assert _nonzero(lines[2, QB]) == {"passing_interceptions": 1}
    assert _nonzero(lines[2, WR2]) == {}  # an incomplete target: no points, but a line
    assert lines[1, WR]["td_kind"] == "receiving"


def test_laterals_split_yards_and_the_touchdown_goes_to_the_scorer():
    lines = _lines([
        # catch for 13, lateral for 20 more and a touchdown by the lateral receiver
        {"play_type": "pass", "passer_player_id": QB, "receiver_player_id": WR,
         "complete_pass": 1, "passing_yards": 33, "receiving_yards": 13, "lateral_reception": 1,
         "lateral_receiver_player_id": WR2, "lateral_receiving_yards": 20, "pass_touchdown": 1,
         "td_player_id": WR2},
        # run for 25, lateral for 5 more and a touchdown by the lateral rusher
        {"play_type": "run", "rusher_player_id": RB, "rushing_yards": 25, "lateral_rush": 1,
         "lateral_rusher_player_id": RB2, "lateral_rushing_yards": 5, "rush_touchdown": 1,
         "td_player_id": RB2},
    ])  # fmt: skip
    assert _nonzero(lines[1, QB]) == {"passing_yards": 33, "passing_tds": 1}
    assert _nonzero(lines[1, WR]) == {"receptions": 1, "receiving_yards": 13}
    assert _nonzero(lines[1, WR2]) == {"receiving_yards": 20, "receiving_tds": 1}
    assert _nonzero(lines[2, RB]) == {"rushing_yards": 25}
    assert _nonzero(lines[2, RB2]) == {"rushing_yards": 5, "rushing_tds": 1}


def test_two_point_conversions_count_only_when_good_and_add_no_yards():
    lines = _lines([
        {"play_type": "pass", "two_point_attempt": 1, "two_point_conv_result": "success",
         "passer_player_id": QB, "receiver_player_id": WR, "complete_pass": 1,
         "passing_yards": 2, "receiving_yards": 2},
        {"play_type": "run", "two_point_attempt": 1, "two_point_conv_result": "failure",
         "rusher_player_id": RB, "rushing_yards": 1},
        {"play_type": "run", "two_point_attempt": 1, "two_point_conv_result": "success",
         "rusher_player_id": RB2, "rushing_yards": 2},
    ])  # fmt: skip
    assert _nonzero(lines[1, QB]) == {"passing_2pt_conversions": 1}
    assert _nonzero(lines[1, WR]) == {"receiving_2pt_conversions": 1}
    assert _nonzero(lines[2, RB]) == {}
    assert _nonzero(lines[3, RB2]) == {"rushing_2pt_conversions": 1}


def test_lost_fumbles_go_to_the_player_who_lost_the_ball():
    lines = _lines([
        # the receiver fumbles after the catch, the defense recovers
        {"play_type": "pass", "passer_player_id": QB, "receiver_player_id": WR,
         "complete_pass": 1, "passing_yards": 8, "receiving_yards": 8, "fumble_lost": 1,
         "fumbled_1_player_id": WR, "fumbled_1_team": "KC", "fumble_recovery_1_team": "PHI"},
        # a strip sack
        {"play_type": "pass", "passer_player_id": QB, "sack": 1, "fumble_lost": 1,
         "fumbled_1_player_id": QB, "fumbled_1_team": "KC", "fumble_recovery_1_team": "PHI"},
        # two fumbles: the rusher's is recovered by his team, the second fumbler loses it
        {"play_type": "run", "rusher_player_id": RB, "rushing_yards": 3, "fumble_lost": 1,
         "fumbled_1_player_id": RB, "fumbled_1_team": "KC", "fumble_recovery_1_team": "KC",
         "fumbled_2_player_id": RB2, "fumbled_2_team": "KC", "fumble_recovery_2_team": "PHI"},
        # a fumble recovered by his own team is not lost
        {"play_type": "run", "rusher_player_id": RB, "rushing_yards": 1, "fumble_lost": 0,
         "fumbled_1_player_id": RB, "fumbled_1_team": "KC", "fumble_recovery_1_team": "KC"},
    ])  # fmt: skip
    assert _nonzero(lines[1, WR]) == {
        "receptions": 1, "receiving_yards": 8, "fumbles_lost_total": 1,
        "receiving_fumbles_lost": 1,
    }  # fmt: skip
    assert _nonzero(lines[1, QB]) == {"passing_yards": 8}
    assert _nonzero(lines[2, QB]) == {"fumbles_lost_total": 1, "sack_fumbles_lost": 1}
    assert _nonzero(lines[3, RB]) == {"rushing_yards": 3}
    assert _nonzero(lines[3, RB2]) == {"fumbles_lost_total": 1}  # not a rusher on the play
    assert _nonzero(lines[4, RB]) == {"rushing_yards": 1}


def test_return_recovery_and_defensive_touchdowns():
    lines = _lines([
        {"play_type": "kickoff", "kickoff_returner_player_id": WR, "return_touchdown": 1,
         "td_player_id": WR},
        # a returner muffs the punt, recovers it himself and scores: a return touchdown
        {"play_type": "punt", "punt_returner_player_id": WR2, "fumble_recovery_1_player_id": WR2,
         "td_player_id": WR2},
        # the quarterback fumbles, a teammate recovers in the end zone
        {"play_type": "run", "rusher_player_id": QB, "fumbled_1_player_id": QB,
         "fumble_recovery_1_player_id": RB, "td_player_id": RB},
        # an interception returned for a touchdown: nothing for the offense's players
        {"play_type": "pass", "passer_player_id": QB, "receiver_player_id": WR,
         "interception": 1, "return_touchdown": 1, "td_player_id": DEF},
    ])  # fmt: skip
    assert _nonzero(lines[1, WR]) == {"special_teams_tds": 1}
    assert _nonzero(lines[2, WR2]) == {"special_teams_tds": 1}
    assert _nonzero(lines[3, RB]) == {"fumble_recovery_tds": 1}
    assert _nonzero(lines[3, QB]) == {}
    assert lines[4, DEF]["td_kind"] == "other" and _nonzero(lines[4, DEF]) == {}
    assert _nonzero(lines[4, QB]) == {"passing_interceptions": 1}


def test_kneels_count_as_rushing_yards_and_garbage_flags_are_kept():
    lines = _lines([
        {"play_type": "qb_kneel", "rusher_player_id": QB, "rushing_yards": -1,
         "is_garbage_time": True},
    ])  # fmt: skip
    assert _nonzero(lines[1, QB]) == {"rushing_yards": -1}
    assert lines[1, QB]["is_garbage_time"] is True


def test_play_lines_are_scored_by_the_one_scoring_engine():
    con = _fact_play([
        {"play_type": "pass", "passer_player_id": QB, "receiver_player_id": WR,
         "complete_pass": 1, "passing_yards": 25, "receiving_yards": 25, "pass_touchdown": 1,
         "td_player_id": WR},
        {"play_type": "run", "rusher_player_id": RB, "rushing_yards": 7, "fumble_lost": 1,
         "fumbled_1_player_id": RB, "fumbled_1_team": "KC", "fumble_recovery_1_team": "PHI"},
        {"play_type": "kickoff", "kickoff_returner_player_id": WR, "td_player_id": WR},
    ])  # fmt: skip
    q = f"SELECT gsis_id, sum({{}}) AS p FROM ({rp.play_stats_sql()}) GROUP BY 1 ORDER BY 1"
    got = dict(con.execute(q.format(score_sql(full_ppr()))).fetchall())
    # QB 25 x 0.04 + 4; RB 7 x 0.1 - 2; WR 1 + 2.5 + 6 + 6 (return)
    assert got == pytest.approx({QB: 5.0, RB: -1.3, WR: 15.5})
    # nflverse's scoring counts only scrimmage fumbles: the rushing fumble still counts
    got = dict(con.execute(q.format(score_sql(nflverse_ppr()))).fetchall())
    assert got[RB] == pytest.approx(-1.3)
    con.close()
    assert set(rp.PLAY_STAT_COLUMNS) == set(ScoringRules.from_config().required_columns()) | {
        "sack_fumbles_lost", "rushing_fumbles_lost", "receiving_fumbles_lost"}  # fmt: skip


# --------------------------------------------------------------------------------------
# Per-play xFP (plays.play_expected_sql) on hand-made per-play tables
# --------------------------------------------------------------------------------------


def _opportunity_db(passes: list[dict], rushes: list[dict]) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    for name, rows in (("fact_opportunity_pass", passes), ("fact_opportunity_rush", rushes)):
        cols = ", ".join(f"{sc.q(c.name)} {c.type}" for c in sc.tables()[name].columns)
        con.execute(f"CREATE TABLE {name} ({cols}, available_at TIMESTAMP)")
        base = {"game_id": "2025_01_KC_PHI", "season": 2025, "week": 1, "season_type": "REG",
                "posteam": "KC", "is_garbage_time": False, "available_at": T0,
                "two_point_attempt": 0}  # fmt: skip
        full = [{**base, "play_id": i + 1, **r} for i, r in enumerate(rows)]
        if full:
            keys = sorted({k for r in full for k in r})
            df = pl.from_dicts([{k: r.get(k) for k in keys} for r in full])
            con.register("df_rows", df)
            con.execute(f"INSERT INTO {name} BY NAME SELECT * FROM df_rows")
            con.unregister("df_rows")
    return con


def test_expected_values_per_play_follow_the_weekly_rules():
    con = _opportunity_db(
        [
            {"passer_player_id": QB, "receiver_player_id": WR, "complete_pass": 1,
             "receiving_yards": 15, "air_yards": 10, "pass_completion_exp": 0.7,
             "yards_after_catch_exp": 5.0, "pass_touchdown_exp": 0.05,
             "pass_interception_exp": 0.02, "two_point_conv_exp": 0.0},
            # a two-point try: only its conversion chance counts
            {"passer_player_id": QB, "receiver_player_id": WR, "two_point_attempt": 1,
             "air_yards": 2, "pass_completion_exp": 0.45, "yards_after_catch_exp": 0.3,
             "pass_touchdown_exp": 0.0, "pass_interception_exp": 0.03,
             "two_point_conv_exp": 0.45, "is_garbage_time": True},
            # a pass without an identified target: the passer's only
            {"passer_player_id": QB, "air_yards": 20, "pass_completion_exp": 0.4,
             "yards_after_catch_exp": 4.0, "pass_touchdown_exp": 0.01,
             "pass_interception_exp": 0.05},
        ],
        [
            {"rusher_player_id": RB, "rush_yards_exp": 4.0, "rush_touchdown_exp": 0.02},
            {"rusher_player_id": QB, "rush_yards_exp": -1.0, "rush_touchdown_exp": 0.0},
        ],
    )  # fmt: skip
    df = con.execute(rp.play_expected_sql()).pl()
    by = {(r["play_id"], r["gsis_id"], r["role"]): r for r in df.iter_rows(named=True)}
    rec = by[1, WR, "receiver"]
    assert rec["receptions_exp"] == pytest.approx(0.7)
    assert rec["rec_yards_gained_exp"] == pytest.approx(0.7 * 15)
    assert (rec["yac"], rec["yac_exp"]) == (5.0, 5.0)
    assert by[1, QB, "passer"]["pass_yards_gained_exp"] == pytest.approx(10.5)
    two = by[2, WR, "receiver"]
    assert two["receptions_exp"] == 0 and two["rec_two_point_conv_exp"] == pytest.approx(0.45)
    assert two["yac"] is None and two["is_garbage_time"] is True
    assert by[2, QB, "passer"]["pass_interception_exp"] == 0  # the weekly file leaves it out
    assert (3, None, "receiver") not in by and by[3, QB, "passer"]["pass_completions_exp"] == 0.4
    rules = full_ppr()
    x = dict(
        con.execute(
            f"SELECT gsis_id, sum({xfp_sql(rules)}) FROM ({rp.play_expected_sql()}) GROUP BY 1"
        ).fetchall()
    )
    # WR: 0.7 + 1.05 + 0.3 + 0.9 (two-point); RB: 0.4 + 0.12
    assert x[WR] == pytest.approx(0.7 + 1.05 + 0.3 + 0.9)
    assert x[RB] == pytest.approx(0.52)
    qb = 10.5 * 0.04 + 0.2 - 0.04 + 0.9 + (0.4 * 24) * 0.04 + 0.04 - 0.1 - 0.1
    assert x[QB] == pytest.approx(qb)
    con.close()


def test_xfp_bound_is_the_rounding_of_the_weekly_components():
    # full PPR: 0.04 + 4 + 2 + 2 + 0.1 + 6 + 2 + 1 + 0.1 + 6 + 2 = 25.24 -> 0.1262
    assert rr.xfp_bound(full_ppr()) == pytest.approx(0.1262)


# --------------------------------------------------------------------------------------
# The warehouse tables
# --------------------------------------------------------------------------------------

PBP_DTYPES = {
    "game_id": pl.String(), "play_id": pl.Float64(), "season": pl.Int32(), "week": pl.Int32(),
    "season_type": pl.String(), "game_date": pl.String(), "posteam": pl.String(),
    "defteam": pl.String(), "home_team": pl.String(), "away_team": pl.String(),
    "play_type": pl.String(), "qtr": pl.Float64(), "wp": pl.Float64(),
    "half_seconds_remaining": pl.Float64(), "score_differential": pl.Float64(),
    "two_point_attempt": pl.Float64(), "pass_attempt": pl.Float64(),
    "rush_attempt": pl.Float64(), "complete_pass": pl.Float64(),
    "pass_touchdown": pl.Float64(), "rush_touchdown": pl.Float64(),
    "passer_player_id": pl.String(), "receiver_player_id": pl.String(),
    "rusher_player_id": pl.String(), "passing_yards": pl.Float64(),
    "receiving_yards": pl.Float64(), "rushing_yards": pl.Float64(), "air_yards": pl.Float64(),
    "td_player_id": pl.String(), "td_team": pl.String(),
}  # fmt: skip
STATS_DTYPES = {
    "player_id": pl.String(), "player_name": pl.String(), "position": pl.String(),
    "season": pl.Int32(), "week": pl.Int32(), "season_type": pl.String(),
    "game_id": pl.String(), "team": pl.String(), "opponent_team": pl.String(),
    "attempts": pl.Int32(), "passing_yards": pl.Int32(), "passing_tds": pl.Int32(),
    "carries": pl.Int32(), "rushing_yards": pl.Int32(), "rushing_tds": pl.Int32(),
    "receptions": pl.Int32(), "targets": pl.Int32(), "receiving_yards": pl.Int32(),
    "receiving_tds": pl.Int32(), "fantasy_points_ppr": pl.Float64(),
}  # fmt: skip

P_QB, P_WR, P_TE, P_RB, P_DAL = (f"00-00001{n:02d}" for n in (10, 11, 12, 13, 14))


def _games_2025() -> list[dict]:
    return [
        game(2025, 1, "2025-09-07", "13:00", "KC", "LAC", result=-6),
        game(2025, 2, "2025-09-14", "13:00", "PHI", "KC", result=3),
        game(2025, 3, "2025-09-18", "20:15", "MIA", "BUF", result=10),
        # moved to a Tuesday night: public only after week 3's Tuesday as-of (a split week)
        game(2025, 3, "2025-09-23", "19:00", "SEA", "DAL", result=4),
        game(2025, 4, "2025-09-28", "13:00", "KC", "MIA", result=7),
    ]


def _play(g: dict, pid: int, kind: str, **kw) -> dict:
    return {"game_id": g["game_id"], "play_id": float(pid), "season": g["season"],
            "week": g["week"], "season_type": "REG", "game_date": g["gameday"],
            "home_team": g["home_team"], "away_team": g["away_team"], "qtr": 2.0,
            "wp": 0.5, "half_seconds_remaining": 900.0, "score_differential": 0.0,
            "two_point_attempt": 0.0, "play_type": kind, "pass_attempt": float(kind == "pass"),
            "rush_attempt": float(kind == "run"), **kw}  # fmt: skip


def _kc_game(g: dict, garbage_wp: float = 0.97) -> tuple[list, list, list]:
    """KC's plays in game ``g``: a 20-yard catch (neutral), a 30-yard touchdown catch in
    garbage time (wp 0.97 with 15 minutes left), an incompletion and a 6-yard run."""
    kc = {"posteam": "KC", "defteam": g["home_team"] if g["away_team"] == "KC" else g["away_team"]}
    plays = [
        _play(g, 1, "pass", **kc, passer_player_id=P_QB, receiver_player_id=P_WR,
              complete_pass=1.0, passing_yards=20.0, receiving_yards=20.0, air_yards=10.0),
        _play(g, 2, "pass", **kc, wp=garbage_wp, passer_player_id=P_QB,
              receiver_player_id=P_WR, complete_pass=1.0, passing_yards=30.0,
              receiving_yards=30.0, air_yards=25.0, pass_touchdown=1.0, td_player_id=P_WR,
              td_team="KC"),
        _play(g, 3, "pass", **kc, passer_player_id=P_QB, receiver_player_id=P_TE,
              complete_pass=0.0, air_yards=15.0),
        _play(g, 4, "run", **kc, rusher_player_id=P_RB, rushing_yards=6.0),
    ]  # fmt: skip
    base = {"game_id": g["game_id"], "season": g["season"], "week": g["week"],
            "posteam": "KC", "two_point_attempt": 0.0}  # fmt: skip
    passes = [
        {**base, "play_id": 1.0, "passer_player_id": P_QB, "receiver_player_id": P_WR,
         "receiver_position": "WR", "complete_pass": "1", "receiving_yards": 20.0,
         "air_yards": 10.0, "pass_completion_exp": 0.7, "yards_after_catch_exp": 5.0,
         "pass_touchdown_exp": 0.05, "pass_interception_exp": 0.02, "two_point_conv_exp": 0.0},
        {**base, "play_id": 2.0, "passer_player_id": P_QB, "receiver_player_id": P_WR,
         "receiver_position": "WR", "complete_pass": "1", "receiving_yards": 30.0,
         "air_yards": 25.0, "pass_completion_exp": 0.6, "yards_after_catch_exp": 5.0,
         "pass_touchdown_exp": 0.2, "pass_interception_exp": 0.02, "two_point_conv_exp": 0.0},
        {**base, "play_id": 3.0, "passer_player_id": P_QB, "receiver_player_id": P_TE,
         "receiver_position": "TE", "complete_pass": "0", "air_yards": 15.0,
         "pass_completion_exp": 0.5, "yards_after_catch_exp": 4.0,
         "pass_touchdown_exp": 0.1, "pass_interception_exp": 0.02, "two_point_conv_exp": 0.0},
    ]  # fmt: skip
    rushes = [
        {**base, "play_id": 4.0, "rusher_player_id": P_RB, "rushing_yards": 6.0,
         "rush_touchdown": "0", "rush_yards_exp": 4.0, "rush_touchdown_exp": 0.02,
         "two_point_conv_exp": 0.0},
    ]  # fmt: skip
    return plays, passes, rushes


def _weekly(g: dict) -> tuple[list, list]:
    """KC's weekly stat lines and ffopportunity rows in game ``g`` (the plays' sums)."""
    opponent = g["home_team"] if g["away_team"] == "KC" else g["away_team"]
    kc = {"season": g["season"], "week": g["week"], "season_type": "REG",
          "game_id": g["game_id"], "team": "KC", "opponent_team": opponent}  # fmt: skip
    stats = [
        {**kc, "player_id": P_QB, "player_name": "Q", "position": "QB", "attempts": 3,
         "passing_yards": 50, "passing_tds": 1},
        {**kc, "player_id": P_WR, "player_name": "W", "position": "WR", "targets": 2,
         "receptions": 2, "receiving_yards": 50, "receiving_tds": 1},
        {**kc, "player_id": P_TE, "player_name": "T", "position": "TE", "targets": 1},
        {**kc, "player_id": P_RB, "player_name": "R", "position": "RB", "carries": 1,
         "rushing_yards": 6},
    ]  # fmt: skip
    ex = {"season": str(g["season"]), "posteam": "KC", "week": float(g["week"]),
          "game_id": g["game_id"]}  # fmt: skip
    opp = [
        {**ex, "player_id": P_QB, "full_name": "Q", "position": "QB",
         "pass_yards_gained_exp": 34.0, "pass_touchdown_exp": 0.35,
         "pass_interception_exp": 0.06},
        {**ex, "player_id": P_WR, "full_name": "W", "position": "WR", "rec_attempt": 2.0,
         "receptions": 2.0, "receptions_exp": 1.3, "rec_yards_gained": 50.0,
         "rec_yards_gained_exp": 28.5, "rec_touchdown_exp": 0.25},
        {**ex, "player_id": P_TE, "full_name": "T", "position": "TE", "rec_attempt": 1.0,
         "receptions_exp": 0.5, "rec_yards_gained_exp": 9.5, "rec_touchdown_exp": 0.1},
        {**ex, "player_id": P_RB, "full_name": "R", "position": "RB", "rush_attempt": 1.0,
         "rush_yards_gained_exp": 4.0, "rush_touchdown_exp": 0.02},
    ]  # fmt: skip
    return stats, opp


def _roster(season: int, week: int, gsis: str, pos: str, team: str) -> dict:
    return {"season": season, "week": week, "team": team, "position": pos, "full_name": gsis,
            "gsis_id": gsis, "status": "ACT", "game_type": "REG"}  # fmt: skip


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """2024 (one KC game, for a previous-season roster) and 2025 weeks 1-4: KC's QB, WR, TE and
    RB with plays, stat lines and expected values; a DAL receiver in the Tuesday-night game."""
    from tests.conftest import RawCache
    from twm import ids
    from twm.sources import nflverse as nv

    tmp = tmp_path_factory.mktemp("regression")
    mp = pytest.MonkeyPatch()
    root = tmp / "raw"
    mp.setattr(nv, "raw_dir", lambda: root)
    mp.setattr(nv, "_loader", lambda ds: (_ for _ in ()).throw(AssertionError("download")))
    mp.setattr(nv, "_configure_nflreadpy", lambda: None)
    mp.setattr(ids, "overrides_path", lambda: tmp / "manual" / ids.OVERRIDES_FILE)
    try:
        cache = RawCache(root)
        cache.write_globals()
        g24 = [game(2024, 1, "2024-09-08", "13:00", "KC", "LAC", result=3)]
        cache.write_season(2024, g24, rosters=[_roster(2024, 1, P_RB, "RB", "KC")])
        g25 = _games_2025()
        kc = [g for g in g25 if "KC" in (g["home_team"], g["away_team"])]
        plays, passes, rushes, stats, opp = [], [], [], [], []
        for g in kc:
            p, pa, ru = _kc_game(g)
            s, o = _weekly(g)
            plays += p
            passes += pa
            rushes += ru
            stats += s
            opp += o
        dal = next(g for g in g25 if g["home_team"] == "DAL")
        plays.append(_play(dal, 1, "pass", posteam="DAL", defteam="SEA", passer_player_id=P_QB,
                           receiver_player_id=P_DAL, complete_pass=1.0, passing_yards=12.0,
                           receiving_yards=12.0, air_yards=8.0))  # fmt: skip
        stats.append({"season": 2025, "week": 3, "season_type": "REG", "game_id": dal["game_id"],
                      "team": "DAL", "opponent_team": "SEA", "player_id": P_DAL,
                      "player_name": "D", "position": "WR", "targets": 1, "receptions": 1,
                      "receiving_yards": 12})  # fmt: skip
        rosters = [_roster(2025, w, P_QB, "QB", "KC") for w in (1, 2, 3, 4)]
        rosters += [_roster(2025, w, P_WR, "WR", "KC") for w in (1, 2, 3, 4)]
        rosters += [_roster(2025, 1, P_TE, "TE", "KC")]  # later weeks: 'roster_earlier'
        rosters += [_roster(2025, 3, P_DAL, "WR", "DAL")]  # the RB: previous season only
        cache.write_season(2025, g25, rosters=rosters, opportunity=opp,
                           opportunity_pass=passes, opportunity_rush=rushes)  # fmt: skip
        cache.write("pbp", 2025, frame(plays, PBP_DTYPES))
        cache.write("player_stats", 2025, frame(stats, STATS_DTYPES))
        db = tmp / "wh.duckdb"
        wb.build_warehouse([2024, 2025], db_path=db)
    finally:
        mp.undo()
    return db


def test_per_play_tables_are_registered_game_data(world):
    for name, player_cols in (("fact_opportunity_pass", {"passer_position", "receiver_position"}),
                              ("fact_opportunity_rush", {"position"})):  # fmt: skip
        assert av.kind_of(name) == "event" and av.GAME_DATA_TABLES[name] == "pbp"
        assert set(av.TABLE_AVAILABILITY[name].hindsight_columns) == player_cols
        assert sc.tables()[name].source in sc.SOURCE_DATASETS
    con = duckdb.connect(str(world), read_only=True)
    try:
        # every row is public exactly when its play is (same game end + the pbp lag)
        wrong = con.execute("""
            SELECT count(*) FROM fact_opportunity_pass o JOIN fact_play f USING (game_id, play_id)
            WHERE o.available_at <> f.available_at OR o.is_garbage_time <> f.is_garbage_time
        """).fetchone()[0]  # fmt: skip
        assert wrong == 0
        row = con.execute("""
            SELECT play_id, complete_pass, season_type, is_garbage_time, air_yards
            FROM fact_opportunity_pass WHERE game_id = '2025_01_KC_LAC' ORDER BY play_id
        """).fetchall()  # fmt: skip
        # categorical '1'/'0' and float play ids become integers; the garbage flag is copied
        assert row == [(1, 1, "REG", False, 10), (2, 1, "REG", True, 25), (3, 0, "REG", False, 15)]
        notes = dict(con.execute(
            "SELECT table_name, notes FROM build_manifest WHERE table_name LIKE 'fact_opp%'"
        ).fetchall())  # fmt: skip
    finally:
        con.close()
    assert '"n_not_in_fact_play": 0' in notes["fact_opportunity_pass"]
    with AsOfView(world, weekly_as_of(world, 2025, 1)) as v, pytest.raises(duckdb.BinderException):
        v.sql("SELECT receiver_position FROM fact_opportunity_pass")


def test_per_play_tables_dedupe_and_flag_plays_missing_from_fact_play(raw, db_path):
    g = [game(2025, 1, "2025-09-07", "13:00", "KC", "PHI", result=3)]
    base = {"game_id": g[0]["game_id"], "season": 2025, "week": 1, "posteam": "KC",
            "passer_player_id": "00-0000001", "receiver_player_id": "00-0000009",
            "pass_completion_exp": 0.6}  # fmt: skip
    passes = [
        {**base, "play_id": 2.0, "receiver_full_name": "B Name", "receiver_position": "OLB"},
        {**base, "play_id": 2.0, "receiver_full_name": "A Name", "receiver_position": "TE"},
        {**base, "play_id": 77.0, "receiver_full_name": "A Name", "receiver_position": "TE"},
    ]  # fmt: skip
    raw.write_season(2025, g, opportunity_pass=passes)
    m = {r["table_name"]: r for r in wb.build_warehouse([2025], db_path=db_path)}
    p = m["fact_opportunity_pass"]
    assert (p["n_rows"], p["n_dropped_duplicates"]) == (2, 1)
    assert '"n_not_in_fact_play": 1' in p["notes"]
    con = duckdb.connect(str(db_path), read_only=True)
    rows = con.execute(
        "SELECT play_id, receiver_full_name, is_garbage_time FROM fact_opportunity_pass "
        "ORDER BY play_id"
    ).fetchall()
    con.close()
    # the QB/RB/WR/TE row wins; a play missing from fact_play has no garbage flag
    assert rows == [(2, "A Name", False), (77, "A Name", None)]


# --------------------------------------------------------------------------------------
# The frame
# --------------------------------------------------------------------------------------


def _row(df: pl.DataFrame, gsis: str, week: int) -> dict:
    return df.filter((pl.col("gsis_id") == gsis) & (pl.col("week") == week)).row(0, named=True)


def test_frame_points_xfp_and_garbage_time(world):
    df = pw.player_games_history(world, [2025])
    wr = _row(df, P_WR, 1)
    # weekly: 2 catches, 50 yards, 1 TD = 13; the garbage-time TD catch was 1 + 3 + 6 = 10
    assert (wr["fantasy_points"], wr["points_garbage"], wr["points_ng"]) == (13.0, 10.0, 3.0)
    assert wr["play_points"] == 13.0  # the plays reproduce the weekly line
    # weekly xFP: 1.3 + 2.85 + 1.5 = 5.65; the garbage play: 0.6 + 1.8 + 1.2 = 3.6
    assert wr["xfp"] == pytest.approx(5.65) and wr["xfp_garbage"] == pytest.approx(3.6)
    assert wr["play_xfp"] == pytest.approx(0.7 + 1.05 + 0.3 + 3.6)
    assert wr["fpoe"] == pytest.approx(13 - 5.65)
    assert wr["fpoe_ng"] == pytest.approx(3.0 - (5.65 - 3.6))
    assert (wr["n_opportunities"], wr["n_opportunities_garbage"]) == (2, 1)
    assert (wr["targets"], wr["receptions"], wr["receiving_yards_exp"]) == (2.0, 2.0, 28.5)
    assert (wr["yac"], wr["yac_exp"]) == (15.0, 10.0)
    qb = _row(df, P_QB, 1)
    assert (qb["fantasy_points"], qb["points_garbage"]) == (6.0, 5.2)  # 30 x 0.04 + 4
    te = _row(df, P_TE, 1)  # no garbage-time play: the same with and without
    assert te["points_ng"] == te["fantasy_points"] and te["xfp_ng"] == te["xfp"]


def test_frame_positions_are_point_in_time(world):
    df = pw.player_games_history(world, [2025])
    src = {(r["gsis_id"], r["week"]): (r["position"], r["position_source"])
           for r in df.iter_rows(named=True)}  # fmt: skip
    assert src[P_WR, 2] == ("WR", "roster")
    assert src[P_TE, 1] == ("TE", "roster") and src[P_TE, 4] == ("TE", "roster_earlier")
    assert src[P_RB, 1] == ("RB", "roster_previous_season")
    assert set(df["position"]) <= {"QB", "RB", "WR", "TE"}


def test_asof_frame_never_holds_a_later_game_and_joins_split_games_late(world):
    w3, w4 = (weekly_as_of(world, 2025, w).replace(tzinfo=None) for w in (3, 4))
    at_w3 = pw.player_games_asof(world, 2025, 3)
    assert at_w3["week"].max() <= 3 and (at_w3["available_at"] <= w3).all()
    assert P_DAL not in set(at_w3["gsis_id"])  # the Tuesday-night game is not public yet
    at_w4 = pw.player_games_asof(world, 2025, 4)
    assert w3 < _row(at_w4, P_DAL, 3)["available_at"] < w4  # joins between the two as-ofs
    kc4 = at_w4.filter(pl.col("week") == 4)
    assert kc4.height == 4 and (kc4["available_at"] == w4).all()  # its week's Tuesday as-of
    # the Monday-morning moment week 4's KC game is public is not enough: rows wait for the
    # week's official as-of
    with AsOfView(world, weekly_as_of(world, 2025, 4) - timedelta(hours=2)) as v:
        assert pw.player_games_for(v, 2025).filter(pl.col("week") == 4).height == 0


def test_history_equals_the_asof_frame_at_every_moment(world):
    hist = pw.player_games_history(world, [2025])
    moments = [weekly_as_of(world, 2025, w) for w in (1, 2, 3, 4)]
    moments += [moments[2] + timedelta(hours=20), moments[0] - timedelta(hours=1)]
    for t in moments:
        with AsOfView(world, t) as v:
            got = pw.player_games_for(v, 2025)
        assert_frame_equal(got, pw.visible(hist, t))


@pytest.mark.parametrize("week", [1, 3, 4])
def test_frame_passes_the_leakage_harness(world, week):
    out = assert_future_invariant(
        lambda v: pw.player_games_for(v, 2025), world, weekly_as_of(world, 2025, week),
        key=["game_id", "gsis_id"],
    )  # fmt: skip
    assert out.height > 0


def test_a_leaky_frame_fails_the_harness(world):
    def leaky(v):
        df = pw.player_games_for(v, 2025)
        future = v.sql("SELECT player_id AS gsis_id, count(*) AS n FROM wh.fact_player_week "
                       "WHERE season = 2025 GROUP BY 1")  # fmt: skip
        return df.join(future, on="gsis_id", how="left")

    with pytest.raises(LeakageError):
        assert_future_invariant(leaky, world, weekly_as_of(world, 2025, 2),
                                key=["game_id", "gsis_id"])  # fmt: skip


def test_frame_agrees_with_the_published_player_week_summary(world):
    from twm.publish.collect import player_week_summary

    con = duckdb.connect(str(world), read_only=True)
    try:
        pub = player_week_summary(con, 2025)
    finally:
        con.close()
    df = pw.player_games_history(world, [2025])
    j = df.join(pub, on=["gsis_id", "season", "week"], suffix="_pub")
    assert j.height == df.height > 0
    # the points (the xFP since step PXFP: the own xFP frame's, tests/test_player_pages_xfp.py)
    assert ((j["fantasy_points"] - j["fantasy_points_pub"]).abs() < 0.006).all()
    assert ((j["fantasy_points"] - j["points_raw"]).abs() < 1e-6).all()


def test_frame_before_2006_is_empty_and_typed(world):
    with AsOfView(world, weekly_as_of(world, 2025, 1)) as v:
        empty = pw.player_games_for(v, 2005)
    assert empty.height == 0 and empty.columns == list(pw.FRAME_COLUMNS)


# --------------------------------------------------------------------------------------
# Registry, report, CLI
# --------------------------------------------------------------------------------------

NOT_METRICS = {"season", "week", "game_id", "gsis_id", "team", "position", "position_source",
               "available_at"}  # fmt: skip


def test_every_frame_metric_is_registered():
    for col in pw.FRAME_COLUMNS:
        if col in NOT_METRICS:
            continue
        e = rg.get(col)
        assert "regression_watch" in e.modules or "shared" in e.modules, col
        assert e.status == "available", col
    for name in ("points_ng", "xfp_ng", "fpoe_ng", "xfp_garbage", "play_xfp", "yac_exp"):
        assert rg.get(name).model_output, name  # spec 6.3: model outputs flagged
    assert not rg.get("play_points").model_output
    assert set(pw.COMPONENTS) <= set(rg.REGISTRY)


def test_report_on_the_synthetic_world(world, tmp_path):
    rep = rr.build_xfp_report(world, [2025])
    assert "## 2. Points" in rep.markdown and "## 6. Limitation" in rep.markdown
    assert rep.differences.height == 0  # the synthetic plays reproduce every weekly line
    csv_path = rr.write_xfp_report(rep, tmp_path / "xfp.md")
    rows = pl.read_csv(csv_path)
    assert list(rows.columns) == list(rr.CSV_COLUMNS)
    kc_wr = rows.filter((pl.col("season") == 2025) & (pl.col("position") == "WR"))
    assert kc_wr["points_garbage"].item() == pytest.approx(30.0)  # 3 KC games x 10
    with pytest.raises(ValueError, match="2006"):
        rr.build_xfp_report(world, [2005])


def test_report_explains_a_difference(world):
    with AsOfView(world, pw.END_OF_TIME) as v:
        df = pw.player_games_for(v, 2025).with_columns(
            pl.when(pl.col("gsis_id") == P_WR).then(pl.col("fantasy_points") + 2.0)
            .otherwise(pl.col("fantasy_points")).alias("fantasy_points"),
        )  # fmt: skip
        # a weekly line that differs from the plays: the report names the stats
        diffs = rr._differences(v, df, ScoringRules.from_config())
    assert diffs.height == 3 and set(diffs["gsis_id"]) == {P_WR}
    assert set(diffs["cause"]) == {"unexplained"}  # the stats agree: a points-only change


def test_cli_player_and_report(world, tmp_path):
    runner = CliRunner()
    res = runner.invoke(app, ["regression", "player", P_WR, "--as-of", "2025-W2", "--db",
                              str(world)])  # fmt: skip
    assert res.exit_code == 0, res.output
    assert "2025 regular season" in res.output and "per game (2 games)" in res.output
    res = runner.invoke(app, ["regression", "player", "Nobody Here", "--season", "2025",
                              "--db", str(world)])  # fmt: skip
    assert res.exit_code == 1
    out = tmp_path / "rw" / "xfp.md"
    res = runner.invoke(app, ["regression", "xfp-report", "--db", str(world), "--start", "2025",
                              "--end", "2025", "--out", str(out)])  # fmt: skip
    assert res.exit_code == 0, res.output
    assert out.exists() and out.with_suffix(".csv").exists()


def test_frame_uses_the_as_of_view_only(world):
    with AsOfView(world, weekly_as_of(world, 2025, 2)) as v:
        pw.player_games_for(v, 2025)
        used = v.tables_used
    assert {"fact_player_week", "fact_opportunity_week", "fact_play", "fact_opportunity_pass",
            "fact_opportunity_rush", "fact_roster_week", "fact_snaps", "fact_game",
            "dim_week"} <= used  # fmt: skip
    sql = pw.frame_sql([2025], datetime(2025, 9, 16, 14), ScoringRules.from_config(),
                       timedelta(hours=6))  # fmt: skip
    assert "wh." not in sql


# --------------------------------------------------------------------------------------
# Real data (opt-in: uv run pytest -m realdata)
# --------------------------------------------------------------------------------------


@pytest.mark.realdata
def test_real_reconciliations(real_full_db):
    path, manifest = real_full_db
    for name in ("fact_opportunity_pass", "fact_opportunity_rush"):
        m = manifest[name]
        import json

        notes = json.loads(m["notes"])
        assert m["n_rows"] > 250_000
        assert notes["n_not_in_fact_play"] <= 0.0001 * m["n_rows"]  # the keys join fact_play
    rep = rr.build_xfp_report(path, range(2006, 2027))
    df = pw.player_games_history(path, list(range(2006, 2027)))
    n = df.height
    gap = (df["fantasy_points"] - df["play_points"]).abs()
    assert n > 100_000 and (gap <= rr.POINTS_BOUND + 1e-9).sum() / n >= 0.999
    with_x = df.filter(pl.col("xfp").is_not_null())
    dx = (with_x["xfp"] - with_x["play_xfp"]).abs()
    assert (dx <= rr.xfp_bound(ScoringRules.from_config()) + 1e-6).sum() / with_x.height >= 0.9999
    # every remaining points difference has a named cause
    assert "unexplained" not in set(rep.differences["cause"])
    # garbage time: a steady 10-25% of points and xFP at every position
    for pos in ("QB", "RB", "WR", "TE"):
        sub = df.filter(pl.col("position") == pos)
        share = sub["points_garbage"].sum() / sub["fantasy_points"].sum()
        xshare = sub["xfp_garbage"].sum() / sub["xfp"].sum()
        assert 0.10 < share < 0.25 and 0.10 < xshare < 0.25, pos


@pytest.mark.realdata
@pytest.mark.parametrize(("season", "week"), [(2015, 1), (2020, 12), (2024, 5)])
def test_real_history_equals_the_asof_frame(real_full_db, season, week):
    """A post-game roster season's week 1 (2015), a split week (2020 W12: a Wednesday game)
    and a game-day season (2024)."""
    path, _ = real_full_db
    hist = pw.player_games_history(path, [season])
    t = weekly_as_of(path, season, week)
    with AsOfView(path, t) as v:
        got = pw.player_games_for(v, season)
    assert got.height > 0
    assert_frame_equal(got, pw.visible(hist, t))


@pytest.mark.realdata
def test_real_frame_passes_the_leakage_harness(real_full_db):
    path, _ = real_full_db
    t = weekly_as_of(path, 2020, 12)  # a split week
    out = assert_future_invariant(lambda v: pw.player_games_for(v, 2020), path, t,
                                  key=["game_id", "gsis_id"])  # fmt: skip
    assert out.height > 1000 and out["week"].max() == 12


@pytest.mark.realdata
def test_real_rows_are_public_only_after_every_input(real_full_db):
    """No input of a row (stat line, expected points, plays) is public after the row."""
    path, _ = real_full_db
    df = pw.player_games_history(path, [2020, 2025]).select("game_id", "gsis_id", "available_at")
    con = duckdb.connect(str(path), read_only=True)
    try:
        inputs = con.execute("""
            SELECT game_id, player_id AS gsis_id, max(available_at) AS t FROM (
                SELECT game_id, player_id, available_at FROM fact_player_week
                UNION ALL SELECT game_id, player_id, available_at FROM fact_opportunity_week
                UNION ALL SELECT game_id, receiver_player_id, available_at
                          FROM fact_opportunity_pass
                UNION ALL SELECT game_id, rusher_player_id, available_at
                          FROM fact_opportunity_rush
            ) GROUP BY 1, 2""").pl()  # fmt: skip
    finally:
        con.close()
    j = df.join(inputs, on=["game_id", "gsis_id"])
    assert j.height > 10_000 and (j["t"] <= j["available_at"]).all()
    assert (j["available_at"] >= datetime(2020, 1, 1, tzinfo=UTC).replace(tzinfo=None)).all()
