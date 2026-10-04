import "server-only";
import { and, asc, count, desc, eq, sql, type SQL } from "drizzle-orm";
import { db } from "@/db/client";
import { boardDisagreement, boardList, boardOutcome, boardRow, boardTrackRecord, dimPlayer, dimTeam, modelVersions } from "@/db/schema";
import { cached } from "@/lib/cache";
import { parseDrivers, type BoardCalCell, type BoardOutcome, type BoardTrackRow, type DisagreementRow, type Driver } from "@/lib/board";
import { BOARD_BANDS } from "@/lib/method";
import { showThirdPartyRanks } from "@/lib/third-party";

// The Cliff board's published tables (step I2c-a): board_list, board_row, board_outcome,
// board_track_record, board_disagreement and the board model_versions rows. Every number on
// /board, the home card and the board sections of /methodology and /track-record comes from here
// (or lib/method.ts).

const kindOf = (k: string): "live" | "backtest" => (k === "live" ? "live" : "backtest");
const iso = (v: string | Date) => new Date(v).toISOString();

export type BoardKey = { season: number; kind: "live" | "backtest" };

/** Every published board, newest season first; live first within a season. */
async function getBoardIndexRaw(): Promise<(BoardKey & { week: number; nPlayers: number })[]> {
  const rows = await db()
    .select({ season: boardList.season, kind: boardList.kind, week: boardList.week, nPlayers: boardList.nPlayers })
    .from(boardList)
    .orderBy(desc(boardList.season), desc(sql`${boardList.kind} = 'live'`));
  return rows.map((r) => ({ ...r, kind: kindOf(r.kind) }));
}
export const getBoardIndex = cached("board.getBoardIndex", getBoardIndexRaw);

export type BoardHeader = BoardKey & { week: number; snapshot: string; asOf: string; generatedAt: string; incomplete: boolean; nPlayers: number; note: string | null; modelVersion: string; missedVersion: string };

export type BoardEntry = {
  gsisId: string;
  name: string;
  team: string;
  teamName: string | null;
  position: string;
  cliffRank: number;
  cliffProbability: number;
  missedRank: number;
  missedProbability: number;
  ecrRank: number | null;
  age: number | null;
  priorSeasons: number;
  gamesS: number;
  ppgS: number;
  posRankS: number;
  cliffDrivers: Driver[];
  missedDrivers: Driver[];
  outcome: BoardOutcome | null;
};

const outcomeJoin = and(eq(boardOutcome.season, boardRow.season), eq(boardOutcome.week, boardRow.week), eq(boardOutcome.gsisId, boardRow.gsisId));

/** One board (week 0, the preseason snapshot): its header and every player by the Cliff chance,
 *  with what happened (final) or pending. */
async function getBoardRaw(season: number, kind: "live" | "backtest"): Promise<{ header: BoardHeader; rows: BoardEntry[] } | null> {
  const h = await db()
    .select()
    .from(boardList)
    .where(and(eq(boardList.season, season), eq(boardList.kind, kind)))
    .orderBy(asc(boardList.week))
    .limit(1);
  const l = h[0];
  if (!l) return null;
  const rows = await db()
    .select({
      gsisId: boardRow.gsisId, name: dimPlayer.displayName, team: boardRow.team, teamName: dimTeam.teamName, position: boardRow.position,
      cliffRank: boardRow.cliffRank, cliffProbability: boardRow.cliffProbability, missedRank: boardRow.missedRank,
      missedProbability: boardRow.missedProbability, ecrRank: boardRow.ecrRank, age: boardRow.age, priorSeasons: boardRow.priorSeasons,
      gamesS: boardRow.gamesS, ppgS: boardRow.ppgS, posRankS: boardRow.posRankS, cliffDrivers: boardRow.cliffDrivers, missedDrivers: boardRow.missedDrivers,
      gamesS1: boardOutcome.gamesS1, ppgS1: boardOutcome.ppgS1, yCliff: boardOutcome.yCliff, yMissed: boardOutcome.yMissed, labelStatus: boardOutcome.labelStatus,
    })
    .from(boardRow)
    .innerJoin(dimPlayer, eq(dimPlayer.gsisId, boardRow.gsisId))
    .leftJoin(dimTeam, eq(dimTeam.teamAbbr, boardRow.team))
    .leftJoin(boardOutcome, outcomeJoin)
    .where(and(eq(boardRow.season, season), eq(boardRow.week, l.week), eq(boardRow.snapshot, l.snapshot), eq(boardRow.kind, kind)))
    .orderBy(asc(boardRow.cliffRank), asc(boardRow.gsisId));
  return {
    header: {
      season: l.season, kind: kindOf(l.kind), week: l.week, snapshot: l.snapshot, asOf: iso(l.asOf), generatedAt: iso(l.generatedAt),
      incomplete: l.incomplete, nPlayers: l.nPlayers, note: l.note, modelVersion: l.modelVersion, missedVersion: l.missedVersion,
    },
    rows: rows.map(({ gamesS1, ppgS1, yCliff, yMissed, labelStatus, cliffDrivers, missedDrivers, ...r }) => ({
      ...r,
      cliffDrivers: parseDrivers(cliffDrivers),
      missedDrivers: parseDrivers(missedDrivers),
      outcome: labelStatus === null ? null : { gamesS1, ppgS1, yCliff, yMissed, labelStatus },
    })),
  };
}
const getBoardCached = cached("board.getBoard", getBoardRaw);
/** One board. While the site is public FantasyPros' per-player ranks are removed (ecrRank NULL,
 *  lib/third-party.ts): after the data cache, so one cache serves both modes and no rank reaches
 *  a page, its markers or its payload. */
