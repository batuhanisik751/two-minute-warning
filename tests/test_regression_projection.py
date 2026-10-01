"""Regression Watch step D3: the rest-of-season projection, the tags and the walk-forward
backtest.

Offline tests run on synthetic D1 frames: the projection formula, the universe (league-shape
agnostic, a 10-team league too) and the remaining-games rule, the tag deciles and thresholds,
and the no-look-ahead property (perturbing season S leaves S's shrinkage and choices unchanged;
perturbing S+1 leaves everything of S unchanged). Opt-in realdata tests check the live path
(AsOfView at the as-of) against the backtest and the committed report."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import yaml
from typer.testing import CliRunner

from twm.cli import app
from twm.config import CONFIG_DIR, ROOT, League
from twm.modules.regression_watch import backtest as bt
from twm.modules.regression_watch import backtest_report as br
from twm.modules.regression_watch import player_week as pw
from twm.modules.regression_watch import projection as pj
from twm.modules.regression_watch import stability as st
from twm.modules.regression_watch import stability_report as sr
from twm.modules.regression_watch import tags as tg

REPORT = ROOT / "reports" / "regression_watch" / "backtest.md"
SIZES = {"QB": 30, "RB": 70, "WR": 70, "TE": 45}  # more players than the 12-team universe


def _league(**kw: object) -> League:
    raw = yaml.safe_load((CONFIG_DIR / "league.yaml").read_text())
    return League(**{**raw, **kw})


def asof(season: int, week: int) -> datetime:
    """The synthetic official Tuesday as-of after (season, week)."""
    return datetime(season, 9, 8, 14, tzinfo=UTC) + timedelta(weeks=week - 1)


def world(seasons: tuple[int, ...] = (2009, 2010, 2011, 2012), *, weeks: int = 17,
          seed: int = 3) -> pl.DataFrame:  # fmt: skip
    """A D1-shaped frame: per player-season a role (xFP level) and a small true FPOE level;
    per game xFP = role + N(0, 2) and FPOE = level + N(0, 5); each game is missed with
    probability 0.1 and 2% of games have no opportunity (xFP empty). A row is public at its
    week's as-of."""
    rng = np.random.default_rng(seed)
    rows: list[tuple] = []
    for s in seasons:
        for pos, n in SIZES.items():
            for i in range(n):
                gid = f"00-{pos}{i:05d}"
                role = rng.uniform(3, 22)
                level = rng.normal(0, 1.0)
                for w in range(1, weeks + 1):
                    if rng.random() < 0.1:
                        continue
                    xfp = max(0.0, role + rng.normal(0, 2))
                    fpoe = level + rng.normal(0, 5)
                    has_opp = rng.random() >= 0.02
                    gxfp = 0.15 * xfp
                    rows.append((s, w, gid, pos, xfp + fpoe, xfp if has_opp else None,
                                 fpoe if has_opp else None, gxfp, asof(s, w)))  # fmt: skip
    df = pl.DataFrame(rows, orient="row", schema=["season", "week", "gsis_id", "position",
                      "fantasy_points", "xfp", "fpoe", "xfp_garbage", "available_at"])  # fmt: skip
    df = df.with_columns(
        pl.format("{}_{}_{}", "season", "week", "gsis_id").alias("game_id"),
        pl.lit("AAA").alias("team"),
        pl.lit("roster").alias("position_source"),
        (pl.col("fantasy_points") * 0.85).alias("points_ng"),
        (pl.col("xfp") - pl.col("xfp_garbage")).alias("xfp_ng"),
        pl.col("available_at").dt.replace_time_zone(None),
    ).with_columns((pl.col("points_ng") - pl.col("xfp_ng")).alias("fpoe_ng"))
    missing = [pl.lit(0.0).alias(c) for c in pw.FRAME_COLUMNS if c not in df.columns]
    return df.with_columns(missing).cast(pw.FRAME_SCHEMA).select(pw.FRAME_COLUMNS)  # type: ignore[arg-type]


