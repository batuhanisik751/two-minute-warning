# Glossary

Generated from `src/twm/registry.py` by `uv run twm glossary --write`; do not edit by
hand. Planned entries are built in the step shown. \* = nflfastR/ffopportunity model
output (PROJECT_SPEC 6.3: trained on many seasons, a mild known leak in backtests).

## Metrics

### CPOE (completion percentage over expected) \*

Whether a quarterback completes more passes than an average passer would have on the same throws.

- **Name:** `cpoe`; **unit:** percentage points; **used by:** shared
- **Formula:** 1 if the pass was complete else 0, minus nflfastR's expected completion probability, x 100
- **Source:** fact_play.cpoe
- **Verified:** equals 100 x (complete_pass - cp) on all 52,174 passes with a CPOE in 2006, 2015 and 2025 (raw play-by-play)

### EPA (expected points added) \*

How much a single play helped or hurt the offense's scoring chances. A 30-yard catch on 3rd and 10 adds a lot; a sack on 1st down costs points.

- **Name:** `epa`; **unit:** points per play; **used by:** shared, decisions, hot_seat
- **Formula:** nflfastR's expected points after the play minus before it, for the offense
- **Source:** fact_play.epa

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

### FLEX-worthy finish

The running back or receiver scored well enough to fill a FLEX slot that week. Informative only; it is not a label.

- **Name:** `is_flex_finish`; **unit:** boolean; **used by:** waiver_radar
- **Formula:** a RB or WR with weekly_pos_rank <= flex_worthy_rank (36); always false for QB and TE
- **Source:** twm.modules.waiver_radar.labels.weekly_finishes

### Fantasy points

The score a player earns for your fantasy team in one game, computed from his yards, touchdowns, catches and mistakes with your league's settings.

- **Name:** `fantasy_points`; **unit:** points; **used by:** shared
- **Formula:** sum over stats of (stat value x points per unit in config/scoring.yaml); full PPR by default
- **Source:** twm.scoring.score / score_sql on fact_player_week
- **Verified:** the nflverse_ppr preset reproduces nflverse's fantasy_points_ppr exactly on all 150,691 QB/RB/WR/TE player-weeks 1999-2026 (tests/test_scoring.py)
- **Waiver Radar reason:** "{player} scored {value:.1f} fantasy points"

### Garbage time \*

Plays after the game is effectively decided. Stats piled up then say little about next week, so views can drop them.

- **Name:** `is_garbage_time`; **unit:** boolean; **used by:** shared, regression_watch
- **Formula:** win probability below 0.05 or above 0.95, except in the final 120 seconds of a half when the score is within 8 points
- **Source:** fact_play.is_garbage_time (twm.situations)

### Implied team total

How many points the betting market expected a team to score, from the spread and the over/under. Only known right before kickoff, so P1 uses it for games already played.

- **Name:** `implied_team_total`; **unit:** points; **used by:** shared
- **Formula:** home: (total_line + spread_line) / 2; away: (total_line - spread_line) / 2 (spread_line > 0 = home favored); closing lines of played games only
- **Source:** fact_game.home_implied_total, fact_game.away_implied_total
- **Verified:** spread_line sign checked in A3 (docs/assumptions.md section 7)

### Neutral situation \*

Plays where the game is still in the balance and neither team is forced to pass or run; the fairest view of how a team really plays.

- **Name:** `is_neutral`; **unit:** boolean; **used by:** shared
- **Formula:** win probability from 0.2 to 0.8 and more than 120 seconds left in the half
- **Source:** fact_play.is_neutral (twm.situations)

### Rostership (percent of leagues)

How many real leagues have the player on a roster. It checks the candidate pool (a player owned in fewer than 50% of leagues is really available); the pool itself never uses it, because it does not exist before 2020.

