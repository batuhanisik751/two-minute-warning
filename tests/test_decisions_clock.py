"""G4: clock-management metrics (twm.modules.decisions.clock_inputs, clock, clock_report).

Offline, on synthetic snaps whose numbers can be checked by hand (kneel_play p = 2 s,
kneel_cycle g = 38 s, run 5 s, pass 7 s): the kneel arithmetic, each metric's definition on
situations inside and just outside it, the runoff and seconds-wasted arithmetic, head-coach
attribution, the measured constants (never season S), and reproduction of stored outputs from
the stored rows alone. Real data (``-m realdata``): a season's counts in a plausible range.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from twm.modules.decisions import clock as ck
from twm.modules.decisions import clock_inputs as ci

CFG = SimpleNamespace(one_score_margin=8, final_window_seconds=120, runoff_seasons=5,
                      clock_ran_min_seconds=10, passivity_min_seconds=40,
                      passivity_min_timeouts=1, passivity_min_ep=1.0)  # fmt: skip
CONSTS = {"kneel_play": 2.0, "kneel_cycle": 38.0, "play_seconds_run": 5.0,
          "play_seconds_pass": 7.0}  # fmt: skip


def win_row(play_id: int, **over) -> dict:
    """One stored window row (an opponent snap; the team = the defense, trailing by 3)."""
    r = dict(season=2020, game_id="2020_01_AAA_BBB", play_id=play_id, week=1, season_type="REG",
             game_date="2020-09-13", qtr=4, home_team="BBB", away_team="AAA", desc="",
             team="BBB", opp="AAA", coach="Home Coach", opp_coach="Away Coach", fixed_drive=20,
             game_seconds_remaining=100, down=1, ydstogo=10, yardline_100=60,
             score_differential=3, team_t=2, play_type="run", n_game_seconds_remaining=None,
             n_down=None, n_team_t=None, n_same_drive=False, drive_last=True,
             drive_last_team_t=2, game_last_drive=True, team_margin=-3, overtime=False,
             **CONSTS, final_window_seconds=120, one_score_margin=8,
             clock_ran_min_seconds=10)  # fmt: skip
    r.update(over)
    return r


def frame(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows, infer_schema_length=None)


def k(down: int, t: int) -> float:
    f = pl.DataFrame({"down": [down], **{c: [v] for c, v in CONSTS.items()}})
    return f.select(ck.kneel_out(pl.col("down"), t)).item()


def test_kneel_arithmetic():
    # n = 5 - d kneels of 2 s, and 36 s more between two kneels the defense does not stop
    assert k(1, 0) == 4 * 2 + 3 * 36 == 116
    assert k(1, 1) == 8 + 2 * 36 and k(1, 2) == 8 + 36 and k(1, 3) == k(1, 9) == 8
    assert k(2, 0) == 6 + 72 and k(3, 0) == 4 + 36 and k(4, 0) == k(4, 3) == 2


def test_timeouts_unused_in_and_out_of_the_definition():
    def one(**over) -> pl.DataFrame:
        return ck.timeouts_unused(ck.window_outputs(frame([win_row(1, **over)])))

    # 1st down at 1:40 vs 2 timeouts: K(1, 2) = 44 < 100 <= K(1, 0) = 116 -> a run-out snap
    m = one()
    assert m.height == 1 and m["case"].item() and m["timeouts_left"].item() == 2
    assert m["coach"].item() == "Home Coach" and m["ro_k_free"].item() == 116
    assert one(drive_last_team_t=0)["case"].to_list() == [False]  # used them all: candidate only
    assert one(overtime=True).height == 0  # not decided in regulation
    assert one(team_margin=-9).height == 0 and one(team_margin=3).height == 0
    assert one(game_last_drive=False).height == 0  # the team got the ball back
    assert one(down=2, game_seconds_remaining=100).height == 0  # K(2, 0) = 78 < 100: no run-out
    assert one(game_seconds_remaining=40, team_t=1).height == 0  # K(1, 1) = 80: kneels anyway
    assert one(team_t=0).height == 0  # no timeout to use at that snap
    assert one(game_seconds_remaining=117).height == 0 and one(game_seconds_remaining=116).height
    assert one(play_type="no_play").height == 0  # a penalty snap is not a run-out snap


def drive(last_t: int = 1, lead: int = 3, fixed_drive: int = 20, base: int = 0) -> list[dict]:
    """An opponent drive in the window: A (1st down 1:20, run; the clock runs to 0:37),
    B (2nd down 0:37, run; the team calls a timeout, next snap 0:30), C (3rd down 0:30, last)."""
    common = dict(score_differential=lead, fixed_drive=fixed_drive, drive_last_team_t=last_t,
                  game_last_drive=False)  # fmt: skip
    return [
        win_row(base + 1, game_seconds_remaining=80, down=1, team_t=2, n_game_seconds_remaining=37,
                n_down=2, n_team_t=2, n_same_drive=True, drive_last=False, **common),
        win_row(base + 2, game_seconds_remaining=37, down=2, team_t=2, n_game_seconds_remaining=30,
                n_down=3, n_team_t=1, n_same_drive=True, drive_last=False, **common),
        win_row(base + 3, game_seconds_remaining=30, down=3, team_t=1, **common),
    ]  # fmt: skip


def test_seconds_wasted_runoff_and_decisive_arithmetic():
    w = ck.window_outputs(frame(drive()))
    a, b, c = w.iter_rows(named=True)
    # A: the run ends at 80 - 5 = 75; 2nd down next, 2 timeouts: K(2, 1) = 42 < 75 <=
    # K(2, -1) = 114 (3 kneels, 3 gaps incl. the one before the next snap): decisive;
    # runoff = 80 - 37 - 5 = 38 s, no timeout -> a missed stop, counted (1 timeout kept)
    assert a["decisive"] and a["runoff"] == 38 and a["missed_stop"] and a["counted"]
    assert a["seconds_wasted"] == 38
    # B: decisive (K(3, 1) = 4 < 32 <= K(3, -1) = 76) but stopped by a timeout; runoff 2
    assert b["decisive"] and b["stopped"] and b["runoff"] == 2 and not b["missed_stop"]
    assert not c["interval"] and c["runoff"] is None  # the drive's last snap: no interval
    g = ck.seconds_wasted(w)
    assert g["seconds_wasted"].item() == 38 and g["case"].item() and g["missed_stops"].item() == 1
    assert g["coach"].item() == "Home Coach" and g["timeouts_kept"].item() == 1


def test_seconds_wasted_out_of_the_definition():
    def wasted(rows) -> float:
        g = ck.seconds_wasted(ck.window_outputs(frame(rows)))
        return float(g["seconds_wasted"].sum()) if g.height else 0.0

    assert wasted(drive(last_t=0)) == 0  # every timeout was used against the drive: k = 0
    assert wasted(drive(lead=9)) == 0  # two scores behind: outside
    rows = drive()
    rows[0]["n_game_seconds_remaining"] = 66  # runoff 80 - 66 - 5 = 9 < 10: the clock stopped
    assert wasted(rows) == 0
    rows = drive()
    rows[0]["game_seconds_remaining"] = 120  # ends at 115 > K(2, -1) = 114: not decisive
    rows[0]["n_game_seconds_remaining"] = 77
    assert wasted(rows) == 0
    # a 1st-down kneel at 1:19, one timeout left: it ends at 77 <= K(2, 0) = 78: the opponent
    # kneels out even if the timeout stops this gap -> not decisive (2025 wk 1, HOU at LA)
    rows = drive(last_t=1)
    rows[0].update(game_seconds_remaining=79, play_type="qb_kneel", team_t=1,
                   n_game_seconds_remaining=40, n_team_t=1)  # fmt: skip
    rows[1].update(team_t=1, n_team_t=1, n_game_seconds_remaining=1)
    assert wasted(rows) == 0
    rows = drive()
    rows[0]["team_t"] = rows[0]["n_team_t"] = 0  # no timeout to use
    assert wasted(rows) == 0
    # two drives, k = 1 each: 38 s each; the first k missed stops of each drive only
    assert wasted(drive() + drive(fixed_drive=22, base=10)) == 76
    two = drive(last_t=1)
    two[1].update(n_team_t=2, n_game_seconds_remaining=0)  # B not stopped: runoff 32
    assert wasted(two) == 38  # k = 1: A only (the first missed stop)


def half_snaps(**last) -> pl.DataFrame:
    """A first half's end: team AAA (2 timeouts) passes at 1:10, runs on 1st down at 1:04, then
    kneels on 2nd down at 0:22; its drive ends the half. ``last`` overrides the run (play 2)."""
    base = dict(game_id="g1", game_half="Half1", fixed_drive=9, posteam="AAA",
                fixed_drive_result="End of half", posteam_timeouts_remaining=2, qb_dropback=0,
                qb_scramble=0, ydstogo=10, yardline_100=45)  # fmt: skip
    rows = [dict(base, play_id=1, play_type="pass", down=1, half_seconds_remaining=70,
                 qb_dropback=1),
            dict(base, play_id=2, play_type="run", down=1, half_seconds_remaining=64),
            dict(base, play_id=3, play_type="qb_kneel", down=2,
                 half_seconds_remaining=22)]  # fmt: skip
    rows[1].update(last)
    return pl.DataFrame(rows, infer_schema_length=None)


def test_half_end_candidates_in_and_out_of_the_definition():
    c = ci.half_end_candidates(half_snaps(), CFG)
    assert c["play_id"].to_list() == [2] and c["tail_snaps"].item() == 2
    assert c["tail_kneels"].item() == 1 and c["tail_runs"].item() == 1
    assert c["half_end_seconds"].item() == 22
    for over in ({"half_seconds_remaining": 39}, {"posteam_timeouts_remaining": 0},
                 {"qb_scramble": 1}, {"down": 2}, {"play_type": "pass"}):  # fmt: skip
        assert ci.half_end_candidates(half_snaps(**over), CFG).height == 0, over
    s = half_snaps()
    timeout = s.with_columns(pl.when(pl.col("play_id") == 3).then(1)
                             .otherwise(pl.col("posteam_timeouts_remaining"))
                             .alias("posteam_timeouts_remaining"))  # fmt: skip
    assert ci.half_end_candidates(timeout, CFG).height == 0  # a timeout inside the tail
    fg = s.with_columns(pl.lit("Field goal").alias("fixed_drive_result"))
    assert ci.half_end_candidates(fg, CFG).height == 0  # the drive did not end the half
    penalty = pl.concat([s, s.filter(pl.col("play_id") == 3).with_columns(
        pl.lit(4, pl.Int64).alias("play_id"), pl.lit("no_play").alias("play_type"))])  # fmt: skip
    assert ci.half_end_candidates(penalty, CFG)["play_id"].to_list() == [2]


def L(z):  # noqa: N802 - the logistic function of the stub WP model
    return 1 / (1 + np.exp(-z))


class _StubWp:
    def predict(self, x):
        return L(x.get_column("score_differential").to_numpy() / 7
                 + (50 - x.get_column("yardline_100").to_numpy()) / 40)  # fmt: skip


def stub_wp(season: int = 2020):
    from twm.modules.decisions import wp as wpm
    from twm.modules.decisions.wp_data import FEATURES

    return wpm.WpModel(f"wp{season}", season, (season - 1,), None, tuple(FEATURES), {},
                       fitted=_StubWp())  # fmt: skip


def pas_row(**over) -> dict:
    r = dict(season=2020, score_differential=0, game_seconds_remaining=1860,
             half_seconds_remaining=60, half_number=1, posteam_timeouts_remaining=2,
             defteam_timeouts_remaining=3, receives_2h_kickoff=1, posteam_is_home=1.0,
             posteam_spread=0.0, down=1, ydstogo=10, yardline_100=50, kick_yardline_100=70,
             kick_runoff=5.0, passivity_min_ep=1.0)  # fmt: skip
    r.update(over)
    return r


def test_passivity_ep_and_wp_left():
    from twm.modules.decisions.wp_data import half_value

    rows = pl.DataFrame([pas_row(), pas_row(receives_2h_kickoff=0), pas_row(yardline_100=75)])
    out = ck.grade_passivity(rows, stub_wp())
    ep = half_value(np.array([50, 50, 75]), np.array([60, 60, 60]))
    assert np.allclose(out["ep_left"].to_numpy(), ep) and out["case"].to_list() == [
        True, True, False]  # fmt: skip
    # attacking: wp = L(0) = .5; the second half: the team (row 1) or the opponent (row 2)
    # receives at its own 30: L(-0.5) for the receiver
    assert np.allclose(out["wp_attack"].to_numpy()[:2], 0.5)
    assert np.allclose(out["wp_halftime"].to_numpy()[:2], [L(-0.5), 1 - L(-0.5)])
    assert np.allclose(out["wp_left"].to_numpy(), out["wp_attack"] - out["wp_halftime"])
    own, _ = ck.halftime_states(rows)
    assert own["game_seconds_remaining"].to_list() == [1795.0] * 3
    assert own["half_number"].to_list() == [2] * 3 and own["posteam_timeouts_remaining"][0] == 3


def game_snaps() -> pl.DataFrame:
    """Raw snaps of one game's end: away AAA leads 20-17 and has the ball (drive 30) at 1:50,
    1:10 (BBB used a timeout in between) and 0:30; BBB's earlier drive 29 at 2:40."""
    base = dict(season=2020, game_id="g2", week=3, season_type="REG", game_date="2020-09-27",
                qtr=4, game_half="Half2", ydstogo=10, yardline_100=60, half_seconds_remaining=0,
                home_team="BBB", away_team="AAA", qb_dropback=0, qb_scramble=0,
                fixed_drive_result="End of game", desc="", result=-3, spread_line=1.0,
                location="Home", home_coach="Home Coach", away_coach="Away Coach",
                overtime=False)  # fmt: skip
    rows = [
        dict(base, play_id=1, posteam="BBB", defteam="AAA", fixed_drive=29, down=1,
             game_seconds_remaining=160, score_differential=-3, posteam_timeouts_remaining=2,
             defteam_timeouts_remaining=3, play_type="pass"),
        dict(base, play_id=2, posteam="AAA", defteam="BBB", fixed_drive=30, down=1,
             game_seconds_remaining=110, score_differential=3, posteam_timeouts_remaining=3,
             defteam_timeouts_remaining=2, play_type="run"),
        dict(base, play_id=3, posteam="AAA", defteam="BBB", fixed_drive=30, down=2,
             game_seconds_remaining=70, score_differential=3, posteam_timeouts_remaining=3,
             defteam_timeouts_remaining=1, play_type="run"),
        dict(base, play_id=4, posteam="AAA", defteam="BBB", fixed_drive=30, down=1,
             game_seconds_remaining=30, score_differential=3, posteam_timeouts_remaining=3,
             defteam_timeouts_remaining=1, play_type="qb_kneel"),
    ]  # fmt: skip
    return pl.DataFrame(rows, infer_schema_length=None)


