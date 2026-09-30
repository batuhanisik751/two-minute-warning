"""Regression Watch step D3: the rest-of-season projection (PROJECT_SPEC 8.2).

    PPG_ros = recency-weighted xFP/game + shrink(FPOE/game)

- **Universe** (:func:`player_state`): at a week's as-of, the QBs, RBs, WRs and TEs with at least
  ``MIN_GAMES`` games so far whose points per game (PPG) or xFP per game ranks inside teams x
  (the starting slots the position can fill, FLEX included) x the candidate-pool multiplier
  (config/league.yaml; 12 teams: QB 18, RB 54, WR 54, TE 36).
- **Shrinkage** (:class:`Priors`): D2's r(g) by position (``stability.shrinkage``) estimated
  ONLY on seasons before the projected one, plus the position's average FPOE/game.
- **Variants** (:data:`VARIANTS`): shrink toward 0 (the spec formula) or toward the position's
  average FPOE; the recency half-life (in games; None = a plain average); garbage time kept
  (``all``), left out of the efficiency only (``ng_fpoe``), or left out of both parts
  (``ng``: the no-garbage xFP is scaled back up by the position's ratio xFP / xFP without
  garbage time, from the same earlier seasons, because the actual rest-of-season points keep
  garbage time). The backtest picks one per season on earlier seasons only.

A "game" is a row of the D1 frame (a stat line or a target, carry or pass); a game without an
opportunity counts with xFP 0, so PPG = xFP/game + FPOE/game exactly. r(g) uses g = games
with an opportunity, like D2.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import polars as pl

from twm.config import FANTASY_POSITIONS, League, round_half_up

FIRST_DATA_SEASON = 2009  # receivers' xFP is not comparable before 2009 (docs/regression_watch.md)
MIN_GAMES = 3  # games so far to enter the universe (and rest-of-season games to be graded)
LAST_N = 3  # the "last 3 games" baseline
HALF_LIVES: tuple[int | None, ...] = (None, 8, 4, 2)  # recency half-life grid, in games
TARGETS = ("zero", "mean")
GARBAGE = ("all", "ng_fpoe", "ng")


@dataclass(frozen=True)
class Variant:
    """One way to project: shrink ``target`` ("zero" | "mean"), recency ``half_life`` in games
    (None: every game weighs the same), ``garbage`` ("all" | "ng_fpoe" | "ng")."""

    target: str
    half_life: int | None
    garbage: str

    @property
    def name(self) -> str:
        hl = "flat" if self.half_life is None else f"hl{self.half_life}"
        return f"{self.target}_{hl}_{self.garbage}"

    @property
    def metric(self) -> str:
        """The shrinkage metric: FPOE/game with or without garbage time."""
        return "fpoe" if self.garbage == "all" else "fpoe_ng"

    def describe(self) -> str:
        toward = "0" if self.target == "zero" else "the position's average"
        hl = "no recency weight" if self.half_life is None else f"half-life {self.half_life} games"
        gt = {"all": "garbage time kept", "ng_fpoe": "efficiency without garbage time",
              "ng": "both parts without garbage time"}[self.garbage]  # fmt: skip
        return f"shrink toward {toward}, {hl}, {gt}"


# Grid order is the tie-break: the spec formula (toward 0, garbage time kept) first.
VARIANTS: tuple[Variant, ...] = tuple(
    Variant(t, hl, gt) for t in TARGETS for gt in GARBAGE for hl in HALF_LIVES
)
SPEC_VARIANT = VARIANTS[0]
VARIANT_BY_NAME = {v.name: v for v in VARIANTS}


def universe_sizes(league: League) -> dict[str, int]:
    """Per position: teams x (dedicated + multi-position slots it can fill) x the pool
    multiplier, rounded half up (12 teams, 1 QB / 2 RB / 2 WR / 1 TE / 1 FLEX, x 1.5: QB 18,
    RB 54, WR 54, TE 36)."""
    shape = league.shape
    return {
        p: round_half_up(
            shape.teams * (shape.dedicated.get(p, 0) + shape.flex_slots.get(p, 0))
            * shape.multiplier
        )
        for p in FANTASY_POSITIONS
    }  # fmt: skip


def hl_suffix(half_life: int | None) -> str:
    return "flat" if half_life is None else f"hl{half_life}"


def _recency(value: str, hl: int | None) -> pl.Expr:
    """Recency-weighted mean of ``value`` over a player's games: the latest game weighs 1, a
    game k games earlier 0.5 ** (k / half-life) (None: a plain mean)."""
    if hl is None:
        return pl.col(value).mean()
    w = pl.lit(0.5).pow(pl.col("_back").cast(pl.Float64) / hl)
    return (w * pl.col(value)).sum() / w.sum()


def _rank(df: pl.DataFrame, value: str, name: str) -> pl.DataFrame:
    """1 = highest ``value`` within the position; ties by gsis_id (deterministic)."""
    return df.sort(["position", value, "gsis_id"], descending=[False, True, False]).with_columns(
        pl.int_range(1, pl.len() + 1).over("position").alias(name)
    )


STATE_ROUND = 6  # per-game values are rounded so runs agree to the bit


def player_state(
    std: pl.DataFrame,
    league: League,
    *,
    half_lives: Sequence[int | None] = HALF_LIVES,
    min_games: int = MIN_GAMES,
) -> pl.DataFrame:
    """One row per player with at least ``min_games`` games in ``std`` (ONE season's D1 frame
    rows public at the as-of): position (his latest game's), games, games with an opportunity
    (``g_opp``), ``ppg``, ``xfp_pg``, ``fpoe_pg`` (= ppg - xfp_pg), their no-garbage-time
    versions, ``last3_ppg``, recency-weighted ``rw_xfp_<hl>`` / ``rw_xfp_ng_<hl>`` per
    half-life, ``ppg_rank`` / ``xfp_rank`` within the position and ``in_universe`` (either
    rank inside :func:`universe_sizes`)."""
    if std.get_column("season").n_unique() > 1:
        raise ValueError("player_state works on one season at a time")
    games = std.sort(["gsis_id", "week"]).with_columns(
        pl.col("xfp").fill_null(0.0).alias("_xfp"),
        pl.col("xfp_ng").fill_null(0.0).alias("_xfp_ng"),
        (pl.len().over("gsis_id") - pl.int_range(1, pl.len() + 1).over("gsis_id")).alias("_back"),
    )
    rw = [
        _recency(src, hl).alias(f"rw_{src[1:]}_{hl_suffix(hl)}")
        for hl in half_lives
        for src in ("_xfp", "_xfp_ng")
    ]
    agg = games.group_by("gsis_id").agg(
        pl.col("season").first(),
        pl.col("position").last(),
        pl.col("team").last(),
        pl.col("week").max().alias("last_week"),
        pl.len().alias("games"),
        pl.col("xfp").is_not_null().sum().alias("g_opp"),
        pl.col("fantasy_points").mean().alias("ppg"),
        pl.col("_xfp").mean().alias("xfp_pg"),
        pl.col("points_ng").mean().alias("ppg_ng"),
        pl.col("_xfp_ng").mean().alias("xfp_ng_pg"),
        pl.col("fantasy_points").tail(LAST_N).mean().alias("last3_ppg"),
        *rw,
    )
    per_game = [c for c in agg.columns if c.endswith("_pg") or c.startswith(("rw_", "ppg"))]
    agg = (
        agg.filter(pl.col("games") >= min_games)
        .with_columns(pl.col(c).round(STATE_ROUND) for c in [*per_game, "last3_ppg"])
        .with_columns(
            (pl.col("ppg") - pl.col("xfp_pg")).round(STATE_ROUND).alias("fpoe_pg"),
            (pl.col("ppg_ng") - pl.col("xfp_ng_pg")).round(STATE_ROUND).alias("fpoe_ng_pg"),
        )
    )
    sizes = universe_sizes(league)
    ranked = _rank(_rank(agg, "ppg", "ppg_rank"), "xfp_pg", "xfp_rank")
    size = pl.col("position").replace_strict(sizes, default=0, return_dtype=pl.Int64)
    return ranked.with_columns(
        ((pl.col("ppg_rank") <= size) | (pl.col("xfp_rank") <= size)).alias("in_universe")
    ).sort(["position", "ppg_rank"])


# --------------------------------------------------------------------------------------
# Shrinkage estimated on earlier seasons, and the projection
# --------------------------------------------------------------------------------------

MAX_G = 20  # r(g) lookup rows (a season has at most 17 games; larger g are computed too)


@dataclass(frozen=True)
class Priors:
    """What a projection of season S may know from before S: D2's shrinkage table estimated on
    ``seasons`` (all < S), and per position the ratio xFP / xFP without garbage time
    (``ng_scale``, for the ``ng`` variants)."""

    seasons: tuple[int, ...]
    table: pl.DataFrame
    ng_scale: dict[str, float]

    def factor(self, position: str, g: float, metric: str = "fpoe") -> float:
        from twm.modules.regression_watch.stability import shrinkage_factor

        return shrinkage_factor(self.table, position, g, metric)

    def mean(self, position: str, metric: str = "fpoe") -> float:
        row = self.table.filter((pl.col("position") == position) & (pl.col("metric") == metric))
        v = row.get_column("prior_mean").item(0) if row.height else None
        return float(v) if v is not None else 0.0

    def lookup(self) -> pl.DataFrame:
        """(position, g_opp) -> r_fpoe, r_fpoe_ng, mean_fpoe, mean_fpoe_ng, ng_scale."""
        rows = [
            (p, g, self.factor(p, g, "fpoe"), self.factor(p, g, "fpoe_ng"), self.mean(p, "fpoe"),
             self.mean(p, "fpoe_ng"), self.ng_scale.get(p, 1.0))
            for p in FANTASY_POSITIONS for g in range(MAX_G + 1)
        ]  # fmt: skip
        schema = {"position": pl.String(), "g_opp": pl.UInt32(), "r_fpoe": pl.Float64(),
                  "r_fpoe_ng": pl.Float64(), "mean_fpoe": pl.Float64(),
                  "mean_fpoe_ng": pl.Float64(), "ng_scale": pl.Float64()}  # fmt: skip
        return pl.DataFrame(rows, schema=schema, orient="row")


def estimate_priors(frame: pl.DataFrame, seasons: Sequence[int]) -> Priors:
    """:class:`Priors` from the D1 ``frame`` rows of ``seasons`` ONLY (a later season can never
    change them): ``stability.shrinkage(seasons, frame=frame)`` and the garbage-time scale."""
    from twm.modules.regression_watch.stability import shrinkage

    wanted = tuple(sorted({int(s) for s in seasons}))
    table = shrinkage(wanted, frame=frame)
    sums = (
        frame.filter(pl.col("season").is_in(wanted) & pl.col("xfp").is_not_null())
        .group_by("position")
        .agg(pl.col("xfp").sum(), pl.col("xfp_ng").sum())
        .sort("position")
    )
    scale = {
        p: (x / n if n and n > 0 else 1.0)
        for p, x, n in sums.select("position", "xfp", "xfp_ng").iter_rows()
    }
    return Priors(wanted, table, {p: round(float(v), STATE_ROUND) for p, v in scale.items()})


def with_priors(state: pl.DataFrame, priors: Priors) -> pl.DataFrame:
    """``state`` plus its shrinkage factors, prior means and garbage-time scale."""
    return state.join(priors.lookup(), on=["position", "g_opp"], how="left")


def projection_expr(variant: Variant) -> pl.Expr:
    """PPG_ros of ``variant`` on a :func:`with_priors` state: the (recency-weighted) xFP/game
    plus the shrunk FPOE/game."""
    hl = hl_suffix(variant.half_life)
    if variant.garbage == "ng":
        xfp = pl.col(f"rw_xfp_ng_{hl}") * pl.col("ng_scale")
    else:
        xfp = pl.col(f"rw_xfp_{hl}")
    m = variant.metric
    fpoe, r = pl.col(f"{m}_pg"), pl.col(f"r_{m}")
    if variant.target == "zero":
        eff = r * fpoe
    else:
        prior = pl.col(f"mean_{m}")
        eff = prior + r * (fpoe - prior)
    return (xfp + eff).round(STATE_ROUND)


def project(state: pl.DataFrame, priors: Priors, variant: Variant) -> pl.DataFrame:
    """The state with ``ppg_ros`` (and the prior columns used)."""
    return with_priors(state, priors).with_columns(projection_expr(variant).alias("ppg_ros"))
