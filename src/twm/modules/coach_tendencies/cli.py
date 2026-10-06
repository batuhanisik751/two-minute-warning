"""`twm coach tendencies` (feature #10, docs/coach_tendencies.md): one coach's tendencies season by
season with his career line, or one season's league table. Reads the warehouse (read-only,
through an AsOfView at ``--as-of``, default now); writes nothing."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import typer

from twm.modules.coach_tendencies.season import METRICS
from twm.modules.questionable.cli import _warehouse, _when

coach_app = typer.Typer(
    help="Head coaches: how each offense plays (coach tendencies, docs/coach_tendencies.md).",
    no_args_is_help=True,
)
SHORT = {"neutral_pass_rate": "pass", "early_down_pass_rate": "early", "proe": "PROE",
         "neutral_sec_per_play": "pace", "no_huddle_rate": "no-huddle", "shotgun_rate": "shotgun",
         "fourth_go_rate": "4th go", "fourth_short_go_rate": "4th-short"}  # fmt: skip
PACE = "neutral_sec_per_play"
LEGEND = ("(nn) = percentile in that season, '-' = not ranked (small sample); pace: seconds per "
          "play, (nn) = faster than nn% of offenses; PROE in percentage points")  # fmt: skip


def fmt(metric: str, value: float | None) -> str:
    """A metric's value as the site shows it: rates in %, PROE in points, pace in seconds."""
    if value is None:
        return "-"
    if metric == "proe":
        return f"{value:+.1f}"
    if metric == PACE:
        return f"{value:.1f}s"
    return f"{value:.0%}"


def cell(metric: str, value: float | None, pct: float | None) -> str:
    """'62% (71)': the value and its percentile (pace: the share of offenses it is faster than)."""
    if pct is None:
        return f"{fmt(metric, value)} (-)"
    return f"{fmt(metric, value)} ({100 - pct if metric == PACE else pct:.0f})"


def wide(rows: pl.DataFrame) -> pl.DataFrame:
    """One row per (coach_id, team, season) with a text cell per metric (the season frame)."""
    cells = rows.with_columns(
        pl.struct("metric", "value", "percentile").map_elements(
            lambda r: cell(r["metric"], r["value"], r["percentile"]), return_dtype=pl.String
        ).alias("cell")
    )  # fmt: skip
    keys = ["coach_id", "team", "season", "through_week", "plays", "is_current"]
    out = cells.pivot("metric", index=keys, values="cell")
    for m in METRICS:
        if m not in out.columns:
            out = out.with_columns(pl.lit(None, pl.String).alias(m))
    return out.with_columns(pl.col(m).fill_null("n/a") for m in METRICS).sort("season", "team")


def _line(parts: list[str], widths: list[int]) -> str:
    return "  ".join(p.ljust(w) for p, w in zip(parts, widths, strict=True)).rstrip()


def find_coach(names: pl.DataFrame, query: str) -> pl.DataFrame:
    """dim_coach rows (coach_id, coach_name) matching ``query``: a warehouse id ('andy_reid'), the
    site's id ('andy-reid') or part of the name ('reid'), case-insensitive."""
    q = query.strip().lower()
    exact = names.filter(pl.col("coach_id") == q.replace("-", "_"))
    if exact.height:
        return exact
    return names.filter(pl.col("coach_name").str.to_lowercase().str.contains(q, literal=True))


def table_lines(w: pl.DataFrame, first: str, label: pl.Expr) -> list[str]:
    """The wide rows as aligned text lines (``label``: the first column)."""
    head = [first, "team", "wk", "snaps", *[SHORT[m] for m in METRICS]]
    body = [[str(x) for x in r] for r in w.select(
        label, "team", "through_week", "plays", *METRICS).iter_rows()]  # fmt: skip
    widths = [max(len(r[i]) for r in [head, *body]) for i in range(len(head))]
    return [_line(head, widths), *(_line(r, widths) for r in body)]


_SEASON = typer.Option(None, "--season", help="The season (default: the newest with plays).")
_COACH = typer.Option(None, "--coach", help="A coach: 'andy_reid', 'andy-reid' or part of a name.")
_DB = typer.Option(None, "--db", help="Warehouse (default: settings).")
_AS_OF = typer.Option(None, "--as-of", help="Point in time, ISO (default: now).")


