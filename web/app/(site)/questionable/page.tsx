import Link from "next/link";
import HistoryTable from "@/components/questionable/HistoryTable";
import QuestionableList from "@/components/questionable/QuestionableList";
import { BacktestScore, LiveRecord, QCalibration } from "@/components/questionable/RecordParts";
import Term from "@/components/Term";
import { NotPublished } from "@/components/track/parts";
import { EmptyState, Note, PageHeader } from "@/components/ui";
import { fmtInt, fmtUtc, seasonWeek } from "@/lib/format";
import { parseInt4 } from "@/lib/params";
import { getQuestionableIndex, getQuestionableSnapshot, getQuestionableTables, getQuestionableWeek } from "@/lib/queries/questionable";
import { INACTIVES_NOTE, WHEN_LISTS_FILL, chooseWeek, questionableHref, type QWeek } from "@/lib/questionable";
import { pageMetadata } from "@/lib/seo";

export const metadata = pageMetadata(
  "/questionable",
  "Questionable",
  "Questionable and Doubtful players this week: the chance each one plays, how he usually scores when he does, and how often tagged players have played since 2016.",
);

function Intro() {
  return (
    <PageHeader title="Questionable" kicker="Will he play?">
      Your player is listed <Term name="questionable">Questionable</Term> or <Term name="doubtful">Doubtful</Term>. Will he play, and if he plays, does he score like
      usual? The chance below is a count, not a guess: how often past players like him (same tag, same position, whether he missed his team&apos;s last game)
      took at least one snap, seasons 2016 to 2025.
    </PageHeader>
  );
}

function WeekLinks({ index, chosen }: { index: (QWeek & { snapshots: number })[]; chosen: QWeek }) {
  if (!index.length) return null;
  return (
    <nav aria-label="Weeks with a list" className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm" data-testid="q-weeks">
      <span className="text-muted">Weeks with a list:</span>
      {index.map((w) => (
        <Link
          key={`${w.season}-${w.week}`}
          href={questionableHref(w)}
          aria-current={w.season === chosen.season && w.week === chosen.week ? "page" : undefined}
          className="inline-flex min-h-11 items-center"
        >
          {seasonWeek(w.season, w.week)}
        </Link>
      ))}
    </nav>
  );
}

export default async function QuestionablePage({ searchParams }: PageProps<"/questionable">) {
  const sp = await searchParams;
  const [current, index, tables] = await Promise.all([getQuestionableWeek(), getQuestionableIndex(), getQuestionableTables()]);
  const chosen = chooseWeek({ season: parseInt4(sp.season), week: parseInt4(sp.week) }, current, index);
  const snap = chosen ? await getQuestionableSnapshot(chosen.season, chosen.week) : null;
  const isCurrent = chosen !== null && current !== null && chosen.season === current.season && chosen.week === current.week;
  return (
    <>
      <Intro />
      {chosen ? <WeekLinks index={index} chosen={chosen} /> : null}
      <section aria-labelledby="q-week-heading" className="mt-6 rounded-xl bg-surface/40 p-3 sm:p-4" data-testid="q-week">
        <h2 id="q-week-heading" className="display text-2xl uppercase sm:text-3xl">
          {chosen ? `${isCurrent ? "This week" : "The list"}, ${seasonWeek(chosen.season, chosen.week)}` : "This week"}
        </h2>
        {snap ? (
          <>
            <p className="mt-2 text-sm text-muted" data-testid="q-asof">
              The newest list: as of <time dateTime={snap.header.asOf}>{fmtUtc(snap.header.asOf)}</time> (the <Term name="as_of">as-of time</Term>), made on{" "}
              <time dateTime={snap.header.generatedAt}>{fmtUtc(snap.header.generatedAt)}</time>. {fmtInt(snap.header.nPlayers)} tagged{" "}
              {snap.header.nPlayers === 1 ? "player" : "players"} whose game had not kicked off; the table (<span className="font-mono text-xs">{snap.header.modelVersion}</span>) was frozen
              before the season.
            </p>
            <div className="mt-3 space-y-2">
              <Note tone="warn">{INACTIVES_NOTE}</Note>
              <p className="text-sm text-muted">
                <Term name="practice_status">Practice status</Term> is shown for context only: since 2025 it no longer tells players apart the way it did, so the chance does not use it.
              </p>
            </div>
            <div className="mt-5">
              <QuestionableList rows={snap.rows} label={`Questionable list, ${seasonWeek(snap.header.season, snap.header.week)}`} />
            </div>
          </>
        ) : (
          <div className="mt-3" data-testid="q-empty">
            <EmptyState title={chosen ? `No tagged player listed for ${seasonWeek(chosen.season, chosen.week)} yet` : "No week is being played right now"}>
              <p>{WHEN_LISTS_FILL}</p>
              <p className="mt-2">{INACTIVES_NOTE}</p>
            </EmptyState>
          </div>
        )}
      </section>
      <Record tables={tables} season={current?.season ?? null} />
    </>
  );
}

function Record({ tables, season }: { tables: Awaited<ReturnType<typeof getQuestionableTables>>; season: number | null }) {
  const seasons = tables.history[0]?.seasons;
  const liveSeason = season ?? tables.live[0]?.season ?? null;
  return (
    <>
      <section aria-labelledby="q-history-heading" className="mt-10" data-testid="q-history-section">
        <h2 id="q-history-heading" className="section-title mb-3 scroll-mt-24">
          How often tagged players play
        </h2>
        {tables.history.length ? (
          <>
            <p className="mb-3 max-w-3xl">
              Every QB, RB, WR and TE on a team&apos;s final injury report of a regular-season week, seasons {seasons}: how many took at least one offensive snap, by tag
              and by how much he practiced that week. Out players almost never play; Doubtful ones rarely do.
            </p>
            <HistoryTable rows={tables.history} />
          </>
        ) : (
          <NotPublished what="How often tagged players played">It appears with the first publish of the Questionable table.</NotPublished>
        )}
      </section>
      <section aria-labelledby="q-cal-heading" className="mt-10" data-testid="q-calibration-section">
        <h2 id="q-cal-heading" className="section-title mb-3 scroll-mt-24">
          Does the chance mean what it says?
        </h2>
        <div className="max-w-4xl space-y-6">
          <BacktestScore rows={tables.backtest} />
          <QCalibration rows={tables.calibration} id="q-cal" />
        </div>
      </section>
      <section aria-labelledby="q-live-heading" className="mt-10" data-testid="q-live-section">
        <h2 id="q-live-heading" className="section-title mb-3 scroll-mt-24">
          {liveSeason ? `${liveSeason} so far` : "This season so far"}
        </h2>
        <LiveRecord rows={tables.live} season={liveSeason} />
        <p className="mt-4 text-sm">
          <Link href="/methodology#questionable">How the chance is counted, and its limits</Link> ·{" "}
          <Link href="/track-record#questionable">Track record</Link>
        </p>
      </section>
    </>
  );
}
