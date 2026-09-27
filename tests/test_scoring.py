"""Scoring engine: hand-computed QB/RB/WR/TE weeks, presets, config validation, SQL = polars,
and (opt-in, real cache) an exact reproduction of nflverse's precomputed points."""

from pathlib import Path

import duckdb
import polars as pl
import pytest
import yaml
from pydantic import ValidationError

from twm import scoring as sc
from twm.config import Scoring, scoring
from twm.sources import nflverse as nv

# Every column the engine can read, as synthetic Int32 stats (0 unless a test sets it).
ALL_COLUMNS = sorted(
    {c for cols in sc.STAT_COLUMNS.values() for c in cols}
    | {c for cols in sc.FUMBLE_COLUMNS.values() for c in cols}
)


def week(name: str, **stats: int) -> dict:
    unknown = set(stats) - set(ALL_COLUMNS)
    assert not unknown, unknown
    return {"player": name, **{c: 0 for c in ALL_COLUMNS}, **stats}


def frame(*rows: dict) -> pl.DataFrame:
    return pl.DataFrame(
        list(rows), schema={"player": pl.Utf8, **{c: pl.Int32 for c in ALL_COLUMNS}}
    )


# Hand-computed examples (full PPR, the shipped config). Each comment is the arithmetic.
QB = week(
    "QB",
    passing_yards=300,  # 300 * 0.04 = 12
    passing_tds=2,  # 2 * 4 = 8
    passing_interceptions=1,  # -2
    rushing_yards=20,  # 2
    rushing_tds=1,  # 6
    sack_fumbles_lost=1,  # a strip-sack: lost on a scrimmage play ...
    fumbles_lost_total=1,  # ... so both fumble counts say 1: -2
)  # 12 + 8 - 2 + 2 + 6 - 2 = 24
RB = week(
    "RB",
    rushing_yards=85,  # 8.5
    rushing_tds=1,  # 6
    receptions=4,  # 4
    receiving_yards=30,  # 3
    rushing_2pt_conversions=1,  # 2
)  # 8.5 + 6 + 4 + 3 + 2 = 23.5
WR = week(
    "WR",
    receptions=7,  # 7
    receiving_yards=112,  # 11.2
    receiving_tds=1,  # 6
    special_teams_tds=1,  # a punt return touchdown: 6
    fumbles_lost_total=1,  # lost on a kick return, NOT a scrimmage fumble: -2 under "all"
)  # 7 + 11.2 + 6 + 6 - 2 = 28.2 (30.2 if only scrimmage fumbles count)
TE = week(
    "TE",
    receptions=5,  # 5
    receiving_yards=48,  # 4.8
    receiving_2pt_conversions=1,  # 2
    fumble_recovery_tds=1,  # recovered a teammate's fumble in the end zone: 6
)  # 5 + 4.8 + 2 + 6 = 17.8 (11.8 in nflverse's own points, which skip this TD)


def points(rows, rules=None) -> dict[str, float]:
    out = sc.score(frame(*rows), rules)
    return dict(zip(out["player"], out["fantasy_points"], strict=True))


def test_shipped_config_is_full_ppr_with_all_fumbles():
    rules = sc.ScoringRules.from_config()
    assert rules == sc.full_ppr()
    assert rules.fumbles_lost_scope == "all"


def test_hand_computed_weeks_full_ppr():
    got = points([QB, RB, WR, TE])
    assert got == pytest.approx({"QB": 24.0, "RB": 23.5, "WR": 28.2, "TE": 17.8})


def test_half_ppr_and_standard_only_change_catches():
    half = points([RB, WR, TE], sc.half_ppr())
    std = points([RB, WR, TE], sc.standard())
    assert half == pytest.approx({"RB": 21.5, "WR": 24.7, "TE": 15.3})
    assert std == pytest.approx({"RB": 19.5, "WR": 21.2, "TE": 12.8})


def test_fumble_scope_decides_whether_a_return_fumble_costs_points():
    scrimmage = sc.ScoringRules(dict(sc.full_ppr().points), fumbles_lost_scope="scrimmage")
    assert points([WR], scrimmage)["WR"] == pytest.approx(30.2)
    # the strip-sack is a scrimmage fumble: it counts under both scopes
    assert points([QB], scrimmage)["QB"] == pytest.approx(24.0)


def test_nflverse_presets_skip_fumble_recovery_touchdowns():
    assert points([TE], sc.nflverse_ppr())["TE"] == pytest.approx(11.8)
    assert points([WR], sc.nflverse_ppr())["WR"] == pytest.approx(30.2)
    assert points([RB], sc.nflverse_standard())["RB"] == pytest.approx(19.5)


def test_missing_stats_count_as_zero():
    df = frame(RB).with_columns(pl.lit(None, pl.Int32).alias("receiving_yards"))
    assert sc.score(df)["fantasy_points"][0] == pytest.approx(20.5)  # 23.5 - 3


def test_missing_column_is_a_clear_error():
    with pytest.raises(KeyError, match="receptions"):
        sc.score(frame(RB).drop("receptions"))


def test_unused_columns_are_not_required():
    """A league that does not score return TDs does not need that column."""
    rules = sc.full_ppr().with_points(**{"misc.special_teams_touchdowns": 0})
    df = frame(RB).drop("special_teams_tds")
    assert sc.score(df, rules)["fantasy_points"][0] == pytest.approx(23.5)


