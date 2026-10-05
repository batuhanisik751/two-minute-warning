// Smoke tests of the Questionable list (feature #1): /questionable (this week's list, an older
// week, a week without a list, the empty seed), the player badge, and the /methodology and
// /track-record sections. Seed-only checks use tests/seed-questionable.ts; against a real
// publish only what holds for any data.
import assert from "node:assert/strict";
import { before, describe, test } from "node:test";
import { INACTIVES_NOTE, WHEN_LISTS_FILL } from "../../lib/questionable";
import { SEED } from "../seed";
import { QUESTIONABLE_SEED as Q } from "../seed-questionable";
import { BASE, DATA, EMPTY_BASE, fetchPage, prose, serverUp, text, type Page } from "./dom";

let up = false;
let emptyUp = false;
const pages = new Map<string, Page>();
const empty = new Map<string, Page>();
const seedOnly = DATA === "seed";
const PAST = `/questionable?season=${Q.pastWeek.season}&week=${Q.pastWeek.week}`;
const NONE = `/questionable?season=${Q.emptyWeek.season}&week=${Q.emptyWeek.week}`;
const PATHS = ["/questionable", "/methodology", "/track-record", ...(seedOnly ? [PAST, NONE, `/player/${Q.featured}`, "/player/00-9000001"] : [])];

