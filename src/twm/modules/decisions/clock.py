"""Clock-management metrics (PROJECT_SPEC 8.4 item 4, step G4); definitions, written before
any season was graded, in docs/decision_metrics.md ("Clock management").

1. ``timeouts_unused``: a lost one-score game (regulation) in which the opponent kept the ball
   to the end from a snap where it could run out the clock only because the team did not use
   its timeouts, and the team still held a timeout at the end.
2. ``half_passivity``: a first half the team ended by kneeling or running from a 1st down with
   >= 40 s and >= 1 timeout where attacking was worth >= 1 net point (G1b's ``half_value``);
   EP and WP left on the table.
3. ``timeout_seconds_wasted``: when trailing by one score in the final two minutes, the seconds
   the clock ran after opponent plays in decisive states while the team kept timeouts it never
   used against that drive.

The **kneel arithmetic** :func:`kneel_out` decides when the opponent "can run out the clock".
Each metric's outputs are pure functions of its stored rows (:func:`window_outputs`,
:func:`grade_passivity`), so :func:`verify` recomputes them from the rows alone.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import polars as pl

from twm.modules.decisions import clock_inputs as ci
from twm.modules.decisions import grade_inputs as gi

KEYS = gi.KEYS
CLOCK_FORMAT = 1  # bumped when the stored layout or the metric math changes


def kneel_out(down: pl.Expr, timeouts: pl.Expr | int) -> pl.Expr:
    """K(d, t): the most seconds ``n = 5 - d`` kneels can run off against a defense that uses
    ``t`` timeouts: n x kneel_play + max(0, n - 1 - t) x (kneel_cycle - kneel_play) (columns
    ``kneel_play`` / ``kneel_cycle``). The offense kneels out the clock from T iff T <= K."""
    n = 5 - down
    t = timeouts if isinstance(timeouts, pl.Expr) else pl.lit(timeouts)
    gaps = (n - 1 - t).clip(lower_bound=0)
    return n * pl.col("kneel_play") + gaps * (pl.col("kneel_cycle") - pl.col("kneel_play"))


def _decisive(down: pl.Expr, clock: pl.Expr) -> pl.Expr:
    """At a SNAP with down ``down`` and ``clock`` seconds left: the offense can run out the
    clock unless the team (``team_t`` >= 1 timeouts) uses them: K(d, t) < clock <= K(d, 0)."""
    return ((pl.col("team_t") >= 1) & (kneel_out(down, pl.col("team_t")) < clock)
            & (clock <= kneel_out(down, 0)))  # fmt: skip


def _decisive_after_play(down: pl.Expr, clock: pl.Expr) -> pl.Expr:
    """AFTER a play (the next snap's down ``down``; ``clock`` when the play ended): one more
    gap runs before the next snap, which a timeout called now stops. The offense can run out
    the clock unless the team uses its timeouts: K(d', t - 1) < clock <= K(d', -1) (K with
    t = -1 = every one of the n' gaps runs: K(d', 0) + kneel_cycle - kneel_play)."""
    t = pl.col("team_t")
    return ((t >= 1) & (kneel_out(down, t - 1) < clock)
            & (clock <= kneel_out(down, -1)))  # fmt: skip


def play_seconds() -> pl.Expr:
    """The play's own seconds: kneel_play, play_seconds_run, else play_seconds_pass."""
    pt = pl.col("play_type")
    return (pl.when(pt == "qb_kneel").then(pl.col("kneel_play"))
            .when(pt == "run").then(pl.col("play_seconds_run"))
            .otherwise(pl.col("play_seconds_pass")))  # fmt: skip


WINDOW_OUTPUTS = ("run_out", "interval", "runoff", "decisive", "stopped", "missed_stop",
                  "counted", "seconds_wasted")  # fmt: skip


