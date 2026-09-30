// The K and D/ST streamer on the site: pure helpers (unit-tested in tests/unit/streamer.test.ts).
// Numbers come from the database only (stream_track_record, stream_pick, stream_outcome and the
// glossary); this file only picks rows and words.

export const STREAM_POSITIONS = ["K", "DST"] as const;

export function isStreamPosition(p: string): boolean {
  return (STREAM_POSITIONS as readonly string[]).includes(p);
}

/** One row of stream_track_record (reports/streamer/backtest.csv). */
export type StreamTrackRow = {
  position: string;
  method: string;
  trainOn: string;
  scope: string;
  seasons: string;
  key: string | null;
  metric: string;
  value: number | null;
  lo: number | null;
  hi: number | null;
  nGroups: number | null;
  nRows: number | null;
  nPos: number | null;
};

/** The published list's method (model_versions.model: "logit_k", "baseline_opponent_dst") as the
 *  track record names it ("logit", "baseline_opponent"). */
export function streamMethodOf(model: string): string {
  return model.replace(/_(k|dst)$/i, "");
}

export function isBaselineMethod(method: string): boolean {
  return method.startsWith("baseline_");
}

export function streamMethodName(method: string, position: string): string {
  switch (method) {
    case "logit":
      return "our model, a logistic regression";
    case "lgbm":
      return "LightGBM, a tree model";
    case "baseline_last_points":
      return "last game's points";
    case "baseline_ppg":
      return "points per game so far";
    case "baseline_opponent":
      return position === "K"
        ? "the next opponent's points allowed per game"
        : "the next opponent's points scored per game, fewer first";
    default:
      return method;
  }
}

export type Verdict = "ahead-clearly" | "ahead" | "behind-clearly" | "behind" | "tied";

export function verdictOf(value: number, lo: number | null, hi: number | null): Verdict {
  if (lo !== null && lo > 0) return "ahead-clearly";
  if (hi !== null && hi < 0) return "behind-clearly";
  if (value > 0) return "ahead";
  if (value < 0) return "behind";
  return "tied";
}

export function verdictWords(v: Verdict): string {
  return {
    "ahead-clearly": "ahead, and the interval excludes zero",
    ahead: "ahead, but not clearly (the interval includes zero)",
    "behind-clearly": "behind, and the interval excludes zero",
    behind: "behind, but not clearly (the interval includes zero)",
    tied: "level",
  }[v];
}

export type StreamComparison = {
  position: string;
  seasons: string;
  metric: string;
  /** the published method and its precision@5 */
  ours: { method: string; value: number };
  /** a model when ours is a rule, else the best simple rule */
  other: { method: string; value: number };
  /** ours minus other, in rate units, with its 95% interval (null when not published) */
  diff: { value: number; lo: number | null; hi: number | null } | null;
  verdict: Verdict | null;
  /** how often a random pool pick scored like a starter (n_pos / n_rows) */
  baseRate: number | null;
};

/** The published method against the strongest alternative of the other kind, from the
 *  pooled precision@5 rows: a model against the best simple rule, a rule against the best
 *  model. The difference is the track record's paired `diff` row (negated when ours is the
 *  rule, whose row is written the other way round). */
export function streamComparison(rows: readonly StreamTrackRow[], position: string, method: string, metric = "p_at_5"): StreamComparison | null {
  const pooled = rows.filter(
    (r) => r.position === position && r.scope === "pooled" && r.trainOn === "pool" && r.metric === metric && r.value !== null,
  );
  const ours = pooled.find((r) => r.method === method);
  if (!ours || ours.value === null) return null;
  const rivals = pooled.filter((r) => isBaselineMethod(r.method) !== isBaselineMethod(method));
  const best = [...rivals].sort((a, b) => (b.value ?? 0) - (a.value ?? 0) || (a.method < b.method ? -1 : 1))[0];
  if (!best || best.value === null) return null;
  const rule = isBaselineMethod(method);
  const model = rule ? best.method : method;
  const baseline = rule ? method : best.method;
  const d = rows.find(
    (r) => r.position === position && r.scope === "diff" && r.trainOn === "pool" && r.metric === metric && r.method === model && r.key === baseline,
  );
  let diff: StreamComparison["diff"] = null;
  if (d && d.value !== null) {
    diff = rule ? { value: -d.value, lo: d.hi === null ? null : -d.hi, hi: d.lo === null ? null : -d.lo } : { value: d.value, lo: d.lo, hi: d.hi };
  }
  return {
    position,
    seasons: ours.seasons,
    metric,
    ours: { method, value: ours.value },
    other: { method: best.method, value: best.value },
    diff,
    verdict: diff ? verdictOf(diff.value, diff.lo, diff.hi) : null,
    baseRate: ours.nRows && ours.nPos !== null ? ours.nPos / ours.nRows : null,
  };
}

/** The top-N that counts as a starter week for K and DST, read from the published y_start
 *  formula ("... top 12 K and top 12 DST in this league ..."); missing when unreadable. */
export function streamTopN(rows: readonly { name: string; formula: string }[]): Record<string, number> {
  const f = rows.find((r) => r.name === "y_start")?.formula ?? "";
  const out: Record<string, number> = {};
  for (const m of f.matchAll(/\btop (\d{1,3}) (K|DST)\b/g)) if (!(m[2] in out)) out[m[2]] = Number(m[1]);
  return out;
}

/** "top-12" or "starter" words for a position. */
export function starterWeek(topN: Record<string, number>, position: string): string {
  const n = topN[position];
  return n ? `top-${n} week` : "starter week";
}

/** "at PHI" / "vs NYG" / "NYG (home or away not known)"; null without an opponent. */
export function nextGame(opponent: string | null, home: boolean | null): string | null {
  if (!opponent) return null;
  if (home === true) return `vs ${opponent} (home)`;
  if (home === false) return `at ${opponent} (away)`;
  return `${opponent} (home or away not known yet)`;
}

/** The next week's result in words: finish and points when final. */
export function streamResult(
  yStart: boolean | null,
  status: string | null,
  points: number | null,
  topN: Record<string, number>,
  position: string,
): string | null {
  if (status !== "final") return null;
  const n = topN[position];
  const finish = yStart === true ? (n ? `top ${n}` : "a starter week") : yStart === false ? (n ? `outside the top ${n}` : "not a starter week") : null;
  const pts = points === null ? "no points recorded (no game or no kick)" : `${points.toFixed(1)} pts`;
  return finish ? `Next week: ${finish}, ${pts}` : `Next week: ${pts}`;
}
