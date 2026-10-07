// Small shared pieces of UI (server components).
import type { Outcome } from "@/lib/format";
import { chancePct, kindLabel, outcomeLabel, pctRange, tierLabel } from "@/lib/format";
import { positionShort } from "@/lib/positions";

export function PageHeader({
  title,
  kicker,
  children,
}: {
  title: string;
  /** a short line above the title (no digits on pages that must show none) */
  kicker?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="mb-8">
      {kicker ? <p className="kicker mb-1">{kicker}</p> : null}
      <h1 className="display text-4xl font-extrabold uppercase sm:text-5xl">{title}</h1>
      <div className="yard-rule mt-3 max-w-md" aria-hidden="true" />
      {children ? <div className="mt-3 max-w-3xl text-muted">{children}</div> : null}
    </div>
  );
}

export function SectionTitle({ id, children, className = "" }: { id?: string; children: React.ReactNode; className?: string }) {
  return (
    <h2 id={id} className={`section-title scroll-mt-24 ${className}`}>
      {children}
    </h2>
  );
}

export function EmptyState({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="rounded-lg border-2 border-dashed border-line bg-surface p-6" data-testid="empty-state">
      <h2 className="display text-2xl uppercase">{title}</h2>
      {children ? <div className="mt-2 space-y-2 text-muted">{children}</div> : null}
    </div>
  );
}

export function Note({ children, tone = "plain" }: { children: React.ReactNode; tone?: "plain" | "warn" }) {
  return (
    <div
      className={`rounded-md border px-4 py-3 text-sm ${
        tone === "warn" ? "border-warn-fg/40 bg-warn-bg text-warn-fg" : "border-line bg-surface text-fg"
      }`}
    >
      {children}
    </div>
  );
}

/** LIVE (a gold pill) or Reconstructed (a dashed one). The dot does not move. */
export function KindBadge({ kind }: { kind: string }) {
  const k = kindLabel(kind);
  const live = kind === "live";
  return (
    <span data-kind={kind} className={live ? "live-pill" : "recon-pill"}>
      <span aria-hidden="true">{live ? "●" : "◌"}</span>
      {k.short}
    </span>
  );
}

/** A position badge: QB, RB, WR, TE, FLEX (and K, D/ST once they are published). */
export function PosBadge({ pos, className = "" }: { pos: string; className?: string }) {
  return (
    <span data-pos={pos} className={`pos-badge ${className}`}>
      {positionShort(pos)}
    </span>
  );
}

const OUTCOME_ICON: Record<Outcome, string> = {
  hit: "✓",
  "no-hit": "✕",
  pending: "…",
  unknown: "–",
};

export function OutcomeBadge({ outcome }: { outcome: Outcome }) {
  const cls = {
    hit: "border-hit text-hit font-semibold",
    "no-hit": "border-line-strong text-miss",
    pending: "border-pending text-pending",
    unknown: "border-line text-muted",
  }[outcome];
  return (
    <span
      data-outcome={outcome}
      className={`inline-flex items-center gap-1 rounded border-[1.5px] px-1.5 py-0.5 text-xs whitespace-nowrap ${cls}`}
    >
      <span aria-hidden="true">{OUTCOME_ICON[outcome]}</span>
      {outcomeLabel(outcome)}
    </span>
  );
}

export function TierBadge({ tier }: { tier: string | null }) {
  if (!tier) return <span className="text-sm text-muted">Not available</span>;
  return (
    <span className="tier" data-tier={tier}>
      {tierLabel(tier)}
    </span>
  );
}

/** A small uppercase label over a value (visually hidden where a column header row says it). */
export function MiniLabel({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <span className={`block font-display text-[0.8rem] font-bold tracking-wider text-muted uppercase ${className}`}>
      {children}
    </span>
  );
}

/** The chance as a big number, a meter (solid to the chance, pale to the top of its range;
 *  hidden from screen readers: the text is the source of truth) and "similar players hit
 *  53-59%". A NULL chance says so in words, never 0% and never the model's probability. */
export function ChanceCell({
  chance,
  low,
  high,
  rangeWords = "similar players hit",
}: {
  chance: number | null;
  low: number | null;
  high: number | null;
  /** the words before the range ("similar players hit 53-59%") */
  rangeWords?: string;
}) {
  if (chance === null) {
    return <span className="block text-sm text-muted">Not available for this list</span>;
  }
  const w = (x: number) => `${Math.max(0, Math.min(1, x)) * 100}%`;
  return (
    <span className="block tnum">
      <span className="big-number block text-[1.875rem]">{chancePct(chance)}</span>
      <span className="meter mt-1 block" aria-hidden="true">
        {low !== null && high !== null ? <span className="meter-range" style={{ left: w(low), width: w(high - low) }} /> : null}
        <span className="meter-fill" style={{ width: w(chance) }} />
      </span>
      {low !== null && high !== null ? (
        <span className="mt-1 block text-xs text-muted">
          {rangeWords} {pctRange(low, high)}
        </span>
      ) : null}
    </span>
  );
}
