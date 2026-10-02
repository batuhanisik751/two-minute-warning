"""`twm board ...`: the Cliff & Breakout Board's commands (registered in twm.cli; step I1b)."""

from __future__ import annotations

from pathlib import Path

import typer

board_app = typer.Typer(
    help="Cliff & Breakout Board: the end-of-season snapshot features, the Cliff and Breakout "
    "labels and the walk-forward backtest (docs/board.md)."
)

DEPARTURES_CSV = "coach_departures.csv"
REPORT_DIR = Path("reports/board")


def _dataset(db: Path, progress) -> object:
    """Build the dataset (own xFP from Regression Watch's folds, departures from the owner's
    file) and write it to data/board/dataset.parquet."""
    from twm.config import ROOT, settings
    from twm.modules.board import dataset as bd
    from twm.modules.board import features as bf
    from twm.modules.board import sources as src

    last = max(bd.complete_seasons(db))
    xfp = src.own_xfp_history(db, last, progress=progress)
    deps = bf.read_departures(settings().path("manual") / DEPARTURES_CSV)
    progress(f"departures: {deps.rows.height} rows, {deps.n_blank_date} blank dates "
             f"({deps.n_blank_unknown} never counted as known)")  # fmt: skip
    df = bd.build_dataset(db, xfp_games=xfp, departures=deps, progress=progress)
    path = bd.write_dataset(df, ROOT / bd.OUT_DIR)
    progress(f"wrote {path} ({df.height} rows)")
    return df


@board_app.command("dataset")
def dataset_cmd(
    db: Path | None = typer.Option(None, "--db", help="Warehouse (default: config paths)."),
) -> None:
    """Features at every end-of-season snapshot 2002 .. the last finished season, with labels."""
    import polars as pl

    from twm.cli import _warehouse_or_exit

    df = _dataset(_warehouse_or_exit(db), lambda m: typer.echo(m, err=True))
    assert isinstance(df, pl.DataFrame)
    counts = df.group_by("season").agg(
        pl.col("in_cliff").sum().alias("cliff"), pl.col("in_breakout").sum().alias("breakout")
    )
    with pl.Config(tbl_rows=-1, tbl_hide_dataframe_shape=True):
        typer.echo(counts.sort("season"))


@board_app.command("backtest")
def backtest_cmd(
    db: Path | None = typer.Option(None, "--db", help="Warehouse (default: config paths)."),
    variant: list[str] = typer.Option([], "--variant", help="Only these variants (repeatable)."),
    out: Path | None = typer.Option(None, "--out", help=f"Default {REPORT_DIR}."),
    resume: bool = typer.Option(
        False,
        "--resume",
        help="Reuse variants already evaluated on the same dataset "
        "(data/board/runs/); run one --variant at a time, then all to write the full reports.",
    ),
) -> None:
    """Rebuild the dataset, run the walk-forward of every variant (snapshots 2007-2024, labels
    2008-2025) and write reports/board/{cliff,breakout}.md + csv."""
    import time

    import duckdb
    import polars as pl

    from twm.cli import _warehouse_or_exit
    from twm.config import ROOT
    from twm.modules.board import backtest as bt
    from twm.modules.board import report as br

    t0 = time.perf_counter()
    say = lambda m: typer.echo(f"[{time.perf_counter() - t0:6.0f} s] {m}", err=True)  # noqa: E731
    known = bt.variants()
    bad = [v for v in variant if v not in known]
    if bad:
        raise typer.BadParameter(f"unknown variant(s) {bad}; known: {', '.join(known)}")
    path = _warehouse_or_exit(db)
    df = _dataset(path, say)
    assert isinstance(df, pl.DataFrame)
    cache = ROOT / "data/board/runs" if resume else None
    reps = br.run_all(df, path, tuple(variant) or None, progress=say, cache=cache)
    con = duckdb.connect(str(path), read_only=True)
    try:  # names for the report's tables only (never a feature)
        names = con.sql("SELECT gsis_id, display_name FROM dim_player").pl()
    finally:
        con.close()
    for p in br.write_reports(reps, out or ROOT / REPORT_DIR, names):
        typer.echo(f"wrote {p}")