def test_next_snap_flags_and_defense_attribution():
    s = ci.add_next_snap(game_snaps())
    assert s["n_game_seconds_remaining"].to_list()[:3] == [110, 70, 30]
    # BBB is the defense at plays 2-4: its timeouts, read on whichever side it is next
    assert s["team_t"].to_list() == [3, 2, 1, 1] and s["n_team_t"].to_list()[1:3] == [1, 1]
    assert s["n_same_drive"].to_list() == [False, True, True, False]
    assert s["drive_last"].to_list() == [True, False, False, True]
    assert s["drive_last_team_t"].to_list()[1:] == [1, 1, 1]
    assert s["game_last_drive"].to_list() == [False, True, True, True]
    w = ci.defense_window(s, CONSTS, CFG)  # the opponent leads, Q4, <= 120 s: plays 2-4
    assert w["play_id"].to_list() == [2, 3, 4] and set(w["team"]) == {"BBB"}
    assert set(w["coach"]) == {"Home Coach"} and set(w["opp_coach"]) == {"Away Coach"}
    assert set(w["team_margin"]) == {-3} and w["kneel_cycle"][0] == 38.0
    m = ck.timeouts_unused(ck.window_outputs(w))
    # play 2: 1st down at 1:50, BBB 2 timeouts: K(1, 2) = 44 < 110 <= 116 -> run-out snap
    assert m["ro_play_id"].item() == 2 and m["timeouts_left"].item() == 1 and m["case"].item()


