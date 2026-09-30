"""Plain-English reasons for the streamer's weekly list (S2a), from the registry's sentences.

- **K** (the logistic regression): the Radar's contributions and rules
  (:func:`twm.modules.waiver_radar.reasons.explain_row`: the value itself must push the chance
  up, from the training mean and from the average kicker of the list; no sentence for a missing
  value), then at most one reason per streamer theme (:data:`THEMES`), strongest first.
- **DST** (the next-opponent rule): the rule's own fact (the opponent's points per game).
- Both: when the pick has the same score as another pick of its list, the tie-breaker that
  ordered them is added (last game's points), so the list never hides why one ranks above the
  other. Every sentence is filled from the same point-in-time feature row that was ranked.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from twm.modules.waiver_radar import reasons as rs

N_REASONS = 3
NEVER_REASONS = frozenset({"kdst_games_to_date", "team_games_to_date", "next_opp_games_to_date"})
RULE_FEATURE = "next_opp_points_per_game"
TIE_FEATURE = "kdst_points_last"
THEMES: dict[str, str] = {
    "is_team_kicker": "role",
    **dict.fromkeys(("kdst_points_per_game", "kdst_ppg_rank", "kdst_points_last"), "scoring"),
    **dict.fromkeys(("kdst_preseason_rank", "weekly_ecr_rank", "weekly_ecr_listed"), "experts"),
    **dict.fromkeys(("k_fg_att_per_game", "k_pat_att_per_game", "k_fg_att_40_plus_per_game"),
                    "kicks"),
    **dict.fromkeys(("k_fg_pct_0_39", "k_fg_pct_40_49", "k_fg_pct_50_plus"), "accuracy"),
    **dict.fromkeys(("team_points_per_game", "team_rz_trips_per_game",
                     "team_rz_stalls_per_game", "team_rz_stall_rate"), "offense"),
    **dict.fromkeys(("next_opp_points_allowed_per_game", "next_opp_rz_trips_allowed_per_game",
                     "next_opp_rz_stall_rate_forced", "next_opp_points_per_game",
                     "next_opp_sacks_allowed_per_game", "next_opp_giveaways_per_game"),
                    "opponent"),
    **dict.fromkeys(("next_is_home", "next_venue_dome", "next_venue_retractable"), "venue"),
    **dict.fromkeys(("dst_sacks_per_game", "dst_takeaways_per_game", "dst_tds_per_game",
                     "dst_points_allowed_per_game"), "defense"),
}  # fmt: skip


def _tied(scored: pl.DataFrame) -> list[bool]:
    """Per row: another row of its list (season, week, position) has the same score."""
    n = pl.len().over("season", "week", "position", "score")
    return scored.select((n > 1).alias("t")).get_column("t").to_list()


def _tie_reason(row: dict[str, Any]) -> dict[str, Any] | None:
    text = rs.phrase(TIE_FEATURE, row)
    if text is None:
        return None
    return {"feature": TIE_FEATURE, "theme": "tie-break", "contribution": None,
            "text": f"{text} (orders it among picks with the same score)"}  # fmt: skip


def k_reasons(model: Any, scored: pl.DataFrame) -> list[list[dict[str, Any]]]:
    """Reasons for the K rows of one week (``scored``: feature rows + name, team, position,
    score)."""
    if scored.height == 0:
        return []
    parts = rs.contribution_parts(model, scored, scored.get_column("position").to_list())
    feats = [f for f in model.features if f not in NEVER_REASONS]
    assert parts.peer is not None
    out = []
    rows = zip(scored.iter_rows(named=True), parts.total.iter_rows(named=True),
               parts.value.iter_rows(named=True), parts.peer.iter_rows(named=True),
               _tied(scored), strict=True)  # fmt: skip
    for row, c, v, p, tied in rows:
        found = rs.explain_row(row, c, value_part=v, peer_part=p, n=len(feats), features=feats)
        picked, used = [], set()
        for r in found:
            theme = THEMES.get(r["feature"], r["feature"])
            if theme in used or len(picked) >= N_REASONS:
                continue
            used.add(theme)
            picked.append({**r, "theme": theme})
        tie = _tie_reason(row) if tied else None
        out.append(picked + ([tie] if tie and TIE_FEATURE not in {r["feature"] for r in picked}
                             else []))  # fmt: skip
    return out


def dst_reasons(scored: pl.DataFrame) -> list[list[dict[str, Any]]]:
    """Reasons for the D/ST rows of one week: the rule's fact, plus the tie-breaker if tied."""
    out = []
    for row, tied in zip(scored.iter_rows(named=True), _tied(scored), strict=True):
        text = rs.phrase(RULE_FEATURE, row)
        rule = (
            [
                {
                    "feature": RULE_FEATURE,
                    "theme": "rule",
                    "contribution": None,
                    "text": f"{text} (the rule ranks D/STs by this: fewer is better)",
                }
            ]
            if text
            else []
        )
        tie = _tie_reason(row) if tied else None
        out.append(rule + ([tie] if tie else []))  # fmt: skip
    return out
