"""Waiver Radar models and backtest (C4): baselines rank the right way, the backtest runs every
model on the same rows, is deterministic (predictions, model versions, report), grades with and
without rostered players, stores its predictions, and the CLI works end to end. Offline,
synthetic data (tests/radar_synthetic.py); an opt-in realdata test runs two real seasons."""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from tests.radar_synthetic import synthetic_dataset
from twm import predictions as pr
from twm.cli import app
from twm.modules.waiver_radar import backtest as bt
from twm.modules.waiver_radar import models as rm
from twm.registry import check_features

ROOT = Path(__file__).resolve().parents[1]
TESTS = (2014, 2015, 2016)


@pytest.fixture(scope="module")
def dataset() -> pl.DataFrame:
    return synthetic_dataset()


@pytest.fixture(scope="module")
def run(dataset) -> bt.BacktestRun:
    return bt.run_backtest(dataset, labels=("y_hit", "y_sustained"), test_seasons=TESTS)


def _scored(rows: list[tuple[str, str, float | None]]) -> pl.DataFrame:
    """(gsis_id, position, value) rows of one as-of."""
    return pl.DataFrame(
        {
            "season": 2020, "week": 3, "gsis_id": [r[0] for r in rows],
            "position": [r[1] for r in rows],
            "fantasy_points_last": [r[2] for r in rows], "snap_share_delta": [r[2] for r in rows],
            "ecr_pos_rank": [None if r[2] is None else int(r[2]) for r in rows],
            "ecr_available": True,
        }
    )  # fmt: skip


def test_the_model_features_are_the_registered_ones():
    assert len(rm.FEATURES) == 49 and "position" in rm.FEATURES
    assert len(check_features(rm.FEATURES, "waiver_radar")) == 49
    for b in rm.BASELINES.values():
        assert b.column not in {"gsis_id", "team", "name"}


@pytest.mark.parametrize("name", ["baseline_last_points", "baseline_snap_delta"])
def test_higher_value_baselines_rank_highest_first(name):
    rows = [("00-3", "WR", 12.0), ("00-1", "WR", 12.0), ("00-2", "WR", None),
            ("00-4", "WR", 20.0), ("00-5", "RB", 1.0)]  # fmt: skip
    ranked = rm.rank_scores(rm.baseline_scores(_scored(rows), name))
    got = {r["gsis_id"]: r["rank"] for r in ranked.iter_rows(named=True)}
    # 20 first; the 12s tie -> smaller id first; no value last; RB is its own list
    assert got == {"00-4": 1, "00-1": 2, "00-3": 3, "00-2": 4, "00-5": 1}


def test_expert_baseline_ranks_the_best_rank_first_and_only_where_covered():
    rows = [("00-3", "WR", 30.0), ("00-1", "WR", 2.0), ("00-2", "WR", None), ("00-4", "WR", 7.0)]
    df = _scored(rows)
    ranked = rm.rank_scores(rm.baseline_scores(df, "baseline_ecr"))
    got = {r["gsis_id"]: r["rank"] for r in ranked.iter_rows(named=True)}
    assert got == {"00-1": 1, "00-4": 2, "00-3": 3, "00-2": 4}
    uncovered = df.with_columns(pl.lit(False).alias("ecr_available"))
    assert rm.baseline_scores(uncovered, "baseline_ecr").height == 0


def test_model_rows_are_pool_final_and_train_eligible(dataset):
    rows = rm.model_rows(dataset)
    assert rows["in_pool"].all() and rows["train_eligible"].all()
    assert (rows["label_status"] == "final").all()
    assert rows.height < dataset.height


def test_every_model_is_graded_on_the_same_rows(run):
    test = run.rows.filter(pl.col("season").is_in(TESTS)).select(rm.KEYS).sort(list(rm.KEYS))
    for label in run.labels:
        for model in ("baseline_last_points", "baseline_snap_delta", "logit", "lgbm"):
            preds = run.run(label, model).predictions
            assert preds.select(rm.KEYS).sort(list(rm.KEYS)).equals(test), (label, model)
            assert preds["rank"].min() == 1
        ecr = run.run(label, "baseline_ecr").predictions
        assert set(ecr["season"].unique()) == {2015, 2016}  # coverage starts in 2015 here


