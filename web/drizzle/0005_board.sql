CREATE TABLE "board_disagreement" (
	"variant" text NOT NULL,
	"model" text NOT NULL,
	"season" integer NOT NULL,
	"pick_group" text NOT NULL,
	"players" integer NOT NULL,
	"hits" integer NOT NULL,
	CONSTRAINT "board_disagreement_variant_season_pick_group_pk" PRIMARY KEY("variant","season","pick_group"),
	CONSTRAINT "board_disagreement_group_check" CHECK ("board_disagreement"."pick_group" in ('model_only', 'ecr_only', 'both'))
);
--> statement-breakpoint
CREATE TABLE "board_list" (
	"season" integer NOT NULL,
	"week" integer NOT NULL,
	"snapshot" text NOT NULL,
	"kind" text NOT NULL,
	"as_of" timestamp with time zone NOT NULL,
	"model_version" text NOT NULL,
	"missed_version" text NOT NULL,
	"generated_at" timestamp with time zone NOT NULL,
	"incomplete" boolean DEFAULT false NOT NULL,
	"n_players" integer NOT NULL,
	"note" text,
	CONSTRAINT "board_list_season_week_snapshot_kind_pk" PRIMARY KEY("season","week","snapshot","kind"),
	CONSTRAINT "board_list_kind_check" CHECK ("board_list"."kind" in ('live', 'backtest')),
	CONSTRAINT "board_list_snapshot_check" CHECK ("board_list"."snapshot" in ('preseason'))
);
--> statement-breakpoint
CREATE TABLE "board_outcome" (
	"season" integer NOT NULL,
	"week" integer NOT NULL,
	"gsis_id" text NOT NULL,
	"games_s1" integer,
	"ppg_s1" double precision,
	"y_cliff" boolean,
	"y_missed" boolean,
	"label_status" text NOT NULL,
	CONSTRAINT "board_outcome_season_week_gsis_id_pk" PRIMARY KEY("season","week","gsis_id"),
	CONSTRAINT "board_outcome_status_check" CHECK ("board_outcome"."label_status" in ('final', 'pending'))
);
--> statement-breakpoint
CREATE TABLE "board_row" (
	"season" integer NOT NULL,
	"week" integer NOT NULL,
	"snapshot" text NOT NULL,
	"kind" text NOT NULL,
	"gsis_id" text NOT NULL,
	"team" text NOT NULL,
	"position" text NOT NULL,
	"as_of" timestamp with time zone NOT NULL,
	"cliff_rank" integer NOT NULL,
	"cliff_probability" double precision NOT NULL,
	"missed_rank" integer NOT NULL,
	"missed_probability" double precision NOT NULL,
	"ecr_rank" integer,
	"age" double precision,
	"prior_seasons" integer NOT NULL,
	"games_s" integer NOT NULL,
	"ppg_s" double precision NOT NULL,
	"pos_rank_s" integer NOT NULL,
	"ppg_change" double precision,
	"touches_per_game_s" double precision,
	"depth_rank_s1" integer,
	"team_change_s1" boolean,
	"dc_absent" boolean,
	"hc_change_s1" boolean,
	"cliff_drivers" jsonb NOT NULL,
	"missed_drivers" jsonb NOT NULL,
	CONSTRAINT "board_row_season_week_snapshot_kind_gsis_id_pk" PRIMARY KEY("season","week","snapshot","kind","gsis_id"),
	CONSTRAINT "board_row_cliff_probability_check" CHECK ("board_row"."cliff_probability" between 0 and 1),
	CONSTRAINT "board_row_missed_probability_check" CHECK ("board_row"."missed_probability" between 0 and 1),
	CONSTRAINT "board_row_rank_check" CHECK ("board_row"."cliff_rank" >= 1 and "board_row"."missed_rank" >= 1)
);
--> statement-breakpoint
CREATE TABLE "board_track_record" (
	"line" integer PRIMARY KEY NOT NULL,
	"population" text NOT NULL,
	"research" boolean NOT NULL,
	"variant" text NOT NULL,
	"slice" text NOT NULL,
	"model" text NOT NULL,
	"vs" text,
	"metric" text NOT NULL,
	"value" double precision,
	"lo" double precision,
	"hi" double precision,
	"share_above_zero" double precision
);
--> statement-breakpoint
ALTER TABLE "board_list" ADD CONSTRAINT "board_list_model_version_model_versions_model_version_fk" FOREIGN KEY ("model_version") REFERENCES "public"."model_versions"("model_version") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "board_list" ADD CONSTRAINT "board_list_missed_version_model_versions_model_version_fk" FOREIGN KEY ("missed_version") REFERENCES "public"."model_versions"("model_version") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "board_outcome" ADD CONSTRAINT "board_outcome_gsis_id_dim_player_gsis_id_fk" FOREIGN KEY ("gsis_id") REFERENCES "public"."dim_player"("gsis_id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "board_row" ADD CONSTRAINT "board_row_gsis_id_dim_player_gsis_id_fk" FOREIGN KEY ("gsis_id") REFERENCES "public"."dim_player"("gsis_id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "board_row" ADD CONSTRAINT "board_row_team_dim_team_team_abbr_fk" FOREIGN KEY ("team") REFERENCES "public"."dim_team"("team_abbr") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "board_row" ADD CONSTRAINT "board_row_list_fk" FOREIGN KEY ("season","week","snapshot","kind") REFERENCES "public"."board_list"("season","week","snapshot","kind") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "board_outcome_gsis_id_idx" ON "board_outcome" USING btree ("gsis_id");--> statement-breakpoint
CREATE INDEX "board_row_gsis_id_idx" ON "board_row" USING btree ("gsis_id","season");