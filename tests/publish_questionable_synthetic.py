"""Made-up Questionable snapshots for `twm publish` tests (feature #1), added to the Waiver
Radar's synthetic publish data (tests/publish_synthetic.py): snapshots stored with the
module's own writer (weekly.store_snapshot) in a small predictions store, the pinned table's
backtest and calibration (the committed pin), a small history and a live record.
Deterministic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

from tests.publish_synthetic import TEAMS, gid
from twm.modules.questionable import production as qp
from twm.modules.questionable import weekly as wk
from twm.publish import questionable as qn
from twm.publish.collect import PublishData

CREATED = datetime(2026, 10, 9, 6, 0, tzinfo=UTC)
FRIDAY = datetime(2026, 10, 9, 20, 0, tzinfo=UTC)  # as-of of the first snapshot (2026 week 5)
KICKOFF = datetime(2026, 10, 11, 17, 0)  # naive UTC, as the store keeps it


def list_rows(n: int = 3, shift: float = 0.0) -> pl.DataFrame:
    """``n`` tagged players (store layout, weekly.list_rows' columns), the first Doubtful."""
    rows = []
    for i in range(n):
        doubt = i == 0
        rows.append({
            "season": 2026, "week": 5, "gsis_id": gid(0, i), "player": f"Player {i}",
            "position": "QB", "team": TEAMS[i % 4], "opponent": TEAMS[(i + 1) % 4],
            "game_id": f"2026_05_{TEAMS[i % 4]}_{TEAMS[(i + 1) % 4]}",
            "kickoff_utc": KICKOFF + timedelta(hours=3 * i),
            "report_status": "Doubtful" if doubt else "Questionable",
            "practice_status": "Limited Participation in Practice", "practice": "limited",
            "missed_prev": i % 2 == 1, "body_part": "knee",
            "play_chance": round(0.01 + shift if doubt else 0.6 + 0.05 * i + shift, 6),
            "plays_n": None if doubt else 1307, "plays_median": None if doubt else 0.8,
            "plays_dud_rate": None if doubt else 0.31, "healthy_median": None if doubt else 0.85,
            "healthy_dud_rate": None if doubt else 0.28, "season_ppg": 12.5 if i else None,
            "season_games": 3 if i else 0, "source": "observed",
        })  # fmt: skip
    cols = [c for c in wk.COLUMNS if c not in ("as_of", "model_version", "created_at")]
    return pl.DataFrame(rows, infer_schema_length=None).select(cols)


def write_store(store: Path, as_ofs: tuple[datetime, ...] = (FRIDAY,), *, n: int = 3,
                shift: float = 0.0, version: str = "lookup-test") -> Path:  # fmt: skip
    """Snapshots of 2026 week 5 at each as-of (append-only: a stored as-of is kept)."""
    for t in as_ofs:
        wk.store_snapshot(store, list_rows(n, shift), season=2026, week=5, as_of=t,
                          model_version=version, created_at=CREATED)  # fmt: skip
    return store


def history(n_out: int = 30) -> pl.DataFrame:
    tagged = pl.DataFrame({
        "report_status": ["Questionable"] * 10 + ["Doubtful"] * 4 + ["Out"] * n_out,
        "practice": (["full", "limited", "dnp", "none", "limited"] * 2 + ["dnp"] * 4
                     + ["dnp"] * n_out),
        "played": [True, True, False, True, False, True, True, False, False, True]
                  + [False, False, False, True] + [False] * n_out,
    })  # fmt: skip
    return qn.history_rows(tagged, "2016-2025")


def add_questionable(data: PublishData, store: Path, *, n_out: int = 30,
                     calibration: int | None = None) -> PublishData:  # fmt: skip
    """``data`` with the snapshots of ``store`` and the module's tables (the real pin's
    backtest and calibration; ``calibration``: keep only that many rows)."""
    spec, _ = qp.load_pinned(2026)
    snaps = wk.read_snapshots(store)
    lists, rows = qn.snapshot_frames(snaps)
    tables = {"questionable_history": history(n_out), **qn.pin_tables(spec.content)}
    if calibration is not None:
        tables["questionable_calibration"] = tables["questionable_calibration"].head(calibration)
    played = pl.DataFrame({"season": [2026], "week": [5], "team": [TEAMS[1]],
                           "gsis_id": [gid(0, 1)], "offense_snaps": [40]})  # fmt: skip
    graded = wk.grade(snaps, played)  # player 1's game is graded; the others are pending
    tables["questionable_live"] = qn.live_rows(graded, 2026)
    versions = sorted(set(lists.get_column("model_version").to_list()))
    vrows = [qn.version_row({**spec.content, "model_version": v}, CREATED) for v in versions]
    data.tables.update(tables)
    data.tables["model_versions"] = pl.concat([data.tables["model_versions"], *vrows],
                                              how="vertical_relaxed")  # fmt: skip
    data.questionable = qn.QuestionableData(2026, versions[0] if versions else "", lists, rows,
                                            tables)  # fmt: skip
    return data
