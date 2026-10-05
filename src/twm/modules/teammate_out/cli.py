"""`twm teammate_out build | pin | weekly | grade` and the module's part of `twm model check`
(feature #5, docs/teammate_out.md)."""

from __future__ import annotations

from pathlib import Path

import typer

from twm.modules.questionable.cli import _root, _season, _warehouse, _when, game_week

teammate_out_app = typer.Typer(
    help="Teammate out: when a starter sits, which teammates get his work and how much (a "
    "frozen allocation table, docs/teammate_out.md).",
    no_args_is_help=True,
)
REPORTS = Path("reports/teammate_out")


def _built(season: int, db: Path, candidate: str | None):
    from twm.modules.teammate_out import history as hs
    from twm.modules.teammate_out import production as tp

    con = hs.connect(db)
    try:
        seasons = list(range(hs.FIRST_SEASON, season))
        pg = con.execute(hs.players_sql(seasons)).pl()
        cands = con.execute(hs.candidates_sql(seasons)).pl()
    finally:
        con.close()
    outs = hs.out_starters(cands)
    bg = hs.baseline_games(outs, pg)
    mates = hs.teammates(hs.vacated(outs, bg, pg), bg, pg)
    try:
        return tp.build(cands, mates, season, candidate)
    except tp.TeammateOutProductionError as e:
        typer.echo(f"not built: {e}", err=True)
        raise typer.Exit(code=1) from None


def _content(b, reason: str | None) -> dict:
    from twm.modules.teammate_out import production as tp

    try:
        return tp.content(b, reason)
    except tp.TeammateOutProductionError as e:
        typer.echo(f"not built: {e}", err=True)
        raise typer.Exit(code=1) from None


_SEASON = typer.Option(None, "--season", help="Season the table scores (default: current).")
_DB = typer.Option(None, "--db", help="Warehouse (default: settings).")
_OUT = typer.Option(REPORTS, "--out", help="Report folder.")
_CAND = typer.Option(None, "--candidate", help="Override the fixed rule's choice (needs --reason).")
_REASON = typer.Option(None, "--reason", help="Who decided the override, when and why.")


@teammate_out_app.command("build")
def build_cmd(season: int | None = _SEASON, db: Path | None = _DB, out: Path = _OUT,
              candidate: str | None = _CAND, reason: str | None = _REASON) -> None:  # fmt: skip
    """Run the event study and the walk-forward backtest of every candidate, fit the table
    and write reports/teammate_out/*.csv (nothing is pinned)."""
    from twm.modules.teammate_out import production as tp

    c = _content(_built(_season(season), _warehouse(db), candidate), reason)
    for p in tp.write_reports(c, _root(out)):
        typer.echo(f"wrote {p}")
    typer.echo(f"{tp.version_of(c)}: {tp.Spec(c).describe()}")


