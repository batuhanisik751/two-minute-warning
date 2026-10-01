// Picks the Decision Report Card's methodology numbers from decisions_track_record (pure;
// unit-tested in tests/unit/decisions-track.test.ts). Nothing is typed in: each number is a row
// of reports/decisions/{wp_backtest,submodels}.csv or the nfl4th summary, as published.

export type TrackRow = {
  source: string;
  line: number;
  section: string;
  scope: string | null;
  subset: string | null;
  method: string | null;
  metric: string;
  value: number | null;
  lo: number | null;
  hi: number | null;
  n: number | null;
  nBlocks: number | null;
};

export type Cell = { value: number; lo: number | null; hi: number | null; n: number | null };

const SPAN = /^(\d{4})-(\d{4})$/;

/** The widest "YYYY-YYYY" span among subsets (the pooled one), NULL when there is none. */
export function widestSpan(subsets: readonly (string | null)[]): string | null {
  let best: { s: string; w: number } | null = null;
  for (const s of subsets) {
    const m = s ? SPAN.exec(s) : null;
    if (!m) continue;
    const w = Number(m[2]) - Number(m[1]);
    if (!best || w > best.w) best = { s: s!, w };
  }
  return best?.s ?? null;
}

type Where = Partial<Pick<TrackRow, "source" | "section" | "scope" | "subset" | "method" | "metric">>;

export function select(rows: readonly TrackRow[], w: Where): TrackRow[] {
  return rows.filter((r) => Object.entries(w).every(([k, v]) => r[k as keyof Where] === v));
}

export function cell(rows: readonly TrackRow[], w: Where): Cell | null {
  const r = select(rows, w)[0];
  return r && r.value !== null ? { value: r.value, lo: r.lo, hi: r.hi, n: r.n } : null;
}

export const WP_METHODS = ["own", "nflfastr_wp", "nflfastr_vegas_wp"] as const;

/** Our WP model against nflfastR's wp and vegas_wp, pooled over the test seasons. */
export function wpComparison(rows: readonly TrackRow[]) {
  const base = { source: "wp_backtest", scope: "pooled" } as const;
  const span = widestSpan(select(rows, { ...base, section: "metrics" }).map((r) => r.subset));
  if (!span) return null;
  const at = { ...base, subset: span };
  const methods = WP_METHODS.map((method) => ({
    method,
    brier: cell(rows, { ...at, section: "metrics", method, metric: "brier" }),
    logLoss: cell(rows, { ...at, section: "metrics", method, metric: "log_loss" }),
    ece: cell(rows, { ...at, section: "metrics", method, metric: "ece" }),
  })).filter((m) => m.brier || m.logLoss);
  const diffs = WP_METHODS.slice(1).map((other) => ({
    method: other,
    brier: cell(rows, { ...at, section: "difference", method: `own - ${other}`, metric: "brier" }),
    logLoss: cell(rows, { ...at, section: "difference", method: `own - ${other}`, metric: "log_loss" }),
  }));
  const n = methods[0]?.brier?.n ?? null;
  return { span, n, methods, diffs };
}

export const SMOOTH_METRICS = ["score_step_h1", "score_step_h2", "curvature", "halftime_possession", "monotone_violations"] as const;

const maxOf = (rs: TrackRow[]) => {
  const v = rs.map((r) => r.value).filter((x): x is number => x !== null);
  return v.length ? Math.max(...v) : null;
};

/** The smoothness limits (fixed before any fix was tried), the worst test fold of the model in
 *  use and of G1's model before the fix, and nflfastR's references, per metric. */
export function smoothness(rows: readonly TrackRow[]) {
  const s = select(rows, { source: "wp_backtest", section: "smoothness" });
  const ownFolds = select(s, { scope: "fold", method: "own" });
  const seasons = [...new Set(ownFolds.map((r) => Number(r.subset)).filter((x) => Number.isFinite(x)))].sort((a, b) => a - b);
  const metrics = SMOOTH_METRICS.map((metric) => ({
    metric,
    limit: cell(s, { scope: "limit", method: "threshold", metric })?.value ?? null,
    ours: maxOf(select(ownFolds, { metric })),
    before: maxOf(select(s, { scope: "fold", method: "g1_before", metric })),
    vegas: cell(s, { scope: "reference", method: "nflfastr_vegas_wp", metric })?.value ?? null,
  })).filter((m) => m.limit !== null);
  const folds = seasons.length ? `${seasons[0]}-${seasons[seasons.length - 1]}` : null;
  // the seasons every candidate fix was judged on (never a test season)
  const validation = select(s, { scope: "validation" }).find((r) => r.subset)?.subset ?? null;
  return { metrics, folds, validation };
}

