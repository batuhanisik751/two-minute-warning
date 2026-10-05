"""Start/sit odds (feature #2, LOCAL ONLY): odds math, smoothing, the rank -> week mapping, the
walk-forward, name resolution, the CLI's "ranks not out yet" path, the report section and the
pin check. Synthetic data with generic names; no ESPN, no network."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from twm.modules.startsit import backtest as bt
from twm.modules.startsit import dist, live


def _d(values, weights=None) -> dist.Dist:
    v = np.asarray(values, dtype=float)
    w = np.full(len(v), 1 / len(v)) if weights is None else np.asarray(weights, dtype=float)
    return dist.Dist(v, w)


def test_odds_split_ties_and_respect_dominance() -> None:
    assert dist.p_greater(_d([5.0]), _d([5.0])) == pytest.approx(0.5)
    assert dist.p_greater(_d([1, 2, 3]), _d([1, 2, 3])) == pytest.approx(0.5)
    assert dist.p_greater(_d([10.0]), _d([1, 2, 3])) == pytest.approx(1.0)
    assert dist.p_greater(_d([0.0, 10.0]), _d([5.0])) == pytest.approx(0.5)
    a, b = _d([1, 4, 9]), _d([2, 4, 6])
    assert dist.p_greater(a, b) + dist.p_greater(b, a) == pytest.approx(1.0)


def test_play_chance_mixes_a_missed_game_at_zero() -> None:
    sure_ten, sure_five = np.full(4, 10.0), np.full(4, 5.0)
    a = dist.make_dist(sure_ten, dnp=0.0, play_chance=0.6)
    b = dist.make_dist(sure_five, dnp=0.0, play_chance=1.0)
    assert dist.p_greater(a, b) == pytest.approx(0.6)
    both_out = dist.make_dist(sure_five, dnp=0.0, play_chance=0.0)
    assert dist.p_greater(both_out, dist.make_dist(sure_ten, 0.0, 0.0)) == pytest.approx(0.5)
    history = dist.make_dist(sure_ten, dnp=0.25)  # default: the rank's own no-stat-line share
    assert history.weights[history.values == 0].sum() == pytest.approx(0.25)
    with pytest.raises(ValueError):
        dist.make_dist(sure_ten, 0.0, 1.5)


def test_smoothing_window_grows_with_rank_and_stays_symmetric() -> None:
    assert [dist.half_window(r, 0.0) for r in (1, 2, 5, 40)] == [0, 1, 1, 1]
    assert [dist.half_window(r, 0.2) for r in (1, 2, 10, 40)] == [0, 1, 3, 9]
    rows = [{"season": 2020, "week": w, "pos": pos, "pos_rank": r, "played": r != 3,
             "points": float(100 - r) if r != 3 else 0.0}
            for w in range(1, 3) for pos, cap in dist.RANK_CAP.items()
            for r in range(1, cap + 1)]  # fmt: skip
    table = dist.fit(pl.DataFrame(rows), 0.0)
    assert table.n["WR"][0] == 2 and table.n["WR"][1] == 6  # rank 1 alone, rank 2 pools 1-3
    assert set(table.atoms["WR"][0]) == {99.0}
    assert table.dnp["WR"][2] == pytest.approx(1 / 3)  # rank 3 pools 2-4; rank 3 never played
    back = dist.RankTable.from_frame(table.frame(), 0.0)
    assert np.array_equal(back.atoms["TE"], table.atoms["TE"])
    assert table.dist("QB", 999).values.tolist() == table.dist("QB", 40).values.tolist()


def _week_rows() -> pl.DataFrame:
    names = ["Alpha Example", "Alpha Sample", "Bravo Example Jr.", "D'Charlie Test"]
    return pl.DataFrame({
        "player": names + ["Echo Twopage"] * 2, "pos": ["WR", "WR", "RB", "TE", "TE", "QB"],
        "pos_rank": [3, 9, 4, 7, 12, 30], "team": ["AAA", "BBB", "CCC", "DDD", "EEE", "EEE"],
        "gsis_id": [f"00-{i}" for i in range(5)] + ["00-4"], "valid": [True] * 6,
    })  # fmt: skip


def test_names_resolve_exactly_by_words_or_close_spelling_else_list_choices() -> None:
    rows = _week_rows()
    assert [r.name for r in live.resolve(rows, "bravo example")] == ["Bravo Example Jr."]
    assert [r.name for r in live.resolve(rows, "DCharlie test")] == ["D'Charlie Test"]
    assert [r.name for r in live.resolve(rows, "Alpah Sample")] == ["Alpha Sample"]
    assert len(live.resolve(rows, "Alpha")) == 2  # ambiguous: the CLI lists both
    assert len(live.resolve(rows, "Echo Twopage")) == 2  # one player on two pages
    assert [r.pos for r in live.resolve(rows, "Echo Twopage TE")] == ["TE"]
    assert live.resolve(rows, "Nobody Here") == []


def _warehouse(path) -> None:  # noqa: ANN001
    """A tiny warehouse: week 4 of 2025 with a Thursday game ('THU') and a Sunday one ('LA')."""
    import duckdb

    from twm.scoring import ScoringRules

    stats = ", ".join(f"{c} DOUBLE" for c in ScoringRules.from_config().required_columns())
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE dim_week (season INT, week INT, season_type VARCHAR, "
                "window_start_utc TIMESTAMP, window_end_utc TIMESTAMP)")  # fmt: skip
    con.execute(
        "INSERT INTO dim_week VALUES (2025, 4, 'REG', '2025-09-23 14:00', "
        "'2025-09-30 14:00'), (2025, 5, 'REG', '2025-09-30 14:00', '2025-10-07 14:00')"
    )
    con.execute(
        "CREATE TABLE fact_schedule (season INT, week INT, season_type VARCHAR, "
        "home_team VARCHAR, away_team VARCHAR, kickoff_utc TIMESTAMP, game_id VARCHAR)"
    )
    con.execute("INSERT INTO fact_schedule VALUES (2025, 4, 'REG', 'THU', 'OPP', "
                "'2025-09-26 00:15', 'g1'), (2025, 4, 'REG', 'LA', 'XYZ', '2025-09-28 17:00', "
                "'g2')")  # fmt: skip
    con.execute("CREATE TABLE fact_ranking (season INT, scrape_date DATE, available_at TIMESTAMP,"
                " ecr_type VARCHAR, page_kind VARCHAR, page_type VARCHAR, page_pos VARCHAR, "
                "fantasypros_id VARCHAR, gsis_id VARCHAR, player VARCHAR, team VARCHAR, "
                "pos_rank INT, ecr DOUBLE, sd DOUBLE)")  # fmt: skip
    old, new = "'2025-09-24', '2025-09-25 00:00'", "'2025-09-26', '2025-09-27 00:00'"
    for snap, rows in ((old, [("f1", "p1", "LAR", 7), ("f2", "p2", "THU", 3)]),
                       (new, [("f1", "p1", "LAR", 5), ("f2", "p2", "THU", 2),
                              ("f3", "p3", "FA", 9), ("f4", "p4", "LA", 11)])):  # fmt: skip
        for fid, gid, team, rank in rows:
            con.execute(f"INSERT INTO fact_ranking VALUES (2025, {snap}, 'wp', 'weekly', "
                        f"'weekly-wr', 'WR', '{fid}', '{gid}', 'Player {fid}', '{team}', {rank},"
                        f" {rank}.0, 1.0)")  # fmt: skip
    con.execute(f"CREATE TABLE fact_player_week (player_id VARCHAR, season INT, week INT, "
                f"season_type VARCHAR, {stats})")  # fmt: skip
    con.execute("INSERT INTO fact_player_week (player_id, season, week, season_type, receptions,"
                " receiving_yards) VALUES ('p1', 2025, 4, 'REG', 5, 50), "
                "('p2', 2025, 4, 'REG', 2, 20)")  # fmt: skip
    con.close()


def test_ranks_map_to_the_week_they_precede_and_skip_games_already_on(tmp_path) -> None:
    import duckdb

    from twm.modules.startsit import data

    _warehouse(tmp_path / "wh.duckdb")
    con = duckdb.connect(str(tmp_path / "wh.duckdb"), read_only=True)
    h = data.history(con, [2025]).sort("fantasypros_id")
    con.close()
    assert h["fantasypros_id"].to_list() == ["f1", "f2", "f4"]  # 'FA' has no game: dropped
    assert h["week"].unique().to_list() == [4]
    by = {r["fantasypros_id"]: r for r in h.iter_rows(named=True)}
    assert by["f1"]["pos_rank"] == 5 and by["f1"]["team"] == "LA"  # newest; LAR -> LA
    assert by["f2"]["pos_rank"] == 3  # Friday's rank came after his Thursday kickoff
    assert by["f1"]["played"] and by["f1"]["points"] > 0
    assert not by["f4"]["played"] and by["f4"]["points"] == 0.0  # kept: part of the risk


def _history(seasons=(2020, 2021, 2022, 2023), seed: int = 7) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for s in seasons:
        for w in (1, 2, 3):
            for pos, cap in dist.RANK_CAP.items():
                for r in range(1, cap + 1):
                    pts = max(0.0, float(rng.normal(30 - 0.25 * r, 6)))
                    rows.append({"season": s, "week": w, "pos": pos, "pos_rank": r,
                                 "points": round(pts, 1), "played": pts > 0,
                                 "team": f"T{r % 16}", "fantasypros_id": f"{pos}{r}"})  # fmt: skip
    return pl.DataFrame(rows).with_columns(pl.col("pos_rank").cast(pl.Int32))


def test_walk_forward_never_sees_its_test_season() -> None:
    h = _history()
    base = bt.walk_forward(h, test_seasons=(2022, 2023))["pairs"]
    late = pl.col("season") == 2023
    changed = h.with_columns(pl.when(late).then(60 - pl.col("points"))
                             .otherwise(pl.col("points")).alias("points"))  # fmt: skip
    again = bt.walk_forward(changed, test_seasons=(2022, 2023))["pairs"]
    assert again["p"].to_list() == base["p"].to_list()  # 2023's outcomes never trained a fold
    assert again.filter(late)["y"].to_list() != base.filter(late)["y"].to_list()
    assert set(base["kind"].unique()) == {"same", "flex"}
    assert base.filter(pl.col("kind") == "same")["gap"].is_between(1, bt.MAX_GAP).all()
    cal = bt.calibration(base)
    assert cal["pairs"].sum() == base.height and cal["bucket"][0] == "50-55%"


def _table() -> dist.RankTable:
    return dist.fit(_history(seasons=(2020,)), 0.1)


def test_the_cli_says_plainly_when_the_weeks_ranks_are_not_out(monkeypatch, tmp_path) -> None:
    from twm.modules.startsit import cli, production

    out: list[str] = []
    monkeypatch.setattr(live, "target_week", lambda db, when: (2026, 5))
    monkeypatch.setattr(production, "load", lambda season: (_table(), {}))

    def not_out(*_a):  # noqa: ANN202
        raise live.RanksNotOutError("The week 5 ranks of 2026 are not out yet: ... Fridays.")

    monkeypatch.setattr(live, "week_ranks", not_out)
    code = cli.run_startsit("Alpha Example", "Bravo Example", db=tmp_path, echo=out.append)
    assert code == cli.EXIT_NOT_OUT and "not out yet" in out[0] and len(out) == 1
    rows = live.WeekRanks(2026, 5, None, _week_rows())
    monkeypatch.setattr(live, "week_ranks", lambda *_a: rows)
    out.clear()
    assert cli.run_startsit("Alpha", "Bravo", db=tmp_path, echo=out.append) == cli.EXIT_NAME
    assert "Alpha Example (WR3" in out[0] and "Alpha Sample (WR9" in out[0]
    out.clear()
    code = cli.run_startsit("Alpha Example", "Alpha Sample", db=tmp_path, play_b=0.5,
                            echo=out.append)  # fmt: skip
    assert code == 0 and "local only, never published" in out[0]
    assert "outscores Alpha Sample about" in out[3] and "50% to play" in out[2]
    assert out[-1].startswith("  Start Alpha Example") or "Close call" in out[-1]


def test_report_section_absent_without_a_league_and_marked_local_only(tmp_path) -> None:
    from twm.league import store
    from twm.modules.startsit import league

    con = store.connect(tmp_path / "league.duckdb")  # an empty store: no league synced
    try:
        assert league.for_report(con, tmp_path / "missing.duckdb") is None
    finally:
        con.close()
    roster = pl.DataFrame({
        "player_name": ["Alpha Example", "Alpha Sample", "Bravo Example Jr.", "D'Charlie Test"],
        "position": ["WR", "WR", "RB", "TE"], "lineup_slot": ["WR", "BE", "RB/WR/TE", "TE"],
        "gsis_id": ["00-0", "00-1", "00-2", "00-3"],
    })  # fmt: skip
    calls = league.slot_calls(roster, _week_rows(), _table())
    assert [c.slot for c in calls] == ["WR", "TE", "RB/WR/TE"]
    assert calls[0].bench == "Alpha Sample (WR9)" and 0 < calls[0].p_starter < 1
    assert calls[1].bench is None  # no TE on the bench
    html = str(league.render_section(league.StartSitSection(2026, 5, None, calls)))
    assert 'data-section="startsit"' in html and league.LICENSE_NOTE in html
    empty = str(league.render_section(league.StartSitSection(reason="not out yet")))
    assert "not out yet" in empty and league.LICENSE_NOTE in empty


def test_the_publish_and_the_pipeline_never_import_it() -> None:
    import subprocess
    import sys

    from twm.config import ROOT

    code = (
        "import importlib, pkgutil, sys\n"
        "import twm.publish, twm.pipeline\n"
        "for pkg in (twm.publish, twm.pipeline):\n"
        "    for m in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + '.'):\n"
        "        importlib.import_module(m.name)\n"
        "bad = sorted(m for m in sys.modules if m.startswith('twm.modules.startsit'))\n"
        "print(bad); sys.exit(1 if bad else 0)\n"
    )
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         timeout=240, cwd=ROOT)  # fmt: skip
    assert res.returncode == 0, res.stdout + res.stderr
    for folder in ("src/twm/publish", "src/twm/pipeline", "web/lib", "web/app"):
        for f in (ROOT / folder).rglob("*"):
            if f.is_file() and f.suffix in {".py", ".ts", ".tsx"}:
                assert "startsit" not in f.read_text(errors="ignore"), f


def test_the_committed_pin_checks_and_a_changed_file_is_refused(tmp_path) -> None:
    import shutil

    from twm import pins
    from twm.config import ROOT
    from twm.modules.startsit import production

    lines, problems = production.check(2026)
    assert problems == [] and "sha256 and rows checked" in lines[0]
    spec = production.read_spec(2026)
    for t in production.TABLES:
        df = production.read_frame(spec, t)
        assert not set(df.columns) & production.FORBIDDEN_COLUMNS, t
    pin = pins.get_pin("startsit")
    for rel in [pin.file, str(production.REPORT)] + [f["file"] for f in spec["files"].values()]:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, tmp_path / rel)
    pins.write_pins({"startsit": pin}, tmp_path / "pins.yaml")
    ok = production.check(2026, root=tmp_path, pin_path=tmp_path / "pins.yaml")
    assert ok[1] == []
    points = tmp_path / spec["files"]["rank_points"]["file"]
    points.write_bytes(points.read_bytes() + b"x")
    _, bad = production.check(2026, root=tmp_path, pin_path=tmp_path / "pins.yaml")
    assert bad and "sha256" in bad[0]
    with pytest.raises(production.StartSitPinError):
        production.load(2025)  # the frozen odds are for the pinned season only
