"""My League: "Your playoff weeks" in the local weekly report (`twm league weekly`). ESPN-optional:
without a synced league there is no report, and without the owner's roster rows (or before any
grid is stored) the section is absent (``None``).

From the latest stored playoff-planner snapshot of the season (:mod:`.weekly`): each of the
owner's players (by NFL team and position) and the Radar's best free agents with their
opponents and matchup ratings in the league's fantasy playoff weeks (from its synced settings:
the weeks after the regular season, one round per ``playoff_matchup_period_length`` weeks;
15-17 when the settings say nothing), the mean rating over his games, and the honest line on
how much a matchup moved points in 2013-2025 (the pinned effect sizes).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

SECTION_ID = "playoff_planner"
TITLE = "Your playoff weeks"
DEFAULT_WEEKS = (15, 16, 17)
FREE_AGENTS_PER_POSITION = 2


@dataclass
class Section:
    season: int
    weeks: list[int]
    through_week: int
    as_of: datetime | None
    mine: list[dict[str, Any]] = field(default_factory=list)
    free_agents: list[dict[str, Any]] = field(default_factory=list)
    matters: list[str] = field(default_factory=list)  # one line per position
    missing_weeks: list[int] = field(default_factory=list)  # league weeks not in the grid


def playoff_weeks(settings: dict[str, str]) -> list[int]:
    """The league's fantasy playoff weeks from its synced settings (DEFAULT_WEEKS without
    reg_season_count or playoff_team_count)."""

    def num(key: str) -> int | None:
        v = str(settings.get(key, "")).strip()
        return int(v) if v.isdigit() else None

    reg, teams = num("reg_season_count"), num("playoff_team_count")
    if not reg or not teams or teams < 2:
        return list(DEFAULT_WEEKS)
    length = max(num("playoff_matchup_period_length") or 1, 1)
    rounds = math.ceil(math.log2(teams))
    return list(range(reg + 1, reg + 1 + rounds * length))


def latest(snaps: pl.DataFrame) -> pl.DataFrame:
    """The newest snapshot's rows (empty in, empty out)."""
    if snaps.is_empty():
        return snaps
    return snaps.filter(pl.col("as_of") == snaps["as_of"].max())


def player_rows(
    grid: pl.DataFrame, players: pl.DataFrame, weeks: list[int]
) -> list[dict[str, Any]]:
    """One dict per player (``players``: player_name, position, pro_team[, percent_owned]):
    ``games`` = [(week, opponent or None, rating or None)], ``mean_rating`` over his games."""
    g = grid.filter(pl.col("week").is_in(weeks))
    out = []
    for p in players.iter_rows(named=True):
        mine = g.filter(
            (pl.col("team") == p.get("pro_team")) & (pl.col("position") == p.get("position"))
        )
        if mine.is_empty():
            continue
        games = [
            (int(r["week"]), r["opponent"], r["rating"])
            for r in mine.sort("week").iter_rows(named=True)
        ]
        rated = [x for _, o, x in games if o is not None and x is not None]
        out.append({"player": p.get("player_name"), "position": p.get("position"),
                    "team": p.get("pro_team"), "percent_owned": p.get("percent_owned"),
                    "candidate": mine["candidate"][0], "games": games,
                    "mean_rating": sum(rated) / len(rated) if rated else None})  # fmt: skip
    return sorted(out, key=lambda r: (-(r["mean_rating"] or 0.0), str(r["player"])))


def matters_lines(spec: Any) -> list[str]:
    """Per position: the backtest's best-vs-worst-fifth gap, realized in weeks 15-17."""
    from twm.modules.playoff_planner import history as hs

    out = []
    for pos in hs.POSITIONS:
        e = spec.effect(pos)
        if e is None:
            continue
        used = spec.chosen.get(pos, "none")
        tail = "ratings used" if used != "none" else "no gain found: every matchup counts as 1.00"
        out.append(f"{'D/ST' if pos == 'DST' else pos}: {e['realized_gap']:+.1f} pts/game "
                   f"between the easiest and the hardest fifth of matchups ({tail})")  # fmt: skip
    return out


