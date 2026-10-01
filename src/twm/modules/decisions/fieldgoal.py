"""Field-goal sub-model: P(make | distance, conditions, era) (PROJECT_SPEC 8.4 item 2, G2).

**Rows**: every ``field_goal`` play of ``fact_play`` (fakes are pass/run plays and belong to the
go model; ``no_play`` rows are dropped: the kick was wiped out by a penalty and retried).
**Label** ``fg_made``: ``field_goal_result`` = 'made' (missed and blocked = 0).

**Inputs, all known to the coach when he decides** (the game's recorded conditions):

- ``fg_distance`` = ``yardline_100`` + 18 (the decision-time distance: 10 yards of end zone and
  the hold about 8 yards behind the line; the recorded ``kick_distance`` minus the yardline is
  18 on 88% of 2023-2025 kicks and 19 on 12%).
- ``roof_closed`` (dome or closed roof), ``temp_f`` and ``wind_mph``: from ``fact_game``; when
  an outdoor game lacks them (2022: 91 of 198 outdoor games, 2023: 41 of 199, 0-14 other
  seasons) they are read from the ``fact_play.weather`` text ("Temp: 73° F, ... Wind: W 3 mph",
  'calm' = 0). Indoors: 70 F and no wind (recorded as such, not imputed). Still unknown
  outdoors: ``weather_missing`` = 1 and the value is filled with the median of the TRAINING
  rows' outdoor games inside the estimator (never from validation or test rows).
- ``surface_grass`` (grass or dessograss; unknown -> training median), the era flags.

**Model**: LightGBM, monotone (a longer kick or more wind never helps; warmer air never hurts);
the weather group's value is measured on every validation season (ablation, information only).

**After a miss** (:func:`miss_spot`): the opponent's first down at the spot of the kick (the hold,
about 8 yards behind the line) or at its own 20 when that spot is inside the 20:
``opp_yardline_100`` = min(80, 92 - ``yardline_100``) (NFL rule since 1994; verified on the
training seasons' misses, :func:`miss_rule_check`). Blocked kicks can be returned; they are
~1.5% of attempts and use the same rule here.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl

from twm.modules.decisions import submodels as sm

MODEL = "fieldgoal_lgbm"
LABEL = "fg_made"
WEATHER = ("roof_closed", "wind_mph", "temp_f", "surface_grass", "weather_missing")
FEATURES = ("fg_distance", *WEATHER, "era_pat_2015", "era_kickoff_2023")
FILTERS = ("not_a_field_goal", "missing_state")
HOLD = 8  # yards behind the line of scrimmage (spot of the kick)
DISTANCE_OFFSET = 10 + HOLD
INDOOR_TEMP = 70.0
MONOTONE = {"fg_distance": -1, "wind_mph": -1, "temp_f": 1}
GRID = tuple({"num_leaves": nl, "min_child_samples": mcs, "n_estimators": 3000}
             for nl in (7, 15) for mcs in (50, 200))  # fmt: skip
DEFAULT_PARAMS = {"num_leaves": 7, "min_child_samples": 200, "n_estimators": 300}
WINDOWS = (None, 10, 5)  # train on all earlier seasons, or the last 10 or 5 (validated)
_OUTDOOR_KNOWN = (pl.col("roof_closed") == 0) & (pl.col("weather_missing") == 0)


def estimator() -> sm.LgbmEstimator:
    return sm.LgbmEstimator(MODEL, monotone=MONOTONE, grid=GRID, default_params=DEFAULT_PARAMS,
                            impute={"temp_f": _OUTDOOR_KNOWN, "wind_mph": _OUTDOOR_KNOWN,
                                    "surface_grass": None})  # fmt: skip


def conditions() -> list[pl.Expr]:
    """``roof_closed``, ``temp_f``, ``wind_mph``, ``surface_grass``, ``weather_missing`` from
    ``roof``, ``temp``, ``wind``, ``weather`` (text) and ``surface``."""
    text = pl.col("weather").cast(pl.String).str.to_lowercase()
    t_txt = text.str.extract(r"temp:\s*(-?\d+)", 1).cast(pl.Float64)
    w_txt = (
        pl.when(text.str.contains(r"wind:\s*calm")).then(pl.lit("0"))
        .otherwise(text.str.extract(r"wind:[^,;\d]*(\d+)[^,;\d]*mph", 1)).cast(pl.Float64)
    )  # fmt: skip
    indoor = pl.col("roof").cast(pl.String).is_in(["dome", "closed"]).fill_null(False)
    temp = (
        pl.when(indoor)
        .then(INDOOR_TEMP)
        .otherwise(pl.coalesce(pl.col("temp").cast(pl.Float64), t_txt))
    )
    wind = pl.when(indoor).then(0.0).otherwise(pl.coalesce(pl.col("wind").cast(pl.Float64), w_txt))
    surface = pl.col("surface").cast(pl.String).str.strip_chars().str.to_lowercase()
    grass = (pl.when(surface.is_in(["grass", "dessograss"])).then(1.0)
             .when(surface.is_null() | (surface == "")).then(None).otherwise(0.0))  # fmt: skip
    return [
        indoor.cast(pl.Int8).alias("roof_closed"),
        temp.alias("temp_f"),
        wind.alias("wind_mph"),
        grass.alias("surface_grass"),
        (~indoor & (temp.is_null() | wind.is_null())).cast(pl.Int8).alias("weather_missing"),
    ]  # fmt: skip


def add_features(frame: pl.DataFrame) -> pl.DataFrame:
    """The model's inputs from a play-state frame with ``yardline_100``, ``season`` and the
    game's ``roof``, ``temp``, ``wind``, ``weather`` and ``surface`` (columns already present,
    e.g. a hypothetical state's, are kept)."""
    f = frame
    if "fg_distance" not in f.columns:
        f = f.with_columns((pl.col("yardline_100") + DISTANCE_OFFSET).alias("fg_distance"))
    if not set(WEATHER) <= set(f.columns):
        for c in ("roof", "temp", "wind", "weather", "surface"):
            if c not in f.columns:
                f = f.with_columns(pl.lit(None, pl.String if c in ("roof", "weather",
                                                                   "surface") else pl.Int32)
                                   .alias(c))  # fmt: skip
        f = f.with_columns(conditions())
    if "era_pat_2015" not in f.columns or "era_kickoff_2023" not in f.columns:
        f = f.with_columns(sm.era_flags())
    return f


