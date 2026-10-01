"""`twm league radar`: the week's Waiver Radar and streamer lists restricted to the league's
real free agents, and the drop candidates on the owner's roster (PROJECT_SPEC 8.3 feature 1;
step F3). Reads only: data/league.duckdb (the newest synced week), the predictions store and
(for a season average) the warehouse.

- **The lists** are the stored lists of the approved models (``config/production_models.yaml``:
  ``waiver_radar`` for QB/RB/WR/TE, ``streamer_k`` / ``streamer_dst`` for K and D/ST), the
  Radar's week N being the list made after week N's games. Nothing is re-scored: the chances
  are the lists' own, calibrated on config/scoring.yaml (a line says so when the league scores
  differently, :func:`scoring_note`).
- **Free agents**: the newest week's ``league_free_agents`` (ESPN's top 50 per position, free
  agents and players on waivers), joined by gsis_id / ``DST-<team>``. A listed player who is a
  free agent keeps his list rank and chance; an ESPN free agent the list's pool does not hold
  is shown apart, "not in the pool", with no chance (never invented).
- **Drop candidates** (:mod:`twm.league.drops`, step F4): the owner's best lineup by
  rest-of-season numbers (the lineup optimizer); the lowest-numbered QB/RB/WR/TE non-starter is
  the candidate, a spare K or D/ST apart. An ESPN IR-slot player (``lineupSlotId`` 21) never is.

Output: NFL player names and numbers only (no fantasy team, owner or member id).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from twm.league import store
from twm.league.drops import DropView, drop_view

RADAR_POSITIONS = ("QB", "RB", "WR", "TE")
STREAM_POSITIONS = ("K", "DST")
POSITIONS = RADAR_POSITIONS + STREAM_POSITIONS
ESPN_POSITION = {"DST": "D/ST"}  # the app's position -> ESPN's
APP_POSITION = {"D/ST": "DST"}
PIN_KEYS = {"QB": "waiver_radar", "RB": "waiver_radar", "WR": "waiver_radar",
            "TE": "waiver_radar", "K": "streamer_k", "DST": "streamer_dst"}  # fmt: skip
PROJECTION_PIN = "regression_watch"


class PersonalUnavailableError(LookupError):
    """An input is missing (no sync, no pinned model, no stored list): one plain line."""


def pinned_versions(pins_path: Path | None = None) -> dict[str, tuple[int, str]]:
    """Pin key -> (season, model_version) of the approved models."""
    from twm.pins import read_pins

    return {k: (p.season, p.model_version) for k, p in read_pins(pins_path).items()}


LIST_SQL = """
    SELECT entity_id, rank_group AS position, score, rank, band, reasons_json, tier, kind,
           model_version, week, horizon, as_of
    FROM predictions WHERE season = ? AND model_version IN ({marks}) {week}
"""


def stored_rows(
    predictions: Path, season: int, versions: list[str], week: int | None = None
) -> pl.DataFrame:
    """The predictions store's rows of ``versions`` in ``season`` (one week, or every week),
    opened read-only."""
    if not predictions.exists():
        raise PersonalUnavailableError(
            f"My League: the predictions store is missing ({predictions.name}): run the weekly "
            "lists first (`uv run twm pipeline run`)"
        )
    con = duckdb.connect(str(predictions), read_only=True)
    try:
        sql = LIST_SQL.format(marks=", ".join("?" * len(versions)),
                              week="AND week = ?" if week is not None else "")  # fmt: skip
        args = [season, *versions] + ([week] if week is not None else [])
        return con.execute(sql, args).pl()
    finally:
        con.close()


@dataclass
class LeagueView:
    """The newest synced week: free agents, every roster, the owner's team, the slots."""

    league_id: int
    season: int
    week: int  # the ESPN week the free agents were read for
    free_agents: pl.DataFrame
    rosters: pl.DataFrame  # every team's roster (the newest synced week)
    my_team: int | None
    slots: dict[str, int] = field(default_factory=dict)  # ESPN slot label -> count


