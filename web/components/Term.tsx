import { lookupTerm } from "@/lib/queries/glossary";
import TermPopover from "./TermPopover";

type Props = {
  /** The glossary name (e.g. "y_hit"): an entry of src/twm/registry.py. */
  name: string;
  /** What the page shows; defaults to the term's title. */
  children?: React.ReactNode;
  /** Also show how it is computed (the glossary's formula): for label definitions. */
  showFormula?: boolean;
};

/** A term with its plain-English explanation, from the glossary table (src/twm/registry.py,
 *  published by `twm publish`), else from the fallback generated from the same registry
 *  (lib/glossary-fallback.ts). An unknown name renders the text alone. */
export default async function Term({ name, children, showFormula = false }: Props) {
  const entry = await lookupTerm(name);
  if (!entry) return <>{children ?? name}</>;
  return (
    <TermPopover
      label={children ?? entry.title}
      title={entry.title}
      explanation={entry.explanation}
      formula={showFormula ? entry.formula : undefined}
      href={`/methodology#term-${entry.name}`}
    />
  );
}
