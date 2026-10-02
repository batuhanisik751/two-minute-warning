// A small, deterministic, FICTIONAL data set for the web tests (every team, player and
// number here is made up; nothing is a real-world fact). Typed against db/schema.ts, so a
// schema change that breaks the seed breaks the typecheck.
//
// Variants:
// - "full": everything the pages read, incl. a live week (2026 W3), a reconstructed week with
//   chances and reasons (2026 W2), and two walk-forward backtest weeks without them
//   (2024 W5, 2025 W6), outcomes final / pending, a player with weekly rows.
//   The K and D/ST streamer and Regression Watch tables come from tests/seed-modules.ts, the
//   Decision Report Card's from tests/seed-decisions.ts.
// - "empty": a first-publish state: site_meta and the glossary only (no lists, no track
//   record), for the empty states.
import type { NodePgDatabase } from "drizzle-orm/node-postgres";
import * as s from "../db/schema";
import { seedDecisions } from "./seed-decisions";
import { seedHotSeat } from "./seed-hot-seat";
import { MODULES_SEED, seedModules } from "./seed-modules";

export type SeedVariant = "full" | "empty";

export const SEED = {
  asOfLive: "2026-09-29T14:00:00.000Z",
  generatedAt: "2026-09-29T15:30:00.000Z",
  liveWeek: { season: 2026, week: 3 },
  reconWeek: { season: 2026, week: 2 },
  backtestWeeks: [
    { season: 2024, week: 5 },
    { season: 2025, week: 6 },
  ],
  /** the player with weekly rows and Radar history (a running back) */
  featured: "00-9000013",
  featuredName: "Rowan Fielding",
  /** a very long name (the second wide receiver) for the layout check: it must wrap cleanly */
  longName: { gsisId: "00-9000026", name: "Bartholomew-Maximilian Wrenfield-Castellanos III" },
  /** the fictional league shape the glossary's starter_threshold states (read by the FLEX note) */
  league: { teams: 10, starters: { QB: 10, RB: 20, WR: 30, TE: 8 } },
  positions: ["QB", "RB", "WR", "TE"] as const,
  /** picks per list */
  sizes: { live: 8, recon: 6, backtest: 12 },
  teams: [
    { teamAbbr: "NHG", teamName: "North Harbor Gulls", teamNick: "Gulls", conference: "NFC", division: "NFC North", color: "#1f4e79", color2: "#d9d9d9" },
    { teamAbbr: "SRO", teamName: "South Ridge Owls", teamNick: "Owls", conference: "AFC", division: "AFC South", color: "#5b2c6f", color2: "#f4d03f" },
    { teamAbbr: "EVP", teamName: "East Valley Pioneers", teamNick: "Pioneers", conference: "AFC", division: "AFC East", color: "#7b241c", color2: "#ffffff" },
    { teamAbbr: "WLF", teamName: "West Lake Foxes", teamNick: "Foxes", conference: "NFC", division: "NFC West", color: "#b9770e", color2: "#17202a" },
  ],
  /** glossary rows the pages ask for (plus one of each kind) */
  glossaryNames: [
    "as_of", "available_at", "candidate_pool", "starter_threshold", "regression_to_the_mean",
    "y_hit", "y_sustained", "fantasy_points", "xfp", "fpoe", "offense_snap_share",
    "target_share", "carry_share", "gsis_id", "label_status", "y_start",
  ],
} as const;

const FIRST = ["Avery", "Blake", "Casey", "Drew", "Emery", "Finley", "Gray", "Harper", "Indy", "Jules", "Kai", "Logan"];
const LAST = ["Quarry", "Rowe", "Wren", "Tate"];

