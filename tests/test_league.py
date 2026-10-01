"""My League (ESPN) data layer, step F2: the guard, the isolated client, data/league.duckdb, the
ESPN -> gsis join, settings-diff, redaction and the publish isolation. Synthetic fixtures only
(tests/league_fixtures.py: fake names, fake cookies); every network path is blocked."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from tests.conftest import plain
from tests.league_fixtures import FAKE_S2, FAKE_SWID, LEAGUE_ID, OTHER_MEMBER
from twm.cli import app
from twm.config import ROOT
from twm.league import commands, env, store

ENV_KEYS = ("ENABLE_MY_LEAGUE", "ESPN_LEAGUE_ID", "ESPN_YEAR", "ESPN_S2", "ESPN_SWID")
GOOD_ENV = (f"ENABLE_MY_LEAGUE=true\nESPN_LEAGUE_ID={LEAGUE_ID}\nESPN_YEAR=2026\n"
            f"ESPN_S2={FAKE_S2}\nESPN_SWID={FAKE_SWID}\n")  # fmt: skip
runner = CliRunner()


def _no_network(*_a, **_k):
    raise AssertionError("a test tried to reach the network")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """No real .env, no real data/league.duckdb or reports/, no ESPN, no network."""
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    env_file = tmp_path / ".env"
    monkeypatch.setattr(commands, "default_env_file", lambda: env_file)
    paths = (tmp_path / "league.duckdb", tmp_path / "wh.duckdb", tmp_path / "rep" / "u.md")
    monkeypatch.setattr(commands, "paths", lambda: paths)
    monkeypatch.setattr(commands, "connect", lambda e: _no_network())
    try:
        import requests

        monkeypatch.setattr(requests, "get", _no_network)
        monkeypatch.setattr(requests.Session, "request", _no_network)
    except ImportError:
        pass
    return env_file, paths


def _run(*args: str, raw: bool = False):
    res = runner.invoke(app, ["league", *args])
    return res.exit_code, res.output if raw else plain(res.output)


@pytest.mark.parametrize("cmd", ["sync", "settings-diff"])
@pytest.mark.parametrize(
    ("dotenv", "expected"),
    [
        (None, "My League is off"),
        ("ENABLE_MY_LEAGUE=false\nESPN_LEAGUE_ID=1\nESPN_YEAR=2026\n", "My League is off"),
        ("ENABLE_MY_LEAGUE=true\nESPN_YEAR=2026\n", "ESPN_LEAGUE_ID is not set"),
        ("ENABLE_MY_LEAGUE=yes\nESPN_LEAGUE_ID=1\n", "ESPN_YEAR is not set"),
        (f"ENABLE_MY_LEAGUE=1\nESPN_LEAGUE_ID=abc{FAKE_S2}\nESPN_YEAR=2026\n", "whole number"),
        (f"ENABLE_MY_LEAGUE=on\nESPN_LEAGUE_ID=1\nESPN_YEAR=2026\nESPN_S2={FAKE_S2}\n",
         "only ESPN_S2 is set"),
    ],
)  # fmt: skip
def test_guard_prints_one_line_and_exits_2(isolated, cmd, dotenv, expected):
    if dotenv is not None:
        isolated[0].write_text(dotenv)
    code, out = _run(cmd, raw=True)
    assert code == env.EXIT_UNAVAILABLE == 2
    assert expected in plain(out) and len(out.strip().splitlines()) == 1
    assert FAKE_S2 not in out and "abc" not in out  # key names only, never values
    assert not isolated[1][0].exists()  # nothing was written


def test_resolve_prefers_the_environment_and_hides_cookies(tmp_path):
    f = tmp_path / ".env"
    f.write_text(GOOD_ENV)
    e = env.resolve({"ESPN_YEAR": "2025"}, f)
    assert (e.league_id, e.year, e.private) == (LEAGUE_ID, 2025, True)
    assert FAKE_S2 not in repr(e) and FAKE_SWID not in repr(e) and str(LEAGUE_ID) not in repr(e)
    public = env.resolve({"ENABLE_MY_LEAGUE": "true", "ESPN_LEAGUE_ID": "7", "ESPN_YEAR": "2026"})
    assert not public.private and public.secrets() == ()


def test_redact_scrubs_raw_quoted_and_braceless_cookies():
    from urllib.parse import quote

    text = f"a {FAKE_S2} b {quote(FAKE_SWID, safe='')} c {FAKE_SWID.strip('{}')} d"
    out = env.redact(text, FAKE_S2, FAKE_SWID)
    assert FAKE_S2 not in out and FAKE_SWID.strip("{}") not in out and out.count("***") == 3


# --------------------------------------------------------------------------------------
# The isolated client over real espn-api objects (built from synthetic payloads)
# --------------------------------------------------------------------------------------


@pytest.fixture
def fake_league():
    pytest.importorskip("espn_api")
    from tests.league_fixtures import FakeLeague

    return FakeLeague()


def _client(league, swid: str = FAKE_SWID):
    from twm.league.espn_client import EspnClient

    return EspnClient(league, secrets=(FAKE_S2, swid), swid=swid)


def test_client_reads_settings_with_the_dst_override_apart(fake_league):
    s = _client(fake_league).settings()
    assert (s.team_count, s.current_week, s.final_week, s.faab) == (12, 3, 18, False)
    assert s.roster_slots == {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "D/ST": 1, "K": 1, "BE": 7,
                              "IR": 1, "RB/WR/TE": 1}  # fmt: skip
    by_id = {i.stat_id: i for i in s.scoring}
    assert (by_id[101].points, by_id[101].dst_points, by_id[101].abbr) == (6.0, 6.0, "KRTD")
    assert (by_id[53].points, by_id[53].dst_points) == (1.0, None)
    assert by_id[53].label == "Each reception"


def test_client_reads_teams_rosters_free_agents_box_scores_and_activity(fake_league):
    c = _client(fake_league)
    assert [(t.team_id, t.name, t.is_mine) for t in c.teams()] == [
        (1, "Fake Team Alpha", True), (2, "Fake Team Beta", False)]  # fmt: skip
    assert [t.is_mine for t in _client(fake_league, swid=OTHER_MEMBER.lower()).teams()] == [
        False, True]  # member ids compare without case  # fmt: skip
    spots = {r.player.espn_id: r for r in c.rosters()}
    assert len(spots) == 7 and spots[-16012].player.position == "D/ST"
    assert (spots[9003].player.lineup_slot, spots[9003].player.pro_team) == ("RB/WR/TE", "WSH")
    c.rosters(week=2)
    assert ("load_roster_week", 2) in fake_league.calls
    fa = c.free_agents(week=3, size=5, position="RB")
    assert [(p.espn_id, p.lineup_slot, p.projected_points) for p in fa][0] == (9101, "", 9.0)
    boxes = c.box_scores(3)
    home = next(b for b in boxes if b.is_home)
    assert (home.team_id, home.opponent_id, home.score, home.projected) == (1, 2, 101.0, 50.0)
    slots = {p.espn_id: (p.lineup_slot, p.points) for p in home.lineup}
    assert slots[9004] == ("BE", 30.0) and slots[9001] == ("QB", 21.5)
    acts = c.activity()
    assert [(a.action, a.team_id, a.espn_id) for a in acts] == [
        ("WAIVER ADDED", 1, 9003), ("DROPPED", 1, 9999), ("TRADE_SENT", 2, 9005),
        ("TRADE_RECEIVED", 1, 9005)]  # fmt: skip
    raw = c.raw_settings()
    assert acts[0].bid_amount == 7.0 and raw.waiver["waiverProcessDays"] == "WEDNESDAY"
    assert raw.slots == c.settings().roster_slots  # ESPN's ids agree with espn-api's labels


def test_client_refuses_a_future_week_and_an_unknown_position(fake_league):
    from twm.league.espn_client import EspnError

    c = _client(fake_league)
    with pytest.raises(EspnError, match="week 4 has not started"):
        c.box_scores(4)  # espn-api would quietly return week 3 (league.py:308)
    with pytest.raises(EspnError, match="position 'DL'"):
        c.free_agents(position="DL")


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        ("ESPNAccessDenied", "private league needs valid ESPN_S2 and ESPN_SWID"),
        ("ESPNInvalidLeague", "no league with this ESPN_LEAGUE_ID"),
        ("KeyError", "did not have the shape espn-api expects"),
        ("ConnectionError", "could not reach ESPN"),
    ],
)
def test_client_errors_are_one_redacted_line(fake_league, exc, expected):
    from espn_api.requests import espn_requests as er
    from requests.exceptions import ConnectionError as ReqConnectionError

    from twm.league.espn_client import EspnError

    known = {"KeyError": KeyError, "ConnectionError": ReqConnectionError}
    cls = known.get(exc) or getattr(er, exc)

    def boom(**_):
        raise cls(f"cookies {FAKE_S2} {FAKE_SWID}")

    fake_league.recent_activity = boom
    with pytest.raises(EspnError) as info:
        _client(fake_league).activity()
    msg = str(info.value)
    assert expected in msg and "recent activity" in msg
    assert FAKE_S2 not in msg and FAKE_SWID.strip("{}") not in msg
    assert info.value.__cause__ is None and info.value.__suppress_context__


def test_connect_failure_is_redacted_and_the_only_network_entry(monkeypatch):
    pytest.importorskip("espn_api")
    import espn_api.football as football
    from espn_api.requests.espn_requests import ESPNAccessDenied

    from twm.league import espn_client

    def refuse(**kw):
        assert kw == {"league_id": LEAGUE_ID, "year": 2026, "espn_s2": FAKE_S2, "swid": FAKE_SWID}
        raise ESPNAccessDenied(f"League {LEAGUE_ID} cannot be accessed with {FAKE_S2}")

    monkeypatch.setattr(football, "League", refuse)
    e = env.LeagueEnv(LEAGUE_ID, 2026, FAKE_S2, FAKE_SWID)
    with pytest.raises(espn_client.EspnError) as info:
        espn_client.connect(e)
    assert "ESPN refused loading the league" in str(info.value) and FAKE_S2 not in str(info.value)
    importers = sorted(p.name for p in (ROOT / "src" / "twm").rglob("*.py")
                       if any(n.split(".")[0] == "espn_api" for n in _imports(p)))  # fmt: skip
    assert importers == ["espn_client.py"]  # the only module that touches the library


def _imports(path: Path) -> list[str]:
    names: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    return names


# --------------------------------------------------------------------------------------
# `twm league sync` round trip into a temporary league.duckdb, the join, redaction
# --------------------------------------------------------------------------------------

BRIDGE = {
    9001: "00-0090001",
    9002: "00-0090002",
    9003: "00-0090003",
    9005: "00-0090005",
    9006: "00-0090006",
    9101: "00-0090101",
}  # fmt: skip (9004, 9102, 9999: no link)
SECRETS = (FAKE_S2, FAKE_SWID, FAKE_SWID.strip("{}"), OTHER_MEMBER.strip("{}"))


def _warehouse(path: Path) -> None:
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE bridge_player_id (id_type VARCHAR, source_id VARCHAR, "
                "gsis_id VARCHAR)")  # fmt: skip
    con.executemany("INSERT INTO bridge_player_id VALUES ('espn', ?, ?)",
                    [(str(k), v) for k, v in BRIDGE.items()])  # fmt: skip
    con.execute("INSERT INTO bridge_player_id VALUES ('pfr', '9004', '00-0099999')")
    con.close()


@pytest.fixture
def cli_sync(isolated, fake_league, monkeypatch):
    env_file, (db, wh, rep) = isolated
    env_file.write_text(GOOD_ENV)
    _warehouse(wh)
    monkeypatch.setattr(commands, "connect", lambda e: _client(fake_league, swid=e.swid))
    return db, rep, fake_league


def _rows(db: Path, sql: str) -> list[tuple]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_sync_round_trip_counts_only_and_joins(cli_sync):
    db, rep, league = cli_sync
    code, out = _run("sync")
    assert code == 0, out
    assert "synced 2026 week 3" in out and "roster spots 7" in out and "free agents 2" in out
    assert "box-score lines 7" in out and "activity rows 4" in out and "your team: found" in out
    assert "players without a gsis_id: 3" in out
    assert "Fake" not in out and "Chiefs" not in out  # counts only, no names
    got = dict(_rows(db, "SELECT espn_id, entity_id FROM league_rosters"))
    assert got[9001] == "00-0090001" and got[-16012] == "DST-KC" and got[9004] is None
    assert _rows(db, "SELECT gsis_id FROM league_box_scores WHERE espn_id = 9003") == [
        ("00-0090003",)]  # fmt: skip
    assert _rows(db, "SELECT league_team_name FROM league_teams WHERE league_is_mine") == [
        ("Fake Team Alpha",)]  # fmt: skip
    methods = dict(_rows(db, "SELECT method, count(*) FROM league_player_map GROUP BY 1"))
    assert methods == {"bridge": 6, "dst_team": 1, "unmatched": 3}
    text = rep.read_text()
    assert "3 player(s)" in text and "| 9004 | Fake Bencher | TE | LAR |" in text
    assert "| 9999 |" in text and "9001" not in text
    for t in store.TABLES:
        nulls = _rows(db, f"SELECT count(*) FROM {t} WHERE league_id IS NULL OR season IS NULL "
                          "OR week IS NULL OR synced_at IS NULL")  # fmt: skip
        assert nulls == [(0,)], t


def test_sync_is_idempotent_and_keeps_weeks_apart(cli_sync):
    db, _, league = cli_sync
    assert _run("sync")[0] == 0
    first = {t: n for t, n in _rows(db, _count_sql()) if t != "league_sync_runs"}
    assert _run("sync")[0] == 0
    again = {t: n for t, n in _rows(db, _count_sql()) if t != "league_sync_runs"}
    assert again == first  # snapshots replaced, keyed rows upserted
    assert _run("sync", "--week", "2")[0] == 0
    assert ("load_roster_week", 2) in league.calls and ("box_scores", 2) in league.calls
    weeks = dict(_rows(db, "SELECT week, count(*) FROM league_rosters GROUP BY 1"))
    assert weeks == {2: 7, 3: 7}
    assert _rows(db, "SELECT count(*) FROM league_activity") == [(4,)]
    code, out = _run("sync", "--week", "4")
    assert code == commands.EXIT_FAILED and "outside weeks 1-3" in out


def _count_sql() -> str:
    return " UNION ALL ".join(f"SELECT '{t}', count(*) FROM {t}" for t in store.TABLES)


def test_no_cookie_or_member_id_in_output_db_or_report(cli_sync):
    db, rep, _ = cli_sync
    code, out = _run("sync")
    assert code == 0
    diff_code, diff_out = _run("settings-diff")
    assert diff_code == 0
    con = duckdb.connect(str(db), read_only=True)
    try:
        for t in store.TABLES:
            cols = [c for c, typ in store.TABLES[t].columns if typ == "VARCHAR"]
            for c in cols:
                values = [v for (v,) in con.execute(f"SELECT DISTINCT {c} FROM {t}").fetchall()]
                assert not any(s in str(v) for v in values for s in SECRETS), (t, c)
    finally:
        con.close()
    blobs = [out, diff_out, rep.read_text(), db.read_bytes().decode("latin-1")]
    assert not any(s in b for b in blobs for s in SECRETS)


def test_a_failing_espn_call_prints_one_redacted_line_and_writes_nothing(cli_sync, monkeypatch):
    db, _, league = cli_sync

    def boom(*_a, **_k):
        raise RuntimeError(f"upstream said {FAKE_S2} / {FAKE_SWID}")

    monkeypatch.setattr(commands, "connect", boom)
    code, out = _run("sync")
    assert code == commands.EXIT_FAILED and "the sync failed (RuntimeError" in out
    assert not any(s in out for s in SECRETS) and not db.exists()
    monkeypatch.setattr(commands, "connect", lambda e: _client(league, swid=e.swid))
    assert _run("sync")[0] == 0
    before = _rows(db, _count_sql())
    league.box_scores = boom  # every read happens before the one write transaction
    code, out = _run("sync")
    assert code == commands.EXIT_FAILED and "box scores of week 3" in out
    assert not any(s in out for s in SECRETS) and _rows(db, _count_sql()) == before


def test_sync_without_a_warehouse_still_works_and_says_so(cli_sync):
    db, _, _ = cli_sync
    commands.paths()[1].unlink()
    code, out = _run("sync")
    assert code == 0 and "players without a gsis_id: 9" in out and "twm build" in out
    assert _rows(db, "SELECT entity_id FROM league_rosters WHERE espn_id = -16012") == [
        ("DST-KC",)]  # D/ST ids come from the team, not the warehouse  # fmt: skip


def test_player_map_and_espn_team_codes():
    rows = [
        {"espn_id": 1, "player_name": "a", "position": "WR", "pro_team": "WSH"},
        {"espn_id": -16028, "player_name": "b", "position": "D/ST", "pro_team": "WSH"},
        {"espn_id": -16014, "player_name": "c", "position": "D/ST", "pro_team": "LAR"},
        {"espn_id": -16000, "player_name": "d", "position": "D/ST", "pro_team": "None"},
    ]
    got = {m["espn_id"]: (m["entity_id"], m["method"]) for m in store.player_map(rows, {"1": "g"})}
    assert got == {1: ("g", "bridge"), -16028: ("DST-WAS", "dst_team"),
                   -16014: ("DST-LA", "dst_team"), -16000: (None, "unmatched")}  # fmt: skip


# --------------------------------------------------------------------------------------
# `twm league settings-diff` on synthetic settings (one difference per item type)
# --------------------------------------------------------------------------------------


def _diff_after_sync(monkeypatch, isolated, league) -> tuple[int, str]:
    isolated[0].write_text(GOOD_ENV)
    monkeypatch.setattr(commands, "connect", lambda e: _client(league, swid=e.swid))
    assert _run("sync")[0] == 0
    config = {p: p.read_bytes() for p in (ROOT / "config").glob("*.yaml")}
    code, out = _run("settings-diff", raw=True)
    assert {p: p.read_bytes() for p in (ROOT / "config").glob("*.yaml")} == config  # untouched
    return code, out


def test_settings_diff_before_any_sync(isolated):
    isolated[0].write_text(GOOD_ENV)
    code, out = _run("settings-diff")
    assert code == 2 and "nothing synced yet" in out


def test_settings_diff_baseline_matches_the_config(isolated, monkeypatch):
    pytest.importorskip("espn_api")
    from tests.league_fixtures import FakeLeague

    code, out = _diff_after_sync(monkeypatch, isolated, FakeLeague())  # IR 1 like league.yaml
    assert code == 0 and "No differences: the config matches the league." in out
    assert "kicking.fg_blocked: ESPN has no blocked-FG category" in out  # held question shown
    assert "Total Fumbles Lost" in out and "waiver:waiverProcessDays: WEDNESDAY" in out
    assert "suggested" not in out


def test_settings_diff_finds_one_difference_per_item_type(isolated, monkeypatch):
    pytest.importorskip("espn_api")
    from tests.league_fixtures import BASE_ITEMS, FakeLeague, item

    changed = {3: (0.05, None), 4: (6, None), 53: (0.5, None), 201: (5, None), 88: (-1, None),
               99: (1, 2), 101: (6, 3), 89: (10, None), 128: (6, None), 136: (-5, None),
               15: (2, None), 69: (-2, None), 70: (-2, None), 71: (-2, None),
               57: (1, None)}  # fmt: skip
    gone = {72, 63}  # scrimmage-only fumbles; no fumble-recovery TD for players
    items = [item(s, p, d) for s, p, d in BASE_ITEMS if s not in changed and s not in gone]
    items += [item(s, p, d) for s, (p, d) in changed.items()]
    slots = {"7": 1, "20": 6, "10": 1, "21": 2}  # OP (superflex), 6 bench, one IDP LB, IR 2
    code, out = _diff_after_sync(monkeypatch, isolated, FakeLeague(items, size=10, slots=slots))
    assert code == 0
    rows = {line.split()[1]: line.split()[2:4] for line in out.splitlines()
            if line.startswith(("scoring.yaml ", "league.yaml "))}  # fmt: skip
    assert rows == {
        "passing.yards": ["0.05", "0.04"],  # a rate (per yard)
        "passing.touchdowns": ["6", "4"],  # a plain item
        "receiving.receptions": ["0.5", "1"],
        "misc.fumble_recovery_touchdowns": ["0", "6"],  # held question
        "options.fumbles_lost_scope": ["scrimmage", "all"],  # held question
        "kicking.fg_made_60_plus": ["5", "6"],
        "kicking.pat_missed": ["-1", "0"],  # held question
        "defense.sacks": ["2", "1"],  # the D/ST override wins for D/ST items
        "defense.kickoff_return_tds": ["3", "6"],
        "defense.points_allowed_tiers": ["[[0,", "10],"],
        "defense.yards_allowed_tiers": ["[[99,", "6],"],  # first and last tiers differ
        "teams": ["10", "12"],
        "lineup.bench": ["6", "7"],
        "lineup.IR": ["2", "1"],
        "lineup.SUPERFLEX": ["1", "0"],
    }
    assert "misc.special_teams_touchdowns" not in rows  # players keep 6 (the override is D/ST)
    assert "stat 15: the league scores 40+ yard TD pass bonus 2; the app does not score it\n" in out
    # the owner's league's bonuses the app leaves out on purpose (C1) say so
    assert ("stat 57: the league scores 200+ yard receiving game 1; the app does not score it on "
            "purpose (a known, deliberate difference: docs/scoring.md)") in out  # fmt: skip
    assert "ESPN slot(s) 'LB' the app lacks" in out
    patch = out.split("# config/scoring.yaml", 1)[1]
    for line in ("  touchdowns: 6  # config: 4", "  fumbles_lost_scope: scrimmage  # config: all",
                 "  points_allowed_tiers:", "    - [0, 10]", "    - [null, -5]",
                 "  yards_allowed_tiers:", "    - [99, 6]", "    - [null, -5]",
                 "teams: 10  # config: 12",
                 "  SUPERFLEX: 1  # config: 0", "slot_eligibility:",
                 "  SUPERFLEX: [QB, RB, TE, WR]"):  # fmt: skip
        assert line in patch.splitlines(), line


# --------------------------------------------------------------------------------------
# League data is never published (spec rule 9; extends tests/test_publish.py)
# --------------------------------------------------------------------------------------


def test_publish_and_pipeline_never_import_the_league_or_espn_api():
    for pkg in ("publish", "pipeline"):
        for path in sorted((ROOT / "src" / "twm" / pkg).glob("*.py")):
            bad = [n for n in _imports(path) if n.split(".")[0] == "espn_api"
                   or n == "twm.league" or n.startswith("twm.league.")]  # fmt: skip
            assert not bad, (path, bad)
    code = (
        "import sys; import twm.cli, twm.publish.collect, twm.publish.write, twm.pipeline.runner; "
        "from typer.testing import CliRunner; CliRunner().invoke(twm.cli.app, ['--help']); "
        "bad = [m for m in sys.modules if m.startswith(('twm.league', 'espn_api'))]; "
        "print(bad); sys.exit(1 if bad else 0)"
    )
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert res.returncode == 0, res.stdout + res.stderr


def test_league_tables_are_not_publish_tables():
    from twm.publish import tables

    published = set(tables.WRITE_ORDER)
    assert not published & set(store.TABLES)
    assert all(t.startswith("league_") for t in store.TABLES)
    assert all("league_id" in dict(t.columns) for t in store.TABLES.values())


def test_publish_forbids_every_league_only_column():
    """Every league table carries league_id and espn_id-style columns publish already refuses;
    every league-only column (store.PRIVATE_COLUMNS) must be in FORBIDDEN_COLUMNS
    (src/twm/publish/collect.py), so a league column can never reach the public database."""
    from twm.publish.collect import FORBIDDEN_COLUMNS

    assert {"league_id", "espn_id", "espn_s2", "swid"} <= FORBIDDEN_COLUMNS
    missing = sorted(store.PRIVATE_COLUMNS - FORBIDDEN_COLUMNS)
    assert not missing, f"add to publish FORBIDDEN_COLUMNS: {missing}"


def test_doctor_says_whether_my_league_is_configured_and_synced(tmp_path):
    from tests.test_doctor import _ctx, _text
    from twm import doctor as dr

    ctx = _ctx(tmp_path)
    ctx.env_file.write_text(GOOD_ENV)
    text = _text(dr.check_env(ctx))
    assert "My League: configured" in text and "not synced yet" in text
    assert not any(s in text for s in SECRETS) and str(LEAGUE_ID) not in text
    (ctx.root / "data" / "league.duckdb").symlink_to(tmp_path / "missing.duckdb")
    assert "not synced yet" in _text(dr.check_env(ctx))  # the link before the first sync
    store.connect(ctx.root / "data" / "league.duckdb").close()
    assert "synced data in data/league.duckdb" in _text(dr.check_env(ctx))


def test_sync_trusts_espn_slot_ids_over_espn_api_label_order(cli_sync):
    """espn-api labels lineupSlotCounts by position (settings.py:10-11), so counts arriving in
    another key order get the wrong labels; the raw slot ids win and the sync says so."""
    from espn_api.football.settings import Settings

    from tests.league_fixtures import settings_payload

    db, _, league = cli_sync
    payload = settings_payload()
    counts = payload["rosterSettings"]["lineupSlotCounts"]
    payload["rosterSettings"]["lineupSlotCounts"] = {k: counts[k] for k in sorted(counts)}
    league.settings_payload, league.settings = payload, Settings(payload)
    assert league.settings.position_slot_counts.get("RB") != 2  # mislabelled by espn-api
    code, out = _run("sync")
    assert code == 0 and "the ids won" in out
    slots = dict(_rows(db, "SELECT key, value FROM league_settings WHERE key LIKE 'slot:%'"))
    assert slots == {"slot:QB": "1", "slot:RB": "2", "slot:WR": "2", "slot:TE": "1",
                     "slot:D/ST": "1", "slot:K": "1", "slot:BE": "7", "slot:IR": "1",
                     "slot:RB/WR/TE": "1"}  # fmt: skip
