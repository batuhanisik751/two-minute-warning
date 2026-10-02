import { fmtInt, pct } from "@/lib/format";
import type { BoardCalGroup } from "@/lib/board";

const CAPTION = { cliff: "Chance of a Cliff", missed: "Chance of missed time" } as const;
const HAPPENED = { cliff: "Really had a Cliff", missed: "Really missed time" } as const;

/** Calibration by band: within each band of a chance, the players behind it, the average chance and
 *  how often it really happened. One small table per chance, so a phone reads them without
 *  scrolling sideways. */
export default function BoardCalibration({ groups, idPrefix = "board-cal" }: { groups: BoardCalGroup[]; idPrefix?: string }) {
  return (
    <div className="grid gap-4 md:grid-cols-2" data-testid="board-calibration">
      {groups.map((g) => (
        <div key={g.chance} className="table-scroll min-w-0 rounded-lg border border-line bg-surface p-2" data-chance={g.chance}>
          <table className="data-table" aria-describedby={`${idPrefix}-note`}>
            <caption className="mb-1 text-left font-display text-base font-bold tracking-wide uppercase">{CAPTION[g.chance]}</caption>
            <thead>
              <tr>
                <th scope="col">Estimate</th>
                <th scope="col" className="num">
                  Average estimate
                </th>
                <th scope="col" className="num">
                  {HAPPENED[g.chance]}
                </th>
              </tr>
            </thead>
            <tbody>
              {g.cells.map((c) => (
                <tr key={c.band} data-band={c.band}>
                  <th scope="row" className="tnum">
                    {c.bandName}
                  </th>
                  <td className="num">{pct(c.meanPred)}</td>
                  <td className="num">
                    <span className="font-semibold">{pct(c.observed)}</span>
                    <span className="block text-xs text-muted">
                      {fmtInt(c.hits)} of {fmtInt(c.n)} players ({fmtInt(c.boards)} {c.boards === 1 ? "board" : "boards"})
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}
