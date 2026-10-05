# Start/sit odds: walk-forward backtest

LOCAL ONLY feature (it reads FantasyPros weekly ranks, which may not be republished); this page holds only aggregate numbers. Method and limits: docs/start_sit.md.

Pin `rank_dist-61ea917e5a85b44a`: distributions fit on 2020-2025 (35,925 ranked player-weeks, 26.9% without a stat line), smoothing c = 0.2. Test seasons 2022-2025, each fit on the seasons before it.

## Brier score and log loss (lower is better)

| season | pairs of | pairs | Brier model | Brier rank rate | Brier coin | log loss model | log loss rank rate | log loss coin |
|---|---|---|---|---|---|---|---|---|
| 2022 | flex | 24,679 | 0.2447 | 0.2452 | 0.2472 | 0.6880 | 0.6892 | 0.6931 |
| 2022 | same | 46,163 | 0.2296 | 0.2346 | 0.2468 | 0.6579 | 0.6686 | 0.6931 |
| 2023 | flex | 24,308 | 0.2446 | 0.2460 | 0.2479 | 0.6864 | 0.6892 | 0.6931 |
| 2023 | same | 46,450 | 0.2295 | 0.2346 | 0.2477 | 0.6558 | 0.6669 | 0.6931 |
| 2024 | flex | 21,075 | 0.2450 | 0.2461 | 0.2477 | 0.6877 | 0.6899 | 0.6931 |
| 2024 | same | 41,143 | 0.2308 | 0.2351 | 0.2471 | 0.6599 | 0.6689 | 0.6931 |
| 2025 | flex | 22,858 | 0.2463 | 0.2469 | 0.2486 | 0.6885 | 0.6898 | 0.6931 |
| 2025 | same | 45,877 | 0.2325 | 0.2364 | 0.2476 | 0.6622 | 0.6705 | 0.6931 |
| all | same | 179,633 | 0.2306 | 0.2352 | 0.2473 | 0.6589 | 0.6687 | 0.6931 |
| all | flex | 92,920 | 0.2451 | 0.2460 | 0.2478 | 0.6876 | 0.6895 | 0.6931 |
| all | all | 272,553 | 0.2355 | 0.2389 | 0.2475 | 0.6687 | 0.6758 | 0.6931 |

## Calibration (the favourite's predicted chance vs how often it won)

| predicted | pairs | mean predicted | favourite won |
|---|---|---|---|
| 50-55% | 110,855 | 52.5% | 53.1% |
| 55-60% | 80,067 | 57.1% | 57.9% |
| 60-65% | 39,477 | 62.2% | 63.8% |
| 65-70% | 22,963 | 67.4% | 68.4% |
| 70-75% | 11,700 | 72.1% | 71.3% |
| 75-80% | 4,783 | 77.2% | 75.8% |
| 80-85% | 2,284 | 82.0% | 79.3% |
| 85%+ | 424 | 86.6% | 78.8% |

## How often the higher-ranked player outscored the other, 2020-2025

| ranks apart | pairs | all | QB | RB | WR | TE |
|---|---|---|---|---|---|---|
| 1 | 14,655 | 51.5% | 52.9% | 51.4% | 50.5% | 52.8% |
| 2-3 | 28,598 | 53.0% | 54.6% | 53.4% | 51.2% | 54.8% |
| 4-6 | 40,506 | 55.5% | 58.4% | 55.6% | 53.7% | 57.1% |
| 7-12 | 72,622 | 59.3% | 63.6% | 60.3% | 56.1% | 62.4% |
| 13-24 | 111,305 | 66.5% | 74.3% | 68.6% | 61.9% | 72.2% |

## Teammates (the independence assumption)

| teammates | pairs | favourite predicted | favourite won | Brier model |
|---|---|---|---|---|
| no | 266,189 | 58.2% | 58.8% | 0.2355 |
| yes | 6,364 | 56.4% | 57.8% | 0.2387 |

## Folds (smoothing chosen on each fold's last training season)

| test season | trained on | c | higher rank won (same position) | higher mean won (FLEX) |
|---|---|---|---|---|
| 2022 | 2020-2021 | 0.3 | 59.7% | 53.9% |
| 2023 | 2020-2022 | 0.3 | 60.2% | 54.0% |
| 2024 | 2020-2023 | 0.3 | 60.5% | 53.9% |
| 2025 | 2020-2024 | 0.05 | 60.6% | 53.9% |
