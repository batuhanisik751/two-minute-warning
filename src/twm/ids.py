"""Player IDs (Step B3): one canonical key, a bridge from every other id system, and a report.

Why several id systems? Every data provider numbers players its own way: nflverse's play-by-play
and stats use the NFL's GSIS id (``00-0033873``), Pro-Football-Reference snap counts use a PFR
slug (``MahoPa00``), ESPN depth charts an ESPN number, FantasyPros rankings a FantasyPros
number, and so on. The warehouse keeps ``gsis_id`` as the one canonical key (spec 7.5) and
stores a *bridge*, ``bridge_player_id``: one row per (``id_type``, ``source_id``) naming the
``gsis_id`` it belongs to, so any source can be joined to every other one.

Where the links come from, in order of trust (``METHODS``, the ``method`` column):

1. ``manual``: ``data/manual/player_id_overrides.csv``, checked by a person (highest precedence);
2. ``players``: nflverse's player table (``load_players``), one row per gsis_id, its ids unique;
3. ``rosters_weekly``: the weekly rosters of the built seasons (2002+);
4. ``ff_playerids``: DynastyProcess's cross-site id table, the only source of Sleeper,
   FantasyPros, Yahoo, Sportradar and MFL ids (it has a few known errors, so it comes last);
5. name passes, only for ``ff_playerids`` rows WITHOUT a gsis_id: ``name_birthdate`` (normalized
   name + exact birth date) and then ``name_position_draft`` (normalized name + position group +
   draft year), each only when the key is unique on both sides.

Safety rules: when sources disagree about an id the most trusted source wins (``is_conflict``);
when the winning source itself maps one id to two players the id is AMBIGUOUS and left out (it
is listed in the report instead: a wrong link is worse than no link); a name link never
overrides an id link and never gives a player a second id of a type he already has; links to a
gsis_id that is not in ``dim_player`` are dropped (counted in the manifest).

Precedence is a rule, not a check, so the build also checks the links against how a dataset
uses the ids (:func:`stage_usage_checks`, today the PFR ids of the snap counts): a season
outside the linked player's career re-links the id (when the other sources agree on one player
whose career fits) or leaves those rows without a gsis_id; a position on the other side of the
ball is only reported. Both are ``suspect`` rows in the report.

The unmatched-id report (spec 7.5) is rebuilt on every build as two warehouse tables of kind
"meta": ``report_id_coverage`` (match rates per dataset, season, id type and scope) and
``report_id_unmatched`` (every unmatched id with a name, plus the suspect links, ambiguous ids,
conflicts, name-pass links and players with several ids of one type). ``twm ids`` renders them
as markdown.

This module holds the id logic as SQL/polars helpers that run inside the build's connection;
``twm.warehouse.build`` materializes the tables.
"""

from __future__ import annotations

import csv
import math
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from twm.warehouse import schema as sc

# --------------------------------------------------------------------------------------
# Id systems and sources
# --------------------------------------------------------------------------------------

# Precedence order: a lower index wins a disagreement.
SOURCES = ("manual", "players", "rosters_weekly", "ff_playerids")
NAME_METHODS = ("name_birthdate", "name_position_draft")
METHODS = SOURCES + NAME_METHODS
METHOD_RANK = {m: i for i, m in enumerate(METHODS)}

# id_type -> {source dataset: column}. Column names come from data/schemas/*.json (checked at
# import). ``nfl`` is the NFL's numeric id: players.nfl_id and rosters_weekly.gsis_it_id agree
# on it; ff_playerids.nfl_id mixes it with an older NFL.com numbering (so one player can carry
# two nfl ids). ff_playerids.stats_global_id is left out: 0 is used as a placeholder in
# thousands of rows.
ID_TYPES: dict[str, dict[str, str]] = {
    "pfr": {"players": "pfr_id", "rosters_weekly": "pfr_id", "ff_playerids": "pfr_id"},
    "espn": {"players": "espn_id", "rosters_weekly": "espn_id", "ff_playerids": "espn_id"},
    "sleeper": {"rosters_weekly": "sleeper_id", "ff_playerids": "sleeper_id"},
    "fantasypros": {"ff_playerids": "fantasypros_id"},
    "yahoo": {"rosters_weekly": "yahoo_id", "ff_playerids": "yahoo_id"},
    "sportradar": {"rosters_weekly": "sportradar_id", "ff_playerids": "sportradar_id"},
    "pff": {"players": "pff_id", "rosters_weekly": "pff_id", "ff_playerids": "pff_id"},
    "nfl": {"players": "nfl_id", "rosters_weekly": "gsis_it_id", "ff_playerids": "nfl_id"},
    "mfl": {"ff_playerids": "mfl_id"},
    "esb": {"players": "esb_id", "rosters_weekly": "esb_id"},
    "smart": {"players": "smart_id", "rosters_weekly": "smart_id"},
    "otc": {"players": "otc_id"},
    "rotowire": {"rosters_weekly": "rotowire_id", "ff_playerids": "rotowire_id"},
    "fantasy_data": {"rosters_weekly": "fantasy_data_id", "ff_playerids": "fantasy_data_id"},
    "cbs": {"ff_playerids": "cbs_id"},
    "stats": {"ff_playerids": "stats_id"},
    "fleaflicker": {"ff_playerids": "fleaflicker_id"},
    "cfbref": {"ff_playerids": "cfbref_id"},
    "rotoworld": {"ff_playerids": "rotoworld_id"},
    "ktc": {"ff_playerids": "ktc_id"},
    "swish": {"ff_playerids": "swish_id"},
}
GSIS = "gsis"  # the canonical id's own id_type label in the report

# dim_player carries one id of each of these types: pfr/espn (were already there) and the
# schema's DIM_PLAYER_ID_COLUMNS (sleeper_id -> sleeper, ...).
DIM_PLAYER_ID_TYPES = ("pfr", "espn") + tuple(
    c.removesuffix("_id") for c in sc.DIM_PLAYER_ID_COLUMNS
)

FANTASY_POSITIONS = ("QB", "RB", "WR", "TE")
OVERRIDE_COLUMNS = ("id_type", "source_id", "gsis_id", "note", "verified_by")
OVERRIDES_FILE = "player_id_overrides.csv"

# Columns the name passes read (checked against the snapshots at import).
PLAYER_COLUMNS = ("gsis_id", "display_name", "birth_date", "position", "position_group",
                  "draft_year")  # fmt: skip
# The career window of the usage check (read when the players file has them).
CAREER_COLUMNS = ("rookie_season", "last_season")
FF_NAME_COLUMNS = ("gsis_id", "name", "birthdate", "position", "draft_year")

# ff_playerids position -> players.position_group (nflverse's groups).
FF_POSITION_GROUP = {
    "QB": "QB", "RB": "RB", "FB": "RB", "WR": "WR", "TE": "TE",
    "OT": "OL", "T": "OL", "G": "OL", "C": "OL", "OL": "OL",
    "DE": "DL", "DT": "DL", "DL": "DL", "NT": "DL",
    "LB": "LB", "ILB": "LB", "OLB": "LB", "MLB": "LB",
    "CB": "DB", "S": "DB", "DB": "DB", "FS": "DB", "SS": "DB", "SAF": "DB",
    "PK": "SPEC", "K": "SPEC", "PN": "SPEC", "P": "SPEC", "LS": "SPEC",
}  # fmt: skip
# Any source's position -> position group (FF_POSITION_GROUP plus snap-count spellings; a
# compound snap position such as "DE/LB" is read by its first part), and group -> side.
POSITION_GROUP = {**FF_POSITION_GROUP, "HB": "RB", "OG": "OL"}
GROUP_SIDE = {
    "QB": "offense", "RB": "offense", "WR": "offense", "TE": "offense", "OL": "offense",
    "DL": "defense", "LB": "defense", "DB": "defense", "SPEC": "special",
}  # fmt: skip


class IdOverrideError(ValueError):
    """``data/manual/player_id_overrides.csv`` is invalid (unknown id_type, unknown gsis_id,
    a duplicate key, a missing value): the build stops and names the rows."""


def _check_source_columns() -> None:
    assert GSIS not in ID_TYPES, "'gsis' is the canonical key, not a bridge id_type"
    assert set(DIM_PLAYER_ID_TYPES) <= set(ID_TYPES), DIM_PLAYER_ID_TYPES
    for cols in ID_TYPES.values():
        for source, col in cols.items():
            sc._check_names(source, [col])
    sc._check_names("players", list(PLAYER_COLUMNS) + list(CAREER_COLUMNS))
    sc._check_names("ff_playerids", list(FF_NAME_COLUMNS))
    sc._check_names("rosters_weekly", ["season", "gsis_id"])


_check_source_columns()


# --------------------------------------------------------------------------------------
# Canonical id strings and normalized names
# --------------------------------------------------------------------------------------

_INTEGRAL_TEXT = re.compile(r"(-?[0-9]+)\.0+")
# Whitespace trimmed from ids, the same set in Python and in SQL (DuckDB's trim() only removes
# spaces; Python's str.strip() also removes other Unicode spaces).
_ID_SPACE = " \t\n\r\f\v"
_ID_SPACE_RE = "[ \\t\\n\\r\\f\\v]"
SQL_INTEGER_TYPES = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT",
                     "USMALLINT", "UINTEGER", "UBIGINT", "UHUGEINT")  # fmt: skip
SQL_FLOAT_TYPES = ("FLOAT", "DOUBLE", "REAL")


