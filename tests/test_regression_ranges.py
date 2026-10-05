"""Feature #4: the 80% range of Regression Watch's rest-of-season projection
(:mod:`twm.modules.regression_watch.ranges`), its walk-forward pools, the coverage check, the
publish of the bounds, and the trade checker's pool (the same machinery, unchanged output).
Synthetic misses, plus the committed pin's frozen backtest (read-only, sha256-checked)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import yaml

from twm.modules.regression_watch import ranges as rg

STEPS = np.linspace(-10.0, 10.0, 41)  # 41 evenly spaced misses: q10 = -8, q90 = +8


def _misses(rows: list[tuple]) -> pl.DataFrame:
    """(season, rank_group, horizon, miss) -> the graded-misses layout (score 10)."""
    schema = {"season": pl.Int32, "rank_group": pl.String, "horizon": pl.Int32,
              "miss": pl.Float64}  # fmt: skip
    df = pl.DataFrame(rows, schema=schema, orient="row")
    return df.with_columns(
        pl.int_range(pl.len()).cast(pl.String).alias("entity_id"),
        pl.lit(4, dtype=pl.Int32).alias("week"), pl.lit(10.0).alias("score"),
        (pl.lit(10.0) + pl.col("miss")).alias("ros_ppg"),
    )  # fmt: skip


def test_the_range_is_the_projection_plus_the_misses_10th_and_90th_percentiles():
    pool = rg.Pool(STEPS, (13, 14), (2011, 2025))
    assert rg.quantiles(pool) == pytest.approx((-8.0, 8.0))
    assert rg.quantiles(pool, 0.5) == pytest.approx((-5.0, 5.0))
    assert rg.quantiles(rg.Pool(STEPS[: rg.MIN_MISSES - 1], (1, 1), (1, 1))) is None
    assert rg.quantiles(None) is None
    m = _misses([(2020, "WR", 14, x) for x in STEPS])
    rows = pl.DataFrame({"season": [2021, 2021], "rank_group": ["WR", "WR"],
                         "horizon": [14, 14], "score": [12.3, None]},
                        schema_overrides={"season": pl.Int32, "horizon": pl.Int32})  # fmt: skip
    got = rg.walk_forward(rows, m)
    assert got.get_column("range_lo").to_list() == [pytest.approx(4.3), None]
    assert got.get_column("range_hi").to_list() == [pytest.approx(20.3), None]
    assert got.row(0, named=True) | {"range_lo": 0, "range_hi": 0} == {
        "season": 2021, "rank_group": "WR", "horizon": 14, "score": 12.3, "range_lo": 0,
        "range_hi": 0, "range_misses": 41, "range_weeks_lo": 14, "range_weeks_hi": 14,
        "range_first": 2020, "range_last": 2020}  # fmt: skip


def test_the_pool_is_the_position_at_about_as_many_weeks_left():
    m = _misses([(2020, "RB", h, float(h)) for h in (7, 9, 11, 13, 14)] +
                [(2020, "TE", 7, 1.0)])  # fmt: skip
    near = rg.near_horizon(m, "RB", 14)
    assert near is not None and sorted(near.misses) == [13.0, 14.0] and near.horizons == (13, 14)
    mid = rg.near_horizon(m, "RB", 10)
    assert mid is not None and sorted(mid.misses) == [9.0, 11.0]  # within 2 weeks left
    far = rg.near_horizon(m, "TE", 15)  # none within 2: the nearest weeks left available
    assert far is not None and far.horizons == (7, 7) and far.seasons == (2020, 2020)
    assert rg.near_horizon(m, "QB", 14) is None


def test_walk_forward_never_uses_the_lists_own_season_or_later():
    small = [(2019, "QB", 14, x) for x in STEPS]  # +-8 at q10/q90
    huge = [(s, "QB", 14, 100.0 * x) for s in (2020, 2021) for x in STEPS]
    m = _misses(small + huge)
    rows = m.select("season", "rank_group", "horizon", "score").unique(maintain_order=True)
    got = {r["season"]: r for r in rg.walk_forward(rows, m).iter_rows(named=True)}
    assert got[2019]["range_lo"] is None and got[2019]["range_misses"] == 0  # nothing earlier
    assert (got[2020]["range_lo"], got[2020]["range_hi"]) == pytest.approx((2.0, 18.0))
    assert got[2020]["range_last"] == 2019
    assert got[2021]["range_first"] == 2019 and got[2021]["range_last"] == 2020
    assert got[2021]["range_misses"] == 82 and got[2021]["range_hi"] > 100


def test_coverage_counts_inside_below_and_above_per_position():
    rows = pl.DataFrame({
        "season": [2020] * 6, "rank_group": ["QB", "QB", "QB", "RB", "RB", "TE"],
        "ros_ppg": [5.0, 10.0, 20.0, 8.0, None, 9.0],
        "range_lo": [6.0, 6.0, 6.0, 8.0, 1.0, None], "range_hi": [14.0, 14.0, 14.0, 9.0, 2.0, None],
    }, schema_overrides={"season": pl.Int32})  # fmt: skip
    got = {r["position"]: r for r in rg.coverage(rows).iter_rows(named=True)}
    assert list(got) == ["QB", "RB", "All"]  # ungraded / unranged rows are left out
    assert (got["QB"]["n"], got["QB"]["inside"], got["QB"]["below"], got["QB"]["above"]) == (
        3, 1, 1, 1)  # fmt: skip
    assert got["RB"]["coverage"] == 1.0  # the bounds count as inside
    assert (got["All"]["n"], got["All"]["inside"]) == (4, 2) and got["All"]["coverage"] == 0.5


def test_a_weekly_lists_reasons_carry_its_range_and_nothing_else_changes():
    reasons = [json.dumps({"ppg": p, "tags": []}, sort_keys=True, separators=(",", ":"))
               for p in (9.0, 1.0)]  # fmt: skip
    table = pl.DataFrame({"season": [2026, 2026], "position": ["WR", "K"],
                          "ppg_ros": [11.0, 3.0], "reasons_json": reasons},
                         schema_overrides={"season": pl.Int32})  # fmt: skip
    m = _misses([(2020, "WR", 14, x) for x in STEPS])
    got = rg.with_ranges(table, m, 15)
    assert got.get_column("range_lo").to_list() == [pytest.approx(3.0), None]
    first = json.loads(got.get_column("reasons_json")[0])
    want = {"level": 0.8, "lo": pytest.approx(3.0), "hi": pytest.approx(19.0), "misses": 41,
            "weeks_left": [14, 14], "seasons": "2020-2020"}  # fmt: skip
    assert first.pop("range") == want
    assert first == {"ppg": 9.0, "tags": []}
    assert got.get_column("reasons_json")[1] == reasons[1]  # no pool: no range, same bytes
    assert rg.with_ranges(table.clear(), m, 15).height == 0


def _old_residual_pool(f: dict, horizon: int) -> tuple[dict, dict]:
    """The trade checker's pool exactly as written before feature #4 (step H6-a)."""
    rows = (
        f["predictions"].select("season", "week", "entity_id", "rank_group", "score", "horizon")
        .join(f["outcomes"].select("season", "week", "entity_id", "ros_ppg"),
              on=["season", "week", "entity_id"], how="inner")
        .filter(pl.col("ros_ppg").is_not_null() & pl.col("score").is_not_null())
        .with_columns((pl.col("ros_ppg") - pl.col("score")).alias("miss"),
                      (pl.col("horizon") - horizon).abs().alias("gap"))
    )  # fmt: skip
    out, used = {}, {}
    for pos in ("QB", "RB", "WR", "TE"):
        sub = rows.filter(pl.col("rank_group") == pos)
        if sub.height == 0:
            continue
        gap = max(2, int(sub.get_column("gap").min()))
        sub = sub.filter(pl.col("gap") <= gap)
        out[pos] = sub.get_column("miss").to_numpy()
        used[pos] = (int(sub.get_column("horizon").min()), int(sub.get_column("horizon").max()))
    return out, used


