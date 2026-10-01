"""G5: the nfl4th benchmark driver (twm.modules.decisions.benchmark, benchmark_report).

Offline, on synthetic graded rows: the exported nfl4th columns and their mapping, the rows
nfl4th evaluates, the 1:1 join on the play key, the comparison columns, the report in both run
states, and the clean skip without Rscript or nfl4th (a fake Rscript stands in for R: the R
script itself is never run in tests). Real data (``-m realdata``): the exported 2025 clock
column equals the warehouse's quarter_seconds_remaining.
"""

from __future__ import annotations

import stat
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from twm.modules.decisions import benchmark as bm
from twm.modules.decisions import benchmark_report as br


def graded_row(play_id: int, **over) -> dict:
    """One stored fourth-down row (only the columns the benchmark reads)."""
    r = dict(game_id="2025_01_AAA_BBB", play_id=play_id, season=2025, week=1, season_type="REG",
             qtr=2, half_seconds_remaining=1000, game_seconds_remaining=2800, home_team="BBB",
             away_team="AAA", posteam="AAA", defteam="BBB", opening_kicker="BBB", ydstogo=4,
             yardline_100=40, score_differential=-3, posteam_timeouts_remaining=3,
             defteam_timeouts_remaining=2, exclusion=None, grade="clear", chosen="punt",
             recommended="go", p_convert=0.5, wp_go_success=0.6, wp_go_failure=0.4, wp_go=0.5,
             fg_available=True, p_make=0.6, wp_fg_make=0.55, wp_fg_miss=0.40, wp_fg=0.49,
             punt_available=True, wp_punt=0.47, field_goal_result=None,
             outcome="punted")  # fmt: skip
    r.update(over)
    return r


def nfl4th_row(play_id: int, **over) -> dict:
    """One row of nfl4th.R's output ("full")."""
    r = dict(game_id="2025_01_AAA_BBB", play_id=play_id, nfl4th_version="1.0.7",
             nfl4th_status="full", fg_make_prob_bundled=0.6, go_boost=1.0, first_down_prob=0.5,
             wp_fail=0.4, wp_succeed=0.6, go_wp=0.5, fg_make_prob=0.6, miss_fg_wp=0.4,
             make_fg_wp=0.55, fg_wp=0.49, punt_wp=0.48, nfl4th_recommended="go")  # fmt: skip
    r.update(over)
    return r


def test_export_states_has_every_documented_column_in_order():
    rows = pl.DataFrame([
        graded_row(1),
        graded_row(2, qtr=1, half_seconds_remaining=1700, season_type="POST", opening_kicker="AAA"),
        graded_row(3, qtr=3, half_seconds_remaining=905),
        graded_row(4, qtr=4, half_seconds_remaining=30),
    ])  # fmt: skip
    s = bm.export_states(rows)
    assert s.columns == list(bm.STATE_COLUMNS)
    assert s.get_column("type").to_list() == ["reg", "post", "reg", "reg"]
    # nfl4th rebuilds half / game seconds from the quarter clock
    assert s.get_column("quarter_seconds_remaining").to_list() == [1000, 800, 5, 30]
    # the home team (BBB) kicked off first in games 1, 3, 4; the away team in game 2
    assert s.get_column("home_opening_kickoff").to_list() == [1, 0, 1, 1]
    assert s.null_count().sum_horizontal().item() == 0


def test_select_rows_keeps_what_nfl4th_evaluates():
    rows = pl.DataFrame([
        graded_row(1),
        graded_row(2, qtr=5, game_seconds_remaining=300),  # overtime
        graded_row(3, qtr=4, game_seconds_remaining=15, half_seconds_remaining=15),
        graded_row(4, qtr=4, game_seconds_remaining=16, half_seconds_remaining=16),
        graded_row(5, exclusion="penalty_no_play"),  # not graded by us
    ])  # fmt: skip
    kept, drops = bm.select_rows(rows)
    assert kept.get_column("play_id").to_list() == [1, 4]
    assert drops == {"overtime": 1, "last_15_seconds": 1}


