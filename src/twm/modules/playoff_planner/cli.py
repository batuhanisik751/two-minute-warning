"""`twm playoff_planner build | pin | weekly | grade` and the module's part of `twm model check`
(feature #6, docs/playoff_planner.md)."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import typer

from twm.modules.questionable.cli import _root, _season, _warehouse, _when

playoff_planner_app = typer.Typer(
    help="Playoff planner: matchup ratings for the fantasy playoff weeks 15-17 and how much "
    "they matter (a frozen rating rule, docs/playoff_planner.md).",
    no_args_is_help=True,
)
REPORTS = Path("reports/playoff_planner")


def _overrides(candidate: list[str] | None) -> dict[str, str]:
    out = {}
    for item in candidate or []:
        pos, _, cand = item.partition("=")
        out[pos.strip().upper()] = cand.strip().lower()
    return out


def _built(season: int, db: Path, candidate: list[str] | None, reason: str | None) -> dict:
    from twm.modules.playoff_planner import backtest as bt
    from twm.modules.playoff_planner import history as hs
    from twm.modules.playoff_planner import production as pp
    from twm.modules.playoff_planner import ratings as rt

    con = hs.connect(db)
    try:
        seasons = [s for s in bt.TEST_SEASONS if s < season]
        units = con.execute(hs.units_sql(seasons)).pl()
        games = con.execute(hs.team_games_sql(seasons)).pl()
    finally:
        con.close()
    try:
        b = pp.build(units, rt.unit_totals(units, games), season, _overrides(candidate))
        return pp.content(b, reason)
    except pp.PlayoffPlannerProductionError as e:
        typer.echo(f"not built: {e}", err=True)
        raise typer.Exit(code=1) from None


_SEASON = typer.Option(None, "--season", help="Season the spec scores (default: current).")
_DB = typer.Option(None, "--db", help="Warehouse (default: settings).")
_OUT = typer.Option(REPORTS, "--out", help="Report folder.")
_CAND = typer.Option(None, "--candidate", help="POS=candidate: override the rule (needs --reason).")
_REASON = typer.Option(None, "--reason", help="Who decided the override, when and why.")


@playoff_planner_app.command("build")
def build_cmd(
    season: int | None = _SEASON,
    db: Path | None = _DB,
    out: Path = _OUT,
    candidate: list[str] | None = _CAND,
    reason: str | None = _REASON,
) -> None:
    """Run the walk-forward backtest of every candidate, choose per position by the fixed rule
    and write reports/playoff_planner/*.csv (nothing is pinned)."""
    from twm.modules.playoff_planner import production as pp

    c = _built(_season(season), _warehouse(db), candidate, reason)
    for p in pp.write_reports(c, _root(out)):
        typer.echo(f"wrote {p}")
    typer.echo(f"{pp.version_of(c)}: {pp.Spec(c).describe()}")


@playoff_planner_app.command("pin")
def pin_cmd(
    season: int | None = _SEASON,
    db: Path | None = _DB,
    out: Path = _OUT,
    candidate: list[str] | None = _CAND,
    reason: str | None = _REASON,
) -> None:
    """Rebuild the spec and approve it: refused unless it reproduces the committed
    reports/playoff_planner/; writes artifacts/production_models/playoff_planner/<version>.json
    and its pin. Review, then commit the file, the pin and the reports together."""
    from twm.modules.playoff_planner import production as pp

    c = _built(_season(season), _warehouse(db), candidate, reason)
    try:
        pin = pp.approve(c, report_dir=_root(out))
    except pp.PlayoffPlannerProductionError as e:
        typer.echo(f"not approved: {e}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"pinned {pin.module} {pin.season}: {pin.model_version} ({pin.file}, sha256 "
               f"{pin.sha256[:12]}...)")  # fmt: skip


def matters_line(spec, position: str) -> str:
    """The honest "how much it matters" line of a position (the pinned effect sizes)."""
    e = spec.effect(position)
    cand = spec.chosen.get(position, "none")
    if e is None:
        return f"{position}: no backtest effect stored"
    head = (f"{position}: best vs worst fifth of matchups: {e['realized_gap']:+.1f} pts/game "
            f"in weeks 15-17 (the raw as-of ratings said {e['rated_gap']:+.1f})")  # fmt: skip
    if cand == "none":
        return head + "; using ratings did not beat ignoring them: every matchup counts as 1.00"
    return head + f"; ratings used: {cand}"


def _weeks(text: str) -> tuple[int, ...]:
    return tuple(int(w) for w in text.split(",") if w.strip())


@playoff_planner_app.command("weekly")
def weekly_cmd(
    season: int | None = typer.Option(None, "--season", help="Season (default: current)."),
    as_of: str | None = typer.Option(None, "--as-of", help="ISO time, UTC unless it says so."),
    weeks: str = typer.Option("15,16,17", "--weeks", help="Fantasy playoff weeks."),
    db: Path | None = _DB,
    store: Path | None = typer.Option(
        None, "--store", help="Predictions store (default: settings)."
    ),
    write: bool = typer.Option(True, "--store-snapshot/--no-store", help="Store the snapshot."),
) -> None:
    """Every team's opponents in the playoff weeks with the pinned matchup ratings per position
    (completed weeks only); stored as that as-of's snapshot (append-only, one per completed
    week). Exit 3: nothing to do (no completed week, the weeks are over, or this completed
    week is stored already)."""
    from twm import pins
    from twm import predictions as pr
    from twm.modules.playoff_planner import history as hs
    from twm.modules.playoff_planner import production as pp
    from twm.modules.playoff_planner import weekly as wk

    s, path, when, wks = _season(season), _warehouse(db), _when(as_of), _weeks(weeks)
    try:
        spec, _ = pp.load_pinned(s)
    except pins.PinError as e:
        typer.echo(f"not usable: {e}", err=True)
        raise typer.Exit(code=1) from None
    target = store if store is not None else pr.default_path()
    rows = wk.grid_rows(path, spec, s, when, wks)
    if rows.is_empty():
        typer.echo(f"nothing to do: no completed {s} week at {when:%Y-%m-%d %H:%M} UTC")
        raise typer.Exit(code=3)
    tw = int(rows["through_week"][0])
    if tw >= max(wks):
        typer.echo(f"nothing to do: weeks {list(wks)} are complete (through week {tw})")
        raise typer.Exit(code=3)
    typer.echo(f"Playoff planner {s}, weeks {list(wks)}, as of {when:%Y-%m-%d %H:%M} UTC "
               f"(through week {tw}; {spec.model_version})")  # fmt: skip
    for pos in hs.POSITIONS:
        typer.echo(f"  {matters_line(spec, pos)}")
        for w in wks if spec.chosen.get(pos, "none") != "none" else ():
            p = rows.filter((pl.col("position") == pos) & (pl.col("week") == w)
                            & pl.col("opponent").is_not_null())  # fmt: skip
            p = p.sort("rating", "team", descending=[True, False])
            ends = [*p.head(3).iter_rows(named=True), *p.tail(1).iter_rows(named=True)]
            txt = ", ".join(f"{r['team']} vs {r['opponent']} {r['rating']:.2f}" for r in ends)
            typer.echo(f"    week {w}: easiest {txt} (hardest last)")
    typer.echo(wk.NOTE)
    if write:
        if tw in wk.stored_weeks(target, s):
            typer.echo(f"nothing to do: completed week {tw} of {s} is stored already ({target})")
            raise typer.Exit(code=3)
        res = wk.store_snapshot(target, rows, as_of=when, model_version=spec.model_version)
        typer.echo(f"snapshot: {res['rows']} row(s) written, {res['kept']} kept ({target})")


@playoff_planner_app.command("grade")
def grade_cmd(
    season: int | None = _SEASON,
    db: Path | None = _DB,
    store: Path | None = typer.Option(None, "--store", help="Predictions store."),
) -> None:
    """The live record so far: each team-week-position's last rating before its week vs
    the realized multiplier of that game (read-only)."""
    from twm import predictions as pr
    from twm.modules.playoff_planner import weekly as wk

    s = _season(season)
    snaps = wk.read_snapshots(store if store is not None else pr.default_path(), s)
    if snaps.is_empty():
        typer.echo(f"{s}: no snapshot stored")
        return
    res = wk.summary(wk.grade(snaps, wk.read_actuals(_warehouse(db), s)))
    for r in res.iter_rows(named=True):
        if not r["n"]:
            typer.echo(f"  {r['position']}: nothing graded yet ({r['pending']} pending)")
            continue
        typer.echo(f"  {r['position']}: {r['n']} graded ({r['pending']} pending): MAE of the "
                   f"realized multiplier {r['mae_rating']:.3f} with the ratings vs "
                   f"{r['mae_flat']:.3f} with a flat 1.0")  # fmt: skip


def check_pin(season: int, report_dir: Path = REPORTS) -> None:
    """`twm model check` for the playoff planner (exit 1 on any problem): the pin present, the
    JSON as pinned (sha256 before it is read, version = the hash of its content, season), and
    its rows equal to the committed reports/playoff_planner/. Changes nothing."""
    from twm import pins
    from twm.modules.playoff_planner import production as pp

    try:
        spec, pin = pp.load_pinned(season)
    except pins.PinError as e:
        typer.echo(f"not usable: {e}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"{pin.module} {season}: approved {pin.model} {pin.model_version} ({pin.file}, "
               f"sha256 {pin.sha256[:12]}..., approved {pin.approved or '?'})")  # fmt: skip
    typer.echo(f"  {spec.describe()}")
    body = {k: v for k, v in spec.content.items() if k != "model_version"}
    problems = pp.report_mismatches(body, _root(report_dir))
    if problems:
        head = f"not usable: the approved spec disagrees with {report_dir} in {len(problems)}"
        typer.echo(f"{head} places, e.g. " + "; ".join(problems[:3]), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"  matches {report_dir}/{{{','.join(pp.REPORTS)}}}.csv and report.md")
