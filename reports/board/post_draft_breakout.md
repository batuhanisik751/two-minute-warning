# Breakout backtest at the post-draft snapshot (step I2b)

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
| `logit` | 0.231 [0.174, 0.334] | 0.222 [0.178, 0.272] | 0.172 [0.142, 0.203] | 0.038 [0.031, 0.045] |
| `lgbm` | 0.209 [0.164, 0.283] | 0.244 [0.206, 0.289] | 0.175 [0.147, 0.203] | 0.038 [0.031, 0.045] |
| `base_ppg_rank` | 0.251 [0.197, 0.336] | 0.244 [0.206, 0.289] | 0.164 [0.136, 0.192] | 0.037 [0.031, 0.043] |
| `eos` | 0.235 [0.170, 0.362] | 0.239 [0.200, 0.289] | 0.167 [0.142, 0.192] | 0.037 [0.030, 0.045] |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | -0.019 [-0.091, 0.054] | -0.022 [-0.072, 0.022] | +0.008 [-0.019, 0.033] | 0.31 |
| `logit` - `eos` | -0.004 [-0.050, 0.022] | -0.017 [-0.033, 0.000] | +0.006 [-0.006, 0.017] | 0.38 |
| `lgbm` - `base_ppg_rank` | -0.042 [-0.112, 0.011] | -0.000 [-0.039, 0.039] | +0.011 [-0.014, 0.033] | 0.06 |
| `lgbm` - `eos` | -0.026 [-0.115, 0.038] | +0.006 [-0.039, 0.050] | +0.008 [-0.011, 0.028] | 0.19 |
| `base_ppg_rank` - `eos` | +0.016 [-0.088, 0.096] | +0.006 [-0.039, 0.056] | -0.003 [-0.028, 0.022] | 0.62 |

