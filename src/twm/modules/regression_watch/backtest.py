"""Regression Watch step D3: the walk-forward backtest of the projection and the tags
(PROJECT_SPEC 8.2 Evaluation, 10 P1 acceptance).

For every season V from 2010 and every as-of week (4-14): the universe at the week's official
Tuesday as-of (season to date only), each :data:`projection.VARIANTS` projection with the
shrinkage estimated on seasons 2009 .. V-1, the two baselines (season-to-date PPG, last-3-games
PPG) and the outcome: points per game over the rest of the regular season (rows that became
public strictly after the as-of; players with fewer than 3 remaining games are left out and
counted).

A projection of season V only depends on V's season to date and seasons < V, so it is computed
once. Test season S (from 2011: two earlier seasons of 2009+ data) then uses the variant with
the lowest MAE, and the Sell-high / Buy-low X with the best precision, on the validation
seasons 2010 .. S-1 (headline weeks 4, 6, 8, 10): never on S itself.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from twm.asof import outcomes_after, to_utc
from twm.backtest.metrics import Interval
from twm.config import League
from twm.modules.regression_watch import projection as pj
from twm.modules.regression_watch import tags as tg
from twm.modules.regression_watch.player_week import visible

HEADLINE_WEEKS = (4, 6, 8, 10)
ALL_WEEKS = tuple(range(4, 15))
FIRST_VALIDATION_SEASON = pj.FIRST_DATA_SEASON + 1  # its shrinkage comes from 2009
FIRST_TEST_SEASON = pj.FIRST_DATA_SEASON + 2  # two earlier seasons of 2009+ data
METHODS = ("model", "baseline_ppg", "baseline_last3", "spec_formula")
METHOD_TITLES = {
    "model": "Projection (variant chosen on earlier seasons)",
    "baseline_ppg": "Season-to-date PPG",
    "baseline_last3": "Last-3-games PPG",
    "spec_formula": "Spec formula as written (toward 0, flat, garbage kept)",
}
PREDICTION = {"baseline_ppg": "ppg", "baseline_last3": "last3_ppg"}

Progress = Callable[[str], None]


def asof_table(db: Path | str, seasons: Sequence[int], weeks: Sequence[int]) -> pl.DataFrame:
    """(season, week, as_of): the official Tuesday as-of of each regular-season week, from
    ``dim_week`` (read-only)."""
    from twm.warehouse.build import connect

    con = connect(db, read_only=True)
    try:
        df = con.execute(
            "SELECT season, week, asof_weekly_utc AS as_of FROM dim_week "
            "WHERE season_type = 'REG' AND season IN (SELECT unnest(?)) "
            "AND week IN (SELECT unnest(?)) ORDER BY season, week",
            [list(map(int, seasons)), list(map(int, weeks))],
        ).pl()
    finally:
        con.close()
    return df.with_columns(pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32))


def rest_of_season(
    season_frame: pl.DataFrame, as_of: datetime, *, min_games: int = pj.MIN_GAMES
) -> pl.DataFrame:
    """Per player of one season: ``ros_games`` and ``ros_ppg`` over the rows public strictly
    after ``as_of`` (the rest of the regular season), and ``ros_rank``: his rest-of-season PPG
    rank within his position among every player with at least ``min_games`` remaining games
    (position: his latest game's by the as-of, else his first later game's)."""
    before = (
        visible(season_frame, as_of)
        .sort(["gsis_id", "week"])
        .group_by("gsis_id")
        .agg(pl.col("position").last().alias("_pos_std"))
    )
    after = outcomes_after(season_frame, as_of).sort(["gsis_id", "week"])
    ros = after.group_by("gsis_id").agg(
        pl.len().alias("ros_games"),
        pl.col("fantasy_points").mean().round(pj.STATE_ROUND).alias("ros_ppg"),
        pl.col("position").first().alias("_pos_ros"),
    )
    ros = ros.join(before, on="gsis_id", how="left").with_columns(
        pl.coalesce("_pos_std", "_pos_ros").alias("ros_position")
    )
    ranked = (
        ros.filter(pl.col("ros_games") >= min_games)
        .sort(["ros_position", "ros_ppg", "gsis_id"], descending=[False, True, False])
        .with_columns(pl.int_range(1, pl.len() + 1).over("ros_position").alias("ros_rank"))
        .select("gsis_id", "ros_rank")
    )
    return ros.join(ranked, on="gsis_id", how="left").select(
        "gsis_id", "ros_games", "ros_ppg", "ros_position", "ros_rank"
    )


def proj_col(variant: pj.Variant) -> str:
    return f"proj_{variant.name}"


def week_rows(
    season_frame: pl.DataFrame,
    week: int,
    as_of: datetime,
    priors: pj.Priors,
    league: League,
    variants: Sequence[pj.Variant] = pj.VARIANTS,
) -> pl.DataFrame:
    """The universe of one as-of: state, every variant's projection (``proj_<name>``), decile
    flags, the rest of the season and ``evaluable`` (at least 3 remaining games)."""
    state = pj.player_state(visible(season_frame, as_of), league)
    uni = pj.with_priors(state.filter(pl.col("in_universe")), priors).with_columns(
        pl.lit(int(week), dtype=pl.Int32).alias("week"),
        pl.lit(to_utc(as_of).replace(tzinfo=None)).alias("as_of"),
        *[pj.projection_expr(v).alias(proj_col(v)) for v in variants],
    )
    uni = tg.decile_flags(uni)
    ros = rest_of_season(season_frame, as_of)
    out = uni.join(ros.drop("ros_position"), on="gsis_id", how="left").with_columns(
        pl.col("ros_games").fill_null(0),
        (pl.col("ros_games").fill_null(0) >= pj.MIN_GAMES).alias("evaluable"),
    )
    return out.sort(["position", "ppg_rank"])


@dataclass(frozen=True)
class SeasonPriors:
    season: int
    priors: pj.Priors


def build_rows(
    frame: pl.DataFrame,
    asofs: pl.DataFrame,
    league: League,
    seasons: Sequence[int],
    *,
    variants: Sequence[pj.Variant] = pj.VARIANTS,
    progress: Progress | None = None,
) -> tuple[pl.DataFrame, list[SeasonPriors]]:
    """Every universe row of ``seasons`` x the as-of weeks in ``asofs`` (season, week, as_of),
    each season projected with priors from seasons FIRST_DATA_SEASON .. season - 1 only."""
    parts: list[pl.DataFrame] = []
    used: list[SeasonPriors] = []
    for season in sorted({int(s) for s in seasons}):
        earlier = list(range(pj.FIRST_DATA_SEASON, season))
        if not earlier:
            raise ValueError(f"season {season}: no earlier season to estimate the shrinkage")
        priors = pj.estimate_priors(frame, earlier)
        used.append(SeasonPriors(season, priors))
        sf = frame.filter(pl.col("season") == season)
        weeks = asofs.filter(pl.col("season") == season).sort("week")
        for week, as_of in weeks.select("week", "as_of").iter_rows():
            when = as_of if as_of.tzinfo else as_of.replace(tzinfo=UTC)
            part = week_rows(sf, int(week), when, priors, league, variants)
            if part.height:
                parts.append(part)
        if progress is not None:
            n = sum(p.height for p in parts if p.item(0, "season") == season)
            progress(f"season {season}: {weeks.height} as-ofs, {n} universe rows")
    rows = pl.concat(parts, how="vertical_relaxed") if parts else pl.DataFrame()
    return rows.sort(["season", "week", "position", "ppg_rank"]), used


# --------------------------------------------------------------------------------------
# Choices made on validation seasons only
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Choice:
    """What test season ``season`` uses, chosen on ``validation_seasons`` (all < season)."""

    season: int
    variant: pj.Variant
    validation_seasons: tuple[int, ...]
    val_mae: float | None
    spec_val_mae: float | None
    n_val: int
    sell: tg.Threshold
    buy: tg.Threshold


def validation_rows(
    rows: pl.DataFrame, season: int, weeks: Sequence[int] = HEADLINE_WEEKS
) -> pl.DataFrame:
    """The graded rows of the seasons before ``season`` (from FIRST_VALIDATION_SEASON), at the
    ``weeks`` as-ofs: never a row of ``season`` or later."""
    return rows.filter(
        (pl.col("season") >= FIRST_VALIDATION_SEASON)
        & (pl.col("season") < season)
        & pl.col("week").is_in(list(weeks))
        & pl.col("evaluable")
    )


def variant_maes(
    val: pl.DataFrame, variants: Sequence[pj.Variant] = pj.VARIANTS
) -> dict[str, float]:
    """Pooled MAE of each variant's projection on ``val`` (rounded to 6 decimals)."""
    if val.height == 0:
        return {}
    out = val.select(
        (pl.col(proj_col(v)) - pl.col("ros_ppg")).abs().mean().alias(v.name) for v in variants
    )
    return {k: round(float(x), 6) for k, x in out.row(0, named=True).items()}


