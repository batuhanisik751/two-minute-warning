"""The golden world: frozen synthetic inputs -> a warehouse slice -> every module's outputs.

PROJECT_SPEC 13: "a small frozen season slice with known outputs for each module (update
deliberately)". The slice is built from committed synthetic inputs (``inputs/*.csv.gz``, made
by ``make_inputs.py``; never real data) by the real pipeline, in a temporary folder:

1. :func:`golden_env` points the raw cache at the folder (and forbids any download);
2. :func:`build` writes the inputs as the Parquet cache and runs ``build_warehouse`` (the
   real B1-C1 build) for 2022-2024;
3. :func:`outputs` runs the Waiver Radar on it (pool, labels, features, the dataset, a tiny
   walk-forward backtest of last week's points and the logistic regression, the production
   model, the weekly list with reasons) and renders each result as text (CSV, JSON, markdown)
   with fixed rounding.

``tests/golden/test_golden.py`` compares that text with ``expected/``; ``update.py`` rewrites
``expected/`` on purpose. The default config (config/*.yaml) is used, so these outputs also pin
the default league shape (12 teams: QB 12, RB 24, WR 24, TE 12; pool cutoffs 18/36/36/18).
"""

from __future__ import annotations

import contextlib
import gzip
import io
import json
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

HERE = Path(__file__).resolve().parent
INPUT_DIR = HERE / "inputs"
EXPECTED_DIR = HERE / "expected"
SEASONS = (2022, 2023, 2024)
TEST_SEASONS = (2023, 2024)  # the tiny walk-forward: 2023 learns from 2022 (a thin fold)
BACKTEST_MODELS = ("baseline_last_points", "logit")
WEEKLY = (2024, 3)  # the weekly list: 2024 week 3, scored by the 2024 production fold
GOLDEN_ASOFS = ((2023, 1), (2024, 3))  # the as-ofs whose every row is pinned
T0 = datetime(2026, 9, 28, 12, 0)  # created_at of the stored rows (never in the outputs)
FLOAT_PRECISION = 6
# Model outputs (probabilities, raw scores, chances) are pinned to 4 decimals: the features are
# already rounded at the source, but a solver can differ in the last bits between platforms
# (CI runs on Linux, the owner on macOS), which 6 decimals would turn into a false alarm.
MODEL_DECIMALS = 4

