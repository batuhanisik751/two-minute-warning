"""The Decision Report Card's frozen history (step P3): the graded 2006-2025 decisions as the
site publishes them, built ONCE on the owner's Mac from G3's and G4's stored grades
(``data/decisions/graded``, ``data/decisions/clock``), committed as zstd Parquet pinned with
its sha256 (``config/production_models.yaml``, entry ``decisions``, like Regression Watch's
frozen lists) and read by the publish on every run. The scheduled job never regrades history:
a Linux rebuild of the fold models' float sums could flip near ties (the Radar's backtest did),
and it would need the warehouse from 1999 and the models of every fold.

**What it holds** (``artifacts/production_models/decisions/history-<spec version>/``):

- ``fourth_downs`` / ``two_point``: every fourth down and try of the seasons, excluded ones
  too (:func:`twm.modules.decisions.site.fourth_rows` / ``try_rows``: the play key, context,
  choice, recommendation, grade, option WPs rounded for display, ``wp_lost`` exact);
- ``clock_cases``: the three clock metrics' rows (:func:`~twm.modules.decisions.site.clock_rows`);
- ``team_games``: every team-game with its head coach (the clock tables' games);
- ``season_inputs``: per season, the models' versions and the season-level inputs (option
  ranges, Platt, the margin, the clock constants) the grades were made with.

**Checked** (:func:`snapshot_mismatches`, by ``twm model check decisions`` and before
approving): the coach-season and league rows of ``reports/decisions/fourth_downs.csv`` and
``clock.csv`` of every snapshot season are reproduced from the snapshot alone (counts exactly,
values to the CSVs' 6 decimals).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import polars as pl

from twm.modules.decisions import site
from twm.pins import DECISION_TABLES

TABLES = DECISION_TABLES  # fourth_downs, two_point, clock_cases, team_games, season_inputs
TOLERANCE = 1.01e-6  # the report CSVs print 6 decimals
GRADE_INFO = ("wp_version", "conversion_version", "fieldgoal_version", "punt_version",
              "tries_version", "fg_max_distance", "punt_min_yardline", "fg_runoff_make",
              "fg_runoff_miss", "margin", "end_of_half_seconds")  # fmt: skip
CLOCK_INFO = ("kneel_play", "kneel_cycle", "play_seconds_run", "play_seconds_pass")


class FrozenError(ValueError):
    """The stored grades cannot be frozen (a season missing, a season of another format)."""


def season_inputs_row(grade_info: dict[str, Any], clock_info: dict[str, Any]) -> dict[str, Any]:
    """One ``season_inputs`` row from a season's G3 and G4 summaries (``season_<S>.json``)."""
    platt = grade_info.get("platt") or {}
    return {"season": int(grade_info["season"]), **{k: grade_info.get(k) for k in GRADE_INFO},
            "platt_kept": bool(platt.get("kept")), "platt_a": platt.get("a"),
            "platt_b": platt.get("b"), "grade_format": int(grade_info["format"]),
            "clock_wp_version": clock_info.get("wp_version"),
            **{k: clock_info.get(k) for k in CLOCK_INFO},
            "clock_format": int(clock_info["format"])}  # fmt: skip


SEASON_SCHEMA = {"season": pl.Int32, **dict.fromkeys(GRADE_INFO[:5], pl.String),
                 "fg_max_distance": pl.Int32, "punt_min_yardline": pl.Int32,
                 "fg_runoff_make": pl.Float64, "fg_runoff_miss": pl.Float64,
                 "margin": pl.Float64, "end_of_half_seconds": pl.Int32,
                 "platt_kept": pl.Boolean, "platt_a": pl.Float64, "platt_b": pl.Float64,
                 "grade_format": pl.Int32, "clock_wp_version": pl.String,
                 **dict.fromkeys(CLOCK_INFO, pl.Float64), "clock_format": pl.Int32}  # fmt: skip


def season_frame(rows: Sequence[dict[str, Any]]) -> pl.DataFrame:
    return pl.DataFrame(list(rows), schema=SEASON_SCHEMA).sort("season")


