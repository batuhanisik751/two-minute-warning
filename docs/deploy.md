# The public database: local Postgres, Neon, publishing

This is how the app's results get from your Mac to the public website. The heavy data (the
nflverse cache, the DuckDB warehouse, the predictions store) stays on your computer. Only
small, public summaries are copied into a Postgres database, and the website (Next.js on
Vercel, step E3) reads them from there.

- **What is published:** the Waiver Radar's weekly lists (top 25 per position) and what
  happened afterwards, the backtest lists 2014-2025 for the time machine, the track record
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
is written or nothing is.

1. **Target check.** `--target local` uses `TWM_LOCAL_DATABASE_URL` (default: the container
   above) and refuses any host that is not this computer. `--target remote` uses
   `DATABASE_URL`, refuses it when it points at this computer, and requires
   `sslmode=verify-full` (the server's certificate is checked). The output names the host and
   database, never the connection string or password.
2. **Checks before writing** (nothing is written if one fails): every pick's player and team
   exist; ranks run 1..n without gaps or repeats; chances and probabilities lie between 0 and
   1 and each chance inside its own range; no column holds another id system or league data;
   the dataset and the predictions store agree on every final outcome.
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
5. **Run log.** Every run adds a row to `pipeline_runs` (success, or failed with the error
   message, scrubbed of the connection string). `--dry-run` does all of the work, rolls it
   back and records nothing.

It ends with a summary: what happened to each live list, rows per table, table sizes, the
target. Exit codes: `0` done; `1` refused or failed (nothing changed); `4` done, but some live
lists were skipped as incomplete. Publishing twice in a row gives identical tables (apart from
the run log and the `generated_at` time).

Sizes: a full publish of the real data is about 21 MB of tables (28 MB for the whole local
database; measured 2026-09-28 after `VACUUM FULL`), far below Neon's free-plan storage of
0.5 GB. Because every run deletes and rewrites most rows, the sizes a publish prints include
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
| `twm publish` on GitHub Actions (step E4) | the same | the same | the code uses `/etc/ssl/certs/ca-certificates.crt`, Ubuntu's bundle. **Not verified yet**: E4 must check it on the runner |

## Files

- `docker-compose.yml`: the local database.
- `web/db/schema.ts`, `web/drizzle/`: the tables and their migrations. `npm run db:generate`
  writes a new migration after a schema change; `npm run db:check` (also in CI) fails when the
  two disagree.
- `src/twm/publish/`: `collect.py` (reads the local files, checks them), `write.py` (the
  transaction), `target.py` (connection strings and their guard rails), `tables.py` (the
  columns the writer sends; a test checks them against a migrated database), `migrate.py`
  (the local migration helper).
- `scripts/neon/roles.sql`, `scripts/neon/apply_roles.py`: the two Neon roles.
