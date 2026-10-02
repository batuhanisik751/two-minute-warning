# Breakout backtest at the preseason snapshot, anchor week1_kickoff_eve (step I2a / I2-fix)

Step I2a / I2-fix: the same rows and labels as the end-of-season report, read at the PRESEASON snapshot, anchor `week1_kickoff_eve` (one hour before the first regular-season week-1 kickoff of S+1: the board's anchor (owner decision 2026-10-02); point in time): every I1b feature plus the week-1 depth charts visible then (team change, depth rank, new competition, QB1 change, vacated targets and carries), a head-coach change of the week-1 team, `dc_absent` (on no chart while his team's chart is visible) and `dc_team_chart_missing` (his team has no visible chart: `dc_absent` is then missing, never a cut; docs/board.md, "Preseason snapshot"). `eos` = the end-of-season report's primary model on the same rows; the ECR (2020+) is the last August/September scrape before week 1 (when it became public vs this anchor: the table below).

ECR timing (negative = the ECR is public only after the as-of):

| snapshot S | ECR scrape of S+1 | public at (UTC) | snapshot as-of (UTC) | ECR public before the as-of by (days) |
|---|---|---|---|---|
| 2019 | 2020-09-03 | 2020-09-04 00:00 | 2020-09-10 Thu 23:20 | +7.0 |
| 2020 | 2021-09-03 | 2021-09-04 00:00 | 2021-09-09 Thu 23:20 | +6.0 |
| 2021 | 2022-09-02 | 2022-09-03 00:00 | 2022-09-08 Thu 23:20 | +6.0 |
| 2022 | 2023-09-01 | 2023-09-02 00:00 | 2023-09-07 Thu 23:20 | +6.0 |
| 2023 | 2024-08-30 | 2024-08-31 00:00 | 2024-09-05 Thu 23:20 | +6.0 |
| 2024 | 2025-08-29 | 2025-08-30 00:00 | 2025-09-04 Thu 23:20 | +6.0 |

Rows and positives per test season (label season = S + 1): `breakout_wr_te` 81 positives (2-7 a season); `breakout_rb` 59 positives (0-9 a season).

| snapshot S | breakout_wr_te rows | breakout_wr_te positives | breakout_rb rows | breakout_rb positives |
|---|---|---|---|---|
| 2007 | 80 | 4 | 36 | 2 |
| 2008 | 81 | 6 | 40 | 5 |
| 2009 | 93 | 7 | 44 | 6 |
| 2010 | 103 | 2 | 37 | 2 |
| 2011 | 107 | 7 | 44 | 2 |
| 2012 | 108 | 6 | 55 | 1 |
| 2013 | 110 | 2 | 50 | 5 |
| 2014 | 93 | 5 | 48 | 4 |
| 2015 | 101 | 4 | 51 | 5 |
| 2016 | 116 | 3 | 59 | 3 |
| 2017 | 104 | 6 | 53 | 9 |
| 2018 | 115 | 6 | 47 | 1 |
| 2019 | 115 | 5 | 58 | 4 |
| 2020 | 105 | 4 | 54 | 2 |
| 2021 | 106 | 4 | 53 | 1 |
| 2022 | 103 | 3 | 49 | 6 |
| 2023 | 103 | 3 | 41 | 1 |
| 2024 | 107 | 4 | 47 | 0 |

## Results

### Breakout WR/TE (`breakout_wr_te`, label `y_breakout`)

