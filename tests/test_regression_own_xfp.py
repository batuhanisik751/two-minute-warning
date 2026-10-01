"""Regression Watch's own walk-forward xFP (step H6-b): synthetic plays only.

- a fold never learns from its test season (guard + perturbing the test season and later ones
  leaves the fold's predictions unchanged);
- the own expectations are scored exactly as D1 scores ffopportunity's (same SQL);
- determinism (two runs, identical outputs);
- the xFP-source parameter leaves the ffopportunity path untouched.
"""

from __future__ import annotations

from datetime import datetime

import duckdb
import numpy as np
import polars as pl
import pytest
from polars.testing import assert_frame_equal

from tests.test_regression_watch import world  # noqa: F401  (the synthetic warehouse fixture)
from twm.backtest.walkforward import Fold, TestSeasonInTrainingError
from twm.modules.regression_watch import own_xfp as ox
from twm.modules.regression_watch import player_week as pw
from twm.modules.regression_watch.plays import play_expected_sql
from twm.scoring import full_ppr, xfp_sql

T0 = datetime(2025, 9, 1)
LOCS = ("left", "middle", "right", None)


def _passes(season: int, n: int, rng: np.random.Generator) -> pl.DataFrame:
    air = rng.integers(-3, 40, n)
    yl = rng.integers(1, 99, n)
    p_catch = 1 / (1 + np.exp(-(1.2 - 0.06 * air)))
    caught = (rng.random(n) < p_catch).astype(int)
    yac = np.where(caught == 1, rng.integers(0, 15, n), 0)
    td = ((caught == 1) & (air + yac >= yl)).astype(int)
    two = (np.arange(n) % 40 == 39).astype(int)
    return pl.DataFrame({
        "season": season, "week": 1 + np.arange(n) % 17, "season_type": "REG",
        "game_id": [f"{season}_{1 + i % 17:02d}_KC_BUF" for i in range(n)],
        "play_id": np.arange(1, n + 1), "posteam": "KC",
        "passer_player_id": "QB1",
        "receiver_player_id": [("WR1", "TE1", None)[i % 3] for i in range(n)],
        "two_point_attempt": two, "is_2pt": two == 1, "is_fixed": False,
        "two_point_converted": rng.integers(0, 2, n) * two, "complete_pass": caught,
        "pass_touchdown": td, "interception": (rng.random(n) < 0.03).astype(int) * (1 - caught),
        "air_yards": air, "receiving_yards": np.where(caught == 1, air + yac, 0),
        "yac": np.where(caught == 1, yac.astype(float), np.nan),
        "is_garbage_time": np.arange(n) % 5 == 0, "available_at": T0,
        "yardline_100": yl, "down": 1 + np.arange(n) % 4, "ydstogo": rng.integers(1, 15, n),
        "pass_location": [LOCS[i % 4] for i in range(n)], "qtr": 1 + np.arange(n) % 4,
        "score_differential": rng.integers(-21, 21, n),
        "pass_completion_exp": p_catch, "yards_after_catch_exp": 5.0,
        "pass_touchdown_exp": 0.04, "pass_interception_exp": 0.02, "two_point_conv_exp": 0.48,
    }, strict=False).with_columns(pl.col("yac").fill_nan(None))  # fmt: skip


