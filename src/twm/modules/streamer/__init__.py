"""The K and D/ST streamer (PROJECT_SPEC 8.6b, S1): which kicker or team defense to pick up for
next week. ``pool`` (who is probably on waivers at a Tuesday as-of) and ``labels`` (did he finish
as a weekly starter the next week) follow the Waiver Radar's C1/C2 rules, adapted to a one-week
horizon; ``features`` (point-in-time, S1c) and ``dataset`` (pool + features + labels, one row per
as-of and entity) follow C3; docs/streamer.md explains them in plain words.
"""
