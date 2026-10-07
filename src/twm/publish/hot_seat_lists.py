"""The Hot-Seat Meter's lists as ``twm publish`` writes them (step H4a). Reads only.

Sources (opened read-only), with the other modules' rules (:mod:`twm.publish.collect`):

- **live lists** and the current season's reconstructed lists: the predictions store
  (``module = 'hot_seat'``), one version per (season, week, snapshot, kind), the latest written;
- **backtest lists** (the time machine: every weekly as-of and the end-of-season snapshot of
  2006 .. the season before the approved one): the FROZEN snapshot pinned with the approved
  model (:mod:`twm.modules.hot_seat.production`; every file's sha256 checked before it is
  read). Never recomputed here: ``twm model check hot_seat`` checks it against
  ``reports/hot_seat/backtest_metrics.csv`` and ``firings_per_season.csv``;
- **coaches**: the site's id is the Decision Report Card's slug of the coach's name
  (:func:`twm.modules.decisions.site.coach_slug`, so ``/coach/[id]`` joins both); the names come
  from the stored rows (the frozen snapshot's and the live rows' reasons), never the warehouse,
  and are upserted into ``dim_coach`` with the decisions' coaches;
- **outcomes**: the frozen snapshot's labels (``departed`` = a positive departure in the window,
  ``censored``, the departure and its announced day -- NULL when no source gives the day and
  the labels fell back to the team's last game date (``date_imputed``) -- 'final'); live rows
  are 'pending' (the labels come from the owner's departure file after the season), for every
  coach of the warehouse's ``dim_coach`` and every week of the live seasons, so a frozen live
  list no longer in the local store (a fresh runner) still gets its outcome row;
- **track record**: ``reports/hot_seat/backtest_metrics.csv`` (every variant, model and slice,
  with its interval) and ``firings_per_season.csv``, row for row.

Rows of a list are every scored coach, ranked by probability (interims ranked and flagged).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from twm.modules.hot_seat.production import SHOWN
from twm.publish.collect import (
    ListData,
    PublishInputError,
    choose_lists,
    csv_table,
    list_table,
    module_store_rows,
    version_rows,
)
from twm.publish.stream_lists import Collected

MODULE = "hot_seat"
LIST_KEY = ("season", "week", "snapshot", "kind")
FIRST_LIVE_SEASON = 2026  # the first season with live Hot-Seat lists
TRACK_TABLE, FIRINGS_TABLE = "hot_seat_track_record", "hot_seat_firings"
EOS_NOTE = "end of season: each team's row after its last regular-season game"
INT_SHOWN = ("reg_games_played", "tenure_seasons", "division_rank", "consecutive_losing_seasons")
LIST_SCHEMA: dict[str, Any] = {
    "season": pl.Int32, "week": pl.Int32, "snapshot": pl.String, "kind": pl.String,
    "as_of": pl.Datetime("us", "UTC"), "model_version": pl.String,
    "generated_at": pl.Datetime("us", "UTC"), "incomplete": pl.Boolean, "n_coaches": pl.Int32,
    "note": pl.String,
}  # fmt: skip
ROW_SCHEMA: dict[str, Any] = {
    "season": pl.Int32, "week": pl.Int32, "snapshot": pl.String, "kind": pl.String,
    "coach_id": pl.String, "team": pl.String, "as_of": pl.Datetime("us", "UTC"),
    "rank": pl.Int32, "probability": pl.Float64, "is_interim": pl.Boolean,
    "drivers": pl.String,
    **{c: (pl.Int32 if c in INT_SHOWN else pl.Float64) for c in SHOWN},
}  # fmt: skip
OUTCOME_SCHEMA: dict[str, Any] = {
    "season": pl.Int32, "week": pl.Int32, "coach_id": pl.String, "departed": pl.Boolean,
    "censored": pl.Boolean, "departure_type": pl.String, "announced": pl.Date,
    "label_status": pl.String,
}  # fmt: skip


def frozen_lists(season: int, created: datetime) -> tuple[pl.DataFrame, dict[str, pl.DataFrame]]:
    """(rows in the store's layout, the snapshot) of the approved model's frozen backtest
    lists before ``season``; sha256 checked before anything is read."""
    from twm import pins
    from twm.modules.hot_seat import production as hp
    from twm.modules.hot_seat.weekly import reasons

    try:
        _, pin = hp.load_pinned(season)
        snap = hp.load_snapshot(pin)
    except pins.PinError as e:
        raise PublishInputError(f"the Hot-Seat frozen backtest cannot be read: {e}") from e
    p = snap["predictions"].filter(pl.col("season") < season)
    rows = p.select(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32), "snapshot",
        pl.lit("backtest").alias("kind"), pl.col("rank").cast(pl.Int32),
        pl.col("coach_id").alias("entity_id"), pl.col("prob").alias("score"),
        pl.Series("reasons_json", [reasons(r) for r in p.to_dicts()], dtype=pl.String),
        pl.lit(False).alias("incomplete"),
        pl.col("as_of").dt.convert_time_zone("UTC").dt.replace_time_zone(None)
        .cast(pl.Datetime("us")),
        pl.lit(created, dtype=pl.Datetime("us")).alias("created_at"), "model_version",
        pl.lit(True).alias("is_current"),
    )  # fmt: skip
    return rows, snap


def store_rows(store: Path, season: int) -> pl.DataFrame:
    """The store's Hot-Seat rows a publish may carry (every live row, every row of ``season``)
    in the common layout (``snapshot`` = the stored ``rank_group``)."""
    df = module_store_rows(store, MODULE, season).rename({"position": "snapshot"})
    return df.select("season", "week", "snapshot", "kind", "rank", "entity_id", "score",
                     "reasons_json", "incomplete", "as_of", "created_at", "model_version",
                     "is_current")  # fmt: skip


def coach_table(recs: list[dict[str, Any]], ids: list[str]) -> pl.DataFrame:
    """(entity_id, coach_id, name): the site's slug of each warehouse coach id's name;
    :class:`PublishInputError` when a row has no name or two names share a slug."""
    from twm.modules.decisions import site

    pairs = sorted({(i, r.get("coach") or "") for i, r in zip(ids, recs, strict=True)})
    nameless = [i for i, n in pairs if not n]
    if nameless:
        raise PublishInputError(f"Hot-Seat rows name a coach without a name: {nameless[:3]}")
    try:
        slugs = site.coach_ids([n for _, n in pairs])
    except site.SiteError as e:
        raise PublishInputError(f"Hot-Seat coaches: {e}") from e
    by_name = dict(zip(slugs["name"], slugs["coach_id"], strict=True))
    return pl.DataFrame(
        {"entity_id": [i for i, _ in pairs], "coach_id": [by_name[n] for _, n in pairs],
         "name": [n for _, n in pairs]},
        schema={"entity_id": pl.String, "coach_id": pl.String, "name": pl.String},
    )  # fmt: skip


def hot_seat_lists(rows: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """(lists, rows, coaches) from the chosen rows (common layout): one list per (season,
    week, snapshot, kind) with every scored coach, his numbers read from the stored reasons;
    ``coaches``: (entity_id, coach_id, name) of every row."""
    if rows.height == 0:
        empty = pl.DataFrame(schema={"entity_id": pl.String, "coach_id": pl.String,
                                     "name": pl.String})  # fmt: skip
        return pl.DataFrame(schema=LIST_SCHEMA), pl.DataFrame(schema=ROW_SCHEMA), empty
    lists = list_table(rows, LIST_KEY, pool="n_coaches").with_columns(
        pl.when(pl.col("snapshot") == "end_of_season").then(pl.lit(EOS_NOTE)).alias("note")
    )
    recs = [json.loads(r) for r in rows.get_column("reasons_json").to_list()]
    ids = rows.get_column("entity_id").to_list()
    coaches = coach_table(recs, ids)
    feats = {c: [r["features"].get(c) for r in recs] for c in SHOWN}
    out = (
        rows.select(
            "season", "week", "snapshot", "kind", "entity_id", "rank",
            pl.col("score").alias("probability"),
            pl.col("as_of").dt.replace_time_zone("UTC"),
        )
        .with_columns(
            pl.Series("team", [r["team"] for r in recs], dtype=pl.String),
            pl.Series("is_interim", [bool(r["interim"]) for r in recs], dtype=pl.Boolean),
            pl.Series("drivers", [json.dumps(r["drivers"]) for r in recs], dtype=pl.String),
            *[pl.Series(c, v, dtype=pl.Float64) for c, v in feats.items()],
        )
        .join(coaches.select("entity_id", "coach_id"), on="entity_id", how="left")
    )  # fmt: skip
    lists = lists.select(list(LIST_SCHEMA)).cast(LIST_SCHEMA)  # type: ignore[arg-type]
    out = out.select(list(ROW_SCHEMA)).cast(ROW_SCHEMA)  # type: ignore[arg-type]
    return (lists.sort("season", "week", "snapshot", "kind"),
            out.sort("season", "week", "snapshot", "kind", "rank"), coaches)  # fmt: skip


def frozen_outcomes(snap: dict[str, pl.DataFrame], coaches: pl.DataFrame) -> pl.DataFrame:
    """The frozen backtest's labels as hot_seat_outcome publishes them ('final'). ``announced``
    is the announcement day only: NULL when the labels imputed it from the team's last game
    (no source gives the day), so the site never calls that date an announcement."""
    o = snap["outcomes"].join(coaches.select("entity_id", "coach_id").unique("entity_id"),
                              left_on="coach_id", right_on="entity_id", how="left",
                              suffix="_site")  # fmt: skip
    return o.select(
        "season", "week", pl.col("coach_id_site").alias("coach_id"),
        (pl.col("y") == 1).alias("departed"), "censored", "departure_type",
        pl.when(pl.col("date_imputed")).then(None).otherwise(pl.col("announced"))
        .alias("announced"),
        pl.lit("final").alias("label_status"),
    ).cast(OUTCOME_SCHEMA)  # type: ignore[arg-type]  # fmt: skip


def pending_outcomes(con: Any, season: int) -> pl.DataFrame:
    """'pending' outcome rows of every week (2 .. the last regular-season week) of the live
    seasons for every coach of the warehouse's dim_coach (by site id): live labels come from
    the owner's departure file after the season, not from this step."""
    from twm.modules.decisions.site import coach_slug

    names = con.execute("SELECT DISTINCT coach_name FROM dim_coach WHERE coach_name IS NOT NULL"
                        ).pl().get_column("coach_name").to_list()  # fmt: skip
    slugs = sorted({coach_slug(n) for n in names})
    weeks = con.execute(
        "SELECT season, week FROM dim_week WHERE season_type = 'REG' AND week >= 2 AND "
        "season BETWEEN ? AND ? ORDER BY season, week", [FIRST_LIVE_SEASON, int(season)]
    ).pl()  # fmt: skip
    grid = weeks.join(pl.DataFrame({"coach_id": slugs}, schema={"coach_id": pl.String}),
                      how="cross")  # fmt: skip
    return grid.with_columns(
        pl.lit(None, dtype=pl.Boolean).alias("departed"),
        pl.lit(None, dtype=pl.Boolean).alias("censored"),
        pl.lit(None, dtype=pl.String).alias("departure_type"),
        pl.lit(None, dtype=pl.Date).alias("announced"), pl.lit("pending").alias("label_status"),
    ).select(list(OUTCOME_SCHEMA)).cast(OUTCOME_SCHEMA)  # type: ignore[arg-type]  # fmt: skip


def collect_hot_seat(
    store: Path, reports: Path, season: int, now: datetime, con: Any
) -> tuple[Collected, pl.DataFrame]:
    """(the Hot-Seat part of a publish, its coaches as dim_coach rows): lists, outcomes,
    backtest versions, the track record (``reports``/backtest_metrics.csv) and firings per
    season (module docstring); ``con``: the warehouse, read-only (dim_coach, dim_week)."""
    created = now.astimezone(UTC).replace(tzinfo=None)
    live = store_rows(store, season)
    back, snap = frozen_lists(season, created)
    rows = choose_lists(pl.concat([live, back.select(live.columns)], how="vertical_relaxed"),
                        LIST_KEY)  # fmt: skip
    lists, out, coaches = hot_seat_lists(rows)
    outcomes = pl.concat([frozen_outcomes(snap, coaches), pending_outcomes(con, season)])
    data = ListData(
        lists, out,
        outcomes.unique(["season", "week", "coach_id"], keep="first").sort(
            "season", "week", "coach_id"),
        out.select("season", "week", "coach_id").unique(),
    )  # fmt: skip
    track = csv_table(reports / "backtest_metrics.csv", TRACK_TABLE)
    firings = csv_table(reports / "firings_per_season.csv", FIRINGS_TABLE)
    dim = coaches.select("coach_id", "name").unique().sort("coach_id")
    got = Collected(data, version_rows(snap["model_versions"], created_at=now.astimezone(UTC)),
                    track, {FIRINGS_TABLE: firings})  # fmt: skip
    return got, dim
