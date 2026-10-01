import Link from "next/link";

/** The season picker of /decisions: a plain GET form (works without JavaScript) and links to the
 *  neighbouring seasons. `seasons` newest first. */
export default function SeasonPicker({ seasons, chosen }: { seasons: number[]; chosen: number }) {
  const i = seasons.indexOf(chosen);
  const newer = i > 0 ? seasons[i - 1] : null;
  const older = i >= 0 && i < seasons.length - 1 ? seasons[i + 1] : null;
  return (
    <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
      <form method="get" action="/decisions" className="flex flex-wrap items-end gap-3" aria-label="Choose a season">
        <label className="flex flex-col text-sm font-medium">
          Season
          <select
            name="season"
            defaultValue={String(chosen)}
            className="mt-1 min-h-11 rounded-md border border-line bg-surface px-2 text-fg"
          >
            {seasons.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" className="min-h-11 rounded-md bg-accent px-4 text-sm font-semibold text-on-accent hover:opacity-90">
          Show season
        </button>
      </form>
      <nav aria-label="Neighbouring seasons" className="flex gap-3 text-sm">
        {older !== null ? (
          <Link href={`/decisions?season=${older}`} className="inline-flex min-h-11 items-center">
            <span aria-hidden="true">&larr;&nbsp;</span>
            {older}
          </Link>
        ) : null}
        {newer !== null ? (
          <Link href={`/decisions?season=${newer}`} className="inline-flex min-h-11 items-center">
            {newer}
            <span aria-hidden="true">&nbsp;&rarr;</span>
          </Link>
        ) : null}
      </nav>
    </div>
  );
}
