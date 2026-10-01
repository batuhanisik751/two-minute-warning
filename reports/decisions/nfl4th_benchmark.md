# Fourth-down benchmark: our recommendations vs nfl4th

Seasons 2024, 2025; nfl4th 1.0.7 (R, CRAN); run status **full**; R ran with every network call denied: yes.

Rows: 7,840 graded fourth downs (G3 rules, G1b WP model); 7,783 benchmarked; left out 37 in overtime and 20 with 15 seconds or less left (nfl4th evaluates neither).

**Fairness.** Our grades use models fitted only on seasons before the graded one. nfl4th's models were fitted on nflfastR data; the package does not document which seasons, and they very likely include these, so nfl4th is probably in-sample here (PROJECT_SPEC 6.3). Agreement is not accuracy: neither side is the truth.

## nfl4th's model files

nfl4th downloads its WP and conversion models on first use. The owner approved these two files (2026-10-01); they were fetched once from the official nflverse/nfl4th release `model_archive` into nfl4th's cache, their sizes checked against the approved numbers, and their sha256 is re-checked on every run. Nothing else was downloaded: no play-by-play, no schedules, no other release asset; R runs with those code paths replaced by errors.

| file | bytes | sha256 | downloaded (UTC) | url |
|---|---|---|---|---|
| `fd_model.rds` | 9,107,324 | `db1c1763e763df41e11e013088d77889ddf57f75025e8529e1f587e0c2c4c31a` | 2026-10-01T13:55:12Z | https://github.com/nflverse/nfl4th/releases/download/model_archive/fd_model.rds |
| `wp_model.rds` | 2,422,111 | `80adad2eb8d5cfb1e9610a31e7b123f9d88f73ce6706fba1ebf9da1b1cc54483` | 2026-10-01T13:55:12Z | https://github.com/nflverse/nfl4th/releases/download/model_archive/wp_model.rds |

## Agreement on the recommended option

Same recommended option (go / field goal / punt), by our grade: 'clear' when our best option beats the second by more than the toss-up margin, else 'toss-up'.

| our grade | season | rows | agree | rate |
|---|---|---|---|---|
| all | 2024 | 3,894 | 3,100 | 79.6% |
| all | 2025 | 3,889 | 3,136 | 80.6% |
| all | all | 7,783 | 6,236 | 80.1% |
| clear | 2024 | 1,769 | 1,656 | 93.6% |
| clear | 2025 | 1,636 | 1,549 | 94.7% |
| clear | all | 3,405 | 3,205 | 94.1% |
| toss-up | 2024 | 2,125 | 1,444 | 68.0% |
| toss-up | 2025 | 2,253 | 1,587 | 70.4% |
| toss-up | all | 4,378 | 3,031 | 69.2% |
| one option | 2024 | 0 | 0 | - |
| one option | 2025 | 0 | 0 | - |
| one option | all | 0 | 0 | - |

## Confusion table (all benchmarked rows)

Rows: our recommendation; columns: nfl4th's.

| ours \ nfl4th | go | field goal | punt |
|---|---|---|---|
| go | 2,535 | 462 | 382 |
| field goal | 219 | 1,302 | 37 |
| punt | 402 | 45 | 2,399 |

## Go gain: ours minus nfl4th's

