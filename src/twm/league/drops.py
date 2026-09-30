"""Drop candidates on the owner's roster (PROJECT_SPEC 8.3 feature 1). Step F4 replaced F3's
"more players than the position's slots plus its FLEX share" rule with the lineup optimizer:

1. **A number per player** (the IR slot left out: an IR-slot player is never suggested):
   QB/RB/WR/TE: Regression Watch's stored rest-of-season projection (points per game) of the
   week; a player outside its universe: his season-to-date points per game from the warehouse
   (config/scoring.yaml, regular-season weeks up to the list's week), labelled "season average,
   not a projection"; K and D/ST: the streamer's chance of the week (it only matters when two
   are rostered, since those slots take one position each).
2. **The best lineup by those numbers** (:func:`twm.league.lineup.optimize` over the league's
   starting slots): its starters are kept.
3. **The candidate** is the lowest-numbered QB/RB/WR/TE non-starter; a spare K or D/ST (two
   rostered) is listed apart with its chance, since a chance and points per game do not compare.
   With the PPG: "about N points over the W weeks left" (N = PPG x W, rounded; W = the
   regular-season weeks after the list's week, Regression Watch's ``horizon``).

Output: NFL player names and numbers only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from twm.league.lineup import Candidate, optimize, starting_slots
from twm.league.regret import IR

SKILL = ("QB", "RB", "WR", "TE")
ESPN_POSITION = {"DST": "D/ST"}
SOURCES = {"projection": "rest-of-season projection",
           "season_average": "season average, not a projection",
           "streamer": "the streamer's chance this week", "": "no number"}  # fmt: skip


@dataclass(frozen=True)
class Rated:
    espn_id: int
    name: str
    position: str  # QB, RB, WR, TE, K, DST
    slot: str  # his ESPN lineup slot in the last roster sync
    value: float | None  # points per game (QB-TE) or the streamer's chance (K, D/ST)
    source: str  # a key of SOURCES
    numbers: str = ""  # the numbers behind the value
    status: str = ""  # ESPN's injury status when not ACTIVE

    @property
    def per_game(self) -> bool:
        return self.source in ("projection", "season_average")


@dataclass
class DropView:
    lineup: list[tuple[str, Rated | None]]  # the best lineup: (slot, player or None = empty)
    bench: list[Rated]  # the non-starters (IR left out): numbered lowest first, then unnumbered
    candidate: Rated | None  # the lowest-numbered QB/RB/WR/TE non-starter
    spares: list[Rated]  # K / D/ST non-starters (two or more rostered), lowest chance first
    on_ir: int
    weeks_left: int | None
    notes: list[str] = field(default_factory=list)

    def points_left(self, r: Rated) -> int | None:
        """N of "about N points over the W weeks left" (PPG x W, rounded)."""
        if not r.per_game or r.value is None or self.weeks_left is None:
            return None
        return round(r.value * self.weeks_left)


def _f(x: Any, fmt: str = ".2f") -> str:
    return "-" if x is None else format(float(x), fmt)


def read_warehouse(warehouse: Path | None, sql: str, args: list[Any]) -> list[tuple] | None:
    """Rows of a read-only warehouse query; None without a warehouse or when it cannot be read."""
    if warehouse is None or not warehouse.exists():
        return None
    try:
        con = duckdb.connect(str(warehouse), read_only=True)
    except duckdb.Error:
        return None
    try:
        return con.execute(sql, args).fetchall()
    except duckdb.Error:
        return None
    finally:
        con.close()


def season_average(
    warehouse: Path | None, season: int, week: int, ids: list[str]
) -> dict[str, tuple[float, int]]:
    """gsis_id -> (points per game, games) over ``season``'s regular-season weeks up to
    ``week`` (config/scoring.yaml via :func:`twm.scoring.score_sql`)."""
    if not ids:
        return {}
    from twm.scoring import score_sql

    marks = ", ".join("?" * len(ids))
    rows = read_warehouse(warehouse, f"SELECT player_id, count(*), sum({score_sql()}) FROM "
                 "fact_player_week WHERE season = ? AND season_type = 'REG' AND week <= ? AND "
                 f"player_id IN ({marks}) GROUP BY player_id",
                 [season, week, *ids])  # fmt: skip
    return {str(p): (round(float(t) / int(n), 2), int(n)) for p, n, t in rows or [] if n}


def weeks_after(warehouse: Path | None, season: int, week: int) -> int | None:
    """The regular-season weeks of ``season`` after ``week`` (dim_week)."""
    rows = read_warehouse(warehouse, "SELECT count(*) FROM dim_week WHERE season = ? AND "
                 "season_type = 'REG' AND week > ?", [season, week])  # fmt: skip
    return int(rows[0][0]) if rows and rows[0][0] else None


def _status(p: dict[str, Any]) -> str:
    s = (p.get("injury_status") or "").upper()
    return "" if s in ("", "ACTIVE", "NORMAL") else s


def rate(p: dict[str, Any], proj: dict, stream: dict, avg: dict) -> Rated:
    """One roster row's number (module docstring, step 1)."""
    from twm.league.personal import chance_text, projection_numbers

    pos, ent = p["position"], p["entity_id"]
    base = {"espn_id": int(p["espn_id"]), "name": str(p["player_name"] or ""), "position": pos,
            "slot": str(p["lineup_slot"] or ""), "status": _status(p)}  # fmt: skip
    if pos in SKILL:
        row = proj.get(ent) if ent is not None else None
        if row is not None and row.get("score") is not None:
            return Rated(**base, value=float(row["score"]), source="projection",
                         numbers=projection_numbers(row.get("reasons_json")))  # fmt: skip
        if ent in avg:
            ppg, games = avg[ent]
            text = f"{ppg:.2f} points per game over {games} game(s) so far"
            return Rated(**base, value=ppg, source="season_average", numbers=text)
        return Rated(**base, value=None, source="")
    row = stream.get((pos, ent)) if ent is not None else None
    if row is None:
        return Rated(**base, value=None, source="")
    band = json.loads(row["band"]) if row.get("band") else {}
    chance = band.get("chance", row.get("score"))
    text = chance_text(pos, row.get("band"), row.get("score"))
    return Rated(**base, value=None if chance is None else float(chance), source="streamer",
                 numbers=f"{text}, rank {row['rank']} of the week's pool")  # fmt: skip


