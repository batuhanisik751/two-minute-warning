"""Hot-Seat H5 research (decision quality vs firing risk): offline, synthetic data only."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest
import statsmodels.api as sm

from twm.modules.hot_seat import research as rs

TRUE_SLOPE = 0.8  # log odds per unit of the synthetic decision column (its SD is 1)


def _world(seed: int = 7, slope: float = TRUE_SLOPE, seasons: int = 20, teams: int = 32):
    """End-of-season rows with a known model: logit P(y) = -2 + slope*dec + 0.6*perf."""
    rng = np.random.default_rng(seed)
    n = seasons * teams
    dec = rng.normal(size=n)
    perf = rng.normal(size=n)
    eta = -2.0 + slope * dec + 0.6 * perf
    y = (rng.random(n) < 1.0 / (1.0 + np.exp(-eta))).astype(np.int8)
    return pl.DataFrame({
        "season": np.repeat(np.arange(2006, 2006 + seasons), teams).astype(np.int32),
        "snapshot": ["end_of_season"] * n, "week": np.full(n, 17, dtype=np.int32),
        "team": [f"T{i % teams:02d}" for i in range(n)],
        "coach_id": [f"c{i}" for i in range(n)],
        "is_interim": [False] * n, "censored": [False] * n,
        "is_first_year_coach": (rng.random(n) < 0.2).tolist(),
        "y": y, "event": y, "dec": dec, "perf": perf,
    })  # fmt: skip


SPEC = rs.Spec("synthetic", "known slope", "end_of_season", decision="dec", controls=("perf",))


def test_fit_matches_statsmodels_at_ridge_zero():
    df = _world()
    x = df.select("dec", "perf").to_numpy()
    y = df.get_column("y").cast(pl.Float64).to_numpy()
    f = rs.fit_logit(x, y, ridge=0.0)
    m = sm.Logit(y, sm.add_constant(x)).fit(disp=0)
    assert f.converged
    np.testing.assert_allclose(f.coef, m.params, atol=1e-8)
    np.testing.assert_allclose(f.se, m.bse, atol=1e-8)


def test_weights_equal_stacked_copies():
    df = _world(seasons=4)
    x = df.select("dec", "perf").to_numpy()
    y = df.get_column("y").cast(pl.Float64).to_numpy()
    w = np.where(df.get_column("season").to_numpy() == 2006, 2.0, 1.0)
    stacked = np.r_[np.flatnonzero(w == 2.0), np.arange(len(y))]
    a = rs.fit_logit(x, y, w)
    b = rs.fit_logit(x[stacked], y[stacked])
    np.testing.assert_allclose(a.coef, b.coef, atol=1e-9)


def test_known_coefficient_recovered_within_both_intervals():
    e = rs.run_spec(SPEC, {"main": _world()}, n_boot=300)
    d = e.design
    j = d.terms.index("dec")
    sd = d.sd[j]
    for kind in ("classical", "bootstrap"):
        lo, hi = e.interval("dec", kind)
        assert lo / sd < TRUE_SLOPE < hi / sd, kind  # per SD -> per unit
    assert abs(e.fit.coef[j + 1] / sd - TRUE_SLOPE) < 0.25
    assert e.boot_ok() == 300


def test_null_world_permutation_p_is_not_small_and_effect_is_found():
    d0 = rs.run_spec(SPEC, {"main": _world(slope=0.0)}, n_boot=10).design
    null = rs.permutation_null(d0, "dec", n_perm=200)
    obs = rs.fit_logit(d0.x, d0.y).coef[1]
    assert rs.permutation_p(obs, null) > 0.05
    d1 = rs.run_spec(SPEC, {"main": _world()}, n_boot=10).design
    null1 = rs.permutation_null(d1, "dec", n_perm=200)
    obs1 = rs.fit_logit(d1.x, d1.y).coef[1]
    assert rs.permutation_p(obs1, null1) == pytest.approx(1 / 201)


def test_permutation_within_season_keeps_season_counts():
    rng = np.random.default_rng(3)
    season = np.repeat(np.array([2006, 2007, 2008]), [5, 7, 4])[rng.permutation(16)]
    perm = rs.permute_within(season, np.random.default_rng(11))
    assert sorted(perm.tolist()) == list(range(16))  # a permutation of the rows
    assert (season[perm] == season).all()  # every row takes a value of its own season
    values = rng.normal(size=16)
    for s in (2006, 2007, 2008):
        m = season == s
        assert m.sum() == (season[perm] == s).sum()
        np.testing.assert_allclose(np.sort(values[perm][m]), np.sort(values[m]))
    assert (perm != np.arange(16)).any()


def test_deterministic_with_fixed_seeds():
    frames = {"main": _world()}
    a = rs.run_spec(SPEC, frames, n_boot=50)
    b = rs.run_spec(SPEC, frames, n_boot=50)
    np.testing.assert_array_equal(a.boot, b.boot)
    np.testing.assert_array_equal(a.fit.coef, b.fit.coef)
    pa = rs.permutation_null(a.design, "dec", n_perm=30)
    pb = rs.permutation_null(b.design, "dec", n_perm=30)
    np.testing.assert_array_equal(pa, pb)
    other = rs.permutation_null(a.design, "dec", n_perm=30, seed=1)
    assert not np.array_equal(pa, other)


def test_design_standardizes_and_drops_constant_and_null_rows():
    df = _world(seasons=3).with_columns(pl.lit(1.0).alias("const"))
    df = df.with_columns(pl.when(pl.int_range(pl.len()) == 0).then(None).otherwise(pl.col("perf"))
                         .alias("perf"))  # fmt: skip
    d = rs.design(df, ["dec", "perf", "const"], "y")
    assert d.terms == ("dec", "perf") and d.dropped_constant == ("const",)
    assert d.dropped_null == 1 and d.x.shape == (df.height - 1, 2)
    np.testing.assert_allclose(d.x.mean(axis=0), 0.0, atol=1e-12)
    np.testing.assert_allclose(d.x.std(axis=0, ddof=1), 1.0)


def _grades(world: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Stored-grade frames for the world's team-seasons: per team one clear call (WP lost =
    16 x dec/100 + 0.2, so the per-game clear measure is dec/100 + 0.0125), one toss-up (0.01),
    one excluded late-game play and one playoff play (both ignored); clock: one case for T00."""
    ts = world.filter(pl.col("snapshot") == "end_of_season").select("season", "team", "dec")
    n = ts.height
    rows = {
        "season": [*ts["season"]] * 4, "posteam": [*ts["team"]] * 4,
        "season_type": ["REG"] * 3 * n + ["POST"] * n,
        "grade": ["clear"] * n + ["toss_up"] * n + [None] * n + ["clear"] * n,
        "exclusion": [None] * 2 * n + ["late_game"] * n + [None] * n,
        "wp_lost": [*(16 * ts["dec"] / 100 + 0.2)] + [0.01] * n + [None] * n + [9.0] * n,
    }  # fmt: skip
    fourth = pl.DataFrame(rows, schema_overrides={"season": pl.Int32})
    clock = pl.DataFrame(
        {"metric": ["timeouts_unused"] * 2, "season": [2006, 2006], "team": ["T00", "T01"],
         "season_type": ["REG", "REG"], "is_case": [True, False]},
        schema_overrides={"season": pl.Int32},
    )  # fmt: skip
    return fourth, clock


