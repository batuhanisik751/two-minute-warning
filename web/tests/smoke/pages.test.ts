// Smoke tests: every page renders against a seeded database (or a real publish with
// SMOKE_DATA=real) and says what it must: the data-as-of line, the disclaimer, the credits,
// list rows, charts with their tables, and real 404s.
import assert from "node:assert/strict";
import { before, describe, test } from "node:test";
import { CREDITS, DISCLAIMER } from "../../lib/site";
import { SEED } from "../seed";
import { BASE, DATA, fetchPage, normalise, prose, serverUp, text, visibleText, type Page } from "./dom";
import { SERVER_RENDERED_404, routeSet, type RouteSet } from "./routes";

let up = false;
let set: RouteSet;
const pages = new Map<string, Page>();
const missing = new Map<string, Page>();

before(
  async () => {
    up = await serverUp();
    if (!up) return;
    set = await routeSet();
    for (const r of set.routes) pages.set(r.path, await fetchPage(r.path));
    for (const m of set.missing) missing.set(m, await fetchPage(m));
  },
  { timeout: 300_000 },
);

const seedOnly = DATA === "seed";

function main(p: Page): Element {
  const m = p.doc.querySelector("main#main");
  assert.ok(m, "no <main id=main>");
  return m;
}

describe("every page", () => {
  test("answers 200 and carries the shell (data-as-of, disclaimer, credits, noindex, skip link)", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    for (const note of set.notes) t.diagnostic(note);
    for (const [path, p] of pages) {
      assert.equal(p.status, 200, `${path} returned ${p.status}`);
      const asOf = p.doc.querySelector("[data-testid=data-as-of]");
      assert.ok(asOf, `${path}: no data-as-of line`);
      assert.match(prose(asOf), /^Data as of .+ \d{2}:\d{2} UTC \(\d{4} week \d+\), updated .+ \d{2}:\d{2} UTC$/, `${path}: ${prose(asOf)}`);
      assert.equal(normalise(p.doc.querySelector("[data-testid=disclaimer]")?.textContent ?? ""), DISCLAIMER, path);
      const credits = normalise(p.doc.querySelector("[data-testid=attribution]")?.textContent ?? "");
      for (const c of CREDITS) assert.ok(credits.includes(c), `${path}: credit ${c} missing`);
      assert.equal(p.doc.querySelector("meta[name=robots]")?.getAttribute("content"), "noindex, nofollow", path);
      const firstFocusable = p.doc.querySelector("a[href], button, input, select, textarea");
      assert.equal(firstFocusable?.getAttribute("href"), "#main", `${path}: the skip link is not the first focusable element`);
      assert.equal(p.doc.querySelectorAll("h1").length, 1, `${path}: expected exactly one <h1>`);
      assert.equal(p.doc.querySelectorAll("nav[aria-label=Main] a").length, 4, `${path}: main navigation`);
      assert.ok(!/postgres(ql)?:\/\//i.test(p.html), `${path}: a connection string in the page`);
    }
  });

  test("the seeded as-of and publish time are the ones shown", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    if (!seedOnly) return t.skip("seed data only");
    const p = pages.get("/")!;
    assert.equal(
      prose(p.doc.querySelector("[data-testid=data-as-of]")!),
      "Data as of Tue 29 Sep 2026, 14:00 UTC (2026 week 3), updated Tue 29 Sep 2026, 15:30 UTC",
    );
  });

  test("unknown pages and player ids answer 404", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    for (const [path, p] of missing) {
      assert.equal(p.status, 404, `${path} returned ${p.status}`);
      assert.ok(p.doc.querySelector("meta[name=robots][content~=noindex]"), `${path}: not noindex`);
      if (SERVER_RENDERED_404.has(path)) {
        assert.match(text(p.doc), /Not found/, path);
        assert.ok(p.doc.querySelector("[data-testid=disclaimer]"), `${path}: no footer`);
      } else {
        // the not-found page travels in the payload the browser renders
        assert.ok(p.html.includes("There is no page at this address."), `${path}: no not-found page in the payload`);
      }
    }
  });

  test("robots.txt disallows everything", async (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    const r = await fetch(`${BASE}/robots.txt`);
    const body = await r.text();
    assert.match(body, /User-Agent: \*/i);
    assert.match(body, /Disallow: \/\s*$/m);
  });
});

describe("/ (this week)", () => {
  test("the live top 5 per position, the track-record headline, the Regression Watch card", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    const p = pages.get("/")!;
    const m = main(p);
    assert.match(text(m), /Regression Watch arrives in a later phase/);
    const lists = m.querySelectorAll("[data-testid=pick-list]");
    if (seedOnly) {
      assert.equal(lists.length, 4);
      for (const l of Array.from(lists)) assert.equal(l.querySelectorAll("[data-testid=pick]").length, 5);
      assert.equal(
        prose(m.querySelector("[data-testid=track-headline]")!),
        "In the 2014–2025 backtest, on average 50.0% of the Radar's weekly top 10 were hits (95% interval 48.0–52.0%), against 40.0% for a list of last week's top scorers (precision@10).",
      );
      assert.match(text(m), /Seed note for QB/);
      assert.ok(m.querySelector("[data-kind=live]"), "no live label");
    } else if (lists.length === 0) {
      assert.ok(m.querySelector("[data-testid=empty-state]"), "neither live lists nor the empty state");
      t.diagnostic("no live list: the empty state is shown");
    }
    if (!seedOnly) assert.ok(m.querySelector("[data-testid=track-headline]"), "no track-record headline");
  });
});

