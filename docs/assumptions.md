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
| `date_modified` | present 2009–2024: a timestamp labelled UTC on essentially every row 2010–2024 (real UTC from 2021; 2010–2020 behave like a wall-clock time of unknown zone, §4), one junk stamp in 2009; absent from 2025/2026 | injuries |
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
`report_primary_injury`, …) have a `date_modified` column, typed as a **UTC datetime**
(`Datetime(us, UTC)`), in the 2009–2024 files; it is **absent from 2025 and 2026**. Only the
2021–2024 values behave like true UTC (below). Verified on the cache:

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
- **Two regimes (B2 review, 2026-09-27).** 2021–2024 stamps are afternoon UTC (Friday median
  19.5–19.9 h UTC; 5–36 morning rows per season) and their time of day moves with daylight
  saving time (Friday median 19.35 h UTC in Sep–Oct vs 20.13 h in Dec–Jan, i.e. about
  15:15–15:20 ET all season, the real afternoon release). 2010–2020 stamps are not: the Friday
  median is 11.6–12.3 h "UTC" every season (1,959–3,147 morning rows per season) and does
  **not** move with DST (11.88 h in Sep–Oct, 11.85 h in Dec–Jan), i.e. before Friday practice
  if read as UTC. They behave like a wall-clock time labelled UTC; the true zone is unknown.
  Read as UTC they are several hours earlier than the real publication, so the availability
  rule adds a conservative **9 h** (`availability.injury_legacy_stamp_offset_hours`): 8 h covers
  a Pacific-standard-time reading, the extra hour the gap to the observed ~15:15–16:00 ET
  release and PDT. Later is safe: with +9 h only 6 of 5,870 team-week last stamps pass their
  kickoff. At the official as-ofs this changes the visibility of 2 rows (2010–2020).
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

**Availability rule (B2, implemented; §12):** for 2021–2024 the observed `date_modified` is
`available_at` (the moment the row reached its final weekly state); for 2010–2020 it is
`date_modified` + 9 h (the stamps are not true UTC, above; manifest branch
`date_modified_legacy_shifted`). For 2009, the 62 null rows of 2010 and 2025+, the team's
kickoff that week (the final report is always out by then; A3's
"Friday 23:59 of the report week" was dropped because it lands after Thursday games and is
ill-defined for weeks that start on a Saturday or Wednesday). In
every case a week-N+1 row is never available at the Tuesday as-of after week N. Live: nflverse
refreshes injuries daily at 07:00 UTC, and the pipeline records the real arrival time.

## 5. Player ids: sources, dtypes and match rates (verified 2026-09-27, B3)

The canonical key is `gsis_id`; `bridge_player_id` maps every other id to it
(docs/warehouse.md, "Player IDs"). What the cache holds:

- `players` (`load_players`): 24,833 rows, `gsis_id` unique and never null. `pfr_id` 22,668
  non-null and unique; `espn_id` 16,568 non-null (**String**) and unique; also `pff_id`,
  `nfl_id`, `esb_id`, `otc_id`, `smart_id` (all String; `esb_id` and `smart_id` each have one
  duplicate: one person listed under two gsis ids). Every `gsis_id` seen in `player_stats`,
  `injuries`, `ff_opportunity`, `ngs_*` and `depth_charts` is in `players` (except a `0`
  placeholder and five other 1999-2000 ids in the stats, see the report).
- `ff_playerids` (DynastyProcess): 12,508 rows; `gsis_id` 8,019 non-null (10 values repeated;
  85 of them, 3 not even in GSIS format, are not in `players`); `pfr_id` 9,645 (16 repeated);
  `espn_id` **Int64** 8,177 (13 repeated); `sleeper_id` Int64 6,405 (6 repeated);
  `fantasypros_id` Int64 4,874 (2 repeated); `mfl_id` Int64 unique; `yahoo_id`,
  `sportradar_id`, `nfl_id` String; `pff_id` Int64; plus `name`, `merge_name`, `position`,
  `team`, `birthdate`, `draft_year`/`round`/`pick`. It is the only source of FantasyPros, MFL and
  (before the weekly rosters carry them) Sleeper/Yahoo/Sportradar ids. `stats_global_id` uses 0
  as a placeholder (6,574 rows) and is not used. `nfl_id` mixes the NFL's current numeric id
  (the one `players.nfl_id` uses) with an older NFL.com numbering (2,431 of 6,480 shared
  players differ).
