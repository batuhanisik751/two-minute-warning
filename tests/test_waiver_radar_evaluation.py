"""Waiver Radar evaluation (C5): every number comes from the predictions store and equals a
direct SQL recomputation; breakouts caught by hand on a tiny store (no hindsight); the current
model version is the one written last; the CLI writes the report, the CSV and the figures and a
second run gives identical files; the committed report agrees with C4's where they overlap; the
notebook is saved executed, without errors or local paths. Offline and synthetic by default;
opt-in realdata tests evaluate the real store and execute the notebook."""

from __future__ import annotations

import csv
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import polars as pl
import pytest
from typer.testing import CliRunner

from tests.radar_synthetic import synthetic_dataset
from twm import predictions as pr
from twm.cli import app
from twm.modules.waiver_radar import backtest as bt
from twm.modules.waiver_radar import evaluation as ev_mod
from twm.modules.waiver_radar.figures import FIGURES

ROOT = Path(__file__).resolve().parents[1]
TESTS = (2014, 2015, 2016)
NOTEBOOK = ROOT / "notebooks" / "01_waiver_radar.ipynb"
FIG_DIR = ROOT / "reports" / "waiver_radar" / "figures"
MAX_FIGURE_BYTES = 200_000
MAX_NOTEBOOK_BYTES = 2_000_000


def with_eval_columns(ds: pl.DataFrame) -> pl.DataFrame:
    """The descriptive columns the evaluation reads, made up consistently with the labels:
    a starter rank (5) in the window weeks a label needs, 40 otherwise."""
    ranks = (
        pl.when(pl.col("y_sustained")).then(pl.lit([5, 8, 40]))
        .when(pl.col("y_hit")).then(pl.lit([5, 40, 40]))
        .otherwise(pl.lit([40, 40, 50]))
    )  # fmt: skip
    return ds.with_columns(
        pl.lit("SYN").alias("team"),
        pl.concat_list(pl.col("week") + 1, pl.col("week") + 2, pl.col("week") + 3)
        .cast(pl.List(pl.Int32)).alias("window_weeks"),
        ranks.cast(pl.List(pl.Int32)).alias("window_ranks"),
        pl.lit([10.0, 8.0, 2.0]).alias("window_points"),
    )  # fmt: skip


@pytest.fixture(scope="module")
def dataset() -> pl.DataFrame:
    return with_eval_columns(synthetic_dataset())


@pytest.fixture(scope="module")
def store(dataset, tmp_path_factory) -> Path:
    run = bt.run_backtest(dataset, labels=("y_hit", "y_sustained"), test_seasons=TESTS)
    preds, versions, outcomes = bt.store_frames(run, created_at=datetime(2026, 9, 28, 12))
    path = tmp_path_factory.mktemp("store") / "predictions.duckdb"
    pr.write_predictions(path, predictions=preds, versions=versions, outcomes=outcomes)
    return path


@pytest.fixture(scope="module")
def evals(store, dataset) -> dict[str, ev_mod.LabelEvaluation]:
    return {
        lb: ev_mod.evaluate_label(ev_mod.load(store, dataset, lb))
        for lb in ("y_hit", "y_sustained")
    }


# --------------------------------------------------------------------------------------
# Every number equals a direct SQL recomputation on the store
# --------------------------------------------------------------------------------------

LISTS_SQL = """
WITH v AS (SELECT model_version, model FROM model_versions WHERE label = '{label}'),
p AS (
  SELECT v.model, p.season, p.week, p.rank_group AS position, p.entity_id, p.score,
         p.raw_score, p.rank, o.{label} AS y, d.owned_avg
  FROM predictions p JOIN v USING (model_version)
  JOIN outcomes o ON o.entity_id = p.entity_id AND o.season = p.season AND o.week = p.week
  JOIN ds d ON d.gsis_id = p.entity_id AND d.season = p.season AND d.week = p.week
),
s AS (
  SELECT *, CASE WHEN '{subset}' = 'all' THEN rank ELSE row_number() OVER (
    PARTITION BY model, season, week, position
    ORDER BY score DESC NULLS LAST, raw_score DESC NULLS LAST, entity_id) END AS r
  FROM p WHERE '{subset}' = 'all' OR owned_avg IS NULL OR owned_avg < 50
)
SELECT model, season, week, position, count(*) AS n,
       sum(CASE WHEN r <= 10 AND y THEN 1 ELSE 0 END)::BIGINT AS hits_top,
       least(10, count(*)) AS n_top,
       sum(CASE WHEN y THEN 1 ELSE 0 END)::BIGINT AS n_pos
FROM s GROUP BY ALL
"""


