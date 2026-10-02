"""The board's POST-DRAFT snapshot (step I2b, PROJECT_SPEC 8.6 / step I2).

Same rows and labels as the end-of-season snapshot of S, read after the S+1 draft, on config
``as_of.board.post_draft`` (spec 6.1: June 1) of S+1 at 00:00 UTC (:func:`post_draft_as_of`).
Every I1b feature is recomputed through an :class:`~twm.asof.AsOfView` at that moment
(``hc_departure`` then counts the departures announced by the as-of: the head-coach change of
this snapshot), and the draft features below exist only now (docs/board.md "Post-draft
snapshot" defines each). The picks are ``dim_player``'s draft
fields (public from ``draft_public_month_day`` of the draft year); a pick's position is his
``fact_combine`` position of that year (public from the draft's first day): ``dim_player``'s
position is today's and hidden point-in-time.

- ``draft_pos_count_pd``: picks of his S team in that draft at his position (RB: RB/HB);
- ``draft_pos_best_round_pd`` / ``draft_pos_best_pick_pd``: the best (lowest) round and overall
  pick among them (NULL when none);
- ``draft_qb_r1_pd``: his S team drafted a quarterback in round 1;
- ``draft_unplaced_pd``: his S team made a round 1-3 pick with no combine position (a pick that
  may be at his position but cannot be placed: the counts above may miss it).

Every one is NULL when the S+1 draft has no visible pick at the as-of. Moves by free agency or
trade are not features: the warehouse has no dated transactions, so they are not observable
point-in-time (docs/board.md).
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any

import polars as pl

from twm.modules.board import features as bf
from twm.warehouse.available import DRAFT_FIRST_DAY, AvailabilityRules

# Only each draft's FIRST day is recorded (available.DRAFT_FIRST_DAY): the day after its last
# day is taken as the first day + 3 days (a three-day draft, Thursday to Saturday, 2010 on;
# 2000-2009 drafts had two days, so this errs late).
DAY_AFTER_LAST = timedelta(days=3)
GROUPS = {"QB": "QB", "RB": "RB", "HB": "RB", "WR": "WR", "TE": "TE"}  # combine pos -> group
UNPLACED_MAX_ROUND = 3
NEW_FEATURES: tuple[str, ...] = (
    "draft_pos_count_pd", "draft_pos_best_round_pd", "draft_pos_best_pick_pd", "draft_qb_r1_pd",
    "draft_unplaced_pd",
)  # fmt: skip


def post_draft_as_of(
    season: int, month_day: str | None = None, rules: AvailabilityRules | None = None
) -> datetime:
    """00:00 UTC on ``month_day`` (default: config ``as_of.board.post_draft``, spec 6.1 "June
    1") of ``season`` + 1 (aware UTC). Refused when that is before the draft is complete and
    public (the day after its last day = its first day + 3 days, and ``draft_public_month_day``):
    a "post-draft" snapshot must see the draft."""
    from twm.config import settings

    y = int(season) + 1
    md = month_day or settings().as_of.board.post_draft
    month, day = (int(x) for x in md.split("-"))
    at = datetime(y, month, day, tzinfo=UTC)
    r = rules or AvailabilityRules.from_settings()
    public = datetime(y, r.draft_public_month, r.draft_public_day, tzinfo=UTC)
    first = DRAFT_FIRST_DAY.get(y)
    done = (
        public
        if first is None
        else max(datetime.combine(first + DAY_AFTER_LAST, time.min, UTC), public)
    )
    if at < done:
        raise ValueError(f"post-draft as-of {at:%Y-%m-%d} is before the {y} draft is public "
                         f"({done:%Y-%m-%d}); check as_of.board.post_draft")  # fmt: skip
    return at


def draft_picks(view: Any, year: int) -> pl.DataFrame:
    """(gsis_id, team, round, pick, grp) of the ``year`` draft's picks visible in ``view``;
    ``grp`` = the pick's combine position group (QB/RB/WR/TE), 'other' for another combine
    position, NULL without a combine row of that year. ``team`` is today's franchise code like
    every team column of the warehouse."""
    y = int(year)
    df = view.sql(f"""
        SELECT p.gsis_id, p.draft_team AS team, CAST(p.draft_round AS INTEGER) AS round,
               CAST(p.draft_pick AS INTEGER) AS pick, c.pos
        FROM dim_player p LEFT JOIN (
            SELECT gsis_id, pos FROM fact_combine WHERE season = {y} AND gsis_id IS NOT NULL
            QUALIFY row_number() OVER (PARTITION BY gsis_id ORDER BY pos) = 1
        ) c ON c.gsis_id = p.gsis_id
        WHERE p.draft_year = {y} AND p.draft_round IS NOT NULL AND p.draft_team IS NOT NULL
        ORDER BY p.draft_pick, p.gsis_id""")
    grp = pl.col("pos").replace_strict(GROUPS, default="other", return_dtype=pl.String)
    return df.with_columns(pl.when(pl.col("pos").is_null()).then(None).otherwise(grp).alias("grp"))


def draft_features(rows: pl.DataFrame, picks: pl.DataFrame) -> pl.DataFrame:
    """``rows`` (gsis_id, position, team = his S team) + :data:`NEW_FEATURES` from ``picks``
    (:func:`draft_picks`; no pick at all: every feature NULL)."""
    c = pl.col
    if picks.is_empty():
        return rows.with_columns(pl.lit(None, dtype=pl.Float64).alias(n) for n in NEW_FEATURES)
    pos = (
        picks.filter(c("grp").is_in(["QB", "RB", "WR", "TE"]))
        .group_by("team", c("grp").alias("position"))
        .agg(c("pick").len().alias("_n"), c("round").min().alias("_r"), c("pick").min().alias("_p"))
    )  # fmt: skip
    team = picks.group_by("team").agg(
        ((c("grp") == "QB") & (c("round") == 1)).any().alias("_qb"),
        (c("grp").is_null() & (c("round") <= UNPLACED_MAX_ROUND)).any().alias("_un"),
    )
    df = rows.join(pos, on=["team", "position"], how="left").join(team, on="team", how="left")
    out = df.with_columns(
        c("_n").fill_null(0).cast(pl.Float64).alias("draft_pos_count_pd"),
        c("_r").cast(pl.Float64).alias("draft_pos_best_round_pd"),
        c("_p").cast(pl.Float64).alias("draft_pos_best_pick_pd"),
        c("_qb").fill_null(False).cast(pl.Float64).alias("draft_qb_r1_pd"),
        c("_un").fill_null(False).cast(pl.Float64).alias("draft_unplaced_pd"),
    )
    return out.drop("_n", "_r", "_p", "_qb", "_un")


def post_draft_features(
    view: Any, season: int, *, xfp_games: pl.DataFrame | None, departures: bf.Departures
) -> pl.DataFrame:
    """Every I1b feature of ``season`` read through ``view`` (the post-draft as-of of S+1) and
    the :data:`NEW_FEATURES`; same rows as :func:`features.snapshot_features`."""
    s = int(season)
    base = bf.snapshot_features(view, s, xfp_games=xfp_games, departures=departures)
    new = draft_features(base.select("gsis_id", "position", "team"), draft_picks(view, s + 1))
    new = new.select("gsis_id", *NEW_FEATURES)
    return base.join(new, on="gsis_id", how="left").sort("season", "gsis_id")
