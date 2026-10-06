"""Is it the coach or the team? Year-over-year persistence of each tendency.

Unit: each completed team-season's MAIN coach row (most offensive snaps that season, ties by
coach_id) with at least ``MIN_PLAYS_PAIR`` snaps. Values are season-relative (the coach's value
minus the league's that season), so league-wide trends (shotgun 10% -> 60%) cannot pass for
persistence. Three kinds of pairs (season A -> season B):

- ``same_coach_same_team``: the team's main coach is the same in seasons s and s+1;
- ``same_coach_new_team``: a coach's next main-coach season (any gap) is with another team;
- ``new_coach_same_team``: the team's main coach changed between s and s+1.

If a tendency belongs to the coach, ``same_coach_new_team`` should look like
``same_coach_same_team`` and ``new_coach_same_team`` should be lower. Each Pearson r has a 95%
interval from a season-block bootstrap: season A's are resampled with replacement and every
pair of a drawn season comes along (pairs of one season share league conditions), ``N_BOOT``
draws with a fixed seed, so the build is deterministic.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from twm.modules.coach_tendencies.season import METRICS

COMPARISONS = ("same_coach_same_team", "same_coach_new_team", "new_coach_same_team")
MIN_PLAYS_PAIR = 400
N_BOOT = 2000
SEED = 20261005
PERSISTENCE_COLUMNS = (
    "metric", "comparison", "n_pairs", "n_seasons", "first_season", "last_season", "r",
    "ci_low", "ci_high",
)  # fmt: skip


def main_rows(wide: pl.DataFrame) -> pl.DataFrame:
    """Each completed team-season's main coach row with ``rel_<m>`` = value - league."""
    done = wide.filter(~pl.col("is_current") & (pl.col("plays") >= MIN_PLAYS_PAIR))
    main = done.sort(
        ["team", "season", "plays", "coach_id"], descending=[False, False, True, False]
    )
    main = main.unique(["team", "season"], keep="first", maintain_order=True)
    rel = [(pl.col(m) - pl.col(f"{m}_league")).alias(f"rel_{m}") for m in METRICS]
    return main.select("coach_id", "team", "season", *rel).sort("team", "season")


def pairs(main: pl.DataFrame) -> pl.DataFrame:
    """Season A -> season B pairs of :func:`main_rows`, labelled with their comparison."""
    nxt = main.with_columns(pl.col("season") - 1)
    team = main.join(nxt, on=["team", "season"], suffix="_b").with_columns(
        (pl.col("season") + 1).alias("season_b"), pl.col("team").alias("team_b")
    )
    same = pl.col("coach_id") == pl.col("coach_id_b")
    label = pl.when(same).then(pl.lit(COMPARISONS[0])).otherwise(pl.lit(COMPARISONS[2]))
    team = team.with_columns(label.alias("comparison"))
    shifted = [pl.col(c).shift(-1).over("coach_id").alias(f"{c}_b") for c in main.columns[1:]]
    moved = (
        main.sort("coach_id", "season")
        .with_columns(*shifted, pl.col("coach_id").alias("coach_id_b"))
        .filter(pl.col("team_b").is_not_null() & (pl.col("team_b") != pl.col("team")))
        .with_columns(pl.lit(COMPARISONS[1]).alias("comparison"))
    )
    cols = ["comparison", "coach_id", "coach_id_b", "team", "team_b", "season", "season_b"]
    cols += [f"rel_{m}{s}" for m in METRICS for s in ("", "_b")]
    return pl.concat([team.select(cols), moved.select(cols)]).sort(cols[:1] + cols[5:7] + cols[3:4])


def pearson(x: np.ndarray, y: np.ndarray) -> float:
    """Pearson r; NaN with fewer than 3 points or no variance."""
    if len(x) < 3:
        return float("nan")
    xc, yc = x - x.mean(), y - y.mean()
    den = float(np.sqrt((xc * xc).sum() * (yc * yc).sum()))
    return float((xc * yc).sum() / den) if den > 0 else float("nan")


def block_bootstrap(
    x: np.ndarray, y: np.ndarray, block: np.ndarray, n_boot: int = N_BOOT, seed: int = SEED
) -> tuple[float, float]:
    """95% percentile interval of Pearson r, resampling whole blocks (seasons)."""
    blocks = np.unique(block)
    members = [np.flatnonzero(block == b) for b in blocks]
    rng = np.random.default_rng(seed)
    rs = np.empty(n_boot)
    for i in range(n_boot):
        idx = np.concatenate([members[k] for k in rng.integers(0, len(blocks), len(blocks))])
        rs[i] = pearson(x[idx], y[idx])
    rs = rs[np.isfinite(rs)]
    if len(rs) == 0:
        return float("nan"), float("nan")
    lo, hi = np.quantile(rs, [0.025, 0.975])
    return float(lo), float(hi)


def persistence(pair_rows: pl.DataFrame, n_boot: int = N_BOOT, seed: int = SEED) -> pl.DataFrame:
    """The published persistence frame: one row per (metric, comparison)."""
    out = []
    for m in METRICS:
        for comp in COMPARISONS:
            p = pair_rows.filter(
                (pl.col("comparison") == comp)
                & pl.col(f"rel_{m}").is_not_null()
                & pl.col(f"rel_{m}_b").is_not_null()
            )
            x, y = p[f"rel_{m}"].to_numpy(), p[f"rel_{m}_b"].to_numpy()
            s = p["season"].to_numpy()
            lo, hi = block_bootstrap(x, y, s, n_boot, seed) if p.height else (np.nan, np.nan)
            out.append(
                {
                    "metric": m,
                    "comparison": comp,
                    "n_pairs": p.height,
                    "n_seasons": len(np.unique(s)),
                    "first_season": int(s.min()) if p.height else None,
                    "last_season": int(s.max()) if p.height else None,
                    "r": pearson(x, y),
                    "ci_low": lo,
                    "ci_high": hi,
                }
            )
    schema = {"first_season": pl.Int64, "last_season": pl.Int64}
    return pl.DataFrame(out, schema_overrides=schema).select(PERSISTENCE_COLUMNS)
