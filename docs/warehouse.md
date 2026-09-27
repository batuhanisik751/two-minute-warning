# The warehouse (Steps B1-B3)

`twm build 2025 2026` (or `--start 1999`) turns the Parquet cache in `data/raw/` into one
DuckDB file, `data/warehouse.duckdb` (gitignored). The build is written to
`data/warehouse.duckdb.building` and renamed over the real file only when everything succeeded,
so a notebook or `twm doctor` holding the old file never blocks the build and never sees a
half-built one. It never downloads: if a season's file is missing it stops with
`MissingCacheError` listing every missing path (exit code 3), and you run `twm ingest` first.
Seasons before a dataset exists (snap counts before 2013, injuries before 2009, depth charts
before 2001) are skipped and listed in the manifest's `seasons_skipped`; a request with no
season >= 1999 (the first schedule) is refused with a message. `twm doctor` shows the tables
and row counts of the current file. Exit codes of `twm build`: 0 built, 1 build failed and was
rolled back (or another build holds the lock), 2 bad arguments, 3 cache missing.

Terms used below (as-of, closing line, gsis_id, REG/POST, ...) are glossed in
`docs/assumptions.md` section 14. Every table and documented column also carries its
explanation inside the file: `SELECT column_name, comment FROM duckdb_columns() WHERE
table_name = 'fact_game'` (or `duckdb_tables()` for the table docs).

Code: `src/twm/warehouse/schema.py` (what each table is), `weeks.py` (the as-of rules, pure
functions), `available.py` (when each row becomes public, B2), `build.py` (the builder),
`src/twm/ids.py` (the player id map and the unmatched-id report, B3), `src/twm/asof.py`
(point-in-time reading) and `src/twm/backtest/leakage.py` (the leakage harness). Tests:
`tests/test_weeks.py`, `tests/test_warehouse_build.py`, `tests/test_available.py`,
`tests/test_asof.py`, `tests/test_leakage.py`, `tests/test_ids.py`, `tests/test_cli_build.py`
(offline, tiny synthetic fixtures in `tests/conftest.py`), plus opt-in `realdata` tests in
`tests/test_warehouse_build.py`, `tests/test_available.py` and `tests/test_ids.py` that read the
real cache (`uv run pytest -m realdata`, never download; they build into a temporary file).

## Tables

"PK" is the primary key: the columns that identify one row. Uniqueness is checked after every build
and a violation stops the build with `PrimaryKeyError` naming the table, the key and example rows.
Every *event* table (all `fact_*` and `coach_*` tables and `dim_coach`) also has an
`available_at` column, always the last one (`fact_schedule` stores `slot_available_at` just
before it, `dim_player` stores `public_from_utc` last): see "When is a row available?" below.

