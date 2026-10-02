import Link from "next/link";
import { KindBadge } from "@/components/ui";
import { MODULE_NAMES, whenName, type ModuleId } from "@/lib/time-machine";
import type { WeekRef } from "@/lib/params";

/** One module's section of /time-machine. Covered: framed like the module's own page (solid =
 *  live, dashed = reconstructed, plain = graded calls) with the same badge, what it said, and a
 *  link to its full page for that week. Not covered: why, and the nearest week it covers. */
export function ModuleSection({
  module,
  at,
  kind,
  kindLine,
  href,
  linkText,
  children,
}: {
  module: ModuleId;
  at: WeekRef;
  /** the list's kind; null for the Decision Report Card (graded after the games) */
  kind: "live" | "backtest" | null;
  /** the badge's long words (a sentence, usually with its glossary term) */
  kindLine: React.ReactNode;
  href: string;
  linkText: string;
  children: React.ReactNode;
}) {
  const frame = kind === "live" ? "frame-live" : kind === "backtest" ? "frame-recon" : "border border-line";
  return (
    <section
      aria-labelledby={`tm-${module}`}
      className={`mt-8 min-w-0 rounded-xl bg-surface/40 p-3 sm:p-4 ${frame}`}
      data-testid="tm-section"
      data-module={module}
      data-kind={kind ?? "graded"}
      data-covered="yes"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 pt-1">
        <h2 id={`tm-${module}`} className="display min-w-0 text-2xl uppercase sm:text-3xl">
          {MODULE_NAMES[module]}, {whenName(at)}
        </h2>
        {kind ? <KindBadge kind={kind} /> : <span className="recon-pill">Graded after the games</span>}
      </div>
      <p className="mt-1 text-sm" data-testid="kind-label">
        {kindLine}
      </p>
      {children}
      <p className="mt-4 text-sm">
        <Link href={href}>{linkText}</Link>
      </p>
    </section>
  );
}

export function NotCovered({ module, at, reason, see, seeHref }: { module: ModuleId; at: WeekRef; reason: string; see: WeekRef | null; seeHref: string | null }) {
  return (
    <section
      aria-labelledby={`tm-${module}`}
      className="mt-8 min-w-0 rounded-xl border border-line bg-surface/40 p-3 sm:p-4"
      data-testid="tm-section"
      data-module={module}
      data-covered="no"
    >
      <h2 id={`tm-${module}`} className="display min-w-0 pt-1 text-2xl uppercase text-muted sm:text-3xl">
        {MODULE_NAMES[module]}, {whenName(at)}
      </h2>
      <p className="mt-2">
        <strong>Not covered then.</strong> <span data-testid="not-covered">{reason}</span>
      </p>
      {see && seeHref ? (
        <p className="mt-2 text-sm">
          <Link href={seeHref}>Go to {whenName(see)}</Link>
        </p>
      ) : null}
    </section>
  );
}
