"""G1: the own win-probability model (twm.modules.decisions.wp_data, wp, wp_report).

Offline, on synthetic plays: filters and the label, the receives-second-half-kickoff
derivation, monotone constraints, the walk-forward guard, wp() on hypothetical states,
determinism, save/load, the isotonic rule and the report (the notebook's functions in script
form). The notebook itself runs against the real backtest with ``-m realdata``.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from twm.backtest.walkforward import TestSeasonInTrainingError, fit_fold, plan_folds
from twm.modules.decisions import wp
from twm.modules.decisions import wp_data as wd


def synth_world(
    seasons: tuple[int, ...] = (2010,), games: int = 4, plays: int = 40, seed: int = 3
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Raw fact_play / fact_game-shaped rows: per game an opening kickoff (HOME kicks: the away
    team receives), ``plays`` scrimmage plays whose score follows the final margin, then a
    third-quarter kickoff. The home team wins when its margin (spread + noise) is positive."""
    rng = np.random.default_rng(seed)
    prow, grow = [], []
    for season in seasons:
        for g in range(games):
            gid, home, away = f"{season}_{g:02d}_A{g}_H{g}", f"H{g}", f"A{g}"
            spread = float(rng.normal(0, 4))
            margin = int(round(spread + rng.normal(0, 10))) or 1
            grow.append((gid, spread, margin, "Home"))
            base = dict(game_id=gid, season=season, week=1 + g % 17, season_type="REG",
                        home_team=home, away_team=away, fixed_drive=1, wp=0.5,
                        vegas_wp=0.5)  # fmt: skip
            prow.append({**base, "play_id": 1, "qtr": 1, "game_half": "Half1", "down": None,
                         "play_type": "kickoff", "kickoff_attempt": 1, "posteam": away,
                         "defteam": home})  # fmt: skip
            for i in range(plays):
                frac = i / plays
                gsr = int(3600 * (1 - frac))
                pos_home = bool(rng.integers(0, 2))
                diff_home = int(round(margin * frac + rng.normal(0, 3)))
                p_home = 1 / (1 + np.exp(-(spread + diff_home) / 6))
                prow.append({**base, "play_id": 10 + i, "qtr": min(4, 1 + int(frac * 4)),
                    "game_half": "Half1" if gsr > 1800 else "Half2",
                    "down": int(rng.integers(1, 5)),
                    "play_type": "pass" if i % 2 else "run", "kickoff_attempt": 0,
                    "posteam": home if pos_home else away, "defteam": away if pos_home else home,
                    "score_differential": diff_home if pos_home else -diff_home,
                    "game_seconds_remaining": gsr, "half_seconds_remaining": gsr % 1800 or 1800,
                    "ydstogo": int(rng.integers(1, 15)), "yardline_100": int(rng.integers(1, 100)),
                    "posteam_timeouts_remaining": 3, "defteam_timeouts_remaining": 3,
                    "fixed_drive": 1 + i // 5,
                    "wp": p_home if pos_home else 1 - p_home,
                    "vegas_wp": p_home if pos_home else 1 - p_home})  # fmt: skip
            prow.append({**base, "play_id": 500, "qtr": 3, "game_half": "Half2", "down": None,
                         "play_type": "kickoff", "kickoff_attempt": 1, "posteam": home,
                         "defteam": away})  # fmt: skip
    plays_df = pl.DataFrame(prow, infer_schema_length=None).select(wd._PLAY_COLS)
    games_df = pl.DataFrame(grow, schema=list(wd._GAME_COLS), orient="row")
    return plays_df, games_df


# --------------------------------------------------------------------------------------
# Rows, filters and the label
# --------------------------------------------------------------------------------------


def _edit(plays: pl.DataFrame, play_id: int, **values) -> pl.DataFrame:
    return plays.with_columns(
        pl.when(pl.col("play_id") == play_id).then(pl.lit(v)).otherwise(pl.col(c)).alias(c)
        for c, v in values.items()
    )