def test_constants_are_medians_of_earlier_seasons_only(monkeypatch):
    sql = []

    def fake_query(db, q):
        sql.append(q)
        return snaps

    rows = []
    for g, (pt, gap, dt) in enumerate(
        [
            ("qb_kneel", 2, 1),
            ("qb_kneel", 4, 1),
            ("qb_kneel", 39, 0),
            ("run", 6, 1),
            ("pass", 8, 1),
            ("run", 40, 0),
        ]
    ):
        for i, (gs, t) in enumerate([(100, 3), (100 - gap, 3 - dt)]):
            r = dict(game_id=f"g{g}", play_id=i, qtr=4, game_half="Half2",
                     half_seconds_remaining=gs, game_seconds_remaining=gs, down=1, posteam="A",
                     defteam="B", fixed_drive=1, play_type=pt, posteam_timeouts_remaining=3,
                     defteam_timeouts_remaining=t)  # fmt: skip
            rows.append(r)
    snaps = pl.DataFrame(rows)
    monkeypatch.setattr(ci.gi, "_query", fake_query)
    c = ci.measure_constants("unused.duckdb", 2020, 5)
    assert "p.season IN (2015, 2016, 2017, 2018, 2019)" in sql[0] and "2020" not in sql[0]
    assert (c["kneel_play"], c["kneel_cycle"]) == (3.0, 39.0) and c["n_kneel_play"] == 2
    assert (c["play_seconds_run"], c["play_seconds_pass"]) == (6.0, 8.0)
    assert c["constant_seasons"] == [2015, 2016, 2017, 2018, 2019]


