"""Small made-up inputs for `twm publish` tests (offline): a warehouse, a predictions store,
a Waiver Radar dataset and an evaluation CSV, shaped like the real ones.

Two backtest seasons (2024, 2025; weeks 1-3) of the winner ``logit`` for ``y_hit`` with final
outcomes, and live lists of the current season (2026) for the weeks asked for. Every list has
``POOL`` players per position, so the top 25 are published and the pool size is ``POOL``.
Deterministic: the same call gives the same files.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import polars as pl

from twm import predictions as pr
from twm.config import FANTASY_POSITIONS
from twm.modules.waiver_radar.evaluation import CSV_COLUMNS
from twm.publish.collect import Inputs
from twm.scoring import ScoringRules, xfp_columns

SEASON = 2026
BACKTEST = (2024, 2025)
WEEKS = (1, 2, 3)
POOL = 30
TEAMS = ("BUF", "DAL", "KC", "SF")
NOW = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)
MODULE = "waiver_radar"


def gid(pos_index: int, i: int) -> str:
    return f"00-0{pos_index}{i:05d}"


def team_of(i: int) -> str:
    return TEAMS[i % len(TEAMS)]


def as_of(season: int, week: int) -> datetime:
    return datetime(season, 9, 9, 14) + timedelta(days=7 * (week - 1))  # naive UTC


def _version(model_version: str, test_season: int, created: datetime) -> dict:
    return {
        "model_version": model_version, "module": MODULE, "model": "logit", "label": "y_hit",
        "feature_list": json.dumps(["snap_share_last", "xfp_avg3"]),
        "params": json.dumps({"C": 0.01, "fixed": {"solver": "liblinear"}}),
        "training_seasons": json.dumps(list(range(2013, test_season))),
        "test_season": test_season, "dataset_hash": "synthetic", "code_version": "test",
        "notes": "{}", "created_at": created,
    }  # fmt: skip


def _hit(pos_index: int, i: int, season: int, week: int) -> bool:
    return (i * 7 + week * 3 + season + pos_index) % 5 == 0 or i < 3


def week_rows(
    season: int,
    week: int,
    model_version: str,
    *,
    kind: str,
    created: datetime,
    incomplete: bool = False,
    shift: int = 0,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(predictions, outcomes) of one week: POOL players per position, ranked by a score that
    falls with i. ``shift`` rotates who is ranked first (a different list)."""
    preds, outs = [], []
    c6 = season == SEASON
    for p_i, pos in enumerate(FANTASY_POSITIONS):
        for rank in range(1, POOL + 1):
            i = (rank - 1 + shift) % POOL
            score = round(0.9 - 0.025 * rank, 6)
            band = (
                json.dumps({"chance": round(score, 4), "lo": round(score - 0.03, 4),
                            "hi": round(score + 0.03, 4), "n": 600, "hits": 300,
                            "p_from": 0.1, "p_to": 0.2, "level": 0.9})
                if c6 else None
            )  # fmt: skip
            reasons = (
                json.dumps([{"text": f"Reason {k} for {gid(p_i, i)}", "feature": "xfp_avg3",
                             "theme": "t", "contribution": 0.1} for k in (1, 2)])
                if c6 else "[]"
            )  # fmt: skip
            tier = "must-add" if score >= 0.5 else "speculative" if score >= 0.25 else "watch"
            preds.append({
                "module": MODULE, "entity_type": "player", "entity_id": gid(p_i, i),
                "season": season, "week": week, "as_of": as_of(season, week), "horizon": 3,
                "rank_group": pos, "score": score, "raw_score": score * 2, "rank": rank,
                "band": band, "model_version": model_version, "reasons_json": reasons,
                "kind": kind, "created_at": created,
                "tier": tier if c6 and rank <= 25 else None, "incomplete": incomplete,
            })  # fmt: skip
            final = season < SEASON
            outs.append({
                "module": MODULE, "entity_id": gid(p_i, i), "season": season, "week": week,
                "as_of": as_of(season, week),
                "y_hit": _hit(p_i, i, season, week) if final else None,
                "y_sustained": False if final else None,
                "label_status": "final" if final else "pending",
            })  # fmt: skip
    return pl.DataFrame(preds), pl.DataFrame(outs)


