"""Hot-Seat H5: does poor fourth-down decision quality raise firing risk? (PROJECT_SPEC 8.5,
"Research question").

Primary: the end-of-season snapshot, seasons 2006-2025 (the decision grades start in 2006),
interims excluded, verified labels: a logistic regression of ``y`` on the standardized
``fourth_down_wp_lost_per_game`` controlling for performance vs expectation and the spec's
context features. The fit is near-unpenalized (an L2 term of :data:`RIDGE` on the
standardized slopes, there only so that a season resample with a separated binary column
still has a finite optimum); the report also gives the pure maximum-likelihood estimate.
Intervals: the classical (Wald) one from the information matrix, and a season-block bootstrap
(whole seasons resampled with replacement, :func:`twm.backtest.metrics.block_indices`, fixed
seed; a season drawn k times weighs k). Robustness specifications: one row each
(:data:`SPECS`). A permutation check shuffles the decision column within season.

Every column is standardized over the specification's own rows (mean 0, SD 1), so every
coefficient is per 1 SD; the decision term's SD in its own units is reported beside it.
The extra decision measures (WP lost on every graded fourth down, clock cases) are read from
the stored, pinned grades (never regraded) and exist for the end-of-season rows only.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl

from twm.backtest.metrics import BOOTSTRAP_SEED, N_BOOT, block_indices
from twm.modules.hot_seat.targets import RUP

DECISION = "fourth_down_wp_lost_per_game"  # clear calls only (the H3a feature)
DECISION_ALL = "fourth_down_wp_lost_all_per_game"  # clear calls and toss-ups (derived here)
CLOCK = "clock_cases_per_game"  # the three clock metrics' cases per game (derived here)
PERFORMANCE = ("wins_vs_expected", "point_diff_per_game", "pythag_minus_wins")
CONTEXT = (
    "tenure_seasons", "is_first_year_coach", "is_second_year_coach", "prev_playoff_round",
    "consecutive_losing_seasons", "rookie_r1_qb_on_roster", "division_rank",
)  # fmt: skip
CONTROLS = (*PERFORMANCE, *CONTEXT)
HAZARD_TIME = ("games_remaining", "final_interval")  # the hazard's baseline over the season
KEYS = ("season", "snapshot", "week", "team", "coach_id")
FIRST_SEASON, LAST_SEASON = 2006, 2025
RIDGE = 1e-3  # L2 on the standardized slopes (not the intercept), on the log-likelihood scale
N_PERM = 1000
PERM_SEED = 20061231
Z95 = 1.959963984540054


@dataclass(frozen=True)
class Fit:
    """A logistic fit: intercept first, then one coefficient per column."""

    coef: np.ndarray
    se: np.ndarray  # classical: sqrt(diag(inverse information)), NaN when not converged
    converged: bool
    iterations: int


def _loglik(xb: np.ndarray, y: np.ndarray, w: np.ndarray) -> float:
    return float(np.sum(w * (y * xb - np.logaddexp(0.0, xb))))


def fit_logit(
    x: np.ndarray,
    y: np.ndarray,
    w: np.ndarray | None = None,
    *,
    ridge: float = RIDGE,
    max_iter: int = 100,
    tol: float = 1e-10,
) -> Fit:
    """Weighted logistic regression by Newton's method with step halving. ``ridge`` adds
    ``-ridge/2 * sum(slope**2)`` to the log-likelihood (0 = maximum likelihood)."""
    n, k = x.shape
    xx = np.column_stack([np.ones(n), x])
    w = np.ones(n) if w is None else np.asarray(w, dtype=np.float64)
    pen = np.full(k + 1, float(ridge))
    pen[0] = 0.0
    beta = np.zeros(k + 1)

    def objective(b: np.ndarray) -> float:
        return _loglik(xx @ b, y, w) - 0.5 * float(np.sum(pen * b * b))

    cur, converged, it = objective(beta), False, 0
    for i in range(1, max_iter + 1):
        it = i
        p = 1.0 / (1.0 + np.exp(-(xx @ beta)))
        grad = xx.T @ (w * (y - p)) - pen * beta
        info = (xx * (w * p * (1.0 - p))[:, None]).T @ xx + np.diag(pen)
        try:
            step = np.linalg.solve(info, grad)
        except np.linalg.LinAlgError:
            break
        t, new, val = 1.0, beta + step, objective(beta + step)
        while val < cur and t > 1e-8:
            t /= 2.0
            new = beta + t * step
            val = objective(new)
        beta, gain, cur = new, val - cur, val
        if abs(gain) < tol * (1.0 + abs(cur)) and float(np.max(np.abs(t * step))) < 1e-6:
            converged = bool(np.all(np.isfinite(beta)))
            break
    se = np.full(k + 1, np.nan)
    if converged:
        p = 1.0 / (1.0 + np.exp(-(xx @ beta)))
        grad = xx.T @ (w * (y - p)) - pen * beta  # ~0 at the optimum (a failed line search is not)
        converged = float(np.max(np.abs(grad))) <= 1e-6 * max(1.0, float(w.sum()))
        info = (xx * (w * p * (1.0 - p))[:, None]).T @ xx + np.diag(pen)
        try:
            se = np.sqrt(np.diag(np.linalg.inv(info))) if converged else se
        except np.linalg.LinAlgError:
            converged = False
    return Fit(beta, se, converged, it)


@dataclass(frozen=True)
class Design:
    """The rows of one specification (complete cases, sorted by key) as standardized arrays."""

    terms: tuple[str, ...]  # columns kept (a constant column is dropped)
    x: np.ndarray  # standardized: (value - mean) / sd
    y: np.ndarray
    season: np.ndarray
    mean: np.ndarray
    sd: np.ndarray  # sample SD (ddof 1) in the column's own units
    rows: pl.DataFrame
    dropped_null: int
    dropped_constant: tuple[str, ...]


def design(rows: pl.DataFrame, terms: Sequence[str], label: str) -> Design:
    """Complete cases of ``terms`` + ``label``, every term standardized over these rows."""
    keep = rows.drop_nulls([*terms, label]).sort(list(KEYS))
    raw = keep.select(pl.col(t).cast(pl.Float64) for t in terms).to_numpy()
    sd = raw.std(axis=0, ddof=1) if keep.height > 1 else np.zeros(len(terms))
    ok = sd > 0
    used = tuple(t for t, k in zip(terms, ok, strict=True) if k)
    mean = raw[:, ok].mean(axis=0)
    return Design(
        terms=used,
        x=(raw[:, ok] - mean) / sd[ok],
        y=keep.get_column(label).cast(pl.Float64).to_numpy(),
        season=keep.get_column("season").to_numpy(),
        mean=mean,
        sd=sd[ok],
        rows=keep,
        dropped_null=rows.height - keep.height,
        dropped_constant=tuple(t for t, k in zip(terms, ok, strict=True) if not k),
    )


def season_blocks(season: np.ndarray) -> tuple[np.ndarray, int]:
    """Each row's block index (seasons in ascending order) and the number of blocks."""
    _, block = np.unique(season, return_inverse=True)
    return block, int(block.max()) + 1 if block.size else 0