def _rushes(season: int, n: int, rng: np.random.Generator) -> pl.DataFrame:
    yl = rng.integers(1, 99, n)
    yards = np.minimum(rng.poisson(4, n) - 1, yl)
    two = (np.arange(n) % 50 == 49).astype(int)
    kneel = (np.arange(n) % 37 == 0) & (two == 0)
    aborted = (np.arange(n) % 41 == 0) & (two == 0) & ~kneel
    return pl.DataFrame({
        "season": season, "week": 1 + np.arange(n) % 17, "season_type": "REG",
        "game_id": [f"{season}_{1 + i % 17:02d}_KC_BUF" for i in range(n)],
        "play_id": np.arange(5001, 5001 + n), "posteam": "KC",
        "rusher_player_id": [("RB1", "QB1")[i % 2] for i in range(n)],
        "two_point_attempt": two, "is_2pt": two == 1, "is_fixed": kneel | aborted,
        "is_kneel": kneel, "two_point_converted": rng.integers(0, 2, n) * two,
        "rush_touchdown": (yards >= yl).astype(int), "rushing_yards": yards.astype(float),
        "is_garbage_time": np.arange(n) % 4 == 0, "available_at": T0,
        "yardline_100": yl, "down": 1 + np.arange(n) % 4, "ydstogo": rng.integers(1, 15, n),
        "run_location": [LOCS[i % 4] for i in range(n)],
        "run_gap": [("end", "tackle", "guard", None)[i % 4] for i in range(n)],
        "qtr": 1 + np.arange(n) % 4, "score_differential": rng.integers(-21, 21, n),
        "qb_scramble": (np.arange(n) % 9 == 0).astype(int),
        "rush_yards_exp": 4.0, "rush_touchdown_exp": 0.03, "two_point_conv_exp": 0.55,
    }, strict=False)  # fmt: skip


def synthetic_plays(seasons=(2006, 2007, 2008, 2009), n: int = 600) -> dict[str, pl.DataFrame]:
    rng = np.random.default_rng(7)
    out = {}
    for kind, make in (("pass", _passes), ("rush", _rushes)):
        out[kind] = pl.concat([make(s, n, rng) for s in seasons]).sort(list(ox.KEYS))
    return out


def _fold_preds(plays: dict[str, pl.DataFrame], season: int) -> dict[str, pl.DataFrame]:
    seasons = sorted(set(plays["pass"]["season"]))
    preds, _ = ox.predict_fold(plays, season, seasons)
    return preds


@pytest.fixture(scope="module")
def plays() -> dict[str, pl.DataFrame]:
    return synthetic_plays()


@pytest.fixture(scope="module")
def preds_2008(plays) -> dict[str, pl.DataFrame]:
    return _fold_preds(plays, 2008)


# --------------------------------------------------------------------------------------
# A fold never learns from its test season
# --------------------------------------------------------------------------------------


def test_the_harness_refuses_a_fold_that_trains_on_its_test_season(plays):
    leaky = Fold(2008, (2006, 2007, 2008), 2007, (2006,))
    with pytest.raises(TestSeasonInTrainingError):
        ox.fit_component(ox.COMPONENT_BY_NAME["completion"], plays["pass"], leaky)
    with pytest.raises(TestSeasonInTrainingError):
        ox.fit_component(ox.COMPONENT_BY_NAME["rush_2pt"], plays["rush"], leaky)


def _scramble_labels(plays: dict[str, pl.DataFrame], seasons: list[int]) -> dict:
    """Every label of ``seasons`` flipped or shifted (features untouched)."""
    hit = pl.col("season").is_in(seasons)
    out = {}
    for kind, cols in (("pass", ("complete_pass", "pass_touchdown", "interception",
                                 "two_point_converted")),
                       ("rush", ("rush_touchdown", "two_point_converted"))):  # fmt: skip
        f = plays[kind].with_columns(
            pl.when(hit).then(1 - pl.col(c)).otherwise(pl.col(c)).alias(c) for c in cols
        )
        shift = ("yac", "rushing_yards")[kind == "rush"]
        out[kind] = f.with_columns(
            pl.when(hit).then(pl.col(shift) + 30.0).otherwise(pl.col(shift)).alias(shift)
        )
    return out


def test_changing_the_test_season_and_later_ones_leaves_the_fold_unchanged(plays, preds_2008):
    other = _fold_preds(_scramble_labels(plays, [2008, 2009]), 2008)
    for kind in ("pass", "rush"):
        assert_frame_equal(preds_2008[kind], other[kind])


def test_changing_a_training_season_does_move_the_fold(plays, preds_2008):
    other = _fold_preds(_scramble_labels(plays, [2007]), 2008)
    assert not preds_2008["pass"].equals(other["pass"])


