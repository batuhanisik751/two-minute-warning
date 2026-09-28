import { badgeText, badges, seasonsText } from "@/lib/buckets";
import { fmtInt } from "@/lib/format";
import type { BucketCounts } from "@/lib/queries/radar";
import Term from "./Term";

/** The hit-rate badges per rank bucket of a position's earlier reconstructed lists. */
export default function BucketBadges({
  counts,
  position,
  beforeSeason,
}: {
  counts: BucketCounts;
  position: string;
  beforeSeason: number;
}) {
  const list = badges(counts.counts);
  const seasons = seasonsText(counts.seasonFrom, counts.seasonTo);
  return (
    <section aria-labelledby="buckets-heading" className="rounded-lg border border-line bg-surface p-4" data-testid="bucket-badges">
      <h3 id="buckets-heading" className="text-sm font-semibold">
        <Term name="rank_bucket_hit_rate">How often each rank hit</Term>
      </h3>
      {seasons ? (
        <p className="mt-1 text-sm text-muted">
          Reconstructed {position} lists of {seasons} ({fmtInt(counts.lists)} weekly lists with final
          outcomes; seasons before {beforeSeason} only).
        </p>
      ) : (
        <p className="mt-1 text-sm text-muted">
          No reconstructed {position} list before {beforeSeason} has final outcomes, so there is no earlier record to
          show.
        </p>
      )}
      {seasons ? (
        <ul className="mt-3 flex flex-wrap gap-2">
          {list.map((b) => (
            <li
              key={b.bucket.label}
              data-bucket={b.bucket.label}
              className="rounded-md border border-line bg-raised px-3 py-1.5 text-sm tnum"
            >
              {badgeText(b)}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
