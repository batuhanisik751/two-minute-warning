"""`twm league report`: the weekly local league report (PROJECT_SPEC 8.3 feature 4; step F4).

One self-contained HTML file, ``reports/league/<season>-W<nn>.html`` (git-ignored; ``nn`` = the
lists' week N, made after week N's games, like the other weekly reports): inline CSS, no script,
no external request, light and dark through ``prefers-color-scheme``. Sections, in plain words
for a first-season player:

1. this week's Waiver Radar on the league's free agents (QB, RB, WR, TE);
2. the K and D/ST streamer on the league's free agents;
3. the drop candidates (:mod:`twm.league.drops`) with their numbers;
4. Regression Watch's Sell-high / Buy-low tags on the owner's own players;
5. lineup regret (season totals and the per-week table, :mod:`twm.league.regret`);
6. the scoring check against ESPN's box scores (:mod:`twm.league.scoring_check`);
7. the settings difference (:mod:`twm.league.settings_diff`) with the suggested patch (nothing
   is applied).

Every number comes from data/league.duckdb, the predictions store (the approved models' stored
lists) and the warehouse. The file names the sync time and the lists' as-of; the only line that
changes between two runs on the same data is "Generated ..." (:data:`GENERATED_PREFIX`). It
carries the owner's fantasy team name (a local file) but never a cookie or a member id.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import duckdb

from twm.league import store
from twm.league.journal import Journal, JournalUnavailableError
from twm.league.journal import build as build_journal
from twm.league.personal import PersonalRadar, PositionList, build, scoring_note
from twm.league.regret import RegretUnavailableError, SeasonRegret, load_season
from twm.league.scoring_check import ScoringCheck, ScoringCheckUnavailableError, check
from twm.league.settings_diff import Diff, lineup_diffs, load, scoring_diffs, yaml_patch
from twm.league.weeks import WeekCheck, week_check

GENERATED_PREFIX = "Generated "
TAG_NAMES = {"sell_high": "Sell-high", "buy_low": "Buy-low"}  # Legit was removed (2026-09-30)


@dataclass(frozen=True)
class TagRow:
    name: str
    position: str
    tag: str  # sell_high / buy_low
    projection: float
    reasons: dict


@dataclass
class SettingsSummary:
    diffs: list[Diff]
    notes: list[str]
    patch: list[str]


@dataclass
class ReportData:
    radar: PersonalRadar
    synced_at: datetime  # the free-agent sync the lists are restricted to (naive UTC)
    team_name: str | None
    tags: list[TagRow]
    regret: SeasonRegret | None
    regret_error: str
    slots: dict[str, int]
    scoring: ScoringCheck | None
    scoring_error: str
    settings: SettingsSummary | None
    weeks: WeekCheck | None
    scoring_note: str | None
    notes: list[str] = field(default_factory=list)
    journal: Journal | None = None  # "You vs the model" (twm.league.journal)
    journal_error: str = ""
    # feature #1: twm.modules.questionable.league.Section; None = no section (no roster)
    questionable: object | None = None
    # feature #2: twm.modules.startsit.league.StartSitSection (FantasyPros ranks: local only);
    # None = no section. Set by commands.run_report after gather.
    startsit: object | None = None
    # feature #5: twm.modules.teammate_out.league.Section; None = no section
    teammate_out: object | None = None


def owner_tags(radar: PersonalRadar) -> list[TagRow]:
    """Regression Watch's Sell-high / Buy-low rows of the owner's own players (the lists'
    week), in the tag order, then the projection's distance from his scoring."""
    names = {r["entity_id"]: r["player_name"] for r in radar.roster.iter_rows(named=True)
             if r["entity_id"] is not None} if radar.roster.height else {}  # fmt: skip
    out = []
    for r in radar.projections.iter_rows(named=True):
        if r["entity_id"] not in names:
            continue
        reasons = json.loads(r.get("reasons_json") or "{}")
        tags = [t for t in reasons.get("tags", [r.get("band")]) if t in TAG_NAMES]
        for t in tags[:1]:
            out.append(TagRow(str(names[r["entity_id"]]), str(r["position"]), t,
                              float(r["score"]), reasons))  # fmt: skip
    order = list(TAG_NAMES)
    return sorted(out, key=lambda x: (order.index(x.tag), -abs(x.reasons.get("gap") or 0),
                                      x.name))  # fmt: skip


