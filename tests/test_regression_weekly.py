"""Regression Watch's weekly list (D4a): the frozen parameters and their pin (tamper refused,
the committed file reproduces the committed backtest's 2025 rows), freshness and its exit code,
live vs reconstructed by the clock, the store round trip (a live week kept, an old store without
the numeric outcome column), final outcomes, the report's determinism and the CLI; realdata:
2026 week 3 reconstructed into a scratch copy of the owner's store."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import polars as pl
import pytest
from typer.testing import CliRunner

from tests.conftest import frame, game, plain
from tests.test_regression_watch import (
    P_QB,
    P_RB,
    P_TE,
    P_WR,
    PBP_DTYPES,
    STATS_DTYPES,
    _kc_game,
    _roster,
    _weekly,
)
from tests.test_waiver_radar_weekly import _inputs
from twm import pins
from twm import predictions as pr
from twm.cli import app
from twm.config import ROOT, league
from twm.modules.regression_watch import backtest as bt
from twm.modules.regression_watch import production as rp
from twm.modules.regression_watch import projection as pj
from twm.modules.regression_watch import tags as tg
from twm.modules.regression_watch import weekly as rwk
from twm.modules.regression_watch.stability import SHRINKAGE_SCHEMA
from twm.modules.waiver_radar import weekly as rw

SEASON = 2025
IN_WINDOW = datetime(2025, 10, 1, 12, tzinfo=UTC)  # after week 4's as-of, before week 5's game
LATER = datetime(2025, 10, 20, tzinfo=UTC)  # after week 5's game, before the season ends
SEASON_OVER = datetime(2026, 2, 1, tzinfo=UTC)
runner = CliRunner()


def _games() -> list[dict]:
    """KC plays weeks 1, 2, 4, 5, 6, 7 (a bye in week 3, when only MIA@BUF is played)."""
    return [
        game(2025, 1, "2025-09-07", "13:00", "KC", "LAC", result=-6),
        game(2025, 2, "2025-09-14", "13:00", "PHI", "KC", result=3),
        game(2025, 3, "2025-09-21", "13:00", "MIA", "BUF", result=10),
        game(2025, 4, "2025-09-28", "13:00", "KC", "MIA", result=7),
        game(2025, 5, "2025-10-05", "13:00", "KC", "LAC", result=3),
        game(2025, 6, "2025-10-12", "13:00", "DEN", "KC", result=-4),
        game(2025, 7, "2025-10-19", "13:00", "KC", "LV", result=1),
    ]


def _injuries(games: list[dict]) -> list[dict]:
    return [{"season": g["season"], "season_type": None, "game_type": "REG", "team": t,
             "week": g["week"], "gsis_id": "00-0000002", "position": "WR",
             "full_name": "Beta Two", "report_status": "Questionable",
             "practice_status": "Limited Participation in Practice"}
            for g in games for t in (g["home_team"], g["away_team"])]  # fmt: skip


def build_world(tmp: Path) -> Path:
    """2025 weeks 1-7: KC's QB, WR, TE and RB (the same plays every game), complete data for
    every KC game; the week-3 game (MIA@BUF) has no player data (not fresh)."""
    from tests.conftest import RawCache
    from twm import ids
    from twm.sources import nflverse as nv
    from twm.warehouse import build as wb

    mp = pytest.MonkeyPatch()
    root = tmp / "raw"
    mp.setattr(nv, "raw_dir", lambda: root)
    mp.setattr(nv, "_loader", lambda ds: (_ for _ in ()).throw(AssertionError("download")))
    mp.setattr(nv, "_configure_nflreadpy", lambda: None)
    mp.setattr(ids, "overrides_path", lambda: tmp / "manual" / ids.OVERRIDES_FILE)
    try:
        cache = RawCache(root)
        cache.write_globals()
        games = _games()
        kc = [g for g in games if "KC" in (g["home_team"], g["away_team"])]
        plays, passes, rushes, stats, opp = [], [], [], [], []
        for g in kc:
            p, pa, ru = _kc_game(g)
            s, o = _weekly(g)
            plays, passes, rushes, stats, opp = (plays + p, passes + pa, rushes + ru,
                                                 stats + s, opp + o)  # fmt: skip
        rosters = [
            _roster(2025, g["week"], pid, pos, "KC")
            for g in kc
            for pid, pos in ((P_QB, "QB"), (P_WR, "WR"), (P_TE, "TE"), (P_RB, "RB"))
        ]
        rosters += [_roster(2025, g["week"], "00-0000199", "QB", t) for g in games
                    for t in (g["home_team"], g["away_team"]) if t != "KC"]  # fmt: skip
        cache.write_season(2025, games, rosters=rosters, opportunity=opp, injuries=_injuries(games),
                           opportunity_pass=passes, opportunity_rush=rushes)  # fmt: skip
        cache.write("pbp", 2025, frame(plays, PBP_DTYPES))
        cache.write("player_stats", 2025, frame(stats, STATS_DTYPES))
        db = tmp / "wh.duckdb"
        wb.build_warehouse([2025], db_path=db)
    finally:
        mp.undo()
    return db


def toy_priors(seasons: tuple[int, ...] = tuple(range(2009, 2025))) -> pj.Priors:
    """r(g) = 1 / (1 + 9 / g) for every position and metric, prior mean 0."""
    rows = [(p, m, 1, 0.1, 1.0, 9.0, 0.0, 100, seasons[0], seasons[-1])
            for p in pj.FANTASY_POSITIONS for m in ("fpoe", "fpoe_ng")]  # fmt: skip
    table = pl.DataFrame(rows, schema=SHRINKAGE_SCHEMA, orient="row")
    return pj.Priors(seasons, table, {p: 1.2 for p in pj.FANTASY_POSITIONS})


def toy_choice(season: int, *, x_sell: float = 1.0, x_buy: float = 1.0,
               variant: str = "mean_flat_all") -> bt.Choice:  # fmt: skip
    val = tuple(range(2010, season))
    return bt.Choice(season, pj.VARIANT_BY_NAME[variant], val, 3.2, 3.3, 1000,
                     tg.Threshold("sell_high", x_sell, 0.9, 40, 8, val),
                     tg.Threshold("buy_low", x_buy, 0.6, 30, 8, val))  # fmt: skip


def toy_params(season: int = SEASON, **kw) -> rp.ProductionParams:
    return rp.make_params(season, toy_choice(season, **kw), toy_choice(season - 1),
                          toy_priors(tuple(range(2009, season))))  # fmt: skip


def toy_csv(path: Path, params: rp.ProductionParams, *, seasons: range = range(2011, 2025)) -> Path:
    """A backtest CSV with the parameters' record rows and pooled tag rows."""
    import csv

    from twm.modules.regression_watch.backtest_report import CSV_COLUMNS

    rows = [*rp.record_rows(params.record)]
    rows += [{"table": "choice", "season": str(s), "method": "zero_flat_all",
              "metric": "validation_mae", "value": "3.3"} for s in seasons
             if s != params.record["season"]]  # fmt: skip
    for tag, v, b in (("sell_high", 0.92, 0.6), ("buy_low", 0.62, 0.39), ("legit", 0.606, 0.611)):
        for group, value in (("tagged", v), ("base", b)):
            rows.append({"table": "tag", "weeks": "headline", "position": "all", "metric": tag,
                         "group": group, "value": str(value), "lo": str(value - 0.02),
                         "hi": str(value + 0.02), "n": "200"})  # fmt: skip
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(CSV_COLUMNS), lineterminator="\n")
        w.writeheader()
        w.writerows({c: r.get(c, "") for c in CSV_COLUMNS} for r in rows)
    return path


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> Path:
    return build_world(tmp_path_factory.mktemp("regression_weekly"))


