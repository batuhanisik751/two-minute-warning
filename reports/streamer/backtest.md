# K and D/ST streamer: walk-forward backtest

Created 2026-09-30 00:30 UTC in 32 s. Command: `uv run twm streamer backtest`; dataset `data/streamer/dataset.parquet`; test seasons 2013-2025; models trained on pool rows. docs/streamer.md explains it in plain words.

- **What is graded**: at every regular-season Tuesday as-of of a test season, each method ranks the pool of its position (kickers or D/STs probably on waivers, reports/streamer/pool_labels.md). A pick is a hit when he finishes in the top 12 of the position the next week (`y_start`); byes and the last week have no label and are left out. precision@k = hits among the top k of a list (a list shorter than k divides by its length); `#1 pick started` = precision@1. Pooled = the mean over lists.
- **Walk-forward**: the model graded on season S learns only from seasons before S AND only from labels public by S's first Tuesday as-of (the training cutoff; any later training row is refused), is tuned on the last training season (pooled precision@5) and calibrated there (isotonic); one model per position; no refit during the season. 2013 is a thin fold: trained on one season, no tuning, cross-fitted calibration.
- **Intervals**: 95% bootstrap intervals that resample whole seasons (2,000 draws); differences are paired (the same seasons drawn for both methods).
- **Model choice**: LightGBM is kept only if it beats the logistic regression on pooled precision@5 over all test seasons; otherwise the logistic regression is kept.
- No betting lines, no weather, no injury news: the as-of is Tuesday, before any of that exists for the next game (PROJECT_SPEC 6.1-6.4).

## Verdict

- **K**: the kept model, logistic regression (precision@5 37.9%), is ahead of every baseline, but not clearly (an interval includes zero). Model minus baseline, precision@5 points: last game's points +1.2 (-0.4 to +2.9); points per game +1.2 (-0.7 to +3.2); next opponent +10.0 (+5.7 to +14.5).
- **DST**: the kept model, logistic regression (precision@5 39.1%), does NOT beat every baseline. Model minus baseline, precision@5 points: last game's points +1.7 (+0.2 to +3.4); points per game +1.5 (-0.2 to +3.1); next opponent -1.0 (-2.4 to +0.3).

## Results

### K

210 weekly lists (2013-2025), 2318 pool rows, 571 of them started (base rate 24.6%); average list 11.0 rows, 4 lists shorter than 5.

| method | precision@3 | precision@5 | #1 pick started |
|---|---|---|---|
| logistic regression | 40.5% (37.5% to 43.1%) | 37.9% (36.0% to 39.6%) | 42.9% (38.2% to 47.8%) |
| LightGBM | 37.1% (33.3% to 40.9%) | 36.4% (33.6% to 39.1%) | 38.6% (34.4% to 42.9%) |
| baseline: last game's points | 39.8% (37.1% to 42.5%) | 36.6% (34.0% to 39.2%) | 41.0% (36.0% to 45.8%) |
| baseline: points per game | 37.1% (33.6% to 41.0%) | 36.6% (34.4% to 38.8%) | 35.2% (31.0% to 40.2%) |
| baseline: next opponent | 30.6% (26.3% to 35.0%) | 27.9% (23.8% to 31.8%) | 37.6% (30.4% to 45.0%) |

Pool kickers who did not kick in their team's latest game (practice squad, camp, free agents) started 2.3% of the time, team kickers 36.0%. Share of such kickers among each method's top 5: last game's points 5%, points per game 5%, next opponent 25%, logistic regression 3%, LightGBM 4%.

#### K: model minus baseline (percentage points, 95% interval, share of resamples above zero)

| model | baseline | precision@3 | precision@5 | #1 pick started |
|---|---|---|---|---|
| logistic regression | last game's points | +0.6 (-1.6 to +2.8), 68% | +1.2 (-0.4 to +2.9), 92% | +1.9 (-4.3 to +7.2), 73% |
| logistic regression | points per game | +3.3 (-1.2 to +7.8), 91% | +1.2 (-0.7 to +3.2), 90% | +7.6 (+2.3 to +12.9), 100% |
| logistic regression | next opponent | +9.8 (+4.2 to +15.7), 100% | +10.0 (+5.7 to +14.5), 100% | +5.2 (-3.9 to +15.6), 85% |
| LightGBM | last game's points | -2.7 (-6.6 to +1.6), 9% | -0.3 (-3.2 to +2.5), 42% | -2.4 (-8.1 to +3.3), 19% |
| LightGBM | points per game | -0.0 (-3.5 to +3.5), 48% | -0.3 (-2.9 to +2.2), 40% | +3.3 (-1.4 to +8.1), 90% |
| LightGBM | next opponent | +6.5 (+0.2 to +12.7), 98% | +8.5 (+3.3 to +13.5), 100% | +1.0 (-9.0 to +10.9), 58% |

