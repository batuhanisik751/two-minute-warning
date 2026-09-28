"""The weekly Waiver Radar list (C6): the freshness check, the live/backtest rule, the production
model (walk-forward fold, persistence), the reasons generator, the confidence band and tiers,
the point-in-time scoring function (leakage harness), the store's weekly writes, determinism and
the CLI (`twm radar score`, `twm radar week`). Offline and synthetic; every id and name is made
up.

The world: the C3 feature tests' 2025 warehouse (tests/test_waiver_radar_features.py), plus an
injury report for every team that played each week and ffopportunity rows for every game except
BUF@MIA of week 3, so week 2 is complete and week 3 is not. The model: the production fold for
2025 trained on the synthetic C4 dataset of 2021-2024 (tests/radar_synthetic.py); its backtest
(test seasons 2022-2024) gives the confidence bins.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np
import polars as pl
import pytest
from typer.testing import CliRunner

from tests.conftest import (
    DAILY_DC_DTYPES,
    INJURY_DTYPES,
    RawCache,
    frame,
)
from tests.radar_synthetic import synthetic_dataset
from tests.test_waiver_radar_features import (
    PLAY_DTYPES,
    PLAYERS,
    ROSTER_DTYPES,
    STATS_DTYPES,
    TEAM_DTYPES,
    TEAM_OF,
    _opp,
    _players,
    depth_2025,
    games_2025,
    gid,
    injuries_2025,
    plays_2025,
    world_rows,
)
from twm import ids
from twm import predictions as pr
from twm import registry as rg
from twm.asof import AsOfView, weekly_as_of
from twm.backtest.leakage import assert_future_invariant
from twm.backtest.walkforward import TestSeasonInTrainingError, fit_fold, production_fold
from twm.cli import app
from twm.modules.waiver_radar import backtest as bt
from twm.modules.waiver_radar import confidence as cf
from twm.modules.waiver_radar import evaluation as ev
from twm.modules.waiver_radar import production as prod
from twm.modules.waiver_radar import reasons as rs
from twm.modules.waiver_radar import weekly as wk
from twm.modules.waiver_radar.features import FEATURE_COLUMNS
from twm.modules.waiver_radar.pool import PoolRules, candidate_pool
from twm.sources import nflverse as nv
from twm.warehouse import build as wb

POOL_RULES = PoolRules(cutoffs={"QB": 1, "RB": 1, "WR": 1, "TE": 1})
SEASONS = (2021, 2022, 2023, 2024)  # the synthetic training seasons; the world is 2025
UNCOVERED = (3, "BUF", "MIA")  # the week-3 game without ffopportunity rows
T0 = datetime(2026, 9, 28, 12, 0)


# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


def _all_injuries(games: list[dict]) -> list[dict]:
    """The feature world's KC report, plus one Questionable row for every other team that
    played each week (a team with a game has a report)."""
    rows = list(injuries_2025())
    have = {(r["week"], r["team"]) for r in rows}
    for g in games:
        for team in (g["home_team"], g["away_team"]):
            if (g["week"], team) in have:
                continue
            n = next((n for n, t in TEAM_OF.items() if t == team), 1)  # KC: its QB
            rows.append({"season": 2025, "game_type": "REG", "team": team, "week": g["week"],
                         "gsis_id": gid(n), "position": PLAYERS[n][1],
                         "full_name": PLAYERS[n][0], "report_status": "Questionable"})  # fmt: skip
            have.add((g["week"], team))
    return rows


def _all_opportunity(games: list[dict], rows: list[dict]) -> list[dict]:
    """The feature world's rows plus one row per game (its home team's receiver), except the
    uncovered week-3 game."""
    out = list(rows)
    have = {r["game_id"] for r in rows}
    for g in games:
        if g["game_id"] in have or (g["week"], g["away_team"], g["home_team"]) == UNCOVERED:
            continue
        team = g["home_team"]
        n = next((n for n, t in TEAM_OF.items() if t == team and PLAYERS[n][1] == "WR"), 4)
        out.append({"season": "2025", "posteam": team, "week": float(g["week"]),
                    "game_id": g["game_id"], "player_id": gid(n), "full_name": PLAYERS[n][0],
                    "position": "WR", "receptions_exp": 2.0,
                    "rec_yards_gained_exp": 20.0})  # fmt: skip
    return out


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("weekly_world")
    with pytest.MonkeyPatch.context() as mp:
        root = tmp / "raw"
        mp.setattr(nv, "raw_dir", lambda: root)

        def _no_download(ds):
            raise AssertionError(f"never download ({ds.name})")

        mp.setattr(nv, "_loader", _no_download)
        mp.setattr(nv, "_configure_nflreadpy", lambda: None)
        mp.setattr(ids, "overrides_path", lambda: tmp / "manual" / ids.OVERRIDES_FILE)
        cache = RawCache(root)
        cache.write_globals()
        cache.write("players", None, _players())
        g25 = games_2025()
        rows = world_rows(g25)
        cache.write_season(
            2025, g25, player_stats=[], rosters=frame(rows["rosters"], ROSTER_DTYPES),
            snaps=rows["snaps"], injuries=frame(_all_injuries(g25), INJURY_DTYPES),
            depth_charts=frame(depth_2025(), DAILY_DC_DTYPES),
            opportunity=_all_opportunity(g25, rows["opportunity"]),
        )  # fmt: skip
        cache.write("player_stats", 2025, frame(rows["stats"], STATS_DTYPES))
        cache.write("pbp", 2025, frame(plays_2025(g25), PLAY_DTYPES))
        team_rows = [{"season": 2025, "week": g["week"], "team": t, "season_type": "REG",
                      "game_id": g["game_id"], "opponent_team": _opp(g, t), "carries": 20}
                     for g in g25 for t in (g["home_team"], g["away_team"])]  # fmt: skip
        cache.write("team_stats", 2025, frame(team_rows, TEAM_DTYPES))
        db = tmp / "weekly.duckdb"
        wb.build_warehouse([2025], db_path=db)
    return db


@pytest.fixture(scope="module")
def dataset() -> pl.DataFrame:
    return synthetic_dataset(SEASONS, weeks=5, per_pos=20, seed=11)


@pytest.fixture(scope="module")
def model(dataset) -> prod.ProductionModel:
    return prod.train_production(dataset, 2025)


@pytest.fixture(scope="module")
def store(dataset, tmp_path_factory) -> Path:
    """A store with the logit backtest of 2022-2024 (the confidence bins come from it)."""
    run = bt.run_backtest(dataset, models=("logit",), labels=("y_hit",),
                          test_seasons=(2022, 2023, 2024))  # fmt: skip
    preds, versions, outcomes = bt.store_frames(run, created_at=T0)
    path = tmp_path_factory.mktemp("weekly_store") / "predictions.duckdb"
    pr.write_predictions(path, predictions=preds, versions=versions, outcomes=outcomes)
    return path


@pytest.fixture(scope="module")
def conf(store) -> cf.Confidence:
    return cf.from_store(store, model="logit", label="y_hit", min_rows=200)


@pytest.fixture
def small_pool(monkeypatch):
    """The world has a handful of players per position: rank everyone (cutoffs of 1)."""
    real = wk.candidate_pool

    def pool(view, season, week, **kw):
        return real(view, season, week, rules=POOL_RULES)

    monkeypatch.setattr(wk, "candidate_pool", pool)
    return pool


def _utc(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)


# --------------------------------------------------------------------------------------
# Freshness
# --------------------------------------------------------------------------------------

AS_OF = _utc(2025, 9, 16, 14)
AFTER = _utc(2025, 9, 16, 15)


def _inputs(**drop: object) -> dict[str, pl.DataFrame]:
    """A complete synthetic week: two games (KC@PHI, DAL@SEA) and every table present."""
    games = pl.DataFrame(
        {"game_id": ["g1", "g2"], "away_team": ["KC", "DAL"], "home_team": ["PHI", "SEA"],
         "kickoff_utc": [datetime(2025, 9, 14, 17), datetime(2025, 9, 14, 20)],
         "has_result": [True, True],
         "available_at": [datetime(2025, 9, 15, 0), datetime(2025, 9, 15, 3)]}
    )  # fmt: skip
    per_game = pl.DataFrame({"game_id": ["g1", "g2"], "n": [10, 12]})
    per_team = pl.DataFrame({"team": ["DAL", "KC", "PHI", "SEA"], "n": [3, 4, 2, 5]})
    out = {"games": games, "stats": per_game, "snaps": per_game, "opportunity": per_game,
           "injuries": per_team, "rosters": per_team}  # fmt: skip
    for key, value in drop.items():
        if key == "result":
            out["games"] = games.with_columns(pl.Series("has_result", [True, False]))
        elif key == "split":
            out["games"] = games.with_columns(
                pl.Series("available_at", [datetime(2025, 9, 15), datetime(2025, 9, 18)])
            )
        elif key in ("stats", "snaps", "opportunity"):
            out[key] = per_game.filter(pl.col("game_id") != value)
        else:
            out[key] = per_team.filter(pl.col("team") != value)
    return out


def test_a_complete_week_is_ready():
    fr = wk.check_freshness(_inputs(), 2025, 2, AS_OF, AFTER)
    assert fr.ok and fr.problems == [] and (fr.n_games, fr.n_played) == (2, 2)


@pytest.mark.parametrize(
    ("drop", "expected"),
    [
        ({"result": True}, "1 of 2 games have no final score in the data yet: DAL@SEA"),
        ({"stats": "g1"}, "1 played game(s) have no player stats yet: KC@PHI"),
        ({"snaps": "g2"}, "1 played game(s) have no snap counts yet: DAL@SEA"),
        ({"opportunity": "g1"}, "have no ffopportunity rows (expected fantasy points) yet: KC@PHI"),
        ({"injuries": "SEA"}, "1 team(s) that played have no week-2 injury report yet: SEA"),
        ({"rosters": "KC"}, "1 team(s) that played have no week-2 weekly roster yet: KC"),
    ],
)
def test_each_missing_piece_refuses_with_its_message(drop, expected):
    fr = wk.check_freshness(_inputs(**drop), 2025, 2, AS_OF, AFTER)
    assert not fr.ok
    assert len(fr.problems) == 1 and expected in fr.problems[0], fr.problems
    msg = fr.message()
    assert "is not ready to score" in msg and "--allow-incomplete" in msg and expected in msg


def test_an_unplayed_game_needs_no_stats_and_a_split_game_is_only_a_note():
    fr = wk.check_freshness(_inputs(result=True), 2025, 2, AS_OF, AFTER)
    assert not any("player stats" in p for p in fr.problems)  # DAL@SEA has no result yet
    split = wk.check_freshness(_inputs(split=True), 2025, 2, AS_OF, AFTER)
    assert split.ok and "moved past the as-of" in split.notes[0]


def test_before_the_as_of_is_not_ready():
    fr = wk.check_freshness(_inputs(), 2025, 2, AS_OF, AS_OF - timedelta(minutes=1))
    assert not fr.ok and "Tuesday's as-of has not come yet" in fr.problems[0]


def test_freshness_on_the_world(world):
    now = _utc(2025, 12, 1)
    two = wk.check_freshness(
        wk.freshness_inputs(world, 2025, 2), 2025, 2, weekly_as_of(world, 2025, 2), now
    )
    assert two.ok, two.problems
    three = wk.check_freshness(
        wk.freshness_inputs(world, 2025, 3), 2025, 3, weekly_as_of(world, 2025, 3), now
    )
    assert three.problems == [
        "1 played game(s) have no ffopportunity rows (expected fantasy points) yet: BUF@MIA"
    ]
    assert wk.last_complete_week(world, 2025, now, before=3) == 2
    # week 4's SEA@DAL was moved past the as-of: a note, not a problem
    four = wk.check_freshness(
        wk.freshness_inputs(world, 2025, 4), 2025, 4, weekly_as_of(world, 2025, 4), now
    )
    assert four.ok and "SEA@DAL" in four.notes[0]


# --------------------------------------------------------------------------------------
# Live or backtest, default week
# --------------------------------------------------------------------------------------

KICKOFF = _utc(2025, 9, 18, 0, 15)


@pytest.mark.parametrize(
    ("now", "kind"),
    [
        (AS_OF - timedelta(seconds=1), "backtest"),  # early: before the official as-of
        (AS_OF, "live"),
        (_utc(2025, 9, 17, 9), "live"),
        (KICKOFF - timedelta(seconds=1), "live"),
        (KICKOFF, "backtest"),  # the next week has started: reconstructed
        (_utc(2026, 9, 28), "backtest"),
    ],
)
def test_live_only_between_the_as_of_and_the_next_kickoff(now, kind):
    assert wk.run_kind(AS_OF, KICKOFF, now) == kind


def test_without_a_next_kickoff_a_run_is_never_live():
    assert wk.run_kind(AS_OF, None, AFTER) == "backtest"


def test_default_week_is_the_latest_as_of_passed(world):
    assert wk.default_week(world, 2025, _utc(2025, 9, 24)) == 3
    assert wk.default_week(world, 2025, _utc(2025, 9, 23, 13, 59)) == 2
    with pytest.raises(LookupError, match="no regular-season week"):
        wk.default_week(world, 2025, _utc(2025, 9, 1))


# --------------------------------------------------------------------------------------
# The production model
# --------------------------------------------------------------------------------------


def test_the_production_fold_trains_only_on_earlier_seasons(dataset, model):
    assert model.fold.train_seasons == SEASONS and model.fold.val_season == 2024
    assert model.fold.tune_seasons == (2021, 2022, 2023)
    assert production_fold([*SEASONS, 2025, 2026], 2025) == model.fold
    # rows of the season it scores (or later) never reach it: add scrambled 2025/2026 rows
    extra = dataset.filter(pl.col("season") == 2024).with_columns(
        pl.lit(2025).cast(pl.Int32).alias("season"), ~pl.col("y_hit"),
        (pl.col("snap_share_last") * -3).alias("snap_share_last"),
    )  # fmt: skip
    again = prod.train_production(pl.concat([dataset, extra]), 2025)
    assert again.model_version == model.model_version
    x = dataset.filter(pl.col("season") == 2024).head(50)
    assert np.array_equal(again.raw(x), model.raw(x))
    # and the harness refuses a training frame that holds them
    with pytest.raises(TestSeasonInTrainingError):
        fit_fold(
            pl.concat([dataset, extra]), model.fold, estimator=prod.estimator("logit"),
            features=FEATURE_COLUMNS, label="y_hit", module="waiver_radar",
            keys=("season", "week", "gsis_id", "position"),
            tune_metric=prod.tune_metric_for("y_hit"),
        )  # fmt: skip


def test_the_version_is_the_backtest_fingerprint(dataset, model):
    seasons, dhash = prod.training_identity(dataset, 2025)
    row = model.version_row
    assert seasons == SEASONS and row["dataset_hash"] == dhash and row["test_season"] == 2025
    assert json.loads(row["training_seasons"]) == list(SEASONS)
    assert pr.model_version(
        module="waiver_radar", model="logit", label="y_hit", features=FEATURE_COLUMNS,
        params=model.params, training_seasons=SEASONS, test_season=2025, dataset_hash=dhash,
    ) == model.model_version  # fmt: skip
    assert json.loads(row["notes"])["kind"] == "production"


def test_saving_and_loading_gives_bit_identical_scores(model, dataset, tmp_path):
    path = prod.save_model(model, tmp_path)
    assert path.name == f"{model.model_version}.joblib"
    back = prod.load_model(path)
    x = dataset.sort("season", "week", "gsis_id")
    raw0, p0 = model.predict(x)
    raw1, p1 = back.predict(x)
    assert np.array_equal(raw0, raw1) and np.array_equal(p0, p1)
    assert back.model_version == model.model_version
    assert np.array_equal(back.train_means, model.train_means)
    with pytest.raises(prod.ProductionModelError, match="not found"):
        prod.load_model(tmp_path / "nope.joblib")
    import joblib

    joblib.dump({"format": 999}, tmp_path / "old.joblib")
    with pytest.raises(prod.ProductionModelError, match="not a model file"):
        prod.load_model(tmp_path / "old.joblib")


def test_ensure_reuses_the_current_version_and_retrains_on_new_data(dataset, tmp_path):
    store = tmp_path / "p.duckdb"
    first = prod.ensure_production(dataset, 2025, store=store, root=tmp_path, created_at=T0)
    assert not first.reused and first.registered and first.path.exists()
    cur = prod.current_production(store, 2025)
    assert cur is not None and cur["model_version"] == first.model.model_version
    again = prod.ensure_production(dataset, 2025, store=store, root=tmp_path)
    assert again.reused and again.model.model_version == first.model.model_version
    # new training data: a new version, which becomes the current one
    changed = dataset.with_columns(
        pl.when(pl.col("season") == 2021).then(~pl.col("y_hit")).otherwise(pl.col("y_hit"))
        .alias("y_hit")
    )  # fmt: skip
    new = prod.ensure_production(changed, 2025, store=store, root=tmp_path,
                                 created_at=T0 + timedelta(hours=1))  # fmt: skip
    assert not new.reused and new.model.model_version != first.model.model_version
    assert prod.current_production(store, 2025)["model_version"] == new.model.model_version  # type: ignore[index]
    # back to the first data: its version is current again, without a second row
    back = prod.ensure_production(dataset, 2025, store=store, root=tmp_path,
                                  created_at=T0 + timedelta(hours=2))  # fmt: skip
    assert back.model.model_version == first.model.model_version and not back.registered
    assert prod.current_production(store, 2025)["model_version"] == first.model.model_version  # type: ignore[index]
    assert pr.read_table(store, "model_versions").height == 2


# --------------------------------------------------------------------------------------
# Reasons
# --------------------------------------------------------------------------------------


def test_contributions_add_up_to_the_log_odds(model, dataset):
    x = dataset.filter(pl.col("season") == 2024)
    parts = rs.contribution_parts(model, x, x.get_column("position").to_list())
    total = parts.baseline + parts.total.sum_horizontal().to_numpy()
    assert np.allclose(total, rs.log_odds(model, x), atol=1e-9, rtol=0)
    assert list(parts.total.columns) == list(FEATURE_COLUMNS)
    # the probability is the logistic of the log-odds (before calibration)
    assert np.allclose(1 / (1 + np.exp(-total)), model.raw(x), atol=1e-12)
    # the value part leaves out only the missing indicators; the peer part is centred per list
    assert parts.peer is not None
    for pos in ("QB", "WR"):
        sel = (x.get_column("position") == pos).to_numpy()
        assert np.allclose(parts.peer.filter(pl.Series(sel)).sum().row(0), 0.0, atol=1e-9)


def _row(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {c: None for c in FEATURE_COLUMNS}
    base.update(name="Test Player", team="KC", position="WR", teammates_out="[]",
                preseason_rank_pos="WR")  # fmt: skip
    base.update(kw)
    return base


def test_top_three_one_per_theme_largest_first():
    row = _row(snap_share_last=0.8, snap_share_avg3=0.7, target_share_avg3=0.25,
               ppg_to_date=11.0, age_at_asof=23.2, position="WR",
               team_games_remaining=15)  # fmt: skip
    contrib = {"snap_share_last": 0.9, "snap_share_avg3": 0.8, "target_share_avg3": 0.5,
               "position": 2.0, "team_games_remaining": 1.5, "ppg_to_date": 0.4,
               "age_at_asof": 0.1, "fpoe_avg3": -0.3}  # fmt: skip
    out = rs.explain_row(row, contrib, weeks=3)
    assert [r["feature"] for r in out] == ["snap_share_last", "target_share_avg3", "ppg_to_date"]
    assert [r["text"] for r in out] == [
        "Played 80% of his team's snaps last game",
        "Drew 25% of his team's targets over the last 3 games",
        "Averages 11.0 fantasy points per game this season",
    ]
    assert out[0]["contribution"] == 0.9 and out[0]["theme"] == "snaps"
    # only positive contributions; fewer than 3 when there are no more
    assert rs.explain_row(row, {"snap_share_last": 0.2, "ppg_to_date": -0.1}) == [
        {"feature": "snap_share_last", "theme": "snaps", "contribution": 0.2,
         "text": "Played 80% of his team's snaps last game"}
    ]  # fmt: skip


def test_value_and_peer_parts_must_push_up_too():
    row = _row(age_at_asof=30.4, snap_share_last=0.8, position="WR")
    contrib = {"age_at_asof": 0.5, "snap_share_last": 0.3}
    # age's push comes only from "age is known" (its missing indicator): not a reason
    out = rs.explain_row(row, contrib, value_part={"age_at_asof": -0.1, "snap_share_last": 0.3})
    assert [r["feature"] for r in out] == ["snap_share_last"]
    # better than the training average but not than his list: not why he ranks high
    out = rs.explain_row(row, contrib, peer_part={"age_at_asof": 0.2, "snap_share_last": -0.1})
    assert [r["feature"] for r in out] == ["age_at_asof"]


MATES = json.dumps([
    {"gsis_id": "00-1", "name": "Starter Back", "position": "RB", "rule": "roster_status",
     "rules": "roster_status+missed_last_game", "status": "RES", "report_status": None,
     "ahead": True, "last_week": 2, "target_share_avg3": 0.05, "carry_share_avg3": 0.55,
     "snap_share_avg3": 0.7},
    {"gsis_id": "00-2", "name": "Slot Receiver", "position": "WR", "rule": "injury_report",
     "rules": "injury_report", "status": "ACT", "report_status": "Out", "ahead": None,
     "last_week": 3, "target_share_avg3": 0.2, "carry_share_avg3": 0.0, "snap_share_avg3": 0.8},
])  # fmt: skip


def test_teammate_sentences_name_him_and_say_why():
    row = _row(position="RB", teammates_out=MATES, snap_share_last=0.78, snap_share_delta=0.37,
               top_teammate_out=True, same_pos_vacated_carry_share=0.55,
               vacated_target_share=0.25, teammate_same_pos_unavailable=1)  # fmt: skip
    assert rs.phrase("snap_share_delta", row) == (
        "Snap share rose from 41% to 78% in his last game after Starter Back went on a reserve "
        "list (such as injured reserve)"
    )
    assert rs.phrase("same_pos_vacated_carry_share", row) == (
        "Starter Back (RB) is on a reserve list (such as injured reserve): 55% of the team's "
        "carries are up for grabs at his position"
    )
    assert rs.phrase("vacated_target_share", row) == (
        "Slot Receiver was ruled out of the last game (and 1 more teammate is out): 25% of the "
        "team's targets are up for grabs"
    )
    assert rs.phrase("top_teammate_out", row) == (
        "Starter Back, who played more snaps than him, is on a reserve list (such as injured "
        "reserve)"
    )
    assert (
        rs.phrase("teammate_same_pos_unavailable", row) == "1 RB teammate(s) are out: Starter Back"
    )


@pytest.mark.parametrize(
    ("mate", "now", "after"),
    [
        ({"rule": "roster_status", "status": "CUT"}, "was released", "was released"),
        ({"rule": "roster_status", "status": "XYZ"}, "is listed XYZ on the roster",
         "left the active roster (XYZ)"),
        ({"rule": "left_team"}, "is no longer on the team's roster (released or traded)",
         "left the team's roster (released or traded)"),
        ({"rule": "injury_report", "report_status": "Doubtful"},
         "was listed as doubtful for the last game", "was listed as doubtful for the last game"),
        ({"rule": "missed_last_game"}, "missed the last game", "missed the last game"),
    ],
)  # fmt: skip
def test_why_a_teammate_is_out(mate, now, after):
    assert rs.teammate_why(mate) == now and rs.teammate_why(mate, after=True) == after


def test_sentences_that_would_not_be_true_are_skipped():
    row = _row(snap_share_last=0.40, snap_share_delta=-0.02, opp_fp_allowed_next3=0.97,
               fpoe_avg3=-1.2, team_epa_per_play=-0.05, depth_rank_change=0,
               depth_rank_now=2, depth_rank_prev=2, is_rookie=False,
               bye_in_next3=False)  # fmt: skip
    for f in ("snap_share_delta", "opp_fp_allowed_next3", "fpoe_avg3", "team_epa_per_play",
              "depth_rank_change", "is_rookie", "xfp_avg3", "vacated_target_share"):  # fmt: skip
        assert rs.phrase(f, row) is None, f
    assert rs.phrase("bye_in_next3", row) == "No bye week in the next 3 weeks"
    ok = _row(opp_fp_allowed_next3=1.18, depth_rank_change=2, depth_rank_now=1,
              depth_rank_prev=3, position="TE", is_rookie=True, preseason_pos_rank=14,
              preseason_rank_pos="WR")  # fmt: skip
    assert rs.phrase("opp_fp_allowed_next3", ok) == (
        "Soft schedule: his next opponents have allowed 1.18 times the average fantasy points "
        "to TEs"
    )
    assert (
        rs.phrase("depth_rank_change", ok) == "Moved up the depth chart from No. 3 to No. 1 at TE"
    )
    assert rs.phrase("is_rookie", ok).startswith("Is a rookie")  # type: ignore[union-attr]
    assert rs.phrase("preseason_pos_rank", ok) == "Was ranked No. 14 among WRs before the season"


def test_a_feature_without_a_sentence_falls_back(monkeypatch):
    import dataclasses

    plain = dataclasses.replace(rg.get("wopr_avg3"), reason_template=None)
    monkeypatch.setitem(rg.REGISTRY, "wopr_avg3", plain)
    assert rs.phrase("wopr_avg3", _row(wopr_avg3=0.456)) == "WOPR, last 3 games: 0.46"
    assert "wopr_avg3" in rs.fallback_features()
    assert rs.fallback_text("snap_share_last", 0.5) == "Snap share, last game: 50%"
    assert rs.fallback_text("top_teammate_out", True) == "A player ahead of him is out: yes"


def test_every_reason_template_renders_and_the_fallback_list_is_empty():
    for f in FEATURE_COLUMNS:
        e = rg.get(f)
        for t in (e.reason_template, e.reason_if_false):
            if t:
                t.format(player="P", value=0.5, prev=0.25, delta=0.25, weeks=3,
                         teammate="T", team="X", why="is out", pos="WR")  # fmt: skip
    assert rs.fallback_features() == []  # every feature that can be a reason has a sentence
    assert set(rs.THEMES) == set(FEATURE_COLUMNS)


# --------------------------------------------------------------------------------------
# The band and the tiers
# --------------------------------------------------------------------------------------


def test_similar_bins_by_hand():
    p = np.array([0.1] * 4 + [0.2] * 4 + [0.3] * 4 + [0.9])
    y = np.array([0, 0, 0, 1] + [0, 0, 1, 1] + [1, 1, 1, 0] + [1])
    b = cf.similar_bins(p, y, min_rows=4)
    # three groups of 4; the lone 0.9 is too small for a bin of its own and joins the last
    assert b.select("p_from", "p_to", "n", "hits").rows() == [
        (0.1, 0.1, 4, 1), (0.2, 0.2, 4, 2), (0.3, 0.9, 5, 4)]  # fmt: skip
    assert b.get_column("rate").to_list() == [0.25, 0.5, 0.8]
    # Wilson 90% for 2 of 4, by hand: z = 1.6449, centre 0.5, half-width
    z = 1.6448536269514722
    half = z * math.sqrt(0.25 / 4 + z * z / 64) / (1 + z * z / 4)
    assert b.row(1, named=True)["lo"] == pytest.approx(0.5 - half)
    assert b.row(1, named=True)["hi"] == pytest.approx(0.5 + half)
    # ties are never split: six predictions of 0.1 make one bin even with min_rows 4
    assert cf.similar_bins(
        np.array([0.1] * 6 + [0.5] * 4), np.array([0] * 6 + [1] * 4), 4
    ).get_column("n").to_list() == [6, 4]


def test_bins_never_go_down():
    p = np.array([0.1] * 4 + [0.2] * 4 + [0.3] * 4)
    y = np.array([1, 1, 0, 0] + [1, 0, 0, 0] + [1, 1, 1, 0])  # 50%, 25%, 75%
    b = cf.similar_bins(p, y, min_rows=4)
    assert b.select("p_from", "p_to", "n", "hits").rows() == [(0.1, 0.2, 8, 3), (0.3, 0.3, 4, 3)]
    assert b.get_column("rate").to_list() == [0.375, 0.75]


def test_band_lookup_tiers_and_text():
    bins = cf.similar_bins(np.array([0.1] * 4 + [0.3] * 4 + [0.6] * 4),
                           np.array([0, 0, 0, 1] + [0, 1, 1, 0] + [1, 1, 1, 0]), 4)  # fmt: skip
    conf = cf.Confidence(bins, "logit", "y_hit", (2014, 2025), 12)
    band = conf.band([0.0, 0.1, 0.29, 0.3, 0.55, 0.6, 0.99])
    assert band.get_column("chance").to_list() == [0.25, 0.25, 0.25, 0.5, 0.5, 0.75, 0.75]
    assert conf.cutoffs == {"must-add": 0.3, "speculative": 0.1}
    assert [cf.tier_of(r) for r in (0.75, 0.5, 0.4999, 0.25, 0.2)] == [
        "must-add", "must-add", "speculative", "speculative", "watch"]  # fmt: skip
    assert cf.band_text(0.52, 0.45, 0.55) == "52% (similar players hit 45-55%)"
    assert cf.band_text(0.021, 0.019, 0.024) == "2% (similar players hit 1.9-2.4%)"
    js = json.loads(cf.band_json(band.row(3, named=True)))
    assert js["chance"] == 0.5 and js["n"] == 4 and js["hits"] == 2 and js["level"] == 0.9


def test_tier_table_counts_on_the_backtest_top_25():
    # bins hitting 0% (from 0.1), 25% (from 0.3) and 75% (from 0.6)
    bins = cf.similar_bins(np.array([0.1] * 4 + [0.3] * 4 + [0.6] * 4),
                           np.array([0, 0, 0, 0] + [0, 1, 0, 0] + [1, 1, 1, 0]), 4)  # fmt: skip
    conf = cf.Confidence(bins, "logit", "y_hit", (2014, 2025), 12)
    rows = pl.DataFrame({
        "season": [2020] * 6, "week": [1, 1, 1, 2, 2, 2], "position": ["WR"] * 6,
        "score": [0.7, 0.35, 0.05, 0.65, 0.2, 0.1], "rank": [1, 2, 26, 1, 2, 3],
        "y": [True, False, True, False, True, False]})  # fmt: skip
    t = cf.tier_table(conf, rows)
    got = {r["tier"]: (r["rows"], r["hits"], r["lists"]) for r in t.iter_rows(named=True)}
    # rank 26 has no tier; 0.7 and 0.65 are must-adds (1 hit), 0.35 speculative, 0.2/0.1 watch
    assert conf.cutoffs == {"must-add": 0.6, "speculative": 0.3}
    assert got == {"must-add": (2, 1, 2), "speculative": (1, 0, 2), "watch": (2, 1, 2)}
    assert t.filter(pl.col("tier") == "must-add").get_column("per_list").to_list() == [1.0]


def test_confidence_from_the_backtest_store(store, conf):
    rows = cf.backtest_rows(store, model="logit", label="y_hit")
    assert conf.n_predictions == rows.height and conf.seasons == (2022, 2024)
    assert int(conf.bins.get_column("n").sum()) == rows.height
    assert conf.bins.get_column("rate").is_sorted()
    assert int(conf.tiers.get_column("rows").sum()) == rows.filter(pl.col("rank") <= 25).height
    wt = conf.week_tiers(2)
    assert (
        int(wt.get_column("rows").sum())
        == rows.filter((pl.col("rank") <= 25) & (pl.col("week") == 2)).height
    )


def test_position_notes_come_from_the_evaluation(tmp_path):
    csv = tmp_path / "evaluation.csv"
    pl.DataFrame({
        "label": ["y_hit"] * 6, "subset": ["all"] * 6,
        "model": ["logit", "logit", "baseline_last_points", "logit", "logit",
                  "baseline_last_points"],
        "scope": ["position_diff", "position", "position", "position_diff", "position",
                  "position"],
        "seasons": ["2014-2025"] * 6,
        "key": ["QB vs baseline_last_points", "QB", "QB", "RB vs baseline_last_points", "RB",
                "RB"],
        "metric": ["p_at_10_diff", "p_at_10", "p_at_10", "p_at_10_diff", "p_at_10", "p_at_10"],
        "value": [0.003, 0.434, 0.431, 0.079, 0.542, 0.463],
        "lo": [-0.006, None, None, 0.05, None, None], "hi": [0.013, None, None, 0.1, None, None],
    }).write_csv(csv)  # fmt: skip
    notes = cf.position_notes(csv, label="y_hit", model="logit")
    assert list(notes) == ["QB"]
    assert notes["QB"] == (
        "Note for QB: in the 2014-2025 backtest the Radar's top 10 at QB hit 43.4% of the time "
        "and a list of last week's top scorers 43.1% (difference +0.3 points, 95% interval "
        "-0.6 to +1.3): at QB this list is no better than last week's points."
    )
    assert cf.position_notes(tmp_path / "missing.csv", label="y_hit", model="logit") == {}


# --------------------------------------------------------------------------------------
# Scoring: point in time, ranks, determinism
# --------------------------------------------------------------------------------------


def _scorer(model, conf, week: int):
    def build(view: AsOfView) -> pl.DataFrame:
        return wk.score_week(view, 2025, week, model, conf)

    return build


@pytest.mark.parametrize("week", [2, 3])
def test_scoring_with_reasons_passes_the_leakage_harness(world, model, conf, small_pool, week):
    out = assert_future_invariant(
        _scorer(model, conf, week), world, weekly_as_of(world, 2025, week), key=["gsis_id"]
    )
    assert out.height > 0 and set(out.columns) == set(wk.SCORED_SCHEMA)


def test_scored_lists_are_ranked_banded_tiered_and_explained(world, model, conf, small_pool):
    with AsOfView(world, weekly_as_of(world, 2025, 3)) as v:
        s = wk.score_week(v, 2025, 3, model, conf, top=2)
        pool = candidate_pool(v, 2025, 3, rules=POOL_RULES).filter(pl.col("in_pool"))
    assert sorted(s.get_column("gsis_id").to_list()) == sorted(pool.get_column("gsis_id").to_list())
    for (pos,), g in s.group_by("position"):
        g = g.sort("rank")
        assert g.get_column("rank").to_list() == list(range(1, g.height + 1))
        assert g.get_column("score").is_sorted(descending=True)
        tiers = g.get_column("tier").to_list()
        assert all(t is not None for t in tiers[:2]) and all(t is None for t in tiers[2:]), pos
    band = conf.band(s.get_column("score").to_numpy())
    assert s.get_column("chance").to_list() == band.get_column("chance").to_list()
    for r in s.iter_rows(named=True):
        reasons = json.loads(r["reasons_json"])
        assert len(reasons) <= 3
        assert all(x["contribution"] > 0 and x["text"] for x in reasons)
        assert len({x["theme"] for x in reasons}) == len(reasons)
        assert not {x["feature"] for x in reasons} & rs.NEVER_REASONS


def test_scoring_is_deterministic(world, model, conf, small_pool):
    as_of = weekly_as_of(world, 2025, 2)
    with AsOfView(world, as_of) as v:
        a = wk.score_week(v, 2025, 2, model, conf)
    with AsOfView(world, as_of) as v:
        b = wk.score_week(v, 2025, 2, model, conf)
    assert a.equals(b)


# --------------------------------------------------------------------------------------
# The store
# --------------------------------------------------------------------------------------


def _week_frames(version: str, week: int, kind: str, score: float) -> tuple:
    preds = pl.DataFrame({
        "module": "waiver_radar", "entity_type": "player", "entity_id": ["00-1", "00-2"],
        "season": 2026, "week": week, "as_of": datetime(2026, 9, 8 + 7 * week, 14),
        "horizon": 3, "rank_group": "WR", "score": score, "raw_score": score, "rank": [1, 2],
        "band": "{}", "model_version": version, "reasons_json": "[]", "kind": kind,
        "created_at": T0, "tier": ["must-add", None], "incomplete": False})  # fmt: skip
    versions = pl.DataFrame({
        "model_version": [version], "module": "waiver_radar", "model": "logit",
        "label": "y_hit", "feature_list": "[]", "params": "{}", "training_seasons": "[2025]",
        "test_season": 2026, "dataset_hash": "h", "code_version": "t", "notes": "{}",
        "created_at": T0})  # fmt: skip
    return preds, versions


def test_weekly_writes_replace_only_their_week_and_keep_live_weeks(tmp_path):
    path = tmp_path / "p.duckdb"
    for week in (1, 2):
        p, v = _week_frames("logit-x", week, "backtest", 0.3)
        pr.write_predictions(path, predictions=p, versions=v, replace="weeks")
    p, v = _week_frames("logit-x", 2, "live", 0.4)
    pr.write_predictions(path, predictions=p, versions=v, replace="weeks")
    got = pr.read_table(path, "predictions")
    assert got.height == 4
    assert got.filter(pl.col("week") == 1).get_column("kind").unique().to_list() == ["backtest"]
    assert got.filter(pl.col("week") == 2).get_column("score").unique().to_list() == [0.4]
    assert pr.read_table(path, "model_versions").height == 1
    # a reconstructed run never overwrites the live week
    p, v = _week_frames("logit-x", 2, "backtest", 0.9)
    with pytest.raises(pr.LiveWeekError, match="already has a live list"):
        pr.write_predictions(path, predictions=p, versions=v, replace="weeks")
    assert pr.read_table(path, "predictions").filter(pl.col("week") == 2).get_column(
        "kind"
    ).to_list() == ["live", "live"]
    with pytest.raises(ValueError, match="tier must be"):
        pr.write_predictions(
            path, predictions=p.with_columns(pl.lit("maybe").alias("tier")), versions=v
        )
    with pytest.raises(ValueError, match="replace must be"):
        pr.write_predictions(path, predictions=p, versions=v, replace="all")


def test_an_older_store_gains_the_new_columns(tmp_path):
    path = tmp_path / "old.duckdb"
    con = duckdb.connect(str(path))
    old = {c: t for c, t in pr.PREDICTION_COLUMNS.items() if c not in pr.PREDICTION_DEFAULTS}
    con.execute("CREATE TABLE predictions (" + ", ".join(f"{c} {t}" for c, t in old.items()) + ")")
    con.close()
    pr.connect(path).close()
    con = duckdb.connect(str(path), read_only=True)
    cols = [r[0] for r in con.execute("DESCRIBE predictions").fetchall()]
    con.close()
    assert cols == list(pr.PREDICTION_COLUMNS)


def test_the_evaluation_ignores_the_live_season(store, dataset, tmp_path):
    """A production version of a season outside the evaluation seasons (outcomes pending) does
    not reach the evaluation."""
    copy = tmp_path / "p.duckdb"
    copy.write_bytes(Path(store).read_bytes())
    p, v = _week_frames("logit-live", 2, "live", 0.4)
    pr.write_predictions(copy, predictions=p, versions=v, replace="weeks")
    before = ev.load_predictions(store, "y_hit")
    after = ev.load_predictions(copy, "y_hit")
    assert after.equals(before)
    assert ev.load_predictions(copy, "y_hit", seasons=(2023, 2024)).get_column(
        "season"
    ).unique().sort().to_list() == [2023, 2024]


# --------------------------------------------------------------------------------------
# A whole run, the report and the CLI
# --------------------------------------------------------------------------------------


def _run(world, model, conf, week=2, **kw):
    return wk.run_week(world, 2025, week, model=model, conf=conf, **kw)


def test_run_week_refuses_incomplete_data_unless_allowed(world, model, conf, small_pool):
    now = _utc(2025, 12, 1)
    with pytest.raises(wk.NotReadyError, match="BUF@MIA"):
        _run(world, model, conf, week=3, now=now)
    run = _run(world, model, conf, week=3, now=now, allow_incomplete=True)
    assert run.incomplete and run.kind == "backtest"
    preds, _, outcomes = wk.store_frames(run, created_at=T0)
    assert preds.get_column("incomplete").unique().to_list() == [True]
    assert outcomes.height == preds.height
    text = wk.build_report(run, generated="Generated at X.", command="c")
    assert "**Warning: incomplete data.**" in text and "BUF@MIA" in text
    with pytest.raises(ValueError, match="last regular-season week"):
        _run(world, model, conf, week=6, now=now)


def test_run_week_live_and_the_report(world, model, conf, small_pool):
    as_of = weekly_as_of(world, 2025, 2)
    run = _run(world, model, conf, now=as_of + timedelta(hours=1), notes={"QB": "QB note."})
    assert run.kind == "live" and not run.incomplete
    preds, versions, outcomes = wk.store_frames(run, created_at=T0)
    assert preds.get_column("kind").unique().to_list() == ["live"]
    assert versions.get_column("model_version").to_list() == [model.model_version]
    assert set(outcomes.get_column("label_status").to_list()) <= {"final", "pending"}
    text = wk.build_report(run, generated="Generated at X.", command="c")
    assert "**Live list.**" in text and "**QB note.**" in text
    assert text == wk.build_report(run, generated="Generated at X.", command="c")
    for pos in ("QB", "RB", "WR", "TE"):
        assert f"## {pos}" in text
    assert model.model_version in text and "must-add" in text


def _cli(world, dataset_path, store_path, tmp, week, now, *extra):
    return CliRunner().invoke(
        app,
        ["radar", "score", "--season", "2025", "--week", str(week), "--db", str(world),
         "--store", str(store_path), "--dataset", str(dataset_path), "--models-dir",
         str(tmp / "models"), "--evaluation", str(tmp / "none.csv"), "--out",
         str(tmp / f"W{week}.md"), "--now", now, *extra],
    )  # fmt: skip


def test_cli_score_and_week(world, dataset, store, model, conf, small_pool, tmp_path):
    ds_path = tmp_path / "dataset.parquet"
    dataset.write_parquet(ds_path)
    store_path = tmp_path / "p.duckdb"
    store_path.write_bytes(Path(store).read_bytes())
    # a pretend clock inside week 2's live window reproduces a run but never makes it live
    ok = _cli(world, ds_path, store_path, tmp_path, 2, "2025-09-16T15:00")
    assert ok.exit_code == 0, ok.output
    assert "stored as 'backtest'" in ok.output and "trained now" in ok.output
    report = (tmp_path / "W2.md").read_text()
    assert report.startswith("# Waiver Radar: 2025 week 2\n\nGenerated at 2025-09-16 15:00 UTC.")
    assert "**Reproduced list (stored as 'backtest').**" in report
    # a second run reuses the model and writes the same report (apart from Generated at)
    again = _cli(world, ds_path, store_path, tmp_path, 2, "2025-09-17T09:30")
    assert again.exit_code == 0 and "reused" in again.output
    second = (tmp_path / "W2.md").read_text()
    assert second.splitlines()[3:] == report.splitlines()[3:] and second != report
    stored = pr.read_table(store_path, "predictions", "season = 2025 AND week = 2")
    assert stored.get_column("kind").unique().to_list() == ["backtest"]
    assert stored.get_column("incomplete").unique().to_list() == [False]
    assert stored.get_column("reasons_json").str.contains('"text"').any()
    assert stored.get_column("model_version").unique().to_list() == [model.model_version]
    # the incomplete week is refused with the list of what is missing and exit code 3
    no = _cli(world, ds_path, store_path, tmp_path, 3, "2025-12-01T00:00")
    assert no.exit_code == wk.EXIT_NOT_READY
    assert "no ffopportunity rows" in no.output and "BUF@MIA" in no.output
    assert "The latest complete week is 2" in no.output
    assert not (tmp_path / "W3.md").exists()
    forced = _cli(world, ds_path, store_path, tmp_path, 3, "2025-12-01T00:00", "--allow-incomplete")
    assert forced.exit_code == 0 and "INCOMPLETE DATA" in forced.output
    assert "stored as 'backtest'" in forced.output
    w3 = (tmp_path / "W3.md").read_text()
    assert "--allow-incomplete" in w3 and "**Reconstructed list (stored as 'backtest').**" in w3
    three = pr.read_table(store_path, "predictions", "season = 2025 AND week = 3")
    assert three.get_column("incomplete").unique().to_list() == [True]
    # a live week-2 list (made in real time), then a reconstructed run cannot overwrite it
    live = _run(world, model, conf, now=weekly_as_of(world, 2025, 2) + timedelta(hours=1))
    assert live.kind == "live"
    wk.store_week(live, store_path, created_at=T0)
    late = _cli(world, ds_path, store_path, tmp_path, 2, "2025-12-01T00:00")
    assert late.exit_code == 1 and "already has a live list" in late.output
    # the stored week, with names and outcomes
    shown = CliRunner().invoke(app, ["radar", "week", "2025", "2", "--store", str(store_path),
                                     "--db", str(world), "--pos", "WR"])  # fmt: skip
    assert shown.exit_code == 0, shown.output
    assert "live list of" in shown.output and "Kc Wr Focus" in shown.output
    assert "WR" in shown.output and "QB  (" not in shown.output
    none = CliRunner().invoke(app, ["radar", "week", "2025", "5", "--store", str(store_path)])
    assert none.exit_code == 1 and "nothing stored" in none.output


def test_band_and_notes_only_from_seasons_before_the_list(tmp_path):
    assert cf.seasons_before(2026) == (2014, 2025)
    assert cf.seasons_before(2020) == (2014, 2019)
    with pytest.raises(ValueError, match="no backtest season before 2014"):
        cf.seasons_before(2014)
    csv = tmp_path / "evaluation.csv"
    pl.DataFrame({
        "label": ["y_hit"] * 3, "subset": ["all"] * 3,
        "model": ["logit", "logit", "baseline_last_points"],
        "scope": ["position_diff", "position", "position"], "seasons": ["2014-2025"] * 3,
        "key": ["QB vs baseline_last_points", "QB", "QB"],
        "metric": ["p_at_10_diff", "p_at_10", "p_at_10"], "value": [0.003, 0.434, 0.431],
        "lo": [-0.006, None, None], "hi": [0.013, None, None],
    }).write_csv(csv)  # fmt: skip
    assert list(cf.position_notes(csv, label="y_hit", model="logit", before=2026)) == ["QB"]
    assert cf.position_notes(csv, label="y_hit", model="logit", before=2025) == {}


def test_a_pretend_clock_is_never_live(world, model, conf, small_pool):
    now = weekly_as_of(world, 2025, 2) + timedelta(hours=1)
    assert _run(world, model, conf, now=now).kind == "live"
    assert _run(world, model, conf, now=now, real_clock=False).kind == "backtest"


def test_changed_fixed_settings_retrain(dataset, tmp_path):
    store = tmp_path / "p.duckdb"
    first = prod.ensure_production(dataset, 2025, store=store, root=tmp_path, created_at=T0)
    con = duckdb.connect(str(store))
    con.execute('UPDATE model_versions SET params = \'{"fixed": {"solver": "other"}}\'')
    con.close()
    again = prod.ensure_production(dataset, 2025, store=store, root=tmp_path)
    assert not again.reused and again.model.model_version == first.model.model_version


def test_cli_week_reads_a_walk_forward_week(store):
    out = CliRunner().invoke(app, ["radar", "week", "2023", "2", "--store", str(store),
                                   "--db", "/nonexistent.duckdb", "--limit", "3"])  # fmt: skip
    assert out.exit_code == 0, out.output
    assert "backtest list of logit-" in out.output and "model " in out.output
    assert "HIT" in out.output or "no hit" in out.output


# --------------------------------------------------------------------------------------
# Real data (opt-in: `uv run pytest -m realdata`; reads the real dataset and store, never
# writes them)
# --------------------------------------------------------------------------------------


def _real_inputs() -> tuple[pl.DataFrame, Path]:
    from twm.config import ROOT

    ds, store = ROOT / "data/waiver_radar/dataset.parquet", pr.default_path()
    if not ds.exists() or not store.exists():
        pytest.skip("real dataset or predictions store not present")
    return pl.read_parquet(ds), store


@pytest.mark.realdata
def test_real_production_fold_is_the_backtest_fold():
    """Trained for 2025 exactly as the production model is trained for 2026, the model has the
    stored backtest's 2025 version and reproduces its stored scores bit for bit."""
    ds, store = _real_inputs()
    pm = prod.train_production(ds, 2025)
    stored = pr.current_versions(store, "waiver_radar", "y_hit").filter(
        (pl.col("model") == "logit") & (pl.col("test_season") == 2025)
    )
    if stored.height == 0:
        pytest.skip("no stored logit backtest for 2025")
    assert pm.model_version == stored.row(0, named=True)["model_version"]
    assert pm.training_seasons == tuple(range(2013, 2025))
    preds = pr.read_table(store, "predictions", f"model_version = '{pm.model_version}'")
    rows = prod.model_rows(ds).filter(pl.col("season") == 2025).sort("season", "week", "gsis_id")
    raw, prob = pm.predict(rows)
    mine = rows.select("week", "gsis_id").with_columns(
        pl.Series("raw", raw), pl.Series("prob", prob)
    )
    both = mine.join(
        preds.select("week", pl.col("entity_id").alias("gsis_id"), "raw_score", "score"),
        on=["week", "gsis_id"],
    )
    assert both.height == rows.height == preds.height
    assert (both["raw"] == both["raw_score"]).all() and (both["prob"] == both["score"]).all()


