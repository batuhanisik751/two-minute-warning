"""Scoring the Cliff board of a season with the APPROVED models (step I2c-a; the Hot-Seat
Meter's weekly.py pattern): `twm board score --season S1`.

The board of S1 = every Cliff player of snapshot S = S1 - 1 (3+ prior seasons, top-36 PPG at his
position in S) read at the preseason as-of of the pinned anchor (config
``as_of.board.preseason``: one hour before the first week-1 kickoff of S1), through an AsOfView
(:func:`twm.modules.board.dataset.build_preseason_dataset`); the ECR rank only when its scrape is
public by then. Both chances come from the pinned models (sha256 checked before a pickle is
opened; nothing is fitted). Stored in the predictions store as two rows per player (rank_group
'cliff' and 'missed', each with its model's version), week 0 (before week 1). The list is
'live' only when scored on the real clock between the as-of and the kickoff; otherwise it is
'backtest' (reconstructed; the 2026 board, scored after its kickoff eve, is one). A stored live
board is never overwritten (append-only, as the Hot-Seat lists).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl

from twm import predictions as pr
from twm.modules.board import production as bp
from twm.modules.waiver_radar import weekly as rw

EXIT_NOT_READY = rw.EXIT_NOT_READY
ENTITY_TYPE = "player"


class NotDueError(ValueError):
    """The board's as-of has not passed yet (its week-1 depth charts are not public)."""


@dataclass
class BoardRun:
    season: int  # the board's season S1
    as_of: datetime  # the preseason as-of of snapshot S1 - 1 (aware UTC)
    kickoff: datetime  # the first week-1 kickoff of S1
    now: datetime
    kind: str  # 'live' or 'backtest' (reconstructed)
    models: dict[str, Any] = field(repr=False)  # role -> the approved ProductionModel
    table: pl.DataFrame = field(repr=False)  # production.score_board(): one row per player
    note: str | None = None  # step I6b: the live list's note (which as-of and depth chart)


def board_rows(db: Path | str, season: int, anchor: str, *, xfp_games: Any,
               departures: Any, at: datetime | None = None) -> pl.DataFrame:  # fmt: skip
    """The population rows of snapshot ``season`` - 1 at its preseason as-of (point in time;
    ``at``: the live board's as-of instead, step I6b)."""
    from twm.modules.board import dataset as bd

    s = int(season) - 1
    df = bd.build_preseason_dataset(db, xfp_games=xfp_games, departures=departures, seasons=[s],
                                    anchor=anchor, at=at)  # fmt: skip
    return bp.population(df.filter(pl.col("season") == s))


def run_board(
    db: Path | str,
    season: int,
    *,
    models: Mapping[str, Any],
    anchor: str,
    now: datetime,
    rows: pl.DataFrame | None = None,
    real_clock: bool = True,
    as_of: datetime | None = None,
    note: str | None = None,
    **inputs: Any,
) -> BoardRun:
    """Make the board of ``season`` with the approved ``models`` (nothing stored, nothing
    fitted). :class:`NotDueError` before the as-of; ``rows``: :func:`board_rows` (default:
    built now from ``inputs``, the own xFP games and the departures). ``as_of`` / ``note``
    (step I6b): the live board's as-of (config ``as_of.board.live_publish``) and its list
    note, instead of the pinned anchor's as-of."""
    from twm.modules.board import preseason as pre

    s = int(season) - 1
    got = sorted({int(pm.season) + 1 for pm in models.values()})
    if got != [int(season)]:
        raise ValueError(f"the approved board models make the board of {got}, not {season}")
    override, as_of = as_of, as_of if as_of is not None else pre.preseason_as_of(db, s, anchor)
    if as_of > now:
        raise NotDueError(f"the {season} board is due at {as_of:%Y-%m-%d %H:%M} UTC ({anchor})")
    kickoff = pre.first_week1_kickoff(db, s)
    rows = rows if rows is not None else board_rows(db, season, anchor, at=override, **inputs)
    if rows.height == 0:
        raise LookupError(f"no Cliff player at the {season} preseason snapshot")
    rows = bp.ecr_columns(db, rows, {s: as_of})
    kind = rw.run_kind(as_of, kickoff, now) if real_clock else "backtest"
    return BoardRun(int(season), as_of, kickoff, now, kind, dict(models),
                    bp.score_board(models, rows), note)  # fmt: skip


