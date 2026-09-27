"""The leakage harness (spec 13 "leakage tests"; P1 acceptance: "a deliberately leaky feature
fails the as-of test").

The toy builders below are the spec's check. A correct feature builder passes; each
deliberately leaky one fails with LeakageError. Read them as examples of what NOT to do.
"""

from __future__ import annotations

from datetime import UTC, datetime

import polars as pl
import pytest

from tests.conftest import (
    draft_class_players,
    player_week,
    season_2015_draft_games,
    season_2020_split_games,
    season_2025_games,
)
from twm.asof import AsOfView, outcomes_after, weekly_as_of
from twm.backtest.leakage import LeakageError, assert_future_invariant
from twm.warehouse import build as wb


@pytest.fixture
def db_2025(raw, db_path):
    """2025: WR p1 (PHI) and p2 (KC) with receptions in weeks 1-4."""
    games = season_2025_games()
    by_week = {}
    for g in games:
        by_week.setdefault(g["week"], g)
    stats = [player_week(by_week[w], "p1", 3 + w) for w in (1, 2, 3, 4)]
    stats += [player_week(by_week[w], "p2", 10 - w) for w in (1, 2, 3, 4)]
    raw.write_season(2025, games, player_stats=stats)
    wb.build_warehouse([2025], db_path=db_path)
    return db_path


@pytest.fixture
def db_2020_split(raw, db_path):
    """2020 W11-W12 stats only: the only rows after W12's as-of are the Wednesday game's."""
    games = season_2020_split_games()
    stats = [player_week(g, f"p_{g['home_team']}", 5) for g in games if g["week"] <= 12]
    raw.write_season(2020, games, player_stats=stats)
    wb.build_warehouse([2020], db_path=db_path)
    return db_path


# ---- the correct builder ---------------------------------------------------------------


def season_to_date_receptions(season: int):
    def build(v: AsOfView) -> pl.DataFrame:
        return v.sql(
            "SELECT player_id, sum(receptions) AS rec, count(*) AS games "
            "FROM fact_player_week WHERE season = ? GROUP BY player_id",
            [season],
        )

    return build


def test_correct_builder_passes(db_2025):
    asof = weekly_as_of(db_2025, 2025, 2)
    out = assert_future_invariant(season_to_date_receptions(2025), db_2025, asof, key=["player_id"])
    assert out.sort("player_id").rows() == [("p1", 4 + 5, 2), ("p2", 9 + 8, 2)]


def test_correct_builder_passes_across_a_split_week(db_2020_split):
    asof = weekly_as_of(db_2020_split, 2020, 12)
    out = assert_future_invariant(season_to_date_receptions(2020), db_2020_split, asof)
    assert "p_PIT" not in set(out["player_id"])  # the Wednesday game is not public yet


def test_builder_using_allowlisted_player_columns_passes(db_2025):
    def build(v: AsOfView) -> pl.DataFrame:
        return v.sql("SELECT gsis_id, display_name, draft_year FROM dim_player")

    assert_future_invariant(build, db_2025, weekly_as_of(db_2025, 2025, 1), key=["gsis_id"])


def test_correct_builder_passes_whatever_the_letter_case(db_2025):
    """SQL identifiers are case-insensitive: the harness must copy Fact_Player_Week too."""

    def build(v: AsOfView) -> pl.DataFrame:
        return v.sql(
            "SELECT player_id, sum(receptions) AS rec FROM Fact_Player_Week GROUP BY player_id"
        )

    assert_future_invariant(build, db_2025, weekly_as_of(db_2025, 2025, 2), key=["player_id"])


def test_correct_schedule_and_calendar_builder_passes(db_2025):
    """Upcoming games and the calendar through the view: masked values stay masked."""

    def build(v: AsOfView) -> pl.DataFrame:
        return v.sql(
            "SELECT s.game_id, s.week, s.home_team, s.gametime, w.asof_weekly_utc, w.n_games "
            "FROM fact_schedule s JOIN dim_week w USING (season, week, season_type)"
        )

    assert_future_invariant(build, db_2025, weekly_as_of(db_2025, 2025, 1), key=["game_id"])


def test_a_key_that_does_not_identify_rows_is_refused(db_2025):
    def build(v: AsOfView) -> pl.DataFrame:
        return v.sql("SELECT player_id, week FROM fact_player_week")

    with pytest.raises(ValueError, match="does not identify the output rows uniquely"):
        assert_future_invariant(build, db_2025, weekly_as_of(db_2025, 2025, 2), key=["player_id"])


