"""Hot-Seat step H4a: the weekly list (``twm hotseat score``).

One run for a season and week N mirrors the other modules' weekly runs (the Radar's rules,
:mod:`twm.modules.waiver_radar.weekly`, through Regression Watch's checks):

1. **Which list**: week N from 2 to the week before the last regular-season week is a
   ``weekly`` list at the Tuesday as-of (``dim_week``); the last week is the
   ``end_of_season`` snapshot instead (owner, 2026-10-01: each team's row after its last
   regular-season game, anchor ``last_game_end``; the last week's Tuesday row is not scored,
   as in the backtest, because its as-of comes after Black Monday). The snapshot is due once
   EVERY team's last game is public (:class:`NotDueError` before): a list is frozen once
   published, so it is never stored with some teams missing.
2. **Freshness**: Regression Watch's checks (the Radar's plus play-by-play rows; exit code 3).
3. **The list** (point in time): the H3a batch path (:func:`.features.features_history` up to
   ``now``, each row at its own as-of) for every current head coach, scored by the APPROVED
   model (:mod:`.production`; loaded, never fitted): the probability of a positive departure
   by the window's end (the model's own), the rank, and the 3 largest drivers (coef x
   standardized value, signed, :func:`.production.drivers`). Interim coaches (took over during
   the season, or the owner's file says so) are scored but flagged: the model was not trained
   on interims.
4. **Live or reconstructed**: the Radar's clock rule (:func:`~twm.modules.waiver_radar.weekly.
   run_kind`): a weekly list is 'live' only on the real clock between its as-of and week N+1's
   first kickoff; the end-of-season snapshot between its latest team's as-of and the first
   playoff kickoff.
5. **Storing**: every coach's row (``module`` 'hot_seat', ``entity_type`` 'coach',
   ``entity_id`` = the warehouse coach id, ``rank_group`` = the snapshot, ``score`` = the
   probability, ``reasons_json`` = team, interim flag, drivers and the key features). A live
   list is append-only: any re-run of a stored live list (same version, season, week,
   snapshot), live or reconstructed, keeps the stored one.
6. **The report** ``reports/hot_seat/weekly/<season>-W<nn>.md`` (:func:`build_report`).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl

from twm import predictions as pr
from twm.modules.hot_seat import production as hp
from twm.modules.hot_seat.models import FEATURES, KEYS, MODULE
from twm.modules.waiver_radar import weekly as rw

REPORT_DIR = Path("reports/hot_seat/weekly")
EXIT_NOT_READY = rw.EXIT_NOT_READY
ENTITY_TYPE = "coach"
FIRST_WEEK = 2  # the features' first weekly as-of (a team needs a played game)
INTERIM_NOTE = "interim: the model was not trained on interims"


class NotDueError(ValueError):
    """The end-of-season snapshot is not due yet: some team's last game is not public."""


def list_snapshot(db: Path | str, season: int, week: int) -> str:
    """'weekly' for weeks 2 .. last - 1, 'end_of_season' for the last regular-season week."""
    last = rw.last_reg_week(db, season)
    if last is None:
        raise LookupError(f"{season} has no regular-season weeks in dim_week")
    if week < FIRST_WEEK or week > last:
        raise ValueError(
            f"week {week} of {season}: Hot-Seat lists exist for weeks {FIRST_WEEK}-{last} "
            f"(week {last}: the end-of-season snapshot)"
        )
    return "end_of_season" if week == last else "weekly"


