# Cliff backtest at the post-draft snapshot (step I2b)

Step I2b: the same rows and labels as the end-of-season report, read at the POST-DRAFT snapshot (00:00 UTC on 06-01 (MM-DD) of S+1, config `as_of.board.post_draft`, spec 6.1; point in time): every I1b feature (head-coach departures now known through the as-of) plus the draft capital his S team added at his position, a first-round rookie QB and a flag for picks with no known position (docs/board.md, "Post-draft snapshot"). Free-agency and trade moves are not observable point-in-time and are not features. `eos` = the end-of-season report's primary model on the same rows; the ECR (2020+) is still taken months later (the last August/September scrape before week 1), so it knows more than the model.

ECR timing (negative = the ECR is public only after the as-of):

| snapshot S | ECR scrape of S+1 | public at (UTC) | snapshot as-of (UTC) | ECR public before the as-of by (days) |
|---|---|---|---|---|
| 2019 | 2020-09-03 | 2020-09-04 00:00 | 2020-06-01 Mon 00:00 | -95.0 |
| 2020 | 2021-09-03 | 2021-09-04 00:00 | 2021-06-01 Tue 00:00 | -95.0 |
| 2021 | 2022-09-02 | 2022-09-03 00:00 | 2022-06-01 Wed 00:00 | -94.0 |
| 2022 | 2023-09-01 | 2023-09-02 00:00 | 2023-06-01 Thu 00:00 | -93.0 |
| 2023 | 2024-08-30 | 2024-08-31 00:00 | 2024-06-01 Sat 00:00 | -91.0 |
| 2024 | 2025-08-29 | 2025-08-30 00:00 | 2025-06-01 Sun 00:00 | -90.0 |

Rows and positives per test season (label season = S + 1): `cliff_main` 337 positives (13-29 a season); `cliff_missed` 251 positives (6-21 a season); `cliff_sensitivity` 588 positives (21-42 a season).

| snapshot S | cliff_main rows | cliff_main positives | cliff_missed rows | cliff_missed positives | cliff_sensitivity rows | cliff_sensitivity positives |
|---|---|---|---|---|---|---|
| 2007 | 86 | 23 | 105 | 19 | 105 | 42 |
| 2008 | 89 | 29 | 102 | 13 | 102 | 42 |
| 2009 | 88 | 20 | 97 | 9 | 97 | 29 |
| 2010 | 76 | 16 | 90 | 14 | 90 | 30 |
| 2011 | 84 | 13 | 98 | 14 | 98 | 27 |
| 2012 | 82 | 14 | 95 | 13 | 95 | 27 |
| 2013 | 82 | 19 | 97 | 15 | 97 | 34 |
| 2014 | 79 | 19 | 92 | 13 | 92 | 32 |
| 2015 | 78 | 13 | 97 | 19 | 97 | 32 |
| 2016 | 77 | 21 | 92 | 15 | 92 | 36 |
| 2017 | 71 | 16 | 92 | 21 | 92 | 37 |
| 2018 | 63 | 18 | 82 | 19 | 82 | 37 |
| 2019 | 71 | 16 | 77 | 6 | 77 | 22 |
| 2020 | 74 | 16 | 94 | 20 | 94 | 36 |
| 2021 | 79 | 13 | 87 | 8 | 87 | 21 |
| 2022 | 85 | 26 | 96 | 11 | 96 | 37 |
| 2023 | 78 | 20 | 87 | 9 | 87 | 29 |
| 2024 | 81 | 25 | 94 | 13 | 94 | 38 |

## Results

### Cliff (6+ games in S+1; main model) (`cliff_main`, label `y_cliff`)