# ---- deliberately leaky builders: each MUST fail --------------------------------------


def test_leaky_bypass_of_the_view_fails(db_2025):
    """Leak 1: reading the raw warehouse (wh.) sees weeks after the as-of."""

    def leaky(v: AsOfView) -> pl.DataFrame:
        return v.sql(
            "SELECT player_id, sum(receptions) AS rec FROM wh.fact_player_week "
            "WHERE season = 2025 GROUP BY player_id"
        )

    with pytest.raises(LeakageError, match=r"\[deleted\] columns \['rec'\]"):
        assert_future_invariant(leaky, db_2025, weekly_as_of(db_2025, 2025, 2), key=["player_id"])


def test_leaky_week_number_filter_fails_on_a_split_week(db_2020_split):
    """Leak 2: 'week <= N' is not time. In 2020 W12 one game was moved to Wednesday: it is
    week 12 but it was not played (let alone published) at W12's Tuesday as-of."""

    def leaky(v: AsOfView) -> pl.DataFrame:
        return v.sql(
            "SELECT player_id, sum(receptions) AS rec FROM wh.fact_player_week "
            "WHERE season = 2020 AND week <= 12 GROUP BY player_id"
        )

    asof = weekly_as_of(db_2020_split, 2020, 12)
    with pytest.raises(LeakageError, match=r"\[deleted\] the output has \d+ rows"):
        assert_future_invariant(leaky, db_2020_split, asof, key=["player_id"])


def test_leaky_label_used_as_a_feature_fails(db_2025):
    """Leak 3: outcomes (the next weeks' receptions) read through wh. inside a feature. Labels
    belong to the label builder, which must use outcomes_after (below), never the features."""

    def leaky(v: AsOfView) -> pl.DataFrame:
        return v.sql(
            "SELECT player_id, sum(receptions) AS next_rec FROM wh.fact_player_week "
            "WHERE season = 2025 AND week > 2 GROUP BY player_id"
        )

    with pytest.raises(LeakageError, match="next_rec|rows"):
        assert_future_invariant(leaky, db_2025, weekly_as_of(db_2025, 2025, 2), key=["player_id"])


def test_leaky_hindsight_column_fails(db_2025):
    """Leak 4: today's team of a player (dim_player.latest_team) through the bypass."""

    def leaky(v: AsOfView) -> pl.DataFrame:
        return v.sql("SELECT gsis_id, latest_team FROM wh.dim_player")

    with pytest.raises(LeakageError, match=r"\[perturbed\] columns \['latest_team'\]"):
        assert_future_invariant(leaky, db_2025, weekly_as_of(db_2025, 2025, 1), key=["gsis_id"])


def test_perturbation_catches_a_leak_that_keeps_the_row_count(db_2025):
    """A leak that deleting future rows cannot reveal is caught by variant (b): p2's
    receptions fall every week (9, 8, 7, 6), so without the future rows the max is still 9;
    only scrambling the future values changes the result."""

    def leaky(v: AsOfView) -> pl.DataFrame:
        return v.sql(
            "SELECT player_id, max(receptions) AS best FROM wh.fact_player_week "
            "WHERE player_id = 'p2' GROUP BY 1"
        )

    with pytest.raises(LeakageError, match=r"\[perturbed\] columns \['best'\]"):
        assert_future_invariant(leaky, db_2025, weekly_as_of(db_2025, 2025, 2), key=["player_id"])


def test_leaky_todays_position_from_an_event_table_fails(db_2025):
    """Leak 5: fact_player_week.position is today's value (not in the as-of view)."""

    def leaky(v: AsOfView) -> pl.DataFrame:
        return v.sql("SELECT DISTINCT player_id, position FROM wh.fact_player_week")

    with pytest.raises(LeakageError, match=r"\[perturbed\] columns \['position'\]"):
        assert_future_invariant(leaky, db_2025, weekly_as_of(db_2025, 2025, 4), key=["player_id"])


