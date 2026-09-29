"""Kicker and team defense / special teams (D/ST) fantasy points (S1, docs/scoring.md).

The same idea as :mod:`twm.scoring`: points are a weighted sum of stat components, the weights
live in ``config/scoring.yaml`` (``kicking:`` and ``defense:``, ESPN's defaults), and this module
only knows which warehouse column holds each stat. D/ST adds one non-linear rule: points
allowed are scored by tier (0 allowed = 5, 1-6 = 4, ...), plus optional yards-allowed tiers
(off unless the config lists some).

- Kickers: :func:`score_kicking` / :func:`score_kicking_sql` read nflverse's kicking columns
  (``fact_kicker_week``, or ``fact_player_week`` / ``fact_team_week`` which carry the same ones).
- D/ST: :func:`score_defense` / :func:`score_defense_sql` read ``fact_defense_week`` (one row
  per team-game with the event counts and ``points_allowed`` computed the ESPN way).

A missing stat (NULL) counts as 0; a NULL points (or yards) allowed scores 0 from its tiers.
Scoring is a per-row calculation, so it cannot leak.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

import polars as pl

from twm.config import (
    DEFENSE_STATS,
    KICKING_STATS,
    DefenseScoring,
    KickingScoring,
    Scoring,
    Tier,
    check_tiers,
    scoring,
)

# Stat -> the warehouse column(s) whose sum is that stat. nflverse's distance buckets are
# 0-19/20-29/30-39/40-49/50-59/60+; ESPN's 0-39 bucket is the sum of the first three. nflverse
# counts a blocked field goal apart from a miss (fg_att = fg_made + fg_missed + fg_blocked).
KICKING_COLUMNS: dict[str, tuple[str, ...]] = {
    "fg_made_0_39": ("fg_made_0_19", "fg_made_20_29", "fg_made_30_39"),
    "fg_made_40_49": ("fg_made_40_49",),
    "fg_made_50_59": ("fg_made_50_59",),
    "fg_made_60_plus": ("fg_made_60_",),
    "fg_missed": ("fg_missed",),
    "fg_blocked": ("fg_blocked",),
    "pat_made": ("pat_made",),
    "pat_missed": ("pat_missed",),
}
# fact_defense_week columns (docs/warehouse.md says where each count comes from).
DEFENSE_COLUMNS: dict[str, tuple[str, ...]] = {
    "sacks": ("def_sacks",),
    "interceptions": ("def_interceptions",),
    "fumble_recoveries": ("fumble_recovery_opp",),
    "blocked_kicks": ("def_punt_blocks", "def_fg_blocks", "def_pat_blocks"),
    "safeties": ("def_safeties",),
    "kickoff_return_tds": ("kickoff_return_tds",),
    "punt_return_tds": ("punt_return_tds",),
    "interception_return_tds": ("interception_return_tds",),
    "fumble_return_tds": ("fumble_return_tds",),
    "blocked_kick_return_tds": ("blocked_kick_return_tds",),
}
POINTS_ALLOWED_COLUMN = "points_allowed"
YARDS_ALLOWED_COLUMN = "yards_allowed"
assert set(KICKING_COLUMNS) == set(KICKING_STATS), "kicking stat registry out of sync"
assert set(DEFENSE_COLUMNS) == set(DEFENSE_STATS), "defense stat registry out of sync"


def _section(cfg: Scoring | None, name: str) -> KickingScoring | DefenseScoring:
    cfg = cfg or scoring()
    sec = getattr(cfg, name)
    if sec is None:
        raise ValueError(f"config/scoring.yaml has no {name}: section; K and D/ST cannot be scored")
    return sec


@dataclass(frozen=True)
class KickingRules:
    """Points per unit of each kicking stat (``"fg_made_50_59" -> 5``). Stats not listed score
    0. Build it from the config (:meth:`from_config`, the default) or a league's settings."""

    points: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        unknown = sorted(set(self.points) - set(KICKING_STATS))
        if unknown:
            raise ValueError(f"unknown kicking stats {unknown}; known: {list(KICKING_STATS)}")

    @classmethod
    def from_config(cls, cfg: Scoring | None = None) -> KickingRules:
        return cls(_section(cfg, "kicking").points())

    def with_points(self, **changes: float) -> KickingRules:
        return replace(self, points={**self.points, **changes})

    def columns(self) -> dict[str, tuple[str, ...]]:
        """Stat -> source columns, for the stats this rule set gives points for."""
        return {k: KICKING_COLUMNS[k] for k in KICKING_STATS if self.points.get(k, 0) != 0}

    def required_columns(self) -> list[str]:
        return sorted({c for cols in self.columns().values() for c in cols})