Go gain = WP(go) - WP(best kick), in WP points (nfl4th's `go_boost`). The difference is ours minus nfl4th's on the same play.

| rows | mean | sd | 5% | 25% | median | 75% | 95% | within 1.5 | beyond 5 | corr |
|---|---|---|---|---|---|---|---|---|---|---|
| 7,783 | 0.03 | 2.04 | -2.64 | -0.55 | 0.02 | 0.85 | 2.59 | 74.7% | 2.6% | 0.812 |

## League go rate: what each side recommends

| season | rows | teams went | we recommend go | nfl4th recommends go |
|---|---|---|---|---|
| 2024 | 3,894 | 20.1% | 41.7% | 39.5% |
| 2025 | 3,889 | 22.9% | 45.2% | 41.6% |

## Where they disagree

Agreement and each side's go recommendation by situation; 'go-gain gap' = mean of ours minus nfl4th's (positive: we like going more), and its mean size.

| game phase | rows | agree | we say go | nfl4th says go | go-gain gap | mean size |
|---|---|---|---|---|---|---|
| Q1-Q3 | 4,965 | 82.5% | 46.1% | 39.1% | 0.33 | 0.96 |
| Q2, last 2:00 | 561 | 75.8% | 32.6% | 29.2% | 0.09 | 1.31 |
| Q4, before 5:00 | 1,299 | 76.8% | 37.5% | 42.0% | -0.26 | 1.14 |
| Q4, 5:00-2:00 | 527 | 74.0% | 41.4% | 46.7% | -0.34 | 1.45 |
| Q4, last 2:00 | 431 | 75.6% | 47.3% | 60.8% | -2.19 | 3.25 |

| field position | rows | agree | we say go | nfl4th says go | go-gain gap | mean size |
|---|---|---|---|---|---|---|
| opp 20 and in | 1,363 | 76.4% | 53.8% | 44.7% | 0.55 | 1.57 |
| opp 21-40 | 1,576 | 77.7% | 43.3% | 36.8% | 0.22 | 1.28 |
| opp 41 to own 45 | 1,406 | 77.9% | 59.2% | 56.9% | -0.12 | 0.99 |
| own 44 and back | 3,438 | 83.7% | 32.9% | 33.9% | -0.20 | 1.05 |

| yards to go | rows | agree | we say go | nfl4th says go | go-gain gap | mean size |
|---|---|---|---|---|---|---|
| 1 | 907 | 91.2% | 94.4% | 92.8% | -0.03 | 1.32 |
| 2-3 | 1,183 | 74.9% | 82.3% | 75.9% | 0.13 | 1.23 |
| 4-6 | 1,749 | 64.8% | 60.8% | 48.1% | 0.10 | 1.20 |
| 7+ | 3,944 | 86.0% | 12.3% | 14.6% | -0.02 | 1.12 |

## Biggest disagreements

The disagreements with the largest go-gain difference (WP points; 'ours vs nfl4th').

| game | teams | clock | situation | score | our grade | chosen | what differs |
|---|---|---|---|---|---|---|---|
| 2024 wk 13 | ARI v MIN | Q4 0:40 | 4th & 10 at own 30 | -1 | clear | go | we say punt (go gain -7.3), nfl4th says go (+14.1); P(first down) 29% vs 27%; WP after success 45.5 vs 55.2, after failure 5.5 vs 0.0. Likely cause: punt (result distribution and its WP). |
| 2024 wk 16 | DAL v TB | Q4 1:52 | 4th & 2 at own 38 | +2 | clear | punt | we say punt (go gain -4.3), nfl4th says go (+13.0); P(first down) 49% vs 58%; WP after success 85.5 vs 100.0, after failure 32.1 vs 43.7. Likely cause: WP model and clock assumptions (value of the states after the play). |
| 2024 wk 18 | GB v CHI | Q4 0:58 | 4th & 4 at opp 37 | -2 | clear | field goal | we say go (go gain +11.2), nfl4th says field goal (-5.9); P(first down) 56% vs 48%; P(make) 47% vs 62%; WP after success 79.5 vs 71.1, after failure 18.7 vs 17.0. Likely cause: field-goal model. |
| 2025 wk 18 | IND v HOU | Q4 2:42 | 4th & 2 at opp 4 | -2 | clear | field goal | we say go (go gain +7.7), nfl4th says field goal (-9.1); P(first down) 46% vs 36%; P(make) 99% vs 99%; WP after success 68.1 vs 62.7, after failure 20.7 vs 19.0. Likely cause: conversion model. |
| 2024 wk 5 | ARI v SF | Q4 1:40 | 4th & 3 at opp 16 | -2 | toss_up | field goal | we say go (go gain +0.7), nfl4th says field goal (-16.0); P(first down) 44% vs 45%; P(make) 96% vs 93%; WP after success 76.1 vs 69.4, after failure 15.4 vs 19.5. Likely cause: WP model and clock assumptions (value of the states after the play). |
| 2025 wk 18 | BAL v PIT | Q4 0:21 | 4th & 7 at 50 | -2 | clear | go | we say punt (go gain -4.9), nfl4th says go (+11.4); P(first down) 35% vs 37%; WP after success 16.2 vs 57.6, after failure 3.5 vs 0.0. Likely cause: WP model and clock assumptions (value of the states after the play). |
| 2025 wk 12 | PIT v CHI | Q4 0:21 | 4th & 6 at opp 47 | -3 | clear | go | we say field goal (go gain -12.9), nfl4th says go (+3.3); P(first down) 38% vs 42%; P(make) 35% vs 23%; WP after success 4.3 vs 26.4, after failure 0.3 vs 0.0. Likely cause: WP model and clock assumptions (value of the states after the play). |
| 2024 wk 6 | IND v TEN | Q4 0:22 | 4th & 6 at own 40 | +3 | clear | punt | we say go (go gain +9.7), nfl4th says punt (-6.5); P(first down) 20% vs 39%; WP after success 99.6 vs 100.0, after failure 99.3 vs 73.6. Likely cause: WP model and clock assumptions (value of the states after the play). |
| 2025 wk 1 | BAL v BUF | Q4 1:33 | 4th & 3 at own 38 | +2 | clear | punt | we say punt (go gain -10.1), nfl4th says go (+5.4); P(first down) 45% vs 54%; WP after success 97.4 vs 100.0, after failure 49.5 vs 58.9. Likely cause: WP model and clock assumptions (value of the states after the play). |
| 2025 wk 15 | IND v SEA | Q4 0:52 | 4th & 3 at opp 42 | -2 | clear | field goal | we say go (go gain +12.2), nfl4th says field goal (-3.2); P(first down) 49% vs 44%; P(make) 31% vs 42%; WP after success 58.9 vs 50.1, after failure 6.7 vs 7.2. Likely cause: field-goal model. |
| 2024 wk 13 | ATL v LAC | Q4 0:47 | 4th & 12 at opp 35 | -4 | toss_up | go | we say field goal (go gain -1.5), nfl4th says go (+12.0); P(first down) 25% vs 23%; P(make) 70% vs 72%; WP after success 49.7 vs 51.8, after failure 4.2 vs 0.0. Likely cause: WP model and clock assumptions (value of the states after the play). |
| 2025 wk 1 | CIN v CLE | Q4 2:00 | 4th & 6 at own 30 | +1 | clear | punt | we say punt (go gain -11.4), nfl4th says go (+0.8); P(first down) 36% vs 44%; WP after success 86.6 vs 83.9, after failure 32.3 vs 43.6. Likely cause: WP model and clock assumptions (value of the states after the play). |

## Why they disagree (all disagreements)

Likely cause = the sub-model whose difference moves WP the most on that play: |conversion chance gap| x |success - failure WP|, |make chance gap| x |make - miss WP| (when a field goal is one of the two picks), |punt WP gap| (when a punt is), and the mean gap of the WP after success and failure. A heuristic, not a decomposition.

| likely cause | rows | clear for us | mean go-gain gap | what it means |
|---|---|---|---|---|
| WP model and clock assumptions (value of the states after the play) | 1,001 | 130 | 1.7 | the states after the play are valued differently: the WP models differ (nfl4th averages its own xgboost model with nflfastR's vegas_wp; ours is G1b's walk-forward model), and so do the clock rules: our go option runs the median fourth-down runoff (14-22 s) off after either outcome, nfl4th 6 s; nfl4th also sets WP to 1 or 0 when the leading team has the ball late and the other side cannot stop the clock (its end-game rules). |
| punt (result distribution and its WP) | 500 | 49 | 1.2 | the punt is valued differently: nfl4th uses a fixed table of punt results by yard line (from its 31 out) priced by its WP; ours uses G2's punt-result distribution priced by our WP model. |
| conversion model | 29 | 12 | 3.6 | the two conversion models give different chances of a first down; the go option moves by that difference times the swing between converting and failing. |
| field-goal model | 17 | 9 | 4.1 | the two field-goal models give different make chances (nfl4th: one model by roof (outdoors or not) and era (2020 on), scaled down beyond the 40 and 0 from the 53; ours: G2's walk-forward model with distance, weather and surface). |

## Sub-models side by side

Means over the rows where both sides have the number (probabilities 0-1, WP in points 0-100); 'observed' is what really happened where it exists.

| component | rows | ours | nfl4th | mean abs diff | corr | observed |
|---|---|---|---|---|---|---|
| P(make) on field-goal attempts | 1,975 | 0.847 | 0.848 | 0.023 | 0.96 | 0.855 |
| P(first down) on real attempts | 1,675 | 0.543 | 0.523 | 0.035 | 0.97 | 0.574 |
| P(first down), every row | 7,783 | 0.386 | 0.371 | 0.033 | 0.98 | - |
| WP after a conversion | 7,783 | 54.124 | 54.342 | 3.326 | 0.99 | - |
| WP after a failed attempt | 7,783 | 41.337 | 41.207 | 3.113 | 0.99 | - |
| WP of a field goal | 3,617 | 50.330 | 50.538 | 3.035 | 0.99 | - |
| WP of a punt (both have one) | 5,605 | 44.224 | 43.760 | 3.196 | 0.99 | - |
| WP of going for it | 7,783 | 46.153 | 45.961 | 3.086 | 0.99 | - |

## Inputs we give nfl4th

nfl4th's `add_4th_probs()` gets a data frame of these columns (names checked against the installed package's help and source); it sets down = 4 and rebuilds the clock, spread and totals itself. Its games table (spread, total, roof) is built from our warehouse (`fact_game`) with nfl4th's own transformations; no play-by-play or schedule is downloaded. No `runoff` is passed (nfl4th's default: 0 seconds).

