// The accessibility pass in a real browser (headless Chrome, tests/smoke/chrome.ts), where axe
// can measure colour contrast: every route of the smoke set and the not-found page, in the
// light AND the dark theme (prefers-color-scheme, which the site follows unless the reader
// chose a theme), with every fold opened. Zero serious or critical violations; moderate and
// minor ones are reported. Plus a keyboard pass on one page of each kind: the skip link is the
// first stop, visible when focused, and moves focus to <main>; the nav is reachable in order;
// every stop shows a focus indicator.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { after, before, describe, test } from "node:test";
import { Chrome, findChrome, type Tab } from "./chrome";
import { BASE, REQUIRE, serverUp } from "./dom";
import { routeSet } from "./routes";

const axeSource = readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf8");
const SCHEMES = ["light", "dark"] as const;
/** Opens every folded list and table (components/Fold.tsx), as the layout check does. */
const OPEN_FOLDS = `(() => { document.querySelectorAll("details[data-fold]").forEach((d) => { d.open = true; }); return true; })()`;

type Finding = { id: string; impact: string | null; help: string; nodes: number; first: string };

const RUN_AXE = `(async () => {
  const res = await window.axe.run(document, { resultTypes: ["violations"] });
  return res.violations.map((v) => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.length,
    first: (v.nodes[0] && (v.nodes[0].target.join(" ") + " " + (v.nodes[0].failureSummary || "").replace(/\\s+/g, " "))) || "" }));
})()`;

let up = false;
let chrome: Chrome | null = null;
let tab: Tab | null = null;
let skipWhy = "";
let paths: string[] = [];
let keyboardPaths: string[] = [];

before(
  async () => {
    up = await serverUp();
    if (!up) return;
    const exe = findChrome();
    if (!exe) {
      skipWhy = "no Chrome found (set CHROME_PATH)";
      if (REQUIRE) throw new Error(`SMOKE_REQUIRE=1 but the browser accessibility pass cannot run: ${skipWhy}`);
      return;
    }
    chrome = await Chrome.launch(exe);
    tab = await chrome.newTab();
    const set = await routeSet();
    // the not-found page: the unmatched path (server-rendered) and an unknown player (the
    // browser renders the not-found page from the payload)
    paths = [...set.routes.map((r) => r.path), "/no-such-page", "/player/00-0000000"];
    const seen = new Set<string>();
    keyboardPaths = set.routes.filter((r) => !seen.has(r.kind) && seen.add(r.kind)).map((r) => r.path);
  },
  { timeout: 200_000 },
);

after(async () => {
  await chrome?.close();
});

/** The focused element as the reader sees it (null: nothing focused). */
const FOCUSED = `(() => { const el = document.activeElement; if (!el || el === document.body) return null;
  const cs = getComputedStyle(el); const r = el.getBoundingClientRect();
  return { tag: el.tagName.toLowerCase(), id: el.id, href: el.getAttribute("href"),
    label: (el.getAttribute("aria-label") || el.textContent || "").trim().replace(/\\s+/g, " ").slice(0, 40),
    indicator: (cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) >= 1) || cs.boxShadow !== "none",
    onScreen: r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth }; })()`;
type Focused = { tag: string; id: string; href: string | null; label: string; indicator: boolean; onScreen: boolean } | null;

/** React reveals a streamed page (app/(site)/loading.tsx) a moment after the load event (until
 *  then the page waits in a hidden <div>): wait for a DISPLAYED <h1> (at most 5 s) so the audit
 *  is of the page, not its loading state. */
async function settle(t: Tab): Promise<void> {
  await t.evaluate(`new Promise((r) => { const t0 = Date.now(); const shown = () => Array.from(document.querySelectorAll("h1")).some((h) => h.getClientRects().length > 0);
    const f = () => (shown() || Date.now() - t0 > 5000 ? r(true) : setTimeout(f, 50)); f(); })`);
}