before(
  async () => {
    up = await serverUp();
    if (!up) return;
    for (const p of PATHS) pages.set(p, await fetchPage(p));
    if (EMPTY_BASE && seedOnly) {
      emptyUp = await serverUp(EMPTY_BASE);
      if (emptyUp) for (const p of ["/questionable", "/methodology", "/track-record"]) empty.set(p, await fetchPage(p, EMPTY_BASE));
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
const pctOf = (s: string) => Number(/(\d{1,3})%/.exec(s)?.[1]);

describe("/questionable (any data)", () => {
  test("the page, its sections and the honest notes", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    const p = pages.get("/questionable")!;
    assert.equal(p.status, 200);
    const m = main("/questionable");
    assert.equal(prose(m.querySelector("h1")!), "Questionable");
    assert.equal(p.doc.querySelector("nav[aria-label=Main] a[aria-current=page]")?.getAttribute("href"), "/questionable");
    for (const id of ["q-week", "q-history-section", "q-calibration-section", "q-live-section"]) assert.ok(m.querySelector(`[data-testid=${id}]`), `section ${id}`);
    // a list or its empty state, and the tag is never the final word
    const list = m.querySelector("[data-testid=q-list]");
    assert.ok(list || m.querySelector("[data-testid=q-empty]"), "neither a list nor the empty state");
    assert.ok(prose(m).includes(INACTIVES_NOTE), "the inactives note");
    for (const r of all(m, "[data-testid=q-row]")) {
      assert.ok(r.querySelector("[data-cell=player] a[href^='/player/']"), "player link");
      assert.match(text(r.querySelector("[data-testid=q-chance]")!), /^\d{1,3}%$/);
    }
    assert.ok(m.querySelector("a[href='/methodology#questionable']"), "methodology link");
  });

  test("/methodology and /track-record have a Questionable section", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    assert.ok(main("/methodology").querySelector("#questionable"), "methodology section");
    assert.ok(main("/methodology").querySelector("a[href='#questionable']"), "on-this-page link");
    const s = main("/track-record").querySelector("[data-testid=track-questionable]");
    assert.ok(s, "track-record section");
    assert.equal(all(s, "[data-testid=panel-backtest]").length, 1);
    assert.equal(all(s, "[data-testid=panel-live]").length, 1);
  });
});

describe("/questionable (seed)", () => {
  test("this week's newest snapshot: by kickoff, then chance; the lines each player gets", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const m = main("/questionable");
    assert.equal(prose(m.querySelector("#q-week-heading")!), `This week, ${Q.week.season} week ${Q.week.week}`);
    assert.match(prose(m.querySelector("[data-testid=q-asof]")!), /^The newest list: as of Sat 3 Oct 2026, 12:00 UTC .* 12 tagged players/);
    const groups = all(m, "[data-testid=q-list] > section");
    assert.equal(groups.length, 3, "three kickoffs");
    const times = groups.map((g) => g.querySelector("time")!.getAttribute("dateTime"));
    assert.deepEqual(times, Q.kickoffs);
    for (const g of groups) {
      const chances = all(g, "[data-testid=q-chance]").map((c) => pctOf(text(c)));
      assert.deepEqual(chances, [...chances].sort((a, b) => b - a), "within a kickoff: chance, highest first");
    }
    const rows = all(m, "[data-testid=q-row]");
    assert.equal(rows.length, Q.ids.length);
    const row = (id: string) => rows.find((r) => r.querySelector(`a[href='/player/${id}']`))!;
    assert.equal(text(row(Q.featured).querySelector("[data-testid=q-chance]")!), "64%");
    assert.match(prose(row(Q.featured).querySelector("[data-testid=q-plays]")!), /^If he plays: usually 80% of his normal points \(healthy players with similar averages: 85%\)\. Dud rate 31% \(healthy: 28%\)\.$/);
    assert.match(prose(row(Q.doubtful)), /Doubtful/);
    assert.match(prose(row(Q.doubtful).querySelector("[data-testid=q-plays]")!), /too few past players like him/);
    assert.match(prose(row(Q.noGames)), /No game with a snap yet this season/);
    assert.ok(row(SEED.longName.gsisId), "the long name is listed (the layout check measures it)");
    assert.equal(Q.featured, SEED.featured, "the badge's player is the main seed's featured one");
    for (const term of ["questionable", "practice_status", "play_chance", "dud_rate"]) {
      assert.ok(m.querySelector(`a[href='/methodology#term-${term}']`), `term ${term}`);
    }
  });

  test("the history, the backtest score, the calibration and the live record", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const m = main("/questionable");
    const bodies = all(m, "[data-testid=q-history] tbody");
    assert.deepEqual(bodies.map((b) => b.getAttribute("data-status")), ["Questionable", "Doubtful", "Out"]);
    assert.deepEqual(bodies.map((b) => all(b, "tr").length), [5, 5, 5]);
    assert.match(prose(bodies[0].querySelector("tr")!), /^Questionable All 440 277 63%$/);
    assert.deepEqual(all(m, "[data-testid=q-backtest-table] tbody tr").map((r) => r.getAttribute("data-grouping")), ["missed_prev+position", "status"]);
    assert.match(prose(m.querySelector("[data-testid=q-backtest]")!), /beats the baseline on both scores/);
    assert.equal(all(m, "[data-testid=q-calibration-table] tbody tr").length, 5);
    assert.match(prose(m.querySelector("[data-testid=q-calibration]")!), /No player fell in 85%\+/);
    const live = m.querySelector("[data-testid=q-live]")!;
    assert.equal(live.getAttribute("data-live"), "graded");
    assert.match(prose(live), /^4 of the 5 graded players played \(80\.0%\), from 1 live week of lists; the chance they were given averaged 60\.0%\./);
    assert.deepEqual(all(live, "[data-cell=label]").map((c) => prose(c)), ["Played, Questionable", "Played, Doubtful"]);
    assert.match(prose(m.querySelector("#q-live-heading")!), /^2026 so far$/);
  });

  test("an older week shows its own newest snapshot; a week without one, the empty state", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const past = main(PAST);
    assert.equal(prose(past.querySelector("#q-week-heading")!), `The list, ${Q.pastWeek.season} week ${Q.pastWeek.week}`);
    assert.equal(all(past, "[data-testid=q-row]").length, 5);
    const none = main(NONE);
    const e = none.querySelector("[data-testid=q-empty]");
    assert.ok(e, "the empty state");
    assert.match(prose(e), new RegExp(`No tagged player listed for ${Q.emptyWeek.season} week ${Q.emptyWeek.week} yet`));
    assert.ok(prose(e).includes(WHEN_LISTS_FILL) && prose(e).includes(INACTIVES_NOTE), "when lists fill in, and the inactives note");
    assert.equal(all(none, "[data-testid=q-weeks] a").length, 2, "links to the two weeks with a list");
  });

  test("the player badge: on this week's newest list only", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const b = main(`/player/${Q.featured}`).querySelector("[data-testid=q-badge]");
    assert.ok(b, "badge");
    assert.match(prose(b), /^Questionable: plays 64% of the time \(2026 week 4 list\)$/);
    assert.equal(b.querySelector("a")?.getAttribute("href"), `/questionable?season=${Q.week.season}&week=${Q.week.week}`);
    assert.equal(main("/player/00-9000001").querySelector("[data-testid=q-badge]"), null, "no badge for a player not listed");
  });

  test("the empty seed: no week, no list, the notes; the other sections say not published", (t) => {
    if (!emptyUp) return t.skip("no empty server");
    const m = main("/questionable", empty);
    const e = m.querySelector("[data-testid=q-empty]");
    assert.ok(e, "the empty state");
    assert.match(prose(e), /^No week is being played right now/);
    assert.ok(prose(e).includes(WHEN_LISTS_FILL));
    assert.match(prose(m.querySelector("[data-testid=q-history-section] [data-testid=not-published]")!), /^How often tagged players played: not published yet\./);
    assert.match(prose(m.querySelector("[data-testid=q-live-empty]")!), /^No live results yet\./);
    assert.equal(all(m, "[data-testid=not-published]").length, 3, "history, backtest and calibration: not published yet");
    assert.ok(main("/methodology", empty).querySelector("[data-testid=questionable-method]"));
  });
});
