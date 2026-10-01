"""Hot-Seat H3a features (twm.modules.hot_seat.features): one unit test per feature on synthetic
input frames, then the point-in-time checks on a synthetic warehouse (the leakage harness, the
reference path = the batch path, a future firing leaves earlier rows unchanged)."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from twm.modules.hot_seat import features as hf

R = hf.HotSeatRules()
TEAMS = pl.DataFrame({"team": ["AAA", "BBB", "CCC", "DDD"], "division": ["D1"] * 4})
GAME_SCHEMA = {
    "game_id": pl.String, "season": pl.Int32, "week": pl.Int32, "season_type": pl.String,
    "game_type": pl.String, "home_team": pl.String, "away_team": pl.String,
    "home_score": pl.Int32, "away_score": pl.Int32, "result": pl.Int32,
    "spread_line": pl.Float64, "home_moneyline": pl.Int32, "away_moneyline": pl.Int32,
    "home_qb_id": pl.String, "away_qb_id": pl.String, "available_at": pl.Datetime("us"),
}  # fmt: skip
TS = pl.Datetime("us")


def avail(season: int, week: int) -> datetime:
    """When week ``week``'s game of ``season`` is public (naive UTC, like the warehouse)."""
    return datetime(season, 9, 12, 3) + timedelta(days=7 * (week - 1))


def as_of(season: int, week: int) -> datetime:
    """The Tuesday as-of after week ``week``."""
    return datetime(season, 9, 13, 14, tzinfo=UTC) + timedelta(days=7 * (week - 1))


def g(season, week, away, home, a, h, *, ml=None, spread=0.0, qb=("QA", "QH"), gt="REG"):
    return {
        "game_id": f"{season}_{week:02d}_{away}_{home}", "season": season, "week": week,
        "season_type": "REG" if gt == "REG" else "POST", "game_type": gt, "home_team": home,
        "away_team": away, "home_score": h, "away_score": a, "result": h - a,
        "spread_line": spread, "home_moneyline": ml[0] if ml else None,
        "away_moneyline": ml[1] if ml else None, "home_qb_id": qb[1], "away_qb_id": qb[0],
        "available_at": avail(season, week),
    }  # fmt: skip


def inputs(games, *, coach=None, plays=(), rosters=(), players=(), grades=None, season=2011):
    """SeasonInputs of ``season`` from game dicts; ``coach(team, season, week)`` names each
    team-game's coach (default ``<team>_coach``); the schedule = the season's REG games."""
    coach = coach or (lambda team, s, w: f"{team.lower()}_coach")
    gdf = pl.DataFrame(games, schema=GAME_SCHEMA)
    crows = [
        {"game_id": r["game_id"], "season": r["season"], "week": r["week"],
         "season_type": r["season_type"], "team": r[f"{side}_team"],
         "coach_id": coach(r[f"{side}_team"], r["season"], r["week"]),
         "available_at": r["available_at"] - timedelta(hours=7)}
        for r in games for side in ("home", "away")
    ]  # fmt: skip
    cschema = {"game_id": pl.String, "season": pl.Int32, "week": pl.Int32,
               "season_type": pl.String, "team": pl.String, "coach_id": pl.String,
               "available_at": TS}  # fmt: skip
    sched = gdf.filter((pl.col("season") == season) & (pl.col("season_type") == "REG")).select(
        "game_id", "week", "home_team", "away_team",
        pl.lit(datetime(season, 5, 20)).cast(TS).alias("available_at"),
    )  # fmt: skip
    pschema = {"game_id": pl.String, "side": pl.String, "team": pl.String,
               "epa_sum": pl.Float64, "n_plays": pl.Int64, "available_at": TS}  # fmt: skip
    rschema = {"week": pl.Int32, "team": pl.String, "gsis_id": pl.String,
               "status": pl.String, "available_at": TS}  # fmt: skip
    frames = {
        "games": gdf, "coaches": pl.DataFrame(crows, schema=cschema), "schedule": sched,
        "plays": pl.DataFrame(list(plays), schema=pschema),
        "rosters": pl.DataFrame(list(rosters), schema=rschema),
        "players": pl.DataFrame({"gsis_id": list(players)}, schema={"gsis_id": pl.String}),
        "teams": TEAMS,
    }  # fmt: skip
    return hf.SeasonInputs(season, frames, batch=False, grades=grades)


