import Link from "next/link";
import Candidates from "@/components/playoff-planner/Candidates";
import LiveRecord from "@/components/playoff-planner/LiveRecord";
import Matters from "@/components/playoff-planner/Matters";
import { getPlannerMeta, getPlannerModel, getPlannerTables } from "@/lib/queries/playoff-planner";
import { Panel } from "./parts";

/** /track-record's playoff-planner panels: the candidates' walk-forward scores with the rule's
 *  pick, how much a matchup mattered, and the season's live record (graded after the last
 *  playoff week): playoff_planner_backtest, _effects, _stability, _live and the version's params. */
export default async function PlayoffPlannerTrack() {
  const [tables, meta, model] = await Promise.all([getPlannerTables(), getPlannerMeta(), getPlannerModel()]);
  const season = meta.season ?? tables.live[0]?.season ?? null;
  return (
    <>
      <p className="mt-2">
        Matchup ratings for the fantasy playoff weeks: which rating the fixed rule picked per position, and how much a matchup really moved points. See{" "}
        <Link href="/playoff-planner">the planner</Link>.
      </p>
      <Panel kind="backtest" title="Walk-forward backtest" id="pp-track-backtest" context="Playoff planner">
        <Candidates rows={tables.backtest} params={model?.params ?? null} />
        <Matters effects={tables.effects} stability={tables.stability} choice={tables.choice} backtest={tables.backtest} />
      </Panel>
      <Panel kind="live" title={season ? `${season} so far` : "This season so far"} id="pp-track-live" context="Playoff planner">
        <LiveRecord rows={tables.live} season={season} lastWeek={meta.weeks[meta.weeks.length - 1]} />
      </Panel>
    </>
  );
}
