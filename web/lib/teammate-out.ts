// The Teammate-out page's pure helpers (feature #5, docs/teammate_out.md; unit-tested in
// tests/unit/teammate-out.test.ts). They only pick, order and word published rows: every number
// comes from the teammate_out_* tables and the pinned version's params
// (src/twm/publish/teammate_out.py).
import { fmtPoints } from "@/lib/format";

/** src/twm/modules/teammate_out/weekly.py NOTE, word for word (a unit test checks). */
export const INACTIVES_NOTE = "Inactives come out about 90 minutes before kickoff: a Doubtful starter may still play, and then nobody gains.";

/** When the lists fill in (docs/teammate_out.md "The live list"). */
export const WHEN_LISTS_FILL =
  "Lists fill in as teams post their injury reports (final reports on Friday for Sunday games, earlier for Thursday and Saturday games) and move players to reserve lists. The site updates nightly.";

export type TOWeek = { season: number; week: number };

export type TOSnapshot = TOWeek & {
  asOf: string;
  generatedAt: string;
  modelVersion: string;
  nTeams: number;
  nOut: number;
  nPlayers: number;
  source: string;
};

export type TORow = {
  team: string;
  gsisId: string;
  name: string;
  opponent: string | null;
  gameId: string | null;
  kickoff: string;
  outIds: string;
  outPlayers: string | null;
  outPositions: string | null;
  outReasons: string | null;
  nOut: number;
  vacCarryShare: number | null;
  vacTargetShare: number | null;
  position: string;
  role: string;
  baseGames: number | null;
  baseCarryShare: number | null;
  baseTargetShare: number | null;
  basePoints: number | null;
  predCarryShare: number | null;
  predTargetShare: number | null;
  predPoints: number;
  pointsLo: number | null;
  pointsHi: number | null;
  predGain: number | null;
};

export type TOAllocationRow = { outPos: string; role: string; position: string; n: number; carry: number | null; target: number | null; nGroup: number };
export type TOBacktestRow = { candidate: string; title: string; chosen: boolean; rulePick: boolean; season: string; n: number; events: number; maeCarry: number | null; maeTarget: number | null; maePoints: number | null; topHit: number | null };
export type TOCoverageRow = { position: string; seasons: string; n: number; below: number; above: number; inside: number; coverage: number | null };
export type TOEventsRow = { outPos: string; seasons: string; sat: number; kept: number; events: number; single: number; multi: number; teammateRows: number };
export type TOLiveRow = {
  season: number;
  n: number;
  pending: number;
  starterPlayed: number;
  didNotPlay: number;
  weeks: number;
  teamWeeks: number;
  ranged: number;
  maePoints: number | null;
  maePointsBase: number | null;
  maeCarryShare: number | null;
  maeCarryShareBase: number | null;
  maeTargetShare: number | null;
  maeTargetShareBase: number | null;
  coverage: number | null;
  topHit: number | null;
};

/** The pinned version's params as published (model_versions.params): the choice and why. */
export type TOParams = {
  chosen?: string;
  rule_choice?: string;
  chosen_by?: string;
  rule?: string;
  path?: string[];
  ranges?: { level?: number; q_lo?: number; q_hi?: number; min_misses?: number };
  event_rule?: { window?: number; rb_carry_share?: number; target_share?: number; min_base_games?: number };
  test_seasons?: number[];
  train_seasons?: string;
};

/** A /teammate-out link. */
export function teammateOutHref(w?: TOWeek): string {
  return w ? `/teammate-out?season=${w.season}&week=${w.week}` : "/teammate-out";
}

export type Absent = { gsisId: string; name: string; position: string | null; reason: string | null };

/** The absent starters a row names, split from out_ids (',') and out_players / out_positions /
 *  out_reasons (', ', same order). A list of another length than the ids is not used (the id
 *  stands in for the name; position and reason stay unknown). */
