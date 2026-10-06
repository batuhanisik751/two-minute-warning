// Runs the smoke and accessibility tests against the BUILT app (`npm run build` first):
//
//   npm run test:smoke:run            seed the test databases (tests/setup-db.ts), start
//                                     `next start` twice (full seed and empty seed), run
//                                     tests/smoke with SMOKE_REQUIRE=1 (incl. the overlap
//                                     check in headless Chrome); then the public pass: a
//                                     third server with SITE_PUBLIC=true on the same data,
//                                     tests/smoke/public.test.ts (no per-player FantasyPros
//                                     value on the public site); stop the servers
//   npm run test:smoke:run -- --real  the same suite against the real local publish
//                                     (database `twm` of docker-compose.yml, read only:
//                                     nothing is created, seeded or written), assertions
//                                     that hold for any data only
//   ... -- --keep                     leave the server(s) running afterwards (for
//                                     screenshots); prints their addresses and process ids
//
// The servers get their database through TWM_LOCAL_DATABASE_URL, built here from the local
// server's address; DATABASE_URL is removed from their environment so a stray production
// value can never be used. Connection strings are never printed.
import { spawn, type ChildProcess } from "node:child_process";
import { existsSync, openSync, rmSync } from "node:fs";
import { createServer } from "node:net";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, isLocalUrl, withDatabase } from "../db/url";
import { TEST_DATABASES, serverUrl, setupDatabase } from "./setup-db";
import { writeLeagueFixture } from "./smoke/league-fixture";

const web = join(dirname(fileURLToPath(import.meta.url)), "..");
const args = new Set(process.argv.slice(2));
const REAL = args.has("--real");
const KEEP = args.has("--keep");
const NO_SETUP = args.has("--no-setup");
// My League (PROJECT_SPEC 8.3): the production servers get the flag AND a fixture report folder,
// so tests/smoke/league.test.ts proves `next start` still answers 404 at /league.
const LEAGUE_DIR = writeLeagueFixture();
// --keep: the servers outlive this script and still point at the folder
if (!KEEP) process.on("exit", () => rmSync(LEAGUE_DIR, { recursive: true, force: true }));

function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const s = createServer();
    s.unref();
    s.on("error", reject);
    s.listen(0, "127.0.0.1", () => {
      const a = s.address();
      const port = typeof a === "object" && a ? a.port : 0;
      s.close(() => resolve(port));
    });
  });
}

async function waitUp(base: string, child: ChildProcess, ms = 60_000): Promise<void> {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if (child.exitCode !== null) throw new Error(`the server at ${base} exited with code ${child.exitCode}`);
    try {
      const r = await fetch(`${base}/robots.txt`, { signal: AbortSignal.timeout(2000) });
      if (r.ok) return;
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error(`the server at ${base} did not answer within ${ms / 1000} s`);
}

async function startServer(dbUrl: string, extra: Record<string, string> = {}): Promise<{ base: string; child: ChildProcess }> {
  const port = await freePort();
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    TWM_LOCAL_DATABASE_URL: dbUrl,
    PORT: String(port),
    ENABLE_MY_LEAGUE: "true",
    LEAGUE_REPORTS_DIR: LEAGUE_DIR,
  };
  delete env.DATABASE_URL;
  // the private servers are private and the public one public, whatever this shell says; the
  // testing override of lib/third-party.ts never reaches a smoke server
  delete env.SITE_PUBLIC;
  delete env.SHOW_THIRD_PARTY_RANKS;
  Object.assign(env, extra);
  // --keep: the server outlives this script, so its output goes to a file under .next/
  const logFile = KEEP ? openSync(join(web, ".next", `smoke-server-${port}.log`), "a") : null;
  const child = spawn(join(web, "node_modules", ".bin", "next"), ["start", "-p", String(port), "-H", "127.0.0.1"], {
    cwd: web,
    env,
    stdio: logFile !== null ? ["ignore", logFile, logFile] : ["ignore", "pipe", "pipe"],
    // Its own process group, always: `next start` runs the real server as a child process, so
    // stopping only `next` can orphan that child (seen 2026-09-28: a leaked next-server on a
    // random port). stopServer signals the whole group.
    detached: true,
  });
  const log: string[] = [];
  child.stdout?.on("data", (d) => log.push(String(d)));
  child.stderr?.on("data", (d) => log.push(String(d)));
  const base = `http://127.0.0.1:${port}`;
  try {
    await waitUp(base, child);
  } catch (err) {
    console.error(log.join(""));
    await stopServer(child);
    throw err;
  }
  console.log(`server up at ${base} (${describe(dbUrl)}), pid ${child.pid}`);
  if (KEEP) child.unref();
  return { base, child };
}

