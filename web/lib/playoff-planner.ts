// The playoff planner's pure helpers (feature #6, docs/playoff_planner.md; unit-tested in
// tests/unit/playoff-planner.test.ts). They only pick, order and word published rows: every
// number comes from the playoff_planner_* tables and the pinned version's params
// (src/twm/publish/playoff_planner.py).

/** src/twm/modules/playoff_planner/weekly.py NOTE, word for word (a unit test checks). */
export const PLANNER_NOTE =
  "A matchup rating moves a player's usual points a little; his role, health and the weather move them more. Week 17 was the last NFL week until 2020: teams that had clinched rested starters then; since 2021 that is week 18.";

/** The site's fantasy playoff weeks when nothing is published (site_meta playoff_planner_weeks). */
export const DEFAULT_WEEKS = [15, 16, 17] as const;

/** Leagues set their own playoff weeks: the site shows 15-17. */
export const LEAGUES_DIFFER =
  "Most leagues play their fantasy playoffs in NFL weeks 15, 16 and 17, so the site shows those. Yours may differ: check your league's settings.";

/** The positions in the grid's order (the snapshot's codes: D/ST is "DST"). */
export const PP_POSITIONS = ["QB", "RB", "WR", "TE", "K", "DST"] as const;

/** A rating this far from 1.00 (or more) counts as easy (above) or hard (below). */
export const BAND = 0.05;

export type PPSnapshot = {
  season: number;
  throughWeek: number;
  asOf: string;
  generatedAt: string;
  modelVersion: string;
  nTeams: number;
  nRows: number;
};

export type PPRow = {
  week: number;
  team: string;
  position: string;
  opponent: string | null;
  home: boolean | null;
  candidate: string;
  rating: number | null;
  ratingRank: number | null;
  raw: number | null;
  shrunk: number | null;
  adjusted: number | null;
  oppGames: number | null;
};

export type PPChoice = { position: string; candidate: string; ruleChoice: string; chosenBy: string; path: string; pseudoGames: number };

export type PPBacktestRow = {
  position: string;
  candidate: string;
  title: string;
  n: number;
  mae: number;
  vs: string | null;
  seasonsWon: number | null;
  seasons: number;
  tookOver: boolean;
  chosen: boolean;
  rulePick: boolean;
};

export type PPEffect = { position: string; horizon: string; nWorst: number; nBest: number; ratedGap: number | null; shrunkGap: number | null; realizedGap: number | null; survived: number | null };
export type PPStability = { position: string; horizon: number; seasons: number; rhoMean: number | null; rhoMin: number | null; rhoMax: number | null };
export type PPLateWeek = { era: string; position: string; week: number; based: number; playedShare: number | null; vsBase: number | null };
export type PPLive = { season: number; position: string; n: number; pending: number; weeks: number; maeRating: number | null; maeFlat: number | null };

export type PPParams = { chosen?: Record<string, string>; rule_choice?: Record<string, string>; chosen_by?: string; rule?: string; test_seasons?: number[]; horizons?: number[]; season_wins_needed?: number; playoff_weeks?: number[] };

export type Band = "easy" | "neutral" | "hard";

/** easy: BAND or more above an average matchup (1.00); hard: BAND or more below. Judged on the
 *  rating as shown (two decimals), so the word always matches the number next to it. */
export function band(rating: number): Band {
  const r = Math.round(rating * 100) / 100;
  if (r >= 1 + BAND - 1e-9) return "easy";
  if (r <= 1 - BAND + 1e-9) return "hard";
  return "neutral";
}

export const BAND_LABEL: Record<Band, string> = { easy: "Easy", neutral: "Neutral", hard: "Hard" };

/** A rating as shown: two decimals ("1.08"). */
export function fmtRating(x: number): string {
  return x.toFixed(2);
}

/** A position the pinned rule rates ('none': every matchup counts as 1.00). */
export function isRated(candidate: string | undefined): boolean {
  return candidate !== undefined && candidate !== "none";
}

export type GridCell = { week: number; opponent: string | null; home: boolean | null; rating: number | null; rank: number | null };
export type GridTeam = { team: string; cells: GridCell[]; total: number | null; games: number };

