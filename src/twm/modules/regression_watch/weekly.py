"""Regression Watch step D4a: the weekly list (``twm regression score``).

One run for a season and week N mirrors the Radar's weekly run
(:mod:`twm.modules.waiver_radar.weekly`) and reuses its rules:

1. **Freshness**: the Radar's checks (:func:`twm.modules.waiver_radar.weekly.check_freshness`:
   final scores, player stats, snap counts, ffopportunity rows, injury reports, rosters, the
   Tuesday as-of has come) and, for every played game, play-by-play rows (``fact_play``: the
   garbage-time split). Anything missing: refuse (exit code 3) unless ``--allow-incomplete``.
2. **The list** (:func:`score_week`, point in time: the D1 frame read through ONE
   :class:`~twm.asof.AsOfView` at the week's official as-of): the universe, the projection and
   the Sell-high and Buy-low tags of D3 (:mod:`.projection`, :mod:`.tags`) with the
   APPROVED frozen parameters (:mod:`.production`); nothing is estimated. D3's third tag,
   Legit, is not assigned (owner, 2026-09-30: it predicted nothing, :data:`DROPPED_TAG`).
3. **Live or reconstructed**: the Radar's clock rule (:func:`twm.modules.waiver_radar.weekly.
   run_kind`): 'live' only on the real clock between the as-of and week N+1's first kickoff.
4. **Storing**: every universe row (``module`` 'regression_watch', ``entity_type`` 'player',
   ``horizon`` = the regular-season weeks left after N (the rest of the season), ``score`` =
   projected points per game, ``rank`` within the position, ``band`` = the tag, ``reasons_json``
   = the numbers behind it). A stored live week is never overwritten.
5. **Outcomes** (:func:`season_outcomes`): once a season's regular season is over, each stored
   row gets its actual rest-of-season points per game (``outcomes.y_value``, 'final').
6. **The report** ``reports/regression_watch/weekly/<season>-W<nn>.md`` (:func:`build_report`).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from twm import predictions as pr
from twm.config import League
from twm.modules.regression_watch import projection as pj
from twm.modules.regression_watch import tags as tg
from twm.modules.regression_watch.production import MODULE, ProductionParams
from twm.modules.waiver_radar import weekly as rw

REPORT_DIR = Path("reports/regression_watch/weekly")
EXIT_NOT_READY = rw.EXIT_NOT_READY
ENTITY_TYPE = "player"
# The tags the product assigns; a row's band is its first tag in this order. D3 tested a third,
# Legit (tags.TAGS): it predicted nothing (60.6% of the players it tagged stayed starters, the
# base rate was 61.1%), so the owner dropped it on 2026-09-30. The frozen backtest lists were
# made with all three (TESTED_TAGS, frozen.build_snapshot); the publish strips Legit from them.
BAND_ORDER = ("sell_high", "buy_low")
DROPPED_TAG = "legit"
TESTED_TAGS = tg.TAGS  # the three tags of the D3 backtest, in band order


def freshness_inputs(db: Path | str, season: int, week: int) -> dict[str, pl.DataFrame]:
    """The Radar's inputs plus the per-game play-by-play row counts."""
    out = rw.freshness_inputs(db, season, week)
    ids = out["games"].get_column("game_id").to_list() or [""]
    con = rw._connect(db)
    try:
        out["plays"] = con.execute(
            f"SELECT game_id, count(*) AS n FROM fact_play WHERE game_id IN "
            f"({', '.join('?' for _ in ids)}) GROUP BY game_id",
            ids,
        ).pl()
    finally:
        con.close()
    return out