def write_backtest(store: Path) -> None:
    for test_season in BACKTEST:
        created = datetime(2026, 9, 28, 7, 0)
        version = f"logit-backtest{test_season}"
        frames = [week_rows(test_season, w, version, kind="backtest", created=created)
                  for w in WEEKS]  # fmt: skip
        pr.write_predictions(
            store,
            predictions=pl.concat([f[0] for f in frames]),
            versions=pl.DataFrame([_version(version, test_season, created)]),
            outcomes=pl.concat([f[1] for f in frames]),
            replace="versions",
        )


PROD = "logit-prod2026"


def write_live_week(
    store: Path,
    week: int,
    *,
    kind: str = "live",
    incomplete: bool = False,
    shift: int = 0,
    version: str = PROD,
    created: datetime | None = None,
) -> None:
    """A current-season list (C6) of ``week``, written like `twm radar score` does."""
    created = created or datetime(2026, 9, 9, 15, 0) + timedelta(days=7 * week)
    preds, outs = week_rows(
        SEASON, week, version, kind=kind, created=created, incomplete=incomplete, shift=shift
    )
    pr.write_predictions(
        store,
        predictions=preds,
        versions=pl.DataFrame([_version(version, SEASON, datetime(2026, 9, 1, 12, 0))]),
        outcomes=outs,
        replace="weeks",
    )


def write_dataset(path: Path, weeks_2026: tuple[int, ...] = (1, 2, 3)) -> None:
    rows = []
    for season in (*BACKTEST, SEASON):
        weeks = WEEKS if season != SEASON else weeks_2026
        for week in weeks:
            for p_i, pos in enumerate(FANTASY_POSITIONS):
                # POOL players in the pool plus one rostered player outside it
                for i in range(POOL + 1):
                    final = season < SEASON
                    rows.append({
                        "season": season, "week": week, "gsis_id": gid(p_i, i),
                        "name": f"Player {p_i}-{i}", "team": team_of(i), "position": pos,
                        "in_pool": i < POOL,
                        "y_hit": _hit(p_i, i, season, week) if final else None,
                        "y_sustained": False if final else None,
                        "label_status": "final" if final else "pending",
                        "window_weeks": [week + 1, week + 2, week + 3],
                        "window_ranks": [i + 1, None, i + 3] if final else [None, None, None],
                        "window_points": [10.5, None, 3.25] if final else [None, None, None],
                    })  # fmt: skip
    df = pl.DataFrame(
        rows,
        schema={"season": pl.Int32, "week": pl.Int32, "gsis_id": pl.String, "name": pl.String,
                "team": pl.String, "position": pl.String, "in_pool": pl.Boolean,
                "y_hit": pl.Boolean, "y_sustained": pl.Boolean, "label_status": pl.String,
                "window_weeks": pl.List(pl.Int32), "window_ranks": pl.List(pl.Int32),
                "window_points": pl.List(pl.Float64)},
        orient="row",
    )  # fmt: skip
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path)


def write_evaluation(path: Path) -> list[dict[str, str]]:
    rows = [
        {"label": "y_hit", "subset": "all", "model": "logit", "scope": "pooled",
         "seasons": "2014-2025", "key": "", "metric": "p_at_10", "value": "0.478279",
         "lo": "0.462222", "hi": "0.494519", "n_groups": "732", "n_rows": "91638",
         "n_pos": "9726", "n_top_hits": "3501", "n_top": "7320"},
        {"label": "y_hit", "subset": "without_rostered", "model": "logit", "scope": "pooled",
         "seasons": "2014-2025", "key": "", "metric": "p_at_10", "value": "0.455055",
         "lo": "0.436062", "hi": "0.47473", "n_groups": "732", "n_rows": "90375",
         "n_pos": "9085", "n_top_hits": "3331", "n_top": "7320"},
        {"label": "y_hit", "subset": "all", "model": "logit", "scope": "position",
         "seasons": "2014-2025", "key": "QB", "metric": "p_at_10", "value": "0.434",
         "lo": "", "hi": "", "n_groups": "183", "n_rows": "1", "n_pos": "1", "n_top_hits": "",
         "n_top": ""},
        {"label": "y_hit", "subset": "all", "model": "baseline_last_points", "scope": "position",
         "seasons": "2014-2025", "key": "QB", "metric": "p_at_10", "value": "0.431", "lo": "",
         "hi": "", "n_groups": "183", "n_rows": "1", "n_pos": "1", "n_top_hits": "",
         "n_top": ""},
        {"label": "y_hit", "subset": "all", "model": "logit", "scope": "position_diff",
         "seasons": "2014-2025", "key": "QB vs baseline_last_points", "metric": "p_at_10_diff",
         "value": "0.003", "lo": "-0.006", "hi": "0.013", "n_groups": "", "n_rows": "",
         "n_pos": "", "n_top_hits": "", "n_top": ""},
        {"label": "y_sustained", "subset": "all", "model": "logit", "scope": "season",
         "seasons": "2014", "key": "2014", "metric": "calibration", "value": "", "lo": "",
         "hi": "", "n_groups": "", "n_rows": "0", "n_pos": "0", "n_top_hits": "", "n_top": ""},
    ]  # fmt: skip
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(CSV_COLUMNS))
        w.writeheader()
        w.writerows(rows)
    return rows


