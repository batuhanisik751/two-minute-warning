# Regression Watch: own walk-forward xFP vs ffopportunity (step H6-b)

Warehouse built 2026-10-01 17:01:25. Own models: one fold per test season 2007-2026, trained on 2006 .. season - 1 only (the 2026 fold is the live model). Intervals: 95%, 2000 season-block resamples (the same seasons drawn for both sources). Command: `uv run twm regression own-xfp`.

## Summary

- **Per play** (test seasons 2007-2026): ffopportunity is closer for completion, pass_td, interception, rush_yards, rush_td; the own model is closer for rush_2pt (season-block intervals of the pooled difference exclude 0). ffopportunity's models saw later seasons, so this flatters it.
- **Per player-game**: own and ffopportunity xFP correlate 0.990; own is -0.36 xFP per player-game on average.
- **Stability (D2)**: FPOE/game split-half r (odd/even games), ffopportunity -> own: QB 0.10 -> 0.28 (+0.18 (+0.14 to +0.23)); RB 0.16 -> 0.23 (+0.06 (+0.04 to +0.09)); WR 0.11 -> 0.20 (+0.09 (+0.07 to +0.11)); TE 0.12 -> 0.17 (+0.06 (+0.04 to +0.08)); with the own xFP more of FPOE repeats at every position, also within seasons, so D2's shrinkage factors (and D3's projections) give FPOE more weight.
- **Leakage (6.3), D3 backtest**: projection MAE own - ffopportunity -0.094 points/game (-0.129 to -0.062) on 9,870 player-weeks; sell-high hit rate own - ffopportunity +0.015 (-0.019 to +0.046); buy-low hit rate own - ffopportunity -0.031 (-0.069 to +0.008).
- **Recommendation: switch Regression Watch to the own walk-forward xFP** (rule: D3's pooled projection MAE not worse and neither tag's hit rate worse, by the season-block intervals). Nothing switches here: the production pin, the published lists and the site are unchanged; switching is the owner's decision (then rerun D2/D3 and pin through `twm regression pin`).

## The own models

| component | replaces | family (number of folds) | median trees (LightGBM) | training plays (last fold) |
|---|---|---|---|---|
| completion | pass_completion_exp | glm 1, lgbm 19 | 131 | 369,803 |
| yac | yards_after_catch_exp | glm 3, lgbm 17 | 78 | 231,755 |
| pass_td | pass_touchdown_exp | glm 3, lgbm 17 | 116 | 369,803 |
| interception | pass_interception_exp | glm 2, lgbm 18 | 84 | 369,803 |
| rush_yards | rush_yards_exp | glm 3, lgbm 17 | 81 | 282,317 |
| rush_td | rush_touchdown_exp | glm 1, lgbm 19 | 98 | 282,317 |
| pass_2pt | two_point_conv_exp | rate 20 | - | 1,377 |
| rush_2pt | two_point_conv_exp | rate 20 | - | 512 |

## 1. Per play: the same plays, own vs ffopportunity

