"""Cliff & Breakout Board (I1b): population and label rules (the 6/8-game edges, y_missed), the
end-of-season snapshot (a later season never leaks in), the speed score, the ECR baseline, the
walk-forward (never trains on the test season) and determinism, all on synthetic data."""

from __future__ import annotations

from datetime import UTC, date, datetime

import duckdb
import numpy as np
import polars as pl
import pytest

from tests import board_world as w
from twm.modules.board import backtest as bt
from twm.modules.board import ecr as be
from twm.modules.board import features as bf
from twm.modules.board import populations as pop
from twm.modules.board import seasons as bs
from twm.modules.board import sources as src


def _rows(specs: list[dict]) -> pl.DataFrame:
    base = {"season": 2020, "position": "WR", "ppg": 10.0, "pos_rank": 10, "prior_seasons": 4}
    return pl.DataFrame([{"gsis_id": f"p{i}", **base, **s} for i, s in enumerate(specs)],
                        schema_overrides={"season": pl.Int32, "pos_rank": pl.Int32,
                                          "prior_seasons": pl.Int32})  # fmt: skip


def _next(specs: dict[str, dict]) -> pl.DataFrame:
    rows = [{"gsis_id": g, "season": 2021, "position": "WR", "games": 10, "ppg": 10.0,
             "pos_rank": 50, **s} for g, s in specs.items()]  # fmt: skip
    df = pl.DataFrame(rows, schema_overrides={"season": pl.Int32, "pos_rank": pl.Int32})
    return pop.outcomes(df.with_columns(pl.col("games").cast(pl.Int64)))


def test_cliff_population_and_labels_edges():
    rows = _rows([
        {"prior_seasons": 3, "pos_rank": 36},  # p0 in: the 3-season and top-36 edges
        {"prior_seasons": 2},  # p1 out: 2 prior seasons
        {"pos_rank": 37},  # p2 out: rank 37
        {"pos_rank": None},  # p3 out: unranked (under 8 games)
        {},  # p4 exactly 30% drop, 6 games: a cliff
        {},  # p5 29.9% drop: not a cliff
        {},  # p6 5 games next season: y_missed, y_cliff NULL
        {},  # p7 no stat line next season (0 games): y_missed
    ])  # fmt: skip
    nxt = _next({"p0": {"ppg": 6.0}, "p4": {"games": 6, "ppg": 7.0},
                 "p5": {"ppg": 7.01}, "p6": {"games": 5, "ppg": 2.0}})  # fmt: skip
    df = pop.add_labels(rows, nxt, last_complete=2021).sort("gsis_id")
    inside = df.get_column("in_cliff").to_list()
    assert inside == [True, False, False, False, True, True, True, True]
    got = {r["gsis_id"]: (r["y_cliff"], r["y_missed"], r["y_cliff_or_missed"])
           for r in df.iter_rows(named=True)}  # fmt: skip
    assert got["p0"] == (True, False, True)
    assert got["p4"] == (True, False, True)
    assert got["p5"] == (False, False, False)
    assert got["p6"] == (None, True, True) and got["p7"] == (None, True, True)
    assert df.filter(pl.col("gsis_id") == "p7").get_column("games_next").item() == 0
    # a snapshot whose next season is not complete has no labels
    late = pop.add_labels(rows, nxt, last_complete=2020)
    assert all(late.get_column(c).null_count() == late.height for c in pop.LABELS)


def test_breakout_population_and_label_edges():
    rows = _rows([
        {"prior_seasons": 0, "pos_rank": 37},  # p0 WR in (rank 37 > 36)
        {"prior_seasons": 1, "pos_rank": 36},  # p1 WR out: top-36 already
        {"prior_seasons": 2, "pos_rank": None},  # p2 out: entering his 4th season
        {"prior_seasons": 1, "pos_rank": None},  # p3 in: unranked (under 8 games)
        {"position": "TE", "prior_seasons": 0, "pos_rank": 12},  # p4 out: top-12 TE
        {"position": "TE", "prior_seasons": 0, "pos_rank": 13},  # p5 in
        {"position": "RB", "prior_seasons": 1, "pos_rank": 24},  # p6 out: top-24 RB
        {"position": "RB", "prior_seasons": 1, "pos_rank": 25},  # p7 in
        {"position": "QB", "prior_seasons": 0, "pos_rank": 40},  # p8 out: a QB
    ])  # fmt: skip
    nxt = _next({
        "p0": {"pos_rank": 24},  # WR top-24 with 8+ games: a breakout
        "p3": {"pos_rank": None, "games": 7},  # under 8 games: never a breakout
        "p5": {"position": "TE", "pos_rank": 13},  # TE 13th: no
        "p7": {"position": "WR", "pos_rank": 25},  # now a WR: the WR threshold (24)
    })  # fmt: skip
    df = pop.add_labels(rows, nxt, last_complete=2021).sort("gsis_id")
    inside = dict(zip(df["gsis_id"], df["in_breakout"], strict=True))
    assert [g for g, x in inside.items() if x] == ["p0", "p3", "p5", "p7"]
    y = dict(zip(df["gsis_id"], df["y_breakout"], strict=True))
    assert (y["p0"], y["p3"], y["p5"], y["p7"]) == (True, False, False, False)
    nxt2 = _next({"p5": {"position": "TE", "pos_rank": 12}})
    assert pop.add_labels(rows, nxt2, 2021).filter(pl.col("gsis_id") == "p5")["y_breakout"].item()


