import "server-only";
import { and, asc, desc, eq, getTableColumns, inArray, sql, type SQL } from "drizzle-orm";
import { db } from "@/db/client";
import { coachSeason, decisionClock, decisionFourth, decisionTwoPoint, decisionsTrackRecord, dimCoach, siteMeta } from "@/db/schema";
import { cached } from "@/lib/cache";
import { optionsOf, type ClockMetric, type DecisionRow } from "@/lib/decisions";

// The Decision Report Card's published tables (step P3): dim_coach, decision_fourth,
// decision_two_point, decision_clock, coach_season, decisions_track_record, and the
// decisions_* keys of site_meta. Every number on /decisions, /coach/[id], the home page's
// coach-of-the-week card and the methodology section comes from here.

export type DecisionsMeta = {
  /** the season graded on every run (site_meta decisions_season) and its newest graded week */
  season: number | null;
  latestWeek: number | null;
  /** the frozen history's seasons ("2006-2025") and the approved grading's version */
  history: string | null;
  version: string | null;
};

const int = (v: string | undefined): number | null => (v !== undefined && /^\d+$/.test(v) ? Number(v) : null);

async function getDecisionsMetaRaw(): Promise<DecisionsMeta> {
  const rows = await db()
    .select()
    .from(siteMeta)
    .where(inArray(siteMeta.key, ["decisions_season", "decisions_latest_week", "decisions_history", "decisions_version"]));
  const m = new Map(rows.map((r) => [r.key, r.value]));
  return {
    season: int(m.get("decisions_season")),
    latestWeek: int(m.get("decisions_latest_week")),
    history: m.get("decisions_history") || null,
    version: m.get("decisions_version") || null,
  };
}
export const getDecisionsMeta = cached("decisions.getDecisionsMeta", getDecisionsMetaRaw);

/** The seasons with coach rows, newest first. */
async function getDecisionSeasonsRaw(): Promise<number[]> {
  const rows = await db().selectDistinct({ season: coachSeason.season }).from(coachSeason).orderBy(desc(coachSeason.season));
  return rows.map((r) => r.season);
}
export const getDecisionSeasons = cached("decisions.getDecisionSeasons", getDecisionSeasonsRaw);

export type CoachSeasonRow = typeof coachSeason.$inferSelect & { name: string };

const seasonColumns = { ...getTableColumns(coachSeason), name: dimCoach.name };

/** Every coach's row of one season (the leaderboard ranks them: lib/decisions.ts rankCoaches). */
async function getSeasonCoachesRaw(season: number): Promise<CoachSeasonRow[]> {
  return db()
    .select(seasonColumns)
    .from(coachSeason)
    .innerJoin(dimCoach, eq(dimCoach.coachId, coachSeason.coachId))
    .where(eq(coachSeason.season, season));
}
export const getSeasonCoaches = cached("decisions.getSeasonCoaches", getSeasonCoachesRaw);

/** One coach: his name and every season row, oldest first; NULL for an unknown id. */
async function getCoachRaw(coachId: string): Promise<{ coachId: string; name: string; seasons: CoachSeasonRow[] } | null> {
  const c = await db().select().from(dimCoach).where(eq(dimCoach.coachId, coachId)).limit(1);
  if (!c[0]) return null;
  const seasons = await db()
    .select(seasonColumns)
    .from(coachSeason)
    .innerJoin(dimCoach, eq(dimCoach.coachId, coachSeason.coachId))
    .where(eq(coachSeason.coachId, coachId))
    .orderBy(asc(coachSeason.season));
  return { coachId: c[0].coachId, name: c[0].name, seasons };
}
export const getCoach = cached("decisions.getCoach", getCoachRaw);

const ctx = <T extends typeof decisionFourth | typeof decisionTwoPoint>(t: T) => ({
  gameId: t.gameId,
  playId: t.playId,
  season: t.season,
  week: t.week,
  seasonType: t.seasonType,
  posteam: t.posteam,
  defteam: t.defteam,
  coachId: t.coachId,
  coachName: dimCoach.name,
  qtr: t.qtr,
  quarterSeconds: t.quarterSeconds,
  scoreDifferential: t.scoreDifferential,
  chosen: t.chosen,
  recommended: t.recommended,
  grade: t.grade,
  correct: t.correct,
  wpLost: t.wpLost,
});

