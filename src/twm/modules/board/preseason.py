"""The board's PRESEASON snapshot (step I2a, PROJECT_SPEC 8.6 / step I2).

Same rows and labels as the end-of-season snapshot of S (the season just completed), read later,
at :func:`preseason_as_of`: by default (config ``as_of.board.preseason``; the owner's I2
board decision) one hour before the first regular-season week-1 kickoff of S+1, or the alternative
anchor, spec 6.1's Tuesday 14:00 UTC (config ``as_of.weekly``) before that kickoff
(:data:`ANCHORS`). Every I1b feature is
recomputed through an :class:`~twm.asof.AsOfView` at that moment, and the features below exist
only now (docs/board.md "Preseason snapshot" defines each):

- the week-1 depth charts of S+1 visible at the as-of (:func:`week1_chart`): legacy weekly
  charts (week 1, REG; public the Wednesday before week 1, so NOT at the Tuesday anchor) or, 2025
  on, each team's latest daily pull;
- ``dc_absent``: on no team's chart although his team's chart is visible (flagged, never dropped;
  every other feature below is then NULL); ``dc_team_chart_missing``: on no chart and his team
  has no visible chart (``dc_absent`` is then NULL: a missing chart is not a cut);
  ``team_change_s1``: his week-1 team differs from his S team;
- ``depth_rank_s1``: his best ``depth_rank`` among his offense slots of his position's group
  (:data:`GROUPS`) on that chart;
- ``new_competitor_s1``: a teammate of the same group listed at his depth rank or ahead who was on
  no regular-season weekly roster of that team in S (a rookie or an arrival);
- ``qb1_change_s1``: his team's S primary starter (most regular-season pass attempts for the
  team in S) is not among the team's best-ranked QBs on the chart;
- ``vacated_targets_share_s1`` / ``vacated_carries_share_s1``: the team's S regular-season
  targets / carries by players absent from its week-1 chart, over the team's S total;
- ``hc_change_s1``: his week-1 team has a head-coach departure after S announced before the
  as-of's date (blank dates: the I1b rule, :func:`features.hc_departure_teams`).
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from twm.modules.board import features as bf
from twm.modules.board.seasons import SeasonNotOverError

# Anchors of the preseason as-of (config ``as_of.board.preseason``; the default is the config's):
# ``week1_kickoff_eve`` = KICKOFF_EVE_LEAD before the first regular-season week-1 kickoff of S+1
# (the step-I2a as-of; the board's anchor, the owner's I2 decision); ``tuesday_before_week_1``
# = spec 6.1, the last ``as_of.weekly`` weekday/time (Tuesday 14:00 UTC) strictly before that
# kickoff (kept as the alternative: no week-1 chart is public then before 2024).
ANCHORS = ("tuesday_before_week_1", "week1_kickoff_eve")
KICKOFF_EVE_LEAD = timedelta(hours=1)
# reports/board/<prefix>{cliff,breakout}.* and data/board/dataset_<prefix>.parquet of each anchor
REPORT_PREFIX = {
    "week1_kickoff_eve": "preseason_", "tuesday_before_week_1": "preseason_tuesday_",
}  # fmt: skip
GROUPS = {"QB": ("QB",), "RB": ("RB", "HB"), "WR": ("WR",), "TE": ("TE",)}
NEW_FEATURES: tuple[str, ...] = (
    "dc_absent", "dc_team_chart_missing", "team_change_s1", "depth_rank_s1", "new_competitor_s1",
    "qb1_change_s1", "vacated_targets_share_s1", "vacated_carries_share_s1", "hc_change_s1",
)  # fmt: skip


def default_anchor() -> str:
    """The config's preseason anchor (``as_of.board.preseason``)."""
    from twm.config import settings

    return settings().as_of.board.preseason


