// A fictional reports/league/ folder for the /league tests (never real league data): an older
// and a newer report, and a file the page must ignore. The newest one carries NEWEST_MARK and a
// fake fantasy team name, which must never appear in `next start` answers or the build output.
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export const FAKE_TEAM = "Fake Team Alpha";
export const NEWEST_MARK = "twm-fixture-league-report-newest";
export const OLDER_MARK = "twm-fixture-league-report-older";

const page = (title: string, mark: string) =>
  `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>${title}</title></head>` +
  `<body><main><h1>${title}</h1><p>${FAKE_TEAM}: your league this week.</p><p id="${mark}">${mark}</p></main></body></html>`;

/** A new temporary folder holding the fixture reports; the caller removes it. */
export function writeLeagueFixture(): string {
  const dir = mkdtempSync(join(tmpdir(), "twm-league-fixture-"));
  writeFileSync(join(dir, "2026-W02.html"), page("My League report: 2026 week 2", OLDER_MARK));
  writeFileSync(join(dir, "2026-W03.html"), page("My League report: 2026 week 3", NEWEST_MARK));
  writeFileSync(join(dir, "unmatched_players.md"), "# not a report\n");
  return dir;
}
