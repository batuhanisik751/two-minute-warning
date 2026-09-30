"""My League step F3: the lineup optimizer, lineup regret, the personalized Radar and the drop
candidates. Synthetic data only (tests/league_fixtures.py and hand-made league rows: fake names,
fake cookies); every network path is blocked by test_league's autouse fixture."""

from __future__ import annotations

import itertools
import random
from datetime import datetime
from pathlib import Path

import duckdb
import polars as pl
import pytest

from tests.league_fixtures import LEAGUE_ID
from tests.test_league import (  # noqa: F401 - fixtures (isolated is autouse)
    GOOD_ENV,
    SECRETS,
    _run,
    cli_sync,
    fake_league,
    isolated,
)
from twm.league import commands, personal, store
from twm.league.lineup import Candidate, optimize, starting_slots
from twm.league.regret import Line, load_season, season_text, week_regret

LEAGUE_SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "RB/WR/TE": 1, "K": 1, "D/ST": 1, "BE": 7,
                "IR": 1}  # fmt: skip
SLOTS = starting_slots(LEAGUE_SLOTS)[0]


def _c(key: int, pos: str, value: float, **kw) -> Candidate:
    return Candidate(key, pos, value, **kw)


# --------------------------------------------------------------------------------------
# the optimizer (twm.league.lineup)
# --------------------------------------------------------------------------------------


def test_starting_slots_keep_the_leagues_slots_and_name_the_unmodelled():
    slots, notes = starting_slots(LEAGUE_SLOTS | {"DT": 1, "OP": 0})
    assert slots == {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "RB/WR/TE": 1, "K": 1, "D/ST": 1}
    assert notes == ["lineup: 1 ESPN slot(s) 'DT' not modelled (left out of every lineup)"]


def test_flex_takes_the_best_player_left_after_the_position_slots():
    got = optimize([_c(1, "RB", 20), _c(2, "RB", 15), _c(3, "RB", 14), _c(4, "WR", 9),
                    _c(5, "WR", 8), _c(6, "WR", 13), _c(7, "TE", 5), _c(8, "TE", 12)],
                   {"RB": 2, "WR": 2, "TE": 1, "RB/WR/TE": 1})  # fmt: skip
    assert [k for s, k in got.picks if s == "RB"] == [1, 2]
    assert [k for s, k in got.picks if s == "RB/WR/TE"] == [3]  # RB 14 beats WR 9 and TE 5
    assert [k for s, k in got.picks if s == "TE"] == [8]
    assert got.value == 20 + 15 + 13 + 9 + 12 + 14 and got.empty == 0


def test_a_slot_stays_empty_only_when_nobody_fits_and_a_negative_dst_still_starts():
    got = optimize([_c(1, "QB", 18), _c(2, "D/ST", -4), _c(3, "RB", 7)], SLOTS)
    assert dict((s, k) for s, k in got.picks if k is not None) == {"QB": 1, "D/ST": 2, "RB": 3}
    assert got.empty == sum(SLOTS.values()) - 3  # RB, 2 WR, TE, FLEX, K stay empty
    assert got.value == 21
    assert optimize([], SLOTS).empty == sum(SLOTS.values())


def test_ties_go_to_the_owners_starter_then_a_fixed_order():
    a = optimize([_c(10, "WR", 9), _c(11, "WR", 9, prefer=True), _c(12, "WR", 9)], {"WR": 1})
    assert a.keys == {11}
    b = optimize([_c(12, "WR", 9), _c(10, "WR", 9), _c(11, "WR", 9)], {"WR": 1})
    assert b == optimize([_c(10, "WR", 9), _c(11, "WR", 9), _c(12, "WR", 9)], {"WR": 1})
    # a float-noise tie is a tie: 0.1 + 0.2 vs 0.3
    c = optimize([_c(1, "WR", 0.1), _c(2, "WR", 0.2), _c(3, "WR", 0.3, prefer=True)],
                 {"WR": 1, "RB/WR/TE": 1})  # fmt: skip
    assert 3 in c.keys


