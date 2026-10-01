"""H1b: cited head-coach corrections (data/manual/coach_corrections.csv), synthetic and offline."""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import duckdb
import polars as pl
import pytest

from tests.conftest import game
from twm.warehouse import build as wb
from twm.warehouse import coach_corrections as cc

URL = "https://en.wikipedia.org/wiki/Example"


def _games(rows: list[tuple[str, str, str, str, str, str]]) -> pl.DataFrame:
    """(game_id, gameday, home_team, away_team, home_coach, away_coach), season 2024."""
    return pl.DataFrame(
        [{"game_id": g, "season": 2024, "gameday": date.fromisoformat(d), "home_team": h,
          "away_team": a, "home_coach": hc, "away_coach": ac} for g, d, h, a, hc, ac in rows]
    )  # fmt: skip


# NYJ: Saleh listed all season (fired 10-08 in this world); ATL: the change one game early
GAMES = _games([
    ("g1", "2024-09-08", "NYJ", "BUF", "Robert Saleh", "Sean McDermott"),
    ("g2", "2024-10-06", "MIN", "NYJ", "Kevin O'Connell", "Robert Saleh"),
    ("g3", "2024-10-14", "NYJ", "BUF", "Robert Saleh", "Sean McDermott"),
    ("g4", "2024-10-20", "PIT", "NYJ", "Mike Tomlin", "Robert Saleh"),
    ("g0", "2024-12-02", "ATL", "NO", "Bobby Petrino", "Dennis Allen"),
    ("g5", "2024-12-09", "ATL", "LV", "Emmitt Thomas", "Klint Kubliak"),
    ("g6", "2024-12-16", "LV", "ATL", "Klint Kubliak", "Emmitt Thomas"),
])  # fmt: skip


def _row(kind: str, season: int | None, team: str | None, out: str, into: str,
         day: str | None = None, line: int = 2) -> cc.Correction:  # fmt: skip
    return cc.Correction(line, kind, season, team, out, into,
                         date.fromisoformat(day) if day else None, URL)  # fmt: skip


def _coaches(df: pl.DataFrame) -> dict[str, tuple[str, str]]:
    return {g: (h, a) for g, h, a in df.select("game_id", "home_coach", "away_coach").rows()}


def test_from_date_adds_a_missing_in_season_change():
    out, applied = cc.apply_corrections(
        GAMES, [_row("from_date", 2024, "NYJ", "Robert Saleh", "Jeff Ulbrich", "2024-10-08")]
    )
    c = _coaches(out)
    assert c["g1"][0] == c["g2"][1] == "Robert Saleh"  # on or before the date
    assert c["g3"][0] == c["g4"][1] == "Jeff Ulbrich"  # every later US-Eastern date
    assert c["g3"][1] == "Sean McDermott"  # the opponent untouched
    assert applied == [{"line": 2, "kind": "from_date", "season": 2024, "team": "NYJ",
                        "coach_out": "Robert Saleh", "coach_in": "Jeff Ulbrich",
                        "from_date": "2024-10-08", "games": 2}]  # fmt: skip


def test_from_date_moves_a_change_listed_one_game_early():
    # the schedule credits the interim with the 12-09 game; the coach resigned on 12-10
    out, applied = cc.apply_corrections(
        GAMES, [_row("from_date", 2024, "ATL", "Bobby Petrino", "Emmitt Thomas", "2024-12-10")]
    )
    c = _coaches(out)
    assert c["g0"][0] == c["g5"][0] == "Bobby Petrino" and c["g6"][1] == "Emmitt Thomas"
    assert applied[0]["games"] == 1


def test_season_and_rename_rows():
    rows = [
        _row("season", 2024, "NYJ", "Robert Saleh", "Aaron Glenn", line=2),
        _row("rename", None, None, "Klint Kubliak", "Klint Kubiak", line=3),
        _row("season", 2024, "ATL", "Emmitt Thomas", "Raheem Morris", "2024-12-10", line=4),
    ]
    out, applied = cc.apply_corrections(GAMES, rows)
    c = _coaches(out)
    assert {c[g][0] for g in ("g1", "g3")} | {c[g][1] for g in ("g2", "g4")} == {"Aaron Glenn"}
    assert c["g5"][1] == c["g6"][0] == "Klint Kubiak"
    assert c["g5"][0] == "Emmitt Thomas" and c["g6"][1] == "Raheem Morris"  # after the date
    assert [a["games"] for a in applied] == [4, 2, 1]
    assert out.columns == GAMES.columns and out["game_id"].to_list() == GAMES["game_id"].to_list()


def test_no_rows_returns_the_games_untouched():
    out, applied = cc.apply_corrections(GAMES, [])
    assert out.equals(GAMES) and applied == []


@pytest.mark.parametrize(("row", "message"), [
    (_row("rename", None, None, "Jay Rosburg", "Jerry Rosburg"), "matches no game"),
    (_row("season", 2023, "NYJ", "Robert Saleh", "Aaron Glenn"), "matches no game"),
    (_row("from_date", 2024, "NYJ", "Robert Saleh", "Jeff Ulbrich", "2024-12-31"),
     "matches no game"),
    # stale: the schedule already shows this change
    (_row("from_date", 2024, "ATL", "Bobby Petrino", "Emmitt Thomas", "2024-12-08"),
     "changes nothing"),
    # coach_out is not the schedule's coach
    (_row("season", 2024, "NYJ", "Rob Saleh", "Aaron Glenn"), "coach_out is not the schedule"),
    (_row("from_date", 2024, "NYJ", "Rob Saleh", "Jeff Ulbrich", "2024-10-08"),
     "list neither coach_out nor coach_in"),
    (_row("from_date", 2024, "ATL", "Dan Quinn", "Emmitt Thomas", "2024-12-02"),
     "coach_out is not the schedule's coach for any game"),
])  # fmt: skip
def test_rows_that_do_not_fit_the_schedule_stop_the_build(row, message):
    with pytest.raises(cc.CoachCorrectionError, match=message):
        cc.apply_corrections(GAMES, [row])