def test_filters_drop_and_count_every_bad_row():
    plays, games = synth_world(games=3)
    g0, g1, g2 = games.get_column("game_id").to_list()
    plays = pl.concat([
        plays,  # a no_play row (penalty: the down is replayed) and a timeout row
        plays.filter((pl.col("game_id") == g0) & (pl.col("play_id") == 10))
        .with_columns(pl.lit(900).alias("play_id"), pl.lit("no_play").alias("play_type")),
        plays.filter((pl.col("game_id") == g0) & (pl.col("play_id") == 10))
        .with_columns(pl.lit(901).alias("play_id"), pl.lit(None, pl.String).alias("play_type"),
                      pl.lit(None, pl.Int64).alias("down")),
    ], how="vertical_relaxed")  # fmt: skip
    bad = (pl.col("game_id") == g0) & pl.col("play_id").is_in([11, 12, 13])

    def put(play_id: int, col: str, value) -> pl.Expr:
        hit = bad & (pl.col("play_id") == play_id)
        return pl.when(hit).then(pl.lit(value)).otherwise(pl.col(col)).alias(col)

    plays = plays.with_columns(  # distance 0, a NULL timeout count, a team not in the game
        put(11, "ydstogo", 0), put(12, "posteam_timeouts_remaining", None),
        put(13, "posteam", "XXX"),
    )  # fmt: skip
    games = games.with_columns(
        pl.when(pl.col("game_id") == g1).then(None).when(pl.col("game_id") == g2).then(0)
        .otherwise(pl.col("result")).alias("result")
    )  # fmt: skip
    s = wd.build_states(plays, games)
    n = 40  # scrimmage plays per game
    # not scrimmage: 2 kickoffs in each played game (g0, the tie g2) + the two extra rows
    assert s.drops == {"unplayed": n + 2, "not_scrimmage": 2 + 2 + 2, "missing_state": 3, "tie": n}
    assert s.n_tie_games == 1 and s.n_candidates == plays.height
    assert s.rows.height == n - 3
    assert s.rows.get_column("game_id").unique().to_list() == [g0]
    assert s.rows.select(wd.FEATURES).null_count().sum_horizontal().item() == 0
    assert s.rows.columns[:3] == list(wd.KEYS) and s.rows.equals(s.rows.sort(list(wd.KEYS)))


def test_label_spread_and_home_follow_the_possession_team():
    plays, games = synth_world(games=1)
    games = games.with_columns(pl.lit(3.5).alias("spread_line"), pl.lit(-7).alias("result"))
    s = wd.build_states(plays, games).rows
    home = s.filter(pl.col("posteam") == pl.col("home_team"))
    away = s.filter(pl.col("posteam") != pl.col("home_team"))
    assert home.height and away.height
    assert home.get_column("posteam_wins").to_list() == [0] * home.height  # home lost by 7
    assert away.get_column("posteam_wins").to_list() == [1] * away.height
    assert set(home.get_column("posteam_spread").to_list()) == {3.5}  # home favored by 3.5
    assert set(away.get_column("posteam_spread").to_list()) == {-3.5}
    assert set(home.get_column("posteam_is_home").to_list()) == {1.0}
    assert set(away.get_column("posteam_is_home").to_list()) == {0.0}
    neutral = wd.build_states(plays, games.with_columns(pl.lit("Neutral").alias("location")))
    assert set(neutral.rows.get_column("posteam_is_home").to_list()) == {0.5}


def test_era_flags_and_time_interactions():
    plays, games = synth_world(seasons=(2014, 2015, 2022, 2023), games=1)
    s = wd.build_states(plays, games).rows
    by = s.group_by("season").agg(pl.col("era_pat_2015").max(), pl.col("era_kickoff_2023").max())
    assert by.sort("season").rows() == [(2014, 0, 0), (2015, 1, 0), (2022, 1, 0), (2023, 1, 1)]
    r = s.row(0, named=True)
    decay = np.exp(-4 * (3600 - r["game_seconds_remaining"]) / 3600)
    assert r["spread_time"] == pytest.approx(r["posteam_spread"] * decay)
    assert r["diff_time_ratio"] == pytest.approx(r["score_differential"] / decay)


