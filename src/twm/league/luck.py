"""The league luck index (feature #9; local only): exact counts from the synced schedule, no
model. docs/my_league.md "Luck and playoff odds" explains every number:

- **record, points for / against**: the regular-season weeks whose games are ALL final (ESPN
  decided every matchup of the week); a tie counts half a win;
- **all-play record**: each such week, every team against every other team's score (a team
  that outscores 9 of 11 others goes 9-2 that week);
- **expected wins**: the sum over those weeks of the share of the other teams outscored (a
  tie half); **luck** = wins - expected wins (positive: more wins than the points deserved);
- **your record under each other team's schedule**: your weekly scores against the opponents
  that team faced (where that team faced you, you face that team instead).

Reads data/league.duckdb's ``league_schedule`` (written by `twm league sync` since feature #9);
the playoff odds (:mod:`twm.league.odds`) start from the same :class:`LeagueSeason`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import duckdb


class LuckUnavailableError(LookupError):
    """Nothing to count (no synced schedule): one plain line."""


@dataclass(frozen=True)
class Side:
    """One team's side of one regular-season matchup (a week: one week per matchup period)."""

    week: int
    team: int
    opponent: int | None  # None on a bye
    score: float | None  # final (ESPN decided the matchup), else None
    live: float | None  # ESPN's live total while the week is being played


@dataclass
class LeagueSeason:
    league_id: int
    season: int
    sync_week: int  # the ESPN week of the sync that wrote the schedule
    synced_at: datetime  # naive UTC
    teams: dict[int, str]  # fantasy team id -> name (local output only)
    my_team: int | None
    sides: list[Side]  # regular season only
    reg_weeks: int  # regular-season weeks (matchup periods)
    playoff_teams: int
    playoff_length: int  # weeks per playoff round
    seeding_rule: str  # ESPN's playoffSeedingRule ('' when an older sync did not store it)
    reseed: bool
    previous_seasons: tuple[int, ...] = ()

    @property
    def team_ids(self) -> list[int]:
        return sorted(self.teams)

    def weeks(self) -> list[int]:
        return sorted({s.week for s in self.sides})

    def final_weeks(self, through: int | None = None) -> list[int]:
        """Regular-season weeks whose every side is final (up to ``through``)."""
        by: dict[int, list[Side]] = {}
        for s in self.sides:
            by.setdefault(s.week, []).append(s)
        done = [w for w, ss in by.items() if ss and all(s.score is not None for s in ss)]
        return sorted(w for w in done if through is None or w <= through)

    def week_sides(self, week: int) -> list[Side]:
        return [s for s in self.sides if s.week == week]


def _int(settings: dict[str, str], *keys: str) -> int | None:
    vals = [settings.get(k, "") for k in keys]
    return next((int(v) for v in vals if v.strip().isdigit()), None)


def load(con: duckdb.DuckDBPyConnection, season: int | None = None) -> LeagueSeason:
    """The newest synced schedule (of ``season``) with the league's playoff settings."""
    from twm.league import store

    # a store synced before feature #9 has no schedule table at all (read-only: not created)
    has = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'league_schedule'"
    ).fetchone()[0]
    part = store.current(con, "league_schedule", season) if has else None
    if part is None:
        raise LuckUnavailableError(
            "My League: no season schedule is stored yet: run `uv run twm league sync` (it "
            "stores the schedule since feature #9)"
        )
    lid, yr = part.league_id, part.season
    s = store.settings_of(con, lid, yr)
    reg = _int(s, "reg_season_count")
    if reg is None:
        raise LuckUnavailableError("My League: the synced settings lack reg_season_count")
    if _int(s, "reg_season_final_week") not in (None, reg):
        raise LuckUnavailableError(
            "My League: a matchup period longer than one week is not supported "
            "(reg_season_final_week != reg_season_count)"
        )
    rows = con.execute(
        "SELECT matchup_period, league_team_id, league_opponent_id, score, live_score FROM "
        "league_schedule WHERE league_id = ? AND season = ? AND matchup_period <= ? AND "
        "playoff_tier = 'NONE' ORDER BY matchup_period, league_team_id", [lid, yr, reg]
    ).fetchall()  # fmt: skip
    sides = [Side(int(w), int(t), None if o is None else int(o), sc, lv)
             for w, t, o, sc, lv in rows]  # fmt: skip
    names = dict(con.execute(
        "SELECT league_team_id, league_team_name FROM league_teams WHERE league_id = ? AND "
        "season = ? ORDER BY synced_at", [lid, yr]).fetchall())  # fmt: skip
    teams = {int(t): str(names.get(t) or f"team {t}") for t in {x.team for x in sides} | set(names)}
    prev = tuple(int(y) for y in s.get("previous_seasons", "").split(",") if y.strip().isdigit())
    return LeagueSeason(
        league_id=lid, season=yr, sync_week=part.week, synced_at=part.synced_at, teams=teams,
        my_team=store.my_team(con, lid, yr), sides=sides, reg_weeks=reg,
        playoff_teams=_int(s, "playoff_team_count") or 0,
        playoff_length=max(_int(s, "playoff_matchup_period_length") or 1, 1),
        seeding_rule=s.get("playoff_seeding_rule", ""),
        reseed=s.get("playoff_reseed", "False") == "True", previous_seasons=prev,
    )  # fmt: skip


