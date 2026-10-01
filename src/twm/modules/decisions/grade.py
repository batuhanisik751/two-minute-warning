"""Fourth-down and two-point grades with stored inputs (PROJECT_SPEC 8.4 items 2, 3, 5; G3).

**Fourth downs.** For every fourth down of season S that no exclusion rule removes
(:mod:`twm.modules.decisions.grade_inputs`), the win probability (WP, G1's own model) of each
option the offense had, all from the fold models that learned from seasons < S only:

- **go**: P(convert) (G2's conversion model, optionally Platt-recalibrated on fourth downs)
  x the expected WP after a conversion + (1 - P) x the expected WP after a failure, over the
  training seasons' distribution of where the ball ends up (G2 tables);
- **field goal** (only when the distance is within the longest field goal made before S):
  P(make) x WP(the opponent receives a kickoff 3 points behind) + (1 - P) x WP(the opponent's
  ball at the spot of the kick, or its 20);
- **punt** (only from yardlines punts come from): G2's punt result distribution, each result
  priced by the WP model.

A touchdown inside an option counts 7 points (extra point assumed good, as G2's punt); after
any score the other team starts at the kickoff spot measured point-in-time (grade_inputs).
**Recommendation** = the option with the highest WP; **WP lost** = WP(best) - WP(chosen).
A decision is **clear** (graded) only when the best option beats the second best by more than
``decisions.toss_up_margin`` (settings, 1.5 WP points); otherwise it is a **toss-up**
(reported, not graded).

**Two-point tries.** After every touchdown: WP(kick) = p_PAT x WP(+1) + (1 - p_PAT) x WP(+0)
and WP(go for two) = p_2pt x WP(+2) + (1 - p_2pt) x WP(+0), with the era's league rates
point-in-time (G2's tries rates) and the opponent receiving the kickoff; same margin.

**Stored inputs.** Every graded row keeps the play key, the full state, every input that is
not a model (kickoff spot, clock runoffs, option ranges, Platt parameters, the margin) and the
version of every model, so :func:`regrade` recomputes any stored row from those inputs alone
(models loaded by version) and :func:`verify` checks the result is bit-identical. Expected
values are exact sums (``math.fsum``), so a row graded alone or in a season batch is the same.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from twm.modules.decisions import conversion, fieldgoal, punt, tries
from twm.modules.decisions import grade_inputs as gi
from twm.modules.decisions import submodels as sm
from twm.modules.decisions import wp as wpm

KEYS = gi.KEYS
GRADE_FORMAT = 1  # bumped when the stored layout or the grading math changes
OPTIONS = ("go", "field_goal", "punt")
TRY_OPTIONS = ("kick", "two_point")
TD_POINTS = 7
FG_POINTS = 3
SUBMODELS = {"conversion": conversion.MODEL, "fieldgoal": fieldgoal.MODEL,
             "punt": punt.MODEL, "tries": tries.MODEL}  # fmt: skip


class GradeError(ValueError):
    """A grade cannot be computed or reproduced (a missing fold, a model of the wrong season)."""


@dataclass(frozen=True)
class Models:
    """The fold models that grade one season (each learned from earlier seasons only)."""

    wp: wpm.WpModel
    conversion: sm.SubModel
    fieldgoal: sm.SubModel
    punt: sm.SubModel
    tries: sm.SubModel

    def versions(self) -> dict[str, str]:
        return {f"{k}_version": getattr(self, k).version
                for k in ("wp", "conversion", "fieldgoal", "punt", "tries")}  # fmt: skip

    def check(self, season: int) -> None:
        """Refuse a model that is not season ``season``'s fold or that saw ``season``."""
        for name in ("wp", "conversion", "fieldgoal", "punt", "tries"):
            m = getattr(self, name)
            last = max(m.train_seasons, default=None)
            if m.test_season != season or (last is not None and last >= season):
                raise GradeError(f"{name} model {m.version} is the fold of {m.test_season} "
                                 f"(trained up to {last}), not of {season}")  # fmt: skip


