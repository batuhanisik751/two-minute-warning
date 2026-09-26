# Two-Minute Warning — NFL Early-Warning App — Project Specification

> **Working name:** Two-Minute Warning (placeholder — rename freely)
> **Audience of this document:** an LLM coding assistant that will build the project with the owner.
> **Owner:** a developer who is in their **first-ever fantasy football season (2026, ESPN league)** and is learning both football analytics and point-in-time modeling. Explanations in code, notebooks and the UI should be beginner-friendly.
> **Owner's existing stack (reuse it):** Python data pipelines, Next.js on Vercel, Neon Postgres, GitHub — the same stack as their F1 analytics app.

---

## 0. Instructions to the LLM reading this

1. Read the whole document before writing code. Section 6 (point-in-time rules) is the part most likely to be done wrong — treat it as a hard requirement.
2. Build in prototype order (Section 10). Do not start a prototype until the previous one meets its acceptance criteria. Prototype 1 has a real deadline: it should be usable during the owner's current season.
3. At every **Decision Point** (marked ⚖️), stop and present options with a recommendation. Do not silently choose.
4. nflverse function names, column names and coverage years listed here are best-known values and **must be verified** against the nflreadpy docs and the nflverse data dictionaries (Step A3). If something differs, use the real value and update this spec's assumptions in `docs/assumptions.md`. Do not invent columns.
5. Time-box investigations: if a data problem takes more than ~30–45 minutes, stop and report what you found and what you suspect.
6. Git: commits are authored solely by the owner. **Do not add AI co-author trailers** (no `Co-Authored-By` lines).
7. Secrets: never commit secrets; use `.env` + `.env.example`. Never `source` a file containing a database URL (special characters such as `&` break shell parsing and can print the secret). **Never ask the owner to paste ESPN cookies (`espn_s2`, `SWID`) into a chat** — they put them in `.env` themselves. Never log them, never include them in test fixtures, never send them anywhere except ESPN's own API.
8. The ESPN integration is **optional** and must be isolated: the entire app must build, test and run with the ESPN module disabled.
9. The public app must not display the owner's league data (leaguemates' names, rosters, transactions). "My League" runs locally only.
10. This is an educational, unofficial project: not affiliated with the NFL or ESPN, not betting advice. No official NFL/team logos in the public app.

---

## 1. Problem definition

### 1.1 The problem

Football information arrives as box scores: *what already happened*. Box scores are noisy — a receiver's two-touchdown week, a running back's garbage-time yards, a coach's lucky fourth-down conversion. Fantasy managers, fans and analysts react to that noise, and the signals that actually predict what happens next (a rising snap share, a teammate's injury, a coach's accumulating bad decisions, an aging player's declining efficiency) are buried in play-by-play data most people never look at.

Existing tools mostly offer **opinions without track records**: expert waiver rankings, "hot seat" columns, breakout lists. Almost none of them say how often their past calls were right, and none let you check what they *would have said* at a given point in the past.

### 1.2 The product thesis

> **Two-Minute Warning flags what is about to change in the NFL — before the box score shows it — and publishes how often each of its flags has been right.**

Every module is a point-in-time prediction with a public, backtested track record and a "time machine" that shows exactly what the app would have said at any past week.

### 1.3 The modules (each is its own prediction problem)

| Module | Question it answers | Unit | Horizon |
|---|---|---|---|
| **Waiver Radar** | Which lightly-rostered players are about to become fantasy starters? | player × week | next 3 weeks |
| **Regression Watch** | Whose fantasy production is driven by unsustainable efficiency (or unlucky and due to rise)? | player × week | rest of season |
| **My League** *(optional, ESPN, local-only)* | Which of *my league's* free agents should I pick up; how many points did I leave on my bench; is this trade good for me? | my league × week | this week / rest of season |
| **Decision Report Card** | Did each coach's fourth-down, two-point and late-game clock decisions gain or lose win probability? | decision → coach × week | — (grading) |
| **Hot-Seat Meter** | What is each head coach's probability of being fired this season? | coach × week | through end of season (+30 days) |
| **Cliff & Breakout Board** | Which veterans are about to fall off a cliff next season, and which second/third-year players are about to break out? | player × season | next season |

### 1.4 Why this is hard (and a good learning project)

- **Point-in-time discipline** across many data sources that update on different schedules (play-by-play nightly, injury reports mid-week, depth charts irregularly, some datasets only after the season).
- **Rare events** (coach firings ≈ 5–10 per season; true breakouts and cliffs are a small share of players) → imbalance, small samples, wide confidence intervals.
- **Model-derived columns** in the source data (EPA, win probability, expected fantasy points) were produced by models fit on all seasons — a subtle leakage source that must be documented.
- **Noise vs signal**: football samples are small (17 games). Regression to the mean is the central statistical idea of the whole app.
- **Counterfactual decision grading** requires building your own win-probability model and evaluating states that did not happen.

### 1.5 What success looks like

- Each module beats a naive baseline in walk-forward backtests, with honest confidence intervals.
- Each module has a **Track Record** entry: backtested seasons plus the live 2026 season, updated automatically.
- The owner uses Waiver Radar and My League in their league this season and can explain every signal in football terms.

---

## 2. Project introduction (README-ready text)

> **Two-Minute Warning** is an open, point-in-time NFL early-warning app. It reads every play of every NFL game since 1999 and flags what is about to change: players about to break into fantasy lineups, hot streaks that won't last, coaches whose decisions are costing their teams wins, coaches about to be fired, and players about to break out — or fall off a cliff — next season.
>
> Every flag comes with a **track record**. Each model is tested the honest way: trained only on seasons before the one it predicts, using only data that was available at the moment of the prediction. A **time machine** lets you pick any week since 2012 and see exactly what the app would have said then — and what actually happened.
>
> Two-Minute Warning is built on the open-source nflverse data ecosystem. It is an unofficial, educational project, not affiliated with the NFL or ESPN, and not betting advice.

### 2.1 What it does (functional summary)

1. **Ingests** nflverse data (play-by-play, player stats, snap counts, injuries, depth charts, schedules with coaches and betting lines, Next Gen Stats, rosters, draft/combine, expected-fantasy-points data) plus fantasy rankings and a cross-platform player ID map.
2. **Builds** a point-in-time warehouse where every row knows when it became available.
3. **Scores** each module on a schedule during the season (nightly, with Tuesday as the official weekly fantasy snapshot).
4. **Grades** coach decisions with its own win-probability model.
5. **Backtests** every module walk-forward and stores every historical prediction for the time machine.
6. **Publishes** aggregated outputs to Postgres; a Next.js web app displays them.
7. **Optionally** connects to the owner's ESPN league locally to personalize fantasy recommendations.