| component | plays | metric | ffopportunity | own | own - ffo (95% interval) | mean actual / ffo / own |
|---|---|---|---|---|---|---|
| catch probability per pass | 355,771 | log loss | 0.5936 | 0.6020 | +0.0084 (+0.0070 to +0.0099) | 0.628 / 0.645 / 0.620 |
| catch probability per pass | 355,771 | brier | 0.2032 | 0.2069 | +0.0037 (+0.0031 to +0.0043) | 0.628 / 0.645 / 0.620 |
| yards after the catch (caught passes) | 223,524 | mae | 4.024 | 4.039 | +0.015 (-0.010 to +0.039) | 5.153 / 5.170 / 5.144 |
| yards after the catch (caught passes) | 223,524 | mse | 41.86 | 41.70 | -0.16 (-0.35 to +0.01) | 5.153 / 5.170 / 5.144 |
| touchdown probability per pass | 355,771 | log loss | 0.1098 | 0.1119 | +0.0020 (+0.0013 to +0.0028) | 0.044 / 0.046 / 0.043 |
| touchdown probability per pass | 355,771 | brier | 0.0316 | 0.0321 | +0.0005 (+0.0003 to +0.0006) | 0.044 / 0.046 / 0.043 |
| interception probability per pass | 355,771 | log loss | 0.1079 | 0.1095 | +0.0016 (+0.0005 to +0.0026) | 0.025 / 0.024 / 0.028 |
| interception probability per pass | 355,771 | brier | 0.0239 | 0.0241 | +0.0002 (+0.0001 to +0.0002) | 0.025 / 0.024 / 0.028 |
| yards per carry | 270,237 | mae | 3.676 | 3.718 | +0.042 (+0.026 to +0.057) | 4.441 / 4.365 / 4.388 |
| yards per carry | 270,237 | mse | 38.26 | 38.67 | +0.41 (+0.26 to +0.58) | 4.441 / 4.365 / 4.388 |
| touchdown probability per carry | 270,237 | log loss | 0.0830 | 0.0846 | +0.0016 (+0.0006 to +0.0028) | 0.033 / 0.033 / 0.032 |
| touchdown probability per carry | 270,237 | brier | 0.0221 | 0.0225 | +0.0004 (+0.0002 to +0.0006) | 0.033 / 0.033 / 0.032 |
| two-point try by pass: success probability | 1,357 | log loss | 0.6942 | 0.6879 | -0.0063 (-0.0150 to +0.0021) | 0.439 / 0.526 / 0.442 |
| two-point try by pass: success probability | 1,357 | brier | 0.2505 | 0.2474 | -0.0032 (-0.0075 to +0.0010) | 0.439 / 0.526 / 0.442 |
| two-point try by run: success probability | 506 | log loss | 0.7849 | 0.6944 | -0.0905 (-0.1350 to -0.0473) | 0.553 / 0.329 / 0.582 |
| two-point try by run: success probability | 506 | brier | 0.2945 | 0.2502 | -0.0443 (-0.0656 to -0.0239) | 0.553 / 0.329 / 0.582 |

Own minus ffopportunity by season (log loss for chances, squared error for yards; negative = own closer):

| season | completion | yac | pass_td | interception | rush_yards | rush_td | pass_2pt | rush_2pt |
|---|---|---|---|---|---|---|---|---|
| 2007 | +0.0161 | -1.09 | +0.0022 | -0.0012 | +0.17 | +0.0009 | +0.0380 | -0.4416 |
| 2008 | +0.0065 | -0.57 | +0.0012 | +0.0007 | +0.41 | +0.0007 | +0.0075 | +0.1873 |
| 2009 | +0.0107 | -0.68 | +0.0014 | -0.0010 | +0.21 | -0.0003 | -0.0026 | +0.0295 |
| 2010 | +0.0088 | +0.07 | +0.0009 | +0.0005 | +0.25 | -0.0001 | +0.0077 | -0.1790 |
| 2011 | +0.0046 | -0.59 | +0.0021 | -0.0004 | +0.08 | -0.0008 | +0.0024 | -0.0939 |
| 2012 | +0.0052 | -0.45 | +0.0007 | +0.0008 | +0.11 | -0.0005 | -0.0040 | -0.3248 |
| 2013 | +0.0047 | -0.72 | +0.0014 | +0.0003 | +0.21 | +0.0003 | -0.0063 | -0.1652 |
| 2014 | +0.0112 | -0.04 | +0.0044 | +0.0045 | +0.90 | +0.0047 | +0.0048 | +0.0684 |
| 2015 | +0.0117 | +0.25 | +0.0033 | +0.0051 | +0.76 | +0.0044 | +0.0097 | +0.0756 |
| 2016 | +0.0091 | +0.11 | +0.0032 | +0.0043 | +0.82 | +0.0051 | -0.0187 | -0.1554 |
| 2017 | +0.0106 | +0.31 | +0.0041 | +0.0050 | +0.77 | +0.0045 | -0.0090 | -0.0602 |
| 2018 | +0.0121 | +0.10 | +0.0046 | +0.0041 | +1.00 | +0.0040 | +0.0035 | -0.1511 |
| 2019 | +0.0109 | +0.50 | +0.0035 | +0.0043 | +0.94 | +0.0052 | -0.0018 | -0.0856 |
| 2020 | +0.0095 | +0.09 | +0.0043 | +0.0047 | +1.01 | +0.0063 | -0.0210 | -0.0521 |
| 2021 | +0.0045 | -0.30 | +0.0008 | -0.0007 | +0.07 | +0.0000 | -0.0091 | -0.0739 |
| 2022 | +0.0047 | +0.14 | -0.0002 | +0.0003 | +0.16 | -0.0004 | +0.0052 | -0.0187 |
| 2023 | +0.0070 | -0.32 | +0.0001 | -0.0008 | +0.10 | -0.0002 | +0.0144 | -0.1496 |
| 2024 | +0.0063 | -0.16 | -0.0005 | -0.0003 | +0.13 | -0.0005 | -0.0472 | -0.0537 |
| 2025 | +0.0053 | +0.14 | +0.0011 | -0.0003 | +0.06 | -0.0008 | -0.0290 | -0.0767 |
| 2026 | +0.0090 | -0.40 | +0.0021 | -0.0012 | -0.26 | +0.0001 | +0.0204 | +0.0651 |

