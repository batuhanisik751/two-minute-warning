"""The player pages' xFP on the own walk-forward xFP (step PXFP, owner's decision of 2026-10-02).

- the history (2013 .. the season before the pin's) is frozen in Regression Watch's pin
  (``player_xfp``): required, sha256 checked before it is read, never recomputed by a publish;
- the season in progress is scored with the pinned live models through the weekly list's own
  function (:func:`twm.modules.regression_watch.own_xfp.live_frame`), never fit;
- player_week_summary's xfp / fpoe / points_ng / xfp_ng / fpoe_ng come from them.
"""

from __future__ import annotations

import inspect
import shutil
from dataclasses import replace
from datetime import UTC, datetime

import polars as pl
import pytest

from tests.test_regression_weekly import SEASON, toy_csv, toy_params
from tests.test_regression_xfp_switch import FITS, live, world  # noqa: F401 (fixtures)
from twm import pins
from twm.config import ROOT
from twm.modules.regression_watch import frozen as fz
from twm.modules.regression_watch import own_xfp as ox
from twm.modules.regression_watch import production as rp
from twm.publish import collect as col
from twm.publish import regression_lists as rl
from twm.publish.collect import PublishInputError
from twm.publish.tables import TABLES

KEYS = ["gsis_id", "season", "week"]


# --------------------------------------------------------------------------------------
# The committed pin: the frozen history is there, small, and checked
# --------------------------------------------------------------------------------------


def test_the_committed_pin_freezes_the_player_pages_history() -> None:
    _, pin = rp.load_pinned_params(2026)
    df = fz.load_player_xfp(pin)
    assert tuple(df.columns) == fz.PLAYER_XFP_COLUMNS
    assert df.height == pin.backtest[fz.PLAYER_XFP].rows
    assert sorted(df.get_column("season").unique()) == list(range(2013, 2026))
    assert not df.select("game_id", "gsis_id").is_duplicated().any()
    assert pin.backtest["player_xfp"].path().stat().st_size < 2_000_000
    # xfp and xfp_ng are NULL together (no ffopportunity row for the game), points_ng never
    assert df["xfp"].is_null().equals(df["xfp_ng"].is_null()) and df["points_ng"].null_count() == 0
    assert df["xfp"].null_count() < df.height // 4


def test_the_frozen_history_is_required(tmp_path) -> None:
    pin_file = tmp_path / "production_models.yaml"
    shutil.copy(ROOT / "config" / "production_models.yaml", pin_file)
    current = pins.read_pins(pin_file)
    pin = current["regression_watch"]
    bt = {t: f for t, f in pin.backtest.items() if t != fz.PLAYER_XFP}
    current["regression_watch"] = replace(pin, backtest=bt)
    pins.write_pins(current, pin_file)
    with pytest.raises(pins.PinError, match="no frozen own-xFP player weeks"):
        fz.load_player_xfp(pins.read_pins(pin_file)["regression_watch"])
    fz.load_snapshot(pins.read_pins(pin_file)["regression_watch"])  # the lists do not need it
    with pytest.raises(PublishInputError, match="no frozen own-xFP player weeks"):
        rl.own_player_weeks(tmp_path / "unused.duckdb", 2026, path=pin_file)


def test_a_changed_frozen_history_is_refused_before_it_is_read(tmp_path, monkeypatch) -> None:
    pin = pins.get_pin("regression_watch")
    bad = replace(pin.backtest[fz.PLAYER_XFP], sha256="0" * 64)
    read = []
    monkeypatch.setattr(pl, "read_parquet", lambda *a, **k: read.append(a) or pl.DataFrame())
    with pytest.raises(pins.PinError, match="not the approved backtest file"):
        fz.load_player_xfp(replace(pin, backtest={**pin.backtest, fz.PLAYER_XFP: bad}))
    assert read == []
    rows = replace(pin.backtest[fz.PLAYER_XFP], rows=1)
    monkeypatch.undo()
    with pytest.raises(pins.PinError, match="rows, the pin says"):
        fz.load_player_xfp(replace(pin, backtest={**pin.backtest, fz.PLAYER_XFP: rows}))


# --------------------------------------------------------------------------------------
# player_week_summary: xfp / fpoe and the no-garbage-time three from the own xFP
# --------------------------------------------------------------------------------------