**snapshots 2007-2024 (labels 2008-2025)**

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit` | 0.257 [0.187, 0.385] | 0.244 [0.200, 0.294] | 0.178 [0.150, 0.206] | 0.037 [0.030, 0.044] |
| `lgbm` | 0.221 [0.171, 0.301] | 0.261 [0.211, 0.311] | 0.169 [0.144, 0.194] | 0.038 [0.031, 0.045] |
| `base_ppg_rank` | 0.251 [0.197, 0.336] | 0.244 [0.206, 0.289] | 0.164 [0.136, 0.192] | 0.037 [0.031, 0.043] |
| `eos` | 0.235 [0.170, 0.362] | 0.239 [0.200, 0.289] | 0.167 [0.142, 0.192] | 0.037 [0.030, 0.045] |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | +0.006 [-0.061, 0.099] | +0.000 [-0.039, 0.039] | +0.014 [-0.014, 0.039] | 0.56 |
| `logit` - `eos` | +0.022 [-0.014, 0.054] | +0.006 [-0.017, 0.028] | +0.011 [-0.003, 0.025] | 0.88 |
| `lgbm` - `base_ppg_rank` | -0.030 [-0.098, 0.027] | +0.017 [-0.017, 0.050] | +0.006 [-0.017, 0.028] | 0.16 |
| `lgbm` - `eos` | -0.014 [-0.109, 0.056] | +0.022 [-0.017, 0.061] | +0.003 [-0.011, 0.017] | 0.35 |
| `base_ppg_rank` - `eos` | +0.016 [-0.088, 0.096] | +0.006 [-0.039, 0.056] | -0.003 [-0.028, 0.022] | 0.62 |

**snapshots 2019-2024 (labels 2020-2025), with the ECR**

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit` | 0.352 [0.184, 0.640] | 0.233 [0.117, 0.350] | 0.175 [0.142, 0.208] | 0.028 [0.022, 0.034] |
| `lgbm` | 0.250 [0.144, 0.470] | 0.283 [0.183, 0.383] | 0.167 [0.117, 0.208] | 0.030 [0.027, 0.034] |
| `base_ppg_rank` | 0.259 [0.160, 0.451] | 0.217 [0.167, 0.267] | 0.150 [0.117, 0.192] | 0.031 [0.029, 0.032] |
| `eos` | 0.343 [0.148, 0.646] | 0.267 [0.167, 0.367] | 0.158 [0.100, 0.208] | 0.028 [0.022, 0.035] |
| `ecr` | 0.315 [0.217, 0.439] | 0.267 [0.200, 0.333] | 0.167 [0.133, 0.208] | - |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | +0.093 [-0.039, 0.266] | +0.017 [-0.067, 0.083] | +0.025 [-0.009, 0.058] | 0.89 |
| `logit` - `eos` | +0.008 [-0.054, 0.082] | -0.033 [-0.067, 0.000] | +0.017 [0.000, 0.050] | 0.57 |
| `logit` - `ecr` | +0.037 [-0.050, 0.255] | -0.033 [-0.117, 0.033] | +0.008 [-0.025, 0.050] | 0.68 |
| `lgbm` - `base_ppg_rank` | -0.009 [-0.113, 0.088] | +0.067 [0.017, 0.133] | +0.017 [-0.033, 0.058] | 0.41 |
| `lgbm` - `eos` | -0.093 [-0.267, 0.074] | +0.017 [-0.067, 0.100] | +0.008 [-0.025, 0.050] | 0.14 |
| `lgbm` - `ecr` | -0.065 [-0.129, 0.038] | +0.017 [-0.033, 0.067] | +0.000 [-0.050, 0.050] | 0.10 |
| `base_ppg_rank` - `eos` | -0.084 [-0.288, 0.072] | -0.050 [-0.100, 0.017] | -0.008 [-0.058, 0.050] | 0.19 |
| `base_ppg_rank` - `ecr` | -0.056 [-0.137, 0.048] | -0.050 [-0.083, -0.017] | -0.017 [-0.033, 0.000] | 0.29 |

Top features (mean share of the importance over the 18 folds; logit = spread of the feature's log-odds contribution, sign from the 2024 fold's standardized coefficient; LightGBM = gain):

