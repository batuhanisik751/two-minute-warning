# Regression Watch backtest: rest-of-season projection and tags (step D3)

`uv run twm regression backtest` on the warehouse built 2026-09-29 20:58:11. Walk-forward test seasons 2011-2025, headline as-of weeks 4, 6, 8, 10; all weeks 4-14 as a secondary table. Points per game, full PPR (config/scoring.yaml). Glossary: `twm glossary ppg_ros`.

**Note added 2026-09-30 (after this run; nothing below changed):** the product dropped the Legit tag, which predicted nothing (60.6% of its players stayed starters, base rate 61.1%); it stays in this report as tested (docs/regression_watch.md).

**P1 acceptance (MET).** Pooled over the test seasons at the headline as-of weeks, the projection's MAE is 3.21 points per game against 3.41 for season-to-date PPG: a difference of -0.19 (-0.25 to -0.13) (95% season-block bootstrap interval), which excludes 0.

Variants chosen (on earlier seasons only): `mean_hl4_all`, `mean_hl8_all` (by season below).

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
| QB | 1170 | 3.38 (3.08 to 3.73) | 3.50 (3.21 to 3.79) | 4.18 (3.89 to 4.49) | -0.11 (-0.31 to 0.09) | -0.80 (-1.04 to -0.48) |
| RB | 3162 | 3.47 (3.35 to 3.58) | 3.58 (3.46 to 3.72) | 4.20 (4.09 to 4.32) | -0.12 (-0.21 to -0.04) | -0.73 (-0.87 to -0.61) |
| WR | 3408 | 3.40 (3.29 to 3.50) | 3.64 (3.52 to 3.77) | 4.33 (4.21 to 4.45) | -0.25 (-0.33 to -0.17) | -0.93 (-1.06 to -0.81) |
| TE | 2245 | 2.49 (2.39 to 2.62) | 2.75 (2.61 to 2.89) | 3.23 (3.14 to 3.32) | -0.26 (-0.39 to -0.13) | -0.74 (-0.85 to -0.61) |
| **pooled** | 9985 | 3.21 (3.12 to 3.30) | 3.41 (3.33 to 3.48) | 4.02 (3.96 to 4.09) | -0.19 (-0.25 to -0.13) | -0.81 (-0.88 to -0.73) |

## Rank correlation (Spearman), headline weeks

Higher is better: how well each method orders the players of one position in one week, averaged over those weekly lists.

| position | lists | projection | season-to-date PPG | last-3 PPG | projection - PPG | projection - last 3 |
|---|---|---|---|---|---|---|
| QB | 60 | 0.349 (0.245 to 0.449) | 0.372 (0.281 to 0.462) | 0.298 (0.210 to 0.383) | -0.023 (-0.083 to 0.044) | 0.051 (-0.035 to 0.129) |
| RB | 60 | 0.602 (0.560 to 0.642) | 0.601 (0.553 to 0.645) | 0.546 (0.502 to 0.586) | 0.001 (-0.014 to 0.019) | 0.056 (0.035 to 0.077) |
| WR | 60 | 0.501 (0.453 to 0.552) | 0.489 (0.445 to 0.535) | 0.390 (0.357 to 0.427) | 0.012 (-0.011 to 0.034) | 0.111 (0.084 to 0.140) |
| TE | 60 | 0.648 (0.609 to 0.683) | 0.601 (0.560 to 0.640) | 0.520 (0.481 to 0.561) | 0.047 (0.021 to 0.076) | 0.128 (0.101 to 0.153) |
| **pooled** | 240 | 0.525 (0.495 to 0.556) | 0.516 (0.484 to 0.548) | 0.439 (0.408 to 0.468) | 0.009 (-0.008 to 0.027) | 0.086 (0.070 to 0.103) |

## All as-of weeks 4-14 (secondary)

| position | rows | projection | season-to-date PPG | last-3 PPG | projection - PPG | projection - last 3 |
|---|---|---|---|---|---|---|
| QB | 3097 | 3.43 (3.17 to 3.72) | 3.53 (3.30 to 3.77) | 4.39 (4.17 to 4.61) | -0.11 (-0.27 to 0.07) | -0.96 (-1.15 to -0.69) |
| RB | 8176 | 3.52 (3.41 to 3.63) | 3.62 (3.52 to 3.73) | 4.31 (4.19 to 4.43) | -0.10 (-0.18 to -0.02) | -0.78 (-0.92 to -0.66) |
| WR | 8920 | 3.50 (3.39 to 3.61) | 3.68 (3.56 to 3.79) | 4.42 (4.32 to 4.51) | -0.18 (-0.24 to -0.11) | -0.92 (-1.03 to -0.82) |
| TE | 5796 | 2.60 (2.50 to 2.71) | 2.81 (2.71 to 2.92) | 3.35 (3.26 to 3.44) | -0.21 (-0.31 to -0.12) | -0.75 (-0.84 to -0.66) |
| **pooled** | 25989 | 3.30 (3.21 to 3.38) | 3.45 (3.38 to 3.53) | 4.14 (4.08 to 4.20) | -0.15 (-0.20 to -0.10) | -0.84 (-0.90 to -0.78) |

