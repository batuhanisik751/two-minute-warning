# Coach tendencies (feature #10)

How each head coach's offense plays, for a fantasy player: does it pass more than expected, play
fast, go for it on fourth down? Per season (the current one to date) and career, against the
league, and how much of it carries over from year to year. Code:
`src/twm/modules/coach_tendencies/` (C10a, pure functions over the warehouse); the coach pages
(`web/app/coach/[id]`) publish it next to the Decision Report Card (C10b).

## Attribution

Every regular-season play is credited to the head coach of the team with the ball in that game:
`coach_game` joined on `(game_id, team = posteam)`. `coach_game` is the schedule's coach
corrected by the cited `data/manual/coach_corrections.csv` in the warehouse build, so a
mid-season firing splits the season at the right game (2025: Tennessee, Callahan 6 games and
McCoy 11; the Giants, Daboll 10 and Kafka 7). The join matches 915,877 of 915,879
regular-season pass/run plays of 1999-2026 (two have no offense). Playoffs are not counted.

## Definitions

An **offensive snap** is a play with `pass = 1` or `rush = 1` and a down: dropbacks (sacks and
scrambles included), designed runs, and plays wiped out by a penalty after the snap. Two-point
tries (no down), kneels and spikes are not snaps.

A **neutral snap** is an offensive snap on 1st-3rd down in quarters 1-3 with
`fact_play.is_neutral` (offense win probability 0.20-0.80 inclusive and more than 120 seconds
left in the half; config `neutral`, PROJECT_SPEC 7.3). Neutral situations remove game script:
the trailing team throws, the leading team runs, the last two minutes of a half are a drill.
Garbage time (`is_garbage_time`) can never be neutral.

| metric | definition | sample (`sample` column) |
|---|---|---|
| `neutral_pass_rate` | share of neutral snaps that were dropbacks | neutral snaps |
| `early_down_pass_rate` | the same on 1st and 2nd down | neutral 1st/2nd-down snaps |
| `proe` | pass rate over expected: mean nflfastR `pass_oe` (percentage points, `100 * (pass - xpass)`), all situations, plays where it is defined; nflfastR's `xpass` already accounts for down, distance, field position, score, clock and win probability | offensive snaps with `pass_oe` |
| `neutral_sec_per_play` | game-clock seconds between consecutive snaps of a drive, first snap neutral (pace pairs, below) | pace pairs |
| `no_huddle_rate` | share of neutral snaps run no-huddle | neutral snaps |
| `shotgun_rate` | share of neutral snaps from shotgun | neutral snaps |
| `fourth_go_rate` | share of fourth-down choices (play_type pass, run, punt or field_goal; replayed downs and kneels excluded) where the offense ran a play (fakes count as going), with `is_neutral`, any quarter (a close fourth quarter is where the choice is real) | fourth-down choices |
| `fourth_short_go_rate` | the same on 4th-and-1 or 4th-and-2 | short fourth-down choices |

**Pace pairs.** A neutral offensive snap and the next row of the game's play-by-play when that
row is the same offense's next snap of the same drive (`fixed_drive`) and quarter, and no
timeout was charged on the first play. Anything between two snaps (a timeout row, the
two-minute warning, a pre-snap penalty, a quarter end, a change of possession) breaks the pair.
Seconds = `game_seconds_remaining` of the first minus the second. A stopped clock after an
incompletion counts as it ran (the usual seconds-per-play convention, so pace mixes tempo with
play mix). Pairs over 60 seconds (0.24% in 2013, max 97) are clock glitches and dropped.
Validated on 2013: Chip Kelly's Eagles fastest (23.2 s), then Denver 25.6, Buffalo 26.4, New
England 26.6; slowest the Chargers 33.3 (their known slow-down year), Pittsburgh 33.4, the Rams
34.0; and play by play on 2013_01_PHI_WAS (no-huddle gaps of 7-31 s).

**League average and percentile.** Each season's league value pools every team's plays. The
percentile ranks the coach-team rows of that season with at least 150 offensive snaps (and at
least 10 fourth-down choices, 5 short ones, for the fourth-down rates):
`100 * (average rank - 0.5) / rows ranked`. A higher value gives a higher percentile, so for
`neutral_sec_per_play` a high percentile means a slow offense. Rows below the minimums keep
their value with no percentile.

**Career.** Completed regular seasons pooled (all his plays, not a mean of season means); the
league comparison weights each season's league value by his sample that season, so
`vs_league` compares him with the league of the years he coached. The season in progress has
its own row (`is_current`).

## Coverage

