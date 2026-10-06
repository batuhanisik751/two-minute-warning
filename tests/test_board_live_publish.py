"""Step I6b: the season's LIVE Cliff board, once a season: when (config
``as_of.board.live_publish``), the window (open / closed / never), the list note naming the depth
chart, the append-only store, the published ``board_list.note`` and the job's ``board_score``
stage (skipped outside the window, run inside it, never twice). Offline: synthetic warehouses,
the committed board pin, a fake executor."""

from __future__ import annotations

from datetime import UTC, datetime

import duckdb
import polars as pl
import pytest
from pydantic import ValidationError

from tests.test_board_production import AS_OF, KICKOFF, REPORTS, SEASON, _run, _stored
from twm import config
from twm.modules.board import live as bl
from twm.modules.board import live_publish as lp
from twm.modules.board import production as bp


def _cfg(rule):
    return config.BoardAsOf(post_draft="06-01", preseason="week1_kickoff_eve", live_publish=rule)


def test_the_config_rule_default_and_forms() -> None:
    assert config.settings().as_of.board.live_publish == "week1_kickoff_eve"  # owner picks later
    assert config.BoardAsOf(post_draft="06-01", preseason="week1_kickoff_eve").live_publish == (
        "week1_kickoff_eve")  # fmt: skip
    assert _cfg("08-30").live_publish == "08-30"
    assert _cfg("days_before_week1:7").live_publish == "days_before_week1: 7"
    assert _cfg({"days_before_week1": 3}).live_publish == "days_before_week1: 3"
    for bad in ("02-30", "days_before_week1: x", "days_before_week1: 99", "tuesday", ""):
        with pytest.raises(ValidationError):
            _cfg(bad)


def test_the_live_as_of_of_each_rule() -> None:
    assert lp.live_as_of(KICKOFF, "week1_kickoff_eve") == AS_OF  # = the preseason anchor
    assert lp.live_as_of(KICKOFF, "days_before_week1: 7") == datetime(2026, 9, 3, tzinfo=UTC)
    assert lp.live_as_of(KICKOFF, "days_before_week1: 0") == datetime(2026, 9, 10, tzinfo=UTC)
    assert lp.live_as_of(KICKOFF, "09-01") == datetime(2026, 9, 1, tzinfo=UTC)


@pytest.fixture
def games(tmp_path):
    db = tmp_path / "w.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE fact_game AS SELECT 2026 AS season, 'REG' AS game_type, 1 AS week, "
                "TIMESTAMP '2026-09-10 00:20:00' AS kickoff_utc")  # fmt: skip
    con.close()
    return db


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def test_the_window_opens_at_the_as_of_and_closes_at_the_kickoff(games) -> None:
    eve = "week1_kickoff_eve"
    w = lp.window(games, SEASON, at("2026-09-09T23:19"), eve)
    assert not w.open and w.reason.startswith("not open yet: 2026-09-09 23:20 UTC")
    w = lp.window(games, SEASON, at("2026-09-09T23:20"), eve)
    assert w.open and (w.as_of, w.kickoff) == (AS_OF, KICKOFF)
    w = lp.window(games, SEASON, at("2026-09-10T00:20"), eve)
    assert not w.open and w.reason.startswith("closed: 2026 has kicked off")
    # the nightly 10:47 UTC run is in an earlier rule's window, never in the kickoff eve's hour
    nightly = at("2026-09-09T10:47")
    assert not lp.window(games, SEASON, nightly, eve).open
    assert lp.window(games, SEASON, nightly, "days_before_week1: 7").open
    assert lp.window(games, SEASON, nightly, "09-05").open
    w = lp.window(games, SEASON, nightly, "09-15")
    assert not w.open and w.reason.startswith("never open")
    w = lp.window(games, 2027, at("2027-09-01T10:47"), eve)  # next year's schedule not built
    assert not w.open and w.as_of is None and "no week-1 kickoff of 2027" in w.reason


class _View:
    """A stand-in AsOfView over an in-memory fact_depth_chart (the rows visible at the as-of)."""

    def __init__(self, rows: list[tuple]) -> None:
        self.con = duckdb.connect()
        self.con.execute("CREATE TABLE fact_depth_chart (season INT, source_format VARCHAR, "
                         "team VARCHAR, dt TIMESTAMP)")  # fmt: skip
        if rows:
            self.con.executemany("INSERT INTO fact_depth_chart VALUES (?, ?, ?, ?)", rows)

    def sql(self, query: str) -> pl.DataFrame:
        return self.con.execute(query).pl()