**snapshots 2007-2024 (labels 2008-2025)**

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit` | 0.359 [0.312, 0.411] | 0.400 [0.317, 0.489] | 0.364 [0.314, 0.411] | 0.171 [0.159, 0.183] |
| `lgbm` | 0.349 [0.297, 0.408] | 0.450 [0.361, 0.539] | 0.375 [0.311, 0.439] | 0.172 [0.159, 0.185] |
| `base_ppg_rank` | 0.255 [0.225, 0.295] | 0.261 [0.211, 0.317] | 0.264 [0.225, 0.303] | 0.180 [0.168, 0.193] |
| `eos` | 0.363 [0.322, 0.409] | 0.433 [0.350, 0.517] | 0.361 [0.314, 0.408] | 0.171 [0.159, 0.183] |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | +0.105 [0.073, 0.139] | +0.139 [0.078, 0.200] | +0.100 [0.058, 0.144] | 1.00 |
| `logit` - `eos` | -0.003 [-0.016, 0.009] | -0.033 [-0.061, -0.006] | +0.003 [-0.014, 0.019] | 0.29 |
| `lgbm` - `base_ppg_rank` | +0.094 [0.060, 0.139] | +0.189 [0.111, 0.278] | +0.111 [0.053, 0.169] | 1.00 |
| `lgbm` - `eos` | -0.014 [-0.038, 0.010] | +0.017 [-0.044, 0.078] | +0.014 [-0.036, 0.064] | 0.12 |
| `base_ppg_rank` - `eos` | -0.108 [-0.143, -0.076] | -0.172 [-0.228, -0.106] | -0.097 [-0.147, -0.053] | 0.00 |

**snapshots 2019-2024 (labels 2020-2025), with the ECR**

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit` | 0.414 [0.339, 0.498] | 0.483 [0.317, 0.667] | 0.433 [0.375, 0.483] | 0.172 [0.154, 0.190] |
| `lgbm` | 0.432 [0.335, 0.535] | 0.550 [0.383, 0.717] | 0.467 [0.350, 0.567] | 0.170 [0.150, 0.188] |
| `base_ppg_rank` | 0.299 [0.234, 0.381] | 0.317 [0.217, 0.433] | 0.292 [0.233, 0.350] | 0.185 [0.163, 0.206] |
| `eos` | 0.410 [0.341, 0.484] | 0.483 [0.317, 0.683] | 0.417 [0.367, 0.459] | 0.173 [0.154, 0.190] |
| `ecr` | 0.503 [0.424, 0.570] | 0.533 [0.417, 0.650] | 0.450 [0.375, 0.525] | - |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | +0.116 [0.063, 0.183] | +0.167 [0.083, 0.250] | +0.142 [0.108, 0.175] | 1.00 |
| `logit` - `eos` | +0.004 [-0.007, 0.017] | 0.000 [-0.050, 0.050] | +0.017 [-0.017, 0.042] | 0.77 |
| `logit` - `ecr` | -0.088 [-0.145, -0.018] | -0.050 [-0.183, 0.083] | -0.017 [-0.058, 0.017] | 0.00 |
| `lgbm` - `base_ppg_rank` | +0.133 [0.068, 0.228] | +0.233 [0.100, 0.367] | +0.175 [0.083, 0.275] | 1.00 |
| `lgbm` - `eos` | +0.022 [-0.012, 0.073] | +0.067 [-0.017, 0.167] | +0.050 [-0.025, 0.125] | 0.92 |
| `lgbm` - `ecr` | -0.071 [-0.133, 0.017] | +0.017 [-0.133, 0.183] | +0.017 [-0.067, 0.100] | 0.06 |
| `base_ppg_rank` - `eos` | -0.111 [-0.177, -0.052] | -0.167 [-0.250, -0.083] | -0.125 [-0.183, -0.075] | 0.00 |
| `base_ppg_rank` - `ecr` | -0.204 [-0.254, -0.152] | -0.217 [-0.300, -0.133] | -0.158 [-0.183, -0.133] | 0.00 |

Top features (mean share of the importance over the 18 folds; logit = spread of the feature's log-odds contribution, sign from the 2024 fold's standardized coefficient; LightGBM = gain):

