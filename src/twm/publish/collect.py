"""What ``twm publish`` writes, gathered from local files (step E2). Reads only; writes nothing.

Sources (all opened read-only):

- the **predictions store** (``data/predictions.duckdb``): the Waiver Radar lists. For the
  winner (``logit`` for ``y_hit``, :mod:`twm.modules.waiver_radar.production`): the walk-forward
  backtest lists of the evaluation seasons (2014-2025; their chance, range and priority computed
  walk-forward from the seasons before each list's own, :func:`backtest_chances`; no reasons: the
  backtest never made them) and every list of the current season (C6: live or reconstructed,
  with the chance, its range, the priority and the reasons). Of each list the top 25 are
  published, plus the pool size. The priority table (``tier_stats``) is rebuilt from the same
  backtest exactly as the weekly report builds it
  (:func:`twm.modules.waiver_radar.confidence.from_store`);
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
# tables.FAMILIES, and (step P3) the Decision Report Card (tables.DECISIONS)
MODULES = ("waiver_radar", "streamer", "regression_watch", "decisions", "hot_seat", "board",
           "questionable", "teammate_out", "playoff_planner", "coach_tendencies",
           "lead_time")  # fmt: skip
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
    # My League (F2: twm.league.store.PRIVATE_COLUMNS; tests/test_league.py checks the list)
    "league_team_id", "league_team_abbrev", "league_team_name", "league_is_mine",
    "league_opponent_id",
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
    """Where the local inputs are, the current season and the clock (aware UTC). ``modules``:
    the modules whose lists a publish carries (the others' tables are not touched); the
    streamer's and Regression Watch's inputs default to the project's paths."""

    warehouse: Path
    store: Path
    dataset: Path
    evaluation_csv: Path
    season: int
    now: datetime
    modules: tuple[str, ...] = MODULES
    streamer_dataset: Path | None = None
    streamer_csv: Path | None = None
    regression_csv: Path | None = None
    regression_stability_csv: Path | None = None
    decisions_season_dir: Path | None = None  # `twm decisions grade-pinned`'s output
    decisions_reports: Path | None = None  # reports/decisions
    hot_seat_reports: Path | None = None  # reports/hot_seat (step H4a)
    board_reports: Path | None = None  # reports/board (step I2c-a)

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

    def path(self, name: str) -> Path:
        """The streamer's / Regression Watch's input ``name`` (its default when unset)."""
        from twm.config import ROOT

        defaults = {
            "streamer_dataset": ROOT / "data" / "streamer" / "dataset.parquet",
            "streamer_csv": ROOT / "reports" / "streamer" / "backtest.csv",
            "regression_csv": ROOT / "reports" / "regression_watch" / "backtest.csv",
            "regression_stability_csv": ROOT / "reports" / "regression_watch" / "stability.csv",
            "decisions_season_dir": ROOT / "data" / "decisions" / "season",
            "decisions_reports": ROOT / "reports" / "decisions",
            "hot_seat_reports": ROOT / "reports" / "hot_seat",
            "board_reports": ROOT / "reports" / "board",
        }
        value = getattr(self, name)
        return Path(value) if value is not None else defaults[name]


@dataclass
class ListData:
    """One module's lists as published (tables.FAMILIES): ``lists`` and ``rows`` in the
    tables' column order; ``outcome_source``: outcome rows of every list the module may have
    in the target (the writer keeps those its lists need, frozen live lists included);
    ``outcome_keys``: (season, week, row id) whose outcomes are always published."""

    lists: pl.DataFrame
    rows: pl.DataFrame
    outcome_source: pl.DataFrame
    outcome_keys: pl.DataFrame


@dataclass
class PublishData:
    """Everything a publish writes, before it meets the target. ``families``: the modules'
    lists (a module missing here is not touched by the publish)."""

    season: int
    now: datetime
    families: dict[str, ListData]
    tables: dict[str, pl.DataFrame]  # model_versions, dim_team, dim_player, the track records,
    # tier_stats, player_week_summary, glossary (in tables.TABLES column order)
    meta: dict[str, str]
    data_as_of: datetime | None
    warnings: list[str] = field(default_factory=list)
    # step P3: the Decision Report Card (twm.publish.decisions.DecisionData; None = not touched)
    decisions: Any = None
    # feature #1: the Questionable list (twm.publish.questionable.QuestionableData; None = not
    # touched)
    questionable: Any = None
    # feature #5: Teammate out (twm.publish.teammate_out.TeammateOutData; None = not touched)
    teammate_out: Any = None
    # feature #6: the playoff planner (twm.publish.playoff_planner.PlayoffPlannerData; None =
    # not touched)
    playoff_planner: Any = None
    # feature #10: coach tendencies (twm.publish.coach_tendencies.CoachTendencyData; None = not
    # touched)
    coach_tendencies: Any = None
    # feature #8: lead time vs the crowd (twm.publish.lead_time.LeadTimeData, aggregates only;
    # None = not touched)
    lead_time: Any = None

    # the Waiver Radar's lists by their E2 names
    @property
    def lists(self) -> pl.DataFrame:
        return self.families[MODULE].lists

    @lists.setter
    def lists(self, df: pl.DataFrame) -> None:
        self.families[MODULE].lists = df

    @property
    def picks(self) -> pl.DataFrame:
        return self.families[MODULE].rows

    @picks.setter
    def picks(self, df: pl.DataFrame) -> None:
        self.families[MODULE].rows = df

    @property
    def outcome_source(self) -> pl.DataFrame:
        return self.families[MODULE].outcome_source

    @property
    def outcome_keys(self) -> pl.DataFrame:
        return self.families[MODULE].outcome_keys


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


def choose_lists(
    rows: pl.DataFrame, key: Sequence[str] = ("season", "week", "position", "kind")
) -> pl.DataFrame:
    """One model version per list (``key``: (season, week, position, kind)): the latest written
    (the most recent ``created_at``), then the store's current version, then the larger version
    id."""
    key = list(key)
    if rows.height == 0:
        return rows
    per_version = rows.group_by([*key, "model_version"]).agg(
        pl.col("created_at").max().alias("_written"), pl.col("is_current").any().alias("_cur")
    )
    chosen = (
        per_version.sort(
            [*key, "_written", "_cur", "model_version"],
            descending=[False] * len(key) + [True, True, True],
        )
        .group_by(key, maintain_order=True)
        .first()
        .select(*key, "model_version")
    )
    return rows.join(chosen, on=[*key, "model_version"], how="inner")


def module_store_rows(store: Path, module: str, season: int) -> pl.DataFrame:
    """Every stored row of ``module``'s lists a publish may carry (steps P2): every 'live' row
    and every row of ``season`` (live or reconstructed); the store is opened read-only. Rows
    of earlier seasons' backtests come from the module's frozen backtest, never from here."""
    if not store.exists():
        raise PublishInputError(f"predictions store not found: {store}")
    con = _duck(store)
    try:
        df = con.execute(
            """
            SELECT season, week, rank_group AS position, kind, rank, entity_id, entity_type,
                   score, raw_score, band, reasons_json, tier, incomplete, as_of, created_at,
                   model_version, TRUE AS is_current
            FROM predictions WHERE module = ? AND (kind = 'live' OR season = ?)
            """,
            [module, int(season)],
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


def version_rows(frame: pl.DataFrame, created_at: datetime | None = None) -> pl.DataFrame:
    """Model-version rows as the store keeps them (lists as JSON text) -> as published (lists
    as lists; ``created_at`` aware UTC, or ``created_at`` where the frame has none)."""
    df = frame
    if "created_at" not in df.columns:
        df = df.with_columns(pl.lit(created_at, dtype=pl.Datetime("us", "UTC")).alias("created_at"))
    df = _as_utc(df, "created_at")
    return df.select(
        "model_version", "module", "model", "label",
        pl.Series("training_seasons",
                  [[int(s) for s in json.loads(x)] for x in df.get_column("training_seasons")],
                  dtype=pl.List(pl.Int32)),
        pl.col("test_season").cast(pl.Int32),
        pl.Series("feature_list",
                  [[str(f) for f in json.loads(x)] for x in df.get_column("feature_list")],
                  dtype=pl.List(pl.String)),
        "params", "created_at",
    )  # fmt: skip


def list_table(
    rows: pl.DataFrame, key: Sequence[str], *, pool: str = "n_pool", version: str = "model_version"
) -> pl.DataFrame:
    """One row per list of the chosen store rows (every module): its as-of, model version, when
    it was written (``generated_at``), whether it was scored with incomplete data, and how many
    rows it has (``pool``)."""
    return _as_utc(
        rows.group_by(list(key)).agg(
            pl.col("as_of").max(),
            pl.col("model_version").first().alias(version),
            pl.col("created_at").max().alias("generated_at"),
            pl.col("incomplete").any(),
            pl.len().cast(pl.Int32).alias(pool),
        ),
        "as_of",
        "generated_at",
    )


def radar_lists(
    rows: pl.DataFrame, dataset_info: pl.DataFrame, notes_by_season: dict[int, dict[str, str]]
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(lists, picks) from the chosen store rows: one list row per (season, week, position,
    kind) and its top ``TOP_N`` picks, each with its team from the dataset."""
    key = ["season", "week", "position", "kind"]
    if rows.height == 0:
        return pl.DataFrame(schema=LIST_SCHEMA), pl.DataFrame(schema=PICK_SCHEMA)
    lists = list_table(rows, key)
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


def backtest_chances(store: Path, picks: pl.DataFrame) -> pl.DataFrame:
    """The chance, its range and the priority of every published 'backtest' pick that has
    none, computed the point-in-time way (E1 decision: the site shows the chance): for a list
    of season S, from the backtest predictions of the seasons before S only, with the same
    code the weekly list uses (:func:`twm.modules.waiver_radar.confidence.from_store` with
    :func:`~twm.modules.waiver_radar.confidence.seasons_before`, then ``band`` and
    ``tier_of``), so no list's chance rests on its own or later outcomes. Lists of the first
    backtest season (2014) have no earlier season and keep NULL; reasons stay empty (the
    backtest never made them)."""
    from twm.modules.waiver_radar import confidence as cf
    from twm.modules.waiver_radar.production import LABEL, MODEL

    todo = picks.filter((pl.col("kind") == "backtest") & pl.col("chance").is_null())
    if todo.height == 0:
        return picks
    parts = []
    for season in sorted({int(x) for x in todo.get_column("season").unique().to_list()}):
        try:
            conf = cf.from_store(store, model=MODEL, label=LABEL, seasons=cf.seasons_before(season))
        except ValueError:  # no earlier backtest season (or none stored): stays NULL
            continue
        sub = todo.filter(pl.col("season") == season)
        bands = [_band(cf.band_json(r)) for r in conf.band(sub.get_column("model_prob").to_numpy())
                 .iter_rows(named=True)]  # fmt: skip
        parts.append(
            sub.select("season", "week", "position", "kind", "rank").with_columns(
                pl.Series("_chance", [b[0] for b in bands], dtype=pl.Float64),
                pl.Series("_low", [b[1] for b in bands], dtype=pl.Float64),
                pl.Series("_high", [b[2] for b in bands], dtype=pl.Float64),
                pl.Series("_tier", [cf.tier_of(b[0]) for b in bands], dtype=pl.String),
            )
        )
    if not parts:
        return picks
    key = ["season", "week", "position", "kind", "rank"]
    filled = picks.join(pl.concat(parts), on=key, how="left")
    return filled.with_columns(
        pl.coalesce("chance", "_chance").alias("chance"),
        pl.coalesce("chance_low", "_low").alias("chance_low"),
        pl.coalesce("chance_high", "_high").alias("chance_high"),
        pl.coalesce("tier", "_tier").alias("tier"),
    ).select(list(PICK_SCHEMA))


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
    without a stat line); ``points_raw``, the unrounded points FPOE is taken from. xFP and
    FPOE are not here: :func:`with_xfp` adds Regression Watch's own walk-forward xFP (step
    PXFP; ffopportunity's before)."""
    from twm.scoring import score_sql

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
               COALESCE(st.fantasy_points, 0) AS points_raw, st.player_display_name
        FROM k
        LEFT JOIN st USING (game_id, team, gsis_id)
        LEFT JOIN sn USING (game_id, team, gsis_id)
        LEFT JOIN tc USING (game_id, team)
        ORDER BY 1, 2, 3
        """
    ).pl()


NG_COLUMNS = ("points_ng", "xfp_ng", "fpoe_ng")
XFP_COLUMNS = ("xfp", "fpoe", *NG_COLUMNS)


def with_xfp(pws: pl.DataFrame, own: pl.DataFrame | None) -> pl.DataFrame:
    """The weekly player summary with its xFP and FPOE, with and without garbage time, from
    Regression Watch's own walk-forward xFP (step PXFP, owner's decision of 2026-10-02:
    :func:`twm.publish.regression_lists.own_player_weeks`; before it, ffopportunity's): ``xfp``
    rounded to 2 decimals, ``fpoe`` = points - xFP, and the no-garbage-time three as
    :func:`twm.publish.regression_lists.ng_columns` rounds them (step P2). NULL where a
    player-week has no own-xFP row (or without ``own``: a publish without Regression Watch)."""
    from twm.publish.regression_lists import ng_columns

    keys = ["gsis_id", "season", "week"]
    if own is None:
        nulls = [pl.lit(None, dtype=pl.Float64).alias(c) for c in ("xfp_raw", *NG_COLUMNS)]
        out = pws.with_columns(nulls)
    else:
        raw = own.select(*keys, pl.col("xfp").alias("xfp_raw")).unique(
            keys, keep="first", maintain_order=True
        )
        ng = ng_columns(own).join(raw, on=keys, how="left")
        out = pws.join(ng.select(*keys, "xfp_raw", *NG_COLUMNS), on=keys, how="left",
                       maintain_order="left")  # fmt: skip
    base = [c for c in pws.columns if c not in ("points_raw", "player_display_name")]
    return out.with_columns(
        pl.col("xfp_raw").round(2).alias("xfp"),
        (pl.col("points_raw") - pl.col("xfp_raw")).round(2).alias("fpoe"),
    ).select(*base, *XFP_COLUMNS, "player_display_name")


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
    warehouse, the season/week it belongs to, when the warehouse was built, the current week
    (the latest regular-season week of ``season`` whose Tuesday as-of has passed) and its
    official as-of (``current_as_of``)."""
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
    # the official Tuesday as-of of (season, current week), as twm.asof.weekly_as_of gives it
    # (ISO 8601 UTC; empty before the season's first as-of)
    meta["current_as_of"] = ""
    if meta["current_week"]:
        from twm.asof import weekly_as_of

        meta["current_as_of"] = weekly_as_of(con, season, int(meta["current_week"])).isoformat()
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


def csv_table(path: Path, table: str, rename: dict[str, str] | None = None) -> pl.DataFrame:
    """Every row of a committed report CSV as ``table`` publishes it (step P2: the streamer's
    and Regression Watch's track records): ``line`` = the data row's number (from 1), the
    CSV's columns (``rename``: CSV name -> column) cast to the table's types (integers through
    float, so '12.0' is 12)."""
    from twm.publish.tables import TABLES

    if not path.exists():
        raise PublishInputError(f"report not found: {path}")
    df = pl.read_csv(path, infer_schema_length=0).rename(rename or {})
    exprs = []
    for c, typ in TABLES[table].columns:
        if c == "line":
            exprs.append(pl.int_range(1, pl.len() + 1, dtype=pl.Int32).alias("line"))
        elif typ == "integer":
            exprs.append(pl.col(c).cast(pl.Float64).cast(pl.Int32))
        elif typ == "double precision":
            exprs.append(pl.col(c).cast(pl.Float64))
        else:
            exprs.append(pl.col(c).cast(pl.String))
    return df.select(exprs)


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
    return version_rows(df)


# Modules with registry entries but no page on the site yet: their terms stay out of the published
# glossary until the module ships (Hot-Seat until step H4b; the board until its web step, I2c-b).
# Empty: every module's terms publish.
UNPUBLISHED_MODULES: frozenset[str] = frozenset()


def glossary() -> pl.DataFrame:
    from twm import registry

    rows = [
        {"name": e.name, "title": e.title, "kind": e.kind, "unit": e.unit,
         "formula": e.formula, "explanation": e.explanation, "verified": e.verified or None,
         "modules": list(e.modules), "model_output": e.model_output}
        for e in sorted(registry.entries(), key=lambda e: e.name)
        if not (e.modules and set(e.modules) <= UNPUBLISHED_MODULES)
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


def merge_coaches(current: pl.DataFrame | None, more: pl.DataFrame) -> pl.DataFrame:
    """dim_coach rows of the decisions (``current``, may be None) and the Hot-Seat Meter
    (``more``): one row per coach id; :class:`PublishInputError` when an id has two names."""
    frames = [f.select("coach_id", "name") for f in (current, more) if f is not None]
    both = pl.concat(frames).unique().sort("coach_id")
    clash = both.filter(pl.col("coach_id").is_duplicated())
    if clash.height:
        raise PublishInputError(f"coach ids with two names: {clash.rows()[:4]}")
    return both


def radar(inputs: Inputs) -> tuple[ListData, pl.DataFrame]:
    """The Waiver Radar's lists and outcomes (module docstring) and its dataset's (gsis_id,
    name) for player names."""
    season = int(inputs.season)
    dataset = read_dataset(inputs.dataset)
    rows = choose_lists(store_list_rows(inputs.store, season))
    notes = position_notes(inputs.evaluation_csv, rows.get_column("season").unique().to_list())
    lists, picks = radar_lists(rows, dataset.select("season", "week", "gsis_id", "team"), notes)
    picks = backtest_chances(inputs.store, picks)
    outcomes = outcome_rows(dataset)
    warnings = check_store_outcomes(inputs.store, picks, outcomes)
    if warnings:
        raise PublishInputError(warnings[0])
    keys = pl.concat([
        picks.select("season", "week", "gsis_id"),
        dataset.filter(pl.col("season") == season).select("season", "week", "gsis_id"),
    ]).unique(maintain_order=True)  # fmt: skip
    return ListData(lists, picks, outcomes, keys), dataset.select("gsis_id", "name")


def collect(inputs: Inputs) -> PublishData:
    """Read every local input and build what a publish writes (nothing is written here)."""
    from twm import predictions as pr
    from twm.config import settings
    from twm.publish import regression_lists as rl
    from twm.publish import stream_lists as sl

    season = int(inputs.season)
    unknown = [m for m in inputs.modules if m not in MODULES]
    if unknown or MODULE not in inputs.modules:
        raise PublishInputError(f"modules must include {MODULE} and be among {MODULES}")
    families: dict[str, ListData] = {}
    families[MODULE], names = radar(inputs)
    extra: list[sl.Collected] = []
    tables: dict[str, pl.DataFrame] = {}
    first = int(settings().seasons["snaps_start"])
    con = _duck(inputs.warehouse)
    try:
        teams = dim_team(con)
        pws = player_week_summary(con, first)
        if "streamer" in inputs.modules:
            got = sl.collect_streamer(
                inputs.store, inputs.path("streamer_dataset"), inputs.path("streamer_csv"),
                season, con, inputs.now.astimezone(UTC).replace(tzinfo=None),
            )  # fmt: skip
            families["streamer"], tables["stream_track_record"] = got.data, got.track_record
            extra.append(got)
        hot_coaches = None
        if "hot_seat" in inputs.modules:  # step H4a: live lists + the frozen backtest
            from twm.publish import hot_seat_lists as hs

            got, hot_coaches = hs.collect_hot_seat(inputs.store, inputs.path("hot_seat_reports"),
                                                   season, inputs.now, con)  # fmt: skip
            families["hot_seat"], tables[hs.TRACK_TABLE] = got.data, got.track_record
            tables.update(got.tables)
            extra.append(got)
        if "board" in inputs.modules:  # step I2c-a: stored boards + the frozen backtest
            from twm.publish import board_lists as bl

            got = bl.collect_board(inputs.store, inputs.path("board_reports"), season,
                                   inputs.now, con)  # fmt: skip
            families["board"], tables[bl.TRACK_TABLE] = got.data, got.track_record
            tables.update(got.tables)
            extra.append(got)
        data_as_of, meta = data_freshness(con, season, inputs.now)
    finally:
        con.close()
    own = None  # step PXFP: the player pages' xFP is Regression Watch's own walk-forward xFP
    if "regression_watch" in inputs.modules:
        own = rl.own_player_weeks(inputs.warehouse, season)
        frame = rl.history(inputs.warehouse, rl.FIRST_LIVE_SEASON, season)  # live seasons only
        got = rl.collect_regression(inputs.store, inputs.warehouse, inputs.path("regression_csv"),
                                    season, inputs.now, frame,
                                    inputs.path("regression_stability_csv"))  # fmt: skip
        families["regression_watch"], tables["regression_track_record"] = got.data, got.track_record
        tables.update(got.tables)
        extra.append(got)
    pws = with_xfp(pws, own)
    qd = None
    if "questionable" in inputs.modules:  # feature #1: the snapshots (append-only) and tables
        from twm.publish import questionable as qn

        qd = qn.collect_questionable(inputs.store, inputs.warehouse, season, inputs.now)
        tables.update(qd.tables)
        meta.update(qn.week_meta(inputs.warehouse, inputs.now))
    td = None
    if "teammate_out" in inputs.modules:  # feature #5: the snapshots (append-only) and tables
        from twm.publish import teammate_out as tn

        td = tn.collect_teammate_out(inputs.store, inputs.warehouse, season, inputs.now)
        tables.update(td.tables)
        meta.update(tn.week_meta(inputs.warehouse, inputs.now))
    ppd = None
    if "playoff_planner" in inputs.modules:  # feature #6: the snapshots (append-only), tables
        from twm.publish import playoff_planner as pp

        ppd = pp.collect_playoff_planner(inputs.store, inputs.warehouse, season, inputs.now)
        tables.update(ppd.tables)
        meta.update(pp.meta(ppd))
    ctd = None
    if "coach_tendencies" in inputs.modules:  # feature #10: replaced tables (the warehouse)
        from twm.publish import coach_tendencies as ct

        ctd = ct.collect_coach_tendencies(inputs.warehouse, season, inputs.now)
        tables.update(ctd.tables)
        meta.update(ct.meta(ctd))
    ltd = None
    if "lead_time" in inputs.modules:  # feature #8: replaced tables, aggregates only
        from twm.publish import lead_time as lt

        ltd = lt.collect_lead_time(inputs.warehouse)
        tables.update(ltd.tables)
    dec = None
    if "decisions" in inputs.modules:  # step P3: the frozen history + the season in progress
        from twm.publish import decisions as dc

        try:
            dec = dc.collect_decisions(season, season_dir=inputs.path("decisions_season_dir"),
                                       reports_dir=inputs.path("decisions_reports"))  # fmt: skip
        except dc.DecisionsInputError as e:
            raise PublishInputError(str(e)) from e
        tables["dim_coach"], tables["decisions_track_record"] = dec.coaches, dec.track_record
        weeks = dec.current["decision_fourth"]["week"]
        meta.update({
            "decisions_season": str(dec.season), "decisions_version": dec.version,
            "decisions_history": "-".join(str(f(dec.history["coach_season"]["season"]))
                                          for f in (min, max)),
            "decisions_latest_week": "" if weeks.len() == 0 else str(weeks.max()),
        })  # fmt: skip
    if hot_coaches is not None:  # the Hot-Seat coaches join the decisions' (same slugs)
        tables["dim_coach"] = merge_coaches(tables.get("dim_coach"), hot_coaches)
    if ctd is not None:  # feature #10: the coach-tendency coaches too (same slugs)
        tables["dim_coach"] = merge_coaches(tables.get("dim_coach"), ctd.coaches)
    ids = families[MODULE].outcome_keys.get_column("gsis_id").to_list()
    ids += pws.get_column("gsis_id").to_list()
    if "regression_watch" in families:
        ids += families["regression_watch"].rows.get_column("gsis_id").to_list()
    if "board" in families:
        ids += families["board"].rows.get_column("gsis_id").to_list()
    if qd is not None:
        ids += qd.rows.get_column("gsis_id").to_list()
        names = pl.concat([names, qd.names])
    if td is not None:  # the teammates and the absent starters
        ids += td.names.get_column("gsis_id").to_list()
        names = pl.concat([names, td.names])
    names = pl.concat([names, pws.select("gsis_id", pl.col("player_display_name").alias("name"))])
    con = _duck(inputs.warehouse)
    try:
        players = dim_player(con, ids, names)
    finally:
        con.close()
    from twm.publish.tables import FAMILIES

    wanted = [v for m, f in families.items() for v in
              f.lists.get_column(FAMILIES[m].version).to_list()]  # fmt: skip
    if "board" in families:  # a board list names its missed-time model too
        wanted += families["board"].lists.get_column("missed_version").to_list()
    if qd is not None:  # the pinned table's version (no store row: built from its JSON)
        wanted.append(qd.version)
    if td is not None:  # the same for Teammate out
        wanted.append(td.version)
    if ppd is not None:  # and the playoff planner's pinned spec
        wanted.append(ppd.version)
    versions = model_versions(inputs.store, wanted)
    more = [e.versions for e in extra if e.versions.height]
    if qd is not None:
        more.append(qd.versions)
    if td is not None:
        more.append(td.versions)
    if ppd is not None:
        more.append(ppd.versions)
    if more:
        versions = pl.concat([versions, *more], how="vertical_relaxed").unique(
            "model_version", keep="first", maintain_order=True
        ).filter(pl.col("model_version").is_in(wanted)).sort("model_version")  # fmt: skip
    code = pr.code_version()
    sha = re.search(r"\(([0-9a-f]+)\)", code)
    meta.update({
        "current_season": str(season),
        "generated_at": inputs.now.astimezone(UTC).isoformat(),
        "code_version": code,
        "git_sha": sha.group(1) if sha else "",
    })  # fmt: skip
    tables.update({
        "model_versions": versions,
        "dim_team": teams,
        "dim_player": players,
        "track_record": track_record(inputs.evaluation_csv),
        "tier_stats": tier_stats(inputs.store, season),
        "player_week_summary": pws.drop("player_display_name"),
        "glossary": glossary(),
    })  # fmt: skip
    return PublishData(season=season, now=inputs.now, families=families, tables=tables,
                       meta=meta, data_as_of=data_as_of, decisions=dec, questionable=qd,
                       teammate_out=td, playoff_planner=ppd, coach_tendencies=ctd,
                       lead_time=ltd,
                       warnings=list(dec.warnings) if dec is not None else [])  # fmt: skip


# --------------------------------------------------------------------------------------
# Validation (before anything is written)
# --------------------------------------------------------------------------------------


def _list_problems(
    name: str, lists: pl.DataFrame, rows: pl.DataFrame, key: list[str], *,
    positions: Sequence[str] | None, pool: str, row_id: str, ranked: bool,
    top: int | None = TOP_N,
) -> list[str]:  # fmt: skip
    """The checks every module's lists pass: one list per key, known kinds and positions, every
    row in a list, and (``ranked``) ranks 1..n without gaps or repeats, n = min(``top``, pool
    size) (``top`` None: the whole pool); unranked lists hold one row per pool member."""
    problems = []
    if lists.select(key).is_duplicated().any():
        problems.append(f"two {name} lists share a ({', '.join(key)})")
    bad_kind = lists.filter(~pl.col("kind").is_in(KINDS))
    if bad_kind.height:
        problems.append(f"unknown {name} list kinds: {bad_kind['kind'].unique().to_list()}")
    if positions is not None:
        where = lists if "position" in lists.columns else rows
        bad_pos = where.filter(~pl.col("position").is_in(list(positions)))
        if bad_pos.height:
            problems.append(f"unknown {name} positions: {bad_pos['position'].unique().to_list()}")
    aggs = [pl.len().alias("n"), pl.col(row_id).n_unique().alias("players")]
    if ranked:
        aggs += [pl.col("rank").min().alias("lo"), pl.col("rank").max().alias("hi"),
                 pl.col("rank").n_unique().alias("u")]  # fmt: skip
    joined = lists.select(*key, pool).join(rows.group_by(key).agg(aggs), on=key, how="left")
    expected = pl.min_horizontal(pl.col(pool), pl.lit(top)) if ranked and top else pl.col(pool)
    broken = pl.col("n").is_null() | (pl.col("n") != expected) | (pl.col("players") != pl.col("n"))
    if ranked:
        broken = broken | (pl.col("lo") != 1) | (pl.col("hi") != pl.col("n")) | (
            pl.col("u") != pl.col("n"))  # fmt: skip
    bad = joined.filter(broken)
    if bad.height:
        r = bad.row(0, named=True)
        what = f"rank their top {top or 'n'} as 1..n without gaps or repeats" if ranked else \
            f"hold one row per player of the pool ({pool})"  # fmt: skip
        problems.append(f"{bad.height} {name} lists do not {what} (e.g. "
                        + " ".join(str(r[k]) for k in key) + ")")  # fmt: skip
    orphans = rows.join(lists.select(key), on=key, how="anti")
    if orphans.height:
        problems.append(f"{orphans.height} {name} rows belong to no published list")
    return problems


def _range_problems(name: str, rows: pl.DataFrame, columns: Sequence[str]) -> list[str]:
    """Probabilities and chances within [0, 1] and each chance inside its own range."""
    problems = []
    for c in columns:
        out = rows.filter(pl.col(c).is_nan() | (pl.col(c) < 0) | (pl.col(c) > 1))
        if out.height:
            problems.append(f"{out.height} {name} have {c} outside [0, 1]")
    band = rows.filter(
        pl.col("chance").is_not_null()
        & ((pl.col("chance_low") > pl.col("chance")) | (pl.col("chance") > pl.col("chance_high")))
    )
    if band.height:
        problems.append(f"{band.height} {name} have a chance outside its own range")
    tiers = rows.filter(pl.col("tier").is_not_null() & ~pl.col("tier").is_in(
        ["must-add", "speculative", "watch"]))  # fmt: skip
    if tiers.height:
        problems.append(f"{tiers.height} {name} have an unknown priority")
    return problems


def _team_problems(name: str, rows: pl.DataFrame, teams: set[str], column: str = "team",
                   *, required: bool = True) -> list[str]:  # fmt: skip
    problems = []
    no_team = rows.filter(pl.col(column).is_null()) if required else rows.clear()
    if no_team.height:
        r = no_team.row(0, named=True)
        who = r.get("gsis_id") or r.get("entity_id")
        problems.append(
            f"{no_team.height} {name} have no team in the dataset (e.g. {who} in {r['season']} "
            f"week {r['week']}): rebuild it"
        )
    unknown = rows.filter(pl.col(column).is_not_null() & ~pl.col(column).is_in(list(teams)))
    if unknown.height:
        problems.append(
            f"{unknown.height} {name} name a team that is not a current franchise: "
            f"{sorted(set(unknown.get_column(column).to_list()))[:5]}"
        )
    return problems


def _streamer_problems(d: ListData, teams: set[str]) -> list[str]:
    """The streamer's lists: keys, ranks 1..n, kickers' gsis ids and D/ST ids of the pick's own
    team, entity types, chances and the K model's probability within [0, 1], teams."""
    from twm.publish.stream_lists import ENTITY_PATTERN, ENTITY_TYPES, POSITIONS

    rows = d.rows
    problems = _list_problems("streamer", d.lists, rows, ["season", "week", "position", "kind"],
                              positions=POSITIONS, pool="n_pool", row_id="entity_id",
                              ranked=True, top=None)  # fmt: skip
    odd = rows.filter(~pl.col("entity_id").str.contains(ENTITY_PATTERN.pattern))
    if odd.height:
        problems.append(f"{odd.height} streamer picks have an id that is neither a gsis id nor "
                        f"DST-<team>, e.g. {odd.get_column('entity_id')[0]!r}")  # fmt: skip
    wrong = rows.filter(
        (pl.col("entity_type") != pl.col("position").replace_strict(ENTITY_TYPES, default=""))
        | ((pl.col("position") == "DST") & (pl.col("entity_id") != "DST-" + pl.col("team")))
        | ((pl.col("position") == "K") & pl.col("entity_id").str.starts_with("DST-"))
    )
    if wrong.height:
        problems.append(f"{wrong.height} streamer picks do not match their position (entity "
                        "type, or a D/ST id of another team)")  # fmt: skip
    problems += _range_problems("streamer picks", rows, ("model_prob", "chance", "chance_low",
                                                         "chance_high"))  # fmt: skip
    if rows.filter((pl.col("position") == "K") & pl.col("model_prob").is_null()).height:
        problems.append("some K picks have no model probability")
    if rows.filter(pl.col("display_name").is_null()).height:
        problems.append("some streamer picks have no name in the streamer dataset")
    problems += _team_problems("streamer picks", rows, teams)
    problems += _team_problems("streamer picks' next opponents", rows, teams, "next_opponent",
                               required=False)  # fmt: skip
    return problems


def _regression_problems(d: ListData, teams: set[str], players: set[str]) -> list[str]:
    """Regression Watch's lists: keys, one row per universe player, positions, tags, the
    projection present, teams and players known."""
    from twm.modules.regression_watch.weekly import BAND_ORDER

    rows = d.rows
    problems = _list_problems("Regression Watch", d.lists, rows, ["season", "week", "kind"],
                              positions=FANTASY_POSITIONS, pool="n_universe", row_id="gsis_id",
                              ranked=False)  # fmt: skip
    bad_tag = rows.filter(
        (pl.col("tag").is_not_null() & ~pl.col("tag").is_in(list(BAND_ORDER)))
        | (pl.col("tag").is_null() != (pl.col("tags").list.len() == 0))
        | (pl.col("tag").is_not_null() & (pl.col("tags").list.first() != pl.col("tag")))
    )
    if bad_tag.height:
        problems.append(f"{bad_tag.height} Regression Watch rows have a tag that is not the "
                        "first of their tags (sell_high, buy_low)")  # fmt: skip
    hidden = rows.filter(~pl.col("tags").list.eval(pl.element().is_in(list(BAND_ORDER))).list.all())
    if hidden.height:
        problems.append(f"{hidden.height} Regression Watch rows carry a tag the product does "
                        "not show (only sell_high, buy_low: Legit was dropped)")  # fmt: skip
    if rows.filter(pl.col("projection").is_null() | pl.col("ppg").is_null()).height:
        problems.append("some Regression Watch rows have no projection or PPG")
    problems += _team_problems("Regression Watch rows", rows, teams)
    missing = rows.filter(~pl.col("gsis_id").is_in(list(players)))
    if missing.height:
        problems.append(f"{missing.height} Regression Watch rows name a player missing from "
                        "dim_player")  # fmt: skip
    return problems


def _hot_seat_problems(d: ListData, teams: set[str], coaches: set[str]) -> list[str]:
    """The Hot-Seat lists: keys, snapshots, every coach ranked 1..n, probabilities within
    [0, 1], teams and coaches known, the drivers present."""
    rows = d.rows
    problems = _list_problems("Hot-Seat", d.lists, rows, ["season", "week", "snapshot", "kind"],
                              positions=None, pool="n_coaches", row_id="coach_id",
                              ranked=True, top=None)  # fmt: skip
    odd = d.lists.filter(~pl.col("snapshot").is_in(["weekly", "end_of_season"]))
    if odd.height:
        problems.append(f"unknown Hot-Seat snapshots: {odd['snapshot'].unique().to_list()}")
    out = rows.filter(pl.col("probability").is_null() | pl.col("probability").is_nan()
                      | (pl.col("probability") < 0) | (pl.col("probability") > 1))  # fmt: skip
    if out.height:
        problems.append(f"{out.height} Hot-Seat rows have a probability outside [0, 1]")
    problems += _team_problems("Hot-Seat rows", rows, teams)
    unknown = rows.filter(pl.col("coach_id").is_null() | ~pl.col("coach_id").is_in(list(coaches)))
    if unknown.height:
        problems.append(f"{unknown.height} Hot-Seat rows name a coach missing from dim_coach")
    if rows.filter(pl.col("drivers").is_null()).height:
        problems.append("some Hot-Seat rows have no drivers")
    return problems


def _board_problems(d: ListData, teams: set[str], players: set[str],
                    versions: set[str]) -> list[str]:  # fmt: skip
    """The board's lists (step I2c-a): one row per player, both ranks 1..n, both chances in
    [0, 1], known teams, players and both model versions, drivers present."""
    rows = d.rows
    key = ["season", "week", "snapshot", "kind"]
    problems = _list_problems("board", d.lists, rows.rename({"cliff_rank": "rank"}), key,
                              positions=None, pool="n_players", row_id="gsis_id", ranked=True,
                              top=None)  # fmt: skip
    problems += _list_problems("board (missed)", d.lists, rows.rename({"missed_rank": "rank"}),
                               key, positions=None, pool="n_players", row_id="gsis_id",
                               ranked=True, top=None)  # fmt: skip
    odd = d.lists.filter(pl.col("snapshot") != "preseason")
    if odd.height:
        problems.append(f"unknown board snapshots: {odd['snapshot'].unique().to_list()}")
    for c in ("cliff_probability", "missed_probability"):
        out = rows.filter(pl.col(c).is_null() | pl.col(c).is_nan() | (pl.col(c) < 0)
                          | (pl.col(c) > 1))  # fmt: skip
        if out.height:
            problems.append(f"{out.height} board rows have a {c} outside [0, 1]")
    problems += _team_problems("board rows", rows, teams)
    if rows.filter(~pl.col("gsis_id").is_in(list(players))).height:
        problems.append("some board rows name a player missing from dim_player")
    unknown = set(d.lists.get_column("missed_version").to_list()) - versions
    if unknown:
        problems.append(f"board lists of unknown missed-time model versions: {sorted(unknown)[:3]}")
    if rows.filter(pl.col("cliff_drivers").is_null() | pl.col("missed_drivers").is_null()).height:
        problems.append("some board rows have no drivers")
    return problems


def validate(data: PublishData) -> list[str]:
    """Plain-English problems (empty = publishable): see docs/deploy.md "What is checked"."""
    from twm.publish.tables import FAMILIES

    problems: list[str] = []
    t, fam = data.tables, data.families
    # 1. no identifiers other than gsis_id, the D/ST ids and team codes, no league data, no NaN
    frames = {**{FAMILIES[m].lists: d.lists for m, d in fam.items()},
              **{FAMILIES[m].rows: d.rows for m, d in fam.items()}, **t}  # fmt: skip
    if data.questionable is not None:
        frames.update({"questionable_list": data.questionable.lists,
                       "questionable_row": data.questionable.rows})  # fmt: skip
    if data.teammate_out is not None:
        frames.update({"teammate_out_list": data.teammate_out.lists,
                       "teammate_out_row": data.teammate_out.rows})  # fmt: skip
    if data.playoff_planner is not None:
        frames.update({"playoff_planner_list": data.playoff_planner.lists,
                       "playoff_planner_row": data.playoff_planner.rows})  # fmt: skip
    for name, df in frames.items():
        bad = sorted(c for c in df.columns if c.lower() in FORBIDDEN_COLUMNS)
        if bad:
            problems.append(f"{name} carries columns that must never be published: {bad}")
        nan = [c for c, d in df.schema.items() if d == pl.Float64 and df[c].is_nan().any()]
        if nan:
            problems.append(f"{name} has NaN values in {nan}")
    gsis = [
        data.picks.select("gsis_id"),
        t["dim_player"].select("gsis_id"),
        t["player_week_summary"].select("gsis_id"),
        data.outcome_keys.select("gsis_id"),
    ]
    if "regression_watch" in fam:
        gsis.append(fam["regression_watch"].rows.select("gsis_id"))
    if data.questionable is not None:
        gsis.append(data.questionable.rows.select("gsis_id"))
    if data.teammate_out is not None:
        gsis.append(data.teammate_out.rows.select("gsis_id"))
    ids = pl.concat(gsis).unique()
    odd = ids.filter(~pl.col("gsis_id").str.contains(GSIS_PATTERN.pattern))
    if odd.height:
        problems.append(
            f"{odd.height} player ids are not gsis ids (00-nnnnnnn or AAAnnnnnn), e.g. "
            f"{odd.get_column('gsis_id')[0]!r}"
        )
    players = set(t["dim_player"].get_column("gsis_id").to_list())
    teams = set(t["dim_team"].get_column("team_abbr").to_list())
    # 2. the Waiver Radar's lists and picks
    lists, picks = data.lists, data.picks
    problems += _list_problems("Waiver Radar", lists, picks, ["season", "week", "position",
                               "kind"], positions=FANTASY_POSITIONS, pool="n_pool",
                               row_id="gsis_id", ranked=True)  # fmt: skip
    problems += _range_problems("picks", picks, ("model_prob", "chance", "chance_low",
                                                 "chance_high"))  # fmt: skip
    if picks.filter(pl.col("model_prob").is_null()).height:
        problems.append("some picks have no model probability")
    problems += _team_problems("picks", picks, teams)
    if "streamer" in fam:
        problems += _streamer_problems(fam["streamer"], teams)
    if "regression_watch" in fam:
        problems += _regression_problems(fam["regression_watch"], teams, players)
    if "hot_seat" in fam:
        dim = t.get("dim_coach")
        coaches = set(dim.get_column("coach_id").to_list()) if dim is not None else set()
        problems += _hot_seat_problems(fam["hot_seat"], teams, coaches)
    if "board" in fam:
        known_v = set(t["model_versions"].get_column("model_version").to_list())
        problems += _board_problems(fam["board"], teams, players, known_v)
    if data.questionable is not None:  # feature #1
        from twm.publish import questionable as qn

        known_v = set(t["model_versions"].get_column("model_version").to_list())
        problems += qn.problems(data.questionable, teams, players, known_v)
    if data.teammate_out is not None:  # feature #5
        from twm.publish import teammate_out as tn

        known_v = set(t["model_versions"].get_column("model_version").to_list())
        problems += tn.problems(data.teammate_out, teams, players, known_v)
    if data.playoff_planner is not None:  # feature #6
        from twm.publish import playoff_planner as pp

        known_v = set(t["model_versions"].get_column("model_version").to_list())
        problems += pp.problems(data.playoff_planner, teams, known_v)
    if data.coach_tendencies is not None:  # feature #10
        from twm.publish import coach_tendencies as ct

        dim = t.get("dim_coach")
        coaches = set(dim.get_column("coach_id").to_list()) if dim is not None else set()
        problems += ct.problems(data.coach_tendencies, teams, coaches)
    if data.lead_time is not None:  # feature #8
        from twm.publish import lead_time as lt

        problems += lt.problems(data.lead_time)
    # 3. every player named exists; the weekly summary is clean
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
    if pws.filter(~pl.col("team").is_in(list(teams))).height:
        problems.append("some player_week_summary rows name a team that is not a current "
                        "franchise")  # fmt: skip
    # step P3: the Decision Report Card's rows
    if data.decisions is not None:
        from twm.publish import decisions as dc

        problems += dc.problems(data.decisions)
    # 4. every list's model version is published with it
    known = set(t["model_versions"].get_column("model_version").to_list())
    for m, d in fam.items():
        unknown = set(d.lists.get_column(FAMILIES[m].version).to_list()) - known
        if unknown:
            problems.append(f"{m} lists of unknown model versions: {sorted(unknown)[:3]}")
    return problems
