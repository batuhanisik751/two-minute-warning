"""The only module that talks to ESPN (PROJECT_SPEC 4.3; step F2): espn-api behind small typed
functions that return plain dataclasses, so a change in ESPN's unofficial API or in the library
breaks here and nowhere else.

Every name used below was checked in the installed espn-api 0.46.0 source; the comments cite
the file and line (site-packages/espn_api/...). :func:`connect` is the only place a network
call starts; the tests build :class:`EspnClient` around a fake league made of real espn-api
objects (tests/league_fixtures.py) and never reach ESPN.

Errors become :class:`EspnError` with one plain line naming what failed (sign-in, league not
found, a library change) and never a cookie: messages pass through :func:`twm.league.env.redact`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from twm.league.env import LeagueEnv, redact

T = TypeVar("T")
DST_SLOT_ID = "16"  # espn_api.football.constant.POSITION_MAP[16] == 'D/ST': pointsOverrides key
FREE_AGENT_POSITIONS = ("QB", "RB", "WR", "TE", "K", "D/ST", "FLEX")  # POSITION_MAP string keys
NOT_STARTING = frozenset({"BE", "IR"})  # box_score.py:25: bench and IR never score


class EspnError(RuntimeError):
    """An ESPN call failed; the message is one line and never contains a cookie."""


@dataclass(frozen=True)
class ScoringItem:
    stat_id: int
    abbr: str  # espn_api's SETTINGS_SCORING_FORMAT_MAP abbreviation ('Unknown' if new)
    label: str
    points: float  # what a player earns per unit
    dst_points: float | None  # the D/ST override (pointsOverrides['16']) when the league sets one


@dataclass(frozen=True)
class LeagueSettings:
    team_count: int
    roster_slots: dict[str, int]  # ESPN slot label ('QB', 'RB/WR/TE', 'D/ST', 'BE', ...) -> count
    scoring: tuple[ScoringItem, ...]
    faab: bool
    acquisition_budget: int
    current_week: int
    nfl_week: int
    final_week: int
    # the fantasy playoffs (base_settings.py; None when the library object lacks them)
    reg_season_count: int | None = None  # regular-season matchup periods (l.4)
    playoff_team_count: int | None = None  # l.8
    playoff_matchup_period_length: int | None = None  # weeks per playoff matchup (l.17; 0 = unset)
    reg_season_final_week: int | None = None  # the last week of the regular season (see below)


@dataclass(frozen=True)
class RawSettings:
    waiver: dict[str, str]  # ESPN's acquisitionSettings scalars as text
    slots: dict[str, int] | None  # lineup slot label -> count, by ESPN's slot ids


@dataclass(frozen=True)
class FantasyTeam:
    team_id: int
    abbrev: str
    name: str
    is_mine: bool  # an owner's member id equals the SWID cookie (compared in memory only)


@dataclass(frozen=True)
class PlayerRow:
    """One ESPN player as a roster spot, a free agent or a box-score line."""

    espn_id: int
    name: str
    position: str  # ESPN's main position: QB, RB, WR, TE, K, D/ST
    pro_team: str  # ESPN abbreviation (WSH, LAR, ...; 'None' for a free agent without a team)
    lineup_slot: str = ""  # QB, RB, RB/WR/TE, BE, IR, ... ('' for a free agent)
    injury_status: str = ""
    points: float | None = None  # actual points in the week (box scores, free agents)
    projected_points: float | None = None  # ESPN's projection for the week
    percent_owned: float | None = None
    on_bye: bool | None = None


@dataclass(frozen=True)
class RosterSpot:
    team_id: int
    player: PlayerRow
    acquisition_type: str


@dataclass(frozen=True)
class BoxScoreTeam:
    matchup: int  # position of the matchup in ESPN's list for the week
    team_id: int
    opponent_id: int | None  # None on a bye
    is_home: bool
    is_playoff: bool
    score: float
    projected: float
    lineup: tuple[PlayerRow, ...]


@dataclass(frozen=True)
class ActivityRow:
    date_ms: int  # ESPN's epoch milliseconds
    seq: int  # position of the action inside the activity
    team_id: int | None
    action: str  # FA ADDED, WAIVER ADDED, DROPPED, TRADE_SENT, TRADE_RECEIVED, UNKNOWN
    espn_id: int | None
    name: str
    position: str
    bid_amount: float


def _describe(what: str, e: BaseException) -> str:
    """One plain line for a failed call (the caller redacts it)."""
    try:
        from espn_api.requests.espn_requests import (
            ESPNAccessDenied,
            ESPNInvalidLeague,
            ESPNUnknownError,
        )
        from requests.exceptions import RequestException
    except ImportError:  # a fake league in a checkout without the espn extra
        ESPNAccessDenied = ESPNInvalidLeague = ESPNUnknownError = RequestException = EspnError  # noqa: N806

    if isinstance(e, ESPNAccessDenied):
        return (
            f"ESPN refused {what}: a private league needs valid ESPN_S2 and ESPN_SWID cookies "
            "(missing, wrong or expired: copy fresh ones from your browser into .env yourself)"
        )
    if isinstance(e, ESPNInvalidLeague):
        return f"ESPN has no league with this ESPN_LEAGUE_ID in ESPN_YEAR ({what})"
    if isinstance(e, ESPNUnknownError):
        return f"ESPN answered {what} with an error ({e}); try again later"
    if isinstance(e, RequestException):
        return f"could not reach ESPN for {what} ({type(e).__name__}); check the connection"
    if isinstance(e, KeyError | AttributeError | TypeError | IndexError | ValueError):
        return (
            f"{what}: ESPN's answer did not have the shape espn-api expects "
            f"({type(e).__name__}: {e}); ESPN or the library may have changed"
        )
    return f"{what} failed ({type(e).__name__}: {e})"


def connect(env: LeagueEnv) -> EspnClient:
    """Sign in and load the league (the only network entry point; several ESPN requests)."""
    try:
        from espn_api.football import League
    except ImportError:
        raise EspnError(
            "espn-api is not installed: run `uv sync --all-extras` (My League is optional)"
        ) from None
    try:
        league = League(
            league_id=env.league_id,
            year=env.year,
            espn_s2=env.espn_s2 or None,
            swid=env.swid or None,
        )
    except Exception as e:  # noqa: BLE001 - every failure becomes one redacted line
        raise EspnError(redact(_describe("loading the league", e), *env.secrets())) from None
    return EspnClient(league, secrets=env.secrets(), swid=env.swid)


def _text(v: Any) -> str:
    """espn-api's json_parsing returns [] for a missing key: treat that as empty text."""
    return v if isinstance(v, str) else ""


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def _int(v: Any) -> int | None:
    return int(v) if isinstance(v, int) and not isinstance(v, bool) else None