def test_fold_predicts_every_play_of_its_season_only(plays, preds_2008):
    for kind in ("pass", "rush"):
        p = preds_2008[kind]
        assert p.height == plays[kind].filter(pl.col("season") == 2008).height
        assert p.select(ox.KEYS).equals(
            plays[kind].filter(pl.col("season") == 2008).select(ox.KEYS)
        )
    r = preds_2008["rush"]
    kneel = r.filter(pl.col("is_kneel"))
    assert kneel.height and (kneel["rush_yards_exp"] == -1.0).all()
    assert (r.filter(pl.col("is_fixed"))["rush_touchdown_exp"] == 0.0).all()
    two = preds_2008["pass"].filter(pl.col("is_2pt"))
    assert (
        two["pass_completion_exp"].is_null().all() and two["two_point_conv_exp"].is_not_null().all()
    )


# --------------------------------------------------------------------------------------
# Scored exactly as D1 scores ffopportunity
# --------------------------------------------------------------------------------------


def _d1_sums(plays: dict[str, pl.DataFrame]) -> pl.DataFrame:
    """D1's own query on tables named as the warehouse's (ffopportunity's values)."""
    con = duckdb.connect()
    con.register("fact_opportunity_pass", plays["pass"])
    con.register("fact_opportunity_rush", plays["rush"])
    inner = play_expected_sql("season_type = 'REG'")
    sql = (f"SELECT game_id, gsis_id, sum({xfp_sql(full_ppr())}) AS xfp FROM ({inner}) "
           "GROUP BY ALL ORDER BY ALL")  # fmt: skip
    out = con.sql(sql).pl()
    con.close()
    return out


def test_scoring_matches_d1_for_identical_inputs(plays):
    mine = ox.player_game_xfp(plays, full_ppr()).select("game_id", "gsis_id", "xfp")
    assert_frame_equal(mine, _d1_sums(plays), check_exact=False, rel_tol=1e-12, abs_tol=1e-9)


def test_own_tables_with_ffopportunitys_values_score_the_same(plays):
    same = {k: v.select(*ox.KEYS, *ox.EXPECTED_COLUMNS[k]) for k, v in plays.items()}
    a = ox.player_game_xfp(ox.own_tables(plays, same), full_ppr())
    assert_frame_equal(a, ox.player_game_xfp(plays, full_ppr()))
    assert (a["xfp_ng"] - (a["xfp"] - a["xfp_garbage"])).abs().max() < 1e-9


def test_own_values_replace_ffopportunitys(plays, preds_2008):
    own = ox.own_tables(plays, preds_2008)
    assert set(own["pass"]["season"]) == {2008}
    joined = own["pass"].join(preds_2008["pass"], on=list(ox.KEYS), suffix="_p")
    assert (joined["pass_completion_exp"] == joined["pass_completion_exp_p"]).all()
    rules = full_ppr()
    pg = ox.player_game_xfp(own, rules)
    wr = own["pass"].filter((pl.col("receiver_player_id") == "WR1") & ~pl.col("is_2pt"))
    want = (wr["pass_completion_exp"] * (1 + 0.1 * (wr["air_yards"]
            + wr["yards_after_catch_exp"])) + 6 * wr["pass_touchdown_exp"]).sum()  # fmt: skip
    two = own["pass"].filter((pl.col("receiver_player_id") == "WR1") & pl.col("is_2pt"))
    want += 2 * two["two_point_conv_exp"].sum()
    got = pg.filter(pl.col("gsis_id") == "WR1")["xfp"].sum()
    assert got == pytest.approx(want)


# --------------------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------------------


def test_two_runs_are_identical(plays, tmp_path):
    import json

    outs = []
    for run in ("a", "b"):
        d = tmp_path / run
        d.mkdir()
        for kind in ("pass", "rush"):
            plays[kind].write_parquet(d / f"inputs_{kind}.parquet")
        ox.run_fold(d, 2009, [2006, 2007, 2008, 2009], "hash")
        meta = json.loads((d / "folds" / "2009.json").read_text())
        outs.append(({k: pl.read_parquet(d / "folds" / f"2009_{k}.parquet")
                      for k in ("pass", "rush")}, meta["components"]))  # fmt: skip
    for kind in ("pass", "rush"):
        assert_frame_equal(outs[0][0][kind], outs[1][0][kind], check_exact=True)
    assert outs[0][1] == outs[1][1]
    assert ox.fold_is_current(tmp_path / "a", 2009, "hash")
    assert not ox.fold_is_current(tmp_path / "a", 2009, "other")


