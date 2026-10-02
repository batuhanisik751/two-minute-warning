# Model card: Decision Report Card -- win probability, sub-models and grading rules (`decisions`)

Source: `config/production_models.yaml`
Pin key `decisions`, pin id (model_version) `grading-100604cddaabf068`, `model: grading`,
approved 2026-10-01, grading season 2026. Every number below is copied from the file named on
the `Source:` line above it (`tests/test_model_cards.py` checks this).

## Purpose and intended use

Grade NFL head coaches' fourth-down and two-point decisions (and three limited clock-management
metrics) by the win probability (WP) each option was worth before the snap: the option chosen
against the best option, in WP points, credited to the head coach. Aggregates (WP lost per game,
per season, league rows) feed the Decision Report Card pages.

**Not intended for:** a verdict on a coach's competence or job (see Ethical notes); the last
two minutes of the 4th quarter and overtime (not graded); in-game live advice; play-calling
beyond the go / field goal / punt choice; betting.

## Inputs and point-in-time rules

Source: `reports/decisions/wp_backtest.md`, "Setup"
- **WP features:** score difference, seconds left in the game and in the half, half, down,
  distance, yards to the end zone, both teams' timeouts, receives the second-half kickoff,
  home / away / neutral, the closing spread from the offense's view, era flags (2015
  extra-point rule, 2023+ kickoff rules) and two time interactions.
- **Label:** the possession team before the snap won the game.

Source: `docs/decision_metrics.md`, "Fourth-down grades (G3: `grade_inputs.py`, `grade.py`)"
- **Point in time:** every option is priced by season S's fold models (trained on the seasons
  before S). Season-level inputs use the seasons before S only: the longest field goal MADE
  before S, the punt range (the 0.1% quantile of punting yardlines before S), the kickoff spot
  from EARLIER weeks of S (else the previous season), the Platt recalibration, which never reads
  season S.
- **Grading rules:** Recommendation = the existing option with the highest WP (ties: go, then
  field goal, then punt). WP lost = WP(best) - WP(chosen). **Clear** (graded) when WP(best) -
  WP(second best) > 0.015 = 1.5 WP points; otherwise **toss-up** (counted, never graded).
- **Exclusions:** not a snap, a penalty that wiped the play out, kneels and spikes, aborted
  snaps, the half's last snap (at most 10 seconds left), and the **late game**: at most 120
  seconds left in the 4th quarter, or overtime.

## Model and training

Source: `reports/decisions/wp_backtest.md`, "Setup"
- **WP model** (G1b): **spline_sym_late_hand_over** (spline_sym_late, handing over to one
  monotone LightGBM fit on every play from Q4 15:00 to 10:00, and in overtime), chosen on the validation seasons
  2004-2005 by a rule fixed beforehand. **Walk-forward:** 20 folds, test seasons 2006-2025;
  the first is 'test 2006: train 1999-2005, tune on 1999-2004 -> validate on 2005'. Isotonic
  calibration kept in 0 of 20 folds.

Source: `reports/decisions/submodels.md`, "1. Go for it: P(convert)", "2. Field goals: P(make)", "3. Punts: where the receiving team starts"
- **Sub-models (G2), one fold per graded season, same walk-forward:** P(convert) (LightGBM,
  monotone in yards to go), P(make) for field goals (LightGBM, monotone; distance, indoors,
  temperature, wind, surface, era flags), the punt result distribution (a Gaussian kernel over
  training punts from nearby yardlines), and extra-point and two-point rates (last 5 seasons
  before S).

Source: `docs/deploy.md`, "The Decision Report Card's approved grading (step P3)"
- **What is pinned:** the grading spec `artifacts/production_models/decisions/<version>.json`
  (the season, each fold model by version, file and sha256, the season-level inputs, the clock
  constants and the `decisions:` settings), the five fold models (WP, conversion, field goal,
  punt, tries), each opened only after its sha256 matched the spec, and the frozen history.

Source: `config/production_models.yaml`
- Spec `artifacts/production_models/decisions/grading-100604cddaabf068.json` (sha256
  `c8749603c0a04cb686ca0db75d44525c8e8d85469c91d003501860bbb217e34f`); frozen history of
  2006-2025: `fourth_downs` (83462 rows), `two_point` (27362), `clock_cases` (1118),
  `team_games` (10862), `season_inputs` (20). Approved 2026-10-01.