- `logit`: `ppg_change` (+) 12%, `pos_rb` (+) 8%, `games_s` (-) 6%, `age_curve_ratio` (-) 6%, `pos_te` (+) 5%, `age` (+) 5%, `touches_per_game_s` (+) 4%, `draft_unplaced_pd` (+) 4%
- `lgbm`: `ppg_change` 19%, `touches_per_game_s` 14%, `age_curve_ratio` 10%, `ppg_s` 7%, `age` 7%, `touches_s` 6%, `yards_per_touch_s` 6%, `yards_per_touch_trend` 4%

Importance smoke test (spec 6.2 rule 6), folds whose top feature holds over 40%: lgbm 2007: ppg_change 100%

Calibration of `logit` (its own probability; 10 equal-count bins, every test season):

| bin | rows | mean predicted | observed | range |
|---|---|---|---|---|
| 1 | 143 | 0.084 | 0.084 | 0.015-0.111 |
| 2 | 142 | 0.131 | 0.120 | 0.111-0.147 |
| 3 | 142 | 0.162 | 0.197 | 0.147-0.174 |
| 4 | 143 | 0.188 | 0.161 | 0.175-0.200 |
| 5 | 142 | 0.211 | 0.162 | 0.200-0.221 |
| 6 | 142 | 0.234 | 0.310 | 0.221-0.247 |
| 7 | 143 | 0.261 | 0.266 | 0.247-0.277 |
| 8 | 142 | 0.294 | 0.324 | 0.277-0.317 |
| 9 | 142 | 0.343 | 0.345 | 0.317-0.378 |
| 10 | 142 | 0.470 | 0.401 | 0.379-0.910 |

Where the model and the market disagree most (snapshots 2019-2024, per season the top 10 of each; who was right = the label):

| group | players | label = 1 | rate |
|---|---|---|---|
| in both | 20 | 17 | 0.85 |
| in the ECR's top 10, not `logit`'s | 40 | 15 | 0.38 |
| in `logit`'s top 10, not the ECR's | 40 | 12 | 0.30 |

Largest rank gaps (2 per season and side):

| label season | player | pos | group | rank by model | rank by ECR score | PPG S | PPG S+1 | games S+1 | label |
|---|---|---|---|---|---|---|---|---|---|
| 2020 | Julian Edelman | WR | ecr_only | 48 | 4 | 16.0 | 9.4 | 6 | 1 |
| 2020 | Andy Dalton | QB | ecr_only | 47 | 5 | 15.7 | 12.4 | 11 | 0 |
| 2020 | David Johnson | RB | model_only | 3 | 70 | 11.8 | 15.0 | 12 | 0 |
| 2020 | Todd Gurley | RB | model_only | 1 | 54 | 14.6 | 10.9 | 15 | 0 |
| 2021 | Cam Newton | QB | ecr_only | 46 | 8 | 17.4 | 12.3 | 7 | 0 |
| 2021 | Sterling Shepard | WR | ecr_only | 37 | 6 | 13.5 | 11.1 | 7 | 0 |
| 2021 | Mike Davis | RB | model_only | 5 | 36 | 13.8 | 8.1 | 17 | 1 |
| 2021 | Kyle Rudolph | TE | model_only | 8 | 27 | 5.5 | 4.0 | 15 | 0 |
| 2022 | Taylor Heinicke | QB | ecr_only | 63 | 3 | 13.9 | 12.9 | 9 | 0 |
| 2022 | Jimmy Garoppolo | QB | ecr_only | 64 | 10 | 15.2 | 15.0 | 11 | 0 |
| 2022 | Saquon Barkley | RB | model_only | 1 | 79 | 11.4 | 17.8 | 16 | 0 |
| 2022 | Cameron Brate | TE | model_only | 8 | 75 | 4.9 | 4.2 | 9 | 0 |
| 2023 | Cooper Rush | QB | ecr_only | 59 | 6 | 7.1 | 0.5 | 7 | 1 |
| 2023 | Devin Singletary | RB | ecr_only | 39 | 9 | 11.1 | 9.8 | 17 | 0 |
| 2023 | Irv Smith | TE | model_only | 4 | 79 | 6.9 | 3.4 | 10 | 1 |
| 2023 | Hayden Hurst | TE | model_only | 6 | 48 | 8.1 | 4.7 | 9 | 1 |
| 2024 | Jakobi Meyers | WR | ecr_only | 49 | 6 | 13.7 | 14.5 | 15 | 0 |
| 2024 | Russell Wilson | QB | ecr_only | 46 | 9 | 17.1 | 15.7 | 11 | 0 |
| 2024 | Noah Fant | TE | model_only | 8 | 77 | 4.9 | 7.4 | 14 | 0 |
| 2024 | Dawson Knox | TE | model_only | 10 | 37 | 5.5 | 4.2 | 14 | 0 |
| 2025 | Keenan Allen | WR | ecr_only | 60 | 5 | 12.3 | 10.7 | 17 | 0 |
| 2025 | Mac Jones | QB | ecr_only | 61 | 10 | 9.6 | 11.8 | 11 | 0 |
| 2025 | Evan Engram | TE | model_only | 10 | 73 | 9.9 | 6.4 | 16 | 1 |
| 2025 | Dallas Goedert | TE | model_only | 5 | 45 | 10.4 | 12.3 | 15 | 0 |

