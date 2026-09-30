// The FICTIONAL seed of the K and D/ST streamer and Regression Watch tables (every name and
// number is made up), loaded by tests/seed.ts for the "full" variant. Typed against
// db/schema.ts. Uses the seed's four teams and its QB/RB/WR/TE players.
import { eq, sql } from "drizzle-orm";
import type { NodePgDatabase } from "drizzle-orm/node-postgres";
import * as s from "../db/schema";

type Db = NodePgDatabase<typeof s>;

export const MODULES_SEED = {
  /** the live week of both modules (the same as the Radar's) */
  liveWeek: { season: 2026, week: 3 },
  /** reconstructed streamer weeks: 2025 W6 with chances, 2024 W5 kickers without (first season) */
  streamBacktest: [
    { season: 2025, week: 6 },
    { season: 2024, week: 5 },
  ],
  /** reconstructed Regression Watch weeks (their weeks are the r(g) columns on /methodology) */
  rwBacktest: [
    { season: 2025, week: 4 },
    { season: 2025, week: 6 },
  ],
  kickers: [
    { id: "00-9100001", name: "Kade Longfoot" },
    { id: "00-9100002", name: "Milo Uprights" },
    { id: "00-9100003", name: "Theodore-Augustin Crossbar-Whitfield" },
    { id: "00-9100004", name: "Sam Holder" },
    { id: "00-9100005", name: "Pat Hashmark" },
  ],
  kNote: "Seed note for K: the kicker model is ahead of last game's points, but not clearly.",
  rwNote: "Seed note: week 3 is earlier than the backtested weeks.",
  /** the reconstructed week whose Buy-low table is long (its extra players 7-12 of each
   *  position are all Buy-low): it folds after 10 rows */
  rwLong: { season: 2025, week: 6, buyLow: 27 },
  /** the stability study's window (regression_stability; fictional numbers) */
  stabilityWindow: "2012-2025",
  /** the fictional league's starter cutoffs the seed glossary's y_start formula states */
  streamTop: { K: 10, DST: 10 },
} as const;

const TEAMS = ["NHG", "SRO", "EVP", "WLF"];
const OPP: Record<string, string> = { NHG: "SRO", SRO: "NHG", EVP: "WLF", WLF: "EVP" };
const at = (iso: string) => new Date(iso);

function versions(): (typeof s.modelVersions.$inferInsert)[] {
  const base = { label: "y_start", module: "streamer", params: { seed: true }, createdAt: at("2026-09-01T00:00:00Z") };
  const seasons = (to: number) => Array.from({ length: to - 2012 + 1 }, (_, i) => 2012 + i);
  const shrink = (metric: string, k: number) =>
    ["QB", "RB", "WR", "TE"].map((position, i) => ({
      n: 100 + i * 50, metric, position, var_noise: 20 + i * 2 + k, var_signal: 0.5 - i * 0.05, prior_mean: 0, first_season: 2009, last_season: 2025,
    }));
  const rwParams = (name: string, halfLife: number | null) => ({
    x_buy: 3, x_sell: 4.5, decile: 0.1, min_games: 3,
    variant: { name, target: "mean", garbage: "all", half_life: halfLife },
    shrinkage: { rows: [...shrink("fpoe", 0), ...shrink("fpoe_ng", -2)], seasons: [2009, 2025] },
  });
  return [
    ...[2024, 2025, 2026].flatMap((y) => [
      { ...base, modelVersion: `logit_k-seed-${y}`, model: "logit_k", trainingSeasons: seasons(y - 1), testSeason: y, featureList: ["is_team_kicker"] },
      { ...base, modelVersion: `rule_dst-seed-${y}`, model: "baseline_opponent_dst", trainingSeasons: seasons(y - 1), testSeason: y, featureList: ["next_opp_points_per_game"] },
    ]),
    { ...base, module: "regression_watch", label: "ppg_ros", modelVersion: "rw-seed-2025", model: "mean_hl8_all", trainingSeasons: [2009, 2010], testSeason: 2025, featureList: ["xfp"], params: rwParams("mean_hl8_all", 8) },
    { ...base, module: "regression_watch", label: "ppg_ros", modelVersion: "rw-seed-2026", model: "mean_flat_all", trainingSeasons: [2009, 2010], testSeason: 2026, featureList: ["xfp"], params: rwParams("mean_flat_all", null) },
  ];
}

