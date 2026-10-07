import assert from "node:assert/strict";
import { test } from "node:test";
import { SITE_DESCRIPTION, SITEMAP_PATHS, pageMetadata, playerLabel, siteOrigin, sitePublic, sitemapEntries } from "../../lib/seo";

test("the origin: SITE_URL, else Vercel's production domain, else localhost; only the origin", () => {
  assert.equal(siteOrigin({ SITE_URL: "https://example.org/some/path?x=1" }), "https://example.org");
  assert.equal(siteOrigin({ SITE_URL: " example.org ", VERCEL_PROJECT_PRODUCTION_URL: "x.vercel.app" }), "https://example.org");
  assert.equal(siteOrigin({ VERCEL_PROJECT_PRODUCTION_URL: "twm.vercel.app" }), "https://twm.vercel.app");
  assert.equal(siteOrigin({ SITE_URL: "ftp://nope", VERCEL_PROJECT_PRODUCTION_URL: "" }), "http://localhost:3000");
  assert.equal(siteOrigin({}), "http://localhost:3000");
});

test("indexing only when SITE_PUBLIC says true", () => {
  assert.equal(sitePublic({}), false);
  assert.equal(sitePublic({ SITE_PUBLIC: "1" }), false);
  assert.equal(sitePublic({ SITE_PUBLIC: "false" }), false);
  assert.equal(sitePublic({ SITE_PUBLIC: " TRUE " }), true);
});

test("a page's metadata: canonical path and a complete openGraph", () => {
  const m = pageMetadata("/waivers", "Waivers", "The Waiver Radar.");
  assert.equal(m.title, "Waivers");
  assert.deepEqual(m.alternates, { canonical: "/waivers" });
  assert.equal((m.openGraph as { url: string }).url, "/waivers");
  assert.equal((m.openGraph as { title: string }).title, "Waivers · Two-Minute Warning");
});

test("the sitemap only once the site is public (else /sitemap.xml answers 404), at the configured origin", () => {
  assert.equal(sitemapEntries({}), null);
  assert.equal(sitemapEntries({ SITE_PUBLIC: "false", SITE_URL: "https://example.org" }), null);
  const e = sitemapEntries({ SITE_PUBLIC: "true", SITE_URL: "https://example.org/x" })!;
  assert.deepEqual(e.map((x) => new URL(x.url).pathname), [...SITEMAP_PATHS]);
  assert.equal(e[0].url, "https://example.org");
  assert.deepEqual(e.find((x) => x.url.endsWith("/methodology"))?.changeFrequency, "monthly");
  assert.ok(e.filter((x) => !x.url.endsWith("/methodology")).every((x) => x.changeFrequency === "weekly"));
});

test("the default description fits in 200 characters and names every module", () => {
  assert.ok(SITE_DESCRIPTION.length <= 200, `${SITE_DESCRIPTION.length} characters`);
  const modules = ["Waiver Radar", "streamers", "Regression Watch", "Questionable", "Teammate out", "Playoff planner", "Decision Report Card", "Hot-Seat Meter", "Cliff board", "coach tendencies"];
  for (const m of modules) assert.ok(SITE_DESCRIPTION.includes(m), m);
});

test("a player's title tells namesakes apart: position and years", () => {
  const mw = { name: "Mike Williams", position: "WR" };
  assert.equal(playerLabel({ ...mw, rookieSeason: 2017, seasons: [2024, 2017, 2020] }), "Mike Williams (WR, 2017–2024)");
  assert.equal(playerLabel({ ...mw, rookieSeason: 2010, seasons: [2013, 2014] }), "Mike Williams (WR, 2010–2014)");
  assert.equal(playerLabel({ ...mw, rookieSeason: 2005, seasons: [] }), "Mike Williams (WR, rookie season 2005)");
  assert.equal(playerLabel({ ...mw, rookieSeason: null, seasons: [2025] }), "Mike Williams (WR, 2025)");
  assert.equal(playerLabel({ ...mw, rookieSeason: 2026, seasons: [2026] }), "Mike Williams (WR, 2026)");
  assert.equal(playerLabel({ name: "X", position: null, rookieSeason: null, seasons: [] }), "X");
});
