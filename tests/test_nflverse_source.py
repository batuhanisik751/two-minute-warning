"""Offline tests for the nflverse wrapper: caching, snapshots and drift detection."""

import json

import polars as pl
import pytest

from twm.sources import nflverse as nv


@pytest.fixture
def tmp_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(nv, "raw_dir", lambda: tmp_path / "raw")
    monkeypatch.setattr(nv, "schema_dir", lambda: tmp_path / "schemas")
    monkeypatch.setattr(nv, "_configure_nflreadpy", lambda: None)
    return tmp_path


def _fake_loader(df: pl.DataFrame, calls: list):
    def fn(season=None, **kw):
        calls.append((season, kw))
        return df

    return fn


def test_registry_names_are_unique_and_loaders_exist():
    import nflreadpy

    for ds in nv.DATASETS.values():
        assert callable(getattr(nflreadpy, ds.loader)), ds.loader


def test_fetch_caches_immutable_seasons(tmp_dirs, monkeypatch):
    df = pl.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    calls: list = []
    monkeypatch.setattr(nv, "_loader", lambda ds: _fake_loader(df, calls))

    out1 = nv.fetch("snap_counts", 2015)
    out2 = nv.fetch("snap_counts", 2015)  # historical season -> served from parquet
    assert out1.equals(df) and out2.equals(df)
    assert calls == [(2015, {})]
    assert nv.cache_path("snap_counts", 2015).exists()
    assert nv.cached_seasons("snap_counts") == [2015]


def test_first_fetch_writes_snapshot_and_drift_is_detected(tmp_dirs, monkeypatch):
    df = pl.DataFrame({"a": [1], "b": ["x"]})
    monkeypatch.setattr(nv, "_loader", lambda ds: _fake_loader(df, []))
    nv.fetch("pbp", 2010)
    snap = json.loads(nv.snapshot_path("pbp").read_text())
    assert snap["columns"] == {"a": "Int64", "b": "String"}

    # a column disappears upstream -> loud failure
    with pytest.raises(nv.SchemaDriftError, match="'b'"):
        nv.check_drift("pbp", pl.DataFrame({"a": [1]}))

    # added / retyped columns are reported but allowed
    report = nv.check_drift("pbp", pl.DataFrame({"a": [1.0], "b": ["x"], "c": [True]}))
    assert report == {"missing": [], "added": ["c"], "retyped": ["a"]}


def test_per_season_dataset_requires_season(tmp_dirs):
    with pytest.raises(ValueError):
        nv.fetch("pbp")


def test_fetch_seasons_tolerates_new_columns(tmp_dirs, monkeypatch):
    frames = {
        2020: pl.DataFrame({"a": [1]}),
        2021: pl.DataFrame({"a": [2], "new_col": ["z"]}),
    }
    monkeypatch.setattr(nv, "_loader", lambda ds: lambda season, **kw: frames[season])
    out = nv.fetch_seasons("injuries", [2020, 2021], snapshot=False)
    assert out.columns == ["a", "new_col"]
    assert out["new_col"].to_list() == [None, "z"]
