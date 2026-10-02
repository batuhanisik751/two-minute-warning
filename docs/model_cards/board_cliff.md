# Model card: Cliff board -- Cliff and missed-time models (`board`)

Source: `config/production_models.yaml`
Pin key `board`, pin id (model_version) `board_spec-1581a6da7602c3f7`, `model: board_spec`,
approved 2026-10-02, board season 2026. Every number below is copied from the file named on the
`Source:` line above it (`tests/test_model_cards.py` checks this).

## Purpose and intended use

Source: `docs/board.md`, "Populations and labels (row = player at the snapshot of S; label from S+1)", "Production (step I2c-a)"
Before each season, for every established fantasy starter (3+ prior seasons and top-36 at his
position in PPG with 8+ games last season), show two chances side by side with FantasyPros'
preseason ECR: the **Cliff** chance (6+ games next season and PPG at most 70% of last season's)
and the **missed-time** chance (fewer than 6 games next season), highlighting where the models
and the market disagree and how past disagreements (2020-2025) turned out.

**Not intended for:** a full draft ranking (it only covers last season's starters and says
nothing about upside); in-season decisions; medical predictions about a real person's health
(the missed-time chance is a base rate for similar profiles, not an injury forecast); betting.
The **Breakout** model is a research note only and is NOT shown on the site (see Evaluation).

## Inputs and point-in-time rules

Source: `docs/board.md`, "Preseason snapshot (steps I2a, I2-fix)"
- **Anchor `week1_kickoff_eve`:** one hour before the first regular-season week-1 kickoff of
  S+1 (a deliberate change from the spec's Tuesday). Every feature is recomputed through an
  `AsOfView` at that moment.
- **Week-1 depth chart:** the chart rows of S+1 visible at the as-of (daily pulls from 2025 on,
  else the legacy week-1 chart). `dc_absent` flags a player on no chart while his team's chart
  is visible (cut, unsigned, retired, hurt). Week-1 rosters are public only after week 1 and
  are not used.

Source: `docs/board.md`, "The snapshot (end of season S)"
- **Season-S features** (usage, efficiency, Next Gen Stats, own walk-forward xFP cut to seasons
  <= S, age and experience) are read at the moment every season-S row is public; free agency and
  the draft come after that snapshot, and the preseason snapshot then adds what changed.

Source: `reports/board/preseason_cliff.md`, "Read before trusting"
- Inputs missing in early seasons (NGS before 2016, RYOE before 2018, snap counts before 2013,
  own xFP before 2009) are imputed inside each fit with a missing indicator.
- Head-coach departures come from the owner's cited research file (accepted in bulk,
  2026-10-01).

## Model and training

Source: `docs/board.md`, "Production (step I2c-a)"
- **Models:** `cliff` = the logistic regression on `y_cliff`; `missed` = a simple logistic
  regression on `y_missed`; both with the preseason features, C by the inner walk-forward, the
  model's own probability (no isotonic step). The board of season S1 uses the folds the
  walk-forward would use for snapshot S = S1 - 1: every labelled snapshot 2002 .. S - 1.
- **For 2026:** Cliff `logit-9633152d93661796` (C 0.01, 1,825 rows, 440 positive), missed
  `logit_simple-072eba13c3d7830f` (C 0.1, 2,165 rows, 340 positive).
- **The spec** names both model files by version and sha256 with the board's season, snapshot
  and anchor; a pickle is opened only after its sha256 matched; the pin is refused when the
  config's preseason anchor is not the approved one.

Source: `config/production_models.yaml`
- **What is pinned:** `artifacts/production_models/board/board_spec-1581a6da7602c3f7.json`
  (sha256 `af82890658def2e579e617b456d9a1dbaf8ea2f5bce8586c8283a274d5eb6691`) and the frozen
  backtest of seasons 2007-2024: `predictions.parquet` (1674 rows), `outcomes.parquet` (1674),
  `model_versions.parquet` (36), plus the 2026 board `current_board.parquet` (94 rows) and the
  rows it was scored from, `current_inputs.parquet` (94). Approved 2026-10-02.

## Evaluation

Source: `reports/board/preseason_cliff.md`, "Cliff (6+ games in S+1; main model) (`cliff_main`, label `y_cliff`)", "Rows and positives per test season"
**Cliff** (`y_cliff`, the pinned `logit`), walk-forward snapshots 2007-2024 (labels 2008-2025):
337 positives (13-29 a season). Intervals resample whole seasons. Baselines: last season's PPG
rank (`base_ppg_rank`) and the end-of-season model (`eos`), and from 2019 FantasyPros' ECR.

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit` | 0.405 [0.354, 0.463] | 0.478 [0.422, 0.539] | 0.392 [0.339, 0.447] | 0.167 [0.155, 0.179] |
| `lgbm` | 0.379 [0.322, 0.441] | 0.450 [0.367, 0.522] | 0.392 [0.330, 0.453] | 0.170 [0.158, 0.181] |
| `base_ppg_rank` | 0.255 [0.225, 0.295] | 0.261 [0.211, 0.317] | 0.264 [0.225, 0.303] | 0.180 [0.168, 0.193] |
| `eos` | 0.363 [0.322, 0.409] | 0.433 [0.350, 0.517] | 0.361 [0.314, 0.408] | 0.171 [0.159, 0.183] |

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | +0.150 [0.114, 0.188] | +0.217 [0.172, 0.256] | +0.128 [0.083, 0.175] | 1.00 |
| `logit` - `eos` | +0.042 [0.019, 0.067] | +0.044 [-0.017, 0.106] | +0.031 [-0.003, 0.061] | 1.00 |

Source: `reports/board/preseason_cliff.md`, "snapshots 2019-2024 (labels 2020-2025), with the ECR"
**Against the market** (snapshots 2019-2024, six seasons, so the intervals are wide): the model
does not beat the ECR (PR-AUC difference -0.043 [-0.129, 0.042]).

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit` | 0.459 [0.371, 0.562] | 0.567 [0.433, 0.683] | 0.450 [0.358, 0.542] | 0.168 [0.151, 0.183] |
| `eos` | 0.410 [0.341, 0.484] | 0.483 [0.317, 0.683] | 0.417 [0.367, 0.459] | 0.173 [0.154, 0.190] |
| `ecr` | 0.503 [0.424, 0.570] | 0.533 [0.417, 0.650] | 0.450 [0.375, 0.525] | - |

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `eos` | +0.049 [0.014, 0.094] | +0.083 [0.000, 0.167] | +0.033 [-0.033, 0.108] | 1.00 |
| `logit` - `ecr` | -0.043 [-0.129, 0.042] | +0.033 [-0.033, 0.083] | -0.000 [-0.058, 0.075] | 0.22 |

Source: `reports/board/preseason_cliff.md`, "Missed most of S+1 (under 6 games) (`cliff_missed`, label `y_missed`)"
**Missed time** (`y_missed`, the pinned `logit_simple`), snapshots 2007-2024: 251 positives
(6-21 a season).

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit_simple` | 0.642 [0.585, 0.699] | 0.728 [0.656, 0.794] | 0.519 [0.456, 0.583] | 0.078 [0.068, 0.089] |
| `base_ppg_rank` | 0.242 [0.209, 0.281] | 0.283 [0.217, 0.350] | 0.264 [0.217, 0.311] | 0.122 [0.111, 0.135] |
| `eos` | 0.384 [0.330, 0.439] | 0.433 [0.361, 0.500] | 0.322 [0.269, 0.375] | 0.113 [0.102, 0.125] |

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit_simple` - `base_ppg_rank` | +0.400 [0.325, 0.468] | +0.444 [0.361, 0.522] | +0.256 [0.200, 0.311] | 1.00 |
| `logit_simple` - `eos` | +0.258 [0.209, 0.308] | +0.294 [0.233, 0.361] | +0.197 [0.156, 0.242] | 1.00 |

Source: `reports/board/preseason_breakout.md`, "Breakout WR/TE (`breakout_wr_te`, label `y_breakout`)"
**Breakout (research note, not on the site):** the WR/TE Breakout model did not beat last
season's PPG rank (PR-AUC difference +0.006 [-0.061, 0.099], precision@10 +0.000), so the
owner kept it off the site; its backtest rows are published as research rows of the track
record only.

| model | PR-AUC [95% CI] | precision@10 | precision@20 | Brier |
|---|---|---|---|---|
| `logit` | 0.257 [0.187, 0.385] | 0.244 [0.200, 0.294] | 0.178 [0.150, 0.206] | 0.037 [0.030, 0.044] |
| `base_ppg_rank` | 0.251 [0.197, 0.336] | 0.244 [0.206, 0.289] | 0.164 [0.136, 0.192] | 0.037 [0.031, 0.043] |

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | +0.006 [-0.061, 0.099] | +0.000 [-0.039, 0.039] | +0.014 [-0.014, 0.039] | 0.56 |

Source: `reports/board/preseason_breakout.md`, "Breakout RB (`breakout_rb`, label `y_breakout`)"
**Breakout RB (research note, not on the site):** for running backs the model was narrowly
ahead of last season's PPG rank on PR-AUC, +0.088 [0.009, 0.183] (precision@10 +0.028 [-0.011,
0.067]), but on a small population, after testing several groups and snapshots, so the owner's
decision stands: kept off the site, to be re-checked after another season.

