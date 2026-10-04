import "server-only";
import { and, asc, count, desc, eq, sql } from "drizzle-orm";
import { alias } from "drizzle-orm/pg-core";
import { db } from "@/db/client";
import { dimTeam, modelVersions, streamList, streamOutcome, streamPick, streamTrackRecord } from "@/db/schema";
import { cached } from "@/lib/cache";
import type { ListKey } from "@/lib/params";
import type { StreamTrackRow } from "@/lib/streamer";
import { reasonTexts } from "./radar";
import { showThirdPartyRanks, shownReasons } from "@/lib/third-party";

// The K and D/ST streamer's published lists (stream_list / stream_pick / stream_outcome) and
// its track record (stream_track_record). Same live/backtest rules as the Radar's lists.

const iso = (d: Date | string) => new Date(d).toISOString();
const kindOf = (k: string): "live" | "backtest" => (k === "live" ? "live" : "backtest");

/** The positions with at least one published streamer list (K, DST). */
async function getStreamPositionsRaw(): Promise<string[]> {
  const rows = await db().selectDistinct({ position: streamList.position }).from(streamList);
  return rows.map((r) => r.position);
}
export const getStreamPositions = cached("stream.getStreamPositions", getStreamPositionsRaw);

/** Every published (season, week, kind) of one position, newest first, live first. */
async function getStreamIndexRaw(position: string): Promise<(ListKey & { positions: number })[]> {
  const rows = await db()
    .select({ season: streamList.season, week: streamList.week, kind: streamList.kind, positions: count() })
    .from(streamList)
    .where(eq(streamList.position, position))
    .groupBy(streamList.season, streamList.week, streamList.kind)
    .orderBy(desc(streamList.season), desc(streamList.week), desc(sql`${streamList.kind} = 'live'`));
  return rows.map((r) => ({ ...r, kind: kindOf(r.kind) }));
}
export const getStreamIndex = cached("stream.getStreamIndex", getStreamIndexRaw);

export type StreamHeader = {
  season: number;
  week: number;
  position: string;
  kind: "live" | "backtest";
  asOf: string;
  generatedAt: string;
  incomplete: boolean;
  nPool: number;
  note: string | null;
  modelVersion: string;
  model: string;
  trainingSeasons: number[];
};

export type StreamPick = {
  position: string;
  rank: number;
  entityId: string;
  entityType: string;
  name: string;
  team: string;
  teamName: string;
  teamColor: string | null;
  teamColor2: string | null;
  opponent: string | null;
  opponentName: string | null;
  home: boolean | null;
  chance: number | null;
  chanceLow: number | null;
  chanceHigh: number | null;
  tier: string | null;
  reasons: string[];
  outcome: { yStart: boolean | null; status: string; points: number | null } | null;
};

const opp = alias(dimTeam, "opp_team");

const headerColumns = {
  season: streamList.season,
  week: streamList.week,
  position: streamList.position,
  kind: streamList.kind,
  asOf: streamList.asOf,
  generatedAt: streamList.generatedAt,
  incomplete: streamList.incomplete,
  nPool: streamList.nPool,
  note: streamList.note,
  modelVersion: streamList.modelVersion,
  model: modelVersions.model,
  trainingSeasons: modelVersions.trainingSeasons,
};

type HeaderRow = {
  season: number; week: number; position: string; kind: string; asOf: Date; generatedAt: Date;
  incomplete: boolean; nPool: number; note: string | null; modelVersion: string; model: string; trainingSeasons: number[];
};

function toHeader(h: HeaderRow): StreamHeader {
  return {
    ...h,
    kind: kindOf(h.kind),
    asOf: iso(h.asOf),
    generatedAt: iso(h.generatedAt),
    note: h.note && h.note.trim() ? h.note : null,
  };
}

function picksQuery() {
  return db()
    .select({
      position: streamPick.position,
      rank: streamPick.rank,
      entityId: streamPick.entityId,
      entityType: streamPick.entityType,
      name: streamPick.displayName,
      team: streamPick.team,
      teamName: dimTeam.teamName,
      teamColor: dimTeam.color,
      teamColor2: dimTeam.color2,
      opponent: streamPick.nextOpponent,
      opponentName: opp.teamName,
      home: streamPick.home,
      chance: streamPick.chance,
      chanceLow: streamPick.chanceLow,
      chanceHigh: streamPick.chanceHigh,
      tier: streamPick.tier,
      reasons: streamPick.reasons,
      yStart: streamOutcome.yStart,
      status: streamOutcome.labelStatus,
      points: streamOutcome.pointsNextWeek,
    })
    .from(streamPick)
    .innerJoin(dimTeam, eq(dimTeam.teamAbbr, streamPick.team))
    .leftJoin(opp, eq(opp.teamAbbr, streamPick.nextOpponent))
    .leftJoin(
      streamOutcome,
      and(
        eq(streamOutcome.season, streamPick.season),
        eq(streamOutcome.week, streamPick.week),
        eq(streamOutcome.entityId, streamPick.entityId),
      ),
    );
}

