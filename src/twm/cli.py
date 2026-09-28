"""`twm` command-line interface. Subcommands are added step by step."""

from __future__ import annotations

from pathlib import Path

import typer

from twm import __version__

app = typer.Typer(help="Two-Minute Warning pipeline.", no_args_is_help=True)


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@app.command()
def doctor() -> None:
    """Check that this checkout is ready: config, env, data, models (exit 1 on a FAIL).

    Checks: config files load and validate (with the derived league shape), .env (key names
    only, never values), the virtualenv's hidden-file problem, the data symlinks, nflreadpy vs
    the schema snapshots, the raw cache's seasons and freshness, the warehouse (built after the
    cache changed?), player-id coverage and the production model. One line per check: PASS,
    WARN (works, but something is missing or stale; the line says what to run) or FAIL
    (broken)."""
    from twm import doctor as dr

    checks = dr.run_checks()
    for c in checks:
        for line in c.lines():
            typer.echo(line)
    typer.echo(dr.summary(checks))
    if any(c.status == "FAIL" for c in checks):
        raise typer.Exit(code=1)


@app.command()
def ingest(
    datasets: list[str] | None = typer.Argument(None, help="Dataset names (default: all)."),
    start: int | None = typer.Option(
        None, help="First season (default and minimum: each dataset's first season)."
    ),
    end: int | None = typer.Option(None, help="Last season (default: current season)."),
    force: bool = typer.Option(False, help="Re-download even if cached."),
) -> None:
    """Download nflverse datasets into the local Parquet cache (data/raw).

    Every dataset and season is attempted even if one fails. The run ends with a summary of
    failures and exits with code 1 if anything failed, so a scheduled job notices.
    """
    from twm.config import settings
    from twm.sources import nflverse as nv

    names = datasets or list(nv.DATASETS)
    unknown = [n for n in names if n not in nv.DATASETS]
    if unknown:
        raise typer.BadParameter(f"unknown datasets: {unknown}; known: {list(nv.DATASETS)}")
    current = settings().current_season
    last = end if end is not None else current
    if start is not None and start > last:
        raise typer.BadParameter(f"--start {start} is after --end {last}")

    failures: list[tuple[str, str, str]] = []

    def run(name: str, season: int | None) -> None:
        label = str(season) if season is not None else "all"
        try:
            df = nv.fetch(name, season, force=force)
        except Exception as e:  # report every failure at the end; the exit code says it failed
            failures.append((name, label, f"{type(e).__name__}: {e}"))
            typer.echo(f"{name:22s} {label:<6} FAILED (details at the end)")
            return
        typer.echo(f"{name:22s} {label:<6} {df.height:>9,} rows  {df.width:>4} cols")

    for name in names:
        ds = nv.DATASETS[name]
        if not ds.per_season:
            run(name, None)
            continue
        first = ds.first_season or settings().seasons["pbp_start"]
        if start is not None and start < first:
            typer.echo(f"{name:22s} starts in {first}; skipping {start}-{first - 1}")
        ds_first = max(first, start) if start is not None else first
        ds_last = last
        if name in nv.POST_SEASON_ONLY and ds_last >= current:
            ds_last = current - 1
            typer.echo(f"{name:22s} {current} is published only after the season; skipped")
        for season in range(ds_first, ds_last + 1):
            run(name, season)

    if failures:
        typer.echo(f"\n{len(failures)} failed:", err=True)
        for name, label, msg in failures:
            typer.echo(f"  {name} {label}: {msg}", err=True)
        drift = [f for f in failures if f[2].startswith("SchemaDriftError")]
        if drift:
            typer.echo(
                "Schema drift means upstream removed columns we rely on; read the message above "
                "before refreshing snapshots.",
                err=True,
            )
        raise typer.Exit(code=1)


@app.command()
def snapshot(
    dataset: str = typer.Argument(
        ..., help="Dataset name (see `twm ingest --help`), or depth_charts_legacy."
    ),
    season: int | None = typer.Option(
        None,
        "--season",
        help="Season to describe (per-season datasets; default: the current season, or the "
        "latest cached one with a warning).",
    ),
    download: bool = typer.Option(
        False,
        "--download",
        help="Re-download that season from nflverse first (network), with the drift check off.",
    ),
) -> None:
    """Rewrite one schema snapshot (data/schemas/<dataset>.json) deliberately.

    By default it reads the local Parquet cache and never downloads. --download re-fetches the
    season from nflverse with the drift check switched off: the deliberate way to accept an
    upstream schema change after `twm ingest` stopped with a SchemaDriftError. Review
    `git diff data/schemas` before committing. `uv run python scripts/refresh_snapshots.py`
    rewrites all of them at once."""
    from twm.sources import nflverse as nv

    known = [*nv.DATASETS, nv.LEGACY_SNAPSHOT]
    if dataset not in known:
        raise typer.BadParameter(f"unknown dataset {dataset!r}; known: {known}")
    try:
        target = nv.snapshot_target(dataset, season)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    if target.warning:
        typer.echo(f"WARNING {target.warning}", err=True)
    try:
        path = nv.refresh_snapshot(
            target.dataset, target.season, snapshot_name=target.snapshot_name, download=download
        )
    except FileNotFoundError as e:
        typer.echo(f"not written: {e}", err=True)
        raise typer.Exit(code=1) from e
    snap = nv.load_snapshot(target.snapshot_name) or {}
    typer.echo(
        f"{target.snapshot_name}: season {target.season or 'all'}, "
        f"{snap.get('n_rows_sample', 0):,} rows, {len(snap.get('columns', {}))} columns "
        f"({'downloaded' if download else 'from the cache'}) -> {nv.project_relative(path)}"
    )


@app.command()
def build(
    seasons: list[int] | None = typer.Argument(
        None, metavar="[SEASON]...", help="Seasons to build, e.g. `twm build 2025 2026`."
    ),
    season_opts: list[int] | None = typer.Option(
        None,
        "--seasons",
        "-s",
        help="Same as the positional seasons (repeatable: -s 2025 -s 2026).",
    ),
    start: int | None = typer.Option(None, help="First season of a range (alternative to SEASON)."),
    end: int | None = typer.Option(None, help="Last season of the range (default: current)."),
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
) -> None:
    """Build the DuckDB warehouse from the local Parquet cache (never downloads).

    Exit codes: 0 built; 1 the build failed and was rolled back (the previous warehouse is
    untouched) or another build holds the lock; 2 bad arguments; 3 cache files missing (run
    `twm ingest`).
    """
    import duckdb

    from twm.config import settings
    from twm.sources import nflverse as nv
    from twm.warehouse import build as wb

    # Typer options take one value each, so `--seasons 2025 2026` arrives as season_opts=[2025]
    # plus the positional 2026; both spellings are merged.
    chosen = sorted({*(seasons or []), *(season_opts or [])})
    if chosen and (start is not None or end is not None):
        raise typer.BadParameter(
            "SEASON... (or --seasons) and --start/--end are mutually exclusive"
        )
    if not chosen and start is not None:
        chosen = list(range(start, (end or settings().current_season) + 1))
    if not chosen:
        raise typer.BadParameter(
            "give seasons (`twm build 2025 2026`) or --start 1999 [--end 2026]"
        )
    try:
        manifest = wb.build_warehouse(chosen, db_path=db)
    except wb.MissingCacheError as e:
        typer.echo(f"nothing built: {e}", err=True)
        raise typer.Exit(code=3) from e
    except wb.BuildInProgressError as e:
        typer.echo(f"nothing built: {e}", err=True)
        raise typer.Exit(code=1) from e
    except (wb.PrimaryKeyError, RuntimeError, ValueError, duckdb.Error, OSError) as e:
        # OSError: an unwritable target directory fails at the lock file, before connect()
        typer.echo(
            f"build failed and was rolled back; the previous warehouse is untouched.\n{e}",
            err=True,
        )
        raise typer.Exit(code=1) from e
    target = nv.project_relative(db if db is not None else settings().path("warehouse"))
    typer.echo(f"built {len(manifest)} tables for seasons {chosen} -> {target}")
    typer.echo(f"{'table':22s} {'rows':>10s}  {'hash':12s}  {'null-key':>8s} {'dupes':>6s}  notes")
    for m in manifest:
        typer.echo(
            f"{m['table_name']:22s} {m['n_rows']:>10,}  {m['content_hash'][:12]:12s}  "
            f"{m['n_dropped_null_key']:>8,} {m['n_dropped_duplicates']:>6,}  {m['notes']}"
        )


