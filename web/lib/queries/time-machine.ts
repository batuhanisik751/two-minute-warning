import "server-only";
import { and, desc, eq } from "drizzle-orm";
import { db } from "@/db/client";
import { coachWeek } from "@/db/schema";
import { cached } from "@/lib/cache";
import { callTotals } from "@/lib/decisions";
import type { WeekRef } from "@/lib/params";

// The time machine's own reads (/time-machine): the Decision Report Card's graded weeks and one
// week's totals, both from coach_week (one row per coach and game). Every other module's lists
// come from that module's own queries (radar, stream, regression, hot-seat, board).

/** Every (season, week) with graded calls, newest first. */
async function getDecisionWeeksRaw(): Promise<WeekRef[]> {
  return db()
    .selectDistinct({ season: coachWeek.season, week: coachWeek.week })
    .from(coachWeek)
    .orderBy(desc(coachWeek.season), desc(coachWeek.week));
}
export const getDecisionWeeks = cached("timeMachine.getDecisionWeeks", getDecisionWeeksRaw);

/** One week's league totals (lib/decisions callTotals over its coach_week rows: one per coach
 *  and game, so each row is one team-game). */
async function getWeekCallsRaw(season: number, week: number): Promise<ReturnType<typeof callTotals>> {
  const rows = await db()
    .select()
    .from(coachWeek)
    .where(and(eq(coachWeek.season, season), eq(coachWeek.week, week)));
  return callTotals(rows.map((r) => ({ ...r, games: 1 })));
}
export const getWeekCalls = cached("timeMachine.getWeekCalls", getWeekCallsRaw);
