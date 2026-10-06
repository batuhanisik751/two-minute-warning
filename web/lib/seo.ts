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
export const SITE_DESCRIPTION =
  "An open, point-in-time NFL early-warning site: the Waiver Radar, K and D/ST streamers, Regression Watch, the Decision Report Card, the Hot-Seat Meter and the Cliff board, each with its public track record.";

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
