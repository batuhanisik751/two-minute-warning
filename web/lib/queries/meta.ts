import "server-only";
import { and, eq, max } from "drizzle-orm";
import { db } from "@/db/client";
import { radarList, siteMeta } from "@/db/schema";
import { cached } from "@/lib/cache";

export type SeasonWeek = { season: number; week: number };

export type SiteMeta = {
  /** site_meta.current_season / current_week: the latest regular-season week of the season
   *  whose Tuesday as-of had passed when the data was published. */
  current: SeasonWeek | null;
  currentSeason: number | null;
  /** The newest published list (any kind) and the newest live list. */
  latestList: SeasonWeek | null;
  latestLive: SeasonWeek | null;
  /** The Tuesday 14:00 UTC as-of the site's data reflects: the as-of of the current week's
   *  lists, else of the newest list (site_meta has no as-of key of its own), with its week. */
  asOf: { at: string; week: SeasonWeek } | null;
  /** site_meta.generated_at: when `twm publish` wrote the data. */
  generatedAt: string | null;
  /** site_meta.data_as_of: the estimated public time of the newest final score included. */
  newestScoreAt: string | null;
  dataThrough: SeasonWeek | null;
};

const int = (v: string | undefined): number | null =>
  v !== undefined && /^\d+$/.test(v) ? Number(v) : null;

const pair = (s: string | undefined, w: string | undefined): SeasonWeek | null => {
  const season = int(s);
  const week = int(w);
  return season !== null && week !== null ? { season, week } : null;
};

async function listAsOf(sw: SeasonWeek): Promise<string | null> {
  const rows = await db()
    .select({ at: max(radarList.asOf) })
    .from(radarList)
    .where(and(eq(radarList.season, sw.season), eq(radarList.week, sw.week)));
  const at = rows[0]?.at ?? null;
  return at ? new Date(at).toISOString() : null;
}

async function getSiteMetaRaw(): Promise<SiteMeta> {
  const rows = await db().select().from(siteMeta);
  const m = new Map(rows.map((r) => [r.key, r.value]));
  const current = pair(m.get("current_season"), m.get("current_week"));
  const latestList = pair(m.get("latest_list_season"), m.get("latest_list_week"));
  const latestLive = pair(m.get("latest_live_list_season"), m.get("latest_live_list_week"));
  let asOf: SiteMeta["asOf"] = null;
  for (const sw of [current, latestList]) {
    if (!sw) continue;
    const at = await listAsOf(sw);
    if (at) {
      asOf = { at, week: sw };
      break;
    }
  }
  const iso = (v: string | undefined) => (v && !Number.isNaN(Date.parse(v)) ? new Date(v).toISOString() : null);
  return {
    current,
    currentSeason: int(m.get("current_season")),
    latestList,
    latestLive,
    asOf,
    generatedAt: iso(m.get("generated_at")),
    newestScoreAt: iso(m.get("data_as_of")),
    dataThrough: pair(m.get("data_through_season"), m.get("data_through_week")),
  };
}

export const getSiteMeta = cached("meta.getSiteMeta", getSiteMetaRaw);
