"""The Waiver Radar candidate pool: who is *probably still on waivers* at a Tuesday as-of (C1).

There is no record of which players sat on fantasy rosters before 2020 (FantasyPros'
rostership starts then, and covers only the players it ranks), so the backtests need a
stand-in. The owner's rule (2026-09-27, PROJECT_SPEC 8.1): a player is in the pool when
he is **outside the top N at his position by BOTH** a preseason ranking **and** his fantasy
points per game (PPG) so far this season, where N = the weekly starter threshold x
``candidate_pool_multiplier`` (derived from config/league.yaml: ``League.candidate_pool_cutoffs``;
QB 18, RB 36, WR 36, TE 18 in the default 12-team league). A player that high on either list
was drafted or has been picked up by now in a typical league of that size.

- **Preseason ranking.** Seasons with a FantasyPros preseason cheat sheet (2020 on, found in
  the data): his rank on the last August/September cheat sheet before the season's first game
  (``method = 'ecr'``). Earlier seasons (``method = 'prior_ppg'``): his rank by last season's
  PPG among players at his current position with at least ``prior_season_min_games`` games;
  rookies drafted in rounds 1 to ``rookie_drafted_rounds`` count as drafted.
- **PPG to date.** Regular-season fantasy points (config/scoring.yaml) per game played, over the
  games visible at the as-of, ranked within position among roster players with a game (ties
  share the better rank).
- **Universe.** Every player on an NFL roster at the as-of: his latest weekly-roster row of the
  season that is public by then (``fact_roster_week``), if his team's latest public roster
  lists him, at QB/RB/WR/TE (the position of that roster week, never today's), with a status
  in ``roster_statuses`` (ACT, INA, DEV) when the season's rosters carry weekly statuses (from
  2016; before that the status is the season-final one and is ignored, see
  :func:`statuses_are_weekly`). docs/waiver_radar.md has the full rules.

Everything is read through an :class:`~twm.asof.AsOfView`, so nothing after the as-of can
leak in (tests/test_waiver_radar_pool.py runs the leakage harness on it). Rostership numbers
from FantasyPros (``owned_avg``, ``owned_espn``) are attached for diagnostics only: they exist
for 2020+ and let the report check how well the stand-in matches reality.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import polars as pl

from twm import ids
from twm.asof import AsOfView
from twm.config import FANTASY_POSITIONS, League, league
from twm.scoring import ScoringRules, score_sql

Method = Literal["auto", "ecr", "prior_ppg"]
METHODS = ("ecr", "prior_ppg")

# Rostership diagnostics come from the season's latest weekly FantasyPros scrape visible at the
# as-of; a player missing from it (bye week) takes his row from a scrape up to this many days
# older (weekly scrapes are about 7 days apart).
OWNERSHIP_MAX_AGE_DAYS = 8

# PPG is rounded to this many decimals before ranking (see _base_sql).
PPG_DECIMALS = 6

# Output columns, in order (tests and the CLI rely on them).
POOL_COLUMNS = (
    "season", "week", "as_of", "gsis_id", "name", "team", "position", "status", "roster_week",
    "method", "preseason_source", "preseason_pos_rank", "preseason_rank_pos",
    "prior_season_ppg", "prior_season_games", "ppg_to_date", "games_to_date", "ppg_pos_rank",
    "in_pool", "excluded_by", "owned_avg", "owned_espn", "owned_scrape_date",
)  # fmt: skip


@dataclass(frozen=True)
class PoolRules:
    """The pool's knobs as plain values (from config/league.yaml and config/scoring.yaml).
    ``teams`` and ``multiplier`` (the league size and ``candidate_pool_multiplier``) are used
    only in generated text."""

    cutoffs: Mapping[str, int]
    roster_statuses: tuple[str, ...] = ("ACT", "INA", "DEV")
    prior_season_min_games: int = 4
    rookie_drafted_rounds: int = 2
    scoring: ScoringRules = field(default_factory=ScoringRules.from_config)
    teams: int | None = None
    multiplier: float | None = None

    def __post_init__(self) -> None:
        missing = [p for p in FANTASY_POSITIONS if p not in self.cutoffs]
        if missing:
            raise ValueError(f"cutoffs lack positions {missing}")

    @classmethod
    def from_config(
        cls, lg: League | None = None, scoring: ScoringRules | None = None
    ) -> PoolRules:
        lg = lg or league()
        return cls(
            cutoffs=lg.candidate_pool_cutoffs(),
            roster_statuses=tuple(lg.pool.roster_statuses),
            prior_season_min_games=lg.pool.prior_season_min_games,
            rookie_drafted_rounds=lg.pool.rookie_drafted_rounds,
            scoring=scoring or ScoringRules.from_config(),
            teams=lg.teams,
            multiplier=lg.candidate_pool_multiplier,
        )


def _sql_list(values: Iterable[str]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def statuses_are_weekly(rosters: pl.DataFrame) -> bool:
    """Do the season's public weekly rosters carry a status OF THAT WEEK?

    The 2002-2015 rosters do not: no player's status ever changes within one of those seasons
    (a player who ended the season on injured reserve is RES on every week's roster, the
    weeks he played included), so their status is hindsight and roster_status must not use
    it. From 2016 statuses change from week to week (ACT, then RES ...). Decided from the
    rosters public at the as-of: statuses count as weekly once any player shows two different
    ones (at a week-1 as-of no rule-1 teammate can exist anyway: he would have played in
    the same game-day week)."""
    if rosters.height == 0:
        return False
    changed = (
        rosters.filter(pl.col("status").is_not_null())
        .group_by("gsis_id")
        .agg(pl.col("status").n_unique().alias("n"))
        .filter(pl.col("n") > 1)
    )
    return changed.height > 0


def _base_sql(season: int, rules: PoolRules, *, apply_status: bool = True) -> str:
    """One row per universe player with every input of both methods (see module docstring).

    Read through the as-of view: each table name below is its point-in-time view."""
    s = int(season)
    pts = score_sql(rules.scoring)
    positions = _sql_list(FANTASY_POSITIONS)
    statuses = _sql_list(rules.roster_statuses)
    # Statuses are trusted only when the season's rosters carry a status OF THAT WEEK; the
    # 2002-2015 rosters stamp the season-final status on every week (a player who ended the
    # season on IR is RES even in weeks he played), so filtering on it would leak the future.
    where = f"WHERE r.status IN ({statuses})" if apply_status else ""
    return f"""
    WITH latest AS (
        -- each player's latest public roster row of the season (a team on its bye week is
        -- missing from that week's game-day roster, so the latest row can be a week older)
        SELECT gsis_id, week AS roster_week, team, position, status, full_name, entry_year
        FROM fact_roster_week WHERE season = {s}
        QUALIFY row_number() OVER (PARTITION BY gsis_id ORDER BY week DESC) = 1
    ),
    team_latest AS (
        SELECT team, max(week) AS team_week FROM fact_roster_week WHERE season = {s}
        GROUP BY team
    ),
    roster AS (
        -- ... and only if it is on his team's latest public roster: a player released
        -- without a CUT row simply vanishes from the next roster (2016 week 1 lists the
        -- summer camp bodies), so an older row of his is not "on a roster" any more
        SELECT l.* FROM latest l
        JOIN team_latest t ON t.team = l.team AND t.team_week = l.roster_week
        WHERE l.position IN ({positions})
    ),
    games AS (
        -- PPG rounded to 6 decimals: a float sum's last bits can depend on the order DuckDB
        -- adds the rows in, and equal PPGs must tie (points are multiples of 0.01)
        SELECT player_id AS gsis_id, count(*) AS games,
               round(sum({pts}) / count(*), {PPG_DECIMALS}) AS ppg
        FROM fact_player_week WHERE season = {s} AND season_type = 'REG' GROUP BY 1
    ),
    ppg AS (
        -- ranked among every roster player of the position with a game (any status)
        SELECT r.gsis_id, g.games, g.ppg,
               rank() OVER (PARTITION BY r.position ORDER BY g.ppg DESC) AS pos_rank
        FROM roster r JOIN games g USING (gsis_id) WHERE g.games >= 1
    ),
    prior_all AS (
        SELECT player_id AS gsis_id, count(*) AS games,
               round(sum({pts}) / count(*), {PPG_DECIMALS}) AS ppg
        FROM fact_player_week WHERE season = {s - 1} AND season_type = 'REG' GROUP BY 1
    ),
    prior AS (
        -- last season's PPG, ranked within the player's CURRENT roster position
        SELECT r.gsis_id,
               rank() OVER (PARTITION BY r.position ORDER BY p.ppg DESC) AS pos_rank
        FROM roster r JOIN prior_all p USING (gsis_id)
        WHERE p.games >= {int(rules.prior_season_min_games)}
    ),
    pre_pages AS (
        SELECT season, scrape_date, page_pos, pos, gsis_id, pos_rank FROM fact_ranking
        WHERE season = {s} AND ecr_type = 'rp' AND page_kind = 'preseason'
    ),
    pre_scrape AS ({ids.preseason_scrape_sql("pre_pages")}),
    pre AS (
        SELECT p.* FROM pre_pages p JOIN pre_scrape USING (season, scrape_date)
        WHERE p.gsis_id IS NOT NULL AND p.pos_rank IS NOT NULL
    ),
    ecr AS (
        -- his rank on his roster position's cheat sheet; else his rank on his own
        -- FantasyPros position's sheet (judged against that position's cutoff)
        SELECT r.gsis_id,
               min(x.pos_rank) FILTER (WHERE x.page_pos = r.position) AS rank_roster_pos,
               min(x.pos_rank) FILTER (WHERE x.page_pos = x.pos AND x.pos <> r.position)
                   AS rank_fp_pos,
               min(x.pos) FILTER (WHERE x.page_pos = x.pos AND x.pos <> r.position) AS fp_pos
        FROM roster r JOIN pre x USING (gsis_id) GROUP BY r.gsis_id
    ),
    rookie AS (
        SELECT r.gsis_id FROM roster r JOIN dim_player d USING (gsis_id)
        WHERE d.draft_year = {s} AND d.draft_round <= {int(rules.rookie_drafted_rounds)}
          AND COALESCE(r.entry_year, {s}) = {s}
    ),
    wp AS (
        SELECT scrape_date, gsis_id, player_owned_avg, player_owned_espn FROM fact_ranking
        WHERE season = {s} AND ecr_type = 'wp' AND page_kind = 'weekly'
    ),
    wp_recent AS (
        -- the season's latest weekly scrape, or one up to OWNERSHIP_MAX_AGE_DAYS older for a
        -- player missing from it (a team on its bye week is not in that week's rankings)
        SELECT * FROM wp WHERE gsis_id IS NOT NULL
          AND scrape_date >= (SELECT max(scrape_date) FROM wp) - {OWNERSHIP_MAX_AGE_DAYS}
    ),
    own_date AS (
        SELECT gsis_id, max(scrape_date) AS owned_scrape_date FROM wp_recent GROUP BY gsis_id
    ),
    own AS (
        SELECT d.gsis_id, d.owned_scrape_date, max(w.player_owned_avg) AS owned_avg,
               max(w.player_owned_espn) AS owned_espn
        FROM own_date d JOIN wp_recent w
          ON w.gsis_id = d.gsis_id AND w.scrape_date = d.owned_scrape_date
        GROUP BY d.gsis_id, d.owned_scrape_date
    )
    SELECT r.gsis_id, r.full_name AS name, r.team, r.position, r.status, r.roster_week,
           e.rank_roster_pos, e.rank_fp_pos, e.fp_pos,
           pr.pos_rank AS prior_rank,
           pa.ppg AS prior_season_ppg, CAST(COALESCE(pa.games, 0) AS INTEGER)
               AS prior_season_games,
           p.ppg AS ppg_to_date, CAST(COALESCE(p.games, 0) AS INTEGER) AS games_to_date,
           p.pos_rank AS ppg_pos_rank,
           rk.gsis_id IS NOT NULL AS drafted_rookie,
           o.owned_avg, o.owned_espn, o.owned_scrape_date,
           (SELECT count(*) FROM pre_scrape) > 0 AS has_ecr
    FROM roster r
    LEFT JOIN ecr e USING (gsis_id)
    LEFT JOIN prior pr USING (gsis_id)
    LEFT JOIN prior_all pa USING (gsis_id)
    LEFT JOIN ppg p USING (gsis_id)
    LEFT JOIN rookie rk USING (gsis_id)
    LEFT JOIN own o USING (gsis_id)
    {where}
    ORDER BY r.position, r.gsis_id"""


BASE_SCHEMA = {
    "gsis_id": pl.String(),
    "name": pl.String(),
    "team": pl.String(),
    "position": pl.String(),
    "status": pl.String(),
    "roster_week": pl.Int32(),
    "rank_roster_pos": pl.Int64(),
    "rank_fp_pos": pl.Int64(),
    "fp_pos": pl.String(),
    "prior_rank": pl.Int64(),
    "prior_season_ppg": pl.Float64(),
    "prior_season_games": pl.Int32(),
    "ppg_to_date": pl.Float64(),
    "games_to_date": pl.Int32(),
    "ppg_pos_rank": pl.Int64(),
    "drafted_rookie": pl.Boolean(),
    "owned_avg": pl.Float64(),
    "owned_espn": pl.Float64(),
    "owned_scrape_date": pl.Date(),
    "has_ecr": pl.Boolean(),
}


def _base(view: AsOfView, season: int, week: int, rules: PoolRules) -> pl.DataFrame:
    rosters = view.sql(f"SELECT gsis_id, status FROM fact_roster_week WHERE season = {int(season)}")
    df = view.sql(_base_sql(season, rules, apply_status=statuses_are_weekly(rosters)))
    df = df.cast({c: t for c, t in BASE_SCHEMA.items() if c in df.columns}, strict=False)
    return df.select(list(BASE_SCHEMA)).with_columns(
        pl.lit(int(season), dtype=pl.Int32).alias("season"),
        pl.lit(int(week), dtype=pl.Int32).alias("week"),
        pl.lit(view.as_of, dtype=pl.Datetime("us", "UTC")).alias("as_of"),
    )


def available_methods(base: pl.DataFrame) -> list[str]:
    """``['ecr', 'prior_ppg']`` when the season has a preseason cheat sheet visible, else
    ``['prior_ppg']`` (an empty universe has no evidence either way: ``['prior_ppg']``)."""
    has_ecr = base.height > 0 and bool(base.get_column("has_ecr").any())
    return ["ecr", "prior_ppg"] if has_ecr else ["prior_ppg"]


def _derive(base: pl.DataFrame, method: str, rules: PoolRules) -> pl.DataFrame:
    """Apply one method's preseason list and the PPG list to the base rows."""
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS} (or 'auto'), not {method!r}")
    cut = pl.col("_list_pos").replace_strict(dict(rules.cutoffs), default=None)
    own_cut = pl.col("position").replace_strict(dict(rules.cutoffs), default=None)
    if method == "ecr":
        df = base.with_columns(
            pl.coalesce("rank_roster_pos", "rank_fp_pos").alias("preseason_pos_rank"),
            pl.when(pl.col("rank_roster_pos").is_not_null())
            .then(pl.col("position"))
            .otherwise(pl.col("fp_pos"))
            .alias("_list_pos"),
            pl.lit(False).alias("_rookie"),
        ).with_columns(
            pl.when(pl.col("preseason_pos_rank").is_not_null())
            .then(pl.lit("ecr"))
            .otherwise(pl.lit("unranked"))
            .alias("preseason_source")
        )
    else:
        df = base.with_columns(
            pl.when(pl.col("drafted_rookie"))
            .then(pl.lit(None, dtype=pl.Int64))
            .otherwise(pl.col("prior_rank"))
            .alias("preseason_pos_rank"),
            pl.col("position").alias("_list_pos"),
            pl.col("drafted_rookie").alias("_rookie"),
        ).with_columns(
            pl.when(pl.col("_rookie"))
            .then(pl.lit("rookie_draft"))
            .when(pl.col("preseason_pos_rank").is_not_null())
            .then(pl.lit("prior_ppg"))
            .otherwise(pl.lit("unranked"))
            .alias("preseason_source")
        )
    pre = (pl.col("preseason_pos_rank") <= cut).fill_null(False)
    ppg = (pl.col("ppg_pos_rank") <= own_cut).fill_null(False)
    pre_label = (
        pl.when(pl.col("_rookie"))
        .then(pl.lit("rookie_draft"))
        .when(pre)
        .then(pl.lit("preseason"))
        .otherwise(pl.lit(None, dtype=pl.String))
    )
    df = df.with_columns(
        pl.lit(method).alias("method"),
        pl.when(pl.col("preseason_pos_rank").is_not_null())
        .then(pl.col("_list_pos"))
        .otherwise(pl.lit(None, dtype=pl.String))
        .alias("preseason_rank_pos"),
        ~(pre | ppg | pl.col("_rookie")).alias("in_pool"),
        pl.when(pre_label.is_not_null() & ppg)
        .then(pre_label + pl.lit("+ppg"))
        .when(pre_label.is_not_null())
        .then(pre_label)
        .when(ppg)
        .then(pl.lit("ppg"))
        .otherwise(pl.lit(None, dtype=pl.String))
        .alias("excluded_by"),
    )
    return df.select(
        pl.col("season"),
        pl.col("week"),
        pl.col("as_of"),
        *[pl.col(c) for c in POOL_COLUMNS[3:]],
    ).cast({"preseason_pos_rank": pl.Int32, "ppg_pos_rank": pl.Int32})


