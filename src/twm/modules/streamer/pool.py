"""The streamer's candidate pool (S1b): kickers and team defenses probably on waivers at a Tuesday
as-of.

The Waiver Radar's rule (C1, :mod:`twm.modules.waiver_radar.pool`), applied to K and DST: an
entity is in the pool when it is **outside the top N at its position by BOTH** a preseason
ranking **and** its fantasy points per game (PPG) so far this season. N = teams x lineup slots of
the position x ``candidate_pool_multiplier`` (config/league.yaml), rounded half up: K 18 and
DST 18 in the default 12-team league with one K and one D/ST slot. A different league shape
changes N by itself (:class:`StreamerRules`).

- **Entities.** A kicker is his ``gsis_id`` (entity_id = gsis_id); a team defense / special
  teams unit is its team (entity_id = ``"DST-<team>"``, e.g. ``DST-SF``: never a gsis_id, which
  starts with digits). Team codes are the warehouse's current franchise codes (LA, LAC, LV).
- **Universe.** K: every player on an NFL roster at the as-of whose latest public roster row of
  the season says ``K`` (the Radar's roster rule, :func:`~twm.modules.waiver_radar.pool.
  roster_ctes_sql`: the position of that roster week, never today's; statuses ACT/INA/DEV when
  the season's rosters carry weekly statuses). DST: every team on the season's schedule
  (``fact_schedule``, public from the spring schedule release).
- **Preseason ranking.** 2020 on (``method = 'ecr'``): the rank on FantasyPros' K or DST
  cheat sheet (``fact_ranking_kdst``), the last August/September scrape before week 1 (the
  Radar's :func:`twm.ids.preseason_scrape_sql`). Earlier seasons (``'prior_ppg'``): the rank by
  last season's PPG (K: among the universe's kickers with at least ``prior_season_min_games``
  games; DST: among the teams). No rookie rule (a drafted kicker is not drafted in fantasy).
- **PPG to date.** Regular-season points (config/scoring.yaml ``kicking:`` / ``defense:``)
  per game over the games visible at the as-of; a kicker's games are the games with a kick
  attempt (``fact_kicker_week``); ranked among the universe with a game; ties share the better
  rank (SQL rank()).

Everything is read through an :class:`~twm.asof.AsOfView` (tests/test_streamer_pool.py runs the
leakage harness). FantasyPros rostership (``owned_avg``, weekly K/DST pages, late 2020 on) is
attached for diagnostics only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import polars as pl

from twm import ids
from twm.asof import AsOfView
from twm.config import STREAMER_POSITIONS, League, league, round_half_up
from twm.modules.waiver_radar.pool import (
    METHODS,
    OWNERSHIP_MAX_AGE_DAYS,
    PPG_DECIMALS,
    Method,
    pool_weeks,
    roster_ctes_sql,
    statuses_are_weekly,
)
from twm.scoring_kdst import DefenseRules, KickingRules, score_defense_sql, score_kicking_sql

# Lineup slot names that hold a team defense (config/league.yaml uses DST; ESPN says D/ST).
DST_SLOT_NAMES = ("DST", "D/ST", "DEF")
DST_PREFIX = "DST-"

# Output columns, in order (tests and the CLI rely on them).
POOL_COLUMNS = (
    "season", "week", "as_of", "position", "entity_id", "gsis_id", "name", "team", "status",
    "roster_week", "method", "preseason_source", "preseason_pos_rank", "prior_season_ppg",
    "prior_season_games", "ppg_to_date", "games_to_date", "ppg_pos_rank", "in_pool",
    "excluded_by", "is_team_kicker", "owned_avg", "owned_scrape_date",
)  # fmt: skip


def _slot_position(slot: str) -> str | None:
    return "K" if slot == "K" else ("DST" if slot in DST_SLOT_NAMES else None)


def streamer_slots(lg: League) -> dict[str, int]:
    """Lineup slots per team for K and DST: slots named K / DST (D/ST, DEF), plus any
    multi-position slot (slot_eligibility) that can hold only one of them."""
    slots = dict.fromkeys(STREAMER_POSITIONS, 0)
    for slot, count in lg.lineup.items():
        pos = _slot_position(slot)
        if pos is None and slot in lg.slot_eligibility:
            holds = {_slot_position(p) for p in lg.slot_eligibility[slot]}
            if len(holds) == 1 and None not in holds:
                pos = holds.pop()
        if pos is not None:
            slots[pos] += int(count)
    return slots


@dataclass(frozen=True)
class StreamerRules:
    """The streamer's league-shaped numbers and scoring (from config/league.yaml and
    config/scoring.yaml). ``slots``: lineup slots per team at K and DST."""

    slots: Mapping[str, int]
    teams: int
    multiplier: float
    roster_statuses: tuple[str, ...] = ("ACT", "INA", "DEV")
    prior_season_min_games: int = 4
    kicking: KickingRules = field(default_factory=KickingRules.from_config)
    defense: DefenseRules = field(default_factory=DefenseRules.from_config)

    def __post_init__(self) -> None:
        missing = [p for p in STREAMER_POSITIONS if p not in self.slots]
        if missing:
            raise ValueError(f"slots lack positions {missing}")
        if self.teams < 1 or self.multiplier < 1:
            raise ValueError("need teams >= 1 and multiplier >= 1")

    @classmethod
    def from_config(cls, lg: League | None = None) -> StreamerRules:
        lg = lg or league()
        return cls(
            slots=streamer_slots(lg),
            teams=lg.teams,
            multiplier=lg.candidate_pool_multiplier,
            roster_statuses=tuple(lg.pool.roster_statuses),
            prior_season_min_games=lg.pool.prior_season_min_games,
        )

    @property
    def positions(self) -> tuple[str, ...]:
        """The positions the league starts (a league without a K slot streams DST only)."""
        return tuple(p for p in STREAMER_POSITIONS if self.slots[p] > 0)

    @property
    def start_thresholds(self) -> dict[str, int]:
        """Teams x slots: a weekly finish at or above it is a start (the label)."""
        return {p: self.teams * self.slots[p] for p in STREAMER_POSITIONS}

    @property
    def cutoffs(self) -> dict[str, int]:
        """The pool's N: teams x slots x candidate_pool_multiplier, rounded half up."""
        return {
            p: round_half_up(self.teams * self.slots[p] * self.multiplier)
            for p in STREAMER_POSITIONS
        }

    def check_positions(self, positions: Sequence[str] | None) -> tuple[str, ...]:
        """``positions`` (default: the league's) validated against the league's lineup."""
        chosen = tuple(p.upper() for p in positions) if positions else self.positions
        bad = [p for p in chosen if p not in STREAMER_POSITIONS or self.slots.get(p, 0) < 1]
        if bad:
            raise ValueError(
                f"positions {bad}: the streamer covers {list(STREAMER_POSITIONS)} with at least "
                f"one lineup slot (this league: {dict(self.slots)})"
            )
        return chosen


