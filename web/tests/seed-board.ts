// The FICTIONAL seed of the Cliff board's tables (every player, team and number is made up),
// loaded by tests/seed.ts for the "full" variant: board_list, board_row, board_outcome,
// board_track_record (Breakout rows flagged research), board_disagreement, the board model
// versions and the glossary terms the pages ask for. Typed against db/schema.ts. Uses the main
// seed's players and teams (a 14-player board, so it folds and has disagreements).
import type { NodePgDatabase } from "drizzle-orm/node-postgres";
import * as s from "../db/schema";
import { players } from "./seed";

type Db = NodePgDatabase<typeof s>;
type Row = typeof s.boardRow.$inferInsert;

const r4 = (x: number) => Math.round(x * 10000) / 10000;

export const BOARD_SEED = {
  /** the current season: a live board and a reconstructed one, outcomes pending */
  season: 2026,
  /** complete past boards with the experts' ranks (2024 has an unranked player) */
  past: [2024, 2025],
  /** a complete board before the experts' ranks exist */
  noEcr: 2019,
  /** the players (main seed ids): QB 1-3, RB 13-16, WR 25-28 (26 = the long name), TE 37-39 */
  ids: [1, 2, 3, 13, 14, 15, 16, 25, 26, 27, 28, 37, 38, 39].map((n) => `00-${String(9000000 + n).padStart(7, "0")}`),
  cliffFeatures: ["age", "ppg_change", "games_s", "depth_rank_s1", "pos_te", "vacated_targets_share_s1"],
  missedFeatures: ["age", "games_s", "pos_te"],
  glossaryNames: ["y_cliff", "y_missed", "age", "ppg_change", "games_s", "depth_rank_s1", "pos_te", "vacated_targets_share_s1"],
  /** the 2024 player the experts did not rank */
  unranked: "00-9000038",
};
const B = BOARD_SEED;

export const boardSeasons = () => [B.noEcr, ...B.past, B.season];
export const boardVersion = (season: number, which: "cliff" | "missed") => `board-${which === "cliff" ? "logit" : "logit-simple"}-seed-${season}`;
export const boardAsOf = (season: number) => new Date(Date.UTC(season, 8, 9, 23, 20));
const CLIFF = [0.81, 0.66, 0.58, 0.52, 0.44, 0.39, 0.33, 0.28, 0.22, 0.18, 0.12, 0.08, 0.05, 0.004];
const MISSED = [0.72, 0.09, 0.15, 0.41, 0.06, 0.3, 0.03, 0.55, 0.11, 0.08, 0.22, 0.04, 0.6, 0.02];
/** the experts' rank minus last season's rank, by player index: big falls for 8-13 (not our top) */
const FALL = [1, 0, 3, 2, -1, 4, 0, 2, 25, 30, 18, 22, 27, 35];

function drivers(k: number) {
  const third =
    k === 2
      ? { feature: "vacated_targets_share_s1", label: "Vacated targets (missing)", contribution: 0.12, value: null, missing: true }
      : { feature: "age", label: "Age after the season", contribution: r4(0.3 - 0.04 * k), value: r4(26 + k * 0.7), missing: false };
  return {
    cliff: [
      { feature: "ppg_change", label: "PPG change", contribution: r4(0.9 - 0.1 * k), value: r4(6 - k), missing: false },
      { feature: "depth_rank_s1", label: "Week-1 depth rank", contribution: r4(0.5 - 0.05 * k), value: 1 + (k % 3), missing: false },
      third,
    ],
    missed: [
      { feature: "games_s", label: "Games played", contribution: r4(-0.3 + 0.05 * k), value: 8 + k, missing: false },
      { feature: "pos_te", label: "Tight end", contribution: -0.2, value: k >= 11 ? 1 : 0, missing: false },
      { feature: "age", label: "Age after the season", contribution: r4(0.2 - 0.02 * k), value: r4(26 + k * 0.7), missing: false },
    ],
  };
}

