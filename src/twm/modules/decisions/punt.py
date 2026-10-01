"""Punt sub-model: where a punt from here really leaves the ball (PROJECT_SPEC 8.4 item 2, G2).

**Rows**: every ``punt`` play of ``fact_play`` (fake punts are pass/run plays: the go model;
``no_play`` rows are dropped: the punt was wiped out and re-kicked). The result of each punt is
read from the next snap of the same game and half (:func:`twm.modules.decisions.submodels.
add_next_snap`), so touchbacks, fair catches, returns, penalties on the return, muffs and
blocks are all whatever they really were:

- ``receiving``: the receiving team's first snap, at ``spot`` = its ``yardline_100``;
- ``kicking``: the kicking team has the ball again (a muff or a blocked punt it recovered, a
  penalty that gave it a first down), at ``spot``;
- ``return_td``: the receiving team scored on the play; ``kicking_td``: the kicking team did;
- ``other`` (dropped from the distribution, counted): the half or the game ended, a safety.

**Distribution** (:func:`distribution`): for a punting yardline p, the outcomes of the training
punts from nearby yardlines, weighted by a Gaussian kernel in the yardline (``bandwidth``
yards; punts more than 3 bandwidths away get no weight), using the last ``window`` training
seasons ("by era": punting keeps improving). (window, bandwidth) is chosen per fold on the
validation season by the mean log score of the observed result in 5-yard bins
(:func:`log_score`), like any hyperparameter.

**Expected WP** (:func:`expected_wp`): the kicking team's win probability averaged over that
distribution, each result turned into a state for G1's :func:`twm.modules.decisions.wp.wp`:
the receiving team's 1st and 10 at its spot (WP = 1 - its WP), the kicking team's 1st and 10
at its spot, or after a touchdown (+7, extra point assumed good) the other team's 1st and 10
at its own 25 (a kickoff approximated by the touchback spot). The clock moves by the training
punts' median snap-to-snap time.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import polars as pl

from twm.modules.decisions import submodels as sm

MODEL = "punt_kernel"
OUTCOMES = ("receiving", "kicking", "return_td", "kicking_td")
FILTERS = ("not_a_punt", "missing_state", "other_result")
WINDOWS = (3, 6, None)  # last N training seasons; None = all
BANDWIDTHS = (1.5, 3.0, 5.0)
DEFAULT = {"window": 6, "bandwidth": 3.0}
BIN = 5  # yards per bin of the log score
FLOOR = 1e-4  # smallest probability a bin gets in the log score
AFTER_TD_YARDLINE = 75  # the other team's own 25 after a touchdown and kickoff
TD_POINTS = 7


def build_rows(plays: pl.DataFrame) -> tuple[pl.DataFrame, dict[str, int]]:
    """One row per punt with its result (``outcome``, ``spot``) and the drop counts."""
    rows, drops = sm.count_filters(plays, [
        ("not_a_punt", pl.col("play_type") == "punt"),
        ("missing_state", pl.col("yardline_100").is_between(1, 99)
         & pl.col("posteam").is_not_null() & pl.col("defteam").is_not_null()),
    ])  # fmt: skip
    td = pl.col("touchdown") == 1
    same = (pl.col("n_game_half") == pl.col("game_half")).fill_null(False)
    outcome = (
        pl.when(td & (pl.col("td_team") == pl.col("defteam"))).then(pl.lit("return_td"))
        .when(td & (pl.col("td_team") == pl.col("posteam"))).then(pl.lit("kicking_td"))
        .when(same & (pl.col("n_posteam") == pl.col("defteam"))).then(pl.lit("receiving"))
        .when(same & (pl.col("n_posteam") == pl.col("posteam"))).then(pl.lit("kicking"))
        .otherwise(pl.lit("other"))
    )  # fmt: skip
    rows = rows.with_columns(outcome.alias("outcome")).with_columns(
        pl.when(pl.col("outcome").is_in(["receiving", "kicking"]))
        .then(pl.col("n_yardline_100")).cast(pl.Int32).alias("spot"),
        (pl.col("game_seconds_remaining") - pl.col("n_game_seconds_remaining"))
        .alias("runoff"),
    )  # fmt: skip
    before = rows.height
    rows = rows.filter(pl.col("outcome") != "other")
    drops["other_result"] = before - rows.height
    rows = wp_states(rows, plays)
    cols = [*sm.KEYS, "week", "yardline_100", "outcome", "spot", "runoff", *sm.STATE_INPUTS]
    return rows.select(list(dict.fromkeys(cols))).sort(list(sm.KEYS)), drops


def wp_states(rows: pl.DataFrame, plays: pl.DataFrame) -> pl.DataFrame:
    """G1's state inputs for ``rows`` (a subset of ``plays``): half, who gets the second-half
    kickoff (from the game's opening kickoff in ``plays``), home, the spread (NULL timeouts or
    spread stay NULL: such punts are skipped by the expected-WP check)."""
    from twm.modules.decisions import wp_data

    return wp_data.add_features(rows.join(wp_data.opening_kickers(plays), on="game_id",
                                          how="left"))  # fmt: skip


# cells of a distribution: receiving at 1..99, kicking at 1..99, return_td, kicking_td
N_CELLS = 99 + 99 + 2


def _cells(rows: pl.DataFrame) -> np.ndarray:
    o, s = rows.get_column("outcome").to_list(), rows.get_column("spot").to_list()
    base = {"receiving": -1, "kicking": 98}
    return np.array([base[k] + int(v) if k in base else (198 if k == "return_td" else 199)
                     for k, v in zip(o, s, strict=True)], dtype=np.int64)  # fmt: skip


def table(rows: pl.DataFrame, bandwidth: float) -> np.ndarray:
    """(99, N_CELLS): row p-1 = the smoothed result distribution of a punt from yardline p.
    A yardline with no training punt within 3 bandwidths gets the pooled distribution."""
    counts = np.zeros((99, N_CELLS))
    if rows.height:
        np.add.at(counts, (rows.get_column("yardline_100").to_numpy() - 1, _cells(rows)), 1.0)
    p = np.arange(1, 100, dtype=np.float64)
    if bandwidth > 0:
        d = (p[:, None] - p[None, :]) / bandwidth
        k = np.where(np.abs(d) <= 3.0, np.exp(-0.5 * d * d), 0.0)
    else:
        k = np.eye(99)
    dist = k @ counts
    tot = dist.sum(axis=1, keepdims=True)
    pooled = counts.sum(axis=0)
    pooled = pooled / pooled.sum() if pooled.sum() > 0 else np.full(N_CELLS, 1.0 / N_CELLS)
    return np.where(tot > 0, dist / np.where(tot > 0, tot, 1.0), pooled[None, :])


def _bins() -> np.ndarray:
    """Cell -> 5-yard bin index (receiving 0-19, kicking 20-39, return_td 40, kicking_td 41)."""
    spot = np.arange(1, 100)
    return np.concatenate([(spot - 1) // BIN, 20 + (spot - 1) // BIN, [40, 41]])


def log_score(rows: pl.DataFrame, dist: np.ndarray) -> np.ndarray:
    """Per punt: log of the probability its real result's 5-yard bin had (floored)."""
    bins = _bins()
    binned = np.zeros((99, 42))
    np.add.at(binned.T, bins, dist.T)
    p = rows.get_column("yardline_100").to_numpy() - 1
    got = binned[p, bins[_cells(rows)]]
    return np.log(np.maximum(got, FLOOR))


