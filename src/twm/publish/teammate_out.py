"""The Teammate-out list as ``twm publish`` writes it (feature #5; docs/teammate_out.md). Reads
only.

Sources (opened read-only):

- **snapshots**: the predictions store's ``teammate_out_snapshots`` (written by ``twm
  teammate_out weekly``), every stored (season, week, as_of), published append-only
  (:data:`twm.publish.tables.TEAMMATE_OUT`): one ``teammate_out_list`` row per snapshot and its
  teammates in ``teammate_out_row`` (the absent starters ride along on each row: ``out_*``).
  Never rewritten once published;
- **the pinned table** (:func:`twm.modules.teammate_out.production.load_pinned`, sha256
  checked before it is read): its version row (``params`` carries the choice and its
  disclosure: ``chosen``, ``rule_choice``, ``chosen_by``, ``rule``), the allocation table, the
  four candidates' walk-forward backtest (``chosen`` = the one used, ``rule_pick`` = the
  pre-set rule's), the 80% range coverage and the event counts;
- **live record** (``teammate_out_live``): the pinned season's graded snapshots
  (:func:`twm.modules.teammate_out.weekly.summary`) with the warehouse's player-games of the
  season (``weekly.read_actuals``). The publish grades every snapshot of the season the target
  holds afterwards, the published ones included (:func:`live_record`): the nightly runner's
  store holds only the night's snapshot.

No FantasyPros data and no league data: everything here is public.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from twm.publish.collect import GSIS_PATTERN, PublishInputError
from twm.publish.questionable import UTC_TS, _empty, _utc
from twm.publish.tables import TABLES, TEAMMATE_OUT

MODULE = "teammate_out"
TITLES = {"nothing": "Nothing changes", "pro_rata": "Pro rata", "group": "Group",
          "role": "Role"}  # fmt: skip
# teammate_out_row's columns the live record grades (weekly.grade; kickoff = kickoff_utc)
GRADED = ("season", "week", "as_of", "team", "gsis_id", "kickoff", "out_ids", "base_points",
          "base_carry_share", "base_target_share", "pred_carry_share", "pred_target_share",
          "pred_points", "points_lo", "points_hi", "pred_gain")  # fmt: skip
ACTUALS = {"season": pl.Int32, "week": pl.Int32, "team": pl.String, "gsis_id": pl.String,
           "played": pl.Boolean, "carry_share": pl.Float64, "target_share": pl.Float64,
           "points": pl.Float64}  # fmt: skip
LIVE_COUNTS = ("n", "pending", "starter_played", "did_not_play", "weeks", "team_weeks", "ranged")


@dataclass
class TeammateOutData:
    """The module's part of a publish: ``lists`` / ``rows`` (every stored snapshot; append-only
    in the target), ``tables`` (its replaced tables by name), ``versions`` (the pinned table's
    model_versions row, published layout), ``names`` ((gsis_id, name) of the teammates and the
    absent starters, for dim_player) and the pinned ``season``."""

    season: int
    version: str
    lists: pl.DataFrame
    rows: pl.DataFrame
    tables: dict[str, pl.DataFrame] = field(default_factory=dict)
    versions: pl.DataFrame | None = None
    names: pl.DataFrame | None = None
    # the season's player-games (weekly.read_actuals): grade the live record at publish
    actuals: pl.DataFrame = field(default_factory=lambda: pl.DataFrame(schema=ACTUALS))


def snapshot_frames(snaps: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(teammate_out_list, teammate_out_row) of the store's snapshot rows (weekly.COLUMNS): one
    list per (season, week, as_of); its rows by kickoff, team, then predicted points (highest
    first)."""
    if snaps.is_empty():
        return _empty(TEAMMATE_OUT.lists), _empty(TEAMMATE_OUT.rows)
    df = _utc(snaps.rename({"kickoff_utc": "kickoff"}), "as_of", "kickoff", "created_at")
    df = df.with_columns(pl.col(c).cast(pl.Int32) for c in ("season", "week", "n_out",
                                                             "base_games"))  # fmt: skip
    key = list(TEAMMATE_OUT.key)
    outs = (
        df.unique([*key, "team"])
        .group_by(key)
        .agg(pl.len().cast(pl.Int32).alias("n_teams"), pl.col("n_out").sum().cast(pl.Int32))
    )
    lists = df.group_by(key).agg(
        pl.col("model_version").unique().alias("_v"), pl.col("source").unique().alias("_s"),
        pl.col("created_at").max().alias("generated_at"),
        pl.len().cast(pl.Int32).alias("n_players"),
    ).join(outs, on=key).sort(key)  # fmt: skip
    odd = lists.filter((pl.col("_v").list.len() != 1) | (pl.col("_s").list.len() != 1))
    if odd.height:
        r = odd.row(0, named=True)
        raise PublishInputError(f"the Teammate-out snapshot {r['season']} week {r['week']} as of "
                                f"{r['as_of']} mixes model versions or sources")  # fmt: skip
    lists = lists.with_columns(pl.col("_v").list.first().alias("model_version"),
                               pl.col("_s").list.first().alias("source"))  # fmt: skip
    rows = df.sort([*key, "kickoff", "team", "pred_points", "gsis_id"],
                   descending=[False] * 5 + [True, False], nulls_last=True)  # fmt: skip
    return (lists.select(TABLES[TEAMMATE_OUT.lists].names),
            rows.select(TABLES[TEAMMATE_OUT.rows].names))  # fmt: skip


