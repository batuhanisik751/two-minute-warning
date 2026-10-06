"""The frozen coach-tendency history (C10c, twm.modules.coach_tendencies.production) on a
synthetic warehouse (tests/test_coach_tendencies.py's DuckRunner, 2023-2026, 2026 in progress):
the pin's round trip and refusals, the pinned publish equal to the direct build (the Mac), a
2025+ warehouse publishing the same history (the runner), only the seasons after the history
read from the warehouse, and career lines recomputed when a completed season follows it."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from tests.test_coach_tendencies import DuckRunner, league, team_week
from twm import pins
from twm.modules.coach_tendencies import build as bd
from twm.modules.coach_tendencies import production as prod
from twm.modules.coach_tendencies import season as sn
from twm.publish import coach_tendencies as ct
from twm.publish.collect import PublishInputError

NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)
WEEKS = {2023: 4, 2024: 3, 2025: 5, 2026: 2}
NAMES = pl.DataFrame({"coach_id": ["coach_a", "coach_b", "coach_c"],
                      "coach_name": ["Coach Able", "Coach Baker", "Coach Cole"]})  # fmt: skip


def warehouse(first: int = 2023) -> DuckRunner:
    """Plays, coaches and team weeks of WEEKS from ``first`` on; BBB passes on every 2025 snap
    (so seasons differ and a career line is more than one season)."""
    fp, cg = league({y: w for y, w in WEEKS.items() if y >= first})
    fp = fp.with_columns(pl.when((pl.col("season") == 2025) & (pl.col("posteam") == "BBB"))
                         .then(1).otherwise(pl.col("pass")).alias("pass"))  # fmt: skip
    tw = team_week({y: w for y, w in WEEKS.items() if y >= first})
    return DuckRunner(fact_play=fp, coach_game=cg, fact_team_week=tw)


def direct(runner: DuckRunner, current: int, first: int = 2023) -> dict[str, pl.DataFrame]:
    """What the publish built before C10c: every season from the warehouse."""
    newest = min(bd.latest_season(runner), current)
    return bd.build(runner, range(first, newest + 1), current, n_boot=50)


def pin_it(tmp_path: Path, current: int) -> tuple[pins.Pin, Path]:
    """Freeze the full warehouse's completed seasons before ``current`` into ``tmp_path``."""
    frames = direct(warehouse(), current)
    pin_path = tmp_path / "production_models.yaml"
    pin = prod.approve(prod.freeze(frames, NAMES, current, first=2023), current,
                       root=tmp_path, path=pin_path, report_dir=tmp_path / "reports")  # fmt: skip
    return pin, pin_path


def same(a: dict[str, pl.DataFrame], b: dict[str, pl.DataFrame], names: tuple[str, ...]) -> None:
    for name in names:
        assert a[name].schema == b[name].schema, name
        assert a[name].equals(b[name]), name


PUBLISHED = tuple(bd.FRAME_KEYS)


def test_pin_round_trip(tmp_path: Path) -> None:
    pin, pin_path = pin_it(tmp_path, 2026)
    assert (pin.module, pin.season, pin.model, pin.backtest_seasons) == (
        "coach_tendencies", 2026, "history", "2023-2025")  # fmt: skip
    assert pin.model_version.startswith("history-") and set(pin.backtest) == set(prod.TABLES)
    assert pins.read_pins(pin_path)["coach_tendencies"] == pin
    history, again = prod.load_pinned(2026, path=pin_path, root=tmp_path)
    assert again == pin and history.seasons == (2023, 2024, 2025) and history.last == 2025
    frozen = prod.freeze(direct(warehouse(), 2026), NAMES, 2026, first=2023)
    for t in prod.TABLES:  # the parquet round trip is exact
        assert history.tables[t].equals(frozen[t]), t
    assert history.tables[prod.COACHES]["coach_id"].to_list() == ["coach_a", "coach_b", "coach_c"]
    assert prod.report_mismatches(history, tmp_path / "reports") == []
    assert "2023-2025" in (tmp_path / "reports" / "report.md").read_text()
    # approving the same history again writes the same files and the same pin
    assert pin_it(tmp_path, 2026)[0] == pin


