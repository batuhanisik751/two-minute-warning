"""Coach tendencies (feature #10, C10a) on synthetic play-by-play frames."""

from __future__ import annotations

import duckdb
import numpy as np
import polars as pl
import pytest

from twm.modules.coach_tendencies import build, fantasy, persistence, plays, season

DEFAULTS = {
    "game_id": "2025_01_AAA_BBB", "play_id": 1, "season": 2025, "week": 1, "season_type": "REG",
    "posteam": "AAA", "qtr": 1, "down": 1, "ydstogo": 10, "fixed_drive": 1, "play_type": "pass",
    "pass": 1, "rush": 0, "no_huddle": 0, "shotgun": 0, "timeout": 0,
    "game_seconds_remaining": 3000, "pass_oe": 0.0, "is_neutral": True, "coach_id": "coach_a",
}  # fmt: skip
SCHEMA = {
    "game_id": pl.String, "play_id": pl.Int64, "season": pl.Int64, "week": pl.Int64,
    "season_type": pl.String, "posteam": pl.String, "qtr": pl.Int64, "down": pl.Int64,
    "ydstogo": pl.Int64, "fixed_drive": pl.Int64, "play_type": pl.String, "pass": pl.Int64,
    "rush": pl.Int64, "no_huddle": pl.Int64, "shotgun": pl.Int64, "timeout": pl.Int64,
    "game_seconds_remaining": pl.Int64, "pass_oe": pl.Float64, "is_neutral": pl.Boolean,
    "coach_id": pl.String,
}  # fmt: skip


def frame(*rows: dict) -> pl.DataFrame:
    """Plays from overrides of DEFAULTS; play_id defaults to the row's position."""
    full = [{**DEFAULTS, "play_id": i + 1, **r} for i, r in enumerate(rows)]
    return pl.DataFrame(full, schema=SCHEMA)


def run(*rows: dict) -> pl.DataFrame:
    return plays.flag_plays(frame(*rows))


RUN = {"play_type": "run", "pass": 0, "rush": 1}


def test_neutral_filter_keeps_quarters_1_3_downs_1_3_and_neutral_state():
    f = run(
        {},  # neutral 1st-down pass
        {"qtr": 4},  # fourth quarter: not neutral
        {"down": 4},  # fourth down: a choice, not a neutral snap
        {"is_neutral": False},  # win probability / clock outside the neutral band
        {"down": None, "play_type": "pass"},  # two-point try: no down
        {"play_type": "qb_kneel", "pass": 0, "rush": 0},  # kneel: not a snap here
        {"down": 3, **RUN},  # neutral 3rd-down run: neutral, not early
    )
    assert f["is_off"].to_list() == [True, True, True, True, False, False, True]
    assert f["is_neutral_play"].to_list() == [True, False, False, False, False, False, True]
    assert f["is_early"].to_list() == [True, False, False, False, False, False, False]


def test_fourth_down_flags():
    f = run(
        {"down": 4, "ydstogo": 1, **RUN},  # go, short
        {"down": 4, "ydstogo": 2, "play_type": "punt", "pass": 0},  # short, punted
        {"down": 4, "ydstogo": 8, "play_type": "field_goal", "pass": 0},
        {"down": 4, "ydstogo": 1, "play_type": "no_play"},  # replayed: not a choice
        {"down": 4, "ydstogo": 1, "is_neutral": False, **RUN},  # out of the neutral band
        {"down": 4, "ydstogo": 3, "qtr": 4},  # fourth quarter counts for fourth downs
    )
    assert f["is_fourth"].to_list() == [True, True, True, False, False, True]
    assert f["is_go"].to_list() == [True, False, False, False, False, True]
    assert f["is_short"].to_list() == [True, True, False, False, False, False]


TIMEOUT_ROW = {"play_type": None, "pass": 0, "rush": 0, "down": None, "timeout": 1}


def pace_game() -> pl.DataFrame:
    return run(
        {"game_seconds_remaining": 3000},  # 1 -> 2: 25 s
        {"game_seconds_remaining": 2975, **RUN},  # 2 -> 3: 6 s (clock stopped after it)
        {"game_seconds_remaining": 2969, "timeout": 1},  # timeout on the play's row: no pair
        {"game_seconds_remaining": 2940},  # next row is a timeout row: no pair
        {"game_seconds_remaining": 2930, **TIMEOUT_ROW},
        {"game_seconds_remaining": 2930},  # next snap is the other team's drive: no pair
        {"game_seconds_remaining": 2910, "posteam": "BBB", "fixed_drive": 2},  # 7 -> 8: 30 s
        {"game_seconds_remaining": 2880, "posteam": "BBB", "fixed_drive": 2},  # 8 -> 9: 75 s
        {"game_seconds_remaining": 2805, "posteam": "BBB", "fixed_drive": 2},  # quarter end
        {"game_seconds_remaining": 2700, "posteam": "BBB", "fixed_drive": 2, "qtr": 2},
        {"game_seconds_remaining": 2670, "posteam": "BBB", "fixed_drive": 2, "qtr": 2,
         "is_neutral": False},  # 10 -> 11 counts (10 is neutral); 11 -> 12 does not
        {"game_seconds_remaining": 2640, "posteam": "BBB", "fixed_drive": 2, "qtr": 2},
    )  # fmt: skip


