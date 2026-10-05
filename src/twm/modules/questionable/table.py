"""The lookup table: groupings, shrunk cell rates, the walk-forward backtest and calibration.

A grouping is the game status plus a tuple of keys (``practice`` first). Its cells are fit
level by level: the status cell's rate is the plain share of its training rows that played;
every deeper cell is shrunk toward its parent cell (the same keys without the last one):

    rate = (played + K * parent_rate) / (n + K),   K = :data:`PSEUDO_COUNT` (fixed, not tuned)

so a cell with few rows stays close to its parent and a large one keeps its own share. A row
whose cell was never seen gets the deepest cell it has (its parent, else the status rate).

The backtest is walk-forward: season s is scored by a table fit on 2016..s-1 only
(:data:`TEST_SEASONS`), on log loss and Brier, against the baseline "overall rate for the
status". :func:`choose` applies the rule fixed before the backtest was run (docs/questionable.md).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import polars as pl

from twm.modules.questionable.history import FIRST_SEASON, MODELLED

PSEUDO_COUNT = 20.0  # K: a cell of 20 rows sits halfway between its own share and its parent's
TEST_SEASONS = tuple(range(2018, 2026))
EPS = 1e-6  # probabilities are clipped to [EPS, 1 - EPS] for the log loss
# A grouping is named by its keys after the status, joined by '+' ("status" = no keys: the
# baseline). The four groupings of the brief, (a)-(d), and the keys :func:`select` may add.
BASELINE = "status"
LETTERS = {"practice": "a", "practice+position": "b", "practice+missed_prev": "c",
           "practice+body_part": "d"}  # fmt: skip
EXTRA_KEYS = ("missed_prev", "position", "body_part")
KEY_TITLES = {
    "practice": "practice status",
    "missed_prev": "missed his team's previous game",
    "position": "position",
    "body_part": "injury body part (top 8, rest 'other')",
}
CHANCE_BUCKETS = ((0.0, 0.30, "<30%"), (0.30, 0.50, "30-50%"), (0.50, 0.70, "50-70%"),
                  (0.70, 0.85, "70-85%"), (0.85, 1.01, "85%+"))  # fmt: skip
SEASON_WINS_NEEDED = 6  # of the 8 test seasons (select(): "wins clearly")
# The first season of nflverse's new injury source (no date_modified; practice statuses not
# comparable with 2016-2024: docs/questionable.md). select() keeps practice only if it helps here.
CHECK_SEASON = 2025


@dataclass(frozen=True)
class Table:
    """A fitted grouping: ``levels[i]`` holds the cells of ``("report_status", *keys[:i])``
    with columns n, played, rate (shrunk)."""

    grouping: str
    keys: tuple[str, ...]
    levels: tuple[pl.DataFrame, ...]
    pseudo_count: float = PSEUDO_COUNT

    def cols(self, depth: int) -> list[str]:
        return ["report_status", *self.keys[:depth]]


def keys_of(grouping: str) -> tuple[str, ...]:
    """'status' -> (); 'practice+missed_prev' -> ('practice', 'missed_prev')."""
    return () if grouping == BASELINE else tuple(grouping.split("+"))


def title(grouping: str) -> str:
    """'(c) status x practice status x missed his team's previous game'."""
    if grouping == BASELINE:
        return "baseline: overall rate for the status"
    letter = f"({LETTERS[grouping]}) " if grouping in LETTERS else ""
    return letter + " x ".join(["status", *(KEY_TITLES[k] for k in keys_of(grouping))])


def modelled(rows: pl.DataFrame) -> pl.DataFrame:
    """The rows the table models: Questionable or Doubtful, from :data:`FIRST_SEASON`."""
    return rows.filter(pl.col("report_status").is_in(MODELLED) & (pl.col("season") >= FIRST_SEASON))


def fit(rows: pl.DataFrame, grouping: str, k: float = PSEUDO_COUNT) -> Table:
    """The grouping's cells from ``rows`` (training rows only: the caller's split)."""
    keys = keys_of(grouping)
    rows = modelled(rows).with_columns(pl.col("played").cast(pl.Float64).alias("_y"))
    levels: list[pl.DataFrame] = []
    for depth in range(len(keys) + 1):
        cols = ["report_status", *keys[:depth]]
        cells = rows.group_by(cols).agg(pl.len().alias("n"), pl.col("_y").sum().alias("played"))
        if depth == 0:
            cells = cells.with_columns((pl.col("played") / pl.col("n")).alias("rate"))
        else:
            parent = levels[-1].select([*cols[:-1], pl.col("rate").alias("_parent")])
            cells = cells.join(parent, on=cols[:-1], how="left").with_columns(
                ((pl.col("played") + k * pl.col("_parent")) / (pl.col("n") + k)).alias("rate")
            ).drop("_parent")  # fmt: skip
        levels.append(cells.sort(cols))
    return Table(grouping, keys, tuple(levels), k)


