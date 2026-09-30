"""data/league.duckdb: the owner's ESPN league, local only (PROJECT_SPEC 8.3; step F2).

Never published: the publish package never imports twm.league (tests/test_publish.py), every
table carries ``league_id`` (refused by publish's forbidden-column check) and league-only
columns start with ``league_`` (listed in :data:`PRIVATE_COLUMNS`). No cookie is ever stored.

Every row has ``league_id``, ``season``, ``week`` (the week the sync read) and ``synced_at``
(UTC). Writes are idempotent: a snapshot table (settings, scoring, rosters, free agents, box
scores, matchups) replaces the rows of the partition it read, keyed tables (teams, activity,
the player map) insert-or-replace by primary key; one transaction per sync.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb
import polars as pl


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[tuple[str, str], ...]  # (name, DuckDB type)
    primary_key: tuple[str, ...]
    partition: tuple[str, ...] | None  # snapshot tables: rows replaced per partition


BASE = (("league_id", "BIGINT"), ("season", "INTEGER"), ("week", "INTEGER"),
        ("synced_at", "TIMESTAMP"))  # fmt: skip
PLAYER = (("espn_id", "BIGINT"), ("player_name", "VARCHAR"), ("position", "VARCHAR"),
          ("pro_team", "VARCHAR"))  # fmt: skip
JOIN = (("gsis_id", "VARCHAR"), ("entity_id", "VARCHAR"))  # filled by the ESPN -> gsis join
SEASON = ("league_id", "season")
WEEK = ("league_id", "season", "week")

TABLES: dict[str, Table] = {t.name: t for t in (
    Table("league_settings", BASE + (("key", "VARCHAR"), ("value", "VARCHAR")),
          SEASON + ("key",), SEASON),
    Table("league_scoring", BASE + (("stat_id", "INTEGER"), ("abbr", "VARCHAR"),
          ("label", "VARCHAR"), ("points", "DOUBLE"), ("dst_points", "DOUBLE")),
          SEASON + ("stat_id",), SEASON),
    Table("league_teams", BASE + (("league_team_id", "INTEGER"), ("league_team_abbrev", "VARCHAR"),
          ("league_team_name", "VARCHAR"), ("league_is_mine", "BOOLEAN")),
          SEASON + ("league_team_id",), None),
    Table("league_rosters", BASE + (("league_team_id", "INTEGER"),) + PLAYER
          + (("lineup_slot", "VARCHAR"), ("injury_status", "VARCHAR"),
             ("acquisition_type", "VARCHAR")) + JOIN,
          WEEK + ("league_team_id", "espn_id"), WEEK),
    Table("league_free_agents", BASE + PLAYER + (("percent_owned", "DOUBLE"), ("points", "DOUBLE"),
          ("projected_points", "DOUBLE"), ("on_bye", "BOOLEAN")) + JOIN,
          WEEK + ("espn_id",), WEEK),
    Table("league_matchups", BASE + (("matchup", "INTEGER"), ("league_team_id", "INTEGER"),
          ("league_opponent_id", "INTEGER"), ("is_home", "BOOLEAN"), ("is_playoff", "BOOLEAN"),
          ("score", "DOUBLE"), ("projected", "DOUBLE")),
          WEEK + ("league_team_id",), WEEK),
    Table("league_box_scores", BASE + (("league_team_id", "INTEGER"),) + PLAYER
          + (("lineup_slot", "VARCHAR"), ("is_starter", "BOOLEAN"), ("points", "DOUBLE"),
             ("projected_points", "DOUBLE"), ("on_bye", "BOOLEAN")) + JOIN,
          WEEK + ("league_team_id", "espn_id"), WEEK),
    Table("league_activity", BASE + (("activity_ms", "BIGINT"), ("activity_at", "TIMESTAMP"),
          ("seq", "INTEGER"), ("league_team_id", "INTEGER"), ("action", "VARCHAR")) + PLAYER
          + (("bid_amount", "DOUBLE"),) + JOIN,
          ("league_id", "activity_ms", "seq"), None),
    Table("league_player_map", BASE + PLAYER + JOIN + (("method", "VARCHAR"),),
          ("league_id", "espn_id"), None),
    Table("league_sync_runs", BASE + (("what", "VARCHAR"), ("n_rows", "INTEGER"),
          ("unmatched", "INTEGER"), ("note", "VARCHAR")), ("league_id", "synced_at", "what"), None),
)}  # fmt: skip
# Columns only league tables have: publish's FORBIDDEN_COLUMNS must list them all (checked by
# tests/test_league.py; espn_id and league_id are there since E2).
PRIVATE_COLUMNS = frozenset(
    c for t in TABLES.values() for c, _ in t.columns if c.startswith("league_")
) | {"espn_id"}


def default_path() -> Path:
    """settings.yaml paths.league_db (data/league.duckdb; a link made by local_storage.sh)."""
    from twm.config import ROOT, settings

    return ROOT / settings().paths.league_db


def _ddl(t: Table) -> str:
    cols = ", ".join(f"{c} {typ}" for c, typ in t.columns)
    return f"CREATE TABLE IF NOT EXISTS {t.name} ({cols}, PRIMARY KEY ({', '.join(t.primary_key)}))"


def connect(path: Path, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open the league store (created with every table on first write)."""
    if read_only:
        return duckdb.connect(str(path), read_only=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    for t in TABLES.values():
        con.execute(_ddl(t))
    return con


POLARS_TYPES = {
    "BIGINT": pl.Int64,
    "INTEGER": pl.Int32,
    "DOUBLE": pl.Float64,
    "VARCHAR": pl.String,
    "BOOLEAN": pl.Boolean,
    "TIMESTAMP": pl.Datetime("us"),
}


def frame(table: str, rows: list[dict], **base: object) -> pl.DataFrame:
    """``rows`` (dicts) plus the base columns as a frame typed like the table."""
    schema = {c: POLARS_TYPES[typ] for c, typ in TABLES[table].columns}
    data = [{**base, **r} for r in rows]
    missing = sorted({c for r in data for c in schema if c not in r})
    if missing:
        raise ValueError(f"{table}: rows lack {missing}")
    return pl.DataFrame(data, schema=schema, orient="row")


def write(con: duckdb.DuckDBPyConnection, table: str, df: pl.DataFrame, **where: object) -> int:
    """Replace the partition ``where`` (snapshot tables) or insert-or-replace by primary key.
    Runs inside the caller's transaction; returns the rows written."""
    t = TABLES[table]
    cols = [c for c, _ in t.columns]
    if t.partition is not None:
        if set(where) != set(t.partition):
            raise ValueError(f"{table}: a snapshot write needs {t.partition}, got {sorted(where)}")
        cond = " AND ".join(f"{k} = ?" for k in t.partition)
        con.execute(f"DELETE FROM {table} WHERE {cond}", [where[k] for k in t.partition])
    if df.height == 0:
        return 0
    con.register("_league_rows", df.select(cols).to_arrow())
    try:
        verb = "INSERT" if t.partition is not None else "INSERT OR REPLACE"
        con.execute(f"{verb} INTO {table} ({', '.join(cols)}) SELECT {', '.join(cols)} "
                    "FROM _league_rows")  # fmt: skip
    finally:
        con.unregister("_league_rows")
    return df.height


def counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    return {n: int(con.execute(f"SELECT count(*) FROM {n}").fetchone()[0]) for n in TABLES}


# ---- the ESPN -> gsis join (spec 4.3: ESPN player id -> espn_id -> gsis_id) -----------------

# ESPN's team codes (espn_api football/constant.py:46 PRO_TEAM_MAP) that differ from the
# warehouse's dim_team.current_abbr (checked 2026-09-30); 'None' = no team.
ESPN_TEAM_ALIASES = {"WSH": "WAS", "LAR": "LA"}
DST_PREFIX = "DST-"  # the streamer's D/ST entity id (twm.modules.streamer.pool.DST_PREFIX)


def nfl_team(espn_abbrev: str | None) -> str | None:
    t = (espn_abbrev or "").strip().upper()
    return None if t in ("", "NONE", "FA") else ESPN_TEAM_ALIASES.get(t, t)


def espn_bridge(warehouse: Path | None) -> dict[str, str] | None:
    """ESPN id (canonical text) -> gsis_id from the warehouse's bridge_player_id, opened read
    only; None when the warehouse is missing or busy (a build holds its write lock)."""
    if warehouse is None or not warehouse.exists():
        return None
    try:
        con = duckdb.connect(str(warehouse), read_only=True)
    except duckdb.Error:
        return None
    try:
        rows = con.execute(
            "SELECT source_id, gsis_id FROM bridge_player_id WHERE id_type = 'espn'"
        ).fetchall()
    except duckdb.Error:
        return None
    finally:
        con.close()
    return {str(s): str(g) for s, g in rows if s is not None and g is not None}


def player_map(players: list[dict], bridge: dict[str, str] | None) -> list[dict]:
    """One row per ESPN player seen (espn_id, player_name, position, pro_team) with gsis_id,
    entity_id (gsis_id, or DST-<team> for a D/ST) and method: bridge, dst_team, unmatched."""
    seen: dict[int, dict] = {}
    for p in players:
        seen[int(p["espn_id"])] = p  # the last sighting wins (roster data is newest)
    out = []
    for eid, p in sorted(seen.items()):
        base = {k: p.get(k) for k in ("espn_id", "player_name", "position", "pro_team")}
        if p.get("position") == "D/ST" or eid < 0:
            team = nfl_team(p.get("pro_team"))
            ent = f"{DST_PREFIX}{team}" if team else None
            out.append({**base, "gsis_id": None, "entity_id": ent,
                        "method": "dst_team" if ent else "unmatched"})  # fmt: skip
            continue
        g = (bridge or {}).get(str(eid))
        out.append({**base, "gsis_id": g, "entity_id": g, "method": "bridge" if g else "unmatched"})
    return out