def test_ranks_need_eight_games():
    df = pl.DataFrame({"gsis_id": ["a", "b", "c"], "season": [2020] * 3,
                       "position": ["WR"] * 3, "games": [8, 7, 9],
                       "points": [80.0, 140.0, 81.0]})  # fmt: skip
    r = bs.add_ranks(df).sort("gsis_id")
    assert r["pos_rank"].to_list() == [1, None, 2]  # b (20 PPG, 7 games) is not ranked
    assert r["ppg"].to_list() == [10.0, 20.0, 9.0]


def test_speed_score_is_barnwells_formula():
    df = pl.DataFrame({"w": [200.0, 224.0, None], "f": [4.0, 4.46, 4.5]})
    got = df.select(src.speed_score(pl.col("w"), pl.col("f"))).to_series().to_list()
    assert got[0] == pytest.approx(156.25)  # 200 x 200 / 4^4
    assert got[1] == pytest.approx(224 * 200 / 4.46**4) and got[2] is None


def _deps(rows: list[tuple]) -> bf.Departures:
    return bf.Departures(pl.DataFrame(
        rows, schema={"team": pl.String, "season": pl.Int32, "departure_type": pl.String,
                      "announced": pl.Date}, orient="row"))  # fmt: skip


def _snapshot(season: int, frames: dict, xfp: pl.DataFrame, deps: bf.Departures):
    view = w.FakeView(frames, w.SNAPSHOT[season])
    return bf.snapshot_features(view, season, xfp_games=xfp, departures=deps)


def test_a_later_season_never_leaks_into_the_snapshot():
    deps = _deps([("KC", 2019, "fired_after_season", date(2020, 1, 6)),
                  ("BUF", 2020, "retired", date(2021, 1, 20)),
                  ("BUF", 2021, "fired_after_season", date(2022, 1, 10))])  # fmt: skip
    full = _snapshot(2020, w.tables(), w.xfp_games(), deps)
    assert full.height == 4 and full["season"].unique().to_list() == [2020]
    # 2021 deleted, or every 2021 number scrambled (stats, xFP), and the 2021 departure gone
    gone = _snapshot(2020, w.tables(drop_after=2020), w.xfp_games(), deps)
    scrambled = _snapshot(2020, w.tables(scramble_after=2020), w.xfp_games(scramble_after=2020),
                          _deps([("KC", 2019, "fired_after_season", date(2020, 1, 6)),
                                 ("BUF", 2020, "retired", date(2021, 1, 20))]))  # fmt: skip
    assert full.equals(gone) and full.equals(scrambled)
    # the features do use season 2020 and the seasons before it
    assert full.filter(pl.col("gsis_id") == "00-0000002")["prior_seasons"].item() == 1
    assert full["ppg_change"].null_count() == 0 and full["xfp_per_game_s"].null_count() == 0
    assert full.filter(pl.col("team") == "BUF")["hc_departure"].all()  # announced before
    assert not full.filter(pl.col("team") == "KC")["hc_departure"].any()  # a 2019 departure


def test_departure_dates_and_the_blank_rule():
    snap = datetime(2021, 2, 9, 14, tzinfo=UTC)
    deps = _deps([("A", 2020, "fired_after_season", date(2021, 2, 8)),  # the day before: known
                  ("B", 2020, "resigned", date(2021, 2, 9)),  # the snapshot's day: not yet
                  ("C", 2020, "interim_not_retained", None),  # blank, early type: known
                  ("D", 2020, "retired", None),  # blank, other type: not known
                  ("E", 2019, "fired_after_season", date(2020, 1, 1))])  # fmt: skip
    assert bf.hc_departure_teams(deps, 2020, snap) == {"A", "C"}
    assert deps.n_blank_date == 2 and deps.n_blank_unknown == 1


