# K and D/ST streamer: walk-forward backtest

Created 2026-09-30 00:49 UTC in 32 s. Command: `uv run twm streamer backtest`; dataset `data/streamer/dataset.parquet`; test seasons 2013-2025; models trained on pool rows. docs/streamer.md explains it in plain words.

- **What is graded**: at every regular-season Tuesday as-of of a test season, each method ranks the pool of its position (kickers or D/STs probably on waivers, reports/streamer/pool_labels.md). A pick is a hit when he finishes in the top 12 of the position the next week (`y_start`); byes and the last week have no label and are left out. precision@k = hits among the top k of a list (a list shorter than k divides by its length); `#1 pick started` = precision@1. Pooled = the mean over lists.
- **Walk-forward**: the model graded on season S learns only from seasons before S AND only from labels public by S's first Tuesday as-of (the training cutoff; any later training row is refused), is tuned on the last training season (pooled precision@5) and calibrated there (isotonic); one model per position; no refit during the season. 2013 is a thin fold: trained on one season, no tuning, cross-fitted calibration.
- **Intervals**: 95% bootstrap intervals that resample whole seasons (2,000 draws); differences are paired (the same seasons drawn for both methods).
- **Model choice**: LightGBM is kept only if it beats the logistic regression on pooled precision@5 over all test seasons; otherwise the logistic regression is kept.
- No betting lines, no weather, no injury news: the as-of is Tuesday, before any of that exists for the next game (PROJECT_SPEC 6.1-6.4).

## Verdict

- **K**: the kept model, logistic regression (precision@5 38.4%), is ahead of every baseline, but not clearly (an interval includes zero). Model minus baseline, precision@5 points: last game's points +1.4 (-0.2 to +3.0); points per game +1.6 (-0.5 to +3.7); next opponent +10.4 (+6.2 to +14.6).
- **DST**: the kept model, logistic regression (precision@5 39.1%), does NOT beat every baseline. Model minus baseline, precision@5 points: last game's points +1.2 (-0.5 to +2.9); points per game +1.8 (+0.2 to +3.4); next opponent -1.1 (-2.5 to +0.2).

## Results

### K

210 weekly lists (2013-2025), 2318 pool rows, 571 of them started (base rate 24.6%); average list 11.0 rows, 4 lists shorter than 5.

| method | precision@3 | precision@5 | #1 pick started |
|---|---|---|---|
| logistic regression | 40.6% (38.1% to 43.1%) | 38.4% (36.3% to 40.3%) | 42.4% (37.1% to 47.6%) |
| LightGBM | 38.6% (35.1% to 41.8%) | 37.0% (34.7% to 39.4%) | 39.5% (34.9% to 44.3%) |
| baseline: last game's points | 39.4% (36.7% to 42.0%) | 36.9% (34.4% to 39.3%) | 40.0% (35.1% to 45.0%) |
| baseline: points per game | 38.1% (34.6% to 41.9%) | 36.7% (34.4% to 39.0%) | 34.8% (31.0% to 38.9%) |
| baseline: next opponent | 30.8% (26.3% to 35.3%) | 28.0% (23.9% to 32.0%) | 37.6% (30.6% to 44.9%) |

Pool kickers who did not kick in their team's latest game (practice squad, camp, free agents) started 2.3% of the time, team kickers 36.0%. Share of such kickers among each method's top 5: last game's points 5%, points per game 5%, next opponent 25%, logistic regression 2%, LightGBM 3%.

#### K: model minus baseline (percentage points, 95% interval, share of resamples above zero)

