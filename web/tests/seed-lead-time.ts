// The FICTIONAL seed of the lead-time tables (feature #8; every number is made up, no player
// anywhere: the tables hold aggregates only), loaded by tests/seed.ts for the "full" variant.
// Seasons: 2020 partial (from waiver period 5), 2021-2023 complete, 2024 excluded (no in-season
// roster %). Crowd at 50%: 100 adds; at 25%: 140. One season row is below the 5-adds minimum
// (statistics NULL). Typed against db/schema.ts.
import type { NodePgDatabase } from "drizzle-orm/node-postgres";
import * as s from "../db/schema";

type Db = NodePgDatabase<typeof s>;
type Summary = typeof s.leadTimeSummary.$inferInsert;

/** [before, same, after, never, never out of pool, median, q1, q3, nearest] per signal */
const SPLITS: Record<number, Record<string, number[]>> = {
  50: { listed: [60, 5, 15, 20, 15, 3, 1, 6, 0], spec_plus: [55, 5, 15, 25, 18, 3, 0, 6, 0], must_add: [20, 10, 20, 50, 30, 0, -2, 2, 0], momentum: [70, 25, 0, 5, 5, 1, 0, 2, 1] },
  25: { listed: [77, 10, 35, 18, 10, 2, -1, 6, 0], spec_plus: [70, 10, 40, 20, 12, 1, -1, 4, 0], must_add: [14, 7, 35, 84, 50, -1, -3, 1, -1], momentum: [42, 84, 0, 14, 14, 0, 0, 1, 0] },
};
export const LEAD_TIME_SEED = {
  span: "2021–2023",
  adds: { 50: 100, 25: 140 },
  /** the headline tiles: listed, must-add, momentum flagged before (crowd at 50%) */
  headline: ["60.0%", "20.0%", "70.0%"],
  /** the 25% sentence: listed vs momentum before */
  t25: ["55.0%", "30.0%"],
  /** the must-adds the crowd never added, and their hit rate (vs added later) */
  ignored: { n: 50, hit: "36.0%", laterHit: "90.0%" },
  /** the head to head, listed vs momentum: both (Radar earlier), Radar only, momentum only, neither */
  h2hListed: [50, 40, 10, 20, 20],
};

function summaryRow(threshold: number, signal: string, scope: string, scopeValue: string, nAdds: number, v: number[] | null): Summary {
  const share = (k: number) => (v === null ? null : v[k] / nAdds);
  return {
    threshold, signal, scope, scopeValue, nAdds,
    nBefore: v?.[0] ?? null, nSame: v?.[1] ?? null, nAfter: v?.[2] ?? null, nNever: v?.[3] ?? null, nNeverOutOfPool: v?.[4] ?? null,
    shareBefore: share(0), shareSame: share(1), shareAfter: share(2), shareNever: share(3),
    leadMedian: v?.[5] ?? null, leadQ1: v?.[6] ?? null, leadQ3: v?.[7] ?? null, nearestMedian: v?.[8] ?? null,
    shareBefore4: v === null ? null : Math.min(v[0], 0.6 * nAdds) / nAdds,
  };
}

function summaryRows(): Summary[] {
  const out: Summary[] = [];
  for (const t of [50, 25] as const) {
    const n = LEAD_TIME_SEED.adds[t];
    for (const [signal, v] of Object.entries(SPLITS[t])) {
      out.push(summaryRow(t, signal, "complete", "", n, v));
      // one season row: 2020 (partial) has 4 adds, too few for statistics (the page shows none of it)
      out.push(summaryRow(t, signal, "season", "2020", 4, null));
    }
  }
  return out;
}

