"""Feature #6, the playoff planner (docs/playoff_planner.md): the ratings math, shrinkage, the
walk-forward rule, K / D/ST direction, the live grid of a synthetic schedule, append-only
snapshots, grading, the My League section without ESPN and the committed pin."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import duckdb
import polars as pl
import pytest

from twm.modules.playoff_planner import backtest as bt
from twm.modules.playoff_planner import history as hs
from twm.modules.playoff_planner import production as pp
from twm.modules.playoff_planner import ratings as rt
from twm.modules.playoff_planner import weekly as wk

GAMES = [(1, "A", "B"), (1, "C", "D"), (2, "A", "C"), (2, "B", "D")]  # (week, home, away)
RB = {(1, "A"): 10.0, (1, "B"): 20.0, (1, "C"): 30.0, (1, "D"): 40.0,
      (2, "A"): 20.0, (2, "C"): 10.0, (2, "B"): 30.0, (2, "D"): 20.0}  # fmt: skip


def team_games(games=GAMES, season: int = 2020) -> pl.DataFrame:
    rows = []
    for w, h, a in games:
        gid = f"{season}_{w:02d}_{a}_{h}"
        rows += [(season, w, gid, h, a), (season, w, gid, a, h)]
    return pl.DataFrame(rows, orient="row",
                        schema=["season", "week", "game_id", "team", "opponent"])  # fmt: skip


def units(points: dict, position: str = "RB", games=GAMES, season: int = 2020) -> pl.DataFrame:
    """One unit per (week, team) of ``points``, at ``position`` (pid = team + position)."""
    g = team_games(games, season)
    rows = [(r["season"], r["week"], r["game_id"], r["team"], r["opponent"], position,
             f"{r['team']}{position}", f"{r['team']} {position}", points[(r["week"], r["team"])])
            for r in g.iter_rows(named=True) if (r["week"], r["team"]) in points]  # fmt: skip
    return pl.DataFrame(rows, orient="row", schema=["season", "week", "game_id", "team",
                        "opponent", "position", "pid", "player", "points"])  # fmt: skip


def rb(r: pl.DataFrame, team: str) -> dict:
    return r.filter((pl.col("position") == "RB") & (pl.col("team") == team)).to_dicts()[0]


def test_ratings_math() -> None:
    tot = rt.unit_totals(units(RB), team_games())
    assert tot.height == 8 * len(hs.POSITIONS)  # every team-game x position (0 when nobody)
    r = rt.ratings(tot, 2, {"RB": 2.0})
    a, c = rb(r, "A"), rb(r, "C")
    assert a["lg_ppg"] == pytest.approx(22.5) and a["games"] == 2 and a["allowed"] == 30
    assert a["raw"] == pytest.approx(30 / 45) and c["raw"] == pytest.approx(60 / 45)
    assert a["shrunk"] == pytest.approx(75 / 90) and c["shrunk"] == pytest.approx(105 / 90)
    # schedule-adjusted, k = 0: A faced B (20; B's other game 30) and C (10; C's other 30)
    assert rt.ratings(tot, 2, {"RB": 0.0}).pipe(rb, "A")["adjusted"] == pytest.approx(30 / 60)
    w1 = rt.ratings(tot, 1, {"RB": 0.0})
    assert rb(w1, "A")["raw"] == pytest.approx(20 / 25) and rb(w1, "A")["games"] == 1
    te = r.filter(pl.col("position") == "TE")  # nobody scored: no league average
    assert te["raw"].null_count() == te.height and (te["shrunk"] == 1.0).all()


def test_shrinkage() -> None:
    tot = rt.unit_totals(units(RB), team_games())
    raw = rt.ratings(tot, 2, {"RB": 0.0})
    assert (raw["raw"].fill_null(1.0) - raw["shrunk"]).abs().max() < 1e-12  # k = 0: raw
    heavy = rt.ratings(tot, 2, {"RB": 1e6})
    assert (heavy.filter(pl.col("position") == "RB")["shrunk"] - 1.0).abs().max() < 1e-4
    mid = rt.ratings(tot, 2, {"RB": 4.0})
    a = rb(mid, "A")
    assert 30 / 45 < a["shrunk"] < 1.0  # between the raw rating and average
    assert rt.PSEUDO_GAMES["TE"] == rt.MAX_PSEUDO and rt.PSEUDO_GAMES["DST"] < rt.PSEUDO_GAMES["RB"]


def test_no_future_game_enters_a_rating_or_a_base() -> None:
    later = {**RB, (2, "A"): 99.0, (2, "C"): 77.0, (2, "B"): 1.0, (2, "D"): 0.0}
    for pts in (RB, later):
        tot = rt.unit_totals(units(pts), team_games())
        r = rt.ratings(tot, 1)
        assert rb(r, "B")["raw"] == pytest.approx(10 / 25)  # week 1 only, whatever week 2 says
        b = bt.bases(pl.concat([units(pts), units(pts).with_columns(pl.col("week") + 2)]), 2)
        assert b.filter(pl.col("pid") == "ARB")["base"][0] == pytest.approx(
            (10 + pts[(2, "A")]) / 2
        )
    rows = bt.scored_rows(units(later), rt.unit_totals(units(later), team_games()), 1, (2,))
    assert rows.is_empty()  # one game to date: below MIN_GAMES, nothing to score


def test_k_and_dst_direction() -> None:
    """K: a defense that gives up kicker points rates > 1 for the kicker facing it. D/ST: the
    rating is about the OFFENSE faced: an offense that gives D/STs points rates > 1."""
    k = {key: 5.0 for key in RB} | {(1, "A"): 15.0}  # A's kicker scored 15 against B
    d = {key: 5.0 for key in RB} | {(1, "C"): 20.0}  # C's D/ST scored 20 against offense D
    tot = rt.unit_totals(pl.concat([units(k, "K"), units(d, "DST")]), team_games())
    r = rt.ratings(tot, 2, {"K": 0.0, "DST": 0.0})

    def get(pos: str, team: str) -> float:
        return r.filter((pl.col("position") == pos) & (pl.col("team") == team))["raw"][0]

    assert get("K", "B") > 1 > get("K", "A")  # B's defense allowed the big kicker game
    assert get("DST", "D") > 1 > get("DST", "C")  # offense D gave the D/ST its points


def spec(chosen: dict[str, str] | None = None, k: float = 0.0) -> pp.Spec:
    ch = {p: "shrunk" for p in hs.POSITIONS} | {"TE": "none"} | (chosen or {})
    return pp.Spec({"chosen": ch, "pseudo_games": {p: k for p in hs.POSITIONS},
                    "effects": [], "model_version": "matchup-test", "season": 2020})  # fmt: skip


def schedule(kick_w2: datetime) -> pl.DataFrame:
    """Weeks 1-2 (GAMES) and 15-17: A-B / C-D, A-C / B-D, then week 16 A-B only (C, D: bye)."""
    late = [(15, "A", "B"), (15, "C", "D"), (16, "A", "B"), (17, "A", "D"), (17, "B", "C")]
    rows = []
    for w, h, a in [*GAMES, *late]:
        k = datetime(2020, 9, 10) if w == 1 else kick_w2 if w == 2 else None
        rows.append((f"2020_{w:02d}_{a}_{h}", w, k, h, a))
    return pl.DataFrame(rows, orient="row", schema={"game_id": pl.String, "week": pl.Int32,
                        "kickoff_utc": pl.Datetime("us"), "home_team": pl.String,
                        "away_team": pl.String})  # fmt: skip


def test_live_grid_of_a_synthetic_schedule() -> None:
    g1 = [x for x in GAMES if x[0] == 1]
    fr = {"units": units({k: v for k, v in RB.items() if k[0] == 1}, games=g1),
          "team_games": team_games(g1), "schedule": schedule(datetime(2020, 9, 17))}  # fmt: skip
    rows = wk.grid(fr, spec(), 2020, datetime(2020, 9, 16))
    assert rows.columns == wk.GRID_COLUMNS and rows.height == 4 * 3 * len(hs.POSITIONS)
    assert set(rows["through_week"]) == {1}  # week 2 has not been played
    bye = rows.filter((pl.col("team") == "C") & (pl.col("week") == 16))
    assert bye["opponent"].null_count() == bye.height and bye["rating"].null_count() == bye.height
    r = rows.filter((pl.col("position") == "RB") & (pl.col("week") == 15))
    vs = dict(zip(r["team"], r["rating"], strict=True))
    assert vs["C"] == pytest.approx(30 / 25) and vs["D"] == pytest.approx(40 / 25)  # k = 0
    rank = dict(zip(r["opponent"], r["rating_rank"], strict=True))
    assert rank["C"] == 1 and rank["B"] == 4  # C allowed the most RB points: easiest
    assert (rows.filter(pl.col("position") == "TE")["rating"].drop_nulls() == 1.0).all()
    assert rows.filter(pl.col("position") == "TE")["rating_rank"].null_count() == 12
    # an unplayed week-2 game kicked off over 3 days ago counts as cancelled: week 2 complete
    later = wk.grid(fr, spec(), 2020, datetime(2020, 9, 21))
    assert set(later["through_week"]) == {2}
    assert wk.grid(
        {**fr, "team_games": team_games([])}, spec(), 2020, datetime(2020, 9, 9)
    ).is_empty()


def stored(tmp_path: Path) -> tuple[Path, pl.DataFrame]:
    g1 = [x for x in GAMES if x[0] == 1]
    fr = {"units": units({k: v for k, v in RB.items() if k[0] == 1}, games=g1),
          "team_games": team_games(g1), "schedule": schedule(datetime(2020, 9, 17))}  # fmt: skip
    rows = wk.grid(fr, spec(), 2020, datetime(2020, 9, 16))
    store = tmp_path / "pred.duckdb"
    res = wk.store_snapshot(store, rows, as_of=datetime(2020, 9, 16), model_version="m-1")
    assert res == {"rows": rows.height, "kept": 0}
    return store, rows


def test_snapshots_are_append_only(tmp_path: Path) -> None:
    store, rows = stored(tmp_path)
    again = wk.store_snapshot(store, rows, as_of=datetime(2020, 9, 17), model_version="m-2")
    assert again["rows"] == 0 and again["kept"] == rows.height  # same completed week: kept
    w2 = rows.with_columns(pl.lit(2, dtype=pl.Int32).alias("through_week"))
    assert wk.store_snapshot(store, w2, as_of=datetime(2020, 9, 23), model_version="m-2")["rows"]
    snaps = wk.read_snapshots(store, 2020)
    assert snaps.height == 2 * rows.height and set(snaps["model_version"]) == {"m-1", "m-2"}
    assert wk.stored_weeks(store, 2020) == {1, 2} and wk.stored_weeks(tmp_path / "x", 2020) == set()
    assert wk.store_snapshot(store, wk.empty_grid(), as_of=datetime(2020, 9, 30),
                             model_version="m-3") == {"rows": 0, "kept": 0}  # fmt: skip


def test_grading_uses_the_last_snapshot_rated_before_the_week(tmp_path: Path) -> None:
    store, rows = stored(tmp_path)  # through week 1
    late = rows.with_columns(pl.lit(15, dtype=pl.Int32).alias("through_week"),
                             (pl.col("rating") * 0 + 9.0).alias("rating"))  # fmt: skip
    wk.store_snapshot(store, late, as_of=datetime(2020, 12, 22), model_version="m-1")
    snaps = wk.read_snapshots(store, 2020)
    act = pl.DataFrame({"season": [2020], "week": [15], "game_id": ["g"], "team": ["C"],
                        "opponent": ["D"], "position": ["RB"], "points": [37.5]})  # fmt: skip
    g = wk.grade(snaps, act)
    c15 = g.filter((pl.col("team") == "C") & (pl.col("week") == 15) & (pl.col("position") == "RB"))
    assert c15["through_week"][0] == 1 and c15["outcome"][0] == "played"  # not the week-15 one
    assert c15["realized"][0] == pytest.approx(37.5 / 25) and c15["rating"][0] == pytest.approx(1.2)
    w16 = g.filter(pl.col("week") == 16)
    assert set(w16["through_week"]) == {15} and w16["outcome"].null_count() == w16.height
    s = wk.summary(g).filter(pl.col("position") == "RB").to_dicts()[0]
    assert (
        s["n"] == 1
        and s["mae_rating"] == pytest.approx(0.3)
        and s["mae_flat"] == pytest.approx(0.5)
    )
    assert wk.summary(g.clear()).is_empty()


def test_my_league_section_absent_without_espn(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from twm.league.report import ReportData
    from twm.modules.playoff_planner import league as lg

    store, _ = stored(tmp_path)
    con = duckdb.connect()
    assert ReportData.__dataclass_fields__["playoff_planner"].default is None
    nobody = SimpleNamespace(roster=pl.DataFrame(), lists=[])
    assert lg.for_report(con, store, nobody, 2020) is None  # no ESPN roster
    roster = pl.DataFrame({"player_name": ["Back", "Tight"], "position": ["RB", "TE"],
                           "pro_team": ["C", "A"], "entity_id": ["x1", "x2"]})  # fmt: skip
    me = SimpleNamespace(roster=roster, lists=[])
    assert lg.for_report(con, tmp_path / "none.duckdb", me, 2020) is None  # no grid stored
    sec = lg.for_report(con, store, me, 2020)  # no league tables: weeks 15-17
    assert sec is not None and sec.weeks == [15, 16, 17] and sec.through_week == 1
    back = next(r for r in sec.mine if r["player"] == "Back")
    assert back["games"][1] == (16, None, None) and back["mean_rating"] == pytest.approx(
        (1.2 + 0.4) / 2
    )
    html = str(lg.render_section(sec))
    assert lg.TITLE in html and "bye" in html and "D 1.20" in html
    assert lg.playoff_weeks({"reg_season_count": "14", "playoff_team_count": "4"}) == [15, 16]
    six = {
        "reg_season_count": "14",
        "playoff_team_count": "6",
        "playoff_matchup_period_length": "1",
    }
    assert lg.playoff_weeks(six) == [15, 16, 17] and lg.playoff_weeks({}) == [15, 16, 17]


def test_units_sql_on_a_tiny_warehouse(tmp_path: Path) -> None:
    from twm.scoring import ScoringRules
    from twm.scoring_kdst import DefenseRules, KickingRules

    con = duckdb.connect(str(tmp_path / "wh.duckdb"))
    stats = sorted({c for cols in ScoringRules.from_config().columns().values() for c in cols})
    zero = ", ".join(f"0.0 AS {c}" for c in stats if c != "receptions")
    con.execute(f"""CREATE TABLE fact_player_week AS SELECT 2020 AS season, 1 AS week,
        'REG' AS season_type, 'g1' AS game_id, 'A' AS team, 'B' AS opponent_team,
        p AS player_id, p AS player_display_name, 'WR' AS position, 5.0 AS receptions, {zero}
        FROM (VALUES ('h1'), ('w1'), ('q1')) t(p)""")
    con.execute("""CREATE TABLE fact_roster_week AS SELECT * FROM (VALUES
        (2020, 1, 'REG', 'h1', 'HB'), (2020, 1, 'REG', 'w1', 'WR'), (2020, 2, 'REG', 'q1', 'QB'))
        t(season, week, season_type, gsis_id, position)""")
    kz = ", ".join(
        f"0 AS {c}" for c in KickingRules.from_config().required_columns() if c != "pat_made"
    )
    con.execute(f"""CREATE TABLE fact_kicker_week AS SELECT 2020 AS season, 1 AS week,
        'REG' AS season_type, 'g1' AS game_id, 'A' AS team, 'B' AS opponent_team, 'k1' AS player_id,
        'K One' AS player_display_name, 2 AS pat_made, {kz}""")
    dz = ", ".join(f"0 AS {c}" for c in DefenseRules.from_config().required_columns()
                   if c not in ("points_allowed", "yards_allowed"))  # fmt: skip
    con.execute(f"""CREATE TABLE fact_defense_week AS SELECT 2020 AS season, 1 AS week,
        'REG' AS season_type, 'g1' AS game_id, t AS team, o AS opponent_team, 0 AS points_allowed,
        300 AS yards_allowed, {dz} FROM (VALUES ('A', 'B'), ('B', 'A')) v(t, o)""")
    u = con.execute(hs.units_sql([2020])).pl()
    pos = dict(zip(u["pid"], u["position"], strict=True))
    assert pos == {"h1": "RB", "w1": "WR", "q1": "QB", "k1": "K", "A": "DST", "B": "DST"}
    assert set(u["opponent"]) == {"B", "A"} and u.filter(pl.col("pid") == "w1")["points"][0] == 5.0
    assert con.execute(hs.team_games_sql([2020])).pl().height == 2
    con.close()


def test_the_committed_pin_checks_and_a_changed_file_is_refused(tmp_path: Path) -> None:
    from dataclasses import replace

    from typer.testing import CliRunner

    from twm import pins
    from twm.cli import app
    from twm.config import ROOT

    sp, pin = pp.load_pinned(2026)
    body = {k: v for k, v in sp.content.items() if k != "model_version"}
    assert pp.report_mismatches(body, ROOT / pp.REPORT_DIR) == []
    assert sp.content["chosen_by"] == "rule" and sp.chosen == sp.content["rule_choice"]
    assert set(sp.chosen) == set(hs.POSITIONS) and pin.backtest_seasons == "2013-2025"
    res = CliRunner().invoke(app, ["model", "check", "playoff_planner"])
    assert res.exit_code == 0, res.output
    bad = tmp_path / pin.file
    bad.parent.mkdir(parents=True)
    bad.write_text(pin.path().read_text().replace('"chosen_by": "rule"', '"chosen_by": "me"'))
    path = tmp_path / "pins.yaml"
    pins.write_pins({pp.PIN_KEY: replace(pin, approved=pin.approved)}, path)
    with pytest.raises(pins.PinError, match="not the approved file"):
        pp.load_pinned(2026, path=path, root=tmp_path)
    with pytest.raises(pins.PinError, match="scores season 2026"):
        pp.load_pinned(2027, path=path, root=tmp_path)


def test_build_choose_and_approve_on_synthetic_seasons(tmp_path: Path, monkeypatch) -> None:
    """The rule picks a candidate only when it wins; approve writes the file and only its pin."""
    monkeypatch.setattr(bt, "TEST_SEASONS", (2018, 2019))
    games = [(w, h, a) for w in range(1, 18) for h, a in (("A", "B"), ("C", "D"))]
    parts = []
    for s in (2018, 2019):  # B and D defenses always allow twice as much
        pts = {(w, t): (20.0 if t in ("A", "C") else 10.0) for w in range(1, 18) for t in "ABCD"}
        parts.append(units(pts, games=games, season=s))
    u = pl.concat(parts)
    tg = pl.concat([team_games(games, s) for s in (2018, 2019)])
    b = pp.build(u, rt.unit_totals(u, tg), 2020)
    assert b.selection.chosen["RB"] == "none"  # every week's matchup is the same: no gain
    with pytest.raises(pp.PlayoffPlannerProductionError, match="give the reason"):
        pp.content(pp.build(u, rt.unit_totals(u, tg), 2020, {"RB": "raw"}))
    c = pp.content(b)
    rep = tmp_path / "reports"
    pp.write_reports(c, rep)
    path = tmp_path / "pins.yaml"
    pin = pp.approve(c, report_dir=rep, root=tmp_path, path=path)
    assert pin.model == pp.MODEL and (tmp_path / pin.file).exists()
    assert pp.load_pinned(2020, path=path, root=tmp_path)[0].model_version == pin.model_version