def test_snapshot_needs_a_played_super_bowl():
    con = duckdb.connect()
    con.execute("CREATE TABLE fact_game AS SELECT 2020 AS season, 'SB' AS game_type, "
                "3 AS result, TIMESTAMP '2021-02-08 06:00:00' AS available_at")  # fmt: skip
    con.execute("CREATE TABLE fact_player_week AS SELECT * FROM (VALUES (2020, TIMESTAMP "
                "'2021-02-08 09:30:00'), (2021, TIMESTAMP '2021-09-14 09:30:00')) "
                "t(season, available_at)")  # fmt: skip
    assert bs.snapshot_as_of(con, 2020) == datetime(2021, 2, 8, 9, 30, tzinfo=UTC)
    with pytest.raises(bs.SeasonNotOverError):
        bs.snapshot_as_of(con, 2021)


def test_age_curve_uses_only_pairs_known_at_the_snapshot():
    rng = np.random.default_rng(7)
    rows = []
    for i in range(80):
        born = date(1990, 1, 1) + (date(1991, 1, 1) - date(1990, 1, 1)) * int(rng.integers(0, 8))
        for s in (2018, 2019, 2020):
            rows.append({"gsis_id": f"g{i}", "season": s, "position": "RB", "games": 12,
                         "ppg": 10.0 * 0.8 ** (s - 2018), "pos_rank": 10})  # fmt: skip
        rows[-1]["ppg"] = 99.0  # a 2020 jump: only a 2020 curve may see it
        born_rows = {"gsis_id": f"g{i}", "birth_date": born}
        rows[-1]["_born"] = born_rows
    ps = pl.DataFrame([{k: v for k, v in r.items() if k != "_born"} for r in rows],
                      schema_overrides={"season": pl.Int32, "pos_rank": pl.Int32})  # fmt: skip
    born = pl.DataFrame([r["_born"] for r in rows if "_born" in r])
    c2019 = bf.age_curves(ps, born, 2019)["RB"]
    assert c2019.n_pairs == 80 and c2019.at(np.array([27.0]))[0] == pytest.approx(0.8)
    assert bf.age_curves(ps.filter(pl.col("season") <= 2019), born, 2019) == {"RB": c2019}
    assert bf.age_curves(ps, born, 2020)["RB"].n_pairs == 160


def test_ecr_ranks_and_scores():
    ranks = pl.DataFrame({
        "gsis_id": ["a", "b", "b", "c"], "page_pos": ["WR", "WR", "RB", "TE"],
        "pos": ["WR", "RB", "RB", "TE"], "pos_rank": [3, 40, 12, 5], "page_max": [50, 50, 30, 20],
    })  # fmt: skip
    rows = pl.DataFrame({"gsis_id": ["a", "b", "c", "d"], "position": ["WR", "RB", "WR", "WR"],
                         "pos_rank_s": [10, 20, 30, 5]})  # fmt: skip
    got = be.attach_ecr(rows, ranks).sort("gsis_id")
    # a: his own page; b: his RB page; c: not on the WR page -> his TE page; d: unranked
    assert got["ecr_rank"].to_list() == [3, 12, 5, None]
    assert got["ecr_fill"].to_list() == [3, 12, 5, 51]
    assert be.ecr_score(got, "fall").to_list() == [-7.0, -8.0, -25.0, 46.0]
    assert be.ecr_score(got, "rise").to_list() == [-3.0, -12.0, -5.0, -51.0]


def _synthetic_dataset(seed: int = 3) -> pl.DataFrame:
    """Cliff rows 2002-2009 with every Cliff feature; y_cliff depends on ppg_change."""
    from twm.modules.board import models as bm

    rng = np.random.default_rng(seed)
    n = 8 * 60
    df = pl.DataFrame({f: rng.normal(size=n) for f in bm.CLIFF_FEATURES}).with_columns(
        pl.Series("season", np.repeat(np.arange(2002, 2010), 60), dtype=pl.Int32),
        pl.Series("gsis_id", [f"00-{i:07d}" for i in range(n)]),
        pl.lit("WR").alias("position"), pl.lit("KC").alias("team"), pl.lit(10.0).alias("ppg"),
        pl.lit(5).alias("pos_rank"), pl.lit(10).alias("games_next"),
        pl.lit(7.0).alias("ppg_next"), pl.lit(True).alias("in_cliff"),
    )  # fmt: skip
    noise = rng.normal(size=n)
    return df.with_columns(pl.Series("y_cliff", (df["ppg_change"].to_numpy() + noise) > 1.0))


