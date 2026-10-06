# Model card: Coach tendencies (`coach_tendencies`)

Source: `config/production_models.yaml`
Pin key `coach_tendencies`, pin id (model_version) `history-3e0284a5594f3899`, `model: history`,
approved 2026-10-06, frozen for season 2026: the completed regular seasons 1999-2025 (913
coach-team seasons, 179 coaches) with their persistence (24 rows) and fantasy link (32 rows).
Every number below is copied from the file named on the `Source:` line above it
(`tests/test_model_cards.py` checks this).

## Purpose and intended use

Source: `docs/coach_tendencies.md`, "Coach tendencies (feature #10)"
How each head coach's offense plays (neutral pass rate, pass rate over expected, pace,
no-huddle, shotgun, fourth-down aggressiveness), season by season with his career line, how
much of each tendency persists from year to year, and the measured link to his pass-catchers'
fantasy points. It answers a beginner's "does this coach's offense throw a lot and play fast?"
**Not intended for:** predicting a single game, or judging a play-caller (the warehouse knows
head coaches, not who calls plays).

## Inputs and point-in-time rules

Source: `docs/coach_tendencies.md`, "Frozen history (C10c)"
- The frozen part is counted rows of COMPLETED regular seasons only (play-by-play with the
  offense's head coach); it never changes once a season is over.
- The season in progress is built from the warehouse on every publish through an as-of view
  (only plays public at the publish clock) and is never frozen.
- The scheduled job's warehouse starts in 2012, so the completed seasons travel frozen: the
  publish reads them from the pin (sha256 checked first) and builds only the seasons after it.

## Model and training

Source: `docs/coach_tendencies.md`, "Definitions"
Nothing is fitted: every tendency is a counted rate (numerator over sample), a league value
and a within-season percentile. The pin freezes each coach-team-season's numerators and
samples, so a career line (all his plays pooled) is recomputed exactly from the frozen and the
newly built seasons. Persistence is a Pearson r with a season-block bootstrap interval.

## Evaluation

Source: `reports/coach_tendencies/report.md`, "## persistence"
Year-over-year persistence, relative to the league (r, its interval, pairs): neutral pass rate
same coach and team 0.473332 (0.416954 to 0.527182), 635 pairs; same coach, new team 0.219189,
60 pairs; new coach, same team 0.221887, 194 pairs. Shotgun 0.726688 / 0.438242 / 0.212605;
no-huddle 0.681691 / 0.545575 / 0.162180; fourth-down go rate 0.377202 / 0.092451 / 0.011408.

Source: `reports/coach_tendencies/report.md`, "## fantasy_link"
Fantasy link (team-seasons, season-relative): PROE with targets per game, same season r
0.710341 (0.666267 to 0.753633), n 416, one SD of PROE (3.987375 points) goes with 2.485435
targets per game; next season r 0.319169. Neutral pass rate same season 0.640807; pace
-0.364973 (a faster offense has more targets).

## Calibration

Not applicable: the module states counted rates and correlations; it predicts no probability.

## Known limits and failure cases

Source: `docs/coach_tendencies.md`, "Limits"
Head coach, not play-caller; a mid-season firing splits a team's season between two coaches;
early-season rows of the season in progress have small samples. Until the yearly re-freeze, a
season completed after the pin joins the career lines (built from the warehouse) but not the
persistence or the fantasy link, which stay the pinned seasons'.

## Owner decisions that shaped it

The owner's rule for history the runner cannot rebuild: freeze it on the Mac, pin it with
sha256 and refuse to publish without it (as for the Decision Report Card's history).

## Reproducibility

Source: `config/production_models.yaml`
`uv run twm coach tendencies-pin` (on the full 1999+ warehouse; refused on a partial one)
writes the snapshot, the spec and `reports/coach_tendencies/`; `uv run twm model check
coach_tendencies` checks every sha256 and row count, the definitions it was frozen with and
that the snapshot reproduces the reports.

## Ethical notes

The tendencies describe public, on-field team behaviour; they say nothing about a coach's job
security or ability beyond what his offense did.
