"""Fantasy points and expected fantasy points PER PLAY (Regression Watch, step D1).

A weekly stat line tells you a receiver scored 18 points; it cannot tell you that 9 of them came
on two catches in the last five minutes of a 38-10 game. To split a player's points (and his
expected points) into garbage time and the rest, both are computed play by play here and then
added up per player-game (:mod:`twm.modules.regression_watch.player_week`).

**Fantasy points per play** (:func:`play_stats_sql`): every play of ``fact_play`` becomes one
stat line per player involved, with the same stat columns as ``fact_player_week``
(``passing_yards``, ``receptions``, ``fumbles_lost_total`` ...), so the one scoring engine
(:func:`twm.scoring.score_sql`, config/scoring.yaml) scores them; there is no second copy of the
weights. Who gets what:

- the **passer** (``passer_player_id``): ``passing_yards``, a passing touchdown when the pass
  scored (whoever carried it in), an interception;
- the **target** (``receiver_player_id``): a reception on a completion, ``receiving_yards``;
- the **rusher** (``rusher_player_id``, kneels and scrambles included): ``rushing_yards``;
- a **lateral**: the player who took it gets ``lateral_receiving_yards`` (after a catch) or
  ``lateral_rushing_yards`` (on a run);
- the **touchdown** goes to ``td_player_id``: a rushing touchdown when he is the rusher or the
  lateral rusher on a rushing touchdown, a receiving touchdown when he is the target or the
  lateral receiver on a passing touchdown, a return touchdown (``special_teams_tds``) when he
  returned a kickoff or punt, a fumble-recovery touchdown when he recovered a fumble, else a
  return touchdown on a kicking play; anything else (interception returns) scores nothing;
- a **two-point conversion** (``two_point_conv_result = 'success'``) counts for the passer, the
  target or the rusher of the try; two-point tries never add yards, catches or touchdowns;
- a **lost fumble** (``fumble_lost = 1``) goes to the player who lost it: the first fumbler
  unless a second fumbler exists and the first fumble was recovered by his own team (then the
  second). It is also classified (sack, rushing, receiving) for the ``scrimmage`` fumble option.

Summed over a game this reproduces ``fact_player_week``'s points for 99.94% of player-games
(docs/regression_watch.md lists every class of difference).

**Expected fantasy points per play** (:func:`play_expected_sql`): ffopportunity's per-play files
(``fact_opportunity_pass``, ``fact_opportunity_rush``) turned into the expected-stat columns of
``fact_opportunity_week`` (``receptions_exp``, ``rec_yards_gained_exp`` ...), so
:func:`twm.scoring.xfp_sql` scores them with the same weights. The rules were read off the data
(summing them per player-game reproduces the weekly file within rounding): a pass is worth
``pass_completion_exp`` expected catches (completions for the passer) and ``pass_completion_exp
x (air_yards + yards_after_catch_exp)`` expected yards, plus its ``pass_touchdown_exp`` and, for
the passer, ``pass_interception_exp``; a run ``rush_yards_exp`` and ``rush_touchdown_exp``; a
two-point try only its ``two_point_conv_exp``.

Both queries read ONE table each and keep the play's ``available_at``, so they run unchanged
through an :class:`~twm.asof.AsOfView` (point-in-time) or on the warehouse.
"""

from __future__ import annotations

from twm.scoring import FUMBLE_COLUMNS, STAT_COLUMNS, XFP_COLUMNS

