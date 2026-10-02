# Cliff backtest (step I1b)

Who among last season's top-36 veterans (3+ prior seasons, 8+ games) loses 30% or more of his points per game next season? Features at the end-of-season snapshot of S (the Tuesday after the Super Bowl, point in time), label from S+1. Walk-forward by snapshot season: each test season's models learn only from earlier snapshots (2002 on). Owner decision 2026-10-02: players with fewer than 6 games in S+1 get the separate label `y_missed` with its own simple logit and are left out of the main Cliff model; the sensitivity run counts them as cliffs. docs/board.md explains every column.

Baselines: last season's PPG rank (a one-feature logit) and FantasyPros' PRESEASON expert consensus ranking (ECR; not ADP, which the warehouse does not have) of S+1, 2020+ only, scored as ECR rank minus last season's rank. The ECR is taken months after the snapshot (after free agency and the draft), so it knows more than the model.

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
| `logit` | 0.363 [0.322, 0.409] | 0.433 [0.350, 0.517] | 0.361 [0.314, 0.408] | 0.171 [0.159, 0.183] |
| `lgbm` | 0.349 [0.303, 0.402] | 0.433 [0.350, 0.517] | 0.367 [0.319, 0.414] | 0.172 [0.159, 0.185] |
| `base_ppg_rank` | 0.255 [0.225, 0.295] | 0.261 [0.211, 0.317] | 0.264 [0.225, 0.303] | 0.180 [0.168, 0.193] |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | +0.108 [0.076, 0.143] | +0.172 [0.106, 0.228] | +0.097 [0.053, 0.147] | 1.00 |
| `lgbm` - `base_ppg_rank` | +0.094 [0.063, 0.131] | +0.172 [0.100, 0.244] | +0.103 [0.053, 0.153] | 1.00 |

