"""Rewrite the schema snapshots in data/schemas from the local Parquet cache.

Usage:
    uv run python scripts/refresh_snapshots.py                # every snapshot, from cache
    uv run python scripts/refresh_snapshots.py pbp injuries   # a subset
    uv run python scripts/refresh_snapshots.py pbp --download # re-download the season first

Per-season datasets are snapshotted from the current season. When the current season is not
cached the latest cached season is used instead and a WARNING is printed: that is right for
participation (published only after the season) but wrong for a dataset whose current season
simply was not ingested yet (the snapshot would describe an older schema), so read the warning
and run `twm ingest <name>` first if in doubt. One-file datasets come from all.parquet;
``depth_charts_legacy`` from depth_charts <DEPTH_CHARTS_LEGACY_LAST_SEASON>. ``--download``
re-fetches with drift checking off: that is the deliberate way to accept an upstream schema
change after a SchemaDriftError. Review `git diff data/schemas` before committing.
"""

from __future__ import annotations

import argparse
import sys
from typing import NamedTuple

from twm.config import settings
from twm.sources import nflverse as nv


class Target(NamedTuple):
    dataset: str
    season: int | None
    snapshot_name: str
    warning: str | None = None  # set when the season is not the one the snapshot should describe


def targets(names: list[str]) -> list[Target]:
    """What to snapshot for every requested name, and a warning when it is a fallback."""
    current = settings().current_season
    out: list[Target] = []
    for name in names:
        if name == nv.LEGACY_SNAPSHOT:
            out.append(Target("depth_charts", nv.DEPTH_CHARTS_LEGACY_LAST_SEASON, name))
            continue
        ds = nv.DATASETS[name]
        if not ds.per_season:
            out.append(Target(name, None, name))
            continue
        cached = nv.cached_seasons(name)
        if current in cached or not cached:
            out.append(Target(name, current, name))
            continue
        out.append(
            Target(
                name,
                cached[-1],
                name,
                f"{name}: season {current} is not cached, snapshotting {cached[-1]} instead; "
                f"fine for a dataset published after the season (participation), otherwise "
                f"run `uv run twm ingest {name} --start {current}` first",
            )
        )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "names", nargs="*", help="snapshot names (default: all, incl. depth_charts_legacy)"
    )
    ap.add_argument(
        "--download", action="store_true", help="re-download instead of reading the cache"
    )
    args = ap.parse_args(argv)

    names = args.names or [*nv.DATASETS, nv.LEGACY_SNAPSHOT]
    unknown = [n for n in names if n not in nv.DATASETS and n != nv.LEGACY_SNAPSHOT]
    if unknown:
        ap.error(f"unknown snapshot names {unknown}; known: {[*nv.DATASETS, nv.LEGACY_SNAPSHOT]}")

    failed = 0
    for name, season, snap_name, warning in targets(names):
        if warning:
            print(f"WARNING {warning}", file=sys.stderr)
        try:
            path = nv.refresh_snapshot(
                name, season, snapshot_name=snap_name, download=args.download
            )
        except Exception as e:  # report and keep going; the exit code says it failed
            failed += 1
            print(f"{snap_name:22s} FAILED: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        snap = nv.load_snapshot(snap_name) or {}
        print(
            f"{snap_name:22s} season={season or 'all':<5} rows={snap.get('n_rows_sample', 0):>9,} "
            f"cols={len(snap.get('columns', {})):>4}  -> {nv.project_relative(path)}"
        )
    print(f"{len(names) - failed} written, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