def test_a_multi_position_player_may_use_the_slot_espn_let_him_start_in():
    plain = _c(1, "RB", 10)
    extra = _c(1, "RB", 10, extra_slot="WR")
    assert not plain.fits("WR") and extra.fits("WR") and extra.fits("RB/WR/TE")
    assert optimize([extra, _c(2, "RB", 12)], {"RB": 1, "WR": 1}).value == 22
    with pytest.raises(ValueError, match="twice"):
        optimize([plain, extra], {"RB": 1})


def _brute(cands: list[Candidate], slots: dict[str, int]) -> tuple[int, int]:
    """(slots filled, value in 1/10,000 points) of the best assignment, by enumeration."""
    labels = [s for s, n in slots.items() for _ in range(n)]
    best = (0, 0)
    for choice in itertools.product([None, *range(len(cands))], repeat=len(labels)):
        used = [i for i in choice if i is not None]
        if len(used) != len(set(used)):
            continue
        if any(i is not None and not cands[i].fits(s) for s, i in zip(labels, choice, strict=True)):
            continue
        best = max(best, (len(used), sum(round(cands[i].value * 10_000) for i in used)))
    return best


def test_the_optimizer_is_exact_on_random_rosters_with_non_nested_flex_slots():
    rng = random.Random(20260930)
    slots = {"QB": 1, "RB": 1, "WR": 1, "RB/WR": 1, "WR/TE": 1, "OP": 1}
    for _ in range(60):
        cands = [_c(i, rng.choice(["QB", "RB", "WR", "TE", "K"]), round(rng.uniform(-3, 30), 2))
                 for i in range(rng.randint(0, 7))]  # fmt: skip
        got = optimize(cands, slots)
        assert (len(got.keys), round(got.value * 10_000)) == _brute(cands, slots)
        assert all(next(c for c in cands if c.key == k).fits(s) for s, k in got.picks if k)


# --------------------------------------------------------------------------------------
# one week of lineup regret (twm.league.regret.week_regret)
# --------------------------------------------------------------------------------------

WEEK = [  # (id, name, position, slot, points, projected, on_bye)
    (1, "Q One", "QB", "QB", 20, 18, False), (2, "Q Two", "QB", "BE", 25, 15, False),
    (3, "R One", "RB", "RB", 10, 12, False), (4, "R Two", "RB", "RB", 5, 11, False),
    (5, "R Three", "RB", "BE", 15, 13, False), (6, "W One", "WR", "WR", 8, 10, False),
    (7, "W Two", "WR", "WR", 12, 9, False), (8, "W Three", "WR", "RB/WR/TE", 3, 8, False),
    (9, "T One", "TE", "TE", 6, 7, False), (10, "K One", "K", "K", 9, 8, False),
    (11, "D One", "D/ST", "D/ST", -2, 6, False), (12, "Hurt Guy", "WR", "IR", 30, None, False),
]  # fmt: skip


def _lines(rows=WEEK) -> list[Line]:
    return [Line(*r) for r in rows]


def test_week_regret_splits_the_gap_into_decisions_and_luck():
    w = week_regret(1, _lines(), SLOTS)
    assert w.actual == 71  # 20 + 10 + 5 + 8 + 12 + 3 + 6 + 9 - 2
    # by projection: R Three (13) and R One at RB, R Two (11) at FLEX over W Three (8)
    assert w.decision == 83 and w.lost_decisions == 12
    # in hindsight also Q Two (25) at QB; the IR-slot player (30) never starts
    assert w.hindsight == 88 and w.lost_luck == 5
    assert 12 not in w.hindsight_lineup.keys and w.no_projection == 0 and w.empty_slots == 0


