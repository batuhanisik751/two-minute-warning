"""Lead time vs the crowd's part of `twm publish` (feature #8): only the study's aggregate frames
reach the publish (FantasyPros' license), the tables' layout and the validation. Synthetic:
tests/publish_lead_time_synthetic.py; the Postgres side (replaced tables, unchanged on a rerun,
the shrink guard) is in tests/test_publish_postgres.py."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from tests import publish_lead_time_synthetic as pls
from tests import publish_synthetic as ps
from twm.modules.lead_time import study as st
from twm.publish import collect as col
from twm.publish import lead_time as lt
from twm.publish.tables import LEAD_TIME, TABLES

# what would name a player or carry FantasyPros' per-player roster %
PLAYER_WORDS = ("gsis", "player", "name", "pct", "owned", "espn", "fantasypros", "scrape")


def test_site_tables_keep_the_layout_and_drop_nothing_but_the_never_bin() -> None:
    raw = pls.frames()
    t = lt.site_tables(raw)
    assert list(t) == list(LEAD_TIME)
    for name in LEAD_TIME:
        assert t[name].columns == list(TABLES[name].names)
        assert not t[name].select(TABLES[name].key).is_duplicated().any(), name
    s = t[lt.SUMMARY]
    assert set(s["threshold"]) == {25, 50} and s.schema["n_adds"] == pl.Int32
    assert dict(s.group_by("scope").agg(pl.col("scope_value").unique().sort()).iter_rows()) == {
        "complete": [""],
        "season": ["2021", "2022"],
        "position": ["QB", "RB", "TE", "WR"],
    }
    assert t[lt.HIST]["lead"].null_count() == 0
    assert raw["hist"]["lead"].null_count() > 0  # the never bin: the summary's n_never
    assert t[lt.HIST].height == raw["hist"]["lead"].drop_nulls().len()
    assert lt.problems(lt.LeadTimeData(t)) == []


def test_no_per_player_column_or_value_can_reach_the_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in LEAD_TIME:  # the published columns themselves
        cols = TABLES[name].names
        assert not set(cols) & (st.PRIVATE_COLUMNS | col.FORBIDDEN_COLUMNS), name
        named = [c for c in cols if not c.startswith("n_") and any(w in c for w in PLAYER_WORDS)]
        assert not named, name  # n_players: a count
    study = st.build(pls.owned(), pls.windows(), pls.radar_rows(), seasons=pls.SEASONS)
    ids = set(study.leads["gsis_id"]) | set(study.states["gsis_id"])
    assert len(ids) > 50  # the study itself is per player ...
    monkeypatch.setattr(st, "run", lambda warehouse=None: study)
    wh = tmp_path / "warehouse.duckdb"
    wh.touch()
    d = lt.collect_lead_time(wh)  # ... what the publish collects is not
    for name, df in d.tables.items():
        for c in df.select(pl.col(pl.String)).columns:
            assert not set(df[c].to_list()) & ids, (name, c)
    good = pls.frames()
    for frame, column in (("summary", "gsis_id"), ("hist", "pct"), ("h2h", "espn_id")):
        bad = {**good, frame: good[frame].with_columns(pl.lit("x").alias(column))}
        with pytest.raises(ValueError, match=column):
            lt.site_tables(bad)
    with pytest.raises(ValueError, match="exactly"):  # a per-player frame by another name
        lt.site_tables({**good, "leads": study.leads})
    smuggled = lt.LeadTimeData({**d.tables, lt.H2H: d.tables[lt.H2H].with_columns(
        pl.lit("x").alias("player_name"))})  # fmt: skip
    assert any("player_name" in p for p in lt.problems(smuggled))


def test_broken_rows_are_caught() -> None:
    good = lt.site_tables(pls.frames())
    d = lt.LeadTimeData(dict(good))

    def problems(name: str, df: pl.DataFrame) -> list[str]:
        d.tables = {**good, name: df}
        return lt.problems(d)

    s = good[lt.SUMMARY]
    assert any("repeats a key" in p for p in problems(lt.SUMMARY, pl.concat([s, s])))
    assert any("unknown signal" in p for p in problems(lt.SUMMARY, s.with_columns(
        pl.lit("gut").alias("signal"))))  # fmt: skip
    assert any("unknown threshold" in p for p in problems(lt.SUMMARY, s.with_columns(
        pl.lit(40, dtype=pl.Int32).alias("threshold"))))  # fmt: skip
    assert any("outside 0-1" in p for p in problems(lt.SUMMARY, s.with_columns(
        pl.lit(1.5).alias("share_before"))))  # fmt: skip
    assert any("do not add up" in p for p in problems(lt.SUMMARY, s.with_columns(
        (pl.col("n_before") + 1).alias("n_before"))))  # fmt: skip
    assert any("scope" in p for p in problems(lt.REVERSE, good[lt.REVERSE].with_columns(
        pl.lit("position").alias("scope"))))  # fmt: skip
    assert any("hist does not match" in p for p in problems(lt.HIST, good[lt.HIST].head(3)))
    assert any("unknown level" in p for p in problems(lt.H2H, good[lt.H2H].with_columns(
        pl.lit("momentum").alias("level"))))  # fmt: skip


def test_the_synthetic_publish_data_validates(tmp_path: Path) -> None:
    assert "lead_time" in col.MODULES
    data = pls.add_lead_time(col.collect(ps.build(tmp_path, live_weeks=(3,)).inputs))
    assert col.validate(data) == []
    assert data.tables[lt.COVERAGE].height == 2
