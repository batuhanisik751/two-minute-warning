"""`twm league journal`: "You vs the model", a learning log of the owner's own adds, drops and
lineups against what the app said at the time (owner request 2026-10-04; local only, never
published). One season is a tiny sample: the journal counts, it never grades.

Reads only: data/league.duckdb (ESPN's activity, free agents and box scores), the predictions
store (the stored lists) and the warehouse (week windows, labels and weekly points).

- **Point in time.** A move at time ``t`` (ESPN's activity time, UTC) is compared with the newest
  list whose ``as_of <= t``; a list made later never is. One model version per list
  (:func:`twm.league.personal.one_version`: the approved one, else the newest stored). A list
  stored after ``t`` (a reconstructed list: ``created_at > t``) is flagged "made later": the
  numbers are the ones the app would have shown, not ones the owner saw.
- **The week of a move**: the regular-season week whose as-of window (after the previous week's
  Tuesday as-of, up to its own) holds ``t`` (``dim_week``), i.e. the week whose games the move
  was made for; without a warehouse, 1 + the newest Radar list week made before ``t``.
- **Adds** (FA ADDED / WAIVER ADDED by the owner's team): the Radar (QB/RB/WR/TE) or streamer
  (K, D/ST) rank, chance and priority, Regression Watch's Sell-high / Buy-low tag, or "not on
  the lists"; the outcome: the Radar's hit label (``y_hit``) when final, else pending (the
  store's final label first, else the labels as they stand now in the warehouse,
  :func:`twm.modules.waiver_radar.weekly.attach_names_and_outcomes`); and his points in the
  owner's starting lineup (ESPN box scores) in the finished weeks since the add.
- **Drops**: the dropped player's points since the drop vs the player added in the same move
  (else the owner's best add of that week): both from the warehouse's weekly points
  (config/scoring.yaml, the same finished weeks for both; QB/RB/WR/TE only).
- **Missed must-adds**: the Radar's must-adds of list week N who were free agents in the
  league's sync of ESPN week N+1 and whom the owner did not add before the next as-of.
- **Lineups**: :func:`twm.league.regret.load_season` (the started lineup vs the decision-time
  best one by ESPN's projections), never re-implemented here.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from twm.league import store
from twm.league.drops import read_warehouse
from twm.league.personal import (
    APP_POSITION,
    PIN_KEYS,
    PIN_MODULES,
    PROJECTION_PIN,
    chance_text,
    one_version,
    pinned_versions,
    stored_lists,
)
from twm.league.regret import RegretUnavailableError, SeasonRegret, load_season

ADDED = ("FA ADDED", "WAIVER ADDED")
DROPPED = "DROPPED"
SKILL = ("QB", "RB", "WR", "TE")
WINDOW = timedelta(days=7)  # a list stays the current one until the next Tuesday as-of
TAGS = {"sell_high": "Sell-high", "buy_low": "Buy-low"}  # as twm.league.report.TAG_NAMES
CAVEAT = ("One season is a tiny sample: these counts are a learning log, not a grade of you or "
          "of the model.")  # fmt: skip


class JournalUnavailableError(LookupError):
    """Nothing to review (no sync, the owner's team not identified): one plain line."""


@dataclass(frozen=True)
class Said:
    """What the app's lists said about one player at a moment (all None: not on the lists)."""

    list_name: str | None = None  # "Radar" / "K streamer" / "D/ST streamer"
    week: int | None = None  # the list's week N (made after week N's games)
    rank: int | None = None
    chance: str = "-"
    tier: str | None = None  # must-add / speculative / watch
    tag: str | None = None  # Regression Watch: Sell-high / Buy-low
    made_later: bool = False  # the list was stored after the move (a reconstructed list)
    no_list: bool = False  # no list of his position was stored before the move

    @property
    def listed(self) -> bool:
        return self.list_name is not None

    def text(self) -> str:
        if not self.listed:
            out = "no list stored before the move" if self.no_list else "not on the lists"
        else:
            out = (f"{self.list_name} week {self.week}: No. {self.rank}, {self.chance}"
                   + (f", {self.tier}" if self.tier else ""))  # fmt: skip
        if self.tag:
            out += f"; Regression Watch: {self.tag}"
        return out + (" (a list stored after the move)" if self.made_later else "")


@dataclass(frozen=True)
class Add:
    at: datetime  # ESPN's activity time (naive UTC)
    week: int | None  # the week whose games the move was made for
    how: str  # "free agent" / "waivers"
    name: str
    position: str
    said: Said
    outcome: str  # hit / miss / pending / "-" (no Radar label)
    points: float  # his points in your starting lineup since the add (finished weeks)
    started: int  # finished weeks he started for you
    rostered: int  # finished weeks he was on your roster
    since: float | None = None  # warehouse points since the add (drop comparisons)


@dataclass(frozen=True)
class Drop:
    at: datetime
    week: int | None
    name: str
    position: str
    said: Said
    points: float | None  # his points since the drop (warehouse; None: not comparable)
    other: str | None  # the add compared with
    other_points: float | None
    basis: str  # "same move" / "your best add that week" / "no add that week"
    weeks: int  # finished weeks counted


@dataclass(frozen=True)
class Missed:
    week: int  # the journal week (the list's week + 1)
    list_week: int
    name: str
    position: str
    rank: int
    chance: str
    outcome: str
    made_later: bool


@dataclass
class Journal:
    season: int
    week: int | None  # --week: one week only
    adds: list[Add] = field(default_factory=list)
    drops: list[Drop] = field(default_factory=list)
    missed: list[Missed] = field(default_factory=list)
    lineups: SeasonRegret | None = None
    lineup_error: str = ""
    notes: list[str] = field(default_factory=list)


# ---- the stored lists, point in time -------------------------------------------------------

LIST_NAMES = {"waiver_radar": "Radar", "streamer_k": "K streamer", "streamer_dst": "D/ST streamer"}


def load_lists(
    predictions: Path, season: int, pins_path: Path | None
) -> tuple[pl.DataFrame, list[str]]:
    """The season's stored lists, ONE model version per (module, week, position) as `twm league
    radar` picks it (Regression Watch: per week), with a ``key`` column (the pin key)."""
    from twm.league.personal import PersonalUnavailableError

    pins, notes = pinned_versions(pins_path), []
    versions = {k: v for k, (s, v) in pins.items() if s == season}
    for key in (*dict.fromkeys(PIN_KEYS.values()), PROJECTION_PIN):
        if key not in versions:
            notes.append(f"no approved {key} model for {season}: its lists are not used")
    try:
        rows = stored_lists(predictions, season, sorted(set(PIN_MODULES.values())))
    except PersonalUnavailableError as e:
        return pl.DataFrame(), [*notes, str(e).removeprefix("My League: ")]
    rw = pl.col("module") == "regression_watch"
    key = pl.col("position").replace_strict(PIN_KEYS, default=None, return_dtype=pl.String)
    rows = rows.with_columns(pl.when(rw).then(pl.lit(PROJECTION_PIN)).otherwise(key).alias("key"))
    parts = []
    lists = rows.filter(pl.col("key") != PROJECTION_PIN)
    for (k, _w, _p), grp in lists.group_by(["key", "week", "position"], maintain_order=True):
        parts.append(one_version(grp, versions.get(str(k)))[0])
    for _w, grp in rows.filter(rw).group_by(["week"], maintain_order=True):
        parts.append(one_version(grp, versions.get(PROJECTION_PIN))[0])
    return (pl.concat(parts) if parts else rows.clear()), notes


def _newest(rows: pl.DataFrame, t: datetime) -> pl.DataFrame:
    """The rows of the newest list week made at or before ``t`` (``as_of <= t``)."""
    before = rows.filter(pl.col("as_of") <= t)
    if before.height == 0:
        return before
    return before.filter(pl.col("week") == before.get_column("week").max())


def _tag(row: dict[str, Any]) -> str | None:
    """Regression Watch's Sell-high / Buy-low tag of a stored row (as the report reads it)."""
    reasons = json.loads(row.get("reasons_json") or "{}")
    tags = [t for t in reasons.get("tags", [row.get("band")]) if t in TAGS]
    return TAGS[tags[0]] if tags else None


def said_at(lists: pl.DataFrame, entity: str | None, position: str, t: datetime) -> Said:
    """What the newest lists public at ``t`` said about ``entity`` (``position``: ESPN's)."""
    if lists.height == 0:
        return Said(no_list=True)
    pos = APP_POSITION.get(position, position)
    key, out = PIN_KEYS.get(pos), {}
    if key is not None:
        week = _newest(lists.filter((pl.col("key") == key) & (pl.col("position") == pos)), t)
        if week.height:
            out["made_later"] = bool(week.get_column("created_at").max() > t)
        else:
            out["no_list"] = True
        mine = week.filter(pl.col("entity_id") == entity) if entity else week.clear()
        if mine.height:
            r = mine.row(0, named=True)
            out |= {"list_name": LIST_NAMES[key], "week": int(r["week"]), "rank": int(r["rank"]),
                    "chance": chance_text(pos, r["band"], r["score"]),
                    "tier": r["tier"]}  # fmt: skip
    rw = _newest(lists.filter(pl.col("key") == PROJECTION_PIN), t)
    mine = rw.filter(pl.col("entity_id") == entity) if entity else rw.clear()
    tag = _tag(mine.row(0, named=True)) if mine.height else None
    if tag:
        late = bool(rw.get_column("created_at").max() > t)
        out |= {"tag": tag, "made_later": out.get("made_later", False) or late}
    return Said(**out)


# ---- the week of a move --------------------------------------------------------------------


def week_windows(warehouse: Path | None, season: int) -> list[tuple]:
    """(week, window start, window end) of the regular season (``dim_week``, naive UTC)."""
    rows = read_warehouse(warehouse, "SELECT week, window_start_utc, window_end_utc FROM dim_week "
                          "WHERE season = ? AND season_type = 'REG' ORDER BY week",
                          [season])  # fmt: skip
    return list(rows or [])


def week_of(t: datetime, windows: list[tuple], lists: pl.DataFrame) -> int | None:
    """The week whose as-of window holds ``t``; without windows, 1 + the newest Radar list week
    made before ``t`` (None: no such list)."""
    for week, start, end in windows:
        if (start is None or t > start) and (end is None or t <= end):
            return int(week)
    if windows or lists.height == 0:
        return None
    before = lists.filter((pl.col("key") == "waiver_radar") & (pl.col("as_of") < t))
    return None if before.height == 0 else int(before.get_column("week").max()) + 1


# ---- outcomes and points -------------------------------------------------------------------

Labels = Callable[[pl.DataFrame], pl.DataFrame]


def warehouse_labels(warehouse: Path | None) -> Labels | None:
    """The Radar's labels as they stand now in the warehouse (the outcome filling of `twm radar
    week`), or None without a warehouse."""
    if warehouse is None or not warehouse.exists():
        return None
    from twm.modules.waiver_radar.weekly import attach_names_and_outcomes

    return lambda rows: attach_names_and_outcomes(rows, warehouse)


def radar_outcomes(
    predictions: Path, season: int, need: dict[tuple[str, int], datetime], labels: Labels | None
) -> tuple[dict[tuple[str, int], str], list[str]]:
    """hit / miss / pending of the Radar rows ``need`` ((gsis id, list week) -> the list's
    as-of): the store's final label first, else ``labels`` when final there, else pending."""
    from twm import predictions as pr

    out, notes = dict.fromkeys(need, "pending"), []
    if not need:
        return out, notes
    try:
        stored = pr.read_table(predictions, "outcomes",
                               f"module = 'waiver_radar' AND season = {int(season)}")  # fmt: skip
    except (duckdb.Error, OSError):
        stored = pl.DataFrame(schema={"entity_id": pl.String, "week": pl.Int32,
                                      "y_hit": pl.Boolean, "label_status": pl.String})  # fmt: skip
    for e, w, y, s in stored.select("entity_id", "week", "y_hit", "label_status").iter_rows():
        if (e, w) in need and s == "final" and y is not None:
            out[(e, w)] = "hit" if y else "miss"
    open_ = sorted(k for k, v in out.items() if v == "pending")
    for week in sorted({w for _, w in open_}) if labels is not None else []:
        ents = [e for e, w in open_ if w == week]
        rows = pl.DataFrame({"gsis_id": ents, "season": season, "week": week,
                             "as_of": need[(ents[0], week)], "y_hit": None, "label_status": None},
                            schema={"gsis_id": pl.String, "season": pl.Int32, "week": pl.Int32,
                                    "as_of": pl.Datetime("us"), "y_hit": pl.Boolean,
                                    "label_status": pl.String})  # fmt: skip
        try:
            got = labels(rows)
        except Exception as e:  # noqa: BLE001 - a busy warehouse: the labels stay pending
            notes.append(f"the Radar's week {week} labels could not be read ({type(e).__name__})")
            continue
        for r in got.iter_rows(named=True):
            if r["label_status"] == "final" and r["y_hit"] is not None:
                out[(r["gsis_id"], week)] = "hit" if r["y_hit"] else "miss"
    return out, notes


def weekly_points(warehouse: Path | None, season: int) -> pl.DataFrame | None:
    """Every QB/RB/WR/TE stat line of ``season`` (gsis_id, week, fantasy_points by
    config/scoring.yaml, is_week_final) from the warehouse, or None without one."""
    if warehouse is None or not warehouse.exists():
        return None
    from twm.modules.waiver_radar.labels import weekly_finishes

    try:
        df = weekly_finishes(warehouse, [season])
    except duckdb.Error:
        return None
    return df.filter(pl.col("position").is_in(list(SKILL))).select(
        "gsis_id", "week", "fantasy_points", "is_week_final")  # fmt: skip


def _since(pts: pl.DataFrame | None, entity: str | None, position: str,
           week: int | None) -> tuple[float | None, int]:  # fmt: skip
    """(his warehouse points in the finished weeks from ``week`` on, those weeks)."""
    if pts is None or entity is None or week is None or position not in SKILL:
        return None, 0
    done = pts.filter(pl.col("is_week_final") & (pl.col("week") >= week))
    mine = done.filter(pl.col("gsis_id") == entity).get_column("fantasy_points")
    return round(float(mine.sum()), 2), done.get_column("week").n_unique()


@dataclass(frozen=True)
class _Move:
    at: datetime
    action: str
    espn_id: int | None
    name: str
    position: str
    entity: str | None
    week: int | None


MOVES_SQL = """
    SELECT activity_at, action, espn_id, player_name, position, entity_id FROM league_activity
    WHERE league_id = ? AND season = ? AND league_team_id = ? AND action IN (?, ?, ?)
    ORDER BY activity_ms, seq
"""


def _moves(con: duckdb.DuckDBPyConnection, lid: int, season: int, team: int,
           windows: list[tuple], lists: pl.DataFrame) -> list[_Move]:  # fmt: skip
    rows = con.execute(MOVES_SQL, [lid, season, team, *ADDED, DROPPED]).fetchall()
    return [_Move(r[0], str(r[1]), None if r[2] is None else int(r[2]), str(r[3] or "?"),
                  str(r[4] or "?"), r[5], week_of(r[0], windows, lists)) for r in rows]  # fmt: skip


def _box(con: duckdb.DuckDBPyConnection, lid: int, season: int, team: int) -> list[tuple]:
    return con.execute(
        "SELECT week, espn_id, lineup_slot, points FROM league_box_scores WHERE league_id = ? "
        "AND season = ? AND league_team_id = ?", [lid, season, team]).fetchall()  # fmt: skip


def _for_you(m: _Move, box: list[tuple], final: set[int], moves: list[_Move]) -> tuple:
    """(points started for you, weeks started, weeks on your roster) in the finished weeks
    from the add's week up to the week you dropped him (if you did)."""
    from twm.league.espn_client import NOT_STARTING

    later = [d.week for d in moves if d.action == DROPPED and d.espn_id == m.espn_id
             and d.at > m.at and d.week is not None]  # fmt: skip
    until = min(later) if later else None
    lines = [(slot, p) for w, eid, slot, p in box if eid == m.espn_id and w in final
             and (m.week is None or w >= m.week) and (until is None or w <= until)]  # fmt: skip
    started = [float(p or 0.0) for slot, p in lines if slot not in NOT_STARTING]
    return round(sum(started), 2), len(started), len(lines)


def build(
    con: duckdb.DuckDBPyConnection,
    predictions: Path,
    warehouse: Path | None,
    *,
    season: int | None = None,
    week: int | None = None,
    pins_path: Path | None = None,
    labels: Labels | None = None,
    points: pl.DataFrame | None = None,
) -> Journal:
    """The journal of ``season`` (default: the latest synced), every week or ``week`` only.
    ``labels`` / ``points`` (tests) replace the warehouse's labels and weekly points."""
    part = store.latest(con, "league_settings", season)
    if part is None:
        raise JournalUnavailableError(
            "My League: nothing synced" + (f" for {season}" if season else "")
            + ": run `uv run twm league sync` first.")  # fmt: skip
    lid, season = part.league_id, part.season
    team = store.my_team(con, lid, season)
    if team is None:
        raise JournalUnavailableError(
            "My League: your team was not identified in the last sync (ESPN_SWID matched no "
            "team owner), so there are no moves to review")  # fmt: skip
    lists, notes = load_lists(predictions, season, pins_path)
    windows = week_windows(warehouse, season)
    if not windows:
        notes.append("no warehouse weeks: a move's week is 1 + the newest Radar list week made "
                     "before it")  # fmt: skip
    labels = labels if labels is not None else warehouse_labels(warehouse)
    pts = points if points is not None else weekly_points(warehouse, season)
    j = Journal(season, week, notes=notes)
    try:
        j.lineups = load_season(con, season, warehouse)
    except RegretUnavailableError as e:
        j.lineup_error = str(e).removeprefix("My League: ")
    final = {w.week for w in j.lineups.weeks} if j.lineups else set()
    moves = _moves(con, lid, season, team, windows, lists)
    box = _box(con, lid, season, team)
    radar = lists.filter(pl.col("key") == "waiver_radar") if lists.height else lists
    as_ofs = dict(radar.group_by("week").agg(pl.col("as_of").max()).iter_rows()) if radar.height \
        else {}  # fmt: skip
    need: dict[tuple[str, int], datetime] = {}
    said = {}
    for m in moves:
        said[m] = said_at(lists, m.entity, m.position, m.at)
        if said[m].list_name == "Radar" and m.action in ADDED:
            need[(str(m.entity), int(said[m].week))] = as_ofs[said[m].week]
    missed, more = _missed(con, lid, season, radar, moves)
    j.notes += more
    for x in missed:
        need[(x[0], x[1])] = as_ofs[x[1]]
    outcomes, more = radar_outcomes(predictions, season, need, labels)
    j.notes += more
    _fill(j, moves, said, outcomes, box, final, pts, missed)
    return j


def _missed(con: duckdb.DuckDBPyConnection, lid: int, season: int, radar: pl.DataFrame,
            moves: list[_Move]) -> tuple[list[tuple], list[str]]:  # fmt: skip
    """(gsis id, list week, name, position, rank, chance, reconstructed) of each Radar must-add
    free in the sync of ESPN week N+1 whom you did not add before the next as-of; notes."""
    out, notes = [], []
    weeks = sorted(set(radar.get_column("week").to_list())) if radar.height else []
    for lw in weeks:
        rows = radar.filter((pl.col("week") == lw) & (pl.col("tier") == "must-add"))
        if rows.height == 0:
            continue
        as_of = rows.get_column("as_of").max()
        fa = con.execute("SELECT entity_id, player_name, synced_at FROM league_free_agents WHERE "
                         "league_id = ? AND season = ? AND week = ?",
                         [lid, season, lw + 1]).fetchall()  # fmt: skip
        if not fa:
            notes.append(f"the week {lw} Radar list: no free-agent sync of ESPN week {lw + 1}, "
                         "so its must-adds are not checked")  # fmt: skip
            continue
        synced = max(r[2] for r in fa)
        if synced > as_of + WINDOW:
            notes.append(f"ESPN week {lw + 1}'s free agents were synced {synced:%Y-%m-%d %H:%M} "
                         "UTC, after that week (a backfill): ESPN lists the free agents of the "
                         "sync time, so a missed must-add may not have been free that "
                         "week")  # fmt: skip
        free = {r[0]: str(r[1] or "?") for r in fa if r[0] is not None}
        added = {m.entity for m in moves if m.action in ADDED and as_of <= m.at < as_of + WINDOW}
        late = bool((rows.get_column("kind") == "backtest").any())
        order = rows.with_columns(
            pl.col("position")
            .replace_strict({p: i for i, p in enumerate(SKILL)}, default=9, return_dtype=pl.Int32)
            .alias("_o")
        )
        for r in order.sort("_o", "rank").iter_rows(named=True):
            e = r["entity_id"]
            if e in free and e not in added:
                out.append((e, lw, free[e], r["position"], int(r["rank"]),
                            chance_text(r["position"], r["band"], r["score"]), late))  # fmt: skip
    return out, notes


def _fill(j: Journal, moves: list[_Move], said: dict[_Move, Said],
          outcomes: dict[tuple[str, int], str], box: list[tuple], final: set[int],
          pts: pl.DataFrame | None, missed: list[tuple]) -> None:  # fmt: skip
    adds: list[tuple[_Move, Add]] = []
    for m in (m for m in moves if m.action in ADDED):
        s = said[m]
        outcome = outcomes.get((str(m.entity), int(s.week)), "pending") if s.list_name == "Radar" \
            else "-"  # fmt: skip
        mine, started, rostered = _for_you(m, box, final, moves)
        how = "waivers" if m.action == "WAIVER ADDED" else "free agent"
        since = _since(pts, m.entity, m.position, m.week)[0]
        adds.append((m, Add(m.at, m.week, how, m.name, m.position, s, outcome, mine, started,
                            rostered, since)))  # fmt: skip
    for m in (m for m in moves if m.action == DROPPED):
        lost, weeks = _since(pts, m.entity, m.position, m.week)
        same, basis = [a for mm, a in adds if mm.at == m.at], "same move"
        if not same:
            same = sorted((a for mm, a in adds if m.week is not None and mm.week == m.week
                           and a.since is not None), key=lambda a: -(a.since or 0.0))  # fmt: skip
            basis = "your best add that week" if same else "no add that week"
        o = same[0] if same else None
        name, other = (o.name, o.since) if o else (None, None)
        j.drops.append(Drop(m.at, m.week, m.name, m.position, said[m], lost, name, other, basis,
                            weeks))  # fmt: skip
    j.adds = [a for _, a in adds]
    j.missed = [Missed(lw + 1, lw, name, pos, rank, chance, outcomes.get((e, lw), "pending"), late)
                for e, lw, name, pos, rank, chance, late in missed]  # fmt: skip
    if j.week is not None:
        j.adds = [a for a in j.adds if a.week == j.week]
        j.drops = [d for d in j.drops if d.week == j.week]
        j.missed = [x for x in j.missed if x.week == j.week]


# ---- text ----------------------------------------------------------------------------------

POINT_IN_TIME = ("Point in time: each move is compared with the newest list made at or before "
                 "it (the list's as-of <= ESPN's activity time, UTC); a list made later is never "
                 "used.")  # fmt: skip
OUTCOME = ("Outcome: the Radar's hit label (a starter finish in his team's next 3 games, "
           "docs/waiver_radar.md), pending until those games are final.")  # fmt: skip


def lineup_weeks(j: Journal) -> list:
    """The finished weeks of lineup regret (``j.week`` only, when given)."""
    if j.lineups is None:
        return []
    return [w for w in j.lineups.weeks if j.week is None or w.week == j.week]


def _count(xs: list[str]) -> str:
    return (f"{xs.count('hit')} hit, {xs.count('miss')} miss, {xs.count('pending')} "
            "pending")  # fmt: skip


def summary(j: Journal) -> list[str]:
    """You vs the model so far: counts only, never a verdict, and the small-sample caveat."""
    listed = sum(a.said.listed for a in j.adds)
    must = sum(a.said.tier == "must-add" for a in j.adds)
    none = sum(a.said.no_list for a in j.adds)
    radar = [a.outcome for a in j.adds if a.said.list_name == "Radar"]
    out = [f"Adds: {len(j.adds)} ({listed} on the lists at the time, {must} of them must-adds; "
           f"{none} made before any list was stored); the Radar's outcome of those on its "
           f"list: {_count(radar)}."]  # fmt: skip
    both = [d for d in j.drops if d.weeks and d.points is not None and d.other_points is not None]
    more = sum(d.points > d.other_points for d in both)  # type: ignore[operator]
    out.append(f"Drops: {len(j.drops)}; {len(both)} compared over finished weeks, the dropped "
               f"player outscored the add in {more}.")  # fmt: skip
    out.append(f"Missed must-adds: {len(j.missed)} ({_count([x.outcome for x in j.missed])}).")
    weeks = lineup_weeks(j)
    if weeks:
        same = sum(w.decision_lineup.keys == w.started for w in weeks)
        lost = round(sum(w.lost_decisions for w in weeks), 2)
        out.append(f"Lineups: {len(weeks)} finished week(s); you started ESPN's decision-time "
                   f"best lineup in {same}; {lost:+.2f} points lost to decisions in all "
                   "(negative: you beat the projections).")  # fmt: skip
    else:
        why = f" ({j.lineup_error})" if j.lineup_error else ""
        out.append(f"Lineups: no finished week to review{why}.")
    return [*out, CAVEAT]


def time_label(t: datetime) -> str:
    return f"{t:%a %Y-%m-%d %H:%M} UTC"


def week_label(w: int | None) -> str:
    return "week ?" if w is None else f"week {w}"


def points_label(x: float | None) -> str:
    return "-" if x is None else f"{x:.2f}"


def add_line(a: Add) -> str:
    started = f"{a.started} start" + ("" if a.started == 1 else "s")
    return (f"{week_label(a.week)}, {time_label(a.at)}, {a.how}: {a.name} ({a.position}): "
            f"{a.said.text()}; outcome: {a.outcome}; for you: {a.points:.2f} points in {started} "
            f"({a.rostered} finished week(s) on your roster)")  # fmt: skip


def drop_line(d: Drop) -> str:
    head = f"{week_label(d.week)}, {time_label(d.at)}: {d.name} ({d.position}): {d.said.text()}; "
    if d.points is None:
        return head + "points since: not compared (QB/RB/WR/TE with a warehouse only)"
    if not d.weeks:
        return head + "no finished week since the drop yet"
    vs = (f"{d.other} ({d.basis}) {points_label(d.other_points)}" if d.other is not None
          else d.basis)  # fmt: skip
    return head + f"{points_label(d.points)} points since ({d.weeks} finished week(s)) vs {vs}"


def missed_line(x: Missed) -> str:
    late = " (a reconstructed list)" if x.made_later else ""
    return (f"{week_label(x.week)} (the Radar's week {x.list_week} list{late}): {x.name} "
            f"({x.position}) No. {x.rank}, {x.chance}; outcome: {x.outcome}")  # fmt: skip


def lineup_line(w: Any) -> str:  # a twm.league.regret.WeekRegret
    return (f"week {w.week}: started {w.actual:.2f}, decision-time best {w.decision:.2f} "
            f"({w.lost_decisions:+.2f} lost to decisions)")  # fmt: skip


def text(j: Journal) -> list[str]:
    """The journal as plain lines (`twm league journal`): NFL player names, never a fantasy
    team, owner or member id."""
    which = f", week {j.week}" if j.week is not None else ""
    out = [f"You vs the model, {j.season}{which}: your adds, drops and lineups against what the "
           "app said at the time.", POINT_IN_TIME, "", "So far:"]  # fmt: skip
    out += [f"  {s}" for s in summary(j)]
    blocks = (("Adds", [add_line(a) for a in j.adds]), ("Drops", [drop_line(d) for d in j.drops]),
              ("Missed must-adds (Radar must-adds free in your league, not added)",
               [missed_line(x) for x in j.missed]),
              ("Lineups (vs the lineup ESPN's projections would have started; `twm league "
               "regret`)", [lineup_line(w) for w in lineup_weeks(j)]))  # fmt: skip
    for title, rows in blocks:
        out += ["", f"{title}: {len(rows)}", *(f"  {r}" for r in rows)]
    out += ["", OUTCOME, "Drops compare points by config/scoring.yaml from nflverse stats (your "
            "league may score a little differently: `twm league settings-diff`)."]  # fmt: skip
    return out + [f"Note: {n}" for n in j.notes]
