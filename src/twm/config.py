"""Typed access to config/*.yaml. Loaded once, validated with pydantic."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"


class Paths(BaseModel):
    raw_cache: str
    schemas: str
    warehouse: str
    league_db: str


class Settings(BaseModel):
    project_name: str
    current_season: int
    seasons: dict[str, Any]
    as_of: dict[str, Any]
    horizons: dict[str, int]
    garbage_time: dict[str, float]
    neutral: dict[str, float]
    paths: Paths

    def path(self, key: str) -> Path:
        return ROOT / getattr(self.paths, key)


class Scoring(BaseModel):
    passing: dict[str, float]
    rushing: dict[str, float]
    receiving: dict[str, float]
    misc: dict[str, float]


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
