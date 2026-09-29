import Link from "next/link";
import type { Pick } from "@/lib/queries/radar";
import { positionLabel } from "@/lib/positions";
import { teamStyle } from "@/lib/team-colors";
import { ChanceCell, PosBadge, TierBadge } from "./ui";

/** The home page's "ticker": each position's No. 1 of the week, side by side. Nothing moves on
 *  its own (a card lifts on hover or focus only, and not at all with reduced motion); the row
 *  wraps instead of scrolling. One card per published position, so K and D/ST join by
 *  themselves once they are published. */
export default function TopBoard({ tops }: { tops: { position: string; pick: Pick }[] }) {
  if (!tops.length) return null;
  return (
    <section aria-labelledby="top-board" className="mb-10">
      <h2 id="top-board" className="section-title">
        Top of the board
      </h2>
      <p className="mt-1 text-sm text-muted">Each position&apos;s No. 1 on this week&apos;s live lists.</p>
      <ul className="mt-3 grid grid-cols-[repeat(auto-fit,minmax(min(100%,13.5rem),1fr))] gap-3" data-testid="top-board">
        {tops.map(({ position, pick }) => (
          <li
            key={position}
            data-row="ticker"
            data-pos={position}
            data-tier={pick.tier ?? "none"}
            style={teamStyle(pick.teamColor, pick.teamColor2)}
            className="pos-edge lift team-mark flex flex-col gap-2 rounded-lg border border-line bg-surface p-3"
          >
            <div data-cell="position" className="flex flex-wrap items-center justify-between gap-2">
              <span className="flex items-center gap-2">
                <PosBadge pos={position} />
                <span className="font-display text-sm font-bold tracking-wider text-muted uppercase">
                  <span className="sr-only">{positionLabel(position)}, </span>No. 1
                </span>
              </span>
              <TierBadge tier={pick.tier} />
            </div>
            <div data-cell="player" className="min-w-0">
              <Link
                href={`/player/${pick.gsisId}`}
                className="font-display text-xl leading-tight font-bold tracking-wide text-fg uppercase underline decoration-line-strong decoration-1 underline-offset-4 [overflow-wrap:anywhere] hover:decoration-hot hover:decoration-2"
              >
                {pick.name}
              </Link>
              <span className="mt-0.5 flex items-center gap-1.5 text-sm text-muted">
                <span className="team-swatch" aria-hidden="true" />
                <span className="min-w-0 [overflow-wrap:anywhere]">
                  {pick.team} · {pick.teamName}
                </span>
              </span>
            </div>
            <div data-cell="chance" className="mt-auto">
              <ChanceCell chance={pick.chance} low={pick.chanceLow} high={pick.chanceHigh} />
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
