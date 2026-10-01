"""Typed access to config/*.yaml. Loaded once, validated with pydantic."""

from __future__ import annotations

import contextlib
import functools
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, PrivateAttr, field_validator, model_validator

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"


class Paths(BaseModel):
    raw_cache: str
    schemas: str
    warehouse: str
    league_db: str
    manual: str = "data/manual"  # committed, hand-curated files (player_id_overrides.csv ...)
    predictions: str = "data/predictions.duckdb"  # the predictions store (twm.predictions)
    models: str = "models"  # trained production models (twm.modules.waiver_radar.production)


# Datasets whose rows are stamped "game end + lag" (twm.warehouse.available).
GAME_DATA_DATASETS = ("pbp", "player_stats", "team_stats", "snap_counts")
MAX_GAME_DATA_LAG_HOURS = 48
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _check_month_day(name: str, v: str) -> str:
    try:
        # 2001 is not a leap year: 02-29 would not exist in most seasons
        datetime.strptime(f"2001-{v}", "%Y-%m-%d")
    except ValueError as e:
        raise ValueError(f"{name} {v!r} is not a valid 'MM-DD'") from e
    return v


def _check_hhmm(name: str, v: str) -> str:
    try:
        datetime.strptime(v, "%H:%M")
    except ValueError as e:
        raise ValueError(f"{name} {v!r} is not a valid 'HH:MM'") from e
    return v


class ScheduleException(BaseModel):
    """One schedule change baked into nflverse's final schedule (``schedule_exceptions``).

    Without ``announced``, the game's row and slot count as public only from its own kickoff:
    a game is always announced before it is played, so this is safe with no date at all.
    ``announced`` (the day the change was made public, or a later day) may be added only with a
    ``source`` link that shows it; the row is then public from 12:00 UTC the day after.
    ``cancelled``: the game was never played and is absent from nflverse (2022 W17 BUF-CIN).
    """

    model_config = ConfigDict(extra="forbid")

    game_id: str
    what: str
    announced: str | None = None
    source: str | None = None
    cancelled: bool = False

    @field_validator("announced")
    @classmethod
    def _date(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError as e:
            raise ValueError(f"schedule_exceptions announced {v!r} is not 'YYYY-MM-DD'") from e
        return v

    @model_validator(mode="after")
    def _dated_needs_source(self) -> ScheduleException:
        if self.announced is not None and not (self.source or "").startswith("http"):
            raise ValueError(
                f"schedule_exceptions {self.game_id}: an announced date needs a source URL "
                "(dates are never typed from memory; without one the kickoff rule applies)"
            )
        return self


class AvailabilityConfig(BaseModel):
    """``availability:`` in settings.yaml: the knobs of the ``available_at`` rules (B2).

    Validated strictly (unknown keys are refused) because a typo here would silently change
    what every backtest is allowed to see.
    """

    model_config = ConfigDict(extra="forbid")

    game_data_lag_hours: dict[str, float]
    game_result_lag_hours: float
    schedule_release_month_day: str
    schedule_slot_lead_days: int
    estimated_kickoff_for_availability_et: dict[str, str]
    injury_legacy_stamp_offset_hours: float
    draft_public_month_day: str
    schedule_exceptions: list[ScheduleException]
    roster_postgame_share_threshold: float

    @field_validator("roster_postgame_share_threshold")
    @classmethod
    def _share(cls, v: float) -> float:
        if not 0 < v < 1:
            raise ValueError(f"roster_postgame_share_threshold = {v}: must be between 0 and 1")
        return v

    @field_validator("game_data_lag_hours")
    @classmethod
    def _lags(cls, v: dict[str, float]) -> dict[str, float]:
        if set(v) != set(GAME_DATA_DATASETS):
            raise ValueError(
                f"game_data_lag_hours needs exactly the keys {list(GAME_DATA_DATASETS)}, "
                f"got {sorted(v)}"
            )
        for name, hours in v.items():
            if not 0 <= hours <= MAX_GAME_DATA_LAG_HOURS:
                raise ValueError(
                    f"game_data_lag_hours.{name} = {hours}: must be between 0 and "
                    f"{MAX_GAME_DATA_LAG_HOURS} hours"
                )
        return v

    @field_validator("game_result_lag_hours", "injury_legacy_stamp_offset_hours")
    @classmethod
    def _hours(cls, v: float, info: Any) -> float:
        if not 0 <= v <= MAX_GAME_DATA_LAG_HOURS:
            raise ValueError(
                f"{info.field_name} = {v}: must be between 0 and {MAX_GAME_DATA_LAG_HOURS} hours"
            )
        return v

    @field_validator("schedule_slot_lead_days")
    @classmethod
    def _lead(cls, v: int) -> int:
        if not 0 <= v <= 60:
            raise ValueError(f"schedule_slot_lead_days = {v}: must be between 0 and 60")
        return v

    @field_validator("schedule_release_month_day", "draft_public_month_day")
    @classmethod
    def _month_day(cls, v: str, info: Any) -> str:
        return _check_month_day(info.field_name, v)

    @field_validator("estimated_kickoff_for_availability_et")
    @classmethod
    def _slots(cls, v: dict[str, str]) -> dict[str, str]:
        if "default" not in v:
            raise ValueError("estimated_kickoff_for_availability_et needs a 'default' slot")
        unknown = sorted(set(v) - {"default", *WEEKDAY_NAMES})
        if unknown:
            raise ValueError(
                f"estimated_kickoff_for_availability_et: unknown keys {unknown} (use weekday "
                "names such as Monday, or default)"
            )
        for day, hhmm in v.items():
            _check_hhmm(f"estimated_kickoff_for_availability_et.{day}", hhmm)
        return v

    @field_validator("schedule_exceptions")
    @classmethod
    def _unique(cls, v: list[ScheduleException]) -> list[ScheduleException]:
        ids = [x.game_id for x in v]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"schedule_exceptions lists {dupes} more than once")
        return v


