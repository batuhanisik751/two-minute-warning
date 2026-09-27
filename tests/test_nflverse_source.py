"""Offline tests for the nflverse wrapper: caching, freshness, snapshots and drift detection.

Every test stubs the nflreadpy loader, so they cover the wrapper's own logic only (never the
network). The one exception, ``test_configure_nflreadpy_turns_its_cache_off``, calls the real
configuration hook because that is where the "stale bytes from nflreadpy's cache" bug lived.
"""

import json
import logging
import os
import time

import polars as pl
import pytest

from twm.sources import nflverse as nv

CURRENT = nv.settings().current_season


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


def _use_loader(monkeypatch, df: pl.DataFrame) -> list:
    calls: list = []
    monkeypatch.setattr(nv, "_loader", lambda ds: _fake_loader(df, calls))
    return calls


def _age(path, hours: float) -> None:
    t = time.time() - hours * 3600
    os.utime(path, (t, t))


GOOD = pl.DataFrame({"a": [1, 2], "b": ["x", "y"]})
DRIFTED = pl.DataFrame({"a": [1, 2]})  # column b disappeared upstream


# --------------------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------------------


def test_registry_has_29_unique_names_and_real_loaders():
    import nflreadpy

    assert len(nv._ALL_DATASETS) == len(nv.DATASETS) == 29
    for ds in nv.DATASETS.values():
        assert callable(getattr(nflreadpy, ds.loader)), ds.loader


def test_registry_refuses_duplicate_names():
    dup = [nv.Dataset("x", "load_pbp"), nv.Dataset("x", "load_schedules")]
    with pytest.raises(ValueError, match="duplicate dataset name 'x'"):
        nv._registry(dup)


def test_pfr_entries_pass_summary_level_explicitly():
    for name in ("pfr_pass", "pfr_rush", "pfr_rec"):
        assert nv.DATASETS[name].kwargs["summary_level"] == "week"


def test_unknown_dataset_name_is_a_helpful_error(tmp_dirs):
    with pytest.raises(KeyError, match="unknown dataset 'pbp_stats'; known: "):
        nv.fetch("pbp_stats", 2025)
    with pytest.raises(KeyError, match="unknown dataset"):
        nv.cached_seasons("nope")


def test_season_argument_is_validated(tmp_dirs):
    with pytest.raises(ValueError, match="one file per season"):
        nv.fetch("pbp")
    with pytest.raises(ValueError, match="single file with no seasons"):
        nv.fetch("teams", 2025)


def test_configure_nflreadpy_turns_its_cache_off():
    from nflreadpy.config import CacheMode, get_config, update_config

    before = get_config()
    try:
        nv._configure_nflreadpy()
        assert get_config().cache_mode == CacheMode.OFF
    finally:
        update_config(cache_mode=before.cache_mode, cache_dir=before.cache_dir)


# --------------------------------------------------------------------------------------
# Cache: immutable seasons, freshness, force, one-file datasets
# --------------------------------------------------------------------------------------


def test_fetch_caches_immutable_seasons(tmp_dirs, monkeypatch):
    calls = _use_loader(monkeypatch, GOOD)
    out1 = nv.fetch("snap_counts", 2015)
    out2 = nv.fetch("snap_counts", 2015)  # historical season -> served from parquet
    assert out1.equals(GOOD) and out2.equals(GOOD)
    assert calls == [(2015, {})]
    assert nv.cache_path("snap_counts", 2015).exists()
    assert nv.cached_seasons("snap_counts") == [2015]


def test_current_season_is_refreshed_when_stale_and_on_force(tmp_dirs, monkeypatch):
    calls = _use_loader(monkeypatch, GOOD)
    path = nv.cache_path("pbp", CURRENT)
    nv.fetch("pbp", CURRENT)
    nv.fetch("pbp", CURRENT)  # fresh file -> cache hit
    assert len(calls) == 1
    _age(path, 13)
    nv.fetch("pbp", CURRENT)  # older than max_age_hours -> loader again
    assert len(calls) == 2
    nv.fetch("pbp", CURRENT, force=True)
    nv.fetch("pbp", CURRENT, force=True)
    assert len(calls) == 4
    # an immutable season ignores max_age_hours but honours force
    nv.fetch("pbp", 2010)
    _age(nv.cache_path("pbp", 2010), 100)
    nv.fetch("pbp", 2010)
    assert len(calls) == 5
    nv.fetch("pbp", 2010, force=True)
    assert len(calls) == 6


