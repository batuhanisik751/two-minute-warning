"""`twm league journal` ("You vs the model", twm.league.journal) on a synthetic league store and
predictions store: no network, no ESPN, no warehouse (its labels and weekly points are given)."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from tests.league_fixtures import LEAGUE_ID
from tests.test_league import GOOD_ENV, _run, isolated  # noqa: F401 - fixture (autouse)
from tests.test_league_f3 import LEAGUE_SLOTS, MODULES, VERSIONS, _band, _box, _p, _pins, _write
from twm import predictions as pr
from twm.league import commands, journal, store

SEASON = 2026
AS_OF = {2: datetime(2026, 9, 22, 14), 3: datetime(2026, 9, 29, 14)}  # Tuesday as-ofs (UTC)
T0 = datetime(2026, 9, 10, 18)  # before any stored list
T1 = datetime(2026, 9, 23, 10)  # week 3's window: the week-2 list is the newest public one
T2 = datetime(2026, 9, 30, 8)  # week 4's window: the week-3 list
MS = {T0: 1_000, T1: 2_000, T2: 3_000}  # activity_ms (only the order matters here)

LISTS = [  # (pin key, position, entity, rank, score, band or tag, week, tier)
    ("waiver_radar", "RB", "G701", 1, 0.6, _band(0.6, 0.5, 0.7), 2, "must-add"),
    ("waiver_radar", "RB", "G702", 2, 0.3, _band(0.3, 0.2, 0.4), 2, "speculative"),
    ("waiver_radar", "WR", "G703", 1, 0.55, _band(0.55, 0.5, 0.6), 2, "must-add"),
    ("waiver_radar", "RB", "G701", 1, 0.7, _band(0.7, 0.6, 0.8), 3, "must-add"),
    ("waiver_radar", "WR", "G706", 2, 0.58, _band(0.58, 0.5, 0.65), 3, "must-add"),
    ("streamer_k", "K", "G704", 2, 0.3, _band(0.3, 0.25, 0.35), 2, "speculative"),
    ("regression_watch", "WR", "G301", 1, 9.0, "buy_low", 2, None),
]
KIND = {2: ("backtest", datetime(2026, 9, 28, 9)), 3: ("live", datetime(2026, 9, 29, 15))}


def _predictions(path: Path, rows=LISTS, outcomes=()) -> Path:
    """The lists (week 2 reconstructed on 09-28, week 3 live) and the Radar's stored outcomes
    ((entity, week, y_hit, status))."""
    con = pr.connect(path)
    try:
        for key, pos, ent, rank, score, band, week, tier in rows:
            kind, created = KIND[week]
            con.execute(
                "INSERT INTO predictions (module, entity_type, entity_id, season, week, as_of, "
                "horizon, rank_group, score, raw_score, rank, band, model_version, reasons_json, "
                "kind, created_at, tier, incomplete) VALUES (?, 'player', ?, ?, ?, ?, 3, ?, ?, "
                "NULL, ?, ?, ?, '{}', ?, ?, ?, FALSE)",
                [MODULES[key], ent, SEASON, week, AS_OF[week], pos, score, rank, band,
                 VERSIONS[key], kind, created, tier],
            )  # fmt: skip
        for ent, week, hit, status in outcomes:
            con.execute("INSERT INTO outcomes (module, entity_id, season, week, as_of, y_hit, "
                        "y_sustained, label_status) VALUES ('waiver_radar', ?, ?, ?, ?, ?, NULL, "
                        "?)", [ent, SEASON, week, AS_OF[week], hit, status])  # fmt: skip
    finally:
        con.close()
    return path


ACTIVITY = [  # (time, action, espn id, name, position, gsis, fantasy team)
    (T0, "FA ADDED", 704, "Free K", "K", "G704", 1),
    (T1, "FA ADDED", 701, "Free RB", "RB", "G701", 1),
    (T1, "DROPPED", 301, "Mine WR A", "WR", "G301", 1),
    (T2, "WAIVER ADDED", 703, "Free WR Outside", "WR", "G703", 1),
    (T2, "DROPPED", 502, "Mine K B", "K", "G502", 1),
    (T2, "FA ADDED", 706, "Free WR", "WR", "G706", 2),  # another team: never the owner's move
]
FREE = {  # ESPN week -> (synced_at, free agents (espn id, name, position, gsis))
    3: (datetime(2026, 9, 23, 6), [(701, "Free RB", "RB", "G701"), (702, "Free RB Two", "RB",
        "G702"), (703, "Free WR Outside", "WR", "G703")]),
    4: (datetime(2026, 9, 30, 6), [(703, "Free WR Outside", "WR", "G703"),
                                   (706, "Free WR", "WR", "G706")]),
}  # fmt: skip
BOX = {  # week -> (synced_at, (espn id, name, position, slot, points, projected, bye))
    3: (datetime(2026, 9, 30, 5), [(101, "Mine QB One", "QB", "QB", 20.0, 18.0, False),
                                   (701, "Free RB", "RB", "RB", 12.5, 10.0, False),
                                   (502, "Mine K B", "K", "K", 8.0, 7.0, False)]),
    4: (datetime(2026, 10, 6, 5), [(101, "Mine QB One", "QB", "QB", 15.0, 18.0, False),
                                   (701, "Free RB", "RB", "BE", 8.0, 9.0, False),
                                   (703, "Free WR Outside", "WR", "WR", 6.0, 8.0, False)]),
}  # fmt: skip


def _league(path: Path, *, mine: bool = True) -> Path:
    con = store.connect(path)
    try:
        kv = {"current_week": 5} | {f"slot:{k}": v for k, v in LEAGUE_SLOTS.items()}
        _write(con, "league_settings", [{"key": k, "value": str(v)} for k, v in kv.items()], 4)
        _write(
            con,
            "league_teams",
            [
                {
                    "league_team_id": t,
                    "league_team_abbrev": a,
                    "league_team_name": n,
                    "league_is_mine": m,
                }
                for t, a, n, m in (
                    (1, "FTA", "Fake Team Alpha", mine),
                    (2, "FTB", "Fake Team Beta", False),
                )
            ],
            4,
        )
        acts = [
            {
                "activity_ms": MS[t],
                "activity_at": t,
                "seq": i,
                "league_team_id": team,
                "action": act,
                "espn_id": eid,
                "player_name": n,
                "position": pos,
                "pro_team": None,
                "bid_amount": 0.0,
                "gsis_id": g,
                "entity_id": g,
            }
            for i, (t, act, eid, n, pos, g, team) in enumerate(ACTIVITY)
        ]
        _write(con, "league_activity", acts, 4)
        for week, (synced, rows) in FREE.items():
            fa = [
                {
                    **_p(e, n, pos, team=None, slot="", gsis=g),
                    "percent_owned": 5.0,
                    "points": 0.0,
                    "projected_points": 1.0,
                    "on_bye": False,
                }
                for e, n, pos, g in rows
            ]
            _write(con, "league_free_agents", fa, week, synced)
        for week, (synced, rows) in BOX.items():
            _write(con, "league_box_scores", _box(rows), week, synced)
    finally:
        con.close()
    return path  # fmt: skip


POINTS = pl.DataFrame(
    [("G701", 3, 12.5, True), ("G701", 4, 8.0, False), ("G301", 3, 20.0, True),
     ("G301", 4, 3.0, False), ("G703", 4, 6.0, False), ("G999", 3, 1.0, True)],
    schema={"gsis_id": pl.String, "week": pl.Int32, "fantasy_points": pl.Float64,
            "is_week_final": pl.Boolean}, orient="row",
)  # fmt: skip
FINAL_NOW = {("G703", 2): False}  # the warehouse's labels as they stand now (final ones)


def _labels(asked: list) -> journal.Labels:
    def labels(rows: pl.DataFrame) -> pl.DataFrame:
        keys = list(zip(rows["gsis_id"], rows["week"], strict=True))
        asked.extend(keys)
        return rows.with_columns(
            pl.Series("y_hit", [FINAL_NOW.get(k) for k in keys], dtype=pl.Boolean),
            pl.Series("label_status", ["final" if k in FINAL_NOW else "pending" for k in keys]),
        )

    return labels  # fmt: skip


@pytest.fixture
def world(tmp_path):
    stored = [("G701", 2, True, "final"), ("G703", 2, None, "pending")]
    preds = _predictions(tmp_path / "p.duckdb", outcomes=stored)
    return _league(tmp_path / "l.duckdb"), preds, _pins(tmp_path / "pins.yaml")


def _sha(*paths: Path) -> list[str]:
    return [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]


def _journal(world, asked: list | None = None, **kw) -> journal.Journal:
    db, preds, pins = world
    con = store.connect(db, read_only=True)
    try:
        return journal.build(con, preds, None, pins_path=pins, points=POINTS,
                             labels=_labels([] if asked is None else asked), **kw)  # fmt: skip
    finally:
        con.close()


def test_adds_use_the_newest_list_public_at_the_move_and_its_outcome(world):
    before = _sha(*world)
    asked: list = []
    j = _journal(world, asked)
    assert _sha(*world) == before  # both stores are read only
    k, rb, wr = j.adds  # the other team's add is never the owner's
    assert (k.name, k.week, k.said.no_list, k.said.listed, k.outcome) == ("Free K", None, True,
                                                                         False, "-")  # fmt: skip
    assert k.said.text() == "no list stored before the move"
    # T1 is after the week-2 list's as-of but before week 3's: the week-3 list is never used
    assert (rb.week, rb.said.list_name, rb.said.week, rb.said.rank, rb.said.tier) == (
        3, "Radar", 2, 1, "must-add")  # fmt: skip
    assert rb.said.made_later  # the week-2 list was stored on 09-28, after the add
    assert rb.outcome == "hit" and ("G701", 2) not in asked  # the store's final label first
    assert (rb.points, rb.started, rb.rostered) == (12.5, 1, 2)  # started week 3, benched week 4
    assert (wr.how, wr.week, wr.said.listed, wr.said.no_list, wr.said.made_later) == (
        "waivers", 4, False, False, False)  # not on the week-3 list, which was live  # fmt: skip
    assert wr.outcome == "-" and (wr.points, wr.started) == (6.0, 1)


def test_a_list_made_after_the_move_is_never_used(world, tmp_path):
    db, _, pins = world
    early = [r for r in LISTS if r[6] == 3]  # only the week-3 list (as-of 09-29, after T1)
    preds = _predictions(tmp_path / "p3.duckdb", early)
    rb = _journal((db, preds, pins)).adds[1]
    assert (rb.name, rb.said.listed, rb.said.no_list, rb.outcome) == ("Free RB", False, True, "-")


def test_drops_compare_the_points_since_with_the_add_of_the_same_move(world):
    wr, k = _journal(world).drops
    assert (wr.name, wr.week, wr.said.tag, wr.basis, wr.other) == (
        "Mine WR A", 3, "Buy-low", "same move", "Free RB")  # fmt: skip
    assert (wr.points, wr.other_points, wr.weeks) == (20.0, 12.5, 1)  # week 4 is not final
    assert (k.name, k.points, k.basis, k.other) == (
        "Mine K B",
        None,
        "same move",
        "Free WR Outside",
    )
    assert "not compared" in journal.drop_line(k)
    assert "20.00 points since (1 finished week(s)) vs Free RB (same move) 12.50" in (
        journal.drop_line(wr))  # fmt: skip


def test_missed_must_adds_were_free_at_the_weeks_sync_and_not_added(world):
    asked: list = []
    j = _journal(world, asked)
    got = [(x.week, x.list_week, x.name, x.rank, x.outcome, x.made_later) for x in j.missed]
    assert got == [
        (3, 2, "Free WR Outside", 1, "miss", True),  # final in the warehouse's labels now
        (4, 3, "Free WR", 2, "pending", False),  # added by another team only
    ]  # Free RB: added in the week-2 list's window; on your roster at the week-4 sync
    assert ("G703", 2) in asked and ("G706", 3) in asked
    assert not any("backfill" in n for n in j.notes)


def test_a_free_agent_sync_after_the_week_is_flagged_and_a_missing_one_skips(world, tmp_path):
    db, preds, pins = world
    con = store.connect(db)
    try:
        con.execute("UPDATE league_free_agents SET synced_at = ? WHERE week = 4",
                    [datetime(2026, 10, 9)])  # fmt: skip
        con.execute("DELETE FROM league_free_agents WHERE week = 3")
    finally:
        con.close()
    j = _journal(world)
    assert [x.list_week for x in j.missed] == [3]
    assert any("ESPN week 4's free agents were synced 2026-10-09" in n for n in j.notes)
    assert any("no free-agent sync of ESPN week 3" in n for n in j.notes)


def test_lineups_come_from_lineup_regret_and_the_summary_counts(world):
    j = _journal(world)
    assert [w.week for w in journal.lineup_weeks(j)] == [3, 4]
    lines = journal.summary(j)
    assert lines[0].startswith("Adds: 3 (1 on the lists at the time, 1 of them must-adds; 1 made")
    assert "1 hit, 0 miss, 0 pending" in lines[0]
    assert lines[1] == ("Drops: 2; 1 compared over finished weeks, the dropped player outscored "
                        "the add in 1.")  # fmt: skip
    assert lines[2] == "Missed must-adds: 2 (0 hit, 1 miss, 1 pending)."
    assert lines[3].startswith("Lineups: 2 finished week(s); you started ESPN's decision-time")
    assert lines[-1] == journal.CAVEAT
    out = "\n".join(journal.text(j))
    assert "You vs the model, 2026:" in out and journal.POINT_IN_TIME in out
    assert "Adds: 3" in out and "Missed must-adds" in out and "week 3: started" in out


def test_one_week_only(world):
    j = _journal(world, week=3)
    assert [a.name for a in j.adds] == ["Free RB"] and [d.name for d in j.drops] == ["Mine WR A"]
    assert [x.name for x in j.missed] == ["Free WR Outside"]
    assert [w.week for w in journal.lineup_weeks(j)] == [3]
    assert "You vs the model, 2026, week 3:" in journal.text(j)[0]


def test_the_week_of_a_move_is_the_as_of_window_that_holds_it():
    windows = [(3, AS_OF[2], AS_OF[3]), (4, AS_OF[3], datetime(2026, 10, 6, 14))]
    empty = pl.DataFrame()
    assert journal.week_of(T1, windows, empty) == 3 and journal.week_of(T2, windows, empty) == 4
    assert journal.week_of(AS_OF[3], windows, empty) == 3  # the window ends at its as-of
    assert journal.week_of(datetime(2027, 1, 20), windows, empty) is None


def test_the_journal_is_guarded_like_the_other_league_commands(isolated, world, monkeypatch):  # noqa: F811
    env_file, (db_path, _, _) = isolated
    code, out = _run("journal")
    assert code == 2 and "My League is off" in out and len(out.strip().splitlines()) == 1
    env_file.write_text(GOOD_ENV)
    code, out = _run("journal")
    assert code == 2 and out.strip() == (
        "My League: nothing synced yet: run `uv run twm league sync` first."
    )
    db, preds, pins = world
    db_path.write_bytes(db.read_bytes())
    monkeypatch.setattr(commands, "lists_paths", lambda: (preds, pins))
    code, out = _run("journal", "--week", "3")
    assert code == 0 and "You vs the model, 2026, week 3:" in out and "Free RB" in out
    assert "Fake Team" not in out and str(LEAGUE_ID) not in out  # no fantasy team or league id


def test_no_owner_team_is_one_line(tmp_path):
    db = _league(tmp_path / "l.duckdb", mine=False)
    preds = _predictions(tmp_path / "p.duckdb")
    con = store.connect(db, read_only=True)
    try:
        with pytest.raises(journal.JournalUnavailableError, match="your team was not identified"):
            journal.build(con, preds, None, pins_path=_pins(tmp_path / "pins.yaml"))
    finally:
        con.close()


def test_the_report_section(world):
    from twm.league import report

    j = _journal(world)
    html = report.journal_section(SimpleNamespace(journal=j, journal_error=""))
    assert 'data-section="journal"' in html and "<h2>You vs the model</h2>" in html
    assert "<h3>Adds (3)</h3>" in html and "<h3>Radar must-adds left on waivers (2)</h3>" in html
    assert "<td>Free RB</td>" in html and journal.CAVEAT in html
    empty = report.journal_section(SimpleNamespace(journal=None, journal_error="nothing yet"))
    assert "nothing yet" in empty and "<h3>" not in empty