def _summary() -> pl.DataFrame:
    return pl.DataFrame({
        "gsis_id": ["a", "a", "b"], "season": [2025, 2026, 2026], "week": [3, 1, 1],
        "team": ["X", "X", "Y"], "position": ["WR", "WR", "RB"],
        "fantasy_points": [10.13, 4.0, 0.0], "snap_share": [0.5, 0.6, None],
        "target_share": [0.2, 0.1, 0.0], "carry_share": [None, 0.0, 0.1],
        "points_raw": [10.125, 4.0, 0.0], "player_display_name": ["A", "A", "B"],
    }, schema_overrides={"season": pl.Int32, "week": pl.Int32})  # fmt: skip


def test_the_summary_takes_xfp_and_fpoe_from_the_own_xfp() -> None:
    own = pl.DataFrame({
        "season": [2025, 2026], "week": [3, 1], "game_id": ["g1", "g2"], "gsis_id": ["a", "a"],
        "xfp": [8.004, None], "xfp_ng": [7.5, None], "points_ng": [9.0, 4.0],
        "fpoe_ng": [1.5, None],
    }, schema_overrides={"season": pl.Int32, "week": pl.Int32})  # fmt: skip
    out = col.with_xfp(_summary(), own)
    assert out.columns == list(TABLES["player_week_summary"].names) + ["player_display_name"]
    a25, a26, b26 = out.sort(KEYS).iter_rows(named=True)
    assert (a25["xfp"], a25["fpoe"]) == (8.0, 2.12)  # round(10.125 - 8.004, 2)
    assert (a25["points_ng"], a25["xfp_ng"], a25["fpoe_ng"]) == (9.0, 7.5, 1.5)
    # no ffopportunity row for the game: no xFP, the points without garbage time stay
    assert (a26["xfp"], a26["fpoe"], a26["xfp_ng"], a26["points_ng"]) == (None, None, None, 4.0)
    assert all(b26[c] is None for c in col.XFP_COLUMNS)  # no own-xFP row: NULL
    alone = col.with_xfp(_summary(), None)  # a publish without Regression Watch
    assert alone.select(col.XFP_COLUMNS).null_count().row(0) == (3,) * 5


def test_the_summary_no_longer_reads_ffopportunitys_weekly_xfp() -> None:
    src = inspect.getsource(col.player_week_summary)
    assert "fact_opportunity_week" not in src and "xfp_sql" not in src
    body = inspect.getsource(col.collect)
    assert "rl.own_player_weeks(inputs.warehouse, season)" in body
    assert "with_xfp(pws, own)" in body


# --------------------------------------------------------------------------------------
# The runner's path: frozen history + the pinned live models, never a fit
# --------------------------------------------------------------------------------------


@pytest.fixture()
def pinned(tmp_path, live):  # noqa: F811 (the fixture of test_regression_xfp_switch)
    """(root, pin file): own parameters for SEASON approved with ``live`` and a frozen history
    of 2013 .. SEASON-1 (synthetic rows) added to their pin."""
    params = toy_params(xfp_source="own")
    pin_file = tmp_path / "production_models.yaml"
    shutil.copy(ROOT / "config" / "production_models.yaml", pin_file)
    rp.approve(params, csv_path=toy_csv(tmp_path / "backtest.csv", params), root=tmp_path,
               path=pin_file, live=live, today=datetime(2025, 9, 1, tzinfo=UTC))  # fmt: skip
    seasons = list(range(2013, SEASON))
    rows = pl.DataFrame({
        "season": seasons, "week": [1] * len(seasons), "game_id": [f"g{s}" for s in seasons],
        "gsis_id": ["00-0000001"] * len(seasons), "xfp": [10.0] * len(seasons),
        "xfp_ng": [9.0] * len(seasons), "points_ng": [8.0] * len(seasons),
        "fpoe_ng": [-1.0] * len(seasons),
    }, schema_overrides={"season": pl.Int32, "week": pl.Int32})  # fmt: skip
    file = fz.write_player_xfp(rows, params.model_version, tmp_path)
    current = pins.read_pins(pin_file)
    pin = current["regression_watch"]
    current["regression_watch"] = replace(pin, backtest={**pin.backtest, fz.PLAYER_XFP: file})
    pins.write_pins(current, pin_file)
    return tmp_path, pin_file


