// The FICTIONAL seed of the coach-tendency tables (feature #10; every coach, team and number is
// made up), loaded by tests/seed.ts for the "full" variant, on the decisions seed's coaches and
// teams: Morgan Gridley (NHG) has 24 seasons, 2003-2026 (the seasons table folds; 2003-2005 have
// no PROE or no-huddle: not charted), Avery O'Hollis (SRO) 2024-2026, Quinn Fairway (EVP)
// 2025-2026 with a 2026 too short to rank (the partial season), the long-named coach (WLF)
// 2024-2026 and Robin Interim (WLF, 3 games of 2025). 2026 is the season in progress, through
// week 3. The persistence table makes shotgun the tendency that follows a coach to a new team and
// no-huddle a high r whose interval includes 0 (uncertain, as in the real table); the fantasy link
// has PROE's numbers. Typed against db/schema.ts.
import type { NodePgDatabase } from "drizzle-orm/node-postgres";
import { COMPARISONS, TENDENCY_METRICS } from "../lib/coach-tendencies";
import * as s from "../db/schema";
import { DECISIONS_SEED } from "./seed-decisions";

type Db = NodePgDatabase<typeof s>;
type Row = typeof s.coachTendencySeason.$inferInsert;

const C = DECISIONS_SEED.coaches;
export const TENDENCY_SEED = {
  season: 2026,
  throughWeek: 3,
  /** the coach with 24 seasons (folds) and the first three without PROE / no-huddle */
  veteran: C[0].id,
  firstSeason: 2003,
  /** the 2026 row below the ranking minimum: every percentile NULL */
  unranked: C[2].id,
  /** what follows a coach to a new team in the seeded persistence table */
  carries: ["shotgun_rate"],
  /** high r at a new team, but its 95% interval includes 0 */
  uncertain: ["no_huddle_rate"],
  proe: { same: 0.68, next: 0.29, xSd: 3.9, targets: 2.4, nextTargets: 0.9, ppr: 5.1 },
};
const T = TENDENCY_SEED;
const STINTS: [string, string, number, number][] = [
  [C[0].id, C[0].team, T.firstSeason, T.season],
  [C[1].id, C[1].team, 2024, T.season],
  [C[2].id, C[2].team, 2025, T.season],
  [C[3].id, C[3].team, 2024, T.season],
  [DECISIONS_SEED.interim.id, DECISIONS_SEED.interim.team, 2025, 2025],
];
const BASE: Record<string, number> = {
  neutral_pass_rate: 0.56, early_down_pass_rate: 0.5, proe: 0, neutral_sec_per_play: 30, no_huddle_rate: 0.06,
  shotgun_rate: 0.6, fourth_go_rate: 0.15, fourth_short_go_rate: 0.6,
};
const STEP: Record<string, number> = { proe: 1.7, neutral_sec_per_play: 0.9, no_huddle_rate: 0.02 };
const r4 = (x: number) => Math.round(x * 10000) / 10000;

function seasonRows(): Row[] {
  const rows: Row[] = [];
  for (const [coachId, team, from, to] of STINTS) {
    const k = STINTS.findIndex((x) => x[0] === coachId) - 2; // -2..2: each coach his own style
    for (let season = from; season <= to; season++) {
      const cur = season === T.season;
      const interim = coachId === DECISIONS_SEED.interim.id;
      const games = cur ? T.throughWeek : interim ? 3 : coachId === C[3].id && season === 2025 ? 14 : 17;
      const plays = cur ? (coachId === T.unranked ? 140 : 190 + 10 * k) : games * 62;
      for (const metric of TENDENCY_METRICS) {
        if ((metric === "proe" || metric === "no_huddle_rate") && season < 2006) continue;
        const step = STEP[metric] ?? 0.04;
        rows.push({
          coachId, team, season, metric, isCurrent: cur, throughWeek: cur ? T.throughWeek : coachId === C[3].id && season === 2025 ? 15 : 18, games, plays,
          value: r4(BASE[metric] + step * (k + ((season % 3) - 1) / 4)), sample: Math.round(plays * (metric.startsWith("fourth") ? 0.06 : 0.6)),
          leagueAvg: BASE[metric], percentile: null,
        });
      }
    }
  }
  // percentiles among each season's rows with at least 150 snaps (as the module ranks them)
  for (const key of new Set(rows.map((r) => `${r.season}|${r.metric}`))) {
    const mine = rows.filter((r) => `${r.season}|${r.metric}` === key && r.plays >= 150).sort((a, b) => a.value - b.value);
    mine.forEach((r, i) => (r.percentile = r4((100 * (i + 0.5)) / mine.length)));
  }
  return rows;
}

