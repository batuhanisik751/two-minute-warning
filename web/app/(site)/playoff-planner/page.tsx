import Link from "next/link";
import Candidates from "@/components/playoff-planner/Candidates";
import GridSection from "@/components/playoff-planner/GridSection";
import LateWeeks from "@/components/playoff-planner/LateWeeks";
import LiveRecord from "@/components/playoff-planner/LiveRecord";
import Matters from "@/components/playoff-planner/Matters";
import Term from "@/components/Term";
import { PageHeader } from "@/components/ui";
import { parsePlannerPosition, parseSort } from "@/lib/playoff-planner";
import { getPlannerMeta, getPlannerModel, getPlannerSnapshot, getPlannerTables } from "@/lib/queries/playoff-planner";
import { pageMetadata } from "@/lib/seo";

export const metadata = pageMetadata(
  "/playoff-planner",
  "Playoff planner",
  "Easy or hard matchups in your fantasy playoff weeks 15, 16 and 17 for every NFL team and position, and how much a matchup really matters, from walk-forward tests over past seasons.",
);

export default async function PlayoffPlannerPage({ searchParams }: PageProps<"/playoff-planner">) {
  const sp = await searchParams;
  const [meta, snap, tables, model] = await Promise.all([getPlannerMeta(), getPlannerSnapshot(), getPlannerTables(), getPlannerModel()]);
  const weeks = meta.weeks;
  const pos = parsePlannerPosition(sp.pos);
  const sort = parseSort(sp.sort, weeks);
  const effect = tables.effects.find((e) => e.position === pos && e.horizon === "all");
  const seasons = tables.backtest[0]?.seasons ?? null;
  const season = snap?.header.season ?? meta.season ?? tables.live[0]?.season ?? null;
  const last = weeks[weeks.length - 1];
  return (
    <>
      <PageHeader title="Playoff planner" kicker="Plan for the fantasy playoffs">
        Which players have easy or hard matchups in your <Term name="fantasy_playoffs">fantasy playoffs</Term>, and how much does that matter? A little: the planner
        says how much for each position, from walk-forward tests over past seasons, and rates only the positions where ratings beat ignoring the opponent.
      </PageHeader>
      <GridSection snap={snap} weeks={weeks} pos={pos} sort={sort} effect={effect} seasons={seasons} />
      <section aria-labelledby="pp-matters-heading" className="mt-10" data-testid="pp-matters-section">
        <h2 id="pp-matters-heading" className="section-title mb-3 scroll-mt-24">
          How much do matchups matter?
        </h2>
        <div className="max-w-4xl space-y-6">
          <Matters effects={tables.effects} stability={tables.stability} choice={tables.choice} backtest={tables.backtest} />
          <LateWeeks rows={tables.lateWeeks} weeks={weeks} />
        </div>
      </section>
      <section aria-labelledby="pp-candidates-heading" className="mt-10" data-testid="pp-candidates-section">
        <h2 id="pp-candidates-heading" className="section-title mb-3 scroll-mt-24">
          Which rating, and why
        </h2>
        <p className="mb-3 max-w-3xl">
          Four ways to rate a matchup were tested on every season since the tests began: no rating at all, the raw points allowed, the same{" "}
          <Term name="pseudo_games">shrunk toward average</Term>, and shrunk with the opponents&apos; own schedules taken into account.
        </p>
        <div className="max-w-4xl">
          <Candidates rows={tables.backtest} params={model?.params ?? null} />
        </div>
      </section>
      <section aria-labelledby="pp-live-heading" className="mt-10" data-testid="pp-live-section">
        <h2 id="pp-live-heading" className="section-title mb-3 scroll-mt-24">
          {season ? `${season} so far` : "This season so far"}
        </h2>
        <LiveRecord rows={tables.live} season={season} lastWeek={last} />
        <p className="mt-4 text-sm">
          <Link href="/methodology#playoff-planner">How the ratings are made, and their limits</Link> · <Link href="/track-record#playoff-planner">Track record</Link>
        </p>
      </section>
    </>
  );
}
