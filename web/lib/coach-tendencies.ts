// Coach tendencies' pure helpers (feature #10, docs/coach_tendencies.md; unit-tested in
// tests/unit/coach-tendencies.test.ts): how the published values read, the seasons table, the
// league table's sort and its "current head coaches", and the two sentences the pages state from
// the published tables (the persistence note, the fantasy link). No number here is measured:
// every one comes from coach_tendency_* (src/twm/publish/coach_tendencies.py).
import { fmtNum, signedNum } from "@/lib/format";

export const TENDENCY_METRICS = [
  "neutral_pass_rate",
  "early_down_pass_rate",
  "proe",
  "neutral_sec_per_play",
  "no_huddle_rate",
  "shotgun_rate",
  "fourth_go_rate",
  "fourth_short_go_rate",
] as const;
export type TendencyMetric = (typeof TENDENCY_METRICS)[number];
export const PACE: TendencyMetric = "neutral_sec_per_play";

/** Column titles (short) and names in sentences (long); the glossary term is the metric itself. */
export const METRIC_TEXT: Record<TendencyMetric, { short: string; long: string }> = {
  neutral_pass_rate: { short: "Pass rate", long: "neutral pass rate" },
  early_down_pass_rate: { short: "Early-down pass", long: "early-down pass rate" },
  proe: { short: "PROE", long: "pass rate over expected" },
  neutral_sec_per_play: { short: "Pace", long: "pace" },
  no_huddle_rate: { short: "No-huddle", long: "no-huddle rate" },
  shotgun_rate: { short: "Shotgun", long: "shotgun rate" },
  fourth_go_rate: { short: "4th-down go", long: "fourth-down go rate" },
  fourth_short_go_rate: { short: "4th-and-short go", long: "fourth-and-short go rate" },
};

export function isTendencyMetric(x: string): x is TendencyMetric {
  return (TENDENCY_METRICS as readonly string[]).includes(x);
}

/** A value as the pages show it: rates "62%", PROE "+4.6 pts", pace "27.0 s". */
export function fmtTendency(metric: string, value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "–";
  if (metric === "proe") return `${signedNum(value)} pts`;
  if (metric === PACE) return `${value.toFixed(1)} s`;
  return `${Math.round(value * 100)}%`;
}

/** A difference with the league: rates in percentage points, PROE in points, pace in seconds
 *  ("1.2 s faster"). */
export function fmtVsLeague(metric: string, diff: number | null): string {
  if (diff === null || !Number.isFinite(diff)) return "–";
  if (metric === PACE) {
    const s = Math.abs(diff).toFixed(1);
    return Number(s) === 0 ? "same pace" : `${s} s ${diff < 0 ? "faster" : "slower"}`;
  }
  return `${signedNum(metric === "proe" ? diff : diff * 100)} pts`;
}

/** "1st", "2nd", "23rd", "11th". */
export function ordinal(n: number): string {
  const t = n % 100;
  if (t >= 11 && t <= 13) return `${n}th`;
  return `${n}${["th", "st", "nd", "rd"][n % 10] ?? "th"}`;
}

/** Where the offense ranks that season: "71st percentile"; pace: "faster than 90%" (its
 *  published percentile counts slower offenses, so the page shows 100 - percentile). NULL: not
 *  ranked (too few snaps or fourth-down choices). */
export function percentileText(metric: string, percentile: number | null): string {
  if (percentile === null || !Number.isFinite(percentile)) return "not ranked";
  if (metric === PACE) return `faster than ${Math.round(100 - percentile)}%`;
  return `${ordinal(Math.round(percentile))} percentile`;
}

/** One published coach_tendency_season row (the fields the pages use). */
export type TendencyRow = {
  coachId: string;
  team: string;
  season: number;
  metric: string;
  isCurrent: boolean;
  throughWeek: number;
  games: number;
  plays: number;
  value: number;
  sample: number;
  leagueAvg: number;
  percentile: number | null;
};

/** One coach-team-season with its metrics: what a table row shows. */
export type TendencySeason = {
  coachId: string;
  team: string;
  season: number;
  isCurrent: boolean;
  throughWeek: number;
  games: number;
  plays: number;
  cells: Partial<Record<TendencyMetric, TendencyRow>>;
};