def choose(
    rows: pl.DataFrame,
    season: int,
    *,
    weeks: Sequence[int] = HEADLINE_WEEKS,
    variants: Sequence[pj.Variant] = pj.VARIANTS,
) -> Choice:
    """The variant with the lowest validation MAE (ties: grid order, the spec formula first)
    and, with its projections, the Sell-high and Buy-low X (tags.choose_x), all on the seasons
    before ``season``. With no validation season: the spec formula and the smallest X."""
    val = validation_rows(rows, season, weeks)
    maes = variant_maes(val, variants)
    if maes:
        best = min(variants, key=lambda v: (maes[v.name], variants.index(v)))
    else:
        best = pj.SPEC_VARIANT if pj.SPEC_VARIANT in variants else variants[0]
    vrows = val.with_columns(pl.col(proj_col(best)).alias("ppg_ros"))
    seasons = tuple(sorted(val.get_column("season").unique().to_list())) if val.height else ()
    return Choice(
        season=int(season),
        variant=best,
        validation_seasons=seasons,
        val_mae=maes.get(best.name),
        spec_val_mae=maes.get(pj.SPEC_VARIANT.name),
        n_val=val.height,
        sell=tg.choose_x(vrows, "sell_high"),
        buy=tg.choose_x(vrows, "buy_low"),
    )


