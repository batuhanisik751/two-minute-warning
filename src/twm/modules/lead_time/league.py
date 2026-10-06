"""My League: "Lead time this season" in the local weekly report (`twm league report`, `twm
league weekly`; feature #8, LOCAL ONLY: it reads the owner's league syncs and may name players).
ESPN-optional: without a synced league there is no report; without free-agent rows of the season
the section is absent (``None``).

The 2026 tracker (:mod:`.live`, docs/lead_time.md "2026 live"): ESPN's percent_owned of every
free agent of the owner's league at each sync (one value per waiver period, the latest sync in
it), the Radar's lists of the season from the predictions store, and per player his state:
**added** (seen under the threshold, later at or above it: the crowd's add, with the Radar's
lead), **already** (over it at his first sync), **below**, **left_pool** (under it when last
seen and missing from the latest sync: maybe rostered in the owner's league, so a later
crossing is invisible) or **unseen** (listed, never in a sync). Never published: the publish
and the pipeline never import this module (tests/test_league.py, tests/test_startsit.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from twm.modules.lead_time import PRIMARY
from twm.modules.lead_time import crowd as cr
from twm.modules.lead_time import live as lv

SECTION_ID = "lead-time"
TITLE = "Lead time this season"
WATCH = 10  # the Radar's must-adds still under the threshold, shown at most


@dataclass
class Section:
    season: int
    threshold: float
    periods: list[int]  # waiver periods with a sync (k = after week k's games)
    counts: dict[str, dict[str, int]]  # live_summary per state
    added: list[dict[str, Any]] = field(default_factory=list)  # the crowd's adds, with leads
    watch: list[dict[str, Any]] = field(default_factory=list)  # must-adds still under it
    n_watch: int = 0


def _names(warehouse: Path | None, ids: list[str]) -> pl.DataFrame:
    from twm.warehouse.build import connect

    con = connect(warehouse, read_only=True)
    try:
        df = con.execute("SELECT gsis_id, display_name AS name, position FROM dim_player "
                         "WHERE gsis_id IN (SELECT unnest(?::VARCHAR[]))", [ids]).pl()  # fmt: skip
    finally:
        con.close()
    return df


def _windows(warehouse: Path | None, season: int) -> pl.DataFrame:
    from twm.warehouse.build import connect

    con = connect(warehouse, read_only=True)
    try:
        return cr.read_windows(con, [season])
    finally:
        con.close()


def build_section(path: pl.DataFrame, lists: pl.DataFrame, names: pl.DataFrame, season: int,
                  threshold: float = PRIMARY) -> Section:  # fmt: skip
    """The section from the live path (:func:`.live.live_path`), the season's lists
    (:func:`.live.live_lists`) and the players' names (gsis_id, name, position)."""
    tr = lv.live_tracker(path, lists, threshold).join(names, on="gsis_id", how="left")
    counts = {r.pop("state"): r for r in lv.live_summary(tr).iter_rows(named=True)}
    tr = tr.with_columns(pl.col("name").fill_null(pl.col("gsis_id")))
    added = tr.filter(pl.col("state") == "added").sort("cross_period", "name")
    watch = tr.filter((pl.col("state") == "below") & pl.col("must_add").is_not_null())
    watch = watch.sort(["last_pct", "name"], descending=[True, False])
    keep = ["name", "position", "first_pct", "last_pct", "cross_period", "listed", "must_add",
            "lead_listed", "lead_must_add"]  # fmt: skip
    periods = sorted({int(p) for p in path.get_column("period").to_list()})
    return Section(season=season, threshold=threshold, periods=periods, counts=counts,
                   added=added.select(keep).to_dicts(), watch=watch.head(WATCH).select(keep)
                   .to_dicts(), n_watch=watch.height)  # fmt: skip


