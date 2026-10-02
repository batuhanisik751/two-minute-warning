CREATE TABLE "hot_seat_firings" (
	"season" integer PRIMARY KEY NOT NULL,
	"positive_departures" integer NOT NULL,
	"fired_in_season" integer NOT NULL,
	"positives_week_12" integer NOT NULL,
	"positives_end_of_season" integer NOT NULL,
	"censored_coach_seasons" integer NOT NULL,
	"interim_coach_seasons" integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE "hot_seat_list" (
	"season" integer NOT NULL,
	"week" integer NOT NULL,
	"snapshot" text NOT NULL,
	"kind" text NOT NULL,
	"as_of" timestamp with time zone NOT NULL,
	"model_version" text NOT NULL,
	"generated_at" timestamp with time zone NOT NULL,
	"incomplete" boolean DEFAULT false NOT NULL,
	"n_coaches" integer NOT NULL,
	"note" text,
	CONSTRAINT "hot_seat_list_season_week_snapshot_kind_pk" PRIMARY KEY("season","week","snapshot","kind"),
	CONSTRAINT "hot_seat_list_kind_check" CHECK ("hot_seat_list"."kind" in ('live', 'backtest')),
	CONSTRAINT "hot_seat_list_snapshot_check" CHECK ("hot_seat_list"."snapshot" in ('weekly', 'end_of_season'))
);
--> statement-breakpoint
CREATE TABLE "hot_seat_outcome" (
	"season" integer NOT NULL,
	"week" integer NOT NULL,
	"coach_id" text NOT NULL,
	"departed" boolean,
	"censored" boolean,
	"departure_type" text,
	"announced" date,
	"label_status" text NOT NULL,
	CONSTRAINT "hot_seat_outcome_season_week_coach_id_pk" PRIMARY KEY("season","week","coach_id"),
	CONSTRAINT "hot_seat_outcome_status_check" CHECK ("hot_seat_outcome"."label_status" in ('final', 'pending'))
);
--> statement-breakpoint
CREATE TABLE "hot_seat_row" (
	"season" integer NOT NULL,
	"week" integer NOT NULL,
	"snapshot" text NOT NULL,
	"kind" text NOT NULL,
	"coach_id" text NOT NULL,
	"team" text NOT NULL,
	"as_of" timestamp with time zone NOT NULL,
	"rank" integer NOT NULL,
	"probability" double precision NOT NULL,
	"is_interim" boolean NOT NULL,
	"drivers" jsonb NOT NULL,
	"reg_games_played" integer NOT NULL,
	"reg_wins" double precision NOT NULL,
	"expected_wins" double precision,
	"wins_vs_expected" double precision,
	"point_diff_per_game" double precision,
	"tenure_seasons" integer,
	"division_rank" integer,
	"prev_season_wins" double precision,
	"consecutive_losing_seasons" integer,
	"fourth_down_wp_lost_per_game" double precision,
	CONSTRAINT "hot_seat_row_season_week_snapshot_kind_coach_id_pk" PRIMARY KEY("season","week","snapshot","kind","coach_id"),
	CONSTRAINT "hot_seat_row_probability_check" CHECK ("hot_seat_row"."probability" between 0 and 1),
	CONSTRAINT "hot_seat_row_rank_check" CHECK ("hot_seat_row"."rank" >= 1)
);
--> statement-breakpoint
CREATE TABLE "hot_seat_track_record" (
	"line" integer PRIMARY KEY NOT NULL,
	"variant" text NOT NULL,
	"model" text NOT NULL,
	"prob" text NOT NULL,
	"slice" text NOT NULL,
	"metric" text NOT NULL,
	"value" double precision,
	"lo" double precision,
	"hi" double precision,
	"n_rows" integer NOT NULL,
	"n_pos" integer NOT NULL,
	"n_seasons" integer NOT NULL
);
--> statement-breakpoint
ALTER TABLE "hot_seat_list" ADD CONSTRAINT "hot_seat_list_model_version_model_versions_model_version_fk" FOREIGN KEY ("model_version") REFERENCES "public"."model_versions"("model_version") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "hot_seat_outcome" ADD CONSTRAINT "hot_seat_outcome_coach_id_dim_coach_coach_id_fk" FOREIGN KEY ("coach_id") REFERENCES "public"."dim_coach"("coach_id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "hot_seat_row" ADD CONSTRAINT "hot_seat_row_coach_id_dim_coach_coach_id_fk" FOREIGN KEY ("coach_id") REFERENCES "public"."dim_coach"("coach_id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "hot_seat_row" ADD CONSTRAINT "hot_seat_row_team_dim_team_team_abbr_fk" FOREIGN KEY ("team") REFERENCES "public"."dim_team"("team_abbr") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "hot_seat_row" ADD CONSTRAINT "hot_seat_row_list_fk" FOREIGN KEY ("season","week","snapshot","kind") REFERENCES "public"."hot_seat_list"("season","week","snapshot","kind") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "hot_seat_outcome_coach_id_idx" ON "hot_seat_outcome" USING btree ("coach_id");--> statement-breakpoint
CREATE INDEX "hot_seat_row_coach_id_idx" ON "hot_seat_row" USING btree ("coach_id","season","week");