---

## 3. Users and the NFL calendar

- **Primary user:** the owner (beginner fantasy manager, 2026 season).
- **Secondary users:** public visitors interested in NFL analytics (all modules except My League).

| Period | What the app emphasizes |
|---|---|
| Sept → early Jan (regular season) | Waiver Radar (Tuesdays), Regression Watch, My League, Decision Report Card (weekly) |
| Nov → Black Monday (the Monday after Week 18) | Hot-Seat Meter |
| Jan → Aug (offseason) | Cliff & Breakout Board; Track Record reviews; model retraining |

---

## 4. Data sources

### 4.1 nflverse via `nflreadpy` (primary)

- Package: `nflreadpy` (the official Python port of `nflreadr`; returns **Polars** DataFrames; the older `nfl_data_py` is deprecated — do not use it). Docs: `https://nflreadpy.nflverse.com/`.
- Data dictionaries: `https://nflreadr.nflverse.com/` (Reference → dictionaries). Update schedule: the nflreadr article "nflverse Data Update and Availability Schedule" — **read it before designing the pipeline schedule.**
- No account or API key needed. Enable nflreadpy's cache.

| Loader (verify) | Content | Coverage (verify) | Notes / gotchas |
|---|---|---|---|
| `load_pbp` | Play-by-play, ~370 columns (EPA, WP, air yards, CPOE, timeouts, penalties, weather…) | 1999+ | `epa`, `wp`, `vegas_wp`, `xpass`, `cpoe`, `xyac_*` are **model outputs** (Section 6.3). Updated nightly in season. |
| `load_player_stats` (weekly) | Player-week box score + derived shares (`target_share`, `air_yards_share`, `wopr`, fantasy points) | 1999+ | Check which scoring its `fantasy_points_ppr` uses; compute your own from components with the league scoring config. |
| `load_schedules` | Games: scores, `home_coach`/`away_coach`, `spread_line`, `total_line`, moneylines, `home_rest`/`away_rest`, `roof`, `surface`, `temp`, `wind`, `referee` | 1999+ | **Coach columns power Hot-Seat labels.** Lines are closing lines (Section 6.4). Verify the sign convention of `spread_line` empirically (correlation with `result`). |
| `load_snap_counts` | Offensive/defensive/special teams snaps and percentages per player-game | **2012+** (Pro Football Reference) | Defines the start of Waiver Radar backtests. Uses PFR IDs → map via the ID map. |
| `load_injuries` | Weekly official injury reports (status, practice participation) | 2009+ | Reports publish Wed–Fri; respect report dates (Section 6.2). |
| `load_depth_charts` | Depth charts | 2001+ | **Format changed from 2025: rows are timestamped, not assigned to a week.** Handle both schemas. |
| `load_rosters_weekly` / `load_rosters` | Rosters, positions, status, birth dates, draft info | 2002+ (weekly) | Positions and ages. |
| `load_nextgen_stats` | NGS passing/rushing/receiving (e.g., separation, rush yards over expected) | 2016+ | Season/week aggregates only. |
| `load_pfr_advstats` | PFR advanced stats (pressures, broken tackles, drops…) | 2018+ | Optional features. |
| `load_ftn_charting` | FTN charting (play-action, motion, RPO, blitz…) | 2022+ (charted within ~48h) | Optional; short history. License: check attribution/share-alike terms. |
| `load_participation` | Players on field, formation, coverage, pass rushers | 2016+ | **Only published after each season ends** → never available in-season. Offseason modules only. |
| `load_ff_opportunity` | Expected fantasy points (ffopportunity model): weekly and per-play (`pbp_pass`, `pbp_rush`) | 2006+ (verify) | Model output (Section 6.3). Check in-season update timing. |
| `load_ff_playerids` | DynastyProcess cross-platform ID map (`gsis_id`, `espn_id`, `pfr_id`, `sleeper_id`…) | current | **Required** for joining snap counts (PFR IDs) and ESPN players. |
| `load_ff_rankings` | FantasyPros expert consensus rankings via DynastyProcess (draft, weekly, historical archive) | varies | Baselines ("model vs experts") and the Waiver Radar "likely available" proxy. |
| `load_draft_picks`, `load_combine` | Draft capital, college, combine measurements | 1980+ / 2000+ | Breakout Board features. |
| `load_contracts` | OverTheCap contracts | varies | Optional Cliff Board feature. |
| `load_players`, `load_teams` | Player and team metadata | — | Team colors (fine to use); **do not use logo URLs in the public app.** |

Storage expectation: full raw nflverse history is a few GB → keep it **local** (Parquet + DuckDB). Only aggregated outputs go to Neon.

### 4.2 Manually curated data

- `data/manual/coach_departures.csv` — one row per head-coach departure since 1999, hand-labeled from Pro Football Reference coaching pages and Wikipedia. Columns: `coach_name`, `team`, `last_season`, `last_game_id` (from schedules), `departure_type` ∈ {`fired_in_season`, `fired_after_season`, `mutual_parting`, `resigned`, `retired`, `left_for_other_job`, `interim_not_retained`, `health_or_death`, `other`}, `announced_date`, `source_url`, `notes`. Expect ~150–200 rows; the LLM generates the candidate list automatically from schedule coach changes, and the **owner verifies/labels** it (budget 3–4 hours). Store the labeling guideline in `docs/labeling_coaches.md`.

### 4.3 ESPN Fantasy (optional, local-only)