@app.command()
def asof(
    season: int = typer.Argument(..., help="Season, e.g. 2025."),
    week: int = typer.Argument(..., help="Week number (regular season or playoffs)."),
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
) -> None:
    """Show what the warehouse looked like at a week's official Tuesday as-of.

    Prints the as-of moment and, per event table, how many of that season's rows were already
    available then (a learning aid for the point-in-time rules in docs/warehouse.md).
    """
    import duckdb

    from twm.asof import AsOfView, WarehouseTooOldError, weekly_as_of
    from twm.config import settings
    from twm.warehouse import available as av

    path = db if db is not None else settings().path("warehouse")
    if not Path(path).exists():
        typer.echo(f"warehouse not found: {path}; run `twm build` first", err=True)
        raise typer.Exit(code=1)
    try:
        when = weekly_as_of(path, season, week, season_type=None)
        with AsOfView(path, when) as view:
            typer.echo(f"as-of for {season} week {week}: {when:%Y-%m-%d %H:%M} UTC ({when:%A})")
            typer.echo(f"{'table':24s} {'visible':>10s} {'of':>10s}  (rows of season {season})")
            for name in av.event_tables():
                if name not in view.tables:
                    continue
                cols = set(view.sql(f"SELECT * FROM {name} LIMIT 0").columns)
                where = f"WHERE season = {int(season)}" if "season" in cols else ""
                seen = view.sql(f"SELECT count(*) AS n FROM {name} {where}").item()
                total = view.sql(f"SELECT count(*) AS n FROM wh.{name} {where}").item()
                label = name if where else f"{name} (all seasons)"
                typer.echo(f"{label:24s} {seen:>10,} {total:>10,}")
    except (LookupError, WarehouseTooOldError, duckdb.Error) as e:
        typer.echo(f"cannot show the as-of: {e}", err=True)
        raise typer.Exit(code=1) from e


@app.command("ids")
def ids_report(
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
    out: Path = typer.Option(
        Path("reports/ids/unmatched_ids.md"),
        "--out",
        help="Markdown file to write (relative paths are under the project root).",
    ),
) -> None:
    """Write the unmatched-ID report (markdown) from the warehouse and print a summary.

    Reads the tables every build refreshes (bridge_player_id, report_id_coverage,
    report_id_unmatched; docs/warehouse.md "Player IDs"). The output is deterministic (sorted;
    the only time in it is the build's), so it can be committed and diffed.
    """
    import duckdb

    from twm import ids
    from twm.config import ROOT, settings
    from twm.warehouse import build as wb

    path = db if db is not None else settings().path("warehouse")
    if not Path(path).exists():
        typer.echo(f"warehouse not found: {path}; run `twm build` first", err=True)
        raise typer.Exit(code=1)
    try:
        con = wb.connect(path, read_only=True)
    except duckdb.Error as e:
        typer.echo(f"cannot open the warehouse: {e}", err=True)
        raise typer.Exit(code=1) from e
    try:
        if not ids.has_report(con):
            typer.echo(
                f"{path} has no ID report tables (built before B3); rebuild it with `twm build`",
                err=True,
            )
            raise typer.Exit(code=1)
        text = ids.render_markdown(con)
        lines = ids.summary_lines(con)
    finally:
        con.close()
    target = out if out.is_absolute() else ROOT / out
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    for line in lines:
        typer.echo(line)
    typer.echo(f"wrote {target}")


radar_app = typer.Typer(
    help="Waiver Radar: the candidate pool (players probably on waivers), its labels, its "
    "features, the training dataset, the backtest and its evaluation, and the weekly list "
    "(`twm radar score`, `twm radar week`)."
)
app.add_typer(radar_app, name="radar")


def _warehouse_or_exit(db: Path | None) -> Path:
    from twm.config import settings

    path = Path(db) if db is not None else settings().path("warehouse")
    if not path.exists():
        typer.echo(f"warehouse not found: {path}; run `twm build` first", err=True)
        raise typer.Exit(code=1)
    return path


@radar_app.command("pool")
def radar_pool(
    season: int = typer.Argument(..., help="Season, e.g. 2023."),
    week: int = typer.Argument(..., help="Regular-season week; the pool at its Tuesday as-of."),
    pos: str | None = typer.Option(None, "--pos", help="Only this position (QB, RB, WR, TE)."),
    method: str = typer.Option(
        "auto", "--method", help="auto, ecr (preseason cheat sheet) or prior_ppg (fallback)."
    ),
    show_all: bool = typer.Option(
        False, "--all", help="Also list rostered players kept out of the pool, with the reason."
    ),
    limit: int = typer.Option(30, "--limit", help="Rows to print (0 = all)."),
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
) -> None:
    """Print the candidate pool at a week's Tuesday as-of, best points per game first."""
    import duckdb
    import polars as pl

    from twm.asof import AsOfView, WarehouseTooOldError, weekly_as_of
    from twm.modules.waiver_radar.pool import candidate_pool

    path = _warehouse_or_exit(db)
    if method not in ("auto", "ecr", "prior_ppg"):
        raise typer.BadParameter("--method must be auto, ecr or prior_ppg")
    try:
        when = weekly_as_of(path, season, week)
        with AsOfView(path, when) as view:
            df = candidate_pool(view, season, week, method=method)  # type: ignore[arg-type]
    except (LookupError, ValueError, WarehouseTooOldError, duckdb.Error) as e:
        typer.echo(f"cannot compute the pool: {e}", err=True)
        raise typer.Exit(code=1) from e
    if pos is not None:
        df = df.filter(pl.col("position") == pos.upper())
    universe = df.height
    if not show_all:
        df = df.filter(pl.col("in_pool"))
    df = df.sort(["ppg_to_date", "gsis_id"], descending=[True, False], nulls_last=True)
    used = df.get_column("method").unique().to_list() if df.height else [method]
    typer.echo(
        f"{season} week {week}, as-of {when:%Y-%m-%d %H:%M} UTC, method {', '.join(used)}: "
        f"{int(df.get_column('in_pool').sum()) if df.height else 0} in the pool of "
        f"{universe} rostered players" + (f" at {pos.upper()}" if pos else "")
    )
    cols = ["name", "team", "position", "status", "games_to_date", "ppg_to_date",
            "ppg_pos_rank", "preseason_source", "preseason_pos_rank", "in_pool", "excluded_by",
            "owned_avg"]  # fmt: skip
    shown = df.select(cols) if limit == 0 else df.select(cols).head(limit)
    with pl.Config(tbl_rows=-1, tbl_cols=-1, tbl_width_chars=200, float_precision=1,
                   tbl_hide_dataframe_shape=True, tbl_hide_column_data_types=True):  # fmt: skip
        typer.echo(str(shown))


