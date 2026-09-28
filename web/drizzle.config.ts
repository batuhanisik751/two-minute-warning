import { defineConfig } from "drizzle-kit";

// `npm run db:migrate` applies web/drizzle/*.sql to the database in MIGRATE_DATABASE_URL:
// the Neon OWNER connection (docs/deploy.md), never the site's read-only role or the job's
// writer role (neither may change tables). Without it, the local Docker database
// (docker-compose.yml; local development only) is migrated.
// `npm run db:generate` and `npm run db:check` never connect to a database.
export default defineConfig({
  dialect: "postgresql",
  schema: "./db/schema.ts",
  out: "./drizzle",
  dbCredentials: {
    url: process.env.MIGRATE_DATABASE_URL ?? "postgres://twm:twm@127.0.0.1:5434/twm",
  },
  strict: true,
  verbose: true,
});
