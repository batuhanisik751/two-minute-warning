import "server-only";
import { and, asc, desc, eq, inArray } from "drizzle-orm";
import { db } from "@/db/client";
import {
  modelVersions,
  playoffPlannerBacktest,
  playoffPlannerChoice,
  playoffPlannerEffects,
  playoffPlannerLateWeeks,
  playoffPlannerList,
  playoffPlannerLive,
  playoffPlannerRow,
  playoffPlannerStability,
  siteMeta,
} from "@/db/schema";
import { cached } from "@/lib/cache";
import { parseWeeks, type PPBacktestRow, type PPChoice, type PPEffect, type PPLateWeek, type PPLive, type PPParams, type PPRow, type PPSnapshot, type PPStability } from "@/lib/playoff-planner";

// The playoff planner's published tables (feature #6, migration 0009): playoff_planner_list,
// playoff_planner_row (append-only snapshots, one per (season, through_week)), the replaced
// playoff_planner_choice, _backtest, _effects, _stability, _late_weeks and _live, the pinned
// version's model_versions row and the playoff_planner_* keys of site_meta. Every number on
// /playoff-planner, its methodology and track-record sections and the player line comes from here.

const iso = (v: string | Date) => new Date(v).toISOString();

/** site_meta playoff_planner_season / playoff_planner_weeks (the pinned season and its fantasy
 *  playoff weeks); the default weeks 15-17 and no season before the first publish. */
async function getPlannerMetaRaw(): Promise<{ season: number | null; weeks: number[] }> {
  const rows = await db()
    .select()
    .from(siteMeta)
    .where(inArray(siteMeta.key, ["playoff_planner_season", "playoff_planner_weeks"]));
  const m = new Map(rows.map((r) => [r.key, r.value]));
  const s = m.get("playoff_planner_season");
  return { season: s && /^\d+$/.test(s) ? Number(s) : null, weeks: parseWeeks(m.get("playoff_planner_weeks")) };
}
export const getPlannerMeta = cached("playoffPlanner.getPlannerMeta", getPlannerMetaRaw);

/** The newest snapshot (the latest season, then its latest completed week) and its rows, or null
 *  before the first one. */
async function getPlannerSnapshotRaw(): Promise<{ header: PPSnapshot; rows: PPRow[] } | null> {
  const h = (await db().select().from(playoffPlannerList).orderBy(desc(playoffPlannerList.season), desc(playoffPlannerList.throughWeek)).limit(1))[0];
  if (!h) return null;
  const rows = await db()
    .select()
    .from(playoffPlannerRow)
    .where(and(eq(playoffPlannerRow.season, h.season), eq(playoffPlannerRow.throughWeek, h.throughWeek)))
    .orderBy(asc(playoffPlannerRow.week), asc(playoffPlannerRow.team), asc(playoffPlannerRow.position));
  const header: PPSnapshot = {
    season: h.season, throughWeek: h.throughWeek, asOf: iso(h.asOf), generatedAt: iso(h.generatedAt),
    modelVersion: h.modelVersion, nTeams: h.nTeams, nRows: h.nRows,
  };
  return {
    header,
    rows: rows.map((r) => ({
      week: r.week, team: r.team, position: r.position, opponent: r.opponent, home: r.home, candidate: r.candidate, rating: r.rating,
      ratingRank: r.ratingRank, raw: r.raw, shrunk: r.shrunk, adjusted: r.adjusted, oppGames: r.oppGames,
    })),
  };
}
export const getPlannerSnapshot = cached("playoffPlanner.getPlannerSnapshot", getPlannerSnapshotRaw);

export type PlannerTables = { choice: PPChoice[]; backtest: PPBacktestRow[]; effects: PPEffect[]; stability: PPStability[]; lateWeeks: PPLateWeek[]; live: PPLive[] };

/** The replaced tables (each empty until the first publish with the module; live: empty until
 *  the last playoff week is played). */
async function getPlannerTablesRaw(): Promise<PlannerTables> {
  const [choice, backtest, effects, stability, lateWeeks, live] = await Promise.all([
    db().select().from(playoffPlannerChoice),
    db().select().from(playoffPlannerBacktest).orderBy(asc(playoffPlannerBacktest.position), asc(playoffPlannerBacktest.candidate)),
    db().select().from(playoffPlannerEffects).orderBy(asc(playoffPlannerEffects.position), asc(playoffPlannerEffects.horizon)),
    db().select().from(playoffPlannerStability).orderBy(asc(playoffPlannerStability.position), asc(playoffPlannerStability.horizon)),
    db().select().from(playoffPlannerLateWeeks).orderBy(asc(playoffPlannerLateWeeks.era), asc(playoffPlannerLateWeeks.position), asc(playoffPlannerLateWeeks.week)),
    db().select().from(playoffPlannerLive).orderBy(desc(playoffPlannerLive.season), asc(playoffPlannerLive.position)),
  ]);
  return { choice, backtest, effects, stability, lateWeeks, live };
}
export const getPlannerTables = cached("playoffPlanner.getPlannerTables", getPlannerTablesRaw);

export type PlannerModel = { modelVersion: string; trainingSeasons: number[]; testSeason: number | null; params: PPParams };

/** The pinned spec's model_versions row (module 'playoff_planner'), newest season first. */
async function getPlannerModelRaw(): Promise<PlannerModel | null> {
  const r = (await db().select().from(modelVersions).where(eq(modelVersions.module, "playoff_planner")).orderBy(desc(modelVersions.testSeason)).limit(1))[0];
  if (!r) return null;
  return { modelVersion: r.modelVersion, trainingSeasons: r.trainingSeasons ?? [], testSeason: r.testSeason, params: (r.params ?? {}) as PPParams };
}
export const getPlannerModel = cached("playoffPlanner.getPlannerModel", getPlannerModelRaw);

/** A player's playoff weeks (the player page's line): his team's rows at his position in the
 *  newest snapshot, only when the position is rated; null otherwise. */
async function getPlayerPlayoffWeeksRaw(team: string, position: string): Promise<{ header: PPSnapshot; rows: PPRow[] } | null> {
  const snap = await getPlannerSnapshot();
  if (!snap) return null;
  const rows = snap.rows.filter((r) => r.team === team && r.position === position);
  if (!rows.some((r) => r.candidate !== "none")) return null;
  return { header: snap.header, rows };
}
export const getPlayerPlayoffWeeks = cached("playoffPlanner.getPlayerPlayoffWeeks", getPlayerPlayoffWeeksRaw);
