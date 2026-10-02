"""`twm offseason status` (step I6b; :mod:`twm.offseason`, docs/offseason.md)."""

from __future__ import annotations

import typer

offseason_app = typer.Typer(
    help="The yearly offseason routine (docs/offseason.md): where it stands, read-only."
)


@offseason_app.command("status")
def status_cmd(
    now: str | None = typer.Option(None, "--now", help="Pretend it is this UTC time (ISO)."),
) -> None:
    """Per pinned module: the season it is approved for vs the season due, whether its frozen
    history covers the finished season; whether that season's coach departures are labelled;
    the season due's live board; and the next step of docs/offseason.md. Changes nothing."""
    from twm import offseason as off
    from twm.cli import _clock

    typer.echo(off.status_text(off.gather(_clock(now))))
