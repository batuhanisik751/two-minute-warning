# Model card: Regression Watch (`regression_watch`)

Source: `config/production_models.yaml`
Pin key `regression_watch`, pin id (model_version) `zero_flat_all-9d98d6f251ee054c`,
`model: params`, approved 2026-10-01, scoring season 2026; xFP source `own`, own walk-forward
xFP fold 2026, version `own_xfp-2026-8577dfffe4683678`, trained on 2006-2025. Every number
below is copied from the file named on the `Source:` line above it (`tests/test_model_cards.py`
checks this).

## Purpose and intended use

Every Tuesday, show which QBs, RBs, WRs and TEs have scored far more (or less) than their
opportunity explains, with a rest-of-season projection in points per game and two tags:
**Sell-high** (efficiency likely to fall back) and **Buy-low** (likely to rise). It helps a
manager price trades and decide whom to stop trusting.

**Not intended for:** a weekly start/sit projection (it is a rest-of-season average); players
with very few games; the Legit tag (tested, not shown: it predicted nothing); betting.

## Inputs and point-in-time rules

Source: `reports/regression_watch/backtest.md`, "How the backtest works"
- **Universe at each as-of** (the official Tuesday after the week, season to date only): QBs,
  RBs, WRs and TEs with at least 3 games whose PPG or xFP/game ranks inside teams x (starting
  slots the position can fill, FLEX included) x the pool multiplier.
- **Projection** = recency-weighted xFP/game + r(g) x FPOE/game, where r(g) is the stability
  study's shrinkage factor for his position after his g games, estimated only on seasons
  2009 .. S-1 for season S.
- **Tags:** Sell-high = FPOE/game in the top decile of his position's universe AND the
  projection at least X points/game below his PPG; Buy-low the mirror.

Source: `docs/regression_watch.md`, "Our own expected points, without hindsight (step H6-b; live since H6-b2)"
- **xFP (expected fantasy points)** per play from the own walk-forward models: a play of season
  S gets its expectations from models trained on 2006 .. S-1 only (the 2026 fold is the live
  model). Inputs are the opportunity and the situation before the snap; never an nflfastR model
  column, and not the garbage-time flag.
- The live list of 2026 week 3, published before the switch, stays as it was (ffopportunity's
  xFP); the lists from week 4 on use the own xFP.

## Model and training

Source: `docs/deploy.md`, "Regression Watch's approved parameters (step D4a)"
- **No fitted model for the list:** one frozen parameters file (the variant, the shrinkage
  table estimated on the seasons before the pinned one, the Sell-high / Buy-low X, the xFP
  source, and the record of the last backtest season). Its version is a hash of its content.
- **The own xFP:** eight model files `<xfp version>/<component>.joblib`, each with its sha256,
  checked before the file is opened; a wrong hash, a missing component or another fold stops
  the run (no fallback to ffopportunity).

Source: `reports/regression_watch/backtest.md`, "How the backtest works"
- **Walk-forward:** test seasons 2011-2025. For test season S the variant with the lowest MAE on
  the validation seasons 2010 .. S-1 (headline weeks) is used; the test season is never looked
  at. 24 variants (shrink toward 0 or the position's average FPOE; recency half-life none / 8 /
  4 / 2 games; three garbage-time treatments). X (0 to 6 by 0.5) is chosen per test season on
  the validation seasons.

Source: `reports/regression_watch/own_xfp.md`, "The own models"
- **Own xFP components:** completion, yac, pass_td, interception, rush_yards, rush_td (each a
  LightGBM or a spline GLM, whichever did better on the last training season), pass_2pt and
  rush_2pt (rates); one fold per test season 2007-2026, trained on 2006 .. season - 1 only.

Source: `config/production_models.yaml`
- **What is pinned:** `artifacts/production_models/regression_watch/zero_flat_all-9d98d6f251ee054c.json`
  (sha256 `89a00f2413f73f15ec6843121ea21eb1934aa2e4bf3ae93a34b204766ccd5bf1`), the eight own xFP
  component files under `own_xfp-2026-8577dfffe4683678/` (each sha256-checked), and the frozen
  backtest lists of 2011-2025: `predictions.parquet` (10826 rows), `outcomes.parquet` (10826
  rows), `model_versions.parquet` (15 rows). Approved 2026-10-01.

## Evaluation

