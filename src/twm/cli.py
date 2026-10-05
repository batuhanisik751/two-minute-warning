"""`twm` command-line interface. Subcommands are added step by step."""

from __future__ import annotations

from pathlib import Path

import typer

from twm import __version__
from twm.modules.board import production_cli as _board_pins  # noqa: F401 (I2c-a commands)
from twm.modules.board.cli import board_app
from twm.modules.decisions import production_cli as _decisions_pins  # noqa: F401 (P3 commands)
from twm.modules.decisions.cli import decisions_app
from twm.modules.hot_seat import production_cli as _hot_seat_pins  # noqa: F401 (H4a commands)
from twm.modules.hot_seat.cli import hotseat_app
from twm.modules.questionable.cli import questionable_app
from twm.modules.streamer.cli import streamer_app
from twm.offseason_cli import offseason_app
from twm.timemachine.cli import timemachine_app

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
app.add_typer(streamer_app, name="streamer")  # S1b: K and D/ST (src/twm/modules/streamer/cli.py)
app.add_typer(decisions_app, name="decisions")  # G1: own WP (src/twm/modules/decisions/cli.py)
app.add_typer(hotseat_app, name="hotseat")  # H1: candidates + label check (modules/hot_seat/cli.py)
app.add_typer(board_app, name="board")  # I1b: Cliff & Breakout (src/twm/modules/board/cli.py)
app.add_typer(timemachine_app, name="timemachine")  # I3a: reproducibility check
app.add_typer(offseason_app, name="offseason")  # I6b: the yearly routine (docs/offseason.md)
app.add_typer(questionable_app, name="questionable")  # feature #1 (docs/questionable.md)


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


