import { cache } from "react";
import { unstable_cache } from "next/cache";
import { databaseKey } from "@/db/client";

// Every read in lib/queries is wrapped in `cached()` (the F1 app's pattern). Pages stay
// force-dynamic: a page renders per request and its data comes from this cache.
//
// - Production: Next's data cache, 1 hour, one tag for everything. There is no revalidation
//   hook yet: a publish shows up within the hour. (E4: a route that calls
//   revalidateTag(DATA_TAG, { expire: 0 }) after `twm publish` would make it immediate.)
// - Development and tests (NODE_ENV != production) or DATA_CACHE=0: no data cache.
// - Always: React's per-request cache, so a page that asks for the glossary in twenty
//   tooltips reads it once.
//
// Cached values round-trip through JSON: query functions return strings for timestamps,
// never Date objects. The key includes a fingerprint of the database URL, so a server
// pointed at another database never serves the first one's rows.
export const DATA_TAG = "data";
export const DATA_TTL_S = 3600;

const ON = process.env.NODE_ENV === "production" && process.env.DATA_CACHE !== "0";

/** Wrap a query-layer read. `name` is `<module>.<export>`, unique per function. */
export function cached<A extends unknown[], R>(
  name: string,
  fn: (...args: A) => Promise<R>,
): (...args: A) => Promise<R> {
  if (!ON) return cache(fn);
  // the fingerprint is read on the first call (never at import time, never at build time)
  let wrapped: ((...args: A) => Promise<R>) | undefined;
  return cache((...args: A) => {
    wrapped ??= unstable_cache(fn, [name, databaseKey()], {
      tags: [DATA_TAG],
      revalidate: DATA_TTL_S,
    });
    return wrapped(...args);
  });
}
