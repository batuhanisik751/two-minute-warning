# Cliff & Breakout Board (step I1b, PROJECT_SPEC 8.6)

The board answers two offseason questions for fantasy drafts:

- **Cliff**: which established veteran will lose 30% or more of his points per game next season?
- **Breakout**: which young WR/TE (and, reported apart, RB) will become a fantasy starter?

I1b builds the populations, labels, features, models and an honest walk-forward backtest
(`reports/board/cliff.md`, `reports/board/breakout.md`). The post-draft and preseason snapshots and
the `/board` page are step I2. Code: `src/twm/modules/board/`; run `uv run twm board backtest`.

## Season-level numbers

- **Points per game (PPG)**: a player's regular-season fantasy points (`fact_player_week` stat
  lines scored with `config/scoring.yaml`, never a precomputed points column) divided by his
  **games played** = regular-season games with a stat line. nflverse writes a line for every
  player with a recorded play, so a game in uniform without a touch, target or pass is not
  counted. Checked on the 2022-2024 top-36 players: 390 of 431 have exactly as many games as
  games with an offensive snap (`fact_snaps`); the other 41 differ by 1-3 games.
- **Position** (point in time, never today's): the player's latest regular-season weekly-roster
  row of the season (`fact_roster_week`, 2002 on); the snap-count position and the previous
  season's roster are fallbacks (never needed 2002-2025). 1999-2001 have no rosters: those
  seasons only feed career totals and season-over-season changes.
- **Rank**: within the position (QB/RB/WR/TE) by PPG among players with **8 or more games**
  (spec 8.6); ties go to more total points, then the id. Fewer than 8 games: no rank.
- **Prior seasons** = S - `entry_year` (equal to the roster's `years_exp` on every 2002-2026
  QB/RB/WR/TE row): seasons in the league before S.

## The snapshot (end of season S)

Features are read at the moment every season-S row the board reads is public: the latest
`available_at` of the season's rows in the player, team, snap, opportunity, Next Gen Stats, game
and roster tables. That is the Tuesday after the Super Bowl, 14:00 UTC (2016 on), a week later for
2002-2015 (their rosters are post-game snapshots, public a week later). Everything goes through an
`AsOfView` at that moment, so no later row, no later draft class and no today's-snapshot column
can be read. The one input outside the view, Regression Watch's own walk-forward xFP, is cut to
seasons <= S (a play of season S is scored by models trained on the seasons before S). Free
agency (March) and the draft (April) come after every snapshot.

## Populations and labels (row = player at the snapshot of S; label from S+1)

**Cliff**: 3+ prior seasons and top-36 at his position in PPG (8+ games) in S. In S+1:

- `y_missed` = fewer than 6 games (injury, benching, release, retirement; 0 games counts);
- `y_cliff` = 6+ games and PPG(S+1) <= 70% of PPG(S) (a drop of exactly 30% counts); NULL when
  `y_missed`;
- `y_cliff_or_missed` = either (the sensitivity run).

Owner decision 2026-10-02 ("please continue" at the I1 decision point; reversible): `y_missed` is a
separate label with its own simple estimate, the main Cliff model trains on the players with 6+
games in S+1, and a sensitivity run counts a missed season as a cliff. All three are computed.

**Breakout**: WR, TE and RB with S - entry year = 0 or 1 (entering their 2nd or 3rd season), a
regular-season stat line in S, and NOT top-36 WR / top-12 TE / top-24 RB in PPG in S (an
unranked player, under 8 games, is "not top-N"). `y_breakout` = top-24 WR / top-12 TE / top-24
RB at his S+1 position in PPG with 8+ games in S+1. WR/TE share one model; RB has its own and is
reported apart (spec: "optionally RB").

Sizes 2002-2024 (snapshots): Cliff 77-105 rows a season (6-22 `y_missed`, 13-29 `y_cliff`);
Breakout WR/TE 77-116 rows (2-7 positives), RB 33-59 rows (0-9 positives).

## Features (formulas in `docs/glossary.md`, module `board`)

- **Cliff model** (25): position flags; `age` (on February 1 after S); `age_curve_ratio` (the
  position's aging curve, a quadratic in age of PPG(s+1) / PPG(s) fitted on the season pairs
  s -> s+1 with s + 1 <= S only: top-48 in s, 6+ games in s+1, at least 50 pairs); prior seasons;
  games, PPG and rank in S; PPG change vs S-1; touches and touches per game in S (workload);
  career touches and targets (1999 on: a career that began earlier is cut); yards per touch in S
  and its trend vs the mean of S-1/S-2; snap share and its trend (2013 on); Next Gen Stats
  separation (2016 on) and rush yards over expected per carry (2018 on: upstream has no RYOE for
  2016-2017), each with its trend; own walk-forward xFP per game and points over expected per
  game in S (2009 on: receiver xFP is unusable in 2006-2008, docs/progress.md D1); `hc_departure`.
- **Missed (`y_missed`) model** (7, "its own simple estimate"): position flags, age, prior
  seasons, games in S, touches per game.
- **Breakout model** (24; RB: 23 without the TE flag): TE flag, age, entering the 3rd season,
  games, PPG and touches per game in S; target share in S and in the rookie season; receiving
  yards per team pass attempt; air-yards share; rush share; own xFP per game; snap share; draft
  round, pick, undrafted; **age at the draft** = age on the first day of his draft (his entry
  year's draft when undrafted: the day he could first have been picked; dates from
  `available.DRAFT_FIRST_DAY`); combine forty, weight, height, vertical, broad jump and **speed
  score** = weight x 200 / forty^4 (Bill Barnwell, Football Outsiders, 2008); **QB quality** = his
  S team's ANY/A ((passing yards + 20 x TD - 45 x INT - sack yards) / (attempts + sacks), Pro
  Football Reference's formula; no model column).
- Shares are over the team-games he played. Missing inputs (eras above, no combine row) are imputed
  inside each fit with a missing indicator (LightGBM: NaN).

**Head-coach departure** (I1: the only team change): his S team (his last regular-season game's
team) has a row in `data/manual/coach_departures.csv` with `last_season` = S announced on a day
before the snapshot's date. 74 of the 208 rows have a blank date. Of the 117 DATED rows of the
types fired in/after season, mutual parting and interim not retained, none was announced on or
after its season's snapshot (they come at or right after the last game), so a blank date of those
types counts as known; other blank types (retired, resigned, left for another job, other: 2 of
their 17 dated rows came after the snapshot, e.g. TB 2021's retirement on 2022-03-30) count as
not known (errs late). Other offseason changes (OC, QB, depth chart) are step I2.

**Skipped**: contract status (optional in the spec; no contract data in the warehouse); team
vacated targets (targets of S teammates no longer on the team at the snapshot): at the end-of-season
snapshot no offseason move has happened yet and the warehouse has only in-season weekly rosters
(no point-in-time offseason roster), so the number would be zero or wrong; it belongs to I2's
later snapshots, and only if a dated roster source exists.

## Models and evaluation

- `logit`: L2 logistic regression = the Hot-Seat `InnerCvLogit` (the Radar's `LogitEstimator`;
  C chosen inside each fit by an inner walk-forward over the last 4 training seasons);
  `logit_simple` for `y_missed`; `lgbm`: the Radar's LightGBM on one thread (the streamer's
  class), tuned by the shared harness on the validation season.
- Walk-forward (`twm.backtest.walkforward`): rows keyed by the snapshot season; test snapshots
  2007-2024 (labels 2008-2025), each trained on snapshots 2002 .. S-1 (their labels, seasons up to
  S, are known at S's snapshot). Probabilities: each model's own (no isotonic step).
- Baselines: last season's PPG rank (a one-feature logit, same inner C; an unranked breakout
  player gets the median rank plus the missing flag) and FantasyPros' **preseason expert
  consensus ranking (ECR)** of S+1 (`fact_ranking` page_kind 'preseason', the project's
  preseason scrape: the last August/September one before week 1). It is ECR, not ADP (the spec
  says ADP; the warehouse has none), it exists for 2020+ only, and it is taken months after the
  snapshot (after free agency and the draft), so it knows more than the model. Scores: Cliff and
  `y_missed` = ECR rank minus last season's PPG rank; Breakout = minus the ECR rank (unranked
  last).