def canonical_id(value: Any) -> str | None:
    """Any source id as its canonical text: integers without a decimal part (4374496 and
    4374496.0 and "4374496.0" all give "4374496"), text trimmed, empty -> None.

    Ids are identifiers, never numbers: "0012" keeps its zeros and "MahoPa00" its case. A float
    that is not integral (never seen in an id column) keeps its shortest text ("1.5")."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError("a boolean is not an id")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return str(int(value)) if value.is_integer() else repr(value)
    text = str(value).strip(_ID_SPACE)
    if not text:
        return None
    m = _INTEGRAL_TEXT.fullmatch(text)
    return m.group(1) if m else text


def canonical_id_sql(expr: str, source_type: str) -> str:
    """:func:`canonical_id` as a DuckDB expression over a column of ``source_type``."""
    t = source_type.upper()
    if t in SQL_INTEGER_TYPES:
        return f"CAST({expr} AS VARCHAR)"
    if t in SQL_FLOAT_TYPES:
        return (
            f"(CASE WHEN isnan({expr}) OR isinf({expr}) THEN NULL "
            f"WHEN {expr} = floor({expr}) THEN CAST(CAST({expr} AS HUGEINT) AS VARCHAR) "
            f"ELSE CAST({expr} AS VARCHAR) END)"
        )
    trimmed = (
        f"regexp_replace(CAST({expr} AS VARCHAR), '^{_ID_SPACE_RE}+|{_ID_SPACE_RE}+$', '', 'g')"
    )
    return f"NULLIF(regexp_replace({trimmed}, '^(-?[0-9]+)\\.0+$', '\\1'), '')"


NAME_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})
_NOT_NAME_CHAR = re.compile(r"[^a-z0-9\s]")


def normalize_name(name: str | None) -> str | None:
    """A player name reduced for matching: lowercase, accents removed, punctuation dropped
    (apostrophes, periods and hyphens join the parts: "D'Andre" -> "dandre", "A.J." -> "aj",
    "Amon-Ra" -> "amonra"), the suffixes jr/sr/ii/iii/iv/v dropped (never the first word),
    spaces collapsed. None or an empty result -> None."""
    if name is None:
        return None
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = _NOT_NAME_CHAR.sub("", text)
    words = text.split()
    words = [w for i, w in enumerate(words) if i == 0 or w not in NAME_SUFFIXES]
    return " ".join(words) or None


# --------------------------------------------------------------------------------------
# Manual overrides
# --------------------------------------------------------------------------------------


def overrides_path() -> Path:
    """``data/manual/player_id_overrides.csv`` (config ``paths.manual``)."""
    from twm.config import settings

    return settings().path("manual") / OVERRIDES_FILE


def read_overrides(path: Path, known_gsis: Iterable[str]) -> list[dict[str, str | None]]:
    """The validated override rows (id_type, source_id, gsis_id, note, verified_by).

    A missing file means no overrides. Raises :class:`IdOverrideError` naming every bad row:
    wrong header, unknown id_type, empty source_id/gsis_id, a gsis_id that is not in
    dim_player, or the same (id_type, source_id) twice."""
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = tuple(reader.fieldnames or ())
        if header != OVERRIDE_COLUMNS:
            raise IdOverrideError(
                f"{path}: the header must be {','.join(OVERRIDE_COLUMNS)} (got {','.join(header)})"
            )
        raw = [
            (i + 2, row)
            for i, row in enumerate(reader)
            if any((v or "").strip() for v in row.values())
        ]
    known = set(known_gsis)
    rows: list[dict[str, str | None]] = []
    problems: list[str] = []
    seen: dict[tuple[str, str], int] = {}
    for line, row in raw:
        id_type = (row.get("id_type") or "").strip()
        source_id = canonical_id(row.get("source_id"))
        gsis_id = canonical_id(row.get("gsis_id"))
        if id_type not in ID_TYPES:
            problems.append(f"line {line}: unknown id_type {id_type!r} (known: "
                            f"{', '.join(ID_TYPES)})")  # fmt: skip
            continue
        if source_id is None or gsis_id is None:
            problems.append(f"line {line}: source_id and gsis_id are required")
            continue
        if gsis_id not in known:
            problems.append(f"line {line}: gsis_id {gsis_id!r} is not in dim_player")
            continue
        key = (id_type, source_id)
        if key in seen:
            problems.append(f"line {line}: ({id_type}, {source_id}) repeats line {seen[key]}")
            continue
        seen[key] = line
        rows.append(
            {"id_type": id_type, "source_id": source_id, "gsis_id": gsis_id,
             "note": (row.get("note") or "").strip() or None,
             "verified_by": (row.get("verified_by") or "").strip() or None}
        )  # fmt: skip
    if problems:
        raise IdOverrideError(f"{path}: {len(problems)} invalid row(s): " + "; ".join(problems))
    return rows


# --------------------------------------------------------------------------------------
# Name passes (pure polars, tested on their own)
# --------------------------------------------------------------------------------------


def _unique_keys(df: pl.DataFrame, key: list[str]) -> pl.DataFrame:
    """Rows whose key (all non-null) occurs exactly once."""
    df = df.drop_nulls(key)
    return df.filter(pl.len().over(key) == 1)


def name_pass_links(ff: pl.DataFrame, players: pl.DataFrame) -> pl.DataFrame:
    """Link ff_playerids rows that have no gsis_id to players by name.

    ``ff``: ``rid, name, birthdate (Date), position, draft_year``;
    ``players``: ``gsis_id, display_name, birth_date (Date), position_group, draft_year``.
    Returns ``rid, gsis_id, method`` (sorted). Pass A (``name_birthdate``): normalized name and
    exact birth date, the key unique among the ff rows and among the players. Pass B
    (``name_position_draft``, for rows pass A did not link): normalized name, position group
    and draft year, unique on both sides, and the birth dates must not contradict.

    Pass B leaves out only the ff rows (``rid``) pass A linked, not their players: one player
    can be linked by both passes (an ff row with his birth date but no draft year, and another
    with his draft year but no birth date). The ``ambiguous`` rule of the build
    (``_stage_name_links``: one id named for two players, or one player named for two ids of a
    type) is what cancels such a pair."""
    empty = pl.DataFrame(schema={"rid": pl.Int64, "gsis_id": pl.String, "method": pl.String})
    if ff.is_empty() or players.is_empty():
        return empty
    norm = pl.col("name").map_elements(normalize_name, return_dtype=pl.String)
    f = ff.with_columns(
        norm.alias("norm"),
        pl.col("position").replace_strict(FF_POSITION_GROUP, default=None).alias("pos_group"),
        pl.col("draft_year").cast(pl.Int64),
    )
    p = players.with_columns(
        pl.col("display_name").map_elements(normalize_name, return_dtype=pl.String).alias("norm"),
        pl.col("draft_year").cast(pl.Int64),
    ).rename({"birth_date": "p_birth", "position_group": "pos_group"})

    a_key = ["norm", "birthdate"]
    fa = _unique_keys(f.select("rid", "norm", "birthdate"), a_key)
    pa = _unique_keys(p.select("gsis_id", "norm", pl.col("p_birth").alias("birthdate")), a_key)
    pass_a = fa.join(pa, on=a_key).select(
        "rid", "gsis_id", pl.lit("name_birthdate").alias("method")
    )

    b_key = ["norm", "pos_group", "draft_year"]
    fb = _unique_keys(f.select("rid", "birthdate", *b_key), b_key)
    pb = _unique_keys(p.select("gsis_id", "p_birth", *b_key), b_key)
    pass_b = (
        fb.join(pb, on=b_key)
        .filter(~pl.col("rid").is_in(pass_a["rid"].implode()))
        .filter(
            pl.col("birthdate").is_null()
            | pl.col("p_birth").is_null()
            | (pl.col("birthdate") == pl.col("p_birth"))
        )
        .select("rid", "gsis_id", pl.lit("name_position_draft").alias("method"))
    )
    out = pl.concat([pass_a, pass_b]).with_columns(pl.col("rid").cast(pl.Int64))
    return out.sort("rid", "gsis_id") if not out.is_empty() else empty


# --------------------------------------------------------------------------------------
# The bridge (runs inside the build connection)
# --------------------------------------------------------------------------------------


def _method_rank_sql(col: str) -> str:
    whens = " ".join(f"WHEN '{m}' THEN {r}" for m, r in METHOD_RANK.items())
    return f"(CASE {col} {whens} END)"


def _candidate_selects(source: str, view: str, types: Mapping[str, str]) -> list[str]:
    """One SELECT per id type the source carries: (id_type, source_id, gsis_id, method,
    season_min, season_max), distinct."""
    gsis = canonical_id_sql(sc.q("gsis_id"), types["gsis_id"])
    seasons = "min(season), max(season)" if "season" in types else "NULL, NULL"
    out = []
    for id_type, cols in ID_TYPES.items():
        col = cols.get(source)
        if col is None or col not in types:
            continue
        sid = canonical_id_sql(sc.q(col), types[col])
        out.append(
            f"SELECT '{id_type}' AS id_type, {sid} AS source_id, {gsis} AS gsis_id, "
            f"'{source}' AS method, {seasons} FROM {view} GROUP BY 1, 2, 3, 4"
        )
    return out


@dataclass(frozen=True)
class BridgeInputs:
    """The source views the build opened: view name and column -> DuckDB type (None when the
    dataset has no file for the built seasons, e.g. rosters_weekly before 2002)."""

    players: tuple[str, Mapping[str, str]]
    ff_playerids: tuple[str, Mapping[str, str]] | None
    rosters_weekly: tuple[str, Mapping[str, str]] | None
    overrides: Sequence[Mapping[str, str | None]] = ()


def stage_players(con: duckdb.DuckDBPyConnection, view: str, types: Mapping[str, str]) -> None:
    """``_players``: the player table's names, birth dates, draft years and career seasons
    (canonical gsis), and ``_pos_group`` (position -> group -> side, :data:`POSITION_GROUP`)."""
    gsis = canonical_id_sql(sc.q("gsis_id"), types["gsis_id"])
    career = ", ".join(
        f"any_value(CAST({sc.q(c)} AS INTEGER)) AS {c}"
        if c in types
        else f"CAST(NULL AS INTEGER) AS {c}"
        for c in CAREER_COLUMNS
    )
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _players AS
        SELECT {gsis} AS gsis_id, any_value(display_name) AS display_name,
               any_value(TRY_CAST(NULLIF(CAST(birth_date AS VARCHAR), '') AS DATE)) AS birth_date,
               any_value(position) AS position, any_value(position_group) AS position_group,
               any_value(CAST(draft_year AS BIGINT)) AS draft_year, {career}
        FROM {view} WHERE {gsis} IS NOT NULL GROUP BY 1""")
    values = ", ".join(
        f"({sc.sql_str(p)}, {sc.sql_str(g)}, {sc.sql_str(GROUP_SIDE[g])})"
        for p, g in sorted(POSITION_GROUP.items())
    )
    con.execute(
        "CREATE OR REPLACE TEMP TABLE _pos_group AS SELECT * FROM "
        f"(VALUES {values}) t(pos, grp, side)"
    )


