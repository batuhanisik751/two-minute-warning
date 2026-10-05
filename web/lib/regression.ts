// Regression Watch on the site: pure helpers (unit-tested in tests/unit/regression.test.ts).
// Every number comes from the database (regression_row, regression_outcome,
// regression_track_record, model_versions.params); this file only picks rows and words.

import { fmtPoints } from "./format";

/** The tags the site shows. A third tag (legit) was tested and dropped (owner decision,
 *  2026-09-30: it predicted nothing better than its base rate); see DROPPED_TAGS. */
export const TAGS = ["sell_high", "buy_low"] as const;
export type Tag = (typeof TAGS)[number];

export const TAG_TITLES: Record<Tag, string> = { sell_high: "Sell-high", buy_low: "Buy-low" };

/** Tags the pipeline may still carry (the frozen live 2026-W03 list does) that the site never
 *  shows: the queries drop them from every row (dropTags), and the track record states them in
 *  one sentence only. The value is the tag's title in the weekly reasons ("Legit: ..."). */
export const DROPPED_TAGS: Record<string, string> = { legit: "Legit" };
const isDropped = (t: string) => Object.hasOwn(DROPPED_TAGS, t);

export function tagTitle(tag: string): string {
  return TAG_TITLES[tag as Tag] ?? tag;
}

const POSITION_ORDER = ["QB", "RB", "WR", "TE"];

export type Stats = { ppg: number | null; xfp: number | null; fpoe: number | null };

export type RegressionRow = {
  gsisId: string;
  name: string;
  position: string;
  team: string;
  teamName: string;
  teamColor: string | null;
  teamColor2: string | null;
  games: number;
  ppg: number;
  ppgNg: number | null;
  xfpPg: number | null;
  xfpPgNg: number | null;
  fpoePg: number | null;
  fpoePgNg: number | null;
  projection: number;
  /** The projection's 80% range (regression_row.projection_lo / _hi, migration 0006); null
   *  when the list has none (the 2026 week 3 live list, the 2011 backtest lists). */
  projectionLo: number | null;
  projectionHi: number | null;
  shrinkage: number | null;
  tag: string | null;
  tags: string[];
  tagReason: string | null;
  outcome: { rosPpg: number | null; rosGames: number | null; status: string } | null;
};

/** `?gt=off` shows the values without garbage time; anything else (or nothing) with it. */
export function parseGarbage(v: string | string[] | undefined): boolean {
  const s = Array.isArray(v) ? v[0] : v;
  return s !== "off";
}

/** PPG, xFP/game and FPOE/game with garbage time (every play) or without it. */
export function statsOf(r: Pick<RegressionRow, "ppg" | "ppgNg" | "xfpPg" | "xfpPgNg" | "fpoePg" | "fpoePgNg">, withGarbage: boolean): Stats {
  return withGarbage ? { ppg: r.ppg, xfp: r.xfpPg, fpoe: r.fpoePg } : { ppg: r.ppgNg, xfp: r.xfpPgNg, fpoe: r.fpoePgNg };
}

const byId = (a: RegressionRow, b: RegressionRow) => (a.gsisId < b.gsisId ? -1 : a.gsisId > b.gsisId ? 1 : 0);

/** The players with a tag, in the weekly report's order (src/twm/modules/regression_watch/
 *  weekly.py _tag_lines): Sell-high by how far the projection sits below his PPG, Buy-low by
 *  how far above. */
export function tagRows(rows: readonly RegressionRow[], tag: Tag): RegressionRow[] {
  const t = rows.filter((r) => r.tags.includes(tag));
  if (tag === "sell_high") return t.sort((a, b) => b.ppg - b.projection - (a.ppg - a.projection) || byId(a, b));
  return t.sort((a, b) => b.projection - b.ppg - (a.projection - a.ppg) || byId(a, b));
}

/** A weekly reason without the sentences of dropped tags ("Buy-low: ... Legit: ..." keeps
 *  "Buy-low: ..."); null when nothing is left. */
export function reasonWithout(reason: string | null): string | null {
  if (!reason) return reason;
  const titles = [...Object.values(TAG_TITLES), ...Object.values(DROPPED_TAGS)].join("|");
  const parts = reason.split(new RegExp(`(?=(?:^|\\s)(?:${titles}): )`));
  const dropped = new RegExp(`^(?:${Object.values(DROPPED_TAGS).join("|")}): `);
  const kept = parts.map((x) => x.trim()).filter((x) => x && !dropped.test(x));
  return kept.length ? kept.join(" ") : null;
}

/** A row as the site shows it: dropped tags out of `tags`, `tag` the first tag left (or none),
 *  and their sentences out of the reason. Rows without a dropped tag come back unchanged. */