Own minus ffopportunity by season window (95% season-block intervals):

| component | metric | 2007-2013 | 2014-2020 | 2021-2026 |
|---|---|---|---|---|
| completion | log loss | +0.0080 (+0.0055 to +0.0110) | +0.0107 (+0.0099 to +0.0115) | +0.0057 (+0.0049 to +0.0066) |
| yac | mse | -0.58 (-0.80 to -0.32) | +0.19 (+0.08 to +0.32) | -0.11 (-0.28 to +0.06) |
| pass_td | log loss | +0.0014 (+0.0010 to +0.0018) | +0.0039 (+0.0035 to +0.0043) | +0.0003 (-0.0002 to +0.0009) |
| interception | log loss | -0.0000 (-0.0006 to +0.0005) | +0.0046 (+0.0043 to +0.0048) | -0.0004 (-0.0007 to -0.0001) |
| rush_yards | mse | +0.20 (+0.14 to +0.28) | +0.89 (+0.81 to +0.95) | +0.09 (+0.03 to +0.13) |
| rush_td | log loss | +0.0000 (-0.0004 to +0.0004) | +0.0049 (+0.0044 to +0.0054) | -0.0004 (-0.0006 to -0.0001) |
| pass_2pt | log loss | +0.0052 (-0.0028 to +0.0159) | -0.0056 (-0.0142 to +0.0031) | -0.0141 (-0.0323 to +0.0058) |
| rush_2pt | log loss | -0.1559 (-0.2903 to -0.0209) | -0.0651 (-0.1156 to -0.0060) | -0.0781 (-0.1160 to -0.0376) |

## 2. Per player-game: own xFP vs ffopportunity's (point-in-time position)

| position | xFP | player-games | mean ffo | mean own | mean own - ffo | mean abs diff | correlation |
|---|---|---|---|---|---|---|---|
| QB | all plays | 11,909 | 14.27 | 13.21 | -1.06 | 1.43 | 0.979 |
| RB | all plays | 25,555 | 8.69 | 8.52 | -0.17 | 0.55 | 0.993 |
| TE | all plays | 18,820 | 6.18 | 5.79 | -0.39 | 0.55 | 0.991 |
| WR | all plays | 38,110 | 8.48 | 8.22 | -0.26 | 0.63 | 0.990 |
| all | all plays | 94,394 | 8.81 | 8.45 | -0.36 | 0.69 | 0.990 |
| QB | no garbage time | 11,909 | 12.08 | 11.17 | -0.90 | 1.21 | 0.983 |
| RB | no garbage time | 25,555 | 7.27 | 7.13 | -0.14 | 0.47 | 0.994 |
| TE | no garbage time | 18,820 | 5.25 | 4.90 | -0.35 | 0.48 | 0.992 |
| WR | no garbage time | 38,110 | 7.17 | 6.95 | -0.22 | 0.54 | 0.991 |
| all | no garbage time | 94,394 | 7.43 | 7.12 | -0.31 | 0.59 | 0.991 |

By season (xFP, all plays): correlation / mean own - ffo per player-game:

