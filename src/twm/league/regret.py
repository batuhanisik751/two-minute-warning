"""`twm league regret`: lineup regret per past week of a season (PROJECT_SPEC 8.3 feature 2;
step F3), from the box scores `twm league sync --week N` stored in data/league.duckdb.

For each week of the owner's team (``league_is_mine``):

- **actual**: the points of the lineup the owner started (box-score starters);
- **hindsight-optimal**: the best legal lineup of the players the owner had that week (starters
  and bench; an IR-slot player cannot start) by their actual points;
- **decision-time optimal**: the same, chosen by ESPN's projected points in the box score (the
  projection ESPN showed before kickoff; a player on a bye is projected 0), scored by what those
  players actually made;
- **lost to decisions** = decision-time optimal - actual (negative: the owner beat ESPN's
  projections); **lost to luck** = hindsight - decision-time optimal (never negative).

The lineups come from :func:`twm.league.lineup.optimize` (its docstring has the rules). A week
counts only when its box scores were synced after its last game ended (the warehouse's
``dim_week.last_game_end_utc_est``; without a warehouse: a week before ESPN's current week).
Output: NFL player names and numbers only, never a fantasy team, owner or member id.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import duckdb

from twm.league import store
from twm.league.espn_client import NOT_STARTING
from twm.league.lineup import Candidate, Lineup, optimize, starting_slots

IR = "IR"  # espn_api.football.constant.POSITION_MAP[21]: the injured-reserve slot


@dataclass(frozen=True)
class Line:
    """One box-score line of the owner's team in a week."""

    espn_id: int
    name: str
    position: str
    slot: str  # ESPN lineup slot: QB, RB, RB/WR/TE, BE, IR, ...
    points: float | None
    projected: float | None
    on_bye: bool = False

    @property
    def starter(self) -> bool:
        return self.slot not in NOT_STARTING

    @property
    def actual(self) -> float:
        return float(self.points or 0.0)

    @property
    def expected(self) -> float:
        """ESPN's projection as the decision-time lineup uses it (a bye or none: 0)."""
        return 0.0 if self.on_bye else float(self.projected or 0.0)


@dataclass(frozen=True)
class WeekRegret:
    week: int
    actual: float
    decision: float  # the decision-time optimal lineup's actual points
    hindsight: float
    decision_lineup: Lineup
    hindsight_lineup: Lineup
    started: frozenset[int]  # the owner's starters (ESPN ids)
    no_projection: int = 0  # lines without an ESPN projection (counted as 0)
    empty_slots: int = 0  # starting slots the owner left empty

    @property
    def lost_decisions(self) -> float:
        return round(self.decision - self.actual, 2)

    @property
    def lost_luck(self) -> float:
        return round(self.hindsight - self.decision, 2)


def _candidates(lines: Sequence[Line], slots: dict[str, int], *, projected: bool) -> list:
    out = []
    for ln in lines:
        if ln.slot == IR:  # an IR-slot player cannot start without a roster move
            continue
        c = Candidate(ln.espn_id, ln.position, ln.expected if projected else ln.actual,
                      prefer=ln.starter)  # fmt: skip
        if ln.starter and ln.slot in slots and not c.fits(ln.slot):
            c = Candidate(c.key, c.position, c.value, extra_slot=ln.slot, prefer=True)
        out.append(c)
    return out


def week_regret(week: int, lines: Sequence[Line], slots: dict[str, int]) -> WeekRegret:
    """One week's actual, decision-time optimal and hindsight-optimal points (``slots``: the
    starting slots of :func:`twm.league.lineup.starting_slots`)."""
    points = {ln.espn_id: ln.actual for ln in lines}
    starters = [ln for ln in lines if ln.starter]
    decision = optimize(_candidates(lines, slots, projected=True), slots)
    hindsight = optimize(_candidates(lines, slots, projected=False), slots)
    filled = sum(1 for ln in starters if ln.slot in slots)
    return WeekRegret(
        week=week,
        actual=round(sum(ln.actual for ln in starters), 2),
        decision=round(sum(points[k] for k in decision.keys), 2),
        hindsight=round(hindsight.value, 2),
        decision_lineup=decision,
        hindsight_lineup=hindsight,
        started=frozenset(ln.espn_id for ln in starters),
        no_projection=sum(
            1 for ln in lines if ln.projected is None and not ln.on_bye and ln.slot != IR
        ),  # fmt: skip
        empty_slots=max(sum(slots.values()) - filled, 0),
    )


@dataclass
class SeasonRegret:
    season: int
    weeks: list[WeekRegret] = field(default_factory=list)
    names: dict[int, str] = field(default_factory=dict)  # ESPN id -> NFL player name
    left_out: list[int] = field(default_factory=list)  # weeks synced before their games ended
    notes: list[str] = field(default_factory=list)

    def total(self, attr: str) -> float:
        return round(sum(getattr(w, attr) for w in self.weeks), 2)


def game_ends(warehouse: Path | None, season: int) -> dict[int, datetime] | None:
    """Regular-season week -> its last game's estimated end (naive UTC) from the warehouse's
    dim_week, opened read-only; None without a warehouse (or while a build holds it)."""
    if warehouse is None or not warehouse.exists():
        return None
    try:
        con = duckdb.connect(str(warehouse), read_only=True)
    except duckdb.Error:
        return None
    try:
        rows = con.execute(
            "SELECT week, last_game_end_utc_est FROM dim_week WHERE season = ? AND "
            "season_type = 'REG'", [season]
        ).fetchall()  # fmt: skip
    except duckdb.Error:
        return None
    finally:
        con.close()
    return {int(w): t for w, t in rows if t is not None}


class RegretUnavailableError(LookupError):
    """Nothing to compute (no box scores, the owner's team not identified): one plain line."""


