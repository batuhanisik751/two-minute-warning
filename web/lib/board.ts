// The Cliff board's pure helpers (no database, no React): the "where we disagree" rule, drivers
// and outcomes in plain words, the calibration grid and the disagreement record. Unit-tested in
// tests/unit/board.test.ts. Every number they show comes from the published rows or lib/method.ts.
import { signedNum, type Driver } from "./hot-seat";
import { BOARD_BANDS, BOARD_CLIFF_DROP, BOARD_DISAGREE_TOP, BOARD_ECR_FIRST_SEASON, BOARD_MIN_GAMES } from "./method";
import { showThirdPartyRanks } from "./third-party";

export { driverEffect, parseDrivers, wholePct, type Driver } from "./hot-seat";

/** What the disagreement rule reads from a board row: his board rank by the Cliff chance (1 =
 *  highest), the experts' preseason rank at his position (NULL = not ranked) and his position
 *  rank by points per game last season. */
export type RankRow = { gsisId: string; cliffRank: number; ecrRank: number | null; posRankS: number };

/** The experts' expected fall in position rank: ECR rank minus last season's rank (positive = the
 *  experts rank him lower than he finished). NULL when they did not rank him at all. */
export function expectedFall(r: RankRow): number | null {
  return r.ecrRank === null ? null : r.ecrRank - r.posRankS;
}

/** True when the board has the experts' ranks (any row ranked; FantasyPros' preseason ECR exists
 *  from BOARD_ECR_FIRST_SEASON on). */
export function hasEcr(rows: RankRow[]): boolean {
  return rows.some((r) => r.ecrRank !== null);
}

/** The experts' top `top`: the players the experts' preseason ranking drops furthest below last
 *  season's finish (ECR rank minus last season's position rank, largest first). A player the
 *  experts did not rank at all counts as dropped furthest (the report scores him one past the
 *  page's last rank); among those, the better last season first. Ties: the id. Empty without
 *  the experts' ranks. */
export function expertsTop(rows: RankRow[], top: number = BOARD_DISAGREE_TOP): Set<string> {
  if (!hasEcr(rows)) return new Set();
  const sorted = [...rows].sort((a, b) => {
    const fa = expectedFall(a);
    const fb = expectedFall(b);
    if (fa === null || fb === null) {
      if (fa !== fb) return fa === null ? -1 : 1;
      if (a.posRankS !== b.posRankS) return a.posRankS - b.posRankS;
    } else if (fa !== fb) return fb - fa;
    return a.gsisId < b.gsisId ? -1 : a.gsisId > b.gsisId ? 1 : 0;
  });
  return new Set(sorted.slice(0, top).map((r) => r.gsisId));
}

export type Disagree = "model" | "experts";

/** Where we disagree, per player (the record's rule, BOARD_DISAGREE_TOP a side, on the whole
 *  board): 'model' = in our top 10 by the Cliff chance but not among the 10 the experts drop
 *  furthest; 'experts' = the reverse. Players on both lists or neither are not marked; nothing is
 *  marked without the experts' ranks. */
export function disagreements(rows: RankRow[], top: number = BOARD_DISAGREE_TOP): Map<string, Disagree> {
  const experts = expertsTop(rows, top);
  const out = new Map<string, Disagree>();
  if (!experts.size) return out;
  for (const r of rows) {
    const ours = r.cliffRank <= top;
    const theirs = experts.has(r.gsisId);
    if (ours && !theirs) out.set(r.gsisId, "model");
    else if (theirs && !ours) out.set(r.gsisId, "experts");
  }
  return out;
}

/** "RB12" style rank; "not ranked" for a NULL experts' rank. */
export function posRank(position: string, rank: number | null): string {
  return rank === null ? "not ranked" : `${position}${rank}`;
}

/** The marker in words, with the two ranks behind it. */
export function disagreeWords(d: Disagree, position: string, r: RankRow): { short: string; long: string } {
  const finish = `after he finished ${posRank(position, r.posRankS)} last season`;
  const ranks = r.ecrRank === null ? `the experts did not rank him ${finish}` : `the experts rank him ${posRank(position, r.ecrRank)} ${finish}`;
  return d === "model"
    ? { short: "We see more risk than the experts", long: `in our top ${BOARD_DISAGREE_TOP} for a Cliff; ${ranks}` }
    : { short: "The experts see more risk than we do", long: `${ranks}, among the ${BOARD_DISAGREE_TOP} they drop furthest; not in our top ${BOARD_DISAGREE_TOP}` };
}

