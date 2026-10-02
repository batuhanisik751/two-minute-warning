import { fmtInt, pct } from "@/lib/format";
import { disagreementRecord, type DisagreementRow, type GroupTally } from "@/lib/board";
import { BOARD_DISAGREE_TOP, BOARD_MIN_GAMES } from "@/lib/method";

const WHAT = {
  cliff_main: { what: "a Cliff", did: "really had a Cliff", caption: "Who really had a Cliff, by board" },
  cliff_missed: { what: "missed time", did: "really missed time", caption: "Who really missed time, by board" },
} as const;

const share = (t: GroupTally) => (t.rate === null ? "none" : pct(t.rate));
const cell = (t: GroupTally) => `${fmtInt(t.hits)} of ${fmtInt(t.players)}`;

/** How past disagreements with the experts turned out (board_disagreement): per board, our top
 *  BOARD_DISAGREE_TOP against the experts' (the players they drop furthest), and how many of each
 *  group really had the outcome. */
export default function DisagreementRecord({ rows, variant, idPrefix = "board" }: { rows: DisagreementRow[]; variant: "cliff_main" | "cliff_missed"; idPrefix?: string }) {
  const rec = disagreementRecord(rows, variant);
  if (!rec) return <p className="text-muted">The record of past disagreements is not published yet.</p>;
  const { what, did, caption } = WHAT[variant];
  const g = rec.groups;
  const pool = variant === "cliff_main" ? ` among the players who went on to play ${BOARD_MIN_GAMES} or more games (a Cliff can only be judged for them)` : " among every player on the board";
  return (
    <div className="space-y-2" data-testid={`record-${variant}`}>
      <p>
        On the {`${rec.from}–${rec.to}`} boards, <strong>{fmtInt(g.model_only.players)}</strong> players were in our top {BOARD_DISAGREE_TOP} for {what} but not
        the experts&apos;: <strong>{fmtInt(g.model_only.hits)}</strong> of them ({share(g.model_only)}) {did}.{" "}
        <strong>{fmtInt(g.ecr_only.players)}</strong> were in the experts&apos; top {BOARD_DISAGREE_TOP} but not ours:{" "}
        <strong>{fmtInt(g.ecr_only.hits)}</strong> ({share(g.ecr_only)}). Where both agreed, {fmtInt(g.both.hits)} of {fmtInt(g.both.players)} ({share(g.both)}).
      </p>
      <div className="table-scroll rounded-lg border border-line bg-surface p-2">
        <table className="data-table" aria-describedby={`${idPrefix}-note-${variant}`}>
          <caption className="mb-1 text-left font-display text-base font-bold tracking-wide uppercase">{caption}</caption>
          <thead>
            <tr>
              <th scope="col">Board</th>
              <th scope="col" className="num">Our pick only</th>
              <th scope="col" className="num">Experts&apos; pick only</th>
              <th scope="col" className="num">Both</th>
            </tr>
          </thead>
          <tbody>
            {rec.seasons.map((s) => (
              <tr key={s.season}>
                <th scope="row">{s.season}</th>
                <td className="num tnum">{cell(s.groups.model_only)}</td>
                <td className="num tnum">{cell(s.groups.ecr_only)}</td>
                <td className="num tnum">{cell(s.groups.both)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p id={`${idPrefix}-note-${variant}`} className="text-sm text-muted">
        Each board&apos;s top {BOARD_DISAGREE_TOP} of each side, ranked{pool}. The experts&apos; list is the players their preseason ranking drops
        furthest below last season&apos;s finish. Small numbers: read them as a rough guide.
      </p>
    </div>
  );
}
