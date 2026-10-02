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
  6. `late_game` (4,685; 206-279 per season, 5-6.5% of its fourth downs): at most
     `decisions.late_game.q4_seconds` (120) seconds left in the 4th quarter, or overtime
     (`decisions.late_game.overtime`): **late game, not graded** (G3b, below).
  7. `missing_state` (0): a state the models cannot score (missing timeouts, spread,
     opening kickoff, kickoff spot) or no head coach to credit.
  73,066 rows are graded (28,733 clear, 44,333 toss-ups); 2026 weeks 1-3: 603 (275 clear).
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
  the try is replayed), `aborted_snap` (20), `late_game` (1,409: the 4th quarter's last 2:00
  and overtime, G3b, below), `missing_state` (0). 25,893 graded (540 clear).
- **State**: the scoring team at the try (the score already counts the six points), then the
  opponent's 1st and 10 at the kickoff spot, `kick_runoff` (the median seconds of the same
  kickoffs) later.
- WP(kick) = p_PAT x WP(+1) + (1 - p_PAT) x WP(+0); WP(two) = p_2pt x WP(+2) + (1 - p_2pt) x
  WP(+0), with season S's point-in-time rates (G2 `tries_rates`: PAT within the rule era,
  2-pt over the last 5 seasons). Defensive two-point returns are ignored. Same margin, WP lost
  and clear / toss-up rule.

## The late game is not graded (G3b, `decisions.late_game`)

Fourth downs and two-point tries with at most `decisions.late_game.q4_seconds` (120) seconds
left in the 4th quarter, and every one in overtime (`decisions.late_game.overtime`), are not
graded: they are counted and reported as "late game, not graded" (exclusion `late_game`, after
`end_of_half` in the rule order, so the last 10 s stay `end_of_half`), never priced, never
published as graded decisions and never credited to a coach. 2006-2025: 4,685 fourth downs (reports/decisions/fourth_downs.md totals also include 2026 to date)
(206-279 per season, 10-42 of them in overtime) and 1,409 tries (52-86 per season); per-season
counts in reports/decisions/fourth_downs.md, section 1 (CSV table `late_game`). The first
half's last 2:00 is still graded. The clock-management metrics below are rule-defined, not
WP-option grades, and are unaffected.

Why: those grades are differences between the WP model's values of late-game states, and that
is where the model cannot be trusted yet. The nfl4th benchmark's disagreements concentrate in
the 4th quarter's last 2:00 (the largest go-gain gap of any game phase, -2.19 WP points; nfl4th
says go on 60.8% of those fourth downs, we on 47.3%); G1b's smoothness limits stop at Q4 15:00
by design (from Q4 10:00 and in overtime the WP model is the tree model alone); and a
hand-checked 2026 case (TB, 4th and 11 at the opponent's 20, down 4, 0:22 left: graded 19.8 WP
points lost because the model gave the field goal 34%, though a field goal leaves them down 1
with the opponent getting the ball) showed the failure. The rule stays until the late-game WP
model is shown to be trustworthy there.

**Honesty**: the rule was decided by the reviewer on 2026-10-01 after the hand-checked case and
the benchmark had been seen, i.e. after looking at graded output. It removes grades; it does
not tune any model or any other rule, and every decision outside the window keeps exactly the
grade it had (checked row by row in the regrade).

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
  `fact_game.home_coach` when it is the home team, else `away_coach`. Since H1b these are the
  schedule's coaches corrected by the cited `data/manual/coach_corrections.csv`
  (docs/warehouse.md): interim coaches the schedule misses (2015 MIA/TEN, 2016 LA, 2019 CAR,
  2024 NYJ/NO/CHI, 2025 TEN/NYG) get their games, 2007 ATL's change moves to the right game,
  the 2026 schedule's fired coaches (ARI, ATL, BUF) give way to their successors, and three
  names are fixed. Re-attributing changed only `coach` / `opp_coach` and coach-keyed
  aggregates: 878 decision rows 2006-2026 (782 graded; 2007 12, 2015 222, 2016 34, 2019 37,
  2022 23, 2024 247, 2025 187, 2026 116), every grade, WP and input identical. (`fact_play`
  keeps the uncorrected pbp names; nothing reads them.)
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

## Clock management (G4: `clock_inputs.py`, `clock.py`)

