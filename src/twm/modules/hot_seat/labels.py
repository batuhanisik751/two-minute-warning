"""Hot-Seat H2: the owner's departure labels (``data/manual/coach_departures.csv``) and their check.

One row per departure: the schedule candidates (``origin = schedule``, joined by
``candidate_id``) plus departures the schedule misses (``origin = source_only``). The research
prefill writes ``departure_type`` / ``announced_date`` / ``source_url`` with ``prefill =
suggested`` (or leaves them empty with ``prefill = blank`` and the reason in ``prefill_note``);
only the owner writes ``y`` in ``verified_by_owner``. Guide: docs/labeling_coaches.md.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

DEPARTURE_TYPES = (
    "fired_in_season",
    "fired_after_season",
    "mutual_parting",
    "resigned_under_pressure",
    "resigned",
    "retired",
    "health_or_death",
    "left_for_other_job",
    "interim_not_retained",
    "other",
)
POSITIVE_TYPES = ("fired_in_season", "fired_after_season", "mutual_parting")  # spec 8.5 default
ORIGINS = ("schedule", "source_only")
PREFILL = ("suggested", "blank")

LABEL_COLUMNS = (
    "candidate_id",
    "origin",
    "team",
    "last_season",
    "change_kind",
    "coach_name",
    "last_game_id",
    "last_game_date",
    "successor_name",
    "successor_first_game_date",
    "interim_suspected",
    "data_gap_suspected",
    "departure_type",
    "announced_date",
    "source_url",
    "prefill",
    "prefill_note",
    "verified_by_owner",
    "notes",
)

URL_RE = re.compile(r"^https?://\S+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
WINDOW_START = (6, 1)  # a departure of season S is announced from June 1 of S ...
WINDOW_END = (9, 30)  # ... to Sept 30 of S+1 (the next season is under way)


@dataclass
class LabelReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    by_type: dict[str, int] = field(default_factory=dict)


def read_labels(path: Path | str) -> pl.DataFrame:
    """The owner's file, every column as text (empty cells as "")."""
    df = pl.read_csv(path, infer_schema=False)
    return df.with_columns(pl.all().fill_null("").str.strip_chars())


def read_candidates(path: Path | str) -> pl.DataFrame | None:
    p = Path(path)
    if not p.exists():
        return None
    return pl.read_csv(p, infer_schema=False).fill_null("")


def _date(text: str) -> dt.date | None:
    if not DATE_RE.match(text):
        return None
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def date_window(last_season: int) -> tuple[dt.date, dt.date]:
    """The dates a departure of ``last_season`` may be announced on (inclusive)."""
    return dt.date(last_season, *WINDOW_START), dt.date(last_season + 1, *WINDOW_END)


def _row_errors(r: dict) -> list[str]:
    """Problems with one row (its id prefixes each message)."""
    out: list[str] = []
    rid = r["candidate_id"] or f"{r['team']} {r['coach_name']} {r['last_season']}"
    if r["origin"] not in ORIGINS:
        out.append(f"origin {r['origin']!r} is not one of {', '.join(ORIGINS)}")
    if not (r["team"] and r["coach_name"] and r["last_season"].isdigit()):
        out.append("team, coach_name and a numeric last_season are required")
    dtype = r["departure_type"]
    if dtype and dtype not in DEPARTURE_TYPES:
        out.append(f"departure_type {dtype!r} is not an allowed category")
    if r["prefill"] and r["prefill"] not in PREFILL:
        out.append(f"prefill {r['prefill']!r} is not one of {', '.join(PREFILL)}")
    date_text = r["announced_date"]
    if date_text:
        day = _date(date_text)
        if day is None:
            out.append(f"announced_date {date_text!r} is not a YYYY-MM-DD date")
        elif r["last_season"].isdigit():
            lo, hi = date_window(int(r["last_season"]))
            if not lo <= day <= hi:
                out.append(f"announced_date {date_text} is outside the window {lo} .. {hi}")
    url = r["source_url"]
    if url and not URL_RE.match(url):
        out.append(f"source_url {url!r} is not an http(s) URL")
    flag = r["verified_by_owner"]
    if flag not in ("", "y"):
        out.append(f"verified_by_owner {flag!r}: write y or leave it empty")
    if flag == "y":
        # a blank announced_date is allowed (no source gives the day): the model then uses the
        # coach's last_game_date (docs/hot_seat.md), so one of the two is needed
        missing = [c for c in ("departure_type", "source_url") if not r[c]]
        if not (r["announced_date"] or r["last_game_date"]):
            missing.append("announced_date (or last_game_date)")
        if missing:
            out.append(f"verified row without {', '.join(missing)}")
    return [f"{rid}: {m}" for m in out]