def _universe_sql(position: str, season: int) -> str:
    """CTEs ending in ``universe`` (key, name, team, status, roster_week): K from the roster
    rule, DST from the season's schedule. Read through the as-of view."""
    s = int(season)
    if position == "K":
        return f"""{roster_ctes_sql(s, ["K"])},
    universe AS (
        SELECT gsis_id AS key, full_name AS name, team, status, roster_week FROM roster
    )"""
    return f"""universe AS (
        SELECT team AS key, team || ' D/ST' AS name, team, CAST(NULL AS VARCHAR) AS status,
               CAST(NULL AS INTEGER) AS roster_week
        FROM (SELECT home_team AS team FROM fact_schedule WHERE season = {s}
                AND season_type = 'REG'
              UNION SELECT away_team FROM fact_schedule WHERE season = {s}
                AND season_type = 'REG')
        WHERE team IS NOT NULL
    )"""


def _points_sql(position: str, season: int, rules: StreamerRules) -> str:
    """SELECT key, season, pts: every regular-season game of ``season`` and the one before."""
    seasons = f"{int(season) - 1}, {int(season)}"
    if position == "K":
        pts = score_kicking_sql(rules.kicking, table_alias="k")
        return (
            f"SELECT k.player_id AS key, k.season, {pts} AS pts FROM fact_kicker_week k "
            f"WHERE k.season IN ({seasons}) AND k.season_type = 'REG'"
        )
    pts = score_defense_sql(rules.defense, table_alias="d")
    return (
        f"SELECT d.team AS key, d.season, {pts} AS pts FROM fact_defense_week d "
        f"WHERE d.season IN ({seasons}) AND d.season_type = 'REG'"
    )