def bootstrap(
    d: Design, n_boot: int = N_BOOT, seed: int = BOOTSTRAP_SEED, ridge: float = RIDGE
) -> np.ndarray:
    """(n_boot, 1 + terms) coefficients refit on season-block resamples (a season drawn k
    times weighs k: the same fit as stacking k copies); a resample that does not converge is a
    row of NaN."""
    block, n_blocks = season_blocks(d.season)
    idx = block_indices(n_blocks, n_boot, seed)
    out = np.full((n_boot, len(d.terms) + 1), np.nan)
    for b in range(n_boot):
        w = np.bincount(idx[b], minlength=n_blocks)[block].astype(np.float64)
        m = w > 0
        f = fit_logit(d.x[m], d.y[m], w[m], ridge=ridge)
        if f.converged:
            out[b] = f.coef
    return out


def permute_within(season: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """An index array that shuffles rows within each season only: row i takes the value of
    row ``perm[i]``, always a row of the same season."""
    perm = np.arange(season.size)
    for s in np.unique(season):
        pos = np.flatnonzero(season == s)
        perm[pos] = rng.permutation(pos)
    return perm


def permutation_null(
    d: Design,
    term: str,
    n_perm: int = N_PERM,
    seed: int = PERM_SEED,
    ridge: float = RIDGE,
) -> np.ndarray:
    """The coefficient of ``term`` refit after shuffling that column within season, ``n_perm``
    times (NaN where a fit does not converge)."""
    j = d.terms.index(term)
    rng = np.random.default_rng(seed)
    out = np.full(n_perm, np.nan)
    for r in range(n_perm):
        x = d.x.copy()
        x[:, j] = d.x[permute_within(d.season, rng), j]
        f = fit_logit(x, d.y, ridge=ridge)
        if f.converged:
            out[r] = f.coef[j + 1]
    return out


@dataclass(frozen=True)
class Spec:
    """One specification: which rows, which label, the decision measure and the controls."""

    name: str
    what: str  # one line for the report
    snapshot: str  # "end_of_season", "week_12" or "all" (the weekly hazard rows + season end)
    label: str = "y"
    decision: str = DECISION
    controls: tuple[str, ...] = CONTROLS
    positives: str = "main"  # "main" or "rup_positive" (targets.POSITIVE_SETS)
    drop_censored: bool = False
    drop_first_year: bool = False

    @property
    def terms(self) -> tuple[str, ...]:
        return (self.decision, *self.controls)


SPECS: tuple[Spec, ...] = (
    Spec("primary", "end of season; performance vs expectation + context controls",
         "end_of_season"),
    Spec("raw", "no controls (raw association)", "end_of_season", controls=()),
    Spec("performance_only", "performance vs expectation controls only", "end_of_season",
         controls=PERFORMANCE),
    Spec("week_12", "the week-12 snapshot (season label; in-season firings after week 12 "
         "count)", "week_12"),
    Spec("hazard", "weekly discrete-time hazard (event in the interval; + games remaining "
         "and the final-interval indicator)", "all", label="event",
         controls=(*CONTROLS, *HAZARD_TIME)),
    Spec("censored_dropped", "censored coach-seasons (non-firing departures) dropped",
         "end_of_season", drop_censored=True),
    Spec("rup_positive", f"`{RUP}` counted as a firing", "end_of_season",
         positives="rup_positive"),
    Spec("no_first_year", "first-year coaches excluded", "end_of_season",
         drop_first_year=True),
    Spec("all_graded", "WP lost on every graded fourth down (clear calls + toss-ups)",
         "end_of_season", decision=DECISION_ALL),
    Spec("with_clock", "plus the clock-management cases per game", "end_of_season",
         controls=(*CONTROLS, CLOCK)),
)  # fmt: skip


def spec_rows(spec: Spec, frames: dict[str, pl.DataFrame]) -> pl.DataFrame:
    """The labelled rows a specification uses: seasons 2006-2025, interims out."""
    df = frames[spec.positives].filter(
        ~pl.col("is_interim") & pl.col("season").is_between(FIRST_SEASON, LAST_SEASON)
    )
    if spec.snapshot == "end_of_season":
        df = df.filter(pl.col("snapshot") == "end_of_season")
    elif spec.snapshot == "week_12":
        df = df.filter((pl.col("snapshot") == "weekly") & (pl.col("week") == 12))
    elif spec.snapshot != "all":
        raise ValueError(f"unknown snapshot {spec.snapshot!r}")
    if spec.drop_censored:
        df = df.filter(~pl.col("censored"))
    if spec.drop_first_year:
        df = df.filter(~pl.col("is_first_year_coach"))
    return df


def _group_sums(df: pl.DataFrame, keys: list[str], values: list[str]) -> pl.DataFrame:
    """Per group, the sums of ``values`` added in a fixed order (the same bits every run,
    unlike a multithreaded group-by)."""
    s = df.sort([*keys, *values])
    if s.height == 0:
        return s.select(*keys, *values)
    k = s.select(keys).to_numpy()
    change = np.ones(s.height, dtype=bool)
    change[1:] = (k[1:] != k[:-1]).any(axis=1)
    starts = np.flatnonzero(change)
    out = s.select(keys)[starts]
    for v in values:
        sums = np.add.reduceat(s.get_column(v).cast(pl.Float64).to_numpy(), starts)
        out = out.with_columns(pl.Series(v, sums, dtype=pl.Float64))
    return out


def decision_extras(fourth: pl.DataFrame, clock: pl.DataFrame) -> pl.DataFrame:
    """Per (season, team), regular season, from the stored grades (never regraded): the WP
    lost on clear calls (``wp_lost_clear``, the H3a feature's numerator), on every graded
    fourth down (``wp_lost_all``: clear + toss-up; rows with no grade are excluded plays, the
    late game since G3b among them) and the clock-management cases (``clock_cases``: every
    metric's ``is_case`` rows)."""
    f = fourth.filter((pl.col("season_type") == "REG") & pl.col("grade").is_not_null())
    f = f.select(
        "season",
        pl.col("posteam").alias("team"),
        pl.when(pl.col("grade") == "clear").then(pl.col("wp_lost")).otherwise(0.0)
        .alias("wp_lost_clear"),
        pl.col("wp_lost").alias("wp_lost_all"),
    )  # fmt: skip
    lost = _group_sums(f, ["season", "team"], ["wp_lost_clear", "wp_lost_all"])
    c = clock.filter((pl.col("season_type") == "REG") & pl.col("is_case"))
    cases = c.group_by("season", "team").agg(pl.len().cast(pl.Float64).alias("clock_cases"))
    out = lost.join(cases, on=["season", "team"], how="full", coalesce=True)
    cols = ["wp_lost_clear", "wp_lost_all", "clock_cases"]
    return out.with_columns(pl.col(cols).fill_null(0.0), pl.col("season").cast(pl.Int32)).sort(
        "season", "team"
    )


def add_extras(rows: pl.DataFrame, extras: pl.DataFrame) -> pl.DataFrame:
    """The end-of-season rows get :data:`DECISION_ALL`, :data:`CLOCK` (per game: the season's
    totals over its regular-season games) and ``_check_clear`` (the clear-call total per game,
    which must equal the stored feature); NULL on the weekly rows. Plus ``final_interval``."""
    eos = pl.col("snapshot") == "end_of_season"
    games = pl.col("reg_games_played").cast(pl.Float64)
    df = rows.join(extras, on=["season", "team"], how="left")
    return df.with_columns(
        pl.when(eos).then(pl.col("wp_lost_all").fill_null(0.0) / games).alias(DECISION_ALL),
        pl.when(eos).then(pl.col("clock_cases").fill_null(0.0) / games).alias(CLOCK),
        pl.when(eos).then(pl.col("wp_lost_clear") / games).alias("_check_clear"),
        (pl.col("games_remaining") == 0).cast(pl.Float64).alias("final_interval"),
    ).drop("wp_lost_clear", "wp_lost_all", "clock_cases")


def load_stored_grades() -> tuple[pl.DataFrame, pl.DataFrame]:
    """The pinned frozen decision history (sha256 checked): fourth downs and clock cases."""
    from twm import pins
    from twm.modules.decisions import frozen as fz

    frames = fz.load_snapshot(pins.get_pin("decisions"))
    return frames["fourth_downs"], frames["clock_cases"]


@dataclass(frozen=True)
class Estimate:
    """One specification fitted: near-unpenalized, pure maximum likelihood, the bootstrap."""

    spec: Spec
    design: Design
    fit: Fit
    mle: Fit  # ridge 0
    boot: np.ndarray  # (n_boot, 1 + terms)
    coach_seasons: int
    positive_coach_seasons: int

    def boot_ok(self) -> int:
        return int(np.isfinite(self.boot[:, 0]).sum())

    def interval(self, term: str, kind: str) -> tuple[float, float]:
        """95% interval of a term's coefficient (log odds per SD): ``classical`` (Wald) or
        ``bootstrap`` (season-block percentile)."""
        j = self.design.terms.index(term) + 1
        if kind == "classical":
            b, se = self.fit.coef[j], self.fit.se[j]
            return float(b - Z95 * se), float(b + Z95 * se)
        vals = self.boot[:, j][np.isfinite(self.boot[:, j])]
        if vals.size == 0:
            return float("nan"), float("nan")
        lo, hi = np.quantile(vals, [0.025, 0.975])
        return float(lo), float(hi)


def run_spec(
    spec: Spec, frames: dict[str, pl.DataFrame], n_boot: int = N_BOOT, ridge: float = RIDGE
) -> Estimate:
    rows = spec_rows(spec, frames)
    d = design(rows, spec.terms, spec.label)
    if spec.decision not in d.terms:
        raise ValueError(f"{spec.name}: the decision column {spec.decision} is constant or empty")
    cs = d.rows.group_by("season", "team", "coach_id").agg(pl.col(spec.label).max().alias("pos"))
    return Estimate(
        spec=spec,
        design=d,
        fit=fit_logit(d.x, d.y, ridge=ridge),
        mle=fit_logit(d.x, d.y, ridge=0.0),
        boot=bootstrap(d, n_boot, ridge=ridge),
        coach_seasons=cs.height,
        positive_coach_seasons=int(cs.get_column("pos").sum()),
    )


@dataclass(frozen=True)
class Research:
    estimates: list[Estimate]
    perm: np.ndarray  # the primary decision coefficient under within-season shuffles
    perm_p: float  # two-sided: (1 + #|null| >= |observed|) / (1 + valid shuffles)
    check_clear_max_diff: float  # recomputed clear WP lost per game vs the stored feature
    n_boot: int
    n_perm: int


def permutation_p(observed: float, null: np.ndarray) -> float:
    vals = null[np.isfinite(null)]
    return float((1 + np.sum(np.abs(vals) >= abs(observed))) / (1 + vals.size))


def run_research(
    main: pl.DataFrame,
    rup: pl.DataFrame,
    extras: pl.DataFrame,
    *,
    specs: Sequence[Spec] = SPECS,
    n_boot: int = N_BOOT,
    n_perm: int = N_PERM,
    progress: Callable[[str], None] | None = None,
) -> Research:
    """Every specification, then the permutation check on the first (the primary)."""
    frames = {"main": add_extras(main, extras), "rup_positive": add_extras(rup, extras)}
    est = []
    for spec in specs:
        est.append(run_spec(spec, frames, n_boot))
        if progress:
            progress(f"{spec.name}: {est[-1].design.rows.height} rows")
    eos = spec_rows(specs[0], frames)
    diff = (eos.get_column("_check_clear") - eos.get_column(DECISION)).abs().max()
    p0 = est[0]
    perm = permutation_null(p0.design, p0.spec.decision, n_perm)
    j = p0.design.terms.index(p0.spec.decision) + 1
    return Research(est, perm, permutation_p(float(p0.fit.coef[j]), perm),
                    float(diff) if diff is not None else float("nan"), n_boot, n_perm)  # fmt: skip


def load_rows(
    db: Path | str, features: Path, labels_csv: Path, candidates_csv: Path
) -> tuple[pl.DataFrame, pl.DataFrame, dict[str, int]]:
    """The verified labelled rows of 2006-2025, built as the backtest builds them
    (``targets.build_targets``, verified mode: refuses unverified departures): the main
    positives and ``resigned_under_pressure`` positive; plus the label counts."""
    from twm.modules.hot_seat import labels as hl
    from twm.modules.hot_seat import targets as ht

    labels, cands = hl.read_labels(labels_csv), hl.read_candidates(candidates_csv)
    if cands is None:
        raise FileNotFoundError(candidates_csv)
    deps, unresolved = ht.resolve_departures(labels, cands, ht.load_coach_names(db))
    if unresolved.height:
        raise ValueError(f"departures without a coach_id: {unresolved['candidate_id'].to_list()}")
    used = labels.filter(pl.col("verified_by_owner") == "y")
    feats = ht.refresh_interim(pl.read_parquet(features), used, cands)
    feats = feats.filter(pl.col("season").is_between(FIRST_SEASON, LAST_SEASON))
    dates = ht.load_team_dates(db, FIRST_SEASON, LAST_SEASON)
    main, counts = ht.build_targets(feats, deps, dates, mode="verified")
    rup, _ = ht.build_targets(
        feats, deps, dates, mode="verified", positive_types=ht.POSITIVE_SETS["rup_positive"]
    )
    return main, rup, counts


def estimate_table(res: Research) -> pl.DataFrame:
    """One row per specification and term: log-odds coefficient per 1 SD, its classical SE,
    the odds ratio with the classical and the season-bootstrap 95% intervals, the pure-MLE
    odds ratio, the term's SD in its own units, and the specification's counts. The
    permutation p-value is on the primary decision row."""
    out = []
    for i, e in enumerate(res.estimates):
        d = e.design
        for j, term in enumerate(d.terms, start=1):
            c_lo, c_hi = e.interval(term, "classical")
            b_lo, b_hi = e.interval(term, "bootstrap")
            is_dec = term == e.spec.decision
            out.append({
                "spec": e.spec.name, "term": term, "is_decision": is_dec,
                "coef": float(e.fit.coef[j]), "se": float(e.fit.se[j]),
                "odds_ratio": float(np.exp(e.fit.coef[j])),
                "or_lo_classical": float(np.exp(c_lo)), "or_hi_classical": float(np.exp(c_hi)),
                "or_lo_bootstrap": float(np.exp(b_lo)), "or_hi_bootstrap": float(np.exp(b_hi)),
                "odds_ratio_mle": float(np.exp(e.mle.coef[j])) if e.mle.converged else None,
                "term_mean": float(d.mean[j - 1]), "term_sd": float(d.sd[j - 1]),
                "n_rows": d.rows.height, "n_positive_rows": int(d.y.sum()),
                "coach_seasons": e.coach_seasons, "positive_coach_seasons":
                e.positive_coach_seasons, "n_seasons": int(np.unique(d.season).size),
                "dropped_null_rows": d.dropped_null, "n_boot": res.n_boot,
                "boot_converged": e.boot_ok(), "fit_converged": e.fit.converged,
                "perm_p": res.perm_p if (i == 0 and is_dec) else None,
                "n_perm": res.n_perm if (i == 0 and is_dec) else None,
            })  # fmt: skip
    return pl.DataFrame(out, infer_schema_length=None)


def grade_counts(fourth: pl.DataFrame, clock: pl.DataFrame) -> dict[str, int]:
    """Regular-season counts of the stored grades, 2006-2025 (the report's limits section)."""
    reg = (pl.col("season_type") == "REG") & pl.col("season").is_between(FIRST_SEASON, LAST_SEASON)
    f = fourth.filter(reg)
    c = clock.filter(reg & pl.col("is_case"))
    out = {
        "fourth_downs": f.height,
        "graded": int(f.select(pl.col("grade").is_not_null().sum()).item()),
        "clear": int(f.select((pl.col("grade") == "clear").sum()).item()),
        "toss_up": int(f.select((pl.col("grade") == "toss_up").sum()).item()),
        "late_game": int(f.select((pl.col("exclusion") == "late_game").sum()).item()),
        "clock_cases": c.height,
    }
    for m in sorted(clock.get_column("metric").unique().to_list()):
        out[f"clock_cases_{m}"] = int(c.filter(pl.col("metric") == m).height)
    return out
