// Small shared pieces of UI (server components).
import type { Outcome } from "@/lib/format";
import { kindLabel, outcomeLabel, pct, pctRange, tierLabel } from "@/lib/format";

export function PageHeader({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="mb-6">
      <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">{title}</h1>
      {children ? <div className="mt-2 max-w-3xl text-muted">{children}</div> : null}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-line bg-surface p-6" data-testid="empty-state">
      <h2 className="text-lg font-semibold">{title}</h2>
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

export function KindBadge({ kind }: { kind: string }) {
  const k = kindLabel(kind);
  const live = kind === "live";
  return (
    <span
      data-kind={kind}
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold ${
        live ? "bg-accent text-on-accent" : "border border-dashed border-recon text-recon"
      }`}
    >
      <span aria-hidden="true">{live ? "●" : "◌"}</span>
      {k.short}
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
    "no-hit": "border-line text-miss",
    pending: "border-pending text-pending",
    unknown: "border-line text-muted",
  }[outcome];
  return (
    <span data-outcome={outcome} className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-xs ${cls}`}>
      <span aria-hidden="true">{OUTCOME_ICON[outcome]}</span>
      {outcomeLabel(outcome)}
    </span>
  );
}

export function TierBadge({ tier }: { tier: string | null }) {
  if (!tier) return <span className="text-sm text-muted">Not available</span>;
  const cls =
    tier === "must-add"
      ? "bg-accent-soft text-fg font-semibold border-accent"
      : tier === "speculative"
        ? "text-fg border-line"
        : "text-muted border-line";
  return <span className={`inline-block rounded border px-1.5 py-0.5 text-xs ${cls}`}>{tierLabel(tier)}</span>;
}

/** "56%" with "similar players hit 53-59%" under it; a NULL chance says so in words. */
export function ChanceText({
  chance,
  low,
  high,
  compact = false,
}: {
  chance: number | null;
  low: number | null;
  high: number | null;
  compact?: boolean;
}) {
  if (chance === null) {
    return <span className="text-sm text-muted">Not available for this list</span>;
  }
  return (
    <span className="tnum">
      <span className="text-base font-semibold">{pct(chance)}</span>
      {low !== null && high !== null ? (
        <span className={`${compact ? "ml-1" : "block"} text-xs text-muted`}>
          similar players hit {pctRange(low, high)}
        </span>
      ) : null}
    </span>
  );
}
