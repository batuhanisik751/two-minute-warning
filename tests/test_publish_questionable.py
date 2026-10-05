"""Feature #1, the Questionable list's publish (src/twm/publish/questionable.py): synthetic
snapshots, the pinned table's tables, the history check and the validation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from tests import publish_questionable_synthetic as pq
from twm.modules.questionable import production as qp
from twm.modules.questionable import weekly as wk
from twm.publish import questionable as qn
from twm.publish import write as wr
from twm.publish.collect import PublishInputError
from twm.publish.tables import TABLES


def test_snapshots_become_lists_and_rows_in_the_tables_layout(tmp_path: Path) -> None:
    later = pq.FRIDAY + timedelta(hours=16)
    store = pq.write_store(tmp_path / "p.duckdb", (pq.FRIDAY, later))
    lists, rows = qn.snapshot_frames(wk.read_snapshots(store))
    assert lists.columns == list(TABLES["questionable_list"].names)
    assert rows.columns == list(TABLES["questionable_row"].names)
    assert lists.get_column("as_of").to_list() == [pq.FRIDAY, later]  # aware UTC
    assert lists.get_column("n_players").to_list() == [3, 3]
    assert set(lists.get_column("source")) == {"observed"}
    first = rows.filter(pl.col("as_of") == pq.FRIDAY)
    # by kickoff, then chance: the Doubtful player kicks off first
    assert first.get_column("report_status").to_list() == ["Doubtful", "Questionable",
                                                           "Questionable"]  # fmt: skip
    assert first.get_column("kickoff")[0] == datetime(2026, 10, 11, 17, tzinfo=UTC)
    assert first.get_column("season_ppg").to_list()[0] is None


def test_an_empty_store_gives_empty_frames(tmp_path: Path) -> None:
    lists, rows = qn.snapshot_frames(wk.read_snapshots(tmp_path / "none.duckdb"))
    assert lists.is_empty() and rows.is_empty()
    assert lists.schema["as_of"] == pl.Datetime("us", "UTC")


def test_a_snapshot_mixing_model_versions_is_refused(tmp_path: Path) -> None:
    store = pq.write_store(tmp_path / "p.duckdb")
    snaps = wk.read_snapshots(store).with_columns(
        pl.Series("model_version", ["a", "b", "a"]))  # fmt: skip
    with pytest.raises(PublishInputError, match="mixes model versions"):
        qn.snapshot_frames(snaps)


def test_the_pinned_tables_equal_the_committed_reports() -> None:
    spec, _ = qp.load_pinned(2026)
    got = qn.pin_tables(spec.content)
    root = Path(__file__).resolve().parents[1] / "reports" / "questionable"
    bt = pl.read_csv(root / "backtest.csv", schema_overrides={"season": pl.String})
    assert got["questionable_backtest"].height == bt.height
    assert got["questionable_backtest"].select("grouping", "season", "chosen", "n").rows() == \
        bt.select("grouping", "season", "chosen", "n").rows()  # fmt: skip
    cal = pl.read_csv(root / "calibration.csv")
    assert got["questionable_calibration"].get_column("bucket").to_list() == cal["bucket"].to_list()
    assert got["questionable_calibration"].get_column("line").to_list() == [1, 2, 3, 4, 5]
    v = qn.version_row(spec.content, pq.CREATED)
    assert v.row(0, named=True)["feature_list"] == ["report_status", "missed_prev", "position"]
    assert v.row(0, named=True)["training_seasons"] == list(range(2016, 2026))


def test_the_history_counts_tags_by_practice_and_is_checked_against_the_pin() -> None:
    h = pq.history()
    assert h.columns == list(TABLES["questionable_history"].names)
    q_all = h.filter((pl.col("report_status") == "Questionable") & (pl.col("practice") == "all"))
    assert q_all.select("n", "played").row(0) == (10, 6)
    assert h.get_column("report_status").to_list()[0] == "Questionable"
    good = [{"report_status": "Questionable", "n": 10}, {"report_status": "Out", "n": 30}]
    assert qn.check_history(h, good) == []
    assert qn.check_history(h, [{"report_status": "Out", "n": 31}]) == [
        "Out: the warehouse has 30 history rows, the pinned table 31"]  # fmt: skip


def test_the_live_record_rows(tmp_path: Path) -> None:
    snaps = wk.read_snapshots(pq.write_store(tmp_path / "p.duckdb"))
    played = pl.DataFrame({"season": [2026], "week": [5], "team": ["DAL"],
                           "gsis_id": [pq.gid(0, 1)], "offense_snaps": [40]})  # fmt: skip
    live = qn.live_rows(wk.grade(snaps, played), 2026)
    assert live.get_column("report_status").to_list() == ["all", "Questionable", "Doubtful"]
    assert live.row(0) == (2026, "all", 1, 0.65, 1.0, 2, 1)  # graded: player 1, who played
    assert live.row(2, named=True)["n"] == 0 and live.row(2, named=True)["actual"] is None
    empty = qn.live_rows(pl.DataFrame(), 2026)
    assert empty.get_column("n").to_list() == [0, 0, 0] and empty.row(0)[-2:] == (0, 0)


def test_only_snapshots_absent_in_the_target_are_planned(tmp_path: Path) -> None:
    later = pq.FRIDAY + timedelta(hours=16)
    store = pq.write_store(tmp_path / "p.duckdb", (pq.FRIDAY, later))
    lists, rows = qn.snapshot_frames(wk.read_snapshots(store))
    q = qn.QuestionableData(2026, "lookup-test", lists, rows)
    decisions, keys = wr.plan_snapshots(q, {(2026, 5, pq.FRIDAY)})
    assert [d.label for d in decisions] == ["questionable 2026-W05 as of 2026-10-10 12:00 UTC"]
    assert keys.get_column("as_of").to_list() == [later]
    assert wr.plan_snapshots(q, set())[1].height == 2


def _frames(store: Path) -> tuple[pl.DataFrame, pl.DataFrame]:
    return qn.snapshot_frames(wk.read_snapshots(store))


def test_the_live_record_grades_the_published_snapshots_too(tmp_path: Path) -> None:
    # night 1 published snapshot A (week 5); night 2 runs on a fresh runner: its store holds
    # only snapshot B (week 6), and week 5's games are in (player 0 sat, 1 and 2 played)
    published = _frames(pq.write_store(tmp_path / "a.duckdb"))[1].select(qn.GRADED)
    week6 = (pq.FRIDAY + timedelta(days=7),)
    b_lists, b_rows = _frames(pq.write_store(tmp_path / "b.duckdb", week6, week=6))
    q = qn.QuestionableData(2026, "lookup-test", b_lists, b_rows,
                            played=pq.game_snaps({0: 0, 1: 40, 2: 30}))  # fmt: skip
    live = qn.live_record(q, published)
    assert live.row(0)[:3] == (2026, "all", 3) and live.row(0)[-2:] == (3, 1)  # B: pending
    assert live.row(0, named=True)["predicted"] == pytest.approx((0.01 + 0.65 + 0.70) / 3)
    assert live.row(0, named=True)["actual"] == pytest.approx(2 / 3)
    assert live.select("n", "actual").rows()[1:] == [(2, 1.0), (1, 0.0)]  # Q, D
    # the local store alone (the bug: a fresh runner's record) knows none of A's players
    assert qn.live_record(q, qn.no_published()).row(0)[2:] == (0, None, None, 3, 0)
    # a night with no local snapshot keeps the full record
    empty = qn.QuestionableData(2026, "", *_frames(tmp_path / "none.duckdb"), played=q.played)
    assert qn.live_record(empty, published).row(0)[2:] == (3, *live.row(0)[3:5], 0, 1)


def test_published_and_local_snapshots_are_counted_once(tmp_path: Path) -> None:
    later = pq.FRIDAY + timedelta(hours=16)
    rows = _frames(pq.write_store(tmp_path / "a.duckdb", (pq.FRIDAY, later)))[1]
    published = rows.filter(pl.col("as_of") == pq.FRIDAY).select(qn.GRADED)
    # the local store still holds the published snapshot (identical) and a new one
    both = qn.record_snapshots(rows, published, 2026)
    assert both.height == 6 and not both.select("as_of", "gsis_id").is_duplicated().any()
    assert both.columns == [*qn.GRADED[:5], "kickoff_utc", *qn.GRADED[6:]]
    assert qn.record_snapshots(rows, rows.select(qn.GRADED), 2026).height == 6
    q = qn.QuestionableData(2026, "lookup-test", pl.DataFrame(), rows, played=pq.game_snaps())
    assert qn.live_record(q, published).equals(qn.live_record(q, qn.no_published()))
    # a local snapshot differing from the published one of the same as-of never counts
    other = _frames(pq.write_store(tmp_path / "b.duckdb", shift=0.1))[1]
    got = qn.record_snapshots(other, published, 2026)
    assert got.get_column("play_chance").to_list() == published["play_chance"].to_list()
    # another season's rows are not this season's record
    assert qn.record_snapshots(rows, published, 2025).is_empty()