def load_models(season: int, *, models_root: Path | None = None,
                folds_root: Path | None = None) -> Models:  # fmt: skip
    """Season ``season``'s fold of every model (the point-in-time choice: trained on seasons
    before it, tuned on the one before that), checked with :meth:`Models.check`."""
    try:
        w = wpm.load_fold_model(
            season,
            models_root=models_root,
            out_dir=None if folds_root is None else folds_root / "wp_backtest",
        )
        subs = {k: sm.load_fold_model(v, season, models_root=models_root,
                                      out_dir=None if folds_root is None
                                      else folds_root / "submodels" / v)
                for k, v in SUBMODELS.items()}  # fmt: skip
    except (wpm.WpModelError, sm.SubModelError) as e:
        raise GradeError(str(e)) from e
    models = Models(wp=w, **subs)
    models.check(season)
    return models


def models_by_version(versions: Mapping[str, str], *, models_root: Path | None = None) -> Models:
    """The models a stored row names (``<name>_version`` -> ``models/decisions/<v>.joblib``)."""
    root = models_root if models_root is not None else wpm.models_dir()
    try:
        w = wpm.load_model(root / f"{versions['wp_version']}.joblib")
        subs = {k: sm.load_model(root / f"{versions[f'{k}_version']}.joblib") for k in SUBMODELS}
    except (wpm.WpModelError, sm.SubModelError, KeyError) as e:
        raise GradeError(f"cannot load the stored row's models: {e}") from e
    return Models(wp=w, **subs)


# --------------------------------------------------------------------------------------
# Hypothetical states for G1's wp()
# --------------------------------------------------------------------------------------


def _clock(seconds: pl.Expr) -> list[pl.Expr]:
    """The clock ``seconds`` later (per row; never below 0)."""
    return [
        (pl.col(c).cast(pl.Float64) - seconds).clip(lower_bound=0.0).alias(c)
        for c in ("game_seconds_remaining", "half_seconds_remaining")
    ]


def _base(inputs: pl.DataFrame) -> pl.DataFrame:
    """The WP state of each input row (``row`` = its position) plus the columns the
    hypothetical states need; derived WP features are recomputed by wp() from these."""
    keep = [*gi.WP_STATE, "kick_yardline_100", "kick_runoff"]
    return inputs.select(keep).with_row_index("row")


def _kickoff_to_other(frame: pl.DataFrame, points: pl.Expr | int) -> pl.DataFrame:
    """The team with the ball scores ``points``; the other team then has a 1st and 10 at the
    kickoff spot, ``kick_runoff`` seconds later. WP of the scoring team = 1 - wp(this)."""
    f = frame.with_columns((pl.col("score_differential") + points).alias("score_differential"))
    f = f.with_columns(_clock(pl.col("kick_runoff")))
    return sm.first_down_at(sm.other_side(f), pl.col("kick_yardline_100"))


def _wp(frame: pl.DataFrame, model: wpm.WpModel) -> np.ndarray:
    if frame.height == 0:
        return np.zeros(0)
    return wpm.wp(frame.select(gi.WP_STATE), model)


def _expect(n: int, row: np.ndarray, prob: np.ndarray, value: np.ndarray) -> np.ndarray:
    """Per row 0..n-1: the exact sum (math.fsum: independent of order and batch) of
    ``prob * value`` over its entries; NaN for a row without entries."""
    out = np.full(n, np.nan)
    if len(row) == 0:
        return out
    order = np.argsort(row, kind="stable")
    r, x = row[order], (prob * value)[order]
    cut = np.flatnonzero(np.diff(r)) + 1
    for lo, hi in zip(np.r_[0, cut], np.r_[cut, len(r)], strict=True):
        out[r[lo]] = math.fsum(x[lo:hi].tolist())
    return out


