"""The available_at rules (B2): one test per rule, the registry, the build guards.

Each test builds a tiny synthetic warehouse (tests/conftest.py) and checks when rows become
visible. "Visible at t" always means ``available_at <= t`` (inclusive).
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime, time, timedelta

import duckdb
import polars as pl
import pytest

from tests.conftest import (
    DAILY_DC_DTYPES,
    INJURY_DM_DTYPES,
    INJURY_DTYPES,
    LEGACY_DC_DTYPES,
    draft_class_players,
    frame,
    game,
    injury,
    player_week,
    season_2015_draft_games,
    season_2020_split_games,
    season_2025_games,
)
from twm.asof import AsOfView, weekly_as_of
from twm.warehouse import available as av
from twm.warehouse import build as wb
from twm.warehouse import schema as sc


def _open(db_path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(db_path), read_only=True)
    con.execute("SET TimeZone='UTC'")
    return con


def _one(db_path, sql, params=None):
    con = _open(db_path)
    try:
        return con.execute(sql, params or []).fetchall()
    finally:
        con.close()


def _notes(manifest, table):
    return json.loads({m["table_name"]: m for m in manifest}[table]["notes"])


def _counts(manifest, table, column="available_at"):
    """The rule branches that placed at least one row (the manifest also lists the zeros)."""
    counts = _notes(manifest, table)[f"{column}_rule_counts"]
    return {b: n for b, n in counts.items() if n}


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


# --------------------------------------------------------------------------------------
# Registry and build guards
# --------------------------------------------------------------------------------------


def test_registry_covers_every_table_and_event_tables_have_available_at(raw, db_path):
    assert "fact_schedule" in sc.tables()
    assert set(av.TABLE_AVAILABILITY) == set(sc.tables()) | {"build_manifest"}
    raw.write_season(2025, season_2025_games())
    wb.build_warehouse([2025], db_path=db_path)
    con = _open(db_path)
    types = {
        (t, c): d
        for t, c, d in con.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns"
        ).fetchall()
    }
    built = {t for t, _ in types}
    assert built == set(av.TABLE_AVAILABILITY)
    for name, a in av.TABLE_AVAILABILITY.items():
        assert a.rule and a.kind in av.KINDS, name
        for extra in a.extra_columns:  # helper time columns are stored and TIMESTAMP
            assert types[(name, extra)] == "TIMESTAMP", (name, extra)
        if a.kind == "event":
            assert types[(name, "available_at")] == "TIMESTAMP", name
            n_null = con.execute(f"SELECT count(*) FROM {name} WHERE available_at IS NULL")
            assert n_null.fetchone() == (0,), name
            # the rule is stored in the file next to the column
            comment = con.execute(
                "SELECT comment FROM duckdb_columns() WHERE table_name = ? "
                "AND column_name = 'available_at'",
                [name],
            ).fetchone()[0]
            assert a.rule in comment
        else:
            assert (name, "available_at") not in types, name
    # hindsight: the allowlist and the hidden columns split dim_player exactly
    player = av.TABLE_AVAILABILITY["dim_player"]
    cols = set(sc.tables()["dim_player"].column_names)
    assert set(player.visible_columns) | set(player.hidden_columns) == cols
    assert not set(player.visible_columns) & set(player.hidden_columns)
    assert set(player.hidden_columns) == {
        "last_season", "status", "latest_team", "rookie_season", "position", "position_group",
        "height", "weight",
    }  # fmt: skip
    assert player.row_visible_column == av.PUBLIC_FROM
    # event tables: today's-snapshot columns are registered and exist
    for name, a in av.TABLE_AVAILABILITY.items():
        assert set(a.hindsight_columns) <= set(sc.tables().get(name, sc.DIM_WEEK).column_names)
        assert set(a.masked_columns) <= {c for t, c in types if t == name}, name
    assert set(av.TABLE_AVAILABILITY["fact_player_week"].hindsight_columns) == {
        "position",
        "position_group",
        "headshot_url",
    }
    assert av.TABLE_AVAILABILITY["build_manifest"].kind == "meta"


def test_manifest_records_rule_branches_and_rows_after_their_week_asof(raw, db_path):
    raw.write_season(2025, season_2025_games())
    manifest = wb.build_warehouse([2025], db_path=db_path)
    game_notes = _notes(manifest, "fact_game")
    # every branch is recorded, zeros included, so "0" and "not computed" differ
    assert game_notes["available_at_rule_counts"] == {
        "estimated_kickoff_night_slot": 0, "game_end": 14,
    }  # fmt: skip
    # the W3 Tuesday-evening game (2025_03_SEA_DAL) ends after W3's Tuesday as-of
    assert game_notes["n_available_after_week_asof"] == 1
    assert _notes(manifest, "fact_play")["n_available_after_week_asof"] == 2  # its two plays
    assert _counts(manifest, "fact_schedule") == {
        "reg_schedule_release": 9,
        "post_previous_round_end": 5,
    }
    inj = _notes(manifest, "fact_injury_report")
    assert inj["available_at_rule_counts"] == {
        "date_modified": 0, "date_modified_legacy_shifted": 0, "kickoff": 14,
        "week_asof_no_game": 0, "unplaced": 0,
    }  # fmt: skip
    assert inj["n_raised_to_after_previous_week_asof"] == 0
    assert _counts(manifest, "fact_depth_chart") == {"daily_dt": 14}
    assert _notes(manifest, "fact_depth_chart")["available_at_rule_counts"]["legacy_unmapped"] == 0
    # both fixture players are undrafted and appear through their first data row
    assert _notes(manifest, "dim_player")["public_from_utc_rule_counts"] == {
        "draft": 0, "first_fact_row": 2, "never": 0,
    }  # fmt: skip


def test_build_fails_loudly_when_a_legacy_chart_cannot_be_placed_in_time(raw, db_path):
    games = [game(2019, 17, "2019-12-29", "16:25", "JAX", "OAK")]
    rows = [_legacy(17, "REG"), _legacy(17, "WC")]  # a WC chart in a week with no WC games
    raw.write_season(2019, games, depth_charts=frame(rows, LEGACY_DC_DTYPES))
    with pytest.raises(
        av.AvailabilityError, match=r"fact_depth_chart: 1 row\(s\) have no available_at.*'WC'"
    ):
        wb.build_warehouse([2019], db_path=db_path)
    assert not db_path.exists()  # rolled back, nothing written


def test_build_fails_when_a_stat_row_has_no_game(raw, db_path):
    games = [game(2025, 1, "2025-09-04", "20:20", "DAL", "PHI")]
    snaps = [{"game_id": "2025_01_XXX_YYY", "season": 2025, "game_type": "REG", "week": 1,
              "player": "A", "pfr_player_id": "AAAA00", "team": "PHI"}]  # fmt: skip
    raw.write_season(2025, games, snaps=snaps)
    with pytest.raises(av.AvailabilityError, match=r"fact_snaps: 1 row\(s\)"):
        wb.build_warehouse([2025], db_path=db_path)


def test_available_at_is_deterministic(raw, db_path, tmp_path):
    raw.write_season(2025, season_2025_games())
    raw.write_season(2020, season_2020_split_games())
    h1 = {m["table_name"]: m["content_hash"] for m in wb.build_warehouse([2020, 2025], db_path)}
    h2 = {
        m["table_name"]: m["content_hash"]
        for m in wb.build_warehouse([2025, 2020], tmp_path / "again.duckdb")
    }
    assert h1 == h2


def test_rules_come_from_config_and_change_the_result(raw, db_path, tmp_path, monkeypatch):
    rules = av.AvailabilityRules.from_settings()
    # the shipped config equals the documented defaults (plus the curated schedule changes)
    assert dataclasses.replace(rules, schedule_exceptions=()) == av.AvailabilityRules()
    assert len(rules.schedule_exceptions) > 20
    assert [x.game_id for x in rules.cancelled_games()] == ["2022_17_BUF_CIN"]
    raw.write_season(2025, season_2025_games())
    wb.build_warehouse([2025], db_path=db_path)
    q = "SELECT available_at FROM fact_play WHERE game_id = '2025_01_MIN_CHI' AND play_id = 1"
    assert _one(db_path, q) == [(datetime(2025, 9, 9, 10, 15),)]  # end 04:15 + 6 h

    def with_pbp_lag(hours):
        lag = {**rules.game_data_lag, "pbp": timedelta(hours=hours)}
        monkeypatch.setattr(
            av.AvailabilityRules, "from_settings", classmethod(lambda cls: cls(game_data_lag=lag))
        )

    with_pbp_lag(7)
    other = tmp_path / "lag7.duckdb"
    wb.build_warehouse([2025], db_path=other)
    assert _one(other, q) == [(datetime(2025, 9, 9, 11, 15),)]
    # 12 h would push Monday night's plays past the Tuesday 14:00 as-of: the build refuses
    with_pbp_lag(12)
    with pytest.raises(av.AvailabilityError, match="fact_play: 2 row.*miss their Tuesday as-of"):
        wb.build_warehouse([2025], db_path=tmp_path / "lag12.duckdb")


# --------------------------------------------------------------------------------------
# Game data: fact_game, fact_play, fact_player_week, fact_team_week, fact_snaps
# --------------------------------------------------------------------------------------


def _per_game_player_stats(games):
    """One player row per game (the default fixture has one per WEEK, the Thursday game)."""
    return [player_week(g, f"p_{g['game_id']}", 1) for g in games]


@pytest.mark.parametrize(("table", "dataset"), list(av.GAME_DATA_TABLES.items()))
def test_game_data_rows_are_their_games_end_plus_the_lag(raw, db_path, table, dataset):
    """Pins the rule for every game-data table: an early (leaky) available_at fails here."""
    games = season_2025_games()
    raw.write_season(2025, games, player_stats=_per_game_player_stats(games))
    wb.build_warehouse([2025], db_path=db_path)
    lag = av.AvailabilityRules().game_data_lag[dataset]
    n_games, n_wrong, n_rows = _one(
        db_path,
        f"""SELECT count(DISTINCT x.game_id),
                   count(*) FILTER (WHERE x.available_at <> g.availability_game_end_utc
                                    + to_microseconds(CAST(? AS BIGINT))),
                   count(*)
            FROM {table} x JOIN fact_game g USING (game_id)""",
        [int(lag.total_seconds() * 1_000_000)],
    )[0]
    assert n_rows > 0 and n_wrong == 0
    assert n_games == len(games)  # every game has rows, so the join cannot pass on nothing


def test_monday_night_rows_are_visible_at_the_tuesday_asof(raw, db_path):
    games = season_2025_games()
    raw.write_season(2025, games, player_stats=_per_game_player_stats(games))
    wb.build_warehouse([2025], db_path=db_path)
    asof = weekly_as_of(db_path, 2025, 1)
    assert asof == _utc(2025, 9, 9, 14)
    with AsOfView(db_path, asof) as v:
        # MIN at CHI, Monday 20:15 ET = 00:15 UTC, ends ~04:15, published by 10:15 UTC
        for t in ("fact_play", "fact_player_week", "fact_team_week", "fact_snaps", "fact_game"):
            assert "2025_01_MIN_CHI" in set(v.table(t, ["game_id"])["game_id"]), t
            visible = v.sql(f"SELECT DISTINCT week FROM {t} ORDER BY 1")["week"].to_list()
            assert visible == [1], t  # and nothing of week 2
        assert v.sql("SELECT count(*) AS n FROM fact_game").item() == 4
    # the final score waits game_result_lag_hours (3 h) after the estimated end
    assert _one(
        db_path,
        "SELECT available_at, game_end_utc_est FROM fact_game WHERE game_id = '2025_01_MIN_CHI'",
    ) == [(datetime(2025, 9, 9, 7, 15), datetime(2025, 9, 9, 4, 15))]


def test_final_score_waits_for_the_result_lag(raw, db_path):
    """Weather delays and overtime run past kickoff + 4 h: the score is hidden until + 3 h."""
    raw.write_season(2025, season_2025_games())
    wb.build_warehouse([2025], db_path=db_path)
    end = _utc(2025, 9, 9, 4, 15)  # MIN at CHI, kickoff + 4 h
    for when, visible in ((end + timedelta(minutes=1), False),
                          (end + timedelta(hours=3) - timedelta(seconds=1), False),
                          (end + timedelta(hours=3), True)):  # fmt: skip
        with AsOfView(db_path, when) as v:
            ids = set(v.table("fact_game", ["game_id"])["game_id"])
        assert ("2025_01_MIN_CHI" in ids) is visible, when


def test_split_week_wednesday_game_is_hidden_until_the_next_asof(raw, db_path):
    raw.write_season(2020, season_2020_split_games())
    manifest = wb.build_warehouse([2020], db_path=db_path)
    w12, w13 = weekly_as_of(db_path, 2020, 12), weekly_as_of(db_path, 2020, 13)
    assert w12 == _utc(2020, 12, 1, 14)
    moved = "2020_12_BAL_PIT"
    for t in ("fact_play", "fact_team_week", "fact_snaps", "fact_game", "coach_game"):
        with AsOfView(db_path, w12) as v:
            ids = set(v.table(t, ["game_id"])["game_id"])
        assert moved not in ids and "2020_12_SEA_PHI" in ids, t  # Monday night is in
        with AsOfView(db_path, w13) as v:
            assert moved in set(v.table(t, ["game_id"])["game_id"]), t
    # its available_at is the game's own end + lag, never a per-week constant
    assert _one(db_path, f"SELECT available_at FROM fact_play WHERE game_id = '{moved}' LIMIT 1")[
        0
    ] == (datetime(2020, 12, 3, 6, 40),)
    assert _notes(manifest, "fact_game")["n_available_after_week_asof"] == 1


def test_estimated_kickoffs_err_late(raw, db_path):
    games = [
        game(1999, 1, "1999-09-12", None, "KC", "SD"),  # Sunday, guessed 13:00 ET
        game(1999, 1, "1999-09-13", None, "MIA", "DEN"),  # Monday, guessed 20:00 ET
    ]
    raw.write_season(1999, games)
    manifest = wb.build_warehouse([1999], db_path=db_path)
    rows = _one(
        db_path,
        "SELECT game_id, game_end_utc_est, availability_game_end_utc, available_at "
        "FROM fact_game ORDER BY game_id",
    )
    assert rows == [
        # Sunday night slot: 20:30 ET (00:30 UTC next day, EDT) + 4 h; score + 3 h
        ("1999_01_KC_SD", datetime(1999, 9, 12, 21), datetime(1999, 9, 13, 4, 30),
         datetime(1999, 9, 13, 7, 30)),
        # Monday night kicked off at 21:00 ET in 1999-2005: 01:00 UTC Tuesday (EDT) + 4 h
        ("1999_01_MIA_DEN", datetime(1999, 9, 14, 4), datetime(1999, 9, 14, 5),
         datetime(1999, 9, 14, 8)),
    ]  # fmt: skip
    assert _counts(manifest, "fact_game") == {"estimated_kickoff_night_slot": 2}
    # still inside the Tuesday as-of (14:00 UTC) with the 6 h lag
    assert _one(db_path, "SELECT max(available_at) FROM fact_play") == [
        (datetime(1999, 9, 14, 11),)
    ]
    # coach_game uses the same late kickoff (availability end - 4 h)
    assert _one(
        db_path, "SELECT DISTINCT available_at FROM coach_game WHERE game_id = '1999_01_KC_SD'"
    ) == [(datetime(1999, 9, 13, 0, 30),)]
    assert _one(
        db_path, "SELECT DISTINCT available_at FROM coach_game WHERE game_id = '1999_01_MIA_DEN'"
    ) == [(datetime(1999, 9, 14, 1),)]


def test_availability_game_end_never_earlier_than_the_estimate():
    rules = av.AvailabilityRules()
    real = datetime(2025, 9, 9, 4, 15)
    assert av.availability_game_end("2025-09-08", real, False, rules) == real
    # a guessed Saturday kickoff at 16:30 ET: 20:30 ET is later
    guess = datetime(2003, 12, 20, 21, 30) + timedelta(hours=4)
    assert av.availability_game_end("2003-12-20", guess, True, rules) == datetime(
        2003, 12, 21, 5, 30
    )
    # a config that would move it earlier is ignored (max)
    early = av.AvailabilityRules(estimated_kickoff_et={"default": time(10)})
    assert av.availability_game_end("2003-12-20", guess, True, early) == guess


def test_guessed_monday_night_kickoff_is_21_00_eastern():
    """1999-2005 Monday-night games kicked off at 21:00 ET: the availability end is 21:00 ET
    + 4 h (01:00 ET), in daylight time (September) and standard time (November)."""
    rules = av.AvailabilityRules()
    sept_guess = datetime(1999, 9, 14, 0) + timedelta(hours=4)  # the 20:00 ET weekday guess
    assert av.availability_game_end("1999-09-13", sept_guess, True, rules) == datetime(
        1999, 9, 14, 5
    )
    nov_guess = datetime(1999, 11, 16, 1) + timedelta(hours=4)
    assert av.availability_game_end("1999-11-15", nov_guess, True, rules) == datetime(
        1999, 11, 16, 6
    )
    # a 1999 Sunday game still uses the 20:30 ET default night slot
    sunday_guess = datetime(1999, 9, 12, 17) + timedelta(hours=4)
    assert av.availability_game_end("1999-09-12", sunday_guess, True, rules) == datetime(
        1999, 9, 13, 4, 30
    )


def test_end_of_season_asof_sees_the_finale_play_by_play(raw, db_path):
    raw.write_season(2025, season_2025_games())
    wb.build_warehouse([2025], db_path=db_path)
    from twm.asof import end_of_regular_season_as_of

    eos = end_of_regular_season_as_of(db_path, 2025)
    assert eos == _utc(2025, 9, 29, 12)  # W4 (last REG week) ended Sunday 09-28
    with AsOfView(db_path, eos) as v:
        finale = v.sql("SELECT count(*) AS n FROM fact_play WHERE game_id = '2025_04_CHI_MIN'")
        assert finale.item() == 2  # 13:00 ET kickoff, 21:00 end, + 6 h = 03:00 < 12:00
        assert v.sql("SELECT count(*) AS n FROM fact_play WHERE week >= 5").item() == 0


# --------------------------------------------------------------------------------------
# fact_schedule
# --------------------------------------------------------------------------------------


def test_fact_schedule_rows_and_columns(raw, db_path):
    raw.write_season(2025, season_2025_games())
    wb.build_warehouse([2025], db_path=db_path)
    con = _open(db_path)
    cols = [r[0] for r in con.execute("DESCRIBE fact_schedule").fetchall()]
    con.close()
    assert cols == [*sc.FACT_SCHEDULE_COLUMNS, "slot_available_at", "available_at"]
    forbidden = {"result", "total", "home_score", "away_score", "overtime", "spread_line",
                 "total_line", "home_moneyline", "away_moneyline", "home_implied_total",
                 "away_implied_total", "home_coach", "away_coach", "referee", "temp", "wind",
                 "roof", "home_qb_id", "away_qb_name", "game_end_utc_est"}  # fmt: skip
    assert not forbidden & set(cols)

    # regular season: visible from the spring release (May 20 of the season, 00:00 UTC)
    with AsOfView(db_path, _utc(2025, 5, 19, 23, 59)) as v:
        assert v.table("fact_schedule").height == 0
    with AsOfView(db_path, _utc(2025, 5, 20)) as v:
        s = v.table("fact_schedule")
        assert s.height == 9 and set(s["season_type"]) == {"REG"}
        # who plays whom in which week is known; the date, time and venue are not yet
        assert s["home_team"].null_count() == 0 and s["week"].null_count() == 0
        for c in av.SCHEDULE_SLOT_COLUMNS:
            assert s[c].null_count() == 9, c
        assert v.table("fact_game").height == 0  # results are not known yet
    # playoffs: the Wild Card matchups appear when the last regular-season game is final
    # (CHI at MIN, Sun 2025-09-28 13:00 ET = 17:00 UTC, ends 21:00, + 3 h result lag)
    wc_set = datetime(2025, 9, 29, 0)
    assert _one(
        db_path, "SELECT DISTINCT available_at FROM fact_schedule WHERE game_type = 'WC'"
    ) == [(wc_set,)]
    with AsOfView(db_path, wc_set.replace(tzinfo=UTC) - timedelta(seconds=1)) as v:
        assert v.sql("SELECT count(*) AS n FROM fact_schedule WHERE week = 5").item() == 0
    with AsOfView(db_path, wc_set.replace(tzinfo=UTC)) as v:
        assert v.sql("SELECT count(*) AS n FROM fact_schedule WHERE week = 5").item() == 2
        assert v.sql("SELECT count(*) AS n FROM fact_schedule WHERE week = 6").item() == 0
    # ... in particular at the Hot-Seat end-of-season snapshot (Monday 12:00 UTC)
    from twm.asof import end_of_regular_season_as_of

    with AsOfView(db_path, end_of_regular_season_as_of(db_path, 2025)) as v:
        assert v.sql("SELECT count(*) AS n FROM fact_schedule WHERE week = 5").item() == 2
    # the Super Bowl pairing is set once the conference game is final (19:00 UTC + 4 h + 3 h)
    assert _one(db_path, "SELECT available_at FROM fact_schedule WHERE game_type = 'SB'") == [
        (datetime(2025, 10, 20, 2),)
    ]


def _slot(db_path, when, game_id):
    with AsOfView(db_path, when) as v:
        return v.sql(
            "SELECT gameday, gametime, kickoff_utc, location, home_rest FROM fact_schedule "
            "WHERE game_id = ?",
            [game_id],
        ).rows()


def test_fact_schedule_slot_columns_wait_for_the_flex_window(raw, db_path):
    """The date/time/venue of a game can change after the release (flexed games; Week 17/18
    slots picked after the previous week): hidden until 12 days before kickoff, and for the
    last two regular-season weeks until the previous week's as-of."""
    raw.write_season(2025, season_2025_games())
    wb.build_warehouse([2025], db_path=db_path)
    # an ordinary week (W2: PHI at KC, Sun 2025-09-14 17:00 UTC): kickoff - 12 days
    w2 = _utc(2025, 9, 2, 17)
    assert _slot(db_path, w2 - timedelta(seconds=1), "2025_02_PHI_KC") == [
        (None, None, None, None, None)
    ]
    assert _slot(db_path, w2, "2025_02_PHI_KC")[0][:3] == (
        datetime(2025, 9, 14).date(), "13:00", datetime(2025, 9, 14, 17),
    )  # fmt: skip
    # the last regular-season week (W4, the fixture's "week 18"): not before W3's as-of
    finale = "2025_04_CHI_MIN"
    assert _slot(db_path, weekly_as_of(db_path, 2025, 1), finale)[0][1] is None
    assert _slot(db_path, weekly_as_of(db_path, 2025, 2), finale)[0][1] is None
    assert _slot(db_path, weekly_as_of(db_path, 2025, 3), finale)[0][1] == "13:00"
    # the second-to-last one (W3) waits for W2's as-of
    assert _slot(db_path, weekly_as_of(db_path, 2025, 1), "2025_03_MIA_BUF")[0][1] is None
    assert _slot(db_path, weekly_as_of(db_path, 2025, 2), "2025_03_MIA_BUF")[0][1] == "20:15"
    assert _one(
        db_path,
        "SELECT slot_available_at FROM fact_schedule WHERE game_id = ?",
        [finale],
    ) == [(datetime(2025, 9, 23, 14),)]


