"""K and D/ST warehouse tables (S1): fact_kicker_week and fact_defense_week on a synthetic
cache (touchdown classification, ESPN points allowed, availability, leakage), plus opt-in
real-data checks: the hand-scored games, the kicker reconciliation and the 2012-2026 build."""

from __future__ import annotations

from datetime import timedelta

import duckdb
import polars as pl
import pytest

from tests.conftest import (
    PBP_DTYPES,
    PLAYER_STATS_DTYPES,
    TEAM_STATS_DTYPES,
    frame,
    plays_for,
    season_2025_games,
)
from twm.asof import AsOfView, weekly_as_of
from twm.backtest.leakage import LeakageError, assert_future_invariant
from twm.scoring import nflverse_standard, score_sql
from twm.scoring_kdst import score_defense_sql, score_kicking_sql
from twm.warehouse import available as av
from twm.warehouse import build as wb

PBP_EXTRA = {
    "play_type": pl.String(), "touchdown": pl.Float64(), "td_team": pl.String(),
    "interception": pl.Float64(), "kickoff_attempt": pl.Float64(),
    "punt_attempt": pl.Float64(), "field_goal_attempt": pl.Float64(),
    "field_goal_result": pl.String(), "extra_point_attempt": pl.Float64(),
    "two_point_attempt": pl.Float64(),
}  # fmt: skip
KICK_COLS = ("fg_att", "fg_made", "fg_missed", "fg_blocked", "fg_made_40_49", "fg_made_50_59",
             "fg_missed_30_39", "pat_att", "pat_made")  # fmt: skip
DEF_COLS = ("def_interceptions", "fumble_recovery_opp", "def_safeties", "def_punt_blocks",
            "def_fg_blocks", "def_pat_blocks", "passing_yards", "sack_yards_lost",
            "rushing_yards")  # fmt: skip
G1 = "2025_01_DAL_PHI"  # PHI 24, DAL 20 (conftest: home 20 + result 4)


def td_play(play_id: int, posteam: str, defteam: str, td_team: str, play_type: str, **kw):
    base = {"game_id": G1, "play_id": float(play_id), "season": 2025, "week": 1,
            "season_type": "REG", "game_date": "2025-09-04", "posteam": posteam,
            "defteam": defteam, "home_team": "PHI", "away_team": "DAL", "qtr": 2.0,
            "desc": kw.pop("desc", "TOUCHDOWN"), "play_type": play_type, "touchdown": 1.0,
            "td_team": td_team, "wp": 0.5, "half_seconds_remaining": 900.0,
            "score_differential": 0.0}  # fmt: skip
    return {**base, **kw}


G1_TD_PLAYS = [
    # PHI's offense throws a pick-six: a DAL D/ST TD, taken out of PHI's points allowed
    td_play(10, "PHI", "DAL", "DAL", "pass", interception=1.0),
    # kickoff: posteam is the RECEIVING team (nflfastR); PHI returns it
    td_play(11, "PHI", "DAL", "PHI", "kickoff", kickoff_attempt=1.0),
    # DAL punts, PHI blocks it and scores
    td_play(
        12,
        "DAL",
        "PHI",
        "PHI",
        "punt",
        punt_attempt=1.0,
        desc="Punt is BLOCKED by X, RECOVERED by PHI, TOUCHDOWN",
    ),  # fmt: skip
    td_play(13, "PHI", "DAL", "PHI", "run"),  # an offensive TD: not D/ST
]


def kicker_row(g: dict, player_id: str, team_home: bool = True, **stats: int) -> dict:
    team, opp = (g["home_team"], g["away_team"]) if team_home else (g["away_team"], g["home_team"])
    return {"player_id": player_id, "player_name": player_id, "position": "K",
            "season": g["season"], "week": g["week"], "season_type": "REG",
            "game_id": g["game_id"], "team": team, "opponent_team": opp, "receptions": 0,
            **stats}  # fmt: skip


