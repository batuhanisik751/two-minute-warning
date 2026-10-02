"""The market baseline (step I1b): FantasyPros' PRESEASON expert consensus ranking (ECR) of
season S+1, i.e. the average of many experts' redraft position ranks, NOT average draft position
(ADP, which the warehouse does not have; spec 8.6 says "preseason ADP"). Preseason cheat sheets
exist for 2020 on (``fact_ranking`` page_kind 'preseason'), so this baseline covers labels
2020-2025 (snapshots S = 2019-2024) only.

The ranking is the project's preseason scrape (:func:`twm.ids.preseason_scrape_sql`: the last
August/September scrape before week 1's first game day), read through an AsOfView at week 1's
as-of of S+1. It is LATER than the board's end-of-season snapshot: the experts already know the
offseason (free agency, the draft, injuries), so it is a strong baseline. A player's rank is his
rank on his S position's sheet, else on his FantasyPros position's sheet; a player on neither is
unranked (scored below every ranked player).

Scores (higher = more likely the label): Cliff and y_missed: ECR rank in S+1 minus PPG rank in S
(how far the market expects him to fall); Breakout: minus the ECR rank.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from twm import ids
from twm.asof import AsOfView, weekly_as_of

FIRST_LABEL_SEASON = 2020


def ecr_ranks(db: Path | str, label_season: int) -> pl.DataFrame:
    """(gsis_id, page_pos, pos, pos_rank, page_max) of the preseason scrape of ``label_season``."""
    s = int(label_season)
    with AsOfView(db, weekly_as_of(db, s, 1)) as v:
        return v.sql(f"""
            WITH pre_pages AS (
                SELECT season, scrape_date, page_pos, pos, gsis_id, pos_rank FROM fact_ranking
                WHERE season = {s} AND ecr_type = 'rp' AND page_kind = 'preseason'
            ), pre_scrape AS ({ids.preseason_scrape_sql("pre_pages")})
            SELECT p.gsis_id, p.page_pos, p.pos, p.pos_rank,
                   max(p.pos_rank) OVER (PARTITION BY p.page_pos) AS page_max
            FROM pre_pages p JOIN pre_scrape USING (season, scrape_date)
            WHERE p.pos_rank IS NOT NULL
            ORDER BY p.page_pos, p.pos_rank, p.gsis_id""")


def attach_ecr(rows: pl.DataFrame, ranks: pl.DataFrame) -> pl.DataFrame:
    """``rows`` (one season; gsis_id, position) + ``ecr_rank`` (NULL = unranked) and
    ``ecr_fill`` (the rank used for scoring: an unranked player = his page's last rank + 1)."""
    r = ranks.filter(pl.col("gsis_id").is_not_null())
    own = r.select("gsis_id", pl.col("page_pos").alias("position"), pl.col("pos_rank").alias("_a"))
    fp = (
        r.filter(pl.col("page_pos") == pl.col("pos"))
        .group_by("gsis_id")
        .agg(pl.col("pos_rank").min().alias("_b"))
    )
    page_max = r.group_by(pl.col("page_pos").alias("position")).agg(pl.col("page_max").max())
    own = own.group_by("gsis_id", "position").agg(pl.col("_a").min())
    out = (
        rows.join(own, on=["gsis_id", "position"], how="left")
        .join(fp, on="gsis_id", how="left")
        .join(page_max, on="position", how="left")
    )
    return (
        out.with_columns(pl.coalesce("_a", "_b").alias("ecr_rank"))
        .with_columns(pl.coalesce("ecr_rank", pl.col("page_max") + 1).alias("ecr_fill"))
        .drop("_a", "_b", "page_max")
    )


def ecr_score(df: pl.DataFrame, kind: str) -> pl.Series:
    """The baseline's score: 'fall' (Cliff, y_missed) or 'rise' (Breakout)."""
    if kind == "fall":
        return (df.get_column("ecr_fill") - df.get_column("pos_rank_s")).cast(pl.Float64)
    if kind == "rise":
        return (-df.get_column("ecr_fill")).cast(pl.Float64)
    raise ValueError(f"unknown ECR score kind {kind!r}")