/** 12 players per position: 00-90000NN, NN = 1 + position index * 12 + i. */
export function players() {
  const out: (typeof s.dimPlayer.$inferInsert)[] = [];
  SEED.positions.forEach((pos, pi) => {
    for (let i = 0; i < 12; i++) {
      const n = 1 + pi * 12 + i;
      out.push({
        gsisId: `00-${String(9000000 + n).padStart(7, "0")}`,
        displayName: `${FIRST[i]} ${LAST[pi]}`,
        position: pos,
        team: SEED.teams[i % SEED.teams.length].teamAbbr,
        draftYear: i % 3 === 0 ? null : 2018 + (i % 6),
        draftRound: i % 3 === 0 ? null : 1 + (i % 7),
        draftPick: i % 3 === 0 ? null : 10 + i * 7,
        rookieSeason: 2018 + (i % 6),
      });
    }
  });
  // the featured running back gets a proper name (RB is position index 1, i = 0 -> n = 13)
  const f = out.find((p) => p.gsisId === SEED.featured)!;
  f.displayName = SEED.featuredName;
  f.draftYear = 2023;
  f.draftRound = 3;
  f.draftPick = 79;
  out.find((p) => p.gsisId === SEED.longName.gsisId)!.displayName = SEED.longName.name;
  return out;
}

const L = SEED.league;
const glossaryRow = (name: string, i: number): typeof s.glossary.$inferInsert => ({
  name,
  title: `Seed term ${name}`,
  kind: name === "gsis_id" ? "identifier" : name.startsWith("y_") || name === "label_status" ? "label" : i % 2 ? "feature" : "concept",
  unit: "seed unit",
  // starter_threshold states the (fictional) league shape the way the real registry does
  formula:
    name === "starter_threshold"
      ? `seed formula: QB top ${L.starters.QB}, RB top ${L.starters.RB}, WR top ${L.starters.WR}, TE top ${L.starters.TE} by fantasy points that week; FLEX-worthy: RB/WR top 45`
      : name === "y_start"
        ? `seed formula: week N+1 rank in the top (teams x slots): top ${MODULES_SEED.streamTop.K} K and top ${MODULES_SEED.streamTop.DST} DST in this league`
        : `seed formula for ${name}`,
  explanation:
    name === "starter_threshold" ? `Seed explanation of ${name}: a typical ${L.teams}-team league.` : `Seed explanation of ${name}.`,
  verified: i % 4 === 0 ? `seed check ${name}` : null,
  modules: ["waiver_radar"],
  modelOutput: name === "xfp" || name === "fpoe",
});

function meta(variant: SeedVariant): (typeof s.siteMeta.$inferInsert)[] {
  const base: Record<string, string> = {
    code_version: "twm 0.0.0 (seed)",
    current_season: "2026",
    current_week: "3",
    data_as_of: "2026-09-29T07:00:00+00:00",
    data_through_season: "2026",
    data_through_week: "3",
    generated_at: SEED.generatedAt,
    git_sha: "seed",
    latest_list_season: variant === "full" ? "2026" : "",
    latest_list_week: variant === "full" ? "3" : "",
    latest_live_list_season: variant === "full" ? "2026" : "",
    latest_live_list_week: variant === "full" ? "3" : "",
    warehouse_built_at: "2026-09-29T06:00:00+00:00",
  };
  return Object.entries(base).map(([key, value]) => ({ key, value }));
}

type Db = NodePgDatabase<typeof s>;

