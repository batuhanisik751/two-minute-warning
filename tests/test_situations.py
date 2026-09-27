"""Garbage-time and neutral flags (PROJECT_SPEC 7.3): boundaries, SQL = polars, config, and the
flags stored on fact_play."""

import duckdb
import polars as pl
import pytest
from pydantic import ValidationError

from twm import situations as st
from twm.config import GarbageTimeConfig, NeutralConfig
from twm.warehouse import build as wb

# (wp, half_seconds_remaining, score_differential) -> (is_garbage_time, is_neutral), default rules
CASES = [
    # extreme win probability, lots of time left: garbage time
    ((0.049, 1500.0, -20.0), (True, False)),
    ((0.951, 1500.0, 20.0), (True, False)),
    # the thresholds themselves are not garbage ("below 0.05", "above 0.95")
    ((0.05, 1500.0, -20.0), (False, False)),
    ((0.95, 1500.0, 20.0), (False, False)),
    # final 2 minutes of a half (<= 120 s) in a one-score game (|diff| <= 8): never garbage
    ((0.02, 120.0, -8.0), (False, False)),
    ((0.98, 60.0, 8.0), (False, False)),
    # ... but one second earlier, or a two-score game, it is
    ((0.02, 121.0, -8.0), (True, False)),
    ((0.02, 120.0, -9.0), (True, False)),
    # overtime: the same rule applies to the last 2 minutes of the overtime period
    ((0.03, 90.0, 3.0), (False, False)),
    # neutral: 0.20-0.80 inclusive and MORE than 120 s left
    ((0.20, 121.0, 0.0), (False, True)),
    ((0.80, 121.0, 0.0), (False, True)),
    ((0.19, 121.0, 0.0), (False, False)),
    ((0.81, 900.0, 0.0), (False, False)),
    ((0.50, 120.0, 0.0), (False, False)),
    # missing inputs: neither flag (never NULL)
    ((None, 1500.0, 0.0), (False, False)),
    ((0.01, None, 3.0), (False, False)),
    ((0.5, None, 0.0), (False, False)),
]


def plays(cases=CASES) -> pl.DataFrame:
    return pl.DataFrame(
        [{"wp": w, "half_seconds_remaining": h, "score_differential": d} for (w, h, d), _ in cases],
        schema={name: pl.Float64 for name in st.INPUT_COLUMNS},
    )


def expected(cases=CASES) -> list[tuple[bool, bool]]:
    return [flags for _, flags in cases]


def test_boundaries_polars():
    out = st.flag_plays(plays())
    assert list(zip(out["is_garbage_time"], out["is_neutral"], strict=True)) == expected()
    assert out["is_garbage_time"].null_count() == 0 and out["is_neutral"].null_count() == 0


def test_boundaries_sql_match_polars():
    con = duckdb.connect()
    con.register("p", plays().to_arrow())
    rows = con.execute(f"SELECT {st.garbage_time_sql()}, {st.neutral_sql()} FROM p").fetchall()
    assert [tuple(r) for r in rows] == expected()
    aliased = con.execute(
        f"SELECT {st.garbage_time_sql(alias='p')}, {st.neutral_sql(alias='p')} FROM p"
    ).fetchall()
    assert [tuple(r) for r in aliased] == expected()


def test_flags_are_never_both_true():
    grid = pl.DataFrame(
        {
            "wp": [x / 100 for x in range(101)] * 3,
            "half_seconds_remaining": [30.0] * 101 + [120.0] * 101 + [1000.0] * 101,
            "score_differential": [3.0] * 101 + [-14.0] * 101 + [0.0] * 101,
        }
    )
    out = st.flag_plays(grid)
    assert not (out["is_garbage_time"] & out["is_neutral"]).any()


def test_thresholds_come_from_the_rules():
    looser = st.SituationRules(garbage_wp_low=0.10, garbage_wp_high=0.90)
    out = st.flag_plays(plays([((0.08, 1500.0, -20.0), (True, False))]), looser)
    assert out["is_garbage_time"].to_list() == [True]
    assert st.flag_plays(plays([((0.08, 1500.0, -20.0), (False, False))]))[
        "is_garbage_time"
    ].to_list() == [False]