def settings_summary(con: duckdb.DuckDBPyConnection) -> SettingsSummary | None:
    from twm.config import league, scoring

    synced = load(con)
    if synced is None:
        return None
    d1, n1 = scoring_diffs(synced.espn, scoring())
    d2, n2 = lineup_diffs(synced.slots, synced.teams, league())
    return SettingsSummary(d1 + d2, n1 + n2, yaml_patch(d1 + d2))


def team_name(con: duckdb.DuckDBPyConnection, league_id: int, season: int) -> str | None:
    row = con.execute(
        "SELECT league_team_name FROM league_teams WHERE league_id = ? AND season = ? AND "
        "league_is_mine ORDER BY synced_at DESC LIMIT 1", [league_id, season]
    ).fetchone()  # fmt: skip
    return None if row is None or row[0] is None else str(row[0])


def gather(
    con: duckdb.DuckDBPyConnection,
    predictions: Path,
    warehouse: Path | None,
    *,
    week: int | None = None,
    pins_path: Path | None = None,
    limit: int = 10,
) -> ReportData:
    """Everything the report shows. Raises PersonalUnavailableError (no sync, no approved
    model, no stored list); the other sections say in one line why they are empty."""
    from twm.league.lineup import starting_slots

    radar = build(con, predictions, week=week, limit=limit, pins_path=pins_path,
                  warehouse=warehouse)  # fmt: skip
    fa = store.current(con, "league_free_agents", radar.season)
    assert fa is not None  # build() raised otherwise
    regret, regret_error = None, ""
    try:
        regret = load_season(con, radar.season, warehouse)
    except RegretUnavailableError as e:
        regret_error = str(e).removeprefix("My League: ")
    scoring, scoring_error = None, ""
    try:
        scoring = check(con, warehouse)
    except ScoringCheckUnavailableError as e:
        scoring_error = str(e)
    slots = starting_slots(store.slot_counts(store.settings_of(con, fa.league_id, radar.season)))
    journal, journal_error = None, ""
    try:
        journal = build_journal(con, predictions, warehouse, season=radar.season,
                                pins_path=pins_path)  # fmt: skip
    except JournalUnavailableError as e:
        journal_error = str(e).removeprefix("My League: ")
    return ReportData(
        radar=radar, synced_at=fa.synced_at, team_name=team_name(con, fa.league_id, radar.season),
        tags=owner_tags(radar), regret=regret, regret_error=regret_error, slots=slots[0],
        scoring=scoring, scoring_error=scoring_error, settings=settings_summary(con),
        weeks=week_check(con, warehouse), scoring_note=scoring_note(con), notes=list(radar.notes),
        journal=journal, journal_error=journal_error,
        questionable=questionable_section(predictions, radar),
        teammate_out=teammate_out_section(con, predictions, radar),
    )  # fmt: skip


def questionable_section(predictions: Path, radar: PersonalRadar) -> object | None:
    """Feature #1: the owner's tagged players from the stored Questionable list (None: no
    roster; a broken store never breaks the report)."""
    from twm.modules.questionable.league import for_report

    try:
        return for_report(predictions, radar.roster, radar.season)
    except Exception:  # noqa: BLE001 - informative only
        return None


def teammate_out_section(
    con: duckdb.DuckDBPyConnection, predictions: Path, radar: PersonalRadar
) -> object | None:
    """Feature #5: the owner's players and the free agents who gain from the stored
    Teammate-out list (None: no roster or no list; a broken store never breaks the report)."""
    from twm.modules.teammate_out.league import for_report

    try:
        return for_report(con, predictions, radar.roster, radar.season)
    except Exception:  # noqa: BLE001 - informative only
        return None


# ---- HTML ----------------------------------------------------------------------------------

