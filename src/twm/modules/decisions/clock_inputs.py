"""The inputs of the clock-management metrics (PROJECT_SPEC 8.4 item 4; step G4).

Everything :mod:`twm.modules.decisions.clock` needs from the typed warehouse (read-only),
point-in-time for the graded season S (every rule: docs/decision_metrics.md, "Clock
management"):

- :func:`load_season`: the snap rows of S (rows with a down: scrimmage plays and ``no_play``
  penalty snaps) with each game's final result, overtime flag, spread, venue and head coaches.
- :func:`add_next_snap`: each snap's next snap in the same game (clock, down, offense, drive,
  both teams' timeouts) and the drive/game end flags the metrics read.
- :func:`measure_constants`: the kneel arithmetic's ``kneel_play`` (p) and ``kneel_cycle``
  (g) and the run / pass play seconds, medians over the seasons before S.

Timeouts are read from the pre-snap ``*_timeouts_remaining`` of consecutive snaps (a count
that went down = a timeout between them), never from where the timeout rows sit: in the
warehouse a timeout row often comes after the snap it preceded (checked on 2015 and 2025).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl

from twm.modules.decisions import grade_inputs as gi

SCRIMMAGE = ("pass", "run", "punt", "field_goal", "qb_kneel", "qb_spike")
_PLAY_COLS = ("game_id", "play_id", "season", "week", "season_type", "game_date", "qtr",
              "game_half", "down", "ydstogo", "yardline_100", "game_seconds_remaining",
              "half_seconds_remaining", "score_differential", "posteam_timeouts_remaining",
              "defteam_timeouts_remaining", "posteam", "defteam", "home_team", "away_team",
              "play_type", "qb_dropback", "qb_scramble", "fixed_drive", "fixed_drive_result",
              "desc")  # fmt: skip
_GAME_COLS = ("result", "spread_line", "location", "home_coach", "away_coach")


def _snap_sql(where: str) -> str:
    p = ", ".join(f'p."{c}"' for c in _PLAY_COLS)
    g = ", ".join(f"g.{c}" for c in _GAME_COLS)
    return (f"SELECT {p}, {g} FROM w.fact_play p JOIN w.fact_game g USING (game_id) "
            f"WHERE p.down IS NOT NULL AND {where} ORDER BY p.game_id, p.play_id")  # fmt: skip


def load_season(db: Path | str, season: int) -> pl.DataFrame:
    """Every snap (row with a down) of ``season`` with its game's result and coaches, sorted
    by game and play, plus ``overtime`` (the game has a snap after the fourth quarter)."""
    f = gi._query(db, _snap_sql(f"p.season = {int(season)}"))
    ot = f.group_by("game_id").agg((pl.col("qtr").max() > 4).alias("overtime"))
    return f.join(ot, on="game_id", how="left").sort("game_id", "play_id")


def add_next_snap(snaps: pl.DataFrame) -> pl.DataFrame:
    """``n_*``: the next snap of the same game (NULL for the game's last snap); ``team_t`` /
    ``n_team_t``: the DEFENSE's timeouts at this snap and at the next one (read from whichever
    side it is on next); ``drive_last``: the last snap of this offense's drive;
    ``drive_last_team_t``: the defense's timeouts at that last snap; ``game_last_drive``:
    this drive holds the game's last snap."""
    w = {"partition_by": "game_id", "order_by": "play_id"}
    nxt = [pl.col(c).shift(-1).over(**w).alias(f"n_{c}") for c in (
        "game_seconds_remaining", "down", "posteam", "fixed_drive", "game_half", "play_type",
        "posteam_timeouts_remaining", "defteam_timeouts_remaining")]  # fmt: skip
    f = snaps.with_columns(nxt)
    same = pl.col("n_posteam") == pl.col("posteam")
    f = f.with_columns(
        pl.col("defteam_timeouts_remaining").alias("team_t"),
        pl.when(same).then(pl.col("n_defteam_timeouts_remaining"))
        .otherwise(pl.col("n_posteam_timeouts_remaining")).alias("n_team_t"),
        (same & (pl.col("n_fixed_drive") == pl.col("fixed_drive"))
         & (pl.col("n_game_half") == pl.col("game_half"))).fill_null(False).alias("n_same_drive"),
    )  # fmt: skip
    drive = ["game_id", "fixed_drive", "posteam"]
    f = f.with_columns(
        (~pl.col("n_same_drive")).alias("drive_last"),
        pl.col("team_t").last().over(drive, order_by="play_id").alias("drive_last_team_t"),
    )
    last = f.group_by("game_id").agg(pl.col("fixed_drive", "posteam").sort_by("play_id").last())
    last = last.with_columns(pl.lit(True).alias("game_last_drive"))
    return f.join(last, on=["game_id", "fixed_drive", "posteam"], how="left").with_columns(
        pl.col("game_last_drive").fill_null(False)).sort("game_id", "play_id")  # fmt: skip