/** The flagged adds spread over the leads -6..10 (complete seasons): flagged = before + same + after. */
function histRows(): (typeof s.leadTimeHist.$inferInsert)[] {
  const out: (typeof s.leadTimeHist.$inferInsert)[] = [];
  for (const t of [50, 25] as const) {
    for (const [signal, v] of Object.entries(SPLITS[t])) {
      const [before, same, after] = v;
      const spread = (n: number, leads: number[]) => leads.forEach((lead, i) => {
        const k = Math.floor(n / leads.length) + (i < n % leads.length ? 1 : 0);
        if (k > 0) out.push({ threshold: t, signal, lead, n: k });
      });
      spread(after, [-6, -5, -4, -3, -2, -1]);
      if (same > 0) out.push({ threshold: t, signal, lead: 0, n: same });
      spread(before, signal === "momentum" ? [1, 2, 3] : [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
    }
  }
  return out;
}

const STATE_ROWS: Record<string, [number, number | null, number | null][]> = {
  // [n, hits, lead median] for already / added_later / never (crowd at 50%, complete seasons)
  must_add: [[40, 28, null], [30, 27, 1], [50, 18, null]],
  spec_plus: [[60, 33, null], [70, 49, 3], [300, 90, null]],
  listed: [[70, 35, null], [75, 50, 4], [450, 99, null]],
  momentum: [[3, null, null], [110, null, 1], [80, null, null]],
};

export async function seedLeadTime(db: Db) {
  const T = LEAD_TIME_SEED;
  await db.insert(s.leadTimeCoverage).values([
    { season: 2020, baselineDate: "2020-10-16", baselinePeriod: 5, lastPeriod: 16, complete: false, inSeasonDays: 13, firstInSeason: "2020-10-16", lastInSeason: "2021-01-01", nPlayers: 700, lastListWeek: 15, adds50: 30, adds25: 31, inStudy: true },
    ...[2021, 2022, 2023].map((season, i) => ({ season, baselineDate: `${season}-09-10`, baselinePeriod: 0, lastPeriod: 17, complete: true, inSeasonDays: 17, firstInSeason: `${season}-09-17`, lastInSeason: `${season + 1}-01-07`, nPlayers: 850, lastListWeek: 16, adds50: [33, 33, 34][i], adds25: [46, 46, 48][i], inStudy: true })),
    { season: 2024, baselineDate: "2024-08-09", baselinePeriod: 0, lastPeriod: 0, complete: false, inSeasonDays: 0, firstInSeason: null, lastInSeason: null, nPlayers: 750, lastListWeek: null, adds50: null, adds25: null, inStudy: false },
  ]);
  await db.insert(s.leadTimeSummary).values(summaryRows());
  await db.insert(s.leadTimeHist).values(histRows());
  await db.insert(s.leadTimeReverse).values(
    Object.entries(STATE_ROWS).flatMap(([signal, rows]) =>
      ["already", "added_later", "never"].map((state, i) => {
        const [n, hits, lead] = rows[i];
        return { threshold: 50, signal, scope: "complete", scopeValue: "", state, n, hits, hitRate: hits === null ? null : hits / n, leadMedian: lead };
      }),
    ),
  );
  await db.insert(s.leadTimeConversion).values(
    ["listed", "spec_plus", "must_add", "momentum"].map((signal, i) => ({ threshold: 50, signal, scope: "complete", scopeValue: "", nFlags: [600, 450, 100, 230][i], nAdded: [80, 80, 40, 130][i], nAddedAfter: [75, 74, 27, 85][i], weeks: 48, flagsPerWeek: [600, 450, 100, 230][i] / 48, shareAdded: [80 / 600, 80 / 450, 0.4, 130 / 230][i], shareAddedAfter: [75 / 600, 74 / 450, 0.27, 85 / 230][i] })),
  );
  const [both, earlier, radar, mom, neither] = T.h2hListed;
  const H2H: Record<string, number[]> = { listed: [both, radar, mom, neither], spec_plus: [48, 12, 22, 18], must_add: [18, 2, 52, 28] };
  await db.insert(s.leadTimeH2h).values(
    Object.entries(H2H).flatMap(([level, ns]) =>
      ["both", "radar_only", "momentum_only", "neither"].map((h2h, i) => ({ threshold: 50, level, h2h, n: ns[i], nRadarEarlier: i === 0 ? (level === "listed" ? earlier : Math.round(ns[0] * 0.6)) : 0, share: ns[i] / 100 })),
    ),
  );
}
