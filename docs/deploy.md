# The public database: local Postgres, Neon, publishing

This is how the app's results get from your Mac to the public website. The heavy data (the
nflverse cache, the DuckDB warehouse, the predictions store) stays on your computer. Only
small, public summaries are copied into a Postgres database, and the website (Next.js on
Vercel, step E3) reads them from there.

- **What is published:** the Waiver Radar's weekly lists (top 25 per position) and what
  happened afterwards, the backtest lists 2014-2025 for the time machine (with the chance,
  its range and the priority computed walk-forward, see below), the track record
  (`reports/waiver_radar/evaluation.csv`, row for row), how each priority tier did, a weekly
  summary per player for the player pages (2013 on), the glossary, and small bookkeeping tables.
- **What is never published:** anything from your ESPN league (spec rule 9; the publisher
  never imports `twm.league`, a test checks it), logos (rule 10), and player ids other than
  nflverse's `gsis_id` (ESPN, PFR, Sleeper... ids stay in the warehouse).
- **Who owns the tables:** Drizzle, in `web/`. `web/db/schema.ts` describes them and
  `web/drizzle/*.sql` are the migrations generated from it. `twm publish` only writes rows.

## The three database roles

| role | who uses it | what it may do | where its connection string lives |
|---|---|---|---|
| owner (Neon's default, e.g. `neondb_owner`) | you, by hand, for migrations and `roles.sql` | everything | nowhere: you paste it when needed |
| `twm_job` (writer) | `twm publish --target remote` (your Mac, later GitHub Actions) | read and write rows; never create, change or drop a table; the run log is append-only | `.env` and the GitHub secret `DATABASE_URL` |
| `twm_web` (reader) | the website | read every table, nothing else | Vercel only (`DATABASE_URL` there) |

## Local database (for development and tests)

`docker-compose.yml` runs Postgres 16 in a container called `twm-postgres` on
`127.0.0.1:5434` (other projects on this Mac use 5432 and 5433). Its user name and password
(`twm` / `twm`) are for local development only and are not secrets: the port only listens on
this computer.

```bash
docker compose up -d twm-postgres   # start (the data survives restarts)
uv run twm migrate-local            # create or update the tables from web/drizzle/*.sql
uv run twm publish --target local   # publish everything to it
uv run pytest -m postgres           # the database tests (each uses its own throwaway database)
docker compose down                 # stop (add -v to delete the local data too)
```

`twm migrate-local` applies the same SQL files the same way Drizzle's own tool does and
keeps Drizzle's ledger (`drizzle.__drizzle_migrations`), so `npm --prefix web run db:migrate`
(without `MIGRATE_DATABASE_URL` it targets the local database) agrees with it. It refuses any
host other than this computer.

To look inside: `docker exec -it twm-postgres psql -U twm -d twm`.

## What a publish does

`uv run twm publish --target local|remote [--dry-run]` reads the local files (never writing
to them), checks them, then writes to the database in **one transaction**: either everything
is written or nothing is. One publish carries every module's lists (step P2): the Waiver
Radar (`radar_list`, `radar_pick`, `radar_outcome`), the K and D/ST streamer (`stream_list`,
`stream_pick`, `stream_outcome`, `stream_track_record`) and Regression Watch
(`regression_list`, `regression_row`, `regression_outcome`, `regression_track_record`,
`regression_stability`), with the same rules for all three (the code is shared: `src/twm/publish/tables.py` `FAMILIES`).

1. **Target check.** `--target local` uses `TWM_LOCAL_DATABASE_URL` (default: the container
   above) and refuses any host that is not this computer. `--target remote` uses
   `DATABASE_URL`, refuses it when it points at this computer, and requires
   `sslmode=verify-full` (the server's certificate is checked). The output names the host and
   database, never the connection string or password.
2. **Checks before writing** (nothing is written if one fails): every pick's player and team
   exist; ranks run 1..n without gaps or repeats; chances and probabilities lie between 0 and
   1 and each chance inside its own range; no column holds another id system or league data;
   the dataset and the predictions store agree on every final outcome. Streamer picks: a
   kicker's gsis_id or `DST-<its team>`, the entity type of its position, a model probability
   for every kicker. Regression Watch: one row per universe player, the tag = the first of its
   tags, and only the product's tags (sell_high, buy_low: Legit, tested and dropped on
   2026-09-30, is stripped from the frozen backtest lists at publish time). The frozen backtests must reproduce their committed
   reports (below), else nothing is published.
3. **Live lists are frozen.** A live list (made in real time on a Tuesday) is published once
   and never changed or removed afterwards, even when your local predictions store no longer
   has it (for example on a fresh GitHub Actions runner). If a live list was scored with
   incomplete data, it is skipped (exit code 4, so a scheduled job can try again later);
   `--allow-incomplete` publishes it anyway, marked as incomplete. To replace one published
   live week on purpose: `--replace-live 2026-W04` (it prints what it replaces).
4. **Everything else is rebuilt** from the local files on every run: the backtest lists, the
   outcomes (of every published pick, including frozen live lists, plus every player of the
   current season's dataset), the track record, the tier table, the player summaries, the
   glossary and `site_meta`. Model versions, players and teams are added or updated, never
   deleted (a frozen live list may need them).
5. **Empty-replacement guard** (step E4). Before anything is written, each replaced table
   (every module's backtest lists and rows, its outcomes and track record, `tier_stats`,
   `player_week_summary`, `glossary`) is compared with what the target holds: if it would lose
   more than `publish.max_shrink_share` of its rows (settings.yaml, 10%), the publish is
   refused with exit code `5` and a failed `pipeline_runs` row, and nothing changes. A run
   whose local inputs went missing or came out much smaller therefore never wipes the site.
   `--allow-shrink` is the owner's explicit override (the drop is reported as a warning).
6. **Unchanged tables are not rewritten** (step E4). Each replaced table's content hash is
   kept in `site_meta` (keys `hash:<table>`; a module's backtest lists and rows share one key,
   `hash:backtest_lists` for the Radar, `hash:stream_backtest_lists` and
   `hash:regression_backtest_lists`, which leave out `generated_at`). A table whose hash and row
   count equal the target's is skipped: the summary says "unchanged (not rewritten)". That
   saves Neon writes on nightly runs where only a few tables change. A table whose rows were
   changed behind the publisher's back (a different row count) is rewritten anyway.
7. **Run log.** Every run adds a row to `pipeline_runs` (success, or failed with the error
   message, scrubbed of the connection string; the scheduled pipeline adds its stages, week
   and list status under `notes.pipeline`, and a stage that failed before the publish is
   recorded with that stage's name in `stage`). `--dry-run` does all of the work, rolls it
   back and records nothing.

**The backtest lists' chance** (the site shows the chance, E1 decision). A backtest list of
season S gets its chance, range and priority from the backtest predictions of the seasons
before S only, with the same code as the weekly list (`confidence.from_store` with
`seasons_before(S)`): no list's chance rests on its own or later outcomes (a test flips a
season's outcomes and checks that its chances do not move). The 2014 lists have no earlier
season and keep no chance (rank and outcome only); backtest lists never have reasons. Early
seasons rest on few earlier seasons (2015 on 2014 alone), so their chances are rougher.

**The streamer's and Regression Watch's lists** (step P2; `src/twm/publish/stream_lists.py`,
`regression_lists.py`). Live lists and the current season's reconstructed lists come from the
predictions store (read-only), as the Radar's do. The backtest lists (the time machine):

- *K and D/ST, 2013-2025*: the approved methods' frozen backtest, read in place from the pins
  (sha256 checked; nothing is restored into a store). K: the K model's snapshot predictions.
  D/ST: the approved rule applied again to the streamer dataset's graded rows, which must give
  exactly the pinned hit-rate table (else the dataset changed and nothing is published). A K
  snapshot label that differs from the dataset's is refused the same way. The chance, range
  and priority of a backtest pick come from the backtest seasons before its own, as above
  (the 2013 lists have none). Team, name and home come from the streamer dataset, the next
  opponent from the schedule; outcomes (`y_start`, next week's points) from the dataset.
- *Regression Watch, the headline as-of weeks 4, 6, 8 and 10 of 2011-2025*: the FROZEN
  snapshot pinned with the approved parameters (see "Regression Watch's approved parameters"
  below; sha256 checked before it is read). It is never recomputed by the job: the lists were
  made once on the owner's Mac by D3's walk-forward (variant and X chosen on the seasons before,
  shrinkage on 2009 .. S-1: the parameters `twm regression pin` would have approved for S; model
  version = that parameters file's version) with the weekly list's own code, and each row keeps
  its team and rest-of-season outcome. `regression_row.team` of a live list is the team of the
  player's latest game public at the list's as-of; `regression_outcome` holds the rest-of-season PPG and games after the
  as-of ('final' once the regular season is over, else 'pending' with NULLs), for every week
  since 2026, so frozen live lists keep their outcomes on a fresh runner. A list before week 4
  carries the note "Week N is earlier than the backtested weeks 4-14: ..." (also in the weekly
  report). `player_week_summary` gains `points_ng`, `xfp_ng`, `fpoe_ng` (without garbage time).
- *Track records*: `reports/streamer/backtest.csv` and `reports/regression_watch/backtest.csv`,
  row for row (`line` = the CSV's data row; the latter's `table` and `group` columns are
  `section` and `row_group`).

`site_meta` also carries `current_week` and `current_as_of` (that week's official Tuesday
14:00 UTC as-of, ISO 8601 UTC, the value `twm.asof.weekly_as_of` gives; empty before the
season's first as-of), `data_as_of`, `generated_at` and the latest list weeks.

It ends with a summary: what happened to each live list, rows per table, table sizes, the
target. Exit codes: `0` done; `1` refused or failed (nothing changed); `4` done, but some live
lists were skipped as incomplete; `5` refused by the empty-replacement guard (nothing
changed). Publishing twice in a row gives identical tables (apart from the run log and the
`generated_at` time).

Sizes: a full publish of the real data is about 29 MB of tables (36 MB for the whole local
database; measured 2026-09-30 after `VACUUM FULL`, with the streamer's 425 lists and 3,762
picks and Regression Watch's 61 lists and 10,964 rows: 6 MB of them; 21 MB before step P2),
far below Neon's free-plan storage of 0.5 GB. Because every run deletes and rewrites most rows, the sizes a publish prints include
the old row versions until Postgres's automatic clean-up (autovacuum) has run; right after a
run they can read about twice the real size.

## Setting up Neon (the owner's steps, in order)

Claude never connects to Neon; you do these steps yourself. Keep every connection string and
password out of chats, commit messages and command lines.

> **Never `source` a file that holds a connection string** (`.env`, anything). Neon's strings
> contain `&`; the shell treats it as "run in the background", splits the string and prints
> the pieces, password included. Read such files with Python (`twm` does) or open them in an
> editor.

1. **Create the project.** In the Neon console (neon.com), on your existing account, create a
   new project for this app, separate from the F1 one (free plan). The region the F1 project
   uses (AWS us-east-1) is a sensible choice. Creating it in the Neon console rather than
   through the Vercel marketplace keeps the owner connection out of Vercel: the marketplace
   attaches the owner's connection string to Vercel automatically (it happened with the F1
   project, and it had to be replaced by hand before the first deploy).
2. **Copy the owner connection string.** In the project's connection details, choose the
   default owner role and the **direct** connection (the host without `-pooler`). Keep it
   in your password manager.
3. **Create the tables.** In a terminal in the project folder:

   ```bash
   printf 'Owner connection string: '; read -rs MIGRATE_DATABASE_URL; echo
   export MIGRATE_DATABASE_URL
   npm --prefix web run db:migrate
   unset MIGRATE_DATABASE_URL
   ```

   `read -s` does not show what you paste and keeps it out of the shell history. Before
   pasting, change the end of the string to `?sslmode=verify-full` (remove `sslmode=require`
   and `channel_binding=require`): Drizzle uses node-postgres, which checks the certificate
   with `verify-full` and must not get an `sslrootcert` setting (see "TLS" below).
4. **Make two passwords**, one per role, and store them in your password manager:

   ```bash
   openssl rand -base64 32 | tr '/+' '_-' | tr -d '='
   ```

   (43 letters, digits, `_` and `-`: safe inside a connection string as they are. Neon
   requires passwords of roles made with SQL to have at least 60 bits of entropy; these have
   far more.)
5. **Create the roles.** `uv run python scripts/neon/apply_roles.py` asks for the owner
   connection string (as copied, or as edited in step 3) and the two passwords, without
   showing them, runs `scripts/neon/roles.sql` and prints what each role may do and the shape
   of the two connection strings. Roles made with SQL are not members of Neon's
   `neon_superuser` (unlike roles made in the console), which is what we want.
6. **Put the writer's connection in `.env`.** Open `.env` in an editor (`cp .env.example .env`
   first if it does not exist) and set

   ```
   DATABASE_URL=postgresql://twm_job:<twm_job password>@<direct host>/<database>?sslmode=verify-full
   ```

   (`twm publish` adds the certificate bundle itself, see "TLS"). Check it without changing
   anything: `uv run twm publish --target remote --dry-run`, then publish:
   `uv run twm publish --target remote`. Afterwards believe the content, not only the summary:
   the Neon console's table view should show rows in `radar_list`.
7. **GitHub secret** (needed from step E4): `gh secret set DATABASE_URL`, then paste the same
   writer string as in `.env` when it asks (without `--body`, it reads the value from the
   keyboard; `--body` would put the value in your shell history).
8. **Vercel** (step E3/E5, not yet): the site's `DATABASE_URL` is the **reader** on the
   **pooled** host (the one with `-pooler`), in node-postgres's shape:

   ```
   postgresql://twm_web:<twm_web password>@<endpoint>-pooler.<region>.aws.neon.tech/<database>?sslmode=verify-full
   ```

   `vercel env add DATABASE_URL production` asks for the value (never type it as an argument).
   The site stays behind Vercel Authentication until the two-live-Tuesdays check (E1
   decision).
9. **After every later migration** (a new file in `web/drizzle/`): repeat step 3, then step 5.
   Step 5 is safe to repeat: it re-asserts every grant (tables created later by the owner are
   covered automatically by the default privileges, but repeating it costs nothing). If Neon
   refuses to change an existing role's password on a repeat run, the script says so and
   still applies the grants.

## The scheduled pipeline (GitHub Actions, step E4)

`.github/workflows/pipeline.yml` runs `uv run twm pipeline run` on GitHub's servers, so the
weekly list is made while your Mac sleeps. All the logic is in `src/twm/pipeline/`; the
workflow only sets up the runner, the cache and the artifact. **The job never commits**
(`permissions: contents: read`; a test fails if any workflow contains `git commit` or
`git push`). Its results go to the public database and to a downloadable artifact.

**When it runs** (UTC; `pipeline:` in `config/settings.yaml`, and a test checks that the cron
lines equal it):

| when | what |
|---|---|
| every day 10:47 | the nightly refresh in season; in the offseason it goes ahead only on Tuesdays |
| Tuesday 15:17, 18:47, 21:17 | retries after the Tuesday 14:00 as-of (snap counts can lag) |
| Wednesday 03:17 | the **last** attempt: a week whose data is still missing now fails the run, so GitHub emails you |

"In season" runs from 7 days before the current season's first regular-season game day to 10
days after its last one; the dates are read from the cached schedule, never typed in. Why
those times: nflverse's own workflows (checked 2026-09-28) rebuild play-by-play and player
stats every day at 09:03 UTC from September to February, plus game-day runs (Monday and
Tuesday 05:33, Sunday 22:03, Monday 00:07, Friday 05:33;
[nflverse-pbp `update_data.yaml`](https://github.com/nflverse/nflverse-pbp/blob/master/.github/workflows/update_data.yaml));
expected points (ffopportunity) on the same game-day windows, Tuesday 05:45
([`ep-update-data.yaml`](https://github.com/ffverse/ffopportunity/blob/main/.github/workflows/ep-update-data.yaml));
rosters every day at 07:07 and injuries at 07:07 from September to February
([nflverse-rosters](https://github.com/nflverse/nflverse-rosters/tree/master/.github/workflows));
snap counts at 00, 06, 12 and 18 UTC in season
([pfr_scrapR `update_snap_counts.yaml`](https://github.com/nflverse/pfr_scrapR/blob/master/.github/workflows/update_snap_counts.yaml);
summary: [nflverse data schedule](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html)).
The minutes avoid the top of the hour: GitHub says scheduled runs "can be delayed during
periods of high loads", the start of every hour especially, and that queued jobs may even be
dropped ([events that trigger workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)).
nflverse's own daily 09:03 run started at 13:42 UTC on Saturday 2026-09-26 and at 14:39 on
Sunday 09-27 (GitHub's API; the same workflow also has a `workflow_dispatch` run at 09:00
every day, and those finished by 09:05), so delays of hours are real: the pipeline decides
everything from the clock (which week is due, whether this is the last attempt), never from
which cron line fired.

**What one run does** (each `twm` command runs as its own process, exactly as you would type
it; the run's log shows every one):

| stage | what |
|---|---|
| preflight | the approved models load (the Radar's model and backtest, the streamer's K model and D/ST rule, Regression Watch's parameters: pin, file, sha256, version); where the publish goes; which CA bundle would check Neon's certificate |
| gate | offseason days without a run stop here (exit 0) |
| ingest | `twm ingest --start <season> --force` (current season and one-file datasets); on a cold cache first `twm ingest --end <season - 1>`; one retry after 60 s |
| build | `twm build --start 2012 --end <season>` (`pipeline.build_start`) |
| plan | which week's list is due: the clock is between its Tuesday as-of and the next week's first kickoff |
| dataset | `twm radar dataset` |
| backtest | `twm model restore-backtest waiver_radar`: the approved backtest restored from its committed snapshot (sha256 and rows checked first, then checked against the committed evaluation); no fold is trained. The bands, priorities and the time-machine lists come from it |
| score | only when a list is due: `twm radar score --pinned` with the approved model; exit 3 = not ready |
| export | the list as CSV and Parquet for the artifact |
| streamer_dataset | `twm streamer dataset` (step P2): the K and D/ST pool, features and labels; the publish reads teams, outcomes and the D/ST rule's backtest from it |
| streamer_backtest | `twm model check streamer`: the approved K model and D/ST rule and their frozen backtest (sha256, rows, and `reports/streamer/backtest.csv` reproduced). Nothing is restored: the weekly list and the publish read the snapshot in place |
| regression_backtest | `twm model check regression_watch`: the approved parameters and their frozen backtest lists (sha256, rows, and `reports/regression_watch/backtest.csv` reproduced). Nothing is recomputed on the runner (a Linux rebuild could flip a near-tie of the variant choice, and it would need the warehouse from 2006) |
| streamer_score, streamer_export | only when a list is due: `twm streamer score` with the approved methods; the same not-ready rules as the Radar (exit 3: a warning on an early attempt, `late` from the last one) |
| regression_score, regression_export | only when a list is due: `twm regression score` with the approved frozen parameters; the same rules. Weeks 1-2 store nothing (nobody has 3 games) |
| publish | ONE `twm publish --target remote` for every module when the `DATABASE_URL` secret exists, otherwise skipped with a warning (so the pipeline can be rehearsed before Neon exists); it also runs after a not-ready score, so outcomes and player pages stay fresh |

**How a run ends** (the job's colour and GitHub's email follow the exit code):

| exit code | job | meaning |
|---|---|---|
| 0 | green | done. Warnings (annotations on the run page): publish skipped (no secret), week not ready yet on an early attempt, a flaky download retried, the approved model's training data differs from today's |
| 1 | red | a stage failed; the job summary names it, and the failure is recorded in `pipeline_runs` (`stage` = the stage) when the database is reachable |
| 3 | red | the week's data had not arrived by the last attempt (or a week you named is not ready), for any of the three lists; the publish still ran |

A live list is still only made in its live window (Tuesday as-of to the next kickoff), and a
run that finds no list due (Thursday night to Tuesday morning) refreshes the data, outcomes and
player pages and publishes them. A manual run with a `week` input scores that week (stored as
reconstructed outside its window).

**What a run leaves behind.** The job summary (stages with times, the week, the list's status,
rows per table, warnings) and an artifact `pipeline-<run id>-<attempt>` kept 90 days (the
maximum for a public repository): `summary.md`, `result.json`, `logs/` (one file per command,
scrubbed of the connection string), `reports/<season>-Wnn.md` (the weekly report),
`lists/<season>-Wnn.csv` and `.parquet` (the top 25 per position), the streamer's and
Regression Watch's reports (`reports/streamer-<season>-Wnn.md`,
`reports/regression_watch-<season>-Wnn.md`) and stored lists (`lists/streamer-...`,
`lists/regression_watch-...`), `publish.json`.
The `pipeline_runs` table gets one row per run that reaches the database: the publish's row
(its notes carry the stages, the week and the list's status), or a failed row naming the stage
that failed. Offseason days stopped by the gate, runs without the secret and dry runs write
none.

**Caches.** The nflverse cache (`data/raw`, about 500 MB) is an `actions/cache` entry per ISO
week (`nflverse-raw-v1-<year>-W<week>`); a miss restores the newest older week and the run
tops it up; a runner without any cache downloads everything. It is saved only after a
successful ingest. GitHub keeps up to 10 GB per repository and removes entries not used for 7
days ([dependency caching](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching)).
The warehouse and the dataset are rebuilt on every run. The backtest is not: it is restored
from the snapshot approved with the model (see "The approved model and its backtest"), because a rebuild on
the Linux runner is not bit-identical to the Mac's (the reviewer's two GitHub rehearsals: a few
2022-2023 top-10 orderings swapped, pooled precision@10 3,498 instead of 3,501 hits, the
must-add cutoff 52.5% instead of 53.0%), and the site would then contradict its own track
record. The warehouse starts in 2012 because the dataset starts with snap counts (2013) and
the 2013 candidate pool uses the prior season's points per game: building 2012-2026 gives the
same dataset and the same published rows as 1999-2026 (checked 2026-09-28), in half the time,
while starting in 2013 changes the 2013 pool. Step P2 checked it again with every module
(2026-09-30, a scratch 2012-2026 build, 36 s): the Radar's dataset is identical; the streamer's
differs only in its 2012 rows (no 2011 season for the prior-season features) and 6 rows outside
the pool (2014, 2016: a stadium's roof is read from its earlier games), none of which the lists,
the D/ST rule's check or the published tables use; week 3's Radar, streamer and Regression
Watch lists scored from it into a fresh store equal the live ones; and a publish from it equals
the one from the full warehouse on every table. Regression Watch's backtest lists need 2006
onwards, which is why they are frozen, never rebuilt by the job.

**Measured on this Mac** (2026-09-28, a fresh clone in a scratch folder, publishing to a local
database; a GitHub runner (4 CPUs, 16 GB) will give other numbers, and its job summary shows
each stage's real time):

| stage | cold (no cache: what a first run does) | warm (cache restored, nothing else) |
|---|---:|---:|
| ingest | 77 s (full history, 421 files) | 7 s (current season + one-file datasets) |
| build (2012-2026) | 32 s | 32 s |
| dataset | 38 s | 39 s |
| backtest (restore the snapshot) | 2 s | 2 s |
| score | 3 s | 3 s |
| publish | 6 s (everything written) | 4 s (every replaced table unchanged, 0 rows rewritten) |
| **all stages** | **2 min 38 s** | **87 s** |

(Before the snapshot, the backtest stage rebuilt the folds: 53 s; the warm run took 2 min 17 s.)

The first run in a fresh virtualenv took about 40 s longer than its stages (most likely Python
compiling the libraries on first import; the next run: 2 s). Sizes: the nflverse cache is
501 MB on disk (442 Parquet files) and 502 MB as a compressed tar, since Parquet does not
shrink further, so each weekly cache entry is about 0.5 GB; the 2012-2026 warehouse is 240 MB
(1999-2026: 400 MB, 68 s), the dataset 9 MB, the predictions store 3 MB (neither is cached),
a run's artifact about 120 KB, the approved model 15 KB, its backtest snapshot 0.89 MB
(predictions 830 KB, outcomes 53 KB, model versions 5 KB; zstd level 19). A retrain candidate
takes the logit backtest (50 s) plus `twm model candidate` (15 s) after the data stages; on
this Mac it reproduces `logit-5828a082c9e09093` and the approved backtest's numbers exactly.

**Running it yourself.** `uv run twm pipeline plan` shows what a run would do now (changes
nothing). `uv run twm pipeline run --publish skip` runs every stage on your Mac with your
cache and store (it refreshes the current season like your Tuesday routine);
`--publish local` publishes to the Docker database; `--publish remote` to Neon (reads
`.env`). Without `--publish`, only a `DATABASE_URL` in the environment (the GitHub secret)
makes it publish. Its files go to `data/pipeline/<run id>/`. Your own `twm score` keeps using
your store's production model; `twm score --pinned` uses the approved one, like the job. On
your Mac the backtest stage finds the approved backtest already in your store and writes
nothing; if you have rebuilt the backtest since, it refuses (pin again, see below).

## The approved model and its backtest (step E4)

The scheduled job must score with the model you approved, never one it trained itself
(spec 9: retraining is a manual workflow with a review step), and the lists' chance, band and
priority, the priority table and the time-machine lists must come from the same backtest as
the published track record (`reports/waiver_radar/evaluation.csv`). A fresh runner has no
`models/` folder and no predictions store (both gitignored), so all of it is committed:

- `config/production_models.yaml` (the **pin**): the season it scores, its `model_version`,
  the model file and its sha256, and the backtest snapshot's three files with their sha256
  and row counts;
- the model, `artifacts/production_models/waiver_radar/<version>.joblib` (about 15 KB; not
  under a folder named `models`, which `.gitignore` ignores anywhere);
- the **backtest snapshot**, `artifacts/production_models/waiver_radar/backtest-<version>/`:
  `predictions.parquet`, `outcomes.parquet`, `model_versions.parquet` (zstd): the logit y_hit
  walk-forward backtest of the evaluation seasons before the pinned season (2014-2025 for
  2026: 12 fold versions, 91,638 predictions and their outcomes), exported read-only from
  the store the evaluation was made from. Only the production model and label: the whole
  store (every method, both labels) would be 4.0 MB, over the repository's 2 MB large-file
  hook, and nothing on the scheduled path reads the other methods (the track record is the
  committed CSV).

What checks them:

- `twm radar score --pinned` (the job) opens the model file only when its sha256 equals the
  pin's (a pickle is only ever opened when it is the approved bytes), then the version inside
  must equal the pin and the season must match. If today's training data differs from the
  data the approved model was trained on, the run warns and still uses the approved model.
- `twm model restore-backtest` (the job's backtest stage) reads each snapshot file only after
  its sha256 matched, checks the row counts, writes it into the fresh store (refused when the
  store already holds a different backtest), then checks it against the committed evaluation:
  every logit y_hit row the store alone determines (precision@10 pooled, by season and by
  position with intervals and counts, rank buckets, PR-AUC, Brier, calibration: 120 rows)
  must match, counts exactly and values to the CSV's 6 decimals. Anything else fails the run.
- `uv run twm model check` runs all of it without changing anything; run it after any change to
  the pin, the snapshot or the evaluation. A test restores the committed snapshot and compares
  it with the committed CSV on every CI run; with your dataset (`uv run pytest -m realdata`) all
  214 logit y_hit rows that do not compare with another method match, breakouts and the
  without-rostered subset included.

Today's pin is `logit-5828a082c9e09093` (trained 2013-2025, the C6 production model) with the
backtest of your store (the one behind the committed evaluation). It was written with
`uv run twm model pin waiver_radar --version logit-5828a082c9e09093`: the command re-saves a
new model with the production code (an existing file of the same version is kept byte for
byte), exports the store's backtest (`--store`, default your `data/predictions.duckdb`, opened
read-only) and refuses when that backtest does not reproduce the committed evaluation. So the
order after a new backtest is: `twm radar backtest`, `twm radar evaluate`, then `twm model pin`.

**Retraining** (e.g. before a new season, or when the job warns that the training data
changed): run the manual workflow `gh workflow run retrain.yml` (input `season`, default
`current_season`). It runs the logit backtest and trains a candidate on fresh data, then
uploads `retrain-candidate-<run id>`: the model, its backtest snapshot and the pin laid out
like the repository plus `retrain_report.md` (the candidate against the approved model:
training data, how much the probabilities and the weekly top 10s change; the candidate
backtest against the approved one: precision@10, the must-add cutoff, each tier's hit rate;
and whether it reproduces the committed evaluation). It never commits and never publishes.
"Nothing to approve" means the model and the lists' numbers equal the approved ones. A
candidate made on the runner will usually NOT reproduce the committed evaluation (Linux float
noise, see "Caches"): approving it as it is would make the site contradict its track record.
To keep them consistent, approve on your Mac: `uv run twm radar backtest`, `uv run twm radar
evaluate`, `uv run twm model pin waiver_radar --version <version>` (after `uv run twm train
waiver_radar` for a new model), then `uv run twm model check`, review `git diff`, commit the
pin, the model, the snapshot and the reports together. The next run uses them.

### The streamer's approved methods (step S2a)

The K and D/ST streamer (docs/streamer.md) is pinned the same way, with two entries next to the
Radar's (whose entry and files stay byte-identical):

- `streamer_k` (`model: logit`): the logistic regression that ranks kickers,
  `artifacts/production_models/streamer/<version>.joblib` (about 11 KB), and its frozen
  backtest `backtest-<version>/` (`predictions`, `outcomes` = the dataset's `y_start` labels of
  those picks, `model_versions`: 2013-2025, 2,318 picks, 13 folds);
- `streamer_dst` (`model: rule`): the D/ST list is a rule, not a fitted model, so the "model"
  file is the rule's definition (`<version>.json`: column, direction, tie-breakers; checked
  against the code's rule when loaded) and the frozen backtest is one table, `hit_rates` (per
  test season, list length and rank: how many lists, how many starts; 434 rows since C1's
  re-approval with the league's D/ST scoring, `twm streamer pin --pos DST`).

`uv run twm streamer score` loads only these (sha256 first, then version and season; a changed
file is never opened) and reads the chance and priority from the snapshots' seasons before the
list's season. `uv run twm model check` (no argument: every pinned module; `streamer`: only
these) also checks that the snapshots reproduce `reports/streamer/backtest.csv` (pooled and
per-season precision@1/3/5 with their counts, and the K model's Brier: 33 rows). To approve
after a new backtest: `uv run twm streamer backtest --store <store>`, then
`uv run twm streamer pin --store <store>` (it trains the season's K fold, writes the rule and the
snapshots, and refuses when they do not reproduce the committed report), `uv run twm model
check`, review, commit the pin, the files and `reports/streamer/` together.

### Regression Watch's approved parameters (step D4a)

Regression Watch (docs/regression_watch.md "The weekly list") has no trained model: its weekly
list uses one frozen parameters file, pinned as a third kind of entry next to the others (their
entries and files stay byte-identical):

- `regression_watch` (`model: params`): `artifacts/production_models/regression_watch/
  <version>.json` (4 KB: the variant, the shrinkage table estimated on the seasons before the
  pinned one, the Sell-high / Buy-low X, and the record of the last backtest season). The
  version is a hash of the file's content, and the record must equal the committed
  `reports/regression_watch/backtest.csv` rows of that season.
- its **frozen backtest lists** (step P2, the pin's `backtest:`; like the Radar's and the
  streamer's snapshots): `backtest-<version>/predictions.parquet` (every universe player of the
  as-of weeks 4, 6, 8 and 10 of 2011-2025 with his projection, tag, the numbers behind them and
  his team: 10,787 rows, 648 KB), `outcomes.parquet` (his rest-of-season PPG, games and PPG
  rank: 60 KB) and `model_versions.parquet` (each season's parameters and D3's choice: 15 rows,
  8 KB), zstd level 19, 716 KB in all (`src/twm/modules/regression_watch/frozen.py`). The
  publish reads them for the time machine; the job never rebuilds them.

`uv run twm regression score` loads only the parameters file (sha256 first, then the version
and the season). `uv run twm model check` (no argument, or `regression_watch`) checks the file
and the record, then the frozen lists: each file's sha256 before it is read and its rows, every
season's variant, validation MAE and X, the headline MAE of the projection per position and
pooled, the tags' hit rates (graded, rate, not graded) against the committed report, and the
parameters' record against the lists' last season. To approve for a new season, on your Mac
with the full warehouse (the lists need 2006 onwards): `uv run twm regression backtest` (the
committed report), then `uv run twm regression pin` (it recomputes the choice from the
warehouse, refuses when its record disagrees with the report, and freezes the backtest lists:
`uv run twm regression freeze` does only that step), `uv run twm model check`, review, commit
the pin, the files and `reports/regression_watch/` together. The scheduled job checks them
(`regression_backtest`) and scores with them (`regression_score`), as `uv run twm score` does.

## The owner's steps for E4

1. **Push** the commit with `.github/workflows/pipeline.yml` from your own GitHub account (as
   every commit here is): GitHub sends a scheduled workflow's notifications to the user who
   last changed its cron lines. Later edits of those lines keep it that way only if you make
   them.
2. **Turn on the failure email:** github.com → your avatar → Settings → Notifications →
   System → Actions: choose Email and tick "Only notify for failed workflows", Save
   ([managing GitHub Actions notifications](https://docs.github.com/en/subscriptions-and-notifications/how-tos/managing-github-actions-notifications)).
   Exit codes 1 and 3 then reach your inbox.
3. **Rehearse** before Neon exists (no secret: everything runs, the publish is skipped with a
   warning): `gh workflow run pipeline.yml --ref main -f skip_publish=true`, then
   `gh run watch` and read the job summary. The preflight row names the CA bundle the runner
   has.
4. **After Neon (E5):** `gh secret set DATABASE_URL` (step 7 above), then a dry run:
   `gh workflow run pipeline.yml --ref main -f dry_run=true`.
5. **The 60-day rule:** in a public repository GitHub disables scheduled workflows after 60
   days without repository activity
   ([disabling and enabling a workflow](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows)).
   In the offseason push something at least every two months, or re-enable it:
   `gh workflow enable pipeline.yml`.

## TLS: which connection string shape for which program

Every connection to Neon checks the server's certificate (`sslmode=verify-full`). The programs
differ in where they find the list of trusted certificate authorities, so the strings differ
slightly. These facts come from the owner's F1 app, where each was found the hard way
(`F1Analytics/docs/RUNBOOK.md` "Three TLS environments" and `docs/OPS_SPEC.md` 10.2), and were
re-checked here where possible.

| program | library | connection string ends with | why |
|---|---|---|---|
| the website, `npm run db:migrate` (Drizzle) | node-postgres (`pg` 8.23) | `?sslmode=verify-full` and nothing about certificates | node checks against its own bundled list. `pg` reads `sslrootcert=` as a file path, so `sslrootcert=system` crashes it (checked in `pg-connection-string`'s source, which opens the value with `readFileSync`); `sslmode=require` works but prints a security warning, because `pg` 8.23 treats it as `verify-full` |
| `twm publish`, `apply_roles.py` on your Mac | psycopg 3.3 with its own libpq 18 | `?sslmode=verify-full` (the code adds `sslrootcert=/etc/ssl/cert.pem`) | on this Mac `sslrootcert=system` fails with "certificate verify failed" for psycopg (F1 app); the macOS bundle `/etc/ssl/cert.pem` works. If the string names its own `sslrootcert`, that one is used |
| `twm publish` on GitHub Actions (step E4) | the same | the same | the code uses `/etc/ssl/certs/ca-certificates.crt`, Ubuntu's bundle: the Mac path `/etc/ssl/cert.pem` does not exist there, so the code falls through to it. Checked 2026-09-28 in an `ubuntu:24.04` container with the `ca-certificates` package (the file exists, 121 certificates; `/etc/ssl/cert.pem` does not; OpenSSL's own `/usr/lib/ssl/cert.pem` links to the same file). The workflow pins `ubuntu-24.04`, and the pipeline's preflight names the bundle it found (and how many authorities it holds) on every run. The certificate check itself happens at the first remote publish (E5) |

## Files

- `docker-compose.yml`: the local database.
- `web/db/schema.ts`, `web/drizzle/`: the tables and their migrations. `npm run db:generate`
  writes a new migration after a schema change; `npm run db:check` (also in CI) fails when the
  two disagree.
- `src/twm/publish/`: `collect.py` (reads the local files, checks them), `stream_lists.py`
  and `regression_lists.py` (the streamer's and Regression Watch's lists, step P2), `write.py`
  (the transaction), `target.py` (connection strings and their guard rails), `tables.py` (the
  columns the writer sends, a test checks them against a migrated database, and `FAMILIES`:
  each module's list tables and how a publish treats them), `migrate.py` (the local migration
  helper). `web/drizzle/0001_streamer_regression_watch.sql` adds the P2 tables (the reviewer
  applies it to Neon with `npm --prefix web run db:migrate` before the first P2 publish);
  `web/drizzle/0002_regression_stability.sql` adds `regression_stability` (step R1: the D2
  stability study for the methodology page; apply it to Neon the same way before the first
  publish that carries it, or that publish fails on the missing table and writes nothing).
- `scripts/neon/roles.sql`, `scripts/neon/apply_roles.py`: the two Neon roles.
- `.github/workflows/pipeline.yml`, `src/twm/pipeline/` (`schedule.py` the calendar,
  `runner.py` the stages and exit codes, `report.py` the job summary and the run's files): the
  scheduled pipeline (`twm pipeline run|plan`).
- `.github/workflows/retrain.yml`, `src/twm/pins.py`, `config/production_models.yaml`,
  `artifacts/production_models/`: the approved model and its backtest snapshot
  (`twm model check|pin|restore-backtest|candidate`).
