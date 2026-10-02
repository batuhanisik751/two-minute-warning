import type { MetadataRoute } from "next";
import { SITEMAP_PATHS, siteOrigin } from "@/lib/seo";

// The site's pages at the configured origin (lib/seo.ts). Player and coach pages are reached
// from these; listing them would mean a database read per sitemap request.
export const dynamic = "force-dynamic";

export default function sitemap(): MetadataRoute.Sitemap {
  const origin = siteOrigin();
  return SITEMAP_PATHS.map((p) => ({ url: `${origin}${p === "/" ? "" : p}`, changeFrequency: p === "/methodology" ? "monthly" : "weekly" }));
}