def candidate_pool(
    view: AsOfView,
    season: int,
    week: int,
    *,
    method: Method = "auto",
    rules: PoolRules | None = None,
) -> pl.DataFrame:
    """The candidate pool at the view's as-of: one row per universe player (``POOL_COLUMNS``).

    ``week`` is the label of the as-of (normally ``view`` was opened at that week's
    ``weekly_as_of``); only the view decides what is visible. ``method``: 'auto' (ECR when the
    season has a preseason cheat sheet visible, else last season's PPG), 'ecr' (ValueError if
    there is none) or 'prior_ppg'. ``in_pool`` is True for players outside the top N by both
    lists; ``excluded_by`` says which list(s) kept a player out.
    """
    if method not in ("auto", *METHODS):
        raise ValueError(f"method must be one of {('auto', *METHODS)}, not {method!r}")
    rules = rules or PoolRules.from_config()
    base = _base(view, season, week, rules)
    methods = available_methods(base)
    chosen = methods[0] if method == "auto" else method
    if chosen not in methods:
        raise ValueError(
            f"no preseason cheat sheet of {season} is visible at {view.as_of:%Y-%m-%d %H:%M} "
            "UTC, so method 'ecr' is not available; use 'prior_ppg' or 'auto'"
        )
    return _derive(base, chosen, rules)


