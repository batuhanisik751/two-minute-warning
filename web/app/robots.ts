import type { MetadataRoute } from "next";
import { siteOrigin, sitePublic } from "@/lib/seo";

// Read per request, like the pages' robots meta tag (app/layout.tsx), so both follow the same
// flag: SITE_PUBLIC=true opens the site to crawlers (lib/seo.ts); by default nothing is crawled.
export const dynamic = "force-dynamic";

export default function robots(): MetadataRoute.Robots {
  if (!sitePublic()) return { rules: [{ userAgent: "*", disallow: "/" }] };
  // /league is the owner's local-only page (404 in production); disallowed all the same
  return { rules: [{ userAgent: "*", allow: "/", disallow: "/league" }], sitemap: `${siteOrigin()}/sitemap.xml` };
}