def out_names(snaps: pl.DataFrame) -> pl.DataFrame:
    """(gsis_id, name) of every teammate and absent starter of the store's snapshot rows: the
    absent starters' ids and names are split from out_ids (',') / out_players (', '), in the
    same order (a name list of another length is ignored: the id is kept, unnamed)."""
    schema = {"gsis_id": pl.String, "name": pl.String}
    if snaps.is_empty():
        return pl.DataFrame(schema=schema)
    mates = snaps.select("gsis_id", pl.col("player").cast(pl.String).alias("name"))
    o = snaps.select("out_ids", "out_players").unique().with_columns(
        pl.col("out_ids").str.split(",").alias("gsis_id"),
        pl.col("out_players").fill_null("").str.split(", ").alias("name"))  # fmt: skip
    same = o.filter(pl.col("gsis_id").list.len() == pl.col("name").list.len())
    named = same.select("gsis_id", "name").explode("gsis_id", "name", empty_as_null=True)
    ids = o.select(pl.col("gsis_id").explode(empty_as_null=True)).with_columns(
        pl.lit(None, pl.String).alias("name")
    )
    return pl.concat([mates, named, ids]).unique(maintain_order=True)


def _span(seasons: list[int]) -> str:
    return f"{min(seasons)}-{max(seasons)}" if seasons else ""


def pin_tables(content: dict[str, Any]) -> dict[str, pl.DataFrame]:
    """teammate_out_allocation, _backtest (title, chosen = the candidate used, rule_pick = the
    pre-set rule's), _coverage and _events from the pinned JSON's content (as
    reports/teammate_out/*.csv)."""
    from twm.modules.teammate_out import production as tp

    fr = tp.report_frames(content)
    ints = pl.Int32
    alloc = fr["allocation"].with_columns(
        pl.col("n", "n_group").cast(ints),
        pl.col("carry", "target", "carry_group", "target_group").cast(pl.Float64))  # fmt: skip
    bt = fr["backtest"].with_columns(
        pl.col("candidate").replace_strict(TITLES, default=pl.col("candidate")).alias("title"),
        (pl.col("candidate") == str(content["rule_choice"])).alias("rule_pick"),
        pl.col("season").cast(pl.String), pl.col("n", "events").cast(ints),
        pl.col("mae_carry", "mae_target", "mae_points", "top_hit").cast(pl.Float64),
    )  # fmt: skip
    tests = [int(s) for s in content["test_seasons"]]
    cov = fr["coverage"].with_columns(
        pl.lit(_span(tests[1:])).alias("seasons"),
        pl.col("n", "below", "above", "inside").cast(ints),
        pl.col("coverage").cast(pl.Float64),
    )
    ev = fr["events"].with_columns(
        pl.lit(str(content["train_seasons"])).alias("seasons"),
        pl.col("sat", "kept", "no_roster_row", "gone_status", "events", "single", "multi",
               "teammate_rows").cast(ints))  # fmt: skip
    names = ("teammate_out_allocation", "teammate_out_backtest", "teammate_out_coverage",
             "teammate_out_events")  # fmt: skip
    return {n: f.select(TABLES[n].names) for n, f in zip(names, (alloc, bt, cov, ev), strict=True)}


PARAMS = ("chosen", "rule_choice", "chosen_by", "rule", "path", "candidates", "pseudo_count",
          "ppo_pseudo", "carry_out", "train_seasons", "test_seasons", "event_rule")  # fmt: skip


