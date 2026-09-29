"""`twm streamer ...`: the K and D/ST streamer's commands (registered in twm.cli)."""

from __future__ import annotations

from pathlib import Path

import typer

streamer_app = typer.Typer(
    help="K and D/ST streamer: the pool of kickers and team defenses probably on waivers at a "
    "Tuesday as-of, and their next-week labels (docs in src/twm/modules/streamer/)."
)

_TABLE = dict(
    tbl_rows=-1,
    tbl_cols=-1,
    tbl_width_chars=200,
    float_precision=1,
    tbl_hide_dataframe_shape=True,
    tbl_hide_column_data_types=True,
)


def _warehouse(db: Path | None) -> Path:
    from twm.cli import _warehouse_or_exit

    return _warehouse_or_exit(db)


def _positions(pos: str | None) -> list[str] | None:
    if pos is None:
        return None
    p = pos.upper().replace("D/ST", "DST")
    if p not in ("K", "DST"):
        raise typer.BadParameter("--pos must be K or DST")
    return [p]


@streamer_app.command("pool")
def streamer_pool(
    season: int = typer.Argument(..., help="Season, e.g. 2023."),
    week: int = typer.Argument(..., help="Regular-season week; the pool at its Tuesday as-of."),
    pos: str | None = typer.Option(None, "--pos", help="Only this position (K or DST)."),
    method: str = typer.Option(
        "auto", "--method", help="auto, ecr (preseason cheat sheet) or prior_ppg (fallback)."
    ),
    show_all: bool = typer.Option(
        False, "--all", help="Also list entities kept out of the pool, with the reason."
    ),
    limit: int = typer.Option(30, "--limit", help="Rows to print (0 = all)."),
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
) -> None:
    """Print the K and DST pool at a week's Tuesday as-of, best points per game first."""
    import duckdb
    import polars as pl

    from twm.asof import WarehouseTooOldError
    from twm.modules.streamer.pool import pool_as_of_week

    path = _warehouse(db)
    if method not in ("auto", "ecr", "prior_ppg"):
        raise typer.BadParameter("--method must be auto, ecr or prior_ppg")
    try:
        when, df = pool_as_of_week(path, season, week, method=method, positions=_positions(pos))
    except (LookupError, ValueError, WarehouseTooOldError, duckdb.Error) as e:
        typer.echo(f"cannot compute the pool: {e}", err=True)
        raise typer.Exit(code=1) from e
    for p in df.get_column("position").unique(maintain_order=True).to_list():
        part = df.filter(pl.col("position") == p)
        n_pool = int(part.get_column("in_pool").sum())
        methods = ", ".join(sorted(part.get_column("method").unique().to_list()))
        typer.echo(
            f"{season} week {week}, as-of {when:%Y-%m-%d %H:%M} UTC, {p}, method {methods}: "
            f"{n_pool} in the pool of {part.height}"
        )
    if not show_all:
        df = df.filter(pl.col("in_pool"))
    df = df.sort(
        ["position", "ppg_to_date", "entity_id"], descending=[True, True, False], nulls_last=True
    )
    cols = ["position", "entity_id", "name", "team", "status", "games_to_date", "ppg_to_date",
            "ppg_pos_rank", "preseason_source", "preseason_pos_rank", "in_pool", "excluded_by",
            "owned_avg"]  # fmt: skip
    shown = df.select(cols) if limit == 0 else df.select(cols).head(limit)
    with pl.Config(**_TABLE):  # type: ignore[arg-type]
        typer.echo(str(shown))


@streamer_app.command("labels")
def streamer_labels(
    season: int = typer.Argument(..., help="Season, e.g. 2023."),
    week: int = typer.Argument(..., help="Regular-season week N; the pool at its Tuesday as-of."),
    pos: str | None = typer.Option(None, "--pos", help="Only this position (K or DST)."),
    show_all: bool = typer.Option(False, "--all", help="Also list entities outside the pool."),
    limit: int = typer.Option(30, "--limit", help="Rows to print (0 = all)."),
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
) -> None:
    """Print one as-of's labelled pool: each entity's week N+1 points, rank and y_start.

    y_start = a top-(teams x lineup slots) finish at the position in week N+1 (K 12 and DST 12
    in a 12-team league); NULL on a bye, after the last regular-season week, or while pending.
    """
    import duckdb
    import polars as pl

    from twm.asof import WarehouseTooOldError
    from twm.modules.streamer.labels import label_rows
    from twm.modules.streamer.pool import pool_as_of_week

    path = _warehouse(db)
    try:
        when, pool = pool_as_of_week(path, season, week, positions=_positions(pos))
        df = label_rows(path, pool)
    except (LookupError, ValueError, WarehouseTooOldError, duckdb.Error) as e:
        typer.echo(f"cannot label the pool: {e}", err=True)
        raise typer.Exit(code=1) from e
    if not show_all:
        df = df.filter(pl.col("in_pool"))
    for p in df.get_column("position").unique(maintain_order=True).to_list():
        part = df.filter(pl.col("position") == p)
        counts = part.get_column("label_status").value_counts(sort=True)
        statuses = ", ".join(
            f"{r['label_status']} {r['count']}" for r in counts.iter_rows(named=True)
        )
        n_start = int(part.get_column("y_start").fill_null(False).sum())
        typer.echo(
            f"{season} week {week} -> week {week + 1}, as-of {when:%Y-%m-%d %H:%M} UTC, {p}: "
            f"{part.height} {'entities' if show_all else 'in the pool'}, {n_start} y_start "
            f"({statuses})"
        )
    df = df.sort(
        ["position", "next_pos_rank", "entity_id"], descending=[True, False, False],
        nulls_last=True,
    )  # fmt: skip
    cols = ["position", "entity_id", "name", "team", "in_pool", "ppg_to_date", "label_week",
            "label_status", "played_next", "next_points", "next_pos_rank", "n_ranked",
            "y_start"]  # fmt: skip
    shown = df.select(cols) if limit == 0 else df.select(cols).head(limit)
    with pl.Config(**_TABLE):  # type: ignore[arg-type]
        typer.echo(str(shown))


@streamer_app.command("pool-labels-report")
def streamer_pool_labels_report(
    start: int = typer.Option(2012, "--start", help="First season."),
    end: int = typer.Option(2025, "--end", help="Last season."),
    out: Path = typer.Option(
        Path("reports/streamer"), "--out", help="Output folder (relative: under the project root)."
    ),
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
) -> None:
    """Write pool_labels.md and .csv: pool sizes and y_start base rates by position and season."""
    from twm.config import ROOT
    from twm.modules.streamer.report import write_report

    path = _warehouse(db)
    if end < start:
        raise typer.BadParameter("--end is before --start")
    target = out if out.is_absolute() else ROOT / out
    md, csv, summary = write_report(path, target, list(range(start, end + 1)))
    typer.echo(f"{summary.height} position-seasons")
    typer.echo(f"wrote {md}")
    typer.echo(f"wrote {csv}")
