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
        None,
        "--season",
        help="Test season(s) to fit (repeatable; 2006-2025 or the current season); "
        "default 2006-2025.",
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
    allowed = wp.fold_seasons()
    bad = [s for s in seasons if s not in allowed]
    if bad:
        raise typer.BadParameter(f"test seasons must be within {min(allowed)}-{max(allowed)}; "
                                 f"got {bad}")  # fmt: skip
    # the arguments are checked before the warehouse: a bad season is a usage error (exit 2)
    path = _warehouse_or_exit(db)
    t0 = time.perf_counter()
    last = max(*seasons, *wp.TEST_SEASONS)
    states = load_states(path, tuple(range(wp.FIRST_SEASON, last + 1)))
    typer.echo(
        f"{states.rows.height:,} play states {wp.FIRST_SEASON}-{last} "
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
        None,
        "--season",
        help="Test season(s) to fit (repeatable; 2006-2025 or the current season); "
        "default 2006-2025.",
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
    from twm.modules.decisions import wp

    path = _warehouse_or_exit(db)
    seasons = sorted(set(season)) if season else list(sm.TEST_SEASONS)
    allowed = wp.fold_seasons()
    bad = [s for s in seasons if s not in allowed]
    if bad:
        raise typer.BadParameter(f"test seasons must be within {min(allowed)}-{max(allowed)}; "
                                 f"got {bad}")  # fmt: skip
    names = list(dict.fromkeys(only)) if only else list(SUBMODEL_NAMES)
    unknown = [n for n in names if n not in SUBMODEL_NAMES]
    if unknown:
        raise typer.BadParameter(f"unknown sub-model(s) {unknown}; known: {SUBMODEL_NAMES}")
    t0 = time.perf_counter()
    plays = sm.load_plays(path, tuple(range(sm.FIRST_SEASON, max(*seasons, *sm.TEST_SEASONS) + 1)))
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


@decisions_app.command("grade")
def grade(
    season: list[int] = typer.Option(
        None,
        "--season",
        help="Season(s) to grade (repeatable; 2006-2025 or the current season); default the "
        "current season. Grade one season per command: each prints its progress.",
    ),
    report_only: bool = typer.Option(
        False, "--report-only", help="Grade nothing; write the report from the stored grades."
    ),
    no_report: bool = typer.Option(False, "--no-report", help="Grade only; skip the report."),
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
) -> None:
    """Grade every fourth down (go / field goal / punt) and every two-point decision of a
    season with the fold models that learned from earlier seasons only (G3); each decision is
    stored with all its inputs under data/decisions/graded/ (reproducible), credited to the
    head coach. Then reports/decisions/fourth_downs.md + .csv from every graded season."""
    import time

    from twm.cli import _warehouse_or_exit
    from twm.config import settings
    from twm.modules.decisions import grade as gr
    from twm.modules.decisions import wp

    seasons = sorted(set(season)) if season else [int(settings().current_season)]
    allowed = wp.fold_seasons()
    bad = [s for s in seasons if s not in allowed]
    if bad:
        raise typer.BadParameter(f"seasons must be within {min(allowed)}-{max(allowed)}; "
                                 f"got {bad}")  # fmt: skip
    t0 = time.perf_counter()
    if not report_only:
        path = _warehouse_or_exit(db)
        for i, s in enumerate(seasons, 1):
            typer.echo(f"[{i}/{len(seasons)}] grading {s}")
            try:
                gr.grade_season(path, s, progress=typer.echo)
            except gr.GradeError as e:
                typer.echo(f"cannot grade {s}: {e}", err=True)
                raise typer.Exit(code=1) from e
    if no_report:
        return
    from twm.modules.decisions import decisions_report

    try:
        md, csv = decisions_report.write_report(progress=typer.echo)
    except gr.GradeError as e:
        typer.echo(f"cannot write the report: {e}", err=True)
        raise typer.Exit(code=1) from e
    typer.echo(f"wrote {md} and {csv} [{time.perf_counter() - t0:.0f} s]")


@decisions_app.command("wp-select")
def wp_select_cmd(
    candidate: str = typer.Option(None, "--candidate", help="Candidate family to fit."),
    season: int = typer.Option(None, "--season", help="Validation season (2004 or 2005)."),
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
) -> None:
    """G1b: fit one candidate WP family's walk-forward fold for one VALIDATION season (2004 or
    2005; test and graded seasons are refused) and save its log loss and smoothness under
    data/decisions/wp_select/. Without --candidate: print every saved result and the family
    the fixed rule chooses (docs/decision_metrics.md, "WP smoothness")."""
    import time

    from twm.modules.decisions import wp_select as ws

    if candidate is None:
        rows = ws.summarize(ws.load_results())
        for r in sorted(rows, key=lambda r: r["log_loss"]):
            typer.echo(f"{r['candidate']:<18} log loss {r['log_loss']:.5f} meets {r['meets']!s:<5} "
                       f"worst ratio {r['worst_ratio']:.2f} h1 {r['score_step_h1']:.1f} "
                       f"h2 {r['score_step_h2']:.1f} curv {r['curvature']:.1f} "
                       f"half {r['halftime_possession']:.1f} viol {r['monotone_violations']} "
                       f"[{r['seconds']:.0f} s]")  # fmt: skip
        if rows:
            typer.echo(f"chosen by the rule: {ws.choose(ws.load_results())} "
                       f"(in use: {ws.CHOSEN})")  # fmt: skip
        return
    if season is None or season not in ws.VALIDATION_SEASONS or candidate not in ws.CANDIDATES:
        raise typer.BadParameter(f"--season must be one of {ws.VALIDATION_SEASONS} and "
                                 f"--candidate one of {sorted(ws.CANDIDATES)}")  # fmt: skip
    from twm.cli import _warehouse_or_exit
    from twm.modules.decisions import wp
    from twm.modules.decisions.wp_data import load_states

    path = _warehouse_or_exit(db)
    t0 = time.perf_counter()
    states = load_states(path, tuple(range(wp.FIRST_SEASON, season + 1)))
    r = ws.run_candidate(states.rows, candidate, season, progress=typer.echo)
    sm = r["smooth"]
    typer.echo(f"{candidate} {season}: log loss {r['log_loss']:.5f}, failures {r['failures']}, "
               + ", ".join(f"{k} {sm[k]:.1f}" for k in ("score_step_h1", "score_step_h2",
                                                        "curvature", "halftime_possession"))
               + f" [{time.perf_counter() - t0:.0f} s]")  # fmt: skip
