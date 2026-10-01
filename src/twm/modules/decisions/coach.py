"""Head-coach attribution and aggregates of the G3 grades (PROJECT_SPEC 8.4 item 5, Outputs).

Every graded decision is credited to the head coach of the team with the ball in that game
(``fact_game.home_coach`` / ``away_coach``; ``fact_play`` carries the same names on every
row 2006-2026). Only **clear** decisions count toward a coach's WP lost; toss-ups are counted
apart. Aggregates per coach-season and coach-week (one game):

- ``fourth_graded`` / ``two_point_graded``: clear decisions; ``*_toss_ups``: the others;
- ``*_wp_lost``: sum over clear decisions of WP(best) - WP(chosen) (WP points as 0-1);
- ``*_wrong``: clear decisions where the chosen option was not the best;
- ``aggressiveness``: the go rate when going for it was clearly best (``go_clear_went`` /
  ``go_clear``), the spec's aggressiveness index;
- ``wp_lost_per_game`` = (fourth + two-point WP lost) / games.
"""

from __future__ import annotations

import polars as pl

GROUP_SEASON = ("season", "coach")
GROUP_WEEK = ("season", "week", "game_id", "coach")


def _aggs(kind: str) -> list[pl.Expr]:
    clear = (pl.col("grade") == "clear").fill_null(False)
    graded = pl.col("exclusion").is_null()
    out = [
        clear.sum().alias(f"{kind}_graded"),
        (graded & (pl.col("grade") != "clear")).fill_null(False).sum().alias(f"{kind}_toss_ups"),
        pl.when(clear).then(pl.col("wp_lost")).otherwise(0.0).sum().alias(f"{kind}_wp_lost"),
        (clear & ~pl.col("correct").fill_null(True)).sum().alias(f"{kind}_wrong"),
    ]
    if kind == "fourth":
        go_clear = clear & (pl.col("recommended") == "go")
        out += [
            go_clear.sum().alias("go_clear"),
            (go_clear & (pl.col("chosen") == "go")).sum().alias("go_clear_went"),
            (graded & (pl.col("chosen") == "go")).fill_null(False).sum().alias("went"),
        ]
    return out


def _combine(fourth: pl.DataFrame, tries: pl.DataFrame, keys: tuple[str, ...]) -> pl.DataFrame:
    teams = pl.col("posteam").unique().sort().str.join("/").alias("team")
    games = pl.col("game_id").n_unique().alias("games")
    f = fourth.group_by(keys).agg(teams, games, *_aggs("fourth"))
    t = tries.group_by(keys).agg(*_aggs("two_point"))
    out = f.join(t, on=list(keys), how="left").with_columns(
        pl.col(c).fill_null(0) for c in ("two_point_graded", "two_point_toss_ups",
                                          "two_point_wp_lost", "two_point_wrong")
    )  # fmt: skip
    return out.with_columns(
        (pl.col("fourth_wp_lost") + pl.col("two_point_wp_lost")).alias("wp_lost"),
        pl.when(pl.col("go_clear") > 0).then(pl.col("go_clear_went") / pl.col("go_clear"))
        .otherwise(None).alias("aggressiveness"),
    ).with_columns((pl.col("wp_lost") / pl.col("games")).alias("wp_lost_per_game")).sort(
        list(keys))  # fmt: skip


def coach_season(fourth: pl.DataFrame, tries: pl.DataFrame) -> pl.DataFrame:
    """One row per (season, coach): games and the aggregates of the module docstring."""
    return _combine(fourth, tries, GROUP_SEASON)


def coach_week(fourth: pl.DataFrame, tries: pl.DataFrame) -> pl.DataFrame:
    """One row per (season, week, game, coach): the same aggregates for one game."""
    return _combine(fourth, tries, GROUP_WEEK)


def leaderboard(seasons: pl.DataFrame, season: int, min_games: int) -> pl.DataFrame:
    """Coaches of ``season`` with at least ``min_games`` games, best (least WP lost per game)
    first, with their rank."""
    b = seasons.filter((pl.col("season") == season) & (pl.col("games") >= min_games))
    return b.sort(["wp_lost_per_game", "coach"]).with_row_index("rank", offset=1)