## Evaluation

Source: `reports/decisions/wp_backtest.md`, "Setup", "Pooled 2006-2025"
**WP model**, walk-forward test seasons 2006-2025, on the 753,923 test plays (5,417 games) that
have nflfastR's `wp` and `vegas_wp`. Lower Brier and log loss are better. Intervals: 95%,
season-block bootstrap (2,000 resamples). Baselines: nflfastR's `wp` (no spread) and
`vegas_wp` (the same closing spread as ours); both were fit once on many seasons, including
seasons tested here, so their numbers are partly in-sample, ours never saw the season it scores.
Ours beats `wp` and is slightly behind `vegas_wp`.

| Method | Brier | Log loss | Calibration error (ECE, pts) |
|---|---|---|---|
| Our model | 0.1487 (0.1448 to 0.1527) | 0.4475 (0.4369 to 0.4588) | 0.4 (0.3 to 1.0) |
| nflfastR wp | 0.1623 (0.1589 to 0.1664) | 0.4803 (0.4710 to 0.4915) | 0.6 (0.4 to 1.2) |
| nflfastR vegas_wp | 0.1473 (0.1432 to 0.1514) | 0.4430 (0.4321 to 0.4550) | 0.3 (0.3 to 0.8) |
| Ours minus nflfastR wp | -0.0136 (-0.0155 to -0.0118) | -0.0328 (-0.0371 to -0.0285) | |
| Ours minus nflfastR vegas_wp | +0.0014 (+0.0009 to +0.0019) | +0.0045 (+0.0029 to +0.0059) | |

Source: `reports/decisions/submodels.md`, "Fourth downs (the decisions graded in G3)"
**P(convert)** on the fourth downs graded in G3, against a lookup baseline:

| Rows | Method | Brier | Log loss | Calibration error (ECE, pts) |
|---|---|---|---|---|
| 2006-2025 (12,066) | Model | 0.2251 (0.2222 to 0.2279) | 0.6410 (0.6344 to 0.6474) | 1.1 (0.8 to 2.1) |
| 2006-2025 (12,066) | Lookup | 0.2278 (0.2252 to 0.2304) | 0.6470 (0.6413 to 0.6526) | 1.7 (1.1 to 2.8) |
| 2006-2025 | Model minus lookup | -0.0028 (-0.0035 to -0.0020) | -0.0060 (-0.0078 to -0.0043) | |

Source: `reports/decisions/submodels.md`, "2. Field goals: P(make)"
**P(make)** for field goals, against a lookup baseline (all attempts, and 50+ yards):

| Rows | Method | Brier | Log loss | Calibration error (ECE, pts) |
|---|---|---|---|---|
| 2006-2025 (20,918) | Model | 0.1185 (0.1154 to 0.1219) | 0.3798 (0.3713 to 0.3888) | 1.6 (1.0 to 2.4) |
| 2006-2025 (20,918) | Lookup | 0.1207 (0.1179 to 0.1237) | 0.3869 (0.3798 to 0.3948) | 4.7 (3.9 to 5.3) |
| 2006-2025 | Model minus lookup | -0.0022 (-0.0030 to -0.0013) | -0.0072 (-0.0093 to -0.0051) | |
| 50+ yards, 2006-2025 (3,285) | Model | 0.2247 (0.2189 to 0.2320) | 0.6414 (0.6291 to 0.6565) | 3.1 (2.1 to 4.9) |
| 50+ yards, 2006-2025 (3,285) | Lookup | 0.2304 (0.2257 to 0.2359) | 0.6540 (0.6442 to 0.6655) | 8.2 (6.1 to 9.8) |
| 50+ yards, 2006-2025 | Model minus lookup | -0.0056 (-0.0090 to -0.0016) | -0.0126 (-0.0201 to -0.0039) | |

Source: `reports/decisions/submodels.md`, "3. Punts: where the receiving team starts"
**Punt result distribution** against the raw same-yardline history (mean log score, 5-yard bins;
higher = better):

| Method | Mean log score (5-yard bins; higher = better) |
|---|---|
| model | -2.122 (-2.139 to -2.104) |
| raw same-yardline history | -2.168 (-2.188 to -2.146) |
| model - raw | +0.045 (+0.040 to +0.050) |

