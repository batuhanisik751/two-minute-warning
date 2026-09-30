"""The published tables as the Python writer sees them (step E2).

``web/db/schema.ts`` (Drizzle) owns the DDL; this module lists, per table, the columns the
publisher writes (in order, with their Postgres types, which COPY needs) and how a publish
treats the table's rows. A postgres-marked test migrates a fresh database and checks that
these lists equal the real columns, so the two files cannot drift apart silently.

How a publish treats each table (the reviewer's rules of 2026-09-28):

- **replace**: every row is deleted and written again in the publish transaction: the rows are
  derived from local files and can always be rebuilt (track_record, tier_stats, glossary,
  player_week_summary, site_meta, radar_outcome; step P2: stream_outcome, regression_outcome,
  stream_track_record, regression_track_record).
- **lists**: radar_list / radar_pick (and, step P2, stream_list / stream_pick and
  regression_list / regression_row, with the same code: :data:`FAMILIES`) hold two kinds of
  list. 'backtest' lists (reconstructed)
  are replaced like the tables above. 'live' lists (made in real time) are **frozen**: a live
  list is inserted only when its (season, week, position) is not in the target yet and is never
  deleted or changed by a normal publish (``--replace-live SEASON-Wnn`` is the owner's explicit
  way to replace one week). A fresh runner with an empty predictions store therefore never
  loses a live list.
- **upsert**: rows are inserted or updated by primary key, never deleted: frozen live lists may
  reference a model version or a player that the current run does not have (model_versions,
  dim_team, dim_player).
- **append**: one new row per run, never changed (pipeline_runs).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Mode = Literal["replace", "lists", "upsert", "append"]


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
    )
}  # fmt: skip

# Write order (parents before children); deletes run in the reverse order.
WRITE_ORDER = (
    "model_versions", "dim_team", "dim_player", "radar_list", "radar_pick", "radar_outcome",
    "stream_list", "stream_pick", "stream_outcome", "regression_list", "regression_row",
    "regression_outcome", "track_record", "stream_track_record", "regression_track_record",
    "tier_stats", "player_week_summary", "glossary", "site_meta", "pipeline_runs",
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
               "regression_backtest_lists", ("regression_track_record",), "regression_",
               "regression watch"),
    )
}  # fmt: skip
# Replaced tables every module shares (published whatever modules a publish carries).
SHARED = ("player_week_summary", "glossary")
_owned = [n for f in FAMILIES.values() for n in (f.lists, f.rows, f.outcomes, *f.replaced)]
assert set(_owned) | set(SHARED) | {"site_meta"} == {
    n for n, t in TABLES.items() if t.mode in ("replace", "lists")
}, "every replaced or list table belongs to one family, SHARED or site_meta"
