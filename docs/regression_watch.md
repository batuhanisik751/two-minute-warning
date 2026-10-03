# Regression Watch: expected points, points over expected and garbage time (step D1)

Regression Watch asks one question about every player: **is he scoring because of his role, or
because of luck that will not last?** Step D1 builds the numbers the later steps need (D2: which
parts are "sticky" from week to week; D3: projections and Sell-high / Buy-low tags). Nothing here
is a prediction yet.

## The three numbers

- **Fantasy points**: his weekly stat line scored with your league's rules
  (`config/scoring.yaml`, full PPR by default; docs/scoring.md).
- **xFP (expected fantasy points)**: what an *average* player would have scored from the same
  chances. Every target and carry has a value that depends on where on the field it happened,
  how deep the pass was, the down and distance and so on. A model estimates, for each target,
  the chance it is caught, the yards it is worth and the chance it is a touchdown (for each
  carry the same for rushing). We add those expectations up and score them with the same
  weights as real points. **Since 2026-10-01 (step H6-b2, the owner's decision) the weekly
  list, the stability study and the backtest use our own walk-forward models**: a play of
  season S is valued by models trained only on seasons before S (section "Our own expected
  points" below). D1 built the frame with nflverse's **ffopportunity** models, which saw later
  seasons too; the frame as built (`twm regression player`) and the Waiver Radar still use
  those. **Since 2026-10-02 (step PXFP, the owner's decision) the player pages' xFP and FPOE
  use the own walk-forward xFP too** (section "The player pages" below).
- **FPOE (fantasy points over expected)** = points - xFP. Positive: he did more with his
  chances than an average player would have. Opportunity (xFP) tends to repeat from week to
  week; FPOE is partly skill but largely luck, so it tends to shrink back toward zero
  ("regression to the mean"; D2 measures how much).

Example (real data, `uv run twm regression player "CeeDee Lamb" --season 2023`): in week 8 of
2023 he scored 41.0 points on 29.0 xFP (FPOE +12.0). But 23.5 of those points came in garbage
time; without it he had 17.5 points on 11.0 xFP (FPOE +6.5).

## Garbage time

A play is **garbage time** when the game is effectively decided: the offense's win
probability before the snap (nflfastR's `wp`) is below 5% or above 95%, except in the final two
minutes of a half when the score is within one possession (config `garbage_time`,
PROJECT_SPEC 7.3; the flag is `fact_play.is_garbage_time`, docs/warehouse.md). Late in a
blowout the losing team throws against soft coverage and the winning team runs out the clock,
so points piled up then say little about next week.

Weekly files cannot tell garbage-time points apart, so D1 computes points **and** expected
points play by play, then adds them up per game:

- `points_garbage`, `xfp_garbage`: what he scored, and what his chances were worth, on
  garbage-time plays;
- `points_ng` = `fantasy_points - points_garbage`, `xfp_ng` = `xfp - xfp_garbage`,
  `fpoe_ng` = `points_ng - xfp_ng`: the same three numbers **without garbage time** ("ng" =
  no garbage).

Why subtract instead of adding up the other plays? The weekly stat line is the official one
(the app and the Waiver Radar show it). Subtracting keeps `points_ng` equal to `fantasy_points`
for a player without a garbage-time play; the two ways differ only for the 0.06% of
player-games where the play-by-play does not reproduce the weekly line (below), and there the
difference stays in the non-garbage part.

How much is garbage time? About one sixth of everything, at every position (2006-2026, regular
season, `reports/regression_watch/xfp.md`):

| position | points from garbage time | xFP from garbage time | chances in garbage time |
|---|---|---|---|
| QB | 15.2% | 15.3% | 16.3% |
| RB | 16.3% | 16.3% | 15.9% |
| WR | 15.4% | 15.5% | 15.6% |
| TE | 15.2% | 15.1% | 15.4% |

The share is steady on average, but not for one player: Lamb got a fifth of his 2023 points in
garbage time (19.0 instead of 23.7 points per game without it). That is what the toggle is
for.

## Points play by play

Every play of `fact_play` becomes one stat line per player involved, with the same stat names
as the weekly file, so the one scoring engine scores it (`src/twm/modules/regression_watch/
plays.py`; no second copy of the weights):

| who | gets |
|---|---|
| the passer (`passer_player_id`) | passing yards (`passing_yards`), a passing touchdown when the pass scored (whoever carried it in), an interception |
| the target (`receiver_player_id`) | a catch on a completion, receiving yards |
| the rusher (`rusher_player_id`; kneels and scrambles are runs) | rushing yards |
| a lateral's receiver (`lateral_receiver_player_id` / `lateral_rusher_player_id`) | the yards after the lateral |
| the scorer (`td_player_id`) | the touchdown: rushing (he ran it, or took the lateral, on a rushing touchdown), receiving (same on a passing touchdown), a return touchdown (he returned the kickoff or punt, or any other touchdown on a kicking play), a fumble-recovery touchdown (he recovered a fumble). Interception returns score nothing for offensive players |
| the passer, target or rusher of a two-point try | a two-point conversion when it succeeded (`two_point_conv_result = 'success'`); a try never adds yards, catches or touchdowns |
| the player who lost a fumble | a lost fumble (`fumble_lost = 1`): the first fumbler, unless a second one exists and the first fumble was recovered by his own team. It is also labelled sack / rushing / receiving for the `scrimmage` fumble option |

**Check against the weekly stat line** (every QB/RB/WR/TE player-game 2006-2026): the play sum
equals the weekly points within 0.1 for **109,543 of 109,610 player-games (99.94%)**, to the
cent for 109,537. Every one of the other 67 is explained by one of three causes (the report
lists the largest with the stats that differ):

- **yards (59)**: the official stat line credits yards the play-by-play's yard columns do not:
  stat corrections applied to the weekly line only, a passer catching his own deflected pass,
  several laterals on one play (e.g. 2017_05_GB_DAL: 16 receiving yards on the weekly line,
  -4 in the plays);
- **lost fumbles (8)**: the weekly line and the play-by-play's fumbler columns disagree on who
  lost the ball, mostly on plays with two fumbles (e.g. 2020_03_LA_BUF, two receivers swapped);
- **touchdowns (1)**: 2011_13_DET_NO repeats a Mark Ingram touchdown run as a "kickoff" row;
  the weekly line counts it a third time as a return touchdown (+6).

## Expected points play by play

ffopportunity also publishes its expectations per play (`fact_opportunity_pass`,
`fact_opportunity_rush`, one row per pass or run, 2006 on). Summing them the right way
reproduces its weekly file exactly (within its 2-decimal rounding); the rules were read off
the data:

- a pass is worth `pass_completion_exp` expected catches (expected completions for the passer)
  and `pass_completion_exp x (air_yards + yards_after_catch_exp)` expected yards, plus its
  `pass_touchdown_exp`; the passer also gets `pass_interception_exp`;
- a run is worth `rush_yards_exp` and `rush_touchdown_exp` (ffopportunity sets a kneel to -1
  yard and an aborted snap to 0);
- a two-point try counts only its `two_point_conv_exp` (as a two-point conversion).

**Check against the weekly xFP**: within the rounding bound (0.1262 points: every weekly
expected stat is stored with 2 decimals, so up to 0.005 x each scoring weight) for **98,447 of
98,449 player-games**. The 2 outside are the passers of two 2013 plays that ffopportunity lists
twice (a target repeated under a linebacker's name); its weekly file counts them twice.

## The frame (`player_week.py`)

One row per player-game: every regular-season game (2006 on) of a player with a weekly stat
line or an ffopportunity row, at QB, RB, WR or TE (109,610 rows on the 2026-09-29 cache).

| column | meaning |
|---|---|
| `season`, `week`, `game_id`, `gsis_id`, `team` | who and which game |
| `position`, `position_source` | his position at the time (below) and where it came from |
| `fantasy_points`, `xfp`, `fpoe` | the weekly numbers (ffopportunity's as built, the Waiver Radar's; the site's `player_week_summary` shows the own walk-forward xFP since step PXFP). `fantasy_points` is 0 when he has expected points but no stat line; `xfp`/`fpoe` are empty without an ffopportunity row (no target, carry or pass) |
| `points_ng`, `xfp_ng`, `fpoe_ng` | the same without garbage time |
| `points_garbage`, `xfp_garbage` | the garbage-time parts |
| `play_points`, `play_xfp` | the play-by-play sums over all his plays (the checks above) |
| `n_opportunities`, `n_opportunities_garbage` | targets, carries and passes (two-point tries included, sacks not), and how many were in garbage time |
| `targets`, `receptions`, `receptions_exp`, `receiving_yards(_exp)`, `receiving_tds(_exp)`, `carries`, `rushing_yards(_exp)`, `rushing_tds(_exp)`, `pass_attempts`, `completions(_exp)`, `passing_yards(_exp)`, `passing_tds(_exp)`, `interceptions(_exp)` | actual and expected parts for the stability study (D2), both from the SAME ffopportunity row, i.e. counted over the same plays (`player_week.COMPONENTS` maps each to its column) |
| `yac`, `yac_exp` | yards after the catch on his catches and ffopportunity's expectation for them ("YAC over expected") |
| `available_at` | when the row became public (below) |

`twm glossary <column>` explains each one (all are in `src/twm/registry.py`).

**Position, at the time.** Players change positions (Cordarrelle Patterson WR to RB, Taysom
Hill QB to TE), and today's position would leak that into old weeks. As in the Waiver Radar,
the position is his weekly-roster position (`fact_roster_week`) as public at his week's
official Tuesday as-of: that week's roster row (`roster`, the normal case from 2016); else his
latest earlier roster row of the season (`roster_earlier`: the 2002-2015 rosters were taken
after the games and are public only a week later); else that game's snap-count position
(`snaps`, 2013 on); else his latest roster row of the previous season
(`roster_previous_season`). On the 2026-09-29 cache: roster 58,827, roster_earlier 47,665,
roster_previous_season 2,016, snaps 1,102. 1,153 player-games have none of these and are left
out: 1,152 are in 2006-2012 (no snap counts yet, rosters public a week late), 879 of them in week
1; only 310 had a target, carry or pass. Fullbacks listed as FB are not in the frame either.

**Point in time.** Everything is read through `twm.asof.AsOfView`, so a frame at an as-of only
contains what was public then. A row is public at the later of its week's official Tuesday
as-of and the moment every input of its game is public (game end + 6 h); a game moved to a
Tuesday or Wednesday (a "split week") joins after its own week's as-of. A row never changes
after it appears. Two ways to get the frame, which give the same rows (tested at every week's
as-of, between as-ofs, on a split week, and on the real cache):

- `player_games_asof(db, season, week)` (or `player_games_for(view, season)`): the season to
  date at that week's Tuesday as-of;
- `player_games_history(db, seasons)`: every row at once with its `available_at`;
  `visible(frame, as_of)` cuts it back to any moment.

The leakage harness (`twm.backtest.leakage`) deletes or scrambles everything that was not
public at an as-of and checks the frame does not change; a deliberately leaky variant that
reads the raw warehouse fails it (tests/test_regression_watch.py).

## Known gaps and limits

- **Model outputs (PROJECT_SPEC 6.3).** The frame's expected values come from ffopportunity's
  models and the garbage-time flag from nflfastR's win-probability model. Both were trained on
  many seasons, including seasons after some of the weeks shown here, so an old week's xFP
  "knows" a little about the future. H6-b re-estimated xFP walk-forward and compared; since
  H6-b2 (2026-10-01) the weekly list, D2 and D3 use the walk-forward xFP, so only the
  garbage-time flag still carries this leak there.
- **Receivers in 2006-2008: xFP too low.** In those seasons the play-by-play, and so
  ffopportunity, names the target of almost no incomplete pass (1.1%, 7.6% and 9.0% of
  incompletions, against 86-97% from 2009; nflverse's weekly `targets` is empty for
  2006-2008 too). An unnamed target is nobody's expected catch, so a receiver's xFP counts
  little more than his catches: WR xFP is 60% of WR points in 2006-2008 and 101% later, and
  receivers' FPOE is too high. Their xFP and FPOE of 2006-2008 are not comparable with later
  seasons; the stability study (D2) should start receivers in 2009 (passers are fine: every
  pass has its passer). The report prints the share per season.
- **Targets that differ.** On 1,120 passes ffopportunity's target differs from today's
  play-by-play (1,104 of them in 2006-2008; on 1,106 the play-by-play names no target). The
  per-play xFP follows ffopportunity, like its weekly file, so those targets count.
- **The 67 player-games** where the play-by-play does not reproduce the weekly points (above):
  their garbage-time split is only as good as the play-by-play.
- **Stat corrections** made midweek are in the weekly file and sometimes not in the
  play-by-play; both are the cache's latest versions (a mild known leak for backtests,
  docs/assumptions.md section 12).
- **Golden slice.** The golden world (`tests/golden/`) pins the frame (xFP, garbage xFP,
  positions, availability), but its synthetic play-by-play has no yard columns, so its per-play
  points are 0; the points reconciliation is tested on synthetic plays and on the real cache.

## Which parts repeat: the stability study (step D2)

`src/twm/modules/regression_watch/stability.py`; results in
`reports/regression_watch/stability.md` (+ .csv); a walk-through for beginners in
`notebooks/02_regression_stability.ipynb`.

**The idea.** Take every player-season with at least 8 games (2009 on; a game counts when he had
a target, carry or pass; one position per player-season, the one of most of his games) and
split his games in two: odd games (1st, 3rd, 5th ...) against even games, and, as a harder
test, his first half against his second half. Both halves are the same player in the same
season; only the luck differs. The **split-half correlation** (`twm glossary
split_half_correlation`) across player-seasons says how much a number repeats: near 1 it is
role or skill, near 0 it was luck. Intervals: 95%, 1,000 bootstrap resamples of the
player-seasons.

**What is measured** (per half): xFP, FPOE and points per game, each also without garbage time;
and the parts of efficiency as rates over expected per chance: TD rate ((touchdowns - expected)
per pass, carry or target), catch rate ((catches - expected) per target; for QBs completions
per pass, CPOE) and YAC over expected per catch (every catch has a YAC expectation, so it is
studied on every season of the study). A rate needs 10 chances in each half.

**The xFP it uses** is the pinned source (`config/production_models.yaml`, `xfp:`): since
2026-10-01 (step H6-b2) the own walk-forward xFP, each season valued by models trained on the
seasons before it (`uv run twm regression stability --xfp ffopportunity` reruns it with
ffopportunity's, for research).

**What the data says** (2009-2025, odd against even games, own xFP): opportunity is far
stickier than efficiency at every position (xFP/game r 0.72 QB to 0.89 RB; FPOE/game 0.17 TE
to 0.28 QB). Touchdowns over expected barely repeat (r 0.02-0.07); catch rate, completion rate
(QB 0.44) and YAC (WR, TE about 0.2) repeat a little. Without garbage time neither number gets
stickier. With ffopportunity's xFP (the approved study until 2026-10-01) FPOE/game repeated
less (0.10 QB to 0.16 RB) and QB completion rate 0.33: its models saw later seasons, so their
expectations already absorb some of what is skill.

**Shrinkage.** Each game's FPOE = the player's true level + luck. The covariance of the odd and
even halves estimates how much true levels differ (`signal_variance`); the variance of their
difference, divided by the average of 1/g_a + 1/g_b, estimates the luck of one game
(`noise_variance`). After g games his FPOE/game is worth

    r(g) = signal_variance / (signal_variance + noise_variance / g)

of its value: the **shrinkage factor** for the rest-of-season projection (D3: xFP/game + r(g)
x FPOE/game). On 2009-2025 (own xFP), r(8) is 0.32 QB, 0.27 RB, 0.24 WR, 0.22 TE; half weight
takes 17-29 games, about one to two seasons (with ffopportunity's xFP: 0.12, 0.20, 0.13, 0.15
and 30-60 games). The estimates move between season windows (RB r(8) 0.36 on 2009-2014, 0.16
on 2020-2025), which the report shows.

**Point in time.** `stability.shrinkage(seasons)` reads only the seasons it is given (and,
with `as_of`, only rows public then), so D3's walk-forward backtest for season S passes the
seasons before S; a later season can never change the estimate (tested).

**Limits.** One noise size per position (a high-volume player's FPOE swings more); backups are
in the study, which widens opportunity's spread; the parts of efficiency have no garbage-time
split; the bootstrap treats player-seasons as independent; the FPOE average of a position is
not zero (own xFP: QB +0.51, RB +0.21, WR +0.27, TE +0.47 per game, since the own models
expect a little less than players scored; with ffopportunity's, QB -0.8: lost fumbles have no
expected value), so shrinking toward 0 or toward the position average is a D3 choice
(`prior_mean`).

## The rest-of-season projection and the tags (step D3)

`projection.py`, `tags.py`, `backtest.py`; results in `reports/regression_watch/backtest.md`
(+ .csv); `twm glossary ppg_ros` (and `sell_high`, `buy_low`, `legit`) explains each number.

**Who is projected.** Every Tuesday (the official as-of), the *universe*: QBs, RBs, WRs and TEs
with at least 3 games this season whose points per game (PPG) or xFP per game ranks inside
teams x (the starting slots the position can fill, FLEX included) x 1.5. In the 12-team league
of `config/league.yaml` that is QB 18, RB 54, WR 54, TE 36; the numbers follow the config (a
10-team league gets 15, 45, 45, 30).

**The projection.** Points per game for the rest of the season =

    his xFP per game (recent games count a little more) + r(g) x his FPOE per game

His opportunity carries over; of his points over expected only the share r(g) that repeats
(D2's shrinkage factor after his g games, about 0.2-0.3 after 8 games) is kept. For season S,
r(g) comes only from the seasons 2009 to S-1. Example (`twm regression project 2026 3`, own
xFP): Jaxon Smith-Njigba scored 34.7 points per game on 22.5 xFP (+12.2 over expected):
projection 23.8.

**What the backtest chose (never looking at the season it tests).** 24 variants were
compared: shrink toward 0 (the spec's formula) or toward the position's average FPOE;
recent games weighted with a half-life of 8, 4 or 2 games, or not at all; garbage time kept,
left out of the efficiency, or left out of both parts. For test season S the variant with the
smallest error on the earlier seasons 2010 to S-1 is used. With the own xFP (since H6-b2) the
choice is toward 0 every season, with a half-life of 4 games (2011-2013), 8 games (2014-2019),
then no recency weight (2020-2025, and 2026): the spec's formula as written. Leaving garbage
time out never helped (as D2 found, it does not make FPOE stickier). With ffopportunity's xFP
the rule had picked the position average (half-life 4, then 8 games), 0.03 points per game
ahead of the spec's formula.

**How good is it?** Tested on 2011-2025 at the as-ofs after weeks 4, 6, 8 and 10 (10,013
player-weeks), against what each player really scored per game afterwards (players with fewer
than 3 games left are not graded; the report counts them). Mean absolute error, points per
game (95% intervals from resampling whole seasons), own xFP:

| | projection | season-to-date PPG | last-3 PPG | projection - PPG |
|---|---|---|---|---|
| all positions | 3.12 | 3.41 | 4.02 | -0.28 (-0.36 to -0.21) |
| QB / RB / WR / TE | 3.12 / 3.40 / 3.34 / 2.41 | 3.51 / 3.58 / 3.65 / 2.75 | 4.17 / 4.20 / 4.32 / 3.23 | QB -0.38 (-0.62 to -0.16); every position clearly better |

The P1 target ("beats season-to-date PPG on MAE") is **met**. The gain is largest early (week
4: 3.04 against 3.49) and small late (week 14: 3.85 against 3.93). With ffopportunity's xFP
(the approved backtest until 2026-10-01) the projection's MAE was 3.21 (-0.19, -0.25 to -0.13)
and the QB interval included 0 (-0.11, -0.31 to 0.09). The *order* of the players is no better
than PPG's (Spearman 0.528 against 0.518, difference within noise): the projection mostly
corrects each player's level toward his opportunity.

**The tags** (per position, at each as-of; X chosen on the earlier seasons as the most accurate
value that still tags at least 3 players a week; own xFP, ffopportunity's in brackets):

- **Sell-high**: FPOE per game in the top tenth of his position AND a projection at least X
  (4.5-5.5) points per game below his PPG. 94% of 262 tags came true (he scored less per game
  afterwards; [92% of 237]), against 60% for any universe player and 82% for the top tenth
  alone.
- **Buy-low**: the mirror (bottom tenth, projection at least X, usually 3, above his PPG). 59%
  came true [62%] against 39% for anyone, but 60% for the bottom tenth alone [61%]: the X
  adds little.
- **Legit**: a starter by PPG (QB top 12, RB 24, WR 24, TE 12) whose FPOE is NOT in the top
  tenth: his points come from his role. 59% were still starters for the rest of the season
  [61%], against 61% of all starters; starters with top-tenth FPOE stayed 68% of the time
  [63%] (they rank higher, so they have further to fall). Read Legit as a description, not a
  forecast. **Tested, not shown (owner, 2026-09-30):** Legit had no forecast value (with
  ffopportunity's xFP 60.6% of the 3,195 tagged player-as-ofs stayed starters against the
  61.1% base rate of every starter; with the own xFP 59.0% of 3,132), so the product dropped
  it: the weekly list assigns only Sell-high and Buy-low (step R1). The backtest, its report
  and its CSV keep Legit as tested (the finding is history; the report says so in a note), and
  so does the frozen backtest snapshot; `twm publish` strips Legit from every list it writes.

**Point in time.** Each projection of season S uses only S's games public at the as-of and the
seasons before S; the variant and X for S are chosen on seasons before S. Tests change season S
(its shrinkage and choices stay the same) and season S+1 (nothing of S changes); the live
command reads the warehouse through an as-of view and matches the backtest to the bit.

**Limits.** The garbage-time flag comes from nflfastR's win probability, trained on later
seasons too (PROJECT_SPEC 6.3; xFP no longer does since H6-b2: each season's xFP comes from
models trained on the seasons before it); a player is graded on the games he played; one
variant serves all positions; the season-block intervals treat seasons as independent.

## The weekly list (step D4a)

**One frozen, approved configuration.** The weekly job never estimates anything. The owner
approved ONE parameters file, `artifacts/production_models/regression_watch/<version>.json`
(4 KB), pinned with its sha256 in `config/production_models.yaml` (`regression_watch`,
`model: params`, like the streamer's D/ST rule). Since 2026-10-01 (step H6-b2, own xFP) it is
`zero_flat_all-9d98d6f251ee054c`; for 2026 it holds what D3's rule picks for 2026: the
variant `zero_flat_all` (shrink toward 0, no recency weight, garbage time kept: the spec's
formula; lowest validation MAE on 2010-2025, 3.112), the shrinkage table estimated on
2009-2025 (per position, with and without garbage time: the signal and noise variances behind
r(g) and the prior mean), the X of Sell-high (5 points/game) and Buy-low (3), and its xFP
source (`xfp_source: own`, part of the version). It also holds the record of 2025 (the same
run's choice for the last backtest season: `zero_flat_all`, X 5 and 3), which must equal the
committed `reports/regression_watch/backtest.csv` rows for 2025: `uv run twm model check`
checks the sha256, the version (a hash of the content) and that record. (The parameters
approved on 2026-09-30 with ffopportunity's xFP were `mean_flat_all-77146b2e5d2169fc`:
toward the position average, X 4.5 and 3.5.)

**The own xFP's live models are pinned too** (the pin's `xfp:` entry: `source: own`, `fold:
2026`, `version: own_xfp-2026-8577dfffe4683678`, trained on 2006-2025): the 2026 fold of
`own_xfp.py`, eight files `artifacts/production_models/regression_watch/<version>/<component>.joblib`
(catch, YAC, pass TD, interception, rush yards, rush TD and the two two-point rates; 3.1 MB),
each with its sha256. Every file's hash is checked before it is opened (a pickle), the files
must be the season's fold with every component and the parameters' source must be the pin's:
anything else is an error, never a fallback to ffopportunity. The scheduled job's preflight
loads them before any download; the weekly list only predicts with them, it never fits.
`uv run twm regression pin` (`--xfp own|ffopportunity`, default: the pinned source)
recomputes the parameters from the warehouse with that source (the own walk-forward history
comes from the folds in `data/regression_watch/own_xfp/`), fits the live models (about 45 s,
byte-identical on a rerun), refuses when the record disagrees with the backtest CSV, then
freezes the backtest lists; review and commit everything together.

**Each week** (`uv run twm regression score`, also part of `uv run twm score`):

- **Freshness** as the Radar's list (final scores, stats, snaps, expected points, injury
  reports, rosters, the Tuesday as-of has come) plus play-by-play rows for every played game
  (the garbage-time split). Missing data: exit code 3, nothing stored (`--allow-incomplete`
  scores anyway and marks the list).
- **The list**: the universe at the week's official as-of (read through one as-of view), the
  projection and the tags with the frozen parameters. With the own xFP the season's plays
  public at the as-of (ffopportunity's per-play rows give the play list and the garbage-time
  flag) are valued by the pinned live models and summed with D1's own SQL, and
  those expected points replace ffopportunity's in the frame (`player_week.with_xfp`). It
  equals D3's `regression project` of the same week. Weeks 1-2: nobody has 3 games yet, so there is no list (exit 0, nothing stored).
- **Live or reconstructed** by the Radar's rule: 'live' only when run on the real clock after
  the as-of and before week N+1's first kickoff; any other run is stored as 'backtest'. A stored
  live week is never overwritten.
- **Stored**: every universe player (module `regression_watch`, entity type `player`): `score`
  = projected points per game for the rest of the season, `rank` within the position, `band` =
  the tag (`sell_high`, `buy_low` or empty; since step R1 no `legit`: lists stored before
  keep it in their rows), `horizon` = the regular-season weeks left (the column is a number
  of weeks), `reasons_json` = the numbers behind it (PPG, xFP/game, FPOE/game, games, the shrinkage and
  the value it shrinks toward, the gap to his PPG, the X, and the same without garbage time).
- **Outcomes**: once a season's regular season is over, every stored row gets his actual
  points per game over the rest of it (`outcomes.y_value`, a numeric outcome column added in
  D4a; empty with fewer than 3 games left, which the backtest does not grade either).
  `uv run twm regression outcomes` does only this.
- **Report** `reports/regression_watch/weekly/<season>-W<nn>.md`: Sell-high and Buy-low
  tables in plain words, each with its backtest hit rate next to the base rate. One line
  ("Two tags only.") says that a third tag, Legit, was tested and dropped because it predicted
  nothing, with its two rates from backtest.csv (60.6% of its players stayed starters; 61.1% of
  every starter did). Reports written before step R1 keep their Legit table. A list before
  week 4 (in practice week 3, the first with a list) opens with "Early in the season": week 3 is earlier
  than the backtested weeks 4-14, so its projection and tags were never checked that early
  (step P2; the published `regression_list.note` carries the same sentence).
- **Published** (step P2) with the other modules by `twm publish` and the scheduled job
  (`regression_score` stage): `regression_list`, `regression_row`, `regression_outcome`,
  `regression_track_record`, `regression_stability` (step R1: `reports/regression_watch/
  stability.csv` row for row, `table` -> `section` and `window` -> `seasons`, for the
  methodology page; replaced each publish, skipped when unchanged, refused when it would
  shrink, like the track record), plus the backtest lists of the headline weeks of 2011-2025 for the
  time machine: frozen once on the owner's Mac (`uv run twm regression freeze`, part of `twm
  regression pin`) and pinned next to the parameters, never rebuilt by the job (docs/deploy.md
  "Regression Watch's approved parameters"). Every published row carries only the product's
  tags: the frozen backtest lists (made with D3's three tags) lose Legit at publish time (a row
  whose tag was Legit has none); live lists already published are frozen and keep their rows
  (2026 week 3), so the site hides 'legit'.

## Our own expected points, without hindsight (step H6-b; live since H6-b2)

ffopportunity's models were trained on many seasons, including seasons after some of the plays
they score (PROJECT_SPEC 6.3). `src/twm/modules/regression_watch/own_xfp.py` re-estimates the
same per-play expectations **walk-forward**: a play of season S gets them from models trained
on 2006 .. S-1 only (the 2026 fold is the live model). Per pass: the catch probability, the
yards after the catch if caught, the touchdown and interception probabilities; per carry: the
yards and the touchdown probability (a kneel stays -1 yard, an aborted snap 0); per two-point
try: its success rate. Inputs are the opportunity and the situation before the snap (air yards,
yards to the end zone, down, distance, pass or run direction, quarter, score difference,
scramble); never an nflfastR model column, and not the garbage-time flag (it is built from
nflfastR's win probability). Each part is LightGBM or a spline GLM, whichever did better on the
last training season. The predictions keep ffopportunity's column names, so D1's own query
scores them (`play_expected_sql(pass_table=, rush_table=)`; no second copy of the weights).

`reports/regression_watch/own_xfp.md` compares the two on the same plays and player-games, and
reruns D2 and D3 with the own xFP in place of ffopportunity's (the same functions: the frame
loaders take an `xfp=` source, `player_week.with_xfp`). Headline (2,000 season-block resamples):

- per play, ffopportunity is closer on catches, touchdowns, interceptions and rushing yards
  (most clearly in 2014-2020), as expected from a model that saw later seasons;
- per player-game the two correlate 0.99; the own xFP is 0.36 lower on average (QB 1.06);
- D2: FPOE/game repeats more with the own xFP (odd/even r, e.g. WR 0.11 -> 0.20, QB 0.10 ->
  0.28), also within seasons;
- D3: the projection's MAE is **lower** with the own xFP: -0.094 points per game (-0.129 to
  -0.062); Sell-high and Buy-low hit rates do not differ beyond noise. The leakage did not
  flatter the published backtest.

**Switched (owner, 2026-10-01; step H6-b2).** D2 and D3 were rerun with the own xFP exactly
as approved before (seasons 2009-2025, test seasons 2011-2025, every choice walk-forward), the
parameters re-approved and their backtest lists re-frozen, and the 2026 fold's models pinned
(section "The weekly list" above). The live list of 2026 week 3, published before the switch,
stays as it was (frozen, ffopportunity's xFP); the lists from week 4 on use the own xFP.
ffopportunity stays in the project: its per-play rows are still the play list the own models
value (each with its garbage-time flag), D1's frame as built and `twm regression player` and
the Waiver Radar's features still use its expectations, and `twm regression own-xfp` / `--xfp
ffopportunity` keep the comparison runnable for research. The player pages followed on
2026-10-02 (next section).

## The player pages (step PXFP, owner's decision of 2026-10-02)

The player pages' weekly xFP and FPOE, with and without garbage time (`player_week_summary`:
`xfp`, `fpoe`, `points_ng`, `xfp_ng`, `fpoe_ng`), are the own walk-forward xFP's, the numbers
of Regression Watch's own frame (`player_week.with_xfp`); `fpoe` = points - xFP as before.

- **History** (2013, the first snap-count season, .. the season before the pin's): frozen on
  the owner's Mac into the Regression Watch pin's snapshot as `player_xfp` (one row per
  player-game: `season`, `week`, `game_id`, `gsis_id`, `xfp`, `xfp_ng`, `points_ng`, `fpoe_ng`;
  each season from the fold trained on the seasons before it), sha256 and rows in
  `config/production_models.yaml`. `uv run twm regression freeze` writes it with the backtest
  lists; `--player-xfp` writes only it (the parameters and the lists keep their bytes). The
  scheduled job and `twm publish` only read it (required: no file, no publish; never
  recomputed, no ffopportunity fallback). `twm model check regression_watch` checks it (sha256,
  rows, one row per player-game, every season) and, where the folds and the warehouse are on
  disk, re-builds it from the saved folds (nothing is fit) to 1e-9; `twm timemachine verify
  --module regression_watch` re-builds every season of it.
- **The season in progress**: scored with the pinned live models by the weekly list's own
  function (`own_xfp.live_frame`: the season's plays valued by the pinned models, summed with
  D1's SQL, put into the frame), over every built row; nothing is fit.
- **Empty** (NULL) where a player-week has no own-xFP row: no ffopportunity row for the game
  (as before), and, new, the 144 player-weeks of 2013-2025 whose weekly stat line lists him
  at RB/WR/TE but whose position at the time (D1's frame) was FB (121), DB (16), CB (4), OL
  (2) or LB (1): D1's frame keeps QB/RB/WR/TE only, so those weeks already had no
  garbage-time split. No player-week gained or lost a value otherwise; `points_ng` is
  unchanged.
- **Old vs new** (82,998 player-weeks of 2013-2026, 66,573 with both): own minus
  ffopportunity per player-week QB -1.07, RB -0.25, TE -0.39, WR -0.28 (all -0.39);
  correlation .981 / .994 / .991 / .990 (all .990), in line with H6-b's per player-game
  comparison above.

## Commands

```
uv run twm regression player "CeeDee Lamb" --season 2023   # weekly points, xFP, FPOE, with and without garbage time
uv run twm regression player 00-0036358 --as-of 2026-W3     # as it looked at week 3's Tuesday as-of
uv run twm regression xfp-report                            # reports/regression_watch/xfp.md (+ .csv)
uv run twm regression stability                             # reports/regression_watch/stability.md (+ .csv), 2009 to last season, pinned xFP source (--xfp own|ffopportunity)
uv run twm regression project 2026 3 --position WR          # projections and tags at week 3's as-of (--csv to save)
uv run twm regression backtest                              # reports/regression_watch/backtest.md (+ .csv), about 3 min with the own xFP (= twm backtest regression_watch; --xfp)
uv run twm regression score                                 # the weekly list with the approved parameters and pinned xFP models (exit 3: data not ready)
uv run twm regression pin                                   # approve the season's parameters + own xFP live models (--xfp; review, then commit)
uv run twm regression own-xfp                               # own walk-forward xFP vs ffopportunity + D2/D3 reruns (reports/regression_watch/own_xfp.md; about 3 min the first time)
uv run twm regression outcomes                              # grade stored lists of finished seasons
uv run pytest tests/test_regression_watch.py tests/test_regression_stability.py tests/test_regression_projection.py tests/test_regression_weekly.py tests/test_regression_own_xfp.py tests/test_regression_xfp_switch.py   # offline tests
uv run pytest -m realdata -k regression                     # the checks on the real cache
```
