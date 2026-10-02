# Model card: Waiver Radar (`waiver_radar`)

Source: `config/production_models.yaml`
Pin key `waiver_radar`, pin id (model_version) `logit-5828a082c9e09093`, approved 2026-09-28,
scoring season 2026. Every number below is copied from the file named on the `Source:` line
above it (`tests/test_model_cards.py` checks this).

## Purpose and intended use

Source: `docs/waiver_radar.md`, "How to read the list"
Every Tuesday, rank the players who are probably on waivers at QB, RB, WR and TE by the chance
that they become a fantasy starter soon, so a manager in a 12-team PPR league knows whom to add
first. Each pick shows a **chance** (how often players the Radar rated like him became a
starter in the backtest, with a 90% range), a priority (must-add, speculative, watch) and the
reasons behind it.

**Not intended for:** start/sit decisions for players already on a roster; dynasty or
rest-of-season value; betting; leagues whose shape is not described in `config/league.yaml`
(the pool and the starter cut-off both derive from it).

## Inputs and point-in-time rules

Source: `docs/progress.md`
- **Pool:** players outside the top N at their position (QB 18, RB 36, WR 36, TE 18) by BOTH a
  preseason list and points per game to date. The preseason list is FantasyPros preseason ECR
  for 2020+, and prior-season PPG (rookies drafted in rounds 1-2 count as drafted) for
  2013-2019.
- **As-of:** the first Tuesday 14:00 UTC after the Eastern date of the week's first kickoff.

Source: `docs/waiver_radar.md`, "Point in time"
- Every input is read through the as-of view (`twm.asof.AsOfView`): rosters by their
  `available_at`, rankings the day after the scrape at 00:00 UTC, game stats from game end + 6 h,
  draft round from the draft. Today's-snapshot player fields are hidden; the pool uses
  `entry_year`, not `rookie_year` (17 rows carry a later season).
- Features: usage and opportunity windows (last game, last 3, season), xFP re-scored from
  ffopportunity, role change (who is out, who moved up), team and schedule context.
- **Label `y_hit`** (the production label): at least one starter finish in the player's window
  of his next games; `y_sustained` (at least two) is reported alongside.
- The leakage harness runs on the pool at several as-ofs and fails if rosters, stats or
  rankings are read from the raw warehouse.

## Model and training

Source: `docs/waiver_radar.md`, "The production model"
- **Model:** logistic regression (the C4 winner for `y_hit`; LightGBM tied and lost on the
  simpler-model rule), L1 penalty with C = 0.01, its probabilities calibrated (isotonic) on the
  season before the scoring season.
- **Training (2026 fold):** trained on 2013-2025 (96,220 player-weeks, 10,371 hits); settings
  tuned on S-1, calibrated on S-1's scores, refit on all seasons before S, never refit during
  the season. The version hashes model, label, features, settings, training seasons and the
  training-data hash: the same data always gives the same version.

Source: `docs/waiver_radar.md`, "Walk-forward: grading a model the honest way"
- **Walk-forward folds:** to grade season S (2014 to 2025) the model learns only from the seasons
  before S; settings are chosen on the last season before S; the first test season, 2014, has
  only 2013 to learn from and uses default settings (a "thin" fold). The harness raises
  `TestSeasonInTrainingError` on any test-season row and refuses unregistered columns.

Source: `config/production_models.yaml`
- **What is pinned:** `artifacts/production_models/waiver_radar/logit-5828a082c9e09093.joblib`
  (sha256 `d00cc925b07b9ede248a0671ab7394b0fbbd1268a092d1a9a5346fd3a862ecce`) and the backtest
  snapshot of seasons 2014-2025: `predictions.parquet` (91638 rows), `outcomes.parquet` (91638
  rows), `model_versions.parquet` (12 rows), each opened only after its sha256 matches the pin.
  Approved 2026-09-28.

## Evaluation

Source: `reports/waiver_radar/evaluation.md`, "Headlines", "Precision@10, pooled, with 95% intervals"
Walk-forward backtest, every Tuesday of 2014-2025, label `y_hit` (at least one starter week in
his next 3 games): 732 weekly lists, 91,638 pool rows, base rate 10.6% (what a random pick
gets). Precision@10 = the share of a list's top 10 who hit; intervals are 95% season-block
bootstrap intervals (2,000 resamples, fixed seed 20140907). The logistic regression is the
pinned model; the baselines are last week's points and the snap-share change; LightGBM is the
other candidate.

