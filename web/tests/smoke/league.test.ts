// /league (My League, PROJECT_SPEC 8.3) is local only. Against the production server (`next
// start`, which tests/run-smoke.ts starts WITH ENABLE_MY_LEAGUE=true and a fixture report
// folder) it must answer 404 and leak nothing; no public page links to it. Under `next dev`
// with the flag it shows the newest fixture report: that part starts its own `next dev` on a
// free port and skips loudly when it cannot (e.g. another `next dev` holds this app's lock).
import assert from "node:assert/strict";
import { spawn, type ChildProcess } from "node:child_process";
import { rmSync } from "node:fs";
import { createServer } from "node:net";
import { dirname, join } from "node:path";
import { after, describe, test } from "node:test";
import { fileURLToPath } from "node:url";
import { BASE, fetchPage, serverUp } from "./dom";
import { FAKE_TEAM, NEWEST_MARK, OLDER_MARK, writeLeagueFixture } from "./league-fixture";

const web = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

describe("/league on the production server", () => {
  test("answers 404 even with ENABLE_MY_LEAGUE=true, and shows no report", async (t) => {
    if (!(await serverUp())) return t.skip(`no server at ${BASE}`);
    if (!process.env.SMOKE_LEAGUE_DIR) t.diagnostic("server not started by run-smoke: the flag may be unset");
    for (const path of ["/league", "/league?week=3"]) {
      const p = await fetchPage(path);
      assert.equal(p.status, 404, `${path} returned ${p.status}`);
      for (const s of [NEWEST_MARK, OLDER_MARK, FAKE_TEAM]) assert.ok(!p.html.includes(s), `${path}: ${s} leaked`);
      // the title (also in the payload the browser renders) is the 404's, not the local page's
      assert.ok(!p.html.includes("My League"), `${path}: the local page's title leaked`);
    }
  });

  test("no public page or robots.txt links to it", async (t) => {
    if (!(await serverUp())) return t.skip(`no server at ${BASE}`);
    for (const path of ["/", "/waivers", "/regression", "/methodology"]) {
      const p = await fetchPage(path);
      const links = [...p.doc.querySelectorAll("a[href]")].map((a) => a.getAttribute("href") ?? "");
      assert.ok(!links.some((h) => /^\/league(\/|\?|$)/.test(h)), `${path} links to /league`);
    }
    const robots = await (await fetch(`${BASE}/robots.txt`)).text();
    assert.ok(!robots.includes("league"), "robots.txt names the route");
  });
});

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

async function stop(child: ChildProcess): Promise<void> {
  if (child.pid === undefined || child.exitCode !== null || child.signalCode !== null) return;
  const gone = new Promise<void>((resolve) => child.once("exit", () => resolve()));
  const signal = (sig: NodeJS.Signals) => {
    try {
      process.kill(-child.pid!, sig); // its whole process group (next dev starts workers)
    } catch {
      /* already gone */
    }
  };
  signal("SIGTERM");
  const timer = setTimeout(() => signal("SIGKILL"), 5000);
  await gone;
  clearTimeout(timer);
  signal("SIGKILL");
}

/** `next dev` with the flag and a fixture folder; null (and why) when it does not come up. */
async function startDev(dir: string): Promise<{ base: string; child: ChildProcess } | { why: string }> {
  const port = await freePort();
  // next dev runs as "development" (set here too, over a runner's "test"); no real database:
  // nothing listens on port 1, so the layout's data-as-of line says the database is unavailable
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    NODE_ENV: "development",
    ENABLE_MY_LEAGUE: "true",
    LEAGUE_REPORTS_DIR: dir,
    PORT: String(port),
    TWM_LOCAL_DATABASE_URL: "postgres://twm_web_test@127.0.0.1:1/twm_web_test_none",
  };
  delete env.DATABASE_URL;
  const child = spawn(join(web, "node_modules", ".bin", "next"), ["dev", "-p", String(port), "-H", "127.0.0.1"], {
    cwd: web,
    env,
    stdio: ["ignore", "pipe", "pipe"],
    detached: true,
  });
  const log: string[] = [];
  child.stdout?.on("data", (d) => log.push(String(d)));
  child.stderr?.on("data", (d) => log.push(String(d)));
  const base = `http://127.0.0.1:${port}`;
  const t0 = Date.now();
  while (Date.now() - t0 < 90_000) {
    if (child.exitCode !== null) return { why: `next dev exited (${child.exitCode}): ${log.join("").slice(-400)}` };
    try {
      if ((await fetch(`${base}/robots.txt`, { signal: AbortSignal.timeout(5000) })).ok) return { base, child };
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  await stop(child);
  return { why: `next dev did not answer within 90 s: ${log.join("").slice(-400)}` };
}

describe("/league under next dev with ENABLE_MY_LEAGUE=true", () => {
  const dir = writeLeagueFixture();
  let child: ChildProcess | null = null;
  after(async () => {
    if (child) await stop(child);
    rmSync(dir, { recursive: true, force: true });
  });

  test("renders the newest fixture report", { timeout: 240_000 }, async (t) => {
    const dev = await startDev(dir);
    if ("why" in dev) {
      console.warn(`SKIPPED LOUDLY: /league dev smoke could not start next dev: ${dev.why}`);
      return t.skip(`could not start next dev: ${dev.why}`);
    }
    child = dev.child;
    const p = await fetchPage("/league", dev.base);
    assert.equal(p.status, 200, `/league under next dev returned ${p.status}`);
    const frame = p.doc.querySelector("iframe[data-testid=league-report]");
    assert.ok(frame, "no report frame");
    const src = frame.getAttribute("srcdoc") ?? "";
    assert.ok(src.includes(NEWEST_MARK) && src.includes(FAKE_TEAM), "the newest fixture report is not shown");
    assert.ok(!p.html.includes(OLDER_MARK), "an older report is shown");
    assert.equal(p.doc.querySelector("[data-testid=league-report-name]")?.textContent, "2026-W03.html");
    assert.equal(frame.getAttribute("sandbox"), "", "the report frame must be sandboxed");
  });
});
