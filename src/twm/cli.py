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


if __name__ == "__main__":
    app()
