import Link from "next/link";
import { seasonWeek } from "@/lib/format";
import { getPlayerQuestionable } from "@/lib/queries/questionable";
import { chancePct, questionableHref } from "@/lib/questionable";

/** On a player page: "Questionable: plays 64% of the time" when he is on the newest
 *  Questionable list of the week whose games are next (links to that list); nothing otherwise. */
export default async function PlayerBadge({ gsisId }: { gsisId: string }) {
  const q = await getPlayerQuestionable(gsisId);
  if (!q) return null;
  return (
    <p className="mb-6" data-testid="q-badge">
      <Link
        href={questionableHref({ season: q.season, week: q.week })}
        className="inline-flex min-h-11 items-center gap-2 rounded-md border border-line-strong bg-surface px-3 py-1.5 font-semibold no-underline"
      >
        <span>
          {q.reportStatus}: plays {chancePct(q.playChance)} of the time
        </span>
        <span className="text-sm font-normal text-muted">({seasonWeek(q.season, q.week)} list)</span>
      </Link>
    </p>
  );
}
