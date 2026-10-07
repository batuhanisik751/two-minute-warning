// The Hot-Seat Meter's pure helpers (no database, no React): rounding, drivers in plain English,
// outcomes in words, the early-season calibration grid and the weekly timelines. Unit-tested in
// tests/unit/hot-seat.test.ts. The wording is careful on purpose: these are real people's jobs.
import { pct, signedNum, wholePct } from "./format";
import { HOT_SEAT_BANDS, HOT_SEAT_FIRST_WEEK, HOT_SEAT_PHASES, HOT_SEAT_WINDOW_DAYS, TRACK_INTERVAL_LEVEL } from "./method";
import { docUrl } from "./site";

// A probability as a whole percent (never "0%" for a positive chance, never "100%" below 1) and a
// signed number with the site's minus sign: the shared formatters of lib/format.ts, re-exported
// for the Hot-Seat's callers.
export { signedNum, wholePct };

export type Driver = { feature: string; label: string; contribution: number; value: number | null; missing: boolean };

/** The drivers JSON of a hot_seat_row ([{feature, label, contribution, value, missing}]); anything
 *  malformed is dropped rather than shown wrong. */
export function parseDrivers(json: unknown): Driver[] {
  if (!Array.isArray(json)) return [];
  const out: Driver[] = [];
  for (const d of json) {
    if (!d || typeof d !== "object") continue;
    const r = d as Record<string, unknown>;
    if (typeof r.feature !== "string" || typeof r.contribution !== "number") continue;
    out.push({
      feature: r.feature,
      label: typeof r.label === "string" ? r.label : r.feature,
      contribution: r.contribution,
      value: typeof r.value === "number" && Number.isFinite(r.value) ? r.value : null,
      missing: r.missing === true,
    });
  }
  return out;
}

const PLAYOFF_WORDS = [
  "missed the playoffs",
  "lost in the wild-card round",
  "lost in the divisional round",
  "lost in the conference final",
  "lost the Super Bowl",
  "won the Super Bowl",
];

const ordinal = (n: number): string => {
  const t = n % 100;
  const suf = t >= 11 && t <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[n % 10] ?? "th";
  return `${n}${suf}`;
};
const plural = (n: number, one: string, many = `${one}s`) => `${Number.isInteger(n) ? n : n.toFixed(1)} ${n === 1 ? one : many}`;

/** The driver's value in plain words ("1.6 wins below the market's expectation"). */
export function driverValue(d: Driver): string {
  if (d.missing || d.value === null) return "not available yet";
  const v = d.value;
  switch (d.feature) {
    case "wins_vs_expected":
      return Math.abs(v) < 0.05 ? "about as many wins as the market expected" : `${Math.abs(v).toFixed(1)} wins ${v < 0 ? "below" : "above"} the market's expectation`;
    case "pythag_minus_wins":
      return `${signedNum(v)} wins (points scored and allowed against the record)`;
    case "point_diff_per_game":
      return `${signedNum(v)} points per game`;
    case "is_first_year_coach":
      return v ? "his first season with the team" : "not his first season with the team";
    case "is_second_year_coach":
      return v ? "his second season with the team" : "not his second season with the team";
    case "tenure_seasons":
      return `season ${v} with the team`;
    case "prev_playoff_round":
      return `last season the team ${PLAYOFF_WORDS[v] ?? "had no recorded playoff result"}`;
    case "prev_season_wins":
      return `${plural(v, "win")} last season`;
    case "consecutive_losing_seasons":
      return `${plural(v, "losing season")} in a row before this one`;
    case "fourth_down_wp_lost_per_game":
      return `${(v * 100).toFixed(2)} WP points per game given away on clear fourth-down calls`;
    case "division_rank":
      return `${ordinal(v)} in the division`;
    case "games_remaining":
      return `${plural(v, "game")} left`;
    case "starting_qb_changes":
      return `${plural(v, "change")} of starting quarterback`;
    case "rookie_r1_qb_on_roster":
      return v ? "a rookie first-round quarterback on the roster" : "no rookie first-round quarterback";
    case "off_epa_neutral":
    case "def_epa_neutral":
    case "off_epa_neutral_trend":
    case "def_epa_neutral_trend":
      return `${signedNum(v, 2)} EPA per play`;
    default:
      return Number.isInteger(v) ? String(v) : v.toFixed(2);
  }
}