def test_join_is_one_to_one_on_the_play_key():
    rows = pl.DataFrame([graded_row(1), graded_row(2)])
    out = pl.DataFrame([nfl4th_row(2, go_wp=0.7), nfl4th_row(1)])  # any order
    j = bm.join(rows, out)
    assert j.height == 2 and j.get_column("play_id").to_list() == [1, 2]
    assert j.get_column("go_wp").to_list() == [0.5, 0.7]
    with pytest.raises(ValueError, match="1 missing"):
        bm.join(rows, out.head(1))
    with pytest.raises(ValueError, match="1 unknown"):
        bm.join(rows, pl.concat([out, pl.DataFrame([nfl4th_row(3)])]))
    with pytest.raises(ValueError, match="duplicate"):
        bm.join(rows, pl.concat([out, out.head(1)]))


def test_compare_agreement_go_gain_and_likely_cause():
    rows = pl.DataFrame([
        graded_row(1),  # we: go; best kick = FG .49 -> go gain +1.0
        graded_row(2, fg_available=False, recommended="punt", wp_go=0.45),  # kick = punt .47
        graded_row(3, recommended="field_goal", wp_go=0.45, p_make=0.9),
    ])  # fmt: skip
    out = pl.DataFrame([
        nfl4th_row(1, go_boost=1.0),
        nfl4th_row(2, nfl4th_recommended="go", go_boost=2.0, first_down_prob=0.7),
        nfl4th_row(3, nfl4th_recommended="go", go_boost=0.5, fg_make_prob=0.5),
    ])  # fmt: skip
    c = bm.compare(bm.join(rows, out))
    assert c.get_column("agree").to_list() == [True, False, False]
    assert c.get_column("our_go_gain").to_list() == pytest.approx([1.0, -2.0, -4.0])
    assert c.get_column("go_gain_diff").to_list() == pytest.approx([0.0, -4.0, -4.5])
    # row 2: conversion gap .2 x swing .2 = .04 beats the punt gap .01 and the WP gaps 0
    # row 3: make-chance gap .4 x make-miss swing .15 = .06 beats everything else
    assert c.get_column("likely_cause").to_list() == [None, bm.CAUSES[0], bm.CAUSES[1]]
    assert c.get_column("d_p_convert").to_list() == pytest.approx([0.0, -0.2, 0.0])


def test_no_kick_means_no_go_gain():
    rows = pl.DataFrame([graded_row(1, fg_available=False, punt_available=False,
                                    grade="one_option")])  # fmt: skip
    c = bm.compare(bm.join(rows, pl.DataFrame([nfl4th_row(1)])))
    assert c.get_column("our_go_gain").to_list() == [None]


def test_read_output_types_survive_an_fg_only_run(tmp_path):
    p = tmp_path / "o.csv"
    p.write_text("game_id,play_id,nfl4th_version,fg_make_prob_bundled,go_boost,first_down_prob,"
                 "wp_fail,wp_succeed,go_wp,fg_make_prob,miss_fg_wp,make_fg_wp,fg_wp,punt_wp,"
                 "nfl4th_recommended,nfl4th_status\n"
                 '"2025_01_AAA_BBB",1,"1.0.7",0.98,,,,,,,,,,,,"fg_only"\n')  # fmt: skip
    out = bm.read_output(p)
    assert out.schema["go_wp"] == pl.Float64 and out.schema["nfl4th_version"] == pl.Utf8
    j = bm.compare(bm.join(pl.DataFrame([graded_row(1)]), out))
    assert j.get_column("d_p_make").to_list() == pytest.approx([-0.38])
    assert j.get_column("agree").to_list() == [None]


# --------------------------------------------------------------------------------------
# Running R: never in tests (a fake Rscript stands in), and a clean skip without it
# --------------------------------------------------------------------------------------


def fake_rscript(tmp_path: Path, body: str) -> Path:
    """An executable that takes nfl4th.R's arguments and runs ``body`` (bash; $OUT = --out)."""
    p = tmp_path / "Rscript"
    p.write_text("#!/bin/bash\nwhile [ $# -gt 0 ]; do [ \"$1\" = --out ] && OUT=\"$2\"; shift; "
                 f"done\n{body}\n")  # fmt: skip
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