def test_backtest_is_deterministic(dataset, run):
    again = bt.run_backtest(dataset, labels=("y_hit", "y_sustained"), test_seasons=TESTS)
    for key, mr in run.runs.items():
        assert mr.predictions.equals(again.runs[key].predictions), key
        assert [v["model_version"] for v in mr.versions] == [
            v["model_version"] for v in again.runs[key].versions
        ]
    one = bt.build_backtest_report(run, created="Created A.")
    two = bt.build_backtest_report(again, created="Created B.")
    strip = lambda t: t.replace("Created A.", "").replace("Created B.", "")  # noqa: E731
    assert strip(one.markdown) == strip(two.markdown)
    assert one.csv_rows == two.csv_rows


def test_model_versions_fingerprint_the_training_rows(dataset, run):
    mr = run.run("y_hit", "lgbm")
    versions = [v["model_version"] for v in mr.versions]
    assert len(set(versions)) == len(TESTS)
    assert set(mr.predictions["model_version"].unique()) == set(versions)
    # the version can be recomputed from the stored record
    for v in mr.versions:
        assert v["model_version"] == pr.model_version(
            module=v["module"], model=v["model"], label=v["label"],
            features=json.loads(v["feature_list"]), params=json.loads(v["params"]),
            training_seasons=json.loads(v["training_seasons"]), test_season=v["test_season"],
            dataset_hash=v["dataset_hash"],
        )  # fmt: skip

    def versions_of(ds):
        other = bt.run_backtest(ds, models=["logit", "baseline_last_points"], test_seasons=TESTS)
        return {
            (m, v["test_season"]): (v["model_version"], json.loads(v["notes"])["test_rows_hash"])
            for m in ("logit", "baseline_last_points")
            for v in other.run("y_hit", m).versions
        }

    old = versions_of(dataset)
    new = versions_of(
        dataset.with_columns(
            pl.when(pl.col("season") == 2015).then(pl.col("xfp_avg3") + 1)
            .otherwise(pl.col("xfp_avg3"))
        )
    )  # fmt: skip
    # 2015 is a training season of the 2016 fold only: only that model gets a new version
    assert old[("logit", 2014)] == new[("logit", 2014)]
    assert old[("logit", 2015)][0] == new[("logit", 2015)][0]  # 2015 is its test season ...
    assert old[("logit", 2015)][1] != new[("logit", 2015)][1]  # ... recorded in the notes
    assert old[("logit", 2016)][0] != new[("logit", 2016)][0]
    for s in TESTS:  # a baseline learns nothing: same version, the new rows are noted
        assert old[("baseline_last_points", s)][0] == new[("baseline_last_points", s)][0]
    assert old[("baseline_last_points", 2015)][1] != new[("baseline_last_points", 2015)][1]


def test_grading_with_and_without_rostered_players(run):
    g = bt.grade(run, "y_hit", "logit")
    rostered = g.rows.filter(pl.col("owned_avg") >= 50)
    assert rostered.height > 0
    n_all = g.groups["all"]["n_rows"].sum()
    n_without = g.groups["without_rostered"]["n_rows"].sum()
    assert n_all - n_without == rostered.height
    # 2014 has no rostership figures: the two subsets agree there
    a = g.pooled("all", [2014])
    b = g.pooled("without_rostered", [2014])
    assert a.value == b.value and a.n_rows == b.n_rows


def test_verdict_rules(run):
    graded = {m: bt.grade(run, "y_hit", m) for m in run.models}
    v = bt.verdict(graded, "y_hit", TESTS)
    full = list(TESTS)
    scores = {m: graded[m].pooled("all", full).value for m in rm.MODELS}
    assert v.winner == max(rm.MODELS, key=lambda m: (scores[m], m == "logit"))
    beats = all(scores[v.winner] > graded[b].pooled("all", full).value for b in rm.NAIVE_BASELINES)
    assert v.accepted is beats and ("PASS" if beats else "FAIL") in v.text
    # a tie goes to the simpler model
    tied = {**graded, "lgbm": graded["logit"]}
    assert bt.verdict(tied, "y_hit", TESTS).winner == "logit"
    no_naive = {m: g for m, g in graded.items() if m != "baseline_snap_delta"}
    assert bt.verdict(no_naive, "y_hit", TESTS).accepted is None
    assert bt.verdict({}, "y_hit", TESTS).winner is None


def test_report_contents(run):
    rep = bt.build_backtest_report(run, created="Created X.")
    text = rep.markdown
    for part in ("## Verdict", "### Precision@10, pooled", "2014-2016 all pool rows",
                 "2015-2016 without rostered", "### Per position", "### Per season",
                 "### Hit rate by rank", "### PR-AUC and Brier", "### The experts' ranks",
                 "### Calibration of the winner", "Dominance check", "## Label `y_sustained`",
                 "- `y_hit` (primary)", "*reference: xfp_avg3 alone*"):  # fmt: skip
        assert part in text, part
    assert text.count("Created X.") == 1
    assert set(rep.verdicts) == {"y_hit", "y_sustained"}
    header = list(bt.CSV_COLUMNS)
    assert all(list(r) == header for r in rep.csv_rows)
    scopes = {r["scope"] for r in rep.csv_rows}
    assert {"pooled", "position", "season", "bucket", "calibration", "importance", "tuning",
            "ecr_covered", "coverage"} <= scopes  # fmt: skip


