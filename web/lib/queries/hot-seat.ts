import "server-only";
import { and, asc, count, desc, eq, sql, type SQL } from "drizzle-orm";
import { db } from "@/db/client";
import { dimCoach, dimTeam, hotSeatFirings, hotSeatList, hotSeatOutcome, hotSeatRow, hotSeatTrackRecord, modelVersions, siteMeta } from "@/db/schema";
import { cached } from "@/lib/cache";
import { parseDrivers, type CalCell, type Driver, type OutcomeRow, type TimelineRow } from "@/lib/hot-seat";
import { HOT_SEAT_BANDS, HOT_SEAT_PHASES } from "@/lib/method";
import type { ListKey } from "@/lib/params";

// The Hot-Seat Meter's published tables (step H4a): hot_seat_list, hot_seat_row, hot_seat_outcome,
// hot_seat_track_record, hot_seat_firings, the hot_seat model_versions rows and the hot_seat_*
// keys of site_meta. Every number on /hot-seat, the coach pages' Hot-Seat section, the home
// card and the methodology and track-record sections comes from here (or lib/method.ts).

const kindOf = (k: string): "live" | "backtest" => (k === "live" ? "live" : "backtest");
const iso = (v: string | Date) => new Date(v).toISOString();

export type HotSeatKey = ListKey & { snapshot: string };

/** Every published list, newest first; live first within a week. (season, week) never repeats
 *  across snapshots: the end-of-season snapshot takes the last week, which has no weekly list. */
async function getHotSeatIndexRaw(): Promise<(HotSeatKey & { positions: number })[]> {
  const rows = await db()
    .select({ season: hotSeatList.season, week: hotSeatList.week, kind: hotSeatList.kind, snapshot: hotSeatList.snapshot })
    .from(hotSeatList)
    .orderBy(desc(hotSeatList.season), desc(hotSeatList.week), desc(sql`${hotSeatList.kind} = 'live'`));
  return rows.map((r) => ({ ...r, kind: kindOf(r.kind), positions: 1 }));
}
export const getHotSeatIndex = cached("hotSeat.getHotSeatIndex", getHotSeatIndexRaw);

export type HotSeatHeader = HotSeatKey & {
  asOf: string;
  generatedAt: string;
  incomplete: boolean;
  nCoaches: number;
  note: string | null;
  modelVersion: string;
};

export type HotSeatEntry = {
  coachId: string;
  name: string;
  team: string;
  teamName: string | null;
  asOf: string;
  rank: number;
  probability: number;
  isInterim: boolean;
  drivers: Driver[];
  regGamesPlayed: number;
  regWins: number;
  expectedWins: number | null;
  winsVsExpected: number | null;
  pointDiffPerGame: number | null;
  tenureSeasons: number | null;
  outcome: OutcomeRow | null;
};

const outcomeOf = (r: { departed: boolean | null; censored: boolean | null; departureType: string | null; announced: string | null; labelStatus: string | null }): OutcomeRow | null =>
  r.labelStatus === null ? null : { departed: r.departed, censored: r.censored, departureType: r.departureType, announced: r.announced, labelStatus: r.labelStatus };

const outcomeColumns = {
  departed: hotSeatOutcome.departed,
  censored: hotSeatOutcome.censored,
  departureType: hotSeatOutcome.departureType,
  announced: hotSeatOutcome.announced,
  labelStatus: hotSeatOutcome.labelStatus,
};
const outcomeJoin = and(eq(hotSeatOutcome.season, hotSeatRow.season), eq(hotSeatOutcome.week, hotSeatRow.week), eq(hotSeatOutcome.coachId, hotSeatRow.coachId));