/** The rows of one board, ranked by each chance (ties by id). */
export function boardRows(season: number, kind: string): Row[] {
  const byId = new Map(players().map((p) => [p.gsisId, p]));
  const drift = (season - 2025) * 0.01 + (kind === "live" ? 0.005 : 0);
  const rows = B.ids.map((gsisId, i) => {
    const p = byId.get(gsisId)!;
    const posRankS = 1 + ((i * 5) % 30);
    const d = drivers(i);
    return {
      season, week: 0, snapshot: "preseason", kind, gsisId, team: p.team!, position: p.position!, asOf: boardAsOf(season),
      cliffRank: 0, cliffProbability: r4(Math.min(0.99, Math.max(0.001, CLIFF[i] + drift))), missedRank: 0, missedProbability: MISSED[i],
      ecrRank: season < 2020 || (season === 2024 && gsisId === B.unranked) ? null : posRankS + FALL[i],
      age: r4(25 + i * 0.8), priorSeasons: 3 + (i % 9), gamesS: 10 + (i % 8), ppgS: r4(18 - i * 0.6), posRankS,
      ppgChange: r4(6 - i), touchesPerGameS: r4(20 - i), depthRankS1: 1 + (i % 3), teamChangeS1: i % 4 === 0, dcAbsent: false, hcChangeS1: i % 5 === 0,
      cliffDrivers: d.cliff, missedDrivers: d.missed,
    } satisfies Row;
  });
  const rank = (key: "cliffProbability" | "missedProbability", into: "cliffRank" | "missedRank") =>
    [...rows].sort((a, b) => b[key] - a[key] || (a.gsisId < b.gsisId ? -1 : 1)).forEach((r, k) => (r[into] = k + 1));
  rank("cliffProbability", "cliffRank");
  rank("missedProbability", "missedRank");
  return rows.sort((a, b) => a.cliffRank - b.cliffRank);
}

/** What happened: pending for the current season; else by board rank k: every 5th missed time
 *  (3 games), ranks 1, 3 and 5 a Cliff, the rest held. */
export function boardOutcomes(season: number): (typeof s.boardOutcome.$inferInsert)[] {
  return boardRows(season, "backtest").map((r, k) => {
    const key = { season, week: 0, gsisId: r.gsisId };
    if (season === B.season) return { ...key, gamesS1: null, ppgS1: null, yCliff: null, yMissed: null, labelStatus: "pending" };
    if (k % 5 === 4) return { ...key, gamesS1: 3, ppgS1: 5, yCliff: null, yMissed: true, labelStatus: "final" };
    const cliff = k < 6 && k % 2 === 0;
    return { ...key, gamesS1: 12 + (k % 5), ppgS1: r4(r.ppgS * (cliff ? 0.6 : 1.02)), yCliff: cliff, yMissed: false, labelStatus: "final" };
  });
}

/** Every seeded board: (season, kind). */
export const boardLists = () => [...boardSeasons().map((season) => ({ season, kind: "backtest" })), { season: B.season, kind: "live" }];

type Track = typeof s.boardTrackRecord.$inferInsert;

/** The track record: three Cliff variants and two Breakout ones (research). Breakout WR/TE shows no
 *  clear difference from the PPG rank; Breakout RB is ahead of it (interval above zero). */