def _check_wp_band(low: float, high: float, name: str) -> None:
    if not 0.0 < low < high < 1.0:
        raise ValueError(f"{name}: need 0 < wp_low < wp_high < 1, got {low} and {high}")


class GarbageTimeConfig(BaseModel):
    """PROJECT_SPEC 7.3: a play is garbage time when the offense's win probability is below
    ``wp_low`` or above ``wp_high``, except in the final ``exclude_final_seconds_half`` seconds
    of a half when the score is within ``one_possession_points``."""

    model_config = ConfigDict(extra="forbid")

    wp_low: float
    wp_high: float
    exclude_final_seconds_half: int
    one_possession_points: int

    @model_validator(mode="after")
    def _valid(self) -> GarbageTimeConfig:
        _check_wp_band(self.wp_low, self.wp_high, "garbage_time")
        if not 0 <= self.exclude_final_seconds_half <= 1800:
            raise ValueError("garbage_time.exclude_final_seconds_half must be 0-1800 seconds")
        if not 0 <= self.one_possession_points <= 30:
            raise ValueError("garbage_time.one_possession_points must be 0-30")
        return self


class NeutralConfig(BaseModel):
    """PROJECT_SPEC 7.3: the stricter "neutral situation" filter (rbsdm style): the offense's
    win probability within [wp_low, wp_high] and more than ``min_half_seconds_remaining``
    seconds left in the half."""

    model_config = ConfigDict(extra="forbid")

    wp_low: float
    wp_high: float
    min_half_seconds_remaining: int

    @model_validator(mode="after")
    def _valid(self) -> NeutralConfig:
        _check_wp_band(self.wp_low, self.wp_high, "neutral")
        if not 0 <= self.min_half_seconds_remaining <= 1800:
            raise ValueError("neutral.min_half_seconds_remaining must be 0-1800 seconds")
        return self


WEEKDAY_KEYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class WeeklyAsOf(BaseModel):
    """``as_of.weekly``: the official weekly as-of, the first <weekday> <time> UTC after the
    Eastern date of the week's first kickoff (PROJECT_SPEC 6.1: Tuesday 14:00 UTC)."""

    model_config = ConfigDict(extra="forbid")

    weekday: str
    time: str

    @field_validator("weekday")
    @classmethod
    def _weekday(cls, v: str) -> str:
        if v.strip().lower() not in WEEKDAY_KEYS:
            raise ValueError(f"as_of.weekly.weekday {v!r} is not a weekday name (e.g. tuesday)")
        return v.strip().lower()

    @field_validator("time")
    @classmethod
    def _time(cls, v: str) -> str:
        return _check_hhmm("as_of.weekly.time", v)


class EndOfSeasonAsOf(BaseModel):
    """``as_of.hot_seat_end_of_season``: <days_after> days after the last game day of the last
    regular-season week, at <time> UTC (the Hot-Seat snapshot)."""

    model_config = ConfigDict(extra="forbid")

    anchor: Literal["last_reg_week"]
    days_after: int = 1
    time: str = "12:00"

    @field_validator("days_after")
    @classmethod
    def _days(cls, v: int) -> int:
        if not 0 <= v <= 14:
            raise ValueError(f"as_of.hot_seat_end_of_season.days_after = {v}: must be 0-14")
        return v

    @field_validator("time")
    @classmethod
    def _time(cls, v: str) -> str:
        return _check_hhmm("as_of.hot_seat_end_of_season.time", v)


