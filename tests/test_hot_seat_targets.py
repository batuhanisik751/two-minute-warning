"""Hot-Seat H3b labels (targets.py) on synthetic rows: every label rule, offline."""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from twm.modules.hot_seat import targets as ht

D = dt.date
UTC = dt.UTC


def _features(rows: list[tuple]) -> pl.DataFrame:
    """(season, snapshot, week, team, coach_id, as_of UTC, is_last_reg_week, is_interim)."""
    cols = ["season", "snapshot", "week", "team", "coach_id", "as_of", "is_last_reg_week",
            "is_interim"]  # fmt: skip
    df = pl.DataFrame(rows, schema=cols, orient="row")
    return df.with_columns(
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        pl.col("as_of").dt.replace_time_zone("UTC"),
    )


def _season(coach: str = "a", *, team: str = "AAA", interim: bool = False) -> list[tuple]:
    """2024: weekly as-ofs weeks 2-4 (Tuesdays 14:00 UTC), week 5 = last REG week (its weekly
    row after Black Monday) and the end-of-season row (as-of 03:00 UTC Monday)."""
    out = []
    for w, day in ((2, D(2024, 9, 17)), (3, D(2024, 9, 24)), (4, D(2024, 10, 1))):
        when = dt.datetime.combine(day, dt.time(14))
        out.append((2024, "weekly", w, team, coach, when, False, interim))
    out.append((2024, "weekly", 5, team, coach, dt.datetime(2024, 10, 8, 14), True, interim))
    out.append((2024, "end_of_season", 5, team, coach, dt.datetime(2024, 10, 7, 3), False,
                interim))  # fmt: skip
    return out


# last REG game Sunday 2024-10-06; final game (playoffs) 2024-10-20
TEAM_DATES = pl.DataFrame(
    {"season": [2024], "team": ["AAA"], "last_reg_date": [D(2024, 10, 6)],
     "final_date": [D(2024, 10, 20)]},
    schema_overrides={"season": pl.Int32},
)  # fmt: skip


def _deps(*rows: tuple) -> pl.DataFrame:
    """(coach_id, departure_type, announced, verified[, date_imputed])."""
    recs = []
    for r in rows:
        coach, kind, day, verified = r[:4]
        recs.append(dict(season=2024, team="AAA", coach_id=coach, candidate_id=f"c_{coach}",
                         departure_type=kind, announced=day, date_imputed=len(r) > 4 and r[4],
                         verified=verified))  # fmt: skip
    schema = {"season": pl.Int32, "team": pl.String, "coach_id": pl.String,
              "candidate_id": pl.String, "departure_type": pl.String, "announced": pl.Date,
              "date_imputed": pl.Boolean, "verified": pl.Boolean}  # fmt: skip
    return pl.DataFrame(recs, schema=schema).select(ht.DEPARTURE_COLUMNS)


def _run(deps: pl.DataFrame, *, rows=None, mode="suggested", **kw):
    feats = _features(rows if rows is not None else _season())
    return ht.build_targets(feats, deps, TEAM_DATES, mode=mode, **kw)


def _by_week(df: pl.DataFrame, col: str) -> dict[tuple[str, int], int]:
    return {(r["snapshot"], r["week"]): r[col] for r in df.iter_rows(named=True)}


def test_no_departure_all_zero_and_last_week_dropped():
    df, n = _run(_deps())
    assert n["dropped_last_reg_week"] == 1
    assert _by_week(df, "y") == {("weekly", 2): 0, ("weekly", 3): 0, ("weekly", 4): 0,
                                 ("end_of_season", 5): 0}  # fmt: skip
    assert df["event"].sum() == 0 and not df["censored"].any()
    eos = df.filter(pl.col("snapshot") == "end_of_season")
    assert eos["day"].item() == D(2024, 10, 6)  # the last REG game day, not the as-of's date
    assert eos["window_end"].item() == D(2024, 11, 19)  # final game + 30 days


