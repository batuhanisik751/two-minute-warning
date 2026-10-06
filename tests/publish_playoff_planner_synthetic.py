"""Made-up playoff-planner snapshots for `twm publish` tests (feature #6), added to the Waiver
Radar's synthetic publish data (tests/publish_synthetic.py): grids stored with the module's own
writer (weekly.store_snapshot) in a small predictions store, the pinned spec's tables (the
committed pin) and a live record graded with made-up unit points. Deterministic.

The four synthetic teams (BUF, DAL, KC, SF) in 2026 weeks 15-17: week 15 BUF-KC and DAL-SF,
week 16 BUF-DAL and KC-SF, week 17 BUF-SF (DAL and KC on a bye). Ratings use the pin's chosen
candidate per position (TE and K: 'none', every matchup 1.0 and no rank)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

from twm.modules.playoff_planner import production as pr
from twm.modules.playoff_planner import weekly as wk
from twm.publish import playoff_planner as pp
from twm.publish.collect import PublishData

CREATED = datetime(2026, 10, 13, 6, 0, tzinfo=UTC)
TUESDAY = datetime(2026, 10, 13, 6, 0, tzinfo=UTC)  # as-of of the first snapshot (through week 5)
GAMES = {15: (("BUF", "KC"), ("DAL", "SF")), 16: (("BUF", "DAL"), ("KC", "SF")),
         17: (("BUF", "SF"),)}  # fmt: skip
TEAMS = ("BUF", "DAL", "KC", "SF")
BASE = {"BUF": 1.12, "DAL": 0.95, "KC": 0.88, "SF": 1.04}  # each defense's raw multiplier
LG = {"QB": 18.0, "RB": 21.0, "WR": 32.0, "TE": 11.0, "K": 8.0, "DST": 7.0}
N_ROWS = 3 * len(TEAMS) * len(LG)  # 72


def grid_rows(through_week: int = 5, shift: float = 0.0) -> pl.DataFrame:
    """The grid as weekly.grid makes it (GRID_COLUMNS): ``shift`` moves every rating."""
    chosen = pr.load_pinned(2026)[0].chosen
    opp = {(w, a): (b, True) for w, gs in GAMES.items() for a, b in gs}
    opp |= {(w, b): (a, False) for w, gs in GAMES.items() for a, b in gs}
    rows = []
    for w in (15, 16, 17):
        for t in TEAMS:
            o, home = opp.get((w, t), (None, None))
            for i, pos in enumerate(LG):
                cand = chosen[pos]
                raw = None if o is None else round(BASE[o] + 0.01 * i + shift, 6)
                shrunk = None if raw is None else round(1 + (raw - 1) / 2, 6)
                rating = None if o is None else (1.0 if cand == "none" else shrunk)
                rows.append({
                    "season": 2026, "through_week": through_week, "week": w, "team": t,
                    "opponent": o, "home": home, "game_id": None if o is None else
                    f"2026_{w}_{t}_{o}", "kickoff_utc": None, "position": pos,
                    "candidate": cand, "rating": rating, "rating_rank": None,
                    "raw": raw, "shrunk": shrunk, "adjusted": shrunk,
                    "opp_games": None if o is None else through_week, "lg_ppg": LG[pos],
                })  # fmt: skip
    df = pl.DataFrame(rows, schema=wk.empty_grid().schema)
    ranked = (pl.col("candidate") != "none") & pl.col("rating").is_not_null()
    rank = pl.col("rating").rank("min", descending=True).over("week", "position")
    return df.with_columns(pl.when(ranked).then(rank).cast(pl.Int32).alias("rating_rank"))


def write_store(store: Path, weeks: tuple[int, ...] = (5,), *, shift: float = 0.0,
                version: str = "matchup-test") -> Path:  # fmt: skip
    """A snapshot through each of ``weeks`` (as of TUESDAY + 7 days per week after 5);
    append-only: a stored (season, through_week) is kept."""
    for tw in weeks:
        t = TUESDAY + timedelta(days=7 * (tw - 5))
        wk.store_snapshot(store, grid_rows(tw, shift), as_of=t, model_version=version,
                          created_at=CREATED)  # fmt: skip
    return store


def actuals(weeks: tuple[int, ...] = (15,), factor: float = 1.0) -> pl.DataFrame:
    """weekly.read_actuals rows: every game of ``weeks`` is in; a unit scores ``factor`` x
    the league's average at its position (so the realized multiplier is ``factor``)."""
    rows = [{"season": 2026, "week": w, "game_id": f"2026_{w}_{a}_{b}", "team": t,
             "opponent": o, "position": pos, "points": round(LG[pos] * factor, 6)}
            for w in weeks for a, b in GAMES[w] for t, o in ((a, b), (b, a))
            for pos in LG]  # fmt: skip
    return pl.DataFrame(rows, schema=pp.ACTUALS)


def add_playoff_planner(data: PublishData, store: Path, *, effects: int | None = None,
                        played: pl.DataFrame | None = None) -> PublishData:  # fmt: skip
    """``data`` with the snapshots of ``store`` and the module's tables (the real pin's;
    ``effects``: keep only that many effect rows), the live record graded with ``played``
    (default: no playoff game is in)."""
    spec, _ = pr.load_pinned(2026)
    lists, rows = pp.snapshot_frames(wk.read_snapshots(store))
    tables = pp.pin_tables(spec.content)
    if effects is not None:
        tables["playoff_planner_effects"] = tables["playoff_planner_effects"].head(effects)
    versions = sorted(set(lists.get_column("model_version").to_list()))
    vrows = [pp.version_row({**spec.content, "model_version": v}, CREATED) for v in versions]
    d = pp.PlayoffPlannerData(2026, versions[0] if versions else "", lists, rows, tables,
                              actuals=actuals(()) if played is None else played)  # fmt: skip
    tables["playoff_planner_live"] = pp.live_record(d, pp.no_published())
    data.tables.update(tables)
    data.tables["model_versions"] = pl.concat([data.tables["model_versions"], *vrows],
                                              how="vertical_relaxed")  # fmt: skip
    data.meta.update(pp.meta(d))
    data.playoff_planner = d
    return data
