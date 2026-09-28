"""The weekly Waiver Radar list (C6): check the week's data, score the pool with the production
model, explain every score, store the list and write ``reports/waiver_radar/weekly/``.

One run (``twm radar score``, :func:`run_week`) for a season and week N:

1. **Freshness** (:func:`check_freshness`): the list is only as good as the data behind it, and
   ``available_at`` is an estimate of when nflverse publishes, not proof that our cache has
   it (docs/progress.md "live runs must check data freshness"). Before scoring: every game of
   week N due by the as-of has a final score; every played game has player stats, snap counts
   and ffopportunity rows; every team that played has a week-N injury report and a week-N
   roster; and the Tuesday as-of has come. Anything missing: refuse with a list of what is
   missing (exit code 3), unless ``--allow-incomplete`` (the rows are then flagged
   ``incomplete`` and the report carries a warning).
2. **Scoring** (:func:`score_week`, the point-in-time function): the C1 pool at the as-of
   (``in_pool``), the C3 features, both through one :class:`~twm.asof.AsOfView`; the production
   model (:mod:`production`) gives the calibrated probability; players are ranked within their
   position (probability, then the raw score, then the player id, as in the backtest); the
   confidence band and the suggested priority come from the backtest (:mod:`confidence`), the
   reasons from the model's contributions (:mod:`reasons`). Everything is read through the
   view, so nothing after the as-of can reach a number, a band, a tier or a reason (tested with
   the leakage harness).
3. **Live or reconstructed** (:func:`run_kind`, spec 8.7): ``kind = 'live'`` only when the run
   happens after the as-of and before the first kickoff of week N+1, in real time; any other
   run is ``'backtest'`` (reconstructed). The clock is a parameter (``now``), so the rule is
   tested without the real clock. A stored live week is never overwritten by a reconstructed
   run (:class:`twm.predictions.LiveWeekError`).
4. **Storing**: every pool row of the week (probability, raw score, rank, band, tier, reasons,
   kind, ``incomplete``, ``created_at``) under the production model's version; the week's
   outcomes as far as known (pending until the window's games are played).
5. **The report** (:func:`build_report`): per position the top 25 with rank, player, team, the
   chance and its band, the model's probability, the priority and 3 reasons; the tier cutoffs
   with their historical hit rates; a note for positions where the backtest showed no gain over
   last week's points (QB). Deterministic apart from its "Generated at" line.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from twm import predictions as pr
from twm.asof import AsOfView, to_utc
from twm.backtest.metrics import add_rank
from twm.backtest.walkforward import season_span
from twm.config import FANTASY_POSITIONS, league, settings
from twm.modules.waiver_radar import confidence as cf
from twm.modules.waiver_radar import reasons as rs
from twm.modules.waiver_radar.features import features_for
from twm.modules.waiver_radar.models import ID, MODULE
from twm.modules.waiver_radar.pool import candidate_pool

GROUP = ("season", "week", "position")
TOP_N = cf.TOP_N
REPORT_DIR = Path("reports/waiver_radar/weekly")
EXIT_NOT_READY = 3
POSITION_ORDER = {p: i for i, p in enumerate(FANTASY_POSITIONS)}

SCORED_SCHEMA: dict[str, pl.DataType] = {
    "season": pl.Int32(), "week": pl.Int32(), "as_of": pl.Datetime("us", "UTC"),
    "gsis_id": pl.String(), "name": pl.String(), "team": pl.String(), "position": pl.String(),
    "score": pl.Float64(), "raw_score": pl.Float64(), "rank": pl.Int32(),
    "chance": pl.Float64(), "band_lo": pl.Float64(), "band_hi": pl.Float64(),
    "band_n": pl.Int64(), "band_hits": pl.Int64(), "band_from": pl.Float64(),
    "band_to": pl.Float64(), "tier": pl.String(), "reasons_json": pl.String(),
}  # fmt: skip


class NotReadyError(RuntimeError):
    """The week's data has not fully arrived (or its as-of has not come): nothing is scored."""

    def __init__(self, freshness: Freshness) -> None:
        super().__init__(freshness.message())
        self.freshness = freshness


# --------------------------------------------------------------------------------------
# Weeks and the clock
# --------------------------------------------------------------------------------------


def _connect(db: Path | str) -> duckdb.DuckDBPyConnection:
    from twm.warehouse.build import connect

    return connect(db, read_only=True)


def _aware(t: datetime | None) -> datetime | None:
    return None if t is None else (t if t.tzinfo else t.replace(tzinfo=UTC))


