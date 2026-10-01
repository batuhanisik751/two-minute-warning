"""G2: the fourth-down sub-models (twm.modules.decisions.submodels, conversion, fieldgoal, punt,
tries, submodels_report).

Offline, on synthetic plays (:func:`synth_plays`: games of third downs, fourth-down punts,
field goals and tries, with the columns ``submodels.load_plays`` returns): the next-snap
reader, labels and filters, weather parsing and train-only imputation, the miss rule, punt
distributions that sum to 1, expected WP through G1's ``wp()``, era rules for the try rates,
the walk-forward guard, determinism, save/load and the report. Real data (``-m realdata``):
league-wide sanity checks computed from the warehouse.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from twm.modules.decisions import conversion as cv
from twm.modules.decisions import fieldgoal as fg
from twm.modules.decisions import punt as pu
from twm.modules.decisions import submodels as sm
from twm.modules.decisions import tries as tr

WEATHER = (("outdoors", 50, 10, None, "grass"), ("dome", None, None, None, "fieldturf"),
           ("outdoors", None, None, "Cloudy Temp: 41° F, Humidity: 70%, Wind: 1 E mph", "grass "),
           ("outdoors", None, None, None, ""),
           ("open", None, None, "Temp: 66° F, Wind: Calm", "astroturf"))  # fmt: skip


def synth_plays(seasons=(2003, 2004, 2005, 2006), games: int = 6, drives: int = 16, seed: int = 7):
    """Raw play rows shaped like ``submodels.load_plays`` (game columns joined, before the
    next-snap columns): per game an opening kickoff, then drives of a 3rd-down play and a
    4th-down decision (go, punt or field goal), each drive starting where the last one left
    the ball; a timeout row, a no_play row and a kneel are sprinkled in; tries at the end."""
    rng = np.random.default_rng(seed)
    out = []
    for season in seasons:
        for g in range(games):
            gid, home, away = f"{season}_{g:02d}_A{g}_H{g}", f"H{g}", f"A{g}"
            roof, temp, wind, text, surface = WEATHER[g % len(WEATHER)]
            game = dict(game_id=gid, season=season, week=1 + g, season_type="REG",
                        home_team=home, away_team=away, spread_line=float(g % 3 - 1),
                        location="Home", result=3, roof=roof, temp=temp, wind=wind,
                        weather=text, surface=surface, posteam_timeouts_remaining=3,
                        defteam_timeouts_remaining=3)  # fmt: skip
            pid = [0]

            def add(_game=game, _pid=pid, **kw):
                _pid[0] += 1
                gsr = max(0, 3600 - 100 * _pid[0])
                row = {**_game, "play_id": _pid[0], "qtr": min(4, 1 + (3600 - gsr) // 900),
                       "game_half": "Half1" if gsr > 1800 else "Half2",
                       "game_seconds_remaining": gsr, "half_seconds_remaining": gsr % 1800,
                       "score_differential": 0, "kickoff_attempt": 0, **kw}  # fmt: skip
                out.append(row)

            add(down=None, play_type="kickoff", kickoff_attempt=1, posteam=away, defteam=home)
            off, de, yl = away, home, 75
            for d in range(drives):
                ytg = int(rng.integers(1, 12))
                conv = rng.random() < 1 / (1 + np.exp(-(1.0 - 0.25 * ytg)))
                base = dict(posteam=off, defteam=de, ydstogo=ytg, yardline_100=yl)
                add(down=3, play_type="pass" if d % 2 else "run", first_down=int(conv),
                    yards_gained=ytg + 2 if conv else 1, **base)  # fmt: skip
                if conv:
                    yl = max(1, yl - ytg - 2)
                    add(down=1, play_type="run", ydstogo=10, yardline_100=yl, posteam=off,
                        defteam=de, yards_gained=0)  # fmt: skip
                if d == 3:
                    add(down=None, play_type="no_play", posteam=None, defteam=None)  # timeout
                    add(down=4, play_type="no_play", penalty=1, **{**base, "yardline_100": yl})
                choice = ("punt", "field_goal", "go")[d % 3] if yl > 2 else "go"
                base = dict(posteam=off, defteam=de, ydstogo=ytg, yardline_100=yl)
                if choice == "punt":
                    spot = int(np.clip(100 - yl + 40 + rng.integers(-8, 9), 1, 99))
                    spot = 80 if spot > 95 else spot
                    add(down=4, play_type="punt", **base)
                    off, de, yl = de, off, spot
                elif choice == "field_goal":
                    made = rng.random() < 1 / (1 + np.exp((yl + 18 - 45) / 6))
                    add(down=4, play_type="field_goal", field_goal_result="made" if made
                        else "missed", **base)  # fmt: skip
                    if made:
                        add(down=None, play_type="kickoff", kickoff_attempt=1, posteam=de,
                            defteam=off)  # fmt: skip
                    off, de, yl = de, off, 75 if made else min(80, 92 - yl)
                else:
                    ok = rng.random() < 1 / (1 + np.exp(-(1.2 - 0.3 * ytg)))
                    td = ok and yl <= ytg
                    add(
                        down=4,
                        play_type="run",
                        first_down=int(ok),
                        touchdown=int(td),
                        td_team=off if td else None,
                        yards_gained=ytg if ok else 0,
                        **base,
                    )
                    if not ok:
                        off, de, yl = de, off, 100 - yl
                    elif td:
                        add(down=None, play_type="extra_point", extra_point_attempt=1,
                            extra_point_result="good", posteam=off, defteam=de)  # fmt: skip
                        off, de, yl = de, off, 75
                    else:
                        yl = max(1, yl - ytg)
            add(down=3, play_type="qb_kneel", posteam=off, defteam=de, ydstogo=5, yardline_100=yl)
            for i in range(4):
                add(down=None, play_type="extra_point", extra_point_attempt=1,
                    extra_point_result="good" if (i + g) % 9 else "failed", posteam=home,
                    defteam=away)  # fmt: skip
            add(down=None, play_type="pass", two_point_attempt=1, two_point_conv_result="success"
                if g % 2 else "failure", posteam=home, defteam=away)  # fmt: skip
    frame = pl.DataFrame(out, infer_schema_length=None)
    for c in sm._PLAY_COLS:
        if c not in frame.columns:
            frame = frame.with_columns(pl.lit(None).alias(c))
    ints = ("first_down", "touchdown", "interception", "fumble_lost", "penalty", "yards_gained",
            "extra_point_attempt", "two_point_attempt", "goal_to_go", "down", "ydstogo",
            "yardline_100", "temp", "wind")  # fmt: skip
    strs = ("td_team", "field_goal_result", "extra_point_result", "two_point_conv_result",
            "weather", "posteam", "defteam")  # fmt: skip
    return frame.with_columns([pl.col(c).cast(pl.Int32) for c in ints]
                              + [pl.col(c).cast(pl.String) for c in strs]).sort(
        "game_id", "play_id")  # fmt: skip


@pytest.fixture(scope="module")
def plays():
    return sm.add_next_snap(synth_plays())


def test_next_snap_skips_non_snaps_and_never_crosses_games():
    p = pl.DataFrame({
        "game_id": ["g1"] * 4 + ["g2"],
        "play_id": [1, 2, 3, 4, 1],
        "down": [4, None, 1, 4, 1],
        "posteam": ["A", None, "B", "B", "C"],
        "yardline_100": [60, None, 75, 40, 70],
        "game_half": ["Half1"] * 5,
        "game_seconds_remaining": [3000, 2990, 2980, 2900, 3600],
    })  # fmt: skip
    n = sm.add_next_snap(p)
    assert n.get_column("n_play_id").to_list() == [3, 3, 4, None, None]
    assert n.get_column("n_posteam").to_list() == ["B", "B", "B", None, None]
    assert n.get_column("n_yardline_100").to_list() == [75, 75, 40, None, None]


def test_conversion_label_and_filters():
    base = dict(game_id="g", season=2010, week=1, posteam="A", defteam="B", home_team="A",
                spread_line=1.0, ydstogo=3, yardline_100=40, score_differential=0,
                game_seconds_remaining=1000, half_seconds_remaining=1000, game_half="Half2",
                goal_to_go=0, td_team=None, first_down=0, touchdown=0, interception=0,
                fumble_lost=0, yards_gained=0)  # fmt: skip
    cases = [  # (down, play_type, overrides, expected label or None = dropped)
        (4, "run", {"first_down": 1, "yards_gained": 4}, 1),
        (4, "pass", {"touchdown": 1, "td_team": "A"}, 1),  # TD counts
        (4, "pass", {"first_down": 1, "interception": 1}, 0),  # gained it, lost the ball
        (3, "pass", {"first_down": 1, "yards_gained": 1}, 1),  # defensive penalty 1st down
        (4, "run", {"touchdown": 1, "td_team": "B", "fumble_lost": 1}, 0),  # return TD
        (4, "no_play", {"first_down": 1}, None),
        (3, "qb_kneel", {}, None),
        (2, "run", {"first_down": 1}, None),
        (4, "run", {"ydstogo": 0}, None),
    ]
    rows = [{**base, **o, "play_id": i, "down": d, "play_type": t}
            for i, (d, t, o, _) in enumerate(cases)]  # fmt: skip
    frame = sm.add_next_snap(pl.DataFrame(rows, infer_schema_length=None).with_columns(
        pl.lit(None, pl.String).alias("location")))  # fmt: skip
    out, drops = cv.build_rows(frame)
    assert drops == {"not_third_or_fourth_down": 1, "not_a_pass_or_run": 2, "missing_state": 1}
    want = [lab for *_, lab in cases if lab is not None]
    assert out.sort("play_id").get_column(cv.LABEL).to_list() == want
    assert out.get_column("is_fourth_down").to_list() == [1, 1, 1, 0, 1]


def test_conversion_complete_derives_flags_for_hypothetical_states():
    s = cv.complete(pl.DataFrame({"down": [4, 3], "ydstogo": [2, 5], "yardline_100": [2, 40],
                                  "season": [2014, 2024]}))  # fmt: skip
    assert s.get_column("goal_to_go").to_list() == [1, 0]
    assert s.get_column("is_fourth_down").to_list() == [1, 0]
    assert s.get_column("era_pat_2015").to_list() == [0, 1]
    assert s.get_column("era_kickoff_2023").to_list() == [0, 1]


def test_weather_conditions_parse_text_and_flag_missing():
    g = pl.DataFrame(
        [dict(zip(("roof", "temp", "wind", "weather", "surface"), w, strict=True))
         for w in WEATHER],
        schema={"roof": pl.String, "temp": pl.Int32, "wind": pl.Int32, "weather": pl.String,
                "surface": pl.String},
    ).with_columns(fg.conditions())  # fmt: skip
    assert g.get_column("roof_closed").to_list() == [0, 1, 0, 0, 0]
    assert g.get_column("temp_f").to_list() == [50.0, 70.0, 41.0, None, 66.0]
    assert g.get_column("wind_mph").to_list() == [10.0, 0.0, 1.0, None, 0.0]  # "1 E mph", calm
    assert g.get_column("weather_missing").to_list() == [0, 0, 0, 1, 0]
    assert g.get_column("surface_grass").to_list() == [1.0, 0.0, 1.0, None, 0.0]  # 'grass '


def test_imputation_uses_training_rows_only():
    est = fg.estimator()
    x = pl.DataFrame({"fg_distance": [30.0, 40, 50, 35, 45, 33], "roof_closed": [0, 0, 0, 1, 0, 0],
                      "wind_mph": [4.0, 8, 30, 0, None, 6], "temp_f": [40.0, 60, 80, 70, None, 50],
                      "surface_grass": [1.0, 0, 1, 0, 1, None],
                      "weather_missing": [0, 0, 0, 0, 1, 0],
                      "era_pat_2015": [0] * 6, "era_kickoff_2023": [0] * 6})  # fmt: skip
    m = est.fit(x, np.array([1, 1, 0, 1, 0, 1]), est.default_params)
    # medians of OUTDOOR games with known weather (the dome's 70 F / 0 mph are left out)
    assert m.fill == {"temp_f": 55.0, "wind_mph": 7.0, "surface_grass": 1.0}
    test = x.head(1).with_columns(pl.lit(None, pl.Float64).alias("wind_mph"))
    filled = m.prepare(test)
    assert filled.get_column("wind_mph").item() == 7.0  # never the test rows' own values


def test_miss_spot_rule():
    s = pl.DataFrame({"yardline_100": [3, 12, 13, 30, 60]})
    assert fg.miss_spot(s).to_list() == [80, 80, 79, 62, 32]


def test_punt_results_come_from_the_next_snap(plays):
    rows, drops = pu.build_rows(plays)
    assert set(rows.get_column("outcome").unique()) <= set(pu.OUTCOMES)
    assert drops["other_result"] >= 0 and rows.height > 50
    recv = rows.filter(pl.col("outcome") == "receiving")
    assert recv.get_column("spot").is_between(1, 99).all()
    # the synthetic receiving spot is 100 - yardline + 40 +- 8 (or a touchback at 80)
    gap = recv.select((pl.col("spot") - (140 - pl.col("yardline_100"))).abs()).to_series()
    assert ((gap <= 8) | (recv.get_column("spot") == 80)).all()


def test_punt_distribution_sums_to_one_everywhere(plays):
    rows, _ = pu.build_rows(plays)
    for bw in (0.0, 1.5, 5.0):
        t = pu.table(rows, bw)
        assert t.shape == (99, pu.N_CELLS)
        np.testing.assert_allclose(t.sum(axis=1), 1.0)
    # an unseen yardline with no neighbour gets the pooled distribution
    lone = rows.head(1).with_columns(pl.lit(50).alias("yardline_100"))
    t = pu.table(lone, 1.5)
    np.testing.assert_allclose(t[0], t[98])
    model = sm.SubModel("v", pu.MODEL, 2006, (2005,), None, ("yardline_100",), {},
                        extras={"table": pu.table(rows, 3.0), "runoff_seconds": 9.0})  # fmt: skip
    d = pu.distribution(pl.DataFrame({"yardline_100": [35, 60, 95]}), model)
    sums = d.group_by("row").agg(pl.col("prob").sum()).sort("row").get_column("prob")
    np.testing.assert_allclose(sums.to_numpy(), 1.0)
    assert d.filter(pl.col("outcome").str.ends_with("td")).get_column("spot").null_count() == (
        d.filter(pl.col("outcome").str.ends_with("td")).height
    )
    with pytest.raises(sm.SubModelError):
        pu.distribution(pl.DataFrame({"yardline_100": [0]}), model)


def test_punt_log_score_floors_impossible_results(plays):
    rows, _ = pu.build_rows(plays)
    t = pu.table(rows.filter(pl.col("outcome") == "receiving"), 3.0)
    odd = rows.head(1).with_columns(pl.lit("kicking_td").alias("outcome"),
                                    pl.lit(None, pl.Int32).alias("spot"))  # fmt: skip
    assert pu.log_score(odd, t)[0] == pytest.approx(np.log(pu.FLOOR))


class _StubFitted:
    """WP = logistic(score difference / 7 + (50 - yardline) / 40): more lead and better field
    position help the offense."""

    def predict(self, x):
        z = (
            x.get_column("score_differential").to_numpy() / 7
            + (50 - x.get_column("yardline_100").to_numpy()) / 40
        )
        return 1 / (1 + np.exp(-z))


def stub_wp_model():
    from twm.modules.decisions import wp
    from twm.modules.decisions.wp_data import FEATURES

    return wp.WpModel("stub", 2006, (2005,), None, tuple(FEATURES), {}, fitted=_StubFitted())


def _state(**kw):
    base = dict(season=2010, score_differential=0, game_seconds_remaining=1500,
                half_seconds_remaining=1500, half_number=2, posteam_timeouts_remaining=3,
                defteam_timeouts_remaining=2, receives_2h_kickoff=0, posteam_is_home=1.0,
                posteam_spread=3.0, yardline_100=60)  # fmt: skip
    return pl.DataFrame([{**base, **kw}])


def test_expected_wp_flips_the_receiving_team_state():
    wm = stub_wp_model()
    table = np.zeros((99, pu.N_CELLS))
    table[:, 70 - 1] = 1.0  # every punt: the receiving team starts at its own 30 (yardline 70)
    model = sm.SubModel("v", pu.MODEL, 2010, (2009,), None, ("yardline_100",), {},
                        extras={"table": table, "runoff_seconds": 9.0})  # fmt: skip
    st = _state(score_differential=4)
    got = pu.expected_wp(st, model, wm)[0]
    # receiving team: down 4, ball at yardline 70 -> its WP = logistic(-4/7 + (50-70)/40)
    want = 1 - 1 / (1 + np.exp(-(-4 / 7 + (50 - 70) / 40)))
    assert got == pytest.approx(want)
    other = sm.other_side(sm.other_side(st.with_columns(pl.lit(1).alias("half_number"))))
    assert other.equals(st.with_columns(pl.lit(1).alias("half_number")))
    # a return touchdown is worse for the kicker than any receiving spot
    table2 = np.zeros((99, pu.N_CELLS))
    table2[:, 198] = 1.0
    m2 = sm.SubModel("v", pu.MODEL, 2010, (2009,), None, ("yardline_100",), {},
                     extras={"table": table2, "runoff_seconds": 9.0})  # fmt: skip
    assert pu.expected_wp(st, m2, wm)[0] < got


def test_conversion_ball_spot_tables_sum_to_one(plays):
    rows, _ = cv.build_rows(plays)
    t = cv.outcome_tables(rows)
    model = sm.SubModel("v", cv.MODEL, 2006, (2005,), None, cv.FEATURES, {}, extras=t)
    st = pl.DataFrame({"ydstogo": [1, 10, 3], "yardline_100": [1, 75, 30]})
    for f in (cv.after_success, cv.after_failure):
        d = f(st, model)
        s = d.group_by("row").agg(pl.col("prob").sum()).sort("row").get_column("prob")
        np.testing.assert_allclose(s.to_numpy(), 1.0)
    a = cv.after_success(st, model)
    # from the 1, every conversion is a touchdown
    assert a.filter(pl.col("row") == 0).get_column("outcome").to_list() == ["touchdown"]
    f = cv.after_failure(st, model).filter(pl.col("outcome") == "opp_ball")
    assert f.get_column("opp_yardline_100").is_between(1, 99).all()


def test_try_rates_follow_the_rule_eras():
    rows = []
    for season in range(2008, 2017):
        rows += [{"season": season, "game_id": f"{season}_x", "play_id": i, "week": 1,
                  "kind": "pat", "success": int(i % (100 if season < 2015 else 16) != 0)}
                 for i in range(200)]  # fmt: skip
        rows += [
            {
                "season": season,
                "game_id": f"{season}_x",
                "play_id": 1000 + i,
                "week": 1,
                "kind": "fg_equivalent",
                "success": int(i % 10 != 0),
            }
            for i in range(50)
        ]
        rows += [
            {
                "season": season,
                "game_id": f"{season}_x",
                "play_id": 2000 + i,
                "week": 1,
                "kind": "two_point",
                "success": i % 2,
            }
            for i in range(20)
        ]
    f = pl.DataFrame(rows).with_columns((pl.col("season") >= 2015).cast(pl.Int8)
                                        .alias("era_pat_2015"))  # fmt: skip
    r14, r15, r16 = (tr.rates(f, s) for s in (2014, 2015, 2016))
    assert r14["pat"]["rate"] == pytest.approx(0.99) and r14["pat"]["seasons"] == list(
        range(2009, 2014))  # fmt: skip
    assert "field goals" in r15["pat"]["source"] and r15["pat"]["rate"] == pytest.approx(0.9)
    assert r16["pat"]["seasons"] == [2015] and r16["pat"]["rate"] == pytest.approx(187 / 200)
    assert r16["two_point"]["rate"] == pytest.approx(0.5)
    lo, hi = r16["pat"]["lo"], r16["pat"]["hi"]
    assert lo < r16["pat"]["rate"] < hi


@pytest.fixture
def fast(monkeypatch):
    """Small grids and one training window (the synthetic world is tiny)."""
    import dataclasses

    monkeypatch.setattr(cv, "GRID", ({"num_leaves": 4, "min_child_samples": 20,
                                      "n_estimators": 200},))  # fmt: skip
    monkeypatch.setattr(fg, "GRID", ({"num_leaves": 4, "min_child_samples": 10,
                                      "n_estimators": 200},))  # fmt: skip
    monkeypatch.setattr(cv, "SPEC", dataclasses.replace(cv.SPEC, windows=(None, 2)))
    monkeypatch.setattr(fg, "SPEC", dataclasses.replace(fg.SPEC, windows=(None,)))


def test_harness_refuses_the_test_season(plays, fast):
    from twm.backtest.walkforward import TestSeasonInTrainingError, fit_fold, plan_folds

    rows, _ = cv.build_rows(plays)
    fold = plan_folds(rows.get_column("season").unique().to_list(), [2006])[0]
    with pytest.raises(TestSeasonInTrainingError):
        fit_fold(rows, fold, estimator=cv.estimator(), features=cv.FEATURES, label=cv.LABEL,
                 module="decisions", keys=sm.KEYS, tune_metric=lambda v: 0.0)  # fmt: skip


def _flip(rows: pl.DataFrame, col: str, season: int = 2006) -> pl.DataFrame:
    return rows.with_columns(pl.when(pl.col("season") == season).then(1 - pl.col(col))
                             .otherwise(pl.col(col)).cast(pl.Int8).alias(col))  # fmt: skip


def test_folds_never_see_the_test_season_and_are_deterministic(plays, fast):
    quiet = {"progress": lambda *_: None}
    rows, _ = cv.build_rows(plays)
    a, b = cv.fit_fold(rows, 2006, **quiet), cv.fit_fold(rows, 2006, **quiet)
    assert a.model.version == b.model.version and a.predictions.equals(b.predictions)
    assert max(a.model.train_seasons) < 2006
    assert [w["window"] for w in a.summary["windows"]] == [None, 2]
    c = cv.fit_fold(_flip(rows, cv.LABEL), 2006, **quiet)  # the test season's labels flipped
    assert c.model.version == a.model.version
    assert c.predictions.get_column("prob").equals(a.predictions.get_column("prob"))
    frows, _ = fg.build_rows(plays)
    f1, f2 = fg.fit_fold(frows, 2006, **quiet), fg.fit_fold(_flip(frows, fg.LABEL), 2006, **quiet)
    assert f1.predictions.get_column("prob").equals(f2.predictions.get_column("prob"))
    assert "weather" in f1.summary["ablation"]
    prows, _ = pu.build_rows(plays)
    p2 = prows.with_columns(pl.when(pl.col("season") == 2006).then(pl.lit("return_td"))
                            .otherwise(pl.col("outcome")).alias("outcome"))  # fmt: skip
    t1, t2 = pu.fit_fold(prows, 2006, **quiet), pu.fit_fold(p2, 2006, **quiet)
    np.testing.assert_array_equal(t1.model.extras["table"], t2.model.extras["table"])
    trows, _ = tr.build_rows(plays)
    assert tr.rates(trows, 2006) == tr.rates(_flip(trows, "success"), 2006)


def test_save_load_and_backtest_reuse(plays, fast, tmp_path):
    rows, _ = cv.build_rows(plays)
    kw = {"models_root": tmp_path / "m", "out_dir": tmp_path / "f", "progress": lambda *_: None}
    cols = cv.SPEC.hash_columns
    assert sm.run_backtest(rows, cv.MODEL, cv.fit_fold, cols, [2006], **kw) == [2006]
    assert sm.run_backtest(rows, cv.MODEL, cv.fit_fold, cols, [2006], **kw) == []  # reused
    m = sm.load_fold_model(cv.MODEL, 2006, models_root=tmp_path / "m", out_dir=tmp_path / "f")
    test = rows.filter(pl.col("season") == 2006)
    saved = pl.read_parquet(tmp_path / "f" / "fold_2006.parquet")
    np.testing.assert_array_equal(cv.p_convert(test, m), saved.get_column("prob").to_numpy())
    changed = _flip(rows, cv.LABEL, 2004)  # a TRAINING season changed: the fold is refit
    assert sm.run_backtest(changed, cv.MODEL, cv.fit_fold, cols, [2006], **kw) == [2006]
    (tmp_path / "junk.joblib").write_bytes(b"not a model")
    with pytest.raises(Exception):  # noqa: B017 - joblib's own error for a non-pickle
        sm.load_model(tmp_path / "junk.joblib")
    with pytest.raises(sm.SubModelError):
        sm.load_model(tmp_path / "missing.joblib")
    with pytest.raises(sm.SubModelError, match="no conversion_lgbm fold for 2005"):
        sm.load_fold_model(cv.MODEL, 2005, models_root=tmp_path / "m", out_dir=tmp_path / "f")


def test_backtest_and_report_end_to_end(plays, fast, tmp_path):
    from twm.modules.decisions import submodels_report as rep
    from twm.modules.decisions.cli import SUBMODEL_NAMES, submodel_parts

    rows, drops, dirs = {}, {}, {}
    for n in SUBMODEL_NAMES:
        mod, model, cols = submodel_parts(n)
        rows[n], drops[n] = mod.build_rows(plays)
        dirs[model] = tmp_path / "folds" / model
        sm.run_backtest(rows[n], model, mod.fit_fold, cols, [2005, 2006],
                        models_root=tmp_path / "m", out_dir=dirs[model],
                        progress=lambda *_: None)  # fmt: skip
    paths = (tmp_path / "r.md", tmp_path / "r.csv")
    kw = dict(drops=drops, seasons=[2005, 2006], paths=paths, models_root=tmp_path / "m",
              out_dirs=dirs, wp_dir=tmp_path / "no_wp", progress=lambda *_: None)  # fmt: skip
    md, csv = rep.write_report(rows, **kw)
    text = md.read_text()
    for head in ("## 1. Go for it", "## 2. Field goals", "## 3. Punts", "## 4. Extra points"):
        assert head in text
    t = pl.read_csv(csv)
    assert set(t.get_column("table").unique()) == {"conversion", "fieldgoal", "punt", "tries"}
    assert t.filter(pl.col("metric") == "log_loss").height > 0
    first = md.read_bytes()
    rep.write_report(rows, **kw)
    assert md.read_bytes() == first  # deterministic


def test_scoring_functions_refuse_bad_states(plays, fast):
    quiet = {"progress": lambda *_: None}
    crow, _ = cv.build_rows(plays)
    m = cv.fit_fold(crow, 2006, **quiet).model
    st = crow.filter(pl.col("season") == 2006).head(3)
    assert ((cv.p_convert(st, m) > 0) & (cv.p_convert(st, m) < 1)).all()
    with pytest.raises(sm.SubModelError, match="lack columns"):
        cv.p_convert(st.drop("ydstogo", "score_differential"), m)
    with pytest.raises(sm.SubModelError):
        cv.p_convert(st.with_columns(pl.lit(0).alias("ydstogo")), m)
    frow, _ = fg.build_rows(plays)
    fm = fg.fit_fold(frow, 2006, **quiet).model
    hypo = pl.DataFrame({"yardline_100": [10, 35], "season": [2006, 2006],
                         "roof": ["dome", "outdoors"], "temp": [None, 20], "wind": [None, 25],
                         "weather": [None, None], "surface": ["fieldturf", "grass"]})  # fmt: skip
    p = fg.p_make(hypo, fm)
    assert p[0] >= p[1]  # monotone: shorter, indoors, no wind can never be worse
    with pytest.raises(sm.SubModelError):
        fg.p_make(hypo.with_columns(pl.lit(0).alias("yardline_100")), fm)


def test_cli_rejects_bad_arguments():
    from typer.testing import CliRunner

    from twm.cli import app

    r = CliRunner().invoke(app, ["decisions", "submodels-backtest", "--season", "2030"])
    assert r.exit_code != 0
    r = CliRunner().invoke(app, ["decisions", "submodels-backtest", "--only", "kickoff",
                                 "--season", "2010"])  # fmt: skip
    assert r.exit_code != 0


# --------------------------------------------------------------------------------------
# Real data (opt-in: -m realdata; never downloads)
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_plays():
    from twm.config import settings

    db = settings().path("warehouse")
    if not db.exists():
        pytest.skip("run `uv run twm build` first")
    return sm.load_plays(db, tuple(range(2006, 2026)))


@pytest.mark.realdata
def test_real_fg_make_rate_falls_with_distance_in_every_era(real_plays):
    rows, _ = fg.build_rows(real_plays)
    bands = (
        rows.with_columns(
            pl.when(pl.col("season") < 2015)
            .then(pl.lit("2006-14"))
            .when(pl.col("season") < 2023)
            .then(pl.lit("2015-22"))
            .otherwise(pl.lit("2023-25"))
            .alias("era"),
            (pl.col("fg_distance").clip(20, 64) // 10).alias("band"),
        )
        .group_by("era", "band")
        .agg(pl.col(fg.LABEL).mean().alias("rate"), pl.len())
    )
    for era in bands.get_column("era").unique():
        r = bands.filter(pl.col("era") == era).sort("band").get_column("rate").to_list()
        assert all(a > b for a, b in zip(r, r[1:], strict=False)), (era, r)
    # the weather columns are filled for all but a handful of outdoor kicks
    assert rows.get_column("weather_missing").mean() < 0.01


@pytest.mark.realdata
def test_real_fourth_and_one_rate_is_plausible(real_plays):
    rows, _ = cv.build_rows(real_plays)
    fourth = rows.filter(pl.col("down") == 4)
    by = fourth.group_by(pl.col("ydstogo").clip(1, 3).alias("d")).agg(pl.col(cv.LABEL).mean())
    rate = dict(by.iter_rows())
    # computed from the data: 4th and 1 converts more often than 4th and 3+, and in a range
    # that public figures share (well above a coin flip, well below a sure thing)
    assert rate[1] > rate[2] > rate[3]
    assert 0.55 < rate[1] < 0.8
    third_one = rows.filter((pl.col("down") == 3) & (pl.col("ydstogo") == 1))
    assert abs(rate[1] - third_one.get_column(cv.LABEL).mean()) < 0.1
