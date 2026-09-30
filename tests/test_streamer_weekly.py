"""The streamer's weekly list (S2a) on the synthetic world (tests/streamer_world.py): freshness
and its exit code, live vs reconstructed by the clock, the store round trip (no outcomes, a live
week never overwritten), the report's determinism, the leakage harness and the CLI."""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from tests.streamer_world import RULES, build_world
from tests.test_streamer_backtest import toy_dataset
from tests.test_streamer_features import _extras
from tests.test_streamer_production import make_backtest
from tests.test_waiver_radar_weekly import AFTER, AS_OF, _inputs
from twm import pins
from twm import predictions as pr
from twm.asof import AsOfView, weekly_as_of
from twm.backtest.leakage import assert_future_invariant
from twm.cli import app
from twm.modules.streamer import confidence as sc
from twm.modules.streamer import production as sp
from twm.modules.streamer import weekly as sw
from twm.modules.streamer.pool import StreamerRules
from twm.modules.waiver_radar import weekly as rw

SEASON = 2025
IN_WINDOW = datetime(2025, 9, 17, 12, tzinfo=UTC)  # after week 2's as-of, before week 3's games
LATER = datetime(2025, 10, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> Path:
    return build_world(tmp_path_factory.mktemp("streamer_weekly"), extras=_extras)


@pytest.fixture(scope="module")
def approved(tmp_path_factory) -> dict:
    """The toy backtest's approved K model and D/ST rule, re-dated to rank 2025 (the world's
    season), their confidences, and a pin file with absolute paths for the CLI."""
    tmp = tmp_path_factory.mktemp("streamer_pins")
    data = toy_dataset()
    store, csv_path = make_backtest(tmp, data)
    made = sp.approve(data, store=store, season=2014, csv_path=csv_path, root=tmp,
                      path=tmp / "toy.yaml")  # fmt: skip
    pm = dataclasses.replace(sp.load_pinned_k(2014, path=tmp / "toy.yaml", root=tmp)[0],
                             season=SEASON)  # fmt: skip
    rule = sp.rule_definition(SEASON)
    kfile = sp.rprod.save_model(pm, tmp / "k")
    rfile = tmp / "rule.json"
    rfile.write_text(sp.rule_json(rule))

    def absolute(pin: pins.Pin, file: Path, version: str) -> pins.Pin:
        snaps = {t: pins.SnapshotFile(str(b.path(tmp)), b.sha256, b.rows)
                 for t, b in pin.backtest.items()}  # fmt: skip
        return dataclasses.replace(pin, season=SEASON, model_version=version, file=str(file),
                                   sha256=pins.sha256_of(file), backtest=snaps)  # fmt: skip

    pin_path = pins.write_pins(
        {"streamer_k": absolute(made["K"], kfile, pm.model_version),
         "streamer_dst": absolute(made["DST"], rfile, rule.model_version)},
        tmp / "pins.yaml",
    )  # fmt: skip
    frames = sp.load_k_snapshot(made["K"], tmp)
    hit_rates = sp.load_hit_rates(made["DST"], tmp)
    return {"k_model": pm, "rule": rule, "k_conf": sc.k_confidence(frames, SEASON),
            "d_conf": sc.dst_confidence(hit_rates, SEASON), "pin_path": pin_path}  # fmt: skip


def _run(world: Path, approved: dict, week: int, now: datetime, **kw) -> sw.WeeklyRun:
    models = {k: approved[k] for k in ("k_model", "rule", "k_conf", "d_conf")}
    kw.setdefault("allow_incomplete", True)  # the world has no snap counts (the Radar's check)
    return sw.run_week(world, SEASON, week, now=now, rules=RULES, **models, **kw)


def test_freshness_is_the_radars_plus_kicker_and_dst_rows() -> None:
    both = pl.DataFrame({"game_id": ["g1", "g2"], "n": [2, 2]})
    ok = sw.check_freshness({**_inputs(), "kickers": both, "defenses": both}, 2025, 2, AS_OF,
                            AFTER)  # fmt: skip
    assert ok.ok, ok.problems
    no_k = {**_inputs(), "kickers": both.head(1), "defenses": both}
    fr = sw.check_freshness(no_k, 2025, 2, AS_OF, AFTER)
    assert fr.problems == ["1 played game(s) have no kicker rows (fact_kicker_week) yet: DAL@SEA"]
    fr = sw.check_freshness({**_inputs(snaps="g1"), "kickers": both, "defenses": both.tail(1)},
                            2025, 2, AS_OF, AFTER)  # fmt: skip
    assert len(fr.problems) == 2 and "snap counts" in fr.problems[0]
    assert "D/ST rows (fact_defense_week)" in fr.problems[1] and "KC@PHI" in fr.problems[1]


def test_a_week_whose_data_has_not_arrived_exits_3(world, tmp_path) -> None:
    store = tmp_path / "store.duckdb"
    res = CliRunner().invoke(app, ["streamer", "score", "--db", str(world), "--season", "2025",
                                   "--week", "2", "--store", str(store), "--now",
                                   "2025-10-01T00:00"])  # fmt: skip
    assert res.exit_code == sw.EXIT_NOT_READY == 3, res.output
    assert "is not ready to score" in res.output and "snap counts" in res.output
    assert not store.exists()  # nothing stored
    with pytest.raises(rw.NotReadyError):
        _run(world, _approved_stub(), 2, LATER, allow_incomplete=False)


def _approved_stub() -> dict:
    return dict.fromkeys(("k_model", "rule", "k_conf", "d_conf"), None) | {
        "k_model": type("M", (), {"season": SEASON})(),
        "rule": type("R", (), {"season": SEASON})(),
    }


def test_live_only_on_the_real_clock_inside_the_window(world, approved) -> None:
    live = _run(world, approved, 2, IN_WINDOW)
    assert live.kind == "live" and live.incomplete
    assert _run(world, approved, 2, IN_WINDOW, real_clock=False).kind == "backtest"
    assert _run(world, approved, 2, LATER).kind == "backtest"
    before = datetime(2025, 9, 16, 13, tzinfo=UTC)  # an hour before the as-of
    early = _run(world, approved, 2, before)
    assert (
        early.kind == "backtest"
        and "Tuesday's as-of has not come yet" in early.freshness.problems[0]
    )
    with pytest.raises(ValueError, match="last regular-season week"):
        _run(world, approved, 4, LATER)
    s = live.scored
    assert set(s["position"]) == {"K", "DST"} and s.filter(pl.col("position") == "DST")[
        "score"].null_count() == s.filter(pl.col("position") == "DST").height  # fmt: skip
    for pos in ("K", "DST"):
        ranks = s.filter(pl.col("position") == pos)["rank"].to_list()
        assert ranks == list(range(1, len(ranks) + 1))


def test_store_round_trip_without_outcomes_and_live_kept(world, approved, tmp_path) -> None:
    store = tmp_path / "p.duckdb"
    live = _run(world, approved, 2, IN_WINDOW)
    counts = sw.store_week(live, store, created_at=datetime(2025, 9, 17, 12))
    assert counts == {"predictions": live.scored.height, "model_versions": 2, "outcomes": 0}
    back = pr.read_table(store, "predictions")
    assert back.height == live.scored.height and set(back["kind"]) == {"live"}
    assert dict(back.group_by("rank_group").agg(pl.col("entity_type").first()).iter_rows()) == {
        "K": "kicker", "DST": "team_defense"}  # fmt: skip
    assert set(back["model_version"]) == {approved["k_model"].model_version,
                                          approved["rule"].model_version}  # fmt: skip
    assert pr.read_table(store, "outcomes").height == 0
    dst = back.filter(pl.col("rank_group") == "DST").sort("rank").row(0, named=True)
    band = json.loads(dst["band"])
    assert band["basis"] == "rank" and band["rank_from"] <= 1 <= band["rank_to"]
    assert dst["score"] is None and dst["tier"] in pr.TIERS and back["incomplete"].all()
    k = back.filter(pl.col("rank_group") == "K").sort("rank").row(0, named=True)
    assert set(json.loads(k["band"])) >= {"chance", "lo", "hi", "p_from", "p_to"}
    reasons = json.loads(dst["reasons_json"])
    assert reasons[0]["feature"] == "next_opp_points_per_game"
    with pytest.raises(pr.LiveWeekError):
        sw.store_week(_run(world, approved, 2, LATER), store)
    vers = pr.read_table(store, "model_versions")
    assert set(vers["model"]) == {"logit_k", "baseline_opponent_dst"}


@pytest.mark.parametrize("week", [1, 2, 3])
def test_the_list_ignores_everything_after_the_as_of(world, approved, week) -> None:
    models = {k: approved[k] for k in ("k_model", "rule", "k_conf", "d_conf")}

    def builder(view: AsOfView) -> pl.DataFrame:
        return sw.score_week(view, SEASON, week, rules=RULES, **models).drop("as_of")

    out = assert_future_invariant(builder, world, weekly_as_of(world, SEASON, week),
                                  key=["position", "entity_id"])  # fmt: skip
    assert out.height > 0


def test_report_is_deterministic_and_says_what_ranks_each_list(world, approved) -> None:
    run = _run(world, approved, 2, LATER)
    notes = {"K": "K note.", "DST": "DST note."}
    a = sw.build_report(run, generated="Generated now.", command="cmd", notes=notes)
    b = sw.build_report(_run(world, approved, 2, LATER), generated="Generated now.",
                        command="cmd", notes=notes)  # fmt: skip
    assert a == b
    for text in ("# K and D/ST streamer: 2025 week 2", "**Reconstructed list", "## Kickers",
                 "## Team defenses (D/ST)", "**D/ST: a simple rule, not a model.**",
                 "logistic regression", "WARNING: incomplete data", "K note.", "DST note.",
                 "How the priorities did on the DST lists of the 2013-2013 backtest"):  # fmt: skip
        assert text in a, text
    live = sw.build_report(_run(world, approved, 2, IN_WINDOW), generated="g", command="c")
    assert "**Live list.**" in live


def test_method_notes_quote_the_backtest_only_for_later_seasons(tmp_path) -> None:
    from twm.config import ROOT

    csv = ROOT / "reports" / "streamer" / "backtest.csv"
    notes = sw.method_notes(csv, 2026)
    assert set(notes) == {"K", "DST"} and "simple rule, not a model" in notes["DST"]
    assert sw.method_notes(csv, 2025) == {} and sw.method_notes(tmp_path / "no.csv", 2026) == {}


def test_cli_scores_with_the_pins_and_twm_score_includes_the_streamer(
    world, approved, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(pins, "default_pin_path", lambda: approved["pin_path"])
    monkeypatch.setattr(StreamerRules, "from_config", classmethod(lambda cls, *a, **k: RULES))
    store, out = tmp_path / "store.duckdb", tmp_path / "w.md"
    args = ["--db", str(world), "--season", "2025", "--week", "2", "--store", str(store),
            "--allow-incomplete", "--now", "2025-10-01T00:00", "--out", str(out)]  # fmt: skip
    res = CliRunner().invoke(app, ["streamer", "score", *args])
    assert res.exit_code == 0, res.output
    assert "stored as 'backtest' (INCOMPLETE DATA)" in res.output and "DST:" in res.output
    assert out.exists() and "## Kickers" in out.read_text()
    n = pr.read_table(store, "predictions").height
    assert n > 0 and f"stored {n} predictions" in res.output
    # a tampered pinned file is refused before anything is scored
    rule_file = Path(pins.read_pins(approved["pin_path"])["streamer_dst"].file)
    text = rule_file.read_text()
    rule_file.write_text(text + " ")
    try:
        bad = CliRunner().invoke(app, ["streamer", "score", *args])
        assert bad.exit_code == 1 and "not the approved file" in bad.output
    finally:
        rule_file.write_text(text)
    # `twm score --module streamer` runs the same command (exit 3 without --allow-incomplete)
    res = CliRunner().invoke(app, ["score", "--module", "streamer", "--db", str(world),
                                   "--as-of", "2025-W2", "--store", str(store)])  # fmt: skip
    assert res.exit_code == 3 and "is not ready to score" in res.output


# ---- real data (opt-in: uv run pytest -m realdata; reads the real cache, dataset and pins,
# writes only a scratch copy of the predictions store) -------------------------------------


@pytest.mark.realdata
def test_real_2026_week_3_reconstructed_into_a_scratch_store(real_full_db, tmp_path) -> None:
    """The committed pins rank 2026 week 3 (reconstructed: a set clock after week 4's games)
    into a COPY of the predictions store; the owner's store is never written. The pinned K
    model is the backtest's 2026 fold (not constant) and reproduces its scores bit for bit."""
    import shutil

    import numpy as np

    from twm.config import ROOT

    path, _ = real_full_db
    owner = pr.default_path()
    store = tmp_path / "predictions_copy.duckdb"
    before = pins.sha256_of(owner) if owner.exists() else None
    if owner.exists():
        shutil.copyfile(owner, store)
        # the owner's store holds week 3's LIVE list since 2026-09-30 (P2), which a
        # reconstructed run must never overwrite: drop it from the scratch copy only
        import duckdb

        con = duckdb.connect(str(store))
        con.execute("DELETE FROM predictions WHERE module = 'streamer' AND season = 2026 "
                    "AND week = 3")  # fmt: skip
        con.close()
    out = tmp_path / "2026-W03.md"
    res = CliRunner().invoke(app, ["streamer", "score", "--db", str(path), "--season", "2026",
                                   "--week", "3", "--store", str(store), "--now",
                                   "2026-10-08T12:00", "--out", str(out)])  # fmt: skip
    assert res.exit_code == 0, res.output
    assert "stored as 'backtest'" in res.output and "INCOMPLETE" not in res.output
    got = pr.read_table(store, "predictions", "module = 'streamer' AND season = 2026 AND week = 3")
    assert set(got["kind"]) == {"backtest"} and set(got["rank_group"]) == {"K", "DST"}
    pm, _ = sp.load_pinned_k(2026)
    rule, _ = sp.load_pinned_rule(2026)
    assert set(got["model_version"]) == {pm.model_version, rule.model_version}
    dst = got.filter(pl.col("rank_group") == "DST").sort("rank")
    assert dst["raw_score"].to_list() == sorted(dst["raw_score"].to_list(), reverse=True)
    k = got.filter(pl.col("rank_group") == "K")
    assert k["score"].n_unique() > 1  # the 2026 K model is not constant
    assert "**D/ST: a simple rule, not a model.**" in out.read_text()
    if owner.exists():
        assert pins.sha256_of(owner) == before  # the owner's store is untouched
    ds_path = ROOT / "data" / "streamer" / "dataset.parquet"
    if ds_path.exists():
        ds = pl.read_parquet(ds_path)
        fresh = sp.train_k(ds, 2026)
        assert fresh.model_version == pm.model_version
        rows = ds.filter((pl.col("season") == 2026) & (pl.col("position") == "K"))
        assert np.array_equal(fresh.predict(rows)[1], pm.predict(rows)[1])
