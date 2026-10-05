"""The allocation table, the predictions and the walk-forward backtest (docs/teammate_out.md).

Allocation: of the share of the team's carries (targets) an absent starter vacated, the part a
teammate absorbed, as a ratio of sums over single-starter events: sum of the teammates' share
changes (game share minus baseline share) over the sum of the vacated shares, per player.
Cells are (absent starter's position, teammate's role); a cell is shrunk to its group cell
(absent position, teammate's position) with :data:`PSEUDO_COUNT` teammate-events. Carries are
allocated only when a RB is out (:data:`CARRY_OUT`): a WR's or TE's vacated carry share is
about 0 and its ratio is noise. Nothing here is machine learning: every number is a ratio of
counted sums.

Candidates (:data:`CANDIDATES`, simplest first): 'nothing' (baseline shares and baseline
points per game), 'pro_rata' (vacated shares split among same-position teammates in proportion
to their baseline shares), 'group' and 'role' (the allocation cells). Points of the last three:
predicted carries + targets (shares x the team's baseline volume) x his PPR per opportunity,
shrunk to the training seasons' position mean with :data:`PPO_PSEUDO` opportunities.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import polars as pl

CANDIDATES = ("nothing", "pro_rata", "group", "role")
TEST_SEASONS = tuple(range(2016, 2026))
SEASON_WINS_NEEDED = 6
PSEUDO_COUNT = 20.0
PPO_PSEUDO = 20.0
CARRY_OUT = ("RB",)
_EV = ["season", "team", "gi"]


def pairs(mates: pl.DataFrame) -> pl.DataFrame:
    """One row per (event, teammate, absent starter): out_pos and that starter's vacated
    carry / target shares (an event with two starters out has two rows per teammate)."""
    cols = ["out_id", "out_pos", "out_carry_share", "out_target_share"]
    keep = [*_EV, "gsis_id", "position", "role", "base_carry_share", "base_target_share"]
    return mates.select(*keep, *cols).explode(cols)


def fit_cells(mates: pl.DataFrame, pseudo_count: float = PSEUDO_COUNT) -> pl.DataFrame:
    """The allocation cells from the single-starter events of ``mates`` (the training rows):
    out_pos, role, position, n, carry, target (shrunk) and the group values."""
    one = mates.filter(pl.col("n_out") == 1).with_columns(
        pl.col("out_pos").list.first().alias("out_pos"),
        (pl.col("act_carry_share") - pl.col("base_carry_share")).alias("d_carry"),
        (pl.col("act_target_share") - pl.col("base_target_share")).alias("d_target"),
    )

    def ratio(d: str, v: str) -> pl.Expr:
        return pl.when(pl.col(v).sum() > 0).then(pl.col(d).sum() / pl.col(v).sum()).otherwise(0.0)

    agg = [pl.len().alias("n"), ratio("d_carry", "vac_carry_share").alias("carry"),
           ratio("d_target", "vac_target_share").alias("target")]  # fmt: skip
    grp = one.group_by("out_pos", "position").agg(*agg)
    rol = one.group_by("out_pos", "role").agg(pl.col("position").first(), *agg)
    k = float(pseudo_count)
    cells = rol.join(grp, on=["out_pos", "position"], suffix="_group").with_columns(
        *[((pl.col("n") * pl.col(c) + k * pl.col(f"{c}_group")) / (pl.col("n") + k)).alias(c)
          for c in ("carry", "target")],
    )  # fmt: skip
    no_carry = ~pl.col("out_pos").is_in(list(CARRY_OUT))
    cells = cells.with_columns(
        *[pl.when(no_carry).then(0.0).otherwise(pl.col(c)).alias(c)
          for c in ("carry", "carry_group")],
    )  # fmt: skip
    cols = ["out_pos", "role", "position", "n", "carry", "target", "n_group", "carry_group",
            "target_group"]  # fmt: skip
    return cells.select(cols).sort("out_pos", "role")


def group_cells(cells: pl.DataFrame) -> pl.DataFrame:
    """The group level of ``cells``: out_pos, position, n, carry, target (unshrunk)."""
    return (cells.select("out_pos", "position", pl.col("n_group").alias("n"),
                         pl.col("carry_group").alias("carry"),
                         pl.col("target_group").alias("target"))
            .unique(["out_pos", "position"]).sort("out_pos", "position"))  # fmt: skip


def ppo_means(mates: pl.DataFrame) -> dict[str, float]:
    """PPR points per opportunity (carry or target) by position over the rows' baselines."""
    g = mates.unique([*_EV, "gsis_id"]).group_by("position").agg(
        (pl.col("base_points") * pl.col("base_games")).sum().alias("pts"),
        ((pl.col("base_carries") + pl.col("base_targets")) * pl.col("base_games")).sum()
        .alias("opp"),
    )  # fmt: skip
    return {p: (float(a) / float(b) if b else 0.0) for p, a, b in g.iter_rows()}