### DST

213 weekly lists (2013-2025), 1424 pool rows, 532 of them started (base rate 37.4%); average list 6.7 rows, 14 lists shorter than 5.

| method | precision@3 | precision@5 | #1 pick started |
|---|---|---|---|
| logistic regression | 42.6% (39.6% to 46.1%) | 39.1% (36.1% to 42.2%) | 46.5% (39.4% to 53.0%) |
| LightGBM | 38.1% (34.9% to 41.2%) | 38.5% (34.7% to 42.2%) | 40.4% (33.2% to 47.2%) |
| baseline: last game's points | 37.3% (33.2% to 41.7%) | 37.4% (33.9% to 41.2%) | 39.0% (31.3% to 47.7%) |
| baseline: points per game | 38.1% (34.7% to 41.5%) | 37.6% (34.7% to 40.7%) | 40.4% (33.8% to 48.1%) |
| baseline: next opponent | 43.3% (40.3% to 46.0%) | 40.2% (36.7% to 43.1%) | 49.8% (44.8% to 55.4%) |

#### DST: model minus baseline (percentage points, 95% interval, share of resamples above zero)

| model | baseline | precision@3 | precision@5 | #1 pick started |
|---|---|---|---|---|
| logistic regression | last game's points | +5.3 (+1.6 to +9.6), 100% | +1.7 (+0.2 to +3.4), 99% | +7.5 (+0.9 to +14.1), 99% |
| logistic regression | points per game | +4.5 (+1.1 to +8.1), 100% | +1.5 (-0.2 to +3.1), 97% | +6.1 (-1.4 to +13.9), 93% |
| logistic regression | next opponent | -0.6 (-2.4 to +1.1), 26% | -1.0 (-2.4 to +0.3), 6% | -3.3 (-9.7 to +3.3), 17% |
| LightGBM | last game's points | +0.8 (-2.8 to +4.7), 64% | +1.0 (-0.8 to +3.1), 86% | +1.4 (-7.1 to +10.3), 60% |
| LightGBM | points per game | -0.0 (-3.6 to +4.2), 46% | +0.8 (-1.2 to +3.1), 78% | +0.0 (-9.5 to +8.4), 48% |
| LightGBM | next opponent | -5.2 (-7.2 to -2.8), 0% | -1.7 (-3.2 to -0.3), 1% | -9.4 (-17.2 to -1.4), 1% |

## By season

#### K: precision@5 by test season

| season | lists | base rate | logistic regression | LightGBM | last game's points | points per game | next opponent |
|---|---|---|---|---|---|---|---|
| 2013 | 15 | 33% | 39% | 33% | 41% | 37% | 37% |
| 2014 | 15 | 35% | 43% | 44% | 41% | 43% | 33% |
| 2015 | 15 | 28% | 36% | 27% | 39% | 33% | 37% |
| 2016 | 16 | 29% | 34% | 35% | 33% | 30% | 32% |
| 2017 | 16 | 26% | 33% | 31% | 31% | 31% | 24% |
| 2018 | 16 | 30% | 38% | 43% | 39% | 38% | 34% |
| 2019 | 16 | 32% | 37% | 33% | 33% | 42% | 32% |
| 2020 | 16 | 14% | 40% | 41% | 43% | 36% | 15% |
| 2021 | 17 | 16% | 33% | 32% | 27% | 33% | 19% |
| 2022 | 17 | 23% | 42% | 35% | 41% | 34% | 20% |
| 2023 | 17 | 25% | 38% | 36% | 33% | 41% | 22% |
| 2024 | 17 | 25% | 42% | 45% | 36% | 38% | 25% |
| 2025 | 17 | 27% | 39% | 38% | 41% | 41% | 35% |

#### DST: precision@5 by test season

