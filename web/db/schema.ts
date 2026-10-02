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
  date,
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

// ---------------------------------------------------------------------------------------
// Decision Report Card (step P3): the graded decisions, the coach tables, the track record.
// The seasons before the approved grading's season are the FROZEN history (the committed
// snapshot pinned in config/production_models.yaml; rewritten only when it changes); the
// season in progress is regraded with the pinned models on every run and replaced.
// ---------------------------------------------------------------------------------------

/** The head coaches named by published decisions (upserted; never deleted). coach_id = the
 * name as a slug ('andy-reid'), the /coach/[id] route. */
export const dimCoach = pgTable("dim_coach", {
  coachId: text("coach_id").primaryKey(),
  name: text("name").notNull(),
});

/** Every graded fourth down (clear and toss-up; excluded plays are not published), credited to
 * the head coach of the team with the ball. WP as 0-1: each option's WP rounded to 4 decimals
 * (wp_fg / wp_punt / p_make NULL when the option did not exist), wp_lost = WP(best) -
 * WP(chosen) exact. quarter_seconds: left in the quarter (overtime: in the period). */
export const decisionFourth = pgTable(
  "decision_fourth",
  {
    gameId: text("game_id").notNull(),
    playId: integer("play_id").notNull(),
    season: integer("season").notNull(),
    week: integer("week").notNull(),
    seasonType: text("season_type").notNull(),
    posteam: text("posteam").notNull(),
    defteam: text("defteam").notNull(),
    coachId: text("coach_id")
      .notNull()
      .references(() => dimCoach.coachId),
    qtr: integer("qtr").notNull(),
    quarterSeconds: integer("quarter_seconds").notNull(),
    scoreDifferential: integer("score_differential").notNull(),
    ydstogo: integer("ydstogo").notNull(),
    yardline100: integer("yardline_100").notNull(),
    chosen: text("chosen").notNull(),
    recommended: text("recommended").notNull(),
    grade: text("grade").notNull(),
    correct: boolean("correct").notNull(),
    wpGo: doublePrecision("wp_go").notNull(),
    wpFg: doublePrecision("wp_fg"),
    wpPunt: doublePrecision("wp_punt"),
    wpLost: doublePrecision("wp_lost").notNull(),
    pConvert: doublePrecision("p_convert").notNull(),
    pMake: doublePrecision("p_make"),
    outcome: text("outcome"),
  },
  (t) => [
    primaryKey({ columns: [t.gameId, t.playId] }),
    index("decision_fourth_coach_idx").on(t.coachId, t.season),
    index("decision_fourth_season_idx").on(t.season, t.week),
    check("decision_fourth_chosen_check", sql`${t.chosen} in ('go', 'field_goal', 'punt')`),
    check(
      "decision_fourth_recommended_check",
      sql`${t.recommended} in ('go', 'field_goal', 'punt')`,
    ),
    check("decision_fourth_grade_check", sql`${t.grade} in ('clear', 'toss_up', 'one_option')`),
    check("decision_fourth_wp_lost_check", sql`${t.wpLost} >= 0 and ${t.wpLost} <= 1`),
  ],
);

/** Every graded try after a touchdown (kick vs two-point): the same rules as decision_fourth.
 * score_differential: before the try; result: the try's result as nflverse names it. */
export const decisionTwoPoint = pgTable(
  "decision_two_point",
  {
    gameId: text("game_id").notNull(),
    playId: integer("play_id").notNull(),
    season: integer("season").notNull(),
    week: integer("week").notNull(),
    seasonType: text("season_type").notNull(),
    posteam: text("posteam").notNull(),
    defteam: text("defteam").notNull(),
    coachId: text("coach_id")
      .notNull()
      .references(() => dimCoach.coachId),
    qtr: integer("qtr").notNull(),
    quarterSeconds: integer("quarter_seconds").notNull(),
    scoreDifferential: integer("score_differential").notNull(),
    chosen: text("chosen").notNull(),
    recommended: text("recommended").notNull(),
    grade: text("grade").notNull(),
    correct: boolean("correct").notNull(),
    wpKick: doublePrecision("wp_kick").notNull(),
    wpTwoPoint: doublePrecision("wp_two_point").notNull(),
    wpLost: doublePrecision("wp_lost").notNull(),
    result: text("result"),
  },
  (t) => [
    primaryKey({ columns: [t.gameId, t.playId] }),
    index("decision_two_point_coach_idx").on(t.coachId, t.season),
    check("decision_two_point_chosen_check", sql`${t.chosen} in ('kick', 'two_point')`),
    check("decision_two_point_recommended_check", sql`${t.recommended} in ('kick', 'two_point')`),
    check("decision_two_point_grade_check", sql`${t.grade} in ('clear', 'toss_up')`),
  ],
);