- Library: `espn-api` (`github.com/cwendt94/espn-api`), football module: `League(league_id, year, espn_s2=None, swid=None)`. ESPN's fantasy API is **unofficial and undocumented** and can change without notice — isolate every call behind `src/twm/league/espn_client.py` so breakage is contained.
- Public league: only `ESPN_LEAGUE_ID` + `ESPN_YEAR` needed. Private league: the owner also adds `ESPN_S2` and `ESPN_SWID` to `.env` (they know how to get them from their browser's cookies). ⚖️ Decision Point: confirm with the owner whether the league is public or private.
- Used calls (verify names in the library): league settings (scoring, roster slots, number of teams), `teams` and rosters, `free_agents(week, size, position)` (current season only), `box_scores(week)` (starters/bench with actual and ESPN-projected points), `recent_activity()` (transactions).
- Player join: ESPN player ID → `espn_id` in `load_ff_playerids` → `gsis_id`. Report unmatched players.

---

## 5. Concepts the owner should learn (explain in docs, notebooks and UI tooltips)

- **EPA (expected points added)** — how much a play changed the expected points of the drive.
- **Win probability (WP) and WPA** — chance of winning from a game state; change caused by a play or decision.
- **Snap share, target share, air-yards share, WOPR** — measures of *opportunity* (WOPR = 1.5 × target share + 0.7 × air-yards share).
- **Expected fantasy points (xFP) and fantasy points over expected (FPOE)** — opportunity valued at league-average efficiency; the gap is efficiency (partly skill, largely noise).
- **Regression to the mean** — why extreme efficiency rarely lasts; why opportunity is "stickier" than efficiency.
- **Garbage time / neutral situations** — plays where the game is effectively decided distort stats.
- **Implied team total** — points a team is expected to score from the betting spread and total.
- **PPR scoring, starters vs flex, bye weeks, waivers/FAAB** — fantasy mechanics.
- **Pythagorean expectation / wins vs expectation** — whether a team's record matches its underlying performance.
- **Fourth-down and two-point decision theory** — comparing WP after each option.

---

## 6. Point-in-time rules (hard requirement)

### 6.1 As-of timestamps

Every prediction has an `as_of` timestamp (UTC). Every source row must have an `available_at` timestamp (actual or conservatively estimated). **A prediction may only use rows with `available_at ≤ as_of`.** Implement a single `asof_filter(df, as_of)` utility used by every feature builder, and unit-test it.

Default as-of points (config values in `config/settings.yaml`):

| Module | Official as-of | Rationale |
|---|---|---|
| Waiver Radar, Regression Watch, My League | **Tuesday 14:00 UTC after week N** | Monday Night Football is final; before most ESPN waivers process (Wednesday). |
| Decision Report Card | Tuesday 14:00 UTC after week N | Grades week N's decisions. |
| Hot-Seat Meter | Tuesday 14:00 UTC after week N; plus an "end of regular season" snapshot the day after Week 18 games before any announcements | Firings are often announced Black Monday morning. |
| Cliff & Breakout Board | Two snapshots: **post-draft (June 1)** and **preseason (the Tuesday before Week 1)** | Rosters/depth charts change over the offseason. |

Estimated availability for historical backtests (document all in `docs/assumptions.md`):

- Play-by-play and player stats for week N: available by the Tuesday as-of (nflverse updates nightly).
- Snap counts: verify typical lag; if uncertain, assume available by the Tuesday as-of for backtests and record the real live lag.
- Injury reports for week N+1: **not** available at the Tuesday as-of (they publish Wednesday–Friday). At the Tuesday as-of you may use week-N final reports and game statuses (who played, who was inactive, who went on IR by the report date).
- Depth charts: pre-2025, week-assigned → treat week N+1's chart as available only at the start of week N+1 (so at the Tuesday as-of use week N's chart). 2025+: use timestamped rows ≤ `as_of`.
- Participation data: available only after the season → **never** used for in-season modules.

### 6.2 Leakage rules

1. Features at `(entity, as_of)` use only data with `available_at ≤ as_of`.
2. **Walk-forward by season:** to evaluate season S, train on seasons < S only (and for in-season modules, you may also include weeks of season S that are strictly before the as-of *only if* the model is refit weekly — default: no in-season refit). Hyperparameters tuned using the last training season as validation.
3. Labels must be built from outcomes strictly after `as_of`; unit-test boundary cases.
4. All preprocessing (imputation, scaling, winsorizing) is fit on training folds only (scikit-learn `Pipeline`).
5. No player/coach/team identifiers as model features.
6. Feature-importance smoke test after each training run; investigate any suspiciously dominant feature.

### 6.3 Model-derived source columns

`epa`, `wp`, `vegas_wp`, `cpoe`, `xpass`, `xyac_*` (nflfastR models) and `load_ff_opportunity` expected values were produced by models trained on many seasons, including seasons *after* some backtest weeks. This is a mild, known leakage. Policy:

- Allowed as features in P1 for speed; **documented** as a limitation on the Methodology page.
- In P2, the Decision Report Card uses **its own** WP model trained walk-forward (Section 8.4), and the xFP used by Regression Watch is re-estimated walk-forward in P2 (8.2). Compare backtest results with and without the nflverse model columns and report the difference.

### 6.4 Betting lines

`spread_line`/`total_line` in schedules are **closing** lines (set right before kickoff). At a Tuesday as-of, next week's closing line does not exist yet.

- Using lines for **already-played** games (e.g., "wins vs market expectation to date") is fine.
- Using **upcoming** games' closing lines at a Tuesday as-of is leakage. P1 must not do it; use opponent strength to date instead. P2 may add "upcoming implied total" as a flagged experimental feature evaluated both with and without, clearly labeled.

---

## 7. Shared definitions

### 7.1 Fantasy scoring

`config/scoring.yaml`, default **full PPR**: passing 0.04/yd, 4/pass TD, −2/INT; rushing and receiving 0.1/yd, 6/TD, 1/reception; −2/fumble lost; 2/two-point conversion. When ESPN is connected, load the league's actual scoring settings and override. Always compute fantasy points from stat components with this config (never trust a precomputed column blindly — use it only as a cross-check).

Positions in scope: QB, RB, WR, TE (P1). K and D/ST are a P3 stretch (Section 8.6b).

### 7.2 League shape and "starter" thresholds

`config/league.yaml`, default 12 teams, lineup 1 QB, 2 RB, 2 WR, 1 TE, 1 FLEX (RB/WR/TE), bench 7 (verify against ESPN when connected). Weekly "starter" threshold per position = teams × starters at that position (+ flex share): default QB top-12, RB top-24, WR top-24, TE top-12; FLEX-worthy = RB/WR top-36. Thresholds derive from config, not hard-coded.

### 7.3 Garbage time and neutral situations

- **Garbage-time play:** `wp` (possession team) < 0.05 or > 0.95 at snap, excluding the final 2 minutes of a half when the score is within one possession — configurable; also offer the stricter rbsdm-style **neutral filter** (0.20 ≤ `wp` ≤ 0.80 and > 120 seconds left in the half).
- Store a boolean per play so every stat view can toggle filtered/unfiltered.