CSS = """
:root{color-scheme:light dark;--bg:#f6f6f2;--fg:#1b1f24;--muted:#56606b;--card:#fff;
--line:#d7dad3;--accent:#0a6a4c;--warn-bg:#fff3d1;--warn-fg:#5e4100;--mark:#e3f4ec}
@media (prefers-color-scheme:dark){:root{--bg:#11151a;--fg:#e8eaed;--muted:#a7b0ba;
--card:#1a2028;--line:#303a46;--accent:#6fd9ad;--warn-bg:#3b2f10;--warn-fg:#ffdc92;
--mark:#173a2d}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,-apple-system,
"Segoe UI",Roboto,sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:1.7rem;margin:0 0 4px}h2{font-size:1.25rem;margin:0 0 6px}
h3{font-size:1.02rem;margin:18px 0 6px}
p{margin:6px 0}.muted{color:var(--muted)}.small{font-size:.9rem}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:16px;margin:16px 0}
.warn{background:var(--warn-bg);color:var(--warn-fg);border-radius:8px;padding:10px 12px;
margin:10px 0}
.scroll{overflow-x:auto;margin:8px 0}
table{border-collapse:collapse;width:100%;font-size:.93rem}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-weight:600;color:var(--muted);white-space:nowrap}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tr.pick td{background:var(--mark);font-weight:600}
nav ol{margin:6px 0;padding-left:20px}a{color:var(--accent)}
pre{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px;
overflow-x:auto;font-size:.88rem}
ul{margin:6px 0;padding-left:20px}li{margin:3px 0}
"""


class Raw(str):
    """HTML that is already escaped (a cell or a paragraph built here)."""


def esc(x: object) -> str:
    import html

    return x if isinstance(x, Raw) else html.escape("-" if x is None else str(x))


def para(text: object, cls: str = "") -> Raw:
    attr = f' class="{cls}"' if cls else ""
    return Raw(f"<p{attr}>{esc(text)}</p>")


def table(head: list[str], rows: list[list[object]], numeric: set[int] = frozenset(),
          marked: set[int] = frozenset()) -> Raw:  # fmt: skip
    """A table in a horizontal scroller (``numeric``: right-aligned columns; ``marked``: the
    highlighted rows)."""

    def cell(tag: str, i: int, v: object) -> str:
        return f"<{tag}{' class="n"' if i in numeric else ''}>{esc(v)}</{tag}>"

    th = "".join(cell("th", i, h) for i, h in enumerate(head))
    body = "".join(
        f"<tr{' class=\"pick\"' if j in marked else ''}>"
        + "".join(cell("td", i, v) for i, v in enumerate(r)) + "</tr>"
        for j, r in enumerate(rows))  # fmt: skip
    return Raw(f'<div class="scroll"><table><thead><tr>{th}</tr></thead><tbody>{body}'
               "</tbody></table></div>")  # fmt: skip


def bullets(items: list[object]) -> Raw:
    if not items:
        return Raw("")
    return Raw("<ul>" + "".join(f"<li>{esc(i)}</li>" for i in items) + "</ul>")


def section(sid: str, title: str, parts: list[object]) -> Raw:
    body = "".join(p if isinstance(p, Raw) else para(p) for p in parts)
    return Raw(f'<section id="{sid}" data-section="{sid}"><h2>{esc(title)}</h2>{body}</section>')


# ---- the sections ------------------------------------------------------------------------

POS_NAMES = {"QB": "Quarterbacks", "RB": "Running backs", "WR": "Wide receivers",
             "TE": "Tight ends", "K": "Kickers", "DST": "D/ST"}  # fmt: skip


def _owned(x: object) -> str:
    return "-" if x is None else f"{float(x):.0f}%"  # type: ignore[arg-type]