def test_a_bye_is_projected_zero_and_an_empty_slot_counts():
    rows = [r for r in WEEK if r[0] not in (10, 12)]  # no kicker on the roster
    rows = [(*r[:6], True) if r[0] == 5 else r for r in rows]  # R Three on a bye (projected 13)
    rows = [(r[0], r[1], r[2], r[3], 0, r[5], r[6]) if r[0] == 5 else r for r in rows]
    w = week_regret(2, _lines(rows), SLOTS)
    assert 5 not in w.decision_lineup.keys and w.decision_lineup.empty == 1  # the K slot
    assert w.actual == 62 and w.decision == 62 and w.lost_decisions == 0  # the owner's lineup
    assert w.hindsight == 67 and w.lost_luck == 5 and w.empty_slots == 1  # Q Two over Q One


# --------------------------------------------------------------------------------------
# a hand-made league store (the same tables `twm league sync` writes)
# --------------------------------------------------------------------------------------

SEASON = 2026
T_SYNC = datetime(2026, 9, 30, 18, 0)  # naive UTC, like the store


def _p(espn_id: int, name: str, pos: str, *, team: int | None = 1, slot: str = "BE",
       gsis: str | None = None, pro: str = "KC", status: str = "ACTIVE") -> dict:  # fmt: skip
    ent = f"DST-{pro}" if pos == "D/ST" else gsis
    return {"espn_id": espn_id, "player_name": name, "position": pos, "pro_team": pro,
            "league_team_id": team, "lineup_slot": slot, "injury_status": status,
            "acquisition_type": "DRAFT", "gsis_id": gsis, "entity_id": ent}  # fmt: skip


def _write(con, table: str, rows: list[dict], week: int, synced: datetime = T_SYNC) -> None:
    base = {"league_id": LEAGUE_ID, "season": SEASON, "week": week, "synced_at": synced}
    t = store.TABLES[table]
    where = {k: base[k] for k in t.partition} if t.partition else {}
    cols = {c for c, _ in t.columns}
    store.write(con, table, store.frame(table, [{k: v for k, v in r.items() if k in cols}
                                                for r in rows], **base), **where)  # fmt: skip


def _league_db(path: Path, *, rosters=(), free_agents=(), boxes=None, current: int = 4,
               slots=LEAGUE_SLOTS, mine: bool = True) -> Path:  # fmt: skip
    """``boxes``: week -> (synced_at, box-score rows of the owner's team)."""
    con = store.connect(path)
    try:
        kv = {"team_count": 12, "current_week": current}
        kv |= {f"slot:{k}": v for k, v in slots.items()}
        _write(con, "league_settings", [{"key": k, "value": str(v)} for k, v in kv.items()],
               current)  # fmt: skip
        teams = [(1, "FTA", "Fake Team Alpha", mine), (2, "FTB", "Fake Team Beta", False)]
        _write(con, "league_teams", [
            {"league_team_id": t, "league_team_abbrev": a, "league_team_name": n,
             "league_is_mine": m} for t, a, n, m in teams], current)  # fmt: skip
        _write(con, "league_rosters", list(rosters), current)
        fa = [{**r, "percent_owned": r.get("percent_owned", 5.0), "points": 0.0,
               "projected_points": 1.0, "on_bye": False} for r in free_agents]  # fmt: skip
        _write(con, "league_free_agents", fa, current)
        for week, (synced, rows) in (boxes or {}).items():
            _write(con, "league_box_scores", rows, week, synced)
    finally:
        con.close()
    return path


def _box(rows=WEEK, team: int = 1) -> list[dict]:
    return [{**_p(i, n, pos, team=team, slot=slot), "is_starter": slot not in ("BE", "IR"),
             "points": pts, "projected_points": proj, "on_bye": bye}
            for i, n, pos, slot, pts, proj, bye in rows]  # fmt: skip


# --------------------------------------------------------------------------------------
# lineup regret over a season (twm.league.regret.load_season, `twm league regret`)
# --------------------------------------------------------------------------------------