def _run(world: Path, week: int = 4, now: datetime = IN_WINDOW, **kw) -> rwk.WeeklyRun:
    return rwk.run_week(world, SEASON, week, params=toy_params(), league=league(), now=now, **kw)


# --------------------------------------------------------------------------------------
# The frozen parameters and their pin
# --------------------------------------------------------------------------------------


def test_params_file_round_trip_is_byte_stable_and_hashes_its_content(tmp_path):
    a, b = toy_params(), toy_params()
    assert rp.params_json(a) == rp.params_json(b) and a.model_version == b.model_version
    assert a.model_version.startswith("mean_flat_all-")
    path = tmp_path / "p.json"
    path.write_text(rp.params_json(a))
    back = rp.load_params(path)
    assert back.model_version == a.model_version and back.shrinkage.equals(a.shrinkage)
    assert back.priors().factor("WR", 3) == pytest.approx(0.25)
    assert back.priors().lookup().equals(a.priors().lookup())
    assert toy_params(x_sell=1.5).model_version != a.model_version
    d = json.loads(path.read_text())
    d["x_sell"] = 9.0  # edited by hand, version left as it was
    path.write_text(json.dumps(d, sort_keys=True, indent=2) + "\n")
    with pytest.raises(rp.RegressionProductionError, match="edited by hand"):
        rp.load_params(path)


