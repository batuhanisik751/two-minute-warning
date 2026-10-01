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

    path = _warehouse_or_exit(db)
    seasons = sorted(set(season)) if season else list(wp.TEST_SEASONS)
    bad = [s for s in seasons if s not in wp.TEST_SEASONS]
    if bad:
        raise typer.BadParameter(f"test seasons must be within 2006-2025; got {bad}")
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
