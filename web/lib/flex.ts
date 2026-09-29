// The FLEX list: one week's RB, WR and TE lists merged into one, ordered by chance. It is a
// way to browse the three lists together, not a separate model: each chance is the chance of a
// starter finish AT HIS OWN POSITION, so FLEX compares three slightly different targets (the
// pages say so). Pure functions, unit-tested (tests/unit/flex.test.ts); the queries only fetch
// the rows (lib/queries/radar.ts).
import type { RankCount } from "./buckets";
import { displayOrder, isFlexPosition } from "./positions";

/** The FLEX list is as long as a position's list: its top 25. */
export const FLEX_SIZE = 25;

export type FlexCandidate = {
  position: string;
  /** his rank in his own position's list */
  rank: number;
  chance: number | null;
  /** the model's calibrated probability: a tie-break only, never shown */
  modelProb: number;
  gsisId: string;
};

/** Highest chance first (a missing chance after every chance), then the higher model
 *  probability, then the better rank in his own list, then RB, WR, TE and the player id (so the
 *  order never depends on the order the rows arrive in). */
export function compareFlex(a: FlexCandidate, b: FlexCandidate): number {
  if (a.chance !== b.chance) {
    if (a.chance === null) return 1;
    if (b.chance === null) return -1;
    return b.chance - a.chance;
  }
  if (a.modelProb !== b.modelProb) return b.modelProb - a.modelProb;
  if (a.rank !== b.rank) return a.rank - b.rank;
  const pos = displayOrder(a.position) - displayOrder(b.position);
  if (pos !== 0) return pos;
  return a.gsisId < b.gsisId ? -1 : a.gsisId > b.gsisId ? 1 : 0;
}

export type Flexed<T> = T & { flexRank: number };

/** The FLEX list of one week and kind: the RB, WR and TE picks (other positions are ignored),
 *  ordered by compareFlex, each player once, cut to `size`. */
export function mergeFlex<T extends FlexCandidate>(picks: readonly T[], size: number = FLEX_SIZE): Flexed<T>[] {
  const sorted = picks.filter((p) => isFlexPosition(p.position)).sort(compareFlex);
  const seen = new Set<string>();
  const out: Flexed<T>[] = [];
  for (const p of sorted) {
    if (out.length >= size) break;
    if (seen.has(p.gsisId)) continue;
    seen.add(p.gsisId);
    out.push({ ...p, flexRank: out.length + 1 });
  }
  return out;
}

/** What ordered a merged list: every pick has a chance ("chance"), none has ("model": the
 *  2014 reconstructed lists), some have ("mixed"), or there are no picks. */
export type FlexOrder = "chance" | "model" | "mixed" | "empty";

export function flexOrderOf(picks: readonly { chance: number | null }[]): FlexOrder {
  if (picks.length === 0) return "empty";
  const withChance = picks.filter((p) => p.chance !== null).length;
  if (withChance === picks.length) return "chance";
  if (withChance === 0) return "model";
  return "mixed";
}

export type FlexHistoryRow = FlexCandidate & {
  season: number;
  week: number;
  yHit: boolean | null;
  status: string | null;
};

export type FlexCounts = {
  counts: RankCount[];
  seasonFrom: number | null;
  seasonTo: number | null;
  lists: number;
};

/** The hit-rate badges' counts for FLEX, the same way as a position's: every week's merged
 *  list is rebuilt from that week's (reconstructed) RB, WR and TE picks, and each FLEX rank's
 *  picks and hits are counted where the outcome is final. `rows` must hold every pick of those
 *  weeks (a pick without a final outcome still takes its FLEX rank). */
export function flexRankCounts(rows: readonly FlexHistoryRow[], size: number = FLEX_SIZE): FlexCounts {
  const weeks = new Map<string, FlexHistoryRow[]>();
  for (const r of rows) {
    const k = `${r.season}-${r.week}`;
    const list = weeks.get(k);
    if (list) list.push(r);
    else weeks.set(k, [r]);
  }
  const byRank = new Map<number, RankCount>();
  let lists = 0;
  let seasonFrom: number | null = null;
  let seasonTo: number | null = null;
  for (const week of weeks.values()) {
    let counted = false;
    for (const p of mergeFlex(week, size)) {
      if (p.status !== "final" || p.yHit === null) continue;
      counted = true;
      const c = byRank.get(p.flexRank) ?? { rank: p.flexRank, picks: 0, hits: 0 };
      c.picks += 1;
      c.hits += p.yHit ? 1 : 0;
      byRank.set(p.flexRank, c);
    }
    if (counted) {
      lists += 1;
      const s = week[0].season;
      seasonFrom = seasonFrom === null ? s : Math.min(seasonFrom, s);
      seasonTo = seasonTo === null ? s : Math.max(seasonTo, s);
    }
  }
  return { counts: [...byRank.values()].sort((a, b) => a.rank - b.rank), seasonFrom, seasonTo, lists };
}
