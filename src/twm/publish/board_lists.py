"""The Cliff board as ``twm publish`` writes it (step I2c-a; the Hot-Seat lists' pattern).
Reads only.

Sources (opened read-only), with the other modules' rules (:mod:`twm.publish.collect`):

- **live boards** and the current season's reconstructed board: the predictions store
  (``module = 'board'``: two rows per player, rank_group 'cliff' and 'missed', each with its
  model's version; :mod:`twm.modules.board.live`), one version per (season, week, snapshot, kind)
  and role, the latest written;
- **backtest boards** (the boards of 2008 .. the season before the approved one, from the
  walk-forward of snapshots 2007 ..): the FROZEN snapshot pinned with the approved models
  (:mod:`twm.modules.board.production`; every file's sha256 checked before it is read). Never
  recomputed here: ``twm model check board`` checks it against reports/board/preseason_cliff.*;
- **outcomes**: the frozen snapshot's labels (games and PPG of the board's season, ``y_cliff``,
  ``y_missed``; 'final'); live seasons' rows are 'pending' for every warehouse player (the
  labels come after the season), so a frozen live board no longer in the local store still gets
  its outcome rows;
- **track record**: reports/board/<anchor prefix>cliff.csv and breakout.csv row for row (the
  Breakout rows flagged ``research``: Breakout is not on the site, owner 2026-10-02);
- **disagreements**: per ECR-era board (2020-2025) and model, the model's and the ECR's top-10
  picks and how many had the label (:func:`~twm.modules.board.production.disagreement_table`).

A board's rows are every Cliff player, ranked by each chance; ``week`` = 0 (before week 1).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from twm.modules.board.production import SHOWN
from twm.publish.collect import (
    ListData,
    PublishInputError,
    choose_lists,
    list_table,
    module_store_rows,
    version_rows,
)
from twm.publish.stream_lists import Collected

MODULE = "board"
LIST_KEY = ("season", "week", "snapshot", "kind")
FIRST_LIVE_SEASON = 2026  # the first board scored by approved models
SNAPSHOT = "preseason"
TRACK_TABLE, DISAGREE_TABLE = "board_track_record", "board_disagreement"
INT_SHOWN = ("prior_seasons", "games_s", "pos_rank_s", "depth_rank_s1")
BOOL_SHOWN = ("team_change_s1", "dc_absent", "hc_change_s1")
LIST_SCHEMA: dict[str, Any] = {
    "season": pl.Int32, "week": pl.Int32, "snapshot": pl.String, "kind": pl.String,
    "as_of": pl.Datetime("us", "UTC"), "model_version": pl.String, "missed_version": pl.String,
    "generated_at": pl.Datetime("us", "UTC"), "incomplete": pl.Boolean, "n_players": pl.Int32,
    "note": pl.String,
}  # fmt: skip
ROW_SCHEMA: dict[str, Any] = {
    "season": pl.Int32, "week": pl.Int32, "snapshot": pl.String, "kind": pl.String,
    "gsis_id": pl.String, "team": pl.String, "position": pl.String,
    "as_of": pl.Datetime("us", "UTC"), "cliff_rank": pl.Int32, "cliff_probability": pl.Float64,
    "missed_rank": pl.Int32, "missed_probability": pl.Float64, "ecr_rank": pl.Int32,
    **{c: (pl.Int32 if c in INT_SHOWN else pl.Boolean if c in BOOL_SHOWN else pl.Float64)
       for c in SHOWN},
    "cliff_drivers": pl.String, "missed_drivers": pl.String,
}  # fmt: skip
OUTCOME_SCHEMA: dict[str, Any] = {
    "season": pl.Int32, "week": pl.Int32, "gsis_id": pl.String, "games_s1": pl.Int32,
    "ppg_s1": pl.Float64, "y_cliff": pl.Boolean, "y_missed": pl.Boolean,
    "label_status": pl.String,
}  # fmt: skip
# the common layout of a board's rows before publishing (ROW_SCHEMA + list bookkeeping; ``note``:
# the live board's list note, step I6b, NULL elsewhere)
WIDE = (*ROW_SCHEMA, "model_version", "missed_version", "created_at", "incomplete", "is_current",
        "note")  # fmt: skip


def _shown() -> list[pl.Expr]:
    return [pl.col(c).cast(ROW_SCHEMA[c]) for c in SHOWN]


def frozen_lists(season: int, created: datetime) -> tuple[pl.DataFrame, dict[str, pl.DataFrame]]:
    """(rows in the common layout :data:`WIDE`, the snapshot) of the approved models' frozen
    backtest boards before ``season`` and, when the pin froze it, the board of ``season``
    (reconstructed: its as-of had passed at approval); sha256 checked before anything is read.
    The snapshot also holds ``current_board`` (when pinned) and ``live_versions`` (the pinned
    models' version rows, created at the approval date)."""
    from twm import pins
    from twm.modules.board import production as bp

    try:
        models, _, pin = bp.load_pinned(season)
        snap = bp.load_snapshot(pin)
        current = bp.load_current(pin)
    except pins.PinError as e:
        raise PublishInputError(f"the board's frozen backtest cannot be read: {e}") from e
    p = snap["predictions"].filter(pl.col("season") < season)
    if current is not None:
        p = pl.concat([p, current["current_board"].select(p.columns)], how="vertical_relaxed")
        snap = {**snap, **current}
    approved = datetime.fromisoformat(pin.approved) if pin.approved else created
    snap["live_versions"] = pl.concat(
        [bp.rprod.version_frame(pm, created_at=approved) for pm in models.values()])  # fmt: skip
    rows = p.select(
        pl.col("season").cast(pl.Int32), pl.lit(bp.WEEK, dtype=pl.Int32).alias("week"),
        pl.lit(SNAPSHOT).alias("snapshot"), pl.lit("backtest").alias("kind"), "gsis_id", "team",
        "position", pl.col("as_of").cast(pl.Datetime("us")),
        pl.col("cliff_rank").cast(pl.Int32), pl.col("cliff_prob").alias("cliff_probability"),
        pl.col("missed_rank").cast(pl.Int32), pl.col("missed_prob").alias("missed_probability"),
        pl.col("ecr_rank").cast(pl.Int32), *_shown(),
        pl.col("cliff_drivers_json").alias("cliff_drivers"),
        pl.col("missed_drivers_json").alias("missed_drivers"),
        pl.col("cliff_version").alias("model_version"), "missed_version",
        pl.lit(created, dtype=pl.Datetime("us")).alias("created_at"),
        pl.lit(False).alias("incomplete"), pl.lit(True).alias("is_current"),
        pl.lit(None, dtype=pl.String).alias("note"),
    )  # fmt: skip
    return rows, snap


def _role_rows(df: pl.DataFrame, role: str) -> pl.DataFrame:
    part = df.filter(pl.col("position") == role).with_columns(pl.lit(SNAPSHOT).alias("snapshot"))
    return choose_lists(part, LIST_KEY)


def store_rows(store: Path, season: int) -> pl.DataFrame:
    """The store's boards a publish may carry (every live row, every row of ``season``) in the
    common layout: the 'cliff' and 'missed' rows of each player joined (``position`` of the
    store's rows = its rank_group); :class:`PublishInputError` when a board lacks a role."""
    df = module_store_rows(store, MODULE, season)
    key = ["season", "week", "kind", "entity_id"]
    cliff, missed = _role_rows(df, "cliff"), _role_rows(df, "missed")
    both = cliff.join(missed.select(*key, pl.col("rank").alias("missed_rank"),
                                    pl.col("score").alias("missed_probability"),
                                    pl.col("model_version").alias("missed_version"),
                                    pl.col("reasons_json").alias("missed_reasons")),
                      on=key, how="full", coalesce=True)  # fmt: skip
    if both.filter(pl.col("model_version").is_null() | pl.col("missed_version").is_null()).height:
        raise PublishInputError("a stored board has players with only one of its two chances")
    recs = [json.loads(r) for r in both.get_column("reasons_json").to_list()]
    miss = [json.loads(r) for r in both.get_column("missed_reasons").to_list()]
    return both.select(
        "season", "week", pl.lit(SNAPSHOT).alias("snapshot"), "kind",
        pl.col("entity_id").alias("gsis_id"),
        pl.Series("team", [r["team"] for r in recs], dtype=pl.String),
        pl.Series("position", [r["position"] for r in recs], dtype=pl.String),
        pl.col("as_of").cast(pl.Datetime("us")), pl.col("rank").cast(pl.Int32).alias("cliff_rank"),
        pl.col("score").alias("cliff_probability"), pl.col("missed_rank").cast(pl.Int32),
        "missed_probability",
        pl.Series("ecr_rank", [r["ecr_rank"] for r in recs], dtype=pl.Int32),
        *[pl.Series(c, [r["features"].get(c) for r in recs]).cast(ROW_SCHEMA[c]) for c in SHOWN],
        pl.Series("cliff_drivers", [json.dumps(r["drivers"]) for r in recs], dtype=pl.String),
        pl.Series("missed_drivers", [json.dumps(r["drivers"]) for r in miss], dtype=pl.String),
        "model_version", "missed_version", "created_at", "incomplete", "is_current",
        pl.Series("note", [r.get("note") for r in recs], dtype=pl.String),
    )  # fmt: skip


def board_lists(rows: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(lists, rows) from the chosen rows (common layout): one list per (season, week,
    snapshot, kind) with every Cliff player, its two model versions."""
    if rows.height == 0:
        return pl.DataFrame(schema=LIST_SCHEMA), pl.DataFrame(schema=ROW_SCHEMA)
    lists = list_table(rows, LIST_KEY, pool="n_players").join(
        rows.group_by(list(LIST_KEY)).agg(pl.col("missed_version").first(),
                                          pl.col("note").drop_nulls().first()),
        on=list(LIST_KEY),
    )  # fmt: skip
    out = rows.with_columns(pl.col("as_of").dt.replace_time_zone("UTC"))
    lists = lists.select(list(LIST_SCHEMA)).cast(LIST_SCHEMA)  # type: ignore[arg-type]
    out = out.select(list(ROW_SCHEMA)).cast(ROW_SCHEMA)  # type: ignore[arg-type]
    return (lists.sort("season", "week", "snapshot", "kind"),
            out.sort("season", "week", "snapshot", "kind", "cliff_rank"))  # fmt: skip


def frozen_outcomes(snap: dict[str, pl.DataFrame]) -> pl.DataFrame:
    """The frozen backtest's labels as board_outcome publishes them ('final')."""
    from twm.modules.board.production import WEEK

    return snap["outcomes"].select(
        "season", pl.lit(WEEK).alias("week"), "gsis_id", "games_s1", "ppg_s1", "y_cliff",
        "y_missed", pl.lit("final").alias("label_status"),
    ).cast(OUTCOME_SCHEMA)  # type: ignore[arg-type]  # fmt: skip


def pending_outcomes(con: Any, season: int) -> pl.DataFrame:
    """'pending' outcome rows of the live seasons' boards (week 0) for every player of the
    warehouse's dim_player: live labels come after the season, not from this step."""
    from twm.modules.board.production import WEEK

    ids = con.execute("SELECT DISTINCT gsis_id FROM dim_player WHERE gsis_id IS NOT NULL"
                      ).pl().get_column("gsis_id").to_list()  # fmt: skip
    seasons = list(range(FIRST_LIVE_SEASON, int(season) + 1))
    grid = pl.DataFrame({"season": seasons}, schema={"season": pl.Int32}).join(
        pl.DataFrame({"gsis_id": sorted(ids)}, schema={"gsis_id": pl.String}), how="cross"
    )
    nul = {c: pl.lit(None, dtype=t) for c, t in OUTCOME_SCHEMA.items()
           if c in ("games_s1", "ppg_s1", "y_cliff", "y_missed")}  # fmt: skip
    return grid.with_columns(pl.lit(WEEK).alias("week"), *[e.alias(c) for c, e in nul.items()],
                             pl.lit("pending").alias("label_status")).select(
        list(OUTCOME_SCHEMA)).cast(OUTCOME_SCHEMA)  # type: ignore[arg-type]  # fmt: skip


def track_table(reports: Path, prefix: str) -> pl.DataFrame:
    """reports/board/<prefix>cliff.csv then <prefix>breakout.csv row for row (``line`` from 1
    across both; ``research``: the Breakout rows, not on the site)."""
    from twm.publish.tables import TABLES

    parts = []
    for pop in ("cliff", "breakout"):
        path = reports / f"{prefix}{pop}.csv"
        if not path.exists():
            raise PublishInputError(f"report not found: {path}")
        df = pl.read_csv(path, infer_schema_length=0)
        parts.append(df.with_columns(pl.lit(pop).alias("population"),
                                     pl.lit(pop == "breakout").alias("research")))  # fmt: skip
    df = pl.concat(parts, how="vertical")
    exprs = []
    for c, typ in TABLES[TRACK_TABLE].columns:
        if c == "line":
            exprs.append(pl.int_range(1, pl.len() + 1, dtype=pl.Int32).alias("line"))
        elif typ == "double precision":
            exprs.append(pl.col(c).cast(pl.Float64))
        elif typ == "boolean":
            exprs.append(pl.col(c).cast(pl.Boolean))
        else:
            exprs.append(pl.col(c).cast(pl.String))
    return df.select(exprs)


def collect_board(store: Path, reports: Path, season: int, now: datetime, con: Any) -> Collected:
    """The board's part of a publish: lists, outcomes, the frozen backtest's versions, the track
    record (``reports``: reports/board, the pinned anchor's files) and the disagreement table
    (module docstring); ``con``: the warehouse, read-only (dim_player)."""
    from twm.modules.board import preseason as pre
    from twm.modules.board import production as bp

    created = now.astimezone(UTC).replace(tzinfo=None)
    back, snap = frozen_lists(season, created)
    live = store_rows(store, season)
    if "current_board" in snap:  # the pin's frozen board replaces the store's reconstruction
        pinned = snap["current_board"]["season"].unique().to_list()
        live = live.filter(~((pl.col("kind") == "backtest") & pl.col("season").is_in(pinned)))
    rows = choose_lists(pl.concat([live.select(WIDE), back.select(WIDE)], how="vertical_relaxed"),
                        LIST_KEY)  # fmt: skip
    lists, out = board_lists(rows)
    outcomes = pl.concat([frozen_outcomes(snap), pending_outcomes(con, season)])
    data = ListData(
        lists, out,
        outcomes.unique(["season", "week", "gsis_id"], keep="first").sort(
            "season", "week", "gsis_id"),
        out.select("season", "week", "gsis_id").unique(),
    )  # fmt: skip
    track = track_table(reports, pre.REPORT_PREFIX[pre.default_anchor()])
    disagree = bp.disagreement_table(snap)
    at = now.astimezone(UTC)
    versions = pl.concat([version_rows(snap[k], created_at=at)
                          for k in ("model_versions", "live_versions")])  # fmt: skip
    return Collected(data, versions, track, {DISAGREE_TABLE: disagree})
