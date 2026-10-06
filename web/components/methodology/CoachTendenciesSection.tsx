import Link from "next/link";
import Term from "@/components/Term";
import { EmptyState } from "@/components/ui";
import { COMPARISON_TEXT, COMPARISONS, fantasyLinkText, fmtR, METRIC_TEXT, persistenceNote, TENDENCY_METRICS, type LinkRow, type PersistenceRow } from "@/lib/coach-tendencies";
import { getTendencyStudies } from "@/lib/queries/coach-tendencies";
import { docUrl } from "@/lib/site";

const rText = (r: { r: number | null; ciLow: number | null; ciHigh: number | null }) =>
  r.r === null ? "–" : `${fmtR(r.r)}${r.ciLow !== null && r.ciHigh !== null ? ` (${fmtR(r.ciLow)} to ${fmtR(r.ciHigh)})` : ""}`;

function PersistenceTable({ rows }: { rows: PersistenceRow[] }) {
  const get = (m: string, c: string) => rows.find((r) => r.metric === m && r.comparison === c);
  return (
    <div className="table-scroll">
      <table className="data-table" data-testid="tendency-persistence">
        <caption className="sr-only">Year-to-year correlation of each tendency, by who stayed: r with its 95% interval and the number of season pairs</caption>
        <thead>
          <tr>
            <th scope="col">Tendency</th>
            {COMPARISONS.map((c) => (
              <th key={c} scope="col" className="num">
                {COMPARISON_TEXT[c]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {TENDENCY_METRICS.map((m) => (
            <tr key={m} data-row="" data-metric={m}>
              <th scope="row" data-cell="metric">
                <Term name={m}>{METRIC_TEXT[m].short}</Term>
              </th>
              {COMPARISONS.map((c) => {
                const r = get(m, c);
                return (
                  <td key={c} className="num" data-cell={c}>
                    <span className="block whitespace-nowrap">{r ? rText(r) : "–"}</span>
                    <span className="block text-xs text-muted">{r ? `${r.nPairs} pairs` : ""}</span>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LinkTable({ rows }: { rows: LinkRow[] }) {
  const get = (m: string, h: string) => rows.find((r) => r.metric === m && r.target === "targets_per_game" && r.horizon === h);
  return (
    <div className="table-scroll">
      <table className="data-table" data-testid="tendency-link">
        <caption className="sr-only">Correlation of each tendency with the team&apos;s targets per game, the same season and the next, with 95% intervals</caption>
        <thead>
          <tr>
            <th scope="col">Tendency</th>
            <th scope="col" className="num">
              Same season
            </th>
            <th scope="col" className="num">
              Next season
            </th>
          </tr>
        </thead>
        <tbody>
          {TENDENCY_METRICS.map((m) => (
            <tr key={m} data-row="" data-metric={m}>
              <th scope="row" data-cell="metric">
                <Term name={m}>{METRIC_TEXT[m].short}</Term>
              </th>
              {(["same_season", "next_season"] as const).map((h) => {
                const r = get(m, h);
                return (
                  <td key={h} className="num whitespace-nowrap" data-cell={h}>
                    {r ? rText(r) : "–"}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** /methodology's coach-tendencies section (feature #10): what is counted and how, the
 *  persistence study and the fantasy link (both from the published tables), and the limits. */
export default async function CoachTendenciesSection() {
  const { persistence, link } = await getTendencyStudies();
  const note = persistenceNote(persistence);
  const fl = fantasyLinkText(link);
  const pairs = persistence.filter((r) => r.firstSeason !== null && r.lastSeason !== null);
  const years = pairs.length ? `${Math.min(...pairs.map((r) => r.firstSeason!))}–${Math.max(...pairs.map((r) => r.lastSeason!)) + 1}` : null;
  return (
    <section aria-labelledby="coach-tendencies" data-testid="coach-tendencies-method">
      <h2 id="coach-tendencies" className="section-title scroll-mt-24">
        Coach tendencies
      </h2>
      {!persistence.length && !link.length ? (
        <div className="mt-3">
          <EmptyState title="No coach tendencies published yet" />
        </div>
      ) : (
        <div className="mt-3 space-y-4">
          <p>
            How each head coach&apos;s offense plays, counted from play-by-play: every regular-season snap is credited to the head coach of the team with the ball in
            that game, so a coach fired mid-season keeps his own games. Most rates use <Term name="is_neutral">neutral situations</Term> only (early downs of the first
            three quarters with the game still open), which removes game script: the trailing team throws, the leading team runs. The tendencies:{" "}
            {TENDENCY_METRICS.map((m, i) => (
              <span key={m}>
                {i ? ", " : ""}
                <Term name={m}>{METRIC_TEXT[m].long}</Term>
              </span>
            ))}
            . Each season is compared with the league that season, with a <Term name="tendency_percentile">percentile</Term> among that season&apos;s offenses.
          </p>
          <h3 className="display mt-6 text-xl uppercase">Is it the coach or the team?</h3>
          <p>
            <Term name="tendency_persistence">Persistence</Term>: the correlation of a team&apos;s style one season with the next{years ? ` (${years})` : ""}, values relative
            to the league that season, with a 95% interval from redrawing whole seasons. Three kinds of pairs: the coach and the team stay; the coach moves to a new team;
            the team gets a new coach.
          </p>
          {persistence.length ? <PersistenceTable rows={persistence} /> : null}
          {note ? (
            <p data-testid="tendency-persistence-text">
              {note.stays} {note.moves} {note.team} {note.sample}
            </p>
          ) : null}
          <h3 className="display mt-6 text-xl uppercase">What it means for fantasy</h3>
          <p>
            <Term name="tendency_fantasy_link">The fantasy link</Term>, measured on completed team-seasons: each tendency against the team&apos;s pass-catchers&apos;
            targets per game, the same season and the next.
          </p>
          {link.length ? <LinkTable rows={link} /> : null}
          {fl ? (
            <p data-testid="tendency-link-text">
              {fl.same} {fl.next}
            </p>
          ) : null}
          <p>
            Limits: the data knows head coaches, not who calls the plays (a defensive head coach&apos;s offense is his coordinator&apos;s); pace is game-clock seconds, so
            incompletions make an offense look faster; four weeks into a season a team has only a few hundred snaps, so early ranks move; PROE rests on nflfastR&apos;s
            expected pass rate, a model.
          </p>
          <p className="text-sm">
            <Link href="/decisions#tendencies">This season&apos;s table</Link> · <a href={docUrl("docs/coach_tendencies.md")}>The full write-up (docs/coach_tendencies.md)</a>
          </p>
        </div>
      )}
    </section>
  );
}