def predict(table: Table, rows: pl.DataFrame) -> pl.Series:
    """Each row's chance he plays: the rate of the deepest cell of his that the table has
    (a status the table never saw gets null)."""
    base = rows.with_row_index("_i")
    out = base.select("_i", pl.lit(None, dtype=pl.Float64).alias("p"))
    for depth, cells in enumerate(table.levels):
        cols = table.cols(depth)
        hit = base.select(["_i", *cols]).join(cells.select([*cols, "rate"]), on=cols, how="inner",
                                              nulls_equal=True)  # fmt: skip
        out = out.join(hit.select("_i", "rate"), on="_i", how="left").with_columns(
            pl.coalesce("rate", "p").alias("p")
        ).drop("rate")  # fmt: skip
    return out.sort("_i").get_column("p")


def log_loss(y: Sequence[bool] | pl.Series, p: Sequence[float] | pl.Series) -> float:
    ys, ps = list(y), [min(max(float(x), EPS), 1 - EPS) for x in p]
    if not ys:
        return float("nan")
    terms = (math.log(q) if t else math.log(1 - q) for t, q in zip(ys, ps, strict=True))
    return -sum(terms) / len(ys)


def brier(y: Sequence[bool] | pl.Series, p: Sequence[float] | pl.Series) -> float:
    ys, ps = list(y), list(p)
    if not ys:
        return float("nan")
    return sum((float(q) - float(t)) ** 2 for t, q in zip(ys, ps, strict=True)) / len(ys)


def walk_forward(
    rows: pl.DataFrame, grouping: str, test_seasons: Sequence[int] = TEST_SEASONS
) -> pl.DataFrame:
    """Every test-season row scored by a table fit on the seasons before it (2016..s-1):
    season, week, gsis_id, report_status, played, p (and the grouping's keys)."""
    rows = modelled(rows)
    keep = ["season", "week", "gsis_id", "report_status", "practice", *EXTRA_KEYS, "played"]
    keep = list(dict.fromkeys(c for c in keep if c in rows.columns))
    out = []
    for s in test_seasons:
        train = rows.filter(pl.col("season") < s)
        test = rows.filter(pl.col("season") == s)
        if test.is_empty() or train.is_empty():
            continue
        p = predict(fit(train, grouping), test)
        out.append(test.select(keep).with_columns(p.alias("p"), pl.lit(grouping).alias("grouping")))
    return pl.concat(out) if out else pl.DataFrame()


def season_metrics(scored: pl.DataFrame) -> pl.DataFrame:
    """Per test season and pooled ('all'): n, log loss, Brier, mean chance, played share."""

    def row(season: str, df: pl.DataFrame) -> dict:
        y, p = df.get_column("played"), df.get_column("p")
        return {"season": season, "n": df.height, "log_loss": log_loss(y, p),
                "brier": brier(y, p), "mean_p": float(p.mean()),  # type: ignore[arg-type]
                "played_rate": float(y.cast(pl.Float64).mean())}  # type: ignore[arg-type]  # fmt: skip

    seasons = sorted(scored.get_column("season").unique().to_list())
    rows = [row(str(s), scored.filter(pl.col("season") == s)) for s in seasons]
    return pl.DataFrame([*rows, row("all", scored)])


def backtest(
    rows: pl.DataFrame,
    groupings: Sequence[str] = (BASELINE, *LETTERS),
    test_seasons: Sequence[int] = TEST_SEASONS,
) -> pl.DataFrame:
    """The walk-forward metrics of every grouping (one frame: grouping, season, ...)."""
    out = [season_metrics(walk_forward(rows, g, test_seasons)).with_columns(
               pl.lit(g).alias("grouping")) for g in groupings]  # fmt: skip
    return pl.concat(out).select("grouping", "season", "n", "log_loss", "brier", "mean_p",
                                 "played_rate")  # fmt: skip


