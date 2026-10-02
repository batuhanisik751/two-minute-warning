"""The production Cliff board (step I2c-a): the approved models, their pin and the frozen
walk-forward backtest (copied from the Hot-Seat Meter's H4a production code).

**Which models** (owner, 2026-10-02, docs/progress.md "I2 board decisions"): the board shows,
for every Cliff player (3+ prior seasons, top-36 PPG at his position in S), two chances at the
PRESEASON snapshot (config ``as_of.board.preseason``: the kickoff eve), each the primary model
of its backtest variant (docs/board.md): ``cliff`` = the L2 logit of ``cliff_main`` (``y_cliff``:
6+ games in S+1 and a 30%+ PPG drop) and ``missed`` = the simple logit of ``cliff_missed``
(``y_missed``: under 6 games in S+1). Breakout is not on the site (owner) and not pinned.

**The models for the board of season S1** are exactly the folds the walk-forward backtest would
use for the snapshot S = S1 - 1 (:func:`twm.backtest.walkforward.fit_fold` with
:func:`production_fold`, the backtest's harness): every labelled row of the snapshots before S
(2002-2024 for the 2026 board), C chosen inside the fit (:class:`InnerCvLogit`); the model's
own probability (no isotonic step). Each is saved as the Radar's
:class:`~twm.modules.waiver_radar.production.ProductionModel` (a pickle opened only after its
sha256 matched).

**The pin** (``config/production_models.yaml`` entry ``board``, ``model: board_spec``; the
decisions' pattern for more than one model): the pin's file is a JSON spec naming both model
files by version and sha256, the board's season, snapshot and anchor; its backtest is the frozen
walk-forward of both models (snapshots 2007-2024 = the boards of 2008-2025): one row per Cliff
player and board with both chances, ranks, the ECR rank, key features and the top drivers; the
outcomes; one ``model_versions`` row per fold and model. ``twm model check board`` checks it
against reports/board/preseason_cliff.csv, _seasons.csv and the disagreement tables of
preseason_cliff.md. Written by ``twm board pin`` (:func:`approve`).
"""

from __future__ import annotations

import json
import warnings
from collections.abc import Mapping, Sequence
from datetime import UTC
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from twm import predictions as pr
from twm.modules.board import backtest as bt
from twm.modules.board import evaluation as bev
from twm.modules.board.models import KEYS, MODULE
from twm.modules.hot_seat.models import InnerCvLogit, Spec
from twm.modules.waiver_radar import production as rprod

PIN_KEY = "board"
MODEL = "board_spec"  # the pin's `model`: a spec naming the two pickled models by sha256
SPEC_FORMAT = 1
ARTIFACT_SUBDIR = "board"  # artifacts/production_models/board/
SNAPSHOT = "preseason"
WEEK = 0  # a board list is made before week 1 of its season (the predictions store's week)
# role -> (backtest variant, its primary model): docs/board.md, reports/board/preseason_cliff.*
ROLES: dict[str, tuple[str, str]] = {"cliff": ("cliff_main", "logit"),
                                     "missed": ("cliff_missed", "logit_simple")}  # fmt: skip
N_DRIVERS = 3
# the key features a published row shows (beside the drivers), registry names
SHOWN = ("age", "prior_seasons", "games_s", "ppg_s", "pos_rank_s", "ppg_change",
         "touches_per_game_s", "depth_rank_s1", "team_change_s1", "dc_absent",
         "hc_change_s1")  # fmt: skip
CALIBRATION = "none: the model's own probability is primary (docs/board.md)"
TOLERANCE = 1e-9  # the report CSVs print every float in full


class BoardProductionError(ValueError):
    """The production board models or their frozen backtest cannot be built or approved."""


def role_spec(role: str) -> tuple[bt.Variant, Spec]:
    """(the variant, the primary model's spec) of ``role`` at the preseason snapshot."""
    name, model = ROLES[role]
    v = bt.variants(SNAPSHOT)[name]
    return v, v.specs[model]


def population(dataset: pl.DataFrame) -> pl.DataFrame:
    """The board's rows: every Cliff player of every snapshot, sorted by the keys."""
    return dataset.filter(pl.col("in_cliff")).sort(list(KEYS))


# --------------------------------------------------------------------------------------
# The models (the backtest's own fold code)
# --------------------------------------------------------------------------------------


def training_rows(dataset: pl.DataFrame, role: str, snapshot_season: int) -> pl.DataFrame:
    """The labelled rows the ``role`` model for snapshot ``snapshot_season`` learns from: the
    variant's rows of the snapshots before it, sorted by the keys (the harness's order)."""
    v, _ = role_spec(role)
    return bt.variant_rows(dataset, v, int(snapshot_season) - 1)


def dataset_hash(train: pl.DataFrame, seasons: Sequence[int], spec: Spec) -> str:
    """What a fold learned from: the content hash of its training rows, season by season."""
    cols = [*KEYS, *spec.features, spec.label]
    return pr.combine_hashes(
        [pr.frame_hash(train.filter(pl.col("season") == s).select(cols)) for s in seasons]
    )


