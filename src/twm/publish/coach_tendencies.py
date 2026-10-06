"""Coach tendencies as ``twm publish`` writes them (feature #10; docs/coach_tendencies.md). Reads
only.

Source: the frozen history of every completed season (C10c: the pin ``coach_tendencies``,
:mod:`twm.modules.coach_tendencies.production`; the runner's warehouse starts in 2012) and the
warehouse, opened read-only through :class:`twm.asof.AsOfView` at the publish clock, for the
seasons after it (the season in progress counts only the plays public at the clock); career
lines are recomputed from both (``production.frames_from_history``). Its four
frames are replaced on every publish (:data:`twm.publish.tables.COACH_TENDENCIES`):
``coach_tendency_season`` / ``_career`` / ``_persistence`` / ``_fantasy_link``.

- **coach ids**: the warehouse's ``coach_id`` ('andy_reid') becomes the site's slug of his
  ``dim_coach.coach_name`` (:func:`twm.modules.decisions.site.coach_slug`: 'andy-reid'), the
  ``/coach/[id]`` route and dim_coach's key; the coaches are upserted into dim_coach with the
  decisions' and the Hot-Seat Meter's (:func:`twm.publish.collect.merge_coaches`);
- **NaN** (an r or an interval on too few pairs) becomes NULL; integer columns are Int32.

Play-by-play only: no FantasyPros data, no league data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import polars as pl

from twm.publish.collect import PublishInputError
from twm.publish.tables import COACH_TENDENCIES, TABLES

MODULE = "coach_tendencies"
SEASON, CAREER, PERSISTENCE, LINK = COACH_TENDENCIES
COACH_TABLES = (SEASON, CAREER)  # the frames keyed by coach


@dataclass
class CoachTendencyData:
    """``tables``: the four frames in tables.TABLES column order; ``coaches``: dim_coach rows
    (coach_id = site slug, name) of every coach they name; ``season``: the newest season and
    ``through_week`` its last week played (None when it has no row)."""

    season: int
    through_week: int | None
    tables: dict[str, pl.DataFrame]
    coaches: pl.DataFrame


def typed(df: pl.DataFrame, name: str) -> pl.DataFrame:
    """``df`` in the table's column order: integers Int32, NaN -> NULL in the float columns."""
    t = TABLES[name]
    cols = []
    for c, pg in t.columns:
        if pg == "integer":
            cols.append(pl.col(c).cast(pl.Int32))
        elif pg == "double precision":
            cols.append(pl.col(c).cast(pl.Float64).fill_nan(None))
        else:
            cols.append(pl.col(c))
    return df.select(cols)


def slugs(names: pl.DataFrame) -> pl.DataFrame:
    """(coach_id, slug, name) from the warehouse's dim_coach (coach_id, coach_name);
    :class:`PublishInputError` when two warehouse coaches would share a site id."""
    from twm.modules.decisions.site import coach_slug

    out = names.select(
        "coach_id",
        pl.col("coach_name").map_elements(coach_slug, return_dtype=pl.String).alias("slug"),
        pl.col("coach_name").alias("name"),
    ).unique()
    clash = out.filter(pl.col("slug").is_duplicated())
    if clash.height:
        raise PublishInputError(f"warehouse coaches share a site id: {clash.rows()[:4]}")
    return out


def site_tables(frames: dict[str, pl.DataFrame],
                names: pl.DataFrame) -> tuple[dict[str, pl.DataFrame], pl.DataFrame]:  # fmt: skip
    """(the four published frames with site coach ids, typed; their dim_coach rows) from
    build()'s frames and the warehouse's dim_coach (coach_id, coach_name)."""
    ids = slugs(names)
    known = set(ids.get_column("coach_id").to_list())
    out: dict[str, pl.DataFrame] = {}
    for name in COACH_TENDENCIES:
        df = frames[name]
        if name in COACH_TABLES:
            missing = sorted(set(df.get_column("coach_id").to_list()) - known)
            if missing:
                raise PublishInputError(f"{name}: coaches missing from the warehouse's "
                                        f"dim_coach: {missing[:4]}")  # fmt: skip
            df = df.join(ids.select("coach_id", "slug"), on="coach_id").with_columns(
                pl.col("slug").alias("coach_id"))  # fmt: skip
        out[name] = typed(df, name).sort(list(TABLES[name].key))
    used = set().union(*(set(out[n].get_column("coach_id").to_list()) for n in COACH_TABLES))
    coaches = (ids.filter(pl.col("slug").is_in(sorted(used)))
               .select(pl.col("slug").alias("coach_id"), "name").sort("coach_id"))  # fmt: skip
    return out, coaches