def calibration(scored: pl.DataFrame) -> pl.DataFrame:
    """Walk-forward chance buckets: predicted (mean chance) vs actual (played share), counts."""
    out = []
    for lo, hi, name in CHANCE_BUCKETS:
        b = scored.filter((pl.col("p") >= lo) & (pl.col("p") < hi))
        out.append({"bucket": name, "n": b.height,
                    "predicted": float(b["p"].mean()) if b.height else None,  # type: ignore[arg-type]
                    "actual": float(b["played"].cast(pl.Float64).mean()) if b.height else None})  # type: ignore[arg-type]  # fmt: skip
    return pl.DataFrame(out, schema={"bucket": pl.String, "n": pl.Int64,
                                     "predicted": pl.Float64, "actual": pl.Float64})  # fmt: skip


def wins_clearly(child: pl.DataFrame, parent: pl.DataFrame) -> bool:
    """The rule fixed before the backtest ran: a richer grouping wins clearly over the one it
    refines when its pooled log loss AND pooled Brier are lower AND its log loss is lower in
    at least :data:`SEASON_WINS_NEEDED` of the test seasons (frames of :func:`season_metrics`)."""
    c = {r["season"]: r for r in child.iter_rows(named=True)}
    p = {r["season"]: r for r in parent.iter_rows(named=True)}
    if c["all"]["log_loss"] >= p["all"]["log_loss"] or c["all"]["brier"] >= p["all"]["brier"]:
        return False
    won = sum(1 for s in c if s != "all" and s in p and c[s]["log_loss"] < p[s]["log_loss"])
    return won >= SEASON_WINS_NEEDED


@dataclass(frozen=True)
class Selection:
    chosen: str
    path: tuple[str, ...]  # status -> ... -> chosen (each step won clearly over the one before)
    metrics: pl.DataFrame  # every grouping evaluated: grouping, season, n, log_loss, ...
    with_practice: str = ""  # the forward path's end when practice may be used
    practice_dropped: bool = False  # it lost to the practice-free grouping on CHECK_SEASON


def _forward(start: list[str], pool: Sequence[str], metrics: Any) -> list[str]:
    path = list(start)
    while True:
        cur = path[-1]
        kids = [f"{cur}+{k}" if cur != BASELINE else k for k in pool if k not in keys_of(cur)]
        winners = [g for g in kids if wins_clearly(metrics(g), metrics(cur))]
        if not winners:
            return path
        pooled = {g: metrics(g).filter(pl.col("season") == "all")["log_loss"][0] for g in winners}
        path.append(min(winners, key=lambda g: (pooled[g], g)))


def _season_row(m: pl.DataFrame, season: int) -> dict[str, Any] | None:
    rows = m.filter(pl.col("season") == str(season)).to_dicts()
    return rows[0] if rows else None


def select(rows: pl.DataFrame, test_seasons: Sequence[int] = TEST_SEASONS) -> Selection:
    """Forward selection with :func:`wins_clearly` (docs/questionable.md, "Choosing"):

    1. (a) must beat the status baseline; then, while a one-key refinement of the current
       grouping wins clearly, keep the one with the lowest pooled log loss (with practice);
    2. the same from the status baseline over :data:`EXTRA_KEYS` only (practice-free);
    3. practice is kept only if the grouping of 1 beats the grouping of 2 on BOTH log loss and
       Brier in :data:`CHECK_SEASON` (nflverse's 2025+ injury source: its practice statuses
       are not comparable with 2016-2024's), else the practice-free grouping is chosen.

    (a)-(d) are always evaluated and returned with every other grouping evaluated."""
    done: dict[str, pl.DataFrame] = {}

    def metrics(g: str) -> pl.DataFrame:
        if g not in done:
            done[g] = season_metrics(walk_forward(rows, g, test_seasons))
        return done[g]

    for g in (BASELINE, *LETTERS):
        metrics(g)
    with_p = [BASELINE]
    if wins_clearly(metrics("practice"), metrics(BASELINE)):
        with_p = _forward([BASELINE, "practice"], EXTRA_KEYS, metrics)
    free = _forward([BASELINE], EXTRA_KEYS, metrics)
    path, dropped = with_p, False
    if "practice" in keys_of(with_p[-1]):
        a, b = (
            _season_row(metrics(with_p[-1]), CHECK_SEASON),
            _season_row(metrics(free[-1]), CHECK_SEASON),
        )
        keep = a is None or b is None or (a["log_loss"] < b["log_loss"] and a["brier"] < b["brier"])
        path, dropped = (with_p, False) if keep else (free, True)
    frames = [m.with_columns(pl.lit(g).alias("grouping")) for g, m in done.items()]
    out = pl.concat(frames).select("grouping", "season", "n", "log_loss", "brier", "mean_p",
                                   "played_rate")  # fmt: skip
    return Selection(path[-1], tuple(path), out, with_p[-1], dropped)