export function dropTags<T extends Pick<RegressionRow, "tag" | "tags" | "tagReason">>(r: T): T {
  if (!r.tags.some(isDropped) && !(r.tag !== null && isDropped(r.tag))) return r;
  const tags = r.tags.filter((t) => !isDropped(t));
  const tag = r.tag !== null && !isDropped(r.tag) ? r.tag : (tags[0] ?? null);
  return { ...r, tag, tags, tagReason: tag === null ? null : reasonWithout(r.tagReason) };
}

/** A /regression link (the garbage-time toggle and the time machine keep each other). */
export function regressionHref(q: { season?: number; week?: number; kind?: string; withGarbage?: boolean; anchor?: string }): string {
  const p = new URLSearchParams();
  if (q.season !== undefined) p.set("season", String(q.season));
  if (q.week !== undefined) p.set("week", String(q.week));
  if (q.kind) p.set("kind", q.kind);
  if (q.withGarbage === false) p.set("gt", "off");
  const s = p.toString();
  return `/regression${s ? `?${s}` : ""}${q.anchor ? `#${q.anchor}` : ""}`;
}

/** Signed points per game with one decimal: 4.26 -> "+4.3", -0.04 -> "0.0". */
export function signed(x: number, digits = 1): string {
  const s = x.toFixed(digits);
  if (Number(s) === 0) return (0).toFixed(digits);
  return x > 0 ? `+${s}` : s;
}

/** An 80% range as the site writes it: (9.1, 15.62) -> "9.1–15.6"; null without both bounds. */
export function rangeText(lo: number | null | undefined, hi: number | null | undefined): string | null {
  if (lo === null || lo === undefined || hi === null || hi === undefined) return null;
  return `${fmtPoints(lo)}–${fmtPoints(hi)}`;
}

/** The projection with its 80% range: "12.3 (9.1–15.6)"; the projection alone without one. */
export function projectionText(r: Pick<RegressionRow, "projection" | "projectionLo" | "projectionHi">): string {
  const range = rangeText(r.projectionLo, r.projectionHi);
  return range ? `${fmtPoints(r.projection)} (${range})` : fmtPoints(r.projection);
}

/** One position's coverage check of the 80% range (published backtest rows with a range and a
 *  final, graded outcome): how many actual rest-of-season PPGs fell inside, below, above. */
export type RangeCoverageRow = { position: string; n: number; inside: number; below: number; above: number; first: number; last: number };

/** QB, RB, WR, TE (those published) and the pooled row ("All"), each with its share inside. */
export function coverageRows(rows: readonly RangeCoverageRow[]): (RangeCoverageRow & { coverage: number })[] {
  const byPos = POSITION_ORDER.flatMap((p) => rows.filter((r) => r.position === p && r.n > 0));
  if (!byPos.length) return [];
  const sum = (k: "n" | "inside" | "below" | "above") => byPos.reduce((a, r) => a + r[k], 0);
  const all: RangeCoverageRow = {
    position: "All",
    n: sum("n"),
    inside: sum("inside"),
    below: sum("below"),
    above: sum("above"),
    first: Math.min(...byPos.map((r) => r.first)),
    last: Math.max(...byPos.map((r) => r.last)),
  };
  return [...byPos, all].map((r) => ({ ...r, coverage: r.inside / r.n }));
}

/** One row of regression_track_record (reports/regression_watch/backtest.csv). */
export type RegressionTrackRow = {
  section: string;
  weeks: string | null;
  position: string | null;
  method: string | null;
  metric: string | null;
  rowGroup: string | null;
  season: number | null;
  value: number;
  lo: number | null;
  hi: number | null;
  n: number;
  nSeasons: number | null;
  perAsof: number | null;
  notGraded: number | null;
};

/** The seasons the backtest tested (its `choice` rows: one per test season). */
export function testSeasons(rows: readonly RegressionTrackRow[]): { from: number; to: number } | null {
  const s = rows.filter((r) => r.section === "choice" && r.season !== null).map((r) => r.season as number);
  return s.length ? { from: Math.min(...s), to: Math.max(...s) } : null;
}

/** The as-of weeks of the frozen walk-forward backtest: the weeks of its reconstructed lists in
 *  the published test seasons. A reconstructed list of the season in progress (2026 week 3, made
 *  to compare with the live list) is not part of it. Without published test seasons: the
 *  reconstructed lists of the seasons before the newest list's. */
