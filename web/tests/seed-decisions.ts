// The FICTIONAL seed of the Decision Report Card's tables (every coach, game and number is made
// up), loaded by tests/seed.ts for the "full" variant: dim_coach, decision_fourth,
// decision_two_point, decision_clock, coach_season and coach_week (both summed from the seeded
// decisions, so the pages' sums agree), decisions_track_record, the decisions_* site_meta keys
// and the decision glossary terms. Typed against db/schema.ts.
import type { NodePgDatabase } from "drizzle-orm/node-postgres";
import * as s from "../db/schema";

type Db = NodePgDatabase<typeof s>;
type Fourth = typeof s.decisionFourth.$inferInsert;
type Try = typeof s.decisionTwoPoint.$inferInsert;
type Clock = typeof s.decisionClock.$inferInsert;

export const DECISIONS_SEED = {
  /** the season graded on every run and its newest graded week (site_meta) */
  season: 2026,
  latestWeek: 3,
  history: "2024-2025",
  /** the complete season with the long worst-calls list (12 wrong fourth downs + 1 wrong try: folds) */
  past: 2025,
  coaches: [
    { id: "morgan-gridley", name: "Morgan Gridley", team: "NHG" },
    { id: "avery-o-hollis", name: "Avery O'Hollis", team: "SRO" },
    { id: "quinn-fairway", name: "Quinn Fairway", team: "EVP" },
    { id: "bartholomew-augustin-longsideline-iii", name: "Bartholomew-Augustin Longsideline III", team: "WLF" },
  ],
  /** an interim coach with 3 games in 2025: below the leaderboard's minimum, listed under it */
  interim: { id: "robin-interim", name: "Robin Interim", team: "WLF" },
  featured: "morgan-gridley",
  games: { 2024: 17, 2025: 17, 2026: 3 } as Record<number, number>,
  /** the home card's week-3 calls */
  weekWorstCoach: "quinn-fairway",
  weekBestCoach: "avery-o-hollis",
  /** decision glossary terms the pages ask for */
  glossaryNames: ["wp_lost", "wp_lost_per_game", "aggressiveness", "decision_grade", "own_wp", "p_convert", "p_fg_make", "punt_expected_wp", "pat_rate", "two_point_rate", "timeouts_unused", "half_passivity", "timeout_seconds_wasted", "passivity_ep_left"],
};

const OPP: Record<string, string> = { NHG: "SRO", SRO: "NHG", EVP: "WLF", WLF: "EVP" };
const r4 = (x: number) => Math.round(x * 10000) / 10000;
const gid = (season: number, week: number, team: string) => {
  const [a, b] = [team, OPP[team]].sort();
  return `${season}_${String(week).padStart(2, "0")}_${a}_${b}`;
};

type Kind = "wrong" | "go_went" | "punt_right" | "toss_up";

function fourth(season: number, week: number, coach: { id: string; team: string }, i: number, kind: Kind): Fourth {
  const yardline = kind === "punt_right" ? 72 : 22 + ((i * 7) % 50);
  const wpGo = r4(0.42 + 0.01 * (i % 20));
  const gap = kind === "toss_up" ? 0.006 : 0.021 + 0.004 * i;
  const goBest = kind !== "punt_right";
  const wpPunt = r4(goBest ? wpGo - gap : wpGo + gap);
  const wpFg = yardline <= 35 ? r4(Math.min(wpGo, wpPunt) - 0.01) : null;
  const best = goBest ? "go" : "punt";
  const chosen = kind === "wrong" ? "punt" : kind === "toss_up" ? "punt" : best;
  const wps: Record<string, number> = { go: wpGo, punt: wpPunt };
  return {
    gameId: gid(season, week, coach.team), playId: 1000 + i, season, week, seasonType: week > 18 ? "POST" : "REG",
    posteam: coach.team, defteam: OPP[coach.team], coachId: coach.id,
    qtr: 1 + (i % 4), quarterSeconds: 37 + ((i * 53) % 840), scoreDifferential: (i % 9) - 4,
    ydstogo: 1 + (i % 6), yardline100: yardline, chosen, recommended: best,
    grade: kind === "toss_up" ? "toss_up" : "clear", correct: chosen === best,
    wpGo, wpFg, wpPunt, wpLost: r4(Math.max(...Object.values(wps)) - wps[chosen]),
    pConvert: r4(0.35 + 0.03 * (i % 10)), pMake: wpFg === null ? null : 0.9,
    outcome: chosen === "go" ? (i % 2 ? "converted" : "failed") : "punted",
  };
}