Source: `reports/regression_watch/backtest.md`, "P1 acceptance (MET)", "Headline: mean absolute error (points per game), as-of weeks 4, 6, 8, 10"
Walk-forward test seasons 2011-2025, headline as-of weeks 4, 6, 8, 10, outcome = points per
game over the rest of the regular season. Baselines: season-to-date PPG and the average of the
last 3 games. Intervals: 95%, 2,000 resamples of whole seasons, paired. **P1 acceptance
(MET):** the projection's MAE is 3.12 points per game against 3.41 for season-to-date PPG, a
difference of -0.28 (-0.36 to -0.21), which excludes 0.

| position | rows | projection | season-to-date PPG | last-3 PPG | projection - PPG | projection - last 3 |
|---|---|---|---|---|---|---|
| QB | 1184 | 3.12 (2.87 to 3.38) | 3.51 (3.22 to 3.80) | 4.17 (3.89 to 4.45) | -0.38 (-0.62 to -0.16) | -1.04 (-1.26 to -0.80) |
| RB | 3164 | 3.40 (3.30 to 3.50) | 3.58 (3.46 to 3.72) | 4.20 (4.09 to 4.32) | -0.18 (-0.27 to -0.10) | -0.80 (-0.93 to -0.68) |
| WR | 3414 | 3.34 (3.22 to 3.46) | 3.65 (3.53 to 3.77) | 4.32 (4.21 to 4.43) | -0.31 (-0.40 to -0.21) | -0.98 (-1.10 to -0.86) |
| TE | 2251 | 2.41 (2.29 to 2.56) | 2.75 (2.61 to 2.89) | 3.23 (3.14 to 3.32) | -0.34 (-0.48 to -0.21) | -0.82 (-0.95 to -0.69) |
| **pooled** | 10013 | 3.12 (3.04 to 3.21) | 3.41 (3.33 to 3.49) | 4.02 (3.96 to 4.08) | -0.28 (-0.36 to -0.21) | -0.89 (-0.97 to -0.81) |

Source: `reports/regression_watch/backtest.md`, "Rank correlation (Spearman), headline weeks"
Rank correlation (higher is better): the projection orders players no better than
season-to-date PPG at QB, RB and WR; pooled, the gap's interval includes 0.

| position | lists | projection | season-to-date PPG | last-3 PPG | projection - PPG | projection - last 3 |
|---|---|---|---|---|---|---|
| QB | 60 | 0.360 (0.279 to 0.438) | 0.379 (0.281 to 0.466) | 0.312 (0.224 to 0.391) | -0.019 (-0.071 to 0.037) | 0.048 (-0.023 to 0.109) |
| RB | 60 | 0.605 (0.562 to 0.643) | 0.602 (0.555 to 0.645) | 0.547 (0.504 to 0.586) | 0.004 (-0.013 to 0.023) | 0.058 (0.037 to 0.079) |
| WR | 60 | 0.498 (0.449 to 0.548) | 0.490 (0.443 to 0.538) | 0.392 (0.359 to 0.429) | 0.007 (-0.019 to 0.032) | 0.105 (0.074 to 0.136) |
| TE | 60 | 0.651 (0.616 to 0.680) | 0.601 (0.559 to 0.641) | 0.520 (0.480 to 0.562) | 0.050 (0.025 to 0.078) | 0.131 (0.101 to 0.161) |
| **pooled** | 240 | 0.528 (0.504 to 0.553) | 0.518 (0.485 to 0.550) | 0.443 (0.415 to 0.470) | 0.011 (-0.008 to 0.030) | 0.086 (0.071 to 0.099) |

Source: `reports/regression_watch/backtest.md`, "Tags (test seasons, headline weeks)"
Tags (hit: Sell-high = rest-of-season PPG below his PPG at the as-of; Buy-low = above; base
rate = every universe player; reference = top- or bottom-decile FPOE, any projection). Legit
is shown for the record: its interval does NOT clear the base rate, which is why it is not in
the product.

| tag | position | tags graded | per as-of | hit rate | base rate | reference | not graded (< 3 games left) |
|---|---|---|---|---|---|---|---|
| Sell-high | pooled | 262 | 4.4 | 93.9% (89.6% to 97.6%) | 60.5% (58.3% to 62.4%) | 81.6% (77.3% to 85.0%) | 16 |
| Buy-low | pooled | 421 | 7.0 | 58.7% (54.9% to 63.6%) | 39.4% (37.5% to 41.6%) | 59.8% (57.2% to 62.6%) | 56 |
| Legit | pooled | 3132 | 52.2 | 59.0% (57.3% to 60.8%) | 61.1% (59.6% to 62.6%) | 68.2% (64.9% to 71.4%) | 196 |

