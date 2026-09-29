// Which positions the site shows, in which order. The tabs of /waivers and the cards of the
// home page come from the DATA (the positions present in radar_list), plus FLEX after TE when
// any FLEX position is present. A position a later step publishes (K, D/ST) appears on its own,
// in the order below; one this file has never heard of goes last, alphabetically.

export const FLEX = "FLEX";

/** A FLEX slot takes a running back, a wide receiver or a tight end. */
export const FLEX_POSITIONS = ["RB", "WR", "TE"] as const;

/** Display order. D/ST is listed under the codes a publisher might use for it. */
export const DISPLAY_ORDER = ["QB", "RB", "WR", "TE", FLEX, "K", "DST", "D/ST", "DEF"] as const;

export const POSITION_LABELS: Record<string, string> = {
  QB: "Quarterbacks",
  RB: "Running backs",
  WR: "Wide receivers",
  TE: "Tight ends",
  FLEX: "FLEX (RB, WR, TE)",
  K: "Kickers",
  DST: "Defense and special teams",
  "D/ST": "Defense and special teams",
  DEF: "Defense and special teams",
};

export function positionLabel(p: string): string {
  return POSITION_LABELS[p] ?? p;
}

export function displayOrder(p: string): number {
  const i = (DISPLAY_ORDER as readonly string[]).indexOf(p);
  return i === -1 ? DISPLAY_ORDER.length : i;
}

export function sortPositions(ps: Iterable<string>): string[] {
  return [...new Set(ps)].sort((a, b) => displayOrder(a) - displayOrder(b) || (a < b ? -1 : a > b ? 1 : 0));
}

export function isFlexPosition(p: string): boolean {
  return (FLEX_POSITIONS as readonly string[]).includes(p);
}

/** The tabs: every published position plus FLEX (after TE) when a FLEX position is published. */
export function tabPositions(present: Iterable<string>): string[] {
  const ps = sortPositions([...present].filter((p) => p !== FLEX));
  return ps.some(isFlexPosition) ? sortPositions([...ps, FLEX]) : ps;
}
