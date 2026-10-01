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
era; G1b adds `drive_value` and `z_margin`). Since G1b: the smooth hand-over model of the next
section. See reports/decisions/wp_backtest.md and notebooks/03_wp_model.ipynb. The sub-models
below turn a fourth-down choice into resulting states; G1's `wp()` prices each state.

## WP smoothness (G1b: `wp_smooth.py`)

A grade is a difference between the WPs of hypothetical states, so the WP model must also be
locally sensible. Five checks on a fixed grid of synthetic states (neutral site, 1st and 10,
all timeouts; WP points, 0-100), defined and given limits BEFORE any fix was tried:

| Metric | What | Limit | Why |
|---|---|---|---|
| `score_step_h1` | largest WP change for ONE point of score, score -14..+14, at Q1 10:00, Q2 15:00, Q2 5:00 x own 25 / midfield / opp 30 x spread -3.5 / 0 / +3.5 x either team receiving the 2nd-half kickoff | 6 | a random-walk model of the margin (sd 13.5 points per game) gives at most 3-4 near a tie; nflfastR's vegas_wp reads 3.8 (wp 5.4); G1 had 9-24 |
| `score_step_h2` | the same at Q3 13:20, Q3 5:00, Q4 15:00 | 8 | random walk: up to about 6 at the start of Q4; vegas_wp 7.2 (wp 9.0); G1 9-21 |
| `curvature` | largest \|second difference\| of WP along the same score lines | 4 | an isolated step makes it about as large as the step; smooth curves stay under 1, key numbers (3, 7) add a little; vegas_wp 2.3; G1 8-24 |
| `halftime_possession` | largest \|WP with the ball at its own 42 (25) minus WP when the opponent has it at ITS own 42 (25)\|, 1/5/10 s before halftime, score -7..+7, spread 0 / 3.5, both kickoff owners | 3 | outside field-goal range nothing much can happen (football: about 0-1); vegas_wp reads 3.8 (mean 1.6), wp 7.6; G1 7-25 |
| `monotone_violations` | grid pairs where WP falls by more than 0.1 as the lead grows by one point or the ball moves one yard closer | 0 | football; G1's monotone constraints already give 0 |

`yard_step` (largest change for one yard, Q1/Q2/Q3, score -7/0/+7) and `possession_2min` (mean
value of the ball at its own 42 / 25, 30-120 s before halftime, tied; added after the first
regrade, see below) are reported without a limit. What the ball was worth in real games in that
window (logistic regression on 1999-2005 first-half plays at the own 25-50): +1.0 WP points
(95% -2.8 to +4.9).
**References**: nflfastR's model cannot be run offline on synthetic states, so its stored `wp`
/ `vegas_wp` are read off real plays 2006-2025 near each grid line (a local regression of
logit WP on the score as a factor and the clock, yardline, spread, home, timeouts as
deviations; score levels with at least 100 nearby plays). They are references only (fit
partly in-sample) and approximate.

**Choosing the fix** (rule fixed before any candidate was fitted): every candidate runs the
walk-forward folds of the VALIDATION seasons 2004 and 2005 (trained on 1999-2003 / 1999-2004,
never a test or graded season); among the candidates whose two fold models meet every limit,
the lowest pooled 2004-2005 log loss wins. If none meets them all, the smallest worst
metric/limit ratio wins (then log loss). Each fold's smoothness is reported (wp_backtest.md;
candidates and their numbers: `uv run twm decisions wp-select`).

**What was chosen (G1b)**: `spline_sym_late_hand_over`. Up to 15:00 of the fourth quarter the
WP is a logistic regression on an ODD cubic spline of `z_margin` (the lead plus `drive_value`,
the net points the ball is expected to add before halftime, plus the remaining spread, in
standard deviations of what can still happen) with no intercept, terms that change sign when the other team has the ball, and
possession terms that vanish as the half ends: so the first three quarters are smooth and,
seconds before halftime, the ball outside field-goal range is worth nothing. From Q4 15:00 to
10:00 it hands over linearly to one monotone LightGBM fit on every play (and uses it alone after
10:00 and in overtime), where real score thresholds (a tie, a field goal, a touchdown) matter.
The limits do not cover the last 15 minutes by design (the grid stops at Q4 15:00).
Rounds of candidates, all judged on 2004-2005 only: (1) G1, bagging, linear trees, the drive
features, a spline model, blends and boosting: all broke the halftime-possession limit, most
also the score steps; (2) the possession-symmetric spline (`spline_sym`, `spline_sym_late`)
met every limit; (3) added after a first regrade of 2006 (a graded season) with
`spline_sym_late` left no clear two-point decision and the 2005 validation season showed the
spline worst in the last minutes: two late-game variants; (4) a regrade with the hand-over then
showed many more clear 'go' calls in the last 2:00 of the first half: the first `drive_value`
(the drive's points minus the other team's next drive, assumed to start 104 s later) valued the
ball at the own 42 two minutes before halftime at 14-18 WP points, against about +1 (-3 to +5)
in real 1999-2005 games. `drive_value` was redefined as the measured NET points until halftime
(`HALF_VALUE_TABLE`, 1999-2005) and every candidate was rerun. The rule picks the hand-over
again (pooled 2004-2005 log loss 0.436326; G1 0.436333; `spline_sym_late_trees` 0.436480;
`spline_sym_late` 0.436517: differences of noise size), and every fold 2006-2026 was refitted
with it (earlier full refits with the first `drive_value` were replaced).
**Calibration**: isotonic is kept only when it helps on the validation season AND the
calibrated model still meets the limits (a step-function calibrator re-creates steps; added in
G1b after the 2008 fold kept one and broke them, from model properties only).

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
  (+7, extra point assumed good) the other team's 1st and 10 at its own 25 (G3's grades use
  the measured kickoff spot instead, below). The clock moves by the training punts' median
  snap-to-snap time (9 s).