/** What the driver does to the estimate, in words. */
export function driverEffect(d: Driver): "raises the estimate" | "lowers the estimate" | "barely moves the estimate" {
  if (Math.abs(d.contribution) < 1e-9) return "barely moves the estimate";
  return d.contribution > 0 ? "raises the estimate" : "lowers the estimate";
}

/** "0 wins in 3 games" style record ("2–1"; a tie counts half a win, so a half shows as such). */
export function recordWords(wins: number, games: number): string {
  if (Number.isInteger(wins)) return `${wins}–${games - wins}`;
  return `${wins.toFixed(1)} wins in ${games} games (a tie counts half)`;
}

/** "First season with the team" / "Season 5 with the team"; NULL: not known. */
export function tenureWords(t: number | null): string {
  if (t === null) return "Tenure not known";
  return t === 1 ? "First season with the team" : `Season ${t} with the team`;
}

export const DEPARTURE_WORDS: Record<string, string> = {
  fired_in_season: "fired during the season",
  fired_after_season: "fired after the season",
  mutual_parting: "a mutual parting",
  retired: "retired",
  resigned: "resigned",
  left_for_other_job: "left for another job",
  interim_not_retained: "interim coach not retained",
  other: "another kind of departure",
};

export type OutcomeRow = { departed: boolean | null; censored: boolean | null; departureType: string | null; announced: string | null; labelStatus: string };
export type OutcomeTone = "let-go" | "not-let-go" | "other" | "pending";

/** What happened to a list's coaches: how many have a final outcome, how many of those were let
 *  go, how many are still pending (the list page and the time machine say it the same way). */
export function hotSeatTally(rows: readonly { outcome: OutcomeRow | null }[]): { final: number; letGo: number; pending: number } {
  const final = rows.filter((r) => r.outcome?.labelStatus === "final" && r.outcome.departed !== null);
  return { final: final.length, letGo: final.filter((r) => r.outcome?.departed).length, pending: rows.length - final.length };
}

/** What really happened, in careful words: let go (fired or a mutual parting) within the window,
 *  another departure (not counted as let go), not let go, or pending (live seasons). */
export function outcomeWords(o: OutcomeRow | null): { tone: OutcomeTone; short: string; long: string } {
  if (!o || o.labelStatus !== "final" || o.departed === null) {
    return { tone: "pending", short: "Pending", long: "pending until the season's departures are labelled" };
  }
  const type = o.departureType ? (DEPARTURE_WORDS[o.departureType] ?? DEPARTURE_WORDS.other) : null;
  // NULL announced on a final departure: the labels imputed the day (no source gives it), G2.4
  const when = o.announced ? `, announced ${o.announced}` : ", announcement date not reported";
  if (o.departed) return { tone: "let-go", short: "Let go", long: `${type ?? "let go"}${when}` };
  if (o.censored) return { tone: "other", short: "Left another way", long: `${type ?? "another departure"}${when}: not counted as let go` };
  return { tone: "not-let-go", short: "Not let go", long: `no firing or mutual parting announced by ${HOT_SEAT_WINDOW_DAYS} days after the season` };
}

export type PhaseKey = (typeof HOT_SEAT_PHASES)[number]["key"];

/** "Weeks 2–4", "Weeks 5–9", "Week 10 on", "End of season" (from lib/method.ts). */
export function phaseName(key: string): string {
  const p: { from: number | null; to: number | null } | undefined = HOT_SEAT_PHASES.find((x) => x.key === key);
  if (!p) return key;
  if (p.from === null) return "End of season";
  if (p.to === null) return `Week ${p.from} on`;
  return p.from === p.to ? `Week ${p.from}` : `Weeks ${p.from}–${p.to}`;
}

/** The probability band of index i: "Under 10%", "10–25%", "50% or more". */
export function bandName(i: number): string {
  const lo = HOT_SEAT_BANDS[i];
  const hi = HOT_SEAT_BANDS[i + 1];
  if (lo === undefined) return "";
  if (i === 0) return `Under ${Math.round((hi ?? 1) * 100)}%`;
  if (hi === undefined) return `${Math.round(lo * 100)}% or more`;
  return `${Math.round(lo * 100)}–${Math.round(hi * 100)}%`;
}