def season_tables(season: int, *, graded_dir: Path | None = None,
                  clock_dir: Path | None = None) -> dict[str, pl.DataFrame]:  # fmt: skip
    """One season's :data:`TABLES` from its stored G3 grades and G4 clock rows (read-only)."""
    from twm.modules.decisions import clock as ck
    from twm.modules.decisions import grade as gr

    gdir = graded_dir if graded_dir is not None else gr.graded_dir()
    cdir = clock_dir if clock_dir is not None else ck.clock_dir()
    try:
        gi = json.loads((gdir / f"season_{season}.json").read_text())
        ci = json.loads((cdir / f"season_{season}.json").read_text())
        fourth = gr.load_graded("fourth_downs", [season], gdir)
        tries = gr.load_graded("two_point", [season], gdir)
        win = ck.load("defense_snaps", [season], cdir)
        pas = ck.load("half_passivity", [season], cdir)
        games = ck.load("team_games", [season], cdir)
    except (OSError, ValueError, gr.GradeError) as e:
        raise FrozenError(f"season {season} has no complete stored grades: {e}") from e
    if gi.get("format") != gr.GRADE_FORMAT or ci.get("format") != ck.CLOCK_FORMAT:
        raise FrozenError(f"season {season}'s stored grades are of another format: regrade it")
    m2 = pas.filter(pl.col("exclusion").is_null())
    return {
        "fourth_downs": site.fourth_rows(fourth), "two_point": site.try_rows(tries),
        "clock_cases": site.clock_rows(ck.timeouts_unused(win), m2, ck.seconds_wasted(win)),
        "team_games": games.select(site.TEAM_GAME_COLUMNS).with_columns(
            pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)),
        "season_inputs": season_frame([season_inputs_row(gi, ci)]),
    }  # fmt: skip


def build_snapshot(seasons: Sequence[int], **dirs: Path | None) -> dict[str, pl.DataFrame]:
    """The snapshot of ``seasons`` (module docstring) from the stored grades."""
    parts = [season_tables(int(s), **dirs) for s in sorted(seasons)]
    if not parts:
        raise FrozenError("no seasons to freeze")
    return {t: pl.concat([p[t] for p in parts], how="vertical_relaxed") for t in TABLES}


# --------------------------------------------------------------------------------------
# The check against the committed reports
# --------------------------------------------------------------------------------------


def _tidy(f: pl.DataFrame, table: str, ids: list[str]) -> dict[tuple, float | None]:
    """{(table, season, coach, team, metric): value} of a report table (the CSVs' melt)."""
    out: dict[tuple, float | None] = {}
    vals = [c for c in f.columns if c not in ids and f.schema[c].is_numeric()]
    for r in f.select(*ids, *vals).iter_rows(named=True):
        for v in vals:
            x = r[v]
            out[(table, str(r["season"]), r.get("coach") or "", r.get("team") or "", v)] = (
                None if x is None else float(x))  # fmt: skip
    return out


def computed_rows(frames: dict[str, pl.DataFrame]) -> dict[str, dict[tuple, float | None]]:
    """The coach-season and league rows of both reports, from the snapshot alone:
    {'fourth_downs': ..., 'clock': ...} keyed like :func:`_tidy`."""
    from twm.modules.decisions import clock_report as cr
    from twm.modules.decisions import coach

    f, t = frames["fourth_downs"], frames["two_point"]
    m1, m2, m3 = site.clock_frames(frames["clock_cases"])
    games = frames["team_games"]
    summ = {int(r["season"]): r for r in frames["season_inputs"].iter_rows(named=True)}
    ids = ["season", "coach", "team"]
    return {
        "fourth_downs": {**_tidy(coach.league_by_season(f, t), "league", ["season"]),
                         **_tidy(coach.coach_season(f, t), "coach_season", ids)},
        "clock": {**_tidy(cr.league(games, m1, m2, m3, summ), "league", ["season"]),
                  **_tidy(cr.coach_season(games, m1, m2, m3), "coach_season", ids)},
    }  # fmt: skip


def report_rows(csv_path: Path, seasons: Sequence[int]) -> dict[tuple, float | None]:
    """The coach-season and league rows of a committed report CSV for ``seasons``."""
    ev = pl.read_csv(csv_path, infer_schema_length=0).filter(
        pl.col("table").is_in(["league", "coach_season"])
        & pl.col("season").is_in([str(s) for s in seasons]))  # fmt: skip
    return {(r["table"], r["season"], r["coach"] or "", r["team"] or "", r["metric"]):
            (None if r["value"] in (None, "") else float(r["value"]))
            for r in ev.iter_rows(named=True)}  # fmt: skip


