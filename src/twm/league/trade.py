"""`twm league trade`: the My League trade checker (PROJECT_SPEC 8.3 feature 3; step H6-a).

Reads only: data/league.duckdb (the latest sync), the predictions store (Regression Watch's
stored list of the approved model), its pinned backtest snapshot and the warehouse (schedule,
season averages). Writes nothing. docs/my_league.md "Trade checker" explains every rule:

- **Players** are matched by name against the synced rosters and free agents
  (:func:`match`: case, accents and punctuation ignored; ambiguous or unknown -> a plain error
  listing the candidates). ``--give`` must be the owner's; ``--get`` players are on ONE other
  team (the partner) and/or free agents.
- **Each side** (the owner, and the partner when there is one): every week counted (the weeks
  after the list's week N up to the league's last scoring week and the NFL's last regular-season
  week), the best legal QB/RB/WR/TE lineup (:func:`twm.league.lineup.optimize`, the league's
  slots and FLEX) by Regression Watch's rest-of-season points per game; a bye scores 0; ESPN's
  OUT / injured-reserve status counts the player out for the first week counted only; IR-slot
  players are left out. K and D/ST are not projected: fixed, the same in both lineups. Over the
  roster limit after the trade, the lowest-numbered QB/RB/WR/TE bench player is dropped.
- **The range**: the 80% interval of the change, by resampling the backtest's real per-game
  misses (actual rest-of-season PPG minus the projection) by position and games ahead
  (:func:`residual_pool`, :func:`simulate`).
- **Fantasy playoffs** (step H6-a2): the weeks after the league's regular season (synced
  ``reg_season_final_week``) are a separate stretch, "only if you make the playoffs"; the
  verdict uses the regular season. A store synced before H6-a2 has no such key: every week
  counts the same, as before, and the output says so (:func:`split_weeks`).
- **The verdict** (:func:`word`): the percent of simulated seasons in which the side gains, as
  printed: 67+ "probably helps", 33 or fewer "probably hurts", else "a close call"; always with
  the point estimate (the simulation's median: point, range and share are one distribution;
  ``--json`` keeps the projection's own change as ``projection_delta``), the range and share.
"""

from __future__ import annotations

import difflib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import numpy as np
import polars as pl

SKILL = ("QB", "RB", "WR", "TE")
FIXED = ("K", "DST")  # not projected by Regression Watch: kept the same in both lineups
LEVEL = 0.80  # the interval's coverage
N_SIM = 4000  # simulated seasons
SEED = 8_3_3  # fixed: the same trade always prints the same range
HORIZON_WINDOW = 2  # backtest rows whose weeks-left is within this of the list's
OUT_STATUSES = frozenset({"OUT", "INJURY_RESERVE"})  # ESPN statuses counted out (first week)
TAG_NAMES = {"sell_high": "Sell-high", "buy_low": "Buy-low"}  # Legit was dropped (2026-09-30)
SOURCES = {"projection": "Regression Watch projection",
           "season_average": "season average, not a projection", "": "no number"}  # fmt: skip


class TradeError(LookupError):
    """The trade cannot be checked (unknown name, missing input): one plain line."""


@dataclass(frozen=True)
class Player:
    espn_id: int
    name: str
    position: str  # QB, RB, WR, TE, K, DST
    pro_team: str | None  # the warehouse's team code (store.nfl_team), None = no NFL team
    team_id: int | None  # the fantasy team, None = a free agent
    slot: str = ""  # ESPN lineup slot of the last roster sync ("" for a free agent)
    status: str = ""  # ESPN injury status when not ACTIVE / NORMAL
    entity_id: str | None = None  # gsis id (or DST-<team>)

    @property
    def on_ir_slot(self) -> bool:
        from twm.config import RESERVE_SLOTS

        return self.slot in RESERVE_SLOTS