| season | QB | RB | WR | TE |
|---|---|---|---|---|
| 2007 | 0.964 / -0.99 | 0.989 / -0.03 | 0.986 / -0.24 | 0.987 / -0.41 |
| 2008 | 0.969 / -1.11 | 0.991 / +0.02 | 0.987 / -0.19 | 0.989 / -0.35 |
| 2009 | 0.977 / -0.85 | 0.991 / +0.02 | 0.987 / -0.18 | 0.989 / -0.40 |
| 2010 | 0.979 / -0.92 | 0.992 / +0.12 | 0.989 / -0.17 | 0.991 / -0.30 |
| 2011 | 0.976 / -1.12 | 0.992 / -0.01 | 0.988 / -0.16 | 0.990 / -0.44 |
| 2012 | 0.977 / -1.20 | 0.992 / +0.01 | 0.990 / -0.23 | 0.992 / -0.45 |
| 2013 | 0.977 / -1.19 | 0.993 / -0.05 | 0.991 / -0.20 | 0.990 / -0.42 |
| 2014 | 0.980 / -1.16 | 0.992 / -0.16 | 0.991 / -0.24 | 0.993 / -0.42 |
| 2015 | 0.981 / -0.77 | 0.993 / -0.06 | 0.991 / -0.10 | 0.992 / -0.30 |
| 2016 | 0.982 / -0.87 | 0.995 / -0.14 | 0.991 / -0.13 | 0.993 / -0.33 |
| 2017 | 0.978 / -0.57 | 0.994 / -0.01 | 0.990 / -0.01 | 0.991 / -0.20 |
| 2018 | 0.979 / -1.61 | 0.994 / -0.47 | 0.989 / -0.55 | 0.992 / -0.52 |
| 2019 | 0.981 / -1.24 | 0.995 / -0.37 | 0.991 / -0.38 | 0.992 / -0.46 |
| 2020 | 0.986 / -1.81 | 0.995 / -0.47 | 0.991 / -0.63 | 0.991 / -0.62 |
| 2021 | 0.989 / -1.43 | 0.995 / -0.40 | 0.993 / -0.52 | 0.992 / -0.53 |
| 2022 | 0.980 / -0.94 | 0.995 / -0.27 | 0.991 / -0.26 | 0.990 / -0.35 |
| 2023 | 0.981 / -0.60 | 0.995 / -0.25 | 0.990 / -0.13 | 0.992 / -0.25 |
| 2024 | 0.982 / -0.80 | 0.995 / -0.28 | 0.991 / -0.23 | 0.993 / -0.32 |
| 2025 | 0.984 / -0.91 | 0.994 / -0.30 | 0.992 / -0.28 | 0.990 / -0.38 |
| 2026 | 0.989 / -1.06 | 0.996 / -0.21 | 0.993 / -0.35 | 0.994 / -0.44 |

## 3. Leakage comparison (PROJECT_SPEC 6.3)

### D2 stability study (seasons 2009-2025, odd/even games; first/second half in the CSV)

Within seasons: each half's value minus its season's mean, so a season-wide offset of one source does not count as a stable player trait.