export async function seedModules(db: Db): Promise<void> {
  await db.insert(s.modelVersions).values(versions());
  await seedStream(db);
  await seedRegression(db);
}

async function seedStream(db: Db): Promise<void> {
  const S = MODULES_SEED;
  const lists: (typeof s.streamList.$inferInsert)[] = [];
  const picks: (typeof s.streamPick.$inferInsert)[] = [];
  const outcomes = new Map<string, typeof s.streamOutcome.$inferInsert>();
  const add = (season: number, week: number, position: "K" | "DST", kind: "live" | "backtest", withChance: boolean) => {
    const live = kind === "live";
    const entities =
      position === "K"
        ? S.kickers.map((k, i) => ({ id: k.id, name: k.name, team: TEAMS[i % TEAMS.length], type: "kicker" }))
        : TEAMS.map((t) => ({ id: `DST-${t}`, name: `${t} D/ST`, team: t, type: "team_defense" }));
    lists.push({
      season, week, position, kind,
      asOf: at(live ? "2026-09-29T14:00:00Z" : `${season}-10-0${week - 3}T14:00:00Z`),
      modelVersion: `${position === "K" ? "logit_k" : "rule_dst"}-seed-${season}`,
      generatedAt: at(live ? "2026-09-29T15:30:00Z" : "2026-09-28T12:00:00Z"),
      incomplete: false,
      nPool: entities.length,
      note: live && position === "K" ? S.kNote : null,
    });
    entities.forEach((e, i) => {
      const rank = i + 1;
      const chance = withChance ? Math.round((0.52 - rank * 0.06) * 10000) / 10000 : null;
      picks.push({
        season, week, position, kind, rank,
        entityId: e.id, entityType: e.type, displayName: e.name, team: e.team,
        nextOpponent: rank === entities.length ? null : OPP[e.team],
        home: rank === entities.length ? null : rank % 2 === 1,
        chance,
        chanceLow: chance === null ? null : Math.round((chance - 0.04) * 10000) / 10000,
        chanceHigh: chance === null ? null : Math.round((chance + 0.04) * 10000) / 10000,
        modelProb: position === "K" ? Math.round((0.5 - rank * 0.05) * 10000) / 10000 : null,
        tier: chance === null ? null : chance >= 0.25 ? "speculative" : "watch",
        reasons: live ? [`Seed streamer reason for ${e.name}`] : [],
      });
      const final = season < 2026;
      outcomes.set(`${season}-${week}-${e.id}`, {
        season, week, entityId: e.id,
        yStart: final ? rank % 2 === 1 : null,
        labelStatus: final ? "final" : "pending",
        pointsNextWeek: final ? (rank === 2 ? null : 14 - rank * 2) : null,
      });
    });
  };
  add(S.liveWeek.season, S.liveWeek.week, "K", "live", true);
  add(S.liveWeek.season, S.liveWeek.week, "DST", "live", true);
  add(S.streamBacktest[0].season, S.streamBacktest[0].week, "K", "backtest", true);
  add(S.streamBacktest[0].season, S.streamBacktest[0].week, "DST", "backtest", true);
  add(S.streamBacktest[1].season, S.streamBacktest[1].week, "K", "backtest", false);
  await db.insert(s.streamList).values(lists);
  await db.insert(s.streamPick).values(picks);
  await db.insert(s.streamOutcome).values([...outcomes.values()]);
  await db.insert(s.streamTrackRecord).values(streamTrack());
}

