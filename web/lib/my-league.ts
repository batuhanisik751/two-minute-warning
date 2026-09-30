// My League (PROJECT_SPEC 8.3): the local-only /league page shows the newest weekly report that
// `uv run twm league report` wrote to reports/league/<season>-W<nn>.html (git-ignored). It is on
// only when ENABLE_MY_LEAGUE is "true" AND the app runs under `next dev`; everywhere else (every
// `next build` / `next start`, Vercel) the page answers 404 without reading anything. No league
// data is ever in the build: the report is read per request, in development only.

/** reports/league/ file names the page may show: <season>-W<two-digit week>.html. */
export const REPORT_NAME = /^(\d{4})-W(\d{2})\.html$/;

type Env = { ENABLE_MY_LEAGUE?: string; NODE_ENV?: string };

/** True only when both hold: the flag is exactly "true" and NODE_ENV is "development". */
export function myLeagueEnabled(env: Env): boolean {
  return env.ENABLE_MY_LEAGUE === "true" && env.NODE_ENV === "development";
}

/** The newest report among `names` (by season, then week; other files are ignored), or null. */
export function newestReport(names: readonly string[]): string | null {
  let best: { name: string; key: number } | null = null;
  for (const name of names) {
    const m = REPORT_NAME.exec(name);
    if (!m) continue;
    const key = Number(m[1]) * 100 + Number(m[2]);
    if (best === null || key > best.key) best = { name, key };
  }
  return best?.name ?? null;
}
