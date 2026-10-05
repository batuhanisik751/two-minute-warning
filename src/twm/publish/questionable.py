"""The Questionable list as ``twm publish`` writes it (feature #1; docs/questionable.md). Reads
only.

Sources (opened read-only):

- **snapshots**: the predictions store's ``questionable_snapshots`` (written by ``twm
  questionable weekly``), every stored (season, week, as_of), published append-only
  (:data:`twm.publish.tables.QUESTIONABLE`): one ``questionable_list`` row per snapshot and its
  players in ``questionable_row``. Never rewritten once published;
- **the pinned table** (:func:`twm.modules.questionable.production.load_pinned`, sha256
  checked before it is read): its version row, its walk-forward backtest (as
  ``reports/questionable/backtest.csv``: title and chosen added) and its calibration;
- **history** (``questionable_history``): how often tagged players played, per tag and
  practice bucket, from the warehouse's history rows of the pin's training seasons (the module's
  own :mod:`twm.modules.questionable.history`); each tag's total must equal the pin's
  ``status_counts`` (else the warehouse is not the one the table was built from: refused);
- **live record** (``questionable_live``): the pinned season's graded snapshots
  (:func:`twm.modules.questionable.weekly.summary`), per tag.

No FantasyPros data and no league data: everything here is public.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from twm.publish.collect import PublishInputError
from twm.publish.tables import QUESTIONABLE, TABLES

MODULE = "questionable"
LIVE_STATUSES = ("all", "Questionable", "Doubtful")
UTC_TS = pl.Datetime("us", "UTC")


@dataclass
class QuestionableData:
    """The module's part of a publish: ``lists`` / ``rows`` (every stored snapshot; append-only
    in the target), ``tables`` (its replaced tables by name), ``versions`` (the pinned table's
    model_versions row, published layout) and the pinned ``season``."""

    season: int
    version: str
    lists: pl.DataFrame
    rows: pl.DataFrame
    tables: dict[str, pl.DataFrame] = field(default_factory=dict)
    versions: pl.DataFrame | None = None
    names: pl.DataFrame | None = None  # (gsis_id, name) of the snapshots' players (dim_player)


def _utc(df: pl.DataFrame, *cols: str) -> pl.DataFrame:
    """Naive-UTC (the store's) or aware columns -> aware UTC microseconds."""
    out = []
    for c in cols:
        tz = getattr(df.schema[c], "time_zone", None)
        e = pl.col(c).cast(pl.Datetime("us", tz))
        out.append((e.dt.replace_time_zone("UTC") if tz is None else e.dt.convert_time_zone("UTC"))
                   .alias(c))  # fmt: skip
    return df.with_columns(out)


def _empty(name: str) -> pl.DataFrame:
    types = {"integer": pl.Int32, "text": pl.String, "timestamptz": UTC_TS, "boolean": pl.Boolean,
             "double precision": pl.Float64}  # fmt: skip
    return pl.DataFrame(schema={c: types[t] for c, t in TABLES[name].columns})


def history_rows(tagged: pl.DataFrame, seasons: str) -> pl.DataFrame:
    """``questionable_history``: per tag (Out included) and practice bucket, the player-weeks
    and how many played; practice 'all' = the tag's total. ``tagged``: history.read_tagged
    rows (report_status, practice, played)."""
    from twm.modules.questionable import history as hs

    by = tagged.group_by("report_status", "practice").agg(
        pl.len().alias("n"), pl.col("played").cast(pl.Int64).sum().alias("played"))  # fmt: skip
    tot = tagged.group_by("report_status").agg(
        pl.lit("all").alias("practice"), pl.len().alias("n"),
        pl.col("played").cast(pl.Int64).sum().alias("played"))  # fmt: skip
    df = pl.concat([by, tot.select(by.columns)])
    order = {s: i for i, s in enumerate(hs.STATUSES)}
    prac = {p: i for i, p in enumerate(("all", *hs.PRACTICE))}
    return df.with_columns(
        pl.lit(seasons).alias("seasons"),
        pl.col("n").cast(pl.Int32), pl.col("played").cast(pl.Int32),
        (pl.col("played") / pl.col("n")).alias("played_rate"),
        pl.col("report_status").replace_strict(order, return_dtype=pl.Int32).alias("_s"),
        pl.col("practice").replace_strict(prac, return_dtype=pl.Int32).alias("_p"),
    ).sort("_s", "_p").select(TABLES["questionable_history"].names)  # fmt: skip


def check_history(history: pl.DataFrame, status_counts: list[dict[str, Any]]) -> list[str]:
    """Problems when a tag's total differs from the pin's ``status_counts``."""
    got = {r["report_status"]: int(r["n"]) for r in
           history.filter(pl.col("practice") == "all").iter_rows(named=True)}  # fmt: skip
    want = {str(r["report_status"]): int(r["n"]) for r in status_counts}
    return [f"{s}: the warehouse has {got.get(s, 0)} history rows, the pinned table "
            f"{n}" for s, n in sorted(want.items()) if got.get(s, 0) != n]  # fmt: skip


def snapshot_frames(snaps: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(questionable_list, questionable_row) of the store's snapshot rows (weekly.COLUMNS):
    one list per (season, week, as_of), its rows by kickoff, then chance (highest first)."""
    if snaps.is_empty():
        return _empty("questionable_list"), _empty("questionable_row")
    df = _utc(snaps.rename({"kickoff_utc": "kickoff"}), "as_of", "kickoff", "created_at")
    df = df.with_columns(pl.col(c).cast(pl.Int32) for c in ("season", "week", "plays_n",
                                                             "season_games"))  # fmt: skip
    key = list(QUESTIONABLE.key)
    lists = df.group_by(key).agg(
        pl.col("model_version").unique().alias("_v"), pl.col("source").unique().alias("_s"),
        pl.col("created_at").max().alias("generated_at"),
        pl.len().cast(pl.Int32).alias("n_players"),
    ).sort(key)  # fmt: skip
    odd = lists.filter((pl.col("_v").list.len() != 1) | (pl.col("_s").list.len() != 1))
    if odd.height:
        r = odd.row(0, named=True)
        raise PublishInputError(f"the Questionable snapshot {r['season']} week {r['week']} as of "
                                f"{r['as_of']} mixes model versions or sources")  # fmt: skip
    lists = lists.with_columns(pl.col("_v").list.first().alias("model_version"),
                               pl.col("_s").list.first().alias("source"))  # fmt: skip
    rows = df.sort([*key, "kickoff", "play_chance", "gsis_id"],
                   descending=[False] * 4 + [True, False])  # fmt: skip
    return (lists.select(TABLES["questionable_list"].names),
            rows.select(TABLES["questionable_row"].names))  # fmt: skip


def pin_tables(content: dict[str, Any]) -> dict[str, pl.DataFrame]:
    """questionable_backtest (as reports/questionable/backtest.csv) and questionable_calibration
    (line = the CSV's row order) from the pinned JSON's content."""
    from twm.modules.questionable import production as qp

    frames = qp.report_frames(content)
    bt = frames["backtest"].with_columns(
        pl.col("season").cast(pl.String), pl.col("n").cast(pl.Int32),
        *(pl.col(c).cast(pl.Float64) for c in ("log_loss", "brier", "mean_p", "played_rate")),
    )  # fmt: skip
    cal = frames["calibration"].with_columns(
        pl.int_range(1, pl.len() + 1, dtype=pl.Int32).alias("line"), pl.col("n").cast(pl.Int32),
        pl.col("predicted").cast(pl.Float64), pl.col("actual").cast(pl.Float64),
    )  # fmt: skip
    names = ("questionable_backtest", "questionable_calibration")
    return {n: f.select(TABLES[n].names) for n, f in zip(names, (bt, cal), strict=True)}


def version_row(content: dict[str, Any], created_at: datetime) -> pl.DataFrame:
    """The pinned table's model_versions row (published layout, collect.version_rows)."""
    first, last = (int(x) for x in str(content["train_seasons"]).split("-"))
    params = {k: content[k] for k in ("grouping", "keys", "path", "pseudo_count",
                                      "with_practice", "practice_dropped", "check_season",
                                      "plays_rules")}  # fmt: skip
    return pl.DataFrame(
        [{"model_version": str(content["model_version"]), "module": MODULE, "model": "lookup",
          "label": "played", "training_seasons": list(range(first, last + 1)),
          "test_season": int(content["season"]),
          "feature_list": ["report_status", *content["keys"]],
          "params": json.dumps(params, sort_keys=True), "created_at": created_at}],
        schema={"model_version": pl.String, "module": pl.String, "model": pl.String,
                "label": pl.String, "training_seasons": pl.List(pl.Int32),
                "test_season": pl.Int32, "feature_list": pl.List(pl.String),
                "params": pl.String, "created_at": UTC_TS},
    )  # fmt: skip


def live_rows(graded: pl.DataFrame, season: int) -> pl.DataFrame:
    """questionable_live from weekly.grade's rows: weekly.summary per tag ('all' first); the
    'all' row also counts the players still pending and the weeks with a graded player."""
    from twm.modules.questionable import weekly as wk

    record = wk.summary(graded)
    weeks = 0
    if graded.height and "outcome" in graded.columns:
        weeks = graded.filter(pl.col("outcome").is_not_null()).get_column("week").n_unique()
    parts = [("all", record), *((s, record["by_status"][s]) for s in LIVE_STATUSES[1:])]
    return pl.DataFrame(
        [{"season": int(season), "report_status": s, "n": int(r["n"]),
          "predicted": r["predicted"], "actual": r["actual"],
          "pending": int(record["pending"]) if s == "all" else None,
          "weeks": int(weeks) if s == "all" else None} for s, r in parts],
        schema={"season": pl.Int32, "report_status": pl.String, "n": pl.Int32,
                "predicted": pl.Float64, "actual": pl.Float64, "pending": pl.Int32,
                "weeks": pl.Int32},
    )  # fmt: skip


def collect_questionable(store: Path, warehouse: Path, season: int, now: datetime,
                         con: Any = None) -> QuestionableData:  # fmt: skip
    """Everything the module publishes (module docstring); ``con``: an open read-only
    warehouse connection (else ``warehouse`` is opened, read-only)."""
    from twm import pins
    from twm.modules.questionable import history as hs
    from twm.modules.questionable import production as qp
    from twm.modules.questionable import weekly as wk

    try:
        spec, pin = qp.load_pinned(season)
    except pins.PinError as e:
        raise PublishInputError(f"the Questionable table cannot be read: {e}") from e
    c = spec.content
    first, last = (int(x) for x in str(c["train_seasons"]).split("-"))
    own = con is None
    con = hs.connect(warehouse) if own else con
    try:
        tagged = hs.read_tagged(con, list(range(first, last + 1)))
    finally:
        if own:
            con.close()
    history = history_rows(tagged, str(c["train_seasons"]))
    problems = check_history(history, c["status_counts"])
    if problems:
        raise PublishInputError("the Questionable history does not match the pinned table ("
                                + "; ".join(problems) + ")")  # fmt: skip
    snaps = wk.read_snapshots(store)
    lists, rows = snapshot_frames(snaps)
    mine = snaps.filter(pl.col("season") == int(season)) if snaps.height else snaps
    graded = wk.grade(mine, wk.read_game_snaps(warehouse, int(season))) if mine.height else mine
    approved = datetime.fromisoformat(pin.approved).replace(tzinfo=UTC) if pin.approved else now
    tables = {"questionable_history": history, **pin_tables(c),
              "questionable_live": live_rows(graded, int(season))}  # fmt: skip
    names = pl.DataFrame(schema={"gsis_id": pl.String, "name": pl.String})
    if snaps.height:
        names = snaps.select("gsis_id", pl.col("player").cast(pl.String).alias("name"))
    return QuestionableData(season=int(season), version=spec.model_version, lists=lists,
                            rows=rows, tables=tables, names=names,
                            versions=version_row(c, approved.astimezone(UTC)))  # fmt: skip


def problems(d: QuestionableData, teams: set[str], players: set[str],
             versions: set[str]) -> list[str]:  # fmt: skip
    """Plain-English problems of the module's rows (empty = publishable)."""
    from twm.publish.collect import _team_problems

    out: list[str] = []
    rows, key = d.rows, list(QUESTIONABLE.key)
    if rows.select([*key, "gsis_id"]).is_duplicated().any():
        out.append("a Questionable snapshot lists a player twice")
    sizes = rows.group_by(key).agg(pl.len().cast(pl.Int32).alias("_n"))
    if d.lists.join(sizes, on=key, how="full", coalesce=True).filter(
            pl.col("_n").is_null() | (pl.col("_n") != pl.col("n_players"))).height:  # fmt: skip
        out.append("a Questionable snapshot's player count differs from its rows")
    bad = rows.filter(pl.col("play_chance").is_null() | pl.col("play_chance").is_nan()
                      | (pl.col("play_chance") < 0) | (pl.col("play_chance") > 1))  # fmt: skip
    if bad.height:
        out.append(f"{bad.height} Questionable rows have a play_chance outside [0, 1]")
    out += _team_problems("Questionable rows", rows, teams)
    out += _team_problems("Questionable rows", rows, teams, "opponent", required=False)
    if rows.filter(~pl.col("gsis_id").is_in(list(players))).height:
        out.append("some Questionable rows name a player missing from dim_player")
    unknown = set(d.lists.get_column("model_version").to_list()) - versions
    if unknown:
        out.append(f"Questionable snapshots of unknown model versions: {sorted(unknown)[:3]}")
    return out


def week_meta(warehouse: Path, now: datetime) -> dict[str, str]:
    """site_meta's ``questionable_season`` / ``questionable_week``: the regular-season week
    whose games are next at ``now`` (the week the nightly list is for; ``twm questionable
    weekly``'s default); empty outside the season's weeks."""
    from twm.modules.questionable.cli import game_week

    try:
        season, week = game_week(warehouse, now.astimezone(UTC))
    except LookupError:
        return {"questionable_season": "", "questionable_week": ""}
    return {"questionable_season": str(season), "questionable_week": str(week)}
