// The FICTIONAL seed of the Hot-Seat Meter's tables (every coach, team and number is made up),
// loaded by tests/seed.ts for the "full" variant: hot_seat_list, hot_seat_row, hot_seat_outcome,
// hot_seat_track_record, hot_seat_firings, the hot_seat model versions, extra fictional teams and
// coaches (a 12-coach list, so it folds), the hot_seat_* site_meta keys and the glossary terms the
// pages ask for. Typed against db/schema.ts. Uses the decisions seed's coaches (their coach pages
// then show both sections) and its interim coach.
import type { NodePgDatabase } from "drizzle-orm/node-postgres";
import * as s from "../db/schema";
import { DECISIONS_SEED } from "./seed-decisions";

type Db = NodePgDatabase<typeof s>;
type Row = typeof s.hotSeatRow.$inferInsert;
type Outcome = typeof s.hotSeatOutcome.$inferInsert;

const EXTRA_TEAMS = [
  ["BRK", "Bay Ridge Kestrels"], ["CLM", "Cold Lake Moose"], ["DSR", "Dune Shore Rams"], ["FGH", "Fog Harbor Herons"],
  ["GMV", "Green Meadow Vipers"], ["HPK", "High Peak Lynx"], ["IRN", "Iron Ridge Bison"], ["JDB", "Jade Bay Otters"],
] as const;

const NEW_COACHES = [
  ["sam-whitlock", "Sam Whitlock"], ["reese-calder", "Reese Calder"], ["jordan-vale", "Jordan Vale"], ["taylor-brook", "Taylor Brook"],
  ["morgan-ashby", "Morgan Ashby"], ["casey-lindqvist", "Casey Lindqvist"], ["devon-marsh", "Devon Marsh"],
] as const;

export const HOT_SEAT_SEED = {
  /** the current season: week 2 reconstructed, week 3 live (outcomes pending) */
  season: 2026,
  liveWeek: 3,
  reconWeek: 2,
  /** the complete past season: weekly lists of weeks 2-4 and the end-of-season snapshot (week 5) */
  past: 2025,
  pastWeeks: [2, 3, 4],
  lastWeek: 5,
  /** an older season with a week-2 list and the snapshot only */
  older: 2024,
  modelVersion: "hotseat-logit-seed-2026",
  /** let go after 2025 (end-of-season positive), and fired during 2025 after week 3 */
  firedAfter: "jordan-vale",
  firedDuring: "sam-whitlock",
  /** retired after 2025: another departure, not counted as let go */
  retired: "reese-calder",
  /** the interim coach (a row in 2025 week 4 and the snapshot) */
  interim: DECISIONS_SEED.interim.id,
  /** a coach with Hot-Seat rows in every list and decisions rows too */
  featured: DECISIONS_SEED.featured,
  glossaryNames: ["wins_vs_expected", "point_diff_per_game", "tenure_seasons", "is_first_year_coach", "prev_playoff_round", "is_interim"],
  features: ["wins_vs_expected", "point_diff_per_game", "tenure_seasons", "is_first_year_coach", "prev_playoff_round"],
};

/** 12 coaches with their teams, in the order of their (fictional) estimates: the decisions seed's
 *  four and eight new ones on the extra teams. In 2025 Sam Whitlock (BRK) is let go after week 3 and
 *  the interim coach takes over; Jordan Vale is let go after the season; Reese Calder retires. */
export function hotSeatCoaches(): { id: string; name: string; team: string }[] {
  const d = DECISIONS_SEED.coaches;
  const n = NEW_COACHES.map(([id, name], i) => ({ id, name, team: EXTRA_TEAMS[i][0] as string }));
  const harper = { id: "harper-quill", name: "Harper Quill", team: EXTRA_TEAMS[7][0] as string };
  const [sam, reese, jordan, taylor, ashby, casey, devon] = n;
  return [jordan, d[0], sam, d[1], reese, d[2], taylor, d[3], ashby, casey, devon, harper].map((c) => ({ id: c.id, name: c.name, team: c.team }));
}

/** The new coaches of 2026 (successors of the three who left after or during 2025). */
export const SUCCESSORS: Record<string, { id: string; name: string }> = {
  "jordan-vale": { id: "jamie-north", name: "Jamie North" },
  "sam-whitlock": { id: "pat-rowan", name: "Pat Rowan" },
  "reese-calder": { id: "lee-ostrander", name: "Lee Ostrander" },
};

const H = HOT_SEAT_SEED;
const r4 = (x: number) => Math.round(x * 10000) / 10000;
const BASE = [0.72, 0.55, 0.48, 0.41, 0.33, 0.27, 0.2, 0.16, 0.12, 0.08, 0.05, 0.004];
/** A Tuesday-like as-of of a fictional week (UTC 14:00). */
export const hotSeatAsOf = (season: number, week: number) => new Date(Date.UTC(season, 8, 1 + 7 * week, 14));