def _blocks(text: str) -> dict[str, str]:
    """The pin file's top-level entries as their exact text."""
    out: dict[str, list[str]] = {}
    key = "#"
    for line in text.splitlines():
        if line and not line.startswith((" ", "#")):
            key = line.rstrip(":")
        out.setdefault(key, []).append(line)
    return {k: "\n".join(v) for k, v in out.items()}


def test_approve_pins_the_file_and_tampering_is_refused(tmp_path):
    params = toy_params()
    csv_path = toy_csv(tmp_path / "backtest.csv", params)
    pin_file = tmp_path / "production_models.yaml"
    shutil.copy(ROOT / "config" / "production_models.yaml", pin_file)
    before = _blocks(pin_file.read_text())
    pin = rp.approve(params, csv_path=csv_path, root=tmp_path, path=pin_file,
                     today=datetime(2025, 9, 1, tzinfo=UTC))  # fmt: skip
    assert (pin.model, pin.season, pin.approved) == ("params", SEASON, "2025-09-01")
    after = _blocks(pin_file.read_text())
    for key in ("waiver_radar", "streamer_k", "streamer_dst", "#"):  # other pins byte-kept
        assert after[key] == before[key]
    got, _ = rp.load_pinned_params(SEASON, path=pin_file, root=tmp_path)
    assert got.model_version == params.model_version
    with pytest.raises(pins.PinError, match="score season 2025, not 2026"):
        rp.load_pinned_params(2026, path=pin_file, root=tmp_path)
    file = pin.path(tmp_path)
    file.write_text(file.read_text().replace('"x_sell": 1.0', '"x_sell": 0.5'))
    with pytest.raises(pins.PinError, match="not the approved file"):
        rp.load_pinned_params(SEASON, path=pin_file, root=tmp_path)
    file.unlink()
    with pytest.raises(pins.PinError, match="missing"):
        rp.load_pinned_params(SEASON, path=pin_file, root=tmp_path)
    streamer_pin = pins.read_pins(pin_file)["streamer_dst"]
    pins.write_pins({**pins.read_pins(pin_file), "regression_watch": streamer_pin}, pin_file)
    with pytest.raises(pins.PinError, match="not 'params'"):
        rp.load_pinned_params(SEASON, path=pin_file, root=tmp_path)


