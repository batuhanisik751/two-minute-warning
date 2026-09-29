"""The K and D/ST streamer's label y_start (S1b) on the synthetic world of tests/streamer_world.py
(start threshold 2): byes, ties at the cutoff, split weeks, season end, pending weeks, misses."""

from __future__ import annotations

import polars as pl
import pytest
from typer.testing import CliRunner

from tests.streamer_world import NO_ROSTER_KICKER, PS_KICKER, RULES, build_world, gid
from twm.asof import AsOfView, weekly_as_of
from twm.cli import app
from twm.modules.streamer import labels as sl
from twm.modules.streamer.pool import candidate_pool


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory):
    return build_world(tmp_path_factory.mktemp("streamer_labels"))


def _labels(db, week: int) -> pl.DataFrame:
    with AsOfView(db, weekly_as_of(db, 2025, week)) as v:
        pool = candidate_pool(v, 2025, week, rules=RULES)
    return sl.label_rows(db, pool, RULES)


def _rows(df: pl.DataFrame) -> dict[str, dict]:
    return {r["entity_id"]: r for r in df.iter_rows(named=True)}


def test_week_1_byes_ties_at_the_cutoff_and_misses(world):
    df = _labels(world, 1)
    assert tuple(df.columns[-len(sl.LABEL_COLUMNS) :]) == sl.LABEL_COLUMNS
    r = _rows(df)
    assert set(df.get_column("label_week")) == {2}
    # 304 (in the pool) is week 2's best kicker; the punter's 2 PATs are not ranked among Ks
    k304 = r[gid(304)]
    assert (k304["in_pool"], k304["label_status"], k304["next_points"]) == (True, "final", 7.0)
    assert (k304["next_pos_rank"], k304["n_ranked"], k304["y_start"]) == (1, 6, True)
    # 301 and 303 tie for 2nd at 5 points: both are starts (ties at the cutoff count)
    assert [r[gid(n)]["next_pos_rank"] for n in (301, 303)] == [2, 2]
    assert r[gid(301)]["y_start"] and r[gid(303)]["y_start"]
    assert r[gid(307)]["next_pos_rank"] == 4 and r[gid(307)]["y_start"] is False
    # SF and CHI are on their bye in week 2: no label
    for e in (gid(305), gid(308), "DST-SF", "DST-CHI"):
        assert (r[e]["label_status"], r[e]["y_start"], r[e]["played_next"]) == ("bye", None, None)
    # the practice-squad kicker's team plays but he does not kick: a miss, not a NULL
    ps = r[gid(PS_KICKER)]
    assert (ps["label_status"], ps["played_next"], ps["next_points"], ps["y_start"]) == (
        "final", False, None, False
    )  # fmt: skip
    # D/ST: LAC 7 sacks (1st), PHI and KC tie for 2nd; DAL 4th
    assert (r["DST-LAC"]["next_pos_rank"], r["DST-LAC"]["y_start"]) == (1, True)
    assert r["DST-PHI"]["y_start"] and r["DST-KC"]["y_start"]
    assert (r["DST-DAL"]["next_pos_rank"], r["DST-DAL"]["y_start"]) == (4, False)
    assert r["DST-LAC"]["n_ranked"] == 6


def test_split_week_belongs_to_its_own_week(world):
    # week 2's label week 3 includes the Tuesday game MIN at LAC (after week 3's as-of)
    r = _rows(_labels(world, 2))
    assert (r[gid(304)]["next_points"], r[gid(304)]["y_start"]) == (10.0, True)
    # 311 has no roster row but kicked for CHI: ranked (2nd), so 305 is 3rd
    assert (r[gid(305)]["next_pos_rank"], r[gid(305)]["y_start"]) == (3, False)
    assert r[gid(304)]["n_ranked"] == 8
    # 308 (CHI) is on the pool at week 2's as-of but never kicked in week 3: a miss
    assert (r[gid(308)]["played_next"], r[gid(308)]["y_start"]) == (False, False)
    # week 3's pool is labelled with week 4, never with the Tuesday game of week 3
    r3 = _rows(_labels(world, 3))
    assert (r3[gid(304)]["label_week"], r3[gid(304)]["next_points"]) == (4, 5.0)
    assert (r3[gid(304)]["next_pos_rank"], r3[gid(304)]["y_start"]) == (4, False)
    assert gid(NO_ROSTER_KICKER) not in r3  # not on a roster: never in the pool