def stored_passivity(model) -> pl.DataFrame:
    rows = pl.DataFrame([pas_row(), pas_row(yardline_100=75)]).with_columns(
        pl.Series("game_id", ["g1", "g2"]), pl.lit(2, pl.Int64).alias("play_id"),
        pl.lit(model.version).alias("wp_version"),
        pl.lit(None, pl.String).alias("exclusion"))  # fmt: skip
    return ck.with_passivity_grades(rows, model)


def test_stored_rows_reproduce_exactly_from_their_inputs(tmp_path):
    model = stub_wp()
    win = ck.window_outputs(frame(drive() + drive(fixed_drive=22, base=10)))
    summary = ck.write_season(2020, win, stored_passivity(model), {"wp_version": model.version},
                              out_dir=tmp_path)  # fmt: skip
    assert summary["late_seconds_wasted"] == 76 and summary["passivity_cases"] == 1
    w, p = ck.load("defense_snaps", out_dir=tmp_path), ck.load("half_passivity", out_dir=tmp_path)
    assert ck.verify(w, "defense_snaps") == []
    assert ck.verify(p, "half_passivity", models={model.version: model}) == []
    bad = w.with_columns(pl.col("seconds_wasted") + 1)
    assert ck.verify(bad, "defense_snaps") == ["seconds_wasted"]
    bad = p.with_columns(pl.col("wp_left") * 2)
    assert ck.verify(bad, "half_passivity", models={model.version: model}) == ["wp_left"]
    # a row recomputed alone equals the same row in its season batch
    alone = ck.recompute(p.head(1), "half_passivity", models={model.version: model})
    assert alone["wp_left"].item() == p["wp_left"][0]


