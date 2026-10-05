"""`twm questionable build | pin | weekly | grade` and the module's part of `twm model check`
(feature #1, docs/questionable.md)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import typer

questionable_app = typer.Typer(
    help="Questionable outcomes: will a tagged player play, and does he score like usual if he "
    "does (a frozen lookup table, docs/questionable.md).",
    no_args_is_help=True,
)


def _season(season: int | None) -> int:
    from twm.config import settings

    return int(season) if season is not None else int(settings().current_season)


def _root(p: Path) -> Path:
    from twm.config import ROOT

    return p if p.is_absolute() else ROOT / p


def _warehouse(db: Path | None) -> Path:
    from twm.config import settings

    path = Path(db) if db is not None else settings().path("warehouse")
    if not path.exists():
        typer.echo(f"warehouse not found: {path} (run `uv run twm build`)", err=True)
        raise typer.Exit(code=1)
    return path


def _built(season: int, db: Path):
    from twm.modules.questionable import history as hs
    from twm.modules.questionable import production as qp

    con = hs.connect(db)
    try:
        seasons = list(range(hs.FIRST_SEASON, season))
        tagged, played = hs.read_tagged(con, seasons), hs.read_played(con, seasons)
    finally:
        con.close()
    return qp.content(qp.build(tagged, played, season))


@questionable_app.command("build")
def build_cmd(
    season: int | None = typer.Option(
        None, "--season", help="Season the table scores (default: current); fit on 2016..season-1."
    ),
    db: Path | None = typer.Option(None, "--db", help="Warehouse (default: settings)."),
    out: Path = typer.Option(Path("reports/questionable"), "--out", help="Report folder."),
) -> None:
    """Build the table, its walk-forward backtest, calibration and "if he plays" buckets from
    the warehouse and write reports/questionable/*.csv (nothing is pinned)."""
    from twm.modules.questionable import production as qp

    c = _built(_season(season), _warehouse(db))
    for p in qp.write_reports(c, _root(out)):
        typer.echo(f"wrote {p}")
    typer.echo(f"{qp.version_of(c)}: {qp.Spec(c).describe()}")
    if c["practice_dropped"]:
        typer.echo(f"  practice dropped: {c['with_practice']} lost to {c['grouping']} in "
                   f"{c['check_season']}")  # fmt: skip


@questionable_app.command("pin")
def pin_cmd(
    season: int | None = typer.Option(None, "--season", help="Season (default: current)."),
    db: Path | None = typer.Option(None, "--db", help="Warehouse (default: settings)."),
    out: Path = typer.Option(Path("reports/questionable"), "--out", help="Report folder."),
) -> None:
    """Rebuild the table and approve it: refused unless it reproduces the committed
    reports/questionable/*.csv; writes artifacts/production_models/questionable/<version>.json
    and its pin. Review, then commit the file, the pin and the reports together."""
    from twm.modules.questionable import production as qp

    c = _built(_season(season), _warehouse(db))
    try:
        pin = qp.approve(c, report_dir=_root(out))
    except qp.QuestionableProductionError as e:
        typer.echo(f"not approved: {e}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"pinned {pin.module} {pin.season}: {pin.model_version} ({pin.file}, sha256 "
               f"{pin.sha256[:12]}...)")  # fmt: skip


def game_week(db: Path, when: datetime) -> tuple[int, int]:
    """The regular-season week whose games are next at ``when``: the week whose window
    [the previous week's as-of, its own as-of) contains ``when`` (dim_week)."""
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    try:
        con.execute("SET TimeZone='UTC'")
        row = con.execute(
            "SELECT season, week FROM dim_week WHERE season_type = 'REG' AND window_start_utc "
            "<= ? AND ? < window_end_utc ORDER BY season DESC, week LIMIT 1",
            [when.replace(tzinfo=None), when.replace(tzinfo=None)],
        ).fetchone()
    finally:
        con.close()
    if row is None:
        raise LookupError(f"no regular-season week's window contains {when:%Y-%m-%d %H:%M} UTC")
    return int(row[0]), int(row[1])


def _when(as_of: str | None) -> datetime:
    if as_of is None:
        return datetime.now(UTC)
    t = datetime.fromisoformat(as_of)
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)


def _pct(x: object) -> str:
    return "-" if x is None else f"{float(x):.0%}"  # type: ignore[arg-type]


@questionable_app.command("weekly")
def weekly_cmd(
    season: int | None = typer.Option(None, "--season", help="Season (default: the as-of's)."),
    week: int | None = typer.Option(
        None, "--week", help="Game week (default: the next games at the as-of)."
    ),  # fmt: skip
    as_of: str | None = typer.Option(
        None, "--as-of", help="ISO time, UTC unless it says otherwise (default: now)."
    ),  # fmt: skip
    db: Path | None = typer.Option(None, "--db", help="Warehouse (default: settings)."),
    store: Path | None = typer.Option(
        None, "--store", help="Predictions store (default: settings paths.predictions)."
    ),  # fmt: skip
    write: bool = typer.Option(True, "--store-snapshot/--no-store", help="Store the snapshot."),
) -> None:
    """The week's Questionable / Doubtful list at the as-of with the pinned table; stored as
    that as-of's snapshot (append-only)."""
    from twm import pins
    from twm import predictions as pr
    from twm.modules.questionable import production as qp
    from twm.modules.questionable import weekly as wk

    path, when = _warehouse(db), _when(as_of)
    try:
        s, w = game_week(path, when)
    except LookupError as e:
        typer.echo(f"not ready: {e}", err=True)
        raise typer.Exit(code=3) from None
    s, w = (season or s), (week or w)
    try:
        spec, _ = qp.load_pinned(s)
    except pins.PinError as e:
        typer.echo(f"not usable: {e}", err=True)
        raise typer.Exit(code=1) from None
    rows = wk.list_rows(path, spec, s, w, when)
    typer.echo(f"Questionable / Doubtful, {s} week {w}, as of {when:%Y-%m-%d %H:%M} UTC "
               f"({spec.model_version}): {rows.height} player(s)")  # fmt: skip
    for r in rows.iter_rows(named=True):
        plays = ("if he plays: median " + _pct(r["plays_median"]) + " of usual, dud rate "
                 + _pct(r["plays_dud_rate"]) + " (healthy " + _pct(r["healthy_dud_rate"]) + ")"
                 if r["plays_n"] else "if he plays: too few past cases")  # fmt: skip
        ppg = "-" if r["season_ppg"] is None else f"{r['season_ppg']:.1f}"
        typer.echo(f"  {r['player']} {r['position']} {r['team']} vs {r['opponent']} "
                   f"{r['kickoff_utc']:%a %H:%M} UTC: {r['report_status']}, practice "
                   f"{r['practice']}, missed last game {'yes' if r['missed_prev'] else 'no'}: "
                   f"{_pct(r['play_chance'])} to play; {plays}; {ppg} PPG")  # fmt: skip
    typer.echo(wk.INACTIVES_NOTE)
    if write:
        target = store if store is not None else pr.default_path()
        res = wk.store_snapshot(target, rows, season=s, week=w, as_of=when,
                                model_version=spec.model_version)  # fmt: skip
        typer.echo(f"snapshot: {res['rows']} row(s) written, {res['kept']} kept ({target})")


@questionable_app.command("grade")
def grade_cmd(
    season: int | None = typer.Option(None, "--season", help="Season (default: current)."),
    db: Path | None = typer.Option(None, "--db", help="Warehouse (default: settings)."),
    store: Path | None = typer.Option(
        None, "--store", help="Predictions store (default: settings paths.predictions)."
    ),  # fmt: skip
) -> None:
    """The live record so far: each player's last snapshot before his kickoff, graded played /
    did not play (read-only)."""
    from twm import predictions as pr
    from twm.modules.questionable import weekly as wk

    s = _season(season)
    snaps = wk.read_snapshots(store if store is not None else pr.default_path(), s)
    res = wk.summary(wk.grade(snaps, wk.read_game_snaps(_warehouse(db), s)) if snaps.height
                     else snaps.with_columns())  # fmt: skip
    typer.echo(f"{s}: {res['n']} graded ({res['pending']} pending): average chance "
               f"{_pct(res['predicted'])}, played {_pct(res['actual'])}")  # fmt: skip


def check_pin(season: int, report_dir: Path = Path("reports/questionable")) -> None:
    """`twm model check` for Questionable (exit 1 on any problem): the pin present, the JSON
    as pinned (sha256 before it is read, version = the hash of its content, season), and its
    backtest, calibration and "if he plays" rows equal to the committed CSVs. Changes nothing."""
    from twm import pins
    from twm.modules.questionable import production as qp

    try:
        spec, pin = qp.load_pinned(season)
    except pins.PinError as e:
        typer.echo(f"not usable: {e}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"{pin.module} {season}: approved {pin.model} {pin.model_version} ({pin.file}, "
               f"sha256 {pin.sha256[:12]}..., approved {pin.approved or '?'})")  # fmt: skip
    typer.echo(f"  {spec.describe()}")
    body = {k: v for k, v in spec.content.items() if k != "model_version"}
    problems = qp.report_mismatches(body, _root(report_dir))
    if problems:
        head = f"not usable: the approved table disagrees with {report_dir} in {len(problems)}"
        typer.echo(f"{head} places, e.g. " + "; ".join(problems[:3]), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"  matches {report_dir}/{{{','.join(qp.REPORTS)}}}.csv")
