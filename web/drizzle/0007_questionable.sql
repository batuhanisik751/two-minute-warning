CREATE TABLE "questionable_backtest" (
	"grouping" text NOT NULL,
	"title" text NOT NULL,
	"chosen" boolean NOT NULL,
	"season" text NOT NULL,
	"n" integer NOT NULL,
	"log_loss" double precision NOT NULL,
	"brier" double precision NOT NULL,
	"mean_p" double precision NOT NULL,
	"played_rate" double precision NOT NULL,
	CONSTRAINT "questionable_backtest_grouping_season_pk" PRIMARY KEY("grouping","season")
);
--> statement-breakpoint
CREATE TABLE "questionable_calibration" (
	"line" integer PRIMARY KEY NOT NULL,
	"bucket" text NOT NULL,
	"n" integer NOT NULL,
	"predicted" double precision,
	"actual" double precision
);
--> statement-breakpoint
CREATE TABLE "questionable_history" (
	"report_status" text NOT NULL,
	"practice" text NOT NULL,
	"seasons" text NOT NULL,
	"n" integer NOT NULL,
	"played" integer NOT NULL,
	"played_rate" double precision NOT NULL,
	CONSTRAINT "questionable_history_report_status_practice_pk" PRIMARY KEY("report_status","practice"),
	CONSTRAINT "questionable_history_status_check" CHECK ("questionable_history"."report_status" in ('Questionable', 'Doubtful', 'Out')),
	CONSTRAINT "questionable_history_practice_check" CHECK ("questionable_history"."practice" in ('all', 'full', 'limited', 'dnp', 'none')),
	CONSTRAINT "questionable_history_counts_check" CHECK ("questionable_history"."played" between 0 and "questionable_history"."n")
);
--> statement-breakpoint
CREATE TABLE "questionable_list" (
	"season" integer NOT NULL,
	"week" integer NOT NULL,
	"as_of" timestamp with time zone NOT NULL,
	"model_version" text NOT NULL,
	"generated_at" timestamp with time zone NOT NULL,
	"n_players" integer NOT NULL,
	"source" text NOT NULL,
	CONSTRAINT "questionable_list_season_week_as_of_pk" PRIMARY KEY("season","week","as_of"),
	CONSTRAINT "questionable_list_source_check" CHECK ("questionable_list"."source" in ('asof', 'observed')),
	CONSTRAINT "questionable_list_players_check" CHECK ("questionable_list"."n_players" >= 1)
);
--> statement-breakpoint
CREATE TABLE "questionable_live" (
	"season" integer NOT NULL,
	"report_status" text NOT NULL,
	"n" integer NOT NULL,
	"predicted" double precision,
	"actual" double precision,
	"pending" integer,
	"weeks" integer,
	CONSTRAINT "questionable_live_season_report_status_pk" PRIMARY KEY("season","report_status"),
	CONSTRAINT "questionable_live_status_check" CHECK ("questionable_live"."report_status" in ('all', 'Questionable', 'Doubtful'))
);
--> statement-breakpoint
CREATE TABLE "questionable_row" (
	"season" integer NOT NULL,
	"week" integer NOT NULL,
	"as_of" timestamp with time zone NOT NULL,
	"gsis_id" text NOT NULL,
	"position" text NOT NULL,
	"team" text NOT NULL,
	"opponent" text,
	"game_id" text,
	"kickoff" timestamp with time zone NOT NULL,
	"report_status" text NOT NULL,
	"practice_status" text,
	"practice" text NOT NULL,
	"missed_prev" boolean,
	"body_part" text,
	"play_chance" double precision NOT NULL,
	"plays_n" integer,
	"plays_median" double precision,
	"plays_dud_rate" double precision,
	"healthy_median" double precision,
	"healthy_dud_rate" double precision,
	"season_ppg" double precision,
	"season_games" integer NOT NULL,
	CONSTRAINT "questionable_row_season_week_as_of_gsis_id_pk" PRIMARY KEY("season","week","as_of","gsis_id"),
	CONSTRAINT "questionable_row_position_check" CHECK ("questionable_row"."position" in ('QB', 'RB', 'WR', 'TE')),
	CONSTRAINT "questionable_row_status_check" CHECK ("questionable_row"."report_status" in ('Questionable', 'Doubtful')),
	CONSTRAINT "questionable_row_practice_check" CHECK ("questionable_row"."practice" in ('full', 'limited', 'dnp', 'none')),
	CONSTRAINT "questionable_row_chance_check" CHECK ("questionable_row"."play_chance" between 0 and 1)
);
--> statement-breakpoint
ALTER TABLE "questionable_list" ADD CONSTRAINT "questionable_list_model_version_model_versions_model_version_fk" FOREIGN KEY ("model_version") REFERENCES "public"."model_versions"("model_version") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "questionable_row" ADD CONSTRAINT "questionable_row_gsis_id_dim_player_gsis_id_fk" FOREIGN KEY ("gsis_id") REFERENCES "public"."dim_player"("gsis_id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "questionable_row" ADD CONSTRAINT "questionable_row_team_dim_team_team_abbr_fk" FOREIGN KEY ("team") REFERENCES "public"."dim_team"("team_abbr") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "questionable_row" ADD CONSTRAINT "questionable_row_opponent_dim_team_team_abbr_fk" FOREIGN KEY ("opponent") REFERENCES "public"."dim_team"("team_abbr") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "questionable_row" ADD CONSTRAINT "questionable_row_list_fk" FOREIGN KEY ("season","week","as_of") REFERENCES "public"."questionable_list"("season","week","as_of") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "questionable_row_gsis_id_idx" ON "questionable_row" USING btree ("gsis_id","season","week");