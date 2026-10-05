"""Score model (b) for the playoff odds' model check (feature #9; local only): a team's week
from its roster, as the trade checker values it (:mod:`twm.league.trade`).

For week w: the roster of week w (the newest synced roster at or before w); the best legal
QB/RB/WR/TE lineup (league slots and FLEX) by Regression Watch's rest-of-season points per game
from the newest stored list made BEFORE week w (list week N < w, the approved model's), a
player without a row at his season average to week N; a bye or an OUT / injured-reserve status
counts 0 and IR-slot players are left out (:func:`twm.league.trade.season_lineup`); plus K and
D/ST at the league's average starter points per slot over the final weeks before w. The sd is
model (a)'s pooled within-team sd of the same weeks (no (b) residuals exist yet).

None for a week without a list made before it: Regression Watch lists need 3 games played, so
the first list of a season is week 3's and (b) can first predict week 4.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl

from twm.league.luck import LeagueSeason
from twm.league.odds import Predictor, fit_shrunk


def _players(con: duckdb.DuckDBPyConnection, ls: LeagueSeason, week: int) -> list:
    from twm.league.trade import Player, _status

    row = con.execute(
        "SELECT max(week) FROM league_rosters WHERE league_id = ? AND season = ? AND week <= ?",
        [ls.league_id, ls.season, week],
    ).fetchone()
    if row is None or row[0] is None:
        return []
    rows = con.execute(
        "SELECT espn_id, player_name, position, pro_team, league_team_id, lineup_slot, "
        "injury_status, entity_id FROM league_rosters WHERE league_id = ? AND season = ? AND "
        "week = ?", [ls.league_id, ls.season, int(row[0])]
    ).fetchall()  # fmt: skip
    from twm.league.store import nfl_team

    return [Player(int(e), str(n or ""), "DST" if p == "D/ST" else str(p), nfl_team(pt),
                   int(t), str(s or ""), _status(st), ent)
            for e, n, p, pt, t, s, st, ent in rows]  # fmt: skip


def _fixed(con: duckdb.DuckDBPyConnection, ls: LeagueSeason, weeks: list[int],
           slots: dict[str, int]) -> float:  # fmt: skip
    """K and D/ST: the league's average starter points per slot over ``weeks`` x the slots."""
    if not weeks:
        return 0.0
    marks = ", ".join("?" * len(weeks))
    avg = dict(con.execute(
        "SELECT lineup_slot, avg(points) FROM league_box_scores WHERE league_id = ? AND "
        f"season = ? AND week IN ({marks}) AND lineup_slot IN ('K', 'D/ST') AND points IS NOT "
        "NULL GROUP BY lineup_slot", [ls.league_id, ls.season, *weeks]).fetchall())  # fmt: skip
    return sum(float(avg.get(s) or 0.0) * slots.get(s, 0) for s in ("K", "D/ST"))


def predict_b(
    con: duckdb.DuckDBPyConnection, ls: LeagueSeason, predictions: Path,
    warehouse: Path | None, pins_path: Path | None = None,
) -> Predictor:  # fmt: skip
    """Model (b) as a predictor (week -> team -> (mean, sd)), None for a week it cannot
    predict honestly (no list made before it, no roster, no schedule)."""
    from twm.league import store
    from twm.league.drops import season_average
    from twm.league.personal import (
        PersonalUnavailableError,
        one_version,
        pinned_versions,
        stored_lists,
    )
    from twm.league.trade import Projections, TradeError, schedule, season_lineup, skill_slots
    from twm.league.trade import value_of as trade_value

    pin = pinned_versions(pins_path).get("regression_watch")
    try:
        lists = stored_lists(predictions, ls.season, ["regression_watch"])
    except PersonalUnavailableError:
        lists = pl.DataFrame()
    slots = store.slot_counts(store.settings_of(con, ls.league_id, ls.season))

    def f(w: int) -> dict[int, tuple[float, float]] | None:
        before = sorted(x for x in set(lists.get_column("week").to_list()) if x < w) \
            if lists.height else []  # fmt: skip
        players = _players(con, ls, w)
        prior = [x for x in ls.final_weeks() if x < w]
        a = fit_shrunk(ls, prior)
        if not before or not players or pin is None or pin[0] != ls.season or a is None:
            return None
        n = before[-1]
        rows, _ = one_version(lists.filter(pl.col("week") == n), pin[1])
        proj = Projections(n, None, pin[1], {r["entity_id"]: r for r in rows.iter_rows(named=True)})
        try:
            sched = schedule(warehouse, ls.season, w - 1, None)
        except TradeError:
            return None
        missing = [p.entity_id for p in players if p.entity_id and p.entity_id not in proj.rows]
        avgs = season_average(warehouse, ls.season, n, missing)
        values = {p.espn_id: trade_value(p, proj, avgs) for p in players}
        fixed = _fixed(con, ls, prior, slots)
        out = {}
        for t in ls.team_ids:
            mine = [p for p in players if p.team_id == t]
            line = season_lineup(mine, values, sched, skill_slots(slots), w, weeks=(w,))
            out[t] = (line.total + fixed, a.sigma)
        return out

    return f