def version_record(fr: Any, train: pl.DataFrame, spec: Spec, kind: str) -> dict[str, Any]:
    """The ``model_versions`` row of a fitted fold (``fr``: a FoldResult of an
    :class:`InnerCvLogit`): its version hashes the model, label, features, the chosen C and how
    it was chosen, the training and test (snapshot) seasons and the training rows."""
    refit = dict(fr.model.refit_params)
    fixed = dict(spec.make().fixed_params)
    params = {"C": float(refit["C"]), "l1_ratio": 0.0, "c_source": refit["c_source"],
              "inner_seasons": refit["inner_seasons"], "fixed": fixed}  # fmt: skip
    seasons = list(fr.fold.train_seasons)
    dhash = dataset_hash(train, seasons, spec)
    version = pr.model_version(
        module=MODULE, model=spec.name, label=spec.label, features=spec.features, params=params,
        training_seasons=seasons, test_season=fr.fold.test_season, dataset_hash=dhash,
    )  # fmt: skip
    loss = {str(c): round(float(v), 9) for c, v in (refit.get("inner_log_loss") or {}).items()}
    notes = {"kind": kind, "snapshot": SNAPSHOT, "validation_season": fr.fold.val_season,
             "calibration": CALIBRATION, "n_train": fr.n_train, "n_train_pos": fr.n_train_pos,
             "inner_log_loss": loss}  # fmt: skip
    return {
        "model_version": version, "module": MODULE, "model": spec.name, "label": spec.label,
        "feature_list": json.dumps(sorted(spec.features)),
        "params": json.dumps(params, sort_keys=True),
        "training_seasons": json.dumps(sorted(seasons)), "test_season": int(fr.fold.test_season),
        "dataset_hash": dhash, "notes": json.dumps(notes, sort_keys=True),
    }  # fmt: skip


def fit_live(dataset: pl.DataFrame, role: str, snapshot_season: int) -> tuple[Any, pl.DataFrame]:
    """(FoldResult, training rows) of the ``role`` fold that scores snapshot ``snapshot_season``
    (the board of ``snapshot_season`` + 1; module docstring)."""
    from twm.backtest.walkforward import fit_fold, production_fold
    from twm.modules.hot_seat.models import neg_log_loss

    _, spec = role_spec(role)
    rows = training_rows(dataset, role, snapshot_season)
    seasons = sorted({int(s) for s in rows.get_column("season").to_list()})
    if not seasons:
        raise BoardProductionError(f"no labelled {role} rows before {snapshot_season}")
    fold = production_fold(seasons, snapshot_season)
    train = rows.filter(pl.col("season").is_in(list(fold.train_seasons)))
    est = spec.make()
    assert isinstance(est, InnerCvLogit)
    est.bind(train, spec.features, KEYS)  # the inner walk-forward reads the seasons from it
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Skipping features without any observed")
        fr = fit_fold(train, fold, estimator=est, features=spec.features, label=spec.label,
                      module=MODULE, keys=KEYS, tune_metric=neg_log_loss(spec.label),
                      cal_group="season")  # fmt: skip
    return fr, train


def production_model(fr: Any, train: pl.DataFrame, role: str, snapshot_season: int):
    """The Radar's ProductionModel of a fitted ``role`` fold (``calibrator`` None: the model's
    own probability is the primary one); ``season`` = the snapshot season it scores."""
    _, spec = role_spec(role)
    record = version_record(fr, train, spec, "production")
    fitted = fr.model.inner
    prep = fitted.pipeline.named_steps["prep"]
    x = train.sort(list(KEYS)).select(list(spec.features))
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Skipping features without any observed")
        z = prep.transform(rprod._as_model_input(x))
    means = np.asarray(z, dtype=np.float64).mean(axis=0)
    return rprod.ProductionModel(
        model_version=record["model_version"], season=int(snapshot_season), model=spec.name,
        label=spec.label, features=tuple(spec.features), fold=fr.fold,
        params=json.loads(record["params"]), fitted=fitted, calibrator=None, train_means=means,
        version_row=record, n_train=fr.n_train, n_train_pos=fr.n_train_pos,
        calibration=CALIBRATION,
    )  # fmt: skip


def train_live(dataset: pl.DataFrame, role: str, snapshot_season: int):
    """Train the approved ``role`` model for snapshot ``snapshot_season`` (only `twm board pin`
    calls this)."""
    fr, train = fit_live(dataset, role, snapshot_season)
    return production_model(fr, train, role, snapshot_season)


# --------------------------------------------------------------------------------------
# A board: both chances, ranks, the ECR rank, key features and the drivers
# --------------------------------------------------------------------------------------


def drivers(fitted: Any, x: pl.DataFrame) -> list[list[dict[str, Any]]]:
    """Per row: the :data:`N_DRIVERS` largest terms of the logit (coef x standardized value;
    the Hot-Seat Meter's :func:`~twm.modules.hot_seat.production.drivers`)."""
    from twm.modules.hot_seat import production as hp

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Skipping features without any observed")
        return hp.drivers(fitted, x, N_DRIVERS)


def ranked(df: pl.DataFrame, prob: str, rank: str) -> pl.DataFrame:
    """``rank`` within each board (season): ``prob`` descending, then the id (the backtest's
    ranking, :func:`twm.backtest.metrics.add_rank`)."""
    from twm.backtest import metrics as mt

    return mt.add_rank(df, group=["season"], by=[(prob, True)], id_col="gsis_id", name=rank)


