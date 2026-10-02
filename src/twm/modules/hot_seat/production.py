"""The production Hot-Seat Meter (step H4a): the approved live model, its pin and its frozen
walk-forward backtest.

**Which model** (owner, 2026-10-01, docs/progress.md): the L2 logistic regression (``logit`` of
the H3b backtest, :data:`twm.modules.hot_seat.models.SPECS`) on the season label ``y``, with the
labels the owner accepted (docs/hot_seat.md "Where the labels come from").

**The model for season S** is exactly the fold the walk-forward backtest would use for test
season S (:func:`twm.backtest.walkforward.fit_fold` with :func:`production_fold`, the harness
the backtest uses): every non-interim labelled row of the seasons before S (2002-2025 for
2026), C chosen inside the fit by the inner walk-forward (:class:`.models.InnerCvLogit`). The
model's own probability is the primary one (as in the backtest): the harness's isotonic
calibrator is not kept. It is saved as the Radar's
:class:`~twm.modules.waiver_radar.production.ProductionModel` (model 'logit', label 'y') with
the Radar's writer and reader (a pickle: opened only after its sha256 matches the pin).

**The frozen backtest** (``artifacts/production_models/hot_seat/backtest-<version>/``):
the verified H3b run's logit predictions of 2006-2025 (every test row, interims included) with
the fold's version, the row's rank, the key features the site shows and the top drivers;
the outcomes (``y``, ``censored``, the departure); one ``model_versions`` row per fold. The
runner never recomputes history: ``twm model check hot_seat`` checks the snapshot against
``reports/hot_seat/backtest_metrics.csv`` and ``firings_per_season.csv``.

**The pin** (``config/production_models.yaml`` entry ``hot_seat``, ``model: logit``):
written by ``twm hotseat pin`` (:func:`approve`).
"""

from __future__ import annotations

import json
import warnings
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from twm import predictions as pr
from twm.modules.hot_seat.models import FEATURES, KEYS, MODULE, SPECS, InnerCvLogit
from twm.modules.waiver_radar import production as rprod

PIN_KEY = "hot_seat"
MODEL = "logit"
LABEL = "y"
ARTIFACT_SUBDIR = "hot_seat"  # artifacts/production_models/hot_seat/
TOLERANCE = 1.01e-6  # the report CSVs print 6 decimals
N_DRIVERS = 3
# the key features a published row shows (beside the drivers), H3a names
SHOWN = ("reg_games_played", "reg_wins", "expected_wins", "wins_vs_expected",
         "point_diff_per_game", "tenure_seasons", "division_rank", "prev_season_wins",
         "consecutive_losing_seasons", "fourth_down_wp_lost_per_game")  # fmt: skip
CALIBRATION = "none: the model's own probability is primary (docs/hot_seat.md)"


class HotSeatProductionError(ValueError):
    """The production Hot-Seat model or its frozen backtest cannot be built or approved."""


def training_rows(targets: pl.DataFrame, season: int) -> pl.DataFrame:
    """The labelled rows the model for ``season`` learns from: every non-interim row of the
    seasons before it, sorted by the keys (the harness's order)."""
    rows = targets.filter((pl.col("season") < int(season)) & ~pl.col("is_interim"))
    return rows.sort(list(KEYS))


def dataset_hash(train: pl.DataFrame, seasons: Sequence[int]) -> str:
    """What a fold learned from: the content hash of its training rows, season by season."""
    cols = [*KEYS, *FEATURES, LABEL]
    return pr.combine_hashes(
        [pr.frame_hash(train.filter(pl.col("season") == s).select(cols)) for s in seasons]
    )