def test_listed_schedule_changes_wait_for_their_kickoff(raw, db_path):
    """nflverse keeps only the FINAL schedule: a game moved after the release (Irma 2017, the
    2024 snow-delayed Wild Card game) shows its new week/date from the release on. Listed
    changes (config availability.schedule_exceptions) carry no typed-in announcement date, so
    the row and its slot are hidden until the game's own kickoff (always after the real
    announcement)."""
    raw.write_season(2017, [game(2017, 1, "2017-09-10", "13:00", "NYJ", "BUF"),
                            game(2017, 11, "2017-11-19", "13:00", "TB", "MIA")])  # fmt: skip
    raw.write_season(2022, [game(2022, 17, "2023-01-01", "13:00", "CHI", "DET")])
    raw.write_season(2023, [game(2023, 18, "2024-01-07", "13:00", "PIT", "BAL"),
                            game(2023, 19, "2024-01-15", "16:30", "PIT", "BUF",
                                 game_type="WC")])  # fmt: skip
    manifest = wb.build_warehouse([2017, 2022, 2023], db_path=db_path)
    irma = "2017_11_TB_MIA"  # week 1 at Tampa, moved to week 11
    kickoff = datetime(2017, 11, 19, 18)  # 13:00 ET
    with AsOfView(db_path, _utc(2017, 6, 1)) as v:
        ids = set(v.table("fact_schedule", ["game_id"])["game_id"])
        assert irma not in ids and "2017_01_NYJ_BUF" in ids
    assert _one(
        db_path,
        "SELECT available_at, slot_available_at FROM fact_schedule WHERE game_id = ?",
        [irma],
    ) == [(kickoff, kickoff)]
    with AsOfView(db_path, _utc(2017, 11, 19, 17, 59)) as v:
        assert irma not in set(v.table("fact_schedule", ["game_id"])["game_id"])
    assert _slot(db_path, _utc(2017, 11, 19, 18), irma)[0][1] == "13:00"
    # the Wild Card game moved from Sunday to Monday: hidden until its Monday kickoff
    snow = "2023_19_PIT_BUF"
    with AsOfView(db_path, _utc(2024, 1, 15, 21, 29)) as v:
        assert snow not in set(v.table("fact_schedule", ["game_id"])["game_id"])
    with AsOfView(db_path, _utc(2024, 1, 15, 21, 30)) as v:
        assert v.sql("SELECT gametime FROM fact_schedule WHERE game_id = ?", [snow]).item() == (
            "16:30"
        )
    notes = _notes(manifest, "fact_schedule")
    assert notes["n_schedule_exceptions"] == 2
    assert notes["n_schedule_exceptions_at_kickoff"] == 2
    # the cancelled 2022 W17 BUF-CIN game is absent from nflverse: recorded for feature code
    assert notes["cancelled_games"] == ["2022_17_BUF_CIN"]
    assert notes["schedule_exceptions_not_found"] == ["2022_11_CLE_BUF"]  # not in this fixture


