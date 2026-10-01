import Link from "next/link";
import Term from "@/components/Term";
import { TOSS_UP_MARGIN } from "@/lib/method";
import { wpPoints } from "@/lib/decisions";

/** The short "how it works" box of /decisions (the full story: /methodology#decisions). The
 *  margin is lib/method.ts's (checked against config/settings.yaml); no other number here. */
export default function HowItWorks() {
  return (
    <section aria-labelledby="how-heading" className="rounded-xl border border-line bg-surface p-4 sm:p-5" data-testid="how-it-works">
      <h2 id="how-heading" className="section-title">
        How it works
      </h2>
      <ul className="mt-3 list-disc space-y-2 pl-5">
        <li>
          <strong>Our own win-probability model.</strong> Every play&apos;s chance to win comes from{" "}
          <Term name="own_wp">our model</Term>, trained only on seasons before the one it prices (a season is never
          graded by a model that saw it).
        </li>
        <li>
          <strong>Every option priced.</strong> On fourth down: going for it (the{" "}
          <Term name="p_convert">chance to convert</Term> and where the ball ends up either way), a field goal (the{" "}
          <Term name="p_fg_make">chance it is good</Term>) and a punt (where the other team starts), each turned into a win
          probability. After a touchdown: the extra point against going for two. Each of these sub-models has its own
          backtest.
        </li>
        <li>
          <strong>Clear calls only.</strong> A decision is graded when the best option beats the second best by more
          than <span className="tnum">{wpPoints(TOSS_UP_MARGIN)}</span> <Term name="wp_points">WP points</Term> (a{" "}
          <Term name="clear_call">clear call</Term>); closer ones are <Term name="toss_up">toss-ups</Term>, counted but
          never graded. <Term name="wp_lost">WP lost</Term> = the best option&apos;s win probability minus the chosen
          one&apos;s.
        </li>
        <li>
          <strong>Stored inputs.</strong> Every graded decision keeps every input it was priced from (the state, the
          models&apos; versions, the measured kickoff spot and clock runoffs), so each grade can be recomputed exactly.
        </li>
      </ul>
      <p className="mt-3 text-sm">
        <Link href="/methodology#decisions">The models, their backtests, the benchmark against nfl4th and every honesty note</Link>
      </p>
    </section>
  );
}