| position | lists | projection | season-to-date PPG | last-3 PPG | projection - PPG | projection - last 3 |
|---|---|---|---|---|---|---|
| QB | 165 | 0.319 (0.221 to 0.418) | 0.337 (0.231 to 0.439) | 0.251 (0.186 to 0.328) | -0.019 (-0.066 to 0.036) | 0.067 (0.002 to 0.124) |
| RB | 165 | 0.585 (0.537 to 0.624) | 0.585 (0.533 to 0.628) | 0.520 (0.469 to 0.562) | -0.000 (-0.013 to 0.014) | 0.065 (0.045 to 0.084) |
| WR | 165 | 0.476 (0.438 to 0.518) | 0.475 (0.433 to 0.519) | 0.394 (0.367 to 0.423) | 0.001 (-0.018 to 0.022) | 0.082 (0.056 to 0.109) |
| TE | 165 | 0.621 (0.583 to 0.658) | 0.585 (0.550 to 0.620) | 0.488 (0.443 to 0.532) | 0.036 (0.016 to 0.058) | 0.134 (0.108 to 0.158) |
| **pooled** | 660 | 0.500 (0.472 to 0.529) | 0.496 (0.461 to 0.530) | 0.413 (0.390 to 0.439) | 0.005 (-0.011 to 0.021) | 0.087 (0.073 to 0.101) |

## By as-of week (all test seasons, pooled positions)

| as-of week | universe rows | left out (< 3 games left) | graded | projection | PPG | last 3 |
|---|---|---|---|---|---|---|
| 4 | 2687 | 103 | 2584 | 3.16 | 3.48 | 3.80 |
| 5 | 2713 | 128 | 2585 | 3.16 | 3.41 | 3.97 |
| 6 | 2703 | 165 | 2538 | 3.17 | 3.35 | 3.96 |
| 7 | 2704 | 188 | 2516 | 3.14 | 3.34 | 4.09 |
| 8 | 2705 | 227 | 2478 | 3.22 | 3.37 | 4.10 |
| 9 | 2688 | 273 | 2415 | 3.24 | 3.39 | 4.14 |
| 10 | 2692 | 307 | 2385 | 3.32 | 3.42 | 4.25 |
| 11 | 2685 | 366 | 2319 | 3.29 | 3.38 | 4.17 |
| 12 | 2671 | 448 | 2223 | 3.41 | 3.44 | 4.16 |
| 13 | 2679 | 578 | 2101 | 3.53 | 3.58 | 4.41 |
| 14 | 2667 | 822 | 1845 | 3.85 | 3.92 | 4.74 |

## By test season: what was chosen, and how it did