function twoPoint(season: number, week: number, coach: { id: string; team: string }, i: number, kind: "wrong" | "two_went" | "toss_up"): Try {
  const wpKick = r4(0.5 + 0.01 * i);
  const wpTwoPoint = r4(kind === "toss_up" ? wpKick + 0.004 : wpKick + 0.03);
  const chosen = kind === "two_went" ? "two_point" : "kick";
  return {
    gameId: gid(season, week, coach.team), playId: 2000 + i, season, week, seasonType: "REG",
    posteam: coach.team, defteam: OPP[coach.team], coachId: coach.id,
    qtr: 4, quarterSeconds: 300 - 20 * i, scoreDifferential: -2 + (i % 3),
    chosen, recommended: "two_point", grade: kind === "toss_up" ? "toss_up" : "clear", correct: chosen === "two_point",
    wpKick, wpTwoPoint, wpLost: r4(Math.max(wpKick, wpTwoPoint) - (chosen === "kick" ? wpKick : wpTwoPoint)),
    result: chosen === "kick" ? "good" : "success",
  };
}

const C = DECISIONS_SEED.coaches;
const by = (id: string) => [...C, DECISIONS_SEED.interim].find((c) => c.id === id)!;

function decisions(): { fourths: Fourth[]; tries: Try[] } {
  const P = DECISIONS_SEED.past;
  const fourths: Fourth[] = [];
  for (let i = 0; i < 12; i++) fourths.push(fourth(P, 1 + i, C[i % 4], i, "wrong"));
  for (let i = 20; i < 23; i++) fourths.push(fourth(P, i - 7, C[i % 4], i, "go_went"));
  for (let i = 30; i < 32; i++) fourths.push(fourth(P, 14, C[i % 4], i, "punt_right"));
  for (let i = 40; i < 44; i++) fourths.push(fourth(P, 15, C[i % 4], i, "toss_up"));
  fourths.push(fourth(P, 16, DECISIONS_SEED.interim, 50, "punt_right"));
  fourths.push(fourth(2024, 5, C[0], 60, "wrong"), fourth(2024, 6, C[0], 61, "go_went"));
  const S = DECISIONS_SEED.season;
  const weeks: [number, string, string][] = [
    [1, "morgan-gridley", "bartholomew-augustin-longsideline-iii"],
    [2, "avery-o-hollis", "morgan-gridley"],
    [3, DECISIONS_SEED.weekWorstCoach, DECISIONS_SEED.weekBestCoach],
  ];
  for (const [w, worst, best] of weeks) {
    fourths.push(fourth(S, w, by(worst), 100 + w, "wrong"), fourth(S, w, by(best), 110 + w, "go_went"));
    fourths.push(fourth(S, w, C[3], 120 + w, "toss_up"));
  }
  const tries: Try[] = [
    twoPoint(P, 2, C[0], 0, "wrong"),
    twoPoint(P, 3, C[1], 1, "two_went"),
    twoPoint(P, 4, C[2], 2, "toss_up"),
    twoPoint(P, 4, C[3], 3, "toss_up"),
    twoPoint(S, 2, C[1], 4, "toss_up"),
  ];
  return { fourths, tries };
}