- **Name:** `owned_avg`; **unit:** percent (0-100); **used by:** waiver_radar
- **Formula:** FantasyPros' average share of leagues rostering the player across sites (fact_ranking.player_owned_avg) from the season's latest weekly ranking public at the as-of (the Friday before that week's games); owned_espn = ESPN's share (fact_ranking.player_owned_espn). 2020 partly, 2021 on
- **Source:** fact_ranking.player_owned_avg, fact_ranking.player_owned_espn

### Starter finish

The player scored like a weekly starter in a 12-team league that week.

- **Name:** `is_starter_finish`; **unit:** boolean; **used by:** waiver_radar
- **Formula:** weekly_pos_rank <= the position's starter threshold (QB top 12, RB top 24, WR top 24, TE top 12)
- **Source:** twm.modules.waiver_radar.labels.weekly_finishes

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

## Features

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

### Air-yards share

Air yards are how far the ball travels past the line of scrimmage before it is caught or falls. A big share means a player gets the deep, valuable looks.

- **Name:** `air_yards_share`; **unit:** share (0-1); **used by:** waiver_radar, regression_watch
- **Formula:** player's receiving air yards / his team's air yards on all pass attempts (completions, incompletions and interceptions) in that game
- **Source:** fact_player_week.air_yards_share
- **Verified:** exact on all 4,419 2025 player-weeks with air yards (denominator from fact_play)

### Air-yards share, last 3 games

How much of the team's downfield passing is aimed at him.

- **Name:** `air_yards_share_avg3`; **unit:** share (0-1); **used by:** waiver_radar
- **Formula:** mean nflverse air_yards_share over the team's last 3 games (team games = regular-season games of the player's as-of team visible at the as-of; last = the most recent, avg3 = mean over the last 3 (fewer early in the season), season = mean over all; a game he missed counts as 0; NULL only when the team has no visible game); can be slightly negative (passes behind the line)
- **Source:** twm.modules.waiver_radar.features; fact_player_week.air_yards_share
- **Waiver Radar reason:** "Got {value:.0%} of his team's air yards (throws down the field) over the last {weeks} games"

### Bye in the next 3 weeks

A bye costs a week of production.

- **Name:** `bye_in_next3`; **unit:** boolean; **used by:** waiver_radar
- **Formula:** his team has no scheduled game in at least one of the calendar weeks N+1 to N+3 (capped at the last regular-season week)
- **Source:** twm.modules.waiver_radar.features; fact_schedule, dim_week
- **Waiver Radar reason:** "Has a bye week in the next 3 weeks" (when no: "No bye week in the next 3 weeks")

### Carry share

The share of his team's running plays given to a player: the running-back version of target share.

- **Name:** `carry_share`; **unit:** share (0-1); **used by:** waiver_radar, regression_watch
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

### Draft round

Teams give early picks more chances.

- **Name:** `draft_round`; **unit:** round (1-7); **used by:** waiver_radar
- **Formula:** dim_player.draft_round; NULL when undrafted (see is_undrafted)
- **Source:** twm.modules.waiver_radar.features; dim_player.draft_round
- **Waiver Radar reason:** "Was drafted in round {value:.0f}: teams give higher draft picks more chances"

### FPOE (fantasy points over expected) \*

Points above or below what his chances were worth: partly skill, largely luck, and it tends to shrink toward zero.

- **Name:** `fpoe`; **unit:** points; **used by:** regression_watch, waiver_radar
- **Formula:** fantasy_points - xfp (the same player-game; a lost fumble or a return touchdown counts fully, having no expected value)
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

### Games played this season

How many games he has a stat line in so far.

