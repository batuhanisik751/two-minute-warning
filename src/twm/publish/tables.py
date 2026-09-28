"""The published tables as the Python writer sees them (step E2).

``web/db/schema.ts`` (Drizzle) owns the DDL; this module lists, per table, the columns the
publisher writes (in order, with their Postgres types, which COPY needs) and how a publish
treats the table's rows. A postgres-marked test migrates a fresh database and checks that
these lists equal the real columns, so the two files cannot drift apart silently.

How a publish treats each table (the reviewer's rules of 2026-09-28):

- **replace**: every row is deleted and written again in the publish transaction: the rows are
  derived from local files and can always be rebuilt (track_record, tier_stats, glossary,
  player_week_summary, site_meta, radar_outcome).
- **lists**: radar_list / radar_pick hold two kinds of list. 'backtest' lists (reconstructed)
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
            ("fpoe", "double precision"),
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
    "track_record", "tier_stats", "player_week_summary", "glossary", "site_meta",
    "pipeline_runs",
)  # fmt: skip
REPLACED = tuple(n for n in WRITE_ORDER if TABLES[n].mode == "replace")
assert set(WRITE_ORDER) == set(TABLES), "WRITE_ORDER must name every table"
