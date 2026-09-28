"""The predictions store (PROJECT_SPEC 8.7): every prediction a module made, the outcomes, and
the model versions behind them, in a local DuckDB file (``data/predictions.duckdb``,
gitignored; settings ``paths.predictions``).

Tables:

- ``predictions``: one row per (model version, entity, season, week): the as-of, the horizon,
  the score (a calibrated probability for a model; the ranking value for a baseline), the
  uncalibrated ``raw_score``, the ``rank`` within ``rank_group`` (the Waiver Radar ranks per
  position), ``band`` and ``reasons_json`` (filled from step C6; NULL / '[]' until then),
  ``kind`` ('backtest' = reconstructed walk-forward, 'live' = made in real time) and
  ``created_at``.
- ``outcomes``: what happened, per (module, entity, season, week): the labels and whether they
  are final.
- ``model_versions``: what produced a prediction: model, label, feature list, hyperparameters,
  training seasons, the dataset hash, the code version and notes.

``model_version`` is a deterministic hash (:func:`model_version`) of the model name, the
label, the sorted feature list, the hyperparameters (fixed settings included), the training and
test seasons and the content hash of the training rows (``dataset_hash``: what the model learned
from; empty for a baseline, which learns nothing): the same trained model always gets the same
version, whichever weeks it scores, and it can be recomputed from its ``model_versions`` row.
The content of the rows a version scored is kept in its notes (``test_rows_hash``), so a
changed input is visible too. ``created_at`` is excluded from every hash.

Writes are idempotent (:func:`write_predictions`): rows of the model versions being written
are replaced, outcomes of the same (module, entity, season, week) are replaced, nothing else
is touched, so each table's key (``PRIMARY_KEYS``) stays unique. Timestamps are naive UTC, like
the warehouse.
"""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from twm import __version__

KINDS = ("backtest", "live")
HASH_LENGTH = 16

PREDICTION_COLUMNS: dict[str, str] = {
    "module": "VARCHAR NOT NULL",
    "entity_type": "VARCHAR NOT NULL",
    "entity_id": "VARCHAR NOT NULL",
    "season": "INTEGER NOT NULL",
    "week": "INTEGER NOT NULL",
    "as_of": "TIMESTAMP NOT NULL",
    "horizon": "INTEGER",
    "rank_group": "VARCHAR",
    "score": "DOUBLE",
    "raw_score": "DOUBLE",
    "rank": "INTEGER NOT NULL",
    "band": "VARCHAR",
    "model_version": "VARCHAR NOT NULL",
    "reasons_json": "VARCHAR NOT NULL",
    "kind": "VARCHAR NOT NULL",
    "created_at": "TIMESTAMP NOT NULL",
}
OUTCOME_COLUMNS: dict[str, str] = {
    "module": "VARCHAR NOT NULL",
    "entity_id": "VARCHAR NOT NULL",
    "season": "INTEGER NOT NULL",
    "week": "INTEGER NOT NULL",
    "as_of": "TIMESTAMP NOT NULL",
    "y_hit": "BOOLEAN",
    "y_sustained": "BOOLEAN",
    "label_status": "VARCHAR NOT NULL",
}
VERSION_COLUMNS: dict[str, str] = {
    "model_version": "VARCHAR NOT NULL",
    "module": "VARCHAR NOT NULL",
    "model": "VARCHAR NOT NULL",
    "label": "VARCHAR NOT NULL",
    "feature_list": "VARCHAR NOT NULL",
    "params": "VARCHAR NOT NULL",
    "training_seasons": "VARCHAR NOT NULL",
    "test_season": "INTEGER",
    "dataset_hash": "VARCHAR NOT NULL",
    "code_version": "VARCHAR NOT NULL",
    "notes": "VARCHAR NOT NULL",
    "created_at": "TIMESTAMP NOT NULL",
}
PRIMARY_KEYS = {
    "predictions": ("model_version", "entity_id", "season", "week"),
    "outcomes": ("module", "entity_id", "season", "week"),
    "model_versions": ("model_version",),
}
TABLES = {
    "predictions": PREDICTION_COLUMNS,
    "outcomes": OUTCOME_COLUMNS,
    "model_versions": VERSION_COLUMNS,
}