def _reg_final_week(s: Any) -> int | None:
    """The last scoring period (week) of the last regular-season matchup period: espn-api's
    matchup_periods (base_settings.py:5, ESPN's matchup period id -> its scoring periods, as
    football/league.py:310 reads it) at reg_season_count; one week per matchup period when the
    map does not list it (ESPN football's regular-season matchups last one week)."""
    n = _int(getattr(s, "reg_season_count", None))
    if n is None:
        return None
    periods = getattr(s, "matchup_periods", None)
    weeks = periods.get(str(n), periods.get(n)) if isinstance(periods, dict) else None
    if isinstance(weeks, list) and weeks and all(_int(w) is not None for w in weeks):
        return max(int(w) for w in weeks)
    return n


def _norm_member(v: Any) -> str:
    return str(v or "").strip().strip("{}").upper()


class EspnClient:
    """Typed reads of one loaded espn-api football ``League`` (or a test double with the same
    attributes). Each method converts library objects to the dataclasses above."""

    def __init__(self, league: Any, *, secrets: tuple[str, ...] = (), swid: str = "") -> None:
        self._league = league
        self._secrets = secrets
        self._me = _norm_member(swid)

    def __repr__(self) -> str:  # no league id, no cookie
        return "EspnClient()"

    def _call(self, what: str, fn: Callable[[], T]) -> T:
        try:
            return fn()
        except EspnError:
            raise
        except Exception as e:  # noqa: BLE001 - one redacted line, no chained traceback
            raise EspnError(redact(_describe(what, e), *self._secrets)) from None

    @property
    def year(self) -> int:
        return int(self._league.year)  # base_league.py:14

    @property
    def current_week(self) -> int:
        return int(self._league.current_week)  # base_league.py:41-43

    # ---- settings (base_settings.py, football/settings.py) ----------------------------------

    def settings(self) -> LeagueSettings:
        return self._call("reading the league settings", self._settings)

    def _settings(self) -> LeagueSettings:
        lg, s = self._league, self._league.settings
        fmt = {int(x["id"]): x for x in s.scoring_format}  # settings.py:13-20
        # the raw items keep a player's points and the D/ST override apart (scoring_format
        # merges them: settings.py:19); base_settings.py:21 is espn-api's only copy of them
        raw = (getattr(s, "_raw_scoring_settings", None) or {}).get("scoringItems")
        items: list[ScoringItem] = []
        if raw is None:
            for sid, x in fmt.items():
                items.append(ScoringItem(sid, x["abbr"], x["label"], float(x["points"]), None))
        else:
            for it in raw:
                sid = int(it["statId"])
                over = (it.get("pointsOverrides") or {}).get(DST_SLOT_ID)
                x = fmt.get(sid, {"abbr": "Unknown", "label": "Unknown"})
                items.append(
                    ScoringItem(sid, x["abbr"], x["label"], float(it.get("points", 0)), _num(over))
                )
        return LeagueSettings(
            team_count=int(s.team_count),  # base_settings.py:7
            roster_slots={str(k): int(v) for k, v in s.position_slot_counts.items() if v},
            scoring=tuple(sorted({i.stat_id: i for i in items}.values(), key=lambda i: i.stat_id)),
            faab=bool(s.faab),  # base_settings.py:23
            acquisition_budget=int(s.acquisition_budget or 0),  # base_settings.py:24
            current_week=int(lg.current_week),
            nfl_week=int(lg.nfl_week),  # football/league.py:41
            final_week=int(lg.finalScoringPeriod),  # base_league.py:36
            reg_season_count=_int(getattr(s, "reg_season_count", None)),
            playoff_team_count=_int(getattr(s, "playoff_team_count", None)),
            playoff_matchup_period_length=_int(getattr(s, "playoff_matchup_period_length", None)),
            reg_season_final_week=_reg_final_week(s),
        )

    def raw_settings(self) -> RawSettings:
        """What espn-api does not parse (or parses fragilely) from ESPN's mSettings view, read
        through the library's own request object (base_league.py:26, espn_requests.py:76):
        acquisitionSettings (waiver days, hours; espn-api keeps only the budget,
        base_settings.py:23-24) and lineupSlotCounts keyed by slot id (espn-api zips the
        counts with POSITION_MAP's order, settings.py:10-11)."""
        from espn_api.football.constant import POSITION_MAP

        def read() -> RawSettings:
            data = self._league.espn_request.league_get(params={"view": "mSettings"})
            st = data.get("settings") or {}
            waiver: dict[str, str] = {}
            for k, v in (st.get("acquisitionSettings") or {}).items():
                if isinstance(v, str | int | float | bool) or v is None:
                    waiver[str(k)] = "" if v is None else str(v)
                elif isinstance(v, list) and all(isinstance(x, str | int | float) for x in v):
                    waiver[str(k)] = ",".join(map(str, v))
            counts = (st.get("rosterSettings") or {}).get("lineupSlotCounts")
            slots = None
            if isinstance(counts, dict):
                slots = {str(POSITION_MAP.get(int(k), f"slot {k}")): int(n)
                         for k, n in counts.items() if int(n)}  # fmt: skip
            return RawSettings(waiver, slots)

        return self._call("reading the raw league settings", read)

    # ---- teams and rosters (football/team.py, football/player.py) ---------------------------

    def teams(self) -> list[FantasyTeam]:
        def read() -> list[FantasyTeam]:
            out = []
            for t in self._league.teams:
                ids = {_norm_member(m.get("id")) for m in (t.owners or [])}  # team.py:42
                mine = bool(self._me) and self._me in ids
                out.append(FantasyTeam(int(t.team_id), str(t.team_abbrev), str(t.team_name), mine))
            return out

        return self._call("reading the teams", read)

    def rosters(self, week: int | None = None) -> list[RosterSpot]:
        """Every team's roster now, or as of ``week`` (league.py:96 load_roster_week)."""

        def read() -> list[RosterSpot]:
            if week is not None and week != self.current_week:
                self._league.load_roster_week(week)
            return [
                RosterSpot(int(t.team_id), _player(p), _text(p.acquisitionType))
                for t in self._league.teams
                for p in t.roster
            ]

        return self._call(f"reading the rosters{f' of week {week}' if week else ''}", read)

    # ---- free agents, box scores, activity (football/league.py) -----------------------------

    def free_agents(
        self, week: int | None = None, size: int = 50, position: str | None = None
    ) -> list[PlayerRow]:
        """Free agents and players on waivers (league.py:358; current season only)."""
        if position is not None and position not in FREE_AGENT_POSITIONS:
            raise EspnError(f"position {position!r} is not one of {FREE_AGENT_POSITIONS}")

        def read() -> list[PlayerRow]:
            got = self._league.free_agents(week=week, size=size, position=position)
            return [_player(p, box=True, slot="") for p in got]

        return self._call(f"reading the free agents ({position or 'all'})", read)

    def box_scores(self, week: int) -> list[BoxScoreTeam]:
        """Both sides of every matchup of ``week`` with starters and bench (league.py:296)."""
        if week > self.current_week:  # espn-api would silently return the current week
            raise EspnError(f"week {week} has not started (ESPN's current week is "
                            f"{self.current_week})")  # fmt: skip

        def read() -> list[BoxScoreTeam]:
            out: list[BoxScoreTeam] = []
            for i, b in enumerate(self._league.box_scores(week=week)):
                sides = (("home", True), ("away", False))
                ids = {side: _team_id(getattr(b, f"{side}_team")) for side, _ in sides}
                for side, is_home in sides:
                    tid = ids[side]
                    if tid is None:
                        continue
                    other = ids["away" if is_home else "home"]
                    out.append(BoxScoreTeam(
                        matchup=i, team_id=tid, opponent_id=other, is_home=is_home,
                        is_playoff=bool(b.is_playoff), score=float(getattr(b, f"{side}_score")),
                        projected=float(getattr(b, f"{side}_projected")),
                        lineup=tuple(_player(p, box=True) for p in getattr(b, f"{side}_lineup")),
                    ))  # fmt: skip
            return out

        return self._call(f"reading the box scores of week {week}", read)

    def activity(self, size: int = 25) -> list[ActivityRow]:
        """Recent adds, drops and trades (league.py:254, activity.py)."""

        def read() -> list[ActivityRow]:
            out = []
            for a in self._league.recent_activity(size=size):
                for seq, (team, action, player, bid) in enumerate(a.actions):
                    pid = getattr(player, "playerId", player)
                    out.append(ActivityRow(
                        date_ms=int(a.date), seq=seq, team_id=_team_id(team), action=str(action),
                        espn_id=pid if isinstance(pid, int) else None,
                        name=_text(getattr(player, "name", "")),
                        position=_text(getattr(player, "position", "")),
                        bid_amount=_num(bid) or 0.0,
                    ))  # fmt: skip
            return out

        return self._call("reading the recent activity", read)


def _team_id(team: Any) -> int | None:
    """A Team (team.py:7), a bare team id, or ''/None (no team, a bye)."""
    tid = getattr(team, "team_id", team)
    return tid if isinstance(tid, int) and not isinstance(tid, bool) else None


def _player(p: Any, *, box: bool = False, slot: str | None = None) -> PlayerRow:
    """player.py (roster spots) or box_player.py (box scores, free agents) -> PlayerRow."""
    owned = _num(p.percent_owned)  # player.py:40 (-1 when ESPN sends none)
    lineup = slot if slot is not None else _text(p.slot_position if box else p.lineupSlot)
    return PlayerRow(
        espn_id=int(p.playerId),
        name=_text(p.name),
        position=_text(p.position),
        pro_team=_text(p.proTeam),
        lineup_slot=lineup,
        injury_status=_text(p.injuryStatus),
        points=_num(p.points) if box else None,
        projected_points=_num(p.projected_points) if box else None,
        percent_owned=owned if owned is not None and owned >= 0 else None,
        on_bye=bool(p.on_bye_week) if box else None,
    )
