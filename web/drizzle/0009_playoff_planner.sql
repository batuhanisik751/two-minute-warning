CREATE TABLE "playoff_planner_backtest" (
	"position" text NOT NULL,
	"candidate" text NOT NULL,
	"title" text NOT NULL,
	"n" integer NOT NULL,
	"mae" double precision NOT NULL,
	"vs" text,
	"seasons_won" integer,
	"seasons" integer NOT NULL,
	"took_over" boolean NOT NULL,
	"chosen" boolean NOT NULL,
	"rule_pick" boolean NOT NULL,
	CONSTRAINT "playoff_planner_backtest_position_candidate_pk" PRIMARY KEY("position","candidate"),
	CONSTRAINT "playoff_planner_backtest_candidate_check" CHECK ("playoff_planner_backtest"."candidate" in ('none', 'raw', 'shrunk', 'adjusted')),
	CONSTRAINT "playoff_planner_backtest_won_check" CHECK ("playoff_planner_backtest"."seasons_won" between 0 and "playoff_planner_backtest"."seasons")
);
--> statement-breakpoint
CREATE TABLE "playoff_planner_choice" (
	"position" text PRIMARY KEY NOT NULL,
	"candidate" text NOT NULL,
	"rule_choice" text NOT NULL,
	"chosen_by" text NOT NULL,
	"path" text NOT NULL,
	"pseudo_games" double precision NOT NULL,
	CONSTRAINT "playoff_planner_choice_position_check" CHECK ("playoff_planner_choice"."position" in ('QB', 'RB', 'WR', 'TE', 'K', 'DST')),
	CONSTRAINT "playoff_planner_choice_candidate_check" CHECK ("playoff_planner_choice"."candidate" in ('none', 'raw', 'shrunk', 'adjusted') and "playoff_planner_choice"."rule_choice" in ('none', 'raw', 'shrunk', 'adjusted'))
);
--> statement-breakpoint
CREATE TABLE "playoff_planner_effects" (
	"position" text NOT NULL,
	"horizon" text NOT NULL,
	"n_worst" integer NOT NULL,
	"n_best" integer NOT NULL,
	"rated_gap" double precision,
	"shrunk_gap" double precision,
	"realized_gap" double precision,
	"survived" double precision,
	CONSTRAINT "playoff_planner_effects_position_horizon_pk" PRIMARY KEY("position","horizon")
);
--> statement-breakpoint
CREATE TABLE "playoff_planner_late_weeks" (
	"era" text NOT NULL,
	"position" text NOT NULL,
	"week" integer NOT NULL,
	"based" integer NOT NULL,
	"played_share" double precision,
	"vs_base" double precision,
	CONSTRAINT "playoff_planner_late_weeks_era_position_week_pk" PRIMARY KEY("era","position","week"),
	CONSTRAINT "playoff_planner_late_weeks_share_check" CHECK ("playoff_planner_late_weeks"."played_share" between 0 and 1)
);
--> statement-breakpoint
CREATE TABLE "playoff_planner_list" (
	"season" integer NOT NULL,
	"through_week" integer NOT NULL,
	"as_of" timestamp with time zone NOT NULL,
	"model_version" text NOT NULL,
	"generated_at" timestamp with time zone NOT NULL,
	"n_teams" integer NOT NULL,
	"n_rows" integer NOT NULL,
	CONSTRAINT "playoff_planner_list_season_through_week_pk" PRIMARY KEY("season","through_week"),
	CONSTRAINT "playoff_planner_list_week_check" CHECK ("playoff_planner_list"."through_week" >= 1),
	CONSTRAINT "playoff_planner_list_counts_check" CHECK ("playoff_planner_list"."n_teams" >= 1 and "playoff_planner_list"."n_rows" >= "playoff_planner_list"."n_teams")
);
--> statement-breakpoint
CREATE TABLE "playoff_planner_live" (
	"season" integer NOT NULL,
	"position" text NOT NULL,
	"n" integer NOT NULL,
	"pending" integer NOT NULL,
	"weeks" integer NOT NULL,
	"mae_rating" double precision,
	"mae_flat" double precision,
	CONSTRAINT "playoff_planner_live_season_position_pk" PRIMARY KEY("season","position"),
	CONSTRAINT "playoff_planner_live_position_check" CHECK ("playoff_planner_live"."position" in ('QB', 'RB', 'WR', 'TE', 'K', 'DST', 'all')),
	CONSTRAINT "playoff_planner_live_counts_check" CHECK ("playoff_planner_live"."n" >= 0 and "playoff_planner_live"."pending" >= 0 and "playoff_planner_live"."weeks" >= 0)
);
--> statement-breakpoint
CREATE TABLE "playoff_planner_row" (
	"season" integer NOT NULL,
	"through_week" integer NOT NULL,
	"week" integer NOT NULL,
	"team" text NOT NULL,
	"position" text NOT NULL,
	"opponent" text,
	"home" boolean,
	"game_id" text,
	"kickoff" timestamp with time zone,
	"candidate" text NOT NULL,
	"rating" double precision,
	"rating_rank" integer,
	"raw" double precision,
	"shrunk" double precision,
	"adjusted" double precision,
	"opp_games" integer,
	"lg_ppg" double precision,
	CONSTRAINT "playoff_planner_row_season_through_week_week_team_position_pk" PRIMARY KEY("season","through_week","week","team","position"),
	CONSTRAINT "playoff_planner_row_position_check" CHECK ("playoff_planner_row"."position" in ('QB', 'RB', 'WR', 'TE', 'K', 'DST')),
	CONSTRAINT "playoff_planner_row_candidate_check" CHECK ("playoff_planner_row"."candidate" in ('none', 'raw', 'shrunk', 'adjusted')),
	CONSTRAINT "playoff_planner_row_bye_check" CHECK (("playoff_planner_row"."opponent" is null) = ("playoff_planner_row"."rating" is null)),
	CONSTRAINT "playoff_planner_row_rating_check" CHECK ("playoff_planner_row"."rating" > 0 and ("playoff_planner_row"."candidate" <> 'none' or ("playoff_planner_row"."rating" = 1 and "playoff_planner_row"."rating_rank" is null))),
	CONSTRAINT "playoff_planner_row_rank_check" CHECK ("playoff_planner_row"."rating_rank" between 1 and 32)
);
--> statement-breakpoint
CREATE TABLE "playoff_planner_stability" (
	"position" text NOT NULL,
	"horizon" integer NOT NULL,
	"seasons" integer NOT NULL,
	"rho_mean" double precision,
	"rho_min" double precision,
	"rho_max" double precision,
	CONSTRAINT "playoff_planner_stability_position_horizon_pk" PRIMARY KEY("position","horizon"),
	CONSTRAINT "playoff_planner_stability_rho_check" CHECK ("playoff_planner_stability"."rho_mean" between -1 and 1)
);
--> statement-breakpoint
ALTER TABLE "playoff_planner_list" ADD CONSTRAINT "playoff_planner_list_model_version_model_versions_model_version_fk" FOREIGN KEY ("model_version") REFERENCES "public"."model_versions"("model_version") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "playoff_planner_row" ADD CONSTRAINT "playoff_planner_row_team_dim_team_team_abbr_fk" FOREIGN KEY ("team") REFERENCES "public"."dim_team"("team_abbr") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "playoff_planner_row" ADD CONSTRAINT "playoff_planner_row_opponent_dim_team_team_abbr_fk" FOREIGN KEY ("opponent") REFERENCES "public"."dim_team"("team_abbr") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "playoff_planner_row" ADD CONSTRAINT "playoff_planner_row_list_fk" FOREIGN KEY ("season","through_week") REFERENCES "public"."playoff_planner_list"("season","through_week") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "playoff_planner_row_team_idx" ON "playoff_planner_row" USING btree ("team","position","season");