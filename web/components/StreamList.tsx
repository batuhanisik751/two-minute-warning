import { outcomeOf } from "@/lib/format";
import type { StreamPick } from "@/lib/queries/stream";
import { nextGame, streamResult } from "@/lib/streamer";
import { teamStyle } from "@/lib/team-colors";
import { FoldList } from "./Fold";
import { HEAD_OUTCOME, HEAD_PLAIN, ROW_OUTCOME, ROW_PLAIN } from "./PickList";
import { ChanceCell, MiniLabel, OutcomeBadge, TierBadge } from "./ui";

type Props = {
  picks: StreamPick[];
  /** the list's accessible name, e.g. "K list, 2026 week 3 (live)" */
  label: string;
  /** the starter-week cutoffs from the glossary (lib/streamer.ts streamTopN) */
  topN: Record<string, number>;
  reasons?: boolean;
  showOutcome?: boolean;
};

/** A K or D/ST streamer list: the Waiver Radar's row layout (components/PickList.tsx: the rows
 *  lay out by their container's width, every cell a grid area, names wrap), with the next game
 *  under the team. Kickers and team defenses have no player page (no weekly rows are published
 *  for them), so the name is plain text. */
export default function StreamList({ picks, label, topN, reasons = true, showOutcome = true }: Props) {
  const anyReasons = picks.some((p) => p.reasons.length > 0);
  const who = picks[0]?.position === "DST" ? "Team defense" : "Kicker";
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
          {who}
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
      <FoldList
        label={label}
        className="divide-y divide-line overflow-hidden rounded-lg border border-line bg-surface"
        testId="stream-list"
        items={picks.map((p) => (
          <Row key={`${p.position}-${p.rank}`} p={p} topN={topN} reasons={reasons && anyReasons} showOutcome={showOutcome} />
        ))}
      />
    </div>
  );
}

function Row({ p, topN, reasons, showOutcome }: { p: StreamPick; topN: Record<string, number>; reasons: boolean; showOutcome: boolean }) {
  const outcome = outcomeOf(p.outcome?.yStart ?? null, p.outcome?.status ?? null);
  const result = p.outcome ? streamResult(p.outcome.yStart, p.outcome.status, p.outcome.points, topN, p.position) : null;
  const game = nextGame(p.opponent, p.home);
  return (
    <li
      data-testid="stream-pick"
      data-row="pick"
      data-tier={p.tier ?? "none"}
      style={teamStyle(p.teamColor, p.teamColor2)}
      className={`team-mark team-stripe ${showOutcome ? ROW_OUTCOME : ROW_PLAIN} px-3 py-3 pl-4`}
    >
      <div data-cell="rank" className="pt-0.5 [grid-area:rank]">
        <span className="sr-only">Rank </span>
        <span className="jersey">{p.rank}</span>
      </div>
      <div data-cell="player" className="min-w-0 [grid-area:player]">
        <p className="min-w-0 font-display text-xl leading-tight font-bold tracking-wide text-fg uppercase [overflow-wrap:anywhere]">
          {p.name}
        </p>
        <span className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-sm text-muted">
          <span className="team-swatch" aria-hidden="true" />
          <span className="min-w-0 [overflow-wrap:anywhere]">
            {p.team} · {p.teamName}
          </span>
        </span>
        <span className="mt-0.5 block text-sm [overflow-wrap:anywhere]" data-testid="next-game">
          Next game: {game ? `${game}${p.opponentName ? `, ${p.opponentName}` : ""}` : "not known yet"}
        </span>
        {reasons ? (
          p.reasons.length ? (
            <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-sm">
              {p.reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          ) : (
            <p className="mt-1.5 text-sm text-muted">No reasons recorded for this pick.</p>
          )
        ) : null}
      </div>
      <div data-cell="chance" className="min-w-0 [grid-area:chance]">
        <MiniLabel className="@3xl:sr-only">Chance</MiniLabel>
        <ChanceCell chance={p.chance} low={p.chanceLow} high={p.chanceHigh} rangeWords="similar picks started" />
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
            {result ? <span className="mt-1 block text-xs text-muted">{result}</span> : null}
          </div>
        ) : null}
      </div>
    </li>
  );
}