def test_receives_second_half_kickoff_comes_from_the_opening_kickoff():
    plays, games = synth_world(games=1)
    # the home team kicks off (away receives) -> home receives the second-half kickoff
    s = wd.build_states(plays, games).rows
    first = s.filter(pl.col("half_number") == 1)
    expect = (first.get_column("posteam") == first.get_column("home_team")).cast(pl.Int8)
    assert first.get_column("receives_2h_kickoff").to_list() == expect.to_list()
    assert set(s.filter(pl.col("half_number") == 2).get_column("receives_2h_kickoff")) == {0}
    # the flag uses the OPENING kickoff only (point-in-time): the third-quarter kickoff row
    # says nothing in the first half, and a game without an opening kickoff gets NULL
    swapped = _edit(plays, 1, posteam="H0", defteam="A0")  # away kicks the opening kickoff
    s2 = wd.build_states(swapped, games).rows.filter(pl.col("half_number") == 1)
    assert s2.get_column("receives_2h_kickoff").to_list() == (1 - expect).to_list()
    none = wd.build_states(plays.filter(pl.col("play_id") != 1), games).rows
    assert none.filter(pl.col("half_number") == 1).get_column("receives_2h_kickoff").null_count()
    assert wd.opening_kickers(plays).rows() == [(games.item(0, "game_id"), "H0")]


def test_overtime_counts_as_fully_elapsed():
    f = pl.DataFrame({"posteam_spread": [6.0, 6.0], "score_differential": [3, 3],
                      "game_seconds_remaining": [600, 600], "half_number": [2, 3]})  # fmt: skip
    out = f.with_columns(wd.time_interactions())
    assert out.get_column("spread_time").to_list()[1] == pytest.approx(6.0 * np.exp(-4.0))
    assert out.get_column("spread_time").to_list()[0] > out.get_column("spread_time").to_list()[1]


# --------------------------------------------------------------------------------------
# The model
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rows() -> pl.DataFrame:
    plays, games = synth_world(seasons=(2003, 2004, 2005, 2006), games=40, plays=40)
    return wd.build_states(plays, games).rows


SMALL = {"num_leaves": 7, "min_child_samples": 20, "n_estimators": 40}


def test_monotone_constraints_hold(rows):
    m = wp.WpEstimator().fit(rows.select(wd.FEATURES), rows.get_column(wd.LABEL).to_numpy(), SMALL)
    base = rows.select(wd.FEATURES).head(50)
    for col, sign in wp.MONOTONE.items():
        lo, hi = rows.get_column(col).min(), rows.get_column(col).max()
        grid = np.linspace(float(lo), float(hi), 7)
        preds = np.stack([m.predict(base.with_columns(pl.lit(v).cast(base.schema[col]).alias(col)))
                          for v in grid])  # fmt: skip
        steps = np.diff(preds, axis=0) * sign
        assert (steps >= -1e-12).all(), col


@pytest.fixture
def small_grid(monkeypatch):
    monkeypatch.setattr(wp, "GRID", ({"num_leaves": 7, "min_child_samples": 20,
                                      "n_estimators": 200},))  # fmt: skip


def test_walk_forward_refuses_the_test_season(rows):
    fold = plan_folds(rows.get_column("season").unique().to_list(), [2006])[0]
    assert fold.train_seasons == (2003, 2004, 2005) and fold.val_season == 2005
    with pytest.raises(TestSeasonInTrainingError):
        fit_fold(rows, fold, estimator=wp.WpEstimator(), features=wd.FEATURES, label=wd.LABEL,
                 module=wp.MODULE, keys=wd.KEYS, tune_metric=lambda v: 0.0)  # fmt: skip


def test_run_fold_learns_only_from_earlier_seasons_and_is_deterministic(rows, small_grid):
    a = wp.run_fold(rows, 2005, progress=lambda m: None)
    b = wp.run_fold(rows, 2005, progress=lambda m: None)
    assert a.model.train_seasons == (2003, 2004) and a.model.val_season == 2004
    assert a.predictions.get_column("season").unique().to_list() == [2005]
    assert a.predictions.equals(b.predictions)  # bit-identical
    assert a.model.version == b.model.version and a.summary == b.summary | {
        "seconds": a.summary["seconds"]}  # fmt: skip
    # later seasons in the input change nothing
    c = wp.run_fold(rows.filter(pl.col("season") <= 2005), 2005, progress=lambda m: None)
    assert c.predictions.equals(a.predictions) and c.model.version == a.model.version
    assert a.summary["calibration"].split()[0] in ("isotonic", "none")