def version_record(fr: Any, train: pl.DataFrame, kind: str) -> dict[str, Any]:
    """The ``model_versions`` row of a fitted logit fold (``fr``: a FoldResult whose model is
    an :class:`InnerCvLogit` fit): its version hashes the model, label, features, the chosen C
    and how it was chosen, the training and test seasons and the training rows."""
    refit = dict(fr.model.refit_params)
    fixed = dict(SPECS[MODEL].make().fixed_params)
    params = {"C": float(refit["C"]), "l1_ratio": 0.0, "c_source": refit["c_source"],
              "inner_seasons": refit["inner_seasons"], "fixed": fixed}  # fmt: skip
    seasons = list(fr.fold.train_seasons)
    dhash = dataset_hash(train, seasons)
    version = pr.model_version(
        module=MODULE, model=MODEL, label=LABEL, features=FEATURES, params=params,
        training_seasons=seasons, test_season=fr.fold.test_season, dataset_hash=dhash,
    )  # fmt: skip
    loss = {str(c): round(float(v), 9) for c, v in (refit.get("inner_log_loss") or {}).items()}
    notes = {"kind": kind, "validation_season": fr.fold.val_season,
             "calibration": CALIBRATION, "n_train": fr.n_train, "n_train_pos": fr.n_train_pos,
             "inner_log_loss": loss}  # fmt: skip
    return {
        "model_version": version, "module": MODULE, "model": MODEL, "label": LABEL,
        "feature_list": json.dumps(sorted(FEATURES)), "params": json.dumps(params, sort_keys=True),
        "training_seasons": json.dumps(sorted(seasons)), "test_season": int(fr.fold.test_season),
        "dataset_hash": dhash, "notes": json.dumps(notes, sort_keys=True),
    }  # fmt: skip


def fit_live(targets: pl.DataFrame, season: int) -> tuple[Any, pl.DataFrame]:
    """(FoldResult, training rows) of the fold that scores ``season`` (module docstring)."""
    from twm.backtest.walkforward import fit_fold, production_fold
    from twm.modules.hot_seat.models import neg_log_loss

    rows = training_rows(targets, season)
    seasons = sorted({int(s) for s in rows.get_column("season").to_list()})
    if not seasons:
        raise HotSeatProductionError(f"no labelled Hot-Seat rows before {season}")
    fold = production_fold(seasons, season)
    train = rows.filter(pl.col("season").is_in(list(fold.train_seasons)))
    spec = SPECS[MODEL]
    est = spec.make()
    assert isinstance(est, InnerCvLogit)
    est.bind(train, spec.features, KEYS)  # the inner walk-forward reads the seasons from it
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Skipping features without any observed")
        fr = fit_fold(train, fold, estimator=est, features=spec.features, label=spec.label,
                      module=MODULE, keys=KEYS, tune_metric=neg_log_loss(spec.label))  # fmt: skip
    return fr, train


def production_model(fr: Any, train: pl.DataFrame, season: int) -> rprod.ProductionModel:
    """The Radar's ProductionModel of a fitted fold (``calibrator`` None: the model's own
    probability is the primary one)."""
    record = version_record(fr, train, "production")
    fitted = fr.model.inner
    prep = fitted.pipeline.named_steps["prep"]
    x = train.sort(list(KEYS)).select(list(FEATURES))
    means = np.asarray(prep.transform(rprod._as_model_input(x)), dtype=np.float64).mean(axis=0)
    return rprod.ProductionModel(
        model_version=record["model_version"], season=int(season), model=MODEL, label=LABEL,
        features=tuple(FEATURES), fold=fr.fold, params=json.loads(record["params"]),
        fitted=fitted, calibrator=None, train_means=means, version_row=record,
        n_train=fr.n_train, n_train_pos=fr.n_train_pos, calibration=CALIBRATION,
    )  # fmt: skip


def train_live(targets: pl.DataFrame, season: int) -> rprod.ProductionModel:
    """Train the approved model for ``season`` (only `twm hotseat pin` calls this)."""
    fr, train = fit_live(targets, season)
    return production_model(fr, train, season)


# --------------------------------------------------------------------------------------
# The labelled rows (the owner-accepted labels; the H3b backtest's own code path)
# --------------------------------------------------------------------------------------


