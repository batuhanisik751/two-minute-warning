"""Playoff planner (feature #6): "which of my players have easy or hard matchups in my fantasy
playoff weeks 15, 16 and 17, and how much does that even matter?" (docs/playoff_planner.md).

Matchup ratings are ratios of counted sums (points a defense allowed to a position per game over
the league's average), shrunk toward 1.0; the walk-forward backtest measures how much of a
rating survives to weeks 15-17. Nothing here is machine learning.

- :mod:`.history`: the warehouse rows (player-games with points, team-games);
- :mod:`.ratings`: the matchup ratings (raw, shrunk, schedule-adjusted) as of a week;
- :mod:`.backtest`: the walk-forward backtest, the fixed selection rule, effect sizes, stability;
- :mod:`.production`: the frozen, sha256-pinned spec (artifacts/production_models/playoff_planner/);
- :mod:`.weekly`: the live grid, its append-only snapshots and their grading;
- :mod:`.league`: the My League report section.
"""
