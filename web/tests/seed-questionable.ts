// The FICTIONAL seed of the Questionable tables (feature #1; every player, team and number is
// made up), loaded by tests/seed.ts for the "full" variant: two snapshots of the week whose games
// are next (2026 W4: the newest has 12 players over three kickoffs, the featured running back
// among them for the player badge, a Doubtful player without an "if he plays" line, a player
// without a game yet, the long name), one older week (W3), the history, the backtest, the
// calibration, the live record, the model version, the glossary terms and the site_meta keys.
// Week 5 has no snapshot (its empty state). Typed against db/schema.ts.
import type { NodePgDatabase } from "drizzle-orm/node-postgres";
import * as s from "../db/schema";
import { SEED, players } from "./seed";

type Db = NodePgDatabase<typeof s>;
type Row = typeof s.questionableRow.$inferInsert;

export const QUESTIONABLE_SEED = {
  week: { season: 2026, week: 4 },
  pastWeek: { season: 2026, week: 3 },
  emptyWeek: { season: 2026, week: 5 },
  version: "lookup-seed-2026",
  asOfs: ["2026-10-02T20:00:00.000Z", "2026-10-03T12:00:00.000Z"],
  pastAsOf: "2026-09-26T12:00:00.000Z",
  kickoffs: ["2026-10-04T17:00:00.000Z", "2026-10-04T20:25:00.000Z", "2026-10-05T00:20:00.000Z"],
  /** main-seed player numbers on the newest snapshot, in the order they are made */
  ids: [13, 2, 26, 37, 14, 27, 3, 38, 15, 28, 4, 39],
  /** the badge: the main seed's featured running back, SEED.featured (Questionable, 64%; a
   *  literal: tests/seed.ts imports this file, so SEED is not initialised yet here) */
  featured: "00-9000013",
  featuredChance: 0.64,
  /** the Doubtful player (no "if he plays" line) and the one without a game this season */
  doubtful: "00-9000002",
  noGames: "00-9000037",
  glossaryNames: ["questionable", "doubtful", "practice_status", "play_chance", "dud_rate"],
};
const Q = QUESTIONABLE_SEED;
const gid = (n: number) => `00-${String(9000000 + n).padStart(7, "0")}`;
const CHANCES = [0.64, 0.02, 0.71, 0.55, 0.48, 0.66, 0.73, 0.39, 0.58, 0.62, 0.45, 0.69];
const PRACTICE = ["limited", "dnp", "full", "limited", "dnp", "limited", "full", "none", "limited", "dnp", "full", "limited"];
const RAW: Record<string, string | null> = { full: "Full Participation in Practice", limited: "Limited Participation in Practice", dnp: "Did Not Participate In Practice", none: null };

function snapshotRows(asOf: string, count: number, week = Q.week.week): Row[] {
  const ps = players();
  return Q.ids.slice(0, count).map((n, i) => {
    const p = ps.find((x) => x.gsisId === gid(n))!;
    const k = i < 7 ? 0 : i < 10 ? 1 : 2;
    const teams: string[] = SEED.teams.map((t) => t.teamAbbr);
    const team = p.team!;
    const opponent = teams[(teams.indexOf(team) + 1) % teams.length];
    const doubtful = p.gsisId === Q.doubtful;
    const games = p.gsisId === Q.noGames ? 0 : 3;
    return {
      season: Q.week.season, week, asOf: new Date(asOf), gsisId: p.gsisId, position: p.position!, team, opponent,
      gameId: i % 2 ? `2026_0${week}_${team}_${opponent}` : `2026_0${week}_${opponent}_${team}`,
      kickoff: new Date(Date.parse(Q.kickoffs[k]) - (Q.week.week - week) * 7 * 86_400_000),
      reportStatus: doubtful ? "Doubtful" : "Questionable", practiceStatus: RAW[PRACTICE[i]], practice: PRACTICE[i], missedPrev: i % 3 === 1,
      bodyPart: "knee", playChance: CHANCES[i], playsN: doubtful ? null : 1300, playsMedian: doubtful ? null : 0.8, playsDudRate: doubtful ? null : 0.31,
      healthyMedian: doubtful ? null : 0.85, healthyDudRate: doubtful ? null : 0.28, seasonPpg: games ? 11.4 + i : null, seasonGames: games,
    };
  });
}

const HISTORY: [string, string, number, number][] = [
  ["Questionable", "full", 90, 67], ["Questionable", "limited", 280, 180], ["Questionable", "dnp", 64, 26], ["Questionable", "none", 6, 4],
  ["Doubtful", "full", 3, 1], ["Doubtful", "limited", 12, 1], ["Doubtful", "dnp", 40, 0], ["Doubtful", "none", 2, 0],
  ["Out", "full", 6, 0], ["Out", "limited", 25, 0], ["Out", "dnp", 280, 1], ["Out", "none", 3, 0],
];