def _random_pin(folder: Path) -> tuple[Path, dict]:
    """A pin whose backtest is 1,500 random rows (some ungraded, some without an outcome)."""
    rng = np.random.default_rng(4)
    n = 1500
    df = pl.DataFrame({
        "season": rng.integers(2011, 2026, n).astype(np.int32),
        "week": rng.choice([4, 6, 8, 10], n).astype(np.int32),
        "entity_id": [f"p{i}" for i in range(n)],
        "rank_group": rng.choice(["QB", "RB", "WR", "TE"], n),
        "score": rng.normal(10, 4, n), "horizon": rng.choice([7, 8, 9, 10, 11, 12, 13, 14], n),
        "ros_ppg": np.where(rng.random(n) < 0.1, np.nan, rng.normal(9.5, 5, n)),
    }).with_columns(pl.col("ros_ppg").fill_nan(None),
                    pl.col("horizon").cast(pl.Int32))  # fmt: skip
    outcomes = df.select("season", "week", "entity_id", "ros_ppg").head(1400)
    frames = {"predictions": df.drop("ros_ppg"), "outcomes": outcomes}
    folder.mkdir(parents=True, exist_ok=True)
    bt: dict = {"seasons": "2011-2025"}
    for table, frame in frames.items():
        f = folder / f"{table}.parquet"
        frame.write_parquet(f)
        bt[table] = {"file": str(f), "sha256": hashlib.sha256(f.read_bytes()).hexdigest(),
                     "rows": frame.height}  # fmt: skip
    v = "zero_flat_all-0000000000000000"
    pin = {"season": 2026, "model_version": v, "file": f"a/{v}.json", "sha256": "0" * 64,
           "backtest": bt}  # fmt: skip
    path = folder / "pins.yaml"
    path.write_text(yaml.safe_dump({"regression_watch": pin}))
    return path, frames


def test_the_trade_checkers_pool_is_unchanged_by_the_shared_machinery(tmp_path):
    from twm.league import trade

    pins_path, frames = _random_pin(tmp_path / "bt")
    for horizon in (3, 7, 10, 14, 15, 20):
        new = trade.residual_pool(horizon, pins_path)
        old, used = _old_residual_pool(frames, horizon)
        assert new.horizons == used and new.seasons == "2011-2025"
        assert list(new.by_position) == list(old)
        for pos, arr in old.items():  # same misses in the same order: same resampled draws
            assert np.array_equal(new.by_position[pos], arr), (horizon, pos)


# --------------------------------------------------------------------------------------
# The publish: projection_lo / projection_hi (migration 0006)
# --------------------------------------------------------------------------------------


def _store_rows(kinds: list[tuple[int, str, dict | None]]) -> pl.DataFrame:
    """Store-layout rows (season, kind, the reasons' range or None), week 4, one WR each."""
    recs = []
    for i, (season, kind, rng) in enumerate(kinds):
        r = {"games": 4, "ppg": 12.0, "xfp_pg": 11.0, "fpoe_pg": 1.0, "shrinkage": 0.2,
             "tags": [], "without_garbage_time": {}} | ({"range": rng} if rng else {})  # fmt: skip
        recs.append({"season": season, "week": 4, "kind": kind, "entity_id": f"00-{i:07d}",
                     "position": "WR", "score": 11.5, "band": None, "reasons_json": json.dumps(r),
                     "as_of": datetime(season, 9, 30), "model_version": "v", "incomplete": False,
                     "created_at": datetime(2026, 10, 4)})  # fmt: skip
    return pl.DataFrame(recs).with_columns(pl.col("season", "week").cast(pl.Int32))


