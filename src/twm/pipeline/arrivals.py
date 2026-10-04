"""What the nightly refresh found (step E4; docs/assumptions.md section 12): per dataset, the
newest season and week in the cache after ``twm ingest`` and when its file was fetched.

The run stores it in ``result.json`` and the job summary, and the publish adds it to the run's
``pipeline_runs`` notes (``arrivals``), so the estimated availability lags can be checked
against the real ones: the first run whose entry shows week N bounds when week N arrived.
Reads the cache files only (Parquet footers and two or three columns); never downloads.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

# the datasets the job summary names (the weekly lists wait for them); the JSON has all
SUMMARY_DATASETS = ("pbp", "player_stats", "snap_counts", "injuries", "schedules")


def _newest(path: Path, season: int | None) -> dict[str, Any]:
    """The newest season (the file's own for a per-season file without the column) and, when
    the file has weeks, its newest week (schedules: of a game with a final result)."""
    cols = pl.read_parquet_schema(path)
    lf = pl.scan_parquet(path)
    out: dict[str, Any] = {"season": season}
    if "season" in cols:
        newest = lf.select(pl.col("season").cast(pl.Int64, strict=False).max()).collect().item()
        out["season"] = None if newest is None else int(newest)
        lf = lf.filter(pl.col("season").cast(pl.Int64, strict=False) == newest)
    if "week" in cols:
        if "result" in cols:  # schedules: every week is listed from spring; count played ones
            lf = lf.filter(pl.col("result").is_not_null())
        week = lf.select(pl.col("week").cast(pl.Int64, strict=False).max()).collect().item()
        out["week"] = None if week is None else int(week)
    return out


def dataset_arrivals(
    season: int, raw: Path | None = None, names: Iterable[str] | None = None
) -> dict[str, dict[str, Any]]:
    """``{dataset: {"season", "week" (when it has weeks), "fetched_utc"}}`` for the current
    season's file of every per-season dataset and every one-file dataset in the cache (``raw``:
    default ``data/raw``). A dataset without a file is left out; an unreadable one says so."""
    from twm.sources import nflverse as nv

    root = raw if raw is not None else nv.raw_dir()
    out: dict[str, dict[str, Any]] = {}
    for name in sorted(names if names is not None else nv.DATASETS):
        per_season = nv.DATASETS[name].per_season
        path = root / name / (f"{season}.parquet" if per_season else "all.parquet")
        if not path.exists():
            continue
        fetched = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        entry: dict[str, Any] = {"fetched_utc": fetched.isoformat(timespec="seconds")}
        try:
            entry = {**_newest(path, season if per_season else None), **entry}
        except Exception as e:  # informative only: the build reads the file itself
            entry["error"] = f"unreadable ({type(e).__name__})"
        out[name] = entry
    return out


def arrivals_text(arrivals: dict[str, dict[str, Any]]) -> str:
    """One line for the job summary: 'pbp 2026 week 4 (fetched 10-06 10:52 UTC), ...'."""
    parts = []
    for name in SUMMARY_DATASETS:
        a = arrivals.get(name)
        if not a:
            continue
        week = f" week {a['week']}" if a.get("week") is not None else ""
        when = str(a.get("fetched_utc", ""))[5:16].replace("T", " ")
        parts.append(f"{name} {a.get('season')}{week} (fetched {when} UTC)")
    return ", ".join(parts)
