"""`twm decisions ...`: the Decision Report Card's commands (registered in twm.cli)."""

from __future__ import annotations

from pathlib import Path

import typer

decisions_app = typer.Typer(
    help="Decision Report Card (P2): the app's own win-probability model (G1) and, later, "
    "fourth-down, two-point and clock grades (docs in src/twm/modules/decisions/)."
)


@decisions_app.command("wp-backtest")
def wp_backtest(
    season: list[int] = typer.Option(
        None, "--season", help="Test season(s) to fit (repeatable); default 2006-2025."
    ),
    force: bool = typer.Option(
        False, "--force", help="Refit folds even when the saved ones are current."
    ),
    report_only: bool = typer.Option(
        False, "--report-only", help="Fit nothing; write the report from the saved folds."
    ),
    no_report: bool = typer.Option(False, "--no-report", help="Fit only; skip the report."),
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
) -> None:
    """Walk-forward backtest of the own WP model: one LightGBM per test season (trained on
    earlier seasons only), saved under models/decisions/; then reports/decisions/wp_backtest.md
    + .csv comparing it with nflfastR's wp and vegas_wp on the same plays."""
    import time

    from twm.cli import _warehouse_or_exit
    from twm.modules.decisions import wp
    from twm.modules.decisions.wp_data import load_states

    seasons = sorted(set(season)) if season else list(wp.TEST_SEASONS)
    bad = [s for s in seasons if s not in wp.TEST_SEASONS]
    if bad:
        raise typer.BadParameter(f"test seasons must be within 2006-2025; got {bad}")
    # the arguments are checked before the warehouse: a bad season is a usage error (exit 2)
    path = _warehouse_or_exit(db)
    t0 = time.perf_counter()
    states = load_states(path, tuple(range(wp.FIRST_SEASON, max(wp.TEST_SEASONS) + 1)))
    typer.echo(
        f"{states.rows.height:,} play states {wp.FIRST_SEASON}-{max(wp.TEST_SEASONS)} "
        f"(dropped: {states.drops}; {states.n_tie_games} tie games) "
        f"[{time.perf_counter() - t0:.0f} s]"
    )
    if not report_only:
        fitted = wp.run_backtest(states.rows, seasons, force=force, progress=typer.echo)
        typer.echo(f"fitted {len(fitted)} fold(s): {fitted or '-'}")
    if no_report:
        return
    from twm.modules.decisions import wp_report

    try:
        md, csv = wp_report.write_report(states, progress=typer.echo)
    except wp.WpModelError as e:
        typer.echo(f"cannot write the report: {e}", err=True)
        raise typer.Exit(code=1) from e
    typer.echo(f"wrote {md} and {csv} [{time.perf_counter() - t0:.0f} s]")


SUBMODEL_NAMES = ("conversion", "fieldgoal", "punt", "tries")


def submodel_parts(name: str):
    """(module, model name, columns hashed per fold) of a G2 sub-model."""
    from twm.modules.decisions import conversion, fieldgoal, punt, tries

    return {
        "conversion": (conversion, conversion.MODEL, conversion.SPEC.hash_columns),
        "fieldgoal": (fieldgoal, fieldgoal.MODEL, fieldgoal.SPEC.hash_columns),
        "punt": (punt, punt.MODEL, punt.HASH_COLUMNS),
        "tries": (tries, tries.MODEL, tries.HASH_COLUMNS),
    }[name]


@decisions_app.command("submodels-backtest")
def submodels_backtest(
    season: list[int] = typer.Option(
        None, "--season", help="Test season(s) to fit (repeatable); default 2006-2025."
    ),
    only: list[str] = typer.Option(
        None, "--only", help="conversion, fieldgoal, punt or tries (repeatable); default all."
    ),
    force: bool = typer.Option(False, "--force", help="Refit folds even when current."),
    report_only: bool = typer.Option(
        False, "--report-only", help="Fit nothing; write the report from the saved folds."
    ),
    no_report: bool = typer.Option(False, "--no-report", help="Fit only; skip the report."),
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
) -> None:
    """Walk-forward backtest of the fourth-down sub-models (G2): go-for-it conversion, field
    goal, punt result distribution, extra-point / two-point rates; one fold per test season
    (learning from earlier seasons only), saved under models/decisions/; then
    reports/decisions/submodels.md + .csv."""
    import time

    from twm.cli import _warehouse_or_exit
    from twm.modules.decisions import submodels as sm

    path = _warehouse_or_exit(db)
    seasons = sorted(set(season)) if season else list(sm.TEST_SEASONS)
    bad = [s for s in seasons if s not in sm.TEST_SEASONS]
    if bad:
        raise typer.BadParameter(f"test seasons must be within 2006-2025; got {bad}")
    names = list(dict.fromkeys(only)) if only else list(SUBMODEL_NAMES)
    unknown = [n for n in names if n not in SUBMODEL_NAMES]
    if unknown:
        raise typer.BadParameter(f"unknown sub-model(s) {unknown}; known: {SUBMODEL_NAMES}")
    t0 = time.perf_counter()
    plays = sm.load_plays(path, tuple(range(sm.FIRST_SEASON, max(sm.TEST_SEASONS) + 1)))
    typer.echo(f"{plays.height:,} play rows [{time.perf_counter() - t0:.0f} s]")
    rows, drops = {}, {}
    for n in SUBMODEL_NAMES:
        mod, model, cols = submodel_parts(n)
        rows[n], drops[n] = mod.build_rows(plays)
        typer.echo(f"{n}: {rows[n].height:,} rows (dropped: {drops[n]})")
        if n in names and not report_only:
            fitted = sm.run_backtest(rows[n], model, mod.fit_fold, cols, seasons, force=force,
                                     progress=typer.echo)  # fmt: skip
            typer.echo(f"{n}: fitted {len(fitted)} fold(s) [{time.perf_counter() - t0:.0f} s]")
    del plays
    if no_report:
        return
    from twm.modules.decisions import submodels_report

    try:
        md, csv = submodels_report.write_report(rows, drops=drops, progress=typer.echo)
    except sm.SubModelError as e:
        typer.echo(f"cannot write the report: {e}", err=True)
        raise typer.Exit(code=1) from e
    typer.echo(f"wrote {md} and {csv} [{time.perf_counter() - t0:.0f} s]")