Source: `reports/decisions/nfl4th_benchmark.md`, "Agreement on the recommended option"
**Benchmark against nfl4th** (seasons 2024, 2025; nfl4th 1.0.7; 7,783 benchmarked fourth
downs). Agreement is not accuracy: nfl4th is probably in-sample here. The benchmark was run
before the late-game rule, so its rows include the 4th quarter's last 2:00 (overtime and 15
seconds or less left are left out because nfl4th evaluates neither).

| our grade | season | rows | agree | rate |
|---|---|---|---|---|
| all | all | 7,783 | 6,236 | 80.1% |
| clear | all | 3,405 | 3,205 | 94.1% |
| toss-up | all | 4,378 | 3,031 | 69.2% |

## Calibration

Source: `reports/decisions/wp_backtest.md`, "Reliability (pooled 2006-2025, 10 bins)", "Pooled 2006-2025"
**WP model**, pooled 2006-2025: the calibration error (ECE) is 0.4 (0.3 to 1.0) points; every
bin's observed win rate is within a point of its mean prediction.

| Predicted | Our model: plays, predicted, observed (95%) | nflfastR wp: plays, predicted, observed (95%) | nflfastR vegas_wp: plays, predicted, observed (95%) |
|---|---|---|---|
| 0.0-0.1 | 107,565, 0.035, 0.032 (0.026 to 0.039) | 89,928, 0.033, 0.029 (0.023 to 0.036) | 111,986, 0.033, 0.032 (0.026 to 0.039) |
| 0.1-0.2 | 60,123, 0.150, 0.152 (0.141 to 0.164) | 57,434, 0.150, 0.152 (0.139 to 0.165) | 62,295, 0.150, 0.156 (0.145 to 0.168) |
| 0.2-0.3 | 66,762, 0.251, 0.248 (0.234 to 0.264) | 62,413, 0.251, 0.261 (0.247 to 0.276) | 65,622, 0.250, 0.257 (0.243 to 0.270) |
| 0.3-0.4 | 67,968, 0.350, 0.346 (0.335 to 0.356) | 70,210, 0.350, 0.364 (0.352 to 0.375) | 62,671, 0.350, 0.347 (0.338 to 0.357) |
| 0.4-0.5 | 67,969, 0.449, 0.454 (0.449 to 0.460) | 84,487, 0.449, 0.458 (0.451 to 0.464) | 66,440, 0.450, 0.453 (0.447 to 0.460) |
| 0.5-0.6 | 65,826, 0.551, 0.556 (0.549 to 0.562) | 90,893, 0.551, 0.551 (0.545 to 0.557) | 65,030, 0.551, 0.554 (0.548 to 0.560) |
| 0.6-0.7 | 72,820, 0.650, 0.657 (0.645 to 0.666) | 78,048, 0.648, 0.635 (0.627 to 0.645) | 69,140, 0.650, 0.654 (0.644 to 0.663) |
| 0.7-0.8 | 70,294, 0.750, 0.758 (0.745 to 0.770) | 67,698, 0.748, 0.741 (0.731 to 0.751) | 68,101, 0.750, 0.747 (0.735 to 0.760) |
| 0.8-0.9 | 63,869, 0.849, 0.853 (0.844 to 0.862) | 59,060, 0.850, 0.852 (0.842 to 0.862) | 66,838, 0.850, 0.847 (0.837 to 0.857) |
| 0.9-1.0 | 110,727, 0.964, 0.969 (0.963 to 0.973) | 93,752, 0.966, 0.970 (0.963 to 0.975) | 115,800, 0.967, 0.969 (0.963 to 0.973) |

Source: `reports/decisions/submodels.md`, "Reliability (fourth downs, 10 bins)"
**P(convert)** on fourth downs: the raw model under-predicts the long-shot conversions (the
lowest bins), which is why the grades use a fourth-down Platt recalibration when it beats the
raw model out of sample.

| Predicted | Rows | Mean predicted | Observed (95%) |
|---|---|---|---|
| 0.0-0.1 | 207 | 0.072 | 0.097 (0.058 to 0.140) |
| 0.1-0.2 | 522 | 0.156 | 0.184 (0.154 to 0.210) |
| 0.2-0.3 | 840 | 0.253 | 0.293 (0.258 to 0.327) |
| 0.3-0.4 | 1,567 | 0.350 | 0.363 (0.343 to 0.383) |
| 0.4-0.5 | 2,088 | 0.454 | 0.464 (0.440 to 0.487) |
| 0.5-0.6 | 2,691 | 0.551 | 0.561 (0.542 to 0.579) |
| 0.6-0.7 | 2,863 | 0.659 | 0.656 (0.635 to 0.676) |
| 0.7-0.8 | 1,288 | 0.722 | 0.715 (0.686 to 0.741) |

