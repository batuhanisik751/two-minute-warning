"""The experts' view at the as-of: FantasyPros positional ranks for the expert baseline (C4).

PROJECT_SPEC 8.1 baseline 3 ranks pool players the way the experts did "where available". For
every pool row this module finds the player's FantasyPros expert consensus positional rank
(ECR) that was public at the Tuesday as-of (``fact_ranking``, through the as-of view):

1. the season's latest **rest-of-season** page of his position (``page_kind = 'ros'``) public
   at the as-of, if it lists him;
2. otherwise his rank on the season's latest **weekly** page of his position that lists him,
   among the weekly pages scraped up to ``WEEKLY_MAX_AGE_DAYS`` before the latest one (a
   weekly page leaves out the teams on their bye week; the same rule as the pool's rostership
   columns);
3. otherwise none (NULL): the experts did not rank him at all.

"His position" is the pool row's roster position: the rank is read from that position's page
(``page_pos``), so ranks within one (as-of, position) group are comparable. ``ecr_available``
says whether any rest-of-season or weekly page of the position was public at the as-of (the
archive starts in December 2019, and some seasons' first pages come in week 3 or 4): the
baseline is only evaluated where it is true.

These columns are registered as METRICS, not features (``twm.registry``): no model may learn
from them (``check_features`` refuses them). The archive's pages were saved on Fridays, so at
the Tuesday as-of after week N the experts' latest page is the one from before week N's games:
they had not seen week N yet, a real handicap of this baseline (docs/waiver_radar.md).

Two paths, one answer (like the features): :func:`expert_ranks_for` reads through an
:class:`~twm.asof.AsOfView` (the reference, tested with the leakage harness);
:func:`expert_ranks_history` reads each season's pages once with their ``available_at`` and
filters them per as-of with :func:`twm.asof.asof_filter` (tests check both agree).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Protocol

import polars as pl

from twm.asof import asof_filter

EXPERT_COLUMNS = ("ecr_available", "ecr_pos_rank", "ecr_page_kind", "ecr_scrape_date")
EXPERT_SCHEMA: dict[str, pl.DataType] = {
    "ecr_available": pl.Boolean(),
    "ecr_pos_rank": pl.Int32(),
    "ecr_page_kind": pl.String(),
    "ecr_scrape_date": pl.Date(),
}
# A weekly page leaves out the teams on their bye week: a player missing from the latest weekly
# page keeps his rank from a page up to this many days older (pages are about 7 days apart).
WEEKLY_MAX_AGE_DAYS = 8
PAGE_KINDS = ("ros", "weekly")  # in order of preference
_KEYS = ("season", "week", "gsis_id")
_PAGE_SCHEMA: dict[str, pl.DataType] = {
    "page_kind": pl.String(),
    "page_pos": pl.String(),
    "scrape_date": pl.Date(),
    "gsis_id": pl.String(),
    "pos_rank": pl.Int32(),
}


class SqlSource(Protocol):
    def sql(self, query: str, params: list | None = None) -> pl.DataFrame: ...


def pages_sql(season: int, *, with_available_at: bool = False) -> str:
    """The season's rest-of-season and weekly positional pages (one row per listed player)."""
    extra = ", available_at" if with_available_at else ""
    return (
        f"SELECT page_kind, page_pos, scrape_date, gsis_id, pos_rank{extra} FROM fact_ranking "
        f"WHERE season = {int(season)} AND page_kind IN ('ros', 'weekly')"
    )


def _cast_pages(pages: pl.DataFrame) -> pl.DataFrame:
    return pages.cast({c: t for c, t in _PAGE_SCHEMA.items()}, strict=False)