def check_freshness(
    inputs: Mapping[str, pl.DataFrame], season: int, week: int, as_of: datetime, now: datetime
) -> rw.Freshness:
    """The Radar's checks, then: every played game has play-by-play rows."""
    fr = rw.check_freshness(inputs, season, week, as_of, now)
    games = inputs["games"]
    if games.height == 0:
        return fr
    played = games.filter(pl.col("has_result"))
    have = set(inputs["plays"].filter(pl.col("n") > 0).get_column("game_id").to_list())
    missing = played.filter(~pl.col("game_id").is_in(list(have)))
    if missing.height:
        fr.problems.append(
            f"{missing.height} played game(s) have no play-by-play rows (fact_play) yet: "
            f"{rw._matchups(missing)}"
        )
    return fr


# --------------------------------------------------------------------------------------
# The list (the point-in-time function)
# --------------------------------------------------------------------------------------


def _r(x: Any) -> float | None:
    return None if x is None else round(float(x), pj.STATE_ROUND)


def reasons(
    row: Mapping[str, Any], params: ProductionParams, tags: Sequence[str] = BAND_ORDER
) -> dict[str, Any]:
    """The numbers behind one row's projection and tag (stored as ``reasons_json``);
    ``tags``: the tags assigned, in band order."""
    v = params.variant
    m = v.metric
    xfp_used = row[f"rw_xfp_ng_{pj.hl_suffix(v.half_life)}"] * row["ng_scale"] if (
        v.garbage == "ng") else row[f"rw_xfp_{pj.hl_suffix(v.half_life)}"]  # fmt: skip
    toward = row[f"mean_{m}"] if v.target == "mean" else 0.0
    return {
        "ppg": _r(row["ppg"]), "xfp_pg": _r(row["xfp_pg"]), "fpoe_pg": _r(row["fpoe_pg"]),
        "games": int(row["games"]), "games_with_opportunity": int(row["g_opp"]),
        "last3_ppg": _r(row["last3_ppg"]), "ppg_rank": int(row["ppg_rank"]),
        "xfp_rank": int(row["xfp_rank"]), "shrinkage_metric": m,
        "shrinkage": _r(row[f"r_{m}"]), "shrink_toward": _r(toward),
        "xfp_used": _r(xfp_used), "fpoe_used": _r(row["ppg_ros"] - xfp_used),
        "projection": _r(row["ppg_ros"]), "gap": _r(row["ppg_ros"] - row["ppg"]),
        "fpoe_top_decile": bool(row["fpoe_top"]), "fpoe_bottom_decile": bool(row["fpoe_bottom"]),
        "tags": [t for t in tags if row[t]],
        "x": {"sell_high": params.x_sell, "buy_low": params.x_buy},
        "without_garbage_time": {
            "ppg": _r(row["ppg_ng"]), "xfp_pg": _r(row["xfp_ng_pg"]),
            "fpoe_pg": _r(row["fpoe_ng_pg"]), "shrinkage": _r(row["r_fpoe_ng"]),
            "shrink_toward": _r(row["mean_fpoe_ng"] if v.target == "mean" else 0.0),
        },
    }  # fmt: skip


def score_week(
    std: pl.DataFrame, names: pl.DataFrame, params: ProductionParams, league: League, week: int,
    *, tags: Sequence[str] = BAND_ORDER,
) -> pl.DataFrame:  # fmt: skip
    """The universe at an as-of with its projection, tags, band, rank (within the position, by
    projection; ties by gsis_id) and reasons: ``std`` is ONE season's D1 frame as public at
    the as-of, ``names`` (gsis_id, name). Exactly D3's :func:`~twm.modules.regression_watch.
    backtest.project_week` with the frozen parameters. Empty when nobody has 3 games yet.
    ``tags``: the tags assigned (a column each), in band order: the product's two by default;
    :data:`TESTED_TAGS` (Legit too) reproduces the frozen backtest lists."""
    unknown = [t for t in tags if t not in TESTED_TAGS]
    if not tags or unknown or list(tags) != [t for t in TESTED_TAGS if t in tags]:
        raise ValueError(f"tags must be among {TESTED_TAGS}, in that order, not {tuple(tags)}")
    if std.height == 0:
        return pl.DataFrame()
    state = pj.player_state(std, league)
    uni = state.filter(pl.col("in_universe"))
    if uni.height == 0:
        return pl.DataFrame()
    uni = pj.project(uni, params.priors(), params.variant)
    uni = tg.decile_flags(uni.with_columns(pl.lit(int(week), dtype=pl.Int32).alias("week")))
    exprs = tg.tag_exprs(league, params.x_sell, params.x_buy)
    table = (
        uni.with_columns(*[e for e in exprs if e.meta.output_name() in tags])
        .join(names, on="gsis_id", how="left")
        .sort(["position", "ppg_ros", "gsis_id"], descending=[False, True, False])
        .with_columns(pl.int_range(1, pl.len() + 1).over("position").cast(pl.Int32).alias("rank"))
    )
    band = pl.when(pl.col(tags[0])).then(pl.lit(tags[0]))
    for t in tags[1:]:
        band = band.when(pl.col(t)).then(pl.lit(t))
    texts = [json.dumps(reasons(r, params, tags), sort_keys=True, separators=(",", ":"))
             for r in table.iter_rows(named=True)]  # fmt: skip
    return table.with_columns(
        band.otherwise(pl.lit(None, dtype=pl.String)).alias("band"),
        pl.Series("reasons_json", texts, dtype=pl.String),
    )


