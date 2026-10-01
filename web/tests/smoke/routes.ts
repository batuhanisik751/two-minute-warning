// Which routes the smoke and accessibility tests visit. With the seed (tests/seed.ts) they are
// fixed; against a real publish they are discovered from the pages themselves.
import { POSITIONS } from "../../lib/method";
import { SEED } from "../seed";
import { DECISIONS_SEED } from "../seed-decisions";
import { MODULES_SEED } from "../seed-modules";
import { DATA, fetchPage } from "./dom";

export type Route = {
  path: string;
  kind: "home" | "waivers-live" | "waivers-backtest" | "waivers-flex" | "waivers-stream" | "waivers" | "player" | "regression" | "methodology" | "decisions" | "coach" | "track-record";
};

export type RouteSet = {
  routes: Route[];
  /** routes that must answer 404 */
  missing: string[];
  /** explanations of what could not be covered (real data only) */
  notes: string[];
};

/** Must answer 404. An unmatched path renders the not-found page on the server. A player id
 *  the database lacks makes the page call notFound(): the status is 404 too, but Next 16
 *  answers with its error shell (<html id="__next_error__">, empty body) and the browser
 *  renders the same not-found page from the payload once the scripts run (checked in a real
 *  browser). */
export const MISSING = ["/player/00-0000000", "/player/not-a-player-id", "/coach/no-such-coach", "/coach/Not_A_Coach", "/no-such-page"];
export const SERVER_RENDERED_404 = new Set(["/no-such-page"]);

function seedRoutes(): RouteSet {
  const live = SEED.liveWeek;
  const bt = SEED.backtestWeeks[1];
  const sb = MODULES_SEED.streamBacktest;
  const rb = MODULES_SEED.rwBacktest[1];
  return {
    routes: [
      { path: "/", kind: "home" },
      { path: "/waivers", kind: "waivers" },
      ...POSITIONS.map((p) => ({ path: `/waivers?season=${live.season}&week=${live.week}&pos=${p}&kind=live`, kind: "waivers-live" as const })),
      ...POSITIONS.map((p) => ({ path: `/waivers?season=${bt.season}&week=${bt.week}&pos=${p}`, kind: "waivers-backtest" as const })),
      { path: `/waivers?season=${SEED.reconWeek.season}&week=${SEED.reconWeek.week}&pos=RB&kind=backtest`, kind: "waivers-backtest" },
      // FLEX: the live week (chances), a walk-forward week (no chances: model order), the
      // reconstructed 2026 week
      { path: `/waivers?season=${live.season}&week=${live.week}&pos=FLEX&kind=live`, kind: "waivers-flex" },
      { path: `/waivers?season=${bt.season}&week=${bt.week}&pos=FLEX`, kind: "waivers-flex" },
      { path: `/waivers?season=${SEED.reconWeek.season}&week=${SEED.reconWeek.week}&pos=FLEX&kind=backtest`, kind: "waivers-flex" },
      // K and D/ST: the live week, a reconstructed week with chances, the first season without
      ...(["K", "DST"] as const).map((p) => ({ path: `/waivers?season=${live.season}&week=${live.week}&pos=${p}&kind=live`, kind: "waivers-stream" as const })),
      ...(["K", "DST"] as const).map((p) => ({ path: `/waivers?season=${sb[0].season}&week=${sb[0].week}&pos=${p}`, kind: "waivers-stream" as const })),
      { path: `/waivers?season=${sb[1].season}&week=${sb[1].week}&pos=K`, kind: "waivers-stream" },
      { path: `/player/${SEED.featured}`, kind: "player" },
      { path: `/player/${SEED.featured}?season=2025`, kind: "player" },
      { path: `/player/${SEED.featured}?season=2025&gt=off`, kind: "player" },
      { path: "/regression", kind: "regression" },
      { path: "/regression?gt=off", kind: "regression" },
      { path: `/regression?season=${rb.season}&week=${rb.week}`, kind: "regression" },
      { path: `/regression?season=${rb.season}&week=${rb.week}&gt=off`, kind: "regression" },
      { path: "/methodology", kind: "methodology" },
      { path: "/track-record", kind: "track-record" },
      // the Decision Report Card: the season being graded, the complete season (long lists that
      // fold), the oldest; a coach with every kind of row, one with clock cases, the long name
      { path: "/decisions", kind: "decisions" },
      { path: `/decisions?season=${DECISIONS_SEED.past}`, kind: "decisions" },
      { path: "/decisions?season=2024", kind: "decisions" },
      ...[DECISIONS_SEED.featured, "avery-o-hollis", DECISIONS_SEED.coaches[3].id].map((id) => ({ path: `/coach/${id}`, kind: "coach" as const })),
    ],
    missing: MISSING,
    notes: [],
  };
}

