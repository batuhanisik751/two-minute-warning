"""Combine and Next Gen Stats warehouse tables (I1a): fact_combine and fact_ngs_*_week on a
synthetic cache (game matching, gsis links, availability, as-of masking, leakage), plus opt-in
real-data checks on the cache."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import duckdb
import polars as pl
import pytest

from tests.conftest import (
    PLAYERS_DTYPES,
    default_players,
    frame,
    ngs_row,
    season_2025_games,
)
from twm.asof import AsOfView, weekly_as_of
from twm.backtest.leakage import LeakageError, assert_future_invariant
from twm.warehouse import available as av
from twm.warehouse import build as wb

REC = "fact_ngs_receiving_week"
BY_ID = {g["game_id"]: g for g in season_2025_games()}
SUN = BY_ID["2025_01_KC_LAC"]  # Sunday 13:00 ET
MNF = BY_ID["2025_01_MIN_CHI"]  # Monday 20:15 ET
SB = BY_ID["2025_08_KC_PHI"]  # Super Bowl: NGS numbers it week 9
PLAYERS = [  # (gsis_id, name, draft_year, rookie_season, pfr_id)
    ("00-0000101", "Drafted Right", 2025, 2025, "RighDr00"),
    ("00-0000102", "Namesake Old", 2019, 2019, "NameOl00"),
    ("00-0000103", "Twin Prospect", None, 2025, "TwinPr00"),
]
COMBINE = [  # draft columns are PFR's record of the linked player
    {"player_name": "Drafted Right", "pfr_id": "RighDr00", "school": "A", "pos": "WR",
     "draft_year": 2025.0, "draft_team": "Kansas City Chiefs", "draft_round": 1.0,
     "draft_ovr": 10.0, "ht": "6-2", "forty": 4.38},
    {"player_name": "Namesake Young", "pfr_id": "NameOl00", "school": "B", "pos": "CB",
     "draft_year": 2019.0, "draft_round": 3.0, "ht": "5-11"},
    {"player_name": "Twin Prospect", "pfr_id": "TwinPr00", "school": "C", "pos": "RB"},
    {"player_name": "Twin Prospect", "pfr_id": "TwinPr00", "school": "D", "pos": "RB"},
    {"player_name": "Gamma Three", "school": "E", "pos": "TE", "ht": "6-5"},
]  # fmt: skip


@pytest.fixture
def i1a_db(raw, db_path):
    extra = [{"gsis_id": g, "display_name": n, "position": "WR", "draft_year": d,
              "rookie_season": r, "pfr_id": p} for g, n, d, r, p in PLAYERS]  # fmt: skip
    raw.write("players", None, pl.concat([default_players(), frame(extra, PLAYERS_DTYPES)]))
    ngs = {
        "ngs_receiving": [
            ngs_row(SUN, "00-0000002", "LAC", targets=7, avg_separation=3.1),
            ngs_row(MNF, "00-0000001", "CHI", targets=6, avg_separation=2.5),
            ngs_row(SB, "00-0000002", "PHI", targets=5, week=9, season_type="POST"),
            ngs_row(SUN, "00-0000002", None, targets=60, week=0),  # season totals
            ngs_row(SUN, "00-9999999", "KC", targets=5),  # not a known player
        ],
    }
    raw.write_season(2025, season_2025_games(), combine=[{"season": 2025, **c} for c in COMBINE],
                     ngs=ngs)  # fmt: skip
    wb.build_warehouse([2025], db_path=db_path)
    return db_path


def rows(db, sql: str) -> list[tuple]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def manifest_notes(db, table: str) -> dict:
    sql = f"SELECT notes FROM build_manifest WHERE table_name = '{table}'"
    return json.loads(rows(db, sql)[0][0])


# ---- Next Gen Stats ---------------------------------------------------------------------------


def test_ngs_weekly_rows_match_their_game_and_known_players(i1a_db):
    got = rows(i1a_db, f"SELECT week, team_abbr, game_id, gsis_id FROM {REC} ORDER BY week, "
                       "player_gsis_id")  # fmt: skip
    assert got == [
        (0, None, None, "00-0000002"),  # season totals: no game
        (1, "CHI", MNF["game_id"], "00-0000001"),
        (1, "LAC", SUN["game_id"], "00-0000002"),
        (1, "KC", SUN["game_id"], None),  # an id no player table knows: gsis_id NULL
        (9, "PHI", SB["game_id"], "00-0000002"),  # NGS's Super Bowl week = schedule week + 1
    ]
    notes = manifest_notes(i1a_db, REC)
    assert notes["n_rows_without_gsis_id"] == 1 and notes["n_week0_rows"] == 1
    assert notes["n_super_bowl_rows_week_shifted"] == 1
    # the other families got the fixture's default row (one game, a known player)
    for fam in ("passing", "rushing"):
        assert rows(i1a_db, f"SELECT count(game_id), count(gsis_id) FROM fact_ngs_{fam}_week") == [
            (1, 1)
        ]


def test_ngs_rows_wait_for_the_lag_and_the_nightly_run(i1a_db):
    got = dict(rows(i1a_db, f"SELECT coalesce(game_id, 'week0'), available_at FROM {REC} "
                            "WHERE gsis_id IS NOT NULL"))  # fmt: skip
    # Sunday 13:00 ET game ends 21:00 UTC: + 6 h is 03:00 UTC, before the nightly NGS run
    # (03:00-05:00 ET), so the row waits for 10:00 UTC Monday
    assert got[SUN["game_id"]] == datetime(2025, 9, 8, 10, 0)
    # Monday night ends 04:15 UTC: + 6 h (10:15) is after the run, still before the as-of
    assert got[MNF["game_id"]] == datetime(2025, 9, 9, 10, 15)
    assert got[MNF["game_id"]] <= rows(i1a_db, "SELECT asof_weekly_utc FROM dim_week WHERE "
                                               "season = 2025 AND week = 1")[0][0]  # fmt: skip
    # season totals: after the season's last game (the Super Bowl, 18:30 ET = 23:30 UTC)
    assert got["week0"] == got[SB["game_id"]] == datetime(2025, 11, 3, 10, 0)
    counts = manifest_notes(i1a_db, REC)["available_at_rule_counts"]
    assert counts["week0_after_season_last_game"] == 1 and counts["unplaced"] == 0


def test_ngs_rule_errs_late_on_both_sides_of_daylight_saving():
    rules = av.AvailabilityRules()
    for end, want in [
        ("2025-09-07 21:00:00", datetime(2025, 9, 8, 10, 0)),  # EDT: run done by 09:00 UTC
        ("2025-12-08 02:00:00", datetime(2025, 12, 8, 10, 0)),  # EST: by 10:00 UTC
        ("2025-12-08 06:00:00", datetime(2025, 12, 8, 12, 0)),  # late end: + 6 h wins
    ]:
        sql = av.available_at_sql(REC, rules)
        src = (f"SELECT 1 AS week, TIMESTAMP '{end}' AS availability_game_end_utc, "
               "NULL AS last_end")  # fmt: skip
        expr = sql.expr.replace("_g.", "x.").replace("_l.", "x.").replace("s.week", "x.week")
        assert duckdb.sql(f"SELECT {expr} FROM ({src}) x").fetchone()[0] == want, end


def test_ngs_weekly_row_without_a_game_stops_the_build(raw, db_path):
    bad = {"ngs_rushing": [ngs_row(SUN, "00-0000001", "NYJ", rush_attempts=12)]}  # NYJ idle
    raw.write_season(2025, season_2025_games(), ngs=bad)
    with pytest.raises(av.AvailabilityError, match="fact_ngs_rushing_week: 1 row"):
        wb.build_warehouse([2025], db_path=db_path)


# ---- combine ----------------------------------------------------------------------------------


def test_combine_links_only_fitting_unique_pfr_ids(i1a_db):
    got = rows(i1a_db, "SELECT player_name, school, gsis_id, height_in FROM fact_combine "
                       "ORDER BY school")  # fmt: skip
    assert got == [
        ("Drafted Right", "A", "00-0000101", 74),  # drafted in the combine's year
        ("Namesake Young", "B", None, 71),  # the PFR id belongs to a 2019 draftee
        ("Twin Prospect", "C", None, None),  # one id on two rows that both fit
        ("Twin Prospect", "D", None, None),
        ("Gamma Three", "E", None, 77),  # no PFR page
    ]
    notes = manifest_notes(i1a_db, "fact_combine")
    assert notes["gsis_id_null_reasons"] == {
        "no_pfr_id": 1, "pfr_id_not_in_bridge": 0, "link_does_not_fit_combine_year": 1,
        "pfr_id_on_several_fitting_rows": 2,
    }  # fmt: skip
    assert notes["gsis_match_by_season"] == {"2025": [5, 1]}
    assert notes["gsis_match_by_pos"]["WR"] == [1, 1]
    # the report lists both doubtful ids for the owner and counts their rows as unmatched
    suspects = rows(i1a_db, "SELECT source_id, n_rows, detail FROM report_id_unmatched WHERE "
                            "kind = 'suspect' AND dataset = 'fact_combine' ORDER BY 1")  # fmt: skip
    assert [(s, n) for s, n, _ in suspects] == [("NameOl00", 1), ("TwinPr00", 2)]
    assert "does not fit" in suspects[0][2] and "2 combine rows fit" in suspects[1][2]
    assert rows(i1a_db, "SELECT n_rows, n_rows_unmatched FROM report_id_coverage WHERE "
                        "dataset = 'fact_combine' AND scope = 'all'") == [(4, 3)]  # fmt: skip


def test_combine_rows_are_public_on_draft_day_and_draft_columns_after_it(i1a_db):
    sql = ("SELECT DISTINCT 'row', available_at FROM fact_combine UNION ALL "
           "SELECT DISTINCT 'draft', draft_available_at FROM fact_combine")  # fmt: skip
    at = dict(rows(i1a_db, sql))
    assert at == {"row": datetime(2025, 4, 24), "draft": datetime(2025, 5, 15)}

    def view(day: datetime) -> list[tuple]:
        with AsOfView(i1a_db, day.replace(tzinfo=UTC)) as v:
            df = v.sql("SELECT school, forty, draft_round, draft_team FROM fact_combine "
                       "WHERE school IN ('A', 'B') ORDER BY school")  # fmt: skip
            return df.rows()

    assert view(datetime(2025, 4, 23, 23, 59)) == []  # before the draft's first day
    assert view(datetime(2025, 4, 30)) == [("A", 4.38, None, None), ("B", None, None, None)]
    assert view(datetime(2025, 5, 15)) == [
        ("A", 4.38, 1, "Kansas City Chiefs"), ("B", None, 3, None),
    ]  # fmt: skip


def test_combine_season_without_a_listed_draft_waits_for_the_draft_public_day():
    rules = av.AvailabilityRules()
    sql = av.available_at_sql("fact_combine", rules)
    got = duckdb.sql(f"SELECT {sql.expr}, {sql.extra[0][1]} FROM (SELECT 2031 AS season) s")
    assert got.fetchone() == (datetime(2031, 5, 15), datetime(2031, 5, 15))
    # every listed first day (latest: 2014-05-08) is before the May 15 fallback
    assert all((d.month, d.day) < (5, 15) for d in av.DRAFT_FIRST_DAY.values())


# ---- leakage harness ---------------------------------------------------------------------------


def targets_to_date(table: str):
    def build(v: AsOfView) -> pl.DataFrame:
        return v.sql(f"SELECT gsis_id, sum(targets) AS targets FROM {table} WHERE season = 2025 "
                     "AND week > 0 AND gsis_id IS NOT NULL GROUP BY gsis_id")  # fmt: skip

    return build


def test_ngs_through_the_view_passes_and_a_bypass_is_caught(i1a_db):
    asof = weekly_as_of(i1a_db, 2025, 1)
    out = assert_future_invariant(targets_to_date(REC), i1a_db, asof, key=["gsis_id"])
    assert sorted(out.rows()) == [("00-0000001", 6), ("00-0000002", 7)]  # week 1 only
    with AsOfView(i1a_db, asof) as v:  # the season totals are not public in-season
        assert v.sql(f"SELECT count(*) AS n FROM {REC} WHERE week = 0")["n"].item() == 0
    with pytest.raises(LeakageError):
        assert_future_invariant(targets_to_date(f"wh.{REC}"), i1a_db, asof, key=["gsis_id"])


def test_combine_draft_columns_read_early_are_caught(i1a_db):
    asof = datetime(2025, 4, 30, tzinfo=UTC)  # after draft day, before May 15

    def capital(table: str):
        def build(v: AsOfView) -> pl.DataFrame:
            return v.sql(f"SELECT school, forty, draft_ovr FROM {table} WHERE season = 2025")

        return build

    out = assert_future_invariant(capital("fact_combine"), i1a_db, asof, key=["school"])
    assert out["draft_ovr"].null_count() == out.height == 5
    with pytest.raises(LeakageError, match="draft_ovr"):
        assert_future_invariant(capital("wh.fact_combine"), i1a_db, asof, key=["school"])


# ---- real data (opt-in: uv run pytest -m realdata) --------------------------------------------


@pytest.mark.realdata
def test_real_ngs_rows_match_games_and_week0_is_the_regular_season(real_full_db):
    path, manifest = real_full_db
    for name in av.NGS_TABLES:
        notes = json.loads(manifest[name]["notes"])
        assert notes["available_at_rule_counts"]["unplaced"] == 0, name
        assert notes["n_rows_without_gsis_id"] == 0, name
        # every weekly row has its game; week 0 never has one
        assert rows(path, f"SELECT count(*) FILTER (WHERE (week = 0) = (game_id IS NOT NULL)) "
                          f"FROM {name}") == [(0,)]  # fmt: skip
    # week 0 receptions are the regular season's (fact_player_week REG sums), 2016-2025
    n, same = rows(path, f"""
        SELECT count(*), count(*) FILTER (WHERE r.receptions = p.reg)
        FROM {REC} r JOIN (
            SELECT player_id, season, sum(receptions) FILTER (WHERE season_type = 'REG') AS reg
            FROM fact_player_week GROUP BY 1, 2) p
          ON p.player_id = r.player_gsis_id AND p.season = r.season
        WHERE r.week = 0 AND r.season <= 2025""")[0]  # fmt: skip
    assert n == 1251 and same / n > 0.99, (n, same)


@pytest.mark.realdata
def test_real_combine_links_fit_the_draft(real_full_db):
    path, manifest = real_full_db
    notes = json.loads(manifest["fact_combine"]["notes"])
    assert notes["available_at_rule_counts"] == {"draft_first_day": 8968,
                                                 "unlisted_season_draft_public_day": 0}  # fmt: skip
    # every kept link of a drafted row names a player drafted in the combine's year
    bad, drafted, linked = rows(path, """
        SELECT count(*) FILTER (WHERE c.gsis_id IS NOT NULL AND p.draft_year IS NOT NULL
                                AND p.draft_year <> c.season),
               count(*) FILTER (WHERE c.draft_year = c.season),
               count(c.gsis_id) FILTER (WHERE c.draft_year = c.season)
        FROM fact_combine c LEFT JOIN dim_player p USING (gsis_id)""")[0]  # fmt: skip
    assert bad == 0
    assert linked / drafted > 0.96, (linked, drafted)
