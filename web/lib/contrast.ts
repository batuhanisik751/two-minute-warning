// WCAG 2.2 relative luminance and contrast ratio, for the theme tokens (tests/unit/contrast
// .test.ts) and the team colours drawn on the pages (lib/team-colors.ts).

/** "#1f4e79" or "#abc" -> "#1f4e79" (lowercase), anything else -> null. */
export function parseHex(v: string | null | undefined): string | null {
  if (!v) return null;
  const s = v.trim().toLowerCase();
  if (/^#[0-9a-f]{6}$/.test(s)) return s;
  if (/^#[0-9a-f]{3}$/.test(s)) return `#${s[1]}${s[1]}${s[2]}${s[2]}${s[3]}${s[3]}`;
  return null;
}

export function luminance(hex: string): number {
  const h = parseHex(hex);
  if (!h) throw new Error(`not a colour: ${hex}`);
  const c = [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
  const l = c.map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * l[0] + 0.7152 * l[1] + 0.0722 * l[2];
}

export function ratio(a: string, b: string): number {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}
