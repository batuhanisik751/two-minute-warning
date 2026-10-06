"""The playoff planner's part of `twm publish` (feature #6): its snapshots as published rows,
the pinned spec's tables, the live record (empty until the last playoff week is played) and
the validation. Synthetic: tests/publish_playoff_planner_synthetic.py; the Postgres side
(append-only, empty-store nights) is in tests/test_publish_postgres.py."""

from __future__ import annotations

import csv
from datetime import timedelta
from pathlib import Path

import polars as pl
import pytest

from tests import publish_playoff_planner_synthetic as pps
from twm.config import ROOT
from twm.modules.playoff_planner import production as pr
from twm.modules.playoff_planner import weekly as wk
from twm.publish import playoff_planner as pp
from twm.publish import write as wr
from twm.publish.collect import PublishInputError
from twm.publish.tables import PLAYOFF_PLANNER, TABLES


def _data(store: Path, played: pl.DataFrame | None = None) -> pp.PlayoffPlannerData:
    spec, _ = pr.load_pinned(2026)
    lists, rows = pp.snapshot_frames(wk.read_snapshots(store))
    return pp.PlayoffPlannerData(2026, "matchup-test", lists, rows, pp.pin_tables(spec.content),
                                 actuals=pps.actuals(()) if played is None else played)  # fmt: skip


def test_snapshots_become_lists_and_rows_in_the_tables_layout(tmp_path: Path) -> None:
    store = pps.write_store(tmp_path / "s.duckdb", (5, 6))
    lists, rows = pp.snapshot_frames(wk.read_snapshots(store))
    assert lists.columns == list(TABLES["playoff_planner_list"].names)
    assert rows.columns == list(TABLES["playoff_planner_row"].names)
    assert lists.select("through_week", "n_teams", "n_rows").rows() == [(5, 4, 72), (6, 4, 72)]
    assert lists["as_of"][1] - lists["as_of"][0] == timedelta(days=7)
    assert str(lists.schema["as_of"]) == "Datetime(time_unit='us', time_zone='UTC')"
    byes = rows.filter(pl.col("opponent").is_null())
    assert byes.height == 2 * 6 * 2 and byes["rating"].null_count() == byes.height  # DAL, KC wk 17
    te = rows.filter(pl.col("position").is_in(["TE", "K"]) & pl.col("opponent").is_not_null())
    assert set(te["rating"].to_list()) == {1.0} and te["rating_rank"].null_count() == te.height
    first = rows.filter(pl.col("through_week") == 5).head(6)
    assert first["position"].to_list() == ["QB", "RB", "WR", "TE", "K", "DST"]


def test_an_empty_store_gives_empty_frames(tmp_path: Path) -> None:
    lists, rows = pp.snapshot_frames(wk.read_snapshots(tmp_path / "none.duckdb"))
    assert lists.is_empty() and rows.is_empty()
    assert lists.columns == list(TABLES["playoff_planner_list"].names)


def test_a_snapshot_mixing_as_ofs_or_versions_is_refused(tmp_path: Path) -> None:
    snaps = wk.read_snapshots(pps.write_store(tmp_path / "s.duckdb"))
    other = pl.when(pl.col("team") == "SF").then(pl.lit("other"))
    odd = snaps.with_columns(other.otherwise(pl.col("model_version")).alias("model_version"))
    with pytest.raises(PublishInputError, match="through week 5 mixes"):
        pp.snapshot_frames(odd)


def _csv(name: str) -> list[dict[str, str]]:
    with (ROOT / "reports" / "playoff_planner" / f"{name}.csv").open() as f:
        return list(csv.DictReader(f))