/** "vs KC" (home), "@ KC" (away), "Bye". */
export function opponentLabel(c: { opponent: string | null; home: boolean | null }): string {
  if (!c.opponent) return "Bye";
  return `${c.home === false ? "@" : "vs"} ${c.opponent}`;
}

/** One position's grid: every team with its opponent in each of ``weeks`` (no row: a bye), the
 *  ratings when the position is rated, and the total over its games (a bye adds nothing). */
export function gridFor(rows: PPRow[], position: string, weeks: readonly number[]): { rated: boolean; teams: GridTeam[] } {
  const mine = rows.filter((r) => r.position === position);
  const rated = mine.some((r) => isRated(r.candidate));
  const byTeam = new Map<string, PPRow[]>();
  for (const r of mine) byTeam.set(r.team, [...(byTeam.get(r.team) ?? []), r]);
  const teams = [...byTeam.entries()].map(([team, rs]) => {
    const cells = weeks.map((week) => {
      const r = rs.find((x) => x.week === week);
      return { week, opponent: r?.opponent ?? null, home: r?.opponent ? r.home : null, rating: rated && r?.opponent ? r.rating : null, rank: rated && r?.opponent ? r.ratingRank : null };
    });
    const games = cells.filter((c) => c.opponent !== null).length;
    const total = rated ? Math.round(cells.reduce((a, c) => a + (c.rating ?? 0), 0) * 100) / 100 : null;
    return { team, cells, total, games };
  });
  return { rated, teams: teams.sort((a, b) => a.team.localeCompare(b.team)) };
}

export type SortKey = "team" | "total" | `w${number}`;

/** The grid's sort from the query string: "team" (default), "total" or "w15" / "w16" / "w17". */
export function parseSort(v: string | string[] | undefined, weeks: readonly number[]): SortKey {
  const s = Array.isArray(v) ? v[0] : v;
  if (s === "total") return "total";
  const m = /^w(\d{1,2})$/.exec(s ?? "");
  if (m && weeks.includes(Number(m[1]))) return `w${Number(m[1])}`;
  return "team";
}

/** Easiest first for "total" and a week (byes and unrated last), else by team. */
export function sortGrid(teams: GridTeam[], sort: SortKey): GridTeam[] {
  const key = (t: GridTeam): number | null => {
    if (sort === "total") return t.total;
    if (sort === "team") return null;
    return t.cells.find((c) => `w${c.week}` === sort)?.rating ?? null;
  };
  return [...teams].sort((a, b) => {
    const x = key(a);
    const y = key(b);
    if (sort !== "team" && x !== y) {
      if (x === null) return 1;
      if (y === null) return -1;
      return y - x;
    }
    return a.team.localeCompare(b.team);
  });
}

/** The position from the query string (the snapshot's codes; "D/ST" means DST). */
export function parsePlannerPosition(v: string | string[] | undefined): string {
  const s = (Array.isArray(v) ? v[0] : v)?.toUpperCase();
  const p = s === "D/ST" || s === "DEF" ? "DST" : s;
  return p && (PP_POSITIONS as readonly string[]).includes(p) ? p : "QB";
}

/** /playoff-planner with a position and a sort (defaults left out). */
export function plannerHref(q: { pos?: string; sort?: SortKey }): string {
  const sp = new URLSearchParams();
  if (q.pos && q.pos !== "QB") sp.set("pos", q.pos);
  if (q.sort && q.sort !== "team") sp.set("sort", q.sort);
  const s = sp.toString();
  return `/playoff-planner${s ? `?${s}` : ""}`;
}

/** site_meta playoff_planner_weeks ("15,16,17"); the default when it is missing or odd. */
export function parseWeeks(v: string | undefined | null): number[] {
  const w = (v ?? "").split(",").map((x) => Number(x.trim()));
  return w.length && w.every((x) => Number.isInteger(x) && x >= 1 && x <= 22) ? w : [...DEFAULT_WEEKS];
}

const ORDER = (p: string) => {
  const i = (PP_POSITIONS as readonly string[]).indexOf(p);
  return i === -1 ? PP_POSITIONS.length : i;
};

