"""Teammate out (feature #5): "my team's RB1 / WR1 / TE1 is out this week: which teammate gets
his work, and how much?" A transparent allocation table from the 2013-onward event study
(docs/teammate_out.md): no machine learning, every number is a ratio of counted sums.

- :mod:`.history`: the event study's rows (starters, the games they sat, the baselines);
- :mod:`.table`: the allocation cells, the candidates' predictions, the walk-forward backtest,
  the 80% ranges;
- :mod:`.production`: the frozen, sha256-pinned table (artifacts/production_models/teammate_out/);
- :mod:`.weekly`: the live list, its append-only nightly snapshots and their grading;
- :mod:`.league`: the My League report section.
"""
