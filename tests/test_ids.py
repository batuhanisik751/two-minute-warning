"""Player IDs (B3): canonical ids, names, the bridge, dim_player ids, the report, `twm ids`.

Every id, name and date below is synthetic (fixture players "Alpha One" ... with made-up
900xxx / 800xxx numbers); nothing is a real-world fact.
"""

from __future__ import annotations

import csv
import json
import re
from datetime import UTC, date, datetime

import duckdb
import polars as pl
import pytest
from typer.testing import CliRunner

from tests.conftest import (
    DAILY_DC_DTYPES,
    FF_PLAYERIDS_DTYPES,
    LEGACY_DC_DTYPES,
    PLAYERS_DTYPES,
    ROSTERS_WEEKLY_DTYPES,
    frame,
    game,
    legacy_depth_chart,
    season_2025_games,
)
from twm import ids
from twm.asof import AsOfView
from twm.backtest.leakage import LeakageError, assert_future_invariant
from twm.cli import app
from twm.sources import nflverse as nv
from twm.warehouse import available as av
from twm.warehouse import build as wb
from twm.warehouse import schema as sc

# --------------------------------------------------------------------------------------
# Canonical ids and normalized names
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (4374496, "4374496"),
        (4374496.0, "4374496"),
        ("4374496.0", "4374496"),
        ("4374496.000", "4374496"),
        ("  4374496 ", "4374496"),
        ("0012", "0012"),  # ids are text: leading zeros stay
        ("MahoPa00", "MahoPa00"),
        ("St.BrAm00", "St.BrAm00"),  # a period inside a slug is not a decimal
        ("12.5", "12.5"),
        (12.5, "12.5"),
        ("", None),
        ("   ", None),
        (None, None),
        (float("nan"), None),
        ("3200454b-4508-0143", "3200454b-4508-0143"),
    ],
)
def test_canonical_id(value, expected):
    assert ids.canonical_id(value) == expected


def test_canonical_id_sql_agrees_with_python():
    con = duckdb.connect()
    cases = {
        "BIGINT": [4374496, 0, None],
        "INTEGER": [12, None],
        "DOUBLE": [4374496.0, 12.5, None, float("nan")],
        "VARCHAR": [
            "4374496.0",
            " 17 ",
            "\t17\r",
            "\n",
            "",
            "MahoPa00",
            "St.BrAm00",
            "0012",
            "1.50",
            None,
        ],
    }
    for typ, values in cases.items():
        for v in values:
            sql = f"SELECT {ids.canonical_id_sql('x', typ)} FROM (SELECT CAST(? AS {typ}) AS x)"
            got = con.execute(sql, [v]).fetchone()[0]
            assert got == ids.canonical_id(v), (typ, v, got)
    with pytest.raises(TypeError):
        ids.canonical_id(True)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Odell Beckham Jr.", "odell beckham"),
        ("D'Andre Swift", "dandre swift"),
        ("A.J. Brown", "aj brown"),
        ("Amon-Ra St. Brown", "amonra st brown"),
        ("Zoë  Ñúñez", "zoe nunez"),  # accents and repeated spaces
        ("Will Fuller V", "will fuller"),
        ("Melvin Gordon III", "melvin gordon"),
        ("Ken Walker II", "ken walker"),
        ("V Sample", "v sample"),  # a suffix word is never dropped as the first word
        ("D’Andre Swift", "dandre swift"),  # typographic apostrophe
        ("", None),
        (None, None),
    ],
)
def test_normalize_name(name, expected):
    assert ids.normalize_name(name) == expected


# --------------------------------------------------------------------------------------
# Name passes (pure function)
# --------------------------------------------------------------------------------------


def _ff(rows):
    return pl.DataFrame(
        rows,
        schema={"rid": pl.Int64, "name": pl.String, "birthdate": pl.Date,
                "position": pl.String, "draft_year": pl.Int64},
        orient="row",
    )  # fmt: skip


def _pl(rows):
    return pl.DataFrame(
        rows,
        schema={"gsis_id": pl.String, "display_name": pl.String, "birth_date": pl.Date,
                "position_group": pl.String, "draft_year": pl.Int64},
        orient="row",
    )  # fmt: skip


def test_name_pass_a_needs_a_unique_name_and_birth_date():
    b = date(1996, 6, 6)
    players = _pl([
        ("00-G1", "Gamma Three", b, "RB", 2019),
        ("00-T1", "Twin Name", b, "WR", 2019),
        ("00-T2", "Twin Name", b, "WR", 2019),  # two players share name + birth date
    ])  # fmt: skip
    ff = _ff([(1, "Gamma Three Jr.", b, "RB", 2019), (2, "Twin Name", b, "WR", 2019)])
    got = ids.name_pass_links(ff, players)
    assert got.to_dicts() == [{"rid": 1, "gsis_id": "00-G1", "method": "name_birthdate"}]
    # the ff side must be unique too: two ff rows with the same key link nothing
    dup = _ff([(1, "Gamma Three", b, "RB", 2019), (2, "Gamma Three", b, "RB", 2019)])
    assert ids.name_pass_links(dup, players).is_empty()


def test_name_pass_b_uses_position_group_and_draft_year_and_respects_birth_dates():
    players = _pl([
        ("00-D4", "Delta Four", None, "TE", 2020),
        ("00-K1", "Kappa One", date(1990, 1, 1), "SPEC", 2012),
        ("00-S1", "Same Name", None, "DB", 2021),
        ("00-S2", "Same Name", None, "DB", 2021),
    ])  # fmt: skip
    ff = _ff([
        (1, "Delta Four", None, "TE", 2020),  # no birth date: pass B links
        (2, "Kappa One", date(1991, 2, 2), "PK", 2012),  # PK -> SPEC, but birth dates differ
        (3, "Same Name", None, "CB", 2021),  # two players: not unique
        (4, "Delta Four", None, "WR", 2020),  # wrong position group
    ])  # fmt: skip
    got = ids.name_pass_links(ff, players)
    assert got.to_dicts() == [{"rid": 1, "gsis_id": "00-D4", "method": "name_position_draft"}]


def test_name_passes_on_empty_frames():
    assert ids.name_pass_links(_ff([]), _pl([])).columns == ["rid", "gsis_id", "method"]


# --------------------------------------------------------------------------------------
# The bridge on a synthetic warehouse
# --------------------------------------------------------------------------------------

PLAYERS_IDS_DTYPES = {**PLAYERS_DTYPES, "espn_id": pl.String(), "last_season": pl.Int32()}