# The stat columns of a play line: every fact_player_week column the scoring engine can read
# (both fumble options included), so score_sql() works on a play line as on a weekly line.
PLAY_STAT_COLUMNS: tuple[str, ...] = (
    "passing_yards", "passing_tds", "passing_interceptions", "passing_2pt_conversions",
    "rushing_yards", "rushing_tds", "rushing_2pt_conversions",
    "receptions", "receiving_yards", "receiving_tds", "receiving_2pt_conversions",
    "fumbles_lost_total", "sack_fumbles_lost", "rushing_fumbles_lost", "receiving_fumbles_lost",
    "special_teams_tds", "fumble_recovery_tds",
)  # fmt: skip
assert set(PLAY_STAT_COLUMNS) == {
    c for cols in (*STAT_COLUMNS.values(), *FUMBLE_COLUMNS.values()) for c in cols
}, "a scoring stat has no per-play attribution"

# The expected-stat columns of a play line: the fact_opportunity_week names xfp_sql() reads,
# plus the passer's expected completions (not scored, a component for the stability study).
PLAY_EXPECTED_COLUMNS: tuple[str, ...] = (*XFP_COLUMNS.values(), "pass_completions_exp")

# Kicking plays: a touchdown on one is a return touchdown (or a fumble recovery).
KICKING_PLAY_TYPES = ("kickoff", "punt", "field_goal", "extra_point")

# How a play's touchdown is credited (column td_kind of play_stats_sql).
TD_KINDS = ("rushing", "receiving", "special_teams", "fumble_recovery", "other")


def _any(col: str, values: tuple[str, ...]) -> str:
    """``col`` equals one of the (possibly NULL) columns ``values``."""
    options = ", ".join("COALESCE(" + v + ", '')" for v in values)
    return f"{col} IN ({options})"