# --------------------------------------------------------------------------------------
# History: every regular-season as-of
# --------------------------------------------------------------------------------------


def pool_weeks(
    db: Path | str, seasons: Sequence[int], weeks: Sequence[int] | None = None
) -> list[tuple[int, int, datetime]]:
    """The regular-season weeks whose Tuesday as-of has already happened in the data.

    A week counts once its games are in the cache: at least one of its games has a final
    score, and every game due to be over by the as-of (``fact_game.available_at`` at or before
    it) has one. So a week whose Monday-night game is not ingested yet is left out, and in the
    current season the list stops at the last complete week. (A game moved past the as-of, the
    five split weeks, does not hold its week back.) Returns (season, week, as_of) sorted.
    """
    from twm.warehouse.build import connect

    if not seasons:
        return []
    in_seasons = ", ".join(str(int(s)) for s in seasons)
    week_filter = ""
    if weeks is not None:
        week_filter = " AND w.week IN (" + (", ".join(str(int(x)) for x in weeks) or "NULL") + ")"
    con = connect(db, read_only=True)
    try:
        rows = con.execute(f"""
            SELECT w.season, w.week, w.asof_weekly_utc FROM dim_week w
            WHERE w.season_type = 'REG' AND w.season IN ({in_seasons}){week_filter}
              AND EXISTS (SELECT 1 FROM fact_game g WHERE g.season = w.season
                          AND g.week = w.week AND g.season_type = 'REG'
                          AND g.result IS NOT NULL)
              AND NOT EXISTS (SELECT 1 FROM fact_game g WHERE g.season = w.season
                              AND g.week = w.week AND g.season_type = 'REG'
                              AND g.result IS NULL AND g.available_at <= w.asof_weekly_utc)
            ORDER BY w.season, w.week""").fetchall()
    finally:
        con.close()
    return [(int(s), int(w), t.replace(tzinfo=UTC)) for s, w, t in rows]


