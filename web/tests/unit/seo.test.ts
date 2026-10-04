import assert from "node:assert/strict";
import { test } from "node:test";
import { SITEMAP_PATHS, pageMetadata, siteOrigin, sitePublic, sitemapEntries } from "../../lib/seo";

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