def graded_rows(rows: pl.DataFrame, choices: Mapping[int, Choice], league: League) -> pl.DataFrame:
    """The rows of the test seasons in ``choices`` with each method's prediction
    (``pred_<method>``), the chosen projection as ``ppg_ros``, the tags and their hits."""
    parts = []
    for season, ch in sorted(choices.items()):
        part = rows.filter(pl.col("season") == season)
        if part.height == 0:
            continue
        parts.append(
            part.with_columns(
                pl.col(proj_col(ch.variant)).alias("ppg_ros"),
                pl.lit(ch.variant.name).alias("variant"),
                pl.lit(ch.sell.x).alias("x_sell"),
                pl.lit(ch.buy.x).alias("x_buy"),
            )
        )
    if not parts:
        return pl.DataFrame()
    g = pl.concat(parts, how="vertical_relaxed")
    return g.with_columns(
        pl.col("ppg_ros").alias("pred_model"),
        pl.col("ppg").alias("pred_baseline_ppg"),
        pl.col("last3_ppg").alias("pred_baseline_last3"),
        pl.col(proj_col(pj.SPEC_VARIANT)).alias("pred_spec_formula"),
    ).with_columns(*tg.tag_exprs(league, pl.col("x_sell"), pl.col("x_buy")), *tg.hit_exprs(league))


# --------------------------------------------------------------------------------------
# Metrics with season-block bootstrap intervals (twm.backtest.metrics, as the Radar's C5)
# --------------------------------------------------------------------------------------

POOLED = "all"
ROW_KEYS = ("season", "week", "gsis_id")