def _sql_lists(store: Path, dataset: pl.DataFrame, label: str, subset: str) -> pl.DataFrame:
    con = duckdb.connect(str(store), read_only=True)
    try:
        con.register("ds", dataset.select("season", "week", "gsis_id", "owned_avg").to_arrow())
        return con.execute(LISTS_SQL.format(label=label, subset=subset)).pl()
    finally:
        con.close()


@pytest.mark.parametrize("label", ["y_hit", "y_sustained"])
@pytest.mark.parametrize("subset", ["all", "without_rostered"])
def test_precision_numbers_equal_a_direct_sql_recomputation(store, dataset, evals, label, subset):
    ev = evals[label]
    lists = _sql_lists(store, dataset, label, subset).with_columns(
        (pl.col("hits_top") / pl.col("n_top")).alias("p")
    )
    for model in ev.full_methods:
        mine = lists.filter(pl.col("model") == model)
        for seasons in ([2014, 2015, 2016], [2015, 2016]):
            sub = mine.filter(pl.col("season").is_in(seasons))
            r = ev.one(subset=subset, model=model, scope="pooled",
                       seasons=f"{seasons[0]}-{seasons[-1]}", metric="p_at_10")  # fmt: skip
            assert r["value"] == pytest.approx(sub["p"].mean(), abs=1e-12), (model, seasons)
            assert r["n_groups"] == sub.height and r["n_top_hits"] == sub["hits_top"].sum()
            assert r["n_rows"] == sub["n"].sum() and r["n_pos"] == sub["n_pos"].sum()
            assert r["lo"] <= r["value"] <= r["hi"]
        for pos in ("QB", "RB", "WR", "TE"):
            sub = mine.filter(pl.col("position") == pos)
            r = ev.one(subset=subset, model=model, scope="position", key=pos, metric="p_at_10")
            assert r["value"] == pytest.approx(sub["p"].mean(), abs=1e-12)
        for s in TESTS:
            sub = mine.filter(pl.col("season") == s)
            r = ev.one(subset=subset, model=model, scope="season", key=s, metric="p_at_10")
            assert r["value"] == pytest.approx(sub["p"].mean(), abs=1e-12)
    # the experts, on their own lists and on the covered lists
    ecr = lists.filter(pl.col("model") == "baseline_ecr")
    r = ev.one(subset=subset, model="baseline_ecr", scope="experts", metric="p_at_10")
    assert r["value"] == pytest.approx(ecr["p"].mean(), abs=1e-12) and r["n_groups"] == ecr.height
    logit_cov = lists.filter(pl.col("model") == "logit").join(
        ecr.select("season", "week", "position"), on=["season", "week", "position"], how="semi"
    )
    r = ev.one(subset=subset, model="logit", scope="experts", metric="p_at_10")
    assert r["value"] == pytest.approx(logit_cov["p"].mean(), abs=1e-12)