def window_outputs(rows: pl.DataFrame) -> pl.DataFrame:
    """Metrics 1 and 3 per stored window row (:func:`clock_inputs.defense_window`):
    ``run_out`` (metric 1's run-out snap), and metric 3's ``interval``, ``runoff``,
    ``decisive``, ``stopped``, ``missed_stop``, ``counted`` (among the first k missed stops of
    the drive, k = the team's timeouts at the drive's last snap) and ``seconds_wasted``."""
    clock, s = pl.col("game_seconds_remaining").cast(pl.Float64), play_seconds()
    scrim = pl.col("play_type").is_in(ci.SCRIMMAGE)
    lead = pl.col("score_differential")
    f = rows.sort(list(KEYS)).with_columns(
        (scrim & (lead >= 1) & _decisive(pl.col("down"), clock)).fill_null(False).alias("run_out"),
        (scrim & pl.col("n_same_drive") & lead.is_between(1, pl.col("one_score_margin"))
         & (pl.col("team_t") >= 1)).fill_null(False).alias("interval"),
        pl.when(pl.col("n_same_drive"))
        .then((clock - pl.col("n_game_seconds_remaining") - s).clip(lower_bound=0.0))
        .otherwise(None).alias("runoff"),
        (pl.col("n_team_t") < pl.col("team_t")).fill_null(False).alias("stopped"),
    )  # fmt: skip
    f = f.with_columns(
        (pl.col("interval") & _decisive_after_play(pl.col("n_down"), clock - s)).fill_null(False)
        .alias("decisive"))  # fmt: skip
    f = f.with_columns((pl.col("decisive") & ~pl.col("stopped")
                        & (pl.col("runoff") >= pl.col("clock_ran_min_seconds")))
                       .fill_null(False).alias("missed_stop"))  # fmt: skip
    drive = ["game_id", "team", "fixed_drive"]
    rank = pl.col("missed_stop").cast(pl.Int32).cum_sum().over(drive, order_by="play_id")
    f = f.with_columns((pl.col("missed_stop") & (rank <= pl.col("drive_last_team_t")))
                       .fill_null(False).alias("counted"))  # fmt: skip
    return f.with_columns(pl.when(pl.col("counted")).then(pl.col("runoff")).otherwise(0.0)
                          .alias("seconds_wasted"))  # fmt: skip


GAME = ("season", "week", "season_type", "game_id", "team", "opp", "coach", "opp_coach")


def timeouts_unused(win: pl.DataFrame) -> pl.DataFrame:
    """Metric 1, one row per CANDIDATE team-game (window rows with :func:`window_outputs`):
    regulation loss by 1..one_score_margin, the opponent's drive held the game's last snap and
    had a run-out snap. ``timeouts_left`` = the team's timeouts at that drive's last snap;
    ``case`` = timeouts_left >= 1. Context: the first run-out snap, the last snap, and metric
    3's counted missed stops and seconds wasted on that drive (could the timeouts kept have
    saved anything?)."""
    f = win.filter(pl.col("game_last_drive") & ~pl.col("overtime")
                   & pl.col("team_margin").is_between(-pl.col("one_score_margin"), -1))  # fmt: skip
    first = pl.col("run_out").arg_true().first()
    g = f.sort(list(KEYS)).group_by(GAME).agg(
        pl.col("run_out").any().alias("has_run_out"),
        pl.col("team_margin").first(), pl.col("drive_last_team_t").first().alias("timeouts_left"),
        *(pl.col(c).gather(first).first().alias(f"ro_{c}") for c in (
            "play_id", "game_seconds_remaining", "down", "ydstogo", "yardline_100",
            "score_differential", "team_t", "desc")),
        kneel_out(pl.col("down"), 0).gather(first).first().alias("ro_k_free"),
        kneel_out(pl.col("down"), pl.col("team_t")).gather(first).first().alias("ro_k_all"),
        pl.col("counted").sum().alias("drive_missed_stops"),
        pl.col("seconds_wasted").sum().alias("drive_seconds_wasted"),
        pl.col("play_id").last().alias("last_play_id"),
        pl.col("game_seconds_remaining").last().alias("last_clock"),
        pl.col("desc").last().alias("last_desc"),
    )  # fmt: skip
    return g.filter(pl.col("has_run_out")).drop("has_run_out").with_columns(
        (pl.col("timeouts_left") >= 1).alias("case")).sort("season", "game_id", "team")  # fmt: skip


def seconds_wasted(win: pl.DataFrame) -> pl.DataFrame:
    """Metric 3, one row per team-game with at least one decisive interval: decisive
    intervals, missed stops, counted ones, ``seconds_wasted`` (sum over the opponent's drives),
    the timeouts the team still held at the end of those drives, ``case`` = seconds > 0, and
    the worst counted interval's context."""
    f = win.filter(pl.col("decisive"))
    worst = pl.col("seconds_wasted").arg_max()
    kept = pl.col("drive_last_team_t").sum().alias("timeouts_kept")
    drives = (f.group_by([*GAME, "fixed_drive"]).agg(pl.col("drive_last_team_t").first())
              .group_by(GAME).agg(kept))  # fmt: skip
    g = f.sort(list(KEYS)).group_by(GAME).agg(
        pl.len().alias("decisive"), pl.col("missed_stop").sum().alias("missed_stops"),
        pl.col("counted").sum().alias("counted"), pl.col("seconds_wasted").sum(),
        pl.col("team_margin").first(),
        *(pl.col(c).gather(worst).first().alias(f"w_{c}") for c in (
            "play_id", "game_seconds_remaining", "down", "ydstogo", "yardline_100",
            "score_differential", "team_t", "runoff", "desc")),
    )  # fmt: skip
    return g.join(drives, on=list(GAME), how="left").with_columns(
        (pl.col("seconds_wasted") > 0).alias("case")).sort("season", "game_id", "team")  # fmt: skip