# Upstream dtypes of every input column (from the nflverse schema snapshots, frozen here).
DTYPES: dict[str, dict[str, str]] = {
    "schedules": {"game_id": "String", "season": "Int32", "game_type": "String", "week": "Int32", "gameday": "String", "weekday": "String", "gametime": "String", "away_team": "String", "away_score": "Int32", "home_team": "String", "home_score": "Int32", "result": "Int32", "total": "Int32", "spread_line": "Float64", "total_line": "Float64", "away_coach": "String", "home_coach": "String"},  # noqa: E501
    "pbp": {"game_id": "String", "play_id": "Float64", "season": "Int32", "week": "Int32", "season_type": "String", "game_date": "String", "posteam": "String", "defteam": "String", "home_team": "String", "away_team": "String", "home_coach": "String", "away_coach": "String", "qtr": "Float64", "down": "Float64", "desc": "String", "play_type": "String", "pass": "Float64", "rush": "Float64", "qb_dropback": "Float64", "epa": "Float64", "wp": "Float64", "half_seconds_remaining": "Float64", "score_differential": "Float64", "yardline_100": "Float64", "receiver_player_id": "String", "rusher_player_id": "String", "two_point_attempt": "Float64", "yards_gained": "Float64", "air_yards": "Float64"},  # noqa: E501
    "player_stats": {"player_id": "String", "player_name": "String", "position": "String", "season": "Int32", "week": "Int32", "season_type": "String", "game_id": "String", "team": "String", "opponent_team": "String", "completions": "Int32", "attempts": "Int32", "passing_yards": "Int32", "passing_tds": "Int32", "passing_interceptions": "Int32", "carries": "Int32", "rushing_yards": "Int32", "rushing_tds": "Int32", "receptions": "Int32", "targets": "Int32", "receiving_yards": "Int32", "receiving_tds": "Int32", "receiving_air_yards": "Int32", "target_share": "Float64", "air_yards_share": "Float64", "wopr": "Float64", "fantasy_points_ppr": "Float64"},  # noqa: E501
    "team_stats": {"season": "Int32", "week": "Int32", "team": "String", "season_type": "String", "game_id": "String", "opponent_team": "String", "carries": "Int32", "passing_epa": "Float64"},  # noqa: E501
    "snap_counts": {"game_id": "String", "season": "Int32", "game_type": "String", "week": "Int32", "player": "String", "pfr_player_id": "String", "position": "String", "team": "String", "opponent": "String", "offense_snaps": "Float64", "offense_pct": "Float64"},  # noqa: E501
    "injuries": {"season": "Int32", "game_type": "String", "team": "String", "week": "Int32", "gsis_id": "String", "position": "String", "full_name": "String", "report_status": "String", "practice_status": "String", "date_modified": "Datetime"},  # noqa: E501
    "depth_charts": {"season": "Int32", "club_code": "String", "week": "Int32", "game_type": "String", "depth_team": "String", "last_name": "String", "first_name": "String", "formation": "String", "gsis_id": "String", "position": "String", "depth_position": "String", "full_name": "String"},  # noqa: E501
    "ff_opportunity": {"season": "String", "posteam": "String", "week": "Float64", "game_id": "String", "player_id": "String", "full_name": "String", "position": "String", "rec_attempt": "Float64", "rush_attempt": "Float64", "receptions_exp": "Float64", "rec_yards_gained_exp": "Float64", "rec_touchdown_exp": "Float64", "rush_yards_gained_exp": "Float64", "rush_touchdown_exp": "Float64", "pass_yards_gained_exp": "Float64", "pass_touchdown_exp": "Float64", "pass_interception_exp": "Float64", "receptions": "Float64", "rec_yards_gained": "Float64", "total_fantasy_points": "Float64"},  # noqa: E501
    "rosters_weekly": {"season": "Int32", "week": "Int32", "team": "String", "position": "String", "full_name": "String", "gsis_id": "String", "pfr_id": "String", "game_type": "String", "status": "String", "entry_year": "Int32", "rookie_year": "Int32", "years_exp": "Int32"},  # noqa: E501
    "teams": {"team_abbr": "String", "team_name": "String", "team_conf": "String", "team_division": "String"},  # noqa: E501
    "players": {"gsis_id": "String", "display_name": "String", "position": "String", "position_group": "String", "birth_date": "String", "rookie_season": "Int32", "draft_year": "Int32", "draft_round": "Int32", "draft_pick": "Int32", "draft_team": "String", "pfr_id": "String", "latest_team": "String"},  # noqa: E501
    "ff_playerids": {"gsis_id": "String", "name": "String", "position": "String", "fantasypros_id": "Int64", "pfr_id": "String", "birthdate": "String", "draft_year": "Int64"},  # noqa: E501
    "ff_rankings_all": {"fp_page": "String", "page_type": "String", "player": "String", "id": "String", "pos": "String", "team": "String", "ecr": "Float64", "sd": "Float64", "ecr_type": "String", "scrape_date": "String"},  # noqa: E501
}  # fmt: skip
INPUTS = tuple(DTYPES)
ONE_FILE = ("teams", "players", "ff_playerids", "ff_rankings_all")
_POLARS = {"String": pl.String(), "Int32": pl.Int32(), "Int64": pl.Int64(),
           "Float64": pl.Float64()}  # fmt: skip
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


# --------------------------------------------------------------------------------------
# Inputs (frozen CSV files)
# --------------------------------------------------------------------------------------


def input_path(name: str, season: int | None) -> Path:
    return INPUT_DIR / name / f"{'all' if season is None else season}.csv.gz"


def write_input(name: str, season: int | None, rows: Sequence[dict]) -> int:
    """Write one input file (gzip without a timestamp, so equal content = equal bytes)."""
    cols = list(DTYPES[name])
    unknown = {k for r in rows for k in r} - set(cols)
    if unknown:
        raise KeyError(f"{name}: columns outside DTYPES: {sorted(unknown)}")
    df = pl.DataFrame([{c: r.get(c) for c in cols} for r in rows], schema=_schema(name, text=True),
                      strict=False)  # fmt: skip
    path = input_path(name, season)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = gzip.compress(df.write_csv().encode(), mtime=0)
    path.write_bytes(data)
    return len(data)


def _schema(name: str, *, text: bool = False) -> dict[str, pl.DataType]:
    return {
        c: (pl.String() if t == "Datetime" else _POLARS[t]) if text or t != "Datetime"
        else pl.Datetime("us", "UTC")
        for c, t in DTYPES[name].items()
    }  # fmt: skip


