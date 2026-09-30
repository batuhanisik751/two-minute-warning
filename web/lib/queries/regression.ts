import "server-only";
import { and, asc, desc, eq, sql } from "drizzle-orm";
import { db } from "@/db/client";
import { dimPlayer, dimTeam, modelVersions, regressionList, regressionOutcome, regressionRow, regressionStability, regressionTrackRecord } from "@/db/schema";
import { cached } from "@/lib/cache";
import type { ListKey } from "@/lib/params";
import type { StabilityRow } from "@/lib/stability";
import { dropTags, type RegressionRow, type RegressionTrackRow } from "@/lib/regression";

// Regression Watch's published lists (regression_list / regression_row / regression_outcome),
// its frozen parameters (model_versions.params) and its track record (regression_track_record).

const iso = (d: Date | string) => new Date(d).toISOString();
const kindOf = (k: string): "live" | "backtest" => (k === "live" ? "live" : "backtest");

/** Every published (season, week, kind), newest first, live first. */
async function getRegressionIndexRaw(): Promise<(ListKey & { positions: number })[]> {
  const rows = await db()
    .select({ season: regressionList.season, week: regressionList.week, kind: regressionList.kind })
    .from(regressionList)
    .orderBy(desc(regressionList.season), desc(regressionList.week), desc(sql`${regressionList.kind} = 'live'`));
  return rows.map((r) => ({ ...r, kind: kindOf(r.kind), positions: 1 }));
}
export const getRegressionIndex = cached("regression.getRegressionIndex", getRegressionIndexRaw);

export type RegressionHeader = {
  season: number;
  week: number;
  kind: "live" | "backtest";
  asOf: string;
  generatedAt: string;
  incomplete: boolean;
  nUniverse: number;
  note: string | null;
  paramsVersion: string;
  model: string;
  params: unknown;
};

const rowColumns = {
  gsisId: regressionRow.gsisId,
  name: dimPlayer.displayName,
  position: regressionRow.position,
  team: regressionRow.team,
  teamName: dimTeam.teamName,
  teamColor: dimTeam.color,
  teamColor2: dimTeam.color2,
  games: regressionRow.games,
  ppg: regressionRow.ppg,
  ppgNg: regressionRow.ppgNg,
  xfpPg: regressionRow.xfpPg,
  xfpPgNg: regressionRow.xfpPgNg,
  fpoePg: regressionRow.fpoePg,
  fpoePgNg: regressionRow.fpoePgNg,
  projection: regressionRow.projection,
  shrinkage: regressionRow.shrinkage,
  tag: regressionRow.tag,
  tags: regressionRow.tags,
  tagReason: regressionRow.tagReason,
  rosPpg: regressionOutcome.rosPpg,
  rosGames: regressionOutcome.rosGames,
  status: regressionOutcome.labelStatus,
};

function rowsQuery() {
  return db()
    .select(rowColumns)
    .from(regressionRow)
    .innerJoin(dimPlayer, eq(dimPlayer.gsisId, regressionRow.gsisId))
    .innerJoin(dimTeam, eq(dimTeam.teamAbbr, regressionRow.team))
    .leftJoin(
      regressionOutcome,
      and(
        eq(regressionOutcome.season, regressionRow.season),
        eq(regressionOutcome.week, regressionRow.week),
        eq(regressionOutcome.gsisId, regressionRow.gsisId),
      ),
    );
}

type Raw = Awaited<ReturnType<ReturnType<typeof rowsQuery>["execute"]>>[number];

/** A row as the site shows it: tags the site dropped (lib/regression.ts DROPPED_TAGS, e.g.
 *  legit on the frozen live 2026-W03 list) are removed here, for every page. */
function toRow(r: Raw): RegressionRow {
  const { rosPpg, rosGames, status, ...rest } = r;
  return dropTags({ ...rest, outcome: status === null ? null : { rosPpg, rosGames, status } });
}

const headerColumns = {
  season: regressionList.season,
  week: regressionList.week,
  kind: regressionList.kind,
  asOf: regressionList.asOf,
  generatedAt: regressionList.generatedAt,
  incomplete: regressionList.incomplete,
  nUniverse: regressionList.nUniverse,
  note: regressionList.note,
  paramsVersion: regressionList.paramsVersion,
  model: modelVersions.model,
  params: modelVersions.params,
};

type HeaderRaw = Awaited<ReturnType<ReturnType<typeof headersQuery>["execute"]>>[number];

function headersQuery() {
  return db()
    .select(headerColumns)
    .from(regressionList)
    .innerJoin(modelVersions, eq(modelVersions.modelVersion, regressionList.paramsVersion));
}

function toHeader(h: HeaderRaw): RegressionHeader {
  return {
    ...h,
    kind: kindOf(h.kind),
    asOf: iso(h.asOf),
    generatedAt: iso(h.generatedAt),
    note: h.note && h.note.trim() ? h.note : null,
  };
}

export type RegressionListData = { header: RegressionHeader; rows: RegressionRow[] };