@radar_app.command("pool-report")
def radar_pool_report(
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
    out: Path = typer.Option(
        Path("reports/waiver_radar/pool_sizes.md"),
        "--out",
        help="Markdown file to write (relative paths are under the project root); a CSV with "
        "the same name is written next to it.",
    ),
    start: int = typer.Option(2013, "--start", help="First season (snap counts start 2013)."),
    end: int | None = typer.Option(None, "--end", help="Last season (default: current)."),
) -> None:
    """Write the pool-size report and its validation (markdown + CSV) and print a summary."""
    import duckdb

    from twm.asof import WarehouseTooOldError
    from twm.config import ROOT, settings
    from twm.modules.waiver_radar.report import build_report, write_report

    path = _warehouse_or_exit(db)
    last = end if end is not None else settings().current_season
    if start > last:
        raise typer.BadParameter(f"--start {start} is after --end {last}")
    try:
        report = build_report(path, list(range(start, last + 1)))
    except (LookupError, ValueError, WarehouseTooOldError, duckdb.Error) as e:
        typer.echo(f"cannot build the report: {e}", err=True)
        raise typer.Exit(code=1) from e
    target = out if out.is_absolute() else ROOT / out
    csv_path = write_report(report, target)
    for line in report.summary:
        typer.echo(line)
    typer.echo(f"wrote {target}")
    typer.echo(f"wrote {csv_path}")


def _list_text(values: list | None, *, digits: int | None = None) -> str:
    if values is None:
        return "?"
    if digits is None:
        return ",".join("-" if v is None else str(v) for v in values)
    return ",".join("-" if v is None else f"{v:.{digits}f}" for v in values)


@radar_app.command("labels")
def radar_labels(
    season: int = typer.Argument(..., help="Season, e.g. 2023."),
    week: int = typer.Argument(..., help="Regular-season week N; the pool at its Tuesday as-of."),
    pos: str | None = typer.Option(None, "--pos", help="Only this position (QB, RB, WR, TE)."),
    hits_only: bool = typer.Option(False, "--hits-only", help="Only players with y_hit."),
    show_all: bool = typer.Option(
        False, "--all", help="Also list rostered players outside the pool."
    ),
    limit: int = typer.Option(30, "--limit", help="Rows to print (0 = all)."),
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
) -> None:
    """Print one as-of's labelled pool: each player's window weeks, weekly ranks and labels.

    y_hit = at least one starter finish (weekly rank at or above the starter threshold of his
    position) in the next 3 weeks his team plays after week N; y_sustained = at least two.
    docs/waiver_radar.md explains every rule.
    """
    import duckdb
    import polars as pl

    from twm.asof import AsOfView, WarehouseTooOldError, weekly_as_of
    from twm.modules.waiver_radar.labels import label_rows, last_reg_weeks
    from twm.modules.waiver_radar.pool import candidate_pool

    path = _warehouse_or_exit(db)
    try:
        when = weekly_as_of(path, season, week)
        last = last_reg_weeks(path, [season]).get(season)
        if last is not None and week >= last:
            typer.echo(
                f"{season} week {week} is the last regular-season week: there is no window "
                "after it, so it has no labels."
            )
            return
        with AsOfView(path, when) as view:
            pool = candidate_pool(view, season, week)
        df = label_rows(path, pool)
    except (LookupError, ValueError, WarehouseTooOldError, duckdb.Error) as e:
        typer.echo(f"cannot label the pool: {e}", err=True)
        raise typer.Exit(code=1) from e
    if pool.height == 0:
        typer.echo(
            f"{season} week {week}, as-of {when:%Y-%m-%d %H:%M} UTC: the pool is empty (no "
            "weekly roster was public yet; docs/waiver_radar.md)."
        )
        return
    if pos is not None:
        df = df.filter(pl.col("position") == pos.upper())
    if not show_all:
        df = df.filter(pl.col("in_pool"))
    n_rows = df.height
    n_hit = int(df.get_column("y_hit").fill_null(False).sum())
    n_sus = int(df.get_column("y_sustained").fill_null(False).sum())
    n_pending = int((df.get_column("label_status") == "pending").sum())
    if hits_only:
        df = df.filter(pl.col("y_hit").fill_null(False))
    df = df.sort(
        ["y_hit", "n_starter_finishes", "best_rank", "ppg_to_date", "gsis_id"],
        descending=[True, True, False, True, False],
        nulls_last=True,
    )
    who = "rostered players" if show_all else "pool players"
    typer.echo(
        f"{season} week {week}, as-of {when:%Y-%m-%d %H:%M} UTC: {n_rows} {who}"
        + (f" at {pos.upper()}" if pos else "")
        + f", {n_hit} y_hit, {n_sus} y_sustained, {n_pending} pending"
    )
    shown = df if limit == 0 else df.head(limit)
    table = shown.select(
        "name", "team", "position", "in_pool", "ppg_to_date",
        pl.col("window_weeks").map_elements(_list_text, return_dtype=pl.String).alias("weeks"),
        pl.col("window_ranks").map_elements(_list_text, return_dtype=pl.String,
                                            skip_nulls=False).alias("ranks"),
        pl.col("window_points").map_elements(lambda v: _list_text(v, digits=1),
                                             return_dtype=pl.String,
                                             skip_nulls=False).alias("points"),
        "n_window_played", "n_starter_finishes", "y_hit", "y_sustained", "label_status",
    )  # fmt: skip
    with pl.Config(tbl_rows=-1, tbl_cols=-1, tbl_width_chars=220, float_precision=1,
                   tbl_hide_dataframe_shape=True, tbl_hide_column_data_types=True):  # fmt: skip
        typer.echo(str(table))


@radar_app.command("labels-report")
def radar_labels_report(
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
    out: Path = typer.Option(
        Path("reports/waiver_radar/labels.md"),
        "--out",
        help="Markdown file to write (relative paths are under the project root); a CSV with "
        "the same name is written next to it.",
    ),
    start: int = typer.Option(2013, "--start", help="First season (snap counts start 2013)."),
    end: int | None = typer.Option(None, "--end", help="Last season (default: current)."),
) -> None:
    """Write the label report (base rates, windows, checks, example hits; markdown + CSV)."""
    import duckdb

    from twm.asof import WarehouseTooOldError
    from twm.config import ROOT, settings
    from twm.modules.waiver_radar.label_report import build_label_report, write_label_report

    path = _warehouse_or_exit(db)
    last = end if end is not None else settings().current_season
    if start > last:
        raise typer.BadParameter(f"--start {start} is after --end {last}")
    try:
        report = build_label_report(path, list(range(start, last + 1)))
    except (LookupError, ValueError, WarehouseTooOldError, duckdb.Error) as e:
        typer.echo(f"cannot build the report: {e}", err=True)
        raise typer.Exit(code=1) from e
    target = out if out.is_absolute() else ROOT / out
    csv_path = write_label_report(report, target)
    for line in report.summary:
        typer.echo(line)
    typer.echo(f"wrote {target}")
    typer.echo(f"wrote {csv_path}")