def feats(inp, week, *, snapshot="weekly", when=None, cancelled=()):
    when = when or as_of(inp.season, week)
    return hf.compute_features(
        inp.visible(when), snapshot=snapshot, week=week, as_of=when, is_last_reg_week=False,
        rules=R, cancelled=cancelled,
    )  # fmt: skip


def row(df: pl.DataFrame, team: str) -> dict:
    return df.filter(pl.col("team") == team).row(0, named=True)


def weeks(season, n, score=lambda w: (20, 10), **kw):
    """Weeks 1..n of ``season``: BBB at AAA and DDD at CCC, scores (away, home) by week."""
    out = []
    for w in range(1, n + 1):
        a, h = score(w)
        out += [g(season, w, "BBB", "AAA", a, h, **kw), g(season, w, "DDD", "CCC", h, a, **kw)]
    return out


# --------------------------------------------------------------------------------------
# Performance vs expectation
# --------------------------------------------------------------------------------------


def test_moneylines_are_de_vigged_and_wins_compared_with_them():
    # AAA (home, -150) beats BBB (+130): decimal 1.667 and 2.3 -> p_home = 0.6 / (0.6 + 1/2.3)
    inp = inputs([g(2011, 1, "BBB", "AAA", 10, 20, ml=(-150, 130))])
    a, b = row(feats(inp, 1), "AAA"), row(feats(inp, 1), "BBB")
    p = 0.6 / (0.6 + 1 / 2.3)
    assert a["expected_wins"] == pytest.approx(p) and b["expected_wins"] == pytest.approx(1 - p)
    assert a["wins_vs_expected"] == pytest.approx(1 - p)
    assert b["wins_vs_expected"] == pytest.approx(-(1 - p))
    assert (a["market_games_moneyline"], a["market_games_spread"]) == (1, 0)


def test_spread_fallback_is_fit_on_earlier_seasons_only():
    past = [g(2010, w, "BBB", "AAA", 10 if w % 3 else 30, 20, spread=3.0) for w in range(1, 10)]
    past += [g(2010, w, "DDD", "CCC", 20, 10, spread=-4.0) for w in range(1, 4)]
    now = [g(2011, 1, "BBB", "AAA", 13, 20, spread=3.0), g(2011, 1, "DDD", "CCC", 20, 17)]
    k = hf.fit_spread_slope(pl.DataFrame(past, schema=GAME_SCHEMA), 2011)
    out = feats(inputs(past + now), 1)
    a = row(out, "AAA")
    assert a["spread_slope"] == pytest.approx(k) and k > 0
    assert a["expected_wins"] == pytest.approx(1 / (1 + math.exp(-3 * k)))
    assert (a["market_games_moneyline"], a["market_games_spread"]) == (0, 1)
    assert row(out, "CCC")["expected_wins"] == pytest.approx(0.5)  # spread 0: a pick'em
    # a later season's games never move the slope
    later = past + [g(2012, 1, "BBB", "AAA", 0, 50, spread=-20.0)]
    assert hf.fit_spread_slope(pl.DataFrame(later, schema=GAME_SCHEMA), 2011) == k


def test_no_probability_without_moneylines_or_an_earlier_season():
    out = feats(inputs([g(2011, 1, "BBB", "AAA", 10, 20)]), 1)
    assert row(out, "AAA")["expected_wins"] is None and row(out, "AAA")["spread_slope"] is None
    assert row(out, "AAA")["wins_vs_expected"] is None


def test_point_differential_and_pythagorean_wins():
    games = [g(2011, 1, "BBB", "AAA", 10, 20), g(2011, 2, "AAA", "BBB", 10, 10)]
    a = row(feats(inputs(games), 2), "AAA")
    assert a["reg_games_played"] == 2 and a["reg_wins"] == 1.5  # a tie is half a win
    assert a["point_diff_per_game"] == pytest.approx(5.0)
    pyth = 2 * 30**2.37 / (30**2.37 + 20**2.37)
    assert a["pythagorean_wins"] == pytest.approx(pyth)
    assert a["pythag_minus_wins"] == pytest.approx(pyth - 1.5)


# --------------------------------------------------------------------------------------
# Underlying quality
# --------------------------------------------------------------------------------------


