-- scripts/neon/roles.sql: the public database's two login roles (docs/deploy.md, step 5).
--
--   twm_web  the site (Vercel): reads every table, writes nothing.
--   twm_job  the publish job (`twm publish --target remote`, GitHub Actions and the owner's
--            .env): reads and writes rows (SELECT, INSERT, UPDATE, DELETE), never changes a
--            table (no DDL); pipeline_runs is append-only (no UPDATE or DELETE there).
--
-- Run it as the database OWNER (the role that runs the Drizzle migrations), AFTER the
-- migrations, with scripts/neon/apply_roles.py, which asks for the two passwords without
-- showing them and hands them over as the settings twm.web_password / twm.job_password (they
-- never appear in this file, in a command line or in the shell history). Plain SQL, no psql
-- meta-commands. Safe to run again after a later migration: every grant is re-asserted and
-- ALTER DEFAULT PRIVILEGES already covers tables that later migrations create (they are
-- created by the same owner role).
--
-- Roles made with SQL are not members of neon_superuser (unlike roles made in the Neon
-- console), and Neon requires their passwords to have at least 60 bits of entropy
-- (neon.com/docs/manage/roles, read 2026-09-28).

DO $roles$
DECLARE
  web_pw text := current_setting('twm.web_password', true);
  job_pw text := current_setting('twm.job_password', true);
  flags text := 'NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS';
  r record;
BEGIN
  IF coalesce(length(web_pw), 0) < 16 OR coalesce(length(job_pw), 0) < 16 THEN
    RAISE EXCEPTION 'twm.web_password and twm.job_password must be set (16+ characters each): '
      'run this file with scripts/neon/apply_roles.py (docs/deploy.md)';
  END IF;
  IF web_pw = job_pw THEN
    RAISE EXCEPTION 'the two roles need different passwords';
  END IF;
  FOR r IN SELECT * FROM (VALUES ('twm_web', web_pw, 20), ('twm_job', job_pw, 3))
           AS t(name, pw, conn_limit) LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r.name) THEN
      -- a managed host may refuse to ALTER a role it let us CREATE (seen on Neon by the
      -- owner's F1 app): say so and go on to the grants, which are what a re-run is for
      BEGIN
        EXECUTE format('ALTER ROLE %I WITH LOGIN PASSWORD %L %s CONNECTION LIMIT %s',
                       r.name, r.pw, flags, r.conn_limit);
      EXCEPTION WHEN insufficient_privilege THEN
        RAISE NOTICE '% exists and cannot be altered here (password unchanged)', r.name;
      END;
    ELSE
      EXECUTE format('CREATE ROLE %I WITH LOGIN PASSWORD %L %s CONNECTION LIMIT %s',
                     r.name, r.pw, flags, r.conn_limit);
    END IF;
  END LOOP;
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO twm_web, twm_job', current_database());
END
$roles$;

-- Only the owner creates tables (already the default since Postgres 15; stated anyway).
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO twm_web, twm_job;

-- The site: read every table, now and in the future.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO twm_web;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO twm_web;

-- The job: read and write rows in every table, now and in the future; no DDL anywhere
-- (it owns nothing and has no CREATE on the schema).
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO twm_job;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO twm_job;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO twm_job;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE ON SEQUENCES TO twm_job;
-- pipeline_runs is the run log: rows are added, never changed or removed.
REVOKE UPDATE, DELETE, TRUNCATE ON pipeline_runs FROM twm_job;

-- Defence in depth (the grants above are the control). Wrapped like the ALTER ROLE above.
DO $defaults$
BEGIN
  ALTER ROLE twm_web SET default_transaction_read_only = on;
  ALTER ROLE twm_web SET statement_timeout = '10s';
  ALTER ROLE twm_web SET search_path = 'public';
  ALTER ROLE twm_job SET lock_timeout = '30s';
  ALTER ROLE twm_job SET idle_in_transaction_session_timeout = '10min';
  ALTER ROLE twm_job SET search_path = 'public';
EXCEPTION WHEN insufficient_privilege THEN
  RAISE NOTICE 'role settings unchanged (this host does not allow ALTER ROLE ... SET here)';
END
$defaults$;
