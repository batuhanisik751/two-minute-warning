# Data assumptions (Step A3, verified against real downloads)

**Verified on:** 2026-09-26 (Saturday of NFL Week 3; only the Thursday game of Week 3 had been played).
**Tooling:** `nflreadpy` 0.1.5, Python 3.13. Re-run with `uv run python scripts/verify_sources.py`
(cache-only by default; `--allow-download` probes seasons missing from `data/raw`; writes
`reports/verify_sources.json`). Column snapshots live in `data/schemas/*.json` and are checked on
every current-season or one-file load, download or cache read (`SchemaDriftError` if a column
disappears; a logged warning if a column changes dtype or is added). Snapshots are refreshed
deliberately with `uv run python scripts/refresh_snapshots.py`, never by `twm ingest`.

Everything below was observed in the data, not taken from docs. Where the spec (Section 4.1)
said something different, the spec is wrong and this file wins.

---

## 1. Loaders: names, coverage, current-season status

All 20 loader functions named in the spec exist in `nflreadpy` with exactly those names, plus
`load_team_stats` (used), `load_stats`, `load_ffverse`, `load_officials`, `load_trades` (unused). Our dataset registry is in
`src/twm/sources/nflverse.py` (`DATASETS`).

| Our name | nflreadpy call | Spec said | **Verified first season** | 2026 status on 2026-09-26 | Notes |
|---|---|---|---|---|---|
| `pbp` | `load_pbp(season)` | 1999+ | **1999** (1998 rejected) | weeks 1–3 (33 games) | 372 columns |
| `player_stats` | `load_player_stats(season, summary_level="week")` | 1999+ | **1999** | weeks 1–3 | 150 columns; REG+POST rows, `season_type` column |
| `team_stats` | `load_team_stats(season, summary_level="week")` | not in spec | 1999 | weeks 1–3 | team-week EPA etc.; handy for context features |
| `schedules` | `load_schedules(season)` | 1999+ | **1999** (1998 returns 0 rows) | 272 games, 33 with results | 46 columns |
| `snap_counts` | `load_snap_counts(season)` | **2012+** | **2013** — the 2012 file exists but has **0 rows** | weeks 1–3 | 16 columns; PFR IDs |
| `injuries` | `load_injuries(season)` | 2009+ | **2009** | weeks 1–3 | `date_modified` UTC report timestamp 2010–2024; 2009 ~all null; absent 2025+ (see §4) |
| `depth_charts` | `load_depth_charts(season)` | 2001+ | **2001** | 200 league-wide snapshots since 2026-03-22 | **two formats** (see §3) |
| `rosters` | `load_rosters(season)` | 2002+ (weekly) | 1999 (1998 also loads) | has `week` 1–3 in 2026 | now looks like a rolling weekly extract; use `rosters_weekly` |
| `rosters_weekly` | `load_rosters_weekly(season)` | 2002+ | **2002** | weeks 1–3 | 36 columns incl. `pfr_id`, `espn_id`, `status`, `draft_number` |
| `ngs_passing/receiving/rushing` | `load_nextgen_stats(season, stat_type=…)` | 2016+ | **2016** | weeks 0–3 | **week 0 = season-to-date aggregate**; drop it for weekly work |
| `pfr_pass/rush/rec` | `load_pfr_advstats(season, stat_type=…, summary_level="week")` | 2018+ | **2018** | weeks 1–2 only | lags a few days behind pbp |
| `ftn_charting` | `load_ftn_charting(season)` | 2022+ | **2022** | weeks 1–3 | license: check before public use |
| `participation` | `load_participation(season)` | 2016+, post-season only | **2016**; 2026 file does not exist | n/a | confirmed: never available in-season |
| `ff_opportunity` | `load_ff_opportunity(season, stat_type="weekly")` | 2006+ (verify) | **2006** | weeks 1–3 (Thursday game already in) | **updates in season within ~2 days** |
| `ff_opportunity_pass/rush` | `…stat_type="pbp_pass"/"pbp_rush"` | 2006+ | **2006** | weeks 1–3 | per-play expected values |
| `draft_picks` | `load_draft_picks(season)` | 1980+ | **1980** (1979 → 0 rows) | 2026 draft present | |
| `combine` | `load_combine(season)` | 2000+ | **2000** (1999 → 0 rows) | 2026 present | |
| `ff_playerids` | `load_ff_playerids()` | current | one file | 12,508 rows | `gsis_id` 8,019, `espn_id` 8,177, `pfr_id` 9,645 non-null |
| `ff_rankings_draft/week/all` | `load_ff_rankings(type=…)` | varies | **archive starts 2019-12-27** | draft scraped 2026-09-25, week 2026-09-26 | see §6 — this changes the Waiver Radar plan |
| `contracts` | `load_contracts()` | varies | one file, `year_signed` 0..2026 (0 = unknown) | 52,959 rows | |
| `players` | `load_players()` | — | one file | 24,833 rows | has `draft_round`, `rookie_season`, `last_season` |
| `teams` | `load_teams()` | — | one file, 36 rows | | logo URL columns exist; **not used** in the public app |