def asofs(seasons: tuple[int, ...], weeks: tuple[int, ...] = bt.ALL_WEEKS) -> pl.DataFrame:
    rows = [(s, w, asof(s, w).replace(tzinfo=None)) for s in seasons for w in weeks]
    return pl.DataFrame(rows, orient="row", schema={"season": pl.Int32, "week": pl.Int32,
                                                    "as_of": pl.Datetime("us")})  # fmt: skip


def one_player(points: list[float], xfp: list[float | None], *, pos: str = "WR",
               season: int = 2015, gid: str = "00-0000001") -> pl.DataFrame:  # fmt: skip
    """One player's season: game i in week i + 1."""
    n = len(points)
    df = pl.DataFrame({
        "season": [season] * n, "week": list(range(1, n + 1)), "gsis_id": [gid] * n,
        "position": [pos] * n, "fantasy_points": points, "xfp": xfp,
        "points_ng": points, "xfp_ng": xfp,
        "available_at": [asof(season, w).replace(tzinfo=None) for w in range(1, n + 1)],
    })  # fmt: skip
    df = df.with_columns(
        (pl.col("fantasy_points") - pl.col("xfp")).alias("fpoe"),
        (pl.col("points_ng") - pl.col("xfp_ng")).alias("fpoe_ng"),
        pl.format("g{}", "week").alias("game_id"),
        pl.lit("AAA").alias("team"),
    )
    missing = [pl.lit(0.0).alias(c) for c in pw.FRAME_COLUMNS if c not in df.columns]
    return df.with_columns(missing).cast(pw.FRAME_SCHEMA).select(pw.FRAME_COLUMNS)  # type: ignore[arg-type]


def priors(var_signal: float, var_noise: float, mean: float = -1.0,
           scale: float = 1.2) -> pj.Priors:  # fmt: skip
    rows = [(p, m, 1, None, var_signal, var_noise, mean, 100, 2009, 2014)
            for p in pj.FANTASY_POSITIONS for m in st.SHRINKAGE_METRICS]  # fmt: skip
    table = pl.DataFrame(rows, schema=st.SHRINKAGE_SCHEMA, orient="row")
    return pj.Priors((2009, 2014), table, dict.fromkeys(pj.FANTASY_POSITIONS, scale))


def test_universe_sizes_follow_the_league_shape():
    assert pj.universe_sizes(_league()) == {"QB": 18, "RB": 54, "WR": 54, "TE": 36}
    assert pj.universe_sizes(_league(teams=10)) == {"QB": 15, "RB": 45, "WR": 45, "TE": 30}
    sf = _league(lineup={"QB": 1, "RB": 2, "WR": 3, "TE": 1, "SUPERFLEX": 1, "bench": 6},
                 slot_eligibility={"SUPERFLEX": ["QB", "RB", "WR", "TE"]})  # fmt: skip
    assert pj.universe_sizes(sf) == {"QB": 36, "RB": 54, "WR": 72, "TE": 36}


def test_player_state_numbers():
    f = one_player([10, 20, 5, 25], [8, 12, None, 20])
    s = pj.player_state(f, _league()).row(0, named=True)
    assert s["games"] == 4 and s["g_opp"] == 3 and s["position"] == "WR"
    assert s["ppg"] == pytest.approx(15.0) and s["xfp_pg"] == pytest.approx(10.0)
    assert s["fpoe_pg"] == pytest.approx(5.0)  # PPG = xFP/game + FPOE/game exactly
    assert s["last3_ppg"] == pytest.approx(50 / 3)
    w = [0.5 ** (k / 2) for k in (3, 2, 1, 0)]  # half-life 2 games, latest weighs 1
    expected = sum(a * b for a, b in zip(w, [8, 12, 0, 20], strict=True)) / sum(w)
    assert s["rw_xfp_hl2"] == pytest.approx(expected)
    assert s["rw_xfp_flat"] == pytest.approx(10.0)
    assert s["ppg_rank"] == 1 and s["in_universe"]


