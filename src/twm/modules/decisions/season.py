"""The season in progress, graded with the approved grading only (step P3): what the scheduled job
runs (``twm decisions grade-pinned``) after every data refresh.

Every fourth down, try and clock case of the season so far is graded with the pinned fold models
and the pinned season-level inputs (:mod:`twm.modules.decisions.production`): nothing is trained
and nothing is measured on earlier seasons (the runner's warehouse starts in 2012, and the
longest made field goal or the punt range before S would differ there). The only inputs read
from the warehouse are the season's own plays and the kickoffs of S-1 and S (the kickoff spot of
each game: the season's earlier weeks, else the previous season). The code is G3's and G4's
(:func:`twm.modules.decisions.grade.assemble` / ``grade_frames``, the clock metrics), so on the
owner's Mac the result equals ``twm decisions grade`` / ``clock`` of the same season exactly
(:func:`twm.modules.decisions.production.approve` checks it). Grades of past weeks are
recomputed every run with the same pins: deterministic on one machine.

Files (gitignored, ``data/decisions/season/``): ``graded/`` and ``clock/`` in G3's and G4's own
layout, so :func:`twm.modules.decisions.frozen.season_tables` shapes them for the site like the
history.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import polars as pl

from twm.modules.decisions import clock as ck
from twm.modules.decisions import clock_inputs as ci
from twm.modules.decisions import grade as gr
from twm.modules.decisions import grade_inputs as gi


def season_dir() -> Path:
    """``data/decisions/season``: the season in progress graded with the pins."""
    from twm.modules.decisions import wp as wpm

    return wpm.backtest_dir().parent / "season"


def grade_season(db: Path | str, season: int, spec: Any, models: gr.Models, *,
                 out_dir: Path | None = None,
                 progress: Callable[[str], None] = print) -> dict[str, Any]:  # fmt: skip
    """Grade ``season`` with ``spec`` and ``models`` (the approved ones) and write G3's and G4's
    files under ``out_dir`` (default :func:`season_dir`) ``/graded`` and ``/clock``. Returns
    {'fourth_downs': G3 summary, 'clock': G4 summary}."""
    t0 = time.perf_counter()
    out = out_dir if out_dir is not None else season_dir()
    if int(spec.season) != int(season):
        raise ValueError(f"the grading spec is for {spec.season}, not {season}")
    models.check(int(season))
    cfg = spec.cfg()
    plays = gi.load_season(db, season)
    games = plays.select("game_id", "season", "week").unique()
    kicks = gi.kickoffs(db, [season - 1, season])
    spots = gi.kickoff_spots(games, kicks, cfg.kickoff_min_kicks)
    info = spec.info()
    fourth, tr = gr.assemble(plays, spots, info, models)
    fourth, tr = gr.grade_frames(fourth, tr, models)
    graded = gr.write_season(season, fourth, tr, info, out_dir=out / "graded",
                             seconds=time.perf_counter() - t0)  # fmt: skip
    progress(f"{season}: {fourth.height:,} fourth downs {graded['fourth_downs']}; "
             f"{tr.height:,} tries {graded['tries']}")  # fmt: skip
    t1 = time.perf_counter()
    c = cfg.clock
    consts = dict(spec.clock)
    snaps = ci.add_next_snap(ci.load_season(db, season))
    win = ck.window_outputs(ci.defense_window(snaps, consts, c))
    sgames = snaps.select("game_id", "season", "week").unique()
    cspots = gi.kickoff_spots(sgames, kicks, cfg.kickoff_min_kicks)
    pas = ck.passivity_rows(ci.half_end_candidates(snaps, c), ci.opening_kickers(db, season),
                            cspots, c.passivity_min_ep, models.wp.version)  # fmt: skip
    pas = ck.with_passivity_grades(pas, models.wp)
    cinfo = {**consts, **c.model_dump(), "wp_version": models.wp.version}
    clock = ck.write_season(season, win, pas, cinfo, team_games=ck.team_games(snaps),
                            out_dir=out / "clock", seconds=time.perf_counter() - t1)  # fmt: skip
    progress(f"{season}: clock: {win.height:,} late-game snaps, {pas.height} half-end "
             f"candidates; timeouts unused {clock['timeouts_unused_cases']}/"
             f"{clock['timeouts_unused_candidates']}, passivity {clock['passivity_cases']}, "
             f"seconds wasted {clock['late_cases']} cases "
             f"[{time.perf_counter() - t0:.0f} s]")  # fmt: skip
    return {"fourth_downs": graded, "clock": clock}


def differences(season: int, a_dir: Path, b_dir: Path) -> list[str]:
    """Where two gradings of ``season`` differ (each a folder with ``graded/`` and ``clock/``:
    ``data/decisions`` holds G3's and G4's, :func:`season_dir` the pinned one): the stored rows
    of every kind, every column, bit for bit (NaN equals NaN). [] = identical."""
    out = []
    pairs = (("graded", "fourth_downs"), ("graded", "two_point"), ("clock", "defense_snaps"),
             ("clock", "half_passivity"), ("clock", "team_games"))  # fmt: skip
    for sub, kind in pairs:
        fa, fb = a_dir / sub / f"{kind}_{season}.parquet", b_dir / sub / f"{kind}_{season}.parquet"
        if not fa.exists() or not fb.exists():
            out.append(f"{sub}/{kind}: missing ({fa.exists()}, {fb.exists()})")
            continue
        x, y = pl.read_parquet(fa), pl.read_parquet(fb)
        if x.columns != y.columns or x.height != y.height:
            out.append(f"{sub}/{kind}: {x.shape} vs {y.shape}")
            continue
        out += [f"{sub}/{kind}.{c}" for c in x.columns if not ck._same(x[c], y[c])]
    return out