def test_one_file_dataset_uses_all_parquet(tmp_dirs, monkeypatch):
    calls = _use_loader(monkeypatch, GOOD)
    nv.fetch("ff_rankings_week")
    assert calls == [(None, {"type": "week"})]
    assert nv.cache_path("ff_rankings_week", None).name == "all.parquet"
    assert nv.cache_path("ff_rankings_week", None).exists()
    nv.fetch("ff_rankings_week")
    assert len(calls) == 1


def test_loader_returning_non_polars_fails_loudly(tmp_dirs, monkeypatch):
    monkeypatch.setattr(nv, "_loader", lambda ds: lambda season=None, **kw: {"a": [1]})
    with pytest.raises(TypeError, match="expected polars.DataFrame"):
        nv.fetch("pbp", 2010)
    assert not nv.cache_path("pbp", 2010).exists()


# --------------------------------------------------------------------------------------
# Empty frames and cached_seasons()
# --------------------------------------------------------------------------------------


def test_empty_immutable_frame_is_returned_but_not_cached(tmp_dirs, monkeypatch, caplog):
    empty = pl.DataFrame({"a": pl.Series([], dtype=pl.Int64)})
    calls = _use_loader(monkeypatch, empty)
    with caplog.at_level(logging.WARNING, logger="twm.sources.nflverse"):
        out = nv.fetch("snap_counts", 2012)
    assert out.height == 0
    assert not nv.cache_path("snap_counts", 2012).exists()
    assert "snap_counts 2012: upstream returned 0 rows; not cached" in caplog.text
    nv.fetch("snap_counts", 2012)  # nothing frozen: the loader is asked again
    assert len(calls) == 2


def test_empty_current_season_frame_is_cached(tmp_dirs, monkeypatch):
    empty = pl.DataFrame({"a": pl.Series([], dtype=pl.Int64)})
    _use_loader(monkeypatch, empty)
    nv.fetch("snap_counts", CURRENT)
    assert nv.cache_path("snap_counts", CURRENT).exists()


def test_cached_seasons_ignores_out_of_range_empty_and_tmp_files(tmp_dirs):
    d = nv.raw_dir() / "snap_counts"  # first_season 2013
    d.mkdir(parents=True)
    GOOD.write_parquet(d / "2011.parquet")  # before coverage
    pl.DataFrame({"a": pl.Series([], dtype=pl.Int64)}).write_parquet(d / "2012.parquet")  # empty
    GOOD.write_parquet(d / "2014.parquet")
    GOOD.write_parquet(d / "2013.parquet")
    GOOD.write_parquet(d / "2015.parquet.tmp")  # crashed write
    (d / "notes.parquet").write_bytes(b"")
    assert nv.cached_seasons("snap_counts") == [2013, 2014]
    assert nv.cached_seasons("pbp") == []


# --------------------------------------------------------------------------------------
# Atomic writes
# --------------------------------------------------------------------------------------


def test_atomic_write_leaves_no_partial_file(tmp_path):
    path = tmp_path / "x" / "f.txt"
    nv._atomic_write(path, lambda p: p.write_text("v1"))
    assert path.read_text() == "v1"
    assert not path.with_name("f.txt.tmp").exists()

    def boom(p):
        p.write_text("half")
        raise OSError("disk full")

    with pytest.raises(OSError, match="disk full"):
        nv._atomic_write(path, boom)
    assert path.read_text() == "v1"  # previous good file intact
    assert not path.with_name("f.txt.tmp").exists()


def test_parquet_and_snapshot_writes_go_through_atomic_write(tmp_dirs, monkeypatch):
    seen: list[str] = []
    real = nv._atomic_write

    def spy(path, write):
        seen.append(path.name)
        real(path, write)

    monkeypatch.setattr(nv, "_atomic_write", spy)
    _use_loader(monkeypatch, GOOD)
    nv.fetch("pbp", CURRENT)
    assert seen == [f"{CURRENT}.parquet", "pbp.json"]
    assert not list(nv.raw_dir().rglob("*.tmp")) and not list(nv.schema_dir().rglob("*.tmp"))


def test_failed_parquet_write_keeps_previous_file(tmp_dirs, monkeypatch):
    _use_loader(monkeypatch, GOOD)
    path = nv.cache_path("pbp", CURRENT)
    nv.fetch("pbp", CURRENT)
    _age(path, 13)

    def partial(self, p, *a, **kw):
        p.write_bytes(b"PAR1garbage")
        raise OSError("killed")

    monkeypatch.setattr(pl.DataFrame, "write_parquet", partial)
    with pytest.raises(OSError, match="killed"):
        nv.fetch("pbp", CURRENT)
    assert pl.read_parquet(path).equals(GOOD)
    assert not path.with_name(path.name + ".tmp").exists()