def test_season_end_has_no_label(world):
    df = _labels(world, 4)
    assert set(df.get_column("label_status")) == {"season_end"}
    for c in sl.LABEL_COLUMNS:
        if c != "label_status":
            assert df.get_column(c).null_count() == df.height, c


def test_pending_until_the_label_week_is_final(tmp_path):
    db = build_world(tmp_path, week4_final=False)
    df = _labels(db, 3)
    playing = df.filter(pl.col("label_status") != "bye")
    assert set(playing.get_column("label_status")) == {"pending"}
    for c in ("played_next", "next_points", "next_pos_rank", "n_ranked", "y_start"):
        assert playing.get_column(c).null_count() == playing.height, c
    assert set(playing.get_column("label_week")) == {4}


def test_only_rows_public_after_the_as_of_count(world):
    """A row public at or before the as-of is a feature, never a label: pretend week 1's pool
    was taken at week 2's as-of, and week 2's lines no longer count (nothing is ranked)."""
    with AsOfView(world, weekly_as_of(world, 2025, 1)) as v:
        pool = candidate_pool(v, 2025, 1, rules=RULES)
    late = pool.with_columns(pl.lit(weekly_as_of(world, 2025, 2)).alias("as_of"))
    df = sl.label_rows(world, late, RULES).filter(pl.col("label_status") == "final")
    assert df.height > 0
    assert set(df.get_column("n_ranked")) == {0} and not df.get_column("y_start").any()


def test_label_rows_checks_its_input(world):
    with pytest.raises(ValueError, match="pool lacks columns"):
        sl.label_rows(world, pl.DataFrame({"season": [2025]}), RULES)
    empty = _labels(world, 1).head(0).select(pl.exclude(sl.LABEL_COLUMNS))
    assert sl.label_rows(world, empty, RULES).columns[-len(sl.LABEL_COLUMNS) :] == list(
        sl.LABEL_COLUMNS
    )


def test_cli_labels(world):
    res = CliRunner().invoke(app, ["streamer", "labels", "2025", "1", "--db", str(world), "--all"])
    assert res.exit_code == 0, res.output
    assert "2025 week 1 -> week 2, as-of 2025-09-09 14:00 UTC, K: 9 entities" in res.output
    assert "bye" in res.output and "DST-LAC" in res.output


def test_pool_labels_report(world, tmp_path):
    from twm.modules.streamer.report import CSV_COLUMNS, write_report

    md, csv, summary = write_report(world, tmp_path, [2025], RULES)
    assert tuple(summary.columns) == CSV_COLUMNS
    k = summary.filter(pl.col("position") == "K").row(0, named=True)
    # as-ofs 1-4; week-1 pool {304, 305, 308, 309}: 304 starts, 309 misses, 305/308 on bye
    assert (k["n_asofs"], k["method"]) == (4, "ecr") and k["n_season_end"] > 0
    week1 = _labels(world, 1).filter(pl.col("in_pool"), pl.col("position") == "K")
    assert week1.get_column("y_start").to_list().count(True) == 1
    assert k["n_bye"] >= 2 and 0 < k["start_rate"] < 1
    text = md.read_text()
    assert "## K by season" in text and "## DST by season" in text and "| 2025 | ecr |" in text
    assert csv.read_text().splitlines()[0] == ",".join(CSV_COLUMNS)
    # deterministic: the same warehouse gives the same files
    md2, _, _ = write_report(world, tmp_path / "again", [2025], RULES)
    assert md2.read_text() == text
