"""The Decision Report Card's published rows (step P3; :data:`twm.publish.tables.DECISIONS`).

- **history** (seasons before the approved grading's season S): the frozen snapshot pinned in
  ``config/production_models.yaml`` (``decisions``; sha256 of every file checked before it is
  read, and its coach-season and league rows must reproduce the committed reports, else nothing
  is published). Never regraded here or on the runner.
- **the season in progress** (S): the grades ``twm decisions grade-pinned`` wrote with the
  approved grading (``data/decisions/season/``); refused when they were made with other models.
- ``dim_coach`` (every coach named), ``decisions_track_record`` (reports/decisions/
  wp_backtest.csv and submodels.csv row for row, and the nfl4th agreement summarized from
  nfl4th_benchmark.csv: :func:`nfl4th_rows`).

Every table's rows come from :func:`site_tables`, the same code for both parts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from twm.modules.decisions import site
from twm.publish.tables import DECISIONS, TABLES

NFL4TH_SOURCE = "nfl4th_benchmark"
NFL4TH_COLUMNS = ("season", "grade", "chosen", "recommended", "nfl4th_recommended", "agree",
                  "nfl4th_status")  # fmt: skip
CSV_SOURCES = ("wp_backtest", "submodels")


class DecisionsInputError(ValueError):
    """The decisions' local inputs are missing or inconsistent: nothing can be published."""


@dataclass
class DecisionData:
    """What a publish writes for the Decision Report Card: ``season`` = S (history = seasons
    before it), ``history`` and ``current`` = {table: rows in TABLES column order}."""

    season: int
    version: str  # the approved grading's version
    history: dict[str, pl.DataFrame]
    current: dict[str, pl.DataFrame]
    coaches: pl.DataFrame
    track_record: pl.DataFrame
    warnings: list[str] = field(default_factory=list)


def _with_ids(f: pl.DataFrame, ids: pl.DataFrame, col: str = "coach") -> pl.DataFrame:
    return f.join(ids.rename({"name": col}), on=col, how="left")


