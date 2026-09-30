"""The lineup optimizer behind lineup regret (step F3): the best legal lineup from the players a
team had in a week, for any value (actual points, or ESPN's projected points).

Rules (tests/test_league_f3.py):

- **Slots** are the league's own starting slots (``league_settings`` ``slot:*`` keys, ESPN's
  labels). A single-position slot (QB, RB, WR, TE, K, D/ST) takes that position; a FLEX-type slot
  takes the positions of :data:`twm.league.settings_diff.MULTI_SLOTS` (RB/WR/TE, RB/WR, WR/TE,
  OP = QB/RB/WR/TE). Bench (BE) and injured reserve (IR) never start. Any other ESPN label
  (IDP, P, HC, ...) is not modelled: :func:`starting_slots` names it in a note.
- **Eligibility**: a player fits the slots of his ESPN main position, plus the slot ESPN let him
  start in that week (multi-position players: the store does not keep ESPN's eligibleSlots).
- **Search**: exact, a dynamic programme over the players with the slots still open as the state
  (a few hundred states for a normal league), so non-nested FLEX slots are handled too.
- **Objective**, in order: fill as many slots as possible (a slot stays empty only when no
  eligible player is left; a negative D/ST still starts), then the largest total value, then the
  most ``prefer`` players (ties go to the players the owner actually started), then the first
  solution in a fixed order (players by ESPN id, slots in :data:`SLOT_ORDER`): deterministic.
- Values are compared in 1/10,000 points, so float noise never flips a choice.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from twm.league.espn_client import NOT_STARTING
from twm.league.settings_diff import MULTI_SLOTS

SINGLE = ("QB", "RB", "WR", "TE", "K", "D/ST")
SLOT_ORDER = ("QB", "RB", "WR", "TE", "RB/WR", "WR/TE", "RB/WR/TE", "OP", "K", "D/ST")
ELIGIBLE: dict[str, frozenset[str]] = {s: frozenset({s}) for s in SINGLE} | {
    label: frozenset(held) for label, held in MULTI_SLOTS.items()
}
SCALE = 10_000
# a DP entry: ((slots filled, value in 1/SCALE points, preferred players), picks (slot index, key))
_Best = tuple[tuple[int, int, int], tuple[tuple[int, int], ...]]


def starting_slots(slot_counts: Mapping[str, int]) -> tuple[dict[str, int], list[str]]:
    """(the starting slots the optimizer fills, label -> count in :data:`SLOT_ORDER`; notes
    naming the league's slots it cannot model)."""
    notes = [
        f"lineup: {n} ESPN slot(s) {label!r} not modelled (left out of every lineup)"
        for label, n in sorted(slot_counts.items())
        if n and label not in ELIGIBLE and label not in NOT_STARTING
    ]
    slots = {s: int(slot_counts[s]) for s in SLOT_ORDER if int(slot_counts.get(s, 0) or 0) > 0}
    return slots, notes


@dataclass(frozen=True)
class Candidate:
    key: int  # ESPN player id
    position: str  # ESPN main position: QB, RB, WR, TE, K, D/ST
    value: float  # what the lineup maximizes
    extra_slot: str = ""  # a starting slot ESPN let him use beyond his position's
    prefer: bool = False  # tie-break: kept over an equal-valued player

    def fits(self, slot: str) -> bool:
        return self.position in ELIGIBLE.get(slot, frozenset()) or slot == self.extra_slot


@dataclass(frozen=True)
class Lineup:
    picks: tuple[tuple[str, int | None], ...]  # (slot, ESPN id or None = empty), slot order
    value: float  # the sum of the chosen players' values

    @property
    def keys(self) -> frozenset[int]:
        return frozenset(k for _, k in self.picks if k is not None)

    @property
    def empty(self) -> int:
        return sum(1 for _, k in self.picks if k is None)


def optimize(candidates: Iterable[Candidate], slots: Mapping[str, int]) -> Lineup:
    """The best legal lineup of ``candidates`` for ``slots`` (label -> count; the objective and
    tie-breaks of the module docstring). A player appears at most once."""
    labels = [s for s in SLOT_ORDER if slots.get(s, 0) > 0]
    labels += sorted(s for s in slots if s not in SLOT_ORDER and slots[s] > 0)
    players = sorted(candidates, key=lambda c: c.key)
    if len({c.key for c in players}) != len(players):
        raise ValueError("a player is listed twice")
    start = tuple(int(slots[s]) for s in labels)
    best: dict[tuple[int, ...], _Best] = {start: ((0, 0, 0), ())}
    for c in players:
        v, fits = round(c.value * SCALE), [i for i, s in enumerate(labels) if c.fits(s)]
        nxt: dict[tuple[int, ...], _Best] = dict(best)  # benching him keeps every state
        for state, ((filled, total, pref), picks) in best.items():
            for i in fits:
                if state[i] == 0:
                    continue
                new = state[:i] + (state[i] - 1,) + state[i + 1 :]
                cand: _Best = ((filled + 1, total + v, pref + c.prefer), (*picks, (i, c.key)))
                if new not in nxt or cand[0] > nxt[new][0]:
                    nxt[new] = cand
        best = nxt
    _, picks = max(best.values(), key=lambda b: b[0])  # max keeps the first of equal keys
    by_key = {c.key: c for c in players}
    out: list[tuple[str, int | None]] = []
    for i, s in enumerate(labels):
        mine = sorted(k for j, k in picks if j == i)
        out += [(s, k) for k in mine] + [(s, None)] * (int(slots[s]) - len(mine))
    value = round(sum(by_key[k].value for _, k in picks), 6)
    return Lineup(tuple(out), value)
