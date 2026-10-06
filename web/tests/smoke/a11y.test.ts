// The automated accessibility pass: axe-core inside jsdom on every page's server render,
// zero serious or critical violations. jsdom has no layout, so axe cannot measure colour
// contrast here: tests/unit/contrast.test.ts checks the theme tokens instead.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { before, describe, test } from "node:test";
import { BASE, EMPTY_BASE, fetchPage, parse, serverUp } from "./dom";
import { SERVER_RENDERED_404, routeSet } from "./routes";

const axeSource = readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf8");

type Violation = { id: string; impact: string | null; help: string; nodes: { html: string }[] };

async function runAxe(html: string, base = BASE): Promise<Violation[]> {
  const dom = parse(html, base);
  const win = dom.window as unknown as {
    eval(src: string): void;
    axe: { run(ctx: unknown, opts: unknown): Promise<{ violations: Violation[] }> };
    document: Document;
    close(): void;
  };
  win.eval(axeSource);
  const res = await win.axe.run(win.document, {
    resultTypes: ["violations"],
    rules: { "color-contrast": { enabled: false } },
  });
  // copied out of the jsdom realm (its arrays are not Node's)
  const out = Array.from(res.violations, (v) => ({
    id: v.id,
    impact: v.impact,
    help: v.help,
    nodes: Array.from(v.nodes, (n) => ({ html: n.html })),
  }));
  win.close();
  return out;
}

let up = false;
let emptyUp = false;
const found = new Map<string, Violation[]>();

before(
  async () => {
    up = await serverUp();
    if (!up) return;
    const set = await routeSet();
    // page-level 404s are Next's client-rendered error shell (tests/smoke/routes.ts); the
    // same not-found page is audited server-rendered at the unmatched path
    for (const r of [...set.routes.map((x) => x.path), ...set.missing.filter((m) => SERVER_RENDERED_404.has(m))]) {
      found.set(r, await runAxe((await fetchPage(r)).html));
    }
    if (EMPTY_BASE) {
      emptyUp = await serverUp(EMPTY_BASE);
      for (const r of ["/", "/waivers", "/methodology", "/regression", "/questionable", "/teammate-out", "/playoff-planner", "/decisions", "/track-record", "/hot-seat", "/board", "/time-machine"]) {
        found.set(`empty:${r}`, await runAxe((await fetchPage(r, EMPTY_BASE)).html, EMPTY_BASE));
      }
    }
  },
  { timeout: 600_000 },
);

describe("axe-core", () => {
  test("no serious or critical violations on any page", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    assert.ok(found.size >= 10, `only ${found.size} pages audited`);
    const bad: string[] = [];
    for (const [route, list] of found) {
      for (const v of list.filter((x) => x.impact === "serious" || x.impact === "critical")) {
        bad.push(`${route}: ${v.impact} ${v.id}: ${v.help} (${v.nodes.length}) ${v.nodes[0]?.html.slice(0, 160)}`);
      }
    }
    assert.deepEqual(bad, []);
    t.diagnostic(`${found.size} pages audited${emptyUp ? " (incl. the empty-database pages)" : ""}`);
  });

  test("moderate and minor findings are reported, not asserted", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    for (const [route, list] of found) {
      for (const v of list.filter((x) => x.impact !== "serious" && x.impact !== "critical")) {
        t.diagnostic(`${route}: ${v.impact} ${v.id} x${v.nodes.length}: ${v.help}`);
      }
    }
  });

  test("the harness detects a violation (canary)", async () => {
    const broken = await runAxe('<!doctype html><html lang="en"><body><a href="#x"></a><input type="text"><img src="a.png"></body></html>');
    const ids = broken.map((v) => v.id);
    assert.ok(ids.includes("link-name"), ids.join(", "));
    assert.ok(ids.includes("image-alt"), ids.join(", "));
  });
});
