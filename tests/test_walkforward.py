"""The walk-forward harness (C4, spec 6.2 rules 2, 4, 5, 6): folds, the refusal to train on the
test season, the feature guard, preprocessing fit on training rows only, test rows that
cannot influence anything, calibration, determinism. Offline, synthetic data."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from tests.radar_synthetic import synthetic_dataset
from twm.backtest import walkforward as wf
from twm.modules.waiver_radar import models as rm
from twm.registry import FeatureCheckError

FEATS = ["snap_share_last", "fantasy_points_last", "depth_rank_now", "position"]
KEYS = ("season", "week", "gsis_id", "position")


@pytest.fixture(scope="module")
def data() -> pl.DataFrame:
    return rm.model_rows(synthetic_dataset())


def _run(rows: pl.DataFrame, name: str = "logit", features=FEATS, tests=(2014, 2015, 2016)):
    return wf.walk_forward(
        rows,
        estimator=rm.estimator(name),
        features=features,
        label="y_hit",
        module="waiver_radar",
        test_seasons=tests,
        keys=KEYS,
        tune_metric=rm.tune_metric_for("y_hit"),
    )


def test_plan_folds_trains_on_earlier_seasons_and_validates_on_the_last():
    folds = wf.plan_folds([2013, 2014, 2015, 2016], [2016, 2014, 2015])
    assert [f.test_season for f in folds] == [2014, 2015, 2016]
    first, second, third = folds
    assert first.thin and first.train_seasons == (2013,) and first.val_season is None
    assert second.train_seasons == (2013, 2014) and second.val_season == 2014
    assert second.tune_seasons == (2013,) and not second.thin
    assert third.train_seasons == (2013, 2014, 2015) and third.val_season == 2015
    assert third.tune_seasons == (2013, 2014)
    for f in folds:
        assert all(s < f.test_season for s in f.train_seasons)
    assert "thin" in first.describe() and "validate on 2015" in third.describe()
    with pytest.raises(wf.WalkForwardError, match="no earlier season"):
        wf.plan_folds([2013, 2014], [2013])
    with pytest.raises(wf.WalkForwardError, match="has no rows"):
        wf.plan_folds([2013, 2014], [2015])
    assert wf.season_span([2013, 2014, 2015]) == "2013-2015"
    assert wf.season_span([2013, 2015]) == "2013, 2015" and wf.season_span([]) == "-"


def test_walk_forward_folds_record_what_they_learned_from(data):
    res = _run(data)
    by_season = {fr.fold.test_season: fr for fr in res.folds}
    assert set(by_season) == {2014, 2015, 2016}
    for s, fr in by_season.items():
        assert fr.n_train == data.filter(pl.col("season") < s).height
        assert fr.n_train_pos == int(data.filter(pl.col("season") < s)["y_hit"].sum())
    thin = by_season[2014]
    assert thin.fold.thin and thin.trials == [] and "cross-fitted" in thin.calibration
    assert thin.params == dict(rm.LogitEstimator.default_params)
    assert thin.n_cal == thin.n_train  # every training row got an out-of-fold score
    fr = by_season[2016]
    assert fr.fold.val_season == 2015 and len(fr.trials) == len(rm.LogitEstimator().grid())
    assert fr.n_cal == data.filter(pl.col("season") == 2015).height
    assert "2015 scores" in fr.calibration
    # one prediction per test row, probabilities in [0, 1]
    test = data.filter(pl.col("season").is_in([2014, 2015, 2016]))
    assert res.predictions.height == test.height
    assert res.predictions.select(KEYS).sort(list(KEYS)).equals(test.select(KEYS).sort(list(KEYS)))
    assert res.predictions["score"].is_between(0, 1).all()


def test_the_harness_refuses_a_test_season_row_in_training(data):
    fold = wf.plan_folds([2013, 2014, 2015], [2015])[0]
    train = data.filter(pl.col("season") < 2015)
    kw = dict(
        estimator=rm.estimator("logit"), features=FEATS, label="y_hit", module="waiver_radar",
        keys=KEYS, tune_metric=rm.tune_metric_for("y_hit"),
    )  # fmt: skip
    wf.fit_fold(train, fold, **kw)  # clean: fine
    leak = pl.concat([train, data.filter(pl.col("season") == 2015).head(1)])
    with pytest.raises(wf.TestSeasonInTrainingError, match="test season 2015"):
        wf.fit_fold(leak, fold, **kw)
    later = pl.concat([train, data.filter(pl.col("season") == 2016).head(1)])
    with pytest.raises(wf.TestSeasonInTrainingError, match=r"\[2016\]"):
        wf.fit_fold(later, fold, **kw)
    thin = wf.plan_folds([2013, 2014], [2014])[0]
    with pytest.raises(wf.TestSeasonInTrainingError):
        wf.fit_fold(data.filter(pl.col("season") <= 2014), thin, **kw)
    with pytest.raises(wf.WalkForwardError, match="no 'season' column"):
        wf.assert_before(train.drop("season"), 2015, what="train")


@pytest.mark.parametrize(
    "bad", ["gsis_id", "team", "y_hit", "ecr_pos_rank", "owned_avg", "not_a_feature"]
)
def test_the_harness_refuses_identifiers_labels_metrics_and_unknown_columns(data, bad):
    rows = data.with_columns(pl.lit(1.0).alias(bad)) if bad not in data.columns else data
    with pytest.raises(FeatureCheckError):
        _run(rows, features=[*FEATS, bad])


def test_walk_forward_checks_its_input(data):
    with pytest.raises(wf.WalkForwardError, match="lack columns"):
        _run(data.drop("depth_rank_now"))
    with pytest.raises(wf.WalkForwardError, match="training row"):
        _run(data.with_columns(pl.lit(None, dtype=pl.Boolean).alias("y_hit")))


def test_preprocessing_is_fit_on_training_rows_only(data):
    """The test season's snap shares are shifted far up and many are missing: an imputer fit
    on them would fill a very different median and change the predictions."""
    rng = np.random.default_rng(3)
    test = data.filter(pl.col("season") == 2016)
    missing = pl.Series(rng.random(test.height) < 0.4)
    test = test.with_columns(
        pl.when(missing).then(None).otherwise(pl.col("snap_share_last") + 5.0)
        .alias("snap_share_last")
    )  # fmt: skip
    rows = pl.concat([data.filter(pl.col("season") < 2016), test])
    res = _run(rows, tests=[2016])
    fr = res.folds[0]
    imputer = fr.model.pipeline.named_steps["prep"].named_transformers_["num"]["impute"]
    train = rows.filter(pl.col("season") < 2016)
    medians = train.select(pl.col(c).cast(pl.Float64).median() for c in FEATS[:3]).row(0)
    assert list(imputer.statistics_) == pytest.approx(list(medians))
    # the same pipeline fit on training rows only reproduces the harness's final model ...
    est = rm.LogitEstimator()
    train = train.sort(list(KEYS))
    x_train, y_train = train.select(FEATS), train["y_hit"].cast(pl.Int8).to_numpy()
    own = est.fit(x_train, y_train, fr.params)
    test_sorted = test.sort(list(KEYS))
    raw = res.predictions.sort(list(KEYS))["raw_score"].to_numpy()
    assert own.predict(test_sorted.select(FEATS)) == pytest.approx(raw)
    # ... and fitting the preprocessing on training + test rows would change them
    both = pl.concat([train, test])
    pipe = est.pipeline(FEATS, fr.params)
    pipe.fit(rm._as_model_input(both.select(FEATS)), both["y_hit"].cast(pl.Int8).to_numpy())
    leaky = pipe.predict_proba(rm._as_model_input(test_sorted.select(FEATS)))[:, 1]
    assert not np.allclose(leaky, raw)


@pytest.mark.parametrize("season", [2014, 2015])  # the thin fold and a tuned fold
@pytest.mark.parametrize("name", ["logit", "lgbm"])
def test_test_rows_cannot_influence_training_tuning_or_calibration(data, name, season):
    """Scramble the features and flip the labels of half of the test season's rows (and of
    every later row): the other half's predictions do not move by a single bit."""
    rng = np.random.default_rng(11)
    touched = pl.Series(rng.random(data.height) < 0.5)
    hit = (pl.col("season") > season) | ((pl.col("season") == season) & pl.col("_touched"))
    marked = data.with_columns(touched.alias("_touched"))
    mutated = marked.with_columns(
        pl.when(hit).then(pl.col("snap_share_last") * -40 + 3)
        .otherwise(pl.col("snap_share_last")).alias("snap_share_last"),
        pl.when(hit).then(~pl.col("y_hit")).otherwise(pl.col("y_hit")).alias("y_hit"),
    )  # fmt: skip
    base = _run(marked, name, tests=[season])
    alt = _run(mutated, name, tests=[season])
    keep = marked.filter(~pl.col("_touched")).select(KEYS)
    a = base.predictions.join(keep, on=list(KEYS), how="semi").sort(list(KEYS))
    b = alt.predictions.join(keep, on=list(KEYS), how="semi").sort(list(KEYS))
    assert a.height > 100 and a.equals(b)
    # sanity: the mutation does reach the touched rows' own predictions
    t_a = base.predictions.join(keep, on=list(KEYS), how="anti").sort(list(KEYS))
    t_b = alt.predictions.join(keep, on=list(KEYS), how="anti").sort(list(KEYS))
    assert not t_a["raw_score"].equals(t_b["raw_score"])


