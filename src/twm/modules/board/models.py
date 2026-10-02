"""Board models (step I1b, PROJECT_SPEC 8.6): the estimators of the other modules, reused.

- ``logit``: L2 logistic regression, the Hot-Seat :class:`~twm.modules.hot_seat.models.InnerCvLogit`
  (the Radar's ``LogitEstimator``: median imputation + missing indicators, standardization; C
  chosen inside each fit by an inner walk-forward over the last 4 training seasons);
- ``lgbm``: the Radar's LightGBM on one thread (:class:`~twm.modules.streamer.models.
  StreamerLgbmEstimator`), tuned on the validation season by the shared harness;
- ``logit_simple``: the ``y_missed`` estimate (owner, 2026-10-02: "its own simple estimate"): the
  same L2 logit on a handful of inputs (:data:`MISSED_FEATURES`);
- ``base_ppg_rank``: the prior-season PPG rank baseline, a one-feature logit (same inner C)
  on ``pos_rank_s`` (an unranked breakout player, under 8 games, gets the training median rank
  plus the missing indicator);
- the preseason FantasyPros ECR baseline is a ranking, not a model (:mod:`.ecr`).

Missing inputs (NGS before 2016, RYOE before 2018, snap counts before 2013, own xFP before
2009, no combine row) are imputed inside each fit with a missing indicator (LightGBM: NaN).
"""

from __future__ import annotations

from twm.modules.hot_seat.models import InnerCvLogit, Spec
from twm.modules.streamer.models import StreamerLgbmEstimator

MODULE = "board"
KEYS = ("season", "gsis_id")
CLIFF_FEATURES: tuple[str, ...] = (
    "pos_rb", "pos_wr", "pos_te", "age", "age_curve_ratio", "prior_seasons", "games_s",
    "ppg_s", "pos_rank_s", "ppg_change", "touches_s", "touches_per_game_s", "career_touches",
    "career_targets", "yards_per_touch_s", "yards_per_touch_trend", "snap_pct_s",
    "snap_pct_trend", "ngs_separation_s", "ngs_separation_trend", "ngs_ryoe_per_att_s",
    "ngs_ryoe_trend", "xfp_per_game_s", "fpoe_per_game_s", "hc_departure",
)  # fmt: skip
MISSED_FEATURES: tuple[str, ...] = (
    "pos_rb", "pos_wr", "pos_te", "age", "prior_seasons", "games_s", "touches_per_game_s",
)  # fmt: skip
BREAKOUT_FEATURES: tuple[str, ...] = (
    "pos_te", "age", "third_season", "games_s", "ppg_s", "touches_per_game_s",
    "target_share_s", "target_share_rookie", "yards_per_team_pass_att_s", "air_yards_share_s",
    "rush_share_s", "xfp_per_game_s", "snap_pct_s", "drafted_round", "drafted_pick",
    "undrafted", "age_at_draft", "combine_forty", "combine_weight", "combine_height",
    "combine_vertical", "combine_broad_jump", "combine_speed_score", "team_any_a",
)  # fmt: skip
BREAKOUT_RB_FEATURES = tuple(f for f in BREAKOUT_FEATURES if f != "pos_te")
BASELINE_FEATURES = ("pos_rank_s",)


def specs(features: tuple[str, ...], label: str) -> dict[str, Spec]:
    """The models and the PPG-rank baseline of one population and label."""
    return {
        "logit": Spec("logit", lambda: InnerCvLogit("logit"), features, label, "model"),
        "lgbm": Spec("lgbm", StreamerLgbmEstimator, features, label, "model"),
        "base_ppg_rank": Spec(
            "base_ppg_rank", lambda: InnerCvLogit("base_ppg_rank"), BASELINE_FEATURES, label,
            "baseline",
        ),
    }  # fmt: skip


def missed_specs(extra: tuple[str, ...] = ()) -> dict[str, Spec]:
    """``y_missed``: the simple logit (+ ``extra``: the preseason features) and the PPG-rank
    baseline."""
    return {
        "logit_simple": Spec("logit_simple", lambda: InnerCvLogit("logit_simple"),
                             (*MISSED_FEATURES, *extra), "y_missed", "model"),
        "base_ppg_rank": Spec("base_ppg_rank", lambda: InnerCvLogit("base_ppg_rank"),
                              BASELINE_FEATURES, "y_missed", "baseline"),
    }  # fmt: skip
