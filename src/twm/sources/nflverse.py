"""Thin, cached wrapper around nflreadpy loaders.

Why a wrapper?
- **Local Parquet cache per dataset and season** under ``data/raw``, one file per season
  (``<dataset>/<season>.parquet``) or one file per dataset (``<dataset>/all.parquet``). This is
  the *only* cache: nflreadpy's own on-disk cache is switched off (see ``_configure_nflreadpy``)
  because its 24 h default silently defeated ``force=True`` and the 12 h current-season refresh
  and doubled disk use. Historical seasons are immutable once fetched; only the current season
  and the one-file datasets are refreshed (after ``max_age_hours``). This makes ``twm build``
  deterministic from cache and keeps nightly runs cheap. Files are written atomically
  (temp file + ``os.replace``) so a killed process never leaves a partial file behind.
- **Schema snapshots** in ``data/schemas/<dataset>.json`` (column names + dtypes), taken only
  from a current-season / one-file load so they describe the schema we expect *today*.
  Every current-season / one-file frame is compared against its snapshot, both when it is
  downloaded (before it is written to the cache, so a drifted frame is never cached) and when
  it is served from the cache. A missing column raises :class:`SchemaDriftError`; added or
  retyped columns are logged as warnings. Historical seasons are never checked: they
  legitimately lack columns that were added later. Snapshots are refreshed deliberately with
  ``scripts/refresh_snapshots.py`` (see :func:`refresh_snapshot`), never by ``twm ingest``.
- **One place to isolate upstream changes** (spec Section 14).

All functions return Polars DataFrames with upstream dtypes untouched; casting happens in the
warehouse loader (docs/assumptions.md, "dtype drift").
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from twm.config import ROOT, settings

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------------------
# Loader registry
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Dataset:
    """One nflverse dataset as we use it.

    name: our short name, also the folder under data/raw and the schema file name.
    loader: the nflreadpy function name (verified in Step A3, see docs/assumptions.md).
    per_season: True if the loader takes a ``seasons`` argument and we cache one file per season.
    kwargs: fixed keyword arguments passed to the loader (e.g. stat_type).
    first_season: first season that has rows (verified in A3, docs/assumptions.md §1);
        None for datasets that are one file.
    """

    name: str
    loader: str
    per_season: bool = True
    kwargs: dict[str, Any] = field(default_factory=dict)
    first_season: int | None = None


def _registry(items: Iterable[Dataset]) -> dict[str, Dataset]:
    """Build the name -> Dataset map, refusing duplicate names instead of dropping one."""
    out: dict[str, Dataset] = {}
    for ds in items:
        if ds.name in out:
            raise ValueError(f"duplicate dataset name {ds.name!r} in DATASETS")
        out[ds.name] = ds
    return out


# first_season values were verified against real downloads in Step A3 (docs/assumptions.md).
_ALL_DATASETS: list[Dataset] = [
    Dataset("pbp", "load_pbp", first_season=1999),
    Dataset(
        "player_stats", "load_player_stats", kwargs={"summary_level": "week"}, first_season=1999
    ),
    Dataset("team_stats", "load_team_stats", kwargs={"summary_level": "week"}, first_season=1999),
    Dataset("schedules", "load_schedules", first_season=1999),
    Dataset("snap_counts", "load_snap_counts", first_season=2013),
    Dataset("injuries", "load_injuries", first_season=2009),
    Dataset("depth_charts", "load_depth_charts", first_season=2001),
    Dataset("rosters", "load_rosters", first_season=1999),
    Dataset("rosters_weekly", "load_rosters_weekly", first_season=2002),
    Dataset(
        "ngs_passing", "load_nextgen_stats", kwargs={"stat_type": "passing"}, first_season=2016
    ),
    Dataset(
        "ngs_receiving", "load_nextgen_stats", kwargs={"stat_type": "receiving"}, first_season=2016
    ),
    Dataset(
        "ngs_rushing", "load_nextgen_stats", kwargs={"stat_type": "rushing"}, first_season=2016
    ),
    Dataset(
        "pfr_pass",
        "load_pfr_advstats",
        kwargs={"stat_type": "pass", "summary_level": "week"},
        first_season=2018,
    ),
    Dataset(
        "pfr_rush",
        "load_pfr_advstats",
        kwargs={"stat_type": "rush", "summary_level": "week"},
        first_season=2018,
    ),
    Dataset(
        "pfr_rec",
        "load_pfr_advstats",
        kwargs={"stat_type": "rec", "summary_level": "week"},
        first_season=2018,
    ),
    Dataset("ftn_charting", "load_ftn_charting", first_season=2022),
    Dataset("participation", "load_participation", first_season=2016),
    Dataset(
        "ff_opportunity", "load_ff_opportunity", kwargs={"stat_type": "weekly"}, first_season=2006
    ),
    Dataset(
        "ff_opportunity_pass",
        "load_ff_opportunity",
        kwargs={"stat_type": "pbp_pass"},
        first_season=2006,
    ),
    Dataset(
        "ff_opportunity_rush",
        "load_ff_opportunity",
        kwargs={"stat_type": "pbp_rush"},
        first_season=2006,
    ),
    Dataset("draft_picks", "load_draft_picks", first_season=1980),
    Dataset("combine", "load_combine", first_season=2000),
    # Not per season: one file each, refreshed like the current season.
    Dataset("ff_playerids", "load_ff_playerids", per_season=False),
    Dataset("ff_rankings_draft", "load_ff_rankings", per_season=False, kwargs={"type": "draft"}),
    Dataset("ff_rankings_week", "load_ff_rankings", per_season=False, kwargs={"type": "week"}),
    Dataset("ff_rankings_all", "load_ff_rankings", per_season=False, kwargs={"type": "all"}),
    Dataset("contracts", "load_contracts", per_season=False),
    Dataset("players", "load_players", per_season=False),
    Dataset("teams", "load_teams", per_season=False),
]

DATASETS: dict[str, Dataset] = _registry(_ALL_DATASETS)

# Depth charts changed format in 2025 (timestamped rows instead of week-assigned rows). The
# pre-2025 schema has its own snapshot, refreshed from the last legacy season.
LEGACY_SNAPSHOT = "depth_charts_legacy"
DEPTH_CHARTS_LEGACY_LAST_SEASON = 2024

REFRESH_HINT = (
    "inspect the change, then refresh the snapshot deliberately with "
    "`uv run python scripts/refresh_snapshots.py {name} --download` and review the diff"
)


class SchemaDriftError(RuntimeError):
    """Raised when a freshly loaded dataset is missing columns that the snapshot had."""


def _dataset(name: str) -> Dataset:
    try:
        return DATASETS[name]
    except KeyError:
        raise KeyError(f"unknown dataset {name!r}; known: {sorted(DATASETS)}") from None


# --------------------------------------------------------------------------------------
# Paths and nflreadpy configuration
# --------------------------------------------------------------------------------------


def raw_dir() -> Path:
    return settings().path("raw_cache")


def schema_dir() -> Path:
    return settings().path("schemas")


def _configure_nflreadpy() -> None:
    """Disable nflreadpy's own cache: the per-dataset Parquet files under data/raw are the cache.

    With it on (24 h default), ``force=True`` and the 12 h current-season refresh would silently
    get stale bytes back from nflreadpy instead of a fresh download. This deviates on purpose
    from PROJECT_SPEC.md §4.1 ("enable nflreadpy's cache"); see docs/assumptions.md.
    """
    from nflreadpy.config import update_config

    update_config(cache_mode="off", verbose=False)


def _loader(ds: Dataset) -> Callable[..., pl.DataFrame]:
    import nflreadpy

    return getattr(nflreadpy, ds.loader)


def _atomic_write(path: Path, write: Callable[[Path], None]) -> None:
    """Run ``write`` against a temp file next to ``path`` and move it into place atomically.

    A crash mid-write leaves at most a ``<name>.tmp`` file, never a truncated ``path``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        write(tmp)
        tmp.replace(path)  # os.replace: atomic on the same filesystem
    finally:
        tmp.unlink(missing_ok=True)


