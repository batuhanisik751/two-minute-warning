"""Made-up coach-tendency frames for `twm publish` tests (feature #10), shaped as
:func:`twm.modules.coach_tendencies.build.build` returns them, added to the Waiver Radar's
synthetic publish data (tests/publish_synthetic.py; its teams BUF, DAL, KC, SF). Deterministic.

Three warehouse coaches: 'pat_example' (BUF 2025 and 2026), 'jo_sample' (DAL 2025, then KC
2026) and 'renee_darcy', named "Renée D'Arcy": his site id is the slug of the name,
'renee-d-arcy', not the warehouse id. 2026 is the season in progress (through week 3).
``nan=True`` puts NaN where tiny inputs give it (persistence and fantasy-link r / intervals)."""

from __future__ import annotations

import polars as pl

from twm.modules.coach_tendencies import fantasy, persistence, season
from twm.publish import coach_tendencies as ct
from twm.publish.collect import PublishData, merge_coaches

NAMES = pl.DataFrame({"coach_id": ["pat_example", "jo_sample", "renee_darcy", "nobody_else"],
                      "coach_name": ["Pat Example", "Jo Sample", "Renée D'Arcy",
                                     "Nobody Else"]})  # fmt: skip
SLUGS = {"pat_example": "pat-example", "jo_sample": "jo-sample", "renee_darcy": "renee-d-arcy"}
ROWS = (("pat_example", "BUF", 2025), ("jo_sample", "DAL", 2025), ("renee_darcy", "SF", 2025),
        ("pat_example", "BUF", 2026), ("jo_sample", "KC", 2026))  # fmt: skip
METRICS = list(season.METRICS)
N_SEASON = len(ROWS) * len(METRICS)  # 40
N_CAREER = 3 * len(METRICS)  # 24


def _value(metric: str, i: int) -> float:
    if metric == "proe":
        return -3.0 + 2.5 * i
    if metric == "neutral_sec_per_play":
        return 26.0 + 1.5 * i
    return 0.2 + 0.1 * i


def season_frame() -> pl.DataFrame:
    rows = []
    for i, (coach, team, year) in enumerate(ROWS):
        cur = year == 2026
        for m in METRICS:
            rows.append({"coach_id": coach, "team": team, "season": year, "is_current": cur,
                         "through_week": 3 if cur else 18, "games": 3 if cur else 17,
                         "plays": 190 if cur else 1000, "metric": m, "value": _value(m, i),
                         "sample": 120 if cur else 600, "league_avg": _value(m, 2),
                         "percentile": None if m == "fourth_go_rate" and cur else
                         100.0 * (i + 0.5) / len(ROWS)})  # fmt: skip
    schema = {"season": pl.Int32, "through_week": pl.Int32, "percentile": pl.Float64}
    return pl.DataFrame(rows, schema_overrides=schema).select(season.SEASON_LONG_COLUMNS)


def career_frame() -> pl.DataFrame:
    rows = [{"coach_id": c, "metric": m, "seasons": 1, "first_season": 2025, "last_season": 2025,
             "teams": t, "value": _value(m, i), "sample": 600, "league_avg": _value(m, 2),
             "vs_league": _value(m, i) - _value(m, 2)}
            for i, (c, t, y) in enumerate(ROWS) if y == 2025 for m in METRICS]  # fmt: skip
    schema = {"first_season": pl.Int32, "last_season": pl.Int32}
    return pl.DataFrame(rows, schema_overrides=schema).select(season.CAREER_COLUMNS)


def persistence_frame(nan: bool = False) -> pl.DataFrame:
    rows = [{"metric": m, "comparison": c, "n_pairs": 40 - 10 * k, "n_seasons": 12,
             "first_season": 2006, "last_season": 2024, "r": 0.5 - 0.2 * k,
             "ci_low": 0.3 - 0.3 * k, "ci_high": 0.7 - 0.1 * k}
            for m in METRICS for k, c in enumerate(persistence.COMPARISONS)]  # fmt: skip
    df = pl.DataFrame(rows).select(persistence.PERSISTENCE_COLUMNS)
    return _nan(df, ("r", "ci_low", "ci_high")) if nan else df


def link_frame(nan: bool = False) -> pl.DataFrame:
    rows = [{"metric": m, "target": t, "horizon": h, "n": 400, "n_seasons": 13,
             "r": 0.7 if h == "same_season" else 0.3, "ci_low": 0.2, "ci_high": 0.8,
             "x_sd": 4.0, "y_per_x_sd": 2.5 if h == "same_season" else 1.0}
            for m in METRICS for t in fantasy.TARGETS for h in fantasy.HORIZONS]  # fmt: skip
    df = pl.DataFrame(rows).select(fantasy.LINK_COLUMNS)
    return _nan(df, ("r", "ci_low", "ci_high", "y_per_x_sd")) if nan else df


def _nan(df: pl.DataFrame, cols: tuple[str, ...]) -> pl.DataFrame:
    """NaN in ``cols`` of the first row (what tiny inputs give)."""
    first = pl.int_range(pl.len()) == 0
    return df.with_columns(pl.when(first).then(float("nan")).otherwise(pl.col(c)).alias(c)
                           for c in cols)  # fmt: skip


def frames(nan: bool = False) -> dict[str, pl.DataFrame]:
    return {ct.SEASON: season_frame(), ct.CAREER: career_frame(),
            ct.PERSISTENCE: persistence_frame(nan), ct.LINK: link_frame(nan)}  # fmt: skip


def add_coach_tendencies(data: PublishData, nan: bool = False) -> PublishData:
    """``data`` with the module's tables, its coaches in dim_coach and its site_meta keys."""
    tables, coaches = ct.site_tables(frames(nan), NAMES)
    s, week = ct.latest(tables[ct.SEASON])
    d = ct.CoachTendencyData(season=s, through_week=week, tables=tables, coaches=coaches)
    data.tables.update(tables)
    data.tables["dim_coach"] = merge_coaches(data.tables.get("dim_coach"), coaches)
    data.meta.update(ct.meta(d))
    data.coach_tendencies = d
    return data
