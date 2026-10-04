# Two-Minute Warning: the website

The public site (PROJECT_SPEC 9.1, Prototype 1): Next.js 16 (App Router, server components),
React 19, Tailwind 4, Drizzle on node-postgres, Recharts. It only **reads** the Postgres
database that `twm publish` fills (docs/deploy.md); it never writes, and nothing on it is
computed from anything but that database.

| route | what it shows |
|---|---|
| `/` | this week: the scoreboard banner, each position's No. 1 ("top of the board"), the track-record headline, the newest live Waiver Radar list's top 5 per position plus a FLEX card, the Streamers card (the newest live K and D/ST lists' top 3), the Regression flags card (the newest live list's top 3 Sell-high and Buy-low) the Coach of the week card (the newest graded week's best call against convention and its worst clear call; only while the current season has graded weeks) the Hot seat card (the top 5 of the current season's newest Hot-Seat list, live if that week has one, else labelled reconstructed) and the Cliff watch card (the top 5 of the newest board by the chance of a Cliff, with the chance of missed time beside it; only while that board is this season's or a later one); an empty state until the first live list exists |
| `/waivers` | one list: position tabs (from the data, plus FLEX), season/week picker (the time machine), live or reconstructed label, chance with its range and meter, priority, reasons, outcomes, hit-rate badges per rank bucket |
| `/waivers?pos=K`, `?pos=DST` | the K and D/ST streamer (tabs after FLEX once `stream_list` has lists): the same row layout, next game (home/away), chance of a top-N week with its range, priority, reasons, outcome (next week's finish and points); explained once (a one-week pick, no betting lines by design, the backtest in one sentence from `stream_track_record`); its own time machine |
| `/regression` | Regression Watch: the week's Sell-high and Buy-low tables, a with/without garbage-time toggle (links, `gt=off`), the xFP-against-PPG scatter with its data table, the track record (MAE against season PPG and last 3; tag hit rates against base rates, and one sentence on the third tag that was tested and dropped); time machine over the published weeks |
| `/player/[id]` | a player (nflverse `gsis_id`): header, weekly charts with data tables (the points/xFP chart with a garbage-time toggle), Radar history, Regression Watch history; 404 for an unknown id |
| `/decisions` | the Decision Report Card (step W4): a season picker; the league's season in a few sentences; a short "how it works" box; the coach leaderboard (decisions, clear calls, wrong calls, WP lost per game, aggressiveness, clock cases; least WP lost per game first); toss-ups explained with the season's counts; the worst calls and the best calls against convention, each with the situation in words, every option's WP, what was chosen and what happened; the season's clock cases |
| `/coach/[id]` | a head coach (`dim_coach.coach_id`, the name as a slug): the coach's seasons (table, and a small chart of WP lost per game and aggressiveness by season with its data table), worst calls, best calls against convention, clock cases; the Hot-Seat history (this season's weekly estimates as a chart with its table; each past season's last estimate, rank and outcome); 404 for an unknown id |
| `/hot-seat` | the Hot-Seat Meter (step H4b): a season/week picker over every list (the end-of-season snapshot named so); the default is the newest season's live list, else its newest reconstructed one, labelled; one row per head coach by estimated chance (a whole percent and a bar), the key numbers (record against the market's expected wins, point differential per game, tenure), the three drivers in words with their direction, a sparkline and the season's estimates in words, an interim flag, and what happened (final for reconstructed seasons, pending for live ones); "How to read this" with the early-season check (calibration by season phase and band, aggregated from the published rows), the labels' provenance and the fourth-down research in one sentence |
| `/board` | the Cliff board (step I2c-b): a season picker over every board (2008 on) and position filters; the default is the newest season's live board, else its reconstructed one, labelled; one row per veteran by the chance of a Cliff: the estimated chance of a Cliff and, as its own number, of missed time (whole percents with bars, never added), last season's team, points per game, position rank and games, age, the experts' preseason rank (FantasyPros ECR, 2020 on; "not ranked" when they left him out), the "where we disagree" marker (`lib/board.ts` `disagreements`: our top 10 by the Cliff chance against the 10 players whose ECR rank falls furthest below last season's rank, unranked counted furthest), the three drivers of each chance in words, a link to `/player/[id]`, and what happened (final for past boards, pending for the current one); "How to read this": what each chance means, the timing (the eve of week 1; a live board published earlier from the latest daily depth chart says so in its note), calibration by band (SQL over the reconstructed boards and final outcomes) and the disagreement record 2020-2025 (`board_disagreement`) |
| `/time-machine` | the time machine (step I3b): a season and week picker over the union of every module's published weeks (the board's once-a-season list is the season's "Preseason", week 0); for the chosen week, one section per module with what it said then (the stored lists, never recomputed: the Radar's top 5 per position, the K and D/ST top 5, the top 5 Sell-high and Buy-low, the Hot-Seat top 10, the board's top 10, the week's graded calls with the league totals from `coach_week`, labelled retrospective) and the outcome overlaid as on each module's page, framed and badged live or reconstructed like that page, with a link to its full page for that week; a module that does not cover the week says why, from its own published weeks (`lib/time-machine.ts`), and links the nearest week it covers |
| `/methodology` | data sources, the point-in-time rule, leakage safeguards, how the Radar works, the results, the K and D/ST streamer (results from `stream_track_record`), Regression Watch (the stability study from `regression_stability`: split-half correlations by position with and without garbage time, the parts of efficiency, the shrinkage table r(g); the projection in words, the backtest results, the PROJECT_SPEC 6.3 limit), the Decision Report Card (our WP model against nflfastR and its smoothness limits, one line per sub-model against its simple baseline, the grading rules, the nfl4th benchmark, the clock metrics' definitions in plain words, every honesty note; from `decisions_track_record`), the Hot-Seat Meter (labels and windows, features, the model and why, every model's backtest numbers with intervals, firings per season, calibration by phase, the research result, limits; from the `hot_seat_*` tables), the Cliff board (who is on it, the two labels, the eve-of-week-1 snapshot, both models' features and fits, the backtest with paired differences against last season's PPG rank, the end-of-season snapshot and the experts; Breakout stated from the research rows of `board_track_record`, in words computed from its intervals; limits), the model cards (one link per `docs/model_cards/*.md` on GitHub, from `lib/site.ts` `MODEL_CARDS`; a unit test checks the list against the folder), the full glossary, disclaimers |

