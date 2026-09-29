import Link from "next/link";
import Term from "@/components/Term";
import { PageHeader } from "@/components/ui";

export const metadata = { title: "Regression Watch" };

// A description of the coming module (PROJECT_SPEC 8.2) in plain words. Phase D is not built:
// this page shows no data and no numbers on purpose.
export default function RegressionPage() {
  return (
    <>
      <PageHeader title="Regression Watch" kicker="Coming later">
        Coming in a later phase. Nothing on this page is data yet: the module has not been built.
      </PageHeader>

      <div className="max-w-3xl space-y-6">
        <section aria-labelledby="what" className="rounded-xl border border-line bg-surface p-4 sm:p-5">
          <h2 id="what" className="section-title">
            What it will do
          </h2>
          <p className="mt-2">
            A player&apos;s fantasy points have two parts. <strong>Opportunity</strong> is what he was given: the
            targets, carries and snaps, valued at what an average player would score from them (
            <Term name="xfp">expected fantasy points</Term>). <strong>Efficiency</strong> is what he made of it: the
            points above or below that expectation (<Term name="fpoe">FPOE</Term>).
          </p>
          <p className="mt-2">
            Opportunity tends to carry over from week to week; efficiency swings a lot and drifts back toward average
            (<Term name="regression_to_the_mean">regression to the mean</Term>). Regression Watch will estimate how much
            of each player&apos;s efficiency is likely to last and tag the players whose scoring is out of line with
            their chances:
          </p>
          <ul className="mt-3 grid gap-3 sm:grid-cols-3">
            <li className="rounded-lg border border-line bg-raised p-3">
              <span className="tier" data-tier="must-add">
                Sell-high
              </span>
              <p className="mt-2 text-sm">Scoring well above what his opportunity supports, and likely to cool off.</p>
            </li>
            <li className="rounded-lg border border-line bg-raised p-3">
              <span className="tier" data-tier="speculative">
                Buy-low
              </span>
              <p className="mt-2 text-sm">
                The mirror image: scoring below what his opportunity supports, and likely to rise.
              </p>
            </li>
            <li className="rounded-lg border border-line bg-raised p-3">
              <span className="tier" data-tier="watch">
                Legit
              </span>
              <p className="mt-2 text-sm">A high scorer whose points are backed by his opportunity.</p>
            </li>
          </ul>
        </section>

        <section aria-labelledby="how" className="rounded-xl border-2 border-dashed border-line bg-surface p-4 sm:p-5">
          <h2 id="how" className="section-title">
            How it will be checked
          </h2>
          <p className="mt-2">
            Like the Waiver Radar, it will be tested the honest way: at past weeks of past seasons, using only the data
            public then, its rest-of-season projections will be compared with what really happened and with simple
            alternatives (points per game so far this season, and over his most recent games). The cutoffs for the tags will come
            from those backtests, not from intuition. The results will be published here with their counts and
            intervals, whatever they show.
          </p>
          <p className="mt-2">
            Until then, every player page (linked from the <Link href="/waivers">Waiver Radar</Link> lists) shows his
            weekly fantasy points next to his expected points.
          </p>
        </section>
      </div>
    </>
  );
}