# --------------------------------------------------------------------------------------
# Schema snapshots
# --------------------------------------------------------------------------------------


def schema_of(df: pl.DataFrame) -> dict[str, str]:
    return {name: str(dtype) for name, dtype in df.schema.items()}


def snapshot_path(name: str) -> Path:
    return schema_dir() / f"{name}.json"


def save_snapshot(name: str, df: pl.DataFrame, *, season: int | None = None) -> Path:
    """Write (or refresh) the schema snapshot for a dataset."""
    import nflreadpy

    path = snapshot_path(name)
    payload = {
        "dataset": name,
        "nflreadpy_version": nflreadpy.__version__,
        "captured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sample_season": season,
        "n_rows_sample": df.height,
        "columns": schema_of(df),
    }
    text = json.dumps(payload, indent=2, sort_keys=False) + "\n"
    _atomic_write(path, lambda p: p.write_text(text))
    return path


def load_snapshot(name: str) -> dict[str, Any] | None:
    path = snapshot_path(name)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def check_drift(name: str, df: pl.DataFrame) -> dict[str, list[str]]:
    """Compare ``df`` with the stored snapshot ``name``.

    Returns {"missing": [...], "added": [...], "retyped": [...]}.
    Raises SchemaDriftError if any snapshot column is missing (added columns are fine).
    A snapshot records one sample season's dtypes, so "retyped" across *seasons* (e.g. injuries
    ``week`` Float64 before 2021) is expected and cast in the warehouse; this check only runs
    on current-season / one-file frames, where a retype means upstream changed.
    """
    snap = load_snapshot(name)
    if snap is None:
        return {"missing": [], "added": [], "retyped": []}
    old = snap["columns"]
    new = schema_of(df)
    missing = sorted(c for c in old if c not in new)
    added = sorted(c for c in new if c not in old)
    retyped = sorted(c for c in old if c in new and old[c] != new[c])
    if missing:
        raise SchemaDriftError(
            f"{name}: columns in snapshot but not in fresh data: {missing}. "
            f"Upstream schema changed; {REFRESH_HINT.format(name=name)}."
        )
    return {"missing": missing, "added": added, "retyped": retyped}