/** The horizon-pooled effect rows ('all'), one per position in the grid's order. */
export function pooledEffects(effects: PPEffect[]): PPEffect[] {
  return effects.filter((e) => e.horizon === "all" && e.position !== "all").sort((a, b) => ORDER(a.position) - ORDER(b.position));
}

/** Per position: the stability (mean Spearman) as of the earliest and the latest horizon. */
export function stabilityRows(rows: PPStability[]): { position: string; early: PPStability; late: PPStability }[] {
  const out: { position: string; early: PPStability; late: PPStability }[] = [];
  for (const p of PP_POSITIONS) {
    const mine = rows.filter((r) => r.position === p).sort((a, b) => a.horizon - b.horizon);
    if (mine.length) out.push({ position: p, early: mine[0], late: mine[mine.length - 1] });
  }
  return out;
}

export type LateEra = { era: string; rows: { position: string; before: number; last: number }[] };

/** The resting note's numbers: per era, the share of based QB / RB / WR / TE who played in the
 *  second-to-last and the last playoff week (late_weeks rows). */
export function lateEras(rows: PPLateWeek[], weeks: readonly number[] = DEFAULT_WEEKS): LateEra[] {
  const [before, last] = [weeks[weeks.length - 2], weeks[weeks.length - 1]];
  const eras = [...new Set(rows.map((r) => r.era))].sort();
  return eras.map((era) => ({
    era,
    rows: ["QB", "RB", "WR", "TE"].flatMap((position) => {
      const at = (w: number) => rows.find((r) => r.era === era && r.position === position && r.week === w)?.playedShare;
      const a = at(before);
      const b = at(last);
      return a === undefined || b === undefined || a === null || b === null ? [] : [{ position, before: a, last: b }];
    }),
  }));
}

/** The candidates of one position in the rule's order (none, raw, shrunk, adjusted). */
export function candidatesOf(rows: PPBacktestRow[], position: string): PPBacktestRow[] {
  const order = ["none", "raw", "shrunk", "adjusted"];
  return rows.filter((r) => r.position === position).sort((a, b) => order.indexOf(a.candidate) - order.indexOf(b.candidate));
}

/** Plain words for the rule's verdict on a candidate. */
export function verdict(r: PPBacktestRow, needed: number | undefined): string {
  if (r.vs === null || r.seasonsWon === null) return "the starting point: matchups ignored";
  const won = `beat ${r.vs === "none" ? "no matchup" : r.vs} in ${r.seasonsWon} of ${r.seasons} seasons`;
  if (r.tookOver) return `${won}: took over`;
  return `${won}${needed ? ` (needs ${needed} and a lower MAE)` : ""}: not used`;
}

/** The live record of one season, in the grid's order ('all' last). */
export function liveOf(rows: PPLive[], season: number | null): PPLive[] {
  return rows.filter((r) => season === null || r.season === season).sort((a, b) => ORDER(a.position) - ORDER(b.position));
}

/** Why a position shows no rating (TE and K in the 2026 pin). */
export function unratedReason(position: string, effect: PPEffect | undefined, seasons: number | null): string {
  const gap = effect?.realizedGap;
  const size = gap === null || gap === undefined ? "" : ` (the easiest fifth of matchups beat the hardest by only ${gap.toFixed(1)} points per game)`;
  return `For ${position === "DST" ? "D/ST" : position}, using matchup ratings did not beat ignoring them${seasons ? ` in ${seasons} seasons of tests` : " in the tests"}${size}: every matchup counts as 1.00, so the grid shows the schedule only.`;
}

/** The usual week-to-week miss of a player's points (the 'none' candidate's MAE: no matchup)
 *  at QB, RB and WR, lowest and highest; null before the backtest is published. */
export function missRange(rows: PPBacktestRow[]): [number, number] | null {
  const m = rows.filter((r) => r.candidate === "none" && ["QB", "RB", "WR"].includes(r.position)).map((r) => r.mae);
  return m.length ? [Math.min(...m), Math.max(...m)] : null;
}