def _score_then_kickoff(frame: pl.DataFrame, points: int, runoff: pl.Expr) -> pl.DataFrame:
    """The team with the ball scores ``points``; ``runoff`` seconds later the other team has
    a 1st and 10 at the kickoff spot. The scoring team's WP = 1 - wp(this state)."""
    f = frame.with_columns((pl.col("score_differential") + points).alias("score_differential"))
    return sm.first_down_at(sm.other_side(f.with_columns(_clock(runoff))),
                            pl.col("kick_yardline_100"))  # fmt: skip


def _receive_after_opp_score(frame: pl.DataFrame, points: int, runoff: pl.Expr) -> pl.DataFrame:
    """The OTHER team scores ``points``; the team with the ball now receives the kickoff:
    its 1st and 10 at the kickoff spot ``runoff`` seconds later (its WP = wp(this))."""
    f = frame.with_columns((pl.col("score_differential") - points).alias("score_differential"))
    return sm.first_down_at(f.with_columns(_clock(runoff)), pl.col("kick_yardline_100"))


SCORE_RUNOFF = pl.col("fg_runoff_make")  # a scoring play and the kickoff after it


def _p_convert(inputs: pl.DataFrame, models: Models) -> tuple[np.ndarray, np.ndarray]:
    """(raw, used) P(convert): ``used`` = the Platt recalibration when the row has one."""
    cols = ("season", "down", "ydstogo", "yardline_100", "goal_to_go", "score_differential",
            "game_seconds_remaining", "posteam_spread")  # fmt: skip
    raw = conversion.p_convert(inputs.select(cols), models.conversion)
    a = inputs.get_column("platt_a").cast(pl.Float64).fill_null(np.nan).to_numpy()
    b = inputs.get_column("platt_b").cast(pl.Float64).fill_null(np.nan).to_numpy()
    has = ~np.isnan(a)
    cal = gi.platt_apply(raw, np.where(has, a, 0.0), np.where(has, b, 1.0))
    return raw, np.where(has, cal, raw)


def go_option(inputs: pl.DataFrame, models: Models) -> dict[str, np.ndarray]:
    """P(convert) and the expected WP after a conversion / a failure (the offense's view)."""
    n, w = inputs.height, models.wp
    base = _base(inputs).with_columns(inputs.select("fg_runoff_make"))
    clock = _clock(pl.lit(float(models.conversion.extras["runoff_seconds"])))
    raw, p = _p_convert(inputs, models)
    yd = inputs.select("ydstogo", "yardline_100")
    # after a conversion: a touchdown, or the offense's 1st down at its new spot
    e = (conversion._spread(yd, models.conversion, "success").select("row", "td", "value", "prob")
         .join(base, on="row", how="left"))  # fmt: skip
    td = (pl.col("td") | (pl.col("value") >= pl.col("yardline_100"))).fill_null(False)
    e_td, e_fd = e.filter(td), e.filter(~td)
    s_td = 1.0 - _wp(_score_then_kickoff(e_td, TD_POINTS, SCORE_RUNOFF), w)
    s_fd = _wp(sm.first_down_at(e_fd.with_columns(clock),
                                pl.col("yardline_100") - pl.col("value")), w)  # fmt: skip
    succ = _expect(n, *_entries([e_td, e_fd], [s_td, s_fd]))
    # after a failure: the opponent's 1st down at its spot, or its touchdown on the play
    e = (conversion._spread(yd, models.conversion, "failure").select("row", "td", "value", "prob")
         .join(base, on="row", how="left"))  # fmt: skip
    e_td, e_ob = e.filter(pl.col("td")), e.filter(~pl.col("td"))
    f_td = _wp(_receive_after_opp_score(e_td, TD_POINTS, SCORE_RUNOFF), w)
    opp = pl.col("value") - pl.col("yardline_100") + 100
    f_ob = 1.0 - _wp(sm.first_down_at(sm.other_side(e_ob.with_columns(clock)), opp), w)
    fail = _expect(n, *_entries([e_td, e_ob], [f_td, f_ob]))
    return {"p_convert_raw": raw, "p_convert": p, "wp_go_success": succ,
            "wp_go_failure": fail, "wp_go": p * succ + (1.0 - p) * fail}  # fmt: skip