def norm(name: str) -> str:
    """Lower case, accents and punctuation dropped, single spaces: "D'Andre Swift" ->
    "dandre swift", "A.J. Brown" -> "aj brown"."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = re.sub(r"['.’]", "", text.lower())
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _label(p: Player, teams: dict[int, str]) -> str:
    where = "free agent" if p.team_id is None else teams.get(p.team_id, f"team {p.team_id}")
    return f"{p.name} ({p.position}, {p.pro_team or 'no NFL team'}, {where})"


def match(query: str, pool: list[Player], teams: dict[int, str]) -> Player:
    """The one player of ``pool`` named ``query``: the full name (normalized, :func:`norm`),
    else the players whose name contains every word of the query (a word may be a prefix:
    "mccaff" finds McCaffrey). None or several -> :class:`TradeError` listing candidates."""
    q = norm(query)
    if not q:
        raise TradeError("My League: an empty player name was given")
    exact = [p for p in pool if norm(p.name) == q]
    if len(exact) == 1:
        return exact[0]
    words = q.split()
    found = exact or [
        p for p in pool
        if all(any(t.startswith(w) for t in norm(p.name).split()) for w in words)
    ]  # fmt: skip
    if len(found) == 1:
        return found[0]
    if found:
        names = "; ".join(_label(p, teams) for p in sorted(found, key=lambda p: p.name))
        raise TradeError(f"My League: {query!r} matches {len(found)} players: {names}. "
                         "Use the full name.")  # fmt: skip
    close = difflib.get_close_matches(q, [norm(p.name) for p in pool], n=5, cutoff=0.6)
    near = [p for c in dict.fromkeys(close) for p in pool if norm(p.name) == c]  # closest first
    hint = ("; did you mean: " + "; ".join(_label(p, teams) for p in near)) if near else ""
    raise TradeError(f"My League: no player named {query!r} on a synced roster or among the "
                     f"synced free agents{hint}")  # fmt: skip


# ---- inputs -------------------------------------------------------------------------------


@dataclass
class LeagueData:
    """What the latest sync says: every rostered player and the synced free agents."""

    league_id: int
    season: int
    sync_week: int  # the ESPN week of the last sync
    players: list[Player]
    teams: dict[int, str]  # fantasy team id -> its name (local output only)
    my_team: int | None
    slots: dict[str, int]  # ESPN slot label -> count
    final_week: int | None  # the league's last scoring week (ESPN finalScoringPeriod)
    # the fantasy playoffs (league_settings keys a sync stores since H6-a2; None = an older sync)
    reg_final_week: int | None = None  # the regular season's last week
    playoff_teams: int | None = None  # playoff_team_count
    playoff_length: int | None = None  # playoff_matchup_period_length (weeks per matchup)
    team_count: int | None = None

    def roster(self, team_id: int) -> list[Player]:
        return [p for p in self.players if p.team_id == team_id]

    @property
    def roster_limit(self) -> int:
        """Players a team may hold outside its reserve (IR) slots: every other slot."""
        from twm.config import RESERVE_SLOTS

        return sum(n for s, n in self.slots.items() if s not in RESERVE_SLOTS)


def _status(s: object) -> str:
    text = str(s or "").upper()
    return "" if text in ("", "ACTIVE", "NORMAL", "NONE") else text


def load_league(con: duckdb.DuckDBPyConnection) -> LeagueData:
    """The rosters and free agents of the newest synced week as :class:`Player` rows."""
    from twm.league import store
    from twm.league.personal import _frame

    part = store.current(con, "league_rosters")
    if part is None:
        raise TradeError("My League: nothing synced yet: run `uv run twm league sync` first.")
    lid, season, week = part.league_id, part.season, part.week
    cols = "espn_id, player_name, position, pro_team, entity_id"
    rosters = _frame(con, f"SELECT {cols}, league_team_id, lineup_slot, injury_status FROM "
                     "league_rosters WHERE league_id = ? AND season = ? AND week = ?",
                     [lid, season, week])  # fmt: skip
    fa = store.current(con, "league_free_agents", season)
    agents = _frame(con, f"SELECT {cols} FROM league_free_agents WHERE league_id = ? AND "
                    "season = ? AND week = ?", [lid, season, fa.week if fa else -1])  # fmt: skip
    players = [
        Player(int(r["espn_id"]), str(r["player_name"] or ""), str(r["position"]),
               r["pro_team"], int(r["league_team_id"]), str(r["lineup_slot"] or ""),
               _status(r["injury_status"]), r["entity_id"])
        for r in rosters.iter_rows(named=True)
    ]  # fmt: skip
    held = {p.espn_id for p in players}
    players += [
        Player(int(r["espn_id"]), str(r["player_name"] or ""), str(r["position"]),
               r["pro_team"], None, entity_id=r["entity_id"])
        for r in agents.iter_rows(named=True) if int(r["espn_id"]) not in held
    ]  # fmt: skip
    rows = con.execute(
        "SELECT league_team_id, league_team_name FROM league_teams WHERE league_id = ? AND "
        "season = ? ORDER BY synced_at", [lid, season]
    ).fetchall()  # fmt: skip
    settings = store.settings_of(con, lid, season)

    def num(*keys: str) -> int | None:  # the first key stored as a whole number
        vals = [settings.get(k, "") for k in keys]
        return next((int(v) for v in vals if v.isdigit()), None)

    return LeagueData(lid, season, week, players, {int(t): str(n) for t, n in rows},
                      store.my_team(con, lid, season), store.slot_counts(settings),
                      num("final_week"), num("reg_season_final_week", "reg_season_count"),
                      num("playoff_team_count"), num("playoff_matchup_period_length"),
                      num("team_count"))  # fmt: skip


@dataclass(frozen=True)
class Value:
    """A player's number: rest-of-season points per game and where it comes from."""

    ppg: float | None
    source: str  # a key of SOURCES
    tag: str = ""  # Regression Watch's tag: Sell-high / Buy-low ("" = none)
    numbers: str = ""  # the numbers behind it


@dataclass
class Projections:
    week: int  # the list's week N (made after week N's games)
    horizon: int | None  # Regression Watch's weeks left after N (the stored rows')
    version: str
    rows: dict[str, dict]  # entity_id -> stored row


def projections(
    predictions: Path, season: int, sync_week: int, week: int | None, pins_path: Path | None
) -> Projections:
    """Regression Watch's stored list of the approved model: ``week``, default the latest
    stored at or before the synced ESPN week (as `twm league radar` picks its lists)."""
    from twm.league.personal import PersonalUnavailableError, pinned_versions, stored_rows

    pin = pinned_versions(pins_path).get("regression_watch")
    if pin is None or pin[0] != season:
        raise TradeError(f"My League: no approved Regression Watch model for {season} "
                         "(config/production_models.yaml): no projection to compare")  # fmt: skip
    try:
        rows = stored_rows(predictions, season, [pin[1]])
    except PersonalUnavailableError as e:
        raise TradeError(str(e)) from e
    weeks = sorted(set(rows.get_column("week").to_list()))
    if week is None:
        before = [w for w in weeks if w <= sync_week]
        if not before:
            raise TradeError(f"My League: no Regression Watch list is stored for {season} up "
                             f"to week {sync_week}: run the weekly lists first")  # fmt: skip
        week = before[-1]
    elif week not in weeks:
        stored = f" (stored: {', '.join(map(str, weeks))})" if weeks else ""
        raise TradeError(f"My League: no Regression Watch list is stored for {season} week "
                         f"{week}{stored}")  # fmt: skip
    rows = rows.filter(pl.col("week") == week)
    hs = [h for h in rows.get_column("horizon").to_list() if h is not None]
    return Projections(week, max(hs) if hs else None, pin[1],
                       {r["entity_id"]: r for r in rows.iter_rows(named=True)})  # fmt: skip