function careerRows(rows: Row[]): (typeof s.coachTendencyCareer.$inferInsert)[] {
  const out: (typeof s.coachTendencyCareer.$inferInsert)[] = [];
  const done = rows.filter((r) => !r.isCurrent);
  for (const coachId of new Set(done.map((r) => r.coachId))) {
    for (const metric of TENDENCY_METRICS) {
      const mine = done.filter((r) => r.coachId === coachId && r.metric === metric);
      if (!mine.length) continue;
      const n = mine.reduce((a, r) => a + r.sample, 0);
      const value = r4(mine.reduce((a, r) => a + r.value * r.sample, 0) / n);
      const leagueAvg = r4(mine.reduce((a, r) => a + r.leagueAvg * r.sample, 0) / n);
      out.push({
        coachId, metric, seasons: mine.length, firstSeason: Math.min(...mine.map((r) => r.season)), lastSeason: Math.max(...mine.map((r) => r.season)),
        teams: [...new Set(mine.map((r) => r.team))].sort().join("/"), value, sample: n, leagueAvg, vsLeague: r4(value - leagueAvg),
      });
    }
  }
  return out;
}

/** r per comparison: the coach and team stay / the coach moves / the team gets a new coach. */
const PERSIST: Record<string, [number, number | null, number]> = {
  neutral_pass_rate: [0.45, 0.2, 0.21], early_down_pass_rate: [0.48, 0.24, 0.22], proe: [0.51, 0.12, 0.1], neutral_sec_per_play: [0.52, 0.18, 0.14],
  no_huddle_rate: [0.66, 0.53, 0.15], shotgun_rate: [0.71, 0.46, 0.2], fourth_go_rate: [0.37, 0.08, 0.02], fourth_short_go_rate: [0.27, null, 0.08],
};

export async function seedCoachTendencies(db: Db): Promise<void> {
  const rows = seasonRows();
  await db.insert(s.coachTendencySeason).values(rows);
  await db.insert(s.coachTendencyCareer).values(careerRows(rows));
  await db.insert(s.coachTendencyPersistence).values(
    TENDENCY_METRICS.flatMap((metric) =>
      COMPARISONS.map((comparison, i) => {
        const r = PERSIST[metric][i];
        const nPairs = r === null ? 0 : [600, 55, 190][i];
        const wide = metric === "no_huddle_rate" && comparison === "same_coach_new_team";
        return { metric, comparison, nPairs, nSeasons: r === null ? 0 : 25, firstSeason: r === null ? null : 2000, lastSeason: r === null ? null : 2024, r, ciLow: r === null ? null : wide ? -0.05 : r4(r - 0.1), ciHigh: r === null ? null : wide ? 0.95 : r4(r + 0.1) };
      }),
    ),
  );
  const P = T.proe;
  await db.insert(s.coachTendencyFantasyLink).values(
    TENDENCY_METRICS.flatMap((metric, m) =>
      ["targets_per_game", "recv_ppr_per_game"].flatMap((target) =>
        ["same_season", "next_season"].map((horizon) => {
          const same = horizon === "same_season";
          const isProe = metric === "proe";
          const r = isProe ? (same ? P.same : P.next) : r4((same ? 0.3 : 0.1) - 0.05 * m);
          const y = isProe ? (target === "targets_per_game" ? (same ? P.targets : P.nextTargets) : same ? P.ppr : 2) : r4(r * 2);
          return { metric, target, horizon, n: same ? 410 : 380, nSeasons: 13, r, ciLow: r4(r - 0.06), ciHigh: r4(r + 0.06), xSd: isProe ? P.xSd : 0.05, yPerXSd: y };
        }),
      ),
    ),
  );
  await db.insert(s.siteMeta).values([
    { key: "coach_tendency_season", value: String(T.season) },
    { key: "coach_tendency_through_week", value: String(T.throughWeek) },
  ]);
}
