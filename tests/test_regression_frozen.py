"""Regression Watch's frozen backtest lists (step P2, the reviewer's follow-up): the committed
snapshot is pinned, reproduces the committed backtest CSV and the approved parameters, is never
read when its bytes changed, and the publish reads it instead of recomputing D3. Realdata:
rebuilding it from the owner's warehouse gives the committed snapshot and D3's rows."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from twm import pins
from twm.config import ROOT, league
from twm.modules.regression_watch import frozen as fz
from twm.modules.regression_watch import production as rprod
from twm.publish import collect as col
from twm.publish import regression_lists as rl

CSV = ROOT / "reports" / "regression_watch" / "backtest.csv"


@pytest.fixture(scope="module")
def committed():
    params, pin = rprod.load_pinned_params(2026)
    return params, pin, fz.load_snapshot(pin)


def test_the_committed_snapshot_is_pinned_small_and_matches_the_report(committed) -> None:
    params, pin, frames = committed
    assert pin.backtest_seasons == "2011-2025"
    assert set(pin.backtest) == {*fz.TABLES, fz.PLAYER_XFP}  # PXFP: the player pages' own xFP
    sizes = {t: pin.backtest[t].path().stat().st_size for t in fz.TABLES}
    assert sum(sizes.values()) < 1_000_000, sizes  # the Radar's snapshot is 0.89 MB
    # 10,826 rows since the own walk-forward xFP (H6-b2; 10,787 with ffopportunity's)
    assert frames["predictions"].height == frames["outcomes"].height == 10_826
    assert sorted(frames["predictions"].get_column("week").unique()) == [4, 6, 8, 10]
    assert frames["model_versions"].height == 15
    assert frames["predictions"].get_column("team").null_count() == 0
    assert fz.snapshot_mismatches(frames, CSV, league(), params) == []


def test_the_checks_notice_a_changed_row_choice_or_parameters(committed) -> None:
    params, _, frames = committed

    def problems(**changed) -> list[str]:
        return fz.snapshot_mismatches({**frames, **changed}, CSV, league(), params)

    p = frames["predictions"]
    tagged = p.filter(pl.col("band") == "sell_high").row(0, named=True)
    untag = pl.when((pl.col("entity_id") == tagged["entity_id"]) & (pl.col("season") == tagged[
        "season"]) & (pl.col("week") == tagged["week"]))  # fmt: skip
    text = json.loads(tagged["reasons_json"])
    text["tags"] = []
    cleared = p.with_columns(untag.then(pl.lit(json.dumps(text))).otherwise(pl.col(
        "reasons_json")).alias("reasons_json"))  # fmt: skip
    assert any("sell_high" in x for x in problems(predictions=cleared))
    moved = p.with_columns(untag.then(pl.col("score") + 1).otherwise(pl.col("score")))
    assert any("MAE" in x for x in problems(predictions=moved))
    v = frames["model_versions"]
    notes = [json.loads(n) for n in v.get_column("notes")]
    notes[0]["choice"]["variant"] = "mean_flat_all"
    changed = v.with_columns(pl.Series("notes", [json.dumps(n) for n in notes]))
    assert any(x.startswith("2011: choice mean_flat_all") for x in problems(model_versions=changed))
    other = replace(params, record={**params.record, "variant": "mean_hl4_all"})
    assert any("record of 2025" in x for x in fz.snapshot_mismatches(frames, CSV, league(), other))
    assert fz.snapshot_mismatches(frames, CSV.with_name("missing.csv"), league())[0].startswith(
        "the backtest report is missing")  # fmt: skip


def _copy(pin: pins.Pin, frames: dict, root: Path) -> pins.Pin:
    """The snapshot written under ``root`` and a pin naming it (absolute paths)."""
    files = fz.write_snapshot(frames, pin.model_version, root)
    absolute = {t: replace(f, file=str(root / f.file)) for t, f in files.items()}
    return replace(pin, backtest=absolute)


def test_a_changed_snapshot_is_refused_before_it_is_read(committed, tmp_path, monkeypatch) -> None:
    _, pin, frames = committed
    mine = _copy(pin, frames, tmp_path)
    assert fz.load_snapshot(mine)["predictions"].equals(frames["predictions"])  # bytes reproduce
    assert {t: mine.backtest[t].sha256 for t in fz.TABLES} == {
        t: pin.backtest[t].sha256 for t in fz.TABLES}  # fmt: skip
    path = Path(mine.backtest["predictions"].file)  # read first
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0xFF
    path.write_bytes(bytes(data))

    def never(*a, **k):  # a changed file is never parsed
        raise AssertionError("read a file whose sha256 did not match")

    monkeypatch.setattr(pl, "read_parquet", never)
    with pytest.raises(
        pins.PinError, match="predictions.parquet is not the approved backtest file"
    ):
        fz.load_snapshot(mine)
    monkeypatch.undo()
    path.unlink()
    with pytest.raises(pins.PinError, match="missing"):
        fz.load_snapshot(mine)
    wrong_rows = replace(pin, backtest={**pin.backtest, "model_versions": replace(
        pin.backtest["model_versions"], rows=14)})  # fmt: skip
    with pytest.raises(pins.PinError, match="has 15 rows, the pin says 14"):
        fz.load_snapshot(wrong_rows)
    with pytest.raises(pins.PinError, match="twm regression freeze"):
        fz.load_snapshot(replace(pin, backtest={}))
    # the publish refuses it too, and names the cause
    monkeypatch.setattr(rprod, "load_pinned_params", lambda season: (None, replace(pin, backtest={
        **pin.backtest, "outcomes": mine.backtest["outcomes"], "predictions": mine.backtest[
        "predictions"]})))  # fmt: skip
    with pytest.raises(col.PublishInputError, match="frozen backtest cannot be read"):
        rl.frozen_lists(2026, datetime(2026, 9, 30))


def test_the_publish_reads_the_snapshot_never_the_warehouse_history(committed) -> None:
    _, _, frames = committed
    rows, snap = rl.frozen_lists(2026, datetime(2026, 9, 30, tzinfo=UTC).replace(tzinfo=None))
    assert rows.height == frames["predictions"].height and set(rows["kind"]) == {"backtest"}
    assert snap["predictions"].equals(frames["predictions"])
    assert rows.get_column("season").max() == 2025  # the lists before the approved season


def test_the_pins_file_keeps_every_other_pin_when_the_snapshot_is_added(tmp_path) -> None:
    path = tmp_path / "production_models.yaml"
    path.write_text(pins.default_pin_path().read_text())
    current = pins.read_pins(path)
    current["regression_watch"] = replace(current["regression_watch"], backtest={})
    pins.write_pins(current, path)
    stripped = path.read_text()
    current["regression_watch"] = pins.read_pins()["regression_watch"]
    pins.write_pins(current, path)
    assert path.read_text() == pins.default_pin_path().read_text()
    assert "backtest-mean_flat_all" not in stripped


# --------------------------------------------------------------------------------------
# Real data (the owner's warehouse, read-only)
# --------------------------------------------------------------------------------------


@pytest.mark.realdata
def test_real_rebuild_equals_the_committed_snapshot_and_d3s_rows(committed) -> None:
    from twm.config import settings
    from twm.modules.regression_watch import backtest as bt

    db = settings().path("warehouse")
    if not db.exists():
        pytest.skip("run `uv run twm build` first")
    _, _, frames = committed
    from twm.modules.regression_watch import xfp_source as xs

    rebuilt = fz.build_snapshot(db, 2026, league(), xfp_source="own")
    for t in fz.TABLES:
        assert rebuilt[t].equals(frames[t]), t
    hist, asofs = bt.load_inputs(db, 2025, bt.HEADLINE_WEEKS, xfp=xs.history(db, 2025, "own"))
    d3, _ = bt.build_rows(hist, asofs, league(), range(2010, 2026))
    graded = bt.graded_rows(d3, {s: bt.choose(d3, s) for s in range(2011, 2026)}, league())
    p = frames["predictions"]
    tags = [json.loads(r)["tags"] for r in p.get_column("reasons_json").to_list()]
    mine = p.select("season", "week", pl.col("entity_id").alias("gsis_id"),
                    pl.col("score").alias("mine"), pl.Series("tags", tags))  # fmt: skip
    both = graded.join(mine, on=["season", "week", "gsis_id"], how="full", coalesce=True)
    assert both.height == graded.height == p.height
    assert (both.get_column("mine") - both.get_column("ppg_ros")).abs().max() == 0.0
    for tag in ("sell_high", "buy_low", "legit"):
        assert both.filter(pl.col(tag) != pl.col("tags").list.contains(tag)).height == 0
