// The FICTIONAL seed of the playoff-planner tables (feature #6; every team and number is made
// up), loaded by tests/seed.ts for the "full" variant: two snapshots of 2026 (through weeks 3 and
// 4: the page shows the newest), the four seed teams in weeks 15-17 (week 15 NHG-SRO and EVP-WLF,
// week 16 NHG-EVP and SRO-WLF, week 17 NHG-WLF with SRO and EVP on a bye), the rule's choice per
// position (TE and K: none), the candidates' backtest, the effect sizes, the stability, the late
// weeks, no live record yet (graded after week 17), the model version and the site_meta keys.
// Typed against db/schema.ts.
import type { NodePgDatabase } from "drizzle-orm/node-postgres";
import * as s from "../db/schema";

type Db = NodePgDatabase<typeof s>;
type Row = typeof s.playoffPlannerRow.$inferInsert;

export const PLAYOFF_SEED = {
  season: 2026,
  throughWeeks: [3, 4],
  version: "matchup-seed-2026",
  asOfs: ["2026-09-29T06:00:00.000Z", "2026-10-06T06:00:00.000Z"],
  chosen: { QB: "adjusted", RB: "shrunk", WR: "adjusted", TE: "none", K: "none", DST: "adjusted" } as Record<string, string>,
  games: { 15: [["NHG", "SRO"], ["EVP", "WLF"]], 16: [["NHG", "EVP"], ["SRO", "WLF"]], 17: [["NHG", "WLF"]] } as Record<number, string[][]>,
  /** each defense's base multiplier (the newest snapshot's; the older one is 0.02 lower) */
  base: { NHG: 0.93, SRO: 1.12, EVP: 0.99, WLF: 1.06 } as Record<string, number>,
  /** the featured running back (00-9000013) plays for NHG: his line is weeks 15-17 vs SRO, vs EVP, vs WLF */
  featuredTeam: "NHG",
  /** an unrated position's player (TE, NHG): no line */
  te: "00-9000037",
  effects: { QB: [7.9, 1.8, 0.23], RB: [5.6, 1.6, 0.28], WR: [4.6, 0.9, 0.2], TE: [6.6, 0.5, 0.07], K: [4.9, 0.2, 0.04], DST: [8.8, 4.3, 0.49] } as Record<string, number[]>,
  rule: "seed rule: the next candidate replaces the choice only if its pooled points MAE is lower and lower in at least 7 test seasons",
};
const P = PLAYOFF_SEED;
const POS = ["QB", "RB", "WR", "TE", "K", "DST"];
const TEAMS = ["NHG", "SRO", "EVP", "WLF"];

function snapshotRows(throughWeek: number, shift: number): Row[] {
  const rows: Row[] = [];
  for (const week of [15, 16, 17]) {
    for (const team of TEAMS) {
      const g = P.games[week].find((x) => x.includes(team));
      const opponent = g ? (g[0] === team ? g[1] : g[0]) : null;
      POS.forEach((position, i) => {
        const candidate = P.chosen[position];
        const raw = opponent ? Math.round((P.base[opponent] + shift + i * 0.004) * 1e6) / 1e6 : null;
        const rating = raw === null ? null : candidate === "none" ? 1 : raw;
        rows.push({
          season: P.season, throughWeek, week, team, position, opponent, home: g ? g[0] === team : null, gameId: g ? `${P.season}_${week}_${g[1]}_${g[0]}` : null,
          kickoff: null, candidate, rating, ratingRank: null, raw, shrunk: raw, adjusted: raw, oppGames: opponent ? throughWeek : null, lgPpg: 10 + i,
        });
      });
    }
  }
  // rank 1 = the easiest of the opponents, per week and rated position
  for (const week of [15, 16, 17]) {
    for (const position of POS.filter((p) => P.chosen[p] !== "none")) {
      const mine = rows.filter((r) => r.week === week && r.position === position && r.rating != null).sort((a, b) => b.rating! - a.rating!);
      mine.forEach((r, k) => (r.ratingRank = k + 1));
    }
  }
  return rows;
}

const MAE: Record<string, number[]> = {
  QB: [6.701, 6.965, 6.686, 6.684], RB: [6.129, 6.192, 6.102, 6.104], WR: [6.052, 6.113, 6.043, 6.043],
  TE: [5.232, 5.5, 5.23, 5.23], K: [3.61, 3.855, 3.615, 3.614], DST: [5.548, 5.673, 5.404, 5.397],
};
const CANDS = ["none", "raw", "shrunk", "adjusted"];
const TITLES = ["No matchup (every game 1.00)", "Raw rating", "Shrunk rating", "Schedule-adjusted rating"];