def position_block(pl_: PositionList, what: str, limit: int) -> list[object]:
    out: list[object] = [Raw(f"<h3>{esc(POS_NAMES[pl_.position])}</h3>")]
    if pl_.picks.height:
        rows = [[r["rank"], r["player_name"], r["pro_team"], r["chance"], r["tier"],
                 _owned(r["percent_owned"])] for r in pl_.picks.iter_rows(named=True)]  # fmt: skip
        out.append(table(["list rank", "player", "NFL team", "chance", "priority",
                          "ESPN owned"], rows, numeric={0, 5}))  # fmt: skip
        if pl_.free > pl_.picks.height:
            out.append(para(f"{pl_.free - pl_.picks.height} more free agent(s) further down "
                            f"the {what}'s list.", "muted small"))  # fmt: skip
    else:
        out.append(para(f"None of the {what}'s players at this position is a free agent in "
                        "your league.", "muted"))  # fmt: skip
    if pl_.outside.height:
        names = [f"{r['player_name']} ({r['pro_team'] or '-'}, {_owned(r['percent_owned'])} "
                 "owned)" for r in pl_.outside.head(limit).iter_rows(named=True)]  # fmt: skip
        more = pl_.outside.height - len(names)
        out.append(para(f"ESPN free agents outside the {what}'s pool (no chance is given, "
                        "never guessed): " + ", ".join(names)
                        + (f" and {more} more." if more > 0 else "."), "small"))  # fmt: skip
    if pl_.unknown:
        out.append(para(f"{pl_.unknown} of the {what}'s top {limit} are neither among the "
                        "synced free agents (ESPN's top 50 per position) nor on a roster.",
                        "muted small"))  # fmt: skip
    return out


def radar_section(d: ReportData, limit: int) -> Raw:
    w = d.radar.week
    parts: list[object] = [
        f"Players you can pick up now, in the Waiver Radar's order (the list made after week "
        f"{w}'s games). The chance is how often players the Radar rated like him in past "
        "seasons had a week good enough to start at his position in their team's next 3 games: "
        "a range, not a promise. Priority: must-add (50% or more), speculative (25-50%), watch.",
    ]
    if d.scoring_note:
        parts.append(Raw(f'<p class="warn">{esc(d.scoring_note)}</p>'))
    for pl_ in d.radar.lists:
        if pl_.position in ("QB", "RB", "WR", "TE"):
            parts += position_block(pl_, "Radar", limit)
    return section("radar", "Waiver Radar: free agents worth adding", parts)


def streamer_section(d: ReportData, limit: int) -> Raw:
    parts: list[object] = [
        "Kickers and D/STs are picked one week at a time. The chance is how often similar past "
        "picks had a week good enough to start at the position the next week.",
    ]
    for pl_ in d.radar.lists:
        if pl_.position in ("K", "DST"):
            parts += position_block(pl_, "streamer", limit)
    return section("streamer", "K and D/ST streamer: free agents for this week", parts)


def drops_section(d: ReportData) -> Raw:
    from twm.league.drops import SOURCES, describe, left_text, value_text

    v = d.radar.drops
    parts: list[object] = [
        "If you need a roster spot for a pickup, this is who to let go. We build your best "
        "lineup for the rest of the season from each player's number (Regression Watch's "
        "projection in points per game; for a player outside it, his season average; for K and "
        "D/ST, the streamer's chance this week). Its starters are kept; the candidate is the "
        "lowest-numbered player on the bench. Players in your IR slot are never suggested.",
    ]
    if v is None:
        parts.append(para("Your team was not identified in the last sync (ESPN_SWID matched no "
                          "team owner), so there is no roster to look at.", "warn"))  # fmt: skip
        return section("drops", "Drop candidates", parts)
    if v.candidate is not None:
        parts.append(Raw(f'<p class="warn"><strong>Drop candidate:</strong> '
                         f"{esc(describe(v, v.candidate))}</p>"))  # fmt: skip
    else:
        parts.append(para("No drop candidate: no QB, RB, WR or TE on your bench has a number.",
                          "muted"))  # fmt: skip
    for r in v.spares:
        parts.append(para(f"Spare {r.position}: {describe(v, r)}. You only start one; the one "
                          "with the lower chance is the easy drop."))  # fmt: skip
    rows = [[s, r.name if r else "(empty)", r.position if r else "", value_text(r) if r else "",
             SOURCES[r.source] if r else ""] for s, r in v.lineup]  # fmt: skip
    parts += [
        Raw("<h3>Your best lineup by these numbers</h3>"),
        table(["slot", "player", "pos", "number", "what the number is"], rows, {3}),
    ]
    brows = [[r.name, r.position, r.slot, value_text(r), left_text(v, r) or "-",
              SOURCES[r.source]] for r in v.bench]  # fmt: skip
    marked = {i for i, r in enumerate(v.bench) if r is v.candidate}
    parts += [Raw("<h3>Your bench, lowest number first</h3>"),
              table(["player", "pos", "ESPN slot", "number", "rest of the season",
                     "what the number is"], brows, {3}, marked)]  # fmt: skip
    if v.on_ir:
        parts.append(para(f"{v.on_ir} player(s) in your IR slot left out.", "muted small"))
    parts += [para(f"Note: {n}", "muted small") for n in v.notes]
    return section("drops", "Drop candidates", parts)


