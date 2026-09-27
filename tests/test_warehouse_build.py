"""Offline warehouse build tests on synthetic fixtures (see conftest.py)."""

from __future__ import annotations

import fcntl
import json
import os
import random
import subprocess
import sys
import time
from datetime import UTC, datetime

import duckdb
import polars as pl
import pytest

from tests.conftest import (
    DAILY_DC_DTYPES,
    INJURY_DTYPES,
    LEGACY_DC_DTYPES,
    PBP_DTYPES,
    SCHEDULE_DTYPES,
    SEASON_2025_COUNTS,
    default_teams,
    frame,
    game,
    plays_for,
    season_2025_games,
)
from twm.sources import nflverse as nv
from twm.warehouse import available as av
from twm.warehouse import build as wb
from twm.warehouse import schema as sc
from twm.warehouse import weeks as wk


def _open(db_path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(db_path), read_only=True)
    con.execute("SET TimeZone='UTC'")
    return con


def _hashes(manifest) -> dict[str, str]:
    return {m["table_name"]: m["content_hash"] for m in manifest}


def _types(con, table) -> dict[str, str]:
    return dict(
        con.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = ? ORDER BY ordinal_position",
            [table],
        ).fetchall()
    )


# --------------------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------------------


def test_every_table_builds_from_fixtures(raw, db_path):
    raw.write_season(2025, season_2025_games())
    manifest = wb.build_warehouse([2025], db_path=db_path)
    con = _open(db_path)

    names = {
        r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()
    }
    assert names == set(sc.tables()) | {"build_manifest"}
    counts = dict(wb.table_counts(db_path))
    assert {k: counts[k] for k in SEASON_2025_COUNTS} == SEASON_2025_COUNTS

    # the spec's docs are stored in the file, so any DuckDB client can read them
    assert (
        con.execute(
            "SELECT comment FROM duckdb_columns() WHERE table_name = 'fact_game' "
            "AND column_name = 'kickoff_is_estimated'"
        )
        .fetchone()[0]
        .startswith("gametime missing")
    )
    assert (
        con.execute("SELECT comment FROM duckdb_tables() WHERE table_name = 'dim_week'").fetchone()[
            0
        ]
        == sc.DIM_WEEK.doc
    )

    # manifest content
    m = {r["table_name"]: r for r in manifest}
    assert m["fact_game"]["seasons"] == "[2025]" and m["fact_game"]["duckdb_version"]
    assert m["fact_game"]["polars_version"] == pl.__version__
    assert set(m["fact_game"]) == set(sc.BUILD_MANIFEST_COLUMNS)
    assert all(len(r["content_hash"]) == 32 for r in manifest)
    notes = json.loads(m["fact_game"]["notes"])
    assert notes["n_kickoff_estimated"] == 0 and "location" in notes["null_columns"]
    # one-file datasets do not depend on the season selection
    assert m["dim_team"]["seasons"] == "[]" and m["dim_player"]["seasons"] == "[]"
    # a partial current week: unplayed games keep NULL results and still get a dim_week row
    assert con.execute("SELECT count(*) FROM fact_game WHERE result IS NULL").fetchone() == (8,)
    assert con.execute(
        "SELECT week, n_games FROM dim_week WHERE season = 2025 AND week IN (3, 4) ORDER BY 1"
    ).fetchall() == [(3, 3), (4, 1)]

    # no TIMESTAMP WITH TIME ZONE anywhere; column order follows the spec
    tz = con.execute(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE data_type LIKE 'TIMESTAMP WITH TIME ZONE%'"
    ).fetchone()[0]
    assert tz == 0
    for name, spec in sc.tables().items():  # available_at is the last column of event tables
        assert list(_types(con, name)) == av.stored_column_names(spec), name
    assert list(_types(con, "fact_play")) == [*sc.FACT_PLAY_COLUMNS, "available_at"]
    t = _types(con, "fact_play")
    assert (t["play_id"], t["goal_to_go"], t["epa"], t["game_date"]) == (
        "INTEGER", "INTEGER", "DOUBLE", "DATE",
    )  # fmt: skip

    # fact_game derived columns
    row = con.execute(
        "SELECT kickoff_utc, game_end_utc_est, home_implied_total, away_implied_total, "
        "season_type, kickoff_is_estimated FROM fact_game WHERE game_id = '2025_01_DAL_PHI'"
    ).fetchone()
    assert row == (
        datetime(2025, 9, 5, 0, 20), datetime(2025, 9, 5, 4, 20), 25.0, 22.0, "REG", False,
    )  # fmt: skip
    assert con.execute(
        "SELECT season_type FROM fact_game WHERE game_type = 'WC' LIMIT 1"
    ).fetchone() == ("POST",)

    # dim_team
    assert con.execute("SELECT count(*) FROM dim_team WHERE NOT is_current").fetchone() == (4,)
    assert (
        dict(
            con.execute(
                "SELECT team_abbr, current_abbr FROM dim_team WHERE NOT is_current"
            ).fetchall()
        )
        == sc.TEAM_ALIASES
    )

    # dim_week: Monday-night W1 -> Tuesday 14:00 UTC; W3 has a Tuesday game after the as-of
    weeks = {
        r[0]: r
        for r in con.execute(
            "SELECT week, asof_weekly_utc, n_games_after_asof, is_last_reg_week, "
            "asof_end_of_regular_season_utc, next_week, window_start_utc FROM dim_week "
            "WHERE season = 2025"
        ).fetchall()
    }
    assert weeks[1][1] == datetime(2025, 9, 9, 14, 0) and weeks[1][2] == 0
    assert weeks[3][1] == datetime(2025, 9, 23, 14, 0) and weeks[3][2] == 1
    assert weeks[4][3] is True and weeks[4][4] == datetime(2025, 9, 29, 12, 0)
    assert sum(1 for w in weeks.values() if w[3]) == 1
    assert weeks[4][5] == 5 and weeks[5][6] == weeks[4][1]  # REG 4 -> WC 5 window handoff

    # coaches
    assert con.execute("SELECT coach_id FROM dim_coach ORDER BY 1").fetchall() == [
        ("away_coach",), ("home_coach",),
    ]  # fmt: skip
    assert con.execute(
        "SELECT first_week, last_week, n_games FROM coach_team_season "
        "WHERE coach_id = 'home_coach' AND team = 'PHI'"
    ).fetchone() == (1, 8, 4)
    # the fixture's "Home Coach"/"Away Coach" means every team playing both home and away
    # shows two coaches; the manifest counts those team-seasons (8 of 10 teams here)
    assert json.loads(m["coach_team_season"]["notes"])["n_team_seasons_multi_coach"] == 8

    # daily depth charts map each snapshot to the week whose window contains dt
    assert con.execute(
        "SELECT week_at_dt FROM fact_depth_chart WHERE dt = TIMESTAMP '2025-09-08 12:00:00'"
    ).fetchone() == (1,)
    assert con.execute(
        "SELECT week_at_dt FROM fact_depth_chart WHERE dt = TIMESTAMP '2025-09-14 12:00:00'"
    ).fetchone() == (2,)
    assert con.execute("SELECT DISTINCT unit FROM fact_depth_chart").fetchall() == [("offense",)]

    # dim_player: the NULL gsis_id row is dropped and counted; draft/latest team normalized
    assert m["dim_player"]["n_dropped_null_key"] == 1
    assert con.execute(
        "SELECT latest_team, birth_date FROM dim_player WHERE gsis_id = '00-0000001'"
    ).fetchone() == ("LAC", datetime(1990, 1, 1).date())