def test_stray_tmp_is_not_served_and_garbage_cache_fails_loudly(tmp_dirs, monkeypatch):
    calls = _use_loader(monkeypatch, GOOD)
    path = nv.cache_path("pbp", 2010)
    path.parent.mkdir(parents=True)
    path.with_name(path.name + ".tmp").write_bytes(b"partial")
    nv.fetch("pbp", 2010)
    assert calls == [(2010, {})]  # the .tmp did not count as a cache hit
    for garbage in (b"PAR1 this is not a parquet file", b"", b"PAR1"):
        path.write_bytes(garbage)
        with pytest.raises(pl.exceptions.ComputeError, match="twm ingest pbp --force"):
            nv.fetch("pbp", 2010)
        # cached_seasons() reads the footer of every file: same error, naming the file
        with pytest.raises(pl.exceptions.ComputeError, match=rf"{path}.*twm ingest pbp --force"):
            nv.cached_seasons("pbp")


# --------------------------------------------------------------------------------------
# Snapshots and drift
# --------------------------------------------------------------------------------------


def test_first_current_season_fetch_writes_snapshot_and_drift_is_detected(tmp_dirs, monkeypatch):
    df = pl.DataFrame({"a": [1], "b": ["x"]})
    _use_loader(monkeypatch, df)
    nv.fetch("pbp", CURRENT)
    snap = json.loads(nv.snapshot_path("pbp").read_text())
    assert snap["columns"] == {"a": "Int64", "b": "String"}
    assert snap["sample_season"] == CURRENT
    assert snap["n_rows_sample"] == 1

    # a column disappears upstream -> loud failure with the refresh recipe
    with pytest.raises(nv.SchemaDriftError, match=r"'b'.*refresh_snapshots.py pbp --download"):
        nv.check_drift("pbp", pl.DataFrame({"a": [1]}))

    # added / retyped columns are reported but allowed
    report = nv.check_drift("pbp", pl.DataFrame({"a": [1.0], "b": ["x"], "c": [True]}))
    assert report == {"missing": [], "added": ["c"], "retyped": ["a"]}


def test_immutable_loads_never_write_a_snapshot(tmp_dirs, monkeypatch):
    _use_loader(monkeypatch, GOOD)
    nv.fetch("pbp", 2010)
    nv.fetch("pbp", 2010)  # cache-hit path either
    assert not nv.schema_dir().exists() or not list(nv.schema_dir().iterdir())
    nv.fetch("pbp", CURRENT)
    assert json.loads(nv.snapshot_path("pbp").read_text())["sample_season"] == CURRENT


def test_snapshot_false_skips_the_write_but_not_the_check(tmp_dirs, monkeypatch):
    _use_loader(monkeypatch, GOOD)
    nv.fetch("pbp", CURRENT, snapshot=False)
    assert not nv.snapshot_path("pbp").exists()
    nv.save_snapshot("pbp", GOOD, season=CURRENT)
    _use_loader(monkeypatch, DRIFTED)
    with pytest.raises(nv.SchemaDriftError):
        nv.fetch("pbp", CURRENT, force=True, snapshot=False)
    # check=False is the deliberate override
    out = nv.fetch("pbp", CURRENT, force=True, snapshot=False, check=False)
    assert out.columns == ["a"]


def test_existing_snapshot_is_not_overwritten_by_fetch(tmp_dirs, monkeypatch):
    nv.save_snapshot("pbp", GOOD, season=CURRENT)
    before = nv.snapshot_path("pbp").read_text()
    _use_loader(monkeypatch, pl.DataFrame({"a": [1], "b": ["x"], "c": [2]}))
    nv.fetch("pbp", CURRENT)
    assert nv.snapshot_path("pbp").read_text() == before


def test_drift_is_checked_before_the_parquet_is_written(tmp_dirs, monkeypatch):
    nv.save_snapshot("pbp", GOOD, season=CURRENT)
    calls = _use_loader(monkeypatch, DRIFTED)
    with pytest.raises(nv.SchemaDriftError, match="'b'"):
        nv.fetch("pbp", CURRENT)
    assert not nv.cache_path("pbp", CURRENT).exists()  # never cached
    with pytest.raises(nv.SchemaDriftError):
        nv.fetch("pbp", CURRENT)  # second call still raises (no stale cache to hide behind)
    assert len(calls) == 2


