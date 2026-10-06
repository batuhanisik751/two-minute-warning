"""Feature #8, lead time vs the crowd (twm.modules.lead_time), on synthetic data: the week
mapping, the threshold crossing, the lead's sign, never-flagged / never-added players, the
momentum baseline, the aggregate-only frames and the live tracker."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import polars as pl
import pytest

from twm.modules.lead_time import crowd as cr
from twm.modules.lead_time import flags as fl
from twm.modules.lead_time import live as lv
from twm.modules.lead_time import study as st

S = 2021
ASOF1 = datetime(2021, 9, 14, 14, 0)  # week 1's official as-of (Tuesday 14:00 UTC)


def windows(season: int = S, weeks: int = 18) -> pl.DataFrame:
    return pl.DataFrame(
        {"season": [season] * weeks, "week": list(range(1, weeks + 1)),
         "window_end_utc": [ASOF1 + timedelta(days=7 * (w - 1)) for w in range(1, weeks + 1)]},
        schema=cr.WINDOW_SCHEMA,
    )  # fmt: skip


def friday(period: int) -> date:
    """The Friday scrape of waiver period ``period`` (0 = the Friday before week 1's as-of)."""
    return (ASOF1 + timedelta(days=7 * period - 4)).date()


def owned(rows: list[tuple[str, int, float]], pos: str = "WR") -> pl.DataFrame:
    """(gsis_id, period, pct) -> owned rows with their Friday scrape date and period."""
    df = pl.DataFrame(
        {"season": [S] * len(rows), "gsis_id": [r[0] for r in rows], "pos": [pos] * len(rows),
         "scrape_date": [friday(r[1]) for r in rows], "pct": [float(r[2]) for r in rows]},
        schema=cr.OWNED_SCHEMA,
    )  # fmt: skip
    return cr.periods_at(df, windows(), "scrape_date")


def lw(last: int = 16) -> pl.DataFrame:
    return pl.DataFrame(
        {"season": [S], "last_list_week": [last]},
        schema={"season": pl.Int32, "last_list_week": pl.Int32},
    )


# --------------------------------------------------------------------------------------
# Week mapping and baselines
# --------------------------------------------------------------------------------------


def test_periods_map_scrapes_to_waiver_periods() -> None:
    days = [date(2021, 9, 10), date(2021, 9, 14), date(2021, 9, 15), date(2021, 9, 17),
            date(2021, 9, 21), date(2022, 1, 7), date(2022, 1, 12)]  # fmt: skip
    df = pl.DataFrame({"season": [S] * len(days), "scrape_date": days})
    got = cr.periods_at(df, windows(), "scrape_date")
    # before week 1's as-of: 0; Tuesday 00:00 of an as-of day still counts in the period before
    # it (waivers clear Wednesday); Wed/Fri after week 1's as-of: 1; the postseason is dropped
    assert got.get_column("period").to_list() == [0, 0, 1, 1, 1, 17]
    assert friday(1) == date(2021, 9, 17)


def test_periods_drop_seasons_without_windows() -> None:
    df = pl.DataFrame({"season": [1999], "scrape_date": [date(1999, 10, 1)]})
    assert cr.periods_at(df, windows(), "scrape_date").height == 0


def test_baseline_is_last_period0_scrape_else_first() -> None:
    o = owned([("a", 0, 1.0), ("a", 1, 2.0)])
    o = pl.concat([o, o.with_columns(scrape_date=pl.lit(date(2021, 9, 3)))])  # an earlier Friday
    b = cr.baselines(o)
    assert b.row(0, named=True)["baseline_date"] == friday(0)
    assert b.row(0, named=True)["complete"] is True
    late = cr.baselines(owned([("a", 5, 1.0), ("a", 7, 2.0)]))
    row = late.row(0, named=True)
    assert (row["baseline_date"], row["baseline_period"], row["complete"]) == (friday(5), 5, False)
    assert row["last_period"] == 7


# --------------------------------------------------------------------------------------
# Crowd adds (threshold crossing) and the momentum baseline
# --------------------------------------------------------------------------------------

PATHS = [
    ("up", 0, 5.0), ("up", 1, 20.0), ("up", 2, 49.9), ("up", 3, 50.0), ("up", 4, 70.0),
    ("star", 0, 99.0), ("star", 3, 40.0), ("star", 5, 90.0),  # started above: never an add
    ("new", 2, 30.0), ("new", 4, 60.0),  # absent from the baseline scrape: counts as below
    ("late", 0, 1.0), ("late", 17, 80.0),  # crosses after the last list week
    ("flat", 0, 2.0), ("flat", 6, 24.0),
]  # fmt: skip


def test_crowd_adds_first_crossing_and_started_below() -> None:
    o = owned(PATHS)
    b = cr.baselines(o)
    adds = cr.crowd_adds(o, b, lw(), 50.0)
    got = {r["gsis_id"]: (r["add_period"], r["add_date"]) for r in adds.iter_rows(named=True)}
    assert got == {"up": (3, friday(3)), "new": (4, friday(4))}  # 50.0 counts (>=)
    assert adds.filter(pl.col("gsis_id") == "new").item(0, "start_pct") is None
    adds25 = cr.crowd_adds(o, b, lw(), 25.0)
    assert dict(adds25.select("gsis_id", "add_period").iter_rows()) == {"up": 2, "new": 2}
    assert cr.crowd_adds(o, b, lw(last=2), 50.0).height == 0  # crossings after the last list


def test_momentum_rule() -> None:
    o = owned([*PATHS, ("jump", 0, 10.0), ("jump", 1, 19.5), ("jump", 2, 29.0)])
    b = cr.baselines(o)
    vals = cr.period_values(o, b)
    # absent in a period with scrapes -> 0 (the pages reach 0%)
    assert vals.filter((pl.col("gsis_id") == "new") & (pl.col("period") == 1)).item(0, "value") == 0
    mom = cr.momentum_flags(vals, b, lw(), 50.0, 10.0)
    got = dict(mom.select("gsis_id", "flag_period").iter_rows())
    # up: +15 in period 1; new: 0 -> 30 in 2; star: 0 (absent) -> 40 in 3; flat: 0 -> 24 in 6;
    # jump: +9.5 twice, never 10
    assert got == {"up": 1, "new": 2, "star": 3, "flat": 6}
    low = cr.momentum_flags(vals, b, lw(), 4.0, 10.0)  # "up" was never under 4% before a rise
    assert "up" not in low.get_column("gsis_id").to_list()


# --------------------------------------------------------------------------------------
# Leads: sign, same week, after, never
# --------------------------------------------------------------------------------------


def adds_frame(rows: list[tuple[str, int]]) -> pl.DataFrame:
    return pl.DataFrame(
        {"season": [S] * len(rows), "gsis_id": [r[0] for r in rows], "pos": ["RB"] * len(rows),
         "add_period": [r[1] for r in rows]},
        schema={"season": pl.Int32, "gsis_id": pl.String, "pos": pl.String, "add_period": pl.Int32},
    )  # fmt: skip


def weeks_frame(rows: list[tuple[str, int]]) -> pl.DataFrame:
    return pl.DataFrame(
        {"season": [S] * len(rows), "gsis_id": [r[0] for r in rows], "week": [r[1] for r in rows]},
        schema={"season": pl.Int32, "gsis_id": pl.String, "week": pl.Int32},
    )


def test_lead_sign_and_categories() -> None:
    adds = adds_frame([("early", 6), ("tie", 4), ("late", 3), ("miss", 5), ("out", 5)])
    flags = weeks_frame([("early", 2), ("early", 5), ("tie", 4), ("late", 7)])
    pool = weeks_frame([("early", 1), ("tie", 1), ("late", 1), ("miss", 1)])
    got = st.add_leads(adds, flags, pool).sort("gsis_id")
    rows = {r["gsis_id"]: r for r in got.iter_rows(named=True)}
    assert (rows["early"]["lead"], rows["early"]["category"]) == (4, "before")  # + = Radar first
    assert rows["early"]["nearest_lead"] == 1  # the latest flag at or before the add (week 5)
    assert (rows["tie"]["lead"], rows["tie"]["category"]) == (0, "same")
    assert (rows["late"]["lead"], rows["late"]["category"]) == (-4, "after")
    assert rows["late"]["nearest_lead"] == -4  # no flag at or before: the first flag's lead
    assert (rows["miss"]["lead"], rows["miss"]["category"], rows["miss"]["in_pool"]) == (
        None,
        "never",
        True,
    )
    assert (rows["out"]["category"], rows["out"]["in_pool"]) == ("never", False)


# --------------------------------------------------------------------------------------
# The reverse view (never added), head to head, suppression
# --------------------------------------------------------------------------------------


def test_state_at_flag_already_added_never() -> None:
    o = owned(PATHS)
    b = cr.baselines(o)
    flags = pl.DataFrame(
        {"season": [S] * 4, "gsis_id": ["up", "star", "flat", "up"], "flag_period": [2, 1, 3, 0]},
        schema={"season": pl.Int32, "gsis_id": pl.String, "flag_period": pl.Int32},
    )
    got = st.state_at_flag(flags, o, b, 50.0)
    rows = {(r["gsis_id"], r["flag_period"]): r for r in got.iter_rows(named=True)}
    assert (rows[("up", 2)]["state"], rows[("up", 2)]["later_period"]) == ("added_later", 3)
    assert rows[("up", 2)]["pct_before"] == 20.0  # the scrape before the list (period 1)
    assert rows[("star", 1)]["state"] == "already"
    assert rows[("flat", 3)]["state"] == "never"
    assert ("up", 0) not in rows  # no crowd data before a week-0 flag


def test_head_to_head() -> None:
    adds = adds_frame([("a", 6), ("b", 6), ("c", 6), ("d", 6)])
    radar = st.add_leads(adds, weeks_frame([("a", 2), ("b", 3), ("d", 8)]))
    mom = st.add_leads(adds, weeks_frame([("a", 4), ("c", 5), ("d", 6)]))
    got = st.head_to_head(radar, mom).sort("gsis_id")
    assert got.get_column("h2h").to_list() == ["both", "radar_only", "momentum_only", "neither"]
    assert got.get_column("radar_earlier").to_list() == [True, False, False, False]


def test_summarize_shares_and_small_cells() -> None:
    adds = adds_frame([(f"p{i}", 6) for i in range(6)])
    flags = weeks_frame([("p0", 2), ("p1", 4), ("p2", 6), ("p3", 9)])
    leads = st.add_leads(adds, flags)
    row = st.summarize(leads, ["season"]).row(0, named=True)
    assert (row["n_adds"], row["n_before"], row["n_same"], row["n_after"], row["n_never"]) == (
        6,
        2,
        1,
        1,
        2,
    )
    assert row["share_before"] == pytest.approx(2 / 6)
    assert row["lead_median"] == pytest.approx(1.0)  # leads 4, 2, 0, -3
    small = st.summarize(leads.head(4), ["season"]).row(0, named=True)
    assert small["n_adds"] == 4 and small["share_before"] is None and small["lead_median"] is None


# --------------------------------------------------------------------------------------
# Point-in-time priorities, the whole build, aggregate-only frames
# --------------------------------------------------------------------------------------


def radar_rows() -> pl.DataFrame:
    """2020: 600 low-score misses and 600 high-score hits (the bins a 2021 list learns from);
    2021 week 1: 'up' high at rank 1 (must-add), 'new' low at rank 2 (watch), 'x' rank 30."""
    prior = [
        (2020, 1, "WR", f"h{i}", 0.9 if i % 2 else 0.1, 1 + i % 25, bool(i % 2))
        for i in range(1200)
    ]
    now = [(S, 1, "WR", "up", 0.9, 1, True), (S, 1, "WR", "new", 0.1, 2, False),
           (S, 1, "WR", "x", 0.9, 30, True), (S, 2, "WR", "flat", 0.9, 1, False),
           (S, 16, "WR", "zz", 0.1, 3, False)]  # fmt: skip
    return pl.DataFrame(prior + now, schema=fl.ROWS_SCHEMA, orient="row")


def test_tiers_use_earlier_seasons_and_top25_only() -> None:
    lists = fl.tiered(radar_rows(), [S])
    tiers = dict(lists.select("gsis_id", "tier").iter_rows())
    assert tiers == {"up": "must-add", "new": "watch", "x": None, "flat": "must-add", "zz": "watch"}
    first = fl.first_flags(fl.flag_weeks(lists)).sort("gsis_id")
    up = first.filter(pl.col("gsis_id") == "up").row(0, named=True)
    assert (up["must_add"], up["listed"], up["pool"]) == (1, 1, 1)
    x = first.filter(pl.col("gsis_id") == "x").row(0, named=True)
    assert (x["listed"], x["pool"]) == (None, 1)
    assert fl.list_weeks(lists).item(0, "last_list_week") == 16


def test_build_frames_are_aggregate_only_and_deterministic() -> None:
    raw = owned(PATHS).drop("period")
    a = st.build_frames(st.build(raw, windows(), radar_rows(), seasons=[S]))
    b = st.build_frames(st.build(raw, windows(), radar_rows(), seasons=[S]))
    assert set(a) == {"coverage", "summary", "hist", "reverse", "conversion", "h2h"}
    for name, df in a.items():
        assert not set(df.columns) & st.PRIVATE_COLUMNS, name
        assert df.equals(b[name]), name
    s = a["summary"].filter((pl.col("scope") == "complete") & (pl.col("signal") == "must_add"))
    row = s.filter(pl.col("threshold") == 50.0).row(0, named=True)
    assert (row["n_adds"], row["share_before"]) == (2, None)  # < MIN_CELL adds: no statistics
    cov = a["coverage"].row(0, named=True)
    assert (cov["season"], cov["complete"], cov["in_study"]) == (S, True, True)


def test_assert_aggregate_only_refuses_player_columns() -> None:
    with pytest.raises(ValueError, match="gsis_id"):
        st.assert_aggregate_only({"x": pl.DataFrame({"gsis_id": ["a"], "n": [1]})})
    with pytest.raises(ValueError, match="pct"):
        st.assert_aggregate_only({"x": pl.DataFrame({"pct": [50.0]})})
    st.assert_aggregate_only({"ok": pl.DataFrame({"threshold": [50.0], "n": [7]})})


def test_build_leads_end_to_end() -> None:
    study = st.build(owned(PATHS).drop("period"), windows(), radar_rows(), seasons=[S])
    lead = study.leads.filter((pl.col("threshold") == 50.0) & (pl.col("signal") == "must_add"))
    got = {r["gsis_id"]: (r["lead"], r["category"]) for r in lead.iter_rows(named=True)}
    assert got == {"up": (2, "before"), "new": (None, "never")}  # 'new' only listed as watch
    listed = study.leads.filter((pl.col("threshold") == 50.0) & (pl.col("signal") == "listed"))
    assert dict(listed.select("gsis_id", "lead").iter_rows()) == {"up": 2, "new": 3}
    mom = study.leads.filter((pl.col("threshold") == 50.0) & (pl.col("signal") == "momentum"))
    assert dict(mom.select("gsis_id", "lead").iter_rows()) == {"up": 2, "new": 2}


# --------------------------------------------------------------------------------------
# The 2026 live tracker
# --------------------------------------------------------------------------------------


def test_live_path_is_keyed_on_sync_time_and_tracks_states() -> None:
    t = ASOF1 + timedelta(days=2)  # in waiver period 1
    rows = pl.DataFrame(
        [(S, "up", t, 30.0), (S, "up", t + timedelta(days=7), 55.0),  # crosses in period 2
         (S, "hi", t, 80.0), (S, "hi", t + timedelta(days=7), 85.0),
         (S, "low", t, 5.0), (S, "low", t + timedelta(days=7), 6.0),
         (S, "gone", t, 10.0),  # missing from the latest sync: maybe rostered in the league
         (S, "up", t + timedelta(hours=1), 31.0)],  # a second sync in period 1: the latest wins
        schema=lv.LIVE_SCHEMA, orient="row",
    )  # fmt: skip
    path = lv.live_path(rows, windows())
    assert path.filter(pl.col("gsis_id") == "up").get_column("pct").to_list() == [31.0, 55.0]
    lists = pl.DataFrame(
        [(S, 1, "WR", "up", 1, "must-add", "live"), (S, 1, "WR", "other", 2, "watch", "live")],
        schema={"season": pl.Int32, "week": pl.Int32, "position": pl.String, "gsis_id": pl.String,
                "rank": pl.Int32, "tier": pl.String, "kind": pl.String},
        orient="row",
    )  # fmt: skip
    tr = lv.live_tracker(path, lists, 50.0)
    state = dict(tr.select("gsis_id", "state").iter_rows())
    assert state == {
        "up": "added",
        "hi": "already",
        "low": "below",
        "gone": "left_pool",
        "other": "unseen",
    }
    up = tr.filter(pl.col("gsis_id") == "up").row(0, named=True)
    assert (up["cross_period"], up["must_add"], up["lead_must_add"]) == (2, 1, 1)
    summary = lv.live_summary(tr)
    assert summary.filter(pl.col("state") == "added").item(0, "before_must_add") == 1
