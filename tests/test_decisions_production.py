"""Step P3: the Decision Report Card's approved grading (pin, spec, fold models), its frozen
history, the season in progress graded with the pins, and the rows the publish builds.

Offline: the committed pin and snapshot (artifacts/production_models/decisions/) are read
in place or copied to a temporary root before being tampered with; the real warehouse is only
read by the ``realdata`` test at the end.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from twm import pins
from twm.config import ROOT
from twm.modules.decisions import frozen as fz
from twm.modules.decisions import production as dp
from twm.modules.decisions import season as sn
from twm.modules.decisions import site
from twm.publish import decisions as dc

SEASON = 2026
REPORTS = {"fourth_downs": ROOT / "reports" / "decisions" / "fourth_downs.csv",
           "clock": ROOT / "reports" / "decisions" / "clock.csv"}  # fmt: skip


@pytest.fixture(scope="module")
def pinned() -> tuple[dp.GradingSpec, pins.Pin, dict[str, pl.DataFrame]]:
    spec, pin = dp.load_pinned_spec(SEASON)
    return spec, pin, fz.load_snapshot(pin)


def copy_pin(tmp_path: Path) -> Path:
    """The committed decisions pin and its files under ``tmp_path`` (same relative paths);
    returns the copied pin file."""
    pin = pins.get_pin(dp.PIN_KEY)
    shutil.copytree(dp.artifact_dir(), dp.artifact_dir(tmp_path))
    path = tmp_path / "pins.yaml"
    pins.write_pins({dp.PIN_KEY: pin}, path)
    assert pin.file.startswith("artifacts/")
    return path


# --------------------------------------------------------------------------------------
# The site's columns
# --------------------------------------------------------------------------------------


def test_coach_ids_are_slugs_and_never_clash() -> None:
    assert site.coach_slug("Andy Reid") == "andy-reid"
    assert site.coach_slug("Sean McVay") == "sean-mcvay"
    assert site.coach_slug("  Dan Quinn Jr. ") == "dan-quinn-jr"
    assert site.coach_slug("José Ñúñez") == "jose-nunez"
    ids = site.coach_ids(["Sean McVay", "Andy Reid", "Andy Reid", None])
    assert ids.rows() == [("andy-reid", "Andy Reid"), ("sean-mcvay", "Sean McVay")]
    with pytest.raises(site.SiteError, match="share an id"):
        site.coach_ids(["Andy Reid", "Andy  Reid"])
    with pytest.raises(site.SiteError):
        site.coach_slug("---")


def _graded_fourth() -> pl.DataFrame:
    return pl.DataFrame({
        "game_id": ["g1", "g1", "g2"], "play_id": [10, 5, 7], "season": [2025] * 3,
        "week": [1, 1, 2], "season_type": ["REG"] * 3, "posteam": ["KC", "KC", "BUF"],
        "defteam": ["BUF", "BUF", "KC"], "coach": ["Andy Reid", "Andy Reid", "Sean McDermott"],
        "qtr": [4, 2, 5], "game_seconds_remaining": [65, 2000, 400],
        "half_seconds_remaining": [65, 200, 400], "score_differential": [-3, 0, 7],
        "ydstogo": [2, 1, 8], "yardline_100": [40, 60, 30],
        "exclusion": [None, "penalty_no_play", None], "chosen": ["punt", "go", "field_goal"],
        "recommended": ["go", None, "field_goal"], "grade": ["clear", None, "toss_up"],
        "correct": [False, None, True], "wp_go": [0.412345678, None, 0.5],
        "wp_fg": [0.3, None, 0.51], "wp_punt": [0.39, None, 0.2],
        "fg_available": [False, None, True], "punt_available": [True, None, False],
        "wp_lost": [0.022345678, None, 0.0], "p_convert": [0.61, None, 0.33],
        "p_make": [0.2, None, 0.876543], "outcome": ["punted", None, "made"],
    })  # fmt: skip


def test_fourth_rows_keep_the_play_key_round_display_values_and_drop_missing_options() -> None:
    f = site.fourth_rows(_graded_fourth())
    assert f.columns == list(site.FOURTH_COLUMNS)
    assert f.select("game_id", "play_id").rows() == [("g1", 5), ("g1", 10), ("g2", 7)]
    one = f.row(1, named=True)
    assert one["quarter_seconds"] == 65 and one["wp_go"] == 0.4123
    assert one["wp_fg"] is None and one["p_make"] is None  # the field goal was out of range
    assert one["wp_lost"] == 0.022345678  # exact: the coach tables sum it
    ot = f.row(2, named=True)
    assert ot["quarter_seconds"] == 400 and ot["wp_punt"] is None and ot["p_make"] == 0.8765
    assert f.row(0, named=True)["exclusion"] == "penalty_no_play"  # excluded rows stay
    assert f.row(0, named=True)["quarter_seconds"] == 200  # Q2: 2000 - 2 * 900


def test_clock_rows_round_trip_to_the_report_frames() -> None:
    m1 = pl.DataFrame({
        "season": [2025], "week": [3], "season_type": ["REG"], "game_id": ["g1"],
        "team": ["KC"], "opp": ["BUF"], "coach": ["Andy Reid"], "opp_coach": ["x"],
        "team_margin": [-3], "timeouts_left": [2], "ro_play_id": [99],
        "ro_game_seconds_remaining": [110], "ro_down": [1], "ro_ydstogo": [10],
        "ro_yardline_100": [70], "ro_score_differential": [3], "ro_team_t": [3],
        "ro_desc": ["(1:50) run"], "ro_k_free": [120.0], "ro_k_all": [40.0],
        "drive_missed_stops": [1], "drive_seconds_wasted": [38.0], "last_play_id": [120],
        "last_clock": [0], "case": [True],
    })  # fmt: skip
    rows = site.clock_rows(m1, pl.DataFrame(), pl.DataFrame())
    assert rows.columns == list(site.CLOCK_COLUMNS) and rows.height == 1
    r = rows.row(0, named=True)
    assert (r["metric"], r["play_id"], r["qtr"], r["quarter_seconds"], r["timeouts"]) == (
        "timeouts_unused", 99, 4, 110, 3)  # fmt: skip
    assert r["is_case"] and r["amount"] == 2.0 and json.loads(r["detail"])["ro_k_all"] == 40.0
    a, b, c = site.clock_frames(rows)
    assert a.select("season", "coach", "case", "timeouts_left").rows() == [
        (2025, "Andy Reid", True, 2.0)]  # fmt: skip
    assert b.height == c.height == 0
    assert site.clock_rows(pl.DataFrame(), pl.DataFrame(), pl.DataFrame()).height == 0


# --------------------------------------------------------------------------------------
# The committed pin and its frozen history
# --------------------------------------------------------------------------------------


def test_the_committed_history_reproduces_the_reports(pinned) -> None:
    spec, pin, frames = pinned
    assert pin.model == dp.MODEL and pin.season == spec.season == SEASON
    assert pin.backtest_seasons == "2006-2025" and set(pin.backtest) == set(fz.TABLES)
    assert frames["season_inputs"]["season"].to_list() == list(range(2006, 2026))
    assert fz.snapshot_mismatches(frames, REPORTS) == []
    # the check compares every coach-season and league row of both reports
    got = fz.computed_rows(frames)
    assert len(got["fourth_downs"]) == len(fz.report_rows(REPORTS["fourth_downs"],
                                                          range(2006, 2026))) > 9000  # fmt: skip
    assert len(got["clock"]) > 7000


def test_a_changed_or_missing_row_breaks_the_check(pinned) -> None:
    _, _, frames = pinned
    f = frames["fourth_downs"]
    i = f.with_row_index().filter(pl.col("grade") == "clear").row(0, named=True)["index"]
    wrong = {**frames, "fourth_downs": f.with_columns(
        pl.when(pl.int_range(pl.len()) == i).then(pl.col("wp_lost") + 0.001)
        .otherwise(pl.col("wp_lost")).alias("wp_lost"))}  # fmt: skip
    bad = fz.snapshot_mismatches(wrong, REPORTS)
    assert bad and all("fourth_downs" in b for b in bad)
    gone = {**frames, "clock_cases": frames["clock_cases"].slice(1)}
    assert any(b.startswith("clock") for b in fz.snapshot_mismatches(gone, REPORTS))
    extra = {**frames, "team_games": frames["team_games"].with_columns(pl.lit(2026).alias(
        "season").cast(pl.Int32))}  # fmt: skip
    assert "without season inputs" in fz.snapshot_mismatches(extra, REPORTS)[0]


def test_the_committed_grading_loads_with_every_model_checked() -> None:
    spec, models, pin = dp.load_pinned(SEASON)
    assert spec.model_version == pin.model_version == dp.version_of(spec.content())
    assert models.versions() == spec.versions()
    for name in dp.MODEL_NAMES:
        m = getattr(models, name)
        assert m.test_season == SEASON and max(m.train_seasons) < SEASON
        assert spec.models[name]["file"].startswith("artifacts/production_models/decisions/")
    info = spec.info()
    assert info["margin"] == 0.015 and info["fg_max_distance"] == 68
    assert info["platt"]["kept"] is False and info["wp_version"] == models.wp.version
    assert set(spec.clock) == set(dp.CLOCK_INPUTS) and spec.cfg().clock.one_score_margin == 8
    with pytest.raises(pins.PinError, match="for season 2026, not 2027"):
        dp.load_pinned(2027)


def test_a_tampered_file_is_never_opened(tmp_path: Path, monkeypatch) -> None:
    path = copy_pin(tmp_path)
    spec, _, _ = dp.load_pinned(SEASON, path=path, root=tmp_path)  # the copy works
    # a changed model file: refused before unpickling
    model = pins._resolve(spec.models["conversion"]["file"], tmp_path)
    model.write_bytes(model.read_bytes() + b"x")
    import joblib

    real = joblib.load

    def load(file, *a, **k):
        assert Path(file) != model, "a tampered file was opened"
        return real(file, *a, **k)

    monkeypatch.setattr(joblib, "load", load)
    with pytest.raises(pins.PinError, match="conversion model file .*it was not opened"):
        dp.load_pinned(SEASON, path=path, root=tmp_path)
    monkeypatch.undo()
    # a changed history file: refused before it is read
    pin = pins.get_pin(dp.PIN_KEY, path)
    hist = pin.backtest["two_point"].path(tmp_path)
    hist.write_bytes(hist.read_bytes()[:-8])
    with pytest.raises(pins.PinError, match="two_point.parquet is not the approved"):
        fz.load_snapshot(pin, tmp_path)
    # a changed spec: refused; a spec whose version is not its content: refused
    sfile = pin.path(tmp_path)
    text = json.loads(sfile.read_text())
    text["inputs"]["fg_max_distance"] = 70
    sfile.write_text(json.dumps(text))
    with pytest.raises(pins.PinError, match="grading spec file .*not opened"):
        dp.load_pinned_spec(SEASON, path=path, root=tmp_path)
    from dataclasses import replace

    pins.write_pins({dp.PIN_KEY: replace(pin, sha256=pins.sha256_of(sfile))}, path)
    with pytest.raises(pins.PinError, match="its content is grading-"):
        dp.load_pinned_spec(SEASON, path=path, root=tmp_path)
    with pytest.raises(dp.DecisionsProductionError, match="not a grading spec"):
        dp.from_content({**text, "format": 99})


def test_a_pin_of_another_kind_or_without_history_is_refused(tmp_path: Path) -> None:
    from dataclasses import replace

    path = copy_pin(tmp_path)
    pin = pins.get_pin(dp.PIN_KEY, path)
    pins.write_pins({dp.PIN_KEY: replace(pin, model="params")}, path)
    with pytest.raises(pins.PinError, match="not 'grading'"):
        dp.load_pinned_spec(SEASON, path=path, root=tmp_path)
    with pytest.raises(pins.PinError, match="no frozen decision history"):
        fz.load_snapshot(replace(pin, backtest={}), tmp_path)
    with pytest.raises(pins.PinError, match="must end before 2025"):
        fz.load_snapshot(replace(pin, season=2025), tmp_path)


# --------------------------------------------------------------------------------------
# The published rows
# --------------------------------------------------------------------------------------


def decision_data(frames: dict[str, pl.DataFrame], history=(2023, 2024),
                  season: int = 2025) -> dc.DecisionData:  # fmt: skip
    """A small DecisionData from the committed snapshot: ``history`` seasons as the frozen part
    and ``season`` as the season in progress (the publish tests use it too)."""

    def part(seasons) -> dict[str, pl.DataFrame]:
        return {t: f.filter(pl.col("season").is_in(list(seasons))) for t, f in frames.items()}

    hist, cur = part(history), part([season])
    names = [*hist["team_games"]["coach"], *cur["team_games"]["coach"]]
    ids = site.coach_ids(names + hist["fourth_downs"]["coach"].to_list())
    tr = dc.csv_rows(ROOT / "reports" / "decisions" / "submodels.csv", "submodels").head(5)
    return dc.DecisionData(season=season, version="grading-test", history=dc.site_tables(
        hist, ids), current=dc.site_tables(cur, ids), coaches=ids,
        track_record=tr.select(dc.TABLES["decisions_track_record"].names))  # fmt: skip


def test_site_tables_match_the_report_and_check_cleanly(pinned) -> None:
    _, _, frames = pinned
    d = decision_data(frames)
    assert dc.problems(d) == []
    f = d.history["decision_fourth"]
    assert f.columns == list(dc.TABLES["decision_fourth"].names)
    assert f["grade"].is_in(["clear", "toss_up"]).all() and f["coach_id"].null_count() == 0
    cs = d.history["coach_season"].filter(pl.col("season") == 2024)
    want = fz.report_rows(REPORTS["fourth_downs"], [2024])
    for r in cs.join(d.coaches, on="coach_id").head(5).iter_rows(named=True):
        key = ("coach_season", "2024", r["name"], r["team"], "wp_lost_per_game")
        assert abs(want[key] - r["wp_lost_per_game"]) < 1e-6
    weeks = d.history["coach_week"]
    assert weeks["opp"].null_count() == 0 and (weeks["team"] != weeks["opp"]).all()
    assert d.current["decision_clock"]["detail"].map_elements(
        lambda x: isinstance(json.loads(x), dict), return_dtype=pl.Boolean).all()  # fmt: skip
    # what the checks catch
    late = {**d.history, "decision_fourth": f.with_columns(pl.lit(2025).alias("season"))}
    assert any("history holds season 2025" in p for p in dc.problems(
        dc.DecisionData(**{**d.__dict__, "history": late})))  # fmt: skip
    twice = {**d.current, "decision_two_point": pl.concat([d.current["decision_two_point"]] * 2)}
    assert any("repeats a game_id, play_id" in p for p in dc.problems(
        dc.DecisionData(**{**d.__dict__, "current": twice})))  # fmt: skip
    nobody = {**d.current, "coach_week": d.current["coach_week"].with_columns(
        pl.lit("nobody").alias("coach_id"))}  # fmt: skip
    assert any("no known coach" in p for p in dc.problems(
        dc.DecisionData(**{**d.__dict__, "current": nobody})))  # fmt: skip


def _nfl4th_csv(path: Path, status: str = "full") -> Path:
    pl.DataFrame({
        "season": [2024, 2024, 2025, 2025], "grade": ["clear", "toss_up", "clear", "clear"],
        "chosen": ["punt", "go", "go", "punt"], "recommended": ["go", "go", "go", "punt"],
        "nfl4th_recommended": ["go", "punt", "go", "field_goal"],
        "agree": ["true", "false", "true", "false"], "nfl4th_status": [status] * 4,
        "extra": [1, 2, 3, 4],
    }).write_csv(path)  # fmt: skip
    return path


def test_the_nfl4th_hook_publishes_only_a_full_benchmark(tmp_path: Path) -> None:
    rows, warn = dc.nfl4th_rows(tmp_path / "missing.csv")
    assert rows.height == 0 and "not found" in warn[0]
    rows, warn = dc.nfl4th_rows(_nfl4th_csv(tmp_path / "a.csv", status="fg_only"))
    assert rows.height == 0 and "not a full run" in warn[0]
    pl.DataFrame({"season": [2024]}).write_csv(tmp_path / "b.csv")
    assert "lacks" in dc.nfl4th_rows(tmp_path / "b.csv")[1][0]
    rows, warn = dc.nfl4th_rows(_nfl4th_csv(tmp_path / "c.csv"))
    assert warn == [] and rows["source"].unique().to_list() == [dc.NFL4TH_SOURCE]
    assert rows["line"].to_list() == list(range(1, rows.height + 1))
    agree = {(r["scope"], r["subset"]): (r["value"], r["n"]) for r in rows.filter(
        pl.col("section") == "agreement").iter_rows(named=True)}  # fmt: skip
    assert agree[("all", "all")] == (0.5, 4) and agree[("clear", "2025")] == (0.5, 2)
    assert agree[("toss_up", "2024")] == (0.0, 1) and agree[("toss_up", "2025")] == (None, 0)
    go = {(r["method"], r["subset"]): r["value"] for r in rows.filter(
        pl.col("section") == "go_rate").iter_rows(named=True)}  # fmt: skip
    assert go[("ours", "all")] == 0.75 and go[("nfl4th", "all")] == 0.5
    assert go[("real", "2025")] == 0.5


def test_the_track_record_keeps_every_report_row(tmp_path: Path) -> None:
    for name in ("wp_backtest", "submodels"):
        shutil.copy(ROOT / "reports" / "decisions" / f"{name}.csv", tmp_path)
    _nfl4th_csv(tmp_path / "nfl4th_benchmark.csv")
    tr, warn = dc.track_record(tmp_path)
    assert warn == [] and tr.columns == list(dc.TABLES["decisions_track_record"].names)
    for name in ("wp_backtest", "submodels"):
        csv = pl.read_csv(tmp_path / f"{name}.csv", infer_schema_length=0)
        part = tr.filter(pl.col("source") == name)
        assert part.height == csv.height and part["line"].max() == csv.height
        assert part["metric"].to_list() == csv["metric"].to_list()
    assert tr.select("source", "line").is_duplicated().sum() == 0
    with pytest.raises(dc.DecisionsInputError, match="report not found"):
        dc.csv_rows(tmp_path / "nope.csv", "wp_backtest")


# --------------------------------------------------------------------------------------
# The commands
# --------------------------------------------------------------------------------------


def test_model_check_decisions_passes_on_the_committed_pin() -> None:
    from twm.cli import app

    res = CliRunner().invoke(app, ["model", "check", "decisions", "--season", "2026"])
    assert res.exit_code == 0, res.output
    assert "frozen history 2006-2025: 83,462 fourth_downs" in res.output
    res = CliRunner().invoke(app, ["model", "check", "decisions", "--season", "2027"])
    assert res.exit_code == 1 and "not usable" in res.output


def test_grade_pinned_refuses_unusable_pins(tmp_path: Path, monkeypatch) -> None:
    from twm.cli import app

    db = tmp_path / "w.duckdb"
    db.write_bytes(b"")

    def refuse(season, **kw):
        raise pins.PinError("conversion.joblib is not the approved conversion model file")

    monkeypatch.setattr(dp, "load_pinned", refuse)
    called = []
    monkeypatch.setattr(sn, "grade_season", lambda *a, **k: called.append(a))
    res = CliRunner().invoke(app, ["decisions", "grade-pinned", "--db", str(db)])
    assert res.exit_code == 1 and "not usable: conversion.joblib" in res.output
    assert called == []


def test_differences_compare_every_stored_column(tmp_path: Path) -> None:
    for root in (tmp_path / "a", tmp_path / "b"):
        for sub, kind in (("graded", "fourth_downs"), ("graded", "two_point"),
                          ("clock", "defense_snaps"), ("clock", "half_passivity"),
                          ("clock", "team_games")):  # fmt: skip
            (root / sub).mkdir(parents=True, exist_ok=True)
            pl.DataFrame({"x": [1.0, float("nan")], "y": ["a", None]}).write_parquet(
                root / sub / f"{kind}_2026.parquet")  # fmt: skip
    assert sn.differences(2026, tmp_path / "a", tmp_path / "b") == []
    pl.DataFrame({"x": [1.0, 2.0], "y": ["a", None]}).write_parquet(
        tmp_path / "b" / "graded" / "two_point_2026.parquet")  # fmt: skip
    assert sn.differences(2026, tmp_path / "a", tmp_path / "b") == ["graded/two_point.x"]
    (tmp_path / "b" / "clock" / "team_games_2026.parquet").unlink()
    assert "clock/team_games: missing (True, False)" in sn.differences(
        2026, tmp_path / "a", tmp_path / "b")  # fmt: skip


# --------------------------------------------------------------------------------------
# Real data (opt-in: -m realdata; reads the warehouse and G3/G4's stored 2026 grades)
# --------------------------------------------------------------------------------------


@pytest.mark.realdata
def test_the_pinned_grading_reproduces_the_stored_grades_of_the_season(tmp_path: Path) -> None:
    """What the job does (`twm decisions grade-pinned`) equals G3's and G4's own grading of the
    season on this Mac, bit for bit, and the publish shapes it like the history."""
    from twm.config import settings

    db = settings().path("warehouse")
    stored = dp.stored_root()
    if not db.exists() or not (stored / "graded" / f"season_{SEASON}.json").exists():
        pytest.skip("needs the warehouse and `twm decisions grade` / `clock` of 2026")
    spec, models, _ = dp.load_pinned(SEASON)
    sn.grade_season(db, SEASON, spec, models, out_dir=tmp_path, progress=lambda _: None)
    assert sn.differences(SEASON, stored, tmp_path) == []
    cur = dc.current_frames(SEASON, spec, tmp_path)
    assert cur["fourth_downs"].height > 600 and cur["season_inputs"].height == 1


# --------------------------------------------------------------------------------------
# G3b: the late game is not graded
# --------------------------------------------------------------------------------------


def test_the_committed_grading_and_history_never_grade_the_late_game(pinned) -> None:
    """The approved grading carries the window; the frozen history counts the 4th quarter's
    last 2:00 and overtime as late_game, and nothing in it is graded or published."""
    spec, _, frames = pinned
    info = spec.info()
    assert (info["late_game_q4_seconds"], info["late_game_overtime"]) == (120, True)
    s = frames["season_inputs"]
    assert s["late_game_q4_seconds"].unique().to_list() == [120]
    assert s["late_game_overtime"].all() and s["grade_format"].unique().to_list() == [2]
    window = ((pl.col("qtr") == 4) & (pl.col("quarter_seconds") <= 120)) | (pl.col("qtr") >= 5)
    for t in ("fourth_downs", "two_point"):
        f = frames[t]
        assert f.filter(window & pl.col("exclusion").is_null()).height == 0
        late = f.filter(pl.col("exclusion") == "late_game")
        assert late.height > 1000 and late["grade"].null_count() == late.height
        assert late.filter(~window).height == 0
    d = decision_data(frames)
    for t in ("decision_fourth", "decision_two_point"):
        pub = d.history[t]
        assert pub.filter(window).height == 0 and pub.height > 0


def test_season_grades_of_another_late_game_window_are_not_published(pinned, tmp_path) -> None:
    spec, _, _ = pinned
    (tmp_path / "graded").mkdir()
    old = {k: v for k, v in spec.info().items() if not k.startswith("late_game")}
    (tmp_path / "graded" / f"season_{SEASON}.json").write_text(json.dumps(old))
    with pytest.raises(dc.DecisionsInputError, match="late_game_q4_seconds"):
        dc.current_frames(SEASON, spec, tmp_path)


def test_stored_grades_of_another_late_game_window_cannot_be_approved(tmp_path) -> None:
    from twm.config import settings

    cfg = settings().decisions
    for sub in ("graded", "clock"):
        (tmp_path / sub).mkdir()
    g = {"season": SEASON, "margin": cfg.toss_up_margin, "wp_version": "wp-x",
         "end_of_half_seconds": cfg.end_of_half_seconds, "late_game_q4_seconds": 60,
         "late_game_overtime": True}  # fmt: skip
    (tmp_path / "graded" / f"season_{SEASON}.json").write_text(json.dumps(g))
    c = {**cfg.clock.model_dump(), "wp_version": "wp-x"}
    (tmp_path / "clock" / f"season_{SEASON}.json").write_text(json.dumps(c))
    with pytest.raises(dp.DecisionsProductionError, match="late_game_q4_seconds"):
        dp.build_spec(SEASON, stored=tmp_path)