def test_pace_pairs_respect_drives_quarters_timeouts_and_cap():
    p = plays.pace_pairs(pace_game())
    assert p["play_id"].to_list() == [1, 2, 7, 10]
    assert p["seconds"].to_list() == [25, 6, 30, 30]  # 8 -> 9 (75 s) is over the cap


def test_pace_pairs_ignore_row_order_and_skip_fourth_quarter():
    g = pace_game()
    assert plays.pace_pairs(g.reverse()).equals(plays.pace_pairs(g))
    q4 = g.with_columns(pl.lit(4).alias("qtr"))
    assert plays.pace_pairs(q4).height == 0


def test_pace_is_not_charted_for_a_team_game_with_a_coarse_clock():
    """Audit 2026-10-06 (G2.1): 1999-2000 play-by-play repeats the clock for many snaps (1999's
    league pace came out 0.007 s); a team-game whose offensive snaps carry distinct clock
    values for fewer than CLOCK_MIN_SHARE of them gives no pace pairs, the other team's game
    keeps its pairs, and a team-season without a charted game has no pace."""
    coarse = [{"game_seconds_remaining": t} for t in (3000, 3000, 3000, 2940, 2940, 2940)]
    fine = [{"game_seconds_remaining": t, "posteam": "BBB", "fixed_drive": 2}
            for t in (2900, 2870, 2840, 2810)]  # fmt: skip
    g = run(*coarse, *fine)
    assert plays.charted_games(g).rows() == [("2025_01_AAA_BBB", "BBB")]
    p = plays.pace_pairs(g)
    assert set(p["posteam"]) == {"BBB"} and p["seconds"].to_list() == [30, 30, 30]
    a = season.aggregate(g, p, season.COACH_KEYS).sort("team")
    assert a["neutral_sec_per_play_n"].to_list() == [0, 3]
    assert a["neutral_sec_per_play"].to_list() == [None, 30.0]
    assert a["plays"].to_list() == [6, 4]  # the other tendencies keep every snap
    # three distinct values in four snaps (0.75) is charted; two in four is not
    three = run(*[{"game_seconds_remaining": t} for t in (3000, 2970, 2970, 2940)])
    two = run(*[{"game_seconds_remaining": t} for t in (3000, 3000, 2940, 2940)])
    assert plays.CLOCK_MIN_SHARE == 0.75
    assert plays.charted_games(three).height == 1 and plays.charted_games(two).height == 0


def test_aggregate_rates_proe_and_pace():
    g = pace_game().with_columns(
        pl.Series("pass_oe", [10.0, -20.0, None, 30.0, None, 0.0, 5, 5, 5, 5, 5, 5])
    )
    pairs = plays.pace_pairs(g)
    a = (
        season.aggregate(g, pairs, season.COACH_KEYS)
        .filter(pl.col("team") == "AAA")
        .row(0, named=True)
    )
    # PROE: mean over the plays where pass_oe is defined (the NULL one is not a zero)
    assert a["proe_n"] == 4 and a["proe"] == pytest.approx((10 - 20 + 30 + 0) / 4)
    assert a["neutral_pass_rate_n"] == 5 and a["neutral_pass_rate"] == pytest.approx(4 / 5)
    assert a["neutral_sec_per_play"] == pytest.approx((25 + 6) / 2)
    assert a["plays"] == 5 and a["games"] == 1


class DuckRunner:
    """The warehouse tables the module reads, from polars frames (in-memory duckdb)."""

    def __init__(self, **tables: pl.DataFrame) -> None:
        self.con = duckdb.connect()
        for name, df in tables.items():
            self.con.register(f"{name}_src", df.to_arrow())
            self.con.execute(f"CREATE TABLE {name} AS SELECT * FROM {name}_src")

    def sql(self, query: str) -> pl.DataFrame:
        return self.con.execute(query).pl()


