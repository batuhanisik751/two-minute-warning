// Which database the site reads, and the guard rails around it.
//
// - DATABASE_URL: set in Vercel to the site's READ-ONLY role (twm_web, docs/deploy.md), and in
//   CI to the throwaway service database the smoke tests seed.
// - TWM_LOCAL_DATABASE_URL: the local Docker database (docker-compose.yml), the same variable
//   `twm publish --target local` uses.
// - Neither: the local Docker database's default (local development only; its user name and
//   password are not secrets, see docker-compose.yml).
//
// A connection string is never logged or shown on a page: errors name the host and database
// at most (describe()).

export const LOCAL_DEFAULT_URL = "postgres://twm:twm@127.0.0.1:5434/twm";

type Env = Record<string, string | undefined>;

export function databaseUrl(env: Env = process.env): string {
  const set = (v: string | undefined) => (v && v.trim() ? v.trim() : undefined);
  return set(env.DATABASE_URL) ?? set(env.TWM_LOCAL_DATABASE_URL) ?? LOCAL_DEFAULT_URL;
}

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "::1", "[::1]"]);

/** True when the connection string points at this computer (the only place the test setup
 *  may create or drop databases). */
export function isLocalUrl(url: string): boolean {
  try {
    return LOCAL_HOSTS.has(new URL(url).hostname);
  } catch {
    return false;
  }
}

/** "database twm on 127.0.0.1:5434": safe to print (no user name, no password). */
export function describe(url: string): string {
  try {
    const u = new URL(url);
    const db = decodeURIComponent(u.pathname.replace(/^\//, "")) || "(default)";
    return `database ${db} on ${u.hostname}${u.port ? `:${u.port}` : ""}`;
  } catch {
    return "an unparseable connection string";
  }
}

/** The same server, another database (for the test setup). */
export function withDatabase(url: string, database: string): string {
  const u = new URL(url);
  u.pathname = `/${encodeURIComponent(database)}`;
  return u.toString();
}