export function absentStarters(r: Pick<TORow, "outIds" | "outPlayers" | "outPositions" | "outReasons">): Absent[] {
  const ids = r.outIds.split(",").filter(Boolean);
  const part = (s: string | null) => {
    const xs = s ? s.split(", ") : [];
    return xs.length === ids.length ? xs : null;
  };
  const names = part(r.outPlayers);
  const pos = part(r.outPositions);
  const why = part(r.outReasons);
  return ids.map((id, i) => ({ gsisId: id, name: names?.[i] ?? id, position: pos?.[i] ?? null, reason: why?.[i] ?? null }));
}

const ROSTER: Record<string, string> = {
  RES: "on a reserve list (injured reserve and similar)",
  PUP: "on the PUP list",
  SUS: "suspended",
  NON: "on the non-football injury list",
  EXE: "on the exempt list",
};

/** Why a starter is out, in words: "Out" / "Doubtful" (the week's injury report) or a roster
 *  status ("roster RES" -> "on a reserve list (injured reserve and similar)"). */
export function reasonLabel(reason: string | null): string {
  if (!reason) return "out";
  if (reason === "Out") return "Out (injury report)";
  if (reason === "Doubtful") return "Doubtful (injury report)";
  const m = /^roster (\w+)$/.exec(reason);
  if (m) return ROSTER[m[1]] ?? `roster status ${m[1]}`;
  return reason;
}

export type TeamGroup = { team: string; opponent: string | null; gameId: string | null; absent: Absent[]; rows: TORow[] };
export type GameGroup = { kickoff: string; teams: TeamGroup[] };

/** The rows by kickoff (earliest first), then team (A-Z); a team's teammates by predicted
 *  points (highest first), then name. */
export function gameGroups(rows: readonly TORow[]): GameGroup[] {
  const sorted = [...rows].sort(
    (a, b) => Date.parse(a.kickoff) - Date.parse(b.kickoff) || a.team.localeCompare(b.team) || b.predPoints - a.predPoints || a.name.localeCompare(b.name),
  );
  const out: GameGroup[] = [];
  for (const r of sorted) {
    let g = out[out.length - 1];
    if (!g || Date.parse(g.kickoff) !== Date.parse(r.kickoff)) {
      g = { kickoff: r.kickoff, teams: [] };
      out.push(g);
    }
    let t = g.teams[g.teams.length - 1];
    if (!t || t.team !== r.team) {
      t = { team: r.team, opponent: r.opponent, gameId: r.gameId, absent: absentStarters(r), rows: [] };
      g.teams.push(t);
    }
    t.rows.push(r);
  }
  return out;
}

/** A share's move: (0.2, 0.28) -> "20% → 28% (+8)"; the change in percentage points, whole. */
export function shareMove(base: number | null, pred: number | null): string {
  if (base === null || pred === null) return "–";
  const b = Math.round(base * 100);
  const p = Math.round(pred * 100);
  const d = p - b;
  return `${b}% → ${p}% (${d > 0 ? "+" : d < 0 ? "−" : "±"}${Math.abs(d)})`;
}

/** Predicted points and the 80% range: "11.0 (7.0–17.0)". */
export function pointsWithRange(r: Pick<TORow, "predPoints" | "pointsLo" | "pointsHi">): string {
  const range = r.pointsLo === null || r.pointsHi === null ? "" : ` (${fmtPoints(r.pointsLo)}–${fmtPoints(r.pointsHi)})`;
  return `${fmtPoints(r.predPoints)}${range}`;
}

/** A signed points gain: 2.46 -> "+2.5", -0.5 -> "−0.5", 0 -> "±0.0". */
export function gainText(x: number | null): string {
  if (x === null) return "–";
  const s = Math.abs(x).toFixed(1);
  return Number(s) === 0 ? "±0.0" : `${x > 0 ? "+" : "−"}${s}`;
}

/** The rest of the gap between the prediction and his usual points: the model pulls his points
 * per carry or target toward his position's average (src/twm/modules/teammate_out/table.py,
 * PPO_PSEUDO), which is what the backtest scored. "" when it is under half a point. */
export function efficiencyText(r: Pick<TORow, "predPoints" | "basePoints" | "predGain">): string {
  if (r.basePoints === null || r.predGain === null) return "";
  const rest = r.predPoints - r.basePoints - r.predGain;
  if (Math.abs(rest) < 0.5) return "";
  return `; his points per carry or target pulled toward the position average: ${gainText(rest)}`;
}