def test_shipped_config_matches_the_spec():
    r = st.SituationRules.from_config()
    assert r == st.SituationRules()  # 0.05/0.95, 120 s, 8 points, neutral 0.20-0.80, > 120 s
    assert "below 0.05 or above 0.95" in r.describe_garbage_time()


def test_config_is_validated():
    with pytest.raises(ValidationError, match="wp_low < wp_high"):
        GarbageTimeConfig(
            wp_low=0.95, wp_high=0.05, exclude_final_seconds_half=120, one_possession_points=8
        )
    with pytest.raises(ValidationError, match="1800"):
        NeutralConfig(wp_low=0.2, wp_high=0.8, min_half_seconds_remaining=5000)
    with pytest.raises(ValidationError):
        GarbageTimeConfig(
            wp_low=0.05,
            wp_high=0.95,
            exclude_final_seconds_half=120,
            one_possession_points=8,
            typo=1,
        )


def test_missing_input_column_is_a_clear_error():
    with pytest.raises(KeyError, match="score_differential"):
        st.flag_plays(plays().drop("score_differential"))


def test_warehouse_stores_the_flags(raw, db_path):
    from tests.conftest import PBP_DTYPES, frame, plays_for, season_2025_games

    games = season_2025_games()
    raw.write_season(2025, games)
    rows = plays_for(games[:1], n=4)
    inputs = [
        (0.01, 1500.0, -21.0),  # blowout, early: garbage time
        (0.5, 1500.0, 0.0),  # coin flip, early: neutral
        (0.02, 100.0, -7.0),  # one-score game, last two minutes: neither
        (None, 800.0, 0.0),  # no win probability: neither
    ]
    for row, (w, h, d) in zip(rows, inputs, strict=True):
        row.update(wp=w, half_seconds_remaining=h, score_differential=d)
    raw.write("pbp", 2025, frame(rows, PBP_DTYPES))
    wb.build_warehouse([2025], db_path=db_path)
    con = duckdb.connect(str(db_path), read_only=True)
    types = dict(
        con.execute(
            "SELECT column_name, data_type FROM duckdb_columns() WHERE table_name = 'fact_play' "
            "AND column_name IN ('is_garbage_time', 'is_neutral')"
        ).fetchall()
    )
    assert types == {"is_garbage_time": "BOOLEAN", "is_neutral": "BOOLEAN"}
    got = con.execute(
        "SELECT is_garbage_time, is_neutral FROM fact_play ORDER BY play_id"
    ).fetchall()
    assert got == [(True, False), (False, True), (False, False), (False, False)]
    comment = con.execute(
        "SELECT comment FROM duckdb_columns() WHERE table_name = 'fact_play' "
        "AND column_name = 'is_garbage_time'"
    ).fetchone()[0]
    assert "below 0.05 or above 0.95" in comment


# --------------------------------------------------------------------------------------
# Real data (opt-in): plausible shares, flags stored exactly as the rule says
# --------------------------------------------------------------------------------------


@pytest.mark.realdata
def test_real_flags_are_plausible_and_match_the_rule(real_full_db):
    db_path, _manifest = real_full_db
    con = duckdb.connect(str(db_path), read_only=True)
    nulls, both = con.execute(
        "SELECT count(*) FILTER (WHERE is_garbage_time IS NULL OR is_neutral IS NULL), "
        "count(*) FILTER (WHERE is_garbage_time AND is_neutral) FROM fact_play"
    ).fetchone()
    assert nulls == 0 and both == 0
    shares = con.execute(
        "SELECT season, avg(is_garbage_time::INT), avg(is_neutral::INT) FROM fact_play "
        "WHERE play_type IN ('pass', 'run') GROUP BY 1 ORDER BY 1"
    ).fetchall()
    assert len(shares) == 28
    for season, garbage, neutral in shares:
        assert 0.08 < garbage < 0.25, (season, garbage)
        assert 0.45 < neutral < 0.65, (season, neutral)
    # the stored flags equal the rule recomputed from the stored inputs
    mismatches = con.execute(
        f"SELECT count(*) FROM fact_play WHERE is_garbage_time <> {st.garbage_time_sql()} "
        f"OR is_neutral <> {st.neutral_sql()}"
    ).fetchone()[0]
    assert mismatches == 0
