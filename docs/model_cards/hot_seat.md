# Model card: Hot-Seat Meter (`hot_seat`)

Source: `config/production_models.yaml`
Pin key `hot_seat`, pin id (model_version) `logit-0ede038915531ea3`, `model: logit`, approved
2026-10-02, scoring season 2026. Every number below is copied from the file named on the
`Source:` line above it (`tests/test_model_cards.py` checks this).

## Purpose and intended use

Source: `docs/hot_seat.md`, "Production (step H4a)"
Every week of the season, estimate for every current NFL head coach the probability of a
positive departure (fired in or after the season, or a mutual parting) announced by 30 days
after the team's final game, rank the coaches, and show the top 3 **drivers** of each estimate.
An end-of-season snapshot is taken after each team's last regular-season game. It is a
public-data description of how situations like this one have ended in the past.

**Not intended for:** predicting or advocating any real person's dismissal; betting markets;
judging a coach's ability; interim coaches (scored but flagged: the model was not trained on
interims).

## Inputs and point-in-time rules

Source: `docs/hot_seat.md`, "Point in time"
- Every input is read through `twm.asof.AsOfView` (or once per season with `available_at` and
  filtered per as-of; the tests check both paths give the same frame): played games, coach
  games, neutral run/pass plays, the schedule, QB roster rows, first-round picks, teams. The
  coach, play and grade inputs are joined to the **visible played games** only.
- **Decision grades** (the Decision Report Card's stored grades, never regraded) are an input:
  the pinned frozen history for 2006-2025 (sha256 checked) and the season's own graded file for
  2026; a game's grades count only when its game row is visible at the as-of.
- `is_interim` also reads the owner's departure file (hindsight).

Source: `reports/hot_seat/backtest.md`, "Labels"
- **Labels** (`--labels verified`): 208 departures joined to feature rows, 74 with a blank date
  imputed as the coach's last listed game day; 12,402 labelled rows, of which 334 interim (never
  trained on, reported apart) and 595 censored (a non-firing departure in the window: y = 0).

## Model and training

Source: `docs/hot_seat.md`, "Models and backtest (step H3b)"
- **Model:** the Waiver Radar's penalized logistic regression on the season label, L2 only;
  C (grid 0.01, 0.1, 1) chosen inside each fit by an inner walk-forward on the training rows
  alone (the last 4 training seasons that have an earlier one). 18 features; missing values:
  median + missing indicator. The model's own probability is primary; the isotonic calibrator
  is not kept.
- **Walk-forward:** test seasons 2006-2025, each trained on every earlier season from 2002.
  Challengers: a discrete-time hazard model and LightGBM (used only if it beat both; it did
  not); baselines: win% to date and wins minus market-expected wins alone.

Source: `docs/hot_seat.md`, "Production (step H4a)"
- **The 2026 fold:** pinned 2026-10-02, C 0.1 (inner seasons 2022-2025), 12,068 training rows
  (2,010 positive rows). `twm hotseat pin` refuses unless every probability equals the
  verified run's; the live path only loads the file (sha256 before the pickle is opened).
- **Drivers:** coefficient x standardized value per feature (value and missing-indicator terms
  summed), the 3 largest by absolute size, signed.

Source: `config/production_models.yaml`
- **What is pinned:** `artifacts/production_models/hot_seat/logit-0ede038915531ea3.joblib`
  (sha256 `fbaa29c406e727286375fd009ef5f76d0bc4f5e09c836efacc80d6c0380dc246`) and the frozen
  backtest of 2006-2025: `predictions.parquet` (10361 rows), `outcomes.parquet`,
  `model_versions.parquet`, each read only after its sha256 matches. Approved 2026-10-02.

## Evaluation