## Known limits and failure cases

Source: `reports/decisions/fourth_downs.md`, "Why the last two minutes are not graded", "7. Limitations"
- **Late game not graded:** the last 2:00 of the 4th quarter and overtime are not graded, because
  the grades there rest on the WP model's values of late-game states, where the evidence (the
  nfl4th benchmark's disagreements, a hand-checked 2026 case) says it cannot be trusted yet. Per
  complete season (2006-2025) that leaves out 206-279 fourth downs and 52-86 tries. The last
  2:00 of the first half still is graded.
- **League-wide models:** no kicker, offense or defense strength beyond the closing spread;
  field goals of 55+ yards are under-predicted in 2023-2025.
- **Simplified options:** a touchdown inside a fourth-down option counts 7 points (extra point
  assumed good); the clock moves by median times, which over-run the clock late in a half when
  real teams hurry.
- **Optimistic history (honesty notes):** the 10/5-season training windows of the conversion and
  field-goal models were added after a first backtest, and two G1b candidate rounds were
  prompted by looking at regraded seasons; the 2006-2025 numbers are therefore slightly
  optimistic, and 2026 is the first clean test.

Source: `docs/decision_metrics.md`, "Clock management (G4: `clock_inputs.py`, `clock.py`)"
- **Clock management** is three limited metrics (timeouts unused in a lost one-score game,
  end-of-half passivity, late timeouts when trailing), not a model of clock strategy.

Source: `docs/timemachine.md`, "What is reused, not recomputed (and why)"
- **Fold models are reused, not retrained,** by the reproducibility check: every play is regraded
  from the warehouse with the stored fold models of that season; retraining every WP fold means
  running the full G1 backtest (1,009,826 play states, 1999-2025). The Linux job never regrades
  history: it serves the frozen history.

## Owner decisions that shaped it

Source: `docs/progress.md`
- 2026-10-01 (owner, spec decision point G5): benchmark the fourth-down recommendations against
  nfl4th (R package; R 4.4 already installed), on 1-2 seasons, reporting the agreement rate.

Source: `docs/decision_metrics.md`, "Honesty note"
- G3b (the late game is not graded) was decided by the reviewer after a hand-checked 2026 case
  and the nfl4th benchmark; the rule only removes grades, it does not tune any model.

Source: `config/production_models.yaml`
- Pin approved 2026-10-01.

## Reproducibility

Source: `docs/timemachine.md`, "Results (owner's Mac, 2026-10-02: commit c8921e9 + I3a; 48d3ada since changed only web/)"
- `uv run twm model check decisions`: the pin, the spec, every fold model file and the frozen
  history, whose coach-season and league rows must reproduce `reports/decisions/fourth_downs.csv`
  and `clock.csv`.
- `uv run twm timemachine verify --module decisions --sample 99`: each season regraded from the
  warehouse with its stored fold models. Last run (2026-10-02): 2006-2025, 100 lists, 122,824
  rows (83,462 fourth downs, 27,362 tries, 1,118 clock, 10,862 team games, 20 inputs), max abs
  diff 0.0, 0 mismatches.

## Ethical notes

These grades are **our models' estimates, not verdicts**. A "clear" mistake means our WP model,
priced with league-wide sub-models that know nothing of the specific kicker, injuries, weather
nuance or game plan, valued another option more by a margin; the coach may have known better.
Grades are credited to the head coach, although coordinators and players share the decision.
Real people's reputations are involved: the pages show the inputs and the margin of every
grade, toss-ups are never graded, and the late game is left out where the model is least
trustworthy. The grades should not be read as evidence about anyone's job.

Source: `docs/research_decisions_vs_firings.md`, "The short answer"
The project's own research found no detectable link between fourth-down WP lost and firings
(odds ratio 1.01 per standard deviation, classical 95% interval 0.76 to 1.35): a poor grade is
not evidence that a coach will be fired.