def default_week(db: Path | str, season: int, now: datetime) -> int:
    """The latest regular-season week of ``season`` whose Tuesday as-of has passed at ``now``
    (dim_week). Whether its data is complete is checked separately (and refused if not)."""
    con = _connect(db)
    try:
        row = con.execute(
            "SELECT max(week) FROM dim_week WHERE season = ? AND season_type = 'REG' "
            "AND asof_weekly_utc <= ?",
            [season, to_utc(now)],
        ).fetchone()
    finally:
        con.close()
    if row is None or row[0] is None:
        raise LookupError(
            f"no regular-season week of {season} has reached its Tuesday as-of yet "
            f"(now {to_utc(now):%Y-%m-%d %H:%M} UTC)"
        )
    return int(row[0])


def last_reg_week(db: Path | str, season: int) -> int | None:
    con = _connect(db)
    try:
        row = con.execute(
            "SELECT max(week) FROM dim_week WHERE season = ? AND season_type = 'REG'", [season]
        ).fetchone()
    finally:
        con.close()
    return None if row is None or row[0] is None else int(row[0])


def next_kickoff(db: Path | str, season: int, week: int) -> datetime | None:
    """The first kickoff of week ``week + 1`` (regular season), aware UTC, or None."""
    con = _connect(db)
    try:
        row = con.execute(
            "SELECT min(kickoff_utc) FROM fact_game WHERE season = ? AND week = ? "
            "AND season_type = 'REG'",
            [season, week + 1],
        ).fetchone()
    finally:
        con.close()
    return _aware(row[0]) if row else None


def last_complete_week(db: Path | str, season: int, now: datetime, *, before: int) -> int | None:
    """The latest week before ``before`` whose data passes :func:`check_freshness` at ``now``
    (for the hint when a week is refused), or None."""
    from twm.asof import weekly_as_of

    for w in range(before - 1, 0, -1):
        try:
            as_of = weekly_as_of(db, season, w)
        except LookupError:
            continue
        if check_freshness(freshness_inputs(db, season, w), season, w, as_of, now).ok:
            return w
    return None


def run_kind(as_of: datetime, next_first_kickoff: datetime | None, now: datetime) -> str:
    """'live' only when ``now`` is at or after the as-of and before week N+1's first kickoff
    (a real-time Tuesday-to-kickoff run); 'backtest' (reconstructed) otherwise. Without a known
    next kickoff a run is never called live."""
    t, a = to_utc(now), to_utc(as_of)
    if next_first_kickoff is None:
        return "backtest"
    return "live" if a <= t < to_utc(next_first_kickoff) else "backtest"


# --------------------------------------------------------------------------------------
# Freshness
# --------------------------------------------------------------------------------------


@dataclass
class Freshness:
    """What the cache holds for one week, and what is missing."""

    season: int
    week: int
    as_of: datetime
    now: datetime
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    n_games: int = 0
    n_played: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems

    def message(self) -> str:
        head = (
            f"{self.season} week {self.week} is not ready to score: some of its data has not "
            f"arrived yet (the list is made at the Tuesday as-of, "
            f"{to_utc(self.as_of):%a %Y-%m-%d %H:%M} UTC)."
        )
        lines = [head, *(f"  - {p}" for p in self.problems)]
        lines.append(
            "What to do: refresh the data (`uv run twm ingest`, then `uv run twm build`) and "
            "run this again; snap counts and expected points often arrive hours after the last "
            "game. To score anyway, add --allow-incomplete (the list is then marked incomplete)."
        )
        return "\n".join(lines)


def _matchups(games: pl.DataFrame) -> str:
    return ", ".join(f"{r['away_team']}@{r['home_team']}" for r in games.iter_rows(named=True))


def freshness_inputs(db: Path | str, season: int, week: int) -> dict[str, pl.DataFrame]:
    """What the warehouse holds for the week (the raw tables on purpose: the question is what
    the cache has, not what was public): games, and per game / per team row counts."""
    con = _connect(db)
    try:
        games = con.execute(
            "SELECT game_id, away_team, home_team, kickoff_utc, result IS NOT NULL AS has_result, "
            "available_at FROM fact_game WHERE season = ? AND week = ? AND season_type = 'REG' "
            "ORDER BY kickoff_utc, game_id",
            [season, week],
        ).pl()
        ids = games.get_column("game_id").to_list() or [""]

        def per_game(table: str) -> pl.DataFrame:
            return con.execute(
                f"SELECT game_id, count(*) AS n FROM {table} WHERE game_id IN "
                f"({', '.join('?' for _ in ids)}) GROUP BY game_id",
                ids,
            ).pl()

        def per_team(table: str) -> pl.DataFrame:
            return con.execute(
                f"SELECT team, count(*) AS n FROM {table} WHERE season = ? AND week = ? "
                "GROUP BY team",
                [season, week],
            ).pl()

        out = {
            "games": games,
            "stats": per_game("fact_player_week"),
            "snaps": per_game("fact_snaps"),
            "opportunity": per_game("fact_opportunity_week"),
            "injuries": per_team("fact_injury_report"),
            "rosters": per_team("fact_roster_week"),
        }
    finally:
        con.close()
    return out