def value_of(p: Player, proj: Projections, averages: dict[str, tuple[float, int]]) -> Value:
    """Regression Watch's projection, else the season average (labelled), else no number."""
    from twm.league.personal import projection_numbers

    row = proj.rows.get(p.entity_id or "")
    if p.position in SKILL and row is not None and row.get("score") is not None:
        tag = TAG_NAMES.get(str(row.get("band") or ""), "")
        return Value(float(row["score"]), "projection", tag,
                     projection_numbers(row.get("reasons_json")))  # fmt: skip
    if p.position in SKILL and (p.entity_id or "") in averages:
        ppg, games = averages[p.entity_id or ""]
        return Value(ppg, "season_average", numbers=f"{ppg:.2f} per game over {games} game(s)")
    return Value(None, "")


@dataclass(frozen=True)
class Schedule:
    weeks: tuple[int, ...]  # the weeks counted
    plays: dict[str, frozenset[int]]  # NFL team -> its regular-season weeks with a game
    last_reg_week: int

    def has_game(self, team: str | None, week: int) -> bool:
        return team is not None and week in self.plays.get(team, frozenset())


def schedule(warehouse: Path | None, season: int, after: int, final_week: int | None) -> Schedule:
    """The weeks after ``after`` up to the league's last scoring week and the NFL's last
    regular-season week (dim_week), and each team's game weeks (fact_schedule): a bye = no game."""
    from twm.league.drops import read_warehouse

    games = read_warehouse(warehouse, "SELECT week, home_team, away_team FROM fact_schedule "
                           "WHERE season = ? AND season_type = 'REG'", [season])  # fmt: skip
    last = read_warehouse(warehouse, "SELECT max(week) FROM dim_week WHERE season = ? AND "
                          "season_type = 'REG'", [season])  # fmt: skip
    if not games or not last or last[0][0] is None:
        raise TradeError(f"My League: the warehouse has no {season} schedule (byes cannot be "
                         "known): build it first (`uv run twm build`)")  # fmt: skip
    plays: dict[str, set[int]] = {}
    for week, home, away in games:
        for team in (home, away):
            plays.setdefault(str(team), set()).add(int(week))
    last_reg = int(last[0][0])
    end = min(last_reg, final_week) if final_week else last_reg
    return Schedule(tuple(range(after + 1, end + 1)),
                    {t: frozenset(w) for t, w in plays.items()}, last_reg)  # fmt: skip


@dataclass
class ResidualPool:
    """The backtest's real per-game misses (actual rest-of-season PPG minus the projection) of
    the rows whose weeks left are near the list's, per position."""

    by_position: dict[str, np.ndarray]
    horizons: dict[str, tuple[int, int]]  # position -> (fewest, most) weeks left used
    seasons: str  # the backtest seasons, e.g. "2011-2025"


def residual_pool(horizon: int, pins_path: Path | None = None) -> ResidualPool:
    """Rows of the approved Regression Watch backtest snapshot (sha256-checked) graded (3+
    games left), per position: those within :data:`HORIZON_WINDOW` weeks of ``horizon``, else
    the nearest weeks left available."""
    from twm.pins import PinError, read_pins, read_snapshot

    try:
        pin = read_pins(pins_path).get("regression_watch")
        if pin is None:
            raise PinError("no approved regression_watch model")
        f = read_snapshot(pin, ("predictions", "outcomes"))
    except PinError as e:
        raise TradeError(f"My League: Regression Watch's backtest cannot be read ({e})") from e
    rows = (
        f["predictions"].select("season", "week", "entity_id", "rank_group", "score", "horizon")
        .join(f["outcomes"].select("season", "week", "entity_id", "ros_ppg"),
              on=["season", "week", "entity_id"], how="inner")
        .filter(pl.col("ros_ppg").is_not_null() & pl.col("score").is_not_null())
        .with_columns((pl.col("ros_ppg") - pl.col("score")).alias("miss"),
                      (pl.col("horizon") - horizon).abs().alias("gap"))
    )  # fmt: skip
    out, used = {}, {}
    for pos in SKILL:
        sub = rows.filter(pl.col("rank_group") == pos)
        if sub.height == 0:
            continue
        gap = max(HORIZON_WINDOW, int(sub.get_column("gap").min()))
        sub = sub.filter(pl.col("gap") <= gap)
        out[pos] = sub.get_column("miss").to_numpy()
        used[pos] = (int(sub.get_column("horizon").min()), int(sub.get_column("horizon").max()))
    return ResidualPool(out, used, pin.backtest_seasons)


# ---- lineups ------------------------------------------------------------------------------


def skill_slots(slot_counts: dict[str, int]) -> dict[str, int]:
    """The league's starting slots the trade checker fills: K and D/ST left out (fixed)."""
    from twm.league.lineup import starting_slots

    return {s: n for s, n in starting_slots(slot_counts)[0].items() if s not in ("K", "D/ST")}


@dataclass
class SeasonLineup:
    total: float  # expected starting points over the weeks counted
    starts: dict[int, int]  # ESPN id -> weeks he starts with a game (and not counted out)
    weekly: list[tuple[int, float, tuple[int, ...]]]  # (week, points, starters' ESPN ids)