def test_the_pinned_tables_equal_the_reports_and_replay_the_rule() -> None:
    spec, _ = pr.load_pinned(2026)
    t = pp.pin_tables(spec.content)
    for name in ("effects", "stability", "late_weeks"):
        got = t[f"playoff_planner_{name}"]
        assert got.columns == list(TABLES[f"playoff_planner_{name}"].names)
        want = {tuple(r.values()) for r in _csv(name)}
        assert {tuple(str(v) if not isinstance(v, float) else f"{v:.6f}" for v in r)
                for r in got.rows()} == want  # fmt: skip
    choice = t["playoff_planner_choice"]
    assert dict(choice.select("position", "candidate").rows()) == spec.chosen
    assert choice.filter(pl.col("position") == "QB")["path"][0] == "none > shrunk > adjusted"
    bt = t["playoff_planner_backtest"]
    pooled = {
        (r["position"], r["candidate"]): float(r["mae"])
        for r in _csv("backtest")
        if r["horizon"] == "all" and r["season"] == "all" and r["position"] != "all"
    }
    assert {(p, c): m for p, c, m in bt.select("position", "candidate", "mae").rows()} == pooled
    won = {
        (p, c): (v, w, o)
        for p, c, v, w, o in bt.select(
            "position", "candidate", "vs", "seasons_won", "took_over"
        ).rows()
    }
    assert won[("QB", "shrunk")] == ("none", 7, True) and won[("QB", "adjusted")] == (
        "shrunk", 10, True)  # fmt: skip
    assert won[("RB", "adjusted")] == ("shrunk", 5, False)
    assert won[("TE", "shrunk")] == ("none", 6, False) and won[("TE", "adjusted")] == (
        "none", 6, False)  # fmt: skip
    assert won[("K", "none")] == (None, None, True)
    picks = bt.filter(pl.col("rule_pick")).select("position", "candidate").rows()
    assert dict(picks) == spec.content["rule_choice"]
    assert bt.filter(pl.col("chosen")).height == 6


def test_a_pin_the_rule_does_not_reproduce_is_refused() -> None:
    spec, _ = pr.load_pinned(2026)
    bad = {**spec.content, "rule_choice": {**spec.content["rule_choice"], "TE": "shrunk"}}
    with pytest.raises(PublishInputError, match="picks TE none, the pin says shrunk"):
        pp.backtest_rows(bad)


def test_the_version_row_discloses_the_choice() -> None:
    spec, _ = pr.load_pinned(2026)
    v = pp.version_row(spec.content, pps.CREATED).row(0, named=True)
    assert v["module"] == "playoff_planner" and v["model"] == "matchup"
    assert v["training_seasons"] == list(range(2006, 2013)) and v["test_season"] == 2026
    import json

    params = json.loads(v["params"])
    assert params["chosen"] == spec.chosen and params["chosen_by"] == "rule"
    assert params["playoff_weeks"] == [15, 16, 17]


def test_the_live_record_is_empty_until_the_last_playoff_week_is_played(tmp_path: Path) -> None:
    store = pps.write_store(tmp_path / "s.duckdb")
    for weeks in ((), (15,), (15, 16)):
        d = _data(store, pps.actuals(weeks))
        live = pp.live_record(d, pp.no_published())
        assert live.is_empty() and live.columns == list(TABLES["playoff_planner_live"].names)
    d = _data(store, pps.actuals((15, 16, 17), factor=1.1))
    live = pp.live_record(d, pp.no_published())
    assert live["position"].to_list() == ["QB", "RB", "WR", "TE", "K", "DST", "all"]
    row = {r["position"]: r for r in live.iter_rows(named=True)}
    # 4 + 4 + 2 games-sides per position in weeks 15-17 (week 17: BUF and SF only)
    assert (row["QB"]["n"], row["QB"]["pending"], row["QB"]["weeks"]) == (10, 0, 3)
    assert row["all"]["n"] == 60 and row["all"]["weeks"] == 3
    assert row["TE"]["mae_rating"] == pytest.approx(0.1) and row["TE"]["mae_flat"] == (
        pytest.approx(0.1))  # fmt: skip
    rated = d.rows.filter((pl.col("position") == "QB") & pl.col("opponent").is_not_null())
    want = (rated["rating"] - 1.1).abs().mean()
    assert row["QB"]["mae_rating"] == pytest.approx(want)


