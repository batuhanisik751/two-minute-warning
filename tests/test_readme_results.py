"""README results (spec P3 acceptance "results summary (honest, including failures)", step I6a):
every number in README.md's "Results" table and "What did not work" list is copied from the
model card that its row or bullet links, which in turn cites the report it comes from
(tests/test_model_cards.py checks the cards against the reports).

Rule: in those two sections every table row and every bullet that contains a number links
exactly one card (`docs/model_cards/<card>.md`), and each number appears in that card as the
same token (no rounding, no partial match; the same matcher as the model-card test).
"""

from __future__ import annotations

import re

import pytest

from tests.test_model_cards import CARD_OF_PIN, CARDS_DIR, NUMBER, _in_text
from twm.config import ROOT

SECTIONS = ("Results (backtests, honest)", "What did not work")
CARD_LINK = re.compile(r"\]\(docs/model_cards/([a-z_]+\.md)\)")


def section(readme: str, title: str) -> str:
    """The body of the `## title` section (up to the next `## ` heading)."""
    head = f"\n## {title}\n"
    assert head in readme, f"README.md has no section {title!r}"
    return readme.split(head, 1)[1].split("\n## ", 1)[0]


def items(body: str) -> list[str]:
    """Table rows and bullets (a bullet's indented continuation lines join it); prose lines too."""
    out: list[str] = []
    for line in body.splitlines():
        if line.startswith("  ") and out:
            out[-1] += " " + line.strip()
        elif line.strip():
            out.append(line.strip())
    return out


def item_errors(item: str) -> list[str]:
    """Problems with one row or bullet (empty = clean)."""
    links = CARD_LINK.findall(item)
    text = CARD_LINK.sub("]", item)
    tokens = NUMBER.findall(text)
    if not tokens:
        return []
    if len(set(links)) != 1:
        return [f"{item[:60]!r}: numbers need exactly one card link, found {sorted(set(links))}"]
    card = (CARDS_DIR / links[0]).read_text()
    missing = [t for t in tokens if not _in_text(t, card)]
    return [f"{links[0]}: {t} not in the card ({item[:40]!r})" for t in missing]


@pytest.mark.parametrize("title", SECTIONS)
def test_readme_numbers_come_from_the_cards(title: str) -> None:
    body = section((ROOT / "README.md").read_text(), title)
    errors = [e for it in items(body) for e in item_errors(it)]
    assert errors == []


def test_results_table_has_one_row_per_card() -> None:
    body = section((ROOT / "README.md").read_text(), SECTIONS[0])
    rows = [r for r in items(body) if r.startswith("|") and CARD_LINK.search(r)]
    assert sorted(CARD_LINK.search(r).group(1) for r in rows) == sorted(CARD_OF_PIN.values())


def test_checker_is_strict() -> None:
    row = "| Radar | 47.8% (46.2-49.5) | [c](docs/model_cards/waiver_radar.md) |"
    assert item_errors(row) == []
    assert item_errors(row.replace("47.8%", "47.80%"))  # not verbatim
    assert item_errors(row.replace("47.8%", "48.8%"))  # not in the card
    assert item_errors(row.replace("waiver_radar.md", "streamer_k.md"))  # the wrong card
    assert item_errors("- Radar 47.8% with no card")  # uncited
    assert item_errors(row + " [d](docs/model_cards/hot_seat.md)")  # two cards
    assert item_errors("- no numbers here") == []
    assert items("- a\n  b\n| c |\n") == ["- a b", "| c |"]