def league(seasons: dict[int, int]) -> tuple[pl.DataFrame, pl.DataFrame]:
    """fact_play and coach_game for AAA vs BBB, weeks 1..n per season. AAA's coach is fired
    after week 2 of every season (coach_a -> coach_c); BBB keeps coach_b."""
    rows, coaches = [], []
    for yr, weeks in seasons.items():
        for wk in range(1, weeks + 1):
            gid = f"{yr}_{wk:02d}_AAA_BBB"
            aaa = "coach_a" if wk <= 2 else "coach_c"
            coaches += [(gid, yr, wk, "REG", "AAA", aaa), (gid, yr, wk, "REG", "BBB", "coach_b")]
            base = {"game_id": gid, "season": yr, "week": wk}
            rows += [{**base, "game_seconds_remaining": 3000}, {**base, **RUN, "pass_oe": -5.0}]
            rows += [{**base, "posteam": "BBB", "fixed_drive": 2, **RUN}]
    cg = pl.DataFrame(
        coaches, schema=["game_id", "season", "week", "season_type", "team", "coach_id"],
        orient="row",
    )  # fmt: skip
    return frame(*rows).drop("coach_id"), cg


def test_mid_season_firing_splits_the_season_at_the_right_game():
    fp, cg = league({2025: 4})
    runner = DuckRunner(fact_play=fp, coach_game=cg)
    f = plays.flag_plays(runner.sql(plays.season_plays_sql(2025)))
    w = season.coach_season(f, plays.pace_pairs(f), current_season=2025)
    got = {(r["coach_id"], r["team"]): (r["games"], r["plays"]) for r in w.iter_rows(named=True)}
    assert got == {
        ("coach_a", "AAA"): (2, 4),
        ("coach_c", "AAA"): (2, 4),
        ("coach_b", "BBB"): (4, 4),
    }
    assert w["through_week"].to_list() == [2, 4, 4]  # sorted coach_a, coach_b, coach_c
    assert w["neutral_pass_rate_league"].unique().to_list() == [pytest.approx(4 / 12)]


def test_percentiles_within_season_with_minimum_samples():
    vals = [1.0, 2.0, 2.0, 4.0]
    w = pl.DataFrame({"season": [2025] * 4, "plays": [200, 200, 200, 100]}).with_columns(
        *[pl.Series(m, vals) for m in season.METRICS],
        *[pl.Series(f"{m}_n", [50, 50, 50, 50]) for m in season.METRICS],
    )
    w = w.with_columns(pl.Series("fourth_go_rate_n", [50, 50, 9, 50]))
    p = season.with_percentiles(w)
    third = 100 / 3
    assert p["proe_pctile"].to_list() == pytest.approx([third / 2, 2 * third, 2 * third, None])
    # the third row has too few fourth-down choices to be ranked: two rows are left
    assert p["fourth_go_rate_pctile"].to_list() == pytest.approx([25.0, 75.0, None, None])


def wide(rows: list[tuple[str, str, int, int, float]]) -> pl.DataFrame:
    """Completed coach-team-season rows (coach, team, season, plays, value of every metric),
    league value 0.5 for every metric."""
    w = pl.DataFrame(rows, schema=["coach_id", "team", "season", "plays", "v"], orient="row")
    return w.with_columns(
        pl.lit(False).alias("is_current"),
        *[pl.col("v").alias(m) for m in season.METRICS],
        *[pl.lit(0.5).alias(f"{m}_league") for m in season.METRICS],
    )


def test_persistence_pairs_compare_coach_and_team():
    w = wide(
        [
            ("x", "T1", 2010, 1000, 0.6), ("y", "T1", 2010, 300, 0.9),  # y: too few snaps
            ("x", "T1", 2011, 1000, 0.7), ("z", "T1", 2012, 1000, 0.4),
            ("x", "T2", 2013, 1000, 0.8),  # x again, another team, after a year off
        ]
    )  # fmt: skip
    main = persistence.main_rows(w)
    assert main["coach_id"].to_list() == ["x", "x", "z", "x"]
    assert main["rel_proe"].to_list() == pytest.approx([0.1, 0.2, -0.1, 0.3])
    p = persistence.pairs(main)
    got = p.select("comparison", "coach_id", "coach_id_b", "team_b", "season", "season_b")
    assert got.rows() == [
        ("new_coach_same_team", "x", "z", "T1", 2011, 2012),
        ("same_coach_new_team", "x", "x", "T2", 2011, 2013),
        ("same_coach_same_team", "x", "x", "T1", 2010, 2011),
    ]