/** Stop a server and everything it started (its process group), and wait until it is gone. */
async function stopServer(child: ChildProcess): Promise<void> {
  if (child.pid === undefined || child.exitCode !== null || child.signalCode !== null) return;
  const gone = new Promise<void>((resolve) => child.once("exit", () => resolve()));
  const signal = (sig: NodeJS.Signals) => {
    try {
      process.kill(-child.pid!, sig);
    } catch {
      /* the group is already gone */
    }
  };
  signal("SIGTERM");
  const timer = setTimeout(() => signal("SIGKILL"), 5000);
  await gone;
  clearTimeout(timer);
  signal("SIGKILL"); // any grandchild that ignored SIGTERM
}

// The private pass; the public pass (tests/smoke/public.test.ts) runs on its own server.
// layout: the overlap check in headless Chrome (tests/smoke/layout.test.ts); terms: every metric
// header is a glossary term (tests/smoke/terms.test.ts)
const PRIVATE_FILES = ["pages", "modules", "decisions", "hot-seat", "board", "questionable", "teammate-out", "playoff-planner", "time-machine", "track", "terms", "a11y", "a11y-browser", "empty", "layout", "league"];

function runTests(env: Record<string, string>, names: string[] = PRIVATE_FILES): Promise<number> {
  return new Promise((resolve) => {
    const files = names.map((f) => join("tests", "smoke", `${f}.test.ts`));
    const child = spawn(
      join(web, "node_modules", ".bin", "tsx"),
      ["--test", "--test-concurrency=1", "--test-reporter=spec", ...files],
      { cwd: web, env: { ...process.env, ...env }, stdio: "inherit" },
    );
    child.on("exit", (code) => resolve(code ?? 1));
  });
}

async function main(): Promise<number> {
  if (!existsSync(join(web, ".next", "BUILD_ID"))) {
    console.error("no production build: run `npm run build` first");
    return 1;
  }
  const server = serverUrl();
  if (!isLocalUrl(server)) {
    console.error(`refusing: the database server is not on this computer (${describe(server)})`);
    return 1;
  }
  const servers: ChildProcess[] = [];
  try {
    let fullUrl: string;
    let emptyUrl: string | null = null;
    if (REAL) {
      fullUrl = withDatabase(server, "twm");
    } else if (NO_SETUP) {
      fullUrl = withDatabase(server, TEST_DATABASES.full);
      emptyUrl = withDatabase(server, TEST_DATABASES.empty);
    } else {
      fullUrl = await setupDatabase("full", server);
      emptyUrl = await setupDatabase("empty", server);
    }
    // Next's data cache (lib/cache.ts, 1 hour, on disk under .next/cache) is keyed by the database
    // URL, and the test databases were just recreated under the same URLs: without this a run
    // within the hour of the last one serves the old seed's rows (seen 2026-09-30, step W3).
    rmSync(join(web, ".next", "cache", "fetch-cache"), { recursive: true, force: true });
    const full = await startServer(fullUrl);
    servers.push(full.child);
    let emptyBase: string | undefined;
    if (emptyUrl) {
      const empty = await startServer(emptyUrl);
      servers.push(empty.child);
      emptyBase = empty.base;
    }
    const code = await runTests({
      SMOKE_BASE_URL: full.base,
      ...(emptyBase ? { SMOKE_EMPTY_BASE_URL: emptyBase } : {}),
      SMOKE_REQUIRE: "1",
      SMOKE_DATA: REAL ? "real" : "seed",
      SMOKE_LEAGUE_DIR: LEAGUE_DIR,
    });
    // The public pass (FIX-W, license): the same data behind SITE_PUBLIC=true; it compares each
    // page with the private server's, so that one stays up until it is done.
    const pub = await startServer(fullUrl, { SITE_PUBLIC: "true" });
    servers.push(pub.child);
    console.log("public pass: SITE_PUBLIC=true");
    const pubCode = await runTests(
      {
        SMOKE_BASE_URL: pub.base,
        SMOKE_PRIVATE_BASE_URL: full.base,
        SMOKE_PUBLIC: "1",
        SMOKE_REQUIRE: "1",
        SMOKE_DATA: REAL ? "real" : "seed",
      },
      ["public"],
    );
    return code || pubCode;
  } catch (err) {
    console.error(`smoke run failed: ${err instanceof Error ? err.message : String(err)}`);
    return 1;
  } finally {
    if (KEEP) {
      console.log(`--keep: servers left running (pids ${servers.map((s) => s.pid).join(", ")}); stop them with kill`);
    } else {
      await Promise.all(servers.map(stopServer));
    }
  }
}

main().then((code) => process.exit(code));
