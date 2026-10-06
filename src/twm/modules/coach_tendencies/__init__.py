"""Coach tendencies (feature #10): how each head coach's offense plays, for a fantasy player.

Per (coach, team, season), regular season, his team on offense: neutral pass rate, pass rate
over expected, early-down pass rate, neutral pace, no-huddle and shotgun rates, fourth-down go
rates, each with the league average and his percentile that season; a career summary; how much
of each tendency persists from year to year (coach vs team); and the measured link to the
team's pass-catcher fantasy points. Definitions: docs/coach_tendencies.md.

- :mod:`.plays`: which plays each tendency is measured on, and the pace pairs;
- :mod:`.season`: the coach-team-season, team-season and career aggregates;
- :mod:`.persistence`: year-over-year correlations with season-block bootstrap intervals;
- :mod:`.fantasy`: the team-season link to targets and receiving PPR points;
- :mod:`.build`: the deterministic build of the published frames.
"""
