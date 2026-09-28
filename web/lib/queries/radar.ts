import "server-only";
import { and, asc, count, desc, eq, isNotNull, lt, lte, max, min, sql } from "drizzle-orm";
import { db } from "@/db/client";
import { dimPlayer, dimTeam, modelVersions, radarList, radarOutcome, radarPick } from "@/db/schema";
import { cached } from "@/lib/cache";
import type { Position } from "@/lib/method";
import type { ListKey } from "@/lib/params";
import type { RankCount } from "@/lib/buckets";

export type ListIndexRow = ListKey & { positions: number };

/** Every published (season, week, kind), newest first; live before reconstructed. */
async function getListIndexRaw(): Promise<ListIndexRow[]> {
  const rows = await db()
    .select({
      season: radarList.season,
      week: radarList.week,
      kind: radarList.kind,
      positions: count(),
    })
    .from(radarList)
    .groupBy(radarList.season, radarList.week, radarList.kind)
    .orderBy(desc(radarList.season), desc(radarList.week), desc(sql`${radarList.kind} = 'live'`));
  return rows.map((r) => ({ ...r, kind: r.kind === "live" ? "live" : "backtest" }));
}
export const getListIndex = cached("radar.getListIndex", getListIndexRaw);

export type ListHeader = {
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

export type PickOutcome = {
  yHit: boolean | null;
  ySustained: boolean | null;
  status: string;
  windowWeeks: number[] | null;
  windowRanks: (number | null)[] | null;
  windowPoints: (number | null)[] | null;
};

export type Pick = {
  rank: number;
  gsisId: string;
  name: string;
  team: string;
  teamName: string;
  chance: number | null;
  chanceLow: number | null;
  chanceHigh: number | null;
  tier: string | null;
  reasons: string[];
  outcome: PickOutcome | null;
};

const iso = (d: Date | string) => new Date(d).toISOString();

/** radar_pick.reasons is a JSON array of sentences (a stored object's `text` also counts). */
export function reasonTexts(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((r) =>
      typeof r === "string"
        ? r
        : r && typeof r === "object" && typeof (r as { text?: unknown }).text === "string"
          ? (r as { text: string }).text
          : null,
    )
    .filter((t): t is string => !!t && t.trim().length > 0);
}

const pickColumns = {
  rank: radarPick.rank,
  gsisId: radarPick.gsisId,
  name: dimPlayer.displayName,
  team: radarPick.team,
  teamName: dimTeam.teamName,
  chance: radarPick.chance,
  chanceLow: radarPick.chanceLow,
  chanceHigh: radarPick.chanceHigh,
  tier: radarPick.tier,
  reasons: radarPick.reasons,
  position: radarPick.position,
  yHit: radarOutcome.yHit,
  ySustained: radarOutcome.ySustained,
  status: radarOutcome.labelStatus,
  windowWeeks: radarOutcome.windowWeeks,
  windowRanks: radarOutcome.windowRanks,
  windowPoints: radarOutcome.windowPoints,
};

type PickRow = {
  rank: number;
  gsisId: string;
  name: string;
  team: string;
  teamName: string;
  chance: number | null;
  chanceLow: number | null;
  chanceHigh: number | null;
  tier: string | null;
  reasons: unknown;
  position: string;
  yHit: boolean | null;
  ySustained: boolean | null;
  status: string | null;
  windowWeeks: number[] | null;
  windowRanks: number[] | null;
  windowPoints: number[] | null;
};

function toPick(r: PickRow): Pick {
  return {
    rank: r.rank,
    gsisId: r.gsisId,
    name: r.name,
    team: r.team,
    teamName: r.teamName,
    chance: r.chance,
    chanceLow: r.chanceLow,
    chanceHigh: r.chanceHigh,
    tier: r.tier,
    reasons: reasonTexts(r.reasons),
    outcome:
      r.status === null
        ? null
        : {
            yHit: r.yHit,
            ySustained: r.ySustained,
            status: r.status,
            windowWeeks: r.windowWeeks,
            windowRanks: r.windowRanks,
            windowPoints: r.windowPoints,
          },
  };
}

function picksQuery() {
  return db()
    .select(pickColumns)
    .from(radarPick)
    .innerJoin(dimPlayer, eq(dimPlayer.gsisId, radarPick.gsisId))
    .innerJoin(dimTeam, eq(dimTeam.teamAbbr, radarPick.team))
    .leftJoin(
      radarOutcome,
      and(
        eq(radarOutcome.season, radarPick.season),
        eq(radarOutcome.week, radarPick.week),
        eq(radarOutcome.gsisId, radarPick.gsisId),
      ),
    );
}

const headerColumns = {
  season: radarList.season,
  week: radarList.week,
  position: radarList.position,
  kind: radarList.kind,
  asOf: radarList.asOf,
  generatedAt: radarList.generatedAt,
  incomplete: radarList.incomplete,
  nPool: radarList.nPool,
  note: radarList.note,
  modelVersion: radarList.modelVersion,
  model: modelVersions.model,
  trainingSeasons: modelVersions.trainingSeasons,
};

function toHeader(h: {
  season: number;
  week: number;
  position: string;
  kind: string;
  asOf: Date;
  generatedAt: Date;
  incomplete: boolean;
  nPool: number;
  note: string | null;
  modelVersion: string;
  model: string;
  trainingSeasons: number[];
}): ListHeader {
  return {
    ...h,
    kind: h.kind === "live" ? "live" : "backtest",
    asOf: iso(h.asOf),
    generatedAt: iso(h.generatedAt),
    note: h.note && h.note.trim() ? h.note : null,
  };
}

/** One weekly list (header + its picks, best first), or null when it does not exist. */
async function getListRaw(
  season: number,
  week: number,
  position: Position,
  kind: "live" | "backtest",
): Promise<{ header: ListHeader; picks: Pick[] } | null> {
  const key = and(
    eq(radarList.season, season),
    eq(radarList.week, week),
    eq(radarList.position, position),
    eq(radarList.kind, kind),
  );
  const headers = await db()
    .select(headerColumns)
    .from(radarList)
    .innerJoin(modelVersions, eq(modelVersions.modelVersion, radarList.modelVersion))
    .where(key);
  if (!headers[0]) return null;
  const rows = await picksQuery()
    .where(
      and(
        eq(radarPick.season, season),
        eq(radarPick.week, week),
        eq(radarPick.position, position),
        eq(radarPick.kind, kind),
      ),
    )
    .orderBy(asc(radarPick.rank));
  return { header: toHeader(headers[0]), picks: rows.map(toPick) };
}
export const getList = cached("radar.getList", getListRaw);

export type TopList = { header: ListHeader; picks: Pick[] };

/** The newest live week's lists, each cut to its top `n` (for the home page). */
async function getLatestLiveTopRaw(n: number): Promise<{ week: { season: number; week: number }; lists: TopList[] } | null> {
  const newest = await db()
    .select({ season: radarList.season, week: radarList.week })
    .from(radarList)
    .where(eq(radarList.kind, "live"))
    .orderBy(desc(radarList.season), desc(radarList.week))
    .limit(1);
  const wk = newest[0];
  if (!wk) return null;
  const headers = await db()
    .select(headerColumns)
    .from(radarList)
    .innerJoin(modelVersions, eq(modelVersions.modelVersion, radarList.modelVersion))
    .where(and(eq(radarList.season, wk.season), eq(radarList.week, wk.week), eq(radarList.kind, "live")));
  const rows = await picksQuery()
    .where(
      and(
        eq(radarPick.season, wk.season),
        eq(radarPick.week, wk.week),
        eq(radarPick.kind, "live"),
        lte(radarPick.rank, n),
      ),
    )
    .orderBy(asc(radarPick.position), asc(radarPick.rank));
  const lists = headers.map((h) => ({
    header: toHeader(h),
    picks: rows.filter((r) => r.position === h.position).map(toPick),
  }));
  return { week: wk, lists };
}
export const getLatestLiveTop = cached("radar.getLatestLiveTop", getLatestLiveTopRaw);

export type BucketCounts = {
  counts: RankCount[];
  seasonFrom: number | null;
  seasonTo: number | null;
  lists: number;
};

/** Picks and hits per rank in the reconstructed lists of `position` from seasons before
 *  `beforeSeason`, counting only final outcomes (a pending window is not a miss). */
async function getBucketCountsRaw(position: Position, beforeSeason: number): Promise<BucketCounts> {
  const where = and(
    eq(radarPick.kind, "backtest"),
    eq(radarPick.position, position),
    lt(radarPick.season, beforeSeason),
    eq(radarOutcome.labelStatus, "final"),
    isNotNull(radarOutcome.yHit),
  );
  const join = and(
    eq(radarOutcome.season, radarPick.season),
    eq(radarOutcome.week, radarPick.week),
    eq(radarOutcome.gsisId, radarPick.gsisId),
  );
  const rows = await db()
    .select({
      rank: radarPick.rank,
      picks: count(),
      hits: sql<number>`count(*) filter (where ${radarOutcome.yHit})`.mapWith(Number),
    })
    .from(radarPick)
    .innerJoin(radarOutcome, join)
    .where(where)
    .groupBy(radarPick.rank)
    .orderBy(asc(radarPick.rank));
  const span = await db()
    .select({
      from: min(radarPick.season),
      to: max(radarPick.season),
      lists: sql<number>`count(distinct (${radarPick.season}, ${radarPick.week}))`.mapWith(Number),
    })
    .from(radarPick)
    .innerJoin(radarOutcome, join)
    .where(where);
  return {
    counts: rows,
    seasonFrom: span[0]?.from ?? null,
    seasonTo: span[0]?.to ?? null,
    lists: span[0]?.lists ?? 0,
  };
}
export const getBucketCounts = cached("radar.getBucketCounts", getBucketCountsRaw);

export type HistoryRow = {
  season: number;
  week: number;
  position: string;
  kind: "live" | "backtest";
  rank: number;
  chance: number | null;
  chanceLow: number | null;
  chanceHigh: number | null;
  tier: string | null;
  outcome: PickOutcome | null;
};

/** Every list a player appears on, newest first. */
async function getPlayerHistoryRaw(gsisId: string): Promise<HistoryRow[]> {
  const rows = await db()
    .select({
      season: radarPick.season,
      week: radarPick.week,
      position: radarPick.position,
      kind: radarPick.kind,
      rank: radarPick.rank,
      chance: radarPick.chance,
      chanceLow: radarPick.chanceLow,
      chanceHigh: radarPick.chanceHigh,
      tier: radarPick.tier,
      yHit: radarOutcome.yHit,
      ySustained: radarOutcome.ySustained,
      status: radarOutcome.labelStatus,
      windowWeeks: radarOutcome.windowWeeks,
      windowRanks: radarOutcome.windowRanks,
      windowPoints: radarOutcome.windowPoints,
    })
    .from(radarPick)
    .leftJoin(
      radarOutcome,
      and(
        eq(radarOutcome.season, radarPick.season),
        eq(radarOutcome.week, radarPick.week),
        eq(radarOutcome.gsisId, radarPick.gsisId),
      ),
    )
    .where(eq(radarPick.gsisId, gsisId))
    .orderBy(desc(radarPick.season), desc(radarPick.week), asc(radarPick.position), asc(radarPick.kind));
  return rows.map((r) => ({
    season: r.season,
    week: r.week,
    position: r.position,
    kind: r.kind === "live" ? "live" : "backtest",
    rank: r.rank,
    chance: r.chance,
    chanceLow: r.chanceLow,
    chanceHigh: r.chanceHigh,
    tier: r.tier,
    outcome:
      r.status === null
        ? null
        : {
            yHit: r.yHit,
            ySustained: r.ySustained,
            status: r.status,
            windowWeeks: r.windowWeeks,
            windowRanks: r.windowRanks,
            windowPoints: r.windowPoints,
          },
  }));
}
export const getPlayerHistory = cached("radar.getPlayerHistory", getPlayerHistoryRaw);

/** The model behind the newest published list (e.g. "logit"): the Radar's own model, whose
 *  rows the track record reports. */
async function getRadarModelRaw(): Promise<string | null> {
  const rows = await db()
    .select({ model: modelVersions.model })
    .from(radarList)
    .innerJoin(modelVersions, eq(modelVersions.modelVersion, radarList.modelVersion))
    .orderBy(desc(radarList.season), desc(radarList.week), desc(sql`${radarList.kind} = 'live'`))
    .limit(1);
  return rows[0]?.model ?? null;
}
export const getRadarModel = cached("radar.getRadarModel", getRadarModelRaw);