| test season | variant | validation seasons | validation MAE | spec formula's validation MAE | X Sell-high | X Buy-low | graded rows | test MAE projection | test MAE PPG |
|---|---|---|---|---|---|---|---|---|---|
| 2011 | `mean_hl4_all` | 2010-2010 | 3.020 | 3.090 | 5 | 0.5 | 688 | 3.25 | 3.33 |
| 2012 | `mean_hl4_all` | 2010-2011 | 3.134 | 3.195 | 5.5 | 3 | 676 | 3.24 | 3.30 |
| 2013 | `mean_hl4_all` | 2010-2012 | 3.171 | 3.231 | 5 | 3 | 642 | 3.11 | 3.35 |
| 2014 | `mean_hl8_all` | 2010-2013 | 3.151 | 3.208 | 5 | 3.5 | 679 | 3.10 | 3.39 |
| 2015 | `mean_hl8_all` | 2010-2014 | 3.141 | 3.193 | 4.5 | 3 | 650 | 3.09 | 3.39 |
| 2016 | `mean_hl8_all` | 2010-2015 | 3.133 | 3.171 | 5 | 3 | 660 | 2.93 | 3.17 |
| 2017 | `mean_hl8_all` | 2010-2016 | 3.104 | 3.142 | 4.5 | 3 | 673 | 3.00 | 3.18 |
| 2018 | `mean_hl8_all` | 2010-2017 | 3.091 | 3.126 | 4.5 | 3 | 648 | 3.35 | 3.70 |
| 2019 | `mean_hl8_all` | 2010-2018 | 3.119 | 3.157 | 4.5 | 3 | 662 | 3.34 | 3.68 |
| 2020 | `mean_hl8_all` | 2010-2019 | 3.141 | 3.176 | 4.5 | 3 | 666 | 3.38 | 3.49 |
| 2021 | `mean_hl8_all` | 2010-2020 | 3.163 | 3.196 | 4.5 | 3 | 657 | 3.38 | 3.44 |
| 2022 | `mean_hl8_all` | 2010-2021 | 3.181 | 3.216 | 4.5 | 3 | 669 | 3.04 | 3.30 |
| 2023 | `mean_hl8_all` | 2010-2022 | 3.170 | 3.207 | 4.5 | 3 | 675 | 3.21 | 3.36 |
| 2024 | `mean_hl8_all` | 2010-2023 | 3.173 | 3.206 | 4.5 | 3 | 684 | 3.56 | 3.47 |
| 2025 | `mean_hl8_all` | 2010-2024 | 3.199 | 3.230 | 4.5 | 3 | 656 | 3.21 | 3.54 |

## Tags (test seasons, headline weeks)

- **Sell-high**: 92.4% of 237 tags came true against a base rate of 60.5% (every universe player) and 81.7% for top-decile FPOE, any projection; its interval is above the base rate.
- **Buy-low**: 61.7% of 447 tags came true against a base rate of 39.4% (every universe player) and 60.8% for bottom-decile FPOE, any projection; its interval is above the base rate.
- **Legit**: 60.6% of 3195 tags came true against a base rate of 61.1% (every player inside the starter threshold) and 63.1% for inside it with top-decile FPOE; its interval does NOT clear the base rate.

| tag | position | tags graded | per as-of | hit rate | base rate | reference | not graded (< 3 games left) |
|---|---|---|---|---|---|---|---|
| Sell-high | QB | 46 | 0.8 | 93.5% (83.3% to 100.0%) | 64.8% (58.6% to 71.0%) | 83.8% (75.8% to 91.0%) | 8 |
| Sell-high | RB | 63 | 1.1 | 92.1% (84.5% to 98.3%) | 58.3% (56.6% to 59.9%) | 80.8% (74.8% to 86.3%) | 2 |
| Sell-high | WR | 108 | 1.8 | 91.7% (84.9% to 97.8%) | 62.3% (59.2% to 65.4%) | 82.1% (75.3% to 88.9%) | 12 |
| Sell-high | TE | 20 | 0.3 | 95.0% (83.3% to 100.0%) | 58.6% (54.8% to 61.9%) | 80.8% (73.7% to 87.7%) | 2 |
| Sell-high | pooled | 237 | 4.0 | 92.4% (87.6% to 96.4%) | 60.5% (58.3% to 62.4%) | 81.7% (77.5% to 85.1%) | 24 |
| Buy-low | QB | 88 | 1.5 | 58.0% (43.9% to 71.7%) | 35.2% (29.0% to 41.4%) | 55.6% (42.3% to 67.6%) | 8 |
| Buy-low | RB | 119 | 2.0 | 53.8% (41.2% to 67.5%) | 41.7% (40.0% to 43.3%) | 56.5% (47.8% to 64.9%) | 24 |
| Buy-low | WR | 176 | 2.9 | 67.6% (58.7% to 75.5%) | 37.6% (34.5% to 40.8%) | 60.5% (55.1% to 65.9%) | 23 |
| Buy-low | TE | 64 | 1.1 | 65.6% (56.2% to 75.5%) | 41.3% (38.0% to 45.1%) | 69.8% (64.7% to 74.4%) | 1 |
| Buy-low | pooled | 447 | 7.5 | 61.7% (55.7% to 67.2%) | 39.4% (37.6% to 41.6%) | 60.8% (58.0% to 63.5%) | 56 |
| Legit | QB | 540 | 9.0 | 58.5% (51.6% to 66.2%) | 59.6% (53.8% to 66.3%) | 64.0% (54.4% to 72.3%) | 32 |
| Legit | RB | 1049 | 17.5 | 64.0% (60.3% to 67.5%) | 67.4% (63.6% to 71.1%) | 79.7% (74.0% to 85.0%) | 85 |
| Legit | WR | 1073 | 17.9 | 59.4% (55.2% to 64.0%) | 57.6% (54.6% to 60.9%) | 51.1% (43.0% to 59.0%) | 66 |
| Legit | TE | 533 | 8.9 | 58.3% (53.8% to 62.8%) | 57.0% (52.9% to 60.9%) | 52.3% (42.4% to 61.6%) | 24 |
| Legit | pooled | 3195 | 53.2 | 60.6% (58.8% to 62.4%) | 61.1% (59.6% to 62.6%) | 63.1% (58.6% to 66.9%) | 207 |