def _plays(games, epa_of):
    """One offense and one defense row per team-game: ``epa_of(team, week)`` = (sum, plays)."""
    out = []
    for r in games:
        for me, them in (("home", "away"), ("away", "home")):
            team, opp = r[f"{me}_team"], r[f"{them}_team"]
            s, n = epa_of(team, r["week"])
            out.append({"game_id": r["game_id"], "side": "off", "team": team, "epa_sum": s,
                        "n_plays": n, "available_at": r["available_at"]})  # fmt: skip
            s2, n2 = epa_of(opp, r["week"])
            out.append({"game_id": r["game_id"], "side": "def", "team": team, "epa_sum": s2,
                        "n_plays": n2, "available_at": r["available_at"]})  # fmt: skip
    return out


def test_neutral_epa_is_play_weighted_and_the_trend_needs_more_games_than_the_window():
    games = weeks(2011, 6)
    # AAA's offense: weeks 1-2 = -10 over 20 plays each, weeks 3-6 = +5 over 10 plays each
    epa = {w: ((-10.0, 20) if w <= 2 else (5.0, 10)) for w in range(1, 7)}
    plays = _plays(games, lambda t, w: epa[w] if t == "AAA" else (1.0, 10))
    inp = inputs(games, plays=plays)
    a6 = row(feats(inp, 6), "AAA")
    std = (-20.0 + 20.0) / (40 + 40)
    assert a6["off_epa_neutral"] == pytest.approx(std)
    assert a6["off_epa_neutral_trend"] == pytest.approx(20.0 / 40 - std)
    assert row(feats(inp, 6), "BBB")["def_epa_neutral"] == pytest.approx(std)  # what AAA scored
    assert row(feats(inp, 6), "BBB")["off_epa_neutral_trend"] == pytest.approx(0.0)
    a4 = row(feats(inp, 4), "AAA")  # 4 games: the last 4 ARE the season, no trend yet
    assert a4["off_epa_neutral"] == pytest.approx(-10.0 / 60)
    assert a4["off_epa_neutral_trend"] is None


# --------------------------------------------------------------------------------------
# Context
# --------------------------------------------------------------------------------------


def test_tenure_flags_censoring_and_consecutive_losing_seasons():
    def coach(team, s, w):
        if team == "AAA":
            return "ann"  # 2008-2011: AAA's coach all along (2008 = first season in the data)
        if team == "CCC":
            return "cal" if s == 2011 else "old"  # a new hire in 2011
        if team == "BBB":
            return "bob" if s != 2009 else "gap"  # bob, away for 2009, back in 2010
        return "dan"

    lose = lambda w: (20, 10)  # noqa: E731 - BBB beats AAA, CCC beats DDD every week
    games = [*weeks(2008, 2, lose), *weeks(2009, 2, lose), *weeks(2010, 2, lose)]
    games += [g(2010, 3, "AAA", "BBB", 30, 0), *weeks(2011, 2, lose)]  # AAA 1-2 in 2010
    out = feats(inputs(games, coach=coach), 2)
    a, b, c = row(out, "AAA"), row(out, "BBB"), row(out, "CCC")
    assert (a["tenure_seasons"], a["tenure_censored"], a["is_first_year_coach"]) == (4, True, False)
    assert a["consecutive_losing_seasons"] == 3  # 2010 (1-2), 2009, 2008
    assert (b["tenure_seasons"], b["is_second_year_coach"], b["tenure_censored"]) == (
        2,
        True,
        False,
    )
    assert b["consecutive_losing_seasons"] == 0  # 2010: 2-1
    assert (c["tenure_seasons"], c["is_first_year_coach"], c["consecutive_losing_seasons"]) == (
        1, True, 0,
    )  # fmt: skip


