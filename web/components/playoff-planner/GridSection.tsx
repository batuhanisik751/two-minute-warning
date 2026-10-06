import Grid from "@/components/playoff-planner/Grid";
import Term from "@/components/Term";
import { EmptyState, Note } from "@/components/ui";
import { fmtUtc } from "@/lib/format";
import { BAND, LEAGUES_DIFFER, unratedReason, type PPEffect, type PPRow, type PPSnapshot, type SortKey } from "@/lib/playoff-planner";

/** Section (a): the newest snapshot's grid of the chosen position, or the empty state before the
 *  first completed week of the season. */
export default function GridSection(props: { snap: { header: PPSnapshot; rows: PPRow[] } | null; weeks: number[]; pos: string; sort: SortKey; effect: PPEffect | undefined; seasons: number | null }) {
  const { snap, weeks, pos, sort } = props;
  const span = `${weeks[0]}-${weeks[weeks.length - 1]}`;
  return (
    <section aria-labelledby="pp-grid-heading" className="mt-6 rounded-xl bg-surface/40 p-3 sm:p-4" data-testid="pp-grid-section">
      <h2 id="pp-grid-heading" className="display text-2xl uppercase sm:text-3xl">
        {snap ? `${snap.header.season} playoff weeks ${span}` : `Playoff weeks ${span}`}
      </h2>
      <div className="mt-3">
        <Note>{LEAGUES_DIFFER}</Note>
      </div>
      {snap ? (
        <>
          <p className="mt-3 text-sm text-muted" data-testid="pp-asof">
            Ratings from the games through week {snap.header.throughWeek} (as of <time dateTime={snap.header.asOf}>{fmtUtc(snap.header.asOf)}</time>), with the rule
            frozen before the season (<span className="font-mono text-xs">{snap.header.modelVersion}</span>). A <Term name="matchup_rating">matchup rating</Term> of {(1 + BAND).toFixed(2)}
            means players at the position scored {Math.round(BAND * 100)}% more than average against that opponent: easy from there up, hard at {(1 - BAND).toFixed(2)} or
            less; rank 1 is the easiest of the opponents. A bye week counts nothing toward the total.
          </p>
          <div className="mt-4">
            <Grid rows={snap.rows} weeks={weeks} pos={pos} sort={sort} unrated={unratedReason(pos, props.effect, props.seasons)} />
          </div>
        </>
      ) : (
        <div className="mt-3" data-testid="pp-empty">
          <EmptyState title="No playoff grid yet">
            <p>
              The grid appears once the season&apos;s first week is complete: every team&apos;s opponents in weeks {span}, rated with the games played so far. It updates
              nightly as each week completes.
            </p>
          </EmptyState>
        </div>
      )}
    </section>
  );
}