def verified_targets(db: Path, features: Path, labels: Path, candidates: Path) -> pl.DataFrame:
    """The labelled rows of 2002-2025 with the verified labels only, built exactly as
    ``twm hotseat backtest --labels verified`` builds them (its main variant)."""
    from twm.modules.hot_seat import backtest as hb
    from twm.modules.hot_seat import labels as hl
    from twm.modules.hot_seat import targets as ht

    lab, cands = hl.read_labels(labels), hl.read_candidates(candidates)
    if cands is None:
        raise HotSeatProductionError(f"not found: {candidates}")
    used = lab.filter(pl.col("verified_by_owner") == "y")
    deps, unresolved = ht.resolve_departures(lab, cands, ht.load_coach_names(db))
    if unresolved.height:
        raise HotSeatProductionError(f"{unresolved.height} departure(s) without a coach_id")
    last = max(hb.TEST_SEASONS)
    feats = ht.refresh_interim(pl.read_parquet(features), used, cands)
    feats = feats.filter(pl.col("season").is_between(hb.FIRST_SEASON, last))
    rows, _ = ht.build_targets(feats, deps, ht.load_team_dates(db, hb.FIRST_SEASON, last),
                               mode="verified")  # fmt: skip
    return rows


# --------------------------------------------------------------------------------------
# Drivers: coef x standardized value (the logit's own terms)
# --------------------------------------------------------------------------------------


def contribution_terms(fitted: Any, x: pl.DataFrame) -> tuple[float, list[str], np.ndarray]:
    """(intercept, input names, terms): ``terms[i, j]`` = coef_j x z_ij over the columns the
    classifier sees (``z``: imputed and standardized, with missing indicators named
    ``missingindicator_<feature>``), so a row's terms sum to its log-odds minus the
    intercept."""
    pipe = fitted.pipeline
    prep, clf = pipe.named_steps["prep"], pipe.named_steps["clf"]
    with warnings.catch_warnings():  # the 2006 fold has no graded season: its imputer warns
        warnings.filterwarnings("ignore", message="Skipping features without any observed")
        z = _transform(prep, x, fitted.features)
    names = [str(n).split("__", 1)[1] for n in prep.get_feature_names_out()]
    return float(clf.intercept_[0]), names, z * np.asarray(clf.coef_[0], dtype=np.float64)


def _transform(prep: Any, x: pl.DataFrame, features: Sequence[str]) -> np.ndarray:
    """The rows as the classifier sees them (imputed, missing indicators, standardized)."""
    z = prep.transform(rprod._as_model_input(x.select(list(features))))
    return np.asarray(z, dtype=np.float64)


def _label(name: str) -> tuple[str, str]:
    """(feature, plain-English label) of an input name, from the registry."""
    from twm import registry

    missing = name.startswith("missingindicator_")
    feature = name.removeprefix("missingindicator_")
    title = registry.get(feature).title
    return feature, (f"{title} (missing)" if missing else title)


def drivers(fitted: Any, x: pl.DataFrame, n: int = N_DRIVERS) -> list[list[dict[str, Any]]]:
    """Per row of ``x``: its ``n`` largest terms by absolute size (ties: input order), signed,
    with the feature, its raw value (None for a missing indicator or a missing value) and the
    registry's label."""
    _, names, terms = contribution_terms(fitted, x)
    raw = x.select(list(fitted.features)).to_dicts()
    out = []
    for i, row in enumerate(raw):
        order = sorted(range(len(names)), key=lambda j: (-abs(terms[i, j]), j))[:n]
        items = []
        for j in order:
            feature, label = _label(names[j])
            value = None if names[j].startswith("missingindicator_") else row.get(feature)
            items.append({"feature": feature, "label": label,
                          "contribution": round(float(terms[i, j]), 6),
                          "value": None if value is None else float(value),
                          "missing": names[j].startswith("missingindicator_")})  # fmt: skip
        out.append(items)
    return out