def _players() -> pl.DataFrame:
    return frame(
        [
            {
                "gsis_id": "00-0000001",
                "display_name": "Alpha One",
                "position": "QB",
                "position_group": "QB",
                "birth_date": "1990-01-01",
                "draft_year": 2012,
                "pfr_id": "OneAl00",
                "espn_id": "101",
            },
            {
                "gsis_id": "00-0000002",
                "display_name": "Beta Two",
                "position": "WR",
                "position_group": "WR",
                "birth_date": "1995-05-05",
                "draft_year": 2018,
                "pfr_id": "TwoBe00",
                "espn_id": "102",
            },
            {
                "gsis_id": "00-0000003",
                "display_name": "Gamma Three",
                "position": "RB",
                "position_group": "RB",
                "birth_date": "1996-06-06",
                "draft_year": 2019,
            },
            {
                "gsis_id": "00-0000004",
                "display_name": "Delta Four",
                "position": "TE",
                "position_group": "TE",
                "draft_year": 2026,
            },  # a future draft class
            {
                "gsis_id": "00-0000005",
                "display_name": "Epsilon Five",
                "position": "WR",
                "position_group": "WR",
                "birth_date": "1997-07-07",
                "draft_year": 2020,
            },
            {
                "gsis_id": "00-0000006",
                "display_name": "Epsilon Five",
                "position": "WR",
                "position_group": "WR",
                "birth_date": "1997-07-07",
                "draft_year": 2020,
            },
            {
                "gsis_id": "00-0000007",
                "display_name": "Zeta Seven",
                "position": "QB",
                "position_group": "QB",
                "draft_year": 2015,
            },
            {
                "gsis_id": "00-0000008",
                "display_name": "Eta Eight",
                "position": "WR",
                "position_group": "WR",
                "birth_date": "1998-08-08",
                "draft_year": 2021,
            },
            {
                "gsis_id": "00-0000009",
                "display_name": "Theta Nine",
                "position": "WR",
                "position_group": "WR",
                "birth_date": "1999-09-09",
                "draft_year": 2022,
            },
            # the usage check (snap counts): players gives Iota Old the PFR id the snaps use
            # for Iota New (career 1980 vs 2025), Kappa Gone's career ended in 2001 (no other
            # candidate), Lambda Swap is a linebacker the snaps list as a WR
            {
                "gsis_id": "00-0000010",
                "display_name": "Iota Old",
                "position": "QB",
                "position_group": "QB",
                "rookie_season": 1980,
                "last_season": 1980,
                "draft_year": 1980,
                "pfr_id": "OldIo00",
            },
            {
                "gsis_id": "00-0000011",
                "display_name": "Iota New",
                "position": "DT",
                "position_group": "DL",
                "rookie_season": 2024,
                "draft_year": 2024,
            },
            {
                "gsis_id": "00-0000012",
                "display_name": "Kappa Gone",
                "position": "OT",
                "position_group": "OL",
                "rookie_season": 2001,
                "last_season": 2001,
                "pfr_id": "GoneKa00",
            },
            {
                "gsis_id": "00-0000013",
                "display_name": "Lambda Swap",
                "position": "LB",
                "position_group": "LB",
                "rookie_season": 2015,
                "draft_year": 2015,
                "pfr_id": "SwapLa00",
            },
        ],  # fmt: skip
        PLAYERS_IDS_DTYPES,
    )


def _ff_playerids() -> pl.DataFrame:
    return frame(
        [
            # agrees with players on espn, adds Sleeper/FantasyPros/MFL ids
            {
                "gsis_id": "00-0000001",
                "name": "Alpha One",
                "position": "QB",
                "espn_id": 101,
                "fantasypros_id": 900001,
                "sleeper_id": 800001,
                "mfl_id": 700001,
                "yahoo_id": "600001",
                "sportradar_id": "sr-0001",
            },
            # an ff_playerids error: gives Beta Two the PFR id players gives Alpha One
            {
                "gsis_id": "00-0000002",
                "name": "Beta Two",
                "position": "WR",
                "pfr_id": "OneAl00",
                "fantasypros_id": 900002,
            },
            # one FantasyPros id on two players inside the only source that has it: ambiguous
            {
                "gsis_id": "00-0000007",
                "name": "Zeta Seven",
                "position": "QB",
                "fantasypros_id": 900010,
            },
            {
                "gsis_id": "00-0000008",
                "name": "Eta Eight",
                "position": "WR",
                "fantasypros_id": 900010,
            },
            # a second MFL id for Alpha One (dim_player keeps the smaller one)
            {"gsis_id": "00-0000001", "name": "Alpha One", "position": "QB", "mfl_id": 700000},
            # gsis_id missing upstream: the name passes
            {
                "name": "Gamma Three",
                "position": "RB",
                "birthdate": "1996-06-06",
                "draft_year": 2019,
                "fantasypros_id": 900003,
                "sleeper_id": 800003,
            },
            {"name": "Delta Four", "position": "TE", "draft_year": 2026, "fantasypros_id": 900004},
            {
                "name": "Epsilon Five",
                "position": "WR",
                "birthdate": "1997-07-07",
                "draft_year": 2020,
                "fantasypros_id": 900005,
            },
            # name + birth date say Eta Eight, but its ESPN id is Beta Two's: row rejected
            {
                "name": "Eta Eight",
                "position": "WR",
                "birthdate": "1998-08-08",
                "fantasypros_id": 900008,
                "espn_id": 102,
            },
            # Alpha One already has FantasyPros id 900001: this second one is not linked
            {
                "name": "Alpha One",
                "position": "QB",
                "birthdate": "1990-01-01",
                "fantasypros_id": 900099,
            },
            # both name-link Theta Nine (pass A: birth date, no draft year; pass B: draft
            # year, no birth date) and cancel each other: one player, two FantasyPros ids
            {
                "name": "Theta Nine",
                "position": "WR",
                "birthdate": "1999-09-09",
                "fantasypros_id": 900091,
            },
            {"name": "Theta Nine", "position": "WR", "draft_year": 2022, "fantasypros_id": 900092},
            # name + birth date say Beta Two, but its Sleeper id is known (only) as a roster
            # player missing from dim_player: an id link exists, so no name link
            {"name": "Beta Two", "position": "WR", "birthdate": "1995-05-05", "sleeper_id": 800077},
            # a player missing from dim_player also claims Beta Two's PFR id: still a conflict
            {"gsis_id": "00-9999998", "name": "Ghost Two", "position": "WR", "pfr_id": "TwoBe00"},
            # agrees with the weekly rosters: Iota Old's PFR id is Iota New's
            {"gsis_id": "00-0000011", "name": "Iota New", "position": "DT", "pfr_id": "OldIo00"},
        ],  # fmt: skip
        FF_PLAYERIDS_DTYPES,
    )