def test_a_sourced_announcement_date_makes_the_change_public_the_day_after(
    raw, db_path, monkeypatch
):
    """Only a date backed by a source may be earlier than kickoff; it counts from 12:00 UTC the
    next day."""
    change = av.ScheduleChange.announced_on("2017_11_TB_MIA", "2017-09-07", "Irma")
    monkeypatch.setattr(
        av.AvailabilityRules,
        "from_settings",
        classmethod(lambda cls: cls(schedule_exceptions=(change,))),
    )
    raw.write_season(2017, [game(2017, 1, "2017-09-10", "13:00", "NYJ", "BUF"),
                            game(2017, 11, "2017-11-19", "13:00", "TB", "MIA")])  # fmt: skip
    wb.build_warehouse([2017], db_path=db_path)
    assert _one(
        db_path, "SELECT available_at FROM fact_schedule WHERE game_id = '2017_11_TB_MIA'"
    ) == [(datetime(2017, 9, 8, 12),)]


def test_fact_schedule_playoff_game_without_a_previous_week_uses_its_kickoff(raw, db_path):
    raw.write_season(2024, [game(2024, 19, "2025-01-11", "16:30", "LAC", "HOU", game_type="WC")])
    manifest = wb.build_warehouse([2024], db_path=db_path)
    assert _one(db_path, "SELECT available_at FROM fact_schedule") == [
        (datetime(2025, 1, 11, 21, 30),)
    ]
    assert _counts(manifest, "fact_schedule") == {"post_own_kickoff_no_previous_week": 1}


