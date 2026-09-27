"""twm.asof: to_utc, asof_filter, outcomes_after, the official as-of lookups and AsOfView."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import duckdb
import polars as pl
import pytest

from tests.conftest import season_2025_games
from twm.asof import (
    AsOfView,
    WarehouseTooOldError,
    asof_filter,
    end_of_regular_season_as_of,
    outcomes_after,
    to_utc,
    weekly_as_of,
)
from twm.warehouse import build as wb

ASOF = datetime(2025, 9, 9, 14, tzinfo=UTC)


def _frame(**kw) -> pl.DataFrame:
    times = [
        datetime(2025, 9, 9, 13, 59, 59),
        datetime(2025, 9, 9, 14),  # exactly at the as-of
        datetime(2025, 9, 9, 14, 0, 0, 1),  # one microsecond later
        datetime(2025, 9, 16, 14),
    ]
    return pl.DataFrame({"id": [1, 2, 3, 4], "available_at": times}, schema_overrides=kw or None)


# --------------------------------------------------------------------------------------
# to_utc
# --------------------------------------------------------------------------------------


def test_to_utc_accepts_aware_and_converts_other_zones():
    assert to_utc(ASOF) == datetime(2025, 9, 9, 14)
    eastern = datetime(2025, 9, 9, 10, tzinfo=ZoneInfo("America/New_York"))  # EDT, UTC-4
    assert to_utc(eastern) == datetime(2025, 9, 9, 14)
    assert to_utc(eastern).tzinfo is None


def test_to_utc_refuses_naive_and_non_datetimes():
    with pytest.raises(TypeError, match="timezone-aware.*weekly_as_of"):
        to_utc(datetime(2025, 9, 9, 14))
    with pytest.raises(TypeError, match="datetime"):
        to_utc("2025-09-09T14:00:00Z")  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# asof_filter / outcomes_after
# --------------------------------------------------------------------------------------


def test_asof_filter_is_inclusive_at_the_boundary():
    assert asof_filter(_frame(), ASOF)["id"].to_list() == [1, 2]


def test_outcomes_after_is_strict_and_never_shares_a_row_with_features():
    df = _frame()
    labels = outcomes_after(df, ASOF)
    assert labels["id"].to_list() == [3, 4]
    features = asof_filter(df, ASOF)
    assert set(features["id"]).isdisjoint(labels["id"])
    assert features.height + labels.height == df.height
    # until is inclusive; a window of one week after the as-of
    assert outcomes_after(df, ASOF, until=ASOF + timedelta(days=7))["id"].to_list() == [3, 4]
    assert outcomes_after(df, ASOF, until=ASOF + timedelta(hours=1))["id"].to_list() == [3]
    with pytest.raises(ValueError, match="before as_of"):
        outcomes_after(df, ASOF, until=ASOF - timedelta(seconds=1))
    with pytest.raises(TypeError, match="timezone-aware"):
        outcomes_after(df, ASOF, until=datetime(2025, 9, 20))


def test_filters_support_lazyframes():
    lf = _frame().lazy()
    out = asof_filter(lf, ASOF)
    assert isinstance(out, pl.LazyFrame)
    assert out.collect()["id"].to_list() == [1, 2]
    assert outcomes_after(lf, ASOF).collect()["id"].to_list() == [3, 4]
    with pytest.raises(ValueError, match="NULL"):
        asof_filter(pl.LazyFrame({"available_at": [None, datetime(2025, 1, 1)]}), ASOF)


def test_filters_validate_the_column():
    with pytest.raises(KeyError, match="static or hindsight"):
        asof_filter(pl.DataFrame({"team_abbr": ["KC"]}), ASOF)
    with pytest.raises(TypeError, match="Datetime"):
        asof_filter(pl.DataFrame({"available_at": ["2025-09-09"]}), ASOF)
    with pytest.raises(TypeError, match="Datetime"):
        asof_filter(pl.DataFrame({"available_at": [datetime(2025, 9, 9).date()]}), ASOF)
    nulls = pl.DataFrame({"available_at": [datetime(2025, 9, 1), None]})
    with pytest.raises(ValueError, match="1 row"):
        asof_filter(nulls, ASOF)
    with pytest.raises(ValueError, match="1 row"):
        outcomes_after(nulls, ASOF)
    with pytest.raises(TypeError, match="timezone-aware"):
        asof_filter(_frame(), datetime(2025, 9, 9, 14))
    # another column name
    df = _frame().rename({"available_at": "dt"})
    assert asof_filter(df, ASOF, column="dt")["id"].to_list() == [1, 2]


def test_filters_handle_timezone_aware_columns_and_other_units():
    aware = _frame().with_columns(
        pl.col("available_at").dt.replace_time_zone("UTC").dt.convert_time_zone("Europe/Berlin")
    )
    assert asof_filter(aware, ASOF)["id"].to_list() == [1, 2]
    ms = _frame().with_columns(pl.col("available_at").cast(pl.Datetime("ms")))
    # the cast to milliseconds drops row 3's extra microsecond: it now equals the as-of
    assert asof_filter(ms, ASOF)["id"].to_list() == [1, 2, 3]


# --------------------------------------------------------------------------------------
# Official as-of lookups and AsOfView (on a tiny built warehouse)
# --------------------------------------------------------------------------------------


@pytest.fixture
def built(raw, db_path):
    raw.write_season(2025, season_2025_games())
    wb.build_warehouse([2025], db_path=db_path)
    return db_path


def test_weekly_and_end_of_season_asof(built):
    assert weekly_as_of(built, 2025, 1) == ASOF
    assert weekly_as_of(str(built), 2025, 5, season_type="POST") == datetime(
        2025, 10, 7, 14, tzinfo=UTC
    )
    assert weekly_as_of(built, 2025, 5, season_type=None).tzinfo is UTC
    con = wb.connect(built)
    assert weekly_as_of(con, 2025, 2) == datetime(2025, 9, 16, 14, tzinfo=UTC)
    assert end_of_regular_season_as_of(con, 2025) == datetime(2025, 9, 29, 12, tzinfo=UTC)
    con.close()
    with pytest.raises(LookupError, match="no REG week 5 of 2025"):
        weekly_as_of(built, 2025, 5)
    with pytest.raises(LookupError, match="2024"):
        end_of_regular_season_as_of(built, 2024)


def test_asof_view_filters_events_keeps_statics_and_hides_hindsight(built):
    with AsOfView(built, ASOF) as v:
        assert v.as_of == ASOF
        assert "fact_play" in v.tables and "dim_team" in v.tables
        assert v.sql("SELECT count(*) AS n FROM fact_game").item() == 4  # week 1 only
        assert v.sql("SELECT max(available_at) AS m FROM fact_play").item() <= to_utc(ASOF)
        assert v.table("dim_team").height == 36  # static: complete
        assert v.table("dim_week").height == 8  # the calendar is always known
        # ... but a week's schedule-derived counts only from its own as-of on
        weeks = v.sql("SELECT week, asof_weekly_utc, n_games FROM dim_week ORDER BY week")
        assert weeks["asof_weekly_utc"].null_count() == 0
        assert weeks["n_games"].to_list() == [4] + [None] * 7
        # build bookkeeping (row counts of the whole warehouse) is not a point-in-time table
        assert "build_manifest" not in v.tables
        with pytest.raises(duckdb.CatalogException, match="build_manifest"):
            v.sql("SELECT count(*) AS n FROM build_manifest")
        assert v.sql("SELECT count(*) AS n FROM wh.build_manifest").item() > 0
        players = v.table("dim_player")
        assert "latest_team" not in players.columns and "display_name" in players.columns
        assert "position" not in players.columns  # today's position: hindsight
        with pytest.raises(duckdb.BinderException, match="latest_team"):
            v.sql("SELECT latest_team FROM dim_player")
        with pytest.raises(duckdb.BinderException, match="position"):
            v.sql("SELECT position FROM fact_player_week")
        # the deliberate bypass: wh.<table> is the whole warehouse, future included
        assert v.sql("SELECT count(*) AS n FROM wh.fact_game").item() == 14
        assert "latest_team" in v.sql("SELECT * FROM wh.dim_player").columns
        # parameters and column selection
        assert v.sql("SELECT count(*) AS n FROM fact_play WHERE week = ?", [1]).item() == 8
        assert v.table("fact_game", ["game_id"]).columns == ["game_id"]
        with pytest.raises(KeyError, match="not a point-in-time table"):
            v.table("no_such_table")
        # any letter case counts (SQL identifiers are case-insensitive)
        assert v.sql("SELECT count(*) AS n FROM Fact_Snaps").item() == 4
        assert v.tables_used == {
            "fact_game", "fact_play", "dim_team", "dim_week", "build_manifest", "dim_player",
            "fact_player_week", "fact_snaps",
        }  # fmt: skip
    with pytest.raises(RuntimeError, match="closed"):
        v.sql("SELECT 1")


def test_asof_view_refuses_naive_as_of_and_missing_files(built, tmp_path):
    with pytest.raises(TypeError, match="timezone-aware"):
        AsOfView(built, datetime(2025, 9, 9, 14))
    with pytest.raises(FileNotFoundError):
        AsOfView(tmp_path / "missing.duckdb", ASOF)


def test_asof_view_is_read_only(built):
    with AsOfView(built, ASOF) as v, pytest.raises(duckdb.Error, match="read-only|read only"):
        v.sql("CREATE TABLE wh.main.x AS SELECT 1 AS a")


def test_asof_view_boundary_matches_asof_filter(built):
    con = wb.connect(built)
    plays = con.execute("SELECT game_id, play_id, available_at FROM fact_play").pl()
    con.close()
    for asof in (ASOF, datetime(2025, 9, 9, 10, 15, tzinfo=UTC), ASOF + timedelta(days=30)):
        with AsOfView(built, asof) as v:
            assert v.table("fact_play").height == asof_filter(plays, asof).height


def test_asof_view_explains_a_warehouse_built_before_b2(tmp_path):
    old = tmp_path / "b1.duckdb"
    con = duckdb.connect(str(old))
    con.execute("CREATE TABLE fact_play AS SELECT 'g' AS game_id, 1 AS play_id")
    con.close()
    with pytest.raises(WarehouseTooOldError, match="built before B2.*twm build"):
        AsOfView(old, ASOF)


def test_asof_view_explains_a_warehouse_without_the_row_rules(built, tmp_path):
    """A warehouse built with the first B2 rules (no dim_player.public_from_utc)."""
    old = tmp_path / "b2_first.duckdb"
    con = duckdb.connect(str(old))
    con.execute(f"ATTACH '{built}' AS src (READ_ONLY)")
    con.execute("CREATE TABLE dim_player AS SELECT * EXCLUDE (public_from_utc) FROM src.dim_player")
    con.close()
    with pytest.raises(WarehouseTooOldError, match="dim_player has no public_from_utc"):
        AsOfView(old, ASOF)


def test_asof_filter_refuses_a_frame_joined_before_filtering():
    stats = _frame()
    games = _frame().with_columns(pl.col("id").alias("game"))
    joined = stats.join(games, on="id")  # available_at + available_at_right
    with pytest.raises(ValueError, match="available_at_right.*BEFORE"):
        asof_filter(joined, ASOF)
    # the right way: filter each table, then join
    ok = asof_filter(stats, ASOF).join(asof_filter(games, ASOF), on="id")
    assert ok["id"].to_list() == [1, 2]