def test_the_publish_scores_the_season_with_the_pinned_models_and_never_fits(
    world, live, pinned, monkeypatch  # noqa: F811
) -> None:  # fmt: skip
    from twm.asof import AsOfView
    from twm.modules.regression_watch.player_week import END_OF_TIME, player_games_for

    root, pin_file = pinned
    with AsOfView(world, END_OF_TIME) as view:
        want = fz.player_xfp_rows(ox.live_frame(view, SEASON, live), SEASON)
        ffo = player_games_for(view, SEASON)

    def boom(*a, **k):
        raise AssertionError("the publish must never fit")

    for name in FITS:
        monkeypatch.setattr(ox, name, boom)
    got = rl.own_player_weeks(world, SEASON, path=pin_file, root=root)
    hist, cur = got.filter(pl.col("season") < SEASON), got.filter(pl.col("season") == SEASON)
    assert hist.equals(fz.load_player_xfp(pins.read_pins(pin_file)["regression_watch"], root))
    assert cur.height == want.height > 0 and cur.equals(want)
    # the season's own xFP is the live models', not ffopportunity's
    j = cur.join(ffo.select("game_id", "gsis_id", pl.col("xfp").alias("ffo")),
                 on=["game_id", "gsis_id"])  # fmt: skip
    assert j.height == cur.height and not j["xfp"].equals(j["ffo"])
    assert j["xfp"].is_null().equals(j["ffo"].is_null())


def test_the_weekly_list_and_the_publish_share_one_function() -> None:
    from twm.modules.regression_watch import weekly as rwk

    for code in (inspect.getsource(rwk.run_week), inspect.getsource(rl.own_player_weeks)):
        assert "live_frame(view, season, live)" in code
        assert "live_player_games(" not in code and "with_xfp(" not in code
    for code in (inspect.getsource(rl.own_player_weeks), inspect.getsource(col.collect),
                 inspect.getsource(col.with_xfp)):  # fmt: skip
        for name in FITS:
            assert f"{name}(" not in code, name


# --------------------------------------------------------------------------------------
# `twm model check regression_watch`: required, and re-built where the folds are on disk
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("rebuilt", ["none", "same", "drift", "fewer"])
def test_model_check_rebuilds_the_history_where_the_folds_exist(rebuilt, monkeypatch) -> None:
    from typer.testing import CliRunner

    from twm.cli import app

    frozen = fz.load_player_xfp(pins.get_pin("regression_watch"))
    other = {"none": None, "same": frozen,
             "drift": frozen.with_columns(pl.col("xfp") + 1e-8),
             "fewer": frozen.head(10)}[rebuilt]  # fmt: skip
    seen = []
    monkeypatch.setattr(fz, "rebuild_player_xfp", lambda db, s: seen.append(s) or other)
    res = CliRunner().invoke(app, ["model", "check", "regression_watch", "--season", "2026"])
    assert seen == [2026]
    if rebuilt in ("none", "same"):
        assert res.exit_code == 0, res.output
        assert "player pages' own xFP 2013-2025: 73,677 player-games" in res.output
        assert ("not re-built here" if rebuilt == "none" else "equal within 1e-09") in res.output
    else:
        assert res.exit_code == 1
        assert "disagree with the folds on disk" in res.output


def test_the_jobs_preflight_stops_without_the_frozen_history(tmp_path, monkeypatch) -> None:
    """The scheduled job's preflight (before any download) loads the frozen history too."""
    from twm.pipeline import runner

    pin_file = tmp_path / "production_models.yaml"
    shutil.copy(ROOT / "config" / "production_models.yaml", pin_file)
    current = pins.read_pins(pin_file)
    pin = current["regression_watch"]
    bad = replace(pin.backtest[fz.PLAYER_XFP], sha256="0" * 64)
    current["regression_watch"] = replace(pin, backtest={**pin.backtest, fz.PLAYER_XFP: bad})
    pins.write_pins(current, pin_file)
    monkeypatch.setattr(pins, "default_pin_path", lambda: pin_file)
    with pytest.raises(pins.PinError, match="player_xfp.parquet is not the approved"):
        runner.default_hooks().check_model(2026)