function drivers(i: number) {
  const third =
    i === 2
      ? { feature: "fourth_down_wp_lost_per_game", label: "Fourth-down WP lost per game (missing)", contribution: 0, value: null, missing: true }
      : { feature: "tenure_seasons", label: "Coach tenure", contribution: r4(-0.2 + 0.03 * i), value: 1 + i, missing: false };
  return [
    { feature: "wins_vs_expected", label: "Wins vs market expectation", contribution: r4(1.1 - 0.15 * i), value: r4(-1.5 + 0.3 * i), missing: false },
    { feature: "prev_playoff_round", label: "Previous season playoff result", contribution: r4(0.4 - 0.1 * i), value: i % 6, missing: false },
    third,
  ];
}

type Coach = { id: string; name: string; team: string };

/** Who coaches which team in a list: Sam Whitlock is gone after 2025 week 3, the interim coach has BRK. */
function staff(season: number, week: number, snapshot: string): (Coach & { interim: boolean })[] {
  return hotSeatCoaches().map((c) => {
    if (season === H.past && c.id === H.firedDuring && (week > 3 || snapshot === "end_of_season")) {
      return { id: H.interim, name: DECISIONS_SEED.interim.name, team: c.team, interim: true };
    }
    const next = SUCCESSORS[c.id];
    if (season === H.season && next) return { ...next, team: c.team, interim: false };
    return { ...c, interim: false };
  });
}

/** The rows of one list, ranked by probability (fictional, but a coach's line moves a little by week). */
export function hotSeatListRows(season: number, week: number, snapshot: string, kind: string): Row[] {
  const rows = staff(season, week, snapshot).map((c, i) => {
    const drift = (week - 3) * 0.02 * (i % 2 ? 1 : -1) + (season - H.season) * 0.01;
    const p = r4(Math.min(0.97, Math.max(0.003, BASE[i] + (i === 11 ? 0 : drift))));
    const games = snapshot === "end_of_season" ? H.lastWeek : week;
    const wins = Math.min(games, Math.round(games * (0.2 + 0.06 * i)));
    const expected = r4(games * 0.5);
    return {
      season, week, snapshot, kind, coachId: c.id, team: c.team, asOf: hotSeatAsOf(season, week), rank: 0, probability: p,
      isInterim: c.interim, drivers: drivers(i), regGamesPlayed: games, regWins: i === 5 ? wins + 0.5 : wins, expectedWins: expected,
      winsVsExpected: r4((i === 5 ? wins + 0.5 : wins) - expected), pointDiffPerGame: r4(-8 + 1.4 * i), tenureSeasons: 1 + i,
      divisionRank: 1 + (i % 4), prevSeasonWins: 4 + i, consecutiveLosingSeasons: i % 3, fourthDownWpLostPerGame: i === 2 ? null : r4(0.01 + 0.002 * i),
    } satisfies Row;
  });
  rows.sort((a, b) => b.probability - a.probability);
  rows.forEach((r, i) => (r.rank = i + 1));
  return rows;
}

/** The final outcome of a backtest row (2024: nobody let go), or pending (the current season). */
function outcomeOf(r: Row): Outcome {
  const key = { season: r.season, week: r.week, coachId: r.coachId };
  if (r.season === H.season) return { ...key, departed: null, censored: null, departureType: null, announced: null, labelStatus: "pending" };
  const no = { ...key, departed: false, censored: false, departureType: null, announced: null, labelStatus: "final" };
  if (r.season !== H.past) return no;
  if (r.coachId === H.firedAfter) return { ...no, departed: true, departureType: "fired_after_season", announced: "2026-01-05" };
  // no source gives the day: the labels imputed it, so it is published as NULL (G2.4)
  if (r.coachId === H.firedDuring) return { ...no, departed: true, departureType: "fired_in_season", announced: null };
  if (r.coachId === H.retired) return { ...no, censored: true, departureType: "retired", announced: "2026-01-08" };
  if (r.coachId === H.interim) return { ...no, censored: true, departureType: "interim_not_retained", announced: "2026-01-06" };
  return no;
}

/** Every seeded list: (season, week, snapshot, kind). */
export function hotSeatLists(): { season: number; week: number; snapshot: string; kind: string }[] {
  const w = (season: number, week: number, kind = "backtest") => ({ season, week, snapshot: "weekly", kind });
  return [
    w(H.older, 2),
    { season: H.older, week: H.lastWeek, snapshot: "end_of_season", kind: "backtest" },
    ...H.pastWeeks.map((week) => w(H.past, week)),
    { season: H.past, week: H.lastWeek, snapshot: "end_of_season", kind: "backtest" },
    w(H.season, H.reconWeek),
    w(H.season, H.liveWeek, "live"),
  ];
}