def _n(x: object, fmt: str = ".1f") -> str:
    return "-" if x is None else format(float(x), fmt)  # type: ignore[arg-type]


def tags_section(d: ReportData) -> Raw:
    parts: list[object] = [
        "Regression Watch compares what a player has scored so far with what his opportunities "
        "(expected fantasy points, xFP) and the history of similar players say he will score "
        "from here. Sell-high: he has scored well above that projection, so his trade value may "
        "be at its peak. Buy-low: he has scored below it, so better weeks are likely: keep him, "
        "and do not sell him cheap.",
    ]
    if not d.tags:
        parts.append(para("None of your players has a Sell-high or Buy-low tag this week.",
                          "muted"))  # fmt: skip
        return section("tags", "Regression Watch on your players", parts)
    rows = []
    for t in d.tags:
        r = t.reasons
        rows.append([t.name, t.position, TAG_NAMES[t.tag],
                     f"{_n(r.get('ppg'))} ({r.get('games', '-')} games)", _n(t.projection),
                     _n(r.get("gap"), "+.1f"), _n(r.get("last3_ppg")), _n(r.get("xfp_pg")),
                     _n(r.get("fpoe_pg"), "+.1f")])  # fmt: skip
    parts.append(table(["player", "pos", "tag", "points per game so far", "projection, rest "
                        "of season", "gap", "last 3 games", "xFP per game", "FPOE per game"],
                       rows, {3, 4, 5, 6, 7, 8}))  # fmt: skip
    parts.append(para("Gap = projection minus points per game so far. FPOE = fantasy points "
                      "over expected (scoring above what his opportunities usually give).",
                      "muted small"))  # fmt: skip
    return section("tags", "Regression Watch on your players", parts)


def regret_section(d: ReportData) -> Raw:
    from twm.league.regret import MEANING

    res = d.regret
    parts: list[object] = [
        "How many points your lineup choices cost you each week. The decision-time best lineup "
        "is the one ESPN's own projections (shown before kickoff) would have set; the "
        "hindsight best is the best lineup you could have set knowing the scores. " + MEANING,
    ]
    if d.slots:
        parts.append(
            para(
                "Your league's starting slots: "
                + ", ".join(f"{k} {n}" for k, n in d.slots.items())
                + ".",
                "small",
            )
        )
    if res is None:
        parts.append(para(d.regret_error, "muted"))
        return section("regret", "Lineup regret", parts)
    if res.left_out:
        parts.append(para("Left out (synced before the week's games were over): week(s) "
                          + ", ".join(map(str, res.left_out)) + "; sync them again with "
                          "`uv run twm league sync --week N`.", "muted small"))  # fmt: skip
    if not res.weeks:
        parts.append(para("No finished week to review yet.", "muted"))
        return section("regret", "Lineup regret", parts)
    cols = ("actual", "decision", "hindsight", "lost_decisions", "lost_luck")
    tot = {c: res.total(c) for c in cols}
    parts.append(para(f"Season so far ({len(res.weeks)} week(s)): {tot['actual']:.2f} points; "
                      f"{tot['lost_decisions']:+.2f} lost to decisions and {tot['lost_luck']:.2f}"
                      " lost to luck."))  # fmt: skip
    rows = [[w.week, f"{w.actual:.2f}", f"{w.decision:.2f}", f"{w.hindsight:.2f}",
             f"{w.lost_decisions:+.2f}", f"{w.lost_luck:.2f}"] for w in res.weeks]  # fmt: skip
    rows.append(["season", *(f"{tot[c]:+.2f}" if c == "lost_decisions" else f"{tot[c]:.2f}"
                             for c in cols)])  # fmt: skip
    parts.append(table(["week", "actual", "decision-time best", "hindsight best",
                        "lost to decisions", "lost to luck"], rows, {1, 2, 3, 4, 5},
                       {len(rows) - 1}))  # fmt: skip
    changes = []
    for w in res.weeks:
        add, drop = w.decision_lineup.keys - w.started, w.started - w.decision_lineup.keys
        if add or drop:
            who = [", ".join(sorted(res.names.get(k) or str(k) for k in ks)) or "-"
                   for ks in (add, drop)]  # fmt: skip
            changes.append(f"Week {w.week}: start {who[0]} instead of {who[1]} "
                           f"({w.lost_decisions:+.2f} points)")  # fmt: skip
    if changes:
        parts += [Raw("<h3>What ESPN's projections would have started instead</h3>"),
                  bullets(changes)]  # fmt: skip
    parts += [para(f"Note: {n}", "muted small") for n in res.notes]
    return section("regret", "Lineup regret", parts)


