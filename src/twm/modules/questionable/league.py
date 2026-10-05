"""My League: "Your players tagged Questionable or Doubtful" in the local weekly report
(`twm league weekly`). ESPN-optional: without a synced league there is no report, and without
the owner's roster rows (or before any list is stored) the section is absent (``None``).

The rows come from the latest stored Questionable snapshot of the season (the nightly list,
:mod:`.weekly`), restricted to the owner's rostered players, starters first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl

SECTION_ID = "questionable"
TITLE = "Your players tagged Questionable or Doubtful"
NOT_STARTING = ("BE", "IR")  # ESPN lineup slots that are not starters


@dataclass
class Section:
    season: int
    week: int | None  # the snapshot's game week (None: nothing stored yet)
    as_of: datetime | None  # the snapshot's as-of (naive UTC)
    rows: list[dict[str, Any]] = field(default_factory=list)


def latest_snapshot(snaps: pl.DataFrame) -> pl.DataFrame:
    """The rows of the newest as-of among ``snaps``."""
    if snaps.is_empty():
        return snaps
    return snaps.filter(pl.col("as_of") == pl.col("as_of").max())


def for_report(predictions: Path, roster: pl.DataFrame, season: int) -> Section | None:
    """The section's data; None (no section) when the owner's roster is unknown (no ESPN team
    found) or no Questionable list of the season is stored yet."""
    from twm.modules.questionable import weekly as wk

    if roster.is_empty() or "entity_id" not in roster.columns:
        return None
    snap = latest_snapshot(wk.read_snapshots(predictions, season))
    if snap.is_empty():  # no list stored yet (the nightly job stores one per run)
        return None
    mine = roster.select(
        pl.col("entity_id").alias("gsis_id"),
        pl.col("lineup_slot").fill_null("").alias("slot"),
    ).unique("gsis_id")
    rows = snap.join(mine, on="gsis_id", how="inner").with_columns(
        (~pl.col("slot").is_in(NOT_STARTING)).alias("starter")
    )
    rows = rows.sort(["starter", "play_chance", "player"], descending=[True, False, False])
    return Section(season, int(snap["week"][0]), snap["as_of"][0], rows.to_dicts())


def plays_text(r: dict[str, Any]) -> str:
    """The bucket's "if he plays" line in words."""
    if not r.get("plays_n"):
        return "too few past cases to say"
    return (f"median {r['plays_median']:.0%} of his usual points, below half of usual "
            f"{r['plays_dud_rate']:.0%} of the time (healthy players: "
            f"{r['healthy_dud_rate']:.0%})")  # fmt: skip


def render_section(sec: Section) -> Any:
    """The HTML section (twm.league.report's helpers)."""
    from twm.league.report import para, section, table
    from twm.modules.questionable.weekly import INACTIVES_NOTE

    parts: list[object] = [
        "From the nightly Questionable list: the chance each tagged player of yours plays "
        "(players like him since 2016) and, if he plays, how he scores against his usual."
    ]
    if sec.as_of is None:
        parts.append(para("No Questionable list stored yet: it fills in as teams post their "
                          "final injury reports (Friday for Sunday games).", "muted"))  # fmt: skip
        return section(SECTION_ID, TITLE, parts)
    parts.append(para(f"Week {sec.week} list as of {sec.as_of:%Y-%m-%d %H:%M} UTC.", "small"))
    rows = [[r["player"], r["position"], "starter" if r["starter"] else "bench",
             r["report_status"], f"{r['play_chance']:.0%}", plays_text(r)]
            for r in sec.rows]  # fmt: skip
    head = ["player", "pos", "slot", "tag", "chance he plays", "if he plays"]
    parts.append(table(head, rows, {4}) if rows else para("None of your players.", "muted"))
    parts.append(para(INACTIVES_NOTE, "muted small"))
    return section(SECTION_ID, TITLE, parts)
