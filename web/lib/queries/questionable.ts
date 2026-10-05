import "server-only";
import { and, asc, desc, eq, inArray, max } from "drizzle-orm";
import { db } from "@/db/client";
import { dimPlayer, modelVersions, questionableBacktest, questionableCalibration, questionableHistory, questionableList, questionableLive, questionableRow, siteMeta } from "@/db/schema";
import { cached } from "@/lib/cache";
import type { QBacktestRow, QCalibrationRow, QHistoryRow, QLiveRow, QRow, QSnapshot, QWeek } from "@/lib/questionable";

// The Questionable list's published tables (feature #1, migration 0007): questionable_list,
// questionable_row (append-only snapshots), questionable_history, questionable_backtest,
// questionable_calibration, questionable_live and the questionable_* keys of site_meta. Every
// number on /questionable, its methodology and track-record sections and the player badge comes
// from here.

const iso = (v: string | Date) => new Date(v).toISOString();

/** site_meta questionable_season / questionable_week: the week whose games are next when the
 *  data was published (empty outside the season's weeks: null). */
async function getQuestionableWeekRaw(): Promise<QWeek | null> {
  const rows = await db()
    .select()
    .from(siteMeta)
    .where(inArray(siteMeta.key, ["questionable_season", "questionable_week"]));
  const m = new Map(rows.map((r) => [r.key, r.value]));
  const s = m.get("questionable_season");
  const w = m.get("questionable_week");
  return s && w && /^\d+$/.test(s) && /^\d+$/.test(w) ? { season: Number(s), week: Number(w) } : null;
}
export const getQuestionableWeek = cached("questionable.getQuestionableWeek", getQuestionableWeekRaw);

/** Every week with a published snapshot, newest first, with its snapshot count. */
async function getQuestionableIndexRaw(): Promise<(QWeek & { snapshots: number })[]> {
  const rows = await db()
    .select({ season: questionableList.season, week: questionableList.week, asOf: questionableList.asOf })
    .from(questionableList)
    .orderBy(desc(questionableList.season), desc(questionableList.week));
  const out: (QWeek & { snapshots: number })[] = [];
  for (const r of rows) {
    const last = out[out.length - 1];
    if (last && last.season === r.season && last.week === r.week) last.snapshots += 1;
    else out.push({ season: r.season, week: r.week, snapshots: 1 });
  }
  return out;
}
export const getQuestionableIndex = cached("questionable.getQuestionableIndex", getQuestionableIndexRaw);

/** The newest snapshot of a week (its header and players), or null when the week has none. */
async function getQuestionableSnapshotRaw(season: number, week: number): Promise<{ header: QSnapshot; rows: QRow[] } | null> {
  const newest = await db()
    .select({ at: max(questionableList.asOf) })
    .from(questionableList)
    .where(and(eq(questionableList.season, season), eq(questionableList.week, week)));
  const at = newest[0]?.at;
  if (!at) return null;
  const key = and(eq(questionableList.season, season), eq(questionableList.week, week), eq(questionableList.asOf, at));
  const h = (await db().select().from(questionableList).where(key).limit(1))[0];
  if (!h) return null;
  const rows = await db()
    .select({ r: questionableRow, name: dimPlayer.displayName })
    .from(questionableRow)
    .leftJoin(dimPlayer, eq(dimPlayer.gsisId, questionableRow.gsisId))
    .where(and(eq(questionableRow.season, season), eq(questionableRow.week, week), eq(questionableRow.asOf, at)))
    .orderBy(asc(questionableRow.kickoff), desc(questionableRow.playChance), asc(questionableRow.gsisId));
  const header: QSnapshot = { season, week, asOf: iso(h.asOf), generatedAt: iso(h.generatedAt), modelVersion: h.modelVersion, nPlayers: h.nPlayers, source: h.source };
  return {
    header,
    rows: rows.map(({ r, name }) => ({
      gsisId: r.gsisId, name: name ?? r.gsisId, position: r.position, team: r.team, opponent: r.opponent, gameId: r.gameId,
      kickoff: iso(r.kickoff), reportStatus: r.reportStatus, practiceStatus: r.practiceStatus, practice: r.practice,
      missedPrev: r.missedPrev, playChance: r.playChance, playsN: r.playsN, playsMedian: r.playsMedian,
      playsDudRate: r.playsDudRate, healthyMedian: r.healthyMedian, healthyDudRate: r.healthyDudRate,
      seasonPpg: r.seasonPpg, seasonGames: r.seasonGames,
    })),
  };
}
export const getQuestionableSnapshot = cached("questionable.getQuestionableSnapshot", getQuestionableSnapshotRaw);

export type QuestionableTables = { history: QHistoryRow[]; backtest: QBacktestRow[]; calibration: QCalibrationRow[]; live: QLiveRow[] };

/** The replaced tables: the 2016-2025 history, the pinned table's backtest and calibration and
 *  the live record (each empty until the first publish with the module). */
async function getQuestionableTablesRaw(): Promise<QuestionableTables> {
  const [history, backtest, calibration, live] = await Promise.all([
    db().select().from(questionableHistory),
    db().select().from(questionableBacktest).orderBy(asc(questionableBacktest.grouping), asc(questionableBacktest.season)),
    db().select().from(questionableCalibration).orderBy(asc(questionableCalibration.line)),
    db().select().from(questionableLive).orderBy(desc(questionableLive.season)),
  ]);
  return { history, backtest, calibration, live };
}
export const getQuestionableTables = cached("questionable.getQuestionableTables", getQuestionableTablesRaw);

/** The player's row on the newest snapshot of the week whose games are next, or null (the
 *  player page's badge). */
async function getPlayerQuestionableRaw(gsisId: string): Promise<(QWeek & { reportStatus: string; playChance: number }) | null> {
  const wk = await getQuestionableWeek();
  if (!wk) return null;
  const snap = await getQuestionableSnapshot(wk.season, wk.week);
  const row = snap?.rows.find((r) => r.gsisId === gsisId);
  return row ? { ...wk, reportStatus: row.reportStatus, playChance: row.playChance } : null;
}
export const getPlayerQuestionable = cached("questionable.getPlayerQuestionable", getPlayerQuestionableRaw);

export type QuestionableModel = {
  modelVersion: string;
  trainingSeasons: number[];
  testSeason: number | null;
  featureList: string[];
  params: { grouping?: string; pseudo_count?: number; check_season?: number; plays_rules?: { min_prior_games?: number; min_prior_ppg?: number; dud?: number; min_bucket_n?: number } };
};

/** The pinned lookup table's model_versions row (module 'questionable'), newest season first. */
async function getQuestionableModelRaw(): Promise<QuestionableModel | null> {
  const rows = await db().select().from(modelVersions).where(eq(modelVersions.module, "questionable")).orderBy(desc(modelVersions.testSeason)).limit(1);
  const r = rows[0];
  if (!r) return null;
  return {
    modelVersion: r.modelVersion,
    trainingSeasons: r.trainingSeasons ?? [],
    testSeason: r.testSeason,
    featureList: r.featureList ?? [],
    params: (r.params ?? {}) as QuestionableModel["params"],
  };
}
export const getQuestionableModel = cached("questionable.getQuestionableModel", getQuestionableModelRaw);
