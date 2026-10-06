"""Feature #8, L8b: `twm radar lead-time` (the study's aggregate tables in the terminal) and the
local weekly report's "Lead time this season" section (twm.modules.lead_time.league), on
synthetic data: a made-up study (tests/publish_lead_time_synthetic.py), a temporary league
store and warehouse. No real league, player or roster number."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import polars as pl
import pytest
from typer.testing import CliRunner

from tests import publish_lead_time_synthetic as pls
from twm.cli import app
from twm.modules.lead_time import league as lg
from twm.modules.lead_time import study as st

S = 2026
ASOF1 = datetime(2026, 9, 15, 14)  # week 1's official as-of (Tuesday 14:00 UTC)


@pytest.fixture
def study(monkeypatch: pytest.MonkeyPatch) -> st.Study:
    s = st.build(pls.owned(), pls.windows(), pls.radar_rows(), seasons=pls.SEASONS)
    monkeypatch.setattr(st, "run", lambda warehouse=None: s)
    return s


def test_cli_prints_the_aggregate_tables_only(study: st.Study, tmp_path: Path) -> None:
    wh = tmp_path / "wh.duckdb"
    wh.touch()
    res = CliRunner().invoke(app, ["radar", "lead-time", "--db", str(wh)])
    assert res.exit_code == 0, res.output
    out = res.output
    for head in (
        "Coverage",
        "Crowd at 50%, complete seasons 2021-2022 pooled (60 crowd adds)",
        "Crowd at 25%",
        "Head to head",
        "Reverse view",
        "Volume and conversion",
    ):
        assert head in out, head
    full = pls.frames()["summary"].filter((pl.col("scope") == "complete")
                                          & (pl.col("threshold") == 50.0))  # fmt: skip
    listed = full.filter(pl.col("signal") == "listed").row(0, named=True)
    assert f"{100 * listed['share_before']:.1f}%" in out
    assert not set(study.leads["gsis_id"]) & set(out.replace(",", " ").split())  # no player
    bad = CliRunner().invoke(app, ["radar", "lead-time", "--db", str(wh), "--threshold", "40"])
    assert bad.exit_code == 2
    gone = CliRunner().invoke(app, ["radar", "lead-time", "--db", str(tmp_path / "none.duckdb")])
    assert gone.exit_code == 1 and "warehouse not found" in gone.output


def _stores(tmp_path: Path, syncs: list[tuple[str, datetime, float]]) -> tuple[Path, Path]:
    """A league store with free-agent syncs and a warehouse with 2026's weeks and names."""
    league, wh = tmp_path / "league.duckdb", tmp_path / "wh.duckdb"
    with duckdb.connect(str(league)) as con:
        con.execute("CREATE TABLE league_free_agents (season INTEGER, gsis_id VARCHAR, "
                    "synced_at TIMESTAMP, percent_owned DOUBLE)")  # fmt: skip
        con.executemany("INSERT INTO league_free_agents VALUES (?, ?, ?, ?)",
                        [(S, g, t, p) for g, t, p in syncs])  # fmt: skip
    with duckdb.connect(str(wh)) as con:
        con.execute("CREATE TABLE dim_week (season INTEGER, week INTEGER, season_type VARCHAR, "
                    "window_end_utc TIMESTAMP)")  # fmt: skip
        con.executemany(
            "INSERT INTO dim_week VALUES (?, ?, 'REG', ?)",
            [(S, w, ASOF1 + timedelta(days=7 * (w - 1))) for w in range(1, 19)],
        )
        con.execute("CREATE TABLE dim_player (gsis_id VARCHAR, display_name VARCHAR, "
                    "position VARCHAR)")  # fmt: skip
        con.executemany(
            "INSERT INTO dim_player VALUES (?, ?, ?)",
            [("G1", "Riser Example", "WR"), ("G2", "Steady Sample", "RB")],
        )
    return league, wh


def _store(tmp_path: Path, rows: list[tuple[int, str, int, str]]) -> Path:
    """A predictions store with Radar list rows (week, gsis_id, rank, tier)."""
    path = tmp_path / "pred.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE predictions (module VARCHAR, season INTEGER, week INTEGER, "
                    "rank_group VARCHAR, entity_id VARCHAR, rank INTEGER, tier VARCHAR, "
                    "kind VARCHAR, created_at TIMESTAMP, model_version VARCHAR)")  # fmt: skip
        con.executemany(
            "INSERT INTO predictions VALUES ('waiver_radar', ?, ?, 'WR', ?, ?, ?, 'live', ?, 'v1')",
            [(S, w, g, r, t, ASOF1) for w, g, r, t in rows],
        )
    return path


def test_the_local_section_measures_leads_once_a_crossing_is_seen(tmp_path: Path) -> None:
    p1, p2 = ASOF1 + timedelta(days=2), ASOF1 + timedelta(days=9)  # waiver periods 1 and 2
    league, wh = _stores(tmp_path, [("G1", p1, 20.0), ("G1", p2, 60.0), ("G2", p1, 30.0),
                                    ("G2", p2, 32.0), ("G3", p1, 10.0)])  # fmt: skip
    pred = _store(tmp_path, [(1, "G1", 1, "must-add"), (1, "G2", 2, "must-add")])
    with duckdb.connect(str(league), read_only=True) as con:
        sec = lg.for_report(con, wh, pred, S)
    assert sec is not None and sec.periods == [1, 2]
    assert [(r["name"], r["cross_period"], r["lead_must_add"]) for r in sec.added] == [
        ("Riser Example", 2, 1)]  # fmt: skip
    assert [r["name"] for r in sec.watch] == ["Steady Sample"] and sec.n_watch == 1
    html = str(lg.render_section(sec))
    assert 'data-section="lead-time"' in html and "Riser Example" in html and "+1" in html
    assert "none can be measured yet" not in html
    assert sec.counts["added"]["before_must_add"] == 1
    # G3 left the owner's free agents while under 50% (maybe rostered there): not an add
    assert sec.counts["left_pool"]["n"] == 1 and "1 player was under 50%" in html


def test_one_period_says_no_lead_yet_and_no_sync_means_no_section(tmp_path: Path) -> None:
    league, wh = _stores(tmp_path, [("G1", ASOF1 + timedelta(days=2), 20.0)])
    with duckdb.connect(str(league), read_only=True) as con:
        sec = lg.for_report(con, wh, tmp_path / "no-store.duckdb", S)
        assert lg.for_report(con, wh, tmp_path / "no-store.duckdb", S - 1) is None
    assert sec is not None and sec.added == [] and sec.watch == []
    assert "none can be measured yet" in str(lg.render_section(sec))