/** One cell of the early-season check: a phase x band group of reconstructed rows with a final
 *  outcome (interims left out), from the database's aggregation. */
export type CalCell = { phase: string; band: number; n: number; departed: number; meanPred: number; coachSeasons: number };
export type CalGroup = { phase: string; name: string; cells: (CalCell & { observed: number; bandName: string })[] };

/** The cells in the page's order: phases as in HOT_SEAT_PHASES, bands low to high; empty groups
 *  are left out, unknown phases dropped. */
export function calibrationGrid(rows: CalCell[]): CalGroup[] {
  return HOT_SEAT_PHASES.map((p) => ({
    phase: p.key,
    name: phaseName(p.key),
    cells: rows
      .filter((r) => r.phase === p.key && r.n > 0)
      .sort((a, b) => a.band - b.band)
      .map((r) => ({ ...r, observed: r.departed / r.n, bandName: bandName(r.band) })),
  })).filter((g) => g.cells.length > 0);
}

/** The headline of the caveat: the earliest phase's highest band (e.g. weeks 2–4 at 50% or more). */
export function earlySeasonCheck(rows: CalCell[]): (CalCell & { observed: number; bandName: string; phaseName: string }) | null {
  const first = calibrationGrid(rows).find((g) => g.phase === HOT_SEAT_PHASES[0].key);
  const top = first?.cells.find((c) => c.band === HOT_SEAT_BANDS.length - 1);
  return top ? { ...top, phaseName: phaseName(top.phase) } : null;
}

/** "2026 week 3" or "2025, end of season". */
export function listName(season: number, week: number, snapshot: string): string {
  return snapshot === "end_of_season" ? `${season}, end of season` : `${season} week ${week}`;
}

export type TimelineRow = { coachId: string; week: number; snapshot: string; kind: string; probability: number };
export type TimelinePoint = { week: number; snapshot: string; kind: string; probability: number };

/** Each coach's points in one season, by week; where a week has a live and a reconstructed list,
 *  the live one wins (as on the list pages). */
export function mergeTimeline(rows: TimelineRow[]): Map<string, TimelinePoint[]> {
  const best = new Map<string, TimelineRow>();
  for (const r of rows) {
    const k = `${r.coachId}|${r.week}`;
    const had = best.get(k);
    if (!had || (had.kind !== "live" && r.kind === "live")) best.set(k, r);
  }
  const out = new Map<string, TimelinePoint[]>();
  for (const r of best.values()) {
    const list = out.get(r.coachId) ?? [];
    list.push({ week: r.week, snapshot: r.snapshot, kind: r.kind, probability: r.probability });
    out.set(r.coachId, list);
  }
  for (const list of out.values()) list.sort((a, b) => a.week - b.week);
  return out;
}

const pointName = (p: TimelinePoint) => (p.snapshot === "end_of_season" ? "end of season" : `week ${p.week}`);

/** A timeline in words, for screen readers and the table: every point when there are few, else
 *  the first, the highest and the latest. */
export function timelineWords(points: TimelinePoint[]): string {
  if (!points.length) return "No weekly estimate yet";
  if (points.length <= 4) return points.map((p) => `${pointName(p)}: ${wholePct(p.probability)}`).join(", ");
  const first = points[0];
  const last = points[points.length - 1];
  const top = points.reduce((a, b) => (b.probability > a.probability ? b : a));
  const peak = top !== first && top !== last ? `, highest ${wholePct(top.probability)} (${pointName(top)})` : "";
  return `${wholePct(first.probability)} (${pointName(first)}) to ${wholePct(last.probability)} (${pointName(last)})${peak}`;
}

/** One estimate per past season for the coach page: the end-of-season snapshot when he has one,
 *  else his last weekly row of that season (a coach let go during the season has no snapshot).
 *  Newest season first. */
export function lastPerSeason<T extends { season: number; week: number; snapshot: string; kind: string }>(rows: T[]): T[] {
  const by = new Map<number, T>();
  const rank = (r: T) => (r.snapshot === "end_of_season" ? 1000 : r.week) + (r.kind === "live" ? 0.5 : 0);
  for (const r of rows) {
    const had = by.get(r.season);
    if (!had || rank(r) > rank(had)) by.set(r.season, r);
  }
  return [...by.values()].sort((a, b) => b.season - a.season);
}

