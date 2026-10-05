CREATE TABLE "teammate_out_allocation" (
	"out_pos" text NOT NULL,
	"role" text NOT NULL,
	"position" text NOT NULL,
	"n" integer NOT NULL,
	"carry" double precision,
	"target" double precision,
	"n_group" integer NOT NULL,
	"carry_group" double precision,
	"target_group" double precision,
	CONSTRAINT "teammate_out_allocation_out_pos_role_pk" PRIMARY KEY("out_pos","role"),
	CONSTRAINT "teammate_out_allocation_pos_check" CHECK ("teammate_out_allocation"."out_pos" in ('RB', 'WR', 'TE') and "teammate_out_allocation"."position" in ('RB', 'WR', 'TE')),
	CONSTRAINT "teammate_out_allocation_n_check" CHECK ("teammate_out_allocation"."n" >= 0 and "teammate_out_allocation"."n_group" >= 0)
);
--> statement-breakpoint
CREATE TABLE "teammate_out_backtest" (
	"candidate" text NOT NULL,
	"title" text NOT NULL,
	"chosen" boolean NOT NULL,
	"rule_pick" boolean NOT NULL,
	"season" text NOT NULL,
	"n" integer NOT NULL,
	"events" integer NOT NULL,
	"mae_carry" double precision,
	"mae_target" double precision,
	"mae_points" double precision,
	"top_hit" double precision,
	CONSTRAINT "teammate_out_backtest_candidate_season_pk" PRIMARY KEY("candidate","season"),
	CONSTRAINT "teammate_out_backtest_candidate_check" CHECK ("teammate_out_backtest"."candidate" in ('nothing', 'pro_rata', 'group', 'role')),
	CONSTRAINT "teammate_out_backtest_hit_check" CHECK ("teammate_out_backtest"."top_hit" between 0 and 1)
);
--> statement-breakpoint
CREATE TABLE "teammate_out_coverage" (
	"position" text PRIMARY KEY NOT NULL,
	"seasons" text NOT NULL,
	"n" integer NOT NULL,
	"below" integer NOT NULL,
	"above" integer NOT NULL,
	"inside" integer NOT NULL,
	"coverage" double precision,
	CONSTRAINT "teammate_out_coverage_position_check" CHECK ("teammate_out_coverage"."position" in ('RB', 'WR', 'TE', 'all')),
	CONSTRAINT "teammate_out_coverage_counts_check" CHECK ("teammate_out_coverage"."below" + "teammate_out_coverage"."above" + "teammate_out_coverage"."inside" = "teammate_out_coverage"."n")
);
--> statement-breakpoint
CREATE TABLE "teammate_out_events" (
	"out_pos" text PRIMARY KEY NOT NULL,
	"seasons" text NOT NULL,
	"sat" integer NOT NULL,
	"kept" integer NOT NULL,
	"no_roster_row" integer NOT NULL,
	"gone_status" integer NOT NULL,
	"events" integer NOT NULL,
	"single" integer NOT NULL,
	"multi" integer NOT NULL,
	"teammate_rows" integer NOT NULL,
	CONSTRAINT "teammate_out_events_pos_check" CHECK ("teammate_out_events"."out_pos" in ('RB', 'WR', 'TE', 'all')),
	CONSTRAINT "teammate_out_events_counts_check" CHECK ("teammate_out_events"."kept" <= "teammate_out_events"."sat" and "teammate_out_events"."single" + "teammate_out_events"."multi" = "teammate_out_events"."events")
);
--> statement-breakpoint
CREATE TABLE "teammate_out_list" (
	"season" integer NOT NULL,
	"week" integer NOT NULL,
	"as_of" timestamp with time zone NOT NULL,
	"model_version" text NOT NULL,
	"generated_at" timestamp with time zone NOT NULL,
	"n_teams" integer NOT NULL,
	"n_out" integer NOT NULL,
	"n_players" integer NOT NULL,
	"source" text NOT NULL,
	CONSTRAINT "teammate_out_list_season_week_as_of_pk" PRIMARY KEY("season","week","as_of"),
	CONSTRAINT "teammate_out_list_source_check" CHECK ("teammate_out_list"."source" in ('asof', 'observed')),
	CONSTRAINT "teammate_out_list_counts_check" CHECK ("teammate_out_list"."n_teams" >= 1 and "teammate_out_list"."n_out" >= "teammate_out_list"."n_teams" and "teammate_out_list"."n_players" >= "teammate_out_list"."n_teams")
);
--> statement-breakpoint
CREATE TABLE "teammate_out_live" (
	"season" integer PRIMARY KEY NOT NULL,
	"n" integer NOT NULL,
	"pending" integer NOT NULL,
	"starter_played" integer NOT NULL,
	"did_not_play" integer NOT NULL,
	"weeks" integer NOT NULL,
	"team_weeks" integer NOT NULL,
	"ranged" integer NOT NULL,
	"mae_points" double precision,
	"mae_points_base" double precision,
	"mae_carry_share" double precision,
	"mae_carry_share_base" double precision,
	"mae_target_share" double precision,
	"mae_target_share_base" double precision,
	"coverage" double precision,
	"top_hit" double precision,
	CONSTRAINT "teammate_out_live_counts_check" CHECK ("teammate_out_live"."n" >= 0 and "teammate_out_live"."pending" >= 0 and "teammate_out_live"."ranged" <= "teammate_out_live"."n"),
	CONSTRAINT "teammate_out_live_rates_check" CHECK ("teammate_out_live"."coverage" between 0 and 1 and "teammate_out_live"."top_hit" between 0 and 1)
);
--> statement-breakpoint
CREATE TABLE "teammate_out_row" (
	"season" integer NOT NULL,
	"week" integer NOT NULL,
	"as_of" timestamp with time zone NOT NULL,
	"team" text NOT NULL,
	"gsis_id" text NOT NULL,
	"opponent" text,
	"game_id" text,
	"kickoff" timestamp with time zone NOT NULL,
	"out_ids" text NOT NULL,
	"out_players" text,
	"out_positions" text,
	"out_reasons" text,
	"n_out" integer NOT NULL,
	"vac_carry_share" double precision,
	"vac_target_share" double precision,
	"position" text NOT NULL,
	"role" text NOT NULL,
	"base_games" integer,
	"base_carry_share" double precision,
	"base_target_share" double precision,
	"base_snap_share" double precision,
	"base_points" double precision,
	"base_team_carries" double precision,
	"base_team_targets" double precision,
	"pred_carry_share" double precision,
	"pred_target_share" double precision,
	"carry_share_change" double precision,
	"target_share_change" double precision,
	"pred_points" double precision NOT NULL,
	"points_lo" double precision,
	"points_hi" double precision,
	"pred_gain" double precision,
	"alloc_carry_share" double precision,
	"alloc_target_share" double precision,
	CONSTRAINT "teammate_out_row_season_week_as_of_team_gsis_id_pk" PRIMARY KEY("season","week","as_of","team","gsis_id"),
	CONSTRAINT "teammate_out_row_position_check" CHECK ("teammate_out_row"."position" in ('RB', 'WR', 'TE')),
	CONSTRAINT "teammate_out_row_out_check" CHECK ("teammate_out_row"."n_out" >= 1),
	CONSTRAINT "teammate_out_row_range_check" CHECK ("teammate_out_row"."points_lo" >= 0 and "teammate_out_row"."points_lo" <= "teammate_out_row"."pred_points" and "teammate_out_row"."pred_points" <= "teammate_out_row"."points_hi")
);
--> statement-breakpoint
ALTER TABLE "teammate_out_list" ADD CONSTRAINT "teammate_out_list_model_version_model_versions_model_version_fk" FOREIGN KEY ("model_version") REFERENCES "public"."model_versions"("model_version") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "teammate_out_row" ADD CONSTRAINT "teammate_out_row_team_dim_team_team_abbr_fk" FOREIGN KEY ("team") REFERENCES "public"."dim_team"("team_abbr") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "teammate_out_row" ADD CONSTRAINT "teammate_out_row_gsis_id_dim_player_gsis_id_fk" FOREIGN KEY ("gsis_id") REFERENCES "public"."dim_player"("gsis_id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "teammate_out_row" ADD CONSTRAINT "teammate_out_row_opponent_dim_team_team_abbr_fk" FOREIGN KEY ("opponent") REFERENCES "public"."dim_team"("team_abbr") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "teammate_out_row" ADD CONSTRAINT "teammate_out_row_list_fk" FOREIGN KEY ("season","week","as_of") REFERENCES "public"."teammate_out_list"("season","week","as_of") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "teammate_out_row_gsis_id_idx" ON "teammate_out_row" USING btree ("gsis_id","season","week");