def _entries(frames: list[pl.DataFrame], values: list[np.ndarray]):
    """(row, prob, value) arrays of the outcome entries of ``frames`` (for :func:`_expect`)."""
    row = np.concatenate([f.get_column("row").to_numpy() for f in frames])
    prob = np.concatenate([f.get_column("prob").to_numpy() for f in frames])
    return row, prob, np.concatenate(values)


def fg_option(inputs: pl.DataFrame, models: Models) -> dict[str, np.ndarray]:
    """P(make) and the offense's WP after a make (3 points, the opponent receives the
    kickoff) and after a miss (the opponent's ball at the spot of the kick, or its 20)."""
    w = models.wp
    p = fieldgoal.p_make(inputs.select("season", "yardline_100", *gi.FG_STATE), models.fieldgoal)
    base = _base(inputs).with_columns(inputs.select("fg_runoff_make", "fg_runoff_miss"))
    made = 1.0 - _wp(_score_then_kickoff(base, FG_POINTS, pl.col("fg_runoff_make")), w)
    miss = base.with_columns(_clock(pl.col("fg_runoff_miss")))
    spot = pl.min_horizontal(pl.lit(80), 100 - fieldgoal.HOLD - pl.col("yardline_100"))
    missed = 1.0 - _wp(sm.first_down_at(sm.other_side(miss), spot), w)
    return {"p_make": p, "wp_fg_make": made, "wp_fg_miss": missed,
            "wp_fg": p * made + (1.0 - p) * missed}  # fmt: skip


def punt_option(inputs: pl.DataFrame, models: Models) -> dict[str, np.ndarray]:
    """The kicking team's expected WP over G2's punt result distribution (after a touchdown
    either way the other team starts at the measured kickoff spot)."""
    states = inputs.select(*sm.STATE_INPUTS, "yardline_100", "kick_yardline_100")
    d = punt.distribution(states, models.punt)
    runoff = float(models.punt.extras.get("runoff_seconds", 0.0))
    o = punt.outcome_wp(d, states, runoff, models.wp, after_td="kick_yardline_100")
    vals = o.get_column("wp_kick").to_numpy()
    ev = _expect(inputs.height, o.get_column("row").to_numpy(),
                 o.get_column("prob").to_numpy(), vals)  # fmt: skip
    return {"wp_punt": ev}


def try_option(inputs: pl.DataFrame, models: Models) -> dict[str, np.ndarray]:
    """WP of kicking the extra point and of going for two, for the scoring team (its state
    at the try; the score already counts the touchdown); the opponent then receives."""
    w = models.wp
    base = _base(inputs)
    after = {pts: 1.0 - _wp(_score_then_kickoff(base, pts, pl.col("kick_runoff")), w)
             for pts in (0, 1, 2)}  # fmt: skip
    pat = inputs.get_column("p_pat").to_numpy()
    two = inputs.get_column("p_two_point").to_numpy()
    return {"wp_after_0": after[0], "wp_after_1": after[1], "wp_after_2": after[2],
            "wp_kick": pat * after[1] + (1.0 - pat) * after[0],
            "wp_two_point": two * after[2] + (1.0 - two) * after[0]}  # fmt: skip


# --------------------------------------------------------------------------------------
# Recommendation, WP lost, clear vs toss-up
# --------------------------------------------------------------------------------------


