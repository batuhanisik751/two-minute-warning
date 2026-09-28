# Two-Minute Warning: the website

The public site (PROJECT_SPEC 9.1, Prototype 1): Next.js 16 (App Router, server components),
React 19, Tailwind 4, Drizzle on node-postgres, Recharts. It only **reads** the Postgres
database that `twm publish` fills (docs/deploy.md); it never writes, and nothing on it is
computed from anything but that database.

| route | what it shows |
|---|---|
| `/` | this week: the newest live Waiver Radar list's top 5 per position, the track-record headline, the Regression Watch card; an empty state until the first live list exists |
| `/waivers` | one list: position tabs, season/week picker (the time machine), live or reconstructed label, chance with its range, priority, reasons, outcomes, hit-rate badges per rank bucket |
| `/regression` | what Regression Watch will do (phase D is not built: no data, no numbers) |
| `/player/[id]` | a player (nflverse `gsis_id`): header, weekly charts with data tables, Radar history; 404 for an unknown id |
| `/methodology` | data sources, the point-in-time rule, leakage safeguards, how the Radar works, the results, the full glossary, disclaimers |

Every page carries the skip link, the "Data as of" line (the Tuesday as-of of the current
week's lists, and when the data was published), the disclaimer of PROJECT_SPEC 16 and the data
credits. `robots.txt` disallows everything and every page is `noindex` until launch (E5).

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
| `/` | `radar.getLatestLiveTop(5)`, `radar.getListIndex`, `radar.getRadarModel`, `track.getTrackRows(pooled, diff)` |
| `/waivers` | `radar.getListIndex`, `radar.getList(season, week, position, kind)`, `radar.getBucketCounts(position, season)` |
| `/player/[id]` | `player.getPlayer`, `player.getPlayerSeasons`, `player.getPlayerWeeks(season)`, `radar.getPlayerHistory` |
| `/methodology` | `track.getTrackRows(...)`, `track.getTierStats`, `radar.getRadarModel`, `glossary.getGlossary` |

The hit-rate badges on `/waivers` count, for the list's position, the picks and hits per rank
in the reconstructed lists of **seasons before the list's own** with final outcomes (so a
past week's badge never uses what came after it). Summed over the four positions for 2026
they equal the track record's `bucket` rows exactly.

## Tests

| tier | command | needs |
|---|---|---|
| types | `npm run typecheck` | nothing (`next typegen` + `tsc`) |
| lint | `npm run lint` | nothing |
| unit | `npm test` | nothing: formatting, rank buckets and badges, query-string parsing, track-record selection, database-URL guard rails, method constants vs the Python code, theme contrast (WCAG AA, both themes) |
| migrations | `npm run db:check` | nothing (never connects) |
| build | `npm run build` | nothing (no database at build time) |
| smoke + accessibility | `npm run test:smoke:run` | a build and the local Postgres server |

`npm run test:smoke:run` (`tests/run-smoke.ts`) creates and seeds two throwaway databases on
the local server (`twm_web_test`, `twm_web_test_empty`; `tests/setup-db.ts` applies
`web/drizzle` with Drizzle's migrator and loads the fictional seed of `tests/seed.ts`), starts
`next start` on each, runs `tests/smoke/*.test.ts` with `SMOKE_REQUIRE=1` and stops the
servers. The suites fetch every page, check the key text (data-as-of line, disclaimer, credits,
list rows, charts and their tables, 404s, empty states) and run axe-core inside jsdom on each
page (zero serious or critical violations; contrast is checked by the unit tier instead).

- `-- --real`: the same suites against the real local publish (database `twm`, read only),
  with only the checks that hold for any data.
- `-- --keep`: leave the servers running (their output goes to `.next/smoke-server-*.log`).
- `npm run test:smoke` runs the suites against servers you started yourself
  (`SMOKE_BASE_URL`, `SMOKE_EMPTY_BASE_URL`); without a server they skip loudly, and with
  `SMOKE_REQUIRE=1` (CI) a missing server fails.

The setup refuses any database server that is not on this computer and any database name
that does not start with `twm_web_test`.

CI (`.github/workflows/ci.yml`, job `web`) runs every tier, the smoke tier against a
`postgres:16` service.

## Notes

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
- **Teams** are today's franchises (dim_team holds current franchises only); past lists say so.
- No logos, no league data, no ESPN data.
