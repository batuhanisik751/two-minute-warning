"""The K and D/ST streamer's models and walk-forward backtest (S1d) on a small synthetic dataset:
list metrics by hand, baseline directions, the training-cutoff guard, the refusal to train on the
test season, one prediction per graded row, the predictions store, the report and the CLI."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest
from typer.testing import CliRunner

from twm import predictions as pr
from twm.backtest.walkforward import (
    RAW_SCORE,
    SCORE,
    TestSeasonInTrainingError,
    fit_fold,
    plan_folds,
)
from twm.cli import app
from twm.modules.streamer import backtest as bt
from twm.modules.streamer import backtest_report as rep
from twm.modules.streamer import models as sm
from twm.modules.streamer.features import (
    DEFENSE_FEATURES,
    FEATURE_COLUMNS,
    FEATURE_SCHEMA,
    KICKER_FEATURES,
)
from twm.registry import check_features

SEASONS = (2012, 2013, 2014)
WEEKS = 6
N = 8  # entities per position; the first 2 are never in the pool
LATE = ("K", 2013, 3, "00-K0005")  # its label is (artificially) public only in October 2014


def toy_dataset(seed: int = 7) -> pl.DataFrame:
    """Every (season, week, position, entity): features, in_pool, a label driven by
    kdst_points_per_game (y_start = it is above 0.5), available_at = as-of + 6 days; the last
    week of each season has no label (season_end)."""
    rng = np.random.default_rng(seed)
    rows = []
    for season in SEASONS:
        first = datetime(season, 9, 11, 14, tzinfo=UTC)
        for week in range(1, WEEKS + 1):
            as_of = first + timedelta(days=7 * (week - 1))
            final = week < WEEKS
            for pos in sm.POSITIONS:
                for i in range(N):
                    eid = f"00-K{i:04d}" if pos == "K" else f"DST-T{i}"
                    feats = {c: float(rng.random()) for c in FEATURE_COLUMNS}
                    other = DEFENSE_FEATURES if pos == "K" else KICKER_FEATURES
                    feats.update(dict.fromkeys(other))
                    feats["weekly_ecr_rank"] = None  # NULL before 2020, like the real data
                    feats["weekly_ecr_listed"] = None
                    y = feats["kdst_points_per_game"] > 0.5
                    avail = as_of + timedelta(days=6)
                    if (pos, season, week, eid) == LATE:
                        avail = datetime(2014, 10, 1, tzinfo=UTC)
                    rows.append({
                        "season": season, "week": week, "as_of": as_of, "position": pos,
                        "entity_id": eid, "in_pool": i >= 2,
                        "is_team_kicker": (i % 3 != 0) if pos == "K" else None,
                        **{k: v for k, v in feats.items() if k != "is_team_kicker"},
                        "label_status": "final" if final else "season_end",
                        "y_start": y if final else None,
                        "available_at": avail if final else None,
                    })  # fmt: skip
    df = pl.DataFrame(rows)
    return df.with_columns(
        pl.col(c).cast(FEATURE_SCHEMA[c]) for c in FEATURE_COLUMNS if c in df.columns
    )


@pytest.fixture(scope="module")
def data() -> pl.DataFrame:
    return toy_dataset()


@pytest.fixture(scope="module")
def run(data: pl.DataFrame) -> bt.BacktestRun:
    return bt.run_backtest(data, test_seasons=[2013, 2014])


def _list(season: int, week: int, pos: str, ys: list[bool]) -> list[dict]:
    return [
        {"season": season, "week": week, "position": pos, "entity_id": f"e{i}", "rank": i + 1,
         "y": y}
        for i, y in enumerate(ys)
    ]  # fmt: skip


def test_list_metrics_by_hand():
    ranked = pl.DataFrame(
        _list(2020, 1, "K", [True, False, False, True, False, True])
        + _list(2020, 1, "DST", [False, True])
    )
    t = {r["position"]: r for r in sm.list_table(ranked, "y").iter_rows(named=True)}
    k, d = t["K"], t["DST"]
    assert (k["p_at_1"], k["p_at_3"], k["p_at_5"]) == (1.0, pytest.approx(1 / 3), 0.4)
    assert (k["n_rows"], k["n_pos"], k["short_5"]) == (6, 3, False)
    # a list of 2 rows divides by its length at k = 3 and 5, and is flagged short
    assert (d["p_at_1"], d["p_at_3"], d["p_at_5"], d["short_3"]) == (0.0, 0.5, 0.5, True)


def test_pooled_interval_and_paired_difference_on_toy_lists():
    lists = pl.DataFrame({
        "season": [2020, 2020, 2021, 2021], "week": [1, 2, 1, 2], "position": ["K"] * 4,
        "n_rows": [5] * 4, "n_pos": [2] * 4, "p_at_5": [0.4, 0.6, 0.2, 0.4],
    })  # fmt: skip
    a = bt.Graded("a", "K", pl.DataFrame(), lists)
    b = bt.Graded("b", "K", pl.DataFrame(), lists.with_columns(pl.col("p_at_5") - 0.2))
    iv = a.interval(5, [2020, 2021])
    assert iv.value == pytest.approx(0.4) and iv.n_blocks == 2 and iv.n_groups == 4
    assert iv.lo == pytest.approx(0.3) and iv.hi == pytest.approx(0.5)  # season means
    d = a.diff(b, 5, [2020, 2021])
    assert (d.value, d.lo, d.hi, d.share_above_zero) == pytest.approx((0.2, 0.2, 0.2, 1.0))


def test_baselines_rank_by_their_column_with_nulls_last_and_ties_by_id():
    rows = pl.DataFrame({
        "season": [2020] * 4, "week": [1] * 4, "position": ["DST"] * 4,
        "entity_id": ["DST-B", "DST-A", "DST-C", "DST-D"],
        "next_opp_points_per_game": [30.0, 17.0, None, 17.0],
    })  # fmt: skip
    ranked = sm.rank_scores(sm.baseline_scores(rows, "baseline_opponent"))
    # DST: FEWER points scored by the next opponent is better; the tie goes to DST-A
    assert ranked.get_column("entity_id").to_list() == ["DST-A", "DST-D", "DST-B", "DST-C"]
    assert ranked.get_column("rank").to_list() == [1, 2, 3, 4]
    k = rows.with_columns(
        pl.lit("K").alias("position"),
        pl.col("next_opp_points_per_game").alias("next_opp_points_allowed_per_game"),
    )
    ranked_k = sm.rank_scores(sm.baseline_scores(k, "baseline_opponent"))
    assert ranked_k.get_column("entity_id").to_list() == ["DST-B", "DST-A", "DST-D", "DST-C"]


def test_position_features_are_registered_and_exclude_the_other_position():
    k, d = sm.position_features("K"), sm.position_features("DST")
    assert not set(k) & set(DEFENSE_FEATURES) and set(KICKER_FEATURES) <= set(k)
    assert not set(d) & set(KICKER_FEATURES) and set(DEFENSE_FEATURES) <= set(d)
    assert set(k) | set(d) == set(FEATURE_COLUMNS)
    check_features(k, "streamer")
    check_features(d, "streamer")
    with pytest.raises(ValueError):
        sm.position_features("WR")


def test_training_cutoff_filters_and_refuses_late_labels(data):
    cutoff = bt.training_cutoff(data, 2014)
    assert cutoff == datetime(2014, 9, 11, 14, tzinfo=UTC)  # the season's first as-of
    source = sm.training_source(data)
    train = bt.training_rows(source, 2014, cutoff)
    assert set(train.get_column("season")) == {2012, 2013}
    assert train.filter(pl.col("label_status") != "final").height == 0
    late = pl.col("entity_id") == LATE[3]
    assert train.filter(late & (pl.col("season") == 2013) & (pl.col("week") == 3)).height == 0
    bt.assert_available_by(train, cutoff, 2014)  # nothing late: no error
    unfiltered = source.filter(pl.col("season") < 2014)
    with pytest.raises(TestSeasonInTrainingError, match="after the cutoff"):
        bt.assert_available_by(unfiltered, cutoff, 2014)  # the LATE row
    no_label = train.head(1).with_columns(pl.lit(None).cast(pl.Datetime("us", "UTC")).alias(
        "available_at"))  # fmt: skip
    with pytest.raises(TestSeasonInTrainingError):
        bt.assert_available_by(no_label, cutoff, 2014)
    with pytest.raises(ValueError, match="no rows"):
        bt.training_cutoff(data, 2030)


def test_refuses_to_train_on_the_test_season(data, monkeypatch):
    source, graded = sm.training_source(data), sm.graded_rows(data)
    cutoff = bt.training_cutoff(data, 2014)
    # a broken filter that lets the test season (and the late row) through: the cutoff guard
    monkeypatch.setattr(bt, "training_rows", lambda src, s, c: src)
    with pytest.raises(TestSeasonInTrainingError):
        bt.run_model_season(source, graded, "logit", "K", 2014, cutoff)
    # the shared harness the adapter calls refuses test-season rows in a training frame too
    k = source.filter(pl.col("position") == "K")
    fold = plan_folds(k.get_column("season").unique().to_list(), [2014])[0]
    with pytest.raises(TestSeasonInTrainingError, match="not before the test season 2014"):
        fit_fold(
            k,
            fold,
            estimator=sm.estimator("logit"),
            features=sm.position_features("K"),
            label=sm.LABEL,
            module=sm.MODULE,
            keys=sm.KEYS,
            tune_metric=sm.tune_metric(),
        )


def test_every_method_ranks_every_graded_row(run, data):
    graded = sm.graded_rows(data).filter(pl.col("season").is_in([2013, 2014]))
    for pos in sm.POSITIONS:
        g = graded.filter(pl.col("position") == pos).select(sm.KEYS).sort(sm.KEYS)
        for m in sm.ALL_METHODS:
            p = run.run(m, pos).predictions
            assert p.select(sm.KEYS).sort(sm.KEYS).equals(g), (m, pos)
            sizes = p.group_by(sm.GROUP).agg(pl.len().alias("n"), pl.col("rank").max())
            assert (sizes["n"] == sizes["rank"]).all()
            assert p.get_column("model_version").n_unique() == 2  # one per test season
    # the late row (public only in October 2014) is not in the 2014 K model's training rows
    k = sm.training_source(data).filter((pl.col("position") == "K") & (pl.col("season") < 2014))
    assert run.run("logit", "K").n_train[2014] == k.height - 1
    assert run.run("logit", "K").n_train[2013] == k.filter(pl.col("season") == 2012).height
    folds = run.run("logit", "DST").folds
    assert [f.fold.thin for f in folds] == [True, False]
    assert [f.fold.train_seasons for f in folds] == [(2012,), (2012, 2013)]


def test_models_learn_the_signal(run):
    # y_start = kdst_points_per_game > 0.5 in the toy data: the PPG baseline ranks perfectly and
    # the logistic regression (26-29 features, 30-60 training rows) gets close; the other
    # baselines rank noise
    for pos in sm.POSITIONS:
        ppg = bt.grade(run, "baseline_ppg", pos)
        logit = bt.grade(run, "logit", pos)
        last = bt.grade(run, "baseline_last_points", pos)
        perfect = ppg.lists.select((pl.col("p_at_1") == (pl.col("n_pos") > 0)).all()).item()
        assert perfect  # the #1 pick started in every list that had a starter
        best = ppg.interval(3, [2013, 2014]).value
        assert logit.interval(3, [2013, 2014]).value >= best - 0.1
        assert logit.interval(3, [2013, 2014]).value > last.interval(3, [2013, 2014]).value
        s = logit.rows.get_column(SCORE)
        assert s.min() >= 0.0 and s.max() <= 1.0 and logit.rows[RAW_SCORE].null_count() == 0


def test_store_frames_write_to_a_predictions_store(run, tmp_path):
    preds, versions = bt.store_frames(run, created_at=datetime(2026, 9, 29, 12))
    store = tmp_path / "p.duckdb"
    counts = pr.write_predictions(store, predictions=preds, versions=versions)
    n_graded = run.graded.height
    assert counts == {"predictions": n_graded * 5, "model_versions": 5 * 2 * 2, "outcomes": 0}
    back = pr.read_table(store, "predictions")
    types = back.group_by("rank_group").agg(pl.col("entity_type").unique()).sort("rank_group")
    assert types.rows() == [("DST", ["team"]), ("K", ["player"])]
    assert set(back.get_column("module")) == {"streamer"} and set(back["horizon"]) == {1}
    names = set(pr.read_table(store, "model_versions").get_column("model"))
    assert names == {bt.stored_name(m, p) for m in sm.ALL_METHODS for p in sm.POSITIONS}
    # idempotent: writing the same run again replaces its rows
    pr.write_predictions(store, predictions=preds, versions=versions)
    assert pr.read_table(store, "predictions").height == n_graded * 5


def test_report_is_deterministic_and_complete(run, data, tmp_path):
    alt = bt.run_backtest(data, methods=sm.MODELS, test_seasons=[2013, 2014], train_on="universe")
    a = rep.build_report(run, alt=alt, created="Created now.")
    b = rep.build_report(run, alt=alt, created="Created now.")
    assert a.markdown == b.markdown and a.csv_rows == b.csv_rows
    for head in ("## Verdict", "### K", "### DST", "## By season", "## Probabilities",
                 "## Sensitivity", "## Folds", "#1 pick started"):  # fmt: skip
        assert head in a.markdown
    assert set(a.verdicts) == {"K", "DST"} and a.verdicts["K"].winner in sm.MODELS
    csv_path = rep.write_report(a, tmp_path / "r" / "backtest.md")
    csv = pl.read_csv(csv_path)
    assert tuple(csv.columns) == rep.CSV_COLUMNS
    assert {"pooled", "diff", "season", "brier", "calibration"} <= set(csv["scope"])
    assert set(csv["train_on"]) == {"pool", "universe"}
    with pytest.raises(ValueError, match="train_on"):
        bt.run_backtest(data, methods=["logit"], test_seasons=[2014], train_on="everything")
    with pytest.raises(ValueError, match="no graded"):
        bt.run_backtest(data, methods=["logit"], test_seasons=[2011])


def test_cli_backtest(data, tmp_path):
    ds = tmp_path / "dataset.parquet"
    data.write_parquet(ds)
    out, store = tmp_path / "rep" / "backtest.md", tmp_path / "store.duckdb"
    runner = CliRunner()
    args = ["streamer", "backtest", "--dataset", str(ds), "--start", "2013", "--end", "2014",
            "--out", str(out)]  # fmt: skip
    res = runner.invoke(app, [*args, "--model", "baseline_ppg", "--model", "logit", "--pos",
                              "D/ST", "--store", str(store)])  # fmt: skip
    assert res.exit_code == 0, res.output
    assert "stored 120 predictions, 4 model versions" in res.output  # 2 methods x 60 DST rows
    assert out.exists() and out.with_suffix(".csv").exists() and store.exists()
    assert "### DST" in out.read_text() and "### K" not in out.read_text()
    res = runner.invoke(app, [*args, "--no-sensitivity", "--pos", "K"])
    assert res.exit_code == 0, res.output
    assert "stored" not in res.output  # no --store: nothing written to any store
    assert "## Sensitivity" not in out.read_text()
    bad = runner.invoke(app, ["streamer", "backtest", "--dataset", str(tmp_path / "none.pq")])
    assert bad.exit_code == 1 and "dataset not found" in bad.output
    bad = runner.invoke(app, [*args, "--model", "magic"])
    assert bad.exit_code == 1 and "cannot run the backtest" in bad.output


# ---- real data (opt-in: uv run pytest -m realdata) ------------------------------------------


@pytest.mark.realdata
def test_real_backtest_respects_the_cutoff_and_is_deterministic(real_full_db):
    from twm.modules.streamer.dataset import build_dataset

    path, _ = real_full_db
    data = build_dataset(path, [2012, 2013, 2014, 2015])
    runs = [bt.run_backtest(data, test_seasons=[2014, 2015]) for _ in range(2)]
    for s in (2014, 2015):
        cutoff = bt.training_cutoff(data, s)
        assert cutoff.month == 9  # the season's first Tuesday as-of
        for pos in sm.POSITIONS:
            src = runs[0].source.filter(pl.col("position") == pos)
            train = bt.training_rows(src, s, cutoff)
            bt.assert_available_by(train, cutoff, s)
            assert train.height == runs[0].run("logit", pos).n_train[s] > 0
    for key, mr in runs[0].runs.items():
        assert mr.predictions.equals(runs[1].runs[key].predictions), key
    a, b = (rep.build_report(r, created="x") for r in runs)
    assert a.markdown == b.markdown