def plays(p: Player, week: int, sched: Schedule, out_week: int | None) -> bool:
    """He has a game that week and ESPN's status does not count him out of it."""
    return sched.has_game(p.pro_team, week) and not (week == out_week and p.status in OUT_STATUSES)


def season_lineup(
    players: list[Player], values: dict[int, Value], sched: Schedule, slots: dict[str, int],
    out_week: int | None, weeks: tuple[int, ...] | None = None,
) -> SeasonLineup:  # fmt: skip
    """Every week counted (or of ``weeks``): the best legal QB/RB/WR/TE lineup (IR-slot players
    left out) by the players' points per game, 0 for a bye or a week ESPN counts him out of."""
    from twm.league.lineup import Candidate, optimize

    active = [p for p in players if p.position in SKILL and not p.on_ir_slot]
    starts: dict[int, int] = {}
    weekly, total = [], 0.0
    for w in sched.weeks if weeks is None else weeks:
        on = {p.espn_id: plays(p, w, sched, out_week) for p in active}
        cands = [Candidate(p.espn_id, p.position, (values[p.espn_id].ppg or 0.0)
                           if on[p.espn_id] else 0.0) for p in active]  # fmt: skip
        best = optimize(cands, slots)
        keys = tuple(k for _, k in best.picks if k is not None and on[k])
        for k in keys:
            starts[k] = starts.get(k, 0) + 1
        weekly.append((w, best.value, keys))
        total += best.value
    return SeasonLineup(round(total, 6), starts, weekly)


def drop_extra(
    players: list[Player], values: dict[int, Value], limit: int, slots: dict[str, int]
) -> tuple[list[Player], list[Player]]:
    """(the roster kept, the players dropped): while more than ``limit`` players sit outside
    the IR slots, drop the lowest-numbered QB/RB/WR/TE outside the best lineup by points per
    game (no number counts as 0; K and D/ST are never dropped: they are not projected)."""
    from twm.league.lineup import Candidate, optimize

    held = [p for p in players if not p.on_ir_slot]
    dropped: list[Player] = []
    while len(held) > limit:
        skill = [p for p in held if p.position in SKILL]
        best = optimize([Candidate(p.espn_id, p.position, values[p.espn_id].ppg or 0.0)
                         for p in skill], slots)  # fmt: skip
        bench = [p for p in skill if p.espn_id not in best.keys]
        if not bench:
            break
        worst = min(bench, key=lambda p: (values[p.espn_id].ppg or 0.0, p.name, p.espn_id))
        held.remove(worst)
        dropped.append(worst)
    return [p for p in players if p not in dropped], dropped


# ---- the trade ----------------------------------------------------------------------------


STRETCHES = {"season": "the rest of the season", "regular": "the regular season",
             "playoffs": "the fantasy playoffs, only if you make the playoffs"}  # fmt: skip


def split_weeks(
    weeks: tuple[int, ...], reg_final_week: int | None
) -> list[tuple[str, tuple[int, ...]]]:
    """The weeks counted as (kind, weeks) stretches, the verdict's first: the regular season,
    then the fantasy playoffs (the weeks after ``reg_final_week``); every week as one
    "season" stretch when the league's regular season is not stored (an older sync)."""
    if reg_final_week is None:
        return [("season", weeks)]
    reg = tuple(w for w in weeks if w <= reg_final_week)
    po = tuple(w for w in weeks if w > reg_final_week)
    return [(k, w) for k, w in (("regular", reg), ("playoffs", po)) if w]


@dataclass
class Stretch:
    """One side's lineups over some of the weeks counted, and the change with its range."""

    kind: str  # a key of STRETCHES
    weeks: tuple[int, ...]
    before: SeasonLineup
    after: SeasonLineup
    lo: float = 0.0  # the change's interval (LEVEL)
    hi: float = 0.0
    share_up: float | None = None  # share of simulated seasons with a gain (None: no change)
    mid: float | None = None  # the simulated change's median (None: not simulated)

    @property
    def projection_delta(self) -> float:
        """The projection's change alone (after - before), before the backtest's misses."""
        return round(self.after.total - self.before.total, 6)

    @property
    def delta(self) -> float:
        """The printed point estimate: the simulation's median, so the point, the range and
        the share come from one distribution (the projection's change when not simulated)."""
        return self.projection_delta if self.mid is None else self.mid


@dataclass
class Side:
    """One team in the trade: its stretches (the verdict's first) and the roster change."""

    team_id: int
    name: str
    mine: bool
    gives: list[Player]
    gets: list[Player]
    after_roster: list[Player]
    dropped: list[Player]
    open_spots: int  # roster spots the trade leaves empty (a pickup is not modelled)
    stretches: list[Stretch]

    @property
    def main(self) -> Stretch:  # the verdict's weeks: the regular season (or every week)
        return self.stretches[0]

    @property
    def playoffs(self) -> Stretch | None:
        return next((x for x in self.stretches[1:] if x.kind == "playoffs"), None)

    # the verdict's stretch, by its old names
    before = property(lambda self: self.main.before)
    after = property(lambda self: self.main.after)
    delta = property(lambda self: self.main.delta)
    lo = property(lambda self: self.main.lo)
    hi = property(lambda self: self.main.hi)
    share_up = property(lambda self: self.main.share_up)


@dataclass
class TradeCheck:
    season: int
    week: int  # Regression Watch's list week N
    weeks: tuple[int, ...]  # the weeks counted
    out_week: int | None  # the week ESPN's OUT / injured-reserve statuses apply to
    sides: list[Side]  # the owner first, then the partner (none when only free agents come)
    values: dict[int, Value]
    players: dict[int, Player]
    pool: ResidualPool | None
    horizon: int | None
    teams: dict[int, str]
    split: list[tuple[str, tuple[int, ...]]] = field(default_factory=list)  # split_weeks()
    notes: list[str] = field(default_factory=list)