export type RecordSeason = {
  season: number;
  team: string;
  /** null: the full regular season (the end-of-season snapshot); else the list's week */
  throughWeek: number | null;
  record: string;
  expectedWins: number | null;
  winsVsExpected: number | null;
  /** an interim coach's row: the record and the expectation are the TEAM's season, not his */
  interim: boolean;
};

/** The coach page's record vs expectation, newest season first: per season his regular-season
 *  record and the market's expected wins from his published Hot-Seat rows (the end-of-season
 *  snapshot; the season in progress, or a coach let go during it: his newest weekly row). A
 *  season without a row has no entry; a NULL expectation stays NULL (shown as not known). The
 *  rows count the TEAM's games: for an interim coach (`interim`) that is not his own record. */
export function recordPerSeason(
  rows: { season: number; week: number; snapshot: string; kind: string; team: string; regWins: number; regGamesPlayed: number; expectedWins: number | null; winsVsExpected: number | null; isInterim?: boolean }[],
): RecordSeason[] {
  return lastPerSeason(rows).map((r) => ({
    season: r.season,
    team: r.team,
    throughWeek: r.snapshot === "end_of_season" ? null : r.week,
    record: recordWords(r.regWins, r.regGamesPlayed),
    expectedWins: r.expectedWins,
    winsVsExpected: r.expectedWins === null ? null : r.winsVsExpected,
    interim: r.isInterim === true,
  }));
}

const WEEK_MS = 7 * 24 * 3600 * 1000;

/** The current season has no published weekly Hot-Seat list yet (the newest published list is
 *  from an earlier season): the season and when its list is due -- from the first weekly week
 *  (HOT_SEAT_FIRST_WEEK) on, the site's current weekly as-of is itself a list's as-of (Tuesday
 *  14:00 UTC), so that list is due then (a week-4 as-of with no list: overdue); before it, the
 *  first weekly week's as-of, counted in whole weeks from the site's current weekly as-of
 *  (site_meta's current week and its lists' as-of); `overdue` when that time had already passed at
 *  the publish. null when the newest list is from the current season (or nothing says which season
 *  is current). dueAt null: no weekly as-of of the season is known yet (before week 1's lists). */
export function seasonNotStarted(
  newestListSeason: number | null,
  meta: { currentSeason: number | null; asOf: { at: string; week: { season: number; week: number } } | null; generatedAt: string | null },
): { season: number; firstWeek: number; dueAt: string | null; overdue: boolean } | null {
  const season = meta.currentSeason;
  if (season === null || (newestListSeason !== null && newestListSeason >= season)) return null;
  const a = meta.asOf;
  const base = a && a.week.season === season && a.week.week >= 1 ? Date.parse(a.at) : NaN;
  const ahead = a ? Math.max(HOT_SEAT_FIRST_WEEK - a.week.week, 0) : 0;
  const dueAt = Number.isNaN(base) ? null : new Date(base + ahead * WEEK_MS).toISOString();
  const published = meta.generatedAt ? Date.parse(meta.generatedAt) : NaN;
  return { season, firstWeek: HOT_SEAT_FIRST_WEEK, dueAt, overdue: dueAt !== null && !Number.isNaN(published) && Date.parse(dueAt) <= published };
}

/** A /hot-seat link. */
export function hotSeatHref(q: { season?: number; week?: number; kind?: string }): string {
  const p = new URLSearchParams();
  if (q.season !== undefined) p.set("season", String(q.season));
  if (q.week !== undefined) p.set("week", String(q.week));
  if (q.kind) p.set("kind", q.kind);
  const s = p.toString();
  return s ? `/hot-seat?${s}` : "/hot-seat";
}

/** The write-up of the fourth-down research (step H5), in the public repository. */
export const RESEARCH_URL = docUrl("docs/research_decisions_vs_firings.md");

export type TrackCellRow = { variant: string; model: string; prob: string; slice: string; metric: string; value: number | null; lo: number | null; hi: number | null; nRows: number; nPos: number; nSeasons: number };
export type TrackCell = { value: number; lo: number | null; hi: number | null; nRows: number; nPos: number; nSeasons: number };

