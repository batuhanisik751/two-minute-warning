import Link from "next/link";
import { BacktestScore, LiveRecord, QCalibration } from "@/components/questionable/RecordParts";
import { getQuestionableTables, getQuestionableWeek } from "@/lib/queries/questionable";
import { Panel } from "./parts";

/** /track-record's Questionable panels: the pinned table's walk-forward score and calibration,
 *  and this season's live record (questionable_backtest, questionable_calibration,
 *  questionable_live). */
export default async function QuestionableTrack() {
  const [tables, week] = await Promise.all([getQuestionableTables(), getQuestionableWeek()]);
  const season = week?.season ?? tables.live[0]?.season ?? null;
  return (
    <>
      <p className="mt-2">
        The chance a Questionable or Doubtful player plays (at least one offensive snap), from a frozen table of past seasons. See{" "}
        <Link href="/questionable">this week&apos;s list</Link>.
      </p>
      <Panel kind="backtest" title="Walk-forward backtest" id="q-track-backtest" context="Questionable outcomes">
        <BacktestScore rows={tables.backtest} />
        <QCalibration rows={tables.calibration} id="q-track-cal" />
      </Panel>
      <Panel kind="live" title={season ? `${season} so far` : "This season so far"} id="q-track-live" context="Questionable outcomes">
        <LiveRecord rows={tables.live} season={season} />
      </Panel>
    </>
  );
}