def test_the_rows_publish_the_stored_range_else_the_walk_forward_one_else_null():
    from twm.publish import regression_lists as rl

    rows = _store_rows([(2026, "live", {"lo": 8.0, "hi": 15.0}), (2026, "live", None),
                        (2026, "backtest", None),
                        (2026, "backtest", {"lo": 7.0, "hi": 16.0})])  # fmt: skip
    teams = rows.select("season", "week", pl.col("entity_id").alias("gsis_id"),
                        pl.lit("KC").alias("team"))  # fmt: skip
    m = _misses([(2020, "WR", 14, x) for x in STEPS])
    horizons = pl.DataFrame({"season": [2026], "week": [4], "horizon": [14]}, schema={
        "season": pl.Int32, "week": pl.Int32, "horizon": pl.Int32})  # fmt: skip
    bounds = rl.backtest_bounds(rows, horizons, m)
    assert bounds.height == 2 and set(bounds.get_column("kind")) == {"backtest"}
    _, out = rl.regression_lists(rows, teams, bounds)
    got = {(r["gsis_id"]): (r["projection_lo"], r["projection_hi"])
           for r in out.iter_rows(named=True)}  # fmt: skip
    assert got["00-0000000"] == (8.0, 15.0)  # stored at scoring
    assert got["00-0000001"] == (None, None)  # a live list scored before feature #4: never filled
    assert got["00-0000002"] == pytest.approx((3.5, 19.5))  # walk-forward at publish
    assert got["00-0000003"] == (7.0, 16.0)  # the stored one wins
    _, plain = rl.regression_lists(rows, teams)
    assert plain.get_column("projection_lo").null_count() == 2


def test_the_current_seasons_weeks_left_come_from_its_last_regular_season_week(monkeypatch):
    from twm.modules.waiver_radar import weekly as wr
    from twm.publish import regression_lists as rl

    monkeypatch.setattr(wr, "last_reg_week", lambda db, season: 18)
    rows = _store_rows([(2026, "backtest", None)])
    snap = pl.DataFrame({"season": [2025, 2025], "week": [4, 4], "horizon": [14, 14]})
    got = rl.weeks_left(snap, Path("unused.duckdb"), 2026, rows).sort("season").rows()
    assert got == [(2025, 4, 14), (2026, 4, 14)]
    monkeypatch.setattr(wr, "last_reg_week", lambda db, season: None)  # no schedule: no range
    assert rl.weeks_left(snap, Path("unused.duckdb"), 2026, rows).height == 1


def test_the_frozen_backtest_lists_get_walk_forward_ranges_with_about_80_percent_coverage():
    """The committed pin (sha256-checked, only read): the 2011 lists have no earlier season (no
    range); 2012-2025's published bounds, checked against the frozen outcomes, cover what
    :func:`ranges.backtest_coverage` says, within a few points of 80% at every position."""
    from twm.publish import regression_lists as rl

    rows, snap = rl.frozen_lists(2026, datetime(2026, 10, 4))
    misses = rg.graded_misses(snap["predictions"], snap["outcomes"])
    horizons = rl.weeks_left(snap["predictions"], Path("unused.duckdb"), 2026, rows)
    teams = snap["predictions"].select("season", "week", pl.col("entity_id").alias("gsis_id"),
                                       "team")  # fmt: skip
    _, out = rl.regression_lists(rows, teams, rl.backtest_bounds(rows, horizons, misses))
    assert out.filter(pl.col("season") == 2011).get_column("projection_lo").null_count() == \
        out.filter(pl.col("season") == 2011).height  # fmt: skip
    later = out.filter(pl.col("season") > 2011)
    assert later.get_column("projection_lo").null_count() == 0
    assert (later.get_column("projection_lo") <= later.get_column("projection")).all()
    assert (later.get_column("projection") <= later.get_column("projection_hi")).all()
    outcomes = snap["outcomes"].select("season", "week", pl.col("entity_id").alias("gsis_id"),
                                       "ros_ppg")  # fmt: skip
    graded = later.join(outcomes, on=["season", "week", "gsis_id"]).select(
        "season", pl.col("position").alias("rank_group"), "ros_ppg",
        pl.col("projection_lo").alias("range_lo"), pl.col("projection_hi").alias("range_hi"),
    )  # fmt: skip
    published = rg.coverage(graded)
    assert published.equals(rg.backtest_coverage(misses))
    by = dict(published.select("position", "coverage").rows())
    assert set(by) == {"QB", "RB", "WR", "TE", "All"}
    assert all(0.75 <= c <= 0.85 for c in by.values()), by
    assert published.row(4, named=True)["first"] == 2012
