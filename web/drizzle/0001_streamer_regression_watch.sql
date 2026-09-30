CREATE TABLE "regression_list" (
	"season" integer NOT NULL,
	"week" integer NOT NULL,
	"kind" text NOT NULL,
	"as_of" timestamp with time zone NOT NULL,
	"params_version" text NOT NULL,
	"generated_at" timestamp with time zone NOT NULL,
	"incomplete" boolean DEFAULT false NOT NULL,
	"n_universe" integer NOT NULL,
	"note" text,
	CONSTRAINT "regression_list_season_week_kind_pk" PRIMARY KEY("season","week","kind"),
	CONSTRAINT "regression_list_kind_check" CHECK ("regression_list"."kind" in ('live', 'backtest'))
);
--> statement-breakpoint
CREATE TABLE "regression_outcome" (
	"season" integer NOT NULL,
	"week" integer NOT NULL,
	"gsis_id" text NOT NULL,
	"ros_ppg" double precision,
	"ros_games" integer,
	"label_status" text NOT NULL,
	CONSTRAINT "regression_outcome_season_week_gsis_id_pk" PRIMARY KEY("season","week","gsis_id")
);
--> statement-breakpoint
CREATE TABLE "regression_row" (
	"season" integer NOT NULL,
	"week" integer NOT NULL,
	"kind" text NOT NULL,
	"gsis_id" text NOT NULL,
	"position" text NOT NULL,
	"team" text NOT NULL,
	"games" integer NOT NULL,
	"ppg" double precision NOT NULL,
	"ppg_ng" double precision,
	"xfp_pg" double precision,
	"xfp_pg_ng" double precision,
	"fpoe_pg" double precision,
	"fpoe_pg_ng" double precision,
	"projection" double precision NOT NULL,
	"shrinkage" double precision,
	"tag" text,
	"tags" text[] DEFAULT '{}'::text[] NOT NULL,
	"tag_reason" text,
	CONSTRAINT "regression_row_season_week_kind_gsis_id_pk" PRIMARY KEY("season","week","kind","gsis_id"),
	CONSTRAINT "regression_row_position_check" CHECK ("regression_row"."position" in ('QB', 'RB', 'WR', 'TE')),
	CONSTRAINT "regression_row_tag_check" CHECK ("regression_row"."tag" is null or "regression_row"."tag" in ('sell_high', 'buy_low', 'legit')),
	CONSTRAINT "regression_row_tags_check" CHECK ("regression_row"."tags" <@ array['sell_high', 'buy_low', 'legit']::text[] and ("regression_row"."tag" is null) = (cardinality("regression_row"."tags") = 0)),
	CONSTRAINT "regression_row_games_check" CHECK ("regression_row"."games" >= 1)
);
--> statement-breakpoint
CREATE TABLE "regression_track_record" (
	"line" integer PRIMARY KEY NOT NULL,
	"section" text NOT NULL,
	"weeks" text,
	"position" text,
	"method" text,
	"metric" text,
	"row_group" text,
	"season" integer,
	"value" double precision NOT NULL,
	"lo" double precision,
	"hi" double precision,
	"n" integer NOT NULL,
	"n_seasons" integer,
	"share_above_zero" double precision,
	"per_asof" double precision,
	"not_graded" integer,
	"detail" text
);
--> statement-breakpoint
CREATE TABLE "stream_list" (
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
	CONSTRAINT "stream_list_season_week_position_kind_pk" PRIMARY KEY("season","week","position","kind"),
	CONSTRAINT "stream_list_kind_check" CHECK ("stream_list"."kind" in ('live', 'backtest')),
	CONSTRAINT "stream_list_position_check" CHECK ("stream_list"."position" in ('K', 'DST'))
);
--> statement-breakpoint
CREATE TABLE "stream_outcome" (
	"season" integer NOT NULL,
	"week" integer NOT NULL,
	"entity_id" text NOT NULL,
	"y_start" boolean,
	"label_status" text NOT NULL,
	"points_next_week" double precision,
	CONSTRAINT "stream_outcome_season_week_entity_id_pk" PRIMARY KEY("season","week","entity_id")
);
--> statement-breakpoint
CREATE TABLE "stream_pick" (
	"season" integer NOT NULL,
	"week" integer NOT NULL,
	"position" text NOT NULL,
	"kind" text NOT NULL,
	"rank" integer NOT NULL,
	"entity_id" text NOT NULL,
	"entity_type" text NOT NULL,
	"display_name" text NOT NULL,
	"team" text NOT NULL,
	"next_opponent" text,
	"home" boolean,
	"chance" double precision,
	"chance_low" double precision,
	"chance_high" double precision,
	"model_prob" double precision,
	"tier" text,
	"reasons" jsonb DEFAULT '[]'::jsonb NOT NULL,
	CONSTRAINT "stream_pick_season_week_position_kind_rank_pk" PRIMARY KEY("season","week","position","kind","rank"),
	CONSTRAINT "stream_pick_rank_check" CHECK ("stream_pick"."rank" >= 1),
	CONSTRAINT "stream_pick_entity_check" CHECK (("stream_pick"."position" = 'K' and "stream_pick"."entity_type" = 'kicker' and "stream_pick"."entity_id" ~ '^(00-[0-9]{7}|[A-Z]{3}[0-9]{6})$') or ("stream_pick"."position" = 'DST' and "stream_pick"."entity_type" = 'team_defense' and "stream_pick"."entity_id" = 'DST-' || "stream_pick"."team")),
	CONSTRAINT "stream_pick_model_prob_check" CHECK ("stream_pick"."model_prob" is null or ("stream_pick"."model_prob" >= 0 and "stream_pick"."model_prob" <= 1)),
	CONSTRAINT "stream_pick_chance_check" CHECK ("stream_pick"."chance" is null or ("stream_pick"."chance_low" <= "stream_pick"."chance" and "stream_pick"."chance" <= "stream_pick"."chance_high" and "stream_pick"."chance_low" >= 0 and "stream_pick"."chance_high" <= 1)),
	CONSTRAINT "stream_pick_tier_check" CHECK ("stream_pick"."tier" is null or "stream_pick"."tier" in ('must-add', 'speculative', 'watch'))
);
--> statement-breakpoint
CREATE TABLE "stream_track_record" (
	"line" integer PRIMARY KEY NOT NULL,
	"position" text NOT NULL,
	"method" text NOT NULL,
	"train_on" text NOT NULL,
	"scope" text NOT NULL,
	"seasons" text NOT NULL,
	"key" text,
	"metric" text NOT NULL,
	"value" double precision,
	"lo" double precision,
	"hi" double precision,
	"share_above_zero" double precision,
	"n_groups" integer,
	"n_rows" integer,
	"n_pos" integer
);
--> statement-breakpoint
ALTER TABLE "player_week_summary" ADD COLUMN "points_ng" double precision;--> statement-breakpoint
ALTER TABLE "player_week_summary" ADD COLUMN "xfp_ng" double precision;--> statement-breakpoint
ALTER TABLE "player_week_summary" ADD COLUMN "fpoe_ng" double precision;--> statement-breakpoint
ALTER TABLE "regression_list" ADD CONSTRAINT "regression_list_params_version_model_versions_model_version_fk" FOREIGN KEY ("params_version") REFERENCES "public"."model_versions"("model_version") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "regression_outcome" ADD CONSTRAINT "regression_outcome_gsis_id_dim_player_gsis_id_fk" FOREIGN KEY ("gsis_id") REFERENCES "public"."dim_player"("gsis_id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "regression_row" ADD CONSTRAINT "regression_row_gsis_id_dim_player_gsis_id_fk" FOREIGN KEY ("gsis_id") REFERENCES "public"."dim_player"("gsis_id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "regression_row" ADD CONSTRAINT "regression_row_team_dim_team_team_abbr_fk" FOREIGN KEY ("team") REFERENCES "public"."dim_team"("team_abbr") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "regression_row" ADD CONSTRAINT "regression_row_list_fk" FOREIGN KEY ("season","week","kind") REFERENCES "public"."regression_list"("season","week","kind") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "stream_list" ADD CONSTRAINT "stream_list_model_version_model_versions_model_version_fk" FOREIGN KEY ("model_version") REFERENCES "public"."model_versions"("model_version") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "stream_pick" ADD CONSTRAINT "stream_pick_team_dim_team_team_abbr_fk" FOREIGN KEY ("team") REFERENCES "public"."dim_team"("team_abbr") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "stream_pick" ADD CONSTRAINT "stream_pick_next_opponent_dim_team_team_abbr_fk" FOREIGN KEY ("next_opponent") REFERENCES "public"."dim_team"("team_abbr") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "stream_pick" ADD CONSTRAINT "stream_pick_list_fk" FOREIGN KEY ("season","week","position","kind") REFERENCES "public"."stream_list"("season","week","position","kind") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "regression_outcome_gsis_id_idx" ON "regression_outcome" USING btree ("gsis_id");--> statement-breakpoint
CREATE INDEX "regression_row_gsis_id_idx" ON "regression_row" USING btree ("gsis_id");--> statement-breakpoint
CREATE INDEX "stream_outcome_entity_id_idx" ON "stream_outcome" USING btree ("entity_id");--> statement-breakpoint
CREATE UNIQUE INDEX "stream_pick_entity_uq" ON "stream_pick" USING btree ("season","week","position","kind","entity_id");--> statement-breakpoint
CREATE INDEX "stream_pick_entity_id_idx" ON "stream_pick" USING btree ("entity_id");