export async function getBoard(season: number, kind: "live" | "backtest"): Promise<{ header: BoardHeader; rows: BoardEntry[] } | null> {
  const b = await getBoardCached(season, kind);
  return b && !showThirdPartyRanks() ? { ...b, rows: b.rows.map((r) => ({ ...r, ecrRank: null })) } : b;
}

/** The calibration check: every reconstructed board's rows with a final outcome, per chance and
 *  band (lib/method.ts BOARD_BANDS): rows, how many had the outcome, the average chance and the
 *  boards behind them. The Cliff counts only players with BOARD_MIN_GAMES+ games (y_cliff is NULL
 *  otherwise), as the model was trained. An aggregation of the published rows, no table. */
async function getBoardCalibrationRaw(): Promise<BoardCalCell[]> {
  const one = async (chance: "cliff" | "missed") => {
    const p = chance === "cliff" ? boardRow.cliffProbability : boardRow.missedProbability;
    const y = chance === "cliff" ? boardOutcome.yCliff : boardOutcome.yMissed;
    const cases: SQL[] = BOARD_BANDS.map((lo, i) => sql`when ${p} >= ${lo} then ${i}`).reverse();
    const band = sql<number>`(case ${sql.join(cases, sql` `)} end)::int`;
    const rows = await db()
      .select({
        band,
        n: count(),
        hits: sql<number>`sum(case when ${y} then 1 else 0 end)::int`,
        meanPred: sql<number>`avg(${p})::float8`,
        boards: sql<number>`count(distinct ${boardRow.season})::int`,
      })
      .from(boardRow)
      .innerJoin(boardOutcome, outcomeJoin)
      .where(and(eq(boardRow.kind, "backtest"), eq(boardOutcome.labelStatus, "final"), sql`${y} is not null`))
      .groupBy(sql`1`);
    return rows.map((r) => ({ chance, band: Number(r.band), n: Number(r.n), hits: Number(r.hits), meanPred: Number(r.meanPred), boards: Number(r.boards) }));
  };
  return [...(await one("cliff")), ...(await one("missed"))];
}
export const getBoardCalibration = cached("board.getBoardCalibration", getBoardCalibrationRaw);

/** The walk-forward track record, row for row (reports/board/preseason_cliff.csv, then the
 *  Breakout research rows). */
async function getBoardTrackRaw(): Promise<BoardTrackRow[]> {
  const rows = await db().select().from(boardTrackRecord).orderBy(asc(boardTrackRecord.line));
  return rows.map((r) => ({ variant: r.variant, slice: r.slice, model: r.model, vs: r.vs, metric: r.metric, value: r.value, lo: r.lo, hi: r.hi, research: r.research }));
}
export const getBoardTrack = cached("board.getBoardTrack", getBoardTrackRaw);

