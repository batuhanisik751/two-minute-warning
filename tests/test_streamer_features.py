"""The K and D/ST streamer's features and dataset (S1c), on the synthetic world of
tests/streamer_world.py plus venues and red-zone drives, and on the real warehouse (realdata).

The world (2 teams x 1 slot): every game ends 22-20 for the home team; kicker points are PATs +
3 x field goals from 30-39 yards; D/ST points are sacks. Added here: stadiums with a roof
history (SEA00 retractable: roof 'open' / 'closed'; LAX01 and MIN01 domes; the rest outdoors),
2025 week 2 SEA-MIN at a neutral site, and 2025 week 1 red-zone drives in DAL at PHI: PHI 2
trips (a touchdown, a field goal), DAL none (a 25-yard TD whose two-point try at the 2 and a
kickoff at the 15 do not count).
"""

from __future__ import annotations

from dataclasses import replace

import polars as pl
import pytest
from polars.testing import assert_frame_equal
from typer.testing import CliRunner

from tests.conftest import PBP_DTYPES, SCHEDULE_DTYPES, RawCache, frame, plays_for
from tests.streamer_world import RULES, build_world, games_2024, games_2025, gid
from twm.asof import AsOfView, weekly_as_of
from twm.backtest.leakage import assert_future_invariant
from twm.cli import app
from twm.modules.streamer import dataset as sd
from twm.modules.streamer import features as sf
from twm.modules.streamer.pool import candidate_pool, pool_history

STADIUMS = {"PHI": "PHI00", "DAL": "DAL00", "KC": "KAN00", "LAC": "LAX01", "SF": "SFO01",
            "SEA": "SEA00", "MIN": "MIN01", "CHI": "CHI98"}  # fmt: skip
ROOFS = {"LAX01": "dome", "MIN01": "dome"}  # SEA00: open / closed below; else outdoors
VENUE_DTYPES = {**SCHEDULE_DTYPES, "stadium_id": pl.String(), "stadium": pl.String(),
                "roof": pl.String(), "location": pl.String()}  # fmt: skip
PLAY_DTYPES = {**PBP_DTYPES, "play_type": pl.String(), "yardline_100": pl.Float64(),
               "fixed_drive": pl.Float64(), "fixed_drive_result": pl.String(),
               "two_point_attempt": pl.Float64()}  # fmt: skip


def _venues(games: list[dict]) -> list[dict]:
    out = []
    for i, g in enumerate(games):
        sid = STADIUMS[g["home_team"]]
        roof = ROOFS.get(sid, "outdoors")
        if sid == "SEA00":
            roof = ("open", "closed")[i % 2]  # a game-day state: only "retractable" is used
        neutral = (g["season"], g["week"], g["home_team"]) == (2025, 2, "MIN")
        out.append({**g, "stadium_id": sid, "stadium": f"{sid} Field", "roof": roof,
                    "location": "Neutral" if neutral else "Home"})  # fmt: skip
    return out


def _drives(games: list[dict]) -> list[dict]:
    """plays_for's filler plays (no yardline) + DAL at PHI's drives (module docstring)."""
    base = [{**p, "fixed_drive": 99.0} for p in plays_for(games)]
    gid1 = "2025_01_DAL_PHI"
    spec = [  # posteam, drive, play_type, yardline, result, two-point
        ("PHI", 1, "pass", 30, "Touchdown", 0), ("PHI", 1, "run", 15, "Touchdown", 0),
        ("DAL", 2, "kickoff", 15, "Touchdown", 0), ("DAL", 2, "pass", 25, "Touchdown", 0),
        ("DAL", 2, "pass", 2, "Touchdown", 1), ("PHI", 3, "pass", 18, "Field goal", 0),
        ("PHI", 3, "field_goal", 12, "Field goal", 0), ("DAL", 4, "run", 50, "Punt", 0),
    ]  # fmt: skip
    extra = [
        {"game_id": gid1, "play_id": float(100 + i), "season": 2025, "week": 1,
         "season_type": "REG", "game_date": "2025-09-04", "posteam": pos,
         "defteam": "DAL" if pos == "PHI" else "PHI", "home_team": "PHI", "away_team": "DAL",
         "qtr": 1.0, "down": 1.0, "desc": f"drive {d}", "play_type": pt,
         "yardline_100": float(yl), "fixed_drive": float(d), "fixed_drive_result": res,
         "two_point_attempt": float(two)}
        for i, (pos, d, pt, yl, res, two) in enumerate(spec)
    ]  # fmt: skip
    return base + extra


