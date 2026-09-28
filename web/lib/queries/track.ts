import "server-only";
import { and, asc, desc, eq, inArray } from "drizzle-orm";
import { db } from "@/db/client";
import { tierStats, trackRecord } from "@/db/schema";
import { cached } from "@/lib/cache";

export const MODULE = "waiver_radar";

export type TrackRow = {
  model: string;
  label: string;
  metric: string;
  scope: string;
  scopeValue: string;
  seasonFrom: number;
  seasonTo: number;
  exclRostered: boolean;
  value: number | null;
  low: number | null;
  high: number | null;
  nLists: number | null;
  nPositives: number | null;
  nRows: number | null;
  nTopHits: number | null;
  nTop: number | null;
};

/** The track record rows (reports/waiver_radar/evaluation.csv, as published) of `scopes`. */
async function getTrackRowsRaw(scopes: string[]): Promise<TrackRow[]> {
  return db()
    .select({
      model: trackRecord.model,
      label: trackRecord.label,
      metric: trackRecord.metric,
      scope: trackRecord.scope,
      scopeValue: trackRecord.scopeValue,
      seasonFrom: trackRecord.seasonFrom,
      seasonTo: trackRecord.seasonTo,
      exclRostered: trackRecord.exclRostered,
      value: trackRecord.value,
      low: trackRecord.low,
      high: trackRecord.high,
      nLists: trackRecord.nLists,
      nPositives: trackRecord.nPositives,
      nRows: trackRecord.nRows,
      nTopHits: trackRecord.nTopHits,
      nTop: trackRecord.nTop,
    })
    .from(trackRecord)
    .where(and(eq(trackRecord.module, MODULE), inArray(trackRecord.scope, scopes)))
    .orderBy(
      asc(trackRecord.scope),
      asc(trackRecord.label),
      asc(trackRecord.seasonFrom),
      asc(trackRecord.model),
      asc(trackRecord.scopeValue),
      asc(trackRecord.metric),
    );
}
export const getTrackRows = cached("track.getTrackRows", getTrackRowsRaw);

export type TierRow = {
  model: string;
  label: string;
  week: number;
  tier: string;
  seasonFrom: number;
  seasonTo: number;
  chanceLow: number;
  chanceHigh: number;
  lists: number;
  players: number;
  hits: number;
  hitRate: number | null;
};

/** How each suggested priority did in the backtest, every week pooled (week 0). */
async function getTierStatsRaw(): Promise<TierRow[]> {
  return db()
    .select({
      model: tierStats.model,
      label: tierStats.label,
      week: tierStats.week,
      tier: tierStats.tier,
      seasonFrom: tierStats.seasonFrom,
      seasonTo: tierStats.seasonTo,
      chanceLow: tierStats.chanceLow,
      chanceHigh: tierStats.chanceHigh,
      lists: tierStats.lists,
      players: tierStats.players,
      hits: tierStats.hits,
      hitRate: tierStats.hitRate,
    })
    .from(tierStats)
    .where(and(eq(tierStats.module, MODULE), eq(tierStats.week, 0)))
    .orderBy(desc(tierStats.chanceLow));
}
export const getTierStats = cached("track.getTierStats", getTierStatsRaw);
