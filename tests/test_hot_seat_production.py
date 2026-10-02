"""Step H4a: the production Hot-Seat Meter: the pin (required, sha256 before opening), the
frozen backtest against the committed reports, the live path (loads, never fits), the drivers'
math, the append-only live store, the end-of-season due rule, the publish family and the job's
preflight. Offline: the committed pin and artifacts, synthetic feature rows."""

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
from twm.modules.hot_seat import production as hp
from twm.modules.hot_seat import weekly as hw
from twm.modules.hot_seat.models import FEATURES, KEYS

SEASON = 2026
AS_OF = datetime(2026, 9, 29, 14, tzinfo=UTC)
TEAMS = ("ARI", "ATL", "BAL", "BUF", "CAR", "CHI")


@pytest.fixture(scope="module")
def pinned():
    return hp.load_pinned(SEASON)


def feature_rows(n: int = 6, *, snapshot: str = "weekly", week: int = 3) -> pl.DataFrame:
    """``n`` made-up coach rows of SEASON with every model input and shown column."""
    rng = np.random.default_rng(11)
    data: dict[str, list] = {
        "season": [SEASON] * n, "snapshot": [snapshot] * n, "week": [week] * n,
        "team": list(TEAMS[:n]), "coach_id": [f"coach_{i}" for i in range(n)],
        "as_of": [AS_OF] * n, "is_interim": [False] * (n - 1) + [True],
        "reg_games_played": [3] * n, "reg_wins": [1.0] * n, "expected_wins": [1.4] * n,
    }  # fmt: skip
    for f in FEATURES:
        data[f] = rng.normal(0.0, 1.0, n).round(3).tolist()
    data["fourth_down_wp_lost_per_game"][0] = None  # a missing value: its indicator is an input
    df = pl.DataFrame(data).with_columns(
        pl.col("season", "week").cast(pl.Int32),
        pl.col("as_of").cast(pl.Datetime("us", "UTC")),
        pl.col("tenure_seasons", "division_rank", "consecutive_losing_seasons")
        .abs().round(0).cast(pl.Int32),
    )  # fmt: skip
    return df


def names(n: int = 6) -> dict[str, str]:
    return {f"coach_{i}": f"Coach {chr(65 + i)}" for i in range(n)}


def _pin_file(tmp_path: Path, **change) -> Path:
    path = tmp_path / "production_models.yaml"
    raw = yaml.safe_load((ROOT / "config" / "production_models.yaml").read_text())
    raw["hot_seat"].update(change)
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    return path


def test_the_pin_is_required_and_checked(tmp_path: Path, monkeypatch, pinned) -> None:
    pm, pin = pinned
    assert pin.module == "hot_seat" and pin.model == "logit" and pin.season == SEASON
    assert pm.model_version == pin.model_version and tuple(pm.features) == FEATURES
    assert pm.calibrator is None and pm.label == "y" and pm.fold.train_seasons[0] == 2002
    raw = yaml.safe_load((ROOT / "config" / "production_models.yaml").read_text())
    del raw["hot_seat"]
    missing = tmp_path / "none.yaml"
    missing.write_text(yaml.safe_dump(raw))
    with pytest.raises(pins.PinError, match="no approved model for hot_seat"):
        hp.load_pinned(SEASON, path=missing)
    # a changed file is never unpickled: the sha256 is checked first
    monkeypatch.setattr(hp.rprod, "load_model", lambda p: pytest.fail("opened a changed file"))
    with pytest.raises(pins.PinError, match="it was not opened"):
        hp.load_pinned(SEASON, path=_pin_file(tmp_path, sha256="0" * 64))
    monkeypatch.undo()
    with pytest.raises(pins.PinError, match="scores season 2026, not 2027"):
        hp.load_pinned(2027)
    with pytest.raises(pins.PinError, match="says model 'rule'"):
        hp.load_pinned(SEASON, path=_pin_file(tmp_path, model="rule"))
    with pytest.raises(pins.PinError, match="holds logit-"):
        hp.load_pinned(SEASON, path=_pin_file(tmp_path, model_version="logit-0000000000000000"))


def test_the_committed_snapshot_reproduces_the_reports(pinned) -> None:
    """The frozen 2006-2025 backtest (sha256, rows) gives the report's main/logit metrics and
    the firings per season; one version per fold; every row named, ranked, with 3 drivers."""
    _, pin = pinned
    frames = hp.load_snapshot(pin)
    reports = ROOT / "reports" / "hot_seat"
    assert hp.snapshot_mismatches(frames, reports / "backtest_metrics.csv",
                                  reports / "firings_per_season.csv") == []  # fmt: skip
    p, v = frames["predictions"], frames["model_versions"]
    assert p.height == frames["outcomes"].height == 10_361 and pin.backtest_seasons == "2006-2025"
    assert v["test_season"].to_list() == list(range(2006, 2026))
    assert v["model"].unique().to_list() == ["logit"]
    assert p["coach_name"].null_count() == 0
    assert all(len(json.loads(d)) == hp.N_DRIVERS for d in p["drivers_json"].to_list()[:500])
    top = p.group_by("season", "snapshot", "week").agg(
        pl.col("rank").min(), pl.len(), pl.col("rank").max().alias("hi")
    )
    assert (top["rank"] == 1).all() and (top["hi"] == top["len"]).all()
    # a changed probability in the reports' sense is caught
    bad = {**frames, "predictions": p.with_columns(pl.col("prob") * 0.5)}
    assert hp.snapshot_mismatches(bad, reports / "backtest_metrics.csv",
                                  reports / "firings_per_season.csv")  # fmt: skip