describe("/waivers", () => {
  test("live weeks: live label, rows with chance, reasons and pending outcomes, rank badges", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    const live = set.routes.filter((r) => r.kind === "waivers-live");
    if (!live.length) return t.skip("no live list in this database");
    for (const r of live) {
      const m = main(pages.get(r.path)!);
      const list = m.querySelector("[data-testid=radar-list]");
      assert.equal(list?.getAttribute("data-kind"), "live", r.path);
      assert.match(text(m.querySelector("[data-testid=kind-label]")!), /^Live list/, r.path);
      const rows = m.querySelectorAll("[data-testid=pick]");
      assert.ok(rows.length > 0, `${r.path}: no rows`);
      assert.ok(m.querySelector("[data-testid=bucket-badges]"), `${r.path}: no rank badges`);
      if (seedOnly) {
        assert.equal(rows.length, SEED.sizes.live, r.path);
        assert.match(text(rows[0]), /Seed reason A/);
        assert.match(text(rows[0]), /Pending/);
        const badges = Array.from(m.querySelectorAll("[data-bucket]")).map((b) => normalise(b.textContent ?? ""));
        assert.deepEqual(badges, [
          "Ranks 1–5: 40% hit (4 of 10)",
          "Ranks 6–10: 40% hit (4 of 10)",
          "Ranks 11–25: 25% hit (1 of 4)",
        ]);
        assert.match(text(m.querySelector("[data-testid=bucket-badges]")!), /Reconstructed \w+ lists of 2024–2025 \(2 weekly lists/);
      }
    }
  });

  test("backtest weeks: reconstructed label, rows, outcomes, NULL chances said in words", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    const bt = set.routes.filter((r) => r.kind === "waivers-backtest");
    assert.ok(bt.length >= 4, "fewer than four backtest pages");
    for (const r of bt) {
      const m = main(pages.get(r.path)!);
      assert.equal(m.querySelector("[data-testid=radar-list]")?.getAttribute("data-kind"), "backtest", r.path);
      assert.match(text(m.querySelector("[data-testid=kind-label]")!), /^Reconstructed list \(backtest\)/, r.path);
      const rows = m.querySelectorAll("[data-testid=pick]");
      assert.ok(rows.length > 0, `${r.path}: no rows`);
      const chances = Array.from(rows).map((row) => text(row));
      // a NULL chance is words, never 0% and never the raw probability
      for (const c of chances) assert.ok(!/Chance 0%/.test(c), `${r.path}: a 0% chance`);
      const allNull = chances.every((c) => /Not available for this list/.test(c));
      if (allNull) assert.match(text(m), /Chance and priority: not available for this list/, r.path);
    }
    if (seedOnly) {
      const m = main(pages.get(`/waivers?season=2025&week=6&pos=RB`)!);
      assert.equal(m.querySelectorAll("[data-testid=pick]").length, SEED.sizes.backtest);
      assert.match(text(m), /No reasons for this list/);
      assert.match(text(m), /Chance and priority: not available for this list/);
      assert.match(text(m), /today's franchise code and name/);
      assert.ok(m.querySelector("[data-outcome=hit]") && m.querySelector("[data-outcome=no-hit]"), "hit and no hit");
      assert.match(text(m), /W7 RB7, W8 did not play, W9 RB33/);
      const badges = Array.from(m.querySelectorAll("[data-bucket]")).map((b) => normalise(b.textContent ?? ""));
      assert.deepEqual(badges, [
        "Ranks 1–5: 40% hit (2 of 5)",
        "Ranks 6–10: 40% hit (2 of 5)",
        "Ranks 11–25: 0% hit (0 of 2)",
      ]);
      // the reconstructed 2026 week has chances and reasons
      const rc = main(pages.get(`/waivers?season=2026&week=2&pos=RB&kind=backtest`)!);
      const first = rc.querySelector("[data-testid=pick]")!;
      assert.match(text(first), /Rowan Fielding/);
      assert.match(text(first), /58% similar players hit 55–61%/);
      assert.match(text(first), /Must-add/);
      assert.ok(!/not available for this list/.test(text(rc)), "the 2026 reconstructed list has chances");
    }
  });

  test("position tabs are links and the week picker is a GET form", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    const p = pages.get("/waivers")!;
    const tabs = Array.from(p.doc.querySelectorAll<HTMLAnchorElement>("nav[aria-label=Position] a"));
    assert.deepEqual(tabs.map((a) => a.textContent), ["QB", "RB", "WR", "TE"]);
    assert.equal(tabs.filter((a) => a.getAttribute("aria-current") === "page").length, 1);
    const form = p.doc.querySelector("form[action='/waivers']");
    assert.equal(form?.getAttribute("method"), "get");
    assert.ok(form?.querySelector("select[name=season]") && form?.querySelector("select[name=week]"));
    assert.ok(p.doc.querySelector("select[name=season] option"), "no seasons in the picker");
    if (seedOnly) {
      const seasons = Array.from(p.doc.querySelectorAll<HTMLOptionElement>("select[name=season] option")).map((o) => o.value);
      assert.deepEqual(seasons, ["2026", "2025", "2024"]);
      // the default list is the newest week, live first
      assert.equal(main(p).querySelector("[data-testid=radar-list]")?.getAttribute("data-kind"), "live");
    }
  });
});