BOX_SQL = """
    SELECT week, synced_at, espn_id, player_name, position, lineup_slot, points,
           projected_points, on_bye
    FROM league_box_scores WHERE league_id = ? AND season = ? AND league_team_id = ?
    ORDER BY week, espn_id
"""


def load_season(
    con: duckdb.DuckDBPyConnection, season: int | None = None, warehouse: Path | None = None
) -> SeasonRegret:
    """Every final week of ``season`` (default: the latest synced) for the owner's team."""
    part = store.latest(con, "league_box_scores", season)
    if part is None:
        raise RegretUnavailableError(
            "My League: no box scores synced" + (f" for {season}" if season else "")
            + ": run `uv run twm league sync --week N` for each past week")  # fmt: skip
    lid, season = part.league_id, part.season
    team = store.my_team(con, lid, season)
    if team is None:
        raise RegretUnavailableError(
            "My League: your team was not identified in the last sync (ESPN_SWID matched no "
            "team owner), so there is no lineup to review")  # fmt: skip
    settings = store.settings_of(con, lid, season)
    slots, notes = starting_slots(store.slot_counts(settings))
    current = int(settings.get("current_week") or 0)
    ends = game_ends(warehouse, season)
    if ends is None:
        notes.append("no warehouse: a week counts once ESPN's current week is past it")
    by_week: dict[int, list] = {}
    for r in con.execute(BOX_SQL, [lid, season, team]).fetchall():
        by_week.setdefault(int(r[0]), []).append(r)
    out = SeasonRegret(season=season, notes=notes)
    scores = dict(con.execute(
        "SELECT week, score FROM league_matchups WHERE league_id = ? AND season = ? AND "
        "league_team_id = ?", [lid, season, team]).fetchall())  # fmt: skip
    for week, rows in sorted(by_week.items()):
        synced = max(r[1] for r in rows)
        final = synced >= ends[week] if ends is not None and week in ends else week < current
        if not final:
            out.left_out.append(week)
            continue
        lines = [Line(int(r[2]), str(r[3] or ""), str(r[4] or ""), str(r[5] or ""), r[6], r[7],
                      bool(r[8])) for r in rows]  # fmt: skip
        out.names |= {ln.espn_id: ln.name for ln in lines}
        wr = week_regret(week, lines, slots)
        out.weeks.append(wr)
        score = scores.get(week)
        if score is not None and abs(float(score) - wr.actual) > 0.011:
            out.notes.append(f"week {week}: ESPN's matchup score {float(score):.2f} is not the "
                             f"starters' sum {wr.actual:.2f} (a stat correction or a slot the "
                             "tool does not model)")  # fmt: skip
    return out


HEAD = ("week", "actual", "decision-time best", "hindsight best", "lost to decisions",
        "lost to luck")  # fmt: skip
MEANING = (
    "Lost to decisions: what a lineup set by ESPN's own projections (before kickoff) would have "
    "added (negative: you beat the projections). Lost to luck: the rest of the gap to the best "
    "lineup in hindsight."
)


def _table(rows: list[tuple[str, ...]]) -> list[str]:
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    out = ["  ".join(c.rjust(w) for c, w in zip(r, widths, strict=True)) for r in rows]
    out.insert(1, "  ".join("-" * w for w in widths))
    return out


def _row(label: str, actual: float, decision: float, hindsight: float, lost_d: float,
         lost_l: float) -> tuple[str, ...]:  # fmt: skip
    return (label, f"{actual:.2f}", f"{decision:.2f}", f"{hindsight:.2f}", f"{lost_d:+.2f}",
            f"{lost_l:.2f}")  # fmt: skip


def _who(res: SeasonRegret, keys: frozenset[int]) -> str:
    return ", ".join(sorted(res.names.get(k) or str(k) for k in keys))


def season_text(res: SeasonRegret, slots_line: str = "") -> list[str]:
    """The per-week table, the season totals and what the projections would have changed."""
    n = len(res.weeks)
    lines = [f"Lineup regret, {res.season}: your team, {n} week(s) from ESPN box scores"
             + (f" ({slots_line})" if slots_line else "")]  # fmt: skip
    if res.left_out:
        weeks = ", ".join(map(str, res.left_out))
        lines.append(f"Left out (synced before the week's games were over): week(s) {weeks}; "
                     "sync again with `uv run twm league sync --week N`.")  # fmt: skip
    if not res.weeks:
        return [*lines, "No finished week to review yet."]
    rows = [HEAD] + [_row(str(w.week), w.actual, w.decision, w.hindsight, w.lost_decisions,
                          w.lost_luck) for w in res.weeks]  # fmt: skip
    cols = ("actual", "decision", "hindsight", "lost_decisions", "lost_luck")
    rows.append(_row("season", *(res.total(a) for a in cols)))
    lines += ["", *_table(rows), "", MEANING]
    changes = []
    for w in res.weeks:
        add, drop = w.decision_lineup.keys - w.started, w.started - w.decision_lineup.keys
        if add or drop:
            ins, outs = _who(res, add) or "-", _who(res, drop) or "-"
            changes.append(f"  week {w.week}: start {ins} instead of {outs} "
                           f"({w.lost_decisions:+.2f} points)")  # fmt: skip
    if changes:
        lines += ["", "What ESPN's projections would have started instead:", *changes]
    missing = sum(w.no_projection for w in res.weeks)
    empty = sum(w.empty_slots for w in res.weeks)
    if missing:
        lines.append(f"Note: {missing} player-week(s) had no ESPN projection (counted as 0).")
    if empty:
        lines.append(f"Note: {empty} starting slot(s) were left empty in your actual lineups.")
    lines += [f"Note: {x}" for x in res.notes]
    return lines
