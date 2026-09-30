"""The K and D/ST streamer (PROJECT_SPEC 8.6b, S1): which kicker or team defense to pick up for
next week. ``pool`` (who is probably on waivers at a Tuesday as-of) and ``labels`` (did he finish
as a weekly starter the next week) follow the Waiver Radar's C1/C2 rules, adapted to a one-week
horizon; ``features`` (point-in-time, S1c) and ``dataset`` (pool + features + labels, one row per
as-of and entity) follow C3; ``models``, ``backtest`` and ``backtest_report`` (S1d) run the
baselines and the Radar's estimators through the shared walk-forward harness, one model per
position; ``production`` (the approved K model and D/ST rule with their pins), ``confidence``,
``reasons`` and ``weekly`` (S2a) make the weekly list like the Radar's C6 list; docs/streamer.md
explains them in plain words.
"""