- `logit`: `games_s` (+) 8%, `pos_te` (+) 7%, `ppg_s` (+) 7%, `yards_per_team_pass_att_s` (+) 6%, `drafted_round` (-) 6%, `depth_rank_s1` (-) 5%, `touches_per_game_s` (+) 5%, `drafted_pick` (-) 4%
- `lgbm`: `ppg_s` 29%, `games_s` 13%, `drafted_pick` 11%, `yards_per_team_pass_att_s` 6%, `combine_speed_score` 5%, `vacated_carries_share_s1` 4%, `age` 3%, `combine_weight` 3%

Importance smoke test (spec 6.2 rule 6), folds whose top feature holds over 40%: lgbm 2009: ppg_s 59%; lgbm 2010: ppg_s 41%

Calibration of `logit` (its own probability; 10 equal-count bins, every test season):

| bin | rows | mean predicted | observed | range |
|---|---|---|---|---|
| 1 | 185 | 0.001 | 0.000 | 0.000-0.002 |
| 2 | 185 | 0.003 | 0.000 | 0.002-0.005 |
| 3 | 185 | 0.006 | 0.005 | 0.005-0.007 |
| 4 | 185 | 0.009 | 0.005 | 0.007-0.011 |
| 5 | 185 | 0.014 | 0.011 | 0.011-0.016 |
| 6 | 185 | 0.020 | 0.011 | 0.016-0.025 |
| 7 | 185 | 0.031 | 0.022 | 0.025-0.039 |
| 8 | 185 | 0.052 | 0.054 | 0.039-0.067 |
| 9 | 185 | 0.101 | 0.086 | 0.067-0.149 |
| 10 | 185 | 0.272 | 0.243 | 0.149-0.789 |

Where the model and the market disagree most (snapshots 2019-2024, per season the top 10 of each; who was right = the label):

| group | players | label = 1 | rate |
|---|---|---|---|
| in both | 36 | 11 | 0.31 |
| in the ECR's top 10, not `logit`'s | 24 | 5 | 0.21 |
| in `logit`'s top 10, not the ECR's | 24 | 3 | 0.12 |

Largest rank gaps (2 per season and side):

| label season | player | pos | group | rank by model | rank by ECR score | PPG S | PPG S+1 | games S+1 | label |
|---|---|---|---|---|---|---|---|---|---|
| 2020 | Jace Sternberger | TE | ecr_only | 66 | 9 | 0.0 | 3.7 | 8 | 0 |
| 2020 | Chris Herndon | TE | ecr_only | 48 | 5 | 1.7 | 6.1 | 12 | 0 |
| 2020 | James Washington | WR | model_only | 8 | 27 | 8.9 | 6.2 | 16 | 0 |
| 2020 | Preston Williams | WR | model_only | 7 | 17 | 11.3 | 9.3 | 8 | 0 |
| 2021 | Juwan Johnson | WR | ecr_only | 66 | 5 | 1.6 | 4.1 | 13 | 0 |
| 2021 | Donald Parham | TE | ecr_only | 25 | 8 | 4.0 | 4.5 | 13 | 0 |
| 2021 | Irv Smith | TE | model_only | 8 | 68 | 7.6 | - | 0 | 0 |
| 2021 | Denzel Mims | WR | model_only | 9 | 34 | 6.7 | 2.1 | 10 | 0 |
| 2022 | Brevin Jordan | TE | ecr_only | 23 | 4 | 7.0 | 3.0 | 9 | 0 |
| 2022 | Gabe Davis | WR | ecr_only | 16 | 8 | 8.4 | 11.4 | 15 | 0 |
| 2022 | Nico Collins | WR | model_only | 10 | 20 | 6.0 | 9.7 | 10 | 0 |
| 2022 | Chase Claypool | WR | model_only | 7 | 14 | 11.1 | 7.0 | 15 | 0 |
| 2023 | Jake Ferguson | TE | ecr_only | 27 | 5 | 4.4 | 10.4 | 17 | 1 |
| 2023 | Jelani Woods | TE | ecr_only | 29 | 9 | 6.2 | - | 0 | 0 |
| 2023 | Josh Palmer | WR | model_only | 10 | 39 | 10.6 | 10.7 | 10 | 0 |
| 2023 | Alec Pierce | WR | model_only | 6 | 30 | 7.0 | 5.6 | 17 | 0 |
| 2024 | Greg Dulcich | TE | ecr_only | 31 | 9 | 2.8 | 1.9 | 4 | 0 |
| 2024 | Tucker Kraft | TE | ecr_only | 16 | 8 | 5.6 | 9.6 | 17 | 0 |
| 2024 | Josh Downs | WR | model_only | 6 | 22 | 9.2 | 13.1 | 14 | 0 |
| 2024 | Romeo Doubs | WR | model_only | 10 | 17 | 10.3 | 10.2 | 13 | 0 |
| 2025 | Ja'Tavion Sanders | TE | ecr_only | 16 | 6 | 4.9 | 4.5 | 12 | 0 |
| 2025 | Theo Johnson | TE | ecr_only | 12 | 7 | 6.2 | 8.5 | 15 | 0 |
| 2025 | Quentin Johnston | WR | model_only | 5 | 28 | 11.6 | 13.2 | 13 | 1 |
| 2025 | Xavier Legette | WR | model_only | 9 | 26 | 8.3 | 6.0 | 15 | 0 |

