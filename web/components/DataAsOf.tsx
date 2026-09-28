import { BuildTimeReadError } from "@/db/client";
import { fmtUtc, seasonWeek } from "@/lib/format";
import { getSiteMeta } from "@/lib/queries/meta";
import Term from "./Term";

/** "Data as of <the Tuesday 14:00 UTC as-of>, updated <generated_at>" on every page. Never
 *  throws for a database problem: the page below says what went wrong. */
export default async function DataAsOf() {
  let meta;
  try {
    meta = await getSiteMeta();
  } catch (err) {
    if (err instanceof BuildTimeReadError) throw err;
    return (
      <p className="text-sm text-muted" data-testid="data-as-of">
        Data as of: not available right now (the database could not be read).
      </p>
    );
  }
  if (!meta.asOf) {
    return (
      <p className="text-sm text-muted" data-testid="data-as-of">
        Data as of: no weekly list has been published yet
        {meta.generatedAt ? (
          <>
            {" "}
            (updated{" "}
            <time dateTime={meta.generatedAt} className="font-medium text-fg">
              {fmtUtc(meta.generatedAt)}
            </time>
            )
          </>
        ) : null}
        .
      </p>
    );
  }
  return (
    <p className="text-sm text-muted" data-testid="data-as-of">
      <Term name="as_of">Data as of</Term>{" "}
      <time dateTime={meta.asOf.at} className="font-medium text-fg">
        {fmtUtc(meta.asOf.at)}
      </time>{" "}
      ({seasonWeek(meta.asOf.week.season, meta.asOf.week.week)})
      {meta.generatedAt ? (
        <>
          , updated{" "}
          <time dateTime={meta.generatedAt} className="font-medium text-fg">
            {fmtUtc(meta.generatedAt)}
          </time>
        </>
      ) : null}
    </p>
  );
}