### 7.4 Implied team totals (only for played games in P1)

If verified that positive `spread_line` = home team favored: home implied = (`total_line` + `spread_line`) / 2; away implied = (`total_line` − `spread_line`) / 2. **Verify the sign convention first.**

### 7.5 IDs

Canonical player key: `gsis_id`. Maintain `dim_player` joined from rosters + `load_ff_playerids` (PFR, ESPN, Sleeper, FantasyPros IDs). Unmatched-ID report generated on every build.

---

## 8. Modules

### 8.1 Waiver Radar (P1)

**Problem.** At each Tuesday as-of during the season, rank players who are probably available in a typical 12-team league by their probability of becoming a fantasy starter in the next 3 weeks.

**Candidate pool.**
- Without ESPN (public app and backtests): "likely available" proxy = players outside the top-N at their position by **both** preseason ADP/ECR (`load_ff_rankings`) and season-to-date PPG, where N ≈ 1.5 × league starters at that position (configurable). Document that historical rostership data does not exist, so backtests use this proxy.
- With ESPN (My League): the actual free-agent list for the owner's league.

**Labels.**
- `y_hit` = 1 if the player finishes at or above the starter threshold (7.2) in **at least one** of weeks N+1..N+3 in which he plays.
- `y_sustained` = 1 if he does so in **at least two** of those weeks.
- Bye weeks: if one of the three weeks is a bye, extend the window by one week (document).
- Rows in the final 3 weeks of the season: shorten the window and flag; exclude weeks with <2 remaining games from training.

**Features (examples; each registered with an explanation).**
- Opportunity: snap share (last week, last 3 weeks, change), target share, air-yards share, WOPR, carry share, red-zone opportunities (targets/carries inside the 20 and 10), routes proxy (pass snaps × snap share where route data is unavailable), xFP last 1/3 weeks.
- Efficiency (weighted low by design): FPOE last 3 weeks.
- Role change: depth-chart position change; teammate at same position ruled out / placed on IR / traded (injury report + rosters); team's vacated opportunity (last-3-week target/carry share of now-unavailable teammates).
- Context: team offensive EPA/play to date, pace (plays per game), opponent defense strength vs position to date for the next 3 opponents (no upcoming lines in P1), bye in window, games remaining.
- Player: position, age, rookie flag, draft round, season-to-date games played.

**Models.** Baselines: (1) rank by last week's fantasy points; (2) rank by snap-share change; (3) FantasyPros weekly ECR where available. Main: regularized logistic regression → LightGBM (P1 ends with whichever wins walk-forward); calibrate with isotonic regression on the validation season.

**Evaluation.** Walk-forward seasons 2014–2025 (2012–2013 used for training only because snap counts start in 2012). Metrics per position and pooled: **precision@10 per week**, hit rate by rank bucket (1–5, 6–10, 11–25), PR-AUC, Brier, calibration curve; "breakouts caught": share of players who became weekly starters from outside the proxy pool that the Radar ranked top-10 at least once beforehand. Always report number of weeks and positives.

**Output (per Tuesday).** Ranked list per position with probability, confidence band, 3 plain-English reasons (e.g., "Snap share jumped from 41% to 78% after the starter went on IR"), and "suggested priority" (must-add / speculative / watch).

### 8.2 Regression Watch (P1, upgraded in P2)

**Problem.** For fantasy-relevant players, separate production into **opportunity** (xFP) and **efficiency** (FPOE), estimate how much of the efficiency is likely to persist, and tag players as *Sell-high*, *Buy-low* or *Legit*.

**Method.**
- P1: xFP from `load_ff_opportunity` components, re-scored with `config/scoring.yaml`.
- P2: own walk-forward xFP models (per target: expected receptions/yards/TDs from air yards, yardline, down/distance, pass location; per carry: expected yards/TDs from yardline, down/distance, box proxy where available). Compare with ffopportunity.
- Stability study (required, shown on the Methodology page): for player-seasons with ≥ 8 games, split-half correlation of xFP/game vs FPOE/game, and of components (TD rate over expected, catch rate over expected, YAC over expected). Expected finding: opportunity is far stickier than efficiency — report whatever the data says.
- Rest-of-season projection: PPG_ros = xFP/game (recency-weighted) + shrinkage × FPOE/game, where the shrinkage factor is estimated from the stability study (empirical Bayes by position and sample size).
- Tags: *Sell-high* if FPOE/game is in the top decile and the projection is ≥ X points/game below current PPG; *Buy-low* the mirror; *Legit* if high PPG is supported by xFP. Thresholds set from backtests, not intuition.
- Garbage-time toggle: show PPG with and without garbage-time plays.

**Evaluation.** Walk-forward: at each as-of week (e.g., after Weeks 4, 6, 8, 10), compare projected rest-of-season PPG to actual; baselines = season-to-date PPG and last-3-week PPG. Metrics: MAE, rank correlation, and hit rate of tags (did Sell-high players actually decline?).

### 8.3 My League — ESPN (P1 stretch, optional, local-only)

**Features.**
1. **Personalized Waiver Radar**: the Radar re-run on the league's actual free agents with the league's actual scoring and roster settings; shows who on the owner's roster would be the drop candidate (lowest rest-of-season projection at a position with depth).
2. **Lineup regret**: for each past week, compare the owner's points to (a) the hindsight-optimal lineup and (b) the "decision-time optimal" lineup using projections available before kickoff (ESPN projected points from box scores). This separates bad luck from bad decisions. Season-to-date totals and a per-week table.
3. **Trade checker**: CLI/local page where the owner enters a proposed trade; compares rest-of-season projections (8.2) for both sides, positional scarcity for the owner's lineup, bye-week overlap, and Regression Watch tags; outputs an expected-points delta with an uncertainty range and plain-English reasoning. It does **not** claim certainty.
4. **Weekly league report**: a local HTML report (`reports/league/<season>-W<week>.html`) combining the above.

**Isolation & privacy.**
- Enabled only when `ENABLE_MY_LEAGUE=true` and ESPN env vars exist; the public build never sets these.
- Runs as CLI (`twm league sync`, `twm league report`) and/or a local-only Next.js route that returns 404 unless the env flag is set **and** `NODE_ENV=development`.
- League data is stored in a separate local DuckDB file (`data/league.duckdb`), never published to Neon.
- Tests use synthetic fixtures (fake team names, no real cookies).
- If ESPN calls fail, the module prints a clear message and the rest of the app is unaffected.

