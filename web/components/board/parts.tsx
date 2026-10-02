// Small shared pieces of the Cliff board (server components). Neutral on purpose: grey-blue bars,
// quiet tags, no alarm colours; the text is always the source of truth.
import { boardOutcomeWords, disagreeWords, type BoardOutcome, type Disagree, type RankRow } from "@/lib/board";

/** What happened in the board's season (final) or "Pending", as a quiet tag with its words. */
export function BoardOutcomeTag({ outcome, ppgS, long = false }: { outcome: BoardOutcome | null; ppgS: number; long?: boolean }) {
  const o = boardOutcomeWords(outcome, ppgS);
  const cls = o.tone === "pending" ? "border-pending text-pending" : o.tone === "held" ? "border-line text-muted" : "border-line-strong text-fg font-semibold";
  return (
    <span className="inline-flex flex-wrap items-baseline gap-x-2">
      <span data-outcome={o.tone} className={`inline-flex items-center rounded border-[1.5px] px-1.5 py-0.5 text-xs whitespace-nowrap ${cls}`}>
        {o.short}
      </span>
      {long ? <span className="text-sm text-muted">{o.long}</span> : null}
    </span>
  );
}

/** The "where we disagree" marker with the ranks behind it. */
export function DisagreeTag({ d, position, r }: { d: Disagree; position: string; r: RankRow }) {
  const w = disagreeWords(d, position, r);
  return (
    <span className="block text-sm" data-testid="disagree" data-disagree={d}>
      <span className="inline-flex items-center rounded border-[1.5px] border-line-strong px-1.5 py-0.5 text-xs font-semibold">
        Where we disagree: {w.short}
      </span>{" "}
      <span className="text-muted">({w.long})</span>
    </span>
  );
}
