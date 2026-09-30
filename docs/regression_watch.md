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
  how deep the pass was, the down and distance and so on. nflverse's **ffopportunity** models
  estimate, for each target, the chance it is caught, the yards it is worth and the chance it
  is a touchdown (for each carry the same for rushing). We add those expectations up and score
  them with the same weights as real points.
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
| `fantasy_points`, `xfp`, `fpoe` | the weekly numbers (the same as the site's `player_week_summary` and the Waiver Radar's). `fantasy_points` is 0 when he has expected points but no stat line; `xfp`/`fpoe` are empty without an ffopportunity row (no target, carry or pass) |
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

- **Model outputs (PROJECT_SPEC 6.3).** The expected values come from ffopportunity's models
  and the garbage-time flag from nflfastR's win-probability model. Both were trained on many
  seasons, including seasons after some of the weeks shown here, so an old week's xFP "knows"
  a little about the future. P1 uses them as they are; P2 re-estimates xFP walk-forward and
  compares.
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
per pass, CPOE) and YAC over expected per catch (ffopportunity has a YAC expectation for every
catch from 2006, so it is studied on every season of the study). A rate needs 10 chances in each
half.

**What the data says** (2009-2025, odd against even games): opportunity is far stickier than
efficiency at every position (xFP/game r 0.76 QB to 0.88 RB; FPOE/game 0.10 QB to 0.16 RB).
Touchdowns over expected barely repeat (r about 0); catch rate, completion rate (QB 0.33) and
YAC (WR, TE about 0.2) repeat a little. Without garbage time neither number gets stickier.

**Shrinkage.** Each game's FPOE = the player's true level + luck. The covariance of the odd and
even halves estimates how much true levels differ (`signal_variance`); the variance of their
difference, divided by the average of 1/g_a + 1/g_b, estimates the luck of one game
(`noise_variance`). After g games his FPOE/game is worth

    r(g) = signal_variance / (signal_variance + noise_variance / g)

of its value: the **shrinkage factor** for the rest-of-season projection (D3: xFP/game + r(g)
x FPOE/game). On 2009-2025, r(8) is 0.12 QB, 0.20 RB, 0.13 WR, 0.15 TE; half weight takes
30-60 games, i.e. several seasons. The estimates move between season windows (RB r(8) 0.33 on
2009-2014, 0.10 on 2020-2025), which the report shows.

**Point in time.** `stability.shrinkage(seasons)` reads only the seasons it is given (and,
with `as_of`, only rows public then), so D3's walk-forward backtest for season S passes the
seasons before S; a later season can never change the estimate (tested).

**Limits.** One noise size per position (a high-volume player's FPOE swings more); backups are
in the study, which widens opportunity's spread; the parts of efficiency have no garbage-time
split (ffopportunity's weekly rows); the bootstrap treats player-seasons as independent; the
FPOE average of QB player-seasons is below zero (-0.8 per game: lost fumbles have no expected
value), so shrinking toward 0 or toward the position average is a D3 choice (`prior_mean`).

## Commands

```
uv run twm regression player "CeeDee Lamb" --season 2023   # weekly points, xFP, FPOE, with and without garbage time
uv run twm regression player 00-0036358 --as-of 2026-W3     # as it looked at week 3's Tuesday as-of
uv run twm regression xfp-report                            # reports/regression_watch/xfp.md (+ .csv)
uv run twm regression stability                             # reports/regression_watch/stability.md (+ .csv), 2009 to last season
uv run pytest tests/test_regression_watch.py tests/test_regression_stability.py   # offline tests
uv run pytest -m realdata -k regression                     # the checks on the real cache
```