def compute_expert_ranks(pages: pl.DataFrame, rows: pl.DataFrame) -> pl.DataFrame:
    """The expert columns for ``rows`` (season, week, gsis_id, position) from the pages that
    were public at their as-of (every row of ``rows`` must share that as-of). Returns
    season, week, gsis_id + EXPERT_COLUMNS in the order of ``rows``."""
    pages = _cast_pages(pages.select(list(_PAGE_SCHEMA)))
    listed = pages.filter(pl.col("gsis_id").is_not_null() & pl.col("pos_rank").is_not_null())
    # rest of season: the latest page of each position, whoever it lists
    ros_day = (
        pages.filter(pl.col("page_kind") == "ros")
        .group_by("page_pos")
        .agg(pl.col("scrape_date").max().alias("_day"))
    )
    ros = (
        listed.filter(pl.col("page_kind") == "ros")
        .join(ros_day, on="page_pos")
        .filter(pl.col("scrape_date") == pl.col("_day"))
        .group_by("page_pos", "gsis_id")
        .agg(pl.col("pos_rank").min(), pl.col("scrape_date").first())
        .with_columns(pl.lit("ros").alias("page_kind"))
    )
    # weekly: the latest page listing him among those up to WEEKLY_MAX_AGE_DAYS older than the
    # latest page of the position
    wk_day = (
        pages.filter(pl.col("page_kind") == "weekly")
        .group_by("page_pos")
        .agg(pl.col("scrape_date").max().alias("_day"))
    )
    weekly = (
        listed.filter(pl.col("page_kind") == "weekly")
        .join(wk_day, on="page_pos")
        .filter(pl.col("scrape_date") >= pl.col("_day") - pl.duration(days=WEEKLY_MAX_AGE_DAYS))
        .filter(pl.col("scrape_date") == pl.col("scrape_date").max().over("page_pos", "gsis_id"))
        .group_by("page_pos", "gsis_id")
        .agg(pl.col("pos_rank").min(), pl.col("scrape_date").first())
        .with_columns(pl.lit("weekly").alias("page_kind"))
    )
    positions = set(pages.get_column("page_pos").unique().to_list())
    base = rows.select("season", "week", "gsis_id", "position").with_row_index("_i")
    joined = base.join(
        ros.rename({"pos_rank": "_ros_rank", "scrape_date": "_ros_day"}).drop("page_kind"),
        left_on=["gsis_id", "position"],
        right_on=["gsis_id", "page_pos"],
        how="left",
    ).join(
        weekly.rename({"pos_rank": "_wk_rank", "scrape_date": "_wk_day"}).drop("page_kind"),
        left_on=["gsis_id", "position"],
        right_on=["gsis_id", "page_pos"],
        how="left",
    )
    out = joined.sort("_i").select(
        "season",
        "week",
        "gsis_id",
        pl.col("position").is_in(sorted(positions)).alias("ecr_available"),
        pl.coalesce("_ros_rank", "_wk_rank").alias("ecr_pos_rank"),
        pl.when(pl.col("_ros_rank").is_not_null())
        .then(pl.lit("ros"))
        .when(pl.col("_wk_rank").is_not_null())
        .then(pl.lit("weekly"))
        .otherwise(pl.lit(None, dtype=pl.String))
        .alias("ecr_page_kind"),
        pl.when(pl.col("_ros_rank").is_not_null())
        .then(pl.col("_ros_day"))
        .otherwise(pl.col("_wk_day"))
        .alias("ecr_scrape_date"),
    )
    return out.cast(EXPERT_SCHEMA)  # type: ignore[arg-type]


def _check_rows(rows: pl.DataFrame) -> None:
    missing = [c for c in (*_KEYS, "position") if c not in rows.columns]
    if missing:
        raise KeyError(f"pool rows lack columns {missing}")


def expert_ranks_for(
    view: SqlSource, season: int, week: int, pool_rows: pl.DataFrame
) -> pl.DataFrame:
    """The reference path: the expert columns of ``pool_rows`` (the pool at ``view``'s as-of,
    ``season``/``week``), every page read through the as-of view."""
    _check_rows(pool_rows)
    rows = pool_rows.filter((pl.col("season") == season) & (pl.col("week") == week))
    if rows.height != pool_rows.height:
        raise ValueError("pool_rows must all belong to the as-of's season and week")
    pages = view.sql(pages_sql(season))
    return compute_expert_ranks(pages, rows)


def empty_expert_ranks() -> pl.DataFrame:
    return pl.DataFrame(
        schema={"season": pl.Int32(), "week": pl.Int32(), "gsis_id": pl.String(), **EXPERT_SCHEMA}
    )


def expert_ranks_history(db: Path | str, pool: pl.DataFrame) -> pl.DataFrame:
    """The batch path: the expert columns of every row of ``pool`` (C1 pool history with its
    ``as_of`` column), each season's pages read once and filtered per as-of."""
    from twm.warehouse.build import connect

    _check_rows(pool)
    if "as_of" not in pool.columns:
        raise KeyError("pool rows lack the as_of column")
    if pool.height == 0:
        return empty_expert_ranks()
    frames = []
    con = connect(db, read_only=True)
    try:
        for season in sorted(pool.get_column("season").unique().to_list()):
            pages = con.execute(pages_sql(int(season), with_available_at=True)).pl()
            rows = pool.filter(pl.col("season") == season)
            parts = rows.partition_by(["week", "as_of"], as_dict=True, maintain_order=True)
            for (_week, as_of), grp in sorted(parts.items()):
                assert isinstance(as_of, datetime)
                frames.append(compute_expert_ranks(asof_filter(pages, as_of), grp))
    finally:
        con.close()
    return pl.concat(frames, how="vertical")
