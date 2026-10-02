# Cliff backtest at the preseason snapshot, anchor week1_kickoff_eve (step I2a / I2-fix)

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
| `logit` | 0.405 [0.354, 0.463] | 0.478 [0.422, 0.539] | 0.392 [0.339, 0.447] | 0.167 [0.155, 0.179] |
| `lgbm` | 0.379 [0.322, 0.441] | 0.450 [0.367, 0.522] | 0.392 [0.330, 0.453] | 0.170 [0.158, 0.181] |
| `base_ppg_rank` | 0.255 [0.225, 0.295] | 0.261 [0.211, 0.317] | 0.264 [0.225, 0.303] | 0.180 [0.168, 0.193] |
| `eos` | 0.363 [0.322, 0.409] | 0.433 [0.350, 0.517] | 0.361 [0.314, 0.408] | 0.171 [0.159, 0.183] |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | +0.150 [0.114, 0.188] | +0.217 [0.172, 0.256] | +0.128 [0.083, 0.175] | 1.00 |
| `logit` - `eos` | +0.042 [0.019, 0.067] | +0.044 [-0.017, 0.106] | +0.031 [-0.003, 0.061] | 1.00 |
| `lgbm` - `base_ppg_rank` | +0.124 [0.086, 0.165] | +0.189 [0.122, 0.256] | +0.128 [0.078, 0.178] | 1.00 |
| `lgbm` - `eos` | +0.016 [-0.012, 0.044] | +0.017 [-0.044, 0.072] | +0.031 [-0.011, 0.072] | 0.86 |
| `base_ppg_rank` - `eos` | -0.108 [-0.143, -0.076] | -0.172 [-0.228, -0.106] | -0.097 [-0.147, -0.053] | 0.00 |

