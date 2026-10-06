import Link from "next/link";
import Term from "@/components/Term";
import { AtSecondary, CoverageNote, H2hTable, IgnoredLine, ReverseTable, SourceNote } from "@/components/lead-time/Compare";
import Histogram from "@/components/lead-time/Histogram";
import { Headline, SummaryTable } from "@/components/lead-time/Summary";
import { PRIMARY, hasStudy, pooledRow, seasonsOf } from "@/lib/lead-time";
import { fmtInt } from "@/lib/format";
import { getLeadTimeTables } from "@/lib/queries/lead-time";
import { H4, NotPublished, Panel } from "./parts";

/** /track-record's "Does the Radar beat the crowd?" (feature #8, docs/lead_time.md): how many weeks
 *  before most ESPN leagues rostered a player the Radar flagged him, against the momentum baseline,
 *  and the reverse view. Aggregates only (lead_time_*), the same on the public site. The season's
 *  live tracker reads the owner's league data and stays on his computer: no live panel numbers. */
export default async function LeadTimeTrack() {
  const t = await getLeadTimeTables();
  const { span } = seasonsOf(t.coverage);
  const n = pooledRow(t.summary, "listed")?.nAdds ?? null;
  return (
    <>
      <p className="mt-2">
        For every player most ESPN leagues ended up rostering (a <Term name="crowd_add">crowd add</Term>), how many weeks earlier did the Radar flag him, and
        does it beat simply watching roster trends? See <Link href="/waivers">this week&apos;s Radar</Link>.
      </p>
      <Panel kind="backtest" title="Past seasons" id="lt-track-backtest" context="Does the Radar beat the crowd?">
        {hasStudy(t) ? (
          <>
            <Headline summary={t.summary} span={span} />
            <SummaryTable summary={t.summary} caption={`Crowd at ${PRIMARY}%${span ? `, complete seasons ${span} pooled` : ""}${n !== null ? ` (${fmtInt(n)} crowd adds)` : ""}`} />
            <Histogram hist={t.hist} summary={t.summary} caption={`How early: the Radar's lists vs the momentum baseline (crowd at ${PRIMARY}%)`} />
            <AtSecondary summary={t.summary} />
            <H4>Head to head</H4>
            <H2hTable h2h={t.h2h} />
            <H4>What became of the Radar&apos;s flags</H4>
            <IgnoredLine reverse={t.reverse} />
            <ReverseTable reverse={t.reverse} />
            <CoverageNote coverage={t.coverage} span={span} />
            <SourceNote />
          </>
        ) : (
          <NotPublished what="The lead-time study">It appears with the first publish that carries it.</NotPublished>
        )}
      </Panel>
      <Panel kind="live" title="This season" id="lt-track-live" context="Does the Radar beat the crowd?">
        <div data-testid="lt-live" data-live="none">
          <p className="font-semibold">No live results yet.</p>
          <p className="mt-1 text-sm text-muted">
            The published data has no roster percentages for the season in progress; the live tracker reads the owner&apos;s own league and stays on his
            computer.
          </p>
        </div>
      </Panel>
    </>
  );
}