def default_path() -> Path:
    from twm.config import settings

    return settings().path("predictions")


# --------------------------------------------------------------------------------------
# Hashes and versions
# --------------------------------------------------------------------------------------


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def frame_hash(df: pl.DataFrame) -> str:
    """sha256 of a frame's content (columns in the given order, rows as given), via CSV text:
    identical frames give identical hashes."""
    buf = io.BytesIO()
    df.write_csv(buf, float_precision=9, datetime_format="%Y-%m-%dT%H:%M:%S%.6f")
    h = hashlib.sha256()
    h.update(_canonical(list(df.columns)).encode())
    h.update(buf.getvalue())
    return h.hexdigest()


def combine_hashes(hashes: Sequence[str]) -> str:
    return hashlib.sha256("|".join(hashes).encode()).hexdigest()


def model_version(
    *,
    module: str,
    model: str,
    label: str,
    features: Sequence[str],
    params: Mapping[str, Any],
    training_seasons: Sequence[int],
    test_season: int | None,
    dataset_hash: str,
) -> str:
    """``<model>-<16 hex>``: a hash of everything that determines the predictions (never the
    creation time)."""
    payload = _canonical(
        {
            "module": module,
            "model": model,
            "label": label,
            "features": sorted(features),
            "params": dict(params),
            "training_seasons": sorted(int(s) for s in training_seasons),
            "test_season": test_season,
            "dataset": dataset_hash,
        }
    )
    return f"{model}-{hashlib.sha256(payload.encode()).hexdigest()[:HASH_LENGTH]}"


def code_version() -> str:
    """The package version plus the git commit when available (informative, never hashed)."""
    from twm.config import ROOT

    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        sha = ""
    return f"twm {__version__}" + (f" ({sha})" if sha else "")


def now_utc() -> datetime:
    """The current time as naive UTC (microseconds)."""
    return datetime.now(UTC).replace(tzinfo=None)


# --------------------------------------------------------------------------------------
# The store
# --------------------------------------------------------------------------------------