def _team_kicker_sql(position: str, season: int) -> str:
    """CTE ``team_kicker`` (key, team): who kicked in each team's latest game with a kick
    attempt so far this season (K only; point in time through the view; a diagnostic, not a
    pool rule: practice-squad and camp kickers stay in the universe like the Radar's)."""
    if position != "K":
        return (
            "team_kicker AS (SELECT CAST(NULL AS VARCHAR) AS key, "
            "CAST(NULL AS VARCHAR) AS team WHERE false)"
        )
    return f"""team_kicker AS (
        SELECT player_id AS key, team FROM fact_kicker_week
        WHERE season = {int(season)} AND season_type = 'REG'
        QUALIFY week = max(week) OVER (PARTITION BY team)
    )"""


def _base_sql(position: str, season: int, rules: StreamerRules, *, apply_status: bool) -> str:
    """One row per universe entity with the inputs of both methods (module docstring)."""
    s = int(season)
    key = "gsis_id" if position == "K" else "nfl_team"
    statuses = ", ".join(f"'{x}'" for x in rules.roster_statuses)
    where = f"WHERE u.status IN ({statuses})" if position == "K" and apply_status else ""
    return f"""
    WITH {_universe_sql(position, s)},
    pts AS ({_points_sql(position, s, rules)}),
    games AS (
        -- PPG rounded like the Radar's: equal PPGs must tie whatever order DuckDB sums in
        SELECT key, count(*) AS games, round(sum(pts) / count(*), {PPG_DECIMALS}) AS ppg
        FROM pts WHERE season = {s} GROUP BY 1
    ),
    ppg AS (
        SELECT u.key, g.games, g.ppg, rank() OVER (ORDER BY g.ppg DESC) AS pos_rank
        FROM universe u JOIN games g USING (key) WHERE g.games >= 1
    ),
    prior_all AS (
        SELECT key, count(*) AS games, round(sum(pts) / count(*), {PPG_DECIMALS}) AS ppg
        FROM pts WHERE season = {s - 1} GROUP BY 1
    ),
    prior AS (
        SELECT u.key, rank() OVER (ORDER BY p.ppg DESC) AS pos_rank
        FROM universe u JOIN prior_all p USING (key)
        WHERE p.games >= {int(rules.prior_season_min_games)}
    ),
    pre_pages AS (
        SELECT season, scrape_date, {key} AS key, pos_rank FROM fact_ranking_kdst
        WHERE season = {s} AND ecr_type = 'rp' AND page_kind = 'preseason'
          AND page_pos = '{position}' AND pos = '{position}'
    ),
    pre_scrape AS ({ids.preseason_scrape_sql("pre_pages")}),
    ecr AS (
        SELECT p.key, min(p.pos_rank) AS pos_rank
        FROM pre_pages p JOIN pre_scrape USING (season, scrape_date)
        WHERE p.key IS NOT NULL AND p.pos_rank IS NOT NULL GROUP BY 1
    ),
    wp AS (
        SELECT scrape_date, {key} AS key, player_owned_avg FROM fact_ranking_kdst
        WHERE season = {s} AND ecr_type = 'wp' AND page_kind = 'weekly'
          AND page_pos = '{position}' AND {key} IS NOT NULL
    ),
    wp_recent AS (
        SELECT * FROM wp
        WHERE scrape_date >= (SELECT max(scrape_date) FROM wp) - {OWNERSHIP_MAX_AGE_DAYS}
    ),
    own AS (
        SELECT w.key, d.owned_scrape_date, max(w.player_owned_avg) AS owned_avg
        FROM (SELECT key, max(scrape_date) AS owned_scrape_date FROM wp_recent GROUP BY key) d
        JOIN wp_recent w ON w.key = d.key AND w.scrape_date = d.owned_scrape_date
        GROUP BY w.key, d.owned_scrape_date
    ),
    {_team_kicker_sql(position, s)}
    SELECT '{position}' AS position, u.key, u.name, u.team, u.status, u.roster_week,
           e.pos_rank AS ecr_rank, pr.pos_rank AS prior_rank,
           pa.ppg AS prior_season_ppg, CAST(COALESCE(pa.games, 0) AS INTEGER)
               AS prior_season_games,
           p.ppg AS ppg_to_date, CAST(COALESCE(p.games, 0) AS INTEGER) AS games_to_date,
           p.pos_rank AS ppg_pos_rank, o.owned_avg, o.owned_scrape_date,
           {"tk.key IS NOT NULL" if position == "K" else "CAST(NULL AS BOOLEAN)"}
               AS is_team_kicker,
           (SELECT count(*) FROM pre_scrape) > 0 AS has_ecr
    FROM universe u
    LEFT JOIN ecr e USING (key)
    LEFT JOIN prior pr USING (key)
    LEFT JOIN prior_all pa USING (key)
    LEFT JOIN ppg p USING (key)
    LEFT JOIN own o USING (key)
    LEFT JOIN team_kicker tk ON tk.key = u.key AND tk.team = u.team
    {where}
    ORDER BY u.key"""


