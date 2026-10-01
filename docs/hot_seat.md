# Hot-Seat Meter: point-in-time features (step H3a), labels and models (step H3b)

PROJECT_SPEC 8.5. The features (H3a) come first; the labels and the models (H3b) are in the last
two sections.
Labels come from the owner-verified `data/manual/coach_departures.csv` (docs/labeling_coaches.md).

- Code: `src/twm/modules/hot_seat/features.py`; config: `hot_seat:` in `config/settings.yaml`.
- Command: `uv run twm hotseat features` (2002 to the current season), `--season S` or
  `--start A --end B` (rebuilds those seasons and keeps the others in the file), `--now` (only
  as-ofs up to that UTC time; default: now). About 12 s for 2002-2026.
- Output: `data/hot_seat/features.parquet` (git-ignored; zstd). Every column is registered in
  `src/twm/registry.py` (module `hot_seat`; `twm glossary <name>`, docs/glossary.md).

## Rows

One row per **head coach x team x as-of**, key `(season, snapshot, week, team, coach_id)`:

- `snapshot = 'weekly'`: every regular-season week N from 2 to the last one, at
  `dim_week.asof_weekly_utc` (Tuesday 14:00 UTC after week N). `week` = N.
- `snapshot = 'end_of_season'`: one row per team-season, `week` = the last regular-season week.
  Its as-of depends on `hot_seat.end_of_season_anchor` (below).
- A team appears once it has a played regular-season game at the as-of. Its coach (`coach_id`)
  is the head coach of its **latest played regular-season game** (`coach_game`, corrected by
  `data/manual/coach_corrections.csv` since H1b). A coach fired on Monday is still the row's
  coach at Tuesday's as-of: his successor appears with the next game's kickoff.
- Only as-ofs not after `--now` are built: in 2026 (built 2026-10-01) weeks 2 and 3 only.
- `is_last_reg_week` marks the last week's weekly row. **Its Tuesday as-of comes after Black
  Monday** (most end-of-season firings), so H3b should use the end-of-season row there and drop
  that weekly row (or label it with care).

### The end-of-season anchor (owner decision 2026-10-01: `last_game_end`)

| `end_of_season_anchor` | as-of | trade-off |
|---|---|---|
| `asof` (spec 6.1; the code default) | `dim_week.asof_end_of_regular_season_utc`: 12:00 UTC the day after the last regular-season game day (07:00 EST Monday) | one time for the league; a few departures are announced the evening of the finale, before it (docs/warehouse.md "As-of rules") |
| `last_game_end` (**used**, `config/settings.yaml`) | per team: the moment its last regular-season game's rows are public, `max(fact_game.available_at, fact_play.available_at)` of that game (game end + 6 h with today's lags) | a team that finished on Saturday gets its own earlier snapshot. An as-of exactly at the game's end would not see that game at all (its rows publish 3-6 h later), so this anchor is not "the final whistle": with a 13:00 ET finale it is about 22:00 ET Sunday, which can still be after a same-evening announcement |

The owner chose `last_game_end` so that a firing announced the evening of the finale counts in
the end-of-season snapshot. Because the snapshot's as-of can fall a few hours after such an
announcement (above) and `announced_date` is a calendar day, the labels (H3b) compare **dates**:
an end-of-season row is positive when a positive departure is announced on or after the team's
last regular-season game day (US Eastern) and within the spec's 30-day window. Nothing leaks:
the features at that as-of only use games already played.

Both are tested (`tests/test_hot_seat_features.py`): the reference path equals the batch path and
the leakage harness passes with either.

## Point in time

Every input is read through `twm.asof.AsOfView` (reference path, `features_for`) or read once per
season with `available_at` and filtered with `asof_filter` per as-of (batch path,
`features_history`); the tests check both give the same frame. Inputs (each reads one table):
`fact_game` (played games, seasons <= S), `coach_game`, `fact_play` (one row per game and side,
neutral run/pass plays), `fact_schedule` (season S), `fact_roster_week` (QB rows of season S),
`dim_player` (first-round picks of draft S; row rule `public_from_utc`), `dim_team` (static).
The coach, play and grade inputs are joined to the **visible played games**, so a future row
cannot enter through them either.

