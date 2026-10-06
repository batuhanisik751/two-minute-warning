"""Lead time vs the crowd (feature #8): how many weeks before most ESPN leagues rostered a player
did the Waiver Radar flag him? docs/lead_time.md has the definitions, the coverage and the results.

- :mod:`.crowd`: the ESPN roster % paths (FantasyPros scrapes in ``fact_ranking``), the waiver
  period of each scrape (``dim_week`` windows), the crowd adds (first crossing of 50% / 25%) and
  the "crowd momentum" baseline;
- :mod:`.flags`: the Radar's flags from the pinned walk-forward backtest, with the priority
  recomputed point-in-time (the same code the published lists use);
- :mod:`.study`: the leads, the shares flagged before / same week / after / never, the reverse
  view (must-adds the crowd never added) and the aggregate-only frames a publish may carry;
- :mod:`.live`: the 2026 tracker from the owner's league syncs (local only).

LICENSE: FantasyPros' per-player roster % never leaves the machine. Only the aggregate frames of
:func:`twm.modules.lead_time.study.build_frames` may be published (checked by
:func:`~twm.modules.lead_time.study.assert_aggregate_only`).
"""

from __future__ import annotations

THRESHOLDS = (50.0, 25.0)  # the crowd "adds" him when his ESPN roster % crosses this
PRIMARY = 50.0
MOMENTUM_POINTS = 10.0  # baseline: a rise of at least this many points in one period
LEVELS = ("must_add", "spec_plus", "listed")  # Radar flag levels (top 25 of a list)
CATEGORIES = ("before", "same", "after", "never")
COMPLETE_SEASONS = (2021, 2022, 2023)  # ESPN % for the whole regular season
PARTIAL_SEASONS = (2020,)  # ESPN % only from 2020-10-16 (waiver period 5)
STUDY_SEASONS = (*PARTIAL_SEASONS, *COMPLETE_SEASONS)  # 2024: no in-season ESPN % at all
MIN_CELL = 5  # published cells with fewer adds or flags carry no statistics
