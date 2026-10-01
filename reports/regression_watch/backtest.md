# Regression Watch backtest: rest-of-season projection and tags (step D3)

`uv run twm regression backtest` on the warehouse built 2026-10-01 17:01:25; xFP: own walk-forward, step H6-b2. Walk-forward test seasons 2011-2025, headline as-of weeks 4, 6, 8, 10; all weeks 4-14 as a secondary table. Points per game, full PPR (config/scoring.yaml). Glossary: `twm glossary ppg_ros`.

**Two tags in the product.** Legit was tested here and dropped from the product (owner, 2026-09-30) because it predicted nothing: 59.0% of its 3,132 players stayed starters, against 61.1% of every starter. It stays in this report as tested (docs/regression_watch.md).

**P1 acceptance (MET).** Pooled over the test seasons at the headline as-of weeks, the projection's MAE is 3.12 points per game against 3.41 for season-to-date PPG: a difference of -0.28 (-0.36 to -0.21) (95% season-block bootstrap interval), which excludes 0.

Variants chosen (on earlier seasons only): `zero_flat_all`, `zero_hl4_all`, `zero_hl4_ng_fpoe`, `zero_hl8_all` (by season below).

## How the backtest works

- **Universe** at each as-of (the official Tuesday after the week, season to date only): QBs, RBs, WRs and TEs with at least 3 games whose PPG or xFP/game ranks inside teams x (starting slots the position can fill, FLEX included) x the pool multiplier: QB 18, RB 54, WR 54, TE 36.
- **Projection** = recency-weighted xFP/game + r(g) x FPOE/game, where r(g) is D2's shrinkage factor for his position after his g games, estimated only on seasons 2009 .. S-1 for season S. 24 variants: shrink toward 0 (the spec formula) or toward the position's average FPOE; recency half-life none / 8 / 4 / 2 games; garbage time kept, left out of the efficiency, or left out of both parts (opportunity scaled back up by the position's xFP / no-garbage xFP ratio of the earlier seasons).
- **Walk-forward**: test seasons 2011-2025. For test season S the variant with the lowest MAE on the validation seasons 2010 .. S-1 (headline weeks) is used; the test season is never looked at. Each validation season is itself projected with shrinkage from the seasons before it.
- **Outcome**: points per game over the rest of the regular season (games played, rows public strictly after the as-of). Players with fewer than 3 games left are left out and counted (table below).
- **Baselines**: season-to-date PPG and the average of the last 3 games.
- **Intervals**: 95%, 2,000 resamples of whole seasons (the Radar's season-block bootstrap); differences are paired (the same resampled seasons for both methods). Spearman is computed per weekly list of a position and averaged.
- **Tags**: Sell-high = FPOE/game in the top decile of his position's universe AND the projection at least X points/game below his PPG; Buy-low the mirror (bottom decile, at least X above); Legit = PPG rank inside the starter threshold (QB 12, RB 24, WR 24, TE 12) with FPOE/game not in the top decile. X (0 to 6 by 0.5) is chosen per test season on the validation seasons: the best precision among the X that tag at least 3 players per as-of. Hits: Sell-high = rest-of-season PPG below his PPG at the as-of; Buy-low = above; Legit = his rest-of-season PPG still ranks inside the threshold (among every player of the position with 3+ games left).

## Headline: mean absolute error (points per game), as-of weeks 4, 6, 8, 10

Lower is better; a negative difference means the projection was closer. Rows = graded player-as-ofs; value (95% interval).

| position | rows | projection | season-to-date PPG | last-3 PPG | projection - PPG | projection - last 3 |
|---|---|---|---|---|---|---|
| QB | 1184 | 3.12 (2.87 to 3.38) | 3.51 (3.22 to 3.80) | 4.17 (3.89 to 4.45) | -0.38 (-0.62 to -0.16) | -1.04 (-1.26 to -0.80) |
| RB | 3164 | 3.40 (3.30 to 3.50) | 3.58 (3.46 to 3.72) | 4.20 (4.09 to 4.32) | -0.18 (-0.27 to -0.10) | -0.80 (-0.93 to -0.68) |
| WR | 3414 | 3.34 (3.22 to 3.46) | 3.65 (3.53 to 3.77) | 4.32 (4.21 to 4.43) | -0.31 (-0.40 to -0.21) | -0.98 (-1.10 to -0.86) |
| TE | 2251 | 2.41 (2.29 to 2.56) | 2.75 (2.61 to 2.89) | 3.23 (3.14 to 3.32) | -0.34 (-0.48 to -0.21) | -0.82 (-0.95 to -0.69) |
| **pooled** | 10013 | 3.12 (3.04 to 3.21) | 3.41 (3.33 to 3.49) | 4.02 (3.96 to 4.08) | -0.28 (-0.36 to -0.21) | -0.89 (-0.97 to -0.81) |

## Rank correlation (Spearman), headline weeks

Higher is better: how well each method orders the players of one position in one week, averaged over those weekly lists.

| position | lists | projection | season-to-date PPG | last-3 PPG | projection - PPG | projection - last 3 |
|---|---|---|---|---|---|---|
| QB | 60 | 0.360 (0.279 to 0.438) | 0.379 (0.281 to 0.466) | 0.312 (0.224 to 0.391) | -0.019 (-0.071 to 0.037) | 0.048 (-0.023 to 0.109) |
| RB | 60 | 0.605 (0.562 to 0.643) | 0.602 (0.555 to 0.645) | 0.547 (0.504 to 0.586) | 0.004 (-0.013 to 0.023) | 0.058 (0.037 to 0.079) |
| WR | 60 | 0.498 (0.449 to 0.548) | 0.490 (0.443 to 0.538) | 0.392 (0.359 to 0.429) | 0.007 (-0.019 to 0.032) | 0.105 (0.074 to 0.136) |
| TE | 60 | 0.651 (0.616 to 0.680) | 0.601 (0.559 to 0.641) | 0.520 (0.480 to 0.562) | 0.050 (0.025 to 0.078) | 0.131 (0.101 to 0.161) |
| **pooled** | 240 | 0.528 (0.504 to 0.553) | 0.518 (0.485 to 0.550) | 0.443 (0.415 to 0.470) | 0.011 (-0.008 to 0.030) | 0.086 (0.071 to 0.099) |

## All as-of weeks 4-14 (secondary)

| position | rows | projection | season-to-date PPG | last-3 PPG | projection - PPG | projection - last 3 |
|---|---|---|---|---|---|---|
| QB | 3137 | 3.24 (3.01 to 3.46) | 3.55 (3.32 to 3.79) | 4.38 (4.16 to 4.60) | -0.31 (-0.52 to -0.11) | -1.15 (-1.34 to -0.92) |
| RB | 8181 | 3.48 (3.38 to 3.57) | 3.62 (3.52 to 3.73) | 4.31 (4.19 to 4.44) | -0.14 (-0.23 to -0.07) | -0.84 (-0.97 to -0.71) |
| WR | 8935 | 3.46 (3.34 to 3.58) | 3.68 (3.56 to 3.79) | 4.42 (4.32 to 4.51) | -0.21 (-0.30 to -0.13) | -0.96 (-1.07 to -0.85) |
| TE | 5818 | 2.54 (2.43 to 2.66) | 2.81 (2.70 to 2.92) | 3.35 (3.25 to 3.44) | -0.28 (-0.38 to -0.18) | -0.81 (-0.91 to -0.71) |
| **pooled** | 26071 | 3.23 (3.14 to 3.32) | 3.45 (3.38 to 3.53) | 4.14 (4.08 to 4.20) | -0.22 (-0.29 to -0.14) | -0.91 (-0.98 to -0.83) |

| position | lists | projection | season-to-date PPG | last-3 PPG | projection - PPG | projection - last 3 |
|---|---|---|---|---|---|---|
| QB | 165 | 0.332 (0.249 to 0.417) | 0.349 (0.249 to 0.449) | 0.262 (0.197 to 0.337) | -0.017 (-0.058 to 0.027) | 0.070 (0.020 to 0.116) |
| RB | 165 | 0.585 (0.538 to 0.621) | 0.586 (0.537 to 0.627) | 0.520 (0.470 to 0.560) | -0.002 (-0.016 to 0.015) | 0.065 (0.045 to 0.084) |
| WR | 165 | 0.474 (0.430 to 0.520) | 0.478 (0.434 to 0.524) | 0.396 (0.368 to 0.426) | -0.004 (-0.029 to 0.021) | 0.079 (0.047 to 0.112) |
| TE | 165 | 0.623 (0.588 to 0.656) | 0.585 (0.549 to 0.620) | 0.488 (0.442 to 0.533) | 0.038 (0.019 to 0.059) | 0.135 (0.107 to 0.162) |
| **pooled** | 660 | 0.504 (0.479 to 0.529) | 0.500 (0.465 to 0.534) | 0.416 (0.393 to 0.443) | 0.004 (-0.013 to 0.021) | 0.087 (0.074 to 0.100) |

## By as-of week (all test seasons, pooled positions)

| as-of week | universe rows | left out (< 3 games left) | graded | projection | PPG | last 3 |
|---|---|---|---|---|---|---|
| 4 | 2691 | 103 | 2588 | 3.04 | 3.49 | 3.80 |
| 5 | 2723 | 129 | 2594 | 3.04 | 3.41 | 3.97 |
| 6 | 2715 | 167 | 2548 | 3.06 | 3.35 | 3.96 |
| 7 | 2726 | 198 | 2528 | 3.05 | 3.34 | 4.09 |
| 8 | 2716 | 233 | 2483 | 3.15 | 3.37 | 4.09 |
| 9 | 2703 | 278 | 2425 | 3.19 | 3.40 | 4.14 |
| 10 | 2704 | 310 | 2394 | 3.25 | 3.41 | 4.24 |
| 11 | 2699 | 372 | 2327 | 3.26 | 3.37 | 4.16 |
| 12 | 2680 | 456 | 2224 | 3.37 | 3.43 | 4.16 |
| 13 | 2687 | 581 | 2106 | 3.54 | 3.59 | 4.42 |
| 14 | 2678 | 824 | 1854 | 3.85 | 3.93 | 4.75 |

## By test season: what was chosen, and how it did

| test season | variant | validation seasons | validation MAE | spec formula's validation MAE | X Sell-high | X Buy-low | graded rows | test MAE projection | test MAE PPG |
|---|---|---|---|---|---|---|---|---|---|
| 2011 | `zero_hl4_ng_fpoe` | 2010-2010 | 2.998 | 3.063 | 5 | 3 | 685 | 3.27 | 3.35 |
| 2012 | `zero_hl4_all` | 2010-2011 | 3.124 | 3.160 | 5.5 | 2.5 | 673 | 3.16 | 3.31 |
| 2013 | `zero_hl4_all` | 2010-2012 | 3.136 | 3.158 | 5 | 2.5 | 645 | 3.02 | 3.32 |
| 2014 | `zero_hl8_all` | 2010-2013 | 3.104 | 3.115 | 4.5 | 3 | 684 | 3.07 | 3.39 |
| 2015 | `zero_hl8_all` | 2010-2014 | 3.096 | 3.108 | 4.5 | 3 | 658 | 3.09 | 3.41 |
| 2016 | `zero_hl8_all` | 2010-2015 | 3.094 | 3.099 | 4.5 | 3 | 658 | 2.89 | 3.19 |
| 2017 | `zero_hl8_all` | 2010-2016 | 3.065 | 3.069 | 4.5 | 0 | 672 | 2.93 | 3.18 |
| 2018 | `zero_hl8_all` | 2010-2017 | 3.049 | 3.052 | 4.5 | 3 | 656 | 3.15 | 3.69 |
| 2019 | `zero_hl8_all` | 2010-2018 | 3.059 | 3.061 | 4.5 | 3 | 658 | 3.23 | 3.71 |
| 2020 | `zero_flat_all` | 2010-2019 | 3.076 | 3.076 | 4.5 | 3 | 671 | 3.25 | 3.48 |
| 2021 | `zero_flat_all` | 2010-2020 | 3.091 | 3.091 | 5 | 0 | 664 | 3.17 | 3.44 |
| 2022 | `zero_flat_all` | 2010-2021 | 3.098 | 3.098 | 5 | 3 | 671 | 2.85 | 3.28 |
| 2023 | `zero_flat_all` | 2010-2022 | 3.079 | 3.079 | 5 | 3 | 678 | 3.11 | 3.34 |
| 2024 | `zero_flat_all` | 2010-2023 | 3.081 | 3.081 | 5 | 3 | 687 | 3.55 | 3.48 |
| 2025 | `zero_flat_all` | 2010-2024 | 3.113 | 3.113 | 5 | 3 | 653 | 3.10 | 3.54 |

## Tags (test seasons, headline weeks)

- **Sell-high**: 93.9% of 262 tags came true against a base rate of 60.5% (every universe player) and 81.6% for top-decile FPOE, any projection; its interval is above the base rate.
- **Buy-low**: 58.7% of 421 tags came true against a base rate of 39.4% (every universe player) and 59.8% for bottom-decile FPOE, any projection; its interval is above the base rate.
- **Legit**: 59.0% of 3132 tags came true against a base rate of 61.1% (every player inside the starter threshold) and 68.2% for inside it with top-decile FPOE; its interval does NOT clear the base rate.

| tag | position | tags graded | per as-of | hit rate | base rate | reference | not graded (< 3 games left) |
|---|---|---|---|---|---|---|---|
| Sell-high | QB | 43 | 0.7 | 100.0% (100.0% to 100.0%) | 64.5% (58.4% to 70.8%) | 80.7% (70.1% to 89.2%) | 2 |
| Sell-high | RB | 59 | 1.0 | 98.3% (94.6% to 100.0%) | 58.3% (56.7% to 59.9%) | 80.9% (74.9% to 86.5%) | 2 |
| Sell-high | WR | 128 | 2.1 | 89.1% (81.9% to 95.9%) | 62.5% (59.3% to 65.5%) | 81.9% (75.7% to 87.7%) | 9 |
| Sell-high | TE | 32 | 0.5 | 96.9% (87.5% to 100.0%) | 58.4% (54.7% to 61.7%) | 82.7% (76.2% to 89.0%) | 3 |
| Sell-high | pooled | 262 | 4.4 | 93.9% (89.6% to 97.6%) | 60.5% (58.3% to 62.4%) | 81.6% (77.3% to 85.0%) | 16 |
| Buy-low | QB | 55 | 0.9 | 49.1% (30.4% to 68.6%) | 35.5% (29.2% to 41.6%) | 54.7% (44.1% to 63.9%) | 7 |
| Buy-low | RB | 126 | 2.1 | 52.4% (41.4% to 64.9%) | 41.6% (40.0% to 43.2%) | 57.3% (50.4% to 64.4%) | 21 |
| Buy-low | WR | 177 | 3.0 | 63.3% (57.3% to 69.0%) | 37.5% (34.3% to 40.7%) | 56.9% (52.1% to 61.2%) | 26 |
| Buy-low | TE | 63 | 1.1 | 66.7% (56.9% to 75.4%) | 41.4% (38.1% to 45.1%) | 69.7% (64.2% to 75.5%) | 2 |
| Buy-low | pooled | 421 | 7.0 | 58.7% (54.9% to 63.6%) | 39.4% (37.5% to 41.6%) | 59.8% (57.2% to 62.6%) | 56 |
| Legit | QB | 535 | 8.9 | 55.9% (48.7% to 64.0%) | 59.6% (53.8% to 66.3%) | 73.6% (63.0% to 83.2%) | 30 |
| Legit | RB | 1041 | 17.4 | 63.7% (59.9% to 67.5%) | 67.4% (63.6% to 71.1%) | 80.3% (75.2% to 85.3%) | 81 |
| Legit | WR | 1052 | 17.5 | 57.4% (53.0% to 62.2%) | 57.6% (54.6% to 60.9%) | 58.5% (50.5% to 65.7%) | 61 |
| Legit | TE | 504 | 8.4 | 56.0% (51.5% to 60.4%) | 57.0% (52.9% to 60.9%) | 60.1% (52.4% to 67.4%) | 24 |
| Legit | pooled | 3132 | 52.2 | 59.0% (57.3% to 60.8%) | 61.1% (59.6% to 62.6%) | 68.2% (64.9% to 71.4%) | 196 |

All as-of weeks 4-14, pooled positions:

| tag | position | tags graded | per as-of | hit rate | base rate | reference | not graded (< 3 games left) |
|---|---|---|---|---|---|---|---|
| Sell-high | pooled | 482 | 2.9 | 94.4% (91.1% to 97.5%) | 59.5% (57.5% to 61.4%) | 77.8% (73.8% to 81.8%) | 54 |
| Buy-low | pooled | 891 | 5.4 | 56.1% (52.5% to 61.6%) | 40.4% (38.6% to 42.5%) | 57.9% (55.1% to 61.2%) | 178 |
| Legit | pooled | 8288 | 50.2 | 60.4% (58.9% to 61.8%) | 62.7% (61.3% to 64.0%) | 70.9% (67.7% to 74.2%) | 907 |

## Shrinkage used for each test season

| test season | estimated on | r(8) QB | r(8) RB | r(8) WR | r(8) TE | mean FPOE/g QB | mean FPOE/g RB | mean FPOE/g WR | mean FPOE/g TE |
|---|---|---|---|---|---|---|---|---|---|
| 2011 | 2009-2010 | 0.386 | 0.281 | 0.244 | 0.450 | 0.10 | 0.08 | 0.02 | 0.36 |
| 2012 | 2009-2011 | 0.376 | 0.344 | 0.289 | 0.417 | 0.10 | 0.05 | 0.04 | 0.34 |
| 2013 | 2009-2012 | 0.415 | 0.343 | 0.276 | 0.345 | 0.18 | 0.02 | 0.05 | 0.31 |
| 2014 | 2009-2013 | 0.388 | 0.365 | 0.269 | 0.244 | 0.21 | 0.01 | 0.04 | 0.38 |
| 2015 | 2009-2014 | 0.391 | 0.359 | 0.271 | 0.238 | 0.30 | 0.03 | 0.08 | 0.39 |
| 2016 | 2009-2015 | 0.387 | 0.343 | 0.268 | 0.239 | 0.42 | 0.02 | 0.11 | 0.40 |
| 2017 | 2009-2016 | 0.371 | 0.326 | 0.242 | 0.230 | 0.42 | 0.05 | 0.11 | 0.40 |
| 2018 | 2009-2017 | 0.355 | 0.328 | 0.240 | 0.224 | 0.42 | 0.05 | 0.10 | 0.37 |
| 2019 | 2009-2018 | 0.342 | 0.339 | 0.236 | 0.233 | 0.49 | 0.12 | 0.15 | 0.40 |
| 2020 | 2009-2019 | 0.361 | 0.331 | 0.224 | 0.208 | 0.51 | 0.15 | 0.18 | 0.41 |
| 2021 | 2009-2020 | 0.367 | 0.312 | 0.234 | 0.211 | 0.57 | 0.17 | 0.24 | 0.42 |
| 2022 | 2009-2021 | 0.365 | 0.302 | 0.244 | 0.203 | 0.54 | 0.18 | 0.26 | 0.42 |
| 2023 | 2009-2022 | 0.362 | 0.286 | 0.235 | 0.207 | 0.52 | 0.19 | 0.26 | 0.42 |
| 2024 | 2009-2023 | 0.359 | 0.283 | 0.241 | 0.200 | 0.49 | 0.18 | 0.27 | 0.43 |
| 2025 | 2009-2024 | 0.360 | 0.290 | 0.246 | 0.204 | 0.51 | 0.20 | 0.28 | 0.44 |

## Limits

- xFP is Regression Watch's own walk-forward xFP (step H6-b2): each season's expectations come from models trained on the seasons before it only (PROJECT_SPEC 6.3). The garbage-time flag still comes from nflfastR's win probability, trained on many seasons, including later ones: a mild, known leak.
- Stat corrections are in the cache's latest version (docs/assumptions.md section 12).
- The outcome counts games played: a player who gets hurt is graded on the games he played, and one with fewer than 3 games left is not graded at all.
- One variant for all positions per season; r(g) has one noise size per position.
- Validation and test seasons share the same league-wide trends; the season-block bootstrap treats seasons as independent.