def journal_section(d: ReportData) -> Raw:
    from twm.league import journal as jr

    title = "You vs the model"
    parts: list[object] = [
        "A learning log: your own adds, drops and lineups next to what the app said at the "
        "time. " + jr.POINT_IN_TIME,
    ]
    j = d.journal
    if j is None:
        parts.append(para(d.journal_error or "Nothing to review yet.", "muted"))
        return section("journal", title, parts)
    parts += [Raw("<h3>So far</h3>"), bullets(jr.summary(j))]
    adds = [[jr.week_label(a.week), jr.time_label(a.at), a.how, a.name, a.position,
             a.said.text(), a.outcome, f"{a.points:.2f} ({a.started} start(s), "
             f"{a.rostered} week(s))"] for a in j.adds]  # fmt: skip
    drops = [[jr.week_label(x.week), jr.time_label(x.at), x.name, x.position, x.said.text(),
              jr.points_label(x.points), f"{x.other or '-'} ({x.basis})",
              jr.points_label(x.other_points), x.weeks] for x in j.drops]  # fmt: skip
    missed = [[jr.week_label(x.week), x.list_week, x.name, x.position, x.rank, x.chance,
               x.outcome] for x in j.missed]  # fmt: skip
    weeks = [[w.week, f"{w.actual:.2f}", f"{w.decision:.2f}", f"{w.lost_decisions:+.2f}"]
             for w in jr.lineup_weeks(j)]  # fmt: skip
    tables = (
        ("Adds", ["week", "when", "how", "player", "pos", "what the app said", "outcome",
                  "points for you"], adds, {7}),
        ("Drops", ["week", "when", "player", "pos", "what the app said", "points since",
                   "compared with", "its points", "finished weeks"], drops, {5, 7, 8}),
        ("Radar must-adds left on waivers", ["week", "list week", "player", "pos", "rank",
                                             "chance", "outcome"], missed, {1, 4}),
        ("Lineups", ["week", "started", "decision-time best", "lost to decisions"], weeks,
         {1, 2, 3}),
    )  # fmt: skip
    for name, head, rows, numeric in tables:
        parts.append(Raw(f"<h3>{esc(name)} ({len(rows)})</h3>"))
        parts.append(table(head, rows, numeric) if rows else para("None.", "muted small"))
    parts += [para(jr.OUTCOME, "muted small"),
              para("Drops compare points by config/scoring.yaml from nflverse stats, the same "
                   "finished weeks for both players (QB/RB/WR/TE). Lineups: the decision-time "
                   "best is the lineup ESPN's own projections would have started (Lineup "
                   "regret above).", "muted small")]  # fmt: skip
    parts += [para(f"Note: {n}", "muted small") for n in j.notes]
    return section("journal", title, parts)


def _explained(r) -> str:  # noqa: ANN001 - a scoring_check.PlayerWeek
    if r.explained_by is None:
        return "not derivable from the stats we hold (a stat correction, or a rule we do not model)"
    return "; ".join(e.text for e in r.explained_by)


