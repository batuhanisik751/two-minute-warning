"""`twm league sync`: read the league from ESPN and store it in data/league.duckdb (step F2).

Every ESPN read happens first (:class:`twm.league.espn_client.EspnClient`); then one
transaction writes all tables, so a failed read leaves the store as it was. Output is counts
only: no team, owner or player name is printed (the unmatched players go to a gitignored
report under reports/league/).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from twm.league import store
from twm.league.espn_client import NOT_STARTING, EspnClient, EspnError, PlayerRow, RawSettings

FREE_AGENT_POSITIONS = ("QB", "RB", "WR", "TE", "K", "D/ST")
FREE_AGENTS_PER_POSITION = 50
ACTIVITY_SIZE = 50


@dataclass
class SyncResult:
    season: int
    week: int
    rows: dict[str, int] = field(default_factory=dict)  # table -> rows written
    unmatched: int = 0
    my_team_found: bool = False
    notes: list[str] = field(default_factory=list)


def _player_cols(p: PlayerRow) -> dict[str, Any]:
    return {"espn_id": p.espn_id, "player_name": p.name, "position": p.position,
            "pro_team": p.pro_team}  # fmt: skip


@dataclass
class _Read:
    """Everything one sync read from ESPN, before anything is written."""

    settings: Any
    waiver: dict[str, str]
    teams: list
    rosters: list
    free_agents: list[PlayerRow]
    boxes: list
    activity: list


def read_league(client: EspnClient, week: int | None, notes: list[str]) -> tuple[_Read, int]:
    s = client.settings()
    w = s.current_week if week is None else week
    if not 1 <= w <= max(s.current_week, 1):
        raise EspnError(f"--week {w} is outside weeks 1-{s.current_week} (ESPN's current week)")
    try:
        raw = client.raw_settings()
    except EspnError as e:  # extra detail only: the sync goes on without it
        raw = RawSettings({}, None)
        notes.append(f"waiver settings not read ({e})")
    if raw.slots is not None and raw.slots != s.roster_slots:
        notes.append("espn-api's roster slot labels disagreed with ESPN's slot ids: the ids won")
        s = replace(s, roster_slots=raw.slots)
    fas: dict[int, PlayerRow] = {}
    for pos in FREE_AGENT_POSITIONS:
        for p in client.free_agents(week=w, size=FREE_AGENTS_PER_POSITION, position=pos):
            fas.setdefault(p.espn_id, p)  # a RB/WR can come back under both positions
    got = _Read(
        settings=s,
        waiver=raw.waiver,
        teams=client.teams(),
        rosters=client.rosters(week),
        free_agents=list(fas.values()),
        boxes=client.box_scores(w),
        activity=client.activity(ACTIVITY_SIZE),
    )
    return got, w


def _rows(got: _Read) -> dict[str, list[dict]]:
    """Table -> rows (without the base columns and the join columns)."""
    s = got.settings
    settings = {"team_count": s.team_count, "faab": s.faab, "acquisition_budget":
                s.acquisition_budget, "current_week": s.current_week, "nfl_week": s.nfl_week,
                "final_week": s.final_week}  # fmt: skip
    settings |= {f"slot:{k}": v for k, v in s.roster_slots.items()}
    settings |= {f"waiver:{k}": v for k, v in got.waiver.items()}
    box_players, matchups = [], []
    for b in got.boxes:
        matchups.append(
            {
                "matchup": b.matchup,
                "league_team_id": b.team_id,
                "league_opponent_id": b.opponent_id,
                "is_home": b.is_home,
                "is_playoff": b.is_playoff,
                "score": b.score,
                "projected": b.projected,
            }
        )
        for p in b.lineup:
            box_players.append({**_player_cols(p), "league_team_id": b.team_id,
                                "lineup_slot": p.lineup_slot,
                                "is_starter": p.lineup_slot not in NOT_STARTING,
                                "points": p.points, "projected_points": p.projected_points,
                                "on_bye": p.on_bye})  # fmt: skip
    activity = {}
    for a in got.activity:
        activity[(a.date_ms, a.seq)] = {
            "activity_ms": a.date_ms, "activity_at": datetime.fromtimestamp(a.date_ms / 1000, UTC)
            .replace(tzinfo=None), "seq": a.seq, "league_team_id": a.team_id, "action": a.action,
            "espn_id": a.espn_id, "player_name": a.name or None, "position": a.position or None,
            "pro_team": None, "bid_amount": a.bid_amount,
        }  # fmt: skip
    return {
        "league_settings": [{"key": k, "value": str(v)} for k, v in settings.items()],
        "league_scoring": [asdict(i) for i in s.scoring],
        "league_teams": [{"league_team_id": t.team_id, "league_team_abbrev": t.abbrev,
                          "league_team_name": t.name, "league_is_mine": t.is_mine}
                         for t in got.teams],
        "league_rosters": [{**_player_cols(r.player), "league_team_id": r.team_id,
                            "lineup_slot": r.player.lineup_slot,
                            "injury_status": r.player.injury_status,
                            "acquisition_type": r.acquisition_type} for r in got.rosters],
        "league_free_agents": [{**_player_cols(p), "percent_owned": p.percent_owned,
                                "points": p.points, "projected_points": p.projected_points,
                                "on_bye": p.on_bye} for p in got.free_agents],
        "league_matchups": matchups,
        "league_box_scores": box_players,
        "league_activity": list(activity.values()),
    }  # fmt: skip


def _join(rows: dict[str, list[dict]], bridge: dict[str, str] | None) -> list[dict]:
    """Fill gsis_id / entity_id in every player table; returns the player map rows."""
    order = ("league_activity", "league_free_agents", "league_box_scores", "league_rosters")
    seen = [r for t in order for r in rows[t] if r.get("espn_id") is not None]
    pmap = store.player_map(seen, bridge)
    by_id = {m["espn_id"]: m for m in pmap}
    for t in order:
        for r in rows[t]:
            m = by_id.get(r.get("espn_id"))
            r["gsis_id"], r["entity_id"] = (m["gsis_id"], m["entity_id"]) if m else (None, None)
    return pmap


def sync(
    client: EspnClient,
    store_path: Path,
    *,
    league_id: int,
    week: int | None = None,
    warehouse: Path | None = None,
    report: Path | None = None,
    now: datetime | None = None,
) -> SyncResult:
    """Read the league (all ESPN calls first), join ESPN ids to gsis ids through the warehouse,
    then write every table in one transaction. ``report``: where the unmatched players go."""
    notes: list[str] = []
    got, w = read_league(client, week, notes)
    season = client.year
    synced = (now or datetime.now(UTC)).astimezone(UTC).replace(tzinfo=None)
    rows = _rows(got)
    bridge = store.espn_bridge(warehouse)
    if bridge is None:
        notes.append("the warehouse (bridge_player_id) is missing or busy: no player got a "
                     "gsis_id (run `uv run twm build`, then sync again)")  # fmt: skip
    rows["league_player_map"] = _join(rows, bridge)
    unmatched = [m for m in rows["league_player_map"] if m["method"] == "unmatched"]
    base = {"league_id": league_id, "season": season, "week": w, "synced_at": synced}
    res = SyncResult(season=season, week=w, unmatched=len(unmatched), notes=notes,
                     my_team_found=any(t.is_mine for t in got.teams))  # fmt: skip
    con = store.connect(store_path)
    try:
        con.begin()
        for name, table_rows in rows.items():
            t = store.TABLES[name]
            where = {k: base[k] for k in t.partition} if t.partition else {}
            res.rows[name] = store.write(con, name, store.frame(name, table_rows, **base), **where)
        runs = [{"what": n, "n_rows": k, "unmatched": res.unmatched, "note": "; ".join(notes)}
                for n, k in res.rows.items()]  # fmt: skip
        store.write(con, "league_sync_runs", store.frame("league_sync_runs", runs, **base))
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()
    if report is not None:
        write_unmatched_report(report, unmatched, season, w)
    return res


def write_unmatched_report(path: Path, unmatched: list[dict], season: int, week: int) -> None:
    """A gitignored markdown list of ESPN players without a gsis_id (fix: an override in
    data/manual/player_id_overrides.csv, id_type espn)."""
    lines = [f"# My League: ESPN players without a gsis_id ({season} week {week})", "",
             f"{len(unmatched)} player(s). Local only (reports/league/ is gitignored).", "",
             "| espn_id | name | position | ESPN team |", "|---|---|---|---|"]  # fmt: skip
    lines += [f"| {m['espn_id']} | {m['player_name'] or ''} | {m['position'] or ''} | "
              f"{m['pro_team'] or ''} |" for m in unmatched]  # fmt: skip
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
