"""The K and D/ST streamer's lists as ``twm publish`` writes them (step P2). Reads only.

Sources (opened read-only), with the Radar's rules (:mod:`twm.publish.collect`):

- **live lists** and the current season's reconstructed lists: the predictions store
  (``module = 'streamer'``), one version per (season, week, position, kind), the latest written;
- **backtest lists** (2013-2025, the time machine): the approved methods' frozen backtest,
  read in place from the pins (``config/production_models.yaml``, every file's sha256 checked,
  never restored into a store). K: the K model's snapshot predictions. D/ST: the rule is
  applied again to the streamer dataset's graded rows (the backtest's own code), and the
  result must reproduce the pinned hit-rate table exactly, else nothing is published. The
  chance, its range and the priority of a backtest pick are computed point in time, from the
  seasons before the list's (the weekly list's code, :mod:`twm.modules.streamer.confidence`);
  reasons stay empty (the backtest never made them);
- **teams, names, home and outcomes**: the streamer dataset (``data/streamer/dataset.parquet``,
  the team at the as-of, next week's home flag, the label ``y_start`` and next week's points);
  the next opponent from the warehouse's schedule (week + 1);
- **track record**: ``reports/streamer/backtest.csv``, row for row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

from twm.publish.collect import (
    ListData,
    PublishInputError,
    _band,
    _reasons,
    choose_lists,
    csv_table,
    module_store_rows,
    version_rows,
)

MODULE = "streamer"
POSITIONS = ("K", "DST")
ENTITY_TYPES = {"K": "kicker", "DST": "team_defense"}
ENTITY_PATTERN = re.compile(r"^(00-\d{7}|[A-Z]{3}\d{6}|DST-[A-Z]{2,3})$")
LIST_KEY = ("season", "week", "position", "kind")
DATASET_COLUMNS = ("season", "week", "position", "entity_id", "name", "team", "next_is_home",
                   "in_pool", "label_status", "y_start", "next_points")  # fmt: skip
LIST_SCHEMA: dict[str, Any] = {
    "season": pl.Int32, "week": pl.Int32, "position": pl.String, "kind": pl.String,
    "as_of": pl.Datetime("us", "UTC"), "model_version": pl.String,
    "generated_at": pl.Datetime("us", "UTC"), "incomplete": pl.Boolean, "n_pool": pl.Int32,
    "note": pl.String,
}  # fmt: skip
PICK_SCHEMA: dict[str, Any] = {
    "season": pl.Int32, "week": pl.Int32, "position": pl.String, "kind": pl.String,
    "rank": pl.Int32, "entity_id": pl.String, "entity_type": pl.String,
    "display_name": pl.String, "team": pl.String, "next_opponent": pl.String,
    "home": pl.Boolean, "chance": pl.Float64, "chance_low": pl.Float64,
    "chance_high": pl.Float64, "model_prob": pl.Float64, "tier": pl.String,
    "reasons": pl.List(pl.String),
}  # fmt: skip
OUTCOME_SCHEMA: dict[str, Any] = {
    "season": pl.Int32, "week": pl.Int32, "entity_id": pl.String, "y_start": pl.Boolean,
    "label_status": pl.String, "points_next_week": pl.Float64,
}  # fmt: skip


def read_dataset(path: Path) -> pl.DataFrame:
    if not path.exists():
        raise PublishInputError(
            f"streamer dataset not found: {path}; run `uv run twm streamer dataset`"
        )
    df = pl.read_parquet(path)  # every column: the D/ST rule reads its features
    missing = [c for c in DATASET_COLUMNS if c not in df.columns]
    if missing:
        raise PublishInputError(f"the streamer dataset {path} lacks {missing}: rebuild it")
    return df.with_columns(pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32))


def _store_layout(df: pl.DataFrame, created: Any) -> pl.DataFrame:
    """Backtest predictions -> the columns :func:`module_store_rows` gives."""
    return df.select(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32), "position",
        pl.lit("backtest").alias("kind"), pl.col("rank").cast(pl.Int32), "entity_id",
        pl.col("position").replace_strict(ENTITY_TYPES).alias("entity_type"),
        pl.col("score").cast(pl.Float64), pl.col("raw_score").cast(pl.Float64),
        pl.lit(None, dtype=pl.String).alias("band"), pl.lit("[]").alias("reasons_json"),
        pl.lit(None, dtype=pl.String).alias("tier"), pl.lit(False).alias("incomplete"),
        pl.col("as_of").cast(pl.Datetime("us")),
        pl.lit(created, dtype=pl.Datetime("us")).alias("created_at"), "model_version",
        pl.lit(True).alias("is_current"),
    )  # fmt: skip


def backtest_rows(
    dataset: pl.DataFrame, season: int, *, created: Any, root: Path | None = None
) -> tuple[pl.DataFrame, pl.DataFrame, dict[str, Any]]:
    """(rows, versions, frozen): the approved methods' backtest lists of the seasons before
    ``season`` in the store's layout, their model versions (store layout) and the frozen
    tables the chances come from ({'k': the K snapshot, 'hit_rates': the D/ST table})."""
    from twm import pins
    from twm.modules.streamer import backtest as bt
    from twm.modules.streamer import models as sm
    from twm.modules.streamer import production as sp

    try:
        _, kpin = sp.load_pinned_k(season, root=root)
        _, dpin = sp.load_pinned_rule(season, root=root)
        k = sp.load_k_snapshot(kpin, root)
        hit_rates = sp.load_hit_rates(dpin, root)
    except (pins.PinError, sp.StreamerProductionError) as e:
        raise PublishInputError(f"the streamer's approved backtest cannot be read: {e}") from e
    kp = k["predictions"].filter(pl.col("season") < season)
    labels = dataset.filter(pl.col("label_status") == "final").select(
        "season", "week", "entity_id", pl.col("y_start").alias("_d")
    )
    stale = (
        k["outcomes"]
        .join(labels, on=["season", "week", "entity_id"], how="inner")
        .filter(pl.col("y_start") != pl.col("_d"))
    )
    if stale.height:
        raise PublishInputError(
            f"{stale.height} K backtest picks have another final label in the streamer dataset "
            "than in the approved snapshot: rebuild the dataset (`uv run twm streamer dataset`)"
        )
    seasons = sorted(int(s) for s in hit_rates.get_column("season").unique().to_list())
    graded = sm.graded_rows(dataset).filter(pl.col("season").is_in(seasons))
    run = bt.run_baseline(graded, sp.RULE_METHOD, "DST")
    ranked = sp.with_labels(run.predictions.with_columns(pl.col("position").alias("rank_group")),
                            dataset)  # fmt: skip
    if not sp.hit_rate_table(ranked).equals(hit_rates):
        raise PublishInputError(
            "the D/ST rule applied to the streamer dataset does not reproduce the approved "
            "hit-rate table (the dataset changed since the rule was approved): rebuild the "
            "dataset, or approve the rule again (`uv run twm streamer pin`)"
        )
    keys = ["season", "week", "position", "entity_id"]
    dst = run.predictions.join(graded.select(*keys, "as_of"), on=keys, how="left")
    rows = pl.concat([
        _store_layout(kp.rename({"rank_group": "position"}), created),
        _store_layout(dst.filter(pl.col("season") < season), created),
    ], how="vertical")  # fmt: skip
    versions = pl.concat([
        version_rows(k["model_versions"]),
        version_rows(pl.DataFrame(run.versions), created_at=created),
    ], how="vertical_relaxed")  # fmt: skip
    return rows, versions, {"k": k, "hit_rates": hit_rates}


def backtest_bands(rows: pl.DataFrame, frozen: dict[str, Any]) -> pl.DataFrame:
    """``band`` (the store's JSON) and ``tier`` of every backtest row without one, point in time:
    a list of season S reads only the frozen backtest seasons before S (K: the snapshot's
    calibrated-probability bins; D/ST: the hit-rate table's rank bins), exactly as the weekly
    list does. The first backtest season (no earlier one) keeps NULL."""
    from twm.modules.streamer import confidence as sc
    from twm.modules.waiver_radar import confidence as cf

    todo = rows.filter((pl.col("kind") == "backtest") & pl.col("band").is_null())
    if todo.height == 0:
        return rows
    key = [*LIST_KEY, "rank"]
    parts = []
    for (season, position), sub in todo.group_by(["season", "position"], maintain_order=True):
        try:
            if position == "K":
                conf = sc.k_confidence(frozen["k"], int(season))  # type: ignore[arg-type]
                value, text = sub.get_column("score").to_numpy(), cf.band_json
            else:
                conf = sc.dst_confidence(frozen["hit_rates"], int(season))  # type: ignore[arg-type]
                value, text = -sub.get_column("rank").cast(pl.Float64).to_numpy(), sc.dst_band_json
        except ValueError:  # no earlier backtest season: stays NULL
            continue
        bands = [text(r) for r in conf.band(value).iter_rows(named=True)]
        parts.append(sub.select(key).with_columns(
            pl.Series("_band", bands, dtype=pl.String),
            pl.Series("_tier", [cf.tier_of(_band(b)[0]) for b in bands], dtype=pl.String),
        ))  # fmt: skip
    if not parts:
        return rows
    filled = rows.join(pl.concat(parts), on=key, how="left")
    return filled.with_columns(
        pl.coalesce("band", "_band").alias("band"), pl.coalesce("tier", "_tier").alias("tier")
    ).drop("_band", "_tier")


def next_games(con: Any, seasons: list[int]) -> pl.DataFrame:
    """(season, week, team, next_opponent): each team's regular-season game of week + 1."""
    marks = ", ".join(str(int(s)) for s in sorted(set(seasons))) or "NULL"
    return (
        con.execute(
            f"""
        SELECT CAST(season AS INTEGER) AS season, CAST(week - 1 AS INTEGER) AS week,
               home_team AS team, away_team AS next_opponent
        FROM fact_game WHERE season_type = 'REG' AND season IN ({marks})
        UNION ALL
        SELECT CAST(season AS INTEGER), CAST(week - 1 AS INTEGER), away_team, home_team
        FROM fact_game WHERE season_type = 'REG' AND season IN ({marks})
        """
        )
        .pl()
        .unique(["season", "week", "team"], keep="first", maintain_order=True)
    )


def stream_lists(
    rows: pl.DataFrame,
    dataset: pl.DataFrame,
    nxt: pl.DataFrame,
    notes_by_season: dict[int, dict[str, str]],
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(lists, picks) from the chosen rows (store layout): one list per (season, week,
    position, kind) with every pick (a streamer list is the whole pool, at most ~30 rows, unlike
    the Radar's top 25); name, team and home from the dataset, the next opponent from the
    schedule."""
    from twm.publish.collect import list_table

    if rows.height == 0:
        return pl.DataFrame(schema=LIST_SCHEMA), pl.DataFrame(schema=PICK_SCHEMA)
    lists = list_table(rows, LIST_KEY)
    pairs = zip(lists.get_column("season"), lists.get_column("position"), strict=True)
    notes = [notes_by_season.get(int(s), {}).get(p) for s, p in pairs]
    lists = lists.with_columns(pl.Series("note", notes, dtype=pl.String))
    top = rows  # every pick: a streamer list is the whole (short) pool
    bands = [_band(b) for b in top.get_column("band").to_list()]
    info = dataset.select("season", "week", "position", "entity_id", pl.col("name").alias(
        "display_name"), "team", pl.col("next_is_home").alias("home")).unique(
        ["season", "week", "position", "entity_id"], keep="first", maintain_order=True)  # fmt: skip
    picks = (
        top.with_columns(
            pl.Series("chance", [b[0] for b in bands], dtype=pl.Float64),
            pl.Series("chance_low", [b[1] for b in bands], dtype=pl.Float64),
            pl.Series("chance_high", [b[2] for b in bands], dtype=pl.Float64),
            pl.Series(
                "reasons",
                [_reasons(r) for r in top.get_column("reasons_json").to_list()],
                dtype=pl.List(pl.String),
            ),  # fmt: skip
            pl.when(pl.col("position") == "K").then(pl.col("score")).alias("model_prob"),
        )
        .join(info, on=["season", "week", "position", "entity_id"], how="left")
        .join(nxt, on=["season", "week", "team"], how="left")
        .select(list(PICK_SCHEMA))
        .cast(PICK_SCHEMA)  # type: ignore[arg-type]
    )
    order = pl.col("position").replace_strict({p: i for i, p in enumerate(POSITIONS)}, default=9)
    lists = lists.select(list(LIST_SCHEMA)).cast(LIST_SCHEMA)  # type: ignore[arg-type]
    return (lists.sort("season", "week", order, "kind"),
            picks.sort("season", "week", order, "kind", "rank"))  # fmt: skip


def outcome_rows(dataset: pl.DataFrame) -> pl.DataFrame:
    """Every dataset row's outcome: y_start, its status and next week's fantasy points."""
    return dataset.select(
        "season", "week", "entity_id", pl.col("y_start").cast(pl.Boolean), "label_status",
        pl.col("next_points").cast(pl.Float64).alias("points_next_week"),
    ).unique(["season", "week", "entity_id"], keep="first", maintain_order=True)  # fmt: skip


@dataclass
class Collected:
    """A module's part of a publish: its lists, the model versions its backtest lists need
    (not in the predictions store) and its track record."""

    data: ListData
    versions: pl.DataFrame  # published layout (collect.version_rows)
    track_record: pl.DataFrame


def collect_streamer(
    store: Path, dataset_path: Path, csv_path: Path, season: int, con: Any, created: Any
) -> Collected:
    """The streamer's lists, outcomes, backtest versions and track record (module docstring);
    ``con``: the warehouse (read-only), ``created``: when the backtest lists count as written
    (naive UTC)."""
    from twm.modules.streamer import weekly as sw

    dataset = read_dataset(dataset_path)
    live = module_store_rows(store, MODULE, season)
    back, versions, frozen = backtest_rows(dataset, season, created=created)
    rows = choose_lists(pl.concat([live, back], how="vertical_relaxed"), LIST_KEY)
    rows = backtest_bands(rows, frozen)
    seasons = sorted(int(s) for s in rows.get_column("season").unique().to_list())
    notes = {s: sw.method_notes(csv_path, s) for s in seasons}
    lists, picks = stream_lists(rows, dataset, next_games(con, seasons), notes)
    keys = pl.concat([
        picks.select("season", "week", "entity_id"),
        dataset.filter((pl.col("season") == season) & pl.col("in_pool"))
        .select("season", "week", "entity_id"),
    ]).unique(maintain_order=True)  # fmt: skip
    return Collected(
        ListData(lists, picks, outcome_rows(dataset), keys),
        versions,
        csv_table(csv_path, "stream_track_record"),
    )
