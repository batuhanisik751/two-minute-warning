// Creates the web tests' databases on the LOCAL Postgres server, applies the Drizzle
// migrations (web/drizzle) with drizzle-orm's own migrator, and loads the fictional seed
// (tests/seed.ts):
//
//   twm_web_test        the "full" seed (the smoke and accessibility tests)
//   twm_web_test_empty  the "empty" seed (a first-publish state, for the empty states)
//
// Usage: npm run db:test-setup
// Server: TWM_LOCAL_DATABASE_URL, else DATABASE_URL, else the docker-compose default (the
// server is only used to create the two databases; its own database is never changed).
// Refuses any host other than this computer, and any database name not starting with
// "twm_web_test". Never prints a connection string.
import { drizzle } from "drizzle-orm/node-postgres";
import { migrate } from "drizzle-orm/node-postgres/migrator";
import { Client, Pool } from "pg";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import * as schema from "../db/schema";
import { LOCAL_DEFAULT_URL, describe, isLocalUrl, withDatabase } from "../db/url";
import { seed, type SeedVariant } from "./seed";

export const TEST_DATABASES: Record<SeedVariant, string> = {
  full: "twm_web_test",
  empty: "twm_web_test_empty",
};

const here = dirname(fileURLToPath(import.meta.url));

export function serverUrl(env: Record<string, string | undefined> = process.env): string {
  return env.TWM_LOCAL_DATABASE_URL?.trim() || env.DATABASE_URL?.trim() || LOCAL_DEFAULT_URL;
}

export async function setupDatabase(variant: SeedVariant, server = serverUrl()): Promise<string> {
  const name = TEST_DATABASES[variant];
  if (!isLocalUrl(server)) {
    throw new Error(`refusing to create test databases on a remote server (${describe(server)})`);
  }
  if (!/^twm_web_test(_[a-z]+)?$/.test(name)) throw new Error(`refusing database name ${name}`);
  const admin = new Client({ connectionString: server });
  await admin.connect();
  try {
    await admin.query(`DROP DATABASE IF EXISTS "${name}" WITH (FORCE)`);
    await admin.query(`CREATE DATABASE "${name}"`);
  } finally {
    await admin.end();
  }
  const url = withDatabase(server, name);
  const pool = new Pool({ connectionString: url, max: 2 });
  try {
    const db = drizzle(pool, { schema });
    await migrate(db, { migrationsFolder: join(here, "..", "drizzle") });
    await seed(db, variant);
  } finally {
    await pool.end();
  }
  console.log(`seeded ${describe(url)} (${variant})`);
  return url;
}

async function main() {
  for (const v of ["full", "empty"] as const) await setupDatabase(v);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main().catch((err) => {
    // the message only: a driver error can quote connection details
    console.error(`db:test-setup failed: ${err instanceof Error ? err.message : String(err)}`);
    process.exit(1);
  });
}