def _full_world(slope: float = 0.0) -> pl.DataFrame:
    """End-of-season and week-12 rows with every column the specifications read."""
    base = _world(slope=slope)
    rng = np.random.default_rng(5)
    n = base.height
    extra = {c: rng.normal(size=n) for c in rs.CONTROLS if c != "is_first_year_coach"}
    eos = base.with_columns(
        *(pl.Series(c, v) for c, v in extra.items()),
        pl.col("dec").alias(rs.DECISION), pl.lit(16).alias("reg_games_played"),
        pl.lit(0).alias("games_remaining"), pl.lit(None, pl.String).alias("departure_type"),
    )  # fmt: skip
    wk = eos.with_columns(
        pl.lit("weekly").alias("snapshot"), pl.lit(12, pl.Int32).alias("week"),
        pl.lit(5).alias("games_remaining"), pl.lit(0, pl.Int8).alias("event"),
    )  # fmt: skip
    return pl.concat([eos, wk], how="vertical")


def test_extras_and_full_run_with_report(tmp_path):
    from twm.modules.hot_seat import research_report as rr

    world = _full_world()
    fourth, clock = _grades(world)
    ex = rs.decision_extras(fourth, clock)
    rows = rs.add_extras(world, ex)
    eos = rows.filter(pl.col("snapshot") == "end_of_season")
    np.testing.assert_allclose(eos["_check_clear"], eos["dec"] / 100 + 0.0125)
    np.testing.assert_allclose(eos[rs.DECISION_ALL], eos["_check_clear"] + 0.01 / 16)
    assert eos.filter(pl.col(rs.CLOCK) > 0).select("season", "team").rows() == [(2006, "T00")]
    assert rows.filter(pl.col("snapshot") == "weekly")[rs.DECISION_ALL].null_count() == n_wk(rows)
    gc = rs.grade_counts(fourth, clock)
    assert (gc["graded"], gc["late_game"], gc["clock_cases"]) == (2 * eos.height, eos.height, 1)
    res = rs.run_research(world, world, ex, n_boot=40, n_perm=40)
    assert [e.spec.name for e in res.estimates] == [s.name for s in rs.SPECS]
    assert res.check_clear_max_diff > 0  # the synthetic feature is dec, not dec/100 + 0.0125
    paths = rr.write_outputs(res, {"departures_joined": 1, "departures_unverified": 0,
                                   "departures_date_imputed": 0}, gc, tmp_path)  # fmt: skip
    text = paths["report"].read_text()
    tab = pl.read_csv(paths["csv"])
    prim = tab.filter((pl.col("spec") == "primary") & pl.col("is_decision")).row(0, named=True)
    assert f"**{prim['odds_ratio']:.2f}**" in text and "## Honest limits" in text
    assert prim["perm_p"] == pytest.approx(res.perm_p, abs=1e-6)
    assert "accepted in bulk" in text


def n_wk(rows: pl.DataFrame) -> int:
    return rows.filter(pl.col("snapshot") == "weekly").height