@radar_app.command("features")
def radar_features(
    season: int = typer.Argument(..., help="Season, e.g. 2023."),
    week: int = typer.Argument(..., help="Regular-season week N; the pool at its Tuesday as-of."),
    pos: str | None = typer.Option(None, "--pos", help="Only this position (QB, RB, WR, TE)."),
    team: str | None = typer.Option(None, "--team", help="Only this team (e.g. KC)."),
    show_all: bool = typer.Option(
        False, "--all", help="Also list rostered players outside the pool."
    ),
    limit: int = typer.Option(25, "--limit", help="Rows to print (0 = all)."),
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
) -> None:
    """Print one as-of's pool with its key features (reference path, through the as-of view)
    and every unavailable teammate with the rule that fired. docs/waiver_radar.md explains
    each feature; `twm glossary <feature>` too."""
    import duckdb
    import polars as pl

    from twm.asof import AsOfView, WarehouseTooOldError, weekly_as_of
    from twm.modules.waiver_radar.features import features_for, unavailable_teammates
    from twm.modules.waiver_radar.pool import candidate_pool

    path = _warehouse_or_exit(db)
    try:
        when = weekly_as_of(path, season, week)
        with AsOfView(path, when) as view:
            pool = candidate_pool(view, season, week)
            feats = features_for(view, season, week, pool)
            mates = unavailable_teammates(view, season)
    except (LookupError, ValueError, KeyError, WarehouseTooOldError, duckdb.Error) as e:
        typer.echo(f"cannot compute the features: {e}", err=True)
        raise typer.Exit(code=1) from e
    df = pool.select("gsis_id", "name", "team", "in_pool").join(feats, on="gsis_id")
    if pos is not None:
        df = df.filter(pl.col("position") == pos.upper())
    if team is not None:
        df = df.filter(pl.col("team") == team.upper())
        mates = mates.filter(pl.col("team") == team.upper())
    if not show_all:
        df = df.filter(pl.col("in_pool"))
    df = df.sort(
        ["same_pos_vacated_target_share", "snap_share_last", "gsis_id"],
        descending=[True, True, False],
        nulls_last=True,
    )
    typer.echo(
        f"{season} week {week}, as-of {when:%Y-%m-%d %H:%M} UTC: {df.height} "
        + ("rostered" if show_all else "pool")
        + " players, most vacated same-position targets first"
    )
    cols = ["name", "team", "position", "snap_share_last", "snap_share_avg3",
            "snap_share_delta", "target_share_avg3", "carry_share_avg3", "xfp_avg3",
            "depth_rank_now", "depth_rank_change", "same_pos_vacated_target_share",
            "same_pos_vacated_carry_share", "top_teammate_out"]  # fmt: skip
    shown = df.select(cols) if limit == 0 else df.select(cols).head(limit)
    cfg = dict(tbl_rows=-1, tbl_cols=-1, tbl_width_chars=240, float_precision=2,
               tbl_hide_dataframe_shape=True, tbl_hide_column_data_types=True)  # fmt: skip
    with pl.Config(**cfg):
        typer.echo(str(shown))
        typer.echo(f"unavailable teammates ({mates.height}):")
        typer.echo(
            str(
                mates.select("team", "name", "position", "rules", "last_week",
                             "target_share_avg3", "carry_share_avg3", "snap_share_avg3")
            )
        )  # fmt: skip


@radar_app.command("dataset")
def radar_dataset(
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
    out: Path = typer.Option(
        Path("data/waiver_radar/dataset.parquet"),
        "--out",
        help="Parquet file to write (relative paths are under the project root; data/ is "
        "gitignored).",
    ),
    start: int = typer.Option(2013, "--start", help="First season (snap counts start 2013)."),
    end: int | None = typer.Option(None, "--end", help="Last season (default: current)."),
) -> None:
    """Build the training dataset: pool rows + features + labels, one row per (season, week,
    player), sorted; deterministic (the same warehouse gives the same file)."""
    import time

    import duckdb

    from twm.asof import WarehouseTooOldError
    from twm.config import ROOT, settings
    from twm.modules.waiver_radar.dataset import build_dataset, write_dataset

    path = _warehouse_or_exit(db)
    last = end if end is not None else settings().current_season
    if start > last:
        raise typer.BadParameter(f"--start {start} is after --end {last}")
    t0 = time.perf_counter()
    try:
        df = build_dataset(path, list(range(start, last + 1)))
    except (LookupError, ValueError, KeyError, WarehouseTooOldError, duckdb.Error) as e:
        typer.echo(f"cannot build the dataset: {e}", err=True)
        raise typer.Exit(code=1) from e
    target = out if out.is_absolute() else ROOT / out
    write_dataset(df, target)
    secs = time.perf_counter() - t0
    n_pool = int(df.get_column("in_pool").sum()) if df.height else 0
    typer.echo(
        f"{df.height:,} rows ({n_pool:,} in the pool), {df.width} columns, seasons "
        f"{start}-{last}, {target.stat().st_size / 1e6:.1f} MB, {secs:.0f} s"
    )
    typer.echo(f"wrote {target}")


@radar_app.command("features-report")
def radar_features_report(
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
    dataset: Path = typer.Option(
        Path("data/waiver_radar/dataset.parquet"),
        "--dataset",
        help="The dataset from `twm radar dataset` (relative paths are under the project root).",
    ),
    out: Path = typer.Option(
        Path("reports/waiver_radar/features.md"),
        "--out",
        help="Markdown file to write (relative paths are under the project root); a CSV with "
        "the same name is written next to it.",
    ),
) -> None:
    """Write the feature report (null rates, means for hits and misses, single-feature AUC per
    position, the xFP check; markdown + CSV) from the dataset."""
    import duckdb
    import polars as pl

    from twm.config import ROOT
    from twm.modules.waiver_radar.feature_report import build_feature_report, write_feature_report

    path = _warehouse_or_exit(db)
    source = dataset if dataset.is_absolute() else ROOT / dataset
    if not source.exists():
        typer.echo(f"dataset not found: {source}; run `twm radar dataset` first", err=True)
        raise typer.Exit(code=1)
    try:
        report = build_feature_report(pl.read_parquet(source), path)
    except (LookupError, ValueError, KeyError, duckdb.Error) as e:
        typer.echo(f"cannot build the report: {e}", err=True)
        raise typer.Exit(code=1) from e
    target = out if out.is_absolute() else ROOT / out
    csv_path = write_feature_report(report, target)
    for line in report.summary:
        typer.echo(line)
    typer.echo(f"wrote {target}")
    typer.echo(f"wrote {csv_path}")


