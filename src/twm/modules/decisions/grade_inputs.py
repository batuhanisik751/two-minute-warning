"""The inputs of the fourth-down and two-point grades (PROJECT_SPEC 8.4 items 2, 3, 5; step G3).

Everything a grade needs besides the fold models (:mod:`twm.modules.decisions.grade`), all
point-in-time for the graded season S:

- :func:`load_season`: the play rows of S (typed warehouse, read-only) with each game's
  closing spread, venue and head coaches (``fact_game.home_coach`` / ``away_coach``).
- :func:`fourth_down_rows` / :func:`try_rows`: the candidate decisions, every exclusion rule
  applied in order and recorded in ``exclusion`` (NULL = graded), with the state of G1's WP
  model, the field-goal conditions and the head coach of the team with the ball.
- :func:`kickoff_spots`: where the receiving team starts after a score, per game: the mean
  start of the same season's kickoffs in EARLIER weeks (at least ``kickoff_min_kicks``), else
  the previous season's (measured, never typed: the kickoff rules changed in 2024 and 2025).
- :func:`ranges`: when the field-goal and punt options exist (the longest made field goal
  and a low quantile of punting yardlines in the seasons before S).
- :func:`runoffs`: the field goal's snap-to-snap seconds (make / miss) in the 5 seasons before S.
- :func:`platt`: the fourth-down Platt recalibration of P(convert), fitted on the conversion
  model's out-of-sample fourth downs of earlier seasons and kept only when it helps on S-1.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from twm.modules.decisions import fieldgoal
from twm.modules.decisions import submodels as sm

KEYS = sm.KEYS
FOURTH_RULES = ("not_a_snap", "penalty_no_play", "kneel_or_spike", "aborted_snap",
                "end_of_half", "missing_state")  # fmt: skip
TRY_RULES = ("penalty_no_play", "aborted_snap", "missing_state")
WP_STATE = (*sm.STATE_INPUTS, "down", "ydstogo", "yardline_100")
FG_STATE = ("fg_distance", "roof_closed", "temp_f", "wind_mph", "surface_grass",
            "weather_missing")  # fmt: skip
CONTEXT = ("week", "season_type", "game_date", "qtr", "posteam", "defteam", "home_team",
           "away_team", "coach", "opp_coach", "play_type", "desc")  # fmt: skip
RESULT = ("yards_gained", "first_down", "touchdown", "td_team", "interception", "fumble_lost",
          "field_goal_result", "extra_point_result", "two_point_conv_result")  # fmt: skip
TRY_WINDOW = 5  # seasons before S for the field goal's runoff (as the try rates)

_PLAY_COLS = ("game_id", "play_id", "season", "week", "season_type", "game_date", "qtr",
              "game_half", "down", "ydstogo", "yardline_100", "goal_to_go",
              "game_seconds_remaining", "half_seconds_remaining", "score_differential",
              "posteam_timeouts_remaining", "defteam_timeouts_remaining", "posteam", "defteam",
              "home_team", "away_team", "play_type", "aborted_play", "kickoff_attempt",
              "extra_point_attempt", "two_point_attempt", *RESULT, "weather", "desc")  # fmt: skip
_GAME_COLS = ("game_id", "spread_line", "location", "roof", "surface", "temp", "wind",
              "home_coach", "away_coach")  # fmt: skip


def _query(db: Path | str, sql: str) -> pl.DataFrame:
    import duckdb

    con = duckdb.connect()
    try:
        con.execute(f"ATTACH '{Path(db)}' AS w (READ_ONLY)")
        return con.sql(sql).pl()
    finally:
        con.close()


def load_season(db: Path | str, season: int) -> pl.DataFrame:
    """Every play row of ``season`` with its game's spread, venue, conditions and coaches,
    sorted by game and play."""
    p = ", ".join(f'p."{c}"' for c in _PLAY_COLS)
    g = ", ".join(f"g.{c}" for c in _GAME_COLS if c != "game_id")
    return _query(db, f"SELECT {p}, {g} FROM w.fact_play p JOIN w.fact_game g USING (game_id) "
                      f"WHERE p.season = {int(season)} ORDER BY p.game_id, p.play_id")  # fmt: skip


SNAP_TYPES = ("pass", "run", "punt", "field_goal", "qb_kneel", "qb_spike", "no_play")
CHOICE = {"pass": "go", "run": "go", "field_goal": "field_goal", "punt": "punt"}


def _states(rows: pl.DataFrame, plays: pl.DataFrame) -> pl.DataFrame:
    """G1's state inputs (half, second-half kickoff, home, spread, era), the field-goal
    conditions and both head coaches, for candidate ``rows`` (a subset of ``plays``)."""
    from twm.modules.decisions import wp_data

    home = pl.col("posteam") == pl.col("home_team")
    f = wp_data.add_features(rows.join(wp_data.opening_kickers(plays), on="game_id", how="left"))
    return fieldgoal.add_features(f).with_columns(
        pl.when(home).then(pl.col("home_coach")).otherwise(pl.col("away_coach")).alias("coach"),
        pl.when(home).then(pl.col("away_coach")).otherwise(pl.col("home_coach"))
        .alias("opp_coach"),
    )  # fmt: skip


def _valid() -> pl.Expr:
    """Every WP input present and in the WP model's range, a coach to credit."""
    ok = (pl.col("posteam") == pl.col("home_team")) | (pl.col("posteam") == pl.col("away_team"))
    for c in (*WP_STATE, "coach"):
        ok = ok & pl.col(c).is_not_null()
    return (ok & pl.col("down").is_between(1, 4) & (pl.col("ydstogo") >= 1)
            & pl.col("yardline_100").is_between(1, 99)
            & pl.col("posteam_timeouts_remaining").is_between(0, 3)
            & pl.col("defteam_timeouts_remaining").is_between(0, 3)
            & (pl.col("half_seconds_remaining") >= 0))  # fmt: skip