# --------------------------------------------------------------------------------------
# Coaches
# --------------------------------------------------------------------------------------


def test_coach_rows_never_reveal_a_firing_early(raw, db_path):
    # PHI: Old Coach weeks 1-2, fired, New Coach from week 3 (kickoff Sun 09-21 13:00 ET)
    games = [
        game(2025, 1, "2025-09-07", "13:00", "DAL", "PHI", home_coach="Old Coach"),
        game(2025, 2, "2025-09-14", "13:00", "PHI", "KC", away_coach="Old Coach"),
        game(2025, 3, "2025-09-21", "13:00", "SF", "PHI", home_coach="New Coach"),
        game(2025, 4, "2025-09-28", "13:00", "PHI", "NYG", away_coach="New Coach"),
    ]
    raw.write_season(2025, games)
    manifest = wb.build_warehouse([2025], db_path=db_path)
    w1, w2 = weekly_as_of(db_path, 2025, 1), weekly_as_of(db_path, 2025, 2)
    new_kick = _utc(2025, 9, 21, 17)

    with AsOfView(db_path, w1) as v:
        # the week-2 row (listing Old Coach) is a FUTURE game: hidden until its kickoff
        rows = v.sql("SELECT week FROM coach_game WHERE team = 'PHI' ORDER BY week")
        assert rows["week"].to_list() == [1]
        assert "new_coach" not in set(v.table("dim_coach")["coach_id"])
    with AsOfView(db_path, w2) as v:
        # after week 2 the stint looks over in hindsight, but nobody knows yet: hidden
        cts = v.sql("SELECT * FROM coach_team_season WHERE team = 'PHI'")
        assert cts.height == 0
    with AsOfView(db_path, new_kick - timedelta(seconds=1)) as v:
        assert v.sql("SELECT count(*) AS n FROM coach_team_season WHERE team='PHI'").item() == 0
    with AsOfView(db_path, new_kick) as v:
        # the new coach walks out for week 3: the old stint and the new coach are now public
        cts = v.sql("SELECT coach_id, last_week FROM coach_team_season WHERE team = 'PHI'")
        assert cts.rows() == [("old_coach", 2)]
        assert "new_coach" in set(v.table("dim_coach")["coach_id"])
    # the new stint is known once its last game is final (week 4 ends 21:00 UTC, + 3 h)
    assert _one(
        db_path,
        "SELECT available_at FROM coach_team_season WHERE coach_id = 'new_coach' AND team='PHI'",
    ) == [(datetime(2025, 9, 29, 0),)]
    counts = _notes(manifest, "coach_team_season")["available_at_rule_counts"]
    assert counts["next_coach_first_kickoff"] == 1
    assert _notes(manifest, "coach_team_season")["n_available_after_week_asof"] == 1