def test_projection_formula():
    f = one_player([10, 20, 5, 25], [8, 12, 4, 20])  # ppg 15, xfp/g 11, fpoe/g 4, g = 4
    state = pj.player_state(f, _league())
    r = 1.0 / (1.0 + 16.0 / 4)  # var_signal 1, var_noise 16, g 4 -> 0.2
    got = {v.name: pj.project(state, priors(1.0, 16.0), v).item(0, "ppg_ros") for v in pj.VARIANTS}
    assert got["zero_flat_all"] == pytest.approx(11 + r * 4)
    assert got["mean_flat_all"] == pytest.approx(11 + (-1 + r * (4 + 1)))
    hl4 = state.item(0, "rw_xfp_hl4")
    assert got["zero_hl4_all"] == pytest.approx(hl4 + r * 4)
    assert got["zero_flat_ng"] == pytest.approx(1.2 * 11 + r * 4)  # no-garbage xFP scaled back
    # r = 1 (no noise) and no recency: the projection is his PPG; r = 0: his xFP/game
    assert pj.project(state, priors(1.0, 0.0), pj.SPEC_VARIANT).item(0, "ppg_ros") == 15.0
    assert pj.project(state, priors(0.0, 16.0), pj.SPEC_VARIANT).item(0, "ppg_ros") == 11.0


def test_variant_grid():
    assert len(pj.VARIANTS) == len(pj.TARGETS) * len(pj.HALF_LIVES) * len(pj.GARBAGE) == 24
    assert pj.Variant("zero", None, "all") == pj.SPEC_VARIANT
    assert len(pj.VARIANT_BY_NAME) == 24 and pj.SPEC_VARIANT.metric == "fpoe"


def test_universe_needs_three_games_and_a_rank_inside_the_cutoff():
    f = world((2012,))
    week6 = pw.visible(f, asof(2012, 6))
    s = pj.player_state(week6, _league())
    assert s["games"].min() >= pj.MIN_GAMES
    counts = week6.group_by("gsis_id").len()
    assert s.height == counts.filter(pl.col("len") >= 3).height
    size = pj.universe_sizes(_league())
    for pos, n in size.items():
        u = s.filter((pl.col("position") == pos) & pl.col("in_universe"))
        assert ((u["ppg_rank"] <= n) | (u["xfp_rank"] <= n)).all()
        assert u.height >= n  # the PPG top n plus xFP top n
        out = s.filter((pl.col("position") == pos) & ~pl.col("in_universe"))
        assert ((out["ppg_rank"] > n) & (out["xfp_rank"] > n)).all()
    ten = pj.player_state(week6, _league(teams=10))
    assert ten["in_universe"].sum() < s["in_universe"].sum()
    for pos, n in pj.universe_sizes(_league(teams=10)).items():
        u = ten.filter((pl.col("position") == pos) & pl.col("in_universe"))
        assert ((u["ppg_rank"] <= n) | (u["xfp_rank"] <= n)).all() and u.height <= 2 * n


def test_player_state_refuses_several_seasons():
    with pytest.raises(ValueError, match="one season"):
        pj.player_state(world((2009, 2010)), _league())


def test_rest_of_season_counts_only_later_rows_and_needs_three_games():
    a = one_player([10] * 8, [9] * 8, gid="00-A")  # 8 games: 3 left after week 5
    b = one_player([20] * 6, [15] * 6, gid="00-B")  # 6 games: 1 left after week 5
    c = one_player([30, 30, 30], [20] * 3, gid="00-C").with_columns(  # joins in weeks 6-8
        pl.col("week") + 5, pl.col("available_at") + timedelta(weeks=5)
    )
    f = pl.concat([a, b, c])
    ros = bt.rest_of_season(f, asof(2015, 5)).sort("gsis_id")
    assert ros["ros_games"].to_list() == [3, 1, 3]
    assert ros["ros_ppg"].to_list() == [10.0, 20.0, 30.0]
    assert ros["ros_rank"].to_list() == [2, None, 1]  # B has < 3 games left: not ranked
    # a row public exactly at the as-of is season to date, never rest of season
    ros7 = bt.rest_of_season(f, asof(2015, 7))
    assert ros7.filter(pl.col("gsis_id") == "00-A")["ros_games"].item() == 1  # week 8 only
    assert "00-A" not in bt.rest_of_season(f, asof(2015, 8))["gsis_id"].to_list()