### Missed most of S+1 (under 6 games) (`cliff_missed`, label `y_missed`)

**snapshots 2007-2024 (labels 2008-2025)**

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit_simple` | 0.388 [0.330, 0.448] | 0.450 [0.378, 0.522] | 0.319 [0.272, 0.367] | 0.112 [0.101, 0.124] |
| `base_ppg_rank` | 0.242 [0.209, 0.281] | 0.283 [0.217, 0.350] | 0.264 [0.217, 0.311] | 0.122 [0.111, 0.135] |
| `eos` | 0.384 [0.330, 0.439] | 0.433 [0.361, 0.500] | 0.322 [0.269, 0.375] | 0.113 [0.102, 0.125] |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit_simple` - `base_ppg_rank` | +0.146 [0.080, 0.215] | +0.167 [0.089, 0.244] | +0.056 [-0.003, 0.114] | 1.00 |
| `logit_simple` - `eos` | +0.004 [-0.015, 0.029] | +0.017 [-0.017, 0.050] | -0.003 [-0.025, 0.019] | 0.70 |
| `base_ppg_rank` - `eos` | -0.142 [-0.204, -0.079] | -0.150 [-0.233, -0.061] | -0.058 [-0.114, -0.003] | 0.00 |

**snapshots 2019-2024 (labels 2020-2025), with the ECR**

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit_simple` | 0.362 [0.242, 0.507] | 0.417 [0.283, 0.583] | 0.300 [0.208, 0.408] | 0.097 [0.084, 0.112] |
| `base_ppg_rank` | 0.178 [0.123, 0.257] | 0.200 [0.100, 0.300] | 0.192 [0.108, 0.292] | 0.108 [0.089, 0.133] |
| `eos` | 0.352 [0.233, 0.484] | 0.383 [0.250, 0.533] | 0.283 [0.192, 0.408] | 0.099 [0.084, 0.116] |
| `ecr` | 0.570 [0.479, 0.662] | 0.517 [0.417, 0.650] | 0.358 [0.283, 0.459] | - |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit_simple` - `base_ppg_rank` | +0.185 [0.099, 0.273] | +0.217 [0.100, 0.350] | +0.108 [0.008, 0.200] | 1.00 |
| `logit_simple` - `eos` | +0.010 [-0.020, 0.054] | +0.033 [-0.033, 0.083] | +0.017 [0.000, 0.050] | 0.80 |
| `logit_simple` - `ecr` | -0.207 [-0.353, -0.094] | -0.100 [-0.200, -0.017] | -0.058 [-0.100, -0.017] | 0.00 |
| `base_ppg_rank` - `eos` | -0.174 [-0.246, -0.081] | -0.183 [-0.300, -0.067] | -0.092 [-0.183, 0.000] | 0.00 |
| `base_ppg_rank` - `ecr` | -0.392 [-0.490, -0.292] | -0.317 [-0.433, -0.200] | -0.167 [-0.250, -0.050] | 0.00 |

