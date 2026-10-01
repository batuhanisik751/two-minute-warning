"""G1b: smoothness of the WP surface (wp_smooth), the drive features, the model families
(wp_models) and the validation-only selection rule (wp_select). Offline, on toy functions and
synthetic plays."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from twm.modules.decisions import wp
from twm.modules.decisions import wp_data as wd
from twm.modules.decisions import wp_select as ws
from twm.modules.decisions import wp_smooth as wsm


def toy(step: float = 0.10, bonus: float = 0.0, slope: float = 0.02):
    """WP = 0.5 + slope x score, plus a jump of ``step`` between +2 and +3 (and -3 and -2,
    symmetric) in the first half only, plus ``bonus`` for having the ball."""

    def predict(states: pl.DataFrame) -> np.ndarray:
        d = states.get_column("score_differential").to_numpy().astype(float)
        h1 = states.get_column("half_number").to_numpy() == 1
        jump = step * ((d >= 3).astype(float) - (d <= -3).astype(float)) * h1
        return 0.5 + slope * d + jump + bonus

    return predict


def test_metrics_find_a_known_step_and_nothing_else():
    m = wsm.measure(toy(), 2020)
    assert m["score_step_h1"] == pytest.approx(12.0)  # 2 (slope) + 10 (jump)
    assert m["score_step_h2"] == pytest.approx(2.0)
    assert m["curvature"] == pytest.approx(10.0)  # |12 - 2|
    assert m["halftime_possession"] == pytest.approx(0.0, abs=1e-9)  # symmetric: no bonus
    assert m["monotone_violations"] == 0 and m["yard_step"] == pytest.approx(0.0)
    # the first of the two largest steps (-3 -> -2; +2 -> +3 is as large)
    assert m["score_step_at"] == -3 and "half 1" in m["score_step_state"]
    assert wsm.failures(m) == ["score_step_h1", "curvature"]
    assert wsm.worst_ratio(m) == pytest.approx(10.0 / 4.0)
    smooth = wsm.measure(toy(step=0.0), 2020)
    assert wsm.failures(smooth) == [] and smooth["curvature"] == pytest.approx(0.0, abs=1e-9)


def test_possession_bonus_and_monotone_violations_are_counted():
    m = wsm.measure(toy(step=0.0, bonus=0.05), 2020)
    assert m["halftime_possession"] == pytest.approx(10.0)  # +5 with the ball, -5 without
    assert m["halftime_possession_mean"] == pytest.approx(10.0)
    down = wsm.measure(toy(step=-0.05), 2020)  # WP falls from +2 to +3 and -3 to -2
    lines_h1 = len([t for t in wsm.SCORE_TIMES if t[1] == 1]) * 3 * 3 * 2
    assert down["monotone_violations"] == 2 * lines_h1


def test_the_grid_states_are_valid_wp_states():
    sl = wsm.score_lines(2020)
    assert sl.height == (3 * 3 * 3 * 2 + 3 * 3 * 3) * len(wsm.SCORE_RANGE)
    own, opp = wsm.possession_pairs(2020)
    assert own.height == opp.height == 3 * 2 * 5 * 2 * 2
    assert (own.get_column("score_differential") == -opp.get_column("score_differential")).all()
    kick = own.get_column("receives_2h_kickoff") + opp.get_column("receives_2h_kickoff")
    assert (kick == 1).all()
    assert (own.get_column("half_seconds_remaining") <= 10).all()
    full = wp.complete_states(sl.drop("line"))
    assert full.select(wd.FEATURES).null_count().sum_horizontal().item() == 0
    assert set(wd.SMOOTH_FEATURES) <= set(full.columns)


# --------------------------------------------------------------------------------------
# The drive features (fixed transforms of the state)
# --------------------------------------------------------------------------------------


def test_drive_ep_reads_the_table_and_is_monotone():
    t = np.array(wd.DRIVE_EP_TABLE)
    for i, yl in enumerate(wd.DRIVE_EP_YARDS):
        for j, h in enumerate(wd.DRIVE_EP_SECONDS):
            assert wd.drive_ep([yl], [h])[0] == pytest.approx(t[i, j])
    assert (wd.drive_ep(np.arange(1, 100), np.zeros(99)) == 0).all()  # the half is over
    yl, hs = np.meshgrid(np.arange(1, 100), [0, 1, 4, 9, 20, 40, 75, 150, 400, 1800])
    v = wd.drive_ep(yl.ravel(), hs.ravel()).reshape(yl.shape)
    assert (np.diff(v, axis=1) <= 1e-12).all()  # farther from the end zone: never more
    assert (np.diff(v, axis=0) >= -1e-12).all()  # more time: never less


def test_half_value_reads_its_table_and_falls_with_distance():
    t = np.array(wd.HALF_VALUE_TABLE)
    for i, yl in enumerate(wd.DRIVE_EP_YARDS):
        for j, h in enumerate(wd.DRIVE_EP_SECONDS):
            assert wd.half_value([yl], [h])[0] == pytest.approx(t[i, j])
    assert (wd.half_value(np.arange(1, 100), np.zeros(99)) == 0).all()
    yl, hs = np.meshgrid(np.arange(1, 100), [1, 9, 40, 100, 400, 1800])
    v = wd.half_value(yl.ravel(), hs.ravel()).reshape(yl.shape)
    assert (np.diff(v, axis=1) <= 1e-12).all()  # farther from the end zone: never more


def test_drive_value_vanishes_at_halftime_and_z_margin_follows_its_formula():
    st = pl.DataFrame({"yardline_100": [58, 75, 20, 58],
                       "half_seconds_remaining": [1, 5, 1500, 900],
                       "game_seconds_remaining": [1801, 1805, 3300, 900],
                       "score_differential": [0, 3, -7, 4],
                       "posteam_spread": [0.0, 3.5, -2.5, 1.0]})  # fmt: skip
    f = wd.smooth_features(st)
    dv = f.get_column("drive_value").to_numpy()
    assert dv[0] == pytest.approx(0.0, abs=0.05) and dv[1] == pytest.approx(0.0, abs=0.05)
    assert dv[2] > 3.0  # the opponent's 20, early in a half
    t = st.get_column("game_seconds_remaining").to_numpy() / 3600
    lead = st.get_column("score_differential").to_numpy() + dv
    z = (lead + st.get_column("posteam_spread").to_numpy() * t) / (wd.MARGIN_SD * np.sqrt(t) + 1)
    assert np.allclose(f.get_column("z_margin").to_numpy(), z)


# --------------------------------------------------------------------------------------
# Selection: validation seasons only, the fixed rule
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("season", [2003, 2006, 2015, 2025, 2026])
def test_selection_refuses_every_season_but_the_validation_ones(season, tmp_path):
    with pytest.raises(ws.SelectionError, match="never a test or graded season"):
        ws.run_candidate(pl.DataFrame(), "g1", season, out_dir=tmp_path)
    assert not list(tmp_path.iterdir())


def test_wp_select_cli_refuses_a_test_season():
    from typer.testing import CliRunner

    from twm.cli import app

    r = CliRunner().invoke(app, ["decisions", "wp-select", "--candidate", "g1", "--season",
                                 "2010"])  # fmt: skip
    assert r.exit_code == 2


def _res(name: str, ll: float, worst: float, failures=(), seasons=ws.VALIDATION_SEASONS):
    sm = {m: 1.0 for m in wsm.METRICS}
    return {name: [{"candidate": name, "season": s, "n": 100, "log_loss": ll, "brier": 0.1,
                    "smooth": sm, "failures": list(failures), "worst_ratio": worst,
                    "seconds": 1.0} for s in seasons]}  # fmt: skip


def test_choose_takes_the_lowest_log_loss_among_smooth_candidates():
    r = {**_res("steppy", 0.40, 3.0, ["curvature"]), **_res("a", 0.43, 0.9),
         **_res("b", 0.42, 0.99), **_res("partial", 0.30, 0.5, seasons=(2004,))}  # fmt: skip
    assert ws.choose(r) == "b"  # steppy breaks a limit; partial lacks 2005
    none_ok = {**_res("x", 0.40, 3.0, ["curvature"]), **_res("y", 0.45, 1.5, ["score_step_h1"])}
    assert ws.choose(none_ok) == "y"  # the smallest worst ratio
    with pytest.raises(ws.SelectionError):
        ws.choose(_res("partial", 0.30, 0.5, seasons=(2005,)))


# --------------------------------------------------------------------------------------
# The model families: deterministic, usable through wp()
# --------------------------------------------------------------------------------------

SMALL = {"num_leaves": 7, "min_child_samples": 5, "n_estimators": 30}


@pytest.fixture(scope="module")
def rows() -> pl.DataFrame:
    from tests.test_decisions_wp import synth_world

    plays, games = synth_world(seasons=(2010, 2011), games=12, plays=60, seed=5)
    return wd.build_states(plays, games).rows


def _xy(rows: pl.DataFrame, features):
    return rows.select(features), rows.get_column(wd.LABEL).to_numpy()


@pytest.mark.parametrize("name", ["bag5", "smooth_feat_bag5", "boost_spline_bag5", "blend"])
def test_ensembles_are_deterministic(rows, name):
    c = ws.CANDIDATES[name]
    x, y = _xy(rows, c.features)
    a = c.make().fit(x, y, SMALL).predict(x)
    b = c.make().fit(x, y, SMALL).predict(x)
    assert np.array_equal(a, b)  # bit-identical
    assert ((a > 0) & (a < 1)).all()


def test_bag_members_differ_and_are_averaged_on_the_logit_scale(rows):
    c = ws.CANDIDATES["bag5"]
    x, y = _xy(rows, c.features)
    m = c.make().fit(x, y, SMALL)
    assert len(m.clfs) == 5
    raw = [k.booster_.predict(x.to_numpy().astype(float), raw_score=True) for k in m.clfs]
    assert not np.array_equal(raw[0], raw[1])  # other seed, other rows and features
    assert np.allclose(m.predict(x), 1 / (1 + np.exp(-np.mean(raw, axis=0))))


def test_tuning_gives_one_tree_count_per_member(rows):
    c = ws.CANDIDATES["bag5"]
    tr, va = rows.filter(pl.col("season") == 2010), rows.filter(pl.col("season") == 2011)
    x, y = _xy(tr, c.features)
    xv, yv = _xy(va, c.features)
    m = c.make().fit(x, y, {**SMALL, "n_estimators": 200}, eval_x=xv, eval_y=yv)
    n = m.refit_params["n_estimators"]
    assert isinstance(n, list) and len(n) == 5 and all(1 <= k <= 200 for k in n)
    again = c.make().fit(x, y, m.refit_params)  # the refit uses each member's count
    assert [k.n_estimators for k in again.clfs] == n


def test_a_spline_model_prices_hypothetical_states_through_wp(rows):
    c = ws.CANDIDATES["spline_logit"]
    x, y = _xy(rows, c.features)
    fitted = c.make().fit(x, y, {"C": 1.0})
    model = wp.WpModel("v", 2012, (2010, 2011), 2011, c.features, {}, fitted, candidate=c.name)
    own, _ = wsm.possession_pairs(2012)  # no smooth features given: wp() derives them
    p = wp.wp(own, model)
    assert p.shape == (own.height,) and ((p > 0) & (p < 1)).all()
    m = wsm.measure(lambda st: wp.wp(st, model), 2012)
    assert m["monotone_violations"] >= 0 and np.isfinite(m["curvature"])


def test_the_symmetric_spline_gives_the_ball_no_value_as_the_half_ends(rows):
    c = ws.CANDIDATES["spline_sym"]
    x, y = _xy(rows, c.features)
    model = wp.WpModel("v", 2012, (2010, 2011), 2011, c.features, {},
                       c.make().fit(x, y, {"C": 1.0}), candidate=c.name)  # fmt: skip
    own, opp = wsm.possession_pairs(2012)
    last = (own.get_column("half_seconds_remaining") == 1).to_numpy()
    value = wp.wp(own, model) - (1.0 - wp.wp(opp, model))
    assert np.abs(value[last]).max() < 1e-12  # exactly symmetric with 1 second left
    early = own.with_columns(pl.lit(900).alias("half_seconds_remaining"),
                             pl.lit(2700).alias("game_seconds_remaining"))  # fmt: skip
    mid = wp.wp(early, model) - (1.0 - wp.wp(wsm.possession_pairs(2012)[1].with_columns(
        pl.lit(900).alias("half_seconds_remaining"),
        pl.lit(2700).alias("game_seconds_remaining")), model))  # fmt: skip
    assert np.abs(mid).max() > 1e-6  # mid-half the ball may be worth something


def test_the_late_gate_hands_over_only_late_in_the_game():
    from twm.modules.decisions.wp_models import late_gate

    st = pl.DataFrame({"half_number": [1, 2, 2, 2, 2, 3],
                       "game_seconds_remaining": [1801, 1500, 900, 750, 600, 400]})  # fmt: skip
    assert late_gate(st).tolist() == [0.0, 0.0, 0.0, 0.5, 1.0, 1.0]


def test_the_hand_over_model_is_the_spline_until_the_fourth_quarter(rows):
    c = ws.CANDIDATES["spline_sym_late_hand_over"]
    x, y = _xy(rows, c.features)
    m = c.make().fit(x, y, SMALL)
    early = x.filter(pl.col("game_seconds_remaining") > 900)
    assert np.array_equal(m.predict(early), m.spline.predict(early))
    late = x.filter((pl.col("game_seconds_remaining") <= 600) & (pl.col("half_number") >= 2))
    assert np.allclose(m.predict(late), m.trees.predict(late))
    again = c.make().fit(x, y, SMALL)
    assert np.array_equal(m.predict(x), again.predict(x))  # deterministic


# --------------------------------------------------------------------------------------
# The regrade comparison (fourth_downs.md section 6)
# --------------------------------------------------------------------------------------


def test_regrade_comparison_counts_what_changed(tmp_path):
    from tests.test_decisions_grade import _graded_season, stub_models
    from twm.modules.decisions import regrade_compare as rc

    after_dir, before_dir = tmp_path / "graded", tmp_path / "graded_g1"
    fourth, tries = _graded_season(stub_models(), after_dir)
    assert rc.compare(fourth, tries, season=2010, min_games=1, before=before_dir) is None
    _graded_season(stub_models(), before_dir)
    same = rc.compare(fourth, tries, season=2010, min_games=1, before=before_dir)
    assert same["fourth"]["changed"] == 0 and same["tries"]["changed"] == 0
    assert same["ranking"]["table"].height >= 1
    # before: one graded try was a clear first-quarter 'mistake' at a 6-point lead
    t = pl.read_parquet(before_dir / "two_point_2010.parquet")
    i = t.with_row_index().filter(pl.col("exclusion").is_null()).get_column("index")[0]
    flip = pl.Series(np.arange(t.height) == i)
    t = t.with_columns(
        pl.when(flip).then(pl.lit("clear")).otherwise(pl.col("grade")).alias("grade"),
        pl.when(flip).then(pl.lit(False)).otherwise(pl.col("correct")).alias("correct"),
        pl.when(flip).then(pl.lit("two_point")).otherwise(pl.col("recommended")).alias(
            "recommended"),
        pl.when(flip).then(1).otherwise(pl.col("qtr")).cast(t.schema["qtr"]).alias("qtr"),
        pl.when(flip).then(6).otherwise(pl.col("score_differential"))
        .cast(t.schema["score_differential"]).alias("score_differential"),
    )  # fmt: skip
    t.write_parquet(before_dir / "two_point_2010.parquet")
    c = rc.compare(fourth, tries, season=2010, min_games=1, before=before_dir)
    assert c["tries"]["q1_six_before"] == 1 + same["tries"]["q1_six_before"]
    assert c["tries"]["changed"] >= 1 and c["fourth"]["changed"] == 0


# --------------------------------------------------------------------------------------
# Real data (opt-in: -m realdata; never downloads)
# --------------------------------------------------------------------------------------


def _real_db():
    from twm.config import settings

    db = settings().path("warehouse")
    if not db.exists():
        pytest.skip("run `uv run twm build` first")
    return db


@pytest.mark.realdata
def test_real_drive_ep_table_is_what_1999_2005_measure():
    t = wd.measure_drive_ep_table(_real_db())
    assert np.abs(t - np.array(wd.DRIVE_EP_TABLE)).max() <= 0.005 + 1e-9  # rounded to 0.01


@pytest.mark.realdata
def test_real_half_value_table_is_what_1999_2005_measure():
    t = wd.measure_half_value_table(_real_db())
    assert np.abs(t - np.array(wd.HALF_VALUE_TABLE)).max() <= 0.005 + 1e-9


@pytest.mark.realdata
def test_real_chosen_family_is_the_rule_choice_and_every_fold_is_smooth():
    _real_db()
    results = ws.load_results()
    if not ws.summarize(results):
        pytest.skip("run `uv run twm decisions wp-select` for every candidate first")
    assert ws.choose(results) == ws.CHOSEN
    import json

    for s in wp.fold_seasons():
        f = wp.backtest_dir() / f"fold_{s}.json"
        if not f.exists():
            pytest.skip("run `uv run twm decisions wp-backtest` first")
        summary = json.loads(f.read_text())
        assert summary["candidate"] == ws.CHOSEN
        assert wsm.failures(summary["smoothness"]) == [], s


@pytest.mark.realdata
def test_real_frozen_g1_numbers_match_the_kept_g1_models():
    from twm.modules.decisions import wp_smooth_report as wsr

    g1 = pl.read_csv(wsr.g1_csv())
    for s in (2010, 2025):
        v = g1.filter((pl.col("season") == s) & (pl.col("metric") == "score_step_h1"))
        path = wp.models_dir() / f"{v['model_version'][0]}.joblib"
        if not path.exists():
            pytest.skip("G1's model files are not on this machine")
        m = wp.load_model(path)
        assert wsm.measure(lambda st, m=m: wp.wp(st, m), s)["score_step_h1"] == pytest.approx(
            v["value"][0], abs=1e-3)  # fmt: skip


def test_rank_moves_are_signed():
    from twm.modules.decisions import regrade_compare as rc

    seasons = pl.DataFrame({"season": [2010] * 3, "coach": ["A", "B", "C"], "team": ["X"] * 3,
                            "games": [10] * 3, "wp_lost_per_game": [0.3, 0.1, 0.2]})  # fmt: skip
    r = rc._rank(seasons, 2010, 1)
    assert r.schema["rank"] == pl.Int64 and r.get_column("coach").to_list() == ["B", "C", "A"]
    assert (r.get_column("rank") - 3).min() == -2  # no unsigned wrap-around
