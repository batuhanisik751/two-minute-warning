import Link from "next/link";
import Term from "@/components/Term";
import BoardTrack from "@/components/track/BoardTrack";
import DecisionsTrack from "@/components/track/DecisionsTrack";
import HotSeatTrack from "@/components/track/HotSeatTrack";
import RadarTrack from "@/components/track/RadarTrack";
import RegressionTrackSection from "@/components/track/RegressionTrackSection";
import StreamTrack from "@/components/track/StreamTrack";
import { PageHeader } from "@/components/ui";
import { pct } from "@/lib/format";
import { AS_OF_WEEKDAY, TRACK_INTERVAL_LEVEL } from "@/lib/method";
import { pageMetadata } from "@/lib/seo";

export const metadata = pageMetadata("/track-record", "Track record", "The public track record: how every module's published estimates did against what happened, with intervals and simple baselines.");

const SECTIONS = [
  { id: "radar", title: "Waiver Radar" },
  { id: "streamer", title: "K and D/ST streamer" },
  { id: "regression", title: "Regression Watch" },
  { id: "decisions", title: "Decision Report Card" },
  { id: "hot-seat", title: "Hot-Seat Meter" },
  { id: "board", title: "Cliff board" },
] as const;

function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section aria-labelledby={id} data-testid={`track-${id}`}>
      <h2 id={id} className="section-title scroll-mt-24">
        {title}
      </h2>
      {children}
    </section>
  );
}

/** /track-record: how each shipped module has done, the walk-forward backtest and the live lists
 *  kept apart. Every number is read from the published track records and live outcomes (or
 *  lib/method.ts); definitions live on /methodology. */
export default function TrackRecordPage() {
  return (
    <>
      <PageHeader title="Track record" kicker="How the tools have done">
        Every module, graded the same honest way, and nothing here is typed in by hand: every number is read from the published
        track records and the live lists&apos; outcomes. Definitions are on <Link href="/methodology">the Methodology page</Link>.
      </PageHeader>

      <div className="max-w-4xl space-y-4" data-testid="track-intro">
        <p>
          <strong>The backtest</strong> is a <Term name="walk_forward">walk-forward backtest</Term>: every past season is predicted
          by a model that learned only from the seasons before it, using only what was public each {AS_OF_WEEKDAY}. Its lists are{" "}
          <Term name="list_kind">reconstructed</Term> after the fact, so it is large but never seen live. Its{" "}
          <Term name="interval">intervals</Term> ({pct(TRACK_INTERVAL_LEVEL)}) come from redrawing whole seasons.
        </p>
        <p>
          <strong>Live results</strong> come only from lists made in real time this season and never changed afterwards. They are
          the real test, but a small one so far, and an outcome counts only once it is final: a pending one is never a miss.
        </p>
        <p>
          Where a module gives probabilities, its <Term name="calibration">calibration</Term> plot shows whether they mean what
          they say. Anything not published yet says so.
        </p>
      </div>

      <nav aria-label="On this page" className="my-8 rounded-lg border border-line bg-surface p-4 text-sm">
        <p className="mb-2 font-display text-sm font-bold tracking-wider text-muted uppercase">On this page</p>
        <ul className="grid gap-x-4 gap-y-1 sm:grid-cols-2 lg:grid-cols-4">
          {SECTIONS.map((s) => (
            <li key={s.id}>
              <a href={`#${s.id}`}>{s.title}</a>
            </li>
          ))}
        </ul>
      </nav>

      <div className="max-w-4xl space-y-12">
        <Section id="radar" title={SECTIONS[0].title}>
          <RadarTrack />
        </Section>
        <Section id="streamer" title={SECTIONS[1].title}>
          <StreamTrack />
        </Section>
        <Section id="regression" title={SECTIONS[2].title}>
          <RegressionTrackSection />
        </Section>
        <Section id="decisions" title={SECTIONS[3].title}>
          <DecisionsTrack />
        </Section>
        <Section id="hot-seat" title={SECTIONS[4].title}>
          <HotSeatTrack />
        </Section>
        <Section id="board" title={SECTIONS[5].title}>
          <BoardTrack />
        </Section>
      </div>
    </>
  );
}