# --------------------------------------------------------------------------------------
# A run: check, list, store
# --------------------------------------------------------------------------------------


@dataclass
class WeeklyRun:
    season: int
    week: int
    as_of: datetime
    now: datetime
    kind: str  # 'live' or 'backtest' (reconstructed)
    freshness: rw.Freshness
    params: ProductionParams
    table: pl.DataFrame  # score_week(); empty before anyone has 3 games
    next_first_kickoff: datetime | None
    horizon: int  # regular-season weeks after this one: the rest of the season

    @property
    def incomplete(self) -> bool:
        return not self.freshness.ok


def run_week(
    db: Path | str,
    season: int,
    week: int,
    *,
    params: ProductionParams,
    league: League,
    now: datetime,
    allow_incomplete: bool = False,
    real_clock: bool = True,
    live: Any = None,
) -> WeeklyRun:
    """Check the week's data and make the list (nothing is stored here);
    :class:`~twm.modules.waiver_radar.weekly.NotReadyError` when data is missing and
    ``allow_incomplete`` is False. Unless ``now`` is the real clock (``real_clock``), the run is
    never 'live'. Everything the list reads goes through one view at the week's as-of.

    ``live``: the approved own xFP models (:func:`.production.load_pinned_xfp`), required when
    the parameters were made with the own xFP (step H6-b2): the season's plays public at the
    as-of are scored with them (nothing is fit) and replace ffopportunity's expected points in
    the frame (:func:`.own_xfp.live_frame`; the player pages' season in progress too)."""
    from twm.asof import AsOfView, weekly_as_of
    from twm.modules.regression_watch.player_week import player_games_for

    if int(season) != int(params.season):
        raise ValueError(f"the approved parameters score {params.season}, not {season}")
    if (params.xfp_source == "own") != (live is not None):
        raise ValueError(f"the approved parameters use the {params.xfp_source} xFP: "
                         + ("the pinned live models are needed" if live is None
                            else "live models do not apply"))  # fmt: skip
    last = rw.last_reg_week(db, season)
    if last is not None and week >= last:
        raise ValueError(
            f"week {week} is the last regular-season week of {season}: no game follows it, so "
            "there is no rest of the season to project"
        )
    as_of = weekly_as_of(db, season, week)
    fresh = check_freshness(freshness_inputs(db, season, week), season, week, as_of, now)
    if not fresh.ok and not allow_incomplete:
        raise rw.NotReadyError(fresh)
    kickoff = rw.next_kickoff(db, season, week)
    kind = rw.run_kind(as_of, kickoff, now) if real_clock else "backtest"
    with AsOfView(db, as_of) as view:
        if live is not None:  # the player pages' season in progress uses the same function
            from twm.modules.regression_watch.own_xfp import live_frame

            std = live_frame(view, season, live)
        else:
            std = player_games_for(view, season)
        names = view.sql("SELECT gsis_id, display_name AS name FROM dim_player")
    table = score_week(std, names, params, league, week)
    horizon = (last - week) if last is not None else 0
    return WeeklyRun(season, week, as_of, now, kind, fresh, params, table, kickoff, horizon)


