# Cliff backtest at the preseason snapshot, anchor tuesday_before_week_1 (step I2a / I2-fix)

Step I2a / I2-fix: the same rows and labels as the end-of-season report, read at the PRESEASON snapshot, anchor `tuesday_before_week_1` (the last Tuesday 14:00 UTC (config `as_of.weekly`) before the first regular-season week-1 kickoff of S+1: spec 6.1's "Tuesday before Week 1", kept as the alternative; point in time): every I1b feature plus the week-1 depth charts visible then (team change, depth rank, new competition, QB1 change, vacated targets and carries), a head-coach change of the week-1 team, `dc_absent` (on no chart while his team's chart is visible) and `dc_team_chart_missing` (his team has no visible chart: `dc_absent` is then missing, never a cut; docs/board.md, "Preseason snapshot"). `eos` = the end-of-season report's primary model on the same rows; the ECR (2020+) is the last August/September scrape before week 1 (when it became public vs this anchor: the table below).

ECR timing (negative = the ECR is public only after the as-of):

| snapshot S | ECR scrape of S+1 | public at (UTC) | snapshot as-of (UTC) | ECR public before the as-of by (days) |
|---|---|---|---|---|
| 2019 | 2020-09-03 | 2020-09-04 00:00 | 2020-09-08 Tue 14:00 | +4.6 |
| 2020 | 2021-09-03 | 2021-09-04 00:00 | 2021-09-07 Tue 14:00 | +3.6 |
| 2021 | 2022-09-02 | 2022-09-03 00:00 | 2022-09-06 Tue 14:00 | +3.6 |
| 2022 | 2023-09-01 | 2023-09-02 00:00 | 2023-09-05 Tue 14:00 | +3.6 |
| 2023 | 2024-08-30 | 2024-08-31 00:00 | 2024-09-03 Tue 14:00 | +3.6 |
| 2024 | 2025-08-29 | 2025-08-30 00:00 | 2025-09-02 Tue 14:00 | +3.6 |

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
| `logit` | 0.361 [0.319, 0.407] | 0.433 [0.344, 0.522] | 0.358 [0.311, 0.403] | 0.171 [0.159, 0.183] |
| `lgbm` | 0.353 [0.303, 0.415] | 0.444 [0.367, 0.517] | 0.375 [0.319, 0.431] | 0.171 [0.159, 0.184] |
| `base_ppg_rank` | 0.255 [0.225, 0.295] | 0.261 [0.211, 0.317] | 0.264 [0.225, 0.303] | 0.180 [0.168, 0.193] |
| `eos` | 0.363 [0.322, 0.409] | 0.433 [0.350, 0.517] | 0.361 [0.314, 0.408] | 0.171 [0.159, 0.183] |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | +0.107 [0.074, 0.142] | +0.172 [0.106, 0.233] | +0.094 [0.050, 0.144] | 1.00 |
| `logit` - `eos` | -0.001 [-0.004, 0.000] | 0.000 [-0.017, 0.017] | -0.003 [-0.008, 0.000] | 0.08 |
| `lgbm` - `base_ppg_rank` | +0.099 [0.065, 0.142] | +0.183 [0.111, 0.256] | +0.111 [0.058, 0.164] | 1.00 |
| `lgbm` - `eos` | -0.009 [-0.034, 0.018] | +0.011 [-0.061, 0.078] | +0.014 [-0.031, 0.058] | 0.26 |
| `base_ppg_rank` - `eos` | -0.108 [-0.143, -0.076] | -0.172 [-0.228, -0.106] | -0.097 [-0.147, -0.053] | 0.00 |