class BoardAsOf(BaseModel):
    """``as_of.board``: the Cliff & Breakout Board's two snapshots (P3)."""

    model_config = ConfigDict(extra="forbid")

    post_draft: str
    preseason: Literal["tuesday_before_week_1"]

    @field_validator("post_draft")
    @classmethod
    def _month_day(cls, v: str) -> str:
        return _check_month_day("as_of.board.post_draft", v)


class AsOfConfig(BaseModel):
    """``as_of:`` in settings.yaml (PROJECT_SPEC 6.1), validated when the config loads: a typo
    would move every official as-of, so unknown keys are refused."""

    model_config = ConfigDict(extra="forbid")

    weekly: WeeklyAsOf
    hot_seat_end_of_season: EndOfSeasonAsOf
    board: BoardAsOf

    @field_validator("hot_seat_end_of_season", mode="before")
    @classmethod
    def _mapping(cls, v: Any) -> Any:
        if not isinstance(v, Mapping):
            raise ValueError(
                "as_of.hot_seat_end_of_season must be a mapping with anchor: last_reg_week "
                f"(and days_after, time), not {v!r}"
            )
        return v


def _weekday_time(name: str, v: str) -> tuple[str, str]:
    """'tuesday 15:17' -> ('tuesday', '15:17'), validated."""
    parts = str(v).strip().lower().split()
    if len(parts) != 2 or parts[0] not in WEEKDAY_KEYS:
        raise ValueError(f"{name} {v!r} is not '<weekday> HH:MM' (e.g. 'tuesday 15:17')")
    return parts[0], _check_hhmm(name, parts[1])


class SeasonWindow(BaseModel):
    """``pipeline.season_window``: the job runs every night from ``days_before_first_game`` days
    before the current season's first regular-season game day to ``days_after_last_game`` days
    after its last one (the dates come from the schedule, never typed here)."""

    model_config = ConfigDict(extra="forbid")

    days_before_first_game: int = 7
    days_after_last_game: int = 10


