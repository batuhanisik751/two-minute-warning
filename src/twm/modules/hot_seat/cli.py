"""`twm hotseat ...`: the Hot-Seat Meter's commands (registered in twm.cli)."""

from __future__ import annotations

from pathlib import Path

import typer

hotseat_app = typer.Typer(
    help="Hot-Seat Meter: candidate head-coach departures from the schedules, the check of "
    "the owner-verified labels (docs/labeling_coaches.md) and the point-in-time features "
    "(docs/hot_seat.md)."
)

CANDIDATES_CSV = "coach_departures_candidates.csv"
LABELS_CSV = "coach_departures.csv"


def _manual(name: str) -> Path:
    from twm.config import settings

    return settings().path("manual") / name


@hotseat_app.command("candidates")
def candidates_cmd(
    db: Path | None = typer.Option(None, "--db", help="Warehouse (default: config paths)."),
    out: Path | None = typer.Option(None, "--out", help=f"Default data/manual/{CANDIDATES_CSV}."),
    first_season: int = typer.Option(2002, "--first-season", help="First departure season."),
    last_season: int = typer.Option(2026, "--last-season", help="Last departure season."),
) -> None:
    """Every head-coach change per team from the schedule's per-game coach columns."""
    import polars as pl

    from twm.cli import _warehouse_or_exit
    from twm.modules.hot_seat import candidates as hc

    games = hc.load_games(_warehouse_or_exit(db))
    df = hc.departure_candidates(games, first_season=first_season, last_season=last_season)
    path = hc.write_candidates(df, out or _manual(CANDIDATES_CSV))
    with pl.Config(tbl_rows=-1, tbl_hide_dataframe_shape=True, tbl_hide_column_data_types=True):
        typer.echo(hc.season_counts(df))
    kinds = {k: int((df["change_kind"] == k).sum()) for k in hc.CHANGE_KINDS}
    typer.echo(
        f"{df.height} rows ({', '.join(f'{k} {v}' for k, v in kinds.items())}); "
        f"interim_suspected {int(df['interim_suspected'].sum())}, data_gap_suspected "
        f"{int(df['data_gap_suspected'].sum())}, coach_returns {int(df['coach_returns'].sum())}"
    )
    typer.echo(f"wrote {path}")


@hotseat_app.command("check-labels")
def check_labels_cmd(
    labels: Path | None = typer.Option(None, "--labels", help=f"Default data/manual/{LABELS_CSV}."),
    candidates: Path | None = typer.Option(
        None, "--candidates", help=f"Default data/manual/{CANDIDATES_CSV}."
    ),
) -> None:
    """Validate the owner's departure labels; exit 1 when a row is invalid."""
    from twm.modules.hot_seat import labels as hl

    lpath = labels or _manual(LABELS_CSV)
    cpath = candidates or _manual(CANDIDATES_CSV)
    if not lpath.exists():
        typer.echo(f"labels file not found: {lpath}", err=True)
        raise typer.Exit(code=1)
    report = hl.check_labels(hl.read_labels(lpath), hl.read_candidates(cpath))
    typer.echo(hl.summary_text(report))
    if report.errors:
        raise typer.Exit(code=1)


FEATURES_PATH = Path("data/hot_seat/features.parquet")


@hotseat_app.command("features")
def features_cmd(
    season: int | None = typer.Option(None, "--season", help="One season (else --start/--end)."),
    start: int = typer.Option(2002, "--start", help="First season."),
    end: int | None = typer.Option(None, "--end", help="Last season (default: current)."),
    db: Path | None = typer.Option(None, "--db", help="Warehouse (default: config paths)."),
    out: Path | None = typer.Option(None, "--out", help=f"Default {FEATURES_PATH}."),
    now: str | None = typer.Option(
        None, "--now", help="Only as-ofs up to this UTC time (ISO; default: now)."
    ),
) -> None:
    """Point-in-time Hot-Seat features (docs/hot_seat.md): one row per coach x team x as-of."""
    import time
    from datetime import UTC, datetime

    import polars as pl

    from twm.cli import _warehouse_or_exit
    from twm.config import ROOT, settings
    from twm.modules.hot_seat import features as hf
    from twm.modules.hot_seat import labels as hl

    t0 = time.perf_counter()
    seasons = (
        [season]
        if season is not None
        else list(range(start, (end or settings().current_season) + 1))
    )
    when = datetime.fromisoformat(now) if now else datetime.now(UTC)
    when = when if when.tzinfo else when.replace(tzinfo=UTC)
    path = out or ROOT / FEATURES_PATH
    df = hf.features_history(
        _warehouse_or_exit(db), seasons, now=when, progress=lambda m: typer.echo(m)
    )
    lpath, cpath = _manual(LABELS_CSV), _manual(CANDIDATES_CSV)
    labels = hl.read_labels(lpath) if lpath.exists() else None
    cands = hl.read_candidates(cpath) if cpath.exists() else None
    if labels is None or cands is None:
        typer.echo("no owner departure file: is_interim = took_over_mid_season only", err=True)
    df = hf.add_interim_flag(df, labels, cands)
    old = pl.read_parquet(path) if path.exists() else None
    if old is not None and old.columns != df.columns:  # another layout: rebuilt from scratch
        old = None
    full = hf.merge_seasons(old, df, seasons)
    hf.write_features(full, path)
    with pl.Config(tbl_rows=-1, tbl_hide_dataframe_shape=True, tbl_hide_column_data_types=True):
        typer.echo(hf.rows_per_season(df))
        typer.echo(hf.null_rates(df))
    typer.echo(f"{df.height} rows ({full.height} in the file) in {time.perf_counter() - t0:.1f} s")
    typer.echo(f"wrote {path}")