def _window(train: pl.DataFrame, seasons: tuple[int, ...], window: int | None) -> pl.DataFrame:
    use = seasons if window is None else seasons[-window:]
    return train.filter(pl.col("season").is_in(list(use)))


def summaries(rows: pl.DataFrame, dist: np.ndarray) -> pl.DataFrame:
    """Per punt: predicted P(touchback: receiving at its 20), P(return TD), P(kicking team
    keeps the ball), the expected receiving spot given a receiving result, and the log score."""
    p = rows.get_column("yardline_100").to_numpy() - 1
    d = dist[p]
    recv = d[:, :99]
    spots = np.arange(1, 100, dtype=np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean_spot = (recv * spots).sum(axis=1) / recv.sum(axis=1)
    return rows.select(sm.KEYS).with_columns(
        pl.Series("p_touchback", recv[:, 79]), pl.Series("p_return_td", d[:, 198]),
        pl.Series("p_kicking", d[:, 99:198].sum(axis=1) + d[:, 199]),
        pl.Series("pred_spot", mean_spot), pl.Series("log_score", log_score(rows, dist)),
    )  # fmt: skip


HASH_COLUMNS = ("week", "yardline_100", "outcome", "spot", "runoff")


def fit_fold(rows: pl.DataFrame, season: int, *, progress=print) -> sm.FoldOutput:
    """Choose (window, bandwidth) on the validation season with tables built from the seasons
    before it, then build the final table from the training seasons (< ``season``) and score
    every punt of ``season``. Refuses to build any table from the test season or later."""
    from twm.backtest.walkforward import assert_before, plan_folds
    from twm.predictions import model_version

    t0 = time.perf_counter()
    fold = plan_folds(rows.get_column("season").unique().to_list(), [season])[0]
    train = rows.filter(pl.col("season") < season).sort(list(sm.KEYS))
    assert_before(train, season, what="build the punt table")
    test = rows.filter(pl.col("season") == season).sort(list(sm.KEYS))
    trials: list[dict[str, Any]] = []
    params = dict(DEFAULT)
    if not fold.thin:
        val = train.filter(pl.col("season") == fold.val_season)
        for w in WINDOWS:
            for bw in BANDWIDTHS:
                tune = _window(train, fold.tune_seasons, w)
                assert_before(tune, fold.val_season or season, what="tune the punt table")
                score = float(log_score(val, table(tune, bw)).mean())
                trials.append({"params": {"window": w, "bandwidth": bw}, "val_log_score": score})
                progress(f"  {MODEL} {season}: window {w or 'all'}, bandwidth {bw}: validation "
                         f"log score {score:.4f} [{time.perf_counter() - t0:.0f} s]")  # fmt: skip
        params = max(trials, key=lambda t: t["val_log_score"])["params"]  # first best wins
    used = _window(train, fold.train_seasons, params["window"])
    dist = table(used, params["bandwidth"])
    runoff = used.filter(pl.col("outcome") == "receiving").get_column("runoff").drop_nulls()
    extras = {"table": dist, "runoff_seconds": float(runoff.median()) if runoff.len() else 0.0,
              "n_punts": used.height,
              "seasons_used": sorted(set(used.get_column("season")))}  # fmt: skip
    dataset_hash = sm.training_hash(train, HASH_COLUMNS)
    model = sm.SubModel(
        version="", model=MODEL, test_season=season, train_seasons=fold.train_seasons,
        val_season=fold.val_season, features=("yardline_100",), params=dict(params),
        dataset_hash=dataset_hash, extras=extras, calibration="none (a distribution)",
    )  # fmt: skip
    model.version = model_version(
        module=sm.MODULE, model=MODEL, label="punt_result", features=("yardline_100",),
        params={**params, "bin": BIN, "floor": FLOOR}, training_seasons=fold.train_seasons,
        test_season=season, dataset_hash=dataset_hash,
    )  # fmt: skip
    preds = summaries(test, dist).with_columns(
        pl.Series("log_score_raw", log_score(test, table(train, 0.0)))
    )
    summary = {
        "test_season": season, "fold": fold.describe(), "version": model.version,
        "model": MODEL, "params": params, "trials": trials, "n_train": used.height,
        "n_test": test.height, "dataset_hash": dataset_hash, "format": sm.FILE_FORMAT,
        "runoff_seconds": extras["runoff_seconds"], "calibration": model.calibration,
        "seconds": round(time.perf_counter() - t0, 1),
    }  # fmt: skip
    progress(f"  {MODEL} {season}: window {params['window'] or 'all'}, bandwidth "
             f"{params['bandwidth']} ({used.height:,} punts) "
             f"[{summary['seconds']:.0f} s]")  # fmt: skip
    return sm.FoldOutput(model, preds, summary)


def _check(model: sm.SubModel) -> np.ndarray:
    if model.model != MODEL or "table" not in model.extras:
        raise sm.SubModelError(f"{model.version} is not a punt model")
    return np.asarray(model.extras["table"])


def distribution(states: pl.DataFrame, model: sm.SubModel) -> pl.DataFrame:
    """Per state (``row`` = its position in ``states``): every possible result of a punt from
    its ``yardline_100`` with ``prob`` > 0 (``outcome`` in :data:`OUTCOMES`; ``spot`` = the
    yardline_100 of the team that has the ball next, NULL after a touchdown). Each row's
    probabilities sum to 1."""
    dist = _check(model)
    y = states.get_column("yardline_100")
    if y.null_count() or not y.is_between(1, 99).all():
        raise sm.SubModelError("punt states need a yardline_100 of 1-99")
    d = dist[y.to_numpy() - 1]
    rows, cells = np.nonzero(d > 0)
    kind = np.where(cells < 99, 0, np.where(cells < 198, 1, cells - 196))
    spot = np.where(cells < 99, cells + 1, np.where(cells < 198, cells - 98, -1))
    return pl.DataFrame({
        "row": rows.astype(np.uint32),
        "outcome": np.array(OUTCOMES)[kind],
        "spot": pl.Series(spot.astype(np.int32)).replace(-1, None),
        "prob": d[rows, cells],
    })  # fmt: skip


def outcome_wp(results: pl.DataFrame, states: pl.DataFrame, runoff: float, wp_model: Any):
    """The kicking team's WP after each result: ``results`` has ``row`` (a position in
    ``states``), ``outcome``, ``spot`` and ``prob``; ``states`` the punting team's state before
    the snap (G1's inputs :data:`submodels.STATE_INPUTS`). Returns ``results`` + ``wp_kick``
    (in the order: receiving, kicking, return_td, kicking_td results)."""
    from twm.modules.decisions import wp as wpm

    missing = [c for c in sm.STATE_INPUTS if c not in states.columns]
    if missing:
        raise sm.SubModelError(f"states lack columns {missing}")
    base = (states.select(sm.STATE_INPUTS).with_row_index("row")
            .with_columns(sm.clock_after(runoff)))  # fmt: skip
    j = results.join(base, on="row", how="left")
    spot, o, sd = pl.col("spot"), pl.col("outcome"), pl.col("score_differential")
    after_td = pl.lit(AFTER_TD_YARDLINE)
    parts = [
        (sm.first_down_at(sm.other_side(j.filter(o == "receiving")), spot), True),
        (sm.first_down_at(j.filter(o == "kicking"), spot), False),
        (sm.first_down_at(j.filter(o == "return_td").with_columns(sd - TD_POINTS), after_td),
         False),
        (sm.first_down_at(sm.other_side(j.filter(o == "kicking_td").with_columns(
            sd + TD_POINTS)), after_td), True),
    ]  # fmt: skip
    out = []
    for frame, flip in parts:
        if frame.height:
            w = wpm.wp(frame, wp_model)
            out.append(frame.select(results.columns).with_columns(
                pl.Series("wp_kick", 1.0 - w if flip else w)))  # fmt: skip
    if not out:
        return results.with_columns(pl.lit(None, pl.Float64).alias("wp_kick")).clear()
    return pl.concat(out)


def expected_wp(states: pl.DataFrame, model: sm.SubModel, wp_model: Any) -> np.ndarray:
    """The kicking team's win probability after punting, averaged over :func:`distribution`
    (one per row of ``states``: the punting team's state before the snap, with G1's state
    inputs :data:`submodels.STATE_INPUTS` and ``yardline_100``)."""
    d = distribution(states, model)
    w = outcome_wp(d, states, float(model.extras.get("runoff_seconds", 0.0)), wp_model)
    out = w.group_by("row").agg((pl.col("prob") * pl.col("wp_kick")).sum())
    full = pl.DataFrame({"row": np.arange(states.height, dtype=np.uint32)})
    return full.join(out, on="row", how="left").sort("row").get_column("prob").to_numpy()