export function backtestWeeks(
  lists: readonly { season: number; week: number; kind: string }[],
  seasons: { from: number; to: number } | null,
): number[] {
  const newest = lists.length ? Math.max(...lists.map((l) => l.season)) : null;
  const inBacktest = (s: number) => (seasons ? s >= seasons.from && s <= seasons.to : newest !== null && s < newest);
  const weeks = lists.filter((l) => l.kind === "backtest" && inBacktest(l.season)).map((l) => l.week);
  return [...new Set(weeks)].sort((a, b) => a - b);
}

export const RW_METHODS: Record<string, string> = {
  model: "Projection (Regression Watch)",
  baseline_ppg: "Season-to-date PPG",
  baseline_last3: "Last-3-games PPG",
  spec_formula: "The spec's formula (shrink toward zero)",
};

export function rwMethodName(m: string): string {
  return RW_METHODS[m] ?? m;
}

/** A value, difference or tag row of the track record. */
export function trackRow(
  rows: readonly RegressionTrackRow[],
  q: { section: string; weeks?: string; position?: string; method?: string; metric?: string; rowGroup?: string },
): RegressionTrackRow | undefined {
  return rows.find(
    (r) =>
      r.section === q.section &&
      (q.weeks === undefined || r.weeks === q.weeks) &&
      (q.position === undefined || r.position === q.position) &&
      (q.method === undefined || r.method === q.method) &&
      (q.metric === undefined || r.metric === q.metric) &&
      (q.rowGroup === undefined || r.rowGroup === q.rowGroup) &&
      r.season === null,
  );
}

export type TagVerdict = "above" | "below" | "same";

/** Tagged hit rate against the base rate: "above" when its interval clears the base rate,
 *  "below" when it sits under it, else "same" (the tag predicts no better than the base rate). */
export function tagVerdict(tagged: Pick<RegressionTrackRow, "value" | "lo" | "hi">, base: Pick<RegressionTrackRow, "value">): TagVerdict {
  if (tagged.lo !== null && tagged.lo > base.value) return "above";
  if (tagged.hi !== null && tagged.hi < base.value) return "below";
  return "same";
}

/** The dropped tags' headline hit rates (tagged against base) for the track record's one
 *  sentence: the numbers come from regression_track_record, the tag's name is never shown. */
export function droppedRates(rows: readonly RegressionTrackRow[], position = "all") {
  return Object.keys(DROPPED_TAGS).flatMap((t) => {
    const q = { section: "tag", weeks: "headline", position, metric: t };
    const tagged = trackRow(rows, { ...q, rowGroup: "tagged" });
    const base = trackRow(rows, { ...q, rowGroup: "base" });
    return tagged && base ? [{ tag: t, tagged, base, verdict: tagVerdict(tagged, base) }] : [];
  });
}

/** One position's FPOE/game reliability from the frozen parameters' shrinkage rows. */
export type ShrinkRow = { position: string; metric: string; n: number; signal: number; noise: number; prior: number; halfWeight: number | null; first: number | null; last: number | null };

type RawShrink = { n?: unknown; metric?: unknown; position?: unknown; var_noise?: unknown; var_signal?: unknown; prior_mean?: unknown; first_season?: unknown; last_season?: unknown };

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** model_versions.params.shrinkage.rows of a Regression Watch parameter set, in position order;
 *  rows that do not parse are left out. */
export function shrinkRows(params: unknown): ShrinkRow[] {
  const rows = (params as { shrinkage?: { rows?: unknown } } | null)?.shrinkage?.rows;
  if (!Array.isArray(rows)) return [];
  const out: ShrinkRow[] = [];
  for (const r of rows as RawShrink[]) {
    const signal = num(r.var_signal);
    const noise = num(r.var_noise);
    const n = num(r.n);
    if (signal === null || noise === null || n === null || typeof r.position !== "string" || typeof r.metric !== "string") continue;
    out.push({
      position: r.position,
      metric: r.metric,
      n,
      signal,
      noise,
      prior: num(r.prior_mean) ?? 0,
      halfWeight: signal > 0 ? noise / signal : null,
      first: num(r.first_season),
      last: num(r.last_season),
    });
  }
  const p = (x: string) => (POSITION_ORDER.indexOf(x) === -1 ? 9 : POSITION_ORDER.indexOf(x));
  return out.sort((a, b) => p(a.position) - p(b.position) || (a.metric < b.metric ? -1 : 1));
}

/** The shrinkage factor after g games: signal / (signal + noise / g). */
export function reliability(r: Pick<ShrinkRow, "signal" | "noise">, g: number): number {
  return r.signal / (r.signal + r.noise / g);
}

/** A number from the parameters (x_sell, x_buy, min_games, ...), or null. */
export function paramNumber(params: unknown, key: string): number | null {
  return num((params as Record<string, unknown> | null)?.[key]);
}
