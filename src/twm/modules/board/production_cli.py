"""`twm board pin | score` and the board's part of `twm model check` (step I2c-a).

Registered on :data:`twm.modules.board.cli.board_app` when :mod:`twm.cli` imports this module
(kept apart from the research commands in ``cli.py``, like the Hot-Seat Meter's pins).
"""

from __future__ import annotations

from pathlib import Path

import typer

from twm.modules.board.cli import REPORT_DIR, board_app


def _season(season: int | None) -> int:
    from twm.config import settings

    return int(season) if season is not None else int(settings().current_season)


def _root(p: Path) -> Path:
    from twm.config import ROOT

    return p if p.is_absolute() else ROOT / p


@board_app.command("pin")
def pin_cmd(
    season: int | None = typer.Option(None, "--season", help="Board season (default: current)."),
    db: Path | None = typer.Option(None, "--db", help="Warehouse (default: config paths)."),
) -> None:
    """Approve the board of a season (owner, 2026-10-02: Cliff + missed-time chances at the
    kickoff-eve preseason snapshot) on the owner's Mac after `twm board backtest --snapshot
    preseason`: rebuild the preseason dataset, refit both primary models' walk-forward
    (cliff_main logit, cliff_missed logit_simple), freeze it under
    artifacts/production_models/board/ and refuse unless it reproduces
    reports/board/preseason_cliff.csv, _seasons.csv and the disagreement tables of
    preseason_cliff.md; train both folds of the snapshot before the season on every earlier
    labelled snapshot; pin the spec naming both files in config/production_models.yaml.
    Review, then commit."""
    import polars as pl

    from twm.cli import _warehouse_or_exit
    from twm.modules.board import production as bp
    from twm.modules.board.cli import _dataset

    s = _season(season)
    path = _warehouse_or_exit(db)
    df = _dataset(path, lambda m: typer.echo(m, err=True), "preseason")
    assert isinstance(df, pl.DataFrame)
    try:
        pin, models = bp.approve(df, path, season=s, report_dir=_root(REPORT_DIR))
    except (bp.BoardProductionError, ValueError) as e:
        typer.echo(f"not approved: {e}", err=True)
        raise typer.Exit(code=1) from None
    rows = ", ".join(f"{f.rows:,} {t}" for t, f in pin.backtest.items())
    typer.echo(f"pinned {pin.module} {pin.season}: {pin.model_version} ({pin.file})")
    for role, pm in models.items():
        typer.echo(f"  {role}: {pm.model_version}, C {pm.params['C']:g} "
                   f"({pm.params['c_source']}, {pm.params['inner_seasons']}), {pm.n_train:,} "
                   f"rows, {pm.n_train_pos:,} positive")  # fmt: skip
    cur = pin.backtest.get("current_board")
    board = (f"the {pin.season} board frozen in the pin ({cur.rows} players: its as-of had "
             "passed)" if cur else f"no {pin.season} board in the pin (its as-of has not passed: "
             "score it live with `twm board score`)")  # fmt: skip
    typer.echo(f"  frozen backtest (snapshots) {pin.backtest_seasons}: {rows}; {board}. Run "
               "`uv run twm model check board`, review, commit the pin and the files "
               "together.")  # fmt: skip


