// Regression Watch's stability study on /methodology: pure helpers over the rows of
// regression_stability (reports/regression_watch/stability.csv, step R1), unit-tested in
// tests/unit/stability.test.ts. Every number is a row's; this file only picks and arranges.

/** One row of regression_stability. section: split_half (a metric's split-half correlation)
 *  or shrinkage (the reliability r(g) of FPOE/game after g games). */
export type StabilityRow = {
  section: string;
  seasons: string;
  split: string;
  position: string;
  metric: string;
  g: number | null;
  n: number;
  value: number;
  lo: number | null;
  hi: number | null;
  varSignal: number | null;
  varNoise: number | null;
  priorMean: number | null;
};

export type Cell = { value: number; lo: number | null; hi: number | null; n: number };

/** The positions in the rows' own order (the study's: QB, RB, WR, TE). */
export function positionsOf(rows: readonly StabilityRow[]): string[] {
  return [...new Set(rows.map((r) => r.position))];
}

/** The seasons window of the split-half rows (one window: the whole study, e.g. "2009-2025"). */
export function studyWindow(rows: readonly StabilityRow[]): string | null {
  return rows.find((r) => r.section === "split_half")?.seasons ?? null;
}

/** A split-half table: per position, the cell of each metric asked for (null when the study
 *  has none, e.g. YAC for quarterbacks); `metrics` may map a position to its own metric. */
export function splitHalf(
  rows: readonly StabilityRow[],
  split: string,
  metrics: readonly (string | Record<string, string>)[],
): { position: string; n: number | null; cells: (Cell | null)[] }[] {
  const mine = rows.filter((r) => r.section === "split_half" && r.split === split);
  return positionsOf(mine).map((position) => {
    const cells = metrics.map((m) => {
      const metric = typeof m === "string" ? m : (m[position] ?? m["*"]);
      const r = mine.find((x) => x.position === position && x.metric === metric);
      return r ? { value: r.value, lo: r.lo, hi: r.hi, n: r.n } : null;
    });
    return { position, n: cells[0]?.n ?? null, cells };
  });
}

/** The lowest and highest value of one metric across positions (the paragraph's range). */
export function extremes(table: ReturnType<typeof splitHalf>, i: number) {
  const xs = table.flatMap((t) => (t.cells[i] ? [{ position: t.position, value: t.cells[i]!.value }] : []));
  if (!xs.length) return null;
  const sorted = [...xs].sort((a, b) => a.value - b.value);
  return { low: sorted[0], high: sorted[sorted.length - 1] };
}

export type ShrinkLine = { position: string; n: number; signal: number; noise: number; prior: number | null; halfWeight: number | null; r: (Cell | null)[] };

/** The shrinkage table of one metric (fpoe or fpoe_ng) and seasons window: per position the
 *  signal and noise variances, the average, games for half weight (noise / signal) and r(g). */
export function shrinkTable(rows: readonly StabilityRow[], metric: string, seasons: string, games: readonly number[]): ShrinkLine[] {
  const mine = rows.filter((r) => r.section === "shrinkage" && r.metric === metric && r.seasons === seasons);
  return positionsOf(mine).flatMap((position) => {
    const p = mine.filter((r) => r.position === position);
    const first = p[0];
    if (first.varSignal === null || first.varNoise === null) return [];
    const r = games.map((g) => {
      const x = p.find((y) => y.g === g);
      return x ? { value: x.value, lo: x.lo, hi: x.hi, n: x.n } : null;
    });
    const halfWeight = first.varSignal > 0 ? first.varNoise / first.varSignal : null;
    return [{ position, n: first.n, signal: first.varSignal, noise: first.varNoise, prior: first.priorMean, halfWeight, r }];
  });
}

/** The r(g) columns: the backtest lists' weeks the study has, then its last g (a full season). */
export function shrinkGames(rows: readonly StabilityRow[], weeks: readonly number[]): number[] {
  const have = new Set(rows.filter((r) => r.section === "shrinkage" && r.g !== null).map((r) => r.g as number));
  if (!have.size) return [];
  const last = Math.max(...have);
  return [...new Set([...weeks.filter((w) => have.has(w)), last])].sort((a, b) => a - b);
}