# --------------------------------------------------------------------------------------
# The frozen walk-forward backtest (the verified H3b run's logit rows)
# --------------------------------------------------------------------------------------

OUTCOME_COLUMNS = ("y", "censored", "departure_type", "announced", "window_end", "date_imputed")
REPRODUCE_TOLERANCE = 1e-9  # the refit folds must give the verified run's probabilities


def ranked(df: pl.DataFrame, prob: str = "prob") -> pl.DataFrame:
    """``rank`` within each list (season, snapshot, week): probability descending, then the
    team code (the backtest's top-5 order); interims are ranked too (flagged on the site)."""
    return df.sort(["season", "snapshot", "week", prob, "team"],
                   descending=[False, False, False, True, False]).with_columns(
        (pl.int_range(pl.len()).over("season", "snapshot", "week") + 1).cast(pl.Int32)
        .alias("rank"))  # fmt: skip


def list_frame(rows: pl.DataFrame, prob: np.ndarray, versions: list[str],
               driver_rows: list[list[dict[str, Any]]],
               names: Mapping[str, str]) -> pl.DataFrame:  # fmt: skip
    """One stored row per scored coach: keys, the coach's name (``names``: warehouse coach id
    -> dim_coach name), as-of, interim flag, version, probability, rank, the key features
    (:data:`SHOWN`) and the drivers as JSON text."""
    ids = rows.get_column("coach_id").to_list()
    out = rows.select(*KEYS, "as_of", "is_interim", *SHOWN).with_columns(
        pl.Series("coach_name", [names.get(i) for i in ids], dtype=pl.String),
        pl.Series("model_version", versions, dtype=pl.String),
        pl.Series("prob", prob, dtype=pl.Float64),
        pl.Series("drivers_json", [json.dumps(d) for d in driver_rows], dtype=pl.String),
    )
    return ranked(out).select(*KEYS, "coach_name", "as_of", "is_interim", "model_version",
                              "prob", "rank", *SHOWN, "drivers_json")  # fmt: skip


def backtest_frames(rows: pl.DataFrame, verified: pl.DataFrame, created_at: Any,
                    names: Mapping[str, str]) -> dict[str, pl.DataFrame]:  # fmt: skip
    """({predictions, outcomes, model_versions}) of the logit walk-forward on ``rows`` (the
    verified targets), refit fold by fold with the backtest's code (seconds) so each fold's
    version and drivers are known; refused unless every probability equals ``verified`` (the
    verified run's logit rows) within :data:`REPRODUCE_TOLERANCE`. The stored probabilities
    are the verified run's."""
    from twm.modules.hot_seat import backtest as hb

    run = hb.run_model(SPECS[MODEL], rows)
    want = verified.filter(pl.col("model") == MODEL).select(*KEYS, pl.col("prob").alias("want"))
    got = run.scored.join(want, on=list(KEYS), how="full", coalesce=True)
    if got.filter(pl.col("prob").is_null() | pl.col("want").is_null()).height:
        raise HotSeatProductionError("the refit backtest scores other rows than the verified run")
    diff = float((got["prob"] - got["want"]).abs().max())  # type: ignore[arg-type]
    if diff > REPRODUCE_TOLERANCE:
        raise HotSeatProductionError(f"the refit folds differ from the verified run by {diff:.3g}")
    preds, outs, vers = [], [], []
    for fr in run.result.folds:
        s = int(fr.fold.test_season)
        train = training_rows(rows, s).filter(pl.col("season").is_in(list(fr.fold.train_seasons)))
        rec = version_record(fr, train, "backtest")
        vers.append(rec)
        test = rows.filter(pl.col("season") == s).sort(list(KEYS))
        prob = (
            test.select(KEYS)
            .join(want, on=list(KEYS), how="left", maintain_order="left")["want"]
            .to_numpy()
        )
        x = test.select(list(FEATURES))
        preds.append(list_frame(test, prob, [rec["model_version"]] * test.height,
                                drivers(fr.model.inner, x), names))  # fmt: skip
        outs.append(test.select(*KEYS, *OUTCOME_COLUMNS))
    vf = pl.DataFrame(vers, schema_overrides={"test_season": pl.Int32}).with_columns(
        pl.lit(pr.code_version()).alias("code_version"),
        pl.lit(created_at, dtype=pl.Datetime("us")).alias("created_at"),
    )  # fmt: skip
    return {"predictions": pl.concat(preds).sort(["season", "snapshot", "week", "rank"]),
            "outcomes": pl.concat(outs).sort(list(KEYS)),
            "model_versions": vf.select(list(pr.VERSION_COLUMNS)).sort("test_season")}  # fmt: skip


