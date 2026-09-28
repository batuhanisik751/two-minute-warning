"""What ``twm publish`` writes, gathered from local files (step E2). Reads only; writes nothing.

Sources (all opened read-only):

- the **predictions store** (``data/predictions.duckdb``): the Waiver Radar lists. For the
  winner (``logit`` for ``y_hit``, :mod:`twm.modules.waiver_radar.production`): the walk-forward
  backtest lists of the evaluation seasons (2014-2025; no chance, no reasons: the backtest never
  made them) and every list of the current season (C6: live or reconstructed, with the chance,
  its range, the priority and the reasons). Of each list the top 25 are published, plus the
  pool size. The priority table (``tier_stats``) is rebuilt from the same backtest exactly as
  the weekly report builds it (:func:`twm.modules.waiver_radar.confidence.from_store`);
- the **dataset** (``data/waiver_radar/dataset.parquet``): each pick's team (the team at the
  as-of, as in the weekly report) and the outcomes (labels and the weekly finishes in the
  window). Outcomes are published for every pick and for every dataset row of the current
  season (a superset of each week's pool), so a live list made earlier, which may no longer be
  in the local store, still finds its outcomes;
- the **warehouse**: players, teams and the weekly player summary for the player pages;
- the committed **evaluation CSV** (``reports/waiver_radar/evaluation.csv``): the track record,
  published row for row, so the site's numbers are the report's numbers;
- the **registry** (:mod:`twm.registry`): the glossary.

Never read or published: the owner's league (``twm.league``; spec rule 9), logos (rule 10),
any player id other than ``gsis_id`` (other ids stay in the warehouse).
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from twm.config import FANTASY_POSITIONS

MODULE = "waiver_radar"
TOP_N = 25  # published picks per list (the weekly report's top 25: confidence.TOP_N)
# nflverse gsis ids: "00-0034796" (most players) or "BAT138483" (older ids; checked: every
# dim_player id in the 2026-09-28 warehouse matches one of the two)
GSIS_PATTERN = re.compile(r"^(00-\d{7}|[A-Z]{3}\d{6})$")
KINDS = ("live", "backtest")
# Player ids from other systems (and league data) that must never reach a published table.
FORBIDDEN_COLUMNS = frozenset({
    "player_id", "pfr_id", "pfr_player_id", "espn_id", "sleeper_id", "fantasypros_id",
    "yahoo_id", "sportradar_id", "mfl_id", "rotowire_id", "nfl_id", "headshot_url",
    "owned_avg", "owned_espn", "league_id", "espn_s2", "swid",
})  # fmt: skip

LIST_SCHEMA: dict[str, Any] = {
    "season": pl.Int32, "week": pl.Int32, "position": pl.String, "kind": pl.String,
    "as_of": pl.Datetime("us", "UTC"), "model_version": pl.String,
    "generated_at": pl.Datetime("us", "UTC"), "incomplete": pl.Boolean, "n_pool": pl.Int32,
    "note": pl.String,
}  # fmt: skip
PICK_SCHEMA: dict[str, Any] = {
    "season": pl.Int32, "week": pl.Int32, "position": pl.String, "kind": pl.String,
    "rank": pl.Int32, "gsis_id": pl.String, "team": pl.String, "chance": pl.Float64,
    "chance_low": pl.Float64, "chance_high": pl.Float64, "model_prob": pl.Float64,
    "tier": pl.String, "reasons": pl.List(pl.String),
}  # fmt: skip
OUTCOME_COLUMNS = (
    "season", "week", "gsis_id", "y_hit", "y_sustained", "label_status", "window_weeks",
    "window_ranks", "window_points",
)  # fmt: skip


class PublishInputError(ValueError):
    """A local input is missing or inconsistent: nothing can be published."""


@dataclass(frozen=True)
class Inputs:
    """Where the local inputs are, the current season and the clock (aware UTC)."""

    warehouse: Path
    store: Path
    dataset: Path
    evaluation_csv: Path
    season: int
    now: datetime

    @classmethod
    def default(cls, now: datetime | None = None) -> Inputs:
        from twm.config import ROOT, settings

        s = settings()
        return cls(
            warehouse=s.path("warehouse"),
            store=s.path("predictions"),
            dataset=ROOT / "data" / "waiver_radar" / "dataset.parquet",
            evaluation_csv=ROOT / "reports" / "waiver_radar" / "evaluation.csv",
            season=s.current_season,
            now=now or datetime.now(UTC),
        )


@dataclass
class PublishData:
    """Everything a publish writes, before it meets the target."""

    season: int
    now: datetime
    lists: pl.DataFrame  # LIST_SCHEMA
    picks: pl.DataFrame  # PICK_SCHEMA
    # outcome rows of every dataset row (the writer keeps those the published picks need)
    outcome_source: pl.DataFrame
    # (season, week, gsis_id) whose outcomes are always published (picks + current season)
    outcome_keys: pl.DataFrame
    tables: dict[str, pl.DataFrame]  # model_versions, dim_team, dim_player, track_record,
    # tier_stats, player_week_summary, glossary (in tables.TABLES column order)
    meta: dict[str, str]
    data_as_of: datetime | None
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def _as_utc(df: pl.DataFrame, *cols: str) -> pl.DataFrame:
    """Timestamp columns as aware UTC: naive ones are UTC already (the store's and the
    warehouse's convention), aware ones are converted."""
    exprs = []
    for c in cols:
        dtype = df.schema[c]
        if isinstance(dtype, pl.Datetime) and dtype.time_zone is not None:
            exprs.append(pl.col(c).dt.convert_time_zone("UTC"))
        else:
            exprs.append(pl.col(c).dt.replace_time_zone("UTC"))
    return df.with_columns(exprs)


def _duck(path: Path):
    import duckdb

    if not path.exists():
        raise PublishInputError(f"not found: {path}")
    con = duckdb.connect(str(path), read_only=True)
    con.execute("SET TimeZone='UTC'")
    return con


def _band(text: str | None) -> tuple[float | None, float | None, float | None]:
    if not text:
        return None, None, None
    b = json.loads(text)
    return float(b["chance"]), float(b["lo"]), float(b["hi"])


def _reasons(text: str | None) -> list[str]:
    return [str(r["text"]) for r in json.loads(text or "[]")]


# --------------------------------------------------------------------------------------
# Waiver Radar lists
# --------------------------------------------------------------------------------------


def store_list_rows(store: Path, season: int) -> pl.DataFrame:
    """Every stored prediction row of the lists a publish may carry: the winner's current
    versions of the evaluation seasons up to ``season``, and every 'live' row of it."""
    from twm import predictions as pr
    from twm.modules.waiver_radar.backtest import eval_seasons
    from twm.modules.waiver_radar.production import LABEL, MODEL

    if not store.exists():
        raise PublishInputError(
            f"predictions store not found: {store}; run `uv run twm radar backtest` (and "
            "`uv run twm radar score` for the current season's lists)"
        )
    first, _ = eval_seasons()
    current = pr.current_versions(store, MODULE, LABEL).filter(
        (pl.col("model") == MODEL) & pl.col("test_season").is_between(first, season)
    )
    con = _duck(store)
    try:
        cols = {r[0] for r in con.execute("DESCRIBE predictions").fetchall()}
        extra = ", ".join(f"p.{c}" if c in cols else f"NULL AS {c}" for c in ("tier", "incomplete"))
        ids = current.get_column("model_version").to_list() or [""]
        marks = ", ".join("?" for _ in ids)
        df = con.execute(
            f"""
            SELECT p.season, p.week, p.rank_group AS position, p.kind, p.rank,
                   p.entity_id AS gsis_id, p.score, p.band, p.reasons_json, {extra}, p.as_of,
                   p.created_at, p.model_version,
                   p.model_version IN ({marks}) AS is_current
            FROM predictions p JOIN model_versions v USING (model_version)
            WHERE p.module = ? AND v.model = ? AND v.label = ? AND p.season BETWEEN ? AND ?
              AND (p.kind = 'live' OR p.model_version IN ({marks}))
            """,
            [*ids, MODULE, MODEL, LABEL, first, season, *ids],
        ).pl()
    finally:
        con.close()
    return df.with_columns(
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        pl.col("rank").cast(pl.Int32),
        pl.col("incomplete").cast(pl.Boolean).fill_null(False),
        pl.col("tier").cast(pl.String),
    )


def choose_lists(rows: pl.DataFrame) -> pl.DataFrame:
    """One model version per (season, week, position, kind): the latest written (the most
    recent ``created_at``), then the store's current version, then the larger version id."""
    key = ["season", "week", "position", "kind"]
    if rows.height == 0:
        return rows
    per_version = rows.group_by([*key, "model_version"]).agg(
        pl.col("created_at").max().alias("_written"), pl.col("is_current").any().alias("_cur")
    )
    chosen = (
        per_version.sort(
            [*key, "_written", "_cur", "model_version"], descending=[False] * 4 + [True, True, True]
        )  # fmt: skip
        .group_by(key, maintain_order=True)
        .first()
        .select(*key, "model_version")
    )
    return rows.join(chosen, on=[*key, "model_version"], how="inner")


def radar_lists(
    rows: pl.DataFrame, dataset_info: pl.DataFrame, notes_by_season: dict[int, dict[str, str]]
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(lists, picks) from the chosen store rows: one list row per (season, week, position,
    kind) and its top ``TOP_N`` picks, each with its team from the dataset."""
    key = ["season", "week", "position", "kind"]
    if rows.height == 0:
        return pl.DataFrame(schema=LIST_SCHEMA), pl.DataFrame(schema=PICK_SCHEMA)
    lists = _as_utc(
        rows.group_by(key).agg(
            pl.col("as_of").max(),
            pl.col("model_version").first(),
            pl.col("created_at").max().alias("generated_at"),
            pl.col("incomplete").any(),
            pl.len().cast(pl.Int32).alias("n_pool"),
        ),
        "as_of",
        "generated_at",
    )
    notes = [
        notes_by_season.get(int(s), {}).get(p)
        for s, p in zip(lists.get_column("season"), lists.get_column("position"), strict=True)
    ]
    lists = lists.with_columns(pl.Series("note", notes, dtype=pl.String))
    top = rows.filter(pl.col("rank") <= TOP_N)
    bands = [_band(b) for b in top.get_column("band").to_list()]
    top = top.with_columns(
        pl.Series("chance", [b[0] for b in bands], dtype=pl.Float64),
        pl.Series("chance_low", [b[1] for b in bands], dtype=pl.Float64),
        pl.Series("chance_high", [b[2] for b in bands], dtype=pl.Float64),
        pl.Series(
            "reasons",
            [_reasons(r) for r in top.get_column("reasons_json").to_list()],
            dtype=pl.List(pl.String),
        ),
        pl.col("score").alias("model_prob"),
    ).join(
        dataset_info.select("season", "week", "gsis_id", "team").unique(
            ["season", "week", "gsis_id"], keep="first", maintain_order=True
        ),
        on=["season", "week", "gsis_id"],
        how="left",
    )
    picks = top.select(list(PICK_SCHEMA)).cast(PICK_SCHEMA)  # type: ignore[arg-type]
    order = pl.col("position").replace_strict(
        {p: i for i, p in enumerate(FANTASY_POSITIONS)}, default=99
    )
    lists = lists.select(list(LIST_SCHEMA)).cast(LIST_SCHEMA)  # type: ignore[arg-type]
    return (
        lists.sort("season", "week", order, "kind"),
        picks.sort("season", "week", order, "kind", "rank"),
    )


def position_notes(csv_path: Path, seasons: Sequence[int]) -> dict[int, dict[str, str]]:
    """The weekly report's position notes (e.g. the QB caveat) per season: only for seasons
    the evaluation does not cover (the same rule as the report: a list never quotes outcomes
    after its as-of)."""
    from twm.modules.waiver_radar import confidence as cf
    from twm.modules.waiver_radar.production import LABEL, MODEL

    return {
        int(s): cf.position_notes(csv_path, label=LABEL, model=MODEL, before=int(s))
        for s in sorted(set(seasons))
    }


# --------------------------------------------------------------------------------------
# Dataset: teams and outcomes
# --------------------------------------------------------------------------------------


def read_dataset(path: Path) -> pl.DataFrame:
    cols = ["season", "week", "gsis_id", "name", "team", "position", "in_pool",
            *OUTCOME_COLUMNS[3:]]  # fmt: skip
    if not path.exists():
        raise PublishInputError(f"dataset not found: {path}; run `uv run twm radar dataset`")
    df = pl.read_parquet(path, columns=cols)
    return df.with_columns(pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32))


def outcome_rows(dataset: pl.DataFrame) -> pl.DataFrame:
    return dataset.select(
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        "gsis_id",
        "y_hit",
        "y_sustained",
        "label_status",
        pl.col("window_weeks").cast(pl.List(pl.Int32)),
        pl.col("window_ranks").cast(pl.List(pl.Int32)),
        pl.col("window_points").cast(pl.List(pl.Float64)),
    ).unique(["season", "week", "gsis_id"], keep="first", maintain_order=True)


def check_store_outcomes(store: Path, picks: pl.DataFrame, outcomes: pl.DataFrame) -> list[str]:
    """Problems where the store's final outcome of a published pick differs from the
    dataset's final outcome (one of the two is stale)."""
    if picks.height == 0:
        return []
    con = _duck(store)
    try:
        stored = con.execute(
            "SELECT season, week, entity_id AS gsis_id, y_hit AS s_hit, label_status AS s_status "
            "FROM outcomes WHERE module = ?",
            [MODULE],
        ).pl()
    finally:
        con.close()
    stored = stored.with_columns(pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32))
    both = (
        picks.select("season", "week", "gsis_id")
        .join(stored, on=["season", "week", "gsis_id"], how="inner")
        .join(outcomes, on=["season", "week", "gsis_id"], how="inner")
        .filter(
            (pl.col("s_status") == "final")
            & (pl.col("label_status") == "final")
            & (pl.col("s_hit") != pl.col("y_hit"))
        )
    )
    if both.height == 0:
        return []
    first = both.row(0, named=True)
    return [
        f"{both.height} published picks have a different final outcome in the predictions store "
        f"and the dataset (e.g. {first['gsis_id']} in {first['season']} week {first['week']}); "
        "one of them is stale: rebuild the dataset (`twm radar dataset`) and re-run the backtest"
    ]


# --------------------------------------------------------------------------------------
# Warehouse: players, teams, the weekly player summary, data freshness
# --------------------------------------------------------------------------------------


def dim_team(con) -> pl.DataFrame:
    return con.execute(
        "SELECT team_abbr, team_name, team_nick, team_conf AS conference, "
        "team_division AS division, team_color AS color, team_color2 AS color2 "
        "FROM dim_team WHERE is_current ORDER BY team_abbr"
    ).pl()


def player_week_summary(con, first_season: int) -> pl.DataFrame:
    """One row per (player, season, week): regular season, QB/RB/WR/TE (the week's listed
    position: the stats row's, else the snap-count row's), from ``first_season``. Fantasy
    points with the config scoring (0 when he played without a stat line); snap share from
    snap counts (NULL without a snap-count row); target and carry share (0 when he played
    without a stat line); xFP from ffopportunity re-scored with the config scoring (NULL
    without an ffopportunity row) and FPOE = points - xFP."""
    from twm.scoring import score_sql, xfp_sql

    pos = ", ".join(f"'{p}'" for p in FANTASY_POSITIONS)
    s = int(first_season)
    return con.execute(
        f"""
        WITH st AS (
            SELECT player_id AS gsis_id, season, week, game_id, team, position, target_share,
                   carries, player_display_name, {score_sql()} AS fantasy_points
            FROM fact_player_week
            WHERE season >= {s} AND season_type = 'REG' AND player_id IS NOT NULL
        ), sn AS (
            SELECT gsis_id, season, week, game_id, team, position, offense_pct
            FROM fact_snaps
            WHERE season >= {s} AND game_type = 'REG' AND gsis_id IS NOT NULL
              AND offense_snaps > 0
        ), tc AS (
            SELECT game_id, team, carries AS team_carries FROM fact_team_week
            WHERE season >= {s} AND season_type = 'REG'
        ), xp AS (
            SELECT game_id, posteam AS team, player_id AS gsis_id, {xfp_sql()} AS xfp
            FROM fact_opportunity_week
            WHERE season >= {s} AND season_type = 'REG' AND player_id IS NOT NULL
        ), k AS (
            SELECT game_id, team, gsis_id FROM st WHERE position IN ({pos})
            UNION
            SELECT game_id, team, gsis_id FROM sn WHERE position IN ({pos})
        )
        SELECT k.gsis_id, CAST(COALESCE(st.season, sn.season) AS INTEGER) AS season,
               CAST(COALESCE(st.week, sn.week) AS INTEGER) AS week, k.team,
               CASE WHEN st.position IN ({pos}) THEN st.position ELSE sn.position END
                   AS position,
               round(COALESCE(st.fantasy_points, 0), 2) AS fantasy_points,
               round(sn.offense_pct, 4) AS snap_share,
               round(COALESCE(st.target_share, 0), 4) AS target_share,
               CASE WHEN tc.team_carries > 0
                    THEN round(COALESCE(st.carries, 0) / tc.team_carries, 4) END AS carry_share,
               round(xp.xfp, 2) AS xfp,
               round(COALESCE(st.fantasy_points, 0) - xp.xfp, 2) AS fpoe,
               st.player_display_name
        FROM k
        LEFT JOIN st USING (game_id, team, gsis_id)
        LEFT JOIN sn USING (game_id, team, gsis_id)
        LEFT JOIN tc USING (game_id, team)
        LEFT JOIN xp USING (game_id, team, gsis_id)
        ORDER BY 1, 2, 3
        """
    ).pl()


def dim_player(con, ids: Sequence[str], fallback_names: pl.DataFrame) -> pl.DataFrame:
    """The warehouse's players among ``ids``; a player missing there (or without a name)
    takes his name from ``fallback_names`` (gsis_id, name)."""
    want = pl.DataFrame({"gsis_id": sorted(set(ids))}, schema={"gsis_id": pl.String})
    con.register("want_ids", want.to_arrow())
    try:
        found = con.execute(
            "SELECT p.gsis_id, p.display_name, p.position, p.latest_team AS team, "
            "p.draft_year, p.draft_round, p.draft_pick, p.rookie_season "
            "FROM dim_player p JOIN want_ids w USING (gsis_id)"
        ).pl()
    finally:
        con.unregister("want_ids")
    names = fallback_names.drop_nulls("name").unique("gsis_id", keep="first", maintain_order=True)
    out = (
        want.join(found, on="gsis_id", how="left")
        .join(names, on="gsis_id", how="left")
        .with_columns(pl.coalesce("display_name", "name").alias("display_name"))
        .drop("name")
    )
    return out.with_columns(
        [
            pl.col(c).cast(pl.Int32)
            for c in ("draft_year", "draft_round", "draft_pick", "rookie_season")
        ]  # fmt: skip
    ).sort("gsis_id")


def data_freshness(con, season: int, now: datetime) -> tuple[datetime | None, dict[str, str]]:
    """(data_as_of, meta): the estimated public time of the newest final score in the
    warehouse, the season/week it belongs to, when the warehouse was built, and the current
    week (the latest regular-season week of ``season`` whose Tuesday as-of has passed)."""
    meta: dict[str, str] = {}
    row = con.execute(
        "SELECT available_at, season, week FROM fact_game WHERE result IS NOT NULL "
        "ORDER BY available_at DESC, season DESC, week DESC LIMIT 1"
    ).fetchone()
    data_as_of = None
    if row is not None and row[0] is not None:
        data_as_of = row[0].replace(tzinfo=UTC)
        meta["data_as_of"] = data_as_of.isoformat()
        meta["data_through_season"] = str(row[1])
        meta["data_through_week"] = str(row[2])
    built = con.execute("SELECT max(built_at) FROM build_manifest").fetchone()
    if built is not None and built[0] is not None:
        meta["warehouse_built_at"] = built[0].replace(tzinfo=UTC).isoformat()
    week = con.execute(
        "SELECT max(week) FROM dim_week WHERE season = ? AND season_type = 'REG' "
        "AND asof_weekly_utc <= ?",
        [season, now.astimezone(UTC).replace(tzinfo=None)],
    ).fetchone()
    meta["current_week"] = "" if week is None or week[0] is None else str(week[0])
    return data_as_of, meta


# --------------------------------------------------------------------------------------
# Reports, backtest tiers, model versions, glossary
# --------------------------------------------------------------------------------------


def _seasons(text: str) -> tuple[int, int]:
    parts = str(text).split("-")
    return int(parts[0]), int(parts[-1])


def track_record(csv_path: Path, module: str = MODULE) -> pl.DataFrame:
    """Every row of the evaluation CSV, as published (see web/db/schema.ts)."""
    if not csv_path.exists():
        raise PublishInputError(f"evaluation report not found: {csv_path}")
    ev = pl.read_csv(csv_path, infer_schema_length=0)
    seasons = [_seasons(s) for s in ev.get_column("seasons").to_list()]

    def num(c: str, dtype: Any) -> pl.Expr:
        return pl.col(c).cast(pl.Float64).cast(dtype)

    return ev.select(
        pl.lit(module).alias("module"),
        "model",
        "label",
        "metric",
        "scope",
        pl.col("key").fill_null("").alias("scope_value"),
        pl.Series("season_from", [s[0] for s in seasons], dtype=pl.Int32),
        pl.Series("season_to", [s[1] for s in seasons], dtype=pl.Int32),
        (pl.col("subset") == "without_rostered").alias("excl_rostered"),
        pl.col("value").cast(pl.Float64),
        pl.col("lo").cast(pl.Float64).alias("low"),
        pl.col("hi").cast(pl.Float64).alias("high"),
        num("n_groups", pl.Int32).alias("n_lists"),
        num("n_pos", pl.Int32).alias("n_positives"),
        num("n_rows", pl.Int32).alias("n_rows"),
        num("n_top_hits", pl.Int32).alias("n_top_hits"),
        num("n_top", pl.Int32).alias("n_top"),
    )


def tier_stats(store: Path, season: int) -> pl.DataFrame:
    """The priority table of the weekly report (every week, and each week number alone), from
    the backtest seasons before ``season`` (confidence.seasons_before)."""
    from twm.modules.waiver_radar import confidence as cf
    from twm.modules.waiver_radar.production import LABEL, MODEL

    try:
        conf = cf.from_store(store, model=MODEL, label=LABEL, seasons=cf.seasons_before(season))
    except ValueError as e:
        raise PublishInputError(f"cannot build the priority table: {e}") from e
    cut = conf.cutoffs
    bounds = {
        "must-add": (cf.MUST_ADD, 1.0, cut.get("must-add"), 1.0),
        "speculative": (cf.SPECULATIVE, cf.MUST_ADD, cut.get("speculative"), cut.get("must-add")),
        "watch": (0.0, cf.SPECULATIVE, 0.0, cut.get("speculative")),
    }
    weeks = [0, *sorted({int(w) for w in conf.history.get_column("week").unique().to_list()})]
    rows = []
    for w in weeks:
        table = conf.tiers if w == 0 else conf.week_tiers(w)
        for r in table.iter_rows(named=True):
            c_lo, c_hi, p_lo, p_hi = bounds[r["tier"]]
            rows.append({
                "module": MODULE, "model": MODEL, "label": LABEL, "week": w, "tier": r["tier"],
                "season_from": conf.seasons[0], "season_to": conf.seasons[1],
                "chance_low": c_lo, "chance_high": c_hi, "prob_low": p_lo, "prob_high": p_hi,
                "lists": r["lists"], "players": r["rows"], "hits": r["hits"],
                "hit_rate": r["rate"], "per_list": r["per_list"],
            })  # fmt: skip
    from twm.publish.tables import TABLES

    schema = {
        c: {"text": pl.String, "integer": pl.Int32, "double precision": pl.Float64}[t]
        for c, t in TABLES["tier_stats"].columns
    }
    return pl.DataFrame(rows, schema=schema, orient="row")


def model_versions(store: Path, versions: Sequence[str]) -> pl.DataFrame:
    """The store's rows of ``versions`` (params as JSON, lists as lists)."""
    want = sorted(set(versions))
    if not want:
        return pl.DataFrame(
            schema={"model_version": pl.String, "module": pl.String, "model": pl.String,
                    "label": pl.String, "training_seasons": pl.List(pl.Int32),
                    "test_season": pl.Int32, "feature_list": pl.List(pl.String),
                    "params": pl.String, "created_at": pl.Datetime("us", "UTC")}
        )  # fmt: skip
    con = _duck(store)
    try:
        df = con.execute(
            "SELECT model_version, module, model, label, training_seasons, test_season, "
            "feature_list, params, created_at FROM model_versions WHERE model_version IN ("
            + ", ".join("?" for _ in want)
            + ") ORDER BY model_version",
            want,
        ).pl()
    finally:
        con.close()
    df = _as_utc(df, "created_at")
    return df.with_columns(
        pl.Series(
            "training_seasons",
            [[int(s) for s in json.loads(x)] for x in df.get_column("training_seasons")],
            dtype=pl.List(pl.Int32),
        ),
        pl.col("test_season").cast(pl.Int32),
        pl.Series(
            "feature_list",
            [[str(f) for f in json.loads(x)] for x in df.get_column("feature_list")],
            dtype=pl.List(pl.String),
        ),
    )


def glossary() -> pl.DataFrame:
    from twm import registry

    rows = [
        {"name": e.name, "title": e.title, "kind": e.kind, "unit": e.unit,
         "formula": e.formula, "explanation": e.explanation, "verified": e.verified or None,
         "modules": list(e.modules), "model_output": e.model_output}
        for e in sorted(registry.entries(), key=lambda e: e.name)
    ]  # fmt: skip
    return pl.DataFrame(
        rows,
        schema={"name": pl.String, "title": pl.String, "kind": pl.String, "unit": pl.String,
                "formula": pl.String, "explanation": pl.String, "verified": pl.String,
                "modules": pl.List(pl.String), "model_output": pl.Boolean},
        orient="row",
    )  # fmt: skip


# --------------------------------------------------------------------------------------
# Everything
# --------------------------------------------------------------------------------------


def collect(inputs: Inputs) -> PublishData:
    """Read every local input and build what a publish writes (nothing is written here)."""
    from twm import predictions as pr
    from twm.config import settings

    season = int(inputs.season)
    dataset = read_dataset(inputs.dataset)
    rows = choose_lists(store_list_rows(inputs.store, season))
    notes = position_notes(inputs.evaluation_csv, rows.get_column("season").unique().to_list())
    lists, picks = radar_lists(rows, dataset.select("season", "week", "gsis_id", "team"), notes)
    outcomes = outcome_rows(dataset)
    warnings = check_store_outcomes(inputs.store, picks, outcomes)
    if warnings:
        raise PublishInputError(warnings[0])
    keys = pl.concat([
        picks.select("season", "week", "gsis_id"),
        dataset.filter(pl.col("season") == season).select("season", "week", "gsis_id"),
    ]).unique(maintain_order=True)  # fmt: skip

    con = _duck(inputs.warehouse)
    try:
        teams = dim_team(con)
        first = int(settings().seasons["snaps_start"])
        pws = player_week_summary(con, first)
        ids = keys.get_column("gsis_id").to_list() + pws.get_column("gsis_id").to_list()
        names = pl.concat([
            dataset.select("gsis_id", "name"),
            pws.select("gsis_id", pl.col("player_display_name").alias("name")),
        ])  # fmt: skip
        players = dim_player(con, ids, names)
        data_as_of, meta = data_freshness(con, season, inputs.now)
    finally:
        con.close()

    versions = model_versions(inputs.store, lists.get_column("model_version").to_list())
    code = pr.code_version()
    sha = re.search(r"\(([0-9a-f]+)\)", code)
    meta.update({
        "current_season": str(season),
        "generated_at": inputs.now.astimezone(UTC).isoformat(),
        "code_version": code,
        "git_sha": sha.group(1) if sha else "",
    })  # fmt: skip
    return PublishData(
        season=season,
        now=inputs.now,
        lists=lists,
        picks=picks,
        outcome_source=outcomes,
        outcome_keys=keys,
        tables={
            "model_versions": versions,
            "dim_team": teams,
            "dim_player": players,
            "track_record": track_record(inputs.evaluation_csv),
            "tier_stats": tier_stats(inputs.store, season),
            "player_week_summary": pws.drop("player_display_name"),
            "glossary": glossary(),
        },
        meta=meta,
        data_as_of=data_as_of,
        warnings=warnings,
    )


# --------------------------------------------------------------------------------------
# Validation (before anything is written)
# --------------------------------------------------------------------------------------


def validate(data: PublishData) -> list[str]:
    """Plain-English problems (empty = publishable): see docs/deploy.md "What is checked"."""
    problems: list[str] = []
    lists, picks, t = data.lists, data.picks, data.tables
    # 1. no identifiers other than gsis_id and team codes, no league data
    frames = {"radar_list": lists, "radar_pick": picks, **t}
    for name, df in frames.items():
        bad = sorted(c for c in df.columns if c.lower() in FORBIDDEN_COLUMNS)
        if bad:
            problems.append(f"{name} carries columns that must never be published: {bad}")
    for name, df in frames.items():
        floats = [c for c, d in df.schema.items() if d == pl.Float64]
        nan = [c for c in floats if df.get_column(c).is_nan().any()]
        if nan:
            problems.append(f"{name} has NaN values in {nan}")
    ids = pl.concat([
        picks.select("gsis_id"), t["dim_player"].select("gsis_id"),
        t["player_week_summary"].select("gsis_id"), data.outcome_keys.select("gsis_id"),
    ]).unique()  # fmt: skip
    odd = ids.filter(~pl.col("gsis_id").str.contains(GSIS_PATTERN.pattern))
    if odd.height:
        problems.append(
            f"{odd.height} player ids are not gsis ids (00-nnnnnnn or AAAnnnnnn), e.g. "
            f"{odd.get_column('gsis_id')[0]!r}"
        )
    # 2. lists: valid keys, kinds, positions, pool sizes
    key = ["season", "week", "position", "kind"]
    if lists.select(key).is_duplicated().any():
        problems.append("two lists share a (season, week, position, kind)")
    bad_kind = lists.filter(~pl.col("kind").is_in(KINDS))
    if bad_kind.height:
        problems.append(f"unknown list kinds: {bad_kind.get_column('kind').unique().to_list()}")
    bad_pos = lists.filter(~pl.col("position").is_in(list(FANTASY_POSITIONS)))
    if bad_pos.height:
        problems.append(f"unknown positions: {bad_pos.get_column('position').unique().to_list()}")
    # 3. ranks 1..n contiguous, n = min(TOP_N, pool size)
    per = picks.group_by(key).agg(
        pl.len().alias("n"), pl.col("rank").min().alias("lo"), pl.col("rank").max().alias("hi"),
        pl.col("rank").n_unique().alias("u"), pl.col("gsis_id").n_unique().alias("players"),
    )  # fmt: skip
    joined = lists.select(*key, "n_pool").join(per, on=key, how="left")
    expected = pl.min_horizontal(pl.col("n_pool"), pl.lit(TOP_N))
    broken = joined.filter(
        pl.col("n").is_null() | (pl.col("n") != expected) | (pl.col("lo") != 1)
        | (pl.col("hi") != pl.col("n")) | (pl.col("u") != pl.col("n"))
        | (pl.col("players") != pl.col("n"))
    )  # fmt: skip
    if broken.height:
        r = broken.row(0, named=True)
        problems.append(
            f"{broken.height} lists do not rank their top {TOP_N} as 1..n without gaps or "
            f"repeats (e.g. {r['season']} week {r['week']} {r['position']} {r['kind']})"
        )
    orphans = picks.join(lists.select(key), on=key, how="anti")
    if orphans.height:
        problems.append(f"{orphans.height} picks belong to no published list")
    # 4. probabilities and chances within [0, 1], chance inside its range
    for c in ("model_prob", "chance", "chance_low", "chance_high"):
        out = picks.filter(pl.col(c).is_nan() | (pl.col(c) < 0) | (pl.col(c) > 1))
        if out.height:
            problems.append(f"{out.height} picks have {c} outside [0, 1]")
    if picks.filter(pl.col("model_prob").is_null()).height:
        problems.append("some picks have no model probability")
    band = picks.filter(
        pl.col("chance").is_not_null()
        & ((pl.col("chance_low") > pl.col("chance")) | (pl.col("chance") > pl.col("chance_high")))
    )
    if band.height:
        problems.append(f"{band.height} picks have a chance outside its own range")
    tiers = picks.filter(pl.col("tier").is_not_null() & ~pl.col("tier").is_in(
        ["must-add", "speculative", "watch"]))  # fmt: skip
    if tiers.height:
        problems.append(f"{tiers.height} picks have an unknown priority")
    # 5. every pick's player and team exist
    players = set(t["dim_player"].get_column("gsis_id").to_list())
    teams = set(t["dim_team"].get_column("team_abbr").to_list())
    no_team = picks.filter(pl.col("team").is_null())
    if no_team.height:
        r = no_team.row(0, named=True)
        problems.append(
            f"{no_team.height} picks have no team in the dataset (e.g. {r['gsis_id']} in "
            f"{r['season']} week {r['week']}): rebuild it with `uv run twm radar dataset`"
        )
    unknown_team = picks.filter(pl.col("team").is_not_null() & ~pl.col("team").is_in(teams))
    if unknown_team.height:
        problems.append(
            f"{unknown_team.height} picks name a team that is not a current franchise: "
            f"{sorted(set(unknown_team.get_column('team').to_list()))[:5]}"
        )
    for name, df in (("picks", picks), ("player_week_summary", t["player_week_summary"])):
        missing = df.filter(~pl.col("gsis_id").is_in(players))
        if missing.height:
            problems.append(f"{missing.height} {name} rows name a player missing from dim_player")
    no_name = t["dim_player"].filter(pl.col("display_name").is_null())
    if no_name.height:
        problems.append(
            f"{no_name.height} players have no name anywhere (e.g. "
            f"{no_name.get_column('gsis_id')[0]})"
        )
    pws = t["player_week_summary"]
    if pws.select("gsis_id", "season", "week").is_duplicated().any():
        problems.append("player_week_summary repeats a (player, season, week)")
    bad_pws_team = pws.filter(~pl.col("team").is_in(teams))
    if bad_pws_team.height:
        problems.append(
            f"{bad_pws_team.height} player_week_summary rows name a team that is not a current "
            "franchise"
        )
    # 6. every list's model version is published with it
    known = set(t["model_versions"].get_column("model_version").to_list())
    unknown = set(lists.get_column("model_version").to_list()) - known
    if unknown:
        problems.append(f"lists of unknown model versions: {sorted(unknown)[:3]}")
    return problems