def version_row(content: dict[str, Any], created_at: datetime) -> pl.DataFrame:
    """The pinned table's model_versions row (published layout, collect.version_rows): its
    ``params`` carry the choice and its disclosure (:data:`PARAMS`, the 80% range's settings)."""
    from twm.modules.teammate_out import production as tp

    first, last = (int(x) for x in str(content["train_seasons"]).split("-"))
    params = {k: content[k] for k in PARAMS}
    params["ranges"] = {k: v for k, v in content["ranges"].items() if k != "cells"}
    return pl.DataFrame(
        [{"model_version": str(content["model_version"]), "module": MODULE, "model": tp.MODEL,
          "label": tp.LABEL, "training_seasons": list(range(first, last + 1)),
          "test_season": int(content["season"]), "feature_list": ["out_position", "role"],
          "params": json.dumps(params, sort_keys=True), "created_at": created_at}],
        schema={"model_version": pl.String, "module": pl.String, "model": pl.String,
                "label": pl.String, "training_seasons": pl.List(pl.Int32),
                "test_season": pl.Int32, "feature_list": pl.List(pl.String),
                "params": pl.String, "created_at": UTC_TS},
    )  # fmt: skip


def live_rows(graded: pl.DataFrame, season: int) -> pl.DataFrame:
    """teammate_out_live from weekly.grade's rows: weekly.summary, plus the weeks with a graded
    teammate (``weeks``). One row for ``season`` (n = 0 and NULL errors before any grade)."""
    from twm.modules.teammate_out import weekly as wk

    record = wk.summary(graded)
    weeks = 0
    if graded.height and "outcome" in graded.columns:
        weeks = graded.filter(pl.col("outcome").is_not_null()).get_column("week").n_unique()
    row = {c: record.get(c) for c in TABLES["teammate_out_live"].names}
    row.update({"season": int(season), "weeks": int(weeks)})
    for c in LIVE_COUNTS:
        row[c] = int(row[c] or 0)
    types = {"integer": pl.Int32, "double precision": pl.Float64}
    return pl.DataFrame([row], schema={c: types[t] for c, t in TABLES["teammate_out_live"].columns})


def published_rows(conn: Any, season: int) -> pl.DataFrame:
    """The ``teammate_out_row`` rows of ``season`` already in the target (``conn``: an open
    psycopg connection; reads only), the columns the live record grades (:data:`GRADED`)."""
    cols = ", ".join(f'"{c}"' for c in GRADED)
    got = conn.execute(f'SELECT {cols} FROM "{TEAMMATE_OUT.rows}" WHERE season = %s',
                       [int(season)]).fetchall()  # fmt: skip
    return pl.DataFrame(got, schema=no_published().schema, orient="row")


def no_published() -> pl.DataFrame:
    """:func:`published_rows` of a target holding no snapshot."""
    return _empty(TEAMMATE_OUT.rows).select(GRADED)


def record_snapshots(local: pl.DataFrame, published: pl.DataFrame, season: int) -> pl.DataFrame:
    """The snapshot rows of ``season`` the live record grades (weekly.grade's layout): the
    ``published`` rows plus the ``local`` ones (teammate_out_row layout) of the snapshots not
    published yet. Append-only: a published (season, week, as_of) wins over a local one."""
    key, cols, this = list(TEAMMATE_OUT.key), list(GRADED), pl.col("season") == int(season)
    published, mine = published.filter(this).select(cols), local.filter(this).select(cols)
    new = mine.join(published.select(key).unique(), on=key, how="anti")
    both = pl.concat([published, new], how="vertical_relaxed")
    both = both.unique([*key, "team", "gsis_id"], keep="first", maintain_order=True)
    return both.rename({"kickoff": "kickoff_utc"}).sort(*key, "team", "gsis_id")


def live_record(d: TeammateOutData, published: pl.DataFrame) -> pl.DataFrame:
    """``teammate_out_live`` as the target will hold it after the publish: :func:`live_rows`
    of :func:`record_snapshots` (``d.rows`` and the target's ``published`` rows of
    ``d.season``), graded with the season's player-games (``d.actuals``)."""
    from twm.modules.teammate_out import weekly as wk

    snaps = record_snapshots(d.rows, published, d.season)
    actuals = d.actuals.with_columns(pl.col("season", "week").cast(pl.Int32))
    return live_rows(wk.grade(snaps, actuals), d.season)