def _first_rule(rules: Sequence[tuple[str, pl.Expr]]) -> pl.Expr:
    """The name of the first rule whose condition holds (NULL = none: the row is graded)."""
    e = pl.lit(None, pl.String)
    for name, cond in reversed(rules):
        e = pl.when(cond.fill_null(False)).then(pl.lit(name)).otherwise(e)
    return e.alias("exclusion")


def fourth_down_rows(plays: pl.DataFrame, end_of_half_seconds: int) -> pl.DataFrame:
    """Every fourth-down row of ``plays`` (one season, :func:`load_season`) with its state,
    ``chosen`` option (go / field_goal / punt; fake punts and fake field goals are pass or
    run plays, so "go") and ``exclusion`` (the first of :data:`FOURTH_RULES` that applies)."""
    rows = _states(plays.filter(pl.col("down") == 4), plays)
    pt = pl.col("play_type")
    rules = [
        ("not_a_snap", ~pt.is_in(SNAP_TYPES) | pt.is_null()),
        ("penalty_no_play", pt == "no_play"),
        ("kneel_or_spike", pt.is_in(["qb_kneel", "qb_spike"])),
        ("aborted_snap", pl.col("aborted_play") == 1),
        ("end_of_half", pl.col("half_seconds_remaining") <= end_of_half_seconds),
        ("missing_state", ~_valid().fill_null(False)),
    ]
    return rows.with_columns(
        _first_rule(rules), pt.replace_strict(CHOICE, default=None).alias("chosen")
    ).sort(list(KEYS))


def try_rows(plays: pl.DataFrame) -> pl.DataFrame:
    """Every try after a touchdown (extra point or two-point attempt) with its state (the
    scoring team has the ball; the score already counts the touchdown), ``chosen`` (kick /
    two_point) and ``exclusion`` (the first of :data:`TRY_RULES` that applies)."""
    tries = plays.filter((pl.col("extra_point_attempt") == 1) | (pl.col("two_point_attempt") == 1))
    rows = _states(tries.with_columns(pl.lit(1, pl.Int32).alias("down"),
                                      pl.lit(10, pl.Int32).alias("ydstogo")), plays)  # fmt: skip
    rules = [
        ("penalty_no_play", pl.col("play_type") == "no_play"),
        ("aborted_snap", pl.col("aborted_play") == 1),
        ("missing_state", ~_valid().fill_null(False)),
    ]
    kick = pl.col("extra_point_attempt") == 1
    return rows.with_columns(
        _first_rule(rules),
        pl.when(kick).then(pl.lit("kick")).otherwise(pl.lit("two_point")).alias("chosen"),
    ).sort(list(KEYS))