def weekday_time_before(moment: datetime, weekday: int, at: time) -> datetime:
    """The last ``weekday`` (Monday = 0) at ``at`` (UTC) strictly before ``moment`` (aware UTC)."""
    day = moment.astimezone(UTC).date()
    for back in range(8):
        d = day - timedelta(days=back)
        cand = datetime.combine(d, at, UTC)
        if d.weekday() == weekday and cand < moment:
            return cand
    raise AssertionError("unreachable: a weekday recurs within 8 days")


def first_week1_kickoff(db: Path | str | duckdb.DuckDBPyConnection, season: int) -> datetime:
    """The first regular-season week-1 kickoff of ``season`` + 1 (aware UTC)."""
    con = db if isinstance(db, duckdb.DuckDBPyConnection) else duckdb.connect(str(db), True)
    try:
        row = con.execute(
            "SELECT min(kickoff_utc) FROM fact_game WHERE season = ? AND game_type = 'REG' "
            "AND week = 1",
            [int(season) + 1],
        ).fetchone()
    finally:
        if not isinstance(db, duckdb.DuckDBPyConnection):
            con.close()
    if not row or row[0] is None:
        raise SeasonNotOverError(f"no week-1 kickoff of {int(season) + 1} in fact_game")
    return row[0].replace(tzinfo=UTC)


def preseason_as_of(
    db: Path | str | duckdb.DuckDBPyConnection, season: int, anchor: str | None = None
) -> datetime:
    """The preseason as-of of snapshot ``season`` at ``anchor`` (default: the config's; see
    :data:`ANCHORS`), aware UTC. The weekday and time come from config ``as_of.weekly``."""
    from twm.config import settings
    from twm.warehouse.weeks import AsOfRules

    anchor = anchor or default_anchor()
    kickoff = first_week1_kickoff(db, season)
    if anchor == "week1_kickoff_eve":
        return kickoff - KICKOFF_EVE_LEAD
    if anchor == "tuesday_before_week_1":
        r = AsOfRules.from_config(settings().as_of)
        return weekday_time_before(kickoff, r.weekly_weekday, r.weekly_time)
    raise ValueError(f"unknown preseason anchor {anchor!r}; known: {ANCHORS}")


def week1_chart(view: Any, next_season: int) -> pl.DataFrame:
    """(team, gsis_id, unit, position, pos_slot, depth_rank) of the week-1 depth charts of
    ``next_season`` visible in ``view``: per team its daily pull with the latest ``dt`` when the
    season has daily rows (the chart in force at the as-of), else the legacy week-1 REG chart. A
    team with no visible chart has no row (:func:`chart_features` then flags its players
    ``dc_team_chart_missing``). ``depth_rank`` = 1 for the starter of each slot: legacy ranks are
    per slot already (two or three WRs share rank 1); a daily pull numbers a position's players
    across its slots (WR 1 .. 15), so its rank is renumbered within the slot (``pos_slot``) in the
    pull's order."""
    s1 = int(next_season)
    return view.sql(f"""
        WITH d AS (SELECT team, max(dt) AS dt FROM fact_depth_chart
                   WHERE season = {s1} AND source_format = 'daily' AND team IS NOT NULL
                   GROUP BY team),
             n AS (SELECT count(*) AS n_daily FROM d)
        SELECT c.team, c.gsis_id, c.unit, c.position, c.pos_slot,
               CASE WHEN c.source_format = 'daily' THEN CAST(row_number() OVER (
                   PARTITION BY c.team, c.unit, c.position, c.pos_slot
                   ORDER BY c.depth_rank, c.gsis_id) AS INTEGER)
               ELSE c.depth_rank END AS depth_rank
        FROM fact_depth_chart c CROSS JOIN n LEFT JOIN d ON c.team = d.team
        WHERE c.season = {s1} AND c.gsis_id IS NOT NULL AND c.team IS NOT NULL
          AND ((c.source_format = 'daily' AND c.dt = d.dt)
            OR (n.n_daily = 0 AND c.source_format = 'legacy' AND c.week = 1
                AND c.game_type = 'REG'))
        ORDER BY c.team, c.unit, c.position, c.depth_rank, c.gsis_id""")