def _frame(con: duckdb.DuckDBPyConnection, sql: str, args: list[Any]) -> pl.DataFrame:
    df = con.execute(sql, args).pl()
    teams = [store.nfl_team(t) for t in df.get_column("pro_team").to_list()]  # 'None' -> None
    return df.with_columns(
        pl.col("position").replace(APP_POSITION).alias("position"),
        pl.Series("pro_team", teams, dtype=pl.String),
    )


def load_league(con: duckdb.DuckDBPyConnection) -> LeagueView:
    """The free agents and rosters of the newest synced week (:func:`store.current`: a later
    backfill of an older week never replaces them)."""
    fa = store.current(con, "league_free_agents")
    if fa is None:
        raise PersonalUnavailableError(
            "My League: nothing synced yet: run `uv run twm league sync` first."
        )
    ro = store.current(con, "league_rosters", fa.season)
    cols = "espn_id, player_name, position, pro_team, entity_id"
    agents = _frame(con, f"SELECT {cols}, percent_owned, projected_points FROM "
                    "league_free_agents WHERE league_id = ? AND season = ? AND week = ?",
                    [fa.league_id, fa.season, fa.week])  # fmt: skip
    rosters = _frame(con, f"SELECT {cols}, league_team_id, lineup_slot, injury_status FROM "
                     "league_rosters WHERE league_id = ? AND season = ? AND week = ?",
                     [fa.league_id, fa.season, ro.week if ro else -1])  # fmt: skip
    return LeagueView(
        league_id=fa.league_id, season=fa.season, week=fa.week, free_agents=agents,
        rosters=rosters, my_team=store.my_team(con, fa.league_id, fa.season),
        slots=store.slot_counts(store.settings_of(con, fa.league_id, fa.season)),
    )  # fmt: skip


def scoring_note(con: duckdb.DuckDBPyConnection) -> str | None:
    """One plain line when `twm league settings-diff` finds a scoring difference, else None."""
    from twm.config import scoring
    from twm.league.settings_diff import load, scoring_diffs

    synced = load(con)
    if synced is None:
        return None
    diffs, _ = scoring_diffs(synced.espn, scoring())
    if not diffs:
        return None
    return (f"Note: your league scores {len(diffs)} setting(s) differently from "
            "config/scoring.yaml (`uv run twm league settings-diff`); the chances below assume "
            "the config's scoring.")  # fmt: skip


def chance_text(position: str, band: str | None, score: float | None) -> str:
    """The list's own chance and range (its ``band``), as the Radar / streamer print it."""
    if not band:
        return "-" if score is None else f"model {100 * score:.0f}%"
    b = json.loads(band)
    if position in STREAM_POSITIONS:
        from twm.modules.streamer.confidence import band_text as stream_text

        return stream_text(b["chance"], b["lo"], b["hi"], position)
    from twm.modules.waiver_radar.confidence import band_text

    return band_text(b["chance"], b["lo"], b["hi"])


@dataclass
class PositionList:
    position: str
    picks: pl.DataFrame  # the list's free agents, list order (rank, name, team, chance, tier)
    outside: pl.DataFrame  # ESPN free agents the list's pool does not hold (no chance)
    free: int  # the list's players who are free agents in the sync
    unknown: int  # of the list's top ``limit``: neither a synced free agent nor on a roster


def position_list(view: LeagueView, rows: pl.DataFrame, position: str, limit: int) -> PositionList:
    """One position of the personalized list (``rows``: the pinned list of the week)."""
    mine = rows.filter(pl.col("position") == position).sort("rank")
    fa = view.free_agents.filter(pl.col("position") == position)
    fa_ids = set(fa.get_column("entity_id").drop_nulls().to_list())
    rostered = set(view.rosters.get_column("entity_id").drop_nulls().to_list())
    info = fa.select("entity_id", "player_name", "pro_team", "percent_owned").unique(
        "entity_id", keep="first", maintain_order=True)  # fmt: skip
    free = mine.filter(pl.col("entity_id").is_in(list(fa_ids))).join(info, on="entity_id")
    chances = [chance_text(position, b, s) for b, s in
               zip(free.get_column("band"), free.get_column("score"), strict=True)]  # fmt: skip
    free = free.with_columns(pl.Series("chance", chances, dtype=pl.String)).sort("rank")
    pool = set(mine.get_column("entity_id").to_list())
    outside = fa.filter(pl.col("entity_id").is_null() | ~pl.col("entity_id").is_in(list(pool)))
    outside = outside.sort(["percent_owned", "player_name"], descending=[True, False],
                           nulls_last=True)  # fmt: skip
    top = mine.head(limit).get_column("entity_id").to_list()
    unknown = sum(1 for e in top if e not in fa_ids and e not in rostered)
    return PositionList(position, free.head(limit), outside, free.height, unknown)