Two inputs are not warehouse tables:

- **Decision grades** (P2 link): the stored Decision Report Card grades, never regraded: the
  pinned frozen history `artifacts/production_models/decisions/history-<pin>/fourth_downs.parquet`
  (sha256 checked through `config/production_models.yaml`) for 2006-2025, and
  `data/decisions/graded/fourth_downs_<S>.parquet` for a season not in the history (2026). A
  game's grades count only when its `fact_game` row is visible at the as-of.
- **`is_interim`** also reads the owner's departure file (hindsight, below).

Tests (`tests/test_hot_seat_features.py`, offline): one unit test per feature on synthetic
frames; on a synthetic warehouse, the harness (`assert_future_invariant`) at weeks 2 and 3 and at
the end of season, the reference path = the batch path for both anchors, a world where the
firing never happens gives identical rows before it, and a deliberately leaky builder (reading
`wh.fact_game`) fails the harness.

## The features

"To date" = the team's regular-season games of season S whose `fact_game` row is visible at the
as-of (played and final). A tie counts as half a win everywhere.

| Feature | Definition |
|---|---|
| `reg_games_played` | played regular-season games to date |
| `reg_wins` | wins to date (ties 0.5) |
| `expected_wins` | sum over the games to date of the team's pregame win probability (next section); NULL if a game has none |
| `wins_vs_expected` | `reg_wins - expected_wins` |
| `point_diff_per_game` | (points for - points against) / games |
| `pythagorean_wins` | games x PF^e / (PF^e + PA^e), e = `hot_seat.pythagorean_exponent` = 2.37, the NFL exponent Football Outsiders uses ([Wikipedia, Pythagorean expectation](https://en.wikipedia.org/wiki/Pythagorean_expectation), "American football"); NULL when PF + PA = 0 |
| `pythag_minus_wins` | `pythagorean_wins - reg_wins` (positive = unlucky) |
| `off_epa_neutral` | sum(`epa`) / plays over the team's offensive plays with `play_type` in (pass, run), no two-point try, `fact_play.is_neutral` (wp 0.20-0.80, > 120 s left in the half), `epa` not NULL; plays weighted equally (the Radar's `team_epa_per_play_neutral` filter) |
| `def_epa_neutral` | the same over the opponents' plays against the team (`defteam`): EPA allowed, higher = worse |
| `off_epa_neutral_trend`, `def_epa_neutral_trend` | the value over the team's last `hot_seat.trend_games` = 4 played games minus the season-to-date value; NULL until the team has played more than 4 games (the last 4 would be the whole season) |
| `tenure_seasons` | seasons of the coach's **current stint** with the team, this one included: consecutive seasons back from S in which he coached it a game (visible `coach_game` rows). A coach back after years away starts again at 1 (Jon Gruden, LV 2018) |
| `is_first_year_coach`, `is_second_year_coach` | `tenure_seasons` = 1, = 2 |
| `tenure_censored` | the stint reaches the warehouse's first season (1999): the true tenure may be longer (11 coach-teams, e.g. Andy Reid PHI, Bill Cowher PIT, Jeff Fisher TEN) |
| `prev_season_wins` | the team's regular-season wins in S-1, whoever coached (NULL: HOU 2002, an expansion team) |
| `prev_playoff_round` | S-1 playoffs: 0 none, 1 lost wild card, 2 lost divisional (a bye team losing its first game too), 3 lost conference, 4 lost Super Bowl, 5 won it; text in `prev_playoff_result` (none/lost_wc/lost_div/lost_conf/lost_sb/won_sb) |
| `consecutive_losing_seasons` | seasons right before S, counted back until one is not losing or not coached by him, in which the coach coached the team (a regular-season game) and the team's regular-season win share was < 0.5. The team's record, not only his games (an in-season hire inherits the season he joined) |
| `division_rank` | rank within `dim_team.team_division` by win share (wins + 0.5 ties) / games to date, among the division's teams with a game; tied teams share the better rank (`min`). Win share, not raw wins, so a bye week does not cost a place |
| `games_remaining` | season length - games played - a listed cancelled game once gone. Season length = the most regular-season games any team has in the visible `fact_schedule` (a game moved after the release is hidden until its kickoff, e.g. 2017 MIA-TB, so a team's own count can be short). The cancelled 2022 BUF-CIN game (`availability.schedule_exceptions`, absent from nflverse) counts as remaining up to its week's as-of and is gone from week 18 and at season end |
| `starting_qb_changes` | distinct starting QBs (`fact_game.home_qb_id` / `away_qb_id`) in the games to date, minus 1 (A, B, A = 1 change) |
| `rookie_r1_qb_on_roster` | a player listed at QB (`fact_roster_week.position`, point in time) on the team's latest visible weekly roster of S, with a status other than CUT, RET, UFA, TRD, was a first-round pick of draft S (`dim_player.draft_round = 1`, `draft_year = S`). NULL without a visible roster. 2002-2015 rosters are post-game snapshots public a week later (docs/warehouse.md), so the week-2 rows use week 1's roster |
| `took_over_mid_season` | the team's first played regular-season game of S had another coach |
| `fourth_down_wp_lost_per_game` | sum of `wp_lost` on the team's clear (graded) fourth downs (posteam, regular season) in the games to date / games to date. NULL before 2006 (no grades) or when one of the games has no stored grade |

Not features (registered as concepts): `is_interim` and `prev_playoff_result`. Info columns:
`as_of`, `is_last_reg_week`, `market_games_moneyline` / `market_games_spread` (how the games to
date were priced) and `spread_slope` (the season's fitted k).

**`is_interim`** (spec 8.5: interim coaches are excluded from training) = `took_over_mid_season`
OR the owner's `data/manual/coach_departures.csv` says the coach-team-season was interim
(`departure_type = interim_not_retained` or `interim_suspected = true`, joined by `candidate_id`
to `coach_departures_candidates.csv`'s `coach_id`). The file is hindsight (written after the
season), so the flag selects rows and must never be a model feature. Today it adds one
coach-season to `took_over_mid_season`: 2012 NO Aaron Kromer (interim from week 1 while Sean
Payton was suspended). 48 coach-seasons, 382 rows are interim in 2002-2026.

**Not built: coordinator changes** (optional in spec 8.5): the warehouse has no coordinator data.

## The market's expectation

A played game's pregame probability for the home team:

1. **Closing moneylines** (`fact_game.home_moneyline`, `away_moneyline`, American odds) when both
   exist: decimal odds o = 1 + ml/100 for ml > 0, 1 + 100/|ml| otherwise; de-vigged
   p_home = (1/o_home) / (1/o_home + 1/o_away). The away team gets 1 - p_home.
2. Otherwise the **spread** (`spread_line`, > 0 = home favored, docs/assumptions.md section 7):
   p_home = 1 / (1 + exp(-k x spread_line)), no intercept (the line already holds home field, so a
   pick'em is 50%). k is fit **walk-forward** by maximum likelihood (Newton's method) on every
   played game (regular season and playoffs) of the seasons **before** S, ties left out: 0.119 for
   2002 (777 games of 1999-2001), 0.135 for 2006, 0.141 for 2026 (`spread_slope` per row). Derived
   from the data, no published constant. Checked against the de-vigged moneylines on the 5,343
   games 2006-2026 that have both: mean absolute difference 0.016 per game, mean bias -0.0025, and
   the same Brier score on the results (0.2108 spread, 0.2109 moneylines).
3. Neither: the probability is NULL and so is `expected_wins` (does not happen in 2002-2026:
   `spread_line` is present on every played game).

Moneyline coverage, share of the played team-games behind each team-season's latest row priced
from moneylines (the rest from the spread): 2002-2005 0 (no moneylines in nflverse), 2006 0.816,
2007 0.996, 2008 0.723, 2009 0.945, 2010-2016 1.0, 2017 0.996, 2018-2026 1.0.

## The 2026-10-01 build

13,280 rows in 12 s: 544 per season 2002-2020 (16 weekly as-ofs x 32 + 32 end of season), 576 in
2021-2025 (17 + 1), 64 in 2026 (weeks 2-3). No duplicate key; every end-of-season row has
`games_remaining` 0.

NULL shares: `off_/def_epa_neutral_trend` 0.191 (weeks 2-4 and teams with 4 games, by design),
`fourth_down_wp_lost_per_game` 0.164 (2002-2005: no grades; every 2006-2026 game is graded),
`prev_season_wins` / `prev_playoff_round` 0.0013 (HOU 2002); every other feature 0.

## Choices made here (review at H3b)

- `rookie_season` of `dim_player` is hidden in the as-of view (hindsight: it can name a later
  season), so "rookie" is `draft_year = S`. All 77 first-round QBs of 2002-2026 have a roster row
  in their draft season, and the end-of-season counts per season equal the draft counts.
- Tenure counts the current stint (consecutive seasons), not every season ever with the team.
- `consecutive_losing_seasons` and `prev_season_wins` use the team's record, not only the coach's
  own games.
- The last week's weekly row is kept (spec: every weekly as-of) but flagged `is_last_reg_week`.
- `division_rank` uses `dim_team`'s current alignment (static, trusted by the harness): correct
  from the 2002 realignment on, which is where the rows start.
- The 2026 grades come from `data/decisions/graded/` (the regrade output); the publish reads
  `data/decisions/season/graded/` (the scheduled job's `grade-pinned` output). The two files were
  byte-identical on 2026-10-01.

## Labels (step H3b)

Code: `src/twm/modules/hot_seat/targets.py`. The owner's departures
(`data/manual/coach_departures.csv`) join the feature rows by coach x team x season: a schedule
row through `candidate_id` (the candidates file gives its `coach_id`), a `source_only` row through
team + season + the coach's name (`dim_coach`). Every date is a US-Eastern calendar day: a weekly
row's day is its as-of's Eastern date; the end-of-season row's day is the team's last
regular-season game day (`fact_game.gameday`; the `last_game_end` anchor above). The window ends
30 days after the team's final game, playoffs included (inclusive). For a row of day `d` and the
coach's departure from that team announced on day `a`:

| case | label |
|---|---|
| `a < d` | row dropped (he is gone) |
| weekly row of the last regular-season week | dropped (after Black Monday; the end-of-season row covers it) |
| positive (`fired_in_season`, `fired_after_season`, `mutual_parting`) and `d <= a <= window end` | `y = 1` |
| any other type in that span (incl. `resigned_under_pressure`, owner 2026-10-01) | `y = 0`, `censored = True` |
| positive, announced after the window | `y = 0` (counted) |
| blank `departure_type` | the coach-season's rows dropped (counted) |
| interim (`took_over_mid_season`, or the owner file says interim) | kept, `is_interim`: never trained on, scored and reported apart |

Hazard label: `event = 1` when a positive departure is announced in `[d, next row's day)`, or
`[d, window end]` on the coach's last row of the season (one event per positive coach-season).

`--labels verified` uses only rows with `verified_by_owner = y` and refuses to run while any
departure a feature row needs is unverified. `--labels suggested` uses the research prefill and
writes only to `data/hot_seat/provisional/` (git-ignored) with a PROVISIONAL banner;
`reports/hot_seat/` is written only from verified labels.

**Blank `announced_date` (both modes, H3b-2).** The owner may verify a row and leave the date
blank when no source gives the day (docs/labeling_coaches.md); a suggested row may be blank too.
Either way the model uses the coach's `last_game_date` (the earliest day it can be announced;
for an in-season firing his last game's Tuesday row is then dropped). The rows are counted
(`departures_date_imputed`), printed by `twm hotseat backtest` and stated in the report's Labels
section; `twm hotseat check-labels` counts the verified ones. A verified row needs a date or a
`last_game_date`.

## Models and backtest (step H3b)

Code: `models.py`, `backtest.py`, `evaluation.py`, `backtest_report.py`; command
`uv run twm hotseat backtest --labels verified|suggested` (about 2 min); notebook
`notebooks/04_hot_seat.ipynb` (reads the outputs only).

- Walk-forward through the shared harness (`twm.backtest.walkforward`): test seasons 2006-2025,
  each trained on every earlier season from 2002.
- Penalty (H3b-2; `models.InnerCvLogit`, for `logit`, `hazard` and both baselines): L2 only. C
  (grid 0.01, 0.1, 1, the Radar's L2 grid) is chosen inside each fit by an inner walk-forward
  on the training rows alone: for each C and each inner season v (the last 4 training seasons
  that have an earlier one), fit on the training seasons before v and take the log loss on v;
  the C with the smallest sum over the v's wins (ties: the smaller C) and is refit on every
  training season. Fewer than 2 inner seasons: C = 0.1 (the Radar's default). The harness sees a
  one-point grid (no outer tuning); its tuning fit (validation season held out, for the isotonic
  calibration) makes its own inner choice without that season. Before H3b-2 the harness chose C
  and L1/L2 on one validation season, and the choice swung (C = 0.01 L1 <-> C = 1 L2) from fold
  to fold. Chosen C per fold: the report's Penalty section and `backtest_penalties.csv` (every
  candidate's summed log loss). The harness gives a fit feature columns only, so the backtest
  binds the training frame to the estimator, which reads each row's season from it only when
  the rows are exactly a season prefix of it (else the default C, recorded as such).
- 18 features (`models.FEATURES`): all H3a features except the levels that grow with the week
  (`reg_games_played`, `reg_wins`, `expected_wins`, `pythagorean_wins`; their differences are
  in), `tenure_censored` (coverage flag) and `took_over_mid_season` (constant on training rows).
  Missing values: median + missing indicator (decision grades before 2006; the 2006 fold has no
  graded training season, so it cannot use that feature).
- `logit`: the Waiver Radar's penalized logistic regression on `y`. `hazard`: the same on
  `event`, plus a derived final-interval indicator (`games_remaining = 0`); season risk at an
  as-of with g games left = 1 - prod(1 - h) over intervals g, g-1, ..., 1 and the final one, the
  features held at their current values (one interval per game left: a bye week's extra interval
  is not counted). `lgbm`: the Radar's LightGBM (single-threaded), used only
  if it beats both on all test rows (lower Brier and higher PR-AUC, paired intervals excluding 0).
  Baselines: one-feature logistic regressions on win% to date and on `wins_vs_expected`.
- Primary probabilities are the models' own; the harness's isotonic calibration (fit on one
  validation season) is reported beside them. Metrics per as-of week, end of season and all rows
  (ROC-AUC, PR-AUC, Brier, season-block bootstrap intervals with per-row season weights), top-5
  hit rate at week 12 and season end, firings per season, interims and censored rows apart, the
  two sensitivity runs (censored dropped; `resigned_under_pressure` positive), coefficients per fold.

## Research question (step H5)

Does poor fourth-down decision quality raise firing risk, controlling for performance vs
expectation (spec 8.5)? `uv run twm hotseat research` (code `research.py`,
`research_report.py`) -> reports/hot_seat/research.md + .csv; the plain-English write-up is
docs/research_decisions_vs_firings.md.

## Where the labels come from

`data/manual/coach_departures.csv`: every departure type, date and source was researched from cited public pages (mostly each season's Wikipedia "NFL season" page and the coaches' own pages, URL and quote per row, step H1); on 2026-10-01 the owner accepted all of them as true in bulk rather than re-checking each row, and one row the research could not settle (2010 TEN, Jeff Fisher) was filled from his own page. Describe them that way wherever results are shown.