def ecr_columns(db: Path | str, rows: pl.DataFrame, as_of: Mapping[int, Any]) -> pl.DataFrame:
    """``rows`` (board rows of one or more snapshot seasons S) + ``ecr_rank`` (FantasyPros'
    preseason position rank of S+1, NULL = unranked or no ECR) and ``ecr_fill`` (the rank the
    backtest's ECR baseline scores with: an unranked player = his page's last + 1; NULL = no
    ECR that season). Only a scrape public by the board's as-of (``as_of``: S -> aware UTC)
    counts (every preseason scrape 2020-2025 was public 6-7 days before the kickoff eve)."""
    from twm.modules.board import ecr as be

    parts = []
    for (s,), part in rows.group_by(["season"], maintain_order=True):
        got = be.scrape_public_at(db, int(s) + 1) if int(s) + 1 >= be.FIRST_LABEL_SEASON else None
        at = as_of[int(s)]
        if got is None or got[1] > at.astimezone(UTC).replace(tzinfo=None):
            nul = pl.lit(None, dtype=pl.Int64)
            parts.append(part.with_columns(nul.alias("ecr_rank"), nul.alias("ecr_fill")))
            continue
        a = be.attach_ecr(part, be.ecr_ranks(db, int(s) + 1))
        parts.append(a.with_columns(pl.col("ecr_rank").cast(pl.Int64),
                                    pl.col("ecr_fill").cast(pl.Int64)))  # fmt: skip
    return pl.concat(parts, how="vertical").sort(list(KEYS))


PRED_COLUMNS = ("season", "snapshot_season", "gsis_id", "position", "team", "as_of",
                "cliff_version", "cliff_prob", "cliff_rank", "missed_version", "missed_prob",
                "missed_rank", "ecr_rank", "ecr_fill", *SHOWN, "cliff_drivers_json",
                "missed_drivers_json")  # fmt: skip
OUTCOME_COLUMNS = ("season", "gsis_id", "games_s1", "ppg_s1", "y_cliff", "y_missed")
Scored = tuple[np.ndarray, list[str], list[list[dict[str, Any]]]]  # prob, versions, drivers


def board_frame(rows: pl.DataFrame, scored: Mapping[str, Scored]) -> pl.DataFrame:
    """One row per Cliff player of each board (``rows``: population rows with ``ecr_rank`` and
    ``ecr_fill``, sorted by the keys; ``scored``: per role the probabilities, versions and
    drivers in that order): ``season`` = S + 1 (the board's season), ``snapshot_season`` = S,
    ``as_of`` = the snapshot's, both chances with their rank in the board (:func:`ranked`)."""
    out = rows.select(
        (pl.col("season") + 1).cast(pl.Int32).alias("season"),
        pl.col("season").cast(pl.Int32).alias("snapshot_season"),
        "gsis_id", "position", "team", pl.col("snapshot").alias("as_of"), "ecr_rank",
        "ecr_fill", *SHOWN,
    )  # fmt: skip
    for role in ROLES:
        prob, versions, drv = scored[role]
        out = out.with_columns(
            pl.Series(f"{role}_version", versions, dtype=pl.String),
            pl.Series(f"{role}_prob", prob, dtype=pl.Float64),
            pl.Series(f"{role}_drivers_json", [json.dumps(d) for d in drv], dtype=pl.String),
        )
    for role in ROLES:
        out = ranked(out, f"{role}_prob", f"{role}_rank")
    return out.select(PRED_COLUMNS).sort("season", "cliff_rank")


def score_board(models: Mapping[str, Any], rows: pl.DataFrame) -> pl.DataFrame:
    """The board of ``rows`` (population rows of one snapshot with the ECR columns) scored by
    the approved models (``models``: role -> ProductionModel; nothing is fitted)."""
    rows = rows.sort(list(KEYS))
    scored: dict[str, Scored] = {}
    for role in ROLES:
        pm = models[role]
        x = rows.select(list(pm.features))
        prob = pm.raw(x) if rows.height else np.zeros(0)
        drv = drivers(pm.fitted, x) if rows.height else []
        scored[role] = (np.asarray(prob, dtype=np.float64), [pm.model_version] * rows.height, drv)
    return board_frame(rows, scored)


def outcome_frame(rows: pl.DataFrame) -> pl.DataFrame:
    """The labels of the board rows: S+1 games and PPG, ``y_cliff`` (NULL when missed) and
    ``y_missed``, keyed by the board's season."""
    return rows.select(
        (pl.col("season") + 1).cast(pl.Int32).alias("season"), "gsis_id",
        pl.col("games_next").cast(pl.Int32).alias("games_s1"),
        pl.col("ppg_next").alias("ppg_s1"), "y_cliff", "y_missed",
    ).sort("season", "gsis_id")  # fmt: skip


def snapshot_as_of(rows: pl.DataFrame) -> dict[int, Any]:
    """{S: the snapshot's as-of (aware UTC)} of the population rows (the dataset's
    ``snapshot`` column: one as-of per S)."""
    got = rows.group_by("season").agg(pl.col("snapshot").max())
    return {int(s): t.replace(tzinfo=UTC) for s, t in got.iter_rows()}


# --------------------------------------------------------------------------------------
# The frozen walk-forward backtest (the preseason report's primary models)
# --------------------------------------------------------------------------------------

REPRODUCE_TOLERANCE = 1e-12  # a fold's own scores must equal the walk-forward's