describe("/player/[id]", () => {
  test("header, three charts as figures with captions and data tables, Radar history", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    const players = set.routes.filter((r) => r.kind === "player");
    if (!players.length) return t.skip("no player page in this database");
    for (const r of players) {
      const m = main(pages.get(r.path)!);
      assert.ok(m.querySelector("[data-testid=player-header]"), r.path);
      const figs = Array.from(m.querySelectorAll("figure[data-testid=chart]"));
      if (figs.length) {
        assert.equal(figs.length, 3, r.path);
        for (const f of figs) {
          assert.ok(normalise(f.querySelector("figcaption")?.textContent ?? "").length > 0, "caption");
          assert.ok(f.querySelector("details table tbody tr"), `${r.path}: a chart without its data table`);
        }
      }
      assert.ok(m.querySelector("nav[aria-label=Season] a[aria-current=page]"), `${r.path}: season picker`);
    }
    if (seedOnly) {
      const m = main(pages.get(`/player/${SEED.featured}`)!);
      assert.equal(normalise(m.querySelector("h1")?.textContent ?? ""), SEED.featuredName);
      assert.match(text(m), /Drafted 2023, round 3, pick 79/);
      const figs = Array.from(m.querySelectorAll("figure[data-testid=chart]"));
      assert.deepEqual(
        figs.map((f) => normalise(f.querySelector("figcaption")?.textContent ?? "")),
        [
          "Fantasy points and expected points (xFP) by week",
          "Snap share by week",
          "Target share and carry share by week",
        ],
      );
      assert.equal(figs[0].querySelectorAll("details tbody tr").length, 2, "2026 has two weeks");
      const hist = m.querySelector("[data-testid=radar-history]")!;
      assert.equal(hist.querySelectorAll("tbody tr").length, 2, "2026: live week 3 and reconstructed week 2");
      assert.match(text(m), /Also on Radar lists in: 2025 \(1\), 2024 \(1\)/);
      const m25 = main(pages.get(`/player/${SEED.featured}?season=2025`)!);
      assert.equal(m25.querySelectorAll("figure[data-testid=chart]")[0].querySelectorAll("details tbody tr").length, 5);
      assert.match(text(m25.querySelector("[data-testid=radar-history]")!), /Hit/);
      assert.match(text(m25), /no data/, "a NULL xFP is shown as 'no data'");
    }
  });
});

describe("/regression", () => {
  test("a stub that describes the module and shows no numbers", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    const m = main(pages.get("/regression")!);
    assert.match(text(m), /Sell-high/);
    assert.match(text(m), /Buy-low/);
    assert.ok(!/\d/.test(visibleText(m)), `digits on /regression: ${visibleText(m).match(/.{0,30}\d.{0,30}/)?.[0]}`);
    assert.equal(m.querySelectorAll("table, figure").length, 0);
  });
});

describe("/methodology", () => {
  test("sources, point-in-time rule, results from the track record, the full glossary", (t) => {
    if (!up) return t.skip(`no server at ${BASE}`);
    const m = main(pages.get("/methodology")!);
    for (const id of ["sources", "point-in-time", "leakage", "radar", "results", "glossary", "disclaimers"]) {
      assert.ok(m.querySelector(`#${id}`), `section #${id}`);
    }
    assert.ok(m.querySelector("[data-testid=pooled-y_hit] tbody tr"), "results table");
    assert.ok(m.querySelector("[data-testid=tier-table] tbody tr"), "priority table");
    if (seedOnly) {
      for (const n of SEED.glossaryNames) assert.ok(m.querySelector(`#term-${n}`), `glossary entry ${n}`);
      assert.match(text(m.querySelector("[data-testid=pooled-y_hit]")!), /Waiver Radar \(logistic regression\) 50\.0% 48\.0–52\.0%/);
      assert.equal(m.querySelectorAll("[data-testid=calibration-table] tbody tr").length, 2);
      assert.equal(m.querySelectorAll("[data-testid=tier-table] tbody tr").length, 3);
      assert.match(text(m), /Seed term xfp/, "model-output features are listed");
    }
  });
});