- Where `players` and `ff_playerids` both give an id for the same player they disagree 8 times
  for PFR and 12 for ESPN (6 for PFF); the examples look like `ff_playerids` errors (one player
  given another player's PFR id), so `players` wins.
- `rosters_weekly` (2002-2026): ids are **String** (`pfr_id`, `espn_id`, `sleeper_id`,
  `yahoo_id`, `sportradar_id`, `pff_id`, `esb_id`, `smart_id`, `gsis_it_id` = the NFL numeric
  id, `rotowire_id`, `fantasy_data_id`); empty strings occur (`yahoo_id` 309 rows, `esb_id`
  423, `gsis_id` 11) and are NULL after canonicalization. Across seasons a few ids name two
  players (e.g. 2 PFR, 5 ESPN, 1 Sleeper id): those are ambiguous unless `players` settles them.
  1,684 roster gsis ids (2014-2026) are not in `players`; 1,509 of them carry roster status
  `CUT` at some point (camp and practice-squad players who never appear in nflverse's data).
- `ff_rankings_all.id` is a String FantasyPros id; `ff_rankings_week.fantasypros_id` and
  `ff_rankings_draft.id` are Int64. Snap counts use PFR slugs (`pfr_player_id`, String).
  Depth charts 2025+ carry an ESPN id (String) and a gsis_id; 32,730 of the 1.13M daily rows
  have an ESPN id but no gsis_id, and the bridge fills 1,125 of them (the rest are ESPN ids no
  id table knows).

Final match rates (full 1999-2026 build, after the bridge and the name passes):

| Dataset (QB/RB/WR/TE rows) | Match |
|---|---|
| Snap counts, rows | every season 2013-2026 between 99.75% (2024) and 100% |
| Preseason `rp` candidate pool (QB 18, RB 36, WR 36, TE 18) | 108 of 108 in every season 2020-2026 |
| Weekly `wp` rankings, rows (IDP pages left out) | 99.95% (2020), 99.91%, 99.82%, 99.82%, 99.50%, 99.02% (2025), 97.24% (2026 so far) |
| Redraft `rp` rankings, distinct players (IDP pages left out) | 2020 12 of 836 unmatched, 2023 44/979, 2026 88/937 (fringe players and rookies) |
| `ff_rankings_week` (current week) | 6 of 472 unmatched |

Even `players` is not always right: a few PFR slugs it gives to one (often long-retired) player
are used by the snap counts for someone else. The build's usage check compares each snap-count
season with the linked player's career (rookie season or draft year - 1 to last season + 1):
on the full build 1 PFR id is re-linked to the player the weekly rosters and `ff_playerids`
agree on, 1 id's 6 snap rows are left without a gsis_id, and 10 ids are flagged for a position
on the other side of the ball (report only). All 12 are `suspect` rows in the id report, for
the owner to confirm with manual overrides (docs/warehouse.md, "Player IDs").

Before B3 (A3), snap counts matched 99.7% (2025) and 99.3% (2013) through `ff_playerids`
alone, and 69.1% in 2013 through `rosters_weekly.pfr_id` alone; `players.pfr_id` first closes
almost all of it. Every build refreshes these numbers (`report_id_coverage`; `twm ids`).

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

**Rostership exists for 2020 on (found in C1; this corrects spec 8.1's "historical rostership
data does not exist").** The archive carries `player_owned_avg` (FantasyPros' average share of
leagues rostering the player, 0-100), `player_owned_espn` and `player_owned_yahoo`. On the
weekly (`wp`) positional pages `player_owned_avg` is filled for 5,062 of 8,124 QB/RB/WR/TE rows
in 2020 and every row 2021-2026; `player_owned_espn` for the same 2020 rows and every row
2021-2023, none 2024-2026 (the preseason cheat sheets still have ESPN figures for part of
2024). It covers only the players FantasyPros ranks each week, and nothing before 2020, so the
candidate pool keeps the rank-based stand-in and uses rostership as its check
(`docs/waiver_radar.md`: about 96% of pool players with a figure are owned in under 50% of
leagues). The page layout: `rp` positional cheat sheets exist in a long form
(`/nfl/rankings/ppr-wr-cheatsheets.php`, 2021 on) and a short form (`ppr-wr-cheatsheets`,
2019-12 to 2020-09; two 2020 scrapes label them `dynasty-offense`); `ros-...` pages are
rest-of-season; `wp` pages (`/nfl/rankings/ppr-wr.php`, short `ppr-wr`) are scraped on
Fridays (84 dates), sometimes Thursdays (17). The archive repeats some rows (1,114
(scrape_date, ecr_type, fp_page, id) groups among `rp`/`wp`), and a few players are listed on
another position's page (152 rows, e.g. a RB on the WR sheet). All handled in `fact_ranking`
(docs/warehouse.md).

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

Resolved in B4 (`docs/scoring.md`): the engine computes points from components and
`options.fumbles_lost_scope` chooses the fumble column: `all` (default, `fumbles_lost_total`, every
lost fumble incl. returns) or `scrimmage` (the three-column sum, as nflverse). The `nflverse_ppr`
preset reproduces `fantasy_points_ppr` exactly on all 150,691 QB/RB/WR/TE player-weeks 1999-2026;
our default differs on 983 player-weeks (return fumbles) and 67 (fumble-recovery touchdowns,
which nflverse does not score). Both choices are to be checked against the ESPN league's settings
in step F2.

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
- **Update timing, checked 2026-09-28 (C3)** with the GitHub releases API (by the orchestrator
  of step C3, recorded in its design brief): the ffopportunity weekly 2026 release asset was
  updated at 00:50 UTC, 16 minutes after nflverse's `play_by_play_2026` asset (00:34 UTC): it is
  rebuilt right after play-by-play. So `fact_opportunity_week.available_at` = the game's end +
  `game_data_lag_hours.pbp` (6 h), like play-by-play (section 12). One observation: live runs
  should keep logging the real arrival time (E4).
- **Warehouse table (C3):** `fact_opportunity_week`, 106,560 player-games 2006-2026 on the
  2026-09-28 cache. Upstream `season` is a String and `week` a Float64 in every season (cast).
  6,441 rows have no `player_id`: at most one per team-game, the passes without an identified
  target (28,046 in all, in `rec_attempt`); dropped. One `player_id` (a tight end) appears twice
  in two 2013 games, the second time under an outside linebacker's name; the QB/RB/WR/TE row is
  kept.
- **xFP re-scoring verified (C3):** every expected component (`*_exp`) and ffopportunity's point
  subtotals are stored with 2 decimals, and its points were computed from unrounded values, so
  re-scoring cannot match to the cent (it does on 14% of player-games). With nflverse-PPR
  weights our xFP is within the rounding bound (0.1412 points) of `total_fantasy_points_exp` on
  106,463 of 106,560 player-games; the other 97 all have an expected rushing two-point
  conversion: the per-play file (`ff_opportunity_rush`) shows ffopportunity adds 0.1 x the
  expected yards of a two-point try (`rush_yards_exp` on `two_point_attempt = 1` plays) to
  `rush_fantasy_points_exp` but not to `rush_yards_gained_exp`. Adding those yards back leaves 0
  player-games outside the bound (largest difference 0.079). Our xFP leaves them out, like real
  scoring.
- **Per-play files (D1, verified 2026-09-29 on the 2006-2026 cache with DuckDB sums per
  player-game against `fact_opportunity_week`).** `ff_opportunity_pass` (374,313 rows) and
  `ff_opportunity_rush` (295,636) have one row per pass (sacks excluded, two-point tries
  included) or run (kneels included), keyed by `game_id` + `play_id` (a float, always integral);
  the same columns and dtypes in every season (no drift); the 0/1 outcome columns
  (`complete_pass`, `pass_touchdown`, `interception`, `rush_touchdown`, ...) are categoricals
  ('0'/'1'). Two pass plays repeat (the 2013 target listed a second time under a linebacker's
  name, as in the weekly file). How the weekly file is made from them, found by summing and
  comparing (all within the 0.005 rounding of the weekly 2-decimal columns, apart from those two
  plays): `receptions_exp` = sum of `pass_completion_exp`, `rec_yards_gained_exp` = sum of
  `pass_completion_exp x (air_yards + yards_after_catch_exp)`, `rec_touchdown_exp` = sum of
  `pass_touchdown_exp`, all over non-two-point targets (85,844 player-games, 4 off: the
  duplicates); the passer's `pass_completions_exp`, `pass_yards_gained_exp`,
  `pass_touchdown_exp` and `pass_interception_exp` are the same sums over all his non-two-point
  passes, targets or not (13,228 passer-games, 2 off); `*_two_point_conv_exp` = sum of
  `two_point_conv_exp` over two-point tries (`pass_interception_exp` of a two-point try is NOT
  counted: 1,258 passer-games would be off); `rush_yards_gained_exp` / `rush_touchdown_exp` =
  sums of `rush_yards_exp` / `rush_touchdown_exp` over non-two-point runs (44,197 rusher-games,
  0 off). `rush_yards_exp` is the value ffopportunity scores: it equals the raw model output
  `rushing_yards_exp` except on 10,504 plays, kneels (-1) and aborted snaps (0);
  `rush_touchdown_exp` is 0 on kneels and two-point tries (`rushing_td_exp` there holds the raw
  model value or the two-point chance). A passer or rusher whose only play was a two-point try
  (44 and 58 player-games) has a weekly row with 0 attempts and only the two-point
  expectation. The upstream per-play `yards_after_catch` is the
  play's yards after the catch only from 2023 (before, it holds other values); the warehouse
  leaves it out and computes yards after the catch as `receiving_yards - air_yards`.
- **Joins to the play-by-play (D1, full build 2026-09-29).** 374,311 pass rows (after the 2
  duplicates) and 295,636 rush rows: 3 and 4 have no `fact_play` play (99.999%); from the other
  side, 357,891 of 357,944 regular-season non-sack pass attempts 2006-2026 and 283,223 of
  283,244 runs have a row. For the same play the passer differs from `fact_play` twice, the
  rusher 27 times and the receiver 1,120 times, 1,104 of them in 2006-2008 and 1,106 where
  today's play-by-play names no target (ffopportunity was built on an older play-by-play
  release; build_manifest notes).
- **Unnamed targets 2006-2008 (D1, same check).** Share of incomplete passes (two-point tries
  excluded) whose target is named in `ff_opportunity_pass`: 1.1% (2006), 7.6% (2007), 9.0%
  (2008), 96.5% (2009), 86-97% in 2009-2026; today's play-by-play is the same (it names 9,855 /
  10,474 / 10,155 receivers on 16,422 / 17,084 / 16,588 passes in 2006-2008, about 95-99% of passes from 2009), and
  `player_stats.targets` is empty for 2006-2008 (67, 14 and 17 player-weeks with a target). So
  receivers' expected points of 2006-2008 count little more than their catches (WR xFP = 60% of
  WR points, 101% from 2009): Regression Watch flags these seasons (docs/regression_watch.md).