def _deltas(p: pl.DataFrame, candidate: str, cells: pl.DataFrame) -> pl.DataFrame:
    """Per (event, teammate, absent starter): d_carry and d_target, the share changes."""
    carry_ok = pl.col("out_pos").is_in(list(CARRY_OUT))
    if candidate == "nothing":
        return p.with_columns(pl.lit(0.0).alias("d_carry"), pl.lit(0.0).alias("d_target"))
    if candidate == "pro_rata":
        same = pl.col("position") == pl.col("out_pos")
        tot = [*_EV, "out_id"]
        p = p.with_columns(
            pl.col("base_carry_share").filter(same).sum().over(tot).alias("_c"),
            pl.col("base_target_share").filter(same).sum().over(tot).alias("_t"),
        )
        dc = pl.when(same & carry_ok & (pl.col("_c") > 0)).then(
            pl.col("out_carry_share") * pl.col("base_carry_share") / pl.col("_c")
        )
        dt = pl.when(same & (pl.col("_t") > 0)).then(
            pl.col("out_target_share") * pl.col("base_target_share") / pl.col("_t")
        )
        return p.with_columns(
            dc.otherwise(0.0).alias("d_carry"), dt.otherwise(0.0).alias("d_target")
        ).drop("_c", "_t")
    g = group_cells(cells).select(
        "out_pos", "position", pl.col("carry").alias("_gc"), pl.col("target").alias("_gt")
    )
    p = p.join(g, on=["out_pos", "position"], how="left")
    if candidate == "role":
        r = cells.select(
            "out_pos", "role", pl.col("carry").alias("_rc"), pl.col("target").alias("_rt")
        )
        p = p.join(r, on=["out_pos", "role"], how="left").with_columns(
            pl.coalesce("_rc", "_gc").alias("_gc"), pl.coalesce("_rt", "_gt").alias("_gt")
        )
    elif candidate != "group":
        raise ValueError(f"unknown candidate {candidate!r}")
    return p.with_columns(
        (pl.col("_gc").fill_null(0.0) * pl.col("out_carry_share")).alias("d_carry"),
        (pl.col("_gt").fill_null(0.0) * pl.col("out_target_share")).alias("d_target"),
    ).select(p.columns[: p.columns.index("_gc")] + ["d_carry", "d_target"])


def predict(
    mates: pl.DataFrame, candidate: str, cells: pl.DataFrame, ppo_mean: dict[str, float]
) -> pl.DataFrame:
    """``mates`` with pred_carry_share, pred_target_share, ppo, pred_points and pred_gain
    (the predicted points gain: 0 for 'nothing', else the opportunity change x ppo)."""
    d = (_deltas(pairs(mates), candidate, cells).group_by(*_EV, "gsis_id")
         .agg(pl.col("d_carry").sum(), pl.col("d_target").sum()))  # fmt: skip
    m = mates.join(d, on=[*_EV, "gsis_id"], how="left").with_columns(
        pl.col("d_carry").fill_null(0.0), pl.col("d_target").fill_null(0.0)
    )
    mean = pl.col("position").replace_strict(ppo_mean, default=0.0, return_dtype=pl.Float64)
    opp = (pl.col("base_carries") + pl.col("base_targets")) * pl.col("base_games")
    m = m.with_columns(
        (pl.col("base_carry_share") + pl.col("d_carry")).clip(0.0, 1.0).alias("pred_carry_share"),
        (pl.col("base_target_share") + pl.col("d_target")).clip(0.0, 1.0)
        .alias("pred_target_share"),
        ((pl.col("base_points") * pl.col("base_games") + PPO_PSEUDO * mean)
         / (opp + PPO_PSEUDO)).alias("ppo"),
    )  # fmt: skip
    opps = (pl.col("pred_carry_share") * pl.col("base_team_carries")
            + pl.col("pred_target_share") * pl.col("base_team_targets"))  # fmt: skip
    gain = (pl.col("d_carry") * pl.col("base_team_carries")
            + pl.col("d_target") * pl.col("base_team_targets")) * pl.col("ppo")  # fmt: skip
    if candidate == "nothing":
        return m.with_columns(pl.col("base_points").alias("pred_points"),
                              pl.lit(0.0).alias("pred_gain"))  # fmt: skip
    return m.with_columns((opps * pl.col("ppo")).alias("pred_points"), gain.alias("pred_gain"))


