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

### Fantasy points

The score a player earns for your fantasy team in one game, computed from his yards, touchdowns, catches and mistakes with your league's settings.

- **Name:** `fantasy_points`; **unit:** points; **used by:** shared
- **Formula:** sum over stats of (stat value x points per unit in config/scoring.yaml); full PPR by default
- **Source:** twm.scoring.score / score_sql on fact_player_week
- **Verified:** the nflverse_ppr preset reproduces nflverse's fantasy_points_ppr exactly on all 150,691 QB/RB/WR/TE player-weeks 1999-2026 (tests/test_scoring.py)

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

### WPA (win probability added) \*

How much one play or decision changed a team's chance of winning.

- **Name:** `wpa`; **unit:** probability points; **used by:** decisions
- **Formula:** win probability after the play minus before it, for the offense
- **Source:** fact_play.wpa

### Win probability \*

The chance the offense wins from this moment, given score, time, field position and more. Garbage time and neutral situations are defined from it.

- **Name:** `wp`; **unit:** probability (0-1); **used by:** shared, decisions
- **Formula:** nflfastR's estimated probability that the team with the ball wins, before the snap
- **Source:** fact_play.wp

## Features

### Air-yards share

Air yards are how far the ball travels past the line of scrimmage before it is caught or falls. A big share means a player gets the deep, valuable looks.

- **Name:** `air_yards_share`; **unit:** share (0-1); **used by:** waiver_radar, regression_watch
- **Formula:** player's receiving air yards / his team's air yards on all pass attempts (completions, incompletions and interceptions) in that game
- **Source:** fact_player_week.air_yards_share
- **Verified:** exact on all 4,419 2025 player-weeks with air yards (denominator from fact_play)

### Carry share (planned, step C3)

The share of his team's running plays given to a player: the running-back version of target share.

- **Name:** `carry_share`; **unit:** share (0-1); **used by:** waiver_radar, regression_watch
- **Formula:** player carries / team carries in that game

### FPOE (fantasy points over expected) \* (planned, step D1)

Points above or below what his chances were worth: partly skill, largely luck, and it tends to shrink toward zero.

- **Name:** `fpoe`; **unit:** points; **used by:** regression_watch, waiver_radar
- **Formula:** fantasy_points - xfp

### Snap share

How often a player was on the field when his team had the ball. Coaches reveal their plans through snaps before the box score does.

- **Name:** `offense_snap_share`; **unit:** share (0-1); **used by:** waiver_radar, regression_watch
- **Formula:** fact_snaps.offense_pct: the player's offensive snaps divided by his team's offensive snaps in that game (Pro Football Reference, rounded to 0.01)
- **Source:** fact_snaps.offense_pct (joined by fact_snaps.gsis_id)

### Target share

The share of his team's passes thrown to a player. Targets are the raw material of receiving points.

- **Name:** `target_share`; **unit:** share (0-1); **used by:** waiver_radar, regression_watch
- **Formula:** player targets / team targets in that game (nflverse player_stats)
- **Source:** fact_player_week.target_share
- **Verified:** equals targets / (sum of targets of the player's team that week) on 358,395 of 358,434 player-weeks 2006-2026

### WOPR (weighted opportunity rating)

One number that blends how often a player is targeted with how deep those targets are; a good summary of a receiver's opportunity.

- **Name:** `wopr`; **unit:** index (about 0-1); **used by:** waiver_radar, regression_watch
- **Formula:** 1.5 x target_share + 0.7 x air_yards_share
- **Source:** fact_player_week.wopr
- **Verified:** equals the formula on every player-week 2009-2026; nflverse's values for 1999-2008 do not (air-yards data is incomplete before 2006)

### xFP (expected fantasy points) \* (planned, step D1)

What an average player would have scored from the same chances (where he was targeted, where he carried the ball). Opportunity is sticky week to week.

- **Name:** `xfp`; **unit:** points; **used by:** regression_watch, waiver_radar
- **Formula:** the ffopportunity model's expected stats for a player's targets and carries, re-scored with config/scoring.yaml

## Labels (what models predict)

### Sustained hit (planned, step C2)

A stricter target: a player who stays startable, not a one-week spike.

- **Name:** `y_sustained`; **unit:** boolean; **used by:** waiver_radar
- **Formula:** as y_hit, but in at least two of those weeks

### Waiver hit (planned, step C2)

What the Waiver Radar predicts: will this player be startable soon?

- **Name:** `y_hit`; **unit:** boolean; **used by:** waiver_radar
- **Formula:** the player finishes at or above his position's starter threshold in at least one of the next three weeks he plays (bye extends the window)

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

### Weekly starter threshold

A player 'finished as a starter' in a week when he scored well enough that a typical 12-team league would have started him.

- **Name:** `starter_threshold`; **unit:** rank; **used by:** waiver_radar, shared
- **Formula:** teams x starters at the position (config/league.yaml): QB top 12, RB top 24, WR top 24, TE top 12 by fantasy points that week; FLEX-worthy: RB/WR top 36

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
