"""Feature #9: the league luck index and the playoff odds (twm.league.luck / odds / odds_view),
on synthetic leagues only (fake team names, no network, no real league data)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import duckdb
import pytest

from twm.league import commands, store
from twm.league import odds as od
from twm.league.luck import LeagueSeason, Side, luck_table, swap_records

T0 = datetime(2026, 10, 5, 20, 0)
RULE = "TOTAL_POINTS_SCORED"


def season(weeks: list[list[tuple[int, int, float | None, float | None]]], *, teams: int = 4,
           playoff: int = 2, reg: int | None = None, mine: int | None = 1,
           rule: str = RULE) -> LeagueSeason:  # fmt: skip
    """``weeks``: per week (1, 2, ...) a list of (team, opponent, team score, opponent score)."""
    sides = []
    for w, games in enumerate(weeks, start=1):
        for a, b, sa, sb in games:
            sides += [Side(w, a, b, sa, None), Side(w, b, a, sb, None)]
    return LeagueSeason(1, 2026, 1, T0, {t: f"Fake {t}" for t in range(1, teams + 1)}, mine,
                        sides, reg or len(weeks), playoff, 1, rule, False)  # fmt: skip


def test_luck_table_all_play_expected_wins_and_luck():
    # week 1: scores 1:100 2:90 3:80 4:70 (1 beats 2, 3 beats 4); week 2: 1:60 2:120 3:110 4:50
    # (1 vs 3: 3 wins; 2 vs 4: 2 wins); week 3 not final (ignored)
    ls = season([[(1, 2, 100.0, 90.0), (3, 4, 80.0, 70.0)],
                 [(1, 3, 60.0, 110.0), (2, 4, 120.0, 50.0)],
                 [(1, 4, None, None), (2, 3, None, None)]])  # fmt: skip
    t = luck_table(ls)
    assert ls.final_weeks() == [1, 2]
    assert (t[1].record, t[2].record, t[3].record, t[4].record) == ("1-1", "1-1", "2-0", "0-2")
    assert (t[1].points_for, t[1].points_against) == (160.0, 200.0)
    assert t[1].all_play == "4-2" and t[3].all_play == "3-3" and t[4].all_play == "0-6"
    assert t[1].expected_wins == pytest.approx(3 / 3 + 1 / 3)
    assert t[3].expected_wins == pytest.approx(1 / 3 + 2 / 3)
    assert t[3].luck == pytest.approx(1.0) and t[1].luck == pytest.approx(1 - 4 / 3)
    assert sum(r.luck for r in t.values()) == pytest.approx(0.0)  # luck sums to zero


def test_tie_counts_half_and_swap_records():
    ls = season([[(1, 2, 100.0, 100.0), (3, 4, 80.0, 70.0)],
                 [(1, 3, 60.0, 110.0), (2, 4, 120.0, 50.0)]])  # fmt: skip
    t = luck_table(ls)
    assert t[1].record == "0-1-1" and t[1].win_points == 0.5
    assert t[1].all_play == "3-2-1" and t[1].expected_wins == pytest.approx(2.5 / 3 + 1 / 3)
    # team 1 under team 3's schedule: wk1 3 faced 4 -> 1 (100) vs 4 (70) W; wk2 3 faced 1 -> 1
    # faces 3 (60 vs 110) L. Under team 4's: wk1 4 faced 3 -> 100 vs 80 W; wk2 4 faced 2 -> L
    assert swap_records(ls, 1) == {2: "1-0-1", 3: "1-1", 4: "1-1"}


def _odds(ls: LeagueSeason, **kw) -> od.Odds:
    model = od.fit_shrunk(ls, ls.final_weeks(kw.get("through")))
    assert model is not None
    return od.simulate(ls, model, sims=kw.pop("sims", 4000), **kw)


def test_seeding_ties_broken_by_total_points():
    # every team 1-1 after the last regular-season week: points for 160 / 185 / 152 / 145
    ls = season([[(1, 2, 100.0, 90.0), (3, 4, 80.0, 70.0)],
                 [(2, 1, 95.0, 60.0), (4, 3, 75.0, 72.0)]])  # fmt: skip
    o = _odds(ls)
    assert o.remaining == ()
    assert {t: x.playoffs for t, x in o.teams.items()} == {1: 1.0, 2: 1.0, 3: 0.0, 4: 0.0}
    assert o.teams[2].seed1 == 1.0
    assert sum(x.title for x in o.teams.values()) == pytest.approx(1.0)


def test_clinched_and_eliminated_teams():
    # 2 of 4 teams make it; after 2 of 3 weeks teams 1 and 2 are 2-0, teams 3 and 4 are 0-2
    ls = season([[(1, 3, 100.0, 90.0), (2, 4, 110.0, 85.0)],
                 [(1, 4, 95.0, 80.0), (2, 3, 120.0, 70.0)],
                 [(1, 3, None, None), (2, 4, None, None)]])  # fmt: skip
    o = _odds(ls)
    assert o.remaining == (3,)
    assert o.teams[1].playoffs == 1.0 and o.teams[2].playoffs == 1.0
    assert o.teams[3].playoffs == 0.0 and o.teams[4].playoffs == 0.0
    assert o.teams[3].title == 0.0 and o.teams[1].title + o.teams[2].title == pytest.approx(1)
    lev = o.leverage  # the owner (team 1) is in whatever happens in week 3
    assert lev is not None and lev.week == 3 and 0.2 < lev.p_win < 0.95
    assert lev.playoffs_if_win == 1.0 and lev.playoffs_if_loss == 1.0


def test_two_equal_teams_are_a_coin_flip():
    ls = season([[(1, 2, 100.0, 90.0)], [(2, 1, 100.0, 90.0)]], teams=2, playoff=2)
    o = _odds(ls, sims=20_000)
    assert o.teams[1].playoffs == 1.0
    assert o.teams[1].seed1 == pytest.approx(0.5, abs=0.02)  # equal record and points
    assert o.teams[1].title == pytest.approx(0.5, abs=0.02)


def test_seeded_runs_repeat_and_seeds_differ():
    ls = season([[(1, 2, 100.0, 90.0), (3, 4, 80.0, 70.0)], [(1, 3, None, None),
                 (2, 4, None, None)], [(1, 4, None, None), (2, 3, None, None)]])  # fmt: skip
    a, b, c = _odds(ls, seed=7), _odds(ls, seed=7), _odds(ls, seed=8)
    assert a.teams == b.teams and a.leverage == b.leverage
    assert a.teams != c.teams


def test_unmapped_rule_and_bracket():
    ls = season([[(1, 2, 100.0, 90.0), (3, 4, 80.0, 70.0)]], rule="HEAD_TO_HEAD")
    with pytest.raises(od.OddsUnavailableError, match="not mapped"):
        _odds(ls)
    assert od.bracket_order(8) == [1, 8, 4, 5, 2, 7, 3, 6]
    assert od.bracket_order(4) == [1, 4, 2, 3]
    with pytest.raises(od.OddsUnavailableError):
        od.bracket_order(6)


def test_game_fraction_left():
    k, e = datetime(2026, 10, 6, 0, 15), datetime(2026, 10, 6, 4, 15)
    assert od.game_fraction_left(k, e, T0) == 1.0  # not started
    assert od.game_fraction_left(k, e, datetime(2026, 10, 6, 5, 0)) == 0.0  # over
    assert od.game_fraction_left(k, e, datetime(2026, 10, 6, 2, 15)) == pytest.approx(0.5)


def test_in_progress_week_keeps_points_and_simulates_the_rest():
    games = {"KC": (datetime(2026, 10, 4, 17), datetime(2026, 10, 4, 21)),  # over at the sync
             "NO": (datetime(2026, 10, 6, 0, 15), datetime(2026, 10, 6, 4, 15))}  # fmt: skip
    starters = [(1, "KC", 30.0, 20.0, False), (1, "NO", 0.0, 15.0, False),
                (2, "KC", 50.0, 20.0, False), (2, None, 0.0, 0.0, True)]  # fmt: skip
    live = od.live_from_rows(4, starters, games, T0, {1: 31.5, 2: None})
    assert live is not None
    assert live.already == {1: 31.5, 2: 50.0}  # ESPN's live total wins when present
    assert live.rem_mean == {1: 15.0, 2: 0.0} and live.rem_share[1] == pytest.approx(15 / 35)
    assert od.live_from_rows(4, [(1, "NO", 0.0, 15.0, False)], games, T0, {}) is None
    # team 2 already leads by more than team 1 can make up: it wins the week in every season
    ls = season([[(1, 2, 100.0, 90.0)], [(2, 1, 110.0, 95.0)], [(1, 2, None, None)],
                 [(2, 1, None, None)]], teams=2, playoff=2)  # fmt: skip
    done = od.LiveWeek(3, {1: 40.0, 2: 200.0}, {1: 15.0, 2: 0.0}, {1: 0.2, 2: 0.0})
    o = _odds(ls, live=done)
    assert o.live is done and [d.week for d in o.decided] == [3]
    assert o.decided[0].p_win == 0.0  # the owner (team 1) cannot catch up in week 3
    assert o.leverage is not None and o.leverage.week == 4  # the next open week instead


def test_model_check_rule_needs_enough_paired_team_weeks():
    weeks = [[(1, 2, 100.0 + i, 90.0 - i), (3, 4, 80.0 + 2 * i, 70.0)] for i in range(12)]
    ls = season(weeks)
    perfect = {w: {s.team: (float(s.score), 5.0) for s in ls.week_sides(w)} for w in range(1, 13)}
    good = od.check_models(ls, lambda w: perfect[w])
    assert good.a.n == 44 and good.b.n == 48 and good.paired == 44  # (a) needs 1 prior week
    assert good.pick == "b" and good.diff is not None and good.diff < 0
    few = od.check_models(ls, lambda w: perfect[w] if w <= 9 else None)
    assert few.paired == 32 and few.pick == "a"  # < 36 paired team-weeks: the simpler model
    none = od.check_models(ls, None)
    assert none.b.n == 0 and none.pick == "a"


def test_estimate_k_method_of_moments():
    # 4 teams, 2 weeks; team means 100, 100, 80, 80 with within-team spread
    ls = season([[(1, 2, 105.0, 95.0), (3, 4, 85.0, 75.0)],
                 [(1, 2, 95.0, 105.0), (3, 4, 75.0, 85.0)]])  # fmt: skip
    k = od.estimate_k(ls, [1, 2])
    assert k is not None and k.sigma == pytest.approx(50**0.5)  # ss 200, df 4
    tau2 = 400 / 3 - 50 / 2  # var of the means (ddof 1) - sigma^2 / n
    assert k.tau2 == pytest.approx(tau2) and k.k_hat == pytest.approx(50 / tau2)
    flat = season([[(1, 2, 100.0, 90.0), (3, 4, 90.0, 100.0)],
                   [(1, 2, 90.0, 100.0), (3, 4, 100.0, 90.0)]])  # fmt: skip
    assert od.estimate_k(flat, [1, 2]).tau2 == od.TAU2_FLOOR


# ---- the sync: the whole schedule and the seeding rule (synthetic ESPN league) -------------


@pytest.fixture
def synced(tmp_path) -> Path:
    pytest.importorskip("espn_api")
    from tests.league_fixtures import FAKE_S2, FAKE_SWID, LEAGUE_ID, FakeLeague
    from twm.league.espn_client import EspnClient
    from twm.league.sync import sync

    client = EspnClient(FakeLeague(), secrets=(FAKE_S2, FAKE_SWID), swid=FAKE_SWID)
    db = tmp_path / "league.duckdb"
    res = sync(client, db, league_id=LEAGUE_ID, now=T0)
    assert res.rows["league_schedule"] == 27  # 13 two-team periods + one bye side
    return db


def test_sync_stores_the_full_schedule_with_null_future_scores(synced):
    con = duckdb.connect(str(synced), read_only=True)
    rows = con.execute(
        "SELECT matchup_period, league_team_id, league_opponent_id, score, live_score, winner "
        "FROM league_schedule ORDER BY 1, 2"
    ).fetchall()
    kv = dict(con.execute("SELECT key, value FROM league_settings").fetchall())
    con.close()
    by = {(w, t): (o, s, lv, wn) for w, t, o, s, lv, wn in rows}
    assert sorted({w for w, _ in by}) == list(range(1, 15))
    assert by[(1, 1)] == (2, 110.5, None, "HOME") and by[(2, 2)] == (1, 99.5, None, "AWAY")
    assert by[(3, 1)] == (2, None, 61.25, "UNDECIDED")  # the live week: no final score yet
    assert by[(9, 2)] == (1, None, None, "UNDECIDED")  # a future week: NULL scores
    assert by[(14, 1)] == (None, None, None, "UNDECIDED") and (14, 2) not in by  # a bye
    assert kv["playoff_seeding_rule"] == RULE and kv["previous_seasons"] == ""
    assert kv["matchup_tie_rule"] == "NONE"


def test_a_failed_schedule_read_keeps_the_stored_one(synced, monkeypatch):
    from tests.league_fixtures import FAKE_S2, FAKE_SWID, LEAGUE_ID, FakeLeague
    from twm.league.espn_client import EspnClient, EspnError
    from twm.league.sync import sync

    client = EspnClient(FakeLeague(), secrets=(FAKE_S2, FAKE_SWID), swid=FAKE_SWID)

    def boom():
        raise EspnError("reading the season schedule failed")

    monkeypatch.setattr(client, "schedule", boom)
    res = sync(client, synced, league_id=LEAGUE_ID, now=datetime(2026, 10, 6))
    assert "league_schedule" not in res.rows and any("schedule" in n for n in res.notes)
    con = duckdb.connect(str(synced), read_only=True)
    assert con.execute("SELECT count(*) FROM league_schedule").fetchone()[0] == 27
    con.close()


# ---- `twm league odds` and the report section on a synthetic store ------------------------

LID, YEAR = 4242, 2026
SCORES = {1: [(1, 2, 120.0, 95.0), (3, 4, 101.0, 99.0)], 2: [(1, 3, 88.0, 107.0),
          (2, 4, 111.0, 93.0)], 3: [(1, 4, 130.0, 92.0), (2, 3, 97.0, 104.0)]}  # fmt: skip
FUTURE = [(1, 2), (3, 4)], [(1, 3), (2, 4)], [(1, 4), (2, 3)]


def _store(db: Path, *, schedule: bool = True) -> None:
    con = store.connect(db)
    base = {"league_id": LID, "season": YEAR, "week": 4, "synced_at": T0}
    kv = {"team_count": 4, "reg_season_count": 6, "reg_season_final_week": 6,
          "playoff_team_count": 2, "playoff_matchup_period_length": 1,
          "playoff_seeding_rule": RULE, "previous_seasons": ""}  # fmt: skip
    settings = [{"key": k, "value": str(v)} for k, v in kv.items()]
    store.write(con, "league_settings", store.frame("league_settings", settings, **base),
                league_id=LID, season=YEAR)  # fmt: skip
    teams = [{"league_team_id": t, "league_team_abbrev": f"F{t}", "league_team_name":
              f"Fake Team {t}", "league_is_mine": t == 1} for t in range(1, 5)]  # fmt: skip
    store.write(con, "league_teams", store.frame("league_teams", teams, **base))
    rows = []
    games = {w: [(a, b, x, y) for a, b, x, y in g] for w, g in SCORES.items()}
    games |= {w + 4: [(a, b, None, None) for a, b in g] for w, g in enumerate(FUTURE)}
    for w, g in games.items():
        for i, (a, b, x, y) in enumerate(g):
            for t, o, s, home in ((a, b, x, True), (b, a, y, False)):
                rows.append({"matchup_period": w, "matchup_id": 10 * w + i, "league_team_id": t,
                             "league_opponent_id": o, "is_home": home, "playoff_tier": "NONE",
                             "winner": "UNDECIDED" if s is None else "HOME",
                             "score": s, "live_score": None})  # fmt: skip
    if schedule:
        store.write(con, "league_schedule", store.frame("league_schedule", rows, **base),
                    league_id=LID, season=YEAR)  # fmt: skip
    con.close()


@pytest.fixture
def cli(tmp_path, monkeypatch):
    for k in ("ENABLE_MY_LEAGUE", "ESPN_LEAGUE_ID", "ESPN_YEAR", "ESPN_S2", "ESPN_SWID"):
        monkeypatch.delenv(k, raising=False)
    env = tmp_path / ".env"
    env.write_text(f"ENABLE_MY_LEAGUE=true\nESPN_LEAGUE_ID={LID}\nESPN_YEAR={YEAR}\n")
    db = tmp_path / "league.duckdb"
    monkeypatch.setattr(commands, "default_env_file", lambda: env)
    monkeypatch.setattr(commands, "paths", lambda: (db, tmp_path / "wh.duckdb", tmp_path / "r.md"))
    monkeypatch.setattr(commands, "lists_paths", lambda: (tmp_path / "pred.duckdb", None))
    return db


def test_odds_command_prints_the_table_and_the_owner_lines(cli):
    _store(cli)
    out: list[str] = []
    assert commands.run_odds(None, 2000, 11, out.append) == 0
    text = "\n".join(out)
    mine = [x for x in out if x.startswith("*")]
    assert len(mine) == 1 and "Fake Team 1" in mine[0] and "2-1" in mine[0]
    assert "final weeks 1-3" in text and "weeks 4-6" in text
    assert "Your leverage, week 4" in text and "other team's schedule" in text
    assert "K=3" in text and "K=12" in text and "Pseudo-weeks: assumed 6" in text
    assert "(b) 0 team-weeks" in text and "ESPN lists no earlier season" in text
    again: list[str] = []
    commands.run_odds(None, 2000, 11, again.append)
    assert again == out  # seeded
    past: list[str] = []
    assert commands.run_odds(2, 2000, 11, past.append) == 0
    assert any("final weeks 1-2" in x for x in past)


def test_odds_absent_without_a_schedule_or_my_league(cli, monkeypatch, tmp_path):
    from twm.league.odds_view import for_report

    out: list[str] = []
    assert commands.run_odds(None, 2000, 1, out.append) == 2  # nothing synced
    _store(cli, schedule=False)
    out.clear()
    assert commands.run_odds(None, 2000, 1, out.append) == 2
    assert "no season schedule" in out[-1]
    con = store.connect(cli, read_only=True)
    assert for_report(con, None, None) is None  # the report shows no section
    con.close()
    monkeypatch.setattr(commands, "default_env_file", lambda: tmp_path / "none.env")
    out.clear()
    assert commands.run_odds(None, 2000, 1, out.append) == 2 and "My League is off" in out[0]


def test_report_section_renders_with_the_owner_marked(cli):
    from twm.league.odds_view import build, render_section

    _store(cli)
    con = store.connect(cli, read_only=True)
    view = build(con, None, None, sims=1000)
    con.close()
    html = str(render_section(view))
    assert 'data-section="odds"' in html and 'class="pick"' in html and "Fake Team 1" in html


def test_publish_and_web_never_use_the_league_odds():
    root = Path(__file__).resolve().parents[1]
    words = ("twm.league.odds", "twm.league.luck", "odds_view", "league_schedule", "luck_table")
    hits = []
    for base, pats in ((root / "src" / "twm" / "publish", ("*.py",)),
                       (root / "web", ("*.ts", "*.tsx", "*.js", "*.mjs"))):  # fmt: skip
        for pat in pats:
            for f in base.rglob(pat):
                if "node_modules" in f.parts or ".next" in f.parts:
                    continue
                text = f.read_text(errors="ignore")
                hits += [f"{f.name}: {w}" for w in words if w in text]
    assert not hits


def test_a_store_synced_before_the_schedule_existed_says_sync_first() -> None:
    import duckdb
    import pytest

    from twm.league import luck

    con = duckdb.connect(":memory:")  # no league_schedule table: an older store
    with pytest.raises(luck.LuckUnavailableError, match="twm league sync"):
        luck.load(con)
