"""My League step F4: the scoring check against ESPN's box scores, the week-mapping check, the
optimizer-based drop candidates and the weekly HTML report (`twm league report`). Synthetic data
only (tests/league_fixtures.py and hand-made rows: fake names, fake cookies); every network path
is blocked by test_league's autouse fixture."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import duckdb
import pytest

from tests.league_fixtures import BASE_ITEMS, LEAGUE_ID
from tests.test_league import (  # noqa: F401 - fixtures (isolated is autouse)
    GOOD_ENV,
    SECRETS,
    _run,
    cli_sync,
    fake_league,
    isolated,
)
from tests.test_league_f3 import (  # noqa: F401 - fixtures
    ROSTERS,
    _box,
    _dim_week,
    _league_db,
    _p,
    _write,
    f3_sync,
    world,
)
from twm.league import scoring_check as sc
from twm.league import store

# --------------------------------------------------------------------------------------
# the scoring check (twm.league.scoring_check)
# --------------------------------------------------------------------------------------

SYNCED = datetime(2026, 9, 16, 9)  # after week 1's last game (tests/test_league_f3.ENDS)
PLAYER_COLS = ("receptions", "receiving_yards", "receiving_tds", "rushing_yards",
               "fumbles_lost_total", "sack_fumbles_lost", "rushing_fumbles_lost",
               "receiving_fumbles_lost", "special_teams_tds")  # fmt: skip
PLAYER_WEEKS = {  # gsis -> stats of week 1 (config: full PPR, every lost fumble counts)
    "G1": (5, 60, 1, 0, 0, 0, 0, 0, 0),  # 5 + 6 + 6 = 17
    "G2": (2, 20, 0, 0, 1, 0, 0, 0, 0),  # 2 + 2 - 2 = 2 (a fumble lost on a kick return)
    "G3": (0, 0, 0, 10, 0, 0, 0, 0, 1),  # 1 + 6 = 7 (a punt return touchdown)
    "G4": (0, 0, 0, 50, 0, 0, 0, 0, 0),  # 5
}
BOX = [  # (espn id, name, position, slot, ESPN points, gsis)
    (1, "Catch Guy", "WR", "WR", 17.0, "G1"), (2, "Return Man", "WR", "WR", 4.0, "G2"),
    (3, "Return Td", "RB", "RB", 1.0, "G3"), (4, "Mystery Back", "RB", "RB", 8.3, "G4"),
    (5, "Kick Guy", "K", "K", 6.0, "G5"), (-16012, "Chiefs D/ST", "D/ST", "D/ST", 4.0, None),
    (6, "No Stats", "WR", "BE", 9.0, "G6"), (7, "Bye Guy", "WR", "BE", 0.0, "G7"),
    (8, "Unlinked", "TE", "TE", 3.0, None),
]  # fmt: skip


def _stat_warehouse(path: Path) -> Path:
    _dim_week(path)
    con = duckdb.connect(str(path))
    cols = ", ".join(f"{c} DOUBLE" for c in PLAYER_COLS)
    con.execute(f"CREATE TABLE fact_player_week (player_id VARCHAR, season INTEGER, week INTEGER, "
                f"season_type VARCHAR, {cols})")  # fmt: skip
    con.executemany(f"INSERT INTO fact_player_week VALUES (?, 2026, 1, 'REG', "
                    f"{', '.join('?' * len(PLAYER_COLS))})",
                    [(g, *v) for g, v in PLAYER_WEEKS.items()])  # fmt: skip
    con.execute(
        "CREATE TABLE fact_kicker_week (player_id VARCHAR, season INTEGER, week INTEGER, "
        "season_type VARCHAR, fg_made_40_49 DOUBLE, pat_made DOUBLE, fg_blocked DOUBLE)"
    )
    con.execute("INSERT INTO fact_kicker_week VALUES ('G5', 2026, 1, 'REG', 1, 2, 1)")  # 4+2-1
    con.execute("CREATE TABLE fact_defense_week (team VARCHAR, season INTEGER, week INTEGER, "
                "season_type VARCHAR, def_sacks DOUBLE, points_allowed DOUBLE, "
                "points_scored_against DOUBLE, yards_allowed DOUBLE)")  # fmt: skip
    con.execute("INSERT INTO fact_defense_week VALUES ('KC', 2026, 1, 'REG', 3, 10, 17, 300)")
    con.close()  # D/ST: 3 sacks + 10 allowed (3) = 6; ESPN counted 17 allowed (1)
    return path


def _box_rows(rows=BOX) -> list[dict]:
    out = []
    for eid, name, pos, slot, pts, gsis in rows:
        p = _p(eid, name, pos, slot=slot, gsis=gsis)
        out.append({**p, "is_starter": slot != "BE", "points": pts, "projected_points": 5.0,
                    "on_bye": False})  # fmt: skip
    return out


def _check(tmp_path: Path, *, items=None) -> sc.ScoringCheck:
    db = _league_db(tmp_path / "l.duckdb", boxes={1: (SYNCED, _box_rows())})
    if items is not None:
        con = store.connect(db)
        rows = [{"stat_id": s, "abbr": "", "label": "", "points": p, "dst_points": d}
                for s, p, d in items]  # fmt: skip
        _write(con, "league_scoring", rows, 4)
        con.close()
    con = store.connect(db, read_only=True)
    try:
        return sc.check(con, _stat_warehouse(tmp_path / "wh.duckdb"))
    finally:
        con.close()


def _by_name(res: sc.ScoringCheck) -> dict[str, sc.PlayerWeek]:
    return {r.name: r for r in res.rows}


def _questions(r: sc.PlayerWeek) -> list[str]:
    return [e.question for e in r.explained_by or ()]


def test_scoring_check_lists_each_difference_with_the_stat_that_explains_it(tmp_path):
    res = _check(tmp_path)
    assert res.weeks == [1] and not res.league_scoring
    by = _by_name(res)
    assert not by["Catch Guy"].differs and by["Catch Guy"].ours == 17
    assert by["Bye Guy"].ours == 0 and not by["Bye Guy"].differs  # no game, no points
    assert res.no_stats == [(1, "No Stats")] and res.unlinked == [(1, "Unlinked")]
    assert [r.name for r in res.differing] == [
        "Chiefs D/ST",
        "Return Man",
        "Return Td",
        "Mystery Back",
        "Kick Guy",
    ]  # by week, ESPN id
    assert _questions(by["Return Man"]) == ["fumbles_scope"] and by["Return Man"].diff == 2
    assert "did not charge it (like `scrimmage`)" in by["Return Man"].explained_by[0].text
    assert _questions(by["Return Td"]) == ["return_tds"] and by["Return Td"].diff == -6
    assert by["Mystery Back"].explained_by is None  # 8.3 vs 5: nothing we hold explains it
    assert _questions(by["Kick Guy"]) == ["fg_blocked"]
    assert _questions(by["Chiefs D/ST"]) == ["dst_points_allowed"]
    assert "(17, not 10)" in by["Chiefs D/ST"].explained_by[0].text


def test_scoring_check_uses_the_leagues_own_settings_first(tmp_path):
    half = [(s, 0.5 if s == 53 else p, d) for s, p, d in BASE_ITEMS]  # half PPR
    rows = [r for r in BOX if r[0] not in (1, 2)] + [
        (1, "Catch Guy", "WR", "WR", 14.5, "G1"),
        (2, "Return Man", "WR", "WR", 3.0, "G2"),
    ]
    db = _league_db(tmp_path / "l.duckdb", boxes={1: (SYNCED, _box_rows(rows))})
    con = store.connect(db)
    _write(con, "league_scoring", [{"stat_id": s, "abbr": "", "label": "", "points": p,
                                    "dst_points": d} for s, p, d in half], 4)  # fmt: skip
    con.close()
    con = store.connect(db, read_only=True)
    try:
        res = sc.check(con, _stat_warehouse(tmp_path / "wh.duckdb"))
    finally:
        con.close()
    catch = _by_name(res)["Catch Guy"]
    assert res.league_scoring and catch.ours == 17 and catch.league == 14.5 and catch.diff == -2.5
    assert _questions(catch) == ["league_settings"]
    assert "receiving receptions -2.50" in catch.explained_by[0].text
    # Return Man (2 catches): the league's half PPR (-1) and the return fumble (+2) together: 3
    assert _questions(_by_name(res)["Return Man"]) == ["league_settings", "fumbles_scope"]


def test_scoring_evidence_settles_or_holds_each_question(tmp_path):
    ev = dict(sc.evidence(_check(tmp_path)))
    q = sc.QUESTIONS
    assert ev[q["fumbles_scope"]].startswith("1 player-week(s): ESPN scored 0 the way the config")
    assert "the other way: consider changing config/scoring.yaml" in ev[q["fumbles_scope"]]
    assert ev[q["fumble_recovery_td"]].startswith("no evidence yet")
    assert "1 D/ST week(s) compared, 1 of them explained" in ev[q["dst_yards_allowed"]]
    assert "not synced" in ev[q["dst_yards_allowed"]]


def test_scoring_check_needs_box_scores_and_a_warehouse(tmp_path):
    empty = _league_db(tmp_path / "a.duckdb")
    con = store.connect(empty, read_only=True)
    try:
        with pytest.raises(sc.ScoringCheckUnavailableError, match="no box scores"):
            sc.check(con, tmp_path / "wh.duckdb")
    finally:
        con.close()
    db = _league_db(tmp_path / "b.duckdb", boxes={1: (SYNCED, _box_rows())})
    con = store.connect(db, read_only=True)
    try:
        with pytest.raises(sc.ScoringCheckUnavailableError, match="no warehouse"):
            sc.check(con, tmp_path / "missing.duckdb")
    finally:
        con.close()


# --------------------------------------------------------------------------------------
# `twm league report` on the FakeLeague fixtures
# --------------------------------------------------------------------------------------

SECTION_IDS = ("radar", "streamer", "drops", "tags", "regret", "scoring", "settings")


def _report(f3_sync) -> tuple[int, str, Path]:  # noqa: F811
    db, rep, _ = f3_sync
    code, out = _run("report", raw=True)
    return code, out, rep.parent / "2026-W03.html"


def test_report_after_a_fake_league_sync(f3_sync):  # noqa: F811
    assert _run("sync")[0] == 0
    code, out, path = _report(f3_sync)
    assert code == 0, out
    assert "wrote" in out and "2026-W03.html" in out and path.exists()
    html = path.read_text()
    for sid in SECTION_IDS:
        assert f'data-section="{sid}"' in html, sid
    assert "<script" not in html and "http://" not in html and "https://" not in html
    assert "prefers-color-scheme:dark" in html
    assert "Fake Team Alpha" in html  # the owner's own team, a local file
    assert "Fake Waiver Back" in html and "52% (similar players hit 45-55%)" in html
    assert "League data synced" in html and "data as of 2026-09-29 14:00 UTC" in html
    private = (*SECRETS, "fake_owner", str(LEAGUE_ID))
    assert not any(s in html or s in out for s in private)
    assert "Fake Team" not in out  # the terminal line stays name-free


def test_report_is_deterministic_except_the_generated_line(f3_sync, monkeypatch):  # noqa: F811
    from twm.league import commands

    assert _run("sync")[0] == 0
    texts = []
    for now in (datetime(2026, 9, 30, 12), datetime(2026, 10, 1, 8, 30)):
        assert commands.run_report(None, echo=lambda _: None, now=now) == 0
        texts.append((f3_sync[1].parent / "2026-W03.html").read_text().splitlines())
    a, b = texts
    changed = [(x, y) for x, y in zip(a, b, strict=True) if x != y]
    assert len(changed) == 1 and all("data-generated" in x for x in changed[0])
    assert "Generated 2026-09-30 12:00 UTC." in changed[0][0]


def test_report_exit_codes(isolated, f3_sync):  # noqa: F811
    env_file = isolated[0]
    env_file.write_text("")
    code, out = _run("report")
    assert code == 2 and "My League is off" in out and len(out.strip().splitlines()) == 1
    env_file.write_text(GOOD_ENV)
    code, out = _run("report")
    assert code == 2 and out.strip() == (
        "My League: nothing synced yet: run `uv run twm league sync` first."
    )
    assert _run("sync")[0] == 0
    code, out = _run("report", "--week", "7")
    assert code == 2 and "no Radar list of the approved model is stored for 2026 week 7" in out
    assert len(out.strip().splitlines()) == 1


# --------------------------------------------------------------------------------------
# the drop candidates (twm.league.drops): the optimizer rule of step F4
# --------------------------------------------------------------------------------------


def _radar(world, warehouse: Path | None = None, rosters=None, tmp: Path | None = None):  # noqa: F811
    from twm.league import personal

    db, preds, pins = world
    if rosters is not None:
        from tests.test_league_f3 import FREE

        db = _league_db(tmp / "r.duckdb", rosters=rosters, free_agents=FREE)
    con = store.connect(db, read_only=True)
    try:
        return personal.build(con, preds, pins_path=pins, warehouse=warehouse)
    finally:
        con.close()


def test_drops_keep_the_best_lineup_and_offer_the_lowest_bench_player(world):  # noqa: F811
    v = _radar(world).drops
    kept = {s: r.name for s, r in v.lineup if r is not None}
    assert kept == {"QB": "Mine QB One", "RB": "Mine RB B", "WR": "Mine WR B", "TE": "Mine TE",
                    "RB/WR/TE": "Mine RB C", "K": "Mine K A", "D/ST": "Chiefs D/ST"}  # fmt: skip
    assert [r.name for s, r in v.lineup if s == "RB"] == ["Mine RB A", "Mine RB B"]
    assert [r.name for r in v.bench] == ["Mine QB Two", "Mine K B", "Mine WR Unmatched"]
    c = v.candidate
    assert (c.name, c.value, c.source) == ("Mine QB Two", 14.0, "projection")
    assert v.weeks_left == 14 and v.points_left(c) == 196  # 14 PPG x 14 weeks
    assert [(r.name, r.value, r.status) for r in v.spares] == [("Mine K B", 0.22, "QUESTIONABLE")]
    assert v.on_ir == 1 and all(r.name != "Mine RB Hurt" for r in v.bench)  # IR: never
    from twm.league.drops import describe, drop_text

    assert describe(v, c).startswith("Mine QB Two (QB): projects 14.00 points per game")
    assert "about 196 points over the 14 weeks left (rest-of-season projection)" in describe(v, c)
    text = "\n".join(drop_text(v))
    assert (
        "candidate: Mine QB Two" in text
        and "spare K: Mine K B (K) [ESPN status: QUESTIONABLE]" in text
    )
    assert "no number (not in the week's lists, no games this season): Mine WR Unmatched" in text


def test_a_player_outside_regression_watch_gets_his_season_average(world, tmp_path):  # noqa: F811
    wh = tmp_path / "wh.duckdb"
    con = duckdb.connect(str(wh))
    con.execute("CREATE TABLE fact_player_week AS SELECT * FROM (VALUES "
                "('G305', 2026, 1, 'REG', 3.0, 30.0), ('G305', 2026, 2, 'REG', 3.0, 30.0), "
                "('G305', 2026, 3, 'REG', 3.0, 30.0), ('G305', 2026, 4, 'REG', 9.0, 200.0)) "
                "t(player_id, season, week, season_type, receptions, receiving_yards)")  # fmt: skip
    cols = ["passing_yards", "passing_tds", "passing_interceptions", "passing_2pt_conversions",
            "rushing_yards", "rushing_tds", "rushing_2pt_conversions", "receiving_tds",
            "receiving_2pt_conversions", "fumbles_lost_total", "special_teams_tds",
            "fumble_recovery_tds"]  # fmt: skip
    for c in cols:
        con.execute(f"ALTER TABLE fact_player_week ADD COLUMN {c} DOUBLE DEFAULT 0")
    con.close()
    rosters = [*ROSTERS, _p(305, "Mine WR Avg", "WR", gsis="G305")]
    v = _radar(world, wh, rosters, tmp_path).drops
    c = v.candidate
    assert (c.name, c.value, c.source) == ("Mine WR Avg", 6.0, "season_average")  # weeks 1-3
    assert "6.00 points per game over 3 game(s) so far" in c.numbers
    assert v.points_left(c) == 84
    from twm.league.drops import describe

    assert describe(v, c).endswith("(season average, not a projection)")


# --------------------------------------------------------------------------------------
# the week-mapping check (twm.league.weeks)
# --------------------------------------------------------------------------------------


def _windows(path: Path) -> Path:
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE dim_week (season INTEGER, week INTEGER, season_type VARCHAR, "
                "window_start_utc TIMESTAMP, window_end_utc TIMESTAMP)")  # fmt: skip
    con.executemany("INSERT INTO dim_week VALUES (2026, ?, 'REG', ?, ?)", [
        (1, None, datetime(2026, 9, 15, 14)),
        (2, datetime(2026, 9, 15, 14), datetime(2026, 9, 22, 14)),
        (3, datetime(2026, 9, 22, 14), datetime(2026, 9, 29, 14)),
        (4, datetime(2026, 9, 29, 14), datetime(2026, 10, 6, 14))])  # fmt: skip
    con.close()
    return path


@pytest.mark.parametrize(("current", "warning"), [
    (4, ""),
    (3, "ESPN's current week is 3, but the warehouse puts the sync time (2026-09-30 18:00 UTC) "
        "in week 4"),
])  # fmt: skip
def test_week_check_compares_espns_week_with_the_warehouses(tmp_path, current, warning):
    from twm.league.weeks import week_check

    db = _league_db(tmp_path / "l.duckdb", current=current)  # settings synced 2026-09-30 18:00
    con = store.connect(db, read_only=True)
    try:
        got = week_check(con, _windows(tmp_path / "wh.duckdb"))
        bare = week_check(con, tmp_path / "missing.duckdb")
    finally:
        con.close()
    assert (got.espn_week, got.warehouse_week) == (current, 4)
    assert got.warning.startswith(warning) if warning else got.warning == ""
    assert bare.warehouse_week is None and "could not be checked" in bare.warning


def test_week_check_before_any_sync(tmp_path):
    from twm.league.weeks import week_check

    con = store.connect(tmp_path / "empty.duckdb")
    try:
        assert week_check(con, None) is None
    finally:
        con.close()


# --------------------------------------------------------------------------------------
# the report's sections on a fuller hand-made league (tags, regret, the scoring check)
# --------------------------------------------------------------------------------------


def test_report_sections_carry_the_numbers(tmp_path):
    from tests.test_league_f3 import FREE, LISTS, _pins, _predictions
    from twm.league import report

    tagged = {"G101": "sell_high", "G302": "buy_low", "G401": "legit"}  # Legit: never shown
    lists = [(k, pos, e, rk, sc_, tagged.get(e) if k == "regression_watch" else b, w, t)
             for k, pos, e, rk, sc_, b, w, t in LISTS]  # fmt: skip
    boxes = {1: (SYNCED, _box_rows())}
    db = _league_db(tmp_path / "l.duckdb", rosters=ROSTERS, free_agents=FREE, boxes=boxes)
    preds = _predictions(tmp_path / "p.duckdb", lists)
    wh = _stat_warehouse(tmp_path / "wh.duckdb")
    con = store.connect(db, read_only=True)
    try:
        d = report.gather(con, preds, wh, pins_path=_pins(tmp_path / "pins.yaml"))
    finally:
        con.close()
    assert [(t.name, t.tag) for t in d.tags] == [("Mine QB One", "sell_high"),
                                                 ("Mine WR B", "buy_low")]  # fmt: skip
    html = report.render(d, datetime(2026, 9, 30, 20))
    assert "<td>Mine QB One</td><td>QB</td><td>Sell-high</td>" in html and "Legit" not in html
    assert "Drop candidate:</strong> Mine QB Two (QB)" in html
    assert "Season so far (1 week(s))" in html  # the week-1 box scores (lineup regret)
    assert "Mystery Back" in html and "not derivable from the stats we hold" in html
    assert "fumble(s) lost on a kick or punt return: ESPN did not charge it" in html
    assert "No differences: the config matches your league." not in html  # lineup.IR differs
    assert html.count("<section ") == 7 and "Fake Team Alpha: your league this week" in html