def list_rows(
    table: pl.DataFrame, params: ProductionParams, *, as_of: datetime, horizon: int, kind: str,
    created: datetime, incomplete: bool,
) -> pl.DataFrame:  # fmt: skip
    """The store's ``predictions`` rows of one list (:func:`score_week`'s table): every universe
    row. The published backtest lists (twm.publish.regression_lists) are made with it too."""
    from twm.asof import to_utc

    return table.select(
        pl.lit(MODULE).alias("module"), pl.lit(ENTITY_TYPE).alias("entity_type"),
        pl.col("gsis_id").alias("entity_id"), pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        pl.lit(to_utc(as_of).replace(tzinfo=None), dtype=pl.Datetime("us")).alias("as_of"),
        pl.lit(horizon, dtype=pl.Int32).alias("horizon"),
        pl.col("position").alias("rank_group"), pl.col("ppg_ros").alias("score"),
        pl.lit(None, dtype=pl.Float64).alias("raw_score"), "rank", "band",
        pl.lit(params.model_version).alias("model_version"), "reasons_json",
        pl.lit(kind).alias("kind"),
        pl.lit(created, dtype=pl.Datetime("us")).alias("created_at"),
        pl.lit(None, dtype=pl.String).alias("tier"), pl.lit(incomplete).alias("incomplete"),
    )  # fmt: skip


def store_frames(run: WeeklyRun, *, created_at: datetime | None = None):
    """(predictions, model_versions) for :func:`twm.predictions.write_predictions`
    (``replace='weeks'``): every universe row of the list."""
    from twm.modules.regression_watch.production import version_frame

    created = created_at if created_at is not None else pr.now_utc()
    preds = list_rows(run.table, run.params, as_of=run.as_of, horizon=run.horizon,
                      kind=run.kind, created=created, incomplete=run.incomplete)  # fmt: skip
    return preds, version_frame(run.params, created_at=created)


def store_week(run: WeeklyRun, store: Path | str, *, created_at: datetime | None = None) -> dict:
    preds, versions = store_frames(run, created_at=created_at)
    return pr.write_predictions(store, predictions=preds, versions=versions, replace="weeks")


# --------------------------------------------------------------------------------------
# Outcomes: the rest-of-season points per game, once the regular season is over
# --------------------------------------------------------------------------------------


def season_final(db: Path | str, season: int, now: datetime) -> bool:
    """True when ``season``'s regular season is over at ``now``: its last week's official
    as-of has passed and every regular-season game has a final score in the data."""
    from twm.asof import to_utc, weekly_as_of

    last = rw.last_reg_week(db, season)
    if last is None or to_utc(now) < to_utc(weekly_as_of(db, season, last)):
        return False
    con = rw._connect(db)
    try:
        row = con.execute(
            "SELECT count(*) FILTER (WHERE result IS NULL), count(*) FROM fact_game "
            "WHERE season = ? AND season_type = 'REG'",
            [season],
        ).fetchone()
    finally:
        con.close()
    return row is not None and int(row[1]) > 0 and int(row[0]) == 0