**snapshots 2019-2024 (labels 2020-2025), with the ECR**

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit` | 0.319 [0.182, 0.526] | 0.250 [0.117, 0.367] | 0.158 [0.100, 0.208] | 0.029 [0.025, 0.034] |
| `lgbm` | 0.248 [0.158, 0.437] | 0.267 [0.183, 0.367] | 0.175 [0.142, 0.208] | 0.030 [0.027, 0.034] |
| `base_ppg_rank` | 0.259 [0.160, 0.451] | 0.217 [0.167, 0.267] | 0.150 [0.117, 0.192] | 0.031 [0.029, 0.032] |
| `eos` | 0.343 [0.148, 0.646] | 0.267 [0.167, 0.367] | 0.158 [0.100, 0.208] | 0.028 [0.022, 0.035] |
| `ecr` | 0.315 [0.217, 0.439] | 0.267 [0.200, 0.333] | 0.167 [0.133, 0.208] | - |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | +0.059 [-0.041, 0.167] | +0.033 [-0.033, 0.100] | +0.008 [-0.050, 0.058] | 0.81 |
| `logit` - `eos` | -0.025 [-0.143, 0.067] | -0.017 [-0.050, 0.000] | 0.000 [-0.025, 0.025] | 0.30 |
| `logit` - `ecr` | +0.003 [-0.062, 0.116] | -0.017 [-0.100, 0.067] | -0.008 [-0.058, 0.042] | 0.56 |
| `lgbm` - `base_ppg_rank` | -0.011 [-0.093, 0.046] | +0.050 [0.000, 0.117] | +0.025 [-0.009, 0.058] | 0.37 |
| `lgbm` - `eos` | -0.096 [-0.278, 0.046] | -0.000 [-0.083, 0.100] | +0.017 [0.000, 0.050] | 0.12 |
| `lgbm` - `ecr` | -0.067 [-0.115, 0.001] | -0.000 [-0.067, 0.067] | +0.008 [-0.025, 0.050] | 0.03 |
| `base_ppg_rank` - `eos` | -0.084 [-0.288, 0.072] | -0.050 [-0.100, 0.017] | -0.008 [-0.058, 0.050] | 0.19 |
| `base_ppg_rank` - `ecr` | -0.056 [-0.137, 0.048] | -0.050 [-0.083, -0.017] | -0.017 [-0.033, 0.000] | 0.29 |

Top features (mean share of the importance over the 18 folds; logit = spread of the feature's log-odds contribution, sign from the 2024 fold's standardized coefficient; LightGBM = gain):

- `logit`: `games_s` (+) 11%, `pos_te` (+) 8%, `ppg_s` (+) 8%, `drafted_round` (-) 7%, `yards_per_team_pass_att_s` (+) 7%, `touches_per_game_s` (+) 5%, `drafted_pick` (-) 5%, `air_yards_share_s` (+) 4%
- `lgbm`: `ppg_s` 31%, `games_s` 14%, `drafted_pick` 13%, `yards_per_team_pass_att_s` 6%, `combine_speed_score` 5%, `combine_weight` 4%, `age` 3%, `pos_te` 3%

Importance smoke test (spec 6.2 rule 6), folds whose top feature holds over 40%: lgbm 2009: ppg_s 59%; lgbm 2012: ppg_s 41%; lgbm 2016: ppg_s 40%; lgbm 2018: ppg_s 40%

Calibration of `logit` (its own probability; 10 equal-count bins, every test season):

| bin | rows | mean predicted | observed | range |
|---|---|---|---|---|
| 1 | 185 | 0.001 | 0.000 | 0.000-0.002 |
| 2 | 185 | 0.004 | 0.005 | 0.002-0.005 |
| 3 | 185 | 0.006 | 0.005 | 0.005-0.008 |
| 4 | 185 | 0.010 | 0.005 | 0.008-0.012 |
| 5 | 185 | 0.014 | 0.005 | 0.012-0.017 |
| 6 | 185 | 0.021 | 0.011 | 0.017-0.025 |
| 7 | 185 | 0.031 | 0.027 | 0.025-0.039 |
| 8 | 185 | 0.051 | 0.059 | 0.039-0.069 |
| 9 | 185 | 0.097 | 0.086 | 0.069-0.135 |
| 10 | 185 | 0.255 | 0.232 | 0.135-0.792 |

Where the model and the market disagree most (snapshots 2019-2024, per season the top 10 of each; who was right = the label):

| group | players | label = 1 | rate |
|---|---|---|---|
| in both | 34 | 12 | 0.35 |
| in the ECR's top 10, not `logit`'s | 26 | 4 | 0.15 |
| in `logit`'s top 10, not the ECR's | 26 | 3 | 0.12 |

Largest rank gaps (2 per season and side):

| label season | player | pos | group | rank by model | rank by ECR score | PPG S | PPG S+1 | games S+1 | label |
|---|---|---|---|---|---|---|---|---|---|
| 2020 | Jace Sternberger | TE | ecr_only | 94 | 9 | 0.0 | 3.7 | 8 | 0 |
| 2020 | Chris Herndon | TE | ecr_only | 72 | 5 | 1.7 | 6.1 | 12 | 0 |
| 2020 | James Washington | WR | model_only | 6 | 27 | 8.9 | 6.2 | 16 | 0 |
| 2020 | Mecole Hardman | WR | model_only | 10 | 20 | 7.5 | 8.0 | 16 | 0 |
| 2021 | Juwan Johnson | WR | ecr_only | 87 | 5 | 1.6 | 4.1 | 13 | 0 |
| 2021 | Deebo Samuel Sr. | WR | ecr_only | 36 | 6 | 11.5 | 21.2 | 16 | 1 |
| 2021 | Irv Smith | TE | model_only | 3 | 68 | 7.6 | - | 0 | 0 |
| 2021 | Gabe Davis | WR | model_only | 4 | 23 | 8.6 | 8.4 | 15 | 0 |
| 2022 | Brevin Jordan | TE | ecr_only | 23 | 4 | 7.0 | 3.0 | 9 | 0 |
| 2022 | Gabe Davis | WR | ecr_only | 18 | 8 | 8.4 | 11.4 | 15 | 0 |
| 2022 | Henry Ruggs III | WR | model_only | 5 | 100 | 12.1 | - | 0 | 0 |
| 2022 | Laviska Shenault Jr. | WR | model_only | 8 | 33 | 7.9 | 5.6 | 13 | 0 |
| 2023 | Jake Ferguson | TE | ecr_only | 29 | 5 | 4.4 | 10.4 | 17 | 1 |
| 2023 | Jelani Woods | TE | ecr_only | 23 | 9 | 6.2 | - | 0 | 0 |
| 2023 | Josh Palmer | WR | model_only | 5 | 39 | 10.6 | 10.7 | 10 | 0 |
| 2023 | Elijah Moore | WR | model_only | 8 | 15 | 6.3 | 8.3 | 16 | 0 |
| 2024 | Greg Dulcich | TE | ecr_only | 52 | 9 | 2.8 | 1.9 | 4 | 0 |
| 2024 | Isaiah Likely | TE | ecr_only | 11 | 6 | 7.2 | 8.2 | 15 | 0 |
| 2024 | Alec Pierce | WR | model_only | 7 | 53 | 5.6 | 10.1 | 16 | 0 |
| 2024 | Josh Downs | WR | model_only | 5 | 22 | 9.2 | 13.1 | 14 | 0 |
| 2025 | Theo Johnson | TE | ecr_only | 20 | 7 | 6.2 | 8.5 | 15 | 0 |
| 2025 | Ja'Tavion Sanders | TE | ecr_only | 18 | 6 | 4.9 | 4.5 | 12 | 0 |
| 2025 | Quentin Johnston | WR | model_only | 5 | 28 | 11.6 | 13.2 | 13 | 1 |
| 2025 | Xavier Legette | WR | model_only | 7 | 26 | 8.3 | 6.0 | 15 | 0 |

### Breakout RB (`breakout_rb`, label `y_breakout`)

**snapshots 2007-2024 (labels 2008-2025)**

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit` | 0.198 [0.128, 0.312] | 0.200 [0.122, 0.283] | 0.139 [0.094, 0.192] | 0.060 [0.045, 0.075] |
| `lgbm` | 0.212 [0.137, 0.308] | 0.183 [0.111, 0.261] | 0.142 [0.094, 0.192] | 0.059 [0.044, 0.074] |
| `base_ppg_rank` | 0.186 [0.138, 0.270] | 0.206 [0.139, 0.272] | 0.136 [0.100, 0.172] | 0.059 [0.044, 0.076] |
| `eos` | 0.234 [0.146, 0.359] | 0.211 [0.139, 0.289] | 0.142 [0.094, 0.194] | 0.058 [0.044, 0.073] |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | +0.012 [-0.070, 0.101] | -0.006 [-0.050, 0.039] | +0.003 [-0.019, 0.028] | 0.60 |
| `logit` - `eos` | -0.036 [-0.100, -0.000] | -0.011 [-0.033, 0.011] | -0.003 [-0.008, 0.000] | 0.02 |
| `lgbm` - `base_ppg_rank` | +0.026 [-0.039, 0.080] | -0.022 [-0.061, 0.022] | +0.006 [-0.014, 0.025] | 0.77 |
| `lgbm` - `eos` | -0.021 [-0.115, 0.066] | -0.028 [-0.061, 0.000] | 0.000 [-0.019, 0.019] | 0.25 |
| `base_ppg_rank` - `eos` | -0.048 [-0.148, 0.054] | -0.006 [-0.044, 0.028] | -0.006 [-0.031, 0.017] | 0.19 |

