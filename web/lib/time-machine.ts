// The time machine (/time-machine, PROJECT_SPEC 8.7): pick a season and a week, see what each
// module said then, with what happened. Pure helpers: the union of the published weeks, which
// module covers a week, and the words for one that does not. Nothing is recomputed here: the page
// shows the stored lists, exactly as published.
import { chooseList, type ListKey, type WeekRef } from "./params";

export const MODULES = ["radar", "stream", "regression", "decisions", "hot_seat", "board"] as const;
export type ModuleId = (typeof MODULES)[number];

export const MODULE_NAMES: Record<ModuleId, string> = {
  radar: "Waiver Radar",
  stream: "Streamers",
  regression: "Regression Watch",
  decisions: "Decision Report Card",
  hot_seat: "Hot-Seat Meter",
  board: "Cliff board",
};

/** The module as a sentence's subject ("the Waiver Radar has ...") and what it publishes. */
const SUBJECTS: Record<ModuleId, [string, string]> = {
  radar: ["the Waiver Radar", "lists"],
  stream: ["the streamer (K and D/ST)", "lists"],
  regression: ["Regression Watch", "lists"],
  decisions: ["the Decision Report Card", "graded weeks"],
  hot_seat: ["the Hot-Seat Meter", "lists"],
  board: ["the Cliff board", "boards"],
};
const cap = (s: string) => s[0].toUpperCase() + s.slice(1);

/** One published week of one module. kind null: graded decisions (a report card, made after
 *  the games: neither live nor reconstructed). */
export type Covered = { module: ModuleId; season: number; week: number; kind: "live" | "backtest" | null };

/** The board is made once a season, on the eve of week 1: its lists carry week 0. */
export const PRESEASON_WEEK = 0;

export type CalendarWeek = WeekRef & { kinds: ("live" | "backtest")[]; modules: ModuleId[] };

/** Every (season, week) any module published, newest first, with its kinds and modules. */
export function calendar(rows: readonly Covered[]): CalendarWeek[] {
  const by = new Map<string, CalendarWeek>();
  for (const r of rows) {
    const key = `${r.season}-${r.week}`;
    const w = by.get(key) ?? { season: r.season, week: r.week, kinds: [], modules: [] };
    if (r.kind && !w.kinds.includes(r.kind)) w.kinds.push(r.kind);
    if (!w.modules.includes(r.module)) w.modules.push(r.module);
    by.set(key, w);
  }
  const order = (m: ModuleId) => MODULES.indexOf(m);
  return [...by.values()]
    .map((w) => ({ ...w, kinds: w.kinds.sort((a, b) => (a === "live" ? -1 : b === "live" ? 1 : 0)), modules: w.modules.sort((a, b) => order(a) - order(b)) }))
    .sort((a, b) => b.season - a.season || b.week - a.week);
}

/** The calendar as the week picker's index (one row per week and kind; a decisions-only week
 *  counts as one row: its option's words come from `weekNote`). */
export function pickerIndex(cal: readonly CalendarWeek[]): (ListKey & { positions: number })[] {
  return cal.flatMap((w) =>
    w.kinds.length
      ? w.kinds.map((kind) => ({ season: w.season, week: w.week, kind, positions: w.modules.length }))
      : [{ season: w.season, week: w.week, kind: "backtest" as const, positions: w.modules.length }],
  );
}

/** The chosen week: the asked one when published; else the asked season's newest week; else
 *  the newest week (params.chooseList's rules, without a kind). */
export function chooseWeek(cal: readonly CalendarWeek[], season: number | null, week: number | null): { chosen: CalendarWeek | null; exact: boolean } {
  const { chosen, exact } = chooseList(pickerIndex(cal), season, week, null);
  if (!chosen) return { chosen: null, exact: false };
  return { chosen: cal.find((w) => w.season === chosen.season && w.week === chosen.week) ?? null, exact };
}

