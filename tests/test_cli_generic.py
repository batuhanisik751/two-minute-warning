"""The spec's generic entry points (PROJECT_SPEC 9): `twm train <module>`, `twm backtest
<module>` and `twm score --as-of <timestamp|season-week>`; the as-of parsing and the rule that
maps a timestamp to a week (twm.asof.parse_as_of / week_at). Offline and synthetic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner

from tests.conftest import season_2025_games
from tests.radar_synthetic import synthetic_dataset
from twm import cli
from twm.asof import AsOfParseError, parse_as_of, week_at, weekly_as_of
from twm.warehouse import build as wb

runner = CliRunner()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-W3", (2026, 3)),
        ("2026w03", (2026, 3)),
        ("2026 3", (2026, 3)),
        (" 2025-W18 ", (2025, 18)),
        ("2026-09-29T14:00Z", datetime(2026, 9, 29, 14, tzinfo=UTC)),
        ("2026-09-29T14:00:00+00:00", datetime(2026, 9, 29, 14, tzinfo=UTC)),
        ("2026-09-29T10:00-04:00", datetime(2026, 9, 29, 14, tzinfo=UTC)),  # US Eastern (EDT)
    ],
)
def test_parse_as_of_accepts_season_weeks_and_zoned_timestamps(text, expected):
    got = parse_as_of(text)
    assert got == expected
    if isinstance(got, datetime):
        assert got.tzinfo is UTC


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("2026-09-29T14:00", "no time zone"),
        ("2026-09-29", "no time zone"),
        ("next tuesday", "not a season-week"),
        ("2026-W0", "weeks start at 1"),
        ("W3", "not a season-week"),
    ],
)
def test_parse_as_of_refuses_naive_and_unknown_text(text, message):
    with pytest.raises(AsOfParseError, match=message):
        parse_as_of(text)


@pytest.fixture
def db(raw, db_path):
    raw.write_season(2025, season_2025_games())
    wb.build_warehouse([2025], db_path=db_path)
    return db_path


def test_week_at_maps_a_timestamp_to_the_week_whose_window_contains_it(db):
    w1, w2 = weekly_as_of(db, 2025, 1), weekly_as_of(db, 2025, 2)
    assert w1 == datetime(2025, 9, 9, 14, tzinfo=UTC)  # Tuesday after the Thursday opener
    with pytest.raises(LookupError, match="before every regular-season as-of"):
        week_at(db, w1 - timedelta(minutes=1))
    assert week_at(db, w1) == (2025, 1, w1)  # the window starts at the as-of itself
    assert week_at(db, w1 + timedelta(days=4))[:2] == (2025, 1)  # the Saturday after
    assert week_at(db, w2 - timedelta(seconds=1))[:2] == (2025, 1)
    eastern = timezone(timedelta(hours=-4))
    assert week_at(db, w2.astimezone(eastern))[:2] == (2025, 2)  # any zone, same moment
    # the playoffs and the offseason: the last regular-season week's list stays current
    assert week_at(db, datetime(2026, 3, 1, tzinfo=UTC))[:2] == (2025, 4)
    with pytest.raises(TypeError, match="timezone-aware"):
        week_at(db, datetime(2025, 9, 20))


def _capture(monkeypatch, name: str) -> list[dict]:
    calls: list[dict] = []
    monkeypatch.setattr(cli, name, lambda **kw: calls.append(kw))
    return calls


def test_score_dispatches_a_season_week_to_the_radar(monkeypatch):
    calls = _capture(monkeypatch, "radar_score")
    out = runner.invoke(cli.app, ["score", "--as-of", "2025-W2", "--allow-incomplete"])
    assert out.exit_code == 0, out.output
    assert len(calls) == 1
    assert (calls[0]["season"], calls[0]["week"], calls[0]["allow_incomplete"]) == (2025, 2, True)
    assert calls[0]["now"] is None  # the real clock: live or reconstructed by the usual rule
    default = runner.invoke(cli.app, ["score"])
    assert default.exit_code == 0 and (calls[1]["season"], calls[1]["week"]) == (None, None)


def test_score_resolves_a_timestamp_through_the_warehouse(db, monkeypatch):
    calls = _capture(monkeypatch, "radar_score")
    out = runner.invoke(cli.app, ["score", "--as-of", "2025-09-20T12:00Z", "--db", str(db)])
    assert out.exit_code == 0, out.output
    assert (calls[0]["season"], calls[0]["week"]) == (2025, 2)
    assert "window of 2025 week 2" in out.output and "Tue 2025-09-16 14:00 UTC" in out.output
    early = runner.invoke(cli.app, ["score", "--as-of", "2025-08-01T00:00Z", "--db", str(db)])
    assert early.exit_code == 1 and "before every regular-season as-of" in early.output


def test_score_refuses_naive_timestamps_and_unknown_modules(monkeypatch):
    calls = _capture(monkeypatch, "radar_score")
    naive = runner.invoke(cli.app, ["score", "--as-of", "2025-09-20T12:00"])
    assert naive.exit_code == 2 and "no time zone" in naive.output
    bad = runner.invoke(cli.app, ["score", "--module", "nope"])
    assert bad.exit_code == 2 and "available: waiver_radar" in bad.output
    planned = runner.invoke(cli.app, ["score", "--module", "hot_seat"])
    assert planned.exit_code == 2 and "not built yet (phase H)" in planned.output
    assert calls == []


def test_backtest_dispatches_to_the_radar(monkeypatch):
    calls = _capture(monkeypatch, "radar_backtest")
    out = runner.invoke(
        cli.app, ["backtest", "waiver-radar", "--start", "2020", "--end", "2021", "--model",
                  "logit", "--label", "y_sustained"],
    )  # fmt: skip
    assert out.exit_code == 0, out.output
    assert calls[0]["start"] == 2020 and calls[0]["models"] == ["logit"]
    assert calls[0]["labels"] == ["y_sustained"]
    bad = runner.invoke(cli.app, ["backtest", "regression_watch"])
    assert bad.exit_code == 2 and "phase D" in bad.output and calls[1:] == []


def test_train_builds_then_reuses_the_production_fold(tmp_path):
    ds = tmp_path / "dataset.parquet"
    synthetic_dataset((2021, 2022, 2023, 2024), weeks=4, per_pos=15, seed=5).write_parquet(ds)
    args = ["train", "waiver_radar", "--season", "2025", "--dataset", str(ds), "--store",
            str(tmp_path / "p.duckdb"), "--models-dir", str(tmp_path / "models")]  # fmt: skip
    first = runner.invoke(cli.app, args)
    assert first.exit_code == 0, first.output
    assert "trained now" in first.output and "trained on 2021-2024" in first.output
    version = first.output.split()[2]
    assert (tmp_path / "models" / f"{version}.joblib").exists()
    again = runner.invoke(cli.app, args)
    assert again.exit_code == 0 and "reused" in again.output and version in again.output
    forced = runner.invoke(cli.app, [*args, "--retrain"])
    assert forced.exit_code == 0 and "trained now" in forced.output
    missing = runner.invoke(cli.app, ["train", "waiver_radar", "--dataset", str(tmp_path / "x")])
    assert missing.exit_code == 1 and "run `twm radar dataset` first" in missing.output
    bad = runner.invoke(cli.app, ["train", "board"])
    assert bad.exit_code == 2 and "phase I" in bad.output
