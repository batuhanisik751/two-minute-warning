# Cliff & Breakout Board (step I1b, PROJECT_SPEC 8.6)

The board answers two offseason questions for fantasy drafts:

- **Cliff**: which established veteran will lose 30% or more of his points per game next season?
- **Breakout**: which young WR/TE (and, reported apart, RB) will become a fantasy starter?

I1b builds the populations, labels, features, models and an honest walk-forward backtest
(`reports/board/cliff.md`, `reports/board/breakout.md`). The preseason (I2a) and post-draft (I2b)
snapshots are below; the board in production (step I2c-a: the approved models, their frozen
backtest, `twm board score`, the published tables) is "Production" at the end; the `/board`
page is step I2c. Code: `src/twm/modules/board/`; run `uv run twm board backtest`.

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

## Preseason snapshot (steps I2a, I2-fix)

Same rows and labels as the end-of-season snapshot of S, read at the preseason as-of of S+1. Two
anchors (config `as_of.board.preseason`, module `twm.modules.board.preseason`, code
`twm board backtest --snapshot preseason [--anchor ...]`):

- **`week1_kickoff_eve`** (the config default since step I2c-a; owner decision 2026-10-02, a
  deliberate change from the spec's Tuesday, below): one hour before the first regular-season
  week-1 kickoff of S+1 (`fact_game.kickoff_utc`); e.g. 2017-09-07 23:30 UTC for S = 2016.
  Reports: `reports/board/preseason_{cliff,breakout}.*` (until I2c-a these were the
  `preseason_kickoff_eve_*` files; regenerated with the same numbers: every CSV byte-identical,
  each report's intro line reworded).
- **`tuesday_before_week_1`** (spec 6.1 "the Tuesday before Week 1", kept as the alternative):
  the last `as_of.weekly` weekday/time (Tuesday 14:00 UTC) strictly before that kickoff; e.g.
  2017-09-05 14:00 UTC, and the day before a Wednesday opener (2012, 2026). It equals week 1's
  `dim_week.asof_weekly_utc` minus 7 days in every season. Reports (kept):
  `reports/board/preseason_tuesday_{cliff,breakout}.*` (until I2c-a: `preseason_*`).

Every feature above is recomputed through an `AsOfView` at that moment (only `hc_departure` can
change: later-dated departures are now known), and these exist only now:

- **Week-1 chart**: `fact_depth_chart` rows of S+1 visible at the as-of: per team its daily pull
  with the latest `dt` (2025 on), else the legacy week-1 REG chart (public the Wednesday 14:00
  UTC before week 1: week 1's Tuesday as-of minus 6 days). The warehouse has no preseason chart
  rows (legacy game types are REG and postseason only), so **at the Tuesday anchor no team has a
  chart for S = 2002-2023**; S = 2024 and 2025 (daily pulls, every pull lists 32 teams) have all
  32. At the kickoff eve every team has one except MIA and TB for S = 2016 (their week-1 game
  moved to week 11; docs/assumptions.md section 18). `depth_rank` 1 = the starter of a slot:
  legacy ranks are per slot already (two or three WRs share rank 1); a daily pull numbers a
  position's players across its slots (WR 1 .. 15), so its rank is renumbered within the slot
  (`pos_slot`) in the pull's order. Week-1 rosters (`fact_roster_week`) are public only after
  week 1 and are not used.
- `dc_absent`: no row of his on any team's chart **while his team's chart is visible** (his S
  team's: his S+1 team is unknown when he is on no chart; cut, unsigned, retired, hurt). Flagged,
  never dropped; every feature below is then NULL.
- `dc_team_chart_missing`: no row of his on any chart and **no visible chart of his team** (a
  Tuesday before the legacy charts publish, or a team whose week-1 game moved). `dc_absent` is
  then NULL (the models' missing indicator), never true: a missing chart is not a cut. Player
  rows chart-missing: 11,961 of 13,176 at the Tuesday anchor (all of 2002-2023), 33 at the
  kickoff eve (2016 MIA/TB).
- `team_change_s1`: his chart team (the one where he has his best offense rank) differs from his
  S team (his last regular-season game's).
- `depth_rank_s1`: his best depth rank among his offense slots of his position (RB: RB or HB);
  NULL when he is listed only elsewhere (FB, KR).
- `new_competitor_s1`: a teammate of the same position at his depth rank or ahead who was on none
  of that team's regular-season weekly rosters of S (a rookie or an arrival).
- `qb1_change_s1`: the team's S primary starter (most regular-season pass attempts for it in S;
  ties: the lower id) is not among its best-ranked QBs on the chart.
- `vacated_targets_share_s1` / `vacated_carries_share_s1`: the team's S regular-season targets /
  carries by players with no row on its chart, over the team's S total (a daily row without a
  `gsis_id` cannot be matched: that player counts as gone).
- `hc_change_s1`: his chart team has a departure with `last_season` = S announced before the
  as-of's date (blank dates: the `hc_departure` rule).

NULL inputs get the models' missing indicators. The variants, folds, metrics and intervals are
I1b's; every model except the PPG-rank baseline gets the nine features. `eos` in the reports is
the end-of-season report's primary model on the same rows, compared with paired season-block
intervals. The ECR is the preseason scrape (the last August/September scrape before week 1's
first game day); each report has a table of when it became public: for labels 2020-2025 at
2020-09-04 .. 2025-08-30 00:00 UTC, 3.6 days before the Tuesday anchor (4.6 in 2020) and 6-7 days
before the kickoff eve, so at both anchors the experts know nothing the snapshot could not.

Results (2026-10-02 I2-fix run, primary models, PR-AUC with 95% season-block intervals,
snapshots 2007-2024; ECR era = labels 2020-2025):

| variant | Tuesday (alternative) | vs `eos` | ECR era vs ECR | kickoff eve (default) | vs `eos` | ECR era vs ECR |
|---|---|---|---|---|---|---|
| Cliff main (logit) | 0.361 [0.319, 0.407] | -0.001 [-0.004, +0.000] | 0.408 vs 0.503: -0.094 [-0.149, -0.021] | 0.405 [0.354, 0.463] | +0.042 [+0.019, +0.067] | 0.459 vs 0.503: -0.043 [-0.129, +0.042] |
| Missed (logit_simple) | 0.384 [0.330, 0.439] | +0.000 [-0.000, +0.000] | 0.352 vs 0.570: -0.217 [-0.375, -0.113] | 0.642 [0.585, 0.699] | +0.258 [+0.209, +0.308] | 0.634 vs 0.570: +0.064 [-0.051, +0.128] |
| Cliff or missed (logit) | 0.535 [0.490, 0.579] | -0.000 [-0.001, +0.001] | 0.545 vs 0.689: -0.144 [-0.197, -0.079] | 0.673 [0.633, 0.709] | +0.138 [+0.117, +0.156] | 0.669 vs 0.689: -0.020 [-0.081, +0.032] |
| Breakout WR/TE (logit) | 0.235 [0.170, 0.362] | -0.000 [-0.000, +0.000] | 0.343 vs 0.315: +0.028 [-0.080, +0.251] | 0.257 [0.187, 0.385] | +0.022 [-0.014, +0.054] | 0.352 vs 0.315: +0.037 [-0.050, +0.255] |
| Breakout RB (logit) | 0.234 [0.146, 0.359] | +0.000 [+0.000, +0.000] | 0.335 vs 0.378: -0.043 [-0.466, +0.256] | 0.274 [0.189, 0.413] | +0.040 [-0.045, +0.116] | 0.302 vs 0.378: -0.075 [-0.331, +0.048] |

In words: at the spec's Tuesday the warehouse has no week-1 chart for any training season, so
the chart features are all missing, the models cannot learn them, and the Tuesday snapshot equals
the end-of-season one (every difference within +/-0.001); the ECR, public 3-4 days earlier, wins
on every Cliff variant. The chart's value shows only at the kickoff eve (Cliff +0.042, Missed
+0.258 over `eos`, a tie with the ECR), which needs the Wednesday chart. The kickoff-eve numbers
equal I2a's to within 0.001: the only rows the new flag changes are 2016's 33 MIA/TB rows. A
Tuesday snapshot with charts would need a dated chart source published before week 1's
Wednesday (none in the warehouse before the 2025 daily pulls).

## Post-draft snapshot (steps I2b, I2-fix)

Same rows and labels as the end-of-season snapshot of S, read after the S+1 draft (module
`twm.modules.board.post_draft`, code `twm board backtest --snapshot post_draft`). **As-of**:
00:00 UTC on config `as_of.board.post_draft` (spec 6.1: June 1, "06-01") of S+1. The code
refuses a configured day before the draft is complete and public: the day after its last day
(its first day + 3 days, because only first days are recorded: `available.DRAFT_FIRST_DAY`,
docs/assumptions.md section 16) and `availability.draft_public_month_day` (May 15; `dim_player`
shows a drafted player's draft fields only from then). I2b used that May 15 lower bound itself;
the I2-fix rerun at June 1 gives the same dataset (0 of 13,176 rows differ in any feature: no
departure was announced and no pick became public between May 15 and June 1) and the same
metrics to the last digit. Every I1b feature is recomputed at that moment; only
`hc_departure` changes (37 rows: NO 2011 and TB 2021, announced after the end-of-season
snapshot). It is this snapshot's **head-coach change announced by the as-of** (his S team; no new
column, never NULL). The new features (NULL when the S+1 draft has no pick visible at the as-of,
which never happens 2003-2026; NULL inputs get the models' missing indicators):

- **The picks**: `dim_player` rows with `draft_year` = S+1 (`draft_team`, `draft_round`, overall
  `draft_pick`). A pick's position is his `fact_combine` `pos` of that year (public from the
  draft's first day): `dim_player.position` is today's and hidden point-in-time, and the raw
  `draft_picks` file is not a warehouse table (not added: the combine covers the early skill
  picks, below). Team codes are today's franchise codes in both, so they match.
- `draft_pos_count_pd`: picks of his S team (his last regular-season game's) at his position
  (RB: RB or HB). `draft_pos_best_round_pd` / `draft_pos_best_pick_pd`: the best (lowest) round
  and overall pick among them; NULL when there is none.
- `draft_qb_r1_pd`: his S team drafted a combine-listed QB in round 1.
- `draft_unplaced_pd` (the missing flag of the counts): his S team made a round 1-3 pick with no
  combine row of that year, so the pick may be at his position and uncounted. Of the drafted
  `dim_player` rows 2003-2025, 4,809 of 5,716 (84%; 74-91% a year) have a combine position; of the
  round 1-3 picks whose position is QB, RB, WR or TE today, all have one in 18 of those 23 drafts,
  all but 1-3 in four (2003, 2007, 2017, 2018) and 25 of 32 in 2021 (the pro-day year).
- **Not features**: team changes by free agency or trade. The warehouse has no dated
  transactions (weekly rosters exist only in season), so a move between the Super Bowl and the
  as-of is not observable point-in-time; it is not approximated.

Results (2026-10-02 run, unchanged by the I2-fix June 1 rerun;
`reports/board/post_draft_{cliff,breakout}.md`, primary models, PR-AUC with 95% season-block
intervals; `eos` = the end-of-season primary on the same rows, paired):

| variant | post-draft PR-AUC | vs `eos` | ECR era (labels 2020-2025): vs ECR |
|---|---|---|---|
| Cliff main | logit 0.359 [0.312, 0.411] | -0.003 [-0.016, +0.009] | 0.414 vs 0.503: -0.088 [-0.145, -0.018] |
| Missed | logit_simple 0.388 [0.330, 0.448] | +0.004 [-0.015, +0.029] | 0.362 vs 0.570: -0.207 [-0.353, -0.094] |
| Cliff or missed | logit 0.534 [0.490, 0.580] | -0.000 [-0.009, +0.009] | 0.554 vs 0.689: -0.135 [-0.189, -0.069] |
| Breakout WR/TE | logit 0.231 [0.174, 0.334] | -0.004 [-0.050, +0.022] | 0.319 vs 0.315: +0.003 [-0.062, +0.116] |
| Breakout RB | logit 0.198 [0.128, 0.312] | -0.036 [-0.100, -0.000] | 0.218 vs 0.378: -0.160 [-0.455, +0.027] |

In words: the draft adds nothing measurable to the end-of-season models (every Cliff difference
is within +/-0.01; the Breakout RB model is slightly worse), and the experts' preseason ECR,
taken three to four months later, still wins on the Cliff variants. The week-1 depth chart
(preseason snapshot) is where offseason information helps.

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
- `notebooks/05_cliff_breakout.ipynb`: the populations, labels and backtest, read from those files,
  and the later snapshots' paired differences and top inputs
  (`reports/board/{post_draft,preseason,preseason_tuesday}_*`).
- `uv run twm board backtest --snapshot preseason|post_draft [--resume]`: also builds
  `data/board/dataset_{preseason,post_draft}.parquet` and writes `reports/board/{preseason,post_draft}_*`
  (the end-of-season variants run first, for the paired `eos` comparison; preseason = the
  config's anchor, the kickoff eve). `--snapshot preseason --anchor tuesday_before_week_1`
  builds `dataset_preseason_tuesday.parquet` and writes `reports/board/preseason_tuesday_*`.
- Tests: `tests/test_board.py` (synthetic world in `tests/board_world.py`; the realdata test runs the
  leakage harness on the real warehouse at the 2016 snapshot), `tests/test_board_preseason.py`,
  `tests/test_board_post_draft.py` (its realdata test runs the harness at the 2016 preseason, both
  anchors, and post-draft as-ofs).

## Production (step I2c-a)

Owner decisions (2026-10-02, docs/progress.md): the board's preseason model learns from the
kickoff-eve anchor; Breakout is NOT on the site (it did not beat last season's PPG rank: its
backtest rows are published as `research` rows of the track record only); `/board` shows the Cliff
and missed-time chances side by side with FantasyPros' preseason ECR, with the disagreements and
how past ones (2020-2025) turned out. Code: `src/twm/modules/board/{production,live,
production_cli}.py`, `src/twm/publish/board_lists.py`.

- **Models** (`production.ROLES`): `cliff` = `cliff_main`'s logit (`y_cliff`) and `missed` =
  `cliff_missed`'s simple logit (`y_missed`), both with the preseason features. The board of
  season S1 uses the folds the walk-forward would use for snapshot S = S1 - 1: every labelled
  snapshot 2002 .. S - 1 (`production_fold`, `fit_fold`), C by the inner walk-forward; the
  model's own probability. For 2026: Cliff `logit-9633152d93661796` (C 0.01, 1,825 rows, 440
  positive), missed `logit_simple-072eba13c3d7830f` (C 0.1, 2,165 rows, 340 positive).
- **Pin** `board` (`model: board_spec`): a JSON spec naming both model files by version and
  sha256 with the board's season, snapshot and anchor (`board_spec-1581a6da7602c3f7`); a pickle
  is opened only after its sha256 matched; the pin is refused when config's preseason anchor is
  not the approved one. **Frozen backtest**: both models' walk-forward on snapshots 2007-2024
  (the boards of 2008-2025), refit with the backtest's own code and refused unless each fold's
  scores equal the walk-forward's: every Cliff player of each board (1,674 rows: the Cliff
  model's evaluation rows are those with 6+ games in S+1, as in the report, but a board shows
  every Cliff player), both chances and ranks (1 = highest, ties by id), the ECR position rank
  (only from a scrape public by the as-of: 2020-2025 were public 6-7 days before), the key
  features, each model's top 3 drivers (coef x standardized value, the Hot-Seat code); outcomes;
  36 fold versions. `twm model check board` reproduces `preseason_cliff.csv`'s rows of both
  models (values, intervals and shares, to 1e-9), `preseason_cliff_seasons.csv` and the
  disagreement tables of `preseason_cliff.md`. `uv run twm board pin` writes it all.
- **The season's board in the pin** (reviewer decision 2026-10-02): when the pin season's as-of
  has passed at approval (2026: yes), `twm board pin` scores that board with the saved model
  files and freezes it in the pin's snapshot: `current_board` (the board's rows, kind
  'backtest' = reconstructed, outcomes pending) and `current_inputs` (the input rows it was
  scored from), each with its sha256 and rows in config/production_models.yaml. Every publish
  takes it from the pin (a store's reconstruction of the same season is dropped), so a fresh
  runner's publish never shrinks it away; `twm model check board` re-scores the frozen inputs
  with the pinned models and must reproduce both chances to 1e-9 (ranks and versions exactly);
  `twm board score` on the Mac says whether its board matches the pinned one.
- **Scoring** `uv run twm board score [--season S1]`: the Cliff rows of snapshot S1 - 1 at the
  pinned anchor (point in time), scored by the pinned models (nothing fitted), stored in the
  predictions store as two rows per player (rank_group `cliff` / `missed`, week 0 = before week
  1). 'live' only when run on the real clock between the as-of and the kickoff (append-only),
  else 'backtest' (reconstructed). Exit 3 before the as-of. The scheduled job has no board
  stage (its preflight loads the pin); every publish carries the pinned boards and a live
  board from the store (frozen in the target once published).
- **Next August** (the 2027 board): the pin for 2027 can be approved once the 2026 labels are
  final, but before the kickoff eve its board's as-of has not passed, so the pin holds no
  season board; the owner runs `uv run twm board score` on the Mac between the kickoff eve
  and the kickoff (stored 'live', published by the next publish from the Mac and frozen in the
  target from then on). A board published earlier from the latest daily depth chart (owner
  decision (1)) and a once-a-season scoring stage in the scheduled job are later steps (I6).
- **Published** (migration `web/drizzle/0005_board.sql`): `board_list` (season S1, week 0,
  snapshot 'preseason', kind, as-of, both model versions), `board_row` (player, his S team and
  position, both chances and ranks, `ecr_rank`, age, prior seasons, games, PPG and rank in S,
  PPG change, touches per game, week-1 depth rank, team change, `dc_absent`, coach change, both
  drivers), `board_outcome` (games and PPG in S1, `y_cliff`, `y_missed`; 'final' / 'pending'),
  `board_track_record` (`preseason_cliff.csv` then `preseason_breakout.csv` row for row) and
  `board_disagreement` (per ECR-era board and model: the model's and the ECR's top-10 picks,
  both, and how many had the label). The glossary's board terms stay unpublished until the
  web step (`publish.collect.UNPUBLISHED_MODULES`).

**The 2026 board** (scored 2026-10-02, after its as-of, Wed 2026-09-09 23:20 UTC, and frozen
in the pin: published as reconstructed, kind 'backtest'; outcomes pending until the 2026 season
ends): 94
Cliff players. Top 10 by the Cliff chance (missed-time chance and rank; preseason ECR position
rank, scrape of 2026-09-04): Darren Waller TE 79.0% (67.3%, #2; TE33), Kenny Gainwell RB 66.0%
(9.9%; RB32), Jake Tonges TE 63.0% (14.9%; TE40), Josh Jacobs RB 56.7% (43.6%, #4; RB47), Dawson
Knox TE 55.6% (11.3%; TE47), Tyler Higbee TE 51.4% (30.1%; TE48), Zach Ertz TE 50.8% (83.4%, #1;
TE64), Christian McCaffrey RB 47.2% (2.7%; RB3), George Kittle TE 41.3% (7.1%; TE10), Rico
Dowdle RB 40.7% (8.3%; RB29).
