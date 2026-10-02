# Model card: D/ST streamer (`streamer_dst`) -- a rule, not a model

Source: `config/production_models.yaml`
Pin key `streamer_dst`, pin id (model_version) `baseline_opponent_dst-e76f31d80f3a905e`,
`model: rule`, approved 2026-10-01, scoring season 2026. Every number below is copied from the
file named on the `Source:` line above it (`tests/test_model_cards.py` checks this).

**This is a rule, not a fitted model.** Nothing is learned from data: the D/STs probably on
waivers are ranked by how many points next week's opponent scores per game, fewer first. The
card exists because the rule is pinned and published like a model, and because a fitted model
was tried and lost to it.

## Purpose and intended use

Source: `docs/streamer.md`, "Who is on the list (the pool)"
Every Tuesday, order the team defenses (D/ST) that are probably on waivers for a manager who
streams one D/ST a week in a 12-team league. The pool: D/STs outside the top 18 both in the
experts' preseason ranking (FantasyPros from 2020; last season's points per game before) and in
points per game so far; on average 6 to 9 D/STs each Tuesday of 2013-2025.

**Not intended for:** season-long D/ST rankings; a forecast of points (the rule has no
probability); weeks with injury, weather or betting-line news after Tuesday; leagues with other
D/ST scoring; betting.

## Inputs and point-in-time rules

Source: `docs/streamer.md`, "The weekly list (S2a)"
- **The rule:** ranked by how many points next week's opponent scores per game, fewer first
  (ties: last game's points, then points per game). Its definition (column, direction,
  tie-breakers) is the pinned JSON file, checked against the code's rule when loaded.
- **Inputs:** the opponent's points per game to date and the D/ST's own fantasy points, read
  through the as-of view at the Tuesday as-of; no betting lines, weather or injury news.

Source: `docs/streamer.md`, "The target: did he score like a starter next week?"
- **Label `y_start`** (for grading only): yes if the D/ST finished in the top 12 of its position
  the following week, scored with the owner's league's yards-allowed tiers since step C1.

## Model and training

Source: `docs/deploy.md`, "The streamer's approved methods (step S2a)"
- **No training.** The pinned "model" file is the rule's definition (`<version>.json`). The
  frozen backtest is one table, `hit_rates` (per test season, list length and rank: how many
  lists, how many starts; 434 rows since C1's re-approval with the league's D/ST scoring).
- **Why a rule:** the walk-forward logistic regression for D/ST (the same harness as the
  kickers') did not beat this rule (see Evaluation), so a method that lost is not shipped.

Source: `config/production_models.yaml`
- **What is pinned:** `artifacts/production_models/streamer/baseline_opponent_dst-e76f31d80f3a905e.json`
  (sha256 `d4d693be7cfc58d03fbd012481a0fe2816a4e31471a4e1a3c59dee0041b16683`) and the backtest
  table `hit_rates.parquet` for 2013-2025 (434 rows), each read only after its sha256 matches.
  Approved 2026-10-01.

Source: `docs/streamer.md`, "The weekly list (S2a)"
- **The chance shown on the site:** how often the rule's pick at that rank started in the
  seasons before the list's season (bins of ranks with at least 100 picks; a better rank never
  shows a lower chance). From week 4 of 2026 the top D/ST of each list is a must-add (its
  interval includes 50%); every other D/ST rank is speculative.

## Evaluation

Source: `reports/streamer/backtest.md`, "Verdict", "### DST"
Walk-forward backtest, test seasons 2013-2025: 213 weekly lists, 1491 pool rows, 513 of them
started (base rate 34.4%). The pinned rule is the row "baseline: next opponent"; the fitted
models are what it was compared with. 95% bootstrap intervals resample whole seasons (2,000
draws). **Verdict:** the kept model, logistic regression (precision@5 36.9%), does NOT beat
every baseline: model minus next opponent -0.4 (-2.2 to +1.2) points on precision@5.

| method | precision@3 | precision@5 | #1 pick started |
|---|---|---|---|
| logistic regression | 40.2% (36.0% to 44.1%) | 36.9% (33.9% to 40.0%) | 49.3% (40.6% to 57.1%) |
| LightGBM | 39.0% (36.8% to 41.1%) | 36.6% (34.1% to 38.8%) | 45.5% (38.9% to 51.6%) |
| baseline: last game's points | 36.3% (32.7% to 39.8%) | 34.5% (31.3% to 37.7%) | 36.2% (31.0% to 41.5%) |
| baseline: points per game | 36.8% (34.1% to 39.3%) | 34.3% (31.9% to 36.3%) | 39.0% (34.7% to 42.9%) |
| baseline: next opponent | 41.9% (39.3% to 44.4%) | 37.3% (35.0% to 40.0%) | 52.1% (47.9% to 56.1%) |

Source: `reports/streamer/backtest.md`, "DST: model minus baseline (percentage points, 95% interval, share of resamples above zero)"
The models against the rule (negative = the rule is better; no gap is certain for the logistic
regression):

