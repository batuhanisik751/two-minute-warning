"""`twm ingest` keeps going after a failure, reports every failure and exits non-zero."""

import re

import polars as pl
import pytest
from typer.testing import CliRunner

from twm.cli import app
from twm.config import settings
from twm.sources import nflverse as nv

runner = CliRunner()
CURRENT = settings().current_season


def _plain(text: str) -> str:
    """Typer prints usage errors in a Rich panel: colored when CI forces color, boxed, and
    wrapped to the terminal width. Strip all of that so assertions see the message itself."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    text = re.sub(r"[│╭╮╰╯─]", " ", text)
    return " ".join(text.split())


@pytest.fixture
def calls(monkeypatch):
    """Replace nv.fetch with a recorder; seasons listed in `fail` raise."""
    seen: list[tuple[str, int | None]] = []
    fail: dict[tuple[str, int | None], Exception] = {}

    def fake_fetch(name, season=None, **kw):
        seen.append((name, season))
        if (name, season) in fail:
            raise fail[(name, season)]
        return pl.DataFrame({"a": [1, 2]})

    monkeypatch.setattr(nv, "fetch", fake_fetch)
    return seen, fail


def test_success_exits_zero(calls):
    seen, _ = calls
    result = runner.invoke(app, ["ingest", "schedules", "--start", "2024", "--end", "2025"])
    assert result.exit_code == 0, result.output
    assert seen == [("schedules", 2024), ("schedules", 2025)]


def test_failure_in_one_file_dataset_does_not_stop_the_run(calls):
    seen, fail = calls
    fail[("players", None)] = ConnectionError("network down")
    result = runner.invoke(app, ["ingest", "players", "teams"])
    assert result.exit_code == 1
    assert ("teams", None) in seen  # the dataset after the failure still ran
    assert "players all: ConnectionError: network down" in result.output


def test_full_error_message_is_reported_not_truncated(calls):
    _, fail = calls
    long = "x" * 300
    fail[("schedules", 2025)] = RuntimeError(long)
    result = runner.invoke(app, ["ingest", "schedules", "--start", "2025", "--end", "2025"])
    assert result.exit_code == 1
    assert long in result.output


def test_schema_drift_is_reported_with_guidance(calls):
    _, fail = calls
    fail[("schedules", 2025)] = nv.SchemaDriftError("schedules: columns ... ['spread_line']")
    result = runner.invoke(app, ["ingest", "schedules", "--start", "2025", "--end", "2025"])
    assert result.exit_code == 1
    assert "spread_line" in result.output
    assert "Schema drift" in result.output


def test_start_is_clamped_to_first_season_with_one_message(calls):
    seen, _ = calls
    result = runner.invoke(app, ["ingest", "snap_counts", "--start", "2010", "--end", "2014"])
    assert result.exit_code == 0, result.output
    assert seen == [("snap_counts", 2013), ("snap_counts", 2014)]
    assert result.output.count("starts in 2013") == 1


def test_start_after_end_is_rejected(calls):
    result = runner.invoke(app, ["ingest", "schedules", "--start", "2025", "--end", "2020"])
    assert result.exit_code == 2
    assert "is after --end" in _plain(result.output)


def test_post_season_only_dataset_skips_current_season(calls):
    seen, _ = calls
    result = runner.invoke(app, ["ingest", "participation", "--start", str(CURRENT - 1)])
    assert result.exit_code == 0, result.output
    assert seen == [("participation", CURRENT - 1)]
    assert "published only after the season" in result.output
