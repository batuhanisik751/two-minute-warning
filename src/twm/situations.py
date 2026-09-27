"""Garbage time and neutral situations (PROJECT_SPEC 7.3, docs/warehouse.md).

Late in a blowout the losing team throws against soft coverage and the winning team runs out the
clock, so stats from those plays say little about how a player will do next week. Each play gets
two flags, computed from its own pre-snap state (so they cannot leak the future):

- ``is_garbage_time``: the offense's win probability is below ``wp_low`` (0.05) or above
  ``wp_high`` (0.95), EXCEPT in the final ``exclude_final_seconds_half`` (120) seconds of a half
  when the score is within ``one_possession_points`` (8): a one-score game in the last two
  minutes is not decided, whatever the model says.
- ``is_neutral``: the stricter filter analysts use for play-calling tendencies: win probability
  between 0.20 and 0.80 (inclusive) and MORE than 120 seconds left in the half.

A play can never be both. A play without a win probability (12 pass/run plays in 1999-2026) is
neither. The win probability is nflfastR's ``wp`` (a model output; Section 6.3 caveat).

The thresholds come from ``config/settings.yaml``; the warehouse stores the flags on
``fact_play`` so every stat can be shown with and without garbage time
(``WHERE NOT is_garbage_time``).
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from twm.config import GarbageTimeConfig, NeutralConfig, settings

# fact_play / pbp columns the flags read.
INPUT_COLUMNS = ("wp", "half_seconds_remaining", "score_differential")


@dataclass(frozen=True)
class SituationRules:
    garbage_wp_low: float = 0.05
    garbage_wp_high: float = 0.95
    close_final_seconds: int = 120
    one_possession_points: int = 8
    neutral_wp_low: float = 0.20
    neutral_wp_high: float = 0.80
    neutral_min_half_seconds: int = 120

    @classmethod
    def from_config(
        cls, garbage: GarbageTimeConfig | None = None, neutral: NeutralConfig | None = None
    ) -> SituationRules:
        s = settings()
        g = garbage or s.garbage_time
        n = neutral or s.neutral
        return cls(
            garbage_wp_low=g.wp_low,
            garbage_wp_high=g.wp_high,
            close_final_seconds=g.exclude_final_seconds_half,
            one_possession_points=g.one_possession_points,
            neutral_wp_low=n.wp_low,
            neutral_wp_high=n.wp_high,
            neutral_min_half_seconds=n.min_half_seconds_remaining,
        )

    def describe_garbage_time(self) -> str:
        return (
            f"win probability below {self.garbage_wp_low:g} or above {self.garbage_wp_high:g}, "
            f"except in the final {self.close_final_seconds} seconds of a half when the score "
            f"is within {self.one_possession_points} points"
        )

    def describe_neutral(self) -> str:
        return (
            f"win probability from {self.neutral_wp_low:g} to {self.neutral_wp_high:g} and more "
            f"than {self.neutral_min_half_seconds} seconds left in the half"
        )


def _rules(rules: SituationRules | None) -> SituationRules:
    return rules or SituationRules.from_config()


# --------------------------------------------------------------------------------------
# SQL (the warehouse build) and polars (feature code, tests): the same logic, kept side by side
# --------------------------------------------------------------------------------------


def garbage_time_sql(rules: SituationRules | None = None, alias: str | None = None) -> str:
    r = _rules(rules)
    p = f"{alias}." if alias else ""
    extreme = f"({p}wp < {r.garbage_wp_low!r} OR {p}wp > {r.garbage_wp_high!r})"
    close_late = (
        f"({p}half_seconds_remaining <= {r.close_final_seconds} "
        f"AND abs({p}score_differential) <= {r.one_possession_points})"
    )
    # COALESCE: a play without win probability (or clock/score) is not garbage time
    return f"COALESCE({extreme} AND NOT {close_late}, FALSE)"


def neutral_sql(rules: SituationRules | None = None, alias: str | None = None) -> str:
    r = _rules(rules)
    p = f"{alias}." if alias else ""
    return (
        f"COALESCE({p}wp BETWEEN {r.neutral_wp_low!r} AND {r.neutral_wp_high!r} "
        f"AND {p}half_seconds_remaining > {r.neutral_min_half_seconds}, FALSE)"
    )


def garbage_time_expr(rules: SituationRules | None = None) -> pl.Expr:
    r = _rules(rules)
    wp = pl.col("wp")
    extreme = (wp < r.garbage_wp_low) | (wp > r.garbage_wp_high)
    close_late = (pl.col("half_seconds_remaining") <= r.close_final_seconds) & (
        pl.col("score_differential").abs() <= r.one_possession_points
    )
    return (extreme & ~close_late).fill_null(False).alias("is_garbage_time")


def neutral_expr(rules: SituationRules | None = None) -> pl.Expr:
    r = _rules(rules)
    wp = pl.col("wp")
    return (
        (
            (wp >= r.neutral_wp_low)
            & (wp <= r.neutral_wp_high)
            & (pl.col("half_seconds_remaining") > r.neutral_min_half_seconds)
        )
        .fill_null(False)
        .alias("is_neutral")
    )


def flag_plays[F: (pl.DataFrame, pl.LazyFrame)](df: F, rules: SituationRules | None = None) -> F:
    """Add ``is_garbage_time`` and ``is_neutral`` to a play-by-play frame."""
    names = df.collect_schema().names() if isinstance(df, pl.LazyFrame) else df.columns
    missing = [c for c in INPUT_COLUMNS if c not in names]
    if missing:
        raise KeyError(f"cannot flag plays: missing columns {missing}")
    return df.with_columns(garbage_time_expr(rules), neutral_expr(rules))