/** One week's list (every universe player, by position then projection), or null. */
async function getRegressionListRaw(season: number, week: number, kind: "live" | "backtest"): Promise<RegressionListData | null> {
  const key = and(eq(regressionList.season, season), eq(regressionList.week, week), eq(regressionList.kind, kind));
  const headers = await headersQuery().where(key);
  if (!headers[0]) return null;
  const rows = await rowsQuery()
    .where(and(eq(regressionRow.season, season), eq(regressionRow.week, week), eq(regressionRow.kind, kind)))
    .orderBy(asc(regressionRow.position), desc(regressionRow.projection), asc(regressionRow.gsisId));
  return { header: toHeader(headers[0]), rows: rows.map(toRow) };
}
export const getRegressionList = cached("regression.getRegressionList", getRegressionListRaw);

/** The newest live list (the home page's Regression flags card), or null. */
async function getLatestRegressionLiveRaw(): Promise<RegressionListData | null> {
  const newest = await db()
    .select({ season: regressionList.season, week: regressionList.week })
    .from(regressionList)
    .where(eq(regressionList.kind, "live"))
    .orderBy(desc(regressionList.season), desc(regressionList.week))
    .limit(1);
  return newest[0] ? getRegressionListRaw(newest[0].season, newest[0].week, "live") : null;
}
export const getLatestRegressionLive = cached("regression.getLatestRegressionLive", getLatestRegressionLiveRaw);

/** Every row of regression_track_record, in the CSV's order. */
async function getRegressionTrackRaw(): Promise<RegressionTrackRow[]> {
  return db()
    .select({
      section: regressionTrackRecord.section,
      weeks: regressionTrackRecord.weeks,
      position: regressionTrackRecord.position,
      method: regressionTrackRecord.method,
      metric: regressionTrackRecord.metric,
      rowGroup: regressionTrackRecord.rowGroup,
      season: regressionTrackRecord.season,
      value: regressionTrackRecord.value,
      lo: regressionTrackRecord.lo,
      hi: regressionTrackRecord.hi,
      n: regressionTrackRecord.n,
      nSeasons: regressionTrackRecord.nSeasons,
      perAsof: regressionTrackRecord.perAsof,
      notGraded: regressionTrackRecord.notGraded,
    })
    .from(regressionTrackRecord)
    .orderBy(asc(regressionTrackRecord.line));
}
export const getRegressionTrack = cached("regression.getRegressionTrack", getRegressionTrackRaw);

/** The parameters of the newest list (live first): the frozen shrinkage table the site shows. */
async function getRegressionParamsRaw(): Promise<{ version: string; model: string; params: unknown; weeks: number[] } | null> {
  const rows = await headersQuery()
    .orderBy(desc(regressionList.season), desc(regressionList.week), desc(sql`${regressionList.kind} = 'live'`))
    .limit(1);
  const weeks = await db()
    .selectDistinct({ week: regressionList.week })
    .from(regressionList)
    .where(eq(regressionList.kind, "backtest"))
    .orderBy(asc(regressionList.week));
  const h = rows[0];
  return h ? { version: h.paramsVersion, model: h.model, params: h.params, weeks: weeks.map((w) => w.week) } : null;
}
export const getRegressionParams = cached("regression.getRegressionParams", getRegressionParamsRaw);

export type RegressionHistoryRow = RegressionRow & { season: number; week: number; kind: "live" | "backtest" };

/** A player's rows on every published list, newest first. */
async function getPlayerRegressionHistoryRaw(gsisId: string): Promise<RegressionHistoryRow[]> {
  const rows = await db()
    .select({ ...rowColumns, season: regressionRow.season, week: regressionRow.week, kind: regressionRow.kind })
    .from(regressionRow)
    .innerJoin(dimPlayer, eq(dimPlayer.gsisId, regressionRow.gsisId))
    .innerJoin(dimTeam, eq(dimTeam.teamAbbr, regressionRow.team))
    .leftJoin(
      regressionOutcome,
      and(eq(regressionOutcome.season, regressionRow.season), eq(regressionOutcome.week, regressionRow.week), eq(regressionOutcome.gsisId, regressionRow.gsisId)),
    )
    .where(eq(regressionRow.gsisId, gsisId))
    .orderBy(desc(regressionRow.season), asc(regressionRow.week), desc(sql`${regressionRow.kind} = 'live'`));
  return rows.map(({ season, week, kind, ...r }) => ({ ...toRow(r), season, week, kind: kindOf(kind) }));
}
export const getPlayerRegressionHistory = cached("regression.getPlayerRegressionHistory", getPlayerRegressionHistoryRaw);

/** Every row of regression_stability (the stability study, reports/regression_watch/
 *  stability.csv), in the CSV's order: /methodology shows its split-half and r(g) tables. */
async function getRegressionStabilityRaw(): Promise<StabilityRow[]> {
  return db()
    .select({
      section: regressionStability.section,
      seasons: regressionStability.seasons,
      split: regressionStability.split,
      position: regressionStability.position,
      metric: regressionStability.metric,
      g: regressionStability.g,
      n: regressionStability.n,
      value: regressionStability.value,
      lo: regressionStability.lo,
      hi: regressionStability.hi,
      varSignal: regressionStability.varSignal,
      varNoise: regressionStability.varNoise,
      priorMean: regressionStability.priorMean,
    })
    .from(regressionStability)
    .orderBy(asc(regressionStability.line));
}
export const getRegressionStability = cached("regression.getRegressionStability", getRegressionStabilityRaw);
