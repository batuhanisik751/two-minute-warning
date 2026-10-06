# Glossary

Generated from `src/twm/registry.py` by `uv run twm glossary --write`; do not edit by
hand. Planned entries are built in the step shown. \* = nflfastR/ffopportunity model
output (PROJECT_SPEC 6.3: trained on many seasons, a mild known leak in backtests).

## Metrics

### 80% range \*

Where his rest-of-season points per game should land 8 times in 10: the projection plus how far off it was for similar players in earlier seasons. In the backtest the real result fell inside about 80% of the time at every position (the Methodology page shows each share); it says nothing about games he misses.

- **Name:** `projection_range`; **unit:** points per game; **used by:** regression_watch
- **Formula:** ppg_ros + the 10th and 90th percentiles of the frozen backtest's per-game misses (rest_of_season_ppg - ppg_ros) at his position, from lists with weeks left within 2 of his list's (else the nearest weeks left), of seasons before the list's only (walk-forward); none with fewer than 30 misses
- **Source:** twm.modules.regression_watch.ranges

### Against convention

A clear call where the aggressive option was best and the coach took it: he went for it on fourth down, or went for two. The number is how much win probability that gained over the best kicking option, by the model.

- **Name:** `against_convention`; **unit:** WP points; **used by:** decisions
- **Formula:** a clear call with chosen = recommended = go (fourth down) or two-point (try); the number = WP(go) - max(WP(field goal), WP(punt)), or WP(two) - WP(kick)
- **Source:** web/lib/decisions.ts (againstConvention); web/lib/queries/decisions.ts

### Aggressiveness

How often a coach goes for it when the numbers clearly say go.

- **Name:** `aggressiveness`; **unit:** share (0-1); **used by:** decisions
- **Formula:** clear fourth downs where going for it was best AND the coach went / clear fourth downs where going for it was best (the go rate when go was clearly best)
- **Source:** twm.modules.decisions.coach

### All-play record

Your record if you had played every team every week. It shows how good your scores were, whatever the schedule: 9-2 in a week means only two teams scored more than you.

- **Name:** `all_play_record`; **unit:** wins-losses; **used by:** my_league
- **Formula:** over the regular-season weeks whose games are all final: each week, one win for every other team the team outscored and one loss for every team that outscored it (a tie counts as a tie)
- **Source:** twm.league.luck (twm league odds; the local weekly report)

### Allocation

How much of the missing starter's work a teammate in that role took on average: 0.46 means the backup RB got about 46% of the RB1's carries.

- **Name:** `allocation`; **unit:** share of the vacated share; **used by:** teammate_out
- **Formula:** over past single-starter-out games (2013 on): sum of a role's share changes (game share minus his baseline share) / sum of the vacated shares; shrunk toward its position group with 20 teammate-games; carries only when a RB is out
- **Source:** twm.modules.teammate_out (frozen table, docs/teammate_out.md)

### Average FPOE per game (prior) \*

The value a shrunk FPOE/game is pulled toward when shrinking toward the position average instead of zero.

- **Name:** `prior_mean`; **unit:** points; **used by:** regression_watch
- **Formula:** sum of FPOE over sum of games, over the player-seasons of a position
- **Source:** twm.modules.regression_watch.stability

### Brier score

How close the probabilities came to what happened: the average squared gap between each probability and the outcome (1 if it happened, 0 if not). Lower is better and 0 is perfect. Example: saying 80% for something that happens adds 0.04; saying 80% for something that does not happen adds 0.64.

- **Name:** `brier`; **unit:** 0-1 (lower is better); **used by:** decisions, hot_seat, questionable
- **Formula:** mean over the rows of (probability - outcome)^2, the outcome 1 when it happened and 0 when not
- **Source:** twm.backtest.metrics.brier

### Buy-low \*

He has scored well below what his chances were worth: his points should rise, so he may be cheap to trade for.

- **Name:** `buy_low`; **unit:** yes/no; **used by:** regression_watch
- **Formula:** FPOE/game in the bottom ceil(n / 10) FPOE/game of his position's universe at that as-of AND ppg_ros at least X above his PPG
- **Source:** twm.modules.regression_watch.tags

### CPOE (completion percentage over expected) \*

Whether a quarterback completes more passes than an average passer would have on the same throws.

- **Name:** `cpoe`; **unit:** percentage points; **used by:** shared
- **Formula:** 1 if the pass was complete else 0, minus nflfastR's expected completion probability, x 100
- **Source:** fact_play.cpoe
- **Verified:** equals 100 x (complete_pass - cp) on all 52,174 passes with a CPOE in 2006, 2015 and 2025 (raw play-by-play)

### Carries

The actual number in the game, counted by ffopportunity over the same plays as its expected value.

- **Name:** `carries`; **unit:** carries; **used by:** regression_watch
- **Formula:** fact_opportunity_week.rush_attempt (two-point tries excluded), from the same ffopportunity row (fact_opportunity_week), i.e. over the same plays
- **Source:** twm.modules.regression_watch.player_week (fact_opportunity_week)

### Catch rate over expected \*

How many more of his targets he caught than an average receiver would have.

- **Name:** `catch_rate_over_expected`; **unit:** catches per target; **used by:** regression_watch
- **Formula:** (receptions - receptions_exp) / targets, over the games of one half of a player-season (summed, then divided)
- **Source:** twm.modules.regression_watch.stability

### Chance

How often players the Radar rated like him became a fantasy starter soon, in earlier seasons' backtests. The range beside it is how sure that rate is. It is the track record of similar players, not a promise, and not the model's own probability (which ran too high for the top players in the backtest).

- **Name:** `chance`; **unit:** share (0-1), with a 90% range; **used by:** waiver_radar
- **Formula:** the observed y_hit rate of the backtest predictions in the same similar-players bin as his model probability: the walk-forward predictions of the same model and label from the seasons before the list's season, sorted by probability, cut into groups of at least 500 (a probability is never split) and merged so the rate never falls as the probability rises; the range is the bin's 90% Wilson interval
- **Source:** twm.modules.waiver_radar.confidence (similar_bins, Confidence.band)

### Chance (K and D/ST)

How often picks the streamer rated alike scored like a starter the very next week, in earlier seasons' backtests: for kickers, picks with a similar model score; for team defenses, the rule's pick at the same rank. The range beside it is how sure that rate is. A track record, not a promise.

- **Name:** `stream_chance`; **unit:** share (0-1), with a 90% range; **used by:** streamer
- **Formula:** read off the frozen streamer backtest of the seasons before the list's season: kickers: the y_start rate of the bin of backtest picks with a similar model score (bins of at least 200 picks); D/STs: the y_start rate of the rule's picks at that rank (rank bins of at least 100 picks); a better score or rank never shows a lower chance; 90% interval
- **Source:** twm.modules.streamer.confidence

### Chance he plays

Out of 100 players like him in past seasons, about this many played. Teams name their inactive players about 90 minutes before kickoff: check then.

- **Name:** `play_chance`; **unit:** share (0-1); **used by:** questionable
- **Formula:** share of past tagged players with the same tag, position and 'missed his team's previous game' who took at least one offensive snap (fact_snaps), seasons 2016 to last season, each group shrunk toward its parent group (20 pseudo-rows)
- **Source:** twm.modules.questionable (frozen table, docs/questionable.md)

### Chance of a Cliff

The model's estimated chance that the player plays enough games next season to judge and loses a large share of his points per game. It is only meaningful for a player who plays: the chance of missing time is a separate number, and the two are never added together.

- **Name:** `board_cliff_chance`; **unit:** probability (0-1); **used by:** board
- **Formula:** the pinned Cliff model's own probability of y_cliff (an L2 logistic regression on the preseason features at the kickoff-eve as-of)
- **Source:** twm.modules.board.production (ROLES: cliff)

### Chance of missed time

The model's estimated chance that the player plays only a few games next season or none (injury, a benching, a release or retirement), from a separate, simpler model.

- **Name:** `board_missed_chance`; **unit:** probability (0-1); **used by:** board
- **Formula:** the pinned missed-time model's own probability of y_missed (a simpler logistic regression on fewer inputs, the same as-of)
- **Source:** twm.modules.board.production (ROLES: missed)

### Clear call or toss-up

Only decisions with a clear answer count against a coach.

- **Name:** `decision_grade`; **unit:** category; **used by:** decisions
- **Formula:** 'clear' when the best option's WP beats the second best by more than decisions.toss_up_margin (1.5 WP points), else 'toss_up' (not graded); the 4th quarter's last 2:00 and overtime are not graded at all (decisions.late_game)
- **Source:** twm.modules.decisions.grade

### Completion rate over expected (CPOE) \*

How many more of his passes were completed than an average passer's would have been.

- **Name:** `completion_rate_over_expected`; **unit:** completions per pass; **used by:** regression_watch
- **Formula:** (completions - completions_exp) / pass_attempts, over the games of one half of a player-season (summed, then divided)
- **Source:** twm.modules.regression_watch.stability

### Completions

The actual number in the game, counted by ffopportunity over the same plays as its expected value.

- **Name:** `completions`; **unit:** passes; **used by:** regression_watch
- **Formula:** fact_opportunity_week.pass_completions, from the same ffopportunity row (fact_opportunity_week), i.e. over the same plays
- **Source:** twm.modules.regression_watch.player_week (fact_opportunity_week)

### Conversion probability

The chance that going for it on this down and distance works.

- **Name:** `p_convert`; **unit:** probability (0-1); **used by:** decisions
- **Formula:** LightGBM on the go-for-it features, trained walk-forward (seasons < S)
- **Source:** twm.modules.decisions.conversion

### Decisions

Every fourth down and every try after a touchdown the Report Card priced: clear calls and toss-ups together. Kneels, the half's last seconds and snaps wiped out by a penalty are left out.