export async function seed(db: Db, variant: SeedVariant): Promise<void> {
  await db.insert(s.siteMeta).values(meta(variant));
  await db.insert(s.glossary).values(SEED.glossaryNames.map(glossaryRow));
  await db.insert(s.pipelineRuns).values({
    runId: `seed-${variant}`,
    startedAt: new Date(SEED.generatedAt),
    finishedAt: new Date(SEED.generatedAt),
    status: "success",
    stage: "publish",
    gitSha: "seed",
    dataAsOf: new Date("2026-09-29T07:00:00Z"),
    notes: { seed: variant },
  });
  if (variant === "empty") return;

  await db.insert(s.dimTeam).values([...SEED.teams]);
  const ps = players();
  await db.insert(s.dimPlayer).values(ps);
  await db.insert(s.modelVersions).values([
    {
      modelVersion: "logit-seed-2024",
      module: "waiver_radar",
      model: "logit",
      label: "y_hit",
      trainingSeasons: [2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023],
      testSeason: 2024,
      featureList: ["snap_share_last"],
      params: { seed: true },
      createdAt: new Date("2026-09-01T00:00:00Z"),
    },
    {
      modelVersion: "logit-seed-2025",
      module: "waiver_radar",
      model: "logit",
      label: "y_hit",
      trainingSeasons: [2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024],
      testSeason: 2025,
      featureList: ["snap_share_last"],
      params: { seed: true },
      createdAt: new Date("2026-09-01T00:00:00Z"),
    },
    {
      modelVersion: "logit-seed-2026",
      module: "waiver_radar",
      model: "logit",
      label: "y_hit",
      trainingSeasons: [2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025],
      testSeason: 2026,
      featureList: ["snap_share_last"],
      params: { seed: true },
      createdAt: new Date("2026-09-01T00:00:00Z"),
    },
  ]);

  const byPos = (pos: string) => ps.filter((p) => p.position === pos);
  const lists: (typeof s.radarList.$inferInsert)[] = [];
  const picks: (typeof s.radarPick.$inferInsert)[] = [];
  const outcomes = new Map<string, typeof s.radarOutcome.$inferInsert>();

  const addWeek = (season: number, week: number, kind: "live" | "backtest", n: number, withChance: boolean, asOf: string) => {
    for (const pos of SEED.positions) {
      lists.push({
        season,
        week,
        position: pos,
        kind,
        asOf: new Date(asOf),
        modelVersion: season === 2024 ? "logit-seed-2024" : season === 2025 ? "logit-seed-2025" : "logit-seed-2026",
        generatedAt: new Date(kind === "live" ? SEED.generatedAt : "2026-09-28T12:00:00Z"),
        incomplete: false,
        nPool: 100 + SEED.positions.indexOf(pos),
        note: pos === "QB" && withChance ? "Seed note for QB: at QB this list is no better than last week's points." : null,
      });
      const pool = byPos(pos);
      // the featured RB is ranked 1st in 2026 and 3rd in the backtest weeks
      const order = pos === "RB" ? rotateFeatured(pool, season >= 2026 ? 0 : 2) : pool;
      for (let r = 1; r <= n; r++) {
        const p = order[r - 1];
        const chance = withChance ? Math.round((0.62 - r * 0.04) * 10000) / 10000 : null;
        picks.push({
          season,
          week,
          position: pos,
          kind,
          rank: r,
          gsisId: p.gsisId,
          team: p.team!,
          chance,
          chanceLow: chance === null ? null : Math.round((chance - 0.03) * 10000) / 10000,
          chanceHigh: chance === null ? null : Math.round((chance + 0.03) * 10000) / 10000,
          modelProb: Math.round((0.65 - r * 0.04) * 10000) / 10000,
          tier: chance === null ? null : chance >= 0.5 ? "must-add" : chance >= 0.25 ? "speculative" : "watch",
          reasons: withChance ? [`Seed reason A for ${p.displayName}`, `Seed reason B for rank ${r}`] : [],
        });
        const final = season < 2026;
        const hit = final ? (r + week) % 3 === 0 || r === 1 : null;
        outcomes.set(`${season}-${week}-${p.gsisId}`, {
          season,
          week,
          gsisId: p.gsisId,
          yHit: hit,
          ySustained: final ? r === 1 : null,
          labelStatus: final ? "final" : "pending",
          windowWeeks: [week + 1, week + 2, week + 3],
          // Postgres arrays may hold NULLs (a week he did not play); Drizzle types them as number[]
          windowRanks: final ? ([hit ? 7 : 40, null, 33] as number[]) : null,
          windowPoints: final ? ([hit ? 21.5 : 4.2, null, 6.1] as number[]) : null,
        });
      }
    }
  };

  for (const w of SEED.backtestWeeks) addWeek(w.season, w.week, "backtest", SEED.sizes.backtest, false, `${w.season}-10-0${w.week - 3}T14:00:00Z`);
  addWeek(SEED.reconWeek.season, SEED.reconWeek.week, "backtest", SEED.sizes.recon, true, "2026-09-22T14:00:00Z");
  addWeek(SEED.liveWeek.season, SEED.liveWeek.week, "live", SEED.sizes.live, true, SEED.asOfLive);

  await db.insert(s.radarList).values(lists);
  await db.insert(s.radarPick).values(picks);
  await db.insert(s.radarOutcome).values([...outcomes.values()]);

  // weekly rows for the featured running back (2025 W1-5, 2026 W1-2) and one receiver
  const weeks: (typeof s.playerWeekSummary.$inferInsert)[] = [];
  const row = (id: string, season: number, week: number, pos: string, team: string, k: number) => ({
    gsisId: id,
    season,
    week,
    team,
    position: pos,
    fantasyPoints: 6 + k * 2.5,
    snapShare: Math.min(1, 0.3 + k * 0.1),
    targetShare: 0.05 + k * 0.01,
    carryShare: pos === "RB" ? 0.2 + k * 0.05 : 0,
    xfp: k === 2 ? null : 7 + k * 2,
    fpoe: k === 2 ? null : Math.round((6 + k * 2.5 - (7 + k * 2)) * 100) / 100,
  });
  const f = ps.find((p) => p.gsisId === SEED.featured)!;
  for (let w = 1; w <= 5; w++) weeks.push(row(f.gsisId, 2025, w, "RB", f.team!, w));
  for (let w = 1; w <= 2; w++) weeks.push(row(f.gsisId, 2026, w, "RB", f.team!, w + 3));
  const wr = byPos("WR")[0];
  for (let w = 1; w <= 2; w++) weeks.push(row(wr.gsisId, 2026, w, "WR", wr.team!, w));
  await db.insert(s.playerWeekSummary).values(weeks);

  await db.insert(s.trackRecord).values(trackRows());
  await db.insert(s.tierStats).values(
    (
      [
        ["must-add", 0.5, 1, 300, 180],
        ["speculative", 0.25, 0.5, 1000, 380],
        ["watch", 0, 0.25, 700, 90],
      ] as const
    ).map(([tier, lo, hi, players, hits]) => ({
      module: "waiver_radar",
      model: "logit",
      label: "y_hit",
      week: 0,
      tier,
      seasonFrom: 2014,
      seasonTo: 2025,
      chanceLow: lo,
      chanceHigh: hi,
      probLow: lo,
      probHigh: hi,
      lists: 100,
      players,
      hits,
      hitRate: hits / players,
      perList: players / 100,
    })),
  );
  // the K and D/ST streamer and Regression Watch (tests/seed-modules.ts)
  await seedModules(db);
  // the Decision Report Card (tests/seed-decisions.ts)
  await seedDecisions(db);
  // the Hot-Seat Meter (tests/seed-hot-seat.ts; needs the decisions seed's coaches)
  await seedHotSeat(db);
}