export type SubmodelLine = {
  key: "conversion" | "fieldgoal" | "punt" | "pat" | "two_point";
  span: string | null;
  metric: "log_loss" | "log_score";
  model: Cell | null;
  base: Cell | null;
  diff: Cell | null;
  baseMethod: string;
};

const SUBMODELS: { key: SubmodelLine["key"]; section: string; scope: string; metric: SubmodelLine["metric"]; base: string; diff: string }[] = [
  { key: "conversion", section: "conversion", scope: "down 4", metric: "log_loss", base: "lookup", diff: "model - lookup" },
  { key: "fieldgoal", section: "fieldgoal", scope: "all kicks", metric: "log_loss", base: "lookup", diff: "model - lookup" },
  { key: "punt", section: "punt", scope: "log score", metric: "log_score", base: "raw same-yardline history", diff: "model - raw" },
  { key: "pat", section: "tries", scope: "pat", metric: "log_loss", base: "naive", diff: "model - naive" },
  { key: "two_point", section: "tries", scope: "two_point", metric: "log_loss", base: "naive", diff: "model - naive" },
];

/** One line per sub-model: its pooled metric against its simple baseline, and the difference. */
export function submodelLines(rows: readonly TrackRow[]): SubmodelLine[] {
  return SUBMODELS.map((d) => {
    const at = { source: "submodels", section: d.section, scope: d.scope };
    const span = widestSpan(select(rows, at).map((r) => r.subset));
    const w = { ...at, subset: span ?? undefined, metric: d.metric };
    return {
      key: d.key,
      span,
      metric: d.metric,
      model: span ? cell(rows, { ...w, method: "model" }) : null,
      base: span ? cell(rows, { ...w, method: d.base }) : null,
      diff: span ? cell(rows, { ...w, method: d.diff }) : null,
      baseMethod: d.base,
    };
  });
}

export type Nfl4thRow = { subset: string; value: number; n: number | null };

/** The nfl4th benchmark: agreement on the recommended option (all benchmarked fourth downs, our
 *  clear calls, our toss-ups; per season and 'all') and the league go rates (ours, nfl4th's,
 *  what coaches really did). */
export function nfl4th(rows: readonly TrackRow[]) {
  const b = select(rows, { source: "nfl4th_benchmark" });
  if (!b.length) return null;
  const of = (section: string, scope: string | null, method: string): Nfl4thRow[] =>
    select(b, { section, method, ...(scope ? { scope } : {}) })
      .filter((r): r is TrackRow & { subset: string; value: number } => r.subset !== null && r.value !== null)
      .map((r) => ({ subset: r.subset, value: r.value, n: r.n }));
  const seasons = [...new Set(b.map((r) => r.subset).filter((s): s is string => !!s && s !== "all"))].sort();
  const pooled = (xs: Nfl4thRow[]) => xs.find((x) => x.subset === "all") ?? null;
  return {
    seasons,
    agreement: {
      all: pooled(of("agreement", "all", "nfl4th")),
      clear: pooled(of("agreement", "clear", "nfl4th")),
      tossUp: pooled(of("agreement", "toss_up", "nfl4th")),
      bySeason: seasons.map((s) => ({ season: s, all: of("agreement", "all", "nfl4th").find((x) => x.subset === s) ?? null, clear: of("agreement", "clear", "nfl4th").find((x) => x.subset === s) ?? null })),
    },
    goRate: {
      ours: pooled(of("go_rate", null, "ours")),
      nfl4th: pooled(of("go_rate", null, "nfl4th")),
      real: pooled(of("go_rate", null, "real")),
    },
  };
}

/** A sub-model against its baseline, from the difference's 95% interval: "better", "worse" or
 *  "no clear difference" (log loss: lower is better; log score: higher is better). */
export function verdict(l: Pick<SubmodelLine, "metric" | "diff">): "better" | "worse" | "no clear difference" | null {
  const d = l.diff;
  if (!d || d.lo === null || d.hi === null) return null;
  const sign = l.metric === "log_score" ? 1 : -1;
  const [lo, hi] = sign > 0 ? [d.lo, d.hi] : [-d.hi, -d.lo];
  if (lo > 0) return "better";
  if (hi < 0) return "worse";
  return "no clear difference";
}