# --------------------------------------------------------------------------------------
# Pinned files: checked before they are opened
# --------------------------------------------------------------------------------------


def load_pinned(season: int, *, path: Path | None = None, root: Path | None = None):
    """(the approved model, its pin) for ``season``; :class:`twm.pins.PinError` otherwise:
    the pin must name a 'logit' model of this season, the file's sha256 must equal the pin's
    (checked BEFORE the pickle is opened) and the model inside must be the pinned version."""
    from twm import pins

    pin = pins.get_pin(PIN_KEY, path)
    if pin.model != MODEL:
        raise pins.PinError(f"the pin of {PIN_KEY} says model {pin.model!r}, not {MODEL!r}")
    if pin.season != int(season):
        raise pins.PinError(
            f"the approved {PIN_KEY} model scores season {pin.season}, not {season}: approve "
            "one for this season (`uv run twm hotseat pin`), review it and commit it"
        )
    file = pin.path(root)
    if not file.exists():
        raise pins.PinError(f"the approved model file is missing: {pin.file}")
    digest = pins.sha256_of(file)
    if digest != pin.sha256:
        raise pins.PinError(
            f"{pin.file} is not the approved file (sha256 {digest[:12]}..., the pin says "
            f"{pin.sha256[:12]}...); it was not opened"
        )
    try:
        pm = rprod.load_model(file)
    except rprod.ProductionModelError as e:
        raise pins.PinError(str(e)) from e
    if pm.model_version != pin.model_version:
        raise pins.PinError(
            f"{pin.file} holds {pm.model_version}, the pin says {pin.model_version}"
        )
    if int(pm.season) != int(season) or pm.model != MODEL or pm.label != LABEL:
        raise pins.PinError(
            f"{pin.file} holds a {pm.model}/{pm.label} model for {pm.season}, not the Hot-Seat "
            f"{MODEL}/{LABEL} model for {season}"
        )
    if tuple(pm.features) != tuple(FEATURES):
        raise pins.PinError(f"{pin.file} uses other features than the Hot-Seat Meter's")
    return pm, pin


def load_snapshot(pin: Any, root: Path | None = None) -> dict[str, pl.DataFrame]:
    """The frozen backtest (sha256 before reading, rows after), which must hang together:
    every prediction's version listed, every prediction labelled, one row per key."""
    from twm import pins

    frames = pins.read_snapshot(pin, pins.BACKTEST_TABLES, root)
    v, p, o = frames["model_versions"], frames["predictions"], frames["outcomes"]
    if set(v.get_column("model").unique().to_list()) != {MODEL}:
        raise pins.PinError(f"the {PIN_KEY} snapshot holds other models than {MODEL}")
    if not set(p.get_column("model_version").unique().to_list()) <= set(v["model_version"]):
        raise pins.PinError(f"the {PIN_KEY} snapshot has predictions of unlisted versions")
    if p.select(KEYS).is_duplicated().any() or p.height != o.height:
        raise pins.PinError(f"the {PIN_KEY} snapshot has duplicate or unlabelled rows")
    if p.select(KEYS).join(o, on=list(KEYS), how="anti").height:
        raise pins.PinError(f"the {PIN_KEY} snapshot has predictions without an outcome")
    return frames