Three limited metrics (spec 8.4 item 4). They were written here, with every threshold fixed in
`config/settings.yaml` (`decisions.clock`), **before any season was graded**; a situation
outside these definitions is never graded. `uv run twm decisions clock --season S` stores each
season's candidates with their inputs under `data/decisions/clock/` (gitignored);
`--report-only` writes reports/decisions/clock.md + .csv. Seasons 2006 on (regular season and
playoffs); each case is credited to the head coach of the team it describes in that game
(`fact_game.home_coach` / `away_coach`, as G3).

**Snaps.** Rows of `fact_play` with a down (scrimmage plays and `no_play` penalty snaps), in
`play_id` order. Timeouts are read from the pre-snap `posteam_timeouts_remaining` /
`defteam_timeouts_remaining` of the snaps (a team used a timeout between two snaps when its
count went down), never from where the timeout rows sit: in the warehouse a timeout row often
comes after the snap it preceded. `T` = `game_seconds_remaining` at a snap.

**Kneel arithmetic** (shared by metrics 1 and 3). From a snap with down `d` and the clock at
`T`, the offense can kneel `n = 5 - d` times (downs `d` to 4); each kneel takes `p` seconds,
and between two kneels the clock runs `g - p` more seconds unless the defense stops it with a
timeout. Against a defense that uses `t` timeouts the kneels exhaust the clock when

    T <= K(d, t) = n * p + max(0, n - 1 - t) * (g - p)

(the last kneel must end the game: a 4th-down kneel with time left turns the ball over).
`p` (`kneel_play`) and `g` (`kneel_cycle`) are measured point-in-time on the
`decisions.clock.runoff_seasons` (5) seasons before S: the median game-clock seconds from a
`qb_kneel` snap in the second or fourth quarter to the same offense's next snap in the same
half, when only the defense's timeouts went down by one in between (`p`, about 2-3 s), or
when neither team's did (`g`, about 38-40 s; 2006-2025 medians). The offense **can run out
the clock unless the team uses its timeouts** when `K(d, t) < T <= K(d, 0)` with `t` >= 1 the
team's timeouts: kneeling ends the game if the team calls none, but not if it calls them all.
The window is the final `decisions.clock.final_window_seconds` (120: after the two-minute
warning, so no automatic stoppage is left) of the fourth quarter.

### 1. Timeouts unused in a lost one-score game (`timeouts_unused`)

A team-game is a **case** when all of these hold:

1. the game ended in regulation (no overtime snap) and the team lost by 1 to
   `decisions.clock.one_score_margin` (8) points;