def test_rank_buckets_and_paired_differences_equal_sql(store, dataset, evals):
    ev = evals["y_hit"]
    con = duckdb.connect(str(store), read_only=True)
    try:
        buckets = con.execute(
            """
            SELECT v.model, CASE WHEN p.rank <= 5 THEN '1-5' WHEN p.rank <= 10 THEN '6-10'
                   ELSE '11-25' END AS b, count(*) AS n, sum(o.y_hit::INT)::BIGINT AS hits
            FROM predictions p JOIN model_versions v USING (model_version)
            JOIN outcomes o ON o.entity_id = p.entity_id AND o.season = p.season
                 AND o.week = p.week
            WHERE v.label = 'y_hit' AND p.rank <= 25 GROUP BY ALL
            """
        ).pl()
    finally:
        con.close()
    for row in buckets.filter(pl.col("model") != "baseline_ecr").iter_rows(named=True):
        r = ev.one(subset="all", model=row["model"], scope="bucket", key=row["b"])
        assert (r["n_rows"], r["n_pos"]) == (row["n"], row["hits"])
        assert r["value"] == pytest.approx(row["hits"] / row["n"])
    lists = _sql_lists(store, dataset, "y_hit", "all").with_columns(
        (pl.col("hits_top") / pl.col("n_top")).alias("p")
    )
    a = lists.filter(pl.col("model") == "logit")["p"].mean()
    b = lists.filter(pl.col("model") == "baseline_last_points")["p"].mean()
    d = ev.one(subset="all", model="logit", scope="diff", seasons="2014-2016",
               key="baseline_last_points", metric="p_at_10_diff")  # fmt: skip
    assert d["value"] == pytest.approx(a - b, abs=1e-12)
    assert d["lo"] <= d["value"] <= d["hi"]
    won = ev.one(subset="all", model="logit", scope="diff", seasons="2014-2016",
                 key="baseline_last_points", metric="seasons_won")  # fmt: skip
    assert won["n_groups"] == 3 and 0 <= won["value"] <= 3


BREAKOUTS_SQL = """
WITH v AS (SELECT model_version, model FROM model_versions WHERE label = 'y_hit'),
p AS (SELECT v.model, p.* FROM predictions p JOIN v USING (model_version)),
firstpool AS (SELECT season, entity_id, min(week) AS fw FROM p WHERE model = 'logit'
              GROUP BY ALL),
brk AS (
  SELECT d.season, d.gsis_id, min(d.week) AS bw FROM ds d
  JOIN firstpool f ON d.season = f.season AND d.gsis_id = f.entity_id
  WHERE d.y_sustained AND d.week >= f.fw AND d.label_status = 'final' GROUP BY ALL
)
SELECT m.model, (SELECT count(*) FROM brk) AS n,
  (SELECT count(*) FROM brk b WHERE EXISTS (
     SELECT 1 FROM p WHERE p.model = m.model AND p.season = b.season
       AND p.entity_id = b.gsis_id AND p.week <= b.bw AND p.rank <= 10)) AS caught,
  (SELECT count(*) FROM brk b WHERE EXISTS (
     SELECT 1 FROM p WHERE p.model = m.model AND p.season = b.season
       AND p.entity_id = b.gsis_id AND p.week <= b.bw AND p.week > b.bw - 3
       AND p.rank <= 10)) AS recent
FROM (SELECT DISTINCT model FROM p) m
"""


def test_breakouts_caught_equal_a_direct_sql_recomputation(store, dataset, evals):
    ev = evals["y_hit"]
    con = duckdb.connect(str(store), read_only=True)
    try:
        con.register(
            "ds",
            dataset.filter(pl.col("season").is_in(TESTS))
            .select("season", "week", "gsis_id", "y_sustained", "label_status")
            .to_arrow(),
        )
        got = con.execute(BREAKOUTS_SQL).pl()
    finally:
        con.close()
    assert got["n"][0] == ev.breakouts["all"].height > 0
    for row in got.filter(pl.col("model") != "baseline_ecr").iter_rows(named=True):
        r = ev.one(subset="all", model=row["model"], scope="breakouts", metric="caught_share")
        rr = ev.one(subset="all", model=row["model"], scope="breakouts",
                    metric="caught_recent_share")  # fmt: skip
        assert (r["n_rows"], r["n_pos"]) == (row["n"], row["caught"]), row["model"]
        assert rr["n_pos"] == row["recent"], row["model"]