def test_the_record_must_reproduce_the_backtest_csv(tmp_path):
    params = toy_params()
    good = toy_csv(tmp_path / "good.csv", params)
    assert rp.record_mismatches(params, good) == []
    text = good.read_text().replace("validation precision 0.9", "validation precision 0.8")
    (tmp_path / "bad.csv").write_text(text)
    problems = rp.record_mismatches(params, tmp_path / "bad.csv")
    assert len(problems) == 1 and "sell_high" in problems[0] and "detail" in problems[0]
    other = toy_params(x_sell=2.0)  # same record, different production X: still consistent
    assert rp.record_mismatches(other, good) == []
    shifted = rp.make_params(SEASON, toy_choice(SEASON), toy_choice(SEASON - 1, x_buy=1.5),
                             toy_priors(tuple(range(2009, SEASON))))  # fmt: skip
    assert any("buy_low: value" in p for p in rp.record_mismatches(shifted, good))
    with pytest.raises(rp.RegressionProductionError, match="disagree"):
        rp.approve(shifted, csv_path=good, root=tmp_path, path=tmp_path / "pins.yaml")
    assert not (tmp_path / "pins.yaml").exists()
    assert rp.record_mismatches(params, tmp_path / "missing.csv") == [
        f"the backtest report is missing: {tmp_path / 'missing.csv'}"
    ]


def test_the_committed_parameters_reproduce_the_committed_2025_rows():
    """`twm model check regression_watch` on the committed pin, file and backtest CSV."""
    params, pin = rp.load_pinned_params(2026)
    assert pin.model == "params" and params.variant.name == "mean_flat_all"
    assert (params.x_sell, params.x_buy) == (4.5, 3.5)
    assert params.shrinkage_seasons == tuple(range(2009, 2026))
    assert params.record["season"] == 2025 and params.record["variant"] == "mean_hl8_all"
    csv_path = ROOT / "reports" / "regression_watch" / "backtest.csv"
    assert rp.record_mismatches(params, csv_path) == []
    out = runner.invoke(app, ["model", "check", "regression_watch", "--season", "2026"])
    assert out.exit_code == 0, out.output
    assert "matches reports/regression_watch/backtest.csv (season 2025" in out.output
    assert (ROOT / pin.file).stat().st_size < 10_000


# --------------------------------------------------------------------------------------
# Freshness, the clock and the list
# --------------------------------------------------------------------------------------


def test_freshness_is_the_radars_plus_play_by_play():
    from tests.test_waiver_radar_weekly import AFTER, AS_OF

    plays = pl.DataFrame({"game_id": ["g1", "g2"], "n": [150, 160]})
    assert rwk.check_freshness({**_inputs(), "plays": plays}, 2025, 2, AS_OF, AFTER).ok
    fr = rwk.check_freshness({**_inputs(), "plays": plays.head(1)}, 2025, 2, AS_OF, AFTER)
    assert fr.problems == ["1 played game(s) have no play-by-play rows (fact_play) yet: DAL@SEA"]
    radar = rwk.check_freshness({**_inputs(snaps="g1"), "plays": plays}, 2025, 2, AS_OF, AFTER)
    assert radar.problems == rw.check_freshness(_inputs(snaps="g1"), 2025, 2, AS_OF, AFTER).problems


def test_a_week_whose_data_has_not_arrived_exits_3(world, tmp_path):
    out = runner.invoke(app, ["regression", "score", "--season", "2025", "--week", "3", "--db",
                              str(world), "--store", str(tmp_path / "s.duckdb"), "--now",
                              "2025-09-24T12:00"])  # fmt: skip
    assert out.exit_code == rwk.EXIT_NOT_READY, out.output
    assert "MIA@BUF" in out.output and not (tmp_path / "s.duckdb").exists()
    with pytest.raises(rw.NotReadyError):
        _run(world, 3, datetime(2025, 9, 24, 12, tzinfo=UTC))


def test_live_only_on_the_real_clock_inside_the_window(world):
    live = _run(world)
    assert (live.kind, live.incomplete, live.horizon) == ("live", False, 3)
    assert _run(world, real_clock=False).kind == "backtest"
    assert _run(world, now=LATER).kind == "backtest"
    early = _run(world, now=datetime(2025, 9, 30, 10, tzinfo=UTC), allow_incomplete=True)
    assert early.kind == "backtest" and early.incomplete  # before Tuesday's as-of
    with pytest.raises(ValueError, match="last regular-season week"):
        _run(world, 7, SEASON_OVER)
    with pytest.raises(ValueError, match="score 2025, not 2026"):
        rwk.run_week(world, 2026, 4, params=toy_params(), league=league(), now=IN_WINDOW)


