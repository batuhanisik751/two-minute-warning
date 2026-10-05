"""Feature #1, Questionable outcomes (twm.modules.questionable): definitions, the table, the
walk-forward backtest, "if he plays", snapshots, grading, the league section and the pin."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import polars as pl
import pytest

from twm.modules.questionable import history as hs
from twm.modules.questionable import plays as pp
from twm.modules.questionable import table as tb

STATS = ("passing_yards", "passing_tds", "passing_interceptions", "passing_2pt_conversions",
         "rushing_yards", "rushing_tds", "rushing_2pt_conversions", "receptions",
         "receiving_yards", "receiving_tds", "receiving_2pt_conversions", "special_teams_tds",
         "fumble_recovery_tds", "fumbles_lost_total")  # fmt: skip
K0 = datetime(2024, 9, 8, 17, 0)


def _warehouse(path: Path) -> Path:
    """Team A plays weeks 1-3 (vs B); player p1 (WR) plays week 1, sits week 2 while on the
    roster, is Questionable (limited) in week 3 and plays; p2 Doubtful week 3, no snap; p3 is
    healthy; team C is on the report in week 3 but has no game (dropped)."""
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE fact_schedule (game_id VARCHAR, season INT, week INT, "
        "season_type VARCHAR, kickoff_utc TIMESTAMP, home_team VARCHAR, away_team VARCHAR)"
    )
    for w in (1, 2, 3):
        con.execute("INSERT INTO fact_schedule VALUES (?, 2024, ?, 'REG', ?, 'A', 'B')",
                    [f"g{w}", w, K0 + timedelta(days=7 * (w - 1))])  # fmt: skip
    con.execute(
        "CREATE TABLE fact_snaps (season INT, week INT, game_type VARCHAR, "
        "gsis_id VARCHAR, position VARCHAR, team VARCHAR, offense_snaps DOUBLE)"
    )
    con.executemany("INSERT INTO fact_snaps VALUES (2024, ?, 'REG', ?, ?, 'A', ?)",
                    [(1, "p1", "WR", 40), (3, "p1", "WR", 30), (1, "p3", "WR", 50),
                     (2, "p3", "WR", 50), (3, "p3", "WR", 50), (3, "p2", "RB", 0)])  # fmt: skip
    stats = ", ".join(f"{c} DOUBLE" for c in STATS)
    con.execute(f"CREATE TABLE fact_player_week (player_id VARCHAR, season INT, week INT, "
                f"season_type VARCHAR, {stats})")  # fmt: skip
    for pid, w, yds in (("p1", 1, 100), ("p1", 3, 40), ("p3", 1, 80), ("p3", 2, 60), ("p3", 3, 70)):
        con.execute(f"INSERT INTO fact_player_week (player_id, season, week, season_type, "
                    f"receiving_yards) VALUES ('{pid}', 2024, {w}, 'REG', {yds})")  # fmt: skip
    con.execute(
        "CREATE TABLE fact_roster_week (season INT, week INT, season_type VARCHAR, "
        "team VARCHAR, gsis_id VARCHAR)"
    )
    con.executemany("INSERT INTO fact_roster_week VALUES (2024, ?, 'REG', 'A', ?)",
                    [(w, p) for w in (1, 2, 3) for p in ("p1", "p2", "p3")])  # fmt: skip
    con.execute(
        "CREATE TABLE fact_injury_report (season INT, season_type VARCHAR, week INT, "
        "team VARCHAR, gsis_id VARCHAR, position VARCHAR, full_name VARCHAR, "
        "report_status VARCHAR, practice_status VARCHAR, report_primary_injury VARCHAR, "
        "date_modified TIMESTAMP)"
    )
    con.executemany("INSERT INTO fact_injury_report VALUES (2024, 'REG', 3, ?, ?, ?, ?, ?, ?, ?, "
                    "NULL)", [("A", "p1", "WR", "One", "Questionable",
                               "Limited Participation in Practice", "Right Hamstring"),
                              ("A", "p2", "RB", "Two", "Doubtful",
                               "Did Not Participate In Practice", "Ribs"),
                              ("C", "p9", "TE", "Nine", "Questionable", None, "Knee")])  # fmt: skip
    con.close()
    return path


def test_practice_buckets_and_body_parts() -> None:
    assert hs.practice_bucket("Full Participation in Practice") == "full"
    assert hs.practice_bucket("Limited Participation in Practice") == "limited"
    assert hs.practice_bucket("Did Not Participate In Practice") == "dnp"
    assert hs.practice_bucket("Out (Definitely Will Not Play)") == "dnp"
    for blank in (None, "", " ", "Note"):
        assert hs.practice_bucket(blank) == "none"
    assert hs.body_part("Right Shoulder") == "shoulder" and hs.body_part("Hips") == "hip"
    assert hs.body_part("Ribs") == "other" and hs.body_part(None) == "other"


def test_played_missed_previous_game_and_teams_without_a_game(tmp_path: Path) -> None:
    con = hs.connect(_warehouse(tmp_path / "wh.duckdb"))
    rows = hs.read_tagged(con, [2024]).sort("gsis_id")
    assert rows["gsis_id"].to_list() == ["p1", "p2"]  # team C had no game: dropped
    p1, p2 = rows.row(0, named=True), rows.row(1, named=True)
    assert p1["played"] and p1["missed_prev"] and p1["practice"] == "limited"
    assert p1["body_part"] == "hamstring" and p1["prior_games"] == 1
    assert p1["points"] == pytest.approx(4.0) and p1["prior_ppg"] == pytest.approx(10.0)
    # a row with offense_snaps = 0 did not play; p2 took a snap in no earlier game
    assert not p2["played"] and p2["points"] is None and p2["missed_prev"]
    played = hs.read_played(con, [2024])
    assert played.filter(pl.col("on_report"))["gsis_id"].to_list() == ["p1"]
    p3 = played.filter((pl.col("gsis_id") == "p3") & (pl.col("week") == 3)).row(0, named=True)
    assert p3["prior_games"] == 2 and p3["prior_ppg"] == pytest.approx(7.0)


def _rows(spec: list[tuple]) -> pl.DataFrame:
    """(season, status, practice, missed_prev, position, played) rows, repeated by count."""
    out = []
    for season, status, practice, missed, pos, played, n in spec:
        out += [{"season": season, "week": 1, "gsis_id": f"x{i}", "report_status": status,
                 "practice": practice, "missed_prev": missed, "position": pos,
                 "body_part": "knee", "played": played} for i in range(n)]  # fmt: skip
    return pl.DataFrame(out)


def test_cells_are_shrunk_toward_their_parent_with_the_fixed_pseudo_count() -> None:
    rows = _rows([(2016, "Questionable", "full", False, "WR", True, 8),
                  (2016, "Questionable", "full", False, "WR", False, 2),
                  (2016, "Questionable", "dnp", False, "WR", False, 10)])  # fmt: skip
    t = tb.fit(rows, "practice")
    assert t.levels[0]["rate"].to_list() == [pytest.approx(0.4)]  # 8 of 20: the status rate
    full = t.levels[1].filter(pl.col("practice") == "full")["rate"][0]
    k = tb.PSEUDO_COUNT
    assert full == pytest.approx((8 + k * 0.4) / (10 + k))
    # an unseen cell gets its parent's rate; an unseen status gets nothing
    probe = _rows([(2017, "Questionable", "limited", False, "WR", True, 1),
                   (2017, "Doubtful", "full", False, "WR", True, 1)])  # fmt: skip
    assert tb.predict(t, probe).to_list() == [pytest.approx(0.4), None]


def test_walk_forward_never_trains_on_the_test_season(monkeypatch) -> None:
    rows = _rows(
        [(s, "Questionable", "full", False, "WR", s % 2 == 0, 5) for s in range(2016, 2021)]
    )
    seen: list[set[int]] = []
    fit = tb.fit

    def spy(train: pl.DataFrame, grouping: str, k: float = tb.PSEUDO_COUNT) -> tb.Table:
        seen.append(set(train["season"].to_list()))
        return fit(train, grouping, k)

    monkeypatch.setattr(tb, "fit", spy)
    scored = tb.walk_forward(rows, "practice", test_seasons=(2018, 2019, 2020))
    assert seen == [{2016, 2017}, {2016, 2017, 2018}, {2016, 2017, 2018, 2019}]
    # 2018 is scored by 2016-2017 only: 5 of 10 played
    assert scored.filter(pl.col("season") == 2018)["p"].to_list() == [pytest.approx(0.5)] * 5


def test_wins_clearly_needs_both_pooled_metrics_and_six_seasons() -> None:
    def m(ll: list[float], brier: float) -> pl.DataFrame:
        rows = [{"season": str(2018 + i), "log_loss": x, "brier": 0.2} for i, x in enumerate(ll)]
        return pl.DataFrame([*rows, {"season": "all", "log_loss": sum(ll) / 8, "brier": brier}])

    parent = m([0.6] * 8, 0.20)
    assert tb.wins_clearly(m([0.5] * 6 + [0.61] * 2, 0.19), parent)
    assert not tb.wins_clearly(m([0.5] * 5 + [0.61] * 3, 0.19), parent)  # 5 of 8 seasons
    assert not tb.wins_clearly(m([0.5] * 8, 0.21), parent)  # Brier worse


def test_if_he_plays_is_compared_with_matched_healthy_players() -> None:
    assert pp.weighted_median([1.0, 2.0, 3.0], [1, 1, 1]) == 2.0
    assert pp.weighted_median([1.0, 2.0, 3.0], [5, 1, 1]) == 1.0
    assert pp.weighted_median([], []) is None
    base = {"season": 2020, "week": 5, "position": "WR", "prior_games": 3}
    tagged = pl.DataFrame([{**base, "points": pts, "prior_ppg": 10.0} for pts in (2.0, 8.0, 12.0)])
    rb = {**base, "position": "RB", "points": 0.0, "prior_ppg": 10.0}
    wrs = [{**base, "points": pts, "prior_ppg": 10.0} for pts in (9.0, 11.0)]
    healthy = pl.DataFrame([*wrs, rb])
    got = pp.compare(pp.eligible(tagged), pp.eligible(healthy))
    assert got["n"] == 3 and got["median"] == pytest.approx(0.8)
    assert got["dud_rate"] == pytest.approx(1 / 3)
    # only the same-cell (WR) healthy rows count: neither is a dud
    assert got["matched"] == 3 and got["healthy_median"] == pytest.approx(0.9)
    assert got["healthy_dud_rate"] == pytest.approx(0.0)
    # below 5 points per game so far, or fewer than 2 earlier games: not eligible
    low = tagged.with_columns(pl.lit(4.9).alias("prior_ppg"))
    assert pp.eligible(low).is_empty()
    assert pp.eligible(tagged.with_columns(pl.lit(1).alias("prior_games"))).is_empty()


def _snap_rows(n: int, kickoff: datetime, chance: float = 0.6) -> pl.DataFrame:
    from twm.modules.questionable import weekly as wk

    row = {c: None for c in wk.COLUMNS if c not in ("as_of", "model_version", "created_at")}
    rows = [{**row, "season": 2026, "week": 5, "gsis_id": f"p{i}", "player": f"P{i}",
             "team": "A", "kickoff_utc": kickoff, "report_status": "Questionable",
             "play_chance": chance, "source": "observed", "missed_prev": False}
            for i in range(n)]  # fmt: skip
    schema = {"season": pl.Int32, "week": pl.Int32, "kickoff_utc": pl.Datetime("us"),
              "play_chance": pl.Float64, "missed_prev": pl.Boolean, "plays_n": pl.Int32,
              "season_games": pl.Int32}  # fmt: skip
    return pl.DataFrame(rows, schema_overrides=schema, infer_schema_length=None)


def test_snapshots_are_append_only_and_the_kickoff_filter(tmp_path: Path) -> None:
    from twm.modules.questionable import weekly as wk

    store, ko = tmp_path / "pred.duckdb", datetime(2026, 10, 11, 17, 0)
    t1 = datetime(2026, 10, 9, 12, tzinfo=UTC)
    rows = _snap_rows(3, ko)
    assert wk.store_snapshot(store, rows, season=2026, week=5, as_of=t1,
                             model_version="lookup-x") == {"rows": 3, "kept": 0}  # fmt: skip
    again = wk.store_snapshot(store, _snap_rows(1, ko, 0.1), season=2026, week=5, as_of=t1,
                              model_version="lookup-y")  # fmt: skip
    assert again == {"rows": 0, "kept": 3}  # a stored as-of is never rewritten
    stored = wk.read_snapshots(store, 2026)
    assert stored.height == 3 and set(stored["model_version"]) == {"lookup-x"}
    # the kickoff filter: games that have started (or have no kickoff yet) drop off
    mixed = pl.concat([_snap_rows(1, ko), _snap_rows(1, datetime(2026, 10, 9, 0, 15))])
    mixed = pl.concat([mixed, _snap_rows(1, ko).with_columns(pl.lit(None).alias("kickoff_utc"))
                       .with_columns(pl.col("kickoff_utc").cast(pl.Datetime("us")))])  # fmt: skip
    assert wk.upcoming(mixed, 5, t1).height == 1
    assert wk.upcoming(mixed, 4, t1).is_empty()


def test_grading_uses_each_players_last_snapshot_before_his_kickoff() -> None:
    from twm.modules.questionable import weekly as wk

    ko = datetime(2026, 10, 11, 17, 0)
    snaps = pl.concat([
        _snap_rows(2, ko, 0.3).with_columns(pl.lit(datetime(2026, 10, 9, 15)).alias("as_of")),
        _snap_rows(2, ko, 0.7).with_columns(pl.lit(datetime(2026, 10, 10, 15)).alias("as_of")),
        _snap_rows(2, ko, 0.9).with_columns(pl.lit(datetime(2026, 10, 11, 18)).alias("as_of")),
    ])  # fmt: skip
    snapped = pl.DataFrame({"season": [2026, 2026], "week": [5, 5], "team": ["A", "A"],
                            "gsis_id": ["p0", "p1"], "offense_snaps": [12.0, 0.0]})  # fmt: skip
    graded = wk.grade(snaps, snapped).sort("gsis_id")
    assert graded["play_chance"].to_list() == [0.7, 0.7]  # not the after-kickoff 0.9
    assert graded["outcome"].to_list() == ["played", "did not play"]
    res = wk.summary(graded)
    assert res["n"] == 2 and res["predicted"] == pytest.approx(0.7) and res["actual"] == 0.5
    assert wk.summary(pl.DataFrame())["n"] == 0  # nothing stored yet
    pending = wk.grade(snaps, snapped.filter(pl.col("team") == "Z"))
    assert pending["outcome"].to_list() == [None, None] and wk.summary(pending)["pending"] == 2


def test_the_league_section_needs_a_roster_and_lists_starters_first(tmp_path: Path) -> None:
    from twm.league.report import ReportData
    from twm.modules.questionable import league as lg
    from twm.modules.questionable import weekly as wk

    assert ReportData.__dataclass_fields__["questionable"].default is None  # no section
    store = tmp_path / "pred.duckdb"
    assert lg.for_report(store, pl.DataFrame(), 2026) is None  # no ESPN roster: absent
    roster = pl.DataFrame({"entity_id": ["p0", "p1", "zz"], "lineup_slot": ["BE", "WR", "QB"]})
    assert lg.for_report(store, roster, 2026) is None  # nothing stored yet: no section
    assert "No Questionable list stored yet" in str(lg.render_section(lg.Section(2026, None, None)))
    t = datetime(2026, 10, 9, 12, tzinfo=UTC)
    rows = _snap_rows(3, datetime(2026, 10, 11, 17))
    wk.store_snapshot(store, rows, season=2026, week=5, as_of=t, model_version="lookup-x")
    sec = lg.for_report(store, roster, 2026)
    assert sec is not None and [r["gsis_id"] for r in sec.rows] == ["p1", "p0"]
    html = str(lg.render_section(sec))
    assert lg.TITLE in html and "P1" in html and "60%" in html and "90 minutes" in html


def _history() -> tuple[pl.DataFrame, pl.DataFrame]:
    rows = []
    for s in range(2016, 2020):
        for i in range(40):
            missed = i % 3 == 0
            rows.append({"season": s, "week": 3 + i % 10, "gsis_id": f"g{s}{i}",
                         "report_status": "Doubtful" if i % 8 == 0 else "Questionable",
                         "practice": ("full", "limited", "dnp")[i % 3], "missed_prev": missed,
                         "position": ("WR", "RB")[i % 2], "body_part": "knee",
                         "played": (i % 5 != 0) and not (missed and i % 2 == 0),
                         "points": 6.0 + i % 7, "prior_games": 3, "prior_points": 30.0,
                         "prior_ppg": 10.0})  # fmt: skip
    tagged = pl.DataFrame(rows).with_columns(
        pl.when(pl.col("played")).then(pl.col("points")).alias("points")
    )
    played = tagged.filter(pl.col("played")).select(
        "season", "week", "position", "points", "prior_games", "prior_ppg",
        pl.lit(False).alias("on_report"))  # fmt: skip
    return tagged, played


def test_the_pin_is_checked_before_reading_and_against_the_reports(tmp_path: Path) -> None:
    from twm import pins
    from twm.modules.questionable import production as qp

    tagged, played = _history()
    c = qp.content(qp.build(tagged, played, 2020))
    reports, pin_file = tmp_path / "reports", tmp_path / "pins.yaml"
    with pytest.raises(qp.QuestionableProductionError):  # no reports yet: refused
        qp.approve(c, report_dir=reports, root=tmp_path, path=pin_file)
    qp.write_reports(c, reports)
    pin = qp.approve(c, report_dir=reports, root=tmp_path, path=pin_file,
                     today=datetime(2026, 10, 4, tzinfo=UTC))  # fmt: skip
    spec, got = qp.load_pinned(2020, path=pin_file, root=tmp_path)
    assert got.model_version == pin.model_version == spec.model_version == qp.version_of(c)
    assert qp.report_mismatches({k: v for k, v in spec.content.items()
                                 if k != "model_version"}, reports) == []  # fmt: skip
    probe = tagged.filter(pl.col("season") == 2019)
    assert spec.chance(probe).null_count() == 0
    with pytest.raises(pins.PinError, match="scores season 2020"):
        qp.load_pinned(2021, path=pin_file, root=tmp_path)
    (reports / "calibration.csv").write_text("bucket,n,predicted,actual\n")
    assert qp.report_mismatches(c, reports)
    f = tmp_path / pin.file
    f.write_text(f.read_text().replace('"season": 2020', '"season": 2020 '))
    with pytest.raises(pins.PinError, match="not the approved file"):
        qp.load_pinned(2020, path=pin_file, root=tmp_path)
