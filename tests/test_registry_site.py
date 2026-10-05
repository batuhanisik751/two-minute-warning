"""The site's tooltip terms live in the registry only (T1, 2026-10-04): the terms that were in
web/lib/site-terms.ts are registry entries (moved word for word, checked once against the old
file), the metrics the pages show have beginner entries, they all publish in the glossary, the
web's fallback (web/lib/glossary-fallback.json, read when the published table lacks a term) is
generated from the registry and in sync, and every term name the web asks <Term> for is a
registry entry."""

import json
import re
from pathlib import Path

from twm import registry as rg
from twm.publish import collect

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
FALLBACK = WEB / "lib" / "glossary-fallback.json"

# the 32 terms that were web/lib/site-terms.ts (their texts now come from the glossary table)
MOVED = (
    "chance", "model_probability", "priority", "list_kind", "precision_at_10",
    "rank_bucket_hit_rate", "interval", "calibration", "walk_forward", "flex", "listed_position",
    "stream_chance", "stream_pool", "garbage_time_view", "current_franchise", "player_season",
    "stability_interval", "decisions_graded", "clear_call", "toss_up", "wrong_call", "clock_case",
    "against_convention", "hot_seat_estimate", "hot_seat_let_go", "hot_seat_driver",
    "board_cliff_chance", "board_missed_chance", "board_ecr", "board_kind", "board_disagree",
    "wp_points",
)  # fmt: skip
# the metrics the audit found on the site without a tooltip
METRICS = ("roc_auc", "pr_auc", "brier", "log_loss", "mae", "ppg", "games")


def test_the_site_terms_are_registry_entries_and_publish():
    assert len(set(MOVED)) == 32
    published = set(collect.glossary().get_column("name").to_list())
    for name in (*MOVED, *METRICS):
        e = rg.get(name)
        assert e.title and e.explanation.strip() and e.formula.strip(), name
        assert name in published, name
    assert {rg.get(n).title for n in ("wp_points", "toss_up")} == {"WP points", "Toss-up"}


def test_no_hand_edited_copy_of_the_texts_in_the_web():
    assert not (WEB / "lib" / "site-terms.ts").exists()
    lib = "\n".join(p.read_text() for p in (WEB / "lib").rglob("*.ts"))
    assert "SITE_TERMS" not in lib


def test_metric_entries_speak_to_a_beginner():
    """What it measures, which direction is better, a one-line example; no league numbers."""
    for name in METRICS:
        e = rg.get(name)
        assert e.kind == "metric", name
        assert re.search(
            r"\b(higher|lower|more)\b.{0,40}\b(better|trustworthy)\b", e.explanation, re.I
        ), name
        assert "Example:" in e.explanation, name
        assert "-team" not in e.explanation, name


def test_every_term_the_web_asks_for_is_in_the_registry():
    """Literal <Term name="..."> in web/app and web/components (dynamic names are checked by
    the smoke tests)."""
    names: set[str] = set()
    for folder in ("app", "components"):
        for p in (WEB / folder).rglob("*.tsx"):
            names |= set(re.findall(r'<Term\s+name="([a-z0-9_]+)"', p.read_text()))
    assert len(names) > 80
    missing = sorted(n for n in names if n not in rg.REGISTRY)
    assert not missing, f"<Term> names without a registry entry: {missing}"


def test_web_fallback_is_generated_and_in_sync():
    committed = FALLBACK.read_text()
    assert committed == rg.glossary_fallback_json(), "run `uv run twm glossary --write`"
    terms = json.loads(committed)["terms"]
    assert set(terms) == set(collect.glossary().get_column("name").to_list())
    assert set(terms[MOVED[0]]) == {"title", "explanation", "formula"}
    # no reason sentence (the FantasyPros-quoting ones are hidden on the public site)
    for e in rg.REGISTRY.values():
        for text in (e.reason_template, e.reason_if_false):
            assert text is None or json.dumps(text)[1:-1] not in committed, e.name