@pytest.fixture
def kdst_db(raw, db_path):
    games = season_2025_games()
    by_id = {g["game_id"]: g for g in games}
    g2 = by_id["2025_02_PHI_KC"]
    # PHI kicker: 44-yd FG, a 35-yd miss, 3 PATs; KC kicker in week 2: 52-yd FG; a WR: no kicks
    stats = [
        kicker_row(by_id[G1], "k_phi", fg_att=2, fg_made=1, fg_missed=1, fg_made_40_49=1,
                   fg_missed_30_39=1, pat_att=3, pat_made=3),
        kicker_row(g2, "k_kc", fg_att=1, fg_made=1, fg_made_50_59=1),
        {**kicker_row(by_id[G1], "wr_phi"), "position": "WR", "receptions": 5},
    ]  # fmt: skip
    team_rows = [
        {"season": 2025, "week": 1, "season_type": "REG", "game_id": G1, "team": "PHI",
         "opponent_team": "DAL", "def_sacks": 2.5, "def_interceptions": 0,
         "def_punt_blocks": 1, "passing_yards": 250, "sack_yards_lost": -10,
         "rushing_yards": 100},
        {"season": 2025, "week": 1, "season_type": "REG", "game_id": G1, "team": "DAL",
         "opponent_team": "PHI", "def_sacks": 1.0, "def_interceptions": 1,
         "passing_yards": 180, "sack_yards_lost": -15, "rushing_yards": 60},
        {"season": 2025, "week": 2, "season_type": "REG", "game_id": g2["game_id"],
         "team": "KC", "opponent_team": "PHI", "def_sacks": 3.0},
    ]  # fmt: skip
    kick_dtypes = {**PLAYER_STATS_DTYPES, **dict.fromkeys(KICK_COLS, pl.Int32())}
    team_dtypes = {**TEAM_STATS_DTYPES, "def_sacks": pl.Float64(),
                   **dict.fromkeys(DEF_COLS, pl.Int32())}  # fmt: skip
    raw.write_season(2025, games)
    raw.write("player_stats", 2025, frame(stats, kick_dtypes))
    raw.write("team_stats", 2025, frame(team_rows, team_dtypes))
    raw.write("pbp", 2025, frame(plays_for(games) + G1_TD_PLAYS, {**PBP_DTYPES, **PBP_EXTRA}))
    wb.build_warehouse([2025], db_path=db_path)
    return db_path


def rows(db, sql: str) -> list[tuple]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_kicker_rows_are_player_games_with_an_attempt(kdst_db):
    got = rows(kdst_db, "SELECT player_id, week, team, fg_att, pat_made, "
               f"{score_kicking_sql()} FROM fact_kicker_week ORDER BY week")  # fmt: skip
    # PHI: 44 yd (4) - miss (1) + 3 PAT (3) = 6; KC: 52 yd = 5; the WR has no row
    assert got == [("k_phi", 1, "PHI", 2, 3, 6.0), ("k_kc", 2, "KC", 1, None, 5.0)]


def test_defense_rows_classify_touchdowns_and_points_allowed_the_espn_way(kdst_db):
    got = rows(kdst_db, f"""
        SELECT team, def_sacks, kickoff_return_tds, punt_return_tds, interception_return_tds,
               fumble_return_tds, blocked_kick_return_tds, points_scored_against,
               offense_giveaway_tds, points_allowed, yards_allowed, {score_defense_sql()}
        FROM fact_defense_week ORDER BY week, team""")  # fmt: skip
    assert got == [
        # 1 sack + INT (2) + pick-six (6); PHI scored 24 -> 0; yards: 250 - 10 + 100 = 340
        # -> 0 (the league's yards tiers, C1: 300-349 = 0)
        ("DAL", 1.0, 0, 0, 1, 0, 0, 24, 0, 24, 340, 9.0),
        # 2.5 sacks + blocked punt (2) + KR TD (6) + blocked-punt TD (6); DAL scored 20, 6 of
        # them on PHI's pick-six: 14 allowed -> 1; 225 yards allowed -> 2 (200-299)
        ("PHI", 2.5, 1, 0, 0, 0, 1, 20, 1, 14, 225, 19.5),
        # no opponent team-stats row: yards_allowed is NULL (no yards tier scored)
        ("KC", 3.0, 0, 0, 0, 0, 0, 20, 0, 20, None, 3.0),
    ]


def test_new_tables_become_public_with_their_game_data(kdst_db):
    lag = max(av.AvailabilityRules().game_data_lag[d] for d in ("team_stats", "pbp"))
    assert lag == timedelta(hours=6)
    for table in ("fact_kicker_week", "fact_defense_week"):
        assert av.kind_of(table) == "event"
        n, wrong = rows(kdst_db, f"""
            SELECT count(*), count(*) FILTER (WHERE x.available_at
                <> g.availability_game_end_utc + INTERVAL 6 HOUR)
            FROM {table} x JOIN fact_game g USING (game_id)""")[0]  # fmt: skip
        assert n > 0 and wrong == 0, table