**snapshots 2019-2024 (labels 2020-2025), with the ECR**

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit` | 0.459 [0.371, 0.562] | 0.567 [0.433, 0.683] | 0.450 [0.358, 0.542] | 0.168 [0.151, 0.183] |
| `lgbm` | 0.453 [0.330, 0.576] | 0.533 [0.383, 0.650] | 0.483 [0.375, 0.575] | 0.166 [0.150, 0.182] |
| `base_ppg_rank` | 0.299 [0.234, 0.381] | 0.317 [0.217, 0.433] | 0.292 [0.233, 0.350] | 0.185 [0.163, 0.206] |
| `eos` | 0.410 [0.341, 0.484] | 0.483 [0.317, 0.683] | 0.417 [0.367, 0.459] | 0.173 [0.154, 0.190] |
| `ecr` | 0.503 [0.424, 0.570] | 0.533 [0.417, 0.650] | 0.450 [0.375, 0.525] | - |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | +0.161 [0.096, 0.230] | +0.250 [0.183, 0.300] | +0.158 [0.092, 0.233] | 1.00 |
| `logit` - `eos` | +0.049 [0.014, 0.094] | +0.083 [0.000, 0.167] | +0.033 [-0.033, 0.108] | 1.00 |
| `logit` - `ecr` | -0.043 [-0.129, 0.042] | +0.033 [-0.033, 0.083] | -0.000 [-0.058, 0.075] | 0.22 |
| `lgbm` - `base_ppg_rank` | +0.154 [0.073, 0.267] | +0.217 [0.117, 0.317] | +0.192 [0.117, 0.275] | 1.00 |
| `lgbm` - `eos` | +0.043 [-0.008, 0.110] | +0.050 [-0.050, 0.133] | +0.067 [0.000, 0.133] | 0.95 |
| `lgbm` - `ecr` | -0.050 [-0.129, 0.044] | 0.000 [-0.083, 0.100] | +0.033 [-0.025, 0.100] | 0.17 |
| `base_ppg_rank` - `eos` | -0.111 [-0.177, -0.052] | -0.167 [-0.250, -0.083] | -0.125 [-0.183, -0.075] | 0.00 |
| `base_ppg_rank` - `ecr` | -0.204 [-0.254, -0.152] | -0.217 [-0.300, -0.133] | -0.158 [-0.183, -0.133] | 0.00 |

Top features (mean share of the importance over the 18 folds; logit = spread of the feature's log-odds contribution, sign from the 2024 fold's standardized coefficient; LightGBM = gain):

- `logit`: `depth_rank_s1` (+) 11%, `ppg_change` (+) 10%, `age_curve_ratio` (-) 6%, `pos_rb` (+) 5%, `team_change_s1` (+) 5%, `games_s` (-) 4%, `touches_per_game_s` (+) 4%, `yards_per_touch_s` (+) 4%
- `lgbm`: `ppg_change` 16%, `depth_rank_s1` 12%, `age_curve_ratio` 10%, `touches_per_game_s` 9%, `touches_s` 6%, `yards_per_touch_s` 5%, `vacated_carries_share_s1` 5%, `age` 5%

Importance smoke test (spec 6.2 rule 6), folds whose top feature holds over 40%: lgbm 2007: ppg_change 100%

Calibration of `logit` (its own probability; 10 equal-count bins, every test season):

| bin | rows | mean predicted | observed | range |
|---|---|---|---|---|
| 1 | 143 | 0.074 | 0.084 | 0.000-0.102 |
| 2 | 142 | 0.123 | 0.099 | 0.102-0.141 |
| 3 | 142 | 0.156 | 0.183 | 0.141-0.168 |
| 4 | 143 | 0.181 | 0.210 | 0.168-0.194 |
| 5 | 142 | 0.206 | 0.162 | 0.194-0.218 |
| 6 | 142 | 0.231 | 0.254 | 0.218-0.243 |
| 7 | 143 | 0.258 | 0.259 | 0.243-0.278 |
| 8 | 142 | 0.297 | 0.275 | 0.279-0.317 |
| 9 | 142 | 0.351 | 0.331 | 0.317-0.390 |
| 10 | 142 | 0.518 | 0.514 | 0.390-0.988 |

Where the model and the market disagree most (snapshots 2019-2024, per season the top 10 of each; who was right = the label):

| group | players | label = 1 | rate |
|---|---|---|---|
| in both | 21 | 18 | 0.86 |
| in the ECR's top 10, not `logit`'s | 39 | 14 | 0.36 |
| in `logit`'s top 10, not the ECR's | 39 | 16 | 0.41 |

Largest rank gaps (2 per season and side):

| label season | player | pos | group | rank by model | rank by ECR score | PPG S | PPG S+1 | games S+1 | label |
|---|---|---|---|---|---|---|---|---|---|
| 2020 | Golden Tate | WR | ecr_only | 46 | 6 | 13.8 | 7.2 | 12 | 1 |
| 2020 | Julian Edelman | WR | ecr_only | 43 | 4 | 16.0 | 9.4 | 6 | 1 |
| 2020 | David Johnson | RB | model_only | 2 | 70 | 11.8 | 15.0 | 12 | 0 |
| 2020 | Todd Gurley | RB | model_only | 1 | 54 | 14.6 | 10.9 | 15 | 0 |
| 2021 | Sterling Shepard | WR | ecr_only | 53 | 6 | 13.5 | 11.1 | 7 | 0 |
| 2021 | Cole Beasley | WR | ecr_only | 30 | 5 | 13.8 | 10.0 | 16 | 0 |
| 2021 | Mike Davis | RB | model_only | 8 | 36 | 13.8 | 8.1 | 17 | 1 |
| 2021 | Kyle Rudolph | TE | model_only | 10 | 27 | 5.5 | 4.0 | 15 | 0 |
| 2022 | Taylor Heinicke | QB | ecr_only | 61 | 3 | 13.9 | 12.9 | 9 | 0 |
| 2022 | Jimmy Garoppolo | QB | ecr_only | 50 | 10 | 15.2 | 15.0 | 11 | 0 |
| 2022 | Saquon Barkley | RB | model_only | 3 | 79 | 11.4 | 17.8 | 16 | 0 |
| 2022 | Chase Edmonds | RB | model_only | 2 | 60 | 11.9 | 5.7 | 13 | 1 |
| 2023 | Cooper Rush | QB | ecr_only | 61 | 6 | 7.1 | 0.5 | 7 | 1 |
| 2023 | Cordarrelle Patterson | RB | ecr_only | 41 | 4 | 11.9 | 3.1 | 12 | 1 |
| 2023 | Hayden Hurst | TE | model_only | 6 | 48 | 8.1 | 4.7 | 9 | 1 |
| 2023 | Cooper Kupp | WR | model_only | 9 | 46 | 22.4 | 13.7 | 12 | 1 |
| 2024 | Jakobi Meyers | WR | ecr_only | 46 | 6 | 13.7 | 14.5 | 15 | 0 |
| 2024 | Adam Thielen | WR | ecr_only | 37 | 3 | 13.6 | 13.9 | 10 | 0 |
| 2024 | Dawson Knox | TE | model_only | 9 | 37 | 5.5 | 4.2 | 14 | 0 |
| 2024 | Gus Edwards | RB | model_only | 6 | 32 | 11.0 | 5.8 | 11 | 1 |
| 2025 | Keenan Allen | WR | ecr_only | 61 | 5 | 12.3 | 10.7 | 17 | 0 |
| 2025 | Mac Jones | QB | ecr_only | 44 | 10 | 9.6 | 11.8 | 11 | 0 |
| 2025 | Jonnu Smith | TE | model_only | 3 | 20 | 13.1 | 5.0 | 17 | 1 |
| 2025 | Noah Fant | TE | model_only | 4 | 21 | 7.4 | 5.8 | 13 | 0 |

### Missed most of S+1 (under 6 games) (`cliff_missed`, label `y_missed`)

**snapshots 2007-2024 (labels 2008-2025)**

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit_simple` | 0.642 [0.585, 0.699] | 0.728 [0.656, 0.794] | 0.519 [0.456, 0.583] | 0.078 [0.068, 0.089] |
| `base_ppg_rank` | 0.242 [0.209, 0.281] | 0.283 [0.217, 0.350] | 0.264 [0.217, 0.311] | 0.122 [0.111, 0.135] |
| `eos` | 0.384 [0.330, 0.439] | 0.433 [0.361, 0.500] | 0.322 [0.269, 0.375] | 0.113 [0.102, 0.125] |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit_simple` - `base_ppg_rank` | +0.400 [0.325, 0.468] | +0.444 [0.361, 0.522] | +0.256 [0.200, 0.311] | 1.00 |
| `logit_simple` - `eos` | +0.258 [0.209, 0.308] | +0.294 [0.233, 0.361] | +0.197 [0.156, 0.242] | 1.00 |
| `base_ppg_rank` - `eos` | -0.142 [-0.204, -0.079] | -0.150 [-0.233, -0.061] | -0.058 [-0.114, -0.003] | 0.00 |

**snapshots 2019-2024 (labels 2020-2025), with the ECR**

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit_simple` | 0.634 [0.552, 0.725] | 0.600 [0.483, 0.750] | 0.425 [0.325, 0.567] | 0.072 [0.056, 0.086] |
| `base_ppg_rank` | 0.178 [0.123, 0.257] | 0.200 [0.100, 0.300] | 0.192 [0.108, 0.292] | 0.108 [0.089, 0.133] |
| `eos` | 0.352 [0.233, 0.484] | 0.383 [0.250, 0.533] | 0.283 [0.192, 0.408] | 0.099 [0.084, 0.116] |
| `ecr` | 0.570 [0.479, 0.662] | 0.517 [0.417, 0.650] | 0.358 [0.283, 0.459] | - |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit_simple` - `base_ppg_rank` | +0.456 [0.386, 0.509] | +0.400 [0.333, 0.500] | +0.233 [0.117, 0.350] | 1.00 |
| `logit_simple` - `eos` | +0.282 [0.222, 0.343] | +0.217 [0.133, 0.283] | +0.142 [0.108, 0.175] | 1.00 |
| `logit_simple` - `ecr` | +0.064 [-0.051, 0.128] | +0.083 [0.000, 0.167] | +0.067 [0.025, 0.117] | 0.89 |
| `base_ppg_rank` - `eos` | -0.174 [-0.246, -0.081] | -0.183 [-0.300, -0.067] | -0.092 [-0.183, 0.000] | 0.00 |
| `base_ppg_rank` - `ecr` | -0.392 [-0.490, -0.292] | -0.317 [-0.433, -0.200] | -0.167 [-0.250, -0.050] | 0.00 |

Top features (mean share of the importance over the 18 folds; logit = spread of the feature's log-odds contribution, sign from the 2024 fold's standardized coefficient; LightGBM = gain):

- `logit_simple`: `depth_rank_s1` (+) 14%, `pos_wr` (-) 13%, `games_s` (-) 10%, `prior_seasons` (+) 9%, `vacated_targets_share_s1` (-) 9%, `pos_rb` (-) 7%, `new_competitor_s1` (-) 7%, `team_change_s1` (+) 5%

Calibration of `logit_simple` (its own probability; 10 equal-count bins, every test season):

| bin | rows | mean predicted | observed | range |
|---|---|---|---|---|
| 1 | 168 | 0.013 | 0.012 | 0.003-0.018 |
| 2 | 167 | 0.022 | 0.042 | 0.018-0.027 |
| 3 | 168 | 0.032 | 0.060 | 0.027-0.037 |
| 4 | 167 | 0.042 | 0.030 | 0.037-0.047 |
| 5 | 167 | 0.053 | 0.054 | 0.047-0.060 |
| 6 | 168 | 0.070 | 0.048 | 0.060-0.078 |
| 7 | 167 | 0.093 | 0.066 | 0.078-0.109 |
| 8 | 168 | 0.132 | 0.071 | 0.109-0.166 |
| 9 | 167 | 0.295 | 0.377 | 0.166-0.587 |
| 10 | 167 | 0.784 | 0.743 | 0.588-0.982 |

Where the model and the market disagree most (snapshots 2019-2024, per season the top 10 of each; who was right = the label):

| group | players | label = 1 | rate |
|---|---|---|---|
| in both | 34 | 28 | 0.82 |
| in the ECR's top 10, not `logit_simple`'s | 26 | 3 | 0.12 |
| in `logit_simple`'s top 10, not the ECR's | 26 | 8 | 0.31 |

Largest rank gaps (2 per season and side):

| label season | player | pos | group | rank by model | rank by ECR score | PPG S | PPG S+1 | games S+1 | label |
|---|---|---|---|---|---|---|---|---|---|
| 2020 | Julian Edelman | WR | ecr_only | 53 | 8 | 16.0 | 9.4 | 6 | 0 |
| 2020 | Alshon Jeffery | WR | ecr_only | 28 | 3 | 13.6 | 3.4 | 7 | 0 |
| 2020 | Tom Brady | QB | model_only | 9 | 70 | 16.5 | 21.1 | 16 | 0 |
| 2020 | Drew Brees | QB | model_only | 6 | 38 | 20.4 | 17.5 | 12 | 0 |
| 2021 | Rex Burkhead | RB | ecr_only | 22 | 6 | 10.8 | 7.0 | 15 | 0 |
| 2021 | Todd Gurley | RB | ecr_only | 13 | 5 | 10.9 | - | 0 | 1 |
| 2021 | Nick Foles | QB | model_only | 3 | 22 | 11.6 | 16.8 | 1 | 1 |
| 2021 | Tyler Eifert | TE | model_only | 10 | 27 | 5.5 | - | 0 | 1 |
| 2022 | Corey Davis | WR | ecr_only | 47 | 5 | 11.7 | 7.5 | 13 | 0 |
| 2022 | Robert Woods | WR | ecr_only | 36 | 9 | 15.2 | 6.8 | 17 | 0 |
| 2022 | Robert Tonyan | TE | model_only | 2 | 81 | 6.3 | 6.6 | 17 | 0 |
| 2022 | Sam Darnold | QB | model_only | 5 | 27 | 13.1 | 14.4 | 6 | 0 |
| 2023 | Ezekiel Elliott | RB | ecr_only | 59 | 9 | 12.4 | 10.3 | 17 | 0 |
| 2023 | Mecole Hardman | WR | ecr_only | 26 | 3 | 11.7 | 3.7 | 7 | 0 |
| 2023 | Dalton Schultz | TE | model_only | 7 | 72 | 9.5 | 10.0 | 15 | 0 |
| 2023 | Cooper Kupp | WR | model_only | 5 | 55 | 22.4 | 13.7 | 12 | 0 |
| 2024 | Adam Thielen | WR | ecr_only | 81 | 5 | 13.6 | 13.9 | 10 | 0 |
| 2024 | Jakobi Meyers | WR | ecr_only | 79 | 10 | 13.7 | 14.5 | 15 | 0 |
| 2024 | Russell Wilson | QB | model_only | 2 | 16 | 17.1 | 15.7 | 11 | 0 |
| 2024 | T.J. Hockenson | TE | model_only | 8 | 19 | 14.6 | 8.7 | 10 | 0 |
| 2025 | Keenan Allen | WR | ecr_only | 56 | 10 | 12.3 | 10.7 | 17 | 0 |
| 2025 | Rico Dowdle | RB | ecr_only | 34 | 9 | 12.4 | 12.7 | 17 | 0 |
| 2025 | Kirk Cousins | QB | model_only | 9 | 38 | 12.6 | 10.4 | 10 | 0 |
| 2025 | Jameis Winston | QB | model_only | 4 | 32 | 11.9 | 14.4 | 3 | 1 |

### Cliff counting a missed season as a cliff (sensitivity) (`cliff_sensitivity`, label `y_cliff_or_missed`)

**snapshots 2007-2024 (labels 2008-2025)**

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit` | 0.673 [0.633, 0.709] | 0.839 [0.794, 0.878] | 0.747 [0.697, 0.794] | 0.179 [0.170, 0.189] |
| `lgbm` | 0.641 [0.580, 0.696] | 0.800 [0.744, 0.844] | 0.700 [0.631, 0.761] | 0.183 [0.172, 0.194] |
| `base_ppg_rank` | 0.433 [0.400, 0.468] | 0.478 [0.411, 0.544] | 0.483 [0.444, 0.517] | 0.221 [0.215, 0.228] |
| `eos` | 0.535 [0.490, 0.579] | 0.650 [0.583, 0.706] | 0.589 [0.536, 0.639] | 0.206 [0.198, 0.214] |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | +0.240 [0.206, 0.268] | +0.361 [0.306, 0.422] | +0.264 [0.222, 0.306] | 1.00 |
| `logit` - `eos` | +0.138 [0.117, 0.156] | +0.189 [0.122, 0.250] | +0.158 [0.117, 0.208] | 1.00 |
| `lgbm` - `base_ppg_rank` | +0.208 [0.159, 0.257] | +0.322 [0.256, 0.389] | +0.217 [0.167, 0.269] | 1.00 |
| `lgbm` - `eos` | +0.106 [0.069, 0.140] | +0.150 [0.083, 0.228] | +0.111 [0.058, 0.164] | 1.00 |
| `base_ppg_rank` - `eos` | -0.102 [-0.139, -0.060] | -0.172 [-0.261, -0.078] | -0.106 [-0.156, -0.058] | 0.00 |