const fourthColumns = {
  ...ctx(decisionFourth),
  ydstogo: decisionFourth.ydstogo,
  yardline100: decisionFourth.yardline100,
  wpGo: decisionFourth.wpGo,
  wpFg: decisionFourth.wpFg,
  wpPunt: decisionFourth.wpPunt,
  pConvert: decisionFourth.pConvert,
  pMake: decisionFourth.pMake,
  outcome: decisionFourth.outcome,
};
const tryColumns = { ...ctx(decisionTwoPoint), wpKick: decisionTwoPoint.wpKick, wpTwoPoint: decisionTwoPoint.wpTwoPoint, result: decisionTwoPoint.result };

type FourthSel = Omit<DecisionRow, "kind" | "options"> & { wpGo: number; wpFg: number | null; wpPunt: number | null };
type TrySel = Omit<DecisionRow, "kind" | "options" | "ydstogo" | "yardline100" | "pConvert" | "pMake" | "outcome"> & {
  wpKick: number;
  wpTwoPoint: number;
  result: string | null;
};

function fromFourth(r: FourthSel): DecisionRow {
  const { wpGo, wpFg, wpPunt, ...rest } = r;
  return { ...rest, kind: "fourth", options: optionsOf({ go: wpGo, field_goal: wpFg, punt: wpPunt }) };
}
function fromTry(r: TrySel): DecisionRow {
  const { wpKick, wpTwoPoint, result, ...rest } = r;
  return {
    ...rest,
    kind: "two_point",
    ydstogo: null,
    yardline100: null,
    pConvert: null,
    pMake: null,
    outcome: result,
    options: optionsOf({ kick: wpKick, two_point: wpTwoPoint }),
  };
}

type Scope = { season: number | null; coachId: string | null; week: number | null };
function scoped(t: typeof decisionFourth | typeof decisionTwoPoint, s: Scope, ...more: SQL[]): SQL | undefined {
  const parts: SQL[] = [eq(t.grade, "clear"), ...more];
  if (s.season !== null) parts.push(eq(t.season, s.season));
  if (s.coachId !== null) parts.push(eq(t.coachId, s.coachId));
  if (s.week !== null) parts.push(eq(t.week, s.week));
  return and(...parts);
}
const byKey = (a: DecisionRow, b: DecisionRow) =>
  b.season - a.season || a.week - b.week || a.gameId.localeCompare(b.gameId) || a.playId - b.playId;

/** The worst clear calls (most WP lost; fourth downs and tries together) of a season, a coach or
 *  a week (NULL = any). */
async function getWorstCallsRaw(season: number | null, coachId: string | null, week: number | null, limit: number): Promise<DecisionRow[]> {
  const s = { season, coachId, week };
  const [f, t] = await Promise.all([
    db()
      .select(fourthColumns)
      .from(decisionFourth)
      .innerJoin(dimCoach, eq(dimCoach.coachId, decisionFourth.coachId))
      .where(scoped(decisionFourth, s, eq(decisionFourth.correct, false)))
      .orderBy(desc(decisionFourth.wpLost), asc(decisionFourth.gameId), asc(decisionFourth.playId))
      .limit(limit),
    db()
      .select(tryColumns)
      .from(decisionTwoPoint)
      .innerJoin(dimCoach, eq(dimCoach.coachId, decisionTwoPoint.coachId))
      .where(scoped(decisionTwoPoint, s, eq(decisionTwoPoint.correct, false)))
      .orderBy(desc(decisionTwoPoint.wpLost), asc(decisionTwoPoint.gameId), asc(decisionTwoPoint.playId))
      .limit(limit),
  ]);
  return [...f.map(fromFourth), ...t.map(fromTry)].sort((a, b) => b.wpLost - a.wpLost || byKey(a, b)).slice(0, limit);
}
export const getWorstCalls = cached("decisions.getWorstCalls", getWorstCallsRaw);

/** The best calls against convention: clear fourth downs where going for it was best and the
 *  coach went, clear tries where two was best and he went for two; the most WP gained over the
 *  best other option first. */