| method | 2014-2025 all pool rows | 2014-2025 without rostered | 2015-2025 all pool rows | 2015-2025 without rostered | lists | picks (2014-2025) |
|---|---|---|---|---|---|---|
| logistic regression | 47.8% (46.2-49.5) | 45.5% (43.6-47.5) | 48.0% (46.4-49.8) | 45.5% (43.5-47.7) | 732 | 3,501/7,320 |
| LightGBM | 47.6% (45.9-49.6) | 45.2% (43.2-47.1) | 47.8% (46.0-49.9) | 45.2% (43.1-47.4) | 732 | 3,483/7,320 |
| last week's points | 40.4% (38.8-42.0) | 38.9% (37.1-40.8) | 40.7% (39.2-42.4) | 39.2% (37.2-41.0) | 732 | 2,956/7,320 |
| snap-share change | 24.6% (23.1-26.2) | 23.8% (22.3-25.2) | 24.6% (23.0-26.3) | 23.6% (22.1-25.2) | 732 | 1,804/7,320 |

Source: `reports/waiver_radar/evaluation.md`, "Paired differences (percentage points, 95% intervals)"
Paired differences (model minus method on the same lists):

| comparison | 2014-2025 all pool rows | 2014-2025 without rostered | 2015-2025 all pool rows | 2015-2025 without rostered | ahead in (2014-2025) | seasons won (2014-2025) |
|---|---|---|---|---|---|---|
| logistic regression - last week's points | +7.4 (+6.2 to +8.7) | +6.6 (+5.5 to +7.7) | +7.3 (+6.0 to +8.6) | +6.3 (+5.3 to +7.5) | 100% | 12 of 12 |
| logistic regression - snap-share change | +23.2 (+21.6 to +24.7) | +21.7 (+20.2 to +23.2) | +23.4 (+21.8 to +25.0) | +21.9 (+20.2 to +23.5) | 100% | 12 of 12 |
| logistic regression - LightGBM | +0.2 (-0.3 to +0.8) | +0.3 (-0.2 to +0.9) | +0.2 (-0.4 to +0.8) | +0.3 (-0.3 to +0.9) | 78% | 7 of 12 |

Source: `reports/waiver_radar/evaluation.md`, "Per position (2014-2025, 95% intervals)"
Per position (all pool rows). **At QB the Radar adds nothing over last week's points**:
+0.3 (-0.6 to +1.3).

| position | lists | hits | base rate | logistic regression | LightGBM | last week's points | snap-share change | logistic regression - last week's points |
|---|---|---|---|---|---|---|---|---|
| QB | 183 | 1,202 | 9.2% | 43.4% (41.2-45.9) | 43.5% (40.9-46.3) | 43.1% (40.8-45.9) | 24.5% (21.1-28.0) | +0.3 (-0.6 to +1.3) |
| RB | 183 | 2,716 | 13.0% | 54.2% (50.8-57.3) | 53.1% (50.4-55.6) | 46.3% (43.5-49.1) | 35.5% (31.7-39.5) | +7.9 (+5.2 to +10.6) |
| WR | 183 | 3,829 | 10.4% | 50.8% (47.6-53.8) | 50.2% (46.3-54.0) | 40.4% (38.1-43.1) | 20.6% (19.3-21.8) | +10.4 (+8.0 to +13.0) |
| TE | 183 | 1,979 | 9.5% | 42.8% (40.2-45.4) | 43.6% (41.2-46.4) | 31.7% (29.6-33.8) | 18.0% (16.1-19.9) | +11.1 (+8.6 to +13.5) |

Source: `reports/waiver_radar/evaluation.md`, "The experts (2020-2025, on the lists where their page existed)"
Against FantasyPros' ranks (2020-2025, the same 356 lists; 6 seasons, so intervals are wide)
the difference includes zero: the Radar cannot claim to beat the experts.

| method | all pool rows | without rostered |
|---|---|---|
| logistic regression | 48.4% (45.3-51.1) | 43.7% (41.3-46.3) |
| LightGBM | 48.5% (45.2-51.7) | 43.5% (40.7-46.3) |
| the experts (FantasyPros) | 46.9% (44.6-49.3) | 42.0% (40.2-44.3) |
| *logistic regression - experts* (won 5 of 6 seasons) | +1.5 (-0.4 to +2.9) | +1.7 (-0.0 to +3.2) |

Source: `reports/waiver_radar/evaluation.md`, "PR-AUC and Brier (the models' probabilities, all pool rows)"
Probabilities, all pool rows (PR-AUC higher is better; Brier lower is better; the base-rate
Brier is an uninformative forecast):

| seasons | hits / rows | base-rate Brier | logistic regression PR-AUC | logistic regression Brier | LightGBM PR-AUC | LightGBM Brier |
|---|---|---|---|---|---|---|
| 2014-2025 pooled | 9,726 / 91,638 | 0.0950 | 0.4326 | 0.0736 | 0.4181 | 0.0744 |