def _clock() -> pl.Expr:
    """'Q4 2:05' from qtr and game_seconds_remaining (overtime: 'OT m:ss' of its period)."""
    q = pl.col("qtr")
    left = pl.when(q <= 4).then(pl.col("game_seconds_remaining") - (4 - q) * 900).otherwise(
        pl.col("half_seconds_remaining"))  # fmt: skip
    mm, ss = (left // 60).cast(pl.Int64), (left % 60).cast(pl.Int64)
    return pl.concat_str(
        pl.when(q <= 4).then(pl.lit("Q") + q.cast(pl.String)).otherwise(pl.lit("OT")),
        pl.lit(" "), mm.cast(pl.String), pl.lit(":"), ss.cast(pl.String).str.zfill(2),
    ).alias("clock")  # fmt: skip


def _field() -> pl.Expr:
    """'own 35' / 'opp 40' / 'midfield' from yardline_100."""
    y = pl.col("yardline_100")
    return (pl.when(y > 50).then(pl.lit("own ") + (100 - y).cast(pl.String))
            .when(y < 50).then(pl.lit("opp ") + y.cast(pl.String))
            .otherwise(pl.lit("midfield")).alias("field"))  # fmt: skip


def worst_calls(fourth: pl.DataFrame, n: int = 10, season: int | None = None) -> pl.DataFrame:
    """The ``n`` clear fourth-down decisions with the most WP lost (optionally one season),
    with their context: game, team, coach, clock, score, down and distance, field position,
    what was chosen and recommended, each option's WP and what happened."""
    f = fourth.filter((pl.col("grade") == "clear").fill_null(False) & (pl.col("wp_lost") > 0))
    if season is not None:
        f = f.filter(pl.col("season") == season)
    return f.sort(["wp_lost", "season", "game_id", "play_id"], descending=[True, False, False,
                                                                           False]).head(n).select(
        "season", "week", "game_id", "play_id", "posteam", "defteam", "coach", _clock(),
        "score_differential", "ydstogo", _field(), "chosen", "recommended", "wp_go", "wp_fg",
        "wp_punt", "fg_available", "punt_available", "wp_lost", "outcome",
    )  # fmt: skip


def league_by_season(fourth: pl.DataFrame, tries: pl.DataFrame) -> pl.DataFrame:
    """Per season: graded fourth downs, clear and toss-up counts, the real go rate vs the
    recommended go rate (all graded fourth downs) and among clear ones, WP lost (total, per
    team-game, per clear decision), the two-point rate vs the recommended rate."""
    g = fourth.filter(pl.col("exclusion").is_null())
    clear = pl.col("grade") == "clear"
    f = g.group_by("season").agg(
        pl.len().alias("fourth_graded_rows"), clear.sum().alias("clear"),
        (~clear).sum().alias("toss_ups"),
        (pl.col("chosen") == "go").mean().alias("go_rate"),
        (pl.col("recommended") == "go").mean().alias("recommended_go_rate"),
        (clear & (pl.col("recommended") == "go")).sum().alias("go_clear"),
        (clear & (pl.col("recommended") == "go") & (pl.col("chosen") == "go")).sum()
        .alias("go_clear_went"),
        (clear & ~pl.col("correct")).sum().alias("clear_wrong"),
        pl.when(clear).then(pl.col("wp_lost")).otherwise(0.0).sum().alias("wp_lost"),
    )  # fmt: skip
    games = fourth.group_by("season").agg(
        pl.struct("game_id", "posteam").n_unique().alias("team_games")
    )
    t = tries.filter(pl.col("exclusion").is_null()).group_by("season").agg(
        pl.len().alias("tries"), (pl.col("chosen") == "two_point").mean().alias("two_point_rate"),
        (pl.col("recommended") == "two_point").mean().alias("recommended_two_point_rate"),
        (pl.col("grade") == "clear").sum().alias("tries_clear"),
        pl.when(pl.col("grade") == "clear").then(pl.col("wp_lost")).otherwise(0.0).sum()
        .alias("tries_wp_lost"),
    )  # fmt: skip
    return f.join(games, on="season").join(t, on="season", how="left").with_columns(
        (pl.col("go_clear_went") / pl.col("go_clear")).alias("aggressiveness"),
        (pl.col("wp_lost") / pl.col("team_games")).alias("wp_lost_per_team_game"),
        (pl.col("wp_lost") / pl.col("clear")).alias("wp_lost_per_clear"),
    ).sort("season")  # fmt: skip
