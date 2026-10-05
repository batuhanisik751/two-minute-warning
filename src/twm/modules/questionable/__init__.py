"""Questionable outcomes (feature #1): "my player is listed Questionable: will he play, and if
he plays, does he score like usual?" A transparent lookup table built from 2016-onward injury
reports (docs/questionable.md): no machine learning, every number is a counted rate.

- :mod:`.history`: the history rows (one tagged player-week each) and the played rows the
  "if he plays" comparison reads;
- :mod:`.table`: the groupings, the shrunk cell rates, the walk-forward backtest and the
  calibration;
- :mod:`.production`: the frozen, sha256-pinned table (artifacts/production_models/questionable/);
- :mod:`.weekly`: the live list, its append-only nightly snapshots and their grading.
"""