@dataclass(frozen=True)
class DefenseRules:
    """Points per D/ST event plus the points-allowed tiers and the optional yards-allowed
    tiers (empty = not scored). A tier is ``(highest value in the tier, points)``, the last one
    unbounded (``None``); a value falls in the first tier whose bound it does not exceed."""

    points: Mapping[str, float] = field(default_factory=dict)
    points_allowed_tiers: tuple[Tier, ...] = ()
    yards_allowed_tiers: tuple[Tier, ...] = ()

    def __post_init__(self) -> None:
        unknown = sorted(set(self.points) - set(DEFENSE_STATS))
        if unknown:
            raise ValueError(f"unknown defense stats {unknown}; known: {list(DEFENSE_STATS)}")
        check_tiers("points_allowed_tiers", list(self.points_allowed_tiers), required=False)
        check_tiers("yards_allowed_tiers", list(self.yards_allowed_tiers), required=False)

    @classmethod
    def from_config(cls, cfg: Scoring | None = None) -> DefenseRules:
        d = _section(cfg, "defense")
        return cls(d.points(), tuple(d.points_allowed_tiers), tuple(d.yards_allowed_tiers))

    def with_points(self, **changes: float) -> DefenseRules:
        return replace(self, points={**self.points, **changes})

    def columns(self) -> dict[str, tuple[str, ...]]:
        return {k: DEFENSE_COLUMNS[k] for k in DEFENSE_STATS if self.points.get(k, 0) != 0}

    def required_columns(self) -> list[str]:
        cols = {c for cols in self.columns().values() for c in cols}
        if self.points_allowed_tiers:
            cols.add(POINTS_ALLOWED_COLUMN)
        if self.yards_allowed_tiers:
            cols.add(YARDS_ALLOWED_COLUMN)
        return sorted(cols)


# --------------------------------------------------------------------------------------
# Tiers
# --------------------------------------------------------------------------------------


def tier_points(value: float | None, tiers: Sequence[Tier]) -> float:
    """Points for ``value`` under ``tiers`` (0 for None or no tiers): the first tier whose
    upper bound (inclusive) is >= value; the unbounded last tier catches the rest."""
    if value is None:
        return 0.0
    for bound, pts in tiers:
        if bound is None or value <= bound:
            return float(pts)
    return 0.0


def _sql_number(x: float) -> str:
    return repr(float(x))


def _tier_expr(col: str, tiers: Sequence[Tier]) -> pl.Expr:
    value = pl.col(col).cast(pl.Float64)
    expr: pl.Expr | pl.When | pl.Then | None = None
    for bound, pts in tiers:
        cond = value.is_not_null() if bound is None else value <= bound
        expr = pl.when(cond) if expr is None else expr.when(cond)  # type: ignore[union-attr]
        expr = expr.then(pl.lit(float(pts)))
    return pl.lit(0.0) if expr is None else expr.otherwise(pl.lit(0.0))  # type: ignore[union-attr]


def _tier_sql(col: str, tiers: Sequence[Tier]) -> str:
    whens = [
        f"WHEN {col} IS NOT NULL THEN {_sql_number(pts)}"
        if bound is None
        else f"WHEN CAST({col} AS DOUBLE) <= {_sql_number(bound)} THEN {_sql_number(pts)}"
        for bound, pts in tiers
    ]
    return f"(CASE {' '.join(whens)} ELSE 0.0 END)" if whens else "0.0"


# --------------------------------------------------------------------------------------
# Scoring (polars and SQL: the same calculation)
# --------------------------------------------------------------------------------------


def _names(df: pl.DataFrame | pl.LazyFrame) -> list[str]:
    return df.collect_schema().names() if isinstance(df, pl.LazyFrame) else df.columns