function streamTrack(): (typeof s.streamTrackRecord.$inferInsert)[] {
  const out: Omit<typeof s.streamTrackRecord.$inferInsert, "line">[] = [];
  const base = { trainOn: "pool", seasons: "2014-2025", nGroups: 100 };
  const p5: Record<string, Record<string, number>> = {
    K: { logit: 0.4, lgbm: 0.38, baseline_last_points: 0.37, baseline_ppg: 0.36, baseline_opponent: 0.3 },
    DST: { logit: 0.39, lgbm: 0.38, baseline_last_points: 0.36, baseline_ppg: 0.37, baseline_opponent: 0.41 },
  };
  for (const [position, methods] of Object.entries(p5)) {
    const n = position === "K" ? { nRows: 1000, nPos: 250 } : { nRows: 800, nPos: 300 };
    for (const [method, v] of Object.entries(methods)) {
      out.push({ ...base, ...n, position, method, scope: "pooled", metric: "p_at_5", value: v, lo: v - 0.02, hi: v + 0.02 });
      out.push({ ...base, ...n, position, method, scope: "pooled", metric: "p_at_1", value: v + 0.05, lo: v, hi: v + 0.1 });
    }
  }
  // K: the model against the best rule; D/ST: the model against the rule (the rule is published)
  out.push({ ...base, position: "K", method: "logit", scope: "diff", key: "baseline_last_points", metric: "p_at_5", value: 0.03, lo: -0.01, hi: 0.07, shareAboveZero: 0.9 });
  out.push({ ...base, position: "DST", method: "logit", scope: "diff", key: "baseline_opponent", metric: "p_at_5", value: -0.02, lo: -0.05, hi: 0.01, shareAboveZero: 0.1 });
  out.push({ ...base, position: "K", method: "logit", scope: "season", seasons: "2025", metric: "p_at_5", value: 0.5 });
  return out.map((r, i) => ({ ...r, line: i + 1 }));
}

type Kind = "sell_high" | "buy_low" | "legit" | null;
/** Tags by position and index within the position (the seed's players 00-90000NN). */
const TAGGED: Record<string, Kind[][]> = {
  QB: [["sell_high"], ["buy_low"], ["legit"], ["legit"], [], []],
  RB: [["legit"], ["sell_high"], ["buy_low", "legit"], [], [], []],
  WR: [["buy_low"], ["sell_high"], ["legit"], ["legit"], [], []],
  TE: [["legit"], [], [], [], [], []],
};
const POSITIONS = ["QB", "RB", "WR", "TE"];

const TITLE: Record<NonNullable<Kind>, string> = { sell_high: "Sell-high", buy_low: "Buy-low", legit: "Legit" };

function rwRows(season: number, week: number, kind: "live" | "backtest") {
  const rows: (typeof s.regressionRow.$inferInsert)[] = [];
  const outcomes: (typeof s.regressionOutcome.$inferInsert)[] = [];
  const long = season === MODULES_SEED.rwLong.season && week === MODULES_SEED.rwLong.week;
  POSITIONS.forEach((pos, pi) => {
    const tagged: Kind[][] = long ? [...TAGGED[pos], ...Array.from({ length: 6 }, (): Kind[] => ["buy_low"])] : TAGGED[pos];
    tagged.forEach((tags, i) => {
      const gsisId = `00-${String(9000000 + 1 + pi * 12 + i).padStart(7, "0")}`;
      const first = tags[0] ?? null;
      // distinct gaps between PPG and projection, so the tables' order never rests on a float tie
      const k = pi + i / 10;
      const [ppg, xfp, projection] =
        first === "sell_high" ? [20, 13 + k, 14 + k] : first === "buy_low" ? [6 + k, 12 + k, 11.5 + 2 * k] : first === "legit" ? [15 + k, 14.5 + k, 14.6 + k] : [8 + k, 8.5 + k, 8.4 + k];
      const r2 = (x: number) => Math.round(x * 100) / 100;
      rows.push({
        season, week, kind, gsisId, position: pos, team: TEAMS[i % TEAMS.length], games: week,
        ppg: r2(ppg), ppgNg: r2(ppg - 1), xfpPg: r2(xfp), xfpPgNg: r2(xfp - 0.5), fpoePg: r2(ppg - xfp), fpoePgNg: r2(ppg - 1 - (xfp - 0.5)),
        projection: r2(projection), shrinkage: 0.1,
        tag: first, tags: tags.filter((t): t is NonNullable<Kind> => t !== null),
        // like the weekly reasons: one sentence per tag, "Buy-low: ... Legit: ..." (Legit is dropped on the site)
        tagReason: first ? tags.map((t) => `${TITLE[t!]}: seed reason for ${gsisId}.`).join(" ") : null,
      });
      const final = season < 2026;
      outcomes.push({
        season, week, gsisId,
        // the first WR (Buy-low) played no games afterwards
        rosPpg: final ? (pi === 2 && i === 0 ? null : r2(projection + (i % 2 ? -1 : 1))) : null,
        rosGames: final ? (pi === 2 && i === 0 ? 0 : 10) : null,
        labelStatus: final ? "final" : "pending",
      });
    });
  });
  return { rows, outcomes };
}