# --------------------------------------------------------------------------------------
# Injuries
# --------------------------------------------------------------------------------------


def test_injury_rules_date_modified_kickoff_and_no_game(raw, db_path):
    games_2024 = [game(2024, 15, "2024-12-15", "13:00", "MIA", "HOU"),
                  game(2024, 16, "2024-12-21", "16:30", "HOU", "KC")]  # fmt: skip
    rows_2024 = [
        injury(2024, 15, "HOU", "p1", _utc(2024, 12, 13, 20)),  # observed stamp
        injury(2024, 16, "HOU", "p1", _utc(2024, 12, 19, 20)),  # next week's report
        injury(2024, 16, "KC", "p2", _utc(2024, 12, 17, 12)),  # stamped before W15's as-of
        injury(2024, 15, "MIA", "p3", None),  # a null stamp (like 62 rows of 2010): kickoff
        injury(2024, 15, "BUF", "p4", None),  # BUF has no game in W15: the week's as-of
    ]
    raw.write_season(2024, games_2024, injuries=frame(rows_2024, INJURY_DM_DTYPES))
    # 2009: the stamp is junk (one 2010-01-01 value), so kickoff is used even when present
    raw.write_season(
        2009,
        [game(2009, 1, "2009-09-13", "13:00", "SF", "STL")],
        injuries=frame([injury(2009, 1, "STL", "p5", _utc(2010, 1, 1, 9, 23))], INJURY_DM_DTYPES),
    )
    # 2025+: no date_modified column at all -> kickoff
    raw.write_season(2025, season_2025_games())
    manifest = wb.build_warehouse([2009, 2024, 2025], db_path=db_path)

    got = dict(
        ((s, w, t), a)
        for s, w, t, a in _one(
            db_path, "SELECT season, week, team, available_at FROM fact_injury_report"
        )
    )
    assert got[(2024, 15, "HOU")] == datetime(2024, 12, 13, 20)
    assert got[(2024, 16, "HOU")] == datetime(2024, 12, 19, 20)
    # raised to just after W15's Tuesday 14:00 as-of: week N+1 reports are never visible then
    assert got[(2024, 16, "KC")] == datetime(2024, 12, 17, 14, 0, 1)
    assert got[(2024, 15, "MIA")] == datetime(2024, 12, 15, 18)  # MIA's kickoff
    assert got[(2024, 15, "BUF")] == datetime(2024, 12, 17, 14)  # W15 as-of
    assert got[(2009, 1, "LA")] == datetime(2009, 9, 13, 17)  # STL -> LA, kickoff
    assert got[(2025, 1, "PHI")] == datetime(2025, 9, 5, 0, 20)  # Thursday-night kickoff

    notes = _notes(manifest, "fact_injury_report")
    assert _counts(manifest, "fact_injury_report") == {
        "date_modified": 3, "kickoff": 1 + 1 + 14, "week_asof_no_game": 1,
    }  # fmt: skip
    assert notes["n_raised_to_after_previous_week_asof"] == 1

    # at the Tuesday as-of after W15: W15's report is usable, W16's is not
    with AsOfView(db_path, weekly_as_of(db_path, 2024, 15)) as v:
        weeks = v.sql("SELECT DISTINCT week FROM fact_injury_report WHERE season = 2024")
        assert weeks["week"].to_list() == [15]
    with AsOfView(db_path, weekly_as_of(db_path, 2025, 1)) as v:
        latest = v.sql("SELECT max(week) AS w FROM fact_injury_report WHERE season = 2025")
        assert latest.item() == 1