def check_pin(season: int) -> None:
    """`twm model check` for the board (exit 1 on any problem): the pin present; the spec and
    both model files as pinned (sha256 BEFORE a file is opened; version, model, label,
    features, snapshot season; the config's anchor); the frozen backtest as pinned (sha256
    before reading, rows), consistent, and reproducing the anchor's
    reports/board/preseason_cliff.csv rows of both models (values, intervals, shares),
    _seasons.csv and the disagreement tables of preseason_cliff.md. Changes nothing."""
    from twm import pins
    from twm.modules.board import preseason as pre
    from twm.modules.board import production as bp

    try:
        models, spec, pin = bp.load_pinned(season)
        frames = bp.load_snapshot(pin)
        current = bp.load_current(pin)
    except pins.PinError as e:
        typer.echo(f"not usable: {e}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"{pin.module} {season}: approved {pin.model} {pin.model_version} ({pin.file}, "
               f"sha256 {pin.sha256[:12]}..., approved {pin.approved or '?'}; anchor "
               f"{spec['anchor']})")  # fmt: skip
    for role, pm in models.items():
        typer.echo(f"  {role}: {pm.model_version} ({pm.model}/{pm.label}, snapshot {pm.season}, "
                   f"C {pm.params['C']:g}); probability = the model's own")  # fmt: skip
    prefix = pre.REPORT_PREFIX[spec["anchor"]]
    problems = bp.snapshot_mismatches(frames, _root(REPORT_DIR), prefix)
    if problems:
        typer.echo(
            f"not usable: the frozen backtest disagrees with the reports in {len(problems)} "
            "places, e.g. " + "; ".join(problems[:3]),
            err=True,
        )
        raise typer.Exit(code=1)
    rows = ", ".join(f"{pin.backtest[t].rows:,} {t}" for t in pins.BACKTEST_TABLES)
    where = f"{REPORT_DIR}/{prefix}cliff"
    typer.echo(f"  frozen backtest (snapshots) {pin.backtest_seasons}: {rows} (sha256 and "
               f"rows checked); matches {where}.csv, _seasons.csv and .md")  # fmt: skip
    if current is None:
        typer.echo(f"  no {season} board in the pin (its as-of had not passed at approval: "
                   "`twm board score` makes it live)")  # fmt: skip
        return
    problems = bp.current_mismatches(models, current)
    if problems:
        typer.echo("not usable: the pinned board of the season is not what the pinned models "
                   "give its frozen inputs: " + "; ".join(problems[:3]), err=True)  # fmt: skip
        raise typer.Exit(code=1)
    b = current["current_board"]
    typer.echo(f"  the {season} board frozen in the pin: {b.height} players (as-of "
               f"{b['as_of'].max():%Y-%m-%d %H:%M} UTC, reconstructed), sha256 and rows checked; "
               f"re-scoring its frozen inputs with the pinned models reproduces it")  # fmt: skip


@board_app.command("score")
def score_cmd(
    season: int | None = typer.Option(None, "--season", help="Board season (default: current)."),
    db: Path | None = typer.Option(None, "--db", help="Warehouse (default: config paths)."),
    store: Path | None = typer.Option(None, "--store", help="Predictions store (default config)."),
    now: str | None = typer.Option(
        None, "--now", help="Pretend it is this UTC time (ISO); the board is then never 'live'."
    ),
    top: int = typer.Option(10, "--top", help="Players printed."),
) -> None:
    """The Cliff board of a season (snapshot season - 1 at its preseason as-of) with the
    APPROVED models (config/production_models.yaml `board`: sha256 checked before a file is
    opened; nothing fitted): every Cliff player's chance of a cliff and of missing most of the
    season, both ranks, the ECR rank and the top 3 drivers. Exit 3 before the as-of. Stored as
    'live' only when scored between the as-of and the kickoff; a stored live board is never
    overwritten (docs/board.md "Production")."""
    from twm import pins
    from twm import predictions as pr
    from twm.cli import _clock, _display_path, _project_path, _warehouse_or_exit
    from twm.config import ROOT, settings
    from twm.modules.board import features as bf
    from twm.modules.board import live as bl
    from twm.modules.board import production as bp
    from twm.modules.board import sources as src
    from twm.modules.board.cli import DEPARTURES_CSV

    path = _warehouse_or_exit(db)
    clock = _clock(now)
    s = _season(season)
    try:
        models, spec, pin = bp.load_pinned(s)
    except pins.PinError as e:
        typer.echo(f"not usable: {e}", err=True)
        raise typer.Exit(code=1) from None
    say = lambda m: typer.echo(m, err=True)  # noqa: E731
    try:
        xfp = src.own_xfp_history(path, s - 1, progress=say)
        deps = bf.read_departures(settings().path("manual") / DEPARTURES_CSV)
        run = bl.run_board(path, s, models=models, anchor=spec["anchor"], now=clock,
                           real_clock=now is None, xfp_games=xfp, departures=deps)  # fmt: skip
    except bl.NotDueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=bl.EXIT_NOT_READY) from e
    except (LookupError, ValueError) as e:
        typer.echo(f"cannot score: {e}", err=True)
        raise typer.Exit(code=1) from e
    store_path = _project_path(store if store is not None else pr.default_path())
    try:
        counts = bl.store_board(run, store_path, created_at=clock.replace(tzinfo=None))
    except pr.LiveWeekError as e:
        typer.echo(f"not stored: {e}", err=True)
        raise typer.Exit(code=1) from e
    kind = "live" if run.kind == "live" else "reconstructed: scored after its as-of"
    typer.echo(f"{s} board (snapshot {s - 1}), as-of {run.as_of:%a %Y-%m-%d %H:%M} UTC "
               f"({spec['anchor']}): '{run.kind}' ({kind}) board of {run.table.height} Cliff "
               f"players with {pin.model_version}")  # fmt: skip
    con = __import__("duckdb").connect(str(path), read_only=True)
    try:  # names for this printout only
        names = dict(con.sql("SELECT gsis_id, display_name FROM dim_player").fetchall())
    finally:
        con.close()
    for r in run.table.sort("cliff_rank").head(top).iter_rows(named=True):
        ecr = "unranked" if r["ecr_rank"] is None else f"ECR {r['position']}{r['ecr_rank']}"
        typer.echo(f"  {r['cliff_rank']}. {names.get(r['gsis_id']) or r['gsis_id']} "
                   f"({r['position']}, {r['team']}): cliff {r['cliff_prob']:.1%}, missed "
                   f"{r['missed_prob']:.1%} (#{r['missed_rank']}); {ecr}")  # fmt: skip
    if counts["kept"]:
        typer.echo(f"a live board of this season is already stored ({counts['kept']} rows): "
                   "kept, nothing written (live boards are append-only)")  # fmt: skip
    else:
        typer.echo(f"stored {counts['predictions']:,} predictions as '{run.kind}' in "
                   f"{_display_path(store_path, ROOT)}")  # fmt: skip
    try:
        current = bp.load_current(pin) if pin.season == s else None
    except pins.PinError as e:
        typer.echo(f"not usable: {e}", err=True)
        raise typer.Exit(code=1) from None
    if current is not None:  # the pin froze this board at approval: the publish uses that one
        same = bp.same_board(run.table, current["current_board"])
        typer.echo("matches the board frozen in the pin (which every publish carries)" if same
                   else "WARNING: differs from the board frozen in the pin; every publish carries "
                   "the pin's (approve again to change it)")  # fmt: skip
