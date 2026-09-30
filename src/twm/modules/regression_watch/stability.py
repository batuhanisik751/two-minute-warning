"""The Regression Watch stability study and shrinkage estimates (step D2, PROJECT_SPEC 8.2).

Question: which parts of a player's production repeat? For every player-season with at least
``MIN_GAMES`` games (regular season, 2009 on, QB/RB/WR/TE; a game counts when he had a target,
carry or pass, i.e. an ffopportunity row), the games are split into two halves, twice:

- **odd / even**: his 1st, 3rd, 5th ... game against his 2nd, 4th, 6th ... (the main view: both
  halves measure the same season, only the luck differs);
- **first / second half**: his first ``n // 2`` games against the rest (a harder test: roles also
  change during a season).

Each half gets a value of each metric in :data:`METRICS` (per-game means of xFP, FPOE and points,
with and without garbage time; rates over expected per chance, e.g. catches minus expected
catches per target). The **split-half correlation** of a metric across player-seasons says how
much of it repeats: near 1 = sticky (skill or role), near 0 = mostly luck. Intervals come from a
bootstrap over player-seasons.

**Shrinkage (empirical Bayes).** A player's FPOE/game after ``g`` games is his true level plus
luck. Across player-seasons the covariance of the two odd/even halves estimates the variance of
the true level (``var_signal``); the variance of their difference estimates the per-game luck
(``var_noise``). The **reliability** of a ``g``-game average is
``r(g) = var_signal / (var_signal + var_noise / g)``, and D3's rest-of-season projection keeps
``r(g)`` of the observed FPOE/game (the shrinkage factor). :func:`shrinkage` estimates the table
from the given ``seasons`` only, so a walk-forward backtest for season S passes seasons < S.

docs/regression_watch.md explains every number for a beginner; ``twm regression stability``
writes reports/regression_watch/stability.md (+ .csv).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import polars as pl

from twm.config import FANTASY_POSITIONS

# Receiver xFP is unusable in 2006-2008 (targets of incomplete passes are missing upstream,
# docs/regression_watch.md); the studies start in 2009 for every position (D1 decision).
FIRST_STUDY_SEASON = 2009
MIN_GAMES = 8  # a player-season needs at least this many games with an opportunity
MIN_DENOMINATOR = 10  # a rate needs at least this many chances (targets, ...) in EACH half
MAX_G = 17  # the shrinkage table covers g = 1 .. MAX_G games (a 17-game season)
N_BOOT = 1000
SEED = 20260929
SPLITS = ("odd_even", "first_second")
RECEIVERS = ("RB", "WR", "TE")


@dataclass(frozen=True)
class Metric:
    """One studied metric: sum(plus) - sum(minus) over a half's games, divided by the half's
    games (``per`` empty) or by the sum of the ``per`` columns (a rate per chance)."""

    name: str  # the registry entry that explains it
    label: str  # short label for tables
    family: str  # "opportunity", "efficiency" or "production"
    plus: tuple[str, ...]
    minus: tuple[str, ...] = ()
    per: tuple[str, ...] = ()  # denominator columns; () = per game
    positions: tuple[str, ...] = FANTASY_POSITIONS
    garbage_time: bool = True  # False: the no-garbage-time variant

    @property
    def per_game(self) -> bool:
        return not self.per


TDS = ("passing_tds", "rushing_tds", "receiving_tds")
CHANCES = ("pass_attempts", "carries", "targets")
METRICS: tuple[Metric, ...] = (
    Metric("xfp", "xFP/game", "opportunity", ("xfp",)),
    Metric("fpoe", "FPOE/game", "efficiency", ("fpoe",)),
    Metric("fantasy_points", "points/game", "production", ("fantasy_points",)),
    Metric("xfp_ng", "xFP/game, no garbage", "opportunity", ("xfp_ng",), garbage_time=False),
    Metric("fpoe_ng", "FPOE/game, no garbage", "efficiency", ("fpoe_ng",), garbage_time=False),
    Metric("points_ng", "points/game, no garbage", "production", ("points_ng",),
           garbage_time=False),
    Metric("td_rate_over_expected", "TD rate over expected", "efficiency", TDS,
           tuple(f"{c}_exp" for c in TDS), CHANCES),
    Metric("catch_rate_over_expected", "catch rate over expected", "efficiency",
           ("receptions",), ("receptions_exp",), ("targets",), RECEIVERS),
    Metric("completion_rate_over_expected", "completion rate over expected", "efficiency",
           ("completions",), ("completions_exp",), ("pass_attempts",), ("QB",)),
    Metric("yac_over_expected", "YAC over expected per catch", "efficiency", ("yac",),
           ("yac_exp",), ("receptions",), RECEIVERS),
)  # fmt: skip
METRIC_BY_NAME = {m.name: m for m in METRICS}
SHRINKAGE_METRICS = ("fpoe", "fpoe_ng")
KEYS = ("season", "gsis_id", "position")


def needed_columns() -> list[str]:
    """The frame columns the metrics add up."""
    cols: set[str] = set()
    for m in METRICS:
        cols |= {*m.plus, *m.minus, *m.per}
    return sorted(cols)


def load_frame(db: Path | str | None, seasons: Sequence[int]) -> pl.DataFrame:
    """The D1 frame of ``seasons`` (every row with its ``available_at``), read through the
    as-of view at the end of time; ``db`` defaults to the configured warehouse."""
    from twm.config import settings
    from twm.modules.regression_watch.player_week import player_games_history

    path = Path(db) if db is not None else settings().path("warehouse")
    return player_games_history(path, sorted({int(s) for s in seasons}))


def study_games(
    frame: pl.DataFrame,
    seasons: Sequence[int] | None = None,
    *,
    min_games: int = MIN_GAMES,
    as_of: datetime | None = None,
    positions: Sequence[str] = FANTASY_POSITIONS,
) -> pl.DataFrame:
    """The games of the study, from a D1 frame: rows of ``seasons`` (all when None) public at
    ``as_of`` (all when None) with an opportunity (``xfp`` not null). A player-season gets ONE
    position, the one of most of his games (ties: his latest game's), and is kept with at least
    ``min_games`` games. Adds ``game_no`` (1 .. n by week), ``n_games`` and the half labels
    ``half_odd_even`` / ``half_first_second`` ("a" or "b")."""
    f = frame
    if as_of is not None:
        from twm.modules.regression_watch.player_week import visible

        f = visible(f, as_of)
    if seasons is not None:
        f = f.filter(pl.col("season").is_in([int(s) for s in seasons]))
    f = f.filter(pl.col("xfp").is_not_null())
    modal = (
        f.group_by("season", "gsis_id", "position")
        .agg(pl.len().alias("_n"), pl.col("week").max().alias("_last"))
        .sort(["season", "gsis_id", "_n", "_last"], descending=[False, False, True, True])
        .unique(["season", "gsis_id"], keep="first", maintain_order=True)
        .select("season", "gsis_id", pl.col("position").alias("_pos"))
    )
    player = ["season", "gsis_id"]
    return (
        f.join(modal, on=player, how="inner")
        .drop("position")
        .rename({"_pos": "position"})
        .filter(pl.col("position").is_in(list(positions)))
        .with_columns(pl.col(c).cast(pl.Float64).fill_null(0.0) for c in needed_columns())
        .sort(["season", "gsis_id", "week"])
        .with_columns(
            pl.int_range(1, pl.len() + 1).over(player).alias("game_no"),
            pl.len().over(player).alias("n_games"),
        )
        .filter(pl.col("n_games") >= min_games)
        .with_columns(
            pl.when(pl.col("game_no") % 2 == 1)
            .then(pl.lit("a"))
            .otherwise(pl.lit("b"))
            .alias("half_odd_even"),
            pl.when(pl.col("game_no") <= pl.col("n_games") // 2)
            .then(pl.lit("a"))
            .otherwise(pl.lit("b"))
            .alias("half_first_second"),
        )
    )


def halves(games: pl.DataFrame, split: str) -> pl.DataFrame:
    """One row per player-season (season, gsis_id, position): ``g_a`` / ``g_b`` games in each
    half and, per metric, ``<m>__num_a``, ``<m>__den_a`` (and ``_b``): the sum of plus minus
    minus columns, and the half's games or chances."""
    if split not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS}")
    half = f"half_{split}"
    sums = games.group_by(*KEYS, half).agg(
        pl.len().alias("g"), *[pl.col(c).sum() for c in needed_columns()]
    )
    exprs = []
    for m in METRICS:
        num = pl.sum_horizontal(*m.plus)
        if m.minus:
            num = num - pl.sum_horizontal(*m.minus)
        den = pl.col("g").cast(pl.Float64) if m.per_game else pl.sum_horizontal(*m.per)
        exprs += [num.alias(f"{m.name}__num"), den.alias(f"{m.name}__den")]
    per_half = sums.select(*KEYS, half, "g", *exprs)
    a = per_half.filter(pl.col(half) == "a").drop(half)
    b = per_half.filter(pl.col(half) == "b").drop(half)
    return (
        a.join(b, on=list(KEYS), how="inner", suffix="_b")
        .rename({c: f"{c}_a" for c in a.columns if c not in KEYS})
        .sort(list(KEYS))
    )