def test_previous_season_wins_and_playoff_round():
    games = weeks(2010, 2)  # AAA 0-2, BBB 2-0, CCC 2-0, DDD 0-2
    games += [
        g(2010, 19, "DDD", "BBB", 10, 20, gt="WC"), g(2010, 20, "BBB", "CCC", 30, 20, gt="DIV"),
        g(2010, 22, "BBB", "AAA", 10, 3, gt="SB"),  # BBB wins the Super Bowl (a made-up bracket)
    ]  # fmt: skip
    games += weeks(2011, 1)
    out = feats(inputs(games), 1)
    got = {t: (row(out, t)["prev_season_wins"], row(out, t)["prev_playoff_round"]) for t in
           ("AAA", "BBB", "CCC", "DDD")}  # fmt: skip
    assert got == {"AAA": (0.0, 4), "BBB": (2.0, 5), "CCC": (2.0, 2), "DDD": (0.0, 1)}
    assert row(out, "AAA")["prev_season_wins"] == 0.0
    assert row(out, "BBB")["prev_playoff_result"] == "won_sb"
    assert row(out, "CCC")["prev_playoff_result"] == "lost_div"


def test_division_rank_by_win_share_ties_share_the_better_rank():
    games = [
        g(2011, 1, "BBB", "AAA", 10, 20),
        g(2011, 1, "DDD", "CCC", 10, 20),
        g(2011, 2, "CCC", "BBB", 10, 10),
    ]  # AAA 1-0 (bye wk 2), CCC 1-0-1, BBB 0-1-1
    out = feats(inputs(games), 2)  # fmt: skip
    ranks = {t: row(out, t)["division_rank"] for t in ("AAA", "BBB", "CCC", "DDD")}
    assert ranks == {"AAA": 1, "CCC": 2, "BBB": 3, "DDD": 4}
    tie = feats(inputs([g(2011, 1, "BBB", "AAA", 10, 20), g(2011, 1, "DDD", "CCC", 10, 20)]), 1)
    assert [row(tie, t)["division_rank"] for t in ("AAA", "CCC", "BBB", "DDD")] == [1, 1, 3, 3]


def test_games_remaining_uses_the_season_length_and_drops_a_cancelled_game_once_gone():
    games = weeks(2011, 4)
    inp = inputs(games)
    assert row(feats(inp, 2), "AAA")["games_remaining"] == 2
    # a moved game hidden from the schedule until its kickoff does not shorten the season
    hidden = inp.frames["schedule"].with_columns(
        pl.when(pl.col("game_id") == "2011_04_BBB_AAA")
        .then(pl.lit(datetime(2011, 12, 1)).cast(TS))
        .otherwise(pl.col("available_at"))
        .alias("available_at")
    )
    inp2 = hf.SeasonInputs(2011, {**inp.frames, "schedule": hidden}, batch=False)
    assert row(feats(inp2, 2), "AAA")["games_remaining"] == 2
    gone = [("2011_04_BBB_AAA", 4, ("BBB", "AAA"))]
    out = feats(inp, 2, cancelled=gone)
    assert row(out, "AAA")["games_remaining"] == 1 and row(out, "CCC")["games_remaining"] == 2


def test_starting_qb_changes_count_distinct_starters():
    games = [g(2011, 1, "BBB", "AAA", 10, 20, qb=("B1", "A1")),
             g(2011, 2, "AAA", "BBB", 10, 20, qb=("A2", "B1")),
             g(2011, 3, "BBB", "AAA", 10, 20, qb=("B1", "A1"))]  # fmt: skip
    out = feats(inputs(games), 3)
    assert (
        row(out, "AAA")["starting_qb_changes"] == 1 and row(out, "BBB")["starting_qb_changes"] == 0
    )


def test_rookie_first_round_qb_on_the_latest_visible_roster():
    games = weeks(2011, 3)

    def r(week, team, gid, status, public_week):
        return {"week": week, "team": team, "gsis_id": gid, "status": status,
                "available_at": avail(2011, public_week)}  # fmt: skip

    rost = [r(1, "CCC", "R1", "ACT", 1), r(1, "AAA", "V1", "ACT", 1), r(2, "CCC", "R1", "CUT", 2),
            r(3, "AAA", "V1", "ACT", 9)]  # fmt: skip
    inp = inputs(games, rosters=rost, players=["R1"])
    w1, w2 = feats(inp, 1), feats(inp, 2)
    assert row(w1, "CCC")["rookie_r1_qb_on_roster"] is True
    assert row(w2, "CCC")["rookie_r1_qb_on_roster"] is False  # released (CUT) by week 2
    assert row(w2, "AAA")["rookie_r1_qb_on_roster"] is False  # V1 is no first-round rookie
    assert row(w2, "BBB")["rookie_r1_qb_on_roster"] is None  # no visible roster


