"""The predictions store (PROJECT_SPEC 8.7): every prediction a module made, the outcomes, and
the model versions behind them, in a local DuckDB file (``data/predictions.duckdb``,
gitignored; settings ``paths.predictions``).

Tables:

- ``predictions``: one row per (model version, entity, season, week): the as-of, the horizon,
  the score (a calibrated probability for a model; the ranking value for a baseline), the
  uncalibrated ``raw_score``, the ``rank`` within ``rank_group`` (the Waiver Radar ranks per
  position), ``band`` and ``reasons_json`` (filled by the weekly lists of step C6: the band as
  JSON text, the reasons as a JSON list; NULL / '[]' for walk-forward backtest rows), ``kind``
  ('backtest' = reconstructed, 'live' = made in real time), ``created_at``, and (added in C6,
  appended so older stores migrate in place) ``tier`` (the suggested priority of a weekly list:
  'must-add', 'speculative', 'watch' or NULL) and ``incomplete`` (TRUE when the week was scored
  although some of its data had not arrived: ``twm radar score --allow-incomplete``).
- ``outcomes``: what happened, per (module, entity, season, week): the labels and whether they
  are final; ``y_value`` (added in D4a, appended so older stores migrate in place) is a numeric
  outcome (Regression Watch: the rest-of-season points per game), NULL for the Radar's rows.
- ``model_versions``: what produced a prediction: model, label, feature list, hyperparameters,
  training seasons, the dataset hash, the code version and notes.

``model_version`` is a deterministic hash (:func:`model_version`) of the model name, the
label, the sorted feature list, the hyperparameters (fixed settings included), the training and
test seasons and the content hash of the training rows (``dataset_hash``: what the model learned
from; empty for a baseline, which learns nothing): the same trained model always gets the same
version, whichever weeks it scores, and it can be recomputed from its ``model_versions`` row.
The content of the rows a version scored is kept in its notes (``test_rows_hash``), so a
changed input is visible too. ``created_at`` is excluded from every hash.

Writes are idempotent (:func:`write_predictions`). A backtest (``replace='versions'``)
replaces every row of the model versions it writes; a weekly list (``replace='weeks'``)
replaces only the rows of the (model version, season, week) it writes, keeps an existing
``model_versions`` row as it is, and never overwrites a stored 'live' week with a
reconstructed ('backtest') one. Outcomes of the same (module, entity, season, week) are
replaced; nothing else is touched, so each table's key (``PRIMARY_KEYS``) stays unique.
Timestamps are naive UTC, like the warehouse.
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
    # added in C6 (weekly lists); ALTERed into older stores by connect()
    "tier": "VARCHAR",
    "incomplete": "BOOLEAN",
}
# Columns a writer may leave out (filled with these defaults): the C6 additions.
PREDICTION_DEFAULTS: dict[str, tuple[Any, pl.DataType]] = {
    "tier": (None, pl.String()),
    "incomplete": (False, pl.Boolean()),
}
TIERS = ("must-add", "speculative", "watch")
REPLACE_MODES = ("versions", "weeks")
OUTCOME_COLUMNS: dict[str, str] = {
    "module": "VARCHAR NOT NULL",
    "entity_id": "VARCHAR NOT NULL",
    "season": "INTEGER NOT NULL",
    "week": "INTEGER NOT NULL",
    "as_of": "TIMESTAMP NOT NULL",
    "y_hit": "BOOLEAN",
    "y_sustained": "BOOLEAN",
    "label_status": "VARCHAR NOT NULL",
    # added in D4a (a numeric outcome: Regression Watch's rest-of-season points per game);
    # ALTERed into older stores by connect(), NULL for the Radar's rows
    "y_value": "DOUBLE",
}
# Outcome columns a writer may leave out (filled with these defaults): the D4a addition.
OUTCOME_DEFAULTS: dict[str, tuple[Any, pl.DataType]] = {"y_value": (None, pl.Float64())}
# The outcome columns before D4a (what the Radar's approved backtest snapshot holds).
LEGACY_OUTCOME_COLUMNS = tuple(c for c in OUTCOME_COLUMNS if c not in OUTCOME_DEFAULTS)
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
    # a store written before C6 lacks the appended prediction columns: add them in place
    for col in PREDICTION_DEFAULTS:
        con.execute(
            f"ALTER TABLE predictions ADD COLUMN IF NOT EXISTS {col} {PREDICTION_COLUMNS[col]}"
        )
    # ... and a store written before D4a lacks the numeric outcome column
    for col in OUTCOME_DEFAULTS:
        con.execute(f"ALTER TABLE outcomes ADD COLUMN IF NOT EXISTS {col} {OUTCOME_COLUMNS[col]}")
    return con


def _check(df: pl.DataFrame, table: str) -> pl.DataFrame:
    cols = list(TABLES[table])
    if table == "predictions":
        df = df.with_columns(
            pl.lit(value, dtype=dtype).alias(c)
            for c, (value, dtype) in PREDICTION_DEFAULTS.items()
            if c not in df.columns
        )
    if table == "outcomes":
        df = df.with_columns(
            pl.lit(value, dtype=dtype).alias(c)
            for c, (value, dtype) in OUTCOME_DEFAULTS.items()
            if c not in df.columns
        )
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
        bad = set(out.get_column("tier").drop_nulls().unique().to_list()) - set(TIERS)
        if bad:
            raise ValueError(f"tier must be one of {TIERS} or NULL, got {sorted(bad)}")
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


class LiveWeekError(ValueError):
    """A reconstructed ('backtest') list would overwrite a stored 'live' list of the same week."""


def write_predictions(
    path: Path | str,
    *,
    predictions: pl.DataFrame,
    versions: pl.DataFrame,
    outcomes: pl.DataFrame | None = None,
    replace: str = "versions",
) -> dict[str, int]:
    """Write predictions, their model versions and (optionally) outcomes in one transaction.
    Returns the row counts written per table.

    ``replace='versions'`` (a backtest): every stored prediction and version row of
    ``versions``' model versions is replaced. ``replace='weeks'`` (a weekly list): only the
    stored predictions of the same (model version, season, week) are replaced, a version row
    already in the store is kept as it is (its ``created_at`` says when the model was made),
    and a week whose stored rows are 'live' is never replaced by 'backtest' rows
    (:class:`LiveWeekError`): the real-time record is kept. Outcomes of the same (module,
    entity, season, week) are replaced in both modes."""
    if replace not in REPLACE_MODES:
        raise ValueError(f"replace must be one of {REPLACE_MODES}, not {replace!r}")
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
        con.register("new_predictions", preds.to_arrow())
        if replace == "versions":
            con.execute(
                "DELETE FROM predictions WHERE model_version IN "
                "(SELECT model_version FROM new_versions)"
            )
            con.execute(
                "DELETE FROM model_versions WHERE model_version IN "
                "(SELECT model_version FROM new_versions)"
            )
            con.execute("INSERT INTO model_versions SELECT * FROM new_versions")
        else:
            clash = con.execute(
                "SELECT p.model_version, p.season, p.week FROM predictions p JOIN "
                "(SELECT DISTINCT model_version, season, week FROM new_predictions "
                " WHERE kind = 'backtest') n USING (model_version, season, week) "
                "WHERE p.kind = 'live' LIMIT 1"
            ).fetchone()
            if clash is not None:
                raise LiveWeekError(
                    f"{clash[1]} week {clash[2]} already has a live list of {clash[0]}; a "
                    "reconstructed run would overwrite the real-time record, so it is not stored"
                )
            con.execute(
                "DELETE FROM predictions p USING (SELECT DISTINCT model_version, season, week "
                "FROM new_predictions) n WHERE p.model_version = n.model_version "
                "AND p.season = n.season AND p.week = n.week"
            )
            con.execute(
                "INSERT INTO model_versions SELECT * FROM new_versions WHERE model_version "
                "NOT IN (SELECT model_version FROM model_versions)"
            )
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


def write_outcomes(path: Path | str, outcomes: pl.DataFrame) -> int:
    """Write outcomes alone (D4a: Regression Watch grades its stored lists once a season is
    over): the stored outcomes of the same (module, entity, season, week) are replaced, nothing
    else is touched. Returns the rows written."""
    outs = _naive(_check(outcomes, "outcomes"))
    if outs.height == 0:
        return 0
    con = connect(path)
    try:
        con.execute("BEGIN TRANSACTION")
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
    return outs.height


def register_version(path: Path | str, version: pl.DataFrame) -> bool:
    """Record one trained model (a ``model_versions`` row) and make it the current one of its
    (module, model, label, test season) (:func:`current_versions`). A version already stored
    keeps its row; only if another version was written after it is its ``created_at`` moved to
    the given time, so it is current again. Returns True when a new row was added."""
    vers = _naive(_check(version, "model_versions"))
    if vers.height != 1:
        raise ValueError("register_version takes exactly one model_versions row")
    row = vers.row(0, named=True)
    con = connect(path)
    try:
        con.execute("BEGIN TRANSACTION")
        con.register("new_version", vers.to_arrow())
        stored = con.execute(
            "SELECT count(*) FROM model_versions WHERE model_version = ?", [row["model_version"]]
        ).fetchone()
        added = not (stored and stored[0])
        if added:
            con.execute("INSERT INTO model_versions SELECT * FROM new_version")
        else:
            con.execute(
                "UPDATE model_versions SET created_at = ? WHERE model_version = ? AND created_at "
                "< (SELECT max(created_at) FROM model_versions WHERE module = ? AND model = ? "
                "AND label = ? AND test_season IS NOT DISTINCT FROM ?)",
                [row["created_at"], row["model_version"], row["module"], row["model"],
                 row["label"], row["test_season"]],
            )  # fmt: skip
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()
    return added


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
