"""My League step LW: the model-version fallback of the stored lists (radar, report, trade) and
`twm league weekly` (the owner's weekly routine). Synthetic data only; every network path is
blocked by test_league's autouse fixture."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.test_league import GOOD_ENV, _run, isolated  # noqa: F401 - isolated is autouse
from tests.test_league_f3 import (
    FREE,
    LISTS,
    ROSTERS,
    RW_HORIZON,
    SEASON,
    VERSIONS,
    _league_db,
    _pins,
    _predictions,
    _reasons,
)
from twm.league import commands, personal, store, trade
from twm.league import weekly as wl
from twm.pipeline.schedule import WeekWindow

RW_PIN = VERSIONS["regression_watch"]


def _rw(path: Path, version: str, created: datetime, proj: float, week: int = 3) -> None:
    """Regression Watch's rows of ``version`` for the owner's QB/RB/WR/TE (test data only)."""
    from twm import predictions as pr

    con = pr.connect(path)
    try:
        for i, (pos, g) in enumerate([("QB", "G101"), ("RB", "G201"), ("RB", "G203"),
                                      ("WR", "G301"), ("TE", "G401")], start=1):  # fmt: skip
            con.execute(
                "INSERT INTO predictions (module, entity_type, entity_id, season, week, as_of, "
                "horizon, rank_group, score, raw_score, rank, band, model_version, reasons_json, "
                "kind, created_at, tier, incomplete) VALUES ('regression_watch', 'player', ?, ?, "
                "?, ?, ?, ?, ?, NULL, ?, NULL, ?, ?, 'live', ?, NULL, FALSE)",
                [g, SEASON, week, datetime(2026, 9, 29, 14), RW_HORIZON, pos, proj + i, i,
                 version, _reasons(proj + i), created],
            )  # fmt: skip
    finally:
        con.close()


def _world(tmp_path: Path, lists: list[tuple]) -> tuple[Path, Path, Path]:
    db = _league_db(tmp_path / "l.duckdb", rosters=ROSTERS, free_agents=FREE)
    return db, _predictions(tmp_path / "p.duckdb", lists), _pins(tmp_path / "pins.yaml")


def _build(world, **kw) -> personal.PersonalRadar:
    db, preds, pins = world
    con = store.connect(db, read_only=True)
    try:
        return personal.build(con, preds, pins_path=pins, **kw)
    finally:
        con.close()


NO_RW = [x for x in LISTS if x[0] != "regression_watch"]


def test_the_newest_stored_version_stands_in_for_a_missing_pinned_list_and_says_so(tmp_path):
    w = _world(tmp_path, NO_RW)
    _rw(w[1], "mean_flat_all-old1", datetime(2026, 9, 20), 5.0)
    _rw(w[1], "mean_flat_all-old2", datetime(2026, 9, 30), 9.0)  # the newest
    res = _build(w)
    assert set(res.projections.get_column("model_version")) == {"mean_flat_all-old2"}  # one
    assert res.projections.height == 5
    note = next(n for n in res.notes if "previous model version" in n)
    assert note.startswith("Regression Watch's projections from the previous model version "
                           f"mean_flat_all-old2: the approved {RW_PIN} has no list stored "
                           "for week 3")  # fmt: skip
    assert "no stored Regression Watch projections" not in "\n".join(res.notes)
    assert res.drops is not None
    used = {r.name: r.source for _, r in res.drops.lineup if r is not None}
    assert used["Mine QB One"] == "projection"  # the fallback list's number, not an average
    assert f"Note: {note}" in personal.radar_text(res)


def test_the_pinned_version_is_preferred_over_a_newer_one(tmp_path):
    w = _world(tmp_path, LISTS)
    _rw(w[1], "mean_flat_all-newer", datetime(2026, 10, 2), 30.0)  # newer, not approved
    res = _build(w)
    assert set(res.projections.get_column("model_version")) == {RW_PIN}
    assert not [n for n in res.notes if "previous model version" in n]


def test_a_radar_position_falls_back_alone_and_never_mixes_versions(tmp_path):
    lists = [x for x in LISTS if not (x[0] == "waiver_radar" and x[1] == "WR")]
    w = _world(tmp_path, lists)  # WR week 3: only "logit-unapproved" is stored
    res = _build(w)
    by = {x.position: x for x in res.lists}
    assert [n for n in res.notes if "previous model version" in n] == [
        "the Radar's WR list from the previous model version logit-unapproved: the approved "
        f"{VERSIONS['waiver_radar']} has no list stored for week 3 (`uv run twm league weekly "
        "--week 3` scores it)"]  # fmt: skip
    assert by["WR"].picks.get_column("player_name").to_list() == ["Free WR Outside"]
    assert by["RB"].picks.get_column("player_name").to_list() == ["Free RB"]  # pinned
    assert "no stored WR list for week 3" not in res.notes


def test_the_trade_checker_falls_back_the_same_way(tmp_path):
    _, preds, pins = _world(tmp_path, NO_RW)
    _rw(preds, "mean_flat_all-old1", datetime(2026, 9, 30), 5.0)
    p = trade.projections(preds, SEASON, 4, None, pins)
    assert (p.week, p.version, len(p.rows)) == (3, "mean_flat_all-old1", 5)
    assert p.note is not None and "from the previous model version mean_flat_all-old1" in p.note
    _rw(preds, RW_PIN, datetime(2026, 9, 29), 7.0)  # the approved list, older: still preferred
    p = trade.projections(preds, SEASON, 4, None, pins)
    assert (p.version, p.note) == (RW_PIN, None)
    with pytest.raises(trade.TradeError, match=r"week 5 \(stored: 3\)"):
        trade.projections(preds, SEASON, 4, 5, pins)


# --------------------------------------------------------------------------------------
# `twm league weekly`: the routine with mocked steps
# --------------------------------------------------------------------------------------

AS_OF_1 = datetime(2026, 9, 15, 14, tzinfo=UTC)  # week w's as-of: one Tuesday per week
NOW = datetime(2026, 10, 3, 15, tzinfo=UTC)  # a Saturday: week 3's as-of has passed, 4's not
PINS = {k: (SEASON, v) for k, v in VERSIONS.items()}


def _windows(season: int) -> list[WeekWindow]:
    return [WeekWindow(w, AS_OF_1 + timedelta(days=7 * (w - 1)), None) for w in range(1, 19)]


@pytest.fixture
def fake(tmp_path):
    calls: list[tuple] = []
    codes: dict[str, int] = {}  # "<command> <subcommand>" or a league step -> exit code

    def execute(stage: str, args: list[str], log: Path, limit: float) -> int:
        calls.append(("twm", *args))
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("first line\nthe last line of the log\n")
        return codes.get(" ".join(args[:2]), 0)

    def league(name: str):
        def step(*a):
            calls.append((name, *a[:-1]))
            a[-1](f"My League: {name} done")
            return codes.get(name, 0)

        return step

    parts = wl.Parts(execute=execute, missing_cache=lambda s: [], windows=_windows,
                     pins=lambda: PINS, stored=lambda s, w: set(), sync=league("sync"),
                     report=league("report"), radar=league("radar"), out=tmp_path / "weekly",
                     pbp_start=1999, season=SEASON)  # fmt: skip
    return parts, calls, codes


def _go(parts, week=None, now=NOW) -> tuple[int, list[str]]:
    out: list[str] = []
    return wl.run(week, 3, out.append, parts, now=now), out


def test_the_routine_runs_its_steps_in_order_one_line_each(fake):
    parts, calls, _ = fake
    code, out = _go(parts)
    assert code == 0
    assert calls[:2] == [("twm", "ingest", "--start", "2026", "--force"),
                         ("twm", "build", "--start", "1999", "--end", "2026")]  # fmt: skip
    scores = calls[2:5]
    assert [c[1] for c in scores] == ["radar", "streamer", "regression"]
    for c in scores:
        assert c[2:7] == ("score", "--season", "2026", "--week", "3")
        assert ("--pinned" in c) == (c[1] == "radar")
        report = Path(c[c.index("--out") + 1])  # never the committed weekly reports
        assert report.parent.parent == parts.out and report.name == f"{c[1]}-2026-W03.md"
    assert calls[5:] == [("sync",), ("report", 3), ("radar", 3, 3)]
    assert [x.split(":")[0] for x in out[:5]] == ["ingest", "build", "score", "sync", "report"]
    assert out[2].startswith("score: week 3: Radar scored in ")
    assert out[3].startswith("sync: sync done (") and out[-1].startswith("My League weekly: done")


@pytest.mark.parametrize(("failing", "done"), [("ingest --start", 1), ("build --start", 2),
                                               ("radar score", 3), ("regression score", 5),
                                               ("sync", 6), ("report", 7)])  # fmt: skip
def test_any_failure_stops_the_routine_with_one_clear_line(fake, failing, done):
    parts, calls, codes = fake
    codes[failing] = 1
    code, out = _go(parts)
    assert code == 1 and len(calls) == done  # nothing after the failed step
    stage = {"ingest --start": "ingest", "build --start": "build"}.get(failing, failing)
    stage = "score" if failing.endswith("score") else stage
    line = next(x for x in out if "stopped" in x)
    assert line.startswith(f"{stage}: stopped: ") and "done in" not in "\n".join(out)
    if failing == "build --start":
        assert line == ("build: stopped: `twm build --start 1999 --end 2026` exited with 1 (log: "
                        f"{parts.out}/20261003T150000Z/build.log)")  # fmt: skip
        assert out[-1] == "    the last line of the log"


def test_the_build_always_gets_the_full_range_from_the_settings(fake):
    from twm.config import settings

    real = wl.default_parts()
    assert (real.pbp_start, real.season) == (settings().seasons["pbp_start"],
                                             settings().current_season)  # fmt: skip
    assert real.out == commands.report_dir() / "weekly"
    parts, calls, _ = fake
    _go(replace(parts, pbp_start=real.pbp_start, season=real.season), week=3)
    builds = [c for c in calls if c[0] == "twm" and c[1] == "build"]
    assert builds == [("twm", "build", "--start", str(real.pbp_start), "--end",
                       str(real.season))]  # fmt: skip


def test_a_stored_list_is_never_scored_again_and_a_partial_one_stops(fake):
    parts, calls, _ = fake
    stored = {VERSIONS["waiver_radar"], VERSIONS["streamer_k"], VERSIONS["streamer_dst"]}
    code, out = _go(replace(parts, stored=lambda s, w: stored))
    assert code == 0 and [c[1] for c in calls if c[0] == "twm"][2:] == ["regression"]
    assert out[2].startswith("score: week 3: Radar already stored, streamer already stored, "
                             "Regression Watch scored in ")  # fmt: skip
    calls.clear()
    code, out = _go(replace(parts, stored=lambda s, w: {VERSIONS["streamer_k"]}))
    assert code == 1 and [c[1] for c in calls if c[0] == "twm"][2:] == ["radar"]
    assert out[-1] == (f"score: stopped: the streamer's week 3 list is stored for "
                       f"{VERSIONS['streamer_k']} only: not scored again (that would replace "
                       "the stored list)")  # fmt: skip


def test_data_not_arrived_no_week_due_a_cold_cache_and_an_explicit_week(fake):
    parts, calls, codes = fake
    codes["streamer score"] = 3
    code, out = _go(parts)
    assert code == wl.EXIT_NOT_READY and calls[-1][1] == "streamer"  # no sync, no report
    assert "score: stopped: week 3's data has not fully arrived yet" in out[-3]
    del codes["streamer score"]
    calls.clear()
    code, out = _go(parts, now=AS_OF_1 - timedelta(days=1))
    assert code == wl.EXIT_UNAVAILABLE and out[-1].startswith("score: stopped: no list is due")
    calls.clear()
    code, out = _go(replace(parts, missing_cache=lambda s: ["pbp 2003", "pbp 2004"]))
    assert code == 1 and calls == [] and "the cache misses 2 historical file(s)" in out[0]
    code, out = _go(parts, week=5)
    assert all(c[c.index("--week") + 1] == "5" for c in calls if "score" in c)
    assert calls[-2:] == [("report", 5), ("radar", 5, 3)]


def test_weekly_is_guarded_like_the_other_league_commands(isolated, fake, monkeypatch):  # noqa: F811
    code, out = _run("weekly")
    assert code == commands.EXIT_UNAVAILABLE and "My League is off" in out
    isolated[0].write_text(GOOD_ENV)
    monkeypatch.setattr(wl, "default_parts", lambda: fake[0])
    code, out = _run("weekly", "--week", "3", "--limit", "2")
    assert code == 0 and "score: week 3: Radar scored" in out and fake[1][-1] == ("radar", 3, 2)


def test_stored_versions_reads_the_store_read_only(tmp_path):
    assert wl.stored_versions(tmp_path / "none.duckdb", SEASON, 3) == set()
    _, preds, _ = _world(tmp_path, LISTS)
    assert wl.stored_versions(preds, SEASON, 3) == {*VERSIONS.values(), "logit-unapproved"}
    assert wl.stored_versions(preds, SEASON, 5) == {VERSIONS["waiver_radar"]}
    assert wl.due_week(_windows(SEASON), NOW) == 3
