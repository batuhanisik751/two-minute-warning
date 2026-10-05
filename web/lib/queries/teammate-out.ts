import "server-only";
import { and, asc, desc, eq, inArray, max } from "drizzle-orm";
import { db } from "@/db/client";
import { dimPlayer, modelVersions, siteMeta, teammateOutAllocation, teammateOutBacktest, teammateOutCoverage, teammateOutEvents, teammateOutList, teammateOutLive, teammateOutRow } from "@/db/schema";
import { cached } from "@/lib/cache";
import type { TOAllocationRow, TOBacktestRow, TOCoverageRow, TOEventsRow, TOLiveRow, TOParams, TORow, TOSnapshot, TOWeek } from "@/lib/teammate-out";

// Teammate out's published tables (feature #5, migration 0008): teammate_out_list,
// teammate_out_row (append-only snapshots), teammate_out_allocation, _backtest, _coverage,
// _events, _live, the pinned version's model_versions row and the teammate_out_* keys of
// site_meta. Every number on /teammate-out, its methodology and track-record sections and the
// player badge comes from here.

const iso = (v: string | Date) => new Date(v).toISOString();

/** site_meta teammate_out_season / teammate_out_week: the week whose games are next when the
 *  data was published (empty outside the season's weeks: null). */
async function getTeammateOutWeekRaw(): Promise<TOWeek | null> {
  const rows = await db()
    .select()
    .from(siteMeta)
    .where(inArray(siteMeta.key, ["teammate_out_season", "teammate_out_week"]));
  const m = new Map(rows.map((r) => [r.key, r.value]));
  const s = m.get("teammate_out_season");
  const w = m.get("teammate_out_week");
  return s && w && /^\d+$/.test(s) && /^\d+$/.test(w) ? { season: Number(s), week: Number(w) } : null;
}
export const getTeammateOutWeek = cached("teammateOut.getTeammateOutWeek", getTeammateOutWeekRaw);

/** Every week with a published snapshot, newest first, with its snapshot count. */
async function getTeammateOutIndexRaw(): Promise<(TOWeek & { snapshots: number })[]> {
  const rows = await db()
    .select({ season: teammateOutList.season, week: teammateOutList.week })
    .from(teammateOutList)
    .orderBy(desc(teammateOutList.season), desc(teammateOutList.week));
  const out: (TOWeek & { snapshots: number })[] = [];
  for (const r of rows) {
    const last = out[out.length - 1];
    if (last && last.season === r.season && last.week === r.week) last.snapshots += 1;
    else out.push({ season: r.season, week: r.week, snapshots: 1 });
  }
  return out;
}
export const getTeammateOutIndex = cached("teammateOut.getTeammateOutIndex", getTeammateOutIndexRaw);

/** The newest snapshot of a week (its header and teammates), or null when the week has none. */
async function getTeammateOutSnapshotRaw(season: number, week: number): Promise<{ header: TOSnapshot; rows: TORow[] } | null> {
  const newest = await db()
    .select({ at: max(teammateOutList.asOf) })
    .from(teammateOutList)
    .where(and(eq(teammateOutList.season, season), eq(teammateOutList.week, week)));
  const at = newest[0]?.at;
  if (!at) return null;
  const key = and(eq(teammateOutList.season, season), eq(teammateOutList.week, week), eq(teammateOutList.asOf, at));
  const h = (await db().select().from(teammateOutList).where(key).limit(1))[0];
  if (!h) return null;
  const rows = await db()
    .select({ r: teammateOutRow, name: dimPlayer.displayName })
    .from(teammateOutRow)
    .leftJoin(dimPlayer, eq(dimPlayer.gsisId, teammateOutRow.gsisId))
    .where(and(eq(teammateOutRow.season, season), eq(teammateOutRow.week, week), eq(teammateOutRow.asOf, at)))
    .orderBy(asc(teammateOutRow.kickoff), asc(teammateOutRow.team), desc(teammateOutRow.predPoints), asc(teammateOutRow.gsisId));
  const header: TOSnapshot = {
    season, week, asOf: iso(h.asOf), generatedAt: iso(h.generatedAt), modelVersion: h.modelVersion,
    nTeams: h.nTeams, nOut: h.nOut, nPlayers: h.nPlayers, source: h.source,
  };
  return {
    header,
    rows: rows.map(({ r, name }) => ({
      team: r.team, gsisId: r.gsisId, name: name ?? r.gsisId, opponent: r.opponent, gameId: r.gameId, kickoff: iso(r.kickoff),
      outIds: r.outIds, outPlayers: r.outPlayers, outPositions: r.outPositions, outReasons: r.outReasons, nOut: r.nOut,
      vacCarryShare: r.vacCarryShare, vacTargetShare: r.vacTargetShare, position: r.position, role: r.role, baseGames: r.baseGames,
      baseCarryShare: r.baseCarryShare, baseTargetShare: r.baseTargetShare, basePoints: r.basePoints, predCarryShare: r.predCarryShare,
      predTargetShare: r.predTargetShare, predPoints: r.predPoints, pointsLo: r.pointsLo, pointsHi: r.pointsHi, predGain: r.predGain,
    })),
  };
}
export const getTeammateOutSnapshot = cached("teammateOut.getTeammateOutSnapshot", getTeammateOutSnapshotRaw);

