import Term from "@/components/Term";
import { CLOCK } from "@/lib/method";

const minutes = (s: number) => (s % 60 === 0 ? `${s / 60} minutes` : `${s} seconds`);

/** The three clock-management metrics' exact definitions in plain words (docs/decision_metrics.md
 *  "Clock management"); the thresholds are lib/method.ts's, checked against config/settings.yaml. */
export function ClockDefinitions() {
  const window = minutes(CLOCK.finalWindowSeconds);
  return (
    <div className="mt-3 space-y-3" data-testid="clock-definitions">
      <p>
        Three limited metrics, each written down with every threshold <em>before</em> any season was graded. A situation
        outside these definitions is never graded. &ldquo;Could kneel out the clock&rdquo; is measured arithmetic: from the
        down and the clock, how long the offense&apos;s remaining kneels take (the seconds per kneel and between kneels are
        measured on the seasons before the graded one), with or without the defense&apos;s timeouts.
      </p>
      <dl className="divide-y divide-line rounded-lg border border-line bg-surface">
        <div className="px-4 py-3">
          <dt className="font-semibold">
            <Term name="timeouts_unused">Timeouts unused in a lost one-score game</Term>
          </dt>
          <dd className="mt-1 text-sm">
            The team lost in regulation by 1 to {CLOCK.oneScoreMargin} points; the opponent had the ball for the game&apos;s
            last snap, and on that drive, in the last {window} of the fourth quarter, reached a snap where it could kneel out
            the clock unless the team used its timeouts; and the team still held at least one timeout at the opponent&apos;s
            last snap. The value is the timeouts left. A fact, not always a mistake: a timeout kept is worthless when the
            opponent converted a first down after the others were used.
          </dd>
        </div>
        <div className="px-4 py-3">
          <dt className="font-semibold">
            <Term name="half_passivity">End-of-half passivity</Term>
          </dt>
          <dd className="mt-1 text-sm">
            First half only (the end of a game is a win-probability question). The team had the ball at the half&apos;s last
            snap, its drive ended the half without a score, turnover or punt, and its last snaps were kneels and designed
            runs with no timeout called; the first of them a 1st down with at least {CLOCK.passivityMinSeconds} seconds and
            at least {CLOCK.passivityMinTimeouts} timeout left. It is a case when the <Term name="passivity_ep_left">expected
            points of attacking</Term> from that spot and clock (net of the other team&apos;s, measured on seasons before any
            graded one) were at least {CLOCK.passivityMinEp.toFixed(1)} point; kneeling scores nothing. The table averages
            every team that had the ball there, passive ones included, so it understates attacking.
          </dd>
        </div>
        <div className="px-4 py-3">
          <dt className="font-semibold">
            <Term name="timeout_seconds_wasted">Seconds wasted with timeouts in hand</Term>
          </dt>
          <dd className="mt-1 text-sm">
            In the last {window} of the fourth quarter, trailing by 1 to {CLOCK.oneScoreMargin} with timeouts: once the
            opponent can kneel out the clock unless the team stops it, the team should call a timeout right after every
            opponent play that leaves the clock running. A missed stop is such a play after which no timeout came and at
            least {CLOCK.clockRanMinSeconds} seconds ran. The seconds wasted on a drive are the runoffs of its first missed
            stops, as many as the timeouts the team never used against that drive (a timeout still held when the drive is
            over can no longer save its seconds). How the drive ended is context, not a condition.
          </dd>
        </div>
      </dl>
    </div>
  );
}