Every page carries the skip link, the "Data as of" line (the Tuesday as-of of the current
week's lists, and when the data was published), the disclaimer of PROJECT_SPEC 16 and the data
credits. Every page has a title, a description, a canonical URL and Open Graph basics; until
the owner makes the site public, `robots.txt` disallows everything and every page is `noindex`
(see "Search engines" below).

## Search engines

One place, `lib/seo.ts`, read per request from the environment (no domain is written in the code):

| variable | effect |
|---|---|
| `SITE_URL` | the canonical origin (e.g. `https://example.org`; only its origin is used) for the canonical links, Open Graph URLs and the sitemap. Unset: Vercel's own `VERCEL_PROJECT_PRODUCTION_URL`, else `http://localhost:3000` |
| `SITE_PUBLIC` | `true`: `robots.txt` allows crawling (except `/league`) and names the sitemap, `/sitemap.xml` is served, pages are indexable. Anything else, or unset (the default): `robots.txt` disallows everything, `/sitemap.xml` answers 404 and every page says `noindex, nofollow` |
| `SHOW_THIRD_PARTY_RANKS` | testing only, never in production: `true` / `false` overrides the license switch below |

`/sitemap.xml` (`app/sitemap.ts`, only with `SITE_PUBLIC=true`) lists the nine pages of the main navigation at that origin;
player and coach pages are reached from them. A page's canonical URL is its path without the
query (a picked week or filter is a view of the same page). The site stays private (Vercel
Authentication, `SITE_PUBLIC` unset) until the owner decides; docs/deploy.md step 10.

**FantasyPros' per-player values (license).** Their Terms of Use forbid republishing, so while
`SITE_PUBLIC=true` the site hides them (owner's decision 2026-10-04): one switch,
`showThirdPartyRanks()` in `lib/third-party.ts` (true unless the site is public). It is applied
in the query layer after the data cache (`getBoard` clears `ecrRank`; `getList`, `getLatestLive`,
`getStreamList`, `getLatestStreamLive` drop the reasons quoting their ranks), so no value reaches
a page, a "where we disagree" marker or the RSC payload, and one cache serves both modes. The
published reasons are text only, so those reasons are identified by their registry template
(`lib/third-party-reasons.json`, id = the feature key; `tests/test_third_party_reasons.py` keeps
it in step with `twm.registry`). Each page that hides something says once that "the experts'
consensus ranks are not shown on the public site (license)" (`components/ThirdPartyNote.tsx`).
Our own estimates and the aggregate model-vs-experts comparisons stay in both modes.

## Running it locally

```bash
docker compose up -d twm-postgres      # the local database (repo root, docs/deploy.md)
uv run twm migrate-local && uv run twm publish --target local   # real data into it
cd web && npm ci
npm run dev                            # http://localhost:3000, reads the local database
```

Which database: `DATABASE_URL` if set (Vercel: the read-only role `twm_web`, docs/deploy.md
step 8), else `TWM_LOCAL_DATABASE_URL`, else the docker-compose default (local only; its user
name and password are not secrets). `web/db/url.ts`. A connection string is never logged or
shown on a page.

## How it reads the data

- `db/client.ts`: one node-postgres pool per process (kept on `globalThis`), created on the
  first query. A read during `next build` throws (`BuildTimeReadError`): the build needs no
  database, and CI proves it.
- `lib/queries/*.ts`: typed Drizzle queries, server-only. Every one is wrapped in
  `cached()` (`lib/cache.ts`): Next's data cache for 1 hour in production (one tag, `data`),
  off in development and tests (`NODE_ENV != production`) or with `DATA_CACHE=0`, plus
  React's per-request cache. The cache key includes a fingerprint of the database URL. There
  is **no revalidation hook yet**: a publish shows up within the hour (E4: a route that calls
  `revalidateTag("data", { expire: 0 })` after `twm publish` would make it immediate).
- Pages are `force-dynamic` (set in `app/layout.tsx`).
- Numbers come from the database only. The few method constants the text states (the chance's
  90% range, the 95% intervals, the rank buckets, the Tuesday 14:00 UTC as-of) are in
  `lib/method.ts`, and `tests/unit/method.test.ts` checks each one against the Python code or
  config it comes from.

Queries per page:

| page | queries (lib/queries) |
|---|---|
| every page | `meta.getSiteMeta` (site_meta; the as-of of the current week's radar_list rows), `glossary.getGlossary` (tooltips) |
| `/` | `radar.getLatestLive`, `radar.getListIndex`, `radar.getRadarModel`, `track.getTrackRows(pooled, diff)`, `glossary.getGlossary` (the FLEX note's league shape), `stream.getLatestStreamLive`, `regression.getLatestRegressionLive` |
| `/waivers` | `radar.getListIndex`, `radar.getPositions` (the tabs), `radar.getList(season, week, position, kind)`, `radar.getBucketCounts(position, season)`; FLEX: `radar.getList` for RB, WR and TE, `radar.getFlexBucketCounts(season)`, `glossary.getGlossary`; the tabs also `stream.getStreamPositions` |
| `/waivers?pos=K`/`DST` | `stream.getStreamIndex(position)` (the time machine), `stream.getStreamList`, `stream.getStreamTrack`, `glossary.getGlossary` (the top-N cutoff from `y_start`) |
| `/regression` | `regression.getRegressionIndex`, `regression.getRegressionList`, `regression.getRegressionTrack` |
| `/player/[id]` | `player.getPlayer`, `player.getPlayerSeasons`, `player.getPlayerWeeks(season)`, `radar.getPlayerHistory`, `regression.getPlayerRegressionHistory` |
| `/methodology` | `track.getTrackRows(...)`, `track.getTierStats`, `radar.getRadarModel`, `glossary.getGlossary`, `stream.getStreamTrack`, `stream.getStreamModels`, `regression.getRegressionParams`, `regression.getRegressionTrack`, `regression.getRegressionStability`, `decisions.getDecisionsTrack`, `decisions.getDecisionsMeta` |
| `/decisions` | `decisions.getDecisionSeasons`, `decisions.getDecisionsMeta` (site_meta `decisions_*`), `decisions.getSeasonCoaches(season)`, `decisions.getWorstCalls`, `decisions.getBestCalls`, `decisions.getClockCases` |
| `/coach/[id]` | `decisions.getCoach(id)` (dim_coach + coach_season), `decisions.getDecisionsMeta`, `decisions.getWorstCalls`, `decisions.getBestCalls`, `decisions.getClockCases` |
| `/` (Coach of the week) | `decisions.getDecisionsMeta`, `meta.getSiteMeta`, `decisions.getWorstCalls(season, week)`, `decisions.getBestCalls(season, week)` |
| `/hot-seat` | `hotSeat.getHotSeatIndex`, `hotSeat.getHotSeatList(season, week, kind)`, `hotSeat.getHotSeatTimeline(season)`, `hotSeat.getHotSeatCalibration` (a SQL aggregation of the reconstructed rows and final outcomes, interims left out) |
| `/coach/[id]` (Hot-Seat history), `/` (Hot seat card) | `hotSeat.getCoachHotSeat(id)`, `hotSeat.getHotSeatMeta` (site_meta `hot_seat_latest_*`); `hotSeat.getHotSeatIndex`, `hotSeat.getHotSeatList`, `meta.getSiteMeta` |
| `/board`, `/` (Cliff watch card) | `board.getBoardIndex`, `board.getBoard(season, kind)`, `board.getBoardCalibration` (a SQL aggregation of the reconstructed rows and final outcomes by band; the Cliff only where `y_cliff` is known), `board.getBoardDisagreement`, `meta.getSiteMeta` |
| `/time-machine` | every module's index (`radar.getListIndex`, `stream.getStreamIndex`, `regression.getRegressionIndex`, `hotSeat.getHotSeatIndex`, `board.getBoardIndex`) and lists (`radar.getList`, `stream.getStreamList`, `regression.getRegressionList`, `hotSeat.getHotSeatList`, `board.getBoard`, `decisions.getWorstCalls` / `getBestCalls`), plus `timeMachine.getDecisionWeeks` and `timeMachine.getWeekCalls(season, week)` (`coach_week`) |
| `/methodology`, `/track-record` (board sections) | `board.getBoardTrack`, `board.getBoardModels` (the newest board's two `model_versions` rows), `board.getBoardGraded`, `board.getBoardLive`, `board.getBoardCalibration`, `board.getBoardDisagreement` |
| `/methodology`, `/track-record` (Hot-Seat sections) | `hotSeat.getHotSeatTrack`, `hotSeat.getHotSeatFirings`, `hotSeat.getHotSeatModel`, `hotSeat.getHotSeatCalibration`, `hotSeat.getHotSeatLive` |

The hit-rate badges on `/waivers` count, for the list's position, the picks and hits per rank
in the reconstructed lists of **seasons before the list's own** with final outcomes (so a
past week's badge never uses what came after it). Summed over the four positions for 2026
they equal the track record's `bucket` rows exactly.

**FLEX** (`lib/flex.ts`, unit-tested) merges one week's RB, WR and TE lists (same season, week
and kind): highest chance first, then the model's probability (a tie-break only, never shown),
then the rank in his own list, then RB, WR, TE and the player id; each player once; top 25.
Lists without chances (the 2014 reconstructed lists) are therefore ordered by the model's
probability, and the page says so. The page explains once that each chance is about a starter
finish at the player's own position, quoting the league shape from the published glossary
(`starter_threshold`, `lib/league.ts`; nothing typed in here). Its hit-rate badges rebuild the
FLEX list of every reconstructed week before the list's season and count per FLEX rank, the
same point-in-time rule as a position's badges.

**Positions** (`lib/positions.ts`): the tabs and the home page's cards are the positions present
in `radar_list`, plus FLEX after TE, plus K and D/ST once `stream_list` has lists (their code is
`DST`, shown as "D/ST"), in the order QB, RB, WR, TE, FLEX, K, D/ST; none is shown before it has a
list. A K or D/ST tab reads the streamer's tables and has its own weeks in the time machine.

**Streamer and Regression Watch** (`lib/streamer.ts`, `lib/regression.ts`, unit-tested): the
streamer's one honest sentence compares the published method (the list's model version: the K
model, the D/ST rule) with the best alternative of the other kind from `stream_track_record`'s
pooled precision@5 rows and its paired `diff` row; the top-N of "a starter week" is read from the
glossary's `y_start` formula. Regression Watch's tables list every player with the tag, in the
weekly report's order; `gt=off` switches PPG, xFP/game and FPOE/game to the `_ng` columns (the
projection is unchanged). **Legit is not shown** (owner decision 2026-09-30: it predicted nothing
better than its base rate): `lib/regression.ts` `DROPPED_TAGS`, and the queries' `toRow` drops it
from `tags`, `tag` and the reason on every page (the frozen live 2026-W03 list still carries it);
the track record states it in one sentence, from `regression_track_record`, without its name. The
/methodology glossary still lists the registry's "Legit (tested, not shown)" entry. The stability
study on /methodology reads `regression_stability` (`lib/stability.ts`, unit-tested): the
split-half tables, one paragraph on what they mean (its numbers picked from the rows), and the
shrinkage table with r(g) for g = the backtest lists' weeks plus the study's last g; the frozen
parameters' window is named in one sentence.

**The Decision Report Card** (step W4; `lib/decisions.ts` and `lib/decisions-track.ts`, unit-tested):
every number comes from the tables P3 publishes (`decision_fourth`, `decision_two_point`,
`decision_clock`, `coach_season`, `dim_coach`, `decisions_track_record`, site_meta `decisions_*`);
the rules' constants the text states (the 1.5-point toss-up margin, the 10-second end-of-half
exclusion, the leaderboard's 8 games, the clock thresholds) are in `lib/method.ts` and
`tests/unit/method.test.ts` checks them against `config/settings.yaml`. A call's situation is
written from its row ("4th and 2 at the opponent's 38, down 3, 6:12 left in Q4"; a try: "Try after
a touchdown, down 1 before the try, ..."). Clock cases of metrics 1 and 3 describe the OPPONENT's
snap (its down and field position; the score turned to the team's view). Worst calls = clear calls
with the most WP lost (fourth downs and tries together); best calls against convention = clear
calls where going for it (or for two) was best and the coach took it, ranked by the WP gained over
the best other option. The leaderboard ranks coaches with at least `min(8, the most games anyone
has)` games (the report's rule), least WP lost per game first; the others are listed under it.
The home card appears only while site_meta `decisions_season` is the current season and has a
`decisions_latest_week` with a wrong clear call or a call against convention.

**The Hot-Seat Meter** (step H4b; `lib/hot-seat.ts`, unit-tested): every number comes from the
`hot_seat_*` tables H4a publishes or from `lib/method.ts` (the 30-day window, the 3 drivers, the
first list week, the phases and bands of the early-season check, and step H5's research numbers,
checked by `tests/unit/method.test.ts` against `src/twm/modules/hot_seat/` and
`reports/hot_seat/research.csv`). Wording is careful on purpose (real people's jobs): an
"estimated chance of being let go", never a prediction; outcomes say "Let go", "Not let go",
"Left another way" or "Pending". The estimate bar uses a neutral chart colour.

**The Cliff board** (step I2c-b; `lib/board.ts`, unit-tested): every number comes from the
`board_*` tables I2c-a publishes or from `lib/method.ts` (the population, the 30% drop, the
6-game line, the 3 drivers, the 1-hour kickoff-eve lead, the top-10 disagreement size and the
first ECR season, checked by `tests/unit/method.test.ts` against `src/twm/modules/board/` and
`config/settings.yaml`). The two chances are always shown side by side and never added. Breakout
is not a list (owner decision 2026-10-02): its backtest appears only on /methodology, from the
`research` rows, with its verdicts computed from the intervals. The marker ranks the whole board;
the published record ranked the Cliff among the players with 6+ games in the season after (known
only afterwards), so the two can differ slightly (said on the page).

**Long lists and tables fold** (owner decision 2026-09-30; `lib/fold.ts`, `components/Fold.tsx`):
more than 10 rows show the first 10 and a native `<details>` "Show all N" (no JavaScript,
keyboard accessible; the words switch to "Show the first 10 only" while open). A ranked list
continues inside the details (`<ol start="11" data-fold-rest>`); a table stays one table and its
extra rows (`tbody[data-fold-rest]`) show while the details under it is open (CSS `:has()`). Folded:
the Radar, FLEX, K and D/ST lists, the Regression Watch tables, the player page's two history
tables. Not folded: the charts' data tables (folded whole already), the glossary (every tooltip
links into it) and navigation.

## Tests

| tier | command | needs |
|---|---|---|
| types | `npm run typecheck` | nothing (`next typegen` + `tsc`) |
| lint | `npm run lint` | nothing |
| unit | `npm test` | nothing: formatting, rank buckets and badges, query-string parsing, track-record selection, database-URL guard rails, method constants vs the Python code, theme contrast (WCAG AA, both themes, incl. the strip, field and position colours), FLEX merge order and counts, the league shape, position tabs, team colours, the streamer's comparison sentence and cutoffs (`lib/streamer.ts`), Regression Watch's tag order, toggle, verdicts, dropped tags and shrinkage table (`lib/regression.ts`), the stability study's tables (`lib/stability.ts`), the fold rule (`lib/fold.ts`), the Decision Report Card's situation in words, options, leaderboard rule, totals and clock-case words (`lib/decisions.ts`) and its methodology picks (`lib/decisions-track.ts`), the Hot-Seat Meter's rounding, driver and outcome words, calibration grouping, timelines and track-record picks (`lib/hot-seat.ts`), the Cliff board's disagreement rule, driver and outcome words, calibration bands, record and track cells (`lib/board.ts`), the site origin and the indexing flag (`lib/seo.ts`) |
| migrations | `npm run db:check` | nothing (never connects) |
| build | `npm run build` | nothing (no database at build time) |
| smoke + accessibility + layout | `npm run test:smoke:run` | a build, the local Postgres server and Google Chrome (or `CHROME_PATH`) |

`npm run test:smoke:run` (`tests/run-smoke.ts`) creates and seeds two throwaway databases on
the local server (`twm_web_test`, `twm_web_test_empty`; `tests/setup-db.ts` applies
`web/drizzle` with Drizzle's migrator and loads the fictional seed of `tests/seed.ts`, `tests/seed-modules.ts`, `tests/seed-decisions.ts`, `tests/seed-hot-seat.ts` and `tests/seed-board.ts`), starts
`next start` on each, runs `tests/smoke/*.test.ts` with `SMOKE_REQUIRE=1`, then the public
pass (a third server on the full seed with `SITE_PUBLIC=true`, `tests/smoke/public.test.ts`: no
experts' rank, marker or FantasyPros reason in any page's HTML or RSC payload, the license note
once, the same pages on the private server showing them) and stops the
servers. The suites fetch every page, check the key text (data-as-of line, disclaimer, credits,
list rows, charts and their tables, 404s, empty states) and run axe-core inside jsdom on each
page (zero serious or critical violations; jsdom cannot measure contrast).

The **browser accessibility pass** (`tests/smoke/a11y-browser.test.ts`, headless Chrome) runs
axe again with colour contrast on every page of the smoke set and the not-found page, in the
light and the dark theme, every fold opened: zero serious or critical violations (moderate ones
are printed). Its keyboard pass, on one page of each kind at 1280 and 375 px, presses Tab: the
skip link must be the first stop, visible, and move focus to `<main>`; the nine nav links must
come in order; each of the first 30 stops must show a focus ring on screen.

**Performance** (`tests/perf.ts`, not a test): `npx tsx tests/perf.ts <base> / /methodology
/waivers /time-machine` against a running `next start` prints, per page, the median of 3 cold
loads of LCP, CLS, TBT, FCP, TTFB, the HTML and the JavaScript transferred, on a mobile profile
like Lighthouse's (412 px, 4x CPU slowdown, 150 ms, 1.6 Mbps; Chrome's own throttling, so a
guide, not a Lighthouse score) and on desktop. Lighthouse itself is not installed.

- `-- --real`: the same suites against the real local publish (database `twm`, read only),
  with only the checks that hold for any data.
- `-- --keep`: leave the servers running (their output goes to `.next/smoke-server-*.log`).
- `npm run test:smoke` runs the suites against servers you started yourself
  (`SMOKE_BASE_URL`, `SMOKE_EMPTY_BASE_URL`); without a server they skip loudly, and with
  `SMOKE_REQUIRE=1` (CI) a missing server fails.

The setup refuses any database server that is not on this computer and any database name
that does not start with `twm_web_test`.

The **overlap check** (`tests/smoke/layout.test.ts`) loads every list page (home, each
position, FLEX, reconstructed lists without chances, /decisions, the coach pages, /hot-seat and /board) in headless Chrome at 320, 360, 390, 414,
600, 768, 800, 1024, 1280, 1440 and 1920 px. In every list row (`[data-row]`: pick rows, the
column header row, the top-of-the-board cards, the Report Card's call and clock-case rows) it measures each line of text
(`Range.getClientRects`, clipped by any ancestor that hides overflow) and fails when two text
boxes intersect, when text sticks out of its row, or when the page scrolls sideways; a canary
proves it catches a broken row. It opens every "Show all N" first, so the folded rows are
measured too (and fails when a page's folds were opened but none of their rows measured). The
pages suite checks the fold rule on every page (`tests/smoke/fold.ts`). Chrome is driven over the DevTools protocol with Node's
built-in WebSocket (`tests/smoke/chrome.ts`): no new dependency, no browser download. Without
Chrome it skips loudly; with `SMOKE_REQUIRE=1` it fails.

CI (`.github/workflows/ci.yml`, job `web`) runs every tier, the smoke tier against a
`postgres:16` service.

## Notes

- **Design**: a game-day broadcast. Barlow Condensed (headings, numbers, ranks) and Inter (body),
  both through `next/font/google`, downloaded at build time and served by the site. Tokens in
  `app/globals.css`: chalk/navy/turf with one hot accent (gold) for must-add and LIVE, a
  scoreboard strip and a field banner that are dark in both themes, one colour per position
  (QB, RB, WR, TE, FLEX; K and D/ST reserved). Motifs are CSS only: yard lines, hash marks,
  mowed-turf stripes, a game clock in the wordmark, jersey-number ranks, a chance meter like a
  win-probability bar (hidden from screen readers: the number is the source of truth). Nothing
  moves on its own; cards lift on hover or focus only, and not with reduced motion.
- **Lists lay out by their container** (Tailwind 4 container queries in `components/PickList.tsx`):
  narrow, medium and wide rows, whatever the window, so a half-width card never gets the
  full-width row.
- **Team colours** (`lib/team-colors.ts`): a stripe and a swatch from `dim_team.color`/`color2`,
  each only where it reaches 3:1 against that theme's card surface (else the second colour, else
  a neutral token). The team's name is always written beside it. No logos.

- **Light and dark**: follows the system; the toggle stores a choice in `localStorage`
  (`twm-theme`) and an inline script in `<head>` applies it before the first paint, so it never
  flashes. Choosing the system's own theme forgets the choice.
- **Tooltips** (`components/Term.tsx`): the term itself is a button (keyboard and touch; not
  hover-only, no `title` attributes) with the explanation from the glossary table (the
  pipeline's registry, `src/twm/registry.py`). Terms the registry does not define (chance,
  priority, precision@10, live or reconstructed ...) are in `lib/site-terms.ts`.
- **404s**: an unknown path renders the not-found page on the server with status 404. An
  unknown player id also answers 404, but Next 16 renders a page-level `notFound()` from an
  error shell whose body the browser fills in once the scripts run; the smoke test checks the
  status and that the not-found page is in the payload.
- **Errors** (checked in a browser with the database stopped, I5): a page whose read fails shows
  "Something went wrong" (`app/error.tsx`: the database may be waking up, try again; a reference
  that matches the server log's digest) inside the normal header and footer, and the header's
  "Data as of" line says the database could not be read. No message, stack or connection string
  reaches the page (the server log names the host and port at most). `app/global-error.tsx` is
  the plain fallback if the layout itself fails. `app/(site)/loading.tsx` is shown while a
  page's queries run (not for `/player/[id]` and `/coach/[id]`, which must answer 404 first).
- **Teams** are today's franchises (dim_team holds current franchises only); past lists say so.
- No logos, no league data, no ESPN data.
