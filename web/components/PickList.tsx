import Link from "next/link";
import { outcomeOf, windowSummary } from "@/lib/format";
import type { Pick } from "@/lib/queries/radar";
import { ChanceText, OutcomeBadge, TierBadge } from "./ui";

type Props = {
  picks: Pick[];
  position: string;
  /** The list's accessible name, e.g. "RB list, 2026 week 3 (live)". */
  label: string;
  /** Show each pick's reasons (the full list page) or not (the home page's top 5). */
  reasons?: boolean;
  showOutcome?: boolean;
};

function MiniLabel({ children }: { children: React.ReactNode }) {
  return <span className="block text-[0.7rem] font-semibold uppercase tracking-wide text-muted">{children}</span>;
}

/** A ranked list of picks: an ordered list, one row per player, readable at 360 px. */
export default function PickList({ picks, position, label, reasons = true, showOutcome = true }: Props) {
  const anyReasons = picks.some((p) => p.reasons.length > 0);
  return (
    <ol aria-label={label} className="divide-y divide-line rounded-lg border border-line bg-surface" data-testid="pick-list">
      {picks.map((p) => {
        const outcome = outcomeOf(p.outcome?.yHit ?? null, p.outcome?.status ?? null);
        const detail =
          p.outcome && p.outcome.status === "final"
            ? windowSummary(p.outcome.windowWeeks, p.outcome.windowRanks, p.outcome.windowPoints, position)
            : null;
        return (
          <li
            key={p.rank}
            data-testid="pick"
            className={`grid grid-cols-[2.5rem_minmax(0,1fr)] gap-x-3 gap-y-2 px-3 py-3 ${
              showOutcome
                ? "md:grid-cols-[2.5rem_minmax(0,1fr)_9.5rem_7rem_9rem]"
                : "md:grid-cols-[2.5rem_minmax(0,1fr)_9.5rem_7rem]"
            }`}
          >
            <span className="row-span-2 pt-0.5 text-lg font-bold tnum md:row-span-1">
              <span className="sr-only">Rank </span>
              {p.rank}
            </span>
            <div className="min-w-0">
              <Link href={`/player/${p.gsisId}`} className="font-semibold">
                {p.name}
              </Link>
              <span className="block text-sm text-muted">
                {p.team} · {p.teamName}
              </span>
              {reasons && anyReasons ? (
                p.reasons.length ? (
                  <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm">
                    {p.reasons.map((r, i) => (
                      <li key={i}>{r}</li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-1 text-sm text-muted">No reasons recorded for this player.</p>
                )
              ) : null}
            </div>
            <div className="col-start-2 grid grid-cols-2 gap-3 sm:grid-cols-3 md:col-start-auto md:contents">
              <div>
                <MiniLabel>Chance</MiniLabel>
                <ChanceText chance={p.chance} low={p.chanceLow} high={p.chanceHigh} />
              </div>
              <div>
                <MiniLabel>Priority</MiniLabel>
                <TierBadge tier={p.tier} />
              </div>
              {showOutcome ? (
                <div>
                  <MiniLabel>Outcome</MiniLabel>
                  <OutcomeBadge outcome={outcome} />
                  {detail ? <span className="mt-1 block text-xs text-muted">{detail}</span> : null}
                </div>
              ) : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