def test_injury_rows_without_date_modified_column_use_kickoff(raw, db_path):
    rows = [{"season": 2026, "season_type": "REG", "game_type": "REG", "team": "KC", "week": 1,
             "gsis_id": "00-0000002", "report_status": "Out"}]  # fmt: skip
    raw.write_season(
        2026,
        [game(2026, 1, "2026-09-13", "16:25", "LV", "KC")],
        injuries=frame(rows, INJURY_DTYPES),
    )
    wb.build_warehouse([2026], db_path=db_path)
    assert _one(db_path, "SELECT available_at FROM fact_injury_report") == [
        (datetime(2026, 9, 13, 20, 25),)
    ]


def test_injury_stamps_2010_2020_are_shifted_later(raw, db_path):
    """2010-2020 date_modified stamps are not true UTC (DST-invariant, before Friday practice):
    they count as public only 9 h later. 2021+ stamps are real UTC and used as stored. The
    previous-week floor still wins when it is later."""
    games_2015 = [game(2015, 5, "2015-10-11", "13:00", "MIA", "HOU"),
                  game(2015, 6, "2015-10-18", "13:00", "HOU", "KC")]  # fmt: skip
    rows_2015 = [
        injury(2015, 5, "HOU", "p1", _utc(2015, 10, 9, 11, 50)),  # Friday 11:50 "UTC"
        # stamped Monday 01:00 before W5's as-of (Tue 10-13 14:00): + 9 h is 10:00, still
        # before the as-of, so the floor (1 s after it) applies
        injury(2015, 6, "KC", "p2", _utc(2015, 10, 12, 1)),
    ]
    raw.write_season(2015, games_2015, injuries=frame(rows_2015, INJURY_DM_DTYPES))
    raw.write_season(
        2022,
        [game(2022, 5, "2022-10-09", "13:00", "MIA", "NYJ")],
        injuries=frame([injury(2022, 5, "NYJ", "p3", _utc(2022, 10, 7, 19, 21))], INJURY_DM_DTYPES),
    )
    manifest = wb.build_warehouse([2015, 2022], db_path=db_path)
    got = {
        (s, w): (a, dm)
        for s, w, a, dm in _one(
            db_path, "SELECT season, week, available_at, date_modified FROM fact_injury_report"
        )
    }
    assert got[(2015, 5)] == (datetime(2015, 10, 9, 20, 50), datetime(2015, 10, 9, 11, 50))
    assert got[(2015, 6)][0] == datetime(2015, 10, 13, 14, 0, 1)  # the floor
    assert got[(2022, 5)] == (datetime(2022, 10, 7, 19, 21), datetime(2022, 10, 7, 19, 21))
    assert _counts(manifest, "fact_injury_report") == {
        "date_modified": 1, "date_modified_legacy_shifted": 2,
    }  # fmt: skip
    assert _notes(manifest, "fact_injury_report")["n_raised_to_after_previous_week_asof"] == 1


# --------------------------------------------------------------------------------------
# Depth charts
# --------------------------------------------------------------------------------------


def _legacy(week, game_type, **kw):
    return {"season": 2019, "club_code": "KC", "week": week, "game_type": game_type,
            "depth_team": "1", "formation": "Offense", "gsis_id": "00-0033873",
            "position": "QB", "depth_position": "QB", "full_name": "P. Mahomes", **kw}  # fmt: skip


def _season_2019_games():
    return [
        game(2019, 16, "2019-12-22", "13:00", "KC", "CHI"),
        game(2019, 17, "2019-12-29", "13:00", "LAC", "KC"),
        game(2019, 18, "2020-01-04", "16:35", "BUF", "HOU", game_type="WC"),
        game(2019, 19, "2020-01-12", "15:05", "HOU", "KC", game_type="DIV"),
        game(2019, 20, "2020-01-19", "15:05", "TEN", "KC", game_type="CON"),
        game(2019, 21, "2020-02-02", "18:30", "SF", "KC", game_type="SB"),
    ]


def test_legacy_depth_chart_weeks_and_orphan_rows(raw, db_path):
    rows = [
        _legacy(16, "REG"),
        _legacy(17, "REG"),
        _legacy(18, "REG"),  # pulled the week after the finale: no REG week 18 -> the WC week
        _legacy(18, "WC"),
        _legacy(None, "SBBYE"),  # the idle week before the Super Bowl -> the SB week
    ]
    raw.write_season(2019, _season_2019_games(), depth_charts=frame(rows, LEGACY_DC_DTYPES))
    manifest = wb.build_warehouse([2019], db_path=db_path)
    got = _one(
        db_path,
        "SELECT week, game_type, available_at FROM fact_depth_chart "
        "ORDER BY available_at, game_type",
    )
    assert got == [
        (16, "REG", datetime(2019, 12, 18, 14)),  # W16 as-of Tue 12-24 minus 6 days
        (17, "REG", datetime(2019, 12, 25, 14)),
        (18, "REG", datetime(2020, 1, 1, 14)),  # WC as-of Tue 01-07 minus 6 days
        (18, "WC", datetime(2020, 1, 1, 14)),
        (None, "SBBYE", datetime(2020, 1, 29, 14)),  # SB as-of Tue 02-04 minus 6 days
    ]
    assert _counts(manifest, "fact_depth_chart") == {
        "legacy_same_week": 3, "legacy_reg_after_finale_to_next_round": 1,
        "legacy_sbbye_to_super_bowl_week": 1,
    }  # fmt: skip
    # at the Tuesday as-of after week 16, week 16's chart is in force and week 17's is not
    with AsOfView(db_path, weekly_as_of(db_path, 2019, 16)) as v:
        assert v.sql("SELECT week FROM fact_depth_chart")["week"].to_list() == [16]
    with AsOfView(db_path, weekly_as_of(db_path, 2019, 17)) as v:
        assert sorted(v.sql("SELECT week FROM fact_depth_chart")["week"].to_list()) == [16, 17]