Regular seasons 1999-2026, 945 coach-team-seasons (31-36 a season: a team has two rows when
its coach changed mid-season), plus the current season to date (2026 through week 4 on
2026-10-05, 32 rows). `proe` and `no_huddle_rate` start in 2006: nflfastR's `xpass`/`pass_oe`
are NULL before, and no-huddle is barely charted before (league 0-1.6% in 1999-2005). Earlier
seasons have no row for those two metrics. **Pace is not charted where the clock is too
coarse** (audit 2026-10-06): the early play-by-play repeats `game_seconds_remaining` for many
snaps (1999's league pace came out 0.007 s, 2000's 25.8 s against 30.7 s in 2001), so a
team-game whose offensive snaps carry distinct clock values for fewer than 75% of them gives no
pace pairs (`plays.CLOCK_MIN_SHARE`). That drops every 1999 team-game (all at most 50%), 62 of
2000's 492 and 3 of 2001's; no team-game of 2002-2025 is below 77%. So 1999 has no pace row
(like PROE before 2006) and 2000-2001 pace rests on the charted games (2000 league 29.8 s). The
other metrics cover every season.

## Persistence: is it the coach or the team?

Unit: each completed team-season's main coach (most offensive snaps, at least 400, about seven
games). Values are relative to the league that season, so league-wide drift (shotgun went from
10% to 60% of snaps) cannot pass for persistence. Pearson r of season A with season B, with a
95% interval from a season-block bootstrap (2,000 draws of whole seasons, fixed seed).

| metric | same coach, same team | same coach, new team | new coach, same team |
|---|---|---|---|
| neutral pass rate | 0.47 (0.42 to 0.52), 637 pairs | 0.25 (-0.04 to 0.54), 60 | 0.23 (0.05 to 0.38), 192 |
| early-down pass rate | 0.49 (0.44 to 0.54) | 0.25 (-0.02 to 0.53) | 0.22 (0.06 to 0.37) |
| PROE (2006+) | 0.52 (0.47 to 0.57), 464 | 0.11 (-0.25 to 0.45), 41 | 0.11 (-0.07 to 0.27), 144 |
| neutral pace (2000+) | 0.60 (0.53 to 0.68), 615 | 0.23 (-0.07 to 0.45), 56 | 0.15 (0.04 to 0.27), 183 |
| no-huddle (2006+) | 0.68 (0.56 to 0.78) | 0.55 (-0.07 to 0.92) | 0.16 (-0.01 to 0.29) |
| shotgun | 0.73 (0.67 to 0.78) | 0.43 (0.10 to 0.68) | 0.22 (0.05 to 0.36) |
| fourth-down go rate | 0.38 (0.29 to 0.45) | 0.09 (-0.16 to 0.31) | 0.01 (-0.07 to 0.11) |
| 4th-and-1-2 go rate | 0.28 (0.20 to 0.37) | 0.17 (-0.08 to 0.39) | 0.09 (-0.05 to 0.23) |

Plainly: a tendency carries over (r about 0.5) when the coach AND the team stay. Change either
and pass rate, PROE, pace and fourth-down aggressiveness keep only a fifth or less (r 0.0-0.25):
it is the combination (quarterback, play-caller, roster), not the head coach alone. The
exceptions are formation and tempo habits: shotgun and no-huddle rates follow the coach to a new
team (0.43, 0.55) much more than they stay with the team after he leaves (0.22, 0.16), though
only 41-60 coaches moved, so those intervals are wide. A fourth-down go rate does not stay with
the team at all once the coach leaves (0.01).

## Fantasy link (measured)

Team-seasons 2013-2025 (416; every coach of the season pooled). Production from
`fact_team_week`: targets per game and full-PPR receiving points per game of all the team's
pass-catchers (1 per catch, 0.1 per yard, 6 per touchdown, 2 per two-point catch, -2 per lost
fumble). Both sides relative to their season. Same season (a description, partly mechanical:
more passes are more targets):

- PROE r = 0.71 (0.67 to 0.75) with targets per game; one SD of PROE (4.0 points) goes with
  +2.5 targets and +5.4 receiving PPR points per game;
- neutral pass rate r = 0.64 (0.59 to 0.69); early-down pass rate 0.61; pace r = -0.37 (a
  faster offense, fewer seconds per play, has more targets: 1.3 per game per SD of 1.6 s);
- no-huddle, shotgun and fourth-down rates: r 0.05-0.19, close to nothing.

Next season (this season's tendency, the same team's production next year, whoever coaches):
PROE r = 0.32 (0.26 to 0.38), neutral pass rate 0.31, pace -0.20; the rest about zero. So a
pass-happy, fast offense this year is worth about +1 target per game next year, before knowing
anything about coaching changes.

## Limits

- **Play-caller vs head coach.** The warehouse knows head coaches, not who calls plays. A
  defensive head coach's offense is his coordinator's; the persistence table shows how little
  of a team's style a head coach carries by himself.
- **Garbage time and game script.** The neutral filter removes most of it for the neutral
  metrics; PROE uses every situation because `xpass` already models score, clock and win
  probability. `fourth_go_rate` keeps the neutral band (win probability 0.20-0.80) in all
  quarters.
- **Small samples early in a season.** After four weeks a team has about 120 neutral snaps
  and 10-20 fourth-down choices: show `sample` and `through_week`; percentiles need 150 snaps
  (10 fourth-down choices, 5 short ones) and are among that season's rows to date.
- **Pace is game-clock pace.** Incompletions and out-of-bounds plays shorten the gap, so pace
  mixes tempo with play mix; no-huddle is the cleaner tempo signal. The early clock is coarse:
  no pace in 1999, and 2000-2001 pace from the team-games whose clock is charted (Coverage).