Top features (mean share of the importance over the 18 folds; logit = spread of the feature's log-odds contribution, sign from the 2024 fold's standardized coefficient; LightGBM = gain):

- `logit_simple`: `games_s` (-) 27%, `pos_wr` (-) 15%, `prior_seasons` (+) 12%, `touches_per_game_s` (-) 12%, `draft_qb_r1_pd` (+) 7%, `age` (+) 6%, `pos_rb` (+) 6%, `draft_pos_best_pick_pd` (-) 4%

Calibration of `logit_simple` (its own probability; 10 equal-count bins, every test season):

| bin | rows | mean predicted | observed | range |
|---|---|---|---|---|
| 1 | 168 | 0.037 | 0.048 | 0.019-0.047 |
| 2 | 167 | 0.056 | 0.072 | 0.048-0.064 |
| 3 | 168 | 0.073 | 0.089 | 0.064-0.082 |
| 4 | 167 | 0.091 | 0.084 | 0.082-0.100 |
| 5 | 167 | 0.108 | 0.084 | 0.100-0.116 |
| 6 | 168 | 0.127 | 0.119 | 0.116-0.142 |
| 7 | 167 | 0.160 | 0.102 | 0.142-0.180 |
| 8 | 168 | 0.210 | 0.232 | 0.180-0.241 |
| 9 | 167 | 0.280 | 0.192 | 0.242-0.332 |
| 10 | 167 | 0.461 | 0.479 | 0.333-0.827 |

Where the model and the market disagree most (snapshots 2019-2024, per season the top 10 of each; who was right = the label):

| group | players | label = 1 | rate |
|---|---|---|---|
| in both | 20 | 13 | 0.65 |
| in the ECR's top 10, not `logit_simple`'s | 40 | 18 | 0.45 |
| in `logit_simple`'s top 10, not the ECR's | 40 | 12 | 0.30 |

Largest rank gaps (2 per season and side):

