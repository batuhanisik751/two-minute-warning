"""K and D/ST scoring (S1): one test per ESPN rule, every points-allowed tier boundary, the
yards-allowed option, config validation and SQL = polars."""

import random

import duckdb
import polars as pl
import pytest
from pydantic import ValidationError

from twm import scoring_kdst as sk
from twm.config import DefenseScoring, Scoring, scoring

# ESPN's defaults as read from https://support.espn.com/hc/en-us/articles/360003914032 on
# 2026-09-29 (S1 brief); the shipped config must say exactly this.
ESPN_KICKING = {
    "fg_made_0_39": 3, "fg_made_40_49": 4, "fg_made_50_59": 5, "fg_made_60_plus": 6,
    "fg_missed": -1, "fg_blocked": -1, "pat_made": 1, "pat_missed": 0,
}  # fmt: skip
ESPN_DEFENSE = {
    "sacks": 1, "interceptions": 2, "fumble_recoveries": 2, "blocked_kicks": 2, "safeties": 2,
    "kickoff_return_tds": 6, "punt_return_tds": 6, "interception_return_tds": 6,
    "fumble_return_tds": 6, "blocked_kick_return_tds": 6,
}  # fmt: skip
ESPN_PA_TIERS = [(0, 5), (6, 4), (13, 3), (17, 1), (27, 0), (34, -1), (45, -3), (None, -5)]

K_COLS = sorted({c for cs in sk.KICKING_COLUMNS.values() for c in cs})
D_COLS = sorted({c for cs in sk.DEFENSE_COLUMNS.values() for c in cs})


def kicker(**stats: int) -> pl.DataFrame:
    assert set(stats) <= set(K_COLS), set(stats) - set(K_COLS)
    return pl.DataFrame(
        [{c: stats.get(c, 0) for c in K_COLS}], schema=dict.fromkeys(K_COLS, pl.Int32)
    )


def defense(points_allowed: int | None = 20, yards_allowed: int | None = 300, **stats: float):
    assert set(stats) <= set(D_COLS), set(stats) - set(D_COLS)
    row = {c: stats.get(c, 0) for c in D_COLS}
    row |= {"points_allowed": points_allowed, "yards_allowed": yards_allowed}
    schema = {c: pl.Float64 if c == "def_sacks" else pl.Int32 for c in D_COLS}
    return pl.DataFrame(
        [row], schema={**schema, "points_allowed": pl.Int32, "yards_allowed": pl.Int32}
    )


def k_points(df: pl.DataFrame, rules: sk.KickingRules | None = None) -> float:
    polars = sk.score_kicking(df, rules)["fantasy_points"][0]
    sql = duckdb.sql(f"SELECT {sk.score_kicking_sql(rules)} FROM df").fetchone()[0]
    assert polars == pytest.approx(sql)
    return polars


def d_points(df: pl.DataFrame, rules: sk.DefenseRules | None = None) -> float:
    polars = sk.score_defense(df, rules)["fantasy_points"][0]
    sql = duckdb.sql(f"SELECT {sk.score_defense_sql(rules)} FROM df").fetchone()[0]
    assert polars == pytest.approx(sql)
    return polars


def test_shipped_config_is_espn_default():
    sc = scoring()
    assert sc.kicking is not None and sc.defense is not None
    assert sc.kicking.points() == ESPN_KICKING
    assert sc.defense.points() == ESPN_DEFENSE
    assert sc.defense.points_allowed_tiers == ESPN_PA_TIERS
    assert sc.defense.yards_allowed_tiers == []  # off by default (the two ESPN pages disagree)


# ---- kickers: one test per rule ------------------------------------------------------------


@pytest.mark.parametrize(
    ("column", "points"),
    [
        ("fg_made_0_19", 3),  # ESPN 0-39 bucket = nflverse 0-19 + 20-29 + 30-39
        ("fg_made_20_29", 3),
        ("fg_made_30_39", 3),
        ("fg_made_40_49", 4),
        ("fg_made_50_59", 5),
        ("fg_made_60_", 6),
    ],
)
def test_each_fg_distance_bucket(column, points):
    assert k_points(kicker(**{column: 1})) == points
    assert k_points(kicker(**{column: 2})) == 2 * points