/** The clock-management metrics (docs/decision_metrics.md "Clock management"), one row per
 * metric and team-game: candidates and cases (is_case). The key snap: timeouts_unused = the
 * opponent's first run-out snap (amount = timeouts the team still held), half_passivity = the
 * decision snap (amount = EP left, wp_left = WP left), seconds_wasted = the worst counted
 * interval (amount = seconds wasted). detail: the metric's own context (JSON). */
export const decisionClock = pgTable(
  "decision_clock",
  {
    metric: text("metric").notNull(),
    season: integer("season").notNull(),
    week: integer("week").notNull(),
    seasonType: text("season_type").notNull(),
    gameId: text("game_id").notNull(),
    team: text("team").notNull(),
    opp: text("opp").notNull(),
    coachId: text("coach_id")
      .notNull()
      .references(() => dimCoach.coachId),
    playId: integer("play_id").notNull(),
    qtr: integer("qtr").notNull(),
    quarterSeconds: integer("quarter_seconds").notNull(),
    down: integer("down"),
    ydstogo: integer("ydstogo"),
    yardline100: integer("yardline_100"),
    scoreDifferential: integer("score_differential").notNull(),
    timeouts: integer("timeouts").notNull(),
    isCase: boolean("is_case").notNull(),
    amount: doublePrecision("amount").notNull(),
    wpLeft: doublePrecision("wp_left"),
    desc: text("desc"),
    detail: jsonb("detail").notNull(),
  },
  (t) => [
    primaryKey({ columns: [t.metric, t.gameId, t.team] }),
    index("decision_clock_coach_idx").on(t.coachId, t.season),
    check(
      "decision_clock_metric_check",
      sql`${t.metric} in ('timeouts_unused', 'half_passivity', 'seconds_wasted')`,
    ),
  ],
);

/** One row per coach and season (the leaderboards; reports/decisions/fourth_downs.csv and
 * clock.csv `coach_season`): games (with a fourth down), clear (graded) and toss-up decisions,
 * WP lost (clear decisions only, 0-1 units), wrong calls, the aggressiveness index (went for it
 * / going was clearly best; NULL without such a decision) and the clock metrics' candidates,
 * cases and amounts (m1 timeouts unused, m2 end-of-half passivity, m3 seconds wasted). team:
 * 'PHI', or 'ATL/OAK' after a move during the season. */
export const coachSeason = pgTable(
  "coach_season",
  {
    season: integer("season").notNull(),
    coachId: text("coach_id")
      .notNull()
      .references(() => dimCoach.coachId),
    team: text("team").notNull(),
    games: integer("games").notNull(),
    fourthGraded: integer("fourth_graded").notNull(),
    fourthTossUps: integer("fourth_toss_ups").notNull(),
    fourthWpLost: doublePrecision("fourth_wp_lost").notNull(),
    fourthWrong: integer("fourth_wrong").notNull(),
    goClear: integer("go_clear").notNull(),
    goClearWent: integer("go_clear_went").notNull(),
    went: integer("went").notNull(),
    twoPointGraded: integer("two_point_graded").notNull(),
    twoPointTossUps: integer("two_point_toss_ups").notNull(),
    twoPointWpLost: doublePrecision("two_point_wp_lost").notNull(),
    twoPointWrong: integer("two_point_wrong").notNull(),
    wpLost: doublePrecision("wp_lost").notNull(),
    aggressiveness: doublePrecision("aggressiveness"),
    wpLostPerGame: doublePrecision("wp_lost_per_game").notNull(),
    m1Candidates: integer("m1_candidates").notNull(),
    m1Cases: integer("m1_cases").notNull(),
    m1TimeoutsLeft: integer("m1_timeouts_left").notNull(),
    m2Candidates: integer("m2_candidates").notNull(),
    m2Cases: integer("m2_cases").notNull(),
    m2EpLeft: doublePrecision("m2_ep_left").notNull(),
    m2WpLeft: doublePrecision("m2_wp_left").notNull(),
    m3DecisiveGames: integer("m3_decisive_games").notNull(),
    m3Cases: integer("m3_cases").notNull(),
    m3SecondsWasted: doublePrecision("m3_seconds_wasted").notNull(),
  },
  (t) => [primaryKey({ columns: [t.season, t.coachId] })],
);

/** One row per coach and game (the coach page's timeline): the decision counts and WP lost of
 * that game, as in coach_season. */