def test_defense_rows_wait_for_the_final_score():
    """With a result lag longer than the data lags, a D/ST row (points allowed come from the
    final score) waits for the result; a kicker row does not need it."""
    rules = av.AvailabilityRules(game_result_lag=timedelta(hours=8))
    end = "SELECT TIMESTAMP '2025-09-08 04:00:00' AS availability_game_end_utc"
    got = {
        t: duckdb.sql(f"SELECT {av.available_at_sql(t, rules).expr} FROM ({end}) _g").fetchone()[0]
        for t in ("fact_defense_week", "fact_kicker_week")
    }
    assert got["fact_defense_week"].hour == 12  # end + 8 h (result), not + 6 h (data)
    assert got["fact_kicker_week"].hour == 10


def dst_to_date(v: AsOfView) -> pl.DataFrame:
    return v.sql(
        f"SELECT team, sum({score_defense_sql()}) AS pts, count(*) AS games "
        "FROM fact_defense_week WHERE season = 2025 GROUP BY team"
    )


def test_asof_view_hides_later_weeks_and_the_harness_passes(kdst_db):
    asof = weekly_as_of(kdst_db, 2025, 1)
    out = assert_future_invariant(dst_to_date, kdst_db, asof, key=["team"])
    assert sorted(out.rows()) == [("DAL", 9.0, 1), ("PHI", 19.5, 1)]  # KC's week 2 is hidden
    with AsOfView(kdst_db, asof) as v:
        assert v.sql("SELECT player_id FROM fact_kicker_week")["player_id"].to_list() == ["k_phi"]


def test_reading_the_raw_tables_is_caught_as_a_leak(kdst_db):
    def leaky(v: AsOfView) -> pl.DataFrame:
        return v.sql(
            f"SELECT team, sum({score_defense_sql()}) AS pts FROM wh.fact_defense_week "
            "WHERE season = 2025 GROUP BY team"
        )

    with pytest.raises(LeakageError):
        assert_future_invariant(leaky, kdst_db, weekly_as_of(kdst_db, 2025, 1), key=["team"])


# ---- real data (opt-in: uv run pytest -m realdata) ------------------------------------------

# D/ST games scored by hand from the play descriptions (fact_play."desc"), ESPN defaults; the
# arithmetic is in the comments (S1 reconciliation, also in docs/warehouse.md).
HAND_SCORED = {
    # 7 sacks 7 + 2 INT 4 + 1 FR 2 + blocked FG 2 + its return TD 6 + Bland pick-six 6 + 0 allowed 5
    ("2023_01_DAL_NYG", "DAL"): (0, 32.0),
    # sacks 2 + INT 2 + FR 2; PIT 26 - 2 giveaway TDs (Highsmith pick-six, Watt fumble
    # return) = 14 allowed -> 1
    ("2023_02_CLE_PIT", "CLE"): (14, 7.0),
    # sacks 2 + Wallace punt return TD 6; LA 31 (a safety on BAL's offense counts) -> -1
    ("2023_14_LA_BAL", "BAL"): (31, 7.0),
    # sack 1 + muffed punt recovered 2; IND 27 - K.Moore's two pick-sixes = 15 -> 1
    ("2023_09_IND_CAR", "CAR"): (15, 4.0),
}


@pytest.mark.realdata
def test_hand_scored_dst_games(real_full_db):
    """Hand-scored without yards allowed (S1); since C1 the config adds the owner's league's
    yards tiers on top, which must add exactly the tier of the game's yards allowed."""
    from dataclasses import replace

    from twm.scoring_kdst import DefenseRules, tier_points

    path, _ = real_full_db
    rules = DefenseRules.from_config()
    no_yards = score_defense_sql(replace(rules, yards_allowed_tiers=()))
    for (game, team), (allowed, points) in HAND_SCORED.items():
        got = rows(path, f"SELECT points_allowed, {no_yards}, yards_allowed, "
                   f"{score_defense_sql()} FROM fact_defense_week "
                   f"WHERE game_id = '{game}' AND team = '{team}'")  # fmt: skip
        assert got[0][:2] == (allowed, points), (game, team)
        yards, full = got[0][2:]
        assert full == points + tier_points(yards, rules.yards_allowed_tiers), (game, team)