def _rosters(season: int) -> pl.DataFrame:
    rows = [
        {"season": season, "week": 1, "team": "KC", "position": "QB", "full_name": "Alpha One",
         "gsis_id": "00-0000001", "pfr_id": "OneAl00", "sleeper_id": "800001"},
        # agrees with the name pass for Gamma Three's Sleeper id
        {"season": season, "week": 1, "team": "KC", "position": "RB",
         "full_name": "Gamma Three", "gsis_id": "00-0000003", "sleeper_id": "800003.0"},
        # Sleeper id 800050: Alpha One in 2024, Beta Two in 2025 -> ambiguous
        {"season": season, "week": 1, "team": "KC", "position": "WR", "full_name": "x",
         "gsis_id": "00-0000001" if season == 2024 else "00-0000002", "sleeper_id": "800050"},
        # a roster player who is not in nflverse's players table: the link is dropped
        {"season": season, "week": 1, "team": "KC", "position": "WR", "full_name": "Ghost",
         "gsis_id": "00-9999999", "sleeper_id": "800077"},
        {"season": season, "week": 1, "team": "KC", "position": "DT", "full_name": "Iota New",
         "gsis_id": "00-0000011", "pfr_id": "OldIo00"},
    ]  # fmt: skip
    return frame(rows, ROSTERS_WEEKLY_DTYPES)


# Extra 2025 snap rows, all in the last game (after the as-of the harness tests use): the
# usage-check players and a kicker (unmatched, not a fantasy position).
USAGE_SNAPS = (("OldIo00", "I. New", "DT"), ("GoneKa00", "K. Gone", "DT"),
               ("SwapLa00", "L. Swap", "WR"), ("KickKi00", "K. Kick", "K"))  # fmt: skip


def _snaps(games):
    rows = []
    for g in games:
        players = [("OneAl00", "A. One", "QB"), ("NoSuch00", "N. Such", "QB")]
        if g["season"] == 2025 and g is games[-1]:
            players += USAGE_SNAPS
        for pfr, name, pos in players:
            rows.append(
                {"game_id": g["game_id"], "season": g["season"], "game_type": g["game_type"],
                 "week": g["week"], "player": name, "pfr_player_id": pfr, "position": pos,
                 "team": g["home_team"], "opponent": g["away_team"], "offense_snaps": 60.0,
                 "offense_pct": 1.0}
            )  # fmt: skip
    return rows


def _daily_charts() -> pl.DataFrame:
    base = {"dt": "2025-09-06T12:00:00Z", "team": "PHI", "pos_grp": "3WR 1TE",
            "pos_name": "Wide Receiver", "pos_abb": "WR", "pos_slot": 1}  # fmt: skip
    return frame(
        [
            {
                **base,
                "player_name": "Alpha One",
                "espn_id": "101",
                "gsis_id": "00-0000001",
                "pos_rank": 1,
            },
            {**base, "player_name": "Beta Two", "espn_id": "102", "gsis_id": None, "pos_rank": 2},
            {**base, "player_name": "Nobody", "espn_id": "999", "gsis_id": None, "pos_rank": 3},
        ],  # fmt: skip
        DAILY_DC_DTYPES,
    )


RANKINGS_ALL_DTYPES = {
    "fp_page": pl.String(),
    "page_type": pl.String(),
    "player": pl.String(),
    "id": pl.String(),
    "pos": pl.String(),
    "ecr": pl.Float64(),
    "ecr_type": pl.String(),
    "scrape_date": pl.String(),
}
RANKINGS_WEEK_DTYPES = {
    "fantasypros_id": pl.Int64(),
    "player_name": pl.String(),
    "pos": pl.String(),
    "scrape_date": pl.String(),
}


def _rankings_all() -> pl.DataFrame:
    qb = {"fp_page": "/nfl/rankings/qb-cheatsheets.php", "page_type": "redraft-qb",
          "pos": "QB", "ecr_type": "rp"}  # fmt: skip
    return frame(
        [
            # the last August/September cheat sheet before 2025 week 1 (first game 2025-09-04)
            {**qb, "player": "Alpha One", "id": "900001", "ecr": 1.0, "scrape_date": "2025-08-28"},
            {
                **qb,
                "player": "Nobody Known",
                "id": "900077",
                "ecr": 2.0,
                "scrape_date": "2025-08-28",
            },
            # a WR on an IDP page (its rank there is no WR rank): not in the pool, not in the
            # rankings coverage
            {
                "fp_page": "/nfl/rankings/db-cheatsheets.php",
                "page_type": "redraft-db",
                "pos": "WR",
                "ecr_type": "rp",
                "player": "Page Hopper",
                "id": "900088",
                "ecr": 1.0,
                "scrape_date": "2025-08-28",
            },
            # a lower-ranked QB: inside the config cutoff (QB 18), outside a cutoff of 1
            {**qb, "player": "Third Arm", "id": "900076", "ecr": 3.0, "scrape_date": "2025-08-28"},
            # an earlier scrape and one after week 1 are not the preseason pool
            {**qb, "player": "Early Bird", "id": "900078", "ecr": 1.0, "scrape_date": "2025-08-21"},
            {**qb, "player": "Late Comer", "id": "900079", "ecr": 1.0, "scrape_date": "2025-09-11"},
            # weekly rankings: one matched id, one not, a team defense ignored
            {
                "fp_page": "/nfl/rankings/qb.php",
                "page_type": "weekly-qb",
                "player": "Alpha One",
                "id": "900001.0",
                "pos": "QB",
                "ecr": 1.0,
                "ecr_type": "wp",
                "scrape_date": "2025-09-11",
            },
            {
                "fp_page": "/nfl/rankings/ppr-wr.php",
                "page_type": "weekly-wr",
                "player": "Eta Eight",
                "id": "900010",
                "pos": "WR",
                "ecr": 9.0,
                "ecr_type": "wp",
                "scrape_date": "2025-09-11",
            },
            {
                "fp_page": "/nfl/rankings/dst.php",
                "page_type": "weekly-dst",
                "player": "Team D",
                "id": "8000",
                "pos": "DST",
                "ecr": 1.0,
                "ecr_type": "wp",
                "scrape_date": "2025-09-11",
            },
        ],  # fmt: skip
        RANKINGS_ALL_DTYPES,
    )


def _write_overrides(rows):
    path = ids.overrides_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(ids.OVERRIDE_COLUMNS)] + [",".join(r) for r in rows]
    path.write_text("\n".join(lines) + "\n")
    return path