def scoring_section(d: ReportData) -> Raw:
    from twm.league.scoring_check import TOLERANCE, evidence

    res = d.scoring
    parts: list[object] = [
        "We score your players' games ourselves (the NFL's stats with config/scoring.yaml, the "
        "rules the models were built with) and compare that with ESPN's box scores. A "
        "difference shows a rule where the config and your league disagree. Nothing is changed "
        "automatically: the open questions below say what the synced weeks show.",
    ]
    if res is None:
        parts.append(para(f"No scoring check: {d.scoring_error}.", "muted"))
        return section("scoring", "Scoring check: our points against ESPN's", parts)
    if not res.weeks:
        left = ", ".join(map(str, res.left_out))
        parts.append(
            para(
                "No finished week to compare yet"
                + (f" (synced before the games were over: week(s) {left})" if left else "")
                + "."
            )
        )
        return section("scoring", "Scoring check: our points against ESPN's", parts)
    diff = res.differing
    by_settings = sum(1 for r in diff if r.explained_by and all(
        e.question == "league_settings" for e in r.explained_by))  # fmt: skip
    unknown = sum(1 for r in diff if r.explained_by is None)
    weeks = ", ".join(map(str, res.weeks)) or "none"
    parts.append(para(
        f"{len(res.rows)} player-week(s) compared (week(s) {weeks}); {len(diff)} differ by more "
        f"than {TOLERANCE:g} points: {by_settings} explained by your league's own settings, "
        f"{len(diff) - by_settings - unknown} by one of the open questions below, {unknown} not "
        "derivable from the stats we hold."))  # fmt: skip
    if diff:
        rows = [[r.week, r.name, r.position, f"{r.espn:.2f}", f"{r.ours:.2f}", f"{r.league:.2f}",
                 f"{r.diff:+.2f}", _explained(r)] for r in diff]  # fmt: skip
        parts.append(table(["week", "player", "pos", "ESPN", "ours (config)", "ours (your "
                            "league's settings)", "ESPN - ours", "what explains it"], rows,
                           {0, 3, 4, 5, 6}))  # fmt: skip
    parts += [
        Raw("<h3>The open scoring questions</h3>"),
        bullets([Raw(f"<strong>{esc(t)}</strong>: {esc(x)}") for t, x in evidence(res)]),
    ]
    extra = []
    if res.no_stats:
        extra.append(
            f"{len(res.no_stats)} player-week(s) with ESPN points but no stats in the "
            "warehouse yet: " + ", ".join(f"{n} (week {w})" for w, n in res.no_stats)
        )
    if res.unlinked:
        extra.append(
            f"{len(res.unlinked)} player-week(s) without a link to the NFL data (no "
            "gsis id): " + ", ".join(f"{n} (week {w})" for w, n in res.unlinked)
        )
    if res.left_out:
        extra.append("Left out (synced before the games were over): week(s) "
                     + ", ".join(map(str, res.left_out)))  # fmt: skip
    parts += [para(x, "muted small") for x in extra]
    return section("scoring", "Scoring check: our points against ESPN's", parts)


def settings_section(d: ReportData) -> Raw:
    from twm.league.settings_diff import fmt

    s = d.settings
    parts: list[object] = [
        "Your league's real settings (from the last sync) against the config files the models "
        "use. Nothing is applied: copy what you agree with into config/ yourself.",
    ]
    if s is None:
        parts.append(para("No settings synced yet.", "muted"))
        return section("settings", "Your league's settings against the config", parts)
    if s.diffs:
        parts.append(table(["file", "setting", "your league", "config", "note"],
                           [[x.file, x.item, fmt(x.espn), fmt(x.config), x.note or "-"]
                            for x in s.diffs]))  # fmt: skip
    else:
        parts.append(para("No differences: the config matches your league."))
    if s.notes:
        parts += [Raw("<h3>To decide by hand (no config key can say it)</h3>"), bullets(s.notes)]
    if s.patch:
        parts += [Raw("<h3>Suggested patch (not applied)</h3>"),
                  Raw(f"<pre>{esc(chr(10).join(s.patch))}</pre>")]  # fmt: skip
    return section("settings", "Your league's settings against the config", parts)


