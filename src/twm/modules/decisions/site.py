"""The Decision Report Card as the site sees it (step P3): the published columns of the graded
decisions, shared by the frozen history snapshot (:mod:`twm.modules.decisions.frozen`) and the
season in progress (:mod:`twm.modules.decisions.season`), so both have the same layout.

- :func:`fourth_rows` / :func:`try_rows`: every fourth down and try (excluded ones too, with
  their ``exclusion``: the coach tables count games from them), the play key, the context the
  pages print (clock, score, down and distance, field position), the choice, the
  recommendation, the grade and each option's WP. ``wp_lost`` keeps full precision (the coach
  tables sum it); the display probabilities are rounded to :data:`DISPLAY_DECIMALS`
  (0.01 WP points: the site prints one decimal of a percent).
- :func:`clock_rows`: the three clock metrics' rows (G4) in one table: one row per metric and
  team-game (candidates and cases), the key snap's context and the metric's amount.
- :func:`coach_slug`: the coach's id on the site (``andy-reid``).
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable

import polars as pl

DISPLAY_DECIMALS = 4
FOURTH_COLUMNS = ("game_id", "play_id", "season", "week", "season_type", "posteam", "defteam",
                  "coach", "qtr", "quarter_seconds", "score_differential", "ydstogo",
                  "yardline_100", "exclusion", "chosen", "recommended", "grade", "correct",
                  "wp_go", "wp_fg", "wp_punt", "wp_lost", "p_convert", "p_make",
                  "outcome")  # fmt: skip
TRY_COLUMNS = ("game_id", "play_id", "season", "week", "season_type", "posteam", "defteam",
               "coach", "qtr", "quarter_seconds", "score_differential", "exclusion", "chosen",
               "recommended", "grade", "correct", "wp_kick", "wp_two_point", "wp_lost",
               "result")  # fmt: skip
CLOCK_COLUMNS = ("metric", "season", "week", "season_type", "game_id", "team", "opp", "coach",
                 "play_id", "qtr", "quarter_seconds", "down", "ydstogo", "yardline_100",
                 "score_differential", "timeouts", "is_case", "amount", "wp_left", "desc",
                 "detail")  # fmt: skip
TEAM_GAME_COLUMNS = ("season", "week", "season_type", "game_id", "team", "coach")
METRICS = ("timeouts_unused", "half_passivity", "seconds_wasted")


class SiteError(ValueError):
    """Rows cannot be shaped for the site (a coach slug clash, a missing column)."""


def coach_slug(name: str) -> str:
    """'Andy Reid' -> 'andy-reid' (accents dropped, anything else between words -> '-')."""
    ascii_ = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-")
    if not slug:
        raise SiteError(f"coach name {name!r} gives an empty id")
    return slug


def coach_ids(names: Iterable[str]) -> pl.DataFrame:
    """(coach_id, name) for every distinct name; :class:`SiteError` when two names share an id."""
    uniq = sorted({str(n) for n in names if n is not None})
    out = pl.DataFrame({"coach_id": [coach_slug(n) for n in uniq], "name": uniq},
                       schema={"coach_id": pl.String, "name": pl.String})  # fmt: skip
    clash = out.filter(pl.col("coach_id").is_duplicated())
    if clash.height:
        raise SiteError(f"coaches share an id: {clash.rows()[:4]}")
    return out.sort("coach_id")


def _quarter_seconds() -> pl.Expr:
    """Seconds left in the quarter (overtime: in the period), as the pages print the clock."""
    q = pl.col("qtr")
    return (pl.when(q <= 4).then(pl.col("game_seconds_remaining") - (4 - q) * 900)
            .otherwise(pl.col("half_seconds_remaining")).cast(pl.Int32)
            .alias("quarter_seconds"))  # fmt: skip


def _shown(col: str, available: str | None = None) -> pl.Expr:
    e = pl.col(col).cast(pl.Float64).round(DISPLAY_DECIMALS)
    if available is not None:
        e = pl.when(pl.col(available).fill_null(False)).then(e).otherwise(None)
    return e.alias(col)


def fourth_rows(graded: pl.DataFrame) -> pl.DataFrame:
    """The published columns (:data:`FOURTH_COLUMNS`) of stored fourth-down rows
    (:func:`twm.modules.decisions.grade.load_graded`), sorted by the play key. An option that
    did not exist has no WP."""
    f = graded.with_columns(_quarter_seconds())
    if "grade" not in f.columns:  # a season whose rows were all excluded
        f = f.with_columns(*(pl.lit(None, pl.String).alias(c) for c in ("recommended", "grade")),
                           pl.lit(None, pl.Boolean).alias("correct"),
                           *(pl.lit(None, pl.Float64).alias(c) for c in (
                               "wp_go", "wp_fg", "wp_punt", "wp_lost", "p_convert", "p_make")),
                           *(pl.lit(None, pl.Boolean).alias(c) for c in (
                               "fg_available", "punt_available")))  # fmt: skip
    return f.select(
        *FOURTH_COLUMNS[:17], pl.col("correct").cast(pl.Boolean), _shown("wp_go"),
        _shown("wp_fg", "fg_available"), _shown("wp_punt", "punt_available"),
        pl.col("wp_lost").cast(pl.Float64), _shown("p_convert"),
        _shown("p_make", "fg_available"), "outcome",
    ).sort("game_id", "play_id")  # fmt: skip


def try_rows(graded: pl.DataFrame) -> pl.DataFrame:
    """The published columns (:data:`TRY_COLUMNS`) of stored two-point rows; ``result`` = the
    try's result ('good', 'failed', 'success', ...)."""
    f = graded.with_columns(_quarter_seconds())
    if "grade" not in f.columns:
        f = f.with_columns(*(pl.lit(None, pl.String).alias(c) for c in ("recommended", "grade")),
                           pl.lit(None, pl.Boolean).alias("correct"),
                           *(pl.lit(None, pl.Float64).alias(c) for c in (
                               "wp_kick", "wp_two_point", "wp_lost")))  # fmt: skip
    result = (pl.when(pl.col("chosen") == "two_point").then(pl.col("two_point_conv_result"))
              .otherwise(pl.col("extra_point_result")).alias("result"))  # fmt: skip
    return f.select(
        *TRY_COLUMNS[:15], pl.col("correct").cast(pl.Boolean), _shown("wp_kick"),
        _shown("wp_two_point"), pl.col("wp_lost").cast(pl.Float64), result,
    ).sort("game_id", "play_id")  # fmt: skip