Source: `reports/hot_seat/backtest.md`, "Walk-forward results (test seasons 2006-2025, interims excluded)", "Firings per season (coach-seasons; interims apart)"
Walk-forward test seasons 2006-2025 (interims excluded): 126 positive departures, 2-9 per
season. 95% intervals from resampling whole seasons (2,000 resamples). Top-5 hit rate: of the
positives at that as-of, the share in their season's top 5 by predicted risk. The pinned model
is `logit`; `hazard` is the challenger it ties; the baselines are win% to date and wins minus
market-expected wins alone.

| model | as-of | ROC-AUC | PR-AUC | Brier | top-5 hit rate | rows | positives | seasons |
|---|---|---|---|---|---|---|---|---|
| logit | all | 0.788 (0.745 to 0.825) | 0.435 (0.350 to 0.530) | 0.1202 (0.1081 to 0.1322) |  | 10059 | 1744 | 20 |
| logit | week_12 | 0.805 (0.755 to 0.853) | 0.491 (0.403 to 0.587) | 0.1132 (0.0993 to 0.1269) | 0.490 (0.425 to 0.559) | 616 | 104 | 20 |
| logit | end_of_season | 0.836 (0.789 to 0.885) | 0.474 (0.371 to 0.615) | 0.0964 (0.0820 to 0.1106) | 0.523 (0.437 to 0.621) | 597 | 86 | 20 |
| hazard | all | 0.782 (0.746 to 0.817) | 0.438 (0.353 to 0.528) | 0.1224 (0.1083 to 0.1367) |  | 10059 | 1744 | 20 |
| hazard | week_12 | 0.805 (0.759 to 0.848) | 0.500 (0.419 to 0.590) | 0.1137 (0.0977 to 0.1299) | 0.423 (0.351 to 0.506) | 616 | 104 | 20 |
| hazard | end_of_season | 0.849 (0.805 to 0.894) | 0.495 (0.382 to 0.636) | 0.0950 (0.0783 to 0.1112) | 0.512 (0.434 to 0.602) | 597 | 86 | 20 |
| lgbm | all | 0.754 (0.702 to 0.802) | 0.417 (0.346 to 0.504) | 0.1245 (0.1117 to 0.1369) |  | 10059 | 1744 | 20 |
| lgbm | week_12 | 0.763 (0.702 to 0.823) | 0.461 (0.371 to 0.559) | 0.1191 (0.1040 to 0.1341) | 0.413 (0.333 to 0.495) | 616 | 104 | 20 |
| lgbm | end_of_season | 0.811 (0.751 to 0.872) | 0.505 (0.397 to 0.633) | 0.0969 (0.0824 to 0.1116) | 0.523 (0.427 to 0.625) | 597 | 86 | 20 |
| base_win_pct | all | 0.732 (0.697 to 0.765) | 0.326 (0.273 to 0.383) | 0.1314 (0.1172 to 0.1448) |  | 10059 | 1744 | 20 |
| base_win_pct | week_12 | 0.777 (0.735 to 0.817) | 0.390 (0.325 to 0.483) | 0.1241 (0.1086 to 0.1386) | 0.385 (0.314 to 0.466) | 616 | 104 | 20 |
| base_win_pct | end_of_season | 0.814 (0.776 to 0.856) | 0.417 (0.343 to 0.521) | 0.1062 (0.0904 to 0.1222) | 0.465 (0.394 to 0.548) | 597 | 86 | 20 |
| base_wins_vs_expected | all | 0.713 (0.675 to 0.750) | 0.309 (0.264 to 0.364) | 0.1338 (0.1178 to 0.1485) |  | 10059 | 1744 | 20 |
| base_wins_vs_expected | week_12 | 0.754 (0.707 to 0.802) | 0.355 (0.302 to 0.431) | 0.1260 (0.1088 to 0.1434) | 0.365 (0.291 to 0.455) | 616 | 104 | 20 |
| base_wins_vs_expected | end_of_season | 0.799 (0.757 to 0.844) | 0.348 (0.294 to 0.443) | 0.1073 (0.0905 to 0.1246) | 0.477 (0.368 to 0.608) | 597 | 86 | 20 |