/** Where the model and the experts disagreed, per ECR-era board and pick group. */
async function getBoardDisagreementRaw(): Promise<DisagreementRow[]> {
  return db()
    .select({ variant: boardDisagreement.variant, model: boardDisagreement.model, season: boardDisagreement.season, pickGroup: boardDisagreement.pickGroup, players: boardDisagreement.players, hits: boardDisagreement.hits })
    .from(boardDisagreement)
    .orderBy(asc(boardDisagreement.variant), asc(boardDisagreement.season), asc(boardDisagreement.pickGroup));
}
export const getBoardDisagreement = cached("board.getBoardDisagreement", getBoardDisagreementRaw);

export type BoardModel = { modelVersion: string; model: string; label: string; snapshotSeason: number | null; trainingSeasons: number[]; featureList: string[]; c: number | null };

/** The two models of the newest board (its model_version and missed_version) and how many board
 *  model versions are published in all (one per earlier fold and model). */
async function getBoardModelsRaw(): Promise<{ cliff: BoardModel | null; missed: BoardModel | null; versions: number }> {
  const rows = await db().select().from(modelVersions).where(eq(modelVersions.module, "board")).orderBy(desc(modelVersions.testSeason));
  const newest = await db().select({ m: boardList.modelVersion, x: boardList.missedVersion }).from(boardList).orderBy(desc(boardList.season), desc(sql`${boardList.kind} = 'live'`)).limit(1);
  const pick = (v: string | undefined): BoardModel | null => {
    const m = rows.find((r) => r.modelVersion === v);
    if (!m) return null;
    const params = (m.params ?? {}) as Record<string, unknown>;
    return {
      modelVersion: m.modelVersion, model: m.model, label: m.label, snapshotSeason: m.testSeason, trainingSeasons: [...m.trainingSeasons].sort((a, b) => a - b),
      featureList: m.featureList, c: typeof params.C === "number" ? params.C : null,
    };
  };
  return { cliff: pick(newest[0]?.m), missed: pick(newest[0]?.x), versions: rows.length };
}
export const getBoardModels = cached("board.getBoardModels", getBoardModelsRaw);

export type BoardLiveSummary = { boards: number; seasons: number[]; rows: number; final: number; cliffs: number; missed: number };

/** The live boards' rows and how many of their outcomes are final: the track record's live
 *  panel. A pending outcome is never counted either way. */
async function getBoardLiveRaw(): Promise<BoardLiveSummary> {
  const lists = await db().select({ season: boardList.season }).from(boardList).where(eq(boardList.kind, "live"));
  const r = await db()
    .select({
      rows: count(),
      final: sql<number>`sum(case when ${boardOutcome.labelStatus} = 'final' and ${boardOutcome.yMissed} is not null then 1 else 0 end)::int`,
      cliffs: sql<number>`sum(case when ${boardOutcome.labelStatus} = 'final' and ${boardOutcome.yCliff} then 1 else 0 end)::int`,
      missed: sql<number>`sum(case when ${boardOutcome.labelStatus} = 'final' and ${boardOutcome.yMissed} then 1 else 0 end)::int`,
    })
    .from(boardRow)
    .leftJoin(boardOutcome, outcomeJoin)
    .where(eq(boardRow.kind, "live"));
  const x = r[0];
  return {
    boards: lists.length, seasons: lists.map((l) => l.season).sort((a, b) => b - a), rows: Number(x?.rows ?? 0),
    final: Number(x?.final ?? 0), cliffs: Number(x?.cliffs ?? 0), missed: Number(x?.missed ?? 0),
  };
}
export const getBoardLive = cached("board.getBoardLive", getBoardLiveRaw);

/** The reconstructed boards with final outcomes: how many, and their first and last season. */
async function getBoardGradedRaw(): Promise<{ boards: number; from: number | null; to: number | null }> {
  const r = await db()
    .select({ boards: sql<number>`count(distinct ${boardOutcome.season})::int`, from: sql<number | null>`min(${boardOutcome.season})`, to: sql<number | null>`max(${boardOutcome.season})` })
    .from(boardOutcome)
    .where(eq(boardOutcome.labelStatus, "final"));
  const x = r[0];
  return { boards: Number(x?.boards ?? 0), from: x?.from ?? null, to: x?.to ?? null };
}
export const getBoardGraded = cached("board.getBoardGraded", getBoardGradedRaw);
