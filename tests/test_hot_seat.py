"""Hot-Seat H1/H2: candidate departures from synthetic schedules, and the owner-label check."""

from __future__ import annotations

import datetime as dt

import polars as pl

from twm.modules.hot_seat import candidates as hc
from twm.modules.hot_seat import labels as hl


def _season(team: str, season: int, coaches: list[str], *, wins: int = 8, played: int = 99):
    """One team-season, one game a week: ``coaches[i]`` coaches week i+1; the first ``wins``
    games are won; games after week ``played`` are not played yet."""
    rows = []
    for i, coach in enumerate(coaches):
        week, day = i + 1, dt.date(season, 9, 7) + dt.timedelta(days=7 * i)
        rows.append(
            dict(
                game_id=f"{season}_{week:02d}_{team}_OPP",
                season=season,
                week=week,
                season_type="REG",
                game_date=day.isoformat(),
                team=team,
                coach_id=coach.lower().replace(" ", "_"),
                coach_name=coach,
                points_for=24 if i < wins else 10,
                points_against=17,
                played=week <= played,
            )
        )
    return rows


def _games(*seasons: list[dict]) -> pl.DataFrame:
    return pl.DataFrame([r for s in seasons for r in s]).select(hc.GAME_COLUMNS)


def _cands(games: pl.DataFrame, **kw) -> pl.DataFrame:
    return hc.departure_candidates(games, first_season=2010, last_season=2026, **kw)


def test_in_season_change_names_last_game_and_successor():
    g = _games(
        _season("AAA", 2010, ["Ann Old"] * 8 + ["Bob New"] * 8, wins=3),
        _season("AAA", 2011, ["Bob New"] * 16),
    )
    df = _cands(g)
    assert df.height == 1
    r = df.row(0, named=True)
    assert (r["change_kind"], r["coach_name"], r["successor_name"]) == (
        "in_season",
        "Ann Old",
        "Bob New",
    )
    assert (r["last_week"], r["last_game_id"], r["successor_first_game_id"]) == (
        8,
        "2010_08_AAA_OPP",
        "2010_09_AAA_OPP",
    )
    assert r["team_record"] == "3-13-0" and r["coach_games_in_season"] == 8
    assert not r["interim_suspected"] and not r["data_gap_suspected"] and not r["coach_returns"]
    assert r["candidate_id"] == "2010_AAA_w08_ann_old"


def test_offseason_change_uses_week_one_of_next_season():
    g = _games(
        _season("AAA", 2010, ["Ann Old"] * 16, wins=4),
        _season("AAA", 2011, ["Cal Hire"] * 16),
    )
    r = _cands(g).row(0, named=True)
    assert (r["change_kind"], r["last_week"], r["successor_name"]) == ("offseason", 16, "Cal Hire")
    assert r["successor_first_game_date"] == "2011-09-07"
    assert not r["interim_suspected"] and not r["data_gap_suspected"]


def test_interim_flag_on_a_coach_who_took_over_mid_season():
    g = _games(
        _season("AAA", 2010, ["Ann Old"] * 6 + ["Ivy Interim"] * 10, wins=2),
        _season("AAA", 2011, ["Cal Hire"] * 16),
    )
    df = _cands(g)
    assert df["change_kind"].to_list() == ["in_season", "offseason"]
    interim = df.filter(pl.col("coach_name") == "Ivy Interim").row(0, named=True)
    assert interim["interim_suspected"] and interim["coach_games_in_season"] == 10
    assert "took over in season 2010 week 7" in interim["auto_note"]
    assert not df.filter(pl.col("coach_name") == "Ann Old")["interim_suspected"].item()


def test_interim_retained_has_no_row_and_returning_coach_is_flagged():
    g = _games(
        _season("AAA", 2010, ["Ann Old"] * 4 + ["Ivy Fill"] * 2 + ["Ann Old"] * 10),
        _season("AAA", 2011, ["Ann Old"] * 16),
    )
    df = _cands(g)
    ann = df.filter(pl.col("coach_name") == "Ann Old").row(0, named=True)
    assert ann["coach_returns"] and "again from 2010 week 7" in ann["auto_note"]
    ivy = df.filter(pl.col("coach_name") == "Ivy Fill").row(0, named=True)
    assert ivy["interim_suspected"] and ivy["successor_name"] == "Ann Old"
    assert df.height == 2  # Ann's own stint continues into 2011: no departure


