"""Regression Watch's lists as ``twm publish`` writes them (step P2). Reads only.

Sources (opened read-only), with the Radar's rules (:mod:`twm.publish.collect`):

- **live lists** and the current season's reconstructed lists: the predictions store
  (``module = 'regression_watch'``), one version per (season, week, kind), the latest written;
- **backtest lists** (the time machine: the headline as-of weeks 4, 6, 8 and 10 of 2011 ..
  the season before the approved one): the FROZEN snapshot pinned with the approved parameters
  (:mod:`twm.modules.regression_watch.frozen`; every file's sha256 checked before it is read),
  with each row's team and rest-of-season outcome. Never recomputed here: the snapshot was
  made on the owner's Mac from the full warehouse and ``twm model check`` checks it against
  ``reports/regression_watch/backtest.csv``;
- **team** of a live list's player: his latest game public at the list's as-of (D1 frame);
- **outcomes** of the live lists: each player's rest-of-season points per game and games after
  the list's as-of, 'final' once the season's regular season is over, else 'pending' (NULL
  numbers); for every week of every season since :data:`FIRST_LIVE_SEASON`, so a frozen live
  list that is no longer in the local store (a fresh runner) still gets its outcomes;
- **track record**: ``reports/regression_watch/backtest.csv``, row for row;
- **stability study** (step R1, owner's decision of 2026-09-30: the methodology page shows the
  real numbers): ``reports/regression_watch/stability.csv`` (D2), row for row
  (:func:`stability_rows`);
- **tags**: the product's Sell-high and Buy-low only; the frozen backtest lists still carry D3's
  third tag, Legit, which is stripped here (:func:`shown_tags`; owner, 2026-09-30). Live lists
  published before that keep their rows (frozen): the site hides 'legit';
- **player_week_summary**'s xFP and FPOE, with and without garbage time: the own walk-forward
  xFP (step PXFP, :func:`own_player_weeks`: the pin's frozen history + the pinned season
  scored live with the pinned models).

The warehouse needs only the seasons the job builds (2012 on): the D1 frame is read for the
live seasons only (since step PXFP the player summaries' history comes from the pin).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from twm.modules.regression_watch.frozen import no_negative_zero
from twm.modules.regression_watch.weekly import BAND_ORDER, DROPPED_TAG
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

MODULE = "regression_watch"
STABILITY_TABLE = "regression_stability"
STABILITY_RENAME = {"table": "section", "window": "seasons"}  # SQL keywords -> column names
LIST_KEY = ("season", "week", "kind")
FIRST_LIVE_SEASON = 2026  # the first season with live lists (the project went live)
LIST_SCHEMA: dict[str, Any] = {
    "season": pl.Int32, "week": pl.Int32, "kind": pl.String, "as_of": pl.Datetime("us", "UTC"),
    "params_version": pl.String, "generated_at": pl.Datetime("us", "UTC"),
    "incomplete": pl.Boolean, "n_universe": pl.Int32, "note": pl.String,
}  # fmt: skip
ROW_SCHEMA: dict[str, Any] = {
    "season": pl.Int32, "week": pl.Int32, "kind": pl.String, "gsis_id": pl.String,
    "position": pl.String, "team": pl.String, "games": pl.Int32, "ppg": pl.Float64,
    "ppg_ng": pl.Float64, "xfp_pg": pl.Float64, "xfp_pg_ng": pl.Float64, "fpoe_pg": pl.Float64,
    "fpoe_pg_ng": pl.Float64, "projection": pl.Float64, "shrinkage": pl.Float64,
    "tag": pl.String, "tags": pl.List(pl.String), "tag_reason": pl.String,
}  # fmt: skip
OUTCOME_SCHEMA: dict[str, Any] = {
    "season": pl.Int32, "week": pl.Int32, "gsis_id": pl.String, "ros_ppg": pl.Float64,
    "ros_games": pl.Int32, "label_status": pl.String,
}  # fmt: skip


def history(db: Path, first: int, season: int) -> pl.DataFrame:
    """The D1 frame of ``first`` .. ``season`` (every row with its ``available_at``)."""
    from twm.modules.regression_watch.player_week import player_games_history

    return player_games_history(db, list(range(int(first), int(season) + 1)))


def frozen_lists(season: int, created: datetime) -> tuple[pl.DataFrame, dict[str, pl.DataFrame]]:
    """(rows in the store's layout, the snapshot) of the approved parameters' frozen backtest
    lists before ``season``; sha256 checked before anything is read."""
    from twm import pins
    from twm.modules.regression_watch import frozen as fz
    from twm.modules.regression_watch import production as rprod

    try:
        _, pin = rprod.load_pinned_params(season)
        snap = fz.load_snapshot(pin)
    except pins.PinError as e:
        raise PublishInputError(f"Regression Watch's frozen backtest cannot be read: {e}") from e
    rows = snap["predictions"].filter(pl.col("season") < season).select(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32),
        pl.col("rank_group").alias("position"), pl.lit("backtest").alias("kind"),
        pl.col("rank").cast(pl.Int32), "entity_id", pl.lit("player").alias("entity_type"),
        pl.col("score").cast(pl.Float64), pl.lit(None, dtype=pl.Float64).alias("raw_score"),
        "band", "reasons_json", pl.lit(None, dtype=pl.String).alias("tier"),
        pl.lit(False).alias("incomplete"), pl.col("as_of").cast(pl.Datetime("us")),
        pl.lit(created, dtype=pl.Datetime("us")).alias("created_at"), "model_version",
        pl.lit(True).alias("is_current"),
    )  # fmt: skip
    return rows, snap


def live_outcomes(
    db: Path, frame: pl.DataFrame, keys: pl.DataFrame, season: int, now: datetime
) -> pl.DataFrame:
    """Rest-of-season PPG and games after each (season, week, as_of) of ``keys`` and every week
    of the seasons since :data:`FIRST_LIVE_SEASON` whose as-of has passed, for every player
    with a game public then: 'final' (the data public at ``now``) once the regular season is
    over, else 'pending' with NULL numbers."""
    from twm.modules.regression_watch import backtest as bt
    from twm.modules.regression_watch import frozen as fz
    from twm.modules.regression_watch.player_week import visible
    from twm.modules.regression_watch.weekly import season_final

    weeks = bt.asof_table(db, list(range(FIRST_LIVE_SEASON, int(season) + 1)), range(1, 23))
    weeks = weeks.filter(pl.col("as_of") <= now.astimezone(UTC).replace(tzinfo=None))
    todo = pl.concat([k.select("season", "week", pl.col("as_of").cast(pl.Datetime("us")))
                      for k in (keys, weeks)]).unique(["season", "week"], keep="first")  # fmt: skip
    ros = fz.rest_of_season_at(visible(frame, now), todo)
    final = [s for s in ros.get_column("season").unique().to_list() if season_final(db, s, now)]
    done = pl.col("season").is_in(final)
    return fz.no_negative_zero(ros.select(
        "season", "week", "gsis_id", pl.when(done).then(pl.col("ros_ppg")).alias("ros_ppg"),
        pl.when(done).then(pl.col("ros_games")).alias("ros_games"),
        pl.when(done).then(pl.lit("final")).otherwise(pl.lit("pending")).alias("label_status"),
    ).cast(OUTCOME_SCHEMA).sort("season", "week", "gsis_id"))  # type: ignore[arg-type]  # fmt: skip


def shown_tags(tags: list[str] | None) -> list[str]:
    """A row's tags as published: the product's (Sell-high, Buy-low), in band order; Legit, which
    the frozen backtest lists still carry, is stripped (owner, 2026-09-30)."""
    return [t for t in BAND_ORDER if t in (tags or [])]


def tag_reason(r: dict[str, Any]) -> str | None:
    """Why the player carries each of his shown tags, in plain English (None without one)."""
    texts = []
    x = r.get("x") or {}
    for tag in shown_tags(r.get("tags")):
        if tag == "sell_high":
            texts.append(
                f"Sell-high: his {r['fpoe_pg']:+.1f} points over expected per game are in the top "
                f"10% of his position, and the projection ({r['projection']:.1f}) is "
                f"{-r['gap']:.1f} points per game below his {r['ppg']:.1f} PPG (the cutoff is "
                f"{x.get('sell_high', 0):g})."
            )
        elif tag == "buy_low":
            texts.append(
                f"Buy-low: his {r['fpoe_pg']:+.1f} points over expected per game are in the "
                f"bottom 10% of his position, and the projection ({r['projection']:.1f}) is "
                f"{r['gap']:.1f} points per game above his {r['ppg']:.1f} PPG (the cutoff is "
                f"{x.get('buy_low', 0):g})."
            )
    return " ".join(texts) or None


def regression_lists(rows: pl.DataFrame, teams: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(lists, rows) from the chosen rows (store layout): one list per (season, week, kind)
    with every universe player, his numbers read from the stored reasons; tags as
    :func:`shown_tags` (a row whose band was Legit has no tag)."""
    from twm.modules.regression_watch.weekly import early_note

    if rows.height == 0:
        return pl.DataFrame(schema=LIST_SCHEMA), pl.DataFrame(schema=ROW_SCHEMA)
    lists = list_table(rows, LIST_KEY, pool="n_universe", version="params_version")
    lists = lists.with_columns(
        pl.Series("note", [early_note(int(w)) for w in lists.get_column("week")], dtype=pl.String)
    )
    recs = [json.loads(r) for r in rows.get_column("reasons_json").to_list()]
    ng = [r.get("without_garbage_time") or {} for r in recs]
    reasons = [tag_reason(r) for r in recs]
    for r, s in zip(recs, rows.get_column("score").to_list(), strict=True):
        r.setdefault("projection", s)

    def num(key: str, src: list[dict[str, Any]] = recs) -> pl.Series:
        return pl.Series(key, [d.get(key) for d in src], dtype=pl.Float64)

    out = (
        rows.select(
            "season",
            "week",
            "kind",
            pl.col("entity_id").alias("gsis_id"),
            "position",
            pl.when(pl.col("band") != DROPPED_TAG).then(pl.col("band")).alias("tag"),
        )
        .with_columns(  # fmt: skip
            pl.Series("games", [int(r["games"]) for r in recs], dtype=pl.Int32),
            num("ppg"),
            num("ppg", ng).alias("ppg_ng"),
            num("xfp_pg"),
            num("xfp_pg", ng).alias("xfp_pg_ng"),
            num("fpoe_pg"),
            num("fpoe_pg", ng).alias("fpoe_pg_ng"),
            rows.get_column("score").cast(pl.Float64).alias("projection"),
            num("shrinkage"),
            pl.Series("tags", [shown_tags(r.get("tags")) for r in recs], dtype=pl.List(pl.String)),
            pl.Series("tag_reason", reasons, dtype=pl.String),
        )
        .join(teams, on=["season", "week", "gsis_id"], how="left")
    )
    lists = lists.select(list(LIST_SCHEMA)).cast(LIST_SCHEMA)  # type: ignore[arg-type]
    out = out.select(list(ROW_SCHEMA)).cast(ROW_SCHEMA)  # type: ignore[arg-type]
    return (lists.sort("season", "week", "kind"),
            no_negative_zero(out.sort("season", "week", "kind", "gsis_id")))  # fmt: skip


def stability_rows(path: Path) -> pl.DataFrame:
    """The D2 stability study (reports/regression_watch/stability.csv: split-half correlations
    and the shrinkage table r(g) of every season window) row for row, as regression_stability
    publishes it: ``line`` = the data row's number, every CSV column (``table`` -> ``section``,
    ``window`` -> ``seasons``)."""
    return csv_table(path, STABILITY_TABLE, STABILITY_RENAME)


def own_player_weeks(
    db: Path, season: int, *, path: Path | None = None, root: Path | None = None
) -> pl.DataFrame:
    """The player pages' xFP (step PXFP, owner's decision of 2026-10-02): Regression Watch's
    own walk-forward xFP per player-game (:data:`twm.modules.regression_watch.frozen.
    PLAYER_XFP_COLUMNS`). The seasons before the pinned one come from the pin's frozen
    history (sha256 checked; REQUIRED, never recomputed here, no ffopportunity fallback); the
    pinned season from the pinned live models through the weekly list's own function
    (:func:`~twm.modules.regression_watch.own_xfp.live_frame`, every built row; nothing is fit).
    """
    from twm import pins
    from twm.asof import AsOfView
    from twm.modules.regression_watch import frozen as fz
    from twm.modules.regression_watch import own_xfp as ox
    from twm.modules.regression_watch import production as rprod
    from twm.modules.regression_watch.player_week import END_OF_TIME

    try:
        live, _, pin = rprod.load_pinned_xfp(season, path=path, root=root)
        history = fz.load_player_xfp(pin, root)
    except pins.PinError as e:
        raise PublishInputError(f"the player pages' own xFP cannot be read: {e}") from e
    if live is None:
        raise PublishInputError(
            "the player pages show the own walk-forward xFP (step PXFP), but the approved "
            "Regression Watch parameters use ffopportunity's: approve own parameters"
        )
    with AsOfView(db, END_OF_TIME) as view:
        current = fz.player_xfp_rows(ox.live_frame(view, season, live), season)
    return pl.concat([history, current.select(history.columns)], how="vertical")


def ng_columns(frame: pl.DataFrame) -> pl.DataFrame:
    """(gsis_id, season, week, points_ng, xfp_ng, fpoe_ng) of every player-game (rounded like
    player_week_summary; a rounded -0.0 becomes 0.0, so two runs hash the same) for its
    garbage-time toggle."""
    return no_negative_zero(frame.select(
        "gsis_id", pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32),
        *[pl.col(c).round(2) for c in ("points_ng", "xfp_ng", "fpoe_ng")],
    ).unique(["gsis_id", "season", "week"], keep="first", maintain_order=True))  # fmt: skip