def _build_world(raw, db_path, *, overrides=None, tmp_path=None):
    raw.write("players", None, _players())
    raw.write("ff_playerids", None, _ff_playerids())
    raw.write("ff_rankings_all", None, _rankings_all())
    raw.write(
        "ff_rankings_week",
        None,
        frame(
            [{"fantasypros_id": 900001, "player_name": "Alpha One", "pos": "QB",
              "scrape_date": "2025-09-12"},
             {"fantasypros_id": 900005, "player_name": "Epsilon Five", "pos": "WR",
              "scrape_date": "2025-09-12"}],
            RANKINGS_WEEK_DTYPES,
        ),
    )  # fmt: skip
    g24 = [game(2024, 1, "2024-09-08", "13:00", "KC", "BAL", result=3)]
    legacy = legacy_depth_chart(g24).vstack(
        frame(
            [{"season": 2024, "club_code": "KC", "week": 1, "game_type": "REG",
              "depth_team": "2", "full_name": "No Gsis", "formation": "Offense",
              "position": "QB", "depth_position": "QB", "gsis_id": None}],
            LEGACY_DC_DTYPES,
        )
    )  # a legacy row without a gsis_id is never flagged gsis_id_from_espn  # fmt: skip
    raw.write_season(2024, g24, snaps=_snaps(g24), rosters=_rosters(2024), depth_charts=legacy)
    g25 = season_2025_games()
    raw.write_season(
        2025, g25, snaps=_snaps(g25), depth_charts=_daily_charts(), rosters=_rosters(2025)
    )
    if overrides is not None:
        _write_overrides(overrides)
    manifest = wb.build_warehouse([2024, 2025], db_path=db_path)
    return {m["table_name"]: m for m in manifest}


@pytest.fixture
def world(raw, db_path):
    manifest = _build_world(raw, db_path)
    con = duckdb.connect(str(db_path), read_only=True)
    yield con, manifest
    con.close()


def _link(con, id_type, source_id):
    return con.execute(
        "SELECT gsis_id, method, n_candidates, is_conflict FROM bridge_player_id "
        "WHERE id_type = ? AND source_id = ?",
        [id_type, source_id],
    ).fetchone()


def _issue(con, kind, id_type, source_id, dataset="bridge_player_id"):
    return con.execute(
        "SELECT gsis_id, name, detail, n_rows FROM report_id_unmatched "
        "WHERE kind = ? AND dataset = ? AND id_type = ? AND source_id = ?",
        [kind, dataset, id_type, source_id],
    ).fetchone()


def test_precedence_players_beat_ff_playerids_and_flag_the_conflict(world):
    con, _ = world
    assert _link(con, "pfr", "OneAl00") == ("00-0000001", "players", 2, True)
    assert _link(con, "espn", "101") == ("00-0000001", "players", 1, False)
    conflict = _issue(con, "conflict", "pfr", "OneAl00")
    assert conflict[0] == "00-0000001" and conflict[1] == "Alpha One"
    assert conflict[2].startswith("precedence picked players; candidates: ")
    assert "ff_playerids=00-0000002 (Beta Two)" in conflict[2]
    # a candidate missing from dim_player still counts as a disagreement
    assert _link(con, "pfr", "TwoBe00") == ("00-0000002", "players", 2, True)
    assert "ff_playerids=00-9999998 (not in dim_player)" in _issue(con, "conflict", "pfr",
                                                                   "TwoBe00")[2]  # fmt: skip
    # ids only ff_playerids knows come from there
    assert _link(con, "fantasypros", "900001") == ("00-0000001", "ff_playerids", 1, False)
    assert _link(con, "sleeper", "800001")[1] == "rosters_weekly"  # rosters outrank ff


def test_a_duplicate_inside_the_winning_source_is_ambiguous_and_left_out(world):
    con, manifest = world
    assert _link(con, "fantasypros", "900010") is None
    detail = _issue(con, "ambiguous", "fantasypros", "900010")[2]
    assert "00-0000007 (Zeta Seven)" in detail and "00-0000008 (Eta Eight)" in detail
    assert json.loads(manifest["bridge_player_id"]["notes"])["n_ambiguous"] >= 2


def test_rosters_weekly_cross_season_disagreement_is_ambiguous(world):
    con, _ = world
    assert _link(con, "sleeper", "800050") is None
    detail = _issue(con, "ambiguous", "sleeper", "800050")[2]
    assert "rosters_weekly names 2 players" in detail


def test_links_to_players_missing_from_dim_player_are_dropped_and_counted(world):
    con, manifest = world
    assert _link(con, "sleeper", "800077") is None
    notes = json.loads(manifest["bridge_player_id"]["notes"])
    assert notes["n_links_to_gsis_not_in_dim_player"] == {"ff_playerids": 1, "rosters_weekly": 1}
    assert con.execute(
        "SELECT count(*) FROM bridge_player_id WHERE gsis_id NOT IN "
        "(SELECT gsis_id FROM dim_player)"
    ).fetchone() == (0,)


def test_name_passes_link_unique_players_and_never_override_id_links(world):
    con, manifest = world
    assert _link(con, "fantasypros", "900003") == ("00-0000003", "name_birthdate", 1, False)
    assert _link(con, "fantasypros", "900004") == ("00-0000004", "name_position_draft", 1,
                                                   False)  # fmt: skip
    # the id link (rosters_weekly, same player) is kept for Gamma Three's Sleeper id
    assert _link(con, "sleeper", "800003") == ("00-0000003", "rosters_weekly", 1, False)
    assert _link(con, "fantasypros", "900005") is None  # two Epsilon Fives
    assert _link(con, "fantasypros", "900008") is None  # its ESPN id is Beta Two's
    assert _link(con, "fantasypros", "900099") is None  # Alpha One already has one
    evidence = _issue(con, "name_link", "fantasypros", "900003")
    assert evidence[0] == "00-0000003"
    assert evidence[2] == (
        "name_birthdate: ff_playerids row Gamma Three, born 1996-06-06, draft year 2019"
    )
    # an id known only through a player missing from dim_player is still an id link
    assert _link(con, "sleeper", "800077") is None
    notes = json.loads(manifest["bridge_player_id"]["notes"])
    assert notes["name_pass_rows_linked"] == {"name_birthdate": 5, "name_position_draft": 2}
    assert notes["name_pass_links_rejected"] == {
        "ambiguous": 2,  # Theta Nine: pass A and pass B each link him
        "contradicts_id_link": 2,  # Eta Eight's row: its FantasyPros and its ESPN id
        "id_link_exists": 2,  # Gamma Three's Sleeper id; Beta Two's row's Sleeper id
        "player_has_other_id_of_type": 1,  # Alpha One's second FantasyPros id
    }