| label season | player | pos | group | rank by model | rank by ECR score | PPG S | PPG S+1 | games S+1 | label |
|---|---|---|---|---|---|---|---|---|---|
| 2020 | Jameis Winston | QB | ecr_only | 60 | 4 | 19.1 | 0.8 | 3 | 1 |
| 2020 | Julian Edelman | WR | ecr_only | 51 | 8 | 16.0 | 9.4 | 6 | 0 |
| 2020 | Tom Brady | QB | model_only | 8 | 70 | 16.5 | 21.1 | 16 | 0 |
| 2020 | Teddy Bridgewater | QB | model_only | 10 | 72 | 10.1 | 16.1 | 15 | 0 |
| 2021 | Todd Gurley | RB | ecr_only | 56 | 5 | 10.9 | - | 0 | 1 |
| 2021 | Wayne Gallman | RB | ecr_only | 48 | 8 | 9.8 | 1.9 | 7 | 0 |
| 2021 | George Kittle | TE | model_only | 8 | 73 | 15.6 | 14.1 | 14 | 0 |
| 2021 | Ryan Fitzpatrick | QB | model_only | 3 | 60 | 17.1 | 0.7 | 1 | 1 |
| 2022 | Darrel Williams | RB | ecr_only | 75 | 10 | 11.5 | 4.2 | 5 | 1 |
| 2022 | Taylor Heinicke | QB | ecr_only | 61 | 7 | 13.9 | 12.9 | 9 | 0 |
| 2022 | Robert Tonyan | TE | model_only | 3 | 81 | 6.3 | 6.6 | 17 | 0 |
| 2022 | Tom Brady | QB | model_only | 9 | 38 | 22.0 | 16.0 | 17 | 0 |
| 2023 | Leonard Fournette | RB | ecr_only | 59 | 4 | 14.1 | 2.0 | 2 | 1 |
| 2023 | Ezekiel Elliott | RB | ecr_only | 48 | 9 | 12.4 | 10.3 | 17 | 0 |
| 2023 | Matthew Stafford | QB | model_only | 2 | 93 | 12.0 | 16.2 | 15 | 0 |
| 2023 | Darren Waller | TE | model_only | 3 | 85 | 10.6 | 9.4 | 12 | 0 |
| 2024 | Jakobi Meyers | WR | ecr_only | 81 | 10 | 13.7 | 14.5 | 15 | 0 |
| 2024 | Adam Thielen | WR | ecr_only | 62 | 5 | 13.6 | 13.9 | 10 | 0 |
| 2024 | Kyler Murray | QB | model_only | 7 | 71 | 18.3 | 17.5 | 17 | 0 |
| 2024 | Mark Andrews | TE | model_only | 10 | 66 | 13.5 | 11.1 | 17 | 0 |
| 2025 | Rico Dowdle | RB | ecr_only | 80 | 9 | 12.4 | 12.7 | 17 | 0 |
| 2025 | Jordan Akins | TE | ecr_only | 32 | 2 | 5.7 | - | 0 | 1 |
| 2025 | Dak Prescott | QB | model_only | 2 | 91 | 14.6 | 18.5 | 17 | 0 |
| 2025 | Evan Engram | TE | model_only | 6 | 85 | 9.9 | 6.4 | 16 | 0 |

### Cliff counting a missed season as a cliff (sensitivity) (`cliff_sensitivity`, label `y_cliff_or_missed`)

**snapshots 2007-2024 (labels 2008-2025)**

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit` | 0.534 [0.490, 0.580] | 0.656 [0.589, 0.711] | 0.592 [0.539, 0.645] | 0.206 [0.199, 0.215] |
| `lgbm` | 0.520 [0.470, 0.575] | 0.622 [0.539, 0.700] | 0.556 [0.500, 0.606] | 0.207 [0.201, 0.214] |
| `base_ppg_rank` | 0.433 [0.400, 0.468] | 0.478 [0.411, 0.544] | 0.483 [0.444, 0.517] | 0.221 [0.215, 0.228] |
| `eos` | 0.535 [0.490, 0.579] | 0.650 [0.583, 0.706] | 0.589 [0.536, 0.639] | 0.206 [0.198, 0.214] |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | +0.102 [0.059, 0.141] | +0.178 [0.089, 0.261] | +0.108 [0.053, 0.161] | 1.00 |
| `logit` - `eos` | -0.000 [-0.009, 0.009] | +0.006 [-0.039, 0.044] | +0.003 [-0.019, 0.022] | 0.51 |
| `lgbm` - `base_ppg_rank` | +0.087 [0.052, 0.128] | +0.144 [0.039, 0.239] | +0.072 [0.031, 0.114] | 1.00 |
| `lgbm` - `eos` | -0.015 [-0.041, 0.011] | -0.028 [-0.089, 0.033] | -0.033 [-0.072, 0.006] | 0.14 |
| `base_ppg_rank` - `eos` | -0.102 [-0.139, -0.060] | -0.172 [-0.261, -0.078] | -0.106 [-0.156, -0.058] | 0.00 |

**snapshots 2019-2024 (labels 2020-2025), with the ECR**

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit` | 0.554 [0.475, 0.622] | 0.683 [0.567, 0.783] | 0.625 [0.558, 0.708] | 0.200 [0.189, 0.211] |
| `lgbm` | 0.529 [0.426, 0.639] | 0.633 [0.467, 0.800] | 0.542 [0.433, 0.633] | 0.200 [0.192, 0.208] |
| `base_ppg_rank` | 0.417 [0.350, 0.492] | 0.500 [0.400, 0.583] | 0.450 [0.367, 0.533] | 0.220 [0.207, 0.231] |
| `eos` | 0.546 [0.473, 0.610] | 0.667 [0.550, 0.750] | 0.642 [0.575, 0.700] | 0.201 [0.191, 0.212] |
| `ecr` | 0.689 [0.637, 0.736] | 0.850 [0.783, 0.933] | 0.642 [0.558, 0.725] | - |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | +0.137 [0.100, 0.177] | +0.183 [0.100, 0.267] | +0.175 [0.100, 0.250] | 1.00 |
| `logit` - `eos` | +0.009 [-0.006, 0.021] | +0.017 [-0.033, 0.067] | -0.017 [-0.042, 0.017] | 0.87 |
| `logit` - `ecr` | -0.135 [-0.189, -0.069] | -0.167 [-0.267, -0.067] | -0.017 [-0.083, 0.042] | 0.00 |
| `lgbm` - `base_ppg_rank` | +0.113 [0.044, 0.200] | +0.133 [-0.050, 0.317] | +0.092 [0.025, 0.175] | 1.00 |
| `lgbm` - `eos` | -0.016 [-0.061, 0.042] | -0.033 [-0.150, 0.083] | -0.100 [-0.142, -0.067] | 0.28 |
| `lgbm` - `ecr` | -0.160 [-0.250, -0.073] | -0.217 [-0.367, -0.067] | -0.100 [-0.167, -0.033] | 0.00 |
| `base_ppg_rank` - `eos` | -0.129 [-0.175, -0.087] | -0.167 [-0.233, -0.100] | -0.192 [-0.250, -0.133] | 0.00 |
| `base_ppg_rank` - `ecr` | -0.272 [-0.337, -0.197] | -0.350 [-0.467, -0.217] | -0.192 [-0.275, -0.083] | 0.00 |