def test_fold_hash_changes_with_the_training_seasons_only(plays):
    h = ox.season_hashes(plays)
    later = {**h, 2009: "changed"}
    assert ox.fold_hash(h, 2008) == ox.fold_hash(later, 2008)
    assert ox.fold_hash(h, 2009) != ox.fold_hash(later, 2009)


# --------------------------------------------------------------------------------------
# The xFP-source parameter leaves the ffopportunity path untouched
# --------------------------------------------------------------------------------------


def test_play_expected_sql_defaults_are_the_warehouse_tables():
    w = "season_type = 'REG' AND season IN (2025)"
    assert play_expected_sql(w) == play_expected_sql(
        w, pass_table="fact_opportunity_pass", rush_table="fact_opportunity_rush"
    )
    other = play_expected_sql(w, pass_table="src_pass", rush_table="src_rush")
    assert "fact_opportunity" not in other
    assert other.replace("src_pass", "fact_opportunity_pass").replace(
        "src_rush", "fact_opportunity_rush"
    ) == play_expected_sql(w)


def _frame() -> pl.DataFrame:
    base = {c: 1.0 for c in pw.COMPONENTS} | {
        "season": 2025, "week": 1, "game_id": "g1", "team": "KC", "position": "WR",
        "position_source": "roster", "fantasy_points": 20.0, "points_ng": 15.0,
        "points_garbage": 5.0, "play_points": 20.0, "n_opportunities": 8,
        "n_opportunities_garbage": 2, "yac": 3.0, "yac_exp": 2.0, "available_at": T0,
    }  # fmt: skip
    ffo = {"xfp": 12.0, "fpoe": 8.0, "xfp_ng": 10.0, "fpoe_ng": 5.0, "xfp_garbage": 2.0,
           "play_xfp": 12.0}  # fmt: skip
    none = dict.fromkeys(ffo)
    rows = [base | ffo | {"gsis_id": "A"}, base | none | {"gsis_id": "B"},
            base | ffo | {"gsis_id": "C"}]  # fmt: skip
    return pl.from_dicts(rows).cast(pw.FRAME_SCHEMA).select(pw.FRAME_COLUMNS)  # type: ignore[arg-type]


def test_with_xfp_replaces_the_expected_side_only():
    frame = _frame()
    assert pw.with_xfp(frame, None) is frame
    exp = {c: 0.5 for c in pw.EXPECTED_COMPONENTS}
    src = pl.from_dicts([
        {"game_id": "g1", "gsis_id": "A", "xfp": 10.0, "xfp_garbage": 1.0, **exp, "yac_exp": 1.5},
        {"game_id": "g1", "gsis_id": "B", "xfp": 3.0, "xfp_garbage": 0.0, **exp, "yac_exp": 1.0},
    ])  # fmt: skip
    out = pw.with_xfp(frame, src)
    a, b, c = (out.row(i, named=True) for i in range(3))
    assert (a["xfp"], a["fpoe"], a["xfp_ng"], a["fpoe_ng"]) == (10.0, 10.0, 9.0, 6.0)
    assert a["play_xfp"] == 10.0 and a["yac_exp"] == 1.5 and a["receptions_exp"] == 0.5
    assert a["receptions"] == 1.0 and a["yac"] == 3.0  # the actual side is the weekly line's
    assert b["xfp"] is None and b["fpoe"] is None and b["receptions_exp"] is None
    assert c["xfp"] == 0.0 and c["xfp_garbage"] == 0.0 and c["play_xfp"] is None
    same = [col for col in pw.FRAME_COLUMNS if col not in pw.XFP_SOURCE_COLUMNS
            and col not in ("fpoe", "xfp_ng", "fpoe_ng", "play_xfp")]  # fmt: skip
    assert_frame_equal(out.select(same), frame.select(same))