def test_gap_flag_from_2024_on_losing_offseason_changes_only():
    g = _games(
        _season("AAA", 2024, ["Ann Old"] * 17, wins=5),
        _season("AAA", 2025, ["Cal Hire"] * 17, wins=10),
        _season("BBB", 2024, ["Bo Win"] * 17, wins=12),
        _season("BBB", 2025, ["Di Next"] * 17, wins=10),
        _season("CCC", 2023, ["Ed Lose"] * 17, wins=3),
        _season("CCC", 2024, ["Fay Hire"] * 17),
        _season("CCC", 2025, ["Fay Hire"] * 17, wins=10),
    )
    df = _cands(g)
    gap = dict(zip(df["coach_name"], df["data_gap_suspected"], strict=True))
    assert gap == {"Ed Lose": False, "Ann Old": True, "Bo Win": False}
    note = df.filter(pl.col("coach_name") == "Ann Old")["auto_note"].item()
    assert "2024+ schedule lists one coach all season" in note and "5-12-0" in note


def test_none_recorded_rows_only_for_losing_teams_in_the_latest_season():
    g = _games(
        _season("AAA", 2025, ["Ann Old"] * 17, wins=9),
        _season("AAA", 2026, ["Ann Old"] * 17, wins=0, played=3),
        _season("BBB", 2025, ["Bo Win"] * 17, wins=9),
        _season("BBB", 2026, ["Bo Win"] * 17, wins=2, played=3),
    )
    df = _cands(g)
    assert df.height == 1
    r = df.row(0, named=True)
    assert (r["change_kind"], r["team"], r["last_week"]) == ("none_recorded", "AAA", 3)
    assert r["data_gap_suspected"] and r["successor_name"] == "" and r["team_record"] == "0-3-0"
    old = _cands(g, gap_from=2027)  # before the gap season no check rows are made
    assert old.height == 0


def test_season_bounds_and_unplayed_games():
    g = _games(
        _season("AAA", 2008, ["Old One"] * 16),
        _season("AAA", 2009, ["Mid Two"] * 16),
        _season("AAA", 2010, ["Mid Two"] * 8 + ["Next Three"] * 8, played=8),
    )
    df = _cands(g)
    assert df.height == 0  # 2008 is before first_season; the 2010 change is in unplayed games


def _world() -> pl.DataFrame:
    return _games(
        _season("BBB", 2010, ["Bea One"] * 5 + ["Bix Two"] * 11, wins=2),
        _season("BBB", 2011, ["Bix Two"] * 16),
        _season("AAA", 2010, ["Ann Old"] * 16, wins=4),
        _season("AAA", 2011, ["Cal Hire"] * 16),
    )


def test_candidates_csv_is_deterministic(tmp_path):
    g = _world()
    a = hc.write_candidates(_cands(g), tmp_path / "a.csv")
    b = hc.write_candidates(
        _cands(g.sample(fraction=1.0, shuffle=True, seed=7)), tmp_path / "b.csv"
    )
    assert a.read_bytes() == b.read_bytes()
    back = pl.read_csv(a)
    assert tuple(back.columns) == hc.CANDIDATE_COLUMNS
    assert back["candidate_id"].to_list() == ["2010_AAA_w16_ann_old", "2010_BBB_w05_bea_one"]


def _label_row(**kw) -> dict:
    row = {c: "" for c in hl.LABEL_COLUMNS}
    row.update(
        candidate_id="2010_AAA_w16_ann_old",
        origin="schedule",
        team="AAA",
        last_season="2010",
        change_kind="offseason",
        coach_name="Ann Old",
        last_game_date="2010-12-21",
        successor_first_game_date="2011-09-07",
        departure_type="fired_after_season",
        announced_date="2011-01-03",
        source_url="https://en.wikipedia.org/wiki/2010_NFL_season",
        prefill="suggested",
    )
    row.update(kw)
    return row


def _labels(*rows: dict) -> pl.DataFrame:
    return pl.DataFrame(list(rows), schema={c: pl.String for c in hl.LABEL_COLUMNS})


def test_check_labels_accepts_a_good_file_and_counts():
    df = _labels(
        _label_row(verified_by_owner="y"),
        _label_row(candidate_id="2010_BBB_w05_bea_one", team="BBB", coach_name="Bea One",
                   change_kind="in_season", last_game_date="2010-10-05",
                   successor_first_game_date="2010-10-12", departure_type="fired_in_season",
                   announced_date="2010-10-06"),
        _label_row(candidate_id="src_2010_AAA_ivy", origin="source_only", coach_name="Ivy Gone",
                   departure_type="", announced_date="", source_url="", prefill="blank"),
    )  # fmt: skip
    rep = hl.check_labels(df, _cands(_world()))
    assert rep.errors == [] and rep.warnings == []
    c = rep.counts
    assert (c["rows"], c["schedule"], c["source_only"], c["verified"]) == (3, 2, 1, 1)
    assert (c["unverified"], c["unverified_suggested"], c["positive_verified"]) == (2, 1, 1)
    assert rep.by_type == {"fired_after_season": 1} and c["candidates_missing"] == 0
    assert "3 rows (2 schedule, 1 source_only): 1 verified" in hl.summary_text(rep)


