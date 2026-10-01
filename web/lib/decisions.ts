// The Decision Report Card's words and small rules (pure; unit-tested in
// tests/unit/decisions.test.ts). Every number they format comes from the database
// (decision_fourth, decision_two_point, decision_clock, coach_season); the only method
// constants are lib/method.ts's.
import { LEADERBOARD_MIN_GAMES } from "@/lib/method";

export type DecisionKind = "fourth" | "two_point";

/** One option of a decision with its win probability (0-1). */
export type OptionWp = { option: string; wp: number };

/** A graded decision as the pages show it (a fourth down or a try after a touchdown). */
export type DecisionRow = {
  kind: DecisionKind;
  gameId: string;
  playId: number;
  season: number;
  week: number;
  seasonType: string;
  posteam: string;
  defteam: string;
  coachId: string;
  coachName: string;
  qtr: number;
  quarterSeconds: number;
  scoreDifferential: number;
  /** fourth downs only (NULL for tries) */
  ydstogo: number | null;
  yardline100: number | null;
  chosen: string;
  recommended: string;
  grade: string;
  correct: boolean;
  /** the options that existed, best first */
  options: OptionWp[];
  wpLost: number;
  pConvert: number | null;
  pMake: number | null;
  outcome: string | null;
};

const ORD = ["th", "st", "nd", "rd"];
/** 1 -> "1st", 4 -> "4th", 11 -> "11th". */
export function ordinal(n: number): string {
  const v = n % 100;
  return `${n}${ORD[(v - 20) % 10] ?? ORD[v] ?? ORD[0]}`;
}

/** Where the ball is, from the offense's view: "the opponent's 38", "midfield", "its own 25". */
export function fieldWords(yardline100: number): string {
  if (yardline100 === 50) return "midfield";
  return yardline100 < 50 ? `the opponent's ${yardline100}` : `its own ${100 - yardline100}`;
}

/** "4th and 2", or "4th and goal" when the line to gain is the goal line. */
export function downWords(down: number, ydstogo: number, yardline100: number): string {
  return `${ordinal(down)} and ${ydstogo >= yardline100 ? "goal" : ydstogo}`;
}

/** The score from the team's view: "up 7", "down 3", "tied". */
export function scoreWords(diff: number): string {
  if (diff === 0) return "tied";
  return diff > 0 ? `up ${diff}` : `down ${-diff}`;
}

/** "6:12 left in Q4"; overtime periods: "6:12 left in overtime". */
export function clockWords(qtr: number, quarterSeconds: number): string {
  const s = Math.max(0, Math.round(quarterSeconds));
  const mmss = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  return `${mmss} left in ${qtr >= 5 ? "overtime" : `Q${qtr}`}`;
}

/** A decision's situation in words: "4th and 2 at the opponent's 38, down 3, 6:12 left in Q4";
 *  a try: "Try after a touchdown, down 1 before the try, 1:12 left in Q4". */
export function situationWords(d: Pick<DecisionRow, "kind" | "qtr" | "quarterSeconds" | "scoreDifferential" | "ydstogo" | "yardline100">): string {
  const score = scoreWords(d.scoreDifferential);
  const clock = clockWords(d.qtr, d.quarterSeconds);
  if (d.kind === "two_point" || d.ydstogo === null || d.yardline100 === null) {
    return `Try after a touchdown, ${score} before the try, ${clock}`;
  }
  return `${downWords(4, d.ydstogo, d.yardline100)} at ${fieldWords(d.yardline100)}, ${score}, ${clock}`;
}

const OPTION_NAMES: Record<string, string> = {
  go: "Go for it",
  field_goal: "Field goal",
  punt: "Punt",
  kick: "Kick the extra point",
  two_point: "Go for two",
};

/** "go" -> "Go for it", "two_point" -> "Go for two". */
export function optionName(option: string): string {
  return OPTION_NAMES[option] ?? option;
}

/** A win probability 0-1 as a percentage with one decimal: 0.4731 -> "47.3%". */
export function wpPct(wp: number): string {
  return `${(wp * 100).toFixed(1)}%`;
}

