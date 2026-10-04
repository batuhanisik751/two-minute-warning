import { Note } from "@/components/ui";
import { THIRD_PARTY_NOTE, showThirdPartyRanks } from "@/lib/third-party";

/** The license note (lib/third-party.ts), said once on a page whose FantasyPros values are hidden;
 *  nothing on the private site. `inline`: a quiet line instead of a boxed note. */
export default function ThirdPartyNote({ inline = false }: { inline?: boolean }) {
  if (showThirdPartyRanks()) return null;
  const t = <span data-testid="third-party-note">{THIRD_PARTY_NOTE}</span>;
  return inline ? <p className="mt-2 text-sm text-muted">{t}</p> : <Note>{t}</Note>;
}
