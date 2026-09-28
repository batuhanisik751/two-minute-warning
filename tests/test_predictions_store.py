"""The predictions store (C4, spec 8.7): schema, idempotent writes, model_version stability."""

from __future__ import annotations

from datetime import datetime

import duckdb
import polars as pl
import pytest

from twm import predictions as pr

T0 = datetime(2026, 9, 28, 12, 0)
T1 = datetime(2026, 9, 29, 8, 30)


def _version(**over) -> str:
    kw = dict(
        module="waiver_radar", model="lgbm", label="y_hit", features=["b", "a"],
        params={"num_leaves": 15, "learning_rate": 0.05}, training_seasons=[2014, 2013],
        test_season=2015, dataset_hash="abc",
    )  # fmt: skip
    kw.update(over)
    return pr.model_version(**kw)


def test_model_version_is_a_stable_hash_of_what_determines_the_predictions():
    v = _version()
    assert v.startswith("lgbm-") and len(v) == len("lgbm-") + pr.HASH_LENGTH
    assert v == _version()
    # order of features / training seasons / params does not matter
    assert v == _version(features=["a", "b"], training_seasons=[2013, 2014],
                         params={"learning_rate": 0.05, "num_leaves": 15})  # fmt: skip
    for change in (
        {"model": "logit"}, {"label": "y_sustained"}, {"features": ["a"]},
        {"params": {"num_leaves": 31, "learning_rate": 0.05}}, {"training_seasons": [2014]},
        {"test_season": 2016}, {"dataset_hash": "abd"}, {"module": "regression_watch"},
    ):  # fmt: skip
        assert _version(**change) != v, change


def test_frame_hash_follows_content():
    df = pl.DataFrame({"a": [1, 2], "b": [0.1, None]})
    assert pr.frame_hash(df) == pr.frame_hash(df.clone())
    assert pr.frame_hash(df) != pr.frame_hash(df.with_columns(pl.col("a") + 1))
    assert pr.frame_hash(df) != pr.frame_hash(df.rename({"b": "c"}))
    assert pr.combine_hashes(["x", "y"]) != pr.combine_hashes(["y", "x"])


def _frames(version: str, score: float, created: datetime, weeks=(1, 2)):
    preds = pl.DataFrame(
        {
            "module": "waiver_radar", "entity_type": "player",
            "entity_id": ["00-1", "00-2"] * len(weeks),
            "season": 2015, "week": [w for w in weeks for _ in range(2)],
            "as_of": [datetime(2015, 9, 15 + 7 * (w - 1), 14) for w in weeks for _ in range(2)],
            "horizon": 3, "rank_group": "WR", "score": score, "raw_score": score,
            "rank": [1, 2] * len(weeks), "band": None, "model_version": version,
            "reasons_json": "[]", "kind": "backtest", "created_at": created,
        }
    ).with_columns(pl.col("band").cast(pl.String))  # fmt: skip
    versions = pl.DataFrame(
        {
            "model_version": [version], "module": "waiver_radar", "model": "lgbm",
            "label": "y_hit", "feature_list": '["a"]', "params": "{}",
            "training_seasons": "[2013, 2014]", "test_season": 2015, "dataset_hash": "abc",
            "code_version": "twm test", "notes": "{}", "created_at": created,
        }
    )  # fmt: skip
    outcomes = pl.DataFrame(
        {
            "module": "waiver_radar", "entity_id": ["00-1", "00-2"] * len(weeks),
            "season": 2015, "week": [w for w in weeks for _ in range(2)],
            "as_of": [datetime(2015, 9, 15 + 7 * (w - 1), 14) for w in weeks for _ in range(2)],
            "y_hit": [True, False] * len(weeks), "y_sustained": False, "label_status": "final",
        }
    )  # fmt: skip
    return preds, versions, outcomes


def test_store_schema_and_idempotent_writes(tmp_path):
    path = tmp_path / "store" / "predictions.duckdb"
    preds, vers, outs = _frames("lgbm-aaaa", 0.3, T0)
    counts = pr.write_predictions(path, predictions=preds, versions=vers, outcomes=outs)
    assert counts == {"predictions": 4, "model_versions": 1, "outcomes": 4}
    con = duckdb.connect(str(path), read_only=True)
    cols = {r[0]: [c[0] for c in con.execute(f"DESCRIBE {r[0]}").fetchall()]
            for r in con.execute("SHOW TABLES").fetchall()}  # fmt: skip
    con.close()
    assert cols == {name: list(spec) for name, spec in pr.TABLES.items()}
    first = pr.read_table(path, "predictions")
    # the same version again (a re-run, later): replaced, not duplicated
    preds2, vers2, outs2 = _frames("lgbm-aaaa", 0.3, T1)
    pr.write_predictions(path, predictions=preds2, versions=vers2, outcomes=outs2)
    again = pr.read_table(path, "predictions")
    assert again.height == 4
    assert again.drop("created_at").equals(first.drop("created_at"))
    assert again["created_at"].unique().to_list() == [T1]
    assert pr.read_table(path, "model_versions").height == 1
    assert pr.read_table(path, "outcomes").height == 4
    # another version is added next to it; a changed version replaces only its own rows
    pr.write_predictions(path, predictions=_frames("logit-bbbb", 0.5, T1)[0],
                         versions=_frames("logit-bbbb", 0.5, T1)[1])  # fmt: skip
    assert pr.read_table(path, "predictions").height == 8
    p3, v3, _ = _frames("logit-bbbb", 0.7, T1, weeks=(1,))
    pr.write_predictions(path, predictions=p3, versions=v3)
    both = pr.read_table(path, "predictions")
    assert both.filter(pl.col("model_version") == "logit-bbbb").height == 2
    assert both.filter(pl.col("model_version") == "lgbm-aaaa").height == 4
    assert pr.read_table(path, "model_versions", "model_version = 'logit-bbbb'").height == 1
    for table, key in pr.PRIMARY_KEYS.items():  # every key is still unique
        assert not pr.read_table(path, table).select(key).is_duplicated().any(), table
    assert pr.read_table(path, "model_versions").height == 2


def test_store_refuses_bad_rows(tmp_path):
    path = tmp_path / "p.duckdb"
    preds, vers, _ = _frames("lgbm-aaaa", 0.3, T0)
    with pytest.raises(ValueError, match="kind"):
        pr.write_predictions(path, predictions=preds.with_columns(pl.lit("guess").alias("kind")),
                             versions=vers)  # fmt: skip
    with pytest.raises(ValueError, match="repeat the key"):
        pr.write_predictions(path, predictions=pl.concat([preds, preds]), versions=vers)
    with pytest.raises(ValueError, match="unlisted model versions"):
        pr.write_predictions(path, predictions=preds, versions=_frames("x-1", 0.1, T0)[1])
    with pytest.raises(ValueError, match="lack columns"):
        pr.write_predictions(path, predictions=preds.drop("rank"), versions=vers)
    with pytest.raises(ValueError, match="unknown table"):
        pr.read_table(path, "nope")
    assert "twm" in pr.code_version()