def test_the_coach_in_charge_is_the_coach_of_the_latest_played_game():
    fired = lambda team, s, w: "new" if (team == "AAA" and w >= 3) else f"{team.lower()}_c"  # noqa: E731
    inp = inputs(weeks(2011, 4), coach=fired)
    assert row(feats(inp, 2), "AAA")["coach_id"] == "aaa_c"  # the firing is in the future
    late = row(feats(inp, 3), "AAA")
    assert late["coach_id"] == "new" and late["took_over_mid_season"] is True
    assert late["tenure_seasons"] == 1 and late["is_first_year_coach"] is True
    assert row(feats(inp, 3), "BBB")["took_over_mid_season"] is False


def test_fourth_down_wp_lost_per_game_needs_every_game_graded():
    games = weeks(2011, 2)
    grades = pl.DataFrame({
        "game_id": ["2011_01_BBB_AAA", "2011_01_BBB_AAA", "2011_02_BBB_AAA", "2011_02_BBB_AAA",
                    "2011_01_DDD_CCC", "2011_01_DDD_CCC"],
        "team": ["AAA", "BBB", "AAA", "BBB", "CCC", "DDD"],
        "wp_lost_clear": [0.03, 0.0, 0.01, 0.02, 0.05, 0.0],
    })  # fmt: skip
    out = feats(inputs(games, grades=grades), 2)
    assert row(out, "AAA")["fourth_down_wp_lost_per_game"] == pytest.approx(0.02)
    assert row(out, "BBB")["fourth_down_wp_lost_per_game"] == pytest.approx(0.01)
    assert row(out, "CCC")["fourth_down_wp_lost_per_game"] is None  # week 2 not graded
    assert row(feats(inputs(games), 2), "AAA")["fourth_down_wp_lost_per_game"] is None
    one = feats(inputs(games, grades=grades), 1)  # week 2's grade is not used before week 2
    assert row(one, "AAA")["fourth_down_wp_lost_per_game"] == pytest.approx(0.03)


def test_grade_rows_sum_clear_calls_per_team_game():
    fourth = pl.DataFrame({
        "game_id": ["G1"] * 4, "season_type": ["REG"] * 4,
        "posteam": ["AAA", "AAA", "AAA", "BBB"], "defteam": ["BBB", "BBB", "BBB", "AAA"],
        "grade": ["clear", "toss_up", None, "toss_up"], "wp_lost": [0.02, 0.5, None, 0.1],
    })  # fmt: skip
    got = hf.grade_rows(fourth).rows()
    assert got == [("G1", "AAA", 0.02), ("G1", "BBB", 0.0)]


def test_is_interim_joins_the_owner_file_to_took_over_mid_season():
    df = feats(inputs(weeks(2011, 2)), 2)
    df = df.with_columns((pl.col("team") == "DDD").alias("took_over_mid_season"))
    labels = pl.DataFrame({
        "candidate_id": ["2011_AAA_w17_aaa_coach", "2011_BBB_w17_bbb_coach"],
        "departure_type": ["interim_not_retained", "fired_after_season"],
        "interim_suspected": ["false", "false"],
    })  # fmt: skip
    cands = pl.DataFrame({
        "candidate_id": ["2011_AAA_w17_aaa_coach", "2011_BBB_w17_bbb_coach"],
        "coach_id": ["aaa_coach", "bbb_coach"], "team": ["AAA", "BBB"],
        "last_season": ["2011", "2011"],
    })  # fmt: skip
    out = hf.add_interim_flag(df, labels, cands)
    got = {t: row(out, t)["is_interim"] for t in ("AAA", "BBB", "CCC", "DDD")}
    assert got == {"AAA": True, "BBB": False, "CCC": False, "DDD": True}
    assert hf.add_interim_flag(df, None, None)["is_interim"].to_list() == [False] * 3 + [True]
    assert out.columns[: len(hf.KEY_COLUMNS)] == list(hf.KEY_COLUMNS)


