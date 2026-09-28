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
    """Show the config in use and the warehouse tables with their row counts."""
    import duckdb

    from twm.config import league, scoring, settings
    from twm.sources import nflverse as nv
    from twm.warehouse import build as wb

    s = settings()
    typer.echo(f"project: {s.project_name}  season: {s.current_season}")
    sc = scoring()
    typer.echo(
        f"scoring: {sc.receiving.get('receptions', 0):g} per catch, fumbles lost: "
        f"{sc.options.fumbles_lost_scope}  teams={league().teams}"
    )
    db = s.path("warehouse")
    if db.exists():
        typer.echo(f"warehouse: {nv.project_relative(db)}")
        try:
            counts = wb.table_counts(db)
        except duckdb.Error:
            typer.echo("  locked by another process (close notebooks / open read-only)")
        else:
            for name, n in counts:
                typer.echo(f"  {name:22s} {n:>12,} rows")
            line = _ids_line(db)
            if line:
                typer.echo(line)
    else:
        typer.echo(
            f"warehouse: not built ({nv.project_relative(db)}); run "
            f"`twm build {s.current_season - 1} {s.current_season}` (or --start 1999)"
        )
    typer.echo("ok")


def _ids_line(db: Path) -> str | None:
    """The one ID-coverage line of ``twm doctor`` (None if the report tables are absent)."""
    import duckdb

    from twm import ids
    from twm.warehouse import build as wb

    try:
        con = wb.connect(db, read_only=True)
    except duckdb.Error:
        return None
    try:
        return ids.doctor_line(con)
    except duckdb.Error:
        return None
    finally:
        con.close()


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
    "features and the training dataset."
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
