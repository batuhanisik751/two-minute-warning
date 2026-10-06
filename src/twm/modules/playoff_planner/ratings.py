"""Matchup ratings (docs/playoff_planner.md, "Matchup ratings").

For a position P and a team X, from X's games through a week: the points units at P scored
against X per game, over the league's average per team-game at P (``lg_ppg``). 1.0 = an
average matchup; 1.20 = units at P scored 20% more than average against X. For QB / RB / WR /
TE / K, X is the defense faced; for D/ST, X is the offense faced (the D/ST points it gives up).

- ``raw``: allowed / (games x lg_ppg) (NULL before X has played);
- ``shrunk``: (allowed + k x lg_ppg) / ((games + k) x lg_ppg), k = :data:`PSEUDO_GAMES` of the
  position (pseudo-games at exactly 1.0);
- ``adjusted`` (schedule-adjusted): (allowed + k x lg_ppg) / (expected + k x lg_ppg), where
  ``expected`` sums, over X's games, what the units it faced usually score (their own mean at P
  in their other games, shrunk to lg_ppg with k games).
"""

from __future__ import annotations

import polars as pl

from twm.modules.playoff_planner import history as hs

CANDIDATES = ("none", "raw", "shrunk", "adjusted")  # simplest first (the rule's order)
# Pseudo-games per position: sigma2_within / sigma2_between of a team's per-game points allowed
# (over the season's league average), estimated once on 2006-2012, before every test season
# (docs/playoff_planner.md): DST 7.6, RB 11.8, K 26.7, QB 33.4, WR 52.4; TE's between-team
# variance was not above zero (estimate -0.00008): capped at :data:`MAX_PSEUDO`.
MAX_PSEUDO = 100.0
PSEUDO_GAMES: dict[str, float] = {"QB": 33.0, "RB": 12.0, "WR": 52.0, "TE": MAX_PSEUDO,
                                  "K": 27.0, "DST": 8.0}  # fmt: skip
_G = ["season", "week", "game_id", "team", "opponent", "position"]


def unit_totals(units: pl.DataFrame, team_games: pl.DataFrame) -> pl.DataFrame:
    """One row per (team-game, position): ``points`` = what the team's units at the position
    scored against ``opponent`` (0 when none scored)."""
    tot = units.group_by(_G).agg(pl.col("points").sum())
    pos = pl.DataFrame({"position": list(hs.POSITIONS)})
    full = team_games.select(_G[:-1]).unique().join(pos, how="cross")
    out = full.join(tot, on=_G, how="left").with_columns(pl.col("points").fill_null(0.0))
    return out.sort(_G)


def ratings(
    totals: pl.DataFrame, through_week: int, pseudo: dict[str, float] | None = None
) -> pl.DataFrame:
    """Per (season, position, team X) from the games of weeks <= ``through_week``: games,
    allowed, expected, lg_ppg and the ratings raw / shrunk / adjusted (module docstring).
    Every team with a game in ``totals``' seasons gets a row (0 games: 1.0, raw NULL)."""
    k = pseudo if pseudo is not None else PSEUDO_GAMES
    kk = pl.col("position").replace_strict(k, default=0.0, return_dtype=pl.Float64)
    w = totals.filter(pl.col("week") <= through_week)
    lg = w.group_by("season", "position").agg(pl.col("points").mean().alias("lg_ppg"))
    off = w.group_by("season", "position", "team").agg(
        pl.col("points").sum().alias("_osum"), pl.len().alias("_on"))  # fmt: skip
    w = w.join(lg, on=["season", "position"]).join(off, on=["season", "position", "team"])
    w = w.with_columns(  # what the faced unit usually scores (leave this game out, shrunk)
        ((pl.col("_osum") - pl.col("points") + kk * pl.col("lg_ppg"))
         / (pl.col("_on") - 1 + kk)).fill_nan(None).alias("_exp"))  # fmt: skip
    w = w.with_columns(pl.col("_exp").fill_null(pl.col("lg_ppg")))
    r = w.group_by("season", "position", pl.col("opponent").alias("team")).agg(
        pl.len().alias("games"), pl.col("points").sum().alias("allowed"),
        pl.col("_exp").sum().alias("expected"), pl.col("lg_ppg").first())  # fmt: skip
    teams = totals.select("season", "team").unique().join(
        pl.DataFrame({"position": list(hs.POSITIONS)}), how="cross")  # fmt: skip
    r = teams.join(r, on=["season", "position", "team"], how="left").join(
        lg, on=["season", "position"], how="left", suffix="_lg")  # fmt: skip
    r = r.with_columns(
        pl.col("games").fill_null(0).cast(pl.Int32), pl.col("allowed").fill_null(0.0),
        pl.col("expected").fill_null(0.0), pl.coalesce("lg_ppg", "lg_ppg_lg").alias("lg_ppg"),
    ).drop("lg_ppg_lg").with_columns(kk.alias("_k"))  # fmt: skip
    lgc, a, g, kc = pl.col("lg_ppg"), pl.col("allowed"), pl.col("games"), pl.col("_k")
    ok = lgc.is_not_null() & (lgc > 0)
    r = r.with_columns(
        pl.when(ok & (g > 0)).then(a / (g * lgc)).alias("raw"),
        pl.when(ok & (g + kc > 0)).then((a + kc * lgc) / ((g + kc) * lgc)).otherwise(1.0)
        .alias("shrunk"),
        pl.when(ok & (pl.col("expected") + kc * lgc > 0))
        .then((a + kc * lgc) / (pl.col("expected") + kc * lgc)).otherwise(1.0).alias("adjusted"),
    ).drop("_k")  # fmt: skip
    return r.with_columns(pl.lit(int(through_week)).alias("through_week")).sort(
        "season", "position", "team")  # fmt: skip


def multiplier(candidate: str) -> pl.Expr:
    """The candidate's multiplier column expression (``none``: 1.0; a NULL raw: 1.0)."""
    if candidate == "none":
        return pl.lit(1.0)
    if candidate not in CANDIDATES:
        raise ValueError(f"unknown candidate {candidate!r}: {CANDIDATES}")
    return pl.col(candidate).fill_null(1.0)
