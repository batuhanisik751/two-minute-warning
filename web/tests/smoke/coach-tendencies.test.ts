// Smoke tests of coach tendencies (feature #10): the coach page's "How his offense plays" (the
// seasons table that folds, the season in progress, a season not charted, pace as "faster than",
// the career line, the persistence note from the published table), a coach without rows, the
// /decisions league table (current coaches, default sort, a sort link, the fantasy link) and the
// /methodology section; the empty seed. Seed-only checks use tests/seed-coach-tendencies.ts;
// against a real publish only what holds for any data.
import assert from "node:assert/strict";
import { before, describe, test } from "node:test";
import { TENDENCY_METRICS } from "../../lib/coach-tendencies";
import { TENDENCY_SEED as T } from "../seed-coach-tendencies";
import { DECISIONS_SEED as D } from "../seed-decisions";
import { HOT_SEAT_SEED as H } from "../seed-hot-seat";
import { BASE, DATA, EMPTY_BASE, fetchPage, prose, serverUp, type Page } from "./dom";

let up = false;
let emptyUp = false;
const pages = new Map<string, Page>();
const empty = new Map<string, Page>();
const seedOnly = DATA === "seed";
const VET = `/coach/${T.veteran}`;
const UNRANKED = `/coach/${T.unranked}`;
const NONE = `/coach/${H.firedAfter}`;
const PACE = "/decisions?tsort=neutral_sec_per_play";
const PATHS = ["/decisions", "/methodology", ...(seedOnly ? [VET, UNRANKED, NONE, PACE] : [])];

before(
  async () => {
    up = await serverUp();
    if (!up) return;
    for (const p of PATHS) pages.set(p, await fetchPage(p));
    if (!seedOnly) {
      // a real publish: the first coach the league table links
      const href = pages.get("/decisions")!.doc.querySelector("[data-testid=tendency-league] a[href^='/coach/']")?.getAttribute("href");
      if (href) pages.set("real-coach", await fetchPage(href));
    }
    if (EMPTY_BASE && seedOnly) {
      emptyUp = await serverUp(EMPTY_BASE);
      if (emptyUp) for (const p of ["/decisions", "/methodology"]) empty.set(p, await fetchPage(p, EMPTY_BASE));
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
const rowsOf = (m: Element, testId: string) => all(m, `[data-testid=${testId}] tbody tr[data-row]`);

describe("/coach/[id]: how his offense plays", () => {
  test("every season newest first, folded after 10, the season in progress and the old seasons", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const m = main(VET);
    const s = m.querySelector("[data-testid=coach-tendencies]")!;
    assert.equal(prose(s.querySelector("h2")!), "How his offense plays");
    const rows = rowsOf(m, "tendency-seasons");
    assert.equal(rows.length, T.season - T.firstSeason + 1);
    assert.equal(all(m, "[data-testid=tendency-seasons] tbody[data-fold-rest] tr").length, rows.length - 10, "folds after 10");
    assert.equal(rows[0].getAttribute("data-season"), String(T.season));
    assert.match(prose(rows[0].querySelector("[data-testid=tendency-partial]")!), new RegExp(`^through week ${T.throughWeek}: \\d+ snaps so far$`));
    const oldest = rows[rows.length - 1];
    assert.equal(oldest.getAttribute("data-season"), String(T.firstSeason));
    for (const c of ["proe", "no_huddle_rate"]) assert.match(prose(oldest.querySelector(`[data-cell=${c}]`)!), /not charted/);
    for (const r of rows) assert.equal(all(r, "td[data-cell]").length, TENDENCY_METRICS.length);
    assert.match(prose(rows[1].querySelector("[data-cell=neutral_sec_per_play]")!), /^\d+\.\d s faster than \d+%$/);
    assert.match(prose(rows[1].querySelector("[data-cell=proe]")!), /^[+-]?\d+\.\d pts \d+(st|nd|rd|th) percentile$/);
    const head = m.querySelector("[data-testid=tendency-seasons] thead")!;
    for (const x of TENDENCY_METRICS) assert.ok(head.querySelector(`a[href='/methodology#term-${x}']`), `header term ${x}`);
    assert.equal(all(head, "[role=button][aria-expanded=false]").length, TENDENCY_METRICS.length, "every metric header is a term");
  });

  test("the career line and the persistence note from the published table", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const m = main(VET);
    const career = m.querySelector("[data-testid=tendency-career]")!;
    assert.match(prose(career.querySelector("h3")!), new RegExp(`^Career, ${T.firstSeason}–${T.season - 1} \\(NHG\\)$`));
    assert.equal(all(career, "tbody tr[data-row]").length, TENDENCY_METRICS.length);
    const note = prose(m.querySelector("[data-testid=tendency-persistence-note]")!);
    assert.match(note, /Style persists when the coach and the team both stay/);
    assert.match(note, /At a new team, only no-huddle rate \(r 0\.53\) and shotgun rate \(r 0\.46\) carry over/);
  });

  test("a season too short to rank, and a coach without a play-by-play season", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const first = rowsOf(main(UNRANKED), "tendency-seasons")[0];
    for (const c of all(first, "td[data-cell]")) assert.match(prose(c), /not ranked$/);
    const none = main(NONE);
    assert.ok(none.querySelector("[data-testid=no-tendencies]"));
    assert.equal(none.querySelector("[data-testid=tendency-persistence-note]"), null);
  });

  test("a real publish: the first coach of the league table has seasons", (t) => {
    if (!up || seedOnly) return t.skip("real data only");
    if (!pages.has("real-coach")) return t.skip("no league table published");
    assert.ok(rowsOf(main("real-coach"), "tendency-seasons").length > 0);
  });
});