## Extra points and two-point tries (`tries.py`)

- **PAT rate** for season S: made / tried extra points in the last 5 seasons before S under
  S's rule (from 2015 the kick is snapped from the 15). For 2015 itself (no earlier season
  under the new rule) the make rate of 31-35-yard field goals (the new extra point is a
  33-yard kick) in the last 5 seasons.
- **Two-point rate** for season S: converted / tried two-point tries in the last 5 seasons.
- Intervals: Jeffreys 95%. `no_play` tries are left out (the try is replayed).

## Fourth-down grades (G3: `grade_inputs.py`, `grade.py`)

`uv run twm decisions grade --season S` grades one season (default: the current season; one
season per command, ~5 s each); `--report-only` writes reports/decisions/fourth_downs.md + .csv.

- **Rows**: every `fact_play` row with `down` = 4 (regular season and playoffs, 2006 on).
- **Exclusions**, applied in this order; the first rule that applies is recorded in
  `exclusion` (2006-2025 counts; 83,462 fourth-down rows):
  1. `not_a_snap` (135): no play type a decision can have (penalty-only rows).
  2. `penalty_no_play` (4,159): the snap did not count (`play_type` = `no_play`): every
     pre-snap penalty (delay of game, false start, neutral zone, offside ...) and every
     penalty that wiped the play out. The replayed fourth down, if any, is graded instead.
  3. `kneel_or_spike` (70): end-of-half kneels and spikes.
  4. `aborted_snap` (174): a fumbled snap (`aborted_play`): the intended play is unknown.
  5. `end_of_half` (1,173): at most `decisions.end_of_half_seconds` (10) seconds left in the
     half: the half's last snap (end-of-half desperation: a kick or a heave; punts are rare
     under 7 s: 20 of 354 fourth downs at 4-6 s), nothing after the play is a real state.
  6. `missing_state` (0): a state the models cannot score (missing timeouts, spread,
     opening kickoff, kickoff spot) or no head coach to credit.
  77,751 rows are graded (34,685 clear, 43,066 toss-ups); 2026 weeks 1-3: 634 (301 clear).
- **Chosen option**: `pass` or `run` = go (fake punts and fake field goals are pass or run
  plays), `field_goal`, `punt`.