/** A week number from the query string: 0 (the preseason) to 99, else null. */
export function parseWeek(v: string | string[] | undefined): number | null {
  const s = Array.isArray(v) ? v[0] : v;
  return s !== undefined && /^\d{1,2}$/.test(s) ? Number(s) : null;
}

/** "Preseason" or "Week 5". */
export function weekTitle(week: number): string {
  return week === PRESEASON_WEEK ? "Preseason" : `Week ${week}`;
}

/** "2024 preseason" or "2024 week 5". */
export function whenName(w: WeekRef): string {
  return `${w.season} ${weekTitle(w.week).toLowerCase()}`;
}

/** The words after a week in the picker: which kinds of list it has. */
export function weekNote(w: Pick<CalendarWeek, "kinds">): string {
  const live = w.kinds.includes("live");
  const recon = w.kinds.includes("backtest");
  return live && recon ? "live and reconstructed" : live ? "live" : recon ? "reconstructed" : "graded calls only";
}

/** "week 3", "weeks 2–17" (every week between), "weeks 4, 6, 8 and 10", or "9 weeks between 1 and 17". */
export function weekList(xs: readonly number[]): string {
  const ws = [...new Set(xs)].sort((a, b) => a - b);
  const lo = ws[0];
  const hi = ws[ws.length - 1];
  if (ws.length === 1) return `week ${lo}`;
  if (hi - lo + 1 === ws.length) return `weeks ${lo}–${hi}`;
  if (ws.length <= 6) return `weeks ${ws.slice(0, -1).join(", ")} and ${hi}`;
  return `${ws.length} weeks between ${lo} and ${hi}`;
}

/** Why a module shows nothing for `at`, from its own published weeks, and the nearest week of
 *  the same season it does cover (for a link), if any. */
export function notCovered(module: ModuleId, weeks: readonly WeekRef[], at: WeekRef): { reason: string; see: WeekRef | null } {
  const [who, what] = SUBJECTS[module];
  if (!weeks.length) return { reason: `${cap(who)} has published nothing yet.`, see: null };
  const seasons = weeks.map((w) => w.season);
  const inSeason = weeks.filter((w) => w.season === at.season).map((w) => w.week);
  const near = inSeason.length
    ? { season: at.season, week: [...inSeason].sort((a, b) => Math.abs(a - at.week) - Math.abs(b - at.week) || a - b)[0] }
    : null;
  if (inSeason.length && inSeason.every((w) => w === PRESEASON_WEEK)) {
    return { reason: `${cap(who)} is made once a season, on the eve of week 1: see the ${at.season} preseason.`, see: near };
  }
  if (at.week === PRESEASON_WEEK) return { reason: `${cap(who)} has nothing for the preseason.`, see: null };
  const first = Math.min(...seasons);
  const last = Math.max(...seasons);
  if (at.season < first) return { reason: `${cap(who)}'s ${what} start in ${first}: there is nothing for ${at.season}.`, see: null };
  if (at.season > last) return { reason: `${cap(who)} has nothing for ${at.season} yet (its newest ${what} are from ${last}).`, see: null };
  if (!inSeason.length) return { reason: `${cap(who)} published nothing for ${at.season}.`, see: null };
  return { reason: `${cap(who)} has nothing for ${whenName(at)}: in ${at.season} its ${what} cover ${weekList(inSeason)}.`, see: near };
}

/** The kind a module shows for `at`: live when that week has a live list (as on the module's own
 *  page), else reconstructed, else null (not covered). */
export function kindAt(rows: readonly ListKey[], at: WeekRef): "live" | "backtest" | null {
  const same = rows.filter((r) => r.season === at.season && r.week === at.week);
  return same.some((r) => r.kind === "live") ? "live" : same.length ? "backtest" : null;
}

/** A /time-machine link. */
export function timeMachineHref(w?: WeekRef): string {
  return w ? `/time-machine?season=${w.season}&week=${w.week}` : "/time-machine";
}
