# The warehouse (Step B1)

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
functions), `build.py` (the builder). Tests: `tests/test_weeks.py`, `tests/test_warehouse_build.py`,
`tests/test_cli_build.py` (offline, tiny synthetic fixtures in `tests/conftest.py`), plus two
opt-in `realdata` tests in `tests/test_warehouse_build.py` that read the real cache
(`uv run pytest -m realdata`, never download).

## Tables

"PK" is the primary key: the columns that identify one row. Uniqueness is checked after every build
and a violation stops the build with `PrimaryKeyError` naming the table, the key and example rows.

| Table | One row is... | PK | Source |
|---|---|---|---|
| `fact_game` | one scheduled game, played or not (`result` NULL until played), with `kickoff_utc`, `game_end_utc_est` (= kickoff + 4 h, an estimate), `home_implied_total`/`away_implied_total` from the closing line (the betting market's expected margin and combined score right before kickoff; a team's implied total is the points the market expects it to score), `season_type` | `game_id` | schedules |
| `fact_play` | one play, 128 curated columns (identifiers, game state, play descriptors, main players, nflverse model columns, context). All 128 names exist in `data/schemas/pbp.json`; nothing was dropped | `game_id, play_id` | pbp |
| `fact_player_week` | one player in one game week, every weekly stat column | `player_id, season, week, season_type` | player_stats |
| `fact_team_week` | one team in one game week | `team, season, week, season_type` | team_stats |
| `fact_snaps` | one player in one game with snap counts and percentages (PFR ids; mapped to `gsis_id` in B3) | `game_id, pfr_player_id` | snap_counts |
| `fact_injury_report` | one player on one team's injury report for one week; `date_modified` is the report row's last-modified time where the source has it (2010-2024; NULL for 2025+ and effectively 2009, see assumptions section 4) | `season, week, team, gsis_id` | injuries |
| `fact_depth_chart` | one depth-chart slot, from either upstream format (`source_format` = `legacy` weekly charts 2001-2024, or `daily` snapshots 2025+) | see below | depth_charts |
| `dim_team` | one of the 36 nflverse team rows; `current_abbr` maps OAK→LV, SD→LAC, STL→LA, LAR→LA; `is_current` is false for those four | `team_abbr` | teams |
| `dim_player` | one player (skeleton; B3 adds the cross-source id map). `dim_team` and `dim_player` are snapshots of the one-file datasets (`all.parquet`): they do not depend on the seasons built (manifest `seasons` = `[]`) and change whenever `twm ingest` refreshes them | `gsis_id` | players |
| `dim_coach` | one head coach; `coach_id` is a slug (`mike_mccarthy`) and spellings that share a slug are merged into one row (`coach_name` = whitespace collapsed, alphabetically first spelling; merges listed in `build_manifest.notes.merged_spellings`). Interim coaches the schedule never names (2024+) are absent | `coach_id` | fact_game |
| `coach_game` | one team in one game and the head coach the schedule lists for it (unplayed games included). The schedule columns record mid-season changes through 2023 but not in 2024-2025 (TEN/NYG 2025, NYJ/CHI/NO 2024 show the fired coach all season), so interim coaches are missing there | `game_id, team` | fact_game |
| `coach_team_season` | one coach-team-season as listed in the schedule: first/last scheduled week and `n_games` = games scheduled (played or not, playoffs included; 17 already for every 2026 coach). Schedule-derived, not point-in-time: use `coach_game` with the B2 as-of filter for games coached to date. A team has more than one row only when nflverse recorded the change (2000-2023) | `coach_id, team, season` | coach_game |
| `dim_week` | one (season, week, season_type) that appears in the schedule, with the official as-of timestamps | `season, week, season_type` | fact_game |
| `build_manifest` | one built table: rows, content hash, seasons, dropped-row counts, versions | `table_name` | the build |

Hot-Seat firing labels (H1/H2) must therefore combine the schedule's coach changes (reliable
2000-2023) with an owner-verified manual file (`data/manual/coach_departures.csv`, to be created
in the Hot-Seat step) for 2024 onward; `build_manifest.notes.n_team_seasons_multi_coach` on
`coach_team_season` shows how many team-seasons the schedule itself splits. Evidence per season
is in `docs/assumptions.md` section 10.

A `fact_game` row mixes three availabilities, which B2 must keep apart: the fixture itself is
known months ahead; `spread_line`, `total_line` and the implied totals are *closing* lines, so
B2 assigns them `available_at = kickoff_utc` (using an upcoming game's closing line at a
Tuesday as-of is leakage, spec 6.4); `result`, `total` and the scores become available at game
end (`game_end_utc_est`).

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
`dt` (daily snapshot time, UTC), `team`, `team_raw`, `gsis_id`, `espn_id`, `player_name`,
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
`week` (2001, 2007-2013 and 2015-2024). B2 maps them by `game_type` first, `week` second, and must decide
whether the post-finale REG charts belong to the Wild Card window or are dropped. `week_at_dt`
(daily rows) is a window label for grouping and inspection only; B2's `available_at` for daily
charts is `dt` itself (assumptions section 12).

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
  leaks; B2 should give flagged rows a conservative `available_at` (e.g. 04:30 UTC of the day
  after `gameday`) rather than trust `game_end_utc_est`.

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
  (`fact_depth_chart`).

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

## Decisions made in B1 (confirm or revisit)

1. **Weekly as-of anchored to the first kickoff's date**, not to the last game's estimated end
   (the design brief's wording). Consequence: in the five split weeks (2010 W16, 2020 W5/W12/W13,
   2021 W15) the moved Tuesday/Wednesday game is *not* available at that week's as-of. The
   alternative (first Tuesday 14:00 after the last game) would put the as-of after the next
   week's kickoff in those weeks.
2. **`fact_depth_chart.week_at_dt` is a window label for grouping and inspection only**; B2 uses
   `dt` as the daily charts' `available_at`. How the orphan legacy REG week-18/19 charts map to a
   window is B2's call (see `fact_depth_chart` above).
3. **`dim_coach.coach_id` is a slug of the name** (`mike_mccarthy`): two different coaches with
   the same spelling would merge, and an upstream spelling fix changes an ID. Stable only while
   nflverse spelling is stable; H1 may replace it with a manual coach table. Likewise team codes
   follow play-by-play (LA/LAC/LV) with raw codes kept only where the source differed
   (`schema.TEAM_ALIASES`), a policy the B3 ID map and any external join must follow.