def test_the_live_record_grades_the_published_snapshots_too(tmp_path: Path) -> None:
    local = _data(pps.write_store(tmp_path / "s.duckdb"), pps.actuals((15, 16, 17)))
    key = ["season", "through_week"]
    published = local.rows.join(local.lists.select(*key, "as_of"), on=key).select(
        pp.no_published().columns)  # fmt: skip
    empty = _data(tmp_path / "empty.duckdb", pps.actuals((15, 16, 17)))
    assert pp.live_record(empty, pp.no_published()).is_empty()
    assert pp.live_record(empty, published).equals(pp.live_record(local, pp.no_published()))
    # a store still holding the published snapshot counts it once
    assert pp.live_record(local, published).equals(pp.live_record(local, pp.no_published()))
    # a different local copy of a published (season, through_week) never replaces it
    other = _data(pps.write_store(tmp_path / "o.duckdb", shift=0.2), pps.actuals((15, 16, 17)))
    snaps = pp.record_snapshots(other, published)
    assert snaps.height == pps.N_ROWS and snaps["rating"].equals(
        pp.record_snapshots(local, pp.no_published())["rating"])  # fmt: skip
    # a later week's local snapshot is graded next to the published one
    later = _data(pps.write_store(tmp_path / "l.duckdb", (6,)), pps.actuals((15, 16, 17)))
    assert pp.record_snapshots(later, published).height == 2 * pps.N_ROWS


def test_only_snapshots_absent_in_the_target_are_planned(tmp_path: Path) -> None:
    d = _data(pps.write_store(tmp_path / "s.duckdb", (5, 6)))
    decisions, keys = wr.plan_snapshots(d, {(2026, 5)}, PLAYOFF_PLANNER)
    assert keys.rows() == [(2026, 6)]
    assert [x.label for x in decisions] == ["playoff planner 2026-W06 as of 2026-10-20 06:00 UTC"]
    assert wr.plan_snapshots(d, set(), PLAYOFF_PLANNER)[1].height == 2


def test_the_validation(tmp_path: Path) -> None:
    d = _data(pps.write_store(tmp_path / "s.duckdb"))
    teams, versions = set(pps.TEAMS), {"matchup-test"}
    assert pp.problems(d, teams, versions) == []

    def bad(**cols: pl.Expr) -> list[str]:
        rows = d.rows.with_columns(**cols)
        return pp.problems(pp.PlayoffPlannerData(2026, "v", d.lists, rows), teams, versions)

    qb = pl.col("position") == "QB"
    fb = pl.when(qb).then(pl.lit("FB")).otherwise(pl.col("position"))
    assert bad(position=fb) == ["some playoff-planner rows are not a QB, RB, WR, TE, K or DST"]
    assert "some playoff-planner ratings are not positive numbers" in bad(
        rating=pl.when(qb).then(-1.0).otherwise(pl.col("rating")))  # fmt: skip
    assert bad(rating=pl.col("rating").fill_null(1.0)) == [
        "some playoff-planner rows have a rating without an opponent or the reverse"]  # fmt: skip
    te = pl.col("position") == "TE"
    assert bad(rating_rank=pl.when(te).then(1).otherwise(pl.col("rating_rank"))) == [
        "some unrated (candidate 'none') playoff-planner rows carry a rating or rank"]  # fmt: skip
    assert bad(rating_rank=pl.col("rating_rank") * 40) == [
        "some playoff-planner ranks are outside 1-32"]  # fmt: skip
    assert "name a team that is not a current franchise" in " ".join(
        bad(opponent=pl.when(qb).then(pl.lit("XXX")).otherwise(pl.col("opponent")))
    )
    assert pp.problems(d, teams, set()) == [
        "playoff-planner snapshots of unknown model versions: ['matchup-test']"]  # fmt: skip
    short = pp.PlayoffPlannerData(2026, "v", d.lists, d.rows.head(10))
    assert pp.problems(short, teams, versions) == [
        "a playoff-planner snapshot's row count differs from its rows"]  # fmt: skip
    twice = pp.PlayoffPlannerData(2026, "v", d.lists, pl.concat([d.rows, d.rows.head(1)]))
    assert "a playoff-planner snapshot lists a (week, team, position) twice" in pp.problems(
        twice, teams, versions)  # fmt: skip
