"""`twm league odds` and the weekly report's "Luck and playoff odds" section (feature #9;
local only): everything :mod:`twm.league.luck` and :mod:`twm.league.odds` compute, gathered
from data/league.duckdb (+ the warehouse's NFL kickoffs, + the predictions store for score
model (b)'s check), as text and as an HTML section. Reads only; writes nothing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from twm.league import odds as od
from twm.league.luck import LeagueSeason, TeamLuck, load, luck_table, swap_records

SECTION_ID = "odds"
TITLE = "Luck and playoff odds"
SENSITIVITY_K = (3.0, 6.0, 12.0)  # pseudo-weeks shown next to the default


def load_live(
    con: duckdb.DuckDBPyConnection, ls: LeagueSeason, warehouse: Path | None
) -> od.LiveWeek | None:
    """The first regular-season week not yet final, when its synced box scores show games
    under way: starters' points so far, ESPN's projections and the NFL kickoffs."""
    from twm.league.drops import read_warehouse
    from twm.league.store import nfl_team

    final = set(ls.final_weeks())
    open_ = [w for w in ls.weeks() if w not in final]
    if not open_:
        return None
    w = open_[0]
    rows = con.execute(
        "SELECT league_team_id, pro_team, points, projected_points, on_bye, synced_at FROM "
        "league_box_scores WHERE league_id = ? AND season = ? AND week = ? AND is_starter",
        [ls.league_id, ls.season, w],
    ).fetchall()  # fmt: skip
    if not rows:
        return None
    synced = max(r[5] for r in rows)
    got = read_warehouse(warehouse, "SELECT home_team, away_team, kickoff_utc, "
                         "game_end_utc_est FROM fact_game WHERE season = ? AND week = ? AND "
                         "season_type = 'REG'", [ls.season, w])  # fmt: skip
    games = None
    if got:
        games = {str(t): (k, e) for h, a, k, e in got for t in (h, a)}
    starters = [(int(t), nfl_team(p), pts, proj, bool(bye)) for t, p, pts, proj, bye, _ in rows]
    same = synced == ls.synced_at  # ESPN's live totals belong to the same sync
    totals = {s.team: (s.live if same else None) for s in ls.week_sides(w)}
    return od.live_from_rows(w, starters, games, synced, totals)


@dataclass
class OddsView:
    ls: LeagueSeason
    table: dict[int, TeamLuck]
    swap: dict[int, str]  # the owner's record under each other team's schedule
    odds: od.Odds
    k_est: od.KEstimate | None
    sensitivity: dict[float, float]  # pseudo-weeks -> the owner's P(playoffs)
    check: od.ModelCheck
    notes: list[str] = field(default_factory=list)


def build(
    con: duckdb.DuckDBPyConnection, warehouse: Path | None, predictions: Path | None,
    *, week: int | None = None, sims: int = od.SIMS, seed: int = od.SEED,
    pins_path: Path | None = None,
) -> OddsView:  # fmt: skip
    """The luck table and the odds after the final weeks (to ``week``: the odds as they stood
    after week N, later results ignored; default: now, with the week in progress)."""
    ls = load(con)
    final = ls.final_weeks(week)
    model = od.fit_shrunk(ls, final)
    if model is None:
        raise od.OddsUnavailableError("My League: no regular-season week is final yet: the "
                                      "odds start after week 1")  # fmt: skip
    live = load_live(con, ls, warehouse) if week is None else None
    res = od.simulate(ls, model, through=week, live=live, sims=sims, seed=seed)
    sens: dict[float, float] = {}
    if ls.my_team is not None:
        for k in SENSITIVITY_K:
            m = od.fit_shrunk(ls, final, k)
            if m is not None:
                o = od.simulate(ls, m, through=week, live=live, sims=sims, seed=seed)
                sens[k] = o.teams[ls.my_team].playoffs
    notes: list[str] = []
    check = od.check_models(ls, None, week)
    if predictions is not None:
        from twm.league.odds_roster import predict_b

        try:
            check = od.check_models(ls, predict_b(con, ls, predictions, warehouse, pins_path),
                                    week)  # fmt: skip
        except (OSError, LookupError, ValueError, duckdb.Error) as e:
            notes.append(f"score model (b) not checked ({type(e).__name__}: {e})")
    if check.pick != "a":
        notes.append("the pre-set rule now picks score model (b): the odds still use (a) "
                     "until the choice is reviewed")  # fmt: skip
    if live is not None and live.note:
        notes.append(live.note)
    return OddsView(ls, luck_table(ls, week), swap_records(ls, ls.my_team, week)
                    if ls.my_team is not None else {}, res, od.estimate_k(ls, final), sens,
                    check, notes)  # fmt: skip


HEAD = ["", "Team", "Record", "PF", "PA", "All-play", "Exp. W", "Luck", "P(playoffs)",
        "P(seed 1)", "P(title)"]  # fmt: skip


def _pct(x: float | None) -> str:
    if x is None:
        return "n/a"
    return "<0.1%" if 0 < x < 0.001 else (">99.9%" if 0.999 < x < 1 else f"{x:.1%}")


def rows(v: OddsView) -> list[list[str]]:
    """One row per team, by P(playoffs), then win points; the owner's marked '*'."""
    o = v.odds.teams
    order = sorted(v.table, key=lambda t: (-o[t].playoffs, -v.table[t].win_points,
                                           -v.table[t].points_for, t))  # fmt: skip
    out = []
    for t in order:
        r = v.table[t]
        out.append(["*" if t == v.ls.my_team else "", v.ls.teams.get(t, str(t)), r.record,
                    f"{r.points_for:.1f}", f"{r.points_against:.1f}", r.all_play,
                    f"{r.expected_wins:.2f}", f"{r.luck:+.2f}", _pct(o[t].playoffs),
                    _pct(o[t].seed1), _pct(o[t].title)])  # fmt: skip
    return out


