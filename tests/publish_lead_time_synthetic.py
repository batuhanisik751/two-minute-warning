"""A made-up lead-time study for `twm publish` tests (feature #8): synthetic roster % paths and
Radar lists run through the real study (:func:`twm.modules.lead_time.study.build` ->
``build_frames``), so the frames have exactly the shape a publish gets. Deterministic.

Seasons 2021 and 2022 (both "complete"; 2020 holds only the Radar's earlier backtest rows the
priorities learn from). Per season 40 players: 30 cross 50% at period 4-12 (the crowd adds),
7 never do, 3 start above it; the Radar lists each in weeks around his crossing, every third
one at a must-add score."""

from __future__ import annotations

from datetime import datetime, timedelta

import polars as pl

from twm.modules.lead_time import crowd as cr
from twm.modules.lead_time import flags as fl
from twm.modules.lead_time import study as st
from twm.publish import lead_time as lt
from twm.publish.collect import PublishData

SEASONS = (2021, 2022)
ASOF1 = {2021: datetime(2021, 9, 14, 14), 2022: datetime(2022, 9, 13, 14)}
POS = ("WR", "RB", "TE", "QB")
N = 40


def windows() -> pl.DataFrame:
    rows = [(s, w, ASOF1[s] + timedelta(days=7 * (w - 1))) for s in SEASONS for w in range(1, 19)]
    return pl.DataFrame(rows, schema=cr.WINDOW_SCHEMA, orient="row")


def _pct(i: int, period: int) -> float | None:
    """Player i's roster % in a period (None: not on that day's pages)."""
    if i >= 37:
        return 70.0 + i % 3  # already rostered by most leagues
    cross = 4 + i % 9
    if i >= 30 or period < cross - 1:
        return 5.0 + (i * 7 + period) % 20  # low (and, for i >= 30, never crosses)
    return 30.0 if period == cross - 1 else 60.0


def owned() -> pl.DataFrame:
    rows = []
    for s in SEASONS:
        for i in range(N):
            for k in range(0, 17):  # a Friday scrape per period (0 = before week 1)
                pct = _pct(i, k)
                if pct is not None:
                    day = (ASOF1[s] + timedelta(days=7 * k - 4)).date()
                    rows.append((s, f"P{s}-{i:02d}", POS[i % 4], day, pct))
    return pl.DataFrame(rows, schema=cr.OWNED_SCHEMA, orient="row")


def radar_rows() -> pl.DataFrame:
    """2020: 1,200 rows the bins learn from (high scores hit); 2021-2022: each player listed
    from a week around his crossing to week 16."""
    rows = [(2020, 1 + i % 16, "WR", f"H{i}", 0.9 if i % 2 else 0.1, 1 + i % 25, bool(i % 2))
            for i in range(1200)]  # fmt: skip
    for s in SEASONS:
        for i in range(N):
            first = max(1, 4 + i % 9 - (i % 5 - 1))  # one period after, same, up to 3 before
            score = 0.9 if i % 3 == 0 else 0.1
            for w in range(first, 17):
                rows.append((s, w, POS[i % 4], f"P{s}-{i:02d}", score, 1 + i % 20, i % 2 == 0))
    return pl.DataFrame(rows, schema=fl.ROWS_SCHEMA, orient="row")


def frames() -> dict[str, pl.DataFrame]:
    return st.build_frames(st.build(owned(), windows(), radar_rows(), seasons=SEASONS))


def add_lead_time(data: PublishData) -> PublishData:
    """``data`` with the module's six tables."""
    d = lt.LeadTimeData(tables=lt.site_tables(frames()))
    data.tables.update(d.tables)
    data.lead_time = d
    return data