/** The cards' link to /board: the experts' ranks are named only where they are shown
 *  (lib/third-party.ts). */
export function boardLinkText(show: boolean = showThirdPartyRanks()): string {
  return `Every player, ${show ? "the experts' ranks, " : ""}the drivers and how to read it`;
}

/** Whether a board season can have the experts' ranks at all. */
export const ecrSeason = (season: number) => season >= BOARD_ECR_FIRST_SEASON;

const ordinal = (n: number): string => {
  const t = n % 100;
  return `${n}${t >= 11 && t <= 13 ? "th" : (({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[n % 10] ?? "th")}`;
};
const yes = (v: number, on: string, off: string) => (v ? on : off);
const n1 = (v: number) => (Number.isInteger(v) ? String(v) : v.toFixed(1));
const share = (v: number) => `${Math.round(v * 100)}%`;
const POS_WORDS: Record<string, [string, string]> = {
  pos_rb: ["a running back", "not a running back"],
  pos_wr: ["a wide receiver", "not a wide receiver"],
  pos_te: ["a tight end", "not a tight end"],
};

/** The driver's value in plain words ("+12.6 points per game over the season before"). */
export function boardDriverValue(d: Driver): string {
  if (d.missing || d.value === null) return "not available (the model fills it in)";
  const v = d.value;
  const pos = POS_WORDS[d.feature];
  if (pos) return yes(v, pos[0], pos[1]);
  switch (d.feature) {
    case "ppg_change":
      return `${signedNum(v)} points per game against the season before`;
    case "ppg_s":
      return `${v.toFixed(1)} points per game last season`;
    case "pos_rank_s":
      return `${ordinal(Math.round(v))} by points per game at his position last season`;
    case "games_s":
      return `${n1(v)} games last season`;
    case "touches_per_game_s":
      return `${v.toFixed(1)} touches per game last season`;
    case "touches_s":
      return `${Math.round(v)} touches last season`;
    case "career_touches":
    case "career_targets":
      return `${Math.round(v).toLocaleString("en-US")} career ${d.feature === "career_touches" ? "touches" : "targets"}`;
    case "age":
      return `${v.toFixed(1)} years old`;
    case "age_curve_ratio":
      return `at his age, players at his position kept ${share(v)} of their points per game on average`;
    case "prior_seasons":
      return `${n1(v)} seasons in the league before last season`;
    case "depth_rank_s1":
      return `number ${n1(v)} at his spot on the week-1 depth chart`;
    case "team_change_s1":
      return yes(v, "on a new team's week-1 depth chart", "on the same team's week-1 depth chart");
    case "dc_absent":
      return yes(v, "on no week-1 depth chart", "on a week-1 depth chart");
    case "dc_team_chart_missing":
      return yes(v, "his team's week-1 depth chart was not available", "his team's week-1 depth chart was available");
    case "new_competitor_s1":
      return yes(v, "a newcomer at his position at or ahead of him on the depth chart", "no newcomer ahead of him on the depth chart");
    case "qb1_change_s1":
      return yes(v, "a new starting quarterback on the depth chart", "the same starting quarterback");
    case "hc_change_s1":
    case "hc_departure":
      return yes(v, "a new head coach", "the same head coach");
    case "vacated_targets_share_s1":
      return `${share(v)} of his team's targets went to players no longer on its depth chart`;
    case "vacated_carries_share_s1":
      return `${share(v)} of his team's carries went to players no longer on its depth chart`;
    case "snap_pct_s":
      return `on the field for ${share(v)} of his team's snaps`;
    case "snap_pct_trend":
      return `snap share ${signedNum(v * 100, 0)} points against the season before`;
    case "yards_per_touch_s":
      return `${v.toFixed(1)} yards per touch`;
    case "yards_per_touch_trend":
      return `${signedNum(v)} yards per touch against the two seasons before`;
    case "ngs_separation_s":
      return `${v.toFixed(1)} yards of separation (Next Gen Stats)`;
    case "ngs_separation_trend":
      return `${signedNum(v)} yards of separation against the season before`;
    case "ngs_ryoe_per_att_s":
      return `${signedNum(v, 2)} rush yards over expected per carry`;
    case "ngs_ryoe_trend":
      return `${signedNum(v, 2)} rush yards over expected per carry against the season before`;
    case "xfp_per_game_s":
      return `${v.toFixed(1)} expected fantasy points per game`;
    case "fpoe_per_game_s":
      return `${signedNum(v)} fantasy points per game over expected`;
    default:
      return Number.isInteger(v) ? String(v) : v.toFixed(2);
  }
}