function rotateFeatured<T extends { gsisId: string }>(pool: T[], at: number): T[] {
  const f = pool.find((p) => p.gsisId === SEED.featured)!;
  const rest = pool.filter((p) => p !== f);
  return [...rest.slice(0, at), f, ...rest.slice(at)];
}

function trackRows(): (typeof s.trackRecord.$inferInsert)[] {
  const out: (typeof s.trackRecord.$inferInsert)[] = [];
  const base = { module: "waiver_radar", seasonFrom: 2014, seasonTo: 2025, scopeValue: "" };
  const p10: Record<string, number> = { logit: 0.5, lgbm: 0.49, baseline_last_points: 0.4, baseline_snap_delta: 0.25 };
  for (const label of ["y_hit", "y_sustained"]) {
    for (const excl of [false, true]) {
      const shrink = (label === "y_sustained" ? 0.3 : 1) - (excl ? 0.02 : 0);
      for (const [model, v] of Object.entries(p10)) {
        out.push({
          ...base, model, label, metric: "p_at_10", scope: "pooled", exclRostered: excl,
          value: v * shrink, low: v * shrink - 0.02, high: v * shrink + 0.02,
          nLists: 100, nPositives: 900, nRows: 9000, nTopHits: Math.round(1000 * v * shrink), nTop: 1000,
        });
      }
      out.push({ ...base, model: "base_rate", label, metric: "base_rate", scope: "pooled", exclRostered: excl, value: 0.1 * shrink, nLists: 100, nPositives: 900, nRows: 9000 });
    }
  }
  for (const other of ["lgbm", "baseline_last_points", "baseline_snap_delta"]) {
    const d = p10.logit - p10[other];
    out.push({ ...base, model: "logit", label: "y_hit", metric: "p_at_10_diff", scope: "diff", scopeValue: other, exclRostered: false, value: d, low: d - 0.01, high: d + 0.01, nLists: 100 });
    out.push({ ...base, model: "logit", label: "y_hit", metric: "seasons_won", scope: "diff", scopeValue: other, exclRostered: false, value: 10, nLists: 12 });
  }
  for (const pos of ["QB", "RB", "WR", "TE"]) {
    for (const [model, v] of [["logit", 0.5], ["baseline_last_points", 0.4]] as const) {
      out.push({ ...base, model, label: "y_hit", metric: "p_at_10", scope: "position", scopeValue: pos, exclRostered: false, value: v, low: v - 0.03, high: v + 0.03, nLists: 25 });
    }
    out.push({ ...base, model: "logit", label: "y_hit", metric: "p_at_10_diff", scope: "position_diff", scopeValue: `${pos} vs baseline_last_points`, exclRostered: false, value: 0.1, low: 0.05, high: 0.15, nLists: 25 });
  }
  for (const [model, v] of [["logit", 0.48], ["baseline_ecr", 0.47]] as const) {
    out.push({ ...base, seasonFrom: 2020, model, label: "y_hit", metric: "p_at_10", scope: "experts", exclRostered: false, value: v, low: v - 0.03, high: v + 0.03, nLists: 50 });
  }
  out.push({ ...base, seasonFrom: 2020, model: "logit", label: "y_hit", metric: "p_at_10_diff", scope: "experts_diff", scopeValue: "baseline_ecr", exclRostered: false, value: 0.01, low: -0.01, high: 0.03, nLists: 50 });
  for (const [bin, pred, obs, n] of [["0.0-0.1", 0.03, 0.03, 5000], ["0.5-0.6", 0.55, 0.53, 400]] as const) {
    out.push({ ...base, model: "logit", label: "y_hit", metric: "mean_pred", scope: "calibration_fixed", scopeValue: bin, exclRostered: false, value: pred, nRows: n });
    out.push({ ...base, model: "logit", label: "y_hit", metric: "observed", scope: "calibration_fixed", scopeValue: bin, exclRostered: false, value: obs, nRows: n });
  }
  // per season (scope 'season', /track-record): twelve seasons, so the seasons table folds
  for (let season = 2014; season <= 2025; season++) {
    const one = { ...base, seasonFrom: season, seasonTo: season, scopeValue: String(season), label: "y_hit", scope: "season", exclRostered: false };
    const k = (season - 2014) / 100;
    out.push({ ...one, model: "logit", metric: "p_at_10", value: 0.44 + k, nLists: 60, nPositives: 800, nRows: 8000, nTopHits: Math.round(600 * (0.44 + k)), nTop: 600 });
    out.push({ ...one, model: "baseline_last_points", metric: "p_at_10", value: 0.38 + k / 2, nLists: 60, nPositives: 800, nRows: 8000, nTopHits: Math.round(600 * (0.38 + k / 2)), nTop: 600 });
    out.push({ ...one, model: "base_rate", metric: "base_rate", value: 0.1, nLists: 60, nPositives: 800, nRows: 8000 });
  }
  return out;
}
