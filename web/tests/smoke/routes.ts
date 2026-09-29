// Which routes the smoke and accessibility tests visit. With the seed (tests/seed.ts) they are
// fixed; against a real publish they are discovered from the pages themselves.
import { POSITIONS } from "../../lib/method";
import { SEED } from "../seed";
import { DATA, fetchPage } from "./dom";

export type Route = {
  path: string;
  kind: "home" | "waivers-live" | "waivers-backtest" | "waivers-flex" | "waivers" | "player" | "regression" | "methodology";
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
export const MISSING = ["/player/00-0000000", "/player/not-a-player-id", "/no-such-page"];
export const SERVER_RENDERED_404 = new Set(["/no-such-page"]);

function seedRoutes(): RouteSet {
  const live = SEED.liveWeek;
  const bt = SEED.backtestWeeks[1];
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
      { path: `/player/${SEED.featured}`, kind: "player" },
      { path: `/player/${SEED.featured}?season=2025`, kind: "player" },
      { path: "/regression", kind: "regression" },
      { path: "/methodology", kind: "methodology" },
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
  routes.push({ path: "/regression", kind: "regression" }, { path: "/methodology", kind: "methodology" });
  return { routes, missing: MISSING, notes };
}

let cachedSet: Promise<RouteSet> | null = null;
export function routeSet(): Promise<RouteSet> {
  cachedSet ??= DATA === "real" ? realRoutes() : Promise.resolve(seedRoutes());
  return cachedSet;
}