@radar_app.command("backtest")
def radar_backtest(
    dataset: Path = typer.Option(
        Path("data/waiver_radar/dataset.parquet"),
        "--dataset",
        help="The dataset from `twm radar dataset` (relative paths are under the project root).",
    ),
    start: int | None = typer.Option(
        None, "--start", help="First test season (default: settings waiver_radar_eval, 2014)."
    ),
    end: int | None = typer.Option(None, "--end", help="Last test season (default: 2025)."),
    models: list[str] | None = typer.Option(
        None,
        "--models",
        "--model",
        help="Models to run (repeatable; default: all): baseline_last_points, "
        "baseline_snap_delta, baseline_ecr, logit, lgbm.",
    ),
    labels: list[str] | None = typer.Option(
        None, "--label", help="Label(s) to predict (repeatable; default y_hit): y_hit, y_sustained."
    ),
    store: Path | None = typer.Option(
        None,
        "--store",
        help="Predictions store to write (default: settings paths.predictions, "
        "data/predictions.duckdb).",
    ),
    out: Path = typer.Option(
        Path("reports/waiver_radar/backtest.md"),
        "--out",
        help="Markdown report to write (relative paths are under the project root); a CSV with "
        "the same name is written next to it.",
    ),
) -> None:
    """Walk-forward backtest of the Waiver Radar: baselines, logistic regression and LightGBM
    trained on earlier seasons only; stores every prediction and writes the report."""
    import time

    import polars as pl

    from twm import predictions as pr
    from twm.backtest.walkforward import WalkForwardError
    from twm.config import ROOT
    from twm.modules.waiver_radar import backtest as bt
    from twm.modules.waiver_radar.models import ALL_MODELS
    from twm.registry import FeatureCheckError

    source = dataset if dataset.is_absolute() else ROOT / dataset
    if not source.exists():
        typer.echo(f"dataset not found: {source}; run `twm radar dataset` first", err=True)
        raise typer.Exit(code=1)
    first, last = bt.eval_seasons()
    first = start if start is not None else first
    last = end if end is not None else last
    if first > last:
        raise typer.BadParameter(f"--start {first} is after --end {last}")
    t0 = time.perf_counter()
    try:
        run = bt.run_backtest(
            pl.read_parquet(source),
            models=models or ALL_MODELS,
            labels=labels or ["y_hit"],
            test_seasons=range(first, last + 1),
            progress=lambda msg: typer.echo(f"  {msg} ({time.perf_counter() - t0:.0f} s)"),
        )
    except (WalkForwardError, FeatureCheckError, ValueError, KeyError) as e:
        typer.echo(f"cannot run the backtest: {e}", err=True)
        raise typer.Exit(code=1) from e
    created = pr.now_utc()
    preds, versions, outcomes = bt.store_frames(run, created_at=created)
    store_path = store if store is not None else pr.default_path()
    store_path = store_path if store_path.is_absolute() else ROOT / store_path
    counts = pr.write_predictions(
        store_path, predictions=preds, versions=versions, outcomes=outcomes
    )
    secs = time.perf_counter() - t0
    args = ["uv run twm radar backtest"]
    if start is not None or end is not None:
        args.append(f"--start {first} --end {last}")
    args += [f"--model {m}" for m in models or []]
    args += [f"--label {lb}" for lb in labels or []]
    report = bt.build_backtest_report(
        run,
        created=f"Created {created:%Y-%m-%d %H:%M} UTC in {secs:.0f} s.",
        command=" ".join(args),
        dataset_name=str(dataset),
    )
    target = out if out.is_absolute() else ROOT / out
    csv_path = bt.write_backtest_report(report, target)
    for line in report.summary:
        typer.echo(line)
    typer.echo(
        f"stored {counts['predictions']:,} predictions, {counts['model_versions']} model "
        f"versions, {counts['outcomes']:,} outcomes in {store_path}"
    )
    typer.echo(f"wrote {target}")
    typer.echo(f"wrote {csv_path}")
    typer.echo(f"{time.perf_counter() - t0:.0f} s")


@radar_app.command("evaluate")
def radar_evaluate(
    labels: list[str] | None = typer.Option(
        None,
        "--label",
        help="Label(s) to evaluate (repeatable; default both): y_hit, y_sustained.",
    ),
    store: Path | None = typer.Option(
        None,
        "--store",
        help="Predictions store to read (default: settings paths.predictions, "
        "data/predictions.duckdb). Read only.",
    ),
    dataset: Path = typer.Option(
        Path("data/waiver_radar/dataset.parquet"),
        "--dataset",
        help="The dataset (names, rostership, experts' coverage, labels outside the pool).",
    ),
    out: Path = typer.Option(
        Path("reports/waiver_radar/evaluation.md"),
        "--out",
        help="Markdown report to write (relative paths are under the project root); a CSV with "
        "every number is written next to it.",
    ),
    figures: Path | None = typer.Option(
        None,
        "--figures",
        help="Folder for the PNG figures (default: `figures/` next to the report).",
    ),
    no_figures: bool = typer.Option(False, "--no-figures", help="Skip the figures."),
) -> None:
    """Evaluate the Waiver Radar backtest from the predictions store: precision@10 with
    season-block bootstrap intervals, paired differences, rank buckets, PR-AUC, Brier,
    calibration and breakouts caught, with and without rostered players; writes the report,
    its CSV and the figures (docs/waiver_radar.md 'Evaluation')."""
    import time

    import polars as pl

    from twm import predictions as pr
    from twm.config import ROOT
    from twm.modules.waiver_radar import evaluation as ev
    from twm.modules.waiver_radar.models import LABELS

    t0 = time.perf_counter()
    chosen = list(dict.fromkeys(labels or LABELS))
    bad = [lb for lb in chosen if lb not in LABELS]
    if bad:
        raise typer.BadParameter(f"unknown label(s) {bad}; choose from {list(LABELS)}")
    source = dataset if dataset.is_absolute() else ROOT / dataset
    if not source.exists():
        typer.echo(f"dataset not found: {source}; run `twm radar dataset` first", err=True)
        raise typer.Exit(code=1)
    store_path = store if store is not None else pr.default_path()
    store_path = store_path if store_path.is_absolute() else ROOT / store_path
    target = out if out.is_absolute() else ROOT / out
    fig_dir = figures if figures is not None else target.parent / "figures"
    fig_dir = fig_dir if fig_dir.is_absolute() else ROOT / fig_dir
    try:
        ds = pl.read_parquet(source)
        evals = [ev.evaluate_label(ev.load(store_path, ds, lb)) for lb in chosen]
    except (ev.EvaluationError, ValueError) as e:
        typer.echo(f"cannot evaluate: {e}", err=True)
        raise typer.Exit(code=1) from e
    fig_names: list[str] = []
    if not no_figures:
        from twm.modules.waiver_radar.figures import write_figures

        for path in write_figures(evals, fig_dir):
            try:
                fig_names.append(path.relative_to(target.parent).as_posix())
            except ValueError:
                fig_names.append(path.name)
    args = ["uv run twm radar evaluate", *(f"--label {lb}" for lb in labels or [])]
    report = ev.build_evaluation_report(
        evals,
        generated=f"Generated at {pr.now_utc():%Y-%m-%d %H:%M} UTC in "
        f"{time.perf_counter() - t0:.0f} s.",
        command=" ".join(args),
        store_name=_display_path(store_path, ROOT),
        dataset_name=_display_path(source, ROOT),
        figures=fig_names,
    )
    csv_path = ev.write_evaluation_report(report, target)
    for line in report.summary:
        typer.echo(line)
    typer.echo(f"wrote {_display_path(target, ROOT)}")
    typer.echo(f"wrote {_display_path(csv_path, ROOT)}")
    if fig_names:
        typer.echo(f"wrote {len(fig_names)} figures in {_display_path(fig_dir, ROOT)}")
    typer.echo(f"{time.perf_counter() - t0:.0f} s")


def _clock(now: str | None):
    """The time a command runs at, aware UTC: ``--now`` (ISO, UTC unless it says otherwise;
    for tests and reproductions) or the real clock."""
    from datetime import UTC, datetime

    if now is None:
        return datetime.now(UTC)
    try:
        t = datetime.fromisoformat(now)
    except ValueError as e:
        raise typer.BadParameter(f"--now {now!r} is not an ISO date-time") from e
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)


def _project_path(path: Path) -> Path:
    from twm.config import ROOT

    return path if path.is_absolute() else ROOT / path


