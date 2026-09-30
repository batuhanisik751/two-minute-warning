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
// K and D/ST streamer (step P2): the same live/backtest rules as the Waiver Radar lists
// ---------------------------------------------------------------------------------------

/** One row per published streamer list (season, week, position K or DST). kind 'live' =
 * made in real time (frozen once published); 'backtest' = reconstructed after the fact. */
export const streamList = pgTable(
  "stream_list",
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
    check("stream_list_kind_check", sql`${t.kind} in ('live', 'backtest')`),
    check("stream_list_position_check", sql`${t.position} in ('K', 'DST')`),
  ],
);

/** Every pick of each streamer list (lists are short: the whole pool). entity_id is the
 * kicker's gsis_id or "DST-<team>" (no FK to dim_player); team / next_opponent are team codes;
 * home: next week's game is at home (NULL while unknown). chance / chance_low / chance_high:
 * how often the method's similar past picks started (NULL for the first backtest season);
 * model_prob: the K model's calibrated probability (NULL for the D/ST rule, which has none). */
export const streamPick = pgTable(
  "stream_pick",
  {
    season: integer("season").notNull(),
    week: integer("week").notNull(),
    position: text("position").notNull(),
    kind: text("kind").notNull(),
    rank: integer("rank").notNull(),
    entityId: text("entity_id").notNull(),
    entityType: text("entity_type").notNull(),
    displayName: text("display_name").notNull(),
    team: text("team")
      .notNull()
      .references(() => dimTeam.teamAbbr),
    nextOpponent: text("next_opponent").references(() => dimTeam.teamAbbr),
    home: boolean("home"),
    chance: doublePrecision("chance"),
    chanceLow: doublePrecision("chance_low"),
    chanceHigh: doublePrecision("chance_high"),
    modelProb: doublePrecision("model_prob"),
    tier: text("tier"),
    reasons: jsonb("reasons").notNull().default(sql`'[]'::jsonb`),
  },
  (t) => [
    primaryKey({ columns: [t.season, t.week, t.position, t.kind, t.rank] }),
    foreignKey({
      name: "stream_pick_list_fk",
      columns: [t.season, t.week, t.position, t.kind],
      foreignColumns: [streamList.season, streamList.week, streamList.position, streamList.kind],
    }),
    uniqueIndex("stream_pick_entity_uq").on(t.season, t.week, t.position, t.kind, t.entityId),
    index("stream_pick_entity_id_idx").on(t.entityId),
    check("stream_pick_rank_check", sql`${t.rank} >= 1`),
    check(
      "stream_pick_entity_check",
      sql`(${t.position} = 'K' and ${t.entityType} = 'kicker' and ${t.entityId} ~ '^(00-[0-9]{7}|[A-Z]{3}[0-9]{6})$') or (${t.position} = 'DST' and ${t.entityType} = 'team_defense' and ${t.entityId} = 'DST-' || ${t.team})`,
    ),
    check(
      "stream_pick_model_prob_check",
      sql`${t.modelProb} is null or (${t.modelProb} >= 0 and ${t.modelProb} <= 1)`,
    ),
    check(
      "stream_pick_chance_check",
      sql`${t.chance} is null or (${t.chanceLow} <= ${t.chance} and ${t.chance} <= ${t.chanceHigh} and ${t.chanceLow} >= 0 and ${t.chanceHigh} <= 1)`,
    ),
    check(
      "stream_pick_tier_check",
      sql`${t.tier} is null or ${t.tier} in ('must-add', 'speculative', 'watch')`,
    ),
  ],
);

/** What happened the week after each streamer list: y_start = a top-12 finish at the position
 * (the backtest's label), points_next_week = the fantasy points scored (NULL while pending or
 * on a bye). Replaced on every publish. */
