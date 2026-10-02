"""`twm hotseat pin | score` and the Hot-Seat part of `twm model check` (step H4a).

Registered on :data:`twm.modules.hot_seat.cli.hotseat_app` when :mod:`twm.cli` imports this
module (kept apart from the research commands in ``cli.py``, like the decisions' pins).
"""

from __future__ import annotations

from pathlib import Path

import typer

from twm.modules.hot_seat.cli import CANDIDATES_CSV, FEATURES_PATH, LABELS_CSV, hotseat_app

REPORT_DIR = Path("reports/hot_seat")
METRICS_CSV = REPORT_DIR / "backtest_metrics.csv"
FIRINGS_CSV = REPORT_DIR / "firings_per_season.csv"
VERIFIED_PREDICTIONS = Path("data/hot_seat/backtest_predictions.parquet")


def _season(season: int | None) -> int:
    from twm.config import settings

    return int(season) if season is not None else int(settings().current_season)


def _root(p: Path) -> Path:
    from twm.config import ROOT

    return p if p.is_absolute() else ROOT / p


@hotseat_app.command("pin")
def pin_cmd(
    season: int | None = typer.Option(None, "--season", help="Season (default: current)."),
    db: Path | None = typer.Option(None, "--db", help="Warehouse (default: config paths)."),
    features: Path | None = typer.Option(None, "--features", help=f"Default {FEATURES_PATH}."),
) -> None:
    """Approve the live Hot-Seat model (owner, 2026-10-01: the L2 logistic regression) on the
    owner's Mac after `twm hotseat backtest --labels verified`: refit the logit walk-forward
    and refuse unless it gives the verified run's probabilities; freeze that backtest under
    artifacts/production_models/hot_seat/ and refuse unless it reproduces
    reports/hot_seat/backtest_metrics.csv and firings_per_season.csv; train the season's fold
    on every earlier verified season; pin both in config/production_models.yaml. Review, then
    commit."""
    import polars as pl

    from twm.cli import _warehouse_or_exit
    from twm.config import settings
    from twm.modules.hot_seat import production as hp
    from twm.modules.hot_seat import targets as ht

    s = _season(season)
    path = _warehouse_or_exit(db)
    man = settings().path("manual")
    fpath = _root(features or FEATURES_PATH)
    for p in (fpath, _root(VERIFIED_PREDICTIONS), man / LABELS_CSV, man / CANDIDATES_CSV):
        if not p.exists():
            typer.echo(f"not found: {p}", err=True)
            raise typer.Exit(code=1)
    try:
        rows = hp.verified_targets(path, fpath, man / LABELS_CSV, man / CANDIDATES_CSV)
        verified = pl.read_parquet(_root(VERIFIED_PREDICTIONS))
        names = dict(ht.load_coach_names(path).iter_rows())
        pin, pm = hp.approve(rows, verified, season=s, metrics_csv=_root(METRICS_CSV),
                             firings_csv=_root(FIRINGS_CSV), names=names)  # fmt: skip
    except (hp.HotSeatProductionError, ValueError) as e:
        typer.echo(f"not approved: {e}", err=True)
        raise typer.Exit(code=1) from None
    rows_text = ", ".join(f"{f.rows:,} {t}" for t, f in pin.backtest.items())
    typer.echo(f"pinned {pin.module} {pin.season}: {pm.model_version} ({pin.file}): C "
               f"{pm.params['C']:g} ({pm.params['c_source']}, {pm.params['inner_seasons']}), "
               f"{pm.n_train:,} rows, {pm.n_train_pos:,} positive; backtest "
               f"{pin.backtest_seasons}: {rows_text}. Run `uv run twm model check hot_seat`, "
               "review, commit the pin and the files together.")  # fmt: skip