def _pick(names: list[str], data: LeagueData) -> list[Player]:
    out = [match(n, data.players, data.teams) for n in names]
    if len({p.espn_id for p in out}) != len(out):
        raise TradeError("My League: the same player is named twice")
    fixed = [p.name for p in out if p.position not in SKILL]
    if fixed:
        raise TradeError(f"My League: {', '.join(fixed)}: K and D/ST are not projected by "
                         "Regression Watch: the checker compares QB/RB/WR/TE only")  # fmt: skip
    return out


def _side(data: LeagueData, team_id: int, out: list[Player], into: list[Player],
          values: dict[int, Value], sched: Schedule, out_week: int | None,
          split: list[tuple[str, tuple[int, ...]]]) -> Side:  # fmt: skip
    slots = skill_slots(data.slots)
    before = data.roster(team_id)
    gone = {p.espn_id for p in out}
    after, dropped = drop_extra([p for p in before if p.espn_id not in gone] + into, values,
                                data.roster_limit, slots)  # fmt: skip
    held = sum(1 for p in after if not p.on_ir_slot)
    was = sum(1 for p in before if not p.on_ir_slot)
    return Side(team_id, data.teams.get(team_id, f"team {team_id}"), team_id == data.my_team,
                out, into, after, dropped, max(0, min(was, data.roster_limit) - held),
                [Stretch(kind, weeks, season_lineup(before, values, sched, slots, out_week, weeks),
                         season_lineup(after, values, sched, slots, out_week, weeks))
                 for kind, weeks in split])  # fmt: skip


def evaluate(
    data: LeagueData, give: list[str], get: list[str], proj: Projections, sched: Schedule,
    averages: dict[str, tuple[float, int]], pool: ResidualPool | None,
) -> TradeCheck:  # fmt: skip
    """The trade's check (no I/O): match the names, build both sides, simulate the range."""
    if data.my_team is None:
        raise TradeError("My League: your team was not identified in the last sync (ESPN_SWID "
                         "matched no team owner)")  # fmt: skip
    if not give or not get:
        raise TradeError("My League: name at least one --give and one --get player")
    gives, gets = _pick(give, data), _pick(get, data)
    if {p.espn_id for p in gives} & {p.espn_id for p in gets}:
        raise TradeError("My League: a player is both given and received")
    for p in gives:
        if p.team_id != data.my_team:
            raise TradeError(f"My League: --give {_label(p, data.teams)} is not on your team")
    for p in gets:
        if p.team_id == data.my_team:
            raise TradeError(f"My League: --get {_label(p, data.teams)} is already yours")
    partners = sorted({p.team_id for p in gets if p.team_id is not None})
    if len(partners) > 1:
        names = ", ".join(data.teams.get(t, str(t)) for t in partners)
        raise TradeError(f"My League: the --get players are on {len(partners)} teams ({names}): "
                         "a trade has one partner (free agents may be added)")  # fmt: skip
    values = {p.espn_id: value_of(p, proj, averages) for p in data.players}
    out_week = data.sync_week if data.sync_week in sched.weeks else None
    split = split_weeks(sched.weeks, data.reg_final_week)
    sides = [_side(data, data.my_team, gives, gets, values, sched, out_week, split)]
    if partners:
        mine = [p for p in gets if p.team_id == partners[0]]
        sides.append(_side(data, partners[0], mine, gives, values, sched, out_week, split))
    check = TradeCheck(data.season, proj.week, sched.weeks, out_week, sides, values,
                       {p.espn_id: p for p in data.players}, pool, proj.horizon,
                       data.teams, split)  # fmt: skip
    if pool is not None:
        simulate(check, pool)
    return check


def simulate(check: TradeCheck, pool: ResidualPool, n: int = N_SIM, seed: int = SEED) -> None:
    """Each side's and stretch's interval of the change (:data:`LEVEL`): every player whose
    starts change gets ONE miss per simulated season, drawn from the backtest's misses of his
    position (:func:`residual_pool`), shared by both sides and both stretches (one season);
    the change = the sum over those players of (starts after - starts before) x (his points
    per game + his miss). A player without a number scores 0 in both lineups, draws nothing."""
    rng = np.random.default_rng(seed)
    stretches = [x for s in check.sides for x in s.stretches]
    moved = sorted({k for x in stretches for k in {*x.before.starts, *x.after.starts}
                    if check.values[k].ppg is not None})  # fmt: skip
    draws = {}
    for k in moved:
        misses = pool.by_position.get(check.players[k].position)
        draws[k] = rng.choice(misses, size=n) if misses is not None and len(misses) else np.zeros(n)
    tail = (1.0 - LEVEL) / 2.0
    for x in stretches:
        d, changed = np.zeros(n), False
        for k in moved:
            dk = x.after.starts.get(k, 0) - x.before.starts.get(k, 0)
            if dk:
                d += dk * (float(check.values[k].ppg or 0.0) + draws[k])
                changed = True
        lo, mid, hi = np.quantile(d, [tail, 0.5, 1.0 - tail])
        x.lo, x.mid, x.hi = round(float(lo), 6), round(float(mid), 6), round(float(hi), 6)
        x.share_up = float((d > 0).mean()) if changed else None


# ---- the words ----------------------------------------------------------------------------