def check_freshness(
    inputs: Mapping[str, pl.DataFrame], season: int, week: int, as_of: datetime, now: datetime
) -> Freshness:
    """Every check of the module docstring, as plain-English problems (empty = ready)."""
    fr = Freshness(season, week, as_of, now)
    a = to_utc(as_of)
    if to_utc(now) < a:
        fr.problems.append(
            f"Tuesday's as-of has not come yet (it is now {to_utc(now):%a %Y-%m-%d %H:%M} UTC): "
            "Monday night's game, its snap counts and Tuesday's roster moves may still be on "
            "the way."
        )
    games = inputs["games"]
    fr.n_games = games.height
    if games.height == 0:
        fr.problems.append("the data has no games for this week")
        return fr
    avail = pl.col("available_at")
    due = games.filter(avail.is_null() | (avail <= a))
    later = games.filter(avail.is_not_null() & (avail > a))
    if later.height:
        fr.notes.append(
            f"{later.height} game(s) were moved past the as-of and belong to next week's data: "
            f"{_matchups(later)}"
        )
    no_result = due.filter(~pl.col("has_result"))
    if no_result.height:
        fr.problems.append(
            f"{no_result.height} of {due.height} games have no final score in the data yet: "
            f"{_matchups(no_result)}"
        )
    played = games.filter(pl.col("has_result"))
    fr.n_played = played.height
    for key, what in (
        ("stats", "player stats"),
        ("snaps", "snap counts"),
        ("opportunity", "ffopportunity rows (expected fantasy points)"),
    ):
        have = set(inputs[key].filter(pl.col("n") > 0).get_column("game_id").to_list())
        missing = played.filter(~pl.col("game_id").is_in(list(have)))
        if missing.height:
            fr.problems.append(
                f"{missing.height} played game(s) have no {what} yet: {_matchups(missing)}"
            )
    teams = sorted(
        set(played.get_column("away_team").to_list())
        | set(played.get_column("home_team").to_list())
    )
    for key, what in (("injuries", "injury report"), ("rosters", "weekly roster")):
        have = set(inputs[key].filter(pl.col("n") > 0).get_column("team").to_list())
        missing_teams = [t for t in teams if t not in have]
        if missing_teams:
            extra = (
                " (in past seasons a team-week without any injury-report rows happened now "
                "and then: if you are sure, use --allow-incomplete)"
                if key == "injuries"
                else ""
            )
            fr.problems.append(
                f"{len(missing_teams)} team(s) that played have no week-{week} {what} yet: "
                f"{', '.join(missing_teams)}{extra}"
            )
    return fr


# --------------------------------------------------------------------------------------
# Scoring (the point-in-time function)
# --------------------------------------------------------------------------------------


def _team_games(view: AsOfView, season: int) -> pl.DataFrame:
    """Regular-season games with a final score per team, visible at the view's as-of."""
    return view.sql(
        "SELECT team, count(*)::INTEGER AS team_games FROM ("
        " SELECT home_team AS team FROM fact_game WHERE season = ? AND season_type = 'REG'"
        "  AND result IS NOT NULL"
        " UNION ALL SELECT away_team FROM fact_game WHERE season = ? AND season_type = 'REG'"
        "  AND result IS NOT NULL) GROUP BY team",
        [season, season],
    )


def empty_scored() -> pl.DataFrame:
    return pl.DataFrame(schema=SCORED_SCHEMA)


def score_week(
    view: AsOfView,
    season: int,
    week: int,
    model: Any,
    conf: cf.Confidence,
    *,
    top: int = TOP_N,
) -> pl.DataFrame:
    """The scored pool at the view's as-of: one row per pool player (``SCORED_SCHEMA``), sorted
    by position and rank. Reads nothing but the view (plus the fixed model and backtest
    bins), so it passes :func:`twm.backtest.leakage.assert_future_invariant`."""
    pool = candidate_pool(view, season, week).filter(pl.col("in_pool"))
    if pool.height == 0:
        return empty_scored()
    feats = features_for(view, season, week, pool)
    keys = ["season", "week", ID]
    info = pool.select(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32), "as_of", ID, "name",
        "team", "preseason_rank_pos",
    )  # fmt: skip
    data = info.join(feats, on=keys, how="left", maintain_order="left")
    games = _team_games(view, season)
    data = data.join(games, on="team", how="left").with_columns(
        pl.min_horizontal(pl.col("team_games").fill_null(0), pl.lit(rs.WINDOW)).alias("_weeks")
    )
    raw, prob = model.predict(data)
    scored = data.with_columns(
        pl.Series("raw_score", raw, dtype=pl.Float64), pl.Series("score", prob, dtype=pl.Float64)
    )
    scored = add_rank(
        scored, group=GROUP, by=[("score", True), ("raw_score", True)], id_col=ID
    ).with_columns(pl.col("rank").cast(pl.Int32))
    band = conf.band(scored.get_column("score").to_numpy())
    scored = scored.hstack(band)
    tier = [
        cf.tier_of(c) if r <= top else None
        for c, r in zip(scored.get_column("chance").to_list(), scored.get_column("rank").to_list(),
                        strict=True)
    ]  # fmt: skip
    explained, _, _ = rs.explain(model, scored, weeks=scored.get_column("_weeks").to_list())
    scored = scored.with_columns(
        pl.Series("tier", tier, dtype=pl.String),
        pl.Series("reasons_json", [rs.reasons_json(r) for r in explained], dtype=pl.String),
        pl.col("as_of").cast(pl.Datetime("us", "UTC")),
    )
    return (
        scored.select(list(SCORED_SCHEMA))
        .with_columns(pl.col("position").replace_strict(POSITION_ORDER, default=99).alias("_p"))
        .sort("_p", "rank")
        .drop("_p")
    )


