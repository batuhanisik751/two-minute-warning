"""The crowd: ESPN roster % paths, waiver periods, crowd adds and the momentum baseline.

The roster % is FantasyPros' snapshot of ESPN's "% rostered" on the day of each scrape
(``fact_ranking.player_owned_espn``, about weekly). LOCAL ONLY: every frame here is per player
and must never be published (FantasyPros' terms); :mod:`.study` turns them into aggregates.

**Waiver period** ``k`` is the stretch after week ``k``'s games: it opens at the official
as-of of week ``k`` (``dim_week.asof_weekly_utc``, the Tuesday 14:00 UTC the Radar's list of
week ``k`` is made) and closes at the as-of of week ``k+1``. A scrape taken on ``scrape_date``
(placed at 00:00 UTC of that day: a Tuesday scrape counts in the period before that day's
list, ESPN's waivers clear on Wednesday) is in period ``k`` = the number of regular-season
weeks whose window ends at or before it; 0 is before any game, and scrapes after the last
regular-season week are dropped.
"""

from __future__ import annotations

from collections.abc import Sequence

import duckdb
import numpy as np
import polars as pl

OWNED_SCHEMA = {"season": pl.Int32, "gsis_id": pl.String, "pos": pl.String,
                "scrape_date": pl.Date, "pct": pl.Float64}  # fmt: skip
WINDOW_SCHEMA = {"season": pl.Int32, "week": pl.Int32, "window_end_utc": pl.Datetime("us")}


def read_owned(con: duckdb.DuckDBPyConnection, seasons: Sequence[int]) -> pl.DataFrame:
    """One row per (season, gsis_id, scrape_date): the ESPN roster % (the largest of that day's
    pages; they differ in about 1 of 5,000 cases) and the player's most frequent position of
    the season. ``con``: the warehouse, opened read-only."""
    marks = ", ".join("?" for _ in seasons)
    pct = con.execute(
        f"""SELECT season, gsis_id, scrape_date, max(player_owned_espn) AS pct
        FROM fact_ranking WHERE season IN ({marks}) AND gsis_id IS NOT NULL
          AND player_owned_espn IS NOT NULL GROUP BY ALL""",
        [int(s) for s in seasons],
    ).pl()
    pos = con.execute(
        f"""SELECT season, gsis_id, pos, count(*) AS n FROM fact_ranking
        WHERE season IN ({marks}) AND gsis_id IS NOT NULL AND player_owned_espn IS NOT NULL
        GROUP BY ALL""",
        [int(s) for s in seasons],
    ).pl()
    return with_position(pct, pos)


def with_position(pct: pl.DataFrame, pos: pl.DataFrame) -> pl.DataFrame:
    """Attach each player-season's most frequent position (ties: alphabetical)."""
    mode = (
        pos.sort(["season", "gsis_id", "n", "pos"], descending=[False, False, True, False])
        .group_by(["season", "gsis_id"], maintain_order=True)
        .first()
        .select("season", "gsis_id", "pos")
    )
    out = pct.join(mode, on=["season", "gsis_id"], how="left")
    out = out.select(list(OWNED_SCHEMA)).cast(OWNED_SCHEMA)  # type: ignore[arg-type]
    return out.sort("season", "gsis_id", "scrape_date")


def read_windows(con: duckdb.DuckDBPyConnection, seasons: Sequence[int]) -> pl.DataFrame:
    """The regular-season weeks of ``seasons`` with the end of their window (the week's
    official as-of, ``dim_week.window_end_utc``)."""
    marks = ", ".join("?" for _ in seasons)
    df = con.execute(
        f"""SELECT season, week, window_end_utc FROM dim_week
        WHERE season IN ({marks}) AND season_type = 'REG' ORDER BY season, week""",
        [int(s) for s in seasons],
    ).pl()
    return df.cast(WINDOW_SCHEMA)  # type: ignore[arg-type]


def periods_at(times: pl.DataFrame, windows: pl.DataFrame, col: str) -> pl.DataFrame:
    """Add ``period`` (Int32) to ``times`` (columns season and ``col``, a Date or naive-UTC
    Datetime): the number of the season's regular-season weeks whose window ends at or before
    it. Rows at or after the end of the last week's window (the postseason) are dropped, so are
    seasons without windows."""
    parts = []
    for (season,), sub in times.group_by(["season"], maintain_order=True):
        ends = windows.filter(pl.col("season") == season).sort("week")
        if ends.height == 0:
            continue
        end_us = ends.get_column("window_end_utc").cast(pl.Datetime("us")).dt.epoch("us")
        t_us = sub.get_column(col).cast(pl.Datetime("us")).dt.epoch("us")
        k = np.searchsorted(end_us.to_numpy(), t_us.to_numpy(), side="right")
        parts.append(sub.with_columns(pl.Series("period", k, dtype=pl.Int32)))
    if not parts:
        return times.with_columns(pl.lit(None, dtype=pl.Int32).alias("period")).head(0)
    out = pl.concat(parts)
    n_weeks = windows.group_by("season").agg(pl.len().cast(pl.Int32).alias("_n"))
    out = out.join(n_weeks, on="season", how="left")
    return out.filter(pl.col("period") < pl.col("_n")).drop("_n")