def collect_teammate_out(store: Path, warehouse: Path, season: int,
                         now: datetime) -> TeammateOutData:  # fmt: skip
    """Everything the module publishes (module docstring); the warehouse is opened read-only
    (the season's player-games only)."""
    from twm import pins
    from twm.modules.teammate_out import production as tp
    from twm.modules.teammate_out import weekly as wk

    try:
        spec, pin = tp.load_pinned(season)
    except pins.PinError as e:
        raise PublishInputError(f"the Teammate-out table cannot be read: {e}") from e
    snaps = wk.read_snapshots(store)
    lists, rows = snapshot_frames(snaps)
    actuals = wk.read_actuals(warehouse, int(season)).select(
        pl.col(c).cast(t) for c, t in ACTUALS.items())  # fmt: skip
    approved = datetime.fromisoformat(pin.approved).replace(tzinfo=UTC) if pin.approved else now
    d = TeammateOutData(season=int(season), version=spec.model_version, lists=lists, rows=rows,
                        tables=pin_tables(spec.content), names=out_names(snaps),
                        versions=version_row(spec.content, approved.astimezone(UTC)),
                        actuals=actuals)  # fmt: skip
    # the local store's record; the publish regrades it with the target's snapshots (a fresh
    # runner's store holds only the night's): write.publish, live_record
    d.tables["teammate_out_live"] = live_record(d, no_published())
    return d


def out_ids(rows: pl.DataFrame) -> pl.DataFrame:
    """(gsis_id) of every absent starter named by ``rows`` (teammate_out_row layout)."""
    return rows.select(
        pl.col("out_ids").str.split(",").explode(empty_as_null=True).alias("gsis_id")
    ).unique()


def problems(d: TeammateOutData, teams: set[str], players: set[str],
             versions: set[str]) -> list[str]:  # fmt: skip
    """Plain-English problems of the module's rows (empty = publishable)."""
    from twm.publish.collect import _team_problems

    out: list[str] = []
    rows, key = d.rows, list(TEAMMATE_OUT.key)
    if rows.select([*key, "team", "gsis_id"]).is_duplicated().any():
        out.append("a Teammate-out snapshot lists a teammate twice")
    sizes = rows.group_by(key).agg(pl.len().cast(pl.Int32).alias("_n"))
    if d.lists.join(sizes, on=key, how="full", coalesce=True).filter(
            pl.col("_n").is_null() | (pl.col("_n") != pl.col("n_players"))).height:  # fmt: skip
        out.append("a Teammate-out snapshot's teammate count differs from its rows")
    if rows.filter(pl.col("kickoff").is_null() | pl.col("role").is_null()).height:
        out.append("some Teammate-out rows have no kickoff or no role")
    if rows.filter(~pl.col("position").is_in(["RB", "WR", "TE"])).height:
        out.append("some Teammate-out rows are not a RB, WR or TE")
    n_ids = pl.col("out_ids").str.split(",").list.len()
    if rows.filter(pl.col("out_ids").is_null() | (n_ids != pl.col("n_out"))).height:
        out.append("some Teammate-out rows' absent starters (out_ids) differ from n_out")
    ids = out_ids(rows.drop_nulls("out_ids")).get_column("gsis_id")
    if (~ids.str.contains(GSIS_PATTERN.pattern)).any():
        out.append("some Teammate-out absent starters are not gsis ids")
    lo, hi, p = pl.col("points_lo"), pl.col("points_hi"), pl.col("pred_points")
    bad = rows.filter((lo < 0) | (lo > p) | (p > hi) | p.is_null() | p.is_nan())
    if bad.height:
        out.append(f"{bad.height} Teammate-out rows have points outside their 80% range")
    out += _team_problems("Teammate-out rows", rows, teams)
    out += _team_problems("Teammate-out rows", rows, teams, "opponent", required=False)
    named = set(rows.get_column("gsis_id").to_list()) | set(ids.to_list())
    if named - players:
        out.append("some Teammate-out rows name a player missing from dim_player")
    unknown = set(d.lists.get_column("model_version").to_list()) - versions
    if unknown:
        out.append(f"Teammate-out snapshots of unknown model versions: {sorted(unknown)[:3]}")
    return out


def week_meta(warehouse: Path, now: datetime) -> dict[str, str]:
    """site_meta's ``teammate_out_season`` / ``teammate_out_week``: the regular-season week
    whose games are next at ``now`` (``twm teammate_out weekly``'s default); empty outside the
    season's weeks."""
    from twm.modules.questionable.cli import game_week

    try:
        season, week = game_week(warehouse, now.astimezone(UTC))
    except LookupError:
        return {"teammate_out_season": "", "teammate_out_week": ""}
    return {"teammate_out_season": str(season), "teammate_out_week": str(week)}