def _decide(inputs: pl.DataFrame, wps: np.ndarray, names: tuple[str, ...]) -> dict[str, Any]:
    """``wps``: (rows, options) WP of each option, NaN where an option did not exist. The
    best option (ties: the earlier name), the second best, the gap, the chosen option's WP,
    WP lost and the grade: 'clear' when the gap exceeds the row's ``margin``, 'toss_up'
    otherwise, 'one_option' when nothing else existed."""
    n = wps.shape[0]
    filled = np.where(np.isnan(wps), -np.inf, wps)
    order = np.argsort(-filled, axis=1, kind="stable")
    best, second = order[:, 0], order[:, 1]
    rows = np.arange(n)
    w_best, w_second = filled[rows, best], filled[rows, second]
    has_second = np.isfinite(w_second)
    gap = np.where(has_second, w_best - np.where(has_second, w_second, 0.0), np.nan)
    chosen = inputs.get_column("chosen").to_list()
    idx = np.array([names.index(c) for c in chosen], dtype=np.int64)
    w_chosen = wps[rows, idx]
    margin = inputs.get_column("margin").to_numpy()
    grade = np.where(~has_second, "one_option", np.where(gap > margin, "clear", "toss_up"))
    names_arr = np.array(names)
    return {"recommended": names_arr[best], "second_best": np.where(has_second,
            names_arr[second], None), "wp_best": w_best,
            "wp_second": np.where(has_second, w_second, np.nan), "gap": gap,
            "wp_chosen": w_chosen, "wp_lost": w_best - w_chosen, "grade": grade,
            "correct": idx == best}  # fmt: skip


FOURTH_OUTPUTS = ("p_convert_raw", "p_convert", "wp_go_success", "wp_go_failure", "wp_go",
                  "fg_available", "p_make", "wp_fg_make", "wp_fg_miss", "wp_fg",
                  "punt_available", "wp_punt", "wp_before", "recommended", "second_best",
                  "wp_best", "wp_second", "gap", "wp_chosen", "wp_lost", "grade",
                  "correct")  # fmt: skip
TRY_OUTPUTS = ("wp_after_0", "wp_after_1", "wp_after_2", "wp_kick", "wp_two_point",
               "recommended", "second_best", "wp_best", "wp_second", "gap", "wp_chosen",
               "wp_lost", "grade", "correct")  # fmt: skip


def grade_fourth_downs(inputs: pl.DataFrame, models: Models) -> pl.DataFrame:
    """The :data:`FOURTH_OUTPUTS` of every row of ``inputs`` (stored-input columns only).
    The field goal exists within ``fg_max_distance`` yards and the punt from
    ``punt_min_yardline`` out, and whatever the offense really chose always exists."""
    if inputs.height == 0:
        return pl.DataFrame()
    go, fg, pt = (go_option(inputs, models), fg_option(inputs, models),
                  punt_option(inputs, models))  # fmt: skip
    chosen = inputs.get_column("chosen")
    fg_ok = ((inputs.get_column("fg_distance") <= inputs.get_column("fg_max_distance"))
             | (chosen == "field_goal")).to_numpy()  # fmt: skip
    punt_ok = ((inputs.get_column("yardline_100") >= inputs.get_column("punt_min_yardline"))
               | (chosen == "punt")).to_numpy()  # fmt: skip
    wps = np.column_stack([go["wp_go"], np.where(fg_ok, fg["wp_fg"], np.nan),
                           np.where(punt_ok, pt["wp_punt"], np.nan)])  # fmt: skip
    out = {**go, **fg, **pt, "fg_available": fg_ok, "punt_available": punt_ok,
           "wp_before": _wp(_base(inputs), models.wp), **_decide(inputs, wps, OPTIONS)}  # fmt: skip
    return pl.DataFrame({k: out[k] for k in FOURTH_OUTPUTS}, strict=False)


def grade_tries(inputs: pl.DataFrame, models: Models) -> pl.DataFrame:
    """The :data:`TRY_OUTPUTS` of every try in ``inputs`` (both options always exist)."""
    if inputs.height == 0:
        return pl.DataFrame()
    t = try_option(inputs, models)
    wps = np.column_stack([t["wp_kick"], t["wp_two_point"]])
    out = {**t, **_decide(inputs, wps, TRY_OPTIONS)}
    return pl.DataFrame({k: out[k] for k in TRY_OUTPUTS}, strict=False)


# --------------------------------------------------------------------------------------
# One season: the stored inputs, the grades, the files
# --------------------------------------------------------------------------------------