| Table | One row is... | PK | Source |
|---|---|---|---|
| `fact_game` | one scheduled game, played or not (`result` NULL until played), with `kickoff_utc`, `game_end_utc_est` (= kickoff + 4 h, an estimate), `home_implied_total`/`away_implied_total` from the closing line (the betting market's expected margin and combined score right before kickoff; a team's implied total is the points the market expects it to score), `season_type`, `availability_game_end_utc` (the game end the `available_at` rules use) | `game_id` | schedules |
| `fact_schedule` | the pre-game view of one scheduled game: only what is public once the schedule is announced (teams, week, date and time, venue, rest days). No scores, lines, coaches, weather or roof state. It is nflverse's FINAL schedule; the date/time/venue columns have their own `slot_available_at` | `game_id` | fact_game |
| `fact_play` | one play, 128 curated columns (identifiers, game state, play descriptors, main players, nflverse model columns, context). All 128 names exist in `data/schemas/pbp.json`; nothing was dropped | `game_id, play_id` | pbp |
| `fact_player_week` | one player in one game week, every weekly stat column | `player_id, season, week, season_type` | player_stats |
| `fact_team_week` | one team in one game week | `team, season, week, season_type` | team_stats |
| `fact_snaps` | one player in one game with snap counts and percentages. The source names players by Pro-Football-Reference id (`pfr_player_id`); `gsis_id` (right after it) is mapped through `bridge_player_id` (NULL for 234 of 327,698 rows on the full build: ids with no link, and 6 rows whose link the usage check found implausible; all listed in the id report). `(game_id, gsis_id)` is unique among rows with a gsis_id (counted on every build, `notes.n_duplicate_game_gsis`, 0), so features can join on it | `game_id, pfr_player_id` | snap_counts |
| `fact_injury_report` | one player on one team's injury report for one week; `date_modified` is the report row's last-modified time where the source has it (2010-2024; NULL for 2025+ and effectively 2009, see assumptions section 4) | `season, week, team, gsis_id` | injuries |
| `fact_depth_chart` | one depth-chart slot, from either upstream format (`source_format` = `legacy` weekly charts 2001-2024, or `daily` snapshots 2025+) | see below | depth_charts |
| `dim_team` | one of the 36 nflverse team rows; `current_abbr` maps OAK→LV, SD→LAC, STL→LA, LAR→LA; `is_current` is false for those four | `team_abbr` | teams |
| `dim_player` | one player from nflverse's player table, with one id per other system (`pfr_id`, `espn_id`, `sleeper_id`, `fantasypros_id`, `yahoo_id`, `sportradar_id`, `mfl_id`, all from `bridge_player_id`, see "Player IDs") and `public_from_utc`, the moment he exists point-in-time (his draft, or his first public data row). `dim_team` and `dim_player` are snapshots of the one-file datasets (`all.parquet`) (manifest `seasons` = `[]`) and change whenever `twm ingest` refreshes them; since B3 the ids and `public_from_utc` also use the built seasons' weekly rosters and facts | `gsis_id` | players (+ bridge) |
| `bridge_player_id` | one id of another system (`id_type`, `source_id`) and the `gsis_id` it belongs to, with the `method` that linked it, `n_candidates` and `is_conflict`; see "Player IDs" | `id_type, source_id` | players, rosters_weekly, ff_playerids, data/manual |
| `report_id_coverage` | the unmatched-id report, part 1: rows and distinct ids per dataset, season, id type and scope (`all`, `fantasy` = QB/RB/WR/TE) and how many map to a `gsis_id` (bookkeeping, rebuilt on every build) | `dataset, season, id_type, scope` | the build + raw cache |
| `report_id_unmatched` | the unmatched-id report, part 2: every unmatched id with a name, plus suspect links (usage contradicts the link), ambiguous ids, conflicts, name-pass links and players with several ids of one type (`kind`) | `kind, dataset, id_type, source_id` | the build + raw cache |
| `dim_coach` | one head coach; `coach_id` is a slug (`mike_mccarthy`) and spellings that share a slug are merged into one row (`coach_name` = whitespace collapsed, alphabetically first spelling; merges listed in `build_manifest.notes.merged_spellings`). Interim coaches the schedule never names (2024+) are absent | `coach_id` | fact_game |
| `coach_game` | one team in one game and the head coach the schedule lists for it (unplayed games included). The schedule columns record mid-season changes through 2023 but not in 2024-2025 (TEN/NYG 2025, NYJ/CHI/NO 2024 show the fired coach all season), so interim coaches are missing there | `game_id, team` | fact_game |
| `coach_team_season` | one coach-team-season as listed in the schedule: first/last scheduled week and `n_games` = games scheduled (played or not, playoffs included; 17 already for every 2026 coach). Point-in-time since B2: a stint row appears only once the stint is over (season end, or the next coach's first kickoff), so at an in-season as-of the current coach has no row here; count `coach_game` rows through `AsOfView` for games coached to date. A team has more than one row only when nflverse recorded the change (2000-2023) | `coach_id, team, season` | coach_game |
| `dim_week` | one (season, week, season_type) that appears in the schedule, with the official as-of timestamps (point-in-time, a week's game counts are masked until its own as-of) | `season, week, season_type` | fact_game |
| `build_manifest` | one built table: rows, content hash, seasons, dropped-row counts, versions (bookkeeping: not part of the as-of view) | `table_name` | the build |

Hot-Seat firing labels (H1/H2) must therefore combine the schedule's coach changes (reliable
2000-2023) with an owner-verified manual file (`data/manual/coach_departures.csv`, to be created
in the Hot-Seat step) for 2024 onward; `build_manifest.notes.n_team_seasons_multi_coach` on
`coach_team_season` shows how many team-seasons the schedule itself splits. Evidence per season
is in `docs/assumptions.md` section 10.

A schedule row mixes three availabilities: the fixture itself is known months ahead; the
closing lines (`spread_line`, `total_line`, the implied totals) exist only just before kickoff
(using an upcoming game's closing line at a Tuesday as-of is leakage, spec 6.4); `result`,
`total` and the scores exist after the game. B2 keeps them apart with two tables: the whole
`fact_game` row becomes available when the game is over (every column is public by then), and
`fact_schedule` carries only the pre-game columns, available from the schedule release (regular
season) or the end of the previous playoff round, with the date/time/venue columns public only
from `slot_available_at` (they can still change). Features that need "upcoming opponents", byes
or games remaining read `fact_schedule`; nothing in P1 reads an unplayed game's line.

Season types: schedules `game_type` is REG (regular season), WC (Wild Card), DIV (Divisional),
CON (Conference championship) or SB (Super Bowl); legacy depth charts add SBBYE, the idle week
before the Super Bowl. `season_type` is `REG` for REG and `POST` for everything else, derived by
the same rule in `weeks.season_type_of` (Python, for `fact_game` and `dim_week`) and
`schema.season_type_sql` (SQL, for injuries and legacy depth charts); `tests/test_weeks.py`
asserts the two agree on every `game_type` value.

### Team abbreviations

Play-by-play, player stats and team stats use today's abbreviations back to 1999 (LA, LAC, LV),
but schedules, snap counts, injuries and legacy depth charts use the abbreviation of the day (STL,
SD, OAK, LAR). Without a fix, `fact_game.home_team` and `fact_play.home_team` disagree for the same
`game_id` in 1999-2019. So every team column in the warehouse is normalized to the current
abbreviation at build time, and the original spelling is kept in a `*_raw` column
(`home_team_raw`, `team_raw`, `opponent_raw`). Empty-string team codes in 1999-2000 play-by-play become
NULL. Always join on the normalized columns.

### Rows the build drops (and counts)

Counts are for the full 1999-2026 history (a 2025+2026 build shows a fraction of them).

- `fact_player_week`: rows with a NULL `player_id` (one placeholder row per week upstream,
  2001-2026: 21 per season through 2020, 22 from 2021, 3 so far in 2026; 533 rows in all).
  `build_manifest.n_dropped_null_key`.
- `fact_team_week`: rows with a NULL `team` (one 1999 week-9 row upstream, game
  `1999_09_PHI_CAR`).
- `dim_player`: rows without a `gsis_id` (none today).
- `fact_injury_report`: when the same player appears twice in a team-week (two 2024 week-15 pairs,
  HOU `00-0039359` and NYJ `00-0034270`), the row with the latest `date_modified` is kept (the
  later `Out` row in both cases); ties fall back to the other columns so the choice is
  deterministic. `build_manifest.n_dropped_duplicates`.
- `fact_depth_chart`: exact duplicate rows in the legacy files (3,856 rows 2001-2024, 1,628 of
  them in 2001). Two legacy rows equal on the key but different elsewhere are *not* removed: they
  stop the build with `PrimaryKeyError` (none exist in the cache today).

Upstream gaps that are not build bugs: three played games have no play-by-play rows
(`1999_01_BAL_STL`, `2000_03_SD_KC`, `2000_06_BUF_MIA`); the 2013 injury file has no Super Bowl
rows and the 2023 one only REG and WC; the 2005 legacy depth-chart file is REG only. Listed in
`docs/assumptions.md` section 10 too.

### `fact_depth_chart`

Columns: `season`, `source_format`, `week` (legacy chart week; NULL for SBBYE rows and for daily
rows), `week_at_dt` (daily only: the `dim_week` week whose window contains `dt`; NULL after the
season's last as-of, i.e. offseason snapshots belong to no week; it is a window label for as-of
filtering, not a chart selector, see the as-of rules), `game_type`, `season_type`,
`dt` (daily snapshot time, UTC), `team`, `team_raw`, `gsis_id`, `espn_id`, `gsis_id_from_espn`
(B3: a daily row whose source has no `gsis_id` gets it from its `espn_id` through
`bridge_player_id`; 1,125 rows on the full build, 31,605 daily rows still have an ESPN id but no
`gsis_id`), `player_name`,
`player_position` (legacy roster position), `unit` (offense / defense / special_teams / other),
`unit_raw` (legacy formation or daily `pos_grp`), `position` (slot: legacy `depth_position` or
daily `pos_abb`), `pos_slot` (daily), `depth_rank` (1 = starter).

Key: `season, source_format, week, game_type, dt, team, unit_raw, position, pos_slot, depth_rank,
gsis_id`. Things this key deliberately allows: a REG chart and a WC chart for the same legacy week;
NULL `week` on SBBYE rows; NULL `gsis_id` on daily rows (empty slots and unmatched names); the same
player in several daily slots (WR and KR). A daily "snapshot" is one league-wide pull (`dt`, all 32 teams); a
date can have up to three pulls, so rows are keyed and mapped to a week by `dt`, never by date
(assumptions section 3).

Legacy charts carry rows that match no schedule week: REG week 18 in 2007-2013 and 2015-2020,
REG week 19 in 2021-2024 (a chart pulled the week after the finale), and SBBYE rows with NULL
`week` (2001, 2007-2013 and 2015-2024). B2 keeps them and places them in time: a REG row whose
week has no REG week belongs to the week with the same number (the Wild Card week), and an
SBBYE row to the season's Super Bowl week (both later than the chart really appeared, so safe).
`week_at_dt` (daily rows) is a window label for grouping and inspection only; the daily charts'
`available_at` is `dt` itself.

## As-of rules (`dim_week`)

Every prediction has an as-of time, and only rows available at that time may be used
(PROJECT_SPEC Section 6). `dim_week` stores those times so every module reads the same ones.

- `asof_weekly_utc` (config `as_of.weekly`, Tuesday 14:00 UTC): the first Tuesday 14:00 UTC
  **strictly after the Eastern date of the week's first kickoff**. A normal Thursday-to-Monday week
  gets the Tuesday after Monday night; a Wednesday opener (2012, 2026) gets the Tuesday six days
  later; a Saturday playoff start gets the Tuesday three days later.
  Why the calendar and not "after the last game": in 2010 W16, 2020 W5/W12/W13 and 2021 W15 one game
  (two in 2021 W15) was moved to Tuesday or Wednesday. An as-of "after the last game" would land after the *next*
  week had already kicked off, which breaks point-in-time. With the calendar rule the moved game(s)
  simply end after the as-of: `n_games_after_asof` counts such games and `is_split_week` flags the
  week. The build asserts that every week's as-of is before the next week's first kickoff.
- `window_start_utc` / `window_end_utc`: week N owns timestamps `t` with
  `previous week's as-of < t <= this week's as-of`, across the whole season (REG and POST together,
  so the gap between week 18 and the Wild Card round has an owner). Before week 1's as-of → week 1:
  every snapshot from the file's first date (late March in 2026) up to week 1's as-of, six months
  of offseason charts in 2026. After the last week's as-of → no week.
  `weeks.week_for_timestamp(rows, season, ts)` is the one implementation of this rule and
  `fact_depth_chart.week_at_dt` calls it. `week_at_dt` is a window label for as-of filtering, not
  a chart selector: to get the chart in force at an as-of, take the latest `dt <= as_of` per team.
  The build asserts every as-of precedes the next week's first kickoff before it maps anything.
- `asof_end_of_regular_season_utc` (config `as_of.hot_seat_end_of_season`, only on the row with
  `is_last_reg_week`): 1 day after the **last game date (Eastern)** of the last regular-season
  week at 12:00 UTC (07:00 EST Monday: the last regular-season week always falls in standard
  time, so the local hour never varies; before Black Monday, the Monday after the regular
  season when most firings are announced). It is computed from the date string, not from
  kickoff times, so a Sunday-night game that ends after midnight UTC still
  gives Monday. Works for 17-week seasons (last REG week 17) and 18-week seasons alike.
  Caveat for the Hot-Seat step: this is the *morning after* the last game, and a few departures
  are announced on the Sunday evening of the finale itself (New England's on 2025-01-05, hours
  after the game), i.e. before this as-of. Section 6.2 puts labels strictly after `as_of`, so the
  Hot-Seat label window ("announced after as_of and within 30 days") should be defined relative to
  the last REG kickoff (or use `days_after: 0` with a time after the last game's estimated end)
  so same-evening announcements are not dropped. Decide in the Hot-Seat step; recorded here so the
  timestamp's owner (B1) and its consumer agree on the caveat.
- `last_game_end_utc_est` = last kickoff + 4 h, kept for reference. 1999 schedules have no kickoff
  times, and the 2000-2005 schedules record all but one Monday-night game (2005_02_NYG_NO, the
  relocated Katrina game, has a real 19:30; plus two 2001 Saturday games and one Thursday game
  each in 2003-2005; 102 rows, 17 per season) with `gametime` `09:00`, a
  12-hour-clock placeholder for the true 21:00/20:30 ET kickoffs. Any `gametime` before 09:30 ET
  (the earliest real kickoff, London games) is treated as missing. All those rows get a documented
  default (13:00 ET Sunday, 16:30 ET Saturday, 20:00 ET otherwise) and
  `fact_game.kickoff_is_estimated = true`. `dim_week.n_kickoff_estimated` counts them per week, so
  a reader of `dim_week` alone can tell that 1999's (and those weeks') `first/last_kickoff_utc`,
  `last_game_end_utc_est` and `n_games_after_asof` are guesses (`asof_weekly_utc` is not affected:
  it comes from the game date). The defaults are typical slots, not the latest ones: a 1999
  late-afternoon or Sunday-night game gets an estimated end up to ~7 h too early, and the 20:00
  ET weekday default is an hour before the 21:00 ET Monday-night kickoffs of that era. No official
  as-of (Tuesday 14:00 UTC, Monday 12:00 UTC end-of-season) falls inside those gaps, so nothing
  leaks; B2 does not trust `game_end_utc_est` for flagged rows anyway: their
  `availability_game_end_utc` assumes the latest normal night kickoff for that weekday (21:00 ET
  on Mondays, 20:30 ET otherwise; see below).

Worked example, 2025 week 1: first game Thursday 2025-09-04 (DAL at PHI), last game Monday
2025-09-08 20:15 ET (MIN at CHI; `kickoff_utc` 2025-09-09 00:15, `game_end_utc_est` 04:15).
`asof_weekly_utc` = Tuesday 2025-09-09 14:00 UTC. Week 2's window is (2025-09-09 14:00,
2025-09-16 14:00]. The 2025 depth-chart snapshot taken 2025-09-10 07:14 UTC therefore has
`week_at_dt = 2`. The last regular-season week (18) ends Sunday 2026-01-04, so
`asof_end_of_regular_season_utc` = Monday 2026-01-05 12:00 UTC, and the Super Bowl's as-of is
Tuesday 2026-02-10 14:00 UTC; the 86,102 daily depth-chart rows dated after that have
`week_at_dt = NULL`.

Kickoff conversion: `gameday` + `gametime` are US Eastern wall-clock strings; `zoneinfo`
converts them, so 2026-09-10 20:35 ET → 2026-09-11 00:35 UTC (daylight time) and 2027-01-10
13:00 ET → 18:00 UTC (standard time). International 09:30 ET kickoffs become 13:30 UTC.

## When is a row available? (`available_at`)

**The principle.** Every prediction is made at a moment, its *as-of* (for example the Tuesday
14:00 UTC after week 5). A backtest is honest only if the prediction uses what was public at
that moment and nothing later. So every *event* row in the warehouse carries `available_at`:
the earliest moment (UTC) we are confident the **whole row, every column of it**, was public.
A prediction at `as_of` may use a row only if `available_at <= as_of` (inclusive: a row stamped
exactly at the as-of counts as known).

We rarely know the true publication time, so the rules are estimates, and **every estimate
errs late**. A late estimate costs a few hours of information; an early one lets the future
leak into the backtest and makes a model look better than it will be live. Every event row must
get an `available_at`: if a rule cannot place a row in time, the build stops with
`AvailabilityError` naming the table, the number of rows and examples (for instance a daily
depth-chart row without a `dt`, or a stat row whose `game_id` is not in the schedule).

**Four kinds of tables** (`twm.warehouse.available.TABLE_AVAILABILITY`, which also holds the
one-line rule stored as the `available_at` column comment in the file):

- *event* tables have `available_at` (last column): all `fact_*` tables, `coach_game`,
  `coach_team_season`, `dim_coach`. A few of their columns are snapshots taken **today**
  (`hindsight_columns`): `fact_player_week.position`, `position_group` and `headshot_url`, and
  `fact_depth_chart.player_position`. A player who changed position shows the later one for
  every past season (Cordarrelle Patterson WR → RB in 2021, Taysom Hill QB → TE), so the as-of
  view leaves those columns out. A point-in-time position must be derived later (B3/C1) from
  the as-of-visible depth-chart slot (`fact_depth_chart.position`) or from weekly rosters;
- *static* tables are always visible: `dim_team` and `dim_week` (the calendar of as-of times).
  `dim_week`'s columns computed from a week's final game list (`n_games`,
  `n_kickoff_estimated`, first/last gameday and kickoff, `last_game_end_utc_est`,
  `n_games_after_asof`, `is_split_week`) reveal later postponements and cancellations, so the
  as-of view shows them as NULL until that week's own `asof_weekly_utc` (the calendar columns,
  `asof_weekly_utc`, `prev_week`, `window_*`, stay visible). Accepted hindsight in `dim_team`:
  `team_division` is today's alignment (Seattle shows NFC West for 1999-2001), and every team
  column in the warehouse uses today's franchise code (a 2014 STL row reads LA), which reveals
  later relocations;
