import Link from "next/link";
import LiveRecord from "@/components/teammate-out/LiveRecord";
import { CandidateTable, CoverageTable } from "@/components/teammate-out/RecordParts";
import { getTeammateOutModel, getTeammateOutTables, getTeammateOutWeek } from "@/lib/queries/teammate-out";
import { Panel } from "./parts";

/** /track-record's Teammate-out panels: the four candidates' walk-forward scores (the rule's pick
 *  and the one used, disclosed), the 80% range's coverage, and this season's live record
 *  (teammate_out_backtest, teammate_out_coverage, teammate_out_live, the version's params). */
export default async function TeammateOutTrack() {
  const [tables, week, model] = await Promise.all([getTeammateOutTables(), getTeammateOutWeek(), getTeammateOutModel()]);
  const season = week?.season ?? tables.live[0]?.season ?? null;
  return (
    <>
      <p className="mt-2">
        When a starting RB, WR or TE is out: which teammates get his carries and targets, and how many PPR points that is worth. See{" "}
        <Link href="/teammate-out">this week&apos;s list</Link>.
      </p>
      <Panel kind="backtest" title="Walk-forward backtest" id="to-track-backtest" context="Teammate out">
        <CandidateTable rows={tables.backtest} params={model?.params ?? null} />
        <CoverageTable rows={tables.coverage} />
      </Panel>
      <Panel kind="live" title={season ? `${season} so far` : "This season so far"} id="to-track-live" context="Teammate out">
        <LiveRecord rows={tables.live} season={season} />
      </Panel>
    </>
  );
}