def test_two_name_links_to_one_player_cancel_each_other(world):
    """Pass B leaves out only the rows pass A linked, not their players: the 'ambiguous'
    rule is what stops Theta Nine getting two FantasyPros ids."""
    con, _ = world
    assert _link(con, "fantasypros", "900091") is None
    assert _link(con, "fantasypros", "900092") is None
    for fp_id in ("900091", "900092"):
        detail = _issue(con, "ambiguous", "fantasypros", fp_id)[2]
        assert "00-0000009 (Theta Nine)" in detail, detail
    assert con.execute(
        "SELECT fantasypros_id FROM dim_player WHERE gsis_id = '00-0000009'"
    ).fetchone() == (None,)


def test_dim_player_gets_one_id_per_system(world):
    con, manifest = world
    rows = {
        r[0]: r[1:]
        for r in con.execute(
            "SELECT gsis_id, pfr_id, espn_id, sleeper_id, fantasypros_id, yahoo_id, "
            "sportradar_id, mfl_id FROM dim_player ORDER BY gsis_id"
        ).fetchall()
    }
    assert rows["00-0000001"] == ("OneAl00", "101", "800001", "900001", "600001", "sr-0001",
                                  "700000")  # fmt: skip
    assert rows["00-0000003"] == (None, None, "800003", "900003", None, None, None)
    assert rows["00-0000002"][0] == "TwoBe00"  # players' own PFR id, not ff's wrong one
    several = _issue(con, "multiple_ids", "mfl", "700001")
    assert several[0] == "00-0000001" and several[2].startswith("dim_player keeps 700000")
    notes = json.loads(manifest["dim_player"]["notes"])
    assert notes["n_players_with_several_ids"] == {"mfl": 1}
    # the view shows the ids (identifiers, not features)
    assert set(sc.DIM_PLAYER_ID_COLUMNS) <= set(av.DIM_PLAYER_VISIBLE)


def test_fact_snaps_gets_gsis_through_the_bridge(world):
    con, manifest = world
    got = con.execute(
        "SELECT pfr_player_id, gsis_id, count(*) FROM fact_snaps GROUP BY 1, 2 ORDER BY 1"
    ).fetchall()
    assert got == [
        ("GoneKa00", None, 1),  # implausible link (usage check): no gsis_id
        ("KickKi00", None, 1),
        ("NoSuch00", None, 15),
        ("OldIo00", "00-0000011", 1),  # re-linked by the usage check
        ("OneAl00", "00-0000001", 15),
        ("SwapLa00", "00-0000013", 1),  # position suspect only: kept
    ]
    cols = [r[0] for r in con.execute("DESCRIBE fact_snaps").fetchall()]
    assert cols.index("gsis_id") == cols.index("pfr_player_id") + 1
    notes = json.loads(manifest["fact_snaps"]["notes"])
    assert notes["n_rows_without_gsis_id"] == 17
    assert notes["n_duplicate_game_gsis"] == 0


def test_usage_check_relinks_a_players_link_used_outside_the_career(world):
    """players gives Iota Old (career 1980) the PFR id the 2025 snaps use; the weekly
    rosters and ff_playerids agree on Iota New, whose career fits: re-linked and reported."""
    con, manifest = world
    assert _link(con, "pfr", "OldIo00") == ("00-0000011", "rosters_weekly_usage_override", 2,
                                            True)  # fmt: skip
    suspect = _issue(con, "suspect", "pfr", "OldIo00", dataset="fact_snaps")
    assert suspect[:2] == ("00-0000011", "Iota New") and suspect[3] == 1
    assert suspect[2].startswith(
        "career: players links 00-0000010 (Iota Old, QB, career 1980-1980) but fact_snaps "
        "uses this id in 2025 (1 rows as DT): re-linked to rosters_weekly=00-0000011 "
        "(Iota New, DT, career 2024-?)"
    ), suspect[2]
    # the conflict row no longer reads as if the precedence pick were safe
    conflict = _issue(con, "conflict", "pfr", "OldIo00")
    assert conflict[:2] == ("00-0000011", "Iota New")
    assert conflict[2].startswith(
        "usage contradicts the precedence pick (career in fact_snaps, see kind suspect): "
        "re-linked to rosters_weekly=00-0000011; candidates: players=00-0000010 (Iota Old)"
    ), conflict[2]
    # dim_player follows the bridge
    pfr = dict(con.execute(
        "SELECT gsis_id, pfr_id FROM dim_player WHERE gsis_id IN ('00-0000010', '00-0000011')"
    ).fetchall())  # fmt: skip
    assert pfr == {"00-0000010": None, "00-0000011": "OldIo00"}
    notes = json.loads(manifest["fact_snaps"]["notes"])["usage_check"]
    assert notes == {"id_type": "pfr", "n_ids_relinked": 1, "n_ids_career_suspect_nulled": 1,
                     "n_rows_gsis_nulled": 1, "n_ids_position_suspect": 1}  # fmt: skip
    by_method = json.loads(manifest["bridge_player_id"]["notes"])["n_links_by_method"]
    assert by_method["rosters_weekly_usage_override"] == 1


def test_usage_check_without_an_alternative_leaves_the_rows_unlinked(world):
    """Kappa Gone's career ended in 2001; the 2025 snaps of his PFR id are not his and no
    other source names anyone: those rows get no gsis_id, the bridge link stays, and his
    public_from_utc does not come from them."""
    con, _ = world
    assert _link(con, "pfr", "GoneKa00") == ("00-0000012", "players", 1, False)
    assert con.execute(
        "SELECT gsis_id FROM fact_snaps WHERE pfr_player_id = 'GoneKa00'"
    ).fetchall() == [(None,)]
    suspect = _issue(con, "suspect", "pfr", "GoneKa00", dataset="fact_snaps")
    assert suspect[0] == "00-0000012" and suspect[3] == 1
    assert (
        "fact_snaps.gsis_id is NULL for those rows (no other source names another player)"
        in (suspect[2])
    )
    # undrafted, and his only data rows were the implausible ones: never public
    assert con.execute(
        "SELECT public_from_utc FROM dim_player WHERE gsis_id = '00-0000012'"
    ).fetchone() == (None,)
    assert con.execute(
        "SELECT public_from_utc FROM bridge_player_id WHERE source_id = 'GoneKa00'"
    ).fetchone() == (None,)
    # the report counts those rows as unmatched and says why
    unmatched = _issue(con, "unmatched", "pfr", "GoneKa00", dataset="fact_snaps")
    assert "implausible" in unmatched[2] and unmatched[3] == 1


def test_usage_check_reports_a_position_mismatch_but_keeps_the_link(world):
    con, _ = world
    assert _link(con, "pfr", "SwapLa00") == ("00-0000013", "players", 1, False)
    suspect = _issue(con, "suspect", "pfr", "SwapLa00", dataset="fact_snaps")
    assert suspect[0] == "00-0000013"
    assert suspect[2].startswith(
        "position: fact_snaps lists this id only as WR (offense) but players links "
        "00-0000013 (Lambda Swap, LB, career 2015-?, defense): link kept"
    ), suspect[2]
    assert con.execute(
        "SELECT count(*) FROM report_id_unmatched WHERE kind = 'suspect'"
    ).fetchone() == (3,)