Coverage was verified by loading the claimed first season and the year before it. For most
datasets `nflreadpy` raises `ValueError: Season must be between …` for the year before (a range
check inside nflreadpy, not evidence about the files). `player_stats` and `team_stats` have no
range check and their 1998 file simply does not exist upstream (HTTP 404). `schedules`,
`draft_picks` and `combine` filter one combined file and return 0 rows. `load_rosters` accepts
1920+ and 1998 loads (1,964 rows). The 0-row probe files are not kept in the cache (§10).

## 2. Column names that differ from the spec

| Spec / assumed name | Real name | Dataset |
|---|---|---|
| `interceptions` | `passing_interceptions` | player_stats |
| `recent_team` | `team` | player_stats |
| fumbles lost | `sack_fumbles_lost` + `rushing_fumbles_lost` + `receiving_fumbles_lost` (also `fumbles_lost_total`) | player_stats |
| `receive_2h_ko` | **does not exist** in the public pbp; must be derived from second-half kickoff (P2 WP model) | pbp |
| `team` | `posteam` | ff_opportunity weekly |
| per-play expected columns | pass: `pass_completion_exp`, `yards_after_catch_exp`, `yardline_exp`, `pass_touchdown_exp`, `pass_first_down_exp`, `pass_interception_exp`, `two_point_conv_exp`; rush: `rush_yards_exp`, `rushing_yards_exp`, `rush_touchdown_exp`, `rushing_td_exp`, `rushing_fd_exp` | ff_opportunity_pass / _rush |
| `fantasypros_id`, `player_name`, `rank` | `id` (string), `player`, no rank (use `ecr`) | ff_rankings_draft / _all |
| `page_type`, `id` | `page`, `page_pos`, `fantasypros_id` (int) | ff_rankings_week |
| `date_modified` | present 2009–2024: real UTC timestamps on essentially every row 2010–2024, one junk stamp in 2009 (§4); absent from 2025/2026 | injuries |
| `season` | absent (one file for all teams) | teams |
| `cols` (nflreadr's nested contract-details column) | `season_history`, `contract_history` | contracts |
| `espn_id` as an integer everywhere | Int64 in `ff_playerids`; a **string** in `rosters_weekly`, `rosters`, `players`, `depth_charts`. Cast one side before the ESPN join (spec 7.5) | ff_playerids vs rosters |

Everything else the spec relies on exists under the expected name (full lists in
`data/schemas/`).

## 3. Depth charts: two formats

- **2001–2024 (legacy):** one row per team × week × slot. Columns: `season`, `club_code`,
  `week` (1–22 incl. playoffs), `game_type`, `depth_team` ("1","2","3" = string rank),
  `formation` (Offense/Defense/Special Teams), `position`, `depth_position`, `gsis_id`, names.
  ~59 rows per team-week. Snapshot: `data/schemas/depth_charts_legacy.json`.
- **2025+ (new):** one row per **league-wide pull** (`dt`) × team × slot. Columns (12): `dt`
  (ISO-8601 UTC string, e.g. `2026-09-26T12:12:29Z`), `team`, `player_name`, `espn_id`,
  `gsis_id`, `pos_grp_id`, `pos_grp` ("3WR 1TE", "Base 4-3 D", "Base 3-4 D", "Special Teams"),
  `pos_id`, `pos_name`, `pos_abb`, `pos_slot`, `pos_rank`. **No `season` or `week` column**;
  the season is the file you loaded, and the 2025 file's pulls run from 2025-08-03 to
  2026-03-14 (the offseason after the season belongs to the earlier season's file).
  **Cadence (verified):** every `dt` value covers all 32 teams; it is roughly one pull per day
  but not exactly: 2026 has 200 distinct `dt` over 188 distinct dates (352 team-days with two
  pulls) and 2025 has 221 over 219 dates (64 team-days with two pulls). Rows per pull:
  2,169–3,289 in 2026 (median 3,117, i.e. ~97 slots per team), 2,095–3,264 in 2025 (median
  2,350, ~73 per team). 567,492 rows in 2026 so far, 554,215 in 2025.
- **Point-in-time rule:** legacy rows for week N are treated as available at the start of week
  N (so at the Tuesday as-of after week N we use week N's chart); new rows use `dt ≤ as_of`
  directly, and the chart in force is the **latest `dt ≤ as_of` per team**. Dedupe on
  `(team, dt)`, never on `(team, date)`, because some days have two pulls.
- **Snapshots:** `depth_charts.json` is the 2025+ schema; `depth_charts_legacy.json` is written
  from the 2024 file by `scripts/refresh_snapshots.py`. Loads of 2001–2024 are not drift-checked
  (historical seasons never are).

## 4. Injury reports carry a report timestamp for 2010–2024 (`date_modified`)

Injury rows (`season`, `week`, `team`, `gsis_id`, `report_status`, `practice_status`,
`report_primary_injury`, …) have a `date_modified` column, a **UTC datetime**
(`Datetime(us, UTC)`), in the 2009–2024 files; it is **absent from 2025 and 2026**. Verified
on the cache:

- **2010–2024: populated on essentially every row** (0 nulls in 2011–2024; 62 nulls of 4,491
  rows in 2010). It is the row's last-modified time: injury reports are updated after each
  practice, so a team-week has several distinct stamps (median 6 per team-week, 10th–90th
  percentile 3–10, max 23).
- **When:** the stamps fall inside the game week. Over the 79,801 stamped rows of 2010–2024,
  81.4% are Fridays, 99.5% are Wed–Sat, and Mon/Tue/Sun together are 0.5% (e.g. 2024 week 1
  spans 09-04 12:55Z to 09-07 20:56Z, Wednesday–Saturday). None is more than 6 days before
  the week's first kickoff, and only **16 rows** (2013: 1, 2015: 1, 2018: 1, 2020: 13) are
  after the week's last game (kickoff + 4 h). The earliest stamp of each season is Sep 3–9
  (week 1's Tuesday or Wednesday in every season except 2018, which starts on Monday 09-03,
  Labor Day).
- **2009:** 4,804 of 4,821 rows are null and the 17 non-null values are one junk stamp
  (`2010-01-01 09:23:14 UTC`); treat the whole season as null.
- `report_status` values across eras: `Out`, `Doubtful`, `Questionable` and null in every
  season 2009–2026; **`Probable`** in 2009–2015 only (2,231 / 1,874 / 2,251 / 2,963 / 2,772 /
  2,607 / 2,702 rows per season; the NFL dropped it in 2016); **`Note`** in 2024 only (6 rows).
  Suggested mapping: `Probable` → active (expected to play); `Note` → null.
- 2009–2020 store `season` and `week` as **Float64**; 2021+ as Int32 (see §10).
- `season_type` exists upstream only in 2025–2026; the warehouse derives it from `game_type`
  for every season. The 2024 file has **2 duplicated player-weeks** (week 15, NYJ `00-0034270`
  and HOU `00-0039359`; no other season has any); the warehouse keeps `date_modified` as a
  naive UTC `TIMESTAMP` in `fact_injury_report` and resolves those with "latest stamp wins".

**Availability rule (B2):** for 2010–2024 use the observed `date_modified` as `available_at`
(the moment the row reached its final weekly state, before the games). For 2009, the 62 null
rows of 2010 and 2025+, **assume** Friday 23:59 UTC of the report week (the last practice
report, before the week's games). Either way, at the Tuesday as-of after week N the week-N
report is fully usable and the week-N+1 report is **not** (it publishes Wed–Fri). Live:
nflverse refreshes injuries daily at 07:00 UTC, and the pipeline records the real arrival time.

## 5. Snap counts use PFR IDs; the join works

`pfr_player_id` is a string like `BankKe01`. Join to `gsis_id` via `ff_playerids.pfr_id`:

| Season | QB/RB/WR/TE snap rows | matched via `ff_playerids` | matched via `rosters_weekly.pfr_id` |
|---|---|---|---|
| 2025 | 7,126 | 99.7% | 99.6% |
| 2013 | 6,389 | 99.3% | 69.1% |

Use `ff_playerids` first, `rosters_weekly` as fallback, and report the unmatched remainder
(spec 7.5). `ff_playerids` ID dtypes: `gsis_id`/`pfr_id` strings; `espn_id`, `sleeper_id`,
`fantasypros_id` Int64. The roster-side `espn_id` (`rosters_weekly`, `rosters`, `players`,
`depth_charts`) is a **string**, so cast one side before the ESPN join.

## 6. FantasyPros rankings archive starts December 2019

`load_ff_rankings(type="all")` has 1,845,918 rows, 366 scrape dates from **2019-12-27** to
2026-09-25. All 14 `ecr_type` codes (rows in the archive): redraft `ro` overall 229,402 /
`rp` positional 332,979 / `rsf` superflex 100,759; dynasty `do` overall 243,827 / `dp`
positional 387,751 / `dsf` superflex 151,134 / `drk` rookies 33,045 (plus `dr` 123 rows and
`rookies` 3,563 rows, both early labels for the same rookie page, last seen 2020-10-16 and
2020-10-12); weekly `wo` overall 17,901 / `wp` positional 86,701 / `wsf` superflex 45,672;
best-ball `bo` overall 95,327 / `bp` positional 117,734 (from 2021-01-01). Weekly PPR pages
(`weekly-rb`, `weekly-wr`, `weekly-te`, `weekly-qb`) exist from the 2020 season.

Preseason redraft positional (`rp`) scrapes in Aug/Sep, usable as "preseason ECR" for the
candidate-pool proxy: 2020: 8, 2021: 9, 2022: 9, 2023: 11, 2024: 7, 2025: 9, 2026: 8.
Weekly (`wp`) in-season scrapes per season: 15–20 (roughly one per week).

**Consequence for the spec (Waiver Radar, 8.1):** the "likely available" proxy needs a
preseason ranking per season. It exists for **2020–2026 only**. For 2013–2019 backtests we
need a fallback (recommended: prior-season PPG rank plus draft capital for rookies, with the
same 1.5×starters cutoff). Decision to be presented at Step C1. The ID join for rankings:
`id` in `ff_rankings_all` is a FantasyPros ID stored as a string; 3,352 of 4,019 `rp` IDs match
`ff_playerids.fantasypros_id` (the rest are mostly IDP/K/DST or long-retired players).

## 7. `spread_line` sign: positive = home team favored

Using schedules for played games (`result` = `home_score − away_score`, verified exact; `total`
= `home_score + away_score`, verified exact):

| Season | games | corr(spread_line, result) | mean result when spread_line > 0 | home favored by moneyline when spread_line > 0 |
|---|---|---|---|---|
| 2024 | 285 | +0.487 | +6.86 | 99.4% |
| 2025 | 285 | +0.506 | +6.93 | 99.4% |

So `spread_line` is the **home team's expected margin**. Implied totals (spec 7.4):
home = (`total_line` + `spread_line`) / 2, away = (`total_line` − `spread_line`) / 2.

## 8. `fantasy_points_ppr` in player_stats

Recomputing full PPR from components on 2025 QB/RB/WR/TE weeks (6,321 rows) matches
`fantasy_points_ppr` within 0.01 on 99.7% of rows (6,301 of 6,321; REG+POST). All 20 mismatches
are exactly +6.0 and are players with `special_teams_tds = 1` (return touchdowns), which
nflverse counts. `special_teams_tds × 6` reconciles every row exactly (**0 remaining**), so
`fantasy_points_ppr` = components + 6 × `special_teams_tds`, with fumbles taken as
`sack_fumbles_lost + rushing_fumbles_lost + receiving_fumbles_lost`. Our scoring engine will
compute from components (spec 7.1) and include a configurable `special_teams_td` line
(default 6, matching ESPN standard scoring).

Note for B4: `fumbles_lost_total` differs from that three-column sum on **31 of 6,321** rows in
2025 (return-game fumbles by returners, e.g. DJ Moore week 1 = 1 vs 0; Riley Leonard week 18
= 2 vs 1). nflverse's `fantasy_points_ppr` does **not** deduct those. B4 must choose which
column the `misc.fumbles_lost` line in `config/scoring.yaml` reads — `fumbles_lost_total`
(every lost fumble, probably what a league's "fumbles lost" setting means; verify against the
ESPN league settings when connected) or the three-column sum (matches nflverse exactly) — and
document the choice. The cross-check against `fantasy_points_ppr` must use the three-column
sum either way.

## 9. `ff_opportunity` details

- `player_id` is a `gsis_id`; positions include all positions (filter to QB/RB/WR/TE).
- Weekly file has 159 columns: actual, `_exp` and `_diff` per component (`pass_`, `rec_`,
  `rush_` × completions/receptions, yards, touchdowns, two-point conversions, first downs,
  interceptions) plus `total_fantasy_points`, `total_fantasy_points_exp`, `*_diff`.
- The fantasy-point columns use ffopportunity's own default scoring; we will **re-score the
  expected components** with `config/scoring.yaml` (spec 8.2) rather than use them directly.
- **Update timing:** the 2026 file already contained 23 rows for Week 3 two days after the
  Thursday game, so it is refreshed in season on roughly the pbp schedule. Live lag to be
  logged each Tuesday.
- Section 6.3 caveat applies (model trained across seasons).

## 10. Other observations

- **pbp `game_date`** is a string date; **schedules `gameday`/`gametime`** are strings
  (`2026-09-10`, `20:20`), kickoff in US Eastern time per nflverse convention. `available_at`
  derivation (B2) will convert to UTC. **1999 has no `gametime` at all** (259 games), and
  **2000-2005 record all but one Monday-night game (2005_02_NYG_NO, the relocated Katrina game,
  has a real `19:30`), two 2001 Saturday games and one Thursday game each in 2003-2005 as
  `09:00`** (102 rows, 17 per season): a 12-hour-clock placeholder for the true
  21:00/20:30 ET kickoffs (verified 2026-09-26; no other season has a `gametime` before `09:30`,
  the London slot that first appears in 2014). The warehouse treats any `gametime` before 09:30
  as missing and flags the row (`fact_game.kickoff_is_estimated`).
- **Upstream gaps that are not build bugs:** three played games have no play-by-play rows
  (`1999_01_BAL_STL`, `2000_03_SD_KC`, `2000_06_BUF_MIA`); the 2013 injury file has no Super
  Bowl rows and the 2023 one only REG and WC; the 2005 legacy depth-chart file is REG only.
- **pbp** has `home_coach`/`away_coach` on every play (same as schedules), all WP variants
  (`wp`, `home_wp`, `vegas_wp`, `wpa`, `vegas_wpa`, …), `xpass`, `cpoe`, `xyac_*`.
- **NGS** weekly rows include `week = 0` season aggregates and POST rows; filter both.
- **`load_rosters`** in 2026 returns rows with `week` 1–3 (440 / 14 / 2,545 rows), i.e. it is a
  latest-week extract, not a season roster. Use `rosters_weekly` as the roster fact table.
- **`rosters_weekly.status`** values: `ACT`, `INA`, `RES` (reserve/IR), `CUT`, `DEV`, `EXE`, `RET`.
- **Depth-chart `dt`** and rankings `scrape_date` are strings; parse at warehouse build.
- **PFR advanced stats** lag pbp by a few days (Week 2 only while pbp had Week 3's Thursday
  game); safe by the Tuesday as-of, but treated as optional features anyway.
- **Participation** is confirmed unavailable in season; offseason modules only.
- **Raw cache:** the full 1999–2026 history of all 29 datasets is 443 Parquet files, ~500 MB
  (`du`, 2026-09-26), far less than the "few GB" the spec expected. nflreadpy's own cache is
  **off** (`update_config(cache_mode="off")`): spec 4.1 says to enable it, but the wrapper's
  Parquet layer is the cache of record, and nflreadpy's 24 h default silently served stale
  bytes to `--force` and to the 12 h current-season refresh while doubling disk use (its
  ~500 MB copy was deleted).
- **Schedule coach columns stop recording mid-season changes after 2023.** `home_coach` /
  `away_coach` are per game, and through 2023 they change when a team changes coach in season
  (e.g. 2022 IND: Frank Reich weeks 1-9, Jeff Saturday weeks 10-18). From 2024 on they are
  season-constant: 2024 shows one coach per team although NYJ, CHI and NO fired theirs in season,
  and 2025 lists Brian Callahan for TEN all 18 weeks (fired 2025-10-13, Mike McCoy interim) and
  Brian Daboll for NYG all 18 weeks (fired 2025-11-10, Mike Kafka interim). Team-seasons with
  more than one coach, counted on the cache (2026-09-26): 1999 0, 2000 4, 2001 1, 2002 0,
  2003 1, 2004 2, 2005 2, 2006 0, 2007 1, 2008 3, 2009 1, 2010 4, 2011 3, 2012 1, 2013 1,
  2014 1, 2015 1, 2016 2, 2017 1, 2018 2, 2019 1, 2020 3, 2021 2, 2022 3, 2023 3, 2024 0,
  2025 0, 2026 0 (so far). Query, per season file:
  `SELECT count(*) FROM (SELECT season, team FROM (SELECT season, home_team AS team, home_coach AS
  coach FROM s UNION ALL SELECT season, away_team, away_coach FROM s) WHERE coach IS NOT NULL
  GROUP BY season, team HAVING count(DISTINCT coach) > 1)`. Consequence for the warehouse:
  `coach_game` / `coach_team_season` / `dim_coach` are exact for 2000-2023 and miss interim
  coaches from 2024 on; the Hot-Seat firing labels must combine them with an owner-verified
  manual file (`data/manual/coach_departures.csv`, Hot-Seat step) for 2024+. The build records
  `n_team_seasons_multi_coach` in `build_manifest.notes` so the gap stays visible.
- **End-of-regular-season as-of vs Sunday-evening announcements.** `dim_week.
  asof_end_of_regular_season_utc` is the morning after the last REG game date (12:00 UTC), per
  config and spec 6.1. Some departures are announced on the Sunday evening of the finale (New
  England's on 2025-01-05, hours after the game), i.e. *before* that as-of; with spec 6.2's
  "outcomes strictly after as_of" rule they would fall out of the label. The Hot-Seat step must
  define the label window relative to the last REG kickoff (or move the as-of to after the last
  game's estimated end) and record the choice; see docs/warehouse.md.
- **0-row probes are not cached.** Loading the year before coverage (`snap_counts` 2012,
  `schedules` 1998, `draft_picks` 1979, `combine` 1999) returns an empty frame; those files
  were deleted and `fetch()` no longer caches an empty immutable season, so `cached_seasons()`
  reports real coverage only.

### Dtype drift to cast at warehouse load (B1)

The raw cache and `data/schemas` keep upstream dtypes untouched (that is how drift is
detected), and `fetch_seasons()` concatenates with `diagonal_relaxed`, which widens a column to
the common supertype without telling the caller. All casts happen in the warehouse loader,
with a test asserting `season`/`week`/`play_id` are INTEGER in every fact table. Verified per
season on the cache (`pl.read_parquet_schema` over every file):

| Dataset | Column | Seasons and dtypes | Cast to |
|---|---|---|---|
| `injuries` | `season`, `week` | Float64 2009–2020; Int32 2021–2026 | INTEGER |
| `ff_opportunity` (weekly) | `season` | **String** (`"2026"`) in every season 2006–2026 | INTEGER |
| `ff_opportunity` (weekly) | `week` | Float64 in every season 2006–2026 | INTEGER |
| `participation` | `play_id` | Int32 2016–2022; Float64 2023–2025 | INTEGER |
| `pbp` | `play_id` | Float64 in every season 1999–2026 | INTEGER |
| `pbp` | the 58 of the 67 `FACT_PLAY_INTEGER` columns (counts, yard lines, seconds, 0/1 flags) that arrive as Float64 in some season (the other 9 are Int32 everywhere) and `injuries` `season`/`week` | Float64 in some or all seasons; **verified integral on every non-null value 1999–2026** (0 fractional, 0 NaN, 0 inf), so the INTEGER cast loses nothing. The build refuses a non-integral value rather than round it | INTEGER |
| `pbp` | `goal_to_go` | Float64 1999–2002, 2020, 2024–2026; Int32 otherwise | INTEGER |
| `pbp` | `xyac_median_yardage` | Float64 1999–2005; Int32 2006–2026 | INTEGER |
| `rosters_weekly`, `rosters` | `draft_number` | String 2002–2015; Int32 2016–2026 (absent in `rosters` 1998–2001) | INTEGER |
| `rosters_weekly`, `rosters` | `jersey_number` | String 2002–2015; Int32 2016–2026 (and `rosters` 1998–2001) | **VARCHAR**: 2002–2015 contain values such as `69B` that are not integers |
| `rosters_weekly`, `rosters` | `height` | Float64 through 2024; Int32 2025–2026 | INTEGER |
| `ff_playerids` vs rosters | `espn_id` | Int64 in `ff_playerids`; String in `rosters_weekly`/`rosters`/`players`/`depth_charts` | VARCHAR on the `ff_playerids` side |

`pbp`, `player_stats` and `schedules` store `season` and `week` as Int32 everywhere; those are
the reference. Because drift checks compare only the current season with its snapshot, none of
the above is flagged by `SchemaDriftError`; a "retyped" warning appears only if the current
season itself changes.

## 11. Upstream update schedule (nflverse article, read 2026-09-26)

| Dataset | In-season update |
|---|---|
| Play-by-play, player/team stats | nightly after game days, plus in-game updates; Wednesday–Thursday reload picks up stat corrections ("Thursday's load_pbp() is the cleanest") |
| Schedules | every 5 minutes |
| Snap counts, PFR advanced stats, FTN charting | every day at 00, 06, 12, 18 UTC (subject to PFR/FTN posting) |
| Injuries, depth charts, rosters | every day at 07:00 UTC |
| Next Gen Stats | nightly 03:00–05:00 ET |
| Participation | after the postseason only |
| ff_opportunity, rankings, playerids, draft, combine, contracts, players, teams | not stated in the article; ff_opportunity observed updating within ~2 days |

**Implication for the Tuesday 14:00 UTC snapshot:** Monday-night pbp lands in the overnight
run (by ~09:00 UTC Tuesday), snap counts by the 12:00 UTC PFR pull, injuries/depth charts at
07:00 UTC. The 14:00 UTC as-of is safe. Stat corrections arrive Wed–Thu; the nightly job will
keep refreshing the current season so backfilled corrections are picked up, and predictions
are never recomputed for a past as-of (time-machine rule).

## 12. Proposed `available_at` rules for backtests (to implement and test in B2)

| Table | `available_at` (UTC) for historical rows |
|---|---|
| plays, player-week, team-week, snap counts | `available_at` = `fact_game.game_end_utc_est` (kickoff + 4 h) of the row's `game_id`. Weekly tables without a `game_id` (ff_opportunity): `dim_week.last_game_end_utc_est`. Never a per-week Tuesday constant: in the five split weeks (`dim_week.is_split_week`: 2010 W16, 2020 W5/W12/W13, 2021 W15) the moved game (two games in 2021 W15) kicks off after that week's `asof_weekly_utc`, so a Tuesday 09:00 rule would leak it (see docs/warehouse.md, dim_week). Rows with `fact_game.kickoff_is_estimated` (1999, and the 102 placeholder rows of 2000-2005) need a conservative end (e.g. 04:30 UTC of the day after `gameday`) |
| schedules (results, closing lines) | results: game end; **lines: kickoff** (closing line), never earlier |
| injuries week N | `date_modified` when present (2010–2024, observed); otherwise Friday 23:59 UTC of week N, the last practice report before the games (2009, 2025+, and the 62 null rows of 2010) |
| depth charts legacy week N | Wednesday 12:00 UTC of week N (start of the week's practice) |
| depth charts 2025+ | `dt` |
| rosters_weekly week N | same as depth charts legacy |
| rankings | `scrape_date` 12:00 UTC |
| draft picks, combine | draft day / March of that year |
| participation | Feb 15 of the following year (offseason) |

These are conservative estimates; live ingestion will record the real arrival time in
`pipeline_runs` so the estimates can be checked against reality this season.

## 13. What changes in the spec because of this verification

1. Snap counts start **2013**, not 2012. Waiver Radar training on 2013 only, evaluation
   2014–2025 unchanged.
2. The FantasyPros archive starts **2020** → the candidate-pool proxy needs a pre-2020 fallback
   (⚖️ at C1).
3. Injury availability is **observed** for 2010–2024 via `date_modified` and assumed only for
   2009 and 2025+ (§4).
4. Depth charts have two schemas (§3); `dt` replaces `season`/`week` from 2025.
5. `receive_2h_ko` is not in pbp; derive it in P2.
6. `load_rosters` is not a season roster in 2026; use `rosters_weekly`.
7. `fantasy_points_ppr` counts return touchdowns; our engine gets a `special_teams_td` line.
8. `team_stats` exists and can feed team-context features cheaply.
9. The ingestion layer stays dtype-agnostic; B1 owns type normalisation (§10 drift table).
10. nflreadpy's own cache is **off** (spec 4.1 said to enable it); the Parquet cache supersedes
    it (§10).

## 14. Glossary (one line each)

- **PPR** — points per reception: a scoring format that adds 1 point per catch (full PPR) on
  top of yards and touchdowns; `config/scoring.yaml` is full PPR.
- **PPG** — fantasy points per game.
- **ECR** — Expert Consensus Rank: FantasyPros' average of many experts' rankings; lower is
  better (`sd`, `best`, `worst` describe how much the experts disagree).
- **ADP** — average draft position: where real drafts took a player; a proxy for how the
  public values him.
- **Redraft / dynasty / best-ball** — one-season leagues (rosters reset every year) /
  keep-your-roster leagues (youth and rookies matter) / leagues with no weekly lineup
  decisions (the best scorers count automatically).
- **Superflex** — a lineup slot that may hold a second QB, which makes QBs far more valuable;
  rankings for it carry the `sf` codes.
- **IDP / K / DST** — individual defensive players, kickers, team defenses (out of scope in P1).
- **gsis_id** — the NFL's own player ID (a string starting with `00-00`), the key nflverse uses
  everywhere; **espn_id**, **pfr_id**, **sleeper_id**, **fantasypros_id** are other sites' IDs,
  mapped in `ff_playerids`.
- **PFR** — Pro Football Reference, the site snap counts and advanced stats come from; its
  player IDs look like `BankKe01`.
- **Snap share** — the share of the team's offensive plays a player was on the field for
  (`offense_pct` in snap counts): opportunity before production.
- **xFP / ffopportunity** — expected fantasy points: what an average player would have scored
  from the same opportunities (targets, carries, field position), from the ffopportunity
  model; **FPOE** is actual minus expected.
- **EPA / WP** — expected points added per play / win probability, nflfastR model outputs used
  as features (Section 6.3 caveat).
- **Closing line / spread / total / moneyline** — the betting market right before kickoff:
  `spread_line` is the home team's expected margin (positive = home favored), `total_line` the
  expected combined score, the moneyline the odds on who wins outright.
- **REG / POST** — regular season / playoffs (the `season_type` / `game_type` columns).
- **IR** — injured reserve (`RES` in roster `status`).
- **As-of / time-machine rule** — every prediction is stamped with the moment it was made and
  uses only data available then; a past week's prediction is never recomputed with later data.