export type BoardOutcome = { gamesS1: number | null; ppgS1: number | null; yCliff: boolean | null; yMissed: boolean | null; labelStatus: string };
export type OutcomeTone = "cliff" | "missed" | "held" | "pending";

/** What happened to a board's players: how many have a final outcome, how many of those were
 *  judged for a Cliff (played enough games) and had one, how many missed time, how many are
 *  pending (the board page and the time machine say it the same way). */
export function boardTally(rows: readonly { outcome: BoardOutcome | null }[]): { final: number; judged: number; cliffs: number; missed: number; pending: number } {
  const final = rows.filter((r) => r.outcome?.labelStatus === "final" && r.outcome.yMissed !== null);
  const judged = final.filter((r) => r.outcome?.yCliff !== null);
  return {
    final: final.length,
    judged: judged.length,
    cliffs: judged.filter((r) => r.outcome?.yCliff).length,
    missed: final.filter((r) => r.outcome?.yMissed).length,
    pending: rows.length - final.length,
  };
}

/** What happened in the board's season, in words: missed time (fewer than BOARD_MIN_GAMES games),
 *  a Cliff (a drop of at least BOARD_CLIFF_DROP in points per game), no Cliff, or pending. */
export function boardOutcomeWords(o: BoardOutcome | null, ppgS: number): { tone: OutcomeTone; short: string; long: string } {
  if (!o || o.labelStatus !== "final" || o.yMissed === null) {
    return { tone: "pending", short: "Pending", long: "pending until the season is over" };
  }
  const g = o.gamesS1 ?? 0;
  const games = `${g} ${g === 1 ? "game" : "games"}`;
  if (o.yMissed) return { tone: "missed", short: "Missed time", long: `${games} (fewer than ${BOARD_MIN_GAMES})` };
  const ppg = o.ppgS1 ?? 0;
  const change = ppgS > 0 ? (ppg - ppgS) / ppgS : 0;
  const delta = `${ppg.toFixed(1)} points per game in ${games} (${signedNum(change * 100, 0)}% from ${ppgS.toFixed(1)})`;
  return o.yCliff
    ? { tone: "cliff", short: "Cliff", long: `${delta}: a drop of ${Math.round(BOARD_CLIFF_DROP * 100)}% or more` }
    : { tone: "held", short: "No Cliff", long: delta };
}

/** The probability band of index i: "Under 10%", "10–25%", "50% or more". */
export function boardBandName(i: number): string {
  const lo = BOARD_BANDS[i];
  const hi = BOARD_BANDS[i + 1];
  if (lo === undefined) return "";
  if (i === 0) return `Under ${Math.round((hi ?? 1) * 100)}%`;
  if (hi === undefined) return `${Math.round(lo * 100)}% or more`;
  return `${Math.round(lo * 100)}–${Math.round(hi * 100)}%`;
}

/** One cell of the calibration check (the database's aggregation of reconstructed boards with a
 *  final outcome): which chance, its band, rows, how many had the outcome, the average chance. */
export type BoardCalCell = { chance: "cliff" | "missed"; band: number; n: number; hits: number; meanPred: number; boards: number };
export type BoardCalGroup = { chance: "cliff" | "missed"; cells: (BoardCalCell & { observed: number; bandName: string })[] };

/** The cells per chance (Cliff first), bands low to high; empty groups left out. */
export function boardCalibration(rows: BoardCalCell[]): BoardCalGroup[] {
  return (["cliff", "missed"] as const)
    .map((chance) => ({
      chance,
      cells: rows
        .filter((r) => r.chance === chance && r.n > 0)
        .sort((a, b) => a.band - b.band)
        .map((r) => ({ ...r, observed: r.hits / r.n, bandName: boardBandName(r.band) })),
    }))
    .filter((g) => g.cells.length > 0);
}

