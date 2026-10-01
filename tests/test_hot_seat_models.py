"""Hot-Seat H3b models and metrics on synthetic data (offline)."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest
from sklearn.metrics import average_precision_score, roc_auc_score

from twm.backtest.walkforward import TestSeasonInTrainingError, fit_fold, plan_folds
from twm.modules.hot_seat import backtest as hb
from twm.modules.hot_seat import evaluation as ev
from twm.modules.hot_seat import models as hm


def _slice(seed: int = 3, n: int = 400, k: int = 8) -> ev.Slice:
    rng = np.random.default_rng(seed)
    p = np.round(rng.uniform(size=n), 1)  # rounded: many ties
    y = (rng.uniform(size=n) < p).astype(float)
    return ev.Slice.of(y, p, rng.integers(2006, 2006 + k, size=n))


def test_unweighted_metrics_equal_scikit_learn():
    s = _slice()
    got = ev.resampled(s, np.ones((1, s.n_blocks)))
    assert got["roc_auc"][0] == pytest.approx(roc_auc_score(s.y, s.p))
    assert got["pr_auc"][0] == pytest.approx(average_precision_score(s.y, s.p))
    assert got["brier"][0] == pytest.approx(np.mean((s.p - s.y) ** 2))


def test_season_weights_equal_duplicated_seasons():
    s = _slice()
    w = np.zeros((1, s.n_blocks))
    w[0, 0], w[0, 1], w[0, 3] = 2, 1, 1  # season 0 drawn twice
    rows = np.concatenate([np.flatnonzero(s.block == b) for b in (0, 0, 1, 3)])
    got = ev.resampled(s, w)
    assert got["roc_auc"][0] == pytest.approx(roc_auc_score(s.y[rows], s.p[rows]))
    assert got["pr_auc"][0] == pytest.approx(average_precision_score(s.y[rows], s.p[rows]))


def test_interval_brackets_value_and_paired_difference_of_self_is_zero():
    m = ev.slice_metrics(_slice(), n_boot=200)
    for e in m.values():
        assert e.lo <= e.value <= e.hi
    d = ev.difference(m["roc_auc"], m["roc_auc"])
    assert d.value == 0 and d.lo == 0 and d.hi == 0
    r = ev.ratio(np.array([1, 2, 0]), np.array([2, 4, 1]), n_boot=200)
    assert r.value == pytest.approx(3 / 7) and r.lo <= r.value <= r.hi


def _rows(seasons=range(2002, 2009), teams: int = 8, seed: int = 7) -> pl.DataFrame:
    """Synthetic labelled rows: weekly weeks 2-5 + an end-of-season row per team-season;
    ``tenure_seasons`` = the season (lets a spy see which seasons a model was fit on); the
    last team of each season is interim (``division_rank`` = 99 marks it)."""
    rng = np.random.default_rng(seed)
    recs = []
    for s in seasons:
        for t in range(teams):
            bad = rng.normal()
            fired = int(bad > 0.6)
            for snap, week, left in [("weekly", w, 6 - w) for w in range(2, 6)] + [
                ("end_of_season", 6, 0)
            ]:
                feats = {f: float(rng.normal()) for f in hm.FEATURES}
                feats.update(
                    wins_vs_expected=-bad + 0.3 * rng.normal(), games_remaining=left,
                    tenure_seasons=s, division_rank=99 if t == teams - 1 else 1,
                    reg_games_played=week, reg_wins=float(rng.integers(0, week + 1)),
                )  # fmt: skip
                recs.append(
                    dict(season=s, snapshot=snap, week=week, team=f"T{t}", coach_id=f"c{t}",
                         is_interim=t == teams - 1, y=fired, censored=False,
                         event=int(fired and left == 0), departure_type="", **feats)
                )  # fmt: skip
    return pl.DataFrame(recs).with_columns(pl.col("season", "week").cast(pl.Int32))


class _Spy:
    """The Radar's logistic regression, recording what each fit saw."""

    name = "spy"
    default_params = hm.LogitEstimator.default_params
    fixed_params = hm.LogitEstimator.fixed_params

    def __init__(self) -> None:
        self.seen: list[tuple[float, float]] = []  # (max season, max division_rank) per fit
        self._base = hm.LogitEstimator()

    def grid(self):
        return self._base.grid()[:2]

    def fit(self, x, y, params, *, eval_x=None, eval_y=None):
        self.seen.append((x["tenure_seasons"].max(), x["division_rank"].max()))
        return self._base.fit(x, y, params)


