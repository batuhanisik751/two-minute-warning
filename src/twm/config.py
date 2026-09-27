"""Typed access to config/*.yaml. Loaded once, validated with pydantic."""

from __future__ import annotations

import functools
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"


class Paths(BaseModel):
    raw_cache: str
    schemas: str
    warehouse: str
    league_db: str
    manual: str = "data/manual"  # committed, hand-curated files (player_id_overrides.csv ...)


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


class Settings(BaseModel):
    project_name: str
    current_season: int
    seasons: dict[str, Any]
    as_of: dict[str, Any]
    horizons: dict[str, int]
    garbage_time: GarbageTimeConfig
    neutral: NeutralConfig
    paths: Paths
    availability: AvailabilityConfig

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


class Scoring(BaseModel):
    """config/scoring.yaml: points per unit of each stat. Unknown sections or stat names are
    rejected, so a typo ("recieving") fails loudly instead of silently scoring zero."""

    model_config = ConfigDict(extra="forbid")

    passing: dict[str, float]
    rushing: dict[str, float]
    receiving: dict[str, float]
    misc: dict[str, float]
    options: ScoringOptions = ScoringOptions()

    @model_validator(mode="after")
    def _known_stats(self) -> Scoring:
        for section, allowed in SCORING_STATS.items():
            unknown = sorted(set(getattr(self, section)) - set(allowed))
            if unknown:
                raise ValueError(
                    f"scoring.{section}: unknown stats {unknown}; allowed: {list(allowed)}"
                )
        return self


class League(BaseModel):
    teams: int
    lineup: dict[str, int]
    starter_rank_threshold: dict[str, int]
    flex_worthy_rank: int
    candidate_pool_multiplier: float


def _load(name: str) -> dict[str, Any]:
    with (CONFIG_DIR / name).open() as f:
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