def kickoffs(db: Path | str, seasons: Sequence[int]) -> pl.DataFrame:
    """One row per kickoff of ``seasons`` that the receiving team (``posteam`` on a kickoff
    row) fielded: its next snap is in the same half and its own, no touchdown on the kick, not
    an onside kick (``desc`` mentions onside). ``spot`` = that snap's yardline_100 (75 = its
    own 25); ``runoff`` = seconds from the kick to that snap."""
    ss = ", ".join(str(int(s)) for s in seasons) or "NULL"
    cols = ("game_id", "play_id", "season", "week", "play_type", "posteam", "down",
            "yardline_100", "game_half", "game_seconds_remaining", "touchdown")  # fmt: skip
    plays = _query(db, f"SELECT {', '.join(cols)}, coalesce(\"desc\" ILIKE '%onside%', false) "
                       f"AS onside FROM w.fact_play WHERE season IN ({ss}) "
                       "ORDER BY game_id, play_id")  # fmt: skip
    k = sm.add_next_snap(plays).filter(
        (pl.col("play_type") == "kickoff") & (pl.col("n_game_half") == pl.col("game_half"))
        & (pl.col("n_posteam") == pl.col("posteam")) & (pl.col("touchdown") != 1)
        & ~pl.col("onside")
    )  # fmt: skip
    return k.select("season", "week", "game_id", pl.col("n_yardline_100").alias("spot"),
                    (pl.col("game_seconds_remaining") - pl.col("n_game_seconds_remaining"))
                    .alias("runoff"))  # fmt: skip


def kickoff_spots(games: pl.DataFrame, kicks: pl.DataFrame, min_kicks: int) -> pl.DataFrame:
    """Per game of ``games`` (game_id, season, week): ``kick_yardline_100`` = the mean receiving
    spot of the same season's kickoffs in EARLIER weeks when there are at least ``min_kicks``
    of them, else of the whole previous season, rounded to the yard; ``kick_runoff`` = the
    median seconds of the same kickoffs; ``kick_source`` and ``kick_n`` say which. Never reads
    the game's own week or later."""
    out = []
    for season, week, gid in games.select("season", "week", "game_id").unique().sort(
            "season", "week", "game_id").iter_rows():  # fmt: skip
        k = kicks.filter((pl.col("season") == season) & (pl.col("week") < week))
        src = "season_to_date"
        if k.height < min_kicks:
            k, src = kicks.filter(pl.col("season") == season - 1), "previous_season"
        mean = k.get_column("spot").mean() if k.height else None
        out.append({"game_id": gid, "kick_spot_mean": mean, "kick_n": k.height,
                    "kick_runoff": float(k.get_column("runoff").median()) if k.height else None,
                    "kick_source": src})  # fmt: skip
    f = pl.DataFrame(out, schema={"game_id": pl.String, "kick_spot_mean": pl.Float64,
                                  "kick_n": pl.Int64, "kick_runoff": pl.Float64,
                                  "kick_source": pl.String})  # fmt: skip
    return f.with_columns(pl.col("kick_spot_mean").round(0).cast(pl.Int32)
                          .alias("kick_yardline_100"))  # fmt: skip


def ranges(db: Path | str, season: int, punt_quantile: float) -> dict[str, Any]:
    """When the kicking options exist in ``season``, from the seasons before it only:
    ``fg_max_distance`` = the longest made field goal (yards), ``punt_min_yardline`` = the
    ``punt_quantile`` quantile of punting yardlines (punts from closer are not an option)."""
    off = fieldgoal.DISTANCE_OFFSET
    r = _query(db, f"SELECT max(yardline_100 + {off}) FILTER (WHERE play_type = 'field_goal' "
                   "AND field_goal_result = 'made') AS fg, quantile_disc(yardline_100, "
                   f"{float(punt_quantile)}) FILTER (WHERE play_type = 'punt') AS punt "
                   f"FROM w.fact_play WHERE season < {int(season)}")  # fmt: skip
    fg, punt = r.row(0)
    return {"fg_max_distance": int(fg), "punt_min_yardline": int(punt)}


def runoffs(db: Path | str, season: int, window: int = TRY_WINDOW) -> dict[str, Any]:
    """Median seconds from a field-goal snap to the next snap in the same half, after a make
    (the kickoff included) and after a miss or block, in the ``window`` seasons before S."""
    seasons = list(range(season - window, season))
    cols = ("game_id", "play_id", "season", "down", "posteam", "yardline_100", "game_half",
            "game_seconds_remaining", "play_type", "field_goal_result")  # fmt: skip
    plays = _query(db, f"SELECT {', '.join(cols)} FROM w.fact_play WHERE season IN "
                       f"({', '.join(map(str, seasons))}) ORDER BY game_id, play_id")  # fmt: skip
    fg = sm.add_next_snap(plays).filter(
        (pl.col("play_type") == "field_goal") & (pl.col("n_game_half") == pl.col("game_half"))
    ).with_columns((pl.col("game_seconds_remaining") - pl.col("n_game_seconds_remaining"))
                   .alias("d"))  # fmt: skip
    made = pl.col("field_goal_result") == "made"
    return {"fg_runoff_make": float(fg.filter(made).get_column("d").median()),
            "fg_runoff_miss": float(fg.filter(~made).get_column("d").median()),
            "runoff_seasons": seasons}  # fmt: skip


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-z))