def test_daily_depth_chart_gsis_is_filled_from_espn_with_a_flag(world):
    con, manifest = world
    got = con.execute(
        "SELECT espn_id, gsis_id, gsis_id_from_espn FROM fact_depth_chart "
        "WHERE source_format = 'daily' ORDER BY espn_id"
    ).fetchall()
    assert got == [("101", "00-0000001", False), ("102", "00-0000002", True),
                   ("999", None, False)]  # fmt: skip
    assert con.execute(
        "SELECT count(*) FROM fact_depth_chart WHERE source_format = 'legacy' AND gsis_id_from_espn"
    ).fetchone() == (0,)
    # ... also for a legacy row without a gsis_id
    assert con.execute(
        "SELECT count(*) FROM fact_depth_chart WHERE source_format = 'legacy' AND gsis_id IS NULL"
    ).fetchone() == (1,)
    notes = json.loads(manifest["fact_depth_chart"]["notes"])
    assert (notes["n_daily_gsis_filled_from_espn"], notes["n_daily_espn_without_gsis"]) == (1, 1)


def test_report_tables_count_the_known_unmatched_ids(world):
    con, manifest = world

    def cov(dataset, season, scope="all"):
        return con.execute(
            "SELECT n_rows, n_rows_unmatched, n_ids, n_ids_unmatched, match_rate "
            "FROM report_id_coverage WHERE dataset = ? AND season = ? AND scope = ?",
            [dataset, season, scope],
        ).fetchone()

    # 2025: OneAl00, NoSuch00 (unmatched) x 14 games + the usage rows (GoneKa00 nulled) and a
    # kicker (unmatched, not a fantasy position)
    assert cov("fact_snaps", 2025) == (32, 16, 6, 3, 0.5)
    assert cov("fact_snaps", 2025, "fantasy") == (29, 14, 3, 1, 0.517241)
    assert cov("fact_snaps", 2024) == (2, 1, 2, 1, 0.5)
    unmatched = _issue(con, "unmatched", "pfr", "NoSuch00", dataset="fact_snaps")
    assert unmatched[1] == "N. Such" and unmatched[3] == 15
    # daily depth-chart rows without an upstream gsis: 1 of 2 filled from ESPN
    assert cov("fact_depth_chart[daily_no_gsis]", 2025) == (2, 1, 2, 1, 0.5)
    # raw cache: weekly rankings (team defenses left out) and the preseason pool
    assert cov("ff_rankings_all[wp]", 2025, "fantasy") == (2, 1, 2, 1, 0.5)
    assert cov("ff_rankings_all[wp]", 2025) == (2, 1, 2, 1, 0.5)  # the DST row is not counted
    # rp: every cheat-sheet row except the IDP page (Page Hopper)
    assert cov("ff_rankings_all[rp]", 2025, "fantasy") == (5, 4, 5, 4, 0.2)
    assert cov("ff_rankings_week", 2025, "fantasy") == (2, 1, 2, 1, 0.5)
    assert cov(ids.POOL_LABEL, 2025, "fantasy") == (3, 2, 3, 2, 0.333333)
    assert cov(ids.POOL_LABEL, 2025) is None  # the pool is reported for QB/RB/WR/TE only
    pool = con.execute(
        "SELECT source_id, name FROM report_id_unmatched WHERE dataset = ? ORDER BY source_id",
        [ids.POOL_LABEL],
    ).fetchall()
    assert pool == [("900076", "Third Arm"), ("900077", "Nobody Known")]
    # an unmatched id the bridge knows as ambiguous says so
    assert "ambiguous" in _issue(con, "unmatched", "fantasypros", "900010",
                                 dataset="ff_rankings_all[wp]")[2]  # fmt: skip
    assert _issue(con, "unmatched", "fantasypros", "900005", dataset="ff_rankings_week")[2] is None
    # a dataset that is not cached is skipped and noted, never fetched (ff_opportunity is a
    # build input since C3, so the fixture always writes it; NGS is never written)
    notes = json.loads(manifest["report_id_coverage"]["notes"])
    assert notes["coverage_datasets_skipped"]["ngs_passing"] == [2024, 2025]
    assert "ff_opportunity" not in notes["coverage_datasets_skipped"]
    assert notes["preseason_pool_unmatched"] == {"2025": "2/3"}
    assert notes["fact_snaps_fantasy_match_rate"] == {"2024": 0.5, "2025": 0.517241}
    assert json.loads(manifest["report_id_unmatched"]["notes"])["n_rows_by_kind"]["conflict"] >= 1


def test_pool_keeps_only_positional_pages_and_the_cutoff(world, db_path):
    """The pool query on its own: a cutoff of 1 QB keeps only the best-ranked QB, and a
    player listed on an IDP page is never in it."""
    con = duckdb.connect()
    con.execute(f"ATTACH {sc.sql_str(str(db_path))} AS wh (READ_ONLY)")
    con.execute("CREATE VIEW dim_week AS SELECT * FROM wh.dim_week")
    path = nv.cache_path("ff_rankings_all", None)
    con.execute(
        f"CREATE VIEW raw_ff_rankings_all AS SELECT * FROM read_parquet({sc.sql_str(str(path))})"
    )
    for cutoffs, want in (
        ({"QB": 1, "WR": 36}, [(2025, "900001", "Alpha One", "QB")]),
        ({"QB": 2, "WR": 36}, [(2025, "900001", "Alpha One", "QB"),
                               (2025, "900077", "Nobody Known", "QB")]),
    ):  # fmt: skip
        pool = ids.pool_coverage(con, cutoffs)
        assert con.execute(f"SELECT * FROM ({pool.rows_sql}) ORDER BY 2").fetchall() == want
    con.close()


def test_new_tables_are_registered_with_the_right_kind():
    assert av.kind_of("bridge_player_id") == "hindsight"
    assert av.kind_of("report_id_coverage") == "meta"
    assert av.kind_of("report_id_unmatched") == "meta"
    bridge = av.TABLE_AVAILABILITY["bridge_player_id"]
    cols = set(sc.tables()["bridge_player_id"].column_names)
    assert set(bridge.visible_columns) | set(bridge.hidden_columns) == cols
    assert not set(bridge.visible_columns) & set(bridge.hidden_columns)


# --------------------------------------------------------------------------------------
# Manual overrides
# --------------------------------------------------------------------------------------