async function seedRegression(db: Db): Promise<void> {
  const S = MODULES_SEED;
  const weeks = [
    { ...S.rwBacktest[0], kind: "backtest" as const },
    { ...S.rwBacktest[1], kind: "backtest" as const },
    { ...S.liveWeek, kind: "live" as const },
  ];
  for (const w of weeks) {
    const live = w.kind === "live";
    const { rows, outcomes } = rwRows(w.season, w.week, w.kind);
    await db.insert(s.regressionList).values({
      season: w.season, week: w.week, kind: w.kind,
      asOf: at(live ? "2026-09-29T14:00:00Z" : `${w.season}-10-0${w.week - 3}T14:00:00Z`),
      paramsVersion: live ? "rw-seed-2026" : "rw-seed-2025",
      generatedAt: at(live ? "2026-09-29T15:30:00Z" : "2026-09-28T12:00:00Z"),
      incomplete: false, nUniverse: rows.length, note: live ? S.rwNote : null,
    });
    await db.insert(s.regressionRow).values(rows);
    await db.insert(s.regressionOutcome).values(outcomes);
  }
  await db.insert(s.regressionTrackRecord).values(rwTrack());
  await db.insert(s.regressionStability).values(rwStability());
  // the featured running back's weeks without garbage time (the player page's toggle)
  await db
    .update(s.playerWeekSummary)
    .set({
      pointsNg: sql`${s.playerWeekSummary.fantasyPoints} - 1`,
      xfpNg: sql`${s.playerWeekSummary.xfp} - 0.5`,
      fpoeNg: sql`${s.playerWeekSummary.fantasyPoints} - 1 - (${s.playerWeekSummary.xfp} - 0.5)`,
    })
    .where(eq(s.playerWeekSummary.gsisId, "00-9000013"));
}