def _span(weeks: list[int] | tuple[int, ...]) -> str:
    if not weeks:
        return "none"
    return f"{weeks[0]}-{weeks[-1]}" if len(weeks) > 1 else str(weeks[0])


def intro_lines(v: OddsView) -> list[str]:
    o, ls = v.odds, v.ls
    final = ls.final_weeks(o.through)
    out = [f"{ls.season}: final weeks {_span(final)} counted (luck and records use only weeks "
           "whose games are all final)."]  # fmt: skip
    if o.live is not None:
        out.append(f"Week {o.live.week} is in progress: the points already scored are kept and "
                   "only the starters whose NFL games had not finished at the sync are "
                   "simulated (ESPN's projection x the share of the game left).")  # fmt: skip
    po = ls.playoff_teams
    out.append(f"{o.sims:,} simulated seasons (seed {o.seed}): regular-season weeks "
               f"{_span(o.remaining)} on the real schedule, then the playoffs ({po} teams, "
               "one round per playoff week, bracket "
               + ", ".join(f"{a}v{b}" for a, b in _pairs(po)) + ", no reseeding; the higher "
               "seed advances on a tie). " + "; ".join(o.notes) + ".")  # fmt: skip
    return out


def _pairs(n: int) -> list[tuple[int, int]]:
    b = od.bracket_order(n)
    return list(zip(b[0::2], b[1::2], strict=True))


def owner_lines(v: OddsView) -> list[str]:
    ls, lev = v.ls, v.odds.leverage
    if ls.my_team is None:
        return ["Your team was not identified in the sync (ESPN_SWID matched no owner)."]
    out = [f"Week {d.week} looks settled: you win it in {_pct(d.p_win)} of the simulations."
           for d in v.odds.decided]  # fmt: skip
    if lev is not None:
        out.append(f"Your leverage, week {lev.week}: win -> {_pct(lev.playoffs_if_win)} to make "
                   f"the playoffs, lose -> {_pct(lev.playoffs_if_loss)} (title "
                   f"{_pct(lev.title_if_win)} vs {_pct(lev.title_if_loss)}); you win week "
                   f"{lev.week} in {_pct(lev.p_win)} of the simulations.")  # fmt: skip
    if v.swap:
        recs = ", ".join(f"{ls.teams.get(t, t)} {r}" for t, r in sorted(v.swap.items()))
        out.append(f"Your record under each other team's schedule: {recs}.")
    return out


def _score(s: od.ModelScore) -> str:
    if not s.n:
        return f"({s.name}) 0 team-weeks"
    return (f"({s.name}) {s.n} team-weeks, NLL {s.nll:.3f}, MAE {s.mae:.1f}, RMSE "
            f"{s.rmse:.1f}")  # fmt: skip