function clocks(): Clock[] {
  const P = DECISIONS_SEED.past;
  const base = { season: P, seasonType: "REG", down: 1, ydstogo: 10, playId: 3000 };
  const avery = { team: "SRO", opp: "NHG", coachId: "avery-o-hollis", week: 5, gameId: gid(P, 5, "SRO") };
  return [
    { ...base, ...avery, metric: "timeouts_unused", qtr: 4, quarterSeconds: 95, yardline100: 70, scoreDifferential: 5, timeouts: 2, isCase: true, amount: 2, wpLeft: null, desc: "Seed: the opponent runs up the middle.", detail: { team_margin: -5, last_clock: 30 } },
    { ...base, ...avery, metric: "seconds_wasted", qtr: 4, quarterSeconds: 80, yardline100: 66, scoreDifferential: 5, timeouts: 2, isCase: true, amount: 31, wpLeft: null, desc: "Seed: the opponent kneels.", detail: { team_margin: -5, missed_stops: 1 } },
    { ...base, metric: "half_passivity", week: 7, gameId: gid(P, 7, "EVP"), team: "EVP", opp: "WLF", coachId: "quinn-fairway", qtr: 2, quarterSeconds: 64, yardline100: 48, scoreDifferential: 3, timeouts: 2, isCase: true, amount: 1.6, wpLeft: 0.031, desc: "Seed: a run to midfield.", detail: { tail_snaps: 2 } },
    { ...base, metric: "timeouts_unused", week: 9, gameId: gid(P, 9, "NHG"), team: "NHG", opp: "SRO", coachId: "morgan-gridley", qtr: 4, quarterSeconds: 70, yardline100: 60, scoreDifferential: 3, timeouts: 0, isCase: false, amount: 0, wpLeft: null, desc: null, detail: { team_margin: -3 } },
  ];
}

type Counts = Omit<typeof s.coachWeek.$inferInsert, "gameId" | "coachId" | "season" | "week" | "seasonType" | "team" | "opp">;

function count(fs: Fourth[], ts: Try[]): Counts {
  const clear = fs.filter((f) => f.grade === "clear");
  const tc = ts.filter((t) => t.grade === "clear");
  const sum = (xs: { wpLost: number }[]) => r4(xs.reduce((a, x) => a + x.wpLost, 0));
  const fourthWpLost = sum(clear);
  const twoPointWpLost = sum(tc);
  return {
    fourthGraded: clear.length, fourthTossUps: fs.filter((f) => f.grade === "toss_up").length, fourthWpLost,
    fourthWrong: clear.filter((f) => !f.correct).length,
    goClear: clear.filter((f) => f.recommended === "go").length,
    goClearWent: clear.filter((f) => f.recommended === "go" && f.chosen === "go").length,
    went: fs.filter((f) => f.chosen === "go").length,
    twoPointGraded: tc.length, twoPointTossUps: ts.filter((t) => t.grade === "toss_up").length, twoPointWpLost,
    twoPointWrong: tc.filter((t) => !t.correct).length, wpLost: r4(fourthWpLost + twoPointWpLost),
  };
}

function seasons(fourths: Fourth[], tries: Try[], clock: Clock[]): (typeof s.coachSeason.$inferInsert)[] {
  const out: (typeof s.coachSeason.$inferInsert)[] = [];
  const rows: [number, { id: string; team: string }][] = [
    [2024, C[0]],
    ...C.map((c) => [DECISIONS_SEED.past, c] as [number, typeof c]),
    [DECISIONS_SEED.past, DECISIONS_SEED.interim],
    ...C.map((c) => [DECISIONS_SEED.season, c] as [number, typeof c]),
  ];
  for (const [season, c] of rows) {
    const games = c.id === DECISIONS_SEED.interim.id ? 3 : DECISIONS_SEED.games[season];
    const k = count(fourths.filter((f) => f.season === season && f.coachId === c.id), tries.filter((t) => t.season === season && t.coachId === c.id));
    const ck = (m: string) => clock.filter((x) => x.season === season && x.coachId === c.id && x.metric === m);
    const cases = (m: string) => ck(m).filter((x) => x.isCase);
    const amount = (m: string) => cases(m).reduce((a, x) => a + x.amount, 0);
    out.push({
      season, coachId: c.id, team: c.team, games, ...k,
      aggressiveness: k.goClear ? k.goClearWent / k.goClear : null,
      wpLostPerGame: k.wpLost / games,
      m1Candidates: ck("timeouts_unused").length, m1Cases: cases("timeouts_unused").length, m1TimeoutsLeft: amount("timeouts_unused"),
      m2Candidates: ck("half_passivity").length, m2Cases: cases("half_passivity").length, m2EpLeft: amount("half_passivity"),
      m2WpLeft: cases("half_passivity").reduce((a, x) => a + (x.wpLeft ?? 0), 0),
      m3DecisiveGames: ck("seconds_wasted").length, m3Cases: cases("seconds_wasted").length, m3SecondsWasted: amount("seconds_wasted"),
    });
  }
  return out;
}