/** The long rows grouped by (coach, team, season), newest season first (teams by name). */
export function groupSeasons(rows: readonly TendencyRow[]): TendencySeason[] {
  const out = new Map<string, TendencySeason>();
  for (const r of rows) {
    const k = `${r.coachId}|${r.team}|${r.season}`;
    let s = out.get(k);
    if (!s) {
      s = { coachId: r.coachId, team: r.team, season: r.season, isCurrent: r.isCurrent, throughWeek: r.throughWeek, games: r.games, plays: r.plays, cells: {} };
      out.set(k, s);
    }
    if (isTendencyMetric(r.metric)) s.cells[r.metric] = r;
  }
  return [...out.values()].sort((a, b) => b.season - a.season || b.throughWeek - a.throughWeek || a.team.localeCompare(b.team));
}

/** The current head coaches of a season: per team, the coach of its latest game (the row with the
 *  most recent week played; a coach replaced mid-season keeps his own row elsewhere). */
export function currentCoaches(seasons: readonly TendencySeason[]): TendencySeason[] {
  const best = new Map<string, TendencySeason>();
  for (const s of seasons) {
    const b = best.get(s.team);
    if (!b || s.throughWeek > b.throughWeek) best.set(s.team, s);
  }
  return [...best.values()];
}

export type TendencySort = TendencyMetric | "team";

/** The league table's sort from the query string (default PROE): a metric or "team". */
export function parseTendencySort(v: string | string[] | undefined): TendencySort {
  const s = Array.isArray(v) ? v[0] : v;
  return s === "team" || (s !== undefined && isTendencyMetric(s)) ? (s as TendencySort) : "proe";
}

/** Most first (more passing, more aggressive); pace fastest first; "team" by team code. A row
 *  without the metric goes last. */
export function sortSeasons(rows: readonly TendencySeason[], sort: TendencySort): TendencySeason[] {
  return [...rows].sort((a, b) => {
    if (sort === "team") return a.team.localeCompare(b.team);
    const x = a.cells[sort]?.value ?? null;
    const y = b.cells[sort]?.value ?? null;
    if (x === null || y === null) return x === y ? a.team.localeCompare(b.team) : x === null ? 1 : -1;
    const d = sort === PACE ? x - y : y - x;
    return d !== 0 ? d : a.team.localeCompare(b.team);
  });
}

export type PersistenceRow = { metric: string; comparison: string; nPairs: number; nSeasons: number; firstSeason: number | null; lastSeason: number | null; r: number | null; ciLow: number | null; ciHigh: number | null };
export type LinkRow = { metric: string; target: string; horizon: string; n: number; nSeasons: number; r: number | null; ciLow: number | null; ciHigh: number | null; xSd: number | null; yPerXSd: number | null };

export const COMPARISONS = ["same_coach_same_team", "same_coach_new_team", "new_coach_same_team"] as const;
export const COMPARISON_TEXT: Record<string, string> = {
  same_coach_same_team: "Same coach, same team",
  same_coach_new_team: "Same coach, new team",
  new_coach_same_team: "New coach, same team",
};
/** The page calls a tendency "carried over" when its year-to-year r is at least this (a reading
 *  rule for the words, not a measured number). */
export const CARRY_R = 0.4;

