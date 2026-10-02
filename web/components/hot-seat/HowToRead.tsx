import Link from "next/link";
import Term from "@/components/Term";
import { fmtInt, pct } from "@/lib/format";
import { calibrationGrid, earlySeasonCheck, type CalCell } from "@/lib/hot-seat";
import { HOT_SEAT_DRIVERS, HOT_SEAT_WINDOW_DAYS } from "@/lib/method";
import PhaseCalibration from "./PhaseCalibration";

/** /hot-seat's "How to read this": what the number means, the early-season caveat computed from
 *  the published reconstructed rows and their outcomes, where the labels come from, and the
 *  fourth-down research result. */
export default function HowToRead({ cal }: { cal: CalCell[] }) {
  const groups = calibrationGrid(cal);
  const early = earlySeasonCheck(cal);
  return (
    <section aria-labelledby="how-to-read" className="mt-10 max-w-4xl" data-testid="how-to-read">
      <h2 id="how-to-read" className="section-title mb-3 scroll-mt-24">
        How to read this
      </h2>
      <div className="space-y-3">
        <p>
          <strong>The number</strong> is an <Term name="hot_seat_estimate">estimated chance</Term> that the head coach is{" "}
          <Term name="hot_seat_let_go">let go</Term> (fired during or after the season, or a mutual parting) and that it is
          announced by {HOT_SEAT_WINDOW_DAYS} days after his team&apos;s final game. It is a statistical estimate from how past
          seasons went, made only from what was public at the time. It does not say that anything will happen, and it knows
          nothing about what owners and general managers say in private.
        </p>
        <p>
          If the estimates are right, coaches given a certain chance are let go about that often. The{" "}
          <Term name="hot_seat_driver">{`${HOT_SEAT_DRIVERS} drivers`}</Term> under each coach are what moves his estimate
          most compared with an average coach. <Term name="is_interim">Interim coaches</Term> are shown but flagged: the model
          was not trained on them.
        </p>
      </div>
      <h3 className="display mt-6 mb-2 text-xl uppercase" id="early-season">
        Early in the season, read it loosely
      </h3>
      {early ? (
        <p className="mb-3" data-testid="early-season-caveat">
          With only a few games played, the estimates are rough. In the reconstructed past seasons, coaches given{" "}
          <strong>{early.bandName.toLowerCase()}</strong> in <strong>{early.phaseName.toLowerCase()}</strong> (an average
          estimate of {pct(early.meanPred)}) were really let go <strong>{pct(early.observed)}</strong> of the time:{" "}
          {fmtInt(early.departed)} of {fmtInt(early.n)} coach-weeks, {fmtInt(early.coachSeasons)} coach-seasons. The tables
          compare every phase of the season the same way.
        </p>
      ) : (
        <p className="mb-3 text-muted">The check needs reconstructed seasons with known outcomes; none is published yet.</p>
      )}
      {groups.length ? <PhaseCalibration groups={groups} /> : null}
      <p id="cal-note" className="mt-2 text-sm text-muted">
        Every reconstructed weekly list and end-of-season snapshot with a known outcome; interim coaches left out. A coach
        appears once per week, so one coach-season counts several times.
      </p>
      <h3 className="display mt-6 mb-2 text-xl uppercase">Where the outcomes come from</h3>
      <p>
        Who was let go, how and when was researched from cited public sources (mostly each season&apos;s Wikipedia page and
        the coaches&apos; own pages, with the link and quote for each). The project&apos;s owner accepted that research in bulk
        rather than re-checking every entry, so a mistake in it is possible.
      </p>
      <h3 className="display mt-6 mb-2 text-xl uppercase">Fourth-down decisions</h3>
      <p data-testid="research-sentence">
        We looked for a link between a coach&apos;s fourth-down decisions (the{" "}
        <Link href="/decisions">Decision Report Card</Link>) and being let go, after accounting for results against
        expectations, and found no detectable one (<Link href="/methodology#hot-seat-research">the research</Link>).
      </p>
    </section>
  );
}