def test_the_note_names_the_rule_and_the_depth_chart() -> None:
    pulls = [(2026, "daily", "ARI", datetime(2026, 9, 1, 6)), (2026, "daily", "ARI",
             datetime(2026, 9, 2, 6)), (2026, "daily", "ATL", datetime(2026, 9, 1, 7)),
             (2025, "daily", "BUF", datetime(2025, 9, 2, 7))]  # fmt: skip
    chart = lp.chart_used(_View(pulls), 2026)
    assert chart == (2, datetime(2026, 9, 1, 7), datetime(2026, 9, 2, 6))
    early = lp.note_text("days_before_week1: 7", datetime(2026, 9, 3, tzinfo=UTC), chart)
    assert early.startswith("Read as of 2026-09-03 00:00 UTC (days_before_week1: 7), earlier "
                            "than the kickoff eve the models learned from")  # fmt: skip
    assert "each team's latest daily depth chart then (2 teams; pulls of 2026-09-01 to " \
           "2026-09-02)" in early  # fmt: skip
    eve = lp.note_text("week1_kickoff_eve", AS_OF, lp.chart_used(_View(pulls[:1]), 2026))
    assert eve.startswith("Read as of the kickoff eve, 2026-09-09 23:20 UTC, from each team's")
    assert "(1 teams; pulls of 2026-09-01)" in eve
    none = lp.note_text("09-01", datetime(2026, 9, 1, tzinfo=UTC), lp.chart_used(_View([]), 2026))
    assert "from the week-1 depth charts (no daily pull was public then)" in none


def test_a_live_board_keeps_its_note_and_is_never_overwritten(tmp_path) -> None:
    from twm.publish import board_lists as pb

    models = bp.load_pinned(SEASON)[0]
    store = tmp_path / "predictions.duckdb"
    run = _run(models, "live")
    run.note = "Read as of 2026-09-03 00:00 UTC (days_before_week1: 7), from ..."
    assert bl.store_board(run, store)["predictions"] == 12 and bl.live_rows(store, SEASON) == 12
    again = _run(models, "live", shift=0.2)
    again.note = "a later night's note"
    assert bl.store_board(again, store)["kept"] == 12  # the first live board stays
    assert bl.live_rows(store, SEASON + 1) == 0
    reasons = _stored(store)["reasons_json"].to_list()
    assert all('"note": "Read as of 2026-09-03' in r for r in reasons)
    con = duckdb.connect()
    con.execute("CREATE TABLE dim_player AS SELECT * FROM (VALUES ('00-0000001')) t(gsis_id)")
    got = pb.collect_board(store, REPORTS, SEASON, datetime(2026, 10, 2, tzinfo=UTC), con)
    lists = got.data.lists
    assert lists.filter(pl.col("kind") == "live")["note"].to_list() == [run.note]
    assert lists.filter(pl.col("kind") == "backtest")["note"].null_count() == 19  # the pin's


def _job(tmp_path, window, codes=None, now="2026-09-08T10:47", cron="47 10 * * *"):
    from dataclasses import dataclass

    from tests.test_pipeline_runner import Fake, run

    @dataclass
    class BoardFake(Fake):
        def hooks(self):
            h = super().hooks()
            h.board_window = window
            return h

    fake = BoardFake(codes=dict(codes or {}))
    return fake, run(fake, tmp_path, now, cron=cron)


def test_the_job_skips_the_board_outside_its_window(tmp_path) -> None:
    seen = []

    def closed(season, now):
        seen.append((season, now))
        return False, "the 2026 live board: closed: 2026 has kicked off"

    fake, res = _job(tmp_path, closed)
    assert seen == [(2026, at("2026-09-08T10:47"))] and "board_score" not in fake.stages()
    st = res.stage("board_score")
    assert st.status == "skipped" and "kicked off" in st.detail and res.exit_code == 0
    assert "board" not in res.modules and fake.stages()[-1] == "publish"


def test_the_job_scores_the_live_board_once_in_its_window(tmp_path) -> None:
    fake, res = _job(tmp_path, lambda s, now: (True, "the 2026 live board: open: ..."))
    assert fake.stages()[-5:] == ["board_score", "questionable", "teammate_out",
                                  "playoff_planner", "publish"]  # fmt: skip
    assert res.exit_code == 0
    args = fake.args("board_score")[0]
    assert args[:5] == ["board", "score", "--season", "2026", "--live-publish"]
    assert "--now" in args  # a pretend clock: stored as reconstructed, never live
    assert res.modules["board"]["score"] == "scored" and res.stage("board_score").status == "ok"
    assert "**Cliff board:** week 0 (the preseason board): scored" in (
        tmp_path / "run" / "summary.md").read_text()  # fmt: skip
    # the window closed between the check and the command (exit 3): skipped, still published
    fake, res = _job(tmp_path / "b", lambda s, now: (True, "open"), {"board_score": 3})
    assert res.stage("board_score").status == "skipped" and fake.stages()[-1] == "publish"
    # any other failure stops the run before the publish
    fake, res = _job(tmp_path / "c", lambda s, now: (True, "open"), {"board_score": 1})
    assert res.exit_code == 1 and "publish" not in fake.stages()


def test_the_window_hook_keeps_a_stored_live_board(tmp_path, monkeypatch, games) -> None:
    from twm.pipeline import runner as rn

    store = tmp_path / "predictions.duckdb"
    monkeypatch.setattr(config.Settings, "path", lambda self, name: games, raising=True)
    monkeypatch.setattr("twm.predictions.default_path", lambda: store)
    due, why = rn.board_window(SEASON, at("2026-09-09T23:30"))
    assert due and why.startswith("the 2026 live board: open")
    bl.store_board(_run(bp.load_pinned(SEASON)[0], "live"), store)
    due, why = rn.board_window(SEASON, at("2026-09-09T23:40"))
    assert not due and "already stored (12 rows): kept" in why
    assert not rn.board_window(SEASON, at("2026-09-10T01:00"))[0]