ENDS = {1: datetime(2026, 9, 15, 4, 0), 2: datetime(2026, 9, 22, 4, 0),
        3: datetime(2026, 9, 29, 4, 0)}  # fmt: skip


def _dim_week(path: Path) -> Path:
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE dim_week (season INTEGER, week INTEGER, season_type VARCHAR, "
                "last_game_end_utc_est TIMESTAMP)")  # fmt: skip
    con.executemany("INSERT INTO dim_week VALUES (2026, ?, 'REG', ?)", list(ENDS.items()))
    con.close()
    return path


def _season_boxes() -> dict:
    week2 = [(*r[:4], 0, r[5], True) if r[0] == 5 else r for r in WEEK]  # R Three on a bye
    return {
        1: (datetime(2026, 9, 16, 9), _box()),
        2: (
            datetime(2026, 9, 23, 9),
            _box(week2) + _box([(99, "Other Guy", "QB", "QB", 40, 30, False)], team=2),
        ),
        3: (datetime(2026, 9, 28, 20), _box()),
    }  # week 3 synced during Sunday's games


def test_regret_counts_only_weeks_synced_after_their_games(tmp_path):
    db = _league_db(tmp_path / "l.duckdb", boxes=_season_boxes())
    con = store.connect(db, read_only=True)
    try:
        res = load_season(con, None, _dim_week(tmp_path / "wh.duckdb"))
        bare = load_season(con, SEASON, tmp_path / "missing.duckdb")
    finally:
        con.close()
    assert [w.week for w in res.weeks] == [1, 2] and res.left_out == [3]
    assert res.total("actual") == 142 and res.total("lost_luck") == 10  # Q Two twice
    assert [w.lost_decisions for w in res.weeks] == [12, 0]  # the bye: R Three projected 0
    assert 99 not in res.names  # the other team's lines are not the owner's
    # without a warehouse a week counts once ESPN's current week (4) is past it
    assert [w.week for w in bare.weeks] == [1, 2, 3]
    assert any("no warehouse" in n for n in bare.notes)
    text = "\n".join(season_text(res))
    assert "week(s) 3" in text and "start R Three instead of W Three (+12.00 points)" in text


def test_regret_needs_box_scores_and_the_owners_team(tmp_path):
    from twm.league.regret import RegretUnavailableError

    empty = _league_db(tmp_path / "a.duckdb")
    other = _league_db(tmp_path / "b.duckdb", boxes=_season_boxes(), mine=False)
    for db, msg in ((empty, "no box scores synced"), (other, "not identified")):
        con = store.connect(db, read_only=True)
        try:
            with pytest.raises(RegretUnavailableError, match=msg):
                load_season(con)
        finally:
            con.close()


def test_cli_regret_prints_the_table_and_the_season_totals(isolated):  # noqa: F811
    env_file, (db, wh, _) = isolated
    env_file.write_text(GOOD_ENV)
    code, out = _run("regret")
    assert code == commands.EXIT_UNAVAILABLE and "nothing synced yet" in out
    _league_db(db, boxes=_season_boxes())
    _dim_week(wh)
    code, out = _run("regret", raw=True)
    assert code == 0, out
    assert "Lineup regret, 2026: your team, 2 week(s)" in out
    assert "slots: QB 1, RB 2, WR 2, TE 1, RB/WR/TE 1, K 1, D/ST 1" in out
    season = next(ln for ln in out.splitlines() if ln.lstrip().startswith("season"))
    assert season.split() == ["season", "142.00", "154.00", "164.00", "+12.00", "10.00"]
    assert "Fake Team" not in out and "FTA" not in out
    assert _run("regret", "--season", "2025")[0] == commands.EXIT_UNAVAILABLE


# --------------------------------------------------------------------------------------
# the personalized Radar and the drop candidates (twm.league.personal, `twm league radar`)
# --------------------------------------------------------------------------------------

