import Link from "next/link";
import { seasonWeek } from "@/lib/format";
import { getPlayerTeammateOut } from "@/lib/queries/teammate-out";
import { gainText, pointsWithRange, reasonLabel, teammateOutHref } from "@/lib/teammate-out";

/** On a player page: "Teammate out (X): predicted 11.0 (7.0–17.0) PPR points as the RB2; his bigger
 *  share is worth +5.0" when he is a
 *  predicted gainer on the newest Teammate-out list of the week whose games are next, or "Starter
 *  out" when he is one of its absent starters (links to that list); nothing otherwise. */
export default async function PlayerBadge({ gsisId }: { gsisId: string }) {
  const t = await getPlayerTeammateOut(gsisId);
  if (!t) return null;
  const text =
    t.kind === "gainer"
      ? `Teammate out (${t.outNames}): predicted ${pointsWithRange(t)} PPR points as the ${t.role}; his bigger share is worth ${gainText(t.predGain)}`
      : `Starter out: ${reasonLabel(t.reason)}; see who gets his work`;
  return (
    <p className="mb-6" data-testid="to-badge" data-kind={t.kind}>
      <Link
        href={teammateOutHref({ season: t.season, week: t.week })}
        className="inline-flex min-h-11 items-center gap-2 rounded-md border border-line-strong bg-surface px-3 py-1.5 font-semibold no-underline"
      >
        <span>{text}</span>
        <span className="text-sm font-normal text-muted">({seasonWeek(t.season, t.week)} list)</span>
      </Link>
    </p>
  );
}