@teammate_out_app.command("pin")
def pin_cmd(season: int | None = _SEASON, db: Path | None = _DB, out: Path = _OUT,
            candidate: str | None = _CAND, reason: str | None = _REASON) -> None:  # fmt: skip
    """Rebuild the table and approve it: refused unless it reproduces the committed
    reports/teammate_out/; writes artifacts/production_models/teammate_out/<version>.json and
    its pin. Review, then commit the file, the pin and the reports together."""
    from twm.modules.teammate_out import production as tp

    c = _content(_built(_season(season), _warehouse(db), candidate), reason)
    try:
        pin = tp.approve(c, report_dir=_root(out))
    except tp.TeammateOutProductionError as e:
        typer.echo(f"not approved: {e}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"pinned {pin.module} {pin.season}: {pin.model_version} ({pin.file}, sha256 "
               f"{pin.sha256[:12]}...)")  # fmt: skip


def _pct(x: object) -> str:
    return "-" if x is None else f"{float(x):.0%}"  # type: ignore[arg-type]


def _pts(x: object) -> str:
    return "?" if x is None else f"{float(x):.1f}"  # type: ignore[arg-type]


@teammate_out_app.command("weekly")
def weekly_cmd(
    season: int | None = typer.Option(None, "--season", help="Season (default: the as-of's)."),
    week: int | None = typer.Option(None, "--week", help="Game week (default: the next games)."),
    as_of: str | None = typer.Option(None, "--as-of", help="ISO time, UTC unless it says so."),
    db: Path | None = _DB,
    store: Path | None = typer.Option(
        None, "--store", help="Predictions store (default: settings)."
    ),
    write: bool = typer.Option(True, "--store-snapshot/--no-store", help="Store the snapshot."),
) -> None:
    """The week's absent starters and their teammates' predicted shares, points and 80%
    ranges with the pinned table; stored as that as-of's snapshot (append-only)."""
    from twm import pins
    from twm import predictions as pr
    from twm.modules.teammate_out import production as tp
    from twm.modules.teammate_out import weekly as wk

    path, when = _warehouse(db), _when(as_of)
    try:
        s, w = game_week(path, when)
    except LookupError as e:
        typer.echo(f"not ready: {e}", err=True)
        raise typer.Exit(code=3) from None
    s, w = (season or s), (week or w)
    try:
        spec, _ = tp.load_pinned(s)
    except pins.PinError as e:
        typer.echo(f"not usable: {e}", err=True)
        raise typer.Exit(code=1) from None
    rows = wk.list_rows(path, spec, s, w, when)
    teams = rows.unique(["team"], maintain_order=True)
    typer.echo(f"Teammate out, {s} week {w}, as of {when:%Y-%m-%d %H:%M} UTC "
               f"({spec.model_version}, {spec.chosen}): {teams.height} team(s), "
               f"{rows.height} teammate(s)")  # fmt: skip
    for t in teams.iter_rows(named=True):
        typer.echo(f"  {t['team']} vs {t['opponent']} {t['kickoff_utc']:%a %H:%M} UTC: "
                   f"{t['out_players']} ({t['out_positions']}; {t['out_reasons']})")  # fmt: skip
        for r in rows.filter(rows["team"] == t["team"]).iter_rows(named=True):
            typer.echo(f"    {r['player']} {r['role']}: carries {_pct(r['pred_carry_share'])} "
                       f"(was {_pct(r['base_carry_share'])}), targets "
                       f"{_pct(r['pred_target_share'])} (was {_pct(r['base_target_share'])}); "
                       f"{r['pred_points']:.1f} pts (usual {r['base_points']:.1f}), 80% range "
                       f"{_pts(r['points_lo'])}-{_pts(r['points_hi'])}")  # fmt: skip
    typer.echo(wk.NOTE)
    if write:
        target = store if store is not None else pr.default_path()
        res = wk.store_snapshot(target, rows, season=s, week=w, as_of=when,
                                model_version=spec.model_version)  # fmt: skip
        typer.echo(f"snapshot: {res['rows']} row(s) written, {res['kept']} kept ({target})")


@teammate_out_app.command("grade")
def grade_cmd(season: int | None = _SEASON, db: Path | None = _DB,
              store: Path | None = typer.Option(None, "--store", help="Predictions store.")
              ) -> None:  # fmt: skip
    """The live record so far: each teammate's last row before his kickoff vs his game
    (read-only)."""
    from twm import predictions as pr
    from twm.modules.teammate_out import weekly as wk

    s = _season(season)
    snaps = wk.read_snapshots(store if store is not None else pr.default_path(), s)
    res = wk.summary(wk.grade(snaps, wk.read_actuals(_warehouse(db), s)) if snaps.height
                     else snaps)  # fmt: skip
    if not res["n"]:
        typer.echo(f"{s}: nothing graded yet ({res['pending']} pending)")
        return
    typer.echo(f"{s}: {res['n']} graded ({res['pending']} pending, {res['starter_played']} "
               f"starter played, {res['did_not_play']} did not play): points MAE "
               f"{res['mae_points']:.2f} vs {res['mae_points_base']:.2f} for his usual; 80% "
               f"range coverage {_pct(res['coverage'])}; top gainer "
               f"{_pct(res['top_hit'])}")  # fmt: skip


def check_pin(season: int, report_dir: Path = REPORTS) -> None:
    """`twm model check` for Teammate out (exit 1 on any problem): the pin present, the JSON
    as pinned (sha256 before it is read, version = the hash of its content, season), and its
    rows equal to the committed reports/teammate_out/. Changes nothing."""
    from twm import pins
    from twm.modules.teammate_out import production as tp

    try:
        spec, pin = tp.load_pinned(season)
    except pins.PinError as e:
        typer.echo(f"not usable: {e}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"{pin.module} {season}: approved {pin.model} {pin.model_version} ({pin.file}, "
               f"sha256 {pin.sha256[:12]}..., approved {pin.approved or '?'})")  # fmt: skip
    typer.echo(f"  {spec.describe()}")
    body = {k: v for k, v in spec.content.items() if k != "model_version"}
    problems = tp.report_mismatches(body, _root(report_dir))
    if problems:
        head = f"not usable: the approved table disagrees with {report_dir} in {len(problems)}"
        typer.echo(f"{head} places, e.g. " + "; ".join(problems[:3]), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"  matches {report_dir}/{{{','.join(tp.REPORTS)}}}.csv and report.md")