def _subset(graded: pl.DataFrame, weeks: Sequence[int], position: str) -> pl.DataFrame:
    g = graded.filter(pl.col("evaluable") & pl.col("week").is_in(list(weeks)))
    return g if position == POOLED else g.filter(pl.col("position") == position)


def _iv(iv: Interval) -> dict[str, object]:
    return {
        "value": iv.value, "lo": iv.lo, "hi": iv.hi, "n": iv.n_groups, "n_seasons": iv.n_blocks,
        "share_above_zero": iv.share_above_zero,
    }  # fmt: skip


def _errors(sub: pl.DataFrame, method: str) -> pl.DataFrame:
    return sub.select(
        *ROW_KEYS, (pl.col(f"pred_{method}") - pl.col("ros_ppg")).abs().alias("err")
    ).sort(list(ROW_KEYS))


def _rhos(sub: pl.DataFrame, method: str) -> pl.DataFrame:
    """Spearman rank correlation of the prediction with the outcome, per as-of and position
    (one group = one weekly list of a position)."""
    return (
        sub.group_by("season", "week", "position")
        .agg(pl.corr(f"pred_{method}", "ros_ppg", method="spearman").alias("rho"), pl.len())
        .filter(pl.col("len") >= 3)
        .with_columns(pl.col("rho").fill_nan(None).round(9))
        .drop_nulls("rho")
        .sort(["season", "week", "position"])
    )


def metric_rows(
    graded: pl.DataFrame, weeks: Sequence[int], label: str, *, n_boot: int | None = None
) -> list[dict[str, object]]:
    """MAE and Spearman by position and pooled for every method, and the paired differences
    model minus each baseline (negative MAE difference = the model is closer)."""
    from twm.backtest.metrics import N_BOOT, block_bootstrap, paired_block_bootstrap

    nb = n_boot or N_BOOT
    out: list[dict[str, object]] = []
    for pos in (*pj.FANTASY_POSITIONS, POOLED):
        sub = _subset(graded, weeks, pos)
        if sub.height == 0:
            continue
        for metric, fn, keys in (("mae", _errors, ROW_KEYS), ("spearman", _rhos,
                                  ("season", "week", "position"))):  # fmt: skip
            value = "err" if metric == "mae" else "rho"
            tables = {m: fn(sub, m) for m in METHODS}
            for m, t in tables.items():
                iv = block_bootstrap(t, value=value, n_boot=nb)
                out.append({"weeks": label, "position": pos, "metric": metric, "method": m,
                            "kind": "value", **_iv(iv)})  # fmt: skip
            for base in ("baseline_ppg", "baseline_last3", "spec_formula"):
                a, b = tables["model"], tables[base]
                common = a.select(keys).join(b.select(keys), on=list(keys))
                a2 = a.join(common, on=list(keys)).sort(list(keys))
                b2 = b.join(common, on=list(keys)).sort(list(keys))
                iv = paired_block_bootstrap(a2, b2, keys=list(keys), value=value, n_boot=nb)
                d = {"weeks": label, "position": pos, "metric": metric, "method": f"model-{base}"}
                out.append({**d, "kind": "difference", **_iv(iv)})
    return out


TAG_GROUPS = {
    # tag: (base rows: the rate to beat, reference rows: the decile alone / the excluded)
    "sell_high": ("every universe player", "top-decile FPOE, any projection"),
    "buy_low": ("every universe player", "bottom-decile FPOE, any projection"),
    "legit": ("every player inside the starter threshold", "inside it with top-decile FPOE"),
}


def _tag_frames(sub: pl.DataFrame, tag: str, league: League) -> dict[str, pl.DataFrame]:
    starter = pl.col("position").replace_strict(
        league.starter_thresholds(), default=0, return_dtype=pl.Int64
    )
    inside = pl.col("ppg_rank") <= starter
    base = {"sell_high": pl.lit(True), "buy_low": pl.lit(True), "legit": inside}[tag]
    ref = {"sell_high": pl.col("fpoe_top"), "buy_low": pl.col("fpoe_bottom"),
           "legit": inside & pl.col("fpoe_top")}[tag]  # fmt: skip
    hit = pl.col(f"hit_{tag}").cast(pl.Float64).alias("hit")
    pick = {"tagged": pl.col(tag), "base": base, "reference": ref}
    return {k: sub.filter(c).select(*ROW_KEYS, hit).sort(list(ROW_KEYS)) for k, c in pick.items()}