# --------------------------------------------------------------------------------------
# Metric 2: end-of-half passivity
# --------------------------------------------------------------------------------------

PASSIVITY_INPUTS = (*gi.WP_STATE, "kick_yardline_100", "kick_runoff", "passivity_min_ep")
PASSIVITY_OUTPUTS = ("ep_left", "wp_attack", "wp_halftime", "wp_left", "case")
HALF_SECONDS = 1800


def halftime_states(rows: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """The second half's first snap, same score, 3 timeouts each, ``kick_runoff`` seconds in:
    (the team receiving at the kickoff spot, the opponent receiving there) - each row's team
    WP is wp(first) when it receives the second-half kickoff, else 1 - wp(second)."""
    from twm.modules.decisions import submodels as sm

    left = (HALF_SECONDS - pl.col("kick_runoff")).clip(lower_bound=0.0)
    f = rows.select(gi.WP_STATE + ("kick_yardline_100", "kick_runoff")).with_columns(
        left.alias("game_seconds_remaining"), left.alias("half_seconds_remaining"),
        pl.lit(2, pl.Int8).alias("half_number"), pl.lit(0, pl.Int8).alias("receives_2h_kickoff"),
        *(pl.lit(3).alias(f"{side}_timeouts_remaining") for side in ("posteam", "defteam")),
    )  # fmt: skip
    kick = pl.col("kick_yardline_100")
    return sm.first_down_at(f, kick), sm.first_down_at(sm.other_side(f), kick)


def grade_passivity(rows: pl.DataFrame, model) -> pl.DataFrame:
    """Metric 2's outputs for candidate rows (:data:`PASSIVITY_INPUTS`) with season S's WP
    model: ``ep_left`` = half_value at the decision snap (kneeling scores 0), ``wp_attack`` =
    wp(decision snap), ``wp_halftime`` = the team's WP at the second half's start,
    ``wp_left`` = the difference, ``case`` = ep_left >= passivity_min_ep."""
    import numpy as np

    from twm.modules.decisions import wp as wpm
    from twm.modules.decisions.wp_data import half_value

    if rows.height == 0:
        return pl.DataFrame(schema={c: pl.Float64 for c in PASSIVITY_OUTPUTS[:-1]}
                            | {"case": pl.Boolean})  # fmt: skip
    ep = half_value(rows.get_column("yardline_100").to_numpy(),
                    rows.get_column("half_seconds_remaining").to_numpy())  # fmt: skip
    attack = wpm.wp(rows.select(gi.WP_STATE), model)
    own, other = halftime_states(rows)
    recv = rows.get_column("receives_2h_kickoff").to_numpy() == 1
    half = np.where(recv, wpm.wp(own.select(gi.WP_STATE), model),
                    1.0 - wpm.wp(other.select(gi.WP_STATE), model))  # fmt: skip
    return pl.DataFrame({"ep_left": ep, "wp_attack": attack, "wp_halftime": half,
                         "wp_left": attack - half,
                         "case": ep >= rows.get_column("passivity_min_ep").to_numpy()})  # fmt: skip


PASSIVITY_CONTEXT = ("week", "season_type", "game_date", "qtr", "posteam", "defteam",
                     "home_team", "away_team", "coach", "opp_coach", "play_type", "desc",
                     "tail_snaps", "tail_kneels", "tail_runs", "half_end_seconds",
                     "kick_source", "kick_n")  # fmt: skip


def passivity_rows(cand: pl.DataFrame, kickers: pl.DataFrame, spots: pl.DataFrame,
                   min_ep: float, wp_version: str) -> pl.DataFrame:  # fmt: skip
    """Metric 2's stored rows: the candidates (:func:`clock_inputs.half_end_candidates`) with
    G1's WP state, both head coaches, the game's kickoff spot and runoff, the threshold, the WP
    model version and ``exclusion`` ('missing_state' or NULL = graded)."""
    from twm.modules.decisions import wp_data

    f = wp_data.add_features(cand.join(kickers, on="game_id", how="left")).with_columns(
        ci._coach("posteam").alias("coach"), ci._coach("defteam").alias("opp_coach"))  # fmt: skip
    f = f.join(spots.select("game_id", "kick_yardline_100", "kick_runoff", "kick_source",
                            "kick_n"), on="game_id", how="left")  # fmt: skip
    bad = ~gi._valid().fill_null(False) | pl.col("kick_yardline_100").is_null()
    return f.with_columns(
        pl.lit(float(min_ep)).alias("passivity_min_ep"), pl.lit(wp_version).alias("wp_version"),
        pl.lit(CLOCK_FORMAT).alias("clock_format"),
        pl.when(bad).then(pl.lit("missing_state")).otherwise(None).alias("exclusion"),
    ).select(list(dict.fromkeys([*KEYS, *PASSIVITY_CONTEXT, *PASSIVITY_INPUTS, "wp_version",
                                 "clock_format", "exclusion"]))).sort(list(KEYS))  # fmt: skip


def with_passivity_grades(rows: pl.DataFrame, model) -> pl.DataFrame:
    """``rows`` + :data:`PASSIVITY_OUTPUTS` (NULL for excluded rows)."""
    g = rows.filter(pl.col("exclusion").is_null())
    out = grade_passivity(g.select(PASSIVITY_INPUTS), model)
    return rows.join(g.select(KEYS).hstack(out), on=list(KEYS), how="left").sort(list(KEYS))


# --------------------------------------------------------------------------------------
# One season: inputs, outputs, storage
# --------------------------------------------------------------------------------------


def clock_dir() -> Path:
    """``data/decisions/clock``: one parquet per season and table (gitignored)."""
    from twm.modules.decisions import wp as wpm

    return wpm.backtest_dir().parent / "clock"


def clock_season(db: Path | str, season: int, *, cfg: Any = None, wp_model=None,
                 out_dir: Path | None = None,
                 progress: Callable[[str], None] = print) -> dict:  # fmt: skip
    """Build, compute and store the three metrics of ``season`` (``defense_snaps_<S>``,
    ``half_passivity_<S>`` parquet files and ``season_<S>.json``); returns the summary."""
    from twm.config import settings
    from twm.modules.decisions import wp as wpm

    t0 = time.perf_counter()
    dcfg = cfg if cfg is not None else settings().decisions
    c = dcfg.clock
    model = wp_model if wp_model is not None else wpm.load_fold_model(season)
    if model.test_season != season or max(model.train_seasons, default=season - 1) >= season:
        raise ValueError(f"WP model {model.version} is not the point-in-time fold of {season}")
    consts = ci.measure_constants(db, season, c.runoff_seasons)
    snaps = ci.add_next_snap(ci.load_season(db, season))
    win = window_outputs(ci.defense_window(snaps, consts, c))
    games = snaps.select("game_id", "season", "week").unique()
    spots = gi.kickoff_spots(games, gi.kickoffs(db, [season - 1, season]),
                             dcfg.kickoff_min_kicks)  # fmt: skip
    pas = passivity_rows(ci.half_end_candidates(snaps, c), ci.opening_kickers(db, season),
                         spots, c.passivity_min_ep, model.version)  # fmt: skip
    pas = with_passivity_grades(pas, model)
    progress(f"{season}: {snaps.height:,} snaps, {win.height:,} window rows, "
             f"{pas.height} half-end candidates [{time.perf_counter() - t0:.0f} s]")  # fmt: skip
    info = {**consts, **c.model_dump(), "wp_version": model.version}
    return write_season(season, win, pas, info, team_games=team_games(snaps), out_dir=out_dir,
                        seconds=time.perf_counter() - t0)  # fmt: skip


def team_games(snaps: pl.DataFrame) -> pl.DataFrame:
    """One row per team and game of the season with its head coach (the leaderboards' games)."""
    g = snaps.select("season", "week", "season_type", "game_id", "home_team", "away_team",
                     "home_coach", "away_coach").unique()  # fmt: skip
    side = [g.select("season", "week", "season_type", "game_id", pl.col(f"{s}_team").alias("team"),
                     pl.col(f"{s}_coach").alias("coach")) for s in ("home", "away")]  # fmt: skip
    return pl.concat(side).sort("season", "game_id", "team")


def counts(win: pl.DataFrame, pas: pl.DataFrame) -> dict[str, int]:
    """Candidates and cases of each metric in stored rows."""
    m1, m3 = timeouts_unused(win), seconds_wasted(win)
    graded = pas.filter(pl.col("exclusion").is_null())
    return {"timeouts_unused_candidates": m1.height,
            "timeouts_unused_cases": int(m1["case"].sum()), "passivity_candidates": pas.height,
            "passivity_missing_state": pas.height - graded.height,
            "passivity_cases": int(graded["case"].sum()) if graded.height else 0,
            "late_decisive_team_games": m3.height, "late_cases": int(m3["case"].sum()),
            "late_seconds_wasted": int(round(float(m3["seconds_wasted"].sum())))}  # fmt: skip


def write_season(season: int, win: pl.DataFrame, pas: pl.DataFrame, info: dict, *,
                 team_games: pl.DataFrame | None = None, out_dir: Path | None = None,
                 seconds: float = 0.0) -> dict:  # fmt: skip
    """Write ``defense_snaps_<S>``, ``half_passivity_<S>`` (and ``team_games_<S>``) parquet
    files and ``season_<S>.json`` (constants, config, counts); returns the summary."""
    d = out_dir if out_dir is not None else clock_dir()
    d.mkdir(parents=True, exist_ok=True)
    if team_games is not None:
        team_games.write_parquet(d / f"team_games_{season}.parquet")
    win.write_parquet(d / f"defense_snaps_{season}.parquet")
    pas.write_parquet(d / f"half_passivity_{season}.parquet")
    summary = {"season": season, "format": CLOCK_FORMAT, **info, **counts(win, pas),
               "seconds": round(seconds, 1)}  # fmt: skip
    (d / f"season_{season}.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    return summary


def load(kind: str, seasons=None, out_dir: Path | None = None) -> pl.DataFrame:
    """Stored rows of ``kind`` ('defense_snaps', 'half_passivity', 'team_games')."""
    d = out_dir if out_dir is not None else clock_dir()
    files = sorted(d.glob(f"{kind}_*.parquet"))
    if seasons is not None:
        want = {int(s) for s in seasons}
        files = [f for f in files if int(f.stem.rsplit("_", 1)[1]) in want]
    if not files:
        raise FileNotFoundError(f"no stored {kind} in {d}: run `twm decisions clock`")
    return pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")


# --------------------------------------------------------------------------------------
# Reproducing stored outputs from the stored inputs alone
# --------------------------------------------------------------------------------------


def _same(a: pl.Series, b: pl.Series) -> bool:
    import numpy as np

    if a.dtype.is_float() or b.dtype.is_float():
        return np.array_equal(a.cast(pl.Float64).to_numpy(), b.cast(pl.Float64).to_numpy(),
                              equal_nan=True)  # fmt: skip
    return a.cast(pl.String).fill_null("<null>").equals(b.cast(pl.String).fill_null("<null>"))


def recompute(stored: pl.DataFrame, kind: str, *, models_root: Path | None = None,
              models: dict | None = None) -> pl.DataFrame:  # fmt: skip
    """The outputs of stored rows recomputed from their stored inputs ('defense_snaps': every
    window row; 'half_passivity': graded rows, the WP model loaded by its stored version)."""
    from twm.modules.decisions import wp as wpm

    if kind == "defense_snaps":
        return window_outputs(stored.drop(WINDOW_OUTPUTS, strict=False)).select(
            *KEYS, *WINDOW_OUTPUTS)  # fmt: skip
    rows = stored.filter(pl.col("exclusion").is_null())
    parts = []
    for (version,), part in rows.group_by("wp_version", maintain_order=True):
        m = (models or {}).get(version) or wpm.load_model(
            (models_root if models_root is not None else wpm.models_dir()) / f"{version}.joblib"
        )
        parts.append(part.select(KEYS).hstack(grade_passivity(part.select(PASSIVITY_INPUTS), m)))
    if not parts:
        return pl.DataFrame()
    return pl.concat(parts).sort(list(KEYS))


def verify(stored: pl.DataFrame, kind: str, **kw) -> list[str]:
    """The output columns whose recomputed values differ from the stored ones (empty = every
    stored output reproduced exactly)."""
    again = recompute(stored, kind, **kw)
    outputs = WINDOW_OUTPUTS if kind == "defense_snaps" else PASSIVITY_OUTPUTS
    have = stored if kind == "defense_snaps" else stored.filter(pl.col("exclusion").is_null())
    have = have.sort(list(KEYS)).select(*KEYS, *outputs)
    if again.height != have.height:
        return ["<rows>"]
    return [c for c in outputs if not _same(have.get_column(c), again.get_column(c))]
