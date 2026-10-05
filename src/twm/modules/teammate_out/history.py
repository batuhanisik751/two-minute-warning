"""The event study's rows (docs/teammate_out.md, "Data and definitions").

- **team game**: a regular-season game of a team with a ``fact_team_week`` row; ``gi`` numbers
  a team's games of a season 1, 2, ... in week order.
- **position**: PFR's game position (``fact_snaps.position``), else his weekly-roster
  position (``fact_player_week.position`` is a today's-snapshot column: not used).
- **played**: at least one offensive snap (``fact_snaps.offense_snaps`` > 0; PFR lists only
  players who took a snap) or a carry / target for the team that week.
- **carry share / target share**: his carries / targets over his team's (``fact_team_week``)
  in that game; a game he did not play is not averaged (his share "when he plays").
- **starter** before a team game: a RB whose carry share averaged at least
  :data:`RB_CARRY_SHARE`, or a WR / TE whose target share averaged at least
  :data:`TARGET_SHARE`, over the games he played among his team's previous :data:`WINDOW`
  games of the season (at least :data:`MIN_WINDOW_GAMES` of them).
- **event** (a starter out): the starter took no offensive snap in the team's game while the
  team played, and was still with the team (a weekly-roster row on it that week whose status
  is not one of :data:`LEFT_STATUSES`: traded, released and retired players are not "out",
  they are gone).
- **baseline games** of an event: the team's earlier games of the season in which every
  starter out in that game played. A teammate's baseline is his mean over the baseline
  games he played (at least :data:`MIN_BASE_GAMES`); the team's is its mean volume over them.

These are outcomes (labels): the module reads the warehouse directly, never through the as-of
view, and the backtest fits only on seasons before the one it scores.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import duckdb
import polars as pl

FIRST_SEASON = 2013  # fact_snaps (PFR) starts in 2013
POSITIONS = ("RB", "WR", "TE")
WINDOW = 4
MIN_WINDOW_GAMES = 2
RB_CARRY_SHARE = 0.45
TARGET_SHARE = 0.20
MIN_BASE_GAMES = 2
LEFT_STATUSES = ("CUT", "TRD", "RET", "UFA", "RFA")
DECIMALS = 6


def _in(values: Sequence[object]) -> str:
    """An SQL IN list of constants (strings quoted; only this module's own values)."""
    return ", ".join(f"'{v}'" if isinstance(v, str) else str(int(str(v))) for v in values)


def position_sql(col: str) -> str:
    """A player position as RB / WR / TE / QB (FB and HB are running backs), else NULL."""
    return (f"CASE WHEN {col} IN ('RB', 'HB', 'FB') THEN 'RB' WHEN {col} IN ('WR', 'TE', 'QB') "
            f"THEN {col} END")  # fmt: skip


def _ctes(seasons: Sequence[int]) -> str:
    """tg: team games (volume, game index ``gi``); pg: RB/WR/TE player-team-games with shares
    and points (config/scoring.yaml); starter: the starters before each team game."""
    from twm.scoring import score_sql

    s = _in(seasons)
    return f"""
    tg AS (SELECT season, week, team, game_id, coalesce(carries, 0) AS team_carries,
                  coalesce(targets, 0) AS team_targets,
                  row_number() OVER (PARTITION BY season, team ORDER BY week) AS gi
           FROM fact_team_week WHERE season_type = 'REG' AND season IN ({s})),
    sn AS (SELECT season, week, team, gsis_id, max(offense_snaps) AS snaps,
                  max(offense_pct) AS snap_share, min(position) AS snap_pos
           FROM fact_snaps WHERE game_type = 'REG' AND gsis_id IS NOT NULL AND season IN ({s})
           GROUP BY ALL),
    st AS (SELECT season, week, team, player_id AS gsis_id,
                  sum(coalesce(carries, 0)) AS carries, sum(coalesce(targets, 0)) AS targets,
                  round(sum({score_sql()}), {DECIMALS}) AS points
           FROM fact_player_week WHERE season_type = 'REG' AND season IN ({s})
             AND player_id IS NOT NULL GROUP BY ALL),
    ro AS (SELECT season, week, team, gsis_id, min(position) AS position
           FROM fact_roster_week WHERE season_type = 'REG' AND season IN ({s})
             AND gsis_id IS NOT NULL GROUP BY ALL),
    pj AS (SELECT coalesce(sn.season, st.season) AS season, coalesce(sn.week, st.week) AS week,
                  coalesce(sn.team, st.team) AS team, coalesce(sn.gsis_id, st.gsis_id) AS gsis_id,
                  coalesce(sn.snap_pos, ro.position) AS raw_position,
                  coalesce(sn.snaps, 0) AS snaps, coalesce(sn.snap_share, 0) AS snap_share,
                  coalesce(st.carries, 0) AS carries, coalesce(st.targets, 0) AS targets,
                  coalesce(st.points, 0) AS points
           FROM sn FULL JOIN st ON sn.season = st.season AND sn.week = st.week
                               AND sn.team = st.team AND sn.gsis_id = st.gsis_id
           LEFT JOIN ro ON ro.season = coalesce(sn.season, st.season)
                       AND ro.week = coalesce(sn.week, st.week)
                       AND ro.team = coalesce(sn.team, st.team)
                       AND ro.gsis_id = coalesce(sn.gsis_id, st.gsis_id)),
    pg AS (SELECT p.season, p.week, p.team, g.gi, g.game_id, p.gsis_id,
                  {position_sql("p.raw_position")} AS position, p.snaps,
                  p.snap_share, p.carries, p.targets, p.points, g.team_carries, g.team_targets,
                  CASE WHEN g.team_carries > 0 THEN p.carries / g.team_carries ELSE 0 END
                      AS carry_share,
                  CASE WHEN g.team_targets > 0 THEN p.targets / g.team_targets ELSE 0 END
                      AS target_share,
                  (p.snaps > 0 OR p.carries + p.targets > 0) AS played
           FROM pj p JOIN tg g USING (season, week, team)
           WHERE {position_sql("p.raw_position")} IN ({_in(POSITIONS)})),
    win AS (SELECT g.season, g.week, g.team, g.gi, p.gsis_id, arg_max(p.position, p.gi) AS position,
                   count(*) AS win_games, round(avg(p.carry_share), {DECIMALS}) AS win_carry_share,
                   round(avg(p.target_share), {DECIMALS}) AS win_target_share
            FROM tg g JOIN pg p ON p.season = g.season AND p.team = g.team AND p.played
                               AND p.gi BETWEEN g.gi - {WINDOW} AND g.gi - 1
            GROUP BY ALL),
    starter AS (SELECT * FROM win WHERE win_games >= {MIN_WINDOW_GAMES} AND (
                    (position = 'RB' AND win_carry_share >= {RB_CARRY_SHARE})
                    OR (position IN ('WR', 'TE') AND win_target_share >= {TARGET_SHARE})))"""


def players_sql(seasons: Sequence[int]) -> str:
    """Every RB/WR/TE player-team-game row (played or not) of ``seasons``."""
    return f"WITH {_ctes(seasons)} SELECT * FROM pg ORDER BY season, team, gi, gsis_id"


def candidates_sql(seasons: Sequence[int]) -> str:
    """Every starter (before a team game) who took no offensive snap that week, with
    ``with_team``: a roster row on the team that week not in :data:`LEFT_STATUSES` (NULL: no
    roster row on the team)."""
    s = _in(seasons)
    return f"""
    WITH {_ctes(seasons)},
    anyplay AS (SELECT DISTINCT season, week, gsis_id FROM pg WHERE played),
    ros AS (SELECT season, week, team, gsis_id,
                   bool_or(status IS NULL OR status NOT IN ({_in(LEFT_STATUSES)})) AS with_team
            FROM fact_roster_week WHERE season_type = 'REG' AND season IN ({s})
              AND gsis_id IS NOT NULL GROUP BY ALL)
    SELECT s.*, r.with_team FROM starter s
    LEFT JOIN anyplay a ON a.season = s.season AND a.week = s.week AND a.gsis_id = s.gsis_id
    LEFT JOIN ros r ON r.season = s.season AND r.week = s.week AND r.team = s.team
                   AND r.gsis_id = s.gsis_id
    WHERE a.gsis_id IS NULL ORDER BY s.season, s.team, s.gi, s.gsis_id"""


def connect(db: Path | str) -> duckdb.DuckDBPyConnection:
    """The warehouse, read-only (history rows are outcomes; never the as-of view)."""
    con = duckdb.connect(str(db), read_only=True)
    con.execute("SET TimeZone='UTC'")
    return con


ROLE_CAP = {"RB": 3, "WR": 4, "TE": 2}  # deeper players share the role "<pos>+"
_KEY = ["season", "team", "gi"]


def out_starters(cands: pl.DataFrame) -> pl.DataFrame:
    """The events' absent starters (``with_team`` true): season, week, team, gi, out_id,
    out_pos and his window shares."""
    return cands.filter(pl.col("with_team").fill_null(False)).select(
        "season", "week", "team", "gi", pl.col("gsis_id").alias("out_id"),
        pl.col("position").alias("out_pos"), "win_games", "win_carry_share", "win_target_share",
    )  # fmt: skip


def baseline_games(outs: pl.DataFrame, pg: pl.DataFrame) -> pl.DataFrame:
    """(season, team, gi, bgi): each event's baseline games (earlier games of the season in
    which every starter out in it played)."""
    n_out = outs.group_by(_KEY).agg(pl.len().alias("n_out"))
    played = pg.filter(pl.col("played")).select(
        "season", "team", pl.col("gi").alias("bgi"), pl.col("gsis_id").alias("out_id")
    )
    cnt = (outs.select(*_KEY, "out_id").join(played, on=["season", "team", "out_id"])
           .filter(pl.col("bgi") < pl.col("gi")).group_by(*_KEY, "bgi")
           .agg(pl.len().alias("n_in")))  # fmt: skip
    return (cnt.join(n_out, on=_KEY).filter(pl.col("n_in") == pl.col("n_out"))
            .select(*_KEY, "bgi").sort(*_KEY, "bgi"))  # fmt: skip


def _means(rows: pl.DataFrame, by: list[str], prefix: str) -> pl.DataFrame:
    cols = ("carry_share", "target_share", "snap_share", "points", "carries", "targets")
    return rows.group_by(by).agg(
        pl.len().alias(f"{prefix}games"),
        *[pl.col(c).mean().round(DECIMALS).alias(f"{prefix}{c}") for c in cols],
    )


def vacated(outs: pl.DataFrame, bg: pl.DataFrame, pg: pl.DataFrame) -> pl.DataFrame:
    """Per event: the absent starters' summed baseline carry and target shares, the team's
    baseline volume and its volume in the game (events with fewer than MIN_BASE_GAMES baseline
    games are dropped)."""
    p = pg.rename({"gi": "bgi", "gsis_id": "out_id"})
    o = _means(outs.join(bg, on=_KEY).join(p, on=["season", "team", "bgi", "out_id"]),
               [*_KEY, "out_id"], "")  # fmt: skip
    per_out = outs.join(o, on=[*_KEY, "out_id"], how="left")
    ev = per_out.group_by(_KEY).agg(
        pl.col("week").first(), pl.len().alias("n_out"),
        pl.col("out_id"), pl.col("out_pos"),
        pl.col("carry_share").alias("out_carry_share"),
        pl.col("target_share").alias("out_target_share"),
        pl.col("carry_share").sum().alias("vac_carry_share"),
        pl.col("target_share").sum().alias("vac_target_share"),
    )  # fmt: skip
    tv = pg.select("season", "team", "gi", "team_carries", "team_targets").unique()
    base = (bg.join(tv.rename({"gi": "bgi"}), on=["season", "team", "bgi"]).group_by(_KEY)
            .agg(pl.len().alias("base_games"),
                 pl.col("team_carries").mean().alias("base_team_carries"),
                 pl.col("team_targets").mean().alias("base_team_targets")))  # fmt: skip
    ev = ev.join(base, on=_KEY, how="inner").join(tv, on=_KEY, how="left")
    return ev.filter(pl.col("base_games") >= MIN_BASE_GAMES).sort(_KEY)


def role_of(position: str, rank: int, n_same_out: int) -> str:
    """A teammate's role: his position and his usage rank among the position's players
    available (the absent starters of his position come first: with the WR1 out, the next WR
    is 'WR2'); past :data:`ROLE_CAP` the pooled role '<pos>+'."""
    r = rank + n_same_out
    return f"{position}{r}" if r <= ROLE_CAP[position] else f"{position}+"


def with_roles(mates: pl.DataFrame, ev: pl.DataFrame) -> pl.DataFrame:
    """Add ``rank`` (by baseline carry + target share, then snap share, within position and
    event) and ``role`` (:func:`role_of`) to the teammate rows."""
    outs = ev.select(*_KEY, "out_pos").explode("out_pos")
    same = outs.group_by(*_KEY, pl.col("out_pos").alias("position")).agg(
        pl.len().alias("n_same_out"))  # fmt: skip
    m = mates.with_columns(
        (pl.col("base_carry_share") + pl.col("base_target_share")).alias("_use")
    ).sort(*_KEY, "position", "_use", "base_snap_share", "gsis_id",
           descending=[False, False, False, False, True, True, False])  # fmt: skip
    m = m.with_columns(pl.int_range(1, pl.len() + 1).over(*_KEY, "position").alias("rank"))
    m = m.join(same, on=[*_KEY, "position"], how="left").with_columns(
        pl.col("n_same_out").fill_null(0))  # fmt: skip
    roles = [role_of(p, int(r), int(n)) for p, r, n in
             zip(m["position"], m["rank"], m["n_same_out"], strict=True)]  # fmt: skip
    return m.with_columns(pl.Series("role", roles, dtype=pl.String)).drop("_use", "n_same_out")


def base_mates(ev: pl.DataFrame, bg: pl.DataFrame, pg: pl.DataFrame) -> pl.DataFrame:
    """One row per (event, teammate who played at least MIN_BASE_GAMES of its baseline
    games): his baseline means (``base_*``; position = his latest baseline game's) and the
    event's columns (no role yet)."""
    outs = ev.select(*_KEY, "out_id").explode("out_id")
    p = pg.filter(pl.col("played"))
    base = bg.join(p.rename({"gi": "bgi"}), on=["season", "team", "bgi"])
    base = base.join(outs.rename({"out_id": "gsis_id"}), on=[*_KEY, "gsis_id"], how="anti")
    b = _means(base, [*_KEY, "gsis_id"], "base_")
    pos = base.sort("bgi").group_by(*_KEY, "gsis_id").agg(pl.col("position").last())
    b = b.join(pos, on=[*_KEY, "gsis_id"]).filter(pl.col("base_games") >= MIN_BASE_GAMES)
    return b.join(ev.drop("base_games"), on=_KEY, how="inner")


ACT = ("carry_share", "target_share", "snap_share", "points", "carries", "targets")


def teammates(ev: pl.DataFrame, bg: pl.DataFrame, pg: pl.DataFrame) -> pl.DataFrame:
    """:func:`base_mates` of the teammates who played in the game, with their game values
    (``act_*``) and roles (ranked among them)."""
    game = pg.filter(pl.col("played")).select(
        *_KEY, "gsis_id", *[pl.col(c).alias(f"act_{c}") for c in ACT]
    )
    m = base_mates(ev, bg, pg).join(game, on=[*_KEY, "gsis_id"], how="inner")
    return with_roles(m, ev).sort(*_KEY, "position", "rank")