def test_report_and_coach_aggregates_end_to_end(tmp_path):
    from twm.modules.decisions import clock_report as cr

    model = stub_wp()
    lost = [win_row(1, game_id="g9", drive_last_team_t=1, team_t=1, game_seconds_remaining=90)]
    win = ck.window_outputs(frame(drive() + lost))
    games = pl.DataFrame({"season": [2020] * 4, "week": [1, 1, 2, 2],
                          "season_type": ["REG"] * 4,
                          "game_id": ["2020_01_AAA_BBB", "2020_01_AAA_BBB", "g9", "g9"],
                          "team": ["BBB", "AAA", "BBB", "AAA"],
                          "coach": ["Home Coach", "Away Coach"] * 2})  # fmt: skip
    pas = stored_passivity(model).with_columns(
        pl.lit(1).alias("week"), pl.lit("AAA").alias("posteam"), pl.lit("BBB").alias("defteam"),
        pl.lit("Away Coach").alias("coach"), pl.lit(1, pl.UInt32).alias("tail_kneels"),
        pl.lit(0, pl.UInt32).alias("tail_runs"))  # fmt: skip
    ck.write_season(2020, win, pas, {"wp_version": model.version, **CONSTS}, team_games=games,
                    out_dir=tmp_path)  # fmt: skip
    md, csv = cr.write_report(out_dir=tmp_path, paths=(tmp_path / "c.md", tmp_path / "c.csv"),
                              models={model.version: model}, progress=lambda _: None)  # fmt: skip
    c = cr.compute(tmp_path, models={model.version: model})
    cs = c["coach_season"].filter(pl.col("coach") == "Home Coach").row(0, named=True)
    # Home Coach: 2 games; metric 1: g9 (lost by 3, 1 timeout kept) = 1 case; metric 3: 38 s
    assert cs["games"] == 2 and cs["m1_cases"] == 1 and cs["m1_timeouts_left"] == 1
    assert cs["m3_seconds_wasted"] == 38 and cs["m2_cases"] == 0
    away = c["coach_season"].filter(pl.col("coach") == "Away Coach").row(0, named=True)
    assert away["m2_cases"] == 1 and away["m2_ep_left"] > 1.0
    lg = c["league"].row(0, named=True)
    assert lg["team_games"] == 4 and lg["m1_cases"] == 1 and lg["kneel_cycle"] == 38.0
    assert c["verify"]["defense_snaps"][1] == [] and c["verify"]["half_passivity"][1] == []
    text = md.read_text()
    assert "## 1. The league" in text and "Home Coach" in text and "differing outputs: none" in text
    rows = pl.read_csv(csv)
    assert set(rows["table"]) == {"league", "coach_season"}