| nfl4th column | how we fill it |
|---|---|
| `game_id` | play key (not used by nfl4th: dropped before its games join, kept for ours) |
| `play_id` | play key |
| `home_team` | stored row (warehouse fact_play) |
| `away_team` | stored row |
| `posteam` | stored row: the team with the ball |
| `type` | 'reg' when season_type is REG, else 'post' (nfl4th's games-join key) |
| `season` | stored row |
| `qtr` | stored row (1-4; overtime is not benchmarked) |
| `quarter_seconds_remaining` | half_seconds_remaining - 900 in quarters 1 and 3, else half_seconds_remaining (nfl4th rebuilds half / game seconds from it) |
| `ydstogo` | stored row |
| `yardline_100` | stored row: yards to the opponent's end zone |
| `score_differential` | stored row: posteam score minus defteam score |
| `home_opening_kickoff` | 1 when the home team kicked the game's first kickoff (our opening_kicker: defteam of the first quarter-1 kickoff row), else 0 |
| `posteam_timeouts_remaining` | stored row (pre-snap) |
| `defteam_timeouts_remaining` | stored row (pre-snap) |

Option availability differs: our field goal exists within the longest field goal made before the season and our punt from the yard lines punts come from; nfl4th always prices a field goal (make chance 0 from its 53, a 70-yard try) and punts only from its 31 out.