def write_warehouse(path: Path) -> None:
    """The warehouse tables a publish reads, with only the columns it reads."""
    rules = ScoringRules.from_config()
    stat_cols = rules.required_columns()
    exp_cols = sorted(set(xfp_columns(rules).values()))
    con = duckdb.connect(str(path))
    try:
        con.execute(
            "CREATE TABLE dim_team (team_abbr VARCHAR, team_name VARCHAR, team_nick VARCHAR, "
            "team_conf VARCHAR, team_division VARCHAR, team_color VARCHAR, team_color2 VARCHAR, "
            "team_logo_espn VARCHAR, is_current BOOLEAN)"
        )
        for i, t in enumerate(TEAMS):
            con.execute(
                "INSERT INTO dim_team VALUES (?, ?, ?, ?, ?, ?, ?, ?, TRUE)",
                [t, f"Team {t}", f"Nick {t}", "AFC" if i % 2 else "NFC",
                 "AFC East" if i % 2 else "NFC West", "#000000", "#FFFFFF",
                 "https://logo.example/x.png"],
            )  # fmt: skip
        con.execute(
            "INSERT INTO dim_team VALUES ('OAK', 'Old Team', 'Old', 'AFC', 'AFC West', '#000000', "
            "'#FFFFFF', NULL, FALSE)"
        )
        con.execute(
            "CREATE TABLE dim_player (gsis_id VARCHAR, display_name VARCHAR, position VARCHAR, "
            "latest_team VARCHAR, draft_year INTEGER, draft_round INTEGER, draft_pick INTEGER, "
            "rookie_season INTEGER, espn_id VARCHAR, pfr_id VARCHAR)"
        )
        for p_i, pos in enumerate(FANTASY_POSITIONS):
            # player 29 is missing here on purpose: his name comes from the dataset
            for i in range(POOL + 1):
                if i == POOL - 1:
                    continue
                con.execute(
                    "INSERT INTO dim_player VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [gid(p_i, i), f"Player {p_i}-{i}", pos, team_of(i), 2020, 1 + i % 7,
                     i + 1, 2020, f"espn{i}", f"pfr{i}"],
                )  # fmt: skip
        cols = ", ".join(f"{c} INTEGER" for c in stat_cols)
        con.execute(
            "CREATE TABLE fact_player_week (player_id VARCHAR, player_display_name VARCHAR, "
            "season INTEGER, week INTEGER, season_type VARCHAR, game_id VARCHAR, team VARCHAR, "
            f"position VARCHAR, target_share DOUBLE, carries INTEGER, {cols})"
        )
        con.execute(
            "CREATE TABLE fact_snaps (gsis_id VARCHAR, season INTEGER, week INTEGER, "
            "game_type VARCHAR, game_id VARCHAR, team VARCHAR, position VARCHAR, "
            "offense_snaps DOUBLE, offense_pct DOUBLE)"
        )
        con.execute(
            "CREATE TABLE fact_team_week (season INTEGER, week INTEGER, season_type VARCHAR, "
            "game_id VARCHAR, team VARCHAR, carries INTEGER)"
        )
        exp = ", ".join(f"{c} DOUBLE" for c in exp_cols)
        con.execute(
            "CREATE TABLE fact_opportunity_week (season INTEGER, week INTEGER, "
            f"season_type VARCHAR, game_id VARCHAR, player_id VARCHAR, posteam VARCHAR, {exp})"
        )
        for season in (2025, SEASON):
            for week in (1, 2):
                for t in TEAMS:
                    game = f"{season}_{week:02d}_{t}"
                    con.execute(
                        "INSERT INTO fact_team_week VALUES (?, ?, 'REG', ?, ?, 25)",
                        [season, week, game, t],
                    )
                for p_i, pos in enumerate(FANTASY_POSITIONS):
                    for i in range(4):  # a few players per position play
                        t = team_of(i)
                        game = f"{season}_{week:02d}_{t}"
                        pid = gid(p_i, i)
                        if i < 3:  # a stat line (player 3 only has snaps)
                            con.execute(
                                "INSERT INTO fact_player_week (player_id, player_display_name, "
                                "season, week, season_type, game_id, team, position, "
                                "target_share, carries, receptions, receiving_yards, "
                                "rushing_yards) VALUES (?, ?, ?, ?, 'REG', ?, ?, ?, ?, ?, ?, ?, ?)",
                                [pid, f"Player {p_i}-{i}", season, week, game, t, pos,
                                 0.1 * i, 5 + i, 3, 40 + i, 10],
                            )  # fmt: skip
                        con.execute(
                            "INSERT INTO fact_snaps VALUES (?, ?, ?, 'REG', ?, ?, ?, ?, ?)",
                            [pid, season, week, game, t, pos, 30.0 + i, round(0.5 + 0.1 * i, 4)],
                        )
                        con.execute(
                            "INSERT INTO fact_opportunity_week (season, week, season_type, "
                            "game_id, player_id, posteam, receptions_exp, rec_yards_gained_exp) "
                            "VALUES (?, ?, 'REG', ?, ?, ?, 4.0, 35.0)",
                            [season, week, game, pid, t],
                        )
                # a lineman with snaps: never published
                con.execute(
                    "INSERT INTO fact_snaps VALUES ('00-0099999', ?, ?, 'REG', ?, 'KC', 'T', "
                    "60, 1.0)",
                    [season, week, f"{season}_{week:02d}_KC"],
                )
        con.execute(
            "CREATE TABLE fact_game (season INTEGER, week INTEGER, result INTEGER, "
            "available_at TIMESTAMP)"
        )
        con.execute(
            "INSERT INTO fact_game VALUES (2026, 2, 3, '2026-09-22 07:00:00'), "
            "(2026, 3, NULL, '2026-09-29 07:00:00'), (2026, 1, 7, '2026-09-15 07:00:00')"
        )
        con.execute("CREATE TABLE build_manifest (table_name VARCHAR, built_at TIMESTAMP)")
        con.execute("INSERT INTO build_manifest VALUES ('fact_game', '2026-09-28 05:40:00')")
        con.execute(
            "CREATE TABLE dim_week (season INTEGER, week INTEGER, season_type VARCHAR, "
            "asof_weekly_utc TIMESTAMP)"
        )
        for w in (1, 2, 3):
            con.execute(
                "INSERT INTO dim_week VALUES (2026, ?, 'REG', ?)",
                [w, datetime(2026, 9, 15, 14) + timedelta(days=7 * (w - 1))],
            )
    finally:
        con.close()


@dataclass
class Synthetic:
    root: Path
    inputs: Inputs
    csv_rows: list[dict[str, str]]


def build(root: Path, *, live_weeks: tuple[int, ...] = (1,), now: datetime = NOW) -> Synthetic:
    """Every input under ``root``; the store holds the backtest and the given live weeks."""
    root.mkdir(parents=True, exist_ok=True)
    store = root / "predictions.duckdb"
    write_backtest(store)
    for w in live_weeks:
        write_live_week(store, w)
    write_dataset(root / "dataset.parquet")
    rows = write_evaluation(root / "evaluation.csv")
    write_warehouse(root / "warehouse.duckdb")
    inputs = Inputs(
        warehouse=root / "warehouse.duckdb",
        store=store,
        dataset=root / "dataset.parquet",
        evaluation_csv=root / "evaluation.csv",
        season=SEASON,
        now=now,
    )
    return Synthetic(root, inputs, rows)


def with_store(syn: Synthetic, store: Path) -> Inputs:
    """The same inputs with another predictions store."""
    from dataclasses import replace

    return replace(syn.inputs, store=store)