type PickRow = Awaited<ReturnType<ReturnType<typeof picksQuery>["execute"]>>[number];

function toPick(r: PickRow): StreamPick {
  const { yStart, status, points, reasons, ...rest } = r;
  return {
    ...rest,
    reasons: reasonTexts(reasons),
    outcome: status === null ? null : { yStart, status, points },
  };
}

export type StreamListData = { header: StreamHeader; picks: StreamPick[] };

/** One streamer list (header + every pick, best first), or null when it does not exist. */
async function getStreamListRaw(season: number, week: number, position: string, kind: "live" | "backtest"): Promise<StreamListData | null> {
  const headers = await db()
    .select(headerColumns)
    .from(streamList)
    .innerJoin(modelVersions, eq(modelVersions.modelVersion, streamList.modelVersion))
    .where(and(eq(streamList.season, season), eq(streamList.week, week), eq(streamList.position, position), eq(streamList.kind, kind)));
  if (!headers[0]) return null;
  const rows = await picksQuery()
    .where(and(eq(streamPick.season, season), eq(streamPick.week, week), eq(streamPick.position, position), eq(streamPick.kind, kind)))
    .orderBy(asc(streamPick.rank));
  return { header: toHeader(headers[0]), picks: rows.map(toPick) };
}
const getStreamListCached = cached("stream.getStreamList", getStreamListRaw);
/** One streamer list; while the site is public without the reasons that quote FantasyPros' ranks
 *  (lib/third-party.ts), filtered after the data cache so one cache serves both modes. */
export async function getStreamList(...args: Parameters<typeof getStreamListRaw>): Promise<StreamListData | null> {
  const l = await getStreamListCached(...args);
  return l && gatePicks(l);
}

function gatePicks(l: StreamListData): StreamListData {
  const show = showThirdPartyRanks();
  return show ? l : { ...l, picks: l.picks.map((p) => ({ ...p, reasons: shownReasons(p.reasons, show) })) };
}

/** The newest live week's K and DST lists (the home page's Streamers card), or null. */
async function getLatestStreamLiveRaw(): Promise<{ week: { season: number; week: number }; lists: StreamListData[] } | null> {
  const newest = await db()
    .select({ season: streamList.season, week: streamList.week })
    .from(streamList)
    .where(eq(streamList.kind, "live"))
    .orderBy(desc(streamList.season), desc(streamList.week))
    .limit(1);
  const wk = newest[0];
  if (!wk) return null;
  const headers = await db()
    .select(headerColumns)
    .from(streamList)
    .innerJoin(modelVersions, eq(modelVersions.modelVersion, streamList.modelVersion))
    .where(and(eq(streamList.season, wk.season), eq(streamList.week, wk.week), eq(streamList.kind, "live")));
  const rows = await picksQuery()
    .where(and(eq(streamPick.season, wk.season), eq(streamPick.week, wk.week), eq(streamPick.kind, "live")))
    .orderBy(asc(streamPick.position), asc(streamPick.rank));
  const lists = headers.map((h) => ({ header: toHeader(h), picks: rows.filter((r) => r.position === h.position).map(toPick) }));
  return { week: wk, lists };
}
const getLatestStreamLiveCached = cached("stream.getLatestStreamLive", getLatestStreamLiveRaw);
/** The newest live week's K and DST lists (gated like getStreamList). */
export async function getLatestStreamLive(): Promise<{ week: { season: number; week: number }; lists: StreamListData[] } | null> {
  const l = await getLatestStreamLiveCached();
  return l && { ...l, lists: l.lists.map(gatePicks) };
}

/** Every row of stream_track_record except the per-season ones, in the CSV's order. */
async function getStreamTrackRaw(): Promise<StreamTrackRow[]> {
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
    .where(sql`${streamTrackRecord.scope} <> 'season'`)
    .orderBy(asc(streamTrackRecord.line));
}
export const getStreamTrack = cached("stream.getStreamTrack", getStreamTrackRaw);

/** The published method per position: the model of the newest list (live first). */
async function getStreamModelsRaw(): Promise<Record<string, string>> {
  const rows = await db()
    .select({ position: streamList.position, model: modelVersions.model })
    .from(streamList)
    .innerJoin(modelVersions, eq(modelVersions.modelVersion, streamList.modelVersion))
    .orderBy(desc(streamList.season), desc(streamList.week), desc(sql`${streamList.kind} = 'live'`));
  const out: Record<string, string> = {};
  for (const r of rows) if (!(r.position in out)) out[r.position] = r.model;
  return out;
}
export const getStreamModels = cached("stream.getStreamModels", getStreamModelsRaw);
