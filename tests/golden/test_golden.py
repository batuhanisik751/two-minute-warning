"""Golden tests (PROJECT_SPEC 13): a small frozen synthetic season slice with known outputs.

The slice is built by the real pipeline from the committed inputs (tests/golden/inputs/, see
make_inputs.py; synthetic, never real data) and every output is compared with the committed
answer in tests/golden/expected/: the pool, the labels, the features, a per-week summary of
the whole dataset, a tiny walk-forward backtest (predictions and fold settings) and the weekly
list with its reasons and report.

A failure prints a readable diff. If the change is intended, regenerate the answers
deliberately and review them before committing:

    uv run python tests/golden/update.py
"""

from __future__ import annotations

import difflib

import pytest

from tests.golden import world

EXPECTED = sorted(p.name for p in world.EXPECTED_DIR.iterdir() if p.is_file())
MAX_DIFF_LINES = 60
MAX_BYTES = 1_000_000


@pytest.fixture(scope="module")
def produced(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    return world.compute(tmp_path_factory.mktemp("golden"))


def _diff(name: str, expected: str, got: str) -> str:
    lines = list(
        difflib.unified_diff(
            expected.splitlines(), got.splitlines(), f"expected/{name}", f"produced/{name}",
            n=1, lineterm="",
        )
    )  # fmt: skip
    more = len(lines) - MAX_DIFF_LINES
    shown = lines[:MAX_DIFF_LINES] + ([f"... {more} more diff lines"] if more > 0 else [])
    return (
        f"golden output {name} changed. If that is intended, run "
        "`uv run python tests/golden/update.py` and review the diff of tests/golden/expected/ "
        "before committing.\n" + "\n".join(shown)
    )


@pytest.mark.parametrize("name", EXPECTED)
def test_output_matches_the_frozen_answer(produced, name):
    expected = (world.EXPECTED_DIR / name).read_text()
    got = produced.get(name)
    assert got is not None, f"{name} is no longer produced; run tests/golden/update.py"
    if got != expected:
        pytest.fail(_diff(name, expected, got), pytrace=False)


def test_every_output_has_a_frozen_answer(produced):
    assert sorted(produced) == EXPECTED, "run `uv run python tests/golden/update.py`"


def test_the_golden_world_is_small_and_synthetic():
    inputs = world.input_size()
    expected = sum((world.EXPECTED_DIR / n).stat().st_size for n in EXPECTED)
    assert inputs + expected < MAX_BYTES
    players = world.read_input("players", None)
    # every id is made up: the 00-0099xxx range and Goldxxx PFR slugs
    assert players.get_column("gsis_id").str.starts_with("00-0099").all()
    assert players.get_column("pfr_id").str.starts_with("Gold").all()


def test_the_weekly_list_is_the_backtest_fold_and_complete(produced):
    import json

    weekly = json.loads(produced["weekly_list.json"])
    assert weekly["backtest_version_of_the_same_fold"] is True
    assert weekly["kind"] == "backtest" and weekly["freshness_problems"] == []
    assert {r["position"] for r in weekly["list"]} == {"QB", "RB", "WR", "TE"}
    # a reason is a fact that raised his chance: most listed players have one
    assert sum(bool(r["reasons"]) for r in weekly["list"]) > len(weekly["list"]) / 2


def test_a_changed_output_fails_with_a_readable_diff():
    old = "season,week,rows\n2024,3,156\n"
    msg = _diff("dataset_summary.csv", old, old.replace("156", "157"))
    assert "uv run python tests/golden/update.py" in msg
    assert "-2024,3,156" in msg and "+2024,3,157" in msg