VERSIONS = {"waiver_radar": "logit-f3test000000001", "streamer_k": "logit_k-f3test00000002",
            "streamer_dst": "baseline_opponent_dst-f3test03",
            "regression_watch": "mean_flat_all-f3test0000004"}  # fmt: skip
MODULES = {"waiver_radar": "waiver_radar", "streamer_k": "streamer", "streamer_dst": "streamer",
           "regression_watch": "regression_watch"}  # fmt: skip


def _pins(path: Path, season: int = SEASON) -> Path:
    import yaml

    body = {k: {"season": season, "model_version": v, "file": f"artifacts/{v}.json",
                "sha256": "0" * 64} for k, v in VERSIONS.items()}  # fmt: skip
    path.write_text(yaml.safe_dump(body))
    return path


def _band(chance: float, lo: float, hi: float) -> str:
    import json

    return json.dumps({"chance": chance, "lo": lo, "hi": hi, "n": 100, "hits": 50, "level": 0.9})


def _reasons(proj: float) -> str:
    import json

    return json.dumps({"projection": proj, "ppg": proj + 1, "games": 3, "xfp_pg": proj - 1,
                       "fpoe_pg": 2.0, "xfp_used": proj - 1.5, "fpoe_used": 1.5})  # fmt: skip


def _predictions(path: Path, rows: list[tuple]) -> Path:
    """``rows``: (pin key or a version, position, entity, rank, score, band, week, tier)."""
    from twm import predictions as pr

    con = pr.connect(path)
    try:
        for key, pos, ent, rank, score, band, week, tier in rows:
            version = VERSIONS.get(key, key)
            module = MODULES.get(key, "waiver_radar")
            reasons = _reasons(score) if module == "regression_watch" else "[]"
            con.execute(
                "INSERT INTO predictions (module, entity_type, entity_id, season, week, as_of, "
                "horizon, rank_group, score, raw_score, rank, band, model_version, reasons_json, "
                "kind, created_at, tier, incomplete) VALUES (?, 'player', ?, ?, ?, ?, 1, ?, ?, "
                "NULL, ?, ?, ?, ?, 'live', ?, ?, FALSE)",
                [module, ent, SEASON, week, datetime(2026, 9, 29, 14), pos, score, rank, band,
                 version, reasons, datetime(2026, 9, 29, 15), tier],
            )  # fmt: skip
    finally:
        con.close()
    return path