def _extras(cache: RawCache) -> None:
    g24, g25 = games_2024(), games_2025()
    cache.write("schedules", 2024, frame(_venues(g24), VENUE_DTYPES))
    cache.write("schedules", 2025, frame(_venues(g25), VENUE_DTYPES))
    cache.write("pbp", 2025, frame(_drives(g25), PLAY_DTYPES))


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory):
    return build_world(tmp_path_factory.mktemp("streamer_features"), extras=_extras)


def _feats(db, week: int, season: int = 2025) -> tuple[pl.DataFrame, pl.DataFrame]:
    with AsOfView(db, weekly_as_of(db, season, week)) as v:
        pool = candidate_pool(v, season, week, rules=RULES)
        return pool, sf.features_for(v, season, week, pool, rules=RULES)


def _row(df: pl.DataFrame, entity: str) -> dict:
    (r,) = df.filter(pl.col("entity_id") == entity).iter_rows(named=True)
    return r


def test_values_after_week_1(world):
    pool, f = _feats(world, 1)
    assert f.columns == [*sf.KEY_COLUMNS, *sf.FEATURE_COLUMNS]
    assert f.select(sf.KEY_COLUMNS).rows() == pool.select(sf.KEY_COLUMNS).rows()  # pool order
    phi_k, dal_k = _row(f, gid(301)), _row(f, gid(302))
    # PHI's kicker: 10 points = 3 field goals (30-39 yards) + 1 extra point
    assert (phi_k["k_fg_att_per_game"], phi_k["k_pat_att_per_game"]) == (3.0, 1.0)
    assert (phi_k["k_fg_pct_0_39"], phi_k["k_fg_pct_40_49"]) == (1.0, None)
    assert phi_k["k_fg_att_40_plus_per_game"] == 0.0
    assert (phi_k["kdst_points_per_game"], phi_k["kdst_points_last"]) == (10.0, 10.0)
    assert phi_k["is_team_kicker"] is True and phi_k["dst_sacks_per_game"] is None
    # red zone: PHI 2 trips, 1 stall; DAL none (a kickoff and a two-point try do not count)
    assert (phi_k["team_rz_trips_per_game"], phi_k["team_rz_stalls_per_game"]) == (2.0, 1.0)
    assert phi_k["team_rz_stall_rate"] == 0.5
    assert (dal_k["team_rz_trips_per_game"], dal_k["team_rz_stall_rate"]) == (0.0, None)
    # week 2: LAC at DAL (DAL's defense allowed PHI's 2 trips), PHI at KC; home 22-20 always
    lac = _row(f, "DST-LAC")
    assert lac["next_opp_rz_trips_allowed_per_game"] == 2.0
    assert lac["next_opp_rz_stall_rate_forced"] == 0.5
    assert (lac["next_opp_points_per_game"], lac["next_opp_points_allowed_per_game"]) == (20, 22)
    assert lac["next_is_home"] is False and lac["k_fg_att_per_game"] is None
    assert (lac["dst_sacks_per_game"], lac["dst_points_allowed_per_game"]) == (4.0, 20.0)
    assert (lac["dst_takeaways_per_game"], lac["dst_tds_per_game"]) == (0.0, 0.0)
    kc = _row(f, "DST-KC")  # PHI's offense was sacked twice by DAL in week 1
    assert (kc["next_opp_sacks_allowed_per_game"], kc["next_opp_giveaways_per_game"]) == (2, 0)
    assert kc["next_is_home"] is True and kc["team_games_to_date"] == 1
    # SEA vs MIN at a neutral site: nobody is home; SF has a bye in week 2
    assert _row(f, "DST-SEA")["next_is_home"] is False
    assert _row(f, "DST-MIN")["next_is_home"] is False
    sf_row = _row(f, "DST-SF")
    assert all(sf_row[c] is None for c in (*sf.OPPONENT_FEATURES, *sf.GAME_FEATURES))
    # no stadium of week 2 has hosted a visible game yet; no weekly page is public yet
    assert f.get_column("next_venue_dome").null_count() == f.height
    assert f.get_column("weekly_ecr_listed").null_count() == f.height


