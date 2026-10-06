"""Step I6b: `twm offseason status` on synthetic pins, labels and warehouses: per module the
pinned season vs the season due and whether the frozen history covers the finished season (the
board's in snapshot seasons), the departure labels, and the next step in the checklist's order.
Read-only by construction (nothing here writes a project file)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import polars as pl
import yaml

from twm import offseason as off
from twm import pins as pn

HISTORY = {"waiver_radar": "2014-{e}", "streamer_k": "2013-{e}", "streamer_dst": "2013-{e}",
           "regression_watch": "2011-{e}", "decisions": "2006-{e}", "hot_seat": "2006-{e}",
           "board": "2007-{b}", "questionable": "2016-{e}", "startsit": "2020-{e}",
           "teammate_out": "2016-{e}", "playoff_planner": "2013-{e}",
           "coach_tendencies": "1999-{e}"}  # fmt: skip


def pin_file(tmp_path: Path, season: int, **override: int) -> dict[str, pn.Pin]:
    """A synthetic config/production_models.yaml: every module pinned for ``season`` with the
    history ending the season before (the board's: two before, in snapshot seasons)."""
    raw = {}
    for m, h in HISTORY.items():
        s = override.get(m, season)
        raw[m] = {"season": s, "model_version": f"{m}-x", "file": f"a/{m}", "sha256": "ab",
                  "backtest": {"seasons": h.format(e=s - 1, b=s - 2)}}  # fmt: skip
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "production_models.yaml"
    path.write_text(yaml.safe_dump(raw))
    return pn.read_pins(path)


def labels(rows: list[tuple[str, int, str]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=["candidate_id", "last_season", "verified_by_owner"],
                        orient="row")  # fmt: skip


def cands(rows: list[tuple[str, int, str]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=["candidate_id", "last_season", "change_kind"],
                        orient="row")  # fmt: skip


def test_history_coverage_and_module_steps(tmp_path: Path) -> None:
    pins = pin_file(tmp_path, 2026)
    assert off.history_end("2014-2025") == 2025 and off.history_end("") is None
    now = [off.module_status(m, pins[m], 2025) for m in off.MODULES]
    assert all(m.current and m.step == "ok" for m in now)
    board = off.module_status("board", pins["board"], 2025)
    assert board.history == "2007-2024" and board.covers  # snapshot 2024 = the 2025 board
    after = [off.module_status(m, pins[m], 2026) for m in off.MODULES]
    assert not any(m.current for m in after) and all(m.due == 2027 for m in after)
    assert all(m.covers is False and m.step.startswith("re-approve for 2027 on the Mac")
               for m in after)  # fmt: skip
    assert "uv run twm board pin" in after[list(off.MODULES).index("board")].step
    assert "uv run twm league startsit-pin" in after[list(off.MODULES).index("startsit")].step
    assert "uv run twm teammate_out pin" in after[list(off.MODULES).index("teammate_out")].step
    assert "uv run twm playoff_planner pin" in after[-2].step
    assert "uv run twm coach tendencies-pin" in after[-1].step  # C10c: the frozen history
    missing = off.module_status("hot_seat", None, 2026)
    assert missing.pinned is None and missing.step.startswith("not pinned")


def test_labels_of_the_finished_season() -> None:
    lb = labels([("a", 2026, "y"), ("b", 2026, ""), ("c", 2025, "y")])
    cd = cands([("a", 2026, "offseason"), ("d", 2026, "in_season"), ("e", 2026, "none_recorded")])
    got = off.labels_status(lb, cd, 2026)
    assert (got.rows, got.verified, got.missing, got.done) == (2, 1, 1, False)
    done = off.labels_status(labels([("a", 2026, "y"), ("d", 2026, "y")]), cd, 2026)
    assert done.done and done.missing == 0  # a none_recorded reminder needs no row
    assert not off.labels_status(None, None, 2027).done  # nothing labelled yet


def test_the_next_step_follows_the_checklist(tmp_path: Path) -> None:
    old, new = pin_file(tmp_path, 2026), pin_file(tmp_path / "n", 2027)
    ok25 = off.labels_status(labels([("c", 2025, "y")]), None, 2025)
    ok26 = off.labels_status(labels([("a", 2026, "y")]), None, 2026)
    todo26 = off.labels_status(labels([("a", 2026, "")]), None, 2026)

    def nxt(pins, finished, current, lb, today, started=False):
        return off.build_status(pins, finished=finished, current_season=current, labels=lb,
                                board="the 2027 live board: not open yet", today=today,
                                started=started).next_step  # fmt: skip

    assert "season is under way" in nxt(old, 2025, 2026, ok25, date(2026, 10, 2), True)
    assert nxt(old, 2025, 2026, ok25, date(2026, 8, 2)).startswith("4. every module")
    assert nxt(old, 2025, 2026, ok25, date(2027, 2, 25)).startswith("1. refresh the data")
    assert nxt(old, None, 2026, None, date(2026, 10, 2)).startswith("1. refresh the data")
    assert nxt(old, 2026, 2026, todo26, date(2027, 2, 25)).startswith(
        "1. set current_season: 2027")  # fmt: skip
    assert nxt(old, 2026, 2027, todo26, date(2027, 2, 25)).startswith(
        "1. label the 2026 coach departures")  # fmt: skip
    step = nxt(old, 2026, 2027, ok26, date(2027, 3, 1))
    assert step.startswith("2. 1/7 waiver_radar: re-approve for 2027")
    half = pin_file(tmp_path / "h", 2026, waiver_radar=2027, streamer_k=2027)
    assert nxt(half, 2026, 2027, ok26, date(2027, 3, 1)).startswith("2. 3/7 streamer_dst")
    done = nxt(new, 2026, 2027, ok26, date(2027, 4, 1))
    assert done.startswith("4. every module is approved") and "timemachine verify" in done
    assert done.endswith("the 2027 live board: not open yet")


def test_the_status_reads_the_warehouse_and_prints_every_module(tmp_path: Path) -> None:
    db = tmp_path / "w.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE fact_game AS SELECT * FROM (VALUES (2025, 'SB', 7), (2026, 'REG', "
                "NULL), (2026, 'SB', NULL)) t(season, game_type, result)")  # fmt: skip
    con.close()
    assert off.finished_season(db) == 2025  # the 2026 Super Bowl has no result yet
    assert off.finished_season(tmp_path / "none.duckdb") is None
    st = off.build_status(pin_file(tmp_path, 2026), finished=2025, current_season=2026,
                          labels=off.labels_status(labels([("c", 2025, "y")]), None, 2025),
                          board="the 2026 live board: closed", today=date(2026, 10, 2),
                          started=True)  # fmt: skip
    text = off.status_text(st)
    for m in off.MODULES:
        assert f"\n{m:<17}   2026  2026" in text, m
    assert "coach departures of 2025: 1 rows, 1 verified" in text and "labelled" in text
    assert "the 2026 live board: closed" in text and "next: the 2026 season is under way" in text


def test_the_command_runs_on_the_project_and_changes_nothing() -> None:
    from typer.testing import CliRunner

    from twm.cli import app
    from twm.config import ROOT

    pin_path = ROOT / "config" / "production_models.yaml"
    before = pin_path.read_bytes()
    res = CliRunner().invoke(app, ["offseason", "status", "--now", "2026-10-02T12:00:00"])
    assert res.exit_code == 0, res.output
    assert "waiver_radar" in res.output and "board" in res.output and "next: " in res.output
    assert pin_path.read_bytes() == before
