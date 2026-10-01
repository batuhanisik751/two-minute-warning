"""Hot-Seat H3b: the label of every feature row (PROJECT_SPEC 8.5), from the owner's file.

The departures (``data/manual/coach_departures.csv``, docs/labeling_coaches.md) are joined to
the feature rows (``data/hot_seat/features.parquet``, docs/hot_seat.md) by coach x team x
season: a schedule row through its ``candidate_id`` (the candidates file gives its
``coach_id``), a ``source_only`` row through team + season + the coach's name (``dim_coach``).
Every date is a US-Eastern calendar day: a weekly row's day is its as-of's Eastern date, the
end-of-season row's day is the team's last regular-season game day (``fact_game.gameday``),
the owner-decided anchor (docs/hot_seat.md). The window ends 30 days after the team's final
game of the season, playoffs included (inclusive).

For a row of day ``d`` (the coach's departure from that team, announced on day ``a``):

- ``a < d``: the row is dropped (he is gone);
- weekly row of the last regular-season week: dropped (its Tuesday as-of follows Black Monday;
  the end-of-season row covers it);
- ``y = 1`` when the departure is positive and ``d <= a <= window end``;
- a non-positive departure in that span: ``y = 0``, ``censored = True``;
- a positive departure after the window: ``y = 0`` (counted);
- hazard ``event = 1`` when a positive departure is announced in ``[d, next row's day)``, or
  ``[d, window end]`` for the coach's last row of the season;
- ``is_interim`` (took over mid-season, or the owner's file says interim): kept and flagged, the
  models leave these rows out of training and report them separately;
- departure type blank: the coach-season's rows are dropped (counted).

Modes: ``verified`` uses only rows with ``verified_by_owner = y`` and refuses to run while any
departure that a feature row needs is unverified (:class:`UnverifiedLabelsError`);
``suggested`` uses the prefilled values (provisional: written only to
``data/hot_seat/provisional/``). A blank date, in BOTH modes (the owner may verify a row and
leave the date blank when no source gives the day, docs/labeling_coaches.md), is imputed as
the coach's last listed game day (``last_game_date``, the earliest day the departure can be
announced), flagged (``date_imputed``), counted (``departures_date_imputed``) and printed.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import polars as pl

from twm.modules.hot_seat.features import add_interim_flag
from twm.modules.hot_seat.labels import POSITIVE_TYPES

LABEL_MODES = ("verified", "suggested")
WINDOW_DAYS = 30  # spec 8.5: announced no later than 30 days after the team's final game
EASTERN = "America/New_York"
RUP = "resigned_under_pressure"
# label variants: the main run (spec default, owner 2026-10-01) and the decision-point-4 run
POSITIVE_SETS: dict[str, tuple[str, ...]] = {
    "main": POSITIVE_TYPES,
    "rup_positive": (*POSITIVE_TYPES, RUP),
}
DEPARTURE_COLUMNS = (
    "season", "team", "coach_id", "candidate_id", "departure_type", "announced",
    "date_imputed", "verified",
)  # fmt: skip


class UnverifiedLabelsError(ValueError):
    """Verified mode, but a departure that a feature row needs is not verified yet."""


TEAM_DATES_SQL = """
WITH g AS (
  SELECT season, game_type, gameday, home_team AS team FROM fact_game WHERE result IS NOT NULL
  UNION ALL
  SELECT season, game_type, gameday, away_team FROM fact_game WHERE result IS NOT NULL
)
SELECT season, team,
       max(gameday) FILTER (WHERE game_type = 'REG') AS last_reg_date,
       max(gameday) AS final_date
