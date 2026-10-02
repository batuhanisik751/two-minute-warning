# Breakout backtest (step I1b)

Which WR/TE (and, reported apart, RB) entering their 2nd or 3rd season, not already top-36 WR / top-12 TE / top-24 RB in PPG, finish top-24 WR / top-12 TE / top-24 RB (8+ games) next season? Features at the end-of-season snapshot of S, label from S+1, walk-forward by snapshot season (training from 2002). Team vacated targets are not a feature: no point-in-time offseason roster exists at the snapshot (docs/board.md).

Baselines: last season's PPG rank (one-feature logit; an unranked player gets the training median rank plus a missing flag) and the preseason FantasyPros ECR of S+1 (2020+ only; ECR, not ADP), scored as minus the ECR rank (unranked last).

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
| `logit` | 0.235 [0.170, 0.362] | 0.239 [0.200, 0.289] | 0.167 [0.142, 0.192] | 0.037 [0.030, 0.045] |
| `lgbm` | 0.200 [0.154, 0.274] | 0.244 [0.189, 0.300] | 0.172 [0.147, 0.200] | 0.038 [0.032, 0.045] |
| `base_ppg_rank` | 0.251 [0.197, 0.336] | 0.244 [0.206, 0.289] | 0.164 [0.136, 0.192] | 0.037 [0.031, 0.043] |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | -0.016 [-0.096, 0.088] | -0.006 [-0.056, 0.039] | +0.003 [-0.022, 0.028] | 0.38 |
| `lgbm` - `base_ppg_rank` | -0.050 [-0.120, 0.002] | -0.000 [-0.033, 0.033] | +0.008 [-0.014, 0.031] | 0.03 |

