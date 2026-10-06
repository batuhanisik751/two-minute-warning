// Smoke tests of /track-record (step H6-c): one section per module, the walk-forward backtest and
// the live results in separate panels, seasons tables that fold, calibration plots only where
// bins are published, honest empty states. Seed-only checks use the fictional seeds.
import assert from "node:assert/strict";
import { before, describe, test } from "node:test";
import { SEED } from "../seed";
import { LEAD_TIME_SEED as LT } from "../seed-lead-time";
import { BASE, DATA, EMPTY_BASE, fetchPage, prose, serverUp, type Page } from "./dom";
import { foldCheck } from "./fold";

const PATH = "/track-record";
const MODULES = ["radar", "streamer", "regression", "decisions", "hot-seat", "board", "questionable", "teammate-out", "playoff-planner", "lead-time"];
let up = false;
let emptyUp = false;
let page: Page;
let empty: Page | null = null;
const seedOnly = DATA === "seed";

before(
  async () => {
    up = await serverUp();
    if (!up) return;
    page = await fetchPage(PATH);
    if (EMPTY_BASE && seedOnly) {
      emptyUp = await serverUp(EMPTY_BASE);
      if (emptyUp) empty = await fetchPage(PATH, EMPTY_BASE);
    }
  },
  { timeout: 120_000 },
);

const main = (p: Page = page) => p.doc.querySelector("main#main")!;
const sec = (id: string, p: Page = page) => {
  const s = main(p).querySelector(`[data-testid=track-${id}]`);
  assert.ok(s, `no ${id} section`);
  return s;
};
const q = (root: Element, sel: string) => Array.from(root.querySelectorAll(sel));

describe("/track-record (any data)", () => {
  test("one section per module, each with a backtest panel and a separate live panel", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    assert.equal(page.status, 200);
    assert.equal(prose(main().querySelector("h1")!), "Track record");
    assert.deepEqual(q(main(), "section[data-testid^=track-] > h2").map((h) => h.id), MODULES);
    for (const id of MODULES) {
      const s = sec(id);
      assert.ok(q(s, "[data-testid=panel-backtest]").length >= 1, `${id}: no backtest panel`);
      assert.ok(q(s, "[data-testid=panel-live]").length >= 1, `${id}: no live panel`);
      for (const live of q(s, "[data-testid=panel-live]")) {
        assert.equal(live.querySelector(".live-pill") !== null, true, `${id}: the live panel is not labelled live`);
        assert.equal(q(live, "[data-testid=calibration], table").length, 0, `${id}: backtest numbers inside the live panel`);
      }
    }
    // the intro links the definitions; the nav marks the page; /methodology links here
    for (const term of ["walk_forward", "list_kind", "interval", "calibration"]) {
      assert.ok(main().querySelector(`[data-testid=track-intro] a[href='/methodology#term-${term}']`), `the intro does not explain ${term}`);
    }
    assert.equal(page.doc.querySelector("nav[aria-label=Main] a[aria-current=page]")?.getAttribute("href"), PATH);
    assert.ok(!/legit/i.test(page.html), "the dropped tag's name is in the page");
  });

  test("Regression Watch: no calibration (it gives no probability) and no per-season test results: said so", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    const s = sec("regression");
    assert.equal(q(s, "[data-testid=calibration]").length, 0);
    const notes = q(s, "[data-testid=not-published]").map((n) => prose(n));
    assert.ok(notes.some((n) => /^A calibration plot: not published yet\./.test(n)), notes.join(" | "));
    if (q(s, "[data-testid=rw-track]").length) assert.ok(notes.some((n) => /^Each season's own test results: not published yet\./.test(n)));
  });

  test("every live block says what it counts, or 'No live results yet'", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    for (const b of q(main(), "[data-live]")) {
      const kind = b.getAttribute("data-live");
      if (kind === "graded") assert.match(prose(b), /^\d[\d,]* of the \d[\d,]* graded .+ \(\d+\.\d%\), from \d+ live/);
      else assert.match(prose(b), /^No live results yet\./);
    }
    const r = foldCheck(page.doc);
    assert.deepEqual(r.problems, []);
  });
});

