"""Coach-team-season, team-season and career aggregates (docs/coach_tendencies.md).

Each tendency is a ratio ``<m>_num / <m>_n`` over the plays of :mod:`.plays`: summing numerators
and samples pools seasons exactly (career = all his plays, not a mean of season means). Per
season each row also gets the league value (all teams' plays pooled) and a percentile among
the rows of that season with at least ``MIN_PLAYS_RANKED`` offensive snaps (and, for the
fourth-down rates, ``MIN_SAMPLE_RANKED`` choices): ``100 * (average rank - 0.5) / rows
ranked`` (higher value -> higher percentile, so for ``neutral_sec_per_play`` a high
percentile means a SLOW offense).
"""

from __future__ import annotations

import polars as pl

METRICS: dict[str, str] = {
    "neutral_pass_rate": "share of neutral 1st-3rd-down snaps that were dropbacks",
    "early_down_pass_rate": "share of neutral 1st-2nd-down snaps that were dropbacks",
    "proe": "pass rate over expected: mean nflfastR pass_oe (percentage points) over offensive "
    "snaps where it is defined, all situations",
    "neutral_sec_per_play": "game-clock seconds between consecutive snaps of a drive, neutral",
    "no_huddle_rate": "share of neutral snaps run no-huddle",
    "shotgun_rate": "share of neutral snaps from shotgun",
    "fourth_go_rate": "share of neutral fourth-down choices where the offense ran a play",
    "fourth_short_go_rate": "the same on 4th-and-1 or 4th-and-2",
}
# First season with usable data: xpass/pass_oe are NULL before 2006; no_huddle is barely
# charted before then (league 0-1.6% in 1999-2005).
FIRST_SEASON = {"proe": 2006, "no_huddle_rate": 2006}
MIN_PLAYS_RANKED = 150
# Fourth downs are few (a full season: ~50-80 neutral choices, ~5-20 on 4th-and-1-2): below
# these samples the value is shown but not ranked.
MIN_SAMPLE_RANKED = {"fourth_go_rate": 10, "fourth_short_go_rate": 5}
COACH_KEYS = ("coach_id", "team", "season")
TEAM_KEYS = ("team", "season")


def _specs() -> dict[str, tuple[pl.Expr, pl.Expr]]:
    """metric -> (value summed, mask of the plays it is measured on); pace comes from pairs."""
    neutral, early = pl.col("is_neutral_play"), pl.col("is_early")
    poe = pl.col("is_off") & pl.col("pass_oe").is_not_null()
    four, go, short = pl.col("is_fourth"), pl.col("is_go"), pl.col("is_short")
    return {
        "neutral_pass_rate": (pl.col("pass"), neutral),
        "early_down_pass_rate": (pl.col("pass"), early),
        "proe": (pl.col("pass_oe"), poe),
        "no_huddle_rate": (pl.col("no_huddle"), neutral),
        "shotgun_rate": (pl.col("shotgun"), neutral),
        "fourth_go_rate": (go.cast(pl.Int64), four),
        "fourth_short_go_rate": (go.cast(pl.Int64), four & short),
    }


def _num_n(metric: str, value: pl.Expr, mask: pl.Expr) -> list[pl.Expr]:
    num = pl.when(mask).then(value.cast(pl.Float64).fill_null(0.0)).otherwise(0.0).sum()
    return [num.alias(f"{metric}_num"), mask.sum().cast(pl.Int64).alias(f"{metric}_n")]


def aggregate(flagged: pl.DataFrame, pairs: pl.DataFrame, keys: tuple[str, ...]) -> pl.DataFrame:
    """Numerators and samples of every metric per ``keys`` (``team`` is the offense), plus
    games, offensive snaps (``plays``) and the last week played."""
    rows = flagged.filter(pl.col("is_off") | pl.col("is_fourth")).rename({"posteam": "team"})
    rows = rows.drop_nulls(subset=list(keys))  # a play without an offense or coach: no row
    aggs = [e for m, (v, mask) in _specs().items() for e in _num_n(m, v, mask)]
    base = rows.group_by(keys).agg(
        pl.col("game_id").filter(pl.col("is_off")).n_unique().cast(pl.Int64).alias("games"),
        pl.col("is_off").sum().cast(pl.Int64).alias("plays"),
        pl.col("week").max().alias("through_week"),
        *aggs,
    )
    pace = (
        pairs.rename({"posteam": "team"})
        .group_by(keys)
        .agg(
            pl.col("seconds").cast(pl.Float64).sum().alias("neutral_sec_per_play_num"),
            pl.len().cast(pl.Int64).alias("neutral_sec_per_play_n"),
        )
    )
    out = base.join(pace, on=list(keys), how="left").with_columns(
        pl.col("neutral_sec_per_play_num").fill_null(0.0),
        pl.col("neutral_sec_per_play_n").fill_null(0),
    )
    return out.with_columns(_value(m) for m in METRICS).sort(list(keys))


