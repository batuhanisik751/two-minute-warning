"""`twm decisions pin | grade-pinned` and the decisions part of `twm model check` (step P3).

Registered on :data:`twm.modules.decisions.cli.decisions_app` when :mod:`twm.cli` imports this
module (kept apart from the grading commands in ``cli.py``).
"""

from __future__ import annotations

from pathlib import Path

import typer

from twm.modules.decisions.cli import decisions_app

REPORTS = {"fourth_downs": Path("reports/decisions/fourth_downs.csv"),
           "clock": Path("reports/decisions/clock.csv")}  # fmt: skip


def _reports() -> dict[str, Path]:
    from twm.config import ROOT

    return {k: ROOT / v for k, v in REPORTS.items()}


def _season(season: int | None) -> int:
    from twm.config import settings

    return int(season) if season is not None else int(settings().current_season)


@decisions_app.command("pin")
def pin_cmd(
    season: int | None = typer.Option(None, "--season", help="Season (default: current)."),
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
) -> None:
    """Approve the grading of the season in progress (the owner's Mac, after `twm decisions
    grade` and `twm decisions clock` of every season and their reports): copy the season's fold
    models and its grading spec to artifacts/production_models/decisions/, freeze the graded
    history of the earlier seasons there, and pin all of it in config/production_models.yaml.
    Refuses unless the history reproduces reports/decisions/{fourth_downs,clock}.csv and the
    pinned grading reproduces the season's stored grades exactly. Review, then commit."""
    from twm.cli import _warehouse_or_exit
    from twm.modules.decisions import production as dp

    s = _season(season)
    path = _warehouse_or_exit(db)
    try:
        pin = dp.approve(s, db=path, reports=_reports(), progress=typer.echo)
    except dp.DecisionsProductionError as e:
        typer.echo(f"not approved: {e}", err=True)
        raise typer.Exit(code=1) from None
    rows = ", ".join(f"{f.rows:,} {t}" for t, f in pin.backtest.items())
    typer.echo(f"pinned {pin.module} {pin.season}: {pin.model_version} ({pin.file}); history "
               f"{pin.backtest_seasons}: {rows}. Run `uv run twm model check decisions`, "
               "review `git diff`, commit the pin, the files and the reports "
               "together.")  # fmt: skip


@decisions_app.command("grade-pinned")
def grade_pinned_cmd(
    season: int | None = typer.Option(None, "--season", help="Season (default: current)."),
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
    out: Path | None = typer.Option(
        None, "--out", help="Folder for the grades (default data/decisions/season)."
    ),
) -> None:
    """Grade every fourth down, try and clock case of the season so far with the APPROVED
    grading only (config/production_models.yaml `decisions`: sha256 of the spec and of every
    model checked before it is opened; nothing trained, nothing measured on earlier seasons).
    What the scheduled job runs; `twm publish` reads the result. Exit 1 when the pins cannot
    be used."""
    from twm import pins
    from twm.cli import _warehouse_or_exit
    from twm.modules.decisions import production as dp
    from twm.modules.decisions import season as sn

    s = _season(season)
    path = _warehouse_or_exit(db)
    try:
        spec, models, pin = dp.load_pinned(s)
    except pins.PinError as e:
        typer.echo(f"not usable: {e}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"approved grading {pin.model_version}: {spec.describe()}")
    try:
        sn.grade_season(path, s, spec, models, out_dir=out, progress=typer.echo)
    except (ValueError, OSError) as e:
        typer.echo(f"cannot grade {s}: {e}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"wrote {out if out is not None else sn.season_dir()}")


def check_pin(season: int) -> None:
    """`twm model check` for the Decision Report Card (exit 1 on any problem): the pin present;
    the grading spec as pinned (sha256 before it is read, version = the hash of its content,
    season); every fold model as the spec names it (sha256 before it is opened, version, the
    season's fold trained on earlier seasons only); the frozen history as pinned (sha256 and
    rows) and reproducing reports/decisions/{fourth_downs,clock}.csv's coach-season and league
    rows. Changes nothing."""
    from twm import pins
    from twm.modules.decisions import frozen as fz
    from twm.modules.decisions import production as dp

    try:
        spec, _models, pin = dp.load_pinned(season)
        frames = fz.load_snapshot(pin)
    except pins.PinError as e:
        typer.echo(f"not usable: {e}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"{pin.module} {season}: approved {pin.model} {pin.model_version} ({pin.file}, "
               f"sha256 {pin.sha256[:12]}..., approved {pin.approved or '?'})")  # fmt: skip
    typer.echo(f"  {spec.describe()} (5 model files: sha256, version and fold checked)")
    problems = fz.snapshot_mismatches(frames, _reports())
    if problems:
        typer.echo(
            f"not usable: the frozen decision history disagrees with the reports in "
            f"{len(problems)} places, e.g. " + "; ".join(problems[:3]),
            err=True,
        )
        raise typer.Exit(code=1)
    rows = ", ".join(f"{pin.backtest[t].rows:,} {t}" for t in fz.TABLES)
    typer.echo(f"  frozen history {pin.backtest_seasons}: {rows} (sha256 and rows checked); "
               "its coach-season and league rows match reports/decisions/fourth_downs.csv and "
               "clock.csv")  # fmt: skip