def test_missing_or_tampered_pins_are_refused(tmp_path: Path) -> None:
    with pytest.raises(pins.PinError, match="no frozen coach-tendency history"):
        prod.load_pinned(2026, path=tmp_path / "none.yaml", root=tmp_path)
    with pytest.raises(PublishInputError, match="coach tendencies: no frozen"):  # the publish
        ct.collect_coach_tendencies(tmp_path / "w.duckdb", 2026, NOW,
                                    pin_path=tmp_path / "none.yaml", root=tmp_path)  # fmt: skip
    pin, pin_path = pin_it(tmp_path, 2026)
    snap = pin.backtest[prod.SEASONS].path(tmp_path)
    good = snap.read_bytes()
    snap.write_bytes(good[:-8] + b"tampered")
    with pytest.raises(pins.PinError, match="not the approved backtest file"):
        prod.load_pinned(2026, path=pin_path, root=tmp_path)
    with pytest.raises(PublishInputError, match="coach tendencies: .*not the approved"):
        ct.collect_coach_tendencies(tmp_path / "w.duckdb", 2026, NOW,
                                    pin_path=pin_path, root=tmp_path)  # fmt: skip
    snap.write_bytes(good)
    spec = pin.path(tmp_path)
    text = spec.read_text()
    spec.write_text(text.replace('"seed": 20261005', '"seed": 1'))
    with pytest.raises(pins.PinError, match="not the approved file"):
        prod.load_pinned(2026, path=pin_path, root=tmp_path)
    spec.write_text(text)
    snap.unlink()
    with pytest.raises(pins.PinError, match="backtest file is missing"):
        prod.load_pinned(2026, path=pin_path, root=tmp_path)
    snap.write_bytes(good)
    prod.load_pinned(2026, path=pin_path, root=tmp_path)  # restored: usable again
    report = tmp_path / "reports" / "persistence.csv"
    report.write_text(report.read_text() + "x\n")
    history, _ = prod.load_pinned(2026, path=pin_path, root=tmp_path)
    assert prod.report_mismatches(history, tmp_path / "reports") == [
        f"{report} is missing or differs from the frozen history"]  # fmt: skip


def test_other_definitions_or_a_history_reaching_the_season_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, pin_path = pin_it(tmp_path, 2026)
    with pytest.raises(pins.PinError, match="reaches the season in progress 2025"):
        prod.load_pinned(2025, path=pin_path, root=tmp_path)
    monkeypatch.setattr(sn, "MIN_PLAYS_RANKED", 151)
    with pytest.raises(pins.PinError, match="frozen with other definitions"):
        prod.load_pinned(2026, path=pin_path, root=tmp_path)


def test_a_partial_warehouse_is_never_frozen() -> None:
    partial = direct(warehouse(first=2025), 2026, first=2025)
    with pytest.raises(prod.CoachTendencyPinError, match="from 2023 without a gap"):
        prod.freeze(partial, NAMES, 2026, first=2023)
    gap = {**partial, "coach_wide": pl.concat([direct(warehouse(), 2026)["coach_wide"].filter(
        pl.col("season") == 2023), partial["coach_wide"]], how="vertical_relaxed")}  # fmt: skip
    with pytest.raises(prod.CoachTendencyPinError, match=r"found 2023-2025, missing \[2024\]"):
        prod.freeze(gap, NAMES, 2026, first=2023)
    with pytest.raises(prod.CoachTendencyPinError, match="missing from dim_coach"):
        prod.freeze(direct(warehouse(), 2026), NAMES.head(2), 2026, first=2023)


def test_the_pinned_publish_equals_the_direct_build(tmp_path: Path) -> None:
    """The Mac: the full warehouse, the pin of 2026; every published frame identical."""
    _, pin_path = pin_it(tmp_path, 2026)
    history, _ = prod.load_pinned(2026, path=pin_path, root=tmp_path)
    runner = warehouse()
    want, got = direct(runner, 2026), prod.frames_from_history(history, runner, 2026)
    same(got, want, PUBLISHED)
    assert got["coach_tendency_season"].filter(pl.col("is_current"))["season"].unique().to_list() \
        == [2026]  # fmt: skip
    names = prod.coach_names(history, NAMES)
    assert names.equals(NAMES)  # every frozen coach is in the full dim_coach
    a, ca = ct.site_tables(got, names)
    b, cb = ct.site_tables({k: want[k] for k in PUBLISHED}, NAMES)
    same(a, b, tuple(a))
    assert ca.equals(cb)


