"""The production K and D/ST streamer (S2a): the owner-approved method per position, its pin and
its frozen backtest.

**What ranks each position** (reports/streamer/backtest.md; reviewer decisions after S1d):

- **K**: the logistic regression (``logit_k``), kept by the rule fixed before the results: it
  is ahead of every baseline on precision@5, but not clearly (an interval includes zero). Its
  calibrated probability beats a constant forecast (Brier), so a kicker gets a 'chance'.
- **DST**: the **next-opponent rule** (``baseline_opponent_dst``: the fewer points next
  week's opponent scores per game, the better), because it beat the model (precision@5 40.3%
  vs 39.1%). A method that lost is never shipped: the D/ST list is that rule, said plainly on
  the list and in docs/streamer.md. Nothing is fitted; a D/ST row has no model probability.

**The K model for season S** is exactly the fold the backtest would use for test season S
(:func:`twm.backtest.walkforward.fit_fold`, the Radar's harness): pool rows of every season
before S whose label was public by S's first Tuesday as-of (the backtest's training cutoff,
:func:`twm.modules.streamer.backtest.training_rows`), tuned and calibrated (isotonic) on S-1,
then refit on all of them. A constant model is refused. Its version is the backtest's
fingerprint for test season S. It is the Radar's
:class:`~twm.modules.waiver_radar.production.ProductionModel` (model 'logit', label 'y_start'),
saved and loaded with the Radar's own writer and reader (a pickle: loaded only after its
sha256 matches the pin).

**The DST rule** is a JSON file (:func:`rule_definition`): column, direction, tie-breakers and a
version (the backtest's fingerprint of the rule for season S).

**Pins** (``config/production_models.yaml``, :mod:`twm.pins`): ``streamer_k`` (``model: logit``;
snapshot = the K backtest's predictions, their labels and the fold versions) and
``streamer_dst`` (``model: rule``; snapshot = the rule's hit-rate table: per test season, list
length and rank, how many lists and how many starts). The labels come from the streamer dataset;
nothing is written to the predictions store's outcomes table (it holds the Radar's labels).
"""

from __future__ import annotations

import json
import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from twm.backtest.walkforward import fit_fold, production_fold
from twm.modules.streamer import backtest as bt
from twm.modules.streamer import models as sm
from twm.modules.waiver_radar import production as rprod
from twm.predictions import combine_hashes

PIN_KEYS = {"K": "streamer_k", "DST": "streamer_dst"}
MODEL_K = "logit"
RULE_METHOD = "baseline_opponent"
RULE_FORMAT = 1
ARTIFACT_SUBDIR = "streamer"  # artifacts/production_models/streamer/


class StreamerProductionError(ValueError):
    """The production K model or D/ST rule cannot be built, loaded or approved as asked."""


def k_training_rows(dataset: pl.DataFrame, season: int) -> pl.DataFrame:
    """The K pool rows the model for ``season`` may learn from (the backtest's rules)."""
    source = sm.training_source(dataset, "pool").filter(pl.col("position") == "K")
    cutoff = bt.training_cutoff(dataset, season)
    train = bt.training_rows(source, season, cutoff)
    bt.assert_available_by(train, cutoff, season)
    return train.sort(list(sm.KEYS))