### 8.4 Decision Report Card (P2)

**Components.**
1. **Own win-probability model** (walk-forward by season): LightGBM classifier on play states → possession team wins. Features: score differential, game seconds remaining, half seconds remaining, half indicator, down, distance, yardline, timeouts (both teams), receives second-half kickoff, home, pregame spread (from closing line — allowed, because pregame lines are known at kickoff), era indicator (e.g., 2015 PAT rule change, 2023+ kickoff rules if relevant). Monotonic constraints where logical (score differential, time × lead interactions handled by the model). Validate calibration on held-out seasons and compare with nflfastR's `wp`/`vegas_wp`.
2. **Fourth-down grading**: for each fourth down (exclude end-of-half kneels/desperation and plays with pre-snap penalties; include fake punts/FGs as "go"):
   - Go: P(convert | distance, yardline, context) model → WP(success state) and WP(failure state).
   - Field goal: P(make | distance, roof, wind, temperature, era) → WP(make) / WP(miss).
   - Punt: expected opponent starting field position by punting yardline (empirical distribution, touchbacks) → WP.
   - Recommended option = max WP; **WP lost** = WP(best) − WP(chosen). Only grade "clear" decisions where the gap between best and second-best exceeds a configurable margin (e.g., 1.5 WP points); report the rest as "toss-ups".
3. **Two-point decisions**: after each touchdown, WP(kick) vs WP(go for 2) using era-appropriate league conversion rates (PAT rates changed after the 2015 rule change).
4. **Clock management (limited, well-defined metrics only)**:
   - Timeouts unused in lost one-score games where the team was on defense in the final 2:00 with the opponent able to run clock.
   - End-of-half passivity: kneels/run-outs with ≥ 40 seconds, ≥ 1 timeout, and field position where the expected points of attacking are clearly positive (use the EP implied by the WP model or a simple EP model).
   - Late-game timeout usage when trailing: seconds wasted vs an optimal-use heuristic (document the heuristic).
   Each metric has a written definition in `docs/decision_metrics.md`; do not grade situations outside those definitions.
5. **Attribution**: decisions are credited to the team's head coach for that game (`home_coach`/`away_coach`).

**Benchmark.** ⚖️ Optional: compare fourth-down recommendations with `nfl4th` (R package) for one or two seasons via a small R script; report agreement rate. If R is not wanted, compare aggregate league go-rates with public figures.

**Outputs.** Per coach per week and season: decisions graded, WP lost, aggressiveness index (go rate when "go" was recommended), a list of the worst calls with context, league leaderboard.

### 8.5 Hot-Seat Meter (P2)

**Unit.** Head coach × team × week, seasons 2002+ (32-team era; 1999–2001 optional).

**Labels.**
- Positive departures: `fired_in_season`, `fired_after_season`, `mutual_parting` (⚖️ Decision Point: include `resigned` under pressure? default: no, report sensitivity).
- `y` = 1 if a positive departure is announced after `as_of` and no later than 30 days after the team's final game of that season.
- Interim head coaches: flagged `is_interim`; excluded from training (report them separately).
- Non-positive departures (retired, health, left for another job): `y = 0` with `censored = True`; sensitivity analysis with them dropped.
- Derive candidate departures automatically from coach-name changes in `load_schedules` (mid-season change → in-season departure; change between seasons → offseason departure), then join the owner-verified `coach_departures.csv`.

**Features.**
- Performance vs expectation: wins − market-expected wins to date (sum of pregame win probabilities from closing moneylines/spreads of *played* games), point differential per game, Pythagorean wins − actual wins.
- Underlying quality: offensive and defensive EPA/play (neutral situations), trend over the last 4 weeks.
- Context: tenure (seasons), first-/second-year coach flags, previous season wins and playoff result, consecutive losing seasons, division rank, games remaining, starting-QB changes, rookie first-round QB on roster (patience factor), coordinator changes if derivable (optional).
- P2 link: season-to-date Decision Report Card WP lost.

**Models.** Penalized logistic regression and a discrete-time hazard model (week-level); LightGBM only if it beats them in walk-forward. Calibrate. Keep the model small — the sample is small.

**Evaluation.** Walk-forward by season (2006–2025). Metrics: at each as-of week and at the end-of-regular-season snapshot — ROC-AUC, PR-AUC, Brier, "top-5 hit rate" (how many of the season's firings were in the model's top 5 at Week 12 and at season end), with bootstrap CIs by season. Always show number of firings per season.

**Research question (write-up required).** Controlling for performance vs expectation, does poor decision quality (8.4) raise firing risk? Report the coefficient with CI and interpret honestly (a null result is a valid result).

### 8.6 Cliff & Breakout Board (P3)

**Cliff (veterans).**
- Population: players with ≥ 3 prior seasons who finished top-36 at their position in PPG (≥ 8 games) in season S.
- `y_cliff` = 1 if PPG in S+1 drops ≥ 30% vs S (with ≥ 6 games played in S+1). Players with < 6 games in S+1 → separate label `y_missed` (injury/benching), modeled separately or treated as censored — ⚖️ Decision Point.
- Features: age and position-specific age curve, career touches/targets, workload in S (e.g., 350+ touches for RBs), efficiency trend over 2–3 seasons (yards per touch, rush yards over expected and separation from NGS 2016+), snap-share trend, FPOE in S (regression risk), team changes (new head coach/OC via Hot-Seat data, QB change, depth-chart competition added — as of the snapshot date), contract status (optional).

**Breakout (second/third-year WR/TE, optionally RB).**
- Population: players entering their 2nd or 3rd season who were **not** top-36 (WR) / top-12 (TE) / top-24 (RB) in PPG the previous season.
- `y_breakout` = 1 if they finish top-24 WR / top-12 TE / top-24 RB in PPG (≥ 8 games) in the next season.
- Features: rookie target share, yards per team pass attempt, air-yards share, xFP/game, draft round and pick, age at draft, combine athletic metrics (speed score, etc.), team vacated targets (as of the snapshot), QB quality.

**Models & evaluation.** Logistic regression + LightGBM; walk-forward by season (labels 2008–2025; NGS features only from 2016). Baselines: prior-season PPG rank; preseason ADP. Metrics: PR-AUC, precision@10 and @20, calibration; compare to preseason ADP to show where the model disagrees with the market and who was right.