- **nflfastR model columns.** `xpass`/`pass_oe` are model outputs (PROJECT_SPEC 6.3 caveat).

## Frames

`twm.modules.coach_tendencies.build.build(runner, seasons=None, current_season=None)` reads
through `runner.sql` (a `twm.asof.AsOfView`: the current season's row then counts only plays
public at its `as_of`), one regular season at a time (about 3 s for 1999-2026), and returns
(keys in `build.FRAME_KEYS`; every frame sorted, the bootstrap seeded, so equal inputs give
equal frames):

| frame | key | columns |
|---|---|---|
| `coach_tendency_season` | coach_id, team, season, metric | is_current, through_week, games, plays, value, sample, league_avg, percentile |
| `coach_tendency_career` | coach_id, metric | seasons, first_season, last_season, teams (`KC/PHI`), value, sample, league_avg, vs_league |
| `coach_tendency_persistence` | metric, comparison | n_pairs, n_seasons, first_season, last_season, r, ci_low, ci_high |
| `coach_tendency_fantasy_link` | metric, target, horizon | n, n_seasons, r, ci_low, ci_high, x_sd, y_per_x_sd |

Rates are fractions (0-1), `proe` is in percentage points, `neutral_sec_per_play` in seconds.
`percentile` is NULL for unranked rows; a metric without data that season has no row.
Persistence and the fantasy link use completed seasons only (before `current_season`).

## On the site (C10b)

- **CLI**: `twm coach tendencies` prints the newest season's league table (`--season` another);
  `--coach andy-reid` (or `andy_reid`, or part of a name) prints his seasons and career line.
- **Publish** (`twm publish`, migration 0010, `src/twm/publish/coach_tendencies.py`): the four
  frames are rebuilt (completed seasons from the frozen history, "Frozen history (C10c)" below;
  the season in progress from the warehouse through an AsOfView at the publish clock) and
  replaced on every run (each its own hash unit, guarded against shrinking). The warehouse `coach_id`
  (`andy_reid`) becomes the site's id, the slug of `dim_coach.coach_name` (`andy-reid`), and the
  coaches are upserted into `dim_coach`; NaN becomes NULL. site_meta: `coach_tendency_season`,
  `coach_tendency_through_week`.
- **Pages**: `/coach/[id]` "How his offense plays" (every season with value and percentile, pace
  as "faster than N%", the career line, the persistence note); `/decisions#tendencies` (each
  team's current head coach, the coach of its latest game, sortable, with the fantasy link);
  `/methodology#coach-tendencies`. The persistence note and the fantasy link are written from the
  published tables (`web/lib/coach-tendencies.ts`); the only fixed number is the reading rule
  that calls a tendency "carried over" at r >= 0.40.


## Frozen history (C10c)

The scheduled job's warehouse is a partial 2012+ build: a direct build there would silently
publish 2012-2025 career lines, persistence and fantasy link. So every COMPLETED regular season
is frozen on the owner's Mac and pinned with sha256 in `config/production_models.yaml` (key
`coach_tendencies`, `model: history`; `src/twm/modules/coach_tendencies/production.py`):

- `uv run twm coach tendencies-pin [--season S]` builds the frames from the FULL warehouse (as
  the direct build does) and freezes, for every season before S (default `current_season`):
  each coach-team-season's wide row (`tendency_seasons`: per metric the numerator and sample,
  the value, the league value and the percentile), the names of those coaches
  (`tendency_coaches`: the runner's dim_coach only knows 2012+ coaches), and the published
  persistence and fantasy-link frames. It writes zstd Parquet under
  `artifacts/production_models/coach_tendencies/history-<16 hex>/`, a spec JSON (the season,
  the seasons, the module's constants, each file's sha256 and rows; the version is its hash),
  `reports/coach_tendencies/` (seasons, persistence, fantasy link; cited by the model card) and
  the pin. Refused when the seasons do not run from 1999 without a gap (a partial warehouse).
- `twm publish` takes the frozen seasons from the pin and builds only the seasons after them
  from the warehouse (the season in progress), then derives the season rows and the career
  lines from both (numerators and samples summed, so a career line is exact); persistence and
  the fantasy link are the pinned frames. It refuses, in plain words, when the pin is missing,
  a file is missing or not the approved bytes (sha256 checked before reading), the history
  reaches the season in progress, or the code's definitions differ from the frozen ones.
- Equivalence: on the Mac the pinned publish equals the direct build for all four frames and
  the coaches (`tests/test_coach_tendencies_pin.py` on a synthetic warehouse, plus the real one
  on 2026-10-06); a warehouse without the history seasons publishes the same rows.
- `uv run twm model check coach_tendencies` (and `all`) checks the pin, every sha256 and row
  count, the definitions and that the snapshot reproduces the reports.
- Yearly, after the Super Bowl (docs/offseason.md): re-freeze for the new season. Until then a
  season completed after the pin is built from the warehouse and joins the career lines, but
  persistence and the fantasy link stay the pinned seasons'.