def test_overlapping_rows_stop_the_build():
    rows = [
        _row("season", 2024, "NYJ", "Robert Saleh", "Aaron Glenn", line=2),
        _row("from_date", 2024, "NYJ", "Robert Saleh", "Jeff Ulbrich", "2024-10-08", line=3),
    ]
    with pytest.raises(cc.CoachCorrectionError, match="lines 2 and 3 both cover 4 team-game"):
        cc.apply_corrections(GAMES, rows)


def _write(path: Path, rows: list[dict[str, str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cc.COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({"source_url": URL, "quote": "X was fired on May 1.",
                        "checked_by": "claude: source", **r})  # fmt: skip
    return path


def test_read_corrections_validates_rows(tmp_path):
    assert cc.read_corrections(tmp_path / "missing.csv") == []
    ok = {"kind": "from_date", "season": "2024", "team": "NYJ", "coach_out": "Robert Saleh",
          "coach_in": "Jeff Ulbrich", "from_date": "2024-10-08"}  # fmt: skip
    rows = cc.read_corrections(_write(tmp_path / "ok.csv", [ok]))
    assert rows == [cc.Correction(2, "from_date", 2024, "NYJ", "Robert Saleh", "Jeff Ulbrich",
                                  date(2024, 10, 8), URL)]  # fmt: skip
    bad = [ok,  # line 2
           {**ok, "coach_in": "Someone Else"},  # line 3: repeats line 2 (same key)
           {**ok, "kind": "fired"},  # line 4
           {**ok, "from_date": ""},  # line 5: a from_date row needs its date
           {"kind": "rename", "coach_out": "A B", "coach_in": "A B"},  # line 6
           {**ok, "season": "", "team": "NO", "quote": " ".join(["w"] * 26)},  # line 7
           {**ok, "team": "CHI", "source_url": "en.wikipedia.org"}]  # line 8  # fmt: skip
    with pytest.raises(cc.CoachCorrectionError) as e:
        cc.read_corrections(_write(tmp_path / "bad.csv", bad))
    msg = str(e.value)
    for text in ("6 invalid row(s)", "line 3: repeats line 2", "line 4: unknown kind 'fired'",
                 "line 5: from_date required", "line 6: coach_out equals coach_in",
                 "line 7: season required; quote longer than 25 words",
                 "line 8: source_url must be a web address"):  # fmt: skip
        assert text in msg, (text, msg)
    (tmp_path / "header.csv").write_text("kind,season\n", encoding="utf-8")
    with pytest.raises(cc.CoachCorrectionError, match="the header must be"):
        cc.read_corrections(tmp_path / "header.csv")


def test_build_applies_the_file_before_the_coach_tables(raw, db_path):
    raw.write_season(2025, [
        game(2025, 1, "2025-09-04", "20:20", "DAL", "PHI", home_coach="Nick Sirianni",
             away_coach="Brian Schottenheimer"),
        game(2025, 2, "2025-09-14", "13:00", "PHI", "KC", home_coach="Andy Reid",
             away_coach="Nick Sirianni"),
    ])  # fmt: skip
    # the raw fixture points the manual folder at tmp: this is the file the build reads
    _write(cc.corrections_path(), [
        {"kind": "from_date", "season": "2025", "team": "PHI", "coach_out": "Nick Sirianni",
         "coach_in": "Interim Coach", "from_date": "2025-09-05"},
        {"kind": "rename", "coach_out": "Andy Reid", "coach_in": "Andrew Reid"},
    ])  # fmt: skip
    manifest = wb.build_warehouse([2025], db_path=db_path)
    con = duckdb.connect(str(db_path), read_only=True)
    q = "SELECT home_coach, away_coach FROM fact_game ORDER BY game_id"
    assert con.execute(q).fetchall() == [("Nick Sirianni", "Brian Schottenheimer"),
                                         ("Andrew Reid", "Interim Coach")]  # fmt: skip
    assert con.execute(
        "SELECT coach_id, first_week, n_games FROM coach_team_season WHERE team = 'PHI' ORDER BY 2"
    ).fetchall() == [("nick_sirianni", 1, 1), ("interim_coach", 2, 1)]
    assert [r[0] for r in con.execute("SELECT coach_id FROM dim_coach ORDER BY 1").fetchall()] == [
        "andrew_reid", "brian_schottenheimer", "interim_coach", "nick_sirianni"]  # fmt: skip
    assert con.execute("SELECT coach_id FROM coach_game WHERE team = 'KC'").fetchall() == [
        ("andrew_reid",)]  # fmt: skip
    con.close()
    notes = json.loads({r["table_name"]: r for r in manifest}["fact_game"]["notes"])
    assert [(a["line"], a["kind"], a["games"]) for a in notes["coach_corrections"]] == [
        (2, "from_date", 1), (3, "rename", 1)]  # fmt: skip
    assert notes["n_coach_team_games_corrected"] == 2
    # a row the schedule no longer needs stops the build; the previous warehouse stays
    _write(cc.corrections_path(), [{"kind": "rename", "coach_out": "Andy Reid",
                                    "coach_in": "Andrew Reid"},
                                   {"kind": "rename", "coach_out": "Gone Coach",
                                    "coach_in": "Other Coach"}])  # fmt: skip
    with pytest.raises(cc.CoachCorrectionError, match="line 3 rename all: Gone Coach"):
        wb.build_warehouse([2025], db_path=db_path)
    assert dict(wb.table_counts(db_path))["dim_coach"] == 4