/** One number of the backtest's track record (hot_seat_track_record), or NULL when it is not
 *  published. Defaults: the main run, the model's own probability. */
export function trackCell(rows: TrackCellRow[], q: { model: string; slice: string; metric: string; variant?: string; prob?: string }): TrackCell | null {
  const r = rows.find((x) => x.variant === (q.variant ?? "main") && x.model === q.model && x.prob === (q.prob ?? "prob") && x.slice === q.slice && x.metric === q.metric);
  if (!r || r.value === null) return null;
  return { value: r.value, lo: r.lo, hi: r.hi, nRows: r.nRows, nPos: r.nPos, nSeasons: r.nSeasons };
}

/** "0.788 (0.745 to 0.825)": a metric with its interval, at the backtest report's precision. */
export function metricWithInterval(c: TrackCell | null, digits = 3): string {
  if (!c) return "not published";
  const f = (x: number) => x.toFixed(digits);
  return c.lo !== null && c.hi !== null ? `${f(c.value)} (${f(c.lo)} to ${f(c.hi)})` : f(c.value);
}

/** The models of the backtest, in words (the report's names). */
export const MODEL_WORDS: Record<string, string> = {
  logit: "Logistic regression (used)",
  hazard: "Discrete-time hazard model",
  lgbm: "LightGBM (challenger)",
  base_win_pct: "Baseline: win share to date",
  base_wins_vs_expected: "Baseline: wins vs market expectation",
};

/** The slices whose top-5 hit rate the backtest publishes, in season order: the weekly ones the
 *  rows carry (`week_<n>`, e.g. week 12) and then the season's end. */
export function top5Slices(rows: TrackCellRow[]): { slice: string; when: string }[] {
  const weeks = [...new Set(rows.filter((r) => r.metric === "top5_hit_rate" && /^week_\d+$/.test(r.slice)).map((r) => Number(r.slice.slice(5))))];
  return [...weeks.sort((a, b) => a - b).map((w) => ({ slice: `week_${w}`, when: `week ${w}` })), { slice: "end_of_season", when: "season end" }];
}

/** The backtest's headline numbers as tiles (main run, the model's own probability), each with
 *  its interval and what it counts: the rows for a score, the coaches let go for a top-5 hit
 *  rate (a share of them, shown as a percentage like the site's other shares). */
export function headlineStats(
  rows: TrackCellRow[],
  level: number = TRACK_INTERVAL_LEVEL,
): { label: string; term?: { name: string; text: string }; value: string; interval: string | null; note: string }[] {
  // `term`: the glossary entry the label names (components/track/parts.tsx makes it a Term)
  const tiles: { label: string; term?: { name: string; text: string }; slice: string; metric: string; digits: number; share?: boolean }[] = [
    { label: "ROC-AUC, every list", term: { name: "roc_auc", text: "ROC-AUC" }, slice: "all", metric: "roc_auc", digits: 3 },
    { label: "Brier score, every list (lower is better)", term: { name: "brier", text: "Brier score" }, slice: "all", metric: "brier", digits: 4 },
    ...top5Slices(rows).map((t) => ({ label: `Coaches let go who were in their season's top 5 at ${t.when}`, slice: t.slice, metric: "top5_hit_rate", digits: 0, share: true })),
  ];
  const out = [];
  for (const t of tiles) {
    const c = trackCell(rows, { model: "logit", slice: t.slice, metric: t.metric });
    if (!c) continue;
    const f = (x: number) => (t.share ? pct(x, t.digits) : x.toFixed(t.digits));
    out.push({
      label: t.label,
      term: t.term,
      value: f(c.value),
      interval: c.lo !== null && c.hi !== null ? `${Math.round(level * 100)}% interval ${f(c.lo)} to ${f(c.hi)}` : null,
      // a top-5 hit rate is a share of the coaches let go (the slice's positives), not of its rows
      note: `${(t.share ? c.nPos : c.nRows).toLocaleString("en-US")} ${t.share ? "coaches let go" : "rows"}, ${c.nSeasons} test seasons`,
    });
  }
  return out;
}