export async function seedPlayoffPlanner(db: Db): Promise<void> {
  await db.insert(s.modelVersions).values({
    modelVersion: P.version, module: "playoff_planner", model: "matchup", label: "unit_points_weeks_15_17", trainingSeasons: [2006, 2007, 2008, 2009, 2010, 2011, 2012],
    testSeason: P.season, featureList: ["opponent_points_allowed", "position"], createdAt: new Date("2026-10-01T00:00:00Z"),
    params: { chosen: P.chosen, rule_choice: P.chosen, chosen_by: "rule", rule: P.rule, test_seasons: [2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025], horizons: [4, 8, 12, 14], playoff_weeks: [15, 16, 17], season_wins_needed: 7 },
  });
  const snaps = P.throughWeeks.map((tw, i) => ({ tw, asOf: P.asOfs[i], rows: snapshotRows(tw, i === 0 ? -0.02 : 0) }));
  await db.insert(s.playoffPlannerList).values(
    snaps.map((x) => ({ season: P.season, throughWeek: x.tw, asOf: new Date(x.asOf), modelVersion: P.version, generatedAt: new Date(Date.parse(x.asOf) + 600_000), nTeams: 4, nRows: x.rows.length })),
  );
  await db.insert(s.playoffPlannerRow).values(snaps.flatMap((x) => x.rows));
  await db.insert(s.playoffPlannerChoice).values(
    POS.map((position) => ({ position, candidate: P.chosen[position], ruleChoice: P.chosen[position], chosenBy: "rule", path: P.chosen[position] === "none" ? "none" : P.chosen[position] === "shrunk" ? "none > shrunk" : "none > shrunk > adjusted", pseudoGames: { QB: 33, RB: 12, WR: 52, TE: 100, K: 27, DST: 8 }[position]! })),
  );
  await db.insert(s.playoffPlannerBacktest).values(
    POS.flatMap((position) =>
      CANDS.map((candidate, i) => {
        const vs = i === 0 ? null : i === 3 && P.chosen[position] !== "none" ? "shrunk" : "none";
        const path = P.chosen[position] === "none" ? ["none"] : P.chosen[position] === "shrunk" ? ["none", "shrunk"] : ["none", "shrunk", "adjusted"];
        return { position, candidate, title: TITLES[i], n: 4000 + i, mae: MAE[position][i], vs, seasonsWon: i === 0 ? null : path.includes(candidate) ? 10 : 5, seasons: 13, tookOver: path.includes(candidate), chosen: candidate === P.chosen[position], rulePick: candidate === P.chosen[position] };
      }),
    ),
  );
  await db.insert(s.playoffPlannerEffects).values(
    [...POS, "all"].flatMap((position) => ["4", "14", "all"].map((horizon) => {
      const [rated, realized, survived] = P.effects[position] ?? [6, 1.5, 0.25];
      return { position, horizon, nWorst: 900, nBest: 800, ratedGap: rated, shrunkGap: Math.round((realized + 0.3) * 10) / 10, realizedGap: realized, survived };
    })),
  );
  await db.insert(s.playoffPlannerStability).values(
    POS.flatMap((position, i) => [4, 14].map((horizon) => ({ position, horizon, seasons: 13, rhoMean: (horizon === 4 ? 0.1 : 0.15) + i * 0.02, rhoMin: 0, rhoMax: 0.6 }))),
  );
  const shares: Record<string, number[]> = { QB: [0.7, 0.68, 0.62], RB: [0.75, 0.73, 0.68], WR: [0.8, 0.79, 0.73], TE: [0.77, 0.75, 0.68] };
  await db.insert(s.playoffPlannerLateWeeks).values(
    ["2013-2020 (17 weeks)", "2021+ (18 weeks)"].flatMap((era, e) => Object.entries(shares).flatMap(([position, sh]) =>
      [15, 16, 17].map((week, k) => ({ era, position, week, based: 200, playedShare: e === 0 ? sh[k] : sh[k] - 0.05 + (k === 2 ? 0.03 : 0), vsBase: 0 })))),
  );
  const meta: Record<string, string> = {
    playoff_planner_season: String(P.season), playoff_planner_weeks: "15,16,17", playoff_planner_latest_season: String(P.season),
    playoff_planner_latest_through_week: String(P.throughWeeks[1]), playoff_planner_latest_as_of: P.asOfs[1],
  };
  await db.insert(s.siteMeta).values(Object.entries(meta).map(([key, value]) => ({ key, value })));
}
