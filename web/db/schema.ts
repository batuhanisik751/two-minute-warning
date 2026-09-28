// The public database's schema (PROJECT_SPEC 9 "Postgres", step E2).
//
// Drizzle owns the DDL: change this file, then `npm run db:generate` writes a new SQL
// migration into web/drizzle/ (commit both). `npm run db:check` fails when the two disagree.
// The Python publisher (`twm publish`, src/twm/publish/) writes the rows; its column lists
// in src/twm/publish/tables.py must match this file (a postgres-marked test checks them
// against a migrated database).
//
// Only aggregates and public data are published: no league data (spec rule 9), no logos
// (rule 10), no player ids other than gsis_id.
import { sql } from "drizzle-orm";
import {
  boolean,
  check,
  doublePrecision,
  foreignKey,
  index,
  integer,
  jsonb,
  pgTable,
  primaryKey,
  text,
  timestamp,
  uniqueIndex,
} from "drizzle-orm/pg-core";

const tstz = (name: string) => timestamp(name, { withTimezone: true });

// ---------------------------------------------------------------------------------------
// Bookkeeping
// ---------------------------------------------------------------------------------------

/** Small facts about the published data: data_as_of, current season/week, generated_at,
 * code version. One row per key; replaced on every publish. */
export const siteMeta = pgTable("site_meta", {
  key: text("key").primaryKey(),
  value: text("value").notNull(),
});

/** One row per pipeline run (append-only: rows are never changed or deleted). */
export const pipelineRuns = pgTable(
  "pipeline_runs",
  {
    runId: text("run_id").primaryKey(),
    startedAt: tstz("started_at").notNull(),
    finishedAt: tstz("finished_at").notNull(),
    status: text("status").notNull(),
    stage: text("stage").notNull(),
    gitSha: text("git_sha"),
    dataAsOf: tstz("data_as_of"),
    notes: jsonb("notes").notNull().default(sql`'{}'::jsonb`),
  },
  (t) => [
    check("pipeline_runs_status_check", sql`${t.status} in ('success', 'failed')`),
    index("pipeline_runs_started_at_idx").on(t.startedAt),
  ],
);

/** The models behind published rows (upserted; never deleted, because frozen live lists may
 * reference a version the current run no longer has). */
export const modelVersions = pgTable("model_versions", {
  modelVersion: text("model_version").primaryKey(),
  module: text("module").notNull(),
  model: text("model").notNull(),
  label: text("label").notNull(),
  trainingSeasons: integer("training_seasons").array().notNull(),
  testSeason: integer("test_season"),
  featureList: text("feature_list").array().notNull(),
  params: jsonb("params").notNull(),
  createdAt: tstz("created_at").notNull(),
});

// ---------------------------------------------------------------------------------------
// Dimensions (upserted; never deleted)
// ---------------------------------------------------------------------------------------

/** The current franchises: names and colors only (no logos: spec rule 10). */
export const dimTeam = pgTable("dim_team", {
  teamAbbr: text("team_abbr").primaryKey(),
  teamName: text("team_name").notNull(),
  teamNick: text("team_nick").notNull(),
  conference: text("conference").notNull(),
  division: text("division").notNull(),
  color: text("color"),
  color2: text("color2"),
});

/** Players referenced by published rows. Display only: `position` is today's listed
 * position (the site says "listed position"), `team` the latest team nflverse lists. */
export const dimPlayer = pgTable("dim_player", {
  gsisId: text("gsis_id").primaryKey(),
  displayName: text("display_name").notNull(),
  position: text("position"),
  team: text("team"),
  draftYear: integer("draft_year"),
  draftRound: integer("draft_round"),
  draftPick: integer("draft_pick"),
  rookieSeason: integer("rookie_season"),
});

// ---------------------------------------------------------------------------------------
// Waiver Radar
// ---------------------------------------------------------------------------------------

/** One row per published weekly list (season, week, position). kind 'live' = made in real
 * time (frozen once published); 'backtest' = reconstructed after the fact. */