| difference | PR-AUC | precision@10 | precision@20 | share of resamples > 0 (PR-AUC) |
|---|---|---|---|---|
| `logit` - `base_ppg_rank` | +0.088 [0.009, 0.183] | +0.028 [-0.011, 0.067] | +0.008 [-0.008, 0.025] | 0.98 |

## Calibration

Source: `reports/board/preseason_cliff.md`, "Calibration of `logit` (its own probability; 10 equal-count bins, every test season)"
**Cliff** (`logit`, its own probability, 10 equal-count bins, every test season): the bins
track the observed rate within a few points; the top bin (0.390-0.988) predicts 0.518 and saw
0.514.

| bin | rows | mean predicted | observed | range |
|---|---|---|---|---|
| 1 | 143 | 0.074 | 0.084 | 0.000-0.102 |
| 2 | 142 | 0.123 | 0.099 | 0.102-0.141 |
| 3 | 142 | 0.156 | 0.183 | 0.141-0.168 |
| 4 | 143 | 0.181 | 0.210 | 0.168-0.194 |
| 5 | 142 | 0.206 | 0.162 | 0.194-0.218 |
| 6 | 142 | 0.231 | 0.254 | 0.218-0.243 |
| 7 | 143 | 0.258 | 0.259 | 0.243-0.278 |
| 8 | 142 | 0.297 | 0.275 | 0.279-0.317 |
| 9 | 142 | 0.351 | 0.331 | 0.317-0.390 |
| 10 | 142 | 0.518 | 0.514 | 0.390-0.988 |

