import Link from "next/link";
import LiveRecord from "@/components/teammate-out/LiveRecord";
import { AllocationTable, CandidateTable, CoverageTable } from "@/components/teammate-out/RecordParts";
import TeammateList from "@/components/teammate-out/TeammateList";
import Term from "@/components/Term";
import { EmptyState, Note, PageHeader } from "@/components/ui";
import { fmtInt, fmtUtc, seasonWeek } from "@/lib/format";
import { parseInt4 } from "@/lib/params";
import { getTeammateOutIndex, getTeammateOutModel, getTeammateOutSnapshot, getTeammateOutTables, getTeammateOutWeek } from "@/lib/queries/teammate-out";
import { pageMetadata } from "@/lib/seo";
import { INACTIVES_NOTE, WHEN_LISTS_FILL, chooseWeek, listMayCome, teammateOutHref, type TOWeek } from "@/lib/teammate-out";

export const metadata = pageMetadata(
  "/teammate-out",
  "Teammate out",
  "A starting RB, WR or TE is out this week: which teammates get his carries and targets, and how many PPR points that is worth, from every game since 2013 in which a starter sat.",
);

function Intro() {
  return (
    <PageHeader title="Teammate out" kicker="Who gets the work?">
      A team&apos;s <Term name="starter_out">starter is out</Term> this week. Who gets his carries and targets, and how much? The answer is counted from every regular-season
      game since 2013 in which a starting RB, WR or TE sat: how his teammates&apos; shares moved. Points come as an 80% range, because a single week is noisy.
    </PageHeader>
  );
}

function WeekLinks({ index, chosen }: { index: (TOWeek & { snapshots: number })[]; chosen: TOWeek }) {
  if (!index.length) return null;
  return (
    <nav aria-label="Weeks with a list" className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm" data-testid="to-weeks">
      <span className="text-muted">Weeks with a list:</span>
      {index.map((w) => (
        <Link
          key={`${w.season}-${w.week}`}
          href={teammateOutHref(w)}
          aria-current={w.season === chosen.season && w.week === chosen.week ? "page" : undefined}
          className="inline-flex min-h-11 items-center"
        >
          {seasonWeek(w.season, w.week)}
        </Link>
      ))}
    </nav>
  );
}

export default async function TeammateOutPage({ searchParams }: PageProps<"/teammate-out">) {
  const sp = await searchParams;
  const [current, index, tables, model] = await Promise.all([getTeammateOutWeek(), getTeammateOutIndex(), getTeammateOutTables(), getTeammateOutModel()]);
  const chosen = chooseWeek({ season: parseInt4(sp.season), week: parseInt4(sp.week) }, current, index);
  const snap = chosen ? await getTeammateOutSnapshot(chosen.season, chosen.week) : null;
  const isCurrent = chosen !== null && current !== null && chosen.season === current.season && chosen.week === current.week;
  const liveSeason = current?.season ?? tables.live[0]?.season ?? null;
  return (
    <>
      <Intro />
      {chosen ? <WeekLinks index={index} chosen={chosen} /> : null}
      <section aria-labelledby="to-week-heading" className="mt-6 rounded-xl bg-surface/40 p-3 sm:p-4" data-testid="to-week">
        <h2 id="to-week-heading" className="display text-2xl uppercase sm:text-3xl">
          {chosen ? `${isCurrent ? "This week" : "The list"}, ${seasonWeek(chosen.season, chosen.week)}` : "This week"}
        </h2>
        {snap ? (
          <>
            <p className="mt-2 text-sm text-muted" data-testid="to-asof">
              The newest list: as of <time dateTime={snap.header.asOf}>{fmtUtc(snap.header.asOf)}</time> (the <Term name="as_of">as-of time</Term>), made on{" "}
              <time dateTime={snap.header.generatedAt}>{fmtUtc(snap.header.generatedAt)}</time>. {fmtInt(snap.header.nOut)} {snap.header.nOut === 1 ? "starter" : "starters"} out
              on {fmtInt(snap.header.nTeams)} {snap.header.nTeams === 1 ? "team" : "teams"} whose game had not kicked off, {fmtInt(snap.header.nPlayers)} teammates listed;
              the table (<span className="font-mono text-xs">{snap.header.modelVersion}</span>) was frozen before the season.
            </p>
            <div className="mt-3">
              <Note tone="warn">{INACTIVES_NOTE}</Note>
            </div>
            <div className="mt-5">
              <TeammateList rows={snap.rows} label={`Teammate-out list, ${seasonWeek(snap.header.season, snap.header.week)}`} />
            </div>
          </>
        ) : (
          <div className="mt-3" data-testid="to-empty">
            {chosen && !listMayCome(chosen, current) ? (
              <EmptyState title={`No list for ${seasonWeek(chosen.season, chosen.week)}`}>
                <p>A list is made only for the week whose games are next{index.length ? "; the weeks that have one are linked above" : ""}.</p>
              </EmptyState>
            ) : (
              <EmptyState title={chosen ? `No starter listed out for ${seasonWeek(chosen.season, chosen.week)} yet` : "No week is being played right now"}>
                <p>{WHEN_LISTS_FILL}</p>
                <p className="mt-2">{INACTIVES_NOTE}</p>
              </EmptyState>
            )}
          </div>
        )}
      </section>
      <section aria-labelledby="to-alloc-heading" className="mt-10" data-testid="to-allocation-section">
        <h2 id="to-alloc-heading" className="section-title mb-3 scroll-mt-24">
          Where the work goes
        </h2>
        <p className="mb-3 max-w-3xl">
          Of the work a starter leaves (his <Term name="vacated_share">vacated share</Term>), the part each teammate <Term name="teammate_role">role</Term> took on average:
          the <Term name="allocation">allocation</Term> table the predictions use. With the WR1 out, the next receiver is the WR2.
        </p>
        <AllocationTable rows={tables.allocation} events={tables.events} />
      </section>
      <section aria-labelledby="to-quality-heading" className="mt-10" data-testid="to-quality-section">
        <h2 id="to-quality-heading" className="section-title mb-3 scroll-mt-24">
          How good is it?
        </h2>
        <div className="max-w-4xl space-y-6">
          <CandidateTable rows={tables.backtest} params={model?.params ?? null} />
          <CoverageTable rows={tables.coverage} />
        </div>
      </section>
      <section aria-labelledby="to-live-heading" className="mt-10" data-testid="to-live-section">
        <h2 id="to-live-heading" className="section-title mb-3 scroll-mt-24">
          {liveSeason ? `${liveSeason} so far` : "This season so far"}
        </h2>
        <LiveRecord rows={tables.live} season={liveSeason} />
        <p className="mt-4 text-sm">
          <Link href="/methodology#teammate-out">How the shares are counted, and their limits</Link> · <Link href="/track-record#teammate-out">Track record</Link>
        </p>
      </section>
    </>
  );
}