def team_facts(view: Any, season: int) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Season S, regular season: (team, gsis_id) on any weekly roster; per (team, gsis_id) the
    targets and carries of his stat lines; per team the primary starter (most pass attempts;
    ties: the lower id)."""
    s = int(season)
    rosters = view.sql(f"""
        SELECT DISTINCT r.team, r.gsis_id FROM fact_roster_week r
        WHERE r.season = {s} AND r.season_type = 'REG' AND r.gsis_id IS NOT NULL
          AND r.team IS NOT NULL""")
    usage = view.sql(f"""
        SELECT team, player_id AS gsis_id, CAST(sum(COALESCE(targets, 0)) AS DOUBLE) AS targets,
               CAST(sum(COALESCE(carries, 0)) AS DOUBLE) AS carries,
               CAST(sum(COALESCE(attempts, 0)) AS DOUBLE) AS attempts
        FROM fact_player_week
        WHERE season = {s} AND season_type = 'REG' AND player_id IS NOT NULL AND team IS NOT NULL
        GROUP BY ALL""")
    qb = (
        usage.filter(pl.col("attempts") > 0)
        .sort(["team", "attempts", "gsis_id"], descending=[False, True, False])
        .group_by("team", maintain_order=True)
        .first()
        .select("team", pl.col("gsis_id").alias("qb_s"))
    )
    return rosters, usage.drop("attempts"), qb


def _group_rows(chart: pl.DataFrame) -> pl.DataFrame:
    """Offense rows of the chart with their position group; (team, gsis_id, grp) -> best rank."""
    m = {slot: g for g, slots in GROUPS.items() for slot in slots}
    return (
        chart.filter(pl.col("unit") == "offense", pl.col("position").is_in(list(m)))
        .with_columns(pl.col("position").replace_strict(m).alias("grp"))
        .group_by("team", "gsis_id", "grp")
        .agg(pl.col("depth_rank").min().alias("rank"))
    )


def team_level(chart: pl.DataFrame, usage: pl.DataFrame, qb: pl.DataFrame) -> pl.DataFrame:
    """Per chart team: ``qb1_change_s1`` and the vacated targets / carries shares."""
    present = chart.select("team", "gsis_id").unique()
    tot = usage.group_by("team").agg(pl.col("targets").sum().alias("t_tot"),
                                     pl.col("carries").sum().alias("c_tot"))  # fmt: skip
    gone = (
        usage.join(present, on=["team", "gsis_id"], how="anti")
        .group_by("team")
        .agg(pl.col("targets").sum().alias("t_gone"), pl.col("carries").sum().alias("c_gone"))
    )
    g = _group_rows(chart).filter(pl.col("grp") == "QB")
    qb1 = g.filter(pl.col("rank") == pl.col("rank").min().over("team"))
    qb1 = qb1.group_by("team").agg(pl.col("gsis_id").alias("qb1"))
    out = (
        present.select("team").unique()
        .join(tot, on="team", how="left").join(gone, on="team", how="left")
        .join(qb, on="team", how="left").join(qb1, on="team", how="left")
    )  # fmt: skip
    return out.select(
        "team",
        pl.when(pl.col("qb_s").is_null() | pl.col("qb1").is_null())
        .then(None)
        .otherwise(~pl.col("qb1").list.contains(pl.col("qb_s")))
        .cast(pl.Float64)
        .alias("qb1_change_s1"),
        (pl.col("t_gone").fill_null(0) / pl.col("t_tot")).alias("vacated_targets_share_s1"),
        (pl.col("c_gone").fill_null(0) / pl.col("c_tot")).alias("vacated_carries_share_s1"),
    )


def chart_features(
    rows: pl.DataFrame, chart: pl.DataFrame, rosters: pl.DataFrame, teams: pl.DataFrame,
    hc: set[str],
) -> pl.DataFrame:  # fmt: skip
    """``rows`` (gsis_id, position, team = his S team) + :data:`NEW_FEATURES`."""
    c = pl.col
    on = chart.join(_group_rows(chart).select("team", "gsis_id", c("rank").alias("_r")),
                    on=["team", "gsis_id"], how="left")  # fmt: skip
    # his week-1 team: the team where he is listed best (an offense rank first), ties: the team
    home = (
        on.sort(["gsis_id", "_r", "depth_rank", "team"], nulls_last=True)
        .group_by("gsis_id", maintain_order=True)
        .first()
        .select("gsis_id", c("team").alias("team_s1"))
    )
    grp = _group_rows(chart).rename({"team": "team_s1", "grp": "position"})
    df = rows.join(home, on="gsis_id", how="left").join(
        grp.rename({"rank": "depth_rank_s1"}), on=["team_s1", "gsis_id", "position"], how="left"
    )
    # same-group teammates at his rank or ahead who were on none of that team's S rosters
    new = grp.join(rosters.rename({"team": "team_s1"}), on=["team_s1", "gsis_id"], how="anti")
    pairs = df.select("gsis_id", "team_s1", "position", "depth_rank_s1").join(
        new.rename({"gsis_id": "_other", "rank": "_orank"}), on=["team_s1", "position"]
    )
    comp = (
        pairs.filter(c("_other") != c("gsis_id"), c("_orank") <= c("depth_rank_s1"))
        .select("gsis_id").unique().with_columns(pl.lit(1.0).alias("_comp"))
    )  # fmt: skip
    absent = c("team_s1").is_null()
    # his team's chart is visible: his S team's (his S+1 team is unknown when he is on no chart;
    # no S team: any team's). Not on a chart AND no chart of his team -> missing, never absent.
    charted = sorted(chart.get_column("team").unique().to_list())
    has_chart = (
        pl.when(c("team").is_null()).then(pl.lit(bool(charted))).otherwise(c("team").is_in(charted))
    )
    df = df.join(comp, on="gsis_id", how="left").join(
        teams.rename({"team": "team_s1"}), on="team_s1", how="left"
    )
    out = df.with_columns(
        pl.when(~absent).then(False).when(has_chart).then(True).otherwise(None)
        .alias("dc_absent"),
        (absent & ~has_chart).alias("dc_team_chart_missing"),
        pl.when(absent).then(None).otherwise((c("team_s1") != c("team")).cast(pl.Float64))
        .alias("team_change_s1"),
        c("depth_rank_s1").cast(pl.Float64),
        pl.when(c("depth_rank_s1").is_null()).then(None).otherwise(c("_comp").fill_null(0.0))
        .alias("new_competitor_s1"),
        pl.when(absent).then(None).otherwise(c("team_s1").is_in(sorted(hc)).cast(pl.Float64))
        .alias("hc_change_s1"),
    )  # fmt: skip
    return out.drop("_comp", "team_s1")


def preseason_features(
    view: Any, season: int, *, xfp_games: pl.DataFrame | None, departures: bf.Departures
) -> pl.DataFrame:
    """Every I1b feature of ``season`` read through ``view`` (the preseason as-of of S+1) and the
    :data:`NEW_FEATURES`; same rows as :func:`features.snapshot_features`."""
    s = int(season)
    base = bf.snapshot_features(view, s, xfp_games=xfp_games, departures=departures)
    chart = week1_chart(view, s + 1)
    rosters, usage, qb = team_facts(view, s)
    teams = team_level(chart, usage, qb)
    hc = bf.hc_departure_teams(departures, s, view.as_of)
    new = chart_features(base.select("gsis_id", "position", "team"), chart, rosters, teams, hc)
    floats = [n for n in NEW_FEATURES if new.schema[n] == pl.Float64]
    new = new.select("gsis_id", *NEW_FEATURES).with_columns(pl.col(floats).round(6))
    return base.join(new, on="gsis_id", how="left").sort("season", "gsis_id")