# --------------------------------------------------------------------------------------
# A run: check, score, store, report
# --------------------------------------------------------------------------------------


@dataclass
class WeeklyRun:
    season: int
    week: int
    as_of: datetime
    now: datetime
    kind: str
    freshness: Freshness
    model: Any  # production.ProductionModel
    conf: cf.Confidence
    scored: pl.DataFrame
    outcomes: pl.DataFrame
    notes: dict[str, str]  # position -> note (confidence.position_notes)
    next_first_kickoff: datetime | None
    model_reused: bool = False

    @property
    def incomplete(self) -> bool:
        return not self.freshness.ok


def week_outcomes(db: Path | str, scored: pl.DataFrame) -> pl.DataFrame:
    """The labels of the scored rows as far as they are known now (C2 ``label_rows``; the
    rows of a season's last regular-season week have none), as ``outcomes`` rows."""
    from twm.modules.waiver_radar.labels import label_rows

    if scored.height == 0:
        return pl.DataFrame(
            schema={"module": pl.String, "entity_id": pl.String, "season": pl.Int32,
                    "week": pl.Int32, "as_of": pl.Datetime("us", "UTC"), "y_hit": pl.Boolean,
                    "y_sustained": pl.Boolean, "label_status": pl.String}
        )  # fmt: skip
    labelled = label_rows(db, scored.select("season", "week", "as_of", ID, "team"))
    return labelled.select(
        pl.lit(MODULE).alias("module"),
        pl.col(ID).alias("entity_id"),
        "season",
        "week",
        "as_of",
        "y_hit",
        "y_sustained",
        "label_status",
    )


def run_week(
    db: Path | str,
    season: int,
    week: int,
    *,
    model: Any,
    conf: cf.Confidence,
    now: datetime,
    allow_incomplete: bool = False,
    notes: dict[str, str] | None = None,
    model_reused: bool = False,
    real_clock: bool = True,
) -> WeeklyRun:
    """Check the week's data and score it (nothing is stored here). Raises
    :class:`NotReadyError` when the data is incomplete and ``allow_incomplete`` is False.
    ``now`` is the time of the run; unless it is the real clock (``real_clock``), the run is
    never 'live' (a pretend time can reproduce a run, not make one in real time)."""
    from twm.asof import weekly_as_of

    last = last_reg_week(db, season)
    if last is not None and week >= last:
        raise ValueError(
            f"week {week} is the last regular-season week of {season}: no games follow it in "
            "the regular season, so there is nothing to predict"
        )
    if int(season) != int(model.season):
        raise ValueError(f"the model scores season {model.season}, not {season}")
    as_of = weekly_as_of(db, season, week)
    fresh = check_freshness(freshness_inputs(db, season, week), season, week, as_of, now)
    if not fresh.ok and not allow_incomplete:
        raise NotReadyError(fresh)
    kickoff = next_kickoff(db, season, week)
    kind = run_kind(as_of, kickoff, now) if real_clock else "backtest"
    with AsOfView(db, as_of) as view:
        scored = score_week(view, season, week, model, conf)
    return WeeklyRun(
        season=season, week=week, as_of=as_of, now=now, kind=kind, freshness=fresh,
        model=model, conf=conf, scored=scored, outcomes=week_outcomes(db, scored),
        notes=notes or {}, next_first_kickoff=kickoff, model_reused=model_reused,
    )  # fmt: skip