export function boardTrack(): Track[] {
  const out: Track[] = [];
  const add = (r: Omit<Track, "line">) => out.push({ line: out.length + 1, ...r });
  const variants: [string, string, string, boolean, number, [number, number, number]][] = [
    ["cliff", "cliff_main", "logit", false, 0.41, [0.15, 0.04, -0.04]],
    ["cliff", "cliff_missed", "logit_simple", false, 0.64, [0.4, 0.26, 0.06]],
    ["cliff", "cliff_sensitivity", "logit", false, 0.67, [0.24, 0.14, -0.02]],
    ["breakout", "breakout_wr_te", "logit", true, 0.26, [0.006, 0.02, 0.04]],
    ["breakout", "breakout_rb", "logit", true, 0.27, [0.088, 0.04, -0.075]],
  ];
  for (const [population, variant, model, research, pr, [vsRank, vsEos, vsEcr]] of variants) {
    const base = { population, research, variant, shareAboveZero: null };
    for (const slice of ["all", "ecr_era"]) {
      add({ ...base, slice, model, vs: null, metric: "pr_auc", value: pr, lo: r4(pr - 0.05), hi: r4(pr + 0.05) });
      add({ ...base, slice, model, vs: null, metric: "p_at_10", value: 0.48, lo: 0.42, hi: 0.54 });
      add({ ...base, slice, model: "base_ppg_rank", vs: null, metric: "pr_auc", value: r4(pr - vsRank), lo: null, hi: null });
      const w = variant === "breakout_rb" ? 0.079 : 0.06;
      add({ ...base, slice, model, vs: "base_ppg_rank", metric: "pr_auc_diff", value: vsRank, lo: r4(vsRank - w), hi: r4(vsRank + 0.09), shareAboveZero: 0.9 });
      add({ ...base, slice, model, vs: "eos", metric: "pr_auc_diff", value: vsEos, lo: r4(vsEos - 0.03), hi: r4(vsEos + 0.03), shareAboveZero: 0.8 });
      if (slice === "ecr_era") {
        add({ ...base, slice, model: "ecr", vs: null, metric: "pr_auc", value: r4(pr - vsEcr), lo: null, hi: null });
        add({ ...base, slice, model, vs: "ecr", metric: "pr_auc_diff", value: vsEcr, lo: r4(vsEcr - 0.09), hi: r4(vsEcr + 0.08), shareAboveZero: 0.3 });
      }
    }
  }
  return out;
}

/** The disagreement record of the seed's two ECR-era past boards. */
export const boardDisagreements = (): (typeof s.boardDisagreement.$inferInsert)[] =>
  B.past.flatMap((season, i) => [
    ["cliff_main", "logit"],
    ["cliff_missed", "logit_simple"],
  ].flatMap(([variant, model]) => [
    { variant, model, season, pickGroup: "model_only", players: 4, hits: 2 + i },
    { variant, model, season, pickGroup: "ecr_only", players: 4, hits: 1 },
    { variant, model, season, pickGroup: "both", players: 6, hits: 4 },
  ]));

function versions(): (typeof s.modelVersions.$inferInsert)[] {
  const createdAt = new Date("2026-09-01T00:00:00Z");
  const train = (to: number) => Array.from({ length: to - 2015 + 1 }, (_, i) => 2015 + i);
  return boardSeasons().flatMap((season) => [
    { modelVersion: boardVersion(season, "cliff"), module: "board", model: "logit", label: "y_cliff", featureList: B.cliffFeatures, trainingSeasons: train(season - 2), testSeason: season - 1, params: { C: 0.01 }, createdAt },
    { modelVersion: boardVersion(season, "missed"), module: "board", model: "logit_simple", label: "y_missed", featureList: B.missedFeatures, trainingSeasons: train(season - 2), testSeason: season - 1, params: { C: 0.1 }, createdAt },
  ]);
}

const glossaryRow = (name: string): typeof s.glossary.$inferInsert => ({
  name, title: `Seed term ${name}`, kind: name.startsWith("y_") ? "label" : "feature", unit: "seed unit", formula: `seed formula for ${name}`,
  explanation: `Seed explanation of ${name}.`, verified: null, modules: ["board"], modelOutput: false,
});

export async function seedBoard(db: Db): Promise<void> {
  await db.insert(s.glossary).values(B.glossaryNames.map(glossaryRow));
  await db.insert(s.modelVersions).values(versions());
  const lists = boardLists();
  await db.insert(s.boardList).values(
    lists.map((l) => ({
      ...l, week: 0, snapshot: "preseason", asOf: boardAsOf(l.season), modelVersion: boardVersion(l.season, "cliff"), missedVersion: boardVersion(l.season, "missed"),
      generatedAt: new Date(l.kind === "live" ? "2026-09-09T23:40:00Z" : "2026-10-02T18:15:00Z"), incomplete: false, nPlayers: B.ids.length, note: null,
    })),
  );
  await db.insert(s.boardRow).values(lists.flatMap((l) => boardRows(l.season, l.kind)));
  await db.insert(s.boardOutcome).values(boardSeasons().flatMap(boardOutcomes));
  await db.insert(s.boardTrackRecord).values(boardTrack());
  await db.insert(s.boardDisagreement).values(boardDisagreements());
}