def test_missing_rscript_skips_cleanly(monkeypatch, tmp_path):
    monkeypatch.setattr(bm.shutil, "which", lambda _: None)
    with pytest.raises(bm.Nfl4thUnavailableError, match="skipped"):
        bm.find_rscript()
    with pytest.raises(bm.Nfl4thUnavailableError):  # before reading any data
        bm.benchmark(tmp_path / "no.duckdb", graded_dir=tmp_path, out_dir=tmp_path)


def test_missing_nfl4th_package_skips_cleanly(tmp_path):
    exe = fake_rscript(tmp_path, "echo 'nfl4th is not installed' >&2; exit 3")
    rows = pl.DataFrame([graded_row(1)])
    with pytest.raises(bm.Nfl4thUnavailableError, match="not installed"):
        bm.run_nfl4th(bm.export_states(rows), pl.DataFrame({"game_id": ["x"]}),
                      out_dir=tmp_path / "w", rscript=exe, progress=lambda _: None)  # fmt: skip


def test_run_nfl4th_reads_back_what_r_wrote(tmp_path):
    header = ",".join(["game_id", "play_id", *bm.NFL4TH_COLUMNS])
    line = '"2025_01_AAA_BBB",1,"1.0.7","full",0.6,1,0.5,0.4,0.6,0.5,0.6,0.4,0.55,0.49,0.48,"go"'
    exe = fake_rscript(tmp_path, f"printf '%s\\n' '{header}' '{line}' > \"$OUT\"")
    rows = pl.DataFrame([graded_row(1)])
    out = bm.run_nfl4th(bm.export_states(rows), pl.DataFrame({"game_id": ["x"]}),
                        out_dir=tmp_path / "w", rscript=exe, progress=lambda _: None)  # fmt: skip
    assert out.get_column("nfl4th_recommended").to_list() == ["go"]
    assert (tmp_path / "w" / "states_bench.csv").exists()
    exported = pl.read_csv(tmp_path / "w" / "states_bench.csv")
    assert exported.columns == list(bm.STATE_COLUMNS)
    failing = fake_rscript(tmp_path, "echo boom >&2; exit 1")
    with pytest.raises(RuntimeError, match="exit 1"):
        bm.run_nfl4th(bm.export_states(rows), pl.DataFrame({"game_id": ["x"]}),
                      out_dir=tmp_path / "w", rscript=failing, progress=lambda _: None)  # fmt: skip


def test_cli_skips_without_rscript(tmp_path):
    from twm.cli import app

    db = tmp_path / "w.duckdb"
    db.write_bytes(b"")
    res = CliRunner().invoke(app, ["decisions", "benchmark-nfl4th", "--db", str(db),
                                   "--rscript", str(tmp_path / "no_such_Rscript")])  # fmt: skip
    assert res.exit_code == 0, res.output
    assert "skipped" in res.output


# --------------------------------------------------------------------------------------
# The report, in both run states
# --------------------------------------------------------------------------------------


def _stored_run(tmp_path: Path, status: str) -> Path:
    import json

    rows = pl.DataFrame([
        graded_row(1, chosen="field_goal", field_goal_result="made", outcome="made"),
        graded_row(2, grade="toss_up", recommended="punt", wp_go=0.45, chosen="go",
                   outcome="converted"),
        graded_row(3, season=2024, game_id="2024_01_AAA_BBB", recommended="field_goal",
                   wp_go=0.45, p_make=0.9),
    ])  # fmt: skip
    full = status == "full"
    out = pl.DataFrame([
        nfl4th_row(1, nfl4th_status=status),
        nfl4th_row(2, nfl4th_status=status, nfl4th_recommended="go", go_boost=2.0),
        nfl4th_row(3, nfl4th_status=status, game_id="2024_01_AAA_BBB", nfl4th_recommended="go"),
    ])  # fmt: skip
    if not full:
        nulls = [c for c in bm.NFL4TH_COLUMNS if c not in bm.TEXT_COLUMNS
                 and c != "fg_make_prob_bundled"] + ["nfl4th_recommended"]  # fmt: skip
        out = out.with_columns(pl.lit(None).cast(out.schema[c]).alias(c) for c in nulls)
    d = tmp_path / status
    d.mkdir()
    bm.compare(bm.join(rows, out)).write_parquet(d / "joined.parquet")
    info = {"seasons": [2024, 2025], "graded_rows": 3, "benchmarked": 3,
            "left_out": {"overtime": 0, "last_15_seconds": 0}, "nfl4th_status": [status],
            "nfl4th_version": ["1.0.7"], "network_denied": True}  # fmt: skip
    (d / "info.json").write_text(json.dumps(info))
    return d