def play_stats_sql(where: str = "TRUE") -> str:
    """One row per (play, player involved) of ``fact_play`` rows matching ``where`` (SQL over
    fact_play's columns, e.g. ``season = 2024 AND season_type = 'REG'``).

    Columns: game_id, play_id, gsis_id, season, week, season_type, posteam, play_type,
    is_garbage_time, available_at, td_kind (how the play's touchdown is credited, NULL without
    one) and :data:`PLAY_STAT_COLUMNS`. A player with two roles on one play (a passer who
    scores on his own fumble recovery) gets one row with both."""
    kicking = ", ".join(f"'{t}'" for t in KICKING_PLAY_TYPES)
    returners = (
        "kickoff_returner_player_id", "punt_returner_player_id",
        "lateral_kickoff_returner_player_id", "lateral_punt_returner_player_id",
    )  # fmt: skip
    rushers = ("rusher_player_id", "lateral_rusher_player_id")
    targets = ("receiver_player_id", "lateral_receiver_player_id")
    recoverers = ("fumble_recovery_1_player_id", "fumble_recovery_2_player_id")
    who = (
        "passer_player_id", *targets, *rushers, "td_player_id", "lost_by",
    )  # fmt: skip
    roles = "\n        UNION\n        ".join(
        f"SELECT game_id, play_id, {c} AS gsis_id FROM p WHERE {c} IS NOT NULL" for c in who
    )
    return f"""
    WITH p AS (
        SELECT *,
               COALESCE(two_point_attempt, 0) = 1 AS is_2pt,
               COALESCE(two_point_conv_result = 'success', FALSE) AS is_2pt_good,
               -- the player who lost the fumble: the first fumbler, unless a second one exists
               -- and the first fumble was recovered by his own team
               CASE WHEN fumble_lost = 1 AND fumbled_1_player_id IS NOT NULL
                         AND (fumbled_2_player_id IS NULL
                              OR fumble_recovery_1_team IS DISTINCT FROM fumbled_1_team)
                    THEN fumbled_1_player_id
                    WHEN fumble_lost = 1 AND fumbled_2_player_id IS NOT NULL
                    THEN fumbled_2_player_id END AS lost_by
        FROM fact_play WHERE {where}
    ),
    t AS (
        SELECT *,
               CASE WHEN td_player_id IS NULL OR is_2pt THEN NULL
                    WHEN rush_touchdown = 1 AND {_any("td_player_id", rushers)} THEN 'rushing'
                    WHEN pass_touchdown = 1 AND {_any("td_player_id", targets)} THEN 'receiving'
                    WHEN play_type IN ({kicking}) AND {_any("td_player_id", returners)}
                        THEN 'special_teams'
                    WHEN {_any("td_player_id", recoverers)} THEN 'fumble_recovery'
                    WHEN play_type IN ({kicking}) THEN 'special_teams'
                    ELSE 'other' END AS td_kind
        FROM p
    ),
    roles AS (
        {roles}
    )
    SELECT t.game_id, t.play_id, r.gsis_id, t.season, t.week, t.season_type, t.posteam,
           t.play_type, t.is_garbage_time, t.available_at, t.td_kind,
           CASE WHEN r.gsis_id = t.passer_player_id AND NOT t.is_2pt
                THEN COALESCE(t.passing_yards, 0) ELSE 0 END AS passing_yards,
           CASE WHEN r.gsis_id = t.passer_player_id AND NOT t.is_2pt AND t.pass_touchdown = 1
                THEN 1 ELSE 0 END AS passing_tds,
           CASE WHEN r.gsis_id = t.passer_player_id AND NOT t.is_2pt AND t.interception = 1
                THEN 1 ELSE 0 END AS passing_interceptions,
           CASE WHEN r.gsis_id = t.passer_player_id AND t.is_2pt_good
                THEN 1 ELSE 0 END AS passing_2pt_conversions,
           CASE WHEN r.gsis_id = t.rusher_player_id AND NOT t.is_2pt
                THEN COALESCE(t.rushing_yards, 0) ELSE 0 END
           + CASE WHEN r.gsis_id = t.lateral_rusher_player_id AND NOT t.is_2pt
                THEN COALESCE(t.lateral_rushing_yards, 0) ELSE 0 END AS rushing_yards,
           CASE WHEN r.gsis_id = t.td_player_id AND t.td_kind = 'rushing'
                THEN 1 ELSE 0 END AS rushing_tds,
           CASE WHEN r.gsis_id = t.rusher_player_id AND t.is_2pt_good
                THEN 1 ELSE 0 END AS rushing_2pt_conversions,
           CASE WHEN r.gsis_id = t.receiver_player_id AND NOT t.is_2pt AND t.complete_pass = 1
                THEN 1 ELSE 0 END AS receptions,
           CASE WHEN r.gsis_id = t.receiver_player_id AND NOT t.is_2pt
                THEN COALESCE(t.receiving_yards, 0) ELSE 0 END
           + CASE WHEN r.gsis_id = t.lateral_receiver_player_id AND NOT t.is_2pt
                THEN COALESCE(t.lateral_receiving_yards, 0) ELSE 0 END AS receiving_yards,
           CASE WHEN r.gsis_id = t.td_player_id AND t.td_kind = 'receiving'
                THEN 1 ELSE 0 END AS receiving_tds,
           CASE WHEN r.gsis_id = t.receiver_player_id AND t.is_2pt_good
                THEN 1 ELSE 0 END AS receiving_2pt_conversions,
           CASE WHEN r.gsis_id = t.lost_by THEN 1 ELSE 0 END AS fumbles_lost_total,
           CASE WHEN r.gsis_id = t.lost_by AND t.sack = 1 AND r.gsis_id = t.passer_player_id
                THEN 1 ELSE 0 END AS sack_fumbles_lost,
           CASE WHEN r.gsis_id = t.lost_by AND {_any("r.gsis_id", tuple(f"t.{c}" for c in rushers))}
                THEN 1 ELSE 0 END AS rushing_fumbles_lost,
           CASE WHEN r.gsis_id = t.lost_by AND {_any("r.gsis_id", tuple(f"t.{c}" for c in targets))}
                THEN 1 ELSE 0 END AS receiving_fumbles_lost,
           CASE WHEN r.gsis_id = t.td_player_id AND t.td_kind = 'special_teams'
                THEN 1 ELSE 0 END AS special_teams_tds,
           CASE WHEN r.gsis_id = t.td_player_id AND t.td_kind = 'fumble_recovery'
                THEN 1 ELSE 0 END AS fumble_recovery_tds
    FROM roles r JOIN t ON t.game_id = r.game_id AND t.play_id = r.play_id"""