@radar_app.command("score")
def radar_score(
    season: int | None = typer.Option(
        None, "--season", help="Season (default: settings current_season)."
    ),
    week: int | None = typer.Option(
        None,
        "--week",
        help="Regular-season week N: the list at its Tuesday as-of (default: the latest week "
        "whose as-of has passed).",
    ),
    allow_incomplete: bool = typer.Option(
        False,
        "--allow-incomplete",
        help="Score even if some of the week's data has not arrived; the list is marked "
        "incomplete in the store and the report.",
    ),
    limit: int = typer.Option(10, "--limit", help="Players per position to print (report: 25)."),
    retrain: bool = typer.Option(
        False, "--retrain", help="Train a new production model even if the stored one matches."
    ),
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
    store: Path | None = typer.Option(
        None, "--store", help="Predictions store (default: settings paths.predictions)."
    ),
    dataset: Path = typer.Option(
        Path("data/waiver_radar/dataset.parquet"),
        "--dataset",
        help="The training dataset from `twm radar dataset` (the production model learns from "
        "its seasons before --season).",
    ),
    models_dir: Path | None = typer.Option(
        None, "--models-dir", help="Where models are saved (default: models/waiver_radar)."
    ),
    evaluation_csv: Path = typer.Option(
        Path("reports/waiver_radar/evaluation.csv"),
        "--evaluation",
        help="The C5 evaluation CSV (position notes such as the QB one are computed from it).",
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Report to write (default: reports/waiver_radar/weekly/<season>-W<week>.md).",
    ),
    now: str | None = typer.Option(
        None,
        "--now",
        hidden=True,
        help="Pretend it is this UTC time (ISO), to reproduce a run; the list is then never "
        "stored as 'live'.",
    ),
) -> None:
    """Score a week's Waiver Radar list with the production model: checks that the week's data
    has arrived (exit code 3 if not), stores every prediction with its band, priority and
    reasons, writes the weekly report and prints the top of each position
    (docs/waiver_radar.md 'Weekly list')."""
    import time

    import polars as pl

    from twm import predictions as pr
    from twm.asof import WarehouseTooOldError, weekly_as_of
    from twm.backtest.walkforward import WalkForwardError
    from twm.config import ROOT, settings
    from twm.modules.waiver_radar import confidence as cf
    from twm.modules.waiver_radar import production as prod
    from twm.modules.waiver_radar import weekly as wk
    from twm.modules.waiver_radar.evaluation import EvaluationError

    t0 = time.perf_counter()
    path = _warehouse_or_exit(db)
    clock = _clock(now)
    chosen_season = season if season is not None else settings().current_season
    try:
        chosen_week = week if week is not None else wk.default_week(path, chosen_season, clock)
        as_of = weekly_as_of(path, chosen_season, chosen_week)
    except LookupError as e:
        typer.echo(f"cannot score: {e}", err=True)
        raise typer.Exit(code=1) from e
    fresh = wk.check_freshness(
        wk.freshness_inputs(path, chosen_season, chosen_week), chosen_season, chosen_week,
        as_of, clock,
    )  # fmt: skip
    if not fresh.ok and not allow_incomplete:
        typer.echo(fresh.message(), err=True)
        done = wk.last_complete_week(path, chosen_season, clock, before=chosen_week)
        if done is not None:
            typer.echo(
                f"The latest complete week is {done}: `uv run twm radar score --season "
                f"{chosen_season} --week {done}`.",
                err=True,
            )
        raise typer.Exit(code=wk.EXIT_NOT_READY)
    source = _project_path(dataset)
    if not source.exists():
        typer.echo(f"dataset not found: {source}; run `twm radar dataset` first", err=True)
        raise typer.Exit(code=1)
    store_path = _project_path(store if store is not None else pr.default_path())
    models_root = _project_path(models_dir) if models_dir is not None else None
    try:
        ens = prod.ensure_production(
            pl.read_parquet(source), chosen_season, store=store_path, root=models_root,
            retrain=retrain,
        )  # fmt: skip
        conf = cf.from_store(
            store_path, model=ens.model.model, label=ens.model.label,
            seasons=cf.seasons_before(chosen_season),
        )  # fmt: skip
        notes = cf.position_notes(
            _project_path(evaluation_csv), label=ens.model.label, model=ens.model.model,
            before=chosen_season,
        )  # fmt: skip
        run = wk.run_week(
            path, chosen_season, chosen_week, model=ens.model, conf=conf, now=clock,
            allow_incomplete=allow_incomplete, notes=notes, model_reused=ens.reused,
            real_clock=now is None,
        )  # fmt: skip
    except wk.NotReadyError as e:  # pragma: no cover - checked above; data changed meanwhile
        typer.echo(str(e), err=True)
        raise typer.Exit(code=wk.EXIT_NOT_READY) from e
    except (
        prod.ProductionModelError, EvaluationError, WalkForwardError, WarehouseTooOldError,
        LookupError, ValueError,
    ) as e:  # fmt: skip
        typer.echo(f"cannot score: {e}", err=True)
        raise typer.Exit(code=1) from e
    try:
        counts = wk.store_week(run, store_path, created_at=clock.replace(tzinfo=None))
    except pr.LiveWeekError as e:
        typer.echo(f"not stored: {e}", err=True)
        raise typer.Exit(code=1) from e
    args = [f"uv run twm radar score --season {chosen_season} --week {chosen_week}"]
    if allow_incomplete:
        args.append("--allow-incomplete")
    text = wk.build_report(
        run, generated=f"Generated at {clock:%Y-%m-%d %H:%M} UTC.", command=" ".join(args)
    )
    target = _project_path(out) if out is not None else wk.report_path(chosen_season, chosen_week)
    wk.write_report(text, target)
    m = ens.model
    typer.echo(
        f"{chosen_season} week {chosen_week}, as-of {as_of:%a %Y-%m-%d %H:%M} UTC: stored as "
        f"'{run.kind}'" + (" (INCOMPLETE DATA)" if run.incomplete else "")
    )
    typer.echo(
        f"model {m.model_version} ({'reused' if ens.reused else 'trained now'}; trained on "
        f"{m.training_seasons[0]}-{m.training_seasons[-1]}, calibrated on {m.fold.val_season})"
    )
    if run.incomplete:
        typer.echo("WARNING: scored with incomplete data:")
        for p in run.freshness.problems:
            typer.echo(f"  - {p}")
    cut = conf.cutoffs
    lo_s, hi_s = conf.seasons
    typer.echo(
        f"priority from the {lo_s}-{hi_s} backtest: must-add = similar players hit 50%+ (model "
        f"probability {cut['must-add'] or 0:.1%} and up), speculative = 25-50% "
        f"({cut['speculative'] or 0:.1%} and up), watch = the rest of the top 25"
    )
    for r in conf.tiers.iter_rows(named=True):
        typer.echo(
            f"  {r['tier']:<11} hit {100 * (r['rate'] or 0):.1f}% in the backtest "
            f"({r['hits']:,} of {r['rows']:,} top-25 players)"
        )
    for note in run.notes.values():
        typer.echo(note)
    typer.echo(wk.compact_table(run.scored, limit))
    typer.echo(
        f"stored {counts['predictions']:,} predictions and {counts['outcomes']:,} outcomes in "
        f"{_display_path(store_path, ROOT)}"
    )
    typer.echo(f"wrote {_display_path(target, ROOT)}")
    typer.echo(f"{time.perf_counter() - t0:.0f} s")


