// Smoke tests of the Decision Report Card (step W4): /decisions, /coach/[id], the home page's
// coach-of-the-week card and the /methodology section. Seed-only checks use
// tests/seed-decisions.ts; against a real publish only what holds for any data.
import assert from "node:assert/strict";
import { before, describe, test } from "node:test";
import { wpPoints } from "../../lib/decisions";
import { CLOCK, TOSS_UP_MARGIN } from "../../lib/method";
import { DECISIONS_SEED as D } from "../seed-decisions";
import { BASE, DATA, EMPTY_BASE, fetchPage, prose, serverUp, text, type Page } from "./dom";
import { foldCheck } from "./fold";
import { routeSet, type RouteSet } from "./routes";

let up = false;
let emptyUp = false;
let set: RouteSet;
const pages = new Map<string, Page>();
const empty = new Map<string, Page>();
const seedOnly = DATA === "seed";

before(
  async () => {
    up = await serverUp();
    if (!up) return;
    set = await routeSet();
    for (const p of [...set.routes.filter((r) => ["home", "decisions", "coach", "methodology"].includes(r.kind)).map((r) => r.path)]) {
      if (!pages.has(p)) pages.set(p, await fetchPage(p));
    }
    if (EMPTY_BASE && seedOnly) {
      emptyUp = await serverUp(EMPTY_BASE);
      if (emptyUp) for (const p of ["/", "/decisions", "/methodology"]) empty.set(p, await fetchPage(p, EMPTY_BASE));
    }
  },
  { timeout: 300_000 },
);

const main = (path: string, from = pages): Element => {
  const p = from.get(path);
  assert.ok(p, `not fetched: ${path}`);
  const m = p.doc.querySelector("main#main");
  assert.ok(m, `${path}: no <main id=main>`);
  return m;
};
const all = (m: Element, sel: string) => Array.from(m.querySelectorAll(sel));

/** "4th and 2 at the opponent's 38, down 3, 6:12 left in Q4." or a try after a touchdown. */
const SITUATION =
  /^(?:\d+(?:st|nd|rd|th) and (?:goal|\d+) at (?:the opponent's \d+|midfield|its own \d+)|Try after a touchdown), (?:up \d+|down \d+|tied)(?: before the try)?, \d+:\d\d left in (?:Q[1-4]|overtime)\.$/;

function checkDecisionRows(path: string, m: Element, coachLinks: boolean) {
  for (const row of all(m, "[data-testid=decision]")) {
    const sit = text(row.querySelector("[data-testid=situation]")!);
    assert.match(sit, SITUATION, `${path}: ${sit}`);
    const opts = all(row, "ul[aria-label='Win probability of each option'] > li");
    assert.ok(opts.length >= 1, `${path}: a call without its options`);
    assert.equal(opts.filter((o) => /· chosen$/.test(text(o))).length, 1, `${path}: exactly one chosen option`);
    for (const o of opts) assert.match(text(o), /\d+\.\d%/, path);
    assert.match(text(row.querySelector("[data-cell=value]")!), /^\+?\d+\.\d WP points (lost|gained)$/, path);
    assert.equal(!!row.querySelector("a[href^='/coach/']"), coachLinks, `${path}: coach link ${coachLinks ? "missing" : "on the coach's own page"}`);
  }
}

describe("/decisions", () => {
  test("every season page: picker, summary, how it works, leaderboard, toss-ups, calls in words, clock cases", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    const routes = set.routes.filter((r) => r.kind === "decisions");
    if (!routes.length) return t.skip("no Decision Report Card in this database");
    for (const r of routes) {
      const m = main(r.path);
      assert.equal(m.querySelector("form[action='/decisions']")?.getAttribute("method"), "get", `${r.path}: season picker`);
      assert.ok(m.querySelector("[data-testid=season-summary]"), `${r.path}: season summary`);
      const how = text(m.querySelector("[data-testid=how-it-works]")!);
      assert.ok(how.includes(`more than ${wpPoints(TOSS_UP_MARGIN)} WP points`), `${r.path}: the clear margin`);
      assert.ok(m.querySelector("[data-testid=how-it-works] a[href='/methodology#decisions']"), `${r.path}: link to the methodology`);
      assert.ok(m.querySelector("[data-testid=leaderboard] tbody tr") || /No coach has enough games yet/.test(text(m)), `${r.path}: leaderboard`);
      assert.match(text(m.querySelector("[data-testid=toss-ups]")!), /were toss-ups/, r.path);
      checkDecisionRows(r.path, m, true);
      assert.ok(m.querySelector("[data-testid=clock-cases]") || m.querySelector("[data-testid=no-clock-cases]"), `${r.path}: clock cases`);
    }
  });

  test("coach pages: the name, seasons table, chart with its data table, calls without self-links", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    const routes = set.routes.filter((r) => r.kind === "coach");
    if (!routes.length) return t.skip("no coach page in this database");
    for (const r of routes) {
      const m = main(r.path);
      assert.ok(text(m.querySelector("h1")!).length > 0, r.path);
      assert.ok(m.querySelector("[data-testid=coach-seasons] tbody tr"), `${r.path}: seasons table`);
      const fig = m.querySelector("[data-testid=chart]");
      assert.ok(fig?.querySelector("details table tbody tr"), `${r.path}: a chart without its data table`);
      checkDecisionRows(r.path, m, false);
    }
  });
});