export const CANDIDATE_ORDER = ["nothing", "pro_rata", "group", "role"] as const;

export type Disclosure = { rows: TOBacktestRow[]; rulePick: TOBacktestRow; used: TOBacktestRow; overridden: boolean; why: string | null; seasons: string[] };

/** The pooled ('all') walk-forward rows of the four candidates (simplest first), which one the
 *  pre-set rule picked (rule_pick), which one the site uses (chosen) and the published reason
 *  (params.chosen_by; null when the rule's pick is used). Null when either is missing. */
export function disclosure(rows: readonly TOBacktestRow[], params: TOParams | null): Disclosure | null {
  const rank = (c: string) => {
    const i = (CANDIDATE_ORDER as readonly string[]).indexOf(c);
    return i === -1 ? CANDIDATE_ORDER.length : i;
  };
  const all = rows.filter((r) => r.season === "all").sort((a, b) => rank(a.candidate) - rank(b.candidate));
  const rulePick = all.find((r) => r.rulePick);
  const used = all.find((r) => r.chosen);
  if (!rulePick || !used) return null;
  const overridden = rulePick.candidate !== used.candidate;
  const why = overridden ? (params?.chosen_by?.trim() || null) : null;
  const seasons = [...new Set(rows.filter((r) => r.season !== "all").map((r) => r.season))].sort();
  return { rows: all, rulePick, used, overridden, why, seasons };
}

/** The lowest value of a metric among rows (to mark the best cell); null when none. */
export function best(rows: readonly TOBacktestRow[], key: "maePoints" | "maeCarry" | "maeTarget" | "topHit"): number | null {
  const xs = rows.map((r) => r[key]).filter((x): x is number => x !== null);
  if (!xs.length) return null;
  return key === "topHit" ? Math.max(...xs) : Math.min(...xs);
}

export const OUT_POS_ORDER = ["RB", "WR", "TE"] as const;

/** The allocation table by absent position (RB, WR, TE), each by teammate position then role
 *  ("RB2" before "RB3" before "RB+"). */
export function allocationGroups(rows: readonly TOAllocationRow[]): { outPos: string; rows: TOAllocationRow[] }[] {
  const roleRank = (r: string) => (r.endsWith("+") ? 99 : Number(r.slice(2)) || 0);
  const posRank = (p: string) => (OUT_POS_ORDER as readonly string[]).indexOf(p);
  return OUT_POS_ORDER.map((p) => ({
    outPos: p,
    rows: rows.filter((r) => r.outPos === p).sort((a, b) => posRank(a.position) - posRank(b.position) || roleRank(a.role) - roleRank(b.role)),
  })).filter((g) => g.rows.length > 0);
}

/** The live record of a season (else the newest published), or null. */
export function liveRecord(rows: readonly TOLiveRow[], season: number | null): TOLiveRow | null {
  return rows.find((r) => season !== null && r.season === season) ?? (season === null ? (rows[0] ?? null) : null);
}

/** The coverage rows: 'all' first, then RB, WR, TE. */
export function coverageRows(rows: readonly TOCoverageRow[]): TOCoverageRow[] {
  const order = ["all", ...OUT_POS_ORDER] as string[];
  return [...rows].sort((a, b) => order.indexOf(a.position) - order.indexOf(b.position));
}

/** The page's week: the asked one when both parts are given, else the week whose games are
 *  next (site_meta teammate_out_season / _week), else the newest week with a snapshot. */
export function chooseWeek(asked: { season: number | null; week: number | null }, current: TOWeek | null, index: readonly TOWeek[]): TOWeek | null {
  if (asked.season !== null && asked.week !== null) return { season: asked.season, week: asked.week };
  return current ?? index[0] ?? null;
}

/** Whether a week with no list may still get one: the week whose games are next or a later
 *  regular-season week (18 at most) of the same season. A past or impossible week (week 99)
 *  never will, so its empty state must not say "yet". */
export function listMayCome(w: TOWeek, current: TOWeek | null): boolean {
  return current !== null && w.season === current.season && w.week >= current.week && w.week <= 18;
}