def backtest_frames(
    dataset: pl.DataFrame, db: Path | str, created_at: Any
) -> dict[str, pl.DataFrame]:
    """({predictions, outcomes, model_versions}) of both models' walk-forward on ``dataset``
    (the preseason dataset at the config anchor), refit fold by fold with the backtest's own
    code (:func:`twm.modules.board.backtest.run_model`, seconds) so each fold's version and
    drivers are known. Every Cliff row of a test snapshot is scored by its fold (the Cliff
    model's evaluation rows are those with 6+ games in S+1, but the board shows every Cliff
    player); refused unless a fold's scores of the evaluated rows equal the walk-forward's."""
    pop = population(dataset).filter(pl.col("season").is_in(list(bt.TEST_SEASONS)))
    pop = ecr_columns(db, pop, snapshot_as_of(pop))
    runs, vers = {}, []
    for role in ROLES:
        v, spec = role_spec(role)
        rows = bt.variant_rows(dataset, v)
        runs[role] = (spec, rows, bt.run_model(spec, rows, bt.TEST_SEASONS))
    parts = []
    for s in bt.TEST_SEASONS:
        part = pop.filter(pl.col("season") == s).sort(list(KEYS))
        scored: dict[str, Scored] = {}
        for role, (spec, rows, res) in runs.items():
            fr = next(f for f in res.folds if int(f.fold.test_season) == s)
            x = part.select(list(spec.features))
            with warnings.catch_warnings():  # early folds: no NGS / snap / xFP seasons
                warnings.filterwarnings("ignore", message="Skipping features without any")
                prob = np.asarray(fr.model.predict(x), dtype=np.float64)
            want = (
                part.select(KEYS)
                .with_columns(pl.Series("p", prob))
                .join(res.predictions.select(*KEYS, "raw_score"), on=list(KEYS), how="inner")
            )
            if want.height != res.predictions.filter(pl.col("season") == s).height:
                raise BoardProductionError(f"{role} {s}: the board misses evaluated rows")
            diff = float((want["p"] - want["raw_score"]).abs().max() or 0.0)  # type: ignore[arg-type]
            if diff > REPRODUCE_TOLERANCE:
                raise BoardProductionError(f"{role} {s}: the fold differs from the walk-forward "
                                           f"by {diff:.3g}")  # fmt: skip
            train = rows.filter(pl.col("season").is_in(list(fr.fold.train_seasons)))
            rec = version_record(fr, train, spec, "backtest")
            vers.append(rec)
            scored[role] = (prob, [rec["model_version"]] * part.height,
                            drivers(fr.model.inner, x))  # fmt: skip
        parts.append(board_frame(part, scored))
    vf = pl.DataFrame(vers, schema_overrides={"test_season": pl.Int32}).with_columns(
        pl.lit(pr.code_version()).alias("code_version"),
        pl.lit(created_at, dtype=pl.Datetime("us")).alias("created_at"),
    )  # fmt: skip
    return {"predictions": pl.concat(parts).sort("season", "cliff_rank"),
            "outcomes": outcome_frame(pop),
            "model_versions": vf.select(list(pr.VERSION_COLUMNS)).sort(
                "test_season", "model")}  # fmt: skip


# --------------------------------------------------------------------------------------
# The snapshot must reproduce the committed preseason report (the published track record)
# --------------------------------------------------------------------------------------


def evaluated(frames: Mapping[str, pl.DataFrame], role: str) -> pl.DataFrame:
    """The ``role`` model's evaluation rows in the backtest's layout (``season`` = the snapshot
    season S, ``y``, ``p_<model>``, ``p_ecr`` = ECR rank minus last season's PPG rank, the
    report's 'fall' score): the Cliff model is evaluated on the rows with 6+ games in S+1."""
    v, spec = role_spec(role)
    df = frames["predictions"].join(frames["outcomes"], on=["season", "gsis_id"], how="left")
    df = df.filter(pl.col(v.label).is_not_null())
    return df.select(
        pl.col("snapshot_season").alias("season"), "gsis_id", "position",
        pl.col(v.label).cast(pl.Int8).alias("y"), pl.col(f"{role}_prob").alias(f"p_{spec.name}"),
        (pl.col("ecr_fill") - pl.col("pos_rank_s")).cast(pl.Float64).alias("p_ecr"),
        pl.col("ppg_s").alias("ppg"), pl.col("ppg_s1").alias("ppg_next"),
        pl.col("games_s1").alias("games_next"),
    ).sort(list(KEYS))  # fmt: skip


def metric_rows(df: pl.DataFrame, model: str) -> pl.DataFrame:
    """The report's metric rows the snapshot determines: ``model`` over every test snapshot;
    ``model``, the ECR and their paired differences in the ECR era."""
    era = df.filter(pl.col("season") >= bev.ECR_FIRST_SNAPSHOT, pl.col("p_ecr").is_not_null())
    rows = bev.slice_table(df, [model], [], "all")
    rows += bev.slice_table(era, [model, "ecr"], ["ecr"], "ecr_era")
    return pl.DataFrame(rows, infer_schema_length=None)