def _row_warnings(r: dict) -> list[str]:
    """Plausible but unusual dates: worth a second look, not an error."""
    out = []
    day, last = _date(r["announced_date"]), _date(r["last_game_date"])
    nxt, rid = _date(r["successor_first_game_date"]), r["candidate_id"]
    gap = r["change_kind"] == "offseason" and r["departure_type"] == "fired_in_season"
    if day and last and day < last and r["departure_type"].startswith("fired") and not gap:
        out.append(f"{rid}: fired on {day}, before his last listed game {last} (check the date)")
    if day and nxt and day > nxt and r["change_kind"] == "in_season":
        out.append(
            f"{rid}: announced {day}, after the successor's first game {nxt} (a leave first?)"
        )
    return out


def _duplicates(df: pl.DataFrame, cols: list[str], what: str) -> list[str]:
    keyed = df.filter(pl.all_horizontal(pl.col(c) != "" for c in cols))
    dup = keyed.group_by(cols).len().filter(pl.col("len") > 1).sort(cols)
    return [f"duplicate {what}: {' / '.join(str(r[c]) for c in cols)}" for r in dup.to_dicts()]


def check_labels(df: pl.DataFrame, candidates: pl.DataFrame | None = None) -> LabelReport:
    """Validate the owner's file; ``candidates`` (the H1 CSV) adds the coverage check."""
    rep = LabelReport()
    missing_cols = [c for c in LABEL_COLUMNS if c not in df.columns]
    if missing_cols:
        rep.errors.append(f"missing columns: {', '.join(missing_cols)}")
        return rep
    for r in df.select(LABEL_COLUMNS).iter_rows(named=True):
        rep.errors += _row_errors(r)
        rep.warnings += _row_warnings(r)
    rep.errors += _duplicates(df, ["candidate_id"], "candidate_id")
    rep.errors += _duplicates(df, ["team", "coach_name", "announced_date"], "departure")
    if candidates is not None:
        changes = candidates.filter(pl.col("change_kind") != "none_recorded")
        want, have = set(changes["candidate_id"]), set(df["candidate_id"])
        sched = set(df.filter(pl.col("origin") == "schedule")["candidate_id"])
        for cid in sorted(want - have):
            rep.warnings.append(f"{cid}: schedule candidate has no row in the labels file")
        for cid in sorted(sched - set(candidates["candidate_id"])):
            rep.warnings.append(f"{cid}: origin schedule but not in the candidates file")
        rep.counts["candidates_missing"] = len(want - have)
    verified = df.filter(pl.col("verified_by_owner") == "y")
    rep.counts.update(
        rows=df.height,
        schedule=int((df["origin"] == "schedule").sum()),
        source_only=int((df["origin"] == "source_only").sum()),
        verified=verified.height,
        unverified=df.height - verified.height,
        unverified_suggested=int(
            ((df["verified_by_owner"] != "y") & (df["prefill"] == "suggested")).sum()
        ),
        positive_verified=int(verified["departure_type"].is_in(POSITIVE_TYPES).sum()),
        verified_blank_date=int((verified["announced_date"] == "").sum()),
    )
    counts = verified.group_by("departure_type").len().sort("departure_type")
    rep.by_type = {r["departure_type"]: r["len"] for r in counts.to_dicts()}
    return rep


def summary_text(rep: LabelReport, *, limit: int = 25) -> str:
    c = rep.counts
    lines = []
    if "rows" in c:
        lines.append(
            f"{c['rows']} rows ({c['schedule']} schedule, {c['source_only']} source_only): "
            f"{c['verified']} verified by the owner, {c['unverified']} not yet "
            f"({c['unverified_suggested']} with a suggestion)"
        )
        if "candidates_missing" in c:
            lines.append(f"schedule candidates without a row: {c['candidates_missing']}")
        types = ", ".join(f"{k} {v}" for k, v in rep.by_type.items()) or "none"
        lines.append(f"verified by type: {types}; positive (spec 8.5): {c['positive_verified']}")
        lines.append(
            f"verified with a blank announced_date (the model uses last_game_date): "
            f"{c['verified_blank_date']}"
        )
    for kind, items in (("ERROR", rep.errors), ("warning", rep.warnings)):
        lines += [f"{kind}: {m}" for m in items[:limit]]
        if len(items) > limit:
            lines.append(f"... {len(items) - limit} more {kind.lower()}s")
    lines.append(f"{len(rep.errors)} errors, {len(rep.warnings)} warnings")
    return "\n".join(lines)