### Breakout RB (`breakout_rb`, label `y_breakout`)

**snapshots 2007-2024 (labels 2008-2025)**

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit` | 0.274 [0.189, 0.413] | 0.233 [0.156, 0.317] | 0.144 [0.100, 0.194] | 0.056 [0.043, 0.069] |
| `lgbm` | 0.275 [0.177, 0.422] | 0.206 [0.133, 0.289] | 0.147 [0.103, 0.197] | 0.056 [0.041, 0.070] |
| `base_ppg_rank` | 0.186 [0.138, 0.270] | 0.206 [0.139, 0.272] | 0.136 [0.100, 0.172] | 0.059 [0.044, 0.076] |
| `eos` | 0.234 [0.146, 0.359] | 0.211 [0.139, 0.289] | 0.142 [0.094, 0.194] | 0.058 [0.044, 0.073] |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | +0.088 [0.009, 0.183] | +0.028 [-0.011, 0.067] | +0.008 [-0.008, 0.025] | 0.98 |
| `logit` - `eos` | +0.040 [-0.045, 0.116] | +0.022 [0.000, 0.050] | +0.003 [-0.011, 0.019] | 0.83 |
| `lgbm` - `base_ppg_rank` | +0.089 [-0.014, 0.207] | -0.000 [-0.033, 0.028] | +0.011 [-0.006, 0.028] | 0.95 |
| `lgbm` - `eos` | +0.042 [-0.034, 0.119] | -0.006 [-0.028, 0.011] | +0.006 [-0.008, 0.025] | 0.85 |
| `base_ppg_rank` - `eos` | -0.048 [-0.148, 0.054] | -0.006 [-0.044, 0.028] | -0.006 [-0.031, 0.017] | 0.19 |

**snapshots 2019-2024 (labels 2020-2025), with the ECR**

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit` | 0.302 [0.134, 0.608] | 0.183 [0.083, 0.317] | 0.100 [0.042, 0.183] | 0.038 [0.021, 0.055] |
| `lgbm` | 0.368 [0.075, 0.704] | 0.150 [0.033, 0.300] | 0.100 [0.042, 0.183] | 0.036 [0.019, 0.053] |
| `base_ppg_rank` | 0.161 [0.084, 0.274] | 0.150 [0.050, 0.267] | 0.100 [0.042, 0.167] | 0.042 [0.021, 0.066] |
| `eos` | 0.335 [0.069, 0.464] | 0.167 [0.050, 0.317] | 0.100 [0.042, 0.183] | 0.041 [0.023, 0.060] |
| `ecr` | 0.378 [0.141, 0.791] | 0.183 [0.083, 0.317] | 0.100 [0.042, 0.183] | - |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | +0.142 [0.031, 0.353] | +0.033 [0.000, 0.067] | 0.000 [-0.025, 0.025] | 1.00 |
| `logit` - `eos` | -0.032 [-0.220, 0.249] | +0.017 [0.000, 0.050] | 0.000 [0.000, 0.000] | 0.52 |
| `logit` - `ecr` | -0.075 [-0.331, 0.048] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.13 |
| `lgbm` - `base_ppg_rank` | +0.207 [-0.038, 0.502] | -0.000 [-0.050, 0.050] | 0.000 [-0.025, 0.025] | 0.94 |
| `lgbm` - `eos` | +0.033 [-0.227, 0.342] | -0.017 [-0.050, 0.000] | 0.000 [0.000, 0.000] | 0.61 |
| `lgbm` - `ecr` | -0.010 [-0.300, 0.192] | -0.033 [-0.067, 0.000] | 0.000 [0.000, 0.000] | 0.38 |
| `base_ppg_rank` - `eos` | -0.174 [-0.296, 0.051] | -0.017 [-0.050, 0.000] | 0.000 [-0.025, 0.025] | 0.09 |
| `base_ppg_rank` - `ecr` | -0.217 [-0.562, -0.027] | -0.033 [-0.067, 0.000] | 0.000 [-0.025, 0.025] | 0.00 |