def test_a_partial_warehouse_publishes_the_same_history(tmp_path: Path) -> None:
    """The runner: plays, coaches and team weeks from 2026 only, a dim_coach without the
    history's coaches: the same published rows as the full warehouse's direct build."""
    _, pin_path = pin_it(tmp_path, 2026)
    history, _ = prod.load_pinned(2026, path=pin_path, root=tmp_path)
    partial = warehouse(first=2026)
    assert partial.sql("SELECT min(season) AS s FROM fact_play")[0, 0] == 2026
    got = prod.frames_from_history(history, partial, 2026)
    want = direct(warehouse(), 2026)
    same(got, want, PUBLISHED)
    assert got["coach_tendency_season"]["season"].unique().sort().to_list() == [2023, 2024, 2025,
                                                                                2026]  # fmt: skip
    runner_names = NAMES.filter(pl.col("coach_id") != "coach_c")  # coach_c: history only here
    names = prod.coach_names(history, runner_names)
    assert sorted(names["coach_id"].to_list()) == ["coach_a", "coach_b", "coach_c"]
    a, _ = ct.site_tables(got, names)
    b, _ = ct.site_tables({k: want[k] for k in PUBLISHED}, NAMES)
    same(a, b, tuple(a))
    # a direct build on the partial warehouse is what the pin prevents: no history at all
    short = direct(partial, 2026, first=2026)
    assert short["coach_tendency_career"].height == 0
    assert got["coach_tendency_career"].height > 0


class Spy(DuckRunner):
    """A DuckRunner that records every query."""

    def __init__(self, inner: DuckRunner) -> None:
        self.con, self.queries = inner.con, []

    def sql(self, query: str) -> pl.DataFrame:
        self.queries.append(query)
        return super().sql(query)


def test_only_the_seasons_after_the_history_are_read(tmp_path: Path) -> None:
    _, pin_path = pin_it(tmp_path, 2026)
    history, _ = prod.load_pinned(2026, path=pin_path, root=tmp_path)
    spy = Spy(warehouse())
    prod.frames_from_history(history, spy, 2026)
    plays = [q for q in spy.queries if "FROM fact_play p" in q]
    assert len(plays) == 1 and "p.season = 2026" in plays[0]
    assert not any("fact_team_week" in q for q in spy.queries)  # the fantasy link is pinned


def test_career_lines_are_recomputed_when_a_completed_season_follows_the_history(
    tmp_path: Path,
) -> None:
    """A history frozen for 2025 (2023-2024) published in 2026 (before the yearly re-pin): 2025
    is built from the warehouse as a completed season and joins the career lines (numerators
    and samples summed); persistence and the fantasy link stay the pinned 2023-2024 frames."""
    _, pin_path = pin_it(tmp_path, 2025)
    history, _ = prod.load_pinned(2026, path=pin_path, root=tmp_path)
    assert history.seasons == (2023, 2024)
    want = direct(warehouse(), 2026)
    for runner in (warehouse(), warehouse(first=2025)):  # the Mac and a 2025+ runner
        got = prod.frames_from_history(history, runner, 2026)
        same(got, want, ("coach_tendency_season", "coach_tendency_career"))
        frozen = direct(warehouse(), 2025)
        same(got, frozen, ("coach_tendency_persistence", "coach_tendency_fantasy_link"))
    mine = (pl.col("coach_id") == "coach_b") & (pl.col("metric") == "neutral_pass_rate")
    car = got["coach_tendency_career"].filter(mine)
    row = car.row(0, named=True)
    assert (row["first_season"], row["last_season"], row["seasons"]) == (2023, 2025, 3)
    seasons = got["coach_tendency_season"].filter(mine & ~pl.col("is_current"))
    pooled = (seasons["value"] * seasons["sample"]).sum() / seasons["sample"].sum()
    assert row["value"] == pytest.approx(pooled) and row["sample"] == seasons["sample"].sum()
    assert seasons.filter(pl.col("season") == 2025)["value"].item() == 1.0  # BBB passed in 2025