**snapshots 2019-2024 (labels 2020-2025), with the ECR**

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit` | 0.669 [0.583, 0.732] | 0.817 [0.717, 0.900] | 0.742 [0.633, 0.842] | 0.178 [0.166, 0.191] |
| `lgbm` | 0.614 [0.482, 0.747] | 0.750 [0.650, 0.833] | 0.675 [0.517, 0.817] | 0.181 [0.162, 0.199] |
| `base_ppg_rank` | 0.417 [0.350, 0.492] | 0.500 [0.400, 0.583] | 0.450 [0.367, 0.533] | 0.220 [0.207, 0.231] |
| `eos` | 0.546 [0.473, 0.610] | 0.667 [0.550, 0.750] | 0.642 [0.575, 0.700] | 0.201 [0.191, 0.212] |
| `ecr` | 0.689 [0.637, 0.736] | 0.850 [0.783, 0.933] | 0.642 [0.558, 0.725] | - |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | +0.252 [0.211, 0.291] | +0.317 [0.300, 0.350] | +0.292 [0.217, 0.375] | 1.00 |
| `logit` - `eos` | +0.123 [0.098, 0.138] | +0.150 [0.083, 0.200] | +0.100 [0.050, 0.150] | 1.00 |
| `logit` - `ecr` | -0.020 [-0.081, 0.032] | -0.033 [-0.150, 0.083] | +0.100 [0.025, 0.175] | 0.24 |
| `lgbm` - `base_ppg_rank` | +0.197 [0.106, 0.314] | +0.250 [0.167, 0.333] | +0.225 [0.133, 0.350] | 1.00 |
| `lgbm` - `eos` | +0.068 [0.014, 0.149] | +0.083 [0.050, 0.100] | +0.033 [-0.058, 0.125] | 0.99 |
| `lgbm` - `ecr` | -0.075 [-0.183, 0.024] | -0.100 [-0.183, -0.033] | +0.033 [-0.058, 0.125] | 0.18 |
| `base_ppg_rank` - `eos` | -0.129 [-0.175, -0.087] | -0.167 [-0.233, -0.100] | -0.192 [-0.250, -0.133] | 0.00 |
| `base_ppg_rank` - `ecr` | -0.272 [-0.337, -0.197] | -0.350 [-0.467, -0.217] | -0.192 [-0.275, -0.083] | 0.00 |

Top features (mean share of the importance over the 18 folds; logit = spread of the feature's log-odds contribution, sign from the 2024 fold's standardized coefficient; LightGBM = gain):

- `logit`: `depth_rank_s1` (+) 13%, `ppg_change` (+) 8%, `games_s` (-) 6%, `team_change_s1` (+) 5%, `vacated_targets_share_s1` (-) 5%, `age_curve_ratio` (-) 5%, `prior_seasons` (+) 5%, `new_competitor_s1` (-) 4%
- `lgbm`: `depth_rank_s1` 38%, `vacated_carries_share_s1` 8%, `age_curve_ratio` 6%, `ppg_change` 6%, `vacated_targets_share_s1` 5%, `team_change_s1` 5%, `ppg_s` 4%, `age` 4%

Importance smoke test (spec 6.2 rule 6), folds whose top feature holds over 40%: lgbm 2007: depth_rank_s1 43%; lgbm 2008: vacated_carries_share_s1 48%; lgbm 2012: depth_rank_s1 44%; lgbm 2013: depth_rank_s1 45%; lgbm 2016: depth_rank_s1 45%; lgbm 2017: depth_rank_s1 45%; lgbm 2020: depth_rank_s1 48%; lgbm 2022: depth_rank_s1 69%; lgbm 2023: depth_rank_s1 48%

Calibration of `logit` (its own probability; 10 equal-count bins, every test season):

| bin | rows | mean predicted | observed | range |
|---|---|---|---|---|
| 1 | 168 | 0.115 | 0.149 | 0.045-0.145 |
| 2 | 167 | 0.165 | 0.156 | 0.145-0.183 |
| 3 | 168 | 0.196 | 0.196 | 0.183-0.209 |
| 4 | 167 | 0.226 | 0.240 | 0.210-0.242 |
| 5 | 167 | 0.263 | 0.204 | 0.242-0.282 |
| 6 | 168 | 0.301 | 0.298 | 0.282-0.322 |
| 7 | 167 | 0.356 | 0.347 | 0.323-0.392 |
| 8 | 168 | 0.435 | 0.435 | 0.392-0.485 |
| 9 | 167 | 0.601 | 0.635 | 0.486-0.766 |
| 10 | 167 | 0.862 | 0.856 | 0.769-0.980 |

Where the model and the market disagree most (snapshots 2019-2024, per season the top 10 of each; who was right = the label):

| group | players | label = 1 | rate |
|---|---|---|---|
| in both | 34 | 33 | 0.97 |
| in the ECR's top 10, not `logit`'s | 26 | 18 | 0.69 |
| in `logit`'s top 10, not the ECR's | 26 | 16 | 0.62 |

Largest rank gaps (2 per season and side):

| label season | player | pos | group | rank by model | rank by ECR score | PPG S | PPG S+1 | games S+1 | label |
|---|---|---|---|---|---|---|---|---|---|
| 2020 | Julian Edelman | WR | ecr_only | 50 | 8 | 16.0 | 9.4 | 6 | 1 |
| 2020 | Golden Tate | WR | ecr_only | 43 | 10 | 13.8 | 7.2 | 12 | 1 |
| 2020 | David Johnson | RB | model_only | 7 | 76 | 11.8 | 15.0 | 12 | 0 |
| 2020 | Tyler Higbee | TE | model_only | 10 | 64 | 11.5 | 8.5 | 15 | 0 |
| 2021 | Philip Rivers | QB | ecr_only | 17 | 3 | 15.0 | - | 0 | 1 |
| 2021 | Drew Brees | QB | ecr_only | 13 | 2 | 17.5 | - | 0 | 1 |
| 2021 | J.D. McKissic | RB | model_only | 3 | 24 | 12.0 | 11.6 | 11 | 0 |
| 2021 | Tyler Eifert | TE | model_only | 9 | 27 | 5.5 | - | 0 | 1 |
| 2022 | Corey Davis | WR | ecr_only | 42 | 5 | 11.7 | 7.5 | 13 | 1 |
| 2022 | Taylor Heinicke | QB | ecr_only | 43 | 7 | 13.9 | 12.9 | 9 | 0 |
| 2022 | Robert Tonyan | TE | model_only | 6 | 81 | 6.3 | 6.6 | 17 | 0 |
| 2022 | D'Onta Foreman | RB | model_only | 10 | 14 | 10.4 | 8.2 | 16 | 0 |
| 2023 | Ezekiel Elliott | RB | ecr_only | 42 | 9 | 12.4 | 10.3 | 17 | 0 |
| 2023 | Cordarrelle Patterson | RB | ecr_only | 26 | 8 | 11.9 | 3.1 | 12 | 1 |
| 2023 | Cooper Kupp | WR | model_only | 9 | 55 | 22.4 | 13.7 | 12 | 1 |
| 2023 | Alvin Kamara | RB | model_only | 6 | 28 | 14.1 | 17.9 | 13 | 0 |
| 2024 | Jakobi Meyers | WR | ecr_only | 67 | 10 | 13.7 | 14.5 | 15 | 0 |
| 2024 | Adam Thielen | WR | ecr_only | 48 | 5 | 13.6 | 13.9 | 10 | 0 |
| 2024 | Tyrod Taylor | QB | model_only | 10 | 25 | 8.7 | 10.0 | 2 | 1 |
| 2024 | T.J. Hockenson | TE | model_only | 9 | 19 | 14.6 | 8.7 | 10 | 1 |
| 2025 | Keenan Allen | WR | ecr_only | 68 | 10 | 12.3 | 10.7 | 17 | 0 |
| 2025 | Adam Thielen | WR | ecr_only | 17 | 7 | 13.9 | 2.8 | 14 | 1 |
| 2025 | Jameis Winston | QB | model_only | 7 | 32 | 11.9 | 14.4 | 3 | 1 |
| 2025 | Noah Fant | TE | model_only | 9 | 29 | 7.4 | 5.8 | 13 | 0 |

## Read before trusting

- Intervals resample whole seasons (18 in `all`, 6 in `ecr_era`): the ECR-era intervals are wide. Precision@k is the mean over seasons of the share of the season's top k with the label.
- Probabilities are each model's own (no isotonic step); the PPG-rank baseline and the ECR are rankings first.
- Inputs missing in early seasons (NGS before 2016, RYOE before 2018, snap counts before 2013, own xFP before 2009) are imputed inside each fit with a missing indicator.
- Head-coach departures: the owner's cited research file (accepted in bulk, 2026-10-01); a blank date counts as known at the snapshot for fired / mutual / interim types only.