- *hindsight* tables are snapshots taken today: `dim_player` and `bridge_player_id`. A player
  **exists point-in-time** only from `public_from_utc`: May 15 (config
  `availability.draft_public_month_day`, later than every draft's last day, before the June 1
  board) of his `draft_year`, or, undrafted, the earliest `available_at` of his rows in
  `fact_player_week`, `fact_snaps` (through its B3 `gsis_id`), `fact_injury_report` and
  `fact_depth_chart`. An undrafted player with no such row never appears (6,924 of 24,833 on
  the full build, mostly pre-1999 players; 6,925 before B3 mapped snap counts and ESPN-only
  depth-chart rows to players). Snap rows whose link the usage check re-linked or left NULL
  (see "Player IDs") never make the wrong player public. A `bridge_player_id` row copies its player's `public_from_utc`,
  so a future player's ids are hidden exactly like the player himself; the view shows
  `id_type`, `source_id` and `gsis_id` (`method`, `n_candidates`, `is_conflict` are hidden
  bookkeeping). Without this row rule a
  2015 as-of would list every later draft class, and the top of next year's draft order is this
  season's final standings. The visible columns are those that do not change later:
  `gsis_id`, `display_name`, `birth_date`, `draft_year`, `draft_round`, `draft_pick`,
  `draft_team` (today's franchise code: a 2016 San Diego pick reads LAC), `college_name`, and
  the ids `espn_id`, `pfr_id`, `sleeper_id`, `fantasypros_id`, `yahoo_id`, `sportradar_id`,
  `mfl_id` (identifiers, not features: spec 6.2 rule 5 is enforced by the feature registry).
  Hidden: `last_season` (tells you in 2019 whether a player retires after
  2019), `status` (later injuries, retirements), `latest_team` (every later trade or signing),
  `rookie_season` (a player who has not debuted yet would already "exist"), `position` and
  `position_group` (today's, see above), `height` and `weight` (today's listing);
- *meta*: `build_manifest` (row counts of the whole warehouse, future rows included) and the
  id report tables `report_id_coverage` and `report_id_unmatched`: not part of the as-of view;
  read `wh.<table>` when you need them.

### The rules

"Game end" below is `fact_game.availability_game_end_utc`: kickoff + 4 h
(`game_end_utc_est`), except for games whose kickoff time was guessed (all of 1999, the 102
2000-2005 placeholders), which are assumed to kick off in the latest normal night slot for their
weekday (config `availability.estimated_kickoff_for_availability_et`: 21:00 ET on Mondays, the
Monday-night kickoff of 1999-2005, and 20:30 ET otherwise) + 4 h, and never earlier than the
guess. "Kickoff" is game end minus 4 h (the real kickoff, or the late night-slot guess).

| Table | `available_at` | Why this is safe |
|---|---|---|
| `fact_game` | game end + `game_result_lag_hours` (3 h) | The row holds the final score, closing lines and coaches: all public once the game is over. Real games ran past kickoff + 4 h (weather delays up to 77 min, overtime); on every 2020+ game with a reliable play clock the last play is before this time. Lines of played games are allowed (spec 6.4). |
| `fact_schedule` | REG: `schedule_release_month_day` (May 20) of the season, 00:00 UTC. POST: when the previous round's last game is final (game end + 3 h; Wild Card: the last regular-season game); a playoff game with no previous week in the build uses its own kickoff. Listed schedule changes: not before their own kickoff (below) | Schedules come out in April or May; May 20 is later than every release. A playoff matchup is set when the previous round ends. Only pre-game columns are in this table. |
| `fact_schedule.slot_available_at` (gates `gameday`, `weekday`, `gametime`, `kickoff_utc`, `kickoff_is_estimated`, `location`, `stadium`, `away_rest`, `home_rest`) | the latest of: the row's `available_at`; kickoff − `schedule_slot_lead_days` (12); for the last two regular-season weeks the previous week's as-of; for a listed change, its kickoff | Date, time and venue can still change after the release: games are flexed with 12 days' notice, and Week 17/18 slots are picked after the previous week. The as-of view shows these columns as NULL until then; who plays whom and the week number stay visible. |
| `fact_play`, `fact_player_week`, `fact_team_week`, `fact_snaps` | the row's game (joined on `game_id`) end + `game_data_lag_hours` (6 h each) | nflverse publishes game data in a nightly run after the game. Each row uses its own game, never a per-week constant, so a game moved to Tuesday or Wednesday (the split weeks) is not visible at its week's Tuesday as-of and is at the next. |
| `fact_injury_report` | 2021-2024 rows with `date_modified`: that stamp. 2010-2020 rows with a stamp: the stamp + `injury_legacy_stamp_offset_hours` (9 h). Otherwise (2009, the 62 null rows of 2010, 2025+): the team's kickoff that week. A team without a game that week: the week's `asof_weekly_utc`. Then never earlier than 1 s after the **previous** week's as-of | `date_modified` is the row's last change, observed. From 2021 the stamps are real UTC; the 2010-2020 ones are not (their time of day does not move with daylight saving time, see `docs/assumptions.md` section 4), so they count 9 h later. The final report is always out by kickoff. The floor enforces spec 6.1: week N+1 reports are not available at the Tuesday as-of after week N. |
| `fact_depth_chart` | daily rows (2025+): `dt`. Legacy weekly rows (2001-2024): that week's `asof_weekly_utc` minus 6 days, the Wednesday 14:00 UTC before the week's games. REG rows whose week has no REG week (the post-finale chart) use the week with the same number (Wild Card); SBBYE rows use the Super Bowl week | Week N's chart is visible at the as-of after week N and week N+1's is not (as-ofs are at least 7 days apart). The orphan mappings are later than the charts' real dates. |
| `coach_game` | kickoff | Who coaches a game is certain at kickoff. A future game's listed coach can reveal a firing, so it stays hidden until then. |
| `coach_team_season` | when the stint's last game is final (game end + 3 h), or, if another coach coaches the team later that season, that coach's first kickoff | A stint's `last_week`/`n_games` are only known once it is over, and a finished stint row mid-season means "fired": it may only appear once the new coach is on the sideline. |
| `dim_coach` | kickoff of the coach's first game in the warehouse | A coach exists, for point-in-time purposes, from his first game. |
| `dim_player.public_from_utc` (row rule, hindsight table) | draft: May 15 of `draft_year`; undrafted: his first data row's `available_at`; else NULL (never) | See "Four kinds of tables". |
| `bridge_player_id.public_from_utc` (row rule, hindsight table) | the linked player's `dim_player.public_from_utc` | An id is only as public as its player. |

**Schedule changes after the release.** nflverse keeps only the FINAL schedule, so without
more a game moved later in the year shows its new week, date or stadium from May 20 on. A
curated list in `config/settings.yaml` (`availability.schedule_exceptions`, 24 entries: Hurricane
Irma's 2017 week-1 game played in week 11, the 2020 COVID moves, the 2014 and 2022 snowstorm
games at Ford Field, the 2020 49ers games in Arizona, the snow-delayed 2023 Wild Card game at
Buffalo ...) names each changed game; the build checks every id against the data. Such a row,
and its slot, count as public only from the game's own kickoff. That is later than the real
announcement for every change (a game is always announced before it is played), so no date has
to be typed in, and nothing in nflverse could verify one. The cost is small: for a few weeks
before each of these 24 games, the moved game is missing from "upcoming games". A change may
carry an earlier `announced` date only together with a `source` link (the config refuses a date
without one); it then counts from 12:00 UTC the day after. The cancelled 2022 week-17 Buffalo at
Cincinnati game is absent from nflverse (both teams show 16 games); it is listed with
`cancelled: true` and recorded in `build_manifest.notes.cancelled_games`, so a "games remaining"
feature for those two teams in 2022 can add it back (`AvailabilityRules.cancelled_games()`). This is a known limit that is reduced, not
removed: a change that is not on the list cannot be detected from nflverse, so features must
not treat a future week's game count, bye or split flag as known in advance for those seasons.

`build_manifest.notes` records per table how many rows each branch placed
(`available_at_rule_counts`, every branch listed, zeros included; `public_from_utc_rule_counts`
on `dim_player`: draft / first_fact_row / never) and `n_available_after_week_asof`: rows not yet
public at their own week's Tuesday as-of. On the full 1999-2026 build that is 6 games (the five
split weeks) and their plays/stats/snaps, 16 injury rows stamped after their week's as-of, 12
`coach_game` rows (split-week games) and 43 coaching stints that ended with a change; 0 for
`fact_schedule` and `fact_depth_chart`. `fact_schedule` also notes `n_schedule_exceptions`,
`n_slot_last_two_reg_weeks`, `cancelled_games` and `schedule_exceptions_not_found` (a listed
game the built seasons do not have: a config typo or an nflverse change).