- **Name:** `games_played_to_date`; **unit:** games; **used by:** waiver_radar
- **Formula:** regular-season games with a stat line visible at the as-of (the pool's games_to_date)
- **Source:** twm.modules.waiver_radar.features; twm.modules.waiver_radar.pool
- **Waiver Radar reason:** "Has played in {value:.0f} games this season"

### Games remaining

How much season is left to use him.

- **Name:** `team_games_remaining`; **unit:** games; **used by:** waiver_radar
- **Formula:** his team's regular-season fixtures after week N (fact_schedule, plus listed cancelled games)
- **Source:** twm.modules.waiver_radar.features; fact_schedule
- **Waiver Radar reason:** "His team has {value:.0f} games left this season"

### Goal-line opportunities per game

Chances inside the 10-yard line: the most valuable touches in fantasy.

- **Name:** `gl_opps_avg3`; **unit:** looks per game; **used by:** waiver_radar
- **Formula:** mean over the team's last 3 games of his targets plus carries (as above) with yardline_100 <= 10 (team games = regular-season games of the player's as-of team visible at the as-of; last = the most recent, avg3 = mean over the last 3 (fewer early in the season), season = mean over all; a game he missed counts as 0; NULL only when the team has no visible game)
- **Source:** twm.modules.waiver_radar.features; fact_play
- **Waiver Radar reason:** "Had {value:.1f} chances per game inside the opponent's 10-yard line over the last {weeks} games"

### New team this season

He changed teams during the season (trade or signing).

- **Name:** `joined_team_recently`; **unit:** boolean; **used by:** waiver_radar
- **Formula:** his latest visible roster team differs from his earliest roster team this season
- **Source:** twm.modules.waiver_radar.features; fact_roster_week
- **Waiver Radar reason:** "Joined {team} during the season"

### Next opponents' points allowed

Whether his next matchups are soft (above 1) or tough (below 1) for his position.

- **Name:** `opp_fp_allowed_next3`; **unit:** ratio (1 = average); **used by:** waiver_radar
- **Formula:** mean over his team's next 3 scheduled opponents (fact_schedule weeks after N, byes skipped; the listed cancelled games count until played) of: fantasy points the opponent's defense allowed per game to his position group this season (visible games; the scorer's snap-count position of that game, else his latest roster position) / the league average per team-game for the group; an opponent without a visible game counts 1.0; NULL when his team has no game left. No betting lines (spec 6.4)
- **Source:** twm.modules.waiver_radar.features; fact_schedule, fact_player_week, fact_snaps
- **Waiver Radar reason:** "Soft schedule: his next opponents have allowed {value:.2f} times the average fantasy points to {pos}s"

### On the depth chart

Whether the team lists him at all.

- **Name:** `depth_listed`; **unit:** boolean; **used by:** waiver_radar
- **Formula:** his gsis_id is on his team's chart in force at the as-of in any slot (offense, defense or special teams); NULL when the team has no visible chart
- **Source:** twm.modules.waiver_radar.features; fact_depth_chart
- **Waiver Radar reason:** "Is listed on his team's depth chart"

### Opponent games observed

How much evidence the matchup number rests on (little early in the season).

- **Name:** `n_opp_games_seen`; **unit:** games; **used by:** waiver_radar
- **Formula:** sum of the visible games of the next opponents used by opp_fp_allowed_next3
- **Source:** twm.modules.waiver_radar.features; fact_game

### PPG rank at his position

Where his points per game rank at his position so far.

- **Name:** `ppg_pos_rank`; **unit:** rank (1 = best); **used by:** waiver_radar
- **Formula:** rank of ppg_to_date within his roster position among roster players with a game (ties share the better rank; the candidate pool's ppg_pos_rank)
- **Source:** twm.modules.waiver_radar.features; twm.modules.waiver_radar.pool
- **Waiver Radar reason:** "Ranks No. {value:.0f} among {pos}s in points per game this season"

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

### Preseason position rank

Where the player stood before the season: experts' consensus ranking (ECR) when it exists, otherwise last season's scoring. A player ranked high was drafted in almost every league.

- **Name:** `preseason_pos_rank`; **unit:** rank (1 = best); **used by:** waiver_radar
- **Formula:** 2020 on (method ecr): the player's rank among his position's players on FantasyPros' last August/September redraft cheat sheet of that position before the season's first game (fact_ranking.pos_rank, page_kind preseason); not on his roster position's sheet: his rank on his own FantasyPros position's sheet, judged against that position's cutoff. 2013-2019 (method prior_ppg): his rank by last season's regular-season PPG among players of his current roster position with at least 4 games; ties share the better rank
- **Source:** fact_ranking.pos_rank; fact_player_week for last season's PPG
- **Waiver Radar reason:** "Was ranked No. {value:.0f} among {pos}s before the season"

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

### Red-zone targets per game

Targets inside the opponent's 20-yard line, where touchdowns come from.

- **Name:** `rz_targets_avg3`; **unit:** targets per game; **used by:** waiver_radar
- **Formula:** mean over the team's last 3 games of his targets on pass plays (not two-point tries) with yardline_100 <= 20 (team games = regular-season games of the player's as-of team visible at the as-of; last = the most recent, avg3 = mean over the last 3 (fewer early in the season), season = mean over all; a game he missed counts as 0; NULL only when the team has no visible game)
- **Source:** twm.modules.waiver_radar.features; fact_play.receiver_player_id, yardline_100
- **Waiver Radar reason:** "Drew {value:.1f} targets per game inside the opponent's 20-yard line over the last {weeks} games"

### Rookie

First-year players often earn more snaps as the season goes on.

- **Name:** `is_rookie`; **unit:** boolean; **used by:** waiver_radar
- **Formula:** his latest visible roster row's entry_year equals the season
- **Source:** twm.modules.waiver_radar.features; fact_roster_week.entry_year
- **Waiver Radar reason:** "Is a rookie: first-year players often earn more snaps as the season goes on"

### Routes proxy

About how many pass plays he was on the field for: a stand-in for routes run, which nflverse does not publish.

- **Name:** `routes_proxy_avg3`; **unit:** dropbacks per game; **used by:** waiver_radar
- **Formula:** mean over the team's last 3 games of (team dropbacks in the game x his snap share in it); dropbacks = plays with qb_dropback = 1 that are not two-point tries (passes, sacks, scrambles) (team games = regular-season games of the player's as-of team visible at the as-of; last = the most recent, avg3 = mean over the last 3 (fewer early in the season), season = mean over all; a game he missed counts as 0; NULL only when the team has no visible game)
- **Source:** twm.modules.waiver_radar.features; fact_play.qb_dropback, fact_snaps.offense_pct
- **Waiver Radar reason:** "Was on the field for about {value:.0f} pass plays per game over the last {weeks} games"