export type DisagreementRow = { variant: string; model: string; season: number; pickGroup: string; players: number; hits: number };
export type GroupTally = { players: number; hits: number; rate: number | null };
export type DisagreementRecord = {
  variant: string;
  model: string;
  from: number;
  to: number;
  groups: Record<"model_only" | "ecr_only" | "both", GroupTally>;
  seasons: { season: number; groups: Record<"model_only" | "ecr_only" | "both", GroupTally> }[];
};

const GROUPS = ["model_only", "ecr_only", "both"] as const;
const tally = (rows: DisagreementRow[], g: string): GroupTally => {
  const own = rows.filter((r) => r.pickGroup === g);
  const players = own.reduce((a, r) => a + r.players, 0);
  const hits = own.reduce((a, r) => a + r.hits, 0);
  return { players, hits, rate: players ? hits / players : null };
};
const tallies = (rows: DisagreementRow[]) => Object.fromEntries(GROUPS.map((g) => [g, tally(rows, g)])) as DisagreementRecord["groups"];

/** board_disagreement summed per variant over its seasons, and per season (oldest first). */
export function disagreementRecord(rows: DisagreementRow[], variant: string): DisagreementRecord | null {
  const own = rows.filter((r) => r.variant === variant);
  if (!own.length) return null;
  const seasons = [...new Set(own.map((r) => r.season))].sort((a, b) => a - b);
  return {
    variant,
    model: own[0].model,
    from: seasons[0],
    to: seasons[seasons.length - 1],
    groups: tallies(own),
    seasons: seasons.map((season) => ({ season, groups: tallies(own.filter((r) => r.season === season)) })),
  };
}

export type BoardTrackRow = { variant: string; slice: string; model: string; vs: string | null; metric: string; value: number | null; lo: number | null; hi: number | null; research: boolean };
export type BoardCell = { value: number; lo: number | null; hi: number | null };

/** One number of board_track_record (NULL when it is not published). */
export function boardCell(rows: BoardTrackRow[], q: { variant: string; slice: string; model: string; metric: string; vs?: string | null }): BoardCell | null {
  const r = rows.find((x) => x.variant === q.variant && x.slice === q.slice && x.model === q.model && x.metric === q.metric && (x.vs ?? null) === (q.vs ?? null));
  return r && r.value !== null ? { value: r.value, lo: r.lo, hi: r.hi } : null;
}

/** A paired difference in words from its interval: ahead, behind, or no clear difference. */
export function diffWords(c: BoardCell | null): "ahead" | "behind" | "no clear difference" | "not published" {
  if (!c) return "not published";
  if (c.lo !== null && c.lo > 0) return "ahead";
  if (c.hi !== null && c.hi < 0) return "behind";
  return "no clear difference";
}

/** "0.405 (0.354 to 0.463)"; a difference gets its sign. */
export function cellWords(c: BoardCell | null, diff = false, digits = 3): string {
  if (!c) return "not published";
  const f = (x: number) => (diff ? signedNum(x, digits) : x.toFixed(digits));
  return c.lo !== null && c.hi !== null ? `${f(c.value)} (${f(c.lo)} to ${f(c.hi)})` : f(c.value);
}

/** A /board link. */
export function boardHref(q: { season?: number; pos?: string; kind?: string }): string {
  const p = new URLSearchParams();
  if (q.season !== undefined) p.set("season", String(q.season));
  if (q.pos) p.set("pos", q.pos);
  if (q.kind) p.set("kind", q.kind);
  const s = p.toString();
  return s ? `/board?${s}` : "/board";
}

/** Live or reconstructed, in words. */
export function boardKindWords(kind: string): string {
  return kind === "live"
    ? "Live board: made before the season's first kickoff from the data public then, and never changed afterwards"
    : "Reconstructed board (backtest): what the model would have said on the eve of week 1, scored afterwards from the data public then";
}

/** The chance's name. */
export const CHANCE_NAMES = { cliff: "Cliff", missed: "Missed time" } as const;