VERSION_COLUMNS = ("wp_version", "conversion_version", "fieldgoal_version", "punt_version",
                   "tries_version")  # fmt: skip
FOURTH_INPUTS = (*gi.WP_STATE, "goal_to_go", *gi.FG_STATE, "kick_yardline_100", "kick_runoff",
                 "fg_max_distance", "punt_min_yardline", "fg_runoff_make", "fg_runoff_miss",
                 "platt_a", "platt_b", "margin", "chosen")  # fmt: skip
TRY_INPUTS = (*gi.WP_STATE, "kick_yardline_100", "kick_runoff", "p_pat", "p_two_point",
              "margin", "chosen")  # fmt: skip
KICK_INFO = ("kick_spot_mean", "kick_n", "kick_source")


def _outcome() -> pl.Expr:
    """What really happened on a fourth down (for context; never an input)."""
    off_td = (pl.col("touchdown") == 1) & (pl.col("td_team") == pl.col("posteam"))
    kept = (pl.col("interception") != 1).fill_null(True) & (pl.col("fumble_lost") != 1).fill_null(
        True)  # fmt: skip
    conv = ((pl.col("first_down") == 1) | off_td).fill_null(False) & kept
    return (
        pl.when(pl.col("chosen") == "go").then(pl.when(off_td).then(pl.lit("touchdown"))
        .when(conv).then(pl.lit("converted")).otherwise(pl.lit("failed")))
        .when(pl.col("chosen") == "field_goal").then(pl.col("field_goal_result"))
        .when(pl.col("chosen") == "punt").then(pl.lit("punted"))
        .otherwise(None).alias("outcome")
    )  # fmt: skip


def season_inputs(db: Path | str, season: int, models: Models, cfg: Any, *,
                  progress: Callable[[str], None] = print):  # fmt: skip
    """Every fourth down and try of ``season`` with its stored inputs (point-in-time) and its
    exclusion; plus the season-level inputs (``info``): (fourth downs, tries, info)."""
    t0 = time.perf_counter()
    plays = gi.load_season(db, season)
    games = plays.select("game_id", "season", "week").unique()
    spots = gi.kickoff_spots(games, gi.kickoffs(db, [season - 1, season]), cfg.kickoff_min_kicks)
    platt = gi.platt(gi.platt_rows(db, season, cfg.platt_seasons), season, cfg.platt_seasons)
    info = {**gi.ranges(db, season, cfg.punt_range_quantile), **gi.runoffs(db, season),
            "platt": platt, "margin": float(cfg.toss_up_margin),
            "end_of_half_seconds": int(cfg.end_of_half_seconds), **models.versions()}  # fmt: skip
    fourth, tr = assemble(plays, spots, info, models)
    progress(f"{season}: {plays.height:,} plays, {fourth.height:,} fourth downs, {tr.height:,} "
             f"tries; inputs [{time.perf_counter() - t0:.0f} s]")  # fmt: skip
    return fourth, tr, info


def assemble(plays: pl.DataFrame, spots: pl.DataFrame, info: Mapping[str, Any],
             models: Models) -> tuple[pl.DataFrame, pl.DataFrame]:  # fmt: skip
    """The candidate fourth downs and tries of ``plays`` (one season) with every stored input:
    the per-game kickoff spot (``spots``), the season-level inputs (``info``: ranges, runoffs,
    Platt, margin, end-of-half seconds) and the models' versions and try rates."""
    fourth = gi.fourth_down_rows(plays, info["end_of_half_seconds"]).with_columns(_outcome())
    tr = gi.try_rows(plays)
    rates, platt = models.tries.extras, info["platt"]
    common = [pl.lit(float(info["margin"])).alias("margin"),
              *(pl.lit(v).alias(k) for k, v in models.versions().items()),
              pl.lit(GRADE_FORMAT).alias("grade_format")]  # fmt: skip
    fourth = fourth.join(spots, on="game_id", how="left").with_columns(
        *common, *(pl.lit(info[k]).alias(k) for k in ("fg_max_distance", "punt_min_yardline",
                                                       "fg_runoff_make", "fg_runoff_miss")),
        pl.lit(platt["a"], pl.Float64).alias("platt_a"),
        pl.lit(platt["b"], pl.Float64).alias("platt_b"),
    )  # fmt: skip
    tr = tr.join(spots, on="game_id", how="left").with_columns(
        *common, pl.lit(float(rates["pat"]["rate"])).alias("p_pat"),
        pl.lit(float(rates["two_point"]["rate"])).alias("p_two_point"),
    )  # fmt: skip
    no_kick = pl.col("exclusion").is_null() & pl.col("kick_yardline_100").is_null()
    fix = pl.when(no_kick).then(pl.lit("missing_state")).otherwise(pl.col("exclusion"))
    return fourth.with_columns(fix.alias("exclusion")), tr.with_columns(fix.alias("exclusion"))