function history(): (typeof s.questionableHistory.$inferInsert)[] {
  const out: (typeof s.questionableHistory.$inferInsert)[] = [];
  for (const tag of ["Questionable", "Doubtful", "Out"]) {
    const mine = HISTORY.filter((h) => h[0] === tag);
    const n = mine.reduce((a, h) => a + h[2], 0);
    const played = mine.reduce((a, h) => a + h[3], 0);
    out.push({ reportStatus: tag, practice: "all", seasons: "2016-2025", n, played, playedRate: played / n });
    for (const [, practice, hn, hp] of mine) out.push({ reportStatus: tag, practice, seasons: "2016-2025", n: hn, played: hp, playedRate: hp / hn });
  }
  return out;
}

function backtest(): (typeof s.questionableBacktest.$inferInsert)[] {
  const rows: [string, string, boolean, number, number][] = [
    ["status", "baseline: overall rate for the status", false, 0.6022, 0.2127],
    ["missed_prev+position", "status x missed previous game x position", true, 0.5812, 0.2023],
  ];
  return rows.flatMap(([grouping, title, chosen, ll, br]) =>
    ["2024", "2025", "all"].map((season, i) => ({ grouping, title, chosen, season, n: i === 2 ? 900 : 450, logLoss: ll + i / 1000, brier: br + i / 1000, meanP: 0.6, playedRate: 0.58 })),
  );
}

const CAL: [string, number, number | null, number | null][] = [
  ["<30%", 40, 0.015, 0.025], ["30-50%", 55, 0.45, 0.42], ["50-70%", 130, 0.61, 0.6], ["70-85%", 140, 0.74, 0.71], ["85%+", 0, null, null],
];

export async function seedQuestionable(db: Db): Promise<void> {
  await db.insert(s.glossary).values(
    Q.glossaryNames.map((name) => ({
      name, title: `Seed term ${name}`, kind: "concept", unit: "seed unit", formula: `seed formula for ${name}`,
      explanation: `Seed explanation of ${name}.`, verified: null, modules: ["questionable"], modelOutput: false,
    })),
  );
  await db.insert(s.modelVersions).values({
    modelVersion: Q.version, module: "questionable", model: "lookup", label: "played", trainingSeasons: [2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025],
    testSeason: 2026, featureList: ["report_status", "missed_prev", "position"], createdAt: new Date("2026-10-01T00:00:00Z"),
    params: { grouping: "missed_prev+position", pseudo_count: 20, check_season: 2025, plays_rules: { min_prior_games: 2, min_prior_ppg: 5, dud: 0.5, min_bucket_n: 30 } },
  });
  const snaps = [
    { season: 2026, week: Q.pastWeek.week, asOf: Q.pastAsOf, rows: snapshotRows(Q.pastAsOf, 5, Q.pastWeek.week) },
    { season: 2026, week: Q.week.week, asOf: Q.asOfs[0], rows: snapshotRows(Q.asOfs[0], 10) },
    { season: 2026, week: Q.week.week, asOf: Q.asOfs[1], rows: snapshotRows(Q.asOfs[1], Q.ids.length) },
  ];
  await db.insert(s.questionableList).values(
    snaps.map((x) => ({ season: x.season, week: x.week, asOf: new Date(x.asOf), modelVersion: Q.version, generatedAt: new Date(Date.parse(x.asOf) + 1_800_000), nPlayers: x.rows.length, source: "observed" })),
  );
  await db.insert(s.questionableRow).values(snaps.flatMap((x) => x.rows));
  await db.insert(s.questionableHistory).values(history());
  await db.insert(s.questionableBacktest).values(backtest());
  await db.insert(s.questionableCalibration).values(CAL.map(([bucket, n, predicted, actual], i) => ({ line: i + 1, bucket, n, predicted, actual })));
  await db.insert(s.questionableLive).values([
    { season: 2026, reportStatus: "all", n: 5, predicted: 0.6, actual: 0.8, pending: 12, weeks: 1 },
    { season: 2026, reportStatus: "Questionable", n: 4, predicted: 0.62, actual: 1, pending: null, weeks: null },
    { season: 2026, reportStatus: "Doubtful", n: 1, predicted: 0.02, actual: 0, pending: null, weeks: null },
  ]);
  const meta: Record<string, string> = {
    questionable_season: String(Q.week.season), questionable_week: String(Q.week.week),
    questionable_latest_season: String(Q.week.season), questionable_latest_week: String(Q.week.week), questionable_latest_as_of: Q.asOfs[1],
  };
  await db.insert(s.siteMeta).values(Object.entries(meta).map(([key, value]) => ({ key, value })));
}