function weeks(fourths: Fourth[], tries: Try[]): (typeof s.coachWeek.$inferInsert)[] {
  const keys = new Map<string, Fourth | Try>();
  for (const d of [...fourths, ...tries]) keys.set(`${d.gameId}|${d.coachId}`, d);
  return [...keys.values()].map((d) => ({
    gameId: d.gameId, coachId: d.coachId, season: d.season, week: d.week, seasonType: d.seasonType, team: d.posteam, opp: d.defteam,
    ...count(fourths.filter((f) => f.gameId === d.gameId && f.coachId === d.coachId), tries.filter((t) => t.gameId === d.gameId && t.coachId === d.coachId)),
  }));
}

type Track = typeof s.decisionsTrackRecord.$inferInsert;

function track(): Track[] {
  const out: Track[] = [];
  const add = (source: string, section: string, scope: string | null, subset: string | null, method: string | null, metric: string, value: number, n: number | null = 1000) => {
    const line = out.filter((r) => r.source === source).length + 1;
    out.push({ source, line, section, scope, subset, method, metric, value, lo: r4(value - 0.002), hi: r4(value + 0.002), n, nBlocks: 2 });
  };
  const W = "wp_backtest";
  for (const [m, b, l, e] of [["own", 0.151, 0.452, 0.005], ["nflfastr_wp", 0.162, 0.481, 0.007], ["nflfastr_vegas_wp", 0.149, 0.447, 0.004]] as const) {
    add(W, "metrics", "pooled", "2016-2025", m, "brier", b, 50000);
    add(W, "metrics", "pooled", "2016-2025", m, "log_loss", l, 50000);
    add(W, "metrics", "pooled", "2016-2025", m, "ece", e, 50000);
  }
  add(W, "difference", "pooled", "2016-2025", "own - nflfastr_wp", "brier", -0.011, 50000);
  add(W, "difference", "pooled", "2016-2025", "own - nflfastr_wp", "log_loss", -0.029, 50000);
  add(W, "difference", "pooled", "2016-2025", "own - nflfastr_vegas_wp", "brier", 0.002, 50000);
  add(W, "difference", "pooled", "2016-2025", "own - nflfastr_vegas_wp", "log_loss", 0.005, 50000);
  for (const [metric, limit, ours, before, vegas] of [["score_step_h1", 6, 3.4, 18, 3.7], ["score_step_h2", 8, 4.9, 16, 7.1], ["curvature", 4, 0.4, 20, 2.2], ["halftime_possession", 3, 1.3, 21, 3.7], ["monotone_violations", 0, 0, 0, null]] as const) {
    add(W, "smoothness", "limit", "-", "threshold", metric, limit, 0);
    for (const season of ["2016", "2025"]) add(W, "smoothness", "fold", season, "own", metric, season === "2016" ? ours - 0.1 : ours, 0);
    add(W, "smoothness", "fold", "2026", "g1_before", metric, before, 0);
    if (vegas !== null) add(W, "smoothness", "reference", "2016-2025", "nflfastr_vegas_wp", metric, vegas, 0);
    add(W, "smoothness", "validation", "2014-2015", "spline_sym_late_hand_over", metric, ours, 0);
  }
  // /track-record: the test seasons one by one (no interval on a season's own score, intervals on
  // its differences) and the reliability bins of our model
  for (let y = 2016; y <= 2025; y++) {
    const k = (y - 2016) / 1000;
    add(W, "season", String(y), "all plays", "own", "brier", 0.15 + k, 5000);
    add(W, "season", String(y), "all plays", "own", "log_loss", 0.45 + k, 5000);
    add(W, "season", String(y), "all plays", "own - nflfastr_wp", "log_loss", -0.03, 5000);
    add(W, "season", String(y), "all plays", "own - nflfastr_vegas_wp", "log_loss", 0.004, 5000);
  }
  for (const [bin, pred, obs] of [["0.0-0.1", 0.04, 0.035], ["0.4-0.5", 0.45, 0.46], ["0.9-1.0", 0.96, 0.97]] as const) {
    add(W, "reliability", "pooled", bin, "own", "mean_predicted", pred, 20000);
    add(W, "reliability", "pooled", bin, "own", "observed", obs, 20000);
  }
  const S = "submodels";
  for (const [section, scope, base, diff, metric, m, b, d, n] of [
    ["conversion", "down 4", "lookup", "model - lookup", "log_loss", 0.64, 0.646, -0.006, 1200],
    ["fieldgoal", "all kicks", "lookup", "model - lookup", "log_loss", 0.38, 0.387, -0.007, 2100],
    ["punt", "log score", "raw same-yardline history", "model - raw", "log_score", -2.12, -2.17, 0.045, 4700],
    ["tries", "pat", "naive", "model - naive", "log_loss", 0.144, 0.156, -0.012, 2500],
    ["tries", "two_point", "naive", "model - naive", "log_loss", 0.6926, 0.6922, 0.0004, 190],
  ] as const) {
    add(S, section, scope, "2016-2025", "model", metric, m, n);
    add(S, section, scope, "2016-2025", base, metric, b, n);
    add(S, section, scope, "2016-2025", diff, metric, d, n);
  }
  const B = "nfl4th_benchmark";
  for (const [scope, all, s24, s25, n] of [["all", 0.81, 0.8, 0.82, 700], ["clear", 0.93, 0.92, 0.94, 300], ["toss_up", 0.7, 0.69, 0.71, 400]] as const) {
    add(B, "agreement", scope, "2024", "nfl4th", "agree_rate", s24, n / 2);
    add(B, "agreement", scope, "2025", "nfl4th", "agree_rate", s25, n / 2);
    add(B, "agreement", scope, "all", "nfl4th", "agree_rate", all, n);
  }
  for (const [method, v] of [["ours", 0.44], ["nfl4th", 0.41], ["real", 0.23]] as const) add(B, "go_rate", "all", "all", method, "go_rate", v, 700);
  // the two-point line's interval straddles zero (no clear difference from the simple rate)
  const tp = out.find((r) => r.source === S && r.scope === "two_point" && r.method === "model - naive")!;
  tp.lo = -0.001;
  tp.hi = 0.0018;
  return out;
}

