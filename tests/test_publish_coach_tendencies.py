"""Coach tendencies' part of `twm publish` (feature #10): the site's coach ids, NaN -> NULL, the
tables' layout, the site_meta keys and the validation. Synthetic:
tests/publish_coach_tendencies_synthetic.py; the Postgres side (replaced tables, dim_coach
upsert, unchanged on a rerun) is in tests/test_publish_postgres.py."""

from __future__ import annotations

import math
from pathlib import Path

import polars as pl
import pytest

from tests import publish_coach_tendencies_synthetic as pcs
from tests import publish_synthetic as ps
from twm.publish import coach_tendencies as ct
from twm.publish import collect as col
from twm.publish.collect import PublishInputError
from twm.publish.tables import COACH_TENDENCIES, TABLES


def test_coach_ids_become_site_slugs_of_the_names() -> None:
    tables, coaches = ct.site_tables(pcs.frames(), pcs.NAMES)
    for name in ct.COACH_TABLES:
        assert set(tables[name]["coach_id"]) <= set(pcs.SLUGS.values())
    assert "renee-d-arcy" in set(tables[ct.SEASON]["coach_id"])  # the name's slug, not the id
    assert coaches.rows() == [("jo-sample", "Jo Sample"), ("pat-example", "Pat Example"),
                              ("renee-d-arcy", "Renée D'Arcy")]  # fmt: skip
    for name in COACH_TENDENCIES:  # the tables' columns, in order, integers as Int32
        assert tables[name].columns == list(TABLES[name].names)
        assert not tables[name].select(TABLES[name].key).is_duplicated().any()
    assert tables[ct.SEASON].schema["sample"] == pl.Int32
    assert tables[ct.PERSISTENCE].schema["first_season"] == pl.Int32


def test_nan_becomes_null() -> None:
    raw = pcs.frames(nan=True)
    assert math.isnan(raw[ct.PERSISTENCE]["r"][0]) and math.isnan(raw[ct.LINK]["r"][0])
    tables, _ = ct.site_tables(raw, pcs.NAMES)
    for name in (ct.PERSISTENCE, ct.LINK):
        df = tables[name]
        assert df["r"].null_count() == 1 and df["ci_low"].null_count() == 1
        assert not any(df[c].is_nan().any() for c, t in df.schema.items() if t == pl.Float64)
    assert tables[ct.LINK]["y_per_x_sd"].null_count() == 1


def test_unknown_or_clashing_coaches_are_refused() -> None:
    with pytest.raises(PublishInputError, match="missing from the warehouse"):
        ct.site_tables(pcs.frames(), pcs.NAMES.filter(pl.col("coach_id") != "jo_sample"))
    twin = pl.concat([pcs.NAMES, pl.DataFrame({"coach_id": ["pat_example_2"],
                                               "coach_name": ["Pat  Example"]})])  # fmt: skip
    with pytest.raises(PublishInputError, match="share a site id"):
        ct.site_tables(pcs.frames(), twin)


def test_meta_names_the_season_in_progress() -> None:
    tables, coaches = ct.site_tables(pcs.frames(), pcs.NAMES)
    s, week = ct.latest(tables[ct.SEASON])
    d = ct.CoachTendencyData(s, week, tables, coaches)
    assert ct.meta(d) == {"coach_tendency_season": "2026", "coach_tendency_through_week": "3"}
    empty = {n: f.clear() for n, f in tables.items()}
    assert ct.meta(ct.CoachTendencyData(*ct.latest(empty[ct.SEASON]), empty, coaches)) == {
        "coach_tendency_season": "", "coach_tendency_through_week": ""}  # fmt: skip


@pytest.fixture
def data(tmp_path: Path) -> col.PublishData:
    return pcs.add_coach_tendencies(col.collect(ps.build(tmp_path, live_weeks=(3,)).inputs))


def test_the_synthetic_tendencies_pass_and_broken_rows_are_caught(data: col.PublishData) -> None:
    assert col.validate(data) == []
    d, good = data.coach_tendencies, dict(data.coach_tendencies.tables)

    def problems(name: str, df: pl.DataFrame) -> list[str]:
        d.tables = {**good, name: df}
        return ct.problems(d, {"BUF", "DAL", "KC", "SF"}, set(pcs.SLUGS.values()))

    s = good[ct.SEASON]
    assert any("not a current franchise" in p for p in problems(ct.SEASON, s.with_columns(
        pl.lit("OAK").alias("team"))))  # fmt: skip
    assert any("percentiles are outside" in p for p in problems(ct.SEASON, s.with_columns(
        pl.lit(101.0).alias("percentile"))))  # fmt: skip
    assert any("rates outside 0-1" in p for p in problems(ct.SEASON, s.with_columns(
        pl.lit(1.5).alias("value"))))  # fmt: skip
    stranger = good[ct.CAREER].with_columns(pl.lit("x-y").alias("coach_id"))
    assert any("missing from dim_coach" in p for p in problems(ct.CAREER, stranger))
    assert any("repeats a key" in p for p in problems(ct.PERSISTENCE, pl.concat(
        [good[ct.PERSISTENCE]] * 2)))  # fmt: skip
    assert any("unknown target" in p for p in problems(ct.LINK, good[ct.LINK].with_columns(
        pl.lit("rushing").alias("target"))))  # fmt: skip
    assert any("correlations outside" in p for p in problems(ct.LINK, good[ct.LINK].with_columns(
        pl.lit(1.2).alias("r"))))  # fmt: skip
    d.tables = good
    assert data.tables["dim_coach"].filter(pl.col("coach_id") == "renee-d-arcy").height == 1
    assert data.meta["coach_tendency_season"] == "2026"