def test_daily_depth_chart_dt_is_inclusive(raw, db_path):
    base = {"team": "PHI", "player_name": "A", "espn_id": "1", "gsis_id": "00-1",
            "pos_grp": "3WR 1TE", "pos_name": "QB", "pos_abb": "QB", "pos_rank": 1}  # fmt: skip
    rows = [{**base, "dt": "2025-09-09T14:00:00Z", "pos_slot": 1},
            {**base, "dt": "2025-09-09T14:00:01Z", "pos_slot": 1}]  # fmt: skip
    raw.write_season(2025, season_2025_games(), depth_charts=frame(rows, DAILY_DC_DTYPES))
    wb.build_warehouse([2025], db_path=db_path)
    with AsOfView(db_path, weekly_as_of(db_path, 2025, 1)) as v:  # 2025-09-09 14:00 UTC
        assert v.sql("SELECT dt FROM fact_depth_chart")["dt"].to_list() == [
            datetime(2025, 9, 9, 14)
        ]


# --------------------------------------------------------------------------------------
# dim_player: a player exists point-in-time from his draft or first data row
# --------------------------------------------------------------------------------------


@pytest.fixture
def draft_db(raw, db_path):
    games = season_2015_draft_games()
    w6 = games[1]
    raw.write("players", None, draft_class_players())
    snaps = [{"game_id": w6["game_id"], "season": 2015, "game_type": "REG", "week": 6,
              "player": "Undrafted Snaps", "pfr_player_id": "UdfaSn00", "team": "KC",
              "opponent": "HOU", "offense_snaps": 10.0}]  # fmt: skip
    raw.write_season(
        2015, games, player_stats=[player_week(w6, "00-UDFA01", 2)], snaps=snaps, injuries=[]
    )
    manifest = wb.build_warehouse([2015], db_path=db_path)
    return db_path, manifest


def _players(db_path, when):
    with AsOfView(db_path, when) as v:
        return set(v.table("dim_player", ["gsis_id"])["gsis_id"])


def test_dim_player_rows_appear_at_the_draft_or_first_data_row(draft_db):
    db_path, manifest = draft_db
    w5, w6 = weekly_as_of(db_path, 2015, 5), weekly_as_of(db_path, 2015, 6)
    # in-season 2015: nobody drafted later than 2015 exists (the next draft's order would
    # reveal this season's final standings)
    with AsOfView(db_path, w5) as v:
        assert v.sql("SELECT count(*) AS n FROM dim_player WHERE draft_year > 2015").item() == 0
        assert v.sql("SELECT count(*) AS n FROM wh.dim_player WHERE draft_year > 2015").item() == 1
    assert _players(db_path, w5) == {"00-D2014"}
    # the undrafted players appear with their first public data row (week 6 stats and snaps,
    # game end + 6 h), i.e. at the as-of after that game
    assert _players(db_path, w6) == {"00-D2014", "00-UDFA01", "00-UDFA02"}
    # the 2016 first pick exists from 2016-05-15 00:00 UTC (after the draft, before June 1)
    assert "00-D2016" not in _players(db_path, _utc(2016, 5, 14, 23, 59))
    assert "00-D2016" in _players(db_path, _utc(2016, 6, 1))
    # a player without a draft and without any data row never appears
    assert "00-GHOST" not in _players(db_path, _utc(2030, 1, 1))
    assert _notes(manifest, "dim_player")["public_from_utc_rule_counts"] == {
        "draft": 2, "first_fact_row": 2, "never": 1,
    }  # fmt: skip
    first = _one(
        db_path,
        "SELECT p.public_from_utc, x.available_at FROM dim_player p JOIN fact_player_week x "
        "ON x.player_id = p.gsis_id WHERE p.gsis_id = '00-UDFA01'",
    )
    assert first[0][0] == first[0][1]


def test_dim_player_view_keeps_the_allowlist_and_hides_the_row_time(draft_db):
    db_path, _ = draft_db
    with AsOfView(db_path, _utc(2016, 6, 1)) as v:
        cols = v.table("dim_player").columns
    assert cols == [c for c in sc.tables()["dim_player"].column_names if c in av.DIM_PLAYER_VISIBLE]
    assert "public_from_utc" not in cols and "position" not in cols


# --------------------------------------------------------------------------------------
# dim_week: the calendar is known, a future week's final game list is not
# --------------------------------------------------------------------------------------


def season_2020_postponement_games():
    """2020-style: W5 got a game moved to Tuesday (a split week) and W12 one to Wednesday,
    both announced long after week 1."""
    return [
        game(2020, 1, "2020-09-13", "13:00", "MIA", "NE"),
        game(2020, 4, "2020-10-04", "13:00", "CLE", "DAL"),
        game(2020, 5, "2020-10-11", "13:00", "MIA", "SF"),
        game(2020, 5, "2020-10-13", "19:00", "BUF", "TEN"),
        game(2020, 12, "2020-11-29", "13:00", "TEN", "IND"),
        game(2020, 12, "2020-12-02", "15:40", "BAL", "PIT"),
    ]


def test_dim_week_masks_future_weeks_schedule_counts(raw, db_path):
    raw.write_season(2020, season_2020_postponement_games())
    wb.build_warehouse([2020], db_path=db_path)
    cols = "week, asof_weekly_utc, prev_week, window_start_utc, n_games, is_split_week, " \
           "last_kickoff_utc, n_games_after_asof"  # fmt: skip
    with AsOfView(db_path, weekly_as_of(db_path, 2020, 1)) as v:
        rows = {r[0]: r for r in v.sql(f"SELECT {cols} FROM dim_week").rows()}
    assert rows[1][4:] == (1, False, datetime(2020, 9, 13, 17), 0)  # week 1 is over
    for week in (4, 5, 12):
        assert rows[week][1] is not None and rows[week][2] is not None  # calendar: known
        assert rows[week][4:] == (None, None, None, None), week  # final game list: not yet
    with AsOfView(db_path, weekly_as_of(db_path, 2020, 5)) as v:
        w5 = v.sql("SELECT n_games, is_split_week FROM dim_week WHERE week = 5").rows()
        w12 = v.sql("SELECT n_games, is_split_week FROM dim_week WHERE week = 12").rows()
    assert w5 == [(2, True)] and w12 == [(None, None)]
    # the stored table is complete (wh. is the deliberate bypass)
    assert _one(db_path, "SELECT n_games FROM dim_week WHERE week = 12") == [(2,)]