def _check_and_log_drift(name: str, df: pl.DataFrame) -> None:
    report = check_drift(name, df)
    if report["retyped"]:
        log.warning(
            "%s: column dtypes changed vs snapshot: %s (%s)",
            name,
            report["retyped"],
            REFRESH_HINT.format(name=name),
        )
    if report["added"]:
        log.warning(
            "%s: new upstream columns not in snapshot: %s (%s)",
            name,
            report["added"],
            REFRESH_HINT.format(name=name),
        )


# --------------------------------------------------------------------------------------
# Loading with local Parquet cache
# --------------------------------------------------------------------------------------


def cache_path(name: str, season: int | None) -> Path:
    fname = f"{season}.parquet" if season is not None else "all.parquet"
    return raw_dir() / name / fname


def _is_fresh(path: Path, max_age_hours: float) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < max_age_hours * 3600


def _unreadable_cache(path: Path, name: str, e: Exception) -> pl.exceptions.ComputeError:
    """The error raised for a corrupt cache file: names the file and how to replace it."""
    return pl.exceptions.ComputeError(
        f"cannot read cached file {path} ({e}); delete it or run `uv run twm ingest {name} --force`"
    )


def _read_cached(path: Path, name: str) -> pl.DataFrame:
    try:
        return pl.read_parquet(path)
    except pl.exceptions.ComputeError as e:
        raise _unreadable_cache(path, name, e) from e


def _cached_rows(path: Path, name: str) -> int:
    """Row count from the Parquet footer (no data read); same error as _read_cached on garbage."""
    try:
        return pq.read_metadata(path).num_rows
    except pa.ArrowException as e:
        raise _unreadable_cache(path, name, e) from e