def for_report(con: duckdb.DuckDBPyConnection, warehouse: Path | None, predictions: Path,
               season: int) -> Section | None:  # fmt: skip
    """The section's data (None: no free-agent sync of the season, or none in a waiver period).
    ``con``: the league store opened read-only; the warehouse and the store are read-only."""
    rows = lv.read_live_owned(con, season)
    if rows.is_empty():
        return None
    path = lv.live_path(rows, _windows(warehouse, season))
    if path.is_empty():
        return None
    if Path(predictions).exists():
        lists = lv.live_lists(Path(predictions), season)
    else:
        lists = pl.DataFrame(schema={"season": pl.Int32, "week": pl.Int32, "position": pl.String,
                                     "gsis_id": pl.String, "rank": pl.Int32, "tier": pl.String,
                                     "kind": pl.String})  # fmt: skip
    ids = sorted(set(path.get_column("gsis_id").to_list()) | set(lists.get_column("gsis_id")))
    return build_section(path, lists, _names(warehouse, ids), season)


STATES = {"added": "added by the crowd", "already": "already over it at the first sync",
          "below": "still under it", "left_pool": "left your free agents while under it",
          "unseen": "listed by the Radar, never a free agent here"}  # fmt: skip


def _wk(x: object) -> str:
    return "-" if x is None else f"week {x}"


def _lead(x: object) -> str:
    return "-" if x is None else f"{int(x):+d}"  # type: ignore[call-overload]


def render_section(sec: Section) -> Any:
    """The HTML section (twm.league.report's helpers)."""
    from twm.league.report import para, section, table

    t = f"{sec.threshold:.0f}%"
    parts: list[object] = [
        f"How early the Radar flags the players most ESPN leagues end up rostering, live this "
        f"season (the study of past seasons is on the site's track record). The crowd: ESPN's "
        f"% rostered of your league's free agents at each sync; the crowd adds a player when "
        f"he goes from under {t} to {t} or more. Lead = the crossing's week minus the Radar's "
        "first flag (+ = the Radar first)."
    ]
    weeks = ", ".join(f"after week {k}" for k in sec.periods)
    parts.append(para(f"Syncs so far: {weeks}.", "small"))
    if len(sec.periods) < 2:
        parts.append(para("One waiver period observed so far: a lead needs a player seen under "
                          f"{t} and later at {t} or more, so none can be measured yet. ESPN "
                          "gives only the current %, so earlier weeks cannot be filled in.",
                          "muted"))  # fmt: skip
    rows = [[STATES[k], c["n"], c["flagged_listed"], c["flagged_must_add"],
             c["before_listed"] if k == "added" else "", c["before_must_add"] if k == "added"
             else ""] for k in STATES if (c := sec.counts.get(k))]  # fmt: skip
    head = ["players", "n", "Radar listed", "Radar must-add", "listed first", "must-add first"]
    parts.append(table(head, rows, {1, 2, 3, 4, 5}))
    if sec.added:
        parts.append(para(f"The crowd's adds this season ({len(sec.added)}):", "small"))
        rows = [[r["name"], r["position"] or "", f"{r['first_pct']:.0f}%",
                 f"after week {r['cross_period']}", _wk(r["listed"]), _lead(r["lead_listed"]),
                 _wk(r["must_add"]), _lead(r["lead_must_add"])] for r in sec.added]  # fmt: skip
        head = ["player", "pos", "first %", "crossed", "listed", "lead", "must-add", "lead"]
        parts.append(table(head, rows, {2, 5, 7}))
    if sec.watch:
        parts.append(
            para(
                f"The Radar's must-adds still under {t} at the latest sync "
                f"({sec.n_watch}; the {len(sec.watch)} most rostered):",
                "small",
            )
        )
        parts.append(table(["player", "pos", "latest %", "must-add since"],
                           [[r["name"], r["position"] or "", f"{r['last_pct']:.0f}%",
                             _wk(r["must_add"])] for r in sec.watch], {2}))  # fmt: skip
    gone = sec.counts.get("left_pool", {}).get("n", 0)
    who = "1 player was" if gone == 1 else f"{gone} players were"
    parts.append(para(f"{who} under {t} when last seen and missing from the "
                      "latest sync (maybe rostered in your league): a crossing after that is "
                      "invisible here. ESPN's numbers from your league's syncs; no FantasyPros "
                      "data. Local only, never published.", "muted small"))  # fmt: skip
    return section(SECTION_ID, TITLE, parts)
