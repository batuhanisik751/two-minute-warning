// FantasyPros' per-player values (the experts' preseason and weekly ranks, and the reasons that
// quote them) may not be republished: their Terms of Use forbid it (owner's decision 2026-10-04,
// docs/progress.md). They stay on the private, login-protected site and are hidden whenever the
// site is public. Our own estimates and the AGGREGATE model-vs-experts comparisons stay in both.
//
// One switch, read per request (pages are force-dynamic): showThirdPartyRanks() is true unless
// SITE_PUBLIC=true (lib/seo.ts); SHOW_THIRD_PARTY_RANKS=true|false overrides it, for testing only
// (docs/deploy.md step 10: never set in production). Unit-tested in tests/unit/third-party.test.ts.
import { sitePublic } from "./seo";
import manifest from "./third-party-reasons.json";

type Env = Record<string, string | undefined>;

export function showThirdPartyRanks(env: Env = process.env): boolean {
  const o = env.SHOW_THIRD_PARTY_RANKS?.trim().toLowerCase();
  if (o === "true") return true;
  if (o === "false") return false;
  return !sitePublic(env);
}

/** Said once on every page that would otherwise show them. */
export const THIRD_PARTY_NOTE = "The experts' consensus ranks are not shown on the public site (license).";

/** A registry reason template as a pattern for the sentence it writes: the number and position
 *  placeholders become their shapes, the player's name anything; anchored at the start (a clause
 *  appended to a sentence never hides it). */
export function templatePattern(template: string): RegExp {
  const parts = template.split(/(\{[a-z_]+(?::[^}]*)?\})/);
  const body = parts
    .map((p) => {
      const m = /^\{([a-z_]+)(?::[^}]*)?\}$/.exec(p);
      if (!m) return p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      return m[1] === "value" ? "\\d+(?:\\.\\d+)?" : m[1] === "pos" ? "\\S+?" : ".+?";
    })
    .join("");
  return new RegExp(`^${body}`);
}

const PATTERNS: [string, RegExp][] = manifest.reasons.map((r) => [r.id, templatePattern(r.template)]);

/** The template id (registry feature key) of a reason built from FantasyPros' values, else null.
 *  A stored reason object is identified by its own feature key; text by its template. */
export function thirdPartyReasonId(reason: unknown): string | null {
  if (reason && typeof reason === "object") {
    const f = (reason as { feature?: unknown }).feature;
    if (typeof f === "string" && PATTERNS.some(([id]) => id === f)) return f;
    const t = (reason as { text?: unknown }).text;
    return typeof t === "string" ? thirdPartyReasonId(t) : null;
  }
  if (typeof reason !== "string") return null;
  const text = reason.trim();
  return PATTERNS.find(([, re]) => re.test(text))?.[0] ?? null;
}

/** The reasons the site may show: all of them privately, without FantasyPros' in public. */
export function shownReasons<T>(reasons: T[], show: boolean = showThirdPartyRanks()): T[] {
  return show ? reasons : reasons.filter((r) => thirdPartyReasonId(r) === null);
}