@dataclass
class TeamLuck:
    team: int
    wins: int = 0
    losses: int = 0
    ties: int = 0
    points_for: float = 0.0
    points_against: float = 0.0
    ap_wins: int = 0  # all-play
    ap_losses: int = 0
    ap_ties: int = 0
    expected_wins: float = 0.0

    @property
    def win_points(self) -> float:
        """Wins with a tie counted as half a win."""
        return self.wins + 0.5 * self.ties

    @property
    def luck(self) -> float:
        return self.win_points - self.expected_wins

    @property
    def record(self) -> str:
        return f"{self.wins}-{self.losses}" + (f"-{self.ties}" if self.ties else "")

    @property
    def all_play(self) -> str:
        return f"{self.ap_wins}-{self.ap_losses}" + (f"-{self.ap_ties}" if self.ap_ties else "")


def _result(mine: float, theirs: float) -> int:
    return (mine > theirs) - (mine < theirs)  # 1 win, 0 tie, -1 loss


def luck_table(ls: LeagueSeason, through: int | None = None) -> dict[int, TeamLuck]:
    """Every team's record, points and all-play numbers over the final weeks (to ``through``)."""
    out = {t: TeamLuck(t) for t in ls.team_ids}
    for w in ls.final_weeks(through):
        sides = ls.week_sides(w)
        scores = {s.team: float(s.score) for s in sides if s.score is not None}
        others = len(scores) - 1
        for s in sides:
            r = out.setdefault(s.team, TeamLuck(s.team))
            me = scores[s.team]
            r.points_for += me
            if s.opponent is not None and s.opponent in scores:
                r.points_against += scores[s.opponent]
                res = _result(me, scores[s.opponent])
                r.wins += res > 0
                r.ties += res == 0
                r.losses += res < 0
            above = sum(1 for t, x in scores.items() if t != s.team and me > x)
            level = sum(1 for t, x in scores.items() if t != s.team and me == x)
            r.ap_wins, r.ap_ties = r.ap_wins + above, r.ap_ties + level
            r.ap_losses += others - above - level
            if others > 0:
                r.expected_wins += (above + 0.5 * level) / others
    return out


def swap_records(ls: LeagueSeason, team: int, through: int | None = None) -> dict[int, str]:
    """``team``'s record under each other team's schedule (final weeks to ``through``): its own
    weekly score against the opponent that team faced; where that team faced ``team``, ``team``
    faces that team instead. A bye week of that team is skipped."""
    out: dict[int, str] = {}
    weeks = ls.final_weeks(through)
    for other in ls.team_ids:
        if other == team:
            continue
        w_, l_, t_ = 0, 0, 0
        for w in weeks:
            sides = {s.team: s for s in ls.week_sides(w)}
            mine, theirs = sides.get(team), sides.get(other)
            if mine is None or theirs is None or theirs.opponent is None:
                continue
            opp = other if theirs.opponent == team else theirs.opponent
            res = _result(float(mine.score or 0.0), float(sides[opp].score or 0.0))
            w_, t_, l_ = w_ + (res > 0), t_ + (res == 0), l_ + (res < 0)
        out[other] = f"{w_}-{l_}" + (f"-{t_}" if t_ else "")
    return out
