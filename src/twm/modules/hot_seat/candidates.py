"""Hot-Seat H1: candidate head-coach departures from the schedule's per-game coach columns.

PROJECT_SPEC 8.5 / Phase H1. Every team's played games (REG and POST, 1999 onward, in kickoff
order) are cut into **stints**: maximal runs of consecutive games with the same listed head coach
(``coach_game``). Each boundary between two stints is a candidate departure of the earlier coach:

- ``in_season``: the two games are in the same season (the coach changed between two games);
- ``offseason``: the next stint starts in a later season (a different coach in week 1).

A row carries the departing coach's last game, the successor and his first game, the team's
regular-season record that season and three flags:

- ``interim_suspected``: the departing coach's stint began after the team's first game of a
  season and ended within that same season (he coached only part of one season);
- ``data_gap_suspected``: nflverse stopped recording mid-season changes after 2023, so from
  ``SCHEDULE_GAP_FROM`` on an offseason change after a losing record may hide an in-season firing
  (and an interim coach the schedule never names); also set on the ``none_recorded`` rows below;
- ``coach_returns``: the departing coach coaches the same team again later (a temporary absence,
  a suspension, or a return), so the row may not be a departure at all.

``none_recorded`` rows: in the latest season with played games, a team from
``SCHEDULE_GAP_FROM`` on with a losing record so far and no change recorded. The schedule cannot
settle whether its coach is still in charge; the owner checks it (no departure is implied).

Rows are kept for departures whose last season is in ``[first_season, last_season]`` and are
sorted deterministically (season, team, last game date, week, coach).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import polars as pl

FIRST_SEASON = 2002
LAST_SEASON = 2026
SCHEDULE_GAP_FROM = 2024  # first season whose schedule coach columns are season-constant

CHANGE_KINDS = ("in_season", "offseason", "none_recorded")

CANDIDATE_COLUMNS = (
    "candidate_id",
    "team",
    "last_season",
    "change_kind",
    "coach_id",
    "coach_name",
    "last_game_id",
    "last_game_date",
    "last_week",
    "last_season_type",
    "successor_id",
    "successor_name",
    "successor_first_game_id",
    "successor_first_game_date",
    "team_record",
    "coach_games_in_season",
    "tenure_first_season",
    "interim_suspected",
    "data_gap_suspected",
    "coach_returns",
    "auto_note",
)

GAME_COLUMNS = (
    "game_id",
    "season",
    "week",
    "season_type",
    "game_date",
    "team",
    "coach_id",
    "coach_name",
    "points_for",
    "points_against",
    "played",
)


@dataclass(frozen=True)
class Stint:
    team: str
    coach_id: str
    coach_name: str
    games: tuple[dict, ...]  # the stint's played games in kickoff order

    @property
    def first(self) -> dict:
        return self.games[0]

    @property
    def last(self) -> dict:
        return self.games[-1]


def stints(games: pl.DataFrame) -> dict[str, list[Stint]]:
    """Per team, the runs of consecutive played games with one listed coach (kickoff order)."""
    played = games.filter(pl.col("played")).sort(["team", "game_date", "season", "week", "game_id"])
    out: dict[str, list[Stint]] = {}
    for row in played.select(GAME_COLUMNS).iter_rows(named=True):
        runs = out.setdefault(row["team"], [])
        if runs and runs[-1].coach_id == row["coach_id"]:
            prev = runs[-1]
            runs[-1] = Stint(prev.team, prev.coach_id, prev.coach_name, (*prev.games, row))
        else:
            runs.append(Stint(row["team"], row["coach_id"], row["coach_name"], (row,)))
    return out


def team_records(games: pl.DataFrame) -> dict[tuple[str, int], tuple[int, int, int]]:
    """(team, season) -> regular-season (wins, losses, ties) over played games."""
    reg = games.filter(pl.col("played") & (pl.col("season_type") == "REG"))
    agg = reg.group_by("team", "season").agg(
        (pl.col("points_for") > pl.col("points_against")).sum().alias("w"),
        (pl.col("points_for") < pl.col("points_against")).sum().alias("l"),
        (pl.col("points_for") == pl.col("points_against")).sum().alias("t"),
    )
    return {(r["team"], r["season"]): (r["w"], r["l"], r["t"]) for r in agg.iter_rows(named=True)}


def _record_text(rec: tuple[int, int, int] | None) -> str:
    return "" if rec is None else f"{rec[0]}-{rec[1]}-{rec[2]}"


def _losing(rec: tuple[int, int, int] | None) -> bool:
    return rec is not None and rec[0] < rec[1]


def _season_games(st: Stint, season: int) -> int:
    return sum(1 for g in st.games if g["season"] == season)


def _team_first_game(runs: list[Stint]) -> dict[int, str]:
    """season -> game_id of the team's first played game that season."""
    first: dict[int, str] = {}
    for st in runs:
        for g in st.games:
            first.setdefault(g["season"], g["game_id"])
    return first


def _row(
    st: Stint,
    nxt: Stint | None,
    kind: str,
    rec,
    *,
    interim: bool,
    gap: bool,
    returns: bool,
    tenure: int,
    note: str,
) -> dict:
    last = st.last
    season = last["season"]
    return {
        "candidate_id": f"{season}_{st.team}_w{last['week']:02d}_{st.coach_id}",
        "team": st.team,
        "last_season": season,
        "change_kind": kind,
        "coach_id": st.coach_id,
        "coach_name": st.coach_name,
        "last_game_id": last["game_id"],
        "last_game_date": str(last["game_date"]),
        "last_week": last["week"],
        "last_season_type": last["season_type"],
        "successor_id": nxt.coach_id if nxt else "",
        "successor_name": nxt.coach_name if nxt else "",
        "successor_first_game_id": nxt.first["game_id"] if nxt else "",
        "successor_first_game_date": str(nxt.first["game_date"]) if nxt else "",
        "team_record": _record_text(rec),
        "coach_games_in_season": _season_games(st, season),
        "tenure_first_season": tenure,
        "interim_suspected": interim,
        "data_gap_suspected": gap,
        "coach_returns": returns,
        "auto_note": note,
    }