def test_cancelled_game_is_gone_after_its_week_and_at_season_end():
    p = hf.AsOfPoint(2022, "weekly", 17, as_of(2022, 17), False)
    assert hf.cancelled_gone(p) == []  # still pending at its own week's as-of
    late = hf.AsOfPoint(2022, "weekly", 18, as_of(2022, 18), True)
    assert hf.cancelled_gone(late) == [("2022_17_BUF_CIN", 17, ("BUF", "CIN"))]
    eos = hf.AsOfPoint(2022, "end_of_season", 18, as_of(2022, 18), True)
    assert len(hf.cancelled_gone(eos)) == 1
    assert hf.cancelled_gone(hf.AsOfPoint(2021, "end_of_season", 18, as_of(2021, 18), True)) == []


def test_features_are_registered_hot_seat_features():
    from twm import registry as rg

    used = rg.check_features(hf.FEATURE_COLUMNS, "hot_seat")
    assert len(used) == len(hf.FEATURE_COLUMNS)
    for name in ("is_interim", "prev_playoff_result"):
        assert rg.get(name).kind != "feature"  # rows / text, never model inputs


# --------------------------------------------------------------------------------------
# Point in time, on a synthetic warehouse (built offline like tests/streamer_world.py)
# --------------------------------------------------------------------------------------

W_TEAMS = ("PHI", "DAL", "NYG", "WAS")
DAYS = {2024: ("2024-09-08", "2024-09-15"),
        2025: ("2025-09-07", "2025-09-14", "2025-09-21", "2025-09-28")}  # fmt: skip


def world_games(*, fired: bool) -> dict[int, list[dict]]:
    """2024: two weeks; 2025: four (moneylines in weeks 1-2 only). ``fired``: DAL's coach is
    replaced from 2025 week 3 (a firing after week 2's as-of)."""
    from tests.conftest import game

    out: dict[int, list[dict]] = {}
    for season, days in DAYS.items():
        rows = []
        for i, day in enumerate(days):
            w = i + 1
            for j, (away, home) in enumerate((("DAL", "PHI"), ("WAS", "NYG"))):
                if w % 2 == 0:
                    away, home = home, away
                coach = {t: f"{t.title()} Coach" for t in W_TEAMS}
                if fired and season == 2025 and w >= 3:
                    coach["DAL"] = "New Boss"
                r = game(season, w, day, "13:00", away, home, result=(-1) ** (w + j) * (3 + j),
                         spread=2.5 - j, home_coach=coach[home],
                         away_coach=coach[away])  # fmt: skip
                ml = (-140, 120) if season == 2025 and w <= 2 else (None, None)
                rows.append({**r, "home_moneyline": ml[0], "away_moneyline": ml[1],
                             "home_qb_id": f"QB_{home}", "away_qb_id": f"QB_{away}"})  # fmt: skip
        out[season] = rows
    return out


def world_plays(games: list[dict]) -> list[dict]:
    """Two neutral plays per team and game (a run and a pass), EPA varying by game."""
    out = []
    for n, gm in enumerate(games):
        for k, (pos, dfn) in enumerate(((gm["home_team"], gm["away_team"]),
                                        (gm["away_team"], gm["home_team"]))):  # fmt: skip
            for i, pt in enumerate(("run", "pass")):
                out.append({
                    "game_id": gm["game_id"], "play_id": float(10 * k + i + 1),
                    "season": gm["season"], "week": gm["week"], "season_type": "REG",
                    "game_date": gm["gameday"], "posteam": pos, "defteam": dfn,
                    "home_team": gm["home_team"], "away_team": gm["away_team"], "qtr": 1.0,
                    "down": 1.0, "desc": f"{pt} {i}", "pass": float(i), "play_type": pt,
                    "epa": 0.1 * (n % 5) - 0.2 * k + 0.05 * i, "wp": 0.5,
                    "half_seconds_remaining": 1500.0, "score_differential": 0.0,
                    "two_point_attempt": 0.0, "yards_gained": 3.0,
                })  # fmt: skip
    return out