def test_primary_key_violation_names_table_and_key_and_rolls_back(raw, db_path):
    games = season_2025_games()
    raw.write_season(2025, games)
    wb.build_warehouse([2025], db_path=db_path)  # good build first

    plays = plays_for(games)
    plays.append(dict(plays[0]))  # duplicate (game_id, play_id)
    raw.write("pbp", 2025, frame(plays, PBP_DTYPES))
    with pytest.raises(
        wb.PrimaryKeyError,
        match=r"fact_play: primary key \('game_id', 'play_id'\) is violated: 1 key value.*"
        r"\('2025_01_DAL_PHI', 1\) appears 2 times",
    ):
        wb.build_warehouse([2025], db_path=db_path)
    # the transaction was rolled back: the earlier good build is intact
    assert dict(wb.table_counts(db_path))["fact_play"] == 28
    assert not db_path.with_name(db_path.name + ".building").exists()


def _dup_teams():
    return pl.concat([default_teams(), default_teams().head(1)])


@pytest.mark.parametrize(
    ("table", "key", "dataset", "make_dup"),
    [
        ("fact_game", "'game_id',", "schedules", lambda g: frame(g + g[:1], SCHEDULE_DTYPES)),
        ("fact_snaps", "'game_id', 'pfr_player_id'", "snap_counts", None),
        ("fact_team_week", "'team', 'season', 'week', 'season_type'", "team_stats", None),
        ("dim_team", "'team_abbr',", "teams", lambda g: _dup_teams()),
    ],
)
def test_primary_key_violation_is_reported_for_every_keyed_table(
    raw, db_path, table, key, dataset, make_dup
):
    games = [game(2025, 1, "2025-09-04", "20:20", "DAL", "PHI")]
    raw.write_season(2025, games)
    if make_dup is not None:
        raw.write(dataset, None if dataset == "teams" else 2025, make_dup(games))
    else:  # duplicate the default single-row file of that dataset
        df = pl.read_parquet(nv.cache_path(dataset, 2025))
        raw.write(dataset, 2025, pl.concat([df, df]))
    with pytest.raises(wb.PrimaryKeyError, match=rf"{table}: primary key \({key}\)"):
        wb.build_warehouse([2025], db_path=db_path)
    assert not db_path.exists()


def test_legacy_depth_chart_non_exact_duplicate_aborts(raw, db_path):
    # Two legacy rows equal on the 11-column key but differing elsewhere are not exact
    # duplicates (DISTINCT keeps both) and must abort rather than pick one silently.
    games = [game(2019, 17, "2019-12-29", "16:25", "JAX", "OAK")]
    rows = [_legacy_row(), _legacy_row(full_name="D. Carr")]
    raw.write_season(2019, games, depth_charts=frame(rows, LEGACY_DC_DTYPES))
    with pytest.raises(wb.PrimaryKeyError, match=r"fact_depth_chart: primary key"):
        wb.build_warehouse([2019], db_path=db_path)


def test_rebuild_swaps_the_file_atomically_and_never_blocks_readers(raw, db_path):
    raw.write_season(2025, season_2025_games())
    raw.write_season(2024, [game(2024, 18, "2025-01-05", "13:00", "MIA", "NYJ", result=3)])
    # leftovers of an interrupted build are removed before the first build starts
    stale = db_path.with_name(db_path.name + ".building")
    stale.write_bytes(b"not a duckdb file")
    stale.with_name(stale.name + ".wal").write_bytes(b"garbage")
    wb.build_warehouse([2025], db_path=db_path)
    r1 = duckdb.connect(str(db_path), read_only=True)
    assert r1.execute("SELECT count(*) FROM fact_game").fetchone() == (14,)
    # a rebuild succeeds although r1 holds the file, and r1 keeps seeing the old contents
    wb.build_warehouse([2025, 2024], db_path=db_path)
    assert r1.execute("SELECT count(*) FROM fact_game").fetchone() == (14,)
    r1.close()  # DuckDB caches instances per path: a new connect while r1 is open reuses it
    con = _open(db_path)
    assert con.execute("SELECT count(*) FROM fact_game").fetchone() == (15,)
    con.close()
    leftovers = sorted(
        p.name for p in db_path.parent.iterdir() if ".building" in p.name or p.suffix == ".wal"
    )
    assert leftovers == []


def test_concurrent_build_is_refused_and_touches_nothing(raw, db_path):
    raw.write_season(2025, season_2025_games())
    wb.build_warehouse([2025], db_path=db_path)
    before = db_path.stat().st_mtime_ns
    # flock is per open-file-description, so a second os.open in this process conflicts
    # exactly like another process would
    lock_path = db_path.with_name(db_path.name + ".build.lock")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(wb.BuildInProgressError, match="already writing"):
            wb.build_warehouse([2025], db_path=db_path)
        assert db_path.stat().st_mtime_ns == before
        assert not db_path.with_name(db_path.name + ".building").exists()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    # once released, a build goes through again and the lock file is left in place
    wb.build_warehouse([2025], db_path=db_path)
    assert lock_path.exists()