def stage_bridge(con: duckdb.DuckDBPyConnection, inputs: BridgeInputs) -> dict[str, Any]:
    """Create the temp tables ``_bridge`` (the links, unique on id_type + source_id, with
    ``method_rank``) and ``_bridge_issues`` (report rows: ambiguous, conflict, name_link).
    ``_players`` must exist (:func:`stage_players`). Returns manifest notes."""
    notes: dict[str, Any] = {}
    selects = []
    pview, ptypes = inputs.players
    selects += _candidate_selects("players", pview, ptypes)
    if inputs.rosters_weekly is not None:
        selects += _candidate_selects("rosters_weekly", *inputs.rosters_weekly)
    if inputs.ff_playerids is not None:
        selects += _candidate_selects("ff_playerids", *inputs.ff_playerids)
    manual = pl.DataFrame(
        [{k: r.get(k) for k in ("id_type", "source_id", "gsis_id")} for r in inputs.overrides],
        schema={"id_type": pl.String, "source_id": pl.String, "gsis_id": pl.String},
    )
    con.register("df_id_overrides", manual)
    selects.append(
        "SELECT id_type, source_id, gsis_id, 'manual' AS method, NULL, NULL FROM df_id_overrides"
    )
    union = " UNION ALL ".join(f"({s})" for s in selects)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _idc_all AS
        SELECT id_type, source_id, gsis_id, method, {_method_rank_sql("method")} AS method_rank,
               CAST(season_min AS INTEGER) AS season_min, CAST(season_max AS INTEGER) AS season_max
        FROM (SELECT * FROM ({union}) t(id_type, source_id, gsis_id, method, season_min,
                                        season_max))
        WHERE source_id IS NOT NULL AND gsis_id IS NOT NULL""")
    con.unregister("df_id_overrides")
    unknown = con.execute(
        "SELECT method, count(*) FROM _idc_all WHERE gsis_id NOT IN (SELECT gsis_id FROM "
        "_players) GROUP BY 1 ORDER BY 1"
    ).fetchall()
    notes["n_links_to_gsis_not_in_dim_player"] = {m: n for m, n in unknown}
    con.execute(
        "CREATE OR REPLACE TEMP TABLE _idc_unknown AS SELECT * FROM _idc_all "
        "WHERE gsis_id NOT IN (SELECT gsis_id FROM _players)"
    )
    con.execute(
        "CREATE OR REPLACE TEMP TABLE _idc AS SELECT * FROM _idc_all "
        "WHERE gsis_id IN (SELECT gsis_id FROM _players)"
    )
    # Resolve every (id_type, source_id): the best-ranked source wins; a winner that names two
    # players is ambiguous.
    # n_candidates counts every proposed player, also those missing from dim_player, so a
    # disagreement with such a candidate still shows as a conflict.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE _idr AS
        WITH per_id AS (
            SELECT c.id_type, c.source_id, min(c.method_rank) AS best_rank,
                   any_value(a.n_candidates) AS n_candidates
            FROM _idc c JOIN (
                SELECT id_type, source_id, count(DISTINCT gsis_id) AS n_candidates
                FROM _idc_all GROUP BY 1, 2) a
              ON a.id_type = c.id_type AND a.source_id = c.source_id
            GROUP BY 1, 2
        )
        SELECT c.id_type, c.source_id, min(c.method) AS method, min(c.method_rank) AS method_rank,
               count(DISTINCT c.gsis_id) AS n_win, min(c.gsis_id) AS gsis_id,
               any_value(p.n_candidates) AS n_candidates
        FROM _idc c JOIN per_id p
          ON p.id_type = c.id_type AND p.source_id = c.source_id AND c.method_rank = p.best_rank
        GROUP BY 1, 2""")
    name_notes = _stage_name_links(con, inputs.ff_playerids)
    notes.update(name_notes)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE _bridge AS
        SELECT id_type, source_id, gsis_id, method, method_rank,
               CAST(n_candidates AS INTEGER) AS n_candidates, n_candidates > 1 AS is_conflict
        FROM _idr WHERE n_win = 1
        UNION ALL
        SELECT id_type, source_id, gsis_id, method, method_rank, 1, FALSE
        FROM _namec WHERE accepted""")
    _stage_bridge_issues(con)
    # rows of a dataset whose link the usage check found implausible (stage_usage_checks)
    con.execute(
        "CREATE OR REPLACE TEMP TABLE _usage_null "
        "(dataset VARCHAR, id_type VARCHAR, source_id VARCHAR, season INTEGER)"
    )
    notes.update(bridge_notes(con))  # refreshed after the usage checks (build)
    notes["n_ambiguous"] = con.execute(
        "SELECT count(*) FROM _bridge_issues WHERE kind = 'ambiguous'"
    ).fetchone()[0]
    notes["n_manual_overrides"] = len(inputs.overrides)
    return notes


def _stage_name_links(
    con: duckdb.DuckDBPyConnection, ff: tuple[str, Mapping[str, str]] | None
) -> dict[str, Any]:
    """``_namec``: name-pass proposals (one row per id of a linked ff row) with ``accepted``
    and the reason when not."""
    cols = "rid BIGINT, id_type VARCHAR, source_id VARCHAR, gsis_id VARCHAR, method VARCHAR, "
    cols += "method_rank INTEGER, ff_name VARCHAR, ff_birthdate DATE, ff_draft_year BIGINT, "
    cols += "ff_position VARCHAR, "
    cols += "accepted BOOLEAN, reason VARCHAR"
    con.execute(f"CREATE OR REPLACE TEMP TABLE _namec ({cols})")
    if ff is None:
        return {"name_pass_rows_linked": dict.fromkeys(NAME_METHODS, 0)}
    view, types = ff
    gsis = canonical_id_sql(sc.q("gsis_id"), types["gsis_id"])
    id_cols = {t: c["ff_playerids"] for t, c in ID_TYPES.items() if "ff_playerids" in c}
    id_cols = {t: c for t, c in id_cols.items() if c in types}
    canon = ", ".join(
        f"{canonical_id_sql(sc.q(c), types[c])} AS {sc.q('id_' + t)}" for t, c in id_cols.items()
    )
    order = ", ".join(sc.q("id_" + t) for t in id_cols)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _ff_nogsis AS
        SELECT row_number() OVER (ORDER BY {order}, name, birthdate, position, draft_year) AS rid,
               *
        FROM (SELECT {canon}, name,
                     TRY_CAST(NULLIF(CAST(birthdate AS VARCHAR), '') AS DATE) AS birthdate,
                     position, CAST(draft_year AS BIGINT) AS draft_year
              FROM {view} WHERE {gsis} IS NULL)""")
    ff_rows = con.execute(
        "SELECT rid, name, birthdate, position, draft_year FROM _ff_nogsis ORDER BY rid"
    ).pl()
    players = con.execute(
        "SELECT gsis_id, display_name, birth_date, position_group, draft_year FROM _players "
        "ORDER BY gsis_id"
    ).pl()
    links = name_pass_links(ff_rows, players)
    con.register("df_name_links", links)
    unpivot = " UNION ALL ".join(
        f"SELECT n.rid, '{t}' AS id_type, f.{sc.q('id_' + t)} AS source_id FROM df_name_links n "
        f"JOIN _ff_nogsis f ON f.rid = n.rid"
        for t in id_cols
    )
    con.execute(f"""
        INSERT INTO _namec
        SELECT u.rid, u.id_type, u.source_id, n.gsis_id, n.method, {_method_rank_sql("n.method")},
               f.name, f.birthdate, f.draft_year, f.position, NULL, NULL
        FROM ({unpivot}) u JOIN df_name_links n ON n.rid = u.rid JOIN _ff_nogsis f ON f.rid = u.rid
        WHERE u.source_id IS NOT NULL""")
    con.unregister("df_name_links")
    # Safety rules, in order: a row whose own ids are linked (by id) to another player is not
    # that player; an id link always wins; a player never gets a second id of a type he already
    # carries; two name links for one id (or one player) cancel each other. The last rule also
    # stops one player getting two ids of a type from two ff rows, one linked by pass A and
    # the other by pass B (name_pass_links drops only the rows pass A linked, not players).
    con.execute("""
        UPDATE _namec SET accepted = FALSE, reason = 'contradicts_id_link'
        WHERE rid IN (
            SELECT n.rid FROM _namec n JOIN _idr r
              ON r.id_type = n.id_type AND r.source_id = n.source_id AND r.n_win = 1
            WHERE r.gsis_id <> n.gsis_id)""")
    con.execute("""
        UPDATE _namec SET accepted = FALSE, reason = 'id_link_exists'
        WHERE reason IS NULL
          AND (id_type, source_id) IN (SELECT id_type, source_id FROM _idc_all)""")
    con.execute("""
        UPDATE _namec SET accepted = FALSE, reason = 'player_has_other_id_of_type'
        WHERE reason IS NULL AND EXISTS (
            SELECT 1 FROM _idr r WHERE r.n_win = 1 AND r.id_type = _namec.id_type
              AND r.gsis_id = _namec.gsis_id AND r.source_id <> _namec.source_id)""")
    con.execute("""
        UPDATE _namec SET accepted = FALSE, reason = 'ambiguous'
        WHERE reason IS NULL AND (
            (id_type, source_id) IN (
                SELECT id_type, source_id FROM _namec WHERE reason IS NULL
                GROUP BY 1, 2 HAVING count(DISTINCT gsis_id) > 1)
            OR (id_type, gsis_id) IN (
                SELECT id_type, gsis_id FROM _namec WHERE reason IS NULL
                GROUP BY 1, 2 HAVING count(DISTINCT source_id) > 1))""")
    con.execute("UPDATE _namec SET accepted = TRUE WHERE reason IS NULL")
    # a duplicated ff row proposes the same link twice: keep one
    con.execute("""
        CREATE OR REPLACE TEMP TABLE _namec AS
        SELECT id_type, source_id, gsis_id, min(method) AS method, min(method_rank) AS method_rank,
               min(ff_name) AS ff_name, min(ff_birthdate) AS ff_birthdate,
               min(ff_draft_year) AS ff_draft_year, min(ff_position) AS ff_position,
               bool_and(accepted) AS accepted,
               min(reason) AS reason
        FROM _namec GROUP BY 1, 2, 3""")
    by_pass = dict(links.group_by("method").len().iter_rows()) if not links.is_empty() else {}
    reasons = con.execute(
        "SELECT reason, count(*) FROM _namec WHERE NOT accepted GROUP BY 1 ORDER BY 1"
    ).fetchall()
    return {
        "name_pass_rows_linked": {m: int(by_pass.get(m, 0)) for m in NAME_METHODS},
        "name_pass_links_rejected": {r: n for r, n in reasons},
    }