def test_the_evaluation_reads_the_store_not_the_features(store, dataset, evals):
    # scrambling every model input of the dataset changes nothing: nothing is re-predicted
    scrambled = dataset.with_columns(
        pl.col("xfp_avg3").shuffle(seed=1), pl.col("fantasy_points_last").shuffle(seed=2),
        pl.col("snap_share_delta").shuffle(seed=3), pl.col("snap_share_last").shuffle(seed=4),
    )  # fmt: skip
    again = ev_mod.evaluate_label(ev_mod.load(store, scrambled, "y_hit"))
    assert again.results == evals["y_hit"].results
    # a dataset whose labels disagree with the store's outcomes is refused
    flipped = dataset.with_columns(
        pl.when((pl.col("season") == 2015) & (pl.col("week") == 2))
        .then(~pl.col("y_hit")).otherwise(pl.col("y_hit")).alias("y_hit")
    )  # fmt: skip
    with pytest.raises(ev_mod.EvaluationError, match="disagree"):
        ev_mod.load(store, flipped, "y_hit")
    with pytest.raises(ev_mod.EvaluationError, match="no row in the dataset"):
        ev_mod.load(store, dataset.filter(pl.col("season") != 2016), "y_hit")
    with pytest.raises(ev_mod.EvaluationError, match="lacks columns"):
        ev_mod.load(store, dataset.drop("window_ranks"), "y_hit")
    with pytest.raises(ValueError, match="unknown label"):
        ev_mod.load_predictions(store, "y_nope")


def test_rostered_rows_are_removed_and_the_lists_reranked(evals):
    ev = evals["y_hit"]
    all_rows, without = ev.ranked["all"], ev.ranked["without_rostered"]
    removed = all_rows.filter(pl.col("owned_avg") >= 50)
    assert removed.height > 0 and without.filter(pl.col("owned_avg") >= 50).height == 0
    assert all_rows.height - without.height == removed.height
    # ranks are 1..n in every list again
    check = without.group_by("model", "season", "week", "position").agg(
        (pl.col("rank").sort() == pl.int_range(1, pl.len() + 1)).all().alias("ok")
    )
    assert check["ok"].all()


# --------------------------------------------------------------------------------------
# The current model version
# --------------------------------------------------------------------------------------


def test_the_latest_written_version_stands(tmp_path, store):
    path = tmp_path / "p.duckdb"
    shutil.copy(store, path)
    con = duckdb.connect(str(path))
    old = con.execute(
        "SELECT model_version FROM model_versions WHERE model = 'logit' AND label = 'y_hit' "
        "AND test_season = 2015"
    ).fetchone()[0]
    # an older, different version of the same model and season (a stale re-run left behind)
    con.execute(
        f"INSERT INTO model_versions SELECT 'logit-stale' AS model_version, * EXCLUDE "
        f"(model_version, created_at), TIMESTAMP '2026-01-01' FROM model_versions "
        f"WHERE model_version = '{old}'"
    )
    con.execute(
        f"INSERT INTO predictions SELECT * REPLACE ('logit-stale' AS model_version, "
        f"1 - score AS score) FROM predictions WHERE model_version = '{old}'"
    )
    con.close()
    cur = pr.current_versions(path, "waiver_radar", "y_hit")
    assert "logit-stale" not in cur["model_version"].to_list()
    assert cur.filter(pl.col("model") == "logit").height == 3
    preds = ev_mod.load_predictions(path, "y_hit")
    assert "logit-stale" not in preds["model_version"].unique().to_list()
    # a tie for the latest write cannot be resolved
    con = duckdb.connect(str(path))
    con.execute("UPDATE model_versions SET created_at = TIMESTAMP '2026-09-28 12:00:00' "
                "WHERE model_version = 'logit-stale'")  # fmt: skip
    con.close()
    with pytest.raises(ValueError, match="tie"):
        pr.current_versions(path, "waiver_radar", "y_hit")


# --------------------------------------------------------------------------------------
# Breakouts caught, by hand, on a tiny store
# --------------------------------------------------------------------------------------

N_PLAYERS = 20
SEASON = 2015
STARTER_WEEKS = {1: {5, 6}, 2: {4, 5}, 3: {2, 3, 6, 7}, 4: {4, 5}, 5: {6, 7}}
NOT_IN_POOL = {(3, 1), (4, 2), (4, 3), (4, 4)}  # (player, as-of week)
RANKS = {  # method -> as-of week -> {player: rank}; the others fill the remaining ranks
    "logit": {
        1: {4: 3, 5: 4, 2: 11, 1: 15},
        2: {3: 5, 1: 8, 2: 11, 5: 14},
        3: {2: 2, 1: 12, 5: 14, 3: 18},
        4: {1: 1, 2: 3, 5: 14, 3: 18},
    },
    "baseline_last_points": {
        1: {2: 1, 1: 12, 4: 13, 5: 14},
        2: {1: 12, 3: 13, 5: 14, 2: 15},
        3: {1: 12, 3: 13, 5: 14, 2: 15},
        4: {5: 1, 1: 2, 3: 13, 2: 15},
    },
}


