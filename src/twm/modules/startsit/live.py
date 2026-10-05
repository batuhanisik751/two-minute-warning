"""This week's ranks at an as-of (point in time: an AsOfView), name resolution and the odds."""

from __future__ import annotations

import difflib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import polars as pl

from twm.asof import AsOfView, sql_timestamp, to_utc
from twm.ids import normalize_name
from twm.modules.startsit.data import POSITIONS, ranks_sql
from twm.modules.startsit.dist import RankTable, p_greater

CLOSE_CALL = 0.55
FLEX = frozenset({"RB", "WR", "TE"})


class RanksNotOutError(LookupError):
    """The target week's weekly ranks are not available at the as-of yet."""


@dataclass(frozen=True)
class Ranked:
    name: str
    pos: str
    rank: int
    team: str | None
    gsis_id: str | None
    valid: bool  # False: his game kicked off before these ranks were available


@dataclass(frozen=True)
class WeekRanks:
    season: int
    week: int
    scrape_date: date
    rows: pl.DataFrame  # ranks_sql(only_valid=False) rows of the week


def target_week(db: Path, as_of: datetime) -> tuple[int, int]:
    """The regular-season week whose window holds ``as_of`` (the first week whose Tuesday
    as-of, ``window_end_utc``, is after it): the week of the games still to come."""
    t = sql_timestamp(to_utc(as_of).replace(tzinfo=None))
    with AsOfView(db, as_of) as v:
        df = v.sql("SELECT season, week FROM dim_week WHERE season_type = 'REG' AND "
                   f"window_end_utc > {t} ORDER BY season, week LIMIT 1")  # fmt: skip
    if df.is_empty():
        raise LookupError("no regular-season week is still to come in the warehouse")
    return int(df["season"][0]), int(df["week"][0])


def week_ranks(db: Path, season: int, week: int, as_of: datetime) -> WeekRanks:
    """The newest weekly ranks for (season, week) available at ``as_of``; RanksNotOutError when
    there are none yet (they arrive Fridays)."""
    with AsOfView(db, as_of) as v:
        rows = v.sql(ranks_sql([season], only_valid=False))
    week_rows = rows.filter(pl.col("week") == week)
    if week_rows.is_empty():
        last = rows.sort("available_at").tail(1)
        newest = (f" (the newest here are of {last['scrape_date'][0]}, for week "
                  f"{last['week'][0]})" if last.height else "")  # fmt: skip
        raise RanksNotOutError(
            f"The week {week} ranks of {season} are not out yet: FantasyPros posts the weekly "
            f"expert ranks on Fridays{newest}. Try again after they land.")  # fmt: skip
    return WeekRanks(season, week, week_rows["scrape_date"].max(), week_rows)


def _ranked(row: dict) -> Ranked:
    return Ranked(row["player"], row["pos"], int(row["pos_rank"]), row["team"], row["gsis_id"],
                  bool(row["valid"]))  # fmt: skip


def resolve(rows: pl.DataFrame, text: str) -> list[Ranked]:
    """The ranked players ``text`` names: exact (case, accents, punctuation, suffixes ignored;
    a trailing position like "TE" narrows it), else every player whose name holds all the
    words, else close spellings. One element: resolved; several: the choices; none: unknown."""
    words = text.split()
    pos = words[-1].upper() if len(words) > 1 and words[-1].upper() in POSITIONS else None
    key = normalize_name(" ".join(words[:-1] if pos else words)) or ""
    df = rows.filter(pl.col("pos") == pos) if pos else rows
    names = [normalize_name(n) or "" for n in df["player"].to_list()]
    hits = [i for i, n in enumerate(names) if n == key]
    if not hits:
        hits = [i for i, n in enumerate(names) if all(w in n.split() for w in key.split())]
    if not hits:
        ratio = [difflib.SequenceMatcher(None, key, n).ratio() for n in names]
        best = max(ratio, default=0.0)
        hits = [i for i, r in enumerate(ratio) if r >= 0.8 and r == best]  # the closest only
    return sorted((_ranked(df.row(i, named=True)) for i in hits),
                  key=lambda r: (r.pos, r.rank, r.name))  # fmt: skip


# The hook for a per-player chance to play (e.g. the Questionable module's, later): a function
# of the ranked player returning his chance to play (0-1), or None for the rank's history.
PlayChanceSource = Callable[[Ranked], float | None]


def no_play_chance(player: Ranked) -> float | None:  # noqa: ARG001 - the default hook
    return None


@dataclass(frozen=True)
class Odds:
    a: Ranked
    b: Ranked
    p_a: float  # P(A outscores B), a tie counting one half
    play_a: float | None  # None: the rank's history (its share without a stat line)
    play_b: float | None
    range_a: tuple[float, float, float]  # 10th / 50th / 90th percentile points
    range_b: tuple[float, float, float]


def odds(
    table: RankTable,
    a: Ranked,
    b: Ranked,
    play_a: float | None = None,
    play_b: float | None = None,
    chance: PlayChanceSource = no_play_chance,
) -> Odds:
    """P(A > B) from independent draws of the two ranks' distributions (teammates and same-game
    correlation ignored). An explicit play chance wins over ``chance`` (the hook)."""
    pa = play_a if play_a is not None else chance(a)
    pb = play_b if play_b is not None else chance(b)
    da, db = table.dist(a.pos, a.rank, pa), table.dist(b.pos, b.rank, pb)
    rng = lambda d: (d.quantile(0.1), d.quantile(0.5), d.quantile(0.9))  # noqa: E731
    return Odds(a, b, p_greater(da, db), pa, pb, rng(da), rng(db))


def verdict(o: Odds) -> str:
    """One plain-English line: the favourite, or a close call under 55%."""
    fav, other, p = (o.a, o.b, o.p_a) if o.p_a >= 0.5 else (o.b, o.a, 1 - o.p_a)
    if p < CLOSE_CALL:
        return (f"Close call: {fav.name} outscores {other.name} only about {p:.0%} of the time; "
                "either is a reasonable start.")  # fmt: skip
    return f"Start {fav.name}: he outscores {other.name} about {p:.0%} of the time."


def describe(o: Odds, wr: WeekRanks) -> list[str]:
    """The CLI's lines (local only: they print FantasyPros ranks)."""
    out = [f"Week {wr.week} of {wr.season}: FantasyPros weekly expert ranks of "
           f"{wr.scrape_date} (local only, never published)."]  # fmt: skip
    for tag, p, play, (q1, q5, q9) in (("A", o.a, o.play_a, o.range_a),
                                       ("B", o.b, o.play_b, o.range_b)):  # fmt: skip
        chance = "as players at that rank did" if play is None else f"{play:.0%} to play"
        out.append(f"  {tag}: {p.name} ({p.team or '?'}) {p.pos}{p.rank}; points 10th/50th/90th "
                   f"percentile {q1:.1f} / {q5:.1f} / {q9:.1f} ({chance})")  # fmt: skip
    out.append(f"  {o.a.name} outscores {o.b.name} about {o.p_a:.0%} of the time "
               f"({o.b.name}: {1 - o.p_a:.0%}).")  # fmt: skip
    if o.a.pos != o.b.pos and not {o.a.pos, o.b.pos} <= FLEX:
        out.append("  Note: different positions outside RB/WR/TE: only a superflex question.")
    out.append(f"  {verdict(o)}")
    return out