describe("/waivers links the lead-time study", () => {
  test("the Radar's page asks 'Does the Radar beat the crowd?' and links the section", async (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    const w = await fetchPage("/waivers");
    const a = w.doc.querySelector("main a[href='/track-record#lead-time']");
    assert.ok(a, "no link to /track-record#lead-time on /waivers");
    assert.equal(prose(a), "Does the Radar beat the crowd?");
    assert.ok(page.doc.querySelector("#lead-time"), "the anchor is missing on /track-record");
  });
});

describe("/track-record (seed)", () => {
  test("Radar: the headline from the seeded pooled rows, twelve seasons folded after ten, the calibration plot", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const s = sec("radar");
    assert.deepEqual(q(s, "[data-testid=radar-headline] [data-cell=value]").map(prose), ["50.0%", "40.0%", "+10.0 points"]);
    assert.match(prose(s.querySelector("[data-testid=radar-headline]")!), /95% interval 48\.0–52\.0%.*100 weekly lists/);
    const f = foldCheck(page.doc).folds.find((x) => x.what === "radar-seasons");
    assert.deepEqual(f && [f.head, f.rest, f.total], [10, 2, 12]);
    assert.match(prose(s.querySelector("[data-testid=radar-seasons] tbody tr")!), /^2025 55\.0% 43\.5% 10\.0% 60 330 of 600$/);
    assert.equal(q(s, "[data-testid=calibration] [data-testid=calibration-table] tbody tr").length, 2);
  });

  test("Streamer: K against its best rule (seasons fold, calibration at the groups' middles); D/ST has no calibration", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const k = main().querySelector("[data-testid=track-stream-K]")!;
    assert.deepEqual(q(k, "[data-testid=stream-headline-K] [data-cell=value]").map(prose), ["40.0%", "37.0%", "+3.0 points"]);
    assert.equal(q(k, "[data-testid=stream-seasons-K] tbody:not([data-fold-rest]) tr").length, 10);
    assert.match(prose(k.querySelector("[data-testid=stream-seasons-K] tbody tr")!), /^2025 50\.0% 36\.0% 26\.7% 16 150$/);
    assert.ok(k.querySelector("[data-testid=calibration-mid]"), "the K plot does not say its marks sit at the middles");
    const d = main().querySelector("[data-testid=track-stream-DST]")!;
    assert.equal(q(d, "[data-testid=stream-seasons-DST] tbody tr").length, 2);
    assert.equal(q(d, "[data-testid=calibration]").length, 0);
    assert.ok(q(d, "[data-testid=not-published]").some((n) => /^The D\/ST calibration: not published yet\./.test(prose(n))));
  });

  test("Regression Watch: the reused track record and what each test season used", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const s = sec("regression");
    assert.equal(q(s, "[data-testid=rw-track]").length, 1);
    assert.deepEqual(q(s, "[data-testid=rw-choices] tbody tr").map((r) => prose(r.querySelector("th")!)), ["2025", "2024"]);
    assert.equal(prose(s.querySelector("[data-testid=rw-live-lists]")!), "1 live weekly list so far: 2026 week 3.");
  });

  test("Decision Report Card: our WP model against nflfastR, ten seasons, the reliability plot; no live table", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const s = sec("decisions");
    assert.deepEqual(q(s, "[data-testid=decisions-headline] [data-cell=value]").map(prose), ["0.4520", "-0.0290", "+0.0050"]);
    assert.equal(q(s, "[data-testid=wp-seasons] tbody tr").length, 10);
    assert.equal(q(s, "[data-testid=calibration-table] tbody tr").length, 3);
    assert.match(prose(s.querySelector("[data-testid=decisions-live]")!), /^No live results yet\. .*not published yet\. This season's grades \(2026, through week \d+\) are on the Report Card\.$/);
  });

  test("live: the seeded live week is pending everywhere, counted, never a miss", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const n = SEED.positions.length;
    assert.match(prose(main().querySelector("[data-testid=radar-live]")!), new RegExp(`^No live results yet\\. ${n} live lists so far \\(${n * SEED.sizes.live} top-10 picks\\); every outcome is still pending`));
    for (const id of ["radar-live", "stream-live-K", "stream-live-DST", "rw-live-sell_high", "rw-live-buy_low"]) {
      assert.equal(main().querySelector(`[data-testid=${id}]`)?.getAttribute("data-live"), "pending", id);
    }
  });

  test("Does the Radar beat the crowd?: the seeded aggregates, the histogram, the 25% result, the reverse view, the source", (t) => {
    if (!up || !seedOnly) return t.skip("seed only");
    const s = sec("lead-time");
    assert.equal(prose(s.querySelector("h2")!), "Does the Radar beat the crowd?");
    assert.deepEqual(q(s, "[data-testid=lt-headline] [data-cell=value]").map(prose), LT.headline);
    assert.match(prose(s.querySelector("[data-testid=lt-headline]")!), new RegExp(`median 3 weeks ahead; ${LT.adds[50]} adds, ${LT.span}`));
    assert.match(prose(s.querySelector("[data-testid=lt-summary] caption")!), new RegExp(`complete seasons ${LT.span} pooled \\(${LT.adds[50]} crowd adds\\)`));
    assert.deepEqual(q(s, "[data-testid=lt-summary] tbody tr").map(prose), [
      "Listed (top 25) 60.0% 5.0% 15.0% 20.0% 3 (1 to 6)",
      "Must-add or speculative 55.0% 5.0% 15.0% 25.0% 3 (0 to 6)",
      "Must-add 20.0% 10.0% 20.0% 50.0% 0 (-2 to 2)",
      "Momentum (+10 points in a week) 70.0% 25.0% 0.0% 5.0% 1 (0 to 2)",
    ]);
    assert.equal(q(s, "[data-testid=lt-hist-table] tbody tr").length, 18);
    assert.match(prose(s.querySelector("[data-testid=lt-hist-table] tbody tr:last-child")!), /^never flagged 20 5$/);
    assert.match(prose(s.querySelector("[data-testid=lt-t25]")!), new RegExp(`${LT.adds[25]} crowd adds.*flagged ${LT.t25[0]} before the crowd and the momentum baseline ${LT.t25[1]}`));
    const [both, earlier, radar, mom, neither] = LT.h2hListed;
    assert.equal(prose(s.querySelector("[data-testid=lt-h2h] tbody tr[data-level=listed]")!), `Listed (top 25) ${both} Radar earlier in ${earlier} ${radar} ${mom} ${neither}`);
    assert.match(prose(s.querySelector("[data-testid=lt-ignored]")!), new RegExp(`never added ${LT.ignored.n}: they were hits only ${LT.ignored.hit} .* against ${LT.ignored.laterHit}`));
    assert.match(prose(s.querySelector("[data-testid=lt-coverage]")!), /complete seasons 2021–2023\. 2020 is partial: .* after week 5.* 2024 is left out: the warehouse has no in-season/);
    assert.match(prose(s.querySelector("[data-testid=lt-source]")!), /^Source: ESPN's rostered percentages as scraped by FantasyPros\. .*no player and no player's percentage\.$/);
    assert.equal(s.querySelector("[data-testid=lt-live]")?.getAttribute("data-live"), "none");
    assert.ok(!/\b00-\d{7}\b/.test(s.innerHTML), "a player id in the lead-time section");
  });

  test("empty database: every section says not published yet, and no live results yet", (t) => {
    if (!emptyUp || !empty) return t.skip("no empty-database server (SMOKE_EMPTY_BASE_URL)");
    assert.equal(empty.status, 200);
    for (const id of MODULES) {
      assert.ok(q(sec(id, empty), "[data-testid=not-published]").length >= 1, `${id}: no not-published note`);
      assert.match(prose(sec(id, empty).querySelector("[data-live]")!), /^No live results yet\./, id);
    }
    assert.equal(q(main(empty), "[data-testid=calibration], [data-row]").length, 0);
    assert.ok(!/\b20\d\d\b/.test(prose(main(empty))), `a year on the empty page: ${prose(main(empty)).slice(0, 200)}`);
  });
});