export const radarList = pgTable(
  "radar_list",
  {
    season: integer("season").notNull(),
    week: integer("week").notNull(),
    position: text("position").notNull(),
    kind: text("kind").notNull(),
    asOf: tstz("as_of").notNull(),
    modelVersion: text("model_version")
      .notNull()
      .references(() => modelVersions.modelVersion),
    generatedAt: tstz("generated_at").notNull(),
    incomplete: boolean("incomplete").notNull().default(false),
    nPool: integer("n_pool").notNull(),
    note: text("note"),
  },
  (t) => [
    primaryKey({ columns: [t.season, t.week, t.position, t.kind] }),
    check("radar_list_kind_check", sql`${t.kind} in ('live', 'backtest')`),
    check("radar_list_position_check", sql`${t.position} in ('QB', 'RB', 'WR', 'TE')`),
  ],
);

/** The top 25 of each list. chance / chance_low / chance_high: how often similar players
 * hit in the backtest, with its 90% range (NULL for walk-forward backtest lists, which never
 * had one); model_prob: the model's calibrated probability; reasons: up to 3 plain-English
 * sentences (empty for backtest lists: reasons were never generated for them). */
export const radarPick = pgTable(
  "radar_pick",
  {
    season: integer("season").notNull(),
    week: integer("week").notNull(),
    position: text("position").notNull(),
    kind: text("kind").notNull(),
    rank: integer("rank").notNull(),
    gsisId: text("gsis_id")
      .notNull()
      .references(() => dimPlayer.gsisId),
    team: text("team")
      .notNull()
      .references(() => dimTeam.teamAbbr),
    chance: doublePrecision("chance"),
    chanceLow: doublePrecision("chance_low"),
    chanceHigh: doublePrecision("chance_high"),
    modelProb: doublePrecision("model_prob").notNull(),
    tier: text("tier"),
    reasons: jsonb("reasons").notNull().default(sql`'[]'::jsonb`),
  },
  (t) => [
    primaryKey({ columns: [t.season, t.week, t.position, t.kind, t.rank] }),
    foreignKey({
      name: "radar_pick_list_fk",
      columns: [t.season, t.week, t.position, t.kind],
      foreignColumns: [radarList.season, radarList.week, radarList.position, radarList.kind],
    }),
    uniqueIndex("radar_pick_player_uq").on(t.season, t.week, t.position, t.kind, t.gsisId),
    index("radar_pick_gsis_id_idx").on(t.gsisId),
    check("radar_pick_rank_check", sql`${t.rank} >= 1`),
    check("radar_pick_model_prob_check", sql`${t.modelProb} >= 0 and ${t.modelProb} <= 1`),
    check(
      "radar_pick_chance_check",
      sql`${t.chance} is null or (${t.chanceLow} <= ${t.chance} and ${t.chance} <= ${t.chanceHigh} and ${t.chanceLow} >= 0 and ${t.chanceHigh} <= 1)`,
    ),
    check(
      "radar_pick_tier_check",
      sql`${t.tier} is null or ${t.tier} in ('must-add', 'speculative', 'watch')`,
    ),
  ],
);

/** What happened after each list: the labels and the player's weekly finishes in the window
 * (ranks at his position; NULL = did not play). Replaced on every publish. */
export const radarOutcome = pgTable(
  "radar_outcome",
  {
    season: integer("season").notNull(),
    week: integer("week").notNull(),
    gsisId: text("gsis_id")
      .notNull()
      .references(() => dimPlayer.gsisId),
    yHit: boolean("y_hit"),
    ySustained: boolean("y_sustained"),
    labelStatus: text("label_status").notNull(),
    windowWeeks: integer("window_weeks").array(),
    windowRanks: integer("window_ranks").array(),
    windowPoints: doublePrecision("window_points").array(),
  },
  (t) => [
    primaryKey({ columns: [t.season, t.week, t.gsisId] }),
    index("radar_outcome_gsis_id_idx").on(t.gsisId),
  ],
);

// ---------------------------------------------------------------------------------------
// Track record (the committed reports, published as they are)
// ---------------------------------------------------------------------------------------