Source: `reports/hot_seat/backtest.md`, "Paired differences (main run; same season resamples for both)"
Paired differences against the win% baseline and the hazard model (Brier: negative = the first
is better; ROC-AUC / PR-AUC: positive = the first is better):

| a - b | as-of | metric | difference (95%) |
|---|---|---|---|
| logit - base_win_pct | all | roc_auc | 0.0558 (0.0245 to 0.0867) |
| logit - base_win_pct | all | pr_auc | 0.1082 (0.0509 to 0.1710) |
| logit - base_win_pct | all | brier | -0.0112 (-0.0184 to -0.0044) |
| logit - base_wins_vs_expected | all | roc_auc | 0.0741 (0.0313 to 0.1134) |
| logit - base_wins_vs_expected | all | pr_auc | 0.1251 (0.0580 to 0.1919) |
| logit - base_wins_vs_expected | all | brier | -0.0136 (-0.0212 to -0.0056) |
| hazard - logit | all | roc_auc | -0.0052 (-0.0158 to 0.0068) |
| hazard - logit | all | pr_auc | 0.0033 (-0.0269 to 0.0343) |
| hazard - logit | all | brier | 0.0022 (-0.0013 to 0.0056) |

Source: `reports/hot_seat/backtest.md`, "Sensitivity runs"
Sensitivity: dropping the censored coaches, or counting `resigned_under_pressure` as a firing,
leaves the logit's numbers within their intervals.

| run | model | as-of | ROC-AUC | PR-AUC | Brier | rows | positives |
|---|---|---|---|---|---|---|---|
| censored_dropped | logit | all | 0.789 (0.746 to 0.825) | 0.437 (0.354 to 0.531) | 0.1219 (0.1099 to 0.1340) | 9833 | 1744 |
| censored_dropped | logit | end_of_season | 0.837 (0.790 to 0.886) | 0.481 (0.375 to 0.622) | 0.0977 (0.0829 to 0.1124) | 584 | 86 |
| rup_positive | logit | all | 0.788 (0.747 to 0.824) | 0.434 (0.348 to 0.530) | 0.1202 (0.1082 to 0.1322) | 10059 | 1744 |
| rup_positive | logit | end_of_season | 0.839 (0.793 to 0.888) | 0.477 (0.371 to 0.621) | 0.0961 (0.0817 to 0.1103) | 597 | 86 |

## Calibration

Source: `reports/hot_seat/backtest.md`, "Calibration"
The models' own probabilities are primary (an isotonic map fit on one validation season of
about 6 firings is thin, and it scores worse):

| model | probability | as-of | Brier (95%) |
|---|---|---|---|
| logit | own | all | 0.1202 (0.1081 to 0.1322) |
| logit | own | end_of_season | 0.0964 (0.0820 to 0.1106) |
| logit | isotonic | all | 0.1354 (0.1210 to 0.1493) |

Source: `reports/hot_seat/backtest.md`, "Reliability at season end (own probabilities, 10-point bins, empty bins left out)"
Reliability at season end (own probabilities): from 0.1 to 0.5 the observed rate runs above the
mean prediction (the model is somewhat low there: the 57 coach-seasons of 0.2-0.3 had 0.247
predicted and 0.298 observed); from 0.3 up each bin holds a few dozen coach-seasons or fewer, so
it is noisy.


| model | bin | rows | positives | mean predicted | observed |
|---|---|---|---|---|---|
| logit | 0.0-0.1 | 346 | 10 | 0.040 | 0.029 |
| logit | 0.1-0.2 | 122 | 21 | 0.140 | 0.172 |
| logit | 0.2-0.3 | 57 | 17 | 0.247 | 0.298 |
| logit | 0.3-0.4 | 32 | 16 | 0.354 | 0.500 |
| logit | 0.4-0.5 | 22 | 11 | 0.441 | 0.500 |
| logit | 0.5-0.6 | 7 | 3 | 0.552 | 0.429 |
| logit | 0.6-0.7 | 5 | 4 | 0.653 | 0.800 |
| logit | 0.7-0.8 | 5 | 3 | 0.731 | 0.600 |
| logit | 0.8-0.9 | 1 | 1 | 0.820 | 1.000 |