def test_cli_clock_rejects_a_season_without_folds():
    from typer.testing import CliRunner

    from twm.cli import app

    r = CliRunner().invoke(app, ["decisions", "clock", "--season", "1990"])
    assert r.exit_code == 2 and "seasons must be within" in r.output


# --------------------------------------------------------------------------------------
# Real data (opt-in: -m realdata; never downloads; needs the warehouse and the 2025 WP fold)
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def clock_2025(tmp_path_factory):
    from twm.config import settings
    from twm.modules.decisions import wp as wpm

    db = settings().path("warehouse")
    if not db.exists():
        pytest.skip("run `uv run twm build` first")
    try:
        model = wpm.load_fold_model(2025)
    except wpm.WpModelError:
        pytest.skip("run `twm decisions wp-backtest` first")
    out = tmp_path_factory.mktemp("clock")
    summary = ck.clock_season(db, 2025, wp_model=model, out_dir=out, progress=lambda _: None)
    return db, summary, out


@pytest.mark.realdata
def test_real_season_counts_are_plausible_and_reproducible(clock_2025):
    from twm.modules.decisions import grade_inputs as gi

    db, s, out = clock_2025
    assert 1 <= s["kneel_play"] <= 5 and 35 <= s["kneel_cycle"] <= 42
    assert 3 <= s["play_seconds_run"] <= 9 and 4 <= s["play_seconds_pass"] <= 11
    assert s["constant_seasons"] == [2020, 2021, 2022, 2023, 2024]
    # the plausible range comes from the data: regulation games decided by 1-8 points
    sql = (
        "SELECT count(*) AS n FROM w.fact_game WHERE season = 2025 AND "
        "abs(result) BETWEEN 1 AND 8 AND coalesce(overtime, 0) = 0"
    )
    close = gi._query(db, sql).item()
    assert 0.05 * close <= s["timeouts_unused_candidates"] <= close
    assert 0 < s["timeouts_unused_cases"] <= s["timeouts_unused_candidates"]
    assert 0.05 * close <= s["late_decisive_team_games"] <= 2 * close
    assert s["late_cases"] <= s["late_decisive_team_games"] and s["passivity_candidates"] < 40
    win, pas = ck.load("defense_snaps", out_dir=out), ck.load("half_passivity", out_dir=out)
    games = ck.load("team_games", out_dir=out)
    assert win["coach"].null_count() == 0 and games["coach"].null_count() == 0
    assert games.group_by("game_id").len()["len"].max() == 2
    m1 = ck.timeouts_unused(win)
    assert m1["team_margin"].is_between(-8, -1).all()
    assert ck.verify(win, "defense_snaps") == [] and ck.verify(pas, "half_passivity") == []