async function getBestCallsRaw(season: number | null, coachId: string | null, week: number | null, limit: number): Promise<(DecisionRow & { gain: number })[]> {
  const s = { season, coachId, week };
  const fGain = sql<number>`${decisionFourth.wpGo} - greatest(${decisionFourth.wpFg}, ${decisionFourth.wpPunt})`;
  const tGain = sql<number>`${decisionTwoPoint.wpTwoPoint} - ${decisionTwoPoint.wpKick}`;
  const [f, t] = await Promise.all([
    db()
      .select({ ...fourthColumns, gain: fGain })
      .from(decisionFourth)
      .innerJoin(dimCoach, eq(dimCoach.coachId, decisionFourth.coachId))
      .where(scoped(decisionFourth, s, eq(decisionFourth.chosen, "go"), eq(decisionFourth.recommended, "go"), sql`${fGain} is not null`))
      .orderBy(desc(fGain), asc(decisionFourth.gameId), asc(decisionFourth.playId))
      .limit(limit),
    db()
      .select({ ...tryColumns, gain: tGain })
      .from(decisionTwoPoint)
      .innerJoin(dimCoach, eq(dimCoach.coachId, decisionTwoPoint.coachId))
      .where(scoped(decisionTwoPoint, s, eq(decisionTwoPoint.chosen, "two_point"), eq(decisionTwoPoint.recommended, "two_point")))
      .orderBy(desc(tGain), asc(decisionTwoPoint.gameId), asc(decisionTwoPoint.playId))
      .limit(limit),
  ]);
  const rows = [
    ...f.map(({ gain, ...r }) => ({ ...fromFourth(r), gain: Number(gain) })),
    ...t.map(({ gain, ...r }) => ({ ...fromTry(r), gain: Number(gain) })),
  ];
  return rows.sort((a, b) => b.gain - a.gain || byKey(a, b)).slice(0, limit);
}
export const getBestCalls = cached("decisions.getBestCalls", getBestCallsRaw);

export type ClockCaseRow = {
  metric: ClockMetric;
  season: number;
  week: number;
  seasonType: string;
  gameId: string;
  team: string;
  opp: string;
  coachId: string;
  coachName: string;
  qtr: number;
  quarterSeconds: number;
  down: number | null;
  ydstogo: number | null;
  yardline100: number | null;
  scoreDifferential: number;
  timeouts: number;
  amount: number;
  wpLeft: number | null;
};

/** The clock-management CASES (not the candidates) of a season and/or a coach, newest first. */
async function getClockCasesRaw(season: number | null, coachId: string | null): Promise<ClockCaseRow[]> {
  const parts: SQL[] = [eq(decisionClock.isCase, true)];
  if (season !== null) parts.push(eq(decisionClock.season, season));
  if (coachId !== null) parts.push(eq(decisionClock.coachId, coachId));
  const rows = await db()
    .select({
      metric: decisionClock.metric,
      season: decisionClock.season,
      week: decisionClock.week,
      seasonType: decisionClock.seasonType,
      gameId: decisionClock.gameId,
      team: decisionClock.team,
      opp: decisionClock.opp,
      coachId: decisionClock.coachId,
      coachName: dimCoach.name,
      qtr: decisionClock.qtr,
      quarterSeconds: decisionClock.quarterSeconds,
      down: decisionClock.down,
      ydstogo: decisionClock.ydstogo,
      yardline100: decisionClock.yardline100,
      scoreDifferential: decisionClock.scoreDifferential,
      timeouts: decisionClock.timeouts,
      amount: decisionClock.amount,
      wpLeft: decisionClock.wpLeft,
    })
    .from(decisionClock)
    .innerJoin(dimCoach, eq(dimCoach.coachId, decisionClock.coachId))
    .where(and(...parts))
    .orderBy(desc(decisionClock.season), desc(decisionClock.week), asc(decisionClock.gameId), asc(decisionClock.metric));
  return rows.map((r) => ({ ...r, metric: r.metric as ClockMetric }));
}
export const getClockCases = cached("decisions.getClockCases", getClockCasesRaw);

export type DecisionsTrackRow = typeof decisionsTrackRecord.$inferSelect;

/** The Decision Report Card's track record, row for row (the methodology section picks from it:
 *  lib/decisions-track.ts). */
async function getDecisionsTrackRaw(): Promise<DecisionsTrackRow[]> {
  return db().select().from(decisionsTrackRecord).orderBy(asc(decisionsTrackRecord.source), asc(decisionsTrackRecord.line));
}
export const getDecisionsTrack = cached("decisions.getDecisionsTrack", getDecisionsTrackRaw);
