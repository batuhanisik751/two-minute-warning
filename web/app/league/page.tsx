import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { EmptyState, Note, PageHeader } from "@/components/ui";
import { myLeagueEnabled } from "@/lib/my-league";

// My League (PROJECT_SPEC 8.3), local only: the newest `twm league report` file from
// reports/league/. 404 unless ENABLE_MY_LEAGUE === "true" AND NODE_ENV === "development".
// `next build` replaces process.env.NODE_ENV with "production", so in every build the first
// check always answers 404 and nothing below it runs: no file is read, nothing is prerendered
// (force-dynamic), and the build output holds no league data (checked by grepping .next for a
// fixture's team name, step F4). Never linked from the navigation, the sitemap or robots.txt.
// No loading.tsx above this route: notFound() must run before the response streams (real 404).
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "My League (local)",
  robots: { index: false, follow: false },
};

export default async function LeaguePage() {
  if (process.env.NODE_ENV !== "development") notFound();
  if (!myLeagueEnabled({ ENABLE_MY_LEAGUE: process.env.ENABLE_MY_LEAGUE, NODE_ENV: process.env.NODE_ENV })) notFound();
  const { readNewestReport } = await import("@/lib/my-league-fs");
  const report = await readNewestReport();
  return (
    <>
      <PageHeader title="My League" kicker="Local only">
        <p className="mt-3 max-w-2xl text-muted">
          Your newest weekly league report from <code>reports/league/</code>. This page exists only under{" "}
          <code>next dev</code> with <code>ENABLE_MY_LEAGUE=true</code>; the public site answers 404 here.
        </p>
      </PageHeader>
      {report === null ? (
        <EmptyState title="No league report yet">
          <p>
            Run <code>uv run twm league sync</code>, then <code>uv run twm league report</code>, and reload.
          </p>
        </EmptyState>
      ) : (
        <div className="space-y-3">
          <Note>
            Showing <span data-testid="league-report-name">{report.name}</span> (the newest file in reports/league/).
          </Note>
          <iframe
            title={`League report ${report.name}`}
            srcDoc={report.html}
            sandbox=""
            className="h-[80vh] w-full rounded-lg border border-line bg-surface"
            data-testid="league-report"
          />
        </div>
      )}
    </>
  );
}