## Calibration

Source: `reports/waiver_radar/evaluation.md`, "Calibration of the winner (logistic regression, 2014-2025)"
Fixed-width bins of the pinned model's `y_hit` probability, 2014-2025. Up to 60% the mean
prediction and the observed rate agree closely; above 60% the bins are small and the model
runs high (the 246 predictions of 70-80% hit 66.7%).

| fixed-width bin | rows | hits | mean predicted | observed |
|---|---|---|---|---|
| 0-10% | 62,869 | 1,720 | 2.5% | 2.7% |
| 10-20% | 10,960 | 1,481 | 13.6% | 13.5% |
| 20-30% | 7,000 | 1,683 | 25.3% | 24.0% |
| 30-40% | 4,185 | 1,447 | 35.0% | 34.6% |
| 40-50% | 3,516 | 1,615 | 45.5% | 45.9% |
| 50-60% | 1,547 | 835 | 54.8% | 54.0% |
| 60-70% | 1,208 | 703 | 63.1% | 58.2% |
| 70-80% | 246 | 164 | 76.0% | 66.7% |
| 80-90% | 66 | 45 | 83.5% | 68.2% |
| 90-100% | 41 | 33 | 99.0% | 80.5% |

The site leads with the chance (the hit rate of backtest predictions grouped by probability),
not with this probability, because the probabilities run high above about 60%.

## Known limits and failure cases

Source: `docs/waiver_radar.md`, "Known limits"
- **Quarterbacks:** the model adds nothing over last week's points (see Evaluation).
- **The two models are tied.** The winner is picked by a fixed rule; a different season split
  could flip it.
- **Precision is flattered by the proxy pool:** rostered players (2020 on; see the "without
  rostered" columns) and, before 2020, a pool built from last season's points.
- **The experts' baseline is handicapped** by Friday scrapes (before the weekend's games) and
  covers 2020-2025 only.
- **Twelve seasons:** with only 12 blocks the bootstrap intervals tend to run a little narrow;
  a lower bound close to zero means "not proven".
- **Calibration above 60%** (`y_hit`) rests on few predictions and runs high; the
  calibration is fit on one season, and the lowest probabilities round to 0%.
- **One setting for the whole season:** no refit during a season.
- **The chance is pooled** over positions and weeks: it runs a little high early in a season
  and for QBs and WRs at the top.
- **Reasons explain the logistic regression only**, measured from the training average.
- **The freshness check trusts the cache's row counts:** it cannot see a partial file or a
  stat correction published later.
- **Pool edge cases:** a player traded to a team on its bye week drops out for that week;
  rostership is measured on the Friday before the games.

Source: `docs/timemachine.md`, "What is not checked (and why)"
- **Linux not recomputed:** the scheduled Linux job never recomputes history; it serves the
  frozen pin because Linux float noise could flip near ties. A candidate trained on the runner
  usually does not reproduce the committed evaluation, so approval happens on the owner's Mac.

## Owner decisions that shaped it

Source: `docs/progress.md`
- 2026-09-27 (C1): the pool rule (top N by BOTH a preseason list and PPG; FantasyPros ECR for
  2020+, prior-season PPG for 2013-2019): the owner's choice.
- 2026-09-28 (C6): the live model for season S is the walk-forward fold with test season S; a
  list is stored as 'live' only when scored between the Tuesday as-of and the next week's first
  kickoff.
- 2026-09-28: the owner's league is 12-team full PPR; everything league-shaped derives from
  `config/league.yaml`.
- 2026-09-28 (E1): the site shows the 'chance' (historical rate), not the raw model probability.

Source: `config/production_models.yaml`
- Pin approved 2026-09-28.

## Reproducibility

Source: `docs/timemachine.md`, "Results (owner's Mac, 2026-10-02: commit c8921e9 + I3a; 48d3ada since changed only web/)"
- `uv run twm model check waiver_radar`: the pin, the model file's sha256 and version, every
  snapshot file's sha256 and row count, and that the snapshot reproduces the committed
  `reports/waiver_radar/evaluation.csv` rows of the production model.
- `uv run twm timemachine verify --module waiver_radar --sample 99`: every season's fold is
  refit from the warehouse and compared with the stored lists. Last run (2026-10-02):
  2014-2025, 732 lists, 91,638 rows (+ 91,638 store rows vs pin), max abs diff 0.0, 0
  mismatches.

## Ethical notes

The Radar ranks real players by public statistics only (no medical or private data); a low
rank is a statement about a fantasy roster spot, not about the player. It is fantasy advice
for a hobby league, not betting advice, and each pick leads with the historical hit rate of
similar picks rather than the model probability, so a pick is never presented as more certain
than history.
