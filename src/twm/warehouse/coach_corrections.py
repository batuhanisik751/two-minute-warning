"""Cited head-coach corrections (H1b): ``data/manual/coach_corrections.csv``.

nflverse's schedule names a head coach per team and game (``home_coach`` / ``away_coach``), but
it is wrong in places: a fired coach listed all season (2015 MIA/TEN, 2016 LA, 2019 CAR,
2024 NYJ/NO/CHI, 2025 TEN/NYG), a change one game early (2007 ATL) or weeks off (2000
ARI/DET/WAS), a 2026 schedule listing coaches fired in January, misspelled names, and one name
shared by two people (Jim E. Mora, IND 1999-2001, and Jim L. Mora). The build applies this
committed file to ``fact_game`` BEFORE the coach tables, so ``coach_game``, ``dim_coach``,
``coach_team_season``, the Decision Report Card and the hot-seat candidates all agree. Every row
cites a public page (``source_url`` and a ``quote`` of at most 25 words); nothing is filled from
memory.

Kinds (``coach_out`` exactly as the schedule spells it; ``coach_in`` as the source does):

- ``season``: every game of ``team`` in ``season`` (only those kicking off on a US-Eastern date
  after ``from_date`` when it is given) is credited to ``coach_in``; each must list
  ``coach_out``;
- ``from_date``: an in-season change on ``from_date`` (the cited announcement date): the
  team-season's games listing ``coach_out`` or ``coach_in`` go to ``coach_out`` up to that
  date and to ``coach_in`` on every later date. This adds a change the schedule misses and moves
  one it records a week early or late; every game after the date must list one of the two;
- ``rename``: every game listing ``coach_out`` (within ``season`` / ``team`` when given) gets
  the spelling ``coach_in``.

Every row is matched against the schedule as published, and the build stops
(:class:`CoachCorrectionError`) when a row matches no game, changes nothing (the schedule now
agrees: remove the row), names a ``coach_out`` the schedule does not list for those games, or
covers a team-game another row covers; also on a malformed or duplicate row. A build of fewer
seasons (the scheduled job builds from ``pipeline.build_start``) skips the rows it cannot check:
a row whose ``season`` is outside the built range, and a season-less ``rename`` that matches no
game when the build does not start at the first season (so stale season-less renames are caught
only by a full build, the one run on the Mac when the file changes). The manifest lists every
applied row with the number of team-games it changed, and every skipped row with ``skipped``;
``available_at`` is not touched.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

FILE = "coach_corrections.csv"
COLUMNS = ("kind", "season", "team", "coach_out", "coach_in", "from_date", "source_url",
           "quote", "checked_by")  # fmt: skip
KINDS = ("season", "from_date", "rename")
MAX_QUOTE_WORDS = 25


class CoachCorrectionError(ValueError):
    """``coach_corrections.csv`` is malformed or does not fit the schedule: the build stops."""


@dataclass(frozen=True)
class Correction:
    line: int
    kind: str
    season: int | None
    team: str | None
    coach_out: str
    coach_in: str
    from_date: date | None
    source_url: str

    def describe(self) -> str:
        where = " ".join(str(x) for x in (self.season, self.team) if x is not None) or "all"
        when = f" from {self.from_date.isoformat()}" if self.from_date else ""
        return f"line {self.line} {self.kind} {where}: {self.coach_out} -> {self.coach_in}{when}"


def corrections_path() -> Path:
    """The file next to ``player_id_overrides.csv`` (config ``paths.manual``). Offline tests
    point the overrides at a tmp folder, so they never read the committed corrections."""
    from twm import ids

    return ids.overrides_path().with_name(FILE)


def _row(line: int, row: dict[str, str | None], problems: list[str]) -> Correction | None:
    v = {k: (row.get(k) or "").strip() for k in COLUMNS}
    bad = []
    if v["kind"] not in KINDS:
        bad.append(f"unknown kind {v['kind']!r} (known: {', '.join(KINDS)})")
    missing = [k for k in ("coach_out", "coach_in", "source_url", "quote", "checked_by")
               if not v[k]]  # fmt: skip
    if v["kind"] in ("season", "from_date"):
        missing += [k for k in ("season", "team") if not v[k]]
    if v["kind"] == "from_date" and not v["from_date"]:
        missing.append("from_date")
    if missing:
        bad.append(f"{', '.join(missing)} required")
    if v["kind"] == "rename" and v["from_date"]:
        bad.append("a rename takes no from_date")
    if v["coach_out"] and v["coach_out"] == v["coach_in"]:
        bad.append("coach_out equals coach_in")
    if v["source_url"] and not v["source_url"].startswith(("https://", "http://")):
        bad.append("source_url must be a web address")
    if len(v["quote"].split()) > MAX_QUOTE_WORDS:
        bad.append(f"quote longer than {MAX_QUOTE_WORDS} words")
    season, day = None, None
    try:
        season = int(v["season"]) if v["season"] else None
        day = date.fromisoformat(v["from_date"]) if v["from_date"] else None
    except ValueError as e:
        bad.append(f"bad season or from_date ({e})")
    if bad:
        problems.append(f"line {line}: " + "; ".join(bad))
        return None
    return Correction(line, v["kind"], season, v["team"] or None, v["coach_out"], v["coach_in"],
                      day, v["source_url"])  # fmt: skip


def read_corrections(path: Path) -> list[Correction]:
    """The validated rows of ``path`` (a missing file means none). Raises
    :class:`CoachCorrectionError` naming every malformed row and every duplicate (same kind,
    season, team and coach_out)."""
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = tuple(reader.fieldnames or ())
        if header != COLUMNS:
            raise CoachCorrectionError(f"{path}: the header must be {','.join(COLUMNS)} "
                                       f"(got {','.join(header)})")  # fmt: skip
        raw = [(i + 2, r) for i, r in enumerate(reader)
               if any((x or "").strip() for x in r.values())]  # fmt: skip
    problems: list[str] = []
    rows, seen = [], {}
    for line, r in raw:
        c = _row(line, r, problems)
        if c is None:
            continue
        key = (c.kind, c.season, c.team, c.coach_out)
        if key in seen:
            problems.append(f"line {line}: repeats line {seen[key]} ({c.kind} {c.coach_out})")
            continue
        seen[key] = line
        rows.append(c)
    if problems:
        raise CoachCorrectionError(f"{path}: {len(problems)} invalid row(s): "
                                   + "; ".join(problems))  # fmt: skip
    return rows


def _sides(games: pl.DataFrame) -> pl.DataFrame:
    """One row per team per game: i, game_id, season, gameday, side, team, coach."""
    keep = ("game_id", "season", "gameday")
    return pl.concat([
        games.select(*keep, side=pl.lit(side), team=pl.col(f"{side}_team"),
                     coach=pl.col(f"{side}_coach"))
        for side in ("home", "away")
    ]).with_row_index("i")  # fmt: skip


def _listed(df: pl.DataFrame) -> str:
    return ", ".join(sorted({str(x) for x in df["coach"].to_list()}))


def _plan(c: Correction, s: pl.DataFrame) -> tuple[pl.DataFrame, str | None]:
    """The team-games ``c`` covers, each with its ``new`` coach, or the reason it does not fit."""
    scope = pl.lit(True)
    if c.season is not None:
        scope &= pl.col("season") == c.season
    if c.team is not None:
        scope &= pl.col("team") == c.team
    ts = s.filter(scope)
    if c.kind == "rename":
        cov = ts.filter(pl.col("coach") == c.coach_out).with_columns(new=pl.lit(c.coach_in))
        return cov, None if cov.height else "matches no game"
    later = pl.col("gameday") > c.from_date if c.from_date is not None else pl.lit(True)
    after = ts.filter(later)
    if not after.height:
        return after, "matches no game"
    if c.kind == "season":
        other = after.filter(pl.col("coach").ne_missing(c.coach_out))
        if other.height:
            return after, (f"coach_out is not the schedule's coach for {other.height} of its "
                           f"{after.height} games (they list {_listed(other)})")  # fmt: skip
        return after.with_columns(new=pl.lit(c.coach_in)), None
    pair = pl.col("coach").is_in([c.coach_out, c.coach_in]).fill_null(False)
    other = after.filter(~pair)
    if other.height:
        return after, (f"{other.height} games after {c.from_date} list neither coach_out nor "
                       f"coach_in ({_listed(other)})")  # fmt: skip
    if not ts.filter(pl.col("coach") == c.coach_out).height:
        return after, f"coach_out is not the schedule's coach for any game ({_listed(ts)})"
    new = pl.when(later).then(pl.lit(c.coach_in)).otherwise(pl.lit(c.coach_out))
    return ts.filter(pair).with_columns(new=new), None


def apply_corrections(
    games: pl.DataFrame, rows: list[Correction], *, first_season: int | None = None
) -> tuple[pl.DataFrame, list[dict[str, Any]]]:
    """``games`` (game_id, season, gameday [US-Eastern date], home/away_team, home/away_coach)
    with the corrections applied, and one manifest entry per row (``games``: team-games
    changed). Raises :class:`CoachCorrectionError` naming every row that does not fit and every
    pair of rows covering one team-game (module docstring)."""
    if not rows:
        return games, []
    s = _sides(games)
    built = {int(x) for x in games["season"].unique().to_list()}
    # only a build (first_season given) skips; a direct call checks every row strictly
    ranged = first_season is not None and bool(built)
    partial = ranged and min(built) > first_season
    plans, problems, skipped = [], [], []
    for c in rows:
        if ranged and c.season is not None and not min(built) <= c.season <= max(built):
            skipped.append((c, "its season is not in this build"))
            continue
        cov, problem = _plan(c, s)
        if c.kind == "rename" and c.season is None and problem == "matches no game" and partial:
            skipped.append((c, "matches no game in this partial build"))
            continue
        changed = cov.filter(pl.col("new").ne_missing(pl.col("coach"))).height if not problem else 0
        if problem is None and not changed:
            problem = "changes nothing: the schedule already agrees (remove the row)"
        if problem:
            problems.append(f"{c.describe()}: {problem}")
        else:
            plans.append((c, cov, changed))
    owner: dict[int, Correction] = {}
    clash: dict[tuple[int, int], int] = {}
    for c, cov, _ in plans:
        for i in cov["i"].to_list():
            if i in owner:
                key = (owner[i].line, c.line)
                clash[key] = clash.get(key, 0) + 1
            else:
                owner[i] = c
    problems += [f"lines {a} and {b} both cover {n} team-game(s)" for (a, b), n in clash.items()]
    if problems:
        raise CoachCorrectionError(f"{corrections_path()}: {len(problems)} row(s) do not fit "
                                   "the schedule: " + "; ".join(problems))  # fmt: skip
    out = games
    if plans:
        upd = pl.concat([cov.select("game_id", "side", "new") for _, cov, _ in plans])
        for side in ("home", "away"):
            u = upd.filter(pl.col("side") == side)
            mapping = dict(zip(u["game_id"].to_list(), u["new"].to_list(), strict=True))
            col = f"{side}_coach"
            out = out.with_columns(
                pl.col("game_id").replace_strict(mapping, default=pl.col(col),
                                                 return_dtype=pl.String).alias(col))  # fmt: skip

    def entry(c: Correction, n: int) -> dict[str, Any]:
        day = c.from_date.isoformat() if c.from_date else None
        return {"line": c.line, "kind": c.kind, "season": c.season, "team": c.team,
                "coach_out": c.coach_out, "coach_in": c.coach_in, "from_date": day,
                "games": n}  # fmt: skip

    applied = [entry(c, n) for c, _, n in plans]
    applied += [{**entry(c, 0), "skipped": why} for c, why in skipped]
    return out, sorted(applied, key=lambda a: a["line"])