def test_drift_on_refresh_keeps_the_previous_good_file(tmp_dirs, monkeypatch):
    _use_loader(monkeypatch, GOOD)
    path = nv.cache_path("pbp", CURRENT)
    nv.fetch("pbp", CURRENT)  # writes cache + snapshot {a, b}
    _age(path, 13)
    _use_loader(monkeypatch, DRIFTED)
    with pytest.raises(nv.SchemaDriftError):
        nv.fetch("pbp", CURRENT)
    assert pl.read_parquet(path).columns == ["a", "b"]  # previous good parquet untouched
    _use_loader(monkeypatch, GOOD)
    assert nv.fetch("pbp", CURRENT).equals(GOOD)


def test_drift_is_checked_on_cache_hits_for_current_season_only(tmp_dirs, monkeypatch):
    nv.save_snapshot("pbp", GOOD, season=CURRENT)
    monkeypatch.setattr(nv, "_loader", lambda ds: pytest.fail("must not download"))
    # a drifted parquet written by something else (e.g. an older wrapper version)
    for season in (CURRENT, 2010):
        p = nv.cache_path("pbp", season)
        p.parent.mkdir(parents=True, exist_ok=True)
        DRIFTED.write_parquet(p)
    with pytest.raises(nv.SchemaDriftError, match="'b'"):
        nv.fetch("pbp", CURRENT)
    assert nv.fetch("pbp", 2010).columns == ["a"]  # historical seasons are exempt
    assert nv.fetch("pbp", CURRENT, check=False).columns == ["a"]


def test_added_and_retyped_columns_are_logged(tmp_dirs, monkeypatch, caplog):
    nv.save_snapshot("pbp", GOOD, season=CURRENT)
    _use_loader(monkeypatch, pl.DataFrame({"a": [1.5], "b": ["x"], "c": [True]}))
    with caplog.at_level(logging.WARNING, logger="twm.sources.nflverse"):
        out = nv.fetch("pbp", CURRENT)
    assert out.columns == ["a", "b", "c"]
    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("pbp: column dtypes changed vs snapshot: ['a']" in m for m in msgs)
    assert any("pbp: new upstream columns not in snapshot: ['c']" in m for m in msgs)
    assert all("refresh_snapshots.py pbp" in m for m in msgs)
    assert nv.snapshot_path("pbp").read_text().count('"a": "Int64"') == 1  # snapshot untouched


def test_refresh_snapshot_from_cache_and_with_download(tmp_dirs, monkeypatch):
    _use_loader(monkeypatch, GOOD)
    nv.fetch("depth_charts", 2024)  # immutable: cached, no snapshot
    assert not nv.snapshot_path("depth_charts").exists()
    with pytest.raises(FileNotFoundError, match="not cached"):
        nv.refresh_snapshot("depth_charts", 2023)

    path = nv.refresh_snapshot(
        "depth_charts", nv.DEPTH_CHARTS_LEGACY_LAST_SEASON, snapshot_name=nv.LEGACY_SNAPSHOT
    )
    snap = json.loads(path.read_text())
    assert path.name == "depth_charts_legacy.json"
    assert snap["dataset"] == nv.LEGACY_SNAPSHOT
    assert snap["sample_season"] == 2024 and snap["columns"] == {"a": "Int64", "b": "String"}

    # after a SchemaDriftError, --download accepts the new upstream schema deliberately
    nv.save_snapshot("pbp", GOOD, season=CURRENT)
    calls = _use_loader(monkeypatch, DRIFTED)
    with pytest.raises(nv.SchemaDriftError):
        nv.fetch("pbp", CURRENT)
    nv.refresh_snapshot("pbp", CURRENT, download=True)
    assert len(calls) == 2
    assert json.loads(nv.snapshot_path("pbp").read_text())["columns"] == {"a": "Int64"}
    assert nv.fetch("pbp", CURRENT).columns == ["a"]  # cache and snapshot now agree

    _use_loader(monkeypatch, GOOD)
    nv.refresh_snapshot("teams", None, download=True)
    assert json.loads(nv.snapshot_path("teams").read_text())["sample_season"] is None
    with pytest.raises(ValueError, match="per-season"):
        nv.refresh_snapshot("pbp")


def test_fetch_seasons_tolerates_new_columns(tmp_dirs, monkeypatch):
    frames = {
        2020: pl.DataFrame({"a": [1]}),
        2021: pl.DataFrame({"a": [2], "new_col": ["z"]}),
    }
    monkeypatch.setattr(nv, "_loader", lambda ds: lambda season, **kw: frames[season])
    out = nv.fetch_seasons("injuries", [2020, 2021], snapshot=False)
    assert out.columns == ["a", "new_col"]
    assert out["new_col"].to_list() == [None, "z"]