function rwTrack(): (typeof s.regressionTrackRecord.$inferInsert)[] {
  const out: Omit<typeof s.regressionTrackRecord.$inferInsert, "line">[] = [];
  const b = { weeks: "headline", nSeasons: 2 };
  for (const [position, shift] of [["all", 0], ["QB", 0.3]] as const) {
    const n = position === "all" ? 900 : 200;
    for (const [method, v] of [["model", 3.2], ["baseline_ppg", 3.4], ["baseline_last3", 4.0], ["spec_formula", 3.25]] as const) {
      out.push({ ...b, section: "value", position, method, metric: "mae", value: v + shift, lo: v + shift - 0.1, hi: v + shift + 0.1, n });
    }
    out.push({ ...b, section: "difference", position, method: "model-baseline_ppg", metric: "mae", value: -0.2, lo: -0.3, hi: -0.1, n, shareAboveZero: 0 });
    out.push({ ...b, section: "difference", position, method: "model-baseline_last3", metric: "mae", value: -0.8, lo: -0.9, hi: -0.7, n, shareAboveZero: 0 });
  }
  const tags: [string, number, number][] = [
    ["sell_high", 0.9, 0.6],
    ["buy_low", 0.62, 0.4],
    ["legit", 0.605, 0.61],
  ];
  for (const [metric, tagged, base] of tags) {
    out.push({ ...b, section: "tag", position: "all", metric, rowGroup: "tagged", value: tagged, lo: tagged - 0.03, hi: tagged + 0.03, n: 120, perAsof: 4, notGraded: 5 });
    out.push({ ...b, section: "tag", position: "all", metric, rowGroup: "base", value: base, lo: base - 0.02, hi: base + 0.02, n: 900 });
  }
  for (const season of [2024, 2025]) {
    out.push({ section: "choice", method: "mean_hl8_all", metric: "validation_mae", season, value: 3.1, n: 600, detail: "seed" });
  }
  return out.map((r, i) => ({ ...r, line: i + 1 }));
}

/** The stability study (regression_stability), fictional: split-half r of the metrics per
 *  position (odd/even and first/second halves), and the shrinkage rows r(g), g = 1..17. */
function rwStability(): (typeof s.regressionStability.$inferInsert)[] {
  const out: Omit<typeof s.regressionStability.$inferInsert, "line">[] = [];
  const seasons = MODULES_SEED.stabilityWindow;
  const xfp: Record<string, number> = { QB: 0.7, RB: 0.8, WR: 0.78, TE: 0.75 };
  const fpoe: Record<string, number> = { QB: 0.05, RB: 0.12, WR: 0.08, TE: 0.09 };
  const n: Record<string, number> = { QB: 100, RB: 240, WR: 360, TE: 180 };
  for (const split of ["odd_even", "first_second"]) {
    const d = split === "odd_even" ? 0 : 0.1;
    for (const position of POSITIONS) {
      const metrics: [string, number, number][] = [
        ["xfp", xfp[position] - d, n[position]],
        ["fpoe", fpoe[position], n[position]],
        ["fantasy_points", xfp[position] - 0.1 - d, n[position]],
        ["xfp_ng", xfp[position] - 0.03 - d, n[position]],
        ["fpoe_ng", fpoe[position] - 0.02, n[position]],
        ["points_ng", xfp[position] - 0.13 - d, n[position]],
        ["td_rate_over_expected", 0.01, n[position] - 5],
        position === "QB" ? ["completion_rate_over_expected", 0.3, n[position] - 8] : ["catch_rate_over_expected", 0.14, n[position] - 40],
      ];
      if (position !== "QB") metrics.push(["yac_over_expected", 0.17, n[position] - 60]);
      for (const [metric, value, k] of metrics) {
        out.push({ section: "split_half", seasons, split, position, metric, n: k, value, lo: value - 0.05, hi: value + 0.05 });
      }
    }
  }
  const variances: Record<string, [number, number, number]> = { QB: [0.5, 20, -0.5], RB: [0.4, 12, 0.02], WR: [0.3, 15, -0.01], TE: [0.2, 8, 0.03] };
  for (const metric of ["fpoe", "fpoe_ng"]) {
    for (const position of POSITIONS) {
      const [signal, noise, prior] = variances[position].map((v, i) => (metric === "fpoe_ng" && i < 2 ? v * 0.8 : v));
      for (let g = 1; g <= 17; g++) {
        const r = signal / (signal + noise / g);
        const ci = g <= 12 ? { lo: r - 0.04, hi: r + 0.04 } : {};
        out.push({ section: "shrinkage", seasons, split: "odd_even", position, metric, g, n: n[position], value: r, ...ci, varSignal: signal, varNoise: noise, priorMean: prior });
      }
    }
  }
  return out.map((r, i) => ({ ...r, line: i + 1 }));
}