describe("/decisions: how each offense plays", () => {
  test("the current head coach of every team, sorted by PROE, with the fantasy link", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const m = main("/decisions");
    const s = m.querySelector("[data-testid=league-tendencies]")!;
    assert.equal(prose(s.querySelector("h2")!), `How each offense plays, ${T.season} (through week ${T.throughWeek})`);
    const rows = rowsOf(m, "tendency-league");
    // one row per team (the 2025 interim coach is not a 2026 coach), most PROE first
    assert.deepEqual(rows.map((r) => r.getAttribute("data-team")), ["WLF", "EVP", "SRO", "NHG"]);
    assert.equal(rows[3].querySelector("a")?.getAttribute("href"), `/coach/${D.coaches[0].id}`);
    assert.equal(s.querySelector("th[aria-sort] a[data-sort]")?.getAttribute("data-sort"), "proe");
    const head = s.querySelector("thead")!;
    for (const x of TENDENCY_METRICS) assert.ok(head.querySelector(`a[href='/methodology#term-${x}']`), `header term ${x}`);
    const link = prose(s.querySelector("[data-testid=tendency-fantasy-link]")!);
    assert.match(link, new RegExp(`r = ${T.proe.same.toFixed(2)} .*over 410 team-seasons`));
    assert.match(link, new RegExp(`\\+${T.proe.targets.toFixed(1)} targets per game and \\+${T.proe.ppr.toFixed(1)} receiving PPR points`));
    assert.match(link, new RegExp(`next season .*r = ${T.proe.next.toFixed(2)}`));
  });

  test("a sort link: pace, fastest first, the page's anchor kept", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const m = main(PACE);
    assert.deepEqual(rowsOf(m, "tendency-league").map((r) => r.getAttribute("data-team")), ["NHG", "SRO", "EVP", "WLF"]);
    const active = m.querySelector("[data-testid=tendency-league] th[aria-sort]")!;
    assert.equal(active.getAttribute("aria-sort"), "ascending");
    const href = m.querySelector("[data-testid=tendency-league] a[data-sort=shotgun_rate]")!.getAttribute("href");
    assert.equal(href, "/decisions?tsort=shotgun_rate#tendencies");
  });

  test("any data: each row has every metric", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    const rows = rowsOf(main("/decisions"), "tendency-league");
    if (!seedOnly && !rows.length) return t.skip("no league table published");
    assert.ok(rows.length > 0);
    for (const r of rows) assert.equal(all(r, "td[data-cell]").length, TENDENCY_METRICS.length);
  });
});

describe("/methodology: coach tendencies", () => {
  test("the section, its contents link, the persistence and fantasy-link tables", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    const m = main("/methodology");
    const s = m.querySelector("[data-testid=coach-tendencies-method]");
    assert.ok(s, "the section");
    assert.ok(m.querySelector("a[href='#coach-tendencies']"), "in the contents");
    if (!seedOnly && s.querySelector("[data-testid=empty-state]")) return;
    assert.equal(rowsOf(s, "tendency-persistence").length, TENDENCY_METRICS.length);
    assert.equal(rowsOf(s, "tendency-link").length, TENDENCY_METRICS.length);
    if (!seedOnly) return;
    assert.equal(prose(s.querySelector("[data-metric=fourth_short_go_rate] [data-cell=same_coach_new_team]")!), "– 0 pairs");
    assert.match(prose(s.querySelector("[data-testid=tendency-persistence-text]")!), /only no-huddle rate \(r 0\.53\) and shotgun rate \(r 0\.46\) carry over/);
    assert.match(prose(s.querySelector("[data-testid=tendency-link-text]")!), new RegExp(`r = ${T.proe.same.toFixed(2)}`));
  });

  test("the empty seed: no league table, the section says nothing is published", (t) => {
    if (!emptyUp) return t.skip("no empty server (seed runs only)");
    assert.equal(main("/decisions", empty).querySelector("[data-testid=league-tendencies]"), null);
    const s = main("/methodology", empty).querySelector("[data-testid=coach-tendencies-method]")!;
    assert.match(prose(s), /No coach tendencies published yet/);
  });
});
