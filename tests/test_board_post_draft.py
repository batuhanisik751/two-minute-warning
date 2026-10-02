"""Cliff & Breakout Board, post-draft snapshot (I2b): the as-of (May 15 of S+1, after the
draft), a pick or a coach departure made public after it is invisible, each draft feature on a
synthetic world, and determinism."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import polars as pl
import pytest

from tests import board_world as w
from twm.modules.board import features as bf
from twm.modules.board import post_draft as pdr
from twm.warehouse.available import AvailabilityRules

AS_OF = datetime(2021, 5, 15, tzinfo=UTC)
PUBLIC = datetime(2021, 5, 15)  # dim_player.public_from of a 2021 pick (naive UTC)


def test_post_draft_as_of_is_the_later_of_the_draft_end_and_the_public_day():
    rules = AvailabilityRules()
    assert pdr.post_draft_as_of(2020, rules) == AS_OF  # draft from 2021-04-29: May 15 is later
    early = replace(rules, draft_public_day=1)
    assert pdr.post_draft_as_of(2020, early) == datetime(2021, 5, 2, tzinfo=UTC)  # 04-29 + 3
    assert pdr.post_draft_as_of(2013, early) == datetime(2014, 5, 11, tzinfo=UTC)  # 05-08 + 3
    assert pdr.post_draft_as_of(2040, rules) == datetime(2041, 5, 15, tzinfo=UTC)  # unlisted


def _picks(rows: list[tuple]) -> tuple[pl.DataFrame, pl.DataFrame]:
    """dim_player and fact_combine rows of 2021 picks (gsis_id, team, round, pick, pos or None
    [, public_from])."""
    players, combine = [], []
    for r in rows:
        g, team, rnd, pick, pos = r[:5]
        players.append({"gsis_id": g, "birth_date": date(1999, 1, 1), "draft_year": 2021,
                        "draft_round": rnd, "draft_pick": pick, "draft_team": team,
                        "public_from": r[5] if len(r) > 5 else PUBLIC})  # fmt: skip
        if pos is not None:
            combine.append({"gsis_id": g, "season": 2021, "pos": pos,
                            "available_at": datetime(2021, 4, 29)})  # fmt: skip
    ints = {c: pl.Int32 for c in ("draft_year", "draft_round", "draft_pick")}
    p = pl.DataFrame(players, schema_overrides=ints)
    return p, pl.DataFrame(combine)


PICKS = [("00-0000101", "KC", 1, 31, "WR"), ("00-0000102", "KC", 4, 140, "WR"),
         ("00-0000103", "BUF", 1, 20, "QB"), ("00-0000104", "BUF", 2, 60, None),
         ("00-0000105", "KC", 6, 200, None), ("00-0000106", "BUF", 3, 90, "DE")]  # fmt: skip
LATE = [("00-0000107", "BUF", 2, 55, "TE", datetime(2021, 6, 1))]  # public after the as-of


def test_a_pick_made_public_after_the_as_of_is_invisible():
    players, combine = _picks(PICKS + LATE)
    view = w.FakeView({"dim_player": players, "fact_combine": combine}, AS_OF)
    got = pdr.draft_picks(view, 2021)
    assert got["gsis_id"].to_list() == ["00-0000103", "00-0000101", "00-0000104",
                                        "00-0000106", "00-0000102", "00-0000105"]  # fmt: skip
    assert got["grp"].to_list() == ["QB", "WR", None, "other", "WR", None]


def test_draft_features_count_best_pick_rookie_qb_and_unplaced():
    rows = pl.DataFrame({"gsis_id": ["a", "b", "c", "d"], "position": ["WR", "TE", "QB", "WR"],
                         "team": ["KC", "KC", "BUF", "BUF"]})  # fmt: skip
    players, combine = _picks(PICKS)
    picks = pdr.draft_picks(w.FakeView({"dim_player": players, "fact_combine": combine}, AS_OF),
                            2021)  # fmt: skip
    f = pdr.draft_features(rows, picks).sort("gsis_id")
    assert f["draft_pos_count_pd"].to_list() == [2.0, 0.0, 1.0, 0.0]
    assert f["draft_pos_best_round_pd"].to_list() == [1.0, None, 1.0, None]
    assert f["draft_pos_best_pick_pd"].to_list() == [31.0, None, 20.0, None]
    assert f["draft_qb_r1_pd"].to_list() == [0.0, 0.0, 1.0, 1.0]
    # BUF's round-2 pick has no combine row; KC's unplaced pick is in round 6 (not counted)
    assert f["draft_unplaced_pd"].to_list() == [0.0, 0.0, 1.0, 1.0]
    none = pdr.draft_features(rows, picks.clear())
    assert all(none[n].null_count() == 4 for n in pdr.NEW_FEATURES)


def test_post_draft_features_are_deterministic_and_ignore_later_picks_and_departures():
    deps = bf.Departures(pl.DataFrame(
        [("KC", 2020, "retired", date(2021, 3, 1)), ("BUF", 2020, "retired", date(2021, 5, 20))],
        schema={"team": pl.String, "season": pl.Int32, "departure_type": pl.String,
                "announced": pl.Date}, orient="row"))  # fmt: skip

    def run(extra: list[tuple]) -> pl.DataFrame:
        frames = w.tables()
        players, combine = _picks(PICKS + extra)
        old = frames["dim_player"].with_columns(pl.lit(None, dtype=pl.String).alias("draft_team"))
        frames["dim_player"] = pl.concat([old, players], how="diagonal_relaxed")
        frames["fact_combine"] = pl.concat([frames["fact_combine"], combine], how="diagonal")
        return pdr.post_draft_features(w.FakeView(frames, AS_OF), 2020, xfp_games=w.xfp_games(),
                                       departures=deps)  # fmt: skip

    a, b, c = run([]), run([]), run(LATE)
    assert a.equals(b) and a.equals(c) and a.height == 4
    f = a.sort("gsis_id")  # 1, 2 = KC WRs; 3 = BUF RB; 4 = BUF TE (the late TE pick is unseen)
    assert f["draft_pos_count_pd"].to_list() == [2.0, 2.0, 0.0, 0.0]
    assert f["draft_qb_r1_pd"].to_list() == [0.0, 0.0, 1.0, 1.0]
    # KC's departure (March) is known at May 15; BUF's (May 20) is not
    assert f["hc_departure"].to_list() == pytest.approx([1.0, 1.0, 0.0, 0.0])


@pytest.mark.realdata
@pytest.mark.parametrize("snapshot", ["preseason", "post_draft"])
def test_later_snapshots_pass_the_leakage_harness_on_the_real_warehouse(snapshot: str):
    """Deleting or scrambling everything not public at the 2016 preseason / post-draft as-of
    (the 2017 games, later charts, later draft classes, today's positions ...) changes no
    feature (the real warehouse, read only; I2a/I2b)."""
    from twm.backtest.leakage import assert_future_invariant
    from twm.config import settings
    from twm.modules.board import preseason as pre

    db = settings().path("warehouse")
    if not db.exists():
        pytest.skip("no warehouse (run `twm build`)")
    deps = bf.read_departures(settings().path("manual") / "coach_departures.csv")
    if snapshot == "preseason":
        at, build = pre.preseason_as_of(db, 2016), pre.preseason_features
    else:
        at, build = pdr.post_draft_as_of(2016), pdr.post_draft_features
    out = assert_future_invariant(
        lambda v: build(v, 2016, xfp_games=None, departures=deps), db, at, key=["gsis_id"]
    )
    assert out.height > 500 and out["snapshot"].unique().to_list() == [at.replace(tzinfo=None)]
    extra = pre.NEW_FEATURES if snapshot == "preseason" else pdr.NEW_FEATURES
    assert all(out[n].null_count() < out.height for n in extra)
