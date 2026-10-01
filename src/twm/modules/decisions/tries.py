"""Extra-point and two-point rates, point-in-time (PROJECT_SPEC 8.4 items 2-3, G2; used by G3).

For test season S (only seasons < S are read):

- **PAT rate**: made / attempted extra points (``extra_point_attempt`` rows, ``extra_point_result``
  good vs failed or blocked) in the last :data:`WINDOW` seasons before S **in S's rule era**
  (from 2015 the kick is snapped from the 15: rates fell from ~99% to ~94%). The first season
  of an era (2015) has no earlier season under its rule, so it uses the make rate of field
  goals of 31-35 yards (the new extra point is a 33-yard kick) in the last WINDOW seasons.
- **Two-point rate**: successful / attempted two-point tries (``two_point_attempt`` rows) in
  the last WINDOW seasons before S.

Intervals: Jeffreys 95% (Beta(k + 1/2, n - k + 1/2) quantiles). ``no_play`` rows are left out
(a penalty wiped the try out; it is retried). The held-out check scores every try of S with
the rate (Brier, log loss) against a naive rate (every earlier season pooled, no era).
"""

from __future__ import annotations

import time
from typing import Any

import polars as pl

from twm.modules.decisions import submodels as sm

MODEL = "tries_rates"
WINDOW = 5
FG_EQUIVALENT = (31, 35)  # fg_distance of the post-2015 extra point (snapped from the 15)
KINDS = ("pat", "two_point", "fg_equivalent")
HASH_COLUMNS = ("week", "kind", "success", "era_pat_2015")


def build_rows(plays: pl.DataFrame) -> tuple[pl.DataFrame, dict[str, int]]:
    """One row per extra point, two-point try and 31-35-yard field goal, with ``kind`` and
    ``success``; and the drop counts."""
    from twm.modules.decisions.fieldgoal import DISTANCE_OFFSET

    pat = (pl.col("extra_point_attempt") == 1) & (pl.col("play_type") == "extra_point")
    two = (pl.col("two_point_attempt") == 1) & pl.col("play_type").is_in(["pass", "run"])
    dist = pl.col("yardline_100") + DISTANCE_OFFSET
    fg = (pl.col("play_type") == "field_goal") & dist.is_between(*FG_EQUIVALENT)
    rows, drops = sm.count_filters(plays, [
        ("not_a_try", pat | two | fg),
        ("no_result", pl.when(pat).then(pl.col("extra_point_result").is_not_null())
         .when(two).then(pl.col("two_point_conv_result").is_not_null())
         .otherwise(pl.col("field_goal_result").is_not_null())),
    ])  # fmt: skip
    rows = rows.with_columns(
        pl.when(pat).then(pl.lit("pat")).when(two).then(pl.lit("two_point"))
        .otherwise(pl.lit("fg_equivalent")).alias("kind"),
        pl.when(pat).then(pl.col("extra_point_result") == "good")
        .when(two).then(pl.col("two_point_conv_result") == "success")
        .otherwise(pl.col("field_goal_result") == "made").cast(pl.Int8).alias("success"),
        *sm.era_flags(),
    )  # fmt: skip
    return rows.select(*sm.KEYS, "week", "kind", "success", "era_pat_2015").sort(
        list(sm.KEYS)), drops  # fmt: skip


def jeffreys(k: int, n: int, level: float = 0.95) -> tuple[float | None, float | None]:
    if n == 0:
        return None, None
    from scipy.stats import beta

    a = (1 - level) / 2
    return float(beta.ppf(a, k + 0.5, n - k + 0.5)), float(beta.ppf(1 - a, k + 0.5, n - k + 0.5))


def _rate(rows: pl.DataFrame, kind: str, seasons: list[int]) -> dict[str, Any]:
    r = rows.filter((pl.col("kind") == kind) & pl.col("season").is_in(seasons))
    n, k = r.height, int(r.get_column("success").sum())
    lo, hi = jeffreys(k, n)
    return {"rate": k / n if n else None, "n": n, "made": k, "lo": lo, "hi": hi,
            "seasons": sorted(seasons)}  # fmt: skip


def rates(rows: pl.DataFrame, season: int) -> dict[str, Any]:
    """The PAT and two-point rates for ``season`` from seasons < it only (see the module
    docstring), with their source seasons, counts and Jeffreys intervals."""
    from twm.backtest.walkforward import assert_before

    past = rows.filter(pl.col("season") < season)
    assert_before(past, season, what="compute try rates")
    have = sorted(set(past.get_column("season").to_list()))
    era = int(season >= 2015)
    same_era = [s for s in have if int(s >= 2015) == era][-WINDOW:]
    if past.filter((pl.col("kind") == "pat") & pl.col("season").is_in(same_era)).height:
        pat = {**_rate(past, "pat", same_era), "source": "extra points, same rule era"}
    else:
        pat = {**_rate(past, "fg_equivalent", have[-WINDOW:]),
               "source": f"field goals of {FG_EQUIVALENT[0]}-{FG_EQUIVALENT[1]} yards "
                         "(first season of the rule era)"}  # fmt: skip
    two = {**_rate(past, "two_point", have[-WINDOW:]), "source": "two-point tries"}
    naive = {k: _rate(past, k, have) for k in ("pat", "two_point")}
    return {"pat": pat, "two_point": two, "naive": naive}


def fit_fold(rows: pl.DataFrame, season: int, *, progress=print) -> sm.FoldOutput:
    """The rates for ``season`` and every try of ``season`` scored with them."""
    from twm.backtest.walkforward import plan_folds
    from twm.predictions import model_version

    t0 = time.perf_counter()
    fold = plan_folds(rows.get_column("season").unique().to_list(), [season])[0]
    r = rates(rows, season)
    train = rows.filter(pl.col("season") < season)
    h = sm.training_hash(train, HASH_COLUMNS)
    model = sm.SubModel(version="", model=MODEL, test_season=season,
                        train_seasons=fold.train_seasons, val_season=None, features=(),
                        params={"window": WINDOW}, dataset_hash=h, extras=r,
                        calibration="none (rates)")  # fmt: skip
    model.version = model_version(module=sm.MODULE, model=MODEL, label="try_success",
                                  features=(), params={"window": WINDOW},
                                  training_seasons=fold.train_seasons, test_season=season,
                                  dataset_hash=h)  # fmt: skip
    test = rows.filter((pl.col("season") == season) & (pl.col("kind") != "fg_equivalent"))
    prob = {k: r[k]["rate"] for k in ("pat", "two_point")}
    naive = {k: r["naive"][k]["rate"] for k in ("pat", "two_point")}
    preds = test.select(*sm.KEYS, "kind", "success").with_columns(
        pl.col("kind").replace_strict(prob, return_dtype=pl.Float64).alias("prob"),
        pl.col("kind").replace_strict(naive, return_dtype=pl.Float64).alias("prob_naive"),
    )
    summary = {"test_season": season, "fold": fold.describe(), "version": model.version,
               "model": MODEL, "rates": r, "n_test": test.height, "dataset_hash": h,
               "format": sm.FILE_FORMAT, "calibration": model.calibration,
               "seconds": round(time.perf_counter() - t0, 1)}  # fmt: skip
    progress(f"  {MODEL} {season}: PAT {prob['pat']:.3f} ({r['pat']['source']}), two-point "
             f"{prob['two_point']:.3f}")  # fmt: skip
    return sm.FoldOutput(model, preds, summary)
