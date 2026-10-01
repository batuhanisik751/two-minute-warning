"""My League step H6-a: the trade checker (`twm league trade`). Synthetic data only: fake team
and player names, a hand-made schedule and backtest; the network is blocked by test_league's
autouse fixture, and the real .env / data are never read."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import duckdb
import polars as pl
import pytest
import yaml

from tests.league_fixtures import LEAGUE_ID
from tests.test_league import GOOD_ENV, _run, isolated  # noqa: F401 - isolated is autouse
from tests.test_league_f3 import LEAGUE_SLOTS, VERSIONS, _league_db, _p, _predictions, _write
from twm.league import commands, store, trade

SEASON = 2026
TEAMS = ("KC", "BUF", "SF", "DAL", "PHI", "MIA", "NYJ", "DET")
BYES = {6: ("KC", "BUF"), 9: ("SF", "DAL")}  # week -> teams without a game


def _warehouse(path: Path) -> Path:
    """dim_week (18 REG weeks) and fact_schedule: the teams not on bye paired in order."""
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE dim_week (season INTEGER, week INTEGER, season_type VARCHAR)")
    con.execute("CREATE TABLE fact_schedule (season INTEGER, week INTEGER, season_type VARCHAR, "
                "home_team VARCHAR, away_team VARCHAR)")  # fmt: skip
    for w in range(1, 19):
        con.execute("INSERT INTO dim_week VALUES (?, ?, 'REG')", [SEASON, w])
        on = [t for t in TEAMS if t not in BYES.get(w, ())]
        for h, a in zip(on[::2], on[1::2], strict=True):
            con.execute("INSERT INTO fact_schedule VALUES (?, ?, 'REG', ?, ?)", [SEASON, w, h, a])
    con.close()
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


MISSES = (-6.0, -3.0, -1.0, 0.0, 1.0, 3.0, 6.0)  # symmetric per-game misses


def _backtest(folder: Path) -> dict:
    """A tiny backtest snapshot: 13-14 weeks left with the symmetric MISSES, and 7 weeks left
    with huge misses (must be left out for a list with about 14 weeks left)."""
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    for pos in ("QB", "RB", "WR", "TE"):
        for h, scale in ((13, 1.0), (14, 1.0), (7, 10.0)):
            for i, m in enumerate(MISSES):
                rows.append((2020, 18 - h, f"{pos}-{h}-{i}", pos, 10.0, h, 10.0 + scale * m))
    df = pl.DataFrame(rows, schema=["season", "week", "entity_id", "rank_group", "score",
                                    "horizon", "ros_ppg"], orient="row")  # fmt: skip
    out = {"seasons": "2011-2025"}
    for table, cols in (("predictions", ["season", "week", "entity_id", "rank_group", "score",
                                         "horizon"]),
                        ("outcomes", ["season", "week", "entity_id", "ros_ppg"])):  # fmt: skip
        f = folder / f"{table}.parquet"
        df.select(cols).write_parquet(f)
        out[table] = {"file": str(f), "sha256": _sha(f), "rows": df.height}
    return out


def _pins(path: Path) -> Path:
    v = VERSIONS["regression_watch"]
    pin = {"season": SEASON, "model_version": v, "file": f"a/{v}.json", "sha256": "0" * 64,
           "backtest": _backtest(path.parent / "bt")}  # fmt: skip
    body = {"regression_watch": pin}
    path.write_text(yaml.safe_dump(body))
    return path


# (espn id, name, position, fantasy team, slot, NFL team, PPG or None, tag, ESPN status)
PLAYERS = [
    (101, "Mine Quarterback", "QB", 1, "QB", "KC", 20.0, None, "ACTIVE"),
    (102, "Mine Back One", "RB", 1, "RB", "KC", 15.0, None, "ACTIVE"),
    (103, "Mine Back Two", "RB", 1, "RB", "BUF", 12.0, None, "ACTIVE"),
    (104, "Mine Back Three", "RB", 1, "BE", "SF", 8.0, None, "ACTIVE"),
    (105, "Mine Receiver One", "WR", 1, "WR", "DAL", 14.0, "legit", "ACTIVE"),
    (106, "Mine Receiver Two", "WR", 1, "WR", "PHI", 11.0, "sell_high", "QUESTIONABLE"),
    (107, "Mine Receiver Three", "WR", 1, "RB/WR/TE", "MIA", 9.0, None, "ACTIVE"),
    (108, "Mine Tight End", "TE", 1, "TE", "NYJ", 7.0, None, "ACTIVE"),
    (109, "Mine Kicker", "K", 1, "K", "KC", None, None, "ACTIVE"),
    (110, "Mine Defense", "D/ST", 1, "D/ST", "KC", None, None, "ACTIVE"),
    (111, "D'Andre Fakeswift", "RB", 1, "BE", "DET", 5.0, None, "ACTIVE"),
    (112, "Mine Injured Back", "RB", 1, "IR", "KC", 16.0, None, "INJURY_RESERVE"),
    (201, "Beta Quarterback", "QB", 2, "QB", "DET", 18.0, None, "ACTIVE"),
    (202, "Beta Back", "RB", 2, "RB", "DAL", 13.0, "buy_low", "ACTIVE"),
    (203, "Beta Receiver", "WR", 2, "WR", "SF", 13.0, None, "OUT"),
    (204, "Beta Receiver Two", "WR", 2, "WR", "MIA", 10.0, None, "ACTIVE"),
    (205, "Beta Tight End", "TE", 2, "TE", "PHI", 6.0, None, "ACTIVE"),
    (206, "Beta Twin Back", "RB", 2, "RB", "KC", 15.0, None, "ACTIVE"),
    (301, "Free Agent Runner", "RB", None, "", "NYJ", 9.5, None, "ACTIVE"),
    (401, "Gamma Back", "RB", 3, "RB", "DET", 6.0, None, "ACTIVE"),
]


def _gsis(i: int) -> str:
    return f"00-00{i:05d}"


def _league(path: Path, slots: dict[str, int] = LEAGUE_SLOTS) -> Path:
    rows = [_p(i, n, pos, team=t, slot=s, gsis=_gsis(i), pro=pro, status=st)
            for i, n, pos, t, s, pro, _v, _tag, st in PLAYERS if t is not None]  # fmt: skip
    free = [_p(i, n, pos, team=None, gsis=_gsis(i), pro=pro)
            for i, n, pos, t, _s, pro, _v, _tag, _st in PLAYERS if t is None]  # fmt: skip
    _league_db(path, rosters=rows, free_agents=free, current=4, slots=slots)
    con = store.connect(path)  # the league's last scoring week, as a sync stores it
    con.execute("INSERT INTO league_settings VALUES (?, ?, 4, ?, 'final_week', '17')",
                [LEAGUE_ID, SEASON, datetime(2026, 9, 30, 18)])  # fmt: skip
    con.close()
    return path


def _lists(path: Path) -> Path:
    rows = [("regression_watch", pos if pos != "D/ST" else "DST", _gsis(i), r, v, tag, 3, None)
            for r, (i, _n, pos, _t, _s, _pro, v, tag, _st) in enumerate(PLAYERS, start=1)
            if v is not None]  # fmt: skip
    return _predictions(path, rows)


@pytest.fixture
def world(isolated, monkeypatch, tmp_path):  # noqa: F811
    env_file, (db, wh, _rep) = isolated
    env_file.write_text(GOOD_ENV)
    _league(db)
    _warehouse(wh)
    preds, pins = _lists(tmp_path / "p.duckdb"), _pins(tmp_path / "pins.yaml")
    monkeypatch.setattr(commands, "lists_paths", lambda: (preds, pins))
    return db, wh, preds, pins


def _check(world, give: list[str], get: list[str], **kw) -> trade.TradeRun:
    db, wh, preds, pins = world
    con = store.connect(db, read_only=True)
    try:
        return trade.run(con, preds, wh, give=give, get=get, pins_path=pins, **kw)
    finally:
        con.close()


# --------------------------------------------------------------------------------------
# the command, end to end
# --------------------------------------------------------------------------------------


def test_cli_trade_prints_both_sides_with_a_range_and_writes_nothing(world):
    db, wh, preds, pins = world
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (db, wh, preds)}
    code, out = _run("trade", "--give", "mine receiver two", "--get", "BETA BACK", raw=True)
    assert code == 0, out
    assert "Fake Team Alpha (you)" in out and "Fake Team Beta" in out
    assert "You (Fake Team Alpha): a close call (weeks 4-17, the rest of the season): about " in out
    assert "range " in out and "(80% interval); gains in " in out
    assert "% of 4,000 simulated seasons" in out and " of 100 " not in out
    assert "Fake Team Beta: a close call (weeks 4-17, " in out and " will " not in out
    assert "weeks 4-17" in out and "fantasy playoffs are not modelled" in out
    assert "K and D/ST are not projected" in out
    assert "Regression Watch tag: Sell-high" in out and "Regression Watch tag: Buy-low" in out
    assert "ESPN status QUESTIONABLE (counted as playing)" in out
    assert "13-14 weeks left" in out and "2011-2025" in out
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (db, wh, preds)} == before


# --------------------------------------------------------------------------------------
# the lineup optimizer over the weeks counted: FLEX and byes
# --------------------------------------------------------------------------------------


def _pl(i: int, pos: str, nfl: str, status: str = "") -> trade.Player:
    return trade.Player(i, f"P{i}", pos, nfl, 1, "BE", status, _gsis(i))


SPEC = ((1, "QB", "KC"), (2, "RB", "KC"), (3, "RB", "BUF"), (4, "RB", "SF"), (5, "WR", "DAL"),
        (6, "WR", "PHI"), (7, "WR", "MIA"), (8, "TE", "NYJ"))  # fmt: skip
ROSTER = [_pl(*x) for x in SPEC]
PPG = {1: 20.0, 2: 15.0, 3: 12.0, 4: 8.0, 5: 14.0, 6: 11.0, 7: 9.0, 8: 7.0}


def _sched(weeks=(5, 6, 7)) -> trade.Schedule:
    plays = {t: frozenset(w for w in range(1, 19) if t not in BYES.get(w, ())) for t in TEAMS}
    return trade.Schedule(tuple(weeks), plays, 18)


def test_each_week_takes_the_best_lineup_with_flex_and_a_bye_scores_zero():
    values = {k: trade.Value(v, "projection") for k, v in PPG.items()}
    slots = trade.skill_slots(LEAGUE_SLOTS)
    assert slots == {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "RB/WR/TE": 1}  # K, D/ST fixed
    res = trade.season_lineup(ROSTER, values, _sched(), slots, out_week=None)
    # week 5 and 7: 20 + 15 + 12 + 14 + 11 + 7 + FLEX 9 (the WR over the RB 8) = 88
    # week 6 (KC, BUF on bye): QB 0, RB 8 + 0, WR 14 + 11, TE 7, FLEX 9 = 49
    assert [w[1] for w in res.weekly] == [88.0, 49.0, 88.0] and res.total == 225.0
    assert res.starts[1] == 2 and res.starts[2] == 2  # a bye week is never a start
    assert res.starts[4] == 1 and res.starts[7] == 3
    assert sum(PPG[k] * n for k, n in res.starts.items()) == res.total


def test_espn_out_counts_only_the_week_it_is_for_and_an_ir_slot_never_starts():
    values = {k: trade.Value(v, "projection") for k, v in PPG.items()} | {
        9: trade.Value(30.0, "projection")}  # fmt: skip
    roster = [*ROSTER[:4], _pl(5, "WR", "DAL", "OUT"), *ROSTER[5:],
              trade.Player(9, "P9", "WR", "NYJ", 1, "IR", "INJURY_RESERVE", _gsis(9))]  # fmt: skip
    res = trade.season_lineup(roster, values, _sched((5, 7)), trade.skill_slots(LEAGUE_SLOTS),
                              out_week=5)  # fmt: skip
    assert [w[1] for w in res.weekly] == [82.0, 88.0]  # week 5 without the WR 14: FLEX RB 8
    assert res.starts[5] == 1 and 9 not in res.starts


# --------------------------------------------------------------------------------------
# roster limit, names, the guard
# --------------------------------------------------------------------------------------


def test_over_the_roster_limit_the_lowest_bench_player_is_dropped(world):
    db = world[0]
    db.unlink()
    _league(db, slots=LEAGUE_SLOTS | {"BE": 2})  # limit 11 = your 11 players outside IR
    res = _check(world, ["Mine Receiver Two"], ["Beta Back", "Beta Receiver"])
    assert res.data.roster_limit == 11
    me, partner = res.check.sides
    assert [p.name for p in me.dropped] == ["D'Andre Fakeswift"]  # 5.0, the lowest bench
    assert sum(1 for p in me.after_roster if not p.on_ir_slot) == 11
    assert "Mine Injured Back" in [p.name for p in me.after_roster]  # IR slot: not counted
    assert partner.dropped == [] and partner.open_spots == 1
    out = "\n".join(trade.notes(res.check, res.data, res.sched))
    assert "roster limit 11: you must drop D'Andre Fakeswift (RB, 5.0)" in out
    assert "Fake Team Beta: the trade opens 1 roster spot(s)" in out


def _pool() -> list[trade.Player]:
    return [trade.Player(i, n, "RB" if pos == "D/ST" else pos, pro, t)
            for i, n, pos, t, _s, pro, _v, _tag, _st in PLAYERS]  # fmt: skip


@pytest.mark.parametrize("query", ["d’andre FAKESWIFT", "D.Andre Fakeswift", "fakeswift",
                                   "  dandre   fakeswift "])  # fmt: skip
def test_names_ignore_case_accents_and_punctuation(query):
    assert trade.match(query, _pool(), {}).espn_id == 111


@pytest.mark.parametrize(("query", "expected"), [
    ("receiver", "'receiver' matches 5 players: Beta Receiver (WR, SF, Fake Team Beta); "),
    ("Beta Recever", "no player named 'Beta Recever' on a synced roster or among the synced "
     "free agents; did you mean: Beta Receiver (WR, SF, Fake Team Beta)"),
    ("Nobody At All", "no player named 'Nobody At All'"),
])  # fmt: skip
def test_unknown_or_ambiguous_names_list_the_candidates(query, expected):
    with pytest.raises(trade.TradeError) as e:
        trade.match(query, _pool(), {1: "Fake Team Alpha", 2: "Fake Team Beta"})
    assert expected in str(e.value)


@pytest.mark.parametrize(("give", "get", "expected"), [
    ("Beta Back", "Beta Receiver", "--give Beta Back (RB, DAL, Fake Team Beta) is not on your "
     "team"),
    ("Mine Back One", "Mine Back Two", "is already yours"),
    ("Mine Back One", "Beta Back|Gamma Back", "the --get players are on 2 teams"),
    ("Mine Kicker", "Beta Back", "Mine Kicker: K and D/ST are not projected"),
    ("Mine Back One", "Mine Back One", "the same player is named twice"),
])  # fmt: skip
def test_the_cli_refuses_a_trade_it_cannot_check_in_one_line(world, give, get, expected):
    args = ["trade", "--give", give] + [a for g in get.split("|") for a in ("--get", g)]
    if give == get:
        args = ["trade", "--give", give, "--give", give, "--get", "Beta Back"]
    code, out = _run(*args, raw=True)
    assert code == commands.EXIT_UNAVAILABLE and expected in out, out
    assert len(out.strip().splitlines()) == 1


def test_trade_is_guarded_like_the_other_league_commands(isolated):  # noqa: F811
    code, out = _run("trade", "--give", "A", "--get", "B", raw=True)
    assert code == commands.EXIT_UNAVAILABLE and "My League is off" in out
    assert len(out.strip().splitlines()) == 1 and not isolated[1][0].exists()


# --------------------------------------------------------------------------------------
# the range
# --------------------------------------------------------------------------------------


def test_every_change_comes_with_an_ordered_80_percent_range(world):
    code, out = _run("trade", "--give", "Mine Receiver Two", "--get", "Beta Back", "--json",
                     raw=True)  # fmt: skip
    assert code == 0, out
    body = json.loads(out)
    assert body["level"] == 0.8 and body["list_week"] == 3 and body["weeks"] == list(range(4, 18))
    for s in body["sides"]:
        assert s["lo"] < s["hi"] and s["lo"] <= s["delta"] <= s["hi"], s
        assert 0.0 <= s["share_up"] <= 1.0 and "range " in s["verdict"]
    me, partner = body["sides"]
    assert me["mine"] and me["projection_delta"] == round(me["after"] - me["before"], 6)
    assert me["gets"][0]["tag"] == "Buy-low" and me["gives"][0]["tag"] == "Sell-high"


def test_a_symmetric_trade_gives_zero_centered_deltas(world):
    res = _check(world, ["Mine Back One"], ["Beta Twin Back"])  # same position, team, PPG
    for s in res.check.sides:
        assert s.delta == 0.0 == s.main.projection_delta and s.lo < 0 < s.hi
        assert abs(s.lo + s.hi) <= 0.1 * (s.hi - s.lo)  # the range is centred on zero
        assert s.share_up is not None and 0.3 < s.share_up < 0.6
        assert ": a close call (weeks 4-17" in trade.verdict(s, res.check)


def test_the_range_uses_the_backtest_rows_with_about_as_many_weeks_left(world):
    pins = world[3]
    near = trade.residual_pool(14, pins)
    assert near.horizons == dict.fromkeys(("QB", "RB", "WR", "TE"), (13, 14))
    assert all(abs(a).max() == 6.0 and len(a) == 14 for a in near.by_position.values())
    far = trade.residual_pool(3, pins)  # none within 2 weeks: the nearest (7) only
    assert far.horizons["RB"] == (7, 7) and abs(far.by_position["RB"]).max() == 60.0


def test_free_agents_only_have_no_partner_side_and_the_out_week_is_respected(world):
    res = _check(world, ["Mine Back Three"], ["Free Agent Runner"])
    assert len(res.check.sides) == 1 and res.check.out_week == 4
    lines = trade.text(res.check, res.data, res.sched, None)
    assert "(only free agents come in: there is no partner side)" in lines
    assert any("Free Agent Runner (RB, NYJ; from free agent)" in x for x in lines)
    week4 = _check(world, ["Mine Receiver Two"], ["Beta Receiver"]).check.sides[0].after.weekly
    assert week4[0][0] == 4 and 203 not in week4[0][2]  # ESPN: OUT for week 4
    assert 203 in week4[1][2]


def test_a_later_backfill_of_an_older_week_never_replaces_the_current_roster(world):
    con = store.connect(world[0])
    old = [_p(102, "Mine Back One", "RB", team=2, slot="RB", gsis=_gsis(102), pro="KC")]
    _write(con, "league_rosters", old, 3, datetime(2026, 10, 1, 9))  # synced after week 4
    _write(con, "league_free_agents", [], 3, datetime(2026, 10, 1, 9))
    con.close()
    res = _check(world, ["Mine Back One"], ["Beta Back"])
    assert res.data.sync_week == 4 and res.check.sides[0].gives[0].team_id == 1


def test_radar_and_report_also_read_the_newest_week_not_the_latest_sync(tmp_path):
    """One helper (store.current) for every reader of the current rosters / free agents."""
    from tests.test_league_f3 import FREE, LISTS, ROSTERS, T_SYNC
    from tests.test_league_f3 import _pins as f3_pins
    from twm.league import personal, report

    db = _league_db(tmp_path / "l.duckdb", rosters=ROSTERS, free_agents=FREE)  # week 4
    con = store.connect(db)  # then a backfill of week 3, synced later, with other rows
    _write(con, "league_rosters", ROSTERS[:1], 3, datetime(2026, 10, 1, 9))
    _write(con, "league_free_agents", [], 3, datetime(2026, 10, 1, 9))
    con.close()
    preds, pins = _predictions(tmp_path / "p.duckdb", LISTS), f3_pins(tmp_path / "pins.yaml")
    con = store.connect(db, read_only=True)
    try:
        assert store.current(con, "league_rosters").week == 4
        assert store.latest(con, "league_rosters").week == 3  # the backfill is the latest sync
        view = personal.load_league(con)
        assert view.week == 4 and view.free_agents.height == len(FREE)
        assert view.rosters.height == len(ROSTERS)
        d = report.gather(con, preds, None, pins_path=pins)
    finally:
        con.close()
    assert d.radar.sync_week == 4 and d.synced_at == T_SYNC


def test_a_player_outside_the_list_gets_his_season_average_labelled_else_no_number():
    proj = trade.Projections(3, 14, "v", {"G1": {"score": 12.0, "band": "legit"}})
    a, b, c = (trade.Player(i, f"P{i}", "WR", "KC", 1, entity_id=f"G{i}") for i in (1, 2, 3))
    assert trade.value_of(a, proj, {}) == trade.Value(12.0, "projection", "", "= xFP - + FPOE - "
                                                      "per game; so far - points per game over - "
                                                      "games (xFP -, FPOE -)")  # fmt: skip
    assert trade.value_of(b, proj, {"G2": (9.5, 2)}) == trade.Value(
        9.5, "season_average", numbers="9.50 per game over 2 game(s)")  # fmt: skip
    assert trade.value_of(c, proj, {}).ppg is None  # no number: never invented


# --------------------------------------------------------------------------------------
# step H6-a2: the fantasy playoffs apart, the verdict's words, the playoff settings synced
# --------------------------------------------------------------------------------------


def _settings(db: Path, **kv: str) -> None:
    con = store.connect(db)
    for k, v in kv.items():
        con.execute("INSERT INTO league_settings VALUES (?, ?, 4, ?, ?, ?)",
                    [LEAGUE_ID, SEASON, datetime(2026, 9, 30, 18), k, v])  # fmt: skip
    con.close()


@pytest.mark.parametrize("key", ["reg_season_final_week", "reg_season_count"])
def test_the_playoff_weeks_are_shown_apart_and_the_verdict_uses_the_regular_season(world, key):
    _settings(world[0], **{key: "14", "playoff_team_count": "4",
                           "playoff_matchup_period_length": "1"})  # fmt: skip
    res = _check(world, ["Mine Receiver Two"], ["Beta Back"])
    assert res.check.split == [("regular", tuple(range(4, 15))), ("playoffs", (15, 16, 17))]
    one = _check(world, ["Mine Receiver Two"], ["Beta Back"])  # the same draws: deterministic
    for s, again in zip(res.check.sides, one.check.sides, strict=True):
        assert [x.kind for x in s.stretches] == ["regular", "playoffs"]
        assert s.main.weeks == tuple(range(4, 15)) and s.playoffs.weeks == (15, 16, 17)
        assert (s.lo, s.hi, s.playoffs.lo) == (again.lo, again.hi, again.playoffs.lo)
        slots = trade.skill_slots(res.data.slots)
        roster = res.data.roster(s.team_id)
        whole = trade.season_lineup(roster, res.check.values, res.sched, slots, 4)
        assert round(s.main.before.total + s.playoffs.before.total, 6) == whole.total
        v = trade.verdict(s, res.check)
        assert "(weeks 4-14, the regular season): about " in v and " will " not in v
        assert ("The fantasy playoff weeks 15-17 (only if you make the playoffs) add about "
                in v) and v.count("% of 4,000 simulated seasons") == 2  # fmt: skip
    lines = trade.text(res.check, res.data, res.sched, None)
    out = "\n".join(lines)
    assert "over weeks 4-14 (the regular season) and, apart, weeks 15-17 (the fantasy" in out
    assert "* the fantasy playoff weeks: only if you make the playoffs" in lines
    assert "│ 15-17* " in out and "│ 4-14 " in out
    note = "the league's regular season ends in week 14 (4 of 12 teams make them, 1 week(s) per"
    assert f"fantasy playoffs: {note} playoff matchup;" in out and "not modelled" not in out
    body = json.loads(trade.as_json(res.check, res.data, res.sched))
    assert body["reg_season_final_week"] == 14 and body["weeks"] == list(range(4, 18))
    assert [x["kind"] for x in body["sides"][0]["stretches"]] == ["regular", "playoffs"]


def test_split_weeks_without_the_settings_or_past_the_regular_season():
    assert trade.split_weeks((4, 5), None) == [("season", (4, 5))]
    assert trade.split_weeks((4, 5), 14) == [("regular", (4, 5))]
    assert trade.split_weeks((15, 16, 17), 14) == [("playoffs", (15, 16, 17))]
    assert trade.span((15,)) == "week 15" and trade.span(()) == "no week left"


@pytest.mark.parametrize(("share", "words", "pct"), [
    (0.90, "probably helps", 90), (0.6667, "probably helps", 67), (0.66, "a close call", 66),
    (0.50, "a close call", 50), (0.34, "a close call", 34), (0.333, "probably hurts", 33),
    (0.05, "probably hurts", 5),
])  # fmt: skip
def test_the_verdict_word_follows_the_printed_percent_of_simulated_seasons(share, words, pct):
    lineup = trade.SeasonLineup(100.0, {}, [])
    x = trade.Stretch("regular", (4, 5), lineup, trade.SeasonLineup(103.0, {}, []), -8.0, 12.0,
                      share)  # fmt: skip
    side = trade.Side(1, "Fake Team Alpha", True, [], [], [], [], 0, [x])
    check = trade.TradeCheck(SEASON, 3, (4, 5), None, [side], {}, {}, None, None, {})
    v = trade.verdict(side, check)
    assert v == (f"You (Fake Team Alpha): {words} (weeks 4-5, the regular season): about +3 "
                 f"points (median), range -8 to +12 (80% interval); gains in {pct}% of 4,000 "
                 "simulated seasons.")  # fmt: skip


def test_sync_stores_the_playoff_settings_and_settings_diff_ignores_them(isolated, monkeypatch):  # noqa: F811
    pytest.importorskip("espn_api")
    from types import SimpleNamespace

    from tests.league_fixtures import FakeLeague
    from tests.test_league import _diff_after_sync, _rows
    from twm.league.espn_client import _reg_final_week

    code, out = _diff_after_sync(monkeypatch, isolated, FakeLeague())
    assert code == 0 and "No differences: the config matches the league." in out
    assert "reg_season" not in out and "playoff" not in out
    kv = dict(_rows(isolated[1][0], "SELECT key, value FROM league_settings"))
    # the fixture's ESPN payload has no playoffMatchupPeriodLength: espn-api's default 0
    want = {"reg_season_count": "14", "reg_season_final_week": "14", "playoff_team_count": "6",
            "playoff_matchup_period_length": "0"}  # fmt: skip
    assert {k: kv.get(k) for k in want} == want
    # the regular season's last week: matchup_periods' scoring periods when ESPN lists them
    assert _reg_final_week(SimpleNamespace(reg_season_count=13,
                                           matchup_periods={"13": [13, 14]})) == 14  # fmt: skip
    assert _reg_final_week(SimpleNamespace(reg_season_count=14, matchup_periods={})) == 14
    assert _reg_final_week(SimpleNamespace()) is None


def test_the_point_is_the_simulations_median_so_a_lean_in_the_misses_moves_it(world):
    import numpy as np

    res = _check(world, ["Mine Receiver Two"], ["Beta Back"])  # a WR out, an RB in
    lean = {"QB": 0.0, "RB": 0.0, "WR": -2.0, "TE": 0.0}  # WRs ran 2 points per game high
    pool = trade.ResidualPool({p: np.array([m]) for p, m in lean.items()}, {}, "2011-2025")
    trade.simulate(res.check, pool)
    for s in res.check.sides:
        for x in s.stretches:
            moved = {*x.before.starts, *x.after.starts}
            wr = sum(x.after.starts.get(k, 0) - x.before.starts.get(k, 0) for k in moved
                     if res.check.players[k].position == "WR")  # fmt: skip
            assert wr != 0 and x.lo == x.mid == x.hi  # one miss per position: no spread
            assert x.delta == pytest.approx(x.projection_delta - 2.0 * wr)
    body = json.loads(trade.as_json(res.check, res.data, res.sched))
    me = body["sides"][0]
    assert me["delta"] == pytest.approx(me["projection_delta"] + 2.0 * 14)  # 14 WR starts out
