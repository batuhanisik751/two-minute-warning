// The /track-record page's pure helpers (unit-tested in tests/unit/track-record.test.ts). They
// only pick and count published rows: every number comes from the database (track_record,
// stream_track_record, decisions_track_record, the live lists and their outcomes).
import type { TrackRow as DecisionsTrackRow } from "@/lib/decisions-track";
import type { TrackRow } from "@/lib/queries/track";
import type { StreamTrackRow } from "@/lib/streamer";

/** "p_at_10" -> 10: the top N a precision@N metric counts (null when the name is not one). */
export function topNOf(metric: string): number | null {
  const m = /^p_at_(\d{1,3})$/.exec(metric);
  return m ? Number(m[1]) : null;
}

export type Bin = { lo: number; hi: number };

/** A probability group's key ("0.3-0.4") as numbers; null when it is not one. */
export function parseBin(key: string | null): Bin | null {
  const m = key ? /^(\d(?:\.\d+)?)-(\d(?:\.\d+)?)$/.exec(key) : null;
  if (!m) return null;
  const lo = Number(m[1]);
  const hi = Number(m[2]);
  return lo < hi && lo >= 0 && hi <= 1 ? { lo, hi } : null;
}

/** One probability group of a calibration plot: the average prediction (null when it is not
 *  published), how often the event really happened (with its interval where published) and the
 *  number of predictions in the group. */
export type CalPoint = Bin & {
  key: string;
  predicted: number | null;
  observed: number;
  observedLo: number | null;
  observedHi: number | null;
  n: number | null;
};

const byLo = (a: CalPoint, b: CalPoint) => a.lo - b.lo;

/** The Radar's calibration (scope calibration_fixed) of one model and label, all pool rows. */
export function radarCalibration(rows: readonly TrackRow[], model: string, label: string): CalPoint[] {
  const cal = rows.filter((r) => r.scope === "calibration_fixed" && r.model === model && r.label === label && !r.exclRostered);
  const out: CalPoint[] = [];
  for (const key of new Set(cal.map((r) => r.scopeValue))) {
    const bin = parseBin(key);
    const obs = cal.find((r) => r.scopeValue === key && r.metric === "observed");
    if (!bin || !obs || obs.value === null) continue;
    const pred = cal.find((r) => r.scopeValue === key && r.metric === "mean_pred");
    out.push({ key, ...bin, predicted: pred?.value ?? null, observed: obs.value, observedLo: null, observedHi: null, n: obs.nRows });
  }
  return out.sort(byLo);
}

/** The streamer's calibration of one position (scope calibration: observed rates only) and the
 *  method it belongs to (null when none is published). */
export function streamCalibration(rows: readonly StreamTrackRow[], position: string): { method: string | null; points: CalPoint[] } {
  const cal = rows.filter((r) => r.position === position && r.scope === "calibration" && r.metric === "observed");
  const method = cal[0]?.method ?? null;
  const points: CalPoint[] = [];
  for (const r of cal.filter((x) => x.method === method)) {
    const bin = parseBin(r.key);
    if (!bin || r.value === null) continue;
    points.push({ key: r.key as string, ...bin, predicted: null, observed: r.value, observedLo: r.lo, observedHi: r.hi, n: r.nRows });
  }
  return { method, points: points.sort(byLo) };
}

/** The WP model's reliability bins (wp_backtest, section reliability, pooled) of one method. */
export function wpCalibration(rows: readonly DecisionsTrackRow[], method: string): CalPoint[] {
  const rel = rows.filter((r) => r.source === "wp_backtest" && r.section === "reliability" && r.method === method);
  const points: CalPoint[] = [];
  for (const key of new Set(rel.map((r) => r.subset))) {
    const bin = parseBin(key);
    const obs = rel.find((r) => r.subset === key && r.metric === "observed");
    if (!bin || !obs || obs.value === null) continue;
    const pred = rel.find((r) => r.subset === key && r.metric === "mean_predicted");
    points.push({ key: key as string, ...bin, predicted: pred?.value ?? null, observed: obs.value, observedLo: obs.lo, observedHi: obs.hi, n: obs.n });
  }
  return points.sort(byLo);
}

// ---------------------------------------------------------------------------------------
// Seasons tables: one row per test season, newest first
// ---------------------------------------------------------------------------------------

export type RadarSeason = { season: number; ours: TrackRow | null; other: TrackRow | null; baseRate: TrackRow | null };

/** The Radar's per-season precision@N (scope season) of `model` and `other`, all pool rows. */
export function radarSeasons(rows: readonly TrackRow[], model: string, other: string, label: string, metric: string): RadarSeason[] {
  const s = rows.filter((r) => r.scope === "season" && r.label === label && !r.exclRostered && r.seasonFrom === r.seasonTo);
  const at = (season: number, m: string, met: string) => s.find((r) => r.seasonFrom === season && r.model === m && r.metric === met) ?? null;
  const seasons = [...new Set(s.filter((r) => r.metric === metric).map((r) => r.seasonFrom))].sort((a, b) => b - a);
  return seasons.map((season) => ({
    season,
    ours: at(season, model, metric),
    other: at(season, other, metric),
    baseRate: s.find((r) => r.seasonFrom === season && r.metric === "base_rate") ?? null,
  }));
}

export type StreamSeason = { season: string; ours: StreamTrackRow | null; other: StreamTrackRow | null };

/** The streamer's per-season rows (scope season) of one position: `ours` and `other`. */
export function streamSeasons(rows: readonly StreamTrackRow[], position: string, ours: string, other: string, metric: string): StreamSeason[] {
  const s = rows.filter((r) => r.position === position && r.scope === "season" && r.trainOn === "pool" && r.metric === metric);
  const seasons = [...new Set(s.map((r) => r.seasons))].sort((a, b) => b.localeCompare(a));
  return seasons.map((season) => ({
    season,
    ours: s.find((r) => r.seasons === season && r.method === ours) ?? null,
    other: s.find((r) => r.seasons === season && r.method === other) ?? null,
  }));
}

