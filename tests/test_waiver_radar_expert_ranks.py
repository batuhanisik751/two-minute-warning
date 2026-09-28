"""The experts' ranks at the as-of (C4 expert baseline): the rest-of-season page first, the
weekly page as a fallback, only pages public at the as-of (leakage harness), batch path equal
to the reference, registered as metrics that no model may use.

The synthetic 2025 world of tests/test_waiver_radar_pool.py plus rest-of-season pages (all
ids and names made up). As-ofs: week 1 2025-09-09, week 2 09-16, week 3 09-23 (a split week).
A page is public the day after its scrape, at 00:00 UTC."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from tests.conftest import RANKINGS_ALL_DTYPES, RawCache, frame, season_2025_games
from tests.test_waiver_radar_pool import (
    RULES,
    _ff_playerids,
    _players,
    _Rewritten,
    fpid,
    gid,
    world_2025,
)
from twm import ids
from twm.asof import AsOfView, weekly_as_of
from twm.backtest.leakage import LeakageError, assert_future_invariant
from twm.modules.waiver_radar import expert_ranks as er
from twm.modules.waiver_radar.pool import candidate_pool
from twm.registry import FeatureCheckError, check_features, get
from twm.sources import nflverse as nv
from twm.warehouse import build as wb

ROS_WR = {"fp_page": "/nfl/rankings/ros-ppr-wr.php", "page_type": "redraft-wr", "ecr_type": "rp"}
ROS_RB = {"fp_page": "/nfl/rankings/ros-ppr-rb.php", "page_type": "redraft-rb", "ecr_type": "rp"}


def _rk(base: dict, n: int, pos: str, ecr: float, day: str) -> dict:
    return {**base, "player": f"Player {n}", "id": fpid(n), "pos": pos, "ecr": ecr,
            "scrape_date": day}  # fmt: skip


def ros_pages() -> list[dict]:
    return [
        # 09-12 (public 09-13): 102 first, 103 second
        _rk(ROS_WR, 102, "WR", 1.0, "2025-09-12"), _rk(ROS_WR, 103, "WR", 2.0, "2025-09-12"),
        # 09-19 (public 09-20): 103 first, 102 second; a RB page appears
        _rk(ROS_WR, 103, "WR", 1.0, "2025-09-19"), _rk(ROS_WR, 102, "WR", 5.0, "2025-09-19"),
        _rk(ROS_RB, 113, "RB", 1.0, "2025-09-19"),
        # 09-26: after week 3's as-of, never visible in these tests
        _rk(ROS_WR, 101, "WR", 1.0, "2025-09-26"), _rk(ROS_WR, 105, "WR", 2.0, "2025-09-26"),
    ]  # fmt: skip


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory):
    tmp = tmp_path_factory.mktemp("experts")
    with pytest.MonkeyPatch.context() as mp:
        root = tmp / "raw"
        mp.setattr(nv, "raw_dir", lambda: root)

        def _no_download(ds):
            raise AssertionError(f"never download ({ds.name})")

        mp.setattr(nv, "_loader", _no_download)
        mp.setattr(nv, "_configure_nflreadpy", lambda: None)
        mp.setattr(ids, "overrides_path", lambda: tmp / "manual" / ids.OVERRIDES_FILE)
        cache = RawCache(root)
        cache.write_globals()
        cache.write("players", None, _players())
        cache.write("ff_playerids", None, _ff_playerids())
        stats, rosters, rankings = world_2025()
        cache.write("ff_rankings_all", None, frame(rankings + ros_pages(), RANKINGS_ALL_DTYPES))
        cache.write_season(2025, season_2025_games(), player_stats=stats, snaps=[],
                           rosters=rosters)  # fmt: skip
        db = tmp / "experts.duckdb"
        wb.build_warehouse([2025], db_path=db)
    return db


def _experts(db, week: int, view_wrapper=None) -> pl.DataFrame:
    with AsOfView(db, weekly_as_of(db, 2025, week)) as v:
        pool = candidate_pool(v, 2025, week, rules=RULES)
        return er.expert_ranks_for(view_wrapper(v) if view_wrapper else v, 2025, week, pool)


def _by_id(df: pl.DataFrame) -> dict[str, tuple]:
    return {
        r["gsis_id"]: (r["ecr_available"], r["ecr_pos_rank"], r["ecr_page_kind"],
                       r["ecr_scrape_date"])
        for r in df.iter_rows(named=True)
    }  # fmt: skip


def test_week_1_only_the_weekly_page_exists(world):
    got = _by_id(_experts(world, 1))
    d = date(2025, 9, 5)
    assert got[gid(101)] == (True, 1, "weekly", d)
    assert got[gid(104)] == (True, 2, "weekly", d)
    assert got[gid(103)] == (True, 3, "weekly", d)
    assert got[gid(102)] == (True, None, None, None)  # on no page: unranked
    rb = [v for k, v in got.items() if k in {gid(107), gid(108), gid(113)}]
    assert rb and all(v == (False, None, None, None) for v in rb)  # no RB page yet


def test_week_2_rest_of_season_first_then_weekly(world):
    got = _by_id(_experts(world, 2))
    ros, wk = date(2025, 9, 12), date(2025, 9, 12)
    assert got[gid(102)] == (True, 1, "ros", ros)
    assert got[gid(103)] == (True, 2, "ros", ros)
    assert got[gid(101)] == (True, 1, "weekly", wk)  # not on the ROS page: weekly rank
    assert got[gid(104)] == (True, 2, "weekly", wk)


def test_week_3_latest_pages_and_the_weekly_age_limit(world):
    df = _experts(world, 3)
    got = _by_id(df)
    ros = date(2025, 9, 19)
    assert got[gid(103)] == (True, 1, "ros", ros)
    assert got[gid(102)] == (True, 2, "ros", ros)
    # 101 is missing from the latest weekly page (09-19) but on 09-12's, 7 days older
    assert got[gid(101)] == (True, 1, "weekly", date(2025, 9, 12))
    assert got[gid(105)] == (True, 1, "weekly", ros)
    assert got[gid(113)] == (True, 1, "ros", ros)  # the RB page
    # the 09-26 pages (after the as-of) are invisible: 101 is not ranked 1 on a ROS page
    assert df.columns == ["season", "week", "gsis_id", *er.EXPERT_COLUMNS]


def test_weekly_pages_older_than_the_limit_do_not_count():
    pages = pl.DataFrame(
        {
            "page_kind": ["weekly", "weekly"], "page_pos": ["WR", "WR"],
            "scrape_date": [date(2025, 9, 5), date(2025, 9, 19)],
            "gsis_id": ["a", "b"], "pos_rank": [1, 1],
        }
    )  # fmt: skip
    rows = pl.DataFrame({"season": [2025, 2025], "week": [3, 3], "gsis_id": ["a", "b"],
                         "position": ["WR", "WR"]})  # fmt: skip
    got = _by_id(er.compute_expert_ranks(pages, rows))
    assert got["a"] == (True, None, None, None)  # 14 days older than the latest weekly page
    assert got["b"] == (True, 1, "weekly", date(2025, 9, 19))


def _builder(week: int, view_wrapper=None):
    def build(v: AsOfView) -> pl.DataFrame:
        pool = candidate_pool(v, 2025, week, rules=RULES)
        return er.expert_ranks_for(view_wrapper(v) if view_wrapper else v, 2025, week, pool)

    return build


@pytest.mark.parametrize("week", [1, 2, 3])
def test_expert_ranks_pass_the_leakage_harness(world, week):
    out = assert_future_invariant(
        _builder(week), world, weekly_as_of(world, 2025, week), key=["gsis_id"]
    )
    assert out.height > 0 and out["ecr_pos_rank"].is_not_null().any()


def test_a_leaky_expert_rank_fails_the_harness(world):
    """Reading the raw warehouse (``wh.``) lets the 09-26 page (after the as-of) in."""
    leaky = _builder(3, lambda v: _Rewritten(v, "FROM fact_ranking", "FROM wh.fact_ranking"))
    _Rewritten.rewrites = 0
    with pytest.raises(LeakageError):
        assert_future_invariant(leaky, world, weekly_as_of(world, 2025, 3), key=["gsis_id"])
    assert _Rewritten.rewrites > 0


def test_batch_path_equals_the_reference(world):
    frames, refs = [], []
    for week in (1, 2, 3):
        with AsOfView(world, weekly_as_of(world, 2025, week)) as v:
            pool = candidate_pool(v, 2025, week, rules=RULES)
            refs.append(er.expert_ranks_for(v, 2025, week, pool))
        frames.append(pool)
    batch = er.expert_ranks_history(world, pl.concat(frames))
    assert batch.equals(pl.concat(refs))
    assert er.expert_ranks_history(world, frames[0].head(0)).height == 0
    with pytest.raises(KeyError):
        er.expert_ranks_history(world, frames[0].drop("as_of"))


def test_expert_columns_are_metrics_no_model_may_use():
    for col in er.EXPERT_COLUMNS:
        assert get(col).kind == "metric"
        with pytest.raises(FeatureCheckError, match="registered as a metric"):
            check_features([col], "waiver_radar")