| model | baseline | precision@3 | precision@5 | #1 pick started |
|---|---|---|---|---|
| logistic regression | next opponent | -1.7 (-4.9 to +0.9), 12% | -0.4 (-2.2 to +1.2), 34% | -2.8 (-12.9 to +6.1), 27% |
| LightGBM | next opponent | -3.0 (-5.8 to -0.2), 2% | -0.7 (-2.7 to +1.3), 27% | -6.6 (-14.7 to +0.9), 4% |

Source: `reports/streamer/backtest.md`, "DST: precision@5 by test season"
Per season, precision@5 (13 seasons of 16 or 17 lists each, so single seasons are noisy):

| season | lists | base rate | logistic regression | next opponent |
|---|---|---|---|---|
| 2013 | 16 | 38% | 36% | 38% |
| 2014 | 16 | 35% | 36% | 36% |
| 2015 | 16 | 39% | 40% | 43% |
| 2016 | 16 | 34% | 38% | 34% |
| 2017 | 16 | 33% | 33% | 33% |
| 2018 | 16 | 34% | 38% | 35% |
| 2019 | 16 | 29% | 31% | 33% |
| 2020 | 16 | 27% | 25% | 34% |
| 2021 | 17 | 31% | 33% | 32% |
| 2022 | 17 | 38% | 42% | 46% |
| 2023 | 17 | 41% | 49% | 46% |
| 2024 | 17 | 33% | 36% | 36% |
| 2025 | 17 | 35% | 42% | 39% |

## Calibration

Source: `reports/streamer/backtest.md`, "DST: probabilities"
The rule gives no probability, so there is nothing to calibrate. The fitted model's
probabilities were WORSE than a constant: Brier score (lower is better): constant forecast (the
training seasons' start rate) 0.2262; logistic regression 0.2306; LightGBM 0.2368. That is why
the site shows each rank's historical start rate under the rule instead.

Source: `reports/streamer/backtest.md`, "### DST"
The rule's #1 pick started 52.1% (47.9% to 56.1%) of the time over 2013-2025, against the base
rate of 34.4%.

## Known limits and failure cases

Source: `docs/streamer.md`, "The honest result"
- **The rule only ties the model:** 0.4 points better on the top 5 and 2.8 points better on the
  #1 pick, and neither gap is certain. It is shipped because the model did not beat it, not
  because it is proven better.

Source: `docs/streamer.md`, "Limitations"
- **Tuesday knowledge only:** no betting lines, weather, injury news or depth-chart changes after
  Tuesday; a Friday check of the weather and the injury report still matters.
- **Small samples:** about 80-300 pool rows per position per season; one unusual season can move
  the pooled result.
- **The pool is an estimate** of the waiver wire: where FantasyPros rostership exists (late 2020
  on), 93-100% of pool rows were rostered in fewer than half of leagues.
- **Scoring and league shape:** a 12-team league with one D/ST; other settings change who counts
  as a top 12 and would need a new backtest. D/STs are scored with the league's yards-allowed
  tiers since step C1 (see below).

Source: `docs/scoring.md`
- **Deliberate scoring differences:** the league's D/ST 2-point return and 1-point safety
  bonuses are not scored.

Source: `docs/timemachine.md`, "What is not checked (and why)"
- **Linux not recomputed:** the scheduled Linux job serves the frozen pin; it never recomputes
  history.

## Owner decisions that shaped it

Source: `docs/progress.md`
- 2026-09-29 (after S1d, technical calls by Claude, recorded for the owner): D/ST lists use the
  'next opponent' rule because it beat the model, and their chance is the historical start rate
  of each rank under that rule in earlier seasons, not a model probability.
- 2026-09-30 (owner, after the first real `twm league settings-diff`): score D/ST yards allowed
  like the league (its nine tiers, in `config/scoring.yaml`); do NOT score the league's bonuses
  (D/ST 2-point return, 1-point safety among them).

Source: `config/production_models.yaml`
- Pin approved 2026-10-01 (the re-approval with the league's D/ST scoring).

## Reproducibility

Source: `docs/timemachine.md`, "Results (owner's Mac, 2026-10-02: commit c8921e9 + I3a; 48d3ada since changed only web/)"
- `uv run twm model check streamer`: the rule file's sha256 and version and the hit-rate
  table's sha256 and row count, and that they reproduce `reports/streamer/backtest.csv`.
- `uv run twm timemachine verify --module streamer_dst --sample 99`: the rule applied to a
  dataset rebuilt from 2012 and compared with the pin. Last run (2026-10-02): 2013-2025, 277
  lists, 434 hit-rate rows + 1,491 served list rows, max abs diff 0.0, 0 mismatches.

## Ethical notes

Team defenses are ranked from public game results only. The list is fantasy advice for a hobby
league, not betting advice; the page shows a rank's past start rate, never a promise.