Top features (mean share of the importance over the 18 folds; logit = spread of the feature's log-odds contribution, sign from the 2024 fold's standardized coefficient; LightGBM = gain):

- `logit`: `games_s` (-) 12%, `ppg_change` (+) 10%, `ppg_s` (-) 7%, `age` (+) 6%, `prior_seasons` (+) 6%, `pos_rank_s` (+) 5%, `pos_rb` (+) 5%, `draft_qb_r1_pd` (+) 4%
- `lgbm`: `games_s` 14%, `ppg_s` 13%, `ppg_change` 12%, `age_curve_ratio` 10%, `age` 8%, `pos_rank_s` 7%, `yards_per_touch_s` 7%, `touches_per_game_s` 6%

Calibration of `logit` (its own probability; 10 equal-count bins, every test season):

| bin | rows | mean predicted | observed | range |
|---|---|---|---|---|
| 1 | 168 | 0.137 | 0.185 | 0.060-0.173 |
| 2 | 167 | 0.196 | 0.210 | 0.174-0.217 |
| 3 | 168 | 0.237 | 0.202 | 0.217-0.257 |
| 4 | 167 | 0.274 | 0.228 | 0.257-0.291 |
| 5 | 167 | 0.310 | 0.323 | 0.291-0.331 |
| 6 | 168 | 0.349 | 0.345 | 0.331-0.366 |
| 7 | 167 | 0.392 | 0.425 | 0.366-0.420 |
| 8 | 168 | 0.453 | 0.411 | 0.421-0.486 |
| 9 | 167 | 0.523 | 0.545 | 0.486-0.572 |
| 10 | 167 | 0.658 | 0.641 | 0.572-0.953 |

Where the model and the market disagree most (snapshots 2019-2024, per season the top 10 of each; who was right = the label):

| group | players | label = 1 | rate |
|---|---|---|---|
| in both | 28 | 26 | 0.93 |
| in the ECR's top 10, not `logit`'s | 32 | 25 | 0.78 |
| in `logit`'s top 10, not the ECR's | 32 | 15 | 0.47 |