def store_frames(
    run: WeeklyRun, *, created_at: datetime | None = None
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """(predictions, model_versions, outcomes) for :func:`twm.predictions.write_predictions`
    with ``replace='weeks'``."""
    from twm.modules.waiver_radar.production import version_frame

    created = created_at if created_at is not None else pr.now_utc()
    horizon = int(settings().horizons["waiver_radar_weeks"])
    s = run.scored
    bands = [cf.band_json(r) for r in s.iter_rows(named=True)]
    preds = s.select(
        pl.lit(MODULE).alias("module"),
        pl.lit("player").alias("entity_type"),
        pl.col(ID).alias("entity_id"),
        "season",
        "week",
        "as_of",
        pl.lit(horizon, dtype=pl.Int32).alias("horizon"),
        pl.col("position").alias("rank_group"),
        "score",
        "raw_score",
        "rank",
        pl.Series("band", bands, dtype=pl.String),
        pl.lit(run.model.model_version).alias("model_version"),
        "reasons_json",
        pl.lit(run.kind).alias("kind"),
        pl.lit(created, dtype=pl.Datetime("us")).alias("created_at"),
        "tier",
        pl.lit(run.incomplete).alias("incomplete"),
    )
    return preds, version_frame(run.model), run.outcomes


def store_week(run: WeeklyRun, store: Path | str, *, created_at: datetime | None = None) -> dict:
    preds, versions, outcomes = store_frames(run, created_at=created_at)
    return pr.write_predictions(
        store, predictions=preds, versions=versions, outcomes=outcomes, replace="weeks"
    )


# --------------------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------------------


def report_path(season: int, week: int, root: Path | None = None) -> Path:
    from twm.config import ROOT

    base = root if root is not None else ROOT
    return base / REPORT_DIR / f"{season}-W{week:02d}.md"


def _pct(x: float | None, digits: int = 0) -> str:
    return "-" if x is None else f"{100 * x:.{digits}f}%"


def _reasons(text: str | None) -> list[str]:
    return [r["text"] for r in json.loads(text or "[]")]


def _kind_text(run: WeeklyRun) -> str:
    nxt = run.next_first_kickoff
    after = f"week {run.week + 1}'s first game" + (
        f" ({to_utc(nxt):%a %Y-%m-%d %H:%M} UTC)" if nxt else ""
    )
    if run.kind == "live":
        return (
            "**Live list.** Made in real time: after the Tuesday as-of and before "
            f"{after}. Stored as 'live'."
        )
    if to_utc(run.now) < to_utc(run.as_of):
        return (
            "**Early list (stored as 'backtest').** Made before the Tuesday as-of, so it is not "
            "the official list of the week; run it again after the as-of."
        )
    if nxt is not None and to_utc(run.now) < to_utc(nxt):
        return (
            "**Reproduced list (stored as 'backtest').** Run with a set clock (`--now`) inside "
            "the live window; only a run on the real clock is stored as 'live'."
        )
    return (
        "**Reconstructed list (stored as 'backtest').** Made after "
        f"{after}, from the data as it stood at the as-of: what the Radar would have said that "
        "Tuesday, not a list made in real time."
    )


def _tier_rows(conf: cf.Confidence) -> list[list[str]]:
    rows = []
    cut = conf.cutoffs
    for r in conf.tiers.iter_rows(named=True):
        floor = {"must-add": f"{cf.MUST_ADD:.0%} or more", "speculative":
                 f"{cf.SPECULATIVE:.0%} to {cf.MUST_ADD:.0%}", "watch":
                 f"below {cf.SPECULATIVE:.0%}"}[r["tier"]]  # fmt: skip
        spec, must = cut.get("speculative"), cut.get("must-add")
        if r["tier"] == "watch":
            prob = "-" if spec is None else f"below {spec:.1%}"
        elif r["tier"] == "speculative" and spec is not None and must is not None:
            prob = f"{spec:.1%} to {must:.1%}"
        else:
            prob = "-" if r["p_from"] is None else f"{r['p_from']:.1%} and up"
        rows.append([
            f"**{r['tier']}**", floor, prob, f"{r['rows']:,}", f"{r['hits']:,}",
            _pct(r["rate"], 1), "-" if r["per_list"] is None else f"{r['per_list']:.1f}",
        ])  # fmt: skip
    return rows


def _table(header: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c).replace("|", "/") for c in r) + " |" for r in rows]
    return out