- **Name:** `decisions_graded`; **unit:** decisions; **used by:** decisions
- **Formula:** fourth downs (2006 on) and tries after a touchdown without an exclusion (not a snap, a penalty with no play, a kneel or spike, an aborted snap, the half's last seconds, the late game, a state the models cannot score): clear calls + toss-ups
- **Source:** twm.modules.decisions.grade (docs/decision_metrics.md)

### Dud rate

How often a tagged player who did play scored less than half his usual. Healthy players do that too (about 1 in 4), so compare the two numbers.

- **Name:** `dud_rate`; **unit:** share (0-1); **used by:** questionable
- **Formula:** among tagged players who played, with 2+ earlier games and 5+ points per game so far: share whose points that week were below 50% of their points per game so far; compared with healthy players matched on season, week, position and points-per-game band
- **Source:** twm.modules.questionable.plays

### EP left on the table (end of half)

How many points, net of the risk, a typical team got from that spot and clock.

- **Name:** `passivity_ep_left`; **unit:** points; **used by:** decisions
- **Formula:** G1b half_value(yardline_100, half_seconds_remaining) at the decision snap minus 0 (kneeling scores nothing): net points from a first-half 1st down to halftime, measured on 1999-2005
- **Source:** twm.modules.decisions.clock

### EPA (expected points added) \*

How much a single play helped or hurt the offense's scoring chances. A 30-yard catch on 3rd and 10 adds a lot; a sack on 1st down costs points.

- **Name:** `epa`; **unit:** points per play; **used by:** shared, decisions, hot_seat
- **Formula:** nflfastR's expected points after the play minus before it, for the offense
- **Source:** fact_play.epa

### End-of-half passivity

Running out the first half with time, timeouts and field position good enough that attacking was clearly worth points.

- **Name:** `half_passivity`; **unit:** case (boolean) per first half; **used by:** decisions
- **Formula:** a first-half drive that ended the half with a tail of kneels / designed runs (no team timeout, no pass) whose first 1st down had >= 40 s and >= 1 timeout; a case when half_value there >= decisions.clock.passivity_min_ep (1.0 point)
- **Source:** twm.modules.decisions.clock

### Estimated chance

The model's estimate of the chance that the head coach is let go (fired during or after the season, or a mutual parting) and that it is announced within a set number of days after his team's final game. An estimate from past seasons' patterns, not a prediction that it will happen.

- **Name:** `hot_seat_estimate`; **unit:** probability (0-1); **used by:** hot_seat
- **Formula:** the live L2 logistic regression's own probability (no recalibration) that the coach is let go (hot_seat_let_go) and it is announced from the row's day through the window's end, a set number of days after his team's final game, playoffs included
- **Source:** twm.modules.hot_seat (targets.py: the window; production.py: the pinned model)

### Expected WP after a punt

How good punting is, as a win chance, averaged over where punts from this spot really end up.

- **Name:** `punt_expected_wp`; **unit:** probability (0-1); **used by:** decisions
- **Formula:** sum over the smoothed empirical distribution of punt results (receiving team's spot, kicking team recovers, return touchdown) of the kicking team's own WP (G1) in the resulting state
- **Source:** twm.modules.decisions.punt

### Expected completions \*

What an average player would have produced from the same chances; compare with the actual number to see efficiency.

- **Name:** `completions_exp`; **unit:** passes; **used by:** regression_watch
- **Formula:** fact_opportunity_week.pass_completions_exp, from the same ffopportunity row (fact_opportunity_week), i.e. over the same plays
- **Source:** twm.modules.regression_watch.player_week (fact_opportunity_week)

### Expected interceptions \*

What an average player would have produced from the same chances; compare with the actual number to see efficiency.

- **Name:** `interceptions_exp`; **unit:** interceptions; **used by:** regression_watch
- **Formula:** fact_opportunity_week.pass_interception_exp, from the same ffopportunity row (fact_opportunity_week), i.e. over the same plays
- **Source:** twm.modules.regression_watch.player_week (fact_opportunity_week)

### Expected passing touchdowns \*

What an average player would have produced from the same chances; compare with the actual number to see efficiency.

- **Name:** `passing_tds_exp`; **unit:** touchdowns; **used by:** regression_watch
- **Formula:** fact_opportunity_week.pass_touchdown_exp, from the same ffopportunity row (fact_opportunity_week), i.e. over the same plays
- **Source:** twm.modules.regression_watch.player_week (fact_opportunity_week)

### Expected passing yards \*

What an average player would have produced from the same chances; compare with the actual number to see efficiency.

- **Name:** `passing_yards_exp`; **unit:** yards; **used by:** regression_watch
- **Formula:** fact_opportunity_week.pass_yards_gained_exp, from the same ffopportunity row (fact_opportunity_week), i.e. over the same plays
- **Source:** twm.modules.regression_watch.player_week (fact_opportunity_week)

### Expected receiving touchdowns \*

What an average player would have produced from the same chances; compare with the actual number to see efficiency.

- **Name:** `receiving_tds_exp`; **unit:** touchdowns; **used by:** regression_watch
- **Formula:** fact_opportunity_week.rec_touchdown_exp, from the same ffopportunity row (fact_opportunity_week), i.e. over the same plays
- **Source:** twm.modules.regression_watch.player_week (fact_opportunity_week)

### Expected receiving yards \*

What an average player would have produced from the same chances; compare with the actual number to see efficiency.

- **Name:** `receiving_yards_exp`; **unit:** yards; **used by:** regression_watch
- **Formula:** fact_opportunity_week.rec_yards_gained_exp, from the same ffopportunity row (fact_opportunity_week), i.e. over the same plays
- **Source:** twm.modules.regression_watch.player_week (fact_opportunity_week)

### Expected receptions \*

What an average player would have produced from the same chances; compare with the actual number to see efficiency.

- **Name:** `receptions_exp`; **unit:** catches; **used by:** regression_watch
- **Formula:** fact_opportunity_week.receptions_exp: the sum of the completion chances of his targets, from the same ffopportunity row (fact_opportunity_week), i.e. over the same plays
- **Source:** twm.modules.regression_watch.player_week (fact_opportunity_week)

### Expected rushing touchdowns \*

What an average player would have produced from the same chances; compare with the actual number to see efficiency.

- **Name:** `rushing_tds_exp`; **unit:** touchdowns; **used by:** regression_watch
- **Formula:** fact_opportunity_week.rush_touchdown_exp, from the same ffopportunity row (fact_opportunity_week), i.e. over the same plays
- **Source:** twm.modules.regression_watch.player_week (fact_opportunity_week)

### Expected rushing yards \*

What an average player would have produced from the same chances; compare with the actual number to see efficiency.

- **Name:** `rushing_yards_exp`; **unit:** yards; **used by:** regression_watch
- **Formula:** fact_opportunity_week.rush_yards_gained_exp, from the same ffopportunity row (fact_opportunity_week), i.e. over the same plays
- **Source:** twm.modules.regression_watch.player_week (fact_opportunity_week)

### Expected yards after the catch \*

Yards after the catch an average receiver would have gained on the same catches; yac - yac_exp is 'YAC over expected'.

- **Name:** `yac_exp`; **unit:** yards; **used by:** regression_watch
- **Formula:** yards_after_catch_exp over his caught targets (two-point tries excluded)
- **Source:** twm.modules.regression_watch.player_week (fact_opportunity_pass)

### Expert rank at the as-of

How the FantasyPros experts ranked the player at his position at the time. Used only as a baseline to beat (the 'experts' ranking); no model learns from it. Their pages were saved on Fridays, so on Tuesday they had not seen the last weekend's games yet.

- **Name:** `ecr_pos_rank`; **unit:** rank (1 = best); **used by:** waiver_radar
- **Formula:** fact_ranking.pos_rank of the player on his roster position's page: the season's latest rest-of-season page public at the as-of if it lists him, else the latest weekly page listing him among those scraped up to twm.modules.waiver_radar.expert_ranks.WEEKLY_MAX_AGE_DAYS days before the latest weekly page; NULL when neither lists him (2020 on)
- **Source:** fact_ranking (page_kind ros, weekly), twm.modules.waiver_radar.expert_ranks

### Expert rank date

The day the experts' page was saved.

- **Name:** `ecr_scrape_date`; **unit:** date; **used by:** waiver_radar
- **Formula:** scrape_date of the page ecr_pos_rank comes from (public the next day at 00:00 UTC, fact_ranking.available_at)
- **Source:** fact_ranking.scrape_date

### Expert rank source

Whether the expert rank is a rest-of-season rank or this week's rank.

- **Name:** `ecr_page_kind`; **unit:** 'ros' or 'weekly'; **used by:** waiver_radar
- **Formula:** which page ecr_pos_rank comes from: 'ros' (rest of season) or 'weekly'; NULL when the player is unranked
- **Source:** twm.modules.waiver_radar.expert_ranks

### Expert ranks available

Whether the experts' ranking existed at that moment; the expert baseline is only graded where it did.

- **Name:** `ecr_available`; **unit:** boolean; **used by:** waiver_radar
- **Formula:** a rest-of-season or weekly FantasyPros page of the player's roster position of this season is public at the as-of (the archive starts in December 2019)
- **Source:** fact_ranking

### Experts' preseason rank (ECR)

FantasyPros' expert consensus ranking: many fantasy experts' preseason ranks at the position, combined into one, from the last scrape before week 1. It is the experts' consensus, not draft position (ADP), and it exists for recent seasons only.

- **Name:** `board_ecr`; **unit:** position rank; **used by:** board
- **Formula:** the expert consensus rank at his position on the preseason ranking page of the last scrape before week 1 public by the board's as-of; none when he is not ranked or before the ranking archive's first season
- **Source:** fact_ranking.pos_rank (page_kind 'preseason')

### Extra-point rate

How often the kick after a touchdown is good.

- **Name:** `pat_rate`; **unit:** share (0-1); **used by:** decisions
- **Formula:** made extra points / attempts in the last 5 seasons before S within the same rule era (2015+: from the 15)
- **Source:** twm.modules.decisions.tries

### FLEX-worthy finish

The running back or receiver scored well enough to fill a FLEX slot that week. Informative only; it is not a label.

- **Name:** `is_flex_finish`; **unit:** boolean; **used by:** waiver_radar
- **Formula:** a RB or WR with weekly_pos_rank <= his position's FLEX-worthy rank (RB/WR top 36); always false for QB and TE
- **Source:** twm.modules.waiver_radar.labels.weekly_finishes

### FPOE without garbage time \*

Points over expected, counting only plays while the game was in doubt.

- **Name:** `fpoe_ng`; **unit:** points; **used by:** regression_watch
- **Formula:** points_ng - xfp_ng
- **Source:** twm.modules.regression_watch.player_week

### Fantasy points

The score a player earns for your fantasy team in one game, computed from his yards, touchdowns, catches and mistakes with your league's settings.

- **Name:** `fantasy_points`; **unit:** points; **used by:** shared
- **Formula:** sum over stats of (stat value x points per unit in config/scoring.yaml); full PPR by default
- **Source:** twm.scoring.score / score_sql on fact_player_week
- **Verified:** the nflverse_ppr preset reproduces nflverse's fantasy_points_ppr exactly on all 150,691 QB/RB/WR/TE player-weeks 1999-2026 (tests/test_scoring.py)
- **Waiver Radar reason:** "{player} scored {value:.1f} fantasy points"

### Fantasy points added up play by play

The same points as the weekly stat line, credited play by play so a game can be split into parts; equal to fantasy_points for 99.9% of player-games.

- **Name:** `play_points`; **unit:** points; **used by:** regression_watch
- **Formula:** the sum over his plays of each play's stats scored with config/scoring.yaml (twm.scoring.score_sql): passer = passing yards, passing touchdown, interception; target = reception, receiving yards; rusher = rushing yards; lateral receiver or rusher = the lateral's yards; td_player_id = the touchdown (rushing, receiving, return or fumble-recovery); two-point conversions to the passer, target or rusher of the try; a lost fumble to the player who lost it
- **Source:** twm.modules.regression_watch.player_week

### Fantasy points in garbage time \*

Points he scored after the game was effectively decided.

- **Name:** `points_garbage`; **unit:** points; **used by:** regression_watch
- **Formula:** the fantasy points of his garbage-time plays (fact_play.is_garbage_time), credited play by play as in play_points
- **Source:** twm.modules.regression_watch.player_week

### Fantasy points without garbage time \*

The points he scored while the game was still in doubt. Late points in a blowout come against soft defenses and say little about next week.

- **Name:** `points_ng`; **unit:** points; **used by:** regression_watch
- **Formula:** fantasy_points - points_garbage: the weekly stat line's points minus those scored on his garbage-time plays (fact_play.is_garbage_time)
- **Source:** twm.modules.regression_watch.player_week

### Field-goal probability

The chance the kick is good from here, in these conditions.

- **Name:** `p_fg_make`; **unit:** probability (0-1); **used by:** decisions
- **Formula:** LightGBM on distance, roof, wind, temperature, surface and era, walk-forward
- **Source:** twm.modules.decisions.fieldgoal

### Games (G)

How many games a player's numbers are based on. More games make a per-game number more trustworthy: a hot start over 2 games says less than a full season. Example: G 3 with 15 PPG means 45 points over 3 games.

- **Name:** `games`; **unit:** games; **used by:** shared
- **Formula:** the number of games behind the numbers beside it: regular-season games with a stat line in the window shown (on Regression Watch: this season's games up to the as-of, the games behind his PPG, xFP/game and FPOE/game)
- **Source:** fact_player_week; twm.modules.regression_watch.projection (player_state)

### Games for half weight \*

After this many games a player's FPOE/game deserves half its face value.

- **Name:** `games_for_half_weight`; **unit:** games; **used by:** regression_watch
- **Formula:** noise_variance / signal_variance: the g where r(g) = 0.5
- **Source:** twm.modules.regression_watch.stability

### Garbage time \*

Plays after the game is effectively decided. Stats piled up then say little about next week, so views can drop them.

- **Name:** `is_garbage_time`; **unit:** boolean; **used by:** shared, regression_watch
- **Formula:** win probability below 0.05 or above 0.95, except in the final 120 seconds of a half when the score is within 8 points
- **Source:** fact_play.is_garbage_time (twm.situations)

### Hit rate by rank

How often players ranked this high hit, counted over earlier reconstructed lists of the same position (seasons before this list's season, so the badge never uses outcomes the list could not have known).

- **Name:** `rank_bucket_hit_rate`; **unit:** share (0-1); **used by:** waiver_radar
- **Formula:** hits / picks at ranks 1-5, 6-10 or 11-25 of the reconstructed (backtest) lists of the same position, over the seasons before the list's season
- **Source:** twm.backtest.metrics.DEFAULT_BUCKETS; web/lib/buckets.ts

### How much a matchup matters

The honest size of the effect: how many points per game really separated players facing the easiest matchups from those facing the hardest, out of sample. The playoff planner's table shows it per position; it is small for most.

- **Name:** `matchup_gap`; **unit:** points per game; **used by:** playoff_planner
- **Formula:** walk-forward 2013-2025: players facing the easiest fifth of matchups (as-of raw rating) minus those facing the hardest fifth, actual minus usual points per game in weeks 15-17
- **Source:** reports/playoff_planner/effects.csv

### Implied team total

How many points the betting market expected a team to score, from the spread and the over/under. Only known right before kickoff, so P1 uses it for games already played.

- **Name:** `implied_team_total`; **unit:** points; **used by:** shared
- **Formula:** home: (total_line + spread_line) / 2; away: (total_line - spread_line) / 2 (spread_line > 0 = home favored); closing lines of played games only
- **Source:** fact_game.home_implied_total, fact_game.away_implied_total
- **Verified:** spread_line sign checked in A3 (docs/assumptions.md section 7)

### Interceptions thrown

The actual number in the game, counted by ffopportunity over the same plays as its expected value.

- **Name:** `interceptions`; **unit:** interceptions; **used by:** regression_watch
- **Formula:** fact_opportunity_week.pass_interception, from the same ffopportunity row (fact_opportunity_week), i.e. over the same plays
- **Source:** twm.modules.regression_watch.player_week (fact_opportunity_week)

### Kickoff spot after a score

Where the other team starts after a score (the kickoff rules changed in 2024 and 2025, so it is measured, not assumed).

- **Name:** `kickoff_spot`; **unit:** yardline_100; **used by:** decisions
- **Formula:** mean receiving start of the same season's kickoffs in earlier weeks (at least decisions.kickoff_min_kicks), else the previous season's, rounded to the yard
- **Source:** twm.modules.decisions.grade_inputs

### Legit (tested, not shown) \*

Tested, not shown: a starter whose production is carried by his opportunity, not by luck. It predicted nothing (in the backtest 59.0% of the players it tagged stayed starters, and so did 61.1% of every starter), so the lists do not carry it.

- **Name:** `legit`; **unit:** yes/no; **used by:** regression_watch
- **Formula:** tested, not shown: PPG rank inside the starter threshold (QB top 12, RB top 24, WR top 24, TE top 12) AND FPOE/game not in the top decile of his position's universe; D3's backtest only, dropped from the product on 2026-09-30
- **Source:** twm.modules.regression_watch.tags

### Log loss

Like the Brier score, it grades probabilities against what happened, but it punishes a confident miss much harder. Lower is better. Example: saying 99% for something that does not happen costs far more than saying 60% for it.

- **Name:** `log_loss`; **unit:** 0 and up (lower is better); **used by:** decisions, questionable
- **Formula:** mean over the rows of -[y ln(p) + (1 - y) ln(1 - p)], y = 1 when it happened, the probability p kept a hair away from 0 and 1
- **Source:** twm.modules.decisions.wp.log_loss; twm.modules.questionable.table.log_loss

### Luck (wins above expected)

How many more games you won than your scores deserved. +1.5 means the schedule handed you about one and a half extra wins; a negative number means you lost games your points would usually win.

- **Name:** `luck`; **unit:** wins; **used by:** my_league
- **Formula:** wins (a tie half) - expected wins; expected wins = the sum over the final regular-season weeks of the share of the other teams the team outscored that week (a tie half)
- **Source:** twm.league.luck (twm league odds; the local weekly report)

### Matchup rating

How friendly an opponent is: 1.10 means players at that position scored 10% more than average against it. It moves points a little; for tight ends and kickers the backtest found no gain, so they count as 1.00.

- **Name:** `matchup_rating`; **unit:** multiplier (1.00 = average); **used by:** playoff_planner
- **Formula:** points the opponent allowed to the position per game this season over the league's average per team-game, shrunk toward 1.00 with k pseudo-games (k by position: QB 33, RB 12, WR 52, TE 100, K 27, DST 8); for a D/ST, the D/ST points the opposing offense gives up; 'adjusted' also divides by what the units it faced usually score
- **Source:** twm.modules.playoff_planner (frozen rule, docs/playoff_planner.md)

### Mean absolute error (MAE)

How far a projection missed on average, whether it was too high or too low. Lower is better. Example: projections of 12 and 8 points per game for two players who both then scored 10 per game miss by 2 each, so the MAE is 2.

- **Name:** `mae`; **unit:** points per game (lower is better); **used by:** regression_watch
- **Formula:** mean over the graded players of |actual rest-of-season points per game - the projection|
- **Source:** twm.modules.regression_watch.backtest (metric mae)

### Model probability

The model's own calibrated probability. The site shows the chance instead: in the backtest the model's probabilities ran too high for the most likely players.

- **Name:** `model_probability`; **unit:** probability (0-1); **used by:** waiver_radar
- **Formula:** the production model's predicted probability of y_hit after an isotonic calibration fit on the validation season (stored as score in the predictions store)
- **Source:** twm.modules.waiver_radar.models

### Neutral situation \*

Plays where the game is still in the balance and neither team is forced to pass or run; the fairest view of how a team really plays.

- **Name:** `is_neutral`; **unit:** boolean; **used by:** shared
- **Formula:** win probability from 0.2 to 0.8 and more than 120 seconds left in the half
- **Source:** fact_play.is_neutral (twm.situations)

### Noise variance per game \*

How much a single game's value swings by luck alone.

- **Name:** `noise_variance`; **unit:** points squared; **used by:** regression_watch
- **Formula:** var(odd-game mean - even-game mean) / mean(1 / odd games + 1 / even games), across player-seasons of a position
- **Source:** twm.modules.regression_watch.stability

### Opportunities

How many chances he had in the game.

- **Name:** `n_opportunities`; **unit:** plays; **used by:** regression_watch
- **Formula:** targets + carries + pass attempts (two-point tries included, sacks not) with an ffopportunity per-play row
- **Source:** twm.modules.regression_watch.player_week

### Opportunities in garbage time \*

How many of his chances came after the game was decided.

- **Name:** `n_opportunities_garbage`; **unit:** plays; **used by:** regression_watch
- **Formula:** n_opportunities on garbage-time plays (fact_play.is_garbage_time)
- **Source:** twm.modules.regression_watch.player_week

### PR-AUC

How cleanly the top of a model's list is filled with the cases that really happened, for rare events. Higher is better, but guessing does not score 0.5: a random order scores the share of cases that happened. Example: if 1 player in 4 really fell off a cliff, a random order scores about 0.25, and a useful model clearly more.

- **Name:** `pr_auc`; **unit:** 0-1 (guessing = the share of cases that happened); **used by:** board, hot_seat
- **Formula:** average precision: the precision at each case that happened, going down the list from the highest probability, averaged over those cases (the area under the precision-recall curve)
- **Source:** twm.backtest.metrics.pr_auc (sklearn average_precision_score)

### Pass attempts

The actual number in the game, counted by ffopportunity over the same plays as its expected value.

- **Name:** `pass_attempts`; **unit:** passes; **used by:** regression_watch
- **Formula:** fact_opportunity_week.pass_attempt (sacks and two-point tries excluded), from the same ffopportunity row (fact_opportunity_week), i.e. over the same plays
- **Source:** twm.modules.regression_watch.player_week (fact_opportunity_week)

### Passing touchdowns

The actual number in the game, counted by ffopportunity over the same plays as its expected value.

- **Name:** `passing_tds`; **unit:** touchdowns; **used by:** regression_watch
- **Formula:** fact_opportunity_week.pass_touchdown, from the same ffopportunity row (fact_opportunity_week), i.e. over the same plays
- **Source:** twm.modules.regression_watch.player_week (fact_opportunity_week)

### Passing yards

The actual number in the game, counted by ffopportunity over the same plays as its expected value.

- **Name:** `passing_yards`; **unit:** yards; **used by:** regression_watch
- **Formula:** fact_opportunity_week.pass_yards_gained, from the same ffopportunity row (fact_opportunity_week), i.e. over the same plays
- **Source:** twm.modules.regression_watch.player_week (fact_opportunity_week)

### Playoff odds

Your chance to make the playoffs, from playing out the rest of the season thousands of times on your real schedule. Early in the season it leans on the league average, so it moves a lot week to week. Local only.

- **Name:** `playoff_odds`; **unit:** share (0-1); **used by:** my_league
- **Formula:** share of 20,000 seeded simulated seasons in which the team finishes in the playoff places: every remaining matchup on the real schedule, each team's weekly score drawn around its season mean shrunk toward the league mean (6 pseudo-weeks), seeded by record then ESPN's tiebreak, then the playoff bracket (docs/my_league.md)
- **Source:** twm.league.odds (twm league odds; the local weekly report)

### Points per game (PPG)

A player's fantasy points divided by the games he played: what he scores in a typical game. Higher is better for your team. Example: 45 points in 3 games is 15 PPG.

- **Name:** `ppg`; **unit:** fantasy points per game; **used by:** shared
- **Formula:** fantasy points (config/scoring.yaml) summed over the games counted / the number of those games (each page says which games: e.g. this season so far, or last season)
- **Source:** fact_player_week (twm.scoring.score_sql)

### Points per opportunity

How much he scores with each carry or target; more work times this gives the predicted points.

- **Name:** `points_per_opportunity`; **unit:** PPR points per carry or target; **used by:** teammate_out
- **Formula:** his PPR points / (carries + targets) over his baseline games, with 20 opportunities at his position's past average added
- **Source:** twm.modules.teammate_out (frozen table, docs/teammate_out.md)

### Precision@10

The share of a list's top 10 who became a fantasy starter soon (a hit). If you had picked up the top 10 at a position that week, it is the share that would have given you a starter week. The track record averages it over every weekly list.

- **Name:** `precision_at_10`; **unit:** share (0-1); **used by:** waiver_radar
- **Formula:** per weekly list: players among its top 10 with y_hit / 10 (a list with fewer than 10 players divides by its size); pooled = the mean over every list (weeks x positions) of the seasons
- **Source:** twm.backtest.metrics

### ROC-AUC

How well a model puts the cases that happened above the ones that did not. 0.5 is no better than guessing and 1 is a perfect order, so higher is better. Example: 0.80 means that in 8 of 10 pairs of one coach who was let go and one who was not, the model gave the first a higher estimate.

- **Name:** `roc_auc`; **unit:** 0-1 (0.5 = guessing); **used by:** hot_seat
- **Formula:** the probability that a random row whose outcome happened gets a higher probability than a random row whose outcome did not (ties count half): the area under the ROC curve, over the pooled walk-forward test rows
- **Source:** twm.modules.hot_seat.evaluation

### Receiving touchdowns

The actual number in the game, counted by ffopportunity over the same plays as its expected value.

- **Name:** `receiving_tds`; **unit:** touchdowns; **used by:** regression_watch
- **Formula:** fact_opportunity_week.rec_touchdown, from the same ffopportunity row (fact_opportunity_week), i.e. over the same plays
- **Source:** twm.modules.regression_watch.player_week (fact_opportunity_week)

### Receiving yards

The actual number in the game, counted by ffopportunity over the same plays as its expected value.

- **Name:** `receiving_yards`; **unit:** yards; **used by:** regression_watch
- **Formula:** fact_opportunity_week.rec_yards_gained, from the same ffopportunity row (fact_opportunity_week), i.e. over the same plays
- **Source:** twm.modules.regression_watch.player_week (fact_opportunity_week)

### Recency-weighted xFP per game \*

His expected points per game, with recent games counting a little more.

- **Name:** `recency_weighted_xfp`; **unit:** points per game; **used by:** regression_watch
- **Formula:** sum of 0.5 ** (k / h) x xFP over his games this season / sum of 0.5 ** (k / h), k = games before his latest one, h = the half-life (none / 8 / 4 / 2 games; chosen per season on earlier seasons)
- **Source:** twm.modules.regression_watch.projection

### Receptions

The actual number in the game, counted by ffopportunity over the same plays as its expected value.

- **Name:** `receptions`; **unit:** catches; **used by:** regression_watch
- **Formula:** fact_opportunity_week.receptions, from the same ffopportunity row (fact_opportunity_week), i.e. over the same plays
- **Source:** twm.modules.regression_watch.player_week (fact_opportunity_week)

### Reliability after g games \*

The share of a g-game average that is real rather than luck; it grows with g.

- **Name:** `reliability`; **unit:** share (0-1); **used by:** regression_watch
- **Formula:** r(g) = signal_variance / (signal_variance + noise_variance / g)
- **Source:** twm.modules.regression_watch.stability

### Rest-of-season projection \*

The points per game we expect for the rest of the season: his opportunity, plus the small part of his luck-or-skill surplus that tends to last.

- **Name:** `ppg_ros`; **unit:** points per game; **used by:** regression_watch
- **Formula:** recency-weighted xFP/game + r(g) x FPOE/game (spec: shrink toward 0) or m + r(g) x (FPOE/game - m) (toward the position's average m); r(g) = shrinkage_factor after his g games with an opportunity, estimated only on seasons before this one; the variant (target, half-life, garbage time) is chosen on earlier seasons (docs/regression_watch.md)
- **Source:** twm.modules.regression_watch.projection

### Rostership (percent of leagues)

How many real leagues have the player on a roster. It checks the candidate pool (a player owned in fewer than 50% of leagues is really available); the pool itself never uses it, because it does not exist before 2020.

- **Name:** `owned_avg`; **unit:** percent (0-100); **used by:** waiver_radar
- **Formula:** FantasyPros' average share of leagues rostering the player across sites (fact_ranking.player_owned_avg) from the season's latest weekly ranking public at the as-of (the Friday before that week's games); owned_espn = ESPN's share (fact_ranking.player_owned_espn). 2020 partly, 2021 on
- **Source:** fact_ranking.player_owned_avg, fact_ranking.player_owned_espn

### Rushing touchdowns

The actual number in the game, counted by ffopportunity over the same plays as its expected value.

- **Name:** `rushing_tds`; **unit:** touchdowns; **used by:** regression_watch
- **Formula:** fact_opportunity_week.rush_touchdown, from the same ffopportunity row (fact_opportunity_week), i.e. over the same plays
- **Source:** twm.modules.regression_watch.player_week (fact_opportunity_week)

### Rushing yards

The actual number in the game, counted by ffopportunity over the same plays as its expected value.

- **Name:** `rushing_yards`; **unit:** yards; **used by:** regression_watch
- **Formula:** fact_opportunity_week.rush_yards_gained, from the same ffopportunity row (fact_opportunity_week), i.e. over the same plays
- **Source:** twm.modules.regression_watch.player_week (fact_opportunity_week)

### Seconds wasted with timeouts in hand

Clock the team let run when a timeout would have kept its last possession alive, while it kept timeouts it never used.

- **Name:** `timeout_seconds_wasted`; **unit:** seconds per team-game; **used by:** decisions
- **Formula:** while down 1-8 in the final 2:00, after an opponent play that leaves K(d', t - 1) < clock <= K(d', -1) (decisive), no team timeout and a runoff (next snap's gap minus the play's measured seconds) >= 10 s; per opponent drive the first k such runoffs, k = the timeouts the team still held at the drive's end
- **Source:** twm.modules.decisions.clock

### Sell-high \*

He has scored well above what his chances were worth, and the projection says it will fade: a good time to trade him away.

- **Name:** `sell_high`; **unit:** yes/no; **used by:** regression_watch
- **Formula:** FPOE/game in the top ceil(n / 10) FPOE/game of his position's universe at that as-of AND ppg_ros at least X (tag_threshold_x) below his PPG
- **Source:** twm.modules.regression_watch.tags

### Shrinkage factor \*

How much of a player's points over expected to keep when projecting the rest of the season; the rest is expected to fade.

- **Name:** `shrinkage_factor`; **unit:** share (0-1); **used by:** regression_watch
- **Formula:** reliability r(g) of FPOE/game for his position after his g games, estimated only from seasons before the one projected (stability.shrinkage(seasons))
- **Source:** twm.modules.regression_watch.stability

### Signal variance \*

How much players truly differ in the metric once luck is taken out.

- **Name:** `signal_variance`; **unit:** points squared; **used by:** regression_watch
- **Formula:** covariance, across player-seasons of a position, of FPOE/game in his odd games and in his even games
- **Source:** twm.modules.regression_watch.stability

### Split-half correlation

How much a number repeats within a season: near 1 it is a lasting trait (role or skill), near 0 it was mostly luck.

- **Name:** `split_half_correlation`; **unit:** correlation (-1 to 1); **used by:** regression_watch
- **Formula:** Pearson correlation, across player-seasons (8+ games with a target, carry or pass; 2009 on; one position per player-season), of a metric in one half of his games with the same metric in the other half: odd against even games, or his first n // 2 games against the rest
- **Source:** twm.modules.regression_watch.stability

### Start/sit odds

Choosing between two players? This is how often a player ranked like the first outscored one ranked like the second. Under 55% it is a close call: either is a reasonable start. Local only: it uses the experts' weekly ranks, which may not be republished.

- **Name:** `start_sit_odds`; **unit:** share (0-1); **used by:** my_league
- **Formula:** P(A outscores B) for independent draws of the league points of players at A's and B's weekly expert ranks in 2020-2025 (each rank pooled with its neighbours), a tie counting one half; walk-forward checked on 2022-2025 (docs/start_sit.md)
- **Source:** twm.modules.startsit (twm league startsit; the local weekly report)

### Starter finish

The player scored like a weekly starter in a 12-team league that week.

- **Name:** `is_starter_finish`; **unit:** boolean; **used by:** waiver_radar
- **Formula:** weekly_pos_rank <= the position's starter threshold (QB top 12, RB top 24, WR top 24, TE top 12)
- **Source:** twm.modules.waiver_radar.labels.weekly_finishes

### TD rate over expected \*

Touchdowns beyond what his chances were worth, per pass, carry or target.

- **Name:** `td_rate_over_expected`; **unit:** touchdowns per chance; **used by:** regression_watch
- **Formula:** (passing_tds + rushing_tds + receiving_tds - the same three _exp) / (pass_attempts + carries + targets), over the games of one half of a player-season (summed, then divided)
- **Source:** twm.modules.regression_watch.stability

### Targets

The actual number in the game, counted by ffopportunity over the same plays as its expected value.

- **Name:** `targets`; **unit:** targets; **used by:** regression_watch
- **Formula:** fact_opportunity_week.rec_attempt: passes thrown to him (two-point tries excluded), from the same ffopportunity row (fact_opportunity_week), i.e. over the same plays
- **Source:** twm.modules.regression_watch.player_week (fact_opportunity_week)

### Timeouts unused in a lost one-score game

The team lost a close game while the opponent ran out the clock, with timeouts still in its pocket. A fact, not always a mistake: see timeout_seconds_wasted.

- **Name:** `timeouts_unused`; **unit:** timeouts (0-3) per team-game; **used by:** decisions
- **Formula:** regulation loss by 1-8; the opponent's drive held the game's last snap and had a 4th-quarter snap with <= 120 s where K(d, t) < clock <= K(d, 0) (t >= 1 the team's timeouts); the team's timeouts at that drive's last snap; a case when >= 1
- **Source:** twm.modules.decisions.clock

### Two-point rate

How often a two-point try succeeds.

- **Name:** `two_point_rate`; **unit:** share (0-1); **used by:** decisions
- **Formula:** successful two-point tries / tries in the last 5 seasons before S
- **Source:** twm.modules.decisions.tries

### Vacated share

The part of his team's running plays (passes) the missing starter usually gets: the work that is up for grabs.

- **Name:** `vacated_share`; **unit:** share (0-1); **used by:** teammate_out
- **Formula:** the absent starter's mean carry (target) share in the team's games this season in which he played (summed when several starters are out)
- **Source:** twm.modules.teammate_out (frozen table, docs/teammate_out.md)

### WP if going for it

The offense's chance to win if it goes for it here.

- **Name:** `wp_go`; **unit:** probability (0-1); **used by:** decisions
- **Formula:** P(convert) x expected WP after a conversion + (1 - P(convert)) x expected WP after a failure (G2 ball-spot tables, G1 WP; fold models of seasons < S)
- **Source:** twm.modules.decisions.grade

### WP if going for two

The scoring team's chance to win if it goes for two.

- **Name:** `wp_two_point`; **unit:** probability (0-1); **used by:** decisions
- **Formula:** two-point rate x WP(+2) + (1 - rate) x WP(+0), the opponent then receiving
- **Source:** twm.modules.decisions.grade

### WP if kicking a field goal

The offense's chance to win if it kicks.

- **Name:** `wp_fg`; **unit:** probability (0-1); **used by:** decisions
- **Formula:** P(make) x WP(3 points up, opponent receives the kickoff) + (1 - P(make)) x WP(the opponent's ball at the spot of the kick or its 20); only within the longest field goal made before S
- **Source:** twm.modules.decisions.grade

### WP if kicking the extra point

The scoring team's chance to win if it kicks after a touchdown.

- **Name:** `wp_kick`; **unit:** probability (0-1); **used by:** decisions
- **Formula:** PAT rate x WP(+1) + (1 - PAT rate) x WP(+0), the opponent then receiving the kickoff
- **Source:** twm.modules.decisions.grade

### WP if punting

The offense's chance to win if it punts.

- **Name:** `wp_punt`; **unit:** probability (0-1); **used by:** decisions
- **Formula:** G2's punt_expected_wp with the measured kickoff spot after a return touchdown; only from yardlines punts come from
- **Source:** twm.modules.decisions.grade

### WP left on the table (end of half)

What running out the half cost in win probability, by the model.

- **Name:** `passivity_wp_left`; **unit:** probability (0-1; shown as WP points); **used by:** decisions
- **Formula:** WP(decision snap) - WP(the second half's first snap: same score, 3 timeouts each, the receiving team at the kickoff spot), both from season S's own WP fold model
- **Source:** twm.modules.decisions.clock

### WP lost

How much win probability a decision gave away by the model's numbers.

- **Name:** `wp_lost`; **unit:** probability (0-1; shown as WP points); **used by:** decisions
- **Formula:** WP of the best option - WP of the chosen option (0 when the best was chosen); summed over clear decisions only
- **Source:** twm.modules.decisions.grade

### WP lost per game

A coach's decision cost per game, for the leaderboard.

- **Name:** `wp_lost_per_game`; **unit:** probability (0-1; shown as WP points); **used by:** decisions
- **Formula:** (fourth-down + two-point WP lost on clear decisions) / games coached
- **Source:** twm.modules.decisions.coach

### WPA (win probability added) \*

How much one play or decision changed a team's chance of winning.

- **Name:** `wpa`; **unit:** probability points; **used by:** decisions
- **Formula:** win probability after the play minus before it, for the offense
- **Source:** fact_play.wpa

### Weekly position rank

Where a player's score ranked at his position that week: rank 5 at WR means four receivers scored more. Built from the finished week, so it is outcome data for labels; a feature must rank only the games public at its as-of.

- **Name:** `weekly_pos_rank`; **unit:** rank (1 = best); **used by:** waiver_radar
- **Formula:** 1 + the number of players of the same position with more fantasy points that regular-season week, among every player with a stat line (rank 'min': ties share the better rank); position = his point-in-time roster position that week (fact_roster_week.position; without a row that week his latest earlier roster week of the season, else that game's fact_snaps.position); QB/RB/WR/TE only (fullbacks and others are not ranked). Column pos_rank of weekly_finishes
- **Source:** twm.modules.waiver_radar.labels.weekly_finishes (fact_player_week, fact_roster_week.position, fact_snaps.position)

### Win probability \*

The chance the offense wins from this moment, given score, time, field position and more. Garbage time and neutral situations are defined from it.

- **Name:** `wp`; **unit:** probability (0-1); **used by:** shared, decisions
- **Formula:** nflfastR's estimated probability that the team with the ball wins, before the snap
- **Source:** fact_play.wp

### Win probability (our model)

The chance the offense wins from this moment, from a model that never saw the season it scores. It replaces nflfastR's wp in the decision grades.

- **Name:** `own_wp`; **unit:** probability (0-1); **used by:** decisions
- **Formula:** trained walk-forward on seasons before the play's season (G1); since G1b a smooth possession-symmetric spline logistic model of the play state up to 15:00 of the fourth quarter, handing over to a monotone LightGBM by 10:00 left (wp_select.CHOSEN); isotonic calibration only where it helped on the validation season and kept the model within the smoothness limits
- **Source:** twm.modules.decisions.wp

### YAC over expected \*

Yards after the catch beyond what an average receiver gains on the same catches.

- **Name:** `yac_over_expected`; **unit:** yards per catch; **used by:** regression_watch
- **Formula:** (yac - yac_exp) / receptions, over the games of one half of a player-season (summed, then divided)
- **Source:** twm.modules.regression_watch.stability

### Yards after the catch

Yards he gained with the ball after catching it.

- **Name:** `yac`; **unit:** yards; **used by:** regression_watch
- **Formula:** receiving_yards - air_yards over his caught targets (two-point tries excluded), from fact_opportunity_pass
- **Source:** twm.modules.regression_watch.player_week (fact_opportunity_pass)

### xFP added up play by play \*

The same expected points as the weekly xFP, counted play by play (equal within rounding).

- **Name:** `play_xfp`; **unit:** points; **used by:** regression_watch
- **Formula:** the sum over his plays of ffopportunity's per-play expected stats scored like xfp: a pass is worth pass_completion_exp catches (completions for the passer) and pass_completion_exp x (air_yards + yards_after_catch_exp) yards plus pass_touchdown_exp (and, for the passer, pass_interception_exp); a run rush_yards_exp and rush_touchdown_exp; a two-point try only two_point_conv_exp
- **Source:** twm.modules.regression_watch.player_week

### xFP in garbage time \*

What his chances after the game was decided were worth.

- **Name:** `xfp_garbage`; **unit:** points; **used by:** regression_watch
- **Formula:** the expected points of his garbage-time plays (fact_play.is_garbage_time), counted play by play as in play_xfp (NULL without an ffopportunity row)
- **Source:** twm.modules.regression_watch.player_week

### xFP without garbage time \*

What his chances were worth while the game was still in doubt.

- **Name:** `xfp_ng`; **unit:** points; **used by:** regression_watch
- **Formula:** xfp - xfp_garbage: the weekly xFP minus the expected points of his garbage-time targets, carries and passes (the own walk-forward xFP's in Regression Watch and on the player pages)
- **Source:** twm.modules.regression_watch.player_week

## Features

### 40-yard dash

Straight-line speed.

- **Name:** `combine_forty`; **unit:** seconds; **used by:** board
- **Formula:** fact_combine.forty (his latest row)
- **Source:** twm.modules.board.features

### A player ahead of him is out

Someone who played ahead of him is out: the classic waiver opportunity.

- **Name:** `top_teammate_out`; **unit:** boolean; **used by:** waiver_radar
- **Formula:** an unavailable same-position teammate averaged a higher snap share than he did over the same games (the teammate's last 3 team games up to his last appearance)
- **Source:** twm.modules.waiver_radar.features; fact_snaps, as vacated_target_share
- **Waiver Radar reason:** "{teammate}, who played more snaps than him, {why}"

### Age

Younger players are likelier to grow into a bigger role.

- **Name:** `age_at_asof`; **unit:** years; **used by:** waiver_radar
- **Formula:** (as-of date - dim_player.birth_date) / 365.25; NULL without a public birth date
- **Source:** twm.modules.waiver_radar.features; dim_player.birth_date
- **Waiver Radar reason:** "Is {value:.0f} years old: younger players more often grow into a bigger role"

### Age after the season

How old he is when the season ends; production falls with age, at different ages by position.

- **Name:** `age`; **unit:** years; **used by:** board
- **Formula:** (February 1 after season S - dim_player.birth_date) / 365.25; NULL without a birth date
- **Source:** twm.modules.board.features

### Age at the draft

Young draftees break out more often: they were productive in college earlier.

- **Name:** `age_at_draft`; **unit:** years; **used by:** board
- **Formula:** (first day of his draft - birth_date) / 365.25; his entry year's draft when undrafted (available.DRAFT_FIRST_DAY, 2000-2026)
- **Source:** twm.modules.board.features

### Aging curve

What a typical player of his position and age keeps of his points per game next season, learned only from earlier seasons.

- **Name:** `age_curve_ratio`; **unit:** ratio; **used by:** board
- **Formula:** the position's quadratic fit of PPG(s+1) / PPG(s) on age over earlier season pairs (s + 1 <= S; top-48 in s, 6+ games in s+1; 50+ pairs), evaluated at his age (clipped to the fitted ages)
- **Source:** twm.modules.board.features

### Air-yards share

Air yards are how far the ball travels past the line of scrimmage before it is caught or falls. A big share means a player gets the deep, valuable looks.

- **Name:** `air_yards_share`; **unit:** share (0-1); **used by:** waiver_radar, regression_watch
- **Formula:** player's receiving air yards / his team's air yards on all pass attempts (completions, incompletions and interceptions) in that game
- **Source:** fact_player_week.air_yards_share
- **Verified:** exact on all 4,419 2025 player-weeks with air yards (denominator from fact_play)

### Air-yards share

His share of the deep game.

- **Name:** `air_yards_share_s`; **unit:** share (0-1); **used by:** board
- **Formula:** his receiving air yards / his team's in his games of S
- **Source:** twm.modules.board.features

### Air-yards share, last 3 games

How much of the team's downfield passing is aimed at him.

- **Name:** `air_yards_share_avg3`; **unit:** share (0-1); **used by:** waiver_radar
- **Formula:** mean nflverse air_yards_share over the team's last 3 games (team games = regular-season games of the player's as-of team visible at the as-of; last = the most recent, avg3 = mean over the last 3 (fewer early in the season), season = mean over all; a game he missed counts as 0; NULL only when the team has no visible game); can be slightly negative (passes behind the line)
- **Source:** twm.modules.waiver_radar.features; fact_player_week.air_yards_share
- **Waiver Radar reason:** "Got {value:.0%} of his team's air yards (throws down the field) over the last {weeks} games"

### Best pick drafted at his position

How much capital the competition cost.

- **Name:** `draft_pos_best_pick_pd`; **unit:** overall pick; **used by:** board
- **Formula:** the lowest overall draft_pick among those picks; NULL when there is none
- **Source:** twm.modules.board.post_draft

### Best round drafted at his position

How much capital the competition cost.

- **Name:** `draft_pos_best_round_pd`; **unit:** round (1 = first); **used by:** board
- **Formula:** the lowest draft_round among those picks; NULL when there is none
- **Source:** twm.modules.board.post_draft

### Broad jump

Explosiveness.

- **Name:** `combine_broad_jump`; **unit:** inches; **used by:** board
- **Formula:** fact_combine.broad_jump
- **Source:** twm.modules.board.features

### Bye in the next 3 weeks

A bye costs a week of production.

- **Name:** `bye_in_next3`; **unit:** boolean; **used by:** waiver_radar
- **Formula:** his team has no scheduled game in at least one of the calendar weeks N+1 to N+3 (capped at the last regular-season week)
- **Source:** twm.modules.waiver_radar.features; fact_schedule, dim_week
- **Waiver Radar reason:** "Has a bye week in the next 3 weeks" (when no: "No bye week in the next 3 weeks")

### Career targets

Mileage as a receiver.

- **Name:** `career_targets`; **unit:** targets; **used by:** board
- **Formula:** targets 1999 .. S
- **Source:** twm.modules.board.features

### Career touches

Mileage: wear accumulated over a career.

- **Name:** `career_touches`; **unit:** touches; **used by:** board
- **Formula:** carries + receptions in every regular season 1999 .. S (a career before 1999 is cut)
- **Source:** twm.modules.board.features

### Carry share

The share of his team's running plays given to a player: the running-back version of target share.

- **Name:** `carry_share`; **unit:** share (0-1); **used by:** waiver_radar, regression_watch, teammate_out
- **Formula:** player carries / team carries in that game (fact_player_week.carries / fact_team_week.carries; 0 when the team had no carry)
- **Source:** fact_player_week.carries, fact_team_week.carries
- **Verified:** team carries equal the sum of the team's player carries on all 6,814 regular-season team-games 2013-2025; player carries equal his play-by-play runs and kneels without two-point tries on 99.996% of player-games

### Carry share, last 3 games

How much of the running game goes to him lately.

- **Name:** `carry_share_avg3`; **unit:** share (0-1); **used by:** waiver_radar
- **Formula:** mean carry_share over the team's last 3 games (team games = regular-season games of the player's as-of team visible at the as-of; last = the most recent, avg3 = mean over the last 3 (fewer early in the season), season = mean over all; a game he missed counts as 0; NULL only when the team has no visible game)
- **Source:** twm.modules.waiver_radar.features; fact_player_week.carries, fact_team_week.carries
- **Waiver Radar reason:** "Got {value:.0%} of his team's carries over the last {weeks} games"

### Carry share, last game

The share of his team's runs he got last game.

- **Name:** `carry_share_last`; **unit:** share (0-1); **used by:** waiver_radar
- **Formula:** carry_share in the team's last game (team games = regular-season games of the player's as-of team visible at the as-of; last = the most recent, avg3 = mean over the last 3 (fewer early in the season), season = mean over all; a game he missed counts as 0; NULL only when the team has no visible game)
- **Source:** twm.modules.waiver_radar.features; fact_player_week.carries, fact_team_week.carries
- **Waiver Radar reason:** "Got {value:.0%} of his team's carries last game"

### Coach tenure

How long the coach has been in charge of this team.

- **Name:** `tenure_seasons`; **unit:** seasons; **used by:** hot_seat
- **Formula:** seasons of the coach's current stint with the team, this one included: consecutive seasons back from this one in which he coached it a game (coach_game, kickoff <= as-of)
- **Source:** twm.modules.hot_seat.features

### Combine height

Size.

- **Name:** `combine_height`; **unit:** inches; **used by:** board
- **Formula:** fact_combine.height_in
- **Source:** twm.modules.board.features

### Combine weight

Size.

- **Name:** `combine_weight`; **unit:** pounds; **used by:** board
- **Formula:** fact_combine.wt
- **Source:** twm.modules.board.features

### Consecutive losing seasons

How many losing seasons in a row the coach has had with this team before this one.

- **Name:** `consecutive_losing_seasons`; **unit:** seasons; **used by:** hot_seat
- **Formula:** seasons right before this one, counted back until one is not losing or not coached by him, in which the coach coached the team and its regular-season win share (ties 0.5) was < 0.5
- **Source:** twm.modules.hot_seat.features

### Defense and return touchdowns per game

Touchdowns scored by the defense or the return teams: rare, but worth a lot.

- **Name:** `dst_tds_per_game`; **unit:** touchdowns per game; **used by:** streamer
- **Formula:** mean (kickoff, punt, interception, fumble and blocked-kick return TDs), per game = mean over the team's regular-season games of the season visible at the as-of (NULL before its first game); NULL for K rows
- **Source:** twm.modules.streamer.features; fact_defense_week.interception_return_tds, fact_defense_week.fumble_return_tds, fact_defense_week.kickoff_return_tds, fact_defense_week.punt_return_tds, fact_defense_week.blocked_kick_return_tds
- **Streamer reason:** "Scores {value:.2f} defense or return touchdowns per game"

### Defense timeouts left

The other team's timeouts: they can stop the clock to get the ball back.

- **Name:** `defteam_timeouts_remaining`; **unit:** 0-3; **used by:** decisions
- **Formula:** the defense's timeouts left in the half, before the snap
- **Source:** fact_play.defteam_timeouts_remaining; twm.modules.decisions.wp_data

### Defensive EPA per play allowed (neutral)

How efficient opponents are against this defense; lower is better.

- **Name:** `def_epa_neutral`; **unit:** points per play; **used by:** hot_seat
- **Formula:** as off_epa_neutral for the opponents' plays against the team (defteam); higher = worse
- **Source:** twm.modules.hot_seat.features

### Defensive EPA trend

Positive: the defense has been allowing more lately than over the whole season.

- **Name:** `def_epa_neutral_trend`; **unit:** points per play; **used by:** hot_seat
- **Formula:** def_epa_neutral over the last 4 games minus the season to date (NULL until more than 4 games); positive = the defense has been worse lately
- **Source:** twm.modules.hot_seat.features

### Depth-chart move

How many spots he moved up the depth chart in a week.

- **Name:** `depth_rank_change`; **unit:** ranks (positive = promoted); **used by:** waiver_radar
- **Formula:** depth_rank_prev - depth_rank_now (NULL unless both exist)
- **Source:** twm.modules.waiver_radar.features; fact_depth_chart
- **Waiver Radar reason:** "Moved up the depth chart from No. {prev:.0f} to No. {value:.0f} at {pos}"

### Depth-chart rank a week earlier

Where he was listed a week ago.

- **Name:** `depth_rank_prev`; **unit:** rank (1 = starter); **used by:** waiver_radar
- **Formula:** depth_rank_now computed from the chart in force 7 days before the as-of
- **Source:** twm.modules.waiver_radar.features; fact_depth_chart
- **Waiver Radar reason:** "Was No. {value:.0f} at {pos} on his team's depth chart a week earlier"

### Depth-chart rank now

Where the team lists him at his position: 1 is the starter.

- **Name:** `depth_rank_now`; **unit:** rank (1 = starter); **used by:** waiver_radar
- **Formula:** on his team's depth chart in force at the as-of (daily charts 2025+: the team's latest snapshot with dt <= as-of; weekly charts: the latest visible week's chart), 1 + the number of players of his position group with a better (lower) depth rank in any offensive slot of the group; ties share the better rank. Slots map to groups by twm.modules.waiver_radar.features.slot_group (QB; RB, HB, FB, J; WR, LWR, RWR, SWR, WR1/WR2, WRE, WE; TE, LTE, RTE, H-B, F and combined slots with TE). NULL when he is not in a slot of his group
- **Source:** twm.modules.waiver_radar.features; fact_depth_chart.position, depth_rank
- **Waiver Radar reason:** "Is No. {value:.0f} at {pos} on his team's depth chart"

### Division rank

Where the team stands in its division right now.

- **Name:** `division_rank`; **unit:** rank (1 = best); **used by:** hot_seat
- **Formula:** rank in the division (dim_team.team_division) by win share (wins + 0.5 ties) / games to date among the division's teams with a game; tied teams share the better rank
- **Source:** twm.modules.hot_seat.features

### Down

Which of the offense's four tries to gain the distance this is.

- **Name:** `down`; **unit:** 1-4; **used by:** decisions
- **Formula:** the down before the snap
- **Source:** fact_play.down; twm.modules.decisions.wp_data

### Draft pick

Draft capital, finer than the round.

- **Name:** `drafted_pick`; **unit:** overall pick; **used by:** board
- **Formula:** dim_player.draft_pick; NULL undrafted
- **Source:** twm.modules.board.features

### Draft picks at his position

His team drafted competition.

- **Name:** `draft_pos_count_pd`; **unit:** count; **used by:** board
- **Formula:** picks of his S team in the S+1 draft (dim_player draft fields, public May 15) whose combine position of that year is his (RB: RB/HB); NULL when no pick is visible
- **Source:** twm.modules.board.post_draft

### Draft round

Teams give early picks more chances.

- **Name:** `draft_round`; **unit:** round (1-7); **used by:** waiver_radar
- **Formula:** dim_player.draft_round; NULL when undrafted (see is_undrafted)
- **Source:** twm.modules.waiver_radar.features; dim_player.draft_round
- **Waiver Radar reason:** "Was drafted in round {value:.0f}: teams give higher draft picks more chances"

### Draft round

Teams give early picks more chances.

- **Name:** `drafted_round`; **unit:** round (1-7); **used by:** board
- **Formula:** dim_player.draft_round; NULL when undrafted
- **Source:** twm.modules.board.features

### Entering his third season

Second-year (rookie season just ended) vs third-year player.

- **Name:** `third_season`; **unit:** boolean; **used by:** board
- **Formula:** S - entry year = 1
- **Source:** twm.modules.board.features

### Era: long extra point (2015+)

Extra points got harder in 2015, which slightly changes what a touchdown is worth.

- **Name:** `era_pat_2015`; **unit:** boolean (0/1); **used by:** decisions
- **Formula:** 1 for seasons 2015 and later (extra points snapped from the 15-yard line)
- **Source:** fact_play.season; twm.modules.decisions.wp_data

### Era: new kickoff rules (2023+)

Kickoff rules changed where drives start after a score.

- **Name:** `era_kickoff_2023`; **unit:** boolean (0/1); **used by:** decisions
- **Formula:** 1 for seasons 2023 and later (fair-catch touchbacks to the 25 in 2023, the dynamic kickoff from 2024)
- **Source:** fact_play.season; twm.modules.decisions.wp_data

### Expected points per game (own xFP)

The points his opportunities were worth to an average player.

- **Name:** `xfp_per_game_s`; **unit:** points per game; **used by:** board
- **Formula:** sum of his own walk-forward xFP (Regression Watch, each season from models trained on earlier seasons) over the regular season of S / games_s; NULL before 2009
- **Source:** twm.modules.board.features

### Extra-point attempts per game

Extra points follow touchdowns, so this shows how often his offense scores.

- **Name:** `k_pat_att_per_game`; **unit:** attempts per game; **used by:** streamer
- **Formula:** sum of pat_att / games, over the kicker's regular-season games of the season with a field-goal or extra-point attempt visible at the as-of, for any team (NULL for DST rows and for a kicker without one)
- **Source:** twm.modules.streamer.features; fact_kicker_week.pat_att
- **Streamer reason:** "Kicks {value:.1f} extra points per game (his offense scores touchdowns)"

### FPOE (fantasy points over expected) \*

Points above or below what his chances were worth: partly skill, largely luck, and it tends to shrink toward zero.

- **Name:** `fpoe`; **unit:** points; **used by:** regression_watch, waiver_radar
- **Formula:** fantasy_points - xfp (the same player-game; a lost fumble or a return touchdown counts fully, having no expected value); in Regression Watch since H6-b2 and on the player pages since PXFP the xfp is the own walk-forward xFP (own_xfp)
- **Source:** twm.scoring.score_sql - twm.scoring.xfp_sql

### FPOE, last 3 games \*

Scoring above or below his chances lately; mostly luck, so it is weighted low.

- **Name:** `fpoe_avg3`; **unit:** points per game; **used by:** waiver_radar
- **Formula:** mean over the team's last 3 games of (fantasy_points - xfp) (team games = regular-season games of the player's as-of team visible at the as-of; last = the most recent, avg3 = mean over the last 3 (fewer early in the season), season = mean over all; a game he missed counts as 0; NULL only when the team has no visible game)
- **Source:** twm.modules.waiver_radar.features; fact_player_week, fact_opportunity_week
- **Waiver Radar reason:** "Scored {value:.1f} points per game more than his chances were worth over the last {weeks} games"

### Fantasy points, last 3 games

His recent scoring per game.

- **Name:** `fantasy_points_avg3`; **unit:** points per game; **used by:** waiver_radar
- **Formula:** mean fantasy points over the team's last 3 games (team games = regular-season games of the player's as-of team visible at the as-of; last = the most recent, avg3 = mean over the last 3 (fewer early in the season), season = mean over all; a game he missed counts as 0; NULL only when the team has no visible game)
- **Source:** twm.modules.waiver_radar.features; fact_player_week (twm.scoring.score_sql)
- **Waiver Radar reason:** "Averaged {value:.1f} fantasy points over the last {weeks} games"

### Fantasy points, last game

What he scored last game.

- **Name:** `fantasy_points_last`; **unit:** points; **used by:** waiver_radar
- **Formula:** fantasy points (config/scoring.yaml) in the team's last game (team games = regular-season games of the player's as-of team visible at the as-of; last = the most recent, avg3 = mean over the last 3 (fewer early in the season), season = mean over all; a game he missed counts as 0; NULL only when the team has no visible game)
- **Source:** twm.modules.waiver_radar.features; fact_player_week (twm.scoring.score_sql)
- **Waiver Radar reason:** "Scored {value:.1f} fantasy points last game"

### Field-goal accuracy, 0-39 yards

How reliable he is on short kicks this season.

- **Name:** `k_fg_pct_0_39`; **unit:** share (0-1); **used by:** streamer
- **Formula:** fg_made / (fg_made + fg_missed) in the distance bucket, summed over the kicker's regular-season games of the season with a field-goal or extra-point attempt visible at the as-of, for any team (NULL for DST rows and for a kicker without one); blocked kicks are not in nflverse's distance buckets and are left out; NULL without an attempt there (0-19, 20-29 and 30-39 yards)
- **Source:** twm.modules.streamer.features; fact_kicker_week.fg_made_30_39, fact_kicker_week.fg_missed_30_39
- **Streamer reason:** "Has made {value:.0%} of his field goals under 40 yards this season"

### Field-goal accuracy, 40-49 yards

How reliable he is on medium-long kicks this season.

- **Name:** `k_fg_pct_40_49`; **unit:** share (0-1); **used by:** streamer
- **Formula:** fg_made / (fg_made + fg_missed) in the distance bucket, summed over the kicker's regular-season games of the season with a field-goal or extra-point attempt visible at the as-of, for any team (NULL for DST rows and for a kicker without one); blocked kicks are not in nflverse's distance buckets and are left out; NULL without an attempt there (40-49 yards)
- **Source:** twm.modules.streamer.features; fact_kicker_week.fg_made_40_49, fact_kicker_week.fg_missed_40_49
- **Streamer reason:** "Has made {value:.0%} of his 40-49-yard field goals this season"

### Field-goal accuracy, 50+ yards

How reliable he is from 50 yards and beyond; coaches trust a strong leg with more long tries.

- **Name:** `k_fg_pct_50_plus`; **unit:** share (0-1); **used by:** streamer
- **Formula:** fg_made / (fg_made + fg_missed) in the distance bucket, summed over the kicker's regular-season games of the season with a field-goal or extra-point attempt visible at the as-of, for any team (NULL for DST rows and for a kicker without one); blocked kicks are not in nflverse's distance buckets and are left out; NULL without an attempt there (50-59 and 60+ yards)
- **Source:** twm.modules.streamer.features; fact_kicker_week.fg_made_50_59, fact_kicker_week.fg_missed_50_59
- **Streamer reason:** "Has made {value:.0%} of his 50+ yard field goals this season"

### Field-goal attempts per game

How often his team sends him out for field goals: the main source of kicker points.

- **Name:** `k_fg_att_per_game`; **unit:** attempts per game; **used by:** streamer
- **Formula:** sum of fg_att / games, over the kicker's regular-season games of the season with a field-goal or extra-point attempt visible at the as-of, for any team (NULL for DST rows and for a kicker without one)
- **Source:** twm.modules.streamer.features; fact_kicker_week.fg_att
- **Streamer reason:** "Tries {value:.1f} field goals per game"

### Field-goal distance

How long the kick would be, known before the snap.

- **Name:** `fg_distance`; **unit:** yards; **used by:** decisions
- **Formula:** yardline_100 + 18 (10 yards of end zone + the hold about 8 yards behind the line; fact_play.kick_distance - yardline_100 is 18 on 88% of 2023-2025 kicks, 19 on 12%)
- **Source:** fact_play.yardline_100; twm.modules.decisions.fieldgoal

### First-year coach

The coach is in his first season with this team.

- **Name:** `is_first_year_coach`; **unit:** boolean; **used by:** hot_seat
- **Formula:** tenure_seasons = 1
- **Source:** twm.modules.hot_seat.features

### Fourth down

Separates fourth-down tries (a chosen gamble) from third downs (which add sample).

- **Name:** `is_fourth_down`; **unit:** boolean (0/1); **used by:** decisions
- **Formula:** 1 when down = 4, 0 on third down
- **Source:** fact_play.down; twm.modules.decisions.conversion

### Fourth-down WP lost per game

How much win probability the coach's fourth-down calls have cost per game.

- **Name:** `fourth_down_wp_lost_per_game`; **unit:** probability (0-1); **used by:** hot_seat
- **Formula:** sum of wp_lost on the team's clear (graded) fourth downs in its games to date / games to date, from the stored Decision Report Card grades (never regraded); NULL before 2006 or when one of the games has no grades
- **Source:** twm.modules.hot_seat.features

### Games played

How many games he played (with a recorded play).

- **Name:** `games_s`; **unit:** games; **used by:** board
- **Formula:** regular-season games with a stat line in S
- **Source:** twm.modules.board.features

### Games played this season

How many games he has a stat line in so far.

- **Name:** `games_played_to_date`; **unit:** games; **used by:** waiver_radar
- **Formula:** regular-season games with a stat line visible at the as-of (the pool's games_to_date)
- **Source:** twm.modules.waiver_radar.features; twm.modules.waiver_radar.pool
- **Waiver Radar reason:** "Has played in {value:.0f} games this season"

### Games played to date

How many games the team has played so far this season.

- **Name:** `reg_games_played`; **unit:** games; **used by:** hot_seat
- **Formula:** the team's played regular-season games of the season visible at the as-of (fact_game)
- **Source:** twm.modules.hot_seat.features

### Games remaining

How much season is left to use him.

- **Name:** `team_games_remaining`; **unit:** games; **used by:** waiver_radar
- **Formula:** his team's regular-season fixtures after week N (fact_schedule, plus listed cancelled games)
- **Source:** twm.modules.waiver_radar.features; fact_schedule
- **Waiver Radar reason:** "His team has {value:.0f} games left this season"

### Games remaining

How many regular-season games the team still has to play.

- **Name:** `games_remaining`; **unit:** games; **used by:** hot_seat
- **Formula:** season length (the most regular-season games any team has in the visible schedule) - games played - a listed cancelled game once gone (2022 BUF/CIN after week 17)
- **Source:** twm.modules.hot_seat.features

### Gets the ball after halftime

In the first half, knowing you get the ball back after halftime is worth a little.

- **Name:** `receives_2h_kickoff`; **unit:** boolean (0/1); **used by:** decisions
- **Formula:** first half only: 1 when the possession team kicked the opening kickoff (it receives the second-half kickoff); 0 in the second half and overtime
- **Source:** fact_play.kickoff_attempt, fact_play.defteam (the game's first kickoff); twm.modules.decisions.wp_data

### Goal to go

Near the goal line a first down is a touchdown, and the defense has less field to cover.

- **Name:** `goal_to_go`; **unit:** boolean (0/1); **used by:** decisions
- **Formula:** 1 when the line to gain is the goal line (fact_play.goal_to_go; for a hypothetical state: ydstogo >= yardline_100)
- **Source:** fact_play.goal_to_go; twm.modules.decisions.conversion

### Goal-line opportunities per game

Chances inside the 10-yard line: the most valuable touches in fantasy.

- **Name:** `gl_opps_avg3`; **unit:** looks per game; **used by:** waiver_radar
- **Formula:** mean over the team's last 3 games of his targets plus carries (as above) with yardline_100 <= 10 (team games = regular-season games of the player's as-of team visible at the as-of; last = the most recent, avg3 = mean over the last 3 (fewer early in the season), season = mean over all; a game he missed counts as 0; NULL only when the team has no visible game)
- **Source:** twm.modules.waiver_radar.features; fact_play
- **Waiver Radar reason:** "Had {value:.1f} chances per game inside the opponent's 10-yard line over the last {weeks} games"

### Grass field

Kicking footing differs a little between grass and turf.

- **Name:** `surface_grass`; **unit:** boolean (0/1); **used by:** decisions
- **Formula:** 1 for grass or dessograss, 0 for artificial turf; unknown -> NULL, filled with the training median
- **Source:** fact_game.surface; twm.modules.decisions.fieldgoal

### Half

Which half the play is in. Halftime resets timeouts and gives one team the ball.

- **Name:** `half_number`; **unit:** 1, 2 or 3; **used by:** decisions
- **Formula:** 1 = first half, 2 = second half, 3 = overtime (from fact_play.game_half)
- **Source:** fact_play.game_half; twm.modules.decisions.wp_data

### Head-coach departure

A new head coach usually means a new offense and new roles.

- **Name:** `hc_departure`; **unit:** boolean; **used by:** board
- **Formula:** his S team (his last regular-season game's) has a departure with last_season S in data/manual/coach_departures.csv announced before the snapshot's date (a blank date counts for fired/mutual/interim types only)
- **Source:** twm.modules.board.features

### Home game next week

Teams tend to score more and allow less at home.

- **Name:** `next_is_home`; **unit:** boolean; **used by:** streamer
- **Formula:** the as-of team is the home team of its week N+1 game and the venue is not neutral (fact_schedule.location, public from slot_available_at); NULL on a bye or while the venue is not public
- **Source:** twm.modules.streamer.features; fact_schedule.home_team, fact_schedule.location
- **Streamer reason:** "Plays at home next week"

### Indoors

No wind or cold indoors.

- **Name:** `roof_closed`; **unit:** boolean (0/1); **used by:** decisions
- **Formula:** 1 when fact_game.roof is dome or closed; 0 for outdoors, open or unknown
- **Source:** fact_game.roof; twm.modules.decisions.fieldgoal

### K/DST games so far

How many games the numbers so far are based on.

- **Name:** `kdst_games_to_date`; **unit:** games; **used by:** streamer
- **Formula:** the pool's games_to_date (K: games with a kick attempt; DST: team games)
- **Source:** twm.modules.streamer.features; twm.modules.streamer.pool

### K/DST points per game

How many fantasy points the kicker or defense has scored per game so far.

- **Name:** `kdst_points_per_game`; **unit:** points per game; **used by:** streamer
- **Formula:** the pool's ppg_to_date: kicking: / defense: points (config/scoring.yaml) per regular-season game visible at the as-of (K: games with a kick attempt; DST: the team's games)
- **Source:** twm.modules.streamer.features; fact_kicker_week, fact_defense_week (twm.scoring_kdst)
- **Streamer reason:** "Has scored {value:.1f} fantasy points per game this season"

### K/DST points, latest game

What it scored last time out.

- **Name:** `kdst_points_last`; **unit:** points; **used by:** streamer
- **Formula:** fantasy points in the entity's most recent visible regular-season game of the season (K: his latest game with a kick attempt, any team)
- **Source:** twm.modules.streamer.features; fact_kicker_week, fact_defense_week
- **Streamer reason:** "Scored {value:.1f} fantasy points in the last game"

### K/DST points-per-game rank

Where its points per game rank at its position so far.

- **Name:** `kdst_ppg_rank`; **unit:** rank (1 = best); **used by:** streamer
- **Formula:** the pool's ppg_pos_rank: rank of kdst_points_per_game among the position's universe with a game (ties share the better rank)
- **Source:** twm.modules.streamer.features; twm.modules.streamer.pool
- **Streamer reason:** "Ranks #{value:.0f} at {pos} in fantasy points per game this season"

### K/DST preseason rank

Where experts (or last season's scoring) placed it before the season.

- **Name:** `kdst_preseason_rank`; **unit:** rank (1 = best); **used by:** streamer
- **Formula:** the pool's preseason_pos_rank: 2020 on the rank on FantasyPros' last August/September K or DST cheat sheet before week 1; earlier the rank by last season's points per game
- **Source:** twm.modules.streamer.features; fact_ranking_kdst.pos_rank; twm.modules.streamer.pool
- **Streamer reason:** "Was ranked #{value:.0f} at {pos} before the season"

### Lead in standard deviations of what is left

A random-walk view of the game: how many 'typical swings of the remaining time' the offense is ahead by, counting the ball and the spread.

- **Name:** `z_margin`; **unit:** z-score; **used by:** decisions
- **Formula:** (score_differential + drive_value + posteam_spread x t) / (13.5 x sqrt(t) + 1), t = share of regulation left
- **Source:** fact_play.score_differential, fact_game.spread_line, fact_play.game_seconds_remaining; twm.modules.decisions.wp_data

### Lead x time played

The same lead is worth more late in the game; this number grows as time runs out.

- **Name:** `diff_time_ratio`; **unit:** points; **used by:** decisions
- **Formula:** score_differential / exp(-4 x elapsed share of regulation; overtime = 1)
- **Source:** fact_play.score_differential, fact_play.game_seconds_remaining; twm.modules.decisions.wp_data

### Long field-goal attempts per game

Long kicks score more fantasy points than short ones in the default scoring.

- **Name:** `k_fg_att_40_plus_per_game`; **unit:** attempts per game; **used by:** streamer
- **Formula:** field goals made or missed from 40+ yards / games, over the kicker's regular-season games of the season with a field-goal or extra-point attempt visible at the as-of, for any team (NULL for DST rows and for a kicker without one) (blocked kicks have no distance bucket)
- **Source:** twm.modules.streamer.features; fact_kicker_week.fg_made_40_49, fact_kicker_week.fg_missed_40_49, fact_kicker_week.fg_made_50_59, fact_kicker_week.fg_made_60_
- **Streamer reason:** "Tries {value:.1f} field goals of 40+ yards per game (long kicks score more)"

### Market-expected wins to date

How many games the betting market expected the team to have won by now.

- **Name:** `expected_wins`; **unit:** wins; **used by:** hot_seat
- **Formula:** sum over the games to date of the team's pregame win probability: de-vigged closing moneylines p = (1/o_team) / (1/o_team + 1/o_opp) on decimal odds (American +150 -> 2.5, -200 -> 1.5); a game without both moneylines uses 1 / (1 + exp(-k x spread_line)) (home side; k fit by maximum likelihood on every played game of the seasons before, ties out); NULL if a game has neither
- **Source:** twm.modules.hot_seat.features

### New competition

A rookie or an arrival shares his role.

- **Name:** `new_competitor_s1`; **unit:** boolean; **used by:** board
- **Formula:** a same-position teammate at his depth rank or ahead on that chart was on none of that team's regular-season weekly rosters of S
- **Source:** twm.modules.board.preseason

### New head coach (week-1 team)

A new head coach on the team he will play for.

- **Name:** `hc_change_s1`; **unit:** boolean; **used by:** board
- **Formula:** his week-1 chart team has a departure with last_season S in coach_departures.csv announced before the as-of's date (blank dates: the hc_departure rule)
- **Source:** twm.modules.board.preseason

### New starting QB

A new quarterback changes the targets.

- **Name:** `qb1_change_s1`; **unit:** boolean; **used by:** board
- **Formula:** the team's S primary starter (most regular-season pass attempts for it in S) is not among its best-ranked QBs on that chart
- **Source:** twm.modules.board.preseason

### New team

He moved in the offseason.

- **Name:** `team_change_s1`; **unit:** boolean; **used by:** board
- **Formula:** his week-1 chart team (the team where he has his best offense rank) differs from his S team (his last regular-season game's)
- **Source:** twm.modules.board.preseason

### New team this season

He changed teams during the season (trade or signing).

- **Name:** `joined_team_recently`; **unit:** boolean; **used by:** waiver_radar
- **Formula:** his latest visible roster team differs from his earliest roster team this season
- **Source:** twm.modules.waiver_radar.features; fact_roster_week
- **Waiver Radar reason:** "Joined {team} during the season"

### Next game under a fixed roof

No wind or rain indoors, so kicks are easier.

- **Name:** `next_venue_dome`; **unit:** boolean; **used by:** streamer
- **Formula:** the week N+1 stadium (fact_schedule.stadium_id, used once fact_schedule.stadium is public) has a roof value 'dome' in earlier games visible at the as-of and never open/closed; NULL without an earlier game there
- **Source:** twm.modules.streamer.features; fact_game.roof, fact_game.stadium_id
- **Streamer reason:** "Next week's game is indoors: no wind or rain"

### Next game under a retractable roof

A retractable roof is often closed in bad weather.

- **Name:** `next_venue_retractable`; **unit:** boolean; **used by:** streamer
- **Formula:** an earlier visible game at the week N+1 stadium has roof 'open' or 'closed' (the game-day state itself is never used: it is decided on game day); NULL without an earlier game there
- **Source:** twm.modules.streamer.features; fact_game.roof, fact_schedule.stadium_id
- **Streamer reason:** "Next week's stadium has a retractable roof"

### Next opponent: games so far

How many games the opponent numbers are based on.

- **Name:** `next_opp_games_to_date`; **unit:** games; **used by:** streamer
- **Formula:** regular-season games with a final score of the entity's as-of team's week N+1 opponent (fact_schedule; NULL on a bye or after the last regular-season week)
- **Source:** twm.modules.streamer.features; fact_game.result

### Next opponent: giveaways per game

An offense that throws interceptions and loses fumbles feeds a defense.

- **Name:** `next_opp_giveaways_per_game`; **unit:** turnovers per game; **used by:** streamer
- **Formula:** mean (def_interceptions + fumble_recovery_opp) of the defenses that faced the entity's as-of team's week N+1 opponent (fact_schedule; NULL on a bye or after the last regular-season week), per game = mean over the team's regular-season games of the season visible at the as-of (NULL before its first game)
- **Source:** twm.modules.streamer.features; fact_defense_week.def_interceptions, fact_defense_week.fumble_recovery_opp
- **Streamer reason:** "Next week's opponent turns the ball over {value:.1f} times per game"

### Next opponent: points allowed per game

A defense that gives up many points means more scoring chances for this team.

- **Name:** `next_opp_points_allowed_per_game`; **unit:** points per game; **used by:** streamer
- **Formula:** mean of the scores against the entity's as-of team's week N+1 opponent (fact_schedule; NULL on a bye or after the last regular-season week), per game = mean over the team's regular-season games of the season visible at the as-of (NULL before its first game)
- **Source:** twm.modules.streamer.features; fact_game.home_score, fact_game.away_score
- **Streamer reason:** "Next week's opponent allows {value:.1f} points per game"

### Next opponent: points per game

A weak offense next week is a good matchup for a team defense.

- **Name:** `next_opp_points_per_game`; **unit:** points per game; **used by:** streamer
- **Formula:** mean final score of the entity's as-of team's week N+1 opponent (fact_schedule; NULL on a bye or after the last regular-season week), per game = mean over the team's regular-season games of the season visible at the as-of (NULL before its first game)
- **Source:** twm.modules.streamer.features; fact_game.home_score, fact_game.away_score
- **Streamer reason:** "Next week's opponent scores {value:.1f} points per game"

### Next opponent: red-zone stall rate forced

A defense that holds teams to field goals in the red zone helps kickers.

- **Name:** `next_opp_rz_stall_rate_forced`; **unit:** share (0-1); **used by:** streamer
- **Formula:** stalls / trips of offenses facing the entity's as-of team's week N+1 opponent (fact_schedule; NULL on a bye or after the last regular-season week) (red-zone trip = a drive (fact_play.fixed_drive) with a snap at the opponent's 20 or closer (pass, run, field goal, kneel, spike or a penalty snap; two-point tries excluded); stall = a trip whose drive result is not 'Touchdown'); NULL without a trip
- **Source:** twm.modules.streamer.features; fact_play.fixed_drive_result, fact_play.defteam
- **Streamer reason:** "Next week's opponent keeps {value:.0%} of red-zone trips out of the end zone"

### Next opponent: red-zone trips allowed

How often offenses reach the red zone against next week's opponent.

- **Name:** `next_opp_rz_trips_allowed_per_game`; **unit:** trips per game; **used by:** streamer
- **Formula:** red-zone trips of offenses facing the entity's as-of team's week N+1 opponent (fact_schedule; NULL on a bye or after the last regular-season week) / its games with play-by-play (red-zone trip = a drive (fact_play.fixed_drive) with a snap at the opponent's 20 or closer (pass, run, field goal, kneel, spike or a penalty snap; two-point tries excluded); stall = a trip whose drive result is not 'Touchdown')
- **Source:** twm.modules.streamer.features; fact_play.yardline_100, fact_play.defteam
- **Streamer reason:** "Next week's opponent allows {value:.1f} red-zone trips per game"

### Next opponent: sacks allowed per game

An offense that gets sacked a lot gives a defense sack points.

- **Name:** `next_opp_sacks_allowed_per_game`; **unit:** sacks per game; **used by:** streamer
- **Formula:** mean def_sacks of the defenses that faced the entity's as-of team's week N+1 opponent (fact_schedule; NULL on a bye or after the last regular-season week), per game = mean over the team's regular-season games of the season visible at the as-of (NULL before its first game)
- **Source:** twm.modules.streamer.features; fact_defense_week.def_sacks, fact_defense_week.opponent_team
- **Streamer reason:** "Next week's opponent gives up {value:.1f} sacks per game"

### Next opponents' points allowed

Whether his next matchups are soft (above 1) or tough (below 1) for his position.

- **Name:** `opp_fp_allowed_next3`; **unit:** ratio (1 = average); **used by:** waiver_radar
- **Formula:** mean over his team's next 3 scheduled opponents (fact_schedule weeks after N, byes skipped; the listed cancelled games count until played) of: fantasy points the opponent's defense allowed per game to his position group this season (visible games; the scorer's snap-count position of that game, else his latest roster position) / the league average per team-game for the group; an opponent without a visible game counts 1.0; NULL when his team has no game left. No betting lines (spec 6.4)
- **Source:** twm.modules.waiver_radar.features; fact_schedule, fact_player_week, fact_snaps
- **Waiver Radar reason:** "Soft schedule: his next opponents have allowed {value:.2f} times the average fantasy points to {pos}s"

### No depth chart for his team yet

We cannot tell yet whether he made the team: a missing chart, not a cut.

- **Name:** `dc_team_chart_missing`; **unit:** boolean; **used by:** board
- **Formula:** no row of his on any visible week-1 chart of S+1 AND no visible chart of his S team at the preseason as-of (legacy charts are public the Wednesday before week 1; a team whose week-1 game moved has none)
- **Source:** twm.modules.board.preseason

### Not on a week-1 depth chart

Cut, unsigned, retired or hurt before the season starts.

- **Name:** `dc_absent`; **unit:** boolean; **used by:** board
- **Formula:** no row of his on any team's week-1 depth chart of S+1 visible at the preseason as-of (fact_depth_chart) while his S team's chart is visible; NULL when it is not (dc_team_chart_missing); the other preseason features are then NULL
- **Source:** twm.modules.board.preseason

### Offense at home

Home teams win a bit more often; neutral-site games (London, Super Bowl) have no home team.

- **Name:** `posteam_is_home`; **unit:** 1 / 0 / 0.5; **used by:** decisions
- **Formula:** 1 when the possession team is the home team, 0 when away, 0.5 at a neutral site (fact_game.location)
- **Source:** fact_game.location; twm.modules.decisions.wp_data

### Offense timeouts left

Timeouts stop the clock: a trailing team with timeouts has more time to come back.

- **Name:** `posteam_timeouts_remaining`; **unit:** 0-3; **used by:** decisions
- **Formula:** the possession team's timeouts left in the half, before the snap
- **Source:** fact_play.posteam_timeouts_remaining; twm.modules.decisions.wp_data

### Offensive EPA per play (neutral)

How efficient the offense is when the score does not force its hand.

- **Name:** `off_epa_neutral`; **unit:** points per play; **used by:** hot_seat
- **Formula:** sum(epa) / plays over the team's run and pass plays (no two-point tries) in neutral situations (fact_play.is_neutral), games to date; plays weighted equally
- **Source:** twm.modules.hot_seat.features

### Offensive EPA trend

Positive: the offense has been better lately than over the whole season.

- **Name:** `off_epa_neutral_trend`; **unit:** points per play; **used by:** hot_seat
- **Formula:** off_epa_neutral over the team's last hot_seat.trend_games (4) games minus the season to date; NULL until the team has played more than 4 games
- **Source:** twm.modules.hot_seat.features

### On the depth chart

Whether the team lists him at all.

- **Name:** `depth_listed`; **unit:** boolean; **used by:** waiver_radar
- **Formula:** his gsis_id is on his team's chart in force at the as-of in any slot (offense, defense or special teams); NULL when the team has no visible chart
- **Source:** twm.modules.waiver_radar.features; fact_depth_chart
- **Waiver Radar reason:** "Is listed on his team's depth chart"

### On the weekly expert list

Experts list only the kickers and defenses worth starting.

- **Name:** `weekly_ecr_listed`; **unit:** boolean; **used by:** streamer
- **Formula:** a weekly K or DST page of the season is visible and lists the entity; NULL when no page is visible (before late 2020)
- **Source:** twm.modules.streamer.features; fact_ranking_kdst.page_kind
- **Streamer reason:** "Was on the experts' weekly {pos} list"

### Opponent games observed

How much evidence the matchup number rests on (little early in the season).

- **Name:** `n_opp_games_seen`; **unit:** games; **used by:** waiver_radar
- **Formula:** sum of the visible games of the next opponents used by opp_fp_allowed_next3
- **Source:** twm.modules.waiver_radar.features; fact_game

### PPG change

A big jump is likely to give some back.

- **Name:** `ppg_change`; **unit:** points per game; **used by:** board
- **Formula:** ppg_s - PPG in S-1 (NULL without S-1)
- **Source:** twm.modules.board.features

### PPG rank at his position

Where his points per game rank at his position so far.

- **Name:** `ppg_pos_rank`; **unit:** rank (1 = best); **used by:** waiver_radar
- **Formula:** rank of ppg_to_date within his roster position among roster players with a game (ties share the better rank; the candidate pool's ppg_pos_rank)
- **Source:** twm.modules.waiver_radar.features; twm.modules.waiver_radar.pool
- **Waiver Radar reason:** "Ranks No. {value:.0f} among {pos}s in points per game this season"

### PPG rank at his position

Where he finished; also the prior-season-rank baseline's only input.

- **Name:** `pos_rank_s`; **unit:** rank; **used by:** board
- **Formula:** rank by ppg_s among his position's players with 8+ games in S (ties: more points, then id); NULL under 8 games
- **Source:** twm.modules.board.features

### Point differential per game

By how much the team outscores (or is outscored by) its opponents on average.

- **Name:** `point_diff_per_game`; **unit:** points per game; **used by:** hot_seat
- **Formula:** (points scored - points allowed) / games, regular season to date
- **Source:** twm.modules.hot_seat.features

### Points allowed per game (ESPN rule)

How many points the defense gives up; fewer points allowed earn more fantasy points.

- **Name:** `dst_points_allowed_per_game`; **unit:** points per game; **used by:** streamer
- **Formula:** mean points_allowed (the opponent's score minus 6 for each touchdown the team's own offense gave up on an interception or fumble return), per game = mean over the team's regular-season games of the season visible at the as-of (NULL before its first game); NULL for K rows
- **Source:** twm.modules.streamer.features; fact_defense_week.points_allowed
- **Streamer reason:** "Allows {value:.1f} points per game"

### Points over expected per game

Scoring beyond his opportunity, which tends not to last.

- **Name:** `fpoe_per_game_s`; **unit:** points per game; **used by:** board
- **Formula:** ppg_s - xfp_per_game_s
- **Source:** twm.modules.board.features

### Points per game

His scoring rate last season.

- **Name:** `ppg_s`; **unit:** points per game; **used by:** board
- **Formula:** regular-season fantasy points in S (config/scoring.yaml) / games_s
- **Source:** twm.modules.board.features

### Points per game this season

How much a player has scored per game so far this season. A player near the top has been picked up by now, so he is not in the candidate pool.

- **Name:** `ppg_to_date`; **unit:** points per game; **used by:** waiver_radar
- **Formula:** fantasy points (config/scoring.yaml) summed over the season's regular-season games public at the as-of / the number of those games (weeks with a stat line); ppg_pos_rank ranks it within the roster position (ties share the better rank)
- **Source:** fact_player_week (twm.scoring.score_sql) through twm.asof.AsOfView
- **Waiver Radar reason:** "Averages {value:.1f} fantasy points per game this season"

### Position

Which position he plays; hit rates differ by position. A category, not an identifier.

- **Name:** `position`; **unit:** category (QB, RB, WR, TE); **used by:** waiver_radar
- **Formula:** his point-in-time roster position (the pool's position)
- **Source:** twm.modules.waiver_radar.features; fact_roster_week.position

### Pregame spread (offense's view)

How many points the betting market expected the team with the ball to win by, set right before kickoff (allowed: it is known when the game starts).

- **Name:** `posteam_spread`; **unit:** points; **used by:** decisions
- **Formula:** closing spread_line if the possession team is home, else minus spread_line (spread_line > 0 = home favored): the margin the market expected for the offense
- **Source:** fact_game.spread_line; twm.modules.decisions.wp_data

### Preseason position rank

Where the player stood before the season: experts' consensus ranking (ECR) when it exists, otherwise last season's scoring. A player ranked high was drafted in almost every league.

- **Name:** `preseason_pos_rank`; **unit:** rank (1 = best); **used by:** waiver_radar
- **Formula:** 2020 on (method ecr): the player's rank among his position's players on FantasyPros' last August/September redraft cheat sheet of that position before the season's first game (fact_ranking.pos_rank, page_kind preseason); not on his roster position's sheet: his rank on his own FantasyPros position's sheet, judged against that position's cutoff. 2013-2019 (method prior_ppg): his rank by last season's regular-season PPG among players of his current roster position with at least 4 games; ties share the better rank
- **Source:** fact_ranking.pos_rank; fact_player_week for last season's PPG
- **Waiver Radar reason:** "Was ranked No. {value:.0f} among {pos}s before the season"

### Previous season playoff result

How far the team went in last season's playoffs.

- **Name:** `prev_playoff_round`; **unit:** round (0-5); **used by:** hot_seat
- **Formula:** last season: 0 no playoffs, 1 lost wild card, 2 lost divisional, 3 lost conference, 4 lost Super Bowl, 5 won it (text in prev_playoff_result)
- **Source:** twm.modules.hot_seat.features

### Previous season wins

How the team did last year.

- **Name:** `prev_season_wins`; **unit:** wins; **used by:** hot_seat
- **Formula:** the team's regular-season wins last season (ties 0.5), whoever coached; NULL for a team without games last season
- **Source:** twm.modules.hot_seat.features

### Prior seasons

Experience; the Cliff needs 3+, the Breakout 0 or 1.

- **Name:** `prior_seasons`; **unit:** seasons; **used by:** board
- **Formula:** S - fact_roster_week.entry_year (= years_exp): his seasons in the league before S
- **Source:** twm.modules.board.features

### Pythagorean wins

The wins a team 'deserves' from its points scored and allowed.

- **Name:** `pythagorean_wins`; **unit:** wins; **used by:** hot_seat
- **Formula:** games x PF^e / (PF^e + PA^e), e = hot_seat.pythagorean_exponent (2.37, the NFL exponent Football Outsiders uses); NULL when PF + PA = 0
- **Source:** twm.modules.hot_seat.features

### Pythagorean wins minus wins

Positive: the team has been unlucky in close games; negative: lucky.

- **Name:** `pythag_minus_wins`; **unit:** wins; **used by:** hot_seat
- **Formula:** pythagorean_wins - reg_wins
- **Source:** twm.modules.hot_seat.features

### RYOE trend

Negative: his running is losing its edge.

- **Name:** `ngs_ryoe_trend`; **unit:** yards; **used by:** board
- **Formula:** ngs_ryoe_per_att_s - the S-1 value
- **Source:** twm.modules.board.features

### Ranked before the season

Whether any preseason list ranked him at all.

- **Name:** `preseason_ranked`; **unit:** boolean; **used by:** waiver_radar
- **Formula:** preseason_pos_rank is not NULL (FantasyPros ECR 2020 on; last season's PPG rank or a round 1-2 rookie before)
- **Source:** twm.modules.waiver_radar.features; twm.modules.waiver_radar.pool
- **Waiver Radar reason:** "Was on a preseason ranking list"

### Red-zone carries per game

Carries inside the opponent's 20-yard line.

- **Name:** `rz_carries_avg3`; **unit:** carries per game; **used by:** waiver_radar
- **Formula:** mean over the team's last 3 games of his runs (play_type run, not two-point tries; kneels excluded) with yardline_100 <= 20 (team games = regular-season games of the player's as-of team visible at the as-of; last = the most recent, avg3 = mean over the last 3 (fewer early in the season), season = mean over all; a game he missed counts as 0; NULL only when the team has no visible game)
- **Source:** twm.modules.waiver_radar.features; fact_play.rusher_player_id, yardline_100
- **Waiver Radar reason:** "Got {value:.1f} carries per game inside the opponent's 20-yard line over the last {weeks} games"

### Red-zone stall rate

The share of red-zone trips that end without a touchdown.

- **Name:** `team_rz_stall_rate`; **unit:** share (0-1); **used by:** streamer
- **Formula:** stalls / trips of the as-of team this season (red-zone trip = a drive (fact_play.fixed_drive) with a snap at the opponent's 20 or closer (pass, run, field goal, kneel, spike or a penalty snap; two-point tries excluded); stall = a trip whose drive result is not 'Touchdown'); NULL without a trip
- **Source:** twm.modules.streamer.features; fact_play.fixed_drive_result
- **Streamer reason:** "{team} scores no touchdown on {value:.0%} of its red-zone trips"

### Red-zone stalls per game

Drives that reach the red zone but stall usually end in a short field goal: kicker points.

- **Name:** `team_rz_stalls_per_game`; **unit:** stalls per game; **used by:** streamer
- **Formula:** the as-of team's red-zone trips that did not end in a touchdown / its games with play-by-play (red-zone trip = a drive (fact_play.fixed_drive) with a snap at the opponent's 20 or closer (pass, run, field goal, kneel, spike or a penalty snap; two-point tries excluded); stall = a trip whose drive result is not 'Touchdown')
- **Source:** twm.modules.streamer.features; fact_play.fixed_drive_result
- **Streamer reason:** "{team}'s red-zone drives stall {value:.1f} times per game (short field-goal chances)"

### Red-zone targets per game

Targets inside the opponent's 20-yard line, where touchdowns come from.

- **Name:** `rz_targets_avg3`; **unit:** targets per game; **used by:** waiver_radar
- **Formula:** mean over the team's last 3 games of his targets on pass plays (not two-point tries) with yardline_100 <= 20 (team games = regular-season games of the player's as-of team visible at the as-of; last = the most recent, avg3 = mean over the last 3 (fewer early in the season), season = mean over all; a game he missed counts as 0; NULL only when the team has no visible game)
- **Source:** twm.modules.waiver_radar.features; fact_play.receiver_player_id, yardline_100
- **Waiver Radar reason:** "Drew {value:.1f} targets per game inside the opponent's 20-yard line over the last {weeks} games"

### Red-zone trips per game

How often the offense gets inside the opponent's 20-yard line.

- **Name:** `team_rz_trips_per_game`; **unit:** trips per game; **used by:** streamer
- **Formula:** the as-of team's red-zone trips / its games with play-by-play (red-zone trip = a drive (fact_play.fixed_drive) with a snap at the opponent's 20 or closer (pass, run, field goal, kneel, spike or a penalty snap; two-point tries excluded); stall = a trip whose drive result is not 'Touchdown')
- **Source:** twm.modules.streamer.features; fact_play.yardline_100, fact_play.fixed_drive
- **Streamer reason:** "{team} reaches the red zone {value:.1f} times per game"

### Rookie

First-year players often earn more snaps as the season goes on.

- **Name:** `is_rookie`; **unit:** boolean; **used by:** waiver_radar
- **Formula:** his latest visible roster row's entry_year equals the season
- **Source:** twm.modules.waiver_radar.features; fact_roster_week.entry_year
- **Waiver Radar reason:** "Is a rookie: first-year players often earn more snaps as the season goes on"

### Rookie first-round QB

The team drafted its quarterback of the future: owners tend to be patient.

- **Name:** `rookie_r1_qb_on_roster`; **unit:** boolean; **used by:** hot_seat
- **Formula:** a QB (fact_roster_week.position) on the team's latest visible weekly roster of the season (status not CUT/RET/UFA/TRD) was a first-round pick of this year's draft (dim_player draft_round = 1, draft_year = season); NULL without a visible roster
- **Source:** twm.modules.hot_seat.features

### Rookie first-round QB

A new quarterback may start.

- **Name:** `draft_qb_r1_pd`; **unit:** boolean; **used by:** board
- **Formula:** his S team drafted a combine-listed QB in round 1 of the S+1 draft
- **Source:** twm.modules.board.post_draft

### Rookie target share

An early role is the strongest sign of a coming breakout.

- **Name:** `target_share_rookie`; **unit:** share (0-1); **used by:** board
- **Formula:** target share in his rookie season (entry year; S itself for a first-year player)
- **Source:** twm.modules.board.features

### Routes proxy

About how many pass plays he was on the field for: a stand-in for routes run, which nflverse does not publish.

- **Name:** `routes_proxy_avg3`; **unit:** dropbacks per game; **used by:** waiver_radar
- **Formula:** mean over the team's last 3 games of (team dropbacks in the game x his snap share in it); dropbacks = plays with qb_dropback = 1 that are not two-point tries (passes, sacks, scrambles) (team games = regular-season games of the player's as-of team visible at the as-of; last = the most recent, avg3 = mean over the last 3 (fewer early in the season), season = mean over all; a game he missed counts as 0; NULL only when the team has no visible game)
- **Source:** twm.modules.waiver_radar.features; fact_play.qb_dropback, fact_snaps.offense_pct
- **Waiver Radar reason:** "Was on the field for about {value:.0f} pass plays per game over the last {weeks} games"

### Running back

Position flag (QB is the case with every flag off). A category, not an identifier.

- **Name:** `pos_rb`; **unit:** boolean; **used by:** board
- **Formula:** his point-in-time position in S is RB
- **Source:** twm.modules.board.features

### Rush share

His share of the running game.

- **Name:** `rush_share_s`; **unit:** share (0-1); **used by:** board
- **Formula:** his carries / his team's carries in his games of S
- **Source:** twm.modules.board.features

### Rush yards over expected per carry

Yards gained beyond what the blocking and defenders' positions predicted.

- **Name:** `ngs_ryoe_per_att_s`; **unit:** yards; **used by:** board
- **Formula:** sum(rush_yards_over_expected) / sum(rush_attempts) over his weekly NGS rushing rows of S (games with 10+ carries; 2018 on: upstream has no RYOE for 2016-2017)
- **Source:** twm.modules.board.features

### Sacks per game

How often the defense sacks the quarterback; every sack scores for a team defense.

- **Name:** `dst_sacks_per_game`; **unit:** sacks per game; **used by:** streamer
- **Formula:** mean def_sacks (half sacks count 0.5), per game = mean over the team's regular-season games of the season visible at the as-of (NULL before its first game); NULL for K rows
- **Source:** twm.modules.streamer.features; fact_defense_week.def_sacks
- **Streamer reason:** "Gets {value:.1f} sacks per game"

### Score difference (offense minus defense)

How far ahead (positive) or behind (negative) the team with the ball is.

- **Name:** `score_differential`; **unit:** points; **used by:** decisions
- **Formula:** possession team's score minus the defense's, before the snap
- **Source:** fact_play.score_differential; twm.modules.decisions.wp_data

### Second-year coach

The coach is in his second season with this team.

- **Name:** `is_second_year_coach`; **unit:** boolean; **used by:** hot_seat
- **Formula:** tenure_seasons = 2
- **Source:** twm.modules.hot_seat.features

### Seconds left in the game

How much time is left. A 7-point lead means little early and a lot late.

- **Name:** `game_seconds_remaining`; **unit:** seconds; **used by:** decisions
- **Formula:** seconds left in regulation (in overtime: in the overtime period), before the snap
- **Source:** fact_play.game_seconds_remaining; twm.modules.decisions.wp_data

### Seconds left in the half

Time left before halftime or the end of the game; drives the two-minute drill.

- **Name:** `half_seconds_remaining`; **unit:** seconds; **used by:** decisions
- **Formula:** seconds left in the current half (or overtime period), before the snap
- **Source:** fact_play.half_seconds_remaining; twm.modules.decisions.wp_data

### Separation (Next Gen Stats)

How open he gets at the catch point; falls as receivers lose speed.

- **Name:** `ngs_separation_s`; **unit:** yards; **used by:** board
- **Formula:** target-weighted mean of avg_separation over his weekly NGS receiving rows of S (games with 5+ targets; 2016 on)
- **Source:** twm.modules.board.features

### Separation trend

Negative: he is getting less open than a year before.

- **Name:** `ngs_separation_trend`; **unit:** yards; **used by:** board
- **Formula:** ngs_separation_s - the S-1 value
- **Source:** twm.modules.board.features

### Snap share

How often a player was on the field when his team had the ball. Coaches reveal their plans through snaps before the box score does.

- **Name:** `offense_snap_share`; **unit:** share (0-1); **used by:** waiver_radar, regression_watch
- **Formula:** fact_snaps.offense_pct: the player's offensive snaps divided by his team's offensive snaps in that game (Pro Football Reference, rounded to 0.01)
- **Source:** fact_snaps.offense_pct (joined by fact_snaps.gsis_id)
- **Waiver Radar reason:** "{player}'s snap share went from {prev:.0%} to {value:.0%}"

### Snap share

How much of the time he is on the field.

- **Name:** `snap_pct_s`; **unit:** share (0-1); **used by:** board
- **Formula:** mean fact_snaps.offense_pct over his regular-season games with an offensive snap in S (2013 on; NULL before)
- **Source:** twm.modules.board.features

### Snap share change

Did his playing time just go up or down? A big rise means the coaches trust him more.

- **Name:** `snap_share_delta`; **unit:** share points; **used by:** waiver_radar
- **Formula:** snap_share_last minus the mean snap share of the up to 3 team games before the last (NULL when the last game is the team's first)
- **Source:** twm.modules.waiver_radar.features; fact_snaps.offense_pct
- **Waiver Radar reason:** "Snap share rose from {prev:.0%} to {value:.0%} in his last game"

### Snap share, last 3 games

His usual playing time lately, less noisy than one game.

- **Name:** `snap_share_avg3`; **unit:** share (0-1); **used by:** waiver_radar
- **Formula:** mean offense_snap_share over the team's last 3 games (team games = regular-season games of the player's as-of team visible at the as-of; last = the most recent, avg3 = mean over the last 3 (fewer early in the season), season = mean over all; a game he missed counts as 0; NULL only when the team has no visible game)
- **Source:** twm.modules.waiver_radar.features; fact_snaps.offense_pct
- **Waiver Radar reason:** "Played {value:.0%} of his team's snaps over the last {weeks} games"

### Snap share, last game

How much he played in the most recent game. A jump is often the first sign of a bigger role.

- **Name:** `snap_share_last`; **unit:** share (0-1); **used by:** waiver_radar
- **Formula:** offense_snap_share in the team's last game (team games = regular-season games of the player's as-of team visible at the as-of; last = the most recent, avg3 = mean over the last 3 (fewer early in the season), season = mean over all; a game he missed counts as 0; NULL only when the team has no visible game)
- **Source:** twm.modules.waiver_radar.features; fact_snaps.offense_pct
- **Waiver Radar reason:** "Played {value:.0%} of his team's snaps last game"

### Snap share, season

His playing time over the whole season so far.

- **Name:** `snap_share_season`; **unit:** share (0-1); **used by:** waiver_radar
- **Formula:** mean offense_snap_share over all team games of the season (team games = regular-season games of the player's as-of team visible at the as-of; last = the most recent, avg3 = mean over the last 3 (fewer early in the season), season = mean over all; a game he missed counts as 0; NULL only when the team has no visible game)
- **Source:** twm.modules.waiver_radar.features; fact_snaps.offense_pct
- **Waiver Radar reason:** "Has played {value:.0%} of his team's snaps this season"

### Snap-share trend

Negative: his role is shrinking.

- **Name:** `snap_pct_trend`; **unit:** share (0-1); **used by:** board
- **Formula:** snap_pct_s - the S-1 value
- **Source:** twm.modules.board.features

### Speed score

Speed adjusted for size.

- **Name:** `combine_speed_score`; **unit:** index (100 = average RB); **used by:** board
- **Formula:** weight x 200 / forty^4 (Bill Barnwell's Speed Score, Football Outsiders 2008)
- **Source:** twm.modules.board.features

### Spread x time left

The pregame expectation fades as the game goes on; this lets the model weigh it less and less.

- **Name:** `spread_time`; **unit:** points; **used by:** decisions
- **Formula:** posteam_spread x exp(-4 x elapsed share of regulation; overtime = 1)
- **Source:** fact_game.spread_line, fact_play.game_seconds_remaining; twm.modules.decisions.wp_data

### Starting-QB changes

How many different quarterbacks beyond the first have started this season.

- **Name:** `starting_qb_changes`; **unit:** changes; **used by:** hot_seat
- **Formula:** distinct starting QBs (fact_game home/away_qb_id) in the games to date minus 1
- **Source:** twm.modules.hot_seat.features

### Takeaways per game

Interceptions and recovered fumbles: both score for a team defense.

- **Name:** `dst_takeaways_per_game`; **unit:** takeaways per game; **used by:** streamer
- **Formula:** mean (def_interceptions + fumble_recovery_opp), per game = mean over the team's regular-season games of the season visible at the as-of (NULL before its first game); NULL for K rows
- **Source:** twm.modules.streamer.features; fact_defense_week.def_interceptions, fact_defense_week.fumble_recovery_opp
- **Streamer reason:** "Forces {value:.1f} takeaways (interceptions and fumbles) per game"

### Target share

The share of his team's passes thrown to a player. Targets are the raw material of receiving points.

- **Name:** `target_share`; **unit:** share (0-1); **used by:** waiver_radar, regression_watch, teammate_out
- **Formula:** player targets / team targets in that game (nflverse player_stats)
- **Source:** fact_player_week.target_share
- **Verified:** equals targets / (sum of targets of the player's team that week) on 358,395 of 358,434 player-weeks 2006-2026
- **Waiver Radar reason:** "{player} drew {value:.0%} of his team's targets"

### Target share

His share of the passing game.

- **Name:** `target_share_s`; **unit:** share (0-1); **used by:** board
- **Formula:** his targets / his team's targets in his games of S
- **Source:** twm.modules.board.features

### Target share, last 3 games

How much of the passing game goes to him lately.

- **Name:** `target_share_avg3`; **unit:** share (0-1); **used by:** waiver_radar
- **Formula:** mean nflverse target_share over the team's last 3 games (team games = regular-season games of the player's as-of team visible at the as-of; last = the most recent, avg3 = mean over the last 3 (fewer early in the season), season = mean over all; a game he missed counts as 0; NULL only when the team has no visible game)
- **Source:** twm.modules.waiver_radar.features; fact_player_week.target_share
- **Waiver Radar reason:** "Drew {value:.0%} of his team's targets over the last {weeks} games"

### Target share, last game

The share of his team's passes thrown his way last game.

- **Name:** `target_share_last`; **unit:** share (0-1); **used by:** waiver_radar
- **Formula:** nflverse target_share in the team's last game, 0 without a stat line (team games = regular-season games of the player's as-of team visible at the as-of; last = the most recent, avg3 = mean over the last 3 (fewer early in the season), season = mean over all; a game he missed counts as 0; NULL only when the team has no visible game)
- **Source:** twm.modules.waiver_radar.features; fact_player_week.target_share
- **Waiver Radar reason:** "Drew {value:.0%} of his team's targets last game"

### Team ANY/A (QB quality)

How good his team's passing game was.

- **Name:** `team_any_a`; **unit:** yards per attempt; **used by:** board
- **Formula:** his S team's (passing yards + 20 x TD - 45 x INT - sack yards) / (attempts + sacks) in the regular season of S (Pro Football Reference's ANY/A; no model column)
- **Source:** twm.modules.board.features

### Team EPA per play \*

How efficient his offense is: good offenses create more points to go around.

- **Name:** `team_epa_per_play`; **unit:** points per play; **used by:** waiver_radar
- **Formula:** mean epa over his team's run and pass plays (no two-point tries) in the season's visible games
- **Source:** twm.modules.waiver_radar.features; fact_play.epa
- **Waiver Radar reason:** "His offense ({team}) has been efficient this season: {value:+.2f} expected points added per play"

### Team EPA per play (neutral) \*

Offensive efficiency when the game is close, the fairest view of a team.

- **Name:** `team_epa_per_play_neutral`; **unit:** points per play; **used by:** waiver_radar
- **Formula:** as team_epa_per_play, neutral situations only (fact_play.is_neutral)
- **Source:** twm.modules.waiver_radar.features; fact_play.epa, is_neutral
- **Waiver Radar reason:** "His offense ({team}) has been efficient when the game is close: {value:+.2f} expected points added per play"

### Team games so far

How many games the team numbers are based on.

- **Name:** `team_games_to_date`; **unit:** games; **used by:** streamer
- **Formula:** the as-of team's regular-season games of the season with a final score visible at the as-of
- **Source:** twm.modules.streamer.features; fact_game.result

### Team neutral pass rate

How pass-heavy his team is when the score does not force it.

- **Name:** `team_pass_rate_neutral`; **unit:** share (0-1); **used by:** waiver_radar
- **Formula:** share of his team's neutral-situation run and pass plays with pass = 1 (passes, sacks, scrambles)
- **Source:** twm.modules.waiver_radar.features; fact_play.pass, is_neutral
- **Waiver Radar reason:** "His team ({team}) passes on {value:.0%} of its plays when the game is close"

### Team plays per game

Pace: more plays mean more chances for everybody.

- **Name:** `team_plays_per_game`; **unit:** plays per game; **used by:** waiver_radar
- **Formula:** his team's run and pass plays (no two-point tries) / its visible games
- **Source:** twm.modules.waiver_radar.features; fact_play
- **Waiver Radar reason:** "His team ({team}) runs {value:.0f} plays per game: more plays, more chances"

### Team points per game

How much the team's offense scores: more scoring drives mean more kicks.

- **Name:** `team_points_per_game`; **unit:** points per game; **used by:** streamer
- **Formula:** mean of the entity's as-of team's final scores, per game = mean over the team's regular-season games of the season visible at the as-of (NULL before its first game)
- **Source:** twm.modules.streamer.features; fact_game.home_score, fact_game.away_score
- **Streamer reason:** "{team} scores {value:.1f} points per game"

### Team's kicker

Whether he is the kicker his team is using now; practice-squad and camp kickers are in the pool but rarely kick.

- **Name:** `is_team_kicker`; **unit:** boolean; **used by:** streamer
- **Formula:** the kicker has a kick attempt in his as-of team's latest visible regular-season game with one (the pool's is_team_kicker; NULL for DST rows)
- **Source:** twm.modules.streamer.features; fact_kicker_week.team; twm.modules.streamer.pool
- **Streamer reason:** "Is {team}'s kicker now: he kicked in its latest game"

### Teammates out at his position

How many players at his position are out.

- **Name:** `teammate_same_pos_unavailable`; **unit:** players; **used by:** waiver_radar
- **Formula:** number of unavailable teammates of his position group
- **Source:** twm.modules.waiver_radar.features; as vacated_target_share
- **Waiver Radar reason:** "{value:.0f} {pos} teammate(s) are out: {teammate}"

### Temperature

Cold air and a hard ball make long kicks shorter.

- **Name:** `temp_f`; **unit:** degrees F; **used by:** decisions
- **Formula:** fact_game.temp, else the 'Temp: N' in fact_play.weather; indoors = 70; still unknown outdoors -> NULL, filled with the median of the training rows' outdoor games
- **Source:** fact_game.temp, fact_play.weather; twm.modules.decisions.fieldgoal

### Tenure starts before the data

The coach was already in charge when our data begins; his tenure is a minimum.

- **Name:** `tenure_censored`; **unit:** boolean; **used by:** hot_seat
- **Formula:** the current stint reaches the warehouse's first season (1999), so the true tenure may be longer than tenure_seasons
- **Source:** twm.modules.hot_seat.features

### Tight end

Position flag.

- **Name:** `pos_te`; **unit:** boolean; **used by:** board
- **Formula:** his point-in-time position in S is TE
- **Source:** twm.modules.board.features

### Took over mid-season

The coach replaced someone during this season (usually an interim coach).

- **Name:** `took_over_mid_season`; **unit:** boolean; **used by:** hot_seat
- **Formula:** the team's first played regular-season game this season had another coach
- **Source:** twm.modules.hot_seat.features

### Touches

Workload; very heavy loads (350+ for a running back) often precede a decline.

- **Name:** `touches_s`; **unit:** touches; **used by:** board
- **Formula:** carries + receptions in S
- **Source:** twm.modules.board.features

### Touches per game

Workload per game.

- **Name:** `touches_per_game_s`; **unit:** touches per game; **used by:** board
- **Formula:** touches_s / games_s
- **Source:** twm.modules.board.features

### Undrafted

He was not drafted.

- **Name:** `is_undrafted`; **unit:** boolean; **used by:** waiver_radar
- **Formula:** no draft round in dim_player at the as-of
- **Source:** twm.modules.waiver_radar.features; dim_player.draft_round
- **Waiver Radar reason:** "Went undrafted" (when no: "Was drafted: teams give drafted players more chances")

### Undrafted

Not drafted.

- **Name:** `undrafted`; **unit:** boolean; **used by:** board
- **Formula:** no draft round in dim_player
- **Source:** twm.modules.board.features

### Unplaced early pick

The draft counts may miss a pick at his position.

- **Name:** `draft_unplaced_pd`; **unit:** boolean; **used by:** board
- **Formula:** his S team made a round 1-3 pick of that draft with no combine row (position unknown point-in-time)
- **Source:** twm.modules.board.post_draft

### Vacated carries

Carries left behind by departed teammates.

- **Name:** `vacated_carries_share_s1`; **unit:** share (0-1); **used by:** board
- **Formula:** the same with carries
- **Source:** twm.modules.board.preseason

### Vacated carry share

Carries freed up by teammates who are out.

- **Name:** `vacated_carry_share`; **unit:** share (sum); **used by:** waiver_radar
- **Formula:** as vacated_target_share, with carry_share
- **Source:** twm.modules.waiver_radar.features; fact_player_week, fact_team_week, fact_roster_week
- **Waiver Radar reason:** "{teammate} {why}: {value:.0%} of the team's carries are up for grabs"

### Vacated carry share at his position

Carries freed up by players at his own position.

- **Name:** `same_pos_vacated_carry_share`; **unit:** share (sum); **used by:** waiver_radar
- **Formula:** vacated_carry_share over unavailable teammates of his own position group only
- **Source:** twm.modules.waiver_radar.features; as vacated_carry_share
- **Waiver Radar reason:** "{teammate} ({pos}) {why}: {value:.0%} of the team's carries are up for grabs at his position"

### Vacated target share

Targets that teammates who are now out used to get: somebody has to catch them.

- **Name:** `vacated_target_share`; **unit:** share (sum); **used by:** waiver_radar
- **Formula:** sum over his unavailable teammates (see teammate_unavailable) of their target_share averaged over the team's last 3 games up to their last appearance (before they became unavailable; games they missed count as 0)
- **Source:** twm.modules.waiver_radar.features; fact_player_week, fact_roster_week, fact_injury_report, fact_snaps
- **Waiver Radar reason:** "{teammate} {why}: {value:.0%} of the team's targets are up for grabs"

### Vacated target share at his position

Targets freed up by players at his own position: the most direct path to more work.

- **Name:** `same_pos_vacated_target_share`; **unit:** share (sum); **used by:** waiver_radar
- **Formula:** vacated_target_share over unavailable teammates of his own position group only
- **Source:** twm.modules.waiver_radar.features; as vacated_target_share
- **Waiver Radar reason:** "{teammate} ({pos}) {why}: {value:.0%} of the team's targets are up for grabs at his position"

### Vacated targets

Targets left behind by departed teammates.

- **Name:** `vacated_targets_share_s1`; **unit:** share (0-1); **used by:** board
- **Formula:** the team's S regular-season targets by players absent from its week-1 chart / the team's S targets
- **Source:** twm.modules.board.preseason

### Value of the ball before the half ends

What having the ball here is worth before halftime (or the end), the other team's later possessions included: about 4 points at the opponent's 20 early in a half, about 2 at midfield, 0 when the half ends.

- **Name:** `drive_value`; **unit:** points; **used by:** decisions
- **Formula:** half_value(yardline_100, half_seconds_remaining): the offense's points minus the defense's from a 1st down at this spot and clock until halftime, a fixed table measured on the 1999-2005 first halves (wp_data.HALF_VALUE_TABLE), read linearly in yards and log seconds; 0 when the half is over
- **Source:** fact_play.yardline_100, fact_play.half_seconds_remaining; twm.modules.decisions.wp_data

### Vertical jump

Explosiveness.

- **Name:** `combine_vertical`; **unit:** inches; **used by:** board
- **Formula:** fact_combine.vertical
- **Source:** twm.modules.board.features

### WOPR (weighted opportunity rating)

One number that blends how often a player is targeted with how deep those targets are; a good summary of a receiver's opportunity.

- **Name:** `wopr`; **unit:** index (about 0-1); **used by:** waiver_radar, regression_watch
- **Formula:** 1.5 x target_share + 0.7 x air_yards_share
- **Source:** fact_player_week.wopr
- **Verified:** equals the formula on every player-week 2009-2026; nflverse's values for 1999-2008 do not (air-yards data is incomplete before 2006)

### WOPR, last 3 games

One number for a receiver's recent opportunity.

- **Name:** `wopr_avg3`; **unit:** index (about 0-1); **used by:** waiver_radar
- **Formula:** mean nflverse wopr (1.5 x target share + 0.7 x air-yards share) over the team's last 3 games (team games = regular-season games of the player's as-of team visible at the as-of; last = the most recent, avg3 = mean over the last 3 (fewer early in the season), season = mean over all; a game he missed counts as 0; NULL only when the team has no visible game)
- **Source:** twm.modules.waiver_radar.features; fact_player_week.wopr
- **Waiver Radar reason:** "Receiving workload (WOPR: targets and air yards combined) of {value:.2f} over the last {weeks} games"

### Weather unknown

Flags the games whose weather was filled in, so the model can treat them apart.

- **Name:** `weather_missing`; **unit:** boolean (0/1); **used by:** decisions
- **Formula:** 1 for an outdoor game whose temperature or wind is unknown even after the weather text (then imputed)
- **Source:** fact_game.temp/wind, fact_play.weather; twm.modules.decisions.fieldgoal

### Week-1 depth rank

Starter or backup going in.

- **Name:** `depth_rank_s1`; **unit:** rank (1 = starter); **used by:** board
- **Formula:** his best depth_rank among his offense slots of his position (RB: RB/HB) on that chart; a daily pull's rank is renumbered within its slot
- **Source:** twm.modules.board.preseason

### Weekly expert rank

Where experts ranked it last week.

- **Name:** `weekly_ecr_rank`; **unit:** rank (1 = best); **used by:** streamer
- **Formula:** the entity's pos_rank on FantasyPros' latest weekly K or DST ranking of the season visible at the as-of (the Friday before week N's games; late 2020 on); K by gsis_id, DST by team; NULL when not listed or no page
- **Source:** twm.modules.streamer.features; fact_ranking_kdst.pos_rank
- **Streamer reason:** "Experts ranked {player} #{value:.0f} at {pos} last week"

### Wide receiver

Position flag.

- **Name:** `pos_wr`; **unit:** boolean; **used by:** board
- **Formula:** his point-in-time position in S is WR
- **Source:** twm.modules.board.features

### Wind

Wind pushes long kicks off line.

- **Name:** `wind_mph`; **unit:** mph; **used by:** decisions
- **Formula:** fact_game.wind, else the 'Wind: ... N mph' in fact_play.weather ('calm' = 0); indoors = 0; still unknown outdoors -> NULL, filled with the training median
- **Source:** fact_game.wind, fact_play.weather; twm.modules.decisions.fieldgoal

### Wins to date

The team's wins so far, ties counting as half a win.

- **Name:** `reg_wins`; **unit:** wins; **used by:** hot_seat
- **Formula:** regular-season wins to date, a tie = 0.5
- **Source:** twm.modules.hot_seat.features

### Wins vs market expectation

Positive: the team has won more than the market expected; negative: fewer.

- **Name:** `wins_vs_expected`; **unit:** wins; **used by:** hot_seat
- **Formula:** reg_wins - expected_wins
- **Source:** twm.modules.hot_seat.features

### Yards per team pass attempt

Production per team dropback: rewards both role and efficiency.

- **Name:** `yards_per_team_pass_att_s`; **unit:** yards; **used by:** board
- **Formula:** his receiving yards / his team's pass attempts in his games of S
- **Source:** twm.modules.board.features

### Yards per touch

Efficiency with the ball.

- **Name:** `yards_per_touch_s`; **unit:** yards; **used by:** board
- **Formula:** (rushing + receiving yards) / touches in S; NULL under 20 touches
- **Source:** twm.modules.board.features

### Yards to go

The distance the offense still needs; 3rd and 1 is far better than 3rd and 12.

- **Name:** `ydstogo`; **unit:** yards; **used by:** decisions
- **Formula:** yards needed for a first down (or a touchdown)
- **Source:** fact_play.ydstogo; twm.modules.decisions.wp_data

### Yards to the end zone

Field position: 1 = at the opponent's goal line, 99 = backed up at your own 1.

- **Name:** `yardline_100`; **unit:** yards (1-99); **used by:** decisions
- **Formula:** yards between the line of scrimmage and the opponent's goal line
- **Source:** fact_play.yardline_100; twm.modules.decisions.wp_data

### Yards-per-touch trend

Negative: he is getting less out of each touch than before.

- **Name:** `yards_per_touch_trend`; **unit:** yards; **used by:** board
- **Formula:** yards_per_touch_s - the mean of the S-1 and S-2 values that exist
- **Source:** twm.modules.board.features

### Years of experience

Seasons in the league before this one.

- **Name:** `years_exp`; **unit:** seasons; **used by:** waiver_radar
- **Formula:** his latest visible roster row's years_exp
- **Source:** twm.modules.waiver_radar.features; fact_roster_week.years_exp
- **Waiver Radar reason:** "Has {value:.0f} seasons of NFL experience"

### xFP (expected fantasy points) \*

What an average player would have scored from the same chances (where he was targeted, where he carried the ball). Opportunity is sticky week to week.

- **Name:** `xfp`; **unit:** points; **used by:** regression_watch, waiver_radar
- **Formula:** sum over stats of (expected stat x points per unit in config/scoring.yaml): the ffopportunity model's expected passing, rushing and receiving yards, touchdowns, two-point conversions, interceptions and receptions of a player-game (fact_opportunity_week *_exp columns); fumbles and return or fumble-recovery touchdowns have no expected value and add 0. Regression Watch's lists, stability study and backtest (since 2026-10-01, step H6-b2) and the player pages' weekly xFP (since 2026-10-02, step PXFP) score the same per-play expectations from the own walk-forward models instead (own_xfp); the Waiver Radar's features keep ffopportunity's
- **Source:** twm.scoring.xfp / xfp_sql on fact_opportunity_week
- **Verified:** with nflverse-PPR weights it reproduces ffopportunity's total_fantasy_points_exp within the rounding of its 2-decimal columns on every row except rushing two-point tries, whose expected yards ffopportunity adds to the points but not to rush_yards_gained_exp (docs/waiver_radar.md)

### xFP, last 3 games \*

What his recent chances were worth per game, whatever he did with them.

- **Name:** `xfp_avg3`; **unit:** points per game; **used by:** waiver_radar
- **Formula:** mean xfp over the team's last 3 games (team games = regular-season games of the player's as-of team visible at the as-of; last = the most recent, avg3 = mean over the last 3 (fewer early in the season), season = mean over all; a game he missed counts as 0; NULL only when the team has no visible game)
- **Source:** twm.modules.waiver_radar.features; fact_opportunity_week
- **Waiver Radar reason:** "His targets and carries were worth {value:.1f} fantasy points per game over the last {weeks} games"

### xFP, last game \*

Expected fantasy points from his chances last game.

- **Name:** `xfp_last`; **unit:** points; **used by:** waiver_radar
- **Formula:** xfp in the team's last game, 0 without a row (team games = regular-season games of the player's as-of team visible at the as-of; last = the most recent, avg3 = mean over the last 3 (fewer early in the season), season = mean over all; a game he missed counts as 0; NULL only when the team has no visible game)
- **Source:** twm.modules.waiver_radar.features; fact_opportunity_week
- **Waiver Radar reason:** "His targets and carries last game were worth {value:.1f} fantasy points for an average player"

## Labels (what models predict)

### Best weekly rank in the window

His best week at his position in the window. A label detail: never a model feature.

- **Name:** `best_rank`; **unit:** rank; **used by:** waiver_radar
- **Formula:** min(weekly_pos_rank) over the window weeks (NULL if never ranked)
- **Source:** twm.modules.waiver_radar.labels.label_rows

### Breakout

A young player who becomes a fantasy starter.

- **Name:** `y_breakout`; **unit:** boolean; **used by:** board
- **Formula:** top-24 WR / top-12 TE / top-24 RB in PPG with 8+ games in S+1 (Breakout population: WR/TE/RB with S - entry year 0 or 1, a game in S, not top-36 WR / top-12 TE / top-24 RB in S)
- **Source:** twm.modules.board.populations

### Cliff

A veteran whose scoring falls by 30% or more.

- **Name:** `y_cliff`; **unit:** boolean; **used by:** board
- **Formula:** PPG in S+1 <= 70% of PPG in S with 6+ games in S+1 (Cliff population: 3+ prior seasons, top-36 PPG at his position with 8+ games in S); NULL when y_missed
- **Source:** twm.modules.board.populations

### Cliff or missed

The sensitivity run: missing most of the next season counts as a cliff.

- **Name:** `y_cliff_or_missed`; **unit:** boolean; **used by:** board
- **Formula:** y_cliff OR y_missed
- **Source:** twm.modules.board.populations

### FG label: the kick was good

Whether the field goal went through.

- **Name:** `fg_made`; **unit:** boolean; **used by:** decisions
- **Formula:** fact_play.field_goal_result = 'made' (missed and blocked = 0)
- **Source:** twm.modules.decisions.fieldgoal

### FLEX-worthy finishes in the window

Informative: how often he was worth a FLEX start. A label detail: never a model feature.

- **Name:** `n_flex_finishes`; **unit:** weeks; **used by:** waiver_radar
- **Formula:** window weeks with is_flex_finish (RB/WR only)
- **Source:** twm.modules.waiver_radar.labels.label_rows

### Games in the label window

How many games the player's team has in the window. A label detail: never a model feature.

- **Name:** `window_games`; **unit:** games; **used by:** waiver_radar
- **Formula:** len(window_weeks): 3, fewer in the last weeks of a season
- **Source:** twm.modules.waiver_radar.labels.label_rows

### Go label: the try converted

Whether going for it worked: the offense kept the ball with a new set of downs.

- **Name:** `converted`; **unit:** boolean; **used by:** decisions
- **Formula:** third or fourth down pass/run: first_down = 1 or the offense's touchdown, and no interception or lost fumble (defensive penalties that give a first down count)
- **Source:** twm.modules.decisions.conversion

### Label status

A pending label (the current season) is unknown, never guessed. A label detail: never a model feature.

- **Name:** `label_status`; **unit:** 'final' or 'pending'; **used by:** waiver_radar
- **Formula:** 'pending' until every game of every window week has a final score (fact_game.result) and stat lines in the cache, else 'final'
- **Source:** twm.modules.waiver_radar.labels.label_rows

### Label window weeks

The weeks whose results decide the label. A label detail: never a model feature.

- **Name:** `window_weeks`; **unit:** list of week numbers; **used by:** waiver_radar
- **Formula:** the first 3 regular-season weeks after the as-of week N in which the player's as-of team has a game (its bye weeks skipped), up to the last regular-season week (dim_week.is_last_reg_week)
- **Source:** twm.modules.waiver_radar.labels.label_rows (fact_game)

### Missed next season

Injury, benching, release or retirement: kept apart from the Cliff (owner, 2026-10-02).

- **Name:** `y_missed`; **unit:** boolean; **used by:** board
- **Formula:** fewer than 6 games in S+1 (Cliff population)
- **Source:** twm.modules.board.populations

### Rest-of-season PPG (actual)

What he really scored per game afterwards: what the projection is graded on.

- **Name:** `rest_of_season_ppg`; **unit:** points per game; **used by:** regression_watch
- **Formula:** fantasy points per game over his regular-season games public after the as-of (graded only with at least 3 such games)
- **Source:** twm.modules.regression_watch.projection

### Short label window

The window was cut short by the end of the regular season. A label detail: never a model feature.

- **Name:** `is_short_window`; **unit:** boolean; **used by:** waiver_radar
- **Formula:** window_games < 3
- **Source:** twm.modules.waiver_radar.labels.label_rows

### Starter finishes in the window

The count behind y_hit and y_sustained. A label detail: never a model feature.

- **Name:** `n_starter_finishes`; **unit:** weeks; **used by:** waiver_radar
- **Formula:** window weeks with is_starter_finish
- **Source:** twm.modules.waiver_radar.labels.label_rows

### Streamer label: a starter next week

Would the kicker or defense you pick up on Tuesday have been worth starting in the very next game? Streaming is a one-week decision.

- **Name:** `y_start`; **unit:** boolean; **used by:** streamer
- **Formula:** the entity's week N+1 fantasy points (config/scoring.yaml kicking: / defense:) rank in the top (teams x lineup slots) at its position among everyone who played that week: top 12 K and top 12 DST in this league (ties at the cutoff all count); NULL on a bye, after the last regular-season week, or while week N+1 is not final; a kicker without a kick in week N+1 is False
- **Source:** twm.modules.streamer.labels (fact_kicker_week, fact_defense_week)

### Sustained hit

A stricter target: a player who stays startable, not a one-week spike.

- **Name:** `y_sustained`; **unit:** boolean (NULL while pending); **used by:** waiver_radar
- **Formula:** n_starter_finishes >= 2: as y_hit, but in at least two window weeks
- **Source:** twm.modules.waiver_radar.labels.label_rows

### Usable for training

Whether a model may learn from this row. A label detail: never a model feature.

- **Name:** `train_eligible`; **unit:** boolean; **used by:** waiver_radar
- **Formula:** window_games >= 2 (PROJECT_SPEC 8.1: weeks with fewer remaining games are left out of training)
- **Source:** twm.modules.waiver_radar.labels.label_rows

### WP label: the team with the ball won

What the win-probability model learns to predict, play by play.

- **Name:** `posteam_wins`; **unit:** boolean; **used by:** decisions
- **Formula:** 1 when the possession team before the snap won the game (fact_game.result from its side > 0), 0 when it lost; tie games are left out
- **Source:** fact_game.result; twm.modules.decisions.wp_data

### Waiver hit

What the Waiver Radar predicts: will this player be startable soon?

- **Name:** `y_hit`; **unit:** boolean (NULL while pending); **used by:** waiver_radar
- **Formula:** n_starter_finishes >= 1: at least one starter finish in the label window (the next 3 regular-season weeks after the as-of week N in which his as-of team plays; a bye is skipped and extends the window, the season's last regular-season week ends it), counting only stat lines public strictly after the as-of
- **Source:** twm.modules.waiver_radar.labels.label_rows

### Weekly points in the window

Week-by-week scores, for reports and eyeballing. A label detail: never a model feature.

- **Name:** `window_points`; **unit:** list of points; **used by:** waiver_radar
- **Formula:** fantasy points of each window week (0 for a week he played without a stat line, NULL for a week he did not play)
- **Source:** twm.modules.waiver_radar.labels.label_rows

### Weekly ranks in the window

Week-by-week ranks, for reports and eyeballing. A label detail: never a model feature.

- **Name:** `window_ranks`; **unit:** list of ranks; **used by:** waiver_radar
- **Formula:** weekly_pos_rank of each window week (NULL where he had no stat line)
- **Source:** twm.modules.waiver_radar.labels.label_rows

### Window weeks played

How many window weeks the player actually took the field on offense. A label detail: never a model feature.

- **Name:** `n_window_played`; **unit:** weeks; **used by:** waiver_radar
- **Formula:** window weeks with fact_snaps.offense_snaps > 0 or a stat line, for any team
- **Source:** twm.modules.waiver_radar.labels.label_rows

## Concepts

### As-of time

The moment a prediction is made. It may only use data that was public by then; the time machine replays any past as-of exactly.

- **Name:** `as_of`; **unit:** UTC timestamp; **used by:** shared
- **Formula:** dim_week.asof_weekly_utc: the first Tuesday 14:00 UTC after the Eastern date of the week's first kickoff (after Monday night for a normal week)
- **Source:** dim_week, twm.asof

### Available at

When a row of data became public. A prediction at an as-of time sees only rows with available_at at or before it.

- **Name:** `available_at`; **unit:** UTC timestamp; **used by:** shared
- **Formula:** per table rule in twm.warehouse.available (e.g. game data: estimated game end + 6 h); later when unsure
- **Source:** every event table's available_at column

### Calibration

Whether probabilities mean what they say: among all players given a similar probability, the share who really hit should be close to that probability.

- **Name:** `calibration`; **unit:** predicted against observed rate; **used by:** shared
- **Formula:** the predictions sorted by probability and cut into equal-count groups (10 by default); each group's mean probability is compared with the share of its rows whose outcome happened (perfect calibration: equal)
- **Source:** twm.backtest.metrics.calibration_bins

### Clear call

A decision where one option's win probability beat the next best by more than the toss-up margin (Methodology page). Only clear calls are graded: a wrong one counts against the coach.

- **Name:** `clear_call`; **unit:** decision; **used by:** decisions
- **Formula:** WP(best option) - WP(second best) > decisions.toss_up_margin (config/settings.yaml), every option priced by our win-probability model of that season
- **Source:** twm.modules.decisions.grade

### Clock case

A game where one of the three clock-management metrics applies: timeouts unused in a lost one-score game, a passive end of the first half, or seconds wasted late while trailing with timeouts in hand. Each has an exact written definition; situations outside them are never graded.

- **Name:** `clock_case`; **unit:** game; **used by:** decisions
- **Formula:** a game where timeouts_unused, half_passivity or timeout_seconds_wasted applies, by its written definition (decisions.clock in config/settings.yaml)
- **Source:** twm.modules.decisions.clock

### Doubtful

The team says the player is unlikely to play. Since 2016 almost none of the Doubtful QBs, RBs, WRs and TEs took an offensive snap (about 1 in 100).

- **Name:** `doubtful`; **unit:** injury tag; **used by:** questionable
- **Formula:** fact_injury_report.report_status = 'Doubtful' on the team's final injury report of the week
- **Source:** fact_injury_report.report_status (nflverse injuries)

### Drivers

The three inputs that move this coach's estimate the most, up or down, compared with an average coach: the logistic regression's own terms (its weight times how far the value is from average).

- **Name:** `hot_seat_driver`; **unit:** log-odds term; **used by:** hot_seat
- **Formula:** the logistic regression's term of each input: coefficient x standardized value (a value's term and its missing-indicator term summed); the 3 largest by absolute size, signed. A row's terms sum to its log-odds minus the intercept
- **Source:** twm.modules.hot_seat.production

### FLEX

A week's running back, wide receiver and tight end lists merged into one and ordered by chance. Each chance is the chance of a starter finish at the player's own position, so FLEX compares three slightly different targets: it is a way to browse the three lists together, not a separate model.

- **Name:** `flex`; **unit:** list; **used by:** waiver_radar
- **Formula:** the week's RB, WR and TE picks of one kind merged: highest chance first, then the higher model probability, the better rank in his own list, RB/WR/TE and the id; each player once
- **Source:** web/lib/flex.ts (mergeFlex)

### Fantasy playoffs

The last weeks of the fantasy season, where a loss ends your year: the weeks a manager plans the roster for.

- **Name:** `fantasy_playoffs`; **unit:** weeks; **used by:** playoff_planner
- **Formula:** NFL weeks 15-17 by default (most leagues); My League reads the owner's league settings: the weeks after the regular season, one round per playoff matchup period
- **Source:** twm.modules.playoff_planner.league, config of the synced league

### Interim coach (row flag)

Interim coaches are left out of training (spec 8.5). Uses the owner's hindsight file, so it selects rows and is never a model feature.

- **Name:** `is_interim`; **unit:** boolean; **used by:** hot_seat
- **Formula:** took_over_mid_season OR the owner's data/manual/coach_departures.csv says the coach-team-season was interim (departure_type interim_not_retained or interim_suspected true)
- **Source:** twm.modules.hot_seat.features

### Interval

The range a number would plausibly move within if the same kind of seasons were played again. It comes from a season-block bootstrap: whole seasons are redrawn at random many times and the number is recomputed each time.

- **Name:** `interval`; **unit:** range (low to high); **used by:** shared
- **Formula:** season-block bootstrap: draw as many test seasons as there are, with replacement, recompute the number on the drawn seasons, 2,000 times with a fixed seed, and keep the middle 95%; a difference between two methods is paired (both are graded on the same drawn seasons)
- **Source:** twm.backtest.metrics (block_indices, N_BOOT, LEVEL)

### Interval (stability study)

The range the number would plausibly move within with other players: the player-seasons are redrawn at random many times and the number is recomputed each time. The same player appears in several seasons, so the true range is a little wider.

- **Name:** `stability_interval`; **unit:** range (low to high); **used by:** regression_watch
- **Formula:** bootstrap over player-seasons: redraw them with replacement 1,000 times, recompute the split-half correlation each time and keep the middle 95%
- **Source:** twm.modules.regression_watch.stability (bootstrap_corr, N_BOOT)

### Kneel-out seconds K(d, t)

The most clock an offense can burn by kneeling from this down; if it is at least the time left, the game is over unless the defense's timeouts cut it.

- **Name:** `kneel_out_seconds`; **unit:** seconds; **used by:** decisions
- **Formula:** n x p + max(0, n - 1 - t) x (g - p), n = 5 - down kneels, t = the defense's timeouts; p (kneel_play) and g (kneel_cycle) = median seconds from a kneel to the next snap with / without a defensive timeout between, measured on the 5 seasons before S
- **Source:** twm.modules.decisions.clock

### Let go

Fired during the season, fired after it, or a mutual parting, announced in the window. Other departures (retired, resigned, left for another job) are not counted as let go.

- **Name:** `hot_seat_let_go`; **unit:** yes/no; **used by:** hot_seat
- **Formula:** departure type fired_in_season, fired_after_season or mutual_parting, announced in the window (the owner-verified departures file); retired, resigned (also under pressure) or left for another job: not let go
- **Source:** the owner-verified departures file (data/manual); twm.modules.hot_seat.targets

### Listed position

The position nflverse lists the player at today, also on his past weekly rows. A Radar list ranks each player at the position of his team's roster that week, which can differ for a player who changed position.

- **Name:** `listed_position`; **unit:** position; **used by:** waiver_radar
- **Formula:** nflverse's current position of the player (today's snapshot, also shown on his past rows); a Radar list ranks him at his team's roster position that week
- **Source:** dim_player.position

### Live or reconstructed

A live list was made in real time on the Tuesday and is never changed afterwards. A reconstructed (backtest) list was made later from the data as it stood on that Tuesday: what the Radar would have said then, not a list anyone saw at the time.

- **Name:** `list_kind`; **unit:** live / backtest; **used by:** shared
- **Formula:** live: scored on the real clock after its as-of and before the next kickoff, stored once and never rescored (append-only); backtest (reconstructed): scored later from the data public at the as-of, through the same point-in-time view
- **Source:** kind column of the predictions store and of the published lists

### Live or reconstructed board

A live board is made before the season's first kickoff from the data public then, and never changed afterwards. A reconstructed board (backtest) was scored later, from the data as it stood on the eve of week 1: what the model would have said then, not a board anyone saw at the time.

- **Name:** `board_kind`; **unit:** live / backtest; **used by:** board
- **Formula:** live: scored on the real clock between the board's as-of (the eve of week 1) and the first kickoff, stored once (append-only); backtest: scored later by the pinned models from the input rows as they stood at the as-of
- **Source:** twm.modules.board.live; twm.publish.board_lists

### Own walk-forward xFP

Regression Watch's expected points since 2026-10-01 and the player pages' since 2026-10-02: the same idea as nflverse's ffopportunity, but each season is valued by models that never saw it or any later season, so a backtest cannot borrow from the future.

- **Name:** `own_xfp`; **unit:** points; **used by:** regression_watch
- **Formula:** xfp from the project's own per-play models (catch chance, yards after the catch, pass touchdown and interception chances per target; yards and touchdown chance per carry; two-point success rates), LightGBM or a spline GLM per part, for a play of season S trained only on seasons 2006 to S-1; inputs are the situation before the snap (air yards, field position, down and distance, direction, quarter, score), never an nflfastR model column; scored with D1's per-play rules and config/scoring.yaml. The current season's models are pinned with their sha256 (config/production_models.yaml) and only loaded by the weekly job; the player pages' earlier seasons are frozen in the same pin (player_xfp)
- **Source:** twm.modules.regression_watch.own_xfp

### PPR (points per reception)

A scoring format that gives a point for every catch, on top of yards and touchdowns, which makes pass-catchers more valuable.

- **Name:** `ppr`; **unit:** points per catch; **used by:** shared
- **Formula:** config/scoring.yaml receiving.receptions (1 = full PPR, 0.5 = half, 0 = standard)

### Player-season

One player's regular season. The stability study counts each season he played enough games in once, at the position of most of his games, and splits his games into two halves.

- **Name:** `player_season`; **unit:** player x season; **used by:** regression_watch
- **Formula:** a QB, RB, WR or TE's regular season (2009 on) with at least 8 games with an opportunity (a target, carry or pass), at the position of most of his games; his games split odd/even and first/second half
- **Source:** twm.modules.regression_watch.stability (MIN_GAMES, halves)

### Practice status

How much he practiced late in the week. Shown for context only: the 2025+ injury data no longer matches earlier seasons here, so the chance does not use it.

- **Name:** `practice_status`; **unit:** full / limited / did not practice / none; **used by:** questionable
- **Formula:** fact_injury_report.practice_status of the week's final report: Full Participation, Limited Participation, Did Not Participate (blank: none)
- **Source:** fact_injury_report.practice_status

### Previous playoff result (text)

Last season's playoff exit, spelled out.

- **Name:** `prev_playoff_result`; **unit:** text; **used by:** hot_seat
- **Formula:** prev_playoff_round as text: none, lost_wc, lost_div, lost_conf, lost_sb, won_sb
- **Source:** twm.modules.hot_seat.features

### Pseudo-games (shrinkage)

A few games say little about a defense, so its rating starts at average and moves away only as real games pile up.

- **Name:** `pseudo_games`; **unit:** games; **used by:** playoff_planner
- **Formula:** k imaginary games at exactly average added to a team's record: rating = (allowed + k x average) / ((games + k) x average); k = within-team over between-team variance, estimated on 2006-2012
- **Source:** twm.modules.playoff_planner (frozen rule, docs/playoff_planner.md)

### Questionable

The team says the player is uncertain to play. Since 2016 about 6 in 10 Questionable QBs, RBs, WRs and TEs played; the list shows the chance for players like him.

- **Name:** `questionable`; **unit:** injury tag; **used by:** questionable
- **Formula:** fact_injury_report.report_status = 'Questionable' on the team's final injury report of the week (2016 on: the NFL's definition since dropping Probable)
- **Source:** fact_injury_report.report_status (nflverse injuries)

### Regression Watch universe

The fantasy-relevant players Regression Watch projects and tags each week.

- **Name:** `regression_universe`; **unit:** players; **used by:** regression_watch
- **Formula:** QB/RB/WR/TE with at least 3 games so far whose PPG or xFP/game ranks inside teams x (starting slots the position can fill, FLEX included) x 1.5: QB 18, RB 54, WR 54, TE 36
- **Source:** twm.modules.regression_watch.projection

### Regression to the mean

A player who scores far above his opportunity usually comes back down; one who scores far below usually rises. Opportunity is 'stickier' than efficiency.

- **Name:** `regression_to_the_mean`; **unit:** -; **used by:** regression_watch
- **Formula:** extreme results drift back toward the average when luck made them extreme

### Starter out

One of the team's main ball carriers or pass catchers misses the game, so his carries and targets go to someone else.

- **Name:** `starter_out`; **unit:** event (team game); **used by:** teammate_out
- **Formula:** a RB with a carry share of 45%+ or a WR/TE with a target share of 20%+ over the games he played among his team's previous 4 (at least 2) who takes no offensive snap while still on the team (traded, released and retired players do not count); live: Out or Doubtful on the week's injury report, or on a reserve list (IR, PUP ...)
- **Source:** fact_snaps, fact_player_week, fact_team_week, fact_roster_week, fact_injury_report

### Streaming pool

The kickers and team defenses who are probably still on waivers: those ranked low both by the experts before the season and by points per game so far. It is an estimate, the same kind as the Waiver Radar's candidate pool, because past waiver wires are not public.

- **Name:** `stream_pool`; **unit:** kickers and D/STs; **used by:** streamer
- **Formula:** the Waiver Radar's candidate-pool rule for K and D/ST: outside the pool cutoff both in the experts' preseason ranking (last season's points per game before 2020) and in points per game so far this season
- **Source:** twm.modules.streamer.pool

### Strength of schedule

How easy or hard a player's coming games look as a whole: above 1.00 is an easier run than average.

- **Name:** `strength_of_schedule`; **unit:** mean matchup rating; **used by:** playoff_planner
- **Formula:** the mean matchup rating of a player's opponents in weeks 15-17 (byes left out)
- **Source:** twm.modules.playoff_planner (frozen rule, docs/playoff_planner.md)

### Suggested priority

A suggestion from the chance: must-add, speculative or watch. The cutoffs, and how often each priority hit in the backtest, are on the Methodology page.

- **Name:** `priority`; **unit:** must-add / speculative / watch; **used by:** waiver_radar, streamer
- **Formula:** from the chance, for the top 25 of a list: must-add when it is at least 0.50, speculative from 0.25 up to that, watch below; ranks 26 and lower get none. The bins only go up, so each priority is a cutoff on the model probability
- **Source:** twm.modules.waiver_radar.confidence (MUST_ADD, SPECULATIVE, tier_table)

### Tag threshold X

How far the projection must sit from his current PPG before we tag him.

- **Name:** `tag_threshold_x`; **unit:** points per game; **used by:** regression_watch
- **Formula:** per season, the X in 0, 0.5 .. 6 with the best Sell-high (Buy-low) precision on the earlier seasons among those tagging at least 3 players per week; never chosen on the season it is used in
- **Source:** twm.modules.regression_watch.projection

### Team

Teams are shown by today's franchise code and name, also for past seasons: a franchise that moved appears under its current name.

- **Name:** `current_franchise`; **unit:** team code; **used by:** shared
- **Formula:** every team column holds today's franchise code (OAK -> LV, SD -> LAC, STL -> LA, LAR -> LA), with today's name
- **Source:** dim_team.current_abbr

### Teammate role

Where the teammate stands in line: with the WR1 out, the next receiver is 'WR2'.

- **Name:** `teammate_role`; **unit:** label (RB2, WR3, TE1 ...); **used by:** teammate_out
- **Formula:** position + usage rank (baseline carry share + target share, then snap share) among the teammates who play; the absent starters of his position count first; deeper than RB3 / WR4 / TE2 is '<pos>+'
- **Source:** twm.modules.teammate_out.history

### Toss-up

A decision whose best two options were within the toss-up margin of each other: the model cannot tell them apart with confidence, so it is counted but never graded, whatever the coach chose.

- **Name:** `toss_up`; **unit:** decision; **used by:** decisions
- **Formula:** WP(best option) - WP(second best) <= decisions.toss_up_margin: counted, never graded
- **Source:** twm.modules.decisions.grade

### Unavailable teammate

A teammate who is out now: his targets and carries are up for grabs. Each rule is recorded so the app can say why.

- **Name:** `teammate_unavailable`; **unit:** rule that fired; **used by:** waiver_radar
- **Formula:** a teammate (same as-of team, QB/RB/WR/TE, who played for the team this season) is unavailable at the as-of if ANY of: roster_status = his row on the team's latest visible weekly roster has a status other than ACT, INA, DEV (RES, PUP, SUS, CUT ...), used only in seasons whose roster statuses change from week to week (2016 on: the 2002-2015 rosters repeat one, season-final status on every week); left_team = he was on the team's roster earlier this season but not on its latest visible roster (released or traded); injury_report = the team's latest visible injury report of the season lists him Out or Doubtful; missed_last_game = no offensive snap or stat line in the team's last game after averaging at least 50% snap share in the up to 3 team games before it
- **Source:** twm.modules.waiver_radar.features (fact_roster_week, fact_injury_report, fact_snaps)

### WP points

Win probability in percentage points: one WP point is one percentage point of the team's chance to win, by our win-probability model.

- **Name:** `wp_points`; **unit:** percentage points (0-100); **used by:** decisions
- **Formula:** 100 x a win probability (or a difference of two) on the 0-1 scale
- **Source:** twm.modules.decisions.wp

### Waiver Radar candidate pool

The players who are probably still on waivers in a typical 12-team league. There is no record of which players sat on fantasy rosters before 2020, so anyone ranked high before the season or scoring well since counts as taken; the rest are the players the Waiver Radar ranks.

- **Name:** `candidate_pool`; **unit:** yes/no per player and as-of (in_pool); **used by:** waiver_radar
- **Formula:** on an NFL roster at the as-of (his latest public weekly roster row of the season, on his team's latest public roster, with status ACT, INA, DEV, at QB/RB/WR/TE) and outside the top N at his position by BOTH preseason_pos_rank and ppg_to_date; N = weekly starter threshold x candidate_pool_multiplier (1.5): QB 18, RB 36, WR 36, TE 18; in seasons without a preseason cheat sheet, rookies drafted in rounds 1-2 count as drafted
- **Source:** twm.modules.waiver_radar.pool.candidate_pool (fact_roster_week, fact_ranking, fact_player_week)

### Walk-forward backtest

Grading a model the honest way: every season is predicted by a model trained only on the seasons before it, using only data that was public at each Tuesday's as-of time.

- **Name:** `walk_forward`; **unit:** method; **used by:** shared
- **Formula:** for each test season S: fit only on seasons before S (settings chosen on the last season before S), score every as-of of S from the data public then, no refit during S; the harness refuses any training row of S or later
- **Source:** twm.backtest (TestSeasonInTrainingError)

### Weekly starter threshold

A player 'finished as a starter' in a week when he scored well enough that a typical 12-team league would have started him.

- **Name:** `starter_threshold`; **unit:** rank; **used by:** waiver_radar, shared
- **Formula:** teams x dedicated starters at the position (derived from config/league.yaml teams and lineup): QB top 12, RB top 24, WR top 24, TE top 12 by fantasy points that week; FLEX-worthy (teams x (dedicated starters + multi-position slots the position can fill), for the positions in flex_worthy_positions): RB/WR top 36
- **Source:** config/league.yaml; twm.modules.waiver_radar.labels.LabelRules

### Where we disagree

A player near the top of our list for a Cliff who is not among the same number of players the experts' ranking drops furthest below last season's finish, or the reverse. The record of past disagreements shows how both kinds turned out.

- **Name:** `board_disagree`; **unit:** marker; **used by:** board
- **Formula:** ours: the top N of the board by the Cliff chance; theirs: the N players with the largest experts' rank minus last season's position rank by points per game (not ranked first); marked when on one of the two only; nothing without the experts' ranks
- **Source:** web/lib/board.ts (disagreements, BOARD_DISAGREE_TOP)

### With or without garbage time

Points, expected points and points over expected either over every play, or only over the plays while the game was still in doubt. Stats piled up once a game is decided say little about next week. The projection itself is the same in both views.

- **Name:** `garbage_time_view`; **unit:** view; **used by:** regression_watch
- **Formula:** with garbage time: points, xFP and FPOE per game over every play; without: points_ng, xfp_ng and fpoe_ng per game (plays with is_garbage_time false only); the projection is computed once and shown in both
- **Source:** fact_play.is_garbage_time; twm.modules.regression_watch.player_week

### Wrong call

A clear call where the coach did not choose the option with the highest win probability.

- **Name:** `wrong_call`; **unit:** decision; **used by:** decisions
- **Formula:** a clear call whose chosen option is not the recommended one (the highest WP); it costs WP lost = WP(best) - WP(chosen)
- **Source:** twm.modules.decisions.grade

## Identifiers (never model features)

### Coach id

An identifier: used to join tables, never as a model feature (a model would memorize who, not learn why).

- **Name:** `coach_id`; **unit:** id; **used by:** shared
- **Formula:** slug of the head coach's name

### Defense team code

An identifier: used to join tables, never as a model feature (a model would memorize who, not learn why).

- **Name:** `defteam`; **unit:** id; **used by:** shared
- **Formula:** team on defense

### ESPN player id

An identifier: used to join tables, never as a model feature (a model would memorize who, not learn why).

- **Name:** `espn_id`; **unit:** id; **used by:** shared
- **Formula:** ESPN's player id

### Game id

An identifier: used to join tables, never as a model feature (a model would memorize who, not learn why).

- **Name:** `game_id`; **unit:** id; **used by:** shared
- **Formula:** season_week_away_home

### NFL player id

An identifier: used to join tables, never as a model feature (a model would memorize who, not learn why).

- **Name:** `gsis_id`; **unit:** id; **used by:** shared
- **Formula:** nflverse's canonical player key

### Offense team code

An identifier: used to join tables, never as a model feature (a model would memorize who, not learn why).

- **Name:** `posteam`; **unit:** id; **used by:** shared
- **Formula:** team with the ball

### Player id (player_stats)

An identifier: used to join tables, never as a model feature (a model would memorize who, not learn why).

- **Name:** `player_id`; **unit:** id; **used by:** shared
- **Formula:** the gsis_id in player_stats

### Pro Football Reference id

An identifier: used to join tables, never as a model feature (a model would memorize who, not learn why).

- **Name:** `pfr_player_id`; **unit:** id; **used by:** shared
- **Formula:** PFR player slug

### Team code

An identifier: used to join tables, never as a model feature (a model would memorize who, not learn why).

- **Name:** `team`; **unit:** id; **used by:** shared
- **Formula:** team abbreviation (today's code)