**snapshots 2019-2024 (labels 2020-2025), with the ECR**

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit` | 0.408 [0.337, 0.485] | 0.500 [0.333, 0.683] | 0.408 [0.358, 0.442] | 0.173 [0.154, 0.191] |
| `lgbm` | 0.428 [0.349, 0.525] | 0.550 [0.433, 0.667] | 0.450 [0.367, 0.542] | 0.169 [0.150, 0.189] |
| `base_ppg_rank` | 0.299 [0.234, 0.381] | 0.317 [0.217, 0.433] | 0.292 [0.233, 0.350] | 0.185 [0.163, 0.206] |
| `eos` | 0.410 [0.341, 0.484] | 0.483 [0.317, 0.683] | 0.417 [0.367, 0.459] | 0.173 [0.154, 0.190] |
| `ecr` | 0.503 [0.424, 0.570] | 0.533 [0.417, 0.650] | 0.450 [0.375, 0.525] | - |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | +0.110 [0.050, 0.176] | +0.183 [0.083, 0.267] | +0.117 [0.075, 0.158] | 1.00 |
| `logit` - `eos` | -0.002 [-0.005, 0.001] | +0.017 [0.000, 0.050] | -0.008 [-0.025, 0.000] | 0.23 |
| `logit` - `ecr` | -0.094 [-0.149, -0.021] | -0.033 [-0.183, 0.117] | -0.042 [-0.092, 0.000] | 0.00 |
| `lgbm` - `base_ppg_rank` | +0.129 [0.074, 0.220] | +0.233 [0.117, 0.367] | +0.158 [0.075, 0.242] | 1.00 |
| `lgbm` - `eos` | +0.018 [-0.010, 0.072] | +0.067 [-0.083, 0.167] | +0.033 [-0.033, 0.100] | 0.89 |
| `lgbm` - `ecr` | -0.075 [-0.116, -0.006] | +0.017 [-0.083, 0.150] | -0.000 [-0.075, 0.075] | 0.02 |
| `base_ppg_rank` - `eos` | -0.111 [-0.177, -0.052] | -0.167 [-0.250, -0.083] | -0.125 [-0.183, -0.075] | 0.00 |
| `base_ppg_rank` - `ecr` | -0.204 [-0.254, -0.152] | -0.217 [-0.300, -0.133] | -0.158 [-0.183, -0.133] | 0.00 |

Top features (mean share of the importance over the 18 folds; logit = spread of the feature's log-odds contribution, sign from the 2024 fold's standardized coefficient; LightGBM = gain):

- `logit`: `ppg_change` (+) 13%, `pos_rb` (+) 10%, `games_s` (-) 7%, `age_curve_ratio` (-) 6%, `pos_te` (+) 6%, `age` (+) 6%, `touches_per_game_s` (+) 5%, `yards_per_touch_s` (+) 5%
- `lgbm`: `ppg_change` 19%, `touches_per_game_s` 14%, `age_curve_ratio` 11%, `touches_s` 8%, `age` 7%, `yards_per_touch_s` 6%, `ppg_s` 6%, `yards_per_touch_trend` 5%

Importance smoke test (spec 6.2 rule 6), folds whose top feature holds over 40%: lgbm 2007: ppg_change 100%; lgbm 2009: touches_s 47%

Calibration of `logit` (its own probability; 10 equal-count bins, every test season):

| bin | rows | mean predicted | observed | range |
|---|---|---|---|---|
| 1 | 143 | 0.080 | 0.091 | 0.013-0.101 |
| 2 | 142 | 0.119 | 0.092 | 0.102-0.138 |
| 3 | 142 | 0.154 | 0.204 | 0.139-0.169 |
| 4 | 143 | 0.183 | 0.168 | 0.169-0.197 |
| 5 | 142 | 0.209 | 0.176 | 0.197-0.222 |
| 6 | 142 | 0.234 | 0.289 | 0.222-0.247 |
| 7 | 143 | 0.261 | 0.266 | 0.248-0.275 |
| 8 | 142 | 0.292 | 0.296 | 0.275-0.312 |
| 9 | 142 | 0.345 | 0.359 | 0.313-0.377 |
| 10 | 142 | 0.475 | 0.430 | 0.378-0.903 |

Where the model and the market disagree most (snapshots 2019-2024, per season the top 10 of each; who was right = the label):

| group | players | label = 1 | rate |
|---|---|---|---|
| in both | 20 | 17 | 0.85 |
| in the ECR's top 10, not `logit`'s | 40 | 15 | 0.38 |
| in `logit`'s top 10, not the ECR's | 40 | 13 | 0.33 |

Largest rank gaps (2 per season and side):

| label season | player | pos | group | rank by model | rank by ECR score | PPG S | PPG S+1 | games S+1 | label |
|---|---|---|---|---|---|---|---|---|---|
| 2020 | Andy Dalton | QB | ecr_only | 55 | 5 | 15.7 | 12.4 | 11 | 0 |
| 2020 | Julian Edelman | WR | ecr_only | 42 | 4 | 16.0 | 9.4 | 6 | 1 |
| 2020 | David Johnson | RB | model_only | 3 | 70 | 11.8 | 15.0 | 12 | 0 |
| 2020 | Todd Gurley | RB | model_only | 1 | 54 | 14.6 | 10.9 | 15 | 0 |
| 2021 | Cam Newton | QB | ecr_only | 52 | 8 | 17.4 | 12.3 | 7 | 0 |
| 2021 | Marvin Jones | WR | ecr_only | 40 | 7 | 14.2 | 10.6 | 17 | 0 |
| 2021 | Mike Davis | RB | model_only | 6 | 36 | 13.8 | 8.1 | 17 | 1 |
| 2021 | Robert Tonyan | TE | model_only | 10 | 30 | 11.8 | 6.3 | 8 | 1 |
| 2022 | Taylor Heinicke | QB | ecr_only | 63 | 3 | 13.9 | 12.9 | 9 | 0 |
| 2022 | Jimmy Garoppolo | QB | ecr_only | 61 | 10 | 15.2 | 15.0 | 11 | 0 |
| 2022 | Saquon Barkley | RB | model_only | 1 | 79 | 11.4 | 17.8 | 16 | 0 |
| 2022 | Cameron Brate | TE | model_only | 8 | 75 | 4.9 | 4.2 | 9 | 0 |
| 2023 | Cooper Rush | QB | ecr_only | 56 | 6 | 7.1 | 0.5 | 7 | 1 |
| 2023 | Cordarrelle Patterson | RB | ecr_only | 33 | 4 | 11.9 | 3.1 | 12 | 1 |
| 2023 | Irv Smith | TE | model_only | 3 | 79 | 6.9 | 3.4 | 10 | 1 |
| 2023 | Hayden Hurst | TE | model_only | 4 | 48 | 8.1 | 4.7 | 9 | 1 |
| 2024 | Russell Wilson | QB | ecr_only | 57 | 9 | 17.1 | 15.7 | 11 | 0 |
| 2024 | Jakobi Meyers | WR | ecr_only | 43 | 6 | 13.7 | 14.5 | 15 | 0 |
| 2024 | Noah Fant | TE | model_only | 10 | 77 | 4.9 | 7.4 | 14 | 0 |
| 2024 | Dawson Knox | TE | model_only | 9 | 37 | 5.5 | 4.2 | 14 | 0 |
| 2025 | Keenan Allen | WR | ecr_only | 62 | 5 | 12.3 | 10.7 | 17 | 0 |
| 2025 | Mac Jones | QB | ecr_only | 61 | 10 | 9.6 | 11.8 | 11 | 0 |
| 2025 | Evan Engram | TE | model_only | 9 | 73 | 9.9 | 6.4 | 16 | 1 |
| 2025 | Dallas Goedert | TE | model_only | 4 | 45 | 10.4 | 12.3 | 15 | 0 |

### Missed most of S+1 (under 6 games) (`cliff_missed`, label `y_missed`)

**snapshots 2007-2024 (labels 2008-2025)**

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit_simple` | 0.384 [0.330, 0.439] | 0.433 [0.361, 0.500] | 0.322 [0.269, 0.375] | 0.113 [0.102, 0.125] |
| `base_ppg_rank` | 0.242 [0.209, 0.281] | 0.283 [0.217, 0.350] | 0.264 [0.217, 0.311] | 0.122 [0.111, 0.135] |
| `eos` | 0.384 [0.330, 0.439] | 0.433 [0.361, 0.500] | 0.322 [0.269, 0.375] | 0.113 [0.102, 0.125] |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit_simple` - `base_ppg_rank` | +0.142 [0.079, 0.204] | +0.150 [0.061, 0.233] | +0.058 [0.003, 0.114] | 1.00 |
| `logit_simple` - `eos` | +0.000 [-0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.69 |
| `base_ppg_rank` - `eos` | -0.142 [-0.204, -0.079] | -0.150 [-0.233, -0.061] | -0.058 [-0.114, -0.003] | 0.00 |

**snapshots 2019-2024 (labels 2020-2025), with the ECR**

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit_simple` | 0.352 [0.233, 0.484] | 0.383 [0.250, 0.533] | 0.283 [0.192, 0.408] | 0.099 [0.084, 0.116] |
| `base_ppg_rank` | 0.178 [0.123, 0.257] | 0.200 [0.100, 0.300] | 0.192 [0.108, 0.292] | 0.108 [0.089, 0.133] |
| `eos` | 0.352 [0.233, 0.484] | 0.383 [0.250, 0.533] | 0.283 [0.192, 0.408] | 0.099 [0.084, 0.116] |
| `ecr` | 0.570 [0.479, 0.662] | 0.517 [0.417, 0.650] | 0.358 [0.283, 0.459] | - |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit_simple` - `base_ppg_rank` | +0.175 [0.081, 0.246] | +0.183 [0.067, 0.300] | +0.092 [-0.000, 0.183] | 1.00 |
| `logit_simple` - `eos` | +0.000 [-0.000, 0.001] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.60 |
| `logit_simple` - `ecr` | -0.217 [-0.375, -0.113] | -0.133 [-0.250, -0.033] | -0.075 [-0.125, -0.025] | 0.00 |
| `base_ppg_rank` - `eos` | -0.174 [-0.246, -0.081] | -0.183 [-0.300, -0.067] | -0.092 [-0.183, 0.000] | 0.00 |
| `base_ppg_rank` - `ecr` | -0.392 [-0.490, -0.292] | -0.317 [-0.433, -0.200] | -0.167 [-0.250, -0.050] | 0.00 |

Top features (mean share of the importance over the 18 folds; logit = spread of the feature's log-odds contribution, sign from the 2024 fold's standardized coefficient; LightGBM = gain):

- `logit_simple`: `games_s` (-) 32%, `pos_wr` (-) 19%, `touches_per_game_s` (-) 16%, `prior_seasons` (+) 15%, `pos_rb` (+) 9%, `age` (+) 7%, `pos_te` (+) 2%, `dc_absent` 0%

Calibration of `logit_simple` (its own probability; 10 equal-count bins, every test season):

| bin | rows | mean predicted | observed | range |
|---|---|---|---|---|
| 1 | 168 | 0.036 | 0.048 | 0.019-0.046 |
| 2 | 167 | 0.057 | 0.072 | 0.046-0.066 |
| 3 | 168 | 0.075 | 0.083 | 0.066-0.084 |
| 4 | 167 | 0.093 | 0.096 | 0.084-0.102 |
| 5 | 167 | 0.110 | 0.078 | 0.102-0.118 |
| 6 | 168 | 0.129 | 0.089 | 0.118-0.143 |
| 7 | 167 | 0.160 | 0.168 | 0.143-0.181 |
| 8 | 168 | 0.209 | 0.208 | 0.182-0.245 |
| 9 | 167 | 0.284 | 0.204 | 0.246-0.336 |
| 10 | 167 | 0.449 | 0.455 | 0.336-0.815 |

Where the model and the market disagree most (snapshots 2019-2024, per season the top 10 of each; who was right = the label):

| group | players | label = 1 | rate |
|---|---|---|---|
| in both | 18 | 12 | 0.67 |
| in the ECR's top 10, not `logit_simple`'s | 42 | 19 | 0.45 |
| in `logit_simple`'s top 10, not the ECR's | 42 | 11 | 0.26 |

Largest rank gaps (2 per season and side):

| label season | player | pos | group | rank by model | rank by ECR score | PPG S | PPG S+1 | games S+1 | label |
|---|---|---|---|---|---|---|---|---|---|
| 2020 | Jameis Winston | QB | ecr_only | 58 | 4 | 19.1 | 0.8 | 3 | 1 |
| 2020 | Julian Edelman | WR | ecr_only | 51 | 8 | 16.0 | 9.4 | 6 | 0 |
| 2020 | Tom Brady | QB | model_only | 4 | 70 | 16.5 | 21.1 | 16 | 0 |
| 2020 | Teddy Bridgewater | QB | model_only | 6 | 72 | 10.1 | 16.1 | 15 | 0 |
| 2021 | Todd Gurley | RB | ecr_only | 50 | 5 | 10.9 | - | 0 | 1 |
| 2021 | Wayne Gallman | RB | ecr_only | 38 | 8 | 9.8 | 1.9 | 7 | 0 |
| 2021 | Tom Brady | QB | model_only | 8 | 77 | 21.1 | 22.0 | 17 | 0 |
| 2021 | Ryan Fitzpatrick | QB | model_only | 2 | 60 | 17.1 | 0.7 | 1 | 1 |
| 2022 | Darrel Williams | RB | ecr_only | 65 | 10 | 11.5 | 4.2 | 5 | 1 |
| 2022 | Taylor Heinicke | QB | ecr_only | 57 | 7 | 13.9 | 12.9 | 9 | 0 |
| 2022 | Robert Tonyan | TE | model_only | 2 | 81 | 6.3 | 6.6 | 17 | 0 |
| 2022 | Jacoby Brissett | QB | model_only | 7 | 72 | 7.2 | 12.0 | 14 | 0 |
| 2023 | Leonard Fournette | RB | ecr_only | 54 | 4 | 14.1 | 2.0 | 2 | 1 |
| 2023 | Jordan Akins | TE | ecr_only | 41 | 7 | 7.8 | 2.2 | 13 | 0 |
| 2023 | Matthew Stafford | QB | model_only | 1 | 93 | 12.0 | 16.2 | 15 | 0 |
| 2023 | Darren Waller | TE | model_only | 2 | 85 | 10.6 | 9.4 | 12 | 0 |
| 2024 | Jakobi Meyers | WR | ecr_only | 79 | 10 | 13.7 | 14.5 | 15 | 0 |
| 2024 | Adam Thielen | WR | ecr_only | 71 | 5 | 13.6 | 13.9 | 10 | 0 |
| 2024 | Aaron Jones | RB | model_only | 10 | 80 | 12.3 | 14.2 | 17 | 0 |
| 2024 | Kyler Murray | QB | model_only | 5 | 71 | 18.3 | 17.5 | 17 | 0 |
| 2025 | Rico Dowdle | RB | ecr_only | 67 | 9 | 12.4 | 12.7 | 17 | 0 |
| 2025 | Keenan Allen | WR | ecr_only | 51 | 10 | 12.3 | 10.7 | 17 | 0 |
| 2025 | Dak Prescott | QB | model_only | 1 | 91 | 14.6 | 18.5 | 17 | 0 |
| 2025 | Evan Engram | TE | model_only | 7 | 85 | 9.9 | 6.4 | 16 | 0 |

### Cliff counting a missed season as a cliff (sensitivity) (`cliff_sensitivity`, label `y_cliff_or_missed`)

**snapshots 2007-2024 (labels 2008-2025)**

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit` | 0.535 [0.490, 0.579] | 0.644 [0.583, 0.700] | 0.583 [0.531, 0.633] | 0.206 [0.199, 0.214] |
| `lgbm` | 0.511 [0.457, 0.569] | 0.633 [0.561, 0.700] | 0.567 [0.500, 0.625] | 0.208 [0.201, 0.215] |
| `base_ppg_rank` | 0.433 [0.400, 0.468] | 0.478 [0.411, 0.544] | 0.483 [0.444, 0.517] | 0.221 [0.215, 0.228] |
| `eos` | 0.535 [0.490, 0.579] | 0.650 [0.583, 0.706] | 0.589 [0.536, 0.639] | 0.206 [0.198, 0.214] |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | +0.102 [0.060, 0.139] | +0.167 [0.078, 0.256] | +0.100 [0.053, 0.150] | 1.00 |
| `logit` - `eos` | -0.000 [-0.001, 0.001] | -0.006 [-0.017, 0.000] | -0.006 [-0.014, 0.000] | 0.36 |
| `lgbm` - `base_ppg_rank` | +0.078 [0.039, 0.123] | +0.156 [0.061, 0.239] | +0.083 [0.033, 0.136] | 1.00 |
| `lgbm` - `eos` | -0.024 [-0.049, 0.008] | -0.017 [-0.072, 0.039] | -0.022 [-0.064, 0.014] | 0.07 |
| `base_ppg_rank` - `eos` | -0.102 [-0.139, -0.060] | -0.172 [-0.261, -0.078] | -0.106 [-0.156, -0.058] | 0.00 |

