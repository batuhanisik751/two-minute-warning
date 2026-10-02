# Model card: K streamer (`streamer_k`)

Source: `config/production_models.yaml`
Pin key `streamer_k`, pin id (model_version) `logit_k-dd21dd427fddf989`, `model: logit`,
approved 2026-09-30, scoring season 2026. Every number below is copied from the file named on
the `Source:` line above it (`tests/test_model_cards.py` checks this).

## Purpose and intended use

Source: `docs/streamer.md`, "Who is on the list (the pool)"
Every Tuesday, order the kickers who are probably on waivers by how likely each is to score like
a starter next week (a top 12 finish at K), for a manager who streams one kicker a week in a
12-team league. The pool: kickers outside the top 18 both in the experts' preseason ranking
(FantasyPros from 2020; last season's points per game before) and in points per game so far.

**Not intended for:** season-long kicker rankings; weeks with fresh injury, weather or
betting-line news (the list knows only what is public on Tuesday); leagues with other scoring
or more than one K slot; betting. Read the list as an order, not as percentages.

## Inputs and point-in-time rules

Source: `docs/streamer.md`, "The target: did he score like a starter next week?", "What the streamer knows on Tuesday (the features)"
- **Label `y_start`:** yes if he finished in the top 12 of his position the following week;
  bye weeks, the week after the last regular-season week and unplayed weeks have no label; a
  kicker who does not kick next week counts as a no.
- **Features (Tuesday as-of):** recent and season kicking volume and points, preseason rank,
  team red-zone context, next opponent and venue; read through the as-of view.
- **No betting lines, no weather, no injury news:** the as-of is Tuesday, before any of that
  exists for the next game.

Source: `reports/streamer/backtest.md`, "Walk-forward"
- **Training cutoff:** the model for season S learns only from seasons before S AND only from
  labels public by S's first Tuesday as-of (any later training row is refused).

## Model and training

Source: `reports/streamer/backtest.md`, "Walk-forward", "Model choice"
- **Walk-forward folds:** one model per position, tuned on the last training season (pooled
  precision@5) and calibrated there (isotonic); no refit during the season. 2013 is a thin fold:
  trained on one season, no tuning, cross-fitted calibration. LightGBM is kept only if it beats
  the logistic regression on pooled precision@5 over all test seasons; it did not.

Source: `docs/streamer.md`, "The weekly list (S2a)"
- **The 2026 model** (trained 2012-2025) kept one feature, "he kicked in his team's latest
  game": in practice, team kickers first, ordered by last game's points. Ties are broken by last
  game's points, then points per game, then preseason rank (never by an id); a setting whose
  model gives every kicker the same score is never chosen.

Source: `config/production_models.yaml`
- **What is pinned:** `artifacts/production_models/streamer/logit_k-dd21dd427fddf989.joblib`
  (sha256 `a35b2e0ff072f8b7a4b4b3075a049b35a6efd54fd64bab4209d00f4457d3550f`, opened only after
  its sha256 matches) and the frozen backtest of 2013-2025: `predictions.parquet` (2318 rows),
  `outcomes.parquet` (2318 rows), `model_versions.parquet` (13 rows). Approved 2026-09-30.

Source: `docs/streamer.md`, "The weekly list (S2a)"
- **The chance shown on the site** is not the model probability: it is how often kickers the
  model scored alike (bins of at least 200 backtest picks) started in the seasons BEFORE the
  list's season, with a 90% interval. In the 2013-2025 backtest no kicker bin reached 50%, so kickers have no
  must-add.

## Evaluation

Source: `reports/streamer/backtest.md`, "Verdict", "### K"
Walk-forward backtest, test seasons 2013-2025: 210 weekly lists, 2318 pool rows, 571 of them
started (base rate 24.6%). precision@k = hits among the top k of a list; `#1 pick started` =
precision@1; 95% bootstrap intervals resample whole seasons (2,000 draws). **Verdict:** the
kept model, logistic regression (precision@5 38.4%), is ahead of every baseline, but not
clearly (an interval includes zero).

| method | precision@3 | precision@5 | #1 pick started |
|---|---|---|---|
| logistic regression | 40.6% (38.1% to 43.1%) | 38.4% (36.3% to 40.3%) | 42.4% (37.1% to 47.6%) |
| LightGBM | 38.6% (35.1% to 41.8%) | 37.0% (34.7% to 39.4%) | 39.5% (34.9% to 44.3%) |
| baseline: last game's points | 39.4% (36.7% to 42.0%) | 36.9% (34.4% to 39.3%) | 40.0% (35.1% to 45.0%) |
| baseline: points per game | 38.1% (34.6% to 41.9%) | 36.7% (34.4% to 39.0%) | 34.8% (31.0% to 38.9%) |
| baseline: next opponent | 30.8% (26.3% to 35.3%) | 28.0% (23.9% to 32.0%) | 37.6% (30.6% to 44.9%) |

Source: `reports/streamer/backtest.md`, "K: model minus baseline (percentage points, 95% interval, share of resamples above zero)"
Paired differences of the pinned model against the baselines:

| model | baseline | precision@3 | precision@5 | #1 pick started |
|---|---|---|---|---|
| logistic regression | last game's points | +1.3 (-1.0 to +3.3), 85% | +1.4 (-0.2 to +3.0), 95% | +2.4 (-3.8 to +7.6), 77% |
| logistic regression | points per game | +2.5 (-1.9 to +6.9), 86% | +1.6 (-0.5 to +3.7), 93% | +7.6 (+2.0 to +13.3), 99% |
| logistic regression | next opponent | +9.8 (+4.0 to +16.0), 100% | +10.4 (+6.2 to +14.6), 100% | +4.8 (-3.8 to +14.6), 84% |

Source: `reports/streamer/backtest.md`, "Pool kickers who did not kick"
Pool kickers who did not kick in their team's latest game started 2.3% of the time, team
kickers 36.0%; the "next opponent" rule puts many of them in its top 5 (25%), the logistic
regression 2%.

## Calibration

Source: `reports/streamer/backtest.md`, "K: probabilities"
Brier score (lower is better): constant forecast (the training seasons' start rate) 0.1878;
logistic regression 0.1685; LightGBM 0.1744. Calibration of the kept model, fixed 10-point
bins: the chances run low below 30% and high from 40% up (the 110 predictions of 50%-60% started
38.2%).

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

## Known limits and failure cases

Source: `docs/streamer.md`, "Limitations"
- **The model adds little:** ahead of last game's points by an interval that includes zero
  (see Evaluation); most of what can be known on Tuesday is already in last week's points.
- **One-feature folds:** the kicker folds of 2021 and of 2026 (the one ranking this season's
  lists) keep only "is his team's kicker", so among team kickers the order is last game's
  points (the tie-breaker).
- **Tuesday knowledge only:** no betting lines, weather, injury news or depth-chart changes
  after Tuesday; a Friday check still matters.
- **Small samples:** about 80-300 pool rows per position per season; one unusual season can move
  the pooled result. Probabilities are calibrated on one season, which is noisy.
- **The pool is an estimate** of the waiver wire; practice-squad, camp and released kickers are
  in it (any good list pushes them to the bottom).
- **Gaps in the data:** no kicker pool in week 1 of 2012-2015 (rosters of that era are only
  published after the games).
- **Trained once before each season** (no updates during the season).

Source: `docs/timemachine.md`, "What is not checked (and why)"
- **Linux not recomputed:** the scheduled Linux job serves the frozen pin; it never recomputes
  history.

## Owner decisions that shaped it

Source: `docs/progress.md`
- 2026-09-29 (after S1d, technical calls by Claude, recorded for the owner): K lists use the
  logistic regression with its calibrated chance; ties broken by last game's points, never by
  id; settings that give a constant model are invalid.
- 2026-09-28: the owner's league is 12-team full PPR with one K slot; everything league-shaped
  derives from `config/league.yaml`.

Source: `config/production_models.yaml`
- Pin approved 2026-09-30.

## Reproducibility

Source: `docs/timemachine.md`, "Results (owner's Mac, 2026-10-02: commit c8921e9 + I3a; 48d3ada since changed only web/)"
- `uv run twm model check streamer`: both streamer pins' sha256s and row counts, and that the
  frozen K backtest reproduces `reports/streamer/backtest.csv`.
- `uv run twm timemachine verify --module streamer_k --sample 99`: each season's fold refit
  from a dataset rebuilt from 2012 and compared with the pinned lists. Last run (2026-10-02):
  2013-2025, 210 lists, 2,318 rows, max abs diff 0.0, 0 mismatches.

## Ethical notes

Kickers are ranked from public game statistics only. The list is fantasy advice for a hobby
league, not betting advice; it says plainly that streaming picks are coin flips at best.
