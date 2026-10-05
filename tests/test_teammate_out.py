"""Feature #5, Teammate out (twm.modules.teammate_out, docs/teammate_out.md): the event rule,
the baselines, the allocation table and its shrinkage, the prediction math, the walk-forward,
the live filter, append-only snapshots, grading, the My League section and the pin check.
Synthetic data only (plus the committed pin and reports for the pin check)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import duckdb
import polars as pl
import pytest

from twm.modules.teammate_out import history as hs
from twm.modules.teammate_out import production as tp
from twm.modules.teammate_out import table as tb
from twm.modules.teammate_out import weekly as wk
from twm.scoring import ScoringRules


def make_warehouse(path: Path, players: list[tuple], roster: list[tuple]) -> Path:
    """A tiny warehouse: ``players`` rows (season, week, team, gsis_id, position, snaps,
    carries, targets, receptions); team volume = the sum of its players' rows; ``roster`` rows
    (season, week, team, gsis_id, status)."""
    stats = sorted({c for cols in ScoringRules.from_config().columns().values() for c in cols})
    con = duckdb.connect(str(path))
    p = pl.DataFrame(players, orient="row", schema=["season", "week", "team", "gsis_id",
                     "position", "snaps", "carries", "targets", "receptions"])  # fmt: skip
    con.register("p", p)
    extra = ", ".join(f"0.0 AS {c}" for c in stats if c not in ("receptions", "carries"))
    con.execute(f"""CREATE TABLE fact_player_week AS SELECT season, week, 'REG' AS season_type,
        team, gsis_id AS player_id, carries, targets, receptions, {extra} FROM p""")
    con.execute("""CREATE TABLE fact_snaps AS SELECT season, week, 'REG' AS game_type, team,
        gsis_id, position, snaps::DOUBLE AS offense_snaps, 0.5 AS offense_pct FROM p
        WHERE snaps > 0""")
    con.execute("""CREATE TABLE fact_team_week AS SELECT season, week, team, 'REG' AS season_type,
        team || season || week AS game_id, sum(carries)::INT AS carries,
        sum(targets)::INT AS targets FROM p GROUP BY ALL""")
    r = pl.DataFrame(roster, orient="row", schema=["season", "week", "team", "gsis_id", "status"])
    con.register("r", r)
    con.execute("""CREATE TABLE fact_roster_week AS SELECT season, week, 'REG' AS season_type,
        team, gsis_id, 'RB' AS position, status FROM r""")
    con.close()
    return path


def season_rows() -> tuple[list[tuple], list[tuple]]:
    """Team A, 2020 weeks 1-4: RB r1 (15 of 25 carries) sits week 4; RB r2 (5 carries, 20 in
    week 4); RB r3 (5 carries, never a starter); WR w1 (8 of 10 targets) sits week 4 traded."""
    rows, ros = [], []
    for w in range(1, 5):
        if w < 4:
            rows += [
                (2020, w, "A", "r1", "RB", 40, 15, 0, 0),
                (2020, w, "A", "w1", "WR", 50, 0, 8, 5),
            ]
        rows += [(2020, w, "A", "r2", "RB", 20, 20 if w == 4 else 5, 1, 1),
                 (2020, w, "A", "r3", "RB", 10, 5, 0, 0),
                 (2020, w, "A", "t1", "TE", 30, 0, 2, 1)]  # fmt: skip
        for g in ("r1", "r2", "r3", "t1"):
            ros.append((2020, w, "A", g, "ACT"))
        ros.append((2020, w, "A", "w1", "TRD" if w == 4 else "ACT"))
    return rows, ros


@pytest.fixture
def wh(tmp_path: Path) -> Path:
    rows, ros = season_rows()
    return make_warehouse(tmp_path / "wh.duckdb", rows, ros)


def history_frames(db: Path) -> tuple[pl.DataFrame, ...]:
    con = hs.connect(db)
    try:
        pg = con.execute(hs.players_sql([2020])).pl()
        cands = con.execute(hs.candidates_sql([2020])).pl()
    finally:
        con.close()
    return pg, cands


def test_event_rule_thresholds_and_gone_players(wh: Path) -> None:
    pg, cands = history_frames(wh)
    got = {(r["gsis_id"], r["week"]): r["with_team"] for r in cands.iter_rows(named=True)}
    assert got == {("r1", 4): True, ("w1", 4): False}  # r3 (20% of carries) is no starter
    assert cands.filter(pl.col("gsis_id") == "r1")["win_carry_share"][0] == pytest.approx(0.6)
    outs = hs.out_starters(cands)
    assert outs["out_id"].to_list() == ["r1"]  # the traded WR is gone, not out


def test_baseline_excludes_the_out_game(wh: Path) -> None:
    pg, cands = history_frames(wh)
    outs = hs.out_starters(cands)
    bg = hs.baseline_games(outs, pg)
    assert bg["bgi"].to_list() == [1, 2, 3]
    ev = hs.vacated(outs, bg, pg)
    assert ev["vac_carry_share"][0] == pytest.approx(0.6) and ev["base_team_carries"][0] == 25
    m = hs.teammates(ev, bg, pg)
    r2 = m.filter(pl.col("gsis_id") == "r2").row(0, named=True)
    assert r2["base_carry_share"] == pytest.approx(0.2) and r2["act_carry_share"] == 0.8
    assert r2["base_games"] == 3 and r2["role"] == "RB2"
    assert m.filter(pl.col("gsis_id") == "r3")["role"][0] == "RB3"
    assert m.filter(pl.col("gsis_id") == "t1")["role"][0] == "TE1"


def mates_frame(n_seasons: int = 4) -> pl.DataFrame:
    """Synthetic teammate rows: one RB-out event per season; RB2 takes half the carries."""
    rows = []
    for s in range(2013, 2013 + n_seasons):
        for gid, role, pos, bc, ac in (("a", "RB2", "RB", 0.2, 0.5), ("b", "WR1", "WR", 0.0, 0.0)):
            rows.append({"season": s, "team": "A", "gi": 5, "gsis_id": gid, "position": pos,
                         "role": role, "base_carry_share": bc, "base_target_share": 0.1,
                         "base_snap_share": 0.5, "base_points": 8.0, "base_carries": 10.0,
                         "base_targets": 2.0, "base_games": 4, "act_carry_share": ac,
                         "act_target_share": 0.1, "act_points": 12.0, "n_out": 1,
                         "out_id": ["x"], "out_pos": ["RB"], "out_carry_share": [0.6],
                         "out_target_share": [0.1], "vac_carry_share": 0.6,
                         "vac_target_share": 0.1, "base_team_carries": 25.0,
                         "base_team_targets": 30.0})  # fmt: skip
    return pl.DataFrame(rows)


def test_shrinkage_toward_the_group() -> None:
    one = mates_frame(1)
    rb3 = one.head(1).with_columns(pl.lit("c").alias("gsis_id"), pl.lit("RB3").alias("role"),
                                   pl.lit(0.2).alias("act_carry_share"))  # fmt: skip
    cells = tb.fit_cells(pl.concat([one, rb3]), pseudo_count=20)
    rb2 = cells.filter(pl.col("role") == "RB2").row(0, named=True)
    raw, group = (0.5 - 0.2) / 0.6, (0.5 - 0.2 + 0.0) / 1.2  # per player, ratio of sums
    assert rb2["n"] == 1 and rb2["carry_group"] == pytest.approx(group)
    assert rb2["carry"] == pytest.approx((1 * raw + 20 * group) / 21)
    wr_out = mates_frame(1).with_columns(pl.lit(["WR"]).alias("out_pos"))
    assert tb.fit_cells(wr_out)["carry"].to_list() == [0.0, 0.0]  # carries only when a RB sits


CELLS = pl.DataFrame(
    [
        {
            "out_pos": "RB",
            "role": "RB2",
            "position": "RB",
            "n": 10,
            "carry": 0.5,
            "target": 0.4,
            "n_group": 20,
            "carry_group": 0.3,
            "target_group": 0.2,
        }
    ]
)


def test_prediction_math() -> None:
    m = mates_frame(1)
    ppo = {"RB": 1.0, "WR": 2.0}
    p = {r["gsis_id"]: r for r in tb.predict(m, "role", CELLS, ppo).iter_rows(named=True)}
    a, b = p["a"], p["b"]
    assert a["pred_carry_share"] == pytest.approx(0.2 + 0.5 * 0.6)
    assert a["pred_target_share"] == pytest.approx(0.1 + 0.4 * 0.1)
    assert a["ppo"] == pytest.approx((8.0 * 4 + 20 * 1.0) / (12 * 4 + 20))
    assert a["pred_points"] == pytest.approx((0.5 * 25 + 0.14 * 30) * a["ppo"])
    assert a["pred_gain"] == pytest.approx((0.3 * 25 + 0.04 * 30) * a["ppo"])
    assert b["pred_carry_share"] == 0.0 and b["pred_gain"] == 0.0  # no cell, no group: 0
    nothing = tb.predict(m, "nothing", CELLS, ppo)
    assert nothing["pred_points"].to_list() == [8.0, 8.0] and nothing["pred_gain"].sum() == 0
    pro = tb.predict(m, "pro_rata", CELLS, ppo).filter(pl.col("gsis_id") == "a").row(0, named=True)
    assert pro["pred_carry_share"] == pytest.approx(0.8)  # the only RB takes it all
    assert pro["pred_target_share"] == pytest.approx(0.2)


def test_walk_forward_never_trains_on_the_test_season(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[int, int]] = []
    real = tb.fit

    def spy(train: pl.DataFrame, pseudo_count: float = tb.PSEUDO_COUNT) -> tb.Fitted:
        seen.append((int(train["season"].min()), int(train["season"].max())))
        return real(train, pseudo_count)

    monkeypatch.setattr(tb, "fit", spy)
    wf = tb.walk_forward(mates_frame(4), "role", [2015, 2016])
    assert seen == [(2013, 2014), (2013, 2015)]
    assert sorted(wf["season"].unique().to_list()) == [2015, 2016]
    bt = tb.backtest(mates_frame(4), [2015, 2016])
    assert set(bt["candidate"]) == set(tb.CANDIDATES) and "all" in bt["season"].to_list()


def spec() -> tp.Spec:
    rng = [{"position": p, "band": "all", "n": 50, "lo": -3.0, "hi": 4.0} for p in hs.POSITIONS]
    return tp.Spec({"chosen": "role", "cells": CELLS.to_dicts(), "season": 2020,
                    "ppo_mean": {"RB": 1.0, "WR": 2.0, "TE": 1.5}, "ranges": {"cells": rng},
                    "model_version": "alloc-test"})  # fmt: skip


KICK = datetime(2020, 10, 4, 17)


def live_frames(wh: Path, tag: str | None = "Out", r1_status: str = "ACT") -> dict:
    pg, _ = history_frames(wh)
    tags = pl.DataFrame(
        {
            "week": [4],
            "team": ["A"],
            "gsis_id": ["r1"],
            "report_status": [tag],
            "player": ["Runner One"],
        }
    ).filter(pl.col("report_status").is_not_null())
    ids = ["r1", "r2", "r3", "w1", "t1"]
    roster = pl.DataFrame({"team": ["A"] * 5, "gsis_id": ids, "full_name": [i.upper() for i in ids],
                           "status": [r1_status, "ACT", "ACT", "ACT", "ACT"]})  # fmt: skip
    games = pl.DataFrame({"game_id": ["g"], "kickoff_utc": [KICK], "team": ["A"],
                          "opponent": ["B"]})  # fmt: skip
    return {"pg": pg.filter(pl.col("week") < 4), "tags": tags, "roster": roster,
            "games": games, "source": "asof"}  # fmt: skip


def test_live_filter_kickoff_thresholds_and_statuses(wh: Path) -> None:
    starters = wk.live_starters(live_frames(wh)["pg"])
    assert sorted(starters["out_id"].to_list()) == ["r1", "w1"]  # r3 is below 45%
    before = datetime(2020, 10, 3, 12)
    rows = wk.predict_week(live_frames(wh), spec(), 2020, 4, before)
    assert set(rows["gsis_id"]) == {"r2", "r3", "w1", "t1"} and set(rows["out_ids"]) == {"r1"}
    r2 = rows.filter(pl.col("gsis_id") == "r2").row(0, named=True)
    assert r2["role"] == "RB2" and r2["out_reasons"] == "Out" and r2["out_players"] == "R1"
    assert r2["pred_carry_share"] == pytest.approx(0.2 + 0.5 * 0.6)
    assert r2["points_hi"] == pytest.approx(r2["pred_points"] + 4.0)
    assert set(rows.columns) == set(wk.LIST_COLUMNS)
    assert wk.predict_week(live_frames(wh), spec(), 2020, 4, KICK).is_empty()  # kicked off
    assert wk.predict_week(live_frames(wh, tag=None), spec(), 2020, 4, before).is_empty()
    ir = wk.predict_week(live_frames(wh, tag=None, r1_status="RES"), spec(), 2020, 4, before)
    assert set(ir["out_reasons"]) == {"roster RES"}
    assert wk.predict_week(live_frames(wh, r1_status="CUT"), spec(), 2020, 4, before).is_empty()


def stored(wh: Path, tmp_path: Path) -> tuple[Path, pl.DataFrame]:
    rows = wk.predict_week(live_frames(wh), spec(), 2020, 4, datetime(2020, 10, 3, 12))
    store = tmp_path / "pred.duckdb"
    for t in (datetime(2020, 10, 3, 12), datetime(2020, 10, 4, 12), datetime(2020, 10, 4, 18)):
        wk.store_snapshot(store, rows, season=2020, week=4, as_of=t, model_version="alloc-test")
    return store, rows


def test_snapshots_are_append_only(wh: Path, tmp_path: Path) -> None:
    store, rows = stored(wh, tmp_path)
    again = wk.store_snapshot(store, rows.head(1), season=2020, week=4,
                              as_of=datetime(2020, 10, 3, 12), model_version="other")  # fmt: skip
    assert again == {"rows": 0, "kept": rows.height}
    empty = wk.store_snapshot(store, rows.head(0), season=2020, week=4,
                              as_of=datetime(2020, 10, 3, 13), model_version="x")  # fmt: skip
    assert empty == {"rows": 0, "kept": 0}
    snaps = wk.read_snapshots(store, 2020)
    assert snaps.height == 3 * rows.height and set(snaps["model_version"]) == {"alloc-test"}
    assert wk.read_snapshots(tmp_path / "none.duckdb").is_empty()


def test_grading_uses_the_last_row_before_kickoff(wh: Path, tmp_path: Path) -> None:
    store, rows = stored(wh, tmp_path)
    acts = wk.read_actuals(wh, 2020)
    g = wk.grade(wk.read_snapshots(store, 2020), acts)
    assert g.height == rows.height and set(g["as_of"]) == {datetime(2020, 10, 4, 12)}
    out = dict(zip(g["gsis_id"], g["outcome"], strict=True))
    assert out == {"r2": "played", "r3": "played", "t1": "played", "w1": "did not play"}
    r2 = g.filter(pl.col("gsis_id") == "r2").row(0, named=True)
    assert r2["act_carry_share"] == 0.8 and r2["act_points"] is not None
    r1_played = acts.filter(pl.col("gsis_id") == "r2").with_columns(pl.lit("r1").alias("gsis_id"))
    sat = acts.vstack(r1_played)
    assert set(wk.grade(wk.read_snapshots(store, 2020), sat)["outcome"]) == {"starter played"}
    pending = wk.grade(wk.read_snapshots(store, 2020), acts.filter(pl.col("week") < 4))
    assert pending["outcome"].is_null().all()
    s = wk.summary(g)
    assert s["n"] == 3 and s["did_not_play"] == 1 and s["pending"] == 0
    assert 0 <= s["coverage"] <= 1
    assert s["mae_points_base"] >= 0 and s["team_weeks"] == 1
    assert wk.summary(pending)["n"] == 0 and wk.summary(pending)["pending"] == rows.height


def test_my_league_section_absent_without_espn(wh: Path, tmp_path: Path) -> None:
    from twm.league.report import ReportData
    from twm.modules.teammate_out import league as lg

    store, _ = stored(wh, tmp_path)
    con = duckdb.connect()
    con.execute("CREATE TABLE league_free_agents (league_id BIGINT, season INT, week INT, "
                "synced_at TIMESTAMP, entity_id VARCHAR, percent_owned DOUBLE)")  # fmt: skip
    con.execute("INSERT INTO league_free_agents VALUES (1, 2020, 4, '2020-10-02', 'r2', 3.0)")
    assert ReportData.__dataclass_fields__["teammate_out"].default is None
    assert lg.for_report(con, store, pl.DataFrame(), 2020) is None  # no ESPN roster
    mine = pl.DataFrame({"entity_id": ["t1"]})
    assert lg.for_report(con, tmp_path / "none.duckdb", mine, 2020) is None  # no list stored
    sec = lg.for_report(con, store, mine, 2020)
    assert sec is not None and sec.week == 4 and sec.as_of == datetime(2020, 10, 4, 18)
    assert [r["gsis_id"] for r in sec.free_agents] == ["r2"]
    assert all(r["pred_points"] > r["base_points"] for r in sec.mine + sec.free_agents)
    html = str(lg.render_section(sec))
    assert lg.TITLE in html and "3% rostered" in html


def test_the_committed_pin_checks_and_a_changed_file_is_refused(tmp_path: Path) -> None:
    from dataclasses import replace

    from typer.testing import CliRunner

    from twm import pins
    from twm.cli import app
    from twm.config import ROOT

    spec_, pin = tp.load_pinned(2026)
    body = {k: v for k, v in spec_.content.items() if k != "model_version"}
    assert tp.report_mismatches(body, ROOT / tp.REPORT_DIR) == []
    assert spec_.chosen == "role" and spec_.content["rule_choice"] == "nothing"
    assert spec_.content["chosen_by"].startswith("owner override")
    res = CliRunner().invoke(app, ["model", "check", "teammate_out"])
    assert res.exit_code == 0, res.output
    bad = tmp_path / pin.file
    bad.parent.mkdir(parents=True)
    bad.write_text(pin.path().read_text().replace('"chosen": "role"', '"chosen": "nothing"'))
    path = tmp_path / "pins.yaml"
    pins.write_pins({tp.PIN_KEY: replace(pin, approved=pin.approved)}, path)
    with pytest.raises(pins.PinError, match="not the approved file"):
        tp.load_pinned(2026, path=path, root=tmp_path)
    with pytest.raises(pins.PinError, match="scores season 2026"):
        tp.load_pinned(2027, path=path, root=tmp_path)