Knobs (`config/settings.yaml`, `availability:`, validated on load): `game_data_lag_hours` per
dataset (0-48), `game_result_lag_hours` (0-48), `schedule_release_month_day` (`MM-DD`),
`schedule_slot_lead_days` (0-60), `estimated_kickoff_for_availability_et` (weekday → `HH:MM`,
plus `default`), `injury_legacy_stamp_offset_hours` (0-48), `draft_public_month_day` (`MM-DD`)
and `schedule_exceptions`. The 6 h lag was chosen from the data, and the margins are thin:

- the tightest non-split week is a late Monday-night week-1 game (22:25 ET kickoff, 2007): it
  ends 7.6 h before its Tuesday as-of, so its rows arrive with **1.6 h** to spare; the guessed
  1999-2005 Monday-night games (21:00 ET) have 2 h;
- the regular-season finale ends 6.5-6.7 h before the end-of-season snapshot (Sunday-night
  finales 2006-2025), a **0.5 h** margin after the lag; the guessed 1999-2005 Sunday night
  slot also leaves 0.5 h, and the 1999-2002 **Monday-night** finales (21:00 ET) land exactly
  at the 12:00 UTC snapshot (**0 h**, visible because the boundary is inclusive);
- the final score (`fact_game`, game end + 3 h) keeps 3 h at the end-of-season snapshot.

So the build checks it: if any game-data or `fact_game` row of a non-split week misses its
Tuesday as-of, or any finale row misses the end-of-season snapshot, it stops with
`AvailabilityError` (a larger lag in config would otherwise silently drop Monday-night games).

### Reading the warehouse point-in-time (`twm.asof`)

```python
from twm.asof import AsOfView, weekly_as_of

db = "data/warehouse.duckdb"
as_of = weekly_as_of(db, 2025, 5)  # 2025-10-07 14:00 UTC, timezone-aware
with AsOfView(db, as_of) as v:
    rec = v.sql("""SELECT player_id, sum(receptions) AS rec
                   FROM fact_player_week WHERE season = 2025 GROUP BY player_id""")
```