CONFLICT_PREFIX = "precedence picked "
CANDIDATES_SEP = "; candidates: "


def _stage_bridge_issues(con: duckdb.DuckDBPyConnection) -> None:
    """``_bridge_issues``: ambiguous ids, conflicts and name-pass links as report rows.

    Candidates missing from dim_player are listed too (``(not in dim_player)``). A conflict's
    detail names the precedence pick; it does not say the pick is right: the usage check
    (:func:`stage_usage_checks`) rewrites it when a dataset's use of the id contradicts it."""
    cand = (
        "string_agg(c.method || '=' || c.gsis_id || ' (' || COALESCE(p.display_name, "
        "'not in dim_player') || ')', '; ' ORDER BY c.method_rank, c.gsis_id)"
    )
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _bridge_issues AS
        SELECT CASE WHEN r.n_win > 1 THEN 'ambiguous' ELSE 'conflict' END AS kind,
               'bridge_player_id' AS dataset, r.id_type, r.source_id,
               CASE WHEN r.n_win = 1 THEN r.gsis_id END AS gsis_id,
               CASE WHEN r.n_win = 1 THEN any_value(w.display_name) END AS name,
               CASE WHEN r.n_win = 1 THEN any_value(w.position) END AS position,
               CAST(min(c.season_min) AS INTEGER) AS season_min,
               CAST(max(c.season_max) AS INTEGER) AS season_max,
               CAST(NULL AS BIGINT) AS n_rows,
               CASE WHEN r.n_win > 1 THEN 'no link (' || r.method || ' names '
                    || r.n_win || ' players): '
                    ELSE '{CONFLICT_PREFIX}' || r.method || '{CANDIDATES_SEP}' END
               || {cand} AS detail
        FROM _idr r
        JOIN _idc_all c ON c.id_type = r.id_type AND c.source_id = r.source_id
        LEFT JOIN _players p ON p.gsis_id = c.gsis_id
        LEFT JOIN _players w ON w.gsis_id = r.gsis_id
        WHERE r.n_win > 1 OR r.n_candidates > 1
        GROUP BY r.id_type, r.source_id, r.n_win, r.method, r.gsis_id
        UNION ALL
        SELECT CASE WHEN n.accepted THEN 'name_link' ELSE 'ambiguous' END,
               'bridge_player_id', n.id_type, n.source_id,
               CASE WHEN n.accepted THEN n.gsis_id END,
               CASE WHEN n.accepted THEN any_value(p.display_name) END,
               CASE WHEN n.accepted THEN any_value(p.position) END,
               NULL, NULL, NULL,
               CASE WHEN n.accepted THEN min(n.method) || ': ff_playerids row '
                    || COALESCE(min(n.ff_name), '?') || ', born '
                    || COALESCE(CAST(min(n.ff_birthdate) AS VARCHAR), '?') || ', draft year '
                    || COALESCE(CAST(min(n.ff_draft_year) AS VARCHAR), '?')
                    || CASE WHEN any_value(g.grp) <> any_value(p.position_group)
                            THEN '; position group differs: ' || any_value(g.grp) || ' vs '
                                 || any_value(p.position_group)
                            ELSE '' END
                    ELSE 'no link (name passes name ' || count(DISTINCT n.gsis_id)
                    || ' player(s) for this id or this player for several ids): '
                    || string_agg(n.method || '=' || n.gsis_id || ' ('
                                  || COALESCE(p.display_name, '?') || ')', '; '
                                  ORDER BY n.gsis_id) END
        FROM _namec n LEFT JOIN _players p ON p.gsis_id = n.gsis_id
        LEFT JOIN _pos_group g ON g.pos = upper(n.ff_position)
        WHERE n.accepted OR n.reason = 'ambiguous'
        GROUP BY n.id_type, n.source_id, n.accepted,
                 CASE WHEN n.accepted THEN n.gsis_id END""")


def _dim_id_pick_sql() -> str:
    """The one id per (id_type, gsis_id) dim_player keeps, from ``_bridge``: the best method,
    then the smallest source_id (deterministic). Columns: id_type, gsis_id, source_id, method.
    Used by dim_player and by the 'multiple_ids' report, so they always agree."""
    types = ", ".join(f"'{t}'" for t in DIM_PLAYER_ID_TYPES)
    return f"""
        SELECT id_type, gsis_id, source_id, method FROM (
            SELECT id_type, gsis_id, source_id, method, row_number() OVER (
                PARTITION BY id_type, gsis_id ORDER BY method_rank, source_id) AS rn
            FROM _bridge WHERE id_type IN ({types})
        ) WHERE rn = 1"""


def dim_player_ids_sql() -> str:
    """One id per DIM_PLAYER_ID_TYPES type per gsis_id (:func:`_dim_id_pick_sql`).
    Columns: gsis_id, pfr_id, espn_id, ..."""
    pivots = ", ".join(
        f"max(CASE WHEN id_type = '{t}' THEN source_id END) AS {sc.q(t + '_id')}"
        for t in DIM_PLAYER_ID_TYPES
    )
    return f"SELECT gsis_id, {pivots} FROM ({_dim_id_pick_sql()}) GROUP BY gsis_id"


def stage_multiple_ids(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Append 'multiple_ids' rows to ``_bridge_issues``: a player with several ids of a
    dim_player type (dim_player keeps one). Returns counts per type."""
    con.execute(f"""
        INSERT INTO _bridge_issues
        SELECT 'multiple_ids', 'bridge_player_id', b.id_type, b.source_id, b.gsis_id,
               any_value(p.display_name), any_value(p.position), NULL, NULL, NULL,
               'dim_player keeps ' || k.source_id || ' (' || k.method || '); this one via '
               || b.method
        FROM _bridge b
        JOIN ({_dim_id_pick_sql()}) k
          ON k.id_type = b.id_type AND k.gsis_id = b.gsis_id AND k.source_id <> b.source_id
        LEFT JOIN _players p ON p.gsis_id = b.gsis_id
        GROUP BY b.id_type, b.source_id, b.gsis_id, k.source_id, k.method, b.method""")
    rows = con.execute(
        "SELECT id_type, count(DISTINCT gsis_id) FROM _bridge_issues WHERE kind = "
        "'multiple_ids' GROUP BY 1 ORDER BY 1"
    ).fetchall()
    return {t: n for t, n in rows}


# --------------------------------------------------------------------------------------
# Usage check: does the linked player's career fit where a dataset uses the id?
# --------------------------------------------------------------------------------------

CAREER_SLACK = 1  # seasons of slack at both ends of a career (rookie_season .. last_season)
USAGE_OVERRIDE_SUFFIX = "_usage_override"


def _fits_career_sql(season: str, p: str) -> str:
    """``season`` lies inside player ``p``'s career window [COALESCE(rookie_season,
    draft_year) - 1, last_season + 1] (an unknown end is open)."""
    lo = f"(COALESCE({p}.rookie_season, {p}.draft_year) - {CAREER_SLACK})"
    hi = f"({p}.last_season + {CAREER_SLACK})"
    return f"(({lo} IS NULL OR {season} >= {lo}) AND ({hi} IS NULL OR {season} <= {hi}))"


def _player_sql(p: str) -> str:
    """'Name, POS, career 2012-2020' of player alias ``p`` for report details."""
    return (
        f"COALESCE({p}.display_name, '?') || ', ' || COALESCE({p}.position, '?') "
        f"|| ', career ' || COALESCE(CAST(COALESCE({p}.rookie_season, {p}.draft_year) AS "
        f"VARCHAR), '?') || '-' || COALESCE(CAST({p}.last_season AS VARCHAR), '?')"
    )


