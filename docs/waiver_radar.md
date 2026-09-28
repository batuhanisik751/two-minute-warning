# Waiver Radar: the candidate pool (step C1) and its labels (step C2)

The Waiver Radar ranks players who are **probably still on waivers** (not on any team in a
typical 12-team fantasy league) by how likely they are to become weekly starters soon. Before it
can rank anyone it needs that list of available players, the *candidate pool*, for every
Tuesday of every season we backtest (2013 on). This page explains how the pool is built, why
it is built that way, and how well it matches reality where reality was recorded. The second
half ([Labels](#labels-did-a-pool-player-become-a-starter-step-c2)) explains the right answers
the models learn from: did a pool player really become a starter in the next weeks?

Code: `src/twm/modules/waiver_radar/pool.py` (the pool), `report.py` (the pool report),
`labels.py` (the labels), `label_report.py` (the label report). Tables: `fact_roster_week` and
`fact_ranking` (docs/warehouse.md). Tests: `tests/test_waiver_radar_pool.py`,
`tests/test_waiver_radar_labels.py`. Reports: `reports/waiver_radar/pool_sizes.md`,
`reports/waiver_radar/labels.md` (each with a `.csv`).

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
played on Sunday and shows RES on that week's roster went on injured reserve *after* the game:
the 2002-2015 rosters were taken after the games and include later moves.

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