2. the game's last scrimmage snap belongs to an opponent's drive (the team never got the ball
   back), and that drive has a **run-out snap**: a scrimmage snap in the fourth quarter with
   `T` <= 120 at which the opponent led and could run out the clock unless the team used its
   timeouts (`K(d, t) < T <= K(d, 0)`, `t` >= 1, the team's timeouts at that snap);
3. the team still held at least one timeout at the opponent's last snap of the game.

Value: the timeouts left (1-3). Candidates (conditions 1 and 2) that used every timeout are
counted, not cases. Context: the first run-out snap (clock, down and distance, field position,
score, the team's timeouts, `K(d, 0)` and `K(d, t)`), the last snap, the final score, and
metric 3's counted missed stops and seconds wasted on that drive. A case is a fact (the game
was lost with timeouts in hand), not by itself a mistake: a timeout kept can be worthless when
the opponent converts a first down after the team used the others (0 missed stops in metric 3).

### 2. End-of-half passivity (`half_passivity`)

First half only (the end of regulation is a win-probability question: a leading team that
kneels is right, and a tied one plays for overtime).

1. **Candidate drive**: the team had the ball at the first half's last scrimmage snap and its
   drive's `fixed_drive_result` is `End of half` (no score, no turnover, no punt).
2. **Passive tail**: the longest run of the drive's last snaps that are all kneels
   (`qb_kneel`), designed runs (`play_type` = `run` with `qb_dropback` = 0 and
   `qb_scramble` = 0: no pass was tried) or penalty snaps (`no_play`: the down is replayed)
   during which the team called no timeout (its count at each tail snap equals its count at
   the half's last snap).
3. **Decision snap**: the tail's first kneel or designed run that is a 1st down with
   `half_seconds_remaining` >= `decisions.clock.passivity_min_seconds` (40) and the team
   holding >= `decisions.clock.passivity_min_timeouts` (1) timeout. No such snap: not a candidate (a tail
   of 2nd and 3rd downs only is outside the definition: the EP table is for 1st downs).
4. **EP of attacking** = G1b's `half_value(yardline_100, half_seconds_remaining)` at the
   decision snap: the offense's points minus the other team's from a first-half 1st down to
   halftime, measured on 1999-2005 (before every graded season: point-in-time). Kneeling's EP
   is 0 (nobody scores), so **EP left on the table** = `half_value`. The table averages every
   team that had the ball there, passive ones included, so it understates attacking: a
   conservative reading.
5. **Case** when EP left >= `decisions.clock.passivity_min_ep` = **1.0 point**; otherwise
   counted as `ep_below_threshold`. Why 1.0 (fixed before grading): a third of a field goal
   net of the turnover risk; at least 4.5 standard errors of the table's cells in this
   region (0.13-0.22 points on 1999-2005); and in seconds and yards it means roughly the own
   40 with a minute left or midfield with 40 s, never the usual touchback kneel (own 25: 0.1
   to 0.4 points at 40-90 s). `missing_state` when the WP state cannot be scored.
6. **WP left on the table** = WP(decision snap) - WP(start of the second half), both from
   season S's own WP fold model (G1b). The second half starts with the same score, 3
   timeouts each, the team that receives the second-half kickoff on a 1st and 10 at the
   game's point-in-time kickoff spot (G3's `kick_yardline_100`), `kick_runoff` seconds into
   the half; WP(decision snap) is the model's value of the state as it was (with the ball).

### 3. Late timeouts when trailing: seconds wasted (`timeout_seconds_wasted`)

**Heuristic** (the optimal use it is measured against): when the team trails late and the
opponent can run out the clock unless the team uses its timeouts, the team should stop the
clock with a timeout right after each opponent play that leaves the clock running, until it
has none left. A timeout not used then lets the clock run for nothing: a timeout still held
when the opponent's drive is over can no longer save that drive's seconds.

1. **Intervals**: each opponent scrimmage snap `i` in the fourth quarter with `T_i` <= 120
   whose next snap `i+1` (any snap with a down) is in the same opponent drive, while the team
   trailed by 1 to `one_score_margin` (8) points and held `t` >= 1 timeouts at snap `i`.
2. The play's own seconds `s` = the measured median for its type: `kneel_play` for kneels,
   `play_seconds_run` for runs, `play_seconds_pass` for everything else (the 5 seasons before
   S: snap to the same offense's next snap in the last 120 s of the second or fourth quarter
   when only the defense's timeouts went down by one in between). The clock when the play
   ended is `T_i - s`; the **runoff** = max(0, `T_i - T_{i+1} - s`): the seconds a timeout
   called right after the play would have saved.
3. **Decisive** interval: after the play, the opponent can run out the clock unless the team
   uses its timeouts: `K(d', t - 1) < T_i - s <= K(d', -1)`, `d'` = the down at snap `i+1`.
   After a play one more gap runs before snap `i+1` (the gap a timeout called now stops), so
   the `n' = 5 - d'` remaining kneels have `n'` gaps: `K(d', -1)` = all of them run
   (`K(d', 0) + g - p`); `K(d', t - 1)` = the team stops this gap and `t - 1` later ones.
   *Correction (disclosed)*: the first written version read `K(d', t) < T_i - s <= K(d', 0)`,
   which leaves out that gap and contradicts the sentence above (it calls a timeout decisive
   when the opponent can kneel out anyway). Found by checking one case by hand after a first
   test run on 2024-2026 (2025 week 1, HOU at LA: after a 1st-down kneel at 1:19, 2nd down,
   one timeout: the kneels run out the clock whether or not it is used), before the full
   grading; no threshold changed.
4. **Missed stop**: a decisive interval where the team did not use a timeout (same count at
   `i+1`) and the clock ran: runoff >= `decisions.clock.clock_ran_min_seconds` (10; smaller
   gaps are incompletions, out-of-bounds plays, penalties: the clock was stopped anyway).
5. **Seconds wasted** on an opponent drive = the sum of the runoffs of its first `k` missed
   stops, `k` = the timeouts the team still held at the drive's last snap (those it never used
   against the drive; stopping later intervals instead saves the same kind of seconds, so only
   timeouts never used are counted). A **case** is a team-game with seconds wasted > 0 (the
   sum over the opponent's drives in the window). Counted ex ante: how the drive ended (a
   kneel-out, a punt, a score) is context, not a condition.

Not counted (outside the definition): seconds before the opponent reaches a decisive state
(they cost time, not a possession), anything before the two-minute warning, the team's own
offense, two-score deficits, ties.

### Stored inputs, attribution and aggregates (G4)

Per season: `defense_snaps_<S>.parquet` (metrics 1 and 3: every opponent snap with a down
in the window while the opponent led, its next snap's clock, down and timeouts, the drive and
game context, the measured `p`, `g`, `s`), `half_passivity_<S>.parquet` (every candidate
with its WP state, kickoff spot and runoff, the WP model version, its exclusion),
`team_games_<S>.parquet` (every team-game with its head coach: the leaderboards' games) and
`season_<S>.json` (the measured constants, the config, counts). `clock.verify` recomputes
every output from the stored rows alone (the WP model loaded by version) and checks it is
identical. Coach-season aggregates (a table parallel to G3's): cases and timeouts left
(metric 1), cases, EP and WP left (metric 2), cases and seconds wasted (metric 3), per
coach-season; the report lists league totals per season, the worst cases, a leaderboard per
metric.

## Benchmark against nfl4th (G5: `benchmark.py`, `scripts/benchmarks/nfl4th.R`)

`twm decisions benchmark-nfl4th` (default 2024 and 2025) compares our recommended option on
every graded fourth down (no exclusion rule; regulation only, more than 15 s left, as nfl4th
evaluates) with the nfl4th R package's. We export the state columns nfl4th's
`add_4th_probs()` documents (mapping in `benchmark.STATE_COLUMNS` and in the report) and the
games table it joins (spread, total, roof) from our warehouse; the R script never downloads:
its cache points at `data/decisions/benchmark/nfl4th/cache`, every download function is
replaced by an error and, on macOS, R runs with network calls denied. nfl4th 1.0.7 ships its
field-goal model, punt table and two-point model but downloads its WP and conversion models
on first use; without those two files in the cache the run is `fg_only` (field-goal make
chances only) and the report says the decision benchmark has not run. The owner approved those two files on 2026-10-01 (fetched once from the nflverse/nfl4th release `model_archive`; url, size, sha256 and download time in the report, re-checked on every run). nfl4th's models are
probably in-sample for these seasons (spec 6.3); ours are walk-forward. Agreement is reported
overall, for our clear decisions and for our toss-ups; a disagreement's "likely cause" is a
heuristic (the sub-model whose gap moves WP the most), not a decomposition. The committed
benchmark (2024-2025) was run before G3b, on graded rows that still included the 4th quarter's
last 2:00 (431 of them): it is part of the evidence for that rule. A rerun now compares only
the decisions graded today (the late game is no longer graded).

## Not graded (yet)

Fourth downs and two-point tries in the 4th quarter's last 2:00 and in overtime (G3b, above).
Clock situations outside the three definitions above.

## Honesty note

The option to train the conversion and field-goal models on only the last 10 or 5 seasons was added after a first walk-forward backtest had shown recent seasons under-predicted. Each fold still chooses its window on its validation season (S-1) only, but because the option itself was suggested by test-season results, the published 2006-2025 numbers for those two models are slightly optimistic. The 2026 season is their first clean test.

G1b (the smoothed WP model): the smoothness limits were fixed before any fix was tried, and every candidate was chosen on the 2004-05 validation seasons only; but two later candidate rounds (the late-game hand-over and the redefined drive value) were prompted by looking at regraded seasons, and the pooled test log loss of a first refit was seen before them. The 2006-2025 WP and grading numbers are therefore slightly optimistic; 2026 is their first clean test.

G4 (clock management): the definitions and every threshold were written before any season was graded. Two things were seen before the full grading and are disclosed: a structural count of metric 2's candidates on 2024 with the thresholds relaxed (first-half kneels with >= 40 s left are almost absent in 2024; nothing was changed), and a first test run on 2024-2026 whose one hand-checked case (2025 week 1, HOU at LA) exposed that metric 3's written formula contradicted its own words; the formula was corrected (section "3. Late timeouts"), metric 1 was kept exactly as written, and the report shows metric 3's missed stops next to each metric-1 case as context.

G3b (the late game is not graded): decided by the reviewer after a hand-checked 2026 case and the nfl4th benchmark, both of which looked at graded output. The rule only removes grades (the 4th quarter's last 2:00 and overtime); it does not tune any model, and every other decision keeps exactly its grade.
