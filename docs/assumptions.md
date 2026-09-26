# Data assumptions (Step A3, verified against real downloads)

**Verified on:** 2026-09-26 (Saturday of NFL Week 3; only the Thursday game of Week 3 had been played).
**Tooling:** `nflreadpy` 0.1.5, Python 3.13. Re-run with `uv run python scripts/verify_sources.py`
(writes `reports/verify_sources.json`). Column snapshots live in `data/schemas/*.json` and are
checked on every current-season download (`SchemaDriftError` if a column disappears).

Everything below was observed in the data, not taken from docs. Where the spec (Section 4.1)
said something different, the spec is wrong and this file wins.

---

## 1. Loaders: names, coverage, current-season status

All 20 loader functions named in the spec exist in `nflreadpy` with exactly those names, plus
`load_team_stats`, `load_officials`, `load_trades` (unused). Our dataset registry is in
`src/twm/sources/nflverse.py` (`DATASETS`).

| Our name | nflreadpy call | Spec said | **Verified first season** | 2026 status on 2026-09-26 | Notes |
|---|---|---|---|---|---|
| `pbp` | `load_pbp(season)` | 1999+ | **1999** (1998 rejected) | weeks 1–3 (33 games) | 372 columns |
| `player_stats` | `load_player_stats(season, summary_level="week")` | 1999+ | **1999** | weeks 1–3 | 150 columns; REG+POST rows, `season_type` column |
| `team_stats` | `load_team_stats(season, summary_level="week")` | not in spec | 1999 | weeks 1–3 | team-week EPA etc.; handy for context features |
| `schedules` | `load_schedules(season)` | 1999+ | **1999** (1998 returns 0 rows) | 272 games, 33 with results | 46 columns |
| `snap_counts` | `load_snap_counts(season)` | **2012+** | **2013** — the 2012 file exists but has **0 rows** | weeks 1–3 | 16 columns; PFR IDs |
| `injuries` | `load_injuries(season)` | 2009+ | **2009** | weeks 1–3 | no report date column in modern data (see §4) |
| `depth_charts` | `load_depth_charts(season)` | 2001+ | **2001** | 200 daily snapshots since 2026-03-22 | **two formats** (see §3) |
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
datasets `nflreadpy` raises `ValueError: Season must be between …` for the year before; for
`schedules`, `draft_picks`, `combine` it returns an empty frame instead.

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
| `date_modified` | present only in early seasons (2009) and null; absent from 2025/2026 | injuries |
| `season` | absent (one file for all teams) | teams |

Everything else the spec relies on exists under the expected name (full lists in
`data/schemas/`).

## 3. Depth charts: two formats

- **2001–2024 (legacy):** one row per team × week × slot. Columns: `season`, `club_code`,
  `week` (1–22 incl. playoffs), `game_type`, `depth_team` ("1","2","3" = string rank),
  `formation` (Offense/Defense/Special Teams), `position`, `depth_position`, `gsis_id`, names.
  ~59 rows per team-week. Snapshot: `data/schemas/depth_charts_legacy.json`.
- **2025+ (new):** one row per **daily snapshot** × team × slot. Columns: `dt` (ISO-8601 UTC
  string, e.g. `2026-09-26T12:12:29Z`), `team`, `player_name`, `espn_id`, `gsis_id`,
  `pos_grp_id`, `pos_grp` ("3WR 1TE", "Base 4-3 D", "Base 3-4 D", "Special Teams"), `pos_id`,
  `pos_name`, `pos_abb`, `pos_slot`, `pos_rank`. **No `season` or `week` column**; the season
  is the file you loaded, and the 2025 file's snapshots run from 2025-08-03 to 2026-03-14
  (the offseason after the season belongs to the earlier season's file). 2026: 200 snapshots
  so far, one per team per day, ~2,800 rows per snapshot.
- **Point-in-time rule:** legacy rows for week N are treated as available at the start of week
  N (so at the Tuesday as-of after week N we use week N's chart); new rows use `dt ≤ as_of`
  directly.

## 4. Injury reports have no publication date

Modern injury rows (`season`, `week`, `team`, `gsis_id`, `report_status`, `practice_status`,
`report_primary_injury`, …) carry **no date**. `date_modified` exists only in old files and is
null. `report_status` values: `Out`, `Doubtful`, `Questionable`, null.

**Assumption for backtests:** the week-N report is available at the end of week N (Friday
before the games at the latest), so at the Tuesday as-of after week N it is fully usable, and
the week-N+1 report is **not** available (it publishes Wed–Fri). Live: nflverse refreshes
injuries daily at 07:00 UTC.

## 5. Snap counts use PFR IDs; the join works

`pfr_player_id` is a string like `BankKe01`. Join to `gsis_id` via `ff_playerids.pfr_id`:

| Season | QB/RB/WR/TE snap rows | matched via `ff_playerids` | matched via `rosters_weekly.pfr_id` |
|---|---|---|---|
| 2025 | 7,126 | 99.7% | 99.6% |
| 2013 | 6,389 | 99.3% | 69.1% |

Use `ff_playerids` first, `rosters_weekly` as fallback, and report the unmatched remainder
(spec 7.5). `ff_playerids` ID dtypes: `gsis_id`/`pfr_id` strings; `espn_id`, `sleeper_id`,
`fantasypros_id` integers.

## 6. FantasyPros rankings archive starts December 2019

`load_ff_rankings(type="all")` has 1.85M rows, 366 scrape dates from **2019-12-27** to
2026-09-25. `ecr_type` codes: `ro`/`rp` redraft overall/positional, `do`/`dp` dynasty,
`wo`/`wp` weekly, `bo`/`bp` best-ball, `*sf` superflex, `drk` dynasty rookies. Weekly PPR pages
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
`fantasy_points_ppr` within 0.01 on 99.7% of rows. All 20 mismatches are exactly +6.0 and are
players with `special_teams_tds = 1` (return touchdowns), which nflverse counts. Adding
`special_teams_tds × 6` leaves 2 rows (99.97% match) still off, which we'll inspect in B4.
Our scoring engine will compute from components (spec 7.1) and include a configurable
`special_teams_td` line (default 6, matching ESPN standard scoring).

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
  derivation (B2) will convert to UTC.
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
- Raw cache size after this verification (three or four seasons of each dataset): 184 MB.
  Full history will be a few GB as the spec expects.

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
| plays, player-week, team-week, snap counts, ff_opportunity | game end ≈ kickoff + 4 h; conservatively: Tuesday 09:00 UTC after the week for all of week N (nightly run) |
| schedules (results, closing lines) | results: game end; **lines: kickoff** (closing line), never earlier |
| injuries week N | Friday 23:59 UTC of week N (last practice report), i.e. before week-N games |
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
3. Injury reports have **no date column** → availability is assumed, not observed (§4).
4. Depth charts have two schemas (§3); `dt` replaces `season`/`week` from 2025.
5. `receive_2h_ko` is not in pbp; derive it in P2.
6. `load_rosters` is not a season roster in 2026; use `rosters_weekly`.
7. `fantasy_points_ppr` counts return touchdowns; our engine gets a `special_teams_td` line.
8. `team_stats` exists and can feed team-context features cheaply.
