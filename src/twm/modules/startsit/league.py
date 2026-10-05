"""The local league report's section "Start/sit odds for your closest calls" (LOCAL ONLY: it uses
FantasyPros ranks, so it lives only in the owner's local report, never published).

For each of the owner's starters at QB/RB/WR/TE/FLEX (ESPN's lineup slots, K and D/ST have no
weekly ranks) the best bench alternative eligible for that slot (both with a valid rank for the
week still to come) and P(starter outscores him); a call under 55% is flagged. No league synced:
no section (:func:`for_report` returns None).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import polars as pl

from twm.modules.startsit.live import CLOSE_CALL

SECTION_ID = "startsit"
TITLE = "Start/sit odds for your closest calls"
LICENSE_NOTE = "Uses FantasyPros ranks: local only, never published."


@dataclass(frozen=True)
class SlotCall:
    slot: str
    starter: str  # "Name (WR14)"
    bench: str | None  # the best eligible bench option, None when there is none with a rank
    p_starter: float | None  # P(starter outscores him)


@dataclass
class StartSitSection:
    season: int | None = None
    week: int | None = None
    scrape_date: date | None = None
    calls: list[SlotCall] = field(default_factory=list)
    reason: str = ""  # why there are no calls


def _roster(con: duckdb.DuckDBPyConnection) -> pl.DataFrame | None:
    from twm.league import store

    part = store.current(con, "league_rosters")
    mine = store.my_team(con, part.league_id, part.season) if part else None
    if part is None or mine is None:
        return None
    return con.execute(
        "SELECT player_name, position, lineup_slot, gsis_id FROM league_rosters WHERE "
        "league_id = ? AND season = ? AND week = ? AND league_team_id = ?",
        [part.league_id, part.season, part.week, mine],
    ).pl()  # fmt: skip


def for_report(con: duckdb.DuckDBPyConnection, warehouse: Path | None,
               now: datetime | None = None) -> StartSitSection | None:  # fmt: skip
    """The section's content (None: no league synced, so no section). Reads only."""
    from twm.modules.startsit import live
    from twm.modules.startsit.production import StartSitPinError, load

    roster = _roster(con)
    if roster is None or roster.is_empty():
        return None
    when = now or datetime.now(UTC)
    when = when if when.tzinfo else when.replace(tzinfo=UTC)
    if warehouse is None or not Path(warehouse).exists():
        return StartSitSection(reason="no warehouse: build it to get start/sit odds.")
    try:
        season, week = live.target_week(warehouse, when)
        table, _ = load(season)
        wr = live.week_ranks(warehouse, season, week, when)
    except live.RanksNotOutError as e:
        return StartSitSection(reason=str(e))
    except (StartSitPinError, LookupError, duckdb.Error, RuntimeError, ValueError, OSError) as e:
        return StartSitSection(reason=f"start/sit odds unavailable: {e}")
    calls = slot_calls(roster, wr.rows, table)
    reason = "" if calls else "none of your starters at QB/RB/WR/TE/FLEX has a rank this week."
    return StartSitSection(season, week, wr.scrape_date, calls, reason)


def slot_calls(roster: pl.DataFrame, ranks: pl.DataFrame, table) -> list[SlotCall]:  # noqa: ANN001
    """``roster`` (player_name, position, lineup_slot, gsis_id) against the week's ``ranks``
    (live.week_ranks rows): one call per ranked starter in a QB/RB/WR/TE/FLEX slot."""
    from twm.league.lineup import ELIGIBLE, SLOT_ORDER
    from twm.modules.startsit import live

    valid = ranks.filter(pl.col("valid") & pl.col("gsis_id").is_not_null())
    rank_of = {}
    for r in valid.sort("pos_rank").iter_rows(named=True):
        rank_of.setdefault((r["gsis_id"], r["pos"]), r)  # a player on two pages: per page
    people = []
    for p in roster.iter_rows(named=True):
        r = rank_of.get((p["gsis_id"], p["position"]))
        if r is not None:
            people.append((p, live.Ranked(p["player_name"], r["pos"], int(r["pos_rank"]),
                                          r["team"], r["gsis_id"], True)))  # fmt: skip
    order = {s: i for i, s in enumerate(SLOT_ORDER)}
    starters = sorted((x for x in people if x[0]["lineup_slot"] in ELIGIBLE
                       and x[1].pos in live.FLEX | {"QB"}),
                      key=lambda x: (order.get(x[0]["lineup_slot"], 99), x[1].rank))  # fmt: skip
    bench = [x for x in people if x[0]["lineup_slot"] == "BE"]
    calls = []
    for p, a in starters:
        slot = p["lineup_slot"]
        best = None
        for _, b in bench:
            if b.pos in ELIGIBLE[slot]:
                o = live.odds(table, a, b)
                if best is None or o.p_a < best.p_a:
                    best = o
        calls.append(SlotCall(slot, f"{a.name} ({a.pos}{a.rank})",
                              f"{best.b.name} ({best.b.pos}{best.b.rank})" if best else None,
                              best.p_a if best else None))  # fmt: skip
    return calls


def render_section(sec: StartSitSection) -> object:
    """The HTML section (twm.league.report's helpers), marked local only."""
    from twm.league.report import para, section, table

    parts: list[object] = [para(LICENSE_NOTE, "muted small")]
    if not sec.calls:
        parts.append(para(sec.reason))
        return section(SECTION_ID, TITLE, parts)
    parts.append(para(f"Week {sec.week} of {sec.season}, FantasyPros weekly ranks of "
                      f"{sec.scrape_date}: the chance each starter outscores your best bench "
                      "option for his slot (from how players at those ranks scored in "
                      "2020-2025). Under 55% is a close call."))  # fmt: skip
    rows = []
    for c in sec.calls:
        if c.p_starter is None:
            rows.append([c.slot, c.starter, "no ranked bench option", "-", ""])
            continue
        flag = ("bench option favoured" if c.p_starter < 0.5 else
                "close call" if c.p_starter < CLOSE_CALL else "")  # fmt: skip
        rows.append([c.slot, c.starter, c.bench, f"{c.p_starter:.0%}", flag])
    parts.append(table(["Slot", "Starter", "Best bench option", "Starter outscores him",
                        "Flag"], rows, numeric={3}))  # fmt: skip
    return section(SECTION_ID, TITLE, parts)
