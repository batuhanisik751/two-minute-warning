"""Regression Watch step D2: the stability study and the shrinkage table.

Offline tests run on synthetic D1 frames with a known signal and noise (the study must recover
them), check the filters (minimum games, one position per player-season, rates need chances),
the point-in-time rule of ``shrinkage(seasons)`` (a later season never changes an earlier
estimate), the report, the CLI and the committed notebook. Opt-in realdata tests run the study
on the real warehouse and execute the notebook."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from polars.testing import assert_frame_equal
from typer.testing import CliRunner

from twm import registry as rg
from twm.cli import app
from twm.config import ROOT
from twm.modules.regression_watch import player_week as pw
from twm.modules.regression_watch import stability as st
from twm.modules.regression_watch import stability_report as sr

NOTEBOOK = ROOT / "notebooks" / "02_regression_stability.ipynb"


def synth(
    *,
    seasons: tuple[int, ...] = (2010,),
    players: int = 300,
    games: int = 16,
    var_signal: float = 1.0,
    var_noise: float = 16.0,
    position: str = "WR",
    seed: int = 7,
) -> pl.DataFrame:
    """A D1-shaped frame: per player-season a true FPOE level ~ N(0, var_signal), per game
    FPOE = level + N(0, var_noise); xFP = 10 + 3 x a true role level + N(0, 1) (sticky by
    construction); 6 targets and 4 expected catches per game, catches = 4 + noise."""
    rng = np.random.default_rng(seed)
    n = len(seasons) * players * games
    season = np.repeat(np.array(seasons), players * games)
    pid = np.tile(np.repeat(np.arange(players), games), len(seasons))
    week = np.tile(np.arange(1, games + 1), len(seasons) * players)
    level = np.repeat(rng.normal(0, math.sqrt(var_signal), len(seasons) * players), games)
    role = np.repeat(rng.normal(0, 1, len(seasons) * players), games)
    fpoe = level + rng.normal(0, math.sqrt(var_noise), n)
    xfp = 10 + 3 * role + rng.normal(0, 1, n)
    cols: dict[str, object] = {
        "season": season, "week": week,
        "game_id": [f"{s}_{w:02d}_{p}" for s, w, p in zip(season, week, pid, strict=True)],
        "gsis_id": [f"00-{p:07d}" for p in pid], "team": "AAA", "position": position,
        "position_source": "roster", "fantasy_points": xfp + fpoe, "xfp": xfp, "fpoe": fpoe,
        "points_ng": xfp + fpoe, "xfp_ng": xfp, "fpoe_ng": fpoe, "targets": 6.0,
        "receptions": 4.0 + rng.normal(0, 1, n), "receptions_exp": 4.0,
    }  # fmt: skip
    kick = [datetime(int(s), 9, 1, tzinfo=UTC) + timedelta(weeks=int(w)) for s, w in
            zip(season, week, strict=True)]  # fmt: skip
    cols["available_at"] = [k.replace(tzinfo=None) for k in kick]
    df = pl.DataFrame({c: cols.get(c) for c in pw.FRAME_COLUMNS if c in cols})
    missing = [pl.lit(0.0).alias(c) for c in pw.FRAME_COLUMNS if c not in cols]
    return df.with_columns(missing).cast(pw.FRAME_SCHEMA).select(pw.FRAME_COLUMNS)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# The math on synthetic data with a known signal-to-noise ratio
# --------------------------------------------------------------------------------------


def test_reliability_formula():
    assert st.reliability(1.0, 16.0, 16) == pytest.approx(0.5)
    assert st.reliability(1.0, 16.0, 8) == pytest.approx(1 / 3)
    assert st.reliability(1.0, 16.0, 0) == 0.0
    assert st.reliability(-0.2, 16.0, 8) == 0.0  # no signal: keep nothing
    assert st.reliability(float("nan"), 16.0, 8) == 0.0
    assert st.reliability(1.0, 0.0, 8) == 1.0
    assert math.isnan(st.pearson(np.array([1.0, 2.0]), np.array([2.0, 1.0])))  # too few pairs
    assert st.pearson(np.arange(5.0), 2 * np.arange(5.0) + 1) == pytest.approx(1.0)


def test_signal_and_noise_are_recovered():
    """3,000 player-seasons of 16 games, signal 1 and noise 16 per game: the split-half
    estimates recover both, and r(g) follows."""
    f = synth(seasons=(2010, 2011, 2012), players=1000)
    table = st.halves(st.study_games(f), "odd_even")
    p = st.pairs(table, st.METRIC_BY_NAME["fpoe"], "WR")
    assert p.n == 3000 and set(p.g_a) == {8} and set(p.g_b) == {8}
    vs, vn = st.signal_noise(p)
    assert vs == pytest.approx(1.0, abs=0.2)
    assert vn == pytest.approx(16.0, rel=0.05)
    sk = st.shrinkage([2010, 2011, 2012], frame=f, metrics=["fpoe"])
    r8 = sk.filter((pl.col("position") == "WR") & (pl.col("g") == 8))["reliability"][0]
    assert r8 == pytest.approx(1 / 3, abs=0.05)
    assert st.shrinkage_factor(sk, "WR", 16) == pytest.approx(0.5, abs=0.05)
    assert sk.filter(pl.col("position") == "WR")["reliability"].is_sorted()  # grows with g
    assert sk.filter(pl.col("position") == "RB")["n"].to_list() == [0] * st.MAX_G


def test_split_half_correlation_matches_the_known_ratio():
    """Odd/even halves of 8 games: the correlation is r(8) = 1 / (1 + 16 / 8) = 1/3 for
    FPOE, and 9 / (9 + 1 / 8) = 0.986 for xFP (signal 9, noise 1)."""
    f = synth(seasons=(2010, 2011), players=1500)
    sh = st.split_half(f, n_boot=200)
    one = sh.filter((pl.col("split") == "odd_even") & (pl.col("position") == "WR"))
    got = {r["metric"]: r for r in one.iter_rows(named=True)}
    assert got["fpoe"]["r"] == pytest.approx(1 / 3, abs=0.05)
    assert got["xfp"]["r"] == pytest.approx(9 / 9.125, abs=0.01)
    assert got["fpoe"]["lo"] < got["fpoe"]["r"] < got["fpoe"]["hi"]
    assert got["fpoe"]["n"] == 3000
    # the per-player level is the same in both halves: first/second agrees here
    fs = sh.filter(
        (pl.col("split") == "first_second") & (pl.col("metric") == "fpoe")
        & (pl.col("position") == "WR")
    )  # fmt: skip
    assert fs["r"][0] == pytest.approx(1 / 3, abs=0.05)
    # no QB/RB/TE rows in the frame: n 0, r NaN, and QB-only / receiver-only metrics respected
    assert sh.filter(pl.col("position") == "QB")["n"].sum() == 0
    assert "catch_rate_over_expected" not in sh.filter(pl.col("position") == "QB")["metric"]
    assert "completion_rate_over_expected" not in one["metric"]


def test_pure_noise_has_no_reliability():
    f = synth(seasons=(2010, 2011), players=1500, var_signal=0.0)
    sk = st.shrinkage([2010, 2011], frame=f, metrics=["fpoe"])
    wr = sk.filter(pl.col("position") == "WR")
    assert wr["reliability"].max() < 0.08  # 0 when the covariance comes out negative


def test_bootstrap_is_seeded_and_brackets_the_estimate():
    f = synth(players=400)
    a = st.split_half(f, n_boot=100, seed=3)
    b = st.split_half(f, n_boot=100, seed=3)
    assert_frame_equal(a, b)
    c = st.split_half(f, n_boot=100, seed=4)
    assert not a.equals(c)
    ok = a.filter(pl.col("n") > 0)
    assert ((ok["lo"] <= ok["r"]) & (ok["r"] <= ok["hi"])).all()


# --------------------------------------------------------------------------------------
# Filters: minimum games, what a game is, one position per player-season, halves, rates
# --------------------------------------------------------------------------------------


def _player(frame: pl.DataFrame, pid: int, season: int = 2010) -> pl.DataFrame:
    return frame.filter((pl.col("gsis_id") == f"00-{pid:07d}") & (pl.col("season") == season))


def test_minimum_games_filter_and_what_counts_as_a_game():
    f = synth(players=4, games=9)
    # player 0: 7 games (dropped); player 1: 8 games (kept); player 2: 9 rows but one without
    # an opportunity (xfp NULL, e.g. only a return stat) -> 8 games (kept); player 3: 9 games
    f = f.filter(~((pl.col("gsis_id") == "00-0000000") & (pl.col("week") >= 8)))
    f = f.filter(~((pl.col("gsis_id") == "00-0000001") & (pl.col("week") == 9)))
    f = f.with_columns(
        pl.when((pl.col("gsis_id") == "00-0000002") & (pl.col("week") == 4))
        .then(None).otherwise(pl.col("xfp")).alias("xfp")
    )  # fmt: skip
    games = st.study_games(f)
    n = dict(games.group_by("gsis_id").agg(pl.len()).iter_rows())
    assert n == {"00-0000001": 8, "00-0000002": 8, "00-0000003": 9}
    assert set(st.study_games(f, min_games=7)["gsis_id"]) >= {"00-0000000"}
    p2 = games.filter(pl.col("gsis_id") == "00-0000002").sort("week")
    assert p2["week"].to_list() == [1, 2, 3, 5, 6, 7, 8, 9]
    assert p2["game_no"].to_list() == list(range(1, 9))


def test_halves_odd_even_and_first_second():
    games = st.study_games(synth(players=1, games=9))
    assert games["half_odd_even"].to_list() == list("ababababa")
    assert games["half_first_second"].to_list() == list("aaaabbbbb")  # first 9 // 2 games
    for split, (ga, gb) in (("odd_even", (5, 4)), ("first_second", (4, 5))):
        t = st.halves(games, split)
        assert (t["g_a"][0], t["g_b"][0]) == (ga, gb)
        want_a = games.filter(pl.col(f"half_{split}") == "a")["fpoe"].sum()
        assert t["fpoe__num_a"][0] == pytest.approx(want_a)
        assert t["fpoe__den_a"][0] == ga  # per game: the denominator is the games
        assert t["catch_rate_over_expected__den_a"][0] == 6 * ga  # a rate: the targets


def test_one_position_per_player_season():
    f = synth(players=1, games=9)
    # 5 games at WR, 4 at RB -> one WR player-season of 9 games
    f = f.with_columns(
        pl.when(pl.col("week") >= 6).then(pl.lit("RB")).otherwise(pl.col("position"))
        .alias("position")
    )  # fmt: skip
    games = st.study_games(f)
    assert games.height == 9 and set(games["position"]) == {"WR"}
    # a tie (4 and 4 of 8 games) goes to the position of his latest game
    tie = f.filter(pl.col("week") >= 2)
    assert set(st.study_games(tie)["position"]) == {"RB"}


def test_rates_need_chances_in_each_half():
    f = synth(players=10, games=8).with_columns(
        pl.when(pl.col("gsis_id") == "00-0000000").then(1.0).otherwise(pl.col("targets"))
        .alias("targets")
    )  # fmt: skip
    table = st.halves(st.study_games(f), "odd_even")
    catch = st.METRIC_BY_NAME["catch_rate_over_expected"]
    assert st.pairs(table, catch, "WR").n == 9  # 4 targets a half < 10: out
    assert st.pairs(table, catch, "WR", min_denominator=4).n == 10
    assert st.pairs(table, st.METRIC_BY_NAME["fpoe"], "WR").n == 10  # per game: all


# --------------------------------------------------------------------------------------
# Point in time: shrinkage(seasons) never reads another season
# --------------------------------------------------------------------------------------


def test_a_later_season_never_changes_an_earlier_estimate():
    f = synth(seasons=(2010, 2011, 2012), players=300)
    early = st.shrinkage([2010, 2011], frame=f)
    assert_frame_equal(early, st.shrinkage([2010, 2011], frame=f.filter(pl.col("season") < 2012)))
    # scramble 2012 completely (other players, huge FPOE): the 2010-2011 table does not move
    wild = f.with_columns(
        pl.when(pl.col("season") == 2012).then(pl.col("fpoe") * 50 + 7).otherwise(pl.col("fpoe"))
        .alias("fpoe")
    )  # fmt: skip
    assert_frame_equal(early, st.shrinkage([2010, 2011], frame=wild))
    assert not early.equals(st.shrinkage([2010, 2011, 2012], frame=wild))  # the test has teeth
    assert early["last_season"].max() == 2011 and early["first_season"].min() == 2010
    # the split-half study follows the same rule
    assert_frame_equal(st.split_half(f, [2010], n_boot=20), st.split_half(wild, [2010], n_boot=20))


def test_shrinkage_loads_only_the_requested_seasons(monkeypatch):
    asked: list[list[int]] = []

    def fake_load(db, seasons):
        asked.append(list(seasons))
        return synth(seasons=tuple(seasons), players=50)

    monkeypatch.setattr(st, "load_frame", fake_load)
    sk = st.shrinkage([2012, 2010, 2011, 2010], db="unused.duckdb")
    assert asked == [[2010, 2011, 2012]]
    assert sk["n"].max() == 150
    with pytest.raises(ValueError, match="at least one season"):
        st.shrinkage([], frame=synth())


def test_as_of_drops_rows_not_public_yet():
    f = synth(seasons=(2010,), players=200)
    cut = datetime(2010, 9, 1, tzinfo=UTC) + timedelta(weeks=10, hours=1)  # after week 10
    got = st.shrinkage([2010], frame=f, as_of=cut)
    want = st.shrinkage([2010], frame=f.filter(pl.col("week") <= 10))
    assert_frame_equal(got, want)
    assert st.study_games(f, as_of=cut)["n_games"].max() == 10


def test_shrinkage_rejects_a_rate_and_unknown_positions():
    f = synth()
    with pytest.raises(ValueError, match="per-game"):
        st.shrinkage([2010], frame=f, metrics=["catch_rate_over_expected"])
    with pytest.raises(LookupError):
        st.shrinkage_factor(st.shrinkage([2010], frame=f), "K", 8)


# --------------------------------------------------------------------------------------
# Report, CLI, registry
# --------------------------------------------------------------------------------------


def _two_positions() -> pl.DataFrame:
    wr = synth(seasons=(2010, 2011, 2012), players=150)
    rb = synth(seasons=(2010, 2011, 2012), players=150, position="RB", seed=9)
    rb = rb.with_columns(("RB" + pl.col("gsis_id")).alias("gsis_id"))
    return pl.concat([wr, rb])


def test_report_on_a_synthetic_frame(tmp_path):
    f = _two_positions()
    rep = sr.build_stability_report(None, [2010, 2011, 2012], frame=f, n_boot=50)
    md = rep.markdown
    for n in range(1, 8):
        assert f"\n## {n}. " in md, n
    assert "Opportunity is far stickier than efficiency at every position" in md
    assert "Warehouse built unknown" in md
    assert rep.summary[0].startswith("Opportunity is far stickier")
    assert all(set(r) == set(sr.CSV_COLUMNS) for r in rep.csv_rows)
    tables = {r["table"] for r in rep.csv_rows}
    assert tables == {"split_half", "shrinkage"}
    wr8 = [r for r in rep.csv_rows if r["table"] == "shrinkage" and r["window"] == "2010-2012"
           and r["position"] == "WR" and r["metric"] == "fpoe" and r["g"] == 8]  # fmt: skip
    assert len(wr8) == 1 and wr8[0]["lo"] != "" and wr8[0]["lo"] <= wr8[0]["value"]
    csv_path = sr.write_stability_report(rep, tmp_path / "rw" / "stability.md")
    assert (tmp_path / "rw" / "stability.md").read_text() == md
    assert csv_path.read_text().splitlines()[0] == ",".join(sr.CSV_COLUMNS)
    again = sr.build_stability_report(None, [2010, 2011, 2012], frame=f, n_boot=50)
    assert again.markdown == md and again.csv_rows == rep.csv_rows  # deterministic


def test_report_refuses_seasons_without_data():
    with pytest.raises(ValueError, match="2009"):
        sr.build_stability_report(None, [2006, 2008], frame=synth())
    with pytest.raises(ValueError, match="player-season"):
        sr.build_stability_report(None, [2015], frame=synth(), n_boot=5)


def test_windows_cover_blocks_and_growing_windows():
    assert sr.windows(2009, 2025) == [(2009, 2014), (2015, 2019), (2020, 2025), (2009, 2015),
                                      (2009, 2020), (2009, 2024)]  # fmt: skip
    assert sr.windows(2020, 2020) == []


def test_cli_stability(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "load_frame", lambda db, seasons: _two_positions())
    monkeypatch.setattr(sr, "_built_at", lambda db: "2026-01-01 00:00:00")
    db = tmp_path / "warehouse.duckdb"
    db.write_bytes(b"")
    out = tmp_path / "rw" / "stability.md"
    runner = CliRunner()
    res = runner.invoke(app, ["regression", "stability", "--db", str(db), "--start", "2010",
                              "--end", "2012", "--out", str(out), "--boot", "20"])  # fmt: skip
    assert res.exit_code == 0, res.output
    assert "WR FPOE/game shrinkage: r(4)" in res.output
    assert out.exists() and out.with_suffix(".csv").exists()
    res = runner.invoke(app, ["regression", "stability", "--db", str(db), "--start", "2013",
                              "--end", "2012"])  # fmt: skip
    assert res.exit_code != 0
    res = runner.invoke(app, ["regression", "stability", "--db", str(tmp_path / "nope.duckdb")])
    assert res.exit_code == 1 and "warehouse not found" in res.output


def test_every_stability_metric_is_registered():
    names = [m.name for m in st.METRICS] + [
        "split_half_correlation", "signal_variance", "noise_variance", "reliability",
        "shrinkage_factor", "games_for_half_weight", "prior_mean",
    ]  # fmt: skip
    for name in names:
        e = rg.get(name)
        assert {"regression_watch", "shared"} & set(e.modules), name
        assert e.status == "available", name
    for m in st.METRICS:
        if not m.per_game:
            assert rg.get(m.name).step == "D2" and rg.get(m.name).model_output, m.name


# --------------------------------------------------------------------------------------
# The committed report and notebook (cheap checks of the files in the repository)
# --------------------------------------------------------------------------------------

REPORT = ROOT / "reports" / "regression_watch" / "stability.md"


def test_committed_report_and_csv_agree():
    import csv

    md = REPORT.read_text(encoding="utf-8")
    with REPORT.with_suffix(".csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0]) == list(sr.CSV_COLUMNS)
    for n in range(1, 8):
        assert f"\n## {n}. " in md, n
    full = [r for r in rows if r["table"] == "shrinkage" and r["window"] == "2009-2025"
            and r["metric"] == "fpoe"]  # fmt: skip
    assert {r["position"] for r in full} == set(pw.FANTASY_POSITIONS)
    for r in full:
        assert 0 <= float(r["value"]) < 1
        if r["g"] == "8":
            assert f"{float(r['value']):.2f} (" in md  # the r(8) cell of section 5
    heads = [r for r in rows if r["table"] == "split_half" and r["split"] == "odd_even"]
    assert {r["metric"] for r in heads} == {m.name for m in st.METRICS}


def test_committed_notebook_is_executed_clean_and_portable():
    import nbformat

    raw = NOTEBOOK.read_text(encoding="utf-8")
    assert len(raw.encode()) < 400_000
    nb = nbformat.reads(raw, as_version=4)
    nbformat.validate(nb)
    code = [c for c in nb.cells if c.cell_type == "code"]
    assert len(code) >= 10
    for c in code:
        assert c.execution_count is not None, c.source[:60]
        for out in c.get("outputs", []):
            assert out.output_type != "error", c.source[:60]
            assert out.get("name") != "stderr", (c.source[:60], out.get("text", "")[:200])
    assert [c.execution_count for c in code] == list(range(1, len(code) + 1))
    for marker in ("/Users/", "/home/", "/private/", "C:\\\\"):
        assert marker not in raw, marker
    titles = [c.source.splitlines()[0] for c in nb.cells if c.cell_type == "markdown"]
    for n in range(1, 8):
        assert any(t.startswith(f"## {n}.") for t in titles), n
    assert json.loads(raw)["metadata"]["kernelspec"]["name"] == "python3"


# --------------------------------------------------------------------------------------
# Real data (opt-in: `uv run pytest -m realdata`): the project's warehouse
# --------------------------------------------------------------------------------------


def _real_db() -> Path:
    from twm.config import settings

    db = settings().path("warehouse")
    if not db.exists():
        pytest.skip("run `uv run twm build` first")
    return db


@pytest.mark.realdata
def test_real_study_matches_the_expected_shape():
    frame = st.load_frame(_real_db(), range(2009, 2026))
    sh = st.split_half(frame, range(2009, 2026), n_boot=100)
    oe = sh.filter(pl.col("split") == "odd_even")
    for pos in pw.FANTASY_POSITIONS:
        one = {r["metric"]: r for r in oe.filter(pl.col("position") == pos).iter_rows(named=True)}
        assert one["xfp"]["n"] > 400 and one["xfp"]["lo"] > one["fpoe"]["hi"], pos
    sk = st.shrinkage(range(2009, 2026), frame=frame)
    r8 = sk.filter((pl.col("g") == 8) & (pl.col("metric") == "fpoe"))["reliability"]
    assert ((r8 > 0) & (r8 < 0.5)).all()
    # the committed CSV was made from the same warehouse (fails after a rebuild changes it:
    # rerun `uv run twm regression stability`)
    with REPORT.with_suffix(".csv").open(encoding="utf-8") as f:
        import csv

        rows = {(r["window"], r["position"], r["metric"], r["g"]): float(r["value"])
                for r in csv.DictReader(f) if r["table"] == "shrinkage"}  # fmt: skip
    for r in sk.filter(pl.col("g") == 8).iter_rows(named=True):
        key = ("2009-2025", r["position"], r["metric"], "8")
        assert rows[key] == pytest.approx(r["reliability"], abs=1e-5), key


@pytest.mark.realdata
def test_real_notebook_executes():
    _real_db()
    import nbformat
    from nbclient import NotebookClient

    nb = nbformat.read(NOTEBOOK, as_version=4)
    NotebookClient(nb, timeout=600, kernel_name="python3",
                   resources={"metadata": {"path": str(ROOT / "notebooks")}}).execute()  # fmt: skip
    errors = [o for c in nb.cells if c.cell_type == "code" for o in c.get("outputs", [])
              if o.output_type == "error"]  # fmt: skip
    assert not errors