# --------------------------------------------------------------------------------------
# The math: split-half correlation, bootstrap, signal and noise, reliability
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Pairs:
    """A metric's two half values per player-season (and each half's games)."""

    a: np.ndarray
    b: np.ndarray
    g_a: np.ndarray
    g_b: np.ndarray
    seasons: np.ndarray

    @property
    def n(self) -> int:
        return len(self.a)


def pairs(
    table: pl.DataFrame, metric: Metric, position: str, *, min_denominator: float = MIN_DENOMINATOR
) -> Pairs:
    """The player-seasons of ``position`` (from :func:`halves`) where ``metric`` is defined in
    both halves: every one for a per-game metric; for a rate, at least ``min_denominator``
    chances in EACH half."""
    m = metric.name
    need = 1.0 if metric.per_game else float(min_denominator)
    t = table.filter(
        (pl.col("position") == position)
        & (pl.col(f"{m}__den_a") >= need)
        & (pl.col(f"{m}__den_b") >= need)
    )
    a = (t[f"{m}__num_a"] / t[f"{m}__den_a"]).to_numpy()
    b = (t[f"{m}__num_b"] / t[f"{m}__den_b"]).to_numpy()
    return Pairs(a, b, t["g_a"].to_numpy(), t["g_b"].to_numpy(), t["season"].to_numpy())


