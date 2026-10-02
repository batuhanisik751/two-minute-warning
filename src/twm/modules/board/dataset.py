"""The board dataset (step I1b): every end-of-season snapshot 2002 .. last with its features
(:func:`twm.modules.board.features.snapshot_features`, each through its own AsOfView) and the
S+1 labels (:func:`twm.modules.board.populations.add_labels`). Written (git-ignored) to
``data/board/dataset.parquet`` by ``twm board dataset`` and by every ``twm board backtest``
(about 5 s, so it is never stale).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import polars as pl

from twm.asof import AsOfView
from twm.modules.board import features as bf
from twm.modules.board import populations as pop
from twm.modules.board import seasons as bs

FIRST_SNAPSHOT = bs.FIRST_ROSTER_SEASON  # point-in-time positions start in 2002
OUT_DIR = Path("data/board")
Progress = Callable[[str], None]


def complete_seasons(db: Path | str) -> list[int]:
    """Seasons 2002 on whose Super Bowl has been played (each has a snapshot)."""
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(
            "SELECT DISTINCT season FROM fact_game WHERE game_type = 'SB' AND result IS NOT NULL "
            "AND season >= ? ORDER BY season",
            [FIRST_SNAPSHOT],
        ).fetchall()
    finally:
        con.close()
    return [int(r[0]) for r in rows]


def build_dataset(
    db: Path | str,
    *,
    xfp_games: pl.DataFrame | None,
    departures: bf.Departures,
    seasons: list[int] | None = None,
    progress: Progress | None = None,
) -> pl.DataFrame:
    """Features of every snapshot in ``seasons`` (default: every complete season) and labels."""
    say = progress or (lambda _m: None)
    done = complete_seasons(db)
    parts = []
    for s in sorted(seasons or done):
        with AsOfView(db, bs.snapshot_as_of(db, s)) as v:
            parts.append(bf.snapshot_features(v, s, xfp_games=xfp_games, departures=departures))
        say(f"snapshot {s}: {parts[-1].height} players")
    return _labelled(db, pl.concat(parts, how="vertical"), max(done))


def _labelled(db: Path | str, rows: pl.DataFrame, last: int) -> pl.DataFrame:
    # outcomes of S+1 for every S < last: the player seasons visible at the last snapshot
    with AsOfView(db, bs.snapshot_as_of(db, last)) as v:
        nxt = pop.outcomes(bs.player_seasons(v, last))
    return pop.add_labels(rows, nxt, last).sort("season", "gsis_id")


def build_preseason_dataset(
    db: Path | str,
    *,
    xfp_games: pl.DataFrame | None,
    departures: bf.Departures,
    seasons: list[int] | None = None,
    progress: Progress | None = None,
) -> pl.DataFrame:
    """Step I2a: the same rows and labels read at the PRESEASON snapshot of each S (one hour
    before the first week-1 kickoff of S+1; :mod:`twm.modules.board.preseason`). A season whose
    as-of has not passed yet is skipped."""
    from datetime import UTC, datetime

    from twm.modules.board import preseason as pre

    say = progress or (lambda _m: None)
    done = complete_seasons(db)
    parts = []
    for s in sorted(seasons or done):
        try:
            at = pre.preseason_as_of(db, s)
        except bs.SeasonNotOverError:
            continue
        if at > datetime.now(UTC):
            continue
        with AsOfView(db, at) as v:
            parts.append(pre.preseason_features(v, s, xfp_games=xfp_games, departures=departures))
        say(f"preseason {s} ({at:%Y-%m-%d %H:%M} UTC): {parts[-1].height} players")
    return _labelled(db, pl.concat(parts, how="vertical"), max(done))


def write_dataset(df: pl.DataFrame, out_dir: Path, name: str = "dataset.parquet") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    df.write_parquet(path)
    return path
