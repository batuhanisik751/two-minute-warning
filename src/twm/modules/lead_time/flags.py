"""The Radar's flags: when was a player first on a Waiver Radar list, and at which priority?

Source: the pinned walk-forward backtest (:func:`twm.pins.load_backtest`, sha256-checked):
weekly lists 2014-2025, each scored by a model that never saw its season. The snapshot keeps no
priority, so it is recomputed exactly as the published lists do
(:func:`twm.publish.collect.backtest_chances`): for a list of season S the similar-player bins
come from the backtest seasons before S only (:func:`~twm.modules.waiver_radar.confidence.
seasons_before`), the chance is the bin's hit rate and the priority ``tier_of(chance)``, for
the top :data:`~twm.modules.waiver_radar.confidence.TOP_N` of each list.

List week W is made at week W's official as-of (Tuesday after its games), for the waiver
period W (:mod:`.crowd`). Flag levels (:data:`twm.modules.lead_time.LEVELS`): ``must_add``;
``spec_plus`` (must-add or speculative); ``listed`` (top 25 at any priority).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import polars as pl

ROWS_SCHEMA = {"season": pl.Int32, "week": pl.Int32, "position": pl.String,
               "gsis_id": pl.String, "score": pl.Float64, "rank": pl.Int32,
               "y": pl.Boolean}  # fmt: skip


def snapshot_rows(frames: dict[str, Any]) -> pl.DataFrame:
    """The backtest's list rows with the Radar's own outcome (``y``: the production label,
    y_hit) from the snapshot frames ({'predictions', 'outcomes', ...})."""
    from twm.modules.waiver_radar.production import LABEL

    p = frames["predictions"].select(
        "season", "week", pl.col("rank_group").alias("position"),
        pl.col("entity_id").alias("gsis_id"), "score", "rank",
    )  # fmt: skip
    o = frames["outcomes"].select(
        pl.col("entity_id").alias("gsis_id"), "season", "week", pl.col(LABEL).alias("y")
    )
    out = p.join(o, on=["gsis_id", "season", "week"], how="left")
    return out.select(list(ROWS_SCHEMA)).cast(ROWS_SCHEMA)  # type: ignore[arg-type]


def pinned_rows() -> pl.DataFrame:
    """:func:`snapshot_rows` of the approved Waiver Radar pin (config/production_models.yaml)."""
    from twm import pins
    from twm.modules.waiver_radar.production import MODULE

    return snapshot_rows(pins.load_backtest(pins.get_pin(MODULE)))


def confidence_of(rows: pl.DataFrame, seasons: tuple[int, int]) -> Any:
    """The similar-player bins of ``rows`` in ``seasons`` (first, last), as
    :func:`~twm.modules.waiver_radar.confidence.from_store` builds them from the store."""
    from twm.modules.waiver_radar import confidence as cf
    from twm.modules.waiver_radar.production import LABEL, MODEL

    sub = rows.filter(pl.col("season").is_between(*seasons))
    if sub.height == 0:
        raise ValueError(f"no backtest rows in {seasons[0]}-{seasons[1]}")
    y = sub.get_column("y").cast(pl.Int64).to_numpy()
    bins = cf.similar_bins(sub.get_column("score").to_numpy(), y, cf.MIN_ROWS)
    seen = sub.get_column("season")
    span = (int(seen.min()), int(seen.max()))  # type: ignore[arg-type]
    return cf.Confidence(bins, MODEL, LABEL, span, sub.height)


def tiered(rows: pl.DataFrame, seasons: Sequence[int]) -> pl.DataFrame:
    """The list rows of ``seasons`` with ``chance`` and ``tier`` (top 25 only, NULL below),
    each season's bins learned from the backtest seasons before it."""
    from twm.modules.waiver_radar import confidence as cf

    parts = []
    for season in sorted({int(s) for s in seasons}):
        sub = rows.filter(pl.col("season") == season)
        if sub.height == 0:
            continue
        conf = confidence_of(rows, cf.seasons_before(season))
        chance = conf.band(sub.get_column("score").to_numpy()).get_column("chance")
        top = pl.col("rank") <= cf.TOP_N
        parts.append(
            sub.with_columns(pl.Series("_chance", chance, dtype=pl.Float64))
            .with_columns(
                pl.when(top).then(pl.col("_chance")).alias("chance"),
                pl.when(top)
                .then(pl.col("_chance").map_elements(cf.tier_of, return_dtype=pl.String))
                .alias("tier"),
            )
            .drop("_chance")
        )
    if not parts:
        return rows.head(0).with_columns(
            pl.lit(None, dtype=pl.Float64).alias("chance"),
            pl.lit(None, dtype=pl.String).alias("tier"),
        )
    return pl.concat(parts).sort("season", "week", "position", "rank")


def flag_weeks(lists: pl.DataFrame) -> pl.DataFrame:
    """Every (season, gsis_id, level, week) a player was flagged at a level (long form), plus
    level ``pool`` (on a list at any rank). ``lists``: :func:`tiered`, or any frame with
    season, week, gsis_id, rank and tier (e.g. the live lists of the store)."""
    level = {
        "must_add": pl.col("tier") == "must-add",
        "spec_plus": pl.col("tier").is_in(["must-add", "speculative"]),
        "listed": pl.col("tier").is_not_null(),
        "pool": pl.lit(True),
    }
    parts = [
        lists.filter(cond).select("season", "gsis_id", pl.lit(name).alias("level"), "week").unique()
        for name, cond in level.items()
    ]
    return pl.concat(parts).sort("season", "gsis_id", "level", "week")


def first_flags(weeks: pl.DataFrame) -> pl.DataFrame:
    """Wide: one row per (season, gsis_id) with the first week at each level (NULL = never):
    must_add, spec_plus, listed, pool."""
    first = weeks.group_by(["season", "gsis_id", "level"]).agg(pl.col("week").min())
    wide = first.pivot(on="level", index=["season", "gsis_id"], values="week")
    for name in ("must_add", "spec_plus", "listed", "pool"):
        if name not in wide.columns:
            wide = wide.with_columns(pl.lit(None, dtype=pl.Int32).alias(name))
    return wide.select("season", "gsis_id", "must_add", "spec_plus", "listed", "pool").sort(
        "season", "gsis_id"
    )


def list_weeks(lists: pl.DataFrame) -> pl.DataFrame:
    """Per season the last list week (season, last_list_week)."""
    return (
        lists.group_by("season")
        .agg(pl.col("week").max().cast(pl.Int32).alias("last_list_week"))
        .sort("season")
    )
