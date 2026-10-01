import "server-only";
import { and, asc, desc, eq, isNotNull } from "drizzle-orm";
import { db } from "@/db/client";
import { radarOutcome, radarPick, regressionList, regressionOutcome, regressionRow, streamOutcome, streamPick, streamTrackRecord } from "@/db/schema";
import { cached } from "@/lib/cache";
import type { StreamTrackRow } from "@/lib/streamer";
import type { LivePick, LiveTagRow } from "@/lib/track-record";

// The /track-record page's reads that the other pages do not make: the streamer's per-season rows
// (getStreamTrack leaves them out) and every live list's picks with their outcomes.

/** stream_track_record's per-season rows (scope 'season'), in the CSV's order. */
async function getStreamSeasonRowsRaw(): Promise<StreamTrackRow[]> {
  return db()
    .select({
      position: streamTrackRecord.position,
      method: streamTrackRecord.method,
      trainOn: streamTrackRecord.trainOn,
      scope: streamTrackRecord.scope,
      seasons: streamTrackRecord.seasons,
      key: streamTrackRecord.key,
      metric: streamTrackRecord.metric,
      value: streamTrackRecord.value,
      lo: streamTrackRecord.lo,
      hi: streamTrackRecord.hi,
      nGroups: streamTrackRecord.nGroups,
      nRows: streamTrackRecord.nRows,
      nPos: streamTrackRecord.nPos,
    })
    .from(streamTrackRecord)
    .where(eq(streamTrackRecord.scope, "season"))
    .orderBy(asc(streamTrackRecord.line));
}
export const getStreamSeasonRows = cached("trackRecord.getStreamSeasonRows", getStreamSeasonRowsRaw);

/** Every pick of every live Waiver Radar list, with its outcome (NULL before one is published). */
async function getRadarLivePicksRaw(): Promise<LivePick[]> {
  return db()
    .select({
      season: radarPick.season,
      week: radarPick.week,
      position: radarPick.position,
      rank: radarPick.rank,
      hit: radarOutcome.yHit,
      status: radarOutcome.labelStatus,
    })
    .from(radarPick)
    .leftJoin(
      radarOutcome,
      and(eq(radarOutcome.season, radarPick.season), eq(radarOutcome.week, radarPick.week), eq(radarOutcome.gsisId, radarPick.gsisId)),
    )
    .where(eq(radarPick.kind, "live"));
}
export const getRadarLivePicks = cached("trackRecord.getRadarLivePicks", getRadarLivePicksRaw);

/** Every pick of every live K and D/ST list, with its outcome (y_start). */
async function getStreamLivePicksRaw(): Promise<LivePick[]> {
  return db()
    .select({
      season: streamPick.season,
      week: streamPick.week,
      position: streamPick.position,
      rank: streamPick.rank,
      hit: streamOutcome.yStart,
      status: streamOutcome.labelStatus,
    })
    .from(streamPick)
    .leftJoin(
      streamOutcome,
      and(eq(streamOutcome.season, streamPick.season), eq(streamOutcome.week, streamPick.week), eq(streamOutcome.entityId, streamPick.entityId)),
    )
    .where(eq(streamPick.kind, "live"));
}
export const getStreamLivePicks = cached("trackRecord.getStreamLivePicks", getStreamLivePicksRaw);

export type RegressionLive = {
  /** the live lists (season, week), newest first */
  lists: { season: number; week: number }[];
  /** every tagged row of them, with its rest-of-season outcome */
  rows: LiveTagRow[];
};

/** The live Regression Watch lists and their tagged rows (tags as published: Legit is dropped
 *  by `twm publish`; the page counts the product's tags only). */
async function getRegressionLiveRaw(): Promise<RegressionLive> {
  const lists = await db()
    .select({ season: regressionList.season, week: regressionList.week })
    .from(regressionList)
    .where(eq(regressionList.kind, "live"))
    .orderBy(desc(regressionList.season), desc(regressionList.week));
  const rows = await db()
    .select({
      season: regressionRow.season,
      week: regressionRow.week,
      tags: regressionRow.tags,
      ppg: regressionRow.ppg,
      rosPpg: regressionOutcome.rosPpg,
      status: regressionOutcome.labelStatus,
    })
    .from(regressionRow)
    .leftJoin(
      regressionOutcome,
      and(
        eq(regressionOutcome.season, regressionRow.season),
        eq(regressionOutcome.week, regressionRow.week),
        eq(regressionOutcome.gsisId, regressionRow.gsisId),
      ),
    )
    .where(and(eq(regressionRow.kind, "live"), isNotNull(regressionRow.tag)));
  return { lists, rows };
}
export const getRegressionLive = cached("trackRecord.getRegressionLive", getRegressionLiveRaw);