def _order(r: Rated) -> tuple:
    group = 0 if r.per_game else (1 if r.value is not None else 2)
    return (group, r.value if r.value is not None else 0.0, r.name, r.espn_id)


def drop_view(
    roster: pl.DataFrame,
    slot_counts: dict[str, int],
    streamer: pl.DataFrame,
    projections: pl.DataFrame,
    *,
    season: int,
    week: int,
    warehouse: Path | None = None,
) -> DropView:
    """The owner's best lineup by rest-of-season numbers, his bench and the drop candidate.
    ``roster``: the owner's roster rows (personal.LeagueView.rosters of his team);
    ``streamer`` / ``projections``: the week's pinned K / D/ST rows and Regression Watch rows."""
    active = roster.filter(pl.col("lineup_slot").fill_null("") != IR).sort("espn_id")
    proj = {r["entity_id"]: r for r in projections.iter_rows(named=True)}
    stream = {(r["position"], r["entity_id"]): r for r in streamer.iter_rows(named=True)}
    skill = active.filter(pl.col("position").is_in(SKILL) & pl.col("entity_id").is_not_null())
    missing = [e for e in skill.get_column("entity_id").to_list() if e not in proj]
    avg = season_average(warehouse, season, week, sorted(set(missing)))
    rated = [rate(p, proj, stream, avg) for p in active.iter_rows(named=True)]
    slots, notes = starting_slots(slot_counts)
    cands = []
    for r in rated:
        c = Candidate(r.espn_id, ESPN_POSITION.get(r.position, r.position), r.value or 0.0,
                      prefer=r.slot not in ("BE", IR, ""))  # fmt: skip
        if r.slot in slots and not c.fits(r.slot):  # a slot ESPN let him start in
            c = Candidate(c.key, c.position, c.value, extra_slot=r.slot, prefer=True)
        cands.append(c)
    best = optimize(cands, slots)
    by_id = {r.espn_id: r for r in rated}
    lineup = [(s, None if k is None else by_id[k]) for s, k in best.picks]
    bench = sorted((r for r in rated if r.espn_id not in best.keys), key=_order)
    counts = {p: sum(1 for r in rated if r.position == p) for p in ("K", "DST")}
    spares = [r for r in bench if r.position in counts and counts[r.position] >= 2]
    horizons = [h for h in (r.get("horizon") for r in proj.values()) if h is not None]
    weeks_left = max(horizons) if horizons else weeks_after(warehouse, season, week)
    candidate = next((r for r in bench if r.per_game), None)
    return DropView(lineup, bench, candidate, spares, roster.height - active.height,
                    None if weeks_left is None else int(weeks_left), notes)  # fmt: skip


RULE = ("Drop candidates on your roster: your best lineup by rest-of-season numbers (Regression "
        "Watch's projection in points per game; a player outside it: his season average; K and "
        "D/ST: the streamer's chance) keeps its starters, and the candidate is the lowest-"
        "numbered QB/RB/WR/TE on the bench. IR-slot players are never suggested.")  # fmt: skip


def value_text(r: Rated) -> str:
    if r.value is None:
        return "-"
    return f"{r.value:.2f}" if r.per_game else f"{100 * r.value:.0f}%"


def left_text(view: DropView, r: Rated) -> str:
    n = view.points_left(r)
    return "" if n is None else f"about {n} points over the {view.weeks_left} weeks left"


def describe(view: DropView, r: Rated) -> str:
    """'Name (POS) [status]: the numbers, about N points over the W weeks left (source)'."""
    status = f" [ESPN status: {r.status}]" if r.status else ""
    left = left_text(view, r)
    what = r.numbers
    if r.source == "projection":
        what = f"projects {r.value:.2f} points per game for the rest of the season {what}"
    return (f"{r.name} ({r.position}){status}: {what}" + (f", {left}" if left else "")
            + f" ({SOURCES[r.source]})")  # fmt: skip


def drop_text(view: DropView | None) -> list[str]:
    """The drop-candidate lines of `twm league radar`."""
    if view is None:
        return [RULE, "  your team was not identified in the last sync (ESPN_SWID matched no "
                "team owner)."]  # fmt: skip
    kept = [f"{s} {r.name} {value_text(r)}" for s, r in view.lineup if r is not None]
    out = [RULE, "  best lineup: " + (", ".join(kept) or "-")]
    if view.bench:
        out.append("  bench (lowest first): " + ", ".join(
            f"{r.name} {r.position} {value_text(r)}" for r in view.bench))  # fmt: skip
    if view.candidate is None:
        out.append("  candidate: none (no QB/RB/WR/TE on the bench has a number)")
    else:
        out.append(f"  candidate: {describe(view, view.candidate)}")
    out += [f"  spare {r.position}: {describe(view, r)}" for r in view.spares]
    unrated = [r.name for r in view.bench if r.value is None and r not in view.spares]
    if unrated:
        why = "not in the week's lists, no games this season"
        out.append(f"  no number ({why}): {', '.join(unrated)}")
    if view.on_ir:
        out.append(f"  ({view.on_ir} IR-slot player(s) left out)")
    out += [f"  note: {n}" for n in view.notes]
    return out
