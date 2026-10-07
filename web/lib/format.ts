// Formatting helpers. Fixed formats, UTC and en-US digit grouping, so the same row renders
// the same text on every machine (no locale, no time zone of the server or the reader).

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const INT = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

const two = (n: number) => String(n).padStart(2, "0");

function toDate(iso: string): Date | null {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "Tue 22 Sep 2026" (UTC). */
export function fmtUtcDate(iso: string): string {
  const d = toDate(iso);
  if (!d) return "unknown date";
  return `${DAYS[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

/** "Tue 22 Sep 2026, 14:00 UTC". */
export function fmtUtc(iso: string): string {
  const d = toDate(iso);
  if (!d) return "unknown time";
  return `${fmtUtcDate(iso)}, ${two(d.getUTCHours())}:${two(d.getUTCMinutes())} UTC`;
}

/** 1234 -> "1,234". */
export function fmtInt(n: number): string {
  return INT.format(n);
}

/** The minus sign every signed number on the site uses (U+2212, not the ASCII hyphen). */
export const MINUS = "\u2212";

/** A number with `digits` decimals, a real minus sign and never "-0": -1.24 -> "−1.2", -0.04 -> "0.0". */
export function fmtNum(x: number, digits = 1): string {
  const s = Math.abs(x).toFixed(digits);
  return x < 0 && Number(s) !== 0 ? `${MINUS}${s}` : s;
}

/** A signed number: "+1.6", "−1.6"; a value that rounds to zero is "0.0" (no sign). */
export function signedNum(x: number, digits = 1): string {
  const s = fmtNum(x, digits);
  return x > 0 && Number(s) !== 0 ? `+${s}` : s;
}

/** A rate 0-1 as a percentage: 0.5601 -> "56%" (digits = 0) or "56.0%" (digits = 1); never "-0%". */
export function pct(x: number, digits = 0): string {
  return `${fmtNum(x * 100, digits)}%`;
}

/** A probability as a whole percent that never reads as certain: never "0%" for a positive
 *  chance, never "100%" below 1 (0.9962 -> ">99%"; the Hot-Seat's rule, lib/hot-seat.ts). */
export function wholePct(p: number): string {
  const v = Math.round(p * 100);
  if (v <= 0 && p > 0) return "<1%";
  if (v >= 100 && p < 1) return ">99%";
  return pct(p);
}

/** The priority cutoffs of a chance: must-add from 50%, speculative from 25%
 *  (src/twm/modules/waiver_radar/confidence.py MUST_ADD, SPECULATIVE; the streamer uses the same;
 *  tests/unit/format.test.ts reads them there). */
export const TIER_CUTOFFS = [0.25, 0.5] as const;

/** A pick's chance as a whole percentage that never rounds up across a priority cutoff: 0.4981
 *  would read "50%" next to "Speculative", so it is "49.8%" (one decimal, rounded down). */
export function chancePct(x: number): string {
  const crosses = TIER_CUTOFFS.some((c) => x < c && Math.round(x * 100) >= c * 100);
  return crosses ? `${(Math.floor(x * 1000) / 10).toFixed(1)}%` : pct(x);
}

/** A range of rates: (0.5263, 0.5933) -> "53-59%" with an en dash. */
export function pctRange(lo: number, hi: number, digits = 0): string {
  return `${fmtNum(lo * 100, digits)}–${fmtNum(hi * 100, digits)}%`;
}

/** A difference of two rates in percentage points: 0.0745 -> "+7.4 points", -0.0035 -> "−0.4 points";
 *  "-0.0" and "+0.0" read as noise: a zero is "0.0". */
export function points(diff: number, digits = 1): string {
  return `${signedNum(diff * 100, digits)} points`;
}

/** A signed interval of differences in points: (0.0624, 0.0868) -> "+6.2 to +8.7". */
export function pointsRange(lo: number, hi: number, digits = 1): string {
  return `${signedNum(lo * 100, digits)} to ${signedNum(hi * 100, digits)}`;
}

/** Fantasy points: one decimal, a real minus sign, never "-0.0". */
export function fmtPoints(x: number): string {
  return fmtNum(x, 1);
}

/** A share 0-1 as a whole percentage (a real minus sign, never "-0%"). */
export function fmtShare(x: number): string {
  const v = Math.round(x * 100);
  return `${v < 0 ? MINUS : ""}${Math.abs(v)}%`;
}

export function seasonWeek(season: number, week: number): string {
  return `${season} week ${week}`;
}

export type Tier = "must-add" | "speculative" | "watch";

export function tierLabel(tier: string | null): string {
  switch (tier) {
    case "must-add":
      return "Must-add";
    case "speculative":
      return "Speculative";
    case "watch":
      return "Watch";
    default:
      return "Not available";
  }
}

export type Outcome = "hit" | "no-hit" | "pending" | "unknown";

/** A pick's outcome from its radar_outcome row: final labels are hit / no hit, a pending
 *  label (its window's games are not all played) is pending, a missing row is unknown. */
export function outcomeOf(yHit: boolean | null, status: string | null): Outcome {
  if (status === "final" && yHit === true) return "hit";
  if (status === "final" && yHit === false) return "no-hit";
  if (status === "pending") return "pending";
  return "unknown";
}

export function outcomeLabel(o: Outcome): string {
  return { hit: "Hit", "no-hit": "No hit", pending: "Pending", unknown: "Not recorded" }[o];
}

export type KindLabel = { short: string; long: string };

/** `subject`: whose list it is ("the Radar", "the streamer", "Regression Watch"). The live words
 *  name no weekday: a list made at 02:00 UTC on Wednesday (Tuesday evening in the US) sits next
 *  to its "made on" time. */
export function kindLabel(kind: string, subject = "the Radar"): KindLabel {
  return kind === "live"
    ? { short: "Live", long: "Live list: made in real time, after its as-of and before the games it is about" }
    : { short: "Reconstructed", long: `Reconstructed list (backtest): what ${subject} would have said then` };
}

/** "Josh Downs" -> "josh-downs" (used only for element ids). */
export function slug(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

/** Weekly finishes in a label window, e.g. "W6 WR7, W8 WR5, W9 WR28" (ranks at his position;
 *  a week he did not play: "W7 did not play"; played without a stat line: "W7 no stat line",
 *  as radar_outcome stores them: rank NULL and points 0). */
export function windowSummary(
  weeks: number[] | null,
  ranks: (number | null)[] | null,
  pointsList: (number | null)[] | null,
  position: string,
): string | null {
  if (!weeks || weeks.length === 0) return null;
  return weeks
    .map((w, i) => {
      const r = ranks?.[i] ?? null;
      const p = pointsList?.[i] ?? null;
      if (r !== null) return `W${w} ${position}${r}`;
      if (p !== null) return `W${w} no stat line`;
      return `W${w} did not play`;
    })
    .join(", ");
}