def season_features(
    db: Path | str,
    season: int,
    now: datetime,
    *,
    grades: Mapping[int, pl.DataFrame] | None = None,
    labels: pl.DataFrame | None = None,
    candidates: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Every row of ``season`` whose as-of is not after ``now`` (the H3a batch path; the
    season's grades: the pinned season grades by default) with ``is_interim``."""
    from twm.modules.hot_seat import features as hf

    feats = hf.features_history(db, [int(season)], now=now,
                                grades=None if grades is None else dict(grades))  # fmt: skip
    return hf.add_interim_flag(feats, labels, candidates)


def select_rows(feats: pl.DataFrame, season: int, week: int, snapshot: str) -> pl.DataFrame:
    """The list's rows. A weekly list: the week's rows. The end-of-season snapshot: one row
    per team, due only when every team of the season (every team with a weekly row) has its
    row (:class:`NotDueError` otherwise)."""
    s = feats.filter(pl.col("season") == int(season))
    if snapshot == "weekly":
        return s.filter((pl.col("snapshot") == "weekly") & (pl.col("week") == int(week)))
    eos = s.filter(pl.col("snapshot") == "end_of_season")
    teams = set(s.filter(pl.col("snapshot") == "weekly").get_column("team").to_list())
    have = set(eos.get_column("team").to_list())
    if not teams or teams - have:
        raise NotDueError(
            f"the {season} end-of-season snapshot is not due yet: {len(have & teams)} of "
            f"{len(teams)} teams' last regular-season game is public"
        )
    return eos


def end_kickoff(db: Path | str, season: int):
    """The first playoff kickoff of ``season`` (aware UTC), or None when not scheduled."""
    from twm.asof import to_utc

    con = rw._connect(db)
    try:
        row = con.execute(
            "SELECT min(kickoff_utc) FROM fact_game WHERE season = ? AND season_type = 'POST'",
            [int(season)],
        ).fetchone()
    finally:
        con.close()
    return None if row is None or row[0] is None else to_utc(row[0])


def score_rows(pm: Any, rows: pl.DataFrame, names: Mapping[str, str]) -> pl.DataFrame:
    """The list: :func:`.production.list_frame` of ``rows`` scored by the approved model ``pm``
    (probability = its own, drivers = its terms), ranked; ``names``: coach id -> name."""
    srt = rows.sort(list(KEYS))
    x = srt.select(list(FEATURES))
    prob = pm.raw(x) if srt.height else srt.get_column("season").cast(pl.Float64).to_numpy()
    driver_rows = hp.drivers(pm.fitted, x) if srt.height else []
    return hp.list_frame(srt, prob, [pm.model_version] * srt.height, driver_rows, names)


@dataclass
class WeeklyRun:
    season: int
    week: int
    snapshot: str  # 'weekly' or 'end_of_season'
    as_of: datetime  # the list's as-of (end of season: the latest team's)
    now: datetime
    kind: str  # 'live' or 'backtest' (reconstructed)
    freshness: rw.Freshness
    model: Any = field(repr=False)  # the approved ProductionModel
    table: pl.DataFrame = field(repr=False)  # score_rows(); one row per coach
    next_kickoff: datetime | None
    horizon: int  # regular-season weeks after this one

    @property
    def incomplete(self) -> bool:
        return not self.freshness.ok


def run_week(
    db: Path | str,
    season: int,
    week: int,
    *,
    model: Any,
    now: datetime,
    allow_incomplete: bool = False,
    real_clock: bool = True,
    feats: pl.DataFrame | None = None,
    names: Mapping[str, str] | None = None,
) -> WeeklyRun:
    """Check the week's data and make the list with the approved ``model`` (nothing stored).
    :class:`NotDueError` before the end-of-season snapshot is due;
    :class:`~twm.modules.waiver_radar.weekly.NotReadyError` when data is missing and
    ``allow_incomplete`` is False. ``feats``: :func:`season_features` (default: built now);
    ``names``: coach id -> name (default: the warehouse's dim_coach)."""
    from twm.asof import to_utc, weekly_as_of
    from twm.modules.regression_watch import weekly as rwk

    if int(season) != int(model.season):
        raise ValueError(f"the approved Hot-Seat model scores {model.season}, not {season}")
    snapshot = list_snapshot(db, season, week)
    feats = feats if feats is not None else season_features(db, season, now)
    rows = select_rows(feats, season, week, snapshot)
    if snapshot == "weekly":
        as_of, kickoff = weekly_as_of(db, season, week), rw.next_kickoff(db, season, week)
        horizon = int(rw.last_reg_week(db, season) or week) - int(week)
    else:
        as_of, kickoff, horizon = to_utc(rows["as_of"].max()), end_kickoff(db, season), 0
    fresh = rwk.check_freshness(rwk.freshness_inputs(db, season, week), season, week, as_of, now)
    if not fresh.ok and not allow_incomplete:
        raise rw.NotReadyError(fresh)
    kind = rw.run_kind(as_of, kickoff, now) if real_clock else "backtest"
    if names is None:
        from twm.modules.hot_seat.targets import load_coach_names

        names = dict(load_coach_names(db).iter_rows())
    table = score_rows(model, rows, names)
    return WeeklyRun(int(season), int(week), snapshot, as_of, now, kind, fresh, model, table,
                     kickoff, horizon)  # fmt: skip


# --------------------------------------------------------------------------------------
# Storing (append-only for live lists)
# --------------------------------------------------------------------------------------


def reasons(r: Mapping[str, Any]) -> str:
    """A stored row's ``reasons_json``: team, coach name, interim flag (and its note), the
    drivers and the key features (:data:`.production.SHOWN`)."""
    out = {"team": r["team"], "coach": r["coach_name"], "interim": bool(r["is_interim"]),
           "note": INTERIM_NOTE if r["is_interim"] else None,
           "drivers": json.loads(r["drivers_json"]),
           "features": {c: r[c] for c in hp.SHOWN}}  # fmt: skip
    return json.dumps(out, default=float)


def list_rows(run: WeeklyRun, created: datetime) -> pl.DataFrame:
    """The store's ``predictions`` rows of one list (every coach; each row at its own as-of)."""
    t = run.table
    return t.select(
        pl.lit(MODULE).alias("module"), pl.lit(ENTITY_TYPE).alias("entity_type"),
        pl.col("coach_id").alias("entity_id"), pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        pl.col("as_of").dt.convert_time_zone("UTC").dt.replace_time_zone(None)
        .cast(pl.Datetime("us")),
        pl.lit(run.horizon, dtype=pl.Int32).alias("horizon"),
        pl.col("snapshot").alias("rank_group"), pl.col("prob").alias("score"),
        pl.col("prob").alias("raw_score"), pl.col("rank").cast(pl.Int32),
        pl.lit(None, dtype=pl.String).alias("band"), "model_version",
        pl.Series("reasons_json", [reasons(r) for r in t.to_dicts()], dtype=pl.String),
        pl.lit(run.kind).alias("kind"),
        pl.lit(created, dtype=pl.Datetime("us")).alias("created_at"),
        pl.lit(None, dtype=pl.String).alias("tier"), pl.lit(run.incomplete).alias("incomplete"),
    )  # fmt: skip


def stored_live(store: Path | str, run: WeeklyRun) -> int:
    """Rows of a stored LIVE list of the run's version, season, week and snapshot."""
    if not Path(store).exists():
        return 0
    con = pr.connect(store, read_only=True)
    try:
        row = con.execute(
            "SELECT count(*) FROM predictions WHERE module = ? AND kind = 'live' AND "
            "model_version = ? AND season = ? AND week = ? AND rank_group = ?",
            [MODULE, run.model.model_version, run.season, run.week, run.snapshot],
        ).fetchone()
    finally:
        con.close()
    return int(row[0]) if row else 0


def store_week(run: WeeklyRun, store: Path | str, *, created_at: datetime | None = None) -> dict:
    """Store the list (``replace='weeks'``) unless a LIVE list of the same version, season,
    week and snapshot is already stored: then nothing is written, live or reconstructed run
    alike, and ``{'predictions': 0, 'kept': n}`` says so (append-only; the job re-runs the
    end-of-season snapshot on the nights after it). A live run replaces a stored
    reconstruction; the store itself refuses any reconstructed overwrite of a live week
    (:class:`twm.predictions.LiveWeekError`)."""
    created = created_at if created_at is not None else pr.now_utc()
    kept = stored_live(store, run)
    if kept:
        return {"predictions": 0, "model_versions": 0, "outcomes": 0, "kept": kept}
    versions = hp.rprod.version_frame(run.model, created_at=created)
    out = pr.write_predictions(store, predictions=list_rows(run, created), versions=versions,
                               replace="weeks")  # fmt: skip
    return {**out, "kept": 0}


# --------------------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------------------


def report_path(season: int, week: int, root: Path | None = None) -> Path:
    from twm.config import ROOT

    return (root if root is not None else ROOT) / REPORT_DIR / f"{season}-W{week:02d}.md"


def driver_text(d: Mapping[str, Any]) -> str:
    """'Wins vs market expectation -1.63 (+1.18)': label, raw value (yes/no for a flag),
    signed log-odds term."""
    from twm import registry

    v = d.get("value")
    if v is None:
        value = ""
    elif registry.get(d["feature"]).unit == "boolean":
        value = " yes" if v else " no"
    else:
        value = f" {v:.3g}"
    return f"{d['label']}{value} ({d['contribution']:+.2f})"


def _kind_text(run: WeeklyRun) -> str:
    if run.kind == "live":
        return "**Live list (stored as 'live').** Scored in real time with the approved model."
    return ("**Reproduced list (stored as 'backtest').** Run outside the live window or with a "
            "set clock; only a run on the real clock inside it is stored as 'live'.")  # fmt: skip


def build_report(run: WeeklyRun, *, generated: str, command: str) -> str:
    """The weekly report (markdown): every coach ranked, with the drivers."""
    what = ("end-of-season snapshot (each team after its last regular-season game)"
            if run.snapshot == "end_of_season" else f"week {run.week}")  # fmt: skip
    m = run.model
    lines = [f"# Hot-Seat Meter: {run.season} {what}", "", generated, "",
             f"As-of {run.as_of:%a %Y-%m-%d %H:%M} UTC. " + _kind_text(run), "",
             f"Approved model {m.model_version}: L2 logistic regression trained on "
             f"{m.fold.train_seasons[0]}-{m.fold.train_seasons[-1]} (C {m.params['C']:g}); "
             "probability that the head coach's departure (fired or a mutual parting) is "
             "announced by 30 days after the team's final game. Labels: cited public-source "
             "research accepted by the owner (docs/hot_seat.md).", ""]  # fmt: skip
    if run.incomplete:
        lines += ["**WARNING: scored with incomplete data:** " + run.freshness.message(), ""]
    lines += [
        "| rank | team | coach | probability | record | wins vs exp. | point diff/g | tenure "
        "| top drivers (log-odds) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in run.table.sort("rank").iter_rows(named=True):
        ds = "; ".join(driver_text(d) for d in json.loads(r["drivers_json"]))
        coach = (r["coach_name"] or r["coach_id"]) + (" (interim)" if r["is_interim"] else "")
        wins, games = r["reg_wins"], r["reg_games_played"]
        lines.append(
            f"| {r['rank']} | {r['team']} | {coach} | {r['prob']:.1%} | {wins:g}-{games - wins:g} "
            f"| {r['wins_vs_expected']:+.2f} | {r['point_diff_per_game']:+.1f} | "
            f"{r['tenure_seasons']} | {ds} |"
        )
    if run.table.filter(pl.col("is_interim")).height:
        lines += ["", f"(interim) {INTERIM_NOTE}."]
    lines += ["", f"Command: `{command}`", ""]
    return "\n".join(lines)


def write_report(text: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path