### Snap share

How often a player was on the field when his team had the ball. Coaches reveal their plans through snaps before the box score does.

- **Name:** `offense_snap_share`; **unit:** share (0-1); **used by:** waiver_radar, regression_watch
- **Formula:** fact_snaps.offense_pct: the player's offensive snaps divided by his team's offensive snaps in that game (Pro Football Reference, rounded to 0.01)
- **Source:** fact_snaps.offense_pct (joined by fact_snaps.gsis_id)
- **Waiver Radar reason:** "{player}'s snap share went from {prev:.0%} to {value:.0%}"

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

### Target share

The share of his team's passes thrown to a player. Targets are the raw material of receiving points.

- **Name:** `target_share`; **unit:** share (0-1); **used by:** waiver_radar, regression_watch
- **Formula:** player targets / team targets in that game (nflverse player_stats)
- **Source:** fact_player_week.target_share
- **Verified:** equals targets / (sum of targets of the player's team that week) on 358,395 of 358,434 player-weeks 2006-2026
- **Waiver Radar reason:** "{player} drew {value:.0%} of his team's targets"

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

### Teammates out at his position

How many players at his position are out.

- **Name:** `teammate_same_pos_unavailable`; **unit:** players; **used by:** waiver_radar
- **Formula:** number of unavailable teammates of his position group
- **Source:** twm.modules.waiver_radar.features; as vacated_target_share
- **Waiver Radar reason:** "{value:.0f} {pos} teammate(s) are out: {teammate}"

### Undrafted

He was not drafted.

- **Name:** `is_undrafted`; **unit:** boolean; **used by:** waiver_radar
- **Formula:** no draft round in dim_player at the as-of
- **Source:** twm.modules.waiver_radar.features; dim_player.draft_round
- **Waiver Radar reason:** "Went undrafted" (when no: "Was drafted: teams give drafted players more chances")

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

### Years of experience

Seasons in the league before this one.

- **Name:** `years_exp`; **unit:** seasons; **used by:** waiver_radar
- **Formula:** his latest visible roster row's years_exp
- **Source:** twm.modules.waiver_radar.features; fact_roster_week.years_exp
- **Waiver Radar reason:** "Has {value:.0f} seasons of NFL experience"

### xFP (expected fantasy points) \*

What an average player would have scored from the same chances (where he was targeted, where he carried the ball). Opportunity is sticky week to week.

- **Name:** `xfp`; **unit:** points; **used by:** regression_watch, waiver_radar
- **Formula:** sum over stats of (expected stat x points per unit in config/scoring.yaml): the ffopportunity model's expected passing, rushing and receiving yards, touchdowns, two-point conversions, interceptions and receptions of a player-game (fact_opportunity_week *_exp columns); fumbles and return or fumble-recovery touchdowns have no expected value and add 0
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

### PPR (points per reception)

A scoring format that gives a point for every catch, on top of yards and touchdowns, which makes pass-catchers more valuable.

- **Name:** `ppr`; **unit:** points per catch; **used by:** shared
- **Formula:** config/scoring.yaml receiving.receptions (1 = full PPR, 0.5 = half, 0 = standard)

### Regression to the mean

A player who scores far above his opportunity usually comes back down; one who scores far below usually rises. Opportunity is 'stickier' than efficiency.

- **Name:** `regression_to_the_mean`; **unit:** -; **used by:** regression_watch
- **Formula:** extreme results drift back toward the average when luck made them extreme

### Unavailable teammate

A teammate who is out now: his targets and carries are up for grabs. Each rule is recorded so the app can say why.

- **Name:** `teammate_unavailable`; **unit:** rule that fired; **used by:** waiver_radar
- **Formula:** a teammate (same as-of team, QB/RB/WR/TE, who played for the team this season) is unavailable at the as-of if ANY of: roster_status = his row on the team's latest visible weekly roster has a status other than ACT, INA, DEV (RES, PUP, SUS, CUT ...), used only in seasons whose roster statuses change from week to week (2016 on: the 2002-2015 rosters repeat one, season-final status on every week); left_team = he was on the team's roster earlier this season but not on its latest visible roster (released or traded); injury_report = the team's latest visible injury report of the season lists him Out or Doubtful; missed_last_game = no offensive snap or stat line in the team's last game after averaging at least 50% snap share in the up to 3 team games before it
- **Source:** twm.modules.waiver_radar.features (fact_roster_week, fact_injury_report, fact_snaps)

### Waiver Radar candidate pool

The players who are probably still on waivers in a typical 12-team league. There is no record of which players sat on fantasy rosters before 2020, so anyone ranked high before the season or scoring well since counts as taken; the rest are the players the Waiver Radar ranks.

- **Name:** `candidate_pool`; **unit:** yes/no per player and as-of (in_pool); **used by:** waiver_radar
- **Formula:** on an NFL roster at the as-of (his latest public weekly roster row of the season, on his team's latest public roster, with status ACT, INA, DEV, at QB/RB/WR/TE) and outside the top N at his position by BOTH preseason_pos_rank and ppg_to_date; N = weekly starter threshold x candidate_pool_multiplier (1.5): QB 18, RB 36, WR 36, TE 18; in seasons without a preseason cheat sheet, rookies drafted in rounds 1-2 count as drafted
- **Source:** twm.modules.waiver_radar.pool.candidate_pool (fact_roster_week, fact_ranking, fact_player_week)

### Weekly starter threshold

A player 'finished as a starter' in a week when he scored well enough that a typical 12-team league would have started him.

- **Name:** `starter_threshold`; **unit:** rank; **used by:** waiver_radar, shared
- **Formula:** teams x starters at the position (config/league.yaml starter_rank_threshold): QB top 12, RB top 24, WR top 24, TE top 12 by fantasy points that week; FLEX-worthy (flex_worthy_rank): RB/WR top 36
- **Source:** config/league.yaml; twm.modules.waiver_radar.labels.LabelRules

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