CONSTANTS = ("kneel_play", "kneel_cycle", "play_seconds_run", "play_seconds_pass")


def constants_from_snaps(snaps: pl.DataFrame, late_seconds: int = 120) -> dict[str, Any]:
    """The medians of docs/decision_metrics.md from second- and fourth-quarter snaps
    (:func:`add_next_snap` columns): seconds from a snap to the same offense's next snap in the
    same half (both real scrimmage snaps) when only the defense's timeouts went down by one in
    between (``kneel_play``, ``play_seconds_run``, ``play_seconds_pass``: runs and passes in the
    half's last ``late_seconds``), or when neither team's did (``kneel_cycle``); ``n_*`` the
    pairs behind each."""
    pair = (pl.col("qtr").is_in([2, 4]) & (pl.col("n_posteam") == pl.col("posteam"))
            & (pl.col("n_game_half") == pl.col("game_half"))
            & pl.col("play_type").is_in(SCRIMMAGE)
            & pl.col("n_play_type").is_in(SCRIMMAGE))  # fmt: skip
    own = pl.col("n_posteam_timeouts_remaining") == pl.col("posteam_timeouts_remaining")
    dt = pl.col("n_defteam_timeouts_remaining") - pl.col("defteam_timeouts_remaining")
    f = snaps.filter(pair.fill_null(False) & own.fill_null(False)).with_columns(
        (pl.col("game_seconds_remaining") - pl.col("n_game_seconds_remaining")).alias("gap")
    )
    late = pl.col("half_seconds_remaining") <= late_seconds
    cells = {"kneel_play": (pl.col("play_type") == "qb_kneel") & (dt == -1),
             "kneel_cycle": (pl.col("play_type") == "qb_kneel") & (dt == 0),
             "play_seconds_run": (pl.col("play_type") == "run") & late & (dt == -1),
             "play_seconds_pass": (pl.col("play_type") == "pass") & late & (dt == -1)}  # fmt: skip
    out: dict[str, Any] = {}
    for k, cond in cells.items():
        g = f.filter(cond.fill_null(False)).get_column("gap")
        out[k] = float(g.median()) if g.len() else None
        out[f"n_{k}"] = int(g.len())
    return out


def measure_constants(db: Path | str, season: int, n_seasons: int) -> dict[str, Any]:
    """:func:`constants_from_snaps` on the ``n_seasons`` seasons before ``season`` (never S)."""
    ss = list(range(int(season) - int(n_seasons), int(season)))
    where = f"p.season IN ({', '.join(map(str, ss))}) AND p.qtr IN (2, 4)"
    snaps = add_next_snap(gi._query(db, _snap_sql(where)))
    out = constants_from_snaps(snaps)
    missing = [k for k in CONSTANTS if out[k] is None]
    if missing:
        raise ValueError(f"cannot measure {missing} on {ss}: no snaps")
    return {**out, "constant_seasons": ss}


CONTEXT = ("week", "season_type", "game_date", "qtr", "home_team", "away_team", "desc")
WINDOW_INPUTS = ("fixed_drive", "game_seconds_remaining", "down", "ydstogo", "yardline_100",
                 "score_differential", "team_t", "play_type", "n_game_seconds_remaining",
                 "n_down", "n_team_t", "n_same_drive", "drive_last", "drive_last_team_t",
                 "game_last_drive", "team_margin", "overtime")  # fmt: skip


def _coach(team: str) -> pl.Expr:
    home = pl.col(team) == pl.col("home_team")
    return pl.when(home).then(pl.col("home_coach")).otherwise(pl.col("away_coach"))


