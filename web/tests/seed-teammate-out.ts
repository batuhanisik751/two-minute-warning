// The FICTIONAL seed of the Teammate-out tables (feature #5; every player, team and number is
// made up), loaded by tests/seed.ts for the "full" variant: two snapshots of the week whose games
// are next (2026 W4: the newest has NHG's RB1 out, the featured running back his predicted top
// gainer, and EVP's WR1 (reserve list) and TE1 (Doubtful) out), one older week (W3), the
// allocation table, the four candidates' backtest (the rule picked "nothing changes", the site
// uses "role": the owner's override), the coverage, the event counts, the live record, the model
// version and the site_meta keys. Week 5 has no snapshot (its empty state). Typed against
// db/schema.ts.
import type { NodePgDatabase } from "drizzle-orm/node-postgres";
import * as s from "../db/schema";
import { players } from "./seed";

type Db = NodePgDatabase<typeof s>;
type Row = typeof s.teammateOutRow.$inferInsert;

const gid = (n: number) => `00-${String(9000000 + n).padStart(7, "0")}`;

export const TEAMMATE_OUT_SEED = {
  week: { season: 2026, week: 4 },
  pastWeek: { season: 2026, week: 3 },
  emptyWeek: { season: 2026, week: 5 },
  version: "alloc-seed-2026",
  asOfs: ["2026-10-02T20:00:00.000Z", "2026-10-03T12:00:00.000Z"],
  pastAsOf: "2026-09-26T12:00:00.000Z",
  kickoffs: ["2026-10-04T17:00:00.000Z", "2026-10-04T20:25:00.000Z"],
  /** the featured running back (a literal: tests/seed.ts imports this file): NHG's RB2, the
   *  predicted top gainer while the RB1 (absent) is out */
  featured: "00-9000013",
  absent: gid(17),
  /** EVP's two absent starters (WR on a reserve list, TE Doubtful) */
  absentTwo: [gid(27), gid(39)],
  why: "Seed reason: the owner chose role because the page answers who gets the work.",
  rule: "seed rule: the next candidate replaces the choice only if its points MAE is lower",
};
const T = TEAMMATE_OUT_SEED;
const nameOf = (id: string) => players().find((p) => p.gsisId === id)!.displayName!;

/** (team, opponent, kickoff index, absent ids, positions, reasons, vacated carry / target share,
 *  teammates: [id, position, role, base points, predicted points, low, high]) */
const GAMES: [string, string, number, string[], string, string, number, number, [number, string, string, number, number, number, number][]][] = [
  ["NHG", "SRO", 0, [T.absent], "RB", "Out", 0.56, 0.1, [[13, "RB", "RB2", 9, 14.2, 6.1, 22], [21, "RB", "RB3", 3, 4.1, 0.2, 9.8], [25, "WR", "WR1", 13.5, 13.1, 5, 21.4]]],
  ["EVP", "WLF", 1, T.absentTwo, "WR, TE", "roster RES, Doubtful", 0, 0.44, [[31, "WR", "WR2", 8, 10, 3.5, 17.2], [43, "TE", "TE2", 3.2, 4.7, 0.6, 10.1], [35, "WR", "WR3", 5.5, 6.3, 1.4, 12.8]]],
];

function snapshotRows(asOf: string, week: number, games = GAMES): Row[] {
  const back = (T.week.week - week) * 7 * 86_400_000;
  return games.flatMap(([team, opponent, k, ids, poss, reasons, vc, vt, mates]) =>
    mates.map(([n, position, role, base, pred, lo, hi]) => ({
      season: T.week.season, week, asOf: new Date(asOf), team, gsisId: gid(n), opponent, gameId: `2026_0${week}_${opponent}_${team}`,
      kickoff: new Date(Date.parse(T.kickoffs[k]) - back), outIds: ids.join(","), outPlayers: ids.map(nameOf).join(", "), outPositions: poss,
      outReasons: reasons, nOut: ids.length, vacCarryShare: vc, vacTargetShare: vt, position, role, baseGames: 3,
      baseCarryShare: position === "RB" ? 0.25 : 0.02, baseTargetShare: position === "RB" ? 0.08 : 0.18, baseSnapShare: 0.5, basePoints: base,
      baseTeamCarries: 26, baseTeamTargets: 34, predCarryShare: position === "RB" ? 0.25 + (pred - base) / 40 : 0.02,
      predTargetShare: position === "RB" ? 0.09 : 0.18 + (pred - base) / 60, carryShareChange: position === "RB" ? (pred - base) / 40 : 0,
      targetShareChange: position === "RB" ? 0.01 : (pred - base) / 60, predPoints: pred, pointsLo: lo, pointsHi: hi,
      predGain: Math.round((pred - base) * 10) / 10, allocCarryShare: null, allocTargetShare: null,
    })),
  );
}