/** One list: its header and every coach, ranked, with his outcome (final or pending). */
async function getHotSeatListRaw(season: number, week: number, kind: "live" | "backtest"): Promise<{ header: HotSeatHeader; rows: HotSeatEntry[] } | null> {
  const h = await db()
    .select()
    .from(hotSeatList)
    .where(and(eq(hotSeatList.season, season), eq(hotSeatList.week, week), eq(hotSeatList.kind, kind)))
    .limit(1);
  if (!h[0]) return null;
  const l = h[0];
  const rows = await db()
    .select({
      coachId: hotSeatRow.coachId,
      name: dimCoach.name,
      team: hotSeatRow.team,
      teamName: dimTeam.teamName,
      asOf: hotSeatRow.asOf,
      rank: hotSeatRow.rank,
      probability: hotSeatRow.probability,
      isInterim: hotSeatRow.isInterim,
      drivers: hotSeatRow.drivers,
      regGamesPlayed: hotSeatRow.regGamesPlayed,
      regWins: hotSeatRow.regWins,
      expectedWins: hotSeatRow.expectedWins,
      winsVsExpected: hotSeatRow.winsVsExpected,
      pointDiffPerGame: hotSeatRow.pointDiffPerGame,
      tenureSeasons: hotSeatRow.tenureSeasons,
      ...outcomeColumns,
    })
    .from(hotSeatRow)
    .innerJoin(dimCoach, eq(dimCoach.coachId, hotSeatRow.coachId))
    .leftJoin(dimTeam, eq(dimTeam.teamAbbr, hotSeatRow.team))
    .leftJoin(hotSeatOutcome, outcomeJoin)
    .where(and(eq(hotSeatRow.season, season), eq(hotSeatRow.week, week), eq(hotSeatRow.snapshot, l.snapshot), eq(hotSeatRow.kind, kind)))
    .orderBy(asc(hotSeatRow.rank), asc(dimCoach.name));
  return {
    header: {
      season: l.season, week: l.week, snapshot: l.snapshot, kind: kindOf(l.kind), asOf: iso(l.asOf), generatedAt: iso(l.generatedAt),
      incomplete: l.incomplete, nCoaches: l.nCoaches, note: l.note, modelVersion: l.modelVersion,
    },
    rows: rows.map(({ departed, censored, departureType, announced, labelStatus, drivers, asOf, ...r }) => ({
      ...r,
      asOf: iso(asOf),
      drivers: parseDrivers(drivers),
      outcome: outcomeOf({ departed, censored, departureType, announced, labelStatus }),
    })),
  };
}
export const getHotSeatList = cached("hotSeat.getHotSeatList", getHotSeatListRaw);

/** Every coach's estimate in every list of one season (both kinds; lib/hot-seat mergeTimeline
 *  keeps the live one where a week has both). */
async function getHotSeatTimelineRaw(season: number): Promise<TimelineRow[]> {
  return db()
    .select({ coachId: hotSeatRow.coachId, week: hotSeatRow.week, snapshot: hotSeatRow.snapshot, kind: hotSeatRow.kind, probability: hotSeatRow.probability })
    .from(hotSeatRow)
    .where(eq(hotSeatRow.season, season))
    .orderBy(asc(hotSeatRow.week));
}
export const getHotSeatTimeline = cached("hotSeat.getHotSeatTimeline", getHotSeatTimelineRaw);

export type CoachHotSeatRow = HotSeatKey & { team: string; rank: number; nCoaches: number; probability: number; isInterim: boolean; outcome: OutcomeRow | null };

/** One coach's rows in every list, oldest first, with the list's size and his outcome. */
async function getCoachHotSeatRaw(coachId: string): Promise<CoachHotSeatRow[]> {
  const rows = await db()
    .select({
      season: hotSeatRow.season, week: hotSeatRow.week, snapshot: hotSeatRow.snapshot, kind: hotSeatRow.kind, team: hotSeatRow.team,
      rank: hotSeatRow.rank, nCoaches: hotSeatList.nCoaches, probability: hotSeatRow.probability, isInterim: hotSeatRow.isInterim, ...outcomeColumns,
    })
    .from(hotSeatRow)
    .innerJoin(hotSeatList, and(eq(hotSeatList.season, hotSeatRow.season), eq(hotSeatList.week, hotSeatRow.week), eq(hotSeatList.snapshot, hotSeatRow.snapshot), eq(hotSeatList.kind, hotSeatRow.kind)))
    .leftJoin(hotSeatOutcome, outcomeJoin)
    .where(eq(hotSeatRow.coachId, coachId))
    .orderBy(asc(hotSeatRow.season), asc(hotSeatRow.week));
  return rows.map(({ departed, censored, departureType, announced, labelStatus, kind, ...r }) => ({
    ...r,
    kind: kindOf(kind),
    outcome: outcomeOf({ departed, censored, departureType, announced, labelStatus }),
  }));
}
export const getCoachHotSeat = cached("hotSeat.getCoachHotSeat", getCoachHotSeatRaw);