Top features (mean share of the importance over the 18 folds; logit = spread of the feature's log-odds contribution, sign from the 2024 fold's standardized coefficient; LightGBM = gain):

- `logit`: `depth_rank_s1` (-) 8%, `ppg_s` (+) 5%, `drafted_pick` (-) 4%, `target_share_s` (-) 4%, `vacated_carries_share_s1` (+) 4%, `air_yards_share_s` (+) 4%, `new_competitor_s1` (-) 4%, `yards_per_team_pass_att_s` (+) 4%
- `lgbm`: `depth_rank_s1` 23%, `drafted_pick` 16%, `touches_per_game_s` 9%, `ppg_s` 7%, `yards_per_team_pass_att_s` 7%, `combine_speed_score` 6%, `team_any_a` 4%, `vacated_carries_share_s1` 3%

Importance smoke test (spec 6.2 rule 6), folds whose top feature holds over 40%: lgbm 2020: depth_rank_s1 54%; lgbm 2022: depth_rank_s1 44%

Calibration of `logit` (its own probability; 10 equal-count bins, every test season):

| bin | rows | mean predicted | observed | range |
|---|---|---|---|---|
| 1 | 87 | 0.006 | 0.000 | 0.001-0.010 |
| 2 | 87 | 0.013 | 0.000 | 0.010-0.016 |
| 3 | 86 | 0.019 | 0.000 | 0.016-0.022 |
| 4 | 87 | 0.026 | 0.000 | 0.022-0.031 |
| 5 | 86 | 0.037 | 0.023 | 0.031-0.043 |
| 6 | 87 | 0.052 | 0.034 | 0.043-0.063 |
| 7 | 87 | 0.073 | 0.034 | 0.063-0.087 |
| 8 | 86 | 0.105 | 0.116 | 0.087-0.129 |
| 9 | 87 | 0.165 | 0.184 | 0.130-0.214 |
| 10 | 86 | 0.318 | 0.291 | 0.217-0.630 |

Where the model and the market disagree most (snapshots 2019-2024, per season the top 10 of each; who was right = the label):

| group | players | label = 1 | rate |
|---|---|---|---|
| in both | 46 | 11 | 0.24 |
| in the ECR's top 10, not `logit`'s | 14 | 0 | 0.00 |
| in `logit`'s top 10, not the ECR's | 14 | 0 | 0.00 |

Largest rank gaps (2 per season and side):