def train_k(dataset: pl.DataFrame, season: int) -> rprod.ProductionModel:
    """Train the K fold that ranks ``season`` (module docstring); refuses a constant model."""
    rows = k_training_rows(dataset, season)
    seasons = sorted({int(s) for s in rows.get_column("season").to_list()})
    if not seasons:
        raise StreamerProductionError(f"no labelled K pool rows before {season}")
    fold = production_fold(seasons, season)
    train = rows.filter(pl.col("season").is_in(list(fold.train_seasons)))
    est, features = sm.estimator(MODEL_K), sm.position_features("K")
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Skipping features without any observed")
        fr = fit_fold(train, fold, estimator=est, features=features, label=sm.LABEL,
                      module=sm.MODULE, keys=sm.KEYS, tune_metric=sm.tune_metric())  # fmt: skip
    x = train.sort(list(sm.KEYS)).select(list(features))
    if sm.is_constant(pl.Series(fr.model.predict(x))):
        raise StreamerProductionError(
            f"the K model for {season} scores every training row alike (settings {fr.params}): "
            "a constant model cannot rank kickers"
        )
    params = {**fr.params, "fixed": dict(est.fixed_params)}
    dhash = combine_hashes([bt._hash(train.filter(pl.col("season") == s))
                            for s in fold.train_seasons])  # fmt: skip
    cutoff = bt.training_cutoff(dataset, season)
    record = bt._version(
        MODEL_K, "K", features, params, fold.train_seasons, season, dhash,
        {"kind": "production", "validation_season": fold.val_season, "thin": fold.thin,
         "calibration": fr.calibration, "training_cutoff": str(cutoff),
         "n_train": fr.n_train, "n_train_pos": fr.n_train_pos},
    )  # fmt: skip
    prep = fr.model.pipeline.named_steps["prep"]  # type: ignore[attr-defined]
    means = np.asarray(prep.transform(rprod._as_model_input(x)), dtype=np.float64).mean(axis=0)
    return rprod.ProductionModel(
        model_version=record["model_version"], season=int(season), model=MODEL_K,
        label=sm.LABEL, features=tuple(features), fold=fold, params=params,
        fitted=fr.model, calibrator=fr.calibrator, train_means=means,  # type: ignore[arg-type]
        version_row=record, n_train=fr.n_train, n_train_pos=fr.n_train_pos,
        calibration=fr.calibration,
    )  # fmt: skip


# --------------------------------------------------------------------------------------
# The D/ST rule
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProductionRule:
    """The D/ST ranking rule of one season (nothing fitted)."""

    model_version: str
    season: int
    column: str
    higher_is_better: bool
    version_row: dict[str, Any]

    @property
    def stored_name(self) -> str:
        return bt.stored_name(RULE_METHOD, "DST")

    def describe(self) -> str:
        way = "more" if self.higher_is_better else "fewer"
        return (
            f"{self.model_version}: the next-opponent rule ({way} {self.column} is better; "
            "ties -> last game's points, then points per game, then preseason rank), nothing fitted"
        )

    def definition(self) -> dict[str, Any]:
        return {
            "format": RULE_FORMAT, "model": "rule", "module": sm.MODULE, "position": "DST",
            "method": RULE_METHOD, "stored_name": self.stored_name, "label": sm.LABEL,
            "column": self.column, "higher_is_better": self.higher_is_better,
            "tie_break": [[c, desc] for c, desc in sm.TIE_BREAK], "season": self.season,
            "model_version": self.model_version,
            "description": sm.BASELINES[RULE_METHOD].description,
        }  # fmt: skip


def rule_definition(season: int) -> ProductionRule:
    """The D/ST rule for ``season``; its version is the backtest's fingerprint of the rule for
    that test season (:func:`twm.modules.streamer.backtest.run_baseline`)."""
    col, higher = sm.BASELINES[RULE_METHOD].columns["DST"]
    params = {"column": col, "higher_is_better": higher}
    record = bt._version(RULE_METHOD, "DST", [col], params, [], int(season), combine_hashes([]),
                         {"kind": "production rule",
                          "description": sm.BASELINES[RULE_METHOD].description})  # fmt: skip
    return ProductionRule(record["model_version"], int(season), col, higher, record)


def rule_json(rule: ProductionRule) -> str:
    """The rule file's exact text (sorted keys: the same rule always gives the same bytes)."""
    return json.dumps(rule.definition(), sort_keys=True, indent=2) + "\n"


def load_rule(path: Path) -> ProductionRule:
    """Read a rule file and check it is the production rule it says it is."""
    if not path.exists():
        raise StreamerProductionError(f"rule file not found: {path}")
    try:
        d = json.loads(path.read_text())
    except ValueError as e:
        raise StreamerProductionError(f"{path} is not a rule file: {e}") from e
    if not isinstance(d, dict) or d.get("format") != RULE_FORMAT or d.get("model") != "rule":
        raise StreamerProductionError(f"{path} is not a rule file of this version of the code")
    rule = rule_definition(int(d["season"]))
    if rule_json(rule) != path.read_text():
        raise StreamerProductionError(
            f"{path} is not the production D/ST rule of {d['season']} (the code's rule is "
            f"{rule.model_version})"
        )
    return rule


def version_frame(model: rprod.ProductionModel | ProductionRule, *, created_at: Any = None):
    """The ``model_versions`` row of the K model or the D/ST rule."""
    return rprod.version_frame(model, created_at=created_at)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# The frozen backtest (from the predictions store + the dataset's labels)
