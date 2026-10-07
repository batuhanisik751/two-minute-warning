// The Questionable page's pure helpers (feature #1, docs/questionable.md; unit-tested in
// tests/unit/questionable.test.ts). They only pick, order and word published rows: every number
// comes from the questionable_* tables (src/twm/publish/questionable.py).
import { pct, pctRange } from "@/lib/format";
import type { CalPoint } from "@/lib/track-record";

/** src/twm/modules/questionable/weekly.py INACTIVES_NOTE, word for word (a unit test checks). */
export const INACTIVES_NOTE = "The tag is not the final word: teams name their inactive players about 90 minutes before kickoff.";

/** When the lists fill in (docs/questionable.md "The live list"). */
export const WHEN_LISTS_FILL =
  "Lists fill in as teams post their final injury reports: on Friday for Sunday games, earlier in the week for Thursday and Saturday games. The site updates nightly.";

/** A play chance as a percentage that never reads as certain: under 0.5% -> "under 1%",
 * over 99.5% -> "over 99%" (a Doubtful cell can shrink to 0.4%, which would round to "0%"). */
export function chancePct(x: number): string {
  if (x < 0.005) return "under 1%";
  if (x > 0.995) return "over 99%";
  return pct(x);
}

export type QWeek = { season: number; week: number };

export type QSnapshot = QWeek & {
  asOf: string;
  generatedAt: string;
  modelVersion: string;
  nPlayers: number;
  source: string;
};

export type QRow = {
  gsisId: string;
  name: string;
  position: string;
  team: string;
  opponent: string | null;
  gameId: string | null;
  kickoff: string;
  reportStatus: string;
  practiceStatus: string | null;
  practice: string;
  missedPrev: boolean | null;
  playChance: number;
  playsN: number | null;
  playsMedian: number | null;
  playsDudRate: number | null;
  healthyMedian: number | null;
  healthyDudRate: number | null;
  seasonPpg: number | null;
  seasonGames: number;
};

export type QHistoryRow = { reportStatus: string; practice: string; seasons: string; n: number; played: number; playedRate: number };
export type QBacktestRow = { grouping: string; title: string; chosen: boolean; season: string; n: number; logLoss: number; brier: number; meanP: number; playedRate: number };
export type QCalibrationRow = { line: number; bucket: string; n: number; predicted: number | null; actual: number | null };
export type QLiveRow = { season: number; reportStatus: string; n: number; predicted: number | null; actual: number | null; pending: number | null; weeks: number | null };

/** A /questionable link. */
export function questionableHref(w?: QWeek): string {
  return w ? `/questionable?season=${w.season}&week=${w.week}` : "/questionable";
}

/** The week the page shows: the asked one when both parts are given, else the week whose
 *  games are next (site_meta questionable_season/_week), else the newest week with a
 *  snapshot, else none. */
export function chooseWeek(asked: { season: number | null; week: number | null }, current: QWeek | null, index: readonly QWeek[]): QWeek | null {
  if (asked.season !== null && asked.week !== null) return { season: asked.season, week: asked.week };
  return current ?? index[0] ?? null;
}

/** Whether a week with no list may still get one: the week whose games are next or a later
 *  regular-season week (18 at most) of the same season. A past or impossible week (week 99)
 *  never will, so its empty state must not say "yet". */
export function listMayCome(w: QWeek, current: QWeek | null): boolean {
  return current !== null && w.season === current.season && w.week >= current.week && w.week <= 18;
}

/** By kickoff (earliest first), then the chance he plays (highest first), then name. */
export function sortRows<T extends Pick<QRow, "kickoff" | "playChance" | "name">>(rows: readonly T[]): T[] {
  return [...rows].sort((a, b) => Date.parse(a.kickoff) - Date.parse(b.kickoff) || b.playChance - a.playChance || a.name.localeCompare(b.name));
}

const PRACTICE: Record<string, string> = { full: "Full", limited: "Limited", dnp: "Did not practice", none: "Not listed", all: "Any" };

/** The practice bucket in words. */
export function practiceLabel(p: string): string {
  return PRACTICE[p] ?? p;
}

/** "If he plays: usually 80% of his normal points (healthy players with similar averages:
 *  85%)", from his bucket's published line; null when the bucket is too small (no line). */
export function ifPlaysText(r: Pick<QRow, "playsMedian" | "healthyMedian">): string | null {
  if (r.playsMedian === null) return null;
  const healthy = r.healthyMedian === null ? "" : ` (healthy players with similar averages: ${pct(r.healthyMedian)})`;
  return `If he plays: usually ${pct(r.playsMedian)} of his normal points${healthy}`;
}