def _check(names: list[str], required: list[str], what: str) -> None:
    missing = [c for c in required if c not in names]
    if missing:
        raise KeyError(f"cannot score {what}: missing stat columns {missing}")


def _linear_terms(
    cols: Mapping[str, tuple[str, ...]], points: Mapping[str, float]
) -> list[pl.Expr]:
    return [
        (
            pl.sum_horizontal(pl.col(c).cast(pl.Float64).fill_null(0.0) for c in cs) * points[stat]
        ).alias(f"fp_{stat}")
        for stat, cs in cols.items()
    ]


def _linear_sql(
    cols: Mapping[str, tuple[str, ...]], points: Mapping[str, float], prefix: str
) -> list[str]:
    out = []
    for stat, cs in cols.items():
        total = " + ".join(f"COALESCE(CAST({prefix}{c} AS DOUBLE), 0)" for c in cs)
        out.append(f"({total}) * {_sql_number(points[stat])}")
    return out


def score_kicking[Frame: (pl.DataFrame, pl.LazyFrame)](
    df: Frame,
    rules: KickingRules | None = None,
    *,
    column: str = "fantasy_points",
    breakdown: bool = False,
) -> Frame:
    """Add ``column`` (kicker fantasy points); with ``breakdown`` also ``fp_<stat>`` columns."""
    rules = rules or KickingRules.from_config()
    _check(_names(df), rules.required_columns(), "kicking")
    terms = _linear_terms(rules.columns(), rules.points)
    total = (pl.sum_horizontal(terms) if terms else pl.lit(0.0)).alias(column)
    return df.with_columns([*terms, total] if breakdown and terms else [total])


def score_kicking_sql(rules: KickingRules | None = None, *, table_alias: str | None = None) -> str:
    """The same calculation as :func:`score_kicking` as a DuckDB SQL expression (DOUBLE)."""
    rules = rules or KickingRules.from_config()
    prefix = f"{table_alias}." if table_alias else ""
    parts = _linear_sql(rules.columns(), rules.points, prefix)
    return "(" + (" + ".join(parts) if parts else "0.0") + ")"


def score_defense[Frame: (pl.DataFrame, pl.LazyFrame)](
    df: Frame,
    rules: DefenseRules | None = None,
    *,
    column: str = "fantasy_points",
    breakdown: bool = False,
) -> Frame:
    """Add ``column`` (D/ST fantasy points: events + points-allowed tier + yards-allowed tier
    when configured); with ``breakdown`` also ``fp_<stat>``, ``fp_points_allowed`` and
    ``fp_yards_allowed`` (when scored) columns."""
    rules = rules or DefenseRules.from_config()
    _check(_names(df), rules.required_columns(), "D/ST")
    terms = _linear_terms(rules.columns(), rules.points)
    if rules.points_allowed_tiers:
        pa = _tier_expr(POINTS_ALLOWED_COLUMN, rules.points_allowed_tiers)
        terms.append(pa.alias("fp_points_allowed"))
    if rules.yards_allowed_tiers:
        ya = _tier_expr(YARDS_ALLOWED_COLUMN, rules.yards_allowed_tiers)
        terms.append(ya.alias("fp_yards_allowed"))
    total = (pl.sum_horizontal(terms) if terms else pl.lit(0.0)).alias(column)
    return df.with_columns([*terms, total] if breakdown and terms else [total])


def score_defense_sql(rules: DefenseRules | None = None, *, table_alias: str | None = None) -> str:
    """The same calculation as :func:`score_defense` as a DuckDB SQL expression (DOUBLE)."""
    rules = rules or DefenseRules.from_config()
    prefix = f"{table_alias}." if table_alias else ""
    parts = _linear_sql(rules.columns(), rules.points, prefix)
    if rules.points_allowed_tiers:
        parts.append(_tier_sql(f"{prefix}{POINTS_ALLOWED_COLUMN}", rules.points_allowed_tiers))
    if rules.yards_allowed_tiers:
        parts.append(_tier_sql(f"{prefix}{YARDS_ALLOWED_COLUMN}", rules.yards_allowed_tiers))
    return "(" + (" + ".join(parts) if parts else "0.0") + ")"