All as-of weeks 4-14, pooled positions:

| tag | position | tags graded | per as-of | hit rate | base rate | reference | not graded (< 3 games left) |
|---|---|---|---|---|---|---|---|
| Sell-high | pooled | 446 | 2.7 | 91.0% (87.1% to 94.9%) | 59.5% (57.5% to 61.3%) | 77.8% (74.2% to 81.3%) | 79 |
| Buy-low | pooled | 940 | 5.7 | 60.5% (54.0% to 66.5%) | 40.4% (38.7% to 42.5%) | 57.6% (54.5% to 60.8%) | 157 |
| Legit | pooled | 8443 | 51.2 | 61.7% (60.0% to 63.4%) | 62.7% (61.3% to 64.0%) | 66.5% (62.5% to 70.2%) | 948 |

## Shrinkage used for each test season

| test season | estimated on | r(8) QB | r(8) RB | r(8) WR | r(8) TE | mean FPOE/g QB | mean FPOE/g RB | mean FPOE/g WR | mean FPOE/g TE |
|---|---|---|---|---|---|---|---|---|---|
| 2011 | 2009-2010 | 0.161 | 0.237 | 0.143 | 0.385 | -0.97 | 0.16 | -0.17 | -0.02 |
| 2012 | 2009-2011 | 0.069 | 0.302 | 0.178 | 0.338 | -1.06 | 0.10 | -0.15 | -0.08 |
| 2013 | 2009-2012 | 0.170 | 0.314 | 0.174 | 0.273 | -1.05 | 0.07 | -0.16 | -0.13 |
| 2014 | 2009-2013 | 0.150 | 0.337 | 0.170 | 0.165 | -1.04 | 0.04 | -0.17 | -0.07 |
| 2015 | 2009-2014 | 0.143 | 0.325 | 0.168 | 0.162 | -0.98 | 0.02 | -0.14 | -0.05 |
| 2016 | 2009-2015 | 0.166 | 0.306 | 0.172 | 0.165 | -0.81 | 0.00 | -0.09 | -0.03 |
| 2017 | 2009-2016 | 0.160 | 0.278 | 0.144 | 0.165 | -0.80 | 0.02 | -0.08 | -0.02 |
| 2018 | 2009-2017 | 0.152 | 0.279 | 0.145 | 0.152 | -0.75 | 0.02 | -0.08 | -0.02 |
| 2019 | 2009-2018 | 0.136 | 0.267 | 0.132 | 0.157 | -0.75 | 0.04 | -0.07 | -0.01 |
| 2020 | 2009-2019 | 0.166 | 0.251 | 0.120 | 0.127 | -0.75 | 0.04 | -0.05 | -0.01 |
| 2021 | 2009-2020 | 0.164 | 0.234 | 0.118 | 0.133 | -0.77 | 0.02 | -0.03 | -0.03 |
| 2022 | 2009-2021 | 0.174 | 0.221 | 0.128 | 0.124 | -0.84 | 0.01 | -0.04 | -0.04 |
| 2023 | 2009-2022 | 0.173 | 0.206 | 0.122 | 0.134 | -0.85 | 0.01 | -0.04 | -0.02 |
| 2024 | 2009-2023 | 0.168 | 0.210 | 0.126 | 0.126 | -0.84 | -0.01 | -0.02 | -0.01 |
| 2025 | 2009-2024 | 0.166 | 0.215 | 0.133 | 0.135 | -0.80 | 0.00 | -0.01 | 0.01 |

## Limits

- xFP and the garbage-time flag come from nflverse models trained on many seasons, including later ones (PROJECT_SPEC 6.3): an old week's xFP knows a little about the future. P2 re-estimates xFP walk-forward.
- Stat corrections are in the cache's latest version (docs/assumptions.md section 12).
- The outcome counts games played: a player who gets hurt is graded on the games he played, and one with fewer than 3 games left is not graded at all.
- One variant for all positions per season; r(g) has one noise size per position.
- Validation and test seasons share the same league-wide trends; the season-block bootstrap treats seasons as independent.