def test_breakdown_columns_sum_to_the_total():
    out = sc.score(frame(QB, WR), breakdown=True)
    parts = [c for c in out.columns if c.startswith("fp_")]
    assert "fp_passing_yards" in parts and "fp_misc_fumbles_lost" in parts
    assert out.select(pl.sum_horizontal(parts).alias("parts"))["parts"].to_list() == pytest.approx(
        out["fantasy_points"].to_list()
    )
    qb = out.filter(pl.col("player") == "QB")
    assert qb["fp_passing_yards"][0] == pytest.approx(12.0)
    assert qb["fp_misc_fumbles_lost"][0] == pytest.approx(-2.0)


def test_lazyframe_is_supported():
    out = sc.score(frame(RB).lazy()).collect()
    assert out["fantasy_points"][0] == pytest.approx(23.5)


@pytest.mark.parametrize(
    "rules",
    [sc.full_ppr(), sc.half_ppr(), sc.standard(), sc.nflverse_ppr(), sc.nflverse_standard()],
)
def test_sql_expression_matches_polars(rules):
    # includes a NULL stat: in SQL, NULL + x is NULL unless the expression guards it
    df = frame(QB, RB, WR, TE).with_columns(
        pl.when(pl.col("player") == "RB")
        .then(None)
        .otherwise(pl.col("receiving_yards"))
        .alias("receiving_yards")
    )
    expected = sc.score(df, rules)["fantasy_points"].to_list()
    con = duckdb.connect()
    con.register("w", df.to_arrow())
    got = [r[0] for r in con.execute(f"SELECT {sc.score_sql(rules)} FROM w").fetchall()]
    assert got == pytest.approx(expected)
    got_alias = con.execute(f"SELECT {sc.score_sql(rules, table_alias='w')} FROM w").fetchall()
    assert [r[0] for r in got_alias] == pytest.approx(expected)


def test_unknown_stat_or_scope_is_rejected():
    with pytest.raises(ValueError, match="unknown scoring stats"):
        sc.ScoringRules({"receiving.recptions": 1})
    with pytest.raises(ValueError, match="fumbles_lost_scope"):
        sc.ScoringRules({}, "returns")  # type: ignore[arg-type]


def test_config_rejects_typos():
    good = yaml.safe_load((Path(__file__).parents[1] / "config" / "scoring.yaml").read_text())
    assert Scoring(**good) == scoring()
    with pytest.raises(ValidationError, match="unknown stats"):
        Scoring(**{**good, "receiving": {**good["receiving"], "recptions": 1}})
    with pytest.raises(ValidationError):
        Scoring(**{**good, "recieving": good["receiving"]})
    with pytest.raises(ValidationError):
        Scoring(**{**good, "options": {"fumbles_lost_scope": "returns"}})


def test_stat_registry_covers_every_config_stat():
    assert set(sc.STAT_KEYS) == set(sc.full_ppr().points)


# --------------------------------------------------------------------------------------
# Real data (opt-in): reproduce nflverse's own points exactly, from the cache, no download
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_weeks() -> pl.DataFrame:
    paths = [nv.cache_path("player_stats", s) for s in range(1999, 2027)]
    paths = [p for p in paths if p.exists()]
    if len(paths) < 20:
        pytest.skip("real player_stats cache not present (run `twm ingest player_stats`)")
    files = ", ".join(f"'{p}'" for p in paths)
    cols = ", ".join(ALL_COLUMNS)
    return pl.from_arrow(
        duckdb.connect()
        .execute(
            f"""SELECT season, week, player_id, position, fantasy_points, fantasy_points_ppr,
                       {cols}
                FROM read_parquet([{files}], union_by_name = true)
                WHERE position IN ('QB', 'RB', 'WR', 'TE')"""
        )
        .arrow()
    )


@pytest.mark.realdata
def test_nflverse_points_are_reproduced_exactly(real_weeks):
    """Every QB/RB/WR/TE week 1999-2026: our engine with the nflverse presets gives nflverse's
    precomputed points, so the component arithmetic is right."""
    assert real_weeks.height > 140_000
    ppr = sc.score(real_weeks, sc.nflverse_ppr())
    std = sc.score(real_weeks, sc.nflverse_standard())
    assert (ppr["fantasy_points"] - ppr["fantasy_points_ppr"]).abs().max() < 1e-6
    assert (std["fantasy_points"] - real_weeks["fantasy_points"]).abs().max() < 1e-6


@pytest.mark.realdata
def test_default_differs_from_nflverse_only_by_the_two_documented_stats(real_weeks):
    ours = sc.score(real_weeks)["fantasy_points"]
    scrimmage = (
        real_weeks["sack_fumbles_lost"].fill_null(0)
        + real_weeks["rushing_fumbles_lost"].fill_null(0)
        + real_weeks["receiving_fumbles_lost"].fill_null(0)
    )
    explained = -2 * (real_weeks["fumbles_lost_total"].fill_null(0) - scrimmage) + 6 * real_weeks[
        "fumble_recovery_tds"
    ].fill_null(0)
    diff = ours - real_weeks["fantasy_points_ppr"]
    assert (diff - explained).abs().max() < 1e-6
    assert (diff.abs() > 1e-6).sum() == ((explained != 0).sum())