def test_missed_blocked_and_pat_rules():
    assert k_points(kicker(fg_missed=1)) == -1  # FG Missed (any distance)
    assert k_points(kicker(fg_blocked=1)) == -1  # a blocked FG is an unmade FG
    assert k_points(kicker(pat_made=1)) == 1
    assert k_points(kicker(pat_missed=1)) == 0  # no ESPN default
    # distance buckets of misses are NOT read (fg_missed already counts them)
    assert k_points(kicker().with_columns(fg_missed_50_59=pl.lit(1))) == 0


def test_hand_computed_kicker_game():
    # 2 x 30-39 (6) + 44 yd (4) + 53 yd (5) + 1 miss (-1) + 3 PAT (3) = 17
    df = kicker(fg_made_30_39=2, fg_made_40_49=1, fg_made_50_59=1, fg_missed=1, pat_made=3)
    assert k_points(df) == 17
    bd = sk.score_kicking(df, breakdown=True)
    parts = [c for c in bd.columns if c.startswith("fp_")]
    assert bd.select(pl.sum_horizontal(parts))[0, 0] == bd["fantasy_points"][0] == 17


def test_kicking_rules_from_a_league():
    rules = sk.KickingRules(ESPN_KICKING).with_points(pat_missed=-1, fg_made_50_59=6)
    assert k_points(kicker(pat_missed=1), rules) == -1
    assert k_points(kicker(fg_made_50_59=1), rules) == 6
    with pytest.raises(ValueError, match="unknown kicking stats"):
        sk.KickingRules({"fg_made_0_49": 3})


# ---- D/ST: one test per event, every points-allowed tier boundary --------------------------

NEUTRAL_PA = 20  # the 18-27 tier scores 0, so an event's points show alone


@pytest.mark.parametrize(
    ("column", "points"),
    [
        ("def_sacks", 1),
        ("def_interceptions", 2),
        ("fumble_recovery_opp", 2),
        ("def_punt_blocks", 2),
        ("def_fg_blocks", 2),
        ("def_pat_blocks", 2),
        ("def_safeties", 2),
        ("kickoff_return_tds", 6),
        ("punt_return_tds", 6),
        ("interception_return_tds", 6),
        ("fumble_return_tds", 6),
        ("blocked_kick_return_tds", 6),
    ],
)
def test_each_dst_event(column, points):
    assert d_points(defense(NEUTRAL_PA, **{column: 1})) == points
    assert d_points(defense(NEUTRAL_PA, **{column: 3})) == 3 * points


def test_half_sacks_score_half_a_point():
    assert d_points(defense(NEUTRAL_PA, def_sacks=2.5)) == 2.5


@pytest.mark.parametrize(
    ("allowed", "points"),
    [
        (0, 5), (1, 4), (6, 4), (7, 3), (13, 3), (14, 1), (17, 1), (18, 0), (27, 0),
        (28, -1), (34, -1), (35, -3), (45, -3), (46, -5), (70, -5),
    ],
)  # fmt: skip
def test_every_points_allowed_tier_boundary(allowed, points):
    assert d_points(defense(allowed)) == points
    assert sk.tier_points(allowed, ESPN_PA_TIERS) == points


def test_missing_points_allowed_scores_no_tier_points():
    assert d_points(defense(None, def_sacks=2)) == 2
    assert sk.tier_points(None, ESPN_PA_TIERS) == 0


def test_hand_computed_dst_game():
    # 4 sacks (4) + 1 INT (2) + 1 fumble recovery (2) + INT return TD (6) + 10 allowed (3) = 17
    df = defense(
        10, def_sacks=4, def_interceptions=1, fumble_recovery_opp=1, interception_return_tds=1
    )
    assert d_points(df) == 17
    bd = sk.score_defense(df, breakdown=True)
    assert bd["fp_points_allowed"][0] == 3
    parts = [c for c in bd.columns if c.startswith("fp_")]
    assert bd.select(pl.sum_horizontal(parts))[0, 0] == bd["fantasy_points"][0]