@radar_app.command("week")
def radar_week(
    season: int = typer.Argument(..., help="Season, e.g. 2023."),
    week: int = typer.Argument(..., help="Regular-season week N (the list of its Tuesday as-of)."),
    pos: str | None = typer.Option(None, "--pos", help="Only this position (QB, RB, WR, TE)."),
    limit: int = typer.Option(25, "--limit", help="Players per position to print."),
    store: Path | None = typer.Option(
        None, "--store", help="Predictions store (default: settings paths.predictions). Read only."
    ),
    db: Path | None = typer.Option(
        None, "--db", help="Warehouse for names, teams and outcomes (default from config)."
    ),
) -> None:
    """Show a stored Waiver Radar week (backtest or live) with outcomes where known: the time
    machine's first form."""
    import polars as pl

    from twm import predictions as pr
    from twm.config import settings
    from twm.modules.waiver_radar import weekly as wk

    store_path = _project_path(store if store is not None else pr.default_path())
    try:
        rows, info = wk.stored_week(store_path, season, week)
    except FileNotFoundError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e
    if rows.height == 0:
        typer.echo(
            f"nothing stored for {season} week {week}: run `twm radar backtest` (2014-2025) or "
            f"`twm radar score --season {season} --week {week}`"
        )
        raise typer.Exit(code=1)
    wh = Path(db) if db is not None else settings().path("warehouse")
    rows = wk.attach_names_and_outcomes(rows, wh if wh.exists() else None)
    if pos is not None:
        rows = rows.filter(pl.col("position") == pos.upper())
    kinds = "/".join(info["kind"])
    typer.echo(
        f"{season} week {week}, as-of {info['as_of']:%a %Y-%m-%d %H:%M} UTC: {kinds} list of "
        f"{info['model_version']}"
        + (" (INCOMPLETE DATA)" if info["incomplete"] else "")
        + (f"; {info['versions']} versions scored this week" if info["versions"] > 1 else "")
    )
    if kinds == "backtest":
        typer.echo("(backtest = reconstructed after the fact; live = made in real time)")
    typer.echo(wk.week_text(rows, info, limit=limit))


# --------------------------------------------------------------------------------------
# The spec's generic entry points (PROJECT_SPEC 9): `twm train|backtest <module>`, `twm score`
# --------------------------------------------------------------------------------------

# Modules with a train/backtest/score implementation (the `twm radar ...` commands stay).
MODULES = {"waiver_radar": "Waiver Radar (`twm radar ...`)"}
# Modules of the spec that are not built yet, with the phase that builds them (spec 11).
PLANNED_MODULES = {"regression_watch": "phase D", "decisions": "phase G", "hot_seat": "phase H",
                   "board": "phase I"}  # fmt: skip


def _module_or_exit(name: str) -> str:
    key = name.strip().lower().replace("-", "_")
    if key in MODULES:
        return key
    built = ", ".join(MODULES)
    if key in PLANNED_MODULES:
        raise typer.BadParameter(
            f"{key} is not built yet ({PLANNED_MODULES[key]}); available: {built}"
        )
    raise typer.BadParameter(f"unknown module {name!r}; available: {built}")


@app.command("train")
def train(
    module: str = typer.Argument(..., help="Module to train: waiver_radar."),
    season: int | None = typer.Option(
        None, "--season", help="Season the model will score (default: settings current_season)."
    ),
    retrain: bool = typer.Option(
        False, "--retrain", help="Train a new model even if the stored one matches the data."
    ),
    dataset: Path = typer.Option(
        Path("data/waiver_radar/dataset.parquet"),
        "--dataset",
        help="The training dataset from `twm radar dataset`.",
    ),
    store: Path | None = typer.Option(
        None, "--store", help="Predictions store (default: settings paths.predictions)."
    ),
    models_dir: Path | None = typer.Option(
        None, "--models-dir", help="Where models are saved (default: models/waiver_radar)."
    ),
) -> None:
    """Build or refresh a module's production model for the current season.

    waiver_radar: the walk-forward fold for the season (logistic regression for y_hit, trained
    on every season before it, tuned and calibrated on the last of them). A stored model is
    reused when it was trained on exactly today's dataset; otherwise a new one is trained,
    saved under models/waiver_radar/ and recorded in the predictions store (docs/waiver_radar.md
    'The production model'). Run it after `twm radar dataset` rebuilt the dataset."""
    import polars as pl

    from twm import predictions as pr
    from twm.backtest.walkforward import WalkForwardError
    from twm.config import settings
    from twm.modules.waiver_radar import production as prod

    _module_or_exit(module)
    chosen = season if season is not None else settings().current_season
    source = _project_path(dataset)
    if not source.exists():
        typer.echo(f"dataset not found: {source}; run `twm radar dataset` first", err=True)
        raise typer.Exit(code=1)
    store_path = _project_path(store if store is not None else pr.default_path())
    root = _project_path(models_dir) if models_dir is not None else None
    try:
        ens = prod.ensure_production(
            pl.read_parquet(source), chosen, store=store_path, root=root, retrain=retrain
        )
    except (prod.ProductionModelError, WalkForwardError, ValueError) as e:
        typer.echo(f"cannot train: {e}", err=True)
        raise typer.Exit(code=1) from e
    from twm.config import ROOT

    m = ens.model
    typer.echo(
        f"waiver_radar {chosen}: {m.model_version} "
        + ("reused (already trained on today's dataset)" if ens.reused else "trained now")
    )
    typer.echo(f"  {m.describe()}")
    typer.echo(f"  file: {_display_path(ens.path, ROOT)}; store: {_display_path(store_path, ROOT)}")


@app.command("backtest")
def backtest(
    module: str = typer.Argument(..., help="Module to backtest: waiver_radar."),
    dataset: Path = typer.Option(
        Path("data/waiver_radar/dataset.parquet"), "--dataset", help="The dataset."
    ),
    start: int | None = typer.Option(None, "--start", help="First test season (default 2014)."),
    end: int | None = typer.Option(None, "--end", help="Last test season (default 2025)."),
    models: list[str] | None = typer.Option(
        None, "--models", "--model", help="Models to run (repeatable; default: all)."
    ),
    labels: list[str] | None = typer.Option(
        None, "--label", help="Label(s) (repeatable; default y_hit): y_hit, y_sustained."
    ),
    store: Path | None = typer.Option(None, "--store", help="Predictions store to write."),
    out: Path = typer.Option(
        Path("reports/waiver_radar/backtest.md"), "--out", help="Markdown report to write."
    ),
) -> None:
    """Walk-forward backtest of a module (waiver_radar: the same as `twm radar backtest`)."""
    _module_or_exit(module)
    radar_backtest(
        dataset=dataset, start=start, end=end, models=models, labels=labels, store=store, out=out
    )


@app.command("score")
def score(
    as_of: str | None = typer.Option(
        None,
        "--as-of",
        help="Which week: a season-week (2026-W3 or '2026 3') or an ISO timestamp with a zone "
        "(2026-09-30T18:00Z), which maps to the week whose list was current then. Default: the "
        "latest week whose Tuesday as-of has passed.",
    ),
    modules: list[str] | None = typer.Option(
        None, "--module", help="Module(s) to score (repeatable; default: all built)."
    ),
    allow_incomplete: bool = typer.Option(
        False, "--allow-incomplete", help="Score even if some of the week's data is missing."
    ),
    limit: int = typer.Option(10, "--limit", help="Players per position to print."),
    retrain: bool = typer.Option(False, "--retrain", help="Train a new production model."),
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
    store: Path | None = typer.Option(None, "--store", help="Predictions store."),
    dataset: Path = typer.Option(
        Path("data/waiver_radar/dataset.parquet"), "--dataset", help="The training dataset."
    ),
) -> None:
    """Score every module's list at an official as-of (waiver_radar: `twm radar score`).

    A list is always made from the data as it stood at a week's official as-of (the Tuesday
    14:00 UTC after the week's games), never at an arbitrary moment: `--as-of 2026-W3` is week
    3's as-of; a timestamp picks the week whose list was current then, i.e. the latest
    regular-season as-of at or before it (Wednesday 2026-09-30 is week 3's list; a timestamp
    in the offseason is the last week of the season before). A timestamp needs a time zone.
    Exit codes as `twm radar score` (3: the week's data has not arrived)."""
    from twm.asof import AsOfParseError, parse_as_of, week_at

    chosen = [_module_or_exit(m) for m in (modules or list(MODULES))]
    season: int | None = None
    week: int | None = None
    if as_of is not None:
        try:
            target = parse_as_of(as_of)
        except AsOfParseError as e:
            raise typer.BadParameter(str(e)) from e
        if isinstance(target, tuple):
            season, week = target
        else:
            path = _warehouse_or_exit(db)
            try:
                season, week, official = week_at(path, target)
            except LookupError as e:
                typer.echo(f"cannot score: {e}", err=True)
                raise typer.Exit(code=1) from e
            typer.echo(
                f"--as-of {target:%Y-%m-%d %H:%M} UTC is in the window of {season} week {week} "
                f"(its list is made at the official as-of {official:%a %Y-%m-%d %H:%M} UTC)"
            )
    for _module in dict.fromkeys(chosen):  # only waiver_radar exists
        radar_score(
            season=season, week=week, allow_incomplete=allow_incomplete, limit=limit,
            retrain=retrain, db=db, store=store, dataset=dataset, models_dir=None,
            evaluation_csv=Path("reports/waiver_radar/evaluation.csv"), out=None, now=None,
        )  # fmt: skip