Source: `reports/regression_watch/own_xfp.md`, "D3 projection backtest (test seasons from 2011, headline weeks 4/6/8/10)"
The own walk-forward xFP against ffopportunity's (which saw later seasons), on the player-weeks
both graded: the projection's MAE is lower with the own xFP; the tag hit rates do not differ
beyond noise.

| position | player-weeks both | MAE model ffo / own | MAE season-to-date PPG | MAE own - ffo | model - PPG (ffo) | model - PPG (own) |
|---|---|---|---|---|---|---|
| all | 9,870 | 3.213 / 3.123 | 3.406 / 3.407 | -0.094 (-0.129 to -0.062) | -0.193 (-0.252 to -0.130) | -0.284 (-0.357 to -0.208) |

| tag | ffopportunity | own | own - ffo |
|---|---|---|---|
| sell-high | 237: 0.924 (+0.876 to +0.964) | 262: 0.939 (+0.896 to +0.976) | +0.015 (-0.019 to +0.046) |
| buy-low | 447: 0.617 (+0.557 to +0.672) | 421: 0.587 (+0.549 to +0.636) | -0.031 (-0.069 to +0.008) |

## Calibration

Source: `reports/regression_watch/backtest.md`, "By as-of week (all test seasons, pooled positions)"
The projection is a number of points per game, not a probability, so there is no probability
calibration. The closest check is its mean absolute error at every as-of week 4-14 (pooled
positions): it stays below season-to-date PPG and the last 3 games at every week, and the gap
to PPG narrows late in the season.

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


## Known limits and failure cases

Source: `reports/regression_watch/backtest.md`, "Limits"
- **Garbage-time leak:** the garbage-time flag still comes from nflfastR's win probability,
  trained on many seasons, including later ones: a mild, known leak.
- **Injuries:** the outcome counts games played; a player with fewer than 3 games left is not
  graded at all.
- **One variant for all positions per season;** r(g) has one noise size per position.
- **Seasons are not independent:** validation and test seasons share league-wide trends; the
  season-block bootstrap treats seasons as independent.
- **Stat corrections** are in the cache's latest version.

Source: `reports/regression_watch/own_xfp.md`, "Notes"
- **The own xFP lags trends** (completion rates rising, interception rates falling): it learns
  the league's level from earlier seasons.
- **Receivers 2006-2008:** the targets of incomplete passes are missing upstream, so receiver
  xFP of those seasons is unusable with either source; D2 and D3 start in 2009.

Source: `reports/regression_watch/backtest.md`, "Rank correlation (Spearman), headline weeks"
- **Ordering:** pooled, the projection does not rank players better than season-to-date PPG
  (0.011 (-0.008 to 0.030)); its value is in the size of the error and in the tags.

Source: `docs/timemachine.md`, "What is not checked (and why)"
- **Live week 3 of 2026** used the previous xFP source (ffopportunity), so a recomputation with
  today's pin would differ by design; it is kept append-only. **Linux not recomputed:** the
  scheduled job serves the frozen lists and never rebuilds them.

## Owner decisions that shaped it

Source: `docs/progress.md`
- 2026-09-29 (D1, technical call by Claude, reversible): the studies and backtests start in 2009
  for every position, because receiver xFP is unusable in 2006-2008.
- 2026-09-30 (owner): remove the Legit tag from the product; show the stability study's real
  numbers on /methodology.
- 2026-10-01 (owner, after the H6-b report): switch Regression Watch to the own walk-forward xFP
  now (live lists from the next Tuesday; earlier live lists stay frozen); step H6-b2.

Source: `config/production_models.yaml`
- Pin approved 2026-10-01.

## Reproducibility

Source: `docs/timemachine.md`, "Results (owner's Mac, 2026-10-02: commit c8921e9 + I3a; 48d3ada since changed only web/)"
- `uv run twm model check regression_watch`: the parameters file, the eight xFP model files and
  the record (sha256 first), then the frozen lists against the committed
  `reports/regression_watch/backtest.csv`.
- `uv run twm timemachine verify --module regression_watch --sample 99`: `frozen.build_snapshot`
  recomputes each season's variant, X, shrinkage, lists, tags and reasons from the warehouse
  (own xFP folds from a scratch copy). Last run (2026-10-02): 2011-2025, 255 lists, 10,826 list
  rows + 15 versions, max abs diff 0.0, 0 mismatches.

## Ethical notes

Source: `reports/regression_watch/backtest.md`, "Tags (test seasons, headline weeks)"
Players are described by public statistics only. "Sell-high" is a statement about fantasy
scoring efficiency that is likely to fall back, not a judgement of a player's skill or effort;
the tags are hit rates of the past, not certainties (a Buy-low tag came true 58.7% of the time
in the backtest).