def test_week_rows_flag_players_with_few_games_left():
    f = world((2011, 2012))
    rows = bt.week_rows(f.filter(pl.col("season") == 2012), 14, asof(2012, 14),
                        pj.estimate_priors(f, [2011]), _league())  # fmt: skip
    assert rows.height and set(rows["evaluable"].to_list()) == {True, False}
    assert (rows.filter("evaluable")["ros_games"] >= 3).all()
    assert (rows.filter(~pl.col("evaluable"))["ros_games"] < 3).all()
    assert all(bt.proj_col(v) in rows.columns for v in pj.VARIANTS)


def test_decile_flags_and_tags():
    n = 20
    u = pl.DataFrame({
        "season": [2015] * n, "week": [6] * n, "position": ["WR"] * n,
        "gsis_id": [f"p{i:02d}" for i in range(n)], "fpoe_pg": [float(i) for i in range(n)],
        "ppg": [15.0] * n, "ppg_ros": [15.0 - i / 2 for i in range(n)],
        "ppg_rank": list(range(1, n + 1)),
    })  # fmt: skip
    d = tg.decile_flags(u).sort("gsis_id")
    assert d.filter("fpoe_top")["gsis_id"].to_list() == ["p18", "p19"]  # ceil(20 / 10) = 2
    assert d.filter("fpoe_bottom")["gsis_id"].to_list() == ["p00", "p01"]
    t = d.with_columns(tg.tag_exprs(_league(), 9.0, 0.0))
    assert t.filter("sell_high")["gsis_id"].to_list() == ["p18", "p19"]  # gaps 9 and 9.5
    t = d.with_columns(tg.tag_exprs(_league(), 9.5, 0.5))
    assert t.filter("sell_high")["gsis_id"].to_list() == ["p19"]  # a gap of exactly X counts
    assert t.filter("buy_low")["gsis_id"].to_list() == []  # projection not 0.5 above PPG
    # Legit: inside the WR starter threshold (12 teams x 2 = 24) and not top-decile FPOE
    assert t.filter("legit").height == n - 2
    t10 = d.with_columns(tg.tag_exprs(_league(teams=5), 9.0, 0.0))  # WR threshold 10
    assert t10.filter("legit")["gsis_id"].to_list() == [f"p{i:02d}" for i in range(10)]


def test_choose_x_best_precision_with_a_minimum_count():
    # 2 as-ofs; top-decile rows with gaps 1..10; hits only for gaps >= 6
    rows = pl.DataFrame({
        "season": [2014] * 10, "week": [4] * 5 + [6] * 5, "fpoe_top": [True] * 10,
        "fpoe_bottom": [False] * 10, "ppg": [20.0] * 10,
        "ppg_ros": [20.0 - g for g in range(1, 11)],
        "ros_ppg": [25.0] * 5 + [10.0] * 5,
    })  # fmt: skip
    th = tg.choose_x(rows, "sell_high", grid=[0, 2, 4, 6, 8], min_per_asof=2)
    assert th.x == 6 and th.precision == 1.0 and th.n_tags == 5 and not th.fallback
    th = tg.choose_x(rows, "sell_high", grid=[0, 2, 4, 6, 8], min_per_asof=3)
    assert th.x == 4 and th.n_tags == 7  # 6 would tag 5 < 3 x 2 as-ofs
    th = tg.choose_x(rows, "sell_high", grid=[0, 2], min_per_asof=50)
    assert th.fallback and th.x == 0
    assert tg.choose_x(rows, "buy_low").fallback  # no bottom-decile rows
    with pytest.raises(ValueError):
        tg.choose_x(rows, "legit")