def model_lines(v: OddsView) -> list[str]:
    m, c, k = v.odds.model, v.check, v.k_est
    out = [
        f"Team score model: (a) each team's season mean shrunk toward the league mean "
        f"({m.league_mean:.1f}) by {od.PSEUDO_WEEKS:g} pseudo-weeks, weekly sd {m.sigma:.1f} "
        "(pooled within teams); a simulated season draws each team's true level once."
    ]
    if not c.b.n:
        why = ("(b) predicted no final week: it needs a Regression Watch list made before the "
               "week, and a season's first list is week 3's")  # fmt: skip
    elif c.diff is None:
        why = "no team-week was predicted by both"
    else:
        se = "n/a" if c.se is None else f"{c.se:.3f}"
        why = f"paired on {c.paired}, mean NLL difference (b)-(a) {c.diff:+.3f} (se {se})"
    out.append(f"Model check (each final week predicted from the weeks before it): {_score(c.a)}; "
               f"{_score(c.b)}; {why}. Pre-set rule (b only with >= {od.MIN_PAIRED} paired "
               f"team-weeks and a lower NLL by > 1 se): ({c.pick}).")  # fmt: skip
    est = (f"estimated from these weeks {k.k_hat:.1f} (sigma^2 / tau^2 = {k.sigma:.1f}^2 / "
           f"{k.tau2:.1f}, method of moments, {k.weeks} week(s): too few to trust, not used)"
           if k is not None else "not estimable yet")  # fmt: skip
    out.append(f"Pseudo-weeks: assumed {od.PSEUDO_WEEKS:g}; {est}.")
    if v.sensitivity:
        sens = ", ".join(f"K={k_:g}: {_pct(p)}" for k_, p in v.sensitivity.items())
        out.append(f"How much the prior matters: your P(playoffs) with {sens} (same seed).")
    hist = v.ls.previous_seasons
    if hist:
        out.append(f"League history: ESPN lists earlier seasons ({', '.join(map(str, hist))}) "
                   "but they are not synced: the odds are not checked against them.")  # fmt: skip
    else:
        out.append("League history: ESPN lists no earlier season of this league, so these odds "
                   "cannot be checked against past seasons here.")  # fmt: skip
    return out + [f"Note: {n}" for n in v.notes]


def text(v: OddsView) -> list[str]:
    """The CLI output: the intro, the table (the owner's row marked '*'), the owner's lines
    and the model lines."""
    grid = [HEAD, *rows(v)]
    widths = [max(len(r[i]) for r in grid) for i in range(len(HEAD))]
    right = set(range(3, len(HEAD)))
    table = ["  ".join(c.rjust(widths[i]) if i in right else c.ljust(widths[i])
                       for i, c in enumerate(r)).rstrip() for r in grid]  # fmt: skip
    return [f"My League: {TITLE.lower()}", *intro_lines(v), "", *table, "",
            *owner_lines(v), *model_lines(v)]  # fmt: skip


def render_section(v: OddsView) -> object:
    """The weekly report's HTML section (twm.league.report's helpers)."""
    from twm.league.report import para, section, table

    body = rows(v)
    marked = {i for i, r in enumerate(body) if r[0] == "*"}
    parts: list[object] = [para(x, "small") for x in intro_lines(v)]
    parts.append(table(HEAD[1:], [r[1:] for r in body], set(range(2, len(HEAD) - 1)), marked))
    parts += [para(x) for x in owner_lines(v)]
    parts += [para(x, "muted small") for x in model_lines(v)]
    return section(SECTION_ID, TITLE, parts)


def for_report(
    con: duckdb.DuckDBPyConnection, warehouse: Path | None, predictions: Path | None,
    pins_path: Path | None = None,
) -> OddsView | None:  # fmt: skip
    """The report's data, or None (no section): no schedule synced, no final week, a setting
    the odds do not support. Never breaks the report."""
    from twm.league.luck import LuckUnavailableError

    try:
        return build(con, warehouse, predictions, pins_path=pins_path)
    except (LuckUnavailableError, od.OddsUnavailableError, duckdb.Error):
        return None
