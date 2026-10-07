// The site's public address and whether search engines may index it: one place, from the
// environment (web/README.md "Search engines", docs/deploy.md step 10). Nothing here is a
// hard-coded domain.
//
// - SITE_URL: the canonical origin, e.g. https://example.org (only its origin is used). Unset:
//   Vercel's VERCEL_PROJECT_PRODUCTION_URL (the production domain Vercel sets itself), else
//   http://localhost:3000 (development).
// - SITE_PUBLIC=true: robots.txt allows crawling and pages are indexable. Anything else (the
//   default): robots.txt disallows everything and every page says noindex, as while the site
//   is behind Vercel Authentication.
import type { Metadata } from "next";

type Env = Record<string, string | undefined>;

export const SITE_NAME = "Two-Minute Warning";
/** The default meta description: at most 200 characters (search engines cut longer ones), naming
 *  every module on the site (tests/unit/seo.test.ts). */
export const SITE_DESCRIPTION =
  "An open, point-in-time NFL site: Waiver Radar, K/DST streamers, Regression Watch, Questionable, Teammate out, Playoff planner, Decision Report Card, Hot-Seat Meter, Cliff board and coach tendencies.";

function origin(raw: string | undefined): string | null {
  const v = raw?.trim();
  if (!v) return null;
  try {
    // a bare host (Vercel's variables have no scheme) is https
    const u = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(v) ? v : `https://${v}`);
    return u.protocol === "http:" || u.protocol === "https:" ? u.origin : null;
  } catch {
    return null;
  }
}

export function siteOrigin(env: Env = process.env): string {
  return origin(env.SITE_URL) ?? origin(env.VERCEL_PROJECT_PRODUCTION_URL) ?? "http://localhost:3000";
}

export function sitePublic(env: Env = process.env): boolean {
  return env.SITE_PUBLIC?.trim().toLowerCase() === "true";
}

/** The routes a sitemap lists (the pages of the main navigation and the track record). */
export const SITEMAP_PATHS = ["/", "/waivers", "/regression", "/questionable", "/teammate-out", "/playoff-planner", "/decisions", "/hot-seat", "/board", "/time-machine", "/track-record", "/methodology"] as const;

/** The sitemap's entries at the configured origin, or null while the site is private
 *  (SITE_PUBLIC not true): app/sitemap.ts then answers 404, as robots.txt names no sitemap. */
export function sitemapEntries(env: Env = process.env): { url: string; changeFrequency: "weekly" | "monthly" }[] | null {
  if (!sitePublic(env)) return null;
  const o = siteOrigin(env);
  return SITEMAP_PATHS.map((p) => ({ url: `${o}${p === "/" ? "" : p}`, changeFrequency: p === "/methodology" ? "monthly" : "weekly" }));
}

/** A page's title, description, canonical URL (the path without its query: a picked week or
 *  filter is a view of the same page) and Open Graph basics. Metadata merges shallowly, so the
 *  page sets its whole openGraph object. */
export function pageMetadata(path: string, title: string, description: string): Metadata {
  return {
    title,
    description,
    alternates: { canonical: path },
    openGraph: { type: "website", siteName: SITE_NAME, locale: "en_US", url: path, title: `${title} · ${SITE_NAME}`, description },
    twitter: { card: "summary", title: `${title} · ${SITE_NAME}`, description },
  };
}

/** A player page's name for its title and description, with what tells namesakes apart (three
 *  Mike Williams): "Mike Williams (WR, 2017–2024)" = his position and his years, from his rookie
 *  season (else the first season with published rows) to the last season with published rows; a
 *  player without any published row: "rookie season 2005". */
export function playerLabel(p: { name: string; position: string | null; rookieSeason: number | null; seasons: readonly number[] }): string {
  const last = p.seasons.length ? Math.max(...p.seasons) : null;
  const first = p.rookieSeason ?? (p.seasons.length ? Math.min(...p.seasons) : null);
  const years =
    first !== null && last !== null ? (first >= last ? String(last) : `${first}–${last}`) : first !== null ? `rookie season ${first}` : null;
  const parts = [p.position, years].filter((x): x is string => !!x);
  return parts.length ? `${p.name} (${parts.join(", ")})` : p.name;
}