Source: `reports/board/preseason_cliff.md`, "Calibration of `logit_simple` (its own probability; 10 equal-count bins, every test season)"
**Missed time** (`logit_simple`): most players sit in low bins; bin 8 predicts 0.132 and saw
0.071, bin 9 predicts 0.295 and saw 0.377.

| bin | rows | mean predicted | observed | range |
|---|---|---|---|---|
| 1 | 168 | 0.013 | 0.012 | 0.003-0.018 |
| 2 | 167 | 0.022 | 0.042 | 0.018-0.027 |
| 3 | 168 | 0.032 | 0.060 | 0.027-0.037 |
| 4 | 167 | 0.042 | 0.030 | 0.037-0.047 |
| 5 | 167 | 0.053 | 0.054 | 0.047-0.060 |
| 6 | 168 | 0.070 | 0.048 | 0.060-0.078 |
| 7 | 167 | 0.093 | 0.066 | 0.078-0.109 |
| 8 | 168 | 0.132 | 0.071 | 0.109-0.166 |
| 9 | 167 | 0.295 | 0.377 | 0.166-0.587 |
| 10 | 167 | 0.784 | 0.743 | 0.588-0.982 |

## Known limits and failure cases

Source: `reports/board/preseason_cliff.md`, "ECR timing (negative = the ECR is public only after the as-of)", "Read before trusting"
- **ECR timing:** the comparison with FantasyPros uses the ECR scrape public before the as-of
  (+6.0 to +7.0 days before it in 2019-2024), and only six seasons have it: the ECR-era
  intervals are wide. The Cliff model does not beat the ECR there.