**snapshots 2019-2024 (labels 2020-2025), with the ECR**

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit` | 0.343 [0.148, 0.646] | 0.267 [0.167, 0.367] | 0.158 [0.100, 0.208] | 0.028 [0.022, 0.035] |
| `lgbm` | 0.229 [0.135, 0.448] | 0.233 [0.133, 0.317] | 0.175 [0.142, 0.208] | 0.031 [0.028, 0.035] |
| `base_ppg_rank` | 0.259 [0.160, 0.451] | 0.217 [0.167, 0.267] | 0.150 [0.117, 0.192] | 0.031 [0.029, 0.032] |
| `ecr` | 0.315 [0.217, 0.439] | 0.267 [0.200, 0.333] | 0.167 [0.133, 0.208] | - |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | +0.084 [-0.072, 0.288] | +0.050 [-0.017, 0.100] | +0.008 [-0.050, 0.058] | 0.81 |
| `logit` - `ecr` | +0.028 [-0.080, 0.251] | -0.000 [-0.067, 0.067] | -0.008 [-0.058, 0.042] | 0.65 |
| `lgbm` - `base_ppg_rank` | -0.031 [-0.123, 0.080] | +0.017 [-0.033, 0.067] | +0.025 [-0.009, 0.058] | 0.25 |
| `lgbm` - `ecr` | -0.087 [-0.127, 0.001] | -0.033 [-0.117, 0.033] | +0.008 [-0.025, 0.050] | 0.03 |
| `base_ppg_rank` - `ecr` | -0.056 [-0.137, 0.048] | -0.050 [-0.083, -0.017] | -0.017 [-0.033, 0.000] | 0.29 |

Top features (mean share of the importance over the 18 folds; logit = spread of the feature's log-odds contribution, sign from the 2024 fold's standardized coefficient; LightGBM = gain):

- `logit`: `games_s` (+) 12%, `pos_te` (+) 9%, `ppg_s` (+) 9%, `drafted_round` (-) 8%, `yards_per_team_pass_att_s` (+) 8%, `touches_per_game_s` (+) 5%, `air_yards_share_s` (+) 5%, `drafted_pick` (-) 5%
- `lgbm`: `ppg_s` 31%, `games_s` 13%, `drafted_pick` 12%, `yards_per_team_pass_att_s` 6%, `combine_speed_score` 5%, `combine_weight` 4%, `touches_per_game_s` 4%, `age` 4%

Importance smoke test (spec 6.2 rule 6), folds whose top feature holds over 40%: lgbm 2009: ppg_s 49%; lgbm 2010: ppg_s 40%; lgbm 2012: ppg_s 40%

Calibration of `logit` (its own probability; 10 equal-count bins, every test season):

| bin | rows | mean predicted | observed | range |
|---|---|---|---|---|
| 1 | 185 | 0.001 | 0.000 | 0.000-0.002 |
| 2 | 185 | 0.003 | 0.005 | 0.002-0.004 |
| 3 | 185 | 0.006 | 0.005 | 0.004-0.008 |
| 4 | 185 | 0.009 | 0.000 | 0.008-0.011 |
| 5 | 185 | 0.014 | 0.016 | 0.011-0.017 |
| 6 | 185 | 0.020 | 0.011 | 0.017-0.024 |
| 7 | 185 | 0.031 | 0.022 | 0.024-0.039 |
| 8 | 185 | 0.051 | 0.065 | 0.039-0.067 |
| 9 | 185 | 0.095 | 0.081 | 0.068-0.129 |
| 10 | 185 | 0.255 | 0.232 | 0.130-0.771 |

Where the model and the market disagree most (snapshots 2019-2024, per season the top 10 of each; who was right = the label):

| group | players | label = 1 | rate |
|---|---|---|---|
| in both | 36 | 13 | 0.36 |
| in the ECR's top 10, not `logit`'s | 24 | 3 | 0.12 |
| in `logit`'s top 10, not the ECR's | 24 | 3 | 0.12 |

Largest rank gaps (2 per season and side):

| label season | player | pos | group | rank by model | rank by ECR score | PPG S | PPG S+1 | games S+1 | label |
|---|---|---|---|---|---|---|---|---|---|
| 2020 | Chris Herndon | TE | ecr_only | 73 | 5 | 1.7 | 6.1 | 12 | 0 |
| 2020 | Jace Sternberger | TE | ecr_only | 59 | 9 | 0.0 | 3.7 | 8 | 0 |
| 2020 | James Washington | WR | model_only | 7 | 27 | 8.9 | 6.2 | 16 | 0 |
| 2020 | Mecole Hardman | WR | model_only | 10 | 20 | 7.5 | 8.0 | 16 | 0 |
| 2021 | Juwan Johnson | WR | ecr_only | 90 | 5 | 1.6 | 4.1 | 13 | 0 |
| 2021 | Deebo Samuel Sr. | WR | ecr_only | 38 | 6 | 11.5 | 21.2 | 16 | 1 |
| 2021 | Irv Smith | TE | model_only | 2 | 68 | 7.6 | - | 0 | 0 |
| 2021 | Gabe Davis | WR | model_only | 6 | 23 | 8.6 | 8.4 | 15 | 0 |
| 2022 | Brevin Jordan | TE | ecr_only | 27 | 4 | 7.0 | 3.0 | 9 | 0 |
| 2022 | Gabe Davis | WR | ecr_only | 24 | 8 | 8.4 | 11.4 | 15 | 0 |
| 2022 | Henry Ruggs III | WR | model_only | 6 | 100 | 12.1 | - | 0 | 0 |
| 2022 | Van Jefferson | WR | model_only | 9 | 24 | 9.9 | 7.9 | 10 | 0 |
| 2023 | Jake Ferguson | TE | ecr_only | 29 | 5 | 4.4 | 10.4 | 17 | 1 |
| 2023 | Chig Okonkwo | TE | ecr_only | 11 | 2 | 6.1 | 6.7 | 17 | 0 |
| 2023 | Josh Palmer | WR | model_only | 6 | 39 | 10.6 | 10.7 | 10 | 0 |
| 2023 | Alec Pierce | WR | model_only | 7 | 30 | 7.0 | 5.6 | 17 | 0 |
| 2024 | Greg Dulcich | TE | ecr_only | 45 | 9 | 2.8 | 1.9 | 4 | 0 |
| 2024 | Tucker Kraft | TE | ecr_only | 12 | 8 | 5.6 | 9.6 | 17 | 0 |
| 2024 | Josh Downs | WR | model_only | 8 | 22 | 9.2 | 13.1 | 14 | 0 |
| 2024 | Jaxon Smith-Njigba | WR | model_only | 4 | 11 | 8.8 | 14.9 | 17 | 1 |
| 2025 | Ja'Tavion Sanders | TE | ecr_only | 18 | 6 | 4.9 | 4.5 | 12 | 0 |
| 2025 | Brenton Strange | TE | ecr_only | 11 | 4 | 6.5 | 9.8 | 12 | 0 |
| 2025 | Quentin Johnston | WR | model_only | 5 | 28 | 11.6 | 13.2 | 13 | 1 |
| 2025 | Xavier Legette | WR | model_only | 8 | 26 | 8.3 | 6.0 | 15 | 0 |

### Breakout RB (`breakout_rb`, label `y_breakout`)

**snapshots 2007-2024 (labels 2008-2025)**

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit` | 0.234 [0.146, 0.359] | 0.211 [0.139, 0.289] | 0.142 [0.094, 0.194] | 0.058 [0.044, 0.073] |
| `lgbm` | 0.209 [0.135, 0.314] | 0.189 [0.122, 0.267] | 0.142 [0.097, 0.192] | 0.059 [0.044, 0.074] |
| `base_ppg_rank` | 0.186 [0.138, 0.270] | 0.206 [0.139, 0.272] | 0.136 [0.100, 0.172] | 0.059 [0.044, 0.076] |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | +0.048 [-0.054, 0.148] | +0.006 [-0.028, 0.044] | +0.006 [-0.017, 0.031] | 0.81 |
| `lgbm` - `base_ppg_rank` | +0.023 [-0.052, 0.087] | -0.017 [-0.056, 0.022] | +0.006 [-0.011, 0.022] | 0.73 |

