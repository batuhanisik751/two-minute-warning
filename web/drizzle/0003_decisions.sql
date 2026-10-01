CREATE TABLE "coach_season" (
	"season" integer NOT NULL,
	"coach_id" text NOT NULL,
	"team" text NOT NULL,
	"games" integer NOT NULL,
	"fourth_graded" integer NOT NULL,
	"fourth_toss_ups" integer NOT NULL,
	"fourth_wp_lost" double precision NOT NULL,
	"fourth_wrong" integer NOT NULL,
	"go_clear" integer NOT NULL,
	"go_clear_went" integer NOT NULL,
	"went" integer NOT NULL,
	"two_point_graded" integer NOT NULL,
	"two_point_toss_ups" integer NOT NULL,
	"two_point_wp_lost" double precision NOT NULL,
	"two_point_wrong" integer NOT NULL,
	"wp_lost" double precision NOT NULL,
	"aggressiveness" double precision,
	"wp_lost_per_game" double precision NOT NULL,
	"m1_candidates" integer NOT NULL,
	"m1_cases" integer NOT NULL,
	"m1_timeouts_left" integer NOT NULL,
	"m2_candidates" integer NOT NULL,
	"m2_cases" integer NOT NULL,
	"m2_ep_left" double precision NOT NULL,
	"m2_wp_left" double precision NOT NULL,
	"m3_decisive_games" integer NOT NULL,
	"m3_cases" integer NOT NULL,
	"m3_seconds_wasted" double precision NOT NULL,
	CONSTRAINT "coach_season_season_coach_id_pk" PRIMARY KEY("season","coach_id")
);
--> statement-breakpoint
CREATE TABLE "coach_week" (
	"game_id" text NOT NULL,
	"coach_id" text NOT NULL,
	"season" integer NOT NULL,
	"week" integer NOT NULL,
	"season_type" text NOT NULL,
	"team" text NOT NULL,
	"opp" text NOT NULL,
	"fourth_graded" integer NOT NULL,
	"fourth_toss_ups" integer NOT NULL,
	"fourth_wp_lost" double precision NOT NULL,
	"fourth_wrong" integer NOT NULL,
	"go_clear" integer NOT NULL,
	"go_clear_went" integer NOT NULL,
	"went" integer NOT NULL,
	"two_point_graded" integer NOT NULL,
	"two_point_toss_ups" integer NOT NULL,
	"two_point_wp_lost" double precision NOT NULL,
	"two_point_wrong" integer NOT NULL,
	"wp_lost" double precision NOT NULL,
	CONSTRAINT "coach_week_game_id_coach_id_pk" PRIMARY KEY("game_id","coach_id")
);
--> statement-breakpoint
CREATE TABLE "decision_clock" (
	"metric" text NOT NULL,
	"season" integer NOT NULL,
	"week" integer NOT NULL,
	"season_type" text NOT NULL,
	"game_id" text NOT NULL,
	"team" text NOT NULL,
	"opp" text NOT NULL,
	"coach_id" text NOT NULL,
	"play_id" integer NOT NULL,
	"qtr" integer NOT NULL,
	"quarter_seconds" integer NOT NULL,
	"down" integer,
	"ydstogo" integer,
	"yardline_100" integer,
	"score_differential" integer NOT NULL,
	"timeouts" integer NOT NULL,
	"is_case" boolean NOT NULL,
	"amount" double precision NOT NULL,
	"wp_left" double precision,
	"desc" text,
	"detail" jsonb NOT NULL,
	CONSTRAINT "decision_clock_metric_game_id_team_pk" PRIMARY KEY("metric","game_id","team"),
	CONSTRAINT "decision_clock_metric_check" CHECK ("decision_clock"."metric" in ('timeouts_unused', 'half_passivity', 'seconds_wasted'))
);
--> statement-breakpoint
CREATE TABLE "decision_fourth" (
	"game_id" text NOT NULL,
	"play_id" integer NOT NULL,
	"season" integer NOT NULL,
	"week" integer NOT NULL,
	"season_type" text NOT NULL,
	"posteam" text NOT NULL,
	"defteam" text NOT NULL,
	"coach_id" text NOT NULL,
	"qtr" integer NOT NULL,
	"quarter_seconds" integer NOT NULL,
	"score_differential" integer NOT NULL,
	"ydstogo" integer NOT NULL,
	"yardline_100" integer NOT NULL,
	"chosen" text NOT NULL,
	"recommended" text NOT NULL,
	"grade" text NOT NULL,
	"correct" boolean NOT NULL,
	"wp_go" double precision NOT NULL,
	"wp_fg" double precision,
	"wp_punt" double precision,
	"wp_lost" double precision NOT NULL,
	"p_convert" double precision NOT NULL,
	"p_make" double precision,
	"outcome" text,
	CONSTRAINT "decision_fourth_game_id_play_id_pk" PRIMARY KEY("game_id","play_id"),
	CONSTRAINT "decision_fourth_chosen_check" CHECK ("decision_fourth"."chosen" in ('go', 'field_goal', 'punt')),
	CONSTRAINT "decision_fourth_recommended_check" CHECK ("decision_fourth"."recommended" in ('go', 'field_goal', 'punt')),
	CONSTRAINT "decision_fourth_grade_check" CHECK ("decision_fourth"."grade" in ('clear', 'toss_up', 'one_option')),
	CONSTRAINT "decision_fourth_wp_lost_check" CHECK ("decision_fourth"."wp_lost" >= 0 and "decision_fourth"."wp_lost" <= 1)
);
--> statement-breakpoint
CREATE TABLE "decision_two_point" (
	"game_id" text NOT NULL,
	"play_id" integer NOT NULL,
	"season" integer NOT NULL,
	"week" integer NOT NULL,
	"season_type" text NOT NULL,
	"posteam" text NOT NULL,
	"defteam" text NOT NULL,
	"coach_id" text NOT NULL,
	"qtr" integer NOT NULL,
	"quarter_seconds" integer NOT NULL,
	"score_differential" integer NOT NULL,
	"chosen" text NOT NULL,
	"recommended" text NOT NULL,
	"grade" text NOT NULL,
	"correct" boolean NOT NULL,
	"wp_kick" double precision NOT NULL,
	"wp_two_point" double precision NOT NULL,
	"wp_lost" double precision NOT NULL,
	"result" text,
	CONSTRAINT "decision_two_point_game_id_play_id_pk" PRIMARY KEY("game_id","play_id"),
	CONSTRAINT "decision_two_point_chosen_check" CHECK ("decision_two_point"."chosen" in ('kick', 'two_point')),
	CONSTRAINT "decision_two_point_recommended_check" CHECK ("decision_two_point"."recommended" in ('kick', 'two_point')),
	CONSTRAINT "decision_two_point_grade_check" CHECK ("decision_two_point"."grade" in ('clear', 'toss_up'))
);
--> statement-breakpoint
CREATE TABLE "decisions_track_record" (
	"source" text NOT NULL,
	"line" integer NOT NULL,
	"section" text NOT NULL,
	"scope" text,
	"subset" text,
	"method" text,
	"metric" text NOT NULL,
	"value" double precision,
	"lo" double precision,
	"hi" double precision,
	"n" integer,
	"n_blocks" integer,
	CONSTRAINT "decisions_track_record_source_line_pk" PRIMARY KEY("source","line")
);
--> statement-breakpoint
CREATE TABLE "dim_coach" (
	"coach_id" text PRIMARY KEY NOT NULL,
	"name" text NOT NULL
);
--> statement-breakpoint
ALTER TABLE "coach_season" ADD CONSTRAINT "coach_season_coach_id_dim_coach_coach_id_fk" FOREIGN KEY ("coach_id") REFERENCES "public"."dim_coach"("coach_id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "coach_week" ADD CONSTRAINT "coach_week_coach_id_dim_coach_coach_id_fk" FOREIGN KEY ("coach_id") REFERENCES "public"."dim_coach"("coach_id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "decision_clock" ADD CONSTRAINT "decision_clock_coach_id_dim_coach_coach_id_fk" FOREIGN KEY ("coach_id") REFERENCES "public"."dim_coach"("coach_id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "decision_fourth" ADD CONSTRAINT "decision_fourth_coach_id_dim_coach_coach_id_fk" FOREIGN KEY ("coach_id") REFERENCES "public"."dim_coach"("coach_id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "decision_two_point" ADD CONSTRAINT "decision_two_point_coach_id_dim_coach_coach_id_fk" FOREIGN KEY ("coach_id") REFERENCES "public"."dim_coach"("coach_id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "coach_week_coach_idx" ON "coach_week" USING btree ("coach_id","season","week");--> statement-breakpoint
CREATE INDEX "decision_clock_coach_idx" ON "decision_clock" USING btree ("coach_id","season");--> statement-breakpoint
CREATE INDEX "decision_fourth_coach_idx" ON "decision_fourth" USING btree ("coach_id","season");--> statement-breakpoint
CREATE INDEX "decision_fourth_season_idx" ON "decision_fourth" USING btree ("season","week");--> statement-breakpoint
CREATE INDEX "decision_two_point_coach_idx" ON "decision_two_point" USING btree ("coach_id","season");