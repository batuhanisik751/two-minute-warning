"""`twm publish` for the K and D/ST streamer and Regression Watch (step P2), offline: the
checks before writing, the generic live-list plan, the frozen backtests' point-in-time chances,
the reproduction checks against the committed reports, the track-record CSVs and the run's
exported lists. The Postgres side: tests/test_publish_postgres.py (P2 section)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from tests import publish_modules_synthetic as pm
from tests import publish_synthetic as ps
from twm.config import ROOT
from twm.publish import collect as col
from twm.publish import regression_lists as rl
from twm.publish import stream_lists as sl
from twm.publish import write as wr
from twm.publish.tables import FAMILIES, SHARED, TABLES


@pytest.fixture
def data(tmp_path: Path) -> col.PublishData:
    return pm.add_modules(col.collect(ps.build(tmp_path, live_weeks=(3,)).inputs))


def test_every_list_table_belongs_to_one_module() -> None:
    assert list(FAMILIES) == list(col.MODULES)
    for f in FAMILIES.values():
        for name in (f.lists, f.rows):
            assert TABLES[name].mode == "lists" and "kind" in TABLES[name].names
        assert TABLES[f.outcomes].mode == "replace" and f.row_id in TABLES[f.outcomes].names
        assert f.version in TABLES[f.lists].names
    assert set(wr.guarded(["streamer"])) == {"stream_list", "stream_pick", "stream_outcome",
                                            "stream_track_record", *SHARED}  # fmt: skip
    assert list(wr.units(FAMILIES))[:6] == ["backtest_lists", "radar_outcome", "track_record",
                                            "tier_stats", "stream_backtest_lists",
                                            "stream_outcome"]  # fmt: skip


def test_the_synthetic_modules_pass_and_broken_rows_are_caught(data: col.PublishData) -> None:
    assert col.validate(data) == []
    st, rw = data.families["streamer"], data.families["regression_watch"]
    good_st, good_rw = st.rows, rw.rows

    def problems(streamer=None, regression=None) -> list[str]:
        st.rows = good_st if streamer is None else streamer
        rw.rows = good_rw if regression is None else regression
        return col.validate(data)

    other = pl.when(pl.col("entity_id") == "DST-BUF").then(pl.lit("DST-DAL"))
    assert any("do not match their position" in p for p in problems(good_st.with_columns(
        other.otherwise(pl.col("entity_id")).alias("entity_id"))))  # fmt: skip
    assert any("neither a gsis id nor DST-<team>" in p for p in problems(good_st.with_columns(
        pl.when(pl.col("rank") == 1).then(pl.lit("K-17")).otherwise(pl.col("entity_id"))
        .alias("entity_id"))))  # fmt: skip
    assert any("K picks have no model probability" in p for p in problems(
        good_st.with_columns(pl.lit(None, dtype=pl.Float64).alias("model_prob"))))  # fmt: skip
    assert any("1..n" in p for p in problems(good_st.with_columns(
        (pl.col("rank") * 2).alias("rank"))))  # fmt: skip
    assert any("outside [0, 1]" in p for p in problems(good_st.with_columns(
        pl.lit(1.2).alias("chance_high"))))  # fmt: skip
    assert any("not the first of their tags" in p for p in problems(regression=good_rw.with_columns(
        pl.when(pl.col("tag") == "buy_low").then(pl.lit("legit")).otherwise(pl.col("tag"))
        .alias("tag"))))  # fmt: skip
    assert any("one row per player" in p for p in problems(regression=good_rw.filter(
        pl.col("gsis_id") != good_rw.get_column("gsis_id")[0])))  # fmt: skip
    assert any("missing from dim_player" in p for p in problems(regression=good_rw.with_columns(
        pl.lit("00-0999998").alias("gsis_id"))))  # fmt: skip
    assert problems() == []


def test_live_decisions_name_their_module(data: col.PublishData) -> None:
    got = {m: wr.plan_live(data, {}, allow_incomplete=False, replace_live=[], module=m)
           for m in col.MODULES}  # fmt: skip
    assert [d.label for d in got["streamer"][0]] == ["streamer 2026-W03 K",
                                                     "streamer 2026-W03 DST"]  # fmt: skip
    assert [d.label for d in got["regression_watch"][0]] == ["regression watch 2026-W03"]
    assert got["regression_watch"][1].columns == ["season", "week"]
    assert {d.label for d in got["waiver_radar"][0]} >= {"2026-W03 QB"}
    # a week no module has a local live list of cannot be replaced
    with pytest.raises(wr.PublishError, match="no live list of 2026 week 9"):
        wr.plan_live(data, {}, allow_incomplete=False, replace_live=[(2026, 9)],
                     module="streamer")  # fmt: skip


def _frozen(flip: int | None = None) -> dict:
    """A K snapshot and a D/ST hit-rate table of 2013-2015 (``flip``: that season's outcomes
    reversed)."""
    rng = np.random.default_rng(7)
    preds, outs, hits = [], [], []
    for s in (2013, 2014, 2015):
        for w in range(1, 18):
            for r in range(1, 16):
                e, p = f"00-00{s % 100}{w:02d}{r:02d}"[:10], float(rng.uniform(0.1, 0.6))
                y = bool(rng.uniform() < p) != (flip == s)
                preds.append({"season": s, "week": w, "rank_group": "K", "entity_id": e,
                              "score": p, "rank": r})  # fmt: skip
                outs.append({"season": s, "week": w, "entity_id": e, "y_start": y})
        hits += [
            {
                "season": s,
                "list_length": 8,
                "rank": r,
                "lists": 17,
                "starts": (17 - 2 * r if flip != s else 2 * r) % 18,
            }
            for r in range(1, 9)
        ]
    return {"k": {"predictions": pl.DataFrame(preds), "outcomes": pl.DataFrame(outs)},
            "hit_rates": pl.DataFrame(hits).cast({c: pl.Int32 for c in ("season", "list_length",
                                                                          "rank")})}  # fmt: skip


def test_backtest_chances_read_only_earlier_seasons() -> None:
    rows = pl.DataFrame([
        {"season": s, "week": 1, "position": pos, "kind": "backtest", "rank": r,
         "score": 0.2 + 0.05 * r if pos == "K" else None, "band": None, "tier": None}
        for s in (2013, 2014, 2015) for pos in ("K", "DST") for r in range(1, 6)
    ], schema_overrides={"score": pl.Float64, "band": pl.String, "tier": pl.String})  # fmt: skip
    base = sl.backtest_bands(rows, _frozen())
    assert base.filter(pl.col("season") == 2013).get_column("band").null_count() == 10
    assert base.filter(pl.col("season") > 2013).get_column("band").null_count() == 0
    moved = sl.backtest_bands(rows, _frozen(flip=2014))
    same = base.filter(pl.col("season") <= 2014).equals(moved.filter(pl.col("season") <= 2014))
    assert same  # a list never rests on its own season's (or later) outcomes
    assert not base.filter(pl.col("season") == 2015).equals(moved.filter(pl.col("season") == 2015))
    assert sl.backtest_bands(rows, _frozen(flip=2015)).equals(base)


def test_tag_reasons_notes_and_negative_zero() -> None:
    r = {"tags": ["buy_low", "legit"], "fpoe_pg": -4.6, "projection": 13.2, "gap": 4.3,
         "ppg": 8.9, "ppg_rank": 11, "x": {"sell_high": 4.5, "buy_low": 3.5}}  # fmt: skip
    text = rl.tag_reason(r, "WR", {"WR": 24})
    assert text is not None and text.startswith("Buy-low: his -4.6 points over expected")
    assert "4.3 points per game above his 8.9 PPG (the cutoff is 3.5)" in text
    assert "Legit: his PPG ranks No. 11 among WRs, inside the starter threshold (24)" in text
    assert rl.tag_reason({"tags": []}, "QB", {}) is None
    df = rl.no_negative_zero(pl.DataFrame({"a": [-0.0, 1.5, None], "b": ["x", "y", "z"]}))
    assert str(df.get_column("a").to_list()) == "[0.0, 1.5, None]"


def test_track_records_are_the_csvs_row_for_row() -> None:
    for table, path, rename in (
        ("stream_track_record", "reports/streamer/backtest.csv", None),
        ("regression_track_record", "reports/regression_watch/backtest.csv",
         {"table": "section", "group": "row_group"}),
    ):  # fmt: skip
        csv = pl.read_csv(ROOT / path, infer_schema_length=0)
        got = col.csv_table(ROOT / path, table, rename)
        assert got.columns == list(TABLES[table].names) and got.height == csv.height
        assert got.get_column("line").to_list() == list(range(1, csv.height + 1))
        first = csv.row(0, named=True)
        assert float(got.get_column("value")[0]) == float(first["value"])
    rw = col.csv_table(ROOT / "reports/regression_watch/backtest.csv", "regression_track_record",
                       {"table": "section", "group": "row_group"})  # fmt: skip
    assert set(rw.get_column("section").unique()) >= {"value", "tag", "choice", "threshold"}


def test_the_run_exports_a_modules_stored_list(tmp_path: Path) -> None:
    import duckdb

    from twm.pipeline.report import export_module_list

    store = tmp_path / "p.duckdb"
    con = duckdb.connect(str(store))
    con.execute(
        "CREATE TABLE predictions AS SELECT 'streamer' AS module, 2026 AS season, 3 AS week, "
        "TIMESTAMP '2026-09-29 14:00' AS as_of, 'live' AS kind, FALSE AS incomplete, 'K' AS "
        "rank_group, 1 AS rank, '00-0090000' AS entity_id, 0.4 AS score, '{}' AS band, "
        "'watch' AS tier, '[]' AS reasons_json, 'logit_k-x' AS model_version"
    )
    con.close()
    info = export_module_list(store, "streamer", 2026, 3, tmp_path / "lists")
    assert [Path(f).name for f in info["files"]] == ["streamer-2026-W03.csv",
                                                     "streamer-2026-W03.parquet"]  # fmt: skip
    assert info["kind"] == "live" and info["incomplete"] is False
    assert pl.read_parquet(info["files"][1]).get_column("entity_id").to_list() == ["00-0090000"]
    assert export_module_list(store, "regression_watch", 2026, 2, tmp_path)["files"] == []


# --------------------------------------------------------------------------------------
# Real data (the owner's warehouse and datasets, read-only; the store is a scratch copy)
# --------------------------------------------------------------------------------------


def _real(path: Path) -> Path:
    if not path.exists():
        pytest.skip(f"{path} is missing")
    return path


@pytest.mark.realdata
def test_real_streamer_backtest_lists_are_the_frozen_backtest(tmp_path: Path) -> None:
    import shutil

    import duckdb

    from twm.config import settings

    s = settings()
    store = tmp_path / "copy.duckdb"
    shutil.copy(_real(s.path("predictions")), store)
    con = duckdb.connect(str(_real(s.path("warehouse"))), read_only=True)
    try:
        got = sl.collect_streamer(store, _real(ROOT / "data" / "streamer" / "dataset.parquet"),
                                  ROOT / "reports" / "streamer" / "backtest.csv", 2026, con,
                                  datetime(2026, 9, 30, 2))  # fmt: skip
    finally:
        con.close()
    back = got.data.rows.filter(pl.col("kind") == "backtest")
    per = back.group_by("position").len().sort("position")
    assert per.rows() == [("DST", 1424), ("K", 2318)]  # the snapshot's K rows; the rule's D/ST
    first = back.filter(pl.col("season") == 2013)
    assert first.get_column("chance").null_count() == first.height  # no earlier season
    assert back.filter(pl.col("season") > 2013).get_column("chance").null_count() == 0
    assert got.track_record.height == 246 and got.versions.height == 26
