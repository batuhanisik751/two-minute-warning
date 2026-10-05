"""`twm league startsit`, `twm league startsit-pin` and `twm model check startsit` (LOCAL ONLY).

The typer commands live in twm/cli.py and import this module inside the command, so nothing
loads it unless a start/sit command runs (the publish never does). Exit codes: 0 done, 1 the
pin is not usable, 2 a name does not resolve, 3 the week's ranks are not out yet.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import typer

EXIT_PIN, EXIT_NAME, EXIT_NOT_OUT = 1, 2, 3


def _choices(text: str, found: list) -> str:
    opts = "; ".join(f"{r.name} ({r.pos}{r.rank}, {r.team or '?'})" for r in found[:8])
    return f'"{text}" matches several ranked players: {opts}. Add more of the name or a position.'


def run_startsit(a_text: str, b_text: str, *, db: Path, season: int | None = None,
                 week: int | None = None, as_of: datetime | None = None,
                 play_a: float | None = None, play_b: float | None = None,
                 echo: Callable[[str], None] = typer.echo) -> int:  # fmt: skip
    """Resolve both names among the target week's ranks at ``as_of`` (default now) and print
    the odds; the week defaults to the next one with games to come."""
    from twm.modules.startsit import live
    from twm.modules.startsit.production import StartSitPinError, load

    when = as_of or datetime.now(UTC)
    if season is None or week is None:
        s0, w0 = live.target_week(db, when)
        season, week = season or s0, week or w0
    try:
        table, _ = load(season)
    except StartSitPinError as e:
        echo(f"start/sit odds not available: {e}")
        return EXIT_PIN
    try:
        wr = live.week_ranks(db, season, week, when)
    except live.RanksNotOutError as e:
        echo(str(e))
        return EXIT_NOT_OUT
    picked = []
    for text in (a_text, b_text):
        found = live.resolve(wr.rows, text)
        if not found:
            echo(f'No player ranked for week {week} matches "{text}" (QB/RB/WR/TE only: no K or '
                 "D/ST ranks).")  # fmt: skip
            return EXIT_NAME
        if len(found) > 1:
            echo(_choices(text, found))
            return EXIT_NAME
        if not found[0].valid:
            echo(f"{found[0].name}'s game kicked off before these ranks came out: no odds.")
            return EXIT_NAME
        picked.append(found[0])
    if picked[0] == picked[1]:
        echo("Both names are the same player.")
        return EXIT_NAME
    o = live.odds(table, picked[0], picked[1], play_a, play_b)
    for line in live.describe(o, wr):
        echo(line)
    return 0


def run_pin(season: int, db: Path, echo: Callable[[str], None] = typer.echo) -> int:
    """Fit on 2020-2025, backtest walk-forward, freeze, write the report and the pin."""
    import duckdb

    from twm.modules.startsit import data, production

    con = duckdb.connect(str(db), read_only=True)
    try:
        history = data.history(con, production.TRAIN_SEASONS)
    finally:
        con.close()
    pin = production.freeze(history, season, progress=lambda m: echo(f"  {m}"))
    echo(
        f"pinned {pin.module} {pin.season}: {pin.model_version} ({pin.file}); wrote "
        f"{production.REPORT}. Check with `uv run twm model check startsit`, then commit."
    )
    return 0


def check_pin(season: int) -> None:
    """`twm model check` for start/sit odds (exit 1 on any problem). Changes nothing."""
    from twm.modules.startsit.production import check

    lines, problems = check(season)
    for line in lines:
        typer.echo(line)
    if problems:
        typer.echo("not usable: " + "; ".join(problems), err=True)
        raise typer.Exit(code=1)
