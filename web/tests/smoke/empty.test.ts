// The empty states, against a database that has had a first publish but no weekly list and
// no track record yet (tests/seed.ts "empty"). Needs SMOKE_EMPTY_BASE_URL (the runner starts
// that second server); required whenever SMOKE_REQUIRE=1 and the data is the seed.
import assert from "node:assert/strict";
import { before, describe, test } from "node:test";
import { DATA, EMPTY_BASE, REQUIRE, fetchPage, prose, serverUp, text, type Page } from "./dom";

let up = false;
const pages = new Map<string, Page>();

before(
  async () => {
    if (!EMPTY_BASE) {
      if (REQUIRE && DATA === "seed") throw new Error("SMOKE_REQUIRE=1 but SMOKE_EMPTY_BASE_URL is not set");
      return;
    }
    up = await serverUp(EMPTY_BASE);
    if (!up) return;
    for (const r of ["/", "/waivers", "/methodology", "/player/00-9000013"]) pages.set(r, await fetchPage(r, EMPTY_BASE));
  },
  { timeout: 120_000 },
);

describe("empty states (no list published yet)", () => {
  test("home: no live list yet, no invented dates", (t) => {
    if (!up) return t.skip("no empty-database server (SMOKE_EMPTY_BASE_URL)");
    const p = pages.get("/")!;
    assert.equal(p.status, 200);
    const m = p.doc.querySelector("main")!;
    assert.match(text(m), /No live list yet/);
    assert.match(text(m), /The first live list appears on a Tuesday after the as-of time \(14:00 UTC\)/);
    assert.equal(m.querySelectorAll("[data-testid=pick-list]").length, 0);
    assert.equal(m.querySelectorAll("[data-testid=track-headline]").length, 0);
    assert.ok(!/\b20\d\d\b/.test(prose(m)), `a year on the empty home page: ${prose(m)}`);
    assert.equal(
      prose(p.doc.querySelector("[data-testid=data-as-of]")!),
      "Data as of: no weekly list has been published yet (updated Tue 29 Sep 2026, 15:30 UTC).",
    );
  });

  test("waivers and methodology say nothing is published; a player page is a 404", (t) => {
    if (!up) return t.skip("no empty-database server (SMOKE_EMPTY_BASE_URL)");
    assert.match(text(pages.get("/waivers")!.doc.querySelector("main")!), /No lists published yet/);
    assert.match(text(pages.get("/methodology")!.doc.querySelector("main")!), /No track record published yet/);
    assert.equal(pages.get("/player/00-9000013")!.status, 404);
  });
});