def _pid(i: int) -> str:
    return f"00-{i:07d}"


def _tiny(tmp_path: Path) -> tuple[Path, pl.DataFrame]:
    """One season, one position (WR), as-ofs 1-4, 20 players; weekly ranks 5 (a starter
    week) in STARTER_WEEKS, else 40; the labels follow from the windows (weeks N+1..N+3)."""
    rows = []
    for w in range(1, 5):
        for i in range(1, N_PLAYERS + 1):
            weeks = [w + 1, w + 2, w + 3]
            starters = [wk in STARTER_WEEKS.get(i, set()) for wk in weeks]
            rows.append(
                {
                    "season": SEASON, "week": w, "gsis_id": _pid(i), "name": f"Player {i}",
                    "team": "TST", "position": "WR", "in_pool": (i, w) not in NOT_IN_POOL,
                    "train_eligible": True, "label_status": "final", "owned_avg": None,
                    "ecr_available": False, "preseason_pos_rank": None, "window_weeks": weeks,
                    "window_ranks": [5 if s else 40 for s in starters],
                    "window_points": [15.0 if s else 3.0 for s in starters],
                    "y_hit": sum(starters) >= 1, "y_sustained": sum(starters) >= 2,
                }
            )  # fmt: skip
    ds = pl.DataFrame(rows).with_columns(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32),
        pl.col("owned_avg").cast(pl.Float64), pl.col("preseason_pos_rank").cast(pl.Int32),
        pl.col("window_weeks").cast(pl.List(pl.Int32)),
        pl.col("window_ranks").cast(pl.List(pl.Int32)),
    )  # fmt: skip
    pool = ds.filter(pl.col("in_pool"))
    created = datetime(2026, 9, 28, 12)
    preds, versions = [], []
    for model, by_week in RANKS.items():
        version = f"{model}-tiny"
        for w in range(1, 5):
            ids = pool.filter(pl.col("week") == w)["gsis_id"].to_list()
            fixed = {_pid(i): r for i, r in by_week[w].items()}
            free = iter(r for r in range(1, len(ids) + 1) if r not in fixed.values())
            ranks = {g: fixed.get(g) or next(free) for g in sorted(ids)}
            for g, r in ranks.items():
                preds.append(
                    {
                        "module": "waiver_radar", "entity_type": "player", "entity_id": g,
                        "season": SEASON, "week": w,
                        "as_of": datetime(SEASON, 9, 15, 14) + timedelta(days=7 * (w - 1)),
                        "horizon": 3, "rank_group": "WR", "score": 1 - r / 100,
                        "raw_score": 1 - r / 100, "rank": r, "band": None,
                        "model_version": version, "reasons_json": "[]", "kind": "backtest",
                        "created_at": created,
                    }
                )  # fmt: skip
        versions.append(
            {
                "model_version": version, "module": "waiver_radar", "model": model,
                "label": "y_hit", "feature_list": "[]", "params": "{}",
                "training_seasons": "[2013, 2014]", "test_season": SEASON, "dataset_hash": "",
                "code_version": "test", "notes": "{}", "created_at": created,
            }
        )  # fmt: skip
    outcomes = pool.select(
        pl.lit("waiver_radar").alias("module"), pl.col("gsis_id").alias("entity_id"), "season",
        "week",
        (pl.lit(datetime(SEASON, 9, 15, 14, tzinfo=UTC))
         + pl.duration(days=7 * (pl.col("week") - 1))).alias("as_of"),
        "y_hit", "y_sustained", "label_status",
    )  # fmt: skip
    path = tmp_path / "tiny.duckdb"
    pr.write_predictions(
        path,
        predictions=pl.DataFrame(preds).with_columns(pl.col("band").cast(pl.String)),
        versions=pl.DataFrame(versions),
        outcomes=outcomes,
    )
    return path, ds