def _f(x: Any, fmt: str = ".2f") -> str:
    return "-" if x is None else format(float(x), fmt)


def projection_numbers(reasons_json: str | None) -> str:
    """Regression Watch's numbers behind a projection (its stored ``reasons_json``)."""
    r = json.loads(reasons_json or "{}")
    return (f"= xFP {_f(r.get('xfp_used'))} + FPOE {_f(r.get('fpoe_used'), '+.2f')} per game; "
            f"so far {_f(r.get('ppg'))} points per game over {r.get('games', '-')} games "
            f"(xFP {_f(r.get('xfp_pg'))}, FPOE {_f(r.get('fpoe_pg'), '+.2f')})")  # fmt: skip


@dataclass
class PersonalRadar:
    season: int
    sync_week: int  # the ESPN week of the free-agent sync
    week: int  # the lists' week N (made after week N's games)
    lists: list[PositionList]
    drops: DropView | None  # None: the owner's team was not identified
    team_found: bool
    notes: list[str] = field(default_factory=list)
    projections: pl.DataFrame = field(default_factory=pl.DataFrame)  # the week's RW rows
    roster: pl.DataFrame = field(default_factory=pl.DataFrame)  # the owner's roster rows
    as_of: datetime | None = None  # the lists' as-of (naive UTC)


def _pins(pins_path: Path | None, season: int) -> tuple[dict[str, str], list[str]]:
    """Pin key -> model_version of ``season``'s approved models, and notes for the missing."""
    pins, notes, out = pinned_versions(pins_path), [], {}
    for key in (*dict.fromkeys(PIN_KEYS.values()), PROJECTION_PIN):
        if key not in pins:
            notes.append(f"no approved {key} model (config/production_models.yaml)")
        elif pins[key][0] != season:
            notes.append(f"the approved {key} model is for {pins[key][0]}, not {season}")
        else:
            out[key] = pins[key][1]
    if "waiver_radar" not in out:
        raise PersonalUnavailableError(f"My League: {notes[0]}: no Radar list to personalize")
    return out, notes