async function press(t: Tab, key: "Tab" | "Enter"): Promise<void> {
  const code = key === "Tab" ? 9 : 13;
  await t.cdp("Input.dispatchKeyEvent", { type: "keyDown", key, code: key, windowsVirtualKeyCode: code, ...(key === "Enter" ? { text: "\r" } : {}) });
  await t.cdp("Input.dispatchKeyEvent", { type: "keyUp", key, code: key, windowsVirtualKeyCode: code });
  await t.evaluate("new Promise((r) => requestAnimationFrame(() => r(true)))");
}

describe("accessibility in a real browser (headless Chrome)", () => {
  test("axe with colour contrast: no serious or critical violations, light and dark", { timeout: 900_000 }, async (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    if (!tab) return t.skip(skipWhy);
    assert.ok(paths.length >= 10, `only ${paths.length} pages`);
    const bad: string[] = [];
    const moderate = new Map<string, string[]>();
    await tab.setViewport(1280);
    for (const path of paths) {
      for (const scheme of SCHEMES) {
        await tab.setColorScheme(scheme);
        await tab.goto(BASE + path);
        await settle(tab);
        await tab.evaluate(OPEN_FOLDS);
        await tab.evaluate(`(() => { ${axeSource}; return true; })()`);
        const found = await tab.evaluate<Finding[]>(RUN_AXE);
        for (const v of found) {
          if (v.impact === "serious" || v.impact === "critical") bad.push(`${path} [${scheme}]: ${v.impact} ${v.id} x${v.nodes}: ${v.help}; ${v.first.slice(0, 220)}`);
          else {
            const k = `${v.impact} ${v.id}: ${v.help}`;
            moderate.set(k, [...(moderate.get(k) ?? []), `${path} [${scheme}] x${v.nodes}`]);
          }
        }
      }
    }
    for (const [k, where] of moderate) t.diagnostic(`${k}: ${where.length} page/theme runs, e.g. ${where.slice(0, 4).join(", ")}`);
    t.diagnostic(`${paths.length} pages x ${SCHEMES.length} themes audited`);
    assert.deepEqual(bad.slice(0, 30), [], `${bad.length} serious/critical findings`);
  });

  test("keyboard: skip link first and working, nav in order, a visible focus indicator on every stop", { timeout: 300_000 }, async (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    if (!tab) return t.skip(skipWhy);
    const STOPS = 30;
    const bad: string[] = [];
    await tab.cdp("Emulation.setFocusEmulationEnabled", { enabled: true });
    for (const width of [1280, 375]) {
      await tab.setViewport(width);
      for (const path of keyboardPaths) {
        await tab.goto(BASE + path);
        await settle(tab);
        await press(tab, "Tab");
        const skip = await tab.evaluate<Focused>(FOCUSED);
        if (skip?.href !== "#main" || !skip.onScreen || !skip.indicator) bad.push(`${path} @${width}: first stop ${JSON.stringify(skip)}`);
        await press(tab, "Enter");
        const main = await tab.evaluate<Focused>(FOCUSED);
        if (main?.id !== "main") bad.push(`${path} @${width}: the skip link moved focus to ${JSON.stringify(main)}`);
        await tab.goto(BASE + path);
        await settle(tab);
        const hrefs: string[] = [];
        for (let i = 0; i < STOPS; i++) {
          await press(tab, "Tab");
          const f = await tab.evaluate<Focused>(FOCUSED);
          if (!f) break;
          hrefs.push(f.href ?? "");
          if (!f.indicator || !f.onScreen) bad.push(`${path} @${width}: stop ${i + 1} (${f.tag} "${f.label}") ${f.indicator ? "" : "no focus indicator "}${f.onScreen ? "" : "off screen"}`);
        }
        const nav = ["/", "/waivers", "/regression", "/questionable", "/teammate-out", "/playoff-planner", "/decisions", "/hot-seat", "/board", "/time-machine", "/track-record", "/methodology"];
        const at = nav.map((h) => hrefs.indexOf(h, 2));
        if (at.some((x, i) => x < 0 || (i > 0 && x < at[i - 1]))) bad.push(`${path} @${width}: nav not reached in order: ${hrefs.slice(0, 14).join(" ")}`);
      }
    }
    t.diagnostic(`${keyboardPaths.length} pages x 2 widths, ${STOPS} stops each`);
    assert.deepEqual(bad.slice(0, 30), [], `${bad.length} keyboard problems`);
  });
});