- Section 6.3 caveat applies (model trained across seasons).

## 10. Other observations

- **pbp `game_date`** is a string date; **schedules `gameday`/`gametime`** are strings
  (`2026-09-10`, `20:20`), kickoff in US Eastern time per nflverse convention. The warehouse
  (B1) converts them to UTC (`fact_game.kickoff_utc`). **1999 has no `gametime` at all** (259 games), and
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
  **H1 correction (2026-10-01, from the Wikipedia season pages cited in
  `data/manual/coach_departures.csv`):** 2000-2023 is not exact either: 2015 MIA (Philbin), 2015
  TEN (Whisenhunt), 2016 LA (Fisher) and 2019 CAR (Rivera) were fired in season but are listed
  all season; and the 2026 schedule still lists Gannon (ARI), Morris (ATL) and McDermott (BUF),
  reported fired in January 2026 (so 2026 coach attribution for those teams is wrong). The owner
  file, not the schedule, is the source of truth for departures (docs/labeling_coaches.md).
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

## 12. `available_at` rules for backtests (implemented in B2)

The rules live in `src/twm/warehouse/available.py`; docs/warehouse.md "When is a row
available?" explains each one with a worked example. Principle: `available_at` is the earliest
moment the whole row was public, and every estimate errs late.

| Table | `available_at` (UTC) | Evidence (full 1999-2026 cache, probed 2026-09-27) |
|---|---|---|
| `fact_play`, `fact_player_week`, `fact_team_week`, `fact_snaps` | the row's game (`game_id`) end + 6 h (`availability.game_data_lag_hours`) | Every row's `game_id` joins `fact_game`. The tightest non-split week (a 22:25 ET week-1 Monday game, 2007) ends 7.6 h before its Tuesday 14:00 as-of: 1.6 h to spare after the lag (2 h for the guessed 1999-2005 Monday games). The regular-season finale ends 6.5-6.7 h before the end-of-season snapshot (Sunday-night finales 2006-2025): 0.5 h to spare; 0.5 h for the guessed 1999-2005 Sunday night slot and exactly 0 h for the 1999-2002 Monday-night finales (inclusive boundary). The build stops if a lag drops any such row. Split weeks (2010 W16, 2020 W5/W12/W13, 2021 W15): the moved games are available after their week's as-of, by construction. |
| game end | `game_end_utc_est`, or for guessed kickoffs (1999; 2000-2005 placeholders) the latest normal night slot for that weekday + 4 h (`availability.estimated_kickoff_for_availability_et`: 21:00 ET on Mondays, 20:30 ET otherwise), never earlier than the guess | The guesses (13:00 ET Sunday default, 20:00 ET weekdays) can be ~7 h early for night games; Monday-night games of 1999-2005 kicked off at 21:00 ET (114 guessed Monday games), every other night game by 20:30 ET, so the estimate errs late. |
| `fact_game` (results, closing lines, coaches) | game end + 3 h (`availability.game_result_lag_hours`) | Weather delays (up to 77 min) and overtime push real endings past kickoff + 4 h. On the 1,722 games of 2020-2025 with a reliable play clock (first play within 30 min of kickoff, last within 6 h) the last play is never after kickoff + 7 h (6 end after kickoff + 4 h; the longest runs 5.3 h). `time_of_day` itself is not the rule: it has junk values and nothing before 2001. |
| `fact_schedule` (pre-game columns only) | REG: May 20 of the season, 00:00 UTC; POST: when the previous round is final (game end + 3 h). Slot columns (date, time, venue, rest days): from `slot_available_at` = the latest of that, kickoff - 12 days, the previous week's as-of for the last two REG weeks. Listed changes (`availability.schedule_exceptions`): not before the game's own kickoff (no typed-in announcement dates; a date is accepted only with a source link) | Schedule releases fall in April–May; flexed games get 12 days' notice; Week 17/18 slots are set after the previous week. nflverse holds only the FINAL schedule; the 24 curated changes (Irma 2017, the 2020 COVID moves, snowstorm and wildfire relocations, the snow-delayed 2023 Wild Card game, the cancelled 2022 W17 BUF-CIN game) are the ones we can correct; others cannot be detected. |
| `dim_week` (static) | always visible; the columns computed from a week's final games (`n_games`, first/last gameday and kickoff, `n_games_after_asof`, `is_split_week` ...) are NULL in the as-of view until that week's own as-of | Built from the FINAL schedule: a future week's counts reveal postponements (2020) and the 2022 W17 cancellation. |
| `dim_player` (hindsight) | a row exists point-in-time from May 15 of its `draft_year` (`availability.draft_public_month_day`; drafts ended by May 10) or, undrafted, from the player's first data row; never otherwise. Visible columns: ids, name, birth date, draft, college; position, size and today's status hidden | Otherwise every later draft class is visible in-season (the next draft's order mirrors the final standings); `position` is today's value (Patterson WR → RB 2021, Taysom Hill QB → TE). |
| `fact_injury_report` | `date_modified` (2021-2024), `date_modified` + 9 h (2010-2020, `availability.injury_legacy_stamp_offset_hours`), else the team's kickoff, else (no game that week) the week's as-of; never before 1 s after the previous week's as-of | The 2010-2020 stamps are not true UTC (§4). Last stamp per team-week vs kickoff (2010-2024, stamps as stored): Sunday games median 53.8 h before, p01 20.9 h; Thursday median 35.5 h, p01 10.1 h; only 8 of ~8,100 team-weeks have a stamp after kickoff (6 of 5,870 for 2010-2020 after the +9 h); 16 rows are available after their own week's as-of. 125 rows are raised to just after the previous week's as-of (mostly Monday reports for the next week; spec 6.1). The 17 rows of the cancelled 2022 W17 BUF-CIN game have stamps; the no-game branch is a guard. |
| `fact_depth_chart` legacy week N | week N's as-of minus 6 days (Wednesday 14:00 UTC before the games); the post-finale REG week 18/19 charts use the Wild Card week, SBBYE the Super Bowl week | Week N's chart is visible at the as-of after week N; week N+1's never is. Verified on 2013-2025: 0 rows of a later week visible at any non-split week's as-of. |
| `fact_depth_chart` daily (2025+) | `dt` | Snapshot time. |
| `coach_game` | kickoff | A future game's listed coach can reveal a firing. |
| `coach_team_season` | when the stint's last game is final (game end + 3 h), or the next coach's first kickoff when the team changed coach (43 stints) | A finished stint mid-season means a firing. |
| `dim_coach` | first kickoff | |
| `fact_roster_week` (C1) | game-day seasons (2016 on): week N's `asof_weekly_utc`; post-game seasons (2002-2015): week N+1's as-of (the last week: its own as-of + 7 days). The regime is measured per season by the build (`availability.roster_postgame_share_threshold`, 1%) | Among players who played in week N (snap or stat row), the share whose week-N roster status is not ACT: 6.6%-9.8% every season 2002-2015, 0%-0.18% every season 2016-2026 (see §15). A post-game roster can contain moves made after the Tuesday as-of, so it waits a week (the A3 proposal, "like legacy depth charts", would have leaked those moves) |
| `fact_ranking` (C1) | the day after `scrape_date`, 00:00 UTC | The scrape's time of day is unknown (A3 proposed 12:00 UTC the same day; the end of the day errs later). The Tuesday as-of therefore sees the previous Friday's weekly ranking |
| `fact_opportunity_week` (C3, ffopportunity weekly) | the row's game end + `game_data_lag_hours.pbp` (6 h), joined on `game_id`, like play-by-play (the A3 proposal was a per-week constant; per game is what the other game-data tables do, so a split-week game waits for its own end) | The 2026 asset is rebuilt 16 minutes after `play_by_play_2026` (GitHub releases API, 2026-09-28, section 9). Every row's `game_id` is in the schedule; 113 rows (split-week games) are public only after their own week's as-of. |
| `fact_opportunity_pass`, `fact_opportunity_rush` (D1, ffopportunity per play) | the row's game end + `game_data_lag_hours.pbp` (6 h), joined on `game_id`: the same moment as its `fact_play` play | They come from the same ffopportunity run as the weekly file (which is their per-player sum, section 9), rebuilt right after play-by-play. Checked on a build: every row's `available_at` equals its play's. 395 pass and 332 rush rows (split-week games) are public only after their week's as-of. |
| draft, combine, participation (not in the warehouse yet) | A3 proposals, to implement when those tables are added: draft/combine the draft day / March; participation Feb 15 of the next year (never in-season) | |

Verified on a full 1999-2026 build (2026-09-27, rebuilt after the B2 review): no event row has
a NULL `available_at`; for
every non-split regular-season week 2013-2025 (222 weeks) all of that week's plays, player
stats, team stats and snaps are visible at its Tuesday as-of and nothing from a later week is
(plays, stats, snaps, games, injury reports, legacy depth charts).

Known, accepted imperfections (values baked into rows that are already visible; neither
`available_at` nor the leakage harness can catch them):

- nflverse's Wednesday–Thursday stat-correction reloads are baked into the cache, so
  historical backtests see corrected week-N stats at the Tuesday as-of (a mild, unavoidable
  leak);
- `fact_schedule` and `dim_week` hold nflverse's FINAL schedule. Postponements, reschedules
  and cancellations (the 2020 COVID moves, the cancelled 2022 W17 CIN-BUF game, flexes) are
  baked into rows visible from May 20. The slot masking, the `dim_week` masking and the 24
  curated changes reduce this; a change outside the list is not detectable, so features must
  not treat future-week counts, byes or split flags as known in advance for those seasons;
- today's snapshots: `fact_player_week.position`/`position_group`/`headshot_url`,
  `fact_depth_chart.player_position` and `dim_player.position`/`height`/`weight` are current
  values (Cordarrelle Patterson is an RB and Taysom Hill a TE for every past season). The
  as-of view hides them; a point-in-time position must be derived in B3/C1 from the
  as-of-visible depth-chart slot (`fact_depth_chart.position`) or `rosters_weekly` once it is
  ingested. `dim_team.team_division` is today's alignment and team codes are today's.

These are conservative estimates for history; live runs record the real arrival time of each
dataset in the run log (`pipeline_runs`, step E4) so the estimates can be checked this season.

## 13. What changes in the spec because of this verification

1. Snap counts start **2013**, not 2012. Waiver Radar training on 2013 only, evaluation
   2014–2025 unchanged.
2. The FantasyPros archive starts **2020** → the candidate-pool proxy needs a pre-2020 fallback
   (⚖️ at C1).
3. Injury availability is **observed** for 2010–2024 via `date_modified` and assumed (kickoff)
   only for 2009 and 2025+ (§4, §12).
4. Depth charts have two schemas (§3); `dt` replaces `season`/`week` from 2025.
5. `receive_2h_ko` is not in pbp; derive it in P2.
6. `load_rosters` is not a season roster in 2026; use `rosters_weekly`.
7. `fantasy_points_ppr` counts return touchdowns; our engine gets a `special_teams_td` line.
8. `team_stats` exists and can feed team-context features cheaply.
9. The ingestion layer stays dtype-agnostic; B1 owns type normalisation (§10 drift table).
10. nflreadpy's own cache is **off** (spec 4.1 said to enable it); the Parquet cache supersedes
    it (§10).
11. (C1) Historical fantasy **rostership exists from 2020** in `ff_rankings_all` (§6), against
    spec 8.1; the candidate pool still uses the rank-based proxy (no figure before 2020 or for
    the deep bench) and reports its agreement with rostership.
12. (C1) Weekly rosters are **post-game** snapshots in 2002-2015 and **game-day** snapshots
    from 2016 (§15); positions for point-in-time use come from them, not from `players`.

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
- **Roster status** — `ACT` active, `INA` inactive for that game (healthy scratch or minor
  injury), `DEV` practice squad (can be promoted any week), `RES` a reserve list (injured
  reserve ...), `PUP`, `SUS`, `CUT`, `RET`; the Waiver Radar treats ACT, INA and DEV as "on a
  roster".
- **Rostership** — the share of fantasy leagues in which a player sits on a team
  (`player_owned_avg`, 0-100); low rostership = available on waivers.
- **Candidate pool** — the players probably still on waivers in a 12-team league at an as-of
  (`docs/waiver_radar.md`).
- **As-of / time-machine rule** — every prediction is stamped with the moment it was made and
  uses only data available then; a past week's prediction is never recomputed with later data.

## 15. Weekly rosters: game-day and post-game snapshots (C1)

Verified on the full 1999-2026 cache (2026-09-27) while building `fact_roster_week`:

- **Snapshot timing.** Among players who played in week N (a snap-count or player-stats row that
  week), the share whose week-N roster status is not ACT is 6.6%-9.8% in every season
  2002-2015 and 0%-0.18% in every season 2016-2026. C1 read this as "the 2002-2015 rosters
  were taken after the games"; C3 found the main cause is that their status is the season-final
  one on every week (last bullet). The build still measures this per season
  (`build_manifest.notes.regime_by_season`) and delays the 2002-2015 rosters by a week (§12):
  whether their membership was recorded after the games cannot be told from the data, so the
  delay is kept as the safe choice. The pool ignores their status (docs/waiver_radar.md).
- **Duplicates.** 16,302 (season, week, gsis_id) groups have more than one row (33,771 rows),
  all in 2002-2015 except 13 in 2019: trades show the old team's TRC/TRD/TRT row next to the
  new team's; 151 rows have no gsis_id.
- **Team codes.** 2002-2015 use ARZ, BLT, CLV, HST and SL for Arizona, Baltimore, Cleveland,
  Houston and St. Louis (checked: the same players carry ARI, BAL, CLE, HOU and LA in
  player_stats for thousands of player-weeks each), plus OAK and SD.
- **Coverage by era.** Practice-squad players (DEV) are listed from 2017 (about 300 a week
  2017-2019, 440+ from 2020, a handful before); INA (inactive for the game) from 2019; the
  2016 week-1 roster is the summer camp roster (2,471 ACT rows; 1,781 in week 2).
  Teams on their bye week are missing from a game-day week's roster. A released player usually
  gets a CUT row, but some simply vanish from the next roster.
- **Positions change.** In `fact_roster_week`, 70 players hold more than one fantasy position
  (QB/RB/WR/TE) across 2013-2025, and 49 player-seasons show two within one season: the
  roster position of the week is the point-in-time position.
- **`rookie_year` is partly hindsight.** 17 rows carry a rookie year later than the roster's
  season; `entry_year` never does. The as-of view hides `rookie_year`.
- **2002-2015 statuses are one value per season (found in C3, 2026-09-28 cache).** In every
  season 2004-2015 no player's status changes from one roster week to the next (0 players; in
  2016-2019 between 384 and 1,616 players a season do), and 98-115 players a season in 2013-2015
  are RES on every week's roster although they played (Chicago's 2015 roster lists two
  receivers and a tight end RES in every week, including weeks in which they played 77-100% of
  the offensive snaps). So the 2002-2015 status is the player's status when the season's file was
  compiled (apparently its end), stamped on every week: hindsight. Team membership does change
  week to week in those seasons (about 450 team stints a season start after week 1 and as many end
  early), so presence on a roster is usable. Consequences: the Waiver Radar's teammate rule
  `roster_status` only uses statuses once they are seen to change within the season (never for
  2002-2015); and the C1 pool universe (statuses ACT, INA, DEV) leaves out in 2013-2015 about
  30-42 rostered QB/RB/WR/TE per as-of whose season-final status is RES although they play later
  that season (507, 507 and 712 player-as-of rows; 2016: 149, legitimately returning players).
  An open question for the owner (docs/waiver_radar.md "Known limits of the features").