def tag_rows(
    graded: pl.DataFrame, weeks: Sequence[int], label: str, league: League,
    *, n_boot: int | None = None,
) -> list[dict[str, object]]:  # fmt: skip
    """Hit rates of the tags in the test seasons (with season-block intervals), the base rate
    and a reference group for each, by position and pooled; ``not_graded`` counts tags on
    players with fewer than 3 remaining games."""
    from twm.backtest.metrics import N_BOOT, block_bootstrap

    nb = n_boot or N_BOOT
    wk = graded.filter(pl.col("week").is_in(list(weeks)))
    n_asofs = wk.select("season", "week").n_unique() if wk.height else 0
    out: list[dict[str, object]] = []
    for pos in (*pj.FANTASY_POSITIONS, POOLED):
        sub = _subset(graded, weeks, pos)
        allpos = wk if pos == POOLED else wk.filter(pl.col("position") == pos)
        for tag in tg.TAGS:
            frames = _tag_frames(sub, tag, league)
            not_graded = allpos.filter(pl.col(tag) & ~pl.col("evaluable")).height
            for group, fr in frames.items():
                iv = block_bootstrap(fr, value="hit", n_boot=nb)
                out.append({
                    "weeks": label, "position": pos, "tag": tag, "group": group, **_iv(iv),
                    "per_asof": round(fr.height / n_asofs, 3) if n_asofs else None,
                    "not_graded": not_graded if group == "tagged" else None,
                })  # fmt: skip
    return out


def excluded_rows(rows: pl.DataFrame, seasons: Sequence[int]) -> pl.DataFrame:
    """Universe players left out of the grading (fewer than 3 remaining games), per week and
    position, over ``seasons``."""
    r = rows.filter(pl.col("season").is_in(list(seasons)))
    return (
        r.group_by("week", "position")
        .agg(pl.len().alias("universe"), (~pl.col("evaluable")).sum().alias("left_out"))
        .sort(["week", "position"])
    )


# --------------------------------------------------------------------------------------
# The whole backtest
# --------------------------------------------------------------------------------------


@dataclass
class Backtest:
    rows: pl.DataFrame  # every universe row, seasons FIRST_VALIDATION_SEASON .. last
    graded: pl.DataFrame  # the test seasons, with predictions, tags and hits
    choices: dict[int, Choice]
    priors: list[SeasonPriors]
    metrics: list[dict[str, object]]
    tags: list[dict[str, object]]
    excluded: pl.DataFrame
    test_seasons: tuple[int, ...]
    n_boot: int


def run_backtest(
    frame: pl.DataFrame,
    asofs: pl.DataFrame,
    league: League,
    *,
    last_season: int,
    first_test_season: int = FIRST_TEST_SEASON,
    n_boot: int | None = None,
    progress: Progress | None = None,
) -> Backtest:
    """Walk forward over the test seasons ``first_test_season`` .. ``last_season``: ``frame``
    is the D1 history frame (it may hold any seasons; each projection reads only what it may),
    ``asofs`` the (season, week, as_of) table of the as-of weeks."""
    from twm.backtest.metrics import N_BOOT

    say = progress or (lambda _m: None)
    seasons = list(range(FIRST_VALIDATION_SEASON, last_season + 1))
    rows, priors = build_rows(frame, asofs, league, seasons, progress=say)
    tests = [s for s in seasons if s >= first_test_season]
    choices = {s: choose(rows, s) for s in tests}
    for s, ch in choices.items():
        say(f"test {s}: variant {ch.variant.name} (validation {ch.validation_seasons[0]}-"
            f"{ch.validation_seasons[-1]}), X sell {ch.sell.x:g} buy {ch.buy.x:g}")  # fmt: skip
    graded = graded_rows(rows, choices, league)
    say("bootstrap intervals ...")
    metrics = [
        *metric_rows(graded, HEADLINE_WEEKS, "headline", n_boot=n_boot),
        *metric_rows(graded, ALL_WEEKS, "all", n_boot=n_boot),
    ]
    tag_metrics = [
        *tag_rows(graded, HEADLINE_WEEKS, "headline", league, n_boot=n_boot),
        *tag_rows(graded, ALL_WEEKS, "all", league, n_boot=n_boot),
    ]
    return Backtest(rows, graded, choices, priors, metrics, tag_metrics,
                    excluded_rows(rows, tests), tuple(tests), n_boot or N_BOOT)  # fmt: skip