/** A correlation: 0.4712 -> "0.47", -0.2 -> "−0.20" (the site's minus sign), -0.001 -> "0.00". */
export const fmtR = (r: number): string => fmtNum(r, 2);
const span = (xs: number[]) => (xs.length ? `${fmtR(Math.min(...xs))} to ${fmtR(Math.max(...xs))}` : "–");
const words = (xs: string[]) => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`);

/** The persistence note, from the published coach_tendency_persistence rows: whether a style
 *  persists when coach and team stay, which tendencies follow the coach to a new team, what a team
 *  keeps after a new coach, and how many moves it rests on. NULL when nothing is published. */
export function persistenceNote(rows: readonly PersistenceRow[]): { stays: string; moves: string; team: string; sample: string } | null {
  const of = (c: string) => TENDENCY_METRICS.map((m) => ({ m, row: rows.find((x) => x.metric === m && x.comparison === c) })).filter((x) => x.row?.r != null) as { m: TendencyMetric; row: PersistenceRow & { r: number } }[];
  const same = of("same_coach_same_team");
  const moved = of("same_coach_new_team");
  const newCoach = of("new_coach_same_team");
  if (!same.length) return null;
  const strong = same.filter((x) => x.row.r >= CARRY_R);
  const stays =
    `Style persists when the coach and the team both stay: season to season, r ${span(same.map((x) => x.row.r))} across the ${same.length} tendencies` +
    (strong.length === same.length ? "." : `; ${strong.length} of ${same.length} reach ${fmtR(CARRY_R)}.`);
  const newR = new Map(newCoach.map((x) => [x.m, x.row.r]));
  // high enough to read as carried over; it does only when its published 95% interval clears 0 (G4.12)
  const high = moved.filter((x) => x.row.r >= CARRY_R && x.row.r > (newR.get(x.m) ?? -1));
  const carry = high.filter((x) => x.row.ciLow === null || x.row.ciLow > 0);
  const unsure = high.filter((x) => !carry.includes(x));
  const rest = moved.filter((x) => !high.includes(x));
  const one = (x: (typeof moved)[number]) =>
    `${METRIC_TEXT[x.m].long} (r ${fmtR(x.row.r)}${x.row.ciLow !== null && x.row.ciHigh !== null ? `, 95% interval ${fmtR(x.row.ciLow)} to ${fmtR(x.row.ciHigh)}` : ""})`;
  const unsureText = unsure.length ? `${words(unsure.map(one))} ${unsure.length === 1 ? "looks high, but its interval includes" : "look high, but their intervals include"} 0: uncertain` : "";
  const tail = [unsureText, rest.length ? `the others keep r ${span(rest.map((x) => x.row.r))}` : ""].filter(Boolean);
  const moves = !moved.length
    ? "No coach has moved to a new team in the published seasons."
    : carry.length
      ? `At a new team, only ${words(carry.map(one))} ${carry.length === 1 ? "carries" : "carry"} over${tail.map((t) => `; ${t}`).join("")}.`
      : unsure.length
        ? `At a new team, no tendency clearly carries over: ${tail.join("; ")}.`
        : `At a new team, no tendency carries over: r ${span(moved.map((x) => x.row.r))}.`;
  const team = newCoach.length ? `A team that changes coach keeps little of its style: r ${span(newCoach.map((x) => x.row.r))}.` : "";
  const pairs = moved.map((x) => x.row.nPairs);
  const sample = pairs.length ? `Coaches who moved: ${Math.min(...pairs) === Math.max(...pairs) ? Math.min(...pairs) : `${Math.min(...pairs)}–${Math.max(...pairs)}`} pairs of seasons, so those intervals are wide.` : "";
  return { stays, moves, team, sample };
}

export const TARGET_TEXT: Record<string, string> = { targets_per_game: "targets per game", recv_ppr_per_game: "receiving PPR points per game" };
export const HORIZON_TEXT: Record<string, string> = { same_season: "Same season", next_season: "Next season" };


/** The fantasy link stated plainly, from the published coach_tendency_fantasy_link rows: PROE
 *  against the team's pass-catchers' targets per game, the same season and the next. NULL when
 *  the PROE rows are missing. */
export function fantasyLinkText(rows: readonly LinkRow[]): { same: string; next: string } | null {
  const get = (target: string, horizon: string) => rows.find((r) => r.metric === "proe" && r.target === target && r.horizon === horizon);
  const same = get("targets_per_game", "same_season");
  const next = get("targets_per_game", "next_season");
  if (!same || same.r === null || same.xSd === null || same.yPerXSd === null) return null;
  const ppr = get("recv_ppr_per_game", "same_season");
  const ci = (r: LinkRow) => (r.ciLow !== null && r.ciHigh !== null ? ` (95% interval ${fmtR(r.ciLow)} to ${fmtR(r.ciHigh)})` : "");
  const sameText =
    `The same season, a team's pass rate over expected and its pass-catchers' targets per game go together: r = ${fmtR(same.r)}${ci(same)} over ${same.n} team-seasons. ` +
    `One standard deviation of PROE (${same.xSd.toFixed(1)} points) comes with ${signedNum(same.yPerXSd)} targets per game` +
    (ppr?.yPerXSd != null ? ` and ${signedNum(ppr.yPerXSd)} receiving PPR points per game` : "") +
    ". Partly mechanical: more passes are more targets.";
  const nextText =
    next && next.r !== null && next.yPerXSd !== null
      ? `The next season (the same team, whoever coaches) the link is weaker: r = ${fmtR(next.r)}${ci(next)}, about ${signedNum(next.yPerXSd)} targets per game per standard deviation.`
      : "No next-season link is published.";
  return { same: sameText, next: nextText };
}
