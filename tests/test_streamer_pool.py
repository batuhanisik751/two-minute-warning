"""The K and D/ST streamer's pool (S1b) and the fact_ranking_kdst table, on the synthetic world of
tests/streamer_world.py (2 teams x 1 slot: pool cutoff 3), plus the league-shape rules."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import duckdb
import polars as pl
import pytest
from typer.testing import CliRunner

from tests.streamer_world import NO_ROSTER_KICKER, PS_KICKER, PUNTER, RULES, build_world, gid
from twm.asof import AsOfView, weekly_as_of
from twm.backtest.leakage import assert_future_invariant
from twm.cli import app
from twm.config import League, league
from twm.modules.streamer import pool as sp


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory):
    return build_world(tmp_path_factory.mktemp("streamer"))


def _pool(db, week: int, season: int = 2025, **kw) -> pl.DataFrame:
    with AsOfView(db, weekly_as_of(db, season, week)) as v:
        return sp.candidate_pool(v, season, week, **{"rules": RULES, **kw})


def _rows(df: pl.DataFrame, position: str) -> dict[str, dict]:
    return {
        r["entity_id"]: r for r in df.filter(pl.col("position") == position).iter_rows(named=True)
    }


def _in_pool(df: pl.DataFrame, position: str) -> set[str]:
    return {e for e, r in _rows(df, position).items() if r["in_pool"]}


def test_fact_ranking_kdst_pages_teams_and_availability(world):
    con = duckdb.connect(str(world), read_only=True)
    con.execute("SET TimeZone='UTC'")
    try:
        rows = con.execute(
            "SELECT page_kind, page_pos, pos, fantasypros_id, gsis_id, team, nfl_team, pos_rank, "
            "scrape_date, available_at FROM fact_ranking_kdst ORDER BY page_pos, scrape_date, "
            "pos_rank, fantasypros_id"
        ).pl()
        n_skill = con.execute("SELECT count(*) FROM fact_ranking").fetchone()[0]
    finally:
        con.close()
    assert n_skill == 0  # K and DST pages never reach fact_ranking
    k = rows.filter(pl.col("page_pos") == "K")
    assert set(k.get_column("pos")) == {"K"}  # 'PK' on the short-form pages is written K
    assert "920309" not in k.get_column("fantasypros_id").to_list()  # the dynasty page
    pre = k.filter(
        pl.col("page_kind") == "preseason", pl.col("scrape_date").cast(pl.String) == "2025-08-28"
    )
    assert pre.get_column("pos_rank").to_list() == [1, 2, 3, 4, 5, 6, 7, 8]
    assert pre.get_column("gsis_id").to_list()[:7] == [gid(n) for n in range(301, 308)]
    fa = pre.filter(pl.col("team") == "FA").row(0, named=True)
    assert fa["nfl_team"] is None and fa["gsis_id"] is None
    dst = rows.filter(pl.col("page_pos") == "DST", pl.col("page_kind") == "preseason")
    assert dst.get_column("gsis_id").null_count() == dst.height
    assert dst.filter(pl.col("team") == "JAC").get_column("nfl_team").to_list() == ["JAX"]
    lag = (
        rows.get_column("available_at") - rows.get_column("scrape_date").cast(pl.Datetime("us"))
    ).unique()
    assert lag.to_list() == [
        timedelta(days=1)
    ]  # public the day after the scrape, like fact_ranking
    weekly = rows.filter(pl.col("page_kind") == "weekly")
    assert sorted(weekly.get_column("page_pos").to_list()) == ["DST", "K"]


def test_pool_week_1_ecr_ties_and_entities(world):
    df = _pool(world, 1)
    assert tuple(df.columns) == sp.POOL_COLUMNS
    assert set(df.get_column("method")) == {"ecr"}
    k = _rows(df, "K")
    # the punter is not a K; 311 has no roster row; 309 (practice squad, DEV) is in
    assert gid(PUNTER) not in k and gid(NO_ROSTER_KICKER) not in k
    assert _in_pool(df, "K") == {gid(304), gid(305), gid(308), gid(PS_KICKER)}
    assert k[gid(301)]["excluded_by"] == "preseason+ppg"
    assert k[gid(302)]["excluded_by"] == "preseason"  # ECR 2, PPG rank 8
    # 306 and 307 tie at 8 points (rank 3, ties share the better rank): both kept out
    assert (k[gid(306)]["ppg_pos_rank"], k[gid(307)]["ppg_pos_rank"]) == (3, 3)
    assert k[gid(306)]["excluded_by"] == k[gid(307)]["excluded_by"] == "ppg"
    ps = k[gid(PS_KICKER)]
    assert (ps["status"], ps["games_to_date"], ps["preseason_source"]) == ("DEV", 0, "unranked")
    assert ps["is_team_kicker"] is False and k[gid(303)]["is_team_kicker"] is True
    # the cheat sheet after kickoff (308 ranked 1st) is not the preseason list
    assert k[gid(308)]["preseason_pos_rank"] is None
    # DST: the entity is the team, never a gsis_id
    d = _rows(df, "DST")
    assert set(d) == {f"DST-{t}" for t in ("PHI", "DAL", "KC", "LAC", "SF", "SEA", "MIN", "CHI")}
    assert _in_pool(df, "DST") == {"DST-LAC", "DST-SF"}
    lac = d["DST-LAC"]
    assert (lac["gsis_id"], lac["name"], lac["team"], lac["is_team_kicker"]) == (
        None, "LAC D/ST", "LAC", None
    )  # fmt: skip
    assert (lac["preseason_pos_rank"], lac["ppg_to_date"], lac["ppg_pos_rank"]) == (4, 4.0, 5)
    assert d["DST-CHI"]["excluded_by"] == "ppg" and d["DST-PHI"]["excluded_by"] == "preseason"


def test_pool_statuses_split_week_and_ownership(world):
    w2, w3 = _pool(world, 2), _pool(world, 3)
    k2, k3 = _rows(w2, "K"), _rows(w3, "K")
    # 308 went on injured reserve in week 3: out of the universe from then on
    assert gid(308) in k2 and gid(308) not in k3
    # the Tuesday game of week 3 (MIN at LAC) ends after week 3's as-of: not visible yet
    assert (k3[gid(304)]["games_to_date"], k3[gid(304)]["ppg_to_date"]) == (2, 4.5)
    assert k3[gid(307)]["games_to_date"] == 2
    assert k3[gid(305)]["games_to_date"] == 2  # SF: week 1 and week 3 (bye in week 2)
    assert _in_pool(w2, "K") == {gid(304), gid(305), gid(306), gid(308), gid(PS_KICKER)}
    # rostership (diagnostic): the weekly page of 2025-09-12 is public at week 2's as-of only
    assert _rows(_pool(world, 1), "K")[gid(304)]["owned_avg"] is None
    assert (k2[gid(304)]["owned_avg"], str(k2[gid(304)]["owned_scrape_date"])) == (
        12.0,
        "2025-09-12",
    )
    assert _rows(w2, "DST")["DST-LAC"]["owned_avg"] == 30.0


def test_pool_prior_season_method(world):
    one_game = replace(RULES, prior_season_min_games=1)
    df = _pool(world, 1, method="prior_ppg", rules=one_game)
    assert set(df.get_column("method")) == {"prior_ppg"}
    k = _rows(df, "K")
    # 2024: 308, 307, 306 were the top 3 kickers by points per game
    assert [k[gid(n)]["preseason_pos_rank"] for n in (308, 307, 306, 301)] == [1, 2, 3, 4]
    assert _in_pool(df, "K") == {gid(302), gid(304), gid(305), gid(PS_KICKER)}
    assert k[gid(302)]["prior_season_ppg"] == 6.0 and k[gid(302)]["prior_season_games"] == 1
    # the default needs 4 games last season: nobody is ranked
    default = _pool(world, 1, method="prior_ppg")
    assert set(default.get_column("preseason_source")) == {"unranked"}
    # 2024 has no cheat sheet: 'auto' falls back, 'ecr' is refused
    assert set(_pool(world, 1, season=2024).get_column("method")) == {"prior_ppg"}
    with pytest.raises(ValueError, match="no preseason K cheat sheet of 2024"):
        _pool(world, 1, season=2024, method="ecr")


@pytest.mark.parametrize("week", [1, 2, 3])
def test_pool_ignores_everything_after_the_as_of(world, week):
    """Leakage harness: deleting or perturbing any row public after the as-of (later games,
    later rosters, later rankings) cannot change the pool."""
    assert_future_invariant(
        lambda v: sp.candidate_pool(v, 2025, week, rules=RULES),
        world,
        weekly_as_of(world, 2025, week),
        key=["position", "entity_id"],
    )


def _league(**changes) -> League:
    return League(**{**league().model_dump(), **changes})


def test_league_shape_sets_n_and_the_start_threshold():
    default = sp.StreamerRules.from_config()
    assert default.slots == {"K": 1, "DST": 1} and default.positions == ("K", "DST")
    assert default.cutoffs == {"K": 18, "DST": 18}  # 12 x 1 x 1.5
    assert default.start_thresholds == {"K": 12, "DST": 12}
    ten = sp.StreamerRules.from_config(_league(teams=10))
    assert ten.cutoffs == {"K": 15, "DST": 15} and ten.start_thresholds == {"K": 10, "DST": 10}
    lineup = {k: v for k, v in league().lineup.items() if k not in ("K", "DST")}
    espn = sp.StreamerRules.from_config(_league(lineup={**lineup, "K": 1, "D/ST": 2}))
    assert espn.slots == {"K": 1, "DST": 2} and espn.cutoffs["DST"] == 36
    no_k = sp.StreamerRules.from_config(_league(lineup={**lineup, "DST": 1}))
    assert no_k.positions == ("DST",)
    with pytest.raises(ValueError, match="at least one lineup slot"):
        no_k.check_positions(["K"])


def test_a_smaller_league_changes_the_pool(world):
    one_team = replace(RULES, teams=1)  # N = round(1 x 1 x 1.5) = 2 (half up)
    assert one_team.cutoffs == {"K": 2, "DST": 2}
    df = _pool(world, 1, rules=one_team)
    # 303 (ECR 3) and the 306/307 tie at PPG rank 3 are now outside the top 2
    assert _in_pool(df, "K") == {gid(n) for n in (304, 305, 306, 307, 308, PS_KICKER)}
    assert _in_pool(df, "DST") == {"DST-KC", "DST-LAC", "DST-SF", "DST-SEA"}
    only_dst = _pool(world, 1, positions=["DST"])
    assert set(only_dst.get_column("position")) == {"DST"}


def test_cli_pool(world):
    """The CLI uses config/league.yaml (12 teams: N = 18), so only the kicker without a game
    and a preseason rank is left in this 9-kicker world."""
    runner = CliRunner()
    res = runner.invoke(app, ["streamer", "pool", "2025", "1", "--db", str(world), "--pos", "K"])
    assert res.exit_code == 0, res.output
    assert (
        "2025 week 1, as-of 2025-09-09 14:00 UTC, K, method ecr: 1 in the pool of 9" in res.output
    )
    assert "Kicker 309" in res.output and "Kicker 301" not in res.output
    res = runner.invoke(app, ["streamer", "pool", "2025", "1", "--db", str(world), "--all"])
    assert res.exit_code == 0 and "Kicker 301" in res.output and "DST-LAC" in res.output
    res = runner.invoke(app, ["streamer", "pool", "2025", "1", "--db", str(world), "--pos", "QB"])
    assert res.exit_code == 2