def test_venues_and_weekly_ranks_after_week_2(world):
    _, f = _feats(world, 2)
    venue = {e: (_row(f, e)["next_venue_dome"], _row(f, e)["next_venue_retractable"])
             for e in ("DST-MIN", "DST-DAL", "DST-SEA", "DST-PHI", "DST-KC")}  # fmt: skip
    assert venue == {
        "DST-MIN": (True, False),  # at LAC: LAX01 hosted domed games
        "DST-DAL": (False, True),  # at SEA: SEA00's roof was 'open' (a game-day state)
        "DST-SEA": (False, True),
        "DST-PHI": (False, False),  # CHI at PHI: outdoors
        "DST-KC": (None, None),  # at SF: SFO01 has no earlier game
    }
    # the weekly pages (scraped 2025-09-12) are public now: LAC's kicker and D/ST are listed
    ranks = {e: (_row(f, e)["weekly_ecr_rank"], _row(f, e)["weekly_ecr_listed"])
             for e in (gid(304), "DST-LAC", gid(301))}  # fmt: skip
    assert ranks == {gid(304): (1, True), "DST-LAC": (1, True), gid(301): (None, False)}
    phi_k = _row(f, gid(301))  # 10 then 5 points: 3 + 1 field goals, 1 + 2 extra points
    assert (phi_k["k_fg_att_per_game"], phi_k["k_pat_att_per_game"]) == (2.0, 1.5)
    assert phi_k["kdst_points_last"] == 5.0


def test_accuracy_by_distance_leaves_blocks_out():
    """Buckets: made / (made + missed); blocked kicks have no distance bucket."""
    zero = {f"{k}_{b}": 0 for b in sf.FG_BUCKETS for k in ("fg_made", "fg_missed")}
    kicks = pl.DataFrame([
        {**zero, "gsis_id": "k1", "week": 1, "game_id": "g1", "fg_att": 5, "pat_att": 2,
         "fg_made_0_39": 2, "fg_missed_0_39": 1, "fg_made_50_plus": 1, "points": 9.0},
        {**zero, "gsis_id": "k1", "week": 2, "game_id": "g2", "fg_att": 2, "pat_att": 0,
         "fg_made_40_49": 1, "points": 4.0},  # the other attempt was blocked
    ])  # fmt: skip
    (r,) = sf._kicker_stats(kicks).iter_rows(named=True)
    assert (r["k_fg_att_per_game"], r["k_pat_att_per_game"]) == (3.5, 1.0)
    assert r["k_fg_pct_0_39"] == pytest.approx(2 / 3)
    assert (r["k_fg_pct_40_49"], r["k_fg_pct_50_plus"]) == (1.0, 1.0)
    assert r["k_fg_att_40_plus_per_game"] == 1.0 and r["_k_last"] == 4.0


def test_masked_venue_hides_home_and_roof(world):
    """Week 3 is the second-to-last week: its venues are public only from week 2's as-of. Week
    1's pool asked about week 3 at week 1's as-of: opponents known, venue NULL, both paths."""
    as_of = weekly_as_of(world, 2025, 1)
    with AsOfView(world, as_of) as v:
        rows = candidate_pool(v, 2025, 1, rules=RULES).with_columns(pl.lit(2).alias("week"))
        ref = sf.features_for(v, 2025, 2, rows, rules=RULES)
    batch = sf.features_history(world, rows, rules=RULES)
    assert_frame_equal(ref, batch)
    phi = _row(ref, "DST-PHI")  # CHI at PHI in week 3
    assert phi["next_opp_games_to_date"] == 1
    assert (phi["next_is_home"], phi["next_venue_dome"]) == (None, None)
    assert ref.get_column("next_is_home").null_count() == ref.height


def test_reference_and_batch_paths_agree(world):
    hist = pool_history(world, [2025], rules=RULES)
    batch = sf.features_history(world, hist, rules=RULES)
    ref = []
    for w in sorted(hist.get_column("week").unique().to_list()):
        with AsOfView(world, weekly_as_of(world, 2025, w)) as v:
            rows = hist.filter(pl.col("week") == w)
            ref.append(sf.features_for(v, 2025, w, rows, rules=RULES))
    assert_frame_equal(pl.concat(ref), batch)
    assert batch.height == hist.height and set(batch.get_column("week")) == {1, 2, 3, 4}


