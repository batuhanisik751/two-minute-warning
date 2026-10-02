// Small shared pieces of the Hot-Seat Meter (server components). Neutral on purpose: a grey-blue
// bar, no heat colours, no icons that judge; the text is always the source of truth.
import { MiniLabel } from "@/components/ui";
import { outcomeWords, wholePct, type OutcomeRow } from "@/lib/hot-seat";

/** The estimate as a whole percent with a bar under it (hidden from screen readers). */
export function EstimateCell({ probability, size = "big" }: { probability: number; size?: "big" | "small" }) {
  const w = `${Math.max(0, Math.min(1, probability)) * 100}%`;
  return (
    <span className="block tnum">
      <span className={`big-number block ${size === "big" ? "text-[1.875rem]" : "text-xl"}`}>{wholePct(probability)}</span>
      <span className="meter mt-1 block" aria-hidden="true">
        <span className="meter-fill meter-estimate" style={{ width: w }} />
      </span>
      <MiniLabel className="mt-1">Estimated chance</MiniLabel>
    </span>
  );
}

/** What really happened to the coach (final) or "Pending" (live seasons), as a quiet tag. */
export function OutcomeTag({ outcome, long = false }: { outcome: OutcomeRow | null; long?: boolean }) {
  const o = outcomeWords(outcome);
  const cls = o.tone === "pending" ? "border-pending text-pending" : o.tone === "let-go" ? "border-line-strong text-fg font-semibold" : "border-line text-muted";
  return (
    <span className="inline-flex flex-wrap items-baseline gap-x-2">
      <span data-outcome={o.tone} className={`inline-flex items-center rounded border-[1.5px] px-1.5 py-0.5 text-xs whitespace-nowrap ${cls}`}>
        {o.short}
      </span>
      {long ? <span className="text-sm text-muted">{o.long}</span> : null}
    </span>
  );
}

/** "Interim" with the reason, for coaches who took over during the season. */
export function InterimFlag() {
  return (
    <span className="inline-flex flex-wrap items-baseline gap-x-2 text-sm" data-testid="interim-flag">
      <span className="inline-flex items-center rounded border-[1.5px] border-line-strong px-1.5 py-0.5 text-xs font-semibold whitespace-nowrap">Interim</span>
      <span className="text-muted">the model was not trained on interim coaches</span>
    </span>
  );
}