def baselines(owned: pl.DataFrame) -> pl.DataFrame:
    """Per season the baseline scrape the "started below" rule reads: the last scrape of
    period 0 (before any game), or, when the season has none (2020: ESPN % from 2020-10-16), its
    first scrape. Columns: season, baseline_date, baseline_period, last_period (the last period
    with a scrape), complete (True when the baseline is in period 0)."""
    days = owned.select("season", "scrape_date", "period").unique()
    rows = []
    for (season,), sub in days.group_by(["season"], maintain_order=True):
        pre = sub.filter(pl.col("period") == 0)
        pick = pre.sort("scrape_date").tail(1) if pre.height else sub.sort("scrape_date").head(1)
        rows.append(
            (int(season), pick.item(0, "scrape_date"), int(pick.item(0, "period")),
             int(sub.get_column("period").max()), pre.height > 0)  # type: ignore[arg-type]
        )  # fmt: skip
    return pl.DataFrame(
        rows,
        schema={"season": pl.Int32, "baseline_date": pl.Date, "baseline_period": pl.Int32,
                "last_period": pl.Int32, "complete": pl.Boolean},
        orient="row",
    ).sort("season")  # fmt: skip


def crowd_adds(
    owned: pl.DataFrame, bases: pl.DataFrame, list_weeks: pl.DataFrame, threshold: float
) -> pl.DataFrame:
    """The crowd's adds at ``threshold`` (percent): players who started the season below it
    (under it at the baseline scrape, or absent from it: the pages list players down to 0%)
    and whose first later scrape at or above it falls in a period after the baseline's and no
    later than the season's last Radar list (``list_weeks``: season, last_list_week).
    Columns: season, gsis_id, pos, start_pct (NULL = absent), add_date, add_period, add_pct."""
    o = owned.join(bases.select("season", "baseline_date", "baseline_period"), on="season")
    start = o.filter(pl.col("scrape_date") == pl.col("baseline_date")).select(
        "season", "gsis_id", pl.col("pct").alias("start_pct")
    )
    first = (
        o.filter((pl.col("scrape_date") > pl.col("baseline_date")) & (pl.col("pct") >= threshold))
        .sort("season", "gsis_id", "scrape_date")
        .group_by(["season", "gsis_id"], maintain_order=True)
        .first()
        .select("season", "gsis_id", pl.col("scrape_date").alias("add_date"),
                pl.col("period").alias("add_period"), pl.col("pct").alias("add_pct"))
    )  # fmt: skip
    players = o.select("season", "gsis_id", "pos", "baseline_period").unique(
        ["season", "gsis_id"], keep="first", maintain_order=True
    )
    df = (
        players.join(start, on=["season", "gsis_id"], how="left")
        .join(first, on=["season", "gsis_id"], how="inner")
        .join(list_weeks.select("season", "last_list_week"), on="season", how="inner")
    )
    below = pl.col("start_pct").is_null() | (pl.col("start_pct") < threshold)
    in_range = (pl.col("add_period") > pl.col("baseline_period")) & (
        pl.col("add_period") <= pl.col("last_list_week")
    )
    return (
        df.filter(below & in_range)
        .select("season", "gsis_id", "pos", "start_pct", "add_date", "add_period", "add_pct")
        .sort("season", "gsis_id")
    )


def period_values(owned: pl.DataFrame, bases: pl.DataFrame) -> pl.DataFrame:
    """Per (season, gsis_id, period) from the baseline's period on: the roster % of the
    player's last scrape in the period (``value``); a period with scrapes in which the player
    is absent gets 0 (the pages reach 0%), a period without any scrape of the season stays
    out. Columns: season, gsis_id, period, value."""
    o = owned.join(bases.select("season", "baseline_period"), on="season").filter(
        pl.col("period") >= pl.col("baseline_period")
    )
    last = (
        o.sort("season", "gsis_id", "scrape_date")
        .group_by(["season", "gsis_id", "period"], maintain_order=True)
        .last()
        .select("season", "gsis_id", "period", pl.col("pct").alias("value"))
    )
    grid = (
        o.select("season", "gsis_id")
        .unique()
        .join(o.select("season", "period").unique(), on="season")
    )
    out = grid.join(last, on=["season", "gsis_id", "period"], how="left")
    return out.with_columns(pl.col("value").fill_null(0.0)).sort("season", "gsis_id", "period")


def momentum_flags(
    values: pl.DataFrame,
    bases: pl.DataFrame,
    list_weeks: pl.DataFrame,
    threshold: float,
    points: float,
) -> pl.DataFrame:
    """The "crowd momentum" baseline: a player is flagged in the first period ``p`` (after the
    baseline's, up to the season's last Radar list) in which his roster % rose by at least
    ``points`` over period ``p - 1`` while it was still under ``threshold`` in ``p - 1``.
    ``values``: :func:`period_values`. Columns: season, gsis_id, flag_period, rise."""
    keys = ["season", "gsis_id"]
    v = values.sort("season", "gsis_id", "period").with_columns(
        pl.col("value").shift(1).over(keys).alias("_prev"),
        pl.col("period").shift(1).over(keys).alias("_prev_period"),
    )
    v = v.join(bases.select("season", "baseline_period"), on="season").join(
        list_weeks.select("season", "last_list_week"), on="season"
    )
    rise = pl.col("value") - pl.col("_prev")
    hit = (
        (pl.col("_prev_period") == pl.col("period") - 1)
        & (rise >= points)
        & (pl.col("_prev") < threshold)
        & (pl.col("period") > pl.col("baseline_period"))
        & (pl.col("period") <= pl.col("last_list_week"))
    )
    return (
        v.filter(hit)
        .group_by(keys, maintain_order=True)
        .first()
        .select("season", "gsis_id", pl.col("period").alias("flag_period"), rise.alias("rise"))
        .sort(keys)
    )
