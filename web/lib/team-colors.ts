// Team colours on the pages: a stripe and a swatch beside each player, from dim_team.color and
// color2 (names and colours only, never logos: PROJECT_SPEC 16). A colour is used on a theme
// only when it stands out from that theme's card surface by the non-text contrast of WCAG
// 1.4.11 (3:1); else the team's second colour; else nothing (the page falls back to a neutral
// token). The team's name is always written next to it: the colour only repeats it.
import { parseHex, ratio } from "./contrast";

/** The card surface of each theme (--surface in app/globals.css; tests/unit/team-colors.test.ts
 *  checks that they agree). */
export const THEME_SURFACES = { light: "#ffffff", dark: "#0d1a2e" } as const;

export const MIN_TEAM_CONTRAST = 3;

/** The first of the team's colours that reaches 3:1 against `surface`, or null. */
export function teamColorOn(surface: string, ...colors: (string | null | undefined)[]): string | null {
  for (const c of colors) {
    const hex = parseHex(c);
    if (hex && ratio(hex, surface) >= MIN_TEAM_CONTRAST) return hex;
  }
  return null;
}

export type TeamColors = { light: string | null; dark: string | null };

export function teamColors(color: string | null | undefined, color2: string | null | undefined): TeamColors {
  return {
    light: teamColorOn(THEME_SURFACES.light, color, color2),
    dark: teamColorOn(THEME_SURFACES.dark, color, color2),
  };
}

/** The inline style that hands a row its team colours (read by .team-mark in globals.css). */
export function teamStyle(color: string | null | undefined, color2: string | null | undefined): React.CSSProperties {
  const c = teamColors(color, color2);
  return {
    ["--team-l" as string]: c.light ?? "var(--team-fallback)",
    ["--team-d" as string]: c.dark ?? "var(--team-fallback)",
  };
}
