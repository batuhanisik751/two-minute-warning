// The league shape behind "a starter finish" (how many players per position count as a
// weekly starter, and for how many teams), read from the PUBLISHED glossary, never typed in
// here: the registry's starter_threshold formula says e.g. "QB top 12, RB top 24, WR top 24,
// TE top 12 by fantasy points that week; FLEX-worthy (...): RB/WR top 36", and its explanation
// names the league size ("a typical 12-team league"). The FLEX page quotes it; when the text
// cannot be read, the page says "the starter threshold of his position" instead of a number.

export type GlossaryText = { name: string; formula: string; explanation: string };

export type LeagueShape = {
  /** position -> weekly starter threshold (top N) */
  starters: Record<string, number>;
  teams: number | null;
};

const SOURCES = ["starter_threshold", "is_starter_finish"];

export function leagueShape(rows: readonly GlossaryText[]): LeagueShape | null {
  let starters: Record<string, number> = {};
  for (const name of SOURCES) {
    const row = rows.find((r) => r.name === name);
    if (!row) continue;
    // only the dedicated starters: the FLEX-worthy thresholds come after the word FLEX
    const cut = row.formula.search(/flex/i);
    const text = cut === -1 ? row.formula : row.formula.slice(0, cut);
    const found: Record<string, number> = {};
    for (const m of text.matchAll(/\b([A-Z]{1,4}(?:\/[A-Z]{1,4})*) top (\d{1,3})\b/g)) {
      for (const pos of m[1].split("/")) if (!(pos in found)) found[pos] = Number(m[2]);
    }
    if (Object.keys(found).length) {
      starters = found;
      break;
    }
  }
  if (!Object.keys(starters).length) return null;
  let teams: number | null = null;
  for (const name of ["starter_threshold", "candidate_pool", "is_starter_finish"]) {
    const m = rows.find((r) => r.name === name)?.explanation.match(/\b(\d{1,2})-team\b/);
    if (m) {
      teams = Number(m[1]);
      break;
    }
  }
  return { starters, teams };
}

/** "top-24 RB, top-24 WR or top-12 TE" for the positions the shape knows (null if none). */
export function startersPhrase(shape: LeagueShape | null, positions: readonly string[]): string | null {
  if (!shape) return null;
  const parts = positions.filter((p) => shape.starters[p] !== undefined).map((p) => `top-${shape.starters[p]} ${p}`);
  if (parts.length !== positions.length || parts.length === 0) return null;
  if (parts.length === 1) return parts[0];
  return `${parts.slice(0, -1).join(", ")} or ${parts[parts.length - 1]}`;
}