/** WP points (0-1 units shown x 100, one decimal): 0.0421 -> "4.2". */
export function wpPoints(x: number, digits = 1): string {
  const s = (x * 100).toFixed(digits);
  return Number(s) === 0 ? (0).toFixed(digits) : s;
}

/** The options that existed, best first (ties keep the grading's order: go, field goal, punt). */
export function optionsOf(wps: Record<string, number | null>): OptionWp[] {
  return Object.entries(wps)
    .filter((e): e is [string, number] => e[1] !== null)
    .map(([option, wp]) => ({ option, wp }))
    .sort((a, b) => b.wp - a.wp);
}

/** How much the chosen option beat the best of the others, in WP (0-1): the gain of a right
 *  call. NULL when the chosen option is missing or it is the only one. */
export function gainOverNext(d: Pick<DecisionRow, "chosen" | "options">): number | null {
  const mine = d.options.find((o) => o.option === d.chosen);
  const others = d.options.filter((o) => o.option !== d.chosen);
  if (!mine || !others.length) return null;
  return mine.wp - Math.max(...others.map((o) => o.wp));
}

/** "Against convention": a clear call where the aggressive option was best and the coach took
 *  it (went for it on fourth down, went for two). */
export function againstConvention(d: Pick<DecisionRow, "grade" | "chosen" | "recommended">): boolean {
  return d.grade === "clear" && d.chosen === d.recommended && (d.chosen === "go" || d.chosen === "two_point");
}

const OUTCOMES: Record<string, string> = {
  punted: "punted",
  made: "the kick was good",
  missed: "the kick missed",
  blocked: "the kick was blocked",
  converted: "converted",
  failed: "failed",
  touchdown: "touchdown",
  good: "the kick was good",
  success: "converted",
  failure: "failed",
};

/** What happened after the snap, in words (NULL: not recorded). nflverse's extra-point result
 *  "failed" is a missed kick; a fourth down's "failed" is a failed try to convert. */
export function outcomeWords(outcome: string | null, kind: DecisionKind = "fourth"): string | null {
  if (!outcome) return null;
  if (kind === "two_point" && outcome === "failed") return "the kick missed";
  return OUTCOMES[outcome] ?? outcome.replace(/_/g, " ");
}

/** "week 5"; playoff games: "playoffs, week 20" (nflverse numbers playoff weeks on). */
export function weekWords(week: number, seasonType: string): string {
  return seasonType === "REG" ? `week ${week}` : `playoffs, week ${week}`;
}

/** The leaderboard's minimum games for a season: LEADERBOARD_MIN_GAMES, or early in a season
 *  the most games any coach has (the report's rule). */
export function leaderboardMinGames(games: readonly number[]): number {
  return Math.min(LEADERBOARD_MIN_GAMES, games.length ? Math.max(...games) : 0);
}

export type LeaderRow = { coachId: string; name: string; games: number; wpLostPerGame: number };

/** The ranked coaches (at least the minimum games; least WP lost per game first, then name) and
 *  the coaches with fewer games (by name). */
export function rankCoaches<T extends LeaderRow>(rows: readonly T[]): { ranked: T[]; fewer: T[]; minGames: number } {
  const minGames = leaderboardMinGames(rows.map((r) => r.games));
  const byName = (a: T, b: T) => a.name.localeCompare(b.name, "en") || a.coachId.localeCompare(b.coachId);
  const ranked = rows.filter((r) => r.games >= minGames).sort((a, b) => a.wpLostPerGame - b.wpLostPerGame || byName(a, b));
  const fewer = rows.filter((r) => r.games < minGames).sort(byName);
  return { ranked, fewer, minGames };
}

/** The aggressiveness index as words: "3 of 7 (43%)"; "no clear go" without such a decision. */
export function aggressivenessWords(went: number, clear: number): string {
  if (clear <= 0) return "no clear go";
  return `${went} of ${clear} (${Math.round((went / clear) * 100)}%)`;
}

export type ClockMetric = "timeouts_unused" | "half_passivity" | "seconds_wasted";

export const CLOCK_TITLES: Record<ClockMetric, string> = {
  timeouts_unused: "Timeouts unused in a lost one-score game",
  half_passivity: "End-of-half passivity",
  seconds_wasted: "Seconds wasted with timeouts in hand",
};