# --------------------------------------------------------------------------------------
# Walk-forward: choices on earlier seasons only
# --------------------------------------------------------------------------------------

SEASONS = (2009, 2010, 2011, 2012)
WEEKS = (4, 6, 8, 10, 12)


def _run(frame: pl.DataFrame) -> bt.Backtest:
    return bt.run_backtest(frame, asofs(SEASONS, WEEKS), _league(), last_season=2012, n_boot=20)


def _perturb(frame: pl.DataFrame, season: int) -> pl.DataFrame:
    """Scramble one season's outcomes (points up to +50%, deterministic)."""
    hit = pl.col("season") == season
    bumped = pl.col("fantasy_points") * (1 + (pl.col("week") % 3) / 4)
    return frame.with_columns(
        pl.when(hit).then(bumped).otherwise(pl.col("fantasy_points")).alias("fantasy_points"),
        pl.when(hit).then(bumped - pl.col("xfp")).otherwise(pl.col("fpoe")).alias("fpoe"),
    )


@pytest.fixture(scope="module")
def base() -> tuple[pl.DataFrame, bt.Backtest]:
    f = world(SEASONS)
    return f, _run(f)


def _priors_of(b: bt.Backtest, season: int) -> pj.Priors:
    return next(p.priors for p in b.priors if p.season == season)


def test_backtest_shape(base):
    _, b = base
    assert b.test_seasons == (2011, 2012) and set(b.choices) == {2011, 2012}
    assert b.choices[2011].validation_seasons == (2010,)
    assert b.choices[2012].validation_seasons == (2010, 2011)
    assert _priors_of(b, 2011).seasons == (2009, 2010)
    assert set(b.graded["season"].unique()) == {2011, 2012}
    assert b.rows["season"].min() == bt.FIRST_VALIDATION_SEASON
    assert (bt.validation_rows(b.rows, 2012)["season"] < 2012).all()


def test_perturbing_season_s_leaves_its_shrinkage_and_choices_unchanged(base):
    f, b = base
    p = _run(_perturb(f, 2012))
    assert _priors_of(p, 2012).table.equals(_priors_of(b, 2012).table)
    assert _priors_of(p, 2012).ng_scale == _priors_of(b, 2012).ng_scale
    assert p.choices[2012] == b.choices[2012]  # variant, X Sell-high, X Buy-low
    assert not p.graded.filter(pl.col("season") == 2012).equals(
        b.graded.filter(pl.col("season") == 2012)
    )  # the perturbation did reach season 2012's outcomes


def test_perturbing_season_s_plus_1_leaves_season_s_unchanged(base):
    f, b = base
    p = _run(_perturb(f, 2012))
    assert p.choices[2011] == b.choices[2011]
    assert p.rows.filter(pl.col("season") <= 2011).equals(b.rows.filter(pl.col("season") <= 2011))
    assert p.graded.filter(pl.col("season") == 2011).equals(
        b.graded.filter(pl.col("season") == 2011)
    )


def test_perturbing_an_earlier_season_does_move_the_estimates(base):
    f, b = base
    p = _run(_perturb(f, 2010))
    assert not _priors_of(p, 2012).table.equals(_priors_of(b, 2012).table)


def test_choose_takes_the_lowest_validation_mae(base):
    _, b = base
    target = pj.VARIANT_BY_NAME["mean_hl4_ng"]
    rigged = b.rows.with_columns(pl.col("ros_ppg").alias(bt.proj_col(target)))  # perfect
    ch = bt.choose(rigged, 2012)
    assert ch.variant == target and ch.val_mae == 0.0
    assert bt.choose(rigged, 2010).variant == pj.SPEC_VARIANT  # no validation season


# --------------------------------------------------------------------------------------
# Report, CLI, registry
# --------------------------------------------------------------------------------------


