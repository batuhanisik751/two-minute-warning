"""`twm` command-line interface. Subcommands are added step by step."""

from __future__ import annotations

import typer

from twm import __version__

app = typer.Typer(help="Two-Minute Warning pipeline.", no_args_is_help=True)


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@app.command()
def doctor() -> None:
    """Check config, data freshness and environment (grows over time)."""
    from twm.config import league, scoring, settings

    s = settings()
    typer.echo(f"project: {s.project_name}  season: {s.current_season}")
    typer.echo(f"scoring: PPR={scoring().receiving['receptions']}  teams={league().teams}")
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


if __name__ == "__main__":
    app()
