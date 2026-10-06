// Smoke tests of the playoff planner (feature #6): /playoff-planner (the grid of a rated position,
// sorting, an unrated position with the reason, the effect sizes, the candidates, the live record
// before week 17, the empty seed), the player line and the /methodology and /track-record
// sections. Seed-only checks use tests/seed-playoff-planner.ts; against a real publish only what
// holds for any data.
import assert from "node:assert/strict";
import { before, describe, test } from "node:test";
import { LEAGUES_DIFFER, PLANNER_NOTE } from "../../lib/playoff-planner";
import { SEED } from "../seed";
import { PLAYOFF_SEED as P } from "../seed-playoff-planner";
import { BASE, DATA, EMPTY_BASE, fetchPage, prose, serverUp, type Page } from "./dom";

let up = false;
let emptyUp = false;
const pages = new Map<string, Page>();
const empty = new Map<string, Page>();
const seedOnly = DATA === "seed";
const SORTED = "/playoff-planner?pos=RB&sort=total";
const PATHS = ["/playoff-planner", "/playoff-planner?pos=TE", "/methodology", "/track-record", ...(seedOnly ? [SORTED, `/player/${SEED.featured}`, `/player/${P.te}`] : [])];

before(
  async () => {
    up = await serverUp();
    if (!up) return;
    for (const p of PATHS) pages.set(p, await fetchPage(p));
    if (EMPTY_BASE && seedOnly) {
      emptyUp = await serverUp(EMPTY_BASE);
      if (emptyUp) for (const p of ["/playoff-planner", "/track-record"]) empty.set(p, await fetchPage(p, EMPTY_BASE));
    }
  },
  { timeout: 300_000 },
);

const main = (path: string, from = pages): Element => {
  const m = from.get(path)?.doc.querySelector("main#main");
  assert.ok(m, `${path}: no <main id=main>`);
  return m;
};
const all = (m: Element, sel: string) => Array.from(m.querySelectorAll(sel));

describe("/playoff-planner", () => {
  test("the page, its four sections, the leagues-differ note and the nav label", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    const p = pages.get("/playoff-planner")!;
    assert.equal(p.status, 200);
    const m = main("/playoff-planner");
    assert.equal(prose(m.querySelector("h1")!), "Playoff planner");
    for (const id of ["pp-grid-section", "pp-matters-section", "pp-candidates-section", "pp-live-section"]) assert.ok(m.querySelector(`[data-testid=${id}]`), id);
    assert.ok(prose(m).includes(LEAGUES_DIFFER), "the leagues-differ note");
    const nav = p.doc.querySelector("nav[aria-label=Main] a[aria-current=page]");
    assert.equal(nav?.getAttribute("href"), "/playoff-planner");
    assert.equal(prose(nav!), "Playoffs");
  });

  test("the grid of a rated position: every team, three weeks, ratings with a word, byes", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const m = main("/playoff-planner");
    const g = m.querySelector("[data-testid=pp-grid]")!;
    assert.deepEqual([g.getAttribute("data-pos"), g.getAttribute("data-rated")], ["QB", "yes"]);
    assert.match(prose(m.querySelector("[data-testid=pp-asof]")!), new RegExp(`through week ${P.throughWeeks[1]} `));
    assert.deepEqual(all(g, "tbody tr").map((r) => r.getAttribute("data-team")), ["EVP", "NHG", "SRO", "WLF"]);
    const nhg = g.querySelector("tr[data-team=NHG]")!;
    assert.deepEqual(all(nhg, "td[data-week]").map((c) => c.getAttribute("data-band")), ["easy", "neutral", "easy"]);
    assert.match(prose(nhg.querySelector("td[data-week='15']")!), /^vs SRO 1\.12 Easy rank 1$/);
    const sro = g.querySelector("tr[data-team=SRO]")!;
    assert.equal(prose(sro.querySelector("td[data-week='17']")!), "Bye");
    assert.match(prose(sro.querySelector("[data-testid=pp-total]")!), /\(2 games\)$/);
    assert.equal(all(g, "thead a[data-sort]").length, 5);
  });

  test("sorting by the total, easiest first, keeps the position", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const g = main(SORTED).querySelector("[data-testid=pp-grid]")!;
    assert.equal(g.getAttribute("data-pos"), "RB");
    const totals = all(g, "[data-testid=pp-total]").map((c) => Number(/^(\d+\.\d+)/.exec(prose(c))?.[1]));
    assert.deepEqual(totals, [...totals].sort((a, b) => b - a));
    assert.equal(g.querySelector("th[aria-sort=descending] a[data-sort]")?.getAttribute("data-sort"), "total");
    assert.equal(g.querySelector("a[aria-current=page][data-pos]")?.getAttribute("data-pos"), "RB");
  });
});

