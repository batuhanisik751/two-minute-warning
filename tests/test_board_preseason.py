"""Cliff & Breakout Board, preseason snapshot (I2a): the as-of (one hour before the first week-1
kickoff of S+1), a depth chart published after it is invisible, each new feature on a synthetic
world, and determinism."""

from __future__ import annotations

from datetime import UTC, date, datetime

import duckdb
import polars as pl
import pytest

from tests import board_world as w
from twm.modules.board import features as bf
from twm.modules.board import preseason as pre
from twm.modules.board.seasons import SeasonNotOverError

AS_OF = datetime(2021, 9, 9, 23, 20, tzinfo=UTC)
COLS = ("season", "source_format", "week", "game_type", "dt", "team", "gsis_id", "unit",
        "position", "pos_slot", "depth_rank", "available_at")  # fmt: skip


def _chart(rows: list[tuple]) -> pl.DataFrame:
    """Legacy week-1 rows (team, gsis_id, position, rank[, available_at])."""
    out = []
    for r in rows:
        team, g, pos, rank = r[:4]
        avail = r[4] if len(r) > 4 else datetime(2021, 9, 8, 14)
        out.append((2021, "legacy", 1, "REG", None, team, g, "offense", pos, None, rank, avail))
    return pl.DataFrame(out, schema={c: None for c in COLS}, orient="row").with_columns(
        pl.col("dt").cast(pl.Datetime), pl.col("pos_slot").cast(pl.Int32)
    )


def test_preseason_as_of_is_one_hour_before_the_first_week1_kickoff():
    con = duckdb.connect()
    con.execute("CREATE TABLE fact_game AS SELECT * FROM (VALUES (2021, 'REG', 1, TIMESTAMP "
                "'2021-09-10 00:20:00'), (2021, 'REG', 1, TIMESTAMP '2021-09-12 17:00:00'), "
                "(2021, 'PRE', 1, TIMESTAMP '2021-08-05 00:00:00')) t(season, game_type, week, "
                "kickoff_utc)")  # fmt: skip
    assert pre.preseason_as_of(con, 2020) == AS_OF
    with pytest.raises(SeasonNotOverError):
        pre.preseason_as_of(con, 2021)


def test_a_chart_published_after_the_as_of_is_invisible():
    early = _chart([("KC", "00-0000001", "WR", 1), ("KC", "00-0000002", "WR", 2)])
    late = _chart([("KC", "00-0000002", "WR", 1, datetime(2021, 9, 10, 1))])  # after the as-of
    chart = pre.week1_chart(w.FakeView({"fact_depth_chart": pl.concat([early, late])}, AS_OF),
                            2021)  # fmt: skip
    got = chart.select("gsis_id", "depth_rank").sort("gsis_id").rows()
    assert got == [("00-0000001", 1), ("00-0000002", 2)]


def test_a_daily_pull_is_renumbered_within_each_slot_and_the_latest_pull_wins():
    def pull(dt: datetime, ranks: list[tuple[str, int, int]]) -> list[tuple]:
        return [(2021, "daily", None, None, dt, "KC", g, "offense", "WR", slot, r, dt)
                for g, slot, r in ranks]  # fmt: skip

    rows = pull(datetime(2021, 9, 1, 7), [("00-0000009", 1, 1)])
    rows += pull(
        datetime(2021, 9, 9, 7),
        [("00-0000001", 1, 1), ("00-0000002", 2, 2), ("00-0000003", 1, 3), ("00-0000004", 2, 4)],
    )
    rows += pull(datetime(2021, 9, 10, 7), [("00-0000005", 1, 1)])  # after the as-of
    df = pl.DataFrame(rows, schema={c: None for c in COLS}, orient="row")
    chart = pre.week1_chart(w.FakeView({"fact_depth_chart": df}, AS_OF), 2021)
    got = chart.select("gsis_id", "depth_rank").sort("gsis_id").rows()
    assert got == [("00-0000001", 1), ("00-0000002", 1), ("00-0000003", 2), ("00-0000004", 2)]