- **Missed time leans on the week-1 depth chart:** the simple model's top feature is
  `depth_rank_s1` (14% of the importance), information the market also has; against the ECR
  (2019-2024) its PR-AUC difference is +0.064 [-0.051, 0.128], which includes zero.
- **Probabilities are each model's own** (no isotonic step).

Source: `docs/board.md`, "Preseason snapshot (steps I2a, I2-fix)"
- **The Tuesday anchor has no charts:** at the spec's Tuesday-before-week-1 anchor no team has
  a chart for S = 2002-2023, which is why the kickoff-eve anchor is used; at the kickoff eve
  every team has one except MIA and TB for S = 2016.

Source: `docs/progress.md`
- **Publishing earlier than the anchor:** the live board next August may be published earlier
  from the latest daily depth chart, and the page must say so.
- **Head-coach departures** (a feature) come from labels accepted in bulk by the owner, not
  checked row by row.

Source: `docs/board.md`, "Production (step I2c-a)"
- **The 2026 board is reconstructed:** its as-of (the kickoff eve) had passed at approval, so it
  is stored as kind 'backtest' (reconstructed, outcomes pending) and frozen in the pin.

Source: `docs/timemachine.md`, "What is not checked (and why)"
- **Linux not recomputed:** the scheduled job never scores the board or recomputes history; it
  serves the pinned boards.

## Owner decisions that shaped it

Source: `docs/progress.md`
- 2026-10-02 (Claude, I1 decision point, owner said "please continue"; reversible): Cliff
  players with fewer than 6 games in S+1 get a separate `y_missed` label (its own simple
  estimate) and are left out of the main Cliff model; a sensitivity run counts them as cliffs.
- 2026-10-02 (owner, I2 board decisions, all recommendations): (1) the preseason model learns
  from the kickoff-eve anchor (a deliberate change from the spec's Tuesday); (2) Breakout is NOT
  shown on the site (it did not beat last season's PPG rank); (3) /board shows the Cliff and
  missed-time chances side by side with FantasyPros' preseason ECR, highlighting disagreements
  and how past disagreements (2020-2025) turned out.

Source: `config/production_models.yaml`
- Pin approved 2026-10-02.

## Reproducibility

Source: `docs/timemachine.md`, "Results (owner's Mac, 2026-10-02: commit c8921e9 + I3a; 48d3ada since changed only web/)"
- `uv run twm model check board`: the spec, both model files and the snapshot (sha256), that the
  snapshot reproduces `reports/board/preseason_cliff.csv`, `preseason_cliff_seasons.csv` and the
  disagreement tables of `preseason_cliff.md`, and that re-scoring the frozen 2026 inputs with
  the pinned models gives the frozen board.
- `uv run twm timemachine verify --module board --sample 99`: the preseason dataset rebuilt
  (kickoff-eve anchor), both folds refit per board. Last run (2026-10-02): 2008-2025, 18 lists,
  1,674 rows (+ the 94-row 2026 board re-scored), max abs diff 0.0, 0 mismatches.

## Ethical notes

The missed-time chance is about real players' availability, which often means injuries. It is a
historical rate for players with a similar public profile (age, position, depth-chart spot), not
a medical judgement, and it uses no medical records or private data. Showing it next to a named player
should be read as "players like this one", never as a claim about his health. Fantasy advice
for a hobby league, not betting advice.