def defense_window(snaps: pl.DataFrame, consts: dict[str, Any], cfg: Any) -> pl.DataFrame:
    """Metrics 1 and 3's stored rows: every snap (:func:`add_next_snap` columns) in the fourth
    quarter with at most ``cfg.final_window_seconds`` left while the offense led, seen from
    the DEFENSE (``team``, its coach, its timeouts ``team_t``, its final margin), with the
    measured constants and the config values the metrics read."""
    home = pl.col("defteam") == pl.col("home_team")
    f = snaps.filter((pl.col("qtr") == 4) & (pl.col("score_differential") >= 1)
                     & (pl.col("game_seconds_remaining") <= cfg.final_window_seconds))  # fmt: skip
    f = f.with_columns(
        pl.col("defteam").alias("team"), pl.col("posteam").alias("opp"),
        _coach("defteam").alias("coach"), _coach("posteam").alias("opp_coach"),
        pl.when(home).then(pl.col("result")).otherwise(-pl.col("result")).alias("team_margin"),
        *(pl.lit(consts[k], pl.Float64).alias(k) for k in CONSTANTS),
        pl.lit(int(cfg.final_window_seconds)).alias("final_window_seconds"),
        pl.lit(int(cfg.one_score_margin)).alias("one_score_margin"),
        pl.lit(int(cfg.clock_ran_min_seconds)).alias("clock_ran_min_seconds"),
    )  # fmt: skip
    keep = [
        *gi.KEYS,
        *CONTEXT,
        "team",
        "opp",
        "coach",
        "opp_coach",
        *WINDOW_INPUTS,
        *CONSTANTS,
        "final_window_seconds",
        "one_score_margin",
        "clock_ran_min_seconds",
    ]
    return f.select(keep).sort(list(gi.KEYS))


def opening_kickers(db: Path | str, season: int) -> pl.DataFrame:
    """game_id -> ``opening_kicker`` (wp_data's rule) from the season's first-quarter kickoffs."""
    from twm.modules.decisions import wp_data

    sql = (
        "SELECT game_id, play_id, qtr, kickoff_attempt, defteam FROM w.fact_play "
        f"WHERE season = {int(season)} AND kickoff_attempt = 1 AND qtr = 1"
    )
    k = gi._query(db, sql)
    return wp_data.opening_kickers(k)


PASSIVE_TYPES = ("qb_kneel", "run")


def _passive() -> pl.Expr:
    """A kneel, a designed run (no dropback, no scramble) or a penalty snap (``no_play``)."""
    dropped = (pl.col("qb_dropback").fill_null(0) == 1) | (pl.col("qb_scramble").fill_null(0) == 1)
    pt = pl.col("play_type")
    return (pt == "qb_kneel") | ((pt == "run") & ~dropped) | (pt == "no_play")


def half_end_candidates(snaps: pl.DataFrame, cfg: Any) -> pl.DataFrame:
    """Metric 2's candidates: per first half whose last snap's drive ended the half
    (``fixed_drive_result`` = 'End of half'), the decision snap of the drive's passive tail
    (docs/decision_metrics.md) with ``tail_snaps`` / ``tail_kneels`` / ``tail_runs`` (from the
    decision snap to the half's end) and ``half_end_seconds`` (the clock at the last snap).
    Halves without a decision snap are not candidates."""
    h1 = snaps.filter(pl.col("game_half") == "Half1").sort("game_id", "play_id")
    last = h1.group_by("game_id").agg(
        pl.col("fixed_drive", "posteam", "fixed_drive_result", "posteam_timeouts_remaining",
               "half_seconds_remaining").sort_by("play_id").last()
    ).filter(pl.col("fixed_drive_result") == "End of half")  # fmt: skip
    last = last.select("game_id", "fixed_drive", "posteam",
                       pl.col("posteam_timeouts_remaining").alias("end_t"),
                       pl.col("half_seconds_remaining").alias("half_end_seconds"))  # fmt: skip
    d = h1.join(last, on=["game_id", "fixed_drive", "posteam"], how="inner").sort(
        "game_id", "play_id"
    )
    ok = (_passive() & (pl.col("posteam_timeouts_remaining") == pl.col("end_t"))).fill_null(False)
    d = d.with_columns(ok.cast(pl.Int8).reverse().cum_min().reverse().over("game_id")
                       .alias("in_tail"))  # fmt: skip
    tail = d.filter(pl.col("in_tail") == 1)
    pt = pl.col("play_type")
    real = pt.is_in(PASSIVE_TYPES)
    can = (real & (pl.col("down") == 1)
           & (pl.col("half_seconds_remaining") >= cfg.passivity_min_seconds)
           & (pl.col("posteam_timeouts_remaining") >= cfg.passivity_min_timeouts))  # fmt: skip
    can = can.fill_null(False)
    tail = tail.with_columns(can.cast(pl.Int8).cum_max().over("game_id").alias("after"))
    t = tail.filter(pl.col("after") == 1)
    agg = t.group_by("game_id").agg(
        pl.len().alias("tail_snaps"), (pt == "qb_kneel").sum().alias("tail_kneels"),
        (pt == "run").sum().alias("tail_runs"),
        pl.col("play_id").filter(can).first().alias("play_id"),
    )  # fmt: skip
    return t.join(agg, on=["game_id", "play_id"], how="inner").sort("game_id", "play_id")