@pytest.mark.parametrize("week", [1, 2, 3])
def test_leakage_harness(world, week):
    """Deleting or perturbing every row public after the as-of changes no feature."""

    def builder(view: AsOfView) -> pl.DataFrame:
        pool = candidate_pool(view, 2025, week, rules=RULES)
        return sf.features_for(view, 2025, week, pool, rules=RULES)

    out = assert_future_invariant(
        builder, world, weekly_as_of(world, 2025, week), key=list(sf.KEY_COLUMNS)
    )
    assert out.height > 0
    assert not sf.uses_bypass(sf.input_sql(2025, RULES))


LEAKS = {  # input -> (its table, as the query names it); read raw and never filtered
    "kicks": "fact_kicker_week",
    "defense": "fact_defense_week",
    "team_games": "fact_game",
    "red_zone": "fact_play",
    "venues": "fact_game",
}


def _leaky(monkeypatch, name: str, *, skip_filter: bool) -> dict[str, str]:
    """Rewrite input ``name`` to read ``wh.<table>`` (every row, the future too); with
    ``skip_filter`` its frame also skips the as-of filter of SeasonInputs.visible."""
    table = LEAKS[name]
    queries = dict(sf.input_sql(2025, RULES))
    assert f"FROM {table} " in queries[name] or f"FROM {table}\n" in queries[name]
    queries[name] = queries[name].replace(f"FROM {table}", f"FROM wh.{table}")
    monkeypatch.setattr(sf, "input_sql", lambda season, rules: queries)
    if skip_filter:
        original = sf.SeasonInputs.visible

        def visible(self, as_of):
            out = original(self, as_of)
            return replace(out, frames={**out.frames, name: self.frames[name]})

        monkeypatch.setattr(sf.SeasonInputs, "visible", visible)
    return queries


def _builder_week2(view: AsOfView) -> pl.DataFrame:
    return sf.features_for(view, 2025, 2, candidate_pool(view, 2025, 2, rules=RULES), rules=RULES)


@pytest.mark.parametrize("name", list(LEAKS))
def test_leaky_variants_fail_the_harness(world, monkeypatch, name):
    from twm.backtest.leakage import LeakageError

    assert sf.uses_bypass(_leaky(monkeypatch, name, skip_filter=True))
    with pytest.raises(LeakageError):
        assert_future_invariant(
            _builder_week2, world, weekly_as_of(world, 2025, 2), key=list(sf.KEY_COLUMNS)
        )


@pytest.mark.parametrize("name", list(LEAKS))
def test_sql_bypass_alone_is_filtered_again(world, monkeypatch, name):
    """Defense in depth: a ``wh.`` read is cut back to the public rows by the as-of filter."""
    with AsOfView(world, weekly_as_of(world, 2025, 2)) as v:
        clean = _builder_week2(v)
    _leaky(monkeypatch, name, skip_filter=False)
    with AsOfView(world, weekly_as_of(world, 2025, 2)) as v:
        assert_frame_equal(_builder_week2(v), clean)


def test_dataset_rows_labels_and_available_at(world, tmp_path):
    df = sd.build_dataset(world, [2025], rules=RULES)
    assert df.columns == sd.dataset_columns()
    assert "is_team_kicker" in df.columns and df.columns.count("is_team_kicker") == 1
    assert df.select(sf.KEY_COLUMNS).is_duplicated().sum() == 0
    assert df.select(sf.KEY_COLUMNS).rows() == sorted(df.select(sf.KEY_COLUMNS).rows())
    hist = pool_history(world, [2025], rules=RULES)
    assert df.height == hist.height  # every pool row, in the pool or not, every status
    final = df.filter(pl.col("label_status") == "final")
    other = df.filter(pl.col("label_status") != "final")
    assert final.height and other.height
    assert final.get_column("available_at").null_count() == 0
    assert final.filter(pl.col("available_at") <= pl.col("as_of")).height == 0
    assert other.get_column("available_at").null_count() == other.height
    # the label's public time: the latest week N+1 line at the position (all share a lag here)
    wk2 = final.filter(pl.col("week") == 1, pl.col("position") == "DST")
    assert wk2.get_column("available_at").n_unique() == 1
    # the features are the batch path's, joined on the key
    feats = sf.features_history(world, hist, rules=RULES)
    joined = df.select(*sf.KEY_COLUMNS, "k_fg_pct_0_39", "next_venue_dome")
    assert_frame_equal(joined, feats.select(joined.columns).sort(list(sf.KEY_COLUMNS)))
    # deterministic: two builds write byte-identical files
    a = sd.write_dataset(df, tmp_path / "a.parquet")
    b = sd.write_dataset(sd.build_dataset(world, [2025], rules=RULES), tmp_path / "b.parquet")
    assert a.read_bytes() == b.read_bytes()