describe("seed: the Decision Report Card", () => {
  test("/decisions (the season being graded): the leaderboard in order, a call per week, the season in words", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const m = main("/decisions");
    assert.match(text(m.querySelector("[data-testid=season-summary]")!), new RegExp(`The league, ${D.season} \\(through week ${D.latestWeek}\\)`));
    const lb = all(m, "[data-testid=leaderboard] tbody tr");
    assert.equal(lb.length, D.coaches.length);
    const per = lb.map((r) => Number(text(r.querySelector("[data-cell-kind=wp-lost-per-game]")!)));
    assert.deepEqual(per, [...per].sort((a, b) => a - b), "least WP lost per game first");
    assert.equal(all(m, "[data-testid=worst-calls] [data-testid=decision]").length, 3);
    assert.equal(all(m, "[data-testid=best-calls] [data-testid=decision]").length, 3);
    assert.ok(m.querySelector("[data-testid=no-clock-cases]"));
  });

  test("/decisions?season=2025: the long worst-calls list folds, the interim coach is listed apart, clock cases in words", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const path = `/decisions?season=${D.past}`;
    const m = main(path);
    assert.equal(all(m, "[data-testid=leaderboard] tbody tr").length, D.coaches.length);
    assert.match(text(m.querySelector("[data-testid=fewer-games]")!), new RegExp(`Fewer than 8 games, not ranked: ${D.interim.name} \\(${D.interim.team}, 3\\)`));
    const folds = foldCheck(pages.get(path)!.doc).folds.filter((f) => f.what === `Worst calls, ${D.past}`);
    assert.deepEqual(folds.map((f) => [f.head, f.rest, f.total]), [[10, 3, 13]]);
    // the worst call: the seed's twelfth wrong fourth down (WP lost 0.065)
    const first = m.querySelector("[data-testid=worst-calls] [data-testid=decision]")!;
    assert.equal(text(first.querySelector("[data-testid=situation]")!), "4th and 6 at the opponent's 49, down 2, 10:20 left in Q4.");
    assert.match(text(first), new RegExp(`${D.coaches[3].name} · WLF against EVP, ${D.past} week 12`));
    assert.match(text(first), /Chose: Punt · best: Go for it · what happened: punted/);
    assert.equal(text(first.querySelector("[data-cell=value]")!), "6.5 WP points lost");
    assert.equal(all(m, "[data-testid=decision][data-kind=two_point]").length, 2, "a wrong try and a try that went for two");
    const cases = all(m, "[data-testid=clock-case]").map((c) => text(c.querySelector("[data-cell=amount]")!));
    assert.deepEqual(cases.sort(), ["1.6 expected points left 3.1 WP points left", "2 timeouts unused", "31 seconds wasted"]);
    assert.match(text(m), /Opponent's ball: 1st and 10 at its own 30, down 5, 1:35 left in Q4, 2 timeouts in hand\./);
  });

  test("the toss-ups and the season's sums agree with the seeded rows", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const m = main(`/decisions?season=${D.past}`);
    // 2025: 12 wrong + 3 go + 3 punts (interim's included) clear fourth downs, 4 toss-ups; tries: 2 clear, 2 toss-ups
    assert.match(prose(m.querySelector("[data-testid=toss-ups]")!), /4 of 22 fourth downs \(18%\) and 2 of 4 tries after a touchdown \(50%\) were toss-ups/);
    assert.match(prose(m.querySelector("[data-testid=season-summary]")!), /5 head coaches, 71 team-games\. 26 decisions priced: 20 clear calls, of which 13 were wrong \(65%\), and 6 toss-ups\./);
  });

  test("a coach page: seasons newest first with the graded week, the chart's table, every season's calls", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const m = main(`/coach/${D.featured}`);
    assert.equal(text(m.querySelector("h1")!), D.coaches[0].name);
    const seasons = all(m, "[data-testid=coach-seasons] tbody tr").map((r) => text(r.querySelector("th")!));
    assert.deepEqual(seasons, [`${D.season} through week ${D.latestWeek}`, String(D.past), "2024"]);
    assert.equal(all(m, "[data-testid=chart] details tbody tr").length, 3);
    const worst = all(m, "[data-testid=worst-calls] [data-testid=decision]").map((r) => text(r));
    assert.ok(worst.some((w) => /, 2024 week 5/.test(w)) && worst.some((w) => new RegExp(`, ${D.past} week`).test(w)), "calls of every season");
    assert.ok(m.querySelector("[data-testid=no-clock-cases]"));
    const avery = main("/coach/avery-o-hollis");
    assert.equal(all(avery, "[data-testid=clock-case]").length, 2);
    assert.equal(avery.querySelectorAll("[data-testid=clock-case] a[href^='/coach/']").length, 0);
  });

  test("home: the coach-of-the-week card shows the newest graded week's best and worst call", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const card = main("/").querySelector("[data-testid=coach-week-card]");
    assert.ok(card, "no coach-of-the-week card");
    assert.match(text(card.querySelector("h2")!), new RegExp(`^Coach of the week, ${D.season} week ${D.latestWeek}$`));
    const best = card.querySelector("[data-testid=week-best] [data-testid=decision]")!;
    const worst = card.querySelector("[data-testid=week-worst] [data-testid=decision]")!;
    assert.match(text(best), /^Avery O'Hollis · /);
    assert.match(text(best.querySelector("[data-cell=value]")!), /^\+\d+\.\d WP points gained$/);
    assert.match(text(worst), /^Quinn Fairway · /);
    assert.equal(card.querySelectorAll("[data-cell=rank]").length, 0, "one call each: no rank");
  });

  test("methodology: the WP model against nflfastR, smoothness, sub-models, nfl4th, clock definitions, honesty notes", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const m = main("/methodology");
    for (const id of ["decisions", "decisions-wp", "decisions-submodels", "decisions-grading", "decisions-nfl4th", "decisions-clock", "decisions-honesty"]) {
      assert.ok(m.querySelector(`#${id}`), `section #${id}`);
    }
    assert.ok(m.querySelector("nav[aria-label='On this page'] a[href='#decisions']"));
    assert.equal(all(m, "[data-testid=wp-table] tbody tr").length, 5);
    assert.match(text(m.querySelector("[data-testid=wp-table]")!), /Our model 0\.1510 0\.1490 to 0\.1530 0\.4520/);
    assert.equal(all(m, "[data-testid=smoothness-table] tbody tr").length, 5);
    assert.match(text(m.querySelector("[data-testid=smoothness-table]")!), /Largest change for one point of score, first half 6\.0 3\.4 18\.0 3\.7/);
    const subs = all(m, "[data-testid=submodels] > li");
    assert.equal(subs.length, 5);
    assert.match(text(subs[0]), /better than the baseline\.$/);
    assert.match(text(m.querySelector("[data-submodel=two_point]")!), /no clear difference from the simple rate\.$/);
    assert.match(prose(m.querySelector("[data-testid=nfl4th]")!), /on 81% of 700; on our clear calls on 93% of 300, and on our toss-ups on 70% of 400/);
    assert.match(prose(m.querySelector("[data-testid=clock-definitions]")!), new RegExp(`lost in regulation by 1 to ${CLOCK.oneScoreMargin} points`));
    const notes = all(m, "[data-testid=decisions-honesty] > li");
    assert.equal(notes.length, 4);
    assert.match(prose(notes[1]), new RegExp(`validation seasons 2014–2015 only.*${D.season} is their first clean test`));
    for (const term of ["clear_call", "toss_up", "against_convention", "clock_case", "wp_points"]) assert.ok(m.querySelector(`#term-${term}`), `glossary: ${term}`);
  });
});

describe("the Decision Report Card before its first publish", () => {
  test("no season: /decisions says so, the home card is hidden, the methodology section says so", (t) => {
    if (!emptyUp) return t.skip("no empty-database server (SMOKE_EMPTY_BASE_URL)");
    assert.equal(empty.get("/decisions")!.status, 200);
    assert.match(text(main("/decisions", empty)), /No decisions published yet/);
    assert.equal(main("/", empty).querySelectorAll("[data-testid=coach-week-card]").length, 0);
    assert.match(text(main("/methodology", empty).querySelector("[data-testid=decisions-method]")!), /No Decision Report Card published yet/);
  });
});
