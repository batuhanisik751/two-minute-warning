import Link from "next/link";
import Term from "@/components/Term";
import { EmptyState } from "@/components/ui";
import { wpPoints } from "@/lib/decisions";
import { smoothness } from "@/lib/decisions-track";
import { END_OF_HALF_SECONDS, LATE_GAME_Q4_SECONDS, LEADERBOARD_MIN_GAMES, TOSS_UP_MARGIN } from "@/lib/method";
import { getDecisionsMeta, getDecisionsTrack } from "@/lib/queries/decisions";
import { ClockDefinitions } from "./DecisionsClock";
import { Nfl4thBenchmark, Submodels } from "./DecisionsModels";
import DecisionsWp from "./DecisionsWp";
import { HonestyNotes } from "./DecisionsHonesty";

const H3 = ({ id, children }: { id: string; children: React.ReactNode }) => (
  <h3 id={id} className="display mt-8 scroll-mt-24 text-2xl uppercase">
    {children}
  </h3>
);

/** /methodology's Decision Report Card section: the WP model (against nflfastR, its smoothing and
 *  limits), the sub-models, the grading rules, the nfl4th benchmark, the clock metrics' exact
 *  definitions and every honesty note. Numbers from decisions_track_record and site_meta; the
 *  rules' constants from lib/method.ts. */
export default async function DecisionsSection() {
  const [rows, meta] = await Promise.all([getDecisionsTrack(), getDecisionsMeta()]);
  return (
    <section aria-labelledby="decisions" data-testid="decisions-method">
      <h2 id="decisions" className="section-title scroll-mt-24">
        The Decision Report Card
      </h2>
      {!rows.length && meta.season === null ? (
        <div className="mt-3">
          <EmptyState title="No Decision Report Card published yet" />
        </div>
      ) : (
        <>
          <p className="mt-3">
            Every fourth down and try after a touchdown, priced by our own win-probability model and its sub-models, and
            three narrow clock-management checks (<Link href="/decisions">the Report Card</Link>).
            {meta.history ? ` Seasons ${meta.history.replace("-", "–")} are a frozen, approved grading;` : ""}
            {meta.season !== null ? ` ${meta.season} is regraded with the same approved models on every run.` : ""}
            {meta.version ? (
              <>
                {" "}
                Grading version <span className="font-mono text-xs">{meta.version}</span>.
              </>
            ) : null}
          </p>
          <H3 id="decisions-wp">Our win-probability model</H3>
          <DecisionsWp rows={rows} />
          <H3 id="decisions-submodels">The sub-models</H3>
          <p className="mt-3">
            Each option&apos;s win probability needs a sub-model, trained walk-forward like the WP model and backtested
            against a simple rate:
          </p>
          <Submodels rows={rows} />
          <H3 id="decisions-grading">Grading</H3>
          <ul className="mt-3 list-disc space-y-2 pl-5">
            <li>
              The recommendation is the option with the highest win probability. <Term name="wp_lost">WP lost</Term> = its
              win probability minus the chosen option&apos;s.
            </li>
            <li>
              A <Term name="clear_call">clear call</Term> when the best option beats the second best by more than{" "}
              {wpPoints(TOSS_UP_MARGIN)} WP points; otherwise a <Term name="toss_up">toss-up</Term>, counted but never
              graded. Only clear calls add to a coach&apos;s WP lost.
            </li>
            <li>
              Not graded: snaps wiped out by a penalty, kneels and spikes, fumbled snaps, and fourth downs with{" "}
              {END_OF_HALF_SECONDS} seconds or less left in the half. Fake punts and fake field goals count as going for it.
            </li>
            <li>
              Also not graded, for now: fourth downs and tries after a touchdown in the last {LATE_GAME_Q4_SECONDS / 60}{" "}
              minutes of the fourth quarter and in overtime. Our win-probability model is not yet reliable there (it is
              where the nfl4th comparison disagrees with us most), so those decisions are counted but never held against
              anyone.
            </li>
            <li>
              Each decision is credited to the head coach of the team with the ball. The leaderboard ranks coaches with at
              least {LEADERBOARD_MIN_GAMES} games in the season (early in a season: as many games as anyone has).
            </li>
            <li>
              Every graded row stores every input (the state, each model&apos;s version, the measured kickoff spot and clock
              runoffs, the margin), and the grades are recomputed from those stored inputs and checked to be identical.
            </li>
          </ul>
          <H3 id="decisions-nfl4th">Benchmark against nfl4th</H3>
          <Nfl4thBenchmark rows={rows} />
          <H3 id="decisions-clock">Clock management: the exact definitions</H3>
          <ClockDefinitions />
          <H3 id="decisions-honesty">Honesty notes</H3>
          <HonestyNotes validation={smoothness(rows).validation} cleanSeason={meta.season} />
        </>
      )}
    </section>
  );
}
