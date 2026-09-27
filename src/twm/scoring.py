"""Fantasy points from stat components (PROJECT_SPEC 7.1, docs/scoring.md).

A fantasy score is a weighted sum: every stat a player produced (yards, touchdowns, catches,
...) times the points the league gives for one unit of it. The weights live in
``config/scoring.yaml``; this module knows which nflverse ``player_stats`` column holds each stat.

We never trust a precomputed points column: nflverse's ``fantasy_points_ppr`` is only used to
cross-check this engine (the ``nflverse_ppr()`` preset reproduces it exactly on every QB/RB/WR/TE
week 1999-2026, see tests/test_scoring.py).

Two entry points compute the same number:

- :func:`score` for polars frames (``DataFrame`` or ``LazyFrame``);
- :func:`score_sql` for a DuckDB expression, e.g. inside an ``AsOfView`` query::

      with AsOfView(db, as_of) as v:
          v.sql(f"SELECT player_id, week, {score_sql()} AS fantasy_points FROM fact_player_week")

A missing stat (NULL) counts as 0. Scoring is a per-row calculation, so it cannot leak.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Literal

import polars as pl

from twm.config import SCORING_STATS, Scoring, scoring

FumbleScope = Literal["all", "scrimmage"]

# Stat key ("section.stat") -> the player_stats column(s) whose sum is that stat.
STAT_COLUMNS: dict[str, tuple[str, ...]] = {
    "passing.yards": ("passing_yards",),
    "passing.touchdowns": ("passing_tds",),
    "passing.interceptions": ("passing_interceptions",),
    "passing.two_point_conversions": ("passing_2pt_conversions",),
    "rushing.yards": ("rushing_yards",),
    "rushing.touchdowns": ("rushing_tds",),
    "rushing.two_point_conversions": ("rushing_2pt_conversions",),
    "receiving.receptions": ("receptions",),
    "receiving.yards": ("receiving_yards",),
    "receiving.touchdowns": ("receiving_tds",),
    "receiving.two_point_conversions": ("receiving_2pt_conversions",),
    # fumbles_lost depends on the scope option; see ScoringRules.columns()
    "misc.special_teams_touchdowns": ("special_teams_tds",),
    "misc.fumble_recovery_touchdowns": ("fumble_recovery_tds",),
}
FUMBLE_COLUMNS: dict[str, tuple[str, ...]] = {
    # every lost fumble, including kick and punt returns
    "all": ("fumbles_lost_total",),
    # only fumbles on sacks, runs and catches: what nflverse's fantasy_points_ppr deducts
    "scrimmage": ("sack_fumbles_lost", "rushing_fumbles_lost", "receiving_fumbles_lost"),
}
STAT_KEYS: tuple[str, ...] = tuple(
    f"{section}.{stat}" for section, stats in SCORING_STATS.items() for stat in stats
)
assert set(STAT_KEYS) == set(STAT_COLUMNS) | {"misc.fumbles_lost"}, "stat registry out of sync"


@dataclass(frozen=True)
class ScoringRules:
    """Points per unit of each stat (``"passing.yards" -> 0.04``) plus the fumble option.

    Stats not listed score 0. Build it from the config (:meth:`from_config`, the default
    everywhere), from a preset, or from a league's own settings (:meth:`from_points`).
    """

    points: Mapping[str, float] = field(default_factory=dict)
    fumbles_lost_scope: FumbleScope = "all"

    def __post_init__(self) -> None:
        unknown = sorted(set(self.points) - set(STAT_KEYS))
        if unknown:
            raise ValueError(f"unknown scoring stats {unknown}; known: {list(STAT_KEYS)}")
        if self.fumbles_lost_scope not in FUMBLE_COLUMNS:
            raise ValueError(
                f"fumbles_lost_scope must be one of {list(FUMBLE_COLUMNS)}, "
                f"not {self.fumbles_lost_scope!r}"
            )

    @classmethod
    def from_config(cls, cfg: Scoring | None = None) -> ScoringRules:
        cfg = cfg or scoring()
        points = {
            f"{section}.{stat}": float(value)
            for section in SCORING_STATS
            for stat, value in getattr(cfg, section).items()
        }
        return cls(points, cfg.options.fumbles_lost_scope)

    @classmethod
    def from_points(
        cls, points: Mapping[str, float], fumbles_lost_scope: FumbleScope = "all"
    ) -> ScoringRules:
        return cls(dict(points), fumbles_lost_scope)

    def with_points(self, **changes: float) -> ScoringRules:
        """A copy with some weights changed, e.g.
        ``rules.with_points(**{"receiving.receptions": 0.5})`` for half PPR."""
        return replace(self, points={**self.points, **changes})

    def columns(self) -> dict[str, tuple[str, ...]]:
        """Stat key -> source columns, for the stats this rule set gives points for."""
        cols = {**STAT_COLUMNS, "misc.fumbles_lost": FUMBLE_COLUMNS[self.fumbles_lost_scope]}
        return {k: cols[k] for k in STAT_KEYS if self.points.get(k, 0) != 0}

    def required_columns(self) -> list[str]:
        return sorted({c for cols in self.columns().values() for c in cols})


# --------------------------------------------------------------------------------------
# Presets (tests, and the "what would this player score under ..." questions)
# --------------------------------------------------------------------------------------

_BASE = {
    "passing.yards": 0.04,
    "passing.touchdowns": 4,
    "passing.interceptions": -2,
    "passing.two_point_conversions": 2,
    "rushing.yards": 0.1,
    "rushing.touchdowns": 6,
    "rushing.two_point_conversions": 2,
    "receiving.yards": 0.1,
    "receiving.touchdowns": 6,
    "receiving.two_point_conversions": 2,
    "misc.fumbles_lost": -2,
    "misc.special_teams_touchdowns": 6,
    "misc.fumble_recovery_touchdowns": 6,
}


def full_ppr() -> ScoringRules:
    return ScoringRules({**_BASE, "receiving.receptions": 1})


def half_ppr() -> ScoringRules:
    return ScoringRules({**_BASE, "receiving.receptions": 0.5})


def standard() -> ScoringRules:
    return ScoringRules(dict(_BASE))


def nflverse_ppr() -> ScoringRules:
    """Reproduces nflverse's ``fantasy_points_ppr``: scrimmage fumbles only, no points for
    fumble-recovery touchdowns (verified on every QB/RB/WR/TE week 1999-2026)."""
    return ScoringRules(
        {**_BASE, "receiving.receptions": 1, "misc.fumble_recovery_touchdowns": 0}, "scrimmage"
    )


def nflverse_standard() -> ScoringRules:
    """Reproduces nflverse's ``fantasy_points`` (no points per catch)."""
    return ScoringRules({**_BASE, "misc.fumble_recovery_touchdowns": 0}, "scrimmage")


