"""A tiny synthetic warehouse for the board tests (I1b): the tables and columns the board reads,
each row with ``available_at``, behind :class:`FakeView`, a stand-in for
:class:`twm.asof.AsOfView` that shows only rows with ``available_at <= as_of``."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import duckdb
import polars as pl

from twm.scoring import ScoringRules

SEASONS = (2018, 2019, 2020, 2021)
WEEKS = range(1, 11)
TEAMS = ("KC", "BUF")
# gsis_id -> (position, entry_year, team, birth_date, draft_round, draft_pick)
PLAYERS: dict[str, tuple[str, int, str, date, int | None, int | None]] = {
    "00-0000001": ("WR", 2014, "KC", date(1991, 5, 1), 1, 20),
    "00-0000002": ("WR", 2019, "KC", date(1997, 3, 1), 2, 50),
    "00-0000003": ("RB", 2015, "BUF", date(1993, 9, 9), None, None),
    "00-0000004": ("TE", 2016, "BUF", date(1994, 1, 2), 3, 90),
}
SNAPSHOT = {s: datetime(s + 1, 2, 20, tzinfo=UTC) for s in SEASONS}


def available(season: int, week: int) -> datetime:
    return datetime(season, 9, 1) + timedelta(days=7 * week)


def tables(scramble_after: int | None = None, drop_after: int | None = None) -> dict:
    """The world's tables as polars frames; seasons after ``drop_after`` deleted, values of
    seasons after ``scramble_after`` scrambled (x * 3 + 7)."""
    cols = ScoringRules.from_config().required_columns()
    pw, tw, ros, sn, ngr, ngu = [], [], [], [], [], []
    for s in SEASONS:
        for w in WEEKS:
            gid, at = f"{s}_{w:02d}_KC_BUF", available(s, w)
            for i, (g, (pos, ey, team, *_)) in enumerate(PLAYERS.items()):
                if s < ey or (g == "00-0000003" and s == 2021 and w > 4):
                    continue  # the RB misses most of 2021
                k = 1 + i + (s - 2018) * (1 if pos == "WR" else -1)
                st = dict.fromkeys(cols, 0) | {
                    "receptions": k, "receiving_yards": 12 * k,
                    "rushing_yards": 60 if pos == "RB" else 0, "receiving_tds": int(w % 3 == 0),
                }  # fmt: skip
                pw.append({"player_id": g, "season": s, "week": w, "season_type": "REG",
                           "game_id": gid, "team": team, "carries": 15 if pos == "RB" else 0,
                           "targets": k + 2, "receiving_air_yards": 8 * k, "attempts": 0,
                           **st, "available_at": at})  # fmt: skip
                ros.append({"gsis_id": g, "season": s, "week": w, "season_type": "REG",
                            "position": pos, "entry_year": ey, "available_at": at})  # fmt: skip
                sn.append({"gsis_id": g, "season": s, "week": w, "game_type": "REG",
                           "position": pos, "offense_snaps": 40.0, "offense_pct": 0.5 + i / 10,
                           "available_at": at})  # fmt: skip
                ngr.append(
                    {
                        "gsis_id": g,
                        "season": s,
                        "week": w,
                        "season_type": "REG",
                        "avg_separation": 2.0 + i / 4,
                        "targets": k + 2,
                        "available_at": at,
                    }
                )
                if pos == "RB":
                    ngu.append({"gsis_id": g, "season": s, "week": w, "season_type": "REG",
                                "rush_yards_over_expected": 5.0, "rush_attempts": 15,
                                "available_at": at})  # fmt: skip
            for t in TEAMS:
                tw.append({"team": t, "game_id": gid, "season": s, "season_type": "REG",
                           "attempts": 35, "targets": 34, "receiving_air_yards": 280,
                           "carries": 25, "passing_yards": 250, "passing_tds": 2,
                           "passing_interceptions": 1, "sack_yards_lost": 14,
                           "sacks_suffered": 2, "available_at": at})  # fmt: skip
    out = {"fact_player_week": pw, "fact_team_week": tw, "fact_roster_week": ros,
           "fact_snaps": sn, "fact_ngs_receiving_week": ngr,
           "fact_ngs_rushing_week": ngu}  # fmt: skip
    frames = {k: pl.DataFrame(v, infer_schema_length=None) for k, v in out.items()}
    return {k: _alter(f, scramble_after, drop_after) for k, f in frames.items()} | statics()


KEYS = {"player_id", "gsis_id", "season", "week", "season_type", "game_type", "game_id",
        "team", "position", "available_at"}  # fmt: skip


def _alter(df: pl.DataFrame, scramble_after: int | None, drop_after: int | None) -> pl.DataFrame:
    if drop_after is not None:
        df = df.filter(pl.col("season") <= drop_after)
    if scramble_after is not None:
        late = pl.col("season") > scramble_after
        nums = [c for c, t in df.schema.items() if c not in KEYS and t.is_numeric()]
        df = df.with_columns(pl.when(late).then(pl.col(c) * 3 + 7).otherwise(pl.col(c))
                             .cast(df.schema[c]).alias(c) for c in nums)  # fmt: skip
    return df


def statics() -> dict[str, pl.DataFrame]:
    players = pl.DataFrame(
        [{"gsis_id": g, "birth_date": b, "draft_year": ey if r else None, "draft_round": r,
          "draft_pick": p, "public_from": datetime(ey, 5, 15)}
         for g, (_, ey, _, b, r, p) in PLAYERS.items()],
        schema_overrides={"draft_year": pl.Int32, "draft_round": pl.Int32,
                          "draft_pick": pl.Int32},
    )  # fmt: skip
    combine = pl.DataFrame(
        [{"gsis_id": "00-0000002", "season": 2019, "forty": 4.4, "wt": 200.0, "height_in": 73,
          "vertical": 36.0, "broad_jump": 125.0, "available_at": datetime(2019, 4, 25)}]
    )  # fmt: skip
    return {"dim_player": players, "fact_combine": combine}


class FakeView:
    """What the board's builders need from an AsOfView: ``as_of`` and ``sql`` over the tables,
    event rows filtered to ``available_at <= as_of``, players to those who exist by then."""

    def __init__(self, frames: dict[str, pl.DataFrame], as_of: datetime) -> None:
        self.as_of = as_of
        self._con = duckdb.connect()
        cut = as_of.replace(tzinfo=None)
        for name, df in frames.items():
            self._con.register(f"src_{name}", df)
            when = "public_from" if name == "dim_player" else "available_at"
            self._con.execute(f"CREATE VIEW {name} AS SELECT * FROM src_{name} "
                              f"WHERE {when} <= TIMESTAMP '{cut}'")  # fmt: skip

    def sql(self, query: str, params: Any = None) -> pl.DataFrame:
        return self._con.execute(query, params or []).pl()


def xfp_games(scramble_after: int | None = None) -> pl.DataFrame:
    rows = [{"game_id": f"{s}_{w:02d}_KC_BUF", "gsis_id": g, "season": s, "xfp": 9.0 + w / 10}
            for s in SEASONS for w in WEEKS for g in PLAYERS if s >= PLAYERS[g][1]]  # fmt: skip
    df = pl.DataFrame(rows, schema_overrides={"season": pl.Int32})
    if scramble_after is not None:
        late = pl.col("season") > scramble_after
        df = df.with_columns(pl.when(late).then(pl.col("xfp") * 3 + 7).otherwise("xfp"))
    return df