def test_breakouts_caught_by_hand(tmp_path):
    path, ds = _tiny(tmp_path)
    ev = ev_mod.evaluate_label(ev_mod.load(path, ds, "y_hit"))
    events = {r["gsis_id"]: r for r in ev.breakouts["all"].iter_rows(named=True)}
    # player 1: starter weeks 5 and 6 -> the week-3 window (4-6) is the first with two
    # player 2: weeks 4, 5 -> week 2; player 4: out of the pool from week 2, breakout at week 2
    # player 3: a week-1 window with two starter weeks, but he entered the pool only in week 2;
    #           his breakout is the next one, week 4 (weeks 6 and 7)
    # player 5: weeks 6, 7 -> week 4; players 6-20 never
    assert {g: r["breakout_week"] for g, r in events.items()} == {
        _pid(1): 3, _pid(2): 2, _pid(3): 4, _pid(4): 2, _pid(5): 4
    }  # fmt: skip
    assert events[_pid(3)]["first_pool_week"] == 2
    assert events[_pid(4)]["in_pool"] is False and events[_pid(1)]["in_pool"] is True
    # size: the starter weeks after the breakout Tuesday, from the later windows
    assert {g: r["rest_starter_weeks"] for g, r in events.items()} == dict.fromkeys(events, 2)
    assert events[_pid(1)]["rest_points"] == pytest.approx(3 + 15 + 15 + 3)  # weeks 4-7
    caught = ev.caught["all"]

    def per(model: str, col: str) -> set[str]:
        c = caught.filter((pl.col("model") == model) & pl.col(col))
        return set(c["gsis_id"].to_list())

    # logit: 1 (rank 8 in week 2, before week 3; his rank 1 in week 4 comes after), 3 (rank 5
    # in week 2), 4 (rank 3 in week 1), 5 (rank 4 in week 1); NOT 2 (rank 2 only in week 3,
    # after his week-2 breakout)
    assert per("logit", "caught") == {_pid(1), _pid(3), _pid(4), _pid(5)}
    # in the 3 Tuesdays up to the breakout: not 5 (week 1 is 3 weeks before week 4)
    assert per("logit", "caught_recent") == {_pid(1), _pid(3), _pid(4)}
    # last week's points: 2 (rank 1 in week 1) and 5 (rank 1 at the breakout Tuesday itself)
    assert per("baseline_last_points", "caught") == {_pid(2), _pid(5)}
    assert per("baseline_last_points", "caught_recent") == {_pid(2), _pid(5)}
    r = ev.one(subset="all", model="logit", scope="breakouts", metric="caught_share")
    assert (r["n_rows"], r["n_pos"], r["value"]) == (5, 4, pytest.approx(0.8))
    r = ev.one(subset="all", model="baseline_last_points", scope="breakouts",
               metric="caught_recent_share")  # fmt: skip
    assert (r["n_rows"], r["n_pos"]) == (5, 2)
    d = ev.one(subset="all", model="logit", scope="breakouts_diff", key="baseline_last_points",
               metric="caught_share_diff")  # fmt: skip
    assert d["value"] == pytest.approx(0.8 - 0.4) and d["lo"] == pytest.approx(0.4)  # 1 season
    best = {
        x["gsis_id"]: (x["best_rank"], x["best_order"])
        for x in caught.filter(pl.col("model") == "logit").iter_rows(named=True)
    }
    assert best[_pid(1)] == (8, 2) and best[_pid(2)] == (11, 1)
    missed = ev_mod.biggest_breakouts(ev, caught=False)
    assert missed["gsis_id"].to_list() == [_pid(2)]
    assert missed["last_points_best_rank"].to_list() == [1]


# --------------------------------------------------------------------------------------
# The report, the CLI and the figures
# --------------------------------------------------------------------------------------


def test_report_sections_and_counts(evals):
    rep = ev_mod.build_evaluation_report(list(evals.values()), generated="Generated at X.")
    text = rep.markdown
    for part in ("## Headlines", "### Precision@10, pooled, with 95% intervals",
                 "### Paired differences", "### Per season", "*season median*",
                 "### Per position (2014-2016, 95% intervals)", "### Hit rate by rank",
                 "### PR-AUC and Brier", "### Calibration of the winner", "fixed-width bin",
                 "### The experts", "### Breakouts caught", "in the 3 Tuesdays before",
                 "players flagged", "biggest breakouts", "## Label `y_sustained`"):  # fmt: skip
        assert part in text, part
    assert text.count("Generated at X.") == 1
    assert "/Users/" not in text and "/private/" not in text
    assert all(list(r) == list(ev_mod.CSV_COLUMNS) for r in rep.csv_rows)
    # every rate row carries its counts
    for r in rep.csv_rows:
        if r["metric"] in ("p_at_10", "caught_share", "hit_rate", "base_rate"):
            assert r["n_rows"] is not None and r["n_pos"] is not None, r
        if r["metric"] == "p_at_10":
            assert r["n_groups"] is not None and r["n_top_hits"] is not None, r