def _pinned_production(dataset, season: int, store: Path):
    """The owner-approved Waiver Radar model for ``season`` (:func:`twm.pins.load_pinned`),
    recorded in the store's model_versions (so its lists can be published) and compared with
    today's training data (a difference is a warning: the approved model is used anyway)."""
    from twm import pins
    from twm import predictions as pr
    from twm.modules.waiver_radar import production as prod

    pm, pin = pins.load_pinned(prod.MODULE, season)
    note = pins.identity_note(pm, dataset)
    if note:
        typer.echo(f"WARNING: {note}", err=True)
    registered = pr.register_version(store, prod.version_frame(pm))
    return prod.Ensured(pm, True, pin.path(), registered)


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
    pinned: bool = typer.Option(
        False,
        "--pinned",
        help="Score with the owner-approved model of config/production_models.yaml (checked "
        "file and version; refused otherwise) instead of the store's current one; never "
        "trains. The scheduled job always uses this.",
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
        frame = pl.read_parquet(source)
        if pinned:
            if retrain:
                raise typer.BadParameter("--pinned never trains: drop --retrain")
            ens = _pinned_production(frame, chosen_season, store_path)
        else:
            ens = prod.ensure_production(
                frame, chosen_season, store=store_path, root=models_root, retrain=retrain
            )
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
    how = "the approved model" if pinned else ("reused" if ens.reused else "trained now")
    typer.echo(
        f"model {m.model_version} ({how}; trained on "
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
# Regression Watch (phase D): `twm regression player`, `xfp-report`, `stability`
# --------------------------------------------------------------------------------------

regression_app = typer.Typer(
    help="Regression Watch: fantasy points vs expected points (xFP) and points over expected "
    "(FPOE), with and without garbage time (docs/regression_watch.md)."
)
app.add_typer(regression_app, name="regression")


def _regression_as_of(path: Path, as_of: str | None):
    """``--as-of`` (a season-week: its official Tuesday as-of; or a zoned timestamp) or now,
    as (aware UTC moment, the season it points at)."""
    from datetime import UTC, datetime

    from twm.asof import AsOfParseError, parse_as_of, weekly_as_of
    from twm.config import settings

    if as_of is None:
        return datetime.now(UTC), settings().current_season
    try:
        target = parse_as_of(as_of)
    except AsOfParseError as e:
        raise typer.BadParameter(str(e)) from e
    if isinstance(target, tuple):
        season, week = target
        try:
            return weekly_as_of(path, season, week), season
        except LookupError as e:
            raise typer.BadParameter(str(e)) from e
    # a timestamp: the NFL season it falls in (seasons start in September)
    return target, target.year if target.month >= 3 else target.year - 1


def _find_player(view, text: str, season: int) -> tuple[str, str]:
    """(gsis_id, name) of a gsis id or a name (exact, then contained; case-insensitive) among
    the players who exist at the view's as-of; several matches prefer those with a game in
    ``season``. typer.Exit(1) with the candidates when it stays ambiguous."""
    import re

    if re.fullmatch(r"\d{2}-\d{7}", text.strip()):
        row = view.sql(
            "SELECT gsis_id, display_name FROM dim_player WHERE gsis_id = ?", [text.strip()]
        )
        if row.height:
            return row.item(0, 0), row.item(0, 1) or text.strip()
        return text.strip(), text.strip()
    for cond in ("lower(display_name) = lower(?)", "contains(lower(display_name), lower(?))"):
        found = view.sql(
            f"SELECT p.gsis_id, p.display_name, count(w.player_id) AS games "
            f"FROM dim_player p LEFT JOIN fact_player_week w ON w.player_id = p.gsis_id "
            f"AND w.season = ? AND w.season_type = 'REG' WHERE {cond} "
            f"GROUP BY 1, 2 ORDER BY games DESC, p.gsis_id",
            [season, text.strip()],
        )
        if found.height == 1 or (found.height > 1 and found.item(1, 2) == 0 < found.item(0, 2)):
            return found.item(0, 0), found.item(0, 1)
        if found.height > 1:
            typer.echo(f"{text!r} matches {found.height} players; use a gsis_id:", err=True)
            for gid, name, games in found.head(10).iter_rows():
                typer.echo(f"  {gid}  {name}  ({games} games in {season})", err=True)
            raise typer.Exit(code=1)
    typer.echo(f"no player named {text!r} (try a gsis_id such as 00-0036358)", err=True)
    raise typer.Exit(code=1)


@regression_app.command("player")
def regression_player(
    player: str = typer.Argument(..., help="A name (e.g. 'CeeDee Lamb') or a gsis_id."),
    season: int | None = typer.Option(None, "--season", help="Season (default: the as-of's)."),
    as_of: str | None = typer.Option(
        None,
        "--as-of",
        help="2026-W3 (that week's Tuesday as-of) or a zoned timestamp (default: now): only "
        "games public then are shown.",
    ),
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
) -> None:
    """A player's weekly fantasy points, xFP and FPOE, with and without garbage time."""
    import duckdb
    import polars as pl

    from twm.asof import AsOfView, WarehouseTooOldError
    from twm.modules.regression_watch.player_week import FIRST_SEASON, player_games_for

    path = _warehouse_or_exit(db)
    when, as_of_season = _regression_as_of(path, as_of)
    season = season if season is not None else as_of_season
    if season < FIRST_SEASON:
        raise typer.BadParameter(f"--season: expected points start in {FIRST_SEASON}")
    try:
        with AsOfView(path, when) as view:
            gsis_id, name = _find_player(view, player, season)
            frame = player_games_for(view, season)
    except (LookupError, ValueError, WarehouseTooOldError, duckdb.Error) as e:
        typer.echo(f"cannot build the player's weeks: {e}", err=True)
        raise typer.Exit(code=1) from e
    rows = frame.filter(pl.col("gsis_id") == gsis_id).sort("week")
    typer.echo(f"{name} ({gsis_id}), {season} regular season, as of {when:%Y-%m-%d %H:%M} UTC")
    if rows.height == 0:
        typer.echo("no game public at that time (a QB/RB/WR/TE game of that season)")
        return
    shown = rows.select(
        "week", "team", "position",
        pl.col("fantasy_points").alias("points"), "xfp", "fpoe",
        pl.col("points_ng").alias("points_ng"), "xfp_ng", "fpoe_ng",
        pl.col("points_garbage").alias("garbage_pts"),
        pl.col("n_opportunities").alias("opps"),
        pl.col("n_opportunities_garbage").alias("opps_garbage"),
    )  # fmt: skip
    cfg = dict(tbl_rows=-1, tbl_cols=-1, tbl_width_chars=200, float_precision=1,
               tbl_hide_dataframe_shape=True, tbl_hide_column_data_types=True)  # fmt: skip
    with pl.Config(**cfg):
        typer.echo(str(shown))
    games = rows.height

    def per_game(col: str) -> str:
        values = rows.get_column(col).drop_nulls()
        return "-" if values.len() == 0 else f"{values.sum() / values.len():.1f}"

    typer.echo(
        f"per game ({games} games): points {per_game('fantasy_points')}, xFP {per_game('xfp')}, "
        f"FPOE {per_game('fpoe')}; without garbage time: points {per_game('points_ng')}, "
        f"xFP {per_game('xfp_ng')}, FPOE {per_game('fpoe_ng')}"
    )
    typer.echo(
        "garbage time = the game was decided (win probability under 5% or over 95%, except a "
        "one-score game's last two minutes of a half); `twm glossary fpoe_ng` explains the "
        "columns"
    )


@regression_app.command("xfp-report")
def regression_xfp_report(
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
    out: Path = typer.Option(
        Path("reports/regression_watch/xfp.md"),
        "--out",
        help="Markdown file to write (relative paths are under the project root); a CSV with "
        "the same name is written next to it.",
    ),
    start: int = typer.Option(2006, "--start", help="First season (ffopportunity starts 2006)."),
    end: int | None = typer.Option(None, "--end", help="Last season (default: current)."),
) -> None:
    """Write the xFP report: the per-play tables' joins, the points and xFP reconciliations,
    garbage-time shares by position and coverage by season (markdown + CSV)."""
    import duckdb

    from twm.asof import WarehouseTooOldError
    from twm.config import ROOT, settings
    from twm.modules.regression_watch.report import build_xfp_report, write_xfp_report

    path = _warehouse_or_exit(db)
    last = end if end is not None else settings().current_season
    if start > last:
        raise typer.BadParameter(f"--start {start} is after --end {last}")
    try:
        report = build_xfp_report(path, list(range(start, last + 1)))
    except (LookupError, ValueError, WarehouseTooOldError, duckdb.Error) as e:
        typer.echo(f"cannot build the report: {e}", err=True)
        raise typer.Exit(code=1) from e
    target = out if out.is_absolute() else ROOT / out
    csv_path = write_xfp_report(report, target)
    for line in report.summary:
        typer.echo(line)
    typer.echo(f"wrote {target}")
    typer.echo(f"wrote {csv_path}")


XFP_OPTION_HELP = (
    "Expected points: 'own' (the walk-forward xFP, step H6-b2) or 'ffopportunity'; default: "
    "the source pinned in config/production_models.yaml."
)


@regression_app.command("stability")
def regression_stability(
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
    out: Path = typer.Option(
        Path("reports/regression_watch/stability.md"),
        "--out",
        help="Markdown file to write (relative paths are under the project root); a CSV with "
        "the same name is written next to it.",
    ),
    start: int = typer.Option(2009, "--start", help="First season (the study starts in 2009)."),
    end: int | None = typer.Option(
        None, "--end", help="Last season (default: the last complete one, current - 1)."
    ),
    n_boot: int = typer.Option(1000, "--boot", help="Bootstrap resamples for the intervals."),
    xfp: str | None = typer.Option(None, "--xfp", help=XFP_OPTION_HELP),
) -> None:
    """Write the stability study (step D2): split-half correlations of xFP, FPOE, points and
    the parts of efficiency by position, and the FPOE shrinkage table (markdown + CSV), with
    the pinned xFP source unless --xfp names one."""
    import duckdb

    from twm.asof import WarehouseTooOldError
    from twm.config import ROOT, settings
    from twm.modules.regression_watch.stability_report import (
        build_stability_report,
        write_stability_report,
    )

    path = _warehouse_or_exit(db)
    last = end if end is not None else settings().current_season - 1
    if start > last:
        raise typer.BadParameter(f"--start {start} is after --end {last}")
    source = _regression_xfp(xfp)
    try:
        report = build_stability_report(
            path, list(range(start, last + 1)), n_boot=n_boot,
            xfp=_regression_history(path, last, source), xfp_source=source,
        )  # fmt: skip
    except (LookupError, ValueError, WarehouseTooOldError, duckdb.Error) as e:
        typer.echo(f"cannot build the report: {e}", err=True)
        raise typer.Exit(code=1) from e
    target = out if out.is_absolute() else ROOT / out
    csv_path = write_stability_report(report, target)
    for line in report.summary:
        typer.echo(line)
    typer.echo(f"wrote {target}")
    typer.echo(f"wrote {csv_path}")


@regression_app.command("backtest")
def regression_backtest(
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
    out: Path = typer.Option(
        Path("reports/regression_watch/backtest.md"),
        "--out",
        help="Markdown file to write (relative paths are under the project root); a CSV with "
        "the same name is written next to it.",
    ),
    end: int | None = typer.Option(
        None, "--end", help="Last test season (default: the last complete one, current - 1)."
    ),
    n_boot: int = typer.Option(2000, "--boot", help="Bootstrap resamples for the intervals."),
    xfp: str | None = typer.Option(None, "--xfp", help=XFP_OPTION_HELP),
) -> None:
    """Walk-forward backtest of the rest-of-season projection and the Sell-high / Buy-low /
    Legit tags (step D3): MAE and rank correlation against season-to-date PPG and last-3 PPG,
    tag hit rates, the variant and thresholds chosen per season (markdown + CSV), with the
    pinned xFP source unless --xfp names one. Writes nothing else (no predictions store)."""
    import duckdb

    from twm.asof import WarehouseTooOldError
    from twm.config import ROOT, league, settings
    from twm.modules.regression_watch import backtest as bt
    from twm.modules.regression_watch.backtest_report import (
        build_backtest_report,
        write_backtest_report,
    )
    from twm.modules.regression_watch.stability_report import _built_at

    path = _warehouse_or_exit(db)
    last = end if end is not None else settings().current_season - 1
    if last < bt.FIRST_TEST_SEASON:
        raise typer.BadParameter(f"--end must be {bt.FIRST_TEST_SEASON} or later")
    source = _regression_xfp(xfp)
    try:
        frame, asofs = bt.load_inputs(path, last, xfp=_regression_history(path, last, source))
        result = bt.run_backtest(
            frame, asofs, league(), last_season=last, n_boot=n_boot,
            progress=lambda m: typer.echo(m, err=True),
        )  # fmt: skip
        report = build_backtest_report(result, league(), _built_at(path), source)
    except (LookupError, ValueError, WarehouseTooOldError, duckdb.Error) as e:
        typer.echo(f"cannot run the backtest: {e}", err=True)
        raise typer.Exit(code=1) from e
    target = out if out.is_absolute() else ROOT / out
    csv_path = write_backtest_report(report, target)
    for line in report.summary:
        typer.echo(line)
    typer.echo(f"wrote {target}")
    typer.echo(f"wrote {csv_path}")


@regression_app.command("own-xfp")
def regression_own_xfp(
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
    out: Path = typer.Option(
        Path("reports/regression_watch/own_xfp.md"),
        "--out",
        help="Markdown file to write (relative paths are under the project root); a CSV with "
        "the same name is written next to it.",
    ),
    end: int | None = typer.Option(
        None, "--end", help="Last season of the D2/D3 reruns (default: current - 1)."
    ),
    workers: int = typer.Option(4, "--workers", help="Processes fitting folds in parallel."),
    n_boot: int = typer.Option(2000, "--boot", help="Season-block resamples for intervals."),
) -> None:
    """Fit Regression Watch's own walk-forward xFP (one fold per season 2007 .. current, the
    current one = the live model; saved under data/regression_watch/own_xfp/, folds already
    current are reused), compare it with ffopportunity per play and per player-game, and rerun
    D2's stability study and D3's backtest with it (PROJECT_SPEC 6.3). Report only: nothing
    is pinned, stored or published."""
    import time

    import duckdb

    from twm.asof import WarehouseTooOldError
    from twm.config import ROOT, settings
    from twm.modules.regression_watch import own_xfp as ox
    from twm.modules.regression_watch.own_xfp_report import (
        build_own_xfp_report,
        recommendation,
        write_own_xfp_report,
    )
    from twm.modules.regression_watch.stability_report import _built_at

    path = _warehouse_or_exit(db)
    current = settings().current_season
    last = end if end is not None else current - 1
    out_dir = ROOT / ox.OUT_DIR
    say = lambda m: typer.echo(m, err=True)  # noqa: E731
    try:
        t0 = time.perf_counter()
        ox.run_folds(path, current, out_dir, workers=workers, progress=say)
        fit_s = time.perf_counter() - t0
        report = build_own_xfp_report(
            path, out_dir, last_fold=current, study_end=last, n_boot=n_boot, progress=say
        )
    except (LookupError, ValueError, WarehouseTooOldError, duckdb.Error) as e:
        typer.echo(f"cannot build the own xFP report: {e}", err=True)
        raise typer.Exit(code=1) from e
    target = out if out.is_absolute() else ROOT / out
    csv_path = write_own_xfp_report(report, target, _built_at(path))
    switch, why = recommendation(report)
    for line in why:
        typer.echo(line)
    typer.echo("recommendation: " + ("switch to own xFP" if switch else "keep ffopportunity"))
    secs = ", ".join(f"{k} {v:.0f} s" for k, v in report.seconds.items())
    typer.echo(f"runtime: folds {fit_s:.0f} s, {secs}")
    typer.echo(f"wrote {target}")
    typer.echo(f"wrote {csv_path}")


@regression_app.command("project")
def regression_project(
    season: int = typer.Argument(..., help="Season, e.g. 2026."),
    week: int = typer.Argument(..., help="Regular-season week: its official Tuesday as-of."),
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
    position: str | None = typer.Option(None, "--position", help="Only QB, RB, WR or TE."),
    top: int = typer.Option(15, "--top", help="Players shown per position (by projection)."),
    csv_out: Path | None = typer.Option(
        None, "--csv", help="Also write every universe player to this CSV file."
    ),
    xfp: str | None = typer.Option(None, "--xfp", help=XFP_OPTION_HELP),
) -> None:
    """Rest-of-season projection and Sell-high / Buy-low / Legit tags at a week's as-of (step
    D3), with the variant and thresholds the backtest would use for that season (chosen on the
    seasons before it) and the pinned xFP source unless --xfp names one. Prints a table; writes
    nothing unless --csv is given."""
    import duckdb
    import polars as pl

    from twm.asof import WarehouseTooOldError
    from twm.config import league
    from twm.modules.regression_watch import backtest as bt
    from twm.modules.regression_watch.tags import TAG_TITLES, TAGS

    path = _warehouse_or_exit(db)
    pos = position.upper() if position else None
    if pos is not None and pos not in ("QB", "RB", "WR", "TE"):
        raise typer.BadParameter(f"--position {position}: QB, RB, WR or TE")
    source = _regression_xfp(xfp)
    try:
        hist = _regression_history(path, season, source)
        wp = bt.project_week(path, season, week, league(), xfp=hist)
    except (LookupError, ValueError, WarehouseTooOldError, duckdb.Error) as e:
        typer.echo(f"cannot project: {e}", err=True)
        raise typer.Exit(code=1) from e
    ch = wp.choice
    typer.echo(f"{season} week {week}, as of {wp.as_of:%Y-%m-%d %H:%M} UTC; variant "
               f"{ch.variant.name} ({ch.variant.describe()}); X Sell-high {ch.sell.x:g}, "
               f"Buy-low {ch.buy.x:g} (chosen on {len(ch.validation_seasons)} earlier "
               "seasons)")  # fmt: skip
    table = wp.table.with_columns(
        pl.concat_str(
            [pl.when(pl.col(t)).then(pl.lit(TAG_TITLES[t])) for t in TAGS], separator=", ",
            ignore_nulls=True,
        ).alias("tags")
    )  # fmt: skip
    for p in ("QB", "RB", "WR", "TE"):
        if pos is not None and p != pos:
            continue
        sub = table.filter(pl.col("position") == p).head(top)
        typer.echo(f"\n{p}  name  team  games  PPG  xFP/g  FPOE/g  shrink  projection  tags")
        for r in sub.iter_rows(named=True):
            typer.echo(f"  {r['name'] or r['gsis_id']}  {r['team']}  {r['games']}  "
                       f"{r['ppg']:.1f}  {r['xfp_pg']:.1f}  {r['fpoe_pg']:+.1f}  "
                       f"{r['r_' + ch.variant.metric]:.2f}  {r['ppg_ros']:.1f}  "
                       f"{r['tags']}")  # fmt: skip
    if csv_out is not None:
        keep = ["season", "week", "gsis_id", "name", "team", "position", "games", "g_opp", "ppg",
                "xfp_pg", "fpoe_pg", "last3_ppg", "ppg_rank", "xfp_rank", "ppg_ros",
                "fpoe_top", "fpoe_bottom", *TAGS, "tags"]  # fmt: skip
        table.select(keep).write_csv(csv_out)
        typer.echo(f"wrote {csv_out}")


def _regression_approved(season: int):
    """(the approved own xFP live models or None, the approved Regression Watch parameters):
    every file's sha256 checked before it is read; nothing is fit."""
    from twm.modules.regression_watch import production as rprod

    live, params, _ = rprod.load_pinned_xfp(season)
    return live, params


def _regression_misses():
    """The approved frozen backtest's graded misses (sha256-checked): the 80% ranges of the
    weekly list (feature #4, :mod:`twm.modules.regression_watch.ranges`)."""
    from twm.modules.regression_watch import ranges

    return ranges.pinned_misses()


def _regression_xfp(xfp: str | None) -> str:
    """--xfp, else the pinned source (exit 1 when the pin names none: never a default)."""
    from twm import pins
    from twm.modules.regression_watch import xfp_source as xs

    if xfp is not None:
        try:
            return xs.check(xfp)
        except ValueError as e:
            raise typer.BadParameter(str(e)) from e
    try:
        return xs.pinned_source()
    except pins.PinError as e:
        typer.echo(f"no xFP source: {e} (or pass --xfp own|ffopportunity)", err=True)
        raise typer.Exit(code=1) from e


def _regression_history(path: Path, last: int, source: str):
    """The expected points of the seasons up to ``last`` for ``source`` (own: the walk-forward
    folds, fitted on this Mac when not current; ffopportunity: None)."""
    from twm.modules.regression_watch import xfp_source as xs

    return xs.history(path, last, source, progress=lambda m: typer.echo(m, err=True))


@regression_app.command("score")
def regression_score(
    season: int | None = typer.Option(None, "--season", help="Season (default: current)."),
    week: int | None = typer.Option(
        None,
        "--week",
        help="Week N: the list at its Tuesday as-of (default: the latest week "
        "whose as-of has passed).",
    ),  # fmt: skip
    allow_incomplete: bool = typer.Option(
        False,
        "--allow-incomplete",
        help="Score even if some of the week's data has not arrived (the list is marked).",
    ),  # fmt: skip
    limit: int = typer.Option(5, "--limit", help="Players per tag to print."),
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
    store: Path | None = typer.Option(
        None, "--store", help="Predictions store (default: settings paths.predictions)."
    ),
    backtest_csv: Path = typer.Option(
        Path("reports/regression_watch/backtest.csv"),
        "--backtest-csv",
        help="The committed backtest (the list's notes on each tag quote it).",
    ),  # fmt: skip
    out: Path | None = typer.Option(
        None, "--out", help="Report (default: reports/regression_watch/weekly/<season>-W<nn>.md)."
    ),
    now: str | None = typer.Option(
        None,
        "--now",
        hidden=True,
        help="Pretend it is this UTC time (ISO); the list is then never stored as 'live'.",
    ),  # fmt: skip
) -> None:
    """Regression Watch's weekly list with the owner-approved frozen parameters
    (config/production_models.yaml, sha256-checked; nothing is re-estimated): checks the
    week's data (exit code 3 if it has not arrived), stores every universe player's
    rest-of-season projection, its 80% range (from the frozen backtest's misses) and tag,
    grades stored lists of finished seasons, writes the weekly report and prints the
    Sell-high and Buy-low tops (docs/regression_watch.md)."""
    import polars as pl

    from twm import pins
    from twm import predictions as pr
    from twm.asof import WarehouseTooOldError, weekly_as_of
    from twm.config import ROOT, league, settings
    from twm.modules.regression_watch import weekly as rwk

    path = _warehouse_or_exit(db)
    clock = _clock(now)
    chosen = season if season is not None else settings().current_season
    try:
        wk = week if week is not None else rwk.rw.default_week(path, chosen, clock)
        as_of = weekly_as_of(path, chosen, wk)
    except LookupError as e:
        typer.echo(f"cannot score: {e}", err=True)
        raise typer.Exit(code=1) from e
    fresh = rwk.check_freshness(rwk.freshness_inputs(path, chosen, wk), chosen, wk, as_of, clock)
    if not fresh.ok and not allow_incomplete:
        typer.echo(fresh.message(), err=True)
        raise typer.Exit(code=rwk.EXIT_NOT_READY)
    store_path = _project_path(store if store is not None else pr.default_path())
    try:
        live, params = _regression_approved(chosen)
        misses = _regression_misses()
        run = rwk.run_week(path, chosen, wk, params=params, league=league(), now=clock,
                           allow_incomplete=allow_incomplete, real_clock=now is None,
                           live=live, misses=misses)  # fmt: skip
    except rwk.rw.NotReadyError as e:  # pragma: no cover - checked above; data changed meanwhile
        typer.echo(str(e), err=True)
        raise typer.Exit(code=rwk.EXIT_NOT_READY) from e
    except (pins.PinError, WarehouseTooOldError, LookupError, ValueError) as e:
        typer.echo(f"cannot score: {e}", err=True)
        raise typer.Exit(code=1) from e
    if run.table.height == 0:
        typer.echo(f"{chosen} week {wk}: no player has 3 games yet at the as-of, so there is no "
                   "Regression Watch list this week (nothing stored)")  # fmt: skip
        return
    try:
        counts = rwk.store_week(run, store_path, created_at=clock.replace(tzinfo=None))
    except pr.LiveWeekError as e:
        typer.echo(f"not stored: {e}", err=True)
        raise typer.Exit(code=1) from e
    graded = rwk.update_outcomes(path, store_path, clock)
    command = f"uv run twm regression score --season {chosen} --week {wk}" + (
        " --allow-incomplete" if allow_incomplete else "")  # fmt: skip
    notes = rwk.tag_notes(_project_path(backtest_csv), chosen)
    text = rwk.build_report(run, league(), generated=f"Generated at {clock:%Y-%m-%d %H:%M} UTC.",
                            command=command, notes=notes)  # fmt: skip
    target = _project_path(out) if out is not None else rwk.report_path(chosen, wk)
    rwk.write_report(text, target)
    typer.echo(f"{chosen} week {wk}, as-of {as_of:%a %Y-%m-%d %H:%M} UTC: stored as '{run.kind}'"
               + (" (INCOMPLETE DATA)" if run.incomplete else ""))  # fmt: skip
    typer.echo(f"approved parameters: {params.describe()}")
    if live is not None:
        span = f"{live.train_seasons[0]}-{live.train_seasons[-1]}"
        typer.echo(f"own xFP: the pinned {live.fold} models {live.version} (trained on {span}; "
                   "nothing fitted)")  # fmt: skip
    t = run.table
    for tag, gap in (("sell_high", pl.col("ppg") - pl.col("ppg_ros")),
                     ("buy_low", pl.col("ppg_ros") - pl.col("ppg"))):  # fmt: skip
        top = t.filter(pl.col(tag)).with_columns(gap.alias("_gap"))
        top = top.sort(["_gap", "gsis_id"], descending=[True, False]).head(limit)
        typer.echo(f"{tag.replace('_', '-').capitalize()} ({t.filter(pl.col(tag)).height}):")
        for r in top.iter_rows(named=True):
            typer.echo(f"  {r['name'] or r['gsis_id']} {r['position']} {r['team']}: PPG "
                       f"{r['ppg']:.1f}, xFP/g {r['xfp_pg']:.1f}, FPOE/g {r['fpoe_pg']:+.1f}, "
                       f"projection {r['ppg_ros']:.1f}")  # fmt: skip
    typer.echo(f"stored {counts['predictions']:,} predictions in {_display_path(store_path, ROOT)}"
               + (f"; {graded:,} final outcomes" if graded else ""))  # fmt: skip
    typer.echo(f"wrote {_display_path(target, ROOT)}")


@regression_app.command("pin")
def regression_pin(
    season: int | None = typer.Option(None, "--season", help="Season (default: current)."),
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
    backtest_csv: Path = typer.Option(
        Path("reports/regression_watch/backtest.csv"),
        "--backtest-csv",
        help="The committed backtest the parameters' record must reproduce.",
    ),  # fmt: skip
    xfp: str | None = typer.Option(None, "--xfp", help=XFP_OPTION_HELP),
) -> None:
    """Approve the season's Regression Watch parameters: recompute D3's choice for it (variant,
    X, shrinkage estimated on the seasons before it) from the warehouse with the xFP source
    (--xfp, else the pinned one), refuse unless the same run reproduces the committed
    backtest's rows of the season before, then write
    artifacts/production_models/regression_watch/<version>.json and pin it in
    config/production_models.yaml (the other pins keep theirs). With the own xFP, the
    season's live models (the fold trained on 2006 .. season-1, about 45 s) are written next
    to it and pinned file by file with their sha256. Review, then commit."""
    from twm import pins
    from twm.config import ROOT, league, settings
    from twm.modules.regression_watch import production as rprod

    path = _warehouse_or_exit(db)
    chosen = season if season is not None else settings().current_season
    source = _regression_xfp(xfp)
    say = lambda m: typer.echo(m, err=True)  # noqa: E731
    try:
        params = rprod.build_params(path, chosen, league(), xfp_source=source, progress=say)
        live = None
        if source == "own":
            from twm.modules.regression_watch import own_xfp as ox

            live, _ = ox.fit_live(path, chosen, progress=say)
        pin = rprod.approve(params, csv_path=_project_path(backtest_csv), live=live)
    except (rprod.RegressionProductionError, LookupError, ValueError) as e:
        typer.echo(f"cannot approve: {e}", err=True)
        raise typer.Exit(code=1) from e
    typer.echo(f"approved {params.describe()}")
    if pin.xfp is not None and pin.xfp.source == "own":
        for name, m in pin.xfp.models.items():
            typer.echo(f"wrote {m.file} ({name}, sha256 {m.sha256[:12]}...)")
    typer.echo(f"wrote {pin.file} (sha256 {pin.sha256[:12]}...) and pinned it in "
               f"{_display_path(pins.default_pin_path(), ROOT)}")  # fmt: skip
    _freeze_regression(path, chosen, backtest_csv)  # P2: its frozen backtest lists too


def _freeze_regression(path: Path, season: int, backtest_csv: Path,
                       player_xfp_only: bool = False) -> None:  # fmt: skip
    from twm import pins
    from twm.config import ROOT, league
    from twm.modules.regression_watch import frozen as fz
    from twm.modules.regression_watch import production as rprod

    say = lambda m: typer.echo(m, err=True)  # noqa: E731
    try:
        if player_xfp_only:  # PXFP: only the player pages' own-xFP history
            pin = fz.freeze_player_xfp(path, season, progress=say)
        else:
            pin = fz.freeze(path, season, league(), csv_path=_project_path(backtest_csv),
                            progress=say)  # fmt: skip
    except (rprod.RegressionProductionError, pins.PinError, LookupError, ValueError) as e:
        typer.echo(f"cannot freeze the backtest lists: {e}", err=True)
        raise typer.Exit(code=1) from e
    tables = [] if player_xfp_only else list(fz.TABLES)
    for table in tables + [t for t in (fz.PLAYER_XFP,) if t in pin.backtest]:
        f = pin.backtest[table]
        size = fz.snapshot_dir(pin.model_version, ROOT).joinpath(f"{table}.parquet").stat()
        typer.echo(f"wrote {f.file} ({f.rows:,} rows, {size.st_size / 1e3:,.0f} KB, sha256 "
                   f"{f.sha256[:12]}...)")  # fmt: skip
    typer.echo(f"pinned them in {_display_path(pins.default_pin_path(), ROOT)} (the parameters "
               "file and the other pins are unchanged); review and commit")  # fmt: skip


@regression_app.command("freeze")
def regression_freeze(
    season: int | None = typer.Option(None, "--season", help="Season (default: current)."),
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
    backtest_csv: Path = typer.Option(
        Path("reports/regression_watch/backtest.csv"),
        "--backtest-csv",
        help="The committed backtest the frozen lists must reproduce.",
    ),  # fmt: skip
    player_xfp: bool = typer.Option(
        False, "--player-xfp", help="Freeze only the player pages' own-xFP history (PXFP)."
    ),
) -> None:
    """Freeze the time machine's backtest lists of the approved parameters (P2): the headline
    as-of weeks of every D3 test season, made from the warehouse (it must reach back to 2006:
    run this on the owner's Mac, never in the scheduled job), refused unless every season's
    choice, the headline MAE and the tag hit rates equal the committed backtest CSV and the
    parameters' record; written under artifacts/production_models/regression_watch/ and pinned
    with their sha256 (`twm regression pin` does this too). With own-xFP parameters also the
    player pages' own-xFP history (step PXFP: every player-game from the first snap-count
    season to the season before, each season from its walk-forward fold); ``--player-xfp``
    freezes only that. Review, then commit."""
    from twm.config import settings

    chosen = season if season is not None else settings().current_season
    _freeze_regression(_warehouse_or_exit(db), chosen, backtest_csv, player_xfp)


@regression_app.command("outcomes")
def regression_outcomes(
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
    store: Path | None = typer.Option(
        None, "--store", help="Predictions store (default: settings paths.predictions)."
    ),
    now: str | None = typer.Option(None, "--now", hidden=True, help="Pretend it is this time."),
) -> None:
    """Grade the stored Regression Watch lists of every season whose regular season is over:
    each row's actual rest-of-season points per game (outcomes.y_value, 'final'). `twm
    regression score` does this too after storing a list."""
    from twm import predictions as pr
    from twm.config import ROOT
    from twm.modules.regression_watch import weekly as rwk

    path = _warehouse_or_exit(db)
    store_path = _project_path(store if store is not None else pr.default_path())
    n = rwk.update_outcomes(path, store_path, _clock(now))
    typer.echo(f"wrote {n:,} final outcomes into {_display_path(store_path, ROOT)}")


def check_regression_pin(
    season: int, backtest_csv: Path = Path("reports/regression_watch/backtest.csv")
) -> None:
    """`twm model check` for Regression Watch (exit 1 on any problem): the pin present, the
    parameters file as pinned (sha256 before it is read, version = the hash of its content,
    season), its xFP source as pinned (own: every live model file's sha256 before it is
    opened, the fold of the season, each file the component it names), and its record of
    the last backtest season reproduces the committed backtest CSV's rows (the variant,
    validation MAE and both X); the frozen backtest lists (P2) as
    pinned, every season's choice, the headline MAE and tag hit rates equal to the CSV's, and
    the parameters' record equal to the lists' last season. Changes nothing."""
    from twm import pins
    from twm.modules.regression_watch import production as rprod

    try:
        live, params, pin = rprod.load_pinned_xfp(season)
    except pins.PinError as e:
        typer.echo(f"not usable: {e}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"{pin.module} {season}: approved {pin.model} {pin.model_version} ({pin.file}, "
               f"sha256 {pin.sha256[:12]}..., approved {pin.approved or '?'})")  # fmt: skip
    typer.echo(f"  {params.describe()}")
    if live is not None:
        typer.echo(f"  own xFP live models {live.version}: {len(live.models)} files, sha256 "
                   f"checked before opening, trained on {pin.xfp.train_seasons}")  # fmt: skip
    else:
        typer.echo("  xFP source: ffopportunity (nflverse's per-play expectations)")
    problems = rprod.record_mismatches(params, _project_path(backtest_csv))
    if problems:
        typer.echo(
            f"not usable: the approved parameters disagree with {backtest_csv} in "
            f"{len(problems)} places, e.g. " + "; ".join(problems[:3]),
            err=True,
        )
        raise typer.Exit(code=1)
    rec = params.record
    typer.echo(f"  matches {backtest_csv} (season {rec['season']}: variant {rec['variant']}, "
               f"X {rec['sell_high']['x']:g} / {rec['buy_low']['x']:g})")  # fmt: skip
    # P2: the frozen backtest lists (the time machine): sha256 before reading, then the report
    from twm.config import league
    from twm.modules.regression_watch import frozen as fz

    try:
        frames = fz.load_snapshot(pin)
    except pins.PinError as e:
        typer.echo(f"not usable: {e}", err=True)
        raise typer.Exit(code=1) from None
    problems = fz.snapshot_mismatches(frames, _project_path(backtest_csv), league(), params)
    if problems:
        typer.echo(
            f"not usable: the frozen backtest lists disagree with {backtest_csv} or the "
            f"parameters in {len(problems)} places, e.g. " + "; ".join(problems[:3]),
            err=True,
        )
        raise typer.Exit(code=1)
    rows = ", ".join(f"{pin.backtest[t].rows:,} {t}" for t in fz.TABLES)
    typer.echo(f"  frozen backtest lists {pin.backtest_seasons}: {rows} (sha256 and rows "
               f"checked); every season's choice, the headline MAE and the tag hit rates match "
               f"{backtest_csv}")  # fmt: skip
    _check_player_xfp(pin)


def _check_player_xfp(pin) -> None:
    """PXFP: the player pages' own-xFP history frozen in the pin (required; sha256 before it
    is read, rows, one row per player-game, every season before the pin's); where the folds
    and the warehouse are on disk (the owner's Mac), re-built from the saved folds (nothing is
    fit) and compared within 1e-9."""
    from twm import pins
    from twm.config import settings
    from twm.modules.regression_watch import frozen as fz

    try:
        frozen = fz.load_player_xfp(pin)
    except pins.PinError as e:
        typer.echo(f"not usable: {e}", err=True)
        raise typer.Exit(code=1) from None
    seasons = frozen.get_column("season")
    head = (f"  player pages' own xFP {seasons.min()}-{seasons.max()}: {frozen.height:,} "
            "player-games (sha256 and rows checked)")  # fmt: skip
    rebuilt = fz.rebuild_player_xfp(settings().path("warehouse"), pin.season)
    if rebuilt is None:
        typer.echo(f"{head}; not re-built here (no own xFP folds or warehouse on disk)")
        return
    problems = fz.player_xfp_mismatches(frozen, rebuilt)
    if problems:
        typer.echo(f"not usable: the frozen own-xFP player weeks disagree with the folds on "
                   f"disk in {len(problems)} places, e.g. " + "; ".join(problems[:3]),
                   err=True)  # fmt: skip
        raise typer.Exit(code=1)
    typer.echo(f"{head}; re-built from the folds on disk: equal within "
               f"{fz.PLAYER_XFP_TOLERANCE:g}")  # fmt: skip


# --------------------------------------------------------------------------------------
# The spec's generic entry points (PROJECT_SPEC 9): `twm train|backtest <module>`, `twm score`
# --------------------------------------------------------------------------------------

# Modules with a train/backtest/score implementation (the `twm radar ...` commands stay).
MODULES = {"waiver_radar": "Waiver Radar (`twm radar ...`)"}
# Built modules without a trained model (D4a): `twm backtest` and `twm score` run them; `twm
# train` and the model-file commands refuse them.
PARAMS_MODULES = {
    "regression_watch": "Regression Watch (`twm regression ...`) has no trained model: its "
    "frozen parameters are approved with `twm regression pin`",
}
# Modules of the spec that are not built yet, with the phase that builds them (spec 11).
PLANNED_MODULES = {"decisions": "phase G", "hot_seat": "phase H", "board": "phase I"}


def _module_or_exit(name: str, *, allow: tuple[str, ...] = ()) -> str:
    """The module key of ``name``; ``allow``: modules of :data:`PARAMS_MODULES` the command
    accepts too."""
    key = name.strip().lower().replace("-", "_")
    if key in MODULES or key in allow:
        return key
    built = ", ".join([*MODULES, *allow])
    if key in PARAMS_MODULES:
        raise typer.BadParameter(PARAMS_MODULES[key])
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
    module: str = typer.Argument(..., help="Module to backtest: waiver_radar, regression_watch."),
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
    out: Path | None = typer.Option(
        None, "--out", help="Markdown report to write (default: the module's reports folder)."
    ),
    db: Path | None = typer.Option(
        None, "--db", help="Warehouse file (regression_watch; default from config)."
    ),
) -> None:
    """Walk-forward backtest of a module (waiver_radar: the same as `twm radar backtest`;
    regression_watch: the same as `twm regression backtest`, which takes only --end, --out
    and --db)."""
    key = _module_or_exit(module, allow=("regression_watch",))
    if key == "regression_watch":
        extra = [o for o, v in (("--start", start), ("--model", models), ("--label", labels),
                                ("--store", store)) if v]  # fmt: skip
        if extra:
            raise typer.BadParameter(f"{', '.join(extra)} do not apply to regression_watch")
        regression_backtest(
            db=db,
            out=out or Path("reports/regression_watch/backtest.md"),
            end=end,
            n_boot=2000,
            xfp=None,
        )
        return
    radar_backtest(
        dataset=dataset, start=start, end=end, models=models, labels=labels, store=store,
        out=out or Path("reports/waiver_radar/backtest.md"),
    )  # fmt: skip


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
    pinned: bool = typer.Option(
        False, "--pinned", help="Use the owner-approved model (config/production_models.yaml)."
    ),
) -> None:
    """Score every module's list at an official as-of (waiver_radar: `twm radar score`;
    streamer: `twm streamer score`, always with its approved K model and D/ST rule;
    regression_watch: `twm regression score`, always with its approved frozen parameters).

    A list is always made from the data as it stood at a week's official as-of (the Tuesday
    14:00 UTC after the week's games), never at an arbitrary moment: `--as-of 2026-W3` is week
    3's as-of; a timestamp picks the week whose list was current then, i.e. the latest
    regular-season as-of at or before it (Wednesday 2026-09-30 is week 3's list; a timestamp
    in the offseason is the last week of the season before). A timestamp needs a time zone.
    Exit codes as `twm radar score` (3: the week's data has not arrived)."""
    from twm.asof import AsOfParseError, parse_as_of, week_at

    # S2a: the streamer scores with its pins only; D4a: Regression Watch with its parameters
    names = modules or [*MODULES, "streamer", "regression_watch"]
    chosen = [
        "streamer" if m.strip().lower() == "streamer"
        else _module_or_exit(m, allow=("regression_watch",))
        for m in names
    ]  # fmt: skip
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
    for module in dict.fromkeys(chosen):
        if module == "regression_watch":
            regression_score(
                season=season, week=week, allow_incomplete=allow_incomplete, limit=5, db=db,
                store=store, backtest_csv=Path("reports/regression_watch/backtest.csv"),
                out=None, now=None,
            )  # fmt: skip
            continue
        if module == "streamer":
            from twm.modules.streamer.cli import streamer_score

            streamer_score(
                season=season, week=week, allow_incomplete=allow_incomplete, limit=limit, db=db,
                store=store, backtest_csv=Path("reports/streamer/backtest.csv"), out=None,
                now=None,
            )  # fmt: skip
            continue
        radar_score(
            season=season, week=week, allow_incomplete=allow_incomplete, limit=limit,
            retrain=retrain, db=db, store=store, dataset=dataset, models_dir=None,
            evaluation_csv=Path("reports/waiver_radar/evaluation.csv"), out=None, now=None,
            pinned=pinned,
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
# The owner-approved production models (step E4): `twm model pin|check|candidate`
# --------------------------------------------------------------------------------------

model_app = typer.Typer(
    help="The owner-approved production models (config/production_models.yaml, "
    "artifacts/production_models/): approve, check, train a candidate for review.",
    no_args_is_help=True,
)
app.add_typer(model_app, name="model")


def _snapshot_text(pin) -> str:
    from twm.pins import BACKTEST_TABLES

    rows = ", ".join(f"{pin.backtest[t].rows:,} {t}" for t in BACKTEST_TABLES)
    return f"backtest snapshot {pin.backtest_seasons}: {rows}"


def _check_against_evaluation(store: Path, evaluation_csv: Path) -> list[str]:
    from twm import pins

    return pins.evaluation_mismatches(store, _project_path(evaluation_csv))


@model_app.command("check")
def model_check(
    module: str = typer.Argument(
        "all",
        help="Module: waiver_radar, streamer, regression_watch, decisions, hot_seat, board, "
        "questionable, or all (every pinned module).",
    ),
    season: int | None = typer.Option(None, "--season", help="Season (default: current)."),
    evaluation_csv: Path = typer.Option(
        Path("reports/waiver_radar/evaluation.csv"),
        "--evaluation",
        help="The committed evaluation (the published track record) the backtest must match.",
    ),
) -> None:
    """Check the approved model and its backtest snapshot (exit 1 on any problem): pin
    present; model file present, sha256 and version as pinned; every backtest file present
    with its sha256 and row count; and the snapshot reproduces the committed evaluation's rows
    of the production model (precision@10 pooled, by season and position, buckets, PR-AUC,
    Brier, calibration). The streamer's pins (K model, D/ST rule) are checked the same way
    against reports/streamer/backtest.csv (S2a), and Regression Watch's frozen parameters
    (sha256, version, and its record of the last backtest season against
    reports/regression_watch/backtest.csv; D4a), and the Decision Report Card's approved
    grading and frozen history (P3: reports/decisions/{fourth_downs,clock}.csv), and the
    Hot-Seat Meter's live model and frozen backtest (H4a: reports/hot_seat/), and the Cliff
    board's two models and frozen backtest (I2c-a: reports/board/preseason_cliff.*). Changes
    nothing."""
    import tempfile

    from twm import pins
    from twm.config import settings
    from twm.modules.streamer.cli import check_pins as check_streamer_pins

    chosen = season if season is not None else settings().current_season
    if module.strip().lower() == "streamer":
        check_streamer_pins(chosen)
        return
    if module.strip().lower().replace("-", "_") == "regression_watch":
        check_regression_pin(chosen)
        return
    if module.strip().lower() == "decisions":  # P3: the approved grading and frozen history
        from twm.modules.decisions.production_cli import check_pin as check_decisions_pin

        check_decisions_pin(chosen)
        return
    if module.strip().lower().replace("-", "_") == "hot_seat":  # H4a: the live model + backtest
        from twm.modules.hot_seat.production_cli import check_pin as check_hot_seat_pin

        check_hot_seat_pin(chosen)
        return
    if module.strip().lower() == "questionable":  # feature #1: the frozen lookup table
        from twm.modules.questionable.cli import check_pin as check_questionable_pin

        check_questionable_pin(chosen)
        return
    if module.strip().lower() == "board":  # I2c-a: the board's models + frozen backtest
        from twm.modules.board.production_cli import check_pin as check_board_pin

        check_board_pin(chosen)
        return
    if module.strip().lower() == "startsit":  # feature #2: frozen start/sit odds (local only)
        from twm.modules.startsit.cli import check_pin as check_startsit_pin

        check_startsit_pin(chosen)
        return
    key = "waiver_radar" if module.strip().lower() == "all" else _module_or_exit(module)
    try:
        pm, pin = pins.load_pinned(key, chosen)
        typer.echo(f"{key} {chosen}: approved model {pm.model_version} ({pin.file}, sha256 "
                   f"{pin.sha256[:12]}..., approved {pin.approved or '?'})")  # fmt: skip
        typer.echo(f"  {pm.describe()}")
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "predictions.duckdb"
            pins.restore_backtest(pin, store)
            typer.echo(f"  {_snapshot_text(pin)} (sha256 and rows checked)")
            problems = _check_against_evaluation(store, evaluation_csv)
    except pins.PinError as e:
        typer.echo(f"not usable: {e}", err=True)
        raise typer.Exit(code=1) from None
    if problems:
        typer.echo(
            f"not usable: the approved backtest disagrees with {evaluation_csv} in "
            f"{len(problems)} places (the site would contradict its own track record), e.g. "
            + "; ".join(problems[:3]),
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(f"  matches {evaluation_csv} (the production model's rows the store determines)")
    if module.strip().lower() == "all" and any(k.startswith("streamer") for k in pins.read_pins()):
        check_streamer_pins(chosen)
    if module.strip().lower() == "all" and "regression_watch" in pins.read_pins():
        check_regression_pin(chosen)
    if module.strip().lower() == "all" and "decisions" in pins.read_pins():
        from twm.modules.decisions.production_cli import check_pin as check_decisions_pin

        check_decisions_pin(chosen)
    if module.strip().lower() == "all" and "hot_seat" in pins.read_pins():
        from twm.modules.hot_seat.production_cli import check_pin as check_hot_seat_pin

        check_hot_seat_pin(chosen)
    if module.strip().lower() == "all" and "board" in pins.read_pins():
        from twm.modules.board.production_cli import check_pin as check_board_pin

        check_board_pin(chosen)
    if module.strip().lower() == "all" and "questionable" in pins.read_pins():
        from twm.modules.questionable.cli import check_pin as check_questionable_pin

        check_questionable_pin(chosen)
    if module.strip().lower() == "all" and "startsit" in pins.read_pins():
        from twm.modules.startsit.cli import check_pin as check_startsit_pin

        check_startsit_pin(chosen)


@model_app.command("restore-backtest")
def model_restore_backtest(
    module: str = typer.Argument("waiver_radar", help="Module: waiver_radar."),
    season: int | None = typer.Option(None, "--season", help="Season (default: current)."),
    store: Path | None = typer.Option(
        None,
        "--store",
        help="Predictions store to restore into (default: settings "
        "paths.predictions; created if missing).",
    ),
    evaluation_csv: Path = typer.Option(
        Path("reports/waiver_radar/evaluation.csv"),
        "--evaluation",
        help="The committed evaluation the restored backtest must match.",
    ),
) -> None:
    """Restore the approved backtest snapshot into a predictions store (the scheduled job's
    backtest stage; no model is trained): sha256 and row counts checked first, refused when
    the store holds a different backtest, then checked against the committed evaluation
    (exit 1 on any problem)."""
    from twm import pins
    from twm import predictions as pr
    from twm.config import ROOT, settings

    key = _module_or_exit(module)
    chosen = season if season is not None else settings().current_season
    target = _project_path(store) if store is not None else pr.default_path()
    try:
        _, pin = pins.load_pinned(key, chosen)
        done = pins.restore_backtest(pin, target)
    except pins.PinError as e:
        typer.echo(f"not restored: {e}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"{done['status']}: {_snapshot_text(pin)} of {pin.model_version} -> "
               f"{_display_path(target, ROOT)}")  # fmt: skip
    problems = _check_against_evaluation(target, evaluation_csv)
    if problems:
        typer.echo(
            f"the approved backtest disagrees with {evaluation_csv} in {len(problems)} places, "
            "e.g. " + "; ".join(problems[:3]),
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(f"checked: it reproduces {evaluation_csv} (the production model's rows)")


@model_app.command("pin")
def model_pin(
    module: str = typer.Argument("waiver_radar", help="Module: waiver_radar."),
    version: str | None = typer.Option(
        None, "--version", help="A model saved under models/<module>/ (e.g. logit-5828a0...)."
    ),
    file: Path | None = typer.Option(
        None,
        "--file",
        help="A model file written by this code (e.g. from the retrain "
        "workflow's artifact), instead of --version.",
    ),
    season: int | None = typer.Option(None, "--season", help="Season (default: current)."),
    store: Path | None = typer.Option(
        None,
        "--store",
        help="The predictions store whose backtest is approved with the model "
        "(default: settings paths.predictions; opened read-only).",
    ),
    evaluation_csv: Path = typer.Option(
        Path("reports/waiver_radar/evaluation.csv"),
        "--evaluation",
        help="The committed evaluation the backtest must match (run `twm radar evaluate` "
        "first after a new backtest).",
    ),
) -> None:
    """Approve a production model together with its backtest: write the model to
    artifacts/production_models/<module>/, export the store's backtest of the production
    model (the seasons before --season) next to it as zstd Parquet, and pin all of it in
    config/production_models.yaml with sha256s and row counts. Refused when that backtest
    does not reproduce the committed evaluation. Review, then commit the files: the scheduled
    job uses only the committed pin."""
    import tempfile

    from twm import pins
    from twm import predictions as pr
    from twm.config import ROOT, settings
    from twm.modules.waiver_radar import production as prod

    key = _module_or_exit(module)
    if (version is None) == (file is None):
        raise typer.BadParameter("give exactly one of --version and --file")
    chosen = season if season is not None else settings().current_season
    source = _project_path(file) if file is not None else prod.model_path(str(version))
    store_path = _project_path(store) if store is not None else pr.default_path()
    try:
        pm = prod.load_model(source)
    except prod.ProductionModelError as e:
        typer.echo(f"not pinned: {e}", err=True)
        raise typer.Exit(code=1) from None
    if version is not None and pm.model_version != version:
        typer.echo(f"not pinned: {source} holds {pm.model_version}, not {version}", err=True)
        raise typer.Exit(code=1)
    if int(pm.season) != chosen or pm.model != prod.MODEL or pm.label != prod.LABEL:
        typer.echo(
            f"not pinned: {pm.model_version} is a {pm.model}/{pm.label} model for season "
            f"{pm.season}, not the production {prod.MODEL}/{prod.LABEL} model for {chosen}",
            err=True,
        )
        raise typer.Exit(code=1)
    problems = _check_against_evaluation(store_path, evaluation_csv)
    if problems:
        typer.echo(
            f"not pinned: the backtest in {_display_path(store_path, ROOT)} disagrees with "
            f"{evaluation_csv} in {len(problems)} places (e.g. {problems[0]}); run `uv run twm "
            "radar evaluate` on this store first, so the track record and the lists agree",
            err=True,
        )
        raise typer.Exit(code=1)
    try:
        pin = pins.approve(pm, store=store_path)
        with tempfile.TemporaryDirectory() as tmp:  # the written snapshot restores cleanly
            pins.restore_backtest(pin, Path(tmp) / "check.duckdb")
    except pins.PinError as e:
        typer.echo(f"not pinned: {e}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"pinned {key} {chosen}: {pin.model_version}")
    typer.echo(f"  model {pin.file} (sha256 {pin.sha256[:12]}...)")
    for t in pins.BACKTEST_TABLES:
        b = pin.backtest[t]
        size = b.path().stat().st_size / 1e6
        typer.echo(f"  {t}: {b.file} ({b.rows:,} rows, {size:.2f} MB, sha256 {b.sha256[:12]}...)")
    typer.echo(f"  wrote {_display_path(pins.default_pin_path(), ROOT)}")
    typer.echo("  review the files, then commit them (the scheduled job uses only the "
               "committed pin)")  # fmt: skip


@model_app.command("candidate")
def model_candidate(
    module: str = typer.Argument("waiver_radar", help="Module: waiver_radar."),
    out: Path = typer.Option(..., "--out", help="Folder for the candidate's files."),
    season: int | None = typer.Option(None, "--season", help="Season (default: current)."),
    dataset: Path = typer.Option(
        Path("data/waiver_radar/dataset.parquet"), "--dataset", help="The training dataset."
    ),
    store: Path | None = typer.Option(
        None,
        "--store",
        help="The store with the candidate's backtest (`twm radar backtest "
        "--model logit --label y_hit` just before; default: settings paths.predictions).",
    ),
    evaluation_csv: Path = typer.Option(
        Path("reports/waiver_radar/evaluation.csv"),
        "--evaluation",
        help="The committed evaluation, to say whether approving needs a new one.",
    ),
) -> None:
    """Train a candidate production model for review (the retrain workflow) and freeze the
    store's backtest with it: writes, under --out, the model file, the backtest snapshot and
    the pin laid out like the repository (artifacts/production_models/...,
    config/production_models.yaml) plus retrain_report.md comparing both with the approved
    ones. Nothing in the repository changes; to approve, copy those files into the checkout,
    run `twm model check`, review `git diff`, commit."""
    import polars as pl

    from twm import pins
    from twm import predictions as pr
    from twm.backtest.walkforward import WalkForwardError
    from twm.config import settings
    from twm.modules.waiver_radar import production as prod
    from twm.pipeline.report import candidate_report

    _module_or_exit(module)
    chosen = season if season is not None else settings().current_season
    source = _project_path(dataset)
    if not source.exists():
        typer.echo(f"dataset not found: {source}; run `twm radar dataset` first", err=True)
        raise typer.Exit(code=1)
    store_path = _project_path(store) if store is not None else pr.default_path()
    frame = pl.read_parquet(source)
    try:
        pm = prod.train_production(frame, chosen)
    except (prod.ProductionModelError, WalkForwardError, ValueError) as e:
        typer.echo(f"cannot train: {e}", err=True)
        raise typer.Exit(code=1) from e
    base = _project_path(out)
    pin_path = base / "config" / pins.PIN_FILE
    current = pins.read_pins()
    if current:
        pins.write_pins(current, pin_path)
    try:
        pin = pins.approve(pm, store=store_path, root=base, path=pin_path)
    except pins.PinError as e:
        typer.echo(f"cannot freeze the candidate's backtest: {e}", err=True)
        raise typer.Exit(code=1) from None
    try:
        old, old_pin = pins.load_pinned(prod.MODULE, chosen)
    except pins.PinError as e:
        old, old_pin, old_note = None, None, str(e)
    else:
        old_note = ""
    problems = _check_against_evaluation(store_path, evaluation_csv)
    text = candidate_report(
        pm, pin, frame, old=old, old_note=old_note, store=store_path, old_pin=old_pin,
        evaluation_problems=problems,
    )  # fmt: skip
    (base / "retrain_report.md").write_text(text)
    typer.echo(f"candidate {pm.model_version} for {chosen}: {pm.describe()}")
    typer.echo(f"  {_snapshot_text(pin)}")
    typer.echo(f"wrote {base / pin.file}, {base / pins.backtest_dir(pin.model_version, Path('.'))}"
               f", {pin_path} and {base / 'retrain_report.md'}")  # fmt: skip


# --------------------------------------------------------------------------------------
# The scheduled pipeline (step E4): `twm pipeline run|plan`
# --------------------------------------------------------------------------------------

pipeline_app = typer.Typer(
    help="The scheduled pipeline (.github/workflows/pipeline.yml): every stage in order.",
    no_args_is_help=True,
)
app.add_typer(pipeline_app, name="pipeline")


def _week_option(week: str | None) -> int | None:
    if week is None or not week.strip():
        return None
    try:
        return int(week)
    except ValueError as e:
        raise typer.BadParameter(f"--week {week!r} is not a week number") from e


@pipeline_app.command("run")
def pipeline_run(
    out: Path | None = typer.Option(
        None, "--out", help="Folder for the run's files (default: data/pipeline/<run id>)."
    ),
    trigger: str = typer.Option(
        "manual",
        "--trigger",
        help="schedule (a cron run) or manual (always runs); GitHub's "
        "event names schedule / workflow_dispatch are accepted.",
    ),
    cron: str | None = typer.Option(
        None, "--cron", help="The cron line that started a scheduled run (github.event.schedule)."
    ),
    week: str | None = typer.Option(
        None, "--week", help="Score this week instead of the one whose list is due (empty: auto)."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Publish with --dry-run (rolled back)."),
    skip_publish: bool = typer.Option(
        False, "--skip-publish", help="Run everything, publish nothing."
    ),
    publish: str = typer.Option(
        "auto",
        "--publish",
        help="auto (remote when DATABASE_URL is set in the environment, "
        "else skipped with a warning), local, remote or skip.",
    ),
    github: bool | None = typer.Option(
        None,
        "--github/--no-github",
        help="Write GitHub annotations, job summary and step "
        "outputs (default: when GITHUB_ACTIONS is set).",
    ),
    run_id: str | None = typer.Option(
        None, "--run-id", help="Id for pipeline_runs (default: a new one)."
    ),
    now: str | None = typer.Option(
        None, "--now", hidden=True, help="Pretend clock (ISO, UTC): rehearsals; never 'live'."
    ),
) -> None:
    """Run the scheduled pipeline: preflight, gate, ingest, build, plan, dataset, backtest,
    score, export, publish (docs/deploy.md 'The scheduled pipeline'). Exit codes: 0 done
    (also: publish skipped, offseason day, week not ready on an early attempt; each with a
    warning), 1 a stage failed, 3 the week's data had not arrived by the last attempt."""
    import os
    import uuid

    from twm.config import ROOT
    from twm.pipeline import runner as rn

    trig = {"workflow_dispatch": "manual", "schedule": "schedule", "manual": "manual"}.get(trigger)
    if trig is None:
        raise typer.BadParameter(f"--trigger {trigger!r}: use schedule or manual")
    rid = run_id or uuid.uuid4().hex
    folder = _project_path(out) if out is not None else ROOT / "data" / "pipeline" / rid
    keys = ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID")
    server, repo, gh_run = (os.environ.get(k, "") for k in keys)
    opts = rn.Options(
        out=folder, trigger=trig, cron=cron or None, week=_week_option(week),
        now=_clock(now) if now is not None else None, dry_run=dry_run,
        skip_publish=skip_publish, publish=publish,
        github=github if github is not None else os.environ.get("GITHUB_ACTIONS") == "true",
        run_id=rid, run_url=f"{server}/{repo}/actions/runs/{gh_run}" if gh_run else "",
    )  # fmt: skip
    result = rn.run(opts)
    typer.echo(f"run files: {folder}")
    raise typer.Exit(code=result.exit_code)


@pipeline_app.command("plan")
def pipeline_plan(
    trigger: str = typer.Option("schedule", "--trigger", help="schedule or manual."),
    cron: str | None = typer.Option(None, "--cron", help="The cron line of a scheduled run."),
    now: str | None = typer.Option(None, "--now", help="The clock (ISO, UTC; default: now)."),
) -> None:
    """Show what a run would do now (reads the cache and the warehouse; changes nothing):
    the gate, the week whose list is due and whether this attempt is the last one."""
    from twm.config import settings
    from twm.pipeline import schedule as sc

    s = settings()
    clock = _clock(now)
    season = s.current_season
    dates = sc.season_dates_from_cache(s.path("raw_cache"), season)
    g = sc.gate(clock, trigger=trigger, cron=cron, dates=dates, cfg=s.pipeline)
    typer.echo(f"{clock:%a %Y-%m-%d %H:%M} UTC, season {season}: "
               f"{'runs' if g.run else 'skipped'}: {g.reason}")  # fmt: skip
    wh = s.path("warehouse")
    if not wh.exists():
        typer.echo("no warehouse yet: the week is planned after `twm build`")
        return
    p = sc.plan_week(clock, season, sc.week_windows(wh, season), s.pipeline)
    typer.echo(p.reason + (f"; {p.attempt_text(clock)}" if p.week is not None else ""))
    typer.echo("nightly cron: " + sc.nightly_cron(s.pipeline) + "; retry crons: "
               + ", ".join(sc.retry_crons(s.pipeline)))  # fmt: skip


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
    streamer_dataset: Path = typer.Option(
        Path("data/streamer/dataset.parquet"),
        "--streamer-dataset",
        help="The K and D/ST streamer dataset (teams, outcomes, the D/ST rule's backtest).",
    ),
    modules: list[str] | None = typer.Option(
        None,
        "--module",
        hidden=True,
        help="Publish only these modules' lists (repeatable; waiver_radar always; tests).",
    ),
    now: str | None = typer.Option(None, "--now", hidden=True, help="Pretend clock (tests)."),
    allow_shrink: bool = typer.Option(
        False,
        "--allow-shrink",
        help="Publish even when a replaced table would lose more than publish.max_shrink_share "
        "(settings.yaml, 10%) of the rows the target holds (refused by default, exit code 5).",
    ),
    run_id: str | None = typer.Option(
        None, "--run-id", hidden=True, help="The pipeline_runs id (the scheduled pipeline's)."
    ),
    notes_file: Path | None = typer.Option(
        None, "--notes-file", hidden=True, help="JSON added to the pipeline_runs notes."
    ),
    result_json: Path | None = typer.Option(
        None, "--result-json", hidden=True, help="Write a machine-readable summary here."
    ),
) -> None:
    """Publish the Waiver Radar, K and D/ST streamer and Regression Watch lists, their outcomes
    and track records, the Decision Report Card (the frozen graded history and the season in
    progress graded by `twm decisions grade-pinned`), the player pages and the glossary to
    Postgres, in one transaction (docs/deploy.md). Reads the predictions store read-only
    (never writes it). Live lists already published are never changed (frozen); the decisions'
    history only when its pinned snapshot changes; everything else is replaced, except a table
    whose content is unchanged (not rewritten). Exit codes: 0 done, 1 refused or failed
    (nothing changed), 4 done but some live lists were skipped as incomplete (retry later), 5
    refused because a replaced table would shrink too much (the local inputs look missing;
    --allow-shrink)."""
    import json
    import uuid

    import duckdb
    import polars as pl

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
    run_id, started = run_id or uuid.uuid4().hex, _clock(None)
    extra: dict = {}
    if notes_file is not None:
        try:
            extra = json.loads(_project_path(notes_file).read_text())
        except (OSError, ValueError) as e:
            typer.echo(f"not published: --notes-file: {e}", err=True)
            raise typer.Exit(code=1) from None

    def write_result(payload: dict) -> None:
        if result_json is not None:
            path = _project_path(result_json)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2, default=str) + "\n")

    s = settings()
    inputs = col.Inputs(
        warehouse=_project_path(db) if db is not None else s.path("warehouse"),
        store=_project_path(store) if store is not None else s.path("predictions"),
        dataset=_project_path(dataset),
        evaluation_csv=_project_path(evaluation_csv),
        season=s.current_season,
        now=clock,
        streamer_dataset=_project_path(streamer_dataset),
        modules=tuple(dict.fromkeys(["waiver_radar", *modules])) if modules else col.MODULES,
    )

    def fail(message: str, step: str) -> None:
        text = tg.redact(message, tgt.url)
        typer.echo(f"not published: {text}", err=True)
        write_result({"status": "failed", "run_id": run_id, "step": step, "error": text})
        if not dry_run:
            ok = wr.record_failure_at(
                tgt, run_id=run_id, started=started, error=text, notes={"step": step, **extra}
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
    except (col.PublishInputError, FileNotFoundError, ValueError, KeyError, duckdb.Error,
            pl.exceptions.PolarsError) as e:  # fmt: skip
        fail(str(e), "collect")
    problems = col.validate(data)
    if problems:
        fail("validation failed: " + "; ".join(problems), "validate")
    try:
        result = wr.publish(
            tgt, data, dry_run=dry_run, allow_incomplete=allow_incomplete, replace_live=weeks,
            run_id=run_id, started=started, allow_shrink=allow_shrink, notes=extra,
        )  # fmt: skip
    except wr.PublishError as e:
        typer.echo(f"not published (rolled back): {e}", err=True)
        write_result({"status": "refused" if e.shrink else "failed", "run_id": run_id,
                      "error": str(e), "recorded": e.recorded})  # fmt: skip
        if not dry_run:
            typer.echo(
                "the failed run was recorded in pipeline_runs"
                if e.recorded
                else "(the failure could not be recorded in pipeline_runs)",
                err=True,
            )
        raise typer.Exit(code=wr.EXIT_SHRINK_REFUSED if e.shrink else 1) from None
    write_result(wr.result_json(result, tgt))
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


# ---- My League (ESPN; step F2): optional, local only. twm.league is imported inside each
# command, never at import time, so the rest of the app (and the publish) never loads it.

league_app = typer.Typer(
    help="My League (ESPN, optional, local only): needs ENABLE_MY_LEAGUE=true and the ESPN keys "
    "in .env; otherwise every command prints one line and exits with code 2.",
    no_args_is_help=True,
)
app.add_typer(league_app, name="league")


@league_app.command("sync")
def league_sync(
    week: int | None = typer.Option(
        None, help="Week to read (default: ESPN's current week; rosters and box scores then)."
    ),
) -> None:
    """Read the league from ESPN into data/league.duckdb (never published): settings, teams,
    rosters, free agents, box scores (starters, bench, actual and ESPN-projected points) and
    recent activity; ESPN ids are joined to gsis ids (unmatched players listed in
    reports/league/). Prints counts only. Exit 1 when ESPN fails, 2 when My League is off."""
    from twm.league.commands import run_sync

    raise typer.Exit(code=run_sync(week))


@league_app.command("settings-diff")
def league_settings_diff() -> None:
    """Compare the league's real settings (last sync) with config/scoring.yaml and
    config/league.yaml: every scoring item incl. K and D/ST, the lineup slots, the number of
    teams. Prints the differences and a suggested YAML patch; never edits the config."""
    from twm.league.commands import run_settings_diff

    raise typer.Exit(code=run_settings_diff())


@league_app.command("radar")
def league_radar(
    week: int | None = typer.Option(
        None,
        help="The lists' week N (made after week N's games; default: the latest stored "
        "at or before the synced ESPN week).",
    ),
    limit: int = typer.Option(10, "--limit", help="Free agents per position to print."),
) -> None:
    """The week's Waiver Radar and K / D/ST streamer lists (the approved models' stored lists,
    their chances unchanged) restricted to your league's free agents from the last sync, ESPN
    free agents the lists' pools miss shown apart without a chance, and the drop candidate on
    your roster (the lowest-numbered bench player once your best rest-of-season lineup is set;
    never an IR-slot player). Reads only; prints NFL player names and numbers. Exit 2 when My
    League is off, nothing is synced or a list is missing."""
    from twm.league.commands import run_radar

    raise typer.Exit(code=run_radar(week, limit))


@league_app.command("regret")
def league_regret(
    season: int | None = typer.Option(None, help="Season (default: the latest synced)."),
) -> None:
    """Lineup regret per past week from the synced box scores: your actual points, the
    decision-time best lineup (by ESPN's projections before kickoff) and the hindsight best
    lineup (by actual points), so points lost to decisions and to luck, per week and for the
    season. Sync each past week first (`twm league sync --week N`). Exit 2 when My League is
    off or nothing is synced."""
    from twm.league.commands import run_regret

    raise typer.Exit(code=run_regret(season))


@league_app.command("journal")
def league_journal(
    season: int | None = typer.Option(None, help="Season (default: the latest synced)."),
    week: int | None = typer.Option(
        None, help="One week only: the moves made for week N's games and its lineup."
    ),
) -> None:
    """You vs the model: your adds, drops and lineups against what the app said at the time
    (the newest lists made before each move; a list made later is never used): each add's
    Radar / streamer rank, chance and priority or "not on the lists", and its outcome; each
    drop's points since vs the add; the Radar's must-adds you left on waivers; your lineups vs
    the decision-time best (lineup regret). Counts, never a verdict: one season is a small
    sample. Reads only; local output with player names. Exit 2 when My League is off or
    nothing is synced."""
    from twm.league.commands import run_journal

    raise typer.Exit(code=run_journal(season, week))


@league_app.command("report")
def league_report(
    week: int | None = typer.Option(
        None,
        help="The lists' week N (made after week N's games; default: the latest stored "
        "at or before the synced ESPN week).",
    ),
) -> None:
    """The weekly league report: one self-contained HTML file in reports/league/
    (<season>-W<nn>.html, never published) with the personalized Waiver Radar and K / D/ST
    streamer, the drop candidates, Regression Watch's tags on your players, lineup regret, the
    scoring check against ESPN's box scores and the settings difference with its suggested
    patch (nothing is applied). Exit 2 when My League is off, nothing is synced or a list is
    missing."""
    from twm.league.commands import run_report

    raise typer.Exit(code=run_report(week))


@league_app.command("weekly")
def league_weekly(
    week: int | None = typer.Option(
        None,
        help="The lists' week N to score and report (made after week N's games; default: the "
        "latest week whose Tuesday as-of has passed).",
    ),
    limit: int = typer.Option(3, "--limit", help="Free agents per position in the summary."),
) -> None:
    """The weekly routine in one command (run it on Tuesday after 14:00 UTC, or any day for a
    refresh): refresh the current season's nflverse data, rebuild the FULL warehouse, score the
    week's Radar, K / D/ST streamer and Regression Watch lists into your local predictions store
    with the approved models (a list already stored is never scored again), sync the league,
    write the report and print the radar summary. One line per step; a failure stops with one
    clear line. Exit 2 when My League is off or no week is due, 3 when the week's data has not
    arrived, 1 when a step failed. Never publishes."""
    from twm.league.commands import run_weekly

    raise typer.Exit(code=run_weekly(week, limit))


@league_app.command("trade")
def league_trade(
    give: list[str] = typer.Option(..., "--give", help="A player you give (repeat for more)."),
    get: list[str] = typer.Option(..., "--get", help="A player you get (repeat for more)."),
    week: int | None = typer.Option(
        None,
        help="Regression Watch's list week N (made after week N's games; default: the latest "
        "stored at or before the synced ESPN week). The weeks after N are counted.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Print the numbers as JSON."),
) -> None:
    """The trade checker: for you and the partner, expected starting-lineup points
    (QB/RB/WR/TE) before and after the trade, each week's best lineup by Regression Watch's
    per-game projection (byes 0; K and D/ST kept fixed), split into regular-season and
    fantasy-playoff weeks. The change, its 80% range and the share of simulated seasons that
    gain all come from one simulation with the projection's real backtest misses (so the change
    is the simulation's median, not after minus before); plus positional scarcity, bye overlap,
    tags and ESPN statuses. Names match the last sync (case and punctuation ignored); --get
    players are on one other team and/or free agents. Reads only; local output with player and
    team names. Exit 2 when My League is off, nothing is synced, an input is missing or a name
    does not resolve."""
    from twm.league.commands import run_trade

    raise typer.Exit(code=run_trade(give, get, week, as_json))


# ---- Start/sit odds (feature #2): LOCAL ONLY (FantasyPros weekly ranks may not be republished).
# No ESPN needed. twm.modules.startsit is imported inside the commands, never at import time.


@league_app.command("startsit")
def league_startsit(
    player_a: str = typer.Argument(..., help='Player A, e.g. "Jane Doe" (add "WR" if needed).'),
    player_b: str = typer.Argument(..., help="Player B."),
    season: int | None = typer.Option(None, "--season", help="Season (default: the as-of's)."),
    week: int | None = typer.Option(None, "--week", help="Week (default: the next to be played)."),
    as_of: str | None = typer.Option(
        None, "--as-of", help="ISO time with a zone (default: now); ranks available then only."
    ),
    play_a: float | None = typer.Option(
        None, "--play-chance-a", help="A's chance to play, 0-1 (default: his rank's history)."
    ),
    play_b: float | None = typer.Option(None, "--play-chance-b", help="B's chance to play."),
    db: Path | None = typer.Option(None, "--db", help="Warehouse (default: config paths)."),
) -> None:
    """Start A or B? The chance A outscores B this week, from how players at each FantasyPros
    weekly expert rank scored in 2020-2025 (frozen, `twm model check startsit`), with both
    players' 10th/50th/90th percentile points and a one-line verdict ("close call" under 55%).
    Local only: prints FantasyPros ranks, never published. Exit 1 no usable pin, 2 a name does
    not resolve (choices listed), 3 the week's ranks are not out yet (they arrive Fridays)."""
    from datetime import datetime

    from twm.asof import AsOfParseError, parse_as_of
    from twm.modules.startsit.cli import run_startsit

    when = None
    if as_of is not None:
        parsed = None
        try:
            parsed = parse_as_of(as_of)
        except AsOfParseError as e:
            typer.echo(str(e), err=True)
        if not isinstance(parsed, datetime):
            typer.echo("--as-of needs an ISO time with a zone (2026-10-04T15:00Z)", err=True)
            raise typer.Exit(code=2)
        when = parsed
    path = _warehouse_or_exit(db)
    raise typer.Exit(code=run_startsit(player_a, player_b, db=path, season=season, week=week,
                                       as_of=when, play_a=play_a, play_b=play_b))  # fmt: skip


@league_app.command("startsit-pin")
def league_startsit_pin(
    season: int | None = typer.Option(None, "--season", help="Season (default: current)."),
    db: Path | None = typer.Option(None, "--db", help="Warehouse (default: config paths)."),
) -> None:
    """Freeze the start/sit distributions (2020-2025) and their walk-forward backtest under
    artifacts/production_models/startsit/, write reports/startsit/backtest.md and pin them in
    config/production_models.yaml (other pins kept). Review, then commit."""
    from twm.config import settings
    from twm.modules.startsit.cli import run_pin

    chosen = season if season is not None else settings().current_season
    raise typer.Exit(code=run_pin(chosen, _warehouse_or_exit(db)))


if __name__ == "__main__":
    app()