def test_loaders_without_a_source_are_the_ffopportunity_frame(world):  # noqa: F811
    from twm.modules.regression_watch import backtest as bt
    from twm.modules.regression_watch import stability as st

    base = pw.player_games_history(world, [2025])
    assert base.filter(pl.col("xfp").is_not_null()).height > 0
    assert_frame_equal(pw.player_games_history(world, [2025], xfp=None), base)
    assert_frame_equal(st.load_frame(world, [2025]), base)
    assert_frame_equal(st.load_frame(world, [2025], xfp=None), base)
    a, b = bt.load_inputs(world, 2025), bt.load_inputs(world, 2025, xfp=None)
    assert_frame_equal(a[0], b[0])
    assert_frame_equal(a[0].filter(pl.col("season") == 2025), base)


def test_ffopportunity_per_play_sums_reproduce_the_frame(world):  # noqa: F811
    plays = ox.load_plays(world, [2025])
    assert plays["pass"].height and plays["rush"].height
    ffo = ox.player_game_xfp(plays)
    base = pw.player_games_history(world, [2025])
    swapped = pw.with_xfp(base, ffo)
    for col in ("play_xfp", "xfp_garbage"):
        assert_frame_equal(swapped.select(col), base.select(col))
    # xFP: the per-play sum (ffopportunity's weekly file is only kept where it has a row)
    both = pl.DataFrame({"s": swapped["xfp"], "p": base["play_xfp"], "w": base["xfp"]})
    both = both.filter(pl.col("w").is_not_null())
    assert both.height > 0 and (both["s"] == both["p"].fill_null(0.0)).all()
    assert swapped.filter(base["xfp"].is_null())["xfp"].is_null().all()
    own = pw.player_games_history(world, [2025], xfp=ffo)
    assert_frame_equal(own, swapped)


# --------------------------------------------------------------------------------------
# The comparisons
# --------------------------------------------------------------------------------------


def test_per_play_table_counts_labelled_plays_and_pairs_the_sources(plays, preds_2008):
    from twm.modules.regression_watch import own_xfp_report as rep

    errors = rep.play_errors(plays, preds_2008)
    labelled = plays["pass"].filter((pl.col("season") == 2008) & ox.fit_mask(
        ox.COMPONENT_BY_NAME["completion"]))  # fmt: skip
    assert errors["completion"].height == labelled.height
    t = rep.per_play_table(errors, n_boot=50)
    row = t.filter((pl.col("component") == "yac") & (pl.col("metric") == "mse")
                   & (pl.col("season") == "all")).row(0, named=True)  # fmt: skip
    e = errors["yac"]
    assert row["own"] == pytest.approx(float(((e["own"] - e["y"]) ** 2).mean()))
    assert row["diff"] == pytest.approx(row["own"] - row["ffo"])
    same = {k: plays[k].filter(pl.col("season") == 2008).select(*ox.KEYS,
            *ox.EXPECTED_COLUMNS[k]) for k in plays}  # fmt: skip
    z = rep.per_play_table(rep.play_errors(plays, same), n_boot=50)
    assert (z["diff"].abs() < 1e-12).all()


def test_stability_comparison_of_a_source_with_itself_is_zero():
    from tests.test_regression_projection import world as projection_world
    from twm.modules.regression_watch import own_xfp_report as rep

    f = projection_world((2009, 2010, 2011))
    t = rep.stability_comparison({"ffopportunity": f, "own": f}, [2009, 2010, 2011], n_boot=50)
    t = t.filter(pl.col("n") >= 3)  # a rate with too few chances has no pairs
    assert t.height >= 20
    assert (t["diff"].abs() < 1e-12).all() and (t["diff_within"].abs() < 1e-12).all()
    shifted = f.with_columns(pl.col("xfp") + 1.0, pl.col("fpoe") - 1.0)
    t2 = rep.stability_comparison({"ffopportunity": f, "own": shifted}, [2009, 2010, 2011],
                                  n_boot=50)  # fmt: skip
    xfp = t2.filter(pl.col("metric").is_in(["xfp", "fpoe"]) & (pl.col("n") >= 3))
    assert (xfp["diff"].abs() < 1e-9).all()  # a constant shift does not change a correlation