Inside an `AsOfView` every table has its normal name: event tables show only rows with
`available_at <= as_of` and without their today's-snapshot columns (`SELECT position FROM
fact_player_week` is an error); `fact_schedule` shows its slot columns as NULL before
`slot_available_at`; `dim_team` everything; `dim_week` every week, with the schedule counts of
weeks not played yet as NULL; `dim_player` only the players who exist by then and only the
allowlisted columns (`SELECT latest_team FROM dim_player` is an error); `bridge_player_id` only
the ids of players who exist; `build_manifest` and the `report_id_*` tables are not there. Plain SQL is therefore safe by default. The real warehouse is attached read-only as `wh`:
`wh.fact_player_week` returns every row and column, future included. That is the deliberate,
visible bypass (for inspection and for building labels), and exactly what the leakage harness
catches when a feature uses it. Table names are matched in any letter case.

For polars frames: `asof_filter(df, as_of)` keeps `available_at <= as_of` (features) and
`outcomes_after(df, as_of, until=...)` keeps `as_of < available_at <= until` (labels, spec 6.2
rule 3), so a row can never be both. Both accept a DataFrame or LazyFrame and refuse a missing,
non-datetime or NULL `available_at`, and a frame that was joined before filtering (it has
`available_at_right` too, whose future rows would slip through): **filter each table, then
join**. `as_of` must be timezone-aware: a naive `datetime` raises `TypeError`, because a local
time silently read as UTC (or the reverse) can leak a whole game.
`end_of_regular_season_as_of(db, season)` gives the Hot-Seat snapshot. A warehouse built before
these rules raises `WarehouseTooOldError` ("rebuild it with `twm build`").

`uv run twm asof 2025 5` prints a week's as-of and, per event table, how many of the season's
rows were visible at that moment.

### The leakage harness (`twm.backtest.leakage`)

`assert_future_invariant(builder, db_path, as_of, key=[...])` runs a feature builder (a function
that takes an `AsOfView` and returns a polars DataFrame) on the real warehouse, then on two
scratch copies in which everything not yet public at `as_of` is (a) deleted or (b) scrambled:
event rows with `available_at > as_of` and `dim_player` rows of players who do not exist yet
(deleted in (a), their non-key values scrambled in (b)); masked values (`fact_schedule` slots
before `slot_available_at`, `dim_week` counts of weeks not played yet: NULL in (a), scrambled in
(b)); today's-snapshot columns (`dim_player`'s hidden columns, `fact_player_week.position` ...:
scrambled on every row in (b)). Scrambling is per type: integers `x * 3 + 7` (or bitwise NOT
where that would not fit, so a value near the type's limit never crashes the harness), floats
`x * 3 + 7`, booleans negated, text reversed, other timestamps +1 day; keys such as `game_id`,
`player_id`, `season`, `week`, `team` untouched. A builder that never sees the future gives
identical output; any difference raises `LeakageError` naming the variant, the columns and
example rows. `key` must identify the output rows (checked).

```python
def season_receptions(v):  # passes: reads through the view
    return v.sql(
        "SELECT player_id, sum(receptions) AS rec FROM fact_player_week "
        "WHERE season = 2020 GROUP BY 1"
    )


def leaky(v):  # fails: 'week <= 12' is not time
    return v.sql(
        "SELECT player_id, sum(receptions) AS rec FROM wh.fact_player_week "
        "WHERE season = 2020 AND week <= 12 GROUP BY 1"
    )


