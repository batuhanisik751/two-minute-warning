"""Synthetic ESPN league for the My League tests (step F2): fake team names, fake cookies, no
network. :func:`fake_league` builds REAL espn-api objects (Settings, Team, Player, BoxPlayer,
BoxScore, Activity) from small hand-written payloads shaped like ESPN's, so the tests also
check that twm.league.espn_client reads the attributes the installed library really sets."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

YEAR = 2026
WEEK = 3
LEAGUE_ID = 424242
FAKE_S2 = "AEBfakeS2cookieValue0123456789abcdefFAKE%2Bplus"
FAKE_SWID = "{0FA4E000-1111-2222-3333-DEADBEEF0001}"
OTHER_MEMBER = "{0FA4E000-1111-2222-3333-DEADBEEF0002}"
DATE_MS = 1790000000000  # 2026-09-21 (a synthetic activity timestamp)

# lineupSlotCounts keys 0..25 in POSITION_MAP order (football/settings.py:10-11 zips them)
SLOT_COUNTS = dict.fromkeys(map(str, range(26)), 0) | {
    "0": 1, "2": 2, "4": 2, "6": 1, "16": 1, "17": 1, "20": 7, "21": 1, "23": 1,
}  # fmt: skip


def item(stat_id: int, points: float, dst: float | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"statId": stat_id, "points": points}
    if dst is not None:
        out["pointsOverrides"] = {"16": dst}
    return out


# ESPN-style scoring items equal to config/scoring.yaml's defaults (so the baseline diff is empty)
BASE_ITEMS: tuple[tuple[int, float, float | None], ...] = (
    (3, 0.04, None), (4, 4, None), (20, -2, None), (19, 2, None),  # passing
    (24, 0.1, None), (25, 6, None), (26, 2, None),  # rushing
    (53, 1, None), (42, 0.1, None), (43, 6, None), (44, 2, None),  # receiving
    (72, -2, None), (63, 6, None), (101, 6, 6), (102, 6, 6),  # misc + D/ST return TDs
    (80, 3, None), (77, 4, None), (198, 5, None), (201, 6, None), (85, -1, None),
    (86, 1, None),  # kicking
    (99, 1, None), (95, 2, None), (96, 2, None), (97, 2, None), (98, 2, None), (103, 6, None),
    (104, 6, None), (93, 6, None),  # D/ST
    (89, 5, None), (90, 4, None), (91, 3, None), (92, 1, None), (121, 0, None), (122, 0, None),
    (123, -1, None), (124, -3, None), (125, -5, None),  # points allowed
)  # fmt: skip


def settings_payload(
    items: list[dict[str, Any]] | None = None, size: int = 12, slots: dict[str, int] | None = None
) -> dict[str, Any]:
    return {
        "scheduleSettings": {"matchupPeriodCount": 14, "matchupPeriods": {"3": [3]},
                             "playoffTeamCount": 6, "playoffSeedingRule": "TOTAL_POINTS_SCORED"},
        "tradeSettings": {"vetoVotesRequired": 4},
        "size": size,
        "draftSettings": {"keeperCount": 0},
        "name": "Synthetic Test League",
        "scoringSettings": {"matchupTieRule": "NONE", "playoffMatchupTieRule": "NONE",
                            "scoringType": "H2H_POINTS",
                            "scoringItems": items if items is not None else
                            [item(*x) for x in BASE_ITEMS]},
        "acquisitionSettings": {"isUsingAcquisitionBudget": False, "acquisitionBudget": 100},
        "rosterSettings": {"lineupSlotCounts": SLOT_COUNTS | (slots or {})},
    }  # fmt: skip


# (espn id, fake name, eligible slots, ESPN pro team id, fantasy team, lineup slot id, points,
#  projected). Pro teams: 12 KC, 25 SF, 28 WSH, 14 LAR, 6 DAL, 2 BUF, 3 CHI, 0 none.
ROSTER = (
    (9001, "Fake Passer", [0, 7, 20, 21], 12, 1, 0, 21.5, 18.0),
    (9002, "Fake Runner", [2, 3, 23, 20, 21], 25, 1, 2, 12.25, 14.0),
    (9003, "Fake Catcher", [3, 4, 5, 23, 20, 21], 28, 1, 23, 8.0, 11.5),
    (9004, "Fake Bencher", [5, 6, 23, 20, 21], 14, 1, 20, 30.0, 6.0),
    (-16012, "Chiefs D/ST", [16, 20, 21], 12, 1, 16, 7.0, 6.5),
    (9005, "Fake Kicker", [17, 20, 21], 6, 2, 17, 9.0, 8.0),
    (9006, "Fake Wideout", [3, 4, 5, 23, 20, 21], 2, 2, 4, 15.0, 12.0),
)  # fmt: skip
FREE_AGENTS = (
    (9101, "Fake Waiver Back", [2, 3, 23, 20, 21], 3, 0, None, 4.5, 9.0),
    (9102, "Fake Unknown Rookie", [3, 4, 5, 23, 20, 21], 0, 0, None, 0.0, 2.0),
)  # fmt: skip
TEAMS = ((1, "FTA", "Fake Team Alpha", FAKE_SWID), (2, "FTB", "Fake Team Beta", OTHER_MEMBER))
PRO_SCHEDULE = {12: (4, DATE_MS), 25: (26, DATE_MS), 28: (21, DATE_MS), 6: (19, DATE_MS)}


def player(row: tuple, *, pool: bool = True) -> dict[str, Any]:
    pid, name, slots, pro, team, slot, pts, proj = row
    stats = [
        {"seasonId": YEAR, "scoringPeriodId": WEEK, "statSourceId": src, "statSplitTypeId": 1,
         "appliedTotal": total, "stats": {"3": 1.0}}
        for src, total in ((0, pts), (1, proj))
    ]  # fmt: skip
    body = {"id": pid, "fullName": name, "defaultPositionId": 1, "eligibleSlots": slots,
            "proTeamId": pro, "injuryStatus": "ACTIVE", "ownership": {"percentOwned": 42.5},
            "stats": stats}  # fmt: skip
    if not pool:  # a free agent (kona_player_info shape)
        return {"id": pid, "onTeamId": 0, "status": "FREEAGENT", "player": body}
    entry: dict[str, Any] = {
        "playerId": pid,
        "acquisitionType": "DRAFT",
        "playerPoolEntry": {"id": pid, "onTeamId": team, "player": body},
    }
    if slot is not None:
        entry["lineupSlotId"] = slot
    return entry


def team_payload(team_id: int, abbrev: str, name: str) -> dict[str, Any]:
    overall = {"wins": 2, "losses": 1, "ties": 0, "pointsFor": 300.5, "pointsAgainst": 280.25,
               "streakLength": 1, "streakType": "WIN"}  # fmt: skip
    return {
        "id": team_id,
        "abbrev": abbrev,
        "name": name,
        "divisionId": 0,
        "record": {"overall": overall},
        "playoffSeed": team_id,
        "rankCalculatedFinal": 0,
    }


WAIVER_RAW = {"isUsingAcquisitionBudget": False, "acquisitionBudget": 100,
              "waiverProcessDays": ["WEDNESDAY"], "waiverHours": 24,
              "nested": {"x": 1}}  # fmt: skip


class FakeLeague:
    """Stands in for espn_api.football.League: the same attributes and methods
    twm.league.espn_client uses, holding real library objects built from the payloads above."""

    def __init__(
        self,
        items: list[dict[str, Any]] | None = None,
        size: int = 12,
        slots: dict[str, int] | None = None,
    ) -> None:
        from espn_api.football.settings import Settings
        from espn_api.football.team import Team

        self.year, self.league_id, self.current_week, self.nfl_week = YEAR, LEAGUE_ID, WEEK, WEEK
        self.finalScoringPeriod = 18
        self.settings_payload = settings_payload(items, size, slots)
        self.settings = Settings(self.settings_payload)
        self.teams = [
            Team(team_payload(tid, ab, name),
                 roster={"entries": [player(r) for r in ROSTER if r[4] == tid]},
                 schedule=[], year=YEAR, pro_schedule=None,
                 owners=[{"id": member, "displayName": f"fake_owner_{tid}"}])
            for tid, ab, name, member in TEAMS
        ]  # fmt: skip
        self.calls: list[tuple[str, Any]] = []
        self.espn_request = SimpleNamespace(league_get=self._league_get)

    def _league_get(self, params: dict | None = None, **_: Any) -> dict[str, Any]:
        self.calls.append(("league_get", params))
        roster = self.settings_payload["rosterSettings"]
        return {"settings": {"acquisitionSettings": dict(WAIVER_RAW), "rosterSettings": roster}}

    def get_team_data(self, team_id: int) -> Any:
        return next((t for t in self.teams if t.team_id == team_id), None)

    def player_info(self, name: str | None = None, playerId: Any = None) -> Any:  # noqa: N803
        return None  # activity.py:27/55: an unknown player stays a bare id

    def load_roster_week(self, week: int) -> None:
        self.calls.append(("load_roster_week", week))

    def free_agents(self, week: int | None = None, size: int = 50, position: str | None = None):
        from espn_api.football.box_player import BoxPlayer

        self.calls.append(("free_agents", (week, size, position)))
        return [BoxPlayer(player(r, pool=False), PRO_SCHEDULE, {}, week or WEEK, YEAR)
                for r in FREE_AGENTS]  # fmt: skip

    def box_scores(self, week: int | None = None):
        from espn_api.football.box_score import BoxScore

        self.calls.append(("box_scores", week))
        side = {
            tid: {
                "teamId": tid,
                "totalPoints": 100.0 + tid,
                "rosterForCurrentScoringPeriod": {
                    "entries": [player(r) for r in ROSTER if r[4] == tid]
                },
            }
            for tid in (1, 2)
        }
        boxes = [BoxScore({"playoffTierType": "NONE", "home": side[1], "away": side[2]},
                          PRO_SCHEDULE, {}, week or WEEK, YEAR)]  # fmt: skip
        for b in boxes:  # football/league.py:329-334
            b.home_team, b.away_team = self.get_team_data(1), self.get_team_data(2)
        return boxes

    def recent_activity(self, size: int = 25, msg_type: str | None = None, offset: int = 0):
        from espn_api.football.activity import Activity

        self.calls.append(("recent_activity", size))
        topics = [
            {"date": DATE_MS, "messages": [{"messageTypeId": 180, "to": 1, "targetId": 9003,
                                            "from": 7},
                                           {"messageTypeId": 239, "for": 1, "targetId": 9999}]},
            {"date": DATE_MS + 1000, "messages": [{"messageTypeId": 244, "from": 2, "to": 1,
                                                   "targetId": 9005}]},
        ]  # fmt: skip
        return [Activity(t, {}, self.get_team_data, self.player_info) for t in topics]