def season_outcomes(
    db: Path | str, store: Path | str, now: datetime, *, seasons: list[int] | None = None
) -> pl.DataFrame:
    """The outcomes of the stored Regression Watch rows (``seasons``, default every stored
    season) whose regular season is over at ``now`` (:func:`season_final`): per (player,
    season, week) his points per game over the regular-season games public after that week's
    as-of (``y_value``; NULL with fewer than 3 such games, which the backtest does not grade
    either), label_status 'final'. The warehouse is read through one view at ``now``."""
    from twm.asof import AsOfView
    from twm.modules.regression_watch.backtest import rest_of_season
    from twm.modules.regression_watch.player_week import player_games_for

    empty = pl.DataFrame(schema={c: pl.String for c in pr.OUTCOME_COLUMNS})
    if not Path(store).exists():
        return empty.clear()
    con = pr.connect(store, read_only=True)
    try:
        keys = con.execute(
            "SELECT DISTINCT entity_id, season, week, as_of FROM predictions WHERE module = ? "
            "ORDER BY season, week, entity_id",
            [MODULE],
        ).pl()
    finally:
        con.close()
    wanted = sorted(set(keys.get_column("season").to_list()))
    wanted = [s for s in wanted if seasons is None or s in seasons]
    parts = []
    for season in [s for s in wanted if season_final(db, s, now)]:
        with AsOfView(db, now) as view:
            frame = player_games_for(view, season)
        for (_week, as_of), grp in keys.filter(pl.col("season") == season).group_by(
            "week", "as_of", maintain_order=True
        ):
            ros = rest_of_season(frame, as_of if as_of.tzinfo else as_of.replace(tzinfo=UTC))
            parts.append(grp.join(ros, left_on="entity_id", right_on="gsis_id", how="left"))
    if not parts:
        return empty.clear()
    out = pl.concat(parts, how="vertical_relaxed")
    return out.select(
        pl.lit(MODULE).alias("module"), "entity_id", pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32), pl.col("as_of").cast(pl.Datetime("us")),
        pl.lit(None, dtype=pl.Boolean).alias("y_hit"),
        pl.lit(None, dtype=pl.Boolean).alias("y_sustained"),
        pl.lit("final").alias("label_status"),
        pl.when(pl.col("ros_games").fill_null(0) >= pj.MIN_GAMES).then(pl.col("ros_ppg"))
        .otherwise(None).cast(pl.Float64).alias("y_value"),
    ).sort("season", "week", "entity_id")  # fmt: skip


def update_outcomes(db: Path | str, store: Path | str, now: datetime) -> int:
    """Write :func:`season_outcomes` into the store; the rows written."""
    outs = season_outcomes(db, store, now)
    return pr.write_outcomes(store, outs) if outs.height else 0


# --------------------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------------------


def report_path(season: int, week: int, root: Path | None = None) -> Path:
    from twm.config import ROOT

    return (root if root is not None else ROOT) / REPORT_DIR / f"{season}-W{week:02d}.md"


def tag_notes(csv_path: Path, season: int) -> dict[str, str]:
    """Per tag, how its picks did in the committed backtest (pooled, headline weeks) next to
    the base rate, read from reports/regression_watch/backtest.csv; under :data:`DROPPED_TAG`,
    why Legit is not shown (:func:`dropped_note`). Empty when the file is missing or its test
    seasons reach ``season`` (a list never quotes outcomes after its as-of)."""
    if not csv_path.exists():
        return {}
    ev = pl.read_csv(csv_path, infer_schema_length=0)
    tested = ev.filter(pl.col("table") == "choice").get_column("season").cast(pl.Int64)
    if tested.len() == 0 or int(tested.max()) >= int(season):  # type: ignore[arg-type]
        return {}
    span = f"{int(tested.min())}-{int(tested.max())}"  # type: ignore[arg-type]
    rows = ev.filter((pl.col("table") == "tag") & (pl.col("weeks") == "headline")
                     & (pl.col("position") == "all"))  # fmt: skip

    def get(tag: str, group: str) -> dict | None:
        hit = rows.filter((pl.col("metric") == tag) & (pl.col("group") == group))
        return hit.row(0, named=True) if hit.height else None

    out = {}
    for tag, what, base in (
        ("sell_high", "fell below their PPG", "every universe player"),
        ("buy_low", "rose above their PPG", "every universe player"),
    ):
        t, b = get(tag, "tagged"), get(tag, "base")
        if not (t and b):
            continue
        tv, bv = float(t["value"]), float(b["value"])
        text = (f"In the {span} backtest (as-of weeks 4, 6, 8, 10), {tv:.1%} of the "
                f"{int(t['n']):,} {tg.TAG_TITLES[tag]} players {what} for the rest of the "
                f"season (95% interval {float(t['lo']):.1%} to {float(t['hi']):.1%}); the base "
                f"rate for {base} was {bv:.1%}.")  # fmt: skip
        out[tag] = text
    t, b = get(DROPPED_TAG, "tagged"), get(DROPPED_TAG, "base")
    if t and b:
        out[DROPPED_TAG] = dropped_note(float(t["value"]), float(b["value"]), int(t["n"]), span)
    return out