def _detail(cols: list[str]) -> pl.Expr:
    """A metric's own context as a JSON object (key order fixed, so the text is stable)."""
    return pl.struct(cols).map_elements(lambda r: json.dumps(r, sort_keys=True),
                                        return_dtype=pl.String).alias("detail")  # fmt: skip


def _clock_frame(f: pl.DataFrame, metric: str, prefix: str, amount: str, detail: list[str],
                 wp_left: pl.Expr) -> pl.DataFrame:  # fmt: skip
    p = prefix
    q = pl.col("qtr") if "qtr" in f.columns else pl.lit(4)
    secs = pl.col(f"{p}game_seconds_remaining") - (4 - q) * 900
    return f.select(
        pl.lit(metric).alias("metric"), pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32), "season_type", "game_id", "team", "opp", "coach",
        pl.col(f"{p}play_id").cast(pl.Int32).alias("play_id"), q.cast(pl.Int32).alias("qtr"),
        secs.cast(pl.Int32).alias("quarter_seconds"),
        *(pl.col(f"{p}{c}").cast(pl.Int32).alias(c) for c in ("down", "ydstogo", "yardline_100",
                                                                "score_differential")),
        pl.col(f"{p}team_t" if p else "posteam_timeouts_remaining").cast(pl.Int32)
        .alias("timeouts"),
        pl.col("case").fill_null(False).alias("is_case"),
        pl.col(amount).cast(pl.Float64).alias("amount"), wp_left.cast(pl.Float64).alias("wp_left"),
        pl.col(f"{p}desc").alias("desc"), _detail(detail),
    )  # fmt: skip


M1_DETAIL = ["team_margin", "ro_k_free", "ro_k_all", "drive_missed_stops",
             "drive_seconds_wasted", "last_play_id", "last_clock"]  # fmt: skip
M2_DETAIL = ["tail_snaps", "tail_kneels", "tail_runs", "half_end_seconds", "wp_attack",
             "wp_halftime", "play_type"]  # fmt: skip
M3_DETAIL = ["team_margin", "decisive", "missed_stops", "counted", "timeouts_kept", "w_runoff"]


def clock_rows(m1: pl.DataFrame, m2: pl.DataFrame, m3: pl.DataFrame) -> pl.DataFrame:
    """One table of the clock metrics (:data:`CLOCK_COLUMNS`): metric 1's candidate team-games
    (:func:`~twm.modules.decisions.clock.timeouts_unused`; amount = timeouts left), metric 2's
    graded half-end candidates (amount = EP left, with WP left) and metric 3's team-games with
    a decisive interval (:func:`~twm.modules.decisions.clock.seconds_wasted`; amount =
    seconds wasted). The key snap: metric 1's first run-out snap, metric 2's decision snap,
    metric 3's worst counted interval."""
    parts = []
    if m1.height:
        parts.append(_clock_frame(m1, METRICS[0], "ro_", "timeouts_left", M1_DETAIL,
                                  pl.lit(None, pl.Float64)))  # fmt: skip
    if m2.height:
        m2 = m2.rename({"posteam": "team", "defteam": "opp"})
        parts.append(_clock_frame(m2, METRICS[1], "", "ep_left", M2_DETAIL, pl.col("wp_left")))
    if m3.height:
        parts.append(_clock_frame(m3, METRICS[2], "w_", "seconds_wasted", M3_DETAIL,
                                  pl.lit(None, pl.Float64)))  # fmt: skip
    schema = {c: pl.String for c in CLOCK_COLUMNS}
    if not parts:
        empty = pl.DataFrame(schema=schema)
        return empty.with_columns(
            *(pl.col(c).cast(pl.Int32) for c in ("season", "week", "play_id", "qtr",
                                                 "quarter_seconds", "down", "ydstogo",
                                                 "yardline_100", "score_differential",
                                                 "timeouts")),
            pl.col("is_case").cast(pl.Boolean),
            *(pl.col(c).cast(pl.Float64) for c in ("amount", "wp_left")))  # fmt: skip
    return pl.concat(parts, how="vertical").sort("metric", "game_id", "team")


def clock_frames(rows: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """:func:`clock_rows` back as the (m1, m2, m3) frames the clock report aggregates
    (:func:`twm.modules.decisions.clock_report.coach_season`): season, coach, case and the
    metric's amount under its own name."""
    out = []
    for metric, amount in zip(METRICS, ("timeouts_left", "ep_left", "seconds_wasted"),
                              strict=True):  # fmt: skip
        f = rows.filter(pl.col("metric") == metric)
        out.append(f.select("season", "week", "game_id", "team", "coach",
                            pl.col("is_case").alias("case"), pl.col("amount").alias(amount),
                            "wp_left"))  # fmt: skip
    return out[0], out[1], out[2]
