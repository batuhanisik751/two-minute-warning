"""The owner's requirement (2026-09-28): the number of teams or lineup players must not matter.

End to end on the golden world (synthetic, tests/golden/): the same pipeline run with the
shipped 12-team config and with a 10-team config (a copy of config/ with `teams: 10`, loaded
through $TWM_CONFIG_DIR) gives different pool cutoffs, a bigger pool, fewer starter finishes,
and generated text (the pool and label reports, the glossary, the weekly list) that speaks of
a 10-team league and never of a 12-team one."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime

import polars as pl
import pytest
import yaml

from tests.golden import world
from twm import config, registry
from twm import predictions as pr
from twm.modules.waiver_radar import backtest as bt
from twm.modules.waiver_radar import confidence as cf
from twm.modules.waiver_radar import production as prod
from twm.modules.waiver_radar import weekly as wk
from twm.modules.waiver_radar.dataset import build_dataset
from twm.modules.waiver_radar.label_report import build_label_report
from twm.modules.waiver_radar.labels import LabelRules
from twm.modules.waiver_radar.pool import PoolRules
from twm.modules.waiver_radar.report import build_report

KEYS = ["season", "week", "gsis_id"]


@pytest.fixture(scope="module")
def golden_db(tmp_path_factory: pytest.TempPathFactory):
    tmp = tmp_path_factory.mktemp("golden_shape")
    with world.golden_env(tmp):
        yield world.build(tmp), tmp


def _run(db, tmp) -> dict:
    """Everything league-shaped, under whatever config is loaded now."""
    lg = config.league()
    ds = build_dataset(db, list(world.SEASONS))
    season, week = world.WEEKLY
    run = bt.run_backtest(ds, models=("logit",), labels=("y_hit",), test_seasons=(season - 1,))
    store = tmp / f"store-{lg.teams}.duckdb"
    preds, versions, outcomes = bt.store_frames(run, created_at=world.T0)
    pr.write_predictions(store, predictions=preds, versions=versions, outcomes=outcomes)
    conf = cf.from_store(store, model="logit", label="y_hit", seasons=(season - 1, season - 1),
                         min_rows=50)  # fmt: skip
    pm = prod.train_production(ds, season)
    weekly = wk.run_week(db, season, week, model=pm, conf=conf,
                         now=datetime(2026, 9, 28, tzinfo=UTC), real_clock=False)  # fmt: skip
    return {
        "pool_rules": PoolRules.from_config(),
        "label_rules": LabelRules.from_config(),
        "ds": ds,
        "texts": {
            "pool report": build_report(db, [season]).markdown,
            "label report": build_label_report(
                db,
                [season],
                examples=[(season, week)],
                first_eval_season=season,
                last_eval_season=season,
            ).markdown,
            "glossary": registry.glossary_markdown(),
            "weekly list": wk.build_report(weekly, generated="-", command="-"),
        },  # fmt: skip
    }


@pytest.fixture(scope="module")
def both(golden_db, tmp_path_factory):
    db, tmp = golden_db
    default = _run(db, tmp)
    cfg = tmp_path_factory.mktemp("cfg10")
    for name in config.CONFIG_FILES:
        shutil.copy(config.CONFIG_DIR / name, cfg / name)
    raw = yaml.safe_load((cfg / "league.yaml").read_text())
    (cfg / "league.yaml").write_text(yaml.safe_dump({**raw, "teams": 10}))
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(config.CONFIG_DIR_ENV, str(cfg))
        config.reload()
        try:
            ten = _run(db, tmp)
        finally:
            mp.delenv(config.CONFIG_DIR_ENV)
            config.reload()
    assert config.league().teams == 12  # restored
    return default, ten


def test_the_derived_numbers_follow_the_number_of_teams(both):
    default, ten = both
    assert dict(default["pool_rules"].cutoffs) == {"QB": 18, "RB": 36, "WR": 36, "TE": 18}
    assert dict(ten["pool_rules"].cutoffs) == {"QB": 15, "RB": 30, "WR": 30, "TE": 15}
    assert dict(default["label_rules"].starter_thresholds) == {"QB": 12, "RB": 24, "WR": 24,
                                                               "TE": 12}  # fmt: skip
    assert dict(ten["label_rules"].starter_thresholds) == {"QB": 10, "RB": 20, "WR": 20, "TE": 10}
    assert dict(ten["label_rules"].flex_ranks) == {"RB": 30, "WR": 30}


def test_a_smaller_league_has_a_bigger_pool_and_fewer_starter_finishes(both):
    default, ten = both
    d, t = default["ds"], ten["ds"]
    assert d.height == t.height  # the same rostered universe
    n12, n10 = int(d.get_column("in_pool").sum()), int(t.get_column("in_pool").sum())
    assert n10 > n12  # smaller cutoffs: more players count as available
    joined = d.select(*KEYS, "in_pool", "n_starter_finishes", "n_flex_finishes").join(
        t.select(*KEYS, "in_pool", "n_starter_finishes", "n_flex_finishes"), on=KEYS,
        suffix="_10",
    )  # fmt: skip
    # a player in the 12-team pool is in the 10-team pool too
    assert joined.filter(pl.col("in_pool") & ~pl.col("in_pool_10")).height == 0
    fewer = joined.filter(pl.col("n_starter_finishes_10") > pl.col("n_starter_finishes"))
    assert fewer.height == 0  # a stricter threshold never adds a starter finish
    assert (joined.get_column("n_starter_finishes_10").sum()
            < joined.get_column("n_starter_finishes").sum())  # fmt: skip
    assert (joined.get_column("n_flex_finishes_10").sum()
            < joined.get_column("n_flex_finishes").sum())  # fmt: skip
    pool_hits = t.filter(pl.col("in_pool")).get_column("y_hit").sum()
    assert pool_hits != d.filter(pl.col("in_pool")).get_column("y_hit").sum()


def test_generated_text_speaks_of_the_configured_league(both):
    default, ten = both
    for what, text in default["texts"].items():
        assert "12-team" in text, what
    for what, text in ten["texts"].items():
        assert "12-team" not in text, what
        assert "10-team" in text, what
    assert "N = QB 15, RB 30, WR 30, TE 15" in ten["texts"]["pool report"]
    assert "QB top 10, RB top 20, WR top 20, TE top 10" in ten["texts"]["label report"]
    assert "in the top 30 at his position" in ten["texts"]["label report"]
    weekly = ten["texts"]["weekly list"]
    assert "top 10 QBs, top 20 RBs, top 20 WRs or top 10 TEs" in weekly
    assert "outside the top 30 by both the preseason list" in weekly
    assert "RB/WR top 30" in ten["texts"]["glossary"]