def test_manual_override_wins_and_resolves_an_ambiguous_id(raw, db_path, tmp_path):
    manifest = _build_world(
        raw,
        db_path,
        tmp_path=tmp_path,
        overrides=[
            ("sleeper", "800050", "00-0000002", "checked the roster pages", "owner"),
            ("pfr", "OneAl00", "00-0000007", "synthetic: moves a players link", "owner"),
        ],
    )
    con = duckdb.connect(str(db_path), read_only=True)
    assert _link(con, "sleeper", "800050") == ("00-0000002", "manual", 2, True)
    assert _link(con, "pfr", "OneAl00") == ("00-0000007", "manual", 3, True)
    # dim_player follows the bridge: Alpha One lost the PFR id to the override
    assert con.execute("SELECT gsis_id FROM dim_player WHERE pfr_id = 'OneAl00'").fetchall() == [
        ("00-0000007",)
    ]
    assert json.loads(manifest["bridge_player_id"]["notes"])["n_manual_overrides"] == 2
    con.close()


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (("madeup", "1", "00-0000001", "", ""), "unknown id_type 'madeup'"),
        (("sleeper", "1", "00-4040404", "", ""), "gsis_id '00-4040404' is not in dim_player"),
        (("sleeper", "", "00-0000001", "", ""), "source_id and gsis_id are required"),
    ],
)
def test_invalid_overrides_stop_the_build_with_a_clear_error(raw, db_path, tmp_path, row, message):
    with pytest.raises(ids.IdOverrideError, match=message):
        _build_world(raw, db_path, tmp_path=tmp_path, overrides=[row])
    assert not db_path.exists()  # rolled back, nothing written


def test_duplicate_override_keys_and_a_wrong_header_are_refused(tmp_path):
    path = tmp_path / "o.csv"
    path.write_text(
        "id_type,source_id,gsis_id,note,verified_by\n"
        "sleeper,800050,00-0000001,,\nsleeper,800050.0,00-0000002,,\n"
    )
    with pytest.raises(ids.IdOverrideError, match=r"\(sleeper, 800050\) repeats line 2"):
        ids.read_overrides(path, ["00-0000001", "00-0000002"])
    path.write_text("id_type,source_id,gsis\n")
    with pytest.raises(ids.IdOverrideError, match="the header must be"):
        ids.read_overrides(path, [])
    assert ids.read_overrides(tmp_path / "missing.csv", []) == []


def test_the_committed_override_file_is_valid():
    """data/manual/player_id_overrides.csv always parses, and every row names a known id
    type, a non-empty id and a gsis_id of the NFL's form (the build checks that the player
    exists in dim_player)."""
    from twm.config import settings

    path = settings().path("manual") / ids.OVERRIDES_FILE
    assert path.read_text().splitlines()[0] == ",".join(ids.OVERRIDE_COLUMNS)
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        assert re.fullmatch(r"00-\d{7}", r["gsis_id"].strip()), r
        assert r["id_type"].strip() in ids.ID_TYPES, r
        assert ids.canonical_id(r["source_id"]) is not None, r
    assert ids.read_overrides(path, [r["gsis_id"].strip() for r in rows]) == [
        {**r, "note": r["note"] or None, "verified_by": r["verified_by"] or None} for r in rows
    ]


# --------------------------------------------------------------------------------------
# Point in time
# --------------------------------------------------------------------------------------


def test_asof_view_hides_a_future_players_bridge_rows(world, db_path):
    as_of = datetime(2025, 9, 9, 14, tzinfo=UTC)  # Delta Four is drafted in 2026
    with AsOfView(db_path, as_of) as v:
        assert v.table("bridge_player_id").columns == list(av.BRIDGE_VISIBLE)
        seen = set(v.sql("SELECT gsis_id FROM bridge_player_id")["gsis_id"])
        assert "00-0000004" not in seen and "00-0000001" in seen
        assert v.sql("SELECT count(*) AS n FROM wh.bridge_player_id WHERE gsis_id = "
                     "'00-0000004'").item() == 1  # fmt: skip
        with pytest.raises(duckdb.BinderException):
            v.sql("SELECT method FROM bridge_player_id")
    with AsOfView(db_path, datetime(2026, 6, 1, tzinfo=UTC)) as v:
        assert v.sql("SELECT count(*) AS n FROM bridge_player_id WHERE gsis_id = "
                     "'00-0000004'").item() == 1  # fmt: skip


def test_bridge_rows_carry_the_players_row_time(world):
    con, manifest = world
    assert con.execute(
        "SELECT count(*) FROM bridge_player_id b JOIN dim_player p USING (gsis_id) "
        "WHERE b.public_from_utc IS DISTINCT FROM p.public_from_utc"
    ).fetchone() == (0,)
    counts = json.loads(manifest["bridge_player_id"]["notes"])["public_from_utc_rule_counts"]
    assert set(counts) == {"never", "player_public"}


def test_harness_passes_a_builder_that_maps_snaps_through_the_bridge(world, db_path):
    as_of = datetime(2025, 9, 9, 14, tzinfo=UTC)

    def snaps_by_player(v):
        return v.sql(
            "SELECT b.gsis_id, sum(s.offense_snaps) AS snaps FROM fact_snaps s "
            "JOIN bridge_player_id b ON b.id_type = 'pfr' AND b.source_id = s.pfr_player_id "
            "GROUP BY 1"
        )

    out = assert_future_invariant(snaps_by_player, db_path, as_of, key=["gsis_id"])
    assert out.to_dicts() == [{"gsis_id": "00-0000001", "snaps": 60.0 * 5}]

    def future_ids(v):  # reads a future player's link through the bypass
        return v.sql("SELECT id_type, source_id, gsis_id FROM wh.bridge_player_id")

    with pytest.raises(LeakageError):
        assert_future_invariant(future_ids, db_path, as_of, key=["id_type", "source_id"])


# --------------------------------------------------------------------------------------
# Determinism, twm ids, twm doctor
# --------------------------------------------------------------------------------------


def test_bridge_and_report_are_deterministic_under_row_order(raw, db_path, tmp_path):
    first = _build_world(raw, db_path)
    raw.write("ff_playerids", None, _ff_playerids().reverse())
    raw.write("players", None, _players().reverse())
    second = wb.build_warehouse([2024, 2025], db_path=tmp_path / "again.duckdb")
    again = {m["table_name"]: m["content_hash"] for m in second}
    for name in ("bridge_player_id", "dim_player", "fact_snaps", "fact_depth_chart",
                 "report_id_coverage", "report_id_unmatched"):  # fmt: skip
        assert first[name]["content_hash"] == again[name], name