def read_input(name: str, season: int | None) -> pl.DataFrame:
    """One input file with its upstream dtypes."""
    raw = gzip.decompress(input_path(name, season).read_bytes())
    df = pl.read_csv(io.BytesIO(raw), schema=_schema(name, text=True))
    for c, t in DTYPES[name].items():
        if t == "Datetime":
            df = df.with_columns(
                pl.col(c).str.to_datetime(DATETIME_FORMAT).dt.replace_time_zone("UTC")
            )
    return df


def input_size() -> int:
    return sum(p.stat().st_size for p in INPUT_DIR.rglob("*.csv.gz"))


# --------------------------------------------------------------------------------------
# The warehouse slice
# --------------------------------------------------------------------------------------


@contextlib.contextmanager
def golden_env(tmp: Path) -> Iterator[Path]:
    """Point the raw cache at ``tmp/raw`` (and the manual id overrides at an empty folder),
    forbid downloads; restore everything afterwards. Yields the raw folder."""
    from twm import ids
    from twm.sources import nflverse as nv

    root = tmp / "raw"
    saved = (nv.raw_dir, nv._loader, nv._configure_nflreadpy, ids.overrides_path)

    def _no_download(ds):
        raise AssertionError(f"the golden world never downloads ({ds.name})")

    nv.raw_dir = lambda: root  # type: ignore[assignment]
    nv._loader = _no_download  # type: ignore[assignment]
    nv._configure_nflreadpy = lambda: None  # type: ignore[assignment]
    ids.overrides_path = lambda: tmp / "manual" / ids.OVERRIDES_FILE  # type: ignore[assignment]
    try:
        yield root
    finally:
        nv.raw_dir, nv._loader, nv._configure_nflreadpy, ids.overrides_path = saved  # type: ignore[assignment]


def build(tmp: Path) -> Path:
    """Write the inputs as the Parquet cache under ``tmp/raw`` and build the warehouse slice
    (call inside :func:`golden_env`). Returns the warehouse path."""
    from twm.sources import nflverse as nv
    from twm.warehouse import build as wb

    for name in INPUTS:
        for season in (None,) if name in ONE_FILE else SEASONS:
            path = nv.cache_path(name, season)
            path.parent.mkdir(parents=True, exist_ok=True)
            read_input(name, season).write_parquet(path)
    db = tmp / "golden.duckdb"
    wb.build_warehouse(list(SEASONS), db_path=db)
    return db


# --------------------------------------------------------------------------------------
# The outputs
# --------------------------------------------------------------------------------------


def _csv(df: pl.DataFrame) -> str:
    """Deterministic CSV text: lists as JSON, floats rounded, datetimes in UTC ISO."""
    out = df.with_columns(
        pl.col(c).map_elements(lambda v: json.dumps(list(v)), return_dtype=pl.String,
                               skip_nulls=True)
        for c, t in df.schema.items() if isinstance(t, pl.List)
    )  # fmt: skip
    return out.write_csv(float_precision=FLOAT_PRECISION, datetime_format="%Y-%m-%dT%H:%M:%SZ")


def _rows_at(df: pl.DataFrame, asofs: Sequence[tuple[int, int]]) -> pl.DataFrame:
    cond = pl.lit(False)
    for s, w in asofs:
        cond = cond | ((pl.col("season") == s) & (pl.col("week") == w))
    return df.filter(cond).sort("season", "week", "position", "gsis_id")


def _summary(ds: pl.DataFrame) -> pl.DataFrame:
    """Per as-of: rows, pool rows, hits, and sums of a few features (catches a change in any
    week, not only the pinned ones)."""
    return (
        ds.group_by("season", "week")
        .agg(
            pl.len().alias("rows"),
            pl.col("in_pool").sum().alias("pool"),
            pl.col("y_hit").filter(pl.col("in_pool")).sum().alias("pool_hits"),
            pl.col("y_sustained").filter(pl.col("in_pool")).sum().alias("pool_sustained"),
            pl.col("n_flex_finishes").sum().alias("flex_finishes"),
            pl.col("snap_share_avg3").sum().round(4).alias("sum_snap_share_avg3"),
            pl.col("xfp_avg3").sum().round(4).alias("sum_xfp_avg3"),
            pl.col("vacated_target_share").sum().round(4).alias("sum_vacated_target_share"),
            pl.col("teammate_same_pos_unavailable").sum().alias("sum_teammates_out"),
        )
        .sort("season", "week")
    )


