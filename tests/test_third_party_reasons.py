"""The site hides reasons built from FantasyPros' per-player values while it is public (license,
docs/progress.md 2026-10-04). Published reasons are text only, so the site identifies them by their
template (web/lib/third-party-reasons.json: id = the registry feature key). These tests fail when
that list and the registry drift apart."""

import json
from pathlib import Path

from twm import registry as rg
from twm.modules.waiver_radar import reasons as rs

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "web" / "lib" / "third-party-reasons.json"

# a feature row per id that phrase() turns into the manifest's example
ROWS = {
    "preseason_pos_rank": {"preseason_pos_rank": 27, "position": "QB"},
    "preseason_ranked": {"preseason_ranked": True, "position": "RB"},
    "kdst_preseason_rank": {"kdst_preseason_rank": 9, "position": "K"},
    "weekly_ecr_rank": {"weekly_ecr_rank": 3, "position": "K", "name": "Jordan Smith"},
    "weekly_ecr_listed": {"weekly_ecr_listed": True, "position": "DST"},
}


def manifest() -> list[dict[str, str]]:
    return json.loads(MANIFEST.read_text())["reasons"]


def fantasypros_reason_features() -> set[str]:
    """Registry entries with a reason sentence whose source or formula is FantasyPros' rankings
    (fact_ranking / fact_ranking_kdst)."""
    out = set()
    for e in rg.REGISTRY.values():
        if not (e.reason_template or e.reason_if_false):
            continue
        blob = f"{e.source} {e.formula}".lower()
        if "fantasypros" in blob or "fact_ranking" in blob:
            out.add(e.name)
    return out


def test_every_fantasypros_reason_is_listed() -> None:
    ids = {r["id"] for r in manifest()}
    assert ids == fantasypros_reason_features()
    assert ids == set(ROWS)


def test_templates_match_the_registry() -> None:
    for r in manifest():
        e = rg.get(r["id"])
        assert r["template"] == e.reason_template, r["id"]
        assert e.reason_if_false is None, r["id"]  # a "no" sentence would need its own template


def test_examples_are_what_the_reasons_code_writes() -> None:
    for r in manifest():
        assert rs.phrase(r["id"], ROWS[r["id"]]) == r["example"], r["id"]