| season | lists | base rate | logistic regression | LightGBM | last game's points | points per game | next opponent |
|---|---|---|---|---|---|---|---|
| 2013 | 16 | 48% | 49% | 50% | 49% | 48% | 48% |
| 2014 | 16 | 32% | 31% | 30% | 32% | 32% | 37% |
| 2015 | 16 | 41% | 43% | 42% | 42% | 42% | 42% |
| 2016 | 16 | 37% | 36% | 34% | 38% | 34% | 39% |
| 2017 | 16 | 45% | 49% | 48% | 48% | 44% | 48% |
| 2018 | 16 | 39% | 40% | 41% | 43% | 41% | 44% |
| 2019 | 16 | 36% | 38% | 37% | 34% | 36% | 41% |
| 2020 | 16 | 33% | 38% | 38% | 29% | 31% | 39% |
| 2021 | 17 | 30% | 31% | 28% | 27% | 31% | 27% |
| 2022 | 17 | 40% | 44% | 44% | 41% | 42% | 44% |
| 2023 | 17 | 40% | 38% | 36% | 39% | 42% | 40% |
| 2024 | 17 | 32% | 32% | 28% | 29% | 32% | 32% |
| 2025 | 17 | 38% | 41% | 44% | 37% | 35% | 43% |

## Probabilities

#### K: probabilities

