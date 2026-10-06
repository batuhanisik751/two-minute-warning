// The overlap check: the list pages rendered by a real browser (headless Chrome, driven over
// the DevTools protocol by tests/smoke/chrome.ts) at widths from 320 to 1920 px. In every
// row of a list ([data-row]: pick rows, the column header row, the home page's top-of-the-
// board cards) it measures the box of every line of text (Range.getClientRects, clipped by
// any ancestor that hides overflow) and fails when two text boxes intersect, when text sticks
// out of its row, or when the page scrolls sideways. A canary proves the check catches a
// broken layout. jsdom has no layout, so this cannot run in the other smoke suites.
import assert from "node:assert/strict";
import { after, before, describe, test } from "node:test";
import { Chrome, findChrome, type Tab } from "./chrome";
import { BASE, DATA, REQUIRE, serverUp } from "./dom";
import { routeSet } from "./routes";

export const WIDTHS = [320, 360, 390, 414, 600, 768, 800, 1024, 1280, 1440, 1920];

type Report = { rows: number; folded: number; texts: number; problems: string[] };

/** Opens every folded list and table ("Show all N", components/Fold.tsx), so the check measures
 *  the folded rows too. Returns how many it opened. */
export const OPEN_FOLDS = `(() => { const ds = Array.from(document.querySelectorAll("details[data-fold]")); ds.forEach((d) => { d.open = true; }); return ds.length; })()`;

/** Runs in the page. Returns what it measured and every problem found. */
export const CHECK = String.raw`(() => {
  const TOL = 1;
  const out = { rows: 0, folded: 0, texts: 0, problems: [] };
  const de = document.documentElement;
  if (de.scrollWidth > de.clientWidth + 1) out.problems.push("the page scrolls sideways: " + de.scrollWidth + " > " + de.clientWidth + " px");
  const clipOf = (el, stop) => {
    let r = { left: -Infinity, top: -Infinity, right: Infinity, bottom: Infinity };
    for (let a = el; a; a = a.parentElement) {
      const cs = getComputedStyle(a);
      if (cs.overflowX !== "visible" || cs.overflowY !== "visible") {
        const b = a.getBoundingClientRect();
        r = { left: Math.max(r.left, b.left), top: Math.max(r.top, b.top), right: Math.min(r.right, b.right), bottom: Math.min(r.bottom, b.bottom) };
      }
      if (a === stop) break;
    }
    return r;
  };
  for (const row of document.querySelectorAll("[data-row]")) {
    const rb = row.getBoundingClientRect();
    if (rb.width === 0 || rb.height === 0) continue; // not displayed at this width
    out.rows++;
    // folded: in an opened list's <details>, or in a folded table's second <tbody> (components/Fold.tsx)
    if (row.closest("details[data-fold], tbody[data-fold-rest]")) out.folded++;
    const label = ((row.querySelector("[data-cell=player] a") || row).textContent || "").trim().slice(0, 40);
    const boxes = [];
    for (const cell of row.querySelectorAll("[data-cell]")) {
      if (cell.closest("[data-row]") !== row) continue;
      const walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
      let n;
      while ((n = walker.nextNode())) {
        const text = n.nodeValue.trim();
        const el = n.parentElement;
        if (!text || !el || el.closest(".sr-only, [hidden]")) continue;
        const clip = clipOf(el, row);
        const range = document.createRange();
        range.selectNodeContents(n);
        for (const r of range.getClientRects()) {
          const b = { left: Math.max(r.left, clip.left), top: Math.max(r.top, clip.top), right: Math.min(r.right, clip.right), bottom: Math.min(r.bottom, clip.bottom) };
          if (b.right - b.left <= TOL || b.bottom - b.top <= TOL) continue;
          boxes.push({ node: n, cell: cell.dataset.cell, text: text.slice(0, 32), ...b });
        }
      }
    }
    out.texts += boxes.length;
    for (let i = 0; i < boxes.length; i++) {
      const a = boxes[i];
      if (a.left < rb.left - TOL || a.right > rb.right + TOL) {
        out.problems.push(label + ': "' + a.text + '" (' + a.cell + ") sticks out of its row");
      }
      for (let j = i + 1; j < boxes.length; j++) {
        const b = boxes[j];
        if (a.node === b.node) continue;
        const w = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        const h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        if (w > TOL && h > TOL) {
          out.problems.push(label + ': "' + a.text + '" (' + a.cell + ') overlaps "' + b.text + '" (' + b.cell + ") by " + w.toFixed(1) + " x " + h.toFixed(1) + " px");
        }
      }
    }
  }
  return out;
})()`;

let up = false;
let chrome: Chrome | null = null;
let tab: Tab | null = null;
let paths: string[] = [];
let skipWhy = "";