def test_the_list_uses_the_frozen_parameters(world):
    t = _run(world).table.sort("position")
    assert t.get_column("position").to_list() == ["QB", "RB", "TE", "WR"]
    assert t.get_column("games").to_list() == [3, 3, 3, 3]
    wr = t.filter(pl.col("position") == "WR").row(0, named=True)
    r = 1.0 / (1.0 + 9.0 / wr["g_opp"])  # the toy shrinkage, toward the prior mean 0
    assert wr["ppg_ros"] == pytest.approx(wr["xfp_pg"] + r * wr["fpoe_pg"], abs=1e-6)
    assert (wr["band"], t.filter(pl.col("position") == "TE").item(0, "band")) == (
        "sell_high", "buy_low")  # fmt: skip
    why = json.loads(wr["reasons_json"])
    assert why["tags"] == ["sell_high"] and why["shrinkage"] == pytest.approx(r, abs=1e-6)
    assert why["x"] == {"sell_high": 1.0, "buy_low": 1.0} and why["games"] == 3
    assert set(why["without_garbage_time"]) == {"ppg", "xfp_pg", "fpoe_pg", "shrinkage",
                                                "shrink_toward"}  # fmt: skip
    assert why["ppg"] > why["without_garbage_time"]["ppg"]  # a garbage-time touchdown a game
    assert rwk.score_week(pl.DataFrame(), pl.DataFrame(), toy_params(), league(), 1).height == 0


# --------------------------------------------------------------------------------------
# The store: round trip, a live week kept, final outcomes, an old store
# --------------------------------------------------------------------------------------


def test_store_round_trip_and_a_live_week_is_kept(world, tmp_path):
    store = tmp_path / "p.duckdb"
    live = _run(world)
    counts = rwk.store_week(live, store, created_at=datetime(2025, 10, 1, 12))
    assert counts == {"predictions": 4, "model_versions": 1, "outcomes": 0}
    rows = pr.read_table(store, "predictions")
    assert set(rows.get_column("module")) == {"regression_watch"}
    assert set(rows.get_column("entity_type")) == {"player"}
    assert set(rows.get_column("kind")) == {"live"} and set(rows.get_column("horizon")) == {3}
    assert rows.sort("entity_id").get_column("score").to_list() == pytest.approx(
        live.table.sort("gsis_id").get_column("ppg_ros").to_list())  # fmt: skip
    back = {r["entity_id"]: r for r in rows.iter_rows(named=True)}
    assert back[P_WR]["band"] == "sell_high" and back[P_WR]["rank_group"] == "WR"
    assert json.loads(back[P_WR]["reasons_json"])["tags"] == ["sell_high"]
    vers = pr.read_table(store, "model_versions")
    assert vers.item(0, "model_version") == toy_params().model_version
    assert (vers.item(0, "label"), vers.item(0, "test_season")) == ("ppg_ros", SEASON)
    with pytest.raises(pr.LiveWeekError):
        rwk.store_week(_run(world, now=LATER), store)
    again = rwk.store_week(live, store, created_at=datetime(2025, 10, 1, 13))
    assert again["predictions"] == 4 and pr.read_table(store, "predictions").height == 4


def test_outcomes_are_written_only_once_the_season_is_over(world, tmp_path):
    store = tmp_path / "p.duckdb"
    run = _run(world, now=LATER)
    rwk.store_week(run, store)
    assert rwk.update_outcomes(world, store, LATER) == 0  # the season is not over
    assert not rwk.season_final(world, SEASON, LATER) and rwk.season_final(
        world, SEASON, SEASON_OVER
    )
    assert rwk.update_outcomes(world, store, SEASON_OVER) == 4
    outs = pr.read_table(store, "outcomes").sort("entity_id")
    assert set(outs.get_column("label_status")) == {"final"}
    assert outs.get_column("y_hit").null_count() == 4
    ppg = run.table.sort("gsis_id").get_column("ppg").to_list()  # the same plays every game
    assert outs.get_column("y_value").to_list() == pytest.approx(ppg)
    assert rwk.update_outcomes(world, store, SEASON_OVER) == 4  # idempotent
    assert pr.read_table(store, "outcomes").height == 4