def _display_path(path: Path, root: Path) -> str:
    """A path relative to the project root when it is inside it (reports never carry a local
    absolute path), else the path as given."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            return str(path)


# --------------------------------------------------------------------------------------
# Publishing (step E2): the public database
# --------------------------------------------------------------------------------------


@app.command()
def publish(
    target: str = typer.Option(
        ...,
        "--target",
        help="local = the Docker database (TWM_LOCAL_DATABASE_URL, default docker-compose.yml); "
        "remote = the public Neon database (DATABASE_URL, the writer role).",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Do everything, then roll back: shows what would change."
    ),
    allow_incomplete: bool = typer.Option(
        False,
        "--allow-incomplete",
        help="Also publish live lists that were scored with incomplete data (skipped by "
        "default, with exit code 4).",
    ),
    replace_live: list[str] | None = typer.Option(
        None,
        "--replace-live",
        help="SEASON-Wnn, e.g. 2026-W04: replace that week's published live lists with the "
        "local ones (live lists are otherwise frozen). Prints what it replaces. Repeatable.",
    ),
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
    store: Path | None = typer.Option(
        None, "--store", help="Predictions store (default: settings paths.predictions)."
    ),
    dataset: Path = typer.Option(
        Path("data/waiver_radar/dataset.parquet"), "--dataset", help="The Waiver Radar dataset."
    ),
    evaluation_csv: Path = typer.Option(
        Path("reports/waiver_radar/evaluation.csv"),
        "--evaluation",
        help="The committed evaluation CSV (published as the track record).",
    ),
    now: str | None = typer.Option(None, "--now", hidden=True, help="Pretend clock (tests)."),
) -> None:
    """Publish the Waiver Radar lists, outcomes, track record, player pages and glossary to
    Postgres, in one transaction (docs/deploy.md). Live lists already published are never
    changed (frozen); everything else is replaced. Exit codes: 0 done, 1 refused or failed
    (nothing changed), 4 done but some live lists were skipped as incomplete (retry later)."""
    import uuid

    from twm.config import settings
    from twm.publish import collect as col
    from twm.publish import target as tg
    from twm.publish import write as wr

    try:
        tgt = tg.resolve(target)
        weeks = [wr.parse_week(w) for w in (replace_live or [])]
    except (tg.TargetError, ValueError) as e:
        typer.echo(f"not published: {e}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"target: {tgt.describe()}")
    clock = _clock(now)
    run_id, started = uuid.uuid4().hex, _clock(None)
    s = settings()
    inputs = col.Inputs(
        warehouse=_project_path(db) if db is not None else s.path("warehouse"),
        store=_project_path(store) if store is not None else s.path("predictions"),
        dataset=_project_path(dataset),
        evaluation_csv=_project_path(evaluation_csv),
        season=s.current_season,
        now=clock,
    )

    def fail(message: str, step: str) -> None:
        text = tg.redact(message, tgt.url)
        typer.echo(f"not published: {text}", err=True)
        if not dry_run:
            ok = wr.record_failure_at(
                tgt, run_id=run_id, started=started, error=text, notes={"step": step}
            )
            typer.echo(
                "the failed run was recorded in pipeline_runs"
                if ok
                else "(the failure could not be recorded in pipeline_runs)",
                err=True,
            )
        raise typer.Exit(code=1)

    try:
        data = col.collect(inputs)
    except (col.PublishInputError, FileNotFoundError, ValueError) as e:
        fail(str(e), "collect")
    problems = col.validate(data)
    if problems:
        fail("validation failed: " + "; ".join(problems), "validate")
    try:
        result = wr.publish(
            tgt, data, dry_run=dry_run, allow_incomplete=allow_incomplete, replace_live=weeks,
            run_id=run_id, started=started,
        )  # fmt: skip
    except wr.PublishError as e:
        typer.echo(f"not published (rolled back): {e}", err=True)
        if not dry_run:
            typer.echo(
                "the failed run was recorded in pipeline_runs"
                if e.recorded
                else "(the failure could not be recorded in pipeline_runs)",
                err=True,
            )
        raise typer.Exit(code=1) from None
    for line in wr.summary_lines(result, tgt)[1:]:
        typer.echo(line)
    if result.skipped:
        raise typer.Exit(code=wr.EXIT_SKIPPED_INCOMPLETE)


@app.command("migrate-local")
def migrate_local() -> None:
    """Apply web/drizzle's migrations to the LOCAL database (TWM_LOCAL_DATABASE_URL, default
    the docker-compose.yml container). Refuses any other host: the public database is
    migrated with `npm --prefix web run db:migrate` and the owner's connection
    (docs/deploy.md)."""
    import psycopg

    from twm.publish import migrate as mg
    from twm.publish import target as tg

    try:
        tgt = tg.resolve("local")
    except tg.TargetError as e:
        typer.echo(f"not migrated: {e}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"target: {tgt.describe()}")
    try:
        with psycopg.connect(tgt.url, autocommit=True, connect_timeout=10) as conn:
            applied = mg.apply_migrations(conn)
    except psycopg.Error as e:
        typer.echo(f"not migrated: {tg.redact(str(e), tgt.url)}", err=True)
        raise typer.Exit(code=1) from None
    if applied:
        typer.echo("applied: " + ", ".join(applied))
    else:
        typer.echo("up to date: no migration to apply")


@app.command()
def glossary(
    name: str | None = typer.Argument(None, help="One term, e.g. `twm glossary wopr`."),
    write: bool = typer.Option(False, "--write", help="Regenerate docs/glossary.md."),
) -> None:
    """Explain a metric or feature in plain English (the feature/metric registry)."""
    from twm import registry
    from twm.config import ROOT

    if write:
        out = ROOT / "docs" / "glossary.md"
        out.write_text(registry.glossary_markdown())
        typer.echo(f"wrote {out} ({len(registry.REGISTRY)} entries)")
        return
    if name is None:
        for e in sorted(registry.REGISTRY.values(), key=lambda e: (e.kind, e.name)):
            flag = " (planned, " + e.step + ")" if e.status == "planned" else ""
            typer.echo(f"{e.kind:10s}  {e.name:24s}  {e.title}{flag}")
        return
    try:
        e = registry.get(name)
    except KeyError as err:
        typer.echo(str(err.args[0]), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"{e.title} [{e.kind}, {e.unit}]")
    typer.echo(f"  {e.explanation}")
    typer.echo(f"  formula: {e.formula}")
    if e.source:
        typer.echo(f"  source:  {e.source}")
    if e.verified:
        typer.echo(f"  checked: {e.verified}")
    if e.status == "planned":
        typer.echo(f"  planned: built in step {e.step}")


if __name__ == "__main__":
    app()
