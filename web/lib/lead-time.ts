// Lead time vs the crowd's pure helpers (feature #8, docs/lead_time.md; unit-tested in
// tests/unit/lead-time.test.ts). Every number the section shows comes from the published
// lead_time_* tables (src/twm/publish/lead_time.py): aggregates only, never a player. The fixed
// rules below (thresholds, the momentum rise) mirror src/twm/modules/lead_time/__init__.py; the
// unit test reads that file so they cannot drift.

/** The crowd "adds" a player when his ESPN rostered % crosses this (headline, secondary). */
export const PRIMARY = 50;
export const SECONDARY = 25;
/** The momentum baseline: a rise of at least this many points in one waiver period. */
export const MOMENTUM_POINTS = 10;
export const SIGNALS = ["listed", "spec_plus", "must_add", "momentum"] as const;
export type Signal = (typeof SIGNALS)[number];
export const LEVELS = ["listed", "spec_plus", "must_add"] as const;
export const SIGNAL_LABELS: Record<Signal, string> = {
  listed: "Listed (top 25)",
  spec_plus: "Must-add or speculative",
  must_add: "Must-add",
  momentum: `Momentum (+${MOMENTUM_POINTS} points in a week)`,
};
export const STATES = ["already", "added_later", "never"] as const;
/** The histogram's clipped ends (study.lead_hist): -6 = six or more weeks after, 10 = ten or more before. */
export const HIST_LO = -6;
export const HIST_HI = 10;

export type LTSummaryRow = {
  threshold: number; signal: string; scope: string; scopeValue: string; nAdds: number;
  nBefore: number | null; nSame: number | null; nAfter: number | null; nNever: number | null;
  nNeverOutOfPool: number | null; shareBefore: number | null; shareSame: number | null;
  shareAfter: number | null; shareNever: number | null; leadMedian: number | null;
  leadQ1: number | null; leadQ3: number | null; nearestMedian: number | null; shareBefore4: number | null;
};
export type LTHistRow = { threshold: number; signal: string; lead: number; n: number };
export type LTReverseRow = {
  threshold: number; signal: string; scope: string; scopeValue: string; state: string; n: number;
  hits: number | null; hitRate: number | null; leadMedian: number | null;
};
export type LTH2hRow = { threshold: number; level: string; h2h: string; n: number; nRadarEarlier: number; share: number };
export type LTCoverageRow = {
  season: number; baselinePeriod: number; complete: boolean; inSeasonDays: number;
  lastListWeek: number | null; adds50: number | null; adds25: number | null; inStudy: boolean;
};
export type LeadTimeTables = { coverage: LTCoverageRow[]; summary: LTSummaryRow[]; hist: LTHistRow[]; reverse: LTReverseRow[]; h2h: LTH2hRow[] };

/** "61.2%", or an en dash for a suppressed cell (fewer than 5 cases). */
export function share(x: number | null | undefined): string {
  return x === null || x === undefined ? "–" : `${(100 * x).toFixed(1)}%`;
}

/** A lead in weeks as published: "3", "6.25", "-2"; an en dash when suppressed. */
export function weeks(x: number | null | undefined): string {
  if (x === null || x === undefined) return "–";
  return String(Number.isInteger(x) ? x : Number(x.toFixed(2)));
}

const byThreshold = <T extends { threshold: number }>(rows: T[], t: number) => rows.filter((r) => r.threshold === t);
const order = (signal: string) => (SIGNALS as readonly string[]).indexOf(signal);

/** The complete seasons' pooled summary at a threshold, in SIGNALS order (missing signals left out). */
export function pooled(summary: LTSummaryRow[], threshold: number = PRIMARY): LTSummaryRow[] {
  return byThreshold(summary, threshold)
    .filter((r) => r.scope === "complete" && order(r.signal) >= 0)
    .sort((a, b) => order(a.signal) - order(b.signal));
}

/** One signal's pooled row (undefined: not published). */
export function pooledRow(summary: LTSummaryRow[], signal: Signal, threshold: number = PRIMARY): LTSummaryRow | undefined {
  return pooled(summary, threshold).find((r) => r.signal === signal);
}

/** The seasons behind the pooled numbers ("2021–2023"), the partial ones and the excluded ones. */
export function seasonsOf(coverage: LTCoverageRow[]): { span: string | null; partial: LTCoverageRow[]; excluded: LTCoverageRow[] } {
  const full = coverage.filter((c) => c.complete && c.inStudy).map((c) => c.season).sort((a, b) => a - b);
  const span = full.length ? (full.length === 1 ? String(full[0]) : `${full[0]}–${full[full.length - 1]}`) : null;
  return { span, partial: coverage.filter((c) => c.inStudy && !c.complete), excluded: coverage.filter((c) => !c.inStudy) };
}

/** "3 weeks before", "same week", "1 week after"; the clipped ends read "10+" and "6+". */
export function leadLabel(lead: number, lo = HIST_LO, hi = HIST_HI): string {
  const n = Math.abs(lead);
  const plus = lead === hi || lead === lo ? "+" : "";
  if (lead === 0) return "same week";
  return `${n}${plus} ${n === 1 && !plus ? "week" : "weeks"} ${lead > 0 ? "before" : "after"}`;
}

export type Bin = { lead: number | null; label: string; counts: Record<string, number> };

/** The histogram's bins from lo to hi (zeros filled in) plus "never" (the summary's n_never),
 *  per signal, at a threshold. */
export function histBins(hist: LTHistRow[], summary: LTSummaryRow[], signals: readonly Signal[], threshold: number = PRIMARY, lo = HIST_LO, hi = HIST_HI): Bin[] {
  const rows = byThreshold(hist, threshold);
  const bins: Bin[] = [];
  for (let lead = lo; lead <= hi; lead++) {
    const counts: Record<string, number> = {};
    for (const s of signals) counts[s] = rows.find((r) => r.signal === s && r.lead === lead)?.n ?? 0;
    bins.push({ lead, label: leadLabel(lead, lo, hi), counts });
  }
  const never: Record<string, number> = {};
  for (const s of signals) never[s] = pooledRow(summary, s, threshold)?.nNever ?? 0;
  bins.push({ lead: null, label: "never flagged", counts: never });
  return bins;
}

/** The reverse view's complete-seasons rows of a signal, by state (already / added_later / never). */
export function reverseOf(reverse: LTReverseRow[], signal: Signal, threshold: number = PRIMARY): Partial<Record<(typeof STATES)[number], LTReverseRow>> {
  const out: Partial<Record<(typeof STATES)[number], LTReverseRow>> = {};
  for (const r of byThreshold(reverse, threshold)) {
    if (r.scope === "complete" && r.signal === signal && (STATES as readonly string[]).includes(r.state)) out[r.state as (typeof STATES)[number]] = r;
  }
  return out;
}

export const H2H_KINDS = ["both", "radar_only", "momentum_only", "neither"] as const;

/** Head to head (complete seasons) of a Radar level against the momentum baseline, by kind. */
export function h2hOf(h2h: LTH2hRow[], level: string, threshold: number = PRIMARY): Partial<Record<(typeof H2H_KINDS)[number], LTH2hRow>> {
  const out: Partial<Record<(typeof H2H_KINDS)[number], LTH2hRow>> = {};
  for (const r of byThreshold(h2h, threshold)) if (r.level === level) out[r.h2h as (typeof H2H_KINDS)[number]] = r;
  return out;
}

/** Whether anything of the study is published (the summary's pooled rows at the headline threshold). */
export function hasStudy(t: LeadTimeTables): boolean {
  return pooled(t.summary, PRIMARY).length > 0;
}