# --------------------------------------------------------------------------------------
# Storing (append-only for live boards)
# --------------------------------------------------------------------------------------


def reasons(r: Mapping[str, Any], role: str, note: str | None = None) -> str:
    """A stored row's ``reasons_json``: team, position, snapshot season, the ECR rank (and the
    baseline's fill), the key features (:data:`.production.SHOWN`) and this role's drivers;
    with ``note`` (step I6b: the live list's note) also ``note``."""
    out = {"team": r["team"], "position": r["position"], "snapshot_season": r["snapshot_season"],
           "ecr_rank": r["ecr_rank"], "ecr_fill": r["ecr_fill"],
           "features": {c: r[c] for c in bp.SHOWN},
           "drivers": json.loads(r[f"{role}_drivers_json"])}  # fmt: skip
    if note is not None:
        out["note"] = note
    return json.dumps(out, default=float)


def list_rows(run: BoardRun, created: datetime) -> pl.DataFrame:
    """The store's ``predictions`` rows of a board: per player one row per role (rank_group
    'cliff' / 'missed', the role's model version, probability and rank), week 0."""
    t, parts = run.table, []
    for role in bp.ROLES:
        parts.append(t.select(
            pl.lit(bp.MODULE).alias("module"), pl.lit(ENTITY_TYPE).alias("entity_type"),
            pl.col("gsis_id").alias("entity_id"), pl.col("season").cast(pl.Int32),
            pl.lit(bp.WEEK, dtype=pl.Int32).alias("week"),
            pl.col("as_of").cast(pl.Datetime("us")),
            pl.lit(None, dtype=pl.Int32).alias("horizon"), pl.lit(role).alias("rank_group"),
            pl.col(f"{role}_prob").alias("score"), pl.col(f"{role}_prob").alias("raw_score"),
            pl.col(f"{role}_rank").cast(pl.Int32).alias("rank"),
            pl.lit(None, dtype=pl.String).alias("band"),
            pl.col(f"{role}_version").alias("model_version"),
            pl.Series("reasons_json", [reasons(r, role, run.note) for r in t.to_dicts()],
                      dtype=pl.String),
            pl.lit(run.kind).alias("kind"),
            pl.lit(created, dtype=pl.Datetime("us")).alias("created_at"),
            pl.lit(None, dtype=pl.String).alias("tier"), pl.lit(False).alias("incomplete"),
        ))  # fmt: skip
    return pl.concat(parts, how="vertical")


def stored_live(store: Path | str, run: BoardRun) -> int:
    """Rows of a stored LIVE board of the run's versions and season."""
    if not Path(store).exists():
        return 0
    versions = [pm.model_version for pm in run.models.values()]
    con = pr.connect(store, read_only=True)
    try:
        row = con.execute(
            "SELECT count(*) FROM predictions WHERE module = ? AND kind = 'live' AND season = ? "
            "AND week = ? AND list_contains(?, model_version)",
            [bp.MODULE, run.season, bp.WEEK, versions],
        ).fetchone()
    finally:
        con.close()
    return int(row[0]) if row else 0


def live_rows(store: Path | str, season: int) -> int:
    """Rows of any stored LIVE board of ``season`` (step I6b: the job scores it once)."""
    if not Path(store).exists():
        return 0
    con = pr.connect(store, read_only=True)
    try:
        row = con.execute(
            "SELECT count(*) FROM predictions WHERE module = ? AND kind = 'live' AND season = ? "
            "AND week = ?",
            [bp.MODULE, int(season), bp.WEEK],
        ).fetchone()
    finally:
        con.close()
    return int(row[0]) if row else 0


def store_board(run: BoardRun, store: Path | str, *, created_at: datetime | None = None) -> dict:
    """Store the board (``replace='weeks'``) unless a LIVE board of the same versions and season
    is already stored: then nothing is written and ``{'predictions': 0, 'kept': n}`` says so
    (append-only); the store itself refuses a reconstructed overwrite of a live week."""
    created = created_at if created_at is not None else pr.now_utc()
    kept = stored_live(store, run)
    if kept:
        return {"predictions": 0, "model_versions": 0, "outcomes": 0, "kept": kept}
    versions = pl.concat([bp.rprod.version_frame(pm, created_at=created)
                          for pm in run.models.values()], how="vertical")  # fmt: skip
    out = pr.write_predictions(store, predictions=list_rows(run, created), versions=versions,
                               replace="weeks")  # fmt: skip
    return {**out, "kept": 0}
