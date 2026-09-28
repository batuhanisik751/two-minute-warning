"""Regenerate the golden expected outputs (tests/golden/expected/) DELIBERATELY.

Run it only when a change to the pipeline is meant to change what the golden world produces;
then review `git diff tests/golden/expected/` line by line before committing, because this is
the frozen answer the tests compare against:

    uv run python tests/golden/update.py

It builds the warehouse slice from the committed inputs (tests/golden/inputs/, see
make_inputs.py) in a temporary folder, runs every module and writes one file per output.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

if __package__ in (None, ""):  # run as a script: make `tests.golden` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.golden import world  # noqa: E402


def main() -> None:
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="twm-golden-") as tmp:
        outs = world.compute(Path(tmp))
    world.EXPECTED_DIR.mkdir(parents=True, exist_ok=True)
    stale = {p.name for p in world.EXPECTED_DIR.iterdir() if p.is_file()} - set(outs)
    for name in sorted(stale):
        (world.EXPECTED_DIR / name).unlink()
        print(f"removed  {name}")
    for name, text in sorted(outs.items()):
        path = world.EXPECTED_DIR / name
        old = path.read_text() if path.exists() else None
        path.write_text(text)
        state = "new" if old is None else ("same" if old == text else "CHANGED")
        print(f"{state:8s} {name} ({len(text.encode()) / 1024:.0f} KB)")
    total = sum(p.stat().st_size for p in world.EXPECTED_DIR.iterdir())
    print(
        f"expected/: {total / 1024:.0f} KB, inputs/: {world.input_size() / 1024:.0f} KB, "
        f"{time.perf_counter() - t0:.1f} s"
    )


if __name__ == "__main__":
    main()