def typical_lineup(
    players: list[Player], values: dict[int, Value], slots: dict[str, int]
) -> list[tuple[str, Player | None]]:
    """The best QB/RB/WR/TE lineup by points per game, byes aside (a typical week)."""
    from twm.league.lineup import Candidate, optimize

    active = {p.espn_id: p for p in players if p.position in SKILL and not p.on_ir_slot}
    best = optimize([Candidate(k, p.position, values[k].ppg or 0.0) for k, p in active.items()],
                    slots)  # fmt: skip
    picks = [(s, None if k is None else active[k]) for s, k in best.picks]
    return _readable(picks, values)


def _readable(
    picks: list[tuple[str, Player | None]], values: dict[int, Value]
) -> list[tuple[str, Player | None]]:
    """The same starters with the best of each position in its own slot (so FLEX shows the
    one left over); the optimizer's order when that is not a legal lineup."""
    from twm.league.lineup import ELIGIBLE

    left = sorted((p for _, p in picks if p is not None),
                  key=lambda p: (-(values[p.espn_id].ppg or 0.0), p.espn_id))  # fmt: skip
    out: list[tuple[str, Player | None]] = []
    for s in [s for s, _ in picks if s in SKILL] + [s for s, _ in picks if s not in SKILL]:
        p = next((p for p in left if p.position in ELIGIBLE.get(s, frozenset())), None)
        if p is not None:
            left.remove(p)
        out.append((s, p))
    return picks if left else out


def byes(p: Player, check: TradeCheck, sched: Schedule) -> list[int]:
    return [w for w in check.weeks if not sched.has_game(p.pro_team, w)]


def _ppg(check: TradeCheck, p: Player) -> str:
    v = check.values[p.espn_id]
    if v.ppg is None:
        return "no number"
    return f"{v.ppg:.1f}" + (" (season average, not a projection)" if v.source == "season_average"
                             else "")  # fmt: skip


def scarcity_lines(check: TradeCheck, data: LeagueData) -> list[str]:
    """The owner's starters at the traded positions in a typical week, before and after."""
    me = check.sides[0]
    slots = skill_slots(data.slots)
    pos = {p.position for p in (*me.gives, *me.gets)}
    out = []
    for when, roster in (("before", data.roster(me.team_id)), ("after", me.after_roster)):
        picks = [(s, p) for s, p in typical_lineup(roster, check.values, slots)
                 if p is not None and (p.position in pos or s not in SKILL)]  # fmt: skip
        text = ", ".join(f"{s} {p.name} {_ppg(check, p)}" for s, p in picks) or "-"
        out.append(f"your {'/'.join(sorted(pos))} starters {when} (a typical week, points per "
                   f"game): {text}")  # fmt: skip
    return out


def bye_lines(check: TradeCheck, data: LeagueData, sched: Schedule) -> list[str]:
    """Weeks counted in which two or more of the owner's typical starters are on bye."""
    me = check.sides[0]
    slots = skill_slots(data.slots)
    out = []
    for when, roster in (("before", data.roster(me.team_id)), ("after", me.after_roster)):
        starters = [p for _, p in typical_lineup(roster, check.values, slots) if p is not None]
        weeks = []
        for w in check.weeks:
            off = [p.name for p in starters if not sched.has_game(p.pro_team, w)]
            if len(off) >= 2:
                weeks.append(f"week {w} ({', '.join(off)})")
        out.append(f"bye overlap {when}: " + ("; ".join(weeks) or "none (no week with two or "
                   "more of your typical starters on bye)"))  # fmt: skip
    moving = [*me.gives, *me.gets]
    text = "; ".join(f"{p.name}: " + (f"bye week {', '.join(map(str, b))}" if b else "no bye")
                     for p in moving for b in [byes(p, check, sched)])  # fmt: skip
    out.append(f"byes of the players traded (weeks counted): {text}")
    return out


def _pts(x: float) -> str:
    return f"{x:+.0f}"


def span(weeks: tuple[int, ...]) -> str:
    if not weeks:
        return "no week left"
    return f"week {weeks[0]}" if len(weeks) == 1 else f"weeks {weeks[0]}-{weeks[-1]}"


def gains(x: Stretch) -> int:
    """The percent of simulated seasons with a gain, rounded (the number printed and judged)."""
    return round(100 * (x.share_up or 0.0))


def word(x: Stretch) -> str:
    """The reviewer's rule (2026-10-01) on the printed percent of simulated seasons with a
    gain: 67 or more "probably helps", 33 or fewer "probably hurts", else "a close call"."""
    k = gains(x)
    if k >= 67:
        return "probably helps"
    if k <= 33:
        return "probably hurts"
    return "a close call"


def _numbers(x: Stretch) -> str:
    return (
        f"about {_pts(x.delta)} points (median), range {_pts(x.lo)} to {_pts(x.hi)} "
        f"({100 * LEVEL:.0f}% interval); gains in {gains(x)}% of {N_SIM:,} simulated seasons"
    )


def verdict(s: Side, check: TradeCheck) -> str:
    """One plain sentence for a side, on the verdict's stretch (the regular season when the
    league's is stored), then what the fantasy playoff weeks add: never a number without its
    range and share, never certainty."""
    who = f"You ({s.name})" if s.mine else s.name
    x = s.main
    where = f"{span(x.weeks)}, {STRETCHES[x.kind]}"
    if x.share_up is None:
        out = f"{who}: no change in the expected lineup points ({where}) by these numbers."
    else:
        out = f"{who}: {word(x)} ({where}): {_numbers(x)}."
    po = s.playoffs
    if po is not None:
        what = (f"add {_numbers(po)}" if po.share_up is not None
                else "change nothing by these numbers")  # fmt: skip
        out += f" The fantasy playoff {span(po.weeks)} (only if you make the playoffs) {what}."
    return out