def _close(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= TOLERANCE


def _csv_problems(got: pl.DataFrame, csv: pl.DataFrame, variant: str) -> list[str]:
    problems = []
    want = csv.filter(pl.col("variant") == variant)
    for r in got.iter_rows(named=True):
        w = want.filter(
            (pl.col("slice") == r["slice"])
            & (pl.col("model") == r["model"])
            & (pl.col("metric") == r["metric"])
            & (pl.col("vs").is_null() if r["vs"] is None else pl.col("vs") == r["vs"])
        )
        what = f"{variant} {r['slice']} {r['model']} {r['metric']}" + (
            f" vs {r['vs']}" if r["vs"] else ""
        )
        if w.height != 1:
            problems.append(f"{what}: not in the report")
            continue
        for col in ("value", "lo", "hi", "share_above_zero"):
            if not _close(r[col], w[col][0]):
                problems.append(f"{what}: {col} {r[col]} in the snapshot, {w[col][0]} reported")
    return problems  # fmt: skip


def _seasons_problems(df: pl.DataFrame, model: str, csv: pl.DataFrame, variant: str) -> list[str]:
    got = bev.per_season(df, [model])
    cols = got.columns
    want = csv.filter(pl.col("variant") == variant).select(cols).cast(got.schema)  # type: ignore[arg-type]
    if got.sort("season").to_dicts() != want.sort("season").to_dicts():
        return [f"{variant}: rows, positives or {model}'s top-k hits per season differ"]
    return []


def md_disagreements(md: str, variant: str, model: str) -> dict[str, tuple[int, int]]:
    """{group: (players, label = 1)} of ``variant``'s disagreement table in a board report
    (:func:`twm.modules.board.report.disagreement_section`)."""
    words = {f"in `{model}`'s top 10, not the ECR's": "model_only",
             f"in the ECR's top 10, not `{model}`'s": "ecr_only", "in both": "both"}  # fmt: skip
    lines = md.splitlines()
    start = next((i for i, x in enumerate(lines) if x.startswith("### ")
                  and f"(`{variant}`," in x), None)  # fmt: skip
    if start is None:
        return {}
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("### ")),
               len(lines))  # fmt: skip
    head = next((i for i in range(start, end) if lines[i].startswith("Where the model and")), None)
    out: dict[str, tuple[int, int]] = {}
    for x in lines[(head or end) + 1 : end]:
        cells = [c.strip() for c in x.strip().strip("|").split("|")]
        if x.startswith("|") and cells[0] in words:
            out[words[cells[0]]] = (int(cells[1]), int(cells[2]))
        elif out and not x.startswith("|"):
            break
    return out