@pytest.mark.parametrize("status", ["full", "fg_only"])
def test_report_in_both_run_states(tmp_path, monkeypatch, status):
    monkeypatch.setattr(br, "report_paths", lambda: (tmp_path / "r.md", tmp_path / "r.csv"))
    md, csv = br.write_report(out_dir=_stored_run(tmp_path, status), progress=lambda _: None)
    text = md.read_text()
    assert "nfl4th 1.0.7" in text and "`home_opening_kickoff`" in text
    assert "| P(make) on field-goal attempts | 1 |" in text
    assert pl.read_csv(csv).height == 3
    if status == "full":
        assert "has NOT run" not in text
        assert "| all | all | 3 | 1 | 33.3% |" in text  # 1 of 3 agree
        assert "| clear | all | 2 | 1 | 50.0% |" in text
        assert "| toss-up | all | 1 | 0 | 0.0% |" in text
        assert "Biggest disagreements" in text and bm.CAUSES[1] in text
        assert "| opp 21-40 | 3 | 33.3% |" in text  # all 3 rows at the opp 40
    else:
        assert "has NOT run" in text and "Agreement on the recommended option" not in text


@pytest.mark.realdata
def test_real_quarter_clock_matches_the_warehouse():
    from twm.config import settings
    from twm.modules.decisions import grade as gr
    from twm.modules.decisions import grade_inputs as gi

    db = settings().path("warehouse")
    try:
        graded = gr.load_graded("fourth_downs", [2025])
    except gr.GradeError:
        pytest.skip("run `twm decisions grade --season 2025` first")
    if not db.exists():
        pytest.skip("run `uv run twm build` first")
    rows, _ = bm.select_rows(graded)
    s = bm.export_states(rows)
    w = gi._query(db, "SELECT game_id, play_id, quarter_seconds_remaining AS q FROM w.fact_play "
                      "WHERE season = 2025 AND down = 4")  # fmt: skip
    j = s.join(w, on=list(bm.KEYS), how="left")
    assert j.height == rows.height > 3000
    assert (j.get_column("quarter_seconds_remaining") == j.get_column("q")).all()
    games = bm.export_games(db, [2025])
    assert games.columns == list(bm.GAME_COLUMNS)
    assert (
        games.select(pl.col("spread_line", "total_line").null_count()).sum_horizontal().item() == 0
    )


def test_model_provenance_checks_size_and_sha256(tmp_path):
    import hashlib
    import json

    d = tmp_path / "R" / "nfl4th"
    d.mkdir(parents=True)
    assert bm.model_provenance(tmp_path) == []  # no model files: an fg_only run
    (d / "wp_model.rds").write_bytes(b"abc")
    sha = hashlib.sha256(b"abc").hexdigest()
    rec = {"wp_model": {"url": "u", "bytes": 3, "sha256": sha, "downloaded_utc": "t"}}
    (d / "models.json").write_text(json.dumps(rec))
    assert bm.model_provenance(tmp_path) == [
        {"file": "wp_model.rds", "bytes": 3, "sha256": sha, "url": "u", "downloaded_utc": "t"}
    ]
    (d / "wp_model.rds").write_bytes(b"abd")
    with pytest.raises(ValueError, match="does not match"):
        bm.model_provenance(tmp_path)
