"""Stored vs recomputed lists (step I3a): the comparison and the seeded season sample."""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import numpy as np
import polars as pl

TOLERANCE = 1e-9  # probabilities / scores: |stored - recomputed| must not exceed this
N_EXAMPLES = 5  # mismatch examples kept per comparison


@dataclass
class Comparison:
    """What one or more comparisons found: lists and rows compared, the largest absolute
    difference of a compared number, and the mismatches (counted; the first few kept)."""

    lists: int = 0
    rows: int = 0
    max_diff: float = 0.0
    mismatches: int = 0
    examples: list[str] = field(default_factory=list)

    def flag(self, n: int, text: str) -> None:
        """Count ``n`` mismatches described by ``text`` (kept while there is room)."""
        if n <= 0:
            return
        self.mismatches += n
        if len(self.examples) < N_EXAMPLES:
            self.examples.append(text)

    def add(self, other: Comparison) -> Comparison:
        self.lists += other.lists
        self.rows += other.rows
        self.max_diff = max(self.max_diff, other.max_diff)
        for text in other.examples:
            if len(self.examples) < N_EXAMPLES:
                self.examples.append(text)
        self.mismatches += other.mismatches
        return self


def sample_seasons(seasons: Iterable[int], n: int, seed: int) -> list[int]:
    """``n`` of ``seasons`` (sorted, unique), always the first and the last, the others drawn
    with ``random.Random(seed)``: the same arguments always give the same sample."""
    have = sorted({int(s) for s in seasons})
    if len(have) <= max(n, 2):
        return have
    middle = random.Random(seed).sample(have[1:-1], max(n, 2) - 2)
    return sorted({have[0], have[-1], *middle})


def _key_text(row: dict, key: Sequence[str]) -> str:
    return ", ".join(f"{k}={row[k]}" for k in key)


def _diff(a: pl.Series, b: pl.Series) -> np.ndarray:
    """|a - b| per row; 0 when both are missing (NULL or NaN), inf when only one is."""
    x = a.cast(pl.Float64).to_numpy().astype(np.float64)
    y = b.cast(pl.Float64).to_numpy().astype(np.float64)
    mx, my = np.isnan(x), np.isnan(y)
    with np.errstate(invalid="ignore"):
        d = np.abs(x - y)
    d[mx & my] = 0.0
    d[mx ^ my] = math.inf
    return d


def compare_lists(
    stored: pl.DataFrame,
    recomputed: pl.DataFrame,
    *,
    lists: Sequence[str],
    entity: Sequence[str],
    rank: str | None = None,
    numbers: Sequence[str] = (),
    exact: Sequence[str] = (),
    tol: float = TOLERANCE,
) -> Comparison:
    """``stored`` vs ``recomputed`` list by list (``lists``: the columns naming a list):
    the same entities in every list (``entity``: the row's id within its list), the same
    order (by ``rank`` when given, else the frames' row order within each list) and rank,
    every ``numbers`` column within ``tol`` and every ``exact`` column equal (NULL = NULL)."""
    key = [*lists, *entity]
    out = Comparison(lists=stored.select(lists).unique().height)
    for name, f in (("stored", stored), ("recomputed", recomputed)):
        if (dup := f.height - f.select(key).unique().height) > 0:
            out.flag(dup, f"{dup} duplicate {name} rows on ({', '.join(key)})")
    cols = list(dict.fromkeys([*([rank] if rank else []), *numbers, *exact]))
    s = stored.select(*key, *cols).with_columns(pl.lit(True).alias("_s"))
    r = recomputed.select(*key, *cols).with_columns(pl.lit(True).alias("_r"))
    j = s.join(r, on=key, how="full", coalesce=True, suffix="_new", nulls_equal=True)
    for flag, what in (("_r", "stored row not recomputed"), ("_s", "recomputed row not stored")):
        miss = j.filter(pl.col(flag).is_null())
        if miss.height:
            first = _key_text(miss.row(0, named=True), key)
            out.flag(miss.height, f"{miss.height} {what}, e.g. {first}")
    both = j.filter(pl.col("_s").is_not_null() & pl.col("_r").is_not_null())
    out.rows = both.height
    for c in numbers:
        d = _diff(both.get_column(c), both.get_column(f"{c}_new"))
        if d.size:
            out.max_diff = max(out.max_diff, float(d.max()))
        bad = np.flatnonzero(d > tol)
        if bad.size:
            row = both.row(int(bad[0]), named=True)
            out.flag(bad.size, f"{bad.size} rows with {c} off by more than {tol:g}, e.g. "
                     f"{_key_text(row, key)}: {row[c]!r} vs {row[f'{c}_new']!r}")  # fmt: skip
    for c in [*([rank] if rank else []), *exact]:
        neq = both.filter(pl.col(c).ne_missing(pl.col(f"{c}_new")))
        if neq.height:
            row = neq.row(0, named=True)
            out.flag(neq.height, f"{neq.height} rows with another {c}, e.g. "
                     f"{_key_text(row, key)}: {row[c]!r} vs {row[f'{c}_new']!r}")  # fmt: skip
    if not rank:  # the row order within each list must be the same
        seq = [f.select(key).with_columns(pl.int_range(pl.len()).over(lists).alias("_i"))
               for f in (stored, recomputed)]  # fmt: skip
        o = seq[0].join(seq[1], on=key, how="inner", nulls_equal=True, suffix="_new")
        moved = o.filter(pl.col("_i") != pl.col("_i_new"))
        if moved.height:
            out.flag(moved.height, f"{moved.height} rows in another position of their list, "
                     f"e.g. {_key_text(moved.row(0, named=True), key)}")  # fmt: skip
    return out