assert_future_invariant(leaky, db, weekly_as_of(db, 2020, 12), key=["player_id"])
# LeakageError: [deleted] the output has 1844 rows on the real warehouse but 1840 ...
```

The second one fails on real data because Baltimore at Pittsburgh (2020 week 12) was moved to
Wednesday: it is week 12, but it had not been played at week 12's Tuesday as-of. The tests in
`tests/test_leakage.py` are the spec's "a deliberately leaky feature fails the as-of test"
(bypasses, a split week, labels, today's team and position, a future week's game count, a
Week-18 kickoff slot, next year's draft class, integers near the type limit).

Limits (read before trusting a green result):

- the harness checks one as-of per call (test a split week and a season start too), and the
  builder must be deterministic;
- a NULL future value stays NULL when scrambled, and a change smaller than the float tolerance
  is not seen;
- **static tables (`dim_team`, `dim_week`'s calendar columns) and `dim_player`'s allowlisted
  columns of players who already exist are copied unchanged: they are TRUSTED, not tested.**
  A builder that uses `dim_team.team_division` (today's alignment) passes; keep future-looking
  reads of those tables out of feature builders, or give the table a row or column rule first;
- it cannot see leakage already **baked into a visible value**: nflverse model columns (spec
  6.3), stat corrections, the FINAL schedule in `fact_schedule` (a change that is not in
  `schedule_exceptions`), or a wrong `available_at` rule. It tests builders, not the warehouse.

### Worked example: 2025 week 5, Tuesday as-of

`weekly_as_of(db, 2025, 5)` = Tuesday 2025-10-07 14:00 UTC. Week 5 ended with Kansas City at
Jacksonville on Monday 2025-10-06 20:15 ET (kickoff 2025-10-07 00:15 UTC).

- Visible: all 78 games of weeks 1-5 in `fact_game` (the Monday game's final score at 07:15
  UTC: end 04:15 + 3 h), their 13,488 plays and the week-5 player stats and snaps (Monday
  night's at 10:15 UTC); the injury reports of weeks 1-5 (1,310 rows, each available at its
  team's kickoff because 2025 has no `date_modified`); every daily depth-chart snapshot up to
  the 07:15 UTC pull that morning; all 272 regular-season `fact_schedule` rows (who plays whom
  in which week, public since 2025-05-20), with the date, time and venue of the 95 games of
  weeks 1-6 and week 7's Thursday game (kickoff within 12 days); `dim_team`; `dim_week` with
  game counts up to week 5; 17,423 players in `dim_player` (the 2025 draft class included).
- Not visible: week 6 of anything. Philadelphia at the Giants on Thursday 2025-10-09 is in
  `fact_schedule` (the fixture and its slot) but not in `fact_game` (no score yet); its injury
  reports appear at kickoff; the date/time of weeks 7-18 (a Week-18 slot is picked after week
  17); week 6's `dim_week.n_games`; the 13 playoff `fact_schedule` rows (matchups unknown);
  every 2025 `coach_team_season` row (no stint is over); any player's position (today's
  value); the closing line of any unplayed game.

### Known, accepted imperfections

- **Stat corrections.** nflverse reloads play-by-play and stats on Wednesday-Thursday with
  corrections; the cache holds the corrected values, so a historical backtest sees a slightly
  cleaner week than a Tuesday user did. A mild, unavoidable leak; live runs record the real
  arrival times (`pipeline_runs`, step E4).
- **The final schedule.** `fact_schedule` and `dim_week` are built from nflverse's FINAL
  schedule. Slots are masked until 12 days before kickoff (and Week 17/18 until the previous
  week), future weeks' `dim_week` counts until their own as-of, and 24 listed changes until
  their own kickoff; changes that are not listed (for example a game postponed within its
  week) still show their final week from the release on. Known, reduced, not removed.
- **Today's snapshots.** Positions, sizes and headshots come from nflverse's current player
  table; the as-of view hides them, but anything derived from them through `wh.` is hindsight.
  `dim_team.team_division` is today's alignment and team codes are today's franchise codes.
- **Model columns** (`epa`, `wp`, `cpoe` ...) come from models trained on later seasons
  (spec 6.3); `available_at` cannot fix that.

## Player IDs (`bridge_player_id`, B3)

**Why several id systems?** Every data provider numbers players its own way. nflverse's
play-by-play, stats, injuries and depth charts use the NFL's GSIS id (`00-00xxxxx`);
Pro-Football-Reference snap counts use a PFR slug (`pfr_player_id`, letters and digits); the
2025+ ESPN depth charts an ESPN number; FantasyPros rankings a FantasyPros number; Sleeper,
Yahoo, Sportradar and MyFantasyLeague (MFL) their own. The warehouse keeps **`gsis_id` as the
one canonical key** (spec 7.5) and a *bridge*: `bridge_player_id` has one row per
(`id_type`, `source_id`) naming the `gsis_id` it belongs to. To put a gsis_id on any source,
join its id there:

```sql
SELECT s.*, b.gsis_id
FROM some_source s
LEFT JOIN bridge_player_id b ON b.id_type = 'fantasypros' AND b.source_id = s.fantasypros_id
```

If your id column is numeric, compare on its canonical text
(`twm.ids.canonical_id_sql(column, its_type)`): a DOUBLE cast to VARCHAR keeps `.0` and
matches nothing.

`source_id` is the id as **canonical text** (`twm.ids.canonical_id`): integers never carry a
decimal part (`4374496`, never `4374496.0`; some sources store ids as numbers, others as
text), text is trimmed, an empty string is NULL. Id types (`twm.ids.ID_TYPES`): `pfr`, `espn`,
`sleeper`, `fantasypros`, `yahoo`, `sportradar`, `pff`, `nfl`, `mfl`, plus the cheap extras
`esb`, `smart`, `otc` (players), `rotowire`, `fantasy_data`, `cbs`, `stats`, `fleaflicker`,
`cfbref`, `rotoworld`, `ktc`, `swish`. Left out: `ff_playerids.stats_global_id` (0 is a
placeholder in 6,574 rows). `nfl` is the NFL's numeric id (players' `nfl_id` and the weekly
rosters' `gsis_it_id` agree on it); `ff_playerids.nfl_id` mixes it with an older NFL.com
numbering, so some players carry two `nfl` ids.

**Where links come from, most trusted first** (`method`):

1. `manual`: `data/manual/player_id_overrides.csv`, checked by a person (below);
2. `players`: nflverse's player table, one row per gsis_id, every id in it unique;
3. `rosters_weekly`: the weekly rosters of the built seasons (2002+);
4. `ff_playerids`: DynastyProcess's cross-site table, the only source of Sleeper-for-old-seasons,
   FantasyPros, MFL and several other ids. It comes last because where it and `players` both
   give an id for the same player they disagree now and then (8 PFR and 12 ESPN ids; the
   examples look like `ff_playerids` errors, e.g. one player given another player's PFR id);
5. the two name passes (below), only for `ff_playerids` rows that have no gsis_id.

**Disagreements and ambiguity.** For every (`id_type`, `source_id`) all sources propose a
gsis_id. `n_candidates` counts the distinct proposals (also those naming a player missing from
`dim_player`, listed as "(not in dim_player)"); when it is above 1 the sources disagree, the
most trusted one wins and `is_conflict` is true (70 ids on the full build, 19 of them only
because a candidate is missing from `dim_player`; every one listed in the report with all
candidates). A conflict row's `detail` starts "precedence picked <method>": precedence is a
rule, not a check, so the pick can still be wrong; when the usage check (next paragraph)
contradicts it, the detail says so instead. When the **winning source itself** names two players for one
id (a duplicate inside that source, or a weekly-roster id that belongs to one player in 2024 and
another in 2025) the id is **ambiguous**: it is left out of the bridge and listed in the report
(19 ids). A wrong link would silently merge two players, which is worse than a missing link that
the report shows. A link to a gsis_id that is not in `dim_player` is dropped too (5,126
candidate links from the weekly rosters and 779 from `ff_playerids`, counted in
`build_manifest.notes.n_links_to_gsis_not_in_dim_player`): nflverse's player table lacks 1,684
weekly-roster players (1,509 of them were cut at some point: camp and practice-squad players)
and 85 `ff_playerids` gsis ids.

**The usage check (`kind = 'suspect'`).** Precedence trusts `players` over everything, but
even `players` can hand an id to the wrong person (a PFR slug given to a 1980 player that the
2026 snap counts use for a 2026 rookie). So after the bridge is resolved, the build checks
every PFR link that `fact_snaps` uses against the linked player's career window
[`COALESCE(rookie_season, draft_year)` - 1, `last_season` + 1] (`twm.ids.stage_usage_checks`):

- **Career rule (hard).** A season outside the window cannot be that player. If the id is
  never used inside the window, the link is not `manual`, and every lower-ranked source that
  names someone else names the *same* player, whose window holds every season of use, the
  bridge is re-linked to him (`method` = `<source>_usage_override`, e.g.
  `rosters_weekly_usage_override`, `is_conflict` true). Otherwise the link stays, and the snap
  rows outside the window get `gsis_id` NULL. Either way a `suspect` row names the evidence
  (`dataset = 'fact_snaps'`). Full build: 1 id re-linked (2 rows), 1 id left NULL for its 6
  rows.
- **Position rule (report only).** The snap counts list the id only on the other side of the
  ball (offense / defense / special teams) than the player's `position_group`: reported as
  `suspect`, link kept. Position changes (edge rushers listed DE or LB, safeties at LB) make a
  finer rule noisy, and even this one catches real two-way players, so it never changes a
  link. Full build: 10 ids.

Rows left NULL, or moved to another player, are ignored by both players' `public_from_utc`
(it reads `fact_snaps.gsis_id` after the check). Owner action: confirm each `suspect` row and
add a `manual` override (below) once the player is verified from the data; a manual link is
never overridden by the check.

**The name passes.** 4,489 `ff_playerids` rows have no gsis_id, so their FantasyPros, Sleeper,
MFL ... ids would otherwise map to nobody. Names are compared after `normalize_name`: lowercase,
accents removed, punctuation dropped (apostrophes, periods and hyphens join the parts:
"D'Andre Swift" -> `dandre swift`, "A.J. Brown" -> `aj brown`, "Amon-Ra St. Brown" ->
`amonra st brown`), the suffixes jr/sr/ii/iii/iv/v dropped ("Odell Beckham Jr." -> `odell
beckham`), spaces collapsed.

- Pass A, `name_birthdate`: normalized name + exact birth date. The key must be unique among
  the gsis-less `ff_playerids` rows **and** among the players: two players with the same name
  and birthday are never guessed between.
- Pass B, `name_position_draft` (only for rows pass A did not link): normalized name + position
  group + draft year, unique on both sides, and the birth dates must not contradict (either one
  missing, or equal).
- Safety rules: a row whose own ids are linked by id (step 2-4) to a different player is not
  linked at all (`contradicts_id_link`, 43 ids); an id that already has an id-based link keeps
  it (`id_link_exists`, 1,687 ids, all agreeing); a player never gets a second id of a type he
  already carries (`player_has_other_id_of_type`, 31); two name links for one id, or one player
  named for two ids, cancel each other (reported as ambiguous). Pass B skips only the rows
  pass A linked, not their players, so one player can be linked by both passes through two
  rows (one with his birth date, one with his draft year); this last rule is what stops him
  getting two ids of a type.
- Result on the full build: the passes proposed 2,782 rows (A 2,523, B 259); after the safety
  rules 2,771 rows (10,645 ids: A 10,022, B 623) were linked. Every link is listed in the report
  (`kind = 'name_link'`, with the evidence) so the owner can spot-check them; a pass-A link
  whose `ff_playerids` position group differs from the player's says "position group differs:
  RB vs WR" in its evidence (64 rows; most are historical position changes, but check them
  first).

**`dim_player`'s ids.** `pfr_id`, `espn_id`, `sleeper_id`, `fantasypros_id`, `yahoo_id`,
`sportradar_id` and `mfl_id` come from the bridge: one id per type per player, the most trusted
method first, then the smallest id (deterministic). For `pfr_id`/`espn_id` that is nflverse's
own value whenever it has one (the `players` link, unless the usage check re-linked it); the
bridge filled 10 PFR (one of them the usage re-link) and 60 ESPN gaps from the weekly rosters,
`ff_playerids` and the name passes (which method: join the
id back to `bridge_player_id`). A player with several ids of one type (25 ids on the full build,
e.g. two ESPN ids) keeps one; the others are listed as `kind = 'multiple_ids'`. Coverage on the
full build: PFR 22,677, ESPN 16,628, MFL 10,695, Sportradar 7,188, Sleeper 6,151, Yahoo 5,339,
FantasyPros 4,765 of 24,833 players.

**The override file** `data/manual/player_id_overrides.csv` (committed; header only to start):

```csv
id_type,source_id,gsis_id,note,verified_by
fantasypros,12345,00-0000000,"why you are sure (a link, what you checked)",your name
```

A row forces that link with the highest precedence (`method = 'manual'`), which also resolves
an ambiguous id. The build validates the file and stops with `IdOverrideError` naming every bad
line: unknown `id_type`, empty `source_id`/`gsis_id`, a gsis_id that is not in `dim_player`,
or the same (`id_type`, `source_id`) twice (ids are compared as canonical text, so `12345` and
`12345.0` are the same key). Use it only for links you verified; take candidates from the
report (suspect links, ambiguous ids, conflicts, unmatched ids with a "known only as ..."
hint). A suspect row's PFR slug may belong to another person in `players` too, so check the
data (games, team, position) before adding it.

**Where the bridge is used.** `fact_snaps.gsis_id` (PFR id, after the usage check),
`fact_depth_chart` daily rows
without an upstream gsis_id (ESPN id, flagged `gsis_id_from_espn`; the key stays unique, checked
on every build) and `dim_player`'s ids. Point-in-time, a bridge row is visible only once its
player exists (`public_from_utc`, copied from `dim_player`), in `AsOfView` and in the leakage
harness alike (a future draft class's ids are deleted or scrambled like the players).

### The unmatched-id report (`twm ids`)

Every build refreshes two bookkeeping tables (kind *meta*): `report_id_coverage` (per dataset,
season, id type and scope: rows, unmatched rows, distinct ids, unmatched ids, `match_rate` =
share of rows that map, `id_match_rate` = share of distinct ids) and `report_id_unmatched`
(every unmatched id with a name, position, first/last season and row count, plus the
`suspect`, `ambiguous`, `conflict`, `name_link` and `multiple_ids` rows; an unmatched id the
bridge knows something about says so in `detail`). Snap rows the usage check left without a
gsis_id count as unmatched. Datasets: the warehouse's `fact_snaps` (PFR),
`fact_depth_chart[daily_no_gsis]` (daily rows without an upstream gsis_id: how many the ESPN id
rescues), `fact_player_week`, `fact_injury_report`, `fact_play` (passer/rusher/receiver; `gsis`
ids: is the id in `dim_player`?), and straight from the raw cache (read from the Parquet files,
never fetched; a dataset or season that is not cached is skipped and listed in
`notes.coverage_datasets_skipped`): FantasyPros rankings (`ff_rankings_all[rp]` and `[wp]` per
scrape year, `ff_rankings_week`, `ff_rankings_draft`; team defenses left out, and for
`ff_rankings_all` the IDP pages (`db`, `dl`, `lb`, `idp`), which sometimes list an offensive
player), `ff_opportunity`,
`pfr_pass/rush/rec`, `ngs_*`, `draft_picks` (gsis and PFR ids) and `combine` (PFR ids). Scope
`fantasy` = QB/RB/WR/TE rows of datasets that have a position.

`ff_rankings_all[rp_preseason_pool]` is the Waiver Radar candidate pool: per season 2020-2026,
the last August/September redraft cheat-sheet scrape before week 1's first game day, and per
position the players ranked inside `teams x starters x candidate_pool_multiplier` by ECR (QB 18,
RB 36, WR 36, TE 18 with `config/league.yaml`; ties included). Only a player's own positional
cheat sheet counts (`qb-cheatsheets`, `ppr-rb-cheatsheets` ...: the page's position must equal
the row's): FantasyPros sometimes lists a player on an IDP or another position's page, and his
rank there is not his WR rank.

`uv run twm ids` writes it as markdown to `reports/ids/unmatched_ids.md` (`--db`, `--out`; a
relative `--out` is under the project root) and prints a summary (with the suspect count); the
file is deterministic (sorted, the only time in it is the build's `built_at`), so two runs can be
diffed. It is gitignored because it changes with every data refresh; regenerate it any time. It starts with a headline table (snaps and weekly rankings match rates,
pool misses per season), then the suspect links, the pool misses, the top 10 unmatched
QB/RB/WR/TE ids per dataset, ambiguous ids, conflicts, extra ids, the name-pass links of
fantasy-site ids or QB/RB/WR/TE players (the SQL for all of them is in the file), and the full
coverage table last. `twm doctor` prints one `ids:` line.

Headline numbers on the full 1999-2026 build (2026-09-27 cache):

| Check | Result |
|---|---|
| Snap counts QB/RB/WR/TE, rows with a gsis_id | 99.75% (2024, the lowest) to 100% (2013, 2017, 2018); every season 2013-2026 >= 99.5% |
| Preseason `rp` candidate pool (108 players per season) without a gsis_id | 0 in every season 2020-2026 |
| Weekly `wp` rankings QB/RB/WR/TE, rows with a gsis_id | 2019 100%, 2020 99.95%, 2021 99.91%, 2022 99.82%, 2023 99.82%, 2024 99.50%, 2025 99.02%, 2026 97.24% (46 of 1,665 rows so far) |
| Redraft `rp` rankings QB/RB/WR/TE, distinct ids with a gsis_id | 2020 98.6% (12 of 836 missing), 2023 95.5% (44/979), 2026 90.6% (88/937) |
| Usage check on snap-count PFR links | 12 suspect ids: 1 re-linked, 1 left NULL (6 rows), 10 position-only (kept) |
| `ff_rankings_week` (current week) QB/RB/WR/TE | 466 of 472 ids |
| gsis ids in stats / injuries / plays / ngs / ff_opportunity | all in `dim_player` except 61 `fact_player_week` and 6 `fact_play` rows of 1999-2000 (a `0` placeholder id and five other 1999-2000 ids) |

The weekly-rankings target (99% of rows) is met through 2025 but not in 2026 (97.24%): the
missing rows are fringe players, mostly 2026 rookies, whose FantasyPros id is in no id table at
all, or whose `ff_playerids` row names a gsis_id that nflverse's player table does not have yet
(the report's `detail` says "known only as a player missing from dim_player"). They can be
linked through the override file once verified, and newer `players`/`ff_playerids` files close
most of them by themselves.

## Garbage time and neutral situations (`fact_play` flags, B5)

Late in a blowout, the losing team throws against soft coverage and the winning team runs out
the clock. Those stats say little about next week, so every play carries two flags, computed
from its own pre-snap state (so they cannot leak the future). Code: `src/twm/situations.py`;
thresholds: `config/settings.yaml` (`garbage_time`, `neutral`), per PROJECT_SPEC 7.3.

| Flag | TRUE when | Share of runs and passes |
|---|---|---|
| `is_garbage_time` | the offense's win probability is below 0.05 or above 0.95, **except** in the final 120 seconds of a half when the score is within 8 points | 16% (13.3%-17.4% per season) |
| `is_neutral` | win probability from 0.20 to 0.80 and **more than** 120 seconds left in the half | 55% (52.7%-58.1% per season) |

- The exception matters: a one-score game in its last two minutes is not decided, whatever the
  model says. It spares 3,373 runs and passes (1999-2026) that the win probability alone would
  call garbage.
- A play is never both. A play without a win probability (12 runs and passes in 28 seasons) is
  neither; the flags are never NULL, so `WHERE NOT is_garbage_time` keeps it.
- Overtime follows the same rule (its last 120 seconds, one-score game).
- The win probability is nflfastR's `wp`, a model output (Section 6.3 caveat). Step G1 trains our
  own walk-forward model; the flag can switch to it then.

Example: in 2025, 25% of Zach Charbonnet's rushing yards (752 on 190 carries) came in garbage
time, the most of any running back with 150+ carries. Query:

```sql
SELECT rusher_player_name, count(*) AS carries, sum(yards_gained) AS yards,
       sum(yards_gained) FILTER (WHERE is_garbage_time) / sum(yards_gained) AS garbage_share