def test_check_labels_reports_each_kind_of_error():
    df = _labels(
        _label_row(departure_type="sacked"),
        _label_row(candidate_id="x2", announced_date="2012-01-03"),  # a year too late
        _label_row(candidate_id="x3", announced_date="2011/01/03"),
        _label_row(candidate_id="x4", verified_by_owner="y", source_url=""),
        _label_row(candidate_id="x5", verified_by_owner="yes", coach_name="Zed"),
        _label_row(candidate_id="x6", source_url="wikipedia", coach_name="Yan"),
        _label_row(candidate_id="x6", origin="guess", coach_name="Xu", announced_date=""),
    )
    rep = hl.check_labels(df)
    text = "\n".join(rep.errors)
    assert "'sacked' is not an allowed category" in text
    assert "2012-01-03 is outside the window 2010-06-01 .. 2011-09-30" in text
    assert "'2011/01/03' is not a YYYY-MM-DD date" in text
    assert "x4: verified row without source_url" in text
    assert "x5: verified_by_owner 'yes'" in text
    assert "'wikipedia' is not an http(s) URL" in text
    assert "origin 'guess'" in text
    assert "duplicate candidate_id: x6" in text
    assert "duplicate departure: AAA / Ann Old / 2011-01-03" in text  # rows 1 and x4
    assert len(rep.errors) == 9


def test_check_labels_coverage_and_date_warnings():
    cands = _cands(_world())
    df = _labels(
        _label_row(departure_type="fired_in_season", change_kind="in_season",
                   announced_date="2010-12-01"),  # before his last listed game
        _label_row(candidate_id="2009_AAA_w16_gone", coach_name="Gone"),
    )  # fmt: skip
    rep = hl.check_labels(df, cands)
    assert rep.errors == []
    text = "\n".join(rep.warnings)
    assert "2010_BBB_w05_bea_one: schedule candidate has no row" in text
    assert "2009_AAA_w16_gone: origin schedule but not in the candidates file" in text
    assert "fired on 2010-12-01, before his last listed game 2010-12-21" in text
    assert rep.counts["candidates_missing"] == 1
    gap = _labels(_label_row(departure_type="fired_in_season", announced_date="2010-11-01"))
    assert hl.check_labels(gap).warnings == []  # offseason row: the schedule gap, no warning


def test_check_labels_missing_columns():
    rep = hl.check_labels(pl.DataFrame({"candidate_id": ["a"]}))
    assert rep.errors[0].startswith("missing columns: origin, team")


def test_check_labels_cli_exit_codes(tmp_path):
    from typer.testing import CliRunner

    from twm.cli import app

    cands = hc.write_candidates(_cands(_world()), tmp_path / "c.csv")
    good, bad = tmp_path / "good.csv", tmp_path / "bad.csv"
    _labels(_label_row(verified_by_owner="y")).write_csv(good)
    _labels(_label_row(verified_by_owner="y", departure_type="sacked")).write_csv(bad)
    run = CliRunner().invoke
    ok = run(app, ["hotseat", "check-labels", "--labels", str(good), "--candidates", str(cands)])
    assert ok.exit_code == 0, ok.output
    assert "1 rows (1 schedule, 0 source_only): 1 verified" in ok.output
    assert "schedule candidates without a row: 1" in ok.output
    ko = run(app, ["hotseat", "check-labels", "--labels", str(bad), "--candidates", str(cands)])
    assert ko.exit_code == 1 and "ERROR: 2010_AAA_w16_ann_old: departure_type" in ko.output
    gone = run(app, ["hotseat", "check-labels", "--labels", str(tmp_path / "none.csv")])
    assert gone.exit_code == 1


def test_in_season_change_announced_after_the_successor_took_over_is_a_warning():
    leave = _label_row(change_kind="in_season", successor_first_game_date="2010-10-12",
                       last_game_date="2010-10-05", announced_date="2011-01-03")  # fmt: skip
    rep = hl.check_labels(_labels(leave))
    assert rep.errors == []
    assert rep.warnings == [
        "2010_AAA_w16_ann_old: announced 2011-01-03, after the successor's first game "
        "2010-10-12 (a leave first?)"
    ]
    assert hl.date_window(2010) == (dt.date(2010, 6, 1), dt.date(2011, 9, 30))


def test_check_labels_verified_row_may_leave_the_date_blank():
    # H3b-2: no source gives the day -> blank; the model uses the coach's last_game_date
    ok = hl.check_labels(_labels(_label_row(verified_by_owner="y", announced_date="")))
    assert ok.errors == [] and ok.counts["verified_blank_date"] == 1
    assert "blank announced_date (the model uses last_game_date): 1" in hl.summary_text(ok)
    bad = hl.check_labels(
        _labels(_label_row(verified_by_owner="y", announced_date="", last_game_date=""))
    )
    assert bad.errors == [
        "2010_AAA_w16_ann_old: verified row without announced_date (or last_game_date)"
    ]