ROSTERS = [
    _p(101, "Mine QB One", "QB", slot="QB", gsis="G101"),
    _p(102, "Mine QB Two", "QB", gsis="G102"),
    _p(201, "Mine RB A", "RB", slot="RB", gsis="G201"),
    _p(202, "Mine RB B", "RB", slot="RB", gsis="G202"),
    _p(203, "Mine RB C", "RB", gsis="G203"),
    _p(204, "Mine RB Hurt", "RB", slot="IR", gsis="G204", status="INJURY_RESERVE"),
    _p(301, "Mine WR A", "WR", slot="WR", gsis="G301"),
    _p(302, "Mine WR B", "WR", slot="WR", gsis="G302"),
    _p(303, "Mine WR Unmatched", "WR", slot="RB/WR/TE"),
    _p(401, "Mine TE", "TE", slot="TE", gsis="G401"),
    _p(501, "Mine K A", "K", slot="K", gsis="G501"),
    _p(502, "Mine K B", "K", gsis="G502", status="QUESTIONABLE"),
    _p(-16012, "Chiefs D/ST", "D/ST", slot="D/ST", pro="KC"),
    _p(601, "Their WR", "WR", team=2, slot="WR", gsis="G601"),
]
FREE = [
    _p(701, "Free RB", "RB", team=None, slot="", gsis="G701", pro="CHI"),
    _p(702, "Free RB Unmatched", "RB", team=None, slot="", pro="CHI") | {"percent_owned": 9.0},
    _p(703, "Free WR Outside", "WR", team=None, slot="", gsis="G703", pro="SF"),
    _p(704, "Free K", "K", team=None, slot="", gsis="G704", pro="DAL"),
    _p(-16003, "Bears D/ST", "D/ST", team=None, slot="", pro="CHI"),
    _p(706, "Free WR", "WR", team=None, slot="", gsis="G706", pro="BUF"),
]
LISTS = [  # (pin key or version, position, entity, rank, score, band, week, tier)
    ("waiver_radar", "QB", "G101", 1, 0.6, _band(0.6, 0.5, 0.7), 3, "must-add"),
    ("waiver_radar", "RB", "G201", 1, 0.7, _band(0.7, 0.6, 0.8), 3, "must-add"),
    ("waiver_radar", "RB", "G701", 2, 0.52, _band(0.52, 0.45, 0.55), 3, "must-add"),
    ("waiver_radar", "RB", "G799", 3, 0.4, _band(0.4, 0.35, 0.45), 3, "speculative"),
    ("waiver_radar", "WR", "G601", 1, 0.5, _band(0.5, 0.4, 0.6), 3, "must-add"),
    ("waiver_radar", "WR", "G706", 5, 0.21, _band(0.21, 0.18, 0.25), 3, "watch"),
    ("waiver_radar", "TE", "G401", 1, 0.3, _band(0.3, 0.2, 0.4), 3, "speculative"),
    ("waiver_radar", "WR", "G703", 1, 0.9, _band(0.9, 0.8, 0.95), 5, "must-add"),  # a later week
    ("logit-unapproved", "WR", "G703", 1, 0.99, _band(0.99, 0.9, 1.0), 3, "must-add"),
    ("streamer_k", "K", "G501", 1, 0.40, _band(0.40, 0.35, 0.45), 3, "must-add"),
    ("streamer_k", "K", "G704", 3, 0.30, _band(0.30, 0.25, 0.35), 3, "speculative"),
    ("streamer_k", "K", "G502", 5, 0.22, _band(0.22, 0.18, 0.26), 3, "watch"),
    ("streamer_dst", "DST", "DST-CHI", 1, None, _band(0.45, 0.4, 0.5), 3, "must-add"),
    ("streamer_dst", "DST", "DST-KC", 2, None, _band(0.35, 0.3, 0.4), 3, "speculative"),
] + [("regression_watch", pos, g, i, proj, None, 3, None) for i, (pos, g, proj) in enumerate([
    ("QB", "G101", 20.0), ("QB", "G102", 14.0), ("RB", "G201", 15.0), ("RB", "G202", 12.0),
    ("RB", "G203", 7.5), ("RB", "G204", 3.0), ("WR", "G301", 18.0), ("WR", "G302", 9.0),
    ("TE", "G401", 10.0)], start=1)]  # fmt: skip


@pytest.fixture
def world(tmp_path):
    db = _league_db(tmp_path / "l.duckdb", rosters=ROSTERS, free_agents=FREE)
    return db, _predictions(tmp_path / "p.duckdb", LISTS), _pins(tmp_path / "pins.yaml")


def _build(world, **kw) -> personal.PersonalRadar:
    db, preds, pins = world
    con = store.connect(db, read_only=True)
    try:
        return personal.build(con, preds, pins_path=pins, **kw)
    finally:
        con.close()


def _names(df: pl.DataFrame) -> list[str]:
    return df.get_column("player_name").to_list()