def outputs(db: Path, tmp: Path) -> dict[str, str]:
    """Every golden output as text, keyed by its file name under ``expected/`` (call inside
    :func:`golden_env`)."""
    from twm import predictions as pr
    from twm.modules.waiver_radar import backtest as bt
    from twm.modules.waiver_radar import confidence as cf
    from twm.modules.waiver_radar import production as prod
    from twm.modules.waiver_radar import weekly as wk
    from twm.modules.waiver_radar.dataset import build_dataset
    from twm.modules.waiver_radar.features import FEATURE_COLUMNS, INFO_COLUMNS
    from twm.modules.waiver_radar.labels import LABEL_COLUMNS
    from twm.modules.waiver_radar.pool import POOL_COLUMNS

    ds = build_dataset(db, list(SEASONS))
    keys = ["season", "week", "gsis_id"]
    pinned = _rows_at(ds, GOLDEN_ASOFS)
    out: dict[str, str] = {
        "pool.csv": _csv(pinned.select(POOL_COLUMNS)),
        "labels.csv": _csv(pinned.select(*keys, "position", "in_pool", *LABEL_COLUMNS)),
        "features.csv": _csv(pinned.select(*keys, *[c for c in FEATURE_COLUMNS
                                                    if c not in keys], *INFO_COLUMNS)),
        "dataset_summary.csv": _csv(_summary(ds)),
    }  # fmt: skip

    run = bt.run_backtest(ds, models=BACKTEST_MODELS, labels=("y_hit",),
                          test_seasons=TEST_SEASONS)  # fmt: skip
    preds, versions, outcomes = bt.store_frames(run, created_at=T0)
    store = tmp / "predictions.duckdb"
    pr.write_predictions(store, predictions=preds, versions=versions, outcomes=outcomes)
    model_of = dict(zip(versions.get_column("model_version"), versions.get_column("model"),
                        strict=True))  # fmt: skip
    bt_rows = (
        preds.with_columns(pl.col("model_version").replace_strict(model_of).alias("model"))
        .select("model", "season", "week", "rank_group", "rank",
                pl.col("entity_id").alias("gsis_id"), pl.col("score").round(MODEL_DECIMALS),
                pl.col("raw_score").round(MODEL_DECIMALS))
        .sort("model", "season", "week", "rank_group", "rank", "gsis_id")
    )  # fmt: skip
    out["backtest_predictions.csv"] = _csv(bt_rows)
    report = bt.build_backtest_report(run, created="(golden)", command="(golden)",
                                      dataset_name="(golden)")  # fmt: skip
    out["backtest_summary.json"] = (
        json.dumps(
            {"verdict": list(report.summary), "folds": _fold_rows(run)}, indent=1, sort_keys=True
        )
        + "\n"
    )

    season, week = WEEKLY
    pm = prod.train_production(ds, season)
    conf = cf.from_store(store, model="logit", label="y_hit", seasons=(TEST_SEASONS[0],
                         season - 1), min_rows=100)  # fmt: skip
    now = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
    weekly = wk.run_week(db, season, week, model=pm, conf=conf, now=now, real_clock=False)
    top = weekly.scored.filter(pl.col("rank") <= 10).select(
        "position",
        "rank",
        "gsis_id",
        "name",
        "team",
        "score",
        "chance",
        "band_lo",
        "band_hi",
        "tier",
        "reasons_json",
    )
    out["weekly_list.json"] = json.dumps(
        {
            "model_version": pm.model_version,
            "backtest_version_of_the_same_fold": pm.model_version
            in versions.get_column("model_version").to_list(),
            "kind": weekly.kind,
            "freshness_problems": weekly.freshness.problems,
            "list": [
                {**{k: (round(v, MODEL_DECIMALS) if isinstance(v, float) else v)
                    for k, v in r.items() if k != "reasons_json"},
                 "reasons": [x["text"] for x in json.loads(r["reasons_json"])]}
                for r in top.iter_rows(named=True)
            ],
        },
        indent=1,
    ) + "\n"  # fmt: skip
    text = wk.build_report(weekly, generated="(golden: no time)", command="(golden)")
    out["weekly_report.md"] = text
    return out


def _fold_rows(run) -> list[dict]:
    rows = []
    for (label, model), mr in sorted(run.runs.items()):
        for f in mr.folds:
            rows.append({
                "label": label, "model": model, "test_season": f.fold.test_season,
                "train": list(f.fold.train_seasons), "thin": f.fold.thin,
                "params": {k: (round(v, 6) if isinstance(v, float) else v)
                           for k, v in sorted(f.params.items())},
            })  # fmt: skip
    return rows


def compute(tmp: Path) -> dict[str, str]:
    """Build the slice in ``tmp`` and return every output (the one-call entry point)."""
    with golden_env(tmp):
        db = build(tmp)
        return outputs(db, tmp)