**snapshots 2019-2024 (labels 2020-2025), with the ECR**

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit` | 0.545 [0.471, 0.609] | 0.667 [0.550, 0.750] | 0.633 [0.575, 0.692] | 0.201 [0.191, 0.212] |
| `lgbm` | 0.512 [0.401, 0.618] | 0.633 [0.483, 0.767] | 0.567 [0.433, 0.675] | 0.203 [0.193, 0.213] |
| `base_ppg_rank` | 0.417 [0.350, 0.492] | 0.500 [0.400, 0.583] | 0.450 [0.367, 0.533] | 0.220 [0.207, 0.231] |
| `eos` | 0.546 [0.473, 0.610] | 0.667 [0.550, 0.750] | 0.642 [0.575, 0.700] | 0.201 [0.191, 0.212] |
| `ecr` | 0.689 [0.637, 0.736] | 0.850 [0.783, 0.933] | 0.642 [0.558, 0.725] | - |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | +0.128 [0.085, 0.174] | +0.167 [0.100, 0.233] | +0.183 [0.117, 0.250] | 1.00 |
| `logit` - `eos` | -0.001 [-0.002, 0.001] | 0.000 [0.000, 0.000] | -0.008 [-0.025, 0.000] | 0.12 |
| `logit` - `ecr` | -0.144 [-0.197, -0.079] | -0.183 [-0.283, -0.083] | -0.008 [-0.050, 0.042] | 0.00 |
| `lgbm` - `base_ppg_rank` | +0.095 [0.032, 0.188] | +0.133 [0.000, 0.267] | +0.117 [0.025, 0.209] | 1.00 |
| `lgbm` - `eos` | -0.034 [-0.073, 0.021] | -0.033 [-0.100, 0.033] | -0.075 [-0.150, -0.017] | 0.21 |
| `lgbm` - `ecr` | -0.177 [-0.274, -0.083] | -0.217 [-0.350, -0.083] | -0.075 [-0.150, -0.008] | 0.00 |
| `base_ppg_rank` - `eos` | -0.129 [-0.175, -0.087] | -0.167 [-0.233, -0.100] | -0.192 [-0.250, -0.133] | 0.00 |
| `base_ppg_rank` - `ecr` | -0.272 [-0.337, -0.197] | -0.350 [-0.467, -0.217] | -0.192 [-0.275, -0.083] | 0.00 |

Top features (mean share of the importance over the 18 folds; logit = spread of the feature's log-odds contribution, sign from the 2024 fold's standardized coefficient; LightGBM = gain):

- `logit`: `games_s` (-) 14%, `ppg_change` (+) 12%, `ppg_s` (-) 8%, `age` (+) 7%, `prior_seasons` (+) 7%, `pos_rank_s` (+) 6%, `pos_rb` (+) 6%, `touches_per_game_s` (+) 4%
- `lgbm`: `games_s` 14%, `ppg_s` 14%, `ppg_change` 13%, `age_curve_ratio` 10%, `age` 8%, `yards_per_touch_s` 7%, `pos_rank_s` 7%, `touches_per_game_s` 6%

Calibration of `logit` (its own probability; 10 equal-count bins, every test season):

| bin | rows | mean predicted | observed | range |
|---|---|---|---|---|
| 1 | 168 | 0.140 | 0.167 | 0.064-0.178 |
| 2 | 167 | 0.201 | 0.198 | 0.179-0.220 |
| 3 | 168 | 0.238 | 0.268 | 0.221-0.254 |
| 4 | 167 | 0.272 | 0.198 | 0.254-0.290 |
| 5 | 167 | 0.309 | 0.305 | 0.290-0.329 |
| 6 | 168 | 0.348 | 0.357 | 0.329-0.366 |
| 7 | 167 | 0.391 | 0.401 | 0.366-0.415 |
| 8 | 168 | 0.446 | 0.417 | 0.416-0.476 |
| 9 | 167 | 0.518 | 0.563 | 0.476-0.565 |
| 10 | 167 | 0.652 | 0.641 | 0.566-0.952 |

Where the model and the market disagree most (snapshots 2019-2024, per season the top 10 of each; who was right = the label):

| group | players | label = 1 | rate |
|---|---|---|---|
| in both | 27 | 25 | 0.93 |
| in the ECR's top 10, not `logit`'s | 33 | 26 | 0.79 |
| in `logit`'s top 10, not the ECR's | 33 | 15 | 0.45 |

Largest rank gaps (2 per season and side):

| label season | player | pos | group | rank by model | rank by ECR score | PPG S | PPG S+1 | games S+1 | label |
|---|---|---|---|---|---|---|---|---|---|
| 2020 | Jameis Winston | QB | ecr_only | 75 | 4 | 19.1 | 0.8 | 3 | 1 |
| 2020 | Julian Edelman | WR | ecr_only | 50 | 8 | 16.0 | 9.4 | 6 | 1 |
| 2020 | David Johnson | RB | model_only | 5 | 76 | 11.8 | 15.0 | 12 | 0 |
| 2020 | Le'Veon Bell | RB | model_only | 4 | 45 | 14.3 | 6.8 | 11 | 1 |
| 2021 | Philip Rivers | QB | ecr_only | 40 | 3 | 15.0 | - | 0 | 1 |
| 2021 | Drew Brees | QB | ecr_only | 31 | 2 | 17.5 | - | 0 | 1 |
| 2021 | Kyle Rudolph | TE | model_only | 8 | 42 | 5.5 | 4.0 | 15 | 0 |
| 2021 | J.D. McKissic | RB | model_only | 1 | 24 | 12.0 | 11.6 | 11 | 0 |
| 2022 | Taylor Heinicke | QB | ecr_only | 58 | 7 | 13.9 | 12.9 | 9 | 0 |
| 2022 | Ben Roethlisberger | QB | ecr_only | 42 | 4 | 13.6 | - | 0 | 1 |
| 2022 | Saquon Barkley | RB | model_only | 3 | 87 | 11.4 | 17.8 | 16 | 0 |
| 2022 | Jacoby Brissett | QB | model_only | 2 | 72 | 7.2 | 12.0 | 14 | 0 |
| 2023 | Leonard Fournette | RB | ecr_only | 67 | 4 | 14.1 | 2.0 | 2 | 1 |
| 2023 | Tom Brady | QB | ecr_only | 33 | 1 | 16.0 | - | 0 | 1 |
| 2023 | Irv Smith | TE | model_only | 1 | 89 | 6.9 | 3.4 | 10 | 1 |
| 2023 | Matthew Stafford | QB | model_only | 9 | 93 | 12.0 | 16.2 | 15 | 0 |
| 2024 | Jakobi Meyers | WR | ecr_only | 70 | 10 | 13.7 | 14.5 | 15 | 0 |
| 2024 | Keenan Allen | WR | ecr_only | 49 | 8 | 21.5 | 12.3 | 15 | 1 |
| 2024 | Aaron Jones | RB | model_only | 7 | 80 | 12.3 | 14.2 | 17 | 0 |
| 2024 | Dawson Knox | TE | model_only | 8 | 45 | 5.5 | 4.2 | 14 | 0 |
| 2025 | Keenan Allen | WR | ecr_only | 66 | 10 | 12.3 | 10.7 | 17 | 0 |
| 2025 | Joe Mixon | RB | ecr_only | 34 | 6 | 17.2 | - | 0 | 1 |
| 2025 | Evan Engram | TE | model_only | 8 | 85 | 9.9 | 6.4 | 16 | 1 |
| 2025 | Dallas Goedert | TE | model_only | 6 | 56 | 10.4 | 12.3 | 15 | 0 |

## Read before trusting

- Intervals resample whole seasons (18 in `all`, 6 in `ecr_era`): the ECR-era intervals are wide. Precision@k is the mean over seasons of the share of the season's top k with the label.
- Probabilities are each model's own (no isotonic step); the PPG-rank baseline and the ECR are rankings first.
- Inputs missing in early seasons (NGS before 2016, RYOE before 2018, snap counts before 2013, own xFP before 2009) are imputed inside each fit with a missing indicator.
- Head-coach departures: the owner's cited research file (accepted in bulk, 2026-10-01); a blank date counts as known at the snapshot for fired / mutual / interim types only.