def pool_history(
    db: Path | str,
    seasons: Sequence[int],
    weeks: Sequence[int] | None = None,
    *,
    methods: Sequence[Method] = ("auto",),
    rules: PoolRules | None = None,
) -> pl.DataFrame:
    """The candidate pool at every regular-season as-of of ``seasons`` (see :func:`pool_weeks`
    for which as-ofs count), concatenated. ``methods``: 'auto' gives one pool per as-of; pass
    ``("ecr", "prior_ppg")`` to get both where the season has a preseason cheat sheet (the
    ``method`` column tells them apart; 'ecr' is skipped where there is none)."""
    rules = rules or PoolRules.from_config()
    frames = []
    for season, week, as_of in pool_weeks(db, seasons, weeks):
        with AsOfView(db, as_of) as view:
            base = _base(view, season, week, rules)
        have = available_methods(base)
        wanted: list[str] = []
        for m in methods:
            chosen = have[0] if m == "auto" else m
            if chosen in have and chosen not in wanted:
                wanted.append(chosen)
        frames += [_derive(base, m, rules) for m in wanted]
    if not frames:
        return _derive(_empty_base(), "prior_ppg", rules)
    return pl.concat(frames, how="vertical")


def _empty_base() -> pl.DataFrame:
    return pl.DataFrame(schema=BASE_SCHEMA).with_columns(
        pl.lit(None, dtype=pl.Int32).alias("season"),
        pl.lit(None, dtype=pl.Int32).alias("week"),
        pl.lit(None, dtype=pl.Datetime("us", "UTC")).alias("as_of"),
    )
