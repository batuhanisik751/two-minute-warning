import Link from "next/link";
import { FoldTable } from "@/components/Fold";
import RatingCell from "@/components/playoff-planner/RatingCell";
import { Note } from "@/components/ui";
import { gridFor, plannerHref, PP_POSITIONS, sortGrid, type PPRow, type SortKey } from "@/lib/playoff-planner";
import { positionShort } from "@/lib/positions";

/** A sortable column header: its sort is a link (a term, when given, sits outside the link: a
 *  button cannot live inside a link). */
function SortHeader({ label, term, sortKey, pos, sort, className = "" }: { label: string; term?: React.ReactNode; sortKey: SortKey; pos: string; sort: SortKey; className?: string }) {
  const active = sort === sortKey;
  return (
    <th scope="col" className={className} aria-sort={active ? (sortKey === "team" ? "ascending" : "descending") : undefined}>
      {term ? <span className="mr-1">{term}</span> : null}
      <Link href={plannerHref({ pos, sort: sortKey })} className="inline-flex min-h-11 items-center" data-sort={sortKey}>
        {label}
        {active ? <span aria-hidden="true">{sortKey === "team" ? " ▲" : " ▼"}</span> : null}
      </Link>
    </th>
  );
}

/** The playoff grid of one position: tabs for the six positions, then every team with its
 *  opponent and matchup rating in each playoff week and the total (sortable by links: no
 *  JavaScript needed, shareable). An unrated position shows the schedule and why. */
export default function Grid({ rows, weeks, pos, sort, unrated }: { rows: PPRow[]; weeks: number[]; pos: string; sort: SortKey; unrated: string | null }) {
  const g = gridFor(rows, pos, weeks);
  const teams = sortGrid(g.teams, g.rated ? sort : "team");
  return (
    <div data-testid="pp-grid" data-pos={pos} data-rated={g.rated ? "yes" : "no"}>
      <nav aria-label="Position" className="mb-4">
        <ul className="flex flex-wrap gap-1.5 sm:gap-2">
          {PP_POSITIONS.map((p) => (
            <li key={p}>
              <Link href={plannerHref({ pos: p, sort })} aria-current={p === pos ? "page" : undefined} data-pos={p} className="pos-tab inline-flex min-h-11 min-w-14 items-center justify-center rounded-md px-3 no-underline">
                {positionShort(p)}
              </Link>
            </li>
          ))}
        </ul>
      </nav>
      {!g.rated ? (
        <div className="mb-4" data-testid="pp-unrated">
          <Note>{unrated}</Note>
        </div>
      ) : null}
      <FoldTable
        label={`${positionShort(pos)} playoff grid`}
        rows={teams.map((t) => (
          <tr key={t.team} data-team={t.team} data-row="">
            <th scope="row" data-cell="team">
              {t.team}
            </th>
            {t.cells.map((c) => (
              <RatingCell key={c.week} cell={c} rated={g.rated} />
            ))}
            {g.rated ? (
              <td className="num" data-testid="pp-total" data-cell="total">
                {t.total === null ? "–" : `${t.total.toFixed(2)}${t.games < weeks.length ? ` (${t.games} games)` : ""}`}
              </td>
            ) : null}
          </tr>
        ))}
        table={(body) => (
          <div className="table-scroll">
            <table className="data-table">
              <caption className="sr-only">
                {positionShort(pos)} matchups in fantasy playoff weeks {weeks.join(", ")}: each team&apos;s opponent{g.rated ? " and matchup rating" : ""}
              </caption>
              <thead>
                <tr>
                  <SortHeader label="Team" sortKey="team" pos={pos} sort={sort} />
                  {weeks.map((w) => (g.rated ? <SortHeader key={w} label={`Week ${w}`} sortKey={`w${w}`} pos={pos} sort={sort} /> : <th key={w} scope="col">{`Week ${w}`}</th>))}
                  {g.rated ? (
                    <SortHeader term={`${weeks.length}-week total`} label="sort by total" sortKey="total" pos={pos} sort={sort} className="num" />
                  ) : null}
                </tr>
              </thead>
              {body}
            </table>
          </div>
        )}
      />
    </div>
  );
}