def test_yards_allowed_tiers_are_off_by_default_and_scored_when_set():
    default = sk.DefenseRules.from_config()
    assert "yards_allowed" not in default.required_columns()
    assert d_points(defense(NEUTRAL_PA, yards_allowed=50)) == 0
    rules = sk.DefenseRules(ESPN_DEFENSE, tuple(ESPN_PA_TIERS), ((99, 5), (299, 2), (None, -1)))
    assert "yards_allowed" in rules.required_columns()
    assert d_points(defense(NEUTRAL_PA, yards_allowed=99), rules) == 5
    assert d_points(defense(NEUTRAL_PA, yards_allowed=100), rules) == 2
    assert d_points(defense(NEUTRAL_PA, yards_allowed=300), rules) == -1


# ---- config validation, errors, SQL = polars ------------------------------------------------


def _defense_cfg(**changes) -> dict:
    return {**ESPN_DEFENSE, "points_allowed_tiers": ESPN_PA_TIERS, **changes}


@pytest.mark.parametrize(
    "tiers",
    [
        [(0, 5), (6, 4)],  # no unbounded last tier
        [(0, 5), (None, 4), (13, 3)],  # unbounded tier in the middle
        [(6, 4), (0, 5), (None, -5)],  # not ascending
        [(0, 5), (0, 4), (None, -5)],  # duplicate bound
        [(-1, 5), (None, 0)],  # negative bound
        [],  # points allowed must be scored
    ],
)
def test_bad_points_allowed_tiers_are_rejected(tiers):
    with pytest.raises(ValidationError, match="points_allowed_tiers"):
        DefenseScoring(**_defense_cfg(points_allowed_tiers=tiers))


def test_config_rejects_typos_and_missing_stats():
    with pytest.raises(ValidationError):
        DefenseScoring(**_defense_cfg(sack=1))  # typo: extra key
    with pytest.raises(ValidationError):
        DefenseScoring(**{k: v for k, v in _defense_cfg().items() if k != "safeties"})
    with pytest.raises(ValidationError, match="yards_allowed_tiers"):
        DefenseScoring(**_defense_cfg(yards_allowed_tiers=[(100, 5)]))


def test_config_without_kdst_sections_loads_but_cannot_score_them():
    base = scoring().model_dump(exclude={"kicking", "defense"})
    cfg = Scoring(**base)
    assert cfg.kicking is None and cfg.defense is None
    with pytest.raises(ValueError, match="no kicking: section"):
        sk.KickingRules.from_config(cfg)
    with pytest.raises(ValueError, match="no defense: section"):
        sk.DefenseRules.from_config(cfg)


def test_missing_column_is_a_clear_error():
    with pytest.raises(KeyError, match="points_allowed"):
        sk.score_defense(defense().drop("points_allowed"))
    with pytest.raises(KeyError, match="fg_made_60_"):
        sk.score_kicking(kicker().drop("fg_made_60_"))


def test_nulls_count_as_zero_and_lazyframes_work():
    df = kicker(pat_made=2).with_columns(fg_missed=pl.lit(None, pl.Int32))
    assert k_points(df) == 2
    assert sk.score_defense(defense(0).lazy()).collect()["fantasy_points"][0] == 5


def test_sql_matches_polars_on_random_rows():
    rng = random.Random(7)
    rows = [
        {c: rng.randint(0, 3) for c in D_COLS}
        | {
            "points_allowed": rng.choice([None, *range(0, 60)]),
            "yards_allowed": rng.randint(0, 600),
        }
        for _ in range(300)
    ]
    df = pl.DataFrame(rows)
    rules = sk.DefenseRules(ESPN_DEFENSE, tuple(ESPN_PA_TIERS), ((199, 3), (None, -2)))
    for r in (None, rules):
        want = sk.score_defense(df, r)["fantasy_points"].to_list()
        got = [x[0] for x in duckdb.sql(f"SELECT {sk.score_defense_sql(r)} FROM df").fetchall()]
        assert got == pytest.approx(want)
        aliased = duckdb.sql(f"SELECT {sk.score_defense_sql(r, table_alias='t')} FROM df t")
        assert [x[0] for x in aliased.fetchall()] == pytest.approx(want)