def test_wp_answers_hypothetical_states(rows, small_grid):
    model = wp.run_fold(rows, 2006, progress=lambda m: None).model
    state = {"season": 2006, "game_seconds_remaining": 300, "half_seconds_remaining": 300,
             "half_number": 2, "down": 1, "ydstogo": 10, "yardline_100": 75,
             "posteam_timeouts_remaining": 3, "defteam_timeouts_remaining": 3,
             "receives_2h_kickoff": 0, "posteam_is_home": 1.0, "posteam_spread": 0.0}  # fmt: skip
    leads = pl.DataFrame([{**state, "score_differential": d} for d in (-14, -7, 0, 7, 14)])
    p = wp.wp(leads, model)
    assert p.shape == (5,) and ((p > 0) & (p < 1)).all()
    assert (np.diff(p) >= 0).all() and p[-1] > p[0]  # monotone in the lead
    full = wp.complete_states(leads)
    assert np.array_equal(wp.wp(full.select(model.features), model), p)
    with pytest.raises(wp.WpModelError, match="lack columns"):
        wp.wp(leads.drop("down"), model)
    with pytest.raises(wp.WpModelError, match="NULLs"):
        wp.wp(leads.with_columns(pl.lit(None, pl.Int64).alias("down")), model)
    with pytest.raises(wp.WpModelError, match="out of range"):
        wp.wp(leads.with_columns(pl.lit(5).alias("down")), model)


def test_save_and_load_give_identical_probabilities(rows, small_grid, tmp_path):
    out = wp.run_fold(rows, 2006, progress=lambda m: None)
    path = wp.save_fold(out, models_root=tmp_path / "m", out_dir=tmp_path / "bt")
    assert path.name == f"{out.model.version}.joblib"
    loaded = wp.load_fold_model(2006, models_root=tmp_path / "m", out_dir=tmp_path / "bt")
    # the model's own features (G1b: the chosen family adds drive_value and z_margin)
    x = rows.filter(pl.col("season") == 2006).select(out.model.features)
    assert np.array_equal(loaded.probability(x), out.model.probability(x))
    assert wp.fold_is_current(rows, 2006, tmp_path / "bt", tmp_path / "m")
    assert not wp.fold_is_current(rows.filter(pl.col("game_id") != rows.item(0, "game_id")),
                                  2006, tmp_path / "bt", tmp_path / "m")  # fmt: skip
    import joblib

    joblib.dump({"format": 999}, tmp_path / "bad.joblib")
    with pytest.raises(wp.WpModelError):
        wp.load_model(tmp_path / "bad.joblib")
    with pytest.raises(wp.WpModelError):
        wp.load_fold_model(2010, models_root=tmp_path / "m", out_dir=tmp_path / "bt")


def test_isotonic_is_kept_only_when_it_helps_out_of_sample():
    rng = np.random.default_rng(0)
    n = 20_000
    games = [f"g{i // 50:04d}" for i in range(n)]
    p = rng.uniform(0.05, 0.95, n)
    y = (rng.uniform(size=n) < p).astype(float)
    helps, raw_ll, iso_ll = wp.isotonic_helps(games, y, p)  # already calibrated: no help
    assert not helps and iso_ll >= raw_ll
    squashed = 0.5 + (p - 0.5) * 0.3  # badly under-confident scores: isotonic fixes them
    helps, raw_ll, iso_ll = wp.isotonic_helps(games, y, squashed)
    assert helps and iso_ll < raw_ll


# --------------------------------------------------------------------------------------
# Backtest driver and report (the notebook's functions, in script form)
# --------------------------------------------------------------------------------------


