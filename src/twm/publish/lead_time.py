"""Lead time vs the crowd as ``twm publish`` writes it (feature #8; docs/lead_time.md). Reads
only.

Source: ONLY :func:`twm.modules.lead_time.study.build_frames` of
:func:`twm.modules.lead_time.study.run` (the warehouse read-only: ESPN's roster % as FantasyPros
scraped it, ``fact_ranking`` 2020-2024, and ``dim_week``; the approved Radar pin's backtest
snapshot). FantasyPros' license: no per-player value may leave the machine, so only those
aggregate frames are published, checked by
:func:`~twm.modules.lead_time.study.assert_aggregate_only` on the study's frames and again on the
site tables (:func:`site_tables`). Six **replace** tables (:data:`twm.publish.tables.LEAD_TIME`),
each its own hash unit, guarded like the others:

- the frames' nullable ``season`` / ``position`` scope columns become ``scope_value`` (text:
  '' for the complete seasons pooled, else the season or the position): every key is NOT NULL;
- ``threshold`` (50.0 / 25.0) becomes an integer; the histogram's never-flagged bin (lead
  NULL) is left out: it is the summary's ``n_never`` of the same (threshold, signal);
- NaN becomes NULL; integer columns are Int32.

Never imports :mod:`twm.modules.lead_time.live` (it reads league data; tests/test_league.py
scans the publish package).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import polars as pl

from twm.publish.collect import FORBIDDEN_COLUMNS, PublishInputError
from twm.publish.tables import LEAD_TIME, TABLES

MODULE = "lead_time"
COVERAGE, SUMMARY, HIST, REVERSE, CONVERSION, H2H = LEAD_TIME
FRAMES = {"coverage": COVERAGE, "summary": SUMMARY, "hist": HIST, "reverse": REVERSE,
          "conversion": CONVERSION, "h2h": H2H}  # fmt: skip
SCOPED = (SUMMARY, REVERSE, CONVERSION)  # the tables with scope / scope_value
THRESHOLDS = (25, 50)
SIGNALS = ("must_add", "spec_plus", "listed", "momentum")
LEVELS = ("must_add", "spec_plus", "listed")
SCOPES = ("complete", "season", "position")
STATES = ("already", "added_later", "never")
H2H_KINDS = ("radar_only", "momentum_only", "both", "neither")


@dataclass
class LeadTimeData:
    """``tables``: the six site tables in tables.TABLES column order (aggregates only)."""

    tables: dict[str, pl.DataFrame]


def typed(df: pl.DataFrame, name: str) -> pl.DataFrame:
    """``df`` in the table's column order: integers Int32, NaN -> NULL in the float columns."""
    cols = []
    for c, pg in TABLES[name].columns:
        if pg == "integer":
            cols.append(pl.col(c).cast(pl.Int32))
        elif pg == "double precision":
            cols.append(pl.col(c).cast(pl.Float64).fill_nan(None))
        else:
            cols.append(pl.col(c))
    return df.select(cols)


def with_scope_value(df: pl.DataFrame) -> pl.DataFrame:
    """``df`` with ``scope_value``: '' for the pooled complete seasons, else the season (scope
    'season') or the position (scope 'position'; only the summary has that scope)."""
    pos = pl.col("position") if "position" in df.columns else pl.lit(None, dtype=pl.String)
    value = (
        pl.when(pl.col("scope") == "season").then(pl.col("season").cast(pl.String))
        .when(pl.col("scope") == "position").then(pos)
        .otherwise(pl.lit(""))
    )  # fmt: skip
    return df.with_columns(value.fill_null("").alias("scope_value"))


def _no_private(tables: dict[str, pl.DataFrame]) -> None:
    """The study's own guard, plus the publish's list of other systems' ids and league data."""
    from twm.modules.lead_time.study import assert_aggregate_only

    assert_aggregate_only(tables)
    for name, df in tables.items():
        bad = sorted(set(df.columns) & FORBIDDEN_COLUMNS)
        if bad:
            raise ValueError(f"lead-time table {name!r} has forbidden columns: {bad}")


