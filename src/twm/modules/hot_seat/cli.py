"""`twm hotseat ...`: the Hot-Seat Meter's commands (registered in twm.cli)."""

from __future__ import annotations

from pathlib import Path

import typer

hotseat_app = typer.Typer(
    help="Hot-Seat Meter: candidate head-coach departures from the schedules, the check of "
    "the owner-verified labels (docs/labeling_coaches.md), the point-in-time features and the "
    "walk-forward backtest (docs/hot_seat.md)."
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


PROVISIONAL_DIR = Path("data/hot_seat/provisional")
REPORT_DIR = Path("reports/hot_seat")


@hotseat_app.command("backtest")
def backtest_cmd(
    labels_mode: str = typer.Option(
        ..., "--labels", help="verified (owner-checked rows only) or suggested (provisional)."
    ),
    db: Path | None = typer.Option(None, "--db", help="Warehouse (default: config paths)."),
    features: Path | None = typer.Option(None, "--features", help=f"Default {FEATURES_PATH}."),
    n_boot: int = typer.Option(2000, "--n-boot", help="Season-bootstrap resamples."),
) -> None:
    """Walk-forward backtest 2006-2025 (spec 8.5). Suggested labels write ONLY to
    data/hot_seat/provisional/ with a PROVISIONAL banner; reports/hot_seat/ needs verified."""
    import time

    import polars as pl

    from twm.cli import _warehouse_or_exit
    from twm.config import ROOT
    from twm.modules.hot_seat import backtest as hb
    from twm.modules.hot_seat import backtest_report as hr
    from twm.modules.hot_seat import labels as hl
    from twm.modules.hot_seat import targets as ht

    if labels_mode not in ht.LABEL_MODES:
        raise typer.BadParameter(f"--labels must be one of {', '.join(ht.LABEL_MODES)}")
    t0 = time.perf_counter()
    path = _warehouse_or_exit(db)
    fpath = features or ROOT / FEATURES_PATH
    lpath, cpath = _manual(LABELS_CSV), _manual(CANDIDATES_CSV)
    for p, how in (
        (fpath, "twm hotseat features"),
        (lpath, "H1"),
        (cpath, "twm hotseat candidates"),
    ):
        if not p.exists():
            typer.echo(f"not found: {p} (run {how})", err=True)
            raise typer.Exit(code=1)
    labels, cands = hl.read_labels(lpath), hl.read_candidates(cpath)
    assert cands is not None
    used = (
        labels if labels_mode == "suggested" else labels.filter(pl.col("verified_by_owner") == "y")
    )
    deps, unresolved = ht.resolve_departures(labels, cands, ht.load_coach_names(path))
    if unresolved.height:
        typer.echo(f"{unresolved.height} departure(s) without a coach_id: "
                   f"{unresolved['candidate_id'].to_list()}", err=True)  # fmt: skip
        raise typer.Exit(code=1)
    feats = ht.refresh_interim(pl.read_parquet(fpath), used, cands)
    last = max(hb.TEST_SEASONS)
    feats = feats.filter(pl.col("season").is_between(hb.FIRST_SEASON, last))
    team_dates = ht.load_team_dates(path, hb.FIRST_SEASON, last)
    try:
        rows, counts = ht.build_targets(feats, deps, team_dates, mode=labels_mode)
    except ht.UnverifiedLabelsError as e:
        typer.echo(f"refused: {e}", err=True)
        raise typer.Exit(code=1) from e
    rup, _ = ht.build_targets(
        feats, deps, team_dates, mode=labels_mode, positive_types=ht.POSITIVE_SETS["rup_positive"]
    )
    typer.echo(", ".join(f"{k} {v}" for k, v in counts.items()))
    typer.echo(f"{counts['departures_date_imputed']} departure(s) with a blank announced_date: "
               "the coach's last_game_date used (docs/hot_seat.md)")  # fmt: skip
    res = hb.run_backtest(
        rows,
        rup,
        n_boot=n_boot,
        progress=lambda m: typer.echo(f"{m} ({time.perf_counter() - t0:.0f} s)"),
    )
    if labels_mode == "verified":
        out_dir, data_dir = ROOT / REPORT_DIR, ROOT / FEATURES_PATH.parent
    else:
        out_dir = data_dir = ROOT / PROVISIONAL_DIR
        typer.echo("PROVISIONAL: suggested labels; outputs only in " + str(out_dir))
    paths = hr.write_outputs(res, counts, labels_mode, out_dir, data_dir)
    rows.write_parquet(data_dir / "targets.parquet", compression="zstd", statistics=False)
    typer.echo(res.lgbm_text)
    for p in paths.values():
        typer.echo(f"wrote {p}")
    typer.echo(f"done in {time.perf_counter() - t0:.0f} s")


@hotseat_app.command("research")
def research_cmd(
    db: Path | None = typer.Option(None, "--db", help="Warehouse (default: config paths)."),
    features: Path | None = typer.Option(None, "--features", help=f"Default {FEATURES_PATH}."),
    n_boot: int = typer.Option(2000, "--n-boot", help="Season-bootstrap resamples."),
    n_perm: int = typer.Option(1000, "--n-perm", help="Within-season permutations."),
    out_dir: Path | None = typer.Option(None, "--out-dir", help=f"Default {REPORT_DIR}."),
) -> None:
    """H5 research question (spec 8.5): does poor fourth-down decision quality raise firing
    risk, controlling for performance vs expectation? Verified labels only; writes
    reports/hot_seat/research.md + research.csv (docs/research_decisions_vs_firings.md)."""
    import time

    from twm.cli import _warehouse_or_exit
    from twm.config import ROOT
    from twm.modules.hot_seat import research as rs
    from twm.modules.hot_seat import research_report as rr
    from twm.modules.hot_seat import targets as ht

    t0 = time.perf_counter()
    path = _warehouse_or_exit(db)
    fpath = features or ROOT / FEATURES_PATH
    lpath, cpath = _manual(LABELS_CSV), _manual(CANDIDATES_CSV)
    for p in (fpath, lpath, cpath):
        if not p.exists():
            typer.echo(f"not found: {p}", err=True)
            raise typer.Exit(code=1)
    try:
        main, rup, counts = rs.load_rows(path, fpath, lpath, cpath)
    except (ht.UnverifiedLabelsError, ValueError) as e:
        typer.echo(f"refused: {e}", err=True)
        raise typer.Exit(code=1) from e
    fourth, clock = rs.load_stored_grades()
    res = rs.run_research(
        main,
        rup,
        rs.decision_extras(fourth, clock),
        n_boot=n_boot,
        n_perm=n_perm,
        progress=lambda m: typer.echo(f"{m} ({time.perf_counter() - t0:.0f} s)"),
    )
    paths = rr.write_outputs(
        res, counts, rs.grade_counts(fourth, clock), out_dir or ROOT / REPORT_DIR
    )
    for p in paths.values():
        typer.echo(f"wrote {p}")
    typer.echo(f"done in {time.perf_counter() - t0:.0f} s")