function track(): (typeof s.hotSeatTrackRecord.$inferInsert)[] {
  const out: (typeof s.hotSeatTrackRecord.$inferInsert)[] = [];
  const models: [string, number][] = [["logit", 0], ["hazard", -0.006], ["lgbm", -0.03], ["base_win_pct", -0.05], ["base_wins_vs_expected", -0.07]];
  for (const [model, d] of models) {
    // week_12: the backtest publishes only its top-5 hit rate (reports/hot_seat/backtest_metrics.csv)
    for (const [slice, n, pos] of [["all", 50, 9], ["week_12", 22, 4], ["end_of_season", 24, 3]] as const) {
      for (const [metric, v, w] of [["roc_auc", 0.79, 0.04], ["pr_auc", 0.44, 0.09], ["brier", 0.12, 0.012], ["top5_hit_rate", 0.52, 0.09]] as const) {
        if (metric === "top5_hit_rate" ? slice === "all" : slice === "week_12") continue;
        const value = r4(metric === "brier" ? v - d / 5 : v + d);
        out.push({ line: out.length + 1, variant: "main", model, prob: "prob", slice, metric, value, lo: r4(value - w), hi: r4(value + w), nRows: n, nPos: pos, nSeasons: 2 });
      }
    }
  }
  out.push({ line: out.length + 1, variant: "main", model: "logit", prob: "prob_iso", slice: "all", metric: "brier", value: 0.135, lo: 0.121, hi: 0.149, nRows: 50, nPos: 9, nSeasons: 2 });
  return out;
}

const firings = (): (typeof s.hotSeatFirings.$inferInsert)[] =>
  [2022, 2023, 2024, 2025].map((season, i) => ({
    season, positiveDepartures: season === H.past ? 2 : i + 1, firedInSeason: season === H.past ? 1 : 0, positivesWeek12: 1,
    positivesEndOfSeason: 1, censoredCoachSeasons: season === H.past ? 2 : 0, interimCoachSeasons: season === H.past ? 1 : 0,
  }));

function versions(): (typeof s.modelVersions.$inferInsert)[] {
  const base = { module: "hot_seat", model: "logit", label: "y", featureList: H.features, createdAt: new Date("2026-09-01T00:00:00Z") };
  const seasons = (to: number) => Array.from({ length: to - 2020 + 1 }, (_, i) => 2020 + i);
  return [
    ...[H.older, H.past].map((y) => ({ ...base, modelVersion: `hotseat-logit-seed-${y}`, trainingSeasons: seasons(y - 1), testSeason: y, params: { C: 1 } })),
    { ...base, modelVersion: H.modelVersion, trainingSeasons: seasons(H.season - 1), testSeason: H.season, params: { C: 0.1 } },
  ];
}

const glossaryRow = (name: string): typeof s.glossary.$inferInsert => ({
  name, title: `Seed term ${name}`, kind: name === "is_interim" ? "concept" : "feature", unit: "seed unit", formula: `seed formula for ${name}`,
  explanation: `Seed explanation of ${name}.`, verified: null, modules: ["hot_seat"], modelOutput: false,
});

export async function seedHotSeat(db: Db): Promise<void> {
  await db.insert(s.siteMeta).values([
    { key: "hot_seat_latest_list_season", value: String(H.season) },
    { key: "hot_seat_latest_list_week", value: String(H.liveWeek) },
    { key: "hot_seat_latest_live_list_season", value: String(H.season) },
    { key: "hot_seat_latest_live_list_week", value: String(H.liveWeek) },
  ]);
  await db.insert(s.glossary).values(H.glossaryNames.map(glossaryRow));
  await db.insert(s.dimTeam).values(
    EXTRA_TEAMS.map(([teamAbbr, teamName], i) => ({
      teamAbbr, teamName, teamNick: teamName.split(" ").slice(-1)[0], conference: i % 2 ? "AFC" : "NFC",
      division: `${i % 2 ? "AFC" : "NFC"} ${["East", "West"][i % 2]}`, color: "#3b4a5a", color2: "#d0d6dc",
    })),
  );
  const known = new Set<string>([...DECISIONS_SEED.coaches.map((c) => c.id), DECISIONS_SEED.interim.id]);
  const fresh = [...hotSeatCoaches(), ...Object.values(SUCCESSORS)].filter((c) => !known.has(c.id));
  await db.insert(s.dimCoach).values(fresh.map((c) => ({ coachId: c.id, name: c.name })));
  await db.insert(s.modelVersions).values(versions());
  const lists = hotSeatLists();
  const rows = lists.flatMap((l) => hotSeatListRows(l.season, l.week, l.snapshot, l.kind));
  await db.insert(s.hotSeatList).values(
    lists.map((l) => ({
      ...l, asOf: hotSeatAsOf(l.season, l.week), modelVersion: l.season === H.season ? H.modelVersion : `hotseat-logit-seed-${l.season}`,
      generatedAt: new Date(l.kind === "live" ? "2026-09-29T15:00:00Z" : "2026-09-30T02:00:00Z"), incomplete: false,
      nCoaches: rows.filter((r) => r.season === l.season && r.week === l.week && r.kind === l.kind).length, note: null,
    })),
  );
  await db.insert(s.hotSeatRow).values(rows);
  // hot_seat_outcome is keyed (season, week, coach): one per row of the season's lists
  const seen = new Set<string>();
  const outcomes = rows.map(outcomeOf).filter((o) => {
    const k = `${o.season}|${o.week}|${o.coachId}`;
    return seen.has(k) ? false : (seen.add(k), true);
  });
  await db.insert(s.hotSeatOutcome).values(outcomes);
  await db.insert(s.hotSeatTrackRecord).values(track());
  await db.insert(s.hotSeatFirings).values(firings());
}