def _number(v: Value) -> str:
    if v.ppg is None:
        return "no number (not in Regression Watch's list, no game this season)"
    numbers = f": {v.numbers}" if v.numbers else ""
    return f"{v.ppg:.2f} points per game ({SOURCES[v.source]}{numbers})"


def player_lines(check: TradeCheck) -> list[str]:
    """Regression Watch's numbers and tag, and ESPN's status, of each player traded."""
    me, out = check.sides[0], []
    for p in (*me.gives, *me.gets):
        v = check.values[p.espn_id]
        if p in me.gives:
            frm = "you"
        else:
            frm = "free agent" if p.team_id is None else check.teams.get(p.team_id, "?")
        tag = f"Regression Watch tag: {v.tag}" if v.tag else "no Regression Watch tag"
        line = f"{p.name} ({p.position}, {p.pro_team or '-'}; from {frm}): {_number(v)}; {tag}"
        if p.status:
            out_now = p.status in OUT_STATUSES and check.out_week is not None
            cnt = f"counted out of week {check.out_week} only" if out_now else "counted as playing"
            line += f"; ESPN status {p.status} ({cnt})"
        out.append(line)
    return out


def method_line(check: TradeCheck) -> str:
    pool = check.pool
    if pool is None:
        return "range: not available (Regression Watch's backtest could not be read)"
    used = ", ".join(f"{p} {len(pool.by_position[p]):,} rows ({lo}-{hi} weeks left)"
                     for p, (lo, hi) in pool.horizons.items())  # fmt: skip
    return (f"range: the middle {100 * LEVEL:.0f}% of {N_SIM:,} simulated seasons; each player "
            "whose starts change gets one per-game miss (actual rest-of-season points per game "
            f"minus the projection) drawn from Regression Watch's backtest {pool.seasons} at his "
            f"position, about {check.horizon} weeks left: {used}. It covers how well players "
            "score per game, not games they miss.")  # fmt: skip


def playoff_line(check: TradeCheck, data: LeagueData, sched: Schedule) -> str:
    """How the fantasy playoffs are counted (or that an older sync cannot say)."""
    last = (f"the league's last scoring week is {data.final_week}" if data.final_week
            else "the league's last scoring week is not stored")  # fmt: skip
    nfl = f"the NFL's last regular-season week is {sched.last_reg_week}"
    if data.reg_final_week is None:
        return (f"fantasy playoffs are not modelled: the synced settings do not say which "
                "weeks are playoffs (a sync before the playoff settings were stored: run `uv run "
                "twm league sync`), so every week counted weighs the same "
                f"({last}; {nfl})")  # fmt: skip
    who = (f"{data.playoff_teams} of {data.team_count or '?'} teams make them"
           if data.playoff_teams else "the number of playoff teams is not stored")  # fmt: skip
    length = (f", {data.playoff_length} week(s) per playoff matchup"
              if data.playoff_length else "")  # fmt: skip
    kinds = dict(check.split)
    if "playoffs" not in kinds:
        counted = "no fantasy playoff week is counted"
    elif "regular" not in kinds:
        counted = "every week counted is a fantasy playoff week (only if you make the playoffs)"
    else:
        counted = (f"{span(kinds['playoffs'])} counted apart (only if you make the playoffs); "
                   "the verdict uses the regular season")  # fmt: skip
    return (f"fantasy playoffs: the league's regular season ends in week {data.reg_final_week} "
            f"({who}{length}; {last}; {nfl}): {counted}")  # fmt: skip


def notes(check: TradeCheck, data: LeagueData, sched: Schedule) -> list[str]:
    """The stated assumptions under every check."""
    out = [playoff_line(check, data, sched),
           "K and D/ST are not projected by Regression Watch: they are kept the same in both "
           "lineups, so they do not move the numbers",
           "IR-slot players are left out of every lineup and of the roster count"]  # fmt: skip
    if check.out_week is None:
        out.append(f"ESPN's injury statuses (the week {data.sync_week} sync) are not applied: "
                   "that week is not counted")  # fmt: skip
    else:
        out.append(f"ESPN's OUT / injured-reserve status counts a player out of week "
                   f"{check.out_week} only (the store has no return dates); questionable and "
                   "doubtful players count as playing")  # fmt: skip
    for s in check.sides:
        who = "you" if s.mine else s.name
        if s.dropped:
            names = ", ".join(f"{p.name} ({p.position}, {_ppg(check, p)})" for p in s.dropped)
            out.append(f"roster limit {data.roster_limit}: {who} must drop {names} (the "
                       "lowest-numbered QB/RB/WR/TE outside the best lineup)")  # fmt: skip
        if s.open_spots and (s.gives or s.gets):
            out.append(f"{who}: the trade opens {s.open_spots} roster spot(s); a pickup "
                       "is not counted")  # fmt: skip
    return out + [method_line(check)]


def _names(players: list[Player]) -> str:
    return ", ".join(f"{p.name} ({p.position})" for p in players) or "-"