export const STATUS_ORDER = ["Questionable", "Doubtful", "Out"] as const;
export const PRACTICE_ORDER = ["all", "full", "limited", "dnp", "none"] as const;

/** The history table's rows in the page's order: by tag, the total first, then by practice. */
export function historyRows(rows: readonly QHistoryRow[]): QHistoryRow[] {
  const rank = (xs: readonly string[], x: string) => (xs.indexOf(x) === -1 ? xs.length : xs.indexOf(x));
  return [...rows].sort((a, b) => rank(STATUS_ORDER, a.reportStatus) - rank(STATUS_ORDER, b.reportStatus) || rank(PRACTICE_ORDER, a.practice) - rank(PRACTICE_ORDER, b.practice));
}

/** The pooled walk-forward score ('all' seasons) of the chosen grouping and of the baseline
 *  (grouping 'status': the overall rate for the tag); null when either is missing. */
export function backtestScore(rows: readonly QBacktestRow[]): { chosen: QBacktestRow; baseline: QBacktestRow; seasons: string[] } | null {
  const chosen = rows.find((r) => r.chosen && r.season === "all");
  const baseline = rows.find((r) => r.grouping === "status" && r.season === "all");
  if (!chosen || !baseline) return null;
  const seasons = [...new Set(rows.filter((r) => r.season !== "all").map((r) => r.season))].sort();
  return { chosen, baseline, seasons };
}

/** A calibration bucket's label ("<30%", "30-50%", "85%+") as a probability range. */
export function bucketRange(bucket: string): { lo: number; hi: number } | null {
  let m = /^<(\d{1,3})%$/.exec(bucket);
  if (m) return { lo: 0, hi: Number(m[1]) / 100 };
  m = /^(\d{1,3})-(\d{1,3})%$/.exec(bucket);
  if (m) return { lo: Number(m[1]) / 100, hi: Number(m[2]) / 100 };
  m = /^(\d{1,3})%\+$/.exec(bucket);
  return m ? { lo: Number(m[1]) / 100, hi: 1 } : null;
}

/** A bucket in the calibration figure's label style: "<30%" -> "0–30%", "85%+" -> "85–100%". */
export function bucketLabel(bucket: string): string {
  const r = bucketRange(bucket);
  return r ? pctRange(r.lo, r.hi) : bucket;
}

/** The calibration plot's points: buckets with rows and published numbers only. */
export function calibrationPoints(rows: readonly QCalibrationRow[]): CalPoint[] {
  const out: CalPoint[] = [];
  for (const r of [...rows].sort((a, b) => a.line - b.line)) {
    const bin = bucketRange(r.bucket);
    if (!bin || r.n === 0 || r.actual === null) continue;
    out.push({ ...bin, key: r.bucket, predicted: r.predicted, observed: r.actual, observedLo: null, observedHi: null, n: r.n });
  }
  return out;
}

/** The live record's rows by tag ('all' first), or null when nothing is published. */
export function liveRecord(rows: readonly QLiveRow[], season: number | null): { all: QLiveRow; byStatus: QLiveRow[] } | null {
  const mine = rows.filter((r) => season === null || r.season === season);
  const all = mine.find((r) => r.reportStatus === "all");
  if (!all) return null;
  return { all, byStatus: STATUS_ORDER.slice(0, 2).flatMap((s) => mine.filter((r) => r.reportStatus === s)) };
}

/** "at CIN" (his team is the visitor) or "vs BAL", from nflverse's game_id
 *  (season_week_away_home); "vs" when the game id says nothing; null without an opponent. */
export function opponentText(team: string, opponent: string | null, gameId: string | null): string | null {
  if (!opponent) return null;
  const parts = gameId?.split("_") ?? [];
  const away = parts.length === 4 && parts[2] === team && parts[3] === opponent;
  return `${away ? "at" : "vs"} ${opponent}`;
}

/** The rows grouped by kickoff (earliest first), each group sorted by sortRows. */
export function kickoffGroups<T extends Pick<QRow, "kickoff" | "playChance" | "name">>(rows: readonly T[]): { kickoff: string; rows: T[] }[] {
  const out: { kickoff: string; rows: T[] }[] = [];
  for (const r of sortRows(rows)) {
    const last = out[out.length - 1];
    if (last && Date.parse(last.kickoff) === Date.parse(r.kickoff)) last.rows.push(r);
    else out.push({ kickoff: r.kickoff, rows: [r] });
  }
  return out;
}
