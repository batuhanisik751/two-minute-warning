# Waiver Radar: the candidate pool (C1), its labels (C2), its features (C3), its models (C4) and their evaluation (C5)

The Waiver Radar ranks players who are **probably still on waivers** (not on any team in a
typical 12-team fantasy league) by how likely they are to become weekly starters soon. Before it
can rank anyone it needs that list of available players, the *candidate pool*, for every
Tuesday of every season we backtest (2013 on). This page explains how the pool is built, why
it is built that way, and how well it matches reality where reality was recorded. The second
half ([Labels](#labels-did-a-pool-player-become-a-starter-step-c2)) explains the right answers
the models learn from: did a pool player really become a starter in the next weeks? The next
part ([Features](#features-what-the-radar-knows-at-the-as-of-step-c3)) explains what the models
learn FROM: each player's situation as it looked on that Tuesday. Then
([Models and backtest](#models-and-backtest-step-c4)) explains how the models are trained and
graded, and the last part ([Evaluation](#evaluation-how-good-and-how-sure-step-c5)) how good
they are, how sure we can be, and where they fail. A guided tour for a first-season fantasy
player: `notebooks/01_waiver_radar.ipynb`.

Code: `src/twm/modules/waiver_radar/pool.py` (the pool), `report.py` (the pool report),
`labels.py` (the labels), `label_report.py` (the label report), `features.py` (the features),
`dataset.py` (pool + features + labels), `feature_report.py` (the feature report),
`expert_ranks.py` (the experts' ranks for the expert baseline), `models.py` (baselines and
models), `backtest.py` (the backtest and its report), `evaluation.py` (the evaluation and its
report), `figures.py` (the evaluation's charts); `src/twm/backtest/walkforward.py` (the
walk-forward harness), `src/twm/backtest/metrics.py` (metrics, the season-block bootstrap,
"caught before it happened"), `src/twm/predictions.py` (the predictions store). Tables:
`fact_roster_week`, `fact_ranking` and `fact_opportunity_week` (docs/warehouse.md). Tests:
`tests/test_waiver_radar_pool.py`, `tests/test_waiver_radar_labels.py`,
`tests/test_waiver_radar_features.py`, `tests/test_waiver_radar_expert_ranks.py`,
`tests/test_waiver_radar_models.py`, `tests/test_walkforward.py`, `tests/test_backtest_metrics.py`,
`tests/test_predictions_store.py`, `tests/test_waiver_radar_evaluation.py`. Reports:
`reports/waiver_radar/pool_sizes.md`, `reports/waiver_radar/labels.md`,
`reports/waiver_radar/features.md`, `reports/waiver_radar/backtest.md`,
`reports/waiver_radar/evaluation.md` (each with a `.csv`; the evaluation's charts are in
`reports/waiver_radar/figures/`).

## Why a stand-in, and which one

A real league knows exactly who is on waivers. Past seasons do not: nobody published which
players sat on fantasy rosters week by week before FantasyPros started saving rostership
percentages in 2020. So the backtests need a rule that says "probably available", and the same
rule must work in every season.

The rule (PROJECT_SPEC 8.1, decided by the owner on 2026-09-27): a player is in the pool when
he is **outside the top N at his position by BOTH** a preseason ranking **and** his fantasy
points per game (PPG) so far this season. A player ranked that high before the season was
drafted in almost every league; a player scoring that well has been picked up by now.

N is the weekly starter threshold (config/league.yaml `starter_rank_threshold`: 12 teams x
starters) times `candidate_pool_multiplier` (1.5): **QB 18, RB 36, WR 36, TE 18**. Change the
multiplier or the league shape and N follows (`League.candidate_pool_cutoffs()`).

The preseason ranking:

- **2020 on (method `ecr`)**: FantasyPros' expert consensus ranking (ECR) on its preseason
  redraft cheat sheet of the player's position. The data decides which seasons have one (the
  archive starts in December 2019).
- **2013-2019 (method `prior_ppg`, the owner's "option 1")**: last season's PPG rank at the
  player's current position among players with at least 4 regular-season games
  (`pool.prior_season_min_games`); rookies drafted in rounds 1-2 (`pool.rookie_drafted_rounds`)
  count as drafted and are never in the pool. A rookie from round 3 on, or anyone who barely
  played last season, is unranked.

## The exact rules

At an as-of (the Tuesday 14:00 UTC after week N, `dim_week.asof_weekly_utc`), everything below
is read through the as-of view (`twm.asof.AsOfView`), so only what was public then is used.

1. **Universe: who is on an NFL roster.** For every player, his latest weekly-roster row of the
   season that is public at the as-of (`fact_roster_week`), **if his team's latest public
   roster lists him**, at QB/RB/WR/TE (the position of that roster week, never today's) with a
   status in `pool.roster_statuses` (ACT active, INA inactive for the game, DEV practice
   squad). Injured reserve (RES), PUP, suspended, cut and retired players are out.
   **Except before 2016:** the 2002-2015 rosters stamp each player's season-END status on every
   week (a receiver who went on injured reserve in December is RES on his September rosters,
   including weeks he played), so using it would leak the future. The pool therefore uses the
   status only once the season's public rosters show some player changing status
   (`statuses_are_weekly`; from week 1-3 of 2016 on), and otherwise keeps every rostered
   QB/RB/WR/TE. Found in C3, fixed in the pool on 2026-09-28; the 2013-2015 pools grew as a
   result.
   - "Latest row per player" matters because a game-day roster leaves out the teams on their
     bye week: those players keep last week's row.
   - "His team's latest roster lists him" matters because a released player often has no CUT
     row: he just disappears from the next roster (the 2016 week-1 roster lists about 77
     active players per team, the whole summer camp; most are gone a week later). Without this
     rule they would stay "rostered" all season.
2. **Preseason rank** (`preseason_pos_rank`, `preseason_source`, `preseason_rank_pos`).
   - `ecr`: the last August/September redraft cheat sheet scraped strictly before week 1's
     first game day (the same selection the id report uses: `twm.ids.preseason_scrape_sql`).
     His rank there is `fact_ranking.pos_rank`: 1 + the number of players of the sheet's
     position with a strictly lower ECR (ties share the better rank). He is ranked on the sheet
     of his **roster** position. If he is not on it (FantasyPros calls him a RB, his roster
     says WR), his rank on his own FantasyPros position's sheet is used and judged against
     that position's N (`preseason_rank_pos` says which). A player FantasyPros lists on another
     position's sheet (a RB on the WR sheet) is ranked among that sheet's players of its
     position. A cheat sheet scraped after kickoff never counts.
   - `prior_ppg`: last season's regular-season PPG, ranked among the players on a roster now
     at his current roster position with at least 4 games last season (a TE who became a WR is
     ranked among WRs). `rookie_draft`: drafted this year (`dim_player.draft_year`) in rounds
     1-2, entered the league this year (`entry_year`).
   - `unranked`: none of the above; the preseason list cannot exclude him.
3. **PPG to date** (`ppg_to_date`, `games_to_date`, `ppg_pos_rank`): fantasy points from
   `config/scoring.yaml` (full PPR) summed over this season's regular-season games that are
   public at the as-of, divided by the number of those games (weeks with a stat line). Ranked
   within the roster position among every player on a roster at that position (any status, so
   a star on injured reserve still holds his spot) with at least one game; ties share the
   better rank (SQL `rank()`).
4. **In the pool** (`in_pool`) unless `preseason_pos_rank <= N` (of `preseason_rank_pos`), or
   `ppg_pos_rank <= N`, or `rookie_draft`. `excluded_by` says why: `preseason`, `ppg`,
   `preseason+ppg`, `rookie_draft`, `rookie_draft+ppg`, or NULL for pool players.
5. **Rostership diagnostics** (`owned_avg`, `owned_espn`, `owned_scrape_date`; never used by
   the pool): FantasyPros' percentage of leagues rostering the player, from the season's latest
   weekly ranking public at the as-of (the Friday before that week's games). A player missing
   from it (his team is on its bye week) takes his row from a scrape up to 8 days older.

Output: one row per universe player, columns `twm.modules.waiver_radar.pool.POOL_COLUMNS`
(season, week, as_of, gsis_id, name, team, position, status, roster_week, method, the columns
above).

## Rosters: two kinds of snapshot (found in C1)

nflverse's weekly rosters are not all taken at the same moment. Among players who played in
week N (a snap-count or stat row that week), the share whose week-N roster status is **not**
ACT is 6.6%-9.8% in 2002-2015 and 0%-0.18% in 2016-2026 (2026-09-27 cache; per season in
`build_manifest.notes.regime_by_season` of `fact_roster_week` and in the report). A player who
played on Sunday and shows RES on that week's roster looked like a roster taken *after* the
game. C3 found the main reason: the 2002-2015 rosters carry the season-final status on every
week (see the universe rule above). Whether their MEMBERSHIP (who is on the team that week) was
also recorded after the games cannot be told from the data, so the one-week delay below is kept
as the safe choice.

So the build measures this per season and sets `available_at` accordingly
(`config/settings.yaml` `availability.roster_postgame_share_threshold`, 1%):

- **game-day** seasons (2016 on): week N's roster is public at week N's Tuesday as-of;
- **post-game** seasons (2002-2015): only at week N+1's as-of (the season's last week: its own
  as-of + 7 days), because it may contain moves made after the Tuesday.

Consequences: in 2013-2015 the week-1 as-of has no public roster and an **empty pool**, and
every later pool uses the previous week's roster.

Other roster facts that shape the pool: practice-squad players (DEV) are listed only from 2017
(about 300 a week, 440+ from 2020; a handful before), and INA only from 2019, so the universe
and the pool are smaller in 2013-2016; the 2016 week-1 roster is the summer camp roster (WR
pool 260 that week, 133 the next). The 2002-2015 rosters spell five teams with the NFL's own
codes (ARZ, BLT, CLV, HST, SL), mapped to today's codes after checking them against the stats
of the same players (`schema.ROSTER_TEAM_ALIASES`).

## Rostership exists (for 2020 on)

PROJECT_SPEC 8.1 says historical rostership does not exist. For 2020 on it does:
`ff_rankings_all` carries `player_owned_avg` (FantasyPros' average across sites; weekly pages:
2020 partly, 2021-2026 fully) and `player_owned_espn` (weekly pages: 2020 partly, 2021-2023).
It covers only the players FantasyPros ranks that week (about two thirds of the universe's
player-weeks in 2021-2023 and 2025, about half in 2020 and 2024; the deep bench has no
figure), and it does not exist for 2013-2019, so the pool cannot be built from it; it is the
check.

## How good is the stand-in (report, 2026-09-27 cache)

`uv run twm radar pool-report` computes every regular-season as-of 2013-2026 (225 as-ofs),
both methods where ECR exists. Full tables in `reports/waiver_radar/pool_sizes.md`.

Typical pool size per as-of (mean over the season's weeks):

| seasons | QB | RB | WR | TE |
|---|---|---|---|---|
| 2013-2015 (`prior_ppg`, post-game rosters, no practice squads) | 52 of 77 | 55 of 101 | 107 of 158 | 67 of 91 |
| 2016 (`prior_ppg`) | 60 of 87 | 97 of 143 | 142 of 194 | 86 of 110 |
| 2017-2019 (`prior_ppg`) | 67 of 94 | 114 of 160 | 189 of 241 | 108 of 134 |
| 2020-2025 (`ecr`) | 80 of 101 | 126 of 166 | 227 of 271 | 127 of 149 |

**ECR vs the fallback (2020-2025).** Computed both ways on the same universe, the two pools
agree closely: Jaccard (players in both pools / players in either) has a per-season mean of
0.934-0.955 and is never below 0.927 at any as-of, and 93.5% (QB) to 95.9% (TE) of
player-weeks get the same verdict. Where they differ, it is 3 to 5 times more often a player in
the ECR pool whom the fallback keeps out (last season's top scorers and every round 1-2 rookie
count as taken) than the reverse.

**Against real rostership (owned_avg, player-weeks with a figure, 2020-2025).** With "owned in
fewer than 50% of leagues" as truly available:

| method | pool & available | pool & owned | not pool & available | not pool & owned | precision | recall |
|---|---|---|---|---|---|---|
| ecr | 31,624 | 1,449 | 1,437 | 10,524 | 95.6% | 95.7% |
| prior_ppg | 29,986 | 1,391 | 3,075 | 10,582 | 95.6% | 90.7% |

Read: about 96 of 100 pool players with a figure were really available (precision; the pool
players without a figure are deep-bench players, nearly all available, so the true precision
is higher), and the ECR pool contains about 96% of the really available players, the fallback
about 91%: the fallback is a little stricter (it keeps out more available players), not
looser. ESPN's own figure (2020-2023) gives the same picture. Per season, and the counts of
pool players owned in 80%+ of leagues and non-pool players owned in under 20%, are in the
report.

**Decision for the owner:** the fallback misses about 5 points of recall against ECR and agrees
with it on about 95% of player-weeks, so option 1 looks fit for 2013-2019. Alternatively, 2020+
backtests could use real rostership (`owned_avg < 50`) as the pool where the figure exists.

## Point in time

Every input comes through the as-of view: rosters (`fact_roster_week.available_at`, the regime
rule above), rankings (`fact_ranking.available_at` = the day after the scrape, 00:00 UTC), stats
(`fact_player_week`, game end + 6 h, so a game moved past the as-of is not counted), last
season's stats (all public long before), draft round (`dim_player`, public from the draft), and
rostership (weekly scrapes public by then). `rookie_year` in the rosters is hidden (17 rows
carry a later season); the pool uses `entry_year`. `tests/test_waiver_radar_pool.py` runs the
leakage harness on the pool at several as-ofs (a split week, post-game rosters) and shows that
reading rosters, stats or rankings from the raw warehouse (`wh.`) fails it; the realdata tests
do the same on the real warehouse.

Known limits: the pool is only as current as the data (a live run must ingest first; see the
progress log's "live runs must check data freshness"); rostership is measured on the Friday
before the week's games, up to four days before the as-of; nflverse's stat corrections are
baked in (docs/warehouse.md); a player traded to a team that is on its bye week drops out of
the universe for that one week (his old team's latest roster no longer lists him, his new
team has no roster that week); PPG counts weeks with a stat line, so a game played without a
recorded stat does not lower a player's average; the current season's rosters are assumed to
stay game-day snapshots (measured on the weeks cached so far).

# Labels: did a pool player become a starter? (step C2)

A model learns from past examples whose answer is known, and it is graded against those answers.
For the Waiver Radar the question at the Tuesday as-of after week N is "will this pool player be
startable soon?", and the **label** is what actually happened in the next few weeks
(PROJECT_SPEC 8.1). Two labels:

- **`y_hit`**: the player had at least one **starter finish** in his **window** (below);
- **`y_sustained`**: at least two (a stricter target: not a one-week spike).

Every universe row of the pool (C1: every rostered player, `in_pool` true or false) gets them,
for as-of weeks 1 to the season's last regular-season week minus 1. Code:
`src/twm/modules/waiver_radar/labels.py`.

## Step 1: weekly finishes (`weekly_finishes`)

Every regular-season week, every player with a stat line that week (`fact_player_week`) gets his
fantasy points (config/scoring.yaml, full PPR) and is ranked at his position by points: rank 1
is the week's best. **Ties share the better rank** (rank method "min": scores 20, 15, 15, 10
rank 1, 2, 2, 4). Everyone with a stat line is ranked, stars included, so "WR24" means 23
receivers scored more that week.

**Which position.** The player's point-in-time position that week, never today's (nflverse's
player table and `fact_player_week.position` show a player's current position for every past
season):

1. his weekly-roster row of that week (`fact_roster_week.position`);
2. without one, his latest earlier roster week of the same season;
3. without one, the position on that game's snap-count row (`fact_snaps.position`);
4. otherwise none (he is not ranked).

On the 2026-09-28 warehouse every stat line of 2013-2026 has a roster row of its own week, so
the fallbacks 2-4 are used 0 times (the report counts them per season). A player who changes
position (a receiver moved to running back) is ranked among receivers until the week his roster
says running back. Only QB, RB, WR and TE are ranked. Fullbacks are not: the 2013-2015 rosters
list them as `FB` (766 stat lines; 28 of them scored at least as much as that week's 24th RB);
from 2016 the rosters have no `FB` position (the 2015 fullbacks are `RB` on the 2016 rosters,
with `depth_chart_position` FB), so they are ranked with the running backs. Kickers and defenders
with a stat line are not ranked either.

A **starter finish** is a rank at or above the weekly starter threshold of the position
(config/league.yaml `starter_rank_threshold` = 12 teams x starters: QB 12, RB 24, WR 24, TE 12;
PROJECT_SPEC 7.2). A **FLEX-worthy finish** (informative, not a label) is a RB or WR ranked in
the top `flex_worthy_rank` (36). Change the league shape in config/league.yaml and both follow.

## Step 2: the window

The window of an as-of after week N is **the next 3 regular-season weeks after N in which the
player's team plays**. "His team" is the team of the pool row (his roster at the as-of). The
schedule is nflverse's final schedule (`fact_game`): labels are written after the fact, so
knowing the final schedule is fine here (features may not, they read `fact_schedule` through the
as-of view).

- **Byes.** A week his team is off is skipped, which extends the window by one week (PROJECT_SPEC
  8.1). Team off in week N+2: the window is N+1, N+3, N+4. Two byes: two extra weeks.
- **Season end.** The window never goes past the season's last regular-season week (from
  `dim_week`: week 17 through 2020, week 18 from 2021). So at the as-of two weeks before the end
  the window has 2 games, one week before the end 1 game, and the last week itself has no label
  rows at all (no window after it). `window_games` counts the games; `is_short_window` = fewer
  than 3; `train_eligible` = at least 2 (PROJECT_SPEC 8.1: "exclude weeks with <2 remaining
  games from training"). Rows with 1 game keep their label (the report shows them) but models
  must not train on them.
- **The cancelled 2022 game.** nflverse's schedule leaves out the cancelled week-17 Buffalo at
  Cincinnati game, so for those two teams week 17 looks like a bye (at the week-14 as-of their
  window is 15, 16, 18; at week 16 it is 18 only).
- **Moved games (split weeks).** The window is by week number: a week-N game played after the
  Tuesday as-of (2020 week 12's Wednesday game and the other split weeks) belongs to week N,
  never to the window of as-of N.
- **Trades.** The window follows the as-of team's schedule. His games for a new team count when
  they fall in the window weeks. A game for the new team in a week his old team was off is not
  in the window (and does not extend it).

## Step 3: the weeks he played

A window week counts as **played** when he has `fact_snaps.offense_snaps > 0` or a stat line
that week, for any team. A blocking tight end with snaps but no stat line played (0 points, no
rank): 9,810 of 80,820 QB/RB/WR/TE player-weeks with offensive snaps in 2013-2025 have no stat
line. A special-teams-only snap row is not played. A week he does not play (injured, inactive,
benched, released) **still uses up a window slot**: only his team's byes extend the window.
`n_window_played` counts the played weeks. A starter finish needs a stat line, so y_hit counts
only weeks he played.

## Step 4: final or pending

A label is **final** once every game of every window week has a final score
(`fact_game.result`) **and** at least one stat line in the cache. Until then it is **pending**:
`y_hit`, `y_sustained` and the counts are NULL, never guessed. Why every game of the week and
not only his team's: his rank compares him with everyone who played that week, so a missing
Monday-night game could move him from 24th to 26th. Why stat lines too: a score can reach the
cache before nflverse's stats; with the score alone everyone in that game would look like he
did not play. (This is stricter than "his team's window games have a score".) On the
2026-09-28 warehouse, 2026 weeks 1-2 are complete and week 3
has one game, so every 2026 row (as-ofs 1 and 2) is pending.

## Point in time: only what happened after the as-of

Labels are hindsight on purpose, so `labels.py` reads the warehouse directly, not through the
as-of view. Two guards keep the direction right (PROJECT_SPEC 6.2 rule 3, "labels strictly after
as_of"):

1. Every stat line and snap row passes `twm.asof.outcomes_after(rows, as_of)` before it is
   joined: only rows that became public **strictly after** the as-of can count. A row stamped
   exactly at the as-of was known then, so it is a feature row and never a label row.
2. The window is restricted to weeks after N by number (so a moved week-N game stays out even
   though it became public after the as-of).

`tests/test_waiver_radar_labels.py` proves both: deleting every event row public at or before
the as-of leaves the labels unchanged (four as-ofs, the split week included); a window-week stat
line stamped at the as-of is ignored and one stamped a microsecond later counts; and the two
deliberate mutations (let rows at the as-of in; start the window at week N) each make a test
fail.

## The label columns

`label_rows(db, pool_rows)` returns the pool rows (in order) plus:

| column | meaning |
|---|---|
| `window_weeks` | the window's week numbers (a list) |
| `window_games` | how many (3; fewer at the end of the season) |
| `is_short_window` | `window_games` < 3 |
| `train_eligible` | `window_games` >= 2 |
| `label_status` | `final` or `pending` |
| `n_window_played` | window weeks he played (snaps or a stat line) |
| `n_starter_finishes` | window weeks with a starter finish |
| `n_flex_finishes` | window weeks with a FLEX-worthy finish (RB/WR; always 0 for QB and TE) |
| `best_rank` | his best weekly rank in the window (NULL if never ranked) |
| `window_ranks`, `window_points` | rank and points per window week (rank NULL without a stat line; points 0 for a snaps-only week, NULL for a week he did not play) |
| `y_hit`, `y_sustained` | `n_starter_finishes` >= 1 / >= 2 |

All of them are registered as labels in `src/twm/registry.py` (docs/glossary.md), so the
feature guard refuses them as model inputs. `labels_history(db, seasons)` = the pool history
(C1) with the labels; `weekly_finishes(db, seasons)` = step 1 on its own.

## Base rates (report, 2026-09-28 warehouse)

`uv run twm radar labels-report` labels every as-of 2013-2026 (212 as-ofs, 128,643 rows, 99,181
of them in the pool; about 15 s). Full tables per season in `reports/waiver_radar/labels.md`.
Train-eligible rows with a final label, walk-forward evaluation seasons 2014-2025 pooled:

| rows | QB | RB | WR | TE | all |
|---|---|---|---|---|---|
| pool: y_hit | 9.3% | 13.3% | 10.8% | 9.8% | 10.9% (of 87,848) |
| pool: y_sustained | 2.6% | 3.7% | 2.2% | 1.9% | 2.5% |
| outside the pool: y_hit | 71.1% | 68.4% | 64.8% | 64.1% | 66.9% (of 25,415) |
| outside the pool: y_sustained | 37.2% | 41.2% | 30.1% | 30.1% | 34.8% |

Read: about 1 pool player in 9 has a starter week within his next three games, and 1 in 40 has
two. That is the precision a model that picks pool players at random would get; the Radar must
beat it. Players outside the pool (drafted high or scoring well) hit six times as often, as they
should. 2013-2015 pool rates are higher (about 15%) because those universes are smaller (no
practice-squad or inactive players on those rosters, see above), so fewer deep-bench players
dilute them.

Where rostership exists (2021-2025), the pool rows that real leagues had rostered in at least
50% of leagues are few (1,106 of 44,545, 2.5%) but hit often (51.9%) and give 13% of the pool's
hits (574 of 4,423); pool rows with a figure under 50% hit 13.9%, and the deep bench without a
figure 2.3%. A model will find those "not really available" players easily, which flatters its
precision; C4 should report its metrics with and without them.

## Known limits of the labels

- **The roster decides the position.** A player whose role differs from his roster position (a
  roster quarterback used as a tight end, say) is ranked at his roster position, which may not
  be the position a fantasy site lists him at.
- **Fullbacks in 2013-2015** are not ranked with running backs (see step 1).
- **Stat corrections** are baked into nflverse's stats (docs/warehouse.md); a label uses the
  corrected numbers, which is what a league's final scores use too.
- **Snap counts can arrive later than stats** in the live season. That only changes
  `n_window_played` (a snaps-only week has no rank), never `y_hit`.
- **The window follows the as-of team**, so a trade in the middle of the window can make it
  cover a week the player sat out or skip a week he played (see step 2).

## Commands

```bash
uv run twm radar pool 2023 6                 # the pool at 2023 week 6's as-of, best PPG first
uv run twm radar pool 2023 6 --pos RB --all  # every rostered RB, with why each one is out
uv run twm radar pool 2019 6 --method prior_ppg --limit 0
uv run twm radar pool-report                 # reports/waiver_radar/pool_sizes.md + .csv
uv run twm radar labels 2023 6               # the labelled pool at 2023 week 6's as-of
uv run twm radar labels 2023 6 --pos RB --hits-only
uv run twm radar labels 2023 6 --all         # every rostered player, in the pool or not
uv run twm radar labels-report               # reports/waiver_radar/labels.md + .csv
```

`pool_history(db, seasons)` (Python) gives every as-of of the seasons in one frame: a week
counts once all its games that should be over by its as-of have a final score in the cache, so
the current season stops at the last complete week.

# Features: what the Radar knows at the as-of (step C3)

A model can only be as good as what it is told. For every pool row (every rostered QB, RB, WR
and TE at the Tuesday as-of after week N) `features.py` describes the player's situation **as
it looked that Tuesday**, in four families (PROJECT_SPEC 8.1). Every feature is registered in
`src/twm/registry.py` with its exact formula (`uv run twm glossary snap_share_delta`,
`docs/glossary.md`); `registry.check_features` refuses anything else, identifiers included.

## The windows: last game, last 3, season

Most opportunity features look back over **team games**: the regular-season games of the
player's team at the as-of (the pool row's `team`) that are visible at the as-of.

- `_last` is the team's most recent visible game; `_avg3` the mean over its last 3 (fewer in
  weeks 1-2); `_season` the mean over all of them; `snap_share_delta` = last game minus the
  mean of the up to 3 games before it.
- **A game he missed counts as 0.** An injured, benched or inactive week is a real 0 in his
  share, not a gap. A player traded in counts 0 in his new team's games before he arrived.
- A feature is NULL only when his team has no visible game yet (its first game was moved past
  the as-of, or it was off in week 1). At the week-1 as-of `last`, `avg3` and `season` are the
  same one game and the delta is NULL.

## Opportunity: what the player is given

Fantasy points follow opportunity, and opportunity is steadier week to week than efficiency,
so this family carries most of the signal.

- **Snap share** (`snap_share_last`, `_avg3`, `_season`, `_delta`): the share of his team's
  offensive snaps he played (Pro Football Reference, through `fact_snaps`). Coaches show a new
  role in snaps before the box score does.
- **Target share, air-yards share, WOPR** (`target_share_last`, `_avg3`, `air_yards_share_avg3`,
  `wopr_avg3`): nflverse's per-game shares of his team's targets and air yards.
- **Carry share** (`carry_share_last`, `_avg3`): his carries / his team's carries
  (`fact_team_week.carries`, which equals the sum of the team's player carries in every game
  2013-2025).
- **Red zone and goal line** (`rz_targets_avg3`, `rz_carries_avg3`, `gl_opps_avg3`): targets and
  runs from play-by-play inside the opponent's 20 (targets + runs inside the 10 for the goal
  line), per game. Two-point tries do not count, nor do kneel-downs.
- **Routes proxy** (`routes_proxy_avg3`): team dropbacks in the game (plays with
  `qb_dropback = 1`: passes, sacks and scrambles, not two-point tries) x his snap share. nflverse
  has no route counts, so this estimates how many pass plays he was on the field for.
- **xFP and FPOE** (`xfp_last`, `xfp_avg3`, `fpoe_avg3`): expected fantasy points, what an
  average player would have scored from the same targets and carries, and his points above
  that (mostly luck, so it tends to shrink back; see below).
- **Points** (`fantasy_points_last`, `_avg3`) with config/scoring.yaml, and the pool's columns:
  `ppg_to_date`, `ppg_pos_rank`, `preseason_pos_rank` (NULL when no preseason list ranked him;
  `preseason_ranked` says whether one did), `games_played_to_date`.

### xFP: re-scored from ffopportunity

`fact_opportunity_week` (new in C3, docs/warehouse.md) holds nflverse's ffopportunity weekly
file: for every player-game the actual and the **expected** passing, rushing and receiving
yards, touchdowns, two-point conversions, interceptions and receptions, from ffopportunity's
models (which were trained on many seasons: the spec 6.3 caveat). xFP scores those expected
stats with **our** config (`twm.scoring.xfp`), so it follows a league's settings. Lost fumbles
and return or fumble-recovery touchdowns have no expected value: they add nothing to xFP, and
FPOE = points - xFP carries them in full.

**Check on real data** (`uv run twm radar features-report`, "xFP check"): with the weights
ffopportunity uses for its own points (nflverse-PPR: 0.04 per passing yard, 4 per passing
touchdown, -2 per interception, 1 per catch, 0.1 per rushing or receiving yard, 6 per touchdown,
2 per two-point conversion), our xFP reproduces its `total_fantasy_points_exp` on 106,463 of
106,560 player-games (99.9%) within rounding. Why "within rounding": ffopportunity stores every
expected component and its three point subtotals with 2 decimals and computed the points from
the unrounded values, so up to 0.14 points of difference is rounding alone (only 14% match to
the cent). The other **97 player-games are rushing two-point tries**: ffopportunity's per-play
rushing file shows that its `rush_fantasy_points_exp` adds 0.1 x the expected yards of a
two-point try, which its own `rush_yards_gained_exp` leaves out (and real scoring does not pay
for). Adding those yards back from the per-play file (`ff_opportunity_rush`, not in the
warehouse) leaves 0 player-games outside the rounding bound (largest difference 0.079). We keep
our version: a two-point try's yards are not fantasy yards.

## Role change: who is out, who moved up

**Depth chart** (`depth_rank_now`, `depth_rank_prev`, `depth_rank_change`, `depth_listed`).
The chart in force at the as-of: from 2025 the team's latest daily snapshot taken at or before
the as-of; before 2025 the latest weekly chart that is public (week N's chart counts from the
Wednesday before week N, so at the Tuesday after week N it is week N's). His rank is his place
in his position group: 1 + the number of group players with a better depth rank in any
offensive slot of the group (ties share the better rank, so three starting receivers are all
1 and the next receiver is 4). `_prev` is the same on the chart in force 7 days earlier, and
`depth_rank_change` = previous - now (positive = promoted). `depth_listed` says whether he is
on the chart at all (special teams included; NULL when his team has no public chart).

Slot names map to position groups (`features.slot_group`, from the data: the weekly charts spell
slots dozens of ways). A slot is split into its letters: any TE part makes it a tight-end slot
(`TE/HB`, `HB-TE`, `FB/TE` hold tight ends on the weekly rosters), otherwise the first part
decides: QB; RB, HB, FB and J (a jumbo back) are running backs; WR, LWR, RWR, SWR, WR1, WR2,
WRE and WE are receivers (`WR\8`, `RB\``, `RB86` are typos of those); TE, LTE, RTE, H-B (an
H-back) and F (both mostly tight ends on the rosters) are tight ends. Offensive-line slots and
junk values (a newline, `19`) are not mapped.

**Teammates who became unavailable: the key waiver signal.** When a starter goes down, his
targets and carries go to somebody. A **teammate** is a QB, RB, WR or TE of the player's as-of
team who played for it this season (offensive snaps or a stat line in a visible team game). He
is **unavailable** at the as-of if ANY of these rules fires (each one is recorded, in this order,
so the app can say why):

1. `roster_status`: on the team's latest public weekly roster his status is not ACT, INA or DEV
   (config/league.yaml `pool.roster_statuses`): injured reserve (RES), PUP, suspended, cut ...
   Only in seasons whose statuses change from week to week: the 2002-2015 rosters repeat one
   status all season, the one the player ended the season with (a receiver who went on injured
   reserve in December is RES on his September rosters too), which would reveal the future
   (docs/assumptions.md section 15). The rule turns itself on once the public rosters of the
   season show any player with two different statuses, which never happens before 2016;
2. `left_team`: he was on the team's roster earlier this season but is not on its latest public
   roster (released or traded; a released player often has no CUT row, he just disappears);
3. `injury_report`: the team's latest public injury report of the season lists him Out or
   Doubtful;
4. `missed_last_game`: no offensive snap and no stat line in the team's last game, after
   averaging at least 50% of the snaps in the up to 3 team games before it. A backup who
   missed a game is not "out" in any way that frees opportunity, so he does not count.

From those teammates:

- `vacated_target_share` / `vacated_carry_share`: the sum of their target (carry) shares
  averaged over **their last 3 team games up to their last appearance**, i.e. before they became
  unavailable (games they missed inside that window count as 0). A receiver on IR since week 5
  keeps the share he had in weeks 3-5; it is not diluted by the weeks he has missed since.
- `same_pos_vacated_target_share`, `same_pos_vacated_carry_share`,
  `teammate_same_pos_unavailable`: the same restricted to his own position group.
- `top_teammate_out`: an unavailable teammate of his position averaged a higher snap share than
  he did over the same games (the teammate's window): someone who played ahead of him is out.
- `joined_team_recently`: his latest roster team differs from his first one this season.
- `teammates_out` (not a feature): the list, as JSON text, with each teammate's name, rules,
  last week played and shares, for the plain-English reasons of step C6.

Three real examples from the dataset (2026-09-28 cache; `twm radar features SEASON WEEK --team
X --all` shows them): at the 2014 week-11 as-of Denver's Ronnie Hillman was out (Out on the
report and no snap in the last game; 60% of the snaps and 49% of the carries in his last three
games before that), and C.J. Anderson, in the pool, had just played 93% of the snaps (up 68 points) and
taken 90% of the carries: he ranked RB 2, 3 and 7 in the next three weeks. At the 2015 week-9
as-of Chicago's Matt Forte was out (Out, missed the last game; 62% of the carries) and Jeremy
Langford (75% of the snaps, 62% of the carries in the last game) ranked RB 1, 17 and 17. At the
2024 week-14 as-of San Francisco's Christian McCaffrey was on injured reserve on the latest
roster and had missed the last game (55% of the carries), and Jordan Mason was on injured
reserve too (25%); Isaac Guerendo (56% of the snaps, up 47 points) ranked RB 24, did not play,
then ranked RB 11.

## Context: the team and the schedule

- **Team offense** (`team_epa_per_play`, `team_epa_per_play_neutral`, `team_plays_per_game`,
  `team_pass_rate_neutral`): his team's expected points added per run or pass play so far (all
  plays and in neutral situations, docs/warehouse.md "Garbage time"), its plays per game (pace:
  more plays, more chances) and how often it passes when the score does not force it.
- **Next opponents** (`opp_fp_allowed_next3`, `n_opp_games_seen`): for each of his team's next
  3 opponents (the public schedule, weeks after N, byes skipped: the same window as the labels),
  the fantasy points its defense allowed per game this season to players of his position
  group, divided by the league average for that group (1.0 = average, above 1 = a soft
  matchup), averaged over the 3. A scorer's position is his snap-count position in that game
  (else his latest roster position). An opponent with no game yet counts as 1.0;
  `n_opp_games_seen` says how much evidence there is (little in September). **No betting lines**
  (spec 6.4): an upcoming game's line does not exist at a Tuesday as-of.
- **Byes and games left** (`bye_in_next3`, `team_games_remaining`): whether weeks N+1 to N+3
  contain a bye of his team (a week without a scheduled game, up to the last regular-season
  week), and his team's regular-season games after week N. The cancelled 2022 week-17 Buffalo
  at Cincinnati game (absent from nflverse) is counted as scheduled until week 17, as it was
  then (config `availability.schedule_exceptions`).

## Player

`position` (a category: QB, RB, WR, TE; not an identifier), `age_at_asof` (from the birth
date), `is_rookie` (his roster's entry year is this season), `draft_round` (NULL when undrafted,
with `is_undrafted`), `years_exp` (his latest public roster row).

## Timing: what is and is not known on Tuesday

Every input is read through the as-of view (docs/warehouse.md), so a feature only ever sees rows
public at the as-of. What that means in practice:

- **The next week's injury report does not exist yet.** Reports for week N+1 come out Wednesday
  to Friday; at the Tuesday as-of the latest report is week N's (for the game just played). A
  starter hurt on Sunday shows up through `missed_last_game`, the week-N report or the roster.
- **Monday and Tuesday roster moves appear in the next roster.** From 2016 a week's roster is
  the game-day list; a player moved to injured reserve on Monday is still ACT on it and shows RES
  only on next week's roster (public at next week's as-of). In 2002-2015 rosters were taken
  after the games and are public a week later still (see "Rosters: two kinds of snapshot").
- **Depth charts:** the daily chart taken at 10:00 UTC on Tuesday is in force at 14:00; the
  one taken at 15:00 is not. Weekly charts before 2025 carry week N's chart until the next
  Wednesday.
- **A game moved past the as-of** (the split weeks) is not a team game yet: at the 2020 week-12
  as-of Baltimore's and Pittsburgh's last game is their week-11 game.
- **Model columns:** EPA (team offense) and ffopportunity's expectations (xFP, FPOE) come from
  models trained on many seasons (spec 6.3): a mild, known leak in backtests, flagged in the
  registry.

`tests/test_waiver_radar_features.py` runs the leakage harness on `features_for` at four
fixture as-ofs (a week-1 as-of, a split week, weekly charts), shows that deliberately leaky
variants fail it (the next week's injury report, a teammate's future roster status, "last 3
games" chosen by week number at a split week, a depth chart taken after the as-of) and that
every input is filtered to the as-of a second time in memory, so even a read around the view
(`wh.`) cannot leak through the reference path. The realdata tests run the harness on two real
as-ofs (2014 week 6, 2020 week 12).

## Two paths, one answer

`features_for(view, season, week, pool_rows)` is the reference: everything read through one
as-of view. `features_history(db, pool)` is the fast path for history: each season's inputs are
read once with their `available_at` and filtered per as-of in memory. Every input is either one
table's rows or a per-game total of one table (all rows of a game become public together), and
tables are joined only after filtering, so both give the same numbers; the tests compare them on
every fixture as-of and on six real ones (2013 week 2, 2014 week 6 with post-game rosters, 2016
week 9, 2020 week 12 a split week, 2023 week 6, 2025 week 10 with daily charts).

## The dataset and the feature report

`uv run twm radar dataset` writes `data/waiver_radar/dataset.parquet` (gitignored): every
labelled pool row of 2013-2026 (C1 pool columns, then the features, `teammates_out`, the
experts' ranks of C4, then the C2 labels), one row per (season, week, player), sorted. On the
2026-09-28 warehouse (rebuilt in C4): 134,113 rows (103,826 in the pool), 86 columns, 9.5 MB,
built in about 35 s; two builds give byte-identical files. The four expert columns
(`ecr_available`, `ecr_pos_rank`, `ecr_page_kind`, `ecr_scrape_date`, see
[the experts' baseline](#the-baselines-what-the-models-must-beat)) are registered as metrics,
so no model can use them; every other column is unchanged by C4.

`uv run twm radar features-report` reads it and writes `reports/waiver_radar/features.md` (and a
CSV): for the rows a model will learn from (in the pool, final label, train-eligible,
2014-2025: 87,848 rows, 10.9% hits), per feature and position, the share missing, the mean for
players who hit and who did not, and the **single-feature AUC**: the chance a random hit has a
higher value than a random miss (0.5 = no signal). It is a sanity check, not a model. The
strongest single features are the opportunity ones (xFP, snap share, target or carry share,
the routes proxy: AUC 0.82-0.87 by position): most pool players are deep backups who never see
the field, so "does he play at all" separates a lot. The teammate features are weak on their own
(AUC about 0.51-0.53): a starter being out matters for the player next in line, not for the
whole pool, which is what a model (C4) combines with his own snaps and depth-chart rank. A
quick look at combinations (same rows): players whose same-position teammate who played ahead of
them is out and whose snap share just rose by 20 points or more hit 24.0% of the time (4,489
rows), players at depth rank 1-2 with at least 30% of the position's targets and carries
vacated 26.2% (1,717 rows), against 10.9% for all pool rows.

## Commands

```bash
uv run twm radar features 2023 6              # the pool at week 6's as-of with its key features
uv run twm radar features 2023 6 --team min --all   # one team, everyone, and who is out and why
uv run twm radar dataset                      # data/waiver_radar/dataset.parquet (2013-2026)
uv run twm radar dataset --start 2020 --end 2021 --out /tmp/ds.parquet
uv run twm radar features-report              # reports/waiver_radar/features.md + .csv
uv run twm glossary vacated_target_share      # one feature's exact definition
```

The features need `fact_opportunity_week`, new in C3: a warehouse built before C3 is refused with
a message; rebuild it once with `uv run twm build --start 1999` (about a minute).

## Known limits of the features

- **The universe of teammates** is the players who played for the team this season: a star on
  injured reserve since the summer vacated nothing this season and is not counted.
- **Long absences keep counting.** A teammate out since week 2 still adds his week-1/2 shares
  in week 12, when the team has long since redistributed them (a "recently vacated" variant is
  an open question for C4).
- **Fullbacks** on 2013-2015 rosters (position FB) count as running backs among teammates.
- **2013-2015 rosters carry season-final statuses** (docs/assumptions.md section 15): the
  teammate rule `roster_status` is off for those seasons (the other three rules still work), and
  the C1 pool universe, which keeps statuses ACT, INA and DEV, leaves out about 30-42 rostered
  players per as-of who were healthy then but ended the season on a reserve list. An open
  question for the owner before C4 trains on 2013-2015.
- **Snap counts start in 2013**, so the dataset does too; xFP starts in 2006.
- **Live runs** must ingest play-by-play, snaps and ffopportunity before scoring a week: a game
  whose ffopportunity rows are missing would count 0 xFP (docs/progress.md "live runs must
  check data freshness").

# Models and backtest (step C4)

The Radar now has everything it needs to learn: the pool (who to rank), the features (what it
knows on Tuesday) and the labels (what happened next). Step C4 trains models on that, and, more
importantly, measures honestly how good they are. Code: `src/twm/backtest/walkforward.py` (the
harness, shared by every later module), `src/twm/backtest/metrics.py`,
`src/twm/modules/waiver_radar/models.py`, `src/twm/modules/waiver_radar/backtest.py`,
`src/twm/predictions.py`. Report: `reports/waiver_radar/backtest.md` (+ CSV).

## Walk-forward: grading a model the honest way

A model that is graded on games it learned from looks brilliant and is useless. So every
prediction in the backtest is made the way it would have been made live:

- To grade season S (every season from 2014 to 2025), the model learns **only from the seasons
  before S**. For 2020 it learns from 2013-2019, then ranks the pool every Tuesday of 2020, and
  those rankings are compared with what happened. Then the same for 2021 (learning from
  2013-2020), and so on. Twelve seasons of predictions, none of which the model had seen.
- Models have settings ("hyperparameters", e.g. how strongly the logistic regression is held
  back from over-fitting) that must be chosen too. They are chosen on the **last season before
  S** (the validation season): a model trained on the seasons before it is tried with each
  setting and graded on that season. The winning setting is then refit on every season before S.
- The first test season, 2014, has only 2013 to learn from, so there is nothing to validate on:
  it uses default settings (a "thin" fold; the report shows 2015-2025 separately too).
- Everything that is learned from data, including how missing values are filled and how numbers
  are scaled, is learned from the training rows only (a scikit-learn `Pipeline`).
- The harness **refuses** to train, tune or calibrate on any row of the test season or later: it
  raises `TestSeasonInTrainingError` instead (tested, including a check that the test fails if
  the guard is switched off). It also refuses any column that is not a registered feature:
  player ids, team codes, labels and the metric columns (`twm.registry.check_features`).
- No refit during a season: the model trained before 2020 ranks every week of 2020.

A test goes further than the guard: it scrambles the features and flips the labels of half of a
test season's rows and checks that the predictions of the other half do not change by a single
bit, for both models, in the thin fold and a tuned fold. Nothing about the test season can reach
the model.

## Precision@10: what it means for you

Every Tuesday the Radar gives one list per position (QB, RB, WR, TE), best first. **Precision@10**
is the share of the top 10 of a list who became a fantasy starter (`y_hit`: at least one week in
the top 12 QB/TE or top 24 RB/WR) within their next 3 games. If you had picked up the Radar's top
10 at a position that week, it is the share that would have given you a starter week. The pooled
number is the average over every list of the season(s): 732 lists in 2014-2025 (about 15
Tuesdays x 4 positions per season). A list with fewer than 10 players divides by its size (none in
the real data). For `y_sustained` (at least two starter weeks) everything is the same.

About 1 pool player in 9 hits (10.6% in 2014-2025): that is the precision of a random pick, the
floor every method must clear.

## The baselines: what the models must beat

- **Last week's points** (`baseline_last_points`): rank by fantasy points in his team's last game.
  What most people do.
- **Snap-share change** (`baseline_snap_delta`): rank by the jump in his share of the offensive
  snaps (last game vs the 3 before). At a week-1 as-of nobody has a change yet, so those lists are
  in id order (a coin flip); that costs this baseline about a point.
- **The experts** (`baseline_ecr`): rank by FantasyPros' positional rank that was public on the
  Tuesday: the latest rest-of-season page, else the latest weekly page (a weekly page leaves out
  teams on their bye, so a page up to 8 days older counts for them). The archive starts in
  December 2019, so this baseline exists for 2020-2025 only, on the 356 of 380 lists where a page
  existed (2024's first pages came in week 4). Two thirds of the pool rows carry an expert rank,
  and 98% of the hits do; an unranked player goes to the bottom. The pages were saved on Fridays,
  so on Tuesday the experts had not seen the weekend's games yet: a real handicap of this
  baseline, since the Radar has. These columns (`ecr_pos_rank` ...) are metrics in the registry;
  no model may use them.

A player without a value (no game yet, no expert rank) is ranked below everyone with one; ties go
to the smaller player id. Every method ranks exactly the same rows, with the same rules (tested).

The report adds one line of **context**, not a baseline: the same lists ranked by `xfp_avg3`
(expected fantasy points over his last 3 games) alone, the strongest single feature of the C3
feature report. It shows how much the models add over the best single number.

## The two models

- **Logistic regression** (`logit`): a weighted sum of the features turned into a probability.
  Missing values are filled with the training median (plus a yes/no "was missing" column), every
  column is standardized (so weights are comparable), position becomes four yes/no columns. The
  weights are held back ("regularized") so it does not chase noise; how strongly (C) and how
  (L1 = drop weak features entirely, L2 = shrink all of them) are chosen on the validation season.
- **LightGBM** (`lgbm`): hundreds of small decision trees, each correcting the previous ones. It
  can learn interactions (a snap-share jump matters more when the starter ahead of him is out)
  and handles missing values itself. Settings tried on the validation season: tree size
  (15 or 31 leaves), minimum players per leaf (50 or 200), share of features per tree (70% or
  all); the number of trees stops when the validation season stops improving.

Both are deterministic: fixed seeds, LightGBM's deterministic mode with a fixed thread count; two
runs give identical predictions (tested, and checked on the full backtest).

## Why calibrate

A model's raw score ranks players but is not an honest probability: "0.30" might really mean
20%. The Radar will show probabilities, so after training, an **isotonic calibration** is fit on
the validation season: it maps each raw score to the hit rate that similar scores really had
there (only ever upward with the score, so the ranking stays the model's). It is then applied to
the refit model's scores (a common compromise: the refit model saw one more season than the
calibrator). In the thin 2014 fold the calibration uses 4-fold cross-fitting inside 2013: the
weeks are split into four groups and each group is scored by a model that did not see it.
Calibration can put nearby scores on the same probability; the list order then follows the raw
score, so it never changes the ranking. The Brier score (the mean squared error of the
probability) grades the calibrated probabilities.

## Players real leagues had rostered

The pool is a stand-in for "on waivers" (C1). Where FantasyPros saved rostership (2020 on), a few
pool players were owned in at least 50% of real leagues; they hit about half the time (C2), so a
model finds them easily and looks better than it is for someone scanning a real waiver wire. Every
precision number is therefore also shown **without** them: they are removed and the lists
re-ranked. 2014-2019 have no rostership figures, so nothing is removed there.

## Results (2026-09-28, `uv run twm radar backtest --label y_hit --label y_sustained`)

`y_hit`, pooled precision@10 (base rate 10.6%, 732 lists, 9,726 hits in 91,638 rows):

| method | 2014-2025 | without rostered | 2015-2025 | without rostered |
|---|---|---|---|---|
| last week's points | 40.4% | 38.9% | 40.7% | 39.2% |
| snap-share change | 24.6% | 23.8% | 24.6% | 23.6% |
| logistic regression | **47.8%** | **45.5%** | **48.0%** | **45.5%** |
| LightGBM | 47.6% | 45.2% | 47.8% | 45.2% |
| (context) xFP last 3 games alone | 45.1% | 43.0% | 45.0% | 42.7% |

- **Acceptance (spec P1): PASS.** The better model beats both naive baselines, by 7.4 points over
  last week's points and 23 points over the snap-share change, in every one of the 12 seasons.
- **Winner: the logistic regression**, by 0.2 points over LightGBM. That gap is noise (the
  logistic regression is ahead in 7 of the 12 seasons); by the rule "higher pooled precision@10, ties to the simpler model"
  the logistic regression is the Radar's model. It is also the better calibrated one (Brier
  0.0736 vs 0.0744; always predicting the training hit rate scores 0.0950) with the higher PR-AUC
  (0.433 vs 0.418).
- **Per position** (logistic regression vs last week's points): RB 54.2% vs 46.3%, WR 50.8% vs
  40.4%, TE 42.8% vs 31.7%, but QB 43.4% vs 43.1%: at quarterback the model adds nothing over
  last week's points (a pool QB who scored well last week is usually a new starter).
- **By rank:** the top 5 of a list hit 56.4% of the time, ranks 6-10 39.3%, ranks 11-25 24.0%.
- **The experts** (2020-2025, the 356 lists with a page): FantasyPros 46.9%, the logistic
  regression 48.4%, LightGBM 48.5%; without rostered players 42.0% vs 43.7% and 43.5%. The
  models edge the experts by about 1.5 points, not more, and lose to them in 2024 (48.1% vs
  44.7%), even with the experts' Friday handicap.
- **Calibration** of the winner: in each tenth of the predictions (lowest to highest) the mean
  probability is within about a point of the observed hit rate (top tenth: 48.3% predicted,
  47.3% observed).
- **y_sustained** (two starter weeks, base rate 2.5%): logistic regression 16.0%, LightGBM 15.4%,
  last week's points 12.6%, snap-share change 7.3%; PASS as well. Experts on their lists: 15.7%
  vs the logistic regression's 16.6%.
- **Runtime:** about 2 minutes for `y_hit` with all five methods, 4 minutes for both labels.

## The importance check (spec 6.2 rule 6)

After every fold the report lists each model's most important features and flags any feature
holding more than 40% of the total, the classic sign of a leak.

- **Logistic regression:** importance = how much a feature moves the log-odds across the training
  rows (the spread of its term). No feature is above 24% in any fold; the leaders are snap share
  in the last game, points per game and its rank, age (younger players break out more often),
  position (which shifts a whole position's list alike and cannot change a ranking within it) and
  `team_games_remaining`, which is public schedule knowledge: in weeks 1-2 (15-16 games left) only
  5-7% of pool rows hit (the lists are full of camp bodies), with 2 games left the window is
  shorter (9.2%), in between about 11%.
- **LightGBM** puts 32-52% of its gain on `xfp_avg3` (expected fantasy points over the last 3
  games) from 2015 on, above 40% in 8 of the 12 folds, so it is flagged. Investigated: `xfp_avg3` is read point-in-time like
  every feature (C3 tests), and on its own it already ranks lists at 45.1%. It is the single best
  summary of recent opportunity (targets, carries and where they came), and a tree model leans on
  its best split. Removing all three ffopportunity columns (`xfp_last`, `xfp_avg3`, `fpoe_avg3`)
  costs LightGBM 0.1 point (47.5%) and the logistic regression nothing (48.0%): the model moves its
  weight to recent fantasy points and snap share. So it is not a leak, and the ffopportunity
  model columns (spec 6.3, trained on many seasons) do not drive the results.
- A missing age is informative (0.2% of such rows hit): the as-of view only knows a player's
  birth date once he was drafted or appeared in a game, depth chart or injury report, so a
  missing age means "undrafted and never seen on the field yet". That is known on Tuesday, not
  a leak (it is also rarer in 2025, whose daily depth charts list practice-squad players).

## The predictions store (the time machine's memory)

Every backtest prediction is saved in `data/predictions.duckdb` (gitignored; `paths.predictions`
in config/settings.yaml): table `predictions` (player, season, week, as-of, horizon 3, the
probability, the raw score, the rank in its position list, `kind` 'backtest'), `outcomes` (the
labels) and `model_versions` (model, label, features, settings, training seasons, the dataset's
content hash, code version). A `model_version` is a fingerprint of the trained model: the model,
label, features, settings (fixed ones included), training and test seasons and the content of
the rows it learned from, never the time it was made. The same trained model keeps its version
whatever weeks it scores (the live season will need that); the content of the rows it scored
is noted beside it. Re-running the backtest replaces the rows of the same versions (833,382
predictions for both labels and five methods). `band` and
`reasons_json` stay empty until step C6.

## Commands

```bash
uv run twm radar backtest                     # all methods, y_hit, 2014-2025 (about 2 minutes)
uv run twm radar backtest --label y_hit --label y_sustained
uv run twm radar backtest --start 2016 --end 2017 --model logit --model baseline_last_points
```

Options: `--dataset` (default `data/waiver_radar/dataset.parquet`; rebuild it with
`uv run twm radar dataset` after any upstream change), `--store`, `--out`.

## Known limits

- **Precision is flattered by the proxy pool** in two ways the report makes visible: rostered
  players (2020 on; the "without rostered" columns) and, before 2020, a pool built from last
  season's points (C1) whose own recall is a little lower.
- **The two models are tied.** The winner is picked by a fixed rule; a different random season
  split could flip it. Both beat the naive baselines by a wide margin, which is what the
  acceptance test asks.
- **The experts' baseline is handicapped** by the Friday scrapes (before the weekend's games) and
  covers 2020-2025 only; it is close to the models, so "better than the experts" is not a claim
  the Radar can make.
- **Quarterbacks:** the model adds nothing over last week's points.
- **Calibration** is fit on one season; the lowest probabilities round to 0%.
- **One setting for the whole season:** no refit during a season (spec default).

# Evaluation: how good, and how sure? (step C5)

C4 answered "does the Radar beat the naive baselines?" (yes). C5 grades it in full: per
position, per season, by rank, how well its percentages can be trusted, how many breakouts it
saw coming, and, for every one of those numbers, **how sure we can be**. Code:
`src/twm/modules/waiver_radar/evaluation.py`, `figures.py`; the reusable parts (the bootstrap,
"caught before it happened", calibration bins) in `src/twm/backtest/metrics.py`. Report:
`reports/waiver_radar/evaluation.md` (+ CSV with every number, + 5 figures). The walk-through
for a beginner: `notebooks/01_waiver_radar.ipynb`.

## One source of truth: the predictions store

The evaluation re-predicts nothing. It reads every ranking the backtest stored
(`data/predictions.duckdb`, read-only) and the outcomes stored beside them, so the report, the
figures, the notebook and the future time machine (C6) show exactly the same lists. For each
(model, label, test season) it uses the model version written last (a backtest re-run after a
change adds new versions and leaves the old ones behind); two versions written at the same
moment stop it with an error. The dataset adds only what the store does not hold: names and
teams, the rostership figures (for "without rostered"), whether an experts' page existed, and the
labels and weekly ranks of players who have left the pool (for breakouts). Before anything is
computed, the dataset's labels and pool rows are checked against the store's outcomes, row by
row: if they disagree (the dataset was rebuilt after the backtest), the evaluation stops and
asks for a new backtest. The stored ranks are recomputed with the backtest's ranking rule and
must match exactly.

Checks that it all adds up (tests): on a synthetic store every precision, bucket rate,
difference and breakout count equals a direct SQL recomputation; scrambling every model input
in the dataset changes nothing (nothing is re-predicted); and the committed report agrees with
C4's `backtest.csv` on every number both report (all 536 of them, 47.8% included).

## How sure? The season-block bootstrap

Twelve seasons are a small sample: another twelve would give somewhat different numbers. A
**95% interval** says how much. It comes from the **season-block bootstrap**: draw 12 seasons at
random from the 12 test seasons, with replacement (some twice, some not at all), recompute the
number on the drawn seasons, repeat 2,000 times with a fixed seed, and keep the middle 95% of
the results. Whole seasons are drawn, never single weeks, because the weeks of one season are
not independent: the same players, the same coaches, the same model trained before the season.
Drawing weeks one by one would pretend there are 732 independent tries and give intervals that
are too narrow.

For a comparison ("the Radar minus last week's points"), both methods are graded on the same
drawn seasons in every resample: a **paired** difference. A season that was hard for everyone
counts against both at once, so the interval measures the gap, not how hard the seasons were.
If a difference's interval stays above zero, the Radar's lead is not luck. The report also
gives the share of resamples in which the model was ahead and the number of seasons it won.

Every rate in the report comes with its counts: the number of lists (weeks x positions), rows,
hits, and hits among the top-10 picks.

## Results (2026-09-28, `uv run twm radar evaluate`)

Precision@10, 2014-2025, with the 95% interval (732 weekly lists):

| method | `y_hit` | without rostered | `y_sustained` | without rostered |
|---|---|---|---|---|
| logistic regression (the Radar) | **47.8%** (46.2-49.5) | 45.5% (43.6-47.5) | **16.0%** (15.0-17.0) | 14.5% (13.3-15.8) |
| LightGBM | 47.6% (45.9-49.6) | 45.2% (43.2-47.1) | 15.4% (14.3-16.5) | 14.0% (12.6-15.4) |
| last week's points | 40.4% (38.8-42.0) | 38.9% (37.1-40.8) | 12.6% (11.5-13.8) | 11.7% (10.5-13.1) |
| snap-share change | 24.6% (23.1-26.2) | 23.8% (22.3-25.2) | 7.3% (6.7-7.9) | 6.9% (6.3-7.5) |
| a random pick (base rate) | 10.6% | | 2.5% | |

- **For a manager:** if you had added the Radar's top 10 at a position every Tuesday, about 5 of
  them (3,501 of 7,320 picks) gave you a starter week within three games.
- **The Radar beats last week's points** by 7.4 points (95% interval 6.2 to 8.7; 12 of 12
  seasons; 6.6 points without rostered players), and the snap-share change by 23.2 (21.6 to
  24.7). For `y_sustained`: +3.4 (2.7 to 4.0) and +8.7 (7.8 to 9.5). The acceptance test of C4
  holds with room to spare.
- **Logistic regression vs LightGBM:** +0.2 points (-0.3 to +0.8) for `y_hit`, a tie as C4
  said; +0.6 (0.2 to 1.0) for `y_sustained`, where the logistic regression is ahead in 10 of 12
  seasons.
- **Per season** the Radar ranges from 42.3% (2020) to 53.1% (2025), median 47.4%.
- **Per position:** RB 54.2%, WR 50.8%, TE 42.8%, QB 43.4%. Against last week's points it gains
  7.9 (RB), 10.4 (WR) and 11.1 (TE) points, all with intervals well above zero, but **at QB
  +0.3 (-0.6 to +1.3): nothing**. The same holds without rostered players and for `y_sustained`.
- **By rank:** the Radar's ranks 1-5 hit 56.4%, 6-10 39.3%, 11-25 24.0%. Its lead over last
  week's points is 12.3 points at ranks 1-5 and only 2.5 and 1.6 below: it is best exactly where a
  manager picks.
- **PR-AUC and Brier** (all rows): 0.433 and 0.0736 for `y_hit` (an uninformative forecast: Brier
  0.0950), per season and per position in the report.
- **Calibration:** in the fixed-width bins (0-10%, 10-20% ...), `y_hit` predictions match the
  observed rate within 5 points in every bin with 1,000+ predictions (up to 70%); the 246
  predictions of 70-80% hit 66.7% (9.4 points too high) and the 107 above 80% are fewer still.
  `y_sustained` is good up to 30%, but its 523 predictions of 30-40% hit only 20.3% (13.4 points
  too high): a `y_sustained` probability above 30% should be read as "likely one good week", not
  "two".
- **The experts** (2020-2025, the 356 lists with a FantasyPros page): Radar 48.4%, experts 46.9%,
  difference +1.5 points with an interval of -0.4 to +2.9 (5 of 6 seasons won). The interval
  includes zero: **the Radar cannot claim to beat the experts** (and they had not even seen the
  weekend's games). Six seasons make these intervals wide.

## Breakouts caught

The spec asks for the share of breakouts the Radar "ranked top-10 at least once beforehand".
The exact definition, made from the data:

- A **breakout** is a player-season in which a player the Radar ranked (a pool row) had **two
  starter weeks within one 3-game window** (`y_sustained`) at that Tuesday or a later one of the
  same season. By then he may have left the pool (scoring well moves a player out of it), so the
  labels of every rostered player are used, not only the pool's. His **breakout Tuesday** is the
  first such as-of that is not before his first Tuesday in the pool. (A player who was already a
  starter before he ever entered the pool is not a breakout of that earlier stretch.)
- A method **caught** him when it ranked him in the **top 10 of his position at the breakout
  Tuesday or an earlier Tuesday of that season**. A ranking made after the breakout Tuesday never
  counts, so nothing is judged with hindsight. A stricter version asks for a top-10 rank in the **3
  Tuesdays up to the breakout** (a manager who added him in September may have dropped him by
  November).
- Every method is judged with its own lists; the experts only on the lists where their page
  existed (every method is then limited to those lists, and only breakouts whose breakout
  Tuesday's list had a page count).

Results 2014-2025 (`y_hit` lists; 870 breakouts, 832 of them still in the pool at the breakout
Tuesday):

| method | caught (95% interval) | in the 3 Tuesdays before | different players it put in a top 10 |
|---|---|---|---|
| the Radar | 55.5% (51.9-58.7), 483 | 45.5%, 396 | 1,614 |
| LightGBM | 58.0% (54.6-61.2), 505 | 47.2%, 411 | 1,690 |
| last week's points | 56.4% (54.6-58.3), 491 | 40.6%, 353 | 2,589 |
| snap-share change | 42.4% (38.9-46.7), 369 | 31.3%, 272 | 3,365 |

Read this honestly: **"ever in the top 10" does not separate the Radar from last week's points**
(-0.9 points, interval -3.6 to +1.4). That measure rewards a method whose top 10 changes a lot
from week to week, and last week's points reshuffles its list every Tuesday: it put 2,589
different players in a top 10, the Radar 1,614. Close to the breakout, the Radar is ahead: +4.9
points in the 3 Tuesdays before (1.7 to 8.2). On the experts' lists (2020-2025, 420 breakouts)
the Radar caught 49.3%, the experts 51.2% (-1.9, interval -4.1 to +0.4). With the `y_sustained`
lists the picture is the same (55.1%).

The biggest breakouts caught and missed are listed in the report with their weekly ranks. Some
of them are the proxy pool's blind spot rather than waiver finds: before 2020 the pool's
preseason list is last season's points per game, so a player who barely played the season
before, or a rookie drafted from round 3 on, is unranked and can be in the pool although real
leagues drafted him (the report shows his preseason rank and, from 2020, his rostership).

## The figures (`reports/waiver_radar/figures/`)

1. `precision_by_season.png`: precision@10 per season, one line per method, with the base rate.
2. `precision_pooled.png`: pooled precision@10 with 95% intervals, all positions and each one.
3. `hit_rate_by_rank.png`: hit rate of ranks 1-5, 6-10 and 11-25 per method.
4. `calibration.png`: the Radar's predicted chance against the observed rate, both labels, with
   the number of predictions in every bin.
5. `breakouts_caught.png`: breakouts caught per method, with the "3 Tuesdays before" version and
   the number of players each method flagged.

Each title states its takeaway and is computed from the numbers; colors follow the method and
are checked for colorblind readers, and every chart also has labels or marker shapes. Two runs
give byte-identical PNGs.

## Known limits

- **Twelve seasons.** The bootstrap's percentile intervals are honest about season-to-season
  swings but cannot know about changes the 12 seasons never showed (a new kind of offense, a
  rule change). With only 12 blocks such intervals tend to run a little narrow, so treat a
  lower bound close to zero as "not proven"; with 6 seasons (the experts) they are rough.
- **The proxy pool** still flatters every method a little: the "without rostered" numbers only
  exist from 2020, and before 2020 a few drafted players sit in the pool (see breakouts).
- **Breakouts** depend on a definition (two starter weeks in one window); a player who broke
  out while already rostered in every league is still counted if the proxy had him in the pool.
- **Calibration above 60%** (`y_hit`) and above 30% (`y_sustained`) rests on few predictions and
  runs high.
- **The current version rule** (last written wins) is simple; C6 will need to decide which
  version a live list uses.

## Commands

```bash
uv run twm radar evaluate                      # both labels -> reports/waiver_radar/evaluation.md,
                                               # evaluation.csv and figures/ (about 5 seconds)
uv run twm radar evaluate --label y_hit --no-figures
uv run twm radar evaluate --store /tmp/p.duckdb --out /tmp/eval/evaluation.md
uv run jupyter nbconvert --to notebook --execute --inplace \
  --ExecutePreprocessor.record_timing=False notebooks/01_waiver_radar.ipynb   # refresh the notebook
```

Options: `--label` (repeatable; default both), `--store`, `--dataset`, `--out`, `--figures`
(default `figures/` next to the report). The command only reads the store and the dataset.