def test_backtest_and_report(rows, small_grid, tmp_path):
    from twm.modules.decisions import wp_report as wr

    seasons = (2005, 2006)
    kw = {"models_root": tmp_path / "m", "out_dir": tmp_path / "bt", "progress": lambda m: None}
    assert wp.run_backtest(rows, seasons, **kw) == [2005, 2006]
    assert wp.run_backtest(rows, seasons, **kw) == []  # reused: current
    assert wp.run_backtest(rows, [2006], force=True, **kw) == [2006]
    states = wd.States(rows, {f: 0 for f in wd.FILTERS}, rows.height, 0)
    with pytest.raises(wp.WpModelError, match="missing"):
        wr.load_backtest(states, (2004, 2005), out_dir=tmp_path / "bt")
    frame, summaries = wr.load_backtest(states, seasons, out_dir=tmp_path / "bt")
    assert frame.height == rows.filter(pl.col("season").is_in(seasons)).height
    assert [s["test_season"] for s in summaries] == list(seasons)
    paths = (tmp_path / "r.md", tmp_path / "r.csv")
    wr.write_report(states, seasons=seasons, out_dir=tmp_path / "bt", paths=paths,
                    progress=lambda m: None)  # fmt: skip
    md, csv = paths[0].read_text(), pl.read_csv(paths[1])
    headings = ("## Setup", "## Pooled 2005-2006", "## By era", "## Slices", "## Per season",
                "## Reliability", "## Do the era flags matter?",
                "## Feature importance")  # fmt: skip
    for heading in headings:
        assert heading in md, heading
    pooled = csv.filter((pl.col("scope") == "pooled") & (pl.col("metric") == "brier"))
    assert sorted(pooled.get_column("method").to_list()) == sorted(
        [*wr.METHODS, "own - nflfastr_wp", "own - nflfastr_vegas_wp"])  # fmt: skip
    own = pooled.filter(pl.col("method") == "own").row(0, named=True)
    assert own["lo"] <= own["value"] <= own["hi"] and own["n_blocks"] == 2
    p = frame.get_column("prob").to_numpy()
    y = frame.get_column(wd.LABEL).to_numpy()
    assert own["value"] == pytest.approx(float(np.mean((p - y) ** 2)), abs=1e-6)
    rel = csv.filter(pl.col("table") == "reliability")
    assert rel.filter(pl.col("metric") == "observed").height == len(wr.METHODS) * wr.N_BINS
    # deterministic: a second report is byte-identical
    paths2 = (tmp_path / "r2.md", tmp_path / "r2.csv")
    wr.write_report(states, seasons=seasons, out_dir=tmp_path / "bt", paths=paths2,
                    progress=lambda m: None)  # fmt: skip
    assert paths2[0].read_text() == paths[0].read_text()
    assert paths2[1].read_bytes() == paths[1].read_bytes()


def test_ece_matches_a_hand_computation():
    from twm.modules.decisions import wp_report as wr

    f = pl.DataFrame({"season": [1, 1, 2, 2], "prob": [0.05, 0.15, 0.05, 0.95],
                      wd.LABEL: [0, 1, 1, 1]})  # fmt: skip
    # bins: [0,.1): preds .05,.05 obs .5 -> gap .45 (2 plays); [.1,.2): .15 vs 1 -> .85;
    # [.9,1]: .95 vs 1 -> .05
    value, lo, hi = wr.ece(f, "prob")
    assert value == pytest.approx((2 * 0.45 + 0.85 + 0.05) / 4)
    assert lo is not None and lo <= value <= hi


def test_cli_rejects_seasons_outside_the_backtest():
    from typer.testing import CliRunner

    from twm.cli import app

    r = CliRunner().invoke(app, ["decisions", "wp-backtest", "--season", "2030"])
    assert r.exit_code == 2
    assert "wp-backtest" in CliRunner().invoke(app, ["decisions", "--help"]).output


# --------------------------------------------------------------------------------------
# Real data (opt-in: -m realdata; never downloads)
# --------------------------------------------------------------------------------------


def _real_db():
    from twm.config import settings

    db = settings().path("warehouse")
    if not db.exists():
        pytest.skip("run `uv run twm build` first")
    return db


@pytest.mark.realdata
def test_real_states_have_the_documented_shape():
    s = wd.load_states(_real_db(), (2023, 2024, 2025))
    r = s.rows
    assert r.height > 100_000 and set(s.drops) == set(wd.FILTERS)
    assert r.select(wd.FEATURES).null_count().sum_horizontal().item() == 0
    assert 0.48 < r.get_column(wd.LABEL).mean() < 0.53
    first = r.filter(pl.col("half_number") == 1).get_column("receives_2h_kickoff").mean()
    assert 0.4 < first < 0.6  # about half of first-half plays belong to the 2nd-half receiver
    assert r.select(pl.corr("posteam_spread", wd.LABEL)).item() > 0.3  # sign of the spread


@pytest.mark.realdata
def test_real_notebook_executes():
    _real_db()
    if not (wp.backtest_dir() / "fold_2025.json").exists():
        pytest.skip("run `uv run twm decisions wp-backtest` first")
    import nbformat
    from nbclient import NotebookClient

    from twm.config import ROOT

    nb = nbformat.read(ROOT / "notebooks" / "03_wp_model.ipynb", as_version=4)
    NotebookClient(nb, timeout=600, kernel_name="python3",
                   resources={"metadata": {"path": str(ROOT / "notebooks")}}).execute()  # fmt: skip
    errors = [o for c in nb.cells if c.cell_type == "code" for o in c.get("outputs", [])
              if o.output_type == "error"]  # fmt: skip
    assert not errors