FROM g WHERE season BETWEEN ? AND ? GROUP BY season, team ORDER BY season, team
"""


def load_team_dates(db: Path | str, first: int, last: int) -> pl.DataFrame:
    """Per team-season (played games): the last regular-season game day and the final game
    day, playoffs included (``fact_game.gameday``, the Eastern date)."""
    from twm.warehouse.build import connect

    con = connect(db, read_only=True)
    try:
        df = con.execute(TEAM_DATES_SQL, [first, last]).pl()
    finally:
        con.close()
    return df.with_columns(pl.col("season").cast(pl.Int32))


def load_coach_names(db: Path | str) -> pl.DataFrame:
    """``dim_coach`` (coach_id, coach_name): resolves ``source_only`` rows by name."""
    from twm.warehouse.build import connect

    con = connect(db, read_only=True)
    try:
        return con.execute("SELECT coach_id, coach_name FROM dim_coach").pl()
    finally:
        con.close()


def _parse_dates(col: str) -> pl.Expr:
    return pl.when(pl.col(col) == "").then(None).otherwise(pl.col(col)).str.to_date("%Y-%m-%d")


def resolve_departures(
    labels: pl.DataFrame,
    candidates: pl.DataFrame,
    coach_names: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """The owner's rows as (season, team, coach_id) departures with a parsed ``announced``
    date (a blank one imputed from ``last_game_date``, flagged ``date_imputed``) and
    ``verified``. Returns (departures, unresolved): rows whose coach_id cannot be found."""
    ids = candidates.select("candidate_id", pl.col("coach_id").alias("_cid"))
    df = labels.join(ids, on="candidate_id", how="left")
    if coach_names is not None and coach_names.height:
        names = coach_names.select(pl.col("coach_name"), pl.col("coach_id").alias("_nid"))
        df = df.join(names.unique("coach_name", keep="first"), on="coach_name", how="left")
    else:
        df = df.with_columns(pl.lit(None, dtype=pl.String).alias("_nid"))
    df = df.with_columns(
        pl.when(pl.col("origin") == "source_only")
        .then(pl.col("_nid"))
        .otherwise(pl.col("_cid"))
        .alias("coach_id"),
        _parse_dates("announced_date").alias("_ann"),
        _parse_dates("last_game_date").alias("_last"),
    )
    unresolved = df.filter(pl.col("coach_id").is_null()).select(labels.columns)
    out = (
        df.filter(pl.col("coach_id").is_not_null())
        .with_columns(
            pl.col("last_season").cast(pl.Int32).alias("season"),
            pl.col("_ann").is_null().alias("date_imputed"),
            pl.coalesce("_ann", "_last").alias("announced"),
            (pl.col("verified_by_owner") == "y").alias("verified"),
        )
        .select(DEPARTURE_COLUMNS)
        .sort("season", "team", "coach_id")
    )
    dup = out.group_by("season", "team", "coach_id").len().filter(pl.col("len") > 1)
    if dup.height:
        raise ValueError(f"two departures for one coach-team-season: {dup.rows()}")
    return out, unresolved


def refresh_interim(
    features: pl.DataFrame, labels: pl.DataFrame | None, candidates: pl.DataFrame | None
) -> pl.DataFrame:
    """``is_interim`` recomputed from the labels in use (features.add_interim_flag)."""
    base = features.drop("is_interim") if "is_interim" in features.columns else features
    return add_interim_flag(base, labels, candidates)


def row_days(features: pl.DataFrame, team_dates: pl.DataFrame) -> pl.DataFrame:
    """Each row's Eastern day (weekly: the as-of's date; end of season: the last regular-season
    game day) and its window end (final game day + 30), for team-seasons in ``team_dates``."""
    df = features.join(team_dates, on=["season", "team"], how="inner")
    eastern = pl.col("as_of").dt.convert_time_zone(EASTERN).dt.date()
    return df.with_columns(
        pl.when(pl.col("snapshot") == "end_of_season")
        .then(pl.col("last_reg_date"))
        .otherwise(eastern)
        .alias("day"),
        (pl.col("final_date") + pl.duration(days=WINDOW_DAYS)).alias("window_end"),
    )


def build_targets(
    features: pl.DataFrame,
    departures: pl.DataFrame,
    team_dates: pl.DataFrame,
    *,
    mode: str,
    positive_types: Sequence[str] = POSITIVE_TYPES,
) -> tuple[pl.DataFrame, dict[str, int]]:
    """The labelled rows (module docstring) and the counts behind them."""
    if mode not in LABEL_MODES:
        raise ValueError(f"mode must be one of {LABEL_MODES}, not {mode!r}")
    df = row_days(features, team_dates)
    n: dict[str, int] = {"rows_in": df.height}
    last_week = (pl.col("snapshot") == "weekly") & pl.col("is_last_reg_week")
    n["dropped_last_reg_week"] = int(df.select(last_week.sum()).item())
    df = df.filter(~last_week)
    keys = ["season", "team", "coach_id"]
    dep = departures.select(*keys, "departure_type", "announced", "date_imputed", "verified")
    df = df.join(dep, on=keys, how="left")
    needed = df.filter(pl.col("verified").is_not_null()).unique(keys)
    n["departures_joined"] = needed.height
    n["departures_unverified"] = int((~needed["verified"]).sum())
    if mode == "verified" and n["departures_unverified"]:
        raise UnverifiedLabelsError(
            f"{n['departures_unverified']} of the {needed.height} departures the feature rows "
            "need are not verified by the owner (verified_by_owner = y); run with "
            "--labels suggested for a provisional run, or finish H2 (docs/labeling_coaches.md)"
        )
    n["departures_date_imputed"] = int(needed["date_imputed"].sum())
    blank = pl.col("departure_type") == ""
    n["departures_blank_type"] = int(needed.select(blank.sum()).item())
    n["dropped_blank_type"] = int(df.select(blank.fill_null(False).sum()).item())
    df = df.filter(~blank.fill_null(False))
    df = df.sort([*keys, "day"]).with_columns(pl.col("day").shift(-1).over(keys).alias("next_day"))
    gone = (pl.col("announced") < pl.col("day")).fill_null(False)
    n["dropped_gone"] = int(df.select(gone.sum()).item())
    df = df.filter(~gone)
    pos = pl.col("departure_type").is_in(list(positive_types)).fill_null(False)
    within = (pl.col("announced") <= pl.col("window_end")).fill_null(False)
    before_next = pl.col("next_day").is_null() | (pl.col("announced") < pl.col("next_day"))
    df = df.with_columns(
        (pos & within).cast(pl.Int8).alias("y"),
        (~pos & pl.col("announced").is_not_null() & within).alias("censored"),
        (pos & within & before_next.fill_null(False)).cast(pl.Int8).alias("event"),
    )
    outside = needed.join(team_dates, on=["season", "team"]).filter(
        pl.col("departure_type").is_in(list(positive_types))
        & (pl.col("announced") > pl.col("final_date") + pl.duration(days=WINDOW_DAYS))
    )
    n["positive_outside_window"] = outside.height
    n["rows_out"] = df.height
    n["interim_rows"] = int(df["is_interim"].sum())
    n["censored_rows"] = int(df["censored"].sum())
    return df, n
