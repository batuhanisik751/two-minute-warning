"""``twm timemachine verify`` (step I3a): stored Time Machine rows == a fresh recomputation."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import typer

timemachine_app = typer.Typer(help="The Time Machine's reproducibility check (step I3a).",
                              no_args_is_help=True)  # fmt: skip


def _seasons_text(seasons: list[int]) -> str:
    """'2014,2017,2025', or '2014-2025 (all 12)' for a run of consecutive seasons."""
    if not seasons:
        return "-"
    if len(seasons) > 4 and seasons == list(range(seasons[0], seasons[-1] + 1)):
        return f"{seasons[0]}-{seasons[-1]} (all {len(seasons)})"
    text = ",".join(str(s) for s in seasons)
    return text if len(text) <= 26 else f"{text[:22]}...({len(seasons)})"


def report_lines(results: list) -> list[str]:
    """One table row per module, then each module's mismatch examples, notes and errors."""
    head = f"{'module':<17} {'status':<16} {'seasons':<26} {'lists':>6} {'rows':>8} max|diff| mism"
    lines = [head, "-" * len(head)]
    for r in results:
        c = r.comparison
        seasons = _seasons_text(r.seasons)
        lines.append(f"{r.module:<17} {r.status:<16} {seasons:<26} {c.lists:>6} {c.rows:>8} "
                     f"{c.max_diff:>9.2g} {c.mismatches:>4}")  # fmt: skip
    for r in results:
        if r.error:
            lines.append(f"{r.module}: cannot be recomputed: {r.error}")
        lines += [f"{r.module}: mismatch: {e}" for e in r.comparison.examples]
        lines += [f"{r.module}: {n}" for n in r.notes]
    return lines


@timemachine_app.command("verify")
def verify_cmd(
    module: str = typer.Option(
        "all",
        "--module",
        help="waiver_radar, streamer_k, streamer_dst "
        "(streamer = both), regression_watch, decisions, hot_seat, board, "
        "or all.",
    ),  # fmt: skip
    sample: int = typer.Option(
        4,
        "--sample",
        min=1,
        help="Seasons per module (the first and last backtest season always among them).",
    ),  # fmt: skip
    seed: int = typer.Option(20261002, "--seed", help="Seed of the season sample."),
    season: int | None = typer.Option(None, "--season", help="Pinned season (default: current)."),
    db: Path | None = typer.Option(None, "--db", help="Warehouse (default: config paths)."),
    work: Path | None = typer.Option(
        None, "--work", help="Keep the recomputed files here (default: a temporary directory)."
    ),  # fmt: skip
) -> None:
    """Recompute sampled past lists of every module from the warehouse with the model used
    then and compare them with the stored rows the site serves (exit 1 on any mismatch or
    module that cannot be recomputed). Reads the warehouse and the predictions store
    read-only; writes only under --work (docs/timemachine.md)."""
    from twm.cli import _warehouse_or_exit
    from twm.config import settings
    from twm.timemachine import recompute as rc

    try:
        keys = rc.chosen(module)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from None
    t0 = time.perf_counter()

    def progress(m: str) -> None:
        typer.echo(f"[{time.perf_counter() - t0:5.0f} s] {m}", err=True)

    with tempfile.TemporaryDirectory(prefix="twm-timemachine-") as tmp:
        ctx = rc.Context(db=_warehouse_or_exit(db), work=work or Path(tmp),
                         season=season or settings().current_season, n=sample, seed=seed,
                         store=settings().path("predictions"), progress=progress)  # fmt: skip
        results = rc.run(ctx, keys)
    for line in report_lines(results):
        typer.echo(line)
    bad = [r.module for r in results if r.status != "REPRODUCED"]
    typer.echo(f"{len(results) - len(bad)} of {len(results)} modules reproduced exactly "
               f"(tolerance 1e-9) in {time.perf_counter() - t0:.0f} s")  # fmt: skip
    if bad:
        typer.echo(f"not reproduced: {', '.join(bad)}", err=True)
        raise typer.Exit(code=1)