class _ConstHazard:
    """h = 0.1 on weekly intervals, 0.5 on the final one."""

    def predict(self, x: pl.DataFrame) -> np.ndarray:
        return np.where(x["games_remaining"].to_numpy() == 0, 0.5, 0.1)


def test_hazard_aggregation_is_one_minus_product_over_remaining_intervals():
    x = pl.DataFrame({"games_remaining": [3, 1, 0], "wins_vs_expected": [0.0, 1.0, 2.0]})
    risk = hm.season_risk(_ConstHazard(), x)
    assert risk == pytest.approx([1 - 0.9**3 * 0.5, 1 - 0.9 * 0.5, 0.5])


def test_walk_forward_never_sees_the_test_season_nor_interims():
    rows = _rows()
    spy = _Spy()
    spec = hm.Spec("spy", lambda: spy, hm.FEATURES, "y", "model")
    run = hb.run_model(spec, rows, test_seasons=(2006, 2007, 2008))
    folds = [fr.fold.test_season for fr in run.result.folds]
    assert folds == [2006, 2007, 2008]
    # the spy is called (grid x tune) + final per fold, in fold order: every fit before 2008
    assert max(s for s, _ in spy.seen) == 2007 and all(r == 1 for _, r in spy.seen)
    assert len(spy.seen) == 3 * (2 + 1)
    # interims are scored (and kept apart), the test seasons only
    scored = run.scored
    assert scored["season"].unique().sort().to_list() == [2006, 2007, 2008]
    assert scored.filter(pl.col("is_interim"))["prob"].null_count() == 0
    assert scored.filter(pl.col("is_interim")).height == 3 * 5
    # and the harness refuses a training frame holding the test season
    with pytest.raises(TestSeasonInTrainingError):
        fit_fold(rows.filter(~pl.col("is_interim")), plan_folds(range(2002, 2009), [2006])[0],
                 estimator=hm.LogitEstimator(), features=hm.FEATURES, label="y",
                 module=hm.MODULE, keys=hm.KEYS, tune_metric=hm.neg_log_loss("y"))  # fmt: skip


def test_top_k_counts_ranks_within_each_season():
    df = pl.DataFrame({"season": [1] * 7 + [2] * 3, "team": list("ABCDEFG") + list("ABC"),
                       "prob": [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.0],
                       "y": [1, 0, 0, 0, 0, 1, 1, 0, 0, 1]})  # fmt: skip
    c = hb.top_k_counts(df, k=5)
    assert c.rows() == [(1, 3, 1), (2, 1, 1)]  # season, positives, hits


def test_backtest_end_to_end_and_provisional_banner(tmp_path):
    from twm.modules.hot_seat import backtest_report as hr

    rows = _rows()
    res = hb.run_backtest(rows, rows, test_seasons=(2006, 2007, 2008), n_boot=50)
    assert set(res.metrics["variant"].unique()) == {"main", "censored_dropped", "rup_positive"}
    assert res.predictions.filter(pl.col("model") == "lgbm").height > 0
    assert "LightGBM" in res.lgbm_text
    eos = res.firings.filter(pl.col("season") == 2006)["positives_end_of_season"].item()
    assert eos == rows.filter((pl.col("season") == 2006) & ~pl.col("is_interim")
                              & (pl.col("snapshot") == "end_of_season"))["y"].sum()  # fmt: skip
    counts = dict.fromkeys(
        ["departures_joined", "departures_unverified", "departures_date_imputed",
         "departures_blank_type", "rows_in", "dropped_last_reg_week", "dropped_gone",
         "dropped_blank_type", "rows_out", "interim_rows", "censored_rows",
         "positive_outside_window"], 0)  # fmt: skip
    paths = hr.write_outputs(res, counts, "suggested", tmp_path / "p", tmp_path / "p")
    text = paths["report"].read_text()
    assert text.startswith("# Hot-Seat") and hr.PROVISIONAL in text
    assert pl.read_csv(paths["metrics"]).height == res.metrics.height
    paths = hr.write_outputs(res, counts, "verified", tmp_path / "r", tmp_path / "d")
    assert "PROVISIONAL" not in paths["report"].read_text()
    assert paths["predictions"].parent == tmp_path / "d"