| metric | position | player-seasons | r ffopportunity | r own | own - ffo | own - ffo within seasons |
|---|---|---|---|---|---|---|
| xFP/game | QB | 607 | 0.764 (+0.707 to +0.809) | 0.718 (+0.637 to +0.776) | -0.047 (-0.071 to -0.030) | -0.051 (-0.074 to -0.034) |
| xFP/game | RB | 1,476 | 0.884 (+0.873 to +0.895) | 0.889 (+0.879 to +0.899) | +0.005 (+0.003 to +0.007) | +0.005 (+0.003 to +0.007) |
| xFP/game | WR | 2,240 | 0.851 (+0.834 to +0.867) | 0.847 (+0.831 to +0.862) | -0.004 (-0.006 to -0.002) | -0.004 (-0.006 to -0.002) |
| xFP/game | TE | 1,098 | 0.851 (+0.832 to +0.870) | 0.848 (+0.830 to +0.868) | -0.003 (-0.007 to +0.001) | -0.003 (-0.007 to +0.001) |
| FPOE/game | QB | 607 | 0.098 (-0.007 to +0.191) | 0.278 (+0.188 to +0.352) | +0.180 (+0.135 to +0.230) | +0.188 (+0.148 to +0.235) |
| FPOE/game | RB | 1,476 | 0.162 (+0.108 to +0.216) | 0.227 (+0.172 to +0.276) | +0.064 (+0.041 to +0.091) | +0.057 (+0.036 to +0.082) |
| FPOE/game | WR | 2,240 | 0.108 (+0.067 to +0.144) | 0.202 (+0.160 to +0.242) | +0.094 (+0.075 to +0.112) | +0.087 (+0.071 to +0.103) |
| FPOE/game | TE | 1,098 | 0.117 (+0.038 to +0.184) | 0.173 (+0.102 to +0.243) | +0.056 (+0.038 to +0.076) | +0.059 (+0.041 to +0.079) |
| xFP/game, no garbage | QB | 607 | 0.708 (+0.648 to +0.756) | 0.663 (+0.585 to +0.722) | -0.045 (-0.064 to -0.031) | -0.050 (-0.066 to -0.037) |
| xFP/game, no garbage | RB | 1,476 | 0.873 (+0.861 to +0.886) | 0.878 (+0.866 to +0.890) | +0.004 (+0.003 to +0.006) | +0.004 (+0.002 to +0.006) |
| xFP/game, no garbage | WR | 2,240 | 0.821 (+0.800 to +0.840) | 0.817 (+0.797 to +0.835) | -0.004 (-0.006 to -0.001) | -0.004 (-0.006 to -0.001) |
| xFP/game, no garbage | TE | 1,098 | 0.820 (+0.800 to +0.840) | 0.815 (+0.795 to +0.836) | -0.004 (-0.009 to -0.000) | -0.004 (-0.009 to +0.000) |
| FPOE/game, no garbage | QB | 607 | 0.088 (-0.008 to +0.171) | 0.257 (+0.171 to +0.336) | +0.168 (+0.124 to +0.220) | +0.174 (+0.133 to +0.221) |
| FPOE/game, no garbage | RB | 1,476 | 0.128 (+0.071 to +0.184) | 0.192 (+0.137 to +0.241) | +0.064 (+0.038 to +0.093) | +0.056 (+0.034 to +0.083) |
| FPOE/game, no garbage | WR | 2,240 | 0.077 (+0.040 to +0.112) | 0.169 (+0.130 to +0.206) | +0.092 (+0.075 to +0.109) | +0.086 (+0.071 to +0.101) |
| FPOE/game, no garbage | TE | 1,098 | 0.115 (+0.026 to +0.198) | 0.166 (+0.080 to +0.252) | +0.052 (+0.034 to +0.073) | +0.055 (+0.037 to +0.075) |
| TD rate over expected | QB | 603 | 0.003 (-0.161 to +0.173) | 0.041 (-0.097 to +0.181) | +0.038 (-0.026 to +0.095) | +0.040 (-0.025 to +0.100) |
| TD rate over expected | RB | 1,400 | 0.058 (-0.012 to +0.129) | 0.066 (-0.000 to +0.133) | +0.007 (-0.009 to +0.025) | +0.006 (-0.010 to +0.023) |
| TD rate over expected | WR | 2,050 | 0.027 (-0.008 to +0.059) | 0.044 (+0.009 to +0.079) | +0.018 (+0.010 to +0.026) | +0.016 (+0.009 to +0.024) |
| TD rate over expected | TE | 854 | 0.005 (-0.074 to +0.093) | 0.017 (-0.067 to +0.114) | +0.012 (-0.006 to +0.027) | +0.013 (-0.005 to +0.028) |
| catch rate over expected | RB | 857 | 0.086 (+0.024 to +0.147) | 0.114 (+0.048 to +0.182) | +0.028 (+0.003 to +0.054) | +0.030 (+0.004 to +0.057) |
| catch rate over expected | WR | 2,015 | 0.162 (+0.108 to +0.212) | 0.232 (+0.173 to +0.282) | +0.071 (+0.049 to +0.091) | +0.052 (+0.035 to +0.069) |
| catch rate over expected | TE | 849 | 0.160 (+0.098 to +0.217) | 0.190 (+0.127 to +0.246) | +0.030 (+0.010 to +0.050) | +0.019 (-0.003 to +0.041) |
| completion rate over expected | QB | 594 | 0.333 (+0.275 to +0.381) | 0.437 (+0.368 to +0.494) | +0.105 (+0.066 to +0.138) | +0.087 (+0.057 to +0.118) |
| YAC over expected per catch | RB | 660 | 0.026 (-0.052 to +0.106) | 0.055 (-0.025 to +0.134) | +0.029 (+0.005 to +0.053) | +0.028 (+0.004 to +0.051) |
| YAC over expected per catch | WR | 1,640 | 0.193 (+0.137 to +0.252) | 0.216 (+0.159 to +0.275) | +0.023 (+0.010 to +0.035) | +0.014 (+0.002 to +0.028) |
| YAC over expected per catch | TE | 668 | 0.207 (+0.143 to +0.266) | 0.203 (+0.127 to +0.272) | -0.005 (-0.025 to +0.017) | +0.005 (-0.015 to +0.025) |