- **Options** (all from the offense's view, priced by G1's `wp()` with season S's fold models):
  - *go*: P(convert) x E[WP after a conversion] + (1 - P) x E[WP after a failure], over G2's
    ball-spot tables (the offense's first down at its new spot or a touchdown; the opponent's
    first down at its spot or its touchdown). P(convert) is Platt-recalibrated when kept (below).
  - *field goal*: P(make) x WP(+3, opponent's 1st and 10 at the kickoff spot) + (1 - P(make))
    x WP(opponent's 1st and 10 at min(80, 92 - yardline_100)). Exists when the distance is at
    most `fg_max_distance` = the longest field goal MADE in the seasons before S.
  - *punt*: G2's punt result distribution, each result priced by `wp()`. Exists from
    `yardline_100` >= `punt_min_yardline` = the `decisions.punt_range_quantile` (0.1%)
    quantile of punting yardlines in the seasons before S (31-32: punts from closer are
    single events).
  - The option the offense really chose always exists, in range or not.
- **Scores inside an option**: a touchdown counts 7 (extra point assumed good, as G2's punt);
  after any score the other team has a 1st and 10 at the **kickoff spot**: the mean start of
  the receiving team's first snap after the same season's kickoffs in EARLIER weeks (at least
  `decisions.kickoff_min_kicks` = 100 of them; onside kicks, return touchdowns and kicks whose
  next snap is the kicking team's left out), else the whole previous season's, rounded to the
  yard. Measured, never typed: 74-75 (own 25-26) through 2023, about 70 from 2024.
- **Clock**: a go moves the clock by the conversion model's median snap-to-snap time; a score
  plus its kickoff by the median seconds from a made field goal's snap to the next snap (5
  seasons before S); a missed field goal by the same median after misses; a punt by the punt
  model's. Timeouts do not change.
- **Recommendation** = the existing option with the highest WP (ties: go, then field goal, then
  punt). **WP lost** = WP(best) - WP(chosen). **Clear** (graded) when WP(best) - WP(second
  best) > `decisions.toss_up_margin` (0.015 = 1.5 WP points); otherwise **toss-up** (counted,
  never graded); `one_option` if nothing else existed.
- **Fourth-down Platt recalibration** of P(convert) (reviewer decision on G2's 4th-and-7+
  under-prediction): logistic regression of the result on logit(P) over the conversion
  model's OUT-OF-SAMPLE fourth downs (each season scored by its own fold) of seasons S-1-n ..
  S-2 (n = `decisions.platt_seasons` = 3), checked on S-1 by log loss; kept only when it beats
  the raw model there, then refit on S-n .. S-1. Never reads season S. Kept for 9 of the 18
  seasons 2008-2025 it could be checked for, not for 2026 (the report lists each).

## Two-point decisions (G3)

- **Rows**: every try after a touchdown (`extra_point_attempt` or `two_point_attempt`; a fake
  or a run from kick formation is "two_point"). Excluded: `penalty_no_play` (40, 2006-2025;
  the try is replayed), `aborted_snap` (20), `missing_state` (0). 27,302 graded (5,349 clear).
- **State**: the scoring team at the try (the score already counts the six points), then the
  opponent's 1st and 10 at the kickoff spot, `kick_runoff` (the median seconds of the same
  kickoffs) later.
- WP(kick) = p_PAT x WP(+1) + (1 - p_PAT) x WP(+0); WP(two) = p_2pt x WP(+2) + (1 - p_2pt) x
  WP(+0), with season S's point-in-time rates (G2 `tries_rates`: PAT within the rule era,
  2-pt over the last 5 seasons). Defensive two-point returns are ignored. Same margin, WP lost
  and clear / toss-up rule.

## Stored inputs and reproducibility (G3)

One row per candidate decision in `data/decisions/graded/fourth_downs_<S>.parquet` and
`two_point_<S>.parquet` (gitignored), excluded rows included (with their rule), plus
`season_<S>.json` (season-level inputs, counts). Each graded row stores the play key, the
context (week, teams, coaches, clock, desc, what happened), the full state (G1's WP inputs,
goal to go, the field-goal conditions), every non-model input (kickoff spot and runoff, the
two option ranges, the field-goal runoffs, the Platt parameters, the try rates, the margin),
the version of each of the five models and every output (P(convert) raw and used, P(make),
each option's WP and its parts, WP before the snap, best, second best, gap, WP lost, grade).
`grade.regrade` recomputes stored rows from those columns alone (models loaded by version
from models/decisions/) and `grade.verify` checks the outputs are bit-identical (expected
values are exact `math.fsum` sums, so a row graded alone equals the same row graded in its
season). The report regrades 200 stored rows per season and kind on every run.

## Attribution and aggregates (G3: `coach.py`)

- A decision is credited to the head coach of the team with the ball in that game:
  `fact_game.home_coach` when it is the home team, else `away_coach` (verified: `fact_play`
  carries identical names on all 963,649 rows 2006-2026; interim coaches get their games).
- Per coach-season and coach-week (one game): clear fourth downs and tries, toss-ups, wrong
  clear calls, WP lost (clear only), **aggressiveness** = the go rate when going for it was
  clearly best, WP lost per game (fourth downs + tries); the worst calls with context; the
  leaderboard (coaches with at least `decisions.leaderboard_min_games` = 8 games, least WP
  lost per game first). League per season: the real go rate vs the recommended go rate.

## The WP model's steps (found in G3, fixed in G1b)

G3 found G1's LightGBM uneven where grades look: one point of score worth 6-19 WP points in
some first-quarter states (football: 1-3) and, one second before halftime, the ball worth -3 to
+20 WP points (football: about 0). Many clear two-point "mistakes" were first-quarter extra
points after a touchdown made the lead 6, and going for it was over-valued late in the first
half. G1b replaced the model (section "WP smoothness") and regraded every season from the
stored inputs; reports/decisions/fourth_downs.md section 6 shows what changed.

## Not graded (yet)

Clock management (G4).

## Honesty note

The option to train the conversion and field-goal models on only the last 10 or 5 seasons was added after a first walk-forward backtest had shown recent seasons under-predicted. Each fold still chooses its window on its validation season (S-1) only, but because the option itself was suggested by test-season results, the published 2006-2025 numbers for those two models are slightly optimistic. The 2026 season is their first clean test.

G1b (the smoothed WP model): the smoothness limits were fixed before any fix was tried, and every candidate was chosen on the 2004-05 validation seasons only; but two later candidate rounds (the late-game hand-over and the redefined drive value) were prompted by looking at regraded seasons, and the pooled test log loss of a first refit was seen before them. The 2006-2025 WP and grading numbers are therefore slightly optimistic; 2026 is their first clean test.