async function realRoutes(): Promise<RouteSet> {
  const notes: string[] = [];
  const first = await fetchPage("/waivers");
  const doc = first.doc;
  const seasons = Array.from(doc.querySelectorAll<HTMLOptionElement>("select[name=season] option")).map((o) => o.value);
  const weekOpts = Array.from(doc.querySelectorAll<HTMLOptionElement>("select[name=week] option"));
  const chosenSeason = doc.querySelector<HTMLOptionElement>("select[name=season] option[selected]")?.value ?? seasons[0];
  const routes: Route[] = [
    { path: "/", kind: "home" },
    { path: "/waivers", kind: "waivers" },
  ];
  const liveWeek = weekOpts.find((o) => /live/.test(o.textContent ?? ""));
  if (liveWeek && chosenSeason) {
    for (const p of POSITIONS) routes.push({ path: `/waivers?season=${chosenSeason}&week=${liveWeek.value}&pos=${p}&kind=live`, kind: "waivers-live" });
    routes.push({ path: `/waivers?season=${chosenSeason}&week=${liveWeek.value}&pos=FLEX&kind=live`, kind: "waivers-flex" });
  } else {
    notes.push(`no live list in the newest season (${chosenSeason ?? "none"}): the live-week pages were not checked`);
  }
  const reconWeek = weekOpts.find((o) => /reconstructed/.test(o.textContent ?? ""));
  if (reconWeek && chosenSeason) {
    for (const p of POSITIONS) routes.push({ path: `/waivers?season=${chosenSeason}&week=${reconWeek.value}&pos=${p}&kind=backtest`, kind: "waivers-backtest" });
  }
  const oldest = seasons[seasons.length - 1];
  if (oldest && oldest !== chosenSeason) {
    for (const p of POSITIONS) routes.push({ path: `/waivers?season=${oldest}&pos=${p}`, kind: "waivers-backtest" });
    routes.push({ path: `/waivers?season=${oldest}&pos=FLEX`, kind: "waivers-flex" });
  }
  const player = doc.querySelector<HTMLAnchorElement>("a[href^='/player/']")?.getAttribute("href");
  if (player) routes.push({ path: player, kind: "player" });
  else notes.push("no player link on /waivers: no player page was checked");
  // K and D/ST: their tabs (from the data), the newest list and the oldest season's
  const tabs = Array.from(doc.querySelectorAll<HTMLAnchorElement>("nav[aria-label=Position] a")).map((a) => a.textContent ?? "");
  for (const [tab, pos] of [["K", "K"], ["D/ST", "DST"]]) {
    if (!tabs.includes(tab)) {
      notes.push(`no ${tab} tab: the ${tab} list was not checked`);
      continue;
    }
    routes.push({ path: `/waivers?pos=${pos}`, kind: "waivers-stream" });
    const sd = (await fetchPage(`/waivers?pos=${pos}`)).doc;
    const ss = Array.from(sd.querySelectorAll<HTMLOptionElement>("select[name=season] option")).map((o) => o.value);
    if (ss.length > 1) routes.push({ path: `/waivers?season=${ss[ss.length - 1]}&pos=${pos}`, kind: "waivers-stream" });
  }
  routes.push({ path: "/regression", kind: "regression" }, { path: "/regression?gt=off", kind: "regression" });
  const rd = (await fetchPage("/regression")).doc;
  const rs = Array.from(rd.querySelectorAll<HTMLOptionElement>("select[name=season] option")).map((o) => o.value);
  if (rs.length > 1) routes.push({ path: `/regression?season=${rs[rs.length - 1]}`, kind: "regression" });
  if (player) routes.push({ path: `${player}${player.includes("?") ? "&" : "?"}gt=off`, kind: "player" });
  routes.push({ path: "/methodology", kind: "methodology" }, { path: "/track-record", kind: "track-record" });
  // the Decision Report Card: the newest season, the oldest, the first coaches it links
  const dd = (await fetchPage("/decisions")).doc;
  const ds = Array.from(dd.querySelectorAll<HTMLOptionElement>("form[action='/decisions'] select[name=season] option")).map((o) => o.value);
  if (ds.length) {
    routes.push({ path: "/decisions", kind: "decisions" });
    const prev = ds[1];
    if (prev) routes.push({ path: `/decisions?season=${prev}`, kind: "decisions" });
    if (ds.length > 2) routes.push({ path: `/decisions?season=${ds[ds.length - 1]}`, kind: "decisions" });
    const coaches = [...new Set(Array.from(dd.querySelectorAll<HTMLAnchorElement>("[data-testid=leaderboard] a[href^='/coach/']")).map((a) => a.getAttribute("href")!))];
    for (const c of coaches.slice(0, 2)) routes.push({ path: c, kind: "coach" });
    if (!coaches.length) notes.push("no coach link on /decisions: no coach page was checked");
  } else {
    notes.push("no Decision Report Card season: /decisions and the coach pages were not checked");
  }
  return { routes, missing: MISSING, notes };
}

let cachedSet: Promise<RouteSet> | null = null;
export function routeSet(): Promise<RouteSet> {
  cachedSet ??= DATA === "real" ? realRoutes() : Promise.resolve(seedRoutes());
  return cachedSet;
}
