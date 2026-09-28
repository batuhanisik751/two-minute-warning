import "server-only";
import { drizzle, type NodePgDatabase } from "drizzle-orm/node-postgres";
import { Pool } from "pg";
import * as schema from "./schema";
import { databaseUrl } from "./url";

/** Thrown when something tries to read the database during `next build`: every page is
 *  rendered per request, so a read at build time is a bug (the build must work without a
 *  database, as it does in CI). */
export class BuildTimeReadError extends Error {
  constructor() {
    super("the database was read during `next build`; pages must read it per request only");
    this.name = "BuildTimeReadError";
  }
}

// One pool per process, kept on globalThis so the dev server's module reloads do not leak
// connections. Created on the first query, never at import time.
const g = globalThis as unknown as { __twmPool?: Pool; __twmDb?: NodePgDatabase<typeof schema> };

export function db(): NodePgDatabase<typeof schema> {
  if (process.env.NEXT_PHASE === "phase-production-build") throw new BuildTimeReadError();
  if (!g.__twmDb) {
    g.__twmPool ??= new Pool({
      connectionString: databaseUrl(),
      max: 5,
      // fail fast when the database is down instead of hanging the page
      connectionTimeoutMillis: 5_000,
    });
    g.__twmDb = drizzle(g.__twmPool, { schema });
  }
  return g.__twmDb;
}

/** A short fingerprint of the database the site reads (part of every cache key, so a
 *  server pointed at another database never serves the first one's cached rows). */
export function databaseKey(): string {
  const url = databaseUrl();
  let h = 5381;
  for (let i = 0; i < url.length; i++) h = ((h << 5) + h + url.charCodeAt(i)) >>> 0;
  return h.toString(36);
}
