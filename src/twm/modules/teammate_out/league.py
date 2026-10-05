"""My League: "Teammates out" in the local weekly report (`twm league weekly`). ESPN-optional:
without a synced league there is no report, and without the owner's roster rows (or before
any list is stored) the section is absent (``None``).

From the latest stored Teammate-out snapshot of the season (:mod:`.weekly`): the owner's
players who gain because a teammate sits, and the league's free agents who gain (pickup
candidates, with ESPN's percent rostered).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

SECTION_ID = "teammate_out"
TITLE = "Teammates out: who gains"
MAX_FREE_AGENTS = 10


@dataclass
class Section:
    season: int
    week: int | None
    as_of: datetime | None
    mine: list[dict[str, Any]] = field(default_factory=list)
    free_agents: list[dict[str, Any]] = field(default_factory=list)


def gainers(snap: pl.DataFrame) -> pl.DataFrame:
    """Rows predicted to gain (predicted points above his usual), biggest gain first."""
    g = snap.with_columns((pl.col("pred_points") - pl.col("base_points")).alias("gain"))
    return g.filter(pl.col("gain") > 0).sort(["gain", "player"], descending=[True, False])


def free_agents(con: duckdb.DuckDBPyConnection, season: int) -> pl.DataFrame:
    """entity_id and percent_owned of the newest free-agent sync of ``season``."""
    from twm.league import store

    part = store.current(con, "league_free_agents", season)
    if part is None:
        return pl.DataFrame(schema={"gsis_id": pl.String, "percent_owned": pl.Float64})
    return con.execute(
        "SELECT entity_id AS gsis_id, max(percent_owned) AS percent_owned FROM "
        "league_free_agents WHERE league_id = ? AND season = ? AND week = ? AND synced_at = ? "
        "AND entity_id IS NOT NULL GROUP BY 1",
        [part.league_id, part.season, part.week, part.synced_at],
    ).pl()  # fmt: skip


def for_report(
    con: duckdb.DuckDBPyConnection, predictions: Path, roster: pl.DataFrame, season: int
) -> Section | None:
    """The section's data; None when the owner's roster is unknown or no list is stored."""
    from twm.modules.questionable.league import latest_snapshot
    from twm.modules.teammate_out import weekly as wk

    if roster.is_empty() or "entity_id" not in roster.columns:
        return None
    snap = latest_snapshot(wk.read_snapshots(predictions, season))
    if snap.is_empty():
        return None
    g = gainers(snap)
    mine = g.join(roster.select(pl.col("entity_id").alias("gsis_id")).unique(), on="gsis_id")
    fa = g.join(free_agents(con, season), on="gsis_id").head(MAX_FREE_AGENTS)
    return Section(season, int(snap["week"][0]), snap["as_of"][0], mine.to_dicts(),
                   fa.to_dicts())  # fmt: skip


def _line(r: dict[str, Any]) -> list[str]:
    lo, hi = r.get("points_lo"), r.get("points_hi")
    rng = "-" if lo is None or hi is None else f"{lo:.1f} to {hi:.1f}"
    return [f"{r['player']} ({r['position']}, {r['team']})", f"{r['out_players']} out",
            f"{r['base_points']:.1f} -> {r['pred_points']:.1f}", rng,
            f"{r['base_target_share']:.0%} -> {r['pred_target_share']:.0%}",
            f"{r['base_carry_share']:.0%} -> {r['pred_carry_share']:.0%}"]  # fmt: skip


def _owned(x: object) -> str:
    return "-" if x is None else f"{float(x):.0f}% rostered"  # type: ignore[arg-type]


def render_section(sec: Section) -> Any:
    """The HTML section (twm.league.report's helpers)."""
    from twm.league.report import para, section, table
    from twm.modules.teammate_out.weekly import NOTE

    head = ["player", "why", "points (usual -> predicted)", "80% range", "target share",
            "carry share"]  # fmt: skip
    parts: list[object] = [
        "When a starter sits, his work goes to his teammates: the players predicted to gain "
        "this week, from past seasons' games with a starter out (2013 on)."
    ]
    parts.append(para(f"Week {sec.week} list as of {sec.as_of:%Y-%m-%d %H:%M} UTC.", "small"))
    parts.append(para("Your players", "small"))
    rows = [_line(r) for r in sec.mine]
    parts.append(table(head, rows, {2}) if rows else para("None of your players.", "muted"))
    parts.append(para("Free agents who gain (pickup candidates)", "small"))
    fa = [[*_line(r)[:4], _owned(r.get("percent_owned"))] for r in sec.free_agents]
    parts.append(table([*head[:4], "ESPN"], fa, {2}) if fa else para("None.", "muted"))
    parts.append(para(NOTE, "muted small"))
    return section(SECTION_ID, TITLE, parts)