def latest(season_rows: pl.DataFrame) -> tuple[int, int | None]:
    """(the newest season of the season frame, its last week played: None without rows)."""
    if season_rows.height == 0:
        return 0, None
    s = int(season_rows.get_column("season").max())  # type: ignore[arg-type]
    week = season_rows.filter(pl.col("season") == s).get_column("through_week").max()
    return s, int(week)  # type: ignore[arg-type]


def collect_coach_tendencies(warehouse: Path, season: int, now: datetime, *,
                             pin_path: Path | None = None,
                             root: Path | None = None) -> CoachTendencyData:  # fmt: skip
    """Everything the module publishes (module docstring): the completed seasons from the
    frozen history (C10c, :mod:`twm.modules.coach_tendencies.production`; refused when its
    pin is missing or a file is not the approved bytes), the seasons after it built through
    an AsOfView at ``now``; ``season`` (the configured current season) is the season in
    progress, and seasons after the newest with a regular-season play are left out."""
    from twm.asof import AsOfView
    from twm.modules.coach_tendencies import production as prod
    from twm.pins import PinError

    try:
        history, _ = prod.load_pinned(int(season), path=pin_path, root=root)
        with AsOfView(warehouse, now) as v:
            frames = prod.frames_from_history(history, v, int(season))
            names = v.sql("SELECT coach_id, coach_name FROM dim_coach")
    except PinError as e:
        raise PublishInputError(f"coach tendencies: {e}") from e
    except FileNotFoundError as e:
        raise PublishInputError(str(e)) from e
    tables, coaches = site_tables(frames, prod.coach_names(history, names))
    s, week = latest(tables[SEASON])
    return CoachTendencyData(season=s, through_week=week, tables=tables, coaches=coaches)


def meta(d: CoachTendencyData) -> dict[str, str]:
    """site_meta's ``coach_tendency_season`` (the newest season with rows) and
    ``coach_tendency_through_week`` (its last week played): the pages name the sample."""
    return {"coach_tendency_season": str(d.season) if d.season else "",
            "coach_tendency_through_week": "" if d.through_week is None else
            str(d.through_week)}  # fmt: skip


def problems(d: CoachTendencyData, teams: set[str], coaches: set[str]) -> list[str]:
    """Plain-English problems of the module's rows (empty = publishable); ``coaches``: the
    dim_coach ids the publish upserts."""
    from twm.modules.coach_tendencies import fantasy, persistence, season
    from twm.publish.collect import _team_problems

    out: list[str] = []
    t = d.tables
    for name in COACH_TENDENCIES:
        if t[name].select(TABLES[name].key).is_duplicated().any():
            out.append(f"{name} repeats a key {TABLES[name].key}")
        if t[name].filter(~pl.col("metric").is_in(list(season.METRICS))).height:
            out.append(f"{name} names an unknown metric")
    for name in COACH_TABLES:
        unknown = set(t[name].get_column("coach_id").to_list()) - coaches
        if unknown:
            out.append(f"{name} names coaches missing from dim_coach: {sorted(unknown)[:3]}")
    rows = t[SEASON]
    out += _team_problems("coach-tendency rows", rows, teams)
    pct = pl.col("percentile")
    if rows.filter(pct.is_not_null() & ((pct < 0) | (pct > 100))).height:
        out.append("some coach-tendency percentiles are outside 0-100")
    rate = ~pl.col("metric").is_in(["proe", "neutral_sec_per_play"])
    for name in COACH_TABLES:
        v = pl.col("value")
        if t[name].filter(rate & ((v < 0) | (v > 1))).height:
            out.append(f"{name} has rates outside 0-1")
    if t[PERSISTENCE].filter(~pl.col("comparison").is_in(persistence.COMPARISONS)).height:
        out.append("coach_tendency_persistence names an unknown comparison")
    link = t[LINK]
    if link.filter(~pl.col("target").is_in(fantasy.TARGETS)
                   | ~pl.col("horizon").is_in(fantasy.HORIZONS)).height:  # fmt: skip
        out.append("coach_tendency_fantasy_link names an unknown target or horizon")
    for name in (PERSISTENCE, LINK):
        r = pl.col("r")
        if t[name].filter(r.is_not_null() & ((r < -1) | (r > 1))).height:
            out.append(f"{name} has correlations outside -1 to 1")
    return out