def stage_usage_checks(
    con: duckdb.DuckDBPyConnection, dataset: str, id_type: str, usage_sql: str
) -> dict[str, Any]:
    """Check the ``_bridge`` links of ``id_type`` against how ``dataset`` uses the ids.

    ``usage_sql`` yields (season, source_id, position) rows (canonical ids). Two rules:

    * **career** (hard): a season outside the linked player's career window
      (:func:`_fits_career_sql`) cannot be that player. When the id is never used inside the
      window, the link is not manual, and every lower-ranked source that names another player
      names the same one, whose window holds every season of use, the bridge is re-linked to
      him (method ``<source>_usage_override``, ``is_conflict`` true). Otherwise the link stays
      and the dataset's rows outside the window get no gsis_id (``_usage_null``).
    * **position** (report only): the dataset lists the id only on the other side of the ball
      (offense / defense / special teams) than the linked player's position group. Position
      changes make this noisy, so the link is kept.

    Both add a ``suspect`` row to ``_bridge_issues`` naming the evidence, and a conflict row
    of the id says the precedence pick is contradicted. Returns manifest notes."""
    t = sc.sql_str(id_type)
    ds = sc.sql_str(dataset)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _usage AS
        SELECT u.source_id, CAST(u.season AS INTEGER) AS season,
               CAST(u.position AS VARCHAR) AS position, count(*) AS n_rows
        FROM ({usage_sql}) u(season, source_id, position)
        WHERE u.source_id IS NOT NULL AND u.season IS NOT NULL
        GROUP BY 1, 2, 3""")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _usage_eval AS
        SELECT u.*, b.gsis_id, b.method, b.method_rank, {_fits_career_sql("u.season", "p")}
                   AS fits, g.side AS use_side, ps.side AS player_side
        FROM _usage u
        JOIN _bridge b ON b.id_type = {t} AND b.source_id = u.source_id
        JOIN _players p ON p.gsis_id = b.gsis_id
        LEFT JOIN _pos_group g ON g.pos = upper(split_part(u.position, '/', 1))
        LEFT JOIN (SELECT DISTINCT grp, side FROM _pos_group) ps ON ps.grp = p.position_group""")
    con.execute("""
        CREATE OR REPLACE TEMP TABLE _usage_ids AS
        SELECT source_id, any_value(gsis_id) AS gsis_id, any_value(method) AS method,
               any_value(method_rank) AS method_rank, min(season) AS s0, max(season) AS s1,
               sum(n_rows) AS n_rows, bool_or(fits) AS any_fit,
               min(season) FILTER (WHERE NOT fits) AS out0,
               max(season) FILTER (WHERE NOT fits) AS out1,
               COALESCE(sum(n_rows) FILTER (WHERE NOT fits), 0) AS n_rows_out,
               string_agg(DISTINCT CAST(season AS VARCHAR), ', '
                          ORDER BY CAST(season AS VARCHAR)) FILTER (WHERE NOT fits)
                   AS out_seasons,
               string_agg(DISTINCT position, ', ' ORDER BY position) AS positions,
               string_agg(DISTINCT use_side, '/' ORDER BY use_side) AS use_sides,
               any_value(player_side) AS player_side,
               bool_and(use_side IS NOT NULL AND player_side IS NOT NULL
                        AND use_side <> player_side) AS side_mismatch
        FROM _usage_eval GROUP BY source_id""")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _usage_fix AS
        WITH hard AS (SELECT * FROM _usage_ids WHERE n_rows_out > 0),
        alt AS (
            SELECT h.source_id, count(DISTINCT c.gsis_id) AS n_alt, min(c.gsis_id) AS alt_gsis,
                   arg_min(c.method, c.method_rank) AS alt_method,
                   min(c.method_rank) AS alt_rank
            FROM hard h JOIN _idc c ON c.id_type = {t} AND c.source_id = h.source_id
             AND c.method_rank > h.method_rank AND c.gsis_id <> h.gsis_id
            GROUP BY h.source_id
        )
        SELECT h.*, COALESCE(a.n_alt, 0) AS n_alt, a.alt_gsis, a.alt_method, a.alt_rank,
               COALESCE(NOT h.any_fit AND h.method <> 'manual' AND a.n_alt = 1
                        AND {_fits_career_sql("h.s0", "ap")}
                        AND {_fits_career_sql("h.s1", "ap")}, FALSE) AS override
        FROM hard h LEFT JOIN alt a USING (source_id)
        LEFT JOIN _players ap ON ap.gsis_id = a.alt_gsis""")
    con.execute(f"""
        INSERT INTO _usage_null
        SELECT DISTINCT {ds}, {t}, e.source_id, e.season FROM _usage_eval e
        WHERE NOT e.fits
          AND e.source_id NOT IN (SELECT source_id FROM _usage_fix WHERE override)""")
    why_null = (
        "CASE WHEN f.any_fit THEN 'the id is also used inside the career' "
        "WHEN f.method = 'manual' THEN 'a manual link is never overridden' "
        "WHEN f.n_alt = 0 THEN 'no other source names another player' "
        "WHEN f.n_alt > 1 THEN 'the other sources name different players' "
        "ELSE 'the other candidate''s career does not fit either' END"
    )
    con.execute(f"""
        INSERT INTO _bridge_issues
        SELECT 'suspect', {ds}, {t}, f.source_id,
               CASE WHEN f.override THEN f.alt_gsis ELSE f.gsis_id END,
               CASE WHEN f.override THEN ap.display_name ELSE p.display_name END,
               f.positions, f.out0, f.out1, f.n_rows_out,
               'career: ' || f.method || ' links ' || f.gsis_id || ' (' || {_player_sql("p")}
               || ') but ' || {ds} || ' uses this id in ' || f.out_seasons || ' ('
               || f.n_rows_out || ' rows as ' || f.positions || '): '
               || CASE WHEN f.override THEN 're-linked to ' || f.alt_method || '='
                       || f.alt_gsis || ' (' || {_player_sql("ap")} || '), the only other '
                       || 'candidate, whose career fits; confirm it with a manual override'
                  ELSE {ds} || '.gsis_id is NULL for those rows (' || {why_null}
                       || '); add a manual override once the player is known' END
        FROM _usage_fix f JOIN _players p ON p.gsis_id = f.gsis_id
        LEFT JOIN _players ap ON ap.gsis_id = f.alt_gsis""")
    con.execute(f"""
        INSERT INTO _bridge_issues
        SELECT 'suspect', {ds}, {t}, i.source_id, i.gsis_id, p.display_name, i.positions,
               i.s0, i.s1, i.n_rows,
               'position: ' || {ds} || ' lists this id only as ' || i.positions || ' ('
               || i.use_sides || ') but ' || i.method || ' links ' || i.gsis_id || ' ('
               || {_player_sql("p")} || ', ' || i.player_side || '): link kept; check it and '
               || 'add a manual override if it is another player'
        FROM _usage_ids i JOIN _players p ON p.gsis_id = i.gsis_id
        WHERE i.side_mismatch AND i.n_rows_out = 0""")
    # a conflict row of a contradicted id must not read as if the pick were safe
    con.execute(f"""
        UPDATE _bridge_issues SET
            gsis_id = COALESCE(x.new_gsis, _bridge_issues.gsis_id),
            name = CASE WHEN x.new_gsis IS NOT NULL THEN x.new_name ELSE _bridge_issues.name END,
            position = CASE WHEN x.new_gsis IS NOT NULL THEN x.new_position
                            ELSE _bridge_issues.position END,
            detail = 'usage contradicts the precedence pick (' || x.rule || ' in ' || {ds}
                     || ', see kind suspect): ' || x.outcome
                     || substr(_bridge_issues.detail,
                               strpos(_bridge_issues.detail, '{CANDIDATES_SEP}'))
        FROM (
            SELECT f.source_id, CASE WHEN f.override THEN f.alt_gsis END AS new_gsis,
                   ap.display_name AS new_name, ap.position AS new_position, 'career' AS rule,
                   CASE WHEN f.override THEN 're-linked to ' || f.alt_method || '='
                        || f.alt_gsis ELSE 'rows outside the career have no gsis_id' END
                       AS outcome
            FROM _usage_fix f LEFT JOIN _players ap ON ap.gsis_id = f.alt_gsis
            UNION ALL
            SELECT source_id, NULL, NULL, NULL, 'position', 'link kept'
            FROM _usage_ids WHERE side_mismatch AND n_rows_out = 0
        ) x
        WHERE _bridge_issues.kind = 'conflict' AND _bridge_issues.dataset = 'bridge_player_id'
          AND _bridge_issues.id_type = {t} AND _bridge_issues.source_id = x.source_id""")
    con.execute(f"""
        UPDATE _bridge SET gsis_id = f.alt_gsis,
            method = f.alt_method || '{USAGE_OVERRIDE_SUFFIX}', method_rank = f.alt_rank,
            is_conflict = TRUE
        FROM _usage_fix f
        WHERE f.override AND _bridge.id_type = {t} AND _bridge.source_id = f.source_id""")
    n_relinked, n_nulled, n_rows_nulled = con.execute(
        "SELECT count(*) FILTER (WHERE override), count(*) FILTER (WHERE NOT override), "
        "COALESCE(sum(n_rows_out) FILTER (WHERE NOT override), 0) FROM _usage_fix"
    ).fetchone()
    n_position = con.execute(
        "SELECT count(*) FROM _usage_ids WHERE side_mismatch AND n_rows_out = 0"
    ).fetchone()[0]
    return {
        "usage_check": {
            "id_type": id_type,
            "n_ids_relinked": n_relinked,
            "n_ids_career_suspect_nulled": n_nulled,
            "n_rows_gsis_nulled": int(n_rows_nulled),
            "n_ids_position_suspect": n_position,
        }
    }


def bridge_notes(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Final link counts of ``_bridge`` (after the usage checks)."""
    rows = con.execute(
        "SELECT method, count(*) FROM _bridge GROUP BY 1 ORDER BY min(method_rank), 1"
    ).fetchall()
    by_type = con.execute("SELECT id_type, count(*) FROM _bridge GROUP BY 1 ORDER BY 1").fetchall()
    n_conf = con.execute("SELECT count(*) FROM _bridge WHERE is_conflict").fetchone()[0]
    return {
        "n_links_by_method": {m: n for m, n in rows},
        "n_links_by_id_type": {t: n for t, n in by_type},
        "n_conflicts": n_conf,
    }


# --------------------------------------------------------------------------------------
# The unmatched-id report
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RawCoverage:
    """A dataset of the raw cache whose ids are checked against the bridge (read straight
    from the cache files, never fetched)."""

    dataset: str  # dataset folder (nflverse.DATASETS name)
    label: str  # report_id_coverage.dataset
    id_col: str
    id_type: str  # a bridge id_type or 'gsis'
    name_col: str
    pos_col: str | None
    season_sql: str  # SQL over the file's columns
    where: str = "TRUE"


