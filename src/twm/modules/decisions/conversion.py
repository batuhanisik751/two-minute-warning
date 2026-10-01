"""Go-for-it sub-model: P(the offense converts | the state) (PROJECT_SPEC 8.4 item 2, G2).

**Rows**: every third- and fourth-down ``pass`` or ``run`` play from ``fact_play`` (third downs
add sample; ``is_fourth_down`` separates them: a fourth-down try is a chosen gamble, a third
down is not). Dropped and counted (:data:`FILTERS`): other downs; ``no_play`` rows (a penalty
wiped the snap out: the down is replayed and the play call is unknown), punts, field goals,
kneels and spikes; rows with a missing or impossible state.

**Label** ``converted``: the play gains a first down or scores the offense's touchdown
(``fact_play.first_down`` = 1 or ``touchdown`` = 1 with ``td_team`` = the offense), and the
offense keeps the ball (no interception, no lost fumble). Penalties: a defensive penalty on a
play that counts and that awards a first down (pass interference, roughing the passer, ...)
is a conversion: that is what the try earned. Penalties that erase the play are ``no_play``
rows (dropped above). nflfastR's ``fourth_down_converted`` agrees except for those penalty
first downs.

**Model**: LightGBM (monotone: more yards to go never helps), base features ``ydstogo``,
``yardline_100``, ``goal_to_go``, ``is_fourth_down`` and the era flags; the score/clock/team
context (``score_differential``, ``game_seconds_remaining``, ``posteam_spread``) is used only
when the validation season says it helps (a grid dimension of every fold).

**Where the ball ends up** (:func:`after_success`, :func:`after_failure`; for G3's WP(success)
and WP(failure) states): empirical distributions from the training seasons, by distance band x
field zone (:data:`YDS_BANDS`, :data:`ZONES`; a cell with fewer than :data:`MIN_CELL` plays
borrows its zone's pooled rows, then all rows). After a conversion: the offense's next snap
(1st down) spot, or a touchdown. After a FOURTH-down failure: the opponent's next snap spot
(turnover on downs at the spot, or wherever a turnover was returned to), or the opponent's
touchdown (a return).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl

from twm.modules.decisions import submodels as sm

MODEL = "conversion_lgbm"
LABEL = "converted"
BASE = ("ydstogo", "yardline_100", "goal_to_go", "is_fourth_down", "era_pat_2015",
        "era_kickoff_2023")  # fmt: skip
CONTEXT = ("score_differential", "game_seconds_remaining", "posteam_spread")
FEATURES = (*BASE, *CONTEXT)
FEATURE_SETS = {"base": BASE, "context": FEATURES}
FILTERS = ("not_third_or_fourth_down", "not_a_pass_or_run", "missing_state")
MONOTONE = {"ydstogo": -1}
GRID = tuple({"num_leaves": nl, "min_child_samples": mcs, "n_estimators": 3000}
             for nl in (15, 31) for mcs in (200, 1000))  # fmt: skip
DEFAULT_PARAMS = {"num_leaves": 15, "min_child_samples": 1000, "n_estimators": 300}
YDS_BANDS = (1, 2, 4, 7, 10)  # upper bounds: 1, 2, 3-4, 5-7, 8-10, 11+
ZONES = (5, 10, 20, 40, 60, 80)  # upper bounds of yardline_100: 1-5, ..., 81-99
MIN_CELL = 30
WINDOWS = (None, 10, 5)  # train on all earlier seasons, or the last 10 or 5 (validated)


def estimator() -> sm.LgbmEstimator:
    return sm.LgbmEstimator(MODEL, monotone=MONOTONE, grid=GRID, default_params=DEFAULT_PARAMS,
                            feature_sets=FEATURE_SETS,
                            impute={"posteam_spread": None})  # fmt: skip


def complete(states: pl.DataFrame) -> pl.DataFrame:
    """Add what follows from the state when missing: ``is_fourth_down`` (from ``down``),
    ``goal_to_go`` (the line to gain is the goal line: ``ydstogo`` >= ``yardline_100``) and
    the era flags (from ``season``)."""
    s = states
    if "is_fourth_down" not in s.columns:
        s = s.with_columns((pl.col("down") == 4).cast(pl.Int8).alias("is_fourth_down"))
    if "goal_to_go" not in s.columns:
        s = s.with_columns((pl.col("ydstogo") >= pl.col("yardline_100")).cast(pl.Int8)
                           .alias("goal_to_go"))  # fmt: skip
    if "era_pat_2015" not in s.columns or "era_kickoff_2023" not in s.columns:
        s = s.with_columns(sm.era_flags())
    return s


def build_rows(plays: pl.DataFrame) -> tuple[pl.DataFrame, dict[str, int]]:
    """Model rows (KEYS, FEATURES, LABEL and the outcome columns of the ball-spot tables) from
    :func:`submodels.load_plays` rows, and the rows each filter dropped."""
    valid = (pl.col("posteam").is_not_null() & pl.col("defteam").is_not_null()
             & (pl.col("ydstogo") >= 1) & pl.col("yardline_100").is_between(1, 99)
             & pl.col("score_differential").is_not_null()
             & (pl.col("game_seconds_remaining") >= 0))  # fmt: skip
    rows, drops = sm.count_filters(plays, [
        ("not_third_or_fourth_down", pl.col("down").is_in([3, 4])),
        ("not_a_pass_or_run", pl.col("play_type").is_in(["pass", "run"])),
        ("missing_state", valid),
    ])  # fmt: skip
    off_td = (pl.col("touchdown") == 1) & (pl.col("td_team") == pl.col("posteam"))
    def_td = (pl.col("touchdown") == 1) & (pl.col("td_team") == pl.col("defteam"))
    kept = (pl.col("interception") != 1) & (pl.col("fumble_lost") != 1)
    got = (pl.col("first_down") == 1) | off_td
    rows = complete(rows.with_columns(sm.posteam_spread())).with_columns(
        (got.fill_null(False) & kept.fill_null(True)).cast(pl.Int8).alias(LABEL),
        off_td.fill_null(False).alias("off_td"),
        def_td.fill_null(False).alias("def_td"),
        (pl.col("n_game_half") == pl.col("game_half")).fill_null(False).alias("same_half"),
    )
    cols = [*sm.KEYS, "week", "down", *FEATURES, LABEL, "posteam", "defteam", "off_td",
            "def_td", "same_half", "n_posteam", "n_yardline_100", "yards_gained", "game_half",
            "half_seconds_remaining", "n_game_seconds_remaining", "n_game_half"]  # fmt: skip
    return rows.select(list(dict.fromkeys(cols))).sort(list(sm.KEYS)), drops


def _band(col: str, bounds: tuple[int, ...]) -> pl.Expr:
    e = pl.lit(len(bounds), dtype=pl.Int8)
    for i in reversed(range(len(bounds))):
        e = pl.when(pl.col(col) <= bounds[i]).then(pl.lit(i, dtype=pl.Int8)).otherwise(e)
    return e


def outcome_tables(train: pl.DataFrame) -> dict[str, Any]:
    """The ball-spot distributions (see the module docstring) from training rows: per
    (yds band, zone) cell the counts of each outcome value. ``success``: value = yards from the
    line of scrimmage to the offense's next snap (= ``yardline_100`` for a touchdown).
    ``failure``: value = yards the offense gained before losing the ball on downs (the
    opponent's spot is 100 - yardline_100 + value), or the opponent's touchdown."""
    t = train.with_columns(_band("ydstogo", YDS_BANDS).alias("yb"),
                           _band("yardline_100", ZONES).alias("zb"))  # fmt: skip
    succ = t.filter(pl.col(LABEL) == 1).with_columns(
        pl.when(pl.col("off_td")).then(pl.col("yardline_100"))
        .when(pl.col("same_half") & (pl.col("n_posteam") == pl.col("posteam")))
        .then(pl.col("yardline_100") - pl.col("n_yardline_100"))
        .alias("value"), pl.col("off_td").alias("td"),
    ).filter(pl.col("value").is_not_null())  # fmt: skip
    fail = t.filter((pl.col(LABEL) == 0) & (pl.col("down") == 4)).with_columns(
        pl.when(pl.col("def_td")).then(0)
        .when(pl.col("same_half") & (pl.col("n_posteam") == pl.col("defteam")))
        .then(pl.col("n_yardline_100") - (100 - pl.col("yardline_100")))
        .alias("value"), pl.col("def_td").alias("td"),
    ).filter(pl.col("value").is_not_null())  # fmt: skip

    def counts(f: pl.DataFrame) -> pl.DataFrame:
        return f.group_by("yb", "zb", "td", "value").len("n").sort("yb", "zb", "td", "value")

    return {"success": counts(succ), "failure": counts(fail),
            "runoff_seconds": sm.median_runoff(t), "min_cell": MIN_CELL}  # fmt: skip


def _cell_distributions(counts: pl.DataFrame, min_cell: int) -> pl.DataFrame:
    """(yb, zb, td, value, prob) for EVERY band x zone cell: the cell's own rows when it has
    at least ``min_cell``, else its zone's pooled rows, else all rows."""
    out = []
    zone = counts.group_by("zb", "td", "value").agg(pl.col("n").sum())
    every = counts.group_by("td", "value").agg(pl.col("n").sum())
    for yb in range(len(YDS_BANDS) + 1):
        for zb in range(len(ZONES) + 1):
            own = counts.filter((pl.col("yb") == yb) & (pl.col("zb") == zb)).select(
                "td", "value", "n"
            )
            if own.get_column("n").sum() < min_cell:
                own = zone.filter(pl.col("zb") == zb).select("td", "value", "n")
            if own.get_column("n").sum() < min_cell:
                own = every.select("td", "value", "n")
            total = own.get_column("n").sum()
            out.append(own.sort("td", "value").with_columns(
                pl.lit(yb, pl.Int8).alias("yb"), pl.lit(zb, pl.Int8).alias("zb"),
                (pl.col("n") / max(total, 1)).alias("prob")).drop("n"))  # fmt: skip
    return pl.concat(out)


def _spread(states: pl.DataFrame, model: sm.SubModel, kind: str) -> pl.DataFrame:
    tables = model.extras
    if kind not in tables:
        raise sm.SubModelError(f"{model.version} has no {kind} table")
    dist = _cell_distributions(tables[kind], int(tables.get("min_cell", MIN_CELL)))
    s = (
        states.select("ydstogo", "yardline_100")
        .with_row_index("row")
        .with_columns(_band("ydstogo", YDS_BANDS).alias("yb"),
                      _band("yardline_100", ZONES).alias("zb"))
        .drop("ydstogo")
    )  # fmt: skip
    return s.join(dist, on=["yb", "zb"], how="left")


def after_success(states: pl.DataFrame, model: sm.SubModel) -> pl.DataFrame:
    """Where a conversion leaves the offense, per state (``row`` = its position in
    ``states``): ``outcome`` 'touchdown' or 'first_down' at ``yardline_100`` (the offense's new
    spot, NULL for a touchdown) with ``prob``; each row's probabilities sum to 1."""
    j = _spread(states, model, "success")
    td = pl.col("td") | (pl.col("value") >= pl.col("yardline_100"))
    return (
        j.with_columns(
            pl.when(td).then(pl.lit("touchdown")).otherwise(pl.lit("first_down")).alias("outcome"),
            pl.when(td).then(None).otherwise((pl.col("yardline_100") - pl.col("value"))
                                             .clip(1, 99)).alias("new_yardline_100"),
        )
        .group_by("row", "outcome", "new_yardline_100").agg(pl.col("prob").sum())
        .rename({"new_yardline_100": "yardline_100"})
        .sort("row", "outcome", "yardline_100", nulls_last=True)
    )  # fmt: skip


def after_failure(states: pl.DataFrame, model: sm.SubModel) -> pl.DataFrame:
    """Where a failed FOURTH-down try leaves the opponent, per state: ``outcome`` 'opp_ball'
    at ``opp_yardline_100`` (the opponent's first-down spot, its own view) or 'opp_touchdown'
    (a return), with ``prob``; each row's probabilities sum to 1."""
    j = _spread(states, model, "failure")
    return (
        j.with_columns(
            pl.when(pl.col("td")).then(pl.lit("opp_touchdown")).otherwise(pl.lit("opp_ball"))
            .alias("outcome"),
            pl.when(pl.col("td")).then(None)
            .otherwise((100 - pl.col("yardline_100") + pl.col("value")).clip(1, 99))
            .alias("opp_yardline_100"),
        )
        .group_by("row", "outcome", "opp_yardline_100").agg(pl.col("prob").sum())
        .sort("row", "outcome", "opp_yardline_100", nulls_last=True)
    )  # fmt: skip


def p_convert(states: pl.DataFrame, model: sm.SubModel) -> np.ndarray:
    """P(convert) per row of ``states`` (a play-state frame like G1's: down, ydstogo,
    yardline_100, season, score_differential, game_seconds_remaining, posteam_spread; the
    derived columns are added by :func:`complete`). Refuses missing columns and bad values."""
    s = complete(states)
    missing = [c for c in model.features if c not in s.columns]
    if missing:
        raise sm.SubModelError(f"states lack columns {missing}")
    bad = s.filter(~((pl.col("ydstogo") >= 1) & pl.col("yardline_100").is_between(1, 99))).height
    if bad:
        raise sm.SubModelError(f"{bad} state(s) have distance < 1 or a yardline outside 1-99")
    return model.probability(s)


OUTCOME_COLUMNS = ("down", "posteam", "defteam", "off_td", "def_td", "same_half", "n_posteam",
                   "n_yardline_100", "game_half", "n_game_half",
                   "n_game_seconds_remaining")  # fmt: skip
SPEC = sm.BinarySpec(
    MODEL,
    LABEL,
    FEATURES,
    estimator,
    extras=outcome_tables,
    extra_columns=OUTCOME_COLUMNS,
    windows=WINDOWS,
)


def fit_fold(rows: pl.DataFrame, season: int, *, progress=print) -> sm.FoldOutput:
    """One walk-forward fold (rows of seasons <= ``season``; learns from seasons < it)."""
    return sm.run_binary_fold(rows, season, SPEC, progress=progress)
