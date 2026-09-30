// The file reads behind /league (lib/my-league.ts): imported by the page only after the
// development-and-flag check, so a production server never loads it.
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { newestReport } from "./my-league";

/** LEAGUE_REPORTS_DIR if set (the smoke tests), else the repo's reports/league/ (web/..). */
export function reportsDir(env: Record<string, string | undefined> = process.env): string {
  const set = env.LEAGUE_REPORTS_DIR?.trim();
  return set ? set : path.join(/* turbopackIgnore: true */ process.cwd(), "..", "reports", "league");
}

export type LeagueReport = { name: string; html: string };

/** The newest report's name and HTML, or null (no folder or no report in it). */
export async function readNewestReport(dir: string = reportsDir()): Promise<LeagueReport | null> {
  let names: string[];
  try {
    names = await readdir(dir);
  } catch {
    return null;
  }
  const name = newestReport(names);
  if (name === null) return null;
  return { name, html: await readFile(path.join(dir, name), "utf8") };
}