FROM fact_play
WHERE season = 2025 AND play_type = 'run' AND rusher_player_id IS NOT NULL
GROUP BY 1 HAVING count(*) >= 150 ORDER BY garbage_share DESC
```

Tests: `tests/test_situations.py` (every boundary: 0.05/0.95 exactly, 120 s exactly, 8 points
exactly, overtime, missing inputs; SQL and polars agree; the stored flags equal the rule on all
1.29M real plays with `-m realdata`).

## Determinism and the manifest

Building twice from the same cache gives the same table content (per-table `content_hash` and
row sets) on any machine. The DuckDB file itself is *not* byte-identical between two builds (its
size differs by ~1 MB on full history), so never compare the files, compare the manifests:

- every column has an explicit type in `schema.py`, so the set of seasons cannot change a dtype
  (upstream files drift between Int32 and Float64 across years);
- every timestamp is stored as a plain `TIMESTAMP` holding UTC (never `TIMESTAMP WITH TIME ZONE`,
  asserted), and every connection sets `TimeZone='UTC'`, so the laptop's zone never leaks in;
- every table is created with `ORDER BY` its key; nothing in table content uses `now()` or random;
- `build_manifest.content_hash` is an md5 (a 32-character fingerprint of a text; any change to
  the text changes it) over the sorted per-row md5s of each row rendered as text, computed
  inside DuckDB. Doubles are rendered in DuckDB's shortest round-trip form, so any change to a
  stored value changes the hash. The text rendering belongs to DuckDB, so compare hashes only
  between builds with the same `duckdb_version` (recorded in `build_manifest`). The hash is
  order-free (sorting the row fingerprints first means the physical row order does not matter):
  the same rows in a different physical order in the Parquet file hash identically (tested,
  including the injury dedupe tie-break).
- a floating-point source column cast to INTEGER (58 play-by-play columns, injuries
  `season`/`week`) stops the build on a non-integral value instead of rounding silently; every
  such column was verified integral over 1999-2026, so the guard only fires on an upstream change.
  `build_manifest` also records `n_rows`, `seasons` (`[]` for `dim_team`/`dim_player`),
  `seasons_skipped`, `n_source_rows`, `n_dropped_null_key`, `n_dropped_duplicates`, `notes`,
  `built_at` (the only non-deterministic value) and the `twm`, `nflreadpy`, `duckdb` and `polars`
  versions. `notes.null_columns` lists the spec columns absent from every source file read (and
  therefore NULL) for every source-backed table except `fact_depth_chart`, whose two formats
  are unioned by hand; other notes: `n_kickoff_estimated` (`fact_game`), `merged_spellings`
  (`dim_coach`), `n_team_seasons_multi_coach` (`coach_team_season`), `n_unit_other`
  (`fact_depth_chart`), and the B2 availability notes: `available_at_rule_counts` (how many
  rows each rule branch placed, zeros included), `n_available_after_week_asof` (rows not yet
  public at their own week's Tuesday as-of), `n_raised_to_after_previous_week_asof`
  (`fact_injury_report`), `n_schedule_exceptions`, `n_slot_last_two_reg_weeks`,
  `cancelled_games` and `schedule_exceptions_not_found` (`fact_schedule`) and
  `public_from_utc_rule_counts` (`dim_player`, `bridge_player_id`). B3 adds, on
  `bridge_player_id`: `n_links_by_method`, `n_links_by_id_type`, `n_conflicts`, `n_ambiguous`,
  `n_links_to_gsis_not_in_dim_player`, `name_pass_rows_linked`, `name_pass_links_rejected`,
  `n_manual_overrides`; on `dim_player`: `n_players_with_id`, `n_players_with_several_ids`,
  `n_pfr_id_not_from_players`, `n_espn_id_not_from_players`; `n_rows_without_gsis_id`
  (`fact_snaps`); `n_daily_gsis_filled_from_espn`, `n_daily_espn_without_gsis`
  (`fact_depth_chart`); the headline match rates, `preseason_pool_unmatched`,
  `preseason_pool_cutoffs` and `coverage_datasets_skipped` (`report_id_coverage`); and
  `n_rows_by_kind` (`report_id_unmatched`).

The whole build runs in one transaction inside the scratch file `<warehouse>.building`; only a
committed build is renamed over the real file (atomic on the same filesystem). A key violation or
any other error rolls it back, removes the scratch file and leaves the previous warehouse
untouched; leftovers of an interrupted build (`.building`, `.building.wal`) are removed before the
next build starts. Building into a fresh scratch file also keeps the file compact: rewriting
tables inside an existing file would keep the old copies until COMMIT and double the size.
Two more guards: concurrent builds of the same target are refused through an advisory lock on
`<warehouse>.build.lock` (`BuildInProgressError`; the lock file stays behind and is harmless), and
the warehouse file is written only by `twm build`, so any `<warehouse>.wal` (DuckDB's
write-ahead log, left by a notebook that opened the file read-write and died before
checkpointing) is discarded with a warning before the rename; DuckDB would otherwise replay it
into the new file. `twm.warehouse.connect()` opens read-only by default for that reason.

## Decisions made in B1-B3 (confirm or revisit)

1. **Weekly as-of anchored to the first kickoff's date**, not to the last game's estimated end
   (the design brief's wording). Consequence: in the five split weeks (2010 W16, 2020 W5/W12/W13,
   2021 W15) the moved Tuesday/Wednesday game is *not* available at that week's as-of. The
   alternative (first Tuesday 14:00 after the last game) would put the as-of after the next
   week's kickoff in those weeks.
2. **`fact_depth_chart.week_at_dt` is a window label for grouping and inspection only**; B2 uses
   `dt` as the daily charts' `available_at`, and maps the orphan legacy REG week-18/19 charts to
   the Wild Card week (see `fact_depth_chart` above).
3. **`dim_coach.coach_id` is a slug of the name** (`mike_mccarthy`): two different coaches with
   the same spelling would merge, and an upstream spelling fix changes an ID. Stable only while
   nflverse spelling is stable; H1 may replace it with a manual coach table. Likewise team codes
   follow play-by-play (LA/LAC/LV) with raw codes kept only where the source differed
   (`schema.TEAM_ALIASES`), a policy the B3 ID map and any external join must follow.
4. **B2: `fact_game` is available as a whole at game end** and a separate `fact_schedule` holds
   the pre-game columns (the alternative, one row with per-column times, would make every reader
   choose columns by time). `roof` is left out of `fact_schedule` (open/closed is decided on game
   day for retractable roofs).
5. **B2: injury rows are never available before the previous week's as-of has passed**, even
   when `date_modified` is earlier (125 rows of 2010-2024 on the full build), to honour spec
   6.1's "week N+1 reports are not available at the Tuesday as-of".
6. **B2: a coaching stint that ended with a change becomes available at the next coach's first
   kickoff**, not at the stint's last game, so a stint row cannot announce a firing before it is
   public.
7. **B2: a daily depth-chart row without `dt` stops the build** (B1 kept it with a NULL `dt`):
   a row that cannot be placed in time cannot be used by any point-in-time reader.
8. **B2 review: point-in-time goes beyond rows.** `dim_player` got a row rule (draft or first
   data row), `fact_schedule` a second time column for its volatile slot columns plus a
   curated list of later schedule changes, `dim_week` masks a future week's schedule counts
   until the week's own as-of (the review proposed "until the week's window starts"; the own
   as-of is stricter, because at week 4's as-of week 5's window has just started and its
   split flag would reveal a postponement announced days later), and today's-snapshot columns
   (positions, sizes) are hidden. `window_end_utc` stays visible: it equals `asof_weekly_utc`.
9. **B2 review: a 3 h result lag** on `fact_game` (and the playoff matchups and coaching
   stints that follow from a result), the 21:00 ET Monday-night slot for guessed kickoffs, and
   a 9 h offset for the 2010-2020 injury stamps. The 6 h game-data lag is unchanged; the build
   now refuses a lag that would drop rows from an official snapshot.
10. **B3: `players` > weekly rosters > `ff_playerids` > name passes**, with a manual file on top;
    an id the winning source maps to two players is left out rather than guessed, and links to
    a gsis_id that nflverse's player table lacks are dropped (so `bridge_player_id.gsis_id` is
    always a `dim_player` row). The weekly rosters are read for the built seasons only (like
    every per-season input), so a 2025-2026 build has fewer roster links than a full one.
11. **B3: the id report reads the raw cache directly** for datasets the warehouse does not hold
    (rankings, NGS, PFR advanced stats, draft picks, combine): a skipped dataset is noted, never
    downloaded. Rankings are grouped by the scrape's calendar year.
12. **B3: `dim_player.public_from_utc` now reads snap counts through `fact_snaps.gsis_id`** (the
    bridge) instead of `dim_player.pfr_id`, and daily depth-chart rows filled from ESPN ids count
    as data rows: 1 more player exists point-in-time (6,924 never visible instead of 6,925).
13. **B3: the usage check can overrule precedence for snap counts**: a PFR link whose player's
    career does not fit the seasons the id is used in is re-linked (only when the other sources
    agree on one player whose career fits) or left NULL for those rows, and reported as
    `suspect`. A position on the other side of the ball is only reported.