def test_report_is_deterministic_and_states_acceptance(base, tmp_path):
    _, b = base
    rep = br.build_backtest_report(b, _league(), "2026-01-01 00:00:00")
    assert rep.accepted and "P1 acceptance (MET)" in rep.markdown
    for heading in ("## Headline: mean absolute error", "## Rank correlation", "## Tags",
                    "## By test season", "## Shrinkage used", "## Limits"):  # fmt: skip
        assert heading in rep.markdown
    assert "`" + b.choices[2012].variant.name + "`" in rep.markdown
    csv_path = br.write_backtest_report(rep, tmp_path / "rw" / "backtest.md")
    assert csv_path.read_text().splitlines()[0] == ",".join(br.CSV_COLUMNS)
    tables = {r["table"] for r in rep.csv_rows}
    assert tables == {"value", "difference", "tag", "choice", "threshold", "left_out"}
    again = br.build_backtest_report(b, _league(), "2026-01-01 00:00:00")
    assert again.markdown == rep.markdown and again.csv_rows == rep.csv_rows


def test_acceptance_verdict_reads_the_pooled_difference():
    rows = [
        {"weeks": "headline", "position": "all", "metric": "mae", "method": "model",
         "value": 3.4, "lo": 3.3, "hi": 3.5},
        {"weeks": "headline", "position": "all", "metric": "mae", "method": "baseline_ppg",
         "value": 3.3, "lo": 3.2, "hi": 3.4},
        {"weeks": "headline", "position": "all", "metric": "mae",
         "method": "model-baseline_ppg", "value": 0.1, "lo": -0.05, "hi": 0.2},
    ]  # fmt: skip
    ok, text = br.accepted(rows)
    assert not ok and "NOT MET" in text and "does NOT exclude 0" in text
    assert br.accepted([]) == (False, "Acceptance cannot be judged: no graded rows.")


def test_cli_backtest(base, tmp_path, monkeypatch):
    f, _ = base
    monkeypatch.setattr(bt, "load_inputs", lambda db, last, xfp=None: (f, asofs(SEASONS, WEEKS)))
    monkeypatch.setattr(sr, "_built_at", lambda db: "2026-01-01 00:00:00")
    db = tmp_path / "warehouse.duckdb"
    db.write_bytes(b"")
    out = tmp_path / "rw" / "backtest.md"
    runner = CliRunner()
    res = runner.invoke(app, ["regression", "backtest", "--db", str(db), "--end", "2012",
                              "--out", str(out), "--boot", "20",
                              "--xfp", "ffopportunity"])  # fmt: skip
    assert res.exit_code == 0, res.output
    assert "P1 acceptance" in res.output and "season 2012" in res.output  # progress too
    assert out.exists() and out.with_suffix(".csv").exists()
    res = runner.invoke(app, ["regression", "backtest", "--db", str(db), "--end", "2010"])
    assert res.exit_code != 0
    res = runner.invoke(app, ["regression", "backtest", "--db", str(tmp_path / "nope.duckdb")])
    assert res.exit_code == 1 and "warehouse not found" in res.output


def test_cli_project(base, tmp_path, monkeypatch):
    f, b = base
    ch = b.choices[2012]
    state = pj.player_state(pw.visible(f.filter(pl.col("season") == 2012), asof(2012, 6)),
                            _league())  # fmt: skip
    uni = pj.project(state.filter("in_universe"), _priors_of(b, 2012), ch.variant)
    uni = tg.decile_flags(uni.with_columns(pl.lit(6, dtype=pl.Int32).alias("week")))
    table = uni.with_columns(*tg.tag_exprs(_league(), ch.sell.x, ch.buy.x),
                             pl.col("gsis_id").alias("name"))  # fmt: skip
    wp = bt.WeekProjection(2012, 6, asof(2012, 6), ch, table)
    monkeypatch.setattr(bt, "project_week", lambda db, season, week, league, xfp=None: wp)
    db = tmp_path / "warehouse.duckdb"
    db.write_bytes(b"")
    out = tmp_path / "p.csv"
    args = ["regression", "project", "2012", "6", "--db", str(db), "--position", "wr",
            "--xfp", "ffopportunity"]  # fmt: skip
    res = CliRunner().invoke(app, [*args, "--top", "3", "--csv", str(out)])
    assert res.exit_code == 0, res.output
    assert ch.variant.name in res.output and "\nWR  name" in res.output
    assert "\nQB  name" not in res.output
    assert pl.read_csv(out).height == table.height
    res = CliRunner().invoke(app, ["regression", "project", "2012", "6", "--db", str(db),
                                   "--position", "K", "--xfp", "ffopportunity"])  # fmt: skip
    assert res.exit_code != 0


