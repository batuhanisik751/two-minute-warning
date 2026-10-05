"""The frozen start/sit distributions and backtest (pin ``startsit`` in
config/production_models.yaml, ``model: rank_dist``).

The pinned file is a JSON spec naming every frozen Parquet file with its sha256 and rows (like
the Decision Report Card's grading spec): ``rank_points`` (the 2020-2025 rank -> points
distributions the CLI and the league report use) and the walk-forward backtest's aggregate
tables. No file holds a player, an id or a FantasyPros value of a player: only ranks, counts,
points and rates (:data:`FORBIDDEN_COLUMNS` is checked by `twm model check startsit`).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from twm import pins
from twm.modules.startsit import backtest as bt
from twm.modules.startsit.dist import N_ATOMS, RANK_CAP, RankTable, fit

MODULE = "startsit"
MODEL = "rank_dist"
TRAIN_SEASONS = list(range(2020, 2026))
TABLES = ("rank_points", "metrics", "calibration", "rank_gap", "teammates", "folds")
FORBIDDEN_COLUMNS = {"fantasypros_id", "gsis_id", "player", "player_id", "player_name", "team",
                     "ecr", "sd", "best", "worst", "pos_rank_a", "pos_rank_b", "points_a",
                     "points_b"}  # fmt: skip
REPORT = Path("reports") / "startsit" / "backtest.md"


class StartSitPinError(ValueError):
    """The frozen start/sit files cannot be used (missing, changed, wrong season)."""


def build_frames(history: pl.DataFrame, progress=None) -> tuple[RankTable, dict[str, Any]]:
    """The production distributions (c chosen on 2025, fit on 2020-2025) and every table."""
    wf = bt.walk_forward(history, progress=progress)
    c, _ = bt.choose_c(history, TRAIN_SEASONS)
    table = fit(history, c, TRAIN_SEASONS)
    frames = {
        "rank_points": table.frame(),
        "metrics": bt.metrics(wf["pairs"]),
        "calibration": bt.calibration(wf["pairs"]),
        "rank_gap": bt.rank_gap(history.filter(pl.col("season").is_in(TRAIN_SEASONS))),
        "teammates": bt.teammates(wf["pairs"]),
        "folds": wf["folds"],
    }
    trained = history.filter(pl.col("season").is_in(TRAIN_SEASONS))
    extra = {"c": c, "rows": trained.height,
             "dnp_share": float(1 - trained["played"].mean())}  # fmt: skip
    return table, {"frames": frames, **extra}


def _digest(frames: dict[str, pl.DataFrame], c: float) -> str:
    h = hashlib.sha256(f"{MODEL}|{c!r}|{N_ATOMS}|{sorted(RANK_CAP.items())}".encode())
    for t in TABLES:
        h.update(t.encode())
        h.update(frames[t].write_csv(float_precision=9).encode())
    return h.hexdigest()[:16]


def _base(root: Path | None) -> Path:
    from twm.config import ROOT

    return root if root is not None else ROOT


def freeze(history: pl.DataFrame, season: int, *, root: Path | None = None,
           pin_path: Path | None = None, progress=None) -> pins.Pin:  # fmt: skip
    """Fit, backtest and freeze; write the spec, the report and the ``startsit`` pin (other
    pins are kept as they are)."""
    from twm.modules.startsit.report import report_text

    base = _base(root)
    _, out = build_frames(history, progress)
    frames = out["frames"]
    version = f"{MODEL}-{_digest(frames, out['c'])}"
    rel_dir = pins.ARTIFACT_DIR / MODULE / version
    (base / rel_dir).mkdir(parents=True, exist_ok=True)
    files = {}
    for t in TABLES:
        rel = rel_dir / f"{t}.parquet"
        frames[t].write_parquet(base / rel, compression="zstd",
                                compression_level=pins.PARQUET_LEVEL)  # fmt: skip
        files[t] = {"file": rel.as_posix(), "sha256": pins.sha256_of(base / rel),
                    "rows": frames[t].height}  # fmt: skip
    spec = {"module": MODULE, "model": MODEL, "model_version": version, "season": int(season),
            "train_seasons": f"{TRAIN_SEASONS[0]}-{TRAIN_SEASONS[-1]}",
            "test_seasons": f"{bt.TEST_SEASONS[0]}-{bt.TEST_SEASONS[-1]}", "c": out["c"],
            "n_atoms": N_ATOMS, "rank_cap": RANK_CAP, "rank_rows": out["rows"],
            "dnp_share": round(out["dnp_share"], 6), "files": files}  # fmt: skip
    spec_rel = pins.ARTIFACT_DIR / MODULE / f"{version}.json"
    (base / spec_rel).write_text(json.dumps(spec, indent=1, sort_keys=True) + "\n")
    (base / REPORT).parent.mkdir(parents=True, exist_ok=True)
    (base / REPORT).write_text(report_text(frames, spec))
    pin = pins.Pin(module=MODULE, season=int(season), model_version=version,
                   file=spec_rel.as_posix(), sha256=pins.sha256_of(base / spec_rel),
                   approved=datetime.now(UTC).date().isoformat(), model=MODEL,
                   backtest_seasons=f"{TRAIN_SEASONS[0]}-{TRAIN_SEASONS[-1]}")  # fmt: skip
    current = pins.read_pins(pin_path)
    current[MODULE] = pin
    pins.write_pins(current, pin_path)
    return pin


def read_spec(season: int, *, root: Path | None = None, pin_path: Path | None = None) -> dict:
    """The pinned spec of ``season`` (its sha256 checked before it is parsed)."""
    try:
        pin = pins.get_pin(MODULE, pin_path)
    except pins.PinError as e:
        raise StartSitPinError(str(e)) from None
    if pin.model != MODEL or pin.season != int(season):
        raise StartSitPinError(f"the {MODULE} pin is for {pin.season} ({pin.model or '?'}), not "
                               f"{season}: start/sit odds exist for {pin.season} only")  # fmt: skip
    path = _base(root) / pin.file
    if not path.is_file() or pins.sha256_of(path) != pin.sha256:
        raise StartSitPinError(f"{pin.file} is missing or not the pinned file (sha256)")
    spec = json.loads(path.read_text())
    if spec.get("model_version") != pin.model_version or spec.get("season") != pin.season:
        raise StartSitPinError(f"{pin.file} holds {spec.get('model_version')}, not the pin's")
    return spec


def read_frame(spec: dict, table: str, *, root: Path | None = None) -> pl.DataFrame:
    """One frozen table (sha256 checked before reading, rows after)."""
    f = spec["files"][table]
    path = _base(root) / f["file"]
    if not path.is_file() or pins.sha256_of(path) != f["sha256"]:
        raise StartSitPinError(f"{f['file']} is missing or not the pinned file (sha256)")
    df = pl.read_parquet(path)
    if df.height != f["rows"]:
        raise StartSitPinError(f"{f['file']} has {df.height} rows, the spec {f['rows']}")
    return df


def load(season: int, *, root: Path | None = None, pin_path: Path | None = None
         ) -> tuple[RankTable, dict]:  # fmt: skip
    """(the frozen distributions, the spec) for ``season``."""
    spec = read_spec(season, root=root, pin_path=pin_path)
    return RankTable.from_frame(read_frame(spec, "rank_points", root=root), spec["c"]), spec


def check(season: int, *, root: Path | None = None, pin_path: Path | None = None
          ) -> tuple[list[str], list[str]]:  # fmt: skip
    """(summary lines, problems) of the pinned files: the spec as pinned, every table as named
    (sha256, rows), no per-player column anywhere, and the committed report equal to the one
    the frozen tables write. Changes nothing."""
    from twm.modules.startsit.report import report_text

    try:
        spec = read_spec(season, root=root, pin_path=pin_path)
        frames = {t: read_frame(spec, t, root=root) for t in TABLES}
    except (StartSitPinError, KeyError) as e:
        return [], [str(e)]
    problems = []
    for t, df in frames.items():
        bad = sorted(set(df.columns) & FORBIDDEN_COLUMNS)
        if bad:
            problems.append(f"{t} holds per-player columns {bad}")
    ranks = frames["rank_points"].group_by("pos").agg(pl.col("rank").max())
    if dict(ranks.iter_rows()) != RANK_CAP:
        problems.append("rank_points does not cover every rank up to the caps")
    report = _base(root) / REPORT
    if not report.is_file() or report.read_text() != report_text(frames, spec):
        problems.append(f"{REPORT} differs from the frozen backtest (rewrite it from the pin)")
    pooled = frames["metrics"].filter((pl.col("season") == "all") & (pl.col("kind") == "all"))
    r = pooled.row(0, named=True) if pooled.height else {}
    lines = [
        f"{MODULE} {season}: {spec['model_version']} ({spec['train_seasons']}, c {spec['c']:g}; "
        f"{len(TABLES)} files, sha256 and rows checked; no per-player column)",
        f"  walk-forward {spec['test_seasons']}: {r.get('pairs', 0):,} pairs, Brier "
        f"{r.get('brier_model', 0):.4f} vs {r.get('brier_rank_rate', 0):.4f} (higher rank at its "
        f"rate) vs {r.get('brier_coin', 0):.4f} (coin); {REPORT} matches",
    ]
    return lines, problems