- Metrics: PR-AUC, ROC-AUC, Brier, precision@10 and @20 per season, pooled, with season-block
  bootstrap intervals and paired differences (the shared metrics and the Hot-Seat evaluation),
  calibration in 10 equal-count bins, the largest model-vs-ECR disagreements and who was right.

## Results (2026-10-02 run; `reports/board/cliff.md`, `reports/board/breakout.md`)

| variant | model PR-AUC [95% CI] | vs PPG-rank baseline | ECR era (labels 2020-2025): model vs ECR |
|---|---|---|---|
| Cliff main (`y_cliff`, base rate 0.24) | logit 0.363 [0.322, 0.409] | +0.108 [0.076, 0.143] | logit 0.410 vs ECR 0.503: -0.093 [-0.147, -0.023] |
| Missed (`y_missed`) | logit_simple 0.384 [0.330, 0.439] | +0.142 [0.079, 0.204] | 0.352 vs 0.570: -0.218 [-0.375, -0.113] |
| Cliff or missed (sensitivity) | logit 0.535 [0.490, 0.579] | +0.102 [0.060, 0.139] | 0.546 vs 0.689: -0.143 [-0.195, -0.080] |
| Breakout WR/TE | logit 0.235 [0.170, 0.362] | -0.016 [-0.096, 0.088] | 0.343 vs 0.315: +0.028 [-0.080, 0.251] |
| Breakout RB | logit 0.234 [0.146, 0.359] | +0.048 [-0.054, 0.148] | 0.335 vs 0.378: -0.043 [-0.466, 0.256] |

In words: the Cliff models clearly beat last season's rank (precision@10 0.43 vs 0.26 for the main
Cliff) but not the preseason experts on PR-AUC, who rank months later with the offseason known
(on the main Cliff the precision@10 gap, -0.05 [-0.18, +0.08], is not significant). The Breakout
models do not beat last season's rank: positives are rare (2-7 a season for WR/TE) and the
intervals are wide. Over 2007-2024 LightGBM's PR-AUC is below the logit's in every variant (it is
slightly ahead on the main Cliff in the six ECR seasons: 0.423 vs 0.410). The leading Cliff inputs: PPG change vs the year before (a jump
gives some back), running back, fewer games, age and the aging curve, workload.

## Commands and files

- `uv run twm board dataset`: the features and labels of every snapshot (`data/board/dataset.parquet`,
  git-ignored, kept outside iCloud by `scripts/local_storage.sh`).
- `uv run twm board backtest [--resume] [--variant NAME]`: rebuilds the dataset (own xFP from
  Regression Watch's folds on the owner's Mac), runs the walk-forward and writes
  `reports/board/{cliff,breakout}.md`, `.csv` (metrics with intervals), `_seasons.csv` (rows,
  positives and top-k hits per season) and `_features.csv` (mean importance per fold). About 30 s
  on an idle Mac; `--resume` keeps each finished variant in `data/board/runs/` so a long run can be
  split.
- `notebooks/05_cliff_breakout.ipynb`: the populations, labels and backtest, read from those files.
- Tests: `tests/test_board.py` (synthetic world in `tests/board_world.py`; the realdata test runs the
  leakage harness on the real warehouse at the 2016 snapshot).