BASE_SCHEMA = {
    "position": pl.String(),
    "key": pl.String(),
    "name": pl.String(),
    "team": pl.String(),
    "status": pl.String(),
    "roster_week": pl.Int32(),
    "ecr_rank": pl.Int64(),
    "prior_rank": pl.Int64(),
    "prior_season_ppg": pl.Float64(),
    "prior_season_games": pl.Int32(),
    "ppg_to_date": pl.Float64(),
    "games_to_date": pl.Int32(),
    "ppg_pos_rank": pl.Int64(),
    "owned_avg": pl.Float64(),
    "owned_scrape_date": pl.Date(),
    "is_team_kicker": pl.Boolean(),
    "has_ecr": pl.Boolean(),
}


def _base(
    view: AsOfView, season: int, week: int, position: str, rules: StreamerRules
) -> pl.DataFrame:
    apply_status = False
    if position == "K":
        rosters = view.sql(
            f"SELECT gsis_id, status FROM fact_roster_week WHERE season = {int(season)}"
        )
        apply_status = statuses_are_weekly(rosters)
    df = view.sql(_base_sql(position, season, rules, apply_status=apply_status))
    df = df.cast({c: t for c, t in BASE_SCHEMA.items() if c in df.columns}, strict=False)
    return df.select(list(BASE_SCHEMA)).with_columns(
        pl.lit(int(season), dtype=pl.Int32).alias("season"),
        pl.lit(int(week), dtype=pl.Int32).alias("week"),
        pl.lit(view.as_of, dtype=pl.Datetime("us", "UTC")).alias("as_of"),
    )


def available_methods(base: pl.DataFrame) -> list[str]:
    """``['ecr', 'prior_ppg']`` when the position's preseason cheat sheet is visible, else
    ``['prior_ppg']`` (an empty universe too)."""
    has_ecr = base.height > 0 and bool(base.get_column("has_ecr").any())
    return ["ecr", "prior_ppg"] if has_ecr else ["prior_ppg"]


def _derive(base: pl.DataFrame, method: str, rules: StreamerRules) -> pl.DataFrame:
    """Apply one method's preseason list and the PPG list (both against the position's N)."""
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS} (or 'auto'), not {method!r}")
    rank_col = "ecr_rank" if method == "ecr" else "prior_rank"
    cut = pl.col("position").replace_strict(rules.cutoffs, default=None)
    df = base.with_columns(pl.col(rank_col).alias("preseason_pos_rank"))
    pre = (pl.col("preseason_pos_rank") <= cut).fill_null(False)
    ppg = (pl.col("ppg_pos_rank") <= cut).fill_null(False)
    is_dst = pl.col("position") == "DST"
    df = df.with_columns(
        pl.lit(method).alias("method"),
        pl.when(pl.col("preseason_pos_rank").is_not_null())
        .then(pl.lit(method))
        .otherwise(pl.lit("unranked"))
        .alias("preseason_source"),
        pl.when(is_dst)
        .then(pl.lit(DST_PREFIX) + pl.col("key"))
        .otherwise(pl.col("key"))
        .alias("entity_id"),
        pl.when(is_dst)
        .then(pl.lit(None, dtype=pl.String))
        .otherwise(pl.col("key"))
        .alias("gsis_id"),
        ~(pre | ppg).alias("in_pool"),
        pl.when(pre & ppg)
        .then(pl.lit("preseason+ppg"))
        .when(pre)
        .then(pl.lit("preseason"))
        .when(ppg)
        .then(pl.lit("ppg"))
        .otherwise(pl.lit(None, dtype=pl.String))
        .alias("excluded_by"),
    )
    return df.select(list(POOL_COLUMNS)).cast(
        {"preseason_pos_rank": pl.Int32, "ppg_pos_rank": pl.Int32}
    )


