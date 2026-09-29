import Link from "next/link";
import { outcomeOf, windowSummary } from "@/lib/format";
import type { Pick } from "@/lib/queries/radar";
import { teamStyle } from "@/lib/team-colors";
import { ChanceCell, MiniLabel, OutcomeBadge, PosBadge, TierBadge } from "./ui";

type Props = {
  picks: (Pick & { flexRank?: number })[];
  /** The list's accessible name, e.g. "RB list, 2026 week 3 (live)". */
  label: string;
  /** Show each pick's reasons (the full list page) or not (the home page's top 5). */
  reasons?: boolean;
  showOutcome?: boolean;
  /** A FLEX list: rank = the FLEX rank, and each row shows his position and his rank there. */
  flex?: boolean;
};

// The rows lay out by the width of THEIR CONTAINER (Tailwind container queries), not the
// window's, so a list in a half-width card never gets the full-width row:
//   narrow (< 28rem)   rank | player            medium (>= 28rem)   rank | player            | chance
//                      rank | chance                                rank | priority, outcome | chance
//                      rank | priority, outcome
//   wide (>= 48rem)    rank | player | chance | priority | outcome   (with a column header row)
// Every cell is a grid area with a minimum width, and names wrap: nothing can overlap
// (tests/smoke/layout.test.ts measures it in Chrome at widths from 320 to 1920 px).
const ROW_OUTCOME =
  "grid grid-cols-[2.75rem_minmax(0,1fr)] gap-x-3 gap-y-2 [grid-template-areas:'rank_player'_'rank_chance'_'rank_meta'] @md:grid-cols-[3rem_minmax(0,1fr)_minmax(7.5rem,9.5rem)] @md:[grid-template-areas:'rank_player_chance'_'rank_meta_chance'] @3xl:grid-cols-[3.25rem_minmax(0,1fr)_9.5rem_8rem_minmax(9rem,12rem)] @3xl:[grid-template-areas:'rank_player_chance_tier_outcome']";
const ROW_PLAIN =
  "grid grid-cols-[2.75rem_minmax(0,1fr)] gap-x-3 gap-y-2 [grid-template-areas:'rank_player'_'rank_chance'_'rank_meta'] @md:grid-cols-[3rem_minmax(0,1fr)_minmax(7.5rem,9.5rem)] @md:[grid-template-areas:'rank_player_chance'_'rank_meta_chance'] @3xl:grid-cols-[3.25rem_minmax(0,1fr)_9.5rem_8rem] @3xl:[grid-template-areas:'rank_player_chance_tier']";
const HEAD_OUTCOME =
  "hidden gap-x-3 @3xl:grid @3xl:grid-cols-[3.25rem_minmax(0,1fr)_9.5rem_8rem_minmax(9rem,12rem)] @3xl:[grid-template-areas:'rank_player_chance_tier_outcome']";
const HEAD_PLAIN =
  "hidden gap-x-3 @3xl:grid @3xl:grid-cols-[3.25rem_minmax(0,1fr)_9.5rem_8rem] @3xl:[grid-template-areas:'rank_player_chance_tier']";

/** A ranked list of picks: an ordered list, one row per player, readable from 320 px. */
export default function PickList({ picks, label, reasons = true, showOutcome = true, flex = false }: Props) {
  const anyReasons = picks.some((p) => p.reasons.length > 0);
  return (
    <div className="@container">
      <div
        aria-hidden="true"
        data-row="header"
        className={`${showOutcome ? HEAD_OUTCOME : HEAD_PLAIN} px-3 pb-1.5 pl-4 font-display text-[0.8rem] font-bold tracking-wider text-muted uppercase`}
      >
        <span data-cell="rank" className="[grid-area:rank]">
          Rank
        </span>
        <span data-cell="player" className="[grid-area:player]">
          Player
        </span>
        <span data-cell="chance" className="[grid-area:chance]">
          Chance
        </span>
        <span data-cell="priority" className="[grid-area:tier]">
          Priority
        </span>
        {showOutcome ? (
          <span data-cell="outcome" className="[grid-area:outcome]">
            Outcome
          </span>
        ) : null}
      </div>
      <ol aria-label={label} className="divide-y divide-line overflow-hidden rounded-lg border border-line bg-surface" data-testid="pick-list">
        {picks.map((p) => {
          const outcome = outcomeOf(p.outcome?.yHit ?? null, p.outcome?.status ?? null);
          const detail =
            p.outcome && p.outcome.status === "final"
              ? windowSummary(p.outcome.windowWeeks, p.outcome.windowRanks, p.outcome.windowPoints, p.position)
              : null;
          const rank = flex && p.flexRank !== undefined ? p.flexRank : p.rank;
          return (
            <li
              key={`${p.position}-${p.rank}`}
              data-testid="pick"
              data-row="pick"
              data-tier={p.tier ?? "none"}
              style={teamStyle(p.teamColor, p.teamColor2)}
              className={`team-mark team-stripe ${showOutcome ? ROW_OUTCOME : ROW_PLAIN} px-3 py-3 pl-4`}
            >
              <div data-cell="rank" className="pt-0.5 [grid-area:rank]">
                <span className="sr-only">Rank </span>
                <span className="jersey">{rank}</span>
              </div>
              <div data-cell="player" className="min-w-0 [grid-area:player]">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  {flex ? <PosBadge pos={p.position} /> : null}
                  <Link
                    href={`/player/${p.gsisId}`}
                    className="min-w-0 font-display text-xl leading-tight font-bold tracking-wide text-fg uppercase underline decoration-line-strong decoration-1 underline-offset-4 [overflow-wrap:anywhere] hover:decoration-hot hover:decoration-2"
                  >
                    {p.name}
                  </Link>
                </div>
                <span className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-sm text-muted">
                  <span className="team-swatch" aria-hidden="true" />
                  <span className="min-w-0 [overflow-wrap:anywhere]">
                    {p.team} · {p.teamName}
                  </span>
                  {flex ? (
                    <span className="whitespace-nowrap">
                      · No. {p.rank} at {p.position}
                    </span>
                  ) : null}
                </span>
                {reasons && anyReasons ? (
                  p.reasons.length ? (
                    <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-sm">
                      {p.reasons.map((r, i) => (
                        <li key={i}>{r}</li>
                      ))}
                    </ul>
                  ) : (
                    <p className="mt-1.5 text-sm text-muted">No reasons recorded for this player.</p>
                  )
                ) : null}
              </div>
              <div data-cell="chance" className="min-w-0 [grid-area:chance]">
                <MiniLabel className="@3xl:sr-only">Chance</MiniLabel>
                <ChanceCell chance={p.chance} low={p.chanceLow} high={p.chanceHigh} />
              </div>
              <div className="flex min-w-0 flex-wrap content-start gap-x-6 gap-y-2 [grid-area:meta] @3xl:contents">
                <div data-cell="priority" className="@3xl:pt-1 @3xl:[grid-area:tier]">
                  <MiniLabel className="@3xl:sr-only">Priority</MiniLabel>
                  <TierBadge tier={p.tier} />
                </div>
                {showOutcome ? (
                  <div data-cell="outcome" className="min-w-0 @3xl:pt-1 @3xl:[grid-area:outcome]">
                    <MiniLabel className="@3xl:sr-only">Outcome</MiniLabel>
                    <OutcomeBadge outcome={outcome} />
                    {detail ? <span className="mt-1 block text-xs text-muted">{detail}</span> : null}
                  </div>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