def test_cli_features_and_dataset(world, tmp_path, monkeypatch):
    from twm.modules.streamer import pool as sp

    monkeypatch.setattr(sp.StreamerRules, "from_config", classmethod(lambda cls, lg=None: RULES))
    runner = CliRunner()
    res = runner.invoke(app, ["streamer", "features", "2025", "2", "--db", str(world), "--all"])
    assert res.exit_code == 0, res.output
    assert "2025 week 2 (for week 3), as-of 2025-09-16 14:00 UTC, K: 9 entities" in res.output
    assert "DST: 8 entities" in res.output
    only_k = runner.invoke(app, ["streamer", "features", "2025", "2", "--pos", "K",
                                 "--db", str(world)])  # fmt: skip
    assert only_k.exit_code == 0 and "K: " in only_k.output and "DST:" not in only_k.output
    out = tmp_path / "ds.parquet"
    res = runner.invoke(app, ["streamer", "dataset", "--db", str(world), "--start", "2025",
                              "--end", "2025", "--out", str(out)])  # fmt: skip
    assert res.exit_code == 0, res.output
    assert f"wrote {out}" in res.output and "with a final label" in res.output
    assert pl.read_parquet(out).height == sd.build_dataset(world, [2025], rules=RULES).height
    bad = runner.invoke(app, ["streamer", "dataset", "--db", str(world), "--start", "2026",
                              "--end", "2025"])  # fmt: skip
    assert bad.exit_code != 0


# ---- real data (opt-in: uv run pytest -m realdata) ------------------------------------------

# 2023_01_DAL_NYG (DAL won 40-0), red-zone drives read from fact_play."desc": DAL reached the
# NYG 20 or closer on drives 3 (FG), 6 (FG from the 20), 8, 12 and 16 (TDs); NYG on drive 1
# (blocked FG returned for a DAL TD) and 9 (missed FG). DAL hosts NYJ in week 2.
HAND_RED_ZONE = {"DST-DAL": (5.0, 2.0, 0.4), "DST-NYG": (2.0, 2.0, 1.0)}


@pytest.mark.realdata
def test_real_paths_agree_and_red_zone_by_hand(real_full_db):
    path, _ = real_full_db
    for season, week in ((2023, 1), (2019, 9), (2024, 14)):
        with AsOfView(path, weekly_as_of(path, season, week)) as v:
            pool = candidate_pool(v, season, week)
            ref = sf.features_for(v, season, week, pool)
        assert_frame_equal(ref, sf.features_history(path, pool))
        if (season, week) != (2023, 1):
            continue
        for entity, want in HAND_RED_ZONE.items():
            r = _row(ref, entity)
            got = (r["team_rz_trips_per_game"], r["team_rz_stalls_per_game"],
                   r["team_rz_stall_rate"])  # fmt: skip
            assert got == want, entity
        nyj = _row(ref, "DST-NYJ")  # next opponent DAL: its defense allowed NYG's 2 trips
        assert (nyj["next_opp_rz_trips_allowed_per_game"], nyj["next_opp_rz_stall_rate_forced"],
                nyj["next_opp_points_allowed_per_game"]) == (2.0, 1.0, 0.0)  # fmt: skip
        assert nyj["next_is_home"] is False


@pytest.mark.realdata
def test_real_dataset_one_season(real_full_db):
    path, _ = real_full_db
    df = sd.build_dataset(path, [2023])
    final = df.filter(pl.col("label_status") == "final")
    assert (
        final.height > 1000 and final.filter(pl.col("available_at") <= pl.col("as_of")).is_empty()
    )
    # K features only on K rows, D/ST features only on DST rows
    k, dst = df.filter(pl.col("position") == "K"), df.filter(pl.col("position") == "DST")
    assert k.get_column("dst_sacks_per_game").null_count() == k.height
    assert dst.get_column("k_fg_att_per_game").null_count() == dst.height
    assert dst.get_column("team_rz_trips_per_game").null_count() == 0