def build(
    con: duckdb.DuckDBPyConnection,
    predictions: Path,
    *,
    week: int | None = None,
    limit: int = 10,
    pins_path: Path | None = None,
    warehouse: Path | None = None,
) -> PersonalRadar:
    """The personalized lists and drop candidates of the newest synced week (``week``: the lists'
    week, default the latest one stored at or before the sync's ESPN week)."""
    view = load_league(con)
    versions, notes = _pins(pins_path, view.season)
    rows = stored_rows(predictions, view.season, sorted(set(versions.values())))
    radar_v = versions["waiver_radar"]
    weeks = sorted(set(rows.filter(pl.col("model_version") == radar_v).get_column("week")))
    if week is None:
        before = [w for w in weeks if w <= view.week]
        if not before:
            raise PersonalUnavailableError(
                f"My League: no Radar list of the approved model is stored for {view.season} "
                f"up to week {view.week}: run the weekly lists first")  # fmt: skip
        week = before[-1]
    elif week not in weeks:
        stored = f" (stored: {', '.join(map(str, weeks))})" if weeks else ""
        raise PersonalUnavailableError(
            f"My League: no Radar list of the approved model is stored for {view.season} "
            f"week {week}{stored}")  # fmt: skip
    if view.week - week > 1:
        notes.append(f"the week {week} lists are older than the free agents (ESPN week "
                     f"{view.week}): a newer list is not stored yet")  # fmt: skip
    rows = rows.filter(pl.col("week") == week)
    lists = []
    for pos in POSITIONS:
        v = versions.get(PIN_KEYS[pos])
        pos_rows = rows.filter((pl.col("position") == pos) & (pl.col("model_version") == v))
        if v is not None and pos_rows.height == 0:
            notes.append(f"no stored {pos} list for week {week}")
        lists.append(position_list(view, pos_rows, pos, limit))
    stream = rows.filter(
        pl.col("position").is_in(STREAM_POSITIONS)
        & pl.col("model_version").is_in([versions.get(PIN_KEYS[p]) for p in STREAM_POSITIONS])
    )
    proj = rows.filter(pl.col("model_version") == versions.get(PROJECTION_PIN, ""))
    if PROJECTION_PIN in versions and proj.height == 0:
        notes.append(f"no stored Regression Watch projections for week {week}")
    mine, drops = view.rosters.clear(), None
    if view.my_team is not None:
        mine = view.rosters.filter(pl.col("league_team_id") == view.my_team)
        drops = drop_view(mine, view.slots, stream, proj, season=view.season, week=week,
                          warehouse=warehouse)  # fmt: skip
    as_of = rows.filter(pl.col("model_version") == radar_v).get_column("as_of").max()
    return PersonalRadar(view.season, view.week, week, lists, drops, view.my_team is not None,
                         notes, proj, mine, as_of)  # fmt: skip


OUTSIDE_SHOWN = 5  # "not in the pool" names printed per position


def _grid(rows: list[tuple[str, ...]]) -> list[str]:
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return ["    " + "  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)).rstrip()
            for r in rows]  # fmt: skip


def _owned(x: Any) -> str:
    return "-" if x is None else f"{float(x):.0f}%"


def list_lines(pl_: PositionList, limit: int) -> list[str]:
    what = "Radar" if pl_.position in RADAR_POSITIONS else "streamer"
    out = [f"{pl_.position}: {pl_.free} free agent(s) on the {what}'s list"
           + (f", the top {min(limit, pl_.free)}" if pl_.free else "")]  # fmt: skip
    if pl_.picks.height:
        head = ("rank", "player", "team", "chance", "priority", "ESPN owned")
        body = [(str(r["rank"]), str(r["player_name"]), str(r["pro_team"] or "-"), r["chance"],
                 str(r["tier"] or "-"), _owned(r["percent_owned"]))
                for r in pl_.picks.iter_rows(named=True)]  # fmt: skip
        out += _grid([head, *body])
    if pl_.outside.height:
        names = [f"{r['player_name']} ({r['pro_team'] or '-'}, {_owned(r['percent_owned'])})"
                 for r in pl_.outside.head(OUTSIDE_SHOWN).iter_rows(named=True)]  # fmt: skip
        more = pl_.outside.height - len(names)
        out.append(f"    not in the {what}'s pool (no chance): " + ", ".join(names)
                   + (f" and {more} more" if more > 0 else ""))  # fmt: skip
    if pl_.unknown:
        out.append(f"    {pl_.unknown} of the {what}'s top {limit} are neither among the synced "
                   "free agents (ESPN's top 50 per position) nor on a roster")  # fmt: skip
    return out


def drop_lines(res: PersonalRadar) -> list[str]:
    from twm.league.drops import drop_text

    return drop_text(res.drops)


def radar_text(res: PersonalRadar, scoring: str | None = None, limit: int = 10) -> list[str]:
    w = res.week
    lines = [f"My League, {res.season}: the week {w} lists (made after week {w}'s games) on "
             f"your league's free agents (sync of ESPN week {res.sync_week})"]  # fmt: skip
    if scoring:
        lines.append(scoring)
    for pl_ in res.lists:
        lines += ["", *list_lines(pl_, limit)]
    lines += ["", *drop_lines(res)]
    lines += [f"Note: {n}" for n in res.notes]
    return lines