# --------------------------------------------------------------------------------------
# Real data (opt-in: `uv run pytest -m realdata`; never downloads, builds into tmp)
# --------------------------------------------------------------------------------------

# The checks cover 2013-2025 (complete seasons with every dataset) of the shared full build
# (tests/conftest.py real_full_db).
REAL_FIRST, REAL_LAST = 2013, 2025


@pytest.mark.realdata
def test_real_every_week_is_complete_and_nothing_later_is_visible(real_full_db):
    """For every non-split regular-season week 2013-2025, at its Tuesday as-of: all of that
    week's plays, player stats and snaps are visible, and nothing from a later week is."""
    path, _ = real_full_db
    con = _open(path)
    weeks = (
        "SELECT season, week, asof_weekly_utc AS asof FROM dim_week "
        f"WHERE season_type = 'REG' AND NOT is_split_week "
        f"AND season BETWEEN {REAL_FIRST} AND {REAL_LAST}"
    )
    assert con.execute(f"SELECT count(*) FROM ({weeks})").fetchone()[0] > 200
    for t in ("fact_play", "fact_player_week", "fact_snaps", "fact_team_week", "fact_game"):
        hidden_now, visible_later = con.execute(f"""
            WITH w AS ({weeks}) SELECT
              (SELECT count(*) FROM w JOIN {t} x ON x.season = w.season AND x.week = w.week
               WHERE x.available_at > w.asof),
              (SELECT count(*) FROM w JOIN {t} x ON x.season = w.season AND x.week > w.week
               WHERE x.available_at <= w.asof)""").fetchone()
        assert (hidden_now, visible_later) == (0, 0), t
    next_injuries = con.execute(f"""
        WITH w AS ({weeks}) SELECT count(*) FROM w JOIN fact_injury_report x
        ON x.season = w.season AND x.week > w.week WHERE x.available_at <= w.asof""").fetchone()
    assert next_injuries == (0,)
    next_charts = con.execute(f"""
        WITH w AS ({weeks}) SELECT count(*) FROM w JOIN fact_depth_chart x
        ON x.source_format = 'legacy' AND x.season = w.season AND x.week > w.week
        WHERE x.available_at <= w.asof""").fetchone()
    assert next_charts == (0,)
    # the Hot-Seat end-of-season snapshot sees every play of the finale
    assert con.execute("""
        SELECT count(*) FROM dim_week w JOIN fact_play p ON p.season = w.season
        AND p.week = w.week WHERE w.is_last_reg_week
        AND p.available_at > w.asof_end_of_regular_season_utc""").fetchone() == (0,)
    for name, a in av.TABLE_AVAILABILITY.items():
        if a.kind == "event":
            assert con.execute(
                f"SELECT count(*) FROM {name} WHERE available_at IS NULL"
            ).fetchone() == (0,)
    # no game-data row is ever available before its game ended; nor a result, nor a coach
    # before kickoff
    for t in av.GAME_DATA_TABLES:
        assert con.execute(f"""SELECT count(*) FROM {t} x JOIN fact_game g USING (game_id)
            WHERE x.available_at < g.game_end_utc_est""").fetchone() == (0,), t
    assert con.execute(
        "SELECT count(*) FROM fact_game WHERE available_at < game_end_utc_est"
    ).fetchone() == (0,)
    assert con.execute("""SELECT count(*) FROM coach_game c JOIN fact_game g USING (game_id)
        WHERE c.available_at < g.kickoff_utc""").fetchone() == (0,)
    # the final score is never public before the last play: games of 2020+ with a reliable
    # wall clock (first play within 30 min of kickoff, last play within 6 h of it)
    n_games, n_late = con.execute("""
        WITH t AS (
            SELECT game_id,
                   min(CAST(CAST(time_of_day AS TIMESTAMPTZ) AS TIMESTAMP)) AS first_play,
                   max(CAST(CAST(time_of_day AS TIMESTAMPTZ) AS TIMESTAMP)) AS last_play
            FROM fact_play WHERE season >= 2020 AND time_of_day IS NOT NULL GROUP BY game_id)
        SELECT count(*), count(*) FILTER (WHERE t.last_play > g.available_at)
        FROM t JOIN fact_game g USING (game_id)
        WHERE abs(epoch(t.first_play) - epoch(g.kickoff_utc)) <= 1800
          AND t.last_play <= g.kickoff_utc + INTERVAL 6 HOUR""").fetchone()
    assert n_games > 1500 and n_late == 0
    # 2010-2020 injury stamps are shifted by at least 8 h (they are not true UTC)
    assert con.execute("""SELECT count(*) FROM fact_injury_report
        WHERE season BETWEEN 2010 AND 2020 AND date_modified IS NOT NULL
          AND available_at < date_modified + INTERVAL 8 HOUR""").fetchone() == (0,)
    # only split-week games (and their stats) are late for their own week
    assert con.execute(f"""
        SELECT count(*) FROM fact_game g JOIN dim_week w ON w.season = g.season
        AND w.week = g.week WHERE g.season BETWEEN {REAL_FIRST} AND {REAL_LAST}
        AND g.available_at > w.asof_weekly_utc""").fetchone() == (5,)
    # nobody drafted after the as-of season is visible in dim_player in-season
    con.close()
    with AsOfView(path, weekly_as_of(path, 2015, 5)) as v:
        assert v.sql("SELECT count(*) AS n FROM dim_player WHERE draft_year > 2015").item() == 0


@pytest.mark.realdata
def test_real_plays_frame_filter_matches_the_view(real_full_db):
    path, _ = real_full_db
    from twm.asof import asof_filter

    asof = weekly_as_of(path, 2020, 12)
    con = _open(path)
    plays = con.execute(
        "SELECT game_id, week, available_at FROM fact_play WHERE season = 2020"
    ).pl()
    con.close()
    filtered = asof_filter(plays, asof)
    with AsOfView(path, asof) as v:
        n_view = v.sql("SELECT count(*) AS n FROM fact_play WHERE season = 2020").item()
    assert filtered.height == n_view
    assert "2020_12_BAL_PIT" not in set(filtered["game_id"])  # the Wednesday game
    assert set(pl.Series(filtered["week"]).unique()) == set(range(1, 13))
