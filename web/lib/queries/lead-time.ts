import "server-only";
import { asc } from "drizzle-orm";
import { db } from "@/db/client";
import { leadTimeCoverage, leadTimeH2h, leadTimeHist, leadTimeReverse, leadTimeSummary } from "@/db/schema";
import { cached } from "@/lib/cache";
import type { LeadTimeTables } from "@/lib/lead-time";

// Lead time vs the crowd's published tables (feature #8, migration 0011): lead_time_coverage,
// _summary, _hist, _reverse and _h2h, aggregates only (src/twm/publish/lead_time.py). Every
// number of /track-record's "Does the Radar beat the crowd?" comes from here. Small tables: every
// row (the coverage without its dates; the page names seasons and periods only).

async function getLeadTimeTablesRaw(): Promise<LeadTimeTables> {
  const c = leadTimeCoverage;
  const [coverage, summary, hist, reverse, h2h] = await Promise.all([
    db()
      .select({ season: c.season, baselinePeriod: c.baselinePeriod, complete: c.complete, inSeasonDays: c.inSeasonDays, lastListWeek: c.lastListWeek, adds50: c.adds50, adds25: c.adds25, inStudy: c.inStudy })
      .from(c)
      .orderBy(asc(c.season)),
    db().select().from(leadTimeSummary),
    db().select().from(leadTimeHist).orderBy(asc(leadTimeHist.lead)),
    db().select().from(leadTimeReverse),
    db().select().from(leadTimeH2h),
  ]);
  return { coverage, summary, hist, reverse, h2h };
}
export const getLeadTimeTables = cached("leadTime.getLeadTimeTables", getLeadTimeTablesRaw);
