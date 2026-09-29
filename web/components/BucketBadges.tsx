import { badgeParts, badges, seasonsText } from "@/lib/buckets";
import { fmtInt } from "@/lib/format";
import type { BucketCounts } from "@/lib/queries/radar";
import Term from "./Term";

/** The hit-rate badges per rank bucket of a position's earlier reconstructed lists (for FLEX:
 *  of the FLEX lists rebuilt from them, lib/flex.ts flexRankCounts). */
export default function BucketBadges({
  counts,
  position,
  beforeSeason,
  flex = false,
}: {
  counts: BucketCounts;
  position: string;
  beforeSeason: number;
  flex?: boolean;
}) {
  const list = badges(counts.counts);
  const seasons = seasonsText(counts.seasonFrom, counts.seasonTo);
  const what = flex ? "FLEX lists rebuilt from the reconstructed RB, WR and TE lists" : `Reconstructed ${position} lists`;
  return (
    <section aria-labelledby="buckets-heading" className="rounded-lg border border-line bg-surface p-4" data-testid="bucket-badges">
      <h3 id="buckets-heading" className="display text-lg uppercase">
        <Term name="rank_bucket_hit_rate">How often each rank hit</Term>
      </h3>
      {seasons ? (
        <p className="mt-1 text-sm text-muted">
          {what} of {seasons} ({fmtInt(counts.lists)} weekly lists with final outcomes; seasons before {beforeSeason}{" "}
          only).
        </p>
      ) : (
        <p className="mt-1 text-sm text-muted">
          No {flex ? "FLEX list rebuilt from the reconstructed lists" : `reconstructed ${position} list`} before{" "}
          {beforeSeason} has final outcomes, so there is no earlier record to show.
        </p>
      )}
      {seasons ? (
        <ul className="mt-3 grid grid-cols-[repeat(auto-fill,minmax(11rem,1fr))] gap-2">
          {list.map((b) => {
            const p = badgeParts(b);
            return (
              <li key={b.bucket.label} data-bucket={b.bucket.label} className="rounded-md border border-line bg-raised px-3 py-2 text-sm tnum">
                <span className="font-display text-[0.8rem] font-bold tracking-wider text-muted uppercase">{p.head}</span>{" "}
                <span className="big-number text-2xl">{p.value}</span>
                {p.detail ? (
                  <>
                    {" "}
                    <span className="whitespace-nowrap text-muted">{p.detail}</span>
                  </>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}
    </section>
  );
}