/** The early-season check: every reconstructed row with a final outcome (interim coaches left
 *  out, as in the backtest's metrics), grouped by season phase and probability band
 *  (lib/method.ts HOT_SEAT_PHASES, HOT_SEAT_BANDS): rows, how many were let go, the average
 *  estimate and the coach-seasons behind them. An aggregation of the published rows, no table. */
async function getHotSeatCalibrationRaw(): Promise<CalCell[]> {
  const phaseCases: SQL[] = HOT_SEAT_PHASES.map((p) =>
    p.from === null
      ? sql`when ${hotSeatRow.snapshot} = 'end_of_season' then ${p.key}`
      : p.to === null
        ? sql`when ${hotSeatRow.week} >= ${p.from} then ${p.key}`
        : sql`when ${hotSeatRow.week} between ${p.from} and ${p.to} then ${p.key}`,
  );
  // the end-of-season case first: its week is the last week, which would match a weekly phase
  const eos = phaseCases.pop()!;
  const phase = sql<string>`(case ${eos} ${sql.join(phaseCases, sql` `)} end)`;
  const bandCases = HOT_SEAT_BANDS.map((lo, i) => sql`when ${hotSeatRow.probability} >= ${lo} then ${i}`).reverse();
  const band = sql<number>`(case ${sql.join(bandCases, sql` `)} end)::int`;
  const rows = await db()
    .select({
      phase,
      band,
      n: count(),
      departed: sql<number>`sum(case when ${hotSeatOutcome.departed} then 1 else 0 end)::int`,
      meanPred: sql<number>`avg(${hotSeatRow.probability})::float8`,
      coachSeasons: sql<number>`count(distinct (${hotSeatRow.season}, ${hotSeatRow.coachId}))::int`,
    })
    .from(hotSeatRow)
    .innerJoin(hotSeatOutcome, outcomeJoin)
    .where(and(eq(hotSeatRow.kind, "backtest"), eq(hotSeatOutcome.labelStatus, "final"), eq(hotSeatRow.isInterim, false), sql`${hotSeatOutcome.departed} is not null`))
    .groupBy(sql`1`, sql`2`);
  return rows.filter((r) => r.phase !== null).map((r) => ({ ...r, n: Number(r.n), departed: Number(r.departed), meanPred: Number(r.meanPred), coachSeasons: Number(r.coachSeasons), band: Number(r.band) }));
}
export const getHotSeatCalibration = cached("hotSeat.getHotSeatCalibration", getHotSeatCalibrationRaw);

export type HotSeatTrackRow = typeof hotSeatTrackRecord.$inferSelect;

/** The walk-forward backtest's metrics, row for row (reports/hot_seat/backtest_metrics.csv). */
async function getHotSeatTrackRaw(): Promise<HotSeatTrackRow[]> {
  return db().select().from(hotSeatTrackRecord).orderBy(asc(hotSeatTrackRecord.line));
}
export const getHotSeatTrack = cached("hotSeat.getHotSeatTrack", getHotSeatTrackRaw);

export type HotSeatFiringsRow = typeof hotSeatFirings.$inferSelect;

async function getHotSeatFiringsRaw(): Promise<HotSeatFiringsRow[]> {
  return db().select().from(hotSeatFirings).orderBy(desc(hotSeatFirings.season));
}
export const getHotSeatFirings = cached("hotSeat.getHotSeatFirings", getHotSeatFiringsRaw);