def check_pin(season: int) -> None:
    """`twm model check` for the Hot-Seat Meter (exit 1 on any problem): the pin present; the
    model file as pinned (sha256 BEFORE the pickle is opened, version, season, model, label,
    features); the frozen backtest as pinned (sha256 before reading, rows), consistent, and
    reproducing reports/hot_seat/backtest_metrics.csv (main, logit, its own probability:
    every slice's ROC-AUC, PR-AUC, Brier and counts, the top-5 hit rates) and
    firings_per_season.csv. Changes nothing."""
    from twm import pins
    from twm.modules.hot_seat import production as hp

    try:
        pm, pin = hp.load_pinned(season)
        frames = hp.load_snapshot(pin)
    except pins.PinError as e:
        typer.echo(f"not usable: {e}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"{pin.module} {season}: approved {pin.model} {pin.model_version} ({pin.file}, "
               f"sha256 {pin.sha256[:12]}..., approved {pin.approved or '?'})")  # fmt: skip
    typer.echo(f"  {pm.describe().split(', tuned')[0]}; C {pm.params['C']:g} "
               f"({pm.params['c_source']} {pm.params['inner_seasons']}); probability = the "
               "model's own")  # fmt: skip
    problems = hp.snapshot_mismatches(frames, _root(METRICS_CSV), _root(FIRINGS_CSV))
    if problems:
        typer.echo(
            f"not usable: the frozen backtest disagrees with the reports in {len(problems)} "
            "places, e.g. " + "; ".join(problems[:3]),
            err=True,
        )
        raise typer.Exit(code=1)
    rows = ", ".join(f"{pin.backtest[t].rows:,} {t}" for t in pins.BACKTEST_TABLES)
    typer.echo(f"  frozen backtest {pin.backtest_seasons}: {rows} (sha256 and rows checked); "
               f"matches {METRICS_CSV} (main/logit) and {FIRINGS_CSV.name}")  # fmt: skip


@hotseat_app.command("score")
def score_cmd(
    season: int | None = typer.Option(None, "--season", help="Season (default: current)."),
    week: int | None = typer.Option(
        None,
        "--week",
        help="Week (default: the latest whose Tuesday as-of has passed); the "
        "last regular-season week = the end-of-season snapshot.",
    ),  # fmt: skip
    db: Path | None = typer.Option(None, "--db", help="Warehouse (default: config paths)."),
    store: Path | None = typer.Option(None, "--store", help="Predictions store (default config)."),
    out: Path | None = typer.Option(
        None, "--out", help="Report (default: reports/hot_seat/weekly/<season>-W<nn>.md)."
    ),
    allow_incomplete: bool = typer.Option(
        False, "--allow-incomplete", help="Score although some of the week's data is missing."
    ),
    now: str | None = typer.Option(
        None, "--now", help="Pretend it is this UTC time (ISO); the list is then never 'live'."
    ),
    top: int = typer.Option(5, "--top", help="Coaches printed."),
) -> None:
    """The Hot-Seat list of a week with the APPROVED model (config/production_models.yaml
    `hot_seat`: sha256 checked before the file is opened; nothing fitted): every current head
    coach's probability of a positive departure, rank and top 3 drivers, interims flagged.
    Exit 3 when the week's data has not arrived or the end-of-season snapshot is not due. A
    stored live list is never overwritten (docs/hot_seat.md "Production")."""
    import polars as pl

    from twm import pins
    from twm import predictions as pr
    from twm.cli import _clock, _display_path, _project_path, _warehouse_or_exit
    from twm.config import ROOT, settings
    from twm.modules.hot_seat import labels as hl
    from twm.modules.hot_seat import production as hp
    from twm.modules.hot_seat import targets as ht
    from twm.modules.hot_seat import weekly as hw

    path = _warehouse_or_exit(db)
    clock = _clock(now)
    s = _season(season)
    try:
        wk = week if week is not None else hw.rw.default_week(path, s, clock)
    except LookupError as e:
        typer.echo(f"cannot score: {e}", err=True)
        raise typer.Exit(code=1) from e
    if wk < hw.FIRST_WEEK:
        typer.echo(f"{s} week {wk}: no Hot-Seat list (the first one is week {hw.FIRST_WEEK}); "
                   "nothing stored")  # fmt: skip
        return
    try:
        pm, pin = hp.load_pinned(s)
    except pins.PinError as e:
        typer.echo(f"not usable: {e}", err=True)
        raise typer.Exit(code=1) from None
    man = settings().path("manual")
    feats = hw.season_features(path, s, clock, labels=hl.read_labels(man / LABELS_CSV),
                               candidates=hl.read_candidates(man / CANDIDATES_CSV))  # fmt: skip
    try:
        names = dict(ht.load_coach_names(path).iter_rows())
        run = hw.run_week(path, s, wk, model=pm, now=clock, allow_incomplete=allow_incomplete,
                          real_clock=now is None, feats=feats, names=names)  # fmt: skip
    except (hw.NotDueError, hw.rw.NotReadyError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=hw.EXIT_NOT_READY) from e
    except (LookupError, ValueError) as e:
        typer.echo(f"cannot score: {e}", err=True)
        raise typer.Exit(code=1) from e
    store_path = _project_path(store if store is not None else pr.default_path())
    try:
        counts = hw.store_week(run, store_path, created_at=clock.replace(tzinfo=None))
    except pr.LiveWeekError as e:
        typer.echo(f"not stored: {e}", err=True)
        raise typer.Exit(code=1) from e
    command = f"uv run twm hotseat score --season {s} --week {wk}" + (
        " --allow-incomplete" if allow_incomplete else "")  # fmt: skip
    text = hw.build_report(run, generated=f"Generated at {clock:%Y-%m-%d %H:%M} UTC.",
                           command=command)  # fmt: skip
    target = _project_path(out) if out is not None else hw.report_path(s, wk)
    hw.write_report(text, target)
    what = "end-of-season snapshot" if run.snapshot == "end_of_season" else f"week {wk}"
    typer.echo(f"{s} {what}, as-of {run.as_of:%a %Y-%m-%d %H:%M} UTC: '{run.kind}' list of "
               f"{run.table.height} coaches with {pin.model_version}"
               + (" (INCOMPLETE DATA)" if run.incomplete else ""))  # fmt: skip
    for r in run.table.sort("rank").head(top).iter_rows(named=True):
        ds = "; ".join(hw.driver_text(d) for d in hw.json.loads(r["drivers_json"]))
        flag = " [interim]" if r["is_interim"] else ""
        typer.echo(f"  {r['rank']}. {r['team']} {r['coach_name'] or r['coach_id']}{flag}"
                   f": {r['prob']:.1%} ({ds})")  # fmt: skip
    n_int = run.table.filter(pl.col("is_interim")).height
    if n_int:
        typer.echo(f"  {n_int} interim coach(es) scored and flagged: {hw.INTERIM_NOTE}")
    if counts["kept"]:
        typer.echo(f"a live list of this week is already stored ({counts['kept']} rows): kept, "
                   "nothing written (live lists are append-only)")  # fmt: skip
    else:
        typer.echo(f"stored {counts['predictions']:,} predictions as '{run.kind}' in "
                   f"{_display_path(store_path, ROOT)}")  # fmt: skip
    typer.echo(f"wrote {_display_path(target, ROOT)}")