def test_stale_wal_beside_the_target_is_discarded_on_rebuild(raw, db_path):
    raw.write_season(2025, season_2025_games())
    wb.build_warehouse([2025], db_path=db_path)
    # a writer that opened the warehouse read-write, wrote, and died before checkpointing
    code = (
        f"import duckdb, os; c = duckdb.connect({str(db_path)!r}); "
        "c.execute('CREATE TABLE foreign_t AS SELECT 1 AS a'); os._exit(0)"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
    wal = db_path.with_name(db_path.name + ".wal")
    assert wal.exists() and wal.stat().st_size > 0
    wb.build_warehouse([2025], db_path=db_path)
    assert not wal.exists()
    con = _open(db_path)
    names = {
        r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()
    }
    con.close()
    assert names == set(sc.tables()) | {"build_manifest"}  # nothing replayed into the new file


def test_connect_is_read_only_by_default(raw, db_path):
    raw.write_season(2025, season_2025_games())
    wb.build_warehouse([2025], db_path=db_path)
    con = wb.connect(db_path)
    with pytest.raises(duckdb.Error, match="read-only"):
        con.execute("CREATE TABLE t AS SELECT 1")
    con.close()


def test_build_refuses_seasons_before_the_first_schedule(raw, db_path):
    with pytest.raises(ValueError, match="none of the requested seasons .* >= 1999"):
        wb.build_warehouse([1990, 1998], db_path=db_path)
    with pytest.raises(ValueError, match="no seasons"):
        wb.build_warehouse([], db_path=db_path)
    assert not db_path.exists()


def test_schema_guard_rejects_unknown_snapshot_columns():
    with pytest.raises(ValueError, match="not in data/schemas/pbp.json"):
        sc._check_names("pbp", ["game_id", "not_a_column"])


def test_cache_path_with_an_apostrophe_builds(tmp_path, monkeypatch, db_path):
    from tests.conftest import RawCache

    root = tmp_path / "O'Brien" / "raw"
    monkeypatch.setattr(nv, "raw_dir", lambda: root)
    cache = RawCache(root)
    cache.write_globals()
    cache.write_season(2026, [game(2026, 1, "2026-09-13", "13:00", "LV", "KC")])  # daily format
    wb.build_warehouse([2026], db_path=db_path)
    assert dict(wb.table_counts(db_path))["fact_depth_chart"] == 1


def test_build_refuses_asof_after_next_weeks_kickoff(raw, db_path):
    # W1 as-of is Tue 2025-09-09 14:00Z; a W2 game at Tue 09:30 ET (13:30Z) starts before it.
    # (09:30 is the earliest real kickoff; 09:00 would be read as the 2000-2005 placeholder.)
    raw.write_season(
        2025,
        [
            game(2025, 1, "2025-09-04", "20:20", "DAL", "PHI"),
            game(2025, 2, "2025-09-09", "09:30", "KC", "LAC"),
        ],
    )
    with pytest.raises(RuntimeError, match=r"dim_week: for \[\(2025, 1\)\] the Tuesday as-of"):
        wb.build_warehouse([2025], db_path=db_path)
    # the transaction was rolled back: no tables were left behind
    assert dict(wb.table_counts(db_path)) == {}
    assert not db_path.with_name(db_path.name + ".building").exists()


def test_dim_coach_merges_spellings_that_share_a_slug(raw, db_path):
    raw.write_season(2025, [
        game(2025, 1, "2025-09-04", "20:20", "DAL", "PHI", home_coach="Sean McVay"),
        game(2025, 2, "2025-09-14", "13:00", "KC", "PHI", home_coach="Sean  Mcvay"),
    ])  # fmt: skip
    manifest = wb.build_warehouse([2025], db_path=db_path)
    con = _open(db_path)
    # canonical spelling: whitespace collapsed, then byte-order first ('V' < 'v')
    assert con.execute(
        "SELECT coach_name FROM dim_coach WHERE coach_id = 'sean_mcvay'"
    ).fetchall() == [("Sean McVay",)]
    assert con.execute(
        "SELECT n_games FROM coach_team_season WHERE coach_id = 'sean_mcvay' AND team = 'PHI'"
    ).fetchone() == (2,)
    m = {r["table_name"]: r for r in manifest}
    notes = json.loads(m["dim_coach"]["notes"])
    assert notes["merged_spellings"] == {"sean_mcvay": ["Sean  Mcvay", "Sean McVay"]}
    assert json.loads(m["coach_team_season"]["notes"])["n_team_seasons_multi_coach"] == 0


def test_table_spec_rejects_duplicate_column_names():
    with pytest.raises(ValueError, match="duplicate column names"):
        sc.Table("t", "", ("a",), (sc.Column("a", "INTEGER"), sc.Column("a", "VARCHAR")))
    # the injuries spec appends date_modified explicitly; the snapshot must not add a second one
    assert sc.tables()["fact_injury_report"].column_names.count("date_modified") == 1


# --------------------------------------------------------------------------------------
# Team abbreviations and 1999 quirks
# --------------------------------------------------------------------------------------


def test_historical_abbreviations_are_normalized_everywhere(raw, db_path):
    games = [game(2015, 4, "2015-10-04", "13:00", "KC", "SD"),
             game(2015, 4, "2015-10-04", "16:25", "STL", "OAK")]  # fmt: skip
    plays = plays_for(games)
    for p in plays:  # pbp already uses current abbreviations for the same game_id
        for col in ("posteam", "defteam", "home_team", "away_team"):
            p[col] = sc.TEAM_ALIASES.get(p[col], p[col])
    raw.write_season(2015, games, pbp=plays)
    wb.build_warehouse([2015], db_path=db_path)
    con = _open(db_path)
    assert con.execute(
        "SELECT count(*) FROM fact_game g JOIN fact_play p USING (game_id) "
        "WHERE g.home_team <> p.home_team OR g.away_team <> p.away_team"
    ).fetchone() == (0,)
    assert con.execute(
        "SELECT home_team, home_team_raw, away_team, away_team_raw FROM fact_game "
        "WHERE game_id = '2015_04_STL_OAK'"
    ).fetchone() == ("LV", "OAK", "LA", "STL")
    assert con.execute("SELECT DISTINCT team FROM coach_game ORDER BY 1").fetchall() == [
        ("KC",), ("LA",), ("LAC",), ("LV",),
    ]  # fmt: skip
    assert con.execute(
        "SELECT team, team_raw, opponent, opponent_raw FROM fact_snaps "
        "WHERE game_id = '2015_04_KC_SD'"
    ).fetchone() == ("LAC", "SD", "KC", "KC")
    assert con.execute(
        "SELECT DISTINCT team, team_raw FROM fact_injury_report ORDER BY 1"
    ).fetchall() == [("LAC", "SD"), ("LV", "OAK")]
    assert con.execute(
        "SELECT DISTINCT team, team_raw FROM fact_depth_chart ORDER BY 1"
    ).fetchall() == [("LAC", "SD"), ("LV", "OAK")]


def test_1999_shape_missing_gametime_empty_posteam_and_game_absent_from_pbp(raw, db_path):
    games = [game(1999, 1, "1999-09-12", None, "BAL", "STL"),
             game(1999, 1, "1999-09-12", None, "KC", "SD"),
             game(1999, 1, "1999-09-13", None, "MIA", "DEN")]  # fmt: skip
    plays = plays_for(games[1:])  # 1999_01_BAL_STL has no pbp
    plays[0]["posteam"] = ""
    plays[0]["defteam"] = ""
    raw.write_season(1999, games, pbp=plays)
    manifest = wb.build_warehouse([1999], db_path=db_path)
    con = _open(db_path)
    assert con.execute(
        "SELECT kickoff_utc, kickoff_is_estimated FROM fact_game WHERE game_id='1999_01_MIA_DEN'"
    ).fetchone() == (datetime(1999, 9, 14, 0, 0), True)
    assert con.execute(
        "SELECT n_games, asof_weekly_utc, n_games_after_asof FROM dim_week"
    ).fetchone() == (3, datetime(1999, 9, 14, 14, 0), 0)
    assert con.execute(
        "SELECT posteam, defteam FROM fact_play WHERE game_id = '1999_01_KC_SD' AND play_id = 1"
    ).fetchone() == (None, None)
    assert con.execute(
        "SELECT count(*) FROM fact_play WHERE game_id = '1999_01_BAL_STL'"
    ).fetchone() == (0,)
    m = {r["table_name"]: r for r in manifest}
    assert json.loads(m["fact_game"]["notes"])["n_kickoff_estimated"] == 3
    assert con.execute("SELECT n_kickoff_estimated FROM dim_week").fetchone() == (3,)
    # datasets that start later are skipped, not errors
    assert json.loads(m["fact_snaps"]["seasons_skipped"]) == [1999]
    assert json.loads(m["fact_injury_report"]["seasons_skipped"]) == [1999]
    assert json.loads(m["fact_depth_chart"]["seasons_skipped"]) == [1999]
    assert dict(wb.table_counts(db_path))["fact_snaps"] == 0


def test_2000_2005_placeholder_gametime_is_flagged_and_counted(raw, db_path):
    # nflverse records the 2000-2005 Monday/Thursday/Saturday prime-time games as '09:00'
    games = [game(2000, 1, "2000-09-03", "13:00", "CAR", "WAS"),
             game(2000, 1, "2000-09-04", "09:00", "DEN", "STL"),  # MNF, really 21:00 ET
             game(2000, 2, "2000-09-10", "16:15", "NE", "NYJ")]  # fmt: skip
    raw.write_season(2000, games)
    manifest = wb.build_warehouse([2000], db_path=db_path)
    con = _open(db_path)
    assert con.execute(
        "SELECT kickoff_utc, game_end_utc_est, kickoff_is_estimated FROM fact_game "
        "WHERE game_id = '2000_01_DEN_STL'"
    ).fetchone() == (datetime(2000, 9, 5, 0, 0), datetime(2000, 9, 5, 4, 0), True)
    assert con.execute(
        "SELECT week, n_kickoff_estimated FROM dim_week ORDER BY week"
    ).fetchall() == [(1, 1), (2, 0)]
    m = {r["table_name"]: r for r in manifest}
    assert json.loads(m["fact_game"]["notes"])["n_kickoff_estimated"] == 1


def test_null_coach_null_line_and_empty_daily_dt(raw, db_path):
    games = [
        game(2025, 1, "2025-09-04", "20:20", "DAL", "PHI", spread=None, home_coach=None),
        game(2025, 1, "2025-09-07", "13:00", "KC", "LAC", spread=-2.5, total=44.5),
    ]
    empty_dt, good = frame(
        [
            {
                "dt": "",
                "team": "PHI",
                "player_name": "A",
                "espn_id": "1",
                "gsis_id": "00-1",
                "pos_grp": "3WR 1TE",
                "pos_name": "QB",
                "pos_abb": "QB",
                "pos_slot": 1,
                "pos_rank": 1,
            },
            {
                "dt": "2025-09-08T12:00:00Z",
                "team": "PHI",
                "player_name": "A",
                "espn_id": "1",
                "gsis_id": "00-1",
                "pos_grp": "3WR 1TE",
                "pos_name": "QB",
                "pos_abb": "QB",
                "pos_slot": 1,
                "pos_rank": 1,
            },
        ],  # fmt: skip
        DAILY_DC_DTYPES,
    ).iter_slices(1)
    # A daily row without a snapshot time cannot be placed in time (B2): the build stops and
    # names the table instead of keeping a row no point-in-time reader could ever use safely.
    raw.write_season(2025, games, depth_charts=pl.concat([empty_dt, good]))
    with pytest.raises(av.AvailabilityError, match=r"fact_depth_chart: 1 row\(s\) have no"):
        wb.build_warehouse([2025], db_path=db_path)
    assert not db_path.exists()

    raw.write_season(2025, games, depth_charts=good)
    wb.build_warehouse([2025], db_path=db_path)
    con = _open(db_path)
    assert con.execute(
        "SELECT home_implied_total, away_implied_total FROM fact_game ORDER BY game_id"
    ).fetchall() == [(None, None), (21.0, 23.5)]
    # only the away coach is known for the first game -> one coach_game row for it
    assert con.execute(
        "SELECT count(*) FROM coach_game WHERE game_id = '2025_01_DAL_PHI'"
    ).fetchone() == (1,)
    assert con.execute(
        "SELECT count(*) FROM dim_coach WHERE coach_id IS NULL OR coach_id = ''"
    ).fetchone() == (0,)
    assert con.execute("SELECT dt, week_at_dt, available_at FROM fact_depth_chart").fetchall() == [
        (datetime(2025, 9, 8, 12, 0), 1, datetime(2025, 9, 8, 12, 0))
    ]


# --------------------------------------------------------------------------------------
# Depth charts
# --------------------------------------------------------------------------------------


def _legacy_row(**kw):
    base = {"season": 2019, "club_code": "OAK", "week": 17, "game_type": "REG",
            "depth_team": "1", "last_name": "Carr", "first_name": "Derek", "formation": "Offense",
            "gsis_id": "00-0031280", "position": "QB", "depth_position": "QB",
            "full_name": "Derek Carr"}  # fmt: skip
    return {**base, **kw}


def test_legacy_depth_chart_dedupe_keys_and_null_week(raw, db_path):
    games = [game(2019, 17, "2019-12-29", "16:25", "JAX", "OAK"),
             game(2019, 18, "2020-01-04", "16:35", "TEN", "NE", game_type="WC"),
             game(2019, 21, "2020-02-02", "18:30", "SF", "KC", game_type="SB")]  # fmt: skip
    rows = [
        _legacy_row(),
        _legacy_row(),  # exact duplicate
        _legacy_row(week=18),  # REG chart pulled in the Wild Card week (the real 2007-2020 shape)
        _legacy_row(week=18, game_type="WC"),  # same week, different chart -> its own row
        _legacy_row(week=None, game_type="SBBYE", depth_team="2"),  # NULL week
        _legacy_row(depth_team="2", gsis_id="00-0000002", full_name="Backup", formation="Defense"),
        _legacy_row(formation="Practice Squad"),  # unknown unit -> 'other'
        _legacy_row(club_code=None),  # NULL team: dropped as a null key, counted
        _legacy_row(formation=None, gsis_id="00-x"),  # NULL unit_raw: same
    ]
    raw.write_season(2019, games, depth_charts=frame(rows, LEGACY_DC_DTYPES))
    manifest = wb.build_warehouse([2019], db_path=db_path)
    con = _open(db_path)
    out = con.execute(
        "SELECT week, game_type, season_type, team, team_raw, unit, unit_raw, depth_rank "
        "FROM fact_depth_chart ORDER BY week NULLS LAST, game_type, unit_raw, depth_rank"
    ).fetchall()
    assert out == [
        (17, "REG", "REG", "LV", "OAK", "defense", "Defense", 2),
        (17, "REG", "REG", "LV", "OAK", "offense", "Offense", 1),
        (17, "REG", "REG", "LV", "OAK", "other", "Practice Squad", 1),
        (18, "REG", "REG", "LV", "OAK", "offense", "Offense", 1),
        (18, "WC", "POST", "LV", "OAK", "offense", "Offense", 1),
        (None, "SBBYE", "POST", "LV", "OAK", "offense", "Offense", 2),
    ]
    m = {r["table_name"]: r for r in manifest}["fact_depth_chart"]
    assert m["n_dropped_duplicates"] == 1 and json.loads(m["notes"])["n_unit_other"] == 1
    assert m["n_dropped_null_key"] == 2
    assert _types(con, "fact_depth_chart")["depth_rank"] == "INTEGER"


def test_legacy_depth_team_that_is_not_an_integer_aborts_the_build(raw, db_path):
    # Deliberately strict: a legacy depth_team such as '' is a data surprise the owner should
    # see, not a silently NULL depth_rank. Revisit (TRY_CAST + a manifest count) if the
    # 2001-2024 files turn out to contain such values.
    games = [game(2019, 17, "2019-12-29", "16:25", "JAX", "OAK")]
    raw.write_season(
        2019, games, depth_charts=frame([_legacy_row(depth_team="")], LEGACY_DC_DTYPES)
    )
    with pytest.raises(duckdb.Error, match=r"(?i)conversion|could not convert"):
        wb.build_warehouse([2019], db_path=db_path)
    assert not db_path.exists()
    assert not db_path.with_name(db_path.name + ".building").exists()


def test_daily_depth_chart_keys_null_ids_and_per_team_dt(raw, db_path):
    games = [game(2026, 1, "2026-09-10", "20:20", "DAL", "PHI")]
    base = {"team": "PHI", "player_name": "A. Brown", "espn_id": "1", "gsis_id": "00-0000009",
            "pos_grp": "3WR 1TE", "pos_name": "Wide Receiver", "pos_abb": "WR", "pos_slot": 1,
            "pos_rank": 1}  # fmt: skip
    rows = [
        {**base, "dt": "2026-09-08T07:15:07Z"},
        {**base, "dt": "2026-09-08T07:15:07Z", "pos_grp": "Special Teams", "pos_abb": "KR"},
        {
            **base,
            "dt": "2026-09-08T07:15:07Z",
            "pos_slot": 2,
            "gsis_id": None,
            "player_name": None,
        },  # fmt: skip
        {
            **base,
            "dt": "2026-09-08T09:40:00Z",
            "team": "DAL",
            "pos_grp": "Base 4-3 D",
            "pos_abb": "LDE",
        },  # fmt: skip
    ]
    raw.write_season(2026, games, depth_charts=frame(rows, DAILY_DC_DTYPES))
    wb.build_warehouse([2026], db_path=db_path)
    con = _open(db_path)
    out = con.execute(
        "SELECT dt, team, unit, position, pos_slot, gsis_id, week_at_dt FROM fact_depth_chart "
        "ORDER BY dt, team, unit, position, pos_slot"
    ).fetchall()
    assert out == [
        (datetime(2026, 9, 8, 7, 15, 7), "PHI", "offense", "WR", 1, "00-0000009", 1),
        (datetime(2026, 9, 8, 7, 15, 7), "PHI", "offense", "WR", 2, None, 1),
        (datetime(2026, 9, 8, 7, 15, 7), "PHI", "special_teams", "KR", 1, "00-0000009", 1),
        (datetime(2026, 9, 8, 9, 40), "DAL", "defense", "LDE", 1, "00-0000009", 1),
    ]
    assert con.execute("SELECT DISTINCT week, game_type FROM fact_depth_chart").fetchall() == [
        (None, None)
    ]


def test_daily_week_at_dt_boundaries(raw, db_path):
    games = season_2025_games()  # W1 as-of 2025-09-09T14:00Z, SB (W8) as-of 2025-11-04T14:00Z
    base = {"team": "PHI", "player_name": "A. Brown", "espn_id": "1", "gsis_id": "00-0000009",
            "pos_grp": "3WR 1TE", "pos_name": "Wide Receiver", "pos_abb": "WR", "pos_slot": 1,
            "pos_rank": 1}  # fmt: skip
    dts = ["2025-08-01T00:00:00Z", "2025-09-09T14:00:00Z", "2025-09-09T14:00:01Z",
           "2025-11-04T14:00:00Z", "2025-11-04T14:00:01Z"]  # fmt: skip
    raw.write_season(
        2025, games, depth_charts=frame([{**base, "dt": d} for d in dts], DAILY_DC_DTYPES)
    )
    wb.build_warehouse([2025], db_path=db_path)
    con = _open(db_path)
    out = con.execute("SELECT dt, week_at_dt FROM fact_depth_chart ORDER BY dt").fetchall()
    # preseason -> W1; exactly at the as-of is inclusive; +1 s -> next week; SB as-of -> 8;
    # after the season's last as-of -> NULL
    assert [w for _, w in out] == [1, 1, 2, 8, None]
    # the SQL join on dt matched every distinct snapshot time (no row lost its week)
    dim = con.execute("SELECT season, week, asof_weekly_utc FROM dim_week").pl().to_dicts()
    assert [wk.week_for_timestamp(dim, 2025, dt) for dt, _ in out] == [w for _, w in out]


# --------------------------------------------------------------------------------------
# Injuries and player stats
# --------------------------------------------------------------------------------------


def _injury(season, week, team, gsis, status, dm=None, *, dm_column=True, **kw):
    row = {"season": season, "game_type": "REG", "team": team, "week": week, "gsis_id": gsis,
           "position": "TE", "full_name": "Player", "report_status": status,
           "practice_status": None, **kw}  # fmt: skip
    if dm_column:
        row["date_modified"] = dm
    return row


def test_injuries_type_drift_dedupe_and_date_modified(raw, db_path):
    dm_dtype = pl.Datetime("us", "UTC")
    old = {**INJURY_DTYPES, "season": pl.Float64(), "week": pl.Float64(), "date_modified": dm_dtype}
    old.pop("season_type")
    df_2009 = frame([_injury(2009.0, 1.0, "STL", "00-0000001", "Out")], old)
    mid = {**INJURY_DTYPES, "date_modified": dm_dtype}
    mid.pop("season_type")
    df_2024 = frame(
        [
            _injury(
                2024,
                15,
                "HOU",
                "00-0039359",
                "Questionable",
                datetime(2024, 12, 15, 3, 34, tzinfo=UTC),
            ),
            _injury(
                2024, 15, "HOU", "00-0039359", "Out", datetime(2024, 12, 15, 14, 17, tzinfo=UTC)
            ),
            _injury(
                2024,
                19,
                "HOU",
                "00-0039359",
                "Out",
                datetime(2025, 1, 10, 14, 17, tzinfo=UTC),
                game_type="WC",
            ),
        ],  # fmt: skip
        mid,
    )
    df_2026 = frame([_injury(2026, 1, "KC", "00-0000002", None, dm_column=False,
                             season_type="REG")], INJURY_DTYPES)  # fmt: skip
    raw.write_season(2009, [game(2009, 1, "2009-09-13", "13:00", "SF", "STL")], injuries=df_2009)
    raw.write_season(2024, [game(2024, 15, "2024-12-15", "13:00", "MIA", "HOU"),
                            game(2024, 19, "2025-01-11", "16:30", "LAC", "HOU", game_type="WC")],
                     injuries=df_2024)  # fmt: skip
    raw.write_season(2026, [game(2026, 1, "2026-09-13", "13:00", "LV", "KC")], injuries=df_2026)

    manifest = wb.build_warehouse([2026, 2009, 2024], db_path=db_path)
    con = _open(db_path)
    t = _types(con, "fact_injury_report")
    assert (t["season"], t["week"], t["date_modified"]) == ("INTEGER", "INTEGER", "TIMESTAMP")
    assert con.execute(
        "SELECT season, week, team, team_raw, season_type, report_status, date_modified "
        "FROM fact_injury_report ORDER BY season, week"
    ).fetchall() == [
        (2009, 1, "LA", "STL", "REG", "Out", None),
        (2024, 15, "HOU", "HOU", "REG", "Out", datetime(2024, 12, 15, 14, 17)),
        (2024, 19, "HOU", "HOU", "POST", "Out", datetime(2025, 1, 10, 14, 17)),
        (2026, 1, "KC", "KC", "REG", None, None),
    ]
    m = {r["table_name"]: r for r in manifest}["fact_injury_report"]
    assert m["n_dropped_duplicates"] == 1 and m["seasons"] == "[2009, 2024, 2026]"
    assert "date_modified" not in json.loads(m["notes"]).get("null_columns", [])
    h_inj = m["content_hash"]

    # building only the modern season gives the same column types and reports the drift
    con.close()
    manifest2 = wb.build_warehouse([2026], db_path=db_path)
    con2 = _open(db_path)
    assert _types(con2, "fact_injury_report") == t
    m26 = {r["table_name"]: r for r in manifest2}["fact_injury_report"]
    assert "date_modified" in json.loads(m26["notes"])["null_columns"]

    # the dedupe tie-break does not depend on the physical row order of the source file
    con2.close()
    raw.write("injuries", 2024, df_2024.reverse())
    manifest3 = wb.build_warehouse([2026, 2009, 2024], db_path=db_path)
    assert {r["table_name"]: r for r in manifest3}["fact_injury_report"]["content_hash"] == h_inj


def test_injury_duplicate_without_date_modified_resolves_deterministically(raw, db_path):
    rows = [
        _injury(2025, 1, "PHI", "00-0000002", "Questionable", dm_column=False, season_type="REG"),
        _injury(2025, 1, "PHI", "00-0000002", "Out", dm_column=False, season_type="REG"),
    ]
    raw.write_season(
        2025,
        [game(2025, 1, "2025-09-04", "20:20", "DAL", "PHI")],
        injuries=frame(rows, INJURY_DTYPES),
    )
    manifest = wb.build_warehouse([2025], db_path=db_path)
    con = _open(db_path)
    # Both date_modified are NULL (every 2025+ file), so the tie-break is the alphabetical
    # order of the remaining columns in spec order; here report_status 'Out' < 'Questionable'.
    # Arbitrary but deterministic; change it deliberately, not by reordering the snapshot.
    assert con.execute("SELECT report_status FROM fact_injury_report").fetchall() == [("Out",)]
    m = {r["table_name"]: r for r in manifest}["fact_injury_report"]
    assert m["n_dropped_duplicates"] == 1


def test_player_stats_null_player_id_dropped_and_counted(raw, db_path):
    games = [game(2026, 1, "2026-09-13", "13:00", "LV", "KC")]
    stats = [
        {
            "player_id": "00-0000001",
            "season": 2026,
            "week": 1,
            "season_type": "REG",
            "game_id": "2026_01_LV_KC",
            "team": "KC",
        },  # fmt: skip
        {
            "player_id": None,
            "season": 2026,
            "week": 1,
            "season_type": "REG",
            "game_id": "2026_01_LV_KC",
            "team": "KC",
            "fantasy_points_ppr": 0.0,
        },  # fmt: skip
    ]
    raw.write_season(2026, games, player_stats=stats)
    manifest = wb.build_warehouse([2026], db_path=db_path)
    m = {r["table_name"]: r for r in manifest}["fact_player_week"]
    assert (m["n_rows"], m["n_source_rows"], m["n_dropped_null_key"]) == (1, 2, 1)


def test_fact_play_integer_casts_and_refuse_nan(raw, db_path):
    games = [game(2025, 1, "2025-09-04", "20:20", "DAL", "PHI")]
    plays = plays_for(games)
    # the real nflverse case: Float64 columns holding integral values
    plays[0]["down"] = 2.0
    plays[1]["down"] = 3.0
    plays[1]["yards_gained"] = 12.0
    raw.write_season(2025, games, pbp=plays)
    wb.build_warehouse([2025], db_path=db_path)
    con = _open(db_path)
    t = _types(con, "fact_play")
    assert (t["down"], t["yards_gained"], t["air_yards"], t["kick_distance"]) == ("INTEGER",) * 4
    assert con.execute(
        "SELECT down, yards_gained, air_yards, kick_distance FROM fact_play ORDER BY play_id"
    ).fetchall() == [(2, 4, 7, None), (3, 12, 7, None)]
    con.close()
    # NaN in an INTEGER column is a data surprise: the build stops instead of guessing
    plays[0]["yards_gained"] = float("nan")
    raw.write("pbp", 2025, frame(plays, PBP_DTYPES))
    with pytest.raises(duckdb.Error, match=r"(?i)conversion|could not convert"):
        wb.build_warehouse([2025], db_path=db_path)
    assert dict(wb.table_counts(db_path))["fact_play"] == 2  # previous file untouched
    # a fractional value in an INTEGER column stops the build too (DuckDB would otherwise
    # round half to even without a word); the message names the column and the value
    plays[0]["yards_gained"] = 4.0
    plays[1]["air_yards"] = 7.5
    raw.write("pbp", 2025, frame(plays, PBP_DTYPES))
    with pytest.raises(duckdb.Error, match=r"non-integral value in air_yards: 7\.5"):
        wb.build_warehouse([2025], db_path=db_path)
    assert dict(wb.table_counts(db_path))["fact_play"] == 2


# --------------------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------------------


def test_build_is_deterministic_and_sensitive_to_data(raw, db_path, tmp_path):
    games = season_2025_games()
    raw.write_season(2025, games)
    raw.write_season(2024, [game(2024, 18, "2025-01-05", "13:00", "MIA", "NYJ", result=3)])
    h1 = _hashes(wb.build_warehouse([2025, 2024], db_path=db_path))
    h2 = _hashes(wb.build_warehouse([2024, 2025], db_path=tmp_path / "other.duckdb"))
    assert h1 == h2

    # dim_week windows and prev/next never cross a season boundary, although 2024's week 18
    # (gameday 2025-01-05) falls inside 2025's calendar year
    con = _open(db_path)
    rows = con.execute(
        "SELECT season, week, prev_week, next_week, window_start_utc FROM dim_week "
        "WHERE (season, week) IN ((2024, 18), (2025, 1)) ORDER BY 1, 2"
    ).fetchall()
    assert rows[0][:4] == (2024, 18, None, None)
    assert rows[1] == (2025, 1, None, 2, None)
    dim = con.execute("SELECT season, week, asof_weekly_utc FROM dim_week").pl().to_dicts()
    con.close()
    assert wk.week_for_timestamp(dim, 2024, datetime(2025, 1, 8)) is None
    assert wk.week_for_timestamp(dim, 2025, datetime(2025, 1, 8)) == 1

    # the same rows in a different physical order hash identically (order-free content_hash)
    plays = plays_for(games)
    random.Random(1).shuffle(plays)
    raw.write("pbp", 2025, frame(plays, PBP_DTYPES))
    h_shuffled = _hashes(wb.build_warehouse([2025, 2024], db_path=tmp_path / "shuffled.duckdb"))
    assert h_shuffled == h1

    con = _open(db_path)
    types_two = {t: _types(con, t) for t in sc.tables()}
    con.close()
    wb.build_warehouse([2025], db_path=db_path)
    con = _open(db_path)
    assert {t: _types(con, t) for t in sc.tables()} == types_two  # subset: same types
    con.close()

    plays = plays_for(games)
    plays[0]["epa"] = plays[0]["epa"] + 1e-9
    raw.write("pbp", 2025, frame(plays, PBP_DTYPES))
    h3 = _hashes(wb.build_warehouse([2025, 2024], db_path=db_path))
    assert h3["fact_play"] != h1["fact_play"]
    assert {k: v for k, v in h3.items() if k != "fact_play"} == {
        k: v for k, v in h1.items() if k != "fact_play"
    }


def test_build_does_not_depend_on_session_timezone(raw, db_path, tmp_path, monkeypatch):
    raw.write_season(2025, season_2025_games())
    dm = pl.Datetime("us", "UTC")
    when = datetime(2025, 9, 6, 20, 5, tzinfo=UTC)
    inj = frame([_injury(2025, 1, "PHI", "00-0000002", "Out", when)],
                {**INJURY_DTYPES, "date_modified": dm})  # fmt: skip
    raw.write("injuries", 2025, inj)
    h_utc = _hashes(wb.build_warehouse([2025], db_path=db_path))
    con = _open(db_path)
    kick_utc = con.execute("SELECT kickoff_utc FROM fact_game ORDER BY 1").fetchall()
    con.close()

    monkeypatch.setattr(wb, "SESSION_TIMEZONE", "America/New_York")
    other = tmp_path / "ny.duckdb"
    h_ny = _hashes(wb.build_warehouse([2025], db_path=other))
    con = _open(other)
    assert h_ny == h_utc
    assert con.execute("SELECT kickoff_utc FROM fact_game ORDER BY 1").fetchall() == kick_utc
    assert con.execute("SELECT date_modified FROM fact_injury_report").fetchone() == (
        datetime(2025, 9, 6, 20, 5),
    )


# --------------------------------------------------------------------------------------
# Cache handling
# --------------------------------------------------------------------------------------


def test_missing_cache_lists_every_missing_path(raw, db_path):
    raw.write_season(2025, season_2025_games())
    os.remove(nv.cache_path("pbp", 2025))
    os.remove(nv.cache_path("injuries", 2025))
    os.remove(nv.cache_path("players", None))  # the one-file (all.parquet) branch
    with pytest.raises(wb.MissingCacheError) as e:
        wb.build_warehouse([2025, 2005], db_path=db_path)
    msg = str(e.value)
    assert "pbp/2025.parquet" in msg and "injuries/2025.parquet" in msg
    assert "players/all.parquet" in msg
    assert "snap_counts/2005.parquet" not in msg  # before first_season -> skipped, not missing
    assert "schedules/2005.parquet" in msg
    assert not db_path.exists()


def test_build_never_calls_the_loader_even_for_stale_current_season(raw, db_path, monkeypatch):
    raw.write_season(2026, [game(2026, 1, "2026-09-13", "13:00", "LV", "KC")])
    stale = time.time() - 3 * 24 * 3600
    for name in ("schedules", "pbp", "depth_charts"):
        os.utime(nv.cache_path(name, 2026), (stale, stale))
    calls = []
    monkeypatch.setattr(nv, "fetch", lambda *a, **k: calls.append(a) or pytest.fail("fetch"))
    wb.build_warehouse([2026], db_path=db_path)  # the raw fixture's _loader would raise too
    assert calls == []
    assert dict(wb.table_counts(db_path))["fact_game"] == 1


# --------------------------------------------------------------------------------------
# Real data (opt-in: `uv run pytest -m realdata`; never downloads, never the default path)
# --------------------------------------------------------------------------------------


@pytest.mark.realdata
def test_real_2025_season_builds_into_a_scratch_file(tmp_path):
    if not nv.cache_path("schedules", 2025).exists():
        pytest.skip("real cache not present (run `twm ingest`)")
    manifest = wb.build_warehouse([2025], db_path=tmp_path / "real.duckdb")
    m = {r["table_name"]: r for r in manifest}
    con = _open(tmp_path / "real.duckdb")
    assert con.execute(
        "SELECT asof_weekly_utc FROM dim_week WHERE season = 2025 AND week = 1"
    ).fetchone() == (datetime(2025, 9, 9, 14, 0),)
    assert con.execute(
        "SELECT count(*) FROM dim_week WHERE season = 2025 AND season_type = 'REG'"
    ).fetchone() == (18,)
    assert "n_unit_other" not in json.loads(m["fact_depth_chart"]["notes"])
    t = _types(con, "fact_play")
    assert all(t[c] == "INTEGER" for c in sc.FACT_PLAY_INTEGER)
    assert json.loads(m["fact_game"]["notes"])["n_kickoff_estimated"] == 0


@pytest.mark.realdata
def test_real_schedules_placeholder_kickoffs_are_exactly_the_2000_2005_rows():
    if not nv.cache_path("schedules", 2006).exists():
        pytest.skip("real cache not present (run `twm ingest`)")
    per_season = {}
    for season in range(1999, 2007):
        df = pl.read_parquet(
            nv.cache_path("schedules", season), columns=["gameday", "gametime", "weekday"]
        )
        per_season[season] = sum(wk.kickoff_utc(d, t, w)[1] for d, t, w in df.iter_rows())
    assert per_season == {1999: 259, 2000: 17, 2001: 17, 2002: 17, 2003: 17, 2004: 17,
                          2005: 17, 2006: 0}  # fmt: skip


def test_symlinked_warehouse_is_rebuilt_at_its_target(raw, tmp_path):
    """The owner keeps data outside iCloud via symlinks; a rebuild must keep the link intact."""
    raw.write_season(2025, season_2025_games())
    target_dir = tmp_path / "elsewhere"
    target_dir.mkdir()
    target = target_dir / "warehouse.duckdb"
    link = tmp_path / "warehouse.duckdb"
    link.symlink_to(target)
    (tmp_path / "warehouse.duckdb.wal").write_bytes(b"stale")  # WAL left under the link name

    wb.build_warehouse([2025], db_path=link)
    wb.build_warehouse([2025], db_path=link)

    assert link.is_symlink() and link.resolve() == target.resolve()
    assert target.is_file()
    assert not (tmp_path / "warehouse.duckdb.wal").exists()
    assert not list(tmp_path.glob("*.building*")) and not list(target_dir.glob("*.building*"))
    assert dict(wb.table_counts(link))["fact_game"] > 0
