"""Thin, cached wrapper around nflreadpy loaders.

Why a wrapper?
- **Local Parquet cache per dataset and season** under ``data/raw``. Historical seasons are
  immutable once fetched; only the current season is refreshed. This makes ``twm build``
  deterministic from cache and keeps nightly runs cheap.
- **Schema snapshots** in ``data/schemas/<dataset>.json`` (column names + dtypes). A later load
  is compared against the snapshot so upstream column renames or removals fail loudly instead
  of silently producing empty features.
- **One place to isolate upstream changes** (spec Section 14).

All functions return Polars DataFrames.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from twm.config import ROOT, settings

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
    """

    name: str
    loader: str
    per_season: bool = True
    kwargs: dict[str, Any] = field(default_factory=dict)
    first_season: int | None = None


# first_season values were verified against real downloads in Step A3 (docs/assumptions.md).
DATASETS: dict[str, Dataset] = {
    d.name: d
    for d in [
        Dataset("pbp", "load_pbp", first_season=1999),
        Dataset(
            "player_stats", "load_player_stats", kwargs={"summary_level": "week"}, first_season=1999
        ),
        Dataset(
            "team_stats", "load_team_stats", kwargs={"summary_level": "week"}, first_season=1999
        ),
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
            "ngs_receiving",
            "load_nextgen_stats",
            kwargs={"stat_type": "receiving"},
            first_season=2016,
        ),
        Dataset(
            "ngs_rushing", "load_nextgen_stats", kwargs={"stat_type": "rushing"}, first_season=2016
        ),
        Dataset("pfr_pass", "load_pfr_advstats", kwargs={"stat_type": "pass"}, first_season=2018),
        Dataset("pfr_rush", "load_pfr_advstats", kwargs={"stat_type": "rush"}, first_season=2018),
        Dataset("pfr_rec", "load_pfr_advstats", kwargs={"stat_type": "rec"}, first_season=2018),
        Dataset("ftn_charting", "load_ftn_charting", first_season=2022),
        Dataset("participation", "load_participation", first_season=2016),
        Dataset(
            "ff_opportunity",
            "load_ff_opportunity",
            kwargs={"stat_type": "weekly"},
            first_season=2006,
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
        Dataset("ff_rankings_draft", "load_ff_rankings", False, {"type": "draft"}),
        Dataset("ff_rankings_week", "load_ff_rankings", False, {"type": "week"}),
        Dataset("ff_rankings_all", "load_ff_rankings", False, {"type": "all"}),
        Dataset("contracts", "load_contracts", per_season=False),
        Dataset("players", "load_players", per_season=False),
        Dataset("teams", "load_teams", per_season=False),
    ]
}


class SchemaDriftError(RuntimeError):
    """Raised when a freshly loaded dataset is missing columns that the snapshot had."""


# --------------------------------------------------------------------------------------
# Paths and nflreadpy configuration
# --------------------------------------------------------------------------------------


def raw_dir() -> Path:
    return settings().path("raw_cache")


def schema_dir() -> Path:
    return settings().path("schemas")


def _configure_nflreadpy() -> None:
    """Enable nflreadpy's own on-disk cache inside data/raw so downloads are reused."""
    from nflreadpy.config import update_config

    cache = raw_dir() / "_nflreadpy_cache"
    cache.mkdir(parents=True, exist_ok=True)
    update_config(cache_mode="filesystem", cache_dir=cache, verbose=False)


def _loader(ds: Dataset) -> Callable[..., pl.DataFrame]:
    import nflreadpy

    return getattr(nflreadpy, ds.loader)


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
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "dataset": name,
        "nflreadpy_version": nflreadpy.__version__,
        "captured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sample_season": season,
        "n_rows_sample": df.height,
        "columns": schema_of(df),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")
    return path


def load_snapshot(name: str) -> dict[str, Any] | None:
    path = snapshot_path(name)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def check_drift(name: str, df: pl.DataFrame) -> dict[str, list[str]]:
    """Compare ``df`` with the stored snapshot.

    Returns {"missing": [...], "added": [...], "retyped": [...]}.
    Raises SchemaDriftError if any snapshot column is missing (added columns are fine).
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
            "Upstream schema changed; inspect and refresh the snapshot deliberately."
        )
    return {"missing": missing, "added": added, "retyped": retyped}


# --------------------------------------------------------------------------------------
# Loading with local Parquet cache
# --------------------------------------------------------------------------------------


def cache_path(name: str, season: int | None) -> Path:
    fname = f"{season}.parquet" if season is not None else "all.parquet"
    return raw_dir() / name / fname


def _is_fresh(path: Path, max_age_hours: float) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < max_age_hours * 3600


def fetch(
    name: str,
    season: int | None = None,
    *,
    force: bool = False,
    max_age_hours: float = 12.0,
    snapshot: bool = True,
) -> pl.DataFrame:
    """Load one dataset (one season for per-season datasets) via the local cache.

    Historical seasons (< current_season) are never re-downloaded unless ``force``.
    The current season and non-seasonal datasets are refreshed when older than ``max_age_hours``.
    """
    ds = DATASETS[name]
    if ds.per_season and season is None:
        raise ValueError(f"{name} is per-season; pass season=")
    if not ds.per_season:
        season = None
    path = cache_path(name, season)
    current = settings().current_season
    immutable = season is not None and season < current

    if not force and path.exists() and (immutable or _is_fresh(path, max_age_hours)):
        df = pl.read_parquet(path)
        if snapshot and load_snapshot(name) is None:
            save_snapshot(name, df, season=season)
        return df

    _configure_nflreadpy()
    fn = _loader(ds)
    df = fn(season, **ds.kwargs) if ds.per_season else fn(**ds.kwargs)
    if not isinstance(df, pl.DataFrame):  # defensive: some loaders may return pandas
        df = pl.from_pandas(df)

    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path)
    if snapshot:
        if load_snapshot(name) is None:
            save_snapshot(name, df, season=season)
        elif not immutable:
            # Drift is checked on current-season and non-seasonal loads only: older seasons
            # legitimately lack columns that were added later (e.g. rosters 1999 vs 2026,
            # depth charts before/after 2025). The legacy depth-chart schema has its own
            # snapshot, ``depth_charts_legacy``.
            check_drift(name, df)
    return df


def fetch_seasons(name: str, seasons: list[int], **kw: Any) -> pl.DataFrame:
    """Concatenate several seasons of a per-season dataset (diagonal concat tolerates
    columns that exist only in some seasons)."""
    frames = [fetch(name, s, **kw) for s in seasons]
    return pl.concat(frames, how="diagonal_relaxed")


def cached_seasons(name: str) -> list[int]:
    d = raw_dir() / name
    if not d.exists():
        return []
    return sorted(int(p.stem) for p in d.glob("*.parquet") if p.stem.isdigit())


def project_relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)
