// The footer of every page: the disclaimer of PROJECT_SPEC 16 (verbatim) and the data
// credits. Each credit was checked against the code that uses the data:
// - nflverse: every dataset (src/twm/sources/nflverse.py), loaded with nflreadpy.
// - ffverse's ffopportunity: expected fantasy points (xFP, FPOE; fact_opportunity_week),
//   downloaded from github.com/ffverse/ffopportunity by nflreadpy's load_ff_opportunity.
// - DynastyProcess: the player id map (load_ff_playerids) and the FantasyPros rankings
//   archive (load_ff_rankings), both from github.com/dynastyprocess/data.
// - FantasyPros: the expert rankings in that archive (the candidate pool's preseason list
//   from 2020, the experts' baseline in the track record, the "ranked before the season"
//   reasons).
// - Pro Football Reference: the source of nflverse's snap counts (snap share).
// No FTN credit: ftn_charting and participation are downloaded but no published number
// uses them.
import Link from "next/link";
import { DISCLAIMER } from "@/lib/site";


export default function SiteFooter() {
  return (
    <footer className="mt-16 border-t border-line bg-surface">
      <div className="mx-auto max-w-5xl space-y-3 px-4 py-6 text-sm text-muted">
        <p data-testid="disclaimer" className="font-medium text-fg">
          {DISCLAIMER}
        </p>
        <p data-testid="attribution">
          Data from <a href="https://nflverse.nflverse.com/">nflverse</a> (play-by-play, player
          stats, snap counts, rosters, schedules). Expected fantasy points from ffverse&apos;s{" "}
          <a href="https://github.com/ffverse/ffopportunity">ffopportunity</a>. Player id map
          and the <a href="https://www.fantasypros.com/">FantasyPros</a> expert rankings archive
          from <a href="https://github.com/dynastyprocess/data">DynastyProcess</a>. Snap counts
          originate from <a href="https://www.pro-football-reference.com/">Pro Football Reference</a>.
        </p>
        <p>
          No NFL or team logos are used. Code under the MIT license on{" "}
          <a href="https://github.com/batuhanisik751/two-minute-warning">GitHub</a>. How it
          works: <Link href="/methodology">Methodology</Link>.
        </p>
      </div>
    </footer>
  );
}
