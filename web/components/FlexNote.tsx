import Term from "@/components/Term";
import type { FlexOrder } from "@/lib/flex";
import { startersPhrase, type LeagueShape } from "@/lib/league";
import { FLEX_POSITIONS } from "@/lib/positions";

/** What FLEX is, said once and plainly: three lists side by side, each chance about a starter
 *  finish at the player's OWN position (the league shape comes from the published glossary,
 *  lib/league.ts), so it is a way to browse, not a separate model. Plus how this list was
 *  ordered, and which of the three lists the week lacks. */
export default function FlexNote({
  shape,
  order,
  missing,
  compact = false,
}: {
  shape: LeagueShape | null;
  order: FlexOrder;
  missing: readonly string[];
  /** the home page's card: the first paragraph only */
  compact?: boolean;
}) {
  const phrase = startersPhrase(shape, FLEX_POSITIONS);
  const league = shape?.teams ? ` in a ${shape.teams}-team league` : "";
  return (
    <div className="rounded-md border border-line border-l-4 border-l-[var(--pos-flex)] bg-surface px-4 py-3 text-sm" data-testid="flex-note">
      <p>
        <strong>FLEX is a way to browse, not a separate model.</strong> It puts the week&apos;s RB, WR and TE lists
        together, ordered by <Term name="chance">chance</Term>. Each chance is the chance of a starter week at the
        player&apos;s own position (
        {phrase ? (
          <>
            a {phrase} week{league}
          </>
        ) : (
          <>
            the <Term name="starter_threshold">starter threshold</Term> of his position
          </>
        )}
        ), so FLEX compares three slightly different targets.
      </p>
      {!compact ? (
        <p className="mt-2 text-muted" data-testid="flex-order">
          {order === "model"
            ? "These lists have no chances, so FLEX orders them by the model's probability instead (it is not shown on the site); ties go to the better rank in his own list."
            : order === "mixed"
              ? "Some of these lists have no chances: players with a chance come first, the rest follow by the model's probability (not shown on the site)."
              : "Equal chances are ordered by the model's probability (not shown on the site), then by the rank in his own list."}
          {missing.length ? ` This week has no ${missing.join(" or ")} list, so FLEX merges the others.` : null}
        </p>
      ) : null}
    </div>
  );
}