def test_an_old_store_without_the_numeric_outcome_column_migrates(tmp_path):
    store = tmp_path / "old.duckdb"
    old_cols = {c: t for c, t in pr.OUTCOME_COLUMNS.items() if c != "y_value"}
    con = duckdb.connect(str(store))
    con.execute(f"CREATE TABLE outcomes ({', '.join(f'{c} {t}' for c, t in old_cols.items())})")
    con.execute("INSERT INTO outcomes VALUES ('waiver_radar', 'p1', 2024, 5, "
                "TIMESTAMP '2024-10-08 14:00', TRUE, FALSE, 'final')")  # fmt: skip
    con.close()
    radar = pr.read_table(store, "outcomes")  # read-only: no migration, no y_value yet
    assert "y_value" not in radar.columns
    pr.connect(store).close()  # a writer's connection adds the column in place
    after = pr.read_table(store, "outcomes")
    assert after.columns == list(pr.OUTCOME_COLUMNS)
    assert after.drop("y_value").equals(radar) and after.item(0, "y_value") is None
    new = pl.DataFrame({"module": ["regression_watch"], "entity_id": ["p2"], "season": [2024],
                        "week": [5], "as_of": [datetime(2024, 10, 8, 14)], "y_hit": [None],
                        "y_sustained": [None], "label_status": ["final"], "y_value": [12.5]},
                       schema_overrides={"y_hit": pl.Boolean, "y_sustained": pl.Boolean,
                                         "season": pl.Int32, "week": pl.Int32})  # fmt: skip
    assert pr.write_outcomes(store, new) == 1
    both = pr.read_table(store, "outcomes").sort("module", descending=True)
    assert both.get_column("y_value").to_list() == [None, 12.5]
    assert both.row(0)[:8] == radar.row(0)  # the Radar's row is untouched
    legacy = new.drop("y_value").with_columns(pl.lit("waiver_radar").alias("module"),
                                              pl.lit("p3").alias("entity_id"))  # fmt: skip
    assert pr.write_outcomes(store, legacy) == 1  # a writer without the column still works


# --------------------------------------------------------------------------------------
# The report and the CLI
# --------------------------------------------------------------------------------------


def test_report_is_deterministic_and_states_what_each_tag_means(world, tmp_path):
    csv_path = toy_csv(tmp_path / "backtest.csv", toy_params())
    notes = rwk.tag_notes(csv_path, 2026)
    assert set(notes) == {"sell_high", "buy_low", "legit"}
    assert "92.0% of the 200 Sell-high players fell below their PPG" in notes["sell_high"]
    assert "the same within noise" in notes["legit"] and "61.1%" in notes["legit"]
    assert rwk.tag_notes(csv_path, 2024) == {}  # a list never quotes later outcomes
    run = _run(world, now=LATER)
    kw = {"generated": "Generated at X.", "command": "c", "notes": notes}
    a, b = (rwk.build_report(run, league(), **kw) for _ in range(2))
    assert a == b and a.startswith("# Regression Watch: 2025 week 4")
    assert "**Reconstructed list (stored as 'backtest').**" in a
    assert "## Sell-high (2)" in a and "## Buy-low (1)" in a and "## Legit (0)" in a
    assert "No regression flag: the production is backed by opportunity" in a
    assert "No Legit player this week." in a and toy_params().model_version in a
    path = rwk.write_report(a, tmp_path / "r.md")
    assert path.read_text() == a