before(
  async () => {
    up = await serverUp();
    if (!up) return;
    const exe = findChrome();
    if (!exe) {
      skipWhy = "no Chrome found (set CHROME_PATH)";
      if (REQUIRE) throw new Error(`SMOKE_REQUIRE=1 but the layout check cannot run: ${skipWhy}`);
      console.warn(`\n  SKIPPING the layout check: ${skipWhy}\n`);
      return;
    }
    chrome = await Chrome.launch(exe);
    tab = await chrome.newTab();
    const set = await routeSet();
    // every page that shows a list: home (half-width cards, the FLEX card, the top-of-the-board
    // row, the Streamers and Regression flags cards), the position lists, FLEX, reconstructed
    // lists without chances, the K and D/ST lists, every Regression Watch table, and the Decision
    // Report Card's call and clock-case rows (/decisions, the coach pages, the home card),
    // /track-record's headline tiles and live rows, the Hot-Seat rows (/hot-seat, the home card) and
    // the Cliff board's rows (/board, the home card)
    paths = set.routes.filter((r) => ["home", "waivers", "waivers-live", "waivers-flex", "waivers-backtest", "waivers-stream", "regression", "decisions", "coach", "track-record", "hot-seat", "board", "time-machine", "questionable", "teammate-out", "playoff-planner"].includes(r.kind)).map((r) => r.path);
  },
  // two Chrome start attempts of up to 60 s each, plus the connection
  { timeout: 200_000 },
);

after(async () => {
  await chrome?.close();
});

describe("layout (headless Chrome)", () => {
  test("no text overlaps in any list row, and no sideways scroll, from 320 to 1920 px", { timeout: 600_000 }, async (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    if (!tab) return t.skip(skipWhy);
    assert.ok(paths.length >= 5, `only ${paths.length} pages to check`);
    const bad: string[] = [];
    let rows = 0;
    let texts = 0;
    let folds = 0;
    let foldedRows = 0;
    for (const path of paths) {
      await tab.setViewport(1280);
      await tab.goto(BASE + path);
      const opened = await tab.evaluate<number>(OPEN_FOLDS);
      folds += opened;
      let seen = 0;
      let folded = 0;
      for (const w of WIDTHS) {
        await tab.setViewport(w);
        const r = await tab.evaluate<Report>(CHECK);
        seen += r.rows;
        folded += r.folded;
        rows += r.rows;
        texts += r.texts;
        for (const p of r.problems) bad.push(`${path} @ ${w}px: ${p}`);
      }
      // real data: a weekly list may honestly be empty (e.g. Questionable before Friday's reports);
      // the page then shows its empty state ("…-empty", not a live-record block) instead of rows
      const empty = DATA === "real" && (await tab.evaluate<boolean>(`!!document.querySelector('[data-testid$="-empty"]:not([data-live])')`));
      assert.ok(seen > 0 || empty, `${path}: no rows measured`);
      if (opened) assert.ok(folded > 0, `${path}: ${opened} folded lists opened, but none of their rows measured`);
      foldedRows += folded;
    }
    t.diagnostic(`${paths.length} pages x ${WIDTHS.length} widths: ${rows} rows (${foldedRows} in ${folds} opened folds), ${texts} text boxes measured`);
    if (DATA === "seed") assert.ok(folds > 0, "the seed has folded lists, but none was checked");
    assert.deepEqual(bad.slice(0, 40), [], `${bad.length} problems`);
  });

  test("the check catches overlapping text (canary)", { timeout: 120_000 }, async (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    if (!tab) return t.skip(skipWhy);
    const list = paths.find((p) => p.includes("kind=live") && !p.includes("FLEX")) ?? paths[0];
    await tab.setViewport(1280);
    await tab.goto(BASE + list);
    const clean = await tab.evaluate<Report>(CHECK);
    assert.deepEqual(clean.problems, []);
    // put the chance into the player's cell, as the old half-width rows did
    await tab.evaluate(`(() => { const s = document.createElement("style"); s.textContent = "[data-cell=chance] { grid-area: player !important }"; document.head.append(s); return true; })()`);
    const broken = await tab.evaluate<Report>(CHECK);
    assert.ok(broken.problems.some((p) => / overlaps /.test(p)), `the canary was not caught: ${broken.problems.slice(0, 3).join("; ")}`);
    // and a row wider than the page
    await tab.evaluate(`(() => { const s = document.createElement("style"); s.textContent = "[data-testid=pick-list] { width: 2400px }"; document.head.append(s); return true; })()`);
    const wide = await tab.evaluate<Report>(CHECK);
    assert.ok(wide.problems.some((p) => /scrolls sideways/.test(p)), "a page wider than the window was not caught");
  });
});