**snapshots 2019-2024 (labels 2020-2025), with the ECR**

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit` | 0.410 [0.341, 0.484] | 0.483 [0.317, 0.683] | 0.417 [0.367, 0.459] | 0.173 [0.154, 0.190] |
| `lgbm` | 0.423 [0.360, 0.499] | 0.533 [0.400, 0.683] | 0.450 [0.400, 0.500] | 0.170 [0.148, 0.191] |
| `base_ppg_rank` | 0.299 [0.234, 0.381] | 0.317 [0.217, 0.433] | 0.292 [0.233, 0.350] | 0.185 [0.163, 0.206] |
| `ecr` | 0.503 [0.424, 0.570] | 0.533 [0.417, 0.650] | 0.450 [0.375, 0.525] | - |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | +0.111 [0.052, 0.177] | +0.167 [0.083, 0.250] | +0.125 [0.075, 0.183] | 1.00 |
| `logit` - `ecr` | -0.093 [-0.147, -0.023] | -0.050 [-0.183, 0.084] | -0.033 [-0.092, 0.017] | 0.00 |
| `lgbm` - `base_ppg_rank` | +0.124 [0.072, 0.207] | +0.217 [0.133, 0.350] | +0.158 [0.100, 0.225] | 1.00 |
| `lgbm` - `ecr` | -0.080 [-0.118, -0.016] | +0.000 [-0.117, 0.150] | 0.000 [-0.067, 0.075] | 0.01 |
| `base_ppg_rank` - `ecr` | -0.204 [-0.254, -0.152] | -0.217 [-0.300, -0.133] | -0.158 [-0.183, -0.133] | 0.00 |

Top features (mean share of the importance over the 18 folds; logit = spread of the feature's log-odds contribution, sign from the 2024 fold's standardized coefficient; LightGBM = gain):

- `logit`: `ppg_change` (+) 13%, `pos_rb` (+) 10%, `games_s` (-) 7%, `age_curve_ratio` (-) 6%, `pos_te` (+) 6%, `age` (+) 6%, `touches_per_game_s` (+) 5%, `yards_per_touch_s` (+) 5%
- `lgbm`: `ppg_change` 19%, `touches_per_game_s` 13%, `age_curve_ratio` 10%, `touches_s` 8%, `age` 7%, `yards_per_touch_s` 7%, `ppg_s` 6%, `yards_per_touch_trend` 5%

Importance smoke test (spec 6.2 rule 6), folds whose top feature holds over 40%: lgbm 2007: ppg_change 100%; lgbm 2009: touches_s 47%

Calibration of `logit` (its own probability; 10 equal-count bins, every test season):

| bin | rows | mean predicted | observed | range |
|---|---|---|---|---|
| 1 | 143 | 0.080 | 0.091 | 0.013-0.100 |
| 2 | 142 | 0.119 | 0.092 | 0.100-0.137 |
| 3 | 142 | 0.155 | 0.204 | 0.138-0.168 |
| 4 | 143 | 0.183 | 0.168 | 0.169-0.197 |
| 5 | 142 | 0.209 | 0.176 | 0.197-0.220 |
| 6 | 142 | 0.233 | 0.289 | 0.221-0.247 |
| 7 | 143 | 0.261 | 0.259 | 0.248-0.275 |
| 8 | 142 | 0.292 | 0.303 | 0.275-0.314 |
| 9 | 142 | 0.345 | 0.359 | 0.314-0.377 |
| 10 | 142 | 0.475 | 0.430 | 0.378-0.902 |

Where the model and the market disagree most (snapshots 2019-2024, per season the top 10 of each; who was right = the label):

| group | players | label = 1 | rate |
|---|---|---|---|
| in both | 21 | 17 | 0.81 |
| in the ECR's top 10, not `logit`'s | 39 | 15 | 0.38 |
| in `logit`'s top 10, not the ECR's | 39 | 12 | 0.31 |

Largest rank gaps (2 per season and side):

| label season | player | pos | group | rank by model | rank by ECR score | PPG S | PPG S+1 | games S+1 | label |
|---|---|---|---|---|---|---|---|---|---|
| 2020 | Andy Dalton | QB | ecr_only | 55 | 5 | 15.7 | 12.4 | 11 | 0 |
| 2020 | Julian Edelman | WR | ecr_only | 43 | 4 | 16.0 | 9.4 | 6 | 1 |
| 2020 | David Johnson | RB | model_only | 3 | 70 | 11.8 | 15.0 | 12 | 0 |
| 2020 | Todd Gurley | RB | model_only | 1 | 54 | 14.6 | 10.9 | 15 | 0 |
| 2021 | Cam Newton | QB | ecr_only | 52 | 8 | 17.4 | 12.3 | 7 | 0 |
| 2021 | Marvin Jones | WR | ecr_only | 39 | 7 | 14.2 | 10.6 | 17 | 0 |
| 2021 | Mike Davis | RB | model_only | 6 | 36 | 13.8 | 8.1 | 17 | 1 |
| 2021 | Robert Tonyan | TE | model_only | 10 | 30 | 11.8 | 6.3 | 8 | 1 |
| 2022 | Taylor Heinicke | QB | ecr_only | 63 | 3 | 13.9 | 12.9 | 9 | 0 |
| 2022 | Jimmy Garoppolo | QB | ecr_only | 61 | 10 | 15.2 | 15.0 | 11 | 0 |
| 2022 | Saquon Barkley | RB | model_only | 1 | 79 | 11.4 | 17.8 | 16 | 0 |
| 2022 | Cameron Brate | TE | model_only | 10 | 75 | 4.9 | 4.2 | 9 | 0 |
| 2023 | Cooper Rush | QB | ecr_only | 56 | 6 | 7.1 | 0.5 | 7 | 1 |
| 2023 | Cordarrelle Patterson | RB | ecr_only | 33 | 4 | 11.9 | 3.1 | 12 | 1 |
| 2023 | Irv Smith | TE | model_only | 3 | 79 | 6.9 | 3.4 | 10 | 1 |
| 2023 | Hayden Hurst | TE | model_only | 4 | 48 | 8.1 | 4.7 | 9 | 1 |
| 2024 | Russell Wilson | QB | ecr_only | 57 | 9 | 17.1 | 15.7 | 11 | 0 |
| 2024 | Jakobi Meyers | WR | ecr_only | 42 | 6 | 13.7 | 14.5 | 15 | 0 |
| 2024 | Noah Fant | TE | model_only | 10 | 77 | 4.9 | 7.4 | 14 | 0 |
| 2024 | Dawson Knox | TE | model_only | 9 | 37 | 5.5 | 4.2 | 14 | 0 |
| 2025 | Keenan Allen | WR | ecr_only | 62 | 5 | 12.3 | 10.7 | 17 | 0 |
| 2025 | Mac Jones | QB | ecr_only | 60 | 10 | 9.6 | 11.8 | 11 | 0 |
| 2025 | Evan Engram | TE | model_only | 8 | 73 | 9.9 | 6.4 | 16 | 1 |
| 2025 | Dallas Goedert | TE | model_only | 4 | 45 | 10.4 | 12.3 | 15 | 0 |

### Missed most of S+1 (under 6 games) (`cliff_missed`, label `y_missed`)

**snapshots 2007-2024 (labels 2008-2025)**

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit_simple` | 0.384 [0.330, 0.439] | 0.433 [0.361, 0.500] | 0.322 [0.269, 0.375] | 0.113 [0.102, 0.125] |
| `base_ppg_rank` | 0.242 [0.209, 0.281] | 0.283 [0.217, 0.350] | 0.264 [0.217, 0.311] | 0.122 [0.111, 0.135] |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit_simple` - `base_ppg_rank` | +0.142 [0.079, 0.204] | +0.150 [0.061, 0.233] | +0.058 [0.003, 0.114] | 1.00 |

**snapshots 2019-2024 (labels 2020-2025), with the ECR**

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit_simple` | 0.352 [0.233, 0.484] | 0.383 [0.250, 0.533] | 0.283 [0.192, 0.408] | 0.099 [0.084, 0.116] |
| `base_ppg_rank` | 0.178 [0.123, 0.257] | 0.200 [0.100, 0.300] | 0.192 [0.108, 0.292] | 0.108 [0.089, 0.133] |
| `ecr` | 0.570 [0.479, 0.662] | 0.517 [0.417, 0.650] | 0.358 [0.283, 0.459] | - |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit_simple` - `base_ppg_rank` | +0.174 [0.081, 0.246] | +0.183 [0.067, 0.300] | +0.092 [-0.000, 0.183] | 1.00 |
| `logit_simple` - `ecr` | -0.218 [-0.375, -0.113] | -0.133 [-0.250, -0.033] | -0.075 [-0.125, -0.025] | 0.00 |
| `base_ppg_rank` - `ecr` | -0.392 [-0.490, -0.292] | -0.317 [-0.433, -0.200] | -0.167 [-0.250, -0.050] | 0.00 |

Top features (mean share of the importance over the 18 folds; logit = spread of the feature's log-odds contribution, sign from the 2024 fold's standardized coefficient; LightGBM = gain):

- `logit_simple`: `games_s` (-) 32%, `pos_wr` (-) 19%, `touches_per_game_s` (-) 16%, `prior_seasons` (+) 15%, `pos_rb` (+) 9%, `age` (+) 7%, `pos_te` (+) 2%

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
| `logit` | 0.535 [0.490, 0.579] | 0.650 [0.583, 0.706] | 0.589 [0.536, 0.639] | 0.206 [0.198, 0.214] |
| `lgbm` | 0.514 [0.461, 0.570] | 0.617 [0.533, 0.689] | 0.564 [0.503, 0.619] | 0.207 [0.200, 0.214] |
| `base_ppg_rank` | 0.433 [0.400, 0.468] | 0.478 [0.411, 0.544] | 0.483 [0.444, 0.517] | 0.221 [0.215, 0.228] |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | +0.102 [0.060, 0.139] | +0.172 [0.078, 0.261] | +0.106 [0.058, 0.156] | 1.00 |
| `lgbm` - `base_ppg_rank` | +0.081 [0.043, 0.126] | +0.139 [0.044, 0.222] | +0.081 [0.033, 0.131] | 1.00 |

**snapshots 2019-2024 (labels 2020-2025), with the ECR**

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit` | 0.546 [0.473, 0.610] | 0.667 [0.550, 0.750] | 0.642 [0.575, 0.700] | 0.201 [0.191, 0.212] |
| `lgbm` | 0.517 [0.409, 0.620] | 0.583 [0.400, 0.733] | 0.567 [0.450, 0.650] | 0.201 [0.189, 0.212] |
| `base_ppg_rank` | 0.417 [0.350, 0.492] | 0.500 [0.400, 0.583] | 0.450 [0.367, 0.533] | 0.220 [0.207, 0.231] |
| `ecr` | 0.689 [0.637, 0.736] | 0.850 [0.783, 0.933] | 0.642 [0.558, 0.725] | - |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | +0.129 [0.087, 0.175] | +0.167 [0.100, 0.233] | +0.192 [0.133, 0.250] | 1.00 |
| `logit` - `ecr` | -0.143 [-0.195, -0.080] | -0.183 [-0.283, -0.083] | -0.000 [-0.050, 0.067] | 0.00 |
| `lgbm` - `base_ppg_rank` | +0.100 [0.033, 0.196] | +0.083 [-0.033, 0.200] | +0.117 [0.042, 0.192] | 1.00 |
| `lgbm` - `ecr` | -0.172 [-0.268, -0.084] | -0.267 [-0.433, -0.100] | -0.075 [-0.133, -0.017] | 0.00 |
| `base_ppg_rank` - `ecr` | -0.272 [-0.337, -0.197] | -0.350 [-0.467, -0.217] | -0.192 [-0.275, -0.083] | 0.00 |