def scored_rows(frames: dict[str, pl.DataFrame]) -> pl.DataFrame:
    """The snapshot's predictions with their outcomes (the backtest's scored layout)."""
    return frames["predictions"].join(frames["outcomes"], on=list(KEYS), how="left")


# --------------------------------------------------------------------------------------
# The snapshot must reproduce the committed backtest report (the published track record)
# --------------------------------------------------------------------------------------


def track_record(scored: pl.DataFrame) -> dict[tuple[str, str], tuple]:
    """{(slice, metric): (value, n_rows, n_pos, n_seasons)} of the report's main/logit/prob
    rows the snapshot determines: ROC-AUC, PR-AUC and Brier per slice (point values: every
    season once) and the top-5 hit rates at week 12 and at season end."""
    from twm.modules.hot_seat import backtest as hb
    from twm.modules.hot_seat import evaluation as ev

    out: dict[tuple[str, str], tuple] = {}
    df = hb.evaluated(scored)
    for sl, sdf in hb.slices(df):
        s = ev.Slice.of(sdf["y"], sdf["prob"], sdf["season"])
        point = ev.resampled(s, np.ones((1, s.n_blocks)))
        counts = (sdf.height, int(sdf["y"].sum()), sdf["season"].n_unique())
        for m in ev.METRICS:
            out[(sl, m)] = (float(point[m][0]), *counts)
    week = df.filter((pl.col("snapshot") == "weekly") & (pl.col("week") == hb.TOP_WEEK))
    eos = df.filter(pl.col("snapshot") == "end_of_season")
    for sl, sdf in ((f"week_{hb.TOP_WEEK:02d}", week), ("end_of_season", eos)):
        c = hb.top_k_counts(sdf)
        out[(sl, f"top{hb.TOP_K}_hit_rate")] = (
            float(c["hits"].sum() / c["positives"].sum()), sdf.height, int(sdf["y"].sum()),
            sdf["season"].n_unique(),
        )  # fmt: skip
    return out


def snapshot_mismatches(
    frames: dict[str, pl.DataFrame], metrics_csv: Path, firings_csv: Path
) -> list[str]:
    """Where the snapshot disagrees with reports/hot_seat/backtest_metrics.csv (variant main,
    model logit, prob) and firings_per_season.csv (its seasons) ([] = consistent)."""
    from twm.modules.hot_seat import backtest as hb

    for p in (metrics_csv, firings_csv):
        if not p.exists():
            return [f"the backtest report is missing: {p}"]
    csv = pl.read_csv(metrics_csv, infer_schema_length=0).filter(
        (pl.col("variant") == "main") & (pl.col("model") == MODEL) & (pl.col("prob") == "prob")
    )
    scored = scored_rows(frames)
    got = track_record(scored)
    problems = []
    if csv.height != len(got):
        problems.append(f"{len(got)} main/logit rows in the snapshot, {csv.height} in the report")
    for (sl, metric), (value, n_rows, n_pos, n_seasons) in sorted(got.items()):
        want = csv.filter((pl.col("slice") == sl) & (pl.col("metric") == metric))
        if want.height != 1:
            problems.append(f"{sl} {metric}: not in the report")
            continue
        w = want.row(0, named=True)
        if abs(float(w["value"]) - value) > TOLERANCE:
            problems.append(f"{sl} {metric}: {value:.6f} in the snapshot, {w['value']} reported")
        for col, g in (("n_rows", n_rows), ("n_pos", n_pos), ("n_seasons", n_seasons)):
            if str(g) != w[col]:
                problems.append(f"{sl} {metric}: {col} {g} in the snapshot, {w[col]} reported")
    fire = hb.firings_per_season(scored)
    rep = pl.read_csv(firings_csv).filter(pl.col("season").is_in(fire["season"].to_list()))
    if (
        fire.select(rep.columns).cast(rep.schema).sort("season").to_dicts()
        != rep.sort(  # type: ignore[arg-type]
            "season"
        ).to_dicts()
    ):
        problems.append(f"firings per season differ from {firings_csv.name}")
    return problems