/** Every row of reports/<module>/evaluation.csv. scope_value is the CSV's `key` ('' when
 * empty); excl_rostered = the CSV's subset 'without_rostered'. */
export const trackRecord = pgTable(
  "track_record",
  {
    module: text("module").notNull(),
    model: text("model").notNull(),
    label: text("label").notNull(),
    metric: text("metric").notNull(),
    scope: text("scope").notNull(),
    scopeValue: text("scope_value").notNull().default(""),
    seasonFrom: integer("season_from").notNull(),
    seasonTo: integer("season_to").notNull(),
    exclRostered: boolean("excl_rostered").notNull(),
    value: doublePrecision("value"),
    low: doublePrecision("low"),
    high: doublePrecision("high"),
    nLists: integer("n_lists"),
    nPositives: integer("n_positives"),
    nRows: integer("n_rows"),
    nTopHits: integer("n_top_hits"),
    nTop: integer("n_top"),
  },
  (t) => [
    primaryKey({
      // an explicit name: the generated one is longer than Postgres's 63-character limit
      name: "track_record_pk",
      columns: [
        t.module,
        t.label,
        t.exclRostered,
        t.model,
        t.scope,
        t.scopeValue,
        t.seasonFrom,
        t.seasonTo,
        t.metric,
      ],
    }),
  ],
);

/** How each suggested priority did in the backtest (top 25 of every list). week 0 = every
 * week; week N = the week-N lists only. chance_low/high: the "similar players hit" range
 * that defines the tier; prob_low/high: the model probabilities it maps to. */
export const tierStats = pgTable(
  "tier_stats",
  {
    module: text("module").notNull(),
    model: text("model").notNull(),
    label: text("label").notNull(),
    week: integer("week").notNull(),
    tier: text("tier").notNull(),
    seasonFrom: integer("season_from").notNull(),
    seasonTo: integer("season_to").notNull(),
    chanceLow: doublePrecision("chance_low").notNull(),
    chanceHigh: doublePrecision("chance_high").notNull(),
    probLow: doublePrecision("prob_low"),
    probHigh: doublePrecision("prob_high"),
    lists: integer("lists").notNull(),
    players: integer("players").notNull(),
    hits: integer("hits").notNull(),
    hitRate: doublePrecision("hit_rate"),
    perList: doublePrecision("per_list"),
  },
  (t) => [primaryKey({ columns: [t.module, t.model, t.label, t.week, t.tier] })],
);

// ---------------------------------------------------------------------------------------
// Player pages and tooltips
// ---------------------------------------------------------------------------------------

/** One row per player-week (regular season, QB/RB/WR/TE, from the first snap-count season):
 * fantasy points with the config scoring, shares, expected points (xFP) and the difference
 * (FPOE). Shares are 0-1. */
export const playerWeekSummary = pgTable(
  "player_week_summary",
  {
    gsisId: text("gsis_id")
      .notNull()
      .references(() => dimPlayer.gsisId),
    season: integer("season").notNull(),
    week: integer("week").notNull(),
    team: text("team")
      .notNull()
      .references(() => dimTeam.teamAbbr),
    position: text("position").notNull(),
    fantasyPoints: doublePrecision("fantasy_points").notNull(),
    snapShare: doublePrecision("snap_share"),
    targetShare: doublePrecision("target_share"),
    carryShare: doublePrecision("carry_share"),
    xfp: doublePrecision("xfp"),
    fpoe: doublePrecision("fpoe"),
  },
  (t) => [
    primaryKey({ columns: [t.gsisId, t.season, t.week] }),
    index("player_week_summary_week_idx").on(t.season, t.week, t.position),
  ],
);

/** The feature/metric registry (src/twm/registry.py) for tooltips and the glossary. */
export const glossary = pgTable("glossary", {
  name: text("name").primaryKey(),
  title: text("title").notNull(),
  kind: text("kind").notNull(),
  unit: text("unit").notNull(),
  formula: text("formula").notNull(),
  explanation: text("explanation").notNull(),
  verified: text("verified"),
  modules: text("modules").array().notNull(),
  modelOutput: boolean("model_output").notNull(),
});
