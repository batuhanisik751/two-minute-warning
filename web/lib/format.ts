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

/** A rate 0-1 as a percentage: 0.5601 -> "56%" (digits = 0) or "56.0%" (digits = 1). */
export function pct(x: number, digits = 0): string {
  return `${(x * 100).toFixed(digits)}%`;
}

/** A range of rates: (0.5263, 0.5933) -> "53-59%" with an en dash. */
export function pctRange(lo: number, hi: number, digits = 0): string {
  return `${(lo * 100).toFixed(digits)}–${(hi * 100).toFixed(digits)}%`;
}

/** A difference of two rates in percentage points: 0.0745 -> "+7.4 points". */
export function points(diff: number, digits = 1): string {
  const v = diff * 100;
  const s = v.toFixed(digits);
  // "-0.0" and "+0.0" read as noise: a zero is "0.0"
  if (Number(s) === 0) return `${(0).toFixed(digits)} points`;
  return `${v > 0 ? "+" : ""}${s} points`;
}

/** A signed interval of differences in points: (0.0624, 0.0868) -> "+6.2 to +8.7". */
export function pointsRange(lo: number, hi: number, digits = 1): string {
  const f = (x: number) => {
    const s = (x * 100).toFixed(digits);
    return Number(s) > 0 ? `+${s}` : s;
  };
  return `${f(lo)} to ${f(hi)}`;
}

/** Fantasy points: one decimal. */
export function fmtPoints(x: number): string {
  return x.toFixed(1);
}

/** A share 0-1 as a whole percentage. */
export function fmtShare(x: number): string {
  return `${Math.round(x * 100)}%`;
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

export function kindLabel(kind: string): KindLabel {
  return kind === "live"
    ? { short: "Live", long: "Live list: made in real time on the Tuesday" }
    : { short: "Reconstructed", long: "Reconstructed list (backtest): what the Radar would have said then" };
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