Largest rank gaps (2 per season and side):

| label season | player | pos | group | rank by model | rank by ECR score | PPG S | PPG S+1 | games S+1 | label |
|---|---|---|---|---|---|---|---|---|---|
| 2020 | Jameis Winston | QB | ecr_only | 75 | 4 | 19.1 | 0.8 | 3 | 1 |
| 2020 | Julian Edelman | WR | ecr_only | 55 | 8 | 16.0 | 9.4 | 6 | 1 |
| 2020 | T.Y. Hilton | WR | model_only | 9 | 75 | 12.5 | 10.9 | 15 | 0 |
| 2020 | Le'Veon Bell | RB | model_only | 5 | 45 | 14.3 | 6.8 | 11 | 1 |
| 2021 | Philip Rivers | QB | ecr_only | 54 | 3 | 15.0 | - | 0 | 1 |
| 2021 | Drew Brees | QB | ecr_only | 37 | 2 | 17.5 | - | 0 | 1 |
| 2021 | Mike Davis | RB | model_only | 9 | 53 | 13.8 | 8.1 | 17 | 1 |
| 2021 | Kyle Rudolph | TE | model_only | 8 | 42 | 5.5 | 4.0 | 15 | 0 |
| 2022 | Taylor Heinicke | QB | ecr_only | 64 | 7 | 13.9 | 12.9 | 9 | 0 |
| 2022 | Ben Roethlisberger | QB | ecr_only | 30 | 4 | 13.6 | - | 0 | 1 |
| 2022 | Saquon Barkley | RB | model_only | 2 | 87 | 11.4 | 17.8 | 16 | 0 |
| 2022 | Jacoby Brissett | QB | model_only | 6 | 72 | 7.2 | 12.0 | 14 | 0 |
| 2023 | Leonard Fournette | RB | ecr_only | 62 | 4 | 14.1 | 2.0 | 2 | 1 |
| 2023 | Ezekiel Elliott | RB | ecr_only | 39 | 9 | 12.4 | 10.3 | 17 | 0 |
| 2023 | Irv Smith | TE | model_only | 1 | 89 | 6.9 | 3.4 | 10 | 1 |
| 2023 | Logan Thomas | TE | model_only | 9 | 60 | 5.9 | 7.9 | 16 | 0 |
| 2024 | Jakobi Meyers | WR | ecr_only | 72 | 10 | 13.7 | 14.5 | 15 | 0 |
| 2024 | Keenan Allen | WR | ecr_only | 42 | 8 | 21.5 | 12.3 | 15 | 1 |
| 2024 | Aaron Jones | RB | model_only | 9 | 80 | 12.3 | 14.2 | 17 | 0 |
| 2024 | Zack Moss | RB | model_only | 7 | 35 | 12.1 | 10.2 | 8 | 0 |
| 2025 | Keenan Allen | WR | ecr_only | 64 | 10 | 12.3 | 10.7 | 17 | 0 |
| 2025 | Rico Dowdle | RB | ecr_only | 37 | 9 | 12.4 | 12.7 | 17 | 0 |
| 2025 | Dallas Goedert | TE | model_only | 8 | 56 | 10.4 | 12.3 | 15 | 0 |
| 2025 | Jameis Winston | QB | model_only | 2 | 32 | 11.9 | 14.4 | 3 | 1 |

## Read before trusting

- Intervals resample whole seasons (18 in `all`, 6 in `ecr_era`): the ECR-era intervals are wide. Precision@k is the mean over seasons of the share of the season's top k with the label.
- Probabilities are each model's own (no isotonic step); the PPG-rank baseline and the ECR are rankings first.
- Inputs missing in early seasons (NGS before 2016, RYOE before 2018, snap counts before 2013, own xFP before 2009) are imputed inside each fit with a missing indicator.
- Head-coach departures: the owner's cited research file (accepted in bulk, 2026-10-01); a blank date counts as known at the snapshot for fired / mutual / interim types only.
