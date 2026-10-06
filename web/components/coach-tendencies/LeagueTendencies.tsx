import Link from "next/link";
import { TendencyCell } from "@/components/coach-tendencies/cells";
import { FoldTable } from "@/components/Fold";
import Term from "@/components/Term";
import {
  currentCoaches,
  fantasyLinkText,
  groupSeasons,
  METRIC_TEXT,
  sortSeasons,
  TENDENCY_METRICS,
  type LinkRow,
  type TendencySort,
} from "@/lib/coach-tendencies";
import { fmtInt } from "@/lib/format";
import type { NamedTendencyRow } from "@/lib/queries/coach-tendencies";

/** /decisions?...&tsort=<sort>#tendencies (the page's own query kept; PROE, the default, left out). */
export function tendencyHref(query: Record<string, string>, sort: TendencySort): string {
  const sp = new URLSearchParams(query);
  if (sort !== "proe") sp.set("tsort", sort);
  const q = sp.toString();
  return `/decisions${q ? `?${q}` : ""}#tendencies`;
}

/** A sortable column header: a metric's header is its glossary term with a "sort" link under it
 *  (a button cannot live inside a link); the coach column's whole label is the link. */
function SortHeader({ label, sortKey, sort, query }: { label: string; sortKey: TendencySort; sort: TendencySort; query: Record<string, string> }) {
  const active = sort === sortKey;
  const up = sortKey === "team" || sortKey === "neutral_sec_per_play";
  const metric = sortKey === "team" ? null : sortKey;
  return (
    <th scope="col" className={metric ? "num" : undefined} aria-sort={active ? (up ? "ascending" : "descending") : undefined}>
      {metric ? (
        <span className="block">
          <Term name={metric}>{label}</Term>
        </span>
      ) : null}
      {/* relative: the screen-reader words are absolutely placed, inside the table's scroll box */}
      <Link href={tendencyHref(query, sortKey)} className="relative inline-flex min-h-11 items-center" data-sort={sortKey}>
        {metric ? "sort" : label}
        {metric ? <span className="sr-only"> by {METRIC_TEXT[metric].long}</span> : null}
        {active ? <span aria-hidden="true">{up ? " ▲" : " ▼"}</span> : null}
      </Link>
    </th>
  );
}

/** /decisions' league table of the current head coaches' tendencies in the newest season (one
 *  row per team: the coach of its latest game), sortable by links (no JavaScript), with the
 *  fantasy link stated from the published table. */
export default function LeagueTendencies(props: { season: number; rows: NamedTendencyRow[]; link: LinkRow[]; sort: TendencySort; query: Record<string, string> }) {
  const { season, sort, query } = props;
  const names = new Map(props.rows.map((r) => [r.coachId, r.name]));
  const all = groupSeasons(props.rows);
  const rows = sortSeasons(currentCoaches(all), sort);
  const partial = rows.some((r) => r.isCurrent);
  const week = Math.max(...rows.map((r) => r.throughWeek));
  const link = fantasyLinkText(props.link);
  return (
    <section aria-labelledby="tendencies" className="mt-10" data-testid="league-tendencies">
      <h2 id="tendencies" className="section-title mb-2 scroll-mt-24">
        How each offense plays, {season}
        {partial ? ` (through week ${week})` : ""}
      </h2>
      <p className="mb-3 max-w-3xl text-sm text-muted">
        Each team&apos;s current head coach and his offense this season: <Term name="neutral_pass_rate">pass rate</Term>,{" "}
        <Term name="proe">pass rate over expected</Term>, <Term name="neutral_sec_per_play">pace</Term>, <Term name="no_huddle_rate">no-huddle</Term>,{" "}
        <Term name="shotgun_rate">shotgun</Term> and <Term name="fourth_go_rate">fourth-down go</Term> rates, with where each ranks (
        <Term name="tendency_percentile">percentile</Term>). Sort by any column.{partial ? " Early in a season the samples are small: a short sample is not ranked." : ""}
      </p>
      {link ? (
        <p className="mb-4 max-w-3xl text-sm" data-testid="tendency-fantasy-link">
          <strong>For fantasy:</strong> {link.same} {link.next}
        </p>
      ) : null}
      <FoldTable
        label={`Offenses of ${season}`}
        rows={rows.map((s) => (
          <tr key={s.team} data-row="" data-team={s.team}>
            <th scope="row" data-cell="coach">
              <Link href={`/coach/${s.coachId}`} className="block">
                {names.get(s.coachId) ?? s.coachId}
              </Link>
              <span className="block text-xs font-normal whitespace-nowrap text-muted">
                {s.team}, {fmtInt(s.plays)} snaps
              </span>
            </th>
            {TENDENCY_METRICS.map((m) => (
              <TendencyCell key={m} metric={m} row={s.cells[m]} />
            ))}
          </tr>
        ))}
        table={(body) => (
          <div className="table-scroll">
            <table className="data-table" data-testid="tendency-league">
              <caption className="sr-only">Every team&apos;s current head coach and his offense&apos;s tendencies in {season}</caption>
              <thead>
                <tr>
                  <SortHeader label="Coach (team)" sortKey="team" sort={sort} query={query} />
                  {TENDENCY_METRICS.map((m) => (
                    <SortHeader key={m} label={METRIC_TEXT[m].short} sortKey={m} sort={sort} query={query} />
                  ))}
                </tr>
              </thead>
              {body}
            </table>
          </div>
        )}
      />
    </section>
  );
}