@pytest.mark.realdata
def test_real_confidence_bins_and_tiers_cover_the_backtest():
    from twm.config import ROOT

    _, store = _real_inputs()
    conf = cf.from_store(store, model="logit", label="y_hit")
    ev_csv = pl.read_csv(ROOT / "reports/waiver_radar/evaluation.csv", infer_schema_length=0)
    base = ev_csv.filter(
        (pl.col("label") == "y_hit") & (pl.col("subset") == "all")
        & (pl.col("model") == "base_rate") & (pl.col("seasons") == "2014-2025")
        & (pl.col("metric") == "base_rate")
    ).row(0, named=True)  # fmt: skip
    assert conf.n_predictions == int(base["n_rows"])
    assert int(conf.bins.get_column("n").sum()) == conf.n_predictions
    assert conf.bins.get_column("rate").is_sorted() and conf.bins.get_column("n").min() >= 500
    assert int(conf.tiers.get_column("rows").sum()) == int(base["n_groups"]) * cf.TOP_N
    rates = dict(zip(conf.tiers["tier"], conf.tiers["rate"], strict=True))
    assert rates["must-add"] >= 0.5 > rates["speculative"] >= 0.25 > rates["watch"]


@pytest.mark.realdata
def test_real_weekly_scoring_passes_the_leakage_harness(real_full_db):
    """A 2025 list (daily depth charts), scored by the 2025 fold with its reasons, cannot see
    anything after its as-of."""
    ds, store = _real_inputs()
    path, _ = real_full_db
    pm = prod.train_production(ds, 2025)
    conf = cf.from_store(store, model="logit", label="y_hit")
    out = assert_future_invariant(
        lambda v: wk.score_week(v, 2025, 10, pm, conf), path, weekly_as_of(path, 2025, 10),
        key=["gsis_id"],
    )  # fmt: skip
    assert out.height > 300
    assert out.get_column("reasons_json").str.contains('"text"').mean() > 0.9  # type: ignore[operator]