export const streamOutcome = pgTable(
  "stream_outcome",
  {
    season: integer("season").notNull(),
    week: integer("week").notNull(),
    entityId: text("entity_id").notNull(),
    yStart: boolean("y_start"),
    labelStatus: text("label_status").notNull(),
    pointsNextWeek: doublePrecision("points_next_week"),
  },
  (t) => [
    primaryKey({ columns: [t.season, t.week, t.entityId] }),
    index("stream_outcome_entity_id_idx").on(t.entityId),
  ],
);

// ---------------------------------------------------------------------------------------
// Regression Watch (step P2): the same live/backtest rules as the other lists
// ---------------------------------------------------------------------------------------

/** One row per published Regression Watch list (season, week). params_version: the frozen
 * parameters that made it (for a backtest list, the ones its season's walk-forward chose). */
export const regressionList = pgTable(
  "regression_list",
  {
    season: integer("season").notNull(),
    week: integer("week").notNull(),
    kind: text("kind").notNull(),
    asOf: tstz("as_of").notNull(),
    paramsVersion: text("params_version")
      .notNull()
      .references(() => modelVersions.modelVersion),
    generatedAt: tstz("generated_at").notNull(),
    incomplete: boolean("incomplete").notNull().default(false),
    nUniverse: integer("n_universe").notNull(),
    note: text("note"),
  },
  (t) => [
    primaryKey({ columns: [t.season, t.week, t.kind] }),
    check("regression_list_kind_check", sql`${t.kind} in ('live', 'backtest')`),
  ],
);

/** Every universe player of each list: season-to-date points, expected points (xFP) and the
 * difference (FPOE) per game, with and without garbage time (_ng), the rest-of-season
 * projection and its shrinkage, and the tag (the first of sell_high, buy_low, legit he has;
 * `tags` lists all of them; tag_reason says why in plain English). */
export const regressionRow = pgTable(
  "regression_row",
  {
    season: integer("season").notNull(),
    week: integer("week").notNull(),
    kind: text("kind").notNull(),
    gsisId: text("gsis_id")
      .notNull()
      .references(() => dimPlayer.gsisId),
    position: text("position").notNull(),
    team: text("team")
      .notNull()
      .references(() => dimTeam.teamAbbr),
    games: integer("games").notNull(),
    ppg: doublePrecision("ppg").notNull(),
    ppgNg: doublePrecision("ppg_ng"),
    xfpPg: doublePrecision("xfp_pg"),
    xfpPgNg: doublePrecision("xfp_pg_ng"),
    fpoePg: doublePrecision("fpoe_pg"),
    fpoePgNg: doublePrecision("fpoe_pg_ng"),
    projection: doublePrecision("projection").notNull(),
    shrinkage: doublePrecision("shrinkage"),
    tag: text("tag"),
    tags: text("tags").array().notNull().default(sql`'{}'::text[]`),
    tagReason: text("tag_reason"),
  },
  (t) => [
    primaryKey({ columns: [t.season, t.week, t.kind, t.gsisId] }),
    foreignKey({
      name: "regression_row_list_fk",
      columns: [t.season, t.week, t.kind],
      foreignColumns: [regressionList.season, regressionList.week, regressionList.kind],
    }),
    index("regression_row_gsis_id_idx").on(t.gsisId),
    check("regression_row_position_check", sql`${t.position} in ('QB', 'RB', 'WR', 'TE')`),
    check(
      "regression_row_tag_check",
      sql`${t.tag} is null or ${t.tag} in ('sell_high', 'buy_low', 'legit')`,
    ),
    check(
      "regression_row_tags_check",
      sql`${t.tags} <@ array['sell_high', 'buy_low', 'legit']::text[] and (${t.tag} is null) = (cardinality(${t.tags}) = 0)`,
    ),
    check("regression_row_games_check", sql`${t.games} >= 1`),
  ],
);

/** What each listed player did over the rest of the season: points per game and games played
 * after the list's as-of ('final' once the regular season is over; NULL while pending).
 * Replaced on every publish. */