### 8.6b Stretch: K and D/ST streamer (P3, optional)

Weekly kicker and defense streaming ranks for ESPN leagues. Kicker: field-goal make-probability model (distance, wind, temperature, roof, altitude, surface) × expected attempts (team red-zone stall rate, implied total if available with the Section 6.4 caveat). D/ST: opponent sack/turnover rates and implied total. Bonus: "kickers over expected" leaderboard.

### 8.7 Cross-cutting: Time Machine and Track Record

- Every scored prediction is stored in `predictions` (`module`, `entity_type`, `entity_id`, `season`, `week`, `as_of`, `horizon`, `score`, `rank`, `band`, `model_version`, `reasons_json`).
- When outcomes resolve, a job fills `outcomes` and computes per-module metrics into `track_record` (by season and for the live season).
- **Time Machine** UI: pick a season and week (2012+ for fantasy modules, 2006+ for Hot-Seat) → view what each module said then, with outcomes overlaid.
- Backtest predictions and live predictions are both stored and **visually distinguished** (live = made in real time; backtest = reconstructed).

---

## 9. Architecture

```
nflverse (nflreadpy) ─┐
DynastyProcess ranks ─┼─► Python pipeline (twm) ─► local Parquet + DuckDB warehouse
manual coach CSV ─────┘        │                                  │
                               ├─ features (as-of) ─ models ─ predictions/outcomes
                               │
                               ├─► publish (aggregates only) ─► Neon Postgres ─► Next.js on Vercel (public)
                               │
ESPN (optional) ─► twm league ─► data/league.duckdb ─► local report / local-only route (never published)
```