**snapshots 2019-2024 (labels 2020-2025), with the ECR**

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit` | 0.335 [0.069, 0.464] | 0.167 [0.050, 0.317] | 0.100 [0.042, 0.183] | 0.041 [0.023, 0.060] |
| `lgbm` | 0.177 [0.044, 0.314] | 0.117 [0.017, 0.233] | 0.100 [0.025, 0.183] | 0.043 [0.021, 0.063] |
| `base_ppg_rank` | 0.161 [0.084, 0.274] | 0.150 [0.050, 0.267] | 0.100 [0.042, 0.167] | 0.042 [0.021, 0.066] |
| `ecr` | 0.378 [0.141, 0.791] | 0.183 [0.083, 0.317] | 0.100 [0.042, 0.183] | - |

Paired differences (same seasons resampled for both):

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | +0.174 [-0.051, 0.296] | +0.017 [0.000, 0.050] | 0.000 [-0.025, 0.025] | 0.91 |
| `logit` - `ecr` | -0.043 [-0.466, 0.256] | -0.017 [-0.050, 0.000] | 0.000 [0.000, 0.000] | 0.31 |
| `lgbm` - `base_ppg_rank` | +0.016 [-0.088, 0.108] | -0.033 [-0.067, 0.000] | -0.000 [-0.025, 0.025] | 0.62 |
| `lgbm` - `ecr` | -0.201 [-0.572, 0.019] | -0.067 [-0.100, -0.033] | -0.000 [-0.025, 0.025] | 0.04 |
| `base_ppg_rank` - `ecr` | -0.217 [-0.562, -0.027] | -0.033 [-0.067, 0.000] | 0.000 [-0.025, 0.025] | 0.00 |

Top features (mean share of the importance over the 18 folds; logit = spread of the feature's log-odds contribution, sign from the 2024 fold's standardized coefficient; LightGBM = gain):

- `logit`: `ppg_s` (+) 8%, `games_s` (+) 7%, `drafted_pick` (-) 7%, `target_share_s` (-) 6%, `drafted_round` (+) 6%, `age` (-) 5%, `combine_speed_score` (+) 5%, `yards_per_team_pass_att_s` (+) 5%
- `lgbm`: `drafted_pick` 26%, `touches_per_game_s` 15%, `ppg_s` 13%, `yards_per_team_pass_att_s` 8%, `combine_speed_score` 6%, `age` 5%, `team_any_a` 5%, `air_yards_share_s` 4%

Importance smoke test (spec 6.2 rule 6), folds whose top feature holds over 40%: lgbm 2012: drafted_pick 59%

Calibration of `logit` (its own probability; 10 equal-count bins, every test season):

| bin | rows | mean predicted | observed | range |
|---|---|---|---|---|
| 1 | 87 | 0.010 | 0.011 | 0.002-0.016 |
| 2 | 87 | 0.019 | 0.000 | 0.016-0.023 |
| 3 | 86 | 0.026 | 0.012 | 0.023-0.030 |
| 4 | 87 | 0.034 | 0.034 | 0.030-0.038 |
| 5 | 86 | 0.043 | 0.023 | 0.038-0.048 |
| 6 | 87 | 0.056 | 0.034 | 0.048-0.065 |
| 7 | 87 | 0.076 | 0.069 | 0.065-0.087 |
| 8 | 86 | 0.106 | 0.070 | 0.087-0.127 |
| 9 | 87 | 0.156 | 0.207 | 0.129-0.189 |
| 10 | 86 | 0.286 | 0.221 | 0.189-0.633 |

Where the model and the market disagree most (snapshots 2019-2024, per season the top 10 of each; who was right = the label):

| group | players | label = 1 | rate |
|---|---|---|---|
| in both | 43 | 10 | 0.23 |
| in the ECR's top 10, not `logit`'s | 17 | 1 | 0.06 |
| in `logit`'s top 10, not the ECR's | 17 | 0 | 0.00 |

Largest rank gaps (2 per season and side):

| label season | player | pos | group | rank by model | rank by ECR score | PPG S | PPG S+1 | games S+1 | label |
|---|---|---|---|---|---|---|---|---|---|
| 2020 | Phillip Lindsay | RB | ecr_only | 21 | 3 | 12.4 | 6.0 | 11 | 0 |
| 2020 | Darrell Henderson | RB | ecr_only | 24 | 6 | 2.5 | 8.7 | 15 | 0 |
| 2020 | Derrius Guice | RB | model_only | 3 | 24 | 11.5 | - | 0 | 0 |
| 2020 | Royce Freeman | RB | model_only | 4 | 20 | 8.9 | 3.4 | 11 | 0 |
| 2021 | Ty Johnson | RB | ecr_only | 20 | 10 | 5.8 | 7.8 | 15 | 0 |
| 2021 | AJ Dillon | RB | ecr_only | 12 | 4 | 4.5 | 10.9 | 17 | 0 |
| 2021 | Cam Akers | RB | model_only | 1 | 54 | 9.3 | 4.3 | 1 | 0 |
| 2021 | J.K. Dobbins | RB | model_only | 2 | 51 | 11.2 | - | 0 | 0 |
| 2022 | Chris Evans | RB | ecr_only | 16 | 10 | 5.5 | 1.3 | 10 | 0 |
| 2022 | Khalil Herbert | RB | ecr_only | 13 | 8 | 4.9 | 9.1 | 13 | 0 |
| 2022 | Zack Moss | RB | model_only | 6 | 12 | 8.1 | 5.0 | 13 | 0 |
| 2022 | Trey Sermon | RB | model_only | 10 | 11 | 4.7 | 1.9 | 1 | 0 |
| 2023 | Jaylen Warren | RB | ecr_only | 17 | 8 | 5.8 | 11.6 | 17 | 0 |
| 2023 | Elijah Mitchell | RB | ecr_only | 16 | 9 | 8.7 | 4.3 | 11 | 0 |
| 2023 | Bam Knight | RB | model_only | 8 | 24 | 8.4 | 1.6 | 2 | 0 |
| 2023 | Michael Carter | RB | model_only | 7 | 15 | 7.9 | 3.9 | 15 | 0 |
| 2024 | Jaleel McLaughlin | RB | ecr_only | 19 | 7 | 6.2 | 6.1 | 16 | 0 |
| 2024 | Chase Brown | RB | ecr_only | 13 | 4 | 4.5 | 15.9 | 16 | 1 |
| 2024 | Dameon Pierce | RB | model_only | 8 | 12 | 5.9 | 4.3 | 10 | 0 |
| 2024 | Kendre Miller | RB | model_only | 9 | 11 | 6.2 | 4.9 | 6 | 0 |
| 2025 | Tyjae Spears | RB | ecr_only | 12 | 6 | 9.5 | 8.6 | 13 | 0 |
| 2025 | Roschon Johnson | RB | ecr_only | 11 | 8 | 6.0 | 0.3 | 6 | 0 |
| 2025 | Audric Estimé | RB | model_only | 9 | 20 | 3.7 | 9.6 | 5 | 0 |
| 2025 | Jaylen Wright | RB | model_only | 7 | 11 | 2.1 | 5.4 | 9 | 0 |

## Read before trusting

- Intervals resample whole seasons (18 in `all`, 6 in `ecr_era`): the ECR-era intervals are wide. Precision@k is the mean over seasons of the share of the season's top k with the label.
- Probabilities are each model's own (no isotonic step); the PPG-rank baseline and the ECR are rankings first.
- Inputs missing in early seasons (NGS before 2016, RYOE before 2018, snap counts before 2013, own xFP before 2009) are imputed inside each fit with a missing indicator.
- Head-coach departures: the owner's cited research file (accepted in bulk, 2026-10-01); a blank date counts as known at the snapshot for fired / mutual / interim types only.