def radar_free_agents(radar: Any, per_position: int = FREE_AGENTS_PER_POSITION) -> pl.DataFrame:
    """The top free agents of each Radar list (player_name, position, pro_team,
    percent_owned)."""
    parts = []
    for lst in getattr(radar, "lists", []) or []:
        picks = lst.picks
        if picks is None or picks.is_empty() or "pro_team" not in picks.columns:
            continue
        cols = [c for c in ("player_name", "pro_team", "percent_owned") if c in picks.columns]
        parts.append(picks.head(per_position).select(cols).with_columns(
            pl.lit(lst.position).alias("position")))  # fmt: skip
    return pl.concat(parts, how="diagonal_relaxed") if parts else pl.DataFrame()


def league_weeks(con: duckdb.DuckDBPyConnection, season: int) -> list[int]:
    """The owner's league's playoff weeks (DEFAULT_WEEKS when nothing is synced)."""
    from twm.league import store

    try:
        part = store.current(con, "league_free_agents", season)
        if part is None:
            return list(DEFAULT_WEEKS)
        return playoff_weeks(store.settings_of(con, part.league_id, part.season))
    except duckdb.Error:  # an older store without the tables: the default weeks
        return list(DEFAULT_WEEKS)


def for_report(con: duckdb.DuckDBPyConnection, predictions: Path, radar: Any,
               season: int) -> Section | None:  # fmt: skip
    """The section's data; None when the owner's roster is unknown or no grid is stored."""
    from twm import pins
    from twm.modules.playoff_planner import production as pp
    from twm.modules.playoff_planner import weekly as wk

    roster = getattr(radar, "roster", None)
    if roster is None or roster.is_empty() or "pro_team" not in roster.columns:
        return None
    snap = latest(wk.read_snapshots(predictions, season))
    if snap.is_empty():
        return None
    weeks = league_weeks(con, season)
    have = set(snap["week"].unique().to_list())
    shown = [w for w in weeks if w in have]
    try:
        spec, _ = pp.load_pinned(season)
        matters = matters_lines(spec)
    except pins.PinError:
        matters = []
    return Section(season, shown, int(snap["through_week"][0]), snap["as_of"][0],
                   player_rows(snap, roster, shown),
                   player_rows(snap, radar_free_agents(radar), shown), matters,
                   [w for w in weeks if w not in have])  # fmt: skip


def _game(g: tuple[int, Any, Any]) -> str:
    _, opp, rating = g
    return "bye" if opp is None else f"{opp} {rating:.2f}"


def _owned(x: object) -> str:
    return "-" if x is None else f"{float(x):.0f}%"  # type: ignore[arg-type]


def render_section(sec: Section) -> Any:
    """The HTML section (twm.league.report's helpers)."""
    from twm.league.report import para, section, table
    from twm.modules.playoff_planner.weekly import NOTE

    head = ["player", *[f"week {w}" for w in sec.weeks], "mean"]
    parts: list[object] = [
        "Your fantasy playoff weeks: each player's opponents and matchup rating (1.00 = an "
        "average matchup; 1.10 = units at his position scored 10% more than average against "
        "that opponent this season). It matters a little. What a matchup was worth in weeks "
        "15-17 of 2013-2025:"
    ]
    parts += [para(line, "small") for line in sec.matters]
    when = "-" if sec.as_of is None else f"{sec.as_of:%Y-%m-%d %H:%M} UTC"
    parts.append(para(f"Ratings through week {sec.through_week} (as of {when}).", "small"))

    def rows(rs: list[dict[str, Any]], extra: bool) -> list[list[object]]:
        return [[f"{r['player']} ({r['position']}, {r['team']})", *[_game(g) for g in r["games"]],
                 "-" if r["mean_rating"] is None else f"{r['mean_rating']:.2f}",
                 *([_owned(r.get("percent_owned"))] if extra else [])] for r in rs]  # fmt: skip

    mine = rows(sec.mine, False)
    parts.append(para("Your players", "small"))
    parts.append(table(head, mine, set()) if mine else para("None of your players.", "muted"))
    fa = rows(sec.free_agents, True)
    parts.append(para("The Radar's best free agents", "small"))
    parts.append(table([*head, "ESPN"], fa, set()) if fa else para("None.", "muted"))
    if sec.missing_weeks:
        parts.append(para(f"Weeks {sec.missing_weeks} of your playoffs are not in the grid "
                          "(it covers NFL weeks 15-17).", "muted small"))  # fmt: skip
    parts.append(para(NOTE, "muted small"))
    return section(SECTION_ID, TITLE, parts)