def disagreement_table(frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    """Per model, ECR-era board (labels 2020-2025) and group: the model's top 10 not in the
    ECR's top 10 ('model_only'), the reverse ('ecr_only') and both, with how many had the label
    (the report's :func:`~twm.modules.board.evaluation.disagreements`, per season; rankings
    among the model's evaluation rows; ``season`` = the board's season)."""
    rows = []
    for role in ROLES:
        v, spec = role_spec(role)
        df = evaluated(frames, role)
        era = df.filter(pl.col("season") >= bev.ECR_FIRST_SNAPSHOT, pl.col("p_ecr").is_not_null())
        for (s,), part in era.group_by(["season"], maintain_order=True):
            summary, _ = bev.disagreements(part, spec.name)
            for r in summary.iter_rows(named=True):
                rows.append({"variant": v.name, "model": spec.name, "season": int(s) + 1,
                             "pick_group": r["group"], "players": int(r["rows"]),
                             "hits": int(r["hits"])})  # fmt: skip
    schema = {"variant": pl.String, "model": pl.String, "season": pl.Int32,
              "pick_group": pl.String, "players": pl.Int32, "hits": pl.Int32}  # fmt: skip
    return pl.DataFrame(rows, schema=schema).sort("variant", "season", "pick_group")


def snapshot_mismatches(frames: Mapping[str, pl.DataFrame], report_dir: Path,
                        prefix: str) -> list[str]:  # fmt: skip
    """Where the snapshot disagrees with ``report_dir``/<prefix>cliff.csv (each pinned model's
    rows the snapshot determines: values, intervals, shares), <prefix>cliff_seasons.csv and the
    disagreement tables of <prefix>cliff.md ([] = consistent)."""
    paths = [report_dir / f"{prefix}cliff{x}" for x in (".csv", "_seasons.csv", ".md")]
    for p in paths:
        if not p.exists():
            return [f"the backtest report is missing: {p}"]
    csv = pl.read_csv(paths[0], infer_schema_length=None)
    seasons = pl.read_csv(paths[1], infer_schema_length=None)
    md = paths[2].read_text()
    table = disagreement_table(frames)
    problems = []
    for role in ROLES:
        v, spec = role_spec(role)
        df = evaluated(frames, role)
        problems += _csv_problems(metric_rows(df, spec.name), csv, v.name)
        problems += _seasons_problems(df, spec.name, seasons, v.name)
        pooled = table.filter(pl.col("variant") == v.name).group_by("pick_group").agg(
            pl.col("players").sum(), pl.col("hits").sum())  # fmt: skip
        got = {g: (int(a), int(b)) for g, a, b in pooled.iter_rows()}
        if got != md_disagreements(md, v.name, spec.name):
            problems.append(f"{v.name}: the disagreement table differs from {paths[2].name}")
    return problems


# --------------------------------------------------------------------------------------
# The spec and the pinned files: checked before they are opened
# --------------------------------------------------------------------------------------


def spec_content(season: int, anchor: str, models: Mapping[str, Any], files: Mapping[str, str],
                 shas: Mapping[str, str]) -> dict[str, Any]:  # fmt: skip
    """The approved board of ``season`` (S1): its snapshot and anchor, and per role the model's
    version, file (relative to the project) and sha256."""
    roles = {}
    for role, pm in models.items():
        v, spec = role_spec(role)
        roles[role] = {"version": pm.model_version, "file": files[role], "sha256": shas[role],
                       "variant": v.name, "model": spec.name, "label": spec.label}  # fmt: skip
    return {"format": SPEC_FORMAT, "module": MODULE, "season": int(season),
            "snapshot": SNAPSHOT, "anchor": anchor, "models": roles}  # fmt: skip


def spec_version(content: Mapping[str, Any]) -> str:
    import hashlib

    text = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return f"{MODEL}-{hashlib.sha256(text.encode()).hexdigest()[:16]}"


def read_spec(path: Path) -> dict[str, Any]:
    """A spec this code wrote (format, keys, version = the hash of its content), else
    :class:`BoardProductionError`."""
    try:
        d = json.loads(Path(path).read_text())
        content = {k: d[k] for k in ("format", "module", "season", "snapshot", "anchor", "models")}
        ok = content["format"] == SPEC_FORMAT and content["module"] == MODULE
        ok = ok and set(content["models"]) == set(ROLES) and all(
            {"version", "file", "sha256", "variant", "model", "label"} <= set(m)
            for m in content["models"].values())  # fmt: skip
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise BoardProductionError(f"cannot read the board spec {path}: {e}") from e
    if not ok:
        raise BoardProductionError(f"{path} is not a board spec of this code")
    if d.get("model_version") != spec_version(content):
        raise BoardProductionError(f"{path} says {d.get('model_version')}, its content is "
                                   f"{spec_version(content)}")  # fmt: skip
    return {**content, "model_version": d["model_version"]}


def _checked_file(entry: Mapping[str, str], what: str, root: Path | None) -> Path:
    """The file an entry names, only after its sha256 matched (else PinError, never opened)."""
    from twm import pins

    file = pins._resolve(entry["file"], root)
    if not file.exists():
        raise pins.PinError(f"the approved {what} file is missing: {entry['file']}")
    digest = pins.sha256_of(file)
    if digest != entry["sha256"]:
        raise pins.PinError(f"{entry['file']} is not the approved {what} file (sha256 "
                            f"{digest[:12]}..., the pin says {entry['sha256'][:12]}...); it was "
                            "not opened")  # fmt: skip
    return file


def load_pinned(season: int, *, path: Path | None = None, root: Path | None = None):
    """(models: role -> ProductionModel, spec, pin) of the board of ``season`` (S1);
    :class:`twm.pins.PinError` otherwise: the pin must be a board spec of this season with the
    pinned sha256 (checked before it is read) and version, made at the config's preseason
    anchor; each model file must have the spec's sha256 (checked BEFORE the pickle is opened)
    and hold the spec's version: the role's model, label and features, the fold of snapshot
    ``season`` - 1."""
    from twm import pins
    from twm.modules.board import preseason as pre

    pin = pins.get_pin(PIN_KEY, path)
    if pin.model != MODEL:
        raise pins.PinError(f"the pin of {PIN_KEY} says model {pin.model!r}, not {MODEL!r}")
    if pin.season != int(season):
        raise pins.PinError(
            f"the approved board is for season {pin.season}, not {season}: approve one for this "
            "season (`uv run twm board pin`), review it and commit it")  # fmt: skip
    file = _checked_file({"file": pin.file, "sha256": pin.sha256}, "board spec", root)
    try:
        spec = read_spec(file)
    except BoardProductionError as e:
        raise pins.PinError(str(e)) from e
    if spec["model_version"] != pin.model_version or spec["season"] != int(season):
        raise pins.PinError(f"{pin.file} holds {spec['model_version']} for {spec['season']}, "
                            f"the pin says {pin.model_version} for {season}")  # fmt: skip
    if spec["anchor"] != pre.default_anchor():
        raise pins.PinError(f"the board was approved at the preseason anchor {spec['anchor']}, "
                            f"config as_of.board.preseason says {pre.default_anchor()}: approve "
                            "it again")  # fmt: skip
    models = {}
    for role, entry in spec["models"].items():
        _, rs = role_spec(role)
        f = _checked_file(entry, f"board {role} model", root)
        try:
            pm = rprod.load_model(f)
        except rprod.ProductionModelError as e:
            raise pins.PinError(str(e)) from e
        if pm.model_version != entry["version"]:
            raise pins.PinError(f"{entry['file']} holds {pm.model_version}, the spec says "
                                f"{entry['version']}")  # fmt: skip
        if (pm.model, pm.label, tuple(pm.features)) != (rs.name, rs.label, tuple(rs.features)) \
                or int(pm.season) != int(season) - 1:  # fmt: skip
            raise pins.PinError(
                f"{entry['file']} holds a {pm.model}/{pm.label} model for snapshot {pm.season}, "
                f"not the board's {role} model ({rs.name}/{rs.label}) for snapshot {season - 1}"
            )
        models[role] = pm
    return models, spec, pin


def load_snapshot(pin: Any, root: Path | None = None) -> dict[str, pl.DataFrame]:
    """The frozen backtest (sha256 before reading, rows after), which must hang together: only
    the two pinned models, every prediction's versions listed, one row per (board, player),
    every row labelled."""
    from twm import pins

    frames = pins.read_snapshot(pin, pins.BACKTEST_TABLES, root)
    v, p, o = frames["model_versions"], frames["predictions"], frames["outcomes"]
    want = {role_spec(r)[1].name for r in ROLES}
    if set(v.get_column("model").unique().to_list()) != want:
        raise pins.PinError(f"the {PIN_KEY} snapshot holds other models than {sorted(want)}")
    used = set(p["cliff_version"].to_list()) | set(p["missed_version"].to_list())
    if not used <= set(v["model_version"].to_list()):
        raise pins.PinError(f"the {PIN_KEY} snapshot has predictions of unlisted versions")
    keys = ["season", "gsis_id"]
    if p.select(keys).is_duplicated().any() or p.height != o.height:
        raise pins.PinError(f"the {PIN_KEY} snapshot has duplicate or unlabelled rows")
    if p.select(keys).join(o, on=keys, how="anti").height:
        raise pins.PinError(f"the {PIN_KEY} snapshot has predictions without an outcome")
    return frames


# --------------------------------------------------------------------------------------
# Approving (`twm board pin`)
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


def _write_snapshot(frames: Mapping[str, pl.DataFrame], version: str, base: Path) -> dict:
    from twm import pins

    out_dir = artifact_dir(base) / f"backtest-{version}"
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for table in [x for x in pins.SNAPSHOT_TABLES if x in frames]:
        df, path = frames[table], out_dir / f"{table}.parquet"
        tmp = path.with_name(path.name + ".tmp")
        df.write_parquet(tmp, compression="zstd", compression_level=pins.PARQUET_LEVEL,
                         statistics=False)  # fmt: skip
        tmp.replace(path)
        files[table] = pins.SnapshotFile(_rel(path, base), pins.sha256_of(path), df.height)
    return files


def _model_file(pm: Any, base: Path) -> Path:
    """The model's file (an existing file of the same version is kept byte for byte: pickling
    is not byte-stable)."""
    target = rprod.model_path(pm.model_version, artifact_dir(base))
    if target.exists():
        try:
            if rprod.load_model(target).model_version == pm.model_version:
                return target
        except rprod.ProductionModelError:
            pass
    return rprod.save_model(pm, target.parent)


def approve(
    dataset: pl.DataFrame,
    db: Path | str,
    *,
    season: int,
    report_dir: Path,
    root: Path | None = None,
    path: Path | None = None,
    today: Any = None,
) -> tuple[Any, dict[str, Any]]:
    """Approve the board of ``season`` (S1): refit both models' walk-forward on ``dataset``
    (the preseason dataset at the config anchor), freeze it and refuse unless it reproduces
    ``report_dir``'s report of that anchor (:func:`snapshot_mismatches`); train both folds of
    snapshot S1 - 1 on every earlier labelled snapshot; write the spec naming both files and
    pin it with the snapshot in config/production_models.yaml (other pins keep theirs).
    (pin, models)."""
    from datetime import datetime

    from twm import pins
    from twm.config import ROOT
    from twm.modules.board import preseason as pre

    base = root if root is not None else ROOT
    now = (today or datetime.now(UTC)).astimezone(UTC)
    anchor = pre.default_anchor()
    frames = backtest_frames(dataset, db, now.replace(tzinfo=None))
    problems = snapshot_mismatches(frames, report_dir, pre.REPORT_PREFIX[anchor])
    if problems:
        raise BoardProductionError(
            f"the frozen backtest disagrees with the {pre.REPORT_PREFIX[anchor]}* reports in "
            f"{len(problems)} places (e.g. {problems[0]}): rerun `uv run twm board backtest "
            "--snapshot preseason`")  # fmt: skip
    models = {role: train_live(dataset, role, int(season) - 1) for role in ROLES}
    files = {role: _model_file(pm, base) for role, pm in models.items()}
    # the season's board when its as-of has passed: scored by the saved files, frozen with it
    saved = {role: rprod.load_model(f) for role, f in files.items()}
    current = current_frames(saved, dataset, db, int(season), now, anchor)
    if current is not None:
        frames = {**frames, **current}
    content = spec_content(season, anchor, models,
                           {r: _rel(f, base) for r, f in files.items()},
                           {r: pins.sha256_of(f) for r, f in files.items()})  # fmt: skip
    version = spec_version(content)
    spec_file = artifact_dir(base) / f"{version}.json"
    spec_file.write_text(json.dumps({**content, "model_version": version}, indent=1,
                                    sort_keys=True) + "\n")  # fmt: skip
    snap = _write_snapshot(frames, version, base)
    seen = sorted(int(s) for s in frames["model_versions"]["test_season"].to_list())
    pin = pins.Pin(PIN_KEY, int(season), version, _rel(spec_file, base),
                   pins.sha256_of(spec_file), now.date().isoformat(), f"{seen[0]}-{seen[-1]}",
                   snap, MODEL)  # fmt: skip
    current = pins.read_pins(path)
    current[PIN_KEY] = pin
    pins.write_pins(current, path)
    return pin, models


# --------------------------------------------------------------------------------------
# The board of the pin's season, frozen in the pin when its as-of had passed (I2c-a)
# --------------------------------------------------------------------------------------

CURRENT_TABLES = ("current_board", "current_inputs")  # twm.pins.BOARD_TABLES
CURRENT_TOLERANCE = 1e-9  # re-scoring the frozen inputs must give the frozen chances
DRIVER_TOLERANCE = 1.01e-6  # a driver's contribution is stored with 6 decimals


def input_columns() -> list[str]:
    """The columns :func:`score_board` reads: keys, team, position, as-of, the ECR columns, the
    shown features and every input of both models."""
    feats = {f for role in ROLES for f in role_spec(role)[1].features} | set(SHOWN)
    return ["season", "gsis_id", "position", "team", "snapshot", "ecr_rank", "ecr_fill",
            *sorted(feats)]  # fmt: skip


def current_frames(
    models: Mapping[str, Any],
    dataset: pl.DataFrame,
    db: Path | str,
    season: int,
    now: Any,
    anchor: str,
) -> dict[str, pl.DataFrame] | None:
    """{current_board, current_inputs} of the board of ``season`` (its Cliff rows of snapshot
    ``season`` - 1 in ``dataset`` at their as-of, the ECR rank public by then, scored by
    ``models``), or None when its as-of has not passed at ``now`` (next August's board: scored
    live by `twm board score`)."""
    from twm.modules.board import preseason as pre

    s = int(season) - 1
    as_of = pre.preseason_as_of(db, s, anchor)
    if as_of > now:
        return None
    rows = population(dataset.filter(pl.col("season") == s))
    if rows.height == 0:
        raise BoardProductionError(f"the {season} board's as-of has passed but the dataset has "
                                   f"no Cliff row of snapshot {s}")  # fmt: skip
    inputs = ecr_columns(db, rows, {s: as_of}).select(input_columns())
    return {"current_board": score_board(models, inputs), "current_inputs": inputs}


def load_current(pin: Any, root: Path | None = None) -> dict[str, pl.DataFrame] | None:
    """The pin's frozen board of its season (sha256 before reading, rows after; None when the
    pin has none): one board of the pin's season, one row per player, its inputs row for row."""
    from twm import pins

    if not set(CURRENT_TABLES) & set(pin.backtest):
        return None
    frames = pins.read_snapshot(pin, CURRENT_TABLES, root)
    b, i = frames["current_board"], frames["current_inputs"]
    if set(b["season"].to_list()) != {int(pin.season)} or b["gsis_id"].is_duplicated().any():
        raise pins.PinError(f"the {PIN_KEY} pin's current board is not one board of {pin.season}")
    if sorted(b["gsis_id"].to_list()) != sorted(i["gsis_id"].to_list()):
        raise pins.PinError(f"the {PIN_KEY} pin's current board and its inputs differ in players")
    return frames


def _drivers_close(a: str, b: str) -> bool:
    x, y = json.loads(a), json.loads(b)
    return len(x) == len(y) and all(
        p["feature"] == q["feature"] and p["missing"] == q["missing"]
        and abs(p["contribution"] - q["contribution"]) <= DRIVER_TOLERANCE
        for p, q in zip(x, y, strict=True))  # fmt: skip


def current_mismatches(models: Mapping[str, Any], current: Mapping[str, pl.DataFrame]) -> list[str]:
    """Where re-scoring the frozen inputs with the pinned ``models`` disagrees with the frozen
    board ([] = it reproduces it: both chances to 1e-9, both ranks and versions exactly, the
    drivers' features and contributions to their 6 decimals)."""
    want = current["current_board"].sort("gsis_id")
    got = score_board(models, current["current_inputs"]).sort("gsis_id")
    if got["gsis_id"].to_list() != want["gsis_id"].to_list():
        return ["the re-scored board lists other players than the frozen one"]
    problems = []
    for role in ROLES:
        diff = float((got[f"{role}_prob"] - want[f"{role}_prob"]).abs().max() or 0.0)  # type: ignore[arg-type]
        if diff > CURRENT_TOLERANCE:
            problems.append(f"{role}: the re-scored chances differ by up to {diff:.3g}")
        for col in (f"{role}_rank", f"{role}_version"):
            if got[col].to_list() != want[col].to_list():
                problems.append(f"{role}: the re-scored {col} differs")
        drv = zip(got[f"{role}_drivers_json"], want[f"{role}_drivers_json"], strict=True)
        if not all(_drivers_close(a, b) for a, b in drv):
            problems.append(f"{role}: the re-scored drivers differ")
    same = ["season", "snapshot_season", "position", "team", "as_of", "ecr_rank", *SHOWN]
    if not got.select(same).equals(want.select(same)):
        problems.append("the re-scored board's shown columns differ")
    return problems


def same_board(a: pl.DataFrame, b: pl.DataFrame) -> bool:
    """Two boards with the same players, ranks and versions and both chances within 1e-9."""
    x, y = a.sort("gsis_id"), b.sort("gsis_id")
    if x["gsis_id"].to_list() != y["gsis_id"].to_list():
        return False
    for role in ROLES:
        if x[f"{role}_rank"].to_list() != y[f"{role}_rank"].to_list():
            return False
        if x[f"{role}_version"].to_list() != y[f"{role}_version"].to_list():
            return False
        diff = float((x[f"{role}_prob"] - y[f"{role}_prob"]).abs().max() or 0.0)  # type: ignore[arg-type]
        if diff > CURRENT_TOLERANCE:
            return False
    return True