def pearson(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson correlation (NaN with fewer than 3 pairs or no spread)."""
    if len(a) < 3:
        return float("nan")
    da, db = a - a.mean(), b - b.mean()
    den = np.sqrt((da * da).sum() * (db * db).sum())
    return float((da * db).sum() / den) if den > 0 else float("nan")


def _resamples(n: int, n_boot: int, rng: np.random.Generator, chunk: int = 200):
    """Bootstrap index matrices (resampling rows with replacement), in chunks."""
    done = 0
    while done < n_boot:
        k = min(chunk, n_boot - done)
        yield rng.integers(0, n, size=(k, n))
        done += k


def bootstrap_corr(
    a: np.ndarray, b: np.ndarray, *, n_boot: int = N_BOOT, rng: np.random.Generator
) -> tuple[float, float]:
    """95% percentile interval of the correlation, resampling the pairs (player-seasons)."""
    if len(a) < 3 or n_boot <= 0:
        return float("nan"), float("nan")
    out = []
    for idx in _resamples(len(a), n_boot, rng):
        x, y = a[idx], b[idx]
        x = x - x.mean(axis=1, keepdims=True)
        y = y - y.mean(axis=1, keepdims=True)
        with np.errstate(invalid="ignore", divide="ignore"):
            out.append((x * y).sum(axis=1) / np.sqrt((x * x).sum(axis=1) * (y * y).sum(axis=1)))
    rs = np.concatenate(out)
    if np.isnan(rs).all():  # no spread in any resample (e.g. a constant metric)
        return float("nan"), float("nan")
    lo, hi = np.nanpercentile(rs, [2.5, 97.5])
    return float(lo), float(hi)


def signal_noise(p: Pairs) -> tuple[float, float]:
    """(var_signal, var_noise) of a per-game metric from its odd/even halves.

    Model: game value = the player-season's true level + independent per-game luck. The two
    half means share the true level, so their covariance across player-seasons estimates
    var_signal; their difference is pure luck with variance var_noise x (1/g_a + 1/g_b), so
    var_noise = var(a - b) / mean(1/g_a + 1/g_b). var_signal can come out negative in a small
    or pure-noise sample (then reliability is 0)."""
    if p.n < 3:
        return float("nan"), float("nan")
    var_signal = float(np.cov(p.a, p.b, ddof=1)[0, 1])
    var_noise = float(np.var(p.a - p.b, ddof=1) / np.mean(1.0 / p.g_a + 1.0 / p.g_b))
    return var_signal, var_noise


def reliability(var_signal: float, var_noise: float, g: float) -> float:
    """r(g) = var_signal / (var_signal + var_noise / g): the share of the spread of g-game
    averages that is real, i.e. the weight a g-game average deserves (0 when g = 0 or there is
    no signal). The shrinkage factor for FPOE/game after g games."""
    if not (var_signal > 0) or g <= 0:
        return 0.0
    if not (var_noise > 0):
        return 1.0
    return float(var_signal / (var_signal + var_noise / g))


# --------------------------------------------------------------------------------------
# The two tables: split-half correlations and the shrinkage table
# --------------------------------------------------------------------------------------

SPLIT_HALF_SCHEMA = {
    "split": pl.String(), "position": pl.String(), "metric": pl.String(),
    "label": pl.String(), "family": pl.String(), "garbage_time": pl.Boolean(),
    "n": pl.Int64(), "r": pl.Float64(), "lo": pl.Float64(), "hi": pl.Float64(),
}  # fmt: skip


def _rng(seed: int, *keys: int) -> np.random.Generator:
    return np.random.default_rng([seed, *keys])


def split_half(
    frame: pl.DataFrame,
    seasons: Sequence[int] | None = None,
    *,
    n_boot: int = N_BOOT,
    seed: int = SEED,
    min_games: int = MIN_GAMES,
    min_denominator: float = MIN_DENOMINATOR,
    as_of: datetime | None = None,
) -> pl.DataFrame:
    """Split-half correlation of every metric, by split and position: ``n`` player-seasons,
    ``r`` and its 95% bootstrap interval ``lo`` .. ``hi`` (resampling player-seasons; each
    (split, position, metric) cell has its own seeded generator, so a cell never depends on
    the others)."""
    games = study_games(frame, seasons, min_games=min_games, as_of=as_of)
    rows = []
    for si, split in enumerate(SPLITS):
        table = halves(games, split)
        for pi, pos in enumerate(FANTASY_POSITIONS):
            for mi, m in enumerate(METRICS):
                if pos not in m.positions:
                    continue
                p = pairs(table, m, pos, min_denominator=min_denominator)
                lo, hi = bootstrap_corr(p.a, p.b, n_boot=n_boot, rng=_rng(seed, si, pi, mi))
                rows.append(
                    (split, pos, m.name, m.label, m.family, m.garbage_time, p.n,
                     pearson(p.a, p.b), lo, hi)
                )  # fmt: skip
    return pl.DataFrame(rows, schema=SPLIT_HALF_SCHEMA, orient="row")


SHRINKAGE_SCHEMA = {
    "position": pl.String(), "metric": pl.String(), "g": pl.Int64(),
    "reliability": pl.Float64(), "var_signal": pl.Float64(), "var_noise": pl.Float64(),
    "prior_mean": pl.Float64(), "n": pl.Int64(), "first_season": pl.Int64(),
    "last_season": pl.Int64(),
}  # fmt: skip


def shrinkage(
    seasons: Sequence[int],
    *,
    frame: pl.DataFrame | None = None,
    db: Path | str | None = None,
    as_of: datetime | None = None,
    metrics: Sequence[str] = SHRINKAGE_METRICS,
    min_games: int = MIN_GAMES,
    max_g: int = MAX_G,
) -> pl.DataFrame:
    """The shrinkage table estimated from ``seasons`` ONLY: per position, metric (FPOE/game and
    its no-garbage-time variant) and g = 1 .. ``max_g`` games, the reliability r(g) (= the
    shrinkage factor), var_signal and var_noise (odd/even halves, :func:`signal_noise`),
    ``prior_mean`` (the average per-game value of the player-seasons) and ``n``.

    Point in time: rows of other seasons are never read (``frame`` is filtered, or only
    ``seasons`` are loaded from ``db``), so a walk-forward backtest for season S passes seasons
    < S and a later season can never change the estimate. ``as_of`` also drops rows not public
    then (for a season still in progress)."""
    wanted = sorted({int(s) for s in seasons})
    if not wanted:
        raise ValueError("shrinkage needs at least one season")
    source = frame if frame is not None else load_frame(db, wanted)
    games = study_games(source, wanted, min_games=min_games, as_of=as_of)
    table = halves(games, "odd_even")
    rows = []
    for pos in FANTASY_POSITIONS:
        for name in metrics:
            m = METRIC_BY_NAME[name]
            if not m.per_game:
                raise ValueError(f"shrinkage works on per-game metrics, not {name}")
            p = pairs(table, m, pos)
            vs, vn = signal_noise(p)
            prior = (
                float(np.sum(p.a * p.g_a + p.b * p.g_b) / np.sum(p.g_a + p.g_b)) if p.n else None
            )
            first = int(p.seasons.min()) if p.n else None
            last = int(p.seasons.max()) if p.n else None
            for g in range(1, max_g + 1):
                rel = reliability(vs, vn, g) if p.n >= 3 else None
                rows.append((pos, name, g, rel, vs, vn, prior, p.n, first, last))
    return pl.DataFrame(rows, schema=SHRINKAGE_SCHEMA, orient="row")


def shrinkage_factor(table: pl.DataFrame, position: str, g: float, metric: str = "fpoe") -> float:
    """r(g) for ``position`` after ``g`` games from a :func:`shrinkage` table (any g >= 0,
    also beyond the table's rows)."""
    row = table.filter((pl.col("position") == position) & (pl.col("metric") == metric))
    if row.is_empty():
        raise LookupError(f"no shrinkage estimate for {position} {metric}")
    r = row.row(0, named=True)
    return reliability(r["var_signal"], r["var_noise"], g)


def shrinkage_intervals(
    frame: pl.DataFrame,
    seasons: Sequence[int],
    *,
    metric: str = "fpoe",
    gs: Sequence[int] = (4, 8, 12),
    n_boot: int = N_BOOT,
    seed: int = SEED,
    min_games: int = MIN_GAMES,
) -> pl.DataFrame:
    """95% bootstrap intervals (resampling player-seasons) of r(g) for each position and g in
    ``gs``: columns position, metric, g, lo, hi."""
    table = halves(study_games(frame, seasons, min_games=min_games), "odd_even")
    m = METRIC_BY_NAME[metric]
    rows = []
    for pi, pos in enumerate(FANTASY_POSITIONS):
        p = pairs(table, m, pos)
        if p.n < 3 or n_boot <= 0:
            rows += [(pos, metric, g, None, None) for g in gs]
            continue
        vs_all, vn_all = [], []
        for idx in _resamples(p.n, n_boot, _rng(seed, 99, pi)):
            a, b = p.a[idx], p.b[idx]
            da, db = a - a.mean(axis=1, keepdims=True), b - b.mean(axis=1, keepdims=True)
            vs_all.append((da * db).sum(axis=1) / (p.n - 1))
            inv = (1.0 / p.g_a + 1.0 / p.g_b)[idx].mean(axis=1)
            vn_all.append(np.var(a - b, axis=1, ddof=1) / inv)
        vs, vn = np.concatenate(vs_all), np.concatenate(vn_all)
        for g in gs:
            r = np.where(vs > 0, vs / (np.clip(vs, 1e-12, None) + vn / g), 0.0)
            lo, hi = np.percentile(r, [2.5, 97.5])
            rows.append((pos, metric, g, float(lo), float(hi)))
    schema = {"position": pl.String(), "metric": pl.String(), "g": pl.Int64(),
              "lo": pl.Float64(), "hi": pl.Float64()}  # fmt: skip
    return pl.DataFrame(rows, schema=schema, orient="row")