Top features (mean share of the importance over the 18 folds; logit = spread of the feature's log-odds contribution, sign from the 2024 fold's standardized coefficient; LightGBM = gain):

- `logit`: `games_s` (-) 14%, `ppg_change` (+) 12%, `ppg_s` (-) 8%, `age` (+) 7%, `prior_seasons` (+) 7%, `pos_rank_s` (+) 6%, `pos_rb` (+) 6%, `touches_per_game_s` (+) 4%
- `lgbm`: `ppg_s` 13%, `ppg_change` 13%, `games_s` 13%, `age_curve_ratio` 10%, `age` 8%, `yards_per_touch_s` 8%, `pos_rank_s` 7%, `touches_per_game_s` 6%

Calibration of `logit` (its own probability; 10 equal-count bins, every test season):

| bin | rows | mean predicted | observed | range |
|---|---|---|---|---|
| 1 | 168 | 0.140 | 0.173 | 0.065-0.178 |
| 2 | 167 | 0.201 | 0.192 | 0.178-0.219 |
| 3 | 168 | 0.237 | 0.268 | 0.219-0.253 |
| 4 | 167 | 0.272 | 0.204 | 0.254-0.289 |
| 5 | 167 | 0.309 | 0.293 | 0.289-0.329 |
| 6 | 168 | 0.348 | 0.357 | 0.329-0.365 |
| 7 | 167 | 0.391 | 0.425 | 0.366-0.415 |
| 8 | 168 | 0.447 | 0.387 | 0.415-0.477 |
| 9 | 167 | 0.519 | 0.581 | 0.477-0.567 |
| 10 | 167 | 0.652 | 0.635 | 0.567-0.953 |