def test_pearson_and_season_block_bootstrap():
    rng = np.random.default_rng(0)
    x = rng.normal(size=60)
    y = 0.5 * x + rng.normal(size=60)
    assert persistence.pearson(x, y) == pytest.approx(np.corrcoef(x, y)[0, 1])
    assert np.isnan(persistence.pearson(x[:2], y[:2]))
    blocks = np.repeat(np.arange(12), 5)
    lo, hi = persistence.block_bootstrap(x, y, blocks, n_boot=300, seed=1)
    assert lo < persistence.pearson(x, y) < hi
    assert (lo, hi) == persistence.block_bootstrap(x, y, blocks, n_boot=300, seed=1)
    assert persistence.block_bootstrap(x, 2 * x, blocks, n_boot=50) == pytest.approx((1.0, 1.0))


def test_persistence_table_counts_pairs_per_metric():
    rows = [("x", "T1", s, 1000, 0.5 + 0.01 * (s % 3)) for s in range(2010, 2016)]
    rows += [("y", "T2", s, 1000, 0.5 - 0.01 * (s % 3)) for s in range(2010, 2016)]
    t = persistence.persistence(persistence.pairs(persistence.main_rows(wide(rows))), n_boot=20)
    assert t.height == len(season.METRICS) * len(persistence.COMPARISONS)
    same = t.filter((pl.col("metric") == "proe") & (pl.col("comparison") == "same_coach_same_team"))
    assert same.row(0, named=True)["n_pairs"] == 10
    assert same.row(0, named=True)["first_season"] == 2010
    moved = t.filter(pl.col("comparison") == "same_coach_new_team")
    assert moved["n_pairs"].to_list() == [0] * len(season.METRICS)


def team_week(seasons: dict[int, int]) -> pl.DataFrame:
    rows = [
        (team, yr, f"{yr}_{wk:02d}_AAA_BBB", "REG", 10 if team == "AAA" else 6, 6, 80, 1, 1, 1)
        for yr, weeks in seasons.items()
        for wk in range(1, weeks + 1)
        for team in ("AAA", "BBB")
    ]
    return pl.DataFrame(rows, schema=[*fantasy.TEAM_WEEK_COLUMNS[:3], "season_type",
                                      *fantasy.TEAM_WEEK_COLUMNS[3:]], orient="row")  # fmt: skip


def test_team_receiving_ppr_and_link_rows():
    rec = fantasy.team_receiving(team_week({2024: 2, 2025: 1}))
    a = rec.filter((pl.col("team") == "AAA") & (pl.col("season") == 2024)).row(0, named=True)
    assert a["games"] == 2 and a["targets_per_game"] == 10
    assert a["recv_ppr_per_game"] == pytest.approx(6 + 8 + 6 + 2 - 2)
    tend = rec.select("team", "season").with_columns(
        *[pl.lit(0.6).alias(m) for m in season.METRICS],
        *[pl.lit(0.5).alias(f"{m}_league") for m in season.METRICS],
    )
    links = fantasy.link_rows(tend, rec)
    same = links.filter(pl.col("horizon") == "same_season")
    assert same.height == 4 and same["x_proe"].to_list() == pytest.approx([0.1] * 4)
    # production relative to the season's team mean: AAA 10 vs BBB 6 targets per game
    assert sorted(same["y_targets_per_game"].to_list()) == [-2, -2, 2, 2]
    nxt = links.filter(pl.col("horizon") == "next_season")
    assert nxt["season"].to_list() == [2024, 2024]  # 2024 tendency -> 2025 production


def test_build_current_season_rows_are_partial_and_excluded_from_history():
    fp, cg = league({2024: 4, 2025: 2})
    runner = DuckRunner(fact_play=fp, coach_game=cg, fact_team_week=team_week({2024: 4, 2025: 2}))
    out = build.build(runner, seasons=[2024, 2025], n_boot=20)
    assert set(build.FRAME_KEYS) <= set(out)
    for name, keys in build.FRAME_KEYS.items():
        assert out[name].select(keys).is_duplicated().sum() == 0, name
    s = out["coach_tendency_season"]
    cur = s.filter(pl.col("is_current"))
    assert cur["season"].unique().to_list() == [2025]
    assert cur["through_week"].unique().to_list() == [2]
    assert sorted(cur["coach_id"].unique().to_list()) == ["coach_a", "coach_b"]
    c = out["coach_tendency_career"]
    assert c["last_season"].max() == 2024
    npr = c.filter((pl.col("coach_id") == "coach_a") & (pl.col("metric") == "neutral_pass_rate"))
    assert npr.row(0, named=True)["sample"] == 4  # 2024 weeks 1-2 only; 2025 is current
    again = build.build(runner, seasons=[2024, 2025], n_boot=20)
    for name in build.FRAME_KEYS:
        assert out[name].equals(again[name]), name
