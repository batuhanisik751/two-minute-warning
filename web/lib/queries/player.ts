import "server-only";
import { asc, desc, eq, and } from "drizzle-orm";
import { db } from "@/db/client";
import { dimPlayer, dimTeam, playerWeekSummary } from "@/db/schema";
import { cached } from "@/lib/cache";

export type PlayerHeader = {
  gsisId: string;
  name: string;
  position: string | null;
  team: string | null;
  teamName: string | null;
  draftYear: number | null;
  draftRound: number | null;
  draftPick: number | null;
  rookieSeason: number | null;
};

async function getPlayerRaw(gsisId: string): Promise<PlayerHeader | null> {
  const rows = await db()
    .select({
      gsisId: dimPlayer.gsisId,
      name: dimPlayer.displayName,
      position: dimPlayer.position,
      team: dimPlayer.team,
      teamName: dimTeam.teamName,
      draftYear: dimPlayer.draftYear,
      draftRound: dimPlayer.draftRound,
      draftPick: dimPlayer.draftPick,
      rookieSeason: dimPlayer.rookieSeason,
    })
    .from(dimPlayer)
    .leftJoin(dimTeam, eq(dimTeam.teamAbbr, dimPlayer.team))
    .where(eq(dimPlayer.gsisId, gsisId))
    .limit(1);
  return rows[0] ?? null;
}
export const getPlayer = cached("player.getPlayer", getPlayerRaw);

export type WeekRow = {
  season: number;
  week: number;
  team: string;
  position: string;
  fantasyPoints: number;
  snapShare: number | null;
  targetShare: number | null;
  carryShare: number | null;
  xfp: number | null;
  fpoe: number | null;
};

/** The seasons with weekly rows for a player, newest first. */
async function getPlayerSeasonsRaw(gsisId: string): Promise<number[]> {
  const rows = await db()
    .selectDistinct({ season: playerWeekSummary.season })
    .from(playerWeekSummary)
    .where(eq(playerWeekSummary.gsisId, gsisId))
    .orderBy(desc(playerWeekSummary.season));
  return rows.map((r) => r.season);
}
export const getPlayerSeasons = cached("player.getPlayerSeasons", getPlayerSeasonsRaw);

/** A player's regular-season weeks of one season, in order. */
async function getPlayerWeeksRaw(gsisId: string, season: number): Promise<WeekRow[]> {
  return db()
    .select({
      season: playerWeekSummary.season,
      week: playerWeekSummary.week,
      team: playerWeekSummary.team,
      position: playerWeekSummary.position,
      fantasyPoints: playerWeekSummary.fantasyPoints,
      snapShare: playerWeekSummary.snapShare,
      targetShare: playerWeekSummary.targetShare,
      carryShare: playerWeekSummary.carryShare,
      xfp: playerWeekSummary.xfp,
      fpoe: playerWeekSummary.fpoe,
    })
    .from(playerWeekSummary)
    .where(and(eq(playerWeekSummary.gsisId, gsisId), eq(playerWeekSummary.season, season)))
    .orderBy(asc(playerWeekSummary.week));
}
export const getPlayerWeeks = cached("player.getPlayerWeeks", getPlayerWeeksRaw);