def test_the_lists_keep_their_ranks_and_chances_for_the_leagues_free_agents_only(world):
    res = _build(world)
    assert (res.week, res.sync_week, res.team_found) == (3, 4, True)  # latest stored <= week 4
    by = {x.position: x for x in res.lists}
    rb = by["RB"]
    assert _names(rb.picks) == ["Free RB"] and rb.picks.row(0, named=True)["rank"] == 2
    assert rb.picks.row(0, named=True)["chance"] == "52% (similar players hit 45-55%)"
    assert _names(rb.outside) == ["Free RB Unmatched"]  # no gsis id: never given a chance
    assert rb.unknown == 1  # G799: neither a synced free agent nor on a roster
    # G703 is only on an unapproved model's list and on the week-5 list: outside the pool
    assert _names(by["WR"].picks) == ["Free WR"] and _names(by["WR"].outside) == ["Free WR Outside"]
    assert by["QB"].free == 0 and by["TE"].free == 0  # their listed players are rostered
    k = by["K"].picks.row(0, named=True)
    assert (k["player_name"], k["rank"]) == ("Free K", 3)
    assert k["chance"] == "30% (similar kickers started 25-35%)"
    dst = by["DST"].picks.row(0, named=True)
    assert dst["player_name"] == "Bears D/ST"
    assert dst["chance"] == "45% (this rank's past picks started 40-50%)"
    assert by["DST"].outside.height == 0


def test_an_explicit_week_and_a_missing_one(world):
    res = _build(world, week=5)
    by = {x.position: x for x in res.lists}
    assert _names(by["WR"].picks) == ["Free WR Outside"] and by["WR"].outside.height == 1
    assert "no stored QB list for week 5" in res.notes
    assert "no stored Regression Watch projections for week 5" in res.notes
    with pytest.raises(personal.PersonalUnavailableError, match=r"week 2 \(stored: 3, 5\)"):
        _build(world, week=2)


def test_drop_candidates_are_the_lowest_number_at_a_position_with_depth(world):
    res = _build(world)
    drops = {d.position: d for d in res.drops}
    assert sorted(drops) == ["K", "QB", "RB", "WR"]  # TE 1 <= 1.33, D/ST 1 <= 1
    assert (drops["QB"].name, drops["QB"].value, drops["QB"].rostered) == ("Mine QB Two", 14, 2)
    rb = drops["RB"]
    assert (rb.name, rb.value, rb.rostered) == ("Mine RB C", 7.5, 3)  # never the IR-slot RB (3.0)
    assert rb.how == "2 RB + 1/3 of 1 RB/WR/TE" and rb.need == pytest.approx(7 / 3, abs=1e-4)
    assert "projects 7.50 points per game" in rb.numbers and "over 3 games" in rb.numbers
    assert (drops["WR"].name, drops["WR"].unrated) == ("Mine WR B", ["Mine WR Unmatched"])
    k = drops["K"]
    assert (k.name, k.value, k.status) == ("Mine K B", 0.22, "QUESTIONABLE")
    assert "rank 5 of the week's pool" in k.numbers
    assert res.on_ir == 1
    text = "\n".join(personal.radar_text(res))
    assert "Mine RB Hurt" not in text and "1 IR-slot player(s) left out" in text
    assert "RB: 3 rostered for 2.33 (2 RB + 1/3 of 1 RB/WR/TE): Mine RB C" in text


def test_the_needs_count_every_flex_type_slot_share():
    got = personal.needs({"QB": 1, "RB": 2, "WR": 2, "TE": 1, "RB/WR/TE": 1, "OP": 1, "K": 1})
    assert got["QB"] == (1.25, "1 QB + 1/4 of 1 OP")
    assert got["WR"][0] == pytest.approx(2 + 1 / 3 + 1 / 4, abs=1e-4)
    assert got["DST"] == (0.0, "0 DST")


def test_no_pinned_model_for_the_season_or_no_owner_team(world, tmp_path):
    db, preds, _ = world
    with pytest.raises(personal.PersonalUnavailableError, match="is for 2025, not 2026"):
        _build((db, preds, _pins(tmp_path / "old.yaml", season=2025)))
    other = _league_db(tmp_path / "o.duckdb", rosters=ROSTERS, free_agents=FREE, mine=False)
    res = _build((other, preds, world[2]))
    assert res.drops == [] and not res.team_found
    assert "your team was not identified" in "\n".join(personal.radar_text(res))