| model | baseline | precision@3 | precision@5 | #1 pick started |
|---|---|---|---|---|
| logistic regression | last game's points | +1.3 (-1.0 to +3.3), 85% | +1.4 (-0.2 to +3.0), 95% | +2.4 (-3.8 to +7.6), 77% |
| logistic regression | points per game | +2.5 (-1.9 to +6.9), 86% | +1.6 (-0.5 to +3.7), 93% | +7.6 (+2.0 to +13.3), 99% |
| logistic regression | next opponent | +9.8 (+4.0 to +16.0), 100% | +10.4 (+6.2 to +14.6), 100% | +4.8 (-3.8 to +14.6), 84% |
| LightGBM | last game's points | -0.8 (-3.8 to +2.3), 28% | +0.1 (-2.0 to +2.1), 51% | -0.5 (-6.6 to +5.4), 42% |
| LightGBM | points per game | +0.5 (-3.6 to +4.7), 55% | +0.3 (-2.4 to +2.7), 58% | +4.8 (+0.5 to +9.0), 99% |
| LightGBM | next opponent | +7.8 (+3.3 to +12.4), 100% | +9.0 (+4.9 to +13.5), 100% | +1.9 (-8.5 to +11.9), 65% |

### DST

213 weekly lists (2013-2025), 1424 pool rows, 532 of them started (base rate 37.4%); average list 6.7 rows, 14 lists shorter than 5.

| method | precision@3 | precision@5 | #1 pick started |
|---|---|---|---|
| logistic regression | 42.6% (39.6% to 46.1%) | 39.1% (36.1% to 42.2%) | 46.5% (39.4% to 53.0%) |
| LightGBM | 39.2% (35.8% to 42.5%) | 38.5% (35.1% to 41.7%) | 40.4% (32.7% to 46.7%) |
| baseline: last game's points | 37.3% (33.4% to 41.4%) | 37.9% (34.6% to 41.3%) | 39.0% (31.3% to 47.4%) |
| baseline: points per game | 38.7% (35.4% to 42.3%) | 37.3% (34.3% to 40.6%) | 38.0% (32.7% to 44.4%) |
| baseline: next opponent | 43.4% (40.5% to 46.1%) | 40.3% (36.8% to 43.2%) | 49.8% (44.8% to 55.4%) |

#### DST: model minus baseline (percentage points, 95% interval, share of resamples above zero)

| model | baseline | precision@3 | precision@5 | #1 pick started |
|---|---|---|---|---|
| logistic regression | last game's points | +5.3 (+1.7 to +9.3), 100% | +1.2 (-0.5 to +2.9), 92% | +7.5 (+1.4 to +13.3), 99% |
| logistic regression | points per game | +3.9 (+0.2 to +7.8), 98% | +1.8 (+0.2 to +3.4), 98% | +8.5 (+1.4 to +15.6), 99% |
| logistic regression | next opponent | -0.8 (-2.6 to +1.0), 22% | -1.1 (-2.5 to +0.2), 5% | -3.3 (-9.7 to +3.3), 17% |
| LightGBM | last game's points | +1.9 (-1.6 to +5.6), 84% | +0.6 (-1.4 to +2.5), 71% | +1.4 (-5.7 to +7.9), 64% |
| LightGBM | points per game | +0.5 (-3.5 to +4.8), 56% | +1.1 (-0.6 to +2.8), 91% | +2.3 (-5.7 to +9.1), 71% |
| LightGBM | next opponent | -4.2 (-6.6 to -1.9), 0% | -1.8 (-3.6 to -0.2), 1% | -9.4 (-17.4 to -1.4), 1% |

## By season

#### K: precision@5 by test season

| season | lists | base rate | logistic regression | LightGBM | last game's points | points per game | next opponent |
|---|---|---|---|---|---|---|---|
| 2013 | 15 | 33% | 39% | 41% | 41% | 37% | 37% |
| 2014 | 15 | 35% | 43% | 40% | 41% | 43% | 33% |
| 2015 | 15 | 28% | 36% | 33% | 40% | 33% | 37% |
| 2016 | 16 | 29% | 34% | 35% | 33% | 30% | 32% |
| 2017 | 16 | 26% | 33% | 30% | 31% | 31% | 24% |
| 2018 | 16 | 30% | 44% | 43% | 39% | 38% | 34% |
| 2019 | 16 | 32% | 37% | 33% | 34% | 42% | 33% |
| 2020 | 16 | 14% | 40% | 41% | 43% | 36% | 16% |
| 2021 | 17 | 16% | 33% | 32% | 28% | 33% | 19% |
| 2022 | 17 | 23% | 42% | 35% | 40% | 34% | 20% |
| 2023 | 17 | 25% | 38% | 36% | 34% | 41% | 22% |
| 2024 | 17 | 25% | 42% | 45% | 36% | 38% | 24% |
| 2025 | 17 | 27% | 39% | 38% | 41% | 42% | 35% |

