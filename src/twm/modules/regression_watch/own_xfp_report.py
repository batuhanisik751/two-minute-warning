"""The own walk-forward xFP against ffopportunity, and the leakage comparison (step H6-b;
PROJECT_SPEC 6.3: "compare backtest results with and without the nflverse model columns").

Three comparisons, all on the SAME plays and player-games:

1. **per play**: each component's own walk-forward prediction and ffopportunity's against what
   happened (log loss and Brier score for chances, mean absolute and mean squared error for
   yards), by season and pooled, with the season-block interval of the difference;
2. **per player-game**: own xFP against ffopportunity's (both summed by D1's per-play SQL):
   correlation and mean difference by position and season;
3. **leakage**: D2's stability study and D3's projection backtest rerun with the own xFP in
   place of ffopportunity's (the same functions, the same seasons and rules; only the
   expected-points source differs, ``player_week.with_xfp``), side by side, with season-block
   intervals of the differences.

ffopportunity's models were trained on seasons that include later ones (often the test season
itself), so comparison 1 flatters it; the own models never saw their test season.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import polars as pl

from twm.modules.regression_watch import own_xfp as ox

N_BOOT = 2000
POOLED = "all"
SOURCES = ("ffopportunity", "own")
# Season windows of the per-play comparison (as D2's report: early, middle, recent seasons).
WINDOWS = ((2007, 2013), (2014, 2020), (2021, 2026))


def play_errors(
    plays: dict[str, pl.DataFrame], preds: dict[str, pl.DataFrame]
) -> dict[str, pl.DataFrame]:
    """Per component: one row per labelled play of the predicted seasons with the label ``y``,
    ``ffo`` (ffopportunity's value) and ``own``, and each source's per-play errors: ``ll_*``
    (log loss) and ``br_*`` (Brier) for a chance, ``ae_*`` and ``se_*`` for yards."""
    out = {}
    for c in ox.COMPONENTS:
        p = preds[c.kind].select(*ox.KEYS, pl.col(c.column).alias("own"))
        rows = (
            plays[c.kind]
            .filter(ox.fit_mask(c))
            .join(p, on=list(ox.KEYS), how="inner")
            .select("season", *ox.KEYS, pl.col(c.label).cast(pl.Float64).alias("y"),
                    pl.col(c.column).alias("ffo"), "own")
            .drop_nulls(["y", "ffo", "own"])
        )  # fmt: skip
        cols = []
        for src in ("ffo", "own"):
            q, y = pl.col(src), pl.col("y")
            if c.objective == "regression":
                cols += [(q - y).abs().alias(f"ae_{src}"), ((q - y) ** 2).alias(f"se_{src}")]
            else:
                k = q.clip(ox.EPS, 1 - ox.EPS)
                ll = -(y * k.log() + (1 - y) * (1 - k).log())
                cols += [ll.alias(f"ll_{src}"), ((q - y) ** 2).alias(f"br_{src}")]
        out[c.name] = rows.with_columns(cols).sort(["season", *ox.KEYS])
    return out


def _metrics(c: ox.Component) -> tuple[tuple[str, str], ...]:
    """(metric, error column prefix): log loss + Brier for chances, MAE + MSE for yards."""
    if c.objective == "regression":
        return (("mae", "ae"), ("mse", "se"))
    return (("log_loss", "ll"), ("brier", "br"))


def per_play_table(errors: dict[str, pl.DataFrame], *, n_boot: int = N_BOOT) -> pl.DataFrame:
    """Per component, metric and season, per :data:`WINDOWS` and pooled (``season`` = "all"):
    plays, the mean label and predictions, each source's value (mean per play) and own -
    ffopportunity, with its season-block interval for the windows and the pooled row (a single
    season is one block: no interval)."""
    from twm.backtest.metrics import paired_block_bootstrap

    rows = []
    keys = ["season", *ox.KEYS]
    for c in ox.COMPONENTS:
        e = errors[c.name]
        groups = [(str(s), e.filter(pl.col("season") == s)) for s in sorted(set(e["season"]))]
        wins = [(f"{a}-{b}", e.filter(pl.col("season").is_between(a, b))) for a, b in WINDOWS]
        for label, sub in [*groups, *wins, (POOLED, e)]:
            if sub.height == 0:
                continue
            base = {"component": c.name, "season": label, "plays": sub.height,
                    "mean_y": sub["y"].mean(), "mean_ffo": sub["ffo"].mean(),
                    "mean_own": sub["own"].mean()}  # fmt: skip
            for metric, pre in _metrics(c):
                f, o = sub[f"{pre}_ffo"].mean(), sub[f"{pre}_own"].mean()
                lo = hi = None
                if label == POOLED or "-" in label:
                    iv = paired_block_bootstrap(
                        sub.select(*keys, pl.col(f"{pre}_own").alias("v")),
                        sub.select(*keys, pl.col(f"{pre}_ffo").alias("v")),
                        keys=keys, value="v", n_boot=n_boot,
                    )  # fmt: skip
                    lo, hi = iv.lo, iv.hi
                rows.append({**base, "metric": metric, "ffo": f, "own": o, "diff": o - f,
                             "diff_lo": lo, "diff_hi": hi})  # fmt: skip
    return pl.from_dicts(rows, infer_schema_length=None)


def player_week_pairs(frame: pl.DataFrame, ffo: pl.DataFrame, own: pl.DataFrame) -> pl.DataFrame:
    """The frame's player-games with an ffopportunity row (``xfp`` not null; point-in-time
    position) and both sources' per-play sums: ``xfp_ffo``, ``xfp_own`` (all plays) and
    ``xfp_ng_ffo``, ``xfp_ng_own`` (garbage time left out); a game without an own row
    (no play predicted) counts 0, as in ``player_week.with_xfp``."""
    k = ["game_id", "gsis_id"]
    sums = [pl.col(c).fill_null(0.0) for c in ("xfp_ffo", "xfp_own", "xfp_ng_ffo", "xfp_ng_own")]
    return (
        frame.filter(pl.col("xfp").is_not_null())
        .select("season", "week", *k, "position", "fantasy_points")
        .join(ffo.select(*k, pl.col("xfp").alias("xfp_ffo"), pl.col("xfp_ng").alias("xfp_ng_ffo")),
              on=k, how="left")
        .join(own.select(*k, pl.col("xfp").alias("xfp_own"), pl.col("xfp_ng").alias("xfp_ng_own")),
              on=k, how="left")
        .with_columns(sums)
        .sort(["season", "week", *k])
    )  # fmt: skip


def player_week_table(pairs: pl.DataFrame) -> pl.DataFrame:
    """Per position (and pooled) x season (and "all"), for xFP and the no-garbage xFP: games,
    each source's mean, the mean and mean absolute own - ffopportunity difference, and the
    correlation of the two."""
    rows = []
    for pos in (*sorted(set(pairs["position"])), POOLED):
        p = pairs if pos == POOLED else pairs.filter(pl.col("position") == pos)
        seasons = [(str(s), p.filter(pl.col("season") == s)) for s in sorted(set(p["season"]))]
        for label, sub in [*seasons, (POOLED, p)]:
            for what, suffix in (("xfp", ""), ("xfp_ng", "_ng")):
                a, b = sub[f"xfp{suffix}_ffo"], sub[f"xfp{suffix}_own"]
                rows.append({
                    "position": pos, "season": label, "what": what, "games": sub.height,
                    "mean_ffo": a.mean(), "mean_own": b.mean(), "mean_diff": (b - a).mean(),
                    "mean_abs_diff": (b - a).abs().mean(),
                    "corr": float(np.corrcoef(a.to_numpy(), b.to_numpy())[0, 1])
                    if sub.height > 2 else None,
                })  # fmt: skip
    return pl.from_dicts(rows, infer_schema_length=None)


# --------------------------------------------------------------------------------------
# Leakage comparison 1: D2's stability study with each xFP source
# --------------------------------------------------------------------------------------


def _block_stats(a: np.ndarray, b: np.ndarray, seasons: np.ndarray, blocks: list) -> np.ndarray:
    """Per season block: n, sum a, sum b, sum a^2, sum b^2, sum ab (a correlation's parts)."""
    out = np.zeros((len(blocks), 6))
    for i, s in enumerate(blocks):
        m = seasons == s
        x, y = a[m], b[m]
        out[i] = (m.sum(), x.sum(), y.sum(), (x * x).sum(), (y * y).sum(), (x * y).sum())
    return out


def _corr(stats: np.ndarray) -> np.ndarray:
    n, sa, sb, saa, sbb, sab = (stats[..., i] for i in range(6))
    with np.errstate(invalid="ignore", divide="ignore"):
        return (n * sab - sa * sb) / np.sqrt((n * saa - sa * sa) * (n * sbb - sb * sb))


def _pct(x: np.ndarray) -> tuple[float | None, float | None]:
    x = x[~np.isnan(x)]
    if not len(x):
        return None, None
    lo, hi = np.percentile(x, [2.5, 97.5])
    return float(lo), float(hi)


def stability_comparison(
    frames: dict[str, pl.DataFrame], seasons: Sequence[int], *, n_boot: int = N_BOOT
) -> pl.DataFrame:
    """D2's split-half correlations (:func:`stability.study_games`, ``halves``, ``pairs``) of
    every metric with each source's frame, on the same player-seasons, with season-block
    intervals for each r and for own - ffopportunity (the same resampled seasons for both)."""
    from twm.backtest.metrics import block_indices
    from twm.config import FANTASY_POSITIONS
    from twm.modules.regression_watch import stability as st

    games = {src: st.study_games(frames[src], seasons) for src in SOURCES}
    keys = ["season", "gsis_id", "position"]
    rows = []
    for split in st.SPLITS:
        t = {src: st.halves(games[src], split) for src in SOURCES}
        if not t["own"].select(keys).equals(t["ffopportunity"].select(keys)):
            raise ValueError("the two sources must be studied on the same player-seasons")
        for pos in FANTASY_POSITIONS:
            for m in (m for m in st.METRICS if pos in m.positions):
                p = {src: st.pairs(t[src], m, pos) for src in SOURCES}
                if not np.array_equal(p["own"].seasons, p["ffopportunity"].seasons):
                    raise ValueError(f"{m.name} {pos}: the two sources pair different rows")
                blocks = sorted(set(p["own"].seasons.tolist()))
                idx = block_indices(len(blocks), n_boot).astype(int)
                stats = {s: _block_stats(p[s].a, p[s].b, p[s].seasons, blocks) for s in SOURCES}
                boot = {s: _corr(stats[s][idx].sum(axis=1)) for s in SOURCES}
                r = {s: st.pearson(p[s].a, p[s].b) for s in SOURCES}
                row = {"split": split, "position": pos, "metric": m.name, "label": m.label,
                       "n": p["own"].n, "n_seasons": len(blocks)}  # fmt: skip
                for s in SOURCES:
                    lo, hi = _pct(boot[s])
                    row |= {f"r_{s}": r[s], f"lo_{s}": lo, f"hi_{s}": hi}
                lo, hi = _pct(boot["own"] - boot["ffopportunity"])
                row |= {"diff": r["own"] - r["ffopportunity"], "diff_lo": lo, "diff_hi": hi}
                # within seasons: each half's value minus its season's mean (a season-wide
                # offset of one source's xFP, e.g. a league trend it lags, is taken out)
                w = {s: _within(p[s], blocks) for s in SOURCES}
                for s in SOURCES:
                    row[f"r_within_{s}"] = float(_corr(w[s].sum(axis=0)))
                bw = {s: _corr(w[s][idx].sum(axis=1)) for s in SOURCES}
                lo, hi = _pct(bw["own"] - bw["ffopportunity"])
                rows.append(row | {"diff_within": row["r_within_own"]
                                   - row["r_within_ffopportunity"],
                                   "diff_within_lo": lo, "diff_within_hi": hi})  # fmt: skip
    return pl.from_dicts(rows, infer_schema_length=None)


def _within(p, blocks: list) -> np.ndarray:
    """Block statistics of the pairs after subtracting each season's mean from a and b."""
    a, b = p.a.astype(float).copy(), p.b.astype(float).copy()
    for s in blocks:
        m = p.seasons == s
        a[m] -= a[m].mean()
        b[m] -= b[m].mean()
    return _block_stats(a, b, p.seasons, blocks)


# --------------------------------------------------------------------------------------
# Leakage comparison 2: D3's projection backtest with each xFP source
# --------------------------------------------------------------------------------------

BT_METHODS = ("model", "baseline_ppg", "baseline_last3")


def backtest_metric_table(results: dict, weeks: str = "headline") -> pl.DataFrame:
    """D3's own metric rows (MAE and Spearman by position, each method and model - baseline,
    season-block intervals) of each source's backtest, side by side."""
    frames = []
    for src in SOURCES:
        m = pl.from_dicts(results[src].metrics, infer_schema_length=None).filter(
            pl.col("weeks") == weeks
        )
        frames.append(m.select("position", "metric", "method", "kind",
                               *(pl.col(c).alias(f"{c}_{src}") for c in ("value", "lo", "hi")),
                               pl.col("n").alias(f"n_{src}")))  # fmt: skip
    keys = ["position", "metric", "method", "kind"]
    return frames[0].join(frames[1], on=keys, how="full", coalesce=True)


def _graded(result, weeks: Sequence[int]) -> pl.DataFrame:
    return result.graded.filter(pl.col("evaluable") & pl.col("week").is_in(list(weeks)))


def model_mae_difference(results: dict, weeks: Sequence[int], *, n_boot: int = N_BOOT):
    """Own - ffopportunity MAE of the chosen projection on the player-weeks both graded, by
    position and pooled, with a paired season-block interval."""
    from twm.backtest.metrics import paired_block_bootstrap
    from twm.modules.regression_watch.backtest import ROW_KEYS

    keys = list(ROW_KEYS)
    err = {
        s: _graded(results[s], weeks).select(
            *keys, "position", (pl.col("pred_model") - pl.col("ros_ppg")).abs().alias("err")
        )
        for s in SOURCES
    }
    common = err["own"].select(keys).join(err["ffopportunity"].select(keys), on=keys)
    rows = []
    for pos in (*sorted(set(common.join(err["own"], on=keys)["position"])), POOLED):
        e = {s: err[s].join(common, on=keys).sort(keys) for s in SOURCES}
        if pos != POOLED:
            e = {s: v.filter(pl.col("position") == pos) for s, v in e.items()}
        iv = paired_block_bootstrap(e["own"], e["ffopportunity"], keys=keys, value="err",
                                    n_boot=n_boot)  # fmt: skip
        rows.append({"position": pos, "rows": e["own"].height,
                     "rows_only_own": err["own"].height - common.height,
                     "rows_only_ffo": err["ffopportunity"].height - common.height,
                     "mae_ffo": e["ffopportunity"]["err"].mean(), "mae_own": e["own"]["err"].mean(),
                     "diff": iv.value, "diff_lo": iv.lo, "diff_hi": iv.hi})  # fmt: skip
    return pl.from_dicts(rows, infer_schema_length=None)


def tag_comparison(results: dict, weeks: Sequence[int], *, n_boot: int = N_BOOT) -> pl.DataFrame:
    """Hit rates of the tagged players (Sell-high, Buy-low, Legit) with each source, by position
    and pooled: the tagged rows differ between sources, so the difference's season-block
    interval draws the SAME seasons for both and recomputes each pooled rate."""
    from twm.backtest.metrics import block_indices
    from twm.modules.regression_watch.tags import TAGS

    rows = []
    g = {s: _graded(results[s], weeks) for s in SOURCES}
    for tag in TAGS:
        for pos in ("QB", "RB", "WR", "TE", POOLED):
            t = {s: g[s].filter(pl.col(tag)) for s in SOURCES}
            if pos != POOLED:
                t = {s: v.filter(pl.col("position") == pos) for s, v in t.items()}
            blocks = sorted(set(t["own"]["season"]) | set(t["ffopportunity"]["season"]))
            idx = block_indices(len(blocks), n_boot).astype(int)
            row: dict[str, object] = {"tag": tag, "position": pos}
            boot = {}
            for s in SOURCES:
                per = (
                    t[s].group_by("season")
                    .agg(pl.col(f"hit_{tag}").cast(pl.Float64).sum().alias("h"), pl.len())
                )  # fmt: skip
                full = pl.DataFrame({"season": blocks}, schema={"season": per["season"].dtype})
                per = full.join(per, on="season", how="left").fill_null(0)
                h, n = per["h"].to_numpy(), per["len"].to_numpy().astype(float)
                with np.errstate(invalid="ignore", divide="ignore"):
                    boot[s] = h[idx].sum(axis=1) / n[idx].sum(axis=1) if len(blocks) else h
                rate = float(h.sum() / n.sum()) if n.sum() else None
                lo, hi = _pct(boot[s]) if len(blocks) else (None, None)
                row |= {f"n_{s}": int(n.sum()), f"rate_{s}": rate, f"lo_{s}": lo, f"hi_{s}": hi}
            lo, hi = _pct(boot["own"] - boot["ffopportunity"]) if len(blocks) else (None, None)
            ro, rf = row["rate_own"], row["rate_ffopportunity"]
            diff = ro - rf if ro is not None and rf is not None else None  # type: ignore[operator]
            rows.append(row | {"diff": diff, "diff_lo": lo, "diff_hi": hi})
    return pl.from_dicts(rows, infer_schema_length=None)


# --------------------------------------------------------------------------------------
# Everything together
# --------------------------------------------------------------------------------------


@dataclass
class OwnXfpReport:
    first_test: int
    last_fold: int
    study_seasons: tuple[int, ...]
    folds: pl.DataFrame  # one row per (fold, component): family, trees, validation losses
    per_play: pl.DataFrame
    player_week: pl.DataFrame
    stability: pl.DataFrame
    reliability: pl.DataFrame  # D2's shrinkage factor r(8) for FPOE/game, each source
    backtest: pl.DataFrame
    model_mae: pl.DataFrame
    tags: pl.DataFrame
    choices: pl.DataFrame  # D3's variant chosen per test season, each source
    seconds: dict[str, float]
    n_boot: int


def _reliability(frames: dict[str, pl.DataFrame], seasons: Sequence[int], g: int = 8):
    from twm.modules.regression_watch import stability as st

    out = []
    for src in SOURCES:
        t = st.shrinkage(seasons, frame=frames[src]).filter(pl.col("g") == g)
        out.append(t.select("position", "metric", pl.lit(src).alias("source"), "reliability",
                            "prior_mean", "n"))  # fmt: skip
    return pl.concat(out).sort(["metric", "position", "source"])


def _choices(results: dict) -> pl.DataFrame:
    rows = [
        {"source": s, "season": season, "variant": ch.variant.name, "sell_x": ch.sell.x,
         "buy_x": ch.buy.x}
        for s in SOURCES for season, ch in sorted(results[s].choices.items())
    ]  # fmt: skip
    return pl.from_dicts(rows, infer_schema_length=None)


def build_own_xfp_report(
    db, out_dir, *, last_fold: int, study_end: int, n_boot: int = N_BOOT, progress=None
) -> OwnXfpReport:
    """Read the fitted folds (:func:`own_xfp.run_folds`), save the own per-play and per-game
    tables under ``out_dir`` and compute every comparison (study seasons 2009 .. ``study_end``
    for D2 and D3, test seasons from 2011 as in D3)."""
    import time

    from twm.config import league
    from twm.modules.regression_watch import backtest as bt
    from twm.modules.regression_watch import stability as st
    from twm.modules.regression_watch.player_week import player_games_history

    say = progress or (lambda _m: None)
    secs: dict[str, float] = {}
    t = time.perf_counter()
    tests = list(range(ox.FIRST_TEST_SEASON, last_fold + 1))
    plays = {k: pl.read_parquet(out_dir / f"inputs_{k}.parquet") for k in ("pass", "rush")}
    preds = ox.load_predictions(out_dir, tests)
    for kind in ("pass", "rush"):
        preds[kind].write_parquet(out_dir / f"plays_{kind}.parquet")
    own_pg = ox.player_game_xfp(ox.own_tables(plays, preds))
    ffo_pg = ox.player_game_xfp({k: v.filter(pl.col("season").is_in(tests))
                                 for k, v in plays.items()})  # fmt: skip
    own_pg.write_parquet(out_dir / "player_games.parquet")
    say("per play ...")
    per_play = per_play_table(play_errors(plays, preds), n_boot=n_boot)
    frame = player_games_history(db, tests)
    pw = player_week_table(player_week_pairs(frame, ffo_pg, own_pg))
    secs["tables"] = round(time.perf_counter() - t, 1)
    t = time.perf_counter()
    say("stability (D2) with each source ...")
    study = list(range(st.FIRST_STUDY_SEASON, study_end + 1))
    frames = {
        "ffopportunity": st.load_frame(db, study),
        "own": st.load_frame(db, study, xfp=own_pg),
    }
    stab = stability_comparison(frames, study, n_boot=n_boot)
    rel = _reliability(frames, study)
    secs["stability"] = round(time.perf_counter() - t, 1)
    results = {}
    for src in SOURCES:
        t = time.perf_counter()
        say(f"projection backtest (D3) with {src} xFP ...")
        f, asofs = bt.load_inputs(db, study_end, xfp=own_pg if src == "own" else None)
        results[src] = bt.run_backtest(f, asofs, league(), last_season=study_end, n_boot=n_boot)
        secs[f"backtest_{src}"] = round(time.perf_counter() - t, 1)
    return OwnXfpReport(
        tests[0], last_fold, tuple(study), ox.load_fold_info(out_dir, tests), per_play, pw,
        stab, rel, backtest_metric_table(results),
        model_mae_difference(results, bt.HEADLINE_WEEKS, n_boot=n_boot),
        tag_comparison(results, bt.HEADLINE_WEEKS, n_boot=n_boot), _choices(results), secs,
        n_boot,
    )  # fmt: skip


# --------------------------------------------------------------------------------------
# Markdown + CSV
# --------------------------------------------------------------------------------------


def _f(x: object, d: int = 3) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "-"
    return f"{x:.{d}f}"


def _s(x: object, d: int = 3) -> str:
    """Signed."""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "-"
    return f"{x:+.{d}f}"


def _ci(lo: object, hi: object, d: int = 3) -> str:
    if lo is None or hi is None:
        return ""
    return f" ({_s(lo, d)} to {_s(hi, d)})"


def _table(header: Sequence[str], rows: list[Sequence[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return out + ["| " + " | ".join(r) + " |" for r in rows]


DIGITS = {"log_loss": 4, "brier": 4, "mae": 3, "mse": 2}
PRIMARY = {"binary": "log_loss", "rate": "log_loss", "regression": "mse"}


def _per_play_md(r: OwnXfpReport) -> list[str]:
    p = r.per_play.filter(pl.col("season") == POOLED)
    rows = []
    for c in ox.COMPONENTS:
        for m in p.filter(pl.col("component") == c.name).iter_rows(named=True):
            d = DIGITS[m["metric"]]
            rows.append(
                [
                    c.title,
                    f"{m['plays']:,}",
                    m["metric"].replace("_", " "),
                    _f(m["ffo"], d),
                    _f(m["own"], d),
                    _s(m["diff"], d) + _ci(m["diff_lo"], m["diff_hi"], d),
                    " / ".join(f"{m[k]:.3f}" for k in ("mean_y", "mean_ffo", "mean_own")),
                ]
            )
    head = ["component", "plays", "metric", "ffopportunity", "own", "own - ffo (95% interval)",
            "mean actual / ffo / own"]  # fmt: skip
    labels = set(r.per_play["season"]) - {POOLED}
    seasons = sorted(x for x in labels if "-" not in x)
    wins = [f"{a}-{b}" for a, b in WINDOWS if f"{a}-{b}" in labels]
    win_rows = []
    for c in ox.COMPONENTS:
        metric = PRIMARY[c.objective]
        cells = [c.name, metric.replace("_", " ")]
        for w in wins:
            m = _row(r.per_play, component=c.name, metric=metric, season=w)
            d = DIGITS[metric]
            cells.append(_s(m["diff"], d) + _ci(m["diff_lo"], m["diff_hi"], d) if m else "-")
        win_rows.append(cells)
    by = []
    for s in seasons:
        q = r.per_play.filter(pl.col("season") == s)
        cells = []
        for c in ox.COMPONENTS:
            m = q.filter((pl.col("component") == c.name)
                         & (pl.col("metric") == PRIMARY[c.objective]))  # fmt: skip
            cells.append(_s(m["diff"][0], DIGITS[PRIMARY[c.objective]]) if m.height else "-")
        by.append([s, *cells])
    return [
        *_table(head, rows), "",
        "Own minus ffopportunity by season (log loss for chances, squared error for yards; "
        "negative = own closer):", "",
        *_table(["season", *(c.name for c in ox.COMPONENTS)], by), "",
        "Own minus ffopportunity by season window (95% season-block intervals):", "",
        *_table(["component", "metric", *wins], win_rows),
    ]  # fmt: skip


def _player_week_md(r: OwnXfpReport) -> list[str]:
    pw = r.player_week
    rows = []
    for what in ("xfp", "xfp_ng"):
        for m in pw.filter((pl.col("season") == POOLED) & (pl.col("what") == what)).iter_rows(
            named=True
        ):
            rows.append(
                [
                    m["position"],
                    "all plays" if what == "xfp" else "no garbage time",
                    f"{m['games']:,}",
                    _f(m["mean_ffo"], 2),
                    _f(m["mean_own"], 2),
                    _s(m["mean_diff"], 2),
                    _f(m["mean_abs_diff"], 2),
                    _f(m["corr"], 3),
                ]
            )
    head = ["position", "xFP", "player-games", "mean ffo", "mean own", "mean own - ffo",
            "mean abs diff", "correlation"]  # fmt: skip
    positions = [p for p in ("QB", "RB", "WR", "TE") if p in set(pw["position"])]
    by = []
    for s in sorted(set(pw["season"]) - {POOLED}):
        q = pw.filter((pl.col("season") == s) & (pl.col("what") == "xfp"))
        cells = []
        for pos in positions:
            m = q.filter(pl.col("position") == pos)
            cells.append(f"{_f(m['corr'][0], 3)} / {_s(m['mean_diff'][0], 2)}" if m.height else "-")
        by.append([s, *cells])
    return [
        *_table(head, rows), "",
        "By season (xFP, all plays): correlation / mean own - ffo per player-game:", "",
        *_table(["season", *positions], by),
    ]  # fmt: skip


STUDY_METRICS = ("xfp", "fpoe", "xfp_ng", "fpoe_ng", "td_rate_over_expected",
                 "catch_rate_over_expected", "completion_rate_over_expected",
                 "yac_over_expected")  # fmt: skip


def _stability_md(r: OwnXfpReport) -> list[str]:
    rows = []
    t = r.stability.filter(pl.col("split") == "odd_even")
    for metric in STUDY_METRICS:
        for m in t.filter(pl.col("metric") == metric).iter_rows(named=True):
            rows.append(
                [
                    m["label"],
                    m["position"],
                    f"{m['n']:,}",
                    _f(m["r_ffopportunity"]) + _ci(m["lo_ffopportunity"], m["hi_ffopportunity"]),
                    _f(m["r_own"]) + _ci(m["lo_own"], m["hi_own"]),
                    _s(m["diff"]) + _ci(m["diff_lo"], m["diff_hi"]),
                    _s(m["diff_within"]) + _ci(m["diff_within_lo"], m["diff_within_hi"]),
                ]
            )
    rel = []
    for pos in ("QB", "RB", "WR", "TE"):
        q = r.reliability.filter((pl.col("metric") == "fpoe") & (pl.col("position") == pos))
        v = {row["source"]: row for row in q.iter_rows(named=True)}
        if v:
            rel.append([pos, _f(v["ffopportunity"]["reliability"]), _f(v["own"]["reliability"]),
                        _s(v["ffopportunity"]["prior_mean"], 2),
                        _s(v["own"]["prior_mean"], 2)])  # fmt: skip
    return [
        *_table(["metric", "position", "player-seasons", "r ffopportunity", "r own",
                 "own - ffo", "own - ffo within seasons"], rows), "",
        "D2's FPOE/game shrinkage factor after 8 games, r(8) (what D3 multiplies FPOE by), and "
        "the average FPOE/game it shrinks toward:", "",
        *_table(["position", "r(8) ffo", "r(8) own", "mean FPOE ffo", "mean FPOE own"], rel),
    ]  # fmt: skip


def _backtest_md(r: OwnXfpReport) -> list[str]:
    b = r.backtest.filter(pl.col("metric") == "mae")
    rows = []
    for pos in ("QB", "RB", "WR", "TE", POOLED):
        q = b.filter(pl.col("position") == pos)
        mm = r.model_mae.filter(pl.col("position") == pos)
        if not q.height or not mm.height:
            continue
        cells = [pos, f"{mm['rows'][0]:,}"]
        for method in ("model", "baseline_ppg"):
            v = q.filter((pl.col("method") == method) & (pl.col("kind") == "value"))
            cells.append(f"{_f(v['value_ffopportunity'][0])} / {_f(v['value_own'][0])}")
        d = mm.row(0, named=True)
        cells.append(_s(d["diff"]) + _ci(d["diff_lo"], d["diff_hi"]))
        for src in SOURCES:
            v = q.filter(pl.col("method") == "model-baseline_ppg").row(0, named=True)
            cells.append(_s(v[f"value_{src}"]) + _ci(v[f"lo_{src}"], v[f"hi_{src}"]))
        rows.append(cells)
    tags = []
    for m in r.tags.filter(pl.col("position") == POOLED).iter_rows(named=True):
        tags.append([m["tag"].replace("_", "-"),
                     *(f"{m[f'n_{s}']:,}: {_f(m[f'rate_{s}'])}" + _ci(m[f"lo_{s}"], m[f"hi_{s}"])
                       for s in SOURCES),
                     _s(m["diff"]) + _ci(m["diff_lo"], m["diff_hi"])])  # fmt: skip
    ch = r.choices.pivot(on="source", index="season", values="variant")
    same = int((ch["ffopportunity"] == ch["own"]).sum())
    return [
        "MAE in points per game over the rest of the season. MAE columns: each backtest on its "
        "own graded player-weeks; own - ffo: on the player-weeks both graded (count shown).", "",
        *_table(["position", "player-weeks both", "MAE model ffo / own", "MAE season-to-date PPG",
                 "MAE own - ffo", "model - PPG (ffo)", "model - PPG (own)"], rows), "",
        "Tag hit rates (tagged player-weeks: hit rate; Legit is no longer shown in the product, "
        "step R1, and is kept here for completeness):", "",
        *_table(["tag", "ffopportunity", "own", "own - ffo"], tags), "",
        f"The variant chosen on earlier seasons is the same with both sources in {same} of "
        f"{ch.height} test seasons (per season in the CSV).",
    ]  # fmt: skip


def _folds_md(r: OwnXfpReport) -> list[str]:
    rows = []
    for c in ox.COMPONENTS:
        f = r.folds.filter(pl.col("component") == c.name)
        fam = f.group_by("family").len().sort("family")
        trees = f.filter(pl.col("family") == "lgbm")["n_trees"]
        rows.append([c.name, c.column, ", ".join(f"{a} {n}" for a, n in fam.iter_rows()),
                     f"{int(trees.median())}" if len(trees) else "-",
                     f"{f['n_train'].max():,}"])  # fmt: skip
    head = ["component", "replaces", "family (number of folds)", "median trees (LightGBM)",
            "training plays (last fold)"]  # fmt: skip
    return _table(head, rows)


def _row(df: pl.DataFrame, **eq: object) -> dict | None:
    q = df
    for k, v in eq.items():
        q = q.filter(pl.col(k) == v)
    return q.row(0, named=True) if q.height else None


def recommendation(r: OwnXfpReport) -> tuple[bool, list[str]]:
    """(switch?, reasons): switch when the own xFP is no worse where it matters to the user, i.e.
    D3's pooled projection MAE is not higher (interval not above 0) and neither the Sell-high
    nor the Buy-low hit rate is lower (interval not below 0)."""
    mae = _row(r.model_mae, position=POOLED)
    tags = {t: _row(r.tags, tag=t, position=POOLED) for t in ("sell_high", "buy_low")}
    mae_ok = mae is not None and mae["diff_lo"] is not None and mae["diff_lo"] <= 0
    tags_ok = all(t is not None and t["diff_hi"] is not None and t["diff_hi"] >= 0
                  for t in tags.values())  # fmt: skip
    why = []
    if mae is not None:
        ci = _ci(mae["diff_lo"], mae["diff_hi"])
        why.append(f"projection MAE own - ffopportunity {_s(mae['diff'])} points/game{ci} "
                   f"on {mae['rows']:,} player-weeks")  # fmt: skip
    for t, v in tags.items():
        if v is not None:
            why.append(f"{t.replace('_', '-')} hit rate own - ffopportunity {_s(v['diff'])}"
                       f"{_ci(v['diff_lo'], v['diff_hi'])}")  # fmt: skip
    return bool(mae_ok and tags_ok), why


def _summary(r: OwnXfpReport) -> list[str]:
    switch, why = recommendation(r)
    worse = r.per_play.filter(
        (pl.col("season") == POOLED) & pl.col("metric").is_in(["log_loss", "mse"])
        & (pl.col("diff_lo") > 0)
    )["component"].to_list()  # fmt: skip
    better = r.per_play.filter(
        (pl.col("season") == POOLED) & pl.col("metric").is_in(["log_loss", "mse"])
        & (pl.col("diff_hi") < 0)
    )["component"].to_list()  # fmt: skip
    corr = _row(r.player_week, position=POOLED, season=POOLED, what="xfp")
    lines = [
        f"- **Per play** (test seasons {r.first_test}-{r.last_fold}): ffopportunity is closer "
        f"for {', '.join(worse) or 'no component'}; the own model is closer for "
        f"{', '.join(better) or 'no component'} (season-block intervals of the pooled "
        "difference exclude 0). ffopportunity's models saw later seasons, so this flatters it.",
    ]
    if corr is not None:
        lines.append(
            f"- **Per player-game**: own and ffopportunity xFP correlate {corr['corr']:.3f}; "
            f"own is {_s(corr['mean_diff'], 2)} xFP per player-game on average."
        )
    st = r.stability.filter((pl.col("split") == "odd_even") & (pl.col("metric") == "fpoe"))
    parts = [f"{m['position']} {m['r_ffopportunity']:.2f} -> {m['r_own']:.2f} "
             f"({_s(m['diff'], 2)}{_ci(m['diff_lo'], m['diff_hi'], 2)})"
             for m in st.iter_rows(named=True)]  # fmt: skip
    if parts:
        up = st.height and (st["diff_lo"] > 0).all() and (st["diff_within_lo"] > 0).all()
        reading = (
            "with the own xFP more of FPOE repeats at every position, also within seasons, so "
            "D2's shrinkage factors (and D3's projections) give FPOE more weight"
            if up else "the per-position intervals are in the D2 table below"
        )  # fmt: skip
        lines.append(
            "- **Stability (D2)**: FPOE/game split-half r (odd/even games), ffopportunity -> "
            f"own: {'; '.join(parts)}; {reading}."
        )
    lines.append("- **Leakage (6.3), D3 backtest**: " + "; ".join(why) + ".")
    verdict = (
        "switch Regression Watch to the own walk-forward xFP" if switch
        else "keep ffopportunity's xFP for now"
    )  # fmt: skip
    lines.append(
        f"- **Recommendation: {verdict}** (rule: D3's pooled projection MAE not worse and "
        "neither tag's hit rate worse, by the season-block intervals). Nothing switches here: "
        "the production pin, the published lists and the site are unchanged; switching is "
        "the owner's decision (then rerun D2/D3 and pin through `twm regression pin`)."
    )
    return lines


NOTES = (
    "ffopportunity's models were trained on many seasons, including seasons after some of "
    "these plays (PROJECT_SPEC 6.3); the own models never saw their test season. The per-play "
    "comparison therefore favours ffopportunity; the table by season window shows where its "
    "advantage is largest.",
    "The own expectations learn the league's level from earlier seasons, so they lag trends "
    "(completion rates rising, interception rates falling): mean predictions vs actual are in "
    "the per-play table and by season in the CSV.",
    "Features: the opportunity and the situation before the snap only; no nflfastR model "
    "column (epa, wp, xpass, cpoe, xyac) and not the garbage-time flag (it is built from "
    "nflfastR's wp). Kneels (-1 yard) and aborted snaps (0) keep ffopportunity's fixed values.",
    "2006-2008: the targets of incomplete passes are missing upstream (docs/regression_watch.md),"
    " so receiver xFP of those seasons is unusable with either source; D2 and D3 start in 2009.",
    "D3's universe ranks players by PPG or xFP, so the two backtests grade slightly different "
    "player-weeks; the own - ffopportunity MAE is on the player-weeks both graded.",
    "Report only: the production pin (config/production_models.yaml), the published lists and "
    "the site are unchanged; the own features are not in the feature registry (they would be "
    "registered if the owner switches).",
)


def report_markdown(r: OwnXfpReport, built_at: str) -> str:
    lines = [
        "# Regression Watch: own walk-forward xFP vs ffopportunity (step H6-b)", "",
        f"Warehouse built {built_at}. Own models: one fold per test season "
        f"{r.first_test}-{r.last_fold}, trained on 2006 .. season - 1 only (the "
        f"{r.last_fold} fold is the live model). Intervals: 95%, {r.n_boot} season-block "
        "resamples (the same seasons drawn for both sources). Command: "
        "`uv run twm regression own-xfp`.", "",
        "## Summary", "", *_summary(r), "",
        "## The own models", "", *_folds_md(r), "",
        "## 1. Per play: the same plays, own vs ffopportunity", "", *_per_play_md(r), "",
        "## 2. Per player-game: own xFP vs ffopportunity's (point-in-time position)", "",
        *_player_week_md(r), "",
        "## 3. Leakage comparison (PROJECT_SPEC 6.3)", "",
        f"### D2 stability study (seasons {r.study_seasons[0]}-{r.study_seasons[-1]}, odd/even "
        "games; first/second half in the CSV)", "",
        "Within seasons: each half's value minus its season's mean, so a season-wide offset of "
        "one source does not count as a stable player trait.", "",
        *_stability_md(r), "",
        "### D3 projection backtest (test seasons from 2011, headline weeks 4/6/8/10)", "",
        *_backtest_md(r), "",
        "## Notes", "", *(f"- {n}" for n in NOTES),
    ]  # fmt: skip
    return "\n".join(lines) + "\n"


def report_csv(r: OwnXfpReport) -> pl.DataFrame:
    """Every table in one long CSV (column ``table`` names it; run times left out)."""
    parts = {
        "folds": r.folds.drop("fold_seconds", strict=False),
        "per_play": r.per_play,
        "player_week": r.player_week,
        "stability": r.stability,
        "reliability_r8": r.reliability,
        "backtest_metrics": r.backtest,
        "backtest_model_mae_own_minus_ffo": r.model_mae,
        "tags": r.tags,
        "variant_choices": r.choices,
    }
    return pl.concat(
        [t.with_columns(pl.lit(name).alias("table")).select("table", pl.exclude("table"))
         for name, t in parts.items()],
        how="diagonal_relaxed",
    )  # fmt: skip


def write_own_xfp_report(r: OwnXfpReport, target, built_at: str):
    """Write the markdown to ``target`` and the CSV next to it; returns the CSV path."""
    from pathlib import Path

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report_markdown(r, built_at))
    csv_path = target.with_suffix(".csv")
    report_csv(r).with_columns(pl.col(pl.Float64).round(6)).write_csv(csv_path)
    return csv_path