def _logit(p: np.ndarray) -> np.ndarray:
    q = np.clip(np.asarray(p, dtype=np.float64), 1e-6, 1 - 1e-6)
    return np.log(q / (1 - q))


def platt_apply(p: np.ndarray, a: float | None, b: float | None) -> np.ndarray:
    """``sigmoid(a + b * logit(p))``; ``p`` unchanged when no recalibration was kept."""
    p = np.asarray(p, dtype=np.float64)
    if a is None or b is None:
        return p
    return _sigmoid(a + b * _logit(p))


def _platt_fit(p: np.ndarray, y: np.ndarray, iters: int = 50) -> tuple[float, float]:
    """Logistic regression of ``y`` on logit(``p``) (intercept a, slope b) by Newton steps
    from (0, 1) (no recalibration): deterministic, no randomness, no regularization."""
    x = np.column_stack([np.ones(len(p)), _logit(p)])
    w = np.array([0.0, 1.0])
    for _ in range(iters):
        q = _sigmoid(x @ w)
        h = x.T @ (x * (q * (1 - q))[:, None]) + 1e-9 * np.eye(2)
        step = np.linalg.solve(h, x.T @ (y - q))
        w = w + step
        if np.max(np.abs(step)) < 1e-12:
            break
    return float(w[0]), float(w[1])


def platt(rows: pl.DataFrame, season: int, n_seasons: int) -> dict[str, Any]:
    """The fourth-down Platt recalibration of P(convert) for ``season``. ``rows``: fourth-down
    conversion rows (``season``, ``converted``) with ``prob`` = the conversion model's
    OUT-OF-SAMPLE prediction (each season scored by its own fold, trained on earlier seasons).
    Fitted on seasons S-1-n .. S-2 and checked on S-1 (log loss vs the raw prediction); KEPT
    only when it helps there, then refit on S-n .. S-1. Season S itself is never read."""
    from twm.modules.decisions.wp import log_loss

    past = rows.filter(pl.col("season") < season)
    have = set(past.get_column("season").to_list())
    val = season - 1
    fit = [s for s in range(val - n_seasons, val) if s in have]
    out: dict[str, Any] = {"kept": False, "a": None, "b": None, "val_season": val,
                           "fit_seasons": fit, "final_seasons": []}  # fmt: skip
    if val not in have or not fit:
        out["reason"] = "no earlier out-of-sample fourth downs to fit and check on"
        return out

    def xy(seasons: Sequence[int]) -> tuple[np.ndarray, np.ndarray]:
        f = past.filter(pl.col("season").is_in(list(seasons)))
        return f.get_column("prob").to_numpy(), f.get_column("converted").to_numpy()

    a, b = _platt_fit(*xy(fit))
    pv, yv = xy([val])
    raw, cal = log_loss(yv, pv), log_loss(yv, platt_apply(pv, a, b))
    out.update(val_logloss_raw=raw, val_logloss_platt=cal, val_n=len(yv))
    if cal < raw:
        final = [s for s in range(season - n_seasons, season) if s in have]
        fa, fb = _platt_fit(*xy(final))
        out.update(kept=True, a=fa, b=fb, final_seasons=final)
    return out


def platt_rows(db: Path | str, season: int, n_seasons: int, folds_dir: Path | None = None):
    """The fourth-down conversion rows of the ``n_seasons + 1`` seasons before ``season`` that
    have a saved conversion fold, with that fold's out-of-sample ``prob``."""
    from twm.modules.decisions import conversion, wp

    seasons = [s for s in range(season - n_seasons - 1, season) if s in wp.fold_seasons()]
    if not seasons:
        return pl.DataFrame(schema={"season": pl.Int32, "converted": pl.Int8, "prob": pl.Float64})
    preds, _ = sm.load_backtest(conversion.MODEL, seasons, folds_dir)
    rows, _ = conversion.build_rows(sm.load_plays(db, seasons))
    return (
        rows.filter(pl.col("is_fourth_down") == 1)
        .join(preds.select(*KEYS, "prob"), on=list(KEYS), how="inner")
        .select("season", conversion.LABEL, "prob")
    )
