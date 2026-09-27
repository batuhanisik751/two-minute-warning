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
    typer.echo(f"scoring: PPR={scoring().receiving['receptions']}  teams={league().teams}")
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
    else:
        typer.echo(
            f"warehouse: not built ({nv.project_relative(db)}); run "
            f"`twm build {s.current_season - 1} {s.current_season}` (or --start 1999)"
        )
    typer.echo("ok")


@app.command()
def ingest(
    datasets: list[str] | None = typer.Argument(None, help="Dataset names (default: all)."),
    start: int = typer.Option(None, help="First season (default: each dataset's first season)."),
    end: int = typer.Option(None, help="Last season (default: current season)."),
    force: bool = typer.Option(False, help="Re-download even if cached."),
) -> None:
    """Download nflverse datasets into the local Parquet cache (data/raw)."""
    from twm.config import settings
    from twm.sources import nflverse as nv

    names = datasets or list(nv.DATASETS)
    unknown = [n for n in names if n not in nv.DATASETS]
    if unknown:
        raise typer.BadParameter(f"unknown datasets: {unknown}; known: {list(nv.DATASETS)}")
    last = end or settings().current_season
    for name in names:
        ds = nv.DATASETS[name]
        if not ds.per_season:
            df = nv.fetch(name, force=force)
            typer.echo(f"{name:22s} all      {df.height:>9,} rows  {df.width:>4} cols")
            continue
        first = start or ds.first_season or settings().seasons["pbp_start"]
        for season in range(first, last + 1):
            try:
                df = nv.fetch(name, season, force=force)
            except Exception as e:  # noqa: BLE001 - report and keep going
                typer.echo(f"{name:22s} {season}   FAILED: {type(e).__name__}: {str(e)[:80]}")
                continue
            typer.echo(f"{name:22s} {season}   {df.height:>9,} rows  {df.width:>4} cols")


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


if __name__ == "__main__":
    app()