describe("/playoff-planner: unrated positions, how much it matters, the candidates, the live record", () => {
  test("an unrated position shows the schedule and says why, with no rating or total", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    const m = main("/playoff-planner?pos=TE");
    const g = m.querySelector("[data-testid=pp-grid]");
    if (!g) return t.skip("no grid published yet");
    if (seedOnly) assert.equal(g.getAttribute("data-rated"), "no");
    if (g.getAttribute("data-rated") !== "no") return;
    assert.match(prose(m.querySelector("[data-testid=pp-unrated]")!), /^For TE, using matchup ratings did not beat ignoring them in \d+ seasons of tests .*every matchup counts as 1\.00/);
    const bands = new Set(all(g, "td[data-week]").map((c) => c.getAttribute("data-band")));
    assert.ok([...bands].every((b) => b === "bye" || b === "unrated"), `rated cells at an unrated position: ${[...bands]}`);
    if (seedOnly) assert.deepEqual([...bands].sort(), ["bye", "unrated"]);
    assert.equal(all(g, "[data-testid=pp-total], thead a[data-sort]").length, 1, "only the team column sorts");
  });

  test("how much it matters: realized vs rated gaps, stability, the resting note", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    const m = main("/playoff-planner");
    assert.ok(prose(m.querySelector("[data-testid=pp-late]")!).endsWith(PLANNER_NOTE));
    if (!seedOnly) return;
    const rows = all(m, "[data-testid=pp-effects] tbody tr");
    assert.deepEqual(rows.map((r) => r.getAttribute("data-pos")), ["QB", "RB", "WR", "TE", "K", "DST"]);
    assert.equal(prose(m.querySelector("[data-testid=pp-effects] tr[data-pos=DST]")!), "D/ST 8.8 4.3 49% rated");
    assert.equal(prose(m.querySelector("[data-testid=pp-effects] tr[data-pos=TE]")!), "TE 6.6 0.5 7% not rated: every matchup 1.00");
    assert.equal(all(m, "[data-testid=pp-stability] tbody tr").length, 6);
    assert.match(prose(m.querySelector("[data-testid=pp-late] li")!), /^2013-2020 \(17 weeks\): .* week 16, then week 17: QB 68% to 62%/);
  });

  test("the candidates per position with the rule's pick; no live record before week 17", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    const m = main("/playoff-planner");
    if (!seedOnly) return;
    assert.equal(all(m, "[data-testid=pp-candidates-table] tbody tr").length, 24);
    assert.deepEqual(all(m, "[data-testid=pp-candidates-table] tr[data-pick=yes]").map((r) => `${r.getAttribute("data-pos")} ${r.getAttribute("data-candidate")}`), Object.entries(P.chosen).map(([p, c]) => `${p} ${c}`));
    assert.match(prose(m.querySelector("[data-testid=pp-candidates-table] tr[data-pos=RB][data-candidate=adjusted]")!), /beat shrunk in 5 of 13 seasons \(needs 7 and a lower MAE\): not used$/);
    const live = m.querySelector("[data-testid=pp-live-section] [data-live]")!;
    assert.equal(live.getAttribute("data-live"), "none");
    assert.match(prose(live), /^No live results yet\. The playoff weeks are graded after week 17/);
    assert.equal(prose(m.querySelector("#pp-live-heading")!), `${P.season} so far`);
  });

  test("the empty database: no grid yet, every table not published, no live results", (t) => {
    if (!emptyUp) return t.skip("no empty-database server (SMOKE_EMPTY_BASE_URL)");
    const m = main("/playoff-planner", empty);
    assert.match(prose(m.querySelector("[data-testid=pp-empty]")!), /^No playoff grid yet .* weeks 15-17/);
    assert.equal(m.querySelector("[data-testid=pp-grid]"), null);
    assert.ok(all(m, "[data-testid=not-published]").length >= 2);
    assert.equal(m.querySelector("[data-live]")?.getAttribute("data-live"), "none");
  });
});

describe("the player line, /methodology and /track-record", () => {
  test("a rated player's playoff weeks link to his position's grid; an unrated player has none", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const line = main(`/player/${SEED.featured}`).querySelector("[data-testid=pp-player-line]");
    assert.ok(line, "no playoff line on the featured running back's page");
    assert.match(prose(line), new RegExp(`^Playoff weeks \\(${P.featuredTeam}\\) · 15: vs SRO 1\\.12 easy · 16: vs EVP 0\\.99 neutral · 17: vs WLF 1\\.06 easy \\(matchups through week ${P.throughWeeks[1]}\\)$`));
    assert.equal(line.querySelector("a")?.getAttribute("href"), "/playoff-planner?pos=RB");
    assert.equal(main(`/player/${P.te}`).querySelector("[data-testid=pp-player-line]"), null);
  });

  test("/methodology explains the ratings, the rule and its picks", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    const m = main("/methodology");
    const s = m.querySelector("[data-testid=playoff-planner-method]");
    assert.ok(s, "no playoff-planner section");
    assert.ok(m.querySelector("a[href='#playoff-planner']"), "not in the contents");
    if (!seedOnly) return;
    assert.match(prose(s), /QB 33, RB 12, WR 52, TE 100, K 27, D\/ST 8 average games/);
    assert.match(prose(s), /It picked QB adjusted, RB shrunk, WR adjusted, TE none, K none, D\/ST adjusted \(used as picked;/);
  });

  test("/track-record: a backtest panel with the candidates and a live panel without tables", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    const s = main("/track-record").querySelector("[data-testid=track-playoff-planner]");
    assert.ok(s, "no playoff-planner section");
    const live = s.querySelector("[data-testid=panel-live]")!;
    assert.equal(all(live, "table").length, 0);
    if (seedOnly) assert.equal(all(s, "[data-testid=panel-backtest] [data-testid=pp-candidates-table] tbody tr").length, 24);
    if (emptyUp) assert.ok(all(main("/track-record", empty).querySelector("[data-testid=track-playoff-planner]")!, "[data-testid=not-published]").length >= 1);
  });
});
