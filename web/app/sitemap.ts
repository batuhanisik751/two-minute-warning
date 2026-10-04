import type { MetadataRoute } from "next";
import { notFound } from "next/navigation";
import { sitemapEntries } from "@/lib/seo";

// The site's pages at the configured origin (lib/seo.ts), only once the site is public
// (SITE_PUBLIC=true, like robots.txt's Sitemap line): before that /sitemap.xml answers 404.
// Read per request so it follows the flag. Player and coach pages are reached from these;
// listing them would mean a database read per sitemap request.
export const dynamic = "force-dynamic";

export default function sitemap(): MetadataRoute.Sitemap {
  const entries = sitemapEntries();
  if (!entries) notFound();
  return entries;
}