@pytest.mark.parametrize("name", ["logit", "lgbm"])
def test_walk_forward_is_deterministic(data, name):
    one = _run(data, name)
    two = _run(data.sample(fraction=1.0, shuffle=True, seed=5), name)  # input order irrelevant
    assert one.predictions.equals(two.predictions)
    assert [f.params for f in one.folds] == [f.params for f in two.folds]


def test_lgbm_early_stopping_sets_the_refit_trees_and_importance_is_a_share(data):
    res = _run(data, "lgbm", features=FEATS, tests=[2015, 2016])
    for fr in res.folds:
        assert fr.params["n_estimators"] < rm.MAX_TREES  # chosen on the validation season
        assert all(t.refit_params["n_estimators"] >= 1 for t in fr.trials)
        assert fr.importance_kind == "gain"
        assert {n for n, _ in fr.importance} == set(FEATS)
        assert sum(s for _, s in fr.importance) == pytest.approx(1.0)
    top = res.folds[-1].importance[0][0]
    assert top in {"snap_share_last", "fantasy_points_last"}  # the two informative columns


def test_dominance_flag_and_choice_rules():
    fold = wf.Fold(2016, (2014, 2015), 2015, (2014,))

    def result(importance):
        return wf.FoldResult(
            fold=fold, params={}, trials=[], calibration="", n_train=1, n_train_pos=0, n_cal=1,
            importance=importance, importance_kind="gain", model=None,  # type: ignore[arg-type]
        )  # fmt: skip

    assert result([("b", 0.59), ("a", 0.41)]).dominant == ("b", 0.59)
    assert result([("a", 0.4), ("b", 0.35), ("c", 0.25)]).dominant is None  # 40% is not > 40%
    assert wf._shares({"a": 3.0, "b": 1.0, "c": 0.0}) == [("a", 0.75), ("b", 0.25), ("c", 0.0)]
    trials = [
        wf.Trial({"i": 0}, {}, 0.5, 0.30),
        wf.Trial({"i": 1}, {}, 0.6, 0.20),
        wf.Trial({"i": 2}, {}, 0.6, 0.25),  # same precision, higher PR-AUC: wins
        wf.Trial({"i": 3}, {}, 0.6, 0.25),  # a full tie: the earlier grid entry stays
        wf.Trial({"i": 4}, {}, None, 0.9),
    ]
    assert wf._choose(trials).params == {"i": 2}
    assert wf.cross_fit_parts([5, 1, 2, 3, 4, 6, 7], 4) == [[1, 5], [2, 6], [3, 7], [4]]