def _value(metric: str) -> pl.Expr:
    first = FIRST_SEASON.get(metric)
    ok = pl.col(f"{metric}_n") > 0
    if first is not None:
        ok = ok & (pl.col("season") >= first)
    return pl.when(ok).then(pl.col(f"{metric}_num") / pl.col(f"{metric}_n")).alias(metric)


def with_league(rows: pl.DataFrame, league: pl.DataFrame) -> pl.DataFrame:
    """Add ``<m>_league`` from an aggregate keyed by season alone (all offenses pooled)."""
    lg = league.select("season", *[pl.col(m).alias(f"{m}_league") for m in METRICS])
    return rows.join(lg, on="season", how="left")


def with_percentiles(rows: pl.DataFrame) -> pl.DataFrame:
    """Add ``<m>_pctile`` within each season (module docstring); NULL for unranked rows."""
    qual = pl.col("plays") >= MIN_PLAYS_RANKED
    out = []
    for m in METRICS:
        enough = pl.col(f"{m}_n") >= MIN_SAMPLE_RANKED.get(m, 1)
        v = pl.when(qual & enough & pl.col(m).is_not_null()).then(pl.col(m))
        rank, n = v.rank("average").over("season"), v.count().over("season")
        out.append((100.0 * (rank - 0.5) / n).alias(f"{m}_pctile"))
    return rows.with_columns(out)


def coach_season(flagged: pl.DataFrame, pairs: pl.DataFrame, current_season: int) -> pl.DataFrame:
    """Wide coach-team-season rows of one or more seasons: counts, ``<m>_num``, ``<m>_n``,
    ``<m>``, ``<m>_league``, ``<m>_pctile`` and ``is_current`` (the season in progress)."""
    league = aggregate(flagged, pairs, ("season",))
    rows = with_percentiles(with_league(aggregate(flagged, pairs, COACH_KEYS), league))
    return rows.with_columns((pl.col("season") == current_season).alias("is_current"))


def team_season(flagged: pl.DataFrame, pairs: pl.DataFrame) -> pl.DataFrame:
    """Wide team-season rows (every coach of the season pooled), with the league columns."""
    league = aggregate(flagged, pairs, ("season",))
    return with_league(aggregate(flagged, pairs, TEAM_KEYS), league)


SEASON_LONG_COLUMNS = (
    *COACH_KEYS, "is_current", "through_week", "games", "plays", "metric", "value", "sample",
    "league_avg", "percentile",
)  # fmt: skip


def season_long(wide: pl.DataFrame) -> pl.DataFrame:
    """The published per-season frame: one row per (coach_id, team, season, metric) with a
    value (metrics without data that season, e.g. PROE before 2006, have no row)."""
    parts = [
        wide.select(
            *COACH_KEYS,
            "is_current",
            "through_week",
            "games",
            "plays",
            pl.lit(m).alias("metric"),
            pl.col(m).alias("value"),
            pl.col(f"{m}_n").alias("sample"),
            pl.col(f"{m}_league").alias("league_avg"),
            pl.col(f"{m}_pctile").alias("percentile"),
        )
        for m in METRICS
    ]
    out = pl.concat(parts).filter(pl.col("value").is_not_null())
    return out.select(SEASON_LONG_COLUMNS).sort("coach_id", "season", "team", "metric")


CAREER_COLUMNS = (
    "coach_id", "metric", "seasons", "first_season", "last_season", "teams", "value", "sample",
    "league_avg", "vs_league",
)  # fmt: skip


def career(wide: pl.DataFrame) -> pl.DataFrame:
    """The published career frame: one row per (coach_id, metric) over his COMPLETED regular
    seasons (the season in progress is its own row in :func:`season_long`). ``value`` pools all
    his plays; ``league_avg`` is the league value of each season weighted by his sample that
    season, so ``vs_league`` compares him with the league of the years he coached."""
    done = wide.filter(~pl.col("is_current"))
    parts = []
    for m in METRICS:
        n, num = pl.col(f"{m}_n"), pl.col(f"{m}_num")
        g = (
            done.filter(pl.col(m).is_not_null())
            .group_by("coach_id")
            .agg(
                pl.col("season").n_unique().cast(pl.Int64).alias("seasons"),
                pl.col("season").min().alias("first_season"),
                pl.col("season").max().alias("last_season"),
                pl.col("team").unique().sort().str.join("/").alias("teams"),
                (num.sum() / n.sum()).alias("value"),
                n.sum().alias("sample"),
                ((n * pl.col(f"{m}_league")).sum() / n.sum()).alias("league_avg"),
            )
        )
        parts.append(g.with_columns(pl.lit(m).alias("metric")))
    out = pl.concat(parts).with_columns((pl.col("value") - pl.col("league_avg")).alias("vs_league"))
    return out.select(CAREER_COLUMNS).sort("coach_id", "metric")