# --------------------------------------------------------------------------------------

OUTCOME_SCHEMA = {
    "module": pl.String, "entity_id": pl.String, "season": pl.Int32, "week": pl.Int32,
    "position": pl.String, "as_of": pl.Datetime("us"), "y_start": pl.Boolean,
    "label_status": pl.String,
}  # fmt: skip
HIT_RATE_KEYS = ("season", "list_length", "rank")


def store_backtest(
    store: Path, stored_model: str, season: int
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(model_versions, predictions) of the store's current backtest of ``stored_model``
    (e.g. 'logit_k') for the test seasons before ``season``; opened read-only."""
    from twm import predictions as pr

    if not Path(store).exists():
        raise StreamerProductionError(f"predictions store not found: {store}")
    vers = pr.current_versions(store, sm.MODULE, sm.LABEL).filter(
        (pl.col("model") == stored_model) & (pl.col("test_season") < season)
    )
    if vers.height == 0:
        raise StreamerProductionError(
            f"{store} has no {stored_model} backtest before {season}: run `uv run twm streamer "
            "backtest --store ...` first"
        )
    ids = vers.get_column("model_version").to_list()
    con = pr.connect(store, read_only=True)
    try:
        preds = con.execute(
            f"SELECT * FROM predictions WHERE module = ? AND kind = 'backtest' AND model_version "
            f"IN ({', '.join('?' for _ in ids)}) "
            "ORDER BY model_version, season, week, rank_group, rank, entity_id",
            [sm.MODULE, *ids],
        ).pl()
    finally:
        con.close()
    return vers.sort("test_season"), preds


def with_labels(preds: pl.DataFrame, dataset: pl.DataFrame) -> pl.DataFrame:
    """``preds`` + position + y_start + label_status + as_of from the dataset (graded rows);
    every prediction must have a final label."""
    truth = sm.graded_rows(dataset).select(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32), "position", "entity_id",
        pl.col(sm.LABEL).alias("y_start"), "label_status",
    )  # fmt: skip
    out = preds.with_columns(pl.col("rank_group").alias("position")).join(
        truth, on=list(sm.KEYS), how="left"
    )
    if out.get_column("y_start").null_count():
        raise StreamerProductionError(
            f"{out.get_column('y_start').null_count()} backtest predictions have no final label "
            "in the dataset: rebuild the dataset and the backtest together"
        )
    return out


def k_snapshot(
    store: Path, dataset: pl.DataFrame, season: int
) -> tuple[dict[str, pl.DataFrame], str]:
    """({predictions, outcomes, model_versions}, seasons) of the K model's backtest."""
    vers, preds = store_backtest(store, bt.stored_name(MODEL_K, "K"), season)
    lab = with_labels(preds, dataset)
    outs = lab.select(
        pl.lit(sm.MODULE).alias("module"), "entity_id", "season", "week", "position", "as_of",
        "y_start", "label_status",
    ).cast(OUTCOME_SCHEMA).sort("season", "week", "entity_id")  # type: ignore[arg-type]  # fmt: skip
    return {"predictions": preds, "outcomes": outs, "model_versions": vers}, _span(vers)


def hit_rate_table(ranked: pl.DataFrame) -> pl.DataFrame:
    """Per (test season, list length, rank): lists = how many lists had that rank, starts =
    how many of those picks started (``ranked``: season, week, position, rank, y_start)."""
    sizes = ranked.group_by("season", "week", "position").agg(pl.len().alias("list_length"))
    return (
        ranked.join(sizes, on=["season", "week", "position"])
        .group_by(*HIT_RATE_KEYS)
        .agg(
            pl.len().cast(pl.Int64).alias("lists"),
            pl.col("y_start").cast(pl.Int64).sum().alias("starts"),
        )  # fmt: skip
        .with_columns(pl.col(c).cast(pl.Int32) for c in HIT_RATE_KEYS)
        .sort(list(HIT_RATE_KEYS))
    )


def dst_snapshot(
    store: Path, dataset: pl.DataFrame, season: int
) -> tuple[dict[str, pl.DataFrame], str]:
    """({hit_rates}, seasons) of the D/ST rule's backtest."""
    vers, preds = store_backtest(store, bt.stored_name(RULE_METHOD, "DST"), season)
    return {"hit_rates": hit_rate_table(with_labels(preds, dataset))}, _span(vers)


def _span(vers: pl.DataFrame) -> str:
    seen = sorted(int(s) for s in vers.get_column("test_season").to_list())
    return f"{seen[0]}-{seen[-1]}" if seen[0] != seen[-1] else str(seen[0])


# --------------------------------------------------------------------------------------
# Pinned files: checked before they are opened
# --------------------------------------------------------------------------------------


def _pinned_file(position: str, season: int, path: Path | None, root: Path | None):
    from twm import pins

    key = PIN_KEYS[position]
    pin = pins.get_pin(key, path)
    want = "rule" if position == "DST" else MODEL_K
    if pin.model != want:
        raise pins.PinError(f"the pin of {key} says model {pin.model!r}, not {want!r}")
    if pin.season != int(season):
        raise pins.PinError(
            f"the approved {key} scores season {pin.season}, not {season}: approve one for "
            "this season (`uv run twm streamer pin`), review it and commit it"
        )
    file = pin.path(root)
    if not file.exists():
        raise pins.PinError(f"the approved file is missing: {pin.file}")
    digest = pins.sha256_of(file)
    if digest != pin.sha256:
        raise pins.PinError(
            f"{pin.file} is not the approved file (sha256 {digest[:12]}..., the pin says "
            f"{pin.sha256[:12]}...); it was not opened"
        )
    return pin, file


def load_pinned_k(season: int, *, path: Path | None = None, root: Path | None = None):
    """(the approved K model, its pin) for ``season``; :class:`twm.pins.PinError` otherwise."""
    from twm import pins

    pin, file = _pinned_file("K", season, path, root)
    try:
        pm = rprod.load_model(file)
    except rprod.ProductionModelError as e:
        raise pins.PinError(str(e)) from e
    if pm.model_version != pin.model_version:
        raise pins.PinError(
            f"{pin.file} holds {pm.model_version}, the pin says {pin.model_version}"
        )
    if int(pm.season) != int(season) or pm.model != MODEL_K or pm.label != sm.LABEL:
        raise pins.PinError(
            f"{pin.file} holds a {pm.model}/{pm.label} model for {pm.season}, not the "
            f"streamer's {MODEL_K}/{sm.LABEL} K model for {season}"
        )
    return pm, pin


def load_pinned_rule(season: int, *, path: Path | None = None, root: Path | None = None):
    """(the approved D/ST rule, its pin) for ``season``."""
    from twm import pins

    pin, file = _pinned_file("DST", season, path, root)
    try:
        rule = load_rule(file)
    except StreamerProductionError as e:
        raise pins.PinError(str(e)) from e
    if rule.model_version != pin.model_version or rule.season != int(season):
        raise pins.PinError(
            f"{pin.file} holds {rule.model_version}, the pin says {pin.model_version}"
        )
    return rule, pin


def load_k_snapshot(pin: Any, root: Path | None = None) -> dict[str, pl.DataFrame]:
    """The K backtest snapshot (sha256 + rows checked), which must hang together."""
    from twm import pins

    frames = pins.read_snapshot(pin, pins.BACKTEST_TABLES, root)
    v, p, o = frames["model_versions"], frames["predictions"], frames["outcomes"]
    names = set(v.get_column("model").unique().to_list())
    if names != {bt.stored_name(MODEL_K, "K")}:
        raise pins.PinError(f"the K snapshot holds {sorted(names)}, not logit_k only")
    if not set(p.get_column("model_version").unique().to_list()) <= set(v["model_version"]):
        raise pins.PinError("the K snapshot has predictions of versions it does not list")
    keys = ["entity_id", "season", "week"]
    if p.select(keys).join(o, on=keys, how="anti").height:
        raise pins.PinError("the K snapshot has predictions without a label")
    return frames


def load_hit_rates(pin: Any, root: Path | None = None) -> pl.DataFrame:
    from twm import pins

    return pins.read_snapshot(pin, ("hit_rates",), root)["hit_rates"]


# --------------------------------------------------------------------------------------
# The snapshot must reproduce the committed backtest report (the published track record)
# --------------------------------------------------------------------------------------

TOLERANCE = 1.01e-6  # the CSV prints 6 decimals


def k_lists(frames: dict[str, pl.DataFrame]) -> pl.DataFrame:
    """One row per K list of the snapshot (sm.list_table: p_at_1/3/5, n_rows, n_pos)."""
    p = frames["predictions"].select(
        "season", "week", pl.col("rank_group").alias("position"), "entity_id", "rank", "score"
    )
    rows = p.join(frames["outcomes"].select("season", "week", "entity_id", "y_start"),
                  on=["season", "week", "entity_id"], how="left")  # fmt: skip
    return sm.list_table(rows, "y_start")


def dst_precision(hit_rates: pl.DataFrame, k: int) -> pl.DataFrame:
    """Per season: lists, rows, starts and the summed precision@k of the rule's lists (a list
    shorter than k divides by its length), from the hit-rate table alone."""
    h = hit_rates.with_columns(pl.min_horizontal(pl.col("list_length"), pl.lit(k)).alias("_k"))
    return h.group_by("season").agg(
        pl.col("lists").filter(pl.col("rank") == 1).sum().alias("n_groups"),
        pl.col("lists").sum().alias("n_rows"), pl.col("starts").sum().alias("n_pos"),
        (pl.col("starts") / pl.col("_k")).filter(pl.col("rank") <= pl.col("_k")).sum()
        .alias("p_sum"),
    ).sort("season")  # fmt: skip


def track_record(k_frames: dict[str, pl.DataFrame], hit_rates: pl.DataFrame) -> dict[tuple, tuple]:
    """{(position, method, scope, seasons, metric): (value, n_groups, n_rows, n_pos)} of the
    rows of reports/streamer/backtest.csv the snapshots determine."""
    out: dict[tuple, tuple] = {}
    lists = k_lists(k_frames)
    span = _span(k_frames["model_versions"])
    for k in sm.LIST_KS:
        m = f"p_at_{k}"
        out[("K", MODEL_K, "pooled", span, m)] = (
            float(lists[m].mean()), lists.height,  # type: ignore[arg-type]
            int(lists["n_rows"].sum()), int(lists["n_pos"].sum()),
        )  # fmt: skip
        d = dst_precision(hit_rates, k)
        seasons = d.get_column("season").to_list()
        dspan = f"{min(seasons)}-{max(seasons)}" if len(seasons) > 1 else str(seasons[0])
        out[("DST", RULE_METHOD, "pooled", dspan, m)] = (
            float(d["p_sum"].sum() / d["n_groups"].sum()), int(d["n_groups"].sum()),
            int(d["n_rows"].sum()), int(d["n_pos"].sum()),
        )  # fmt: skip
        if k == sm.TUNE_K:
            for r in d.iter_rows(named=True):
                out[("DST", RULE_METHOD, "season", str(r["season"]), m)] = (
                    r["p_sum"] / r["n_groups"], r["n_groups"], r["n_rows"], r["n_pos"])  # fmt: skip
            by = lists.group_by("season").agg(pl.col(m).mean(), pl.len(), pl.col("n_rows").sum(),
                                              pl.col("n_pos").sum())  # fmt: skip
            for s, v, n, rows, pos in by.iter_rows():
                out[("K", MODEL_K, "season", str(s), m)] = (v, n, rows, pos)
    p = k_frames["predictions"].join(k_frames["outcomes"], on=["entity_id", "season", "week"])
    err = (p["score"] - p["y_start"].cast(pl.Float64)) ** 2
    out[("K", MODEL_K, "brier", span, "brier")] = (float(err.mean()), None, None, None)  # type: ignore[arg-type]
    return out


def track_record_mismatches(
    k_frames: dict[str, pl.DataFrame], hit_rates: pl.DataFrame, csv_path: Path
) -> list[str]:
    """Where the snapshots disagree with the committed backtest CSV ([] = consistent)."""
    if not csv_path.exists():
        return [f"the backtest report is missing: {csv_path}"]
    csv = pl.read_csv(csv_path, infer_schema_length=0).filter(pl.col("train_on") == "pool")
    problems = []
    for (pos, method, scope, seasons, metric), got in sorted(
        track_record(k_frames, hit_rates).items()
    ):
        want = csv.filter((pl.col("position") == pos) & (pl.col("method") == method)
                          & (pl.col("scope") == scope) & (pl.col("seasons") == seasons)
                          & (pl.col("metric") == metric))  # fmt: skip
        what = f"{pos} {method} {scope} {seasons} {metric}"
        if want.height != 1:
            problems.append(f"{what}: not in the report")
            continue
        w = want.row(0, named=True)
        if abs(float(w["value"]) - got[0]) > TOLERANCE:
            problems.append(f"{what}: {got[0]:.6f} in the snapshot, {w['value']} in the report")
        for col, g in zip(("n_groups", "n_rows", "n_pos"), got[1:], strict=True):
            if g is not None and str(g) != (w[col] or ""):
                problems.append(f"{what}: {col} {g} in the snapshot, {w[col]} in the report")
    return problems


# --------------------------------------------------------------------------------------
# Approving (`twm streamer pin`)
# --------------------------------------------------------------------------------------


def artifact_dir(root: Path | None = None) -> Path:
    from twm import pins
    from twm.config import ROOT

    return (root if root is not None else ROOT) / pins.ARTIFACT_DIR / ARTIFACT_SUBDIR


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


def _rel(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return str(path)


def approve(
    dataset: pl.DataFrame,
    *,
    store: Path,
    season: int,
    csv_path: Path,
    root: Path | None = None,
    path: Path | None = None,
    today: Any = None,
    positions: Sequence[str] = ("K", "DST"),
) -> dict[str, Any]:
    """Approve the K model and the D/ST rule for ``season`` with the store's backtest (opened
    read-only) and the dataset's labels: train the K fold (an existing file of the same version
    is kept byte for byte: pickling is not byte-stable), write the rule file, export both
    snapshots, refuse unless they reproduce ``csv_path`` (reports/streamer/backtest.csv), and
    pin both in config/production_models.yaml (other pins keep theirs). {position: Pin}.

    ``positions`` re-approves only those (e.g. ``("DST",)`` after a D/ST scoring change): the
    other position's pin, file and snapshot are left byte for byte, and its PINNED snapshot
    (sha256-checked) must reproduce ``csv_path`` together with the new one."""
    from datetime import UTC, datetime

    from twm import pins
    from twm.config import ROOT

    chosen = tuple(dict.fromkeys(p.upper() for p in positions))
    if not chosen or set(chosen) - set(PIN_KEYS):
        raise StreamerProductionError(f"positions {list(positions)}: choose from {list(PIN_KEYS)}")
    base = root if root is not None else ROOT
    if "K" in chosen:
        k_frames, k_span = k_snapshot(store, dataset, season)
    else:
        k_frames = load_k_snapshot(load_pinned_k(season, path=path, root=root)[1], root)
    if "DST" in chosen:
        d_frames, d_span = dst_snapshot(store, dataset, season)
    else:
        d_frames = {
            "hit_rates": load_hit_rates(load_pinned_rule(season, path=path, root=root)[1], root)
        }
    problems = track_record_mismatches(k_frames, d_frames["hit_rates"], csv_path)
    if problems:
        raise StreamerProductionError(
            f"the store's backtest disagrees with {csv_path} in {len(problems)} places (e.g. "
            f"{problems[0]}): run `uv run twm streamer backtest --store <this store>` so the "
            "report and the lists agree"
        )
    day = (today or datetime.now(UTC)).astimezone(UTC).date().isoformat()
    made = {}
    if "K" in chosen:
        pm = train_k(dataset, season)
        target = rprod.model_path(pm.model_version, artifact_dir(base))
        keep = False
        if target.exists():
            try:
                keep = rprod.load_model(target).model_version == pm.model_version
            except rprod.ProductionModelError:
                keep = False
        k_file = target if keep else rprod.save_model(pm, target.parent)
        k_snap = _write_snapshot(k_frames, pm.model_version, base)
        made["K"] = pins.Pin(PIN_KEYS["K"], int(season), pm.model_version, _rel(k_file, base),
                             pins.sha256_of(k_file), day, k_span, k_snap, MODEL_K)  # fmt: skip
    if "DST" in chosen:
        rule = rule_definition(season)
        r_file = artifact_dir(base) / f"{rule.model_version}.json"
        r_file.write_text(rule_json(rule))
        d_snap = _write_snapshot(d_frames, rule.model_version, base)
        made["DST"] = pins.Pin(PIN_KEYS["DST"], int(season), rule.model_version,
                               _rel(r_file, base), pins.sha256_of(r_file), day, d_span, d_snap,
                               "rule")  # fmt: skip
    current = pins.read_pins(path)
    current.update({p.module: p for p in made.values()})
    pins.write_pins(current, path)
    return made