def test_cli_evaluate_writes_report_csv_and_figures_deterministically(store, dataset, tmp_path):
    src = tmp_path / "ds.parquet"
    dataset.write_parquet(src)
    out = tmp_path / "rep" / "evaluation.md"
    args = ["radar", "evaluate", "--store", str(store), "--dataset", str(src), "--out", str(out)]
    runner = CliRunner()
    res = runner.invoke(app, args)
    assert res.exit_code == 0, res.output
    assert "y_hit" in res.output and "wrote 5 figures" in res.output
    figs = {name: (out.parent / "figures" / name).read_bytes() for name in FIGURES}
    assert all(0 < len(b) < MAX_FIGURE_BYTES for b in figs.values())
    assert all(b.startswith(b"\x89PNG") for b in figs.values())
    md, csv_text = out.read_text(), out.with_suffix(".csv").read_text()
    assert "![Breakouts caught per method](figures/breakouts_caught.png)" in md
    # a second run: identical CSV and figures, the report differs only in its "Generated at"
    assert runner.invoke(app, args).exit_code == 0
    assert out.with_suffix(".csv").read_text() == csv_text
    assert {n: (out.parent / "figures" / n).read_bytes() for n in FIGURES} == figs
    strip = lambda t: [x for x in t.splitlines() if not x.startswith("Generated at")]  # noqa: E731
    assert strip(out.read_text()) == strip(md)
    # one label, no figures
    one = runner.invoke(app, [*args, "--label", "y_sustained", "--no-figures"])
    assert one.exit_code == 0 and "wrote 5 figures" not in one.output
    assert "## Label `y_hit`" not in out.read_text()
    bad = runner.invoke(app, [*args, "--label", "nope"])
    assert bad.exit_code == 2 and "unknown label" in bad.output
    missing = runner.invoke(app, ["radar", "evaluate", "--dataset", str(tmp_path / "x.pq")])
    assert missing.exit_code == 1 and "run `twm radar dataset` first" in missing.output
    no_store = runner.invoke(app, [*args[:2], "--store", str(tmp_path / "none.duckdb"),
                                   "--dataset", str(src), "--out", str(out)])  # fmt: skip
    assert no_store.exit_code == 1 and "not found" in no_store.output


# --------------------------------------------------------------------------------------
# The committed report and notebook (cheap checks of the files in the repository)
# --------------------------------------------------------------------------------------


def _csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_committed_evaluation_agrees_with_the_c4_backtest_report():
    """C5 recomputes from the store what C4 computed in memory: where both report a number,
    it must be the same (precision@10 pooled, per position, per season; rank buckets; PR-AUC
    and Brier; the experts on their covered lists)."""
    c4 = _csv(ROOT / "reports" / "waiver_radar" / "backtest.csv")
    c5 = _csv(ROOT / "reports" / "waiver_radar" / "evaluation.csv")
    index = {
        (r["label"], r["subset"], r["model"], r["scope"], r["seasons"], r["key"], r["metric"]): r
        for r in c5
    }
    scope_map = {"pooled": "pooled", "position": "position", "season": "season",
                 "bucket": "bucket", "ecr_covered": "experts",
                 "calibration": "calibration_equal"}  # fmt: skip
    checked = 0
    for r in c4:
        if r["value"] == "" or r["model"].startswith("reference"):
            continue
        scope = scope_map.get(r["scope"])
        key = "" if r["scope"] in ("pooled", "ecr_covered") else r["key"]
        if r["scope"] == "season" and r["metric"] in ("pr_auc", "brier"):
            scope = "prob"
            key = r["key"] if "pooled" not in r["key"] else "pooled"
        if scope is None:
            continue
        seasons = r["seasons"].replace(" pooled", "")
        mine = index.get((r["label"], r["subset"], r["model"], scope, seasons, key, r["metric"]))
        assert mine is not None, r
        # both CSVs round to 6 decimals; a value on a rounding edge may differ in the last one
        assert float(mine["value"]) == pytest.approx(float(r["value"]), abs=2e-6), r
        checked += 1
    assert checked > 300
    headline = index[("y_hit", "all", "logit", "pooled", "2014-2025", "", "p_at_10")]
    assert float(headline["value"]) == pytest.approx(0.47828, abs=5e-6)


