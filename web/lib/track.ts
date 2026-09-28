// Picking rows out of the published track record (pure functions, unit-tested).
import type { TrackRow } from "@/lib/queries/track";
import { BASELINE_LAST_POINTS } from "./method";

type Range = { seasonFrom: number; seasonTo: number };

/** The widest season range among rows (earliest start, then latest end). */
export function widestRange(rows: Range[]): Range | null {
  let best: Range | null = null;
  for (const r of rows) {
    if (
      !best ||
      r.seasonFrom < best.seasonFrom ||
      (r.seasonFrom === best.seasonFrom && r.seasonTo > best.seasonTo)
    ) {
      best = { seasonFrom: r.seasonFrom, seasonTo: r.seasonTo };
    }
  }
  return best;
}

const inRange = (r: Range, g: Range) => r.seasonFrom === g.seasonFrom && r.seasonTo === g.seasonTo;

/** Pooled precision@10 of a method for a label over a season range. */
export function pooledP10(
  rows: TrackRow[],
  model: string,
  label: string,
  exclRostered: boolean,
  range: Range,
): TrackRow | null {
  return (
    rows.find(
      (r) =>
        r.scope === "pooled" &&
        r.metric === "p_at_10" &&
        r.model === model &&
        r.label === label &&
        r.exclRostered === exclRostered &&
        inRange(r, range),
    ) ?? null
  );
}

export type Headline = {
  range: Range;
  radar: TrackRow;
  baseline: TrackRow;
  diff: TrackRow | null;
};

/** The home page's sentence: the Radar's pooled precision@10 for y_hit (all pool rows) vs
 *  last week's points, over the widest season range where both exist. */
export function headline(rows: TrackRow[], model: string): Headline | null {
  const cands = rows.filter(
    (r) =>
      r.scope === "pooled" &&
      r.metric === "p_at_10" &&
      r.label === "y_hit" &&
      !r.exclRostered &&
      r.value !== null &&
      (r.model === model || r.model === BASELINE_LAST_POINTS),
  );
  const ranges = cands.filter(
    (a) => a.model === model && cands.some((b) => b.model === BASELINE_LAST_POINTS && inRange(a, b)),
  );
  const range = widestRange(ranges);
  if (!range) return null;
  const radar = pooledP10(rows, model, "y_hit", false, range);
  const baseline = pooledP10(rows, BASELINE_LAST_POINTS, "y_hit", false, range);
  if (!radar || !baseline) return null;
  const diff =
    rows.find(
      (r) =>
        r.scope === "diff" &&
        r.metric === "p_at_10_diff" &&
        r.model === model &&
        r.scopeValue === BASELINE_LAST_POINTS &&
        r.label === "y_hit" &&
        !r.exclRostered &&
        inRange(r, range),
    ) ?? null;
  return { range, radar, baseline, diff };
}

/** Rows of one scope/metric/label/subset over one season range, in the given model order. */
export function select(
  rows: TrackRow[],
  q: { scope: string; metric: string; label: string; exclRostered: boolean; range: Range },
): TrackRow[] {
  return rows.filter(
    (r) =>
      r.scope === q.scope &&
      r.metric === q.metric &&
      r.label === q.label &&
      r.exclRostered === q.exclRostered &&
      inRange(r, q.range),
  );
}