Brier score (lower is better): constant forecast (the training seasons' start rate) 0.1878; logistic regression 0.1699; LightGBM 0.1707.

Calibration of the kept model (logistic regression), fixed 10-point bins:

| predicted | rows | mean predicted | observed start rate |
|---|---|---|---|
| 0%-10% | 810 | 0.9% | 4.8% |
| 10%-20% | 136 | 16.0% | 24.3% |
| 20%-30% | 405 | 25.6% | 34.1% |
| 30%-40% | 444 | 35.6% | 37.8% |
| 40%-50% | 356 | 41.8% | 35.4% |
| 50%-60% | 108 | 52.5% | 38.9% |
| 60%-70% | 41 | 63.4% | 41.5% |
| 70%-80% | 2 | 77.4% | 100.0% |
| 80%-90% | 2 | 86.3% | 50.0% |
| 90%-100% | 14 | 100.0% | 35.7% |

#### DST: probabilities

Brier score (lower is better): constant forecast (the training seasons' start rate) 0.2350; logistic regression 0.2498; LightGBM 0.2675.

Calibration of the kept model (logistic regression), fixed 10-point bins:

| predicted | rows | mean predicted | observed start rate |
|---|---|---|---|
| 0%-10% | 39 | 1.1% | 30.8% |
| 10%-20% | 40 | 16.1% | 20.0% |
| 20%-30% | 293 | 25.7% | 30.7% |
| 30%-40% | 410 | 34.3% | 44.1% |
| 40%-50% | 447 | 43.3% | 37.4% |
| 50%-60% | 118 | 51.9% | 39.0% |
| 60%-70% | 25 | 65.7% | 48.0% |
| 70%-80% | 30 | 76.3% | 30.0% |
| 80%-90% | 15 | 83.9% | 26.7% |
| 90%-100% | 7 | 96.6% | 42.9% |

## Sensitivity: models trained on every universe row

The same walk-forward, but each model also learns from the kickers and D/STs that were NOT in the pool (rostered in most leagues): 14,686 final rows instead of 3,974 (all seasons, both positions). Graded on the same pool lists; in brackets the change against training on pool rows only (percentage points, 95% season-block interval).

| position | model | precision@3 | precision@5 | #1 pick started |
|---|---|---|---|---|
| K | logistic regression | 37.8% (-2.7 (-6.8 to +1.4)) | 36.9% (-1.0 (-2.8 to +0.7)) | 38.6% (-4.3 (-12.2 to +2.9)) |
| K | LightGBM | 39.2% (+2.1 (-1.9 to +5.8)) | 38.4% (+2.0 (-0.2 to +4.1)) | 41.4% (+2.9 (-1.4 to +7.0)) |
| DST | logistic regression | 40.8% (-1.9 (-4.8 to +0.9)) | 39.2% (+0.1 (-1.4 to +1.6)) | 47.9% (+1.4 (-5.2 to +8.0)) |
| DST | LightGBM | 42.3% (+4.2 (+1.5 to +6.9)) | 39.4% (+0.9 (-0.9 to +2.9)) | 52.1% (+11.7 (+5.6 to +19.7)) |

## Folds

#### K: logistic regression folds (importance: log-odds contribution (std))

| test | trained on | validation | training rows (starts) | chosen settings | top 3 features |
|---|---|---|---|---|---|
| 2013 | 2012 | thin | 75 (30) | C=0.1, l1_ratio=0 | kdst_preseason_rank 14%, k_fg_att_40_plus_per_game 11%, next_venue_dome 8% |
| 2014 | 2012-2013 | 2013 | 173 (62) | C=0.01, l1_ratio=0 | team_rz_trips_per_game 9%, kdst_points_last 8%, k_pat_att_per_game 8% |
| 2015 | 2012-2014 | 2014 | 314 (111) | C=0.1, l1_ratio=1 | is_team_kicker 44%, kdst_preseason_rank 18%, team_rz_stall_rate 16% (dominant) |
| 2016 | 2012-2015 | 2015 | 420 (141) | C=0.01, l1_ratio=0 | is_team_kicker 10%, kdst_points_last 9%, kdst_preseason_rank 7% |
| 2017 | 2012-2016 | 2016 | 549 (178) | C=0.1, l1_ratio=0 | is_team_kicker 14%, team_rz_stall_rate 8%, kdst_points_last 7% |
| 2018 | 2012-2017 | 2017 | 733 (226) | C=0.01, l1_ratio=1 | is_team_kicker 0%, k_fg_att_40_plus_per_game 0%, k_fg_att_per_game 0% (constant model: ties, ranked by id) |
| 2019 | 2012-2018 | 2018 | 891 (273) | C=0.01, l1_ratio=0 | kdst_preseason_rank 10%, is_team_kicker 8%, kdst_points_last 7% |
| 2020 | 2012-2019 | 2019 | 1023 (315) | C=1, l1_ratio=0 | is_team_kicker 13%, next_is_home 9%, team_rz_stall_rate 8% |
| 2021 | 2012-2020 | 2020 | 1327 (358) | C=0.1, l1_ratio=0 | is_team_kicker 15%, kdst_points_last 6%, next_opp_sacks_allowed_per_game 6% |
| 2022 | 2012-2021 | 2021 | 1610 (404) | C=0.1, l1_ratio=1 | is_team_kicker 59%, kdst_preseason_rank 7%, next_opp_sacks_allowed_per_game 6% (dominant) |
| 2023 | 2012-2022 | 2022 | 1798 (447) | C=0.01, l1_ratio=0 | is_team_kicker 15%, kdst_points_last 8%, kdst_preseason_rank 6% |
| 2024 | 2012-2023 | 2023 | 1967 (489) | C=0.01, l1_ratio=0 | is_team_kicker 14%, kdst_points_last 7%, kdst_preseason_rank 6% |
| 2025 | 2012-2024 | 2024 | 2171 (540) | C=1, l1_ratio=0 | is_team_kicker 31%, weekly_ecr_listed 7%, kdst_preseason_rank 5% |

#### K: LightGBM folds (importance: gain)

| test | trained on | validation | training rows (starts) | chosen settings | top 3 features |
|---|---|---|---|---|---|
| 2013 | 2012 | thin | 75 (30) | num_leaves=15, min_child_samples=100, colsample_bytree=0.8, n_estimators=300 | is_team_kicker 0%, k_fg_att_40_plus_per_game 0%, k_fg_att_per_game 0% (constant model: ties, ranked by id) |
| 2014 | 2012-2013 | 2013 | 173 (62) | num_leaves=15, min_child_samples=50, colsample_bytree=0.7, n_estimators=1 | k_pat_att_per_game 58%, kdst_preseason_rank 42%, is_team_kicker 0% (dominant) |
| 2015 | 2012-2014 | 2014 | 314 (111) | num_leaves=15, min_child_samples=200, colsample_bytree=0.7, n_estimators=1 | is_team_kicker 0%, k_fg_att_40_plus_per_game 0%, k_fg_att_per_game 0% (constant model: ties, ranked by id) |
| 2016 | 2012-2015 | 2015 | 420 (141) | num_leaves=15, min_child_samples=50, colsample_bytree=1, n_estimators=104 | kdst_points_last 11%, k_pat_att_per_game 10%, next_opp_giveaways_per_game 6% |
| 2017 | 2012-2016 | 2016 | 549 (178) | num_leaves=15, min_child_samples=50, colsample_bytree=1, n_estimators=16 | kdst_points_last 20%, is_team_kicker 19%, k_pat_att_per_game 13% |
| 2018 | 2012-2017 | 2017 | 733 (226) | num_leaves=15, min_child_samples=200, colsample_bytree=0.7, n_estimators=68 | k_pat_att_per_game 26%, kdst_points_last 17%, kdst_preseason_rank 14% |
| 2019 | 2012-2018 | 2018 | 891 (273) | num_leaves=15, min_child_samples=200, colsample_bytree=1, n_estimators=76 | kdst_points_last 21%, kdst_preseason_rank 16%, k_pat_att_per_game 15% |
| 2020 | 2012-2019 | 2019 | 1023 (315) | num_leaves=15, min_child_samples=50, colsample_bytree=0.7, n_estimators=31 | is_team_kicker 21%, k_pat_att_per_game 10%, next_opp_sacks_allowed_per_game 7% |
| 2021 | 2012-2020 | 2020 | 1327 (358) | num_leaves=15, min_child_samples=50, colsample_bytree=0.7, n_estimators=102 | is_team_kicker 20%, k_pat_att_per_game 6%, next_opp_sacks_allowed_per_game 6% |
| 2022 | 2012-2021 | 2021 | 1610 (404) | num_leaves=15, min_child_samples=200, colsample_bytree=1, n_estimators=96 | is_team_kicker 53%, kdst_points_last 7%, next_opp_points_allowed_per_game 5% (dominant) |
| 2023 | 2012-2022 | 2022 | 1798 (447) | num_leaves=15, min_child_samples=200, colsample_bytree=0.7, n_estimators=40 | is_team_kicker 59%, kdst_points_per_game 9%, kdst_points_last 7% (dominant) |
| 2024 | 2012-2023 | 2023 | 1967 (489) | num_leaves=15, min_child_samples=50, colsample_bytree=1, n_estimators=94 | is_team_kicker 36%, next_opp_points_allowed_per_game 6%, next_opp_points_per_game 5% |
| 2025 | 2012-2024 | 2024 | 2171 (540) | num_leaves=31, min_child_samples=50, colsample_bytree=1, n_estimators=105 | is_team_kicker 29%, next_opp_points_allowed_per_game 6%, next_opp_sacks_allowed_per_game 5% |

#### DST: logistic regression folds (importance: log-odds contribution (std))

| test | trained on | validation | training rows (starts) | chosen settings | top 3 features |
|---|---|---|---|---|---|
| 2013 | 2012 | thin | 116 (39) | C=0.1, l1_ratio=0 | next_opp_sacks_allowed_per_game 10%, next_opp_rz_trips_allowed_per_game 7%, dst_points_allowed_per_game 7% |
| 2014 | 2012-2013 | 2013 | 200 (79) | C=0.01, l1_ratio=0 | next_opp_sacks_allowed_per_game 13%, dst_sacks_per_game 11%, next_opp_points_allowed_per_game 9% |
| 2015 | 2012-2014 | 2014 | 311 (114) | C=0.1, l1_ratio=1 | next_opp_sacks_allowed_per_game 56%, next_opp_points_allowed_per_game 31%, kdst_preseason_rank 12% (dominant) |
| 2016 | 2012-2015 | 2015 | 410 (155) | C=0.1, l1_ratio=0 | next_opp_points_allowed_per_game 12%, next_opp_sacks_allowed_per_game 10%, team_points_per_game 9% |
| 2017 | 2012-2016 | 2016 | 519 (195) | C=0.01, l1_ratio=0 | next_opp_sacks_allowed_per_game 13%, next_opp_points_per_game 10%, next_opp_points_allowed_per_game 9% |
| 2018 | 2012-2017 | 2017 | 617 (239) | C=1, l1_ratio=0 | next_opp_games_to_date 16%, team_points_per_game 9%, dst_points_allowed_per_game 8% |
| 2019 | 2012-2018 | 2018 | 709 (275) | C=0.1, l1_ratio=1 | next_opp_points_per_game 36%, next_opp_sacks_allowed_per_game 19%, team_points_per_game 15% |
| 2020 | 2012-2019 | 2019 | 820 (315) | C=0.1, l1_ratio=1 | next_opp_points_per_game 29%, next_opp_sacks_allowed_per_game 17%, team_points_per_game 15% |
| 2021 | 2012-2020 | 2020 | 958 (360) | C=1, l1_ratio=0 | next_opp_games_to_date 10%, next_opp_points_per_game 8%, next_opp_points_allowed_per_game 8% |
| 2022 | 2012-2021 | 2021 | 1073 (395) | C=0.1, l1_ratio=1 | next_opp_sacks_allowed_per_game 16%, next_opp_points_per_game 15%, weekly_ecr_listed 14% |
| 2023 | 2012-2022 | 2022 | 1175 (436) | C=1, l1_ratio=0 | next_opp_games_to_date 12%, team_rz_trips_per_game 6%, next_opp_sacks_allowed_per_game 6% |
| 2024 | 2012-2023 | 2023 | 1317 (493) | C=0.1, l1_ratio=0 | kdst_points_per_game 9%, next_opp_points_per_game 9%, next_opp_points_allowed_per_game 8% |
| 2025 | 2012-2024 | 2024 | 1428 (528) | C=0.01, l1_ratio=0 | next_opp_sacks_allowed_per_game 13%, next_opp_points_per_game 12%, weekly_ecr_listed 8% |

#### DST: LightGBM folds (importance: gain)

| test | trained on | validation | training rows (starts) | chosen settings | top 3 features |
|---|---|---|---|---|---|
| 2013 | 2012 | thin | 116 (39) | num_leaves=15, min_child_samples=100, colsample_bytree=0.8, n_estimators=300 | dst_points_allowed_per_game 0%, dst_sacks_per_game 0%, dst_takeaways_per_game 0% (constant model: ties, ranked by id) |
| 2014 | 2012-2013 | 2013 | 200 (79) | num_leaves=15, min_child_samples=50, colsample_bytree=0.7, n_estimators=29 | dst_points_allowed_per_game 36%, next_opp_rz_trips_allowed_per_game 26%, dst_sacks_per_game 9% |
| 2015 | 2012-2014 | 2014 | 311 (114) | num_leaves=15, min_child_samples=200, colsample_bytree=0.7, n_estimators=1 | dst_points_allowed_per_game 0%, dst_sacks_per_game 0%, dst_takeaways_per_game 0% (constant model: ties, ranked by id) |
| 2016 | 2012-2015 | 2015 | 410 (155) | num_leaves=15, min_child_samples=50, colsample_bytree=1, n_estimators=14 | next_opp_sacks_allowed_per_game 17%, team_rz_stall_rate 17%, next_opp_points_allowed_per_game 16% |
| 2017 | 2012-2016 | 2016 | 519 (195) | num_leaves=15, min_child_samples=50, colsample_bytree=0.7, n_estimators=18 | next_opp_sacks_allowed_per_game 14%, team_rz_stall_rate 9%, next_opp_points_allowed_per_game 9% |
| 2018 | 2012-2017 | 2017 | 617 (239) | num_leaves=15, min_child_samples=200, colsample_bytree=1, n_estimators=394 | next_opp_points_per_game 13%, team_points_per_game 13%, dst_points_allowed_per_game 11% |
| 2019 | 2012-2018 | 2018 | 709 (275) | num_leaves=15, min_child_samples=50, colsample_bytree=0.7, n_estimators=28 | next_opp_sacks_allowed_per_game 12%, team_points_per_game 11%, next_opp_points_per_game 10% |
| 2020 | 2012-2019 | 2019 | 820 (315) | num_leaves=15, min_child_samples=50, colsample_bytree=1, n_estimators=44 | team_rz_stall_rate 11%, next_opp_sacks_allowed_per_game 9%, team_points_per_game 9% |
| 2021 | 2012-2020 | 2020 | 958 (360) | num_leaves=15, min_child_samples=200, colsample_bytree=1, n_estimators=32 | next_opp_sacks_allowed_per_game 26%, next_opp_points_per_game 19%, next_opp_points_allowed_per_game 18% |
| 2022 | 2012-2021 | 2021 | 1073 (395) | num_leaves=31, min_child_samples=50, colsample_bytree=1, n_estimators=19 | next_opp_sacks_allowed_per_game 13%, next_opp_points_allowed_per_game 9%, kdst_points_per_game 9% |
| 2023 | 2012-2022 | 2022 | 1175 (436) | num_leaves=15, min_child_samples=200, colsample_bytree=1, n_estimators=45 | next_opp_sacks_allowed_per_game 23%, next_opp_points_per_game 14%, kdst_points_per_game 10% |
| 2024 | 2012-2023 | 2023 | 1317 (493) | num_leaves=31, min_child_samples=50, colsample_bytree=1, n_estimators=19 | next_opp_points_per_game 12%, kdst_points_per_game 10%, next_opp_points_allowed_per_game 10% |
| 2025 | 2012-2024 | 2024 | 1428 (528) | num_leaves=31, min_child_samples=50, colsample_bytree=0.7, n_estimators=1 | next_opp_sacks_allowed_per_game 28%, next_opp_games_to_date 10%, next_opp_points_allowed_per_game 10% |