_SCRAPE_YEAR = "CAST(substr(scrape_date, 1, 4) AS INTEGER)"
_NOT_DST = "COALESCE(pos, '') NOT IN ('DST', 'DEF')"
# FantasyPros' IDP pages (db-, dl-, lb-, idp- ...) sometimes list an offensive player; their
# ranks are not fantasy-offense ranks, so the rankings coverage leaves those pages out.
_NOT_IDP_PAGE = "NOT regexp_matches(COALESCE(fp_page, ''), '(^|/|-)(db|dl|lb|idp)(-|[.]php|$)')"

RAW_COVERAGE: tuple[RawCoverage, ...] = (
    RawCoverage("ff_rankings_all", "ff_rankings_all[rp]", "id", "fantasypros", "player", "pos",
                _SCRAPE_YEAR, f"ecr_type = 'rp' AND {_NOT_DST} AND {_NOT_IDP_PAGE}"),
    RawCoverage("ff_rankings_all", "ff_rankings_all[wp]", "id", "fantasypros", "player", "pos",
                _SCRAPE_YEAR, f"ecr_type = 'wp' AND {_NOT_DST} AND {_NOT_IDP_PAGE}"),
    RawCoverage("ff_rankings_week", "ff_rankings_week", "fantasypros_id", "fantasypros",
                "player_name", "pos", _SCRAPE_YEAR, _NOT_DST),
    RawCoverage("ff_rankings_draft", "ff_rankings_draft", "id", "fantasypros", "player", "pos",
                _SCRAPE_YEAR, _NOT_DST),
    RawCoverage("ff_opportunity", "ff_opportunity", "player_id", GSIS, "full_name", "position",
                "CAST(season AS INTEGER)"),
    RawCoverage("pfr_pass", "pfr_pass", "pfr_player_id", "pfr", "pfr_player_name", None,
                "season"),
    RawCoverage("pfr_rush", "pfr_rush", "pfr_player_id", "pfr", "pfr_player_name", None,
                "season"),
    RawCoverage("pfr_rec", "pfr_rec", "pfr_player_id", "pfr", "pfr_player_name", None, "season"),
    RawCoverage("ngs_passing", "ngs_passing", "player_gsis_id", GSIS, "player_display_name",
                "player_position", "season"),
    RawCoverage("ngs_rushing", "ngs_rushing", "player_gsis_id", GSIS, "player_display_name",
                "player_position", "season"),
    RawCoverage("ngs_receiving", "ngs_receiving", "player_gsis_id", GSIS, "player_display_name",
                "player_position", "season"),
    RawCoverage("draft_picks", "draft_picks[gsis]", "gsis_id", GSIS, "pfr_player_name",
                "position", "season"),
    RawCoverage("draft_picks", "draft_picks[pfr]", "pfr_player_id", "pfr", "pfr_player_name",
                "position", "season"),
    RawCoverage("combine", "combine", "pfr_id", "pfr", "player_name", "pos", "season"),
)  # fmt: skip

POOL_LABEL = "ff_rankings_all[rp_preseason_pool]"
# The positional cheat-sheet page of a pool row: '/nfl/rankings/qb-cheatsheets.php',
# 'ppr-wr-cheatsheets' ... (group 1 must equal the row's lower-case position).
POOL_PAGE_RE = "(qb|rb|wr|te)-cheatsheets"

# ---- FantasyPros page classification, shared by fact_ranking (warehouse, C1) and the id
# report's preseason pool: one definition of "a position's preseason cheat sheet", "rest of
# season" and "weekly" page, and of which preseason scrape a season uses.
# Pages come in a long form ('/nfl/rankings/ppr-wr-cheatsheets.php', 2021+) and a short form
# ('ppr-wr-cheatsheets', 2019-12 to 2020-10). IDP (db/dl/lb/idp), kicker and defense pages are
# never QB/RB/WR/TE pages; the 2020 short-form pages labelled 'dynasty-...' are left out too.
PRESEASON_PAGE_RE = "(^|/)(ppr-)?(qb|rb|wr|te)-cheatsheets([.]php)?$"
ROS_PAGE_RE = "(^|/)ros-(ppr-)?(qb|rb|wr|te)([.]php)?$"
WEEKLY_PAGE_RE = "(^|/)(ppr-)?(qb|rb|wr|te)([.]php)?$"
# group 1: the page's position, for any of the three kinds of page
PAGE_POS_RE = "(qb|rb|wr|te)(-cheatsheets)?([.]php)?$"
PAGE_KINDS = ("preseason", "ros", "weekly")
# Preseason cheat sheets count only when scraped in these months (the draft season) and before
# the season's first game day (week 1's dim_week.first_gameday); the last such scrape wins.
PRESEASON_MONTHS = (8, 9)


def ranking_page_kind_sql(ecr_type: str, fp_page: str, page_type: str) -> str:
    """SQL: 'preseason' | 'ros' | 'weekly' for a QB/RB/WR/TE positional page, else NULL.

    ``preseason``: a redraft positional (``rp``) cheat sheet; ``ros``: an ``rp`` rest-of-season
    page; ``weekly``: a weekly positional (``wp``) page. Arguments are SQL expressions."""
    page = f"COALESCE({fp_page}, '')"
    return (
        f"(CASE WHEN COALESCE({page_type}, '') LIKE 'dynasty%' THEN NULL "
        f"WHEN {ecr_type} = 'rp' AND regexp_matches({page}, '{PRESEASON_PAGE_RE}') "
        "THEN 'preseason' "
        f"WHEN {ecr_type} = 'rp' AND regexp_matches({page}, '{ROS_PAGE_RE}') THEN 'ros' "
        f"WHEN {ecr_type} = 'wp' AND regexp_matches({page}, '{WEEKLY_PAGE_RE}') THEN 'weekly' "
        "END)"
    )


def ranking_page_pos_sql(fp_page: str) -> str:
    """SQL: the page's position in upper case (QB/RB/WR/TE); only meaningful for pages that
    :func:`ranking_page_kind_sql` classifies."""
    return f"NULLIF(upper(regexp_extract(COALESCE({fp_page}, ''), '{PAGE_POS_RE}', 1)), '')"


def preseason_scrape_sql(pages: str, weeks: str = "dim_week") -> str:
    """SQL: per season, the scrape that is the preseason ranking: the last ``scrape_date`` of
    ``pages`` (preseason cheat-sheet rows with ``season`` and ``scrape_date``) in August or
    September and strictly before week 1's first game day (``weeks``: dim_week or its
    point-in-time view). Columns: season, scrape_date."""
    months = ", ".join(str(m) for m in PRESEASON_MONTHS)
    return f"""
        SELECT p.season, max(p.scrape_date) AS scrape_date
        FROM {pages} p JOIN {weeks} w ON w.season = p.season AND w.week = 1
         AND w.season_type = 'REG'
        WHERE month(CAST(p.scrape_date AS DATE)) IN ({months})
          AND CAST(p.scrape_date AS DATE) < w.first_gameday
        GROUP BY p.season"""


def _check_raw_coverage() -> None:
    for r in RAW_COVERAGE:
        cols = [r.id_col, r.name_col] + ([r.pos_col] if r.pos_col else [])
        cols += ["scrape_date"] if "scrape_date" in r.season_sql else ["season"]
        if "ecr_type" in r.where:
            cols.append("ecr_type")
        if "fp_page" in r.where:
            cols.append("fp_page")
        sc._check_names(r.dataset, cols)
    sc._check_names("ff_rankings_all", ["fp_page", "page_type", "ecr", "ecr_type"])


_check_raw_coverage()


@dataclass(frozen=True)
class CoverageRows:
    """SQL producing (season, source_id, name, position) rows of one dataset for one id type."""

    label: str
    id_type: str
    rows_sql: str
    has_position: bool


def warehouse_coverage() -> list[CoverageRows]:
    """The warehouse's own id columns: snaps (pfr), daily depth charts without a source gsis
    (espn), and gsis ids in stats, injuries and play-by-play (present in dim_player?)."""
    play = " UNION ALL ".join(
        f"SELECT season, {role}_player_id AS source_id, {role}_player_name AS name, "
        "NULL AS position FROM fact_play"
        for role in ("passer", "rusher", "receiver")
    )
    return [
        CoverageRows("fact_snaps", "pfr",
                     "SELECT season, pfr_player_id, player, position FROM fact_snaps", True),
        CoverageRows("fact_depth_chart[daily_no_gsis]", "espn",
                     "SELECT season, espn_id, player_name, position FROM fact_depth_chart "
                     "WHERE source_format = 'daily' AND (gsis_id IS NULL OR gsis_id_from_espn)",
                     True),
        CoverageRows("fact_player_week", GSIS,
                     "SELECT season, player_id, player_name, position FROM fact_player_week",
                     True),
        CoverageRows("fact_injury_report", GSIS,
                     "SELECT season, gsis_id, full_name, position FROM fact_injury_report", True),
        CoverageRows("fact_play", GSIS, play, False),
    ]  # fmt: skip