/** The glossary entry of each clock metric (the registry names the third one differently). */
export const CLOCK_TERMS: Record<ClockMetric, string> = {
  timeouts_unused: "timeouts_unused",
  half_passivity: "half_passivity",
  seconds_wasted: "timeout_seconds_wasted",
};

export type ClockCase = {
  metric: ClockMetric;
  qtr: number;
  quarterSeconds: number;
  down: number | null;
  ydstogo: number | null;
  yardline100: number | null;
  scoreDifferential: number;
  timeouts: number;
  amount: number;
};

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** A clock case's key snap in words. Metrics 1 and 3 describe the OPPONENT's snap (its down and
 *  field position; the score is turned to the team's view); metric 2 the team's own snap. */
export function clockSituation(c: ClockCase): string {
  const opp = c.metric !== "half_passivity";
  const field =
    c.down !== null && c.ydstogo !== null && c.yardline100 !== null
      ? `${downWords(c.down, c.ydstogo, c.yardline100)} at ${fieldWords(c.yardline100)}`
      : null;
  const ball = opp ? `Opponent's ball${field ? `: ${field}` : ""}` : (field ?? "Its ball");
  const score = scoreWords(opp ? -c.scoreDifferential : c.scoreDifferential);
  return `${ball}, ${score}, ${clockWords(c.qtr, c.quarterSeconds)}, ${plural(c.timeouts, "timeout", "timeouts")} in hand`;
}

/** The case's value in words: "2 timeouts unused", "1.8 expected points left", "39 seconds wasted". */
export function clockAmountWords(metric: ClockMetric, amount: number): string {
  if (metric === "timeouts_unused") return `${plural(Math.round(amount), "timeout", "timeouts")} unused`;
  if (metric === "half_passivity") return `${amount.toFixed(1)} expected points left`;
  return `${plural(Math.round(amount), "second", "seconds")} wasted`;
}

/** The coach_season / coach_week counts a leaderboard row needs. */
export type CoachCounts = {
  fourthGraded: number;
  fourthTossUps: number;
  fourthWrong: number;
  twoPointGraded: number;
  twoPointTossUps: number;
  twoPointWrong: number;
  m1Cases: number;
  m2Cases: number;
  m3Cases: number;
};

/** A coach-season's totals: decisions graded (clear calls and toss-ups, fourth downs and tries),
 *  clear calls, wrong clear calls, clock cases (the three metrics). */
export function coachTotals(c: CoachCounts): { decisions: number; clear: number; tossUps: number; wrong: number; clockCases: number } {
  const clear = c.fourthGraded + c.twoPointGraded;
  const tossUps = c.fourthTossUps + c.twoPointTossUps;
  return { decisions: clear + tossUps, clear, tossUps, wrong: c.fourthWrong + c.twoPointWrong, clockCases: c.m1Cases + c.m2Cases + c.m3Cases };
}

/** Sums of a season's coach rows (the league's season): every count, plus the clear go calls. */
export function leagueTotals<T extends CoachCounts & { goClear: number; goClearWent: number; games: number; wpLost: number }>(rows: readonly T[]) {
  const sum = (f: (r: T) => number) => rows.reduce((a, r) => a + f(r), 0);
  return {
    coaches: rows.length,
    teamGames: sum((r) => r.games),
    fourthGraded: sum((r) => r.fourthGraded),
    fourthTossUps: sum((r) => r.fourthTossUps),
    twoPointGraded: sum((r) => r.twoPointGraded),
    twoPointTossUps: sum((r) => r.twoPointTossUps),
    wrong: sum((r) => r.fourthWrong + r.twoPointWrong),
    goClear: sum((r) => r.goClear),
    goClearWent: sum((r) => r.goClearWent),
    wpLost: sum((r) => r.wpLost),
    clockCases: sum((r) => r.m1Cases + r.m2Cases + r.m3Cases),
  };
}

/** A coach id is dim_coach's slug of the name ("andy-reid", "bill-o-brien"). */
export const COACH_ID_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
export function isCoachId(id: string): boolean {
  return id.length <= 64 && COACH_ID_PATTERN.test(id);
}
