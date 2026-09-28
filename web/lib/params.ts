// Reading /waivers and /player query strings. Anything unexpected falls back to a default
// (never an error page): these are links people share and edit by hand.
import { POSITIONS, type Position } from "./method";

type Raw = string | string[] | undefined;

const first = (v: Raw): string | undefined => (Array.isArray(v) ? v[0] : v);

export function parsePosition(v: Raw, fallback: Position = POSITIONS[0]): Position {
  const s = first(v)?.toUpperCase();
  return (POSITIONS as readonly string[]).includes(s ?? "") ? (s as Position) : fallback;
}

/** A positive integer below 10,000 (seasons, weeks), else null. */
export function parseInt4(v: Raw): number | null {
  const s = first(v);
  if (!s || !/^\d{1,4}$/.test(s)) return null;
  const n = Number(s);
  return n > 0 ? n : null;
}

export function parseKind(v: Raw): "live" | "backtest" | null {
  const s = first(v);
  return s === "live" || s === "backtest" ? s : null;
}

/** nflverse's gsis_id: "00-0038997" (or the older three-letter form "ABC123456"), the same
 *  pattern `twm publish` enforces (src/twm/publish/collect.py GSIS_PATTERN). */
export const GSIS_PATTERN = /^(00-\d{7}|[A-Z]{3}\d{6})$/;

export function isGsisId(id: string): boolean {
  return GSIS_PATTERN.test(id);
}

export type ListKey = { season: number; week: number; kind: "live" | "backtest" };

/** Which list /waivers shows: the requested (season, week[, kind]) when it exists; else the
 *  requested season's latest week; else the newest list. Live beats reconstructed for the
 *  same week unless kind=backtest is asked for. `available` is newest first. */
export function chooseList(
  available: ListKey[],
  season: number | null,
  week: number | null,
  kind: "live" | "backtest" | null,
): { chosen: ListKey | null; exact: boolean } {
  if (available.length === 0) return { chosen: null, exact: false };
  const pick = (rows: ListKey[]) =>
    (kind ? rows.find((r) => r.kind === kind) : undefined) ??
    rows.find((r) => r.kind === "live") ??
    rows[0];
  if (season !== null && week !== null) {
    const same = available.filter((r) => r.season === season && r.week === week);
    if (same.length) {
      const c = pick(same);
      return { chosen: c, exact: kind === null || c.kind === kind };
    }
  }
  if (season !== null) {
    const inSeason = available.filter((r) => r.season === season);
    if (inSeason.length) {
      const top = inSeason[0];
      const c = pick(inSeason.filter((r) => r.week === top.week));
      return { chosen: c, exact: week === null && kind === null };
    }
  }
  const top = available[0];
  const c = pick(available.filter((r) => r.season === top.season && r.week === top.week));
  return { chosen: c, exact: season === null && week === null && kind === null };
}

export type WeekRef = { season: number; week: number };

/** The distinct weeks of `available` (newest first) and the weeks just newer and older than
 *  `at`, for the previous/next links. */
export function neighbors(available: ListKey[], at: WeekRef): { newer: WeekRef | null; older: WeekRef | null } {
  const weeks: WeekRef[] = [];
  for (const r of available) {
    const last = weeks[weeks.length - 1];
    if (!last || last.season !== r.season || last.week !== r.week) weeks.push({ season: r.season, week: r.week });
  }
  const i = weeks.findIndex((w) => w.season === at.season && w.week === at.week);
  if (i === -1) return { newer: null, older: null };
  return { newer: i > 0 ? weeks[i - 1] : null, older: i < weeks.length - 1 ? weeks[i + 1] : null };
}

/** A /waivers link. */
export function waiversHref(q: { pos?: string; season?: number; week?: number; kind?: string }): string {
  const p = new URLSearchParams();
  if (q.pos) p.set("pos", q.pos);
  if (q.season !== undefined) p.set("season", String(q.season));
  if (q.week !== undefined) p.set("week", String(q.week));
  if (q.kind) p.set("kind", q.kind);
  const s = p.toString();
  return s ? `/waivers?${s}` : "/waivers";
}
