# Decision Report Card: definitions

What the Decision Report Card (PROJECT_SPEC 8.4) measures, how each number is built, and what
is left out. Started in G2 with the inputs of the fourth-down grades; G3 adds the grades
themselves (WP lost, toss-ups, two-point decisions) and G4 the clock-management metrics.
Every model below is trained **walk-forward**: the number used for season S comes from
seasons before S only (spec 6.2). Backtest: `uv run twm decisions submodels-backtest`
(report: reports/decisions/submodels.md + .csv; code: src/twm/modules/decisions/).

## Win probability (G1)

`own_wp`: the chance that the team with the ball wins, from the state before the snap (score,
clock, down, distance, field position, timeouts, second-half kickoff, home, closing spread,
era). See reports/decisions/wp_backtest.md and notebooks/03_wp_model.ipynb. The sub-models
below turn a fourth-down choice into resulting states; G1's `wp()` prices each state.

## Go for it: P(convert) (`conversion.py`)

- **Definition**: the probability that a third- or fourth-down pass or run gains a first down
  or scores the offense's touchdown, with the offense keeping the ball.
- **Rows**: third- and fourth-down `pass` and `run` plays (third downs add sample; the
  `is_fourth_down` flag separates them). Fake punts and fake field goals are pass/run plays,
  so they are "go" rows.
- **Label** `converted`: `first_down` = 1 or the offense's touchdown, and no interception or
  lost fumble. A defensive penalty that gives a first down on a play that counts is a
  conversion.
- **Inputs**: yards to go, yards to the end zone, goal to go, fourth down, era flags; the
  score difference, clock and closing spread only when the validation season says they help
  (they did in every fold 2006-2025).
- **Training window**: all earlier seasons, or only the last 10 or 5, whichever predicts the
  validation season (the season before S) best: offenses keep getting better.
- **Excluded**: `no_play` rows (a penalty wiped the snap out; the down is replayed and the
  play call is unknown), kneels, spikes, plays with a missing state.
- **Where the ball ends up** (for WP(success) and WP(failure)): the training seasons' empirical
  distribution of the next snap, by distance band x field zone (cells under 30 plays borrow
  their zone, then everything). After a conversion: the offense's new first-down spot or a
  touchdown. After a FOURTH-down failure: the opponent's first-down spot (turnover on downs at
  the spot, or wherever a turnover was returned) or the opponent's touchdown.

## Field goal: P(make) (`fieldgoal.py`)

- **Definition**: the probability that a field-goal attempt from this spot, in this game's
  conditions, is good.
- **Rows**: `field_goal` plays (fakes are go rows; `no_play` rows dropped). **Label**
  `fg_made`: `field_goal_result` = made (missed and blocked = 0).
- **Inputs** (all known when the coach decides): `fg_distance` = yardline + 18 (end zone + the
  hold), indoors (dome or closed roof), temperature, wind, grass vs turf, era flags.
  Temperature and wind come from the schedule; when an outdoor game lacks them they are read
  from the play-by-play weather text; still missing -> `weather_missing` = 1 and the value is
  the median of the training seasons' outdoor games. Indoors = 70 F, no wind.
- **Training window**: all earlier seasons or the last 10 or 5 (validated; kickers improve).
- **After a miss**: the opponent's first down at the spot of the kick, or at its own 20 when
  that spot is inside the 20: opponent yardline_100 = min(80, 92 - yardline_100). Blocked
  kicks (about 1.5% of tries) use the same rule although some are returned.

## Punt: where the receiving team starts (`punt.py`)

- **Definition**: for a punt from yardline p, the distribution of results: the receiving
  team's first-down spot (touchbacks, fair catches, returns and penalties on the return are
  whatever really happened), the kicking team keeping the ball (muffs, blocks it recovered,
  penalties that gave it a first down), a return touchdown, or a kicking-team touchdown.
- **Rows**: `punt` plays (fake punts are go rows; `no_play` rows dropped). The result is read
  from the next snap of the same game and half; punts after which the half or game ended are
  left out (170 of 65,976 punts 1999-2025).
- **Smoothing, by era**: the results of training punts from nearby yardlines, weighted by a
  Gaussian kernel (bandwidth 1.5, 3 or 5 yards), from the last 3 or 6 training seasons or all
  of them; the pair is chosen on the validation season by the mean log score of the observed
  result (5-yard bins).
- **Expected WP** (`punt_expected_wp`): the kicking team's WP averaged over the distribution,
  each result priced by G1's `wp()`: the receiving team's 1st and 10 at its spot (the kicking
  team's WP is 1 minus it), the kicking team's 1st and 10 at its spot, or after a touchdown
  (+7, extra point assumed good) the other team's 1st and 10 at its own 25. The clock moves
  by the training punts' median snap-to-snap time (9 s).

## Extra points and two-point tries (`tries.py`)

- **PAT rate** for season S: made / tried extra points in the last 5 seasons before S under
  S's rule (from 2015 the kick is snapped from the 15). For 2015 itself (no earlier season
  under the new rule) the make rate of 31-35-yard field goals (the new extra point is a
  33-yard kick) in the last 5 seasons.
- **Two-point rate** for season S: converted / tried two-point tries in the last 5 seasons.
- Intervals: Jeffreys 95%. `no_play` tries are left out (the try is replayed).

## Not graded (yet)

The grades themselves (recommended option = highest WP, WP lost, the toss-up margin, the
exclusions of end-of-half kneels and plays with pre-snap penalties, head-coach attribution)
are defined in G3; clock management in G4. Nothing here is a grade of a coach yet.

## Honesty note

The option to train the conversion and field-goal models on only the last 10 or 5 seasons was added after a first walk-forward backtest had shown recent seasons under-predicted. Each fold still chooses its window on its validation season (S-1) only, but because the option itself was suggested by test-season results, the published 2006-2025 numbers for those two models are slightly optimistic. The 2026 season is their first clean test.