D2's FPOE/game shrinkage factor after 8 games, r(8) (what D3 multiplies FPOE by), and the average FPOE/game it shrinks toward:

| position | r(8) ffo | r(8) own | mean FPOE ffo | mean FPOE own |
|---|---|---|---|---|
| QB | 0.119 | 0.322 | -0.78 | +0.51 |
| RB | 0.199 | 0.274 | +0.01 | +0.21 |
| WR | 0.134 | 0.244 | -0.01 | +0.27 |
| TE | 0.150 | 0.217 | +0.04 | +0.47 |

### D3 projection backtest (test seasons from 2011, headline weeks 4/6/8/10)

MAE in points per game over the rest of the season. MAE columns: each backtest on its own graded player-weeks; own - ffo: on the player-weeks both graded (count shown).

| position | player-weeks both | MAE model ffo / own | MAE season-to-date PPG | MAE own - ffo | model - PPG (ffo) | model - PPG (own) |
|---|---|---|---|---|---|---|
| QB | 1,144 | 3.383 / 3.123 | 3.497 / 3.507 | -0.282 (-0.431 to -0.141) | -0.114 (-0.315 to +0.093) | -0.384 (-0.623 to -0.156) |
| RB | 3,142 | 3.466 / 3.400 | 3.582 / 3.580 | -0.064 (-0.090 to -0.039) | -0.116 (-0.210 to -0.037) | -0.181 (-0.272 to -0.104) |
| WR | 3,364 | 3.396 / 3.339 | 3.644 / 3.647 | -0.066 (-0.102 to -0.032) | -0.249 (-0.329 to -0.167) | -0.308 (-0.404 to -0.208) |
| TE | 2,220 | 2.492 / 2.406 | 2.748 / 2.746 | -0.083 (-0.122 to -0.045) | -0.256 (-0.389 to -0.133) | -0.340 (-0.484 to -0.210) |
| all | 9,870 | 3.213 / 3.123 | 3.406 / 3.407 | -0.094 (-0.129 to -0.062) | -0.193 (-0.252 to -0.130) | -0.284 (-0.357 to -0.208) |

Tag hit rates (tagged player-weeks: hit rate; Legit is no longer shown in the product, step R1, and is kept here for completeness):

| tag | ffopportunity | own | own - ffo |
|---|---|---|---|
| sell-high | 237: 0.924 (+0.876 to +0.964) | 262: 0.939 (+0.896 to +0.976) | +0.015 (-0.019 to +0.046) |
| buy-low | 447: 0.617 (+0.557 to +0.672) | 421: 0.587 (+0.549 to +0.636) | -0.031 (-0.069 to +0.008) |
| legit | 3,195: 0.606 (+0.588 to +0.624) | 3,132: 0.590 (+0.573 to +0.608) | -0.016 (-0.021 to -0.011) |

The variant chosen on earlier seasons is the same with both sources in 0 of 15 test seasons (per season in the CSV).

## Notes

- ffopportunity's models were trained on many seasons, including seasons after some of these plays (PROJECT_SPEC 6.3); the own models never saw their test season. The per-play comparison therefore favours ffopportunity; the table by season window shows where its advantage is largest.
- The own expectations learn the league's level from earlier seasons, so they lag trends (completion rates rising, interception rates falling): mean predictions vs actual are in the per-play table and by season in the CSV.
- Features: the opportunity and the situation before the snap only; no nflfastR model column (epa, wp, xpass, cpoe, xyac) and not the garbage-time flag (it is built from nflfastR's wp). Kneels (-1 yard) and aborted snaps (0) keep ffopportunity's fixed values.
- 2006-2008: the targets of incomplete passes are missing upstream (docs/regression_watch.md), so receiver xFP of those seasons is unusable with either source; D2 and D3 start in 2009.
- D3's universe ranks players by PPG or xFP, so the two backtests grade slightly different player-weeks; the own - ffopportunity MAE is on the player-weeks both graded.
- Report only: the production pin (config/production_models.yaml), the published lists and the site are unchanged; the own features are not in the feature registry (they would be registered if the owner switches).
