"""Step I2c-a: the production Cliff board: the pin (required, sha256 before opening, the config's
anchor), the frozen backtest against the committed preseason reports, the live path (loads,
never fits), the append-only store, the publish family and `twm model check board`. Offline:
the committed pin and artifacts, synthetic feature rows."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import yaml

from twm import pins
from twm import predictions as pr
from twm.config import ROOT
from twm.modules.board import live as bl
from twm.modules.board import production as bp

SEASON = 2026  # the board's season (snapshot 2025)
AS_OF = datetime(2026, 9, 9, 23, 20, tzinfo=UTC)  # 2026's kickoff eve
KICKOFF = datetime(2026, 9, 10, 0, 20, tzinfo=UTC)
TEAMS = ("ARI", "ATL", "BAL", "BUF", "CAR", "CHI")
REPORTS = ROOT / "reports" / "board"
BOOLS = ("dc_absent", "dc_team_chart_missing")


@pytest.fixture(scope="module")
def pinned():
    return bp.load_pinned(SEASON)


def board_rows(n: int = 6) -> pl.DataFrame:
    """``n`` made-up Cliff rows of snapshot 2025 with every input of both models, the shown
    columns and the ECR columns."""
    rng = np.random.default_rng(7)
    feats = sorted({f for r in bp.ROLES for f in bp.role_spec(r)[1].features} | set(bp.SHOWN))
    data: dict[str, list] = {
        "season": [SEASON - 1] * n, "gsis_id": [f"00-00000{i:02d}" for i in range(n)],
        "position": ["RB", "WR", "TE", "QB", "RB", "WR"][:n], "team": list(TEAMS[:n]),
        "snapshot": [AS_OF.replace(tzinfo=None)] * n, "ecr_rank": [3, None, 12, 20, 1, 40][:n],
        "ecr_fill": [3, 61, 12, 20, 1, 40][:n],
    }  # fmt: skip
    for f in feats:
        if f in BOOLS:
            data[f] = [bool(i % 2) for i in range(n)]
        else:
            data[f] = rng.normal(0.0, 1.0, n).round(3).tolist()
    data["ppg_change"][0] = None  # a missing value: its indicator is an input
    return pl.DataFrame(data).with_columns(
        pl.col("season").cast(pl.Int32), pl.col("ecr_rank", "ecr_fill").cast(pl.Int64),
        pl.col("prior_seasons", "games_s", "pos_rank_s").abs().round(0).cast(pl.Int32) + 3,
    )  # fmt: skip


def _pin_file(tmp_path: Path, **change) -> Path:
    path = tmp_path / "production_models.yaml"
    raw = yaml.safe_load((ROOT / "config" / "production_models.yaml").read_text())
    raw["board"].update(change)
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    return path


def _run(models, kind: str, *, shift: float = 0.0) -> bl.BoardRun:
    table = bp.score_board(models, board_rows())
    table = table.with_columns(pl.col("cliff_prob") * (1.0 - shift))
    return bl.BoardRun(SEASON, AS_OF, KICKOFF, AS_OF, kind, dict(models), table)


def test_the_pin_is_required_and_checked(tmp_path: Path, monkeypatch, pinned) -> None:
    models, spec, pin = pinned
    assert pin.module == "board" and pin.model == "board_spec" and pin.season == SEASON
    assert spec["anchor"] == "week1_kickoff_eve" and spec["snapshot"] == "preseason"
    for role, (variant, model) in bp.ROLES.items():
        pm, rs = models[role], bp.role_spec(role)[1]
        assert spec["models"][role]["variant"] == variant and pm.model == model
        assert pm.model_version == spec["models"][role]["version"] and pm.calibrator is None
        assert tuple(pm.features) == rs.features and pm.label == rs.label
        assert pm.season == SEASON - 1 and pm.fold.train_seasons == tuple(range(2002, 2025))
    raw = yaml.safe_load((ROOT / "config" / "production_models.yaml").read_text())
    del raw["board"]
    missing = tmp_path / "none.yaml"
    missing.write_text(yaml.safe_dump(raw))
    with pytest.raises(pins.PinError, match="no approved model for board"):
        bp.load_pinned(SEASON, path=missing)
    # a changed file is never read or unpickled: the sha256 is checked first
    monkeypatch.setattr(bp.rprod, "load_model", lambda p: pytest.fail("opened a changed file"))
    with pytest.raises(pins.PinError, match="it was not opened"):
        bp.load_pinned(SEASON, path=_pin_file(tmp_path, sha256="0" * 64))
    monkeypatch.undo()
    with pytest.raises(pins.PinError, match="is for season 2026, not 2027"):
        bp.load_pinned(2027)
    with pytest.raises(pins.PinError, match="says model 'logit'"):
        bp.load_pinned(SEASON, path=_pin_file(tmp_path, model="logit"))
    with pytest.raises(pins.PinError, match="holds board_spec-"):
        bp.load_pinned(SEASON, path=_pin_file(tmp_path, model_version="board_spec-0000"))
    # the config switch: a board approved at the kickoff eve is refused at another anchor
    from twm.modules.board import preseason as pre

    monkeypatch.setattr(pre, "default_anchor", lambda: "tuesday_before_week_1")
    with pytest.raises(pins.PinError, match="approved at the preseason anchor week1_kickoff_eve"):
        bp.load_pinned(SEASON)


def test_the_committed_snapshot_reproduces_the_reports(pinned) -> None:
    """The frozen walk-forward (snapshots 2007-2024 = boards 2008-2025; sha256, rows) gives the
    preseason report's rows of both models (values, intervals), its per-season hits and its
    disagreement tables; one version per fold and model; every board ranked 1..n twice."""
    _, _, pin = pinned
    frames = bp.load_snapshot(pin)
    assert bp.snapshot_mismatches(frames, REPORTS, "preseason_") == []
    p, v = frames["predictions"], frames["model_versions"]
    assert p.height == frames["outcomes"].height == 1_674 and pin.backtest_seasons == "2007-2024"
    assert sorted(set(p["season"].to_list())) == list(range(2008, 2026))
    assert v.height == 36 and sorted(set(v["test_season"].to_list())) == list(range(2007, 2025))
    assert sorted(v["model"].unique().to_list()) == ["logit", "logit_simple"]
    assert all(len(json.loads(d)) == bp.N_DRIVERS for d in p["cliff_drivers_json"].to_list())
    for role in bp.ROLES:
        top = p.group_by("season").agg(
            pl.col(f"{role}_rank").min().alias("lo"),
            pl.col(f"{role}_rank").max().alias("hi"),
            pl.len(),
        )
        assert (top["lo"] == 1).all() and (top["hi"] == top["len"]).all()  # fmt: skip
    md = (REPORTS / "preseason_cliff.md").read_text()
    assert set(bp.md_disagreements(md, "cliff_main", "logit")) == {"both", "ecr_only", "model_only"}
    # a changed probability is caught (metrics, seasons and disagreements)
    bad = {**frames, "predictions": p.with_columns(pl.col("cliff_prob") ** 2)}
    assert any("cliff_main" in x for x in bp.snapshot_mismatches(bad, REPORTS, "preseason_"))
    # another anchor's report is another backtest
    assert bp.snapshot_mismatches(frames, REPORTS, "preseason_tuesday_")


def test_the_seasons_board_is_frozen_in_the_pin(pinned) -> None:
    """The 2026 board (its as-of had passed at approval) is in the pin: re-scoring its frozen
    inputs with the pinned models reproduces it; a changed input or chance is caught."""
    models_, _, pin = pinned
    assert {t: pin.backtest[t].rows for t in bp.CURRENT_TABLES} == {
        "current_board": 94, "current_inputs": 94}  # fmt: skip
    cur = bp.load_current(pin)
    assert cur is not None and bp.current_mismatches(models_, cur) == []
    b = cur["current_board"]
    assert b["season"].unique().to_list() == [SEASON] and b["snapshot_season"][0] == SEASON - 1
    assert b["as_of"].max() == AS_OF.replace(tzinfo=None)
    assert set(b["cliff_version"]) == {models_["cliff"].model_version}
    moved = {**cur, "current_inputs": cur["current_inputs"].with_columns(pl.col("age") + 3)}
    assert any("chances differ" in x for x in bp.current_mismatches(models_, moved))
    wrong = {**cur, "current_board": b.with_columns(pl.col("missed_prob") * 0.9)}
    assert any("missed" in x for x in bp.current_mismatches(models_, wrong))
    assert bp.same_board(b, b) and not bp.same_board(b, wrong["current_board"])


def test_the_live_path_loads_and_never_fits(monkeypatch) -> None:
    from sklearn.linear_model import LogisticRegression

    from twm.backtest import walkforward
    from twm.modules.hot_seat import models
    from twm.modules.waiver_radar.models import LogitEstimator

    def boom(*a, **k):
        raise AssertionError("the live path fitted a model")

    for target, name in ((walkforward, "fit_fold"), (walkforward, "walk_forward"),
                         (models.InnerCvLogit, "fit"), (LogitEstimator, "fit"),
                         (LogisticRegression, "fit"), (bp, "train_live"), (bp, "fit_live"),
                         (bp.bt, "run_model")):  # fmt: skip
        monkeypatch.setattr(target, name, boom)
    models_, _, _ = bp.load_pinned(SEASON)
    table = bp.score_board(models_, board_rows())
    assert table.height == 6 and table["season"].unique().to_list() == [SEASON]
    for role in bp.ROLES:
        assert table[f"{role}_rank"].sort().to_list() == list(range(1, 7))
        assert table[f"{role}_prob"].is_between(0.0, 1.0).all()
        assert table.sort(f"{role}_rank")[f"{role}_prob"].is_sorted(descending=True)
        assert table[f"{role}_version"].unique().to_list() == [models_[role].model_version]
        d = json.loads(table[f"{role}_drivers_json"][0])
        assert len(d) == bp.N_DRIVERS and {"feature", "label", "contribution"} <= set(d[0])
    assert table["ecr_rank"].null_count() == 1  # unranked: shown as such, never filled


def _stored(store: Path) -> pl.DataFrame:
    return pr.read_table(store, "predictions").filter(pl.col("module") == "board")


def test_live_boards_are_append_only(tmp_path: Path, pinned) -> None:
    models_, _, _ = pinned
    store = tmp_path / "predictions.duckdb"
    first = bl.store_board(_run(models_, "backtest"), store)
    assert first["predictions"] == 12 and first["kept"] == 0  # 6 players x 2 roles
    got = _stored(store)
    assert set(got["rank_group"]) == {"cliff", "missed"} and set(got["week"]) == {bp.WEEK}
    assert set(got["kind"]) == {"backtest"} and got["as_of"].max() == AS_OF.replace(tzinfo=None)
    live = bl.store_board(_run(models_, "live"), store)  # a live run replaces a reconstruction
    assert live["predictions"] == 12 and set(_stored(store)["kind"]) == {"live"}
    again = bl.store_board(_run(models_, "live", shift=0.3), store)
    assert again == {"predictions": 0, "model_versions": 0, "outcomes": 0, "kept": 12}
    late = bl.store_board(_run(models_, "backtest", shift=0.3), store)
    assert late["kept"] == 12 and set(_stored(store)["kind"]) == {"live"}
    probs = _stored(store).filter(pl.col("rank_group") == "cliff").sort("entity_id")["score"]
    want = _run(models_, "live").table.sort("gsis_id")["cliff_prob"]
    assert np.allclose(probs.to_numpy(), want.to_numpy())


def test_a_board_is_due_only_after_its_as_of(tmp_path: Path, pinned) -> None:
    import duckdb

    models_, _, _ = pinned
    db = tmp_path / "w.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE fact_game AS SELECT 2026 AS season, 'REG' AS game_type, 1 AS week, "
                "TIMESTAMP '2026-09-10 00:20:00' AS kickoff_utc")  # fmt: skip
    con.close()
    with pytest.raises(bl.NotDueError, match="due at 2026-09-09 23:20 UTC"):
        bl.run_board(db, SEASON, models=models_, anchor="week1_kickoff_eve",
                     now=datetime(2026, 9, 9, 22, tzinfo=UTC))  # fmt: skip
    with pytest.raises(ValueError, match="make the board of \\[2026\\], not 2027"):
        bl.run_board(db, 2027, models=models_, anchor="week1_kickoff_eve", now=AS_OF)


def test_the_publish_family(tmp_path: Path, pinned) -> None:
    """The pin's frozen boards (2008-2025 and the 2026 board frozen at approval) give one list
    per (season, week 0, 'preseason', kind) with every Cliff player and both chances, whatever
    the store holds: a store's reconstructed 2026 board is replaced by the pin's, a live one is
    kept beside it. The board's tables and (since its page, step I2c-b) its glossary terms
    publish with every module."""
    import duckdb

    from twm.publish import board_lists as pb
    from twm.publish import collect as col
    from twm.publish.tables import FAMILIES

    fam = FAMILIES["board"]
    assert fam.key == ("season", "week", "snapshot") and fam.row_id == "gsis_id"
    assert "board" in col.MODULES and "board" not in col.UNPUBLISHED_MODULES
    assert {"dc_absent", "age_curve_ratio"} <= set(col.glossary()["name"].to_list())  # I2c-b
    models_, _, _ = pinned
    con = duckdb.connect()
    con.execute("CREATE TABLE dim_player AS SELECT * FROM (VALUES ('00-0000001')) t(gsis_id)")
    now = datetime(2026, 10, 2, tzinfo=UTC)
    store = tmp_path / "predictions.duckdb"
    bl.store_board(_run(models_, "backtest"), store)  # a store's reconstruction of 2026
    got = pb.collect_board(store, REPORTS, SEASON, now, con)
    lists, out = got.data.lists, got.data.rows
    assert lists.height == 19 and out.height == 1_674 + 94 and set(lists["kind"]) == {"backtest"}
    mine = out.filter(pl.col("season") == SEASON).sort("cliff_rank")
    assert mine.height == 94 and mine["cliff_rank"].to_list() == list(range(1, 95))
    assert len(json.loads(mine["missed_drivers"][0])) == bp.N_DRIVERS
    live_v = {pm.model_version for pm in models_.values()}
    assert live_v <= set(got.versions["model_version"].to_list())  # a fresh runner has them
    outcomes = got.data.outcome_source
    assert outcomes.filter(pl.col("label_status") == "final").height == 1_674
    assert outcomes.filter(pl.col("season") == SEASON)["label_status"].unique().to_list() == [
        "pending"]  # fmt: skip
    teams, players = set(out["team"]), set(out["gsis_id"])
    versions = set(got.versions["model_version"].to_list())
    assert col._board_problems(got.data, teams, players, versions) == []
    bad = col.ListData(lists, out.with_columns(pl.col("missed_probability") + 1), outcomes,
                       got.data.outcome_keys)  # fmt: skip
    assert any("outside [0, 1]" in p for p in col._board_problems(bad, teams, players, versions))
    assert any("unknown missed-time" in p for p in col._board_problems(got.data, teams, players,
                                                                       set()))  # fmt: skip
    # a live board in the store is published beside the pin's reconstruction
    live_store = tmp_path / "live.duckdb"
    bl.store_board(_run(models_, "live"), live_store)
    got = pb.collect_board(live_store, REPORTS, SEASON, now, con)
    assert got.data.lists.height == 20 and got.data.lists.filter(pl.col("kind") == "live")[
        "n_players"].to_list() == [6]  # fmt: skip
    track = got.track_record
    assert track["line"].to_list() == list(range(1, track.height + 1))
    assert set(track.filter(pl.col("research"))["population"]) == {"breakout"}
    assert track.height == sum(pl.read_csv(REPORTS / f"preseason_{p}.csv").height
                               for p in ("cliff", "breakout"))  # fmt: skip
    dis = got.tables["board_disagreement"]
    assert dis.height == 36 and sorted(set(dis["season"].to_list())) == list(range(2020, 2026))


def test_the_model_check_command() -> None:
    from typer.testing import CliRunner

    from twm.cli import app

    res = CliRunner().invoke(app, ["model", "check", "board", "--season", str(SEASON)])
    assert res.exit_code == 0, res.output
    assert "matches reports/board/preseason_cliff.csv" in res.output


def test_the_jobs_preflight_loads_the_board_and_stops_on_a_changed_one(
    tmp_path: Path, monkeypatch
) -> None:
    """The job never scores the board (no stage), but its preflight checks the pin, as every
    module's, because each publish carries the board's frozen data."""
    from twm.pipeline import runner

    assert "board" not in runner.LATE_SCORES
    assert "board" in runner.default_hooks().check_model(SEASON)
    pin_file = _pin_file(tmp_path, sha256="0" * 64)
    monkeypatch.setattr(pins, "default_pin_path", lambda: pin_file)
    with pytest.raises(pins.PinError, match=r"\.json is not the approved board spec file"):
        runner.default_hooks().check_model(SEASON)
