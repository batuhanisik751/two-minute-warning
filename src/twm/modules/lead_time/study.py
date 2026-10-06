"""The study: leads of the Radar's flags over the crowd's adds, the momentum baseline, the reverse
view and the aggregate-only frames a publish may carry (docs/lead_time.md).

Rules (fixed before any number was computed; docs/lead_time.md "Definitions"):

- **lead** = the crowd's add period - the Radar's first flag period at the level (positive =
  the Radar first); **before** lead >= 1, **same** lead 0 (the same waiver period: the Tuesday
  list and the Friday scrape after that Wednesday's waivers), **after** lead <= -1, **never**
  no flag at that level in the season (split by whether he was ever in the Radar's pool);
- **nearest lead** (sensitivity): from the latest flag at or before the add, else the first;
- the momentum baseline is scored with the same code as a fourth "signal".

Per-player frames (``Study.adds`` ...) are LOCAL ONLY; :func:`build_frames` returns aggregates.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from twm.modules.lead_time import (
    COMPLETE_SEASONS,
    LEVELS,
    MIN_CELL,
    MOMENTUM_POINTS,
    STUDY_SEASONS,
    THRESHOLDS,
)
from twm.modules.lead_time import crowd as cr
from twm.modules.lead_time import flags as fl

SIGNALS = ("must_add", "spec_plus", "listed", "momentum")


def add_leads(
    adds: pl.DataFrame, weeks: pl.DataFrame, pool: pl.DataFrame | None = None
) -> pl.DataFrame:
    """``adds`` (:func:`~twm.modules.lead_time.crowd.crowd_adds`) with one signal's flags
    (``weeks``: season, gsis_id, week; every week flagged): flag_period (the first), lead,
    nearest_lead, category and in_pool (``pool``: season, gsis_id of the players on any list;
    None = everyone counts as in the pool)."""
    keys = ["season", "gsis_id"]
    w = weeks.select(*keys, pl.col("week").cast(pl.Int32))
    first = w.group_by(keys).agg(pl.col("week").min().alias("flag_period"))
    near = (
        adds.select(*keys, "add_period")
        .join(w, on=keys)
        .filter(pl.col("week") <= pl.col("add_period"))
        .group_by(keys)
        .agg(pl.col("week").max().alias("_near"))
    )
    df = adds.join(first, on=keys, how="left").join(near, on=keys, how="left")
    if pool is None:
        df = df.with_columns(pl.lit(True).alias("in_pool"))
    else:
        tag = pool.select(keys).unique().with_columns(pl.lit(True).alias("in_pool"))
        df = df.join(tag, on=keys, how="left").with_columns(pl.col("in_pool").fill_null(False))
    lead = pl.col("add_period") - pl.col("flag_period")
    near_lead = pl.col("add_period") - pl.coalesce("_near", "flag_period")
    return df.with_columns(
        lead.cast(pl.Int32).alias("lead"),
        near_lead.cast(pl.Int32).alias("nearest_lead"),
        pl.when(pl.col("flag_period").is_null()).then(pl.lit("never"))
        .when(lead >= 1).then(pl.lit("before"))
        .when(lead == 0).then(pl.lit("same"))
        .otherwise(pl.lit("after")).alias("category"),
    ).drop("_near")  # fmt: skip


CATS = ("before", "same", "after", "never")
STAT_COLUMNS = (
    *(f"n_{c}" for c in CATS),
    "n_never_out_of_pool",
    *(f"share_{c}" for c in CATS),
    "lead_median",
    "lead_q1",
    "lead_q3",
    "nearest_median",
    "share_before_4",
)


def summarize(leads: pl.DataFrame, by: Sequence[str]) -> pl.DataFrame:
    """Per group of :func:`add_leads` rows: n_adds, the counts and shares per category (and
    never-flagged players who were never in the pool), the lead's median and quartiles over
    the flagged adds, the nearest lead's median and the share flagged 1-4 periods before.
    Groups with fewer than :data:`MIN_CELL` adds keep n_adds only (statistics NULL)."""
    cat = pl.col("category")
    flagged = pl.col("lead").filter(pl.col("flag_period").is_not_null())
    near = pl.col("nearest_lead").filter(pl.col("flag_period").is_not_null())
    n = pl.len()
    out = leads.group_by(list(by)).agg(
        n.cast(pl.Int64).alias("n_adds"),
        *[(cat == c).sum().cast(pl.Int64).alias(f"n_{c}") for c in CATS],
        ((cat == "never") & ~pl.col("in_pool")).sum().cast(pl.Int64).alias("n_never_out_of_pool"),
        *[((cat == c).sum() / n).alias(f"share_{c}") for c in CATS],
        flagged.median().cast(pl.Float64).alias("lead_median"),
        flagged.quantile(0.25, "linear").cast(pl.Float64).alias("lead_q1"),
        flagged.quantile(0.75, "linear").cast(pl.Float64).alias("lead_q3"),
        near.median().cast(pl.Float64).alias("nearest_median"),
        (pl.col("lead").is_between(1, 4).sum() / n).alias("share_before_4"),
    )  # fmt: skip
    small = pl.col("n_adds") < MIN_CELL
    out = out.with_columns(
        [pl.when(small).then(None).otherwise(pl.col(c)).alias(c) for c in STAT_COLUMNS]
    )
    return out.sort(list(by)) if by else out


def state_at_flag(
    flags: pl.DataFrame, owned: pl.DataFrame, bases: pl.DataFrame, threshold: float
) -> pl.DataFrame:
    """The crowd at each flag (``flags``: season, gsis_id, flag_period, ...): ``pct_before``
    (his % at the latest scrape before the list, period <= flag_period - 1; NULL = absent, i.e.
    low), ``later_period`` (the period of his first scrape at or above ``threshold`` from the
    flag's period on, any regular-season period) and ``state``: already (>= threshold before
    the list) / added_later / never. Only flags with crowd data before them are kept
    (flag_period - 1 >= the season's baseline period)."""
    keys = ["season", "gsis_id"]
    f = flags.join(bases.select("season", "baseline_period"), on="season").filter(
        pl.col("flag_period") - 1 >= pl.col("baseline_period")
    )
    o = owned.select(*keys, "scrape_date", "period", "pct").join(
        f.select(*keys, "flag_period"), on=keys
    )
    before = (
        o.filter(pl.col("period") <= pl.col("flag_period") - 1)
        .sort(*keys, "scrape_date")
        .group_by(keys, maintain_order=True)
        .last()
        .select(*keys, pl.col("pct").alias("pct_before"))
    )
    later = (
        o.filter((pl.col("period") >= pl.col("flag_period")) & (pl.col("pct") >= threshold))
        .group_by(keys)
        .agg(pl.col("period").min().alias("later_period"))
    )
    df = f.join(before, on=keys, how="left").join(later, on=keys, how="left")
    already = pl.col("pct_before").is_not_null() & (pl.col("pct_before") >= threshold)
    return df.with_columns(
        pl.when(already).then(pl.lit("already"))
        .when(pl.col("later_period").is_not_null()).then(pl.lit("added_later"))
        .otherwise(pl.lit("never")).alias("state"),
    ).drop("baseline_period").sort(keys)  # fmt: skip


def head_to_head(radar: pl.DataFrame, momentum: pl.DataFrame) -> pl.DataFrame:
    """Per crowd add (both :func:`add_leads` frames over the same adds): which signal flagged
    him before the crowd (``h2h``: radar_only / momentum_only / both / neither) and, when both
    did, whether the Radar's flag was earlier (``radar_earlier``)."""
    keys = ["season", "gsis_id"]
    m = momentum.select(*keys, pl.col("category").alias("_mc"), pl.col("flag_period").alias("_mf"))
    df = radar.join(m, on=keys, how="left")
    rb, mb = pl.col("category") == "before", pl.col("_mc") == "before"
    return df.with_columns(
        pl.when(rb & mb).then(pl.lit("both")).when(rb).then(pl.lit("radar_only"))
        .when(mb).then(pl.lit("momentum_only")).otherwise(pl.lit("neither")).alias("h2h"),
        (rb & mb & (pl.col("flag_period") < pl.col("_mf"))).alias("radar_earlier"),
    ).drop("_mc", "_mf")  # fmt: skip


@dataclass
class Study:
    """Everything the study computed. ``leads``, ``states`` and ``h2h`` are per player and
    LOCAL ONLY (FantasyPros' roster %); ``coverage`` is per season."""

    coverage: pl.DataFrame
    leads: pl.DataFrame = field(repr=False)  # per (threshold, signal, add)
    states: pl.DataFrame = field(repr=False)  # per (threshold, signal, first flag)
    h2h: pl.DataFrame = field(repr=False)  # per (threshold, level, add): Radar vs momentum
    bases: pl.DataFrame = field(default_factory=pl.DataFrame)


def _first(weeks: pl.DataFrame) -> pl.DataFrame:
    return weeks.group_by(["season", "gsis_id"]).agg(pl.col("week").min().alias("flag_period"))


def _hits(lists: pl.DataFrame) -> pl.DataFrame:
    """The Radar's own outcome (y) of each (season, week, player) list row."""
    return lists.select("season", "week", "gsis_id", "y").unique(
        ["season", "week", "gsis_id"], keep="first", maintain_order=True
    )


def build(
    owned: pl.DataFrame,
    windows: pl.DataFrame,
    rows: pl.DataFrame,
    *,
    seasons: Sequence[int] = STUDY_SEASONS,
    thresholds: Sequence[float] = THRESHOLDS,
    points: float = MOMENTUM_POINTS,
) -> Study:
    """The whole study from its three inputs (deterministic): ``owned``
    (:func:`~twm.modules.lead_time.crowd.read_owned`), ``windows``
    (:func:`~twm.modules.lead_time.crowd.read_windows`) and the Radar's list ``rows``
    (:func:`~twm.modules.lead_time.flags.snapshot_rows`)."""
    keep = [int(s) for s in seasons]
    owned_all = cr.periods_at(owned, windows, "scrape_date")
    owned = owned_all.filter(pl.col("season").is_in(keep))
    lists = fl.tiered(rows, keep)
    weeks, lw = fl.flag_weeks(lists), fl.list_weeks(lists)
    bases = cr.baselines(owned)
    values = cr.period_values(owned, bases)
    pool = weeks.filter(pl.col("level") == "pool")
    hits = _hits(lists)
    leads, states, h2h = [], [], []
    for t in thresholds:
        adds = cr.crowd_adds(owned, bases, lw, t)
        mom = cr.momentum_flags(values, bases, lw, t, points)
        signal_weeks = {lv: weeks.filter(pl.col("level") == lv) for lv in LEVELS}
        signal_weeks["momentum"] = mom.select(
            "season", "gsis_id", pl.col("flag_period").alias("week")
        )
        tag = [pl.lit(float(t)).alias("threshold")]
        per = {}
        for sig, w in signal_weeks.items():
            per[sig] = add_leads(adds, w, pool)
            leads.append(per[sig].with_columns(*tag, pl.lit(sig).alias("signal")))
            first = _first(w).join(
                hits.rename({"week": "flag_period"}),
                on=["season", "gsis_id", "flag_period"],
                how="left",
            )
            st = state_at_flag(first, owned, bases, t)
            states.append(st.with_columns(*tag, pl.lit(sig).alias("signal")))
        for lv in LEVELS:
            h = head_to_head(per[lv], per["momentum"])
            h2h.append(h.with_columns(*tag, pl.lit(lv).alias("level")))
    cov = coverage(owned_all, lw, pl.concat(leads), keep)
    return Study(
        cov, pl.concat(leads), pl.concat(states, how="diagonal_relaxed"), pl.concat(h2h), bases
    )


def coverage(
    owned: pl.DataFrame, lw: pl.DataFrame, leads: pl.DataFrame, study: Sequence[int]
) -> pl.DataFrame:
    """Per season with any ESPN % (aggregate, publishable): the in-season scrape days (period
    >= 1), the first and last of them, the baseline, the last list week, whether the season is
    complete (baseline before week 1 and in-season scrapes) and in the study, the players with
    a roster %, and the crowd adds at each threshold."""
    bases = cr.baselines(owned)
    days = (
        owned.filter(pl.col("period") >= 1)
        .group_by("season")
        .agg(
            pl.col("scrape_date").n_unique().cast(pl.Int64).alias("in_season_days"),
            pl.col("scrape_date").min().alias("first_in_season"),
            pl.col("scrape_date").max().alias("last_in_season"),
        )
    )
    players = owned.group_by("season").agg(
        pl.col("gsis_id").n_unique().cast(pl.Int64).alias("n_players")
    )
    adds = (
        leads.filter(pl.col("signal") == LEVELS[0])
        .group_by("season")
        .agg(
            *[
                (pl.col("threshold") == t).sum().cast(pl.Int64).alias(f"adds_{int(t)}")
                for t in THRESHOLDS
            ]
        )
    )
    df = (
        bases.join(days, on="season", how="left")
        .join(players, on="season", how="left")
        .join(lw, on="season", how="left")
        .join(adds, on="season", how="left")
        .with_columns(
            pl.col("in_season_days").fill_null(0),
            pl.col("season").is_in(list(study)).alias("in_study"),
        )
    )
    full = pl.col("complete") & (pl.col("in_season_days") > 0)
    return df.with_columns(full.alias("complete")).sort("season")


def lead_summary(leads: pl.DataFrame) -> pl.DataFrame:
    """:func:`summarize` per (threshold, signal) for three scopes: ``complete`` (2021-2023
    pooled, the headline), ``season`` (each study season, 2020 partial) and ``position``
    (complete seasons, per crowd position)."""
    key = ["threshold", "signal"]
    full = leads.filter(pl.col("season").is_in(list(COMPLETE_SEASONS)))
    parts = [
        summarize(full, key).with_columns(
            pl.lit("complete").alias("scope"), pl.lit(None, dtype=pl.Int32).alias("season"),
            pl.lit(None, dtype=pl.String).alias("position"),
        ),
        summarize(leads, [*key, "season"]).with_columns(
            pl.lit("season").alias("scope"), pl.lit(None, dtype=pl.String).alias("position")
        ),
        summarize(full.rename({"pos": "position"}), [*key, "position"]).with_columns(
            pl.lit("position").alias("scope"), pl.lit(None, dtype=pl.Int32).alias("season")
        ),
    ]  # fmt: skip
    cols = ["threshold", "signal", "scope", "season", "position", "n_adds", *STAT_COLUMNS]
    out = pl.concat([p.select(cols) for p in parts], how="vertical_relaxed")
    return out.sort(
        "threshold",
        "signal",
        "scope",
        "season",
        "position",
        descending=[True, False, False, False, False],
        nulls_last=True,
    )


def lead_hist(leads: pl.DataFrame, lo: int = -6, hi: int = 10) -> pl.DataFrame:
    """The lead's distribution over the complete seasons: per (threshold, signal) the adds per
    lead (clipped to [lo, hi]; NULL = never flagged)."""
    full = leads.filter(pl.col("season").is_in(list(COMPLETE_SEASONS)))
    return (
        full.with_columns(pl.col("lead").clip(lo, hi).alias("lead"))
        .group_by("threshold", "signal", "lead")
        .agg(pl.len().cast(pl.Int64).alias("n"))
        .sort("threshold", "signal", "lead", descending=[True, False, False], nulls_last=True)
    )


def _scoped(df: pl.DataFrame, by: Sequence[str], aggs: list[pl.Expr]) -> pl.DataFrame:
    """``aggs`` per ``by`` for the complete seasons pooled (scope 'complete', season NULL) and
    per study season (scope 'season')."""
    full = df.filter(pl.col("season").is_in(list(COMPLETE_SEASONS)))
    a = (
        full.group_by(list(by))
        .agg(aggs)
        .with_columns(
            pl.lit("complete").alias("scope"), pl.lit(None, dtype=pl.Int32).alias("season")
        )
    )
    b = df.group_by([*by, "season"]).agg(aggs).with_columns(pl.lit("season").alias("scope"))
    return pl.concat([a, b.select(a.columns)], how="vertical_relaxed")


def reverse_summary(states: pl.DataFrame) -> pl.DataFrame:
    """Every signal's first flags by what the crowd did (already / added_later / never): n, the
    Radar's own hits (its label; NULL for momentum) and the median periods from flag to the
    crowd's crossing (added_later). Cells under :data:`MIN_CELL` keep n only."""
    gap = (pl.col("later_period") - pl.col("flag_period")).filter(pl.col("state") == "added_later")
    radar = pl.col("signal") != "momentum"
    aggs = [
        pl.len().cast(pl.Int64).alias("n"),
        pl.col("y").cast(pl.Int64).sum().alias("hits"),
        pl.col("y").cast(pl.Float64).mean().alias("hit_rate"),
        gap.median().cast(pl.Float64).alias("lead_median"),
    ]
    out = _scoped(states, ["threshold", "signal", "state"], aggs)
    small = pl.col("n") < MIN_CELL
    out = out.with_columns(
        pl.when(radar & ~small).then(pl.col("hits")).alias("hits"),
        pl.when(radar & ~small).then(pl.col("hit_rate")).alias("hit_rate"),
        pl.when(~small).then(pl.col("lead_median")).alias("lead_median"),
    )
    cols = [
        "threshold",
        "signal",
        "scope",
        "season",
        "state",
        "n",
        "hits",
        "hit_rate",
        "lead_median",
    ]
    return out.select(cols).sort(
        "threshold",
        "signal",
        "scope",
        "season",
        "state",
        descending=[True, False, False, False, False],
        nulls_last=True,
    )


def conversion(states: pl.DataFrame, coverage_: pl.DataFrame) -> pl.DataFrame:
    """Volume and conversion of each signal (the baseline comparison's other half): flags of
    players under the threshold at the flag (state != already), flags per list week, and the
    share the crowd added later (``share_added``: crossing in the flag's period or later;
    ``share_added_after``: strictly later)."""
    weeks = coverage_.filter(pl.col("in_study")).select(
        "season",
        (pl.col("last_list_week") - pl.col("baseline_period")).cast(pl.Int64).alias("weeks"),
    )
    s = states.filter(pl.col("state") != "already")
    added = pl.col("state") == "added_later"
    aggs = [
        pl.len().cast(pl.Int64).alias("n_flags"),
        added.sum().cast(pl.Int64).alias("n_added"),
        (added & (pl.col("later_period") > pl.col("flag_period")))
        .sum()
        .cast(pl.Int64)
        .alias("n_added_after"),
    ]
    out = _scoped(s, ["threshold", "signal"], aggs)
    full = weeks.filter(pl.col("season").is_in(list(COMPLETE_SEASONS))).get_column("weeks").sum()
    out = out.join(weeks, on="season", how="left").with_columns(
        pl.col("weeks").fill_null(int(full))
    )
    small = pl.col("n_flags") < MIN_CELL
    return out.with_columns(
        (pl.col("n_flags") / pl.col("weeks")).alias("flags_per_week"),
        pl.when(~small).then(pl.col("n_added") / pl.col("n_flags")).alias("share_added"),
        pl.when(~small)
        .then(pl.col("n_added_after") / pl.col("n_flags"))
        .alias("share_added_after"),
    ).sort(
        "threshold",
        "signal",
        "scope",
        "season",
        descending=[True, False, False, False],
        nulls_last=True,
    )


def h2h_summary(h2h: pl.DataFrame) -> pl.DataFrame:
    """Radar level vs the momentum baseline on the same crowd adds (complete seasons): per
    (threshold, level, h2h) the adds, their share and, of 'both', how often the Radar's flag
    came earlier."""
    full = h2h.filter(pl.col("season").is_in(list(COMPLETE_SEASONS)))
    out = full.group_by("threshold", "level", "h2h").agg(
        pl.len().cast(pl.Int64).alias("n"),
        pl.col("radar_earlier").sum().cast(pl.Int64).alias("n_radar_earlier"),
    )
    tot = pl.col("n").sum().over("threshold", "level")
    return out.with_columns((pl.col("n") / tot).alias("share")).sort(
        "threshold", "level", "h2h", descending=[True, False, False]
    )


# Columns that would carry a player's identity or FantasyPros' per-player roster %.
PRIVATE_COLUMNS = frozenset({
    "gsis_id", "entity_id", "player", "player_name", "espn_id", "fantasypros_id", "pct",
    "pct_before", "start_pct", "add_pct", "add_date", "scrape_date", "value", "rise",
    "percent_owned", "player_owned_espn", "player_owned_avg", "player_owned_yahoo",
})  # fmt: skip


def assert_aggregate_only(frames: dict[str, pl.DataFrame]) -> None:
    """Refuse (ValueError) any frame with a per-player column (FantasyPros' license: the public
    site may show aggregate statistics only) or with a nested column."""
    for name, df in frames.items():
        bad = sorted(set(df.columns) & PRIVATE_COLUMNS)
        nested = [c for c, t in df.schema.items() if isinstance(t, (pl.List, pl.Struct, pl.Array))]
        if bad or nested:
            raise ValueError(f"lead-time frame {name!r} is not aggregate-only: {bad + nested}")


def build_frames(study: Study) -> dict[str, pl.DataFrame]:
    """The frames a publish may carry, aggregates only (checked): coverage, summary, hist,
    reverse, conversion, h2h. Deterministic for the same inputs."""
    frames = {
        "coverage": study.coverage,
        "summary": lead_summary(study.leads),
        "hist": lead_hist(study.leads),
        "reverse": reverse_summary(study.states),
        "conversion": conversion(study.states, study.coverage),
        "h2h": h2h_summary(study.h2h),
    }
    assert_aggregate_only(frames)
    return frames


READ_SEASONS = (2020, 2021, 2022, 2023, 2024)  # 2024 is read for the coverage table only


def run(warehouse: Path | None = None) -> Study:
    """The study on the real data: the warehouse (read-only; default settings paths.warehouse)
    and the approved Radar pin's backtest snapshot. Writes nothing."""
    from twm.warehouse.build import connect

    con = connect(warehouse, read_only=True)
    try:
        owned = cr.read_owned(con, READ_SEASONS)
        windows = cr.read_windows(con, READ_SEASONS)
    finally:
        con.close()
    return build(owned, windows, fl.pinned_rows())