def build_report(run: WeeklyRun, *, generated: str, command: str) -> str:
    """The weekly list as markdown (deterministic apart from ``generated``)."""
    m, conf, s = run.model, run.conf, run.scored
    lg = league()
    thresholds = ", ".join(f"top {n} {p}s" for p, n in lg.starter_rank_threshold.items())
    thresholds = " or ".join(thresholds.rsplit(", ", 1))
    top10, top10_hits, top10_rows = cf.top_k_rate(conf, 10)
    cut = lg.candidate_pool_cutoffs()
    lines = [
        f"# Waiver Radar: {run.season} week {run.week}",
        "",
        generated,
        "",
        "The players most likely to become fantasy starters soon, among those probably still "
        f"on waivers in a {lg.teams}-team league, from the data as it stood on **"
        f"{to_utc(run.as_of):%A %Y-%m-%d at %H:%M} UTC** (the as-of: the moment the list is "
        f"made, after week {run.week}'s games). Made with `{command}`.",
        "",
        "> " + _kind_text(run),
        "",
    ]
    if run.incomplete:
        lines += [
            "> **Warning: incomplete data.** This list was scored with `--allow-incomplete` "
            "although some of the week's data had not arrived:",
            *[f"> - {p}" for p in run.freshness.problems],
            "> The list may change once the data is complete.",
            "",
        ]
    for note in run.freshness.notes:
        lines += [f"> Note: {note}", ""]
    lo_s, hi_s = conf.seasons
    lines += [
        "## How to read it",
        "",
        f"- **Chance**: how often players the Radar rated like him became a fantasy starter "
        f"(finished a week in the {thresholds}) within their team's next 3 games, in the "
        f"{lo_s}-{hi_s} backtest ({conf.n_predictions:,} predictions, each made by a model that "
        f"had not seen that season). In brackets, the {cf.BAND_LEVEL:.0%} range of that rate: "
        f'"similar players hit 45-55%". The **model** column is the Radar\'s own probability; '
        "it runs high for the top players, so trust the chance.",
        f"- **Priority** (top {TOP_N} only): **must-add** when similar players hit at least "
        f"{cf.MUST_ADD:.0%} of the time, **speculative** from {cf.SPECULATIVE:.0%} to "
        f"{cf.MUST_ADD:.0%}, **watch** below. How each tier really did is in the table below.",
        "- **Why**: the facts that raised his chance the most (the model's own weights), true "
        "as of the Tuesday; at most one per topic (playing time, targets, carries ...).",
        "- **When**: after Monday night's game and Tuesday's data updates (the as-of is Tuesday "
        "14:00 UTC), before your league's waivers clear (often Wednesday; check your league).",
        f"- A chance is not a promise: in {lo_s}-{hi_s}, {_pct(top10, 1)} of the Radar's "
        f"top-10 picks became starters ({top10_hits:,} of {top10_rows:,}); the rest did not.",
        "",
        f"### Suggested priority: how each tier did ({lo_s}-{hi_s}, top {TOP_N} of every list)",
        "",
    ]
    lines += _table(
        ["priority", "similar players hit", "model probability", "players", "hits",
         "hit rate", "per list"],
        _tier_rows(conf),
    )  # fmt: skip
    lines += [
        "",
        f"Counted over {int(conf.tiers.get_column('lists').max() or 0):,} weekly lists "  # type: ignore[arg-type]
        '(weeks x positions). "per list" = how many players of a top 25 get that priority in '
        "a typical week.",
    ]
    wt = conf.week_tiers(run.week)
    if wt.height and int(wt.get_column("rows").sum()):
        parts = [
            f"{r['tier']} {_pct(r['rate'], 1)} ({r['hits']:,} of {r['rows']:,})"
            for r in wt.iter_rows(named=True)
        ]
        n_lists = int(wt.get_column("lists").max() or 0)  # type: ignore[arg-type]
        lines += [
            "",
            f"Week-{run.week} lists only ({n_lists} lists of {lo_s}-{hi_s}): "
            + ", ".join(parts)
            + ". The same chance can mean a little more or less at different points of a "
            "season; these are the rates at this week.",
        ]
    lines.append("")
    for pos in FANTASY_POSITIONS:
        sub = s.filter(pl.col("position") == pos)
        top = sub.filter(pl.col("rank") <= TOP_N)
        counts = {t: int((top.get_column("tier") == t).sum()) for t in cf.TIER_ORDER}
        lines += [
            f"## {pos}",
            "",
            f"{sub.height} {pos}s in the pool (outside the top {cut[pos]} by both the preseason "
            f"list and points per game); top {min(TOP_N, sub.height)} shown: "
            + ", ".join(f"{counts[t]} {t}" for t in cf.TIER_ORDER)
            + ".",
            "",
        ]
        if pos in run.notes:
            lines += [f"**{run.notes[pos]}**", ""]
        rows = []
        for r in top.iter_rows(named=True):
            why = " · ".join(_reasons(r["reasons_json"])) or "-"
            rows.append([
                r["rank"], r["name"] or r[ID], r["team"] or "-",
                cf.band_text(r["chance"], r["band_lo"], r["band_hi"]), _pct(r["score"]),
                r["tier"] or "-", why,
            ])  # fmt: skip
        lines += _table(["#", "player", "team", "chance", "model", "priority", "why"], rows)
        lines.append("")
    fallback = rs.fallback_features()
    lines += [
        "## About this list",
        "",
        f"- **Model** `{m.model_version}`: logistic regression for `{m.label}` (at least one "
        "starter week in the next 3 games), trained on "
        f"{season_span(m.training_seasons)} ({m.n_train:,} player-weeks, {m.n_train_pos:,} "
        f"hits), settings tuned and probabilities calibrated on {m.fold.val_season}; the "
        f"walk-forward fold for test season {m.season} (never trained on {m.season}).",
        "- **Pool**: players on an NFL roster at the as-of, outside the top N at their position "
        "by both a preseason ranking and points per game (docs/waiver_radar.md).",
        f"- **Chance and priority** from the stored {conf.model} backtest of {lo_s}-{hi_s} "
        f"(bins of at least {cf.MIN_ROWS} predictions, {conf.bins.height} bins; "
        'docs/waiver_radar.md "Weekly list").',
        "- **Reasons**: each feature's contribution to the model's log-odds (its weight times "
        "how far the player's value is from the training average); the largest positive ones, "
        "one per topic, phrased from the registry (docs/glossary.md). Features phrased with a "
        "generic sentence: " + (", ".join(f"`{f}`" for f in fallback) or "none") + ".",
        "- Unofficial and educational, not affiliated with the NFL or ESPN. Predictions are "
        "probabilistic and frequently wrong. Not betting advice. Data: nflverse; rankings: "
        "FantasyPros via DynastyProcess; snap counts: Pro Football Reference.",
    ]
    return "\n".join(lines).rstrip() + "\n"