def test_announced_on_the_as_of_day_counts_the_day_before_drops():
    # fired Tuesday 2024-09-24 (week 3's as-of day): week 3 row positive, event there
    df, n = _run(_deps(("a", "fired_in_season", D(2024, 9, 24), False)))
    assert _by_week(df, "y") == {("weekly", 2): 1, ("weekly", 3): 1}
    assert _by_week(df, "event") == {("weekly", 2): 0, ("weekly", 3): 1}
    assert n["dropped_gone"] == 2  # week 4 and end of season: he is gone
    # fired Monday 2024-09-23: the week 3 row (Tuesday) is dropped, the event is week 2's
    df, _ = _run(_deps(("a", "fired_in_season", D(2024, 9, 23), False)))
    assert _by_week(df, "y") == {("weekly", 2): 1}
    assert _by_week(df, "event") == {("weekly", 2): 1}


def test_eastern_date_of_the_as_of():
    # an as-of at 02:00 UTC on Sept 25 is still Sept 24 in New York
    rows = _season()
    rows[1] = (2024, "weekly", 3, "AAA", "a", dt.datetime(2024, 9, 25, 2), False, False)
    df, _ = _run(_deps(("a", "fired_in_season", D(2024, 9, 24), False)), rows=rows)
    assert _by_week(df, "y")[("weekly", 3)] == 1


def test_end_of_season_label_compares_dates_finale_evening_firing():
    # announced the evening of the finale (Sunday 10-06), before the 03:00 UTC Monday as-of
    df, n = _run(_deps(("a", "fired_after_season", D(2024, 10, 6), False)))
    assert _by_week(df, "y") == {("weekly", 2): 1, ("weekly", 3): 1, ("weekly", 4): 1,
                                 ("end_of_season", 5): 1}  # fmt: skip
    assert _by_week(df, "event") == {("weekly", 2): 0, ("weekly", 3): 0, ("weekly", 4): 0,
                                     ("end_of_season", 5): 1}  # fmt: skip
    assert n["dropped_gone"] == 0
    # announced the day before the finale: the end-of-season row is dropped (he is gone)
    df, n = _run(_deps(("a", "fired_after_season", D(2024, 10, 5), False)))
    assert ("end_of_season", 5) not in _by_week(df, "y") and n["dropped_gone"] == 1
    assert _by_week(df, "event")[("weekly", 4)] == 1


def test_window_thirty_days_after_the_final_game():
    df, n = _run(_deps(("a", "fired_after_season", D(2024, 11, 19), False)))  # final + 30
    assert df["y"].to_list() == [1, 1, 1, 1] and n["positive_outside_window"] == 0
    df, n = _run(_deps(("a", "fired_after_season", D(2024, 11, 20), False)))  # final + 31
    assert df["y"].sum() == 0 and df["event"].sum() == 0 and not df["censored"].any()
    assert n["positive_outside_window"] == 1


def test_non_positive_departure_is_censored_and_resigned_under_pressure_sensitivity():
    deps = _deps(("a", "resigned_under_pressure", D(2024, 10, 7), False))
    df, n = _run(deps)
    assert df["y"].sum() == 0 and df["censored"].all() and n["censored_rows"] == 4
    df, _ = _run(deps, positive_types=ht.POSITIVE_SETS["rup_positive"])
    assert df["y"].to_list() == [1, 1, 1, 1] and not df["censored"].any()
    df, _ = _run(_deps(("a", "retired", D(2024, 10, 7), False)))
    assert df["censored"].all() and df["y"].sum() == 0


def test_interim_rows_are_kept_and_flagged():
    rows = _season("i", interim=True)
    df, n = _run(_deps(("i", "interim_not_retained", D(2024, 10, 7), False)), rows=rows)
    assert df["is_interim"].all() and n["interim_rows"] == 4
    assert df["y"].sum() == 0 and df["censored"].all()


def test_blank_departure_type_drops_the_coach_season():
    rows = _season("a") + _season("b", team="AAA")[:1]
    df, n = _run(_deps(("a", "", D(2024, 10, 7), False)), rows=rows)
    assert n["departures_blank_type"] == 1 and n["dropped_blank_type"] == 4
    assert df["coach_id"].to_list() == ["b"]