@coach_app.command("tendencies")
def tendencies_cmd(season: int | None = _SEASON, coach: str | None = _COACH,
                   db: Path | None = _DB, as_of: str | None = _AS_OF) -> None:  # fmt: skip
    """A coach's tendencies season by season and his career line (--coach), else the season's
    league table: neutral pass rate, PROE, pace, no-huddle, shotgun, fourth-down go rates."""
    from twm.asof import AsOfView
    from twm.modules.coach_tendencies import build as bd

    with AsOfView(_warehouse(db), _when(as_of)) as v:
        newest = bd.latest_season(v)
        frames = bd.build(v, n_boot=1)  # the CLI prints no interval
        names = v.sql("SELECT coach_id, coach_name FROM dim_coach")
    w = wide(frames["coach_tendency_season"]).join(names, on="coach_id", how="left")
    if coach is None:
        year = newest if season is None else int(season)
        rows = w.filter(pl.col("season") == year).sort("team")
        if rows.height == 0:
            typer.echo(f"no coach-tendency rows for {year}", err=True)
            raise typer.Exit(code=1)
        part = " (season in progress)" if rows["is_current"].any() else ""
        typer.echo(f"Coach tendencies {year}{part}, regular season; {LEGEND}")
        for line in table_lines(rows, "coach", pl.col("coach_name")):
            typer.echo(line)
        return
    found = find_coach(names, coach)
    if found.height != 1:
        what = "no coach matches" if found.height == 0 else "several coaches match"
        typer.echo(f"{what} {coach!r}: {found['coach_name'].to_list()[:8]}", err=True)
        raise typer.Exit(code=1)
    cid, name = found.row(0)
    mine = w.filter((pl.col("coach_id") == cid)
                    & ((pl.col("season") == season) if season is not None else True))  # fmt: skip
    typer.echo(f"{name} ({cid}): {mine.height} coach-team seasons; {LEGEND}")
    for line in table_lines(mine, "season", pl.col("season").cast(pl.String)):
        typer.echo(line)
    car = frames["coach_tendency_career"].filter(pl.col("coach_id") == cid)
    if car.height:
        r0 = car.row(0, named=True)
        vs = {r["metric"]: r for r in car.iter_rows(named=True)}
        parts = [f"{SHORT[m]} {fmt(m, vs[m]['value'])} (league {fmt(m, vs[m]['league_avg'])})"
                 for m in METRICS if m in vs]  # fmt: skip
        typer.echo(f"career {r0['first_season']}-{r0['last_season']} ({r0['teams']}, completed "
                   f"seasons): " + "; ".join(parts))  # fmt: skip


_PIN_SEASON = typer.Option(None, "--season", help="The season in progress the history is frozen "
                           "for (default: settings current_season).")  # fmt: skip


@coach_app.command("tendencies-pin")
def tendencies_pin_cmd(season: int | None = _PIN_SEASON, db: Path | None = _DB,
                       as_of: str | None = _AS_OF) -> None:  # fmt: skip
    """Freeze every completed season's coach tendencies (1999 to the season before --season)
    from the FULL warehouse, on the owner's Mac: write the snapshot, the spec and
    reports/coach_tendencies/, and pin them as `coach_tendencies` in
    config/production_models.yaml (C10c). Refused on a partial warehouse. Review and commit."""
    from twm.asof import AsOfView
    from twm.config import settings
    from twm.modules.coach_tendencies import build as bd
    from twm.modules.coach_tendencies import production as prod

    current = int(season) if season is not None else int(settings().current_season)
    with AsOfView(_warehouse(db), _when(as_of)) as v:
        newest = min(bd.latest_season(v), current)
        frames = bd.build(v, range(bd.FIRST_SEASON, newest + 1), current)
        names = v.sql("SELECT coach_id, coach_name FROM dim_coach")
    try:
        pin = prod.approve(prod.freeze(frames, names, current), current)
    except prod.CoachTendencyPinError as e:
        typer.echo(f"not pinned: {e}", err=True)
        raise typer.Exit(code=1) from None
    rows = ", ".join(f"{s.rows:,} {t}" for t, s in pin.backtest.items())
    typer.echo(
        f"pinned {prod.PIN_KEY} {pin.model_version} for {current}: seasons "
        f"{pin.backtest_seasons} ({rows}); reports: {prod.REPORT_DIR.as_posix()}/"
    )
    typer.echo("review (the reports' diff), run `uv run twm model check coach_tendencies`, commit")


def check_pin(season: int) -> None:
    """`twm model check coach_tendencies` (and `all`): the pin, every file's sha256 and rows,
    the definitions it was frozen with, and reports/coach_tendencies/ reproduced from the
    snapshot (exit 1 on any problem)."""
    from twm.config import ROOT
    from twm.modules.coach_tendencies import production as prod
    from twm.pins import PinError

    try:
        history, pin = prod.load_pinned(int(season))
    except PinError as e:
        typer.echo(f"not usable: {e}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"{prod.PIN_KEY} {season}: frozen history {pin.model_version} ({pin.file}, sha256 "
               f"{pin.sha256[:12]}..., approved {pin.approved or '?'})")  # fmt: skip
    typer.echo(f"  {history.describe()} (sha256 and rows checked)")
    problems = prod.report_mismatches(history, ROOT / prod.REPORT_DIR)
    if problems:
        typer.echo("not usable: " + "; ".join(problems[:3]), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"  matches {prod.REPORT_DIR.as_posix()}/ (seasons, persistence, fantasy link)")