def play_expected_sql(where: str = "TRUE") -> str:
    """One row per (play, player with an opportunity on it) of ``fact_opportunity_pass`` and
    ``fact_opportunity_rush`` rows matching ``where`` (SQL over their common columns: season,
    week, season_type, game_id, posteam).

    Columns: game_id, play_id, gsis_id, season, week, season_type, posteam, is_garbage_time
    (fact_play's, NULL when the play is not there), available_at, role ('passer', 'receiver',
    'rusher'), is_2pt, :data:`PLAY_EXPECTED_COLUMNS` (0 for the other roles), and for a target
    that was caught (two-point tries excluded) ``yac`` (receiving_yards - air_yards) and
    ``yac_exp`` (ffopportunity's expected yards after the catch)."""
    zero = dict.fromkeys(PLAY_EXPECTED_COLUMNS, "0.0")
    not2 = "COALESCE(two_point_attempt, 0) = 0"
    is2 = "COALESCE(two_point_attempt, 0) = 1"
    exp_yards = "pass_completion_exp * (air_yards + yards_after_catch_exp)"

    def when(cond: str, expr: str) -> str:
        return f"CASE WHEN {cond} THEN COALESCE({expr}, 0.0) ELSE 0.0 END"

    passer = {
        **zero,
        "pass_completions_exp": when(not2, "pass_completion_exp"),
        "pass_yards_gained_exp": when(not2, exp_yards),
        "pass_touchdown_exp": when(not2, "pass_touchdown_exp"),
        "pass_interception_exp": when(not2, "pass_interception_exp"),
        "pass_two_point_conv_exp": when(is2, "two_point_conv_exp"),
    }
    receiver = {
        **zero,
        "receptions_exp": when(not2, "pass_completion_exp"),
        "rec_yards_gained_exp": when(not2, exp_yards),
        "rec_touchdown_exp": when(not2, "pass_touchdown_exp"),
        "rec_two_point_conv_exp": when(is2, "two_point_conv_exp"),
    }
    rusher = {
        **zero,
        "rush_yards_gained_exp": when(not2, "rush_yards_exp"),
        "rush_touchdown_exp": when(not2, "rush_touchdown_exp"),
        "rush_two_point_conv_exp": when(is2, "two_point_conv_exp"),
    }
    caught = f"{not2} AND complete_pass = 1"

    def part(table: str, role: str, player: str, cols: dict[str, str], yac: bool) -> str:
        exp = ", ".join(f"{expr} AS {name}" for name, expr in cols.items())
        yac_cols = (
            f"CASE WHEN {caught} THEN CAST(receiving_yards - air_yards AS DOUBLE) END AS yac, "
            f"CASE WHEN {caught} THEN yards_after_catch_exp END AS yac_exp"
            if yac
            else "CAST(NULL AS DOUBLE) AS yac, CAST(NULL AS DOUBLE) AS yac_exp"
        )
        return (
            f"SELECT game_id, play_id, {player} AS gsis_id, season, week, season_type, posteam, "
            f"is_garbage_time, available_at, '{role}' AS role, {is2} AS is_2pt, {exp}, "
            f"{yac_cols} FROM {table} WHERE {player} IS NOT NULL AND ({where})"
        )

    return "\n    UNION ALL\n    ".join(
        [
            part("fact_opportunity_pass", "passer", "passer_player_id", passer, False),
            part("fact_opportunity_pass", "receiver", "receiver_player_id", receiver, True),
            part("fact_opportunity_rush", "rusher", "rusher_player_id", rusher, False),
        ]
    )
