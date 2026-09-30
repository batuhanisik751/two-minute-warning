import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { readNewestReport } from "../../lib/my-league-fs";
import { myLeagueEnabled, newestReport } from "../../lib/my-league";

test("the /league page is on only with the flag AND under next dev", () => {
  assert.equal(myLeagueEnabled({ ENABLE_MY_LEAGUE: "true", NODE_ENV: "development" }), true);
  for (const env of [
    { ENABLE_MY_LEAGUE: "true", NODE_ENV: "production" },
    { ENABLE_MY_LEAGUE: "true", NODE_ENV: "test" },
    { ENABLE_MY_LEAGUE: "true" },
    { ENABLE_MY_LEAGUE: "1", NODE_ENV: "development" },
    { ENABLE_MY_LEAGUE: "TRUE", NODE_ENV: "development" },
    { NODE_ENV: "development" },
  ]) {
    assert.equal(myLeagueEnabled(env), false, JSON.stringify(env));
  }
});

test("the newest report is by season, then week; other files are ignored", () => {
  assert.equal(newestReport(["2026-W03.html", "2026-W10.html", "2025-W17.html"]), "2026-W10.html");
  assert.equal(newestReport(["2025-W17.html", "unmatched_players.md", "2027-W1.html", "x.html"]), "2025-W17.html");
  assert.equal(newestReport(["unmatched_players.md"]), null);
  assert.equal(newestReport([]), null);
});

test("readNewestReport reads the newest file, and null for a missing or empty folder", async () => {
  const dir = mkdtempSync(join(tmpdir(), "twm-league-"));
  try {
    assert.equal(await readNewestReport(join(dir, "missing")), null);
    assert.equal(await readNewestReport(dir), null);
    writeFileSync(join(dir, "2026-W02.html"), "<p>old</p>");
    writeFileSync(join(dir, "2026-W03.html"), "<p>new</p>");
    assert.deepEqual(await readNewestReport(dir), { name: "2026-W03.html", html: "<p>new</p>" });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
