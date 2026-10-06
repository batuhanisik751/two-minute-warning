import Link from "next/link";
import { MetricHeader, TendencyCell } from "@/components/coach-tendencies/cells";
import { FoldTable } from "@/components/Fold";
import Term from "@/components/Term";
import { Note } from "@/components/ui";
import { groupSeasons, persistenceNote, TENDENCY_METRICS, type TendencySeason } from "@/lib/coach-tendencies";
import { fmtInt } from "@/lib/format";
import { getCoachTendencies, getTendencyStudies } from "@/lib/queries/coach-tendencies";
import CareerTable from "./CareerTable";

function SeasonsTable({ rows, name }: { rows: TendencySeason[]; name: string }) {
  return (
    <FoldTable
      label={`${name}'s offense by season`}
      rows={rows.map((s) => (
        <tr key={`${s.season}-${s.team}`} data-row="" data-season={s.season}>
          <th scope="row" data-cell="season">
            <span className="block whitespace-nowrap">
              {s.season} {s.team}
            </span>
            <span className="block text-xs font-normal whitespace-nowrap text-muted" data-testid={s.isCurrent ? "tendency-partial" : undefined}>
              {s.isCurrent ? `through week ${s.throughWeek}: ${fmtInt(s.plays)} snaps so far` : `${fmtInt(s.plays)} snaps`}
            </span>
          </th>
          {TENDENCY_METRICS.map((m) => (
            <TendencyCell key={m} metric={m} row={s.cells[m]} />
          ))}
        </tr>
      ))}
      table={(body) => (
        <div className="table-scroll">
          <table className="data-table" data-testid="tendency-seasons">
            <caption className="sr-only">{name}&apos;s offense by season against the league: each value and where it ranked that season</caption>
            <thead>
              <tr>
                <th scope="col">Season</th>
                {TENDENCY_METRICS.map((m) => (
                  <MetricHeader key={m} metric={m} />
                ))}
              </tr>
            </thead>
            {body}
          </table>
        </div>
      )}
    />
  );
}

/** The coach page's "How his offense plays" (feature #10): every season of his offense against
 *  the league (value and percentile; the season in progress says how much of it), his career
 *  line, and the persistence note from the published persistence table. */
export default async function CoachTendencies({ coachId, name }: { coachId: string; name: string }) {
  const [{ seasons, career }, studies] = await Promise.all([getCoachTendencies(coachId), getTendencyStudies()]);
  const rows = groupSeasons(seasons);
  const note = persistenceNote(studies.persistence);
  return (
    <section aria-labelledby="tendencies-heading" className="mt-10" data-testid="coach-tendencies">
      <h2 id="tendencies-heading" className="section-title mb-2 scroll-mt-24">
        How his offense plays
      </h2>
      <p className="mb-3 max-w-3xl text-sm text-muted">
        Every regular-season snap of his team&apos;s offense, season by season, newest first. Under each value: where the offense ranked that season (
        <Term name="tendency_percentile">percentile</Term>; pace reads &ldquo;faster than&rdquo; a share of offenses). Most numbers are{" "}
        <Term name="is_neutral">neutral situations</Term> only, so a blowout does not count.
      </p>
      {rows.length ? <SeasonsTable rows={rows} name={name} /> : <p className="text-muted" data-testid="no-tendencies">No play-by-play season is published for this coach.</p>}
      {career.length ? <CareerTable rows={career} name={name} /> : null}
      {note && rows.length ? (
        <div className="mt-4 max-w-3xl" data-testid="tendency-persistence-note">
          <Note>
            <strong>How much of this is the coach?</strong> {note.stays} {note.moves} {note.team} {note.sample}{" "}
            <Link href="/methodology#coach-tendencies">How it is measured</Link>
          </Note>
        </div>
      ) : null}
    </section>
  );
}