| label season | player | pos | group | rank by model | rank by ECR score | PPG S | PPG S+1 | games S+1 | label |
|---|---|---|---|---|---|---|---|---|---|
| 2020 | Phillip Lindsay | RB | ecr_only | 16 | 3 | 12.4 | 6.0 | 11 | 0 |
| 2020 | Nyheim Hines | RB | ecr_only | 15 | 9 | 7.4 | 12.1 | 16 | 0 |
| 2020 | Royce Freeman | RB | model_only | 7 | 20 | 8.9 | 3.4 | 11 | 0 |
| 2020 | Jaylen Samuels | RB | model_only | 9 | 21 | 8.0 | 1.8 | 9 | 0 |
| 2021 | Darrynton Evans | RB | ecr_only | 22 | 9 | 3.2 | 3.8 | 1 | 0 |
| 2021 | Ty Johnson | RB | ecr_only | 16 | 10 | 5.8 | 7.8 | 15 | 0 |
| 2021 | Benny Snell | RB | model_only | 7 | 13 | 5.0 | 0.8 | 16 | 0 |
| 2021 | Joshua Kelley | RB | model_only | 8 | 12 | 6.2 | 2.1 | 8 | 0 |
| 2022 | Chris Evans | RB | ecr_only | 18 | 10 | 5.5 | 1.3 | 10 | 0 |
| 2022 | Chuba Hubbard | RB | ecr_only | 13 | 9 | 8.6 | 6.3 | 14 | 0 |
| 2022 | Zack Moss | RB | model_only | 7 | 12 | 8.1 | 5.0 | 13 | 0 |
| 2022 | Eno Benjamin | RB | model_only | 9 | 13 | 3.5 | 6.9 | 13 | 0 |
| 2023 | Jaylen Warren | RB | ecr_only | 17 | 8 | 5.8 | 11.6 | 17 | 0 |
| 2023 | Elijah Mitchell | RB | ecr_only | 14 | 9 | 8.7 | 4.3 | 11 | 0 |
| 2023 | Tyler Allgeier | RB | model_only | 6 | 11 | 10.0 | 8.1 | 17 | 0 |
| 2023 | Chuba Hubbard | RB | model_only | 9 | 12 | 6.3 | 10.7 | 17 | 0 |
| 2024 | Jaleel McLaughlin | RB | ecr_only | 14 | 7 | 6.2 | 6.1 | 16 | 0 |
| 2024 | Roschon Johnson | RB | ecr_only | 11 | 10 | 6.8 | 6.0 | 13 | 0 |
| 2024 | Tank Bigsby | RB | model_only | 8 | 13 | 1.8 | 8.1 | 16 | 0 |
| 2024 | Dameon Pierce | RB | model_only | 10 | 12 | 5.9 | 4.3 | 10 | 0 |
| 2025 | Tyjae Spears | RB | ecr_only | 30 | 6 | 9.5 | 8.6 | 13 | 0 |
| 2025 | Ray Davis | RB | ecr_only | 11 | 7 | 6.8 | 3.8 | 17 | 0 |
| 2025 | Jaylen Wright | RB | model_only | 5 | 11 | 2.1 | 5.4 | 9 | 0 |
| 2025 | Will Shipley | RB | model_only | 10 | 12 | 1.2 | 1.2 | 14 | 0 |

## Read before trusting

- Intervals resample whole seasons (18 in `all`, 6 in `ecr_era`): the ECR-era intervals are wide. Precision@k is the mean over seasons of the share of the season's top k with the label.
- Probabilities are each model's own (no isotonic step); the PPG-rank baseline and the ECR are rankings first.
- Inputs missing in early seasons (NGS before 2016, RYOE before 2018, snap counts before 2013, own xFP before 2009) are imputed inside each fit with a missing indicator.
- Head-coach departures: the owner's cited research file (accepted in bulk, 2026-10-01); a blank date counts as known at the snapshot for fired / mutual / interim types only.