@pytest.mark.realdata
def test_kicker_points_reconcile_with_team_kicking(real_full_db):
    """No upstream kicker fantasy points exist (nflverse's fantasy_points are 0 on kicker rows),
    so the check is internal: a team-game's kicker points = the same rules on the team's own
    kicking columns (fact_team_week), and nflverse's attempt identities hold."""
    path, _ = real_full_db
    k, t = score_kicking_sql(table_alias="k"), score_kicking_sql(table_alias="t")
    n, eq = rows(path, f"""
        WITH kk AS (SELECT game_id, team, sum({k}) AS pts FROM fact_kicker_week k GROUP BY ALL)
        SELECT count(*), count(*) FILTER (WHERE abs(COALESCE(kk.pts, 0) - {t}) < 1e-9)
        FROM fact_team_week t LEFT JOIN kk USING (game_id, team)""")[0]  # fmt: skip
    assert n > 14_000 and eq == n
    bad = rows(path, """SELECT count(*) FROM fact_kicker_week
        WHERE fg_att <> fg_made + fg_missed + fg_blocked OR pat_att <> pat_made + pat_missed
        + pat_blocked OR fg_made <> fg_made_0_19 + fg_made_20_29 + fg_made_30_39
        + fg_made_40_49 + fg_made_50_59 + fg_made_60_""")[0][0]  # fmt: skip
    assert bad == 0
    # nothing upstream to reconcile against: nflverse's fantasy_points on every kicker row are
    # exactly its non-kicking stats scored (39 rows are non-zero: kickers or punters who ran,
    # passed or caught), i.e. kicking is not scored upstream
    upstream = score_sql(nflverse_standard(), table_alias="p")
    n_rows, n_equal = rows(path, f"""SELECT count(*),
        count(*) FILTER (WHERE abs(p.fantasy_points - {upstream}) < 1e-6)
        FROM fact_kicker_week k JOIN fact_player_week p
        USING (player_id, season, week, season_type)""")[0]  # fmt: skip
    assert n_rows > 14_000 and n_equal == n_rows


@pytest.mark.realdata
def test_dst_rows_cover_every_team_game_and_agree_with_team_stats(real_full_db):
    path, _ = real_full_db
    n_team, n_def, n_null, n_neg = rows(path, """
        SELECT (SELECT count(*) FROM fact_team_week), count(*),
               count(*) FILTER (WHERE points_scored_against IS NULL OR points_allowed IS NULL),
               count(*) FILTER (WHERE points_allowed < 0)
        FROM fact_defense_week""")[0]  # fmt: skip
    assert n_team == n_def > 14_000 and n_null == 0 and n_neg == 0
    # nflverse's special_teams_tds = our kickoff + punt + blocked-kick return TDs (100% of
    # 2021-2023 team-games when checked in S1; allow a sliver for older seasons)
    n, eq = rows(path, """
        SELECT count(*), count(*) FILTER (WHERE t.special_teams_tds
            = d.kickoff_return_tds + d.punt_return_tds + d.blocked_kick_return_tds)
        FROM fact_team_week t JOIN fact_defense_week d USING (team, season, week, season_type)
        """)[0]  # fmt: skip
    assert eq / n > 0.99
    # a punting team only scores on a punt play after the receiving team loses the ball (a fake
    # punt is a run or pass play upstream), so "punting-team TD on a punt" = a fumble return
    # TD. One upstream exception: in 2002_05_PHI_JAX the description says JAX's B.Shaw
    # returned a punt 69 yards, but td_team (and nflverse's team stats) credit PHI; we follow
    # the data.
    fakes = rows(path, """SELECT DISTINCT game_id FROM fact_play WHERE touchdown = 1
        AND (punt_attempt = 1 OR play_type = 'punt') AND td_team = posteam
        AND COALESCE(fumble_lost, 0) = 0""")  # fmt: skip
    assert fakes == [("2002_05_PHI_JAX",)]


@pytest.mark.realdata
def test_github_range_2012_2026_builds(tmp_path, monkeypatch):
    """The GitHub pipeline builds 2012-2026 only: the new tables must build there too."""
    from twm.sources import nflverse as nv

    seasons = list(range(2012, 2027))
    if not all(nv.cache_path("schedules", s).exists() for s in seasons):
        pytest.skip("real cache not present (run `twm ingest`)")

    def _no_download(ds):
        pytest.fail(f"realdata test tried to download {ds.name}")

    monkeypatch.setattr(nv, "_loader", _no_download)
    path = tmp_path / "gh.duckdb"
    manifest = {m["table_name"]: m for m in wb.build_warehouse(seasons, db_path=path)}
    for table in ("fact_kicker_week", "fact_defense_week"):
        assert manifest[table]["n_rows"] > 7_000, table
        got = rows(path, f"SELECT min(season), max(season) FROM {table}")
        assert got == [(2012, 2026)], table