def test_d3_entries_are_registered():
    from twm import registry as rg

    names = {"regression_universe", "recency_weighted_xfp", "ppg_ros", "sell_high", "buy_low",
             "legit", "tag_threshold_x", "rest_of_season_ppg"}  # fmt: skip
    assert names <= set(rg.REGISTRY)
    assert all(rg.get(n).step == "D3" for n in names)
    assert "QB 18, RB 54, WR 54, TE 36" in rg.get("regression_universe").formula


# --------------------------------------------------------------------------------------
# Real data (opt-in: `uv run pytest -m realdata`): the project's warehouse
# --------------------------------------------------------------------------------------


def _real_db() -> Path:
    from twm.config import settings

    db = settings().path("warehouse")
    if not db.exists():
        pytest.skip("run `uv run twm build` first")
    return db


@pytest.mark.realdata
def test_real_project_week_matches_the_backtest():
    """The live path (an AsOfView AT the as-of) gives the backtest's rows (history frame cut
    with visible()), projections and choices for a past week."""
    from twm.config import league

    db = _real_db()
    wp = bt.project_week(db, 2023, 6, league())
    seasons = range(bt.FIRST_VALIDATION_SEASON, 2024)
    history = pw.player_games_history(db, range(pj.FIRST_DATA_SEASON, 2024))
    rows, _ = bt.build_rows(history, bt.asof_table(db, seasons, bt.HEADLINE_WEEKS), league(),
                            seasons)  # fmt: skip
    assert bt.choose(rows, 2023) == wp.choice
    week = rows.filter((pl.col("season") == 2023) & (pl.col("week") == 6))
    j = wp.table.select("gsis_id", "ppg_ros").join(
        week.select("gsis_id", pl.col(bt.proj_col(wp.choice.variant)).alias("bt")),
        on="gsis_id", how="full",
    )  # fmt: skip
    assert j.height == wp.table.height == week.height > 150
    assert (j["ppg_ros"] == j["bt"]).all()


@pytest.mark.realdata
def test_real_backtest_matches_the_committed_report():
    """The committed CSV was made from this warehouse (fails after a rebuild changes it: rerun
    `uv run twm regression backtest`); the point values need no resampling to compare."""
    import csv

    from twm.config import league
    from twm.modules.regression_watch import xfp_source as xs

    db = _real_db()  # the committed report uses the pinned source (own xFP since H6-b2)
    frame, a = bt.load_inputs(db, 2025, xfp=xs.history(db, 2025, xs.pinned_source()))
    b = bt.run_backtest(frame, a, league(), last_season=2025, n_boot=10)
    with REPORT.with_suffix(".csv").open(encoding="utf-8") as fh:
        committed = {
            (r["weeks"], r["position"], r["metric"], r["method"]): float(r["value"])
            for r in csv.DictReader(fh)
            if r["table"] in ("value", "difference")
        }
    assert len(committed) == len(b.metrics)
    for r in b.metrics:
        key = (r["weeks"], r["position"], r["metric"], r["method"])
        assert committed[key] == pytest.approx(r["value"], abs=1e-5), key
    assert br.accepted(b.metrics)[0]
