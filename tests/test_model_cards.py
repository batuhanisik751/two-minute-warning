"""Model cards (spec P3 item 5, step I4a): every pin of `config/production_models.yaml` has a
card in `docs/model_cards/` that names its pin id and approved date; every card has the same
headings; and every number in a card is copied verbatim from the file it cites.

Citation rule (simple and strict): a line `Source: `path`` (optionally followed by quoted
"anchors", each of which must appear verbatim in that file) opens a cited block that runs to
the next Source line or the next `#`/`##` heading. Every number outside the Source lines must
sit in a cited block and appear in the cited file as the same token (no rounding, no partial
match: 47.8 does not match 47.81, 638 does not match 91,638). In the Evaluation and Calibration
sections the cited file must be a report under `reports/`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from twm.config import ROOT

CARDS_DIR = ROOT / "docs" / "model_cards"
CARD_OF_PIN = {
    "waiver_radar": "waiver_radar.md",
    "streamer_k": "streamer_k.md",
    "streamer_dst": "streamer_dst.md",
    "regression_watch": "regression_watch.md",
    "decisions": "decisions_wp.md",
    "hot_seat": "hot_seat.md",
    "board": "board_cliff.md",
    "questionable": "questionable.md",  # feature #1: Questionable outcomes
    "startsit": "startsit.md",  # feature #2: start/sit odds (local only)
}
HEADINGS = [
    "Purpose and intended use",
    "Inputs and point-in-time rules",
    "Model and training",
    "Evaluation",
    "Calibration",
    "Known limits and failure cases",
    "Owner decisions that shaped it",
    "Reproducibility",
    "Ethical notes",
]
REPORT_ONLY = {"Evaluation", "Calibration"}
NUMBER = re.compile(r"(?<![\w.])(?<!\d,)\.?\d+(?:[.,]\d+)*(?!\w)")
SOURCE = re.compile(r"^Source: `([^`]+)`(.*)$")
ANCHOR = re.compile(r'"([^"]+)"')


def _pins() -> dict:
    return yaml.safe_load((ROOT / "config" / "production_models.yaml").read_text())


def _in_text(token: str, text: str) -> bool:
    """``token`` occurs in ``text`` as a whole number (not inside a longer one)."""
    pat = rf"(?<![\w.])(?<!\d,){re.escape(token)}(?!\w|[.,]\d)"
    return re.search(pat, text) is not None


def citation_errors(card: str) -> list[str]:
    """Every problem with the numbers and Source lines of one card's text (empty = clean)."""
    errors: list[str] = []
    section, source, text = "", None, ""
    seen: set[str] = set()
    fence = False
    for no, line in enumerate(card.splitlines(), 1):
        if line.startswith("```"):
            fence = not fence
        heading = re.match(r"^(#{1,2}) (.+)$", line) if not fence else None
        if heading:
            section, source, text = heading.group(2).strip(), None, ""
            seen.add(section)
            continue
        m = SOURCE.match(line)
        if m:
            source, path = m.group(1), ROOT / m.group(1)
            if not path.is_file():
                errors.append(f"line {no}: cited file {source} does not exist")
                source, text = None, ""
                continue
            text = path.read_text()
            if section in REPORT_ONLY and not source.startswith("reports/"):
                errors.append(f"line {no}: {section} must cite a report under reports/")
            for anchor in ANCHOR.findall(m.group(2)):
                if anchor not in text:
                    errors.append(f"line {no}: anchor {anchor!r} not in {source}")
            continue
        for token in NUMBER.findall(line):
            if source is None:
                errors.append(f"line {no}: {token} has no Source line ({section})")
            elif not _in_text(token, text):
                errors.append(f"line {no}: {token} not in {source}")
    evaluation = card.split("\n## Evaluation\n", 1)[-1].split("\n## ", 1)[0]
    if "Evaluation" in seen and "\nSource: `reports/" not in evaluation:
        errors.append("Evaluation cites no report")
    return errors


@pytest.mark.parametrize("key", sorted(CARD_OF_PIN))
def test_card_names_its_pin(key: str) -> None:
    pin = _pins()[key]
    card = (CARDS_DIR / CARD_OF_PIN[key]).read_text()
    assert f"`{pin['model_version']}`" in card, f"{CARD_OF_PIN[key]} misses its pin id"
    assert pin["approved"] in card, f"{CARD_OF_PIN[key]} misses the approved date"
    assert f"`{key}`" in card, f"{CARD_OF_PIN[key]} misses its pin key"
    if "xfp" in pin:
        assert f"`{pin['xfp']['version']}`" in card


def test_every_pin_has_a_card_and_every_card_a_pin() -> None:
    assert set(_pins()) == set(CARD_OF_PIN), "a pin without a card (or a card without a pin)"
    cards = {p.name for p in CARDS_DIR.glob("*.md")} - {"README.md"}
    assert cards == set(CARD_OF_PIN.values())
    index = (CARDS_DIR / "README.md").read_text()
    for name in cards:
        assert f"({name})" in index, f"README.md does not link {name}"


@pytest.mark.parametrize("name", sorted(CARD_OF_PIN.values()))
def test_card_headings(name: str) -> None:
    lines = (CARDS_DIR / name).read_text().splitlines()
    assert lines[0].startswith("# Model card: ")
    assert [h[3:] for h in lines if h.startswith("## ")] == HEADINGS


@pytest.mark.parametrize("name", ["README.md", *sorted(CARD_OF_PIN.values())])
def test_every_number_is_copied_from_its_source(name: str) -> None:
    assert citation_errors((CARDS_DIR / name).read_text()) == []


def test_checker_is_strict(tmp_path: Path) -> None:
    src = "reports/waiver_radar/evaluation.md"
    ok = f'## Evaluation\nSource: `{src}`, "Headlines"\nPrecision 47.8% (46.2-49.5).\n'
    assert citation_errors(ok) == []
    assert citation_errors(ok.replace("47.8%", "47.80%"))  # not verbatim
    assert citation_errors(ok.replace("47.8%", "47.85%"))  # wrong number
    assert citation_errors(ok.replace("Headlines", "Headlinez"))  # anchor missing
    assert citation_errors(ok.replace(src, "docs/waiver_radar.md"))  # not a report
    assert citation_errors("## Evaluation\nPrecision 47.8%.\n")  # uncited
    assert citation_errors(ok + "## Calibration\nBrier 0.0736\n")  # source ends at ##
    assert citation_errors(f"## Evaluation\nSource: `{src}`\n91,639 rows\n")
    assert not _in_text("638", "91,638 rows") and not _in_text("47.8", "47.81")
    assert _in_text(".120", "Brier .120 vs .122") and not _in_text(".12", "Brier .120")