@pytest.fixture(scope="module")
def report(plays, preds_2008):
    """Every table of the report from synthetic inputs (D3 on the projection tests' world)."""
    from tests import test_regression_projection as tp
    from twm.modules.regression_watch import own_xfp_report as rep

    f = tp.world()
    own = f.with_columns((pl.col("xfp") * 0.9).alias("xfp")).with_columns(
        (pl.col("fantasy_points") - pl.col("xfp")).alias("fpoe")
    )
    frames = {"ffopportunity": f, "own": own}
    results = {s: tp._run(frames[s]) for s in rep.SOURCES}
    seasons = sorted(set(f["season"]))
    infos = ox.predict_fold(plays, 2008, [2006, 2007, 2008, 2009])[1]
    folds = pl.from_dicts([{**vars(i), "fold_seconds": 1.0} for i in infos])
    pg = ox.player_game_xfp(plays)
    pairs = pl.DataFrame({"season": [2008] * 4, "week": [1, 1, 2, 2], "game_id": list("abcd"),
                          "gsis_id": ["A"] * 4, "position": ["WR"] * 4,
                          "fantasy_points": [10.0] * 4, "xfp_ffo": [9.0, 8.0, 7.0, 5.0],
                          "xfp_own": [8.5, 8.0, 6.0, 5.5], "xfp_ng_ffo": [8.0, 7.0, 6.0, 5.0],
                          "xfp_ng_own": [8.0, 7.5, 6.0, 4.0]})  # fmt: skip
    assert pg.height
    return rep.OwnXfpReport(
        2008, 2008, tuple(seasons), folds,
        rep.per_play_table(rep.play_errors(plays, preds_2008), n_boot=20),
        rep.player_week_table(pairs), rep.stability_comparison(frames, seasons, n_boot=20),
        rep._reliability(frames, seasons), rep.backtest_metric_table(results),
        rep.model_mae_difference(results, (4, 6, 8, 10), n_boot=20),
        rep.tag_comparison(results, (4, 6, 8, 10), n_boot=20), rep._choices(results),
        {"tables": 1.0}, 20,
    )  # fmt: skip


def test_report_renders_every_section_deterministically(report, tmp_path):
    from twm.modules.regression_watch import own_xfp_report as rep

    r = report
    md = rep.report_markdown(r, "2026-10-01 00:00:00")
    for head in ("## Summary", "## The own models", "## 1. Per play", "## 2. Per player-game",
                 "### D2 stability study", "### D3 projection backtest", "## Notes"):  # fmt: skip
        assert head in md
    assert "Recommendation:" in md and md == rep.report_markdown(r, "2026-10-01 00:00:00")
    csv = rep.write_own_xfp_report(r, tmp_path / "own.md", "2026-10-01 00:00:00")
    t = pl.read_csv(csv)
    assert {"per_play", "stability", "tags", "backtest_metrics"} <= set(t["table"])
    assert "fold_seconds" not in t.columns


def test_recommendation_follows_the_stated_rule(report):
    from dataclasses import replace

    from twm.modules.regression_watch import own_xfp_report as rep

    r = report
    mae = pl.DataFrame({"position": ["all"], "rows": [100], "diff": [-0.1], "diff_lo": [-0.2],
                        "diff_hi": [-0.05]})  # fmt: skip
    tags = pl.DataFrame({"tag": ["sell_high", "buy_low"], "position": ["all", "all"],
                         "diff": [0.0, -0.02], "diff_lo": [-0.05, -0.06],
                         "diff_hi": [0.05, 0.01]})  # fmt: skip
    assert rep.recommendation(replace(r, model_mae=mae, tags=tags))[0]
    worse = mae.with_columns(pl.lit(0.01).alias("diff_lo"), pl.lit(0.2).alias("diff_hi"))
    assert not rep.recommendation(replace(r, model_mae=worse, tags=tags))[0]
    bad_tag = tags.with_columns(pl.lit(-0.01).alias("diff_hi"))
    assert not rep.recommendation(replace(r, model_mae=mae, tags=bad_tag))[0]