# --------------------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------------------


def _check_columns(names: list[str], rules: ScoringRules) -> None:
    missing = [c for c in rules.required_columns() if c not in names]
    if missing:
        raise KeyError(
            f"cannot score: missing stat columns {missing} (expected nflverse player_stats / "
            "fact_player_week columns)"
        )


def _term(stat: str, cols: tuple[str, ...], weight: float) -> pl.Expr:
    total = pl.sum_horizontal(pl.col(c).cast(pl.Float64).fill_null(0.0) for c in cols)
    return (total * weight).alias(f"fp_{stat.replace('.', '_')}")


def score[Frame: (pl.DataFrame, pl.LazyFrame)](
    df: Frame,
    rules: ScoringRules | None = None,
    *,
    column: str = "fantasy_points",
    breakdown: bool = False,
) -> Frame:
    """Add ``column`` (fantasy points) to ``df``; with ``breakdown`` also one ``fp_<stat>``
    column per scored stat (the points that stat contributed, useful for explanations)."""
    rules = rules or ScoringRules.from_config()
    names = df.collect_schema().names() if isinstance(df, pl.LazyFrame) else df.columns
    _check_columns(names, rules)
    terms = [_term(stat, cols, rules.points[stat]) for stat, cols in rules.columns().items()]
    if not terms:
        return df.with_columns(pl.lit(0.0).alias(column))
    total = pl.sum_horizontal(terms).alias(column)
    return df.with_columns([*terms, total] if breakdown else [total])


def _sql_number(x: float) -> str:
    return repr(float(x))


def score_sql(rules: ScoringRules | None = None, *, table_alias: str | None = None) -> str:
    """The same calculation as :func:`score` as a DuckDB SQL expression (DOUBLE)."""
    rules = rules or ScoringRules.from_config()
    prefix = f"{table_alias}." if table_alias else ""
    parts = []
    for stat, cols in rules.columns().items():
        total = " + ".join(f"COALESCE(CAST({prefix}{c} AS DOUBLE), 0)" for c in cols)
        parts.append(f"({total}) * {_sql_number(rules.points[stat])}")
    return "(" + (" + ".join(parts) if parts else "0.0") + ")"
