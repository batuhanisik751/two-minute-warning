"""Hot-Seat H3a: point-in-time features, one row per head coach x team x as-of (no labels).

PROJECT_SPEC 8.5 "Features". Rows: every team with a played regular-season game, at every weekly
regular-season as-of from week 2 (``snapshot = 'weekly'``, dim_week.asof_weekly_utc of week N)
and at one end-of-regular-season snapshot per team-season (``snapshot = 'end_of_season'``,
anchor ``hot_seat.end_of_season_anchor``: ``asof`` = dim_week.asof_end_of_regular_season_utc, or
``last_game_end`` = the moment the team's last regular-season game's rows are public). The coach
is the head coach of the team's most recent played regular-season game (coach_game). Every
definition is in docs/hot_seat.md and registered in :mod:`twm.registry` (module ``hot_seat``).

Point in time, as in the streamer (:mod:`twm.modules.streamer.features`): :func:`features_for`
reads every input through an :class:`~twm.asof.AsOfView` (the reference, run through the leakage
harness in tests/test_hot_seat_features.py); :func:`features_history` reads each season's inputs
once with ``available_at`` and filters them per as-of (each input reads ONE table; the play
input is one row per game and side, whose plays share one ``available_at``). The Decision Report
Card's stored fourth-down grades are not in the warehouse: a graded game counts only when its
``fact_game`` row is visible. ``is_interim`` also reads the owner's departure file (hindsight; an
exclusion flag, never a model feature): :func:`add_interim_flag`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import duckdb
import numpy as np
import polars as pl

from twm.asof import asof_filter

KEY_COLUMNS = ("season", "snapshot", "week", "team", "coach_id")
SNAPSHOTS = ("weekly", "end_of_season")
PERFORMANCE_FEATURES = (
    "reg_games_played", "reg_wins", "expected_wins", "wins_vs_expected", "point_diff_per_game",
    "pythagorean_wins", "pythag_minus_wins",
)  # fmt: skip
QUALITY_FEATURES = (
    "off_epa_neutral", "def_epa_neutral", "off_epa_neutral_trend", "def_epa_neutral_trend",
)  # fmt: skip
CONTEXT_FEATURES = (
    "tenure_seasons", "is_first_year_coach", "is_second_year_coach", "tenure_censored",
    "prev_season_wins", "prev_playoff_round", "consecutive_losing_seasons", "division_rank",
    "games_remaining", "starting_qb_changes", "rookie_r1_qb_on_roster", "took_over_mid_season",
)  # fmt: skip
DECISION_FEATURES = ("fourth_down_wp_lost_per_game",)
FEATURE_FAMILIES: dict[str, tuple[str, ...]] = {
    "performance vs expectation": PERFORMANCE_FEATURES,
    "underlying quality": QUALITY_FEATURES,
    "context": CONTEXT_FEATURES,
    "decisions (P2 link)": DECISION_FEATURES,
}
FEATURE_COLUMNS: tuple[str, ...] = tuple(c for f in FEATURE_FAMILIES.values() for c in f)
# Not features: when the row was taken, the playoff result as text, how the market probability
# of the games to date was found, and the interim flag (exclusion, spec 8.5).
INFO_COLUMNS = (
    "as_of", "is_last_reg_week", "prev_playoff_result", "market_games_moneyline",
    "market_games_spread", "spread_slope",
)  # fmt: skip
PLAYOFF_RESULTS = ("none", "lost_wc", "lost_div", "lost_conf", "lost_sb", "won_sb")
ROUND_OF_GAME_TYPE = {"WC": 1, "DIV": 2, "CON": 3, "SB": 4}
FIRST_GRADED_SEASON = 2006  # the Decision Report Card grades 2006 on


@dataclass(frozen=True)
class HotSeatRules:
    """``hot_seat:`` in config/settings.yaml (validated there)."""

    end_of_season_anchor: str = "asof"
    pythagorean_exponent: float = 2.37
    trend_games: int = 4

    @classmethod
    def from_config(cls) -> HotSeatRules:
        from twm.config import settings

        c = settings().hot_seat
        return cls(c.end_of_season_anchor, float(c.pythagorean_exponent), int(c.trend_games))


# --------------------------------------------------------------------------------------
# Inputs: one season, read through a view (reference) or from the warehouse (batch)
# --------------------------------------------------------------------------------------


class SqlSource(Protocol):
    def sql(self, query: str, params: Sequence[Any] | None = None) -> pl.DataFrame: ...


class _Warehouse:
    """The warehouse itself (every row, future included) behind the ``sql`` interface."""

    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self._con = con

    def sql(self, query: str, params: Sequence[Any] | None = None) -> pl.DataFrame:
        return self._con.execute(query, params or []).pl()


EVENT_INPUTS = ("games", "coaches", "plays", "schedule", "rosters")
_ORDER = {
    "games": ("game_id",), "coaches": ("game_id", "team"), "plays": ("game_id", "side", "team"),
    "schedule": ("game_id",), "rosters": ("week", "team", "gsis_id"), "players": ("gsis_id",),
    "teams": ("team",),
}  # fmt: skip


def input_sql(season: int, *, batch: bool) -> dict[str, str]:
    """The SQL of every input of ``season``, by name. The same text runs through an AsOfView
    (tables are their point-in-time views) and on the warehouse (``batch``: every row)."""
    s = int(season)
    neutral = "play_type IN ('pass', 'run') AND COALESCE(two_point_attempt, 0) = 0 AND is_neutral"
    # epa summed as DECIMAL: exact, so a game's sum does not depend on the order of the plays
    side = """
            SELECT game_id, '{side}' AS side, {col} AS team,
                   CAST(sum(CAST(epa AS DECIMAL(18, 9))) AS DOUBLE) AS epa_sum,
                   count(epa) AS n_plays, max(available_at) AS available_at
            FROM fact_play
            WHERE season = {s} AND season_type = 'REG' AND {neutral} AND epa IS NOT NULL
            GROUP BY game_id, {col}"""
    plays = " UNION ALL ".join(
        side.format(side=k, col=c, s=s, neutral=neutral) for k, c in (("off", "posteam"),
                                                                     ("def", "defteam"))
    )  # fmt: skip
    return {
        "games": f"""
            SELECT game_id, season, week, season_type, game_type, home_team, away_team,
                   home_score, away_score, result, spread_line, home_moneyline, away_moneyline,
                   home_qb_id, away_qb_id, available_at
            FROM fact_game WHERE season <= {s} AND result IS NOT NULL""",
        "coaches": f"""
            SELECT game_id, season, week, season_type, team, coach_id, available_at
            FROM coach_game WHERE season <= {s}""",
        "plays": plays,
        "schedule": f"""
            SELECT game_id, week, home_team, away_team, available_at
            FROM fact_schedule WHERE season = {s} AND season_type = 'REG'""",
        "rosters": f"""
            SELECT week, team, gsis_id, status, available_at FROM fact_roster_week
            WHERE season = {s} AND season_type = 'REG' AND position = 'QB'""",
        # hindsight table: the view shows only players who exist by the as-of; the batch path
        # reads the row rule (public_from_utc) and applies it in SeasonInputs.visible
        "players": (
            "SELECT gsis_id, public_from_utc FROM dim_player "
            f"WHERE draft_round = 1 AND draft_year = {s} AND public_from_utc IS NOT NULL"
            if batch
            else f"SELECT gsis_id FROM dim_player WHERE draft_round = 1 AND draft_year = {s}"
        ),
        # static: today's alignment, unchanged since the 2002 realignment (rows start in 2002)
        "teams": "SELECT team_abbr AS team, team_division AS division FROM dim_team "
        "WHERE is_current",
    }


@dataclass(frozen=True)
class SeasonInputs:
    """Every input frame of one season. ``batch``: read from the warehouse with future rows
    (filter with :meth:`visible`); otherwise read through an as-of view. ``grades``: the
    stored fourth-down grades per team-game (:func:`load_grades`), or None (no grades)."""

    season: int
    frames: dict[str, pl.DataFrame]
    batch: bool
    grades: pl.DataFrame | None = None

    def visible(self, as_of: datetime) -> SeasonInputs:
        """The inputs as they were public at ``as_of`` (``available_at <= as_of``; players by
        their row rule). Through a view this changes nothing (the view already filtered)."""
        frames = dict(self.frames)
        for name in EVENT_INPUTS:
            frames[name] = asof_filter(self.frames[name], as_of)
        if self.batch:
            players = asof_filter(self.frames["players"], as_of, column="public_from_utc")
            frames["players"] = players.drop("public_from_utc")
        return replace(self, frames=frames, batch=False)


def load_inputs(
    source: SqlSource, season: int, *, batch: bool = False, grades: pl.DataFrame | None = None
) -> SeasonInputs:
    """Run :func:`input_sql` on ``source`` (an AsOfView, or the warehouse with ``batch``)."""
    frames = {
        name: source.sql(query).sort(list(_ORDER[name]), nulls_last=True)
        for name, query in input_sql(season, batch=batch).items()
    }
    return SeasonInputs(int(season), frames, batch, grades)


# --------------------------------------------------------------------------------------
# The market's pregame win probability of played games
# --------------------------------------------------------------------------------------


def decimal_odds(american: pl.Expr) -> pl.Expr:
    """American odds -> decimal odds: +150 -> 2.5, -200 -> 1.5."""
    return pl.when(american > 0).then(1 + american / 100).otherwise(1 + 100 / american.abs())


def moneyline_home_prob(home_ml: pl.Expr, away_ml: pl.Expr) -> pl.Expr:
    """De-vigged home win probability (1/o_h) / (1/o_h + 1/o_a) on decimal odds."""
    ih, ia = 1 / decimal_odds(home_ml), 1 / decimal_odds(away_ml)
    return ih / (ih + ia)


def fit_spread_slope(games: pl.DataFrame, season: int) -> float | None:
    """k of P(home win) = 1 / (1 + exp(-k x spread_line)), maximum likelihood on every played
    game (regular season and playoffs) of the seasons BEFORE ``season`` (walk-forward; ties
    left out; no intercept: spread_line already holds home field, so a pick'em is 50%).
    None when there is no such game."""
    t = games.filter(
        (pl.col("season") < season) & pl.col("spread_line").is_not_null() & (pl.col("result") != 0)
    ).sort("game_id")
    if t.height == 0:
        return None
    x = t.get_column("spread_line").to_numpy().astype(float)
    y = (t.get_column("result").to_numpy() > 0).astype(float)
    k = 0.0
    for _ in range(100):  # Newton's method on a concave log-likelihood: converges in a few steps
        p = 1.0 / (1.0 + np.exp(-k * x))
        hess = float(np.sum(p * (1 - p) * x * x))
        if hess <= 0:
            return None
        step = float(np.sum((y - p) * x)) / hess
        k += step
        if abs(step) < 1e-12:
            break
    return k


def team_games(games: pl.DataFrame, season: int, slope: float | None) -> pl.DataFrame:
    """One row per team and played regular-season game of ``season`` (in ``games``): week,
    opponent, points for / against, ``win`` (1, a tie 0.5, 0), the market's pregame ``p_win``
    (de-vigged closing moneylines; without both moneylines the spread logistic with ``slope``;
    NULL when neither exists), its ``market_source`` and the team's starting QB."""
    g = games.filter((pl.col("season") == season) & (pl.col("season_type") == "REG"))
    has_ml = pl.col("home_moneyline").is_not_null() & pl.col("away_moneyline").is_not_null()
    p_spread = (
        1 / (1 + (-slope * pl.col("spread_line")).exp())
        if slope is not None
        else pl.lit(None, pl.Float64)
    )
    p_ml = moneyline_home_prob(pl.col("home_moneyline"), pl.col("away_moneyline"))
    g = g.with_columns(
        pl.when(has_ml).then(p_ml).otherwise(p_spread).alias("p_home"),
        pl.when(has_ml)
        .then(pl.lit("moneyline"))
        .when(p_spread.is_not_null())
        .then(pl.lit("spread"))
        .alias("market_source"),
    )
    sides = []
    for me, them, p in (("home", "away", pl.col("p_home")), ("away", "home", 1 - pl.col("p_home"))):
        sides.append(
            g.select(
                "game_id",
                "week",
                pl.col(f"{me}_team").alias("team"),
                pl.col(f"{them}_team").alias("opp"),
                pl.col(f"{me}_score").alias("pf"),
                pl.col(f"{them}_score").alias("pa"),
                p.alias("p_win"),
                "market_source",
                pl.col(f"{me}_qb_id").alias("qb_id"),
            )  # fmt: skip
        )
    out = pl.concat(sides, how="vertical")
    win = pl.when(pl.col("pf") > pl.col("pa")).then(1.0).when(pl.col("pf") == pl.col("pa"))
    return out.with_columns(win.then(0.5).otherwise(0.0).alias("win")).sort("team", "week")


def season_records(games: pl.DataFrame) -> pl.DataFrame:
    """Per (season, team): regular-season games, wins (ties 0.5) and win share, and the playoff
    round (0 none, 1 lost wild card, 2 lost divisional, 3 lost conference, 4 lost Super Bowl,
    5 won it; NULL when the team played no regular-season game)."""
    sides = [
        games.select(
            "season",
            "season_type",
            "game_type",
            pl.col(f"{me}_team").alias("team"),
            pl.col(f"{me}_score").alias("pf"),
            pl.col(f"{them}_score").alias("pa"),
        )  # fmt: skip
        for me, them in (("home", "away"), ("away", "home"))
    ]
    g = pl.concat(sides, how="vertical").with_columns(
        pl.when(pl.col("pf") > pl.col("pa"))
        .then(1.0)
        .when(pl.col("pf") == pl.col("pa"))
        .then(0.5)
        .otherwise(0.0)
        .alias("win")
    )
    reg = (
        g.filter(pl.col("season_type") == "REG")
        .group_by("season", "team")
        .agg(pl.len().alias("games"), pl.col("win").sum().alias("wins"))
        .with_columns((pl.col("wins") / pl.col("games")).alias("win_share"))
    )
    rnd = pl.col("game_type").replace_strict(
        ROUND_OF_GAME_TYPE, default=None, return_dtype=pl.Int64
    )
    post = (
        g.filter(pl.col("season_type") == "POST")
        .with_columns(
            pl.when(pl.col("win") < 1)
            .then(rnd)
            .when(pl.col("game_type") == "SB")
            .then(5)
            .otherwise(0)
            .alias("value")
        )  # fmt: skip
        .group_by("season", "team")
        .agg(pl.col("value").max().alias("playoff_round"))
    )
    return (
        reg.join(post, on=["season", "team"], how="left")
        .with_columns(pl.col("playoff_round").fill_null(0).cast(pl.Int64))
        .sort("season", "team")
    )


# --------------------------------------------------------------------------------------
# One as-of
# --------------------------------------------------------------------------------------


def coach_context(
    coaches: pl.DataFrame, tg: pl.DataFrame, records: pl.DataFrame, season: int
) -> pl.DataFrame:
    """Per team with a played game in ``tg``: the coach in charge (the coach of its latest
    played regular-season game) and his tenure (the seasons of his current stint with the team:
    consecutive seasons up to this one with a game coached for it), first-/second-year flags,
    whether the tenure is left-censored (the stint reaches the warehouse's first season), his
    consecutive losing seasons with the team before this one, and whether he took over during
    this season (the team's first game this season had another coach)."""
    reg = coaches.filter((pl.col("season") == season) & (pl.col("season_type") == "REG"))
    played = tg.select("game_id", "team", "week").join(
        reg.select("game_id", "team", "coach_id"), on=["game_id", "team"], how="inner"
    )
    ends = (
        played.sort("team", "week")
        .group_by("team", maintain_order=True)
        .agg(pl.col("coach_id").first().alias("first_coach"), pl.col("coach_id").last())
    )
    stints = coaches.select("season", "season_type", "team", "coach_id").unique()
    first_data_season = coaches.get_column("season").min() if coaches.height else None
    reg_seasons = {
        (r["coach_id"], r["team"], r["season"])
        for r in stints.filter(pl.col("season_type") == "REG").iter_rows(named=True)
    }
    losing = {(r["team"], r["season"]): r["win_share"] < 0.5 for r in records.iter_rows(named=True)}
    rows = []
    for r in ends.iter_rows(named=True):
        coach, team = r["coach_id"], r["team"]
        mine = stints.filter((pl.col("coach_id") == coach) & (pl.col("team") == team))
        have = set(mine.get_column("season").to_list())
        tenure, first = 0, season
        while first in have:  # the current stint: consecutive seasons back from this one
            tenure, first = tenure + 1, first - 1
        streak, s = 0, season - 1
        while (coach, team, s) in reg_seasons and losing.get((team, s), False):
            streak, s = streak + 1, s - 1
        rows.append(
            {
                "team": team,
                "coach_id": coach,
                "tenure_seasons": tenure,
                "is_first_year_coach": tenure == 1,
                "is_second_year_coach": tenure == 2,
                "tenure_censored": first + 1 == first_data_season,
                "consecutive_losing_seasons": streak,
                "took_over_mid_season": r["first_coach"] != coach,
            }
        )
    schema = {
        "team": pl.String, "coach_id": pl.String, "tenure_seasons": pl.Int64,
        "is_first_year_coach": pl.Boolean, "is_second_year_coach": pl.Boolean,
        "tenure_censored": pl.Boolean, "consecutive_losing_seasons": pl.Int64,
        "took_over_mid_season": pl.Boolean,
    }  # fmt: skip
    return pl.DataFrame(rows, schema=schema)


def performance(tg: pl.DataFrame, exponent: float) -> pl.DataFrame:
    """Per team: games, wins, the market's expected wins (NULL unless every game has a
    probability), point differential per game, Pythagorean wins, starting-QB changes."""
    e = float(exponent)
    agg = tg.group_by("team").agg(
        pl.len().alias("reg_games_played"),
        pl.col("win").sum().alias("reg_wins"),
        pl.col("pf").sum().alias("pf"),
        pl.col("pa").sum().alias("pa"),
        pl.col("p_win").sum().alias("p_sum"),
        pl.col("p_win").null_count().alias("p_missing"),
        (pl.col("market_source") == "moneyline").sum().alias("market_games_moneyline"),
        (pl.col("market_source") == "spread").sum().alias("market_games_spread"),
        pl.col("qb_id").drop_nulls().n_unique().alias("n_qbs"),
    )
    n, pf, pa = pl.col("reg_games_played"), pl.col("pf").cast(pl.Float64), pl.col("pa")
    pyth = n * pf.pow(e) / (pf.pow(e) + pa.cast(pl.Float64).pow(e))
    return agg.with_columns(
        pl.when(pl.col("p_missing") == 0).then(pl.col("p_sum")).alias("expected_wins"),
        ((pl.col("pf") - pl.col("pa")) / n).alias("point_diff_per_game"),
        pl.when(pl.col("pf") + pl.col("pa") > 0).then(pyth).alias("pythagorean_wins"),
        pl.when(pl.col("n_qbs") > 0).then(pl.col("n_qbs") - 1).alias("starting_qb_changes"),
    ).with_columns(
        (pl.col("reg_wins") - pl.col("expected_wins")).alias("wins_vs_expected"),
        (pl.col("pythagorean_wins") - pl.col("reg_wins")).alias("pythag_minus_wins"),
    )


def neutral_epa(plays: pl.DataFrame, tg: pl.DataFrame, trend_games: int) -> pl.DataFrame:
    """Per team: offensive and defensive (allowed) EPA per neutral run/pass play over the
    season to date, and the trend = the last ``trend_games`` played games minus the season to
    date (NULL until the team has played more than ``trend_games`` games)."""
    games = tg.select("game_id", "team", "week")
    recent = (
        games.sort("team", "week", descending=[False, True])
        .group_by("team", maintain_order=True)
        .head(trend_games)
        .with_columns(pl.lit(True).alias("recent"))
    )
    n_games = games.group_by("team").agg(pl.len().alias("n_games"))
    p = plays.join(games, on=["game_id", "team"], how="inner").join(
        recent.select("game_id", "team", "recent"), on=["game_id", "team"], how="left"
    )
    r = pl.col("recent").fill_null(False)
    n_all, n_last = pl.col("n_plays").sum(), pl.col("n_plays").filter(r).sum()
    by = p.group_by("team", "side").agg(
        pl.when(n_all > 0).then(pl.col("epa_sum").sum() / n_all).alias("std"),
        pl.when(n_last > 0).then(pl.col("epa_sum").filter(r).sum() / n_last).alias("last"),
    )
    by = by.join(n_games, on="team", how="left").with_columns(
        pl.when(pl.col("n_games") > trend_games).then(pl.col("last") - pl.col("std")).alias("trend")
    )
    out = n_games.select("team")
    for side in ("off", "def"):
        part = by.filter(pl.col("side") == side).select(
            "team",
            pl.col("std").alias(f"{side}_epa_neutral"),
            pl.col("trend").alias(f"{side}_epa_neutral_trend"),
        )
        out = out.join(part, on="team", how="left")
    return out


OFF_ROSTER_STATUSES = ("CUT", "RET", "UFA", "TRD")  # released, retired, unsigned, traded away


def rookie_r1_qb(rosters: pl.DataFrame, players: pl.DataFrame) -> pl.DataFrame:
    """Per team with a visible roster this season: is a first-round pick of this year's draft
    (dim_player draft_round 1, draft_year = the season) a QB on its latest visible weekly
    roster (fact_roster_week.position, any status but the off-roster ones)?"""
    latest = rosters.group_by("team").agg(pl.col("week").max().alias("latest"))
    rows = rosters.join(latest, on="team").filter(pl.col("week") == pl.col("latest"))
    rookies = players.get_column("gsis_id").implode()
    on = ~pl.col("status").fill_null("").is_in(OFF_ROSTER_STATUSES) & pl.col("gsis_id").is_in(
        rookies
    )
    return rows.group_by("team").agg(on.any().alias("rookie_r1_qb_on_roster"))


def division_ranks(perf: pl.DataFrame, teams: pl.DataFrame) -> pl.DataFrame:
    """Rank in the division by win share (wins + 0.5 x ties) / games, 1 = best, among the
    division's teams with a played game; tied teams share the better rank."""
    share = pl.col("reg_wins") / pl.col("reg_games_played")
    return (
        perf.select("team", "reg_wins", "reg_games_played")
        .join(teams, on="team", how="inner")
        .with_columns(
            share.rank("min", descending=True)
            .over("division")
            .cast(pl.Int64)
            .alias("division_rank")
        )
        .select("team", "division_rank")
    )


def games_remaining(
    schedule: pl.DataFrame,
    perf: pl.DataFrame,
    cancelled: Sequence[tuple[str, int, tuple[str, str]]],
) -> pl.DataFrame:
    """Regular-season games left: the season's length (the most regular-season games any team
    has in the visible schedule; a game moved after the release is hidden until its kickoff,
    so a team's own count can be short) minus the team's played games, minus a listed
    cancelled game (``cancelled``: (game_id, week, teams), absent from nflverse) once it is
    gone (the caller passes only those)."""
    sides = [schedule.select(pl.col(c).alias("team")) for c in ("home_team", "away_team")]
    counts = pl.concat(sides).group_by("team").len()
    length = int(counts.get_column("len").max()) if counts.height else None
    gone: dict[str, int] = {}
    for _gid, _week, pair in cancelled:
        for t in pair:
            gone[t] = gone.get(t, 0) + 1
    return perf.select(
        "team",
        (
            pl.lit(length, pl.Int64)
            - pl.col("reg_games_played")
            - pl.col("team").replace_strict(gone, default=0, return_dtype=pl.Int64)
        ).alias("games_remaining"),
    )


def decision_cost(tg: pl.DataFrame, grades: pl.DataFrame | None) -> pl.DataFrame:
    """Per team: the WP lost on its clear (graded) fourth-down calls in its played games / its
    games; NULL without stored grades or when one of its games has none (not graded)."""
    if grades is None:
        return (
            tg.select("team")
            .unique()
            .with_columns(pl.lit(None, pl.Float64).alias("fourth_down_wp_lost_per_game"))
        )
    graded = grades.get_column("game_id").unique().implode()
    g = tg.select("game_id", "team").join(grades, on=["game_id", "team"], how="left")
    return g.group_by("team").agg(
        pl.when(pl.col("game_id").is_in(graded).all())
        .then(pl.col("wp_lost_clear").fill_null(0.0).sum() / pl.len())
        .alias("fourth_down_wp_lost_per_game")
    )


FEATURE_SCHEMA: dict[str, Any] = {
    "reg_games_played": pl.Int64, "reg_wins": pl.Float64, "expected_wins": pl.Float64,
    "wins_vs_expected": pl.Float64, "point_diff_per_game": pl.Float64,
    "pythagorean_wins": pl.Float64, "pythag_minus_wins": pl.Float64,
    "off_epa_neutral": pl.Float64, "def_epa_neutral": pl.Float64,
    "off_epa_neutral_trend": pl.Float64, "def_epa_neutral_trend": pl.Float64,
    "tenure_seasons": pl.Int64, "is_first_year_coach": pl.Boolean,
    "is_second_year_coach": pl.Boolean, "tenure_censored": pl.Boolean,
    "prev_season_wins": pl.Float64, "prev_playoff_round": pl.Int64,
    "consecutive_losing_seasons": pl.Int64, "division_rank": pl.Int64,
    "games_remaining": pl.Int64, "starting_qb_changes": pl.Int64,
    "rookie_r1_qb_on_roster": pl.Boolean, "took_over_mid_season": pl.Boolean,
    "fourth_down_wp_lost_per_game": pl.Float64,
}  # fmt: skip
INFO_SCHEMA: dict[str, Any] = {
    "as_of": pl.Datetime("us", "UTC"), "is_last_reg_week": pl.Boolean,
    "prev_playoff_result": pl.String, "market_games_moneyline": pl.Int64,
    "market_games_spread": pl.Int64, "spread_slope": pl.Float64,
}  # fmt: skip
KEY_SCHEMA: dict[str, Any] = {
    "season": pl.Int32, "snapshot": pl.String, "week": pl.Int32, "team": pl.String,
    "coach_id": pl.String,
}  # fmt: skip
OUTPUT_COLUMNS = (*KEY_COLUMNS, *INFO_COLUMNS, *FEATURE_COLUMNS)


def empty_features() -> pl.DataFrame:
    """A typed frame with the output columns of :func:`compute_features` and no rows."""
    return pl.DataFrame(schema={**KEY_SCHEMA, **INFO_SCHEMA, **FEATURE_SCHEMA}).select(
        OUTPUT_COLUMNS
    )


def compute_features(
    inp: SeasonInputs,
    *,
    snapshot: str,
    week: int,
    as_of: datetime,
    is_last_reg_week: bool,
    rules: HotSeatRules,
    cancelled: Sequence[tuple[str, int, tuple[str, str]]] = (),
) -> pl.DataFrame:
    """The features of every team with a played regular-season game at ``as_of``, from
    ``inp`` already made :meth:`~SeasonInputs.visible` at ``as_of``. ``cancelled``: the listed
    cancelled games of the season already gone at this as-of."""
    if snapshot not in SNAPSHOTS:
        raise ValueError(f"snapshot must be one of {SNAPSHOTS}, not {snapshot!r}")
    s, f = inp.season, inp.frames
    slope = fit_spread_slope(f["games"], s)
    tg = team_games(f["games"], s, slope)
    if tg.height == 0:
        return empty_features()
    records = season_records(f["games"].filter(pl.col("season") < s))
    perf = performance(tg, rules.pythagorean_exponent)
    prev = records.filter(pl.col("season") == s - 1).select(
        "team",
        pl.col("wins").alias("prev_season_wins"),
        pl.col("playoff_round").alias("prev_playoff_round"),
    )
    out = coach_context(f["coaches"], tg, records, s)
    for part in (
        perf,
        neutral_epa(f["plays"], tg, rules.trend_games),
        prev,
        division_ranks(perf, f["teams"]),
        games_remaining(f["schedule"], perf, cancelled),
        rookie_r1_qb(f["rosters"], f["players"]),
        decision_cost(tg, inp.grades),
    ):
        out = out.join(part, on="team", how="left")
    names = dict(enumerate(PLAYOFF_RESULTS))
    out = out.with_columns(
        pl.lit(s, pl.Int32).alias("season"),
        pl.lit(snapshot).alias("snapshot"),
        pl.lit(week, pl.Int32).alias("week"),
        pl.lit(as_of).dt.convert_time_zone("UTC").dt.cast_time_unit("us").alias("as_of"),
        pl.lit(is_last_reg_week).alias("is_last_reg_week"),
        pl.col("prev_playoff_round")
        .replace_strict(names, default=None, return_dtype=pl.String)
        .alias("prev_playoff_result"),
        pl.lit(slope, pl.Float64).alias("spread_slope"),
    )
    schema = {**KEY_SCHEMA, **INFO_SCHEMA, **FEATURE_SCHEMA}
    return out.select([pl.col(c).cast(schema[c]) for c in OUTPUT_COLUMNS]).sort("team")


# --------------------------------------------------------------------------------------
# The as-ofs, the stored grades and the cancelled games
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AsOfPoint:
    """One as-of of a season: its snapshot kind, week label and the teams it is for (None =
    every team; the ``last_game_end`` anchor gives each team its own end-of-season as-of)."""

    season: int
    snapshot: str
    week: int
    as_of: datetime
    is_last_reg_week: bool
    teams: tuple[str, ...] | None = None


def _utc(ts: datetime) -> datetime:
    from datetime import UTC

    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


def as_of_points(
    source: SqlSource, season: int, rules: HotSeatRules, now: datetime
) -> list[AsOfPoint]:
    """The season's as-ofs not after ``now``: dim_week's Tuesday as-of of every regular-season
    week from 2, then the end-of-season snapshot by ``rules.end_of_season_anchor``. Calendar
    reads (the schedule's dates, when a game's rows are public), not features."""
    s, now = int(season), _utc(now)
    weeks = source.sql(
        "SELECT week, asof_weekly_utc, is_last_reg_week, asof_end_of_regular_season_utc "
        f"FROM dim_week WHERE season = {s} AND season_type = 'REG' ORDER BY week"
    )
    points = [
        AsOfPoint(
            s, "weekly", int(r["week"]), _utc(r["asof_weekly_utc"]), bool(r["is_last_reg_week"])
        )
        for r in weeks.iter_rows(named=True)
        if r["week"] >= 2 and _utc(r["asof_weekly_utc"]) <= now
    ]
    last = weeks.filter(pl.col("is_last_reg_week"))
    if last.height == 0:
        return points
    last_week, eos = int(last["week"][0]), last["asof_end_of_regular_season_utc"][0]
    if rules.end_of_season_anchor == "asof":
        if eos is not None and _utc(eos) <= now:
            points.append(AsOfPoint(s, "end_of_season", last_week, _utc(eos), True))
        return points
    ends = source.sql(f"""
        WITH g AS (
            SELECT game_id, home_team AS team, week, result, available_at FROM fact_game
            WHERE season = {s} AND season_type = 'REG'
            UNION ALL
            SELECT game_id, away_team, week, result, available_at FROM fact_game
            WHERE season = {s} AND season_type = 'REG'),
        last AS (SELECT * FROM g
                 QUALIFY row_number() OVER (PARTITION BY team ORDER BY week DESC) = 1)
        SELECT l.team, greatest(l.available_at, max(p.available_at)) AS as_of
        FROM last l LEFT JOIN fact_play p ON p.game_id = l.game_id
        WHERE l.result IS NOT NULL GROUP BY l.team, l.available_at ORDER BY as_of, l.team""")
    for (when,), grp in ends.group_by(["as_of"], maintain_order=True):
        if _utc(when) <= now:
            teams = tuple(sorted(grp.get_column("team").to_list()))
            points.append(AsOfPoint(s, "end_of_season", last_week, _utc(when), True, teams))
    return points


def cancelled_gone(point: AsOfPoint) -> list[tuple[str, int, tuple[str, str]]]:
    """The listed cancelled games (availability.schedule_exceptions) of the point's season
    that are off the schedule at its as-of: from the week after the game's own week (the
    end-of-season snapshot included). Game ids read ``<season>_<week>_<away>_<home>``."""
    from twm.warehouse.available import AvailabilityRules

    out = []
    for x in AvailabilityRules.from_settings().cancelled_games():
        season, week, away, home = x.game_id.split("_")
        if int(season) == point.season and (
            point.snapshot == "end_of_season" or point.week > int(week)
        ):
            out.append((x.game_id, int(week), (away, home)))
    return out


def grade_rows(fourth: pl.DataFrame) -> pl.DataFrame:
    """Stored fourth-down grades -> one row per (game_id, team) of every graded regular-season
    game (both teams, from posteam and defteam) with ``wp_lost_clear``: the WP the team lost
    on its clear (graded) calls (0 when none)."""
    f = fourth.filter(pl.col("season_type") == "REG")
    lost = f.group_by("game_id", pl.col("posteam").alias("team")).agg(
        pl.col("wp_lost").filter(pl.col("grade") == "clear").sum().alias("wp_lost_clear")
    )
    teams = pl.concat(
        [f.select("game_id", pl.col(c).alias("team")) for c in ("posteam", "defteam")]
    ).unique()
    return (
        teams.join(lost, on=["game_id", "team"], how="left")
        .with_columns(pl.col("wp_lost_clear").fill_null(0.0))
        .sort("game_id", "team")
    )


def load_grades(
    seasons: Sequence[int], *, graded_dir: Path | None = None, history: pl.DataFrame | None = None
) -> dict[int, pl.DataFrame]:
    """The stored fourth-down grades per season (never regraded): the pinned frozen history
    (``config/production_models.yaml`` entry ``decisions``, sha256 checked) for its seasons,
    else the season in progress graded with the pins (``data/decisions/season/graded/
    fourth_downs_<S>.parquet``, what the site publishes; never the development grades). Seasons
    before 2006 or without a file are left out (their feature is NULL)."""
    from twm.modules.decisions import grade as gr
    from twm.modules.decisions import season as sn

    want = sorted({int(s) for s in seasons if int(s) >= FIRST_GRADED_SEASON})
    if not want:
        return {}
    if history is None:
        from twm import pins
        from twm.modules.decisions import frozen as fz

        history = fz.load_snapshot(pins.get_pin("decisions"))["fourth_downs"]
    in_history = set(history.get_column("season").unique().to_list())
    gdir = graded_dir if graded_dir is not None else sn.season_dir() / "graded"
    out: dict[int, pl.DataFrame] = {}
    for s in want:
        if s in in_history:
            out[s] = grade_rows(history.filter(pl.col("season") == s))
        elif (gdir / f"fourth_downs_{s}.parquet").exists():
            out[s] = grade_rows(gr.load_graded("fourth_downs", [s], gdir))
    return out


# --------------------------------------------------------------------------------------
# The two paths
# --------------------------------------------------------------------------------------


def features_for(
    view: SqlSource,
    point: AsOfPoint,
    *,
    rules: HotSeatRules | None = None,
    grades: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """The reference implementation: the rows of ``point`` with every input read through
    ``view`` (an :class:`~twm.asof.AsOfView` at ``point.as_of``)."""
    rules = rules or HotSeatRules.from_config()
    as_of = view.as_of  # type: ignore[attr-defined]
    if _utc(as_of) != _utc(point.as_of):
        raise ValueError(f"the view is at {as_of}, the point at {point.as_of}")
    inp = load_inputs(view, point.season, batch=False, grades=grades).visible(as_of)
    return _rows(inp, point, rules)


def _rows(inp: SeasonInputs, point: AsOfPoint, rules: HotSeatRules) -> pl.DataFrame:
    out = compute_features(
        inp, snapshot=point.snapshot, week=point.week, as_of=point.as_of,
        is_last_reg_week=point.is_last_reg_week, rules=rules, cancelled=cancelled_gone(point),
    )  # fmt: skip
    if point.teams is not None:
        out = out.filter(pl.col("team").is_in(list(point.teams)))
    return out


def features_history(
    db: Path | str,
    seasons: Sequence[int],
    *,
    now: datetime,
    rules: HotSeatRules | None = None,
    grades: dict[int, pl.DataFrame] | None = None,
    progress: Any = None,
) -> pl.DataFrame:
    """The batch path: every as-of of ``seasons`` up to ``now`` (:func:`as_of_points`), each
    season's inputs read once and filtered per as-of. Equals :func:`features_for` row by row
    (tested). ``grades``: per season (default :func:`load_grades`)."""
    from twm.warehouse.build import connect

    rules = rules or HotSeatRules.from_config()
    grades = grades if grades is not None else load_grades(seasons)
    frames = [empty_features()]
    con = connect(db, read_only=True)
    try:
        con.execute("SET TimeZone='UTC'")
        source = _Warehouse(con)
        for season in sorted({int(s) for s in seasons}):
            points = as_of_points(source, season, rules, now)
            inputs = load_inputs(source, season, batch=True, grades=grades.get(season))
            for point in points:
                frames.append(_rows(inputs.visible(point.as_of), point, rules))
            if progress is not None:
                progress(f"{season}: {len(points)} as-ofs")
    finally:
        con.close()
    return pl.concat(frames, how="vertical").sort(list(KEY_COLUMNS))


def add_interim_flag(
    df: pl.DataFrame, labels: pl.DataFrame | None, candidates: pl.DataFrame | None
) -> pl.DataFrame:
    """``is_interim`` (spec 8.5: interim coaches are excluded from training) = the coach took
    over during the season (``took_over_mid_season``) OR the owner's departure file says the
    coach-team-season was an interim one (``departure_type = interim_not_retained`` or
    ``interim_suspected = true``; joined by candidate_id to the candidates' ``coach_id``). The
    file is hindsight: the flag selects rows, it is never a model feature."""
    owner = pl.DataFrame(schema={"season": pl.Int32, "team": pl.String, "coach_id": pl.String})
    if labels is not None and candidates is not None and labels.height:
        said = labels.filter(
            (pl.col("departure_type") == "interim_not_retained")
            | (pl.col("interim_suspected").cast(pl.String).str.to_lowercase() == "true")
        ).select("candidate_id")
        owner = (
            said.join(
                candidates.select("candidate_id", "coach_id", "team", "last_season"),
                on="candidate_id",
                how="inner",
            )  # fmt: skip
            .select(pl.col("last_season").cast(pl.Int32).alias("season"), "team", "coach_id")
            .unique()
        )
    flagged = owner.with_columns(pl.lit(True).alias("_owner"))
    out = df.join(flagged, on=["season", "team", "coach_id"], how="left")
    out = out.with_columns(
        (pl.col("took_over_mid_season") | pl.col("_owner").fill_null(False)).alias("is_interim")
    ).drop("_owner")
    cols = [*KEY_COLUMNS, *INFO_COLUMNS, "is_interim", *FEATURE_COLUMNS]
    return out.select(cols)


def write_features(df: pl.DataFrame, path: Path) -> Path:
    """Write the features (zstd Parquet, atomically)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.write_parquet(tmp, compression="zstd", statistics=False)
    tmp.replace(path)
    return path


def merge_seasons(
    old: pl.DataFrame | None, new: pl.DataFrame, seasons: Sequence[int]
) -> pl.DataFrame:
    """``old`` without ``seasons`` plus ``new`` (a partial rebuild keeps the other seasons)."""
    if old is None or old.height == 0:
        return new.sort(list(KEY_COLUMNS))
    keep = old.filter(~pl.col("season").is_in(list(seasons))).select(new.columns)
    return pl.concat([keep, new], how="vertical").sort(list(KEY_COLUMNS))


def rows_per_season(df: pl.DataFrame) -> pl.DataFrame:
    """Rows per season and snapshot kind, with the moneyline coverage of the market feature:
    the share of the played team-games behind each team-season's latest row priced from
    moneylines (the rest from the spread logistic)."""
    latest = df.sort("as_of").group_by("season", "team", maintain_order=True).last()
    cover = latest.group_by("season").agg(
        (pl.col("market_games_moneyline").sum() / pl.col("reg_games_played").sum())
        .round(3)
        .alias("moneyline_share")
    )
    counts = df.group_by("season").agg(
        (pl.col("snapshot") == "weekly").sum().alias("weekly_rows"),
        (pl.col("snapshot") == "end_of_season").sum().alias("end_of_season_rows"),
    )
    return counts.join(cover, on="season", how="left").sort("season")


def null_rates(df: pl.DataFrame) -> pl.DataFrame:
    """Share of NULL values per feature column (and is_interim)."""
    cols = [c for c in ("is_interim", *FEATURE_COLUMNS) if c in df.columns]
    n = max(df.height, 1)
    return pl.DataFrame(
        {"feature": cols, "null_share": [round(df.get_column(c).null_count() / n, 4) for c in cols]}
    )
