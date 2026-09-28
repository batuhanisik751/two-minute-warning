CREATE TABLE "dim_player" (
	"gsis_id" text PRIMARY KEY NOT NULL,
	"display_name" text NOT NULL,
	"position" text,
	"team" text,
	"draft_year" integer,
	"draft_round" integer,
	"draft_pick" integer,
	"rookie_season" integer
);
--> statement-breakpoint
CREATE TABLE "dim_team" (
	"team_abbr" text PRIMARY KEY NOT NULL,
	"team_name" text NOT NULL,
	"team_nick" text NOT NULL,
	"conference" text NOT NULL,
	"division" text NOT NULL,
	"color" text,
	"color2" text
);
--> statement-breakpoint
CREATE TABLE "glossary" (
	"name" text PRIMARY KEY NOT NULL,
	"title" text NOT NULL,
	"kind" text NOT NULL,
	"unit" text NOT NULL,
	"formula" text NOT NULL,
	"explanation" text NOT NULL,
	"verified" text,
	"modules" text[] NOT NULL,
	"model_output" boolean NOT NULL
);
--> statement-breakpoint
CREATE TABLE "model_versions" (
	"model_version" text PRIMARY KEY NOT NULL,
	"module" text NOT NULL,
	"model" text NOT NULL,
	"label" text NOT NULL,
	"training_seasons" integer[] NOT NULL,
	"test_season" integer,
	"feature_list" text[] NOT NULL,
	"params" jsonb NOT NULL,
	"created_at" timestamp with time zone NOT NULL
);
--> statement-breakpoint
CREATE TABLE "pipeline_runs" (
	"run_id" text PRIMARY KEY NOT NULL,
	"started_at" timestamp with time zone NOT NULL,
	"finished_at" timestamp with time zone NOT NULL,
	"status" text NOT NULL,
	"stage" text NOT NULL,
	"git_sha" text,
	"data_as_of" timestamp with time zone,
	"notes" jsonb DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT "pipeline_runs_status_check" CHECK ("pipeline_runs"."status" in ('success', 'failed'))
);
--> statement-breakpoint
CREATE TABLE "player_week_summary" (
	"gsis_id" text NOT NULL,
	"season" integer NOT NULL,
	"week" integer NOT NULL,
	"team" text NOT NULL,
	"position" text NOT NULL,
	"fantasy_points" double precision NOT NULL,
	"snap_share" double precision,
	"target_share" double precision,
	"carry_share" double precision,
	"xfp" double precision,
	"fpoe" double precision,
	CONSTRAINT "player_week_summary_gsis_id_season_week_pk" PRIMARY KEY("gsis_id","season","week")
);
--> statement-breakpoint
CREATE TABLE "radar_list" (
	"season" integer NOT NULL,
	"week" integer NOT NULL,
	"position" text NOT NULL,
	"kind" text NOT NULL,
	"as_of" timestamp with time zone NOT NULL,
	"model_version" text NOT NULL,
	"generated_at" timestamp with time zone NOT NULL,
	"incomplete" boolean DEFAULT false NOT NULL,
	"n_pool" integer NOT NULL,
	"note" text,
	CONSTRAINT "radar_list_season_week_position_kind_pk" PRIMARY KEY("season","week","position","kind"),
	CONSTRAINT "radar_list_kind_check" CHECK ("radar_list"."kind" in ('live', 'backtest')),
	CONSTRAINT "radar_list_position_check" CHECK ("radar_list"."position" in ('QB', 'RB', 'WR', 'TE'))
);
--> statement-breakpoint
CREATE TABLE "radar_outcome" (
	"season" integer NOT NULL,
	"week" integer NOT NULL,
	"gsis_id" text NOT NULL,
	"y_hit" boolean,
	"y_sustained" boolean,
	"label_status" text NOT NULL,
	"window_weeks" integer[],
	"window_ranks" integer[],
	"window_points" double precision[],
	CONSTRAINT "radar_outcome_season_week_gsis_id_pk" PRIMARY KEY("season","week","gsis_id")
);
--> statement-breakpoint
CREATE TABLE "radar_pick" (
	"season" integer NOT NULL,
	"week" integer NOT NULL,
	"position" text NOT NULL,
	"kind" text NOT NULL,
	"rank" integer NOT NULL,
	"gsis_id" text NOT NULL,
	"team" text NOT NULL,
	"chance" double precision,
	"chance_low" double precision,
	"chance_high" double precision,
	"model_prob" double precision NOT NULL,
	"tier" text,
	"reasons" jsonb DEFAULT '[]'::jsonb NOT NULL,
	CONSTRAINT "radar_pick_season_week_position_kind_rank_pk" PRIMARY KEY("season","week","position","kind","rank"),
	CONSTRAINT "radar_pick_rank_check" CHECK ("radar_pick"."rank" >= 1),
	CONSTRAINT "radar_pick_model_prob_check" CHECK ("radar_pick"."model_prob" >= 0 and "radar_pick"."model_prob" <= 1),
	CONSTRAINT "radar_pick_chance_check" CHECK ("radar_pick"."chance" is null or ("radar_pick"."chance_low" <= "radar_pick"."chance" and "radar_pick"."chance" <= "radar_pick"."chance_high" and "radar_pick"."chance_low" >= 0 and "radar_pick"."chance_high" <= 1)),
	CONSTRAINT "radar_pick_tier_check" CHECK ("radar_pick"."tier" is null or "radar_pick"."tier" in ('must-add', 'speculative', 'watch'))
);
--> statement-breakpoint
CREATE TABLE "site_meta" (
	"key" text PRIMARY KEY NOT NULL,
	"value" text NOT NULL
);
--> statement-breakpoint
CREATE TABLE "tier_stats" (
	"module" text NOT NULL,
	"model" text NOT NULL,
	"label" text NOT NULL,
	"week" integer NOT NULL,
	"tier" text NOT NULL,
	"season_from" integer NOT NULL,
	"season_to" integer NOT NULL,
	"chance_low" double precision NOT NULL,
	"chance_high" double precision NOT NULL,
	"prob_low" double precision,
	"prob_high" double precision,
	"lists" integer NOT NULL,
	"players" integer NOT NULL,
	"hits" integer NOT NULL,
	"hit_rate" double precision,
	"per_list" double precision,
	CONSTRAINT "tier_stats_module_model_label_week_tier_pk" PRIMARY KEY("module","model","label","week","tier")
);
--> statement-breakpoint
CREATE TABLE "track_record" (
	"module" text NOT NULL,
	"model" text NOT NULL,
	"label" text NOT NULL,
	"metric" text NOT NULL,
	"scope" text NOT NULL,
	"scope_value" text DEFAULT '' NOT NULL,
	"season_from" integer NOT NULL,
	"season_to" integer NOT NULL,
	"excl_rostered" boolean NOT NULL,
	"value" double precision,
	"low" double precision,
	"high" double precision,
	"n_lists" integer,
	"n_positives" integer,
	"n_rows" integer,
	"n_top_hits" integer,
	"n_top" integer,
	CONSTRAINT "track_record_pk" PRIMARY KEY("module","label","excl_rostered","model","scope","scope_value","season_from","season_to","metric")
);
--> statement-breakpoint
ALTER TABLE "player_week_summary" ADD CONSTRAINT "player_week_summary_gsis_id_dim_player_gsis_id_fk" FOREIGN KEY ("gsis_id") REFERENCES "public"."dim_player"("gsis_id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "player_week_summary" ADD CONSTRAINT "player_week_summary_team_dim_team_team_abbr_fk" FOREIGN KEY ("team") REFERENCES "public"."dim_team"("team_abbr") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "radar_list" ADD CONSTRAINT "radar_list_model_version_model_versions_model_version_fk" FOREIGN KEY ("model_version") REFERENCES "public"."model_versions"("model_version") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "radar_outcome" ADD CONSTRAINT "radar_outcome_gsis_id_dim_player_gsis_id_fk" FOREIGN KEY ("gsis_id") REFERENCES "public"."dim_player"("gsis_id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "radar_pick" ADD CONSTRAINT "radar_pick_gsis_id_dim_player_gsis_id_fk" FOREIGN KEY ("gsis_id") REFERENCES "public"."dim_player"("gsis_id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "radar_pick" ADD CONSTRAINT "radar_pick_team_dim_team_team_abbr_fk" FOREIGN KEY ("team") REFERENCES "public"."dim_team"("team_abbr") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "radar_pick" ADD CONSTRAINT "radar_pick_list_fk" FOREIGN KEY ("season","week","position","kind") REFERENCES "public"."radar_list"("season","week","position","kind") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "pipeline_runs_started_at_idx" ON "pipeline_runs" USING btree ("started_at");--> statement-breakpoint
CREATE INDEX "player_week_summary_week_idx" ON "player_week_summary" USING btree ("season","week","position");--> statement-breakpoint
CREATE INDEX "radar_outcome_gsis_id_idx" ON "radar_outcome" USING btree ("gsis_id");--> statement-breakpoint
CREATE UNIQUE INDEX "radar_pick_player_uq" ON "radar_pick" USING btree ("season","week","position","kind","gsis_id");--> statement-breakpoint
CREATE INDEX "radar_pick_gsis_id_idx" ON "radar_pick" USING btree ("gsis_id");