DROPPED_TEXT = (
    "A third tag, Legit (a starter whose points over expected are not in the top 10% of his "
    "position), was tested and dropped because it predicted nothing"
)


def dropped_note(rate: float, base: float, n: int, span: str) -> str:
    """Why the list has no Legit tag, with the backtest's two rates (backtest.csv)."""
    return (f"{DROPPED_TEXT}: in the {span} backtest (as-of weeks 4, 6, 8, 10), {rate:.1%} of "
            f"the {n:,} players it tagged stayed inside the starter threshold for the rest of the "
            f"season, and so did {base:.1%} of every player inside it.")  # fmt: skip


FIRST_BACKTESTED_WEEK, LAST_BACKTESTED_WEEK = 4, 14  # D3's as-of weeks (backtest.ALL_WEEKS)


def early_note(week: int) -> str | None:
    """The warning a list made before the first backtested as-of carries (the weekly report and
    the published regression_list.note; reviewer's rule after D4a); None from week 4 on."""
    if int(week) >= FIRST_BACKTESTED_WEEK:
        return None
    return (
        f"Week {week} is earlier than the backtested weeks {FIRST_BACKTESTED_WEEK}-"
        f"{LAST_BACKTESTED_WEEK}: nobody has more than {week} games yet, and the projection and "
        "the tags were never checked this early in the season, so treat them as rough."
    )


def _kind_text(run: WeeklyRun) -> str:
    from twm.asof import to_utc

    nxt = run.next_first_kickoff
    after = f"week {run.week + 1}'s first game" + (
        f" ({to_utc(nxt):%a %Y-%m-%d %H:%M} UTC)" if nxt else "")  # fmt: skip
    if run.kind == "live":
        return f"**Live list.** Made in real time, after the Tuesday as-of and before {after}."
    if to_utc(run.now) < to_utc(run.as_of):
        return "**Early list (stored as 'backtest').** Made before the Tuesday as-of."
    if nxt is not None and to_utc(run.now) < to_utc(nxt):
        return ("**Reproduced list (stored as 'backtest').** Run with a set clock inside the live "
                "window; only a run on the real clock is stored as 'live'.")  # fmt: skip
    return (
        f"**Reconstructed list (stored as 'backtest').** Made after {after}, from the data "
        "as it stood at the as-of: what Regression Watch would have said that Tuesday."
    )


HEAD = ["player", "pos", "team", "games", "PPG", "xFP/game", "FPOE/game", "shrink",
        "projection", "projection - PPG"]  # fmt: skip


def _row(r: Mapping[str, Any], metric: str) -> list[object]:
    return [r["name"] or r["gsis_id"], r["position"], r["team"] or "-", r["games"],
            f"{r['ppg']:.1f}", f"{r['xfp_pg']:.1f}", f"{r['fpoe_pg']:+.1f}",
            f"{r['r_' + metric]:.2f}", f"{r['ppg_ros']:.1f}",
            f"{r['ppg_ros'] - r['ppg']:+.1f}"]  # fmt: skip