def build_hot_seat_world(tmp, *, fired: bool = True):
    """Write the world's raw cache under ``tmp`` and build the warehouse (offline)."""
    from tests.conftest import PBP_DTYPES, SCHEDULE_DTYPES, RawCache, frame
    from twm import ids
    from twm.sources import nflverse as nv
    from twm.warehouse import build as wb

    sched = {**SCHEDULE_DTYPES, "home_moneyline": pl.Int32(), "away_moneyline": pl.Int32(),
             "home_qb_id": pl.String(), "away_qb_id": pl.String()}  # fmt: skip
    plays = {**PBP_DTYPES, "play_type": pl.String(), "two_point_attempt": pl.Float64()}
    games = world_games(fired=fired)
    with pytest.MonkeyPatch.context() as mp:
        root = tmp / "raw"
        mp.setattr(nv, "raw_dir", lambda: root)
        mp.setattr(nv, "_loader", lambda ds: (_ for _ in ()).throw(AssertionError(ds.name)))
        mp.setattr(nv, "_configure_nflreadpy", lambda: None)
        mp.setattr(ids, "overrides_path", lambda: tmp / "manual" / ids.OVERRIDES_FILE)
        cache = RawCache(root)
        cache.write_globals()
        for season, rows in games.items():
            cache.write_season(season, [{k: v for k, v in r.items() if k in SCHEDULE_DTYPES}
                                        for r in rows])  # fmt: skip
            cache.write("schedules", season, frame(rows, sched))
            cache.write("pbp", season, frame(world_plays(rows), plays))
        db = tmp / "hot_seat.duckdb"
        wb.build_warehouse(sorted(games), db_path=db)
    return db


FAR = datetime(2030, 1, 1, tzinfo=UTC)
R2 = hf.HotSeatRules(trend_games=2)  # a trend after 3 games in the 4-week world


def world_grades(db) -> pl.DataFrame:
    """A stored grade for every 2025 game (both teams), so a future game's grade could leak."""
    from twm.warehouse.build import connect

    con = connect(db, read_only=True)
    try:
        ids = con.execute("SELECT game_id, home_team, away_team FROM fact_game "
                          "WHERE season = 2025 ORDER BY game_id").fetchall()  # fmt: skip
    finally:
        con.close()
    rows = [(gid, t, 0.01 * (i + 1)) for i, (gid, h, a) in enumerate(ids) for t in (h, a)]
    return pl.DataFrame(rows, schema=["game_id", "team", "wp_lost_clear"], orient="row")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("hot_seat_world")
    db = build_hot_seat_world(tmp, fired=True)
    return db, world_grades(db)


def _points(db, rules):
    from twm.warehouse.build import connect

    con = connect(db, read_only=True)
    try:
        con.execute("SET TimeZone='UTC'")
        return hf.as_of_points(hf._Warehouse(con), 2025, rules, FAR)
    finally:
        con.close()


@pytest.mark.parametrize("anchor", ["asof", "last_game_end"])
def test_reference_path_equals_batch_path(world, anchor):
    from polars.testing import assert_frame_equal

    from twm.asof import AsOfView

    db, grades = world
    rules = hf.HotSeatRules(end_of_season_anchor=anchor, trend_games=2)
    batch = hf.features_history(db, [2025], now=FAR, rules=rules, grades={2025: grades})
    points = _points(db, rules)
    assert [p.week for p in points if p.snapshot == "weekly"] == [2, 3, 4]
    assert batch.height == 4 * len([p for p in points if p.teams is None]) + sum(
        len(p.teams) for p in points if p.teams is not None
    )
    for p in points:
        with AsOfView(db, p.as_of) as v:
            ref = hf.features_for(v, p, rules=rules, grades=grades)
        want = batch.filter((pl.col("as_of") == p.as_of) & (pl.col("snapshot") == p.snapshot))
        assert ref.height > 0
        assert_frame_equal(ref.sort("team"), want.sort("team"))
    assert batch["off_epa_neutral_trend"].drop_nulls().len() > 0  # exercised
    assert batch["fourth_down_wp_lost_per_game"].null_count() == 0


def test_end_of_season_anchor_last_game_end_waits_for_the_last_games_rows(world):
    db, _ = world
    late = _points(db, hf.HotSeatRules(end_of_season_anchor="last_game_end"))[-1]
    eos = _points(db, R)[-1]
    assert late.snapshot == eos.snapshot == "end_of_season"
    assert late.as_of < eos.as_of  # Sunday night, not Monday 12:00 UTC
    assert late.as_of == datetime(2025, 9, 29, 3, 0, tzinfo=UTC)  # 13:00 ET + 4 h + 6 h