## Known limits and failure cases

Source: `docs/progress.md`
- **Labels were bulk-accepted, not checked row by row:** the owner accepted every
  research-prefilled departure type and date as verified in bulk (2026-10-01); any write-up or
  page must describe the labels as cited public-source research accepted by the owner.

Source: `reports/hot_seat/backtest.md`, "Labels", "Calibration"
- **Imputed dates:** 74 of the 208 departures had a blank date, imputed as the coach's last
  listed game day.
- **Thin calibration data:** the isotonic map would be fit on one validation season of about 6
  firings; the model's own probabilities are used instead.

Source: `docs/hot_seat.md`, "Point in time", "Models and backtest (step H3b)"
- **Hindsight in `is_interim`:** it reads the owner's departure file; interims are never trained
  on and are flagged on the page.
- **Decision-grade feature gaps:** the grades start in 2006, so the 2006 fold has no graded
  training season and cannot use that feature (median + missing indicator elsewhere).
- **Small event counts:** a few positive departures per season; the hazard model ties the
  logistic regression within noise.

Source: `docs/timemachine.md`, "What is not checked (and why)"
- **Linux not recomputed:** the scheduled job serves the frozen backtest; it never recomputes
  history.

## Owner decisions that shaped it

Source: `docs/progress.md`
- 2026-10-01 (owner, spec decision point 4 and the H3 label anchor): "resigned under pressure"
  is NOT a positive departure (censored like other non-firings), with a sensitivity run that
  counts it; the end-of-season snapshot is taken after each team's last regular-season game
  (`hot_seat.end_of_season_anchor: last_game_end`).
- 2026-10-01 (owner, H2): every research-prefilled row of `data/manual/coach_departures.csv`
  accepted as verified in bulk (not checked row by row); the one row the research left blank
  (2010 TEN Fisher) filled by the reviewer from his Wikipedia page.
- 2026-10-01 (owner, H4): the live Hot-Seat Meter uses the L2 logistic regression (ties the
  hazard model within noise; simpler; better on the weekly rows: Brier .120 vs .122, week-12
  top-5 .49 vs .42).

Source: `config/production_models.yaml`
- Pin approved 2026-10-02.

## Reproducibility

Source: `docs/timemachine.md`, "Results (owner's Mac, 2026-10-02: commit c8921e9 + I3a; 48d3ada since changed only web/)"
- `uv run twm model check hot_seat`: the pin, the model file and the snapshot, which must
  reproduce `reports/hot_seat/backtest_metrics.csv` (main, logit, own probability) and
  `firings_per_season.csv`.
- `uv run twm timemachine verify --module hot_seat --sample 99`: features rebuilt, the verified
  targets, each season's fold refit. Last run (2026-10-02): 2006-2025, 325 lists, 10,361 rows,
  max abs diff 0.0, 0 mismatches.

## Ethical notes

Source: `docs/hot_seat.md`, "Production (step H4a)"
This module is about **real people's jobs**. The Meter describes how often coaches in similar
public situations (results against expectations, tenure, context) left their jobs in the past;
it does not know what an owner thinks, and it must never be presented as a prediction that a
named person will or should be fired. Ranking coaches by risk can amplify media pressure on a
person: each estimate comes with its top 3 drivers so it can be questioned, and interim
coaches are flagged. The labels are public-source research accepted by
the owner in bulk, not individually checked; an error in them is an error about a real
person's career, and a correction should be welcomed and cited. Fourth-down decision grades
are one input among many and are the project's own model estimates, not verdicts.