export type TeammateOutTables = { allocation: TOAllocationRow[]; backtest: TOBacktestRow[]; coverage: TOCoverageRow[]; events: TOEventsRow[]; live: TOLiveRow[] };

/** The replaced tables: the pinned allocation table, the four candidates' backtest, the 80%
 *  range's coverage, the event counts and the live record (each empty until the first publish
 *  with the module). */
async function getTeammateOutTablesRaw(): Promise<TeammateOutTables> {
  const [allocation, backtest, coverage, events, live] = await Promise.all([
    db().select().from(teammateOutAllocation).orderBy(asc(teammateOutAllocation.outPos), asc(teammateOutAllocation.role)),
    db().select().from(teammateOutBacktest).orderBy(asc(teammateOutBacktest.candidate), asc(teammateOutBacktest.season)),
    db().select().from(teammateOutCoverage),
    db().select().from(teammateOutEvents),
    db().select().from(teammateOutLive).orderBy(desc(teammateOutLive.season)),
  ]);
  return { allocation, backtest, coverage, events, live };
}
export const getTeammateOutTables = cached("teammateOut.getTeammateOutTables", getTeammateOutTablesRaw);

export type TeammateOutModel = { modelVersion: string; trainingSeasons: number[]; testSeason: number | null; params: TOParams };

/** The pinned allocation table's model_versions row (module 'teammate_out'), newest season
 *  first: its params carry the choice and the reason (chosen, rule_choice, chosen_by). */
async function getTeammateOutModelRaw(): Promise<TeammateOutModel | null> {
  const rows = await db().select().from(modelVersions).where(eq(modelVersions.module, "teammate_out")).orderBy(desc(modelVersions.testSeason)).limit(1);
  const r = rows[0];
  if (!r) return null;
  return { modelVersion: r.modelVersion, trainingSeasons: r.trainingSeasons ?? [], testSeason: r.testSeason, params: (r.params ?? {}) as TOParams };
}
export const getTeammateOutModel = cached("teammateOut.getTeammateOutModel", getTeammateOutModelRaw);

export type PlayerTeammateOut = TOWeek &
  ({ kind: "gainer"; team: string; role: string; predPoints: number; pointsLo: number | null; pointsHi: number | null; predGain: number | null; outNames: string } | { kind: "absent"; team: string; reason: string | null });

/** The player's place in the newest snapshot of the week whose games are next: a predicted
 *  gainer (a listed teammate whose bigger share is worth points: pred_gain > 0) or an absent
 *  starter; null otherwise (the player page's badge). */
async function getPlayerTeammateOutRaw(gsisId: string): Promise<PlayerTeammateOut | null> {
  const wk = await getTeammateOutWeek();
  if (!wk) return null;
  const snap = await getTeammateOutSnapshot(wk.season, wk.week);
  if (!snap) return null;
  const mate = snap.rows.find((r) => r.gsisId === gsisId && r.predGain !== null && r.predGain > 0);
  if (mate) {
    const outNames = mate.outPlayers ?? mate.outIds;
    return { ...wk, kind: "gainer", team: mate.team, role: mate.role, predPoints: mate.predPoints, pointsLo: mate.pointsLo, pointsHi: mate.pointsHi, predGain: mate.predGain, outNames };
  }
  for (const r of snap.rows) {
    const ids = r.outIds.split(",");
    const i = ids.indexOf(gsisId);
    if (i === -1) continue;
    const reasons = r.outReasons?.split(", ");
    return { ...wk, kind: "absent", team: r.team, reason: reasons && reasons.length === ids.length ? reasons[i] : null };
  }
  return null;
}
export const getPlayerTeammateOut = cached("teammateOut.getPlayerTeammateOut", getPlayerTeammateOutRaw);