def _differences(got: dict[tuple, Any], want: dict[tuple, Any], name: str) -> list[str]:
    out = []
    if set(got) != set(want):
        miss, extra = sorted(set(want) - set(got)), sorted(set(got) - set(want))
        out.append(f"{name}: {len(miss)} report rows missing from the snapshot (e.g. "
                   f"{miss[:1]}) and {len(extra)} extra (e.g. {extra[:1]})")  # fmt: skip
    for k in sorted(set(got) & set(want)):
        a, b = got[k], want[k]
        if (a is None) != (b is None) or (a is not None and abs(a - b) > TOLERANCE):
            out.append(f"{name} {' '.join(k)}: {a} in the snapshot, {b} in the report")
    return out


def snapshot_mismatches(frames: dict[str, pl.DataFrame], reports: dict[str, Path]) -> list[str]:
    """Where the snapshot disagrees with the committed reports (``reports``: {'fourth_downs':
    path, 'clock': path}) on its seasons' coach-season and league rows ([] = consistent)."""
    seasons = sorted(int(s) for s in frames["season_inputs"].get_column("season").to_list())
    for t in ("fourth_downs", "two_point", "clock_cases", "team_games"):
        extra = set(frames[t].get_column("season").unique().to_list()) - set(seasons)
        if extra:
            return [f"{t} holds seasons {sorted(extra)} without season inputs"]
    got = computed_rows(frames)
    out = []
    for name, path in reports.items():
        if not Path(path).exists():
            out.append(f"the report is missing: {path}")
            continue
        out += _differences(got[name], report_rows(Path(path), seasons), name)
    return out


# --------------------------------------------------------------------------------------
# The committed files
# --------------------------------------------------------------------------------------


def snapshot_dir(version: str, root: Path | None = None) -> Path:
    from twm.modules.decisions.production import artifact_dir

    return artifact_dir(root) / f"history-{version}"


def write_snapshot(frames: dict[str, pl.DataFrame], version: str,
                   root: Path | None = None) -> dict[str, Any]:  # fmt: skip
    """Write the frames as ``history-<version>/<table>.parquet`` (zstd, the pins' level) and
    describe them for the pin ({table: SnapshotFile})."""
    from twm import pins
    from twm.config import ROOT

    base = root if root is not None else ROOT
    out_dir = snapshot_dir(version, base)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for table in TABLES:
        path = out_dir / f"{table}.parquet"
        tmp = path.with_name(path.name + ".tmp")
        frames[table].write_parquet(tmp, compression="zstd", compression_level=pins.PARQUET_LEVEL,
                                    statistics=False)  # fmt: skip
        tmp.replace(path)
        try:
            rel = path.relative_to(base).as_posix()
        except ValueError:
            rel = str(path)
        files[table] = pins.SnapshotFile(rel, pins.sha256_of(path), frames[table].height)
    return files


def load_snapshot(pin: Any, root: Path | None = None) -> dict[str, pl.DataFrame]:
    """The pinned history (every file's sha256 checked BEFORE it is read, rows after), which must
    hang together: seasons before the pinned one, each with its inputs, one row per play key."""
    from twm import pins

    if set(pin.backtest) != set(TABLES):
        raise pins.PinError(
            f"the pin of {pin.module} has no frozen decision history: approve it with `uv run "
            "twm decisions pin` on the owner's Mac (the stored grades of every season), review "
            "and commit")  # fmt: skip
    frames = pins.read_snapshot(pin, TABLES, root)
    seasons = set(frames["season_inputs"].get_column("season").to_list())
    if not seasons or max(seasons) >= int(pin.season):
        raise pins.PinError(f"the frozen decision history must end before {pin.season}")
    for t, key in (("fourth_downs", ["game_id", "play_id"]), ("two_point", ["game_id", "play_id"]),
                   ("clock_cases", ["metric", "game_id", "team"]),
                   ("team_games", ["game_id", "team"])):  # fmt: skip
        f = frames[t]
        if not set(f.get_column("season").unique().to_list()) <= seasons:
            raise pins.PinError(f"the frozen {t} holds seasons without inputs")
        if f.select(key).is_duplicated().any():
            raise pins.PinError(f"the frozen {t} repeats a {', '.join(key)}")
    return frames