Where the model and the market disagree most (snapshots 2019-2024, per season the top 10 of each; who was right = the label):

| group | players | label = 1 | rate |
|---|---|---|---|
| in both | 28 | 26 | 0.93 |
| in the ECR's top 10, not `logit`'s | 32 | 25 | 0.78 |
| in `logit`'s top 10, not the ECR's | 32 | 14 | 0.44 |

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
| 2022 | Taylor Heinicke | QB | ecr_only | 57 | 7 | 13.9 | 12.9 | 9 | 0 |
| 2022 | Ben Roethlisberger | QB | ecr_only | 42 | 4 | 13.6 | - | 0 | 1 |
| 2022 | Saquon Barkley | RB | model_only | 3 | 87 | 11.4 | 17.8 | 16 | 0 |
| 2022 | Jacoby Brissett | QB | model_only | 2 | 72 | 7.2 | 12.0 | 14 | 0 |
| 2023 | Leonard Fournette | RB | ecr_only | 67 | 4 | 14.1 | 2.0 | 2 | 1 |
| 2023 | Tom Brady | QB | ecr_only | 33 | 1 | 16.0 | - | 0 | 1 |
| 2023 | Irv Smith | TE | model_only | 1 | 89 | 6.9 | 3.4 | 10 | 1 |
| 2023 | Matthew Stafford | QB | model_only | 9 | 93 | 12.0 | 16.2 | 15 | 0 |
| 2024 | Jakobi Meyers | WR | ecr_only | 70 | 10 | 13.7 | 14.5 | 15 | 0 |
| 2024 | Keenan Allen | WR | ecr_only | 46 | 8 | 21.5 | 12.3 | 15 | 1 |
| 2024 | Aaron Jones | RB | model_only | 7 | 80 | 12.3 | 14.2 | 17 | 0 |
| 2024 | Dawson Knox | TE | model_only | 8 | 45 | 5.5 | 4.2 | 14 | 0 |
| 2025 | Keenan Allen | WR | ecr_only | 66 | 10 | 12.3 | 10.7 | 17 | 0 |
| 2025 | Joe Mixon | RB | ecr_only | 34 | 6 | 17.2 | - | 0 | 1 |
| 2025 | Evan Engram | TE | model_only | 8 | 85 | 9.9 | 6.4 | 16 | 1 |
| 2025 | Dallas Goedert | TE | model_only | 7 | 56 | 10.4 | 12.3 | 15 | 0 |

## Read before trusting

- Intervals resample whole seasons (18 in `all`, 6 in `ecr_era`): the ECR-era intervals are wide. Precision@k is the mean over seasons of the share of the season's top k with the label.
- Probabilities are each model's own (no isotonic step); the PPG-rank baseline and the ECR are rankings first.
- Inputs missing in early seasons (NGS before 2016, RYOE before 2018, snap counts before 2013, own xFP before 2009) are imputed inside each fit with a missing indicator.
- Head-coach departures: the owner's cited research file (accepted in bulk, 2026-10-01); a blank date counts as known at the snapshot for fired / mutual / interim types only.