const ALLOC: [string, string, string, number, number | null, number][] = [
  ["RB", "RB2", "RB", 408, 0.45, 0.41], ["RB", "RB3", "RB", 331, 0.23, 0.22], ["RB", "RB+", "RB", 206, 0.08, 0.11], ["RB", "WR1", "WR", 390, null, -0.16],
  ["WR", "WR2", "WR", 360, null, 0.15], ["WR", "WR3", "WR", 300, null, 0.18], ["WR", "TE1", "TE", 280, null, 0.1], ["TE", "TE2", "TE", 90, null, 0.26],
];
const CANDS: [string, string, number, number, number, number][] = [
  ["nothing", "Nothing changes", 4.36, 0.044, 0.052, 0.114], ["pro_rata", "Pro rata", 4.574, 0.0401, 0.0551, 0.271],
  ["group", "Group", 4.437, 0.0431, 0.0512, 0.186], ["role", "Role", 4.393, 0.0402, 0.0508, 0.269],
];

export async function seedTeammateOut(db: Db): Promise<void> {
  await db.insert(s.modelVersions).values({
    modelVersion: T.version, module: "teammate_out", model: "alloc", label: "teammate_points", trainingSeasons: [2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025],
    testSeason: 2026, featureList: ["out_position", "role"], createdAt: new Date("2026-10-01T00:00:00Z"),
    params: { chosen: "role", rule_choice: "nothing", chosen_by: T.why, rule: T.rule, path: ["nothing"], ranges: { level: 0.8, q_lo: 0.1, q_hi: 0.9, min_misses: 30 },
      event_rule: { window: 4, rb_carry_share: 0.45, target_share: 0.2, min_base_games: 2 }, test_seasons: [2024, 2025], train_seasons: "2013-2025" },
  });
  const snaps = [
    { week: T.pastWeek.week, asOf: T.pastAsOf, rows: snapshotRows(T.pastAsOf, T.pastWeek.week, GAMES.slice(0, 1)) },
    { week: T.week.week, asOf: T.asOfs[0], rows: snapshotRows(T.asOfs[0], T.week.week, GAMES.slice(0, 1)) },
    { week: T.week.week, asOf: T.asOfs[1], rows: snapshotRows(T.asOfs[1], T.week.week) },
  ];
  await db.insert(s.teammateOutList).values(
    snaps.map((x) => ({
      season: T.week.season, week: x.week, asOf: new Date(x.asOf), modelVersion: T.version, generatedAt: new Date(Date.parse(x.asOf) + 1_800_000),
      nTeams: new Set(x.rows.map((r) => r.team)).size, nOut: [...new Map(x.rows.map((r) => [r.team, r.nOut])).values()].reduce((a, b) => a + b, 0), nPlayers: x.rows.length, source: "observed",
    })),
  );
  await db.insert(s.teammateOutRow).values(snaps.flatMap((x) => x.rows));
  await db.insert(s.teammateOutAllocation).values(ALLOC.map(([outPos, role, position, n, carry, target]) => ({ outPos, role, position, n, carry, target, nGroup: n * 2 })));
  await db.insert(s.teammateOutBacktest).values(
    CANDS.flatMap(([candidate, title, mp, mc, mt, hit]) =>
      ["2024", "2025", "all"].map((season, i) => ({
        candidate, title, chosen: candidate === "role", rulePick: candidate === "nothing", season, n: i === 2 ? 1400 : 700, events: i === 2 ? 160 : 80,
        maeCarry: mc, maeTarget: mt, maePoints: mp + (i - 2) / 100, topHit: hit,
      })),
    ),
  );
  await db.insert(s.teammateOutCoverage).values(
    [["all", 1400, 150, 160], ["RB", 400, 40, 45], ["WR", 700, 80, 85], ["TE", 300, 30, 30]].map(([position, n, below, above]) => ({
      position: String(position), seasons: "2025-2025", n: Number(n), below: Number(below), above: Number(above),
      inside: Number(n) - Number(below) - Number(above), coverage: (Number(n) - Number(below) - Number(above)) / Number(n),
    })),
  );
  await db.insert(s.teammateOutEvents).values(
    [["all", 1200, 1100, 980, 880, 100], ["RB", 560, 510, 500, 420, 80], ["WR", 520, 480, 380, 370, 10], ["TE", 120, 110, 100, 90, 10]].map(([outPos, sat, kept, events, single, multi]) => ({
      outPos: String(outPos), seasons: "2013-2025", sat: Number(sat), kept: Number(kept), noRosterRow: Number(sat) - Number(kept), goneStatus: 0,
      events: Number(events), single: Number(single), multi: Number(multi), teammateRows: Number(events) * 9,
    })),
  );
  await db.insert(s.teammateOutLive).values({
    season: 2026, n: 3, pending: 6, starterPlayed: 0, didNotPlay: 0, weeks: 1, teamWeeks: 1, ranged: 3, maePoints: 3.4, maePointsBase: 3.9,
    maeCarryShare: 0.05, maeCarryShareBase: 0.06, maeTargetShare: 0.04, maeTargetShareBase: 0.05, coverage: 2 / 3, topHit: 1,
  });
  const meta: Record<string, string> = {
    teammate_out_season: String(T.week.season), teammate_out_week: String(T.week.week),
    teammate_out_latest_season: String(T.week.season), teammate_out_latest_week: String(T.week.week), teammate_out_latest_as_of: T.asOfs[1],
  };
  await db.insert(s.siteMeta).values(Object.entries(meta).map(([key, value]) => ({ key, value })));
}
