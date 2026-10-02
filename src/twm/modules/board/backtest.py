"""Board walk-forward backtest (step I1b, PROJECT_SPEC 8.6 "Models & evaluation").

Rows are keyed by the SNAPSHOT season S (features at the end of S, label from S+1). Test seasons
S = 2007 .. 2024, i.e. labels 2008-2025; each fold trains on every snapshot season from 2002 up
to S-1 (whose labels, from seasons up to S, are all known at S's snapshot), through the shared
harness :func:`twm.backtest.walkforward.walk_forward`, which refuses any row of the test season
or later. Variants (owner decision 2026-10-02, all three computed for the Cliff):

- ``cliff_main``: ``y_cliff`` on the Cliff rows with 6+ games in S+1;
- ``cliff_missed``: ``y_missed`` (fewer than 6 games in S+1), its own simple logit;
- ``cliff_sensitivity``: ``y_cliff_or_missed`` on every Cliff row;
- ``breakout_wr_te`` and ``breakout_rb``: ``y_breakout`` (RB reported apart, spec "optionally RB").

Every test row is scored by its fold's model; ``prob`` = the model's own probability (the
harness's isotonic calibration is not used: ranking metrics do not need it and the logit's own
probability is the one calibrated in the report).
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

import polars as pl

from twm.backtest.walkforward import WalkForwardResult, walk_forward
from twm.modules.board import models as bm
from twm.modules.board import post_draft as pdr
from twm.modules.board import preseason as pre
from twm.modules.hot_seat.models import InnerCvLogit, Spec, neg_log_loss

FIRST_TEST_SEASON = 2007
LAST_TEST_SEASON = 2024
TEST_SEASONS = tuple(range(FIRST_TEST_SEASON, LAST_TEST_SEASON + 1))
INFO = ("season", "gsis_id", "position", "team", "ppg", "pos_rank", "games_next", "ppg_next")
Progress = Callable[[str], None]


@dataclass(frozen=True)
class Variant:
    name: str
    population: str  # "cliff" or "breakout"
    label: str
    specs: dict[str, Spec]
    ecr_kind: str  # how the ECR baseline scores the rows: "fall" or "rise"
    title: str
    positions: tuple[str, ...] = ()  # breakout: the positions of the rows (empty: all)
    snapshot: str = "end_of_season"  # or "preseason" (I2a) / "post_draft" (I2b): + NEW_FEATURES

    @property
    def models(self) -> list[str]:
        return list(self.specs)


SNAPSHOTS = ("end_of_season", "preseason", "post_draft")
# the features each later snapshot adds to every model (the PPG-rank baseline excepted)
EXTRA_FEATURES: dict[str, tuple[str, ...]] = {
    "end_of_season": (), "preseason": pre.NEW_FEATURES, "post_draft": pdr.NEW_FEATURES,
}  # fmt: skip


def variants(snapshot: str = "end_of_season") -> dict[str, Variant]:
    """The five variants; at the ``preseason`` (step I2a) and ``post_draft`` (step I2b)
    snapshots every model (the PPG-rank baseline excepted) also gets that snapshot's
    :data:`EXTRA_FEATURES`."""
    if snapshot not in SNAPSHOTS:
        raise ValueError(f"unknown snapshot {snapshot!r}")
    x = EXTRA_FEATURES[snapshot]
    cliff = bm.specs((*bm.CLIFF_FEATURES, *x), "y_cliff")
    sens = bm.specs((*bm.CLIFF_FEATURES, *x), "y_cliff_or_missed")
    return {
        v.name: v
        for v in (
            Variant("cliff_main", "cliff", "y_cliff", cliff, "fall",
                    "Cliff (6+ games in S+1; main model)", snapshot=snapshot),
            Variant("cliff_missed", "cliff", "y_missed", bm.missed_specs(x), "fall",
                    "Missed most of S+1 (under 6 games)", snapshot=snapshot),
            Variant("cliff_sensitivity", "cliff", "y_cliff_or_missed", sens, "fall",
                    "Cliff counting a missed season as a cliff (sensitivity)", snapshot=snapshot),
            Variant("breakout_wr_te", "breakout", "y_breakout",
                    bm.specs((*bm.BREAKOUT_FEATURES, *x), "y_breakout"), "rise", "Breakout WR/TE",
                    ("WR", "TE"), snapshot),
            Variant("breakout_rb", "breakout", "y_breakout",
                    bm.specs((*bm.BREAKOUT_RB_FEATURES, *x), "y_breakout"), "rise", "Breakout RB",
                    ("RB",), snapshot),
        )
    }  # fmt: skip


def variant_rows(dataset: pl.DataFrame, v: Variant, last: int = LAST_TEST_SEASON) -> pl.DataFrame:
    """The variant's labelled rows of snapshots up to ``last``."""
    df = dataset.filter(pl.col(f"in_{v.population}"), pl.col("season") <= last)
    if v.positions:
        df = df.filter(pl.col("position").is_in(list(v.positions)))
    return df.filter(pl.col(v.label).is_not_null()).sort(list(bm.KEYS))


@dataclass
class VariantRun:
    variant: Variant
    scored: pl.DataFrame  # INFO + label "y" + one probability column per model ("p_<model>")
    results: dict[str, WalkForwardResult] = field(default_factory=dict)


def run_model(spec: Spec, rows: pl.DataFrame, test_seasons: Iterable[int]) -> WalkForwardResult:
    """Walk-forward of one model on ``rows`` (every season; the harness splits them)."""
    estimator = spec.make()
    if isinstance(estimator, InnerCvLogit):
        # fit reads the season of each training row from this frame (its inner walk-forward)
        estimator.bind(rows, spec.features, bm.KEYS)
    with warnings.catch_warnings():
        # early folds have no NGS / snap / xFP seasons: the imputer drops an all-missing column
        # for that fold (the model cannot use it there), as intended
        warnings.filterwarnings("ignore", message="Skipping features without any observed")
        return walk_forward(
            rows,
            estimator=estimator,
            features=spec.features,
            label=spec.label,
            module=bm.MODULE,
            test_seasons=test_seasons,
            keys=bm.KEYS,
            tune_metric=neg_log_loss(spec.label),
            cal_group="season",
        )


def run_variant(
    dataset: pl.DataFrame,
    v: Variant,
    test_seasons: Sequence[int] = TEST_SEASONS,
    progress: Progress | None = None,
) -> VariantRun:
    rows = variant_rows(dataset, v, max(test_seasons))
    scored = rows.filter(pl.col("season").is_in(list(test_seasons))).select(
        *INFO, pl.col(v.label).cast(pl.Int8).alias("y")
    )
    run = VariantRun(v, scored)
    for name, spec in v.specs.items():
        res = run_model(spec, rows, test_seasons)
        pred = res.predictions.select(*bm.KEYS, pl.col("raw_score").alias(f"p_{name}"))
        run.scored = run.scored.join(pred, on=list(bm.KEYS), how="left")
        run.results[name] = res
        if progress:
            progress(f"{v.name} {name}: {len(res.folds)} folds")
    run.scored = run.scored.sort(list(bm.KEYS))
    return run
