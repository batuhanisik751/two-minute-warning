"""Board features at the end-of-season snapshot of season S (step I1b, PROJECT_SPEC 8.6).

:func:`snapshot_features` reads everything through an :class:`~twm.asof.AsOfView` at the
snapshot (:func:`twm.modules.board.seasons.snapshot_as_of`) and returns one row per player with a
regular-season stat line in S at QB/RB/WR/TE. Only seasons <= S exist in the view, so a later
season can never leak in; the own walk-forward xFP (a file) is cut to seasons <= S here.
docs/board.md defines every column; the registry (``twm.registry``, module ``board``) holds the
formulas. Highlights:

- ``age``: years on February 1 after season S (just before every snapshot, which falls between
  February 4 and 21); ``age_at_draft``: years on the first day of his draft (his entry year's
  draft when undrafted: the day he could first have been picked);
- ``age_curve_ratio``: the position's aging curve fitted on earlier seasons only (pairs s -> s+1
  with s + 1 <= S): a quadratic in age of PPG(s+1) / PPG(s) for players ranked in the top
  :data:`CURVE_MAX_RANK` with at least 6 games in s+1, evaluated at his age (clipped to the
  fitted ages); NULL with fewer than :data:`CURVE_MIN_PAIRS` pairs;
- ``hc_departure``: a head-coach departure of his S team (his last regular-season game's team)
  in data/manual/coach_departures.csv with ``last_season`` = S announced on a day before the
  snapshot's (UTC) date; a blank date counts as known only for :data:`BLANK_KNOWN_TYPES`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from twm.config import FANTASY_POSITIONS
from twm.modules.board import seasons as bs
from twm.modules.board import sources as src
from twm.warehouse.available import DRAFT_FIRST_DAY

CURVE_MAX_RANK = 48
CURVE_MIN_PAIRS = 50
CURVE_MIN_GAMES_NEXT = 6
MIN_TOUCHES_EFFICIENCY = 20  # yards per touch of a season with fewer touches: NULL
# A blank announced date of these types counts as announced before the snapshot: they are
# announced at or right after the team's last game, and of the 117 such rows WITH a date none
# was announced on or after its season's snapshot (2002-2025). Other types (retired, resigned,
# left_for_other_job, other: 2 of their 17 dated rows came after it) count as not known.
BLANK_KNOWN_TYPES = ("fired_in_season", "fired_after_season", "mutual_parting",
                     "interim_not_retained")  # fmt: skip
AGE_MONTH_DAY = (2, 1)  # ages are taken on February 1 after the season
ID_COLUMNS = ("gsis_id", "season", "position", "team", "entry_year", "games", "points", "ppg",
              "pos_rank", "snapshot")  # fmt: skip
# Every board feature (registered in twm.registry, module "board"); models pick subsets.
FEATURES: tuple[str, ...] = (
    "pos_rb", "pos_wr", "pos_te", "age", "age_curve_ratio", "prior_seasons", "games_s",
    "ppg_s", "pos_rank_s", "ppg_change", "touches_s", "touches_per_game_s", "career_touches",
    "career_targets", "yards_per_touch_s", "yards_per_touch_trend", "snap_pct_s",
    "snap_pct_trend", "ngs_separation_s", "ngs_separation_trend", "ngs_ryoe_per_att_s",
    "ngs_ryoe_trend", "xfp_per_game_s", "fpoe_per_game_s", "hc_departure", "target_share_s",
    "target_share_rookie", "yards_per_team_pass_att_s", "air_yards_share_s", "rush_share_s",
    "drafted_round", "drafted_pick", "undrafted", "age_at_draft", "combine_forty",
    "combine_weight", "combine_height", "combine_vertical", "combine_broad_jump",
    "combine_speed_score", "team_any_a", "third_season",
)  # fmt: skip


@dataclass(frozen=True)
class Departures:
    """Head-coach departures (team, season = the coach's last season, departure_type,
    announced: NULL when the file's date is blank)."""

    rows: pl.DataFrame

    @property
    def n_blank_date(self) -> int:
        return int(self.rows.get_column("announced").null_count())

    @property
    def n_blank_unknown(self) -> int:
        """Blank-dated rows never counted as known (a type outside BLANK_KNOWN_TYPES)."""
        r = self.rows.filter(pl.col("announced").is_null())
        return int((~r.get_column("departure_type").is_in(list(BLANK_KNOWN_TYPES))).sum())


def read_departures(path: Path | str) -> Departures:
    from twm.modules.hot_seat.labels import read_labels

    df = read_labels(path).select(
        "team",
        pl.col("last_season").cast(pl.Int32).alias("season"),
        "departure_type",
        pl.when(pl.col("announced_date") == "")
        .then(None)
        .otherwise(pl.col("announced_date"))
        .str.to_date("%Y-%m-%d")
        .alias("announced"),
    )
    return Departures(df.sort("season", "team", "departure_type"))


def years_between(born: pl.Expr, on: pl.Expr) -> pl.Expr:
    return ((on - born).dt.total_days() / 365.25).round(4)


def age_on_feb1(season: pl.Expr, born: pl.Expr) -> pl.Expr:
    m, d = AGE_MONTH_DAY
    return years_between(born, pl.date(season + 1, m, d))


def draft_day(year: pl.Expr) -> pl.Expr:
    """The first day of ``year``'s draft (available.DRAFT_FIRST_DAY, 2000-2026); NULL else."""
    days = {int(y): d for y, d in DRAFT_FIRST_DAY.items()}
    return year.replace_strict(days, default=None, return_dtype=pl.Date)


@dataclass(frozen=True)
class Curve:
    """One position's aging curve: PPG(s+1) / PPG(s) = c0 x age^2 + c1 x age + c2."""

    coef: tuple[float, float, float]
    age_min: float
    age_max: float
    n_pairs: int

    def at(self, age: np.ndarray) -> np.ndarray:
        return np.polyval(np.asarray(self.coef), np.clip(age, self.age_min, self.age_max))


def age_curves(ps: pl.DataFrame, born: pl.DataFrame, last_season: int) -> dict[str, Curve]:
    """Per position, the curve fitted on the season pairs (s, s+1) with s + 1 <= ``last_season``
    (players in the top CURVE_MAX_RANK in s, at least CURVE_MIN_GAMES_NEXT games in s+1)."""
    cur = ps.filter(
        pl.col("pos_rank") <= CURVE_MAX_RANK, pl.col("season") + 1 <= int(last_season)
    ).select("gsis_id", "season", "position", "ppg")
    nxt = ps.select(
        "gsis_id", (pl.col("season") - 1).alias("season"), pl.col("games").alias("g1"),
        pl.col("ppg").alias("p1"),
    )  # fmt: skip
    pairs = (
        cur.join(nxt, on=["gsis_id", "season"])
        .filter(pl.col("g1") >= CURVE_MIN_GAMES_NEXT, pl.col("ppg") > 0)
        .join(born.select("gsis_id", "birth_date"), on="gsis_id")
        .with_columns(
            age_on_feb1(pl.col("season"), pl.col("birth_date")).alias("age"),
            (pl.col("p1") / pl.col("ppg")).alias("ratio"),
        )
        .drop_nulls(["age", "ratio"])
        .sort("position", "season", "gsis_id")
    )
    out = {}
    for pos in FANTASY_POSITIONS:
        p = pairs.filter(pl.col("position") == pos)
        if p.height < CURVE_MIN_PAIRS:
            continue
        age, ratio = p.get_column("age").to_numpy(), p.get_column("ratio").to_numpy()
        c = np.polyfit(age, ratio, 2)
        out[pos] = Curve((float(c[0]), float(c[1]), float(c[2])), float(age.min()),
                         float(age.max()), p.height)  # fmt: skip
    return out


def apply_curves(df: pl.DataFrame, curves: Mapping[str, Curve]) -> pl.Series:
    """``age_curve_ratio`` for each row of ``df`` (columns position, age)."""
    out = np.full(df.height, np.nan)
    pos = df.get_column("position").to_numpy()
    age = df.get_column("age").cast(pl.Float64).fill_null(np.nan).to_numpy()
    for p, curve in curves.items():
        sel = (pos == p) & np.isfinite(age)
        out[sel] = curve.at(age[sel])
    return pl.Series("age_curve_ratio", np.round(out, 6), dtype=pl.Float64).fill_nan(None)


def _ratio(num: str, den: str) -> pl.Expr:
    return pl.when(pl.col(den) > 0).then(pl.col(num) / pl.col(den)).otherwise(None)


def season_metrics(ps: pl.DataFrame) -> pl.DataFrame:
    """Per player-season usage and efficiency (the inputs of the S, S-1, S-2 columns)."""
    touches = pl.col("carries") + pl.col("receptions")
    yards = pl.col("rushing_yards") + pl.col("receiving_yards")
    return ps.with_columns(
        touches.alias("touches"),
        pl.when(touches >= MIN_TOUCHES_EFFICIENCY).then(yards / touches).alias("ypt"),
        _ratio("targets", "team_targets").alias("target_share"),
        _ratio("receiving_yards", "team_pass_attempts").alias("yptpa"),
        _ratio("air_yards", "team_air_yards").alias("air_share"),
        _ratio("carries", "team_carries").alias("rush_share"),
    )


def _lag(df: pl.DataFrame, k: int, cols: dict[str, str]) -> pl.DataFrame:
    """Columns of season - k renamed (``cols``: source -> new name), keyed to the later season."""
    return df.select(
        "gsis_id", (pl.col("season") + k).alias("season"),
        *(pl.col(a).alias(b) for a, b in cols.items()),
    )  # fmt: skip


def hc_departure_teams(departures: Departures, season: int, snapshot: datetime) -> set[str]:
    """Teams whose head coach's departure after ``season`` is known at the snapshot: announced
    on a day before the snapshot's (UTC) date, or a blank date of a BLANK_KNOWN_TYPES type."""
    r = departures.rows.filter(pl.col("season") == int(season))
    known = (pl.col("announced") < snapshot.date()) | (
        pl.col("announced").is_null() & pl.col("departure_type").is_in(list(BLANK_KNOWN_TYPES))
    )
    return set(r.filter(known).get_column("team").to_list())


def snapshot_features(
    view: Any, season: int, *, xfp_games: pl.DataFrame | None, departures: Departures
) -> pl.DataFrame:
    """One row per QB/RB/WR/TE with a regular-season stat line in ``season``: the
    :data:`ID_COLUMNS` and every board feature, from ``view`` (the season's snapshot)."""
    s, snap = int(season), view.as_of
    ps = bs.player_seasons(view, s)
    born = src.players(view)
    m = season_metrics(ps)
    cur = m.filter(pl.col("season") == s, pl.col("position").is_in(list(FANTASY_POSITIONS)))
    prev = _lag(m, 1, {"ppg": "ppg_prev", "ypt": "ypt_prev"})
    prev2 = _lag(m, 2, {"ypt": "ypt_prev2"})
    career = (
        m.group_by("gsis_id")
        .agg(
            pl.col("touches").sum().alias("career_touches"),
            pl.col("targets").sum().alias("career_targets"),
        )  # fmt: skip
        .with_columns(pl.lit(s, dtype=pl.Int32).alias("season"))
    )
    rookie = m.filter(pl.col("season") == pl.col("entry_year")).select(
        "gsis_id", pl.col("target_share").alias("target_share_rookie")
    )
    ngs = src.ngs_seasons(view, s)
    ngs_prev = _lag(ngs, 1, {"ngs_separation": "sep_prev", "ngs_ryoe_per_att": "ryoe_prev"})
    snaps = src.snap_seasons(view, s)
    snaps_prev = _lag(snaps, 1, {"snap_share": "snap_prev"})
    xfp = src.xfp_seasons(xfp_games, s) if xfp_games is not None else None
    teams = bs.team_seasons(view, s).filter(pl.col("season") == s).drop("season")
    hc = hc_departure_teams(departures, s, snap)
    keys = ["gsis_id", "season"]
    df = (
        cur.join(prev, on=keys, how="left").join(prev2, on=keys, how="left")
        .join(career, on=keys, how="left").join(rookie, on="gsis_id", how="left")
        .join(ngs, on=keys, how="left").join(ngs_prev, on=keys, how="left")
        .join(snaps, on=keys, how="left").join(snaps_prev, on=keys, how="left")
        .join(born, on="gsis_id", how="left").join(src.combine(view), on="gsis_id", how="left")
        .join(teams, on="team", how="left")
    )  # fmt: skip
    if xfp is not None and s >= src.XFP_FIRST_SEASON:
        df = df.join(xfp, on=keys, how="left")
    else:
        df = df.with_columns(pl.lit(None, dtype=pl.Float64).alias("xfp_total"))
    df = df.with_columns(age_on_feb1(pl.col("season"), pl.col("birth_date")).alias("age"))
    df = df.with_columns(apply_curves(df, age_curves(ps, born, s)))
    return _finish(df, hc, snap)


def _diff(a: str, b: str) -> pl.Expr:
    return pl.col(a) - pl.col(b)


def _finish(df: pl.DataFrame, hc: set[str], snapshot: datetime) -> pl.DataFrame:
    """The registered feature columns from the joined inputs."""
    c = pl.col
    early = pl.mean_horizontal("ypt_prev", "ypt_prev2")  # NULL only when both are NULL
    out = df.with_columns(
        *((c("position") == p).alias(f"pos_{p.lower()}") for p in ("RB", "WR", "TE")),
        (c("season") - c("entry_year")).alias("prior_seasons"),
        c("games").alias("games_s"), c("ppg").alias("ppg_s"), c("pos_rank").alias("pos_rank_s"),
        _diff("ppg", "ppg_prev").alias("ppg_change"),
        c("touches").alias("touches_s"), (c("touches") / c("games")).alias("touches_per_game_s"),
        c("ypt").alias("yards_per_touch_s"), (c("ypt") - early).alias("yards_per_touch_trend"),
        c("snap_share").alias("snap_pct_s"), _diff("snap_share", "snap_prev").alias(
            "snap_pct_trend"),
        c("ngs_separation").alias("ngs_separation_s"),
        _diff("ngs_separation", "sep_prev").alias("ngs_separation_trend"),
        c("ngs_ryoe_per_att").alias("ngs_ryoe_per_att_s"),
        _diff("ngs_ryoe_per_att", "ryoe_prev").alias("ngs_ryoe_trend"),
        (c("xfp_total") / c("games")).alias("xfp_per_game_s"),
        (c("ppg") - c("xfp_total") / c("games")).alias("fpoe_per_game_s"),
        c("team").is_in(sorted(hc)).fill_null(False).alias("hc_departure"),
        c("target_share").alias("target_share_s"),
        c("yptpa").alias("yards_per_team_pass_att_s"), c("air_share").alias("air_yards_share_s"),
        c("rush_share").alias("rush_share_s"),
        c("draft_round").alias("drafted_round"), c("draft_pick").alias("drafted_pick"),
        c("draft_round").is_null().alias("undrafted"),
        years_between(c("birth_date"), draft_day(pl.coalesce("draft_year", "entry_year"))).alias(
            "age_at_draft"),
        c("forty").alias("combine_forty"), c("weight").alias("combine_weight"),
        c("height_in").alias("combine_height"), c("vertical").alias("combine_vertical"),
        c("broad_jump").alias("combine_broad_jump"), c("speed_score").alias("combine_speed_score"),
        (c("season") - c("entry_year") == 1).alias("third_season"),
        pl.lit(snapshot.replace(tzinfo=None)).alias("snapshot"),
    )  # fmt: skip
    floats = [n for n, t in out.schema.items() if t == pl.Float64 and n in FEATURES]
    return (
        out.with_columns(pl.col(floats).round(6))
        .select(*ID_COLUMNS, *FEATURES)
        .sort("season", "gsis_id")
    )