def test_leaky_future_week_calendar_counts_fail(raw, db_path):
    """Leak 6: a future week's game count from dim_week (2020: a postponement makes W5 a split
    week, announced long after week 1)."""
    from tests.test_available import season_2020_postponement_games

    raw.write_season(2020, season_2020_postponement_games())
    wb.build_warehouse([2020], db_path=db_path)

    def leaky(v: AsOfView) -> pl.DataFrame:
        return v.sql("SELECT week, n_games, is_split_week FROM wh.dim_week")

    def correct(v: AsOfView) -> pl.DataFrame:
        return v.sql("SELECT week, n_games, is_split_week FROM dim_week")

    asof = weekly_as_of(db_path, 2020, 1)
    assert_future_invariant(correct, db_path, asof, key=["week"])
    with pytest.raises(LeakageError, match=r"\[deleted\] columns \['n_games', 'is_split_week'\]"):
        assert_future_invariant(leaky, db_path, asof, key=["week"])


def test_leaky_final_schedule_slot_fails(db_2025):
    """Leak 7: the last regular-season week's kickoff times, picked late, read months early."""

    def leaky(v: AsOfView) -> pl.DataFrame:
        return v.sql("SELECT game_id, gametime FROM wh.fact_schedule WHERE week = 4")

    with pytest.raises(LeakageError, match=r"columns \['gametime'\]"):
        assert_future_invariant(leaky, db_2025, datetime(2025, 6, 1, tzinfo=UTC), key=["game_id"])


def test_leaky_next_draft_class_fails(raw, db_path):
    """Leak 8: listing next year's draft (its order mirrors this season's final standings)
    through wh.dim_player. The view only has players drafted by the as-of."""
    raw.write("players", None, draft_class_players())
    raw.write_season(2015, season_2015_draft_games())
    wb.build_warehouse([2015], db_path=db_path)
    asof = weekly_as_of(db_path, 2015, 5)

    def draft_capital(table):
        def build(v: AsOfView) -> pl.DataFrame:
            return v.sql(
                f"SELECT gsis_id, draft_pick, draft_team FROM {table} WHERE draft_year >= 2015"
            )

        return build

    assert assert_future_invariant(draft_capital("dim_player"), db_path, asof).height == 0
    with pytest.raises(LeakageError, match=r"\[deleted\] the output has 1 rows"):
        assert_future_invariant(draft_capital("wh.dim_player"), db_path, asof, key=["gsis_id"])


def test_large_integers_are_perturbed_without_overflow(raw, db_path):
    """fact_game.gsis holds values near 2**31 (1999101011): x * 3 + 7 would overflow INTEGER
    and crash the harness instead of testing the builder."""
    games = season_2025_games()
    for g in games:
        g["gsis"] = 2_000_000_000 + g["week"]
    raw.write_season(2025, games)
    wb.build_warehouse([2025], db_path=db_path)
    asof = weekly_as_of(db_path, 2025, 1)

    def correct(v: AsOfView) -> pl.DataFrame:
        return v.sql("SELECT game_id, gsis FROM fact_game")

    def leaky_max(v: AsOfView) -> pl.DataFrame:
        return v.sql("SELECT max(gsis) AS gsis FROM wh.fact_game")

    def leaky_min(v: AsOfView) -> pl.DataFrame:
        # deleting the future rows keeps the minimum (week 1): only the perturbed copy, whose
        # overflowing values fall back to ~x, can reveal this one
        return v.sql("SELECT min(gsis) AS gsis FROM wh.fact_game")

    # both copies are built for the correct builder (the old x * 3 + 7 crashed here)
    assert assert_future_invariant(correct, db_path, asof, key=["game_id"]).height == 4
    with pytest.raises(LeakageError, match=r"\[deleted\] columns \['gsis'\]"):
        assert_future_invariant(leaky_max, db_path, asof)
    with pytest.raises(LeakageError, match=r"\[perturbed\] columns \['gsis'\]"):
        assert_future_invariant(leaky_min, db_path, asof)


def test_labels_come_from_outcomes_after(db_2025):
    """How labels ARE built: rows strictly after the as-of, up to a horizon."""
    asof = weekly_as_of(db_2025, 2025, 2)
    with AsOfView(db_2025, asof) as v:
        everything = v.sql("SELECT week, receptions, available_at FROM wh.fact_player_week")
    horizon = weekly_as_of(db_2025, 2025, 4)
    labels = outcomes_after(everything, asof, until=horizon)
    assert sorted(labels["week"].unique().to_list()) == [3, 4]