def top_gainer_hits(pred: pl.DataFrame) -> pl.DataFrame:
    """Per event: whether the teammate with the largest predicted gain (ties: higher baseline
    points, then id) had the largest real gain (game points minus baseline points)."""
    p = pred.with_columns((pl.col("act_points") - pl.col("base_points")).alias("_real"))
    pick = (
        p.sort("pred_gain", "base_points", "gsis_id", descending=[True, True, False])
        .group_by(_EV, maintain_order=True)
        .first()
        .select(*_EV, pl.col("gsis_id").alias("_p"))
    )
    real = (
        p.sort("_real", "gsis_id", descending=[True, False])
        .group_by(_EV, maintain_order=True)
        .first()
        .select(*_EV, pl.col("gsis_id").alias("_r"))
    )
    return pick.join(real, on=_EV).with_columns((pl.col("_p") == pl.col("_r")).alias("hit"))


def metrics(pred: pl.DataFrame) -> dict[str, float | int]:
    """n, events, MAE of carry share, target share and points, and the top-gainer hit rate."""
    hits = top_gainer_hits(pred)
    err = pred.select(
        (pl.col("act_carry_share") - pl.col("pred_carry_share")).abs().mean().alias("mae_carry"),
        (pl.col("act_target_share") - pl.col("pred_target_share")).abs().mean()
        .alias("mae_target"),
        (pl.col("act_points") - pl.col("pred_points")).abs().mean().alias("mae_points"),
    ).row(0, named=True)  # fmt: skip
    return {"n": pred.height, "events": hits.height, **{k: float(v) for k, v in err.items()},
            "top_hit": float(hits["hit"].mean()) if hits.height else 0.0}  # fmt: skip


@dataclass(frozen=True)
class Fitted:
    """What a prediction needs from the training seasons."""

    cells: pl.DataFrame
    ppo_mean: dict[str, float]


def fit(train: pl.DataFrame, pseudo_count: float = PSEUDO_COUNT) -> Fitted:
    return Fitted(fit_cells(train, pseudo_count), ppo_means(train))


def walk_forward(mates: pl.DataFrame, candidate: str, tests: Sequence[int]) -> pl.DataFrame:
    """Each test season's rows predicted with cells fit on the seasons before it only."""
    out = []
    for s in tests:
        test = mates.filter(pl.col("season") == s)
        if test.is_empty():
            continue
        f = fit(mates.filter(pl.col("season") < s))
        out.append(predict(test, candidate, f.cells, f.ppo_mean))
    return pl.concat(out, how="diagonal_relaxed").with_columns(pl.lit(candidate).alias("candidate"))


def backtest(mates: pl.DataFrame, tests: Sequence[int] = TEST_SEASONS) -> pl.DataFrame:
    """Walk-forward metrics of every candidate, by test season and pooled ('all')."""
    rows = []
    for c in CANDIDATES:
        wf = walk_forward(mates, c, tests)
        for s in [*tests, None]:
            part = wf if s is None else wf.filter(pl.col("season") == s)
            if part.height:
                rows.append({"candidate": c, "season": "all" if s is None else str(s),
                             **metrics(part)})  # fmt: skip
    return pl.DataFrame(rows)


@dataclass(frozen=True)
class Selection:
    chosen: str
    path: tuple[str, ...]
    metrics: pl.DataFrame


def select(bt: pl.DataFrame, needed: int = SEASON_WINS_NEEDED) -> Selection:
    """The forward rule (fixed before any test number was seen): from 'nothing', the next
    candidate replaces the current choice only if its pooled points MAE is lower AND its
    points MAE is lower in at least ``needed`` test seasons."""

    def mae(c: str) -> dict[str, float]:
        part = bt.filter(pl.col("candidate") == c)
        return dict(zip(part["season"], part["mae_points"], strict=True))

    chosen, path = CANDIDATES[0], [CANDIDATES[0]]
    for c in CANDIDATES[1:]:
        a, b = mae(c), mae(chosen)
        wins = sum(a[s] < b[s] for s in a if s != "all" and s in b)
        if a["all"] < b["all"] and wins >= needed:
            chosen = c
            path.append(c)
    return Selection(chosen, tuple(path), bt)


