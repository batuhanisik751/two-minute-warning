"""G3: fourth-down and two-point grades (twm.modules.decisions.grade_inputs, grade, coach,
decisions_report).

Offline, with stub models whose numbers can be checked by hand (WP = logistic(score / 7 +
(50 - yardline) / 40); constant P(convert) and P(make); one-outcome ball-spot tables and punt
distributions): each option's math, the range rules, clear vs toss-up and WP lost, the
two-point math, the exclusion rules and their order, head-coach attribution, the kickoff spot
read from earlier weeks only, the Platt check that never reads the graded season, the
point-in-time fold check, and reproduction of stored rows from their inputs alone. Real data
(``-m realdata``): one graded season's sanity.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from twm.modules.decisions import coach
from twm.modules.decisions import conversion as cv
from twm.modules.decisions import fieldgoal as fg
from twm.modules.decisions import grade as gr
from twm.modules.decisions import grade_inputs as gi
from twm.modules.decisions import punt as pu
from twm.modules.decisions import submodels as sm
from twm.modules.decisions import tries as tr
from twm.modules.decisions import wp as wpm
from twm.modules.decisions.wp_data import FEATURES


def L(z):  # noqa: N802 - the logistic function of the stub WP model
    return 1 / (1 + np.exp(-z))


class _StubWp:
    def predict(self, x):
        return L(x.get_column("score_differential").to_numpy() / 7
                 + (50 - x.get_column("yardline_100").to_numpy()) / 40)  # fmt: skip


class _Const:
    def __init__(self, p):
        self.p = p

    def predict(self, x):
        return np.full(x.height, self.p)


def _table(td: bool, value: int) -> pl.DataFrame:
    return pl.DataFrame({"yb": [0], "zb": [0], "td": [td], "value": [value], "n": [100]},
                        schema={"yb": pl.Int8, "zb": pl.Int8, "td": pl.Boolean,
                                "value": pl.Int32, "n": pl.UInt32})  # fmt: skip


def stub_models(season=2010, p_conv=0.6, p_make=0.8, punt_spot=70, gain=5) -> gr.Models:
    """WP stub; every conversion gains ``gain`` yards, every failure gives the opponent the
    ball at the spot, every punt leaves the receiving team at ``punt_spot``."""
    train = (season - 1,)
    w = wpm.WpModel(f"wp{season}", season, train, None, tuple(FEATURES), {}, fitted=_StubWp())
    conv = sm.SubModel(f"cv{season}", cv.MODEL, season, train, None, ("ydstogo",), {},
                       fitted=_Const(p_conv),
                       extras={"success": _table(False, gain), "failure": _table(False, 0),
                               "runoff_seconds": 10.0, "min_cell": 30})  # fmt: skip
    fgm = sm.SubModel(f"fg{season}", fg.MODEL, season, train, None, ("fg_distance",), {},
                      fitted=_Const(p_make))  # fmt: skip
    table = np.zeros((99, pu.N_CELLS))
    table[:, punt_spot - 1] = 1.0
    pm = sm.SubModel(f"pu{season}", pu.MODEL, season, train, None, ("yardline_100",), {},
                     extras={"table": table, "runoff_seconds": 9.0})  # fmt: skip
    tm = sm.SubModel(f"tr{season}", tr.MODEL, season, train, None, (), {},
                     extras={"pat": {"rate": 0.94}, "two_point": {"rate": 0.48}})  # fmt: skip
    return gr.Models(wp=w, conversion=conv, fieldgoal=fgm, punt=pm, tries=tm)


def fourth_inputs(models: gr.Models, **kw) -> pl.DataFrame:
    """One stored-input row of a fourth down (every FOURTH_INPUTS column + versions)."""
    row = dict(season=2010, score_differential=0, game_seconds_remaining=1500,
               half_seconds_remaining=1500, half_number=2, posteam_timeouts_remaining=3,
               defteam_timeouts_remaining=3, receives_2h_kickoff=0, posteam_is_home=1.0,
               posteam_spread=0.0, down=4, ydstogo=2, yardline_100=40, goal_to_go=0,
               fg_distance=58, roof_closed=1, temp_f=70.0, wind_mph=0.0, surface_grass=0.0,
               weather_missing=0, kick_yardline_100=75, kick_runoff=0.0, fg_max_distance=60,
               punt_min_yardline=32, fg_runoff_make=5.0, fg_runoff_miss=5.0, platt_a=None,
               platt_b=None, margin=0.015, chosen="punt", exclusion=None, play_id=1,
               game_id="g1")  # fmt: skip
    row.update(kw)
    row["fg_distance"] = row["yardline_100"] + fg.DISTANCE_OFFSET
    return pl.DataFrame([{**row, **models.versions()}],
                        schema_overrides={"platt_a": pl.Float64, "platt_b": pl.Float64,
                                          "exclusion": pl.String})  # fmt: skip


def test_each_option_matches_the_hand_computation():
    m = stub_models()
    x = fourth_inputs(m, score_differential=3, yardline_100=40)
    go, fgo, pt = gr.go_option(x, m), gr.fg_option(x, m), gr.punt_option(x, m)
    # go: success = own 1st down 5 yards on (yardline 35); failure = opponent ball at its 60
    succ = L(3 / 7 + (50 - 35) / 40)
    fail = 1 - L(-3 / 7 + (50 - 60) / 40)
    assert go["wp_go_success"][0] == pytest.approx(succ)
    assert go["wp_go_failure"][0] == pytest.approx(fail)
    assert go["wp_go"][0] == pytest.approx(0.6 * succ + 0.4 * fail)
    # field goal: make = +3 and the opponent at the kickoff spot (75); miss = opponent at
    # min(80, 92 - 40) = 52
    make = 1 - L(-6 / 7 + (50 - 75) / 40)
    miss = 1 - L(-3 / 7 + (50 - 52) / 40)
    assert fgo["wp_fg"][0] == pytest.approx(0.8 * make + 0.2 * miss)
    # punt: the receiving team at its own 30
    assert pt["wp_punt"][0] == pytest.approx(1 - L(-3 / 7 + (50 - 70) / 40))


def test_touchdowns_inside_options_count_seven_and_use_the_kickoff_spot():
    m = stub_models(gain=40)  # every conversion from the 40 scores
    x = fourth_inputs(m, score_differential=0, yardline_100=40, kick_yardline_100=70)
    go = gr.go_option(x, m)
    assert go["wp_go_success"][0] == pytest.approx(1 - L(-7 / 7 + (50 - 70) / 40))
    later = fourth_inputs(m, score_differential=0, yardline_100=40, kick_yardline_100=65)
    assert gr.go_option(later, m)["wp_go_success"][0] < go["wp_go_success"][0]


def test_platt_is_applied_only_when_the_row_has_one():
    m = stub_models(p_conv=0.3)
    plain = gr.go_option(fourth_inputs(m), m)
    cal = gr.go_option(fourth_inputs(m, platt_a=0.4, platt_b=1.1), m)
    assert plain["p_convert"][0] == plain["p_convert_raw"][0] == pytest.approx(0.3)
    z = 0.4 + 1.1 * np.log(0.3 / 0.7)
    assert cal["p_convert"][0] == pytest.approx(L(z))
    assert cal["p_convert_raw"][0] == pytest.approx(0.3)


def test_decide_clear_toss_up_one_option_and_wp_lost():
    x = pl.DataFrame({"chosen": ["punt", "go", "field_goal", "go"],
                      "margin": [0.015] * 4})  # fmt: skip
    wps = np.array([[0.50, 0.40, 0.48],    # go best by 2 pts: clear, punt lost 2
                    [0.50, 0.49, 0.30],    # go best by 1 pt: toss-up
                    [0.45, 0.45, np.nan],  # tie: the earlier option (go) is "best"
                    [0.30, np.nan, np.nan]])  # only go existed  # fmt: skip
    d = gr._decide(x, wps, gr.OPTIONS)
    assert list(d["grade"]) == ["clear", "toss_up", "toss_up", "one_option"]
    assert list(d["recommended"]) == ["go", "go", "go", "go"]
    assert d["wp_lost"][0] == pytest.approx(0.02) and d["gap"][0] == pytest.approx(0.02)
    assert d["wp_lost"][2] == 0.0 and not d["correct"][2] and d["correct"][1]
    assert np.isnan(d["gap"][3]) and d["second_best"][3] is None


def test_kicking_options_exist_only_in_range_unless_chosen():
    m = stub_models()
    far = fourth_inputs(m, yardline_100=50, fg_max_distance=60, chosen="punt")  # 68-yard kick
    g = gr.grade_fourth_downs(far.select(gr.FOURTH_INPUTS), m)
    assert not g.item(0, "fg_available") and g.item(0, "punt_available")
    tried = fourth_inputs(m, yardline_100=50, fg_max_distance=60, chosen="field_goal")
    g2 = gr.grade_fourth_downs(tried.select(gr.FOURTH_INPUTS), m)
    assert g2.item(0, "fg_available") and g2.item(0, "wp_chosen") == g2.item(0, "wp_fg")
    near = fourth_inputs(m, yardline_100=20, punt_min_yardline=32, chosen="field_goal")
    assert not gr.grade_fourth_downs(near.select(gr.FOURTH_INPUTS), m).item(0, "punt_available")


def test_two_point_math():
    m = stub_models()
    x = fourth_inputs(m, score_differential=-1, yardline_100=15, chosen="kick",
                      kick_yardline_100=75).with_columns(
        pl.lit(0.94).alias("p_pat"), pl.lit(0.48).alias("p_two_point"))  # fmt: skip
    t = gr.try_option(x, m)
    after = {k: 1 - L(-(-1 + k) / 7 + (50 - 75) / 40) for k in (0, 1, 2)}
    assert t["wp_kick"][0] == pytest.approx(0.94 * after[1] + 0.06 * after[0])
    assert t["wp_two_point"][0] == pytest.approx(0.48 * after[2] + 0.52 * after[0])
    g = gr.grade_tries(x.select(gr.TRY_INPUTS), m)
    best = "kick" if t["wp_kick"][0] >= t["wp_two_point"][0] else "two_point"
    assert g.item(0, "recommended") == best
    assert g.item(0, "wp_lost") == pytest.approx(abs(t["wp_kick"][0] - t["wp_two_point"][0])
                                                 if best == "two_point" else 0.0)  # fmt: skip


def _play(**kw):
    base = dict(game_id="2010_01_A_H", play_id=1, season=2010, week=1, season_type="REG",
                game_date=None, qtr=3, game_half="Half2", down=4, ydstogo=3, yardline_100=45,
                goal_to_go=0, game_seconds_remaining=1200, half_seconds_remaining=1200,
                score_differential=0, posteam_timeouts_remaining=3,
                defteam_timeouts_remaining=3, posteam="H", defteam="A", home_team="H",
                away_team="A", play_type="pass", aborted_play=0, kickoff_attempt=0,
                extra_point_attempt=0, two_point_attempt=0, yards_gained=0, first_down=0,
                touchdown=0, td_team=None, interception=0, fumble_lost=0,
                field_goal_result=None, extra_point_result=None, two_point_conv_result=None,
                weather=None, desc="", spread_line=3.0, location="Home", roof="dome",
                surface="grass", temp=None, wind=None, home_coach="Home Coach",
                away_coach="Away Coach")  # fmt: skip
    return {**base, **kw}


def synth_season() -> pl.DataFrame:
    rows = [
        _play(play_id=1, qtr=1, down=None, play_type="kickoff", kickoff_attempt=1,
              posteam="A", defteam="H", game_half="Half1"),
        _play(play_id=2),                                                      # go (graded)
        _play(play_id=3, play_type="punt", posteam="A", defteam="H"),          # punt (graded)
        _play(play_id=4, play_type="no_play", half_seconds_remaining=5),       # penalty first
        _play(play_id=5, play_type="qb_kneel"),
        _play(play_id=6, play_type="run", aborted_play=1),
        _play(play_id=7, play_type="punt", half_seconds_remaining=8),          # end of half
        _play(play_id=8, posteam_timeouts_remaining=None),                     # missing state
        _play(play_id=9, play_type=None),                                      # not a snap
        _play(play_id=10, play_type="field_goal", yardline_100=20),
        _play(play_id=11, down=None, play_type="extra_point", extra_point_attempt=1,
              yardline_100=15, score_differential=6),
        _play(play_id=12, down=None, play_type="pass", two_point_attempt=1, yardline_100=2,
              posteam="A", defteam="H", score_differential=-1),
        _play(play_id=13, down=None, play_type="no_play", two_point_attempt=1, yardline_100=2),
    ]  # fmt: skip
    return pl.DataFrame(rows, infer_schema_length=None).with_columns(
        pl.col("down", "ydstogo", "posteam_timeouts_remaining").cast(pl.Int32)
    )


def test_exclusion_rules_apply_in_order_and_fakes_are_go():
    f = gi.fourth_down_rows(synth_season(), end_of_half_seconds=10)
    ex = dict(zip(f.get_column("play_id"), f.get_column("exclusion"), strict=True))
    assert ex == {2: None, 3: None, 4: "penalty_no_play", 5: "kneel_or_spike",
                  6: "aborted_snap", 7: "end_of_half", 8: "missing_state", 9: "not_a_snap",
                  10: None}  # fmt: skip
    chosen = dict(zip(f.get_column("play_id"), f.get_column("chosen"), strict=True))
    assert chosen[2] == "go" and chosen[3] == "punt" and chosen[10] == "field_goal"
    t = gi.try_rows(synth_season())
    assert dict(zip(t.get_column("play_id"), t.get_column("chosen"), strict=True)) == {
        11: "kick",
        12: "two_point",
        13: "two_point",
    }
    assert t.filter(pl.col("play_id") == 13).item(0, "exclusion") == "penalty_no_play"


def test_decisions_are_credited_to_the_head_coach_of_the_team_with_the_ball():
    f = gi.fourth_down_rows(synth_season(), end_of_half_seconds=10)
    by = {r["play_id"]: (r["coach"], r["opp_coach"]) for r in f.iter_rows(named=True)}
    assert by[2] == ("Home Coach", "Away Coach")  # H has the ball at home
    assert by[3] == ("Away Coach", "Home Coach")
    t = gi.try_rows(synth_season())
    assert t.filter(pl.col("play_id") == 12).item(0, "coach") == "Away Coach"
    # the WP state: H is home, A kicked off first, so H does not get the 2nd-half kickoff
    row = f.filter(pl.col("play_id") == 2).row(0, named=True)
    assert row["posteam_is_home"] == 1.0 and row["posteam_spread"] == 3.0


def test_kickoff_spot_reads_earlier_weeks_only_and_falls_back_to_last_season():
    kicks = pl.DataFrame({
        "season": [2009] * 4 + [2010] * 5,
        "week": [1, 2, 3, 4, 1, 1, 2, 3, 3],
        "game_id": ["x"] * 9,
        "spot": [80, 80, 70, 70, 60, 62, 64, 10, 10],  # week-3 kicks must never be read
        "runoff": [0.0] * 4 + [5.0] * 5,
    })  # fmt: skip
    games = pl.DataFrame({"game_id": ["w1", "w3"], "season": [2010, 2010], "week": [1, 3]})
    s = gi.kickoff_spots(games, kicks, min_kicks=3).sort("game_id")
    w1, w3 = s.row(0, named=True), s.row(1, named=True)
    assert w1["kick_source"] == "previous_season" and w1["kick_spot_mean"] == 75.0
    assert w1["kick_runoff"] == 0.0 and w1["kick_n"] == 4
    assert w3["kick_source"] == "season_to_date" and w3["kick_spot_mean"] == 62.0
    assert w3["kick_yardline_100"] == 62 and w3["kick_runoff"] == 5.0
    few = gi.kickoff_spots(games, kicks, min_kicks=4).sort("game_id")
    assert few.item(1, "kick_source") == "previous_season"


def _platt_rows(season_bias: dict[int, float], n: int = 4000, seed: int = 3) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    out = []
    for s, bias in season_bias.items():
        p = rng.uniform(0.1, 0.8, n)
        y = rng.random(n) < L(bias + np.log(p / (1 - p)))
        out.append(pl.DataFrame({"season": s, "converted": y.astype(np.int8), "prob": p}))
    return pl.concat(out)


def test_platt_is_kept_only_when_it_helps_on_the_season_before_and_never_reads_s():
    biased = _platt_rows({2006: 0.5, 2007: 0.5, 2008: 0.5, 2009: 0.5})
    p = gi.platt(biased, 2010, 3)
    assert p["kept"] and p["val_season"] == 2009 and p["fit_seasons"] == [2006, 2007, 2008]
    assert p["final_seasons"] == [2007, 2008, 2009] and 0.3 < p["a"] < 0.7
    honest = gi.platt(_platt_rows({2006: 0.0, 2007: 0.0, 2008: 0.0, 2009: 0.0}), 2010, 3)
    assert not honest["kept"] and honest["a"] is None
    with_s = pl.concat([biased, _platt_rows({2010: -3.0}, seed=9)])
    assert gi.platt(with_s, 2010, 3) == p  # season S itself is never read
    assert not gi.platt(biased.filter(pl.col("season") == 2009), 2010, 3)["kept"]  # no fit


def test_models_must_be_the_graded_seasons_point_in_time_folds(tmp_path):
    m = stub_models(season=2010)
    m.check(2010)
    with pytest.raises(gr.GradeError):
        m.check(2011)  # the 2010 fold does not grade 2011
    seen = stub_models(season=2010)
    seen.punt.train_seasons = (2009, 2010)  # a model that saw the graded season
    with pytest.raises(gr.GradeError):
        seen.check(2010)
    with pytest.raises(gr.GradeError):
        gr.load_models(2010, models_root=tmp_path, folds_root=tmp_path)


def test_stored_rows_reproduce_exactly_from_their_inputs(tmp_path):
    m = stub_models()
    rows = pl.concat([fourth_inputs(m, play_id=i, score_differential=d, yardline_100=y,
                                    ydstogo=t, chosen=c, game_seconds_remaining=1500 - i)
                      for i, (d, y, t, c) in enumerate([(0, 40, 2, "punt"), (-4, 70, 1, "go"),
                                                        (3, 25, 6, "field_goal"),
                                                        (7, 55, 9, "punt")])])  # fmt: skip
    rows = rows.with_columns(pl.lit(2010).alias("season"))
    out = gr.grade_fourth_downs(rows.select(gr.FOURTH_INPUTS), m)
    stored = rows.hstack(out)
    path = tmp_path / "fourth_downs_2010.parquet"
    stored.write_parquet(path)
    back = pl.read_parquet(path)
    known = {tuple(m.versions().values()): m}
    assert gr.verify(back, "fourth_downs", models=known) == []
    alone = gr.regrade(back.slice(2, 1), "fourth_downs", models=known)
    assert alone.item(0, "wp_go") == back.item(2, "wp_go")  # bit for bit, alone or in a batch
    tampered = back.with_columns(pl.lit(80).alias("kick_yardline_100"))
    assert "wp_fg" in gr.verify(tampered, "fourth_downs", models=known)
    with pytest.raises(gr.GradeError):  # versions that name no saved model
        gr.verify(back, "fourth_downs", models_root=tmp_path)


def _graded_season(m: gr.Models, tmp_path):
    plays = synth_season()
    spots = pl.DataFrame({"game_id": ["2010_01_A_H"], "kick_spot_mean": [74.6],
                          "kick_n": [150], "kick_runoff": [0.0],
                          "kick_source": ["season_to_date"],
                          "kick_yardline_100": [75]}).with_columns(
        pl.col("kick_yardline_100").cast(pl.Int32))  # fmt: skip
    info = {"fg_max_distance": 60, "punt_min_yardline": 32, "fg_runoff_make": 5.0,
            "fg_runoff_miss": 5.0, "platt": {"kept": False, "a": None, "b": None},
            "margin": 0.015, "end_of_half_seconds": 10, **m.versions()}  # fmt: skip
    fourth, tries = gr.grade_frames(*gr.assemble(plays, spots, info, m), m)
    gr.write_season(2010, fourth, tries, info, out_dir=tmp_path)
    return fourth, tries


def test_season_assembly_stores_inputs_and_report_runs_end_to_end(tmp_path):
    from twm.config import settings
    from twm.modules.decisions import decisions_report as dr

    m = stub_models()
    fourth, tries = _graded_season(m, tmp_path)
    graded = fourth.filter(pl.col("exclusion").is_null())
    assert graded.height == 3 and graded.get_column("grade").null_count() == 0
    assert set(gr.FOURTH_INPUTS) | set(gr.VERSION_COLUMNS) <= set(fourth.columns)
    assert fourth.filter(pl.col("exclusion").is_not_null()).get_column("wp_go").null_count() == 6
    assert tries.filter(pl.col("exclusion").is_null()).height == 2
    known = {tuple(m.versions().values()): m}
    c = dr.compute(tmp_path, cfg=settings().decisions, models=known,
                   models_root=tmp_path)  # fmt: skip
    assert c["verify"] == {"rows": 5, "mismatches": []}
    md = dr.render(c)
    assert "## 1. What was graded" in md and "Home Coach" in md and "penalty_no_play" in md
    long = dr.csv_rows(c)
    assert set(long.get_column("table")) >= {"league", "coach_season"}
    assert tuple(long.columns) == dr.CSV_COLUMNS


def test_coach_aggregates_count_clear_decisions_only():
    f = pl.DataFrame({
        "season": [2010] * 5, "week": [1, 1, 1, 2, 2], "game_id": ["a", "a", "a", "b", "b"],
        "play_id": [1, 2, 3, 1, 2], "coach": ["X"] * 5, "posteam": ["T"] * 5,
        "exclusion": [None, None, None, None, "end_of_half"],
        "grade": ["clear", "clear", "toss_up", "clear", None],
        "recommended": ["go", "go", "punt", "punt", None],
        "chosen": ["go", "punt", "go", "punt", "punt"],
        "correct": [True, False, False, True, None],
        "wp_lost": [0.0, 0.04, 0.01, 0.0, None],
    })  # fmt: skip
    t = f.head(0).select("season", "week", "game_id", "play_id", "coach", "posteam",
                         "exclusion", "grade", "chosen", "correct", "wp_lost")  # fmt: skip
    s = coach.coach_season(f, t).row(0, named=True)
    assert (s["games"], s["fourth_graded"], s["fourth_toss_ups"], s["fourth_wrong"]) == (2, 3, 1, 1)
    assert s["fourth_wp_lost"] == pytest.approx(0.04) and s["aggressiveness"] == 0.5
    assert s["wp_lost_per_game"] == pytest.approx(0.02) and s["went"] == 2
    w = coach.coach_week(f, t)
    assert w.height == 2 and w.filter(pl.col("game_id") == "a").item(0, "fourth_wp_lost") == 0.04


def test_cli_grade_rejects_a_season_without_folds():
    from typer.testing import CliRunner

    from twm.cli import app

    r = CliRunner().invoke(app, ["decisions", "grade", "--season", "2030"])
    assert r.exit_code == 2


# --------------------------------------------------------------------------------------
# Real data (opt-in: -m realdata; never downloads; needs the warehouse and the 2025 folds)
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def graded_2025(tmp_path_factory):
    from twm.config import settings

    db = settings().path("warehouse")
    if not db.exists():
        pytest.skip("run `uv run twm build` first")
    try:
        models = gr.load_models(2025)
    except gr.GradeError:
        pytest.skip("run `twm decisions wp-backtest` and `submodels-backtest` first")
    out = tmp_path_factory.mktemp("graded")
    summary = gr.grade_season(db, 2025, models=models, out_dir=out, progress=lambda _: None)
    return summary, gr.load_graded("fourth_downs", [2025], out), gr.load_graded(
        "two_point", [2025], out)  # fmt: skip


@pytest.mark.realdata
def test_real_season_is_graded_sensibly_and_reproducibly(graded_2025):
    summary, f, t = graded_2025
    g = f.filter(pl.col("exclusion").is_null())
    assert g.height > 3000 and summary["fourth_downs"]["excluded_penalty_no_play"] > 100
    assert g.get_column("coach").null_count() == 0
    assert f.group_by("game_id").agg(pl.col("coach").n_unique()).get_column("coach").max() == 2
    assert 60 <= g.get_column("kick_yardline_100").min() <= g.get_column(
        "kick_yardline_100").max() <= 80  # fmt: skip
    close = (pl.col("qtr") <= 3) & (pl.col("score_differential").abs() <= 7)
    deep = g.filter(close & (pl.col("ydstogo") >= 10) & pl.col("yardline_100").is_between(75, 90))
    assert (deep.get_column("recommended") == "punt").mean() > 0.9
    short = g.filter(close & (pl.col("ydstogo") == 1) & pl.col("yardline_100").is_between(35, 55))
    assert (short.get_column("recommended") == "go").mean() > 0.5
    assert gr.verify(g.sample(100, seed=1), "fourth_downs") == []
    assert gr.verify(t.filter(pl.col("exclusion").is_null()).sample(50, seed=1),
                     "two_point") == []  # fmt: skip


def _save_folds(models: gr.Models, season: int, models_root, folds_root) -> None:
    """Save ``models`` as the folds of ``season`` (the layout the backtests write)."""
    import json

    for name, m in (("wp", models.wp), *((k, getattr(models, k)) for k in gr.SUBMODELS)):
        if name == "wp":
            path, d = wpm.save_model(m, models_root), folds_root / "wp_backtest"
        else:
            path, d = sm.save_model(m, models_root), folds_root / "submodels" / m.model
        d.mkdir(parents=True, exist_ok=True)
        (d / f"fold_{season}.json").write_text(json.dumps({"model_file": path.name}))


def test_load_models_picks_the_graded_seasons_fold_and_refuses_a_mislabelled_one(tmp_path):
    root, folds = tmp_path / "models", tmp_path / "folds"
    for s in (2009, 2010):
        _save_folds(stub_models(season=s), s, root, folds)
    got = gr.load_models(2010, models_root=root, folds_root=folds)
    assert got.versions() == stub_models(season=2010).versions()
    _save_folds(stub_models(season=2010), 2011, root, folds)  # 2011 points at the 2010 fold
    with pytest.raises(gr.GradeError, match="not of 2011"):
        gr.load_models(2011, models_root=root, folds_root=folds)
    by_version = gr.models_by_version(got.versions(), models_root=root)
    assert by_version.versions() == got.versions()