#### DST: precision@5 by test season

| season | lists | base rate | logistic regression | LightGBM | last game's points | points per game | next opponent |
|---|---|---|---|---|---|---|---|
| 2013 | 16 | 48% | 49% | 49% | 49% | 48% | 48% |
| 2014 | 16 | 32% | 31% | 30% | 34% | 32% | 37% |
| 2015 | 16 | 41% | 43% | 44% | 43% | 42% | 42% |
| 2016 | 16 | 37% | 36% | 34% | 39% | 35% | 40% |
| 2017 | 16 | 45% | 49% | 48% | 45% | 44% | 48% |
| 2018 | 16 | 39% | 40% | 41% | 40% | 43% | 44% |
| 2019 | 16 | 36% | 38% | 37% | 36% | 34% | 41% |
| 2020 | 16 | 33% | 38% | 38% | 30% | 30% | 39% |
| 2021 | 17 | 30% | 31% | 28% | 28% | 29% | 27% |
| 2022 | 17 | 40% | 44% | 44% | 41% | 41% | 44% |
| 2023 | 17 | 40% | 38% | 36% | 42% | 41% | 40% |
| 2024 | 17 | 32% | 32% | 33% | 29% | 31% | 32% |
| 2025 | 17 | 38% | 41% | 38% | 37% | 36% | 43% |

## Probabilities

#### K: probabilities

