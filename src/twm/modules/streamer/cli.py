"""`twm streamer ...`: the K and D/ST streamer's commands (registered in twm.cli)."""

from __future__ import annotations

from pathlib import Path

import typer

streamer_app = typer.Typer(
    help="K and D/ST streamer: the pool of kickers and team defenses probably on waivers at a "
    "Tuesday as-of, their point-in-time features and next-week labels, and the training "
    "dataset (docs in src/twm/modules/streamer/)."
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


# Columns `twm streamer features` prints per position (every feature: `twm glossary <name>`).
FEATURE_VIEW = {
    "K": ["entity_id", "name", "team", "in_pool", "kdst_points_per_game", "is_team_kicker",
          "k_fg_att_per_game", "k_fg_pct_50_plus", "team_rz_trips_per_game",
          "team_rz_stall_rate", "next_opp_points_allowed_per_game",
          "next_opp_rz_stall_rate_forced", "next_is_home", "next_venue_dome",
          "weekly_ecr_rank"],
    "DST": ["entity_id", "in_pool", "kdst_points_per_game", "dst_sacks_per_game",
            "dst_takeaways_per_game", "dst_points_allowed_per_game", "next_opp_points_per_game",
            "next_opp_sacks_allowed_per_game", "next_opp_giveaways_per_game", "next_is_home",
            "weekly_ecr_rank"],
}  # fmt: skip


@streamer_app.command("features")
def streamer_features(
    season: int = typer.Argument(..., help="Season, e.g. 2023."),
    week: int = typer.Argument(..., help="Regular-season week N; the pool at its Tuesday as-of."),
    pos: str | None = typer.Option(None, "--pos", help="Only this position (K or DST)."),
    show_all: bool = typer.Option(False, "--all", help="Also list entities outside the pool."),
    limit: int = typer.Option(30, "--limit", help="Rows per position to print (0 = all)."),
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
) -> None:
    """Print one as-of's K and DST pool with key point-in-time features (reference path,
    through the as-of view), best points per game first. `twm glossary <feature>` explains
    each column."""
    import duckdb
    import polars as pl

    from twm.asof import WarehouseTooOldError
    from twm.modules.streamer.features import features_as_of_week

    path = _warehouse(db)
    try:
        when, df = features_as_of_week(path, season, week, positions=_positions(pos))
    except (LookupError, ValueError, KeyError, WarehouseTooOldError, duckdb.Error) as e:
        typer.echo(f"cannot compute the features: {e}", err=True)
        raise typer.Exit(code=1) from e
    if not show_all:
        df = df.filter(pl.col("in_pool"))
    for p in df.get_column("position").unique(maintain_order=True).to_list():
        part = df.filter(pl.col("position") == p).sort(
            ["kdst_points_per_game", "entity_id"], descending=[True, False], nulls_last=True
        )
        typer.echo(
            f"{season} week {week} (for week {week + 1}), as-of {when:%Y-%m-%d %H:%M} UTC, {p}: "
            f"{part.height} {'entities' if show_all else 'in the pool'}"
        )
        shown = part.select(FEATURE_VIEW[p])
        with pl.Config(**{**_TABLE, "float_precision": 2}):  # type: ignore[arg-type]
            typer.echo(str(shown if limit == 0 else shown.head(limit)))


@streamer_app.command("dataset")
def streamer_dataset(
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
    out: Path = typer.Option(
        Path("data/streamer/dataset.parquet"),
        "--out",
        help="Parquet file to write (relative paths are under the project root; data/ is "
        "gitignored).",
    ),
    start: int = typer.Option(2012, "--start", help="First season (the GitHub build's first)."),
    end: int | None = typer.Option(None, "--end", help="Last season (default: current)."),
) -> None:
    """Build the streamer's training dataset: pool rows + features + next-week labels +
    available_at, one row per (season, week, position, entity), sorted; deterministic (the
    same warehouse gives the same file). Train on label_status == 'final' only."""
    import time

    import duckdb
    import polars as pl

    from twm.asof import WarehouseTooOldError
    from twm.config import ROOT, settings
    from twm.modules.streamer.dataset import build_dataset, write_dataset

    path = _warehouse(db)
    last = end if end is not None else settings().current_season
    if start > last:
        raise typer.BadParameter(f"--start {start} is after --end {last}")
    t0 = time.perf_counter()
    try:
        df = build_dataset(path, list(range(start, last + 1)))
    except (LookupError, ValueError, KeyError, WarehouseTooOldError, duckdb.Error) as e:
        typer.echo(f"cannot build the dataset: {e}", err=True)
        raise typer.Exit(code=1) from e
    target = out if out.is_absolute() else ROOT / out
    write_dataset(df, target)
    secs = time.perf_counter() - t0
    for p in ("K", "DST"):
        part = df.filter(pl.col("position") == p)
        final = part.filter(pl.col("label_status") == "final")
        typer.echo(
            f"{p}: {part.height:,} rows ({int(part.get_column('in_pool').sum()):,} in the pool), "
            f"{final.height:,} with a final label ({int(final.get_column('y_start').sum()):,} "
            "y_start)"
        )
    typer.echo(
        f"{df.height:,} rows, {df.width} columns, seasons {start}-{last}, "
        f"{target.stat().st_size / 1e6:.1f} MB, {secs:.0f} s"
    )
    typer.echo(f"wrote {target}")


@streamer_app.command("backtest")
def streamer_backtest(
    dataset: Path = typer.Option(
        Path("data/streamer/dataset.parquet"),
        "--dataset",
        help="The dataset from `twm streamer dataset` (relative paths are under the project root).",
    ),
    start: int = typer.Option(2013, "--start", help="First test season (2012 trains it)."),
    end: int = typer.Option(2025, "--end", help="Last test season."),
    models: list[str] | None = typer.Option(
        None,
        "--model",
        help="Methods to run (repeatable; default all): baseline_last_points, "
        "baseline_ppg, baseline_opponent, logit, lgbm.",
    ),  # fmt: skip
    pos: str | None = typer.Option(None, "--pos", help="Only this position (K or DST)."),
    sensitivity: bool = typer.Option(
        True,
        "--sensitivity/--no-sensitivity",
        help="Also train the models on the whole universe (in or out of the pool) as context.",
    ),  # fmt: skip
    store: Path | None = typer.Option(
        None,
        "--store",
        help="Predictions store to write (a DuckDB file); default: not stored. "
        "S1 never writes the configured store (data/predictions.duckdb).",
    ),  # fmt: skip
    out: Path = typer.Option(
        Path("reports/streamer/backtest.md"),
        "--out",
        help="Markdown report (relative paths are under the project root); a CSV with the same "
        "name is written next to it.",
    ),  # fmt: skip
) -> None:
    """Walk-forward backtest of the streamer, per position: three baselines, logistic regression
    and LightGBM trained on earlier seasons only (labels public by the season's first as-of);
    writes the report and, with --store, the predictions."""
    import time

    import polars as pl

    from twm import predictions as pr
    from twm.backtest.walkforward import WalkForwardError
    from twm.config import ROOT
    from twm.modules.streamer import backtest as bt
    from twm.modules.streamer import backtest_report as rep
    from twm.modules.streamer.models import ALL_METHODS, MODELS, POSITIONS
    from twm.registry import FeatureCheckError

    source = dataset if dataset.is_absolute() else ROOT / dataset
    if not source.exists():
        typer.echo(f"dataset not found: {source}; run `twm streamer dataset` first", err=True)
        raise typer.Exit(code=1)
    if start > end:
        raise typer.BadParameter(f"--start {start} is after --end {end}")
    positions = _positions(pos) or list(POSITIONS)
    methods = list(models or ALL_METHODS)
    t0 = time.perf_counter()

    def progress(msg: str) -> None:
        typer.echo(f"  {msg} ({time.perf_counter() - t0:.0f} s)")

    df = pl.read_parquet(source)
    kw = dict(positions=positions, test_seasons=range(start, end + 1), progress=progress)
    try:
        run = bt.run_backtest(df, methods=methods, **kw)  # type: ignore[arg-type]
        alt_models = [m for m in methods if m in MODELS]
        alt = None
        if sensitivity and alt_models:
            alt = bt.run_backtest(df, methods=alt_models, train_on="universe", **kw)  # type: ignore[arg-type]
    except (WalkForwardError, FeatureCheckError, ValueError, KeyError) as e:
        typer.echo(f"cannot run the backtest: {e}", err=True)
        raise typer.Exit(code=1) from e
    created = pr.now_utc()
    args = ["uv run twm streamer backtest"]
    if (start, end) != (2013, 2025):
        args.append(f"--start {start} --end {end}")
    args += [f"--model {m}" for m in models or []]
    args += [f"--pos {pos}"] if pos else []
    args += [] if sensitivity else ["--no-sensitivity"]
    report = rep.build_report(
        run, alt=alt, command=" ".join(args), dataset_name=str(dataset),
        created=f"Created {created:%Y-%m-%d %H:%M} UTC in {time.perf_counter() - t0:.0f} s.",
    )  # fmt: skip
    target = out if out.is_absolute() else ROOT / out
    csv_path = rep.write_report(report, target)
    for line in report.summary:
        typer.echo(line)
    if store is not None:
        path = store if store.is_absolute() else ROOT / store
        preds, versions = bt.store_frames(run, created_at=created)
        counts = pr.write_predictions(path, predictions=preds, versions=versions)
        typer.echo(
            f"stored {counts['predictions']:,} predictions, {counts['model_versions']} model "
            f"versions in {path}"
        )
    typer.echo(f"wrote {target}")
    typer.echo(f"wrote {csv_path}")


def _approved(season: int):
    """(K model, D/ST rule, K confidence, D/ST confidence) from the committed pins; every file's
    sha256 checked before it is opened (twm.pins.PinError otherwise)."""
    from twm.modules.streamer import confidence as sc
    from twm.modules.streamer import production as sp

    pm, kpin = sp.load_pinned_k(season)
    rule, dpin = sp.load_pinned_rule(season)
    k_conf = sc.k_confidence(sp.load_k_snapshot(kpin), season)
    d_conf = sc.dst_confidence(sp.load_hit_rates(dpin), season)
    return pm, rule, k_conf, d_conf


@streamer_app.command("score")
def streamer_score(
    season: int | None = typer.Option(None, "--season", help="Season (default: current)."),
    week: int | None = typer.Option(
        None,
        "--week",
        help="Week N: the list at its Tuesday as-of (default: the latest week "
        "whose as-of has passed).",
    ),  # fmt: skip
    allow_incomplete: bool = typer.Option(
        False,
        "--allow-incomplete",
        help="Score even if some of the week's data has not arrived (the list is marked).",
    ),  # fmt: skip
    limit: int = typer.Option(5, "--limit", help="Picks per position to print."),
    db: Path | None = typer.Option(None, "--db", help="Warehouse file (default from config)."),
    store: Path | None = typer.Option(
        None, "--store", help="Predictions store (default: settings paths.predictions)."
    ),
    backtest_csv: Path = typer.Option(
        Path("reports/streamer/backtest.csv"),
        "--backtest-csv",
        help="The committed backtest (the list's notes on each method quote it).",
    ),  # fmt: skip
    out: Path | None = typer.Option(
        None, "--out", help="Report (default: reports/streamer/weekly/<season>-W<nn>.md)."
    ),
    now: str | None = typer.Option(
        None,
        "--now",
        hidden=True,
        help="Pretend it is this UTC time (ISO); the list is then never stored as 'live'.",
    ),  # fmt: skip
) -> None:
    """Rank a week's kickers and D/STs with the owner-approved methods (config/
    production_models.yaml: the K model and the D/ST rule, sha256-checked): checks the week's
    data (exit code 3 if it has not arrived), stores the list, writes the weekly report and
    prints the top of each position (docs/streamer.md 'The weekly list')."""
    import polars as pl

    from twm import pins
    from twm import predictions as pr
    from twm.asof import WarehouseTooOldError, weekly_as_of
    from twm.cli import _clock, _display_path, _project_path
    from twm.config import ROOT, settings
    from twm.modules.streamer import weekly as sw

    path = _warehouse(db)
    clock = _clock(now)
    chosen = season if season is not None else settings().current_season
    try:
        wk = week if week is not None else sw.rw.default_week(path, chosen, clock)
        as_of = weekly_as_of(path, chosen, wk)
    except LookupError as e:
        typer.echo(f"cannot score: {e}", err=True)
        raise typer.Exit(code=1) from e
    fresh = sw.check_freshness(sw.freshness_inputs(path, chosen, wk), chosen, wk, as_of, clock)
    if not fresh.ok and not allow_incomplete:
        typer.echo(fresh.message(), err=True)
        raise typer.Exit(code=sw.EXIT_NOT_READY)
    store_path = _project_path(store if store is not None else pr.default_path())
    try:
        pm, rule, k_conf, d_conf = _approved(chosen)
        run = sw.run_week(
            path, chosen, wk, k_model=pm, rule=rule, k_conf=k_conf, d_conf=d_conf, now=clock,
            allow_incomplete=allow_incomplete, real_clock=now is None,
        )  # fmt: skip
    except sw.rw.NotReadyError as e:  # pragma: no cover - checked above; data changed meanwhile
        typer.echo(str(e), err=True)
        raise typer.Exit(code=sw.EXIT_NOT_READY) from e
    except (pins.PinError, WarehouseTooOldError, LookupError, ValueError) as e:
        typer.echo(f"cannot score: {e}", err=True)
        raise typer.Exit(code=1) from e
    try:
        counts = sw.store_week(run, store_path, created_at=clock.replace(tzinfo=None))
    except pr.LiveWeekError as e:
        typer.echo(f"not stored: {e}", err=True)
        raise typer.Exit(code=1) from e
    command = f"uv run twm streamer score --season {chosen} --week {wk}" + (
        " --allow-incomplete" if allow_incomplete else "")  # fmt: skip
    notes = sw.method_notes(_project_path(backtest_csv), chosen)
    text = sw.build_report(run, generated=f"Generated at {clock:%Y-%m-%d %H:%M} UTC.",
                           command=command, notes=notes)  # fmt: skip
    target = _project_path(out) if out is not None else sw.report_path(chosen, wk)
    sw.write_report(text, target)
    typer.echo(f"{chosen} week {wk}, as-of {as_of:%a %Y-%m-%d %H:%M} UTC: stored as '{run.kind}'"
               + (" (INCOMPLETE DATA)" if run.incomplete else ""))  # fmt: skip
    typer.echo(f"K: {pm.model_version} (approved); D/ST: {rule.model_version} (approved rule)")
    cols = ["rank", "name", "team", "chance", "tier"]
    with pl.Config(**_TABLE):  # type: ignore[arg-type]
        for pos in ("K", "DST"):
            top = run.scored.filter((pl.col("position") == pos) & (pl.col("rank") <= limit))
            typer.echo(f"{pos}:\n{top.select(cols).with_columns(pl.col('chance') * 100)}")
    typer.echo(f"stored {counts['predictions']:,} predictions in {_display_path(store_path, ROOT)}")
    typer.echo(f"wrote {_display_path(target, ROOT)}")


@streamer_app.command("pin")
def streamer_pin(
    season: int | None = typer.Option(None, "--season", help="Season (default: current)."),
    store: Path = typer.Option(
        ...,
        "--store",
        help="The predictions store holding the streamer backtest to approve "
        "(`twm streamer backtest --store`; opened read-only).",
    ),  # fmt: skip
    dataset: Path = typer.Option(
        Path("data/streamer/dataset.parquet"),
        "--dataset",
        help="The dataset the backtest was run on (its labels go into the snapshot).",
    ),  # fmt: skip
    backtest_csv: Path = typer.Option(
        Path("reports/streamer/backtest.csv"),
        "--backtest-csv",
        help="The committed backtest report the snapshots must reproduce.",
    ),  # fmt: skip
    pos: str | None = typer.Option(
        None,
        "--pos",
        help="Re-approve only this position (K or DST; default both): the other one's pin, "
        "file and snapshot stay byte for byte, and its pinned snapshot must still reproduce "
        "the report.",
    ),  # fmt: skip
) -> None:
    """Approve the streamer's production methods for a season: train the K model (the
    backtest's fold for that season), write the D/ST rule, export both frozen backtests to
    artifacts/production_models/streamer/ and pin them (streamer_k, streamer_dst) in
    config/production_models.yaml with sha256s. Refused unless the snapshots reproduce the
    committed backtest report. Review, then commit: `twm streamer score` uses only the pins."""
    import polars as pl

    from twm.cli import _display_path, _project_path
    from twm.config import ROOT, settings
    from twm.modules.streamer import production as sp

    chosen = season if season is not None else settings().current_season
    source = _project_path(dataset)
    if not source.exists():
        typer.echo(f"dataset not found: {source}", err=True)
        raise typer.Exit(code=1)
    try:
        made = sp.approve(pl.read_parquet(source), store=_project_path(store), season=chosen,
                          csv_path=_project_path(backtest_csv),
                          positions=_positions(pos) or ("K", "DST"))  # fmt: skip
        pm, _ = sp.load_pinned_k(chosen)
        sp.load_pinned_rule(chosen)
    except (sp.StreamerProductionError, ValueError) as e:
        typer.echo(f"not pinned: {e}", err=True)
        raise typer.Exit(code=1) from e
    typer.echo(f"pinned streamer {chosen}: {pm.describe()}")
    for pin in made.values():
        typer.echo(f"  {pin.module}: {pin.file} (model {pin.model}, sha256 {pin.sha256[:12]}...)")
        for t, b in pin.backtest.items():
            typer.echo(f"    {t}: {b.file} ({b.rows:,} rows, sha256 {b.sha256[:12]}...)")
    typer.echo(f"  wrote {_display_path(ROOT / 'config' / 'production_models.yaml', ROOT)}; "
               "review the files, then commit them")  # fmt: skip


def check_pins(season: int, backtest_csv: Path = Path("reports/streamer/backtest.csv")) -> None:
    """`twm model check` for the streamer (exit 1 on any problem): both pins present, the K
    model and the D/ST rule files as pinned (sha256 before opening, version, season), every
    snapshot file's sha256 and rows, and the snapshots reproduce the committed backtest
    report's rows of the production methods. Changes nothing."""
    from twm import pins
    from twm.cli import _project_path
    from twm.modules.streamer import production as sp

    try:
        pm, kpin = sp.load_pinned_k(season)
        rule, dpin = sp.load_pinned_rule(season)
        frames, hit_rates = sp.load_k_snapshot(kpin), sp.load_hit_rates(dpin)
    except pins.PinError as e:
        typer.echo(f"not usable: {e}", err=True)
        raise typer.Exit(code=1) from None
    for pin, what in ((kpin, pm.describe()), (dpin, rule.describe())):
        typer.echo(f"{pin.module} {season}: approved {pin.model} {pin.model_version} ({pin.file}, "
                   f"sha256 {pin.sha256[:12]}..., approved {pin.approved or '?'})")  # fmt: skip
        typer.echo(f"  {what}")
        rows = ", ".join(f"{b.rows:,} {t}" for t, b in pin.backtest.items())
        typer.echo(f"  backtest snapshot {pin.backtest_seasons}: {rows} (sha256 and rows checked)")
    problems = sp.track_record_mismatches(frames, hit_rates, _project_path(backtest_csv))
    if problems:
        typer.echo(
            f"not usable: the approved streamer backtest disagrees with {backtest_csv} in "
            f"{len(problems)} places, e.g. " + "; ".join(problems[:3]),
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(f"  matches {backtest_csv} (the production methods' rows the snapshots determine)")