def build_rows(plays: pl.DataFrame) -> tuple[pl.DataFrame, dict[str, int]]:
    """Model rows (KEYS, FEATURES, LABEL and the next-snap columns) and the drop counts."""
    rows, drops = sm.count_filters(plays, [
        ("not_a_field_goal", pl.col("play_type") == "field_goal"),
        ("missing_state", pl.col("yardline_100").is_between(1, 99)
         & pl.col("field_goal_result").is_not_null() & pl.col("posteam").is_not_null()),
    ])  # fmt: skip
    rows = add_features(rows).with_columns(
        (pl.col("field_goal_result") == "made").cast(pl.Int8).alias(LABEL),
        (pl.col("n_game_half") == pl.col("game_half")).fill_null(False).alias("same_half"),
        ((pl.col("roof_closed") == 0) & (pl.col("temp").is_null() | pl.col("wind").is_null())
         & (pl.col("weather_missing") == 0)).alias("weather_from_text"),
    )  # fmt: skip
    cols = [*sm.KEYS, "week", "down", "yardline_100", *FEATURES, LABEL, "field_goal_result",
            "posteam", "defteam", "same_half", "n_posteam", "n_yardline_100", "roof",
            "weather_from_text"]  # fmt: skip
    return rows.select(list(dict.fromkeys(cols))).sort(list(sm.KEYS)), drops


def miss_spot(states: pl.DataFrame) -> pl.Series:
    """The opponent's first-down spot (its ``yardline_100``) after a missed field goal."""
    s = states.select(pl.min_horizontal(pl.lit(80), 100 - HOLD - pl.col("yardline_100")))
    return s.to_series().alias("opp_yardline_100")


def miss_rule_check(rows: pl.DataFrame) -> dict[str, Any]:
    """How often the opponent's next snap after a miss is where :func:`miss_spot` says (exactly
    and within 1 yard: the hold is 7-9 yards deep), among misses followed by the opponent's
    snap in the same half; blocked kicks are counted apart."""
    m = rows.filter((pl.col(LABEL) == 0) & pl.col("same_half")
                    & (pl.col("n_posteam") == pl.col("defteam")))  # fmt: skip
    m = m.with_columns(miss_spot(m)).with_columns(
        (pl.col("n_yardline_100") - pl.col("opp_yardline_100")).abs().alias("gap")
    )
    out: dict[str, Any] = {"n_misses": int(rows.filter(pl.col(LABEL) == 0).height)}
    for kind in ("missed", "blocked"):
        k = m.filter(pl.col("field_goal_result") == kind)
        n = max(k.height, 1)
        out[kind] = {"n": k.height,
                     "exact": k.filter(pl.col("gap") == 0).height / n,
                     "within_1": k.filter(pl.col("gap") <= 1).height / n}  # fmt: skip
    return out


def p_make(states: pl.DataFrame, model: sm.SubModel) -> np.ndarray:
    """P(make) per row of ``states`` (a play-state frame: ``yardline_100``, ``season`` and the
    game's conditions, or the model features themselves)."""
    s = add_features(states)
    bad = s.filter(~pl.col("fg_distance").is_between(DISTANCE_OFFSET + 1, 99 + DISTANCE_OFFSET))
    if bad.height:
        raise sm.SubModelError(f"{bad.height} state(s) have a yardline outside 1-99")
    return model.probability(s)


SPEC = sm.BinarySpec(
    MODEL, LABEL, FEATURES, estimator,
    extras=lambda train: {"miss_rule": miss_rule_check(train)},
    ablation={"weather": WEATHER},
    extra_columns=("field_goal_result", "defteam", "same_half", "n_posteam", "n_yardline_100"),
    windows=WINDOWS,
)  # fmt: skip


def fit_fold(rows: pl.DataFrame, season: int, *, progress=print) -> sm.FoldOutput:
    """One walk-forward fold (rows of seasons <= ``season``; learns from seasons < it)."""
    return sm.run_binary_fold(rows, season, SPEC, progress=progress)
