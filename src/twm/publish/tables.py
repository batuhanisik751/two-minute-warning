"""The published tables as the Python writer sees them (step E2).

``web/db/schema.ts`` (Drizzle) owns the DDL; this module lists, per table, the columns the
publisher writes (in order, with their Postgres types, which COPY needs) and how a publish
treats the table's rows. A postgres-marked test migrates a fresh database and checks that
these lists equal the real columns, so the two files cannot drift apart silently.

How a publish treats each table (the reviewer's rules of 2026-09-28):

- **replace**: every row is deleted and written again in the publish transaction: the rows are
  derived from local files and can always be rebuilt (track_record, tier_stats, glossary,
  player_week_summary, site_meta, radar_outcome; step P2: stream_outcome, regression_outcome,
  stream_track_record, regression_track_record; step R1: regression_stability; step H4a:
  hot_seat_outcome, hot_seat_track_record, hot_seat_firings; step I2c-a: board_outcome,
  board_track_record, board_disagreement).
- **lists**: radar_list / radar_pick (and, step P2, stream_list / stream_pick and
  regression_list / regression_row; step H4a: hot_seat_list / hot_seat_row; step I2c-a:
  board_list / board_row, with the same
  code: :data:`FAMILIES`) hold two kinds of list. 'backtest' lists (reconstructed)
  are replaced like the tables above. 'live' lists (made in real time) are **frozen**: a live
  list is inserted only when its (season, week, position) is not in the target yet and is never
  deleted or changed by a normal publish (``--replace-live SEASON-Wnn`` is the owner's explicit
  way to replace one week). A fresh runner with an empty predictions store therefore never
  loses a live list.
- **upsert**: rows are inserted or updated by primary key, never deleted: frozen live lists may
  reference a model version or a player that the current run does not have (model_versions,
  dim_team, dim_player).
- **append**: one new row per run, never changed (pipeline_runs).
- **seasons** (step P3, the Decision Report Card: :data:`DECISIONS`): decision_fourth,
  decision_two_point, decision_clock, coach_season and coach_week hold two parts, split by the
  approved grading's season S. The **history** (seasons before S) comes from the frozen,
  pinned snapshot and is one hash unit (rewritten only when the snapshot changes); the
  **season in progress** (S) is regraded with the pinned models on every run and is another
  unit. The empty-replacement guard watches each part of each table.
- **snapshots** (feature #1, the Questionable list: :data:`QUESTIONABLE`): questionable_list /
  questionable_row hold the nightly snapshots keyed by (season, week, as_of). Append-only: a
  snapshot is inserted only when its key is not in the target yet and is never deleted or
  changed by a publish (a fresh runner with an empty store loses nothing). Its other tables
  (questionable_history, questionable_backtest, questionable_calibration, questionable_live)
  are replaced, each its own hash unit, guarded like the others. Feature #5, Teammate out
  (:data:`TEAMMATE_OUT`: teammate_out_list / teammate_out_row and its replaced tables), follows
  the same rules (:data:`SNAPSHOTS`); so does feature #6, the playoff planner
  (:data:`PLAYOFF_PLANNER`), whose snapshots are keyed by (season, through_week): one per
  completed week.
- Feature #10, coach tendencies (:data:`COACH_TENDENCIES`): four **replace** tables rebuilt
  from the warehouse on every publish, each its own hash unit, guarded like the others.
- Feature #8, lead time vs the crowd (:data:`LEAD_TIME`): six **replace** tables of
  aggregates only (FantasyPros' license), rebuilt on every publish the same way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Mode = Literal["replace", "lists", "upsert", "append", "seasons", "snapshots"]


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[tuple[str, str], ...]  # (column, Postgres type as COPY needs it)
    key: tuple[str, ...]
    mode: Mode

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(c for c, _ in self.columns)

    @property
    def types(self) -> tuple[str, ...]:
        return tuple(t for _, t in self.columns)


def _t(name: str, key: tuple[str, ...], mode: Mode, *cols: tuple[str, str]) -> Table:
    return Table(name, tuple(cols), key, mode)


TS = "timestamptz"
DP = "double precision"
# coach_season / coach_week: the G3 aggregates (twm.modules.decisions.coach), in this order
COACH_COUNTS = (
    ("fourth_graded", "integer"), ("fourth_toss_ups", "integer"), ("fourth_wp_lost", DP),
    ("fourth_wrong", "integer"), ("go_clear", "integer"), ("go_clear_went", "integer"),
    ("went", "integer"), ("two_point_graded", "integer"), ("two_point_toss_ups", "integer"),
    ("two_point_wp_lost", DP), ("two_point_wrong", "integer"), ("wp_lost", DP),
)  # fmt: skip
# Upserted columns that keep their first-published value when the row already exists: the
# scheduled job rebuilds the backtest every run, which stamps each model version with a new
# created_at; the version (its key) is the same model, so it was created when first published.
KEEP_ON_CONFLICT: dict[str, tuple[str, ...]] = {"model_versions": ("created_at",)}
TABLES: dict[str, Table] = {
    t.name: t
    for t in (
        _t("site_meta", ("key",), "replace", ("key", "text"), ("value", "text")),
        _t(
            "pipeline_runs", ("run_id",), "append",
            ("run_id", "text"), ("started_at", TS), ("finished_at", TS), ("status", "text"),
            ("stage", "text"), ("git_sha", "text"), ("data_as_of", TS), ("notes", "jsonb"),
        ),
        _t(
            "model_versions", ("model_version",), "upsert",
            ("model_version", "text"), ("module", "text"), ("model", "text"), ("label", "text"),
            ("training_seasons", "integer[]"), ("test_season", "integer"),
            ("feature_list", "text[]"), ("params", "jsonb"), ("created_at", TS),
        ),
        _t(
            "dim_team", ("team_abbr",), "upsert",
            ("team_abbr", "text"), ("team_name", "text"), ("team_nick", "text"),
            ("conference", "text"), ("division", "text"), ("color", "text"), ("color2", "text"),
        ),
        _t(
            "dim_player", ("gsis_id",), "upsert",
            ("gsis_id", "text"), ("display_name", "text"), ("position", "text"), ("team", "text"),
            ("draft_year", "integer"), ("draft_round", "integer"), ("draft_pick", "integer"),
            ("rookie_season", "integer"),
        ),
        _t("dim_coach", ("coach_id",), "upsert", ("coach_id", "text"), ("name", "text")),
        _t(
            "radar_list", ("season", "week", "position", "kind"), "lists",
            ("season", "integer"), ("week", "integer"), ("position", "text"), ("kind", "text"),
            ("as_of", TS), ("model_version", "text"), ("generated_at", TS),
            ("incomplete", "boolean"), ("n_pool", "integer"), ("note", "text"),
        ),
        _t(
            "radar_pick", ("season", "week", "position", "kind", "rank"), "lists",
            ("season", "integer"), ("week", "integer"), ("position", "text"), ("kind", "text"),
            ("rank", "integer"), ("gsis_id", "text"), ("team", "text"),
            ("chance", "double precision"), ("chance_low", "double precision"),
            ("chance_high", "double precision"), ("model_prob", "double precision"),
            ("tier", "text"), ("reasons", "jsonb"),
        ),
        _t(
            "radar_outcome", ("season", "week", "gsis_id"), "replace",
            ("season", "integer"), ("week", "integer"), ("gsis_id", "text"),
            ("y_hit", "boolean"), ("y_sustained", "boolean"), ("label_status", "text"),
            ("window_weeks", "integer[]"), ("window_ranks", "integer[]"),
            ("window_points", "double precision[]"),
        ),
        _t(
            "stream_list", ("season", "week", "position", "kind"), "lists",
            ("season", "integer"), ("week", "integer"), ("position", "text"), ("kind", "text"),
            ("as_of", TS), ("model_version", "text"), ("generated_at", TS),
            ("incomplete", "boolean"), ("n_pool", "integer"), ("note", "text"),
        ),
        _t(
            "stream_pick", ("season", "week", "position", "kind", "rank"), "lists",
            ("season", "integer"), ("week", "integer"), ("position", "text"), ("kind", "text"),
            ("rank", "integer"), ("entity_id", "text"), ("entity_type", "text"),
            ("display_name", "text"), ("team", "text"), ("next_opponent", "text"),
            ("home", "boolean"), ("chance", "double precision"),
            ("chance_low", "double precision"), ("chance_high", "double precision"),
            ("model_prob", "double precision"), ("tier", "text"), ("reasons", "jsonb"),
        ),
        _t(
            "stream_outcome", ("season", "week", "entity_id"), "replace",
            ("season", "integer"), ("week", "integer"), ("entity_id", "text"),
            ("y_start", "boolean"), ("label_status", "text"),
            ("points_next_week", "double precision"),
        ),
        _t(
            "regression_list", ("season", "week", "kind"), "lists",
            ("season", "integer"), ("week", "integer"), ("kind", "text"), ("as_of", TS),
            ("params_version", "text"), ("generated_at", TS), ("incomplete", "boolean"),
            ("n_universe", "integer"), ("note", "text"),
        ),
        _t(
            "regression_row", ("season", "week", "kind", "gsis_id"), "lists",
            ("season", "integer"), ("week", "integer"), ("kind", "text"), ("gsis_id", "text"),
            ("position", "text"), ("team", "text"), ("games", "integer"),
            ("ppg", "double precision"), ("ppg_ng", "double precision"),
            ("xfp_pg", "double precision"), ("xfp_pg_ng", "double precision"),
            ("fpoe_pg", "double precision"), ("fpoe_pg_ng", "double precision"),
            ("projection", "double precision"), ("shrinkage", "double precision"),
            ("tag", "text"), ("tags", "text[]"), ("tag_reason", "text"),
            ("projection_lo", "double precision"), ("projection_hi", "double precision"),
        ),
        _t(
            "regression_outcome", ("season", "week", "gsis_id"), "replace",
            ("season", "integer"), ("week", "integer"), ("gsis_id", "text"),
            ("ros_ppg", "double precision"), ("ros_games", "integer"), ("label_status", "text"),
        ),
        _t(
            "stream_track_record", ("line",), "replace",
            ("line", "integer"), ("position", "text"), ("method", "text"), ("train_on", "text"),
            ("scope", "text"), ("seasons", "text"), ("key", "text"), ("metric", "text"),
            ("value", "double precision"), ("lo", "double precision"),
            ("hi", "double precision"), ("share_above_zero", "double precision"),
            ("n_groups", "integer"), ("n_rows", "integer"), ("n_pos", "integer"),
        ),
        _t(
            "regression_track_record", ("line",), "replace",
            ("line", "integer"), ("section", "text"), ("weeks", "text"), ("position", "text"),
            ("method", "text"), ("metric", "text"), ("row_group", "text"),
            ("season", "integer"), ("value", "double precision"), ("lo", "double precision"),
            ("hi", "double precision"), ("n", "integer"), ("n_seasons", "integer"),
            ("share_above_zero", "double precision"), ("per_asof", "double precision"),
            ("not_graded", "integer"), ("detail", "text"),
        ),
        _t(  # step R1: reports/regression_watch/stability.csv row for row (the methodology page)
            "regression_stability", ("line",), "replace",
            ("line", "integer"), ("section", "text"), ("seasons", "text"), ("split", "text"),
            ("position", "text"), ("metric", "text"), ("g", "integer"), ("n", "integer"),
            ("value", "double precision"), ("lo", "double precision"),
            ("hi", "double precision"), ("var_signal", "double precision"),
            ("var_noise", "double precision"), ("prior_mean", "double precision"),
        ),
        # step H4a: the Hot-Seat Meter (FAMILIES["hot_seat"]; the coach is the site's slug)
        _t(
            "hot_seat_list", ("season", "week", "snapshot", "kind"), "lists",
            ("season", "integer"), ("week", "integer"), ("snapshot", "text"), ("kind", "text"),
            ("as_of", TS), ("model_version", "text"), ("generated_at", TS),
            ("incomplete", "boolean"), ("n_coaches", "integer"), ("note", "text"),
        ),
        _t(
            "hot_seat_row", ("season", "week", "snapshot", "kind", "coach_id"), "lists",
            ("season", "integer"), ("week", "integer"), ("snapshot", "text"), ("kind", "text"),
            ("coach_id", "text"), ("team", "text"), ("as_of", TS), ("rank", "integer"),
            ("probability", DP), ("is_interim", "boolean"), ("drivers", "jsonb"),
            ("reg_games_played", "integer"), ("reg_wins", DP), ("expected_wins", DP),
            ("wins_vs_expected", DP), ("point_diff_per_game", DP), ("tenure_seasons", "integer"),
            ("division_rank", "integer"), ("prev_season_wins", DP),
            ("consecutive_losing_seasons", "integer"), ("fourth_down_wp_lost_per_game", DP),
        ),
        _t(
            "hot_seat_outcome", ("season", "week", "coach_id"), "replace",
            ("season", "integer"), ("week", "integer"), ("coach_id", "text"),
            ("departed", "boolean"), ("censored", "boolean"), ("departure_type", "text"),
            ("announced", "date"), ("label_status", "text"),
        ),
        _t(  # reports/hot_seat/backtest_metrics.csv row for row
            "hot_seat_track_record", ("line",), "replace",
            ("line", "integer"), ("variant", "text"), ("model", "text"), ("prob", "text"),
            ("slice", "text"), ("metric", "text"), ("value", DP), ("lo", DP), ("hi", DP),
            ("n_rows", "integer"), ("n_pos", "integer"), ("n_seasons", "integer"),
        ),
        _t(  # reports/hot_seat/firings_per_season.csv row for row
            "hot_seat_firings", ("season",), "replace",
            ("season", "integer"), ("positive_departures", "integer"),
            ("fired_in_season", "integer"), ("positives_week_12", "integer"),
            ("positives_end_of_season", "integer"), ("censored_coach_seasons", "integer"),
            ("interim_coach_seasons", "integer"),
        ),
        # step I2c-a: the Cliff board (FAMILIES["board"]; week 0 = before week 1 of the season)
        _t(
            "board_list", ("season", "week", "snapshot", "kind"), "lists",
            ("season", "integer"), ("week", "integer"), ("snapshot", "text"), ("kind", "text"),
            ("as_of", TS), ("model_version", "text"), ("missed_version", "text"),
            ("generated_at", TS), ("incomplete", "boolean"), ("n_players", "integer"),
            ("note", "text"),
        ),
        _t(
            "board_row", ("season", "week", "snapshot", "kind", "gsis_id"), "lists",
            ("season", "integer"), ("week", "integer"), ("snapshot", "text"), ("kind", "text"),
            ("gsis_id", "text"), ("team", "text"), ("position", "text"), ("as_of", TS),
            ("cliff_rank", "integer"), ("cliff_probability", DP), ("missed_rank", "integer"),
            ("missed_probability", DP), ("ecr_rank", "integer"), ("age", DP),
            ("prior_seasons", "integer"), ("games_s", "integer"), ("ppg_s", DP),
            ("pos_rank_s", "integer"), ("ppg_change", DP), ("touches_per_game_s", DP),
            ("depth_rank_s1", "integer"), ("team_change_s1", "boolean"),
            ("dc_absent", "boolean"), ("hc_change_s1", "boolean"), ("cliff_drivers", "jsonb"),
            ("missed_drivers", "jsonb"),
        ),
        _t(
            "board_outcome", ("season", "week", "gsis_id"), "replace",
            ("season", "integer"), ("week", "integer"), ("gsis_id", "text"),
            ("games_s1", "integer"), ("ppg_s1", DP), ("y_cliff", "boolean"),
            ("y_missed", "boolean"), ("label_status", "text"),
        ),
        _t(  # reports/board/preseason_cliff.csv then preseason_breakout.csv, row for row
            "board_track_record", ("line",), "replace",
            ("line", "integer"), ("population", "text"), ("research", "boolean"),
            ("variant", "text"), ("slice", "text"), ("model", "text"), ("vs", "text"),
            ("metric", "text"), ("value", DP), ("lo", DP), ("hi", DP), ("share_above_zero", DP),
        ),
        _t(  # the frozen backtest's model-vs-ECR top-10 picks per ECR-era board
            "board_disagreement", ("variant", "season", "pick_group"), "replace",
            ("variant", "text"), ("model", "text"), ("season", "integer"), ("pick_group", "text"),
            ("players", "integer"), ("hits", "integer"),
        ),
        _t(
            "track_record",
            ("module", "label", "excl_rostered", "model", "scope", "scope_value", "season_from",
             "season_to", "metric"),
            "replace",
            ("module", "text"), ("model", "text"), ("label", "text"), ("metric", "text"),
            ("scope", "text"), ("scope_value", "text"), ("season_from", "integer"),
            ("season_to", "integer"), ("excl_rostered", "boolean"),
            ("value", "double precision"), ("low", "double precision"),
            ("high", "double precision"), ("n_lists", "integer"), ("n_positives", "integer"),
            ("n_rows", "integer"), ("n_top_hits", "integer"), ("n_top", "integer"),
        ),
        _t(
            "tier_stats", ("module", "model", "label", "week", "tier"), "replace",
            ("module", "text"), ("model", "text"), ("label", "text"), ("week", "integer"),
            ("tier", "text"), ("season_from", "integer"), ("season_to", "integer"),
            ("chance_low", "double precision"), ("chance_high", "double precision"),
            ("prob_low", "double precision"), ("prob_high", "double precision"),
            ("lists", "integer"), ("players", "integer"), ("hits", "integer"),
            ("hit_rate", "double precision"), ("per_list", "double precision"),
        ),
        _t(
            "player_week_summary", ("gsis_id", "season", "week"), "replace",
            ("gsis_id", "text"), ("season", "integer"), ("week", "integer"), ("team", "text"),
            ("position", "text"), ("fantasy_points", "double precision"),
            ("snap_share", "double precision"), ("target_share", "double precision"),
            ("carry_share", "double precision"), ("xfp", "double precision"),
            ("fpoe", "double precision"), ("points_ng", "double precision"),
            ("xfp_ng", "double precision"), ("fpoe_ng", "double precision"),
        ),
        _t(
            "glossary", ("name",), "replace",
            ("name", "text"), ("title", "text"), ("kind", "text"), ("unit", "text"),
            ("formula", "text"), ("explanation", "text"), ("verified", "text"),
            ("modules", "text[]"), ("model_output", "boolean"),
        ),
        # step P3: the Decision Report Card (DECISIONS)
        _t(
            "decision_fourth", ("game_id", "play_id"), "seasons",
            ("game_id", "text"), ("play_id", "integer"), ("season", "integer"),
            ("week", "integer"), ("season_type", "text"), ("posteam", "text"),
            ("defteam", "text"), ("coach_id", "text"), ("qtr", "integer"),
            ("quarter_seconds", "integer"), ("score_differential", "integer"),
            ("ydstogo", "integer"), ("yardline_100", "integer"), ("chosen", "text"),
            ("recommended", "text"), ("grade", "text"), ("correct", "boolean"),
            ("wp_go", DP), ("wp_fg", DP), ("wp_punt", DP), ("wp_lost", DP), ("p_convert", DP),
            ("p_make", DP), ("outcome", "text"),
        ),
        _t(
            "decision_two_point", ("game_id", "play_id"), "seasons",
            ("game_id", "text"), ("play_id", "integer"), ("season", "integer"),
            ("week", "integer"), ("season_type", "text"), ("posteam", "text"),
            ("defteam", "text"), ("coach_id", "text"), ("qtr", "integer"),
            ("quarter_seconds", "integer"), ("score_differential", "integer"),
            ("chosen", "text"), ("recommended", "text"), ("grade", "text"),
            ("correct", "boolean"), ("wp_kick", DP), ("wp_two_point", DP), ("wp_lost", DP),
            ("result", "text"),
        ),
        _t(
            "decision_clock", ("metric", "game_id", "team"), "seasons",
            ("metric", "text"), ("season", "integer"), ("week", "integer"),
            ("season_type", "text"), ("game_id", "text"), ("team", "text"), ("opp", "text"),
            ("coach_id", "text"), ("play_id", "integer"), ("qtr", "integer"),
            ("quarter_seconds", "integer"), ("down", "integer"), ("ydstogo", "integer"),
            ("yardline_100", "integer"), ("score_differential", "integer"),
            ("timeouts", "integer"), ("is_case", "boolean"), ("amount", DP), ("wp_left", DP),
            ("desc", "text"), ("detail", "jsonb"),
        ),
        _t(
            "coach_season", ("season", "coach_id"), "seasons",
            ("season", "integer"), ("coach_id", "text"), ("team", "text"), ("games", "integer"),
            *COACH_COUNTS, ("aggressiveness", DP), ("wp_lost_per_game", DP),
            ("m1_candidates", "integer"), ("m1_cases", "integer"),
            ("m1_timeouts_left", "integer"), ("m2_candidates", "integer"),
            ("m2_cases", "integer"), ("m2_ep_left", DP), ("m2_wp_left", DP),
            ("m3_decisive_games", "integer"), ("m3_cases", "integer"),
            ("m3_seconds_wasted", DP),
        ),
        _t(
            "coach_week", ("game_id", "coach_id"), "seasons",
            ("game_id", "text"), ("coach_id", "text"), ("season", "integer"),
            ("week", "integer"), ("season_type", "text"), ("team", "text"), ("opp", "text"),
            *COACH_COUNTS,
        ),
        _t(
            "decisions_track_record", ("source", "line"), "replace",
            ("source", "text"), ("line", "integer"), ("section", "text"), ("scope", "text"),
            ("subset", "text"), ("method", "text"), ("metric", "text"), ("value", DP),
            ("lo", DP), ("hi", DP), ("n", "integer"), ("n_blocks", "integer"),
        ),
        # feature #1 (migration 0007): the Questionable snapshots and their tables (QUESTIONABLE)
        _t(
            "questionable_list", ("season", "week", "as_of"), "snapshots",
            ("season", "integer"), ("week", "integer"), ("as_of", TS),
            ("model_version", "text"), ("generated_at", TS), ("n_players", "integer"),
            ("source", "text"),
        ),
        _t(
            "questionable_row", ("season", "week", "as_of", "gsis_id"), "snapshots",
            ("season", "integer"), ("week", "integer"), ("as_of", TS), ("gsis_id", "text"),
            ("position", "text"), ("team", "text"), ("opponent", "text"), ("game_id", "text"),
            ("kickoff", TS), ("report_status", "text"), ("practice_status", "text"),
            ("practice", "text"), ("missed_prev", "boolean"), ("body_part", "text"),
            ("play_chance", DP), ("plays_n", "integer"), ("plays_median", DP),
            ("plays_dud_rate", DP), ("healthy_median", DP), ("healthy_dud_rate", DP),
            ("season_ppg", DP), ("season_games", "integer"),
        ),
        _t(
            "questionable_history", ("report_status", "practice"), "replace",
            ("report_status", "text"), ("practice", "text"), ("seasons", "text"),
            ("n", "integer"), ("played", "integer"), ("played_rate", DP),
        ),
        _t(
            "questionable_backtest", ("grouping", "season"), "replace",
            ("grouping", "text"), ("title", "text"), ("chosen", "boolean"), ("season", "text"),
            ("n", "integer"), ("log_loss", DP), ("brier", DP), ("mean_p", DP),
            ("played_rate", DP),
        ),
        _t(
            "questionable_calibration", ("line",), "replace",
            ("line", "integer"), ("bucket", "text"), ("n", "integer"), ("predicted", DP),
            ("actual", DP),
        ),
        _t(
            "questionable_live", ("season", "report_status"), "replace",
            ("season", "integer"), ("report_status", "text"), ("n", "integer"),
            ("predicted", DP), ("actual", DP), ("pending", "integer"), ("weeks", "integer"),
        ),
        # feature #5 (migration 0008): the Teammate-out snapshots and their tables (TEAMMATE_OUT)
        _t(
            "teammate_out_list", ("season", "week", "as_of"), "snapshots",
            ("season", "integer"), ("week", "integer"), ("as_of", TS),
            ("model_version", "text"), ("generated_at", TS), ("n_teams", "integer"),
            ("n_out", "integer"), ("n_players", "integer"), ("source", "text"),
        ),
        _t(
            "teammate_out_row", ("season", "week", "as_of", "team", "gsis_id"), "snapshots",
            ("season", "integer"), ("week", "integer"), ("as_of", TS), ("team", "text"),
            ("gsis_id", "text"), ("opponent", "text"), ("game_id", "text"), ("kickoff", TS),
            ("out_ids", "text"), ("out_players", "text"), ("out_positions", "text"),
            ("out_reasons", "text"), ("n_out", "integer"), ("vac_carry_share", DP),
            ("vac_target_share", DP), ("position", "text"), ("role", "text"),
            ("base_games", "integer"), ("base_carry_share", DP), ("base_target_share", DP),
            ("base_snap_share", DP), ("base_points", DP), ("base_team_carries", DP),
            ("base_team_targets", DP), ("pred_carry_share", DP), ("pred_target_share", DP),
            ("carry_share_change", DP), ("target_share_change", DP), ("pred_points", DP),
            ("points_lo", DP), ("points_hi", DP), ("pred_gain", DP),
            ("alloc_carry_share", DP), ("alloc_target_share", DP),
        ),
        _t(
            "teammate_out_allocation", ("out_pos", "role"), "replace",
            ("out_pos", "text"), ("role", "text"), ("position", "text"), ("n", "integer"),
            ("carry", DP), ("target", DP), ("n_group", "integer"), ("carry_group", DP),
            ("target_group", DP),
        ),
        _t(
            "teammate_out_backtest", ("candidate", "season"), "replace",
            ("candidate", "text"), ("title", "text"), ("chosen", "boolean"),
            ("rule_pick", "boolean"), ("season", "text"), ("n", "integer"),
            ("events", "integer"), ("mae_carry", DP), ("mae_target", DP), ("mae_points", DP),
            ("top_hit", DP),
        ),
        _t(
            "teammate_out_coverage", ("position",), "replace",
            ("position", "text"), ("seasons", "text"), ("n", "integer"), ("below", "integer"),
            ("above", "integer"), ("inside", "integer"), ("coverage", DP),
        ),
        _t(
            "teammate_out_events", ("out_pos",), "replace",
            ("out_pos", "text"), ("seasons", "text"), ("sat", "integer"), ("kept", "integer"),
            ("no_roster_row", "integer"), ("gone_status", "integer"), ("events", "integer"),
            ("single", "integer"), ("multi", "integer"), ("teammate_rows", "integer"),
        ),
        _t(
            "teammate_out_live", ("season",), "replace",
            ("season", "integer"), ("n", "integer"), ("pending", "integer"),
            ("starter_played", "integer"), ("did_not_play", "integer"), ("weeks", "integer"),
            ("team_weeks", "integer"), ("ranged", "integer"), ("mae_points", DP),
            ("mae_points_base", DP), ("mae_carry_share", DP), ("mae_carry_share_base", DP),
            ("mae_target_share", DP), ("mae_target_share_base", DP), ("coverage", DP),
            ("top_hit", DP),
        ),
        # feature #6 (migration 0009): the playoff planner's snapshots, one per (season,
        # completed week), and its tables (PLAYOFF_PLANNER)
        _t(
            "playoff_planner_list", ("season", "through_week"), "snapshots",
            ("season", "integer"), ("through_week", "integer"), ("as_of", TS),
            ("model_version", "text"), ("generated_at", TS), ("n_teams", "integer"),
            ("n_rows", "integer"),
        ),
        _t(
            "playoff_planner_row", ("season", "through_week", "week", "team", "position"),
            "snapshots",
            ("season", "integer"), ("through_week", "integer"), ("week", "integer"),
            ("team", "text"), ("position", "text"), ("opponent", "text"), ("home", "boolean"),
            ("game_id", "text"), ("kickoff", TS), ("candidate", "text"), ("rating", DP),
            ("rating_rank", "integer"), ("raw", DP), ("shrunk", DP), ("adjusted", DP),
            ("opp_games", "integer"), ("lg_ppg", DP),
        ),
        _t(
            "playoff_planner_choice", ("position",), "replace",
            ("position", "text"), ("candidate", "text"), ("rule_choice", "text"),
            ("chosen_by", "text"), ("path", "text"), ("pseudo_games", DP),
        ),
        _t(
            "playoff_planner_backtest", ("position", "candidate"), "replace",
            ("position", "text"), ("candidate", "text"), ("title", "text"), ("n", "integer"),
            ("mae", DP), ("vs", "text"), ("seasons_won", "integer"), ("seasons", "integer"),
            ("took_over", "boolean"), ("chosen", "boolean"), ("rule_pick", "boolean"),
        ),
        _t(
            "playoff_planner_effects", ("position", "horizon"), "replace",
            ("position", "text"), ("horizon", "text"), ("n_worst", "integer"),
            ("n_best", "integer"), ("rated_gap", DP), ("shrunk_gap", DP),
            ("realized_gap", DP), ("survived", DP),
        ),
        _t(
            "playoff_planner_stability", ("position", "horizon"), "replace",
            ("position", "text"), ("horizon", "integer"), ("seasons", "integer"),
            ("rho_mean", DP), ("rho_min", DP), ("rho_max", DP),
        ),
        _t(
            "playoff_planner_late_weeks", ("era", "position", "week"), "replace",
            ("era", "text"), ("position", "text"), ("week", "integer"), ("based", "integer"),
            ("played_share", DP), ("vs_base", DP),
        ),
        _t(
            "playoff_planner_live", ("season", "position"), "replace",
            ("season", "integer"), ("position", "text"), ("n", "integer"),
            ("pending", "integer"), ("weeks", "integer"), ("mae_rating", DP),
            ("mae_flat", DP),
        ),
        # feature #10 (migration 0010): coach tendencies, every table replaced (COACH_TENDENCIES)
        _t(
            "coach_tendency_season", ("coach_id", "team", "season", "metric"), "replace",
            ("coach_id", "text"), ("team", "text"), ("season", "integer"), ("metric", "text"),
            ("is_current", "boolean"), ("through_week", "integer"), ("games", "integer"),
            ("plays", "integer"), ("value", DP), ("sample", "integer"), ("league_avg", DP),
            ("percentile", DP),
        ),
        _t(
            "coach_tendency_career", ("coach_id", "metric"), "replace",
            ("coach_id", "text"), ("metric", "text"), ("seasons", "integer"),
            ("first_season", "integer"), ("last_season", "integer"), ("teams", "text"),
            ("value", DP), ("sample", "integer"), ("league_avg", DP), ("vs_league", DP),
        ),
        _t(
            "coach_tendency_persistence", ("metric", "comparison"), "replace",
            ("metric", "text"), ("comparison", "text"), ("n_pairs", "integer"),
            ("n_seasons", "integer"), ("first_season", "integer"), ("last_season", "integer"),
            ("r", DP), ("ci_low", DP), ("ci_high", DP),
        ),
        _t(
            "coach_tendency_fantasy_link", ("metric", "target", "horizon"), "replace",
            ("metric", "text"), ("target", "text"), ("horizon", "text"), ("n", "integer"),
            ("n_seasons", "integer"), ("r", DP), ("ci_low", DP), ("ci_high", DP), ("x_sd", DP),
            ("y_per_x_sd", DP),
        ),
        # feature #8 (migration 0011): lead time vs the crowd, aggregates only, every table
        # replaced (LEAD_TIME); scope_value: '' (complete seasons pooled), a season or a position
        _t(
            "lead_time_coverage", ("season",), "replace",
            ("season", "integer"), ("baseline_date", "date"), ("baseline_period", "integer"),
            ("last_period", "integer"), ("complete", "boolean"), ("in_season_days", "integer"),
            ("first_in_season", "date"), ("last_in_season", "date"), ("n_players", "integer"),
            ("last_list_week", "integer"), ("adds_50", "integer"), ("adds_25", "integer"),
            ("in_study", "boolean"),
        ),
        _t(
            "lead_time_summary", ("threshold", "signal", "scope", "scope_value"), "replace",
            ("threshold", "integer"), ("signal", "text"), ("scope", "text"),
            ("scope_value", "text"), ("n_adds", "integer"), ("n_before", "integer"),
            ("n_same", "integer"), ("n_after", "integer"), ("n_never", "integer"),
            ("n_never_out_of_pool", "integer"), ("share_before", DP), ("share_same", DP),
            ("share_after", DP), ("share_never", DP), ("lead_median", DP), ("lead_q1", DP),
            ("lead_q3", DP), ("nearest_median", DP), ("share_before_4", DP),
        ),
        _t(
            "lead_time_hist", ("threshold", "signal", "lead"), "replace",
            ("threshold", "integer"), ("signal", "text"), ("lead", "integer"), ("n", "integer"),
        ),
        _t(
            "lead_time_reverse", ("threshold", "signal", "scope", "scope_value", "state"),
            "replace",
            ("threshold", "integer"), ("signal", "text"), ("scope", "text"),
            ("scope_value", "text"), ("state", "text"), ("n", "integer"), ("hits", "integer"),
            ("hit_rate", DP), ("lead_median", DP),
        ),
        _t(
            "lead_time_conversion", ("threshold", "signal", "scope", "scope_value"), "replace",
            ("threshold", "integer"), ("signal", "text"), ("scope", "text"),
            ("scope_value", "text"), ("n_flags", "integer"), ("n_added", "integer"),
            ("n_added_after", "integer"), ("weeks", "integer"), ("flags_per_week", DP),
            ("share_added", DP), ("share_added_after", DP),
        ),
        _t(
            "lead_time_h2h", ("threshold", "level", "h2h"), "replace",
            ("threshold", "integer"), ("level", "text"), ("h2h", "text"), ("n", "integer"),
            ("n_radar_earlier", "integer"), ("share", DP),
        ),
    )
}  # fmt: skip

# Write order (parents before children); deletes run in the reverse order.
WRITE_ORDER = (
    "model_versions", "dim_team", "dim_player", "dim_coach", "radar_list", "radar_pick",
    "radar_outcome", "stream_list", "stream_pick", "stream_outcome", "regression_list",
    "regression_row", "regression_outcome", "track_record", "stream_track_record",
    "regression_track_record", "regression_stability", "hot_seat_list", "hot_seat_row",
    "hot_seat_outcome", "hot_seat_track_record", "hot_seat_firings", "board_list", "board_row",
    "board_outcome", "board_track_record", "board_disagreement", "decision_fourth",
    "decision_two_point",
    "decision_clock", "coach_season", "coach_week", "decisions_track_record",
    "questionable_list", "questionable_row", "questionable_history", "questionable_backtest",
    "questionable_calibration", "questionable_live", "teammate_out_list", "teammate_out_row",
    "teammate_out_allocation", "teammate_out_backtest", "teammate_out_coverage",
    "teammate_out_events", "teammate_out_live", "playoff_planner_list", "playoff_planner_row",
    "playoff_planner_choice", "playoff_planner_backtest", "playoff_planner_effects",
    "playoff_planner_stability", "playoff_planner_late_weeks", "playoff_planner_live",
    "coach_tendency_season", "coach_tendency_career", "coach_tendency_persistence",
    "coach_tendency_fantasy_link", "lead_time_coverage", "lead_time_summary", "lead_time_hist",
    "lead_time_reverse", "lead_time_conversion", "lead_time_h2h", "tier_stats",
    "player_week_summary", "glossary", "site_meta", "pipeline_runs",
)  # fmt: skip
REPLACED = tuple(n for n in WRITE_ORDER if TABLES[n].mode == "replace")
assert set(WRITE_ORDER) == set(TABLES), "WRITE_ORDER must name every table"


@dataclass(frozen=True)
class Family:
    """One module's weekly lists as a publish treats them (step P2: the Radar's rules, the same
    code for every module). ``lists`` / ``rows`` hold 'live' lists (frozen once published) and
    'backtest' lists (replaced); ``outcomes`` and ``replaced`` are the module's replaced
    tables. ``key``: a list's key without its kind; ``row_id``: the column naming the listed
    player (with season and week, the outcomes' key); ``order``: the rows' order in a list
    (None: by ``row_id``); ``version``: the list's model version column; ``unit``: the hash
    unit of its backtest lists (site_meta ``hash:<unit>``); ``meta``: the prefix of its
    site_meta keys; ``label``: how the printed summary names the module ('' for the Radar)."""

    module: str
    lists: str
    rows: str
    outcomes: str
    key: tuple[str, ...]
    row_id: str
    order: str | None
    version: str
    unit: str
    replaced: tuple[str, ...]
    meta: str
    label: str


FAMILIES: dict[str, Family] = {
    f.module: f
    for f in (
        Family("waiver_radar", "radar_list", "radar_pick", "radar_outcome",
               ("season", "week", "position"), "gsis_id", "rank", "model_version",
               "backtest_lists", ("track_record", "tier_stats"), "", ""),
        Family("streamer", "stream_list", "stream_pick", "stream_outcome",
               ("season", "week", "position"), "entity_id", "rank", "model_version",
               "stream_backtest_lists", ("stream_track_record",), "stream_", "streamer"),
        Family("regression_watch", "regression_list", "regression_row", "regression_outcome",
               ("season", "week"), "gsis_id", None, "params_version",
               "regression_backtest_lists", ("regression_track_record", "regression_stability"),
               "regression_",
               "regression watch"),
        Family("hot_seat", "hot_seat_list", "hot_seat_row", "hot_seat_outcome",
               ("season", "week", "snapshot"), "coach_id", "rank", "model_version",
               "hot_seat_backtest_lists", ("hot_seat_track_record", "hot_seat_firings"),
               "hot_seat_", "hot seat"),
        Family("board", "board_list", "board_row", "board_outcome",
               ("season", "week", "snapshot"), "gsis_id", "cliff_rank", "model_version",
               "board_backtest_lists", ("board_track_record", "board_disagreement"), "board_",
               "board"),
    )
}  # fmt: skip
# Replaced tables every module shares (published whatever modules a publish carries).
SHARED = ("player_week_summary", "glossary")


@dataclass(frozen=True)
class SeasonSplit:
    """The Decision Report Card's tables as a publish treats them (step P3; mode 'seasons'):
    ``tables`` split at the approved grading's season S into the frozen ``history`` unit
    (seasons < S) and the ``season`` unit (S); ``replaced``: its other replaced tables;
    ``dims``: its upserted dimension; ``label``: how the summary names it."""

    module: str
    tables: tuple[str, ...]
    history: str
    season: str
    replaced: tuple[str, ...]
    dims: tuple[str, ...]
    label: str


DECISIONS = SeasonSplit("decisions", ("decision_fourth", "decision_two_point", "decision_clock",
                                      "coach_season", "coach_week"),
                        "decisions_history", "decisions_season", ("decisions_track_record",),
                        ("dim_coach",), "decision report card")  # fmt: skip


@dataclass(frozen=True)
class Snapshots:
    """The Questionable list's tables as a publish treats them (feature #1; mode 'snapshots'):
    ``lists`` / ``rows`` append-only, keyed by ``key`` (rows: plus ``row_id``); ``replaced``:
    its replaced tables, each its own hash unit; ``label``: how the summary names it."""

    module: str
    lists: str
    rows: str
    key: tuple[str, ...]
    row_id: str
    replaced: tuple[str, ...]
    label: str


QUESTIONABLE = Snapshots("questionable", "questionable_list", "questionable_row",
                         ("season", "week", "as_of"), "gsis_id",
                         ("questionable_history", "questionable_backtest",
                          "questionable_calibration", "questionable_live"),
                         "questionable")  # fmt: skip
# feature #5: the same rules; a row is one teammate of a team in a snapshot (row key: team too)
TEAMMATE_OUT = Snapshots("teammate_out", "teammate_out_list", "teammate_out_row",
                         ("season", "week", "as_of"), "gsis_id",
                         ("teammate_out_allocation", "teammate_out_backtest",
                          "teammate_out_coverage", "teammate_out_events", "teammate_out_live"),
                         "teammate out")  # fmt: skip
# feature #6: one snapshot per (season, completed week) (the runner's store starts empty each
# night and writes the same through_week until the next week completes: keyed by it, never by
# as_of); a row is one (playoff week, team, position)
PLAYOFF_PLANNER = Snapshots("playoff_planner", "playoff_planner_list", "playoff_planner_row",
                            ("season", "through_week"), "position",
                            ("playoff_planner_choice", "playoff_planner_backtest",
                             "playoff_planner_effects", "playoff_planner_stability",
                             "playoff_planner_late_weeks", "playoff_planner_live"),
                            "playoff planner")  # fmt: skip
SNAPSHOTS = (QUESTIONABLE, TEAMMATE_OUT, PLAYOFF_PLANNER)
# feature #10: coach tendencies, rebuilt from the warehouse on every publish: four replaced
# tables (each its own hash unit, guarded like the others); their coaches join dim_coach
COACH_TENDENCIES = ("coach_tendency_season", "coach_tendency_career",
                    "coach_tendency_persistence", "coach_tendency_fantasy_link")  # fmt: skip
# feature #8: lead time vs the crowd, rebuilt on every publish from the study's aggregate-only
# frames (twm.publish.lead_time): six replaced tables, each its own hash unit, guarded
LEAD_TIME = ("lead_time_coverage", "lead_time_summary", "lead_time_hist", "lead_time_reverse",
             "lead_time_conversion", "lead_time_h2h")  # fmt: skip
_owned = [n for f in FAMILIES.values() for n in (f.lists, f.rows, f.outcomes, *f.replaced)]
assert set(_owned) | set(SHARED) | {"site_meta"} | set(DECISIONS.replaced) | {
    n for s in SNAPSHOTS for n in s.replaced
} | set(COACH_TENDENCIES) | set(LEAD_TIME) == {
    n for n, t in TABLES.items() if t.mode in ("replace", "lists")
}, (
    "every replaced or list table belongs to one family, SHARED, DECISIONS, SNAPSHOTS, "
    "COACH_TENDENCIES, LEAD_TIME or site_meta"
)
assert set(DECISIONS.tables) == {n for n, t in TABLES.items() if t.mode == "seasons"}
assert {n for s in SNAPSHOTS for n in (s.lists, s.rows)} == {
    n for n, t in TABLES.items() if t.mode == "snapshots"
}