SECTIONS = (("radar", "Waiver Radar"), ("streamer", "K and D/ST streamer"),
            ("drops", "Drop candidates"), ("tags", "Regression Watch on your players"),
            ("regret", "Lineup regret"), ("journal", "You vs the model"),
            ("scoring", "Scoring check"),
            ("settings", "Settings against the config"))  # fmt: skip


def _utc(t: datetime | None) -> str:
    return "unknown" if t is None else f"{t:%Y-%m-%d %H:%M} UTC"


def render(d: ReportData, generated: datetime, limit: int = 10) -> str:
    """The whole HTML file; ``generated`` (UTC) is printed on the one line that changes."""
    r = d.radar
    title = f"My League report: {r.season} week {r.week}"
    team = f"{d.team_name}: " if d.team_name else ""
    head = [
        Raw(f"<h1>{esc(title)}</h1>"),
        para(f"{team}your league this week, from the lists made after week {r.week}'s games."),
        para(
            f"League data synced {_utc(d.synced_at)} (ESPN week {r.sync_week}). Model lists: "
            f"week {r.week}, data as of {_utc(r.as_of)}.",
            "small",
        ),  # fmt: skip
        Raw(f'<p class="muted small" data-generated>{GENERATED_PREFIX}{_utc(generated)}.</p>'),
    ]
    if d.weeks is not None and d.weeks.warning:
        head.append(Raw(f'<p class="warn" data-week-check><strong>Week check:</strong> '
                        f"{esc(d.weeks.warning)}</p>"))  # fmt: skip
    head += [para(f"Note: {n}", "muted small") for n in d.notes]
    nav = "".join(f'<li><a href="#{sid}">{esc(name)}</a></li>' for sid, name in SECTIONS)
    body = [radar_section(d, limit), streamer_section(d, limit), drops_section(d)]
    body += [tags_section(d), regret_section(d), journal_section(d), scoring_section(d),
             settings_section(d)]  # fmt: skip
    if d.startsit is not None:  # feature #2: start/sit odds (FantasyPros ranks: local only)
        from twm.modules.startsit.league import SECTION_ID, TITLE, render_section

        nav += f'<li><a href="#{SECTION_ID}">{esc(TITLE)}</a></li>'
        body.append(render_section(d.startsit))
    if d.questionable is not None:  # feature #1: tagged players (twm.modules.questionable)
        from twm.modules.questionable.league import SECTION_ID as Q_ID
        from twm.modules.questionable.league import TITLE as Q_TITLE
        from twm.modules.questionable.league import render_section as q_section

        nav += f'<li><a href="#{Q_ID}">{esc(Q_TITLE)}</a></li>'
        body.append(q_section(d.questionable))
    if d.teammate_out is not None:  # feature #5: who gains when a starter sits
        from twm.modules.teammate_out.league import SECTION_ID as T_ID
        from twm.modules.teammate_out.league import TITLE as T_TITLE
        from twm.modules.teammate_out.league import render_section as t_section

        nav += f'<li><a href="#{T_ID}">{esc(T_TITLE)}</a></li>'
        body.append(t_section(d.teammate_out))
    foot = para("A local report: never published, never sent anywhere. Chances and projections "
                "are estimates from past seasons, not promises. NFL data: nflverse. League "
                "data: ESPN (your cookies are not in this file).", "muted small")  # fmt: skip
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta name="robots" content="noindex, nofollow">'
            '<meta name="color-scheme" content="light dark">'
            f"<title>{esc(title)}</title><style>{CSS}</style></head>\n<body><main>"
            + "".join(head) + f'<nav aria-label="Sections"><ol>{nav}</ol></nav>\n'
            + "\n".join(body) + f"\n<footer>{foot}</footer></main></body></html>\n")  # fmt: skip


def report_path(directory: Path, season: int, week: int) -> Path:
    return directory / f"{season}-W{week:02d}.html"


def write(d: ReportData, directory: Path, generated: datetime, limit: int = 10) -> Path:
    path = report_path(directory, d.radar.season, d.radar.week)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".html.tmp")
    tmp.write_text(render(d, generated, limit), encoding="utf-8")
    tmp.replace(path)
    return path