def test_store_frames(run, tmp_path):
    preds, versions, outcomes = bt.store_frames(run, created_at=pr.now_utc())
    n = sum(mr.predictions.height for mr in run.runs.values())
    assert preds.height == n
    assert preds["kind"].unique().to_list() == ["backtest"]
    assert preds["reasons_json"].unique().to_list() == ["[]"] and preds["band"].is_null().all()
    assert preds["horizon"].unique().to_list() == [3]
    assert versions.height == len(run.labels) * len(run.models) * len(TESTS)
    assert outcomes.height == run.rows.filter(pl.col("season").is_in(TESTS)).height
    path = tmp_path / "p.duckdb"
    pr.write_predictions(path, predictions=preds, versions=versions, outcomes=outcomes)
    pr.write_predictions(path, predictions=preds, versions=versions, outcomes=outcomes)
    assert pr.read_table(path, "predictions").height == n  # idempotent
    stored = pr.read_table(path, "model_versions", "model = 'lgbm' AND label = 'y_hit'")
    assert sorted(stored["test_season"].to_list()) == list(TESTS)
    assert all('"fixed"' in p and '"deterministic": true' in p for p in stored["params"])


def test_cli_backtest_end_to_end(dataset, tmp_path):
    src = tmp_path / "ds.parquet"
    dataset.write_parquet(src)
    out = tmp_path / "rep" / "backtest.md"
    store = tmp_path / "store.duckdb"
    args = ["radar", "backtest", "--dataset", str(src), "--start", "2014", "--end", "2016",
            "--store", str(store), "--out", str(out), "--model", "baseline_last_points",
            "--model", "baseline_snap_delta", "--model", "logit"]  # fmt: skip
    runner = CliRunner()
    res = runner.invoke(app, args)
    assert res.exit_code == 0, res.output
    assert "stored" in res.output and "y_hit:" in res.output
    first_md, first_csv = out.read_text(), out.with_suffix(".csv").read_text()
    first_store = pr.read_table(store, "predictions").drop("created_at")
    assert runner.invoke(app, args).exit_code == 0
    drop_created = lambda t: [x for x in t.splitlines() if "Created" not in x]  # noqa: E731
    assert drop_created(out.read_text()) == drop_created(first_md)
    assert out.with_suffix(".csv").read_text() == first_csv
    assert pr.read_table(store, "predictions").drop("created_at").equals(first_store)
    bad = runner.invoke(app, [*args[:8], "--model", "nope"])
    assert bad.exit_code == 1 and "unknown model" in bad.output
    missing = runner.invoke(app, ["radar", "backtest", "--dataset", str(tmp_path / "x.pq")])
    assert missing.exit_code == 1 and "run `twm radar dataset` first" in missing.output


# --------------------------------------------------------------------------------------
# Real data (opt-in: `uv run pytest -m realdata`; reads data/waiver_radar/dataset.parquet)
# --------------------------------------------------------------------------------------


@pytest.mark.realdata
def test_real_two_season_backtest_beats_a_random_ranking():
    path = ROOT / "data" / "waiver_radar" / "dataset.parquet"
    if not path.exists():
        pytest.skip("run `uv run twm radar dataset` first")
    ds = pl.read_parquet(path)
    run = bt.run_backtest(ds, models=["baseline_last_points", "logit", "lgbm"],
                          test_seasons=[2016, 2017])  # fmt: skip
    for model in ("logit", "lgbm", "baseline_last_points"):
        g = bt.grade(run, "y_hit", model)
        pooled = g.pooled("all", [2016, 2017])
        assert pooled.n_groups == 120 and pooled.base_rate is not None
        assert pooled.value > pooled.base_rate + 0.2, (model, pooled)  # far above random
    for mr in (run.run("y_hit", "logit"), run.run("y_hit", "lgbm")):
        assert [f.fold.train_seasons for f in mr.folds] == [
            (2013, 2014, 2015), (2013, 2014, 2015, 2016)
        ]  # fmt: skip
        assert all(f.dominant is None or f.dominant[1] < 0.6 for f in mr.folds)