def grade_frames(fourth: pl.DataFrame, tr: pl.DataFrame, models: Models):
    """:func:`assemble`'s rows + their grades (NULL outputs for excluded rows)."""
    return (_graded(fourth, FOURTH_INPUTS, grade_fourth_downs, models),
            _graded(tr, TRY_INPUTS, grade_tries, models))  # fmt: skip


def _graded(rows: pl.DataFrame, inputs: tuple[str, ...], fn, models: Models) -> pl.DataFrame:
    """``rows`` + the grade outputs (NULL for excluded rows)."""
    g = rows.filter(pl.col("exclusion").is_null())
    out = fn(g.select(inputs), models)
    if out.height == 0:
        return rows
    return rows.join(g.select(KEYS).hstack(out), on=list(KEYS),
                     how="left").sort(list(KEYS))  # fmt: skip


def graded_dir() -> Path:
    """``data/decisions/graded``: one parquet per season and kind (gitignored)."""
    return wpm.backtest_dir().parent / "graded"


def grade_season(db: Path | str, season: int, *, cfg: Any = None, models: Models | None = None,
                 out_dir: Path | None = None, progress: Callable[[str], None] = print):  # fmt: skip
    """Grade every fourth down and try of ``season`` with its fold models and write
    ``fourth_downs_<S>.parquet``, ``two_point_<S>.parquet`` and ``season_<S>.json``."""
    from twm.config import settings

    t0 = time.perf_counter()
    cfg = cfg if cfg is not None else settings().decisions
    models = models if models is not None else load_models(season)
    models.check(season)
    fourth, tr, info = season_inputs(db, season, models, cfg, progress=progress)
    fourth, tr = grade_frames(fourth, tr, models)
    progress(f"{season}: graded [{time.perf_counter() - t0:.0f} s]")
    summary = write_season(season, fourth, tr, info, out_dir=out_dir,
                           seconds=time.perf_counter() - t0)  # fmt: skip
    progress(f"{season}: {summary['fourth_downs']} | tries {summary['tries']} "
             f"[{summary['seconds']:.0f} s]")  # fmt: skip
    return summary