export type HotSeatModel = { modelVersion: string; model: string; testSeason: number | null; trainingSeasons: number[]; featureList: string[]; c: number | null; createdAt: string };

/** The newest Hot-Seat model version (the approved live model's fold) and how many backtest folds
 *  (one model version per earlier test season) were published with it. */
async function getHotSeatModelRaw(): Promise<{ live: HotSeatModel | null; folds: number }> {
  const rows = await db().select().from(modelVersions).where(eq(modelVersions.module, "hot_seat")).orderBy(desc(modelVersions.testSeason));
  const m = rows[0];
  if (!m) return { live: null, folds: 0 };
  const params = (m.params ?? {}) as Record<string, unknown>;
  return {
    live: {
      modelVersion: m.modelVersion, model: m.model, testSeason: m.testSeason, trainingSeasons: [...m.trainingSeasons].sort((a, b) => a - b),
      featureList: m.featureList, c: typeof params.C === "number" ? params.C : null, createdAt: iso(m.createdAt),
    },
    folds: rows.filter((r) => r.testSeason !== m.testSeason).length,
  };
}
export const getHotSeatModel = cached("hotSeat.getHotSeatModel", getHotSeatModelRaw);

/** site_meta's hot_seat_latest_list_* and hot_seat_latest_live_list_* (empty: none). */
async function getHotSeatMetaRaw(): Promise<{ latest: { season: number; week: number } | null; latestLive: { season: number; week: number } | null }> {
  const rows = await db().select().from(siteMeta).where(sql`${siteMeta.key} like 'hot_seat_latest_%'`);
  const m = new Map(rows.map((r) => [r.key, r.value]));
  const int = (v: string | undefined) => (v !== undefined && /^\d+$/.test(v) ? Number(v) : null);
  const pair = (s: string, w: string) => {
    const a = int(m.get(s));
    const b = int(m.get(w));
    return a !== null && b !== null ? { season: a, week: b } : null;
  };
  return { latest: pair("hot_seat_latest_list_season", "hot_seat_latest_list_week"), latestLive: pair("hot_seat_latest_live_list_season", "hot_seat_latest_live_list_week") };
}
export const getHotSeatMeta = cached("hotSeat.getHotSeatMeta", getHotSeatMetaRaw);

export type HotSeatLiveSummary = { lists: number; seasons: number[]; rows: number; final: number; letGo: number; coachSeasons: number };

/** The live lists' rows and how many of their outcomes are final (and let go): the track record's
 *  live panel. A pending outcome is never counted either way. */
async function getHotSeatLiveRaw(): Promise<HotSeatLiveSummary> {
  const lists = await db().select({ season: hotSeatList.season }).from(hotSeatList).where(eq(hotSeatList.kind, "live"));
  const r = await db()
    .select({
      rows: count(),
      final: sql<number>`sum(case when ${hotSeatOutcome.labelStatus} = 'final' and ${hotSeatOutcome.departed} is not null then 1 else 0 end)::int`,
      letGo: sql<number>`sum(case when ${hotSeatOutcome.labelStatus} = 'final' and ${hotSeatOutcome.departed} then 1 else 0 end)::int`,
      coachSeasons: sql<number>`count(distinct (${hotSeatRow.season}, ${hotSeatRow.coachId}))::int`,
    })
    .from(hotSeatRow)
    .leftJoin(hotSeatOutcome, outcomeJoin)
    .where(eq(hotSeatRow.kind, "live"));
  const x = r[0];
  return {
    lists: lists.length,
    seasons: [...new Set(lists.map((l) => l.season))].sort((a, b) => b - a),
    rows: Number(x?.rows ?? 0),
    final: Number(x?.final ?? 0),
    letGo: Number(x?.letGo ?? 0),
    coachSeasons: Number(x?.coachSeasons ?? 0),
  };
}
export const getHotSeatLive = cached("hotSeat.getHotSeatLive", getHotSeatLiveRaw);