def coach_tables(frames: dict[str, pl.DataFrame]) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(coach_season, coach_week) by coach NAME from the frozen layout's frames: G3's
    aggregates (:mod:`twm.modules.decisions.coach`) with G4's clock columns per season and the
    opponent per game (from ``team_games``)."""
    from twm.modules.decisions import clock_report as cr
    from twm.modules.decisions import coach

    f, t, games = frames["fourth_downs"], frames["two_point"], frames["team_games"]
    m1, m2, m3 = site.clock_frames(frames["clock_cases"])
    clock = cr.coach_season(games, m1, m2, m3).drop("team", "games")
    seasons = coach.coach_season(f, t).join(clock, on=["season", "coach"], how="left")
    nums = [c for c in clock.columns if c.startswith("m")]
    seasons = seasons.with_columns(pl.col(c).fill_null(0) for c in nums)
    opp = games.join(games.select("game_id", pl.col("team").alias("opp")), on="game_id") \
        .filter(pl.col("team") != pl.col("opp")).select("game_id", "team", "opp")  # fmt: skip
    weeks = coach.coach_week(f, t).join(
        games.select("game_id", "team", "season_type"), on=["game_id", "team"], how="left"
    ).join(opp, on=["game_id", "team"], how="left")  # fmt: skip
    return seasons, weeks


def site_tables(frames: dict[str, pl.DataFrame], ids: pl.DataFrame) -> dict[str, pl.DataFrame]:
    """The five split tables (:data:`DECISIONS`.tables) of one part, in TABLES column order:
    graded decisions only (clear and toss-up), coaches by ``coach_id``."""
    graded = pl.col("exclusion").is_null()
    seasons, weeks = coach_tables(frames)
    clock = frames["clock_cases"]  # detail: JSON text (written as jsonb)
    out = {
        "decision_fourth": _with_ids(frames["fourth_downs"].filter(graded), ids),
        "decision_two_point": _with_ids(frames["two_point"].filter(graded), ids),
        "decision_clock": _with_ids(clock, ids),
        "coach_season": _with_ids(seasons, ids),
        "coach_week": _with_ids(weeks, ids),
    }
    return {n: typed(out[n], n) for n in DECISIONS.tables}


def typed(df: pl.DataFrame, name: str) -> pl.DataFrame:
    """``df`` in table ``name``'s column order, integers as Int64 and doubles as Float64 (a
    count summed from a float column, e.g. timeouts left, is still a count)."""
    cast = {"integer": pl.Int64, "double precision": pl.Float64, "boolean": pl.Boolean}
    return df.select(pl.col(c).cast(cast[t]) if t in cast else pl.col(c)
                     for c, t in TABLES[name].columns)  # fmt: skip


# --------------------------------------------------------------------------------------
# The track record
# --------------------------------------------------------------------------------------


def csv_rows(path: Path, source: str) -> pl.DataFrame:
    """Every row of a decisions report CSV (wp_backtest.csv, submodels.csv) as published."""
    if not path.exists():
        raise DecisionsInputError(f"report not found: {path}")
    df = pl.read_csv(path, infer_schema_length=0)

    def num(c: str, dtype: pl.DataType) -> pl.Expr:
        return pl.col(c).cast(pl.Float64).cast(dtype)

    return df.select(
        pl.lit(source).alias("source"), pl.int_range(1, pl.len() + 1, dtype=pl.Int32).alias("line"),
        pl.col("table").alias("section"), "scope", "subset", "method", "metric",
        num("value", pl.Float64), num("lo", pl.Float64), num("hi", pl.Float64),
        num("n_plays", pl.Int64).alias("n"), num("n_blocks", pl.Int64).alias("n_blocks"),
    )  # fmt: skip


def nfl4th_rows(path: Path) -> tuple[pl.DataFrame, list[str]]:
    """THE HOOK for G5's nfl4th benchmark (reports/decisions/nfl4th_benchmark.csv, one row per
    benchmarked fourth down): (rows, warnings). Published only when the file exists, has
    :data:`NFL4TH_COLUMNS` and every row comes from a full nfl4th run (``nfl4th_status`` =
    'full'); otherwise no rows and a warning, so the publish goes on and the numbers appear on
    the first publish after the benchmark is final. Rows: section 'agreement' (scope = our
    grade 'all' / 'clear' / 'toss_up', subset = season or 'all', value = share of the same
    recommended option, n = rows) and 'go_rate' (method 'ours' / 'nfl4th' = recommended,
    'real' = chosen)."""
    empty = pl.DataFrame(schema={n: _dtype(t) for n, t in TABLES["decisions_track_record"]
                                 .columns})  # fmt: skip
    if not path.exists():
        return empty, [f"nfl4th benchmark not published: {path.name} not found"]
    head = pl.read_csv(path, n_rows=0).columns
    missing = [c for c in NFL4TH_COLUMNS if c not in head]
    if missing:
        return empty, [f"nfl4th benchmark not published: {path.name} lacks {missing}"]
    f = pl.read_csv(path, columns=list(NFL4TH_COLUMNS), infer_schema_length=0)
    if f.height == 0 or (f["nfl4th_status"] != "full").any():
        return empty, [f"nfl4th benchmark not published: {path.name} is not a full run yet"]
    f = f.with_columns(pl.col("agree").str.to_lowercase() == "true")
    rows = []
    subsets = [*sorted(f["season"].unique().to_list()), "all"]
    for grade in ("all", "clear", "toss_up"):
        for sub in subsets:
            g = f if grade == "all" else f.filter(pl.col("grade") == grade)
            g = g if sub == "all" else g.filter(pl.col("season") == sub)
            rows.append(("agreement", grade, sub, "nfl4th", "agree_rate",
                         g["agree"].mean() if g.height else None, g.height))  # fmt: skip
    for sub in subsets:
        g = f if sub == "all" else f.filter(pl.col("season") == sub)
        for method, col in (("ours", "recommended"), ("nfl4th", "nfl4th_recommended"),
                            ("real", "chosen")):  # fmt: skip
            rows.append(("go_rate", "all", sub, method, "go_rate",
                         (g[col] == "go").mean() if g.height else None, g.height))  # fmt: skip
    out = pl.DataFrame(
        [(NFL4TH_SOURCE, i, *r[:5], None if r[5] is None else float(r[5]), None, None, r[6],
          None) for i, r in enumerate(rows, 1)],
        schema=empty.schema, orient="row")  # fmt: skip
    return out, []


def _dtype(pg: str) -> pl.DataType:
    return {"integer": pl.Int64, "double precision": pl.Float64}.get(pg, pl.String)


def track_record(reports_dir: Path) -> tuple[pl.DataFrame, list[str]]:
    """decisions_track_record's rows and the nfl4th hook's warnings."""
    parts = [csv_rows(reports_dir / f"{s}.csv", s) for s in CSV_SOURCES]
    nfl, warnings = nfl4th_rows(reports_dir / f"{NFL4TH_SOURCE}.csv")
    schema = parts[0].schema
    rows = pl.concat([*(p.cast(dict(schema)) for p in parts), nfl.cast(dict(schema))])
    return rows.select(TABLES["decisions_track_record"].names), warnings


# --------------------------------------------------------------------------------------
# Collect and check
# --------------------------------------------------------------------------------------


def current_frames(season: int, spec: object, season_dir: Path) -> dict[str, pl.DataFrame]:
    """The season in progress in the frozen layout, from ``twm decisions grade-pinned``'s
    files; refused when they are missing or were graded with other models or inputs."""
    from twm.modules.decisions import frozen as fz

    info_file = season_dir / "graded" / f"season_{season}.json"
    if not info_file.exists():
        raise DecisionsInputError(
            f"the {season} season is not graded with the approved grading ({info_file} is "
            "missing): run `uv run twm decisions grade-pinned` first")  # fmt: skip
    info = json.loads(info_file.read_text())
    want = {**spec.versions(), **{k: spec.inputs[k] for k in ("fg_max_distance",
                                                               "punt_min_yardline")}}  # fmt: skip
    odd = [k for k, v in want.items() if info.get(k) != v]
    if odd:
        raise DecisionsInputError(
            f"the {season} grades in {season_dir} were not made with the approved grading "
            f"{spec.model_version} ({', '.join(odd)} differ): run `uv run twm decisions "
            "grade-pinned` again")  # fmt: skip
    try:
        return fz.season_tables(season, graded_dir=season_dir / "graded",
                                clock_dir=season_dir / "clock")  # fmt: skip
    except fz.FrozenError as e:
        raise DecisionsInputError(str(e)) from e


def collect_decisions(season: int, *, season_dir: Path | None = None,
                      reports_dir: Path | None = None, pin_path: Path | None = None,
                      root: Path | None = None) -> DecisionData:  # fmt: skip
    """Everything a publish writes for the Decision Report Card (module docstring)."""
    from twm import pins
    from twm.config import ROOT
    from twm.modules.decisions import frozen as fz
    from twm.modules.decisions import production as dp
    from twm.modules.decisions import season as sn

    reports = reports_dir if reports_dir is not None else ROOT / "reports" / "decisions"
    try:
        spec, pin = dp.load_pinned_spec(season, path=pin_path, root=root)
        history = fz.load_snapshot(pin, root)
    except pins.PinError as e:
        raise DecisionsInputError(f"the approved decisions grading cannot be used: {e}") from e
    problems = fz.snapshot_mismatches(history, {"fourth_downs": reports / "fourth_downs.csv",
                                                "clock": reports / "clock.csv"})  # fmt: skip
    if problems:
        raise DecisionsInputError(
            f"the frozen decision history disagrees with the reports in {len(problems)} places "
            f"(e.g. {problems[0]})")  # fmt: skip
    current = current_frames(season, spec, season_dir if season_dir is not None
                             else sn.season_dir())  # fmt: skip
    names = [*history["team_games"]["coach"], *current["team_games"]["coach"]]
    for part in (history, current):
        for t in ("fourth_downs", "two_point", "clock_cases"):
            names += part[t]["coach"].to_list()
    try:
        ids = site.coach_ids(names)
    except site.SiteError as e:
        raise DecisionsInputError(str(e)) from e
    tr, warnings = track_record(reports)
    return DecisionData(season=int(season), version=spec.model_version,
                        history=site_tables(history, ids), current=site_tables(current, ids),
                        coaches=ids, track_record=tr, warnings=warnings)  # fmt: skip


def problems(d: DecisionData) -> list[str]:
    """Plain-English problems of the decision rows (empty = publishable)."""
    out = []
    known = set(d.coaches["coach_id"].to_list())
    for name in DECISIONS.tables:
        h, c = d.history[name], d.current[name]
        if h.filter(pl.col("season") >= d.season).height:
            out.append(f"{name}: the frozen history holds season {d.season} or later")
        if c.filter(pl.col("season") != d.season).height:
            out.append(f"{name}: the season in progress holds other seasons")
        both = pl.concat([h, c], how="vertical_relaxed")
        if both.select(TABLES[name].key).is_duplicated().any():
            out.append(f"{name} repeats a {', '.join(TABLES[name].key)}")
        if both.filter(pl.col("coach_id").is_null() | ~pl.col("coach_id").is_in(known)).height:
            out.append(f"{name}: some rows name no known coach")
        nan = [x for x, t in both.schema.items() if t == pl.Float64 and both[x].is_nan().any()]
        if nan:
            out.append(f"{name} has NaN values in {nan}")
    for name, cols in (("decision_fourth", ("wp_go", "wp_fg", "wp_punt", "wp_lost", "p_convert",
                                            "p_make")),
                       ("decision_two_point", ("wp_kick", "wp_two_point", "wp_lost"))):  # fmt: skip
        both = pl.concat([d.history[name], d.current[name]], how="vertical_relaxed")
        bad = [x for x in cols if both.filter((pl.col(x) < 0) | (pl.col(x) > 1)).height]
        if bad:
            out.append(f"{name}: {bad} outside 0-1")
    return out