Brier score (lower is better): constant forecast (the training seasons' start rate) 0.1878; logistic regression 0.1685; LightGBM 0.1744.

Calibration of the kept model (logistic regression), fixed 10-point bins:

| predicted | rows | mean predicted | observed start rate |
|---|---|---|---|
| 0%-10% | 815 | 1.1% | 4.5% |
| 10%-20% | 131 | 16.0% | 24.4% |
| 20%-30% | 259 | 25.0% | 32.0% |
| 30%-40% | 645 | 34.7% | 37.2% |
| 40%-50% | 295 | 42.0% | 37.6% |
| 50%-60% | 110 | 52.5% | 38.2% |
| 60%-70% | 42 | 63.4% | 40.5% |
| 70%-80% | 4 | 74.5% | 75.0% |
| 80%-90% | 3 | 86.2% | 33.3% |
| 90%-100% | 14 | 100.0% | 35.7% |

#### DST: probabilities

Brier score (lower is better): constant forecast (the training seasons' start rate) 0.2350; logistic regression 0.2498; LightGBM 0.2667.

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
| K | logistic regression | 37.6% (-3.0 (-7.0 to +1.1)) | 36.7% (-1.6 (-3.7 to +0.2)) | 38.1% (-4.3 (-12.1 to +2.4)) |
| K | LightGBM | 39.2% (+0.6 (-2.3 to +3.6)) | 38.4% (+1.3 (-0.4 to +3.0)) | 41.4% (+1.9 (-3.3 to +6.6)) |
| DST | logistic regression | 40.8% (-1.9 (-4.8 to +0.9)) | 39.2% (+0.1 (-1.4 to +1.6)) | 47.9% (+1.4 (-5.2 to +8.0)) |
| DST | LightGBM | 42.3% (+3.1 (+0.0 to +6.2)) | 39.4% (+0.9 (-0.6 to +2.5)) | 52.1% (+11.7 (+4.3 to +20.2)) |

## Folds

#### K: logistic regression folds (importance: log-odds contribution (std))

| test | trained on | validation | training rows (starts) | chosen settings | top 3 features |
|---|---|---|---|---|---|
| 2013 | 2012 | thin | 75 (30) | C=0.1, l1_ratio=0 | kdst_preseason_rank 14%, k_fg_att_40_plus_per_game 11%, next_venue_dome 8% |
| 2014 | 2012-2013 | 2013 | 173 (62) | C=0.01, l1_ratio=0 | team_rz_trips_per_game 9%, kdst_points_last 8%, k_pat_att_per_game 8% |
| 2015 | 2012-2014 | 2014 | 314 (111) | C=0.1, l1_ratio=1 | is_team_kicker 44%, kdst_preseason_rank 18%, team_rz_stall_rate 16% (dominant) |
| 2016 | 2012-2015 | 2015 | 420 (141) | C=0.01, l1_ratio=0 | is_team_kicker 10%, kdst_points_last 9%, kdst_preseason_rank 7% |
| 2017 | 2012-2016 | 2016 | 549 (178) | C=0.1, l1_ratio=0 | is_team_kicker 14%, team_rz_stall_rate 8%, kdst_points_last 7% |
| 2018 | 2012-2017 | 2017 | 733 (226) | C=0.1, l1_ratio=0 | is_team_kicker 13%, kdst_preseason_rank 7%, next_is_home 6% |
| 2019 | 2012-2018 | 2018 | 891 (273) | C=0.01, l1_ratio=0 | kdst_preseason_rank 10%, is_team_kicker 8%, kdst_points_last 7% |
| 2020 | 2012-2019 | 2019 | 1023 (315) | C=1, l1_ratio=0 | is_team_kicker 13%, next_is_home 9%, team_rz_stall_rate 8% |
| 2021 | 2012-2020 | 2020 | 1327 (358) | C=0.01, l1_ratio=1 | is_team_kicker 100%, k_fg_att_40_plus_per_game 0%, k_fg_att_per_game 0% (dominant) |
| 2022 | 2012-2021 | 2021 | 1610 (404) | C=0.1, l1_ratio=1 | is_team_kicker 59%, kdst_preseason_rank 7%, next_opp_sacks_allowed_per_game 6% (dominant) |
| 2023 | 2012-2022 | 2022 | 1798 (447) | C=0.01, l1_ratio=0 | is_team_kicker 15%, kdst_points_last 8%, kdst_preseason_rank 6% |
| 2024 | 2012-2023 | 2023 | 1967 (489) | C=0.01, l1_ratio=0 | is_team_kicker 14%, kdst_points_last 7%, kdst_preseason_rank 6% |
| 2025 | 2012-2024 | 2024 | 2171 (540) | C=1, l1_ratio=0 | is_team_kicker 31%, weekly_ecr_listed 7%, kdst_preseason_rank 5% |

#### K: LightGBM folds (importance: gain)

| test | trained on | validation | training rows (starts) | chosen settings | top 3 features |
|---|---|---|---|---|---|
| 2013 | 2012 | thin | 75 (30) | num_leaves=15, min_child_samples=100, colsample_bytree=0.8, n_estimators=300 | is_team_kicker 0%, k_fg_att_40_plus_per_game 0%, k_fg_att_per_game 0% (constant model: ranked by the tie-breakers) |
| 2014 | 2012-2013 | 2013 | 173 (62) | num_leaves=15, min_child_samples=50, colsample_bytree=0.7, n_estimators=1 | k_pat_att_per_game 58%, kdst_preseason_rank 42%, is_team_kicker 0% (dominant) |
| 2015 | 2012-2014 | 2014 | 314 (111) | num_leaves=15, min_child_samples=50, colsample_bytree=0.7, n_estimators=24 | k_pat_att_per_game 22%, team_points_per_game 18%, kdst_points_last 12% |
| 2016 | 2012-2015 | 2015 | 420 (141) | num_leaves=15, min_child_samples=50, colsample_bytree=1, n_estimators=104 | kdst_points_last 11%, k_pat_att_per_game 10%, next_opp_giveaways_per_game 6% |
| 2017 | 2012-2016 | 2016 | 549 (178) | num_leaves=15, min_child_samples=50, colsample_bytree=1, n_estimators=16 | kdst_points_last 20%, is_team_kicker 19%, k_pat_att_per_game 13% |
| 2018 | 2012-2017 | 2017 | 733 (226) | num_leaves=15, min_child_samples=200, colsample_bytree=0.7, n_estimators=68 | k_pat_att_per_game 26%, kdst_points_last 17%, kdst_preseason_rank 14% |
| 2019 | 2012-2018 | 2018 | 891 (273) | num_leaves=15, min_child_samples=200, colsample_bytree=1, n_estimators=76 | kdst_points_last 21%, kdst_preseason_rank 16%, k_pat_att_per_game 15% |
| 2020 | 2012-2019 | 2019 | 1023 (315) | num_leaves=15, min_child_samples=200, colsample_bytree=1, n_estimators=2 | kdst_points_last 72%, kdst_preseason_rank 19%, k_fg_pct_50_plus 10% (dominant) |
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
| 2013 | 2012 | thin | 116 (39) | num_leaves=15, min_child_samples=100, colsample_bytree=0.8, n_estimators=300 | dst_points_allowed_per_game 0%, dst_sacks_per_game 0%, dst_takeaways_per_game 0% (constant model: ranked by the tie-breakers) |
| 2014 | 2012-2013 | 2013 | 200 (79) | num_leaves=15, min_child_samples=50, colsample_bytree=0.7, n_estimators=29 | dst_points_allowed_per_game 36%, next_opp_rz_trips_allowed_per_game 26%, dst_sacks_per_game 9% |
| 2015 | 2012-2014 | 2014 | 311 (114) | num_leaves=15, min_child_samples=50, colsample_bytree=0.7, n_estimators=2 | next_opp_points_allowed_per_game 25%, kdst_preseason_rank 24%, next_opp_points_per_game 16% |
| 2016 | 2012-2015 | 2015 | 410 (155) | num_leaves=15, min_child_samples=50, colsample_bytree=1, n_estimators=14 | next_opp_sacks_allowed_per_game 17%, team_rz_stall_rate 17%, next_opp_points_allowed_per_game 16% |
| 2017 | 2012-2016 | 2016 | 519 (195) | num_leaves=15, min_child_samples=50, colsample_bytree=0.7, n_estimators=18 | next_opp_sacks_allowed_per_game 14%, team_rz_stall_rate 9%, next_opp_points_allowed_per_game 9% |
| 2018 | 2012-2017 | 2017 | 617 (239) | num_leaves=15, min_child_samples=200, colsample_bytree=1, n_estimators=394 | next_opp_points_per_game 13%, team_points_per_game 13%, dst_points_allowed_per_game 11% |
| 2019 | 2012-2018 | 2018 | 709 (275) | num_leaves=15, min_child_samples=50, colsample_bytree=0.7, n_estimators=28 | next_opp_sacks_allowed_per_game 12%, team_points_per_game 11%, next_opp_points_per_game 10% |
| 2020 | 2012-2019 | 2019 | 820 (315) | num_leaves=15, min_child_samples=50, colsample_bytree=1, n_estimators=44 | team_rz_stall_rate 11%, next_opp_sacks_allowed_per_game 9%, team_points_per_game 9% |
| 2021 | 2012-2020 | 2020 | 958 (360) | num_leaves=15, min_child_samples=200, colsample_bytree=1, n_estimators=32 | next_opp_sacks_allowed_per_game 26%, next_opp_points_per_game 19%, next_opp_points_allowed_per_game 18% |
| 2022 | 2012-2021 | 2021 | 1073 (395) | num_leaves=31, min_child_samples=50, colsample_bytree=1, n_estimators=19 | next_opp_sacks_allowed_per_game 13%, next_opp_points_allowed_per_game 9%, kdst_points_per_game 9% |
| 2023 | 2012-2022 | 2022 | 1175 (436) | num_leaves=15, min_child_samples=200, colsample_bytree=1, n_estimators=45 | next_opp_sacks_allowed_per_game 23%, next_opp_points_per_game 14%, kdst_points_per_game 10% |
| 2024 | 2012-2023 | 2023 | 1317 (493) | num_leaves=15, min_child_samples=200, colsample_bytree=1, n_estimators=4 | next_opp_points_per_game 32%, next_opp_sacks_allowed_per_game 25%, kdst_preseason_rank 11% |
| 2025 | 2012-2024 | 2024 | 1428 (528) | num_leaves=31, min_child_samples=50, colsample_bytree=1, n_estimators=3 | next_opp_sacks_allowed_per_game 16%, next_opp_points_allowed_per_game 13%, team_points_per_game 13% |
