"""Feature #5, Teammate out's publish (src/twm/publish/teammate_out.py): synthetic snapshots, the
pinned table's tables (and the owner-override disclosure), the live record graded from
published + local snapshots, and the validation."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from tests import publish_teammate_out_synthetic as pt
from twm.modules.teammate_out import production as tp
from twm.modules.teammate_out import weekly as wk
from twm.publish import teammate_out as tn
from twm.publish import write as wr
from twm.publish.collect import PublishInputError
from twm.publish.tables import TABLES, TEAMMATE_OUT


def _frames(store: Path) -> tuple[pl.DataFrame, pl.DataFrame]:
    return tn.snapshot_frames(wk.read_snapshots(store))


def test_snapshots_become_lists_and_rows_in_the_tables_layout(tmp_path: Path) -> None:
    later = pt.FRIDAY + timedelta(hours=16)
    lists, rows = _frames(pt.write_store(tmp_path / "p.duckdb", (pt.FRIDAY, later)))
    assert lists.columns == list(TABLES["teammate_out_list"].names)
    assert rows.columns == list(TABLES["teammate_out_row"].names)
    assert lists.get_column("as_of").to_list() == [pt.FRIDAY, later]  # aware UTC
    assert lists.select("n_teams", "n_out", "n_players").rows() == [(2, 3, 5)] * 2
    first = rows.filter(pl.col("as_of") == pt.FRIDAY)
    # by kickoff, then team, then predicted points (highest first)
    assert first.get_column("role").to_list() == ["WR1", "RB2", "RB3", "WR2", "TE2"]
    assert first.get_column("kickoff")[0] == datetime(2026, 10, 11, 17, tzinfo=UTC)
    assert first.get_column("out_ids")[3] == f"{pt.OUT_IDS[1]},{pt.OUT_IDS[2]}"


def test_an_empty_store_gives_empty_frames(tmp_path: Path) -> None:
    lists, rows = _frames(tmp_path / "none.duckdb")
    assert lists.is_empty() and rows.is_empty()
    assert lists.schema["as_of"] == pl.Datetime("us", "UTC")
    assert tn.out_names(wk.read_snapshots(tmp_path / "none.duckdb")).is_empty()


def test_a_snapshot_mixing_model_versions_is_refused(tmp_path: Path) -> None:
    snaps = wk.read_snapshots(pt.write_store(tmp_path / "p.duckdb"))
    snaps = snaps.with_columns(pl.Series("model_version", ["a", "b", "a", "a", "a"]))
    with pytest.raises(PublishInputError, match="mixes model versions"):
        tn.snapshot_frames(snaps)


def test_the_absent_starters_are_named_for_dim_player(tmp_path: Path) -> None:
    names = tn.out_names(wk.read_snapshots(pt.write_store(tmp_path / "p.duckdb")))
    named = dict(names.drop_nulls("name").unique("gsis_id").iter_rows())
    assert named[pt.OUT_IDS[0]] == "Player 1-0" and named[pt.OUT_IDS[2]] == "Player 3-0"
    assert set(pt.MATE_IDS) | set(pt.OUT_IDS) == set(names.get_column("gsis_id"))


def test_the_pinned_tables_equal_the_reports_and_disclose_the_override() -> None:
    spec, _ = tp.load_pinned(2026)
    got = tn.pin_tables(spec.content)
    root = Path(__file__).resolve().parents[1] / "reports" / "teammate_out"
    for name in ("allocation", "backtest", "coverage", "events"):
        csv = pl.read_csv(root / f"{name}.csv", schema_overrides={"season": pl.String})
        mine = got[f"teammate_out_{name}"]
        assert mine.columns == list(TABLES[f"teammate_out_{name}"].names)
        assert mine.select(csv.columns).rows() == csv.rows(), name
    bt = got["teammate_out_backtest"].filter(pl.col("season") == "all")
    assert bt.get_column("candidate").to_list() == ["nothing", "pro_rata", "group", "role"]
    # the site uses role; the rule fixed before the backtest picked "nothing changes"
    assert bt.filter("chosen").get_column("candidate").to_list() == ["role"]
    assert bt.filter("rule_pick").get_column("candidate").to_list() == ["nothing"]
    assert set(got["teammate_out_coverage"].get_column("seasons")) == {"2017-2025"}
    v = tn.version_row(spec.content, pt.CREATED).row(0, named=True)
    params = json.loads(v["params"])
    assert (params["chosen"], params["rule_choice"]) == ("role", "nothing")
    assert params["chosen_by"].startswith("owner override") and params["ranges"]["level"] == 0.8
    assert v["module"] == "teammate_out" and v["training_seasons"] == list(range(2013, 2026))


def test_the_live_record_row(tmp_path: Path) -> None:
    _, rows = _frames(pt.write_store(tmp_path / "p.duckdb"))
    d = tn.TeammateOutData(2026, "alloc-test", pl.DataFrame(), rows, actuals=pt.actuals())
    r = tn.live_record(d, tn.no_published()).row(0, named=True)
    assert (r["n"], r["pending"], r["weeks"], r["team_weeks"], r["ranged"]) == (3, 2, 1, 1, 3)
    assert r["mae_points"] == pytest.approx(4 / 3) and r["mae_points_base"] == pytest.approx(3)
    assert (r["coverage"], r["top_hit"]) == (1.0, 1.0)
    # an absent starter played after all: BUF's rows are not graded
    late = tn.TeammateOutData(2026, "", pl.DataFrame(), rows,
                              actuals=pt.actuals(starters=(pt.OUT_IDS[0],)))  # fmt: skip
    r = tn.live_record(late, tn.no_published()).row(0, named=True)
    assert (r["n"], r["starter_played"], r["mae_points"]) == (0, 3, None)
    empty = tn.live_rows(pl.DataFrame(), 2026)
    assert empty.columns == list(TABLES["teammate_out_live"].names)
    assert empty.row(0)[:8] == (2026, 0, 0, 0, 0, 0, 0, 0)


def test_the_live_record_grades_the_published_snapshots_too(tmp_path: Path) -> None:
    # night 1 published snapshot A (week 5); night 2 runs on a fresh runner: its store holds
    # only snapshot B (week 6), and week 5's games are in (BUF and DAL)
    published = _frames(pt.write_store(tmp_path / "a.duckdb"))[1].select(tn.GRADED)
    b_lists, b_rows = _frames(pt.write_store(tmp_path / "b.duckdb", (pt.FRIDAY + timedelta(7),),
                                             week=6))  # fmt: skip
    played = pt.actuals({**dict.fromkeys(pt.MATE_IDS[:3], 8.0), pt.MATE_IDS[3]: 11.0,
                         pt.MATE_IDS[4]: 5.0})  # fmt: skip
    d = tn.TeammateOutData(2026, "alloc-test", b_lists, b_rows, actuals=played)
    live = tn.live_record(d, published).row(0, named=True)
    assert (live["n"], live["pending"], live["weeks"], live["team_weeks"]) == (5, 5, 1, 2)
    # the local store alone (a fresh runner's record) knows none of A's teammates
    alone = tn.live_record(d, tn.no_published()).row(0, named=True)
    assert (alone["n"], alone["pending"], alone["mae_points"]) == (0, 5, None)
    # a night with no local snapshot keeps the full record
    empty = tn.TeammateOutData(2026, "", *_frames(tmp_path / "none.duckdb"), actuals=played)
    again = tn.live_record(empty, published).row(0, named=True)
    assert (again["n"], again["pending"], again["mae_points"]) == (5, 0, live["mae_points"])


def test_published_and_local_snapshots_are_counted_once(tmp_path: Path) -> None:
    later = pt.FRIDAY + timedelta(hours=16)
    rows = _frames(pt.write_store(tmp_path / "a.duckdb", (pt.FRIDAY, later)))[1]
    published = rows.filter(pl.col("as_of") == pt.FRIDAY).select(tn.GRADED)
    both = tn.record_snapshots(rows, published, 2026)
    assert both.height == 10 and not both.select("as_of", "team", "gsis_id").is_duplicated().any()
    assert "kickoff_utc" in both.columns
    # a local snapshot differing from the published one of the same as-of never counts
    other = _frames(pt.write_store(tmp_path / "b.duckdb", shift=0.5))[1]
    got = tn.record_snapshots(other, published, 2026)
    assert got.get_column("pred_points").to_list() == published.sort(
        "as_of", "team", "gsis_id")["pred_points"].to_list()  # fmt: skip
    assert tn.record_snapshots(rows, published, 2025).is_empty()


def test_only_snapshots_absent_in_the_target_are_planned(tmp_path: Path) -> None:
    later = pt.FRIDAY + timedelta(hours=16)
    lists, rows = _frames(pt.write_store(tmp_path / "p.duckdb", (pt.FRIDAY, later)))
    d = tn.TeammateOutData(2026, "alloc-test", lists, rows)
    decisions, keys = wr.plan_snapshots(d, {(2026, 5, pt.FRIDAY)}, TEAMMATE_OUT)
    assert [x.label for x in decisions] == ["teammate out 2026-W05 as of 2026-10-10 12:00 UTC"]
    assert keys.get_column("as_of").to_list() == [later]


def _problems(d: tn.TeammateOutData, **kw: set[str]) -> list[str]:
    teams = kw.get("teams", {"BUF", "DAL", "KC", "SF"})
    players = kw.get("players", set(pt.MATE_IDS) | set(pt.OUT_IDS))
    return tn.problems(d, teams, players, kw.get("versions", {"alloc-test"}))


def test_the_validation(tmp_path: Path) -> None:
    lists, rows = _frames(pt.write_store(tmp_path / "p.duckdb"))
    d = tn.TeammateOutData(2026, "alloc-test", lists, rows)
    assert _problems(d) == []
    assert _problems(d, players=set(pt.MATE_IDS)) == [
        "some Teammate-out rows name a player missing from dim_player"]  # fmt: skip
    assert "unknown model versions" in _problems(d, versions=set())[0]
    odd = rows.with_columns(pl.when(pl.col("role") == "RB2").then(pl.lit(30.0))
                            .otherwise("points_lo").alias("points_lo"))  # fmt: skip
    assert _problems(tn.TeammateOutData(2026, "", lists, odd)) == [
        "1 Teammate-out rows have points outside their 80% range"]  # fmt: skip
    twice = pl.concat([rows, rows.head(1)])
    got = _problems(tn.TeammateOutData(2026, "", lists, twice))
    assert got[:2] == [
        "a Teammate-out snapshot lists a teammate twice",
        "a Teammate-out snapshot's teammate count differs from its rows",
    ]
    short = rows.with_columns(pl.lit(2, pl.Int32).alias("n_out"))
    assert "differ from n_out" in _problems(tn.TeammateOutData(2026, "", lists, short))[0]