def collect_regression(
    store: Path, db: Path, csv_path: Path, season: int, now: datetime, frame: pl.DataFrame,
    stability_csv: Path,
) -> Collected:  # fmt: skip
    """Regression Watch's lists, outcomes, backtest versions, track record (``csv_path``) and
    stability study (``stability_csv``) (module docstring); ``frame``: :func:`history` (the D1
    frame of the live seasons at least)."""
    from twm.modules.regression_watch import frozen as fz

    created = now.astimezone(UTC).replace(tzinfo=None)
    live = module_store_rows(store, MODULE, season)
    back, snap = frozen_lists(season, created)
    rows = choose_lists(pl.concat([live, back.select(live.columns)], how="vertical_relaxed"),
                        LIST_KEY)  # fmt: skip
    mine = rows.join(back.select("season", "week").unique(), on=["season", "week"], how="anti")
    keys = mine.select("season", "week", "as_of").unique()
    frozen_team = snap["predictions"].select("season", "week", pl.col("entity_id").alias(
        "gsis_id"), "team")  # fmt: skip
    teams = pl.concat([fz.teams_at(frame, keys).select(frozen_team.columns), frozen_team])
    lists, out = regression_lists(rows, teams)
    frozen_out = snap["outcomes"].select(
        "season", "week", pl.col("entity_id").alias("gsis_id"), "ros_ppg", "ros_games",
        "label_status",
    ).cast(OUTCOME_SCHEMA)  # type: ignore[arg-type]  # fmt: skip
    outcomes = pl.concat([frozen_out, live_outcomes(db, frame, keys, season, now)])
    return Collected(
        ListData(
            lists,
            out,
            outcomes.unique(["season", "week", "gsis_id"], keep="first").sort(
                "season", "week", "gsis_id"
            ),
            out.select("season", "week", "gsis_id").unique(),
        ),  # fmt: skip
        version_rows(snap["model_versions"], created_at=now.astimezone(UTC)),
        csv_table(csv_path, "regression_track_record", {"table": "section", "group": "row_group"}),
        {STABILITY_TABLE: stability_rows(stability_csv)},
    )