def _notes(
    st: Stint,
    nxt: Stint | None,
    *,
    interim: bool,
    gap: bool,
    returns_at: dict | None,
    gap_from: int,
    rec,
) -> str:
    parts = []
    if interim:
        parts.append(f"took over in season {st.first['season']} week {st.first['week']}")
    if returns_at is not None:
        parts.append(
            f"coaches {st.team} again from {returns_at['season']} week {returns_at['week']}"
        )
    if gap and nxt is not None:
        parts.append(
            f"{gap_from}+ schedule lists one coach all season; losing record "
            f"{_record_text(rec)}: check for an in-season firing, unnamed interim"
        )
    if gap and nxt is None:
        parts.append(f"no change recorded; losing record {_record_text(rec)} so far: check")
    return "; ".join(parts)


def departure_candidates(
    games: pl.DataFrame,
    *,
    first_season: int = FIRST_SEASON,
    last_season: int = LAST_SEASON,
    gap_from: int = SCHEDULE_GAP_FROM,
) -> pl.DataFrame:
    """Candidate departures (one row per coach change, plus ``none_recorded`` checks)."""
    records = team_records(games)
    played = games.filter(pl.col("played"))
    latest = played["season"].max() if played.height else None
    rows: list[dict] = []
    for team, runs in stints(games).items():
        first_game = _team_first_game(runs)
        for i, st in enumerate(runs):
            season = st.last["season"]
            nxt = runs[i + 1] if i + 1 < len(runs) else None
            if not first_season <= season <= last_season:
                continue
            if nxt is None and not (season == latest and season >= gap_from):
                continue
            rec = records.get((team, season))
            started_mid = st.first["game_id"] != first_game[st.first["season"]]
            interim = started_mid and st.first["season"] == season
            later = [s for s in runs[i + 1 :] if s.coach_id == st.coach_id]
            returns_at = later[0].first if later else None
            if nxt is None:
                if not _losing(rec):
                    continue
                kind, gap = "none_recorded", True
            else:
                kind = "in_season" if nxt.first["season"] == season else "offseason"
                gap = kind == "offseason" and season >= gap_from and _losing(rec)
            note = _notes(
                st, nxt, interim=interim, gap=gap, returns_at=returns_at, gap_from=gap_from, rec=rec
            )
            rows.append(
                _row(
                    st,
                    nxt,
                    kind,
                    rec,
                    interim=interim,
                    gap=gap,
                    returns=returns_at is not None,
                    tenure=st.first["season"],
                    note=note,
                )
            )
    return _frame(rows)


def _frame(rows: list[dict]) -> pl.DataFrame:
    schema = {c: pl.String for c in CANDIDATE_COLUMNS}
    schema.update(
        last_season=pl.Int64,
        last_week=pl.Int64,
        coach_games_in_season=pl.Int64,
        tenure_first_season=pl.Int64,
        interim_suspected=pl.Boolean,
        data_gap_suspected=pl.Boolean,
        coach_returns=pl.Boolean,
    )
    df = pl.DataFrame(rows, schema=schema) if rows else pl.DataFrame(schema=schema)
    return df.sort(["last_season", "team", "last_game_date", "last_week", "coach_id"])


GAMES_SQL = """
SELECT cg.game_id, cg.season, cg.week, cg.season_type,
       CAST(fg.gameday AS VARCHAR) AS game_date, cg.team, cg.coach_id, dc.coach_name,
       CASE WHEN cg.is_home THEN fg.home_score ELSE fg.away_score END AS points_for,
       CASE WHEN cg.is_home THEN fg.away_score ELSE fg.home_score END AS points_against,
       fg.result IS NOT NULL AS played
FROM coach_game cg
JOIN fact_game fg ON fg.game_id = cg.game_id
JOIN dim_coach dc ON dc.coach_id = cg.coach_id
ORDER BY cg.team, fg.gameday, cg.game_id
"""


def load_games(db: Path | str) -> pl.DataFrame:
    """Every team-game with its listed coach and score (read-only warehouse read).

    A label table, not a feature: it reads the warehouse as of today on purpose (the departures
    are the outcomes Hot-Seat predicts); features must still go through ``twm.asof``.
    """
    from twm.warehouse.build import connect

    con = connect(db, read_only=True)
    try:
        return con.execute(GAMES_SQL).pl().select(GAME_COLUMNS)
    finally:
        con.close()


def write_candidates(df: pl.DataFrame, path: Path | str) -> Path:
    """Write the CSV (columns in ``CANDIDATE_COLUMNS`` order, booleans as true/false)."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    text = [c for c in CANDIDATE_COLUMNS if df.schema[c] == pl.String]
    df.select(CANDIDATE_COLUMNS).with_columns(pl.col(text).replace("", None)).write_csv(out)
    return out


def season_counts(df: pl.DataFrame) -> pl.DataFrame:
    """Rows per last season: per change kind and per flag (for the CLI summary)."""
    kinds = [(pl.col("change_kind") == k).sum().alias(k) for k in CHANGE_KINDS]
    flags = [pl.col(f).sum().alias(f) for f in ("interim_suspected", "data_gap_suspected")]
    return df.group_by("last_season").agg(*kinds, *flags).sort("last_season")