def fetch(
    name: str,
    season: int | None = None,
    *,
    force: bool = False,
    max_age_hours: float = 12.0,
    snapshot: bool = True,
    check: bool = True,
) -> pl.DataFrame:
    """Load one dataset (one season for per-season datasets) via the local cache.

    Historical seasons (< current_season) are never re-downloaded unless ``force``; an empty
    historical frame is returned but not cached (nflverse may backfill it later).
    The current season and one-file datasets are refreshed when older than ``max_age_hours``.

    Schema policy (current season / one-file datasets only; historical seasons are exempt):
    - ``check``: compare the frame with ``data/schemas/<name>.json`` when that snapshot exists,
      on download (before the file is written, so a drifted frame is never cached) and on every
      cache read. Missing columns raise :class:`SchemaDriftError`; added/retyped columns are
      logged as warnings.
    - ``snapshot``: write the snapshot when none exists yet. ``snapshot=False`` only suppresses
      that write; drift is still checked. Use ``check=False`` (as :func:`refresh_snapshot` does)
      to accept a changed upstream schema deliberately.
    """
    ds = _dataset(name)
    if ds.per_season and season is None:
        raise ValueError(f"{name} is stored one file per season: call fetch({name!r}, season=2025)")
    if not ds.per_season and season is not None:
        raise ValueError(f"{name} is a single file with no seasons: call fetch({name!r})")
    path = cache_path(name, season)
    current = settings().current_season
    immutable = season is not None and season < current
    check = check and not immutable
    snapshot = snapshot and not immutable

    if not force and path.exists() and (immutable or _is_fresh(path, max_age_hours)):
        df = _read_cached(path, name)
        if check and load_snapshot(name) is not None:
            _check_and_log_drift(name, df)
        if snapshot and load_snapshot(name) is None:
            save_snapshot(name, df, season=season)
        return df

    _configure_nflreadpy()
    fn = _loader(ds)
    df = fn(season, **ds.kwargs) if ds.per_season else fn(**ds.kwargs)
    if not isinstance(df, pl.DataFrame):
        raise TypeError(f"{ds.loader} returned {type(df).__name__}, expected polars.DataFrame")

    if check and load_snapshot(name) is not None:
        _check_and_log_drift(name, df)  # raises before anything is written
    if immutable and df.height == 0:
        log.warning("%s %s: upstream returned 0 rows; not cached", name, season)
        return df
    _atomic_write(path, df.write_parquet)
    if snapshot and load_snapshot(name) is None:
        save_snapshot(name, df, season=season)
    return df


def fetch_seasons(name: str, seasons: list[int], **kw: Any) -> pl.DataFrame:
    """Concatenate several seasons of a per-season dataset.

    Uses ``diagonal_relaxed``: columns that exist only in some seasons are null-filled, and a
    column whose dtype differs across seasons is widened to the common supertype (e.g. injuries
    ``week`` Int32 + Float64 -> Float64, rosters ``draft_number`` Int32 + String -> String).
    Callers must cast join keys (``season``, ``week``, ``play_id``, ...) explicitly; see the
    dtype-drift list in docs/assumptions.md §10.
    """
    frames = [fetch(name, s, **kw) for s in seasons]
    return pl.concat(frames, how="diagonal_relaxed")


def cached_seasons(name: str) -> list[int]:
    """Seasons with a usable cache file: within the dataset's coverage and not empty."""
    ds = _dataset(name)
    d = raw_dir() / name
    if not d.exists():
        return []
    out = []
    for p in d.glob("*.parquet"):
        if not p.stem.isdigit():
            continue
        season = int(p.stem)
        if ds.first_season is not None and season < ds.first_season:
            continue
        if _cached_rows(p, name) == 0:
            continue
        out.append(season)
    return sorted(out)


def refresh_snapshot(
    name: str,
    season: int | None = None,
    *,
    snapshot_name: str | None = None,
    download: bool = False,
) -> Path:
    """Deliberately (re)write ``data/schemas/<snapshot_name or name>.json``.

    By default the frame comes from the local cache (``season`` for per-season datasets, the
    one file otherwise) and nothing is downloaded. With ``download=True`` the season is
    re-downloaded with drift checking off, which is how a :class:`SchemaDriftError` is resolved
    once the upstream change has been reviewed. Historical seasons are cached first only when
    ``download=True``.
    """
    ds = _dataset(name)
    if ds.per_season and season is None:
        raise ValueError(f"{name} is per-season: pass season=")
    if not ds.per_season:
        season = None
    if download:
        df = fetch(name, season, force=True, snapshot=False, check=False)
    else:
        path = cache_path(name, season)
        if not path.exists():
            raise FileNotFoundError(
                f"{path} is not cached; run `uv run twm ingest {name}` first or pass download=True"
            )
        df = _read_cached(path, name)
    return save_snapshot(snapshot_name or name, df, season=season)


def project_relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)