def test_walk_forward_never_trains_on_the_test_season_and_is_deterministic():
    ds = _synthetic_dataset()
    v = bt.variants()["cliff_main"]
    tests = (2007, 2008, 2009)
    a = bt.run_variant(ds, v, tests)
    b = bt.run_variant(ds, v, tests)
    assert a.scored.equals(b.scored)  # same data, same predictions, every model
    assert a.scored.height == 180 and set(a.scored["season"].unique()) == set(tests)
    for name, res in a.results.items():
        for fr in res.folds:
            s = fr.fold.test_season
            assert max(fr.fold.train_seasons) < s, name
            assert fr.n_train == ds.filter(pl.col("season") < s).height, name
    # the model learns the planted signal; the PPG-rank baseline cannot
    from twm.backtest.metrics import pr_auc

    y = a.scored["y"].to_numpy()
    assert pr_auc(y, a.scored["p_logit"].to_numpy()) > pr_auc(y, a.scored["p_base_ppg_rank"])


def test_evaluation_and_reports_on_a_synthetic_run(tmp_path, monkeypatch):
    from twm.modules.board import evaluation as ev
    from twm.modules.board import report as br

    monkeypatch.setattr(ev, "ECR_FIRST_SNAPSHOT", 2008)
    ds = _synthetic_dataset()
    run = bt.run_variant(ds, bt.variants()["cliff_main"], (2007, 2008, 2009))
    ranks = {}
    for s in (2008, 2009):
        ids = ds.filter(pl.col("season") == s).sort("ppg_change")["gsis_id"].to_list()[:40]
        ranks[s] = pl.DataFrame({"gsis_id": ids, "page_pos": "WR", "pos": "WR",
                                 "pos_rank": list(range(1, 41)), "page_max": 40})  # fmt: skip
    rep = br.evaluate(run, ranks)
    m = rep.metrics
    assert set(m["slice"]) == {"all", "ecr_era"} and "ecr" in set(m["model"])
    pr = m.filter(pl.col("slice") == "all", pl.col("model") == "logit",
                  pl.col("metric") == "pr_auc").row(0, named=True)  # fmt: skip
    assert pr["lo"] <= pr["value"] <= pr["hi"]
    assert rep.seasons["positives"].sum() == int(run.scored["y"].sum())
    assert set(rep.agree_summary["group"]) <= {"both", "model_only", "ecr_only"}
    again = br.VariantReport.from_cache(rep.to_cache())  # the --resume cache round trip
    assert again.metrics.equals(rep.metrics) and again.run.variant.name == "cliff_main"
    names = pl.DataFrame({"gsis_id": ds["gsis_id"], "display_name": ds["gsis_id"]})
    paths = br.write_reports({"cliff_main": rep}, tmp_path, names)
    text = (tmp_path / "cliff.md").read_text()
    assert paths[0].name == "cliff.md" and "PR-AUC" in text and "precision@10" in text
    assert (tmp_path / "cliff_seasons.csv").exists() and not (tmp_path / "breakout.md").exists()


@pytest.mark.realdata
def test_snapshot_features_pass_the_leakage_harness_on_the_real_warehouse():
    """Deleting or scrambling everything not public at the 2016 snapshot (the 2017 draft class,
    2017 games, today's positions ...) changes no feature (the real warehouse, read only)."""
    from twm.backtest.leakage import assert_future_invariant
    from twm.config import settings

    db = settings().path("warehouse")
    if not db.exists():
        pytest.skip("no warehouse (run `twm build`)")
    deps = bf.read_departures(settings().path("manual") / "coach_departures.csv")
    snap = bs.snapshot_as_of(db, 2016)
    out = assert_future_invariant(
        lambda v: bf.snapshot_features(v, 2016, xfp_games=None, departures=deps), db, snap,
        key=["gsis_id"],
    )  # fmt: skip
    assert out.height > 500 and out["snapshot"].unique().to_list() == [snap.replace(tzinfo=None)]


@pytest.mark.realdata
def test_real_notebook_executes():
    import nbformat
    from nbclient import NotebookClient

    from twm.config import ROOT

    if (
        not (ROOT / "reports/board/cliff.csv").exists()
        or not (ROOT / "data/board/dataset.parquet").exists()
    ):
        pytest.skip("run `uv run twm board backtest` first")
    nb = nbformat.read(ROOT / "notebooks" / "05_cliff_breakout.ipynb", as_version=4)
    NotebookClient(nb, timeout=300, kernel_name="python3",
                   resources={"metadata": {"path": str(ROOT / "notebooks")}}).execute()  # fmt: skip
    errors = [o for c in nb.cells if c.cell_type == "code" for o in c.get("outputs", [])
              if o.output_type == "error"]  # fmt: skip
    assert not errors