@pytest.mark.realdata
def test_real_notebook_executes():
    import nbformat
    from nbclient import NotebookClient

    from twm.config import ROOT
    from twm.modules.hot_seat import backtest_report as hr

    dirs = (ROOT / "reports/hot_seat", ROOT / "data/hot_seat/provisional")
    if not any((d / hr.FILES["metrics"]).exists() for d in dirs):
        pytest.skip("run `uv run twm hotseat backtest --labels ...` first")
    nb = nbformat.read(ROOT / "notebooks" / "04_hot_seat.ipynb", as_version=4)
    NotebookClient(nb, timeout=300, kernel_name="python3",
                   resources={"metadata": {"path": str(ROOT / "notebooks")}}).execute()  # fmt: skip
    errors = [o for c in nb.cells if c.cell_type == "code" for o in c.get("outputs", [])
              if o.output_type == "error"]  # fmt: skip
    assert not errors


def _final_and_tuning(run: hb.ModelRun) -> dict[int, tuple[dict, dict | None]]:
    return {fr.fold.test_season: (fr.model.refit_params,
                                  fr.trials[0].refit_params if fr.trials else None)
            for fr in run.result.folds}  # fmt: skip


def test_inner_walk_forward_l2_c_from_earlier_training_seasons_only():
    rows = _rows()
    run = hb.run_model(hm.SPECS["logit"], rows, test_seasons=(2004, 2006, 2008))
    got = _final_and_tuning(run)
    assert all(len(fr.trials) == 1 for fr in run.result.folds)  # one-point harness grid
    for final, tuning in got.values():
        for p in (final, tuning):
            assert p["l1_ratio"] == 0.0 and p["C"] in hm.L2_GRID
    # last 4 training seasons with an earlier one; the tuning fit holds out the validation season
    assert got[2008][0]["inner_seasons"] == "2004,2005,2006,2007"
    assert got[2008][1]["inner_seasons"] == "2003,2004,2005,2006"
    assert (got[2006][0]["inner_seasons"], got[2006][1]["inner_seasons"]) == (
        "2003,2004,2005", "2003,2004")  # fmt: skip
    # 2004 trains on 2002-2003: one inner season only -> the documented default
    assert got[2004][0]["C"] == hm.DEFAULT_C and got[2004][0]["c_source"].startswith("default")
    # no test-season data in any choice: scrambling 2008 changes nothing in the 2008 fold
    rng = np.random.default_rng(1)
    late = pl.col("season") == 2008
    scrambled = rows.with_columns(
        pl.when(late).then(1 - pl.col("y")).otherwise(pl.col("y")).alias("y"),
        pl.when(late).then(pl.lit(rng.normal())).otherwise(pl.col("wins_vs_expected"))
        .alias("wins_vs_expected"),
    )  # fmt: skip
    run2 = hb.run_model(hm.SPECS["logit"], scrambled, test_seasons=(2008,))
    fr, fr2 = run.result.folds[-1], run2.result.folds[0]
    assert fr2.model.refit_params == fr.model.refit_params
    assert hm.coefficients(fr2.model) == hm.coefficients(fr.model)


def test_inner_choice_is_the_summed_season_log_loss():
    train = _rows(seasons=range(2002, 2006)).filter(~pl.col("is_interim")).sort(hm.KEYS)
    x, y = train.select(hm.FEATURES), train["y"].to_numpy()
    seasons = train["season"].to_numpy()
    est = hm.InnerCvLogit("logit")
    c, how = est.choose_c(x, y, seasons)
    want = {}
    for cand in hm.L2_GRID:
        want[cand] = 0.0
        for v in (2003, 2004, 2005):
            m = hm.LogitEstimator().fit(x.filter(pl.Series(seasons < v)), y[seasons < v],
                                        {"C": cand, "l1_ratio": 0.0})  # fmt: skip
            p = m.predict(x.filter(pl.Series(seasons == v)))
            want[cand] += hm.season_log_loss(y[seasons == v].astype(float), p)
    assert how["inner_log_loss"] == pytest.approx(want)
    assert c == min(want, key=want.get) and how["inner_seasons"] == "2003,2004,2005"
    # unbound, or rows that are not a season prefix of the bound frame: seasons unknown
    assert est.fit(x, y, est.default_params).refit_params["c_source"] == "default (seasons unknown)"
    est.bind(train, hm.FEATURES, hm.KEYS)
    assert est.seasons_of(x) is not None and est.seasons_of(x.reverse()) is None