# --------------------------------------------------------------------------------------
# Approving (`twm hotseat pin`)
# --------------------------------------------------------------------------------------


def artifact_dir(root: Path | None = None) -> Path:
    from twm import pins
    from twm.config import ROOT

    return (root if root is not None else ROOT) / pins.ARTIFACT_DIR / ARTIFACT_SUBDIR


def _rel(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return str(path)


def _write_snapshot(frames: dict[str, pl.DataFrame], version: str, base: Path) -> dict[str, Any]:
    from twm import pins

    out_dir = artifact_dir(base) / f"backtest-{version}"
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for table, df in frames.items():
        path = out_dir / f"{table}.parquet"
        tmp = path.with_name(path.name + ".tmp")
        df.write_parquet(tmp, compression="zstd", compression_level=pins.PARQUET_LEVEL,
                         statistics=False)  # fmt: skip
        tmp.replace(path)
        files[table] = pins.SnapshotFile(_rel(path, base), pins.sha256_of(path), df.height)
    return files


def approve(
    targets: pl.DataFrame,
    verified: pl.DataFrame,
    *,
    season: int,
    metrics_csv: Path,
    firings_csv: Path,
    names: Mapping[str, str],
    root: Path | None = None,
    path: Path | None = None,
    today: Any = None,
) -> tuple[Any, rprod.ProductionModel]:
    """Approve the live model for ``season``: refit the logit walk-forward on ``targets``
    (the verified labelled rows) and refuse unless it gives ``verified``'s probabilities (the
    verified run, data/hot_seat/backtest_predictions.parquet); freeze that backtest and refuse
    unless it reproduces ``metrics_csv`` and ``firings_csv``; train the season's fold (an
    existing file of the same version is kept byte for byte: pickling is not byte-stable),
    and pin both in config/production_models.yaml (other pins keep theirs). (Pin, model)."""
    from datetime import UTC, datetime

    from twm import pins
    from twm.config import ROOT

    base = root if root is not None else ROOT
    now = (today or datetime.now(UTC)).astimezone(UTC)
    frames = backtest_frames(targets, verified, now.replace(tzinfo=None), names)
    unnamed = frames["predictions"].filter(pl.col("coach_name").is_null())
    if unnamed.height:
        raise HotSeatProductionError(
            f"{unnamed.height} backtest rows name a coach without a name in dim_coach, e.g. "
            f"{unnamed['coach_id'][0]}"
        )
    problems = snapshot_mismatches(frames, metrics_csv, firings_csv)
    if problems:
        raise HotSeatProductionError(
            f"the frozen backtest disagrees with the reports in {len(problems)} places (e.g. "
            f"{problems[0]}): rerun `uv run twm hotseat backtest --labels verified`"
        )
    pm = train_live(targets, season)
    target = rprod.model_path(pm.model_version, artifact_dir(base))
    keep = False
    if target.exists():
        try:
            keep = rprod.load_model(target).model_version == pm.model_version
        except rprod.ProductionModelError:
            keep = False
    file = target if keep else rprod.save_model(pm, target.parent)
    snap = _write_snapshot(frames, pm.model_version, base)
    seen = sorted(int(s) for s in frames["model_versions"]["test_season"].to_list())
    pin = pins.Pin(PIN_KEY, int(season), pm.model_version, _rel(file, base),
                   pins.sha256_of(file), now.date().isoformat(), f"{seen[0]}-{seen[-1]}", snap,
                   MODEL)  # fmt: skip
    current = pins.read_pins(path)
    current[PIN_KEY] = pin
    pins.write_pins(current, path)
    return pin, pm