**snapshots 2019-2024 (labels 2020-2025), with the ECR**

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit` | 0.218 [0.080, 0.438] | 0.167 [0.050, 0.317] | 0.092 [0.042, 0.158] | 0.042 [0.023, 0.060] |
| `lgbm` | 0.172 [0.047, 0.339] | 0.133 [0.033, 0.267] | 0.092 [0.025, 0.183] | 0.043 [0.022, 0.063] |
| `base_ppg_rank` | 0.161 [0.084, 0.274] | 0.150 [0.050, 0.267] | 0.100 [0.042, 0.167] | 0.042 [0.021, 0.066] |
| `eos` | 0.335 [0.069, 0.464] | 0.167 [0.050, 0.317] | 0.100 [0.042, 0.183] | 0.041 [0.023, 0.060] |
| `ecr` | 0.378 [0.141, 0.791] | 0.183 [0.083, 0.317] | 0.100 [0.042, 0.183] | - |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | +0.057 [-0.032, 0.215] | +0.017 [0.000, 0.050] | -0.008 [-0.025, 0.000] | 0.84 |
| `logit` - `eos` | -0.117 [-0.231, 0.031] | 0.000 [0.000, 0.000] | -0.008 [-0.025, 0.000] | 0.16 |
| `logit` - `ecr` | -0.160 [-0.455, 0.027] | -0.017 [-0.050, 0.000] | -0.008 [-0.025, 0.000] | 0.04 |
| `lgbm` - `base_ppg_rank` | +0.012 [-0.083, 0.141] | -0.017 [-0.050, 0.000] | -0.008 [-0.033, 0.017] | 0.60 |
| `lgbm` - `eos` | -0.162 [-0.282, -0.001] | -0.033 [-0.067, 0.000] | -0.008 [-0.025, 0.000] | 0.02 |
| `lgbm` - `ecr` | -0.205 [-0.529, -0.022] | -0.050 [-0.083, -0.017] | -0.008 [-0.025, 0.000] | 0.01 |
| `base_ppg_rank` - `eos` | -0.174 [-0.296, 0.051] | -0.017 [-0.050, 0.000] | 0.000 [-0.025, 0.025] | 0.09 |
| `base_ppg_rank` - `ecr` | -0.217 [-0.562, -0.027] | -0.033 [-0.067, 0.000] | 0.000 [-0.025, 0.025] | 0.00 |

Top features (mean share of the importance over the 18 folds; logit = spread of the feature's log-odds contribution, sign from the 2024 fold's standardized coefficient; LightGBM = gain):

- `logit`: `ppg_s` (+) 7%, `games_s` (+) 7%, `drafted_pick` (-) 6%, `drafted_round` (-) 5%, `target_share_s` (+) 5%, `age` (-) 5%, `touches_per_game_s` (+) 4%, `combine_speed_score` (+) 4%
- `lgbm`: `drafted_pick` 25%, `touches_per_game_s` 15%, `ppg_s` 12%, `yards_per_team_pass_att_s` 8%, `combine_speed_score` 7%, `age` 5%, `team_any_a` 4%, `air_yards_share_s` 4%

Importance smoke test (spec 6.2 rule 6), folds whose top feature holds over 40%: lgbm 2012: drafted_pick 59%

Calibration of `logit` (its own probability; 10 equal-count bins, every test season):

| bin | rows | mean predicted | observed | range |
|---|---|---|---|---|
| 1 | 87 | 0.012 | 0.000 | 0.003-0.018 |
| 2 | 87 | 0.022 | 0.000 | 0.018-0.025 |
| 3 | 86 | 0.028 | 0.035 | 0.025-0.031 |
| 4 | 87 | 0.036 | 0.011 | 0.032-0.040 |
| 5 | 86 | 0.044 | 0.012 | 0.040-0.048 |
| 6 | 87 | 0.057 | 0.057 | 0.049-0.066 |
| 7 | 87 | 0.076 | 0.057 | 0.066-0.088 |
| 8 | 86 | 0.104 | 0.116 | 0.089-0.122 |
| 9 | 87 | 0.150 | 0.184 | 0.123-0.185 |
| 10 | 86 | 0.275 | 0.209 | 0.188-0.673 |

Where the model and the market disagree most (snapshots 2019-2024, per season the top 10 of each; who was right = the label):

| group | players | label = 1 | rate |
|---|---|---|---|
| in both | 43 | 10 | 0.23 |
| in the ECR's top 10, not `logit`'s | 17 | 1 | 0.06 |
| in `logit`'s top 10, not the ECR's | 17 | 0 | 0.00 |

Largest rank gaps (2 per season and side):

| label season | player | pos | group | rank by model | rank by ECR score | PPG S | PPG S+1 | games S+1 | label |
|---|---|---|---|---|---|---|---|---|---|
| 2020 | Darrell Henderson | RB | ecr_only | 24 | 6 | 2.5 | 8.7 | 15 | 0 |
| 2020 | Phillip Lindsay | RB | ecr_only | 15 | 3 | 12.4 | 6.0 | 11 | 0 |
| 2020 | Derrius Guice | RB | model_only | 4 | 24 | 11.5 | - | 0 | 0 |
| 2020 | Royce Freeman | RB | model_only | 2 | 20 | 8.9 | 3.4 | 11 | 0 |
| 2021 | Ty Johnson | RB | ecr_only | 21 | 10 | 5.8 | 7.8 | 15 | 0 |
| 2021 | Darrynton Evans | RB | ecr_only | 18 | 9 | 3.2 | 3.8 | 1 | 0 |
| 2021 | Cam Akers | RB | model_only | 1 | 54 | 9.3 | 4.3 | 1 | 0 |
| 2021 | J.K. Dobbins | RB | model_only | 2 | 51 | 11.2 | - | 0 | 0 |
| 2022 | Cam Akers | RB | ecr_only | 11 | 2 | 4.3 | 9.4 | 15 | 0 |
| 2022 | Chris Evans | RB | ecr_only | 13 | 10 | 5.5 | 1.3 | 10 | 0 |
| 2022 | Zack Moss | RB | model_only | 6 | 12 | 8.1 | 5.0 | 13 | 0 |
| 2022 | Trey Sermon | RB | model_only | 9 | 11 | 4.7 | 1.9 | 1 | 0 |
| 2023 | Khalil Herbert | RB | ecr_only | 14 | 6 | 9.1 | 9.4 | 12 | 0 |
| 2023 | Jaylen Warren | RB | ecr_only | 16 | 8 | 5.8 | 11.6 | 17 | 0 |
| 2023 | Deon Jackson | RB | model_only | 9 | 16 | 7.0 | 2.0 | 2 | 0 |
| 2023 | Michael Carter | RB | model_only | 8 | 15 | 7.9 | 3.9 | 15 | 0 |
| 2024 | Jaleel McLaughlin | RB | ecr_only | 19 | 7 | 6.2 | 6.1 | 16 | 0 |
| 2024 | Chase Brown | RB | ecr_only | 13 | 4 | 4.5 | 15.9 | 16 | 1 |
| 2024 | Dameon Pierce | RB | model_only | 8 | 12 | 5.9 | 4.3 | 10 | 0 |
| 2024 | Kendre Miller | RB | model_only | 9 | 11 | 6.2 | 4.9 | 6 | 0 |
| 2025 | Ray Davis | RB | ecr_only | 11 | 7 | 6.8 | 3.8 | 17 | 0 |
| 2025 | Blake Corum | RB | ecr_only | 13 | 10 | 2.2 | 7.2 | 17 | 0 |
| 2025 | Kendre Miller | RB | model_only | 10 | 13 | 4.9 | 4.8 | 7 | 0 |
| 2025 | Jaylen Wright | RB | model_only | 8 | 11 | 2.1 | 5.4 | 9 | 0 |

## Read before trusting

- Intervals resample whole seasons (18 in `all`, 6 in `ecr_era`): the ECR-era intervals are wide. Precision@k is the mean over seasons of the share of the season's top k with the label.
- Probabilities are each model's own (no isotonic step); the PPG-rank baseline and the ECR are rankings first.
- Inputs missing in early seasons (NGS before 2016, RYOE before 2018, snap counts before 2013, own xFP before 2009) are imputed inside each fit with a missing indicator.
- Head-coach departures: the owner's cited research file (accepted in bulk, 2026-10-01); a blank date counts as known at the snapshot for fired / mutual / interim types only.