def test_the_live_path_loads_and_never_fits(monkeypatch, pinned) -> None:
    from sklearn.linear_model import LogisticRegression

    from twm.backtest import walkforward
    from twm.modules.hot_seat import models
    from twm.modules.waiver_radar.models import LogitEstimator

    def boom(*a, **k):
        raise AssertionError("the live path fitted a model")

    for target, name in ((walkforward, "fit_fold"), (models.InnerCvLogit, "fit"),
                         (LogitEstimator, "fit"), (LogisticRegression, "fit"),
                         (hp, "train_live"), (hp, "fit_live")):  # fmt: skip
        monkeypatch.setattr(target, name, boom)
    pm, _ = hp.load_pinned(SEASON)
    table = hw.score_rows(pm, feature_rows(), names())
    assert table.height == 6 and table["rank"].sort().to_list() == list(range(1, 7))
    assert table["prob"].is_between(0.0, 1.0).all()
    assert table.sort("rank")["prob"].is_sorted(descending=True)
    assert table["coach_name"].to_list() == [names()[c] for c in table["coach_id"].to_list()]
    assert table["model_version"].unique().to_list() == [pm.model_version]


def test_drivers_sum_to_the_logit_minus_the_intercept(pinned) -> None:
    pm, _ = pinned
    x = feature_rows().sort(list(KEYS)).select(list(FEATURES))
    intercept, inputs, terms = hp.contribution_terms(pm.fitted, x)
    p = pm.raw(x)
    assert np.allclose(terms.sum(axis=1), np.log(p / (1 - p)) - intercept, atol=1e-9)
    assert "missingindicator_fourth_down_wp_lost_per_game" in inputs
    ds = hp.drivers(pm.fitted, x)
    for i, row in enumerate(ds):
        assert len(row) == 3
        sizes = [abs(d["contribution"]) for d in row]
        assert sizes == sorted(sizes, reverse=True)
        assert min(sizes) >= np.sort(np.abs(terms[i]))[-3] - 1e-6  # the 3 largest, signed
        assert all(d["label"] and d["feature"] in FEATURES for d in row)


def _run(pm, kind: str, *, shift: float = 0.0) -> hw.WeeklyRun:
    table = hw.score_rows(pm, feature_rows(), names()).with_columns(pl.col("prob") * (1 - shift))
    fresh = hw.rw.Freshness(SEASON, 3, AS_OF, AS_OF)
    return hw.WeeklyRun(SEASON, 3, "weekly", AS_OF, AS_OF, kind, fresh, pm, table, None, 15)


def _stored(store: Path) -> pl.DataFrame:
    return pr.read_table(store, "predictions").sort("entity_id").drop("created_at")


def test_live_lists_are_append_only(tmp_path: Path, pinned) -> None:
    """A re-run of a stored live list (live or reconstructed) keeps the stored one; a live run
    replaces a reconstructed one."""
    pm, _ = pinned
    store = tmp_path / "predictions.duckdb"
    assert hw.store_week(_run(pm, "backtest"), store)["predictions"] == 6
    got = hw.store_week(_run(pm, "live", shift=0.1), store)
    assert got["predictions"] == 6 and got["kept"] == 0  # live replaces the reconstruction
    first = _stored(store)
    assert first["kind"].unique().to_list() == ["live"]
    assert first["rank_group"].unique().to_list() == ["weekly"]
    got = hw.store_week(_run(pm, "live", shift=0.3), store)
    assert got == {"predictions": 0, "model_versions": 0, "outcomes": 0, "kept": 6}
    got = hw.store_week(_run(pm, "backtest", shift=0.3), store)  # e.g. a later night's re-run
    assert got["predictions"] == 0 and got["kept"] == 6
    assert _stored(store).equals(first)
    # the store's own guard stays: a reconstructed list never overwrites a live week
    with pytest.raises(pr.LiveWeekError):
        pr.write_predictions(store, predictions=hw.list_rows(_run(pm, "backtest"), AS_OF),
                             versions=hp.rprod.version_frame(pm), replace="weeks")  # fmt: skip
    rec = json.loads(first.filter(pl.col("entity_id") == "coach_5")["reasons_json"][0])
    assert rec["interim"] is True and rec["note"] == hw.INTERIM_NOTE and rec["coach"] == "Coach F"
    assert len(rec["drivers"]) == 3 and set(rec["features"]) == set(hp.SHOWN)