- **Pipeline:** Python 3.12, `uv`, `nflreadpy`, `polars` (pandas only where a library requires it), `duckdb`, `pyarrow`, `scikit-learn`, `lightgbm`, `statsmodels`, `shap` (optional), `typer` (CLI), `pydantic` (config), `httpx`/`tenacity` where needed, `pytest`, `ruff`. Optional extra `[espn]`: `espn-api`.
- **CLI:** `twm ingest`, `twm build`, `twm train <module>`, `twm backtest <module>`, `twm score --as-of <timestamp|season-week>`, `twm publish`, `twm league sync`, `twm league report`, `twm doctor` (checks data freshness, ID coverage, env).
- **Scheduling:** ⚖️ Decision Point — GitHub Actions cron (recommended: runs while the owner's Mac sleeps) vs a local launcher like the owner's F1 nightly push. In season: nightly ingest + score; the Tuesday 14:00 UTC run is the official weekly snapshot. Offseason: weekly. Retraining is a separate, manually triggered workflow with a review step.
- **Postgres (Neon):** publish only aggregates: `dim_player`, `dim_coach`, `dim_team`, `predictions` (current + backtest, trimmed to needed columns), `outcomes`, `track_record`, `player_week_summary` (for charts), `coach_decisions` (graded decisions), `coach_week_summary`, `model_versions`, `pipeline_runs`. Target < 200 MB; check the current Neon free-plan limit.
- **Web:** Next.js (App Router, TypeScript), Tailwind, server components reading Postgres (⚖️ Drizzle vs raw SQL via the Neon serverless driver), charts with Recharts or visx (⚖️). No Python on Vercel.

### 9.1 Web pages (P3 final state; P1/P2 ship subsets)

| Route | Content |
|---|---|
| `/` | "This week": top Radar adds, biggest Regression flags, coach of the week (best/worst decision), Hot-Seat top 5 (in season) or Board highlights (offseason) |
| `/waivers` | Waiver Radar by position; filters; reasons; hit-rate badge per rank bucket |
| `/regression` | Sell-high / Buy-low / Legit tables; xFP vs actual scatter; garbage-time toggle |
| `/decisions` | Decision Report Card leaderboard; worst calls; per-coach drill-down |
| `/hot-seat` | Probability bars per coach with weekly timeline; drivers |
| `/board` | Cliff & Breakout boards; model vs ADP disagreements |
| `/player/[id]` | Player timeline: snaps, shares, xFP vs FP, Radar/Regression history |
| `/coach/[id]` | Coach timeline: decisions, hot-seat history, record vs expectation |
| `/time-machine` | Season/week picker across modules with outcomes |
| `/track-record` | Per-module backtest + live metrics, calibration plots, seasons table |
| `/methodology` | Data sources, as-of rules, leakage notes (incl. 6.3), stability study, definitions, glossary, disclaimers |
| `/league` (local only) | My League (ESPN) — 404 in production |

UI requirements: beginner-friendly tooltips for every metric (from the feature/metric registry), responsive layout, accessible charts, light/dark mode, loading/empty/error states, "data as of" timestamp on every page, attribution footer (nflverse, FTN if used, DynastyProcess/FantasyPros for rankings), and the disclaimer.

---

## 10. Prototypes

### Prototype 1 — Fantasy Core (target: usable by ~Week 7 of the 2026 season)

Features:
1. Repo scaffold, config files (`settings.yaml`, `scoring.yaml`, `league.yaml`), `twm` CLI, CI (lint + tests).
2. nflverse ingestion 1999–2026 with caching; data dictionary checks; `docs/assumptions.md`.
3. Point-in-time layer: `available_at` on every table, `asof_filter`, tests.
4. `dim_player` ID map with unmatched-ID report.
5. Fantasy scoring engine from components + tests; garbage-time/neutral flags on plays.
6. Waiver Radar: candidate-pool proxy, labels, features, baselines, logistic + LightGBM, calibration, walk-forward 2014–2025, weekly scoring for 2026.
7. Regression Watch (P1 version): ffopportunity-based xFP, stability study, rest-of-season projection, tags, backtest.
8. `predictions`/`outcomes` storage for both modules (time-machine-ready).
9. Minimal public web app: `/`, `/waivers`, `/regression`, `/player/[id]`, `/methodology` (draft), deployed on Vercel reading Neon.
10. Scheduled pipeline producing the Tuesday snapshot automatically.
11. **Stretch (optional):** My League (ESPN) — personalized Radar, lineup regret, weekly local report. Trade checker may slip to P2.

Acceptance criteria:
- [ ] `twm build` from cache is deterministic (identical outputs on re-run).
- [ ] As-of, label-boundary, scoring and garbage-time unit tests pass; a deliberately leaky feature fails the as-of test.
- [ ] Waiver Radar beats both naive baselines on pooled walk-forward precision@10 (report the numbers either way).
- [ ] Regression Watch rest-of-season projection beats season-to-date PPG on MAE in walk-forward.
- [ ] The Tuesday snapshot publishes automatically for at least two consecutive live weeks.
- [ ] With `ENABLE_MY_LEAGUE` unset, the full test suite and web build pass; with it set (locally), the league report generates.

### Prototype 2 — Coach Core (target: early December 2026, before Black Monday)

Features:
1. Walk-forward win-probability model + calibration report vs nflfastR WP.
2. Conversion, field-goal and punt sub-models.
3. Fourth-down and two-point grading; clock-management metrics as defined.
4. Decision Report Card pages (`/decisions`, `/coach/[id]`).
5. Candidate coach departures generated from schedules; owner-verified `coach_departures.csv`.
6. Hot-Seat Meter: labels, features, models, walk-forward 2006–2025, weekly live scoring, end-of-season snapshot; `/hot-seat` page.
7. Research write-up: decision quality vs firing risk.
8. Regression Watch upgrade: own walk-forward xFP; comparison vs ffopportunity; leakage comparison (6.3).
9. My League: trade checker (if not done in P1).
10. `/track-record` page (first version) for all shipped modules.

Acceptance criteria:
- [ ] WP model is well calibrated on held-out seasons (reliability curve + Brier reported) and comparable to nflfastR WP.
- [ ] Every graded decision stores its inputs so any grade can be reproduced.
- [ ] Hot-Seat walk-forward metrics reported with CIs and firings-per-season counts; end-of-season snapshot for 2026 published before Black Monday.
- [ ] Decision-quality research question answered in `docs/research_decisions_vs_firings.md`.

### Prototype 3 — Final (target: August 2027, before 2027 fantasy drafts)

Features:
1. Cliff & Breakout Board (post-draft and preseason snapshots), `/board` page, model-vs-ADP view.
2. Time Machine across all modules (`/time-machine`), backtest vs live distinction.
3. Track Record completed with the full 2026 live season results.
4. Full Methodology page: as-of rules, leakage notes, stability study, WP model card, glossary.
5. Model cards per module (`docs/model_cards/*.md`).
6. Polish: accessibility pass, responsive checks, performance (Lighthouse ≥ 90 on `/`), SEO metadata, error/empty states, attribution + disclaimers.
7. Offseason retraining workflow including 2026 data.
8. Stretch: K/D-ST streamer (8.6b); FTN charting features (2022+) as experimental Radar features.

Acceptance criteria:
- [ ] Time Machine for any backtest week reproduces the stored predictions exactly (no recomputation drift).
- [ ] Every number in the UI traces to a table row and a `model_version`.
- [ ] Track Record shows backtest and 2026 live results for every module.
- [ ] README includes architecture diagram, setup, results summary (honest, including failures) and live link.

---

## 11. Step-by-step implementation plan

Commit after each step. Steps marked 🧑 need the owner.

### Phase A — Setup (P1)
1. **A1. Scaffold:** `uv` project, `src/twm/`, `ruff`, `pytest`, `pre-commit`, `.gitignore` (ignore `data/`, `.env`, `models/`, `reports/league/`), `.env.example` (`DATABASE_URL`, `ENABLE_MY_LEAGUE`, `ESPN_LEAGUE_ID`, `ESPN_YEAR`, `ESPN_S2`, `ESPN_SWID`), config YAMLs, GitHub Actions CI.
2. **A2. Ingestion:** `src/twm/sources/nflverse.py` wrapping each loader with caching and schema snapshots (column names + dtypes saved to `data/schemas/`), so upstream schema changes are detected.
3. **A3. Verification:** script that checks every loader, coverage year and column this spec relies on; writes `docs/assumptions.md` with findings (including `spread_line` sign, depth-chart format change, snap-count ID type, ff_opportunity update timing). Report discrepancies to the owner.

### Phase B — Warehouse & shared logic (P1)
4. **B1.** DuckDB warehouse: fact tables (`fact_play`, `fact_player_week`, `fact_snaps`, `fact_injury_report`, `fact_depth_chart`, `fact_game`) and dimensions (`dim_player`, `dim_team`, `dim_coach`, `dim_week` with official as-of timestamps).
5. **B2.** `available_at` derivation per table + `asof_filter` + tests.
6. **B3.** ID map build + unmatched report.
7. **B4.** Scoring engine + tests (hand-computed examples for QB/RB/WR/TE weeks).
8. **B5.** Garbage-time and neutral flags + tests.
9. **B6.** Feature/metric registry (`src/twm/registry.py`): name, module, formula description, beginner-friendly explanation, unit.

### Phase C — Waiver Radar (P1)
10. **C1.** Candidate-pool proxy (rankings join) + report of pool sizes per week.
11. **C2.** Labels (`y_hit`, `y_sustained`) + boundary tests (byes, season end).
12. **C3.** Features (as-of filtered) + tests for teammate-injury and vacated-opportunity logic.
13. **C4.** Baselines + logistic + LightGBM; walk-forward harness `src/twm/backtest/` shared by all modules.
14. **C5.** Metrics & plots; notebook `01_waiver_radar.ipynb`.
15. **C6.** Weekly scoring + reasons generator (top feature contributions → plain-English templates from the registry).

### Phase D — Regression Watch (P1)
16. **D1.** xFP from ffopportunity re-scored; FPOE.
17. **D2.** Stability study + shrinkage estimates; notebook `02_regression_stability.ipynb`.
18. **D3.** Rest-of-season projection + tags + backtest.

### Phase E — Publish & web (P1)
19. **E1.** 🧑 ⚖️ Confirm Neon project, ORM/driver, chart library, scheduler choice.
20. **E2.** `twm publish` (aggregates only) + Postgres schema migrations.
21. **E3.** Next.js app: layout, disclaimer/attribution footer, `/`, `/waivers`, `/regression`, `/player/[id]`, draft `/methodology`.
22. **E4.** Scheduled workflow (nightly + Tuesday snapshot), run log, failure notification (GitHub Actions email is enough).
23. **E5.** Deploy; verify two live Tuesdays.

### Phase F — My League, ESPN (P1 stretch)
24. **F1.** 🧑 Owner confirms public/private and fills `.env` locally.
25. **F2.** `espn_client.py` (isolated), `data/league.duckdb`, `twm league sync`, synthetic-fixture tests.
26. **F3.** Personalized Radar + drop candidates; lineup regret (hindsight vs decision-time).
27. **F4.** Weekly local HTML report; optional local-only `/league` route guarded by env + `NODE_ENV`.

### Phase G — Decision Report Card (P2)
28. **G1.** Walk-forward WP model + calibration notebook `03_wp_model.ipynb`.
29. **G2.** Conversion / FG / punt sub-models.
30. **G3.** Fourth-down + two-point grading with stored inputs; `docs/decision_metrics.md`.
31. **G4.** Clock-management metrics.
32. **G5.** ⚖️ Optional `nfl4th` benchmark.
33. **G6.** Pages `/decisions`, `/coach/[id]`.

### Phase H — Hot-Seat Meter (P2)
34. **H1.** Auto-generate candidate departures from schedules → `coach_departures_candidates.csv`.
35. **H2.** 🧑 Owner labels/verifies (3–4 h) using `docs/labeling_coaches.md`.
36. **H3.** Labels, features, models, walk-forward; notebook `04_hot_seat.ipynb`.
37. **H4.** Live weekly scoring + end-of-season snapshot; `/hot-seat`.
38. **H5.** Research write-up (decisions vs firings).
39. **H6.** Regression Watch own-xFP upgrade + leakage comparison; trade checker if pending; `/track-record` v1.

### Phase I — Final (P3)
40. **I1.** Cliff & Breakout labels, features, models, walk-forward; notebook `05_cliff_breakout.ipynb`.
41. **I2.** Post-draft and preseason snapshots; `/board`.
42. **I3.** Time Machine UI + reproducibility test.
43. **I4.** Track Record complete; model cards; full Methodology page.
44. **I5.** Polish (accessibility, performance, SEO, states).
45. **I6.** Offseason retrain workflow; README; final deploy.
46. **I7.** Stretch: K/D-ST streamer; FTN experimental features.

---

## 12. Repository structure

```
two-minute-warning/
├── PROJECT_SPEC.md
├── README.md
├── pyproject.toml
├── .env.example
├── config/
│   ├── settings.yaml        # as-of times, horizons, thresholds, seasons
│   ├── scoring.yaml         # default full PPR
│   └── league.yaml          # default 12-team lineup
├── src/twm/
│   ├── sources/             # nflverse.py, rankings.py
│   ├── warehouse/           # duckdb schema, builders, available_at
│   ├── asof.py              # asof_filter
│   ├── ids.py               # dim_player / ID map
│   ├── scoring.py
│   ├── situations.py        # garbage time / neutral flags
│   ├── registry.py          # features & metrics with explanations
│   ├── backtest/            # walk-forward harness, metrics, calibration
│   ├── modules/
│   │   ├── waiver_radar/
│   │   ├── regression_watch/
│   │   ├── decisions/       # wp model, sub-models, grading, clock metrics
│   │   ├── hot_seat/
│   │   └── board/           # cliff & breakout
│   ├── league/              # optional ESPN: espn_client.py, personalize.py, regret.py, trade.py, report.py
│   ├── publish/             # Postgres export
│   └── cli.py
├── data/                    # gitignored: raw cache, warehouse.duckdb, league.duckdb, manual/ (manual/ IS committed)
├── models/                  # gitignored artifacts per module/season
├── notebooks/               # 01..05
├── docs/                    # assumptions.md, decision_metrics.md, labeling_coaches.md, model_cards/, research_*.md
├── reports/                 # generated metrics/figures; reports/league/ gitignored
├── tests/
├── web/                     # Next.js app
└── .github/workflows/       # ci.yml, nightly.yml, retrain.yml
```

(Commit `data/manual/coach_departures.csv`; ignore the rest of `data/`.)

---

## 13. Testing strategy

- **Unit:** scoring engine, garbage-time flags, `asof_filter`, every label builder (boundaries: byes, season end, interim coaches, mid-season firings), implied totals, ID mapping, fourth-down state transitions.
- **Leakage tests:** a synthetic "future" feature must be rejected by `asof_filter`; walk-forward harness must refuse to train on the test season.
- **Schema drift tests:** compare current loader schemas with snapshots; fail loudly on missing columns.
- **Golden tests:** a small frozen season slice with known outputs for each module (update deliberately).
- **ESPN:** fully mocked with synthetic fixtures; no network in CI; no real cookies anywhere in the repo.
- **Web:** type-check, lint, build; a smoke test that key pages render with a seeded database.

---

## 14. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Upstream nflverse schema/source changes (e.g., depth charts 2025) | Schema snapshots + drift tests; `docs/assumptions.md`; loaders isolated |
| ESPN unofficial API breaks | Isolated client; optional module; graceful failure; rest of app unaffected |
| Small samples (coach firings, breakouts) | Simple models, bootstrap CIs, counts always shown, honest write-ups |
| Leakage via model-derived columns | Documented; own walk-forward WP/xFP in P2; with/without comparison |
| No historical rostership data | Explicit candidate-pool proxy; clearly stated on Methodology page |
| Scope creep across six modules | Strict prototype order; stretch items clearly marked |
| Timing: season moves on while building | P1 minimal web first; notebooks and CLI are usable before the UI is |
| Trademark / licensing | No official logos; attribution footer; check FTN and other dataset licenses before publishing |

---

## 15. Decision Points (⚖️ — ask the owner)

1. ESPN league public or private? (Determines whether cookies are needed.)
2. Scheduler: GitHub Actions (recommended) vs local launcher like the F1 app.
3. Web data layer: Drizzle vs raw SQL; charts: Recharts vs visx.
4. Hot-Seat: count "resigned under pressure" as positive? (Default no + sensitivity.)
5. Cliff: treat "missed most of next season" as censored or as its own label?
6. Optional `nfl4th` benchmark using R?
7. Include 1999–2001 seasons in Hot-Seat training?
8. Project name.

---

## 16. Attribution, legal and disclaimers

- Data: **nflverse** (credit on every page), **FTN Data** if charting/participation data is used (check license terms), **DynastyProcess / FantasyPros** for rankings and ID maps, **Pro Football Reference** as the underlying snap-count source.
- No NFL or team logos or other trademarks beyond team names and colors in the public app.
- Disclaimer (footer): "Two-Minute Warning is an unofficial, educational project. It is not affiliated with or endorsed by the NFL or ESPN. Predictions are probabilistic and frequently wrong. Not betting advice."