def load_inputs(
    db: Path | str,
    last_season: int,
    weeks: Sequence[int] = ALL_WEEKS,
    *,
    xfp: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """The D1 history frame of 2009 .. ``last_season`` (through the as-of view at the end of
    time; every row keeps its ``available_at``) and the as-of table. ``xfp``: another
    expected-points source (``player_week.with_xfp``); None = ffopportunity's."""
    from twm.modules.regression_watch.player_week import player_games_history

    seasons = list(range(pj.FIRST_DATA_SEASON, last_season + 1))
    return player_games_history(db, seasons, xfp=xfp), asof_table(db, seasons, weeks)


# --------------------------------------------------------------------------------------
# One week, live or historical (`twm regression project`)
# --------------------------------------------------------------------------------------


@dataclass
class WeekProjection:
    season: int
    week: int
    as_of: datetime
    choice: Choice
    table: pl.DataFrame  # the universe with ppg_ros, the tags and names


def project_week(
    db: Path | str, season: int, week: int, league: League, *, progress: Progress | None = None,
    xfp: pl.DataFrame | None = None,
) -> WeekProjection:  # fmt: skip
    """The universe at (season, week)'s official as-of, read through an AsOfView AT that
    as-of, projected with shrinkage from 2009 .. season-1 and the variant and X chosen on the
    validation seasons 2010 .. season-1 (exactly what the backtest does for a test season).
    ``xfp``: another expected-points source for every season up to ``season``
    (:func:`.player_week.with_xfp`); None = ffopportunity's."""
    from twm.asof import AsOfView, weekly_as_of
    from twm.modules.regression_watch.player_week import (
        player_games_for,
        player_games_history,
        with_xfp,
    )

    if season < FIRST_TEST_SEASON:
        raise ValueError(f"projections need two earlier seasons of 2009+ data: {season}")
    as_of = weekly_as_of(db, season, week)
    earlier = list(range(pj.FIRST_DATA_SEASON, season))
    history = player_games_history(db, earlier, xfp=xfp)
    asofs = asof_table(db, earlier, HEADLINE_WEEKS)
    val_seasons = list(range(FIRST_VALIDATION_SEASON, season))
    rows, _ = build_rows(history, asofs, league, val_seasons, progress=progress)
    choice = choose(rows, season)
    priors = pj.estimate_priors(history, earlier)
    with AsOfView(db, as_of) as view:
        std = with_xfp(player_games_for(view, season), xfp)
        names = view.sql("SELECT gsis_id, display_name AS name FROM dim_player")
    if std.height == 0:
        raise LookupError(f"no games of {season} are public at week {week}'s as-of ({as_of})")
    state = pj.player_state(std, league)
    uni = pj.project(state.filter(pl.col("in_universe")), priors, choice.variant)
    uni = tg.decile_flags(uni.with_columns(pl.lit(int(week), dtype=pl.Int32).alias("week")))
    table = (
        uni.with_columns(*tg.tag_exprs(league, choice.sell.x, choice.buy.x))
        .join(names, on="gsis_id", how="left")
        .sort(["position", "ppg_ros", "gsis_id"], descending=[False, True, False])
    )
    return WeekProjection(season, week, as_of, choice, table)