def _world() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """S = 2020. Rows: 1 stays (KC WR1); 2 moves KC -> BUF behind a new WR; 3 is on no chart;
    4 is BUF's TE1 with a new TE behind him. BUF's QB1 is new, KC's is not."""
    rows = pl.DataFrame({"gsis_id": ["00-0000001", "00-0000002", "00-0000003", "00-0000004"],
                         "position": ["WR", "WR", "RB", "TE"],
                         "team": ["KC", "KC", "BUF", "BUF"]})  # fmt: skip
    chart = _chart([("KC", "00-0000001", "WR", 1), ("KC", "00-0000020", "QB", 1),
                    ("BUF", "00-0000010", "WR", 1), ("BUF", "00-0000002", "WR", 2),
                    ("BUF", "00-0000004", "TE", 1), ("BUF", "00-0000011", "TE", 2),
                    ("BUF", "00-0000013", "QB", 1), ("BUF", "00-0000012", "QB", 2)])  # fmt: skip
    rosters = pl.DataFrame({"team": ["KC", "KC", "KC", "BUF", "BUF", "BUF"],
                            "gsis_id": ["00-0000001", "00-0000002", "00-0000020", "00-0000003",
                                        "00-0000004", "00-0000012"]})  # fmt: skip
    usage = pl.DataFrame({"team": ["KC", "KC", "BUF", "BUF"],
                          "gsis_id": ["00-0000001", "00-0000002", "00-0000003", "00-0000004"],
                          "targets": [100.0, 50.0, 0.0, 40.0],
                          "carries": [10.0, 0.0, 200.0, 0.0]})  # fmt: skip
    qb = pl.DataFrame({"team": ["KC", "BUF"], "qb_s": ["00-0000020", "00-0000012"]})
    return rows, chart, rosters, usage, qb


def test_team_level_qb1_change_and_vacated_shares():
    _, chart, _, usage, qb = _world()
    t = pre.team_level(chart, usage, qb).sort("team")
    assert t["team"].to_list() == ["BUF", "KC"]
    assert t["qb1_change_s1"].to_list() == [1.0, 0.0]
    assert t["vacated_targets_share_s1"].to_list() == pytest.approx([0.0, 50 / 150])
    assert t["vacated_carries_share_s1"].to_list() == pytest.approx([1.0, 0.0])


def test_chart_features_team_change_rank_competition_absent_and_coach():
    rows, chart, rosters, usage, qb = _world()
    teams = pre.team_level(chart, usage, qb)
    f = pre.chart_features(rows, chart, rosters, teams, {"BUF"}).sort("gsis_id")
    assert f["dc_absent"].to_list() == [False, False, True, False]
    assert f["team_change_s1"].to_list() == [0.0, 1.0, None, 0.0]
    assert f["depth_rank_s1"].to_list() == [1.0, 2.0, None, 1.0]
    # 2: a new WR (not on BUF's 2020 rosters) is ahead of him; 4: the new TE is behind him
    assert f["new_competitor_s1"].to_list() == [0.0, 1.0, None, 0.0]
    assert f["qb1_change_s1"].to_list() == [0.0, 1.0, None, 1.0]
    assert f["hc_change_s1"].to_list() == [0.0, 1.0, None, 1.0]
    assert f["vacated_carries_share_s1"].to_list() == pytest.approx([0.0, 1.0, None, 1.0])


def test_preseason_features_are_deterministic_and_ignore_a_later_chart():
    deps = bf.Departures(pl.DataFrame(
        [("BUF", 2020, "fired_after_season", date(2021, 1, 10))],
        schema={"team": pl.String, "season": pl.Int32, "departure_type": pl.String,
                "announced": pl.Date}, orient="row"))  # fmt: skip
    chart = _chart([("KC", "00-0000001", "WR", 1), ("BUF", "00-0000002", "WR", 1),
                    ("BUF", "00-0000004", "TE", 1)])  # fmt: skip
    later = _chart([("KC", "00-0000003", "RB", 1, datetime(2021, 9, 10, 2))])

    def run(extra: pl.DataFrame | None) -> pl.DataFrame:
        frames = {**w.tables(), "fact_depth_chart": pl.concat([chart, extra]) if extra is not
                  None else chart}  # fmt: skip
        teams = pl.DataFrame(
            {"gsis_id": list(w.PLAYERS), "team": [p[2] for p in w.PLAYERS.values()]}
        )
        frames["fact_roster_week"] = frames["fact_roster_week"].join(teams, on="gsis_id")
        return pre.preseason_features(w.FakeView(frames, AS_OF), 2020, xfp_games=w.xfp_games(),
                                      departures=deps)  # fmt: skip

    a, b, c = run(None), run(None), run(later)
    assert a.equals(b) and a.equals(c) and a.height == 4
    f = a.sort("gsis_id")
    assert f["dc_absent"].to_list() == [False, False, True, False]
    assert f["team_change_s1"].to_list() == [0.0, 1.0, None, 0.0]
    assert f["hc_change_s1"].to_list() == [0.0, 1.0, None, 1.0]
