"""Generate the golden world's synthetic raw inputs (tests/golden/inputs/*.csv.gz).

A made-up league, NOT real data: 8 teams (named with real team codes so the warehouse's team
table works), 3 seasons (2022-2024) of 7 regular-season weeks, 20 players per team (3 QB, 6 RB,
8 WR, 3 TE; every name and id is invented). Each player has a hidden talent that drifts from
week to week; the depth chart follows talent (the starter keeps a small edge), injuries take
players out for 1-3 weeks (injury report, inactive or injured-reserve roster status), so
backups step in: the Waiver Radar's breakouts. Stats come from talent and playing time. Two
teams have a bye in weeks 4 and 5, one receiver is traded after week 3 of each season, the
two weakest players of every team retire after a season and rookies (some drafted, some not)
replace them. FantasyPros-style preseason cheat sheets exist for 2023 and 2024 (so 2022 uses
the prior-season fallback, with no prior season).

The inputs are generated ONCE and committed ("frozen"): the golden tests read the files, not
this generator, so a numpy or polars upgrade cannot change them silently. Regenerate only on
purpose (then refresh the expected outputs too):

    uv run python tests/golden/make_inputs.py
    uv run python tests/golden/update.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

if __package__ in (None, ""):  # run as a script: make `tests.golden` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.golden.world import DTYPES, INPUTS, write_input  # noqa: E402

SEED = 20260928
SEASONS = (2022, 2023, 2024)
WEEKS = 7
TEAMS = ("KC", "PHI", "DAL", "SEA", "MIA", "BUF", "SF", "DET")
ROSTER = {"QB": 3, "RB": 6, "WR": 8, "TE": 3}
# offensive snap share by depth rank (1 = starter); deeper players do not play on offense
SNAP_SHARE = {
    "QB": (1.0,),
    "RB": (0.62, 0.34, 0.12),
    "WR": (0.92, 0.84, 0.66, 0.26, 0.10),
    "TE": (0.80, 0.34),
}
TARGET_WEIGHT = {"QB": 0.0, "RB": 0.35, "WR": 1.0, "TE": 0.7}
ADOT = {"RB": 1.0, "WR": 10.5, "TE": 7.0}  # average depth of target (air yards per target)
BYES = {4: ("KC", "PHI"), 5: ("DAL", "SEA")}  # week -> the two teams off
TRADE_AFTER_WEEK = 3
RANKED_SEASONS = SEASONS  # seasons with a preseason cheat sheet (every one)
PRESEASON_SCRAPE = "08-28"


@dataclass
class Player:
    n: int
    name: str
    pos: str
    team: str
    talent: float
    birth: date
    entry: int
    draft_round: int | None
    injured_until: int = 0  # misses weeks <= this (of the current season)
    on_ir: bool = False

    @property
    def gsis(self) -> str:
        return f"00-0099{self.n:03d}"

    @property
    def pfr(self) -> str:
        return f"Gold{self.n:03d}"

    @property
    def fp_id(self) -> int:
        return 950000 + self.n


FIRST = (
    "Avery",
    "Blake",
    "Casey",
    "Drew",
    "Emery",
    "Finley",
    "Gray",
    "Harper",
    "Indy",
    "Jules",
    "Kai",
    "Lane",
    "Marlo",
    "Noel",
    "Oakley",
    "Parker",
    "Quinn",
    "Reese",
    "Sage",
    "Tatum",
)
LAST = (
    "Aldane",
    "Brisco",
    "Corvell",
    "Dunmore",
    "Eskew",
    "Fallon",
    "Garrow",
    "Hollis",
    "Ivers",
    "Jessup",
    "Kittle",
    "Larue",
    "Merced",
    "Norcott",
    "Orwell",
    "Pruitt",
    "Quarry",
    "Rourke",
    "Sully",
    "Tobin",
)


class World:
    def __init__(self, seed: int = SEED) -> None:
        self.rng = np.random.default_rng(seed)
        self.players: list[Player] = []
        self.next_n = 1
        self.rows: dict[str, list[dict]] = {k: [] for k in INPUTS}
        for team in TEAMS:
            for pos, k in ROSTER.items():
                for _ in range(k):
                    self._new_player(pos, team, SEASONS[0], veteran=True)

    # -- players ---------------------------------------------------------------------------

    def _new_player(self, pos: str, team: str, season: int, *, veteran: bool) -> Player:
        r = self.rng
        n = self.next_n
        self.next_n += 1
        name = f"{FIRST[n % len(FIRST)]} {LAST[(n * 7) % len(LAST)]}{n:03d}"
        if veteran:
            entry = int(season - r.integers(0, 9))
            rnd = int(r.integers(1, 8)) if r.random() < 0.8 else None
            talent = float(r.normal())
        else:
            entry = season
            rnd = int(r.integers(1, 8)) if r.random() < 0.7 else None
            talent = float(r.normal(0.4 if rnd is not None and rnd <= 2 else -0.3, 0.8))
        age = 22 + (season - entry) + int(r.integers(0, 2))
        birth = date(season - age, int(r.integers(1, 13)), int(r.integers(1, 29)))
        p = Player(n, name, pos, team, talent, birth, entry, rnd)
        self.players.append(p)
        return p

    def depth(self, team: str, pos: str, week: int) -> list[Player]:
        """Healthy players of a team-position, best first (a starter keeps a 0.3 edge)."""
        group = [p for p in self.players if p.team == team and p.pos == pos]
        healthy = [p for p in group if p.injured_until < week]
        prev = getattr(self, "_starters", {}).get((team, pos))
        return sorted(healthy, key=lambda p: -(p.talent + (0.3 if p is prev else 0.0)))

    # -- the calendar ---------------------------------------------------------------------

    @staticmethod
    def first_thursday(season: int) -> date:
        d = date(season, 9, 5)
        return d + timedelta(days=(3 - d.weekday()) % 7)

    def schedule(self, season: int) -> list[dict]:
        """Circle-method round robin of the 8 teams; byes drop the game of the two teams off
        (their opponents meet each other instead)."""
        teams = list(TEAMS)
        rot = teams[1:]
        shift = SEASONS.index(season)
        rot = rot[shift:] + rot[:shift]
        games = []
        thu = self.first_thursday(season)
        for w in range(1, WEEKS + 1):
            ring = [teams[0], *rot]
            pairs = [(ring[i], ring[-1 - i]) for i in range(4)]
            rot = rot[-1:] + rot[:-1]
            off = BYES.get(w, ())
            if off:
                keep = [pr for pr in pairs if pr[0] not in off and pr[1] not in off]
                left = [t for pr in pairs for t in pr if t not in off and pr not in keep]
                pairs = keep + ([tuple(left)] if len(left) == 2 else [])
            slots = [
                (thu, "20:15"),
                (thu + timedelta(days=3), "13:00"),
                (thu + timedelta(days=3), "16:25"),
                (thu + timedelta(days=4), "20:15"),
            ]
            if len(pairs) == 3:
                slots = [slots[0], slots[1], slots[3]]
            for i, (a, b) in enumerate(pairs):
                home, away = (a, b) if (w + i + shift) % 2 else (b, a)
                day, time = slots[i]
                games.append(
                    {
                        "game_id": f"{season}_{w:02d}_{away}_{home}",
                        "season": season,
                        "game_type": "REG",
                        "week": w,
                        "gameday": day.isoformat(),
                        "weekday": day.strftime("%A"),
                        "gametime": time,
                        "away_team": away,
                        "home_team": home,
                        "away_coach": f"Coach {away}",
                        "home_coach": f"Coach {home}",
                        "spread_line": 1.5,
                        "total_line": 45.5,
                    }
                )
            thu += timedelta(days=7)
        return games

    # -- one game --------------------------------------------------------------------------

    def play_team(self, season: int, g: dict, team: str, week: int) -> dict:
        r = self.rng
        opp = g["away_team"] if team == g["home_team"] else g["home_team"]
        lines: dict[int, dict] = {}
        snaps: dict[int, float] = {}
        for pos, shares in SNAP_SHARE.items():
            for rank, p in enumerate(self.depth(team, pos, week)[: len(shares)]):
                snaps[p.n] = float(np.clip(shares[rank] + r.normal(0, 0.03), 0.02, 1.0))
                if rank == 0:
                    self._starters[(team, pos)] = p
        players = {p.n: p for p in self.players if p.n in snaps}
        n_pass, n_run = int(r.normal(33, 4)), int(r.normal(24, 4))
        catchers = [p for p in players.values() if p.pos != "QB"]
        w = np.array(
            [snaps[p.n] * TARGET_WEIGHT[p.pos] * np.exp(0.45 * p.talent) for p in catchers]
        )
        targets = r.multinomial(n_pass, w / w.sum())
        rushers = [p for p in players.values() if p.pos in ("RB", "QB")]
        wr = np.array(
            [
                snaps[p.n] * (0.25 if p.pos == "QB" else 1.0) * np.exp(0.3 * p.talent)
                for p in rushers
            ]
        )
        carries = r.multinomial(n_run, wr / wr.sum())
        team_air = 0.0
        for p, t in zip(catchers, targets, strict=True):
            rec = int(r.binomial(t, float(np.clip(0.64 + 0.04 * p.talent, 0.4, 0.85))))
            yds = int(round(rec * max(2.0, r.normal(10.0 + 1.2 * p.talent, 2.5))))
            air = float(t * max(0.0, ADOT[p.pos] + p.talent + r.normal(0, 1.5)))
            team_air += air
            td = int(r.poisson(0.045 * t * np.exp(0.2 * p.talent)))
            lines.setdefault(p.n, {}).update(
                targets=int(t),
                receptions=rec,
                receiving_yards=yds,
                receiving_tds=td,
                receiving_air_yards=int(round(air)),
            )
        for p, c in zip(rushers, carries, strict=True):
            yds = int(round(c * r.normal(4.3 + 0.4 * p.talent, 1.0)))
            td = int(r.poisson(0.035 * c))
            lines.setdefault(p.n, {}).update(carries=int(c), rushing_yards=yds, rushing_tds=td)
        qb = next((p for p in players.values() if p.pos == "QB"), None)
        if qb is not None:
            rec_lines = [v for k, v in lines.items() if players[k].pos != "QB"]
            lines.setdefault(qb.n, {}).update(
                attempts=n_pass,
                completions=sum(v.get("receptions", 0) for v in rec_lines),
                passing_yards=sum(v.get("receiving_yards", 0) for v in rec_lines),
                passing_tds=sum(v.get("receiving_tds", 0) for v in rec_lines),
                passing_interceptions=int(r.poisson(0.025 * n_pass)),
            )
        team_targets = int(targets.sum()) or 1
        n_snaps = n_pass + n_run + int(r.integers(2, 6))
        stats = []
        for n, line in lines.items():
            p = players[n]
            if not any(line.get(k, 0) for k in ("targets", "carries", "attempts")):
                continue
            ts = line.get("targets", 0) / team_targets
            ays = (line.get("receiving_air_yards", 0.0) / team_air) if team_air else 0.0
            ppr = (
                0.04 * line.get("passing_yards", 0)
                + 4 * line.get("passing_tds", 0)
                - 2 * line.get("passing_interceptions", 0)
                + 0.1 * line.get("rushing_yards", 0)
                + 6 * line.get("rushing_tds", 0)
                + line.get("receptions", 0)
                + 0.1 * line.get("receiving_yards", 0)
                + 6 * line.get("receiving_tds", 0)
            )
            stats.append(
                {
                    "player_id": p.gsis,
                    "player_name": p.name,
                    "position": p.pos,
                    "season": season,
                    "week": week,
                    "season_type": "REG",
                    "game_id": g["game_id"],
                    "team": team,
                    "opponent_team": opp,
                    **line,
                    "target_share": round(ts, 4),
                    "air_yards_share": round(ays, 4),
                    "wopr": round(1.5 * ts + 0.7 * ays, 4),
                    "fantasy_points_ppr": round(ppr, 2),
                }
            )
            xrec = 0.63 * line.get("targets", 0)
            self.rows["ff_opportunity"].append(
                {
                    "season": str(season),
                    "posteam": team,
                    "week": float(week),
                    "game_id": g["game_id"],
                    "player_id": p.gsis,
                    "full_name": p.name,
                    "position": p.pos,
                    "rec_attempt": float(line.get("targets", 0)),
                    "rush_attempt": float(line.get("carries", 0)),
                    "receptions_exp": round(xrec, 3),
                    "rec_yards_gained_exp": round(
                        xrec * 10.0 + 0.3 * line.get("receiving_air_yards", 0.0), 3
                    ),
                    "rec_touchdown_exp": round(0.045 * line.get("targets", 0), 4),
                    "rush_yards_gained_exp": round(4.3 * line.get("carries", 0), 3),
                    "rush_touchdown_exp": round(0.035 * line.get("carries", 0), 4),
                    "pass_yards_gained_exp": round(7.0 * line.get("attempts", 0), 3),
                    "pass_touchdown_exp": round(0.045 * line.get("attempts", 0), 4),
                    "pass_interception_exp": round(0.025 * line.get("attempts", 0), 4),
                    "receptions": float(line.get("receptions", 0)),
                    "rec_yards_gained": float(line.get("receiving_yards", 0)),
                    "total_fantasy_points": round(ppr, 2),
                }
            )
        self.rows["player_stats"].extend(stats)
        for n, share in snaps.items():
            p = players[n]
            self.rows["snap_counts"].append(
                {
                    "game_id": g["game_id"],
                    "season": season,
                    "game_type": "REG",
                    "week": week,
                    "player": p.name,
                    "pfr_player_id": p.pfr,
                    "position": p.pos,
                    "team": team,
                    "opponent": opp,
                    "offense_snaps": float(round(n_snaps * share)),
                    "offense_pct": round(share, 2),
                }
            )
        self.rows["team_stats"].append(
            {
                "season": season,
                "week": week,
                "team": team,
                "season_type": "REG",
                "game_id": g["game_id"],
                "opponent_team": opp,
                "carries": int(carries.sum()),
                "passing_epa": round(float(r.normal(2.0, 6.0)), 3),
            }
        )
        self._plays(season, g, team, opp, lines, players)
        return {"points": int(r.integers(10, 35))}

    def _plays(self, season, g, team, opp, lines, players) -> None:
        """A dozen plays per team-game (enough for EPA, pace, neutral pass rate and red-zone
        looks), drawn from the game's targets and carries."""
        r = self.rng
        tgt = [players[n].gsis for n, v in lines.items() for _ in range(v.get("targets", 0))]
        car = [
            players[n].gsis
            for n, v in lines.items()
            for _ in range(v.get("carries", 0))
            if players[n].pos == "RB"
        ]
        base = {
            "game_id": g["game_id"],
            "season": season,
            "week": g["week"],
            "season_type": "REG",
            "game_date": g["gameday"],
            "posteam": team,
            "defteam": opp,
            "home_team": g["home_team"],
            "away_team": g["away_team"],
            "home_coach": g["home_coach"],
            "away_coach": g["away_coach"],
            "qtr": 1.0,
            "down": 1.0,
            "score_differential": 0.0,
            "two_point_attempt": 0.0,
        }
        start = 1 if team == g["home_team"] else 501
        for i in range(12):
            passing = i < 7
            wp = 0.97 if i == 11 else round(float(r.uniform(0.25, 0.75)), 3)
            who = (r.choice(tgt) if tgt else None) if passing else (r.choice(car) if car else None)
            yl = float(r.integers(1, 99)) if i % 4 else float(r.integers(1, 20))
            self.rows["pbp"].append(
                {
                    **base,
                    "play_id": float(start + i),
                    "desc": f"play {i + 1}",
                    "play_type": "pass" if passing else "run",
                    "pass": float(passing),
                    "rush": float(not passing),
                    "qb_dropback": float(passing),
                    "epa": round(float(r.normal(0.05, 1.0)), 4),
                    "wp": wp,
                    "half_seconds_remaining": 1500.0 - 100 * i,
                    "yardline_100": yl,
                    "receiver_player_id": str(who) if passing and who else None,
                    "rusher_player_id": str(who) if not passing and who else None,
                    "yards_gained": float(r.integers(-2, 20)),
                    "air_yards": 7.0 if passing else None,
                }
            )

    # -- one season ------------------------------------------------------------------------

    def season(self, season: int) -> None:
        r = self.rng
        self._starters: dict[tuple[str, str], Player] = {}
        for p in self.players:
            p.injured_until, p.on_ir = 0, False
        games = self.schedule(season)
        traded: Player | None = None
        for w in range(1, WEEKS + 1):
            if w == TRADE_AFTER_WEEK + 1:
                wrs = self.depth(TEAMS[SEASONS.index(season)], "WR", w)
                traded = wrs[2] if len(wrs) > 2 else None
                if traded is not None:
                    traded.team = TEAMS[(SEASONS.index(season) + 3) % len(TEAMS)]
            week_games = [g for g in games if g["week"] == w]
            playing = {t for g in week_games for t in (g["home_team"], g["away_team"])}
            self._injury_report(season, w, week_games, playing)
            self._rosters(season, w, playing)
            self._depth_charts(season, w, week_games)
            for g in week_games:
                home = self.play_team(season, g, g["home_team"], w)
                away = self.play_team(season, g, g["away_team"], w)
                g.update(
                    home_score=home["points"],
                    away_score=away["points"],
                    result=home["points"] - away["points"],
                    total=home["points"] + away["points"],
                )
            # after the games: injuries (out for 1-3 weeks; 3 weeks = injured reserve) and
            # talent drift (a backup can overtake the starter)
            for p in self.players:
                if p.team in playing and p.injured_until < w and r.random() < 0.045:
                    k = int(r.integers(1, 4))
                    p.injured_until, p.on_ir = w + k, k >= 3
                p.talent += float(r.normal(0, 0.12))
        self.rows["schedules"].extend(games)
        # the offseason: the two weakest players of each team retire, rookies arrive
        if season != SEASONS[-1]:
            for team in TEAMS:
                mine = sorted((p for p in self.players if p.team == team), key=lambda p: p.talent)
                for old in mine[:2]:
                    self.players.remove(old)
                    self._new_player(old.pos, team, season + 1, veteran=False)
            if traded is not None:
                traded.team = TEAMS[SEASONS.index(season)]  # he re-signs with his old team

    def _injury_report(self, season: int, w: int, week_games: list[dict], playing: set) -> None:
        r = self.rng
        thu = self.first_thursday(season) + timedelta(days=7 * (w - 1))
        stamp = datetime.combine(thu - timedelta(days=1), datetime.min.time()) + timedelta(hours=20)
        for team in sorted(playing):
            group = [p for p in self.players if p.team == team]
            out = [p for p in group if p.injured_until >= w]
            questionable = [group[int(r.integers(0, len(group)))]]
            for p, status in [
                *((p, "Out") for p in out),
                *((p, "Questionable") for p in questionable),
            ]:
                self.rows["injuries"].append(
                    {
                        "season": season,
                        "game_type": "REG",
                        "team": team,
                        "week": w,
                        "gsis_id": p.gsis,
                        "position": p.pos,
                        "full_name": p.name,
                        "report_status": status,
                        "practice_status": None,
                        "date_modified": stamp.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )

    def _rosters(self, season: int, w: int, playing: set) -> None:
        for p in self.players:
            if p.team not in playing:
                continue  # a team on its bye week has no game-day roster
            status = (
                "RES"
                if p.injured_until >= w and p.on_ir
                else ("INA" if p.injured_until >= w else "ACT")
            )
            self.rows["rosters_weekly"].append(
                {
                    "season": season,
                    "week": w,
                    "team": p.team,
                    "position": p.pos,
                    "full_name": p.name,
                    "gsis_id": p.gsis,
                    "pfr_id": p.pfr,
                    "game_type": "REG",
                    "status": status,
                    "entry_year": p.entry,
                    "rookie_year": p.entry,
                    "years_exp": season - p.entry,
                }
            )

    def _depth_charts(self, season: int, w: int, week_games: list[dict]) -> None:
        for g in week_games:
            for team in (g["home_team"], g["away_team"]):
                for pos in ROSTER:
                    for rank, p in enumerate(self.depth(team, pos, w)[:3]):
                        first, last = p.name.split(" ", 1)
                        self.rows["depth_charts"].append(
                            {
                                "season": season,
                                "club_code": team,
                                "week": w,
                                "game_type": "REG",
                                "depth_team": str(rank + 1),
                                "last_name": last,
                                "first_name": first,
                                "formation": "Offense",
                                "gsis_id": p.gsis,
                                "position": pos,
                                "depth_position": pos,
                                "full_name": p.name,
                            }
                        )

    def rankings(self, season: int) -> None:
        """A preseason cheat sheet per position: rank by talent plus expert noise."""
        r = self.rng
        for pos in ROSTER:
            group = [p for p in self.players if p.pos == pos]
            noisy = sorted(group, key=lambda p: -(p.talent + r.normal(0, 0.6)))
            for i, p in enumerate(noisy):
                self.rows["ff_rankings_all"].append(
                    {
                        "fp_page": f"/nfl/rankings/{pos.lower()}-cheatsheets.php",
                        "page_type": f"redraft-{pos.lower()}",
                        "player": p.name,
                        "id": str(p.fp_id),
                        "pos": pos,
                        "team": p.team,
                        "ecr": float(i + 1),
                        "sd": 1.0,
                        "ecr_type": "rp",
                        "scrape_date": f"{season}-{PRESEASON_SCRAPE}",
                    }
                )

    def globals_(self, everyone: list[Player]) -> None:
        for team in TEAMS:
            self.rows["teams"].append(
                {
                    "team_abbr": team,
                    "team_name": f"Team {team}",
                    "team_conf": "AFC",
                    "team_division": "AFC East",
                }
            )
        for p in sorted(everyone, key=lambda p: p.n):
            self.rows["players"].append(
                {
                    "gsis_id": p.gsis,
                    "display_name": p.name,
                    "position": p.pos,
                    "position_group": p.pos,
                    "birth_date": p.birth.isoformat(),
                    "rookie_season": p.entry,
                    "draft_year": p.entry if p.draft_round is not None else None,
                    "draft_round": p.draft_round,
                    "draft_pick": None if p.draft_round is None else 32 * (p.draft_round - 1) + 1,
                    "draft_team": p.team if p.draft_round is not None else None,
                    "pfr_id": p.pfr,
                    "latest_team": p.team,
                }
            )
            self.rows["ff_playerids"].append(
                {
                    "gsis_id": p.gsis,
                    "name": p.name,
                    "position": p.pos,
                    "fantasypros_id": p.fp_id,
                    "pfr_id": p.pfr,
                    "birthdate": p.birth.isoformat(),
                    "draft_year": p.entry,
                }
            )


def main() -> None:
    world = World()
    everyone: dict[int, Player] = {}
    for season in SEASONS:
        for p in world.players:
            everyone[p.n] = p
        if season in RANKED_SEASONS:
            world.rankings(season)
        world.season(season)
    for p in world.players:
        everyone[p.n] = p
    world.globals_(list(everyone.values()))
    total = 0
    for name in INPUTS:
        rows = world.rows[name]
        if name in ("teams", "players", "ff_playerids", "ff_rankings_all"):
            total += write_input(name, None, rows)
            continue
        for season in SEASONS:
            part = [r for r in rows if int(r["season"]) == season]
            total += write_input(name, season, part)
    print(
        f"wrote {sum(len(v) for v in world.rows.values()):,} rows in {len(DTYPES)} datasets, "
        f"{total / 1024:.0f} KB (tests/golden/inputs/)"
    )


if __name__ == "__main__":
    main()