def side_table(check: TradeCheck) -> list[str]:
    """The sides as a Rich table, rendered to plain text lines (no colour)."""
    import io

    from rich.console import Console
    from rich.table import Table

    t = Table(title=None, show_lines=False)
    for col, right in (("team", False), ("weeks", False), ("gives", False), ("gets", False),
                       ("drops", False), ("before", True), ("after", True), ("change", True),
                       (f"{100 * LEVEL:.0f}% range", True), ("gains", True)):  # fmt: skip
        t.add_column(col, justify="right" if right else "left",
                     no_wrap=col in ("team", "weeks") or right)  # fmt: skip
    tags = {"season": "", "regular": "", "playoffs": "*"}
    for s in check.sides:
        for i, x in enumerate(s.stretches):
            rng = "-" if x.share_up is None else f"{_pts(x.lo)} to {_pts(x.hi)}"
            share = "-" if x.share_up is None else f"{gains(x)}%"
            who = (f"{s.name} (you)" if s.mine else s.name) if i == 0 else ""
            names = (_names(s.gives), _names(s.gets), _names(s.dropped)) if i == 0 else ("",) * 3
            weeks = span(x.weeks).split()[-1] + tags[x.kind]  # "4-14", "15-17*"
            t.add_row(who, weeks, *names, f"{x.before.total:.0f}", f"{x.after.total:.0f}",
                      _pts(x.delta), rng, share)  # fmt: skip
    buf = io.StringIO()
    Console(file=buf, width=120, color_system=None, highlight=False, emoji=False).print(t)
    out = buf.getvalue().rstrip().splitlines()
    if any(k == "playoffs" for k, _ in check.split):
        out.append("* the fantasy playoff weeks: only if you make the playoffs")
    return out


def text(check: TradeCheck, data: LeagueData, sched: Schedule, scoring: str | None) -> list[str]:
    """`twm league trade`'s output: the table, the verdicts, the context, the assumptions."""
    over = " and, apart, ".join(f"{span(w)} ({STRETCHES[k]})" for k, w in check.split)
    out = [f"Trade check {check.season}: expected starting-lineup points (QB/RB/WR/TE) over "
           f"{over}, by Regression Watch's week {check.week} list (made after week "
           f"{check.week}'s games). 'before' and 'after': the projection's totals over those "
           f"weeks; 'change' (median), '{100 * LEVEL:.0f}% range' and 'gains' (the share with a "
           f"gain): {N_SIM:,} simulated seasons with Regression Watch's real misses, which "
           "correct the projection's average lean by position.", ""]  # fmt: skip
    out += side_table(check) + [""]
    out += [verdict(s, check) for s in check.sides]
    if len(check.sides) == 1:
        out.append("(only free agents come in: there is no partner side)")
    out += ["", "The players:"] + [f"  {x}" for x in player_lines(check)]
    out += ["", "Context:"] + [f"  {x}" for x in scarcity_lines(check, data)]
    out += [f"  {x}" for x in bye_lines(check, data, sched)]
    out += ["", "Assumptions:"] + [f"  {x}" for x in notes(check, data, sched)]
    if scoring:
        line = scoring.removeprefix("Note: ").replace("the chances below", "the projections")
        out.append(f"  {line}")
    return out


def _p(p: Player, check: TradeCheck) -> dict:
    v = check.values[p.espn_id]
    return {"name": p.name, "position": p.position, "nfl_team": p.pro_team, "ppg": v.ppg,
            "source": v.source, "tag": v.tag or None, "status": p.status or None}  # fmt: skip


def as_json(check: TradeCheck, data: LeagueData, sched: Schedule) -> str:
    """`--json`: the numbers of :func:`text` (local output: player and team names)."""
    sides = [{"team": s.name, "mine": s.mine, "gives": [_p(p, check) for p in s.gives],
              "gets": [_p(p, check) for p in s.gets],
              "drops": [_p(p, check) for p in s.dropped], "open_spots": s.open_spots,
              "before": s.before.total, "after": s.after.total, "delta": s.delta,
              "projection_delta": s.main.projection_delta,
              "lo": None if s.share_up is None else s.lo,
              "hi": None if s.share_up is None else s.hi, "share_up": s.share_up,
              "verdict": verdict(s, check),
              "stretches": [{"kind": x.kind, "weeks": list(x.weeks), "before": x.before.total,
                             "after": x.after.total, "delta": x.delta,
                             "projection_delta": x.projection_delta,
                             "lo": None if x.share_up is None else x.lo,
                             "hi": None if x.share_up is None else x.hi,
                             "share_up": x.share_up,
                             "word": None if x.share_up is None else word(x)}
                            for x in s.stretches]} for s in check.sides]  # fmt: skip
    body = {"season": check.season, "list_week": check.week, "weeks": list(check.weeks),
            "reg_season_final_week": data.reg_final_week,
            "split": [{"kind": k, "weeks": list(w)} for k, w in check.split],
            "level": LEVEL, "n_sim": N_SIM, "out_week": check.out_week, "sides": sides,
            "players": player_lines(check),
            "context": scarcity_lines(check, data) + bye_lines(check, data, sched),
            "notes": notes(check, data, sched)}  # fmt: skip
    return json.dumps(body, indent=2)


@dataclass
class TradeRun:
    check: TradeCheck
    data: LeagueData
    sched: Schedule


def run(
    con: duckdb.DuckDBPyConnection, predictions: Path, warehouse: Path | None, *,
    give: list[str], get: list[str], week: int | None = None, pins_path: Path | None = None,
) -> TradeRun:  # fmt: skip
    """Read the inputs (read-only) and check the trade: :class:`TradeError` when one is
    missing or a name does not resolve."""
    from twm.league.drops import season_average

    data = load_league(con)
    proj = projections(predictions, data.season, data.sync_week, week, pins_path)
    sched = schedule(warehouse, data.season, proj.week, data.final_week)
    if not sched.weeks:
        raise TradeError(f"My League: no week of {data.season} is left after week {proj.week}")
    missing = sorted({p.entity_id for p in data.players if p.position in SKILL and p.entity_id
                      and p.entity_id not in proj.rows})  # fmt: skip
    averages = season_average(warehouse, data.season, proj.week, missing)
    pool = residual_pool(proj.horizon or len(sched.weeks), pins_path)
    return TradeRun(evaluate(data, give, get, proj, sched, averages, pool), data, sched)