def test_committed_figures_are_small_pngs():
    for name in FIGURES:
        path = FIG_DIR / name
        data = path.read_bytes()
        assert data.startswith(b"\x89PNG") and len(data) < MAX_FIGURE_BYTES, name


def test_committed_notebook_is_executed_clean_and_portable():
    import nbformat

    raw = NOTEBOOK.read_text(encoding="utf-8")
    assert len(raw.encode()) < MAX_NOTEBOOK_BYTES
    nb = nbformat.reads(raw, as_version=4)
    nbformat.validate(nb)
    code = [c for c in nb.cells if c.cell_type == "code"]
    assert len(code) >= 10
    for c in code:
        assert c.execution_count is not None, c.source[:60]
        for out in c.get("outputs", []):
            assert out.output_type != "error", c.source[:60]
            assert out.get("name") != "stderr", (c.source[:60], out.get("text", "")[:200])
    counts = [c.execution_count for c in code]
    assert counts == list(range(1, len(code) + 1))  # run top to bottom in one go
    for marker in ("/Users/", "/home/", "/private/", "C:\\\\"):
        assert marker not in raw, marker
    titles = [c.source.splitlines()[0] for c in nb.cells if c.cell_type == "markdown"]
    for n in range(1, 8):
        assert any(t.startswith(f"## {n}.") for t in titles), n
    assert json.loads(raw)["metadata"]["kernelspec"]["name"] == "python3"


# --------------------------------------------------------------------------------------
# Real data (opt-in: `uv run pytest -m realdata`)
# --------------------------------------------------------------------------------------


def _real_paths() -> tuple[Path, Path]:
    store = pr.default_path()
    ds = ROOT / "data" / "waiver_radar" / "dataset.parquet"
    if not store.exists() or not ds.exists():
        pytest.skip("run `uv run twm radar dataset` and `uv run twm radar backtest` first")
    return store, ds


@pytest.mark.realdata
def test_real_evaluation_reproduces_the_c4_headline():
    store, ds = _real_paths()
    ev = ev_mod.evaluate_label(ev_mod.load(store, pl.read_parquet(ds), "y_hit"))
    r = ev.one(subset="all", model="logit", scope="pooled", seasons="2014-2025",
               metric="p_at_10")  # fmt: skip
    assert r["value"] == pytest.approx(0.47828, abs=5e-6)
    assert (r["n_groups"], r["n_rows"], r["n_pos"]) == (732, 91_638, 9_726)
    assert r["lo"] < r["value"] < r["hi"] and r["hi"] - r["lo"] < 0.06
    assert ev.winner == "logit"
    d = ev.one(subset="all", model="logit", scope="diff", seasons="2014-2025",
               key="baseline_last_points", metric="p_at_10_diff")  # fmt: skip
    assert d["lo"] > 0  # the Radar beats last week's points beyond the noise
    b = ev.one(subset="all", model="logit", scope="breakouts", metric="caught_share")
    assert b["n_rows"] > 500 and 0 < b["value"] < 1


@pytest.mark.realdata
def test_real_notebook_executes(tmp_path):
    _real_paths()
    import nbformat
    from nbclient import NotebookClient

    nb = nbformat.read(NOTEBOOK, as_version=4)
    work = tmp_path / "notebooks"
    work.mkdir()
    NotebookClient(nb, timeout=600, kernel_name="python3",
                   resources={"metadata": {"path": str(ROOT / "notebooks")}}).execute()  # fmt: skip
    errors = [o for c in nb.cells if c.cell_type == "code" for o in c.get("outputs", [])
              if o.output_type == "error"]  # fmt: skip
    assert not errors