def site_tables(frames: dict[str, pl.DataFrame]) -> dict[str, pl.DataFrame]:
    """The six published tables from :func:`~twm.modules.lead_time.study.build_frames`'s
    frames (checked aggregate-only before and after; anything else is refused)."""
    if set(frames) != set(FRAMES):
        raise ValueError(f"lead-time frames must be exactly {sorted(FRAMES)}: {sorted(frames)}")
    _no_private(frames)
    out: dict[str, pl.DataFrame] = {}
    for key, name in FRAMES.items():
        df = frames[key]
        if "threshold" in df.columns:
            df = df.with_columns(pl.col("threshold").round(0).cast(pl.Int32))
        if name in SCOPED:
            df = with_scope_value(df)
        if name == HIST:
            df = df.filter(pl.col("lead").is_not_null())
        out[name] = typed(df, name).sort(list(TABLES[name].key))
    _no_private(out)
    return out


def collect_lead_time(warehouse: Path) -> LeadTimeData:
    """Everything the module publishes: the study on ``warehouse`` (read-only) and the pinned
    Radar backtest, as aggregates (module docstring). About 2 s."""
    from twm.modules.lead_time import study as st

    if not Path(warehouse).exists():
        raise PublishInputError(f"warehouse not found: {warehouse}")
    try:
        frames = st.build_frames(st.run(Path(warehouse)))
    except FileNotFoundError as e:
        raise PublishInputError(str(e)) from e
    return LeadTimeData(tables=site_tables(frames))


def _scope_problems(name: str, df: pl.DataFrame) -> list[str]:
    s, v = pl.col("scope"), pl.col("scope_value")
    ok = (
        ((s == "complete") & (v == ""))
        | ((s == "season") & v.str.contains(r"^\d{4}$"))
        | ((s == "position") & v.is_in(["QB", "RB", "WR", "TE"]) & pl.lit(name == SUMMARY))
    )
    return [f"{name} has an unknown scope or scope value"] if df.filter(~ok).height else []


def _share_problems(name: str, df: pl.DataFrame) -> list[str]:
    cols = [c for c in df.columns if c.startswith("share") or c == "hit_rate"]
    if not cols:
        return []
    bad = df.filter(pl.any_horizontal([(pl.col(c) < 0) | (pl.col(c) > 1) for c in cols]))
    return [f"{name} has shares outside 0-1"] if bad.height else []


def problems(d: LeadTimeData) -> list[str]:
    """Plain-English problems of the module's rows (empty = publishable)."""
    out: list[str] = []
    t = d.tables
    try:
        _no_private(t)
    except ValueError as e:
        out.append(str(e))
    for name in LEAD_TIME:
        df = t[name]
        if df.select(TABLES[name].key).is_duplicated().any():
            out.append(f"{name} repeats a key {TABLES[name].key}")
        if "threshold" in df.columns and df.filter(~pl.col("threshold").is_in(THRESHOLDS)).height:
            out.append(f"{name} has an unknown threshold")
        if "signal" in df.columns and df.filter(~pl.col("signal").is_in(SIGNALS)).height:
            out.append(f"{name} names an unknown signal")
        if name in SCOPED:
            out += _scope_problems(name, df)
        out += _share_problems(name, df)
    s = t[SUMMARY]
    parts = pl.sum_horizontal("n_before", "n_same", "n_after", "n_never")
    if s.filter(pl.col("n_before").is_not_null() & (parts != pl.col("n_adds"))).height:
        out.append("lead_time_summary's before / same / after / never do not add up to n_adds")
    full = s.filter(pl.col("scope") == "complete").select("threshold", "signal", "n_adds",
                                                           "n_never")  # fmt: skip
    hist = t[HIST].group_by("threshold", "signal").agg(pl.col("n").sum().alias("flagged"))
    both = full.join(hist, on=["threshold", "signal"], how="left").with_columns(
        pl.col("flagged").fill_null(0))  # fmt: skip
    off = pl.col("flagged") + pl.col("n_never") != pl.col("n_adds")
    if both.filter(pl.col("n_never").is_not_null() & off).height:
        out.append("lead_time_hist does not match the summary's flagged adds")
    if t[REVERSE].filter(~pl.col("state").is_in(STATES)).height:
        out.append("lead_time_reverse names an unknown state")
    h = t[H2H]
    if h.filter(~pl.col("level").is_in(LEVELS) | ~pl.col("h2h").is_in(H2H_KINDS)).height:
        out.append("lead_time_h2h names an unknown level or comparison")
    cov = t[COVERAGE]
    if cov.filter(pl.col("complete") & (pl.col("in_season_days") < 1)).height:
        out.append("lead_time_coverage calls a season without in-season data complete")
    return out
