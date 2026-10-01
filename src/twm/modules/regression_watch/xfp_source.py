"""Regression Watch's expected-points source: ONE switch, read from the pin (step H6-b2).

Since the owner's decision of 2026-10-01 the approved configuration uses the **own walk-forward
xFP** (:mod:`.own_xfp`): a play of season S gets its expectations from models trained on the
seasons before S only (PROJECT_SPEC 6.3). The pin of ``regression_watch``
(``config/production_models.yaml``) names the source in its ``xfp`` entry:

- ``own``: the live fold (the pinned season, trained on 2006 .. S-1), every model file with its
  sha256; the weekly list scores the season's plays with them and never fits;
- ``ffopportunity``: nflverse's per-play expectations (the source before H6-b2; still there
  for research, e.g. ``twm regression own-xfp`` compares the two).

There is no default: a pin without an ``xfp`` entry is refused (no silent fallback). The
studies and approvals (``twm regression stability|backtest|project|pin|freeze``) take the
pin's source unless ``--xfp`` names one; with ``own`` they read the walk-forward history from
the folds on the owner's Mac (:func:`.own_xfp.history_player_games`).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import polars as pl

OWN = "own"
FFOPPORTUNITY = "ffopportunity"
SOURCES = (OWN, FFOPPORTUNITY)


def check(source: str) -> str:
    if source not in SOURCES:
        raise ValueError(f"the xFP source must be one of {SOURCES}, not {source!r}")
    return source


def pinned_source(path: Path | None = None) -> str:
    """The pinned source (:class:`twm.pins.PinError` when the pin or its ``xfp`` entry is
    missing: never a default)."""
    from twm import pins
    from twm.modules.regression_watch.production import PIN_KEY

    pin = pins.get_pin(PIN_KEY, path)
    if pin.xfp is None:
        raise pins.PinError(f"the pin of {PIN_KEY} names no xFP source (`xfp:`): approve the "
                            "parameters with `uv run twm regression pin --xfp own`")  # fmt: skip
    return pin.xfp.source


def history(
    db: Path | str, last_season: int, source: str,
    *, progress: Callable[[str], None] | None = None,
) -> pl.DataFrame | None:  # fmt: skip
    """The per player-game expected points of the seasons up to ``last_season`` for the frame
    loaders' ``xfp=`` (:func:`.player_week.with_xfp`): None = ffopportunity's (the frame as
    built), else the own walk-forward xFP (each season from its own fold)."""
    if check(source) == FFOPPORTUNITY:
        return None
    from twm.modules.regression_watch import own_xfp as ox

    return ox.history_player_games(db, last_season, progress=progress)
