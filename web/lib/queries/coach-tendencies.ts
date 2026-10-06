import "server-only";
import { asc, eq, inArray } from "drizzle-orm";
import { db } from "@/db/client";
import { coachTendencyCareer, coachTendencyFantasyLink, coachTendencyPersistence, coachTendencySeason, dimCoach, siteMeta } from "@/db/schema";
import { cached } from "@/lib/cache";
import type { LinkRow, PersistenceRow, TendencyRow } from "@/lib/coach-tendencies";

// Coach tendencies' published tables (feature #10, migration 0010): coach_tendency_season,
// _career, _persistence, _fantasy_link and the coach_tendency_* keys of site_meta. Every number
// of the coach page's "How his offense plays", the /decisions league table and the methodology
// section comes from here.

export type TendencyMeta = {
  /** the newest season with rows and its last week played (site_meta) */
  season: number | null;
  throughWeek: number | null;
};

const int = (v: string | undefined): number | null => (v !== undefined && /^\d+$/.test(v) ? Number(v) : null);

async function getTendencyMetaRaw(): Promise<TendencyMeta> {
  const rows = await db()
    .select()
    .from(siteMeta)
    .where(inArray(siteMeta.key, ["coach_tendency_season", "coach_tendency_through_week"]));
  const m = new Map(rows.map((r) => [r.key, r.value]));
  return { season: int(m.get("coach_tendency_season")), throughWeek: int(m.get("coach_tendency_through_week")) };
}
export const getTendencyMeta = cached("coachTendencies.getTendencyMeta", getTendencyMetaRaw);

export type CareerRow = typeof coachTendencyCareer.$inferSelect;
export type NamedTendencyRow = TendencyRow & { name: string };

/** One coach's season rows (every team and metric) and his career rows. */
async function getCoachTendenciesRaw(coachId: string): Promise<{ seasons: TendencyRow[]; career: CareerRow[] }> {
  const [seasons, career] = await Promise.all([
    db().select().from(coachTendencySeason).where(eq(coachTendencySeason.coachId, coachId)).orderBy(asc(coachTendencySeason.season), asc(coachTendencySeason.team)),
    db().select().from(coachTendencyCareer).where(eq(coachTendencyCareer.coachId, coachId)),
  ]);
  return { seasons, career };
}
export const getCoachTendencies = cached("coachTendencies.getCoachTendencies", getCoachTendenciesRaw);

/** Every coach's rows of one season, with his name (the league table). */
async function getSeasonTendenciesRaw(season: number): Promise<NamedTendencyRow[]> {
  return db()
    .select({ ...coachTendencySeasonColumns(), name: dimCoach.name })
    .from(coachTendencySeason)
    .innerJoin(dimCoach, eq(dimCoach.coachId, coachTendencySeason.coachId))
    .where(eq(coachTendencySeason.season, season));
}
export const getSeasonTendencies = cached("coachTendencies.getSeasonTendencies", getSeasonTendenciesRaw);

function coachTendencySeasonColumns() {
  const t = coachTendencySeason;
  return { coachId: t.coachId, team: t.team, season: t.season, metric: t.metric, isCurrent: t.isCurrent, throughWeek: t.throughWeek, games: t.games, plays: t.plays, value: t.value, sample: t.sample, leagueAvg: t.leagueAvg, percentile: t.percentile };
}

/** The persistence and fantasy-link tables (small: every row). */
async function getTendencyStudiesRaw(): Promise<{ persistence: PersistenceRow[]; link: LinkRow[] }> {
  const [persistence, link] = await Promise.all([db().select().from(coachTendencyPersistence), db().select().from(coachTendencyFantasyLink)]);
  return { persistence, link };
}
export const getTendencyStudies = cached("coachTendencies.getTendencyStudies", getTendencyStudiesRaw);