def connect(path: Path | str, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open (and create, if needed) the store with its three tables."""
    path = Path(path)
    if read_only:
        con = duckdb.connect(str(path), read_only=True)
        con.execute("SET TimeZone='UTC'")
        return con
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute("SET TimeZone='UTC'")
    for name, cols in TABLES.items():
        # No PRIMARY KEY constraint: DuckDB's key index does not give back its space when rows
        # are replaced, so every re-run would grow the file (measured: +18 MB per full rewrite).
        # write_predictions keeps the keys unique instead (and the tests check it).
        body = ", ".join(f"{c} {t}" for c, t in cols.items())
        con.execute(f"CREATE TABLE IF NOT EXISTS {name} ({body})")
    return con


def _check(df: pl.DataFrame, table: str) -> pl.DataFrame:
    cols = list(TABLES[table])
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"{table} rows lack columns {missing}")
    out = df.select(cols)
    key = list(PRIMARY_KEYS[table])
    if out.select(key).is_duplicated().any():
        raise ValueError(f"{table} rows repeat the key {key}")
    if table == "predictions":
        bad = set(out.get_column("kind").unique().to_list()) - set(KINDS)
        if bad:
            raise ValueError(f"kind must be one of {KINDS}, got {sorted(bad)}")
    return out


def _naive(df: pl.DataFrame) -> pl.DataFrame:
    """Aware timestamps -> naive UTC (the store's convention)."""
    return df.with_columns(
        [
            pl.col(c).dt.convert_time_zone("UTC").dt.replace_time_zone(None)
            for c, t in df.schema.items()
            if isinstance(t, pl.Datetime) and t.time_zone is not None
        ]
    )


def write_predictions(
    path: Path | str,
    *,
    predictions: pl.DataFrame,
    versions: pl.DataFrame,
    outcomes: pl.DataFrame | None = None,
) -> dict[str, int]:
    """Replace the rows of ``versions``' model versions (predictions and version rows) and the
    outcomes of the same (module, entity, season, week), in one transaction. Returns the row
    counts written per table."""
    preds = _naive(_check(predictions, "predictions"))
    vers = _naive(_check(versions, "model_versions"))
    outs = _naive(_check(outcomes, "outcomes")) if outcomes is not None else None
    unknown = set(preds.get_column("model_version").unique().to_list()) - set(
        vers.get_column("model_version").to_list()
    )
    if unknown:
        raise ValueError(f"predictions of unlisted model versions: {sorted(unknown)[:3]}")
    con = connect(path)
    try:
        con.execute("BEGIN TRANSACTION")
        con.register("new_versions", vers.to_arrow())
        con.execute(
            "DELETE FROM predictions WHERE model_version IN "
            "(SELECT model_version FROM new_versions)"
        )
        con.execute(
            "DELETE FROM model_versions WHERE model_version IN "
            "(SELECT model_version FROM new_versions)"
        )
        con.execute("INSERT INTO model_versions SELECT * FROM new_versions")
        con.register("new_predictions", preds.to_arrow())
        con.execute("INSERT INTO predictions SELECT * FROM new_predictions")
        if outs is not None:
            con.register("new_outcomes", outs.to_arrow())
            con.execute(
                "DELETE FROM outcomes o USING new_outcomes n WHERE o.module = n.module AND "
                "o.entity_id = n.entity_id AND o.season = n.season AND o.week = n.week"
            )
            con.execute("INSERT INTO outcomes SELECT * FROM new_outcomes")
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()
    return {
        "predictions": preds.height,
        "model_versions": vers.height,
        "outcomes": 0 if outs is None else outs.height,
    }


def current_versions(path: Path | str, module: str, label: str | None = None) -> pl.DataFrame:
    """The model versions that currently stand for each (model, label, test season) of a
    module: the one written last (``created_at``). A backtest re-run after a code or data
    change writes new versions and leaves the old ones in the store; this picks the new ones.
    Raises if two versions of the same (model, label, test season) share the latest time
    (the store cannot tell which one counts). Sorted by (model, label, test_season)."""
    where = "module = ?" + (" AND label = ?" if label is not None else "")
    params = [module] + ([label] if label is not None else [])
    con = connect(path, read_only=True)
    try:
        out = con.execute(
            "SELECT * FROM (SELECT *, max(created_at) OVER (PARTITION BY model, label, "
            f"test_season) AS _latest FROM model_versions WHERE {where}) "
            "WHERE created_at = _latest ORDER BY model, label, test_season, model_version",
            params,
        ).pl()
    finally:
        con.close()
    out = out.drop("_latest")
    dup = out.filter(pl.struct("model", "label", "test_season").is_duplicated())
    if dup.height:
        first = dup.row(0, named=True)
        raise ValueError(
            f"{dup.height} model versions tie for the latest write of the same model, label and "
            f"test season (e.g. {first['model']} {first['label']} {first['test_season']}); "
            "re-run the backtest"
        )
    return out


def read_table(path: Path | str, table: str, where: str = "") -> pl.DataFrame:
    """A whole table (optionally filtered with a SQL ``where`` clause), sorted by its key."""
    if table not in TABLES:
        raise ValueError(f"unknown table {table!r}; tables: {sorted(TABLES)}")
    con = connect(path, read_only=True)
    try:
        order = ", ".join(PRIMARY_KEYS[table])
        clause = f" WHERE {where}" if where else ""
        return con.execute(f"SELECT * FROM {table}{clause} ORDER BY {order}").pl()
    finally:
        con.close()