class PipelineConfig(BaseModel):
    """``pipeline:`` in settings.yaml: the scheduled job (step E4, `twm pipeline run`,
    .github/workflows/pipeline.yml; docs/deploy.md 'The scheduled pipeline'). The workflow's
    cron lines must match ``nightly`` and ``retry_attempts`` (tests/test_workflows.py)."""

    model_config = ConfigDict(extra="forbid")

    season_window: SeasonWindow = SeasonWindow()
    offseason_weekday: str = "tuesday"
    nightly: str = "10:47"
    retry_attempts: list[str] = ["tuesday 15:17", "tuesday 18:47", "tuesday 21:17",
                                 "wednesday 03:17"]  # fmt: skip
    build_start: int = 2012

    @field_validator("offseason_weekday")
    @classmethod
    def _offseason_weekday(cls, v: str) -> str:
        if v.strip().lower() not in WEEKDAY_KEYS:
            raise ValueError(f"pipeline.offseason_weekday {v!r} is not a weekday name")
        return v.strip().lower()

    @field_validator("nightly")
    @classmethod
    def _nightly(cls, v: str) -> str:
        return _check_hhmm("pipeline.nightly", v)

    @field_validator("retry_attempts")
    @classmethod
    def _attempts(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("pipeline.retry_attempts needs at least one attempt")
        return [" ".join(_weekday_time("pipeline.retry_attempts", a)) for a in v]

    def attempts(self) -> list[tuple[str, str]]:
        """The retry attempts as (weekday, 'HH:MM')."""
        return [_weekday_time("pipeline.retry_attempts", a) for a in self.retry_attempts]


class PublishConfig(BaseModel):
    """``publish:`` in settings.yaml (`twm publish`, docs/deploy.md)."""

    model_config = ConfigDict(extra="forbid")

    # A replaced table may lose at most this share of the rows the target holds; a bigger drop
    # means the local inputs are missing or broken, and the publish is refused (--allow-shrink).
    max_shrink_share: float = 0.10

    @field_validator("max_shrink_share")
    @classmethod
    def _share(cls, v: float) -> float:
        if not 0 <= v < 1:
            raise ValueError(f"publish.max_shrink_share must be in [0, 1), not {v}")
        return v


class Settings(BaseModel):
    project_name: str
    current_season: int
    seasons: dict[str, Any]
    as_of: AsOfConfig
    horizons: dict[str, int]
    garbage_time: GarbageTimeConfig
    neutral: NeutralConfig
    paths: Paths
    availability: AvailabilityConfig
    pipeline: PipelineConfig = PipelineConfig()
    publish: PublishConfig = PublishConfig()

    def path(self, key: str) -> Path:
        return ROOT / getattr(self.paths, key)


# The stats config/scoring.yaml may score, per section (src/twm/scoring.py maps each to columns).
SCORING_STATS: dict[str, tuple[str, ...]] = {
    "passing": ("yards", "touchdowns", "interceptions", "two_point_conversions"),
    "rushing": ("yards", "touchdowns", "two_point_conversions"),
    "receiving": ("receptions", "yards", "touchdowns", "two_point_conversions"),
    "misc": ("fumbles_lost", "special_teams_touchdowns", "fumble_recovery_touchdowns"),
}


class ScoringOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fumbles_lost_scope: Literal["all", "scrimmage"] = "all"


# K and D/ST (S1): the stats config/scoring.yaml's kicking: and defense: sections may score
# (src/twm/scoring_kdst.py maps each to warehouse columns). Every stat must be listed.
KICKING_STATS: tuple[str, ...] = (
    "fg_made_0_39", "fg_made_40_49", "fg_made_50_59", "fg_made_60_plus",
    "fg_missed", "fg_blocked", "pat_made", "pat_missed",
)  # fmt: skip
DEFENSE_STATS: tuple[str, ...] = (
    "sacks", "interceptions", "fumble_recoveries", "blocked_kicks", "safeties",
    "kickoff_return_tds", "punt_return_tds", "interception_return_tds", "fumble_return_tds",
    "blocked_kick_return_tds",
)  # fmt: skip

# One tier: (highest value in the tier, points); None = no upper bound (the last tier).
Tier = tuple[int | None, float]


def check_tiers(name: str, tiers: list[Tier], *, required: bool) -> list[Tier]:
    """Tiers ascend strictly, start at 0 or above, and end with the unbounded tier."""
    if not tiers:
        if required:
            raise ValueError(f"{name}: at least one tier is required")
        return tiers
    bounds = [b for b, _ in tiers]
    if bounds[-1] is not None or any(b is None for b in bounds[:-1]):
        raise ValueError(f"{name}: only the last tier has no upper bound (null), got {bounds}")
    finite = [b for b in bounds[:-1] if b is not None]
    if any(b < 0 for b in finite) or finite != sorted(set(finite)):
        raise ValueError(f"{name}: upper bounds must be >= 0 and strictly ascending: {bounds}")
    return tiers


class KickingScoring(BaseModel):
    """config/scoring.yaml ``kicking:``: points per field goal by distance bucket (yards of
    the kick), per miss, per block and per extra point."""

    model_config = ConfigDict(extra="forbid")

    fg_made_0_39: float
    fg_made_40_49: float
    fg_made_50_59: float
    fg_made_60_plus: float
    fg_missed: float
    fg_blocked: float
    pat_made: float
    pat_missed: float

    def points(self) -> dict[str, float]:
        return {k: float(getattr(self, k)) for k in KICKING_STATS}


class DefenseScoring(BaseModel):
    """config/scoring.yaml ``defense:``: points per D/ST event plus the points-allowed tiers
    (and optional yards-allowed tiers, off when empty)."""

    model_config = ConfigDict(extra="forbid")

    sacks: float
    interceptions: float
    fumble_recoveries: float
    blocked_kicks: float
    safeties: float
    kickoff_return_tds: float
    punt_return_tds: float
    interception_return_tds: float
    fumble_return_tds: float
    blocked_kick_return_tds: float
    points_allowed_tiers: list[Tier]
    yards_allowed_tiers: list[Tier] = []

    @field_validator("points_allowed_tiers")
    @classmethod
    def _pa_tiers(cls, v: list[Tier]) -> list[Tier]:
        return check_tiers("defense.points_allowed_tiers", v, required=True)

    @field_validator("yards_allowed_tiers")
    @classmethod
    def _ya_tiers(cls, v: list[Tier]) -> list[Tier]:
        return check_tiers("defense.yards_allowed_tiers", v, required=False)

    def points(self) -> dict[str, float]:
        return {k: float(getattr(self, k)) for k in DEFENSE_STATS}


class Scoring(BaseModel):
    """config/scoring.yaml: points per unit of each stat. Unknown sections or stat names are
    rejected, so a typo ("recieving") fails loudly instead of silently scoring zero."""

    model_config = ConfigDict(extra="forbid")

    passing: dict[str, float]
    rushing: dict[str, float]
    receiving: dict[str, float]
    misc: dict[str, float]
    options: ScoringOptions = ScoringOptions()
    # K and D/ST (S1); optional so a config written for QB/RB/WR/TE only still loads (K and D/ST
    # scoring then raises a clear error).
    kicking: KickingScoring | None = None
    defense: DefenseScoring | None = None

    @model_validator(mode="after")
    def _known_stats(self) -> Scoring:
        for section, allowed in SCORING_STATS.items():
            unknown = sorted(set(getattr(self, section)) - set(allowed))
            if unknown:
                raise ValueError(
                    f"scoring.{section}: unknown stats {unknown}; allowed: {list(allowed)}"
                )
        return self


# Fantasy positions in scope for P1 (PROJECT_SPEC 7.1).
FANTASY_POSITIONS = ("QB", "RB", "WR", "TE")
# The K and D/ST streamer's positions (S1b): the lineup keys of config/league.yaml.
STREAMER_POSITIONS = ("K", "DST")


class PoolConfig(BaseModel):
    """``pool:`` in league.yaml: the Waiver Radar candidate pool (step C1). Strict, because a
    typo would silently change which players a backtest calls "available"."""

    model_config = ConfigDict(extra="forbid")

    roster_statuses: list[str]
    prior_season_min_games: int
    rookie_drafted_rounds: int
    ownership_available_below: float
    ownership_high: float
    ownership_low: float

    @field_validator("roster_statuses")
    @classmethod
    def _statuses(cls, v: list[str]) -> list[str]:
        if not v or len(set(v)) != len(v) or any(not s or s != s.upper() for s in v):
            raise ValueError(
                f"pool.roster_statuses {v!r}: need a non-empty list of distinct upper-case "
                "roster status codes (ACT, INA, DEV ...)"
            )
        return v

    @field_validator("prior_season_min_games")
    @classmethod
    def _games(cls, v: int) -> int:
        if not 1 <= v <= 17:
            raise ValueError(f"pool.prior_season_min_games = {v}: must be between 1 and 17")
        return v

    @field_validator("rookie_drafted_rounds")
    @classmethod
    def _rounds(cls, v: int) -> int:
        if not 0 <= v <= 7:
            raise ValueError(f"pool.rookie_drafted_rounds = {v}: must be between 0 and 7")
        return v

    @model_validator(mode="after")
    def _percentages(self) -> PoolConfig:
        for name in ("ownership_available_below", "ownership_high", "ownership_low"):
            if not 0 <= getattr(self, name) <= 100:
                raise ValueError(f"pool.{name} must be a percentage between 0 and 100")
        if self.ownership_low >= self.ownership_high:
            raise ValueError("pool.ownership_low must be below pool.ownership_high")
        return self


# ---- League shape (PROJECT_SPEC 7.2) --------------------------------------------------------
# Lineup slots that never score: the bench and injured reserve.
BENCH_SLOTS = ("bench", "BE", "IR")
# ... of which the reserve slots hold players who cannot play (ESPN's IR): they are not even
# bench, so no derived number (bench count, thresholds, pool cutoffs) ever counts them.
RESERVE_SLOTS = ("IR",)
# Positions the app does not rank (kickers, team defenses, individual defensive players, punters,
# head coaches): a lineup may have slots for them (ESPN leagues usually have K and D/ST); they are
# accepted and ignored, like any multi-position slot that holds none of FANTASY_POSITIONS.
OTHER_POSITIONS = ("K", "DST", "D/ST", "DEF", "P", "HC", "DL", "DE", "DT", "LB", "DB", "CB", "S",
                   "DP")  # fmt: skip
MAX_TEAMS = 32
# Plain words for the positions in scope (generated text: "a running back or receiver").
POSITION_WORDS = {"QB": "quarterback", "RB": "running back", "WR": "receiver", "TE": "tight end"}


def round_half_up(x: float) -> int:
    """Round to the nearest whole number, halves up (16.5 -> 17), computed on the decimal
    value so a multiplier such as 1.1 cannot land a hair below a half. Python's round() would
    round halves to the even number (16.5 -> 16)."""
    return int(Decimal(repr(x)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def or_join(items: Sequence[str], word: str = "or") -> str:
    """ "RB", "RB or WR", "QB, RB or WR"."""
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} {word} {items[-1]}"


def rank_groups(ranks: Mapping[str, int], *, sep: str = "/") -> str:
    """Positions that share a rank, grouped, in position order: {RB: 36, WR: 36} -> "RB/WR top
    36"; {QB: 24, RB: 48, WR: 48} -> "QB top 24, RB/WR top 48"."""
    groups: dict[int, list[str]] = {}
    for pos, n in ranks.items():
        groups.setdefault(int(n), []).append(pos)
    return ", ".join(f"{sep.join(ps)} top {n}" for n, ps in groups.items())


@dataclass(frozen=True)
class LeagueShape:
    """Every league-shaped number, derived from config/league.yaml (``League.shape``).

    - ``dedicated``: starting slots per team that only this position can fill (a QB slot; a
      multi-position slot whose only in-scope position it is counts too).
    - ``flex_slots``: starting slots per team this position can fill together with other
      positions (FLEX, SUPERFLEX ...).
    - ``starter_thresholds`` (PROJECT_SPEC 7.2): teams x dedicated starters: a weekly finish at
      or above it is a **starter finish** (the Waiver Radar's labels).
    - ``flex_worthy_ranks``: for each position in ``flex_worthy_positions`` that a
      multi-position slot can hold: its starter threshold + teams x those slots (with no
      override: teams x (dedicated + flex slots)): the most players of that position a league
      could start. Informative only (``is_flex_finish``), never a label.
    - ``pool_cutoffs``: starter threshold x ``candidate_pool_multiplier``, rounded half up: the
      Waiver Radar treats players ranked inside it as rostered.
    - ``bench_slots``: bench slots per team; reserve slots (IR, ``RESERVE_SLOTS``) are not
      counted anywhere.
    - ``overridden``: which numbers came from explicit overrides in league.yaml.
    """

    teams: int
    dedicated: dict[str, int]
    flex_slots: dict[str, int]
    starter_thresholds: dict[str, int]
    flex_worthy_ranks: dict[str, int]
    pool_cutoffs: dict[str, int]
    multiplier: float
    starting_slots: int
    bench_slots: int
    ignored_slots: dict[str, int]
    overridden: tuple[str, ...] = ()

    @property
    def size_text(self) -> str:
        """ "12-team" (generated text: "a typical 12-team league")."""
        return f"{self.teams}-team"

    def describe(self) -> list[str]:
        """Plain-English lines: the derived numbers and where they come from."""
        lines = [
            f"{self.teams} teams; per team {self.starting_slots} starting slots (QB/RB/WR/TE "
            "dedicated: "
            + ", ".join(f"{p} {n}" for p, n in self.dedicated.items())
            + "; multi-position slots each position can fill: "
            + (", ".join(f"{p} {n}" for p, n in self.flex_slots.items() if n) or "none")
            + (
                "; ignored (not ranked): "
                + ", ".join(f"{s} {n}" for s, n in self.ignored_slots.items())
                if self.ignored_slots
                else ""
            )
            + f"), bench {self.bench_slots}",
            "starter threshold (teams x dedicated starters): "
            + ", ".join(f"{p} top {n}" for p, n in self.starter_thresholds.items()),
            "FLEX-worthy rank: " + (rank_groups(self.flex_worthy_ranks) or "none"),
            f"candidate-pool cutoff (threshold x {self.multiplier:g}): "
            + ", ".join(f"{p} {n}" for p, n in self.pool_cutoffs.items()),
        ]
        if self.overridden:
            lines.append("explicit overrides in league.yaml: " + ", ".join(self.overridden))
        return lines


class League(BaseModel):
    """config/league.yaml: the fantasy league the app is tuned for.

    Only ``teams``, ``lineup`` (slot -> count per team, bench included), ``slot_eligibility``
    (which positions each multi-position slot can hold) and ``candidate_pool_multiplier`` are
    needed: every threshold is derived from them (:class:`LeagueShape`, ``League.shape``).
    ``starter_rank_threshold`` and ``flex_worthy_rank`` are optional overrides, normally left
    out. Strict (unknown keys and slot names are refused), because a typo would silently change
    which players count as starters or as available.
    """

    model_config = ConfigDict(extra="forbid")

    teams: int
    lineup: dict[str, int]
    slot_eligibility: dict[str, list[str]] = {}
    # positions that get a FLEX-worthy rank when a multi-position slot can hold them (TE is left
    # out by default: PROJECT_SPEC 7.2 defines FLEX-worthy for RB/WR; a superflex QB is in)
    flex_worthy_positions: list[str] = ["QB", "RB", "WR"]
    candidate_pool_multiplier: float
    starter_rank_threshold: dict[str, int] | None = None  # optional override, per position
    flex_worthy_rank: int | dict[str, int] | None = None  # optional override (all or per position)
    pool: PoolConfig
    _shape: LeagueShape | None = PrivateAttr(default=None)

    @field_validator("teams")
    @classmethod
    def _teams(cls, v: int) -> int:
        if not 2 <= v <= MAX_TEAMS:
            raise ValueError(f"teams = {v}: a league needs between 2 and {MAX_TEAMS} teams")
        return v

    @field_validator("candidate_pool_multiplier")
    @classmethod
    def _multiplier(cls, v: float) -> float:
        if not math.isfinite(v) or v <= 0:
            raise ValueError("candidate_pool_multiplier must be positive")
        if v < 1:
            raise ValueError(
                f"candidate_pool_multiplier = {v:g}: must be at least 1, or the pool cutoff "
                "would be smaller than the number of starters and startable players would count "
                "as available"
            )
        return v

    @model_validator(mode="after")
    def _derive(self) -> League:
        self._shape = _derive_shape(self)
        return self

    @property
    def shape(self) -> LeagueShape:
        assert self._shape is not None  # set by the validator
        return self._shape

    def starter_thresholds(self) -> dict[str, int]:
        """Per position (QB, RB, WR, TE), the weekly starter threshold (teams x dedicated
        starters, or the override): 12 teams x 1 QB = QB top 12."""
        return dict(self.shape.starter_thresholds)

    def flex_worthy_ranks(self) -> dict[str, int]:
        """Per FLEX-worthy position (default RB and WR), the FLEX-worthy rank: RB 12 x (2 + 1)
        = 36 in the default league. Empty when the lineup has no multi-position slot."""
        return dict(self.shape.flex_worthy_ranks)

    def candidate_pool_cutoffs(self) -> dict[str, int]:
        """Per position, how many players count as "surely rostered": the starter threshold
        times ``candidate_pool_multiplier``, rounded half up (QB 12 x 1.5 = 18, RB 24 x 1.5 =
        36 ...). The one place this number is computed (the id report and the pool both use
        it)."""
        return dict(self.shape.pool_cutoffs)


def _derive_shape(lg: League) -> LeagueShape:
    """Validate the lineup and derive every league-shaped number (see LeagueShape)."""
    in_scope = set(FANTASY_POSITIONS)
    known_positions = in_scope | set(OTHER_POSITIONS)
    problems: list[str] = []
    for slot, positions in lg.slot_eligibility.items():
        if slot in known_positions or slot in BENCH_SLOTS:
            problems.append(
                f"slot_eligibility.{slot}: {slot} is a position or the bench, not a "
                "multi-position slot"
            )
        unknown = [p for p in positions if p not in known_positions]
        if not positions or unknown:
            problems.append(
                f"slot_eligibility.{slot} = {positions}: needs a non-empty list of positions "
                f"({', '.join(FANTASY_POSITIONS)} or ignored ones such as K, DST)"
                + (f"; unknown {unknown}" if unknown else "")
            )
        if len(set(positions)) != len(positions):
            problems.append(f"slot_eligibility.{slot} lists a position twice: {positions}")
    bad_flex = [p for p in lg.flex_worthy_positions if p not in in_scope]
    if bad_flex or len(set(lg.flex_worthy_positions)) != len(lg.flex_worthy_positions):
        problems.append(
            f"flex_worthy_positions {lg.flex_worthy_positions}: distinct positions from "
            f"{list(FANTASY_POSITIONS)}"
        )
    dedicated = dict.fromkeys(FANTASY_POSITIONS, 0)
    flex_slots = dict.fromkeys(FANTASY_POSITIONS, 0)
    ignored: dict[str, int] = {}
    starting = bench = 0
    for slot, count in lg.lineup.items():
        if not isinstance(count, int) or count < 0:
            problems.append(f"lineup.{slot} = {count}: a slot count must be a whole number >= 0")
            continue
        if slot in BENCH_SLOTS:
            bench += 0 if slot in RESERVE_SLOTS else count
            continue
        if slot in in_scope:
            dedicated[slot] += count
        elif slot in OTHER_POSITIONS:
            if count:
                ignored[slot] = ignored.get(slot, 0) + count
        elif slot in lg.slot_eligibility:
            holds = [p for p in FANTASY_POSITIONS if p in lg.slot_eligibility[slot]]
            if len(holds) == 1:
                dedicated[holds[0]] += count  # a slot only one ranked position can fill
            elif holds:
                for p in holds:
                    flex_slots[p] += count
            elif count:
                ignored[slot] = ignored.get(slot, 0) + count
        else:
            problems.append(
                f"lineup.{slot}: unknown slot. Use a position ({', '.join(FANTASY_POSITIONS)}, "
                f"or {', '.join(OTHER_POSITIONS[:2])} ... which are ignored), a bench slot "
                f"({', '.join(BENCH_SLOTS)}), or a multi-position slot listed under "
                "slot_eligibility (e.g. FLEX: [RB, WR, TE])"
            )
            continue
        starting += count
    if problems:
        raise ValueError("league.yaml: " + "; ".join(problems))

    overridden: list[str] = []
    override = dict(lg.starter_rank_threshold or {})
    unknown = sorted(set(override) - in_scope)
    if unknown:
        raise ValueError(
            f"starter_rank_threshold: unknown positions {unknown} (use {list(FANTASY_POSITIONS)})"
        )
    thresholds: dict[str, int] = {}
    for p in FANTASY_POSITIONS:
        if p in override:
            thresholds[p] = int(override[p])
            overridden.append(f"starter_rank_threshold.{p}")
        else:
            thresholds[p] = lg.teams * dedicated[p]
    for p, n in thresholds.items():
        if n < 1:
            raise ValueError(
                f"{p} has no starting slot of its own (lineup.{p} = {dedicated[p]}), so there is "
                f"no weekly starter threshold for {p}s (teams x {p} starters = 0). Add a {p} "
                f"slot to the lineup or set starter_rank_threshold.{p}"
                if p not in override
                else f"starter_rank_threshold.{p} = {n}: must be at least 1"
            )

    flex_ranks = {
        p: thresholds[p] + lg.teams * flex_slots[p]
        for p in FANTASY_POSITIONS
        if flex_slots[p] > 0 and p in lg.flex_worthy_positions
    }
    fo = lg.flex_worthy_rank
    if fo is not None:
        if not flex_ranks:
            raise ValueError(
                "flex_worthy_rank is set but no FLEX-worthy position exists: the lineup has no "
                "multi-position slot holding a position in flex_worthy_positions"
            )
        chosen = dict.fromkeys(flex_ranks, fo) if isinstance(fo, int) else dict(fo)
        extra = sorted(set(chosen) - set(flex_ranks))
        if extra:
            raise ValueError(
                f"flex_worthy_rank: {extra} have no FLEX-worthy rank to override (a position "
                "needs a multi-position slot that can hold it and a place in "
                f"flex_worthy_positions); FLEX-worthy positions: {list(flex_ranks)}"
            )
        for p, n in chosen.items():
            flex_ranks[p] = int(n)
            overridden.append(f"flex_worthy_rank.{p}")
    for p, n in flex_ranks.items():
        if n < thresholds[p]:
            raise ValueError(
                f"FLEX-worthy rank of {p} ({n}) is below its starter threshold ({thresholds[p]})"
            )
    cutoffs = {p: round_half_up(n * lg.candidate_pool_multiplier) for p, n in thresholds.items()}
    return LeagueShape(
        teams=lg.teams,
        dedicated=dedicated,
        flex_slots=flex_slots,
        starter_thresholds=thresholds,
        flex_worthy_ranks=flex_ranks,
        pool_cutoffs=cutoffs,
        multiplier=lg.candidate_pool_multiplier,
        starting_slots=starting,
        bench_slots=bench,
        ignored_slots=ignored,
        overridden=tuple(overridden),
    )


# Environment variable naming another folder with settings.yaml, scoring.yaml and league.yaml
# (e.g. a 10-team league for a what-if run: `TWM_CONFIG_DIR=/path/to/cfg uv run twm ...`).
# Paths inside settings.yaml stay relative to the project root.
CONFIG_DIR_ENV = "TWM_CONFIG_DIR"
CONFIG_FILES = ("settings.yaml", "scoring.yaml", "league.yaml")


def config_dir() -> Path:
    """The folder the YAML files are read from: $TWM_CONFIG_DIR, else ``config/``."""
    env = os.environ.get(CONFIG_DIR_ENV)
    return Path(env) if env else CONFIG_DIR


def _load(name: str) -> dict[str, Any]:
    with (config_dir() / name).open() as f:
        return yaml.safe_load(f)


@functools.cache
def settings() -> Settings:
    return Settings(**_load("settings.yaml"))


@functools.cache
def scoring() -> Scoring:
    return Scoring(**_load("scoring.yaml"))


@functools.cache
def league() -> League:
    return League(**_load("league.yaml"))


def reload() -> None:
    """Forget the loaded config (after $TWM_CONFIG_DIR or a YAML file changed) and rebuild the
    registry texts that quote it. The next settings()/scoring()/league() call reads the files
    again."""
    import sys

    for fn in (settings, scoring, league):
        fn.cache_clear()
    registry = sys.modules.get("twm.registry")
    if registry is not None:
        # an invalid or missing league.yaml: the next league() call raises the clear message;
        # the registry keeps its previous texts until the config loads
        with contextlib.suppress(ValueError, OSError):
            registry.refresh()