def write_season(season: int, fourth: pl.DataFrame, tr: pl.DataFrame, info: Mapping[str, Any],
                 *, out_dir: Path | None = None, seconds: float = 0.0) -> dict:  # fmt: skip
    """Write ``fourth_downs_<S>.parquet``, ``two_point_<S>.parquet`` and ``season_<S>.json``
    (the season-level inputs and the counts); returns the summary."""
    d = out_dir if out_dir is not None else graded_dir()
    d.mkdir(parents=True, exist_ok=True)
    fourth.write_parquet(d / f"fourth_downs_{season}.parquet")
    tr.write_parquet(d / f"two_point_{season}.parquet")
    summary = {"season": season, "format": GRADE_FORMAT, **info,
               "fourth_downs": _counts(fourth), "tries": _counts(tr),
               "seconds": round(seconds, 1)}  # fmt: skip
    (d / f"season_{season}.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    return summary


def _counts(rows: pl.DataFrame) -> dict[str, int]:
    """Rows by exclusion rule and, for graded rows, by grade."""
    ex = rows.group_by("exclusion").len().iter_rows()
    out = {f"excluded_{k}": int(n) for k, n in ex if k is not None}
    if "grade" in rows.columns:
        for k, n in rows.filter(pl.col("exclusion").is_null()).group_by("grade").len().iter_rows():
            out[str(k)] = int(n)
    out["rows"] = rows.height
    return dict(sorted(out.items()))


def load_graded(kind: str, seasons=None, out_dir: Path | None = None) -> pl.DataFrame:
    """The stored rows of ``kind`` ('fourth_downs' or 'two_point') for ``seasons`` (default:
    every stored season)."""
    d = out_dir if out_dir is not None else graded_dir()
    files = sorted(d.glob(f"{kind}_*.parquet"))
    if seasons is not None:
        want = {int(s) for s in seasons}
        files = [f for f in files if int(f.stem.rsplit("_", 1)[1]) in want]
    if not files:
        raise GradeError(f"no graded {kind} in {d}: run `twm decisions grade`")
    return pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")


# --------------------------------------------------------------------------------------
# Reproducing stored grades
# --------------------------------------------------------------------------------------

KINDS = {"fourth_downs": (FOURTH_INPUTS, FOURTH_OUTPUTS, grade_fourth_downs),
         "two_point": (TRY_INPUTS, TRY_OUTPUTS, grade_tries)}  # fmt: skip


def regrade(stored: pl.DataFrame, kind: str, *, models: Mapping[tuple, Models] | None = None,
            models_root: Path | None = None) -> pl.DataFrame:  # fmt: skip
    """Recompute the grade outputs of stored graded rows (``kind`` 'fourth_downs' or
    'two_point') from their stored inputs alone, with the models their version columns name
    (``models``: optional {version tuple: Models}, else loaded from ``models_root``). Returns
    KEYS + the outputs, in ``stored``'s row order."""
    inputs, outputs, fn = KINDS[kind]
    rows = stored.filter(pl.col("exclusion").is_null()).with_row_index("_i")
    parts = []
    for vers, part in rows.group_by(list(VERSION_COLUMNS), maintain_order=True):
        key = tuple(vers)
        m = (models or {}).get(key) or models_by_version(dict(zip(VERSION_COLUMNS, key,
                                                                  strict=True)),
                                                         models_root=models_root)  # fmt: skip
        if kind == "two_point":
            _check_rates(part, m)
        out = fn(part.select(inputs), m)
        parts.append(part.select("_i", *KEYS).hstack(out))
    if not parts:
        return pl.DataFrame()
    return pl.concat(parts, how="diagonal_relaxed").sort("_i").drop("_i")


def _check_rates(rows: pl.DataFrame, models: Models) -> None:
    """The stored try rates must be the tries model's (they are inputs AND a model output)."""
    r = models.tries.extras
    for col, k in (("p_pat", "pat"), ("p_two_point", "two_point")):
        if not (rows.get_column(col) == float(r[k]["rate"])).all():
            raise GradeError(f"stored {col} differs from {models.tries.version}'s rate")


def verify(stored: pl.DataFrame, kind: str, **kw) -> list[str]:
    """The output columns whose regraded values differ from the stored ones (bit for bit;
    NaN equals NaN). Empty = every stored grade reproduced exactly."""
    _, outputs, _ = KINDS[kind]
    again = regrade(stored, kind, **kw)
    have = stored.filter(pl.col("exclusion").is_null()).select(*KEYS, *outputs)
    if again.height != have.height:
        return ["<rows>"]
    bad = []
    for c in outputs:
        a, b = have.get_column(c), again.get_column(c)
        if a.dtype.is_float():
            same = np.array_equal(a.to_numpy(), b.cast(pl.Float64).to_numpy(), equal_nan=True)
        else:
            same = a.cast(pl.String).fill_null("<null>").equals(
                b.cast(pl.String).fill_null("<null>"))  # fmt: skip
        if not same:
            bad.append(c)
    return bad