# --------------------------------------------------------------------------------------
# `twm league radar` / `regret` after a FakeLeague sync; the guard; privacy
# --------------------------------------------------------------------------------------

FAKE_LISTS = [
    ("waiver_radar", "RB", "00-0090002", 1, 0.7, _band(0.7, 0.6, 0.8), 3, "must-add"),
    ("waiver_radar", "RB", "00-0090101", 2, 0.52, _band(0.52, 0.45, 0.55), 3, "must-add"),
    ("waiver_radar", "WR", "00-0090006", 1, 0.5, _band(0.5, 0.4, 0.6), 3, "must-add"),
    ("streamer_k", "K", "00-0090005", 1, 0.4, _band(0.4, 0.35, 0.45), 3, "must-add"),
    ("streamer_dst", "DST", "DST-KC", 1, None, _band(0.45, 0.4, 0.5), 3, "must-add"),
    ("regression_watch", "QB", "00-0090001", 1, 20.0, None, 3, None),
    ("regression_watch", "RB", "00-0090002", 1, 12.0, None, 3, None),
]


@pytest.fixture
def f3_sync(cli_sync, monkeypatch, tmp_path):  # noqa: F811
    preds = _predictions(tmp_path / "p.duckdb", FAKE_LISTS)
    monkeypatch.setattr(commands, "lists_paths", lambda: (preds, _pins(tmp_path / "pins.yaml")))
    return cli_sync


def test_cli_radar_after_a_fake_league_sync(f3_sync):
    assert _run("sync")[0] == 0
    code, out = _run("radar", raw=True)
    assert code == 0, out
    assert "the week 3 lists (made after week 3's games)" in out and "ESPN week 3" in out
    assert "Fake Waiver Back" in out and "52% (similar players hit 45-55%)" in out
    assert "not in the Radar's pool (no chance): Fake Unknown Rookie (-, 42%)" in out
    assert "Fake Runner" not in out  # rostered: never offered as a pickup
    assert "no position has more players than it needs" in out
    assert "scores" not in out  # the fixture's scoring equals config/scoring.yaml
    assert _run("radar", "--week", "7")[0] == commands.EXIT_UNAVAILABLE


def test_cli_radar_says_when_the_league_scores_differently(f3_sync, monkeypatch):
    from tests.league_fixtures import BASE_ITEMS, FakeLeague, item
    from tests.test_league import _client

    items = [item(s, 0.5 if s == 53 else p, d) for s, p, d in BASE_ITEMS]  # half-PPR
    monkeypatch.setattr(commands, "connect", lambda e: _client(FakeLeague(items), swid=e.swid))
    assert _run("sync")[0] == 0
    code, out = _run("radar")
    assert code == 0
    assert (
        "your league scores 1 setting(s) differently from config/scoring.yaml (`uv run twm "
        "league settings-diff`); the chances below assume the config's scoring."
    ) in out


@pytest.mark.parametrize("cmd", ["radar", "regret"])
def test_the_new_commands_are_guarded_like_sync(isolated, cmd):  # noqa: F811
    code, out = _run(cmd)
    assert code == commands.EXIT_UNAVAILABLE and "My League is off" in out


def test_no_output_carries_a_cookie_a_member_id_or_a_fantasy_team(f3_sync):
    outs = [_run("sync", raw=True), _run("radar", raw=True), _run("regret", raw=True),
            _run("settings-diff", raw=True)]  # fmt: skip
    assert [code for code, _ in outs] == [0, 0, 0, 0]
    private = (*SECRETS, "Fake Team Alpha", "Fake Team Beta", "fake_owner", str(LEAGUE_ID))
    for _, out in outs:
        assert not any(s in out for s in private), out