const glossaryRow = (name: string): typeof s.glossary.$inferInsert => ({
  name, title: `Seed term ${name}`, kind: "metric", unit: "seed unit", formula: `seed formula for ${name}`,
  explanation: `Seed explanation of ${name}.`, verified: null, modules: ["decisions"], modelOutput: false,
});

export async function seedDecisions(db: Db): Promise<void> {
  const D = DECISIONS_SEED;
  await db.insert(s.siteMeta).values([
    { key: "decisions_season", value: String(D.season) },
    { key: "decisions_latest_week", value: String(D.latestWeek) },
    { key: "decisions_history", value: D.history },
    { key: "decisions_version", value: "grading-seed" },
  ]);
  await db.insert(s.glossary).values(D.glossaryNames.map(glossaryRow));
  await db.insert(s.dimCoach).values([...D.coaches, D.interim].map((c) => ({ coachId: c.id, name: c.name })));
  const { fourths, tries } = decisions();
  const clock = clocks();
  await db.insert(s.decisionFourth).values(fourths);
  await db.insert(s.decisionTwoPoint).values(tries);
  await db.insert(s.decisionClock).values(clock);
  await db.insert(s.coachSeason).values(seasons(fourths, tries, clock));
  await db.insert(s.coachWeek).values(weeks(fourths, tries));
  await db.insert(s.decisionsTrackRecord).values(track());
}