type Num = { value: number; lo: number | null; hi: number | null };
const num = (r: DecisionsTrackRow | undefined): Num | null => (r && r.value !== null ? { value: r.value, lo: r.lo, hi: r.hi } : null);

export type WpSeason = { season: string; n: number | null; brier: Num | null; logLoss: Num | null; vsWp: Num | null; vsVegas: Num | null };

/** The WP model's per-season test results (wp_backtest, section season): our Brier and log loss
 *  and the log-loss difference against nflfastR's two models (with season-block intervals). */
export function wpSeasons(rows: readonly DecisionsTrackRow[]): WpSeason[] {
  const s = rows.filter((r) => r.source === "wp_backtest" && r.section === "season" && r.scope !== null);
  const seasons = [...new Set(s.map((r) => r.scope as string))].sort((a, b) => b.localeCompare(a));
  return seasons.map((season) => {
    const at = (method: string, metric: string) => s.find((r) => r.scope === season && r.method === method && r.metric === metric);
    const own = at("own", "log_loss");
    return {
      season,
      n: own?.n ?? null,
      brier: num(at("own", "brier")),
      logLoss: num(own),
      vsWp: num(at("own - nflfastr_wp", "log_loss")),
      vsVegas: num(at("own - nflfastr_vegas_wp", "log_loss")),
    };
  });
}

// ---------------------------------------------------------------------------------------
// Live results: lists made in real time, graded only once their outcome is final
// ---------------------------------------------------------------------------------------

/** One pick of a live list and its outcome (hit: y_hit / y_start; NULL while unknown). */
export type LivePick = { season: number; week: number; position: string; rank: number; hit: boolean | null; status: string | null };

export type LiveCount = { lists: number; picks: number; graded: number; hits: number; pending: number; notGraded: number };
export type LiveWeek = LiveCount & { season: number; week: number };

const zero = (): LiveCount => ({ lists: 0, picks: 0, graded: 0, hits: 0, pending: 0, notGraded: 0 });

function add(c: LiveCount, p: Pick<LivePick, "hit" | "status">) {
  c.picks++;
  if (p.status !== "final") c.pending++;
  else if (p.hit === null) c.notGraded++;
  else {
    c.graded++;
    if (p.hit) c.hits++;
  }
}

/** The top `topN` of every live list, counted per week (newest first) and in total: graded =
 *  a final outcome; pending outcomes are never counted as misses. */
export function liveSummary(picks: readonly LivePick[], topN: number): { weeks: LiveWeek[]; total: LiveCount } {
  const weeks = new Map<string, LiveWeek & { keys: Set<string> }>();
  const total = zero();
  const lists = new Set<string>();
  for (const p of picks) {
    const k = `${p.season}-${p.week}`;
    const list = `${k}-${p.position}`;
    let w = weeks.get(k);
    if (!w) weeks.set(k, (w = { season: p.season, week: p.week, ...zero(), keys: new Set() }));
    w.keys.add(list);
    lists.add(list);
    if (p.rank > topN) continue;
    add(w, p);
    add(total, p);
  }
  total.lists = lists.size;
  const out = [...weeks.values()].map(({ keys, ...w }) => ({ ...w, lists: keys.size }));
  return { weeks: out.sort((a, b) => b.season - a.season || b.week - a.week), total };
}

/** Did a Regression Watch tag come true (src/twm/modules/regression_watch/tags.py hit_exprs)?
 *  Sell-high: his rest-of-season PPG was below his PPG at the list; buy-low: above it. NULL while
 *  the outcome is not known (or he played too few games to grade: ros_ppg is NULL then). */
export function tagCameTrue(tag: string, ppg: number, rosPpg: number | null): boolean | null {
  if (rosPpg === null) return null;
  if (tag === "sell_high") return rosPpg < ppg;
  if (tag === "buy_low") return rosPpg > ppg;
  return null;
}

/** One tagged row of a live Regression Watch list and its rest-of-season outcome. */
export type LiveTagRow = { season: number; week: number; tags: string[]; ppg: number; rosPpg: number | null; status: string | null };

/** Every live tag of `tags`, counted like liveSummary (one "list" per week; rank is unused). */
export function liveTagSummary(rows: readonly LiveTagRow[], tags: readonly string[]): Record<string, { weeks: LiveWeek[]; total: LiveCount }> {
  const out: Record<string, { weeks: LiveWeek[]; total: LiveCount }> = {};
  for (const tag of tags) {
    const picks: LivePick[] = rows
      .filter((r) => r.tags.includes(tag))
      .map((r) => ({ season: r.season, week: r.week, position: "all", rank: 1, hit: tagCameTrue(tag, r.ppg, r.rosPpg), status: r.status }));
    out[tag] = liveSummary(picks, 1);
  }
  return out;
}

/** The axes' ticks of a calibration plot: every probability group's edges, in order. */
export function binEdges(points: readonly Bin[]): number[] {
  return [...new Set(points.flatMap((p) => [p.lo, p.hi]))].sort((a, b) => a - b);
}

/** The plot's marks: across, the group's average prediction (its middle when that is not
 *  published); up, how often it really happened. */
export function calMarks(points: readonly CalPoint[]): { x: number; y: number; n: number | null; mid: boolean; key: string }[] {
  return points.map((p) => ({ x: p.predicted ?? (p.lo + p.hi) / 2, y: p.observed, n: p.n, mid: p.predicted === null, key: p.key }));
}