def write_report(text: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)
    return path


def compact_table(scored: pl.DataFrame, limit: int = 10) -> str:
    """The top ``limit`` of each position as plain text (for the terminal)."""
    out = []
    for pos in FANTASY_POSITIONS:
        sub = scored.filter((pl.col("position") == pos) & (pl.col("rank") <= limit))
        if sub.height == 0:
            continue
        out.append(f"{pos}")
        for r in sub.iter_rows(named=True):
            first = (_reasons(r["reasons_json"]) or ["-"])[0]
            out.append(
                f"  {r['rank']:>2}  {str(r['name'] or r[ID])[:24]:<24} {str(r['team'] or '-'):<4}"
                f" {cf.band_text(r['chance'], r['band_lo'], r['band_hi']):<34}"
                f" {str(r['tier'] or '-'):<11} {first}"
            )
    return "\n".join(out)


# --------------------------------------------------------------------------------------
# Reading a stored week back (the time machine's first form: `twm radar week`)
# --------------------------------------------------------------------------------------


def stored_week(
    store: Path | str, season: int, week: int, *, model: str = "logit", label: str = "y_hit"
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """The stored list of one week for ``model``/``label``: (rows, info). Which version when
    several scored the week: a live one first (the latest), else the store's current version
    for the season (:func:`twm.predictions.current_versions`), else the latest created. Rows
    carry the stored outcome (``y_hit``, ``label_status``) where the store has one."""
    if not Path(store).exists():
        raise FileNotFoundError(f"predictions store not found: {store}")
    con = pr.connect(store, read_only=True)
    try:
        cols = {r[0] for r in con.execute("DESCRIBE predictions").fetchall()}
        extra = ", ".join(f"p.{c}" if c in cols else f"NULL AS {c}" for c in pr.PREDICTION_DEFAULTS)
        df = con.execute(
            f"""
            SELECT p.entity_id AS gsis_id, p.season, p.week, p.as_of, p.rank_group AS position,
                   p.score, p.raw_score, p.rank, p.band, p.model_version, p.reasons_json, p.kind,
                   p.created_at, {extra}, v.test_season, v.created_at AS version_created,
                   o.y_hit, o.y_sustained, o.label_status
            FROM predictions p JOIN model_versions v USING (model_version)
            LEFT JOIN outcomes o ON o.module = p.module AND o.entity_id = p.entity_id
                 AND o.season = p.season AND o.week = p.week
            WHERE p.module = ? AND v.model = ? AND v.label = ? AND p.season = ? AND p.week = ?
            """,
            [MODULE, model, label, season, week],
        ).pl()
    finally:
        con.close()
    if df.height == 0:
        return df, {}
    versions = df.group_by("model_version").agg(
        (pl.col("kind") == "live").any().alias("live"),
        pl.col("created_at").max().alias("written"),
        pl.col("version_created").first(),
    )
    live = versions.filter(pl.col("live")).sort("written", "model_version", descending=True)
    current = pr.current_versions(store, MODULE, label).filter(
        (pl.col("model") == model) & (pl.col("test_season") == season)
    )
    cur_ids = set(current.get_column("model_version").to_list())
    if live.height:
        chosen = live.row(0, named=True)["model_version"]
    elif cur_ids & set(versions.get_column("model_version").to_list()):
        chosen = sorted(cur_ids & set(versions.get_column("model_version").to_list()))[0]
    else:
        chosen = versions.sort("version_created", "model_version", descending=True).row(
            0, named=True
        )["model_version"]
    rows = df.filter(pl.col("model_version") == chosen)
    info = {
        "model_version": chosen,
        "kind": rows.get_column("kind").unique().sort().to_list(),
        "incomplete": bool(rows.get_column("incomplete").fill_null(False).any()),
        "versions": versions.height,
        "as_of": rows.get_column("as_of").max(),
    }
    order = pl.col("position").replace_strict(POSITION_ORDER, default=99)
    return rows.sort(order, "rank"), info


def attach_names_and_outcomes(rows: pl.DataFrame, db: Path | str | None) -> pl.DataFrame:
    """Names and teams from the pool at the stored as-of, and outcomes the store does not have
    as final yet from the labels as they stand now (C2); without a warehouse, ids only."""
    out = rows.with_columns(
        pl.lit(None, dtype=pl.String).alias("name"), pl.lit(None, dtype=pl.String).alias("team")
    )
    if db is None or not Path(db).exists() or rows.height == 0:
        return out
    from twm.modules.waiver_radar.labels import label_rows

    season, week = int(rows.get_column("season")[0]), int(rows.get_column("week")[0])
    as_of = rows.get_column("as_of").max()
    as_of = as_of.replace(tzinfo=UTC) if as_of.tzinfo is None else as_of  # type: ignore[union-attr]
    with AsOfView(db, as_of) as view:
        pool = candidate_pool(view, season, week)
    who = pool.select(ID, "name", "team", "as_of")
    out = rows.join(
        who.select(pl.col(ID).alias("gsis_id"), "name", "team"), on="gsis_id", how="left"
    )
    need = out.filter(pl.col("label_status").is_null() | (pl.col("label_status") != "final"))
    if need.height:
        lab = label_rows(
            db,
            pool.filter(pl.col(ID).is_in(need.get_column("gsis_id").to_list())).select(
                "season", "week", "as_of", ID, "team"
            ),
        ).select(
            pl.col(ID).alias("gsis_id"),
            pl.col("y_hit").alias("_y"),
            pl.col("label_status").alias("_status"),
        )
        out = (
            out.join(lab, on="gsis_id", how="left")
            .with_columns(
                pl.when(pl.col("label_status") == "final")
                .then(pl.col("y_hit"))
                .otherwise(pl.col("_y"))
                .alias("y_hit"),
                pl.when(pl.col("label_status") == "final")
                .then(pl.col("label_status"))
                .otherwise(pl.col("_status"))
                .alias("label_status"),
            )  # fmt: skip
            .drop("_y", "_status")
        )
    return out


def week_text(rows: pl.DataFrame, info: Mapping[str, Any], *, limit: int = 25) -> str:
    """The stored week as plain text: per position the top ``limit`` with the chance (or the
    probability for walk-forward rows without a band), priority, outcome and first reason."""
    lines = []
    for pos in FANTASY_POSITIONS:
        sub = rows.filter((pl.col("position") == pos) & (pl.col("rank") <= limit))
        if sub.height == 0:
            continue
        n_hit = int(sub.get_column("y_hit").fill_null(False).sum())
        n_final = int((sub.get_column("label_status") == "final").sum())
        lines.append(f"{pos}  (hits so far among these: {n_hit} of {n_final} with a final outcome)")
        for r in sub.iter_rows(named=True):
            if r["band"]:
                b = json.loads(r["band"])
                chance = cf.band_text(b["chance"], b["lo"], b["hi"])
            else:
                chance = f"model {_pct(r['score'])}"
            outcome = (
                ("HIT" if r["y_hit"] else "no hit") if r["label_status"] == "final"
                else ("pending" if r["label_status"] == "pending" else "-")
            )  # fmt: skip
            first = (_reasons(r["reasons_json"]) or ["-"])[0]
            name = r["name"] or r["gsis_id"]
            lines.append(
                f"  {r['rank']:>2}  {str(name)[:24]:<24} {str(r['team'] or '-'):<4} {chance:<34}"
                f" {str(r['tier'] or '-'):<11} {outcome:<8} {first}"
            )
    return "\n".join(lines)


__all__ = [
    "EXIT_NOT_READY", "Freshness", "NotReadyError", "WeeklyRun", "build_report",
    "check_freshness", "compact_table", "default_week", "freshness_inputs",
    "last_complete_week", "next_kickoff", "report_path", "run_kind", "run_week", "score_week",
    "store_frames", "store_week", "stored_week", "week_text", "write_report",
]  # fmt: skip