export const coachWeek = pgTable(
  "coach_week",
  {
    gameId: text("game_id").notNull(),
    coachId: text("coach_id")
      .notNull()
      .references(() => dimCoach.coachId),
    season: integer("season").notNull(),
    week: integer("week").notNull(),
    seasonType: text("season_type").notNull(),
    team: text("team").notNull(),
    opp: text("opp").notNull(),
    fourthGraded: integer("fourth_graded").notNull(),
    fourthTossUps: integer("fourth_toss_ups").notNull(),
    fourthWpLost: doublePrecision("fourth_wp_lost").notNull(),
    fourthWrong: integer("fourth_wrong").notNull(),
    goClear: integer("go_clear").notNull(),
    goClearWent: integer("go_clear_went").notNull(),
    went: integer("went").notNull(),
    twoPointGraded: integer("two_point_graded").notNull(),
    twoPointTossUps: integer("two_point_toss_ups").notNull(),
    twoPointWpLost: doublePrecision("two_point_wp_lost").notNull(),
    twoPointWrong: integer("two_point_wrong").notNull(),
    wpLost: doublePrecision("wp_lost").notNull(),
  },
  (t) => [
    primaryKey({ columns: [t.gameId, t.coachId] }),
    index("coach_week_coach_idx").on(t.coachId, t.season, t.week),
  ],
);

/** The Decision Report Card's track record, row for row: source 'wp_backtest' and 'submodels'
 * = every row of reports/decisions/wp_backtest.csv and submodels.csv (line = the CSV's data
 * row, from 1; section = its `table`, n = its `n_plays`); 'nfl4th_benchmark' = the agreement
 * with nfl4th summarized from reports/decisions/nfl4th_benchmark.csv (section 'agreement':
 * scope = our grade, subset = the season or 'all', value = the share of the same
 * recommendation, n = rows; section 'go_rate': the recommended and real go rates). */
export const decisionsTrackRecord = pgTable(
  "decisions_track_record",
  {
    source: text("source").notNull(),
    line: integer("line").notNull(),
    section: text("section").notNull(),
    scope: text("scope"),
    subset: text("subset"),
    method: text("method"),
    metric: text("metric").notNull(),
    value: doublePrecision("value"),
    lo: doublePrecision("lo"),
    hi: doublePrecision("hi"),
    n: integer("n"),
    nBlocks: integer("n_blocks"),
  },
  (t) => [primaryKey({ columns: [t.source, t.line] })],
);

// ---------------------------------------------------------------------------------------
// The Hot-Seat Meter (step H4a)
// ---------------------------------------------------------------------------------------

/** One Hot-Seat list per (season, week, snapshot, kind): snapshot 'weekly' (the Tuesday as-of
 * of weeks 2 .. the week before the last) or 'end_of_season' (week = the last regular-season
 * week; each team's row after its last regular-season game, as_of = the latest team's).
 * 'live' lists are frozen once published; 'backtest' lists (2006 .. the season before the
 * approved model's) come from the frozen walk-forward backtest and are replaced. */
export const hotSeatList = pgTable(
  "hot_seat_list",
  {
    season: integer("season").notNull(),
    week: integer("week").notNull(),
    snapshot: text("snapshot").notNull(),
    kind: text("kind").notNull(),
    asOf: tstz("as_of").notNull(),
    modelVersion: text("model_version")
      .notNull()
      .references(() => modelVersions.modelVersion),
    generatedAt: tstz("generated_at").notNull(),
    incomplete: boolean("incomplete").notNull().default(false),
    nCoaches: integer("n_coaches").notNull(),
    note: text("note"),
  },
  (t) => [
    primaryKey({ columns: [t.season, t.week, t.snapshot, t.kind] }),
    check("hot_seat_list_kind_check", sql`${t.kind} in ('live', 'backtest')`),
    check(
      "hot_seat_list_snapshot_check",
      sql`${t.snapshot} in ('weekly', 'end_of_season')`,
    ),
  ],
);

/** Every scored head coach of each list (coach_id = the site's slug, as in dim_coach and
 * /coach/[id]), ranked by probability: the chance that his departure (fired in or after the
 * season, or a mutual parting) is announced by 30 days after the team's final game, the
 * model's own probability. as_of = the row's own (end of season: the team's). drivers = the 3
 * largest terms of the logistic regression (coef x standardized value), JSON
 * [{feature, label, contribution, value, missing}]. is_interim: took over during the season
 * (scored, but the model was not trained on interims). The key features the page shows
 * follow (the H3a names, docs/glossary.md). A coach's timeline = his rows across lists. */