def raw_coverage(
    con: duckdb.DuckDBPyConnection,
    seasons: Sequence[int],
    cache_path: Callable[[str, int | None], Path],
) -> tuple[list[CoverageRows], dict[str, Any]]:
    """Open views over the cached raw files (built seasons for per-season datasets, the one
    file otherwise); datasets or seasons that are not cached are skipped and listed."""
    from twm.sources import nflverse as nv

    out: list[CoverageRows] = []
    skipped: dict[str, list[int] | str] = {}
    views: dict[str, dict[str, str]] = {}
    for r in RAW_COVERAGE:
        ds = nv.DATASETS[r.dataset]
        view = f"raw_{r.dataset}"
        if r.dataset not in views:
            if ds.per_season:
                want = [s for s in seasons if ds.first_season is None or s >= ds.first_season]
                paths = [(s, cache_path(r.dataset, s)) for s in want]
                have = [p for _, p in paths if p.exists()]
                missing = [s for s, p in paths if not p.exists()]
                if missing:
                    skipped[r.dataset] = missing
            else:
                p = cache_path(r.dataset, None)
                have = [p] if p.exists() else []
                if not have:
                    skipped[r.dataset] = "not cached"
            if not have:
                views[r.dataset] = {}
                continue
            files = "[" + ", ".join(sc.sql_str(str(p)) for p in have) + "]"
            con.execute(
                f"CREATE OR REPLACE TEMP VIEW {view} AS "
                f"SELECT * FROM read_parquet({files}, union_by_name=true)"
            )
            views[r.dataset] = {
                row[0]: row[1] for row in con.execute(f"DESCRIBE {view}").fetchall()
            }
        types = views[r.dataset]
        if not types:
            continue
        sid = canonical_id_sql(sc.q(r.id_col), types[r.id_col])
        pos = f"CAST({sc.q(r.pos_col)} AS VARCHAR)" if r.pos_col else "NULL"
        out.append(
            CoverageRows(
                r.label,
                r.id_type,
                f"SELECT {r.season_sql} AS season, {sid} AS source_id, "
                f"CAST({sc.q(r.name_col)} AS VARCHAR) AS name, {pos} AS position "
                f"FROM {view} WHERE {r.where}",
                r.pos_col is not None,
            )
        )
    return out, {"coverage_datasets_skipped": skipped} if skipped else {}


def pool_coverage(
    con: duckdb.DuckDBPyConnection, cutoffs: Mapping[str, int]
) -> CoverageRows | None:
    """The Waiver Radar candidate pool in the preseason redraft positional (``rp``) rankings:
    per season, the last August/September cheat-sheet scrape before week 1's first game day,
    and per position the players ranked inside ``cutoffs`` (``League.candidate_pool_cutoffs``:
    QB 18, RB 36, WR 36, TE 18 in the default league; by ECR, ties included). Only a player's
    own positional page counts (a WR's rank on the WR cheat sheet, never his rank on an IDP or
    another position's page). Seasons that are not built are skipped."""
    if "raw_ff_rankings_all" not in {
        r[0] for r in con.execute("SELECT view_name FROM duckdb_views()").fetchall()
    }:
        return None
    cut = " ".join(f"WHEN '{p}' THEN {int(n)}" for p, n in cutoffs.items())
    pos = ", ".join(f"'{p}'" for p in cutoffs)
    kind = ranking_page_kind_sql("ecr_type", "fp_page", "page_type")
    return CoverageRows(
        POOL_LABEL,
        "fantasypros",
        f"""
        WITH pages AS (
            SELECT {_SCRAPE_YEAR} AS season, scrape_date, pos, id, player, ecr, fp_page
            FROM raw_ff_rankings_all
            WHERE {kind} = 'preseason' AND {ranking_page_pos_sql("fp_page")} = pos
              AND pos IN ({pos}) AND ecr IS NOT NULL
        ),
        last_scrape AS ({preseason_scrape_sql("pages")}),
        ranked AS (
            SELECT p.season, {canonical_id_sql("p.id", "VARCHAR")} AS source_id,
                   min(p.player) AS name, p.pos AS position, min(p.ecr) AS ecr
            FROM pages p JOIN last_scrape l USING (season, scrape_date)
            GROUP BY p.season, 2, p.pos
        )
        SELECT season, source_id, name, position FROM (
            SELECT *, rank() OVER (PARTITION BY season, position ORDER BY ecr) AS pos_rank
            FROM ranked)
        WHERE pos_rank <= CASE position {cut} END""",
        True,
    )


def _matched_sql(id_type: str, label: str) -> str:
    if id_type == GSIS:
        return "r.source_id IN (SELECT gsis_id FROM dim_player)"
    # rows the usage check left without a gsis_id (their link is implausible) do not count
    return (
        f"r.source_id IN (SELECT source_id FROM bridge_player_id WHERE id_type = '{id_type}') "
        f"AND NOT EXISTS (SELECT 1 FROM _usage_null n WHERE n.dataset = {sc.sql_str(label)} "
        f"AND n.id_type = '{id_type}' AND n.source_id = r.source_id AND n.season = r.season)"
    )


def stage_reports(
    con: duckdb.DuckDBPyConnection, sources: Sequence[CoverageRows]
) -> dict[str, Any]:
    """Create ``_report_coverage`` and ``_report_unmatched`` (plus the bridge issues) from the
    coverage sources; return headline notes."""
    fantasy = ", ".join(f"'{p}'" for p in FANTASY_POSITIONS)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE _cov_rows (dataset VARCHAR, id_type VARCHAR,
            season INTEGER, source_id VARCHAR, name VARCHAR, position VARCHAR,
            has_position BOOLEAN, matched BOOLEAN)""")
    for s in sources:
        con.execute(f"""
            INSERT INTO _cov_rows
            SELECT {sc.sql_str(s.label)}, '{s.id_type}', CAST(r.season AS INTEGER),
                   r.source_id, r.name, r.position, {str(s.has_position).upper()},
                   {_matched_sql(s.id_type, s.label)}
            FROM ({s.rows_sql}) r(season, source_id, name, position)
            WHERE r.source_id IS NOT NULL""")
    scopes = f"""
        SELECT *, 'all' AS scope FROM _cov_rows
        UNION ALL
        SELECT *, 'fantasy' FROM _cov_rows WHERE has_position AND position IN ({fantasy})"""
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _report_coverage AS
        SELECT dataset, season, id_type, scope,
               count(*) AS n_rows, count(*) FILTER (WHERE NOT matched) AS n_rows_unmatched,
               count(DISTINCT source_id) AS n_ids,
               count(DISTINCT source_id) FILTER (WHERE NOT matched) AS n_ids_unmatched,
               round(1 - count(*) FILTER (WHERE NOT matched) / count(*), 6) AS match_rate,
               round(1 - count(DISTINCT source_id) FILTER (WHERE NOT matched)
                     / count(DISTINCT source_id), 6) AS id_match_rate
        FROM ({scopes}) WHERE dataset <> '{POOL_LABEL}' OR scope = 'fantasy'
        GROUP BY dataset, season, id_type, scope""")
    # why an unmatched id has no link, when the bridge knows something about it
    con.execute("""
        CREATE OR REPLACE TEMP TABLE _unmatched_why AS
        SELECT id_type, source_id, arg_min(why, prio) AS why FROM (
            SELECT DISTINCT id_type, source_id, 0 AS prio,
                   'the link is implausible for these seasons (see kind suspect)' AS why
            FROM _usage_null
            UNION ALL
            SELECT DISTINCT id_type, source_id, 1,
                   'ambiguous in bridge_player_id (see kind ambiguous)'
            FROM _bridge_issues WHERE kind = 'ambiguous'
            UNION ALL
            SELECT id_type, source_id, 2, 'known only as a player missing from dim_player: '
                   || string_agg(DISTINCT method || '=' || gsis_id, '; ' ORDER BY method || '='
                                 || gsis_id)
            FROM _idc_unknown GROUP BY 1, 2
        ) GROUP BY 1, 2""")
    con.execute("""
        CREATE OR REPLACE TEMP TABLE _report_unmatched AS
        SELECT 'unmatched' AS kind, r.dataset, r.id_type, r.source_id,
               CAST(NULL AS VARCHAR) AS gsis_id, min(r.name) AS name,
               min(r.position) AS position, min(r.season) AS season_min,
               max(r.season) AS season_max, count(*) AS n_rows, any_value(w.why) AS detail
        FROM _cov_rows r
        LEFT JOIN _unmatched_why w ON w.id_type = r.id_type AND w.source_id = r.source_id
        WHERE NOT r.matched GROUP BY r.dataset, r.id_type, r.source_id
        UNION ALL
        SELECT kind, dataset, id_type, source_id, gsis_id, name, position, season_min,
               season_max, n_rows, detail FROM _bridge_issues""")
    return _headline_notes(con)


def _headline_notes(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    def rates(dataset: str, scope: str, col: str = "match_rate") -> dict[str, float]:
        rows = con.execute(
            f"SELECT season, {col} FROM _report_coverage WHERE dataset = ? AND scope = ? "
            "ORDER BY season",
            [dataset, scope],
        ).fetchall()
        return {str(s): float(r) for s, r in rows}

    pool = con.execute(
        "SELECT season, n_ids, n_ids_unmatched FROM _report_coverage WHERE dataset = ? "
        "ORDER BY season",
        [POOL_LABEL],
    ).fetchall()
    return {
        "fact_snaps_fantasy_match_rate": rates("fact_snaps", "fantasy"),
        "rankings_rp_fantasy_id_match_rate": rates(
            "ff_rankings_all[rp]", "fantasy", "id_match_rate"
        ),  # fmt: skip
        "rankings_wp_fantasy_match_rate": rates("ff_rankings_all[wp]", "fantasy"),
        "preseason_pool_unmatched": {str(s): f"{u}/{n}" for s, n, u in pool},
    }


# --------------------------------------------------------------------------------------
# Reading the report back (twm ids, twm doctor)
# --------------------------------------------------------------------------------------

REPORT_TABLES = ("report_id_coverage", "report_id_unmatched", "bridge_player_id")


def has_report(con: duckdb.DuckDBPyConnection) -> bool:
    names = {r[0] for r in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
    return all(t in names for t in REPORT_TABLES)


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.2f}%"


def doctor_line(con: duckdb.DuckDBPyConnection) -> str | None:
    """One line of id coverage for ``twm doctor`` (None when the tables are absent)."""
    if not has_report(con):
        return None
    snaps = con.execute(
        "SELECT min(season), max(season), sum(n_rows_unmatched), sum(n_rows), "
        "arg_min(season, (match_rate, season)), min(match_rate) FROM report_id_coverage "
        "WHERE dataset = 'fact_snaps' AND scope = 'fantasy'"
    ).fetchone()
    n_links, n_conf = con.execute(
        "SELECT count(*), count(*) FILTER (WHERE is_conflict) FROM bridge_player_id"
    ).fetchone()
    n_amb, n_suspect = con.execute(
        "SELECT count(*) FILTER (WHERE kind = 'ambiguous'), "
        "count(*) FILTER (WHERE kind = 'suspect') FROM report_id_unmatched"
    ).fetchone()
    part = "fact_snaps: no rows"
    if snaps[3]:
        part = (
            f"snaps QB/RB/WR/TE {_pct(1 - snaps[2] / snaps[3])} matched ({snaps[0]}-{snaps[1]}, "
            f"lowest {_pct(snaps[5])} in {snaps[4]})"
        )
    return (
        f"ids: {part}; {n_links:,} bridge links, {n_amb:,} ambiguous, {n_conf:,} conflicts, "
        f"{n_suspect:,} suspect"
    )


PLAIN_INT_COLUMNS = frozenset({"season", "first", "last"})


def _md_table(
    header: Sequence[str], rows: Iterable[Sequence[Any]], plain: Iterable[str] = PLAIN_INT_COLUMNS
) -> list[str]:
    """A markdown table; integers get thousands separators except in the ``plain`` columns
    (seasons), floats print as percentages."""
    keep = {i for i, h in enumerate(header) if h in set(plain)}

    def cell(i: int, v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, bool):
            return str(v).lower()
        if isinstance(v, int) and i not in keep:
            return f"{v:,}"
        if isinstance(v, float):
            return _pct(v)
        return str(v).replace("|", "\\|").replace("\n", " ")

    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(cell(i, v) for i, v in enumerate(r)) + " |" for r in rows]
    return out


