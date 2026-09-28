import type { MetadataRoute } from "next";

// Nothing is indexed until launch (E5); every page also carries <meta name="robots"
// content="noindex, nofollow"> (app/layout.tsx).
export default function robots(): MetadataRoute.Robots {
  return { rules: [{ userAgent: "*", disallow: "/" }] };
}