def test_a_list_before_week_4_says_it_is_earlier_than_the_backtested_weeks(world):
    """Reviewer's rule after D4a (P2): the report (and the published regression_list.note)."""
    from twm.modules.regression_watch import backtest as bt

    backtested = (rwk.FIRST_BACKTESTED_WEEK, rwk.LAST_BACKTESTED_WEEK)
    assert backtested == (bt.ALL_WEEKS[0], bt.ALL_WEEKS[-1])
    assert rwk.early_note(4) is None and rwk.early_note(14) is None
    note = rwk.early_note(3)
    assert note is not None and "earlier than the backtested weeks 4-14" in note
    kw = {"generated": "Generated at X.", "command": "c"}
    early = rwk.build_report(_run(world, week=3, now=LATER, allow_incomplete=True), league(),
                             **kw)  # fmt: skip
    assert f"- **Early in the season.** {note}" in early
    assert "Early in the season" not in rwk.build_report(_run(world, now=LATER), league(), **kw)


def test_cli_scores_stores_and_writes_the_report(world, tmp_path, monkeypatch):
    from twm import cli

    monkeypatch.setattr(cli, "_regression_approved", lambda season: toy_params(season))
    store, out = tmp_path / "s.duckdb", tmp_path / "w.md"
    args = ["regression", "score", "--season", "2025", "--week", "4", "--db", str(world),
            "--store", str(store), "--out", str(out), "--now", "2026-02-01T00:00"]  # fmt: skip
    res = runner.invoke(app, args)
    assert res.exit_code == 0, res.output
    assert "stored as 'backtest'" in res.output and "4 final outcomes" in res.output
    assert "Sell-high (2):" in res.output and out.read_text().startswith("# Regression Watch")
    assert pr.read_table(store, "predictions").height == 4
    early = runner.invoke(app, [*args[:5], "2", *args[6:]])  # nobody has 3 games yet
    assert early.exit_code == 0 and "no Regression Watch list this week" in early.output
    graded = runner.invoke(app, ["regression", "outcomes", "--db", str(world), "--store",
                                 str(store), "--now", "2026-02-01T00:00"])  # fmt: skip
    assert graded.exit_code == 0 and "wrote 4 final outcomes" in graded.output


def test_generic_commands_run_regression_watch(monkeypatch):
    from twm import cli

    calls: list[dict] = []
    monkeypatch.setattr(cli, "regression_backtest", lambda **kw: calls.append(kw))
    out = runner.invoke(app, ["backtest", "regression_watch", "--end", "2024"])
    assert out.exit_code == 0, out.output
    assert calls[0]["end"] == 2024 and calls[0]["out"] == Path(
        "reports/regression_watch/backtest.md"
    )
    bad = runner.invoke(app, ["backtest", "regression_watch", "--model", "logit"])
    assert bad.exit_code == 2 and "--model do not apply" in plain(bad.output)
    train = runner.invoke(app, ["train", "regression_watch"])
    assert train.exit_code == 2 and "twm regression pin" in plain(train.output)


# --------------------------------------------------------------------------------------
# Real data (the owner's warehouse, read-only; stores are scratch copies)
# --------------------------------------------------------------------------------------


def _real_db() -> Path:
    from twm.config import settings

    db = settings().path("warehouse")
    if not db.exists():
        pytest.skip("run `uv run twm build` first")
    return db


@pytest.mark.realdata
def test_real_parameters_are_reproduced_from_the_warehouse():
    """Recomputing D3's 2026 choice from the warehouse gives the committed file byte for byte
    (and so its 2025 record, which `twm model check` compares with the committed CSV)."""
    params, pin = rp.load_pinned_params(2026)
    fresh = rp.build_params(_real_db(), 2026, league())
    assert rp.params_json(fresh) == (ROOT / pin.file).read_text()


def _drop_week(store: Path, module: str, season: int, week: int) -> None:
    """Remove a week's rows from a SCRATCH copy of the store: the owner's store holds week 3's
    live list since 2026-09-30, which a reconstructed run must never overwrite."""
    import duckdb

    con = duckdb.connect(str(store))
    try:
        con.execute("DELETE FROM predictions WHERE module = ? AND season = ? AND week = ?",
                    [module, season, week])  # fmt: skip
    finally:
        con.close()