export const regressionOutcome = pgTable(
  "regression_outcome",
  {
    season: integer("season").notNull(),
    week: integer("week").notNull(),
    gsisId: text("gsis_id")
      .notNull()
      .references(() => dimPlayer.gsisId),
    rosPpg: doublePrecision("ros_ppg"),
    rosGames: integer("ros_games"),
    labelStatus: text("label_status").notNull(),
  },
  (t) => [
    primaryKey({ columns: [t.season, t.week, t.gsisId] }),
    index("regression_outcome_gsis_id_idx").on(t.gsisId),
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

/** Every row of reports/streamer/backtest.csv, in its order (line = the CSV's data row, from
 * 1); the columns keep the CSV's names. */
export const streamTrackRecord = pgTable("stream_track_record", {
  line: integer("line").primaryKey(),
  position: text("position").notNull(),
  method: text("method").notNull(),
  trainOn: text("train_on").notNull(),
  scope: text("scope").notNull(),
  seasons: text("seasons").notNull(),
  key: text("key"),
  metric: text("metric").notNull(),
  value: doublePrecision("value"),
  lo: doublePrecision("lo"),
  hi: doublePrecision("hi"),
  shareAboveZero: doublePrecision("share_above_zero"),
  nGroups: integer("n_groups"),
  nRows: integer("n_rows"),
  nPos: integer("n_pos"),
});

/** Every row of reports/regression_watch/backtest.csv, in its order (line = the CSV's data
 * row, from 1). section = the CSV's `table` (value, difference, tag, threshold, choice,
 * left_out) and row_group its `group` (both renamed: SQL keywords). */
export const regressionTrackRecord = pgTable("regression_track_record", {
  line: integer("line").primaryKey(),
  section: text("section").notNull(),
  weeks: text("weeks"),
  position: text("position"),
  method: text("method"),
  metric: text("metric"),
  rowGroup: text("row_group"),
  season: integer("season"),
  value: doublePrecision("value").notNull(),
  lo: doublePrecision("lo"),
  hi: doublePrecision("hi"),
  n: integer("n").notNull(),
  nSeasons: integer("n_seasons"),
  shareAboveZero: doublePrecision("share_above_zero"),
  perAsof: doublePrecision("per_asof"),
  notGraded: integer("not_graded"),
  detail: text("detail"),
});

/** Every row of reports/regression_watch/stability.csv (the D2 stability study), in its order
 * (line = the CSV's data row, from 1; step R1: the methodology page shows the real numbers).
 * section = the CSV's `table` (split_half: split-half correlation of a metric; shrinkage: the
 * reliability r(g) of FPOE/game after g games) and seasons its `window` (both renamed: SQL
 * keywords). g is empty on split_half rows; lo/hi (95% interval) where the study has one;
 * var_signal, var_noise, prior_mean on shrinkage rows. */
export const regressionStability = pgTable("regression_stability", {
  line: integer("line").primaryKey(),
  section: text("section").notNull(),
  seasons: text("seasons").notNull(),
  split: text("split").notNull(),
  position: text("position").notNull(),
  metric: text("metric").notNull(),
  g: integer("g"),
  n: integer("n").notNull(),
  value: doublePrecision("value").notNull(),
  lo: doublePrecision("lo"),
  hi: doublePrecision("hi"),
  varSignal: doublePrecision("var_signal"),
  varNoise: doublePrecision("var_noise"),
  priorMean: doublePrecision("prior_mean"),
});

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
 * (FPOE). Shares are 0-1. points_ng / xfp_ng / fpoe_ng: the same three without garbage-time
 * plays (Regression Watch's toggle; NULL where the play-by-play has no row of the game). */
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
    pointsNg: doublePrecision("points_ng"),
    xfpNg: doublePrecision("xfp_ng"),
    fpoeNg: doublePrecision("fpoe_ng"),
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