def test_verified_mode_refuses_unverified_departures():
    deps = _deps(("a", "fired_after_season", D(2024, 10, 7), False))
    with pytest.raises(ht.UnverifiedLabelsError, match="1 of the 1 departures"):
        _run(deps, mode="verified")
    ok = _deps(("a", "fired_after_season", D(2024, 10, 7), True))
    df, n = _run(ok, mode="verified")
    assert n["departures_unverified"] == 0 and df["y"].sum() == 4
    with pytest.raises(ValueError, match="mode must be"):
        _run(ok, mode="maybe")


def test_hazard_event_once_per_positive_coach_season_and_last_row_runs_to_window_end():
    # a replaced coach: rows weeks 2-3 only (someone else coached later), fired a week later
    rows = _season("a")[:2] + _season("b")[2:]
    df, _ = _run(_deps(("a", "fired_in_season", D(2024, 10, 2), False)), rows=rows)
    a = df.filter(pl.col("coach_id") == "a")
    assert a["y"].to_list() == [1, 1] and a["event"].to_list() == [0, 1]
    assert a["next_day"].to_list() == [D(2024, 9, 24), None]
    assert df.filter(pl.col("coach_id") == "b")["y"].sum() == 0


def test_resolve_departures_ids_names_and_imputed_dates():
    labels = pl.DataFrame(
        {"candidate_id": ["2024_AAA_w05_a", ""], "origin": ["schedule", "source_only"],
         "team": ["AAA", "BBB"], "last_season": ["2024", "2024"], "coach_name": ["A", "Bee"],
         "last_game_date": ["2024-10-06", "2024-09-29"], "departure_type": ["fired_after_season",
         "fired_in_season"], "announced_date": ["", "2024-09-30"], "verified_by_owner": ["", "y"]}
    )  # fmt: skip
    cands = pl.DataFrame({"candidate_id": ["2024_AAA_w05_a"], "coach_id": ["a"]})
    names = pl.DataFrame({"coach_id": ["bee"], "coach_name": ["Bee"]})
    dep, unresolved = ht.resolve_departures(labels, cands, names)
    assert unresolved.height == 0
    got = {r["coach_id"]: r for r in dep.iter_rows(named=True)}
    assert got["a"]["announced"] == D(2024, 10, 6) and got["a"]["date_imputed"]
    assert got["bee"]["announced"] == D(2024, 9, 30) and not got["bee"]["date_imputed"]
    assert got["bee"]["verified"] and not got["a"]["verified"]
    _, unresolved = ht.resolve_departures(labels, cands, None)
    assert unresolved["coach_name"].to_list() == ["Bee"]


def test_blank_date_uses_last_game_date_in_verified_mode_too():
    # H3b-2: the owner may verify a row and leave the date blank when no source gives the day
    labels = pl.DataFrame(
        {"candidate_id": ["2024_AAA_w05_a"], "origin": ["schedule"], "team": ["AAA"],
         "last_season": ["2024"], "coach_name": ["A"], "last_game_date": ["2024-10-06"],
         "departure_type": ["fired_after_season"], "announced_date": [""],
         "verified_by_owner": ["y"]}
    )  # fmt: skip
    cands = pl.DataFrame({"candidate_id": ["2024_AAA_w05_a"], "coach_id": ["a"]})
    dep, _ = ht.resolve_departures(labels, cands, None)
    row = dep.row(0, named=True)
    assert row["announced"] == D(2024, 10, 6) and row["date_imputed"] and row["verified"]
    for mode in ht.LABEL_MODES:
        df, n = _run(dep, mode=mode)  # verified mode does not refuse it
        assert n["departures_date_imputed"] == 1 and n["departures_unverified"] == 0
        # announced on his last game day: every remaining row (weeks 2-4 + season end) is y = 1
        assert df["y"].sum() == 4