# name-pass links the markdown lists (the table has them all): fantasy-site ids and players
# of the fantasy positions
NAME_LINK_ID_TYPES = ("fantasypros", "sleeper", "yahoo")


def render_markdown(con: duckdb.DuckDBPyConnection, *, top: int = 10) -> str:
    """The unmatched-id report as markdown: the headline numbers first, then the links to
    check, then the full coverage table. Deterministic: sorted, and the only time in it is the
    build's ``built_at``. ``top``: unmatched QB/RB/WR/TE ids listed per dataset."""
    built_at = con.execute(
        "SELECT max(built_at) FROM build_manifest WHERE table_name = 'report_id_coverage'"
    ).fetchone()[0]
    q = con.execute
    fantasy = ", ".join(f"'{p}'" for p in FANTASY_POSITIONS)
    lines = [
        "# Player ID coverage (unmatched-id report)",
        "",
        f"Generated by `twm ids` from the warehouse built at "
        f"{built_at if built_at is not None else 'unknown'} UTC. Every build refreshes the "
        "tables `report_id_coverage` and `report_id_unmatched`; docs/warehouse.md, section "
        '"Player IDs", explains the rules.',
        "",
        "## Headline",
        "",
        "Share of QB/RB/WR/TE rows whose id maps to a `gsis_id` (snap counts: PFR ids; weekly "
        "`wp` rankings: FantasyPros ids) and the Waiver Radar candidate pool (preseason `rp` "
        "rankings) players without one.",
        "",
    ]
    rows = q(
        """
        SELECT season,
               max(match_rate) FILTER (WHERE dataset = 'fact_snaps') AS snaps,
               max(CAST(n_ids_unmatched AS VARCHAR) || '/' || CAST(n_ids AS VARCHAR))
                   FILTER (WHERE dataset = ?) AS pool,
               max(match_rate) FILTER (WHERE dataset = 'ff_rankings_all[wp]') AS wp
        FROM report_id_coverage
        WHERE scope = 'fantasy' AND dataset IN ('fact_snaps', 'ff_rankings_all[wp]', ?)
        GROUP BY season ORDER BY season""",
        [POOL_LABEL, POOL_LABEL],
    ).fetchall()
    lines += (
        _md_table(
            ["season", "snaps QB/RB/WR/TE", "pool unmatched", "weekly wp QB/RB/WR/TE"], rows
        )  # fmt: skip
        if rows
        else ["No coverage rows."]
    )
    lines += ["", "## Bridge links", ""]
    rows = q(
        "SELECT id_type, method, count(*), count(*) FILTER (WHERE is_conflict) "
        "FROM bridge_player_id GROUP BY 1, 2 ORDER BY 1, 2"
    ).fetchall()
    lines += _md_table(["id type", "method", "links", "of which conflicts"], rows)
    kinds = dict(
        q("SELECT kind, count(*) FROM report_id_unmatched GROUP BY 1 ORDER BY 1").fetchall()
    )
    lines += [
        "",
        f"Suspect links (usage contradicts the link): {kinds.get('suspect', 0):,}. Ambiguous "
        f"ids (left out): {kinds.get('ambiguous', 0):,}. Conflicts (resolved by precedence): "
        f"{kinds.get('conflict', 0):,}. Name-pass links: {kinds.get('name_link', 0):,}. Extra "
        f"ids of a player (dim_player keeps one): {kinds.get('multiple_ids', 0):,}.",
    ]
    rows = q(
        "SELECT dataset, id_type, source_id, gsis_id, name, season_min, season_max, n_rows, "
        "detail FROM report_id_unmatched WHERE kind = 'suspect' "
        "ORDER BY dataset, id_type, source_id"
    ).fetchall()
    lines += ["", f"## Suspect links (check these first): {len(rows):,}", ""]
    lines += (
        _md_table(
            ["dataset", "id type", "id", "gsis_id", "name", "first", "last", "rows", "evidence"],
            rows,
        )  # fmt: skip
        if rows
        else ["None."]
    )
    lines += ["", "## Waiver Radar candidate pool (preseason `rp` rankings) without a gsis_id", ""]
    rows = q(
        "SELECT season_min, position, name, source_id FROM report_id_unmatched "
        "WHERE kind = 'unmatched' AND dataset = ? ORDER BY season_min, position, name, source_id",
        [POOL_LABEL],
    ).fetchall()
    lines += _md_table(["season", "pos", "name", "fantasypros id"], rows) if rows else ["None."]
    lines += ["", f"## Top {top} unmatched QB/RB/WR/TE ids per dataset (by rows)", ""]
    rows = q(
        f"""
        SELECT dataset, id_type, source_id, name, position, season_min, season_max, n_rows
        FROM (SELECT *, row_number() OVER (PARTITION BY dataset
                                           ORDER BY n_rows DESC, id_type, source_id) AS rn
              FROM report_id_unmatched
              WHERE kind = 'unmatched' AND position IN ({fantasy}) AND dataset <> ?)
        WHERE rn <= {int(top)} ORDER BY dataset, rn""",
        [POOL_LABEL],
    ).fetchall()
    lines += (
        _md_table(["dataset", "id type", "id", "name", "pos", "first", "last", "rows"], rows)
        if rows
        else ["None."]
    )
    for kind, title in (
        ("ambiguous", "Ambiguous ids (no link)"),
        ("conflict", "Conflicts (sources disagree; precedence picked one)"),
        ("multiple_ids", "Players with several ids of one type"),
    ):
        rows = q(
            "SELECT id_type, source_id, gsis_id, name, detail FROM report_id_unmatched "
            "WHERE kind = ? ORDER BY id_type, source_id",
            [kind],
        ).fetchall()
        lines += ["", f"## {title}: {len(rows):,}", ""]
        lines += (
            _md_table(["id type", "id", "gsis_id", "name", "detail"], rows) if rows else ["None."]
        )
    # one line per linked ff_playerids row (all its ids share the evidence); only rows that
    # link a fantasy-site id or a QB/RB/WR/TE player, a position mismatch first
    types = ", ".join(f"'{t}'" for t in NAME_LINK_ID_TYPES)
    rows = q(
        f"""
        SELECT name, gsis_id, detail, string_agg(id_type || ' ' || source_id, ', '
                                                 ORDER BY id_type, source_id)
        FROM report_id_unmatched WHERE kind = 'name_link'
        GROUP BY name, gsis_id, detail
        HAVING bool_or(id_type IN ({types})) OR bool_or(position IN ({fantasy}))
        ORDER BY detail NOT LIKE '%position group differs%', name, gsis_id, detail"""
    ).fetchall()
    n_links = kinds.get("name_link", 0)
    lines += [
        "",
        f"## Name-pass links (spot-check these): {n_links:,} ids; {len(rows):,} rows listed",
        "",
        "Listed: links of a FantasyPros, Sleeper or Yahoo id, or of a QB/RB/WR/TE player "
        "(position-group mismatches first). All of them: `SELECT * FROM report_id_unmatched "
        "WHERE kind = 'name_link'`.",
        "",
    ]
    lines += _md_table(["player", "gsis_id", "evidence", "ids linked"], rows) if rows else ["None."]
    lines += [
        "",
        "## Coverage",
        "",
        "Match rate = share of rows whose id maps to a `gsis_id` (for `gsis` ids: the id is "
        "in `dim_player`). `fantasy` = QB/RB/WR/TE rows (datasets with a position).",
        "",
    ]
    rows = q(
        "SELECT dataset, id_type, scope, season, n_rows, n_rows_unmatched, n_ids, "
        "n_ids_unmatched, match_rate FROM report_id_coverage "
        "ORDER BY dataset, id_type, scope, season"
    ).fetchall()
    lines += _md_table(
        ["dataset", "id type", "scope", "season", "rows", "rows unmatched", "ids",
         "ids unmatched", "match rate"],
        rows,
    )  # fmt: skip
    return "\n".join(lines) + "\n"


def summary_lines(con: duckdb.DuckDBPyConnection) -> list[str]:
    """A few lines for the terminal after ``twm ids``."""
    out = [doctor_line(con) or "ids: no report tables"]
    rows = con.execute(
        "SELECT dataset, min(match_rate), arg_min(season, (match_rate, season)), "
        "sum(n_rows_unmatched), sum(n_rows) FROM report_id_coverage WHERE scope = 'fantasy' "
        "GROUP BY 1 ORDER BY 1"
    ).fetchall()
    for dataset, low, season, unmatched, n in rows:
        out.append(
            f"  {dataset:36s} fantasy rows unmatched {unmatched:>8,} of {n:>10,} "
            f"(lowest season {season}: {_pct(low)})"
        )
    return out