def _tag_lines(run: WeeklyRun, tag: str, intro: str, note: str | None) -> list[str]:
    if run.table.height == 0:
        return [f"## {tg.TAG_TITLES[tag]} (0)", "", intro, "", "No player has "
                f"{pj.MIN_GAMES} games yet.", ""]  # fmt: skip
    t = run.table.filter(pl.col(tag))
    gap = pl.col("ppg_ros") - pl.col("ppg") if tag == "buy_low" else pl.col("ppg") - pl.col(
        "ppg_ros")  # fmt: skip
    t = t.with_columns(gap.alias("_gap")).sort(["_gap", "gsis_id"], descending=[True, False])
    lines = [f"## {tg.TAG_TITLES[tag]} ({t.height})", "", intro]
    if note:
        lines += ["", note]
    lines.append("")
    if t.height == 0:
        return [*lines, f"No {tg.TAG_TITLES[tag]} player this week.", ""]
    rows = [_row(r, run.params.variant.metric) for r in t.iter_rows(named=True)]
    return [*lines, *rw._table(HEAD, rows), ""]


def build_report(
    run: WeeklyRun, league: League, *, generated: str, command: str,
    notes: Mapping[str, str] | None = None,
) -> str:  # fmt: skip
    """The weekly markdown (deterministic apart from ``generated``)."""
    from twm.asof import to_utc

    notes = notes or {}
    p = run.params
    sizes = pj.universe_sizes(league)
    s0, s1 = p.shrinkage_seasons[0], p.shrinkage_seasons[-1]
    size_text = ", ".join(f"{k} {sizes[k]}" for k in pj.FANTASY_POSITIONS)
    lines = [
        f"# Regression Watch: {run.season} week {run.week}", "",
        f"{generated} Command: `{command}`.", "",
        f"- {_kind_text(run)}",
        *([f"- **Early in the season.** {early_note(run.week)}"] if early_note(run.week) else []),
        f"- **As-of** {to_utc(run.as_of):%a %Y-%m-%d %H:%M} UTC: only data public then is used. "
        f"The projection is points per game over the rest of the regular season (weeks "
        f"{run.week + 1} on, {run.horizon} weeks).",
        f"- **Players** ({run.table.height}): QBs, RBs, WRs and TEs with at least "
        f"{pj.MIN_GAMES} games whose points per game (PPG) or expected points per game (xFP) "
        f"ranks inside {size_text}.",
    ]  # fmt: skip
    if run.incomplete:
        lines.append("- **WARNING: incomplete data** (scored with --allow-incomplete): "
                     + "; ".join(run.freshness.problems))  # fmt: skip
    lines += [
        "", "## How the list is made", "",
        "- **xFP/game** = the points an average player would score with his targets, carries "
        "and throws; **FPOE/game** = his PPG minus that: efficiency and luck. Opportunity "
        "repeats; FPOE mostly does not.",
        f"- **Projection** = xFP/game + shrink x FPOE/game ({p.variant.describe()}). **Shrink** "
        f"= how much of his FPOE/game past seasons say is real after his games so far "
        f"(estimated on {s0}-{s1}).",
        f"- Approved parameters `{p.model_version}` (config/production_models.yaml), chosen on "
        f"the {p.choice['validation_seasons'][0]}-{p.choice['validation_seasons'][-1]} "
        "seasons; nothing is re-estimated.",
        "- **Two tags only.** " + (notes.get(DROPPED_TAG) or DROPPED_TEXT
                                   + " (reports/regression_watch/backtest.md)."), "",
    ]  # fmt: skip
    intros = {
        "sell_high": f"FPOE/game in the top 10% of his position AND the projection at least "
                     f"{p.x_sell:g} points/game below his PPG: he has been lucky and should cool "
                     "off.",
        "buy_low": f"FPOE/game in the bottom 10% of his position AND the projection at least "
                   f"{p.x_buy:g} points/game above his PPG: he has been unlucky and should "
                   "pick up.",
    }  # fmt: skip
    for tag in BAND_ORDER:
        lines += _tag_lines(run, tag, intros[tag], notes.get(tag))
    return "\n".join(lines).rstrip() + "\n"


def write_report(text: str, path: Path) -> Path:
    return rw.write_report(text, path)