export const hotSeatRow = pgTable(
  "hot_seat_row",
  {
    season: integer("season").notNull(),
    week: integer("week").notNull(),
    snapshot: text("snapshot").notNull(),
    kind: text("kind").notNull(),
    coachId: text("coach_id")
      .notNull()
      .references(() => dimCoach.coachId),
    team: text("team")
      .notNull()
      .references(() => dimTeam.teamAbbr),
    asOf: tstz("as_of").notNull(),
    rank: integer("rank").notNull(),
    probability: doublePrecision("probability").notNull(),
    isInterim: boolean("is_interim").notNull(),
    drivers: jsonb("drivers").notNull(),
    regGamesPlayed: integer("reg_games_played").notNull(),
    regWins: doublePrecision("reg_wins").notNull(),
    expectedWins: doublePrecision("expected_wins"),
    winsVsExpected: doublePrecision("wins_vs_expected"),
    pointDiffPerGame: doublePrecision("point_diff_per_game"),
    tenureSeasons: integer("tenure_seasons"),
    divisionRank: integer("division_rank"),
    prevSeasonWins: doublePrecision("prev_season_wins"),
    consecutiveLosingSeasons: integer("consecutive_losing_seasons"),
    fourthDownWpLostPerGame: doublePrecision("fourth_down_wp_lost_per_game"),
  },
  (t) => [
    primaryKey({ columns: [t.season, t.week, t.snapshot, t.kind, t.coachId] }),
    foreignKey({
      name: "hot_seat_row_list_fk",
      columns: [t.season, t.week, t.snapshot, t.kind],
      foreignColumns: [hotSeatList.season, hotSeatList.week, hotSeatList.snapshot, hotSeatList.kind],
    }),
    index("hot_seat_row_coach_id_idx").on(t.coachId, t.season, t.week),
    check("hot_seat_row_probability_check", sql`${t.probability} between 0 and 1`),
    check("hot_seat_row_rank_check", sql`${t.rank} >= 1`),
  ],
);

/** What happened, per (season, week, coach): departed = a positive departure announced in
 * the window (on or after the row's day, by 30 days after the final game), censored = another
 * departure in that span (e.g. resigned under pressure); 'final' for the frozen backtest's
 * rows (labels: cited public-source research accepted by the owner), 'pending' (NULLs) for
 * live seasons. Replaced on every publish. */
export const hotSeatOutcome = pgTable(
  "hot_seat_outcome",
  {
    season: integer("season").notNull(),
    week: integer("week").notNull(),
    coachId: text("coach_id")
      .notNull()
      .references(() => dimCoach.coachId),
    departed: boolean("departed"),
    censored: boolean("censored"),
    departureType: text("departure_type"),
    announced: date("announced"),
    labelStatus: text("label_status").notNull(),
  },
  (t) => [
    primaryKey({ columns: [t.season, t.week, t.coachId] }),
    index("hot_seat_outcome_coach_id_idx").on(t.coachId),
    check("hot_seat_outcome_status_check", sql`${t.labelStatus} in ('final', 'pending')`),
  ],
);

/** The Hot-Seat walk-forward backtest's track record: every row of
 * reports/hot_seat/backtest_metrics.csv (line = the CSV's data row, from 1): per variant
 * (main, censored_dropped, rup_positive), model, probability ('prob' = the model's own,
 * 'prob_iso' = the isotonic-calibrated one) and slice ('all', 'week_02' .. 'week_17',
 * 'end_of_season'): ROC-AUC, PR-AUC, Brier and the top-5 hit rates, with the season-block
 * bootstrap interval (lo, hi) and the rows, positives and seasons behind it. */
export const hotSeatTrackRecord = pgTable("hot_seat_track_record", {
  line: integer("line").primaryKey(),
  variant: text("variant").notNull(),
  model: text("model").notNull(),
  prob: text("prob").notNull(),
  slice: text("slice").notNull(),
  metric: text("metric").notNull(),
  value: doublePrecision("value"),
  lo: doublePrecision("lo"),
  hi: doublePrecision("hi"),
  nRows: integer("n_rows").notNull(),
  nPos: integer("n_pos").notNull(),
  nSeasons: integer("n_seasons").notNull(),
});

/** Head-coach departures per season, row for row from reports/hot_seat/firings_per_season.csv
 * (coach-seasons; interims counted apart): positive departures, of them fired in season,
 * positives on the week-12 and end-of-season rows, censored coach-seasons, interims. */
export const hotSeatFirings = pgTable("hot_seat_firings", {
  season: integer("season").primaryKey(),
  positiveDepartures: integer("positive_departures").notNull(),
  firedInSeason: integer("fired_in_season").notNull(),
  positivesWeek12: integer("positives_week_12").notNull(),
  positivesEndOfSeason: integer("positives_end_of_season").notNull(),
  censoredCoachSeasons: integer("censored_coach_seasons").notNull(),
  interimCoachSeasons: integer("interim_coach_seasons").notNull(),
});
