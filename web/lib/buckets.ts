// The hit-rate badges of /waivers: how often players at each rank bucket of a list hit, in
// earlier reconstructed (backtest) lists of the same position. The database query
// (lib/queries/radar.ts getBucketRates) counts picks and hits per rank; this file turns
// those counts into buckets and badge text, and is unit-tested on its own.
import { RANK_BUCKETS } from "./method";
import { fmtInt, pct } from "./format";

export type Bucket = { lo: number; hi: number; label: string };

export const BUCKETS: Bucket[] = RANK_BUCKETS.map(([lo, hi]) => ({ lo, hi, label: `${lo}-${hi}` }));

/** The bucket of a rank ("1-5", "6-10", "11-25"), or null outside every bucket. */
export function bucketOf(rank: number, buckets: Bucket[] = BUCKETS): Bucket | null {
  if (!Number.isInteger(rank)) return null;
  return buckets.find((b) => rank >= b.lo && rank <= b.hi) ?? null;
}

export type RankCount = { rank: number; picks: number; hits: number };

export type Badge = {
  bucket: Bucket;
  picks: number;
  hits: number;
  /** hits / picks, or null without picks */
  rate: number | null;
};

/** Per-rank counts -> one badge per bucket (a rank outside every bucket is ignored). */
export function badges(counts: RankCount[], buckets: Bucket[] = BUCKETS): Badge[] {
  return buckets.map((bucket) => {
    let picks = 0;
    let hits = 0;
    for (const c of counts) {
      if (c.rank >= bucket.lo && c.rank <= bucket.hi) {
        picks += c.picks;
        hits += c.hits;
      }
    }
    return { bucket, picks, hits, rate: picks > 0 ? hits / picks : null };
  });
}

/** "Ranks 1-5: 56% hit (2,064 of 3,660)" or "Ranks 11-25: no earlier lists". */
export function badgeText(b: Badge): string {
  const head = `Ranks ${b.bucket.label.replace("-", "–")}`;
  if (b.rate === null) return `${head}: no earlier lists`;
  return `${head}: ${pct(b.rate)} hit (${fmtInt(b.hits)} of ${fmtInt(b.picks)})`;
}

/** "2014-2025" / "2014" / null (the seasons the badges count). */
export function seasonsText(from: number | null, to: number | null): string | null {
  if (from === null || to === null) return null;
  return from === to ? String(from) : `${from}–${to}`;
}
