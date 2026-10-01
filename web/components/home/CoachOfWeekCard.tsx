import Link from "next/link";
import DecisionList from "@/components/decisions/DecisionList";
import Term from "@/components/Term";
import { seasonWeek } from "@/lib/format";
import { getBestCalls, getDecisionsMeta, getWorstCalls } from "@/lib/queries/decisions";
import { getSiteMeta } from "@/lib/queries/meta";

/** The home page's "Coach of the week" card: the newest graded week's best call against
 *  convention and its worst clear call (most WP lost). Shown only while the current season has
 *  graded weeks (site_meta decisions_season = current_season, with a decisions_latest_week);
 *  otherwise, or when that week has neither call, nothing is rendered. */
export default async function CoachOfWeekCard() {
  const [meta, site] = await Promise.all([getDecisionsMeta(), getSiteMeta()]);
  const { season, latestWeek: week } = meta;
  if (season === null || week === null || season !== site.currentSeason) return null;
  const [worst, best] = await Promise.all([getWorstCalls(season, null, week, 1), getBestCalls(season, null, week, 1)]);
  if (!worst.length && !best.length) return null;
  const when = seasonWeek(season, week);
  return (
    <section aria-labelledby="coach-week-card" className="mt-6 min-w-0 rounded-xl border border-line bg-surface p-4 sm:p-5" data-testid="coach-week-card">
      <p className="kicker">Decision Report Card</p>
      <h2 id="coach-week-card" className="section-title mt-1">
        Coach of the week, {when}
      </h2>
      <p className="mt-2 text-sm text-muted">
        The week&apos;s best call <Term name="against_convention">against convention</Term> and its costliest{" "}
        <Term name="clear_call">clear call</Term> (<Term name="wp_lost">WP lost</Term>), by our win-probability model.
      </p>
      <div className="mt-4 grid gap-5 lg:grid-cols-2">
        <section aria-labelledby="week-best" className="min-w-0">
          <h3 id="week-best" className="display mb-2 text-xl uppercase">
            Best call
          </h3>
          {best.length ? (
            <DecisionList rows={best} label={`Best call against convention, ${when}`} testId="week-best" value="gain" numbered={false} />
          ) : (
            <p className="text-muted">No clear go call was taken this week.</p>
          )}
        </section>
        <section aria-labelledby="week-worst" className="min-w-0">
          <h3 id="week-worst" className="display mb-2 text-xl uppercase">
            Worst call
          </h3>
          {worst.length ? (
            <DecisionList rows={worst} label={`Worst call, ${when}`} testId="week-worst" value="lost" numbered={false} />
          ) : (
            <p className="text-muted">No clearly wrong call this week.</p>
          )}
        </section>
      </div>
      <p className="mt-3 text-sm">
        <Link href={`/decisions?season=${season}`}>The season&apos;s leaderboard, worst calls and clock cases</Link>
      </p>
    </section>
  );
}