def test_twm_ids_writes_the_markdown_report(world, db_path, tmp_path):
    out = tmp_path / "reports" / "unmatched_ids.md"
    result = CliRunner().invoke(app, ["ids", "--db", str(db_path), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "ids: snaps QB/RB/WR/TE 51.61% matched" in result.output
    assert "3 suspect" in result.output
    text = out.read_text()
    assert text.startswith("# Player ID coverage")
    assert text.index("## Headline") < text.index("## Suspect links") < text.index("## Coverage")
    for needle in (
        "## Coverage",
        "| 2025 | 51.72% | 2/3 | 50.00% |",  # the headline row
        "## Suspect links (check these first): 3",
        "| fact_snaps | pfr | all | 2025 | 32 | 16 |",
        "Nobody Known",
        "## Ambiguous ids (no link): ",
        "## Conflicts",
        "Gamma Three | 00-0000003 | name_birthdate",
        "fantasypros 900003",
    ):
        assert needle in text, needle  # fmt: skip
    # deterministic: a second run writes the same bytes
    CliRunner().invoke(app, ["ids", "--db", str(db_path), "--out", str(out)])
    assert out.read_text() == text


def test_twm_ids_explains_a_missing_or_old_warehouse(tmp_path):
    result = CliRunner().invoke(app, ["ids", "--db", str(tmp_path / "none.duckdb")])
    assert result.exit_code == 1 and "run `twm build` first" in result.output
    old = tmp_path / "old.duckdb"
    duckdb.connect(str(old)).close()
    result = CliRunner().invoke(app, ["ids", "--db", str(old), "--out", str(tmp_path / "x.md")])
    assert result.exit_code == 1 and "built before B3" in result.output


def test_doctor_prints_one_id_coverage_line(world, db_path, monkeypatch):
    from twm.config import settings

    s = settings()
    monkeypatch.setattr(s.paths, "warehouse", str(db_path))
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    lines = [x.strip() for x in result.output.splitlines() if x.strip().startswith("ids: ")]
    assert len(lines) == 1 and "ambiguous" in lines[0] and "conflicts" in lines[0]


# --------------------------------------------------------------------------------------
# Real data (opt-in: `uv run pytest -m realdata`; reads the cache, never downloads)
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_full(real_full_db):
    """The shared full 1999-2026 build (tests/conftest.py), opened read-only."""
    path, _ = real_full_db
    con = duckdb.connect(str(path), read_only=True)
    yield con
    con.close()


@pytest.mark.realdata
def test_real_snap_counts_match_in_every_season(real_full):
    rates = dict(
        real_full.execute(
            "SELECT season, match_rate FROM report_id_coverage WHERE dataset = 'fact_snaps' "
            "AND scope = 'fantasy' ORDER BY season"
        ).fetchall()
    )
    assert sorted(rates) == list(range(2013, 2027))
    assert min(rates.values()) >= 0.995, rates


@pytest.mark.realdata
def test_real_preseason_candidate_pool_maps_completely(real_full):
    pool = real_full.execute(
        "SELECT season, n_ids, n_ids_unmatched FROM report_id_coverage WHERE dataset = ? "
        "ORDER BY season",
        [ids.POOL_LABEL],
    ).fetchall()
    assert [s for s, *_ in pool] == list(range(2020, 2027))
    assert all(n == 108 for _, n, _ in pool)  # QB 18 + RB 36 + WR 36 + TE 18
    missing = real_full.execute(
        "SELECT season_min, position, name, source_id FROM report_id_unmatched WHERE dataset = ?",
        [ids.POOL_LABEL],
    ).fetchall()
    assert missing == []


@pytest.mark.realdata
def test_real_bridge_invariants(real_full):
    con = real_full
    assert con.execute(
        "SELECT count(*) FROM bridge_player_id WHERE gsis_id NOT IN "
        "(SELECT gsis_id FROM dim_player)"
    ).fetchone() == (0,)
    for table in av.event_tables():
        assert con.execute(
            f"SELECT count(*) FROM {table} WHERE available_at IS NULL"
        ).fetchone() == (0,), table
    # weekly wp rankings: every season is reported (target 99% of rows; see docs)
    wp = dict(
        con.execute(
            "SELECT season, match_rate FROM report_id_coverage "
            "WHERE dataset = 'ff_rankings_all[wp]' AND scope = 'fantasy'"
        ).fetchall()
    )
    assert set(wp) >= set(range(2020, 2027)) and min(wp.values()) > 0.95
    # feature joins on (game_id, gsis_id) are safe, and no snap row is linked to a player
    # outside his career window (the usage check)
    assert con.execute(
        "SELECT count(*) FROM (SELECT 1 FROM fact_snaps WHERE gsis_id IS NOT NULL "
        "GROUP BY game_id, gsis_id HAVING count(*) > 1)"
    ).fetchone() == (0,)
    assert con.execute(
        "SELECT count(*) FROM fact_snaps s JOIN dim_player p USING (gsis_id) "
        f"WHERE NOT {ids._fits_career_sql('s.season', 'p')}"
    ).fetchone() == (0,)
    assert con.execute(
        "SELECT count(*) FROM report_id_unmatched WHERE kind = 'suspect' "
        "AND NOT (detail LIKE 'career: %' OR detail LIKE 'position: %')"
    ).fetchone() == (0,)


@pytest.mark.realdata
def test_real_pool_rows_come_from_their_own_positional_page(real_full_db):
    """Every candidate-pool player is ranked on his own position's cheat sheet in the pool's
    scrape (never only on an IDP or another position's page)."""
    from twm.config import league

    path, _ = real_full_db
    lg = league()
    cutoffs = lg.candidate_pool_cutoffs()
    con = duckdb.connect()
    con.execute(f"ATTACH {sc.sql_str(str(path))} AS wh (READ_ONLY)")
    con.execute("CREATE VIEW dim_week AS SELECT * FROM wh.dim_week")
    raw = nv.cache_path("ff_rankings_all", None)
    con.execute(
        f"CREATE VIEW raw_ff_rankings_all AS SELECT * FROM read_parquet({sc.sql_str(str(raw))})"
    )
    pool = ids.pool_coverage(con, cutoffs)
    n_rows, n_own = con.execute(f"""
        WITH pool AS ({pool.rows_sql})
        SELECT count(*), count(*) FILTER (WHERE EXISTS (
            SELECT 1 FROM raw_ff_rankings_all r
            WHERE {ids.canonical_id_sql("r.id", "VARCHAR")} = pool.source_id
              AND r.pos = pool.position AND r.ecr_type = 'rp'
              AND CAST(substr(r.scrape_date, 1, 4) AS INTEGER) = pool.season
              AND regexp_extract(r.fp_page, '{ids.POOL_PAGE_RE}', 1) = lower(r.pos)))
        FROM pool""").fetchone()
    assert n_rows == 7 * 108 and n_own == n_rows
    con.close()