def test_the_end_of_season_snapshot_is_due_once_every_team_has_finished(monkeypatch) -> None:
    monkeypatch.setattr(hw.rw, "last_reg_week", lambda db, season: 18)
    assert hw.list_snapshot("x", SEASON, 18) == "end_of_season"
    assert hw.list_snapshot("x", SEASON, 2) == hw.list_snapshot("x", SEASON, 17) == "weekly"
    for week in (1, 19):
        with pytest.raises(ValueError, match="weeks 2-18"):
            hw.list_snapshot("x", SEASON, week)
    weekly = feature_rows(4).with_columns(pl.lit(17, dtype=pl.Int32).alias("week"))
    eos = feature_rows(4, snapshot="end_of_season", week=18)
    early = pl.concat([weekly, eos.head(3)])  # one team's last game is not public yet
    with pytest.raises(hw.NotDueError, match="3 of 4 teams"):
        hw.select_rows(early, SEASON, 18, "end_of_season")
    rows = hw.select_rows(pl.concat([weekly, eos]), SEASON, 18, "end_of_season")
    assert rows.height == 4 and rows["snapshot"].unique().to_list() == ["end_of_season"]
    assert hw.select_rows(pl.concat([weekly, eos]), SEASON, 17, "weekly").height == 4


def test_the_jobs_preflight_stops_on_a_changed_hot_seat_model(tmp_path: Path, monkeypatch) -> None:
    from twm.pipeline import runner

    pin_file = _pin_file(tmp_path, sha256="0" * 64)
    monkeypatch.setattr(pins, "default_pin_path", lambda: pin_file)
    with pytest.raises(pins.PinError, match=r"\.joblib is not the approved file"):
        runner.default_hooks().check_model(SEASON)


def test_the_publish_family(tmp_path: Path, pinned) -> None:
    """The store's live rows and the frozen backtest give the same lists and rows: one list per
    (season, week, snapshot, kind), every coach ranked, the site's coach slug, the drivers."""
    from twm.publish import collect as col
    from twm.publish import hot_seat_lists as hs
    from twm.publish.tables import FAMILIES

    fam = FAMILIES["hot_seat"]
    assert fam.key == ("season", "week", "snapshot") and fam.row_id == "coach_id"
    # published with every module; its glossary terms publish since the site step (H4b)
    assert "hot_seat" in col.MODULES and "hot_seat" not in col.UNPUBLISHED_MODULES
    published = set(col.glossary()["name"].to_list())
    assert {"wins_vs_expected", "tenure_seasons", "is_interim"} <= published
    pm, _ = pinned
    store = tmp_path / "predictions.duckdb"
    hw.store_week(_run(pm, "live"), store)
    created = datetime(2026, 10, 2)  # naive UTC, as collect passes it
    back, snap = hs.frozen_lists(SEASON, created)
    live = hs.store_rows(store, SEASON)
    rows = col.choose_lists(pl.concat([live, back.select(live.columns)], how="vertical_relaxed"),
                            hs.LIST_KEY)  # fmt: skip
    lists, out, coaches = hs.hot_seat_lists(rows)
    want = snap["predictions"].select("season", "week", "snapshot").unique().height + 1
    assert lists.height == want and out.height == snap["predictions"].height + 6
    assert set(lists.filter(pl.col("snapshot") == "end_of_season")["note"]) == {hs.EOS_NOTE}
    one = out.filter((pl.col("kind") == "live") & (pl.col("rank") == 1)).row(0, named=True)
    assert one["coach_id"].startswith("coach-") and one["team"] in TEAMS
    assert len(json.loads(one["drivers"])) == 3
    assert coaches.filter(pl.col("coach_id") == "andy-reid").height == 1  # the decisions' slug
    outcomes = hs.frozen_outcomes(snap, coaches)
    assert outcomes.height == 10_361 and outcomes["coach_id"].null_count() == 0
    assert outcomes["label_status"].unique().to_list() == ["final"]
    data = col.ListData(lists, out, outcomes, out.select("season", "week", "coach_id"))
    teams, ids = set(out["team"]), set(coaches["coach_id"])
    assert col._hot_seat_problems(data, teams, ids) == []
    bad = col.ListData(lists, out.with_columns(pl.col("probability") + 1), outcomes,
                       data.outcome_keys)  # fmt: skip
    assert any("outside [0, 1]" in p for p in col._hot_seat_problems(bad, teams, ids))
    assert any("missing from dim_coach" in p for p in col._hot_seat_problems(data, teams, set()))


def test_the_model_check_command() -> None:
    from typer.testing import CliRunner

    from twm.cli import app

    res = CliRunner().invoke(app, ["model", "check", "hot_seat", "--season", str(SEASON)])
    assert res.exit_code == 0, res.output
    assert "frozen backtest 2006-2025: 10,361 predictions" in res.output and "matches" in res.output