@pytest.mark.realdata
def test_real_2026_week_3_reconstructed_into_a_scratch_store(tmp_path):
    """The committed pin lists 2026 week 3 (reconstructed: a set clock after week 4's games)
    into a COPY of the owner's store, which is never written; the list equals D3's own
    projection of that week (bt.project_week, which re-estimates everything)."""
    db, owner = _real_db(), pr.default_path()
    store = tmp_path / "predictions_copy.duckdb"
    before = pins.sha256_of(owner) if owner.exists() else None
    if owner.exists():
        shutil.copyfile(owner, store)
        _drop_week(store, "regression_watch", 2026, 3)  # the owner's live week 3 (P2 note)
    out = tmp_path / "2026-W03.md"
    res = runner.invoke(app, ["regression", "score", "--db", str(db), "--season", "2026",
                              "--week", "3", "--store", str(store), "--now", "2026-10-08T12:00",
                              "--out", str(out)])  # fmt: skip
    assert res.exit_code == 0, res.output
    assert "stored as 'backtest'" in res.output and "INCOMPLETE" not in res.output
    got = pr.read_table(store, "predictions", "module = 'regression_watch' AND season = 2026")
    params, _ = rp.load_pinned_params(2026)
    assert set(got["kind"]) == {"backtest"} and set(got["horizon"]) == {15}
    assert set(got["model_version"]) == {params.model_version}
    wp = bt.project_week(db, 2026, 3, league())
    assert wp.choice.variant == params.variant and wp.choice.sell.x == params.x_sell
    want = wp.table.select("gsis_id", "ppg_ros", "sell_high", "buy_low", "legit").sort("gsis_id")
    mine = got.select(
        pl.col("entity_id").alias("gsis_id"),
        pl.col("score").alias("ppg_ros"),
        *[(pl.col("band") == t).fill_null(False).alias(t) for t in rwk.BAND_ORDER],
    )
    mine = mine.sort("gsis_id").with_columns(
        (pl.col("legit") | pl.col("gsis_id").is_in(
            want.filter(pl.col("buy_low") & pl.col("legit")).get_column("gsis_id").to_list()))
        .alias("legit"))  # a Buy-low starter is also Legit (band = its first tag)  # fmt: skip
    assert mine.equals(want)
    assert "## Sell-high" in out.read_text() and "## Legit" in out.read_text()
    if owner.exists():
        assert pins.sha256_of(owner) == before  # the owner's store is untouched


@pytest.mark.realdata
def test_real_2025_week_6_list_and_its_final_outcomes_match_the_backtest(tmp_path):
    """2025's parameters (built, not approved) list 2025 week 6 through the as-of view exactly
    as the batch backtest projects it, and the final outcomes are the backtest's
    rest-of-season points per game."""
    db = _real_db()
    p25 = rp.build_params(db, 2025, league())
    assert p25.variant.name == "mean_hl8_all" and (p25.x_sell, p25.x_buy) == (4.5, 3.0)
    run = rwk.run_week(db, 2025, 6, params=p25, league=league(), now=SEASON_OVER,
                       real_clock=False)  # fmt: skip
    store = tmp_path / "p.duckdb"
    rwk.store_week(run, store)
    assert rwk.update_outcomes(db, store, SEASON_OVER) == run.table.height
    from twm.modules.regression_watch.player_week import player_games_history

    hist = player_games_history(db, list(range(2009, 2026)))
    asofs = bt.asof_table(db, [2025], [6])
    rows, _ = bt.build_rows(hist, asofs, league(), [2025], variants=[p25.variant])
    want = rows.select("gsis_id", pl.col(bt.proj_col(p25.variant)).alias("ppg_ros"), "ros_ppg",
                       "evaluable").sort("gsis_id")  # fmt: skip
    assert run.table.select("gsis_id", "ppg_ros").sort("gsis_id").equals(want.select(
        "gsis_id", "ppg_ros"))  # fmt: skip
    outs = pr.read_table(store, "outcomes", "module = 'regression_watch'").sort("entity_id")
    graded = want.with_columns(pl.when("evaluable").then("ros_ppg").alias("y"))
    assert outs.get_column("y_value").to_list() == graded.get_column("y").to_list()
    assert set(outs.get_column("label_status")) == {"final"}