# ---- 80% ranges from the walk-forward misses (as Regression Watch's) --------------------
LEVEL = 0.8
Q_LO, Q_HI = 0.1, 0.9
POINT_BANDS = (6.0, 12.0)  # predicted points: below 6, 6 to 12, 12 and more
MIN_MISSES = 30
ALL = "all"


def band_expr(col: str = "pred_points") -> pl.Expr:
    lo, hi = POINT_BANDS
    return (pl.when(pl.col(col) < lo).then(pl.lit(f"<{lo:g}"))
            .when(pl.col(col) < hi).then(pl.lit(f"{lo:g}-{hi:g}"))
            .otherwise(pl.lit(f"{hi:g}+")))  # fmt: skip


def range_cells(misses: pl.DataFrame) -> pl.DataFrame:
    """position, band ('all' = the position's every row), n, lo, hi: the 10th and 90th
    percentiles of the misses (real minus predicted points) of walk-forward rows."""
    r = misses.with_columns((pl.col("act_points") - pl.col("pred_points")).alias("_miss"),
                            band_expr().alias("band"))  # fmt: skip
    agg = [pl.len().alias("n"), pl.col("_miss").quantile(Q_LO, "linear").alias("lo"),
           pl.col("_miss").quantile(Q_HI, "linear").alias("hi")]  # fmt: skip
    by_band = r.group_by("position", "band").agg(*agg)
    by_pos = r.group_by("position").agg(*agg).with_columns(pl.lit(ALL).alias("band"))
    cols = ["position", "band", "n", "lo", "hi"]
    return pl.concat([by_band.select(cols), by_pos.select(cols)]).sort("position", "band")


def with_ranges(pred: pl.DataFrame, cells: pl.DataFrame) -> pl.DataFrame:
    """``pred`` with points_lo / points_hi (the band's cell when it has MIN_MISSES misses,
    else the position's; lo at least 0); NULL when neither has enough."""
    ok = cells.filter(pl.col("n") >= MIN_MISSES)
    b = ok.filter(pl.col("band") != ALL).select("position", "band", "lo", "hi")
    p = ok.filter(pl.col("band") == ALL).select("position", pl.col("lo").alias("_plo"),
                                               pl.col("hi").alias("_phi"))  # fmt: skip
    out = pred.with_columns(band_expr().alias("_band")).join(
        b.rename({"band": "_band"}), on=["position", "_band"], how="left"
    ).join(p, on="position", how="left")  # fmt: skip
    lo, hi = pl.coalesce("lo", "_plo"), pl.coalesce("hi", "_phi")
    return out.with_columns(
        (pl.col("pred_points") + lo).clip(lower_bound=0.0).alias("points_lo"),
        (pl.col("pred_points") + hi).alias("points_hi"),
    ).drop("_band", "lo", "hi", "_plo", "_phi")


def coverage(wf: pl.DataFrame, tests: Sequence[int] = TEST_SEASONS) -> pl.DataFrame:
    """Walk-forward coverage of the ranges: test season s gets cells from the misses of the
    test seasons before it (the first has none: left out). position ('all' too), n, inside,
    below, above, coverage."""
    parts = []
    for s in tests:
        cur, past = wf.filter(pl.col("season") == s), wf.filter(pl.col("season") < s)
        if cur.is_empty() or past.is_empty():
            continue
        parts.append(with_ranges(cur, range_cells(past)))
    r = pl.concat(parts).filter(pl.col("points_lo").is_not_null())
    a = pl.col("act_points")
    r = r.with_columns(
        (a < pl.col("points_lo")).alias("below"), (a > pl.col("points_hi")).alias("above")
    )
    agg = [pl.len().alias("n"), pl.col("below").sum(), pl.col("above").sum()]
    out = pl.concat([r.group_by("position").agg(*agg),
                     r.select(pl.lit(ALL).alias("position"), *agg)])  # fmt: skip
    return (
        out.with_columns((pl.col("n") - pl.col("below") - pl.col("above")).alias("inside"))
        .with_columns((pl.col("inside") / pl.col("n")).alias("coverage"))
        .sort("position")
    )