@pytest.mark.parametrize("which", ["weekly_2", "weekly_3", "end_of_season"])
def test_leakage_harness(world, which):
    """Deleting or scrambling every row not yet public at the as-of changes nothing."""
    from twm.backtest.leakage import assert_future_invariant

    db, grades = world
    points = {f"{p.snapshot}_{p.week}" if p.snapshot == "weekly" else p.snapshot: p
              for p in _points(db, R2)}  # fmt: skip
    p = points[which]
    out = assert_future_invariant(
        lambda v: hf.features_for(v, p, rules=R2, grades=grades), db, p.as_of,
        key=list(hf.KEY_COLUMNS),
    )  # fmt: skip
    assert out.height == 4


def test_a_future_firing_and_future_games_leave_earlier_rows_unchanged(world, tmp_path):
    """World B: DAL keeps its coach all season. Rows up to week 2's as-of (before the firing,
    and before weeks 3-4 were played) are identical; from week 3 on DAL's coach differs."""
    from polars.testing import assert_frame_equal

    db_a, grades = world
    db_b = build_hot_seat_world(tmp_path, fired=False)
    a = hf.features_history(db_a, [2025], now=FAR, rules=R2, grades={2025: grades})
    b = hf.features_history(db_b, [2025], now=FAR, rules=R2, grades={2025: grades})
    early = pl.col("week") <= 2
    assert_frame_equal(a.filter(early), b.filter(early))
    dal = (pl.col("team") == "DAL") & (pl.col("week") == 3)
    assert a.filter(dal)["coach_id"].to_list() == ["new_boss"]
    assert b.filter(dal)["coach_id"].to_list() == ["dal_coach"]
    assert a.filter(dal)["took_over_mid_season"].to_list() == [True]
    # a weekly as-of after ``now`` is not built
    cut = hf.features_history(db_a, [2025], now=datetime(2025, 9, 20, tzinfo=UTC), rules=R2,
                              grades={2025: grades})  # fmt: skip
    assert cut["week"].unique().to_list() == [2]


def test_a_deliberately_leaky_builder_fails_the_harness(world, monkeypatch):
    """Reading fact_game past the view (``wh.``) without the as-of filter is caught."""
    from twm.backtest.leakage import LeakageError, assert_future_invariant

    db, _ = world
    p = _points(db, R2)[0]
    real_sql, real_visible = hf.input_sql, hf.SeasonInputs.visible

    def leaky_sql(season, *, batch):
        q = real_sql(season, batch=batch)
        return {**q, "games": q["games"].replace("FROM fact_game", "FROM wh.fact_game")}

    def leaky_visible(self, as_of):
        out = real_visible(self, as_of)
        return hf.SeasonInputs(out.season, {**out.frames, "games": self.frames["games"]},
                               batch=False, grades=out.grades)  # fmt: skip

    monkeypatch.setattr(hf, "input_sql", leaky_sql)
    monkeypatch.setattr(hf.SeasonInputs, "visible", leaky_visible)
    with pytest.raises(LeakageError):
        assert_future_invariant(lambda v: hf.features_for(v, p, rules=R2), db, p.as_of,
                                key=list(hf.KEY_COLUMNS))  # fmt: skip


def test_cli_writes_the_file_and_a_season_rebuild_keeps_the_others(world, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from twm.cli import app

    db, _ = world
    monkeypatch.setattr(hf, "load_grades", lambda seasons, **kw: {})
    out = tmp_path / "features.parquet"
    args = ["hotseat", "features", "--db", str(db), "--out", str(out), "--now", "2030-01-01"]
    res = CliRunner().invoke(app, [*args, "--start", "2024", "--end", "2025"])
    assert res.exit_code == 0, res.output
    first = pl.read_parquet(out)
    assert first["season"].unique().to_list() == [2024, 2025] and "is_interim" in first.columns
    assert "moneyline_share" in res.output and "null_share" in res.output
    res = CliRunner().invoke(app, [*args, "--season", "2025"])
    assert res.exit_code == 0, res.output
    again = pl.read_parquet(out)
    assert again.height == first.height and again.equals(first)


def test_hot_seat_terms_stay_off_the_published_glossary_until_the_page_ships():
    from twm import registry
    from twm.publish import collect

    names = set(collect.glossary().get_column("name").to_list())
    hot = {e.name for e in registry.entries() if set(e.modules) == {"hot_seat"}}
    assert hot and not hot & names
    shared = {e.name for e in registry.entries() if "hot_seat" not in e.modules}
    assert shared <= names