def _methods_for(base: pl.DataFrame, methods: Sequence[str]) -> list[str]:
    have = available_methods(base)
    wanted: list[str] = []
    for m in methods:
        chosen = have[0] if m == "auto" else m
        if chosen in have and chosen not in wanted:
            wanted.append(chosen)
    return wanted


def candidate_pool(
    view: AsOfView,
    season: int,
    week: int,
    *,
    method: Method = "auto",
    rules: StreamerRules | None = None,
    positions: Sequence[str] | None = None,
) -> pl.DataFrame:
    """The K and DST pool at the view's as-of: one row per universe entity (``POOL_COLUMNS``),
    K rows first. ``method``: 'auto' (per position, ECR when its preseason cheat sheet is
    visible, else last season's PPG), 'ecr' (ValueError where there is none) or 'prior_ppg'.
    ``positions``: default the league's (K and DST with one slot each by default)."""
    if method not in ("auto", *METHODS):
        raise ValueError(f"method must be one of {('auto', *METHODS)}, not {method!r}")
    rules = rules or StreamerRules.from_config()
    frames = []
    for position in rules.check_positions(positions):
        base = _base(view, season, week, position, rules)
        chosen = _methods_for(base, [method])
        if not chosen:
            raise ValueError(
                f"no preseason {position} cheat sheet of {season} is visible at "
                f"{view.as_of:%Y-%m-%d %H:%M} UTC, so method 'ecr' is not available; use "
                "'prior_ppg' or 'auto'"
            )
        frames.append(_derive(base, chosen[0], rules))
    return pl.concat(frames, how="vertical")


def pool_history(
    db: Path | str,
    seasons: Sequence[int],
    weeks: Sequence[int] | None = None,
    *,
    methods: Sequence[Method] = ("auto",),
    rules: StreamerRules | None = None,
    positions: Sequence[str] | None = None,
) -> pl.DataFrame:
    """The pool at every regular-season as-of of ``seasons`` whose games are in the cache (the
    Radar's :func:`~twm.modules.waiver_radar.pool.pool_weeks`), concatenated. ``methods`` as in
    the Radar's pool_history: ('ecr', 'prior_ppg') gives both where ECR exists."""
    rules = rules or StreamerRules.from_config()
    chosen_positions = rules.check_positions(positions)
    frames = []
    for season, week, as_of in pool_weeks(db, seasons, weeks):
        with AsOfView(db, as_of) as view:
            bases = [_base(view, season, week, p, rules) for p in chosen_positions]
        for base in bases:
            frames += [_derive(base, m, rules) for m in _methods_for(base, methods)]
    if not frames:
        return _derive(_empty_base(), "prior_ppg", rules)
    return pl.concat(frames, how="vertical")


def _empty_base() -> pl.DataFrame:
    return pl.DataFrame(schema=BASE_SCHEMA).with_columns(
        pl.lit(None, dtype=pl.Int32).alias("season"),
        pl.lit(None, dtype=pl.Int32).alias("week"),
        pl.lit(None, dtype=pl.Datetime("us", "UTC")).alias("as_of"),
    )


def pool_as_of_week(
    db: Path | str, season: int, week: int, **kwargs: object
) -> tuple[datetime, pl.DataFrame]:
    """(as_of, pool) at the official Tuesday as-of of (season, week): for the CLI."""
    from twm.asof import weekly_as_of

    when = weekly_as_of(db, season, week)
    with AsOfView(db, when) as view:
        return when, candidate_pool(view, season, week, **kwargs)  # type: ignore[arg-type]
