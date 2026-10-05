"""'If he plays': does a tagged player who plays score like usual? (docs/questionable.md)

For a tagged player who played and had at least :data:`MIN_PRIOR_GAMES` earlier games with an
offensive snap that season, averaging at least :data:`MIN_PRIOR_PPG` points (fantasy-relevant):

    ratio = that week's points / his season-to-date points per game before the week

Any player's ratio drifts below 1 on average (regression to the mean: a high average so far
partly is luck), so the tagged ratio is compared with **healthy** players: the same ratio for
players on no injury report that week, matched on season, week, position and season-to-date
points-per-game band (:data:`PPG_BANDS`). Each healthy row in a cell is weighted by
(tagged rows in the cell / healthy rows in the cell), so the healthy pool has the tagged
bucket's exact mix. Published per bucket: the median ratio and the "dud rate" (the share
below :data:`DUD` = half his usual), tagged vs healthy.
"""

from __future__ import annotations

from collections.abc import Sequence

import polars as pl

MIN_PRIOR_GAMES = 2
MIN_PRIOR_PPG = 5.0
DUD = 0.5
PPG_BANDS = (5.0, 8.0, 11.0, 14.0, 18.0)  # lower edges: 5-8, 8-11, 11-14, 14-18, 18+
CELL = ("season", "week", "position", "band")
# The "if he plays" buckets: per status, and per status and whether he missed his team's
# previous game (not practice: 2025+ practice statuses are not comparable, docs/questionable.md)
BY = "missed_prev"
ALL = "all"  # the value of a status's overall line
MIN_BUCKET_N = 30  # a bucket with fewer qualifying rows shows its status's line (or none)


def eligible(rows: pl.DataFrame) -> pl.DataFrame:
    """Rows that played with enough earlier games and points: + ratio and band columns."""
    out = rows.filter(
        pl.col("points").is_not_null()
        & (pl.col("prior_games") >= MIN_PRIOR_GAMES)
        & (pl.col("prior_ppg") >= MIN_PRIOR_PPG)
    )
    band = pl.lit(0, dtype=pl.Int32)
    for i, edge in enumerate(PPG_BANDS[1:], start=1):
        band = pl.when(pl.col("prior_ppg") >= edge).then(pl.lit(i, dtype=pl.Int32)).otherwise(band)
    return out.with_columns((pl.col("points") / pl.col("prior_ppg")).alias("ratio"),
                            band.alias("band"))  # fmt: skip


def weighted_median(values: Sequence[float], weights: Sequence[float]) -> float | None:
    """The smallest value whose cumulative weight reaches half the total (None if empty)."""
    pairs = sorted((float(v), float(w)) for v, w in zip(values, weights, strict=True) if w > 0)
    total = sum(w for _, w in pairs)
    if not pairs or total <= 0:
        return None
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= total / 2:
            return v
    return pairs[-1][0]  # pragma: no cover - float rounding only


def compare(tagged: pl.DataFrame, healthy: pl.DataFrame) -> dict[str, float | int | None]:
    """One bucket: tagged n / median / dud rate, and the matched healthy median / dud rate
    (``matched``: tagged rows whose cell has healthy rows; the others are left out of the
    healthy weights)."""
    t = tagged.get_column("ratio")
    out: dict[str, float | int | None] = {
        "n": tagged.height,
        "median": float(t.median()) if tagged.height else None,  # type: ignore[arg-type]
        "dud_rate": float((t < DUD).mean()) if tagged.height else None,  # type: ignore[arg-type]
    }
    need = tagged.group_by(list(CELL)).agg(pl.len().alias("_t"))
    have = healthy.group_by(list(CELL)).agg(pl.len().alias("_h"))
    w = healthy.join(need, on=list(CELL), how="inner").join(have, on=list(CELL), how="inner")
    w = w.with_columns((pl.col("_t") / pl.col("_h")).alias("_w"))
    matched = int(need.join(have, on=list(CELL), how="inner").get_column("_t").sum())
    ws = w.get_column("_w")
    total = float(ws.sum()) if w.height else 0.0
    out["matched"] = matched
    out["healthy_rows"] = w.height
    out["healthy_median"] = weighted_median(w.get_column("ratio").to_list(), ws.to_list())
    dud = (w.get_column("ratio") < DUD).cast(pl.Float64) * ws
    out["healthy_dud_rate"] = float(dud.sum()) / total if total > 0 else None
    return out


def bucket_table(tagged: pl.DataFrame, played: pl.DataFrame, by: str = BY) -> pl.DataFrame:
    """Per status overall (``value`` 'all') and per value of ``by`` within the status: the
    :func:`compare` numbers. ``tagged``: history rows (only modelled statuses that played
    and qualify are used); ``played``: :func:`.history.read_played` rows."""
    from twm.modules.questionable.history import MODELLED

    t = eligible(tagged.filter(pl.col("report_status").is_in(MODELLED) & pl.col("played")))
    t = t.with_columns(pl.col(by).cast(pl.String).alias("_v"))
    h = eligible(played.filter(~pl.col("on_report")))
    rows = []
    for status in MODELLED:
        ts = t.filter(pl.col("report_status") == status)
        for value in [ALL, *sorted(ts.get_column("_v").drop_nulls().unique().to_list())]:
            tv = ts if value == ALL else ts.filter(pl.col("_v") == value)
            rows.append({"report_status": status, "key": by, "value": value, **compare(tv, h)})
    schema = {"report_status": pl.String, "key": pl.String, "value": pl.String, "n": pl.Int64,
              "median": pl.Float64, "dud_rate": pl.Float64, "matched": pl.Int64,
              "healthy_rows": pl.Int64, "healthy_median": pl.Float64,
              "healthy_dud_rate": pl.Float64}  # fmt: skip
    return pl.DataFrame(rows, schema=